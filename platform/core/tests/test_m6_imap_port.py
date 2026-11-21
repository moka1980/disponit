"""Porten for 228 (ADR-011): M-6 over IMAP — vanlig Gmail med app-passord.

  1. En e-post tolkes riktig: tekstdel foran HTML, HTML uten tekstdel
     renses, vedlegg merkes, kodet emne dekodes, Message-ID blir id-en.
  2. Første runde starter FRA NÅ; neste runde henter det nye, kryptert og
     idempotent; cursoren flyttes med hver melding.
  3. En avvist innlogging setter kilden `feilet`; et nettverksbrudd rører
     ingenting.
  4. De to veiene deler ikke kandidater: Microsoft-runden ser aldri en
     IMAP-kilde, og omvendt.
  5. Svaret går ut over SMTP, i tråden, fra postboksen, nøyaktig én gang.
  6. Tilkoblingen prøver innloggingen FØR noe lagres, tar bare et
     app-passord, og gir aldri passordet tilbake.
  7. Vertene er faste: kunden oppgir aldri en server.

MUTASJONER SOM DREPER DENNE: la første runde hente alt (2), fjern
`leverandor = 'm365'` fra `m6_hentekandidater` (4), la `send_ett` prøve
igjen etter en usikker SMTP-feil (5), lagre før innloggingen er prøvd (6).
"""
import inspect
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_m37 import _sett_kontekst
from .test_m6_inntak_port import _m365, _pa, _veksler, pg_plan

PASSORD = "abcdefghijklmnop"


def _raa(*, fra="Per Hansen <per@nordvik.example>", emne="Befaring",
         tekst="Hei, kan dere komme torsdag?", html=None, vedlegg=False,
         mid=None, uten_mid=False):
    from email.message import EmailMessage
    m = EmailMessage()
    m["From"] = fra
    m["To"] = "post@fjordlys.example"
    m["Subject"] = emne
    m["Date"] = "Fri, 20 Nov 2026 09:00:00 +0100"
    if not uten_mid:
        m["Message-ID"] = mid or f"<{secrets.token_hex(6)}@mail.example>"
    if tekst is not None:
        m.set_content(tekst)
        if html is not None:
            m.add_alternative(html, subtype="html")
    elif html is not None:
        m.set_content(html, subtype="html")
    if vedlegg:
        m.add_attachment(b"%PDF-1.4", maintype="application",
                         subtype="pdf", filename="tilbud.pdf")
    return m.as_bytes()


# ---------------------------------------------------------------------------
# 1. Tolkingen — rene funksjoner
# ---------------------------------------------------------------------------

def test_en_epost_tolkes_til_formen_lagringen_leser():
    from plan.epost_imap import tolk
    m = tolk(_raa(mid="<abc123@mail.example>", vedlegg=True,
                  html="<p>Hei i <b>HTML</b></p>"), 7, 42)
    assert m["id"] == "<abc123@mail.example>"
    assert m["from"]["emailAddress"] == {"address": "per@nordvik.example",
                                         "name": "Per Hansen"}
    assert m["toRecipients"] == [
        {"emailAddress": {"address": "post@fjordlys.example"}}]
    assert m["subject"] == "Befaring"
    # TEKSTDELEN vinner over HTML, og vedlegget merkes uten å lagres.
    assert m["_kropp"] == "Hei, kan dere komme torsdag?"
    assert m["hasAttachments"] is True
    assert m["receivedDateTime"].startswith("2026-11-20T09:00:00")

    # Bare HTML: markupen lagres ikke som kundens ord.
    h = tolk(_raa(tekst=None, html="<html><style>p{}</style><body>"
                  "<p>Linje én</p><p>Linje&nbsp;to &amp; mer</p>"
                  "<script>alert(1)</script></body></html>"), 7, 43)
    assert "<" not in h["_kropp"] and "alert" not in h["_kropp"]
    assert "Linje én" in h["_kropp"] and "to & mer" in h["_kropp"]

    # Kodet emne (norske tegn) dekodes; uten Message-ID utledes en stabil id.
    k = tolk(_raa(emne="Spørsmål om æøå", uten_mid=True), 7, 44)
    assert k["subject"] == "Spørsmål om æøå"
    assert k["id"] == "imap-7-44"
    assert tolk(_raa(uten_mid=True), 7, 44)["id"] == "imap-7-44"
    # En Message-ID som ikke har formen <…> brukes ikke som id.
    assert tolk(_raa(mid="ikke en id"), 7, 45)["id"] == "imap-7-45"


def test_cursoren_og_svaret():
    from plan.epost_imap import bygg_svar, les_cursor, skriv_cursor
    assert les_cursor(skriv_cursor(7, 42)) == (7, 42)
    for ugyldig in (None, "", "imap:7", "https://graph.microsoft.com/x",
                    "imap:a:b", "imap:7:42:1"):
        assert les_cursor(ugyldig) is None, ugyldig

    s = bygg_svar("post@gmail.example", "per@nordvik.example", "Befaring",
                  "Vi kommer torsdag.", "<abc123@mail.example>")
    assert s["From"] == "post@gmail.example"
    assert s["To"] == "per@nordvik.example"
    assert s["Subject"] == "Re: Befaring"
    assert s["In-Reply-To"] == s["References"] == "<abc123@mail.example>"
    assert s.get_content().strip() == "Vi kommer torsdag."
    # «Re:» dobles ikke, og et linjeskift i emnet kan ikke lage en ny header.
    assert bygg_svar("a@b.example", "c@d.example", "RE: Befaring", "x",
                     None)["Subject"] == "RE: Befaring"
    ond = bygg_svar("a@b.example", "c@d.example",
                    "Hei\r\nBcc: tyv@x.example", "x", "imap-7-1")
    assert "Bcc" not in ond and "\n" not in ond["Subject"]
    assert ond["In-Reply-To"] is None, "en utledet id er ikke en Message-ID"


def test_vertene_er_faste_og_passordet_er_et_app_passord():
    from api import epost_kilde
    from plan import epost_imap
    assert epost_imap.TJENESTER == {
        "gmail": ("imap.gmail.com", 993, "smtp.gmail.com", 465)}
    assert epost_kilde.IMAP_TJENESTER == tuple(epost_imap.TJENESTER)
    # Kunden oppgir ALDRI en server: endepunktet leser ikke noe vertsfelt.
    kilde = inspect.getsource(epost_kilde.imap_endepunkt)
    for felt in ('"vert"', '"host"', '"server"', '"port"'):
        assert felt not in kilde, felt
    assert epost_kilde._APPPASSORD.match("abcdefghijklmnop")
    for ikke in ("Sommer2026!", "abcdefghijklmno", "abcd efgh ijkl mnop",
                 "abcdefghijklmnop1"):
        assert not epost_kilde._APPPASSORD.match(ikke), ikke
    assert epost_kilde.kan_svare(epost_kilde.IMAP_SCOPE) is True
    assert epost_kilde.kan_svare("imap") is False
    assert epost_kilde.kan_svare(
        "https://graph.microsoft.com/Mail.Read offline_access") is False


# ---------------------------------------------------------------------------
# Rigg: en etterlignet postboks
# ---------------------------------------------------------------------------

class _Postboks:
    """Det `epost_imap.Imap` gir runden, med en liste i minnet bak."""

    def __init__(self, uidvalidity=7):
        self.uidvalidity = uidvalidity
        self.meldinger = {}            # uid -> rå bytes
        self.innlogginger = []
        self.avvis = False
        self.nede = False

    def legg_til(self, raa):
        uid = max(self.meldinger, default=0) + 1
        self.meldinger[uid] = raa
        return uid

    def kobler(self):
        boks = self

        class K:
            def __init__(self, tjeneste, bruker, passord):
                from plan.epost_imap import ImapAvvist, ImapForbigaende
                boks.innlogginger.append((tjeneste, bruker, passord))
                if boks.nede:
                    raise ImapForbigaende("OSError")
                if boks.avvis:
                    raise ImapAvvist("avvist")

            def velg(self):
                return boks.uidvalidity, max(boks.meldinger, default=0) + 1

            def nye(self, etter, maks):
                return sorted(u for u in boks.meldinger if u > etter)[:maks]

            def hent(self, uid):
                return boks.meldinger[uid]

            def lukk(self):
                pass
        return K


def _imap_kilde(m, postboks=None):
    from api.epost_kilde import IMAP_SCOPE
    from db import kryptering
    postboks = postboks or f"felles-{secrets.token_hex(4)}@gmail.example"
    _sett_kontekst(m, TENANT)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(m, TENANT)
    ct, nonce = kryptering.krypter(
        dek, {"imap_tjeneste": "gmail", "imap_passord": PASSORD},
        TENANT, key_id)
    kid = m.execute(
        "INSERT INTO epost_kilde (tenant, leverandor, postboks,"
        " auth_kryptert, nonce, key_id, scope) VALUES"
        " (%s,'imap',%s,%s,%s,%s,%s) RETURNING kilde_id",
        (TENANT, postboks, ct, nonce, key_id, IMAP_SCOPE)).fetchone()[0]
    m.commit()
    return kid, postboks, dek


def _kilde(m, kid):
    _sett_kontekst(m, TENANT)
    r = m.execute("SELECT status, delta_token, sist_hentet_ts FROM epost_kilde"
                  " WHERE tenant=%s AND kilde_id=%s", (TENANT, kid)).fetchone()
    n = m.execute("SELECT count(*) FROM epost_melding WHERE tenant=%s"
                  " AND kilde_id=%s", (TENANT, kid)).fetchone()[0]
    m.rollback()
    return r, n


def _mine(res, kid):
    return [r for r in res["resultater"] if r["kilde"] == str(kid)[:8]]


# ---------------------------------------------------------------------------
# 2–4. Innhentingen
# ---------------------------------------------------------------------------

@pg_plan
def test_forste_runde_starter_fra_naa_og_neste_henter_det_nye(
        migrator, miljo, app, monkeypatch, capsys):
    from db import kryptering
    from plan.epost_imap import kjor_en_runde
    _m365(monkeypatch)
    kid, postboks, dek = _imap_kilde(migrator)
    boks = _Postboks()
    for i in range(3):                       # gammel post, FØR tilkoblingen
        boks.legg_til(_raa(emne=f"Gammel {i}"))
    pa = _pa()
    try:
        res = kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                            sender=lambda *a: None)
        mine = _mine(res, kid)
        assert mine and mine[0]["nye"] == 0 and mine[0]["sett"] == 0, res
        assert mine[0]["startet_fra"] == "naa"
        (status, cursor, hentet), antall = _kilde(migrator, kid)
        assert status == "aktiv" and cursor == "imap:7:3" and antall == 0
        assert hentet is not None
        assert boks.innlogginger[-1] == ("gmail", postboks, PASSORD)

        hemmelig = "Fortrolig-" + secrets.token_hex(5)
        boks.legg_til(_raa(emne=hemmelig, tekst=hemmelig,
                           mid="<ny1@mail.example>"))
        boks.legg_til(_raa(emne="Ny to", mid="<ny2@mail.example>",
                           vedlegg=True))
        res = kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                            sender=lambda *a: None)
        mine = _mine(res, kid)
        assert mine[0]["sett"] == 2 and mine[0]["nye"] == 2, res
        (status, cursor, _h), antall = _kilde(migrator, kid)
        assert cursor == "imap:7:5" and antall == 2

        _sett_kontekst(migrator, TENANT)
        rader = migrator.execute(
            "SELECT leverandor_melding_id, kropp_kryptert, nonce, key_id,"
            " har_vedlegg, epost_melding::text FROM epost_melding"
            " WHERE tenant=%s AND kilde_id=%s ORDER BY mottatt_ts,"
            " leverandor_melding_id", (TENANT, kid)).fetchall()
        migrator.rollback()
        assert sorted(r[0] for r in rader) == ["<ny1@mail.example>",
                                               "<ny2@mail.example>"]
        assert sorted(r[4] for r in rader) == [False, True]
        for r in rader:
            assert hemmelig not in r[5], "klartekst i basen"
        en = [r for r in rader if r[0] == "<ny1@mail.example>"][0]
        klar = kryptering.dekrypter(dek, bytes(en[1]), bytes(en[2]),
                                    TENANT, en[3])
        assert klar["emne"] == hemmelig and klar["kropp"] == hemmelig
        assert klar["fra"] == "per@nordvik.example"

        # Samme postboks én gang til: ingenting nytt, ingenting dobbelt.
        res = kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                            sender=lambda *a: None)
        assert _mine(res, kid)[0]["nye"] == 0
        assert _kilde(migrator, kid)[1] == 2

        # Postboksen bytter uid-rom: cursoren er ugyldig, start fra nå.
        boks.uidvalidity = 8
        res = kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                            sender=lambda *a: None)
        assert _mine(res, kid)[0]["startet_fra"] == "naa"
        assert _kilde(migrator, kid)[0][1] == "imap:8:5"
    finally:
        pa.close()
    logg = capsys.readouterr().out
    for ikke in (hemmelig, PASSORD, postboks, "per@nordvik.example"):
        assert ikke not in logg, "persondata eller passord i loggen"


@pg_plan
def test_avvist_innlogging_feiler_kilden_og_brudd_rorer_ingenting(
        migrator, miljo, app, monkeypatch):
    from plan.epost_imap import kjor_en_runde
    _m365(monkeypatch)
    kid, _pb, _dek = _imap_kilde(migrator)
    boks = _Postboks()
    pa = _pa()
    try:
        boks.nede = True
        res = kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                            sender=lambda *a: None)
        assert "forbigaende" in _mine(res, kid)[0]
        assert _kilde(migrator, kid)[0][0] == "aktiv"

        boks.nede, boks.avvis = False, True
        res = kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                            sender=lambda *a: None)
        assert _mine(res, kid)[0]["feilet"] == "innlogging"
        assert _kilde(migrator, kid)[0][0] == "feilet"
        # En feilet kilde er ikke kandidat lenger.
        res = kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                            sender=lambda *a: None)
        assert _mine(res, kid) == []
    finally:
        pa.close()


@pg_plan
def test_de_to_veiene_deler_ikke_kandidater(migrator, miljo, app,
                                           monkeypatch):
    """En IMAP-kilde i Microsoft-runden ville fått et tokenforsøk og blitt
    satt `feilet`. Dørene skiller dem."""
    from plan import epost, epost_imap

    from .test_m6_inntak_port import _kilde as _m365_kilde
    _m365(monkeypatch)
    imap_kid, _pb, _dek = _imap_kilde(migrator)
    m365_kid, _key, _d = _m365_kilde(migrator)
    pa = _pa()
    try:
        ms = {str(r[1]) for r in epost.kandidater(pa, 200)}
        im = {str(r[1]) for r in epost_imap.kandidater(pa, 200)}
        assert str(m365_kid) in ms and str(imap_kid) not in ms
        assert str(imap_kid) in im and str(m365_kid) not in im
        kall = []
        res = epost.kjor_en_runde(
            app.tjeneste, pa, veksler=_veksler({}),
            graf=lambda a, url, **k: kall.append(url) or {
                "value": [], "@odata.deltaLink": "x"})
        assert not [r for r in res["resultater"]
                    if r["kilde"] == str(imap_kid)[:8]]
    finally:
        pa.close()
    assert _kilde(migrator, imap_kid)[0][0] == "aktiv"


# ---------------------------------------------------------------------------
# 5. Utsendingen
# ---------------------------------------------------------------------------

@pg_plan
def test_svaret_gaar_ut_over_smtp_i_traaden_en_gang(migrator, miljo, app,
                                                   klient, token,
                                                   monkeypatch):
    from plan import epost
    from plan.epost_imap import ImapForbigaende, kjor_en_runde

    from .test_m6_svarutkast_port import SVAR, _adm, _post
    _m365(monkeypatch)
    kid, postboks, _dek = _imap_kilde(migrator)
    boks = _Postboks()
    pa = _pa()
    sendt = []

    def sender(tjeneste, bruker, passord, melding):
        sendt.append((tjeneste, bruker, passord, melding))

    try:
        kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(), sender=sender)
        boks.legg_til(_raa(mid="<sporsmaal@mail.example>", emne="Befaring"))
        kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(), sender=sender)
        _sett_kontekst(migrator, TENANT)
        mid = migrator.execute(
            "SELECT melding_id FROM epost_melding WHERE tenant=%s"
            " AND kilde_id=%s", (TENANT, kid)).fetchone()[0]
        migrator.rollback()

        tok = _adm(token)
        uid = _post(klient, tok, f"/v1/epost/meldinger/{mid}/svarutkast",
                    {"tekst": SVAR}).json()["utkast_id"]
        r = _post(klient, tok, f"/v1/epost/utkast/{uid}/send", {})
        assert r.status_code == 200 and r.json()["status"] == "sendes", r.text

        # Microsoft-senderen tar den ALDRI: feil vei ville postet til Graph.
        graph = []
        epost.send_runde(app.tjeneste, pa, veksler=_veksler({}),
                         poster=lambda a, url, k: graph.append(url))
        assert graph == []

        # Et nettverksbrudd FØR noe er tatt imot: utkastet står i kø.
        def nede(*a):
            raise ImapForbigaende("OSError")
        res = kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                            sender=nede)
        mine = [x for x in res["svar"]["resultater"]
                if x["utkast"] == str(uid)[:8]]
        assert mine and "forbigaende" in mine[0], res

        res = kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                            sender=sender)
        mine = [x for x in res["svar"]["resultater"]
                if x["utkast"] == str(uid)[:8]]
        assert mine and mine[0].get("sendt") is True, res
        assert len(sendt) == 1
        tjeneste, bruker, passord, melding = sendt[0]
        assert (tjeneste, bruker, passord) == ("gmail", postboks, PASSORD)
        assert melding["From"] == postboks
        assert melding["To"] == "per@nordvik.example"
        assert melding["Subject"] == "Re: Befaring"
        assert melding["In-Reply-To"] == "<sporsmaal@mail.example>"
        assert melding.get_content().strip() == SVAR

        # Runde to: ikke kandidat lenger — svaret går ikke to ganger.
        res = kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                            sender=sender)
        assert not [x for x in res["svar"]["resultater"]
                    if x["utkast"] == str(uid)[:8]]
        assert len(sendt) == 1
    finally:
        pa.close()


@pg_plan
def test_en_usikker_smtp_feil_sendes_aldri_paa_nytt(migrator, miljo, app,
                                                   klient, token,
                                                   monkeypatch):
    """Vet vi ikke om serveren tok imot, skal svaret IKKE gå en gang til av
    seg selv: et menneske ser det som feilet og avgjør."""
    import smtplib

    from plan.epost_imap import kjor_en_runde

    from .test_m6_svarutkast_port import SVAR, _adm, _post
    _m365(monkeypatch)
    kid, _pb, _dek = _imap_kilde(migrator)
    boks = _Postboks()
    pa = _pa()
    forsok = []

    def usikker(*a):
        forsok.append(1)
        raise smtplib.SMTPServerDisconnected("brutt midt i DATA")

    try:
        kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                      sender=usikker)
        boks.legg_til(_raa())
        kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                      sender=usikker)
        _sett_kontekst(migrator, TENANT)
        mid = migrator.execute(
            "SELECT melding_id FROM epost_melding WHERE tenant=%s"
            " AND kilde_id=%s", (TENANT, kid)).fetchone()[0]
        migrator.rollback()
        tok = _adm(token)
        uid = _post(klient, tok, f"/v1/epost/meldinger/{mid}/svarutkast",
                    {"tekst": SVAR}).json()["utkast_id"]
        _post(klient, tok, f"/v1/epost/utkast/{uid}/send", {})
        res = kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                            sender=usikker)
        mine = [x for x in res["svar"]["resultater"]
                if x["utkast"] == str(uid)[:8]]
        assert mine and mine[0]["feilet"].startswith("smtp_"), res
        kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                      sender=usikker)
        assert len(forsok) == 1, "svaret ble forsøkt sendt to ganger"
    finally:
        pa.close()


# ---------------------------------------------------------------------------
# 6. Tilkoblingen
# ---------------------------------------------------------------------------

def _koble(klient, tok, kropp):
    return klient.post("/v1/epost/kilder/imap", json=kropp, headers={
        "authorization": f"Bearer {tok}",
        "Idempotency-Key": "imap-" + secrets.token_hex(8)})


@pg
def test_tilkoblingen_prover_innloggingen_for_noe_lagres(
        migrator, miljo, klient, token, monkeypatch):
    from plan import epost_imap
    tok, _ = token(rolle="admin",
                   scopes=("epost:read", "epost:kilde:administrer"))
    postboks = f"Felles-{secrets.token_hex(4)}@Gmail.example"
    forsok = []

    def antall():
        _sett_kontekst(migrator, TENANT)
        n = migrator.execute(
            "SELECT count(*) FROM epost_kilde WHERE tenant=%s AND postboks=%s",
            (TENANT, postboks.lower())).fetchone()[0]
        migrator.rollback()
        return n

    def avvist(tjeneste, bruker, passord):
        forsok.append((tjeneste, bruker, passord))
        raise epost_imap.ImapAvvist("nei")
    monkeypatch.setattr(epost_imap, "prov_innlogging", avvist)

    # Et vanlig kontopassord tas ALDRI imot — det når ikke engang Google.
    for ikke in ("Sommer2026!", "", None, "abcdefghijklmno"):
        r = _koble(klient, tok, {"postboks": postboks, "passord": ikke})
        assert r.status_code == 400, (ikke, r.text)
    assert forsok == []
    r = _koble(klient, tok, {"postboks": "ikke en adresse",
                             "passord": PASSORD})
    assert r.status_code == 400
    r = _koble(klient, tok, {"postboks": postboks, "passord": PASSORD,
                             "tjeneste": "imap.ond.example"})
    assert r.status_code == 400 and forsok == []

    # Google sier nei: ingenting lagres.
    r = _koble(klient, tok, {"postboks": postboks,
                             "passord": "abcd efgh ijkl mnop"})
    assert r.status_code == 400, r.text
    assert r.json()["feil"] == "imap_innlogging_avvist"
    assert forsok == [("gmail", postboks.lower(), PASSORD)]
    assert antall() == 0

    def nede(*a):
        raise epost_imap.ImapForbigaende("OSError")
    monkeypatch.setattr(epost_imap, "prov_innlogging", nede)
    r = _koble(klient, tok, {"postboks": postboks, "passord": PASSORD})
    assert r.status_code == 502 and antall() == 0

    # Google sier ja: kilden finnes, kryptert, og passordet kommer ikke ut.
    monkeypatch.setattr(epost_imap, "prov_innlogging", lambda *a: None)
    r = _koble(klient, tok, {"postboks": postboks,
                             "passord": "abcd efgh ijkl mnop"})
    assert r.status_code == 200, r.text
    assert PASSORD not in r.text
    assert r.json()["leverandor"] == "imap" and antall() == 1
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT auth_kryptert, scope, status FROM epost_kilde WHERE tenant=%s"
        " AND postboks=%s", (TENANT, postboks.lower())).fetchone()
    migrator.rollback()
    assert PASSORD.encode() not in bytes(rad[0])
    assert rad[1] == "imap smtp" and rad[2] == "aktiv"
    r = klient.get("/v1/epost/kilder",
                   headers={"authorization": f"Bearer {tok}"})
    assert PASSORD not in r.text
    mine = [k for k in r.json()["kilder"]
            if k["postboks"] == postboks.lower()]
    assert mine and mine[0]["kan_svare"] is True
    assert mine[0]["leverandor"] == "imap"

    # En leser kan ikke koble til.
    les, _ = token(rolle="leser", scopes=("epost:read",))
    assert _koble(klient, les, {"postboks": postboks,
                                "passord": PASSORD}).status_code == 403
