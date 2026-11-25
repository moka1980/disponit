"""M-6 over IMAP (229, ADR-011): vanlig Gmail med app-passord.

Eiers vedtak 21.11.2026: mange små bedrifter deler én vanlig Gmail-konto.
Googles egen innlogging (OAuth) krever verifisering og en årlig
sikkerhetsgjennomgang før fremmede kunder kan koble til; IMAP med
app-passord gjør det ikke.

EGEN MODUL, IKKE EN GREN I `plan/epost.py`. Microsoft-innhenteren står i
M-6s bevisrot og røres ikke: de to veiene har hver sine kandidatdører
(229), og deler bare det som er likt — lagringen av én melding (`_lagre`,
med kryptering, idempotens og broen til kundeserviceregisteret) og
bokføringen av et sendt svar (`m6_svar_sendt` / `m6_svar_feilet`).

VERTENE ER FASTE. Kunden oppgir adressen og app-passordet, aldri en
server: `TJENESTER` er hele settet. Det finnes derfor ingen vei til å få
plattformen til å koble seg mot en vert kunden har valgt.

INNHENTINGEN
  * Cursoren er `imap:<uidvalidity>:<siste uid>` i `delta_token`.
  * FØRSTE RUNDE STARTER FRA NÅ, som hos Microsoft: cursoren settes til
    postboksens siste melding, og ingenting eldre hentes. Det samme skjer
    om postboksen bytter `uidvalidity` (da er de gamle uid-ene ugyldige).
  * Noen få meldinger per runde; kapasitet forsinker, den konsumerer ikke.
  * En avvist innlogging setter kilden `feilet` (veien tilbake er å koble
    til på nytt med et nytt app-passord). Alt annet er forbigående.

IKKE MED I FØRSTE VERSJON, og det sies: en melding som SLETTES i Gmail
speiles ikke hit (Microsoft-veien gjør det, 210).

Loggen bærer aldri adresse, emne, tekst eller passord.
"""
from __future__ import annotations

import base64
import email
import email.policy
import email.utils
import imaplib
import os
import re
import smtplib
import socket
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage

from plan import epost

#: tjeneste -> (imap-vert, imap-port, smtp-vert, smtp-port). FAST sett.
TJENESTER = {
    "gmail": ("imap.gmail.com", 993, "smtp.gmail.com", 465),
}
MAKS_PER_RUNDE = int(os.environ.get("DISPONIT_IMAP_MAKS_PER_RUNDE", "25"))
MAKS_BYTES = 2 * 1024 * 1024          # større meldinger hentes uten vedlegg
TIDSAVBRUDD_S = 20
AKTOR = epost.AKTOR


class Bearer(str):
    """Et kortlivet innloggingstoken fra Google (ADR-012), der et
    app-passord ellers står. TYPEN velger innloggingsmåten: XOAUTH2 for et
    `Bearer`, vanlig LOGIN for en ren streng. Slik holder alle kallene —
    og alle testsnittene — den ene formen (tjeneste, bruker, passord)."""


def _xoauth2(bruker: str, token: str) -> bytes:
    return f"user={bruker}\x01auth=Bearer {token}\x01\x01".encode("utf-8")


class ImapAvvist(Exception):
    """Innloggingen ble AVVIST — feil passord, eller IMAP er slått av."""


class ImapForbigaende(Exception):
    """Nettverk, tidsavbrudd, serverfeil: prøv igjen neste runde."""


# ---------------------------------------------------------------------------
# Cursoren
# ---------------------------------------------------------------------------

def les_cursor(delta: str | None) -> tuple[int, int] | None:
    """`imap:<uidvalidity>:<siste uid>` -> (uidvalidity, siste), ellers None."""
    m = re.fullmatch(r"imap:(\d{1,12}):(\d{1,12})", delta or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def skriv_cursor(uidvalidity: int, siste: int) -> str:
    return f"imap:{int(uidvalidity)}:{int(siste)}"


# ---------------------------------------------------------------------------
# Én e-post (RFC 5322) -> formen `_lagre` tar imot
# ---------------------------------------------------------------------------

_TAGG = re.compile(r"<[^>]{0,2000}>")


def _tekst_fra_html(html: str) -> str:
    """Nødløsning for en melding uten tekstdel: taggene bort, luft samlet.
    Flaten har sin egen `lesbarTekst`; dette er bare så registeret ikke
    lagrer markup som kundens ord."""
    uten = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    uten = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", uten)
    uten = _TAGG.sub(" ", uten)
    import html as _html
    uten = _html.unescape(uten)
    linjer = [re.sub(r"[ \t\xa0]+", " ", l).strip() for l in uten.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(linjer)).strip()


def tolk(raa: bytes, uidvalidity: int, uid: int) -> dict:
    """Rå e-post -> meldingen i Graph-formen `epost._lagre` leser.

    `id` er meldingens egen Message-ID når den har en (den er det svaret
    skal peke på i `In-Reply-To`), ellers en id utledet av postboksens
    uid — alltid stabil, så samme melding to ganger blir én rad.
    """
    m = email.message_from_bytes(raa, policy=email.policy.default)
    navn, adresse = email.utils.parseaddr(str(m.get("From") or ""))
    til = [a for _n, a in email.utils.getaddresses(
        [str(v) for v in m.get_all("To", [])]) if a][:20]
    mid = str(m.get("Message-ID") or "").strip()
    if not re.fullmatch(r"<[^<>\s]{1,300}>", mid):
        mid = ""
    refs = re.findall(r"<[^<>\s]{1,300}>", str(m.get("References") or ""))
    dato = None
    try:
        d = email.utils.parsedate_to_datetime(str(m.get("Date") or ""))
        if d is not None:
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            dato = d.isoformat()
    except (TypeError, ValueError):
        dato = None
    if dato is None:
        # Uten lesbar dato er «nå» det ærligste: meldingen kom nettopp inn.
        dato = datetime.now(timezone.utc).isoformat()

    kropp, vedlegg = "", False
    del_tekst = del_html = None
    for del_ in m.walk():
        if del_.is_multipart():
            continue
        if del_.get_content_disposition() == "attachment" \
                or del_.get_filename():
            vedlegg = True
            continue
        ct = del_.get_content_type()
        try:
            innhold = del_.get_content()
        except (LookupError, UnicodeError, ValueError):
            continue
        if not isinstance(innhold, str):
            continue
        if ct == "text/plain" and del_tekst is None:
            del_tekst = innhold
        elif ct == "text/html" and del_html is None:
            del_html = innhold
    if del_tekst is not None:
        kropp = del_tekst.strip()
    elif del_html is not None:
        kropp = _tekst_fra_html(del_html)
    emne = str(m.get("Subject") or "").strip()
    return {
        "id": mid or f"imap-{int(uidvalidity)}-{int(uid)}",
        "conversationId": (refs[0] if refs else mid) or None,
        "receivedDateTime": dato,
        "from": {"emailAddress": {"address": adresse, "name": navn}},
        "toRecipients": [{"emailAddress": {"address": a}} for a in til],
        "subject": emne[:500],
        "bodyPreview": re.sub(r"\s+", " ", kropp)[:255],
        "hasAttachments": vedlegg,
        "_kropp": kropp,
    }


# ---------------------------------------------------------------------------
# Den ekte forbindelsen (testene bytter den ut)
# ---------------------------------------------------------------------------

class Imap:
    """Tynn klient over `imaplib`: innboksen, lesende, og ikke mer."""

    def __init__(self, tjeneste: str, bruker: str, passord: str):
        vert, port, _sv, _sp = TJENESTER[tjeneste]
        try:
            self._c = imaplib.IMAP4_SSL(
                vert, port, ssl_context=ssl.create_default_context(),
                timeout=TIDSAVBRUDD_S)
        except (OSError, socket.timeout, ssl.SSLError) as e:
            raise ImapForbigaende(type(e).__name__) from e
        try:
            if isinstance(passord, Bearer):
                self._c.authenticate(
                    "XOAUTH2", lambda _utfordring: _xoauth2(bruker, passord))
            else:
                self._c.login(bruker, passord)
        except imaplib.IMAP4.error as e:
            self.lukk()
            raise ImapAvvist("innloggingen ble avvist") from e
        except (OSError, socket.timeout) as e:
            self.lukk()
            raise ImapForbigaende(type(e).__name__) from e

    def velg(self) -> tuple[int, int]:
        """-> (uidvalidity, uidnext) for innboksen, åpnet LESENDE."""
        try:
            typ, _ = self._c.select("INBOX", readonly=True)
            if typ != "OK":
                raise ImapForbigaende("select")
            typ, data = self._c.status("INBOX", "(UIDVALIDITY UIDNEXT)")
            tekst = (data[0] or b"").decode("ascii", "replace")
            v = re.search(r"UIDVALIDITY (\d+)", tekst)
            n = re.search(r"UIDNEXT (\d+)", tekst)
            if typ != "OK" or not v or not n:
                raise ImapForbigaende("status")
            return int(v.group(1)), int(n.group(1))
        except (imaplib.IMAP4.error, OSError, socket.timeout) as e:
            raise ImapForbigaende(type(e).__name__) from e

    def nye(self, etter_uid: int, maks: int) -> list[int]:
        try:
            typ, data = self._c.uid("SEARCH", None,
                                    f"UID {int(etter_uid) + 1}:*")
            if typ != "OK":
                raise ImapForbigaende("search")
            uider = sorted(int(x) for x in (data[0] or b"").split()
                           if x.isdigit())
            # `n:*` gir ALLTID siste melding, også når den er eldre enn n.
            return [u for u in uider if u > etter_uid][:maks]
        except (imaplib.IMAP4.error, OSError, socket.timeout) as e:
            raise ImapForbigaende(type(e).__name__) from e

    def hent(self, uid: int) -> bytes:
        """Meldingen, uten å merke den som lest (`BODY.PEEK`). En stor
        melding hentes uten vedleggene: hode og første tekstdel."""
        try:
            typ, data = self._c.uid("FETCH", str(int(uid)), "(RFC822.SIZE)")
            m = re.search(rb"RFC822\.SIZE (\d+)", b" ".join(
                x if isinstance(x, bytes) else b" ".join(
                    y for y in x if isinstance(y, bytes))
                for x in (data or []) if x))
            stor = bool(m and int(m.group(1)) > MAKS_BYTES)
            hva = "(BODY.PEEK[HEADER] BODY.PEEK[1])" if stor \
                else "(BODY.PEEK[])"
            typ, data = self._c.uid("FETCH", str(int(uid)), hva)
            if typ != "OK":
                raise ImapForbigaende("fetch")
            deler = [x[1] for x in (data or [])
                     if isinstance(x, tuple) and len(x) > 1
                     and isinstance(x[1], bytes)]
            if not deler:
                raise ImapForbigaende("tom")
            return b"".join(deler)
        except (imaplib.IMAP4.error, OSError, socket.timeout) as e:
            raise ImapForbigaende(type(e).__name__) from e

    def lukk(self) -> None:
        try:
            self._c.logout()
        except Exception:                                   # noqa: BLE001
            pass


def prov_innlogging(tjeneste: str, bruker: str, passord: str) -> None:
    """Brukt ved TILKOBLING: lykkes innloggingen, og finnes innboksen?
    Kaster `ImapAvvist` eller `ImapForbigaende`."""
    k = Imap(tjeneste, bruker, passord)
    try:
        k.velg()
    finally:
        k.lukk()


def send_smtp(tjeneste: str, bruker: str, passord: str,
              melding: EmailMessage) -> None:
    """Sender ÉN melding. `ImapAvvist` = innloggingen avvist;
    `ImapForbigaende` = ingenting ble tatt imot (trygt å prøve igjen)."""
    _iv, _ip, vert, port = TJENESTER[tjeneste]
    try:
        s = smtplib.SMTP_SSL(vert, port, timeout=TIDSAVBRUDD_S,
                             context=ssl.create_default_context())
    except (OSError, socket.timeout, ssl.SSLError,
            smtplib.SMTPConnectError) as e:
        raise ImapForbigaende(type(e).__name__) from e
    try:
        try:
            if isinstance(passord, Bearer):
                s.ehlo()
                kode, _svar = s.docmd("AUTH", "XOAUTH2 " + base64.b64encode(
                    _xoauth2(bruker, passord)).decode("ascii"))
                if kode == 334:
                    # Google forklarer avslaget i en utfordring og venter
                    # et tomt svar før den endelige koden kommer.
                    kode, _svar = s.docmd("")
                if kode != 235:
                    raise smtplib.SMTPAuthenticationError(kode, b"xoauth2")
            else:
                s.login(bruker, passord)
        except smtplib.SMTPAuthenticationError as e:
            raise ImapAvvist("smtp-innloggingen ble avvist") from e
        except (smtplib.SMTPException, OSError, socket.timeout) as e:
            raise ImapForbigaende(type(e).__name__) from e
        s.send_message(melding)
    finally:
        try:
            s.quit()
        except Exception:                                   # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Innhentingen
# ---------------------------------------------------------------------------

def kandidater(conn, grense: int = epost.MAKS_KILDER) -> list:
    conn.rollback()
    rader = conn.execute("SELECT * FROM m6_imap_hentekandidater(%s)",
                         (grense,)).fetchall()
    conn.rollback()
    return rader


def hent_en(conn, rad, *, kobler=Imap) -> dict:
    """Én kilde, én runde. `kobler` er testsnittet."""
    from api.epost_kilde import KildeFeil, hent_imap_tilgang
    from db.pg import sett_kontekst
    tenant, kilde_id, _postboks, delta, _sist = rad
    kid = str(kilde_id)
    rid = f"imap-{kid[:8]}"
    ut = {"kilde": kid[:8], "sett": 0, "nye": 0}
    sett_kontekst(conn, tenant, AKTOR, rid)
    try:
        tilgang = hent_imap_tilgang(conn, tenant, kilde_id)
    except KildeFeil as e:
        conn.rollback()
        return epost._feilet(conn, tenant, kilde_id, rid, ut, "tilgang",
                             str(e))
    except ImapForbigaende as e:
        # Google-tokenet lot seg ikke fornye AKKURAT NÅ (ADR-012): drift.
        conn.rollback()
        ut["forbigaende"] = str(e)[:40]
        return ut
    conn.rollback()
    try:
        k = kobler(tilgang["tjeneste"], tilgang["bruker"],
                   tilgang["passord"])
    except ImapAvvist:
        return epost._feilet(conn, tenant, kilde_id, rid, ut, "innlogging",
                             "imap-innloggingen ble avvist")
    except ImapForbigaende as e:
        ut["forbigaende"] = str(e)[:40]
        return ut
    try:
        uidvalidity, uidnext = k.velg()
        cursor = les_cursor(delta)
        if cursor is None or cursor[0] != uidvalidity:
            # FØRSTE RUNDE, eller postboksen har byttet uid-rom: start
            # FRA NÅ. Ingenting eldre hentes.
            siste = max(uidnext - 1, 0)
            ut["startet_fra"] = "naa"
        else:
            siste = cursor[1]
            for uid in k.nye(siste, MAKS_PER_RUNDE):
                raa = k.hent(uid)
                m = tolk(raa, uidvalidity, uid)
                ut["sett"] += 1
                sett_kontekst(conn, tenant, AKTOR, rid)
                if epost._lagre(conn, tenant, kilde_id, m, m.pop("_kropp"),
                                "text"):
                    ut["nye"] += 1
                # CURSOREN FLYTTES MED HVER MELDING, i samme transaksjon som
                # raden: et avbrudd midt i runden mister ingenting og
                # henter ingenting to ganger.
                siste = uid
                conn.execute(
                    "UPDATE epost_kilde SET delta_token=%s"
                    " WHERE tenant=%s AND kilde_id=%s",
                    (skriv_cursor(uidvalidity, siste), tenant, kilde_id))
                conn.commit()
        sett_kontekst(conn, tenant, AKTOR, rid)
        conn.execute(
            "UPDATE epost_kilde SET sist_hentet_ts=now(), delta_token=%s"
            " WHERE tenant=%s AND kilde_id=%s",
            (skriv_cursor(uidvalidity, siste), tenant, kilde_id))
        conn.commit()
    except ImapForbigaende as e:
        conn.rollback()
        ut["forbigaende"] = str(e)[:40]
    finally:
        k.lukk()
    return ut


# ---------------------------------------------------------------------------
# Utsendingen
# ---------------------------------------------------------------------------

def sendekandidater(conn, grense: int = epost.MAKS_KILDER) -> list:
    conn.rollback()
    rader = conn.execute("SELECT * FROM m6_imap_sendekandidater(%s)",
                         (grense,)).fetchall()
    conn.rollback()
    return rader


def bygg_svar(postboks: str, til: str, emne: str, tekst: str,
              svar_paa: str | None) -> EmailMessage:
    """Svaret som e-post: fra postboksen, til avsenderen, i tråden."""
    m = EmailMessage()
    m["From"] = postboks
    m["To"] = til
    rent = re.sub(r"[\r\n]+", " ", emne or "").strip()
    m["Subject"] = rent if re.match(r"(?i)^re:", rent) else f"Re: {rent}"
    if svar_paa and re.fullmatch(r"<[^<>\s]{1,300}>", svar_paa):
        m["In-Reply-To"] = svar_paa
        m["References"] = svar_paa
    m["Date"] = email.utils.formatdate(localtime=False)
    m["Message-ID"] = email.utils.make_msgid(
        domain=postboks.rpartition("@")[2] or None)
    m.set_content(tekst)
    return m


def send_ett(conn, rad, *, sender=send_smtp) -> dict:
    """Ett svar ut over SMTP. `sender` er testsnittet."""
    from api.epost_kilde import KildeFeil, hent_imap_tilgang
    from db import kryptering
    from db.pg import sett_kontekst
    tenant, utkast_id, _melding_id, kilde_id = rad
    uid = str(utkast_id)
    rid = f"svar-{uid[:8]}"
    ut = {"utkast": uid[:8]}

    def feilet(grunn):
        sett_kontekst(conn, tenant, AKTOR, rid)
        conn.execute("SELECT m6_svar_feilet(%s,%s,%s,%s)",
                     (tenant, utkast_id, grunn, AKTOR))
        conn.commit()
        ut["feilet"] = grunn
        epost._log("epost_svar_feilet", **ut)
        return ut

    sett_kontekst(conn, tenant, AKTOR, rid)
    r = conn.execute("SELECT * FROM m6_imap_for_utsending(%s,%s)",
                     (tenant, utkast_id)).fetchone()
    if r is None:
        conn.rollback()
        return feilet("utkast_borte")
    status, lev_id, ct, nonce, key_id, _kid, m_ct, m_nonce, m_key = r
    if status != "sendes":
        conn.rollback()
        ut["hoppet"] = status            # noen andre rakk den først
        return ut
    if ct is None or m_ct is None:
        conn.rollback()
        return feilet("uten_tekst_eller_traad")
    try:
        tekst = kryptering.dekrypter(
            kryptering.hent_dek(conn, tenant, key_id), bytes(ct),
            bytes(nonce), tenant, key_id)["tekst"]
        opprinnelig = kryptering.dekrypter(
            kryptering.hent_dek(conn, tenant, m_key), bytes(m_ct),
            bytes(m_nonce), tenant, m_key)
        postboks = conn.execute(
            "SELECT postboks FROM epost_kilde WHERE tenant=%s"
            " AND kilde_id=%s", (tenant, kilde_id)).fetchone()[0]
    except Exception:                                       # noqa: BLE001
        conn.rollback()
        return feilet("uleselig_utkast")
    til = str(opprinnelig.get("fra") or "").strip()
    if not til or "@" not in til:
        conn.rollback()
        return feilet("uten_mottaker")
    try:
        tilgang = hent_imap_tilgang(conn, tenant, kilde_id)
    except KildeFeil:
        conn.rollback()
        return feilet("tilgang_avvist")
    except ImapForbigaende as e:
        conn.rollback()
        ut["forbigaende"] = str(e)[:40]
        epost._log("epost_svar_forbigaende", **ut)
        return ut
    conn.rollback()
    melding = bygg_svar(postboks, til, str(opprinnelig.get("emne") or ""),
                        tekst, lev_id)
    try:
        sender(tilgang["tjeneste"], tilgang["bruker"], tilgang["passord"],
               melding)
    except ImapAvvist:
        return feilet("sendetilgang_avvist")
    except ImapForbigaende as e:
        # DRIFT, ikke en dom: ingenting ble tatt imot, utkastet står i kø.
        ut["forbigaende"] = str(e)[:40]
        epost._log("epost_svar_forbigaende", **ut)
        return ut
    except smtplib.SMTPRecipientsRefused:
        return feilet("mottaker_avvist")
    except Exception as e:                                  # noqa: BLE001
        # USIKKERT om serveren tok imot: ALDRI et nytt forsøk av seg selv —
        # et svar skal ikke gå to ganger. Et menneske ser det som feilet.
        # Grunnen er en KODE (181): typenavnet går i loggen, ikke i basen.
        epost._log("epost_svar_usikker", feil=type(e).__name__, **ut)
        return feilet("smtp_usikker")
    sett_kontekst(conn, tenant, AKTOR, rid)
    ny = conn.execute("SELECT m6_svar_sendt(%s,%s,%s)",
                      (tenant, utkast_id, AKTOR)).fetchone()[0]
    conn.commit()
    ut["sendt"] = bool(ny)
    return ut


def kjor_en_runde(tjeneste, conn, *, kobler=Imap, sender=send_smtp) -> dict:
    """IMAP-rundens to halvdeler: inn, så ut. Kill-switchen er M-6s."""
    if epost.er_av():
        return {"av": True, "plukket": 0, "resultater": []}
    inn = [hent_en(conn, r, kobler=kobler) for r in kandidater(conn)]
    utk = [send_ett(conn, r, sender=sender) for r in sendekandidater(conn)]
    res = {"plukket": len(inn), "resultater": inn,
           "svar": {"plukket": len(utk), "resultater": utk}}
    if inn or utk:
        epost._log("epost_imap_runde", **res)
    return res
