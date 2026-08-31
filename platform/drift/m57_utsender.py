"""M-57-utsenderen: konsumsjonen av signerte utsendingslister (081).

Formen er varselsenderens — oneshot bak en timer, senderrollens DSN,
klaim committes FØR SMTP — men DOMMEN er en annen, for dette er ikke en
kopi av portalen: utsendelsen er IRREVERSIBEL. Derfor:

* Et klaim som dør mellom SMTP-aksept og kvittering blir TERMINALT
  `uviss` (m57_merk_uviss) og går ALDRI tilbake i køen — «kan alt ha
  gått ut» er et menneskes dom, ikke en retry. Varselkøens lease-retur
  gjelder kopier; her ville den vært dobbeltsending.
* Kun feil som BEVISELIG skjedde FØR aksept (flettefeil, avvist
  mottaker/oppkobling/innlogging — `_FEIL_FOER_AKSEPT`) kvitteres
  `feilet` og prøves igjen, opp til MAKS_FORSOK. Alle andre unntak
  (timeout midt i dialogen, brutt forbindelse etter DATA) kvitteres
  IKKE: klaimet blir stående og lease-utløpet feller `uviss`-dommen —
  usikkerhet arves aldri inn i en retry (CodeRabbit).

Innholdet UTLEDES av signert tilstand i basen (manifestet, 080) —
aldri av en payload utenfor signaturens dekning (#149): mottaker og
flettefelt leses bak manifestraden, teksten er malens (`maler.flett`,
port 13/14), og firmateksten er kundens referanse når den kommer —
`None` («ingen tone») til kunden har koblet en (#160-lageret står
klart). Frigivelsen fødes idempotent i klaim-døren (078 pseudonymiserer;
056 håndhever signatur + antallstak).

Rollen er `disponit_varselsender`: null tabellrettigheter, kun EXECUTE
på de fem 081-dørene (+ frigivelsesveien fra 056). Kryss-tenant-evnen
bor INNE i domene-eide definer-dører (027-formen).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
import uuid

import smtplib

from drift.varselsender import _locale, _send_ekte, _smtp_oppsett, rendre

GRENSE = int(os.environ.get("DISPONIT_M57_UTSENDER_GRENSE", "50"))
MAKS_FORSOK = int(os.environ.get("DISPONIT_M57_UTSENDER_MAKS_FORSOK", "3"))
FRIST_S = int(os.environ.get("DISPONIT_M57_UTSENDER_FRIST_S", "240"))
LEASE_MIN = int(os.environ.get("DISPONIT_M57_UTSENDER_LEASE_MIN", "30"))
SPRAK = os.environ.get("DISPONIT_VARSEL_SPRAK", "nb")
#: M-8 (082, DOM 5): tokenlevetiden — døren tar selv least(now() +
#: levetid, payloadvinduets slutt), så tallet her er TAKET, aldri en
#: forlengelse av kundens frist.
TIDSVALG_LEVETID_DOGN = int(os.environ.get(
    "DISPONIT_M8_TIDSVALG_LEVETID_DOGN", "30"))

#: Unntak som BEVISER at ingen e-post ble akseptert: fletting skjer før
#: nettet, og disse SMTP-klassene reises før serveren har tatt imot
#: meldingen. Alt utenfor settet er et UVISST utfall.
_FEIL_FOER_AKSEPT = (smtplib.SMTPRecipientsRefused,
                     smtplib.SMTPSenderRefused,
                     smtplib.SMTPAuthenticationError,
                     smtplib.SMTPHeloError,
                     smtplib.SMTPConnectError,
                     ConnectionRefusedError)

#: Emnenøklene per listetype — tekst bor i locales/, aldri her.
_EMNE = {"invitasjon": "rekruttering.utsending.emne.invitasjon",
         "avslag": "rekruttering.utsending.emne.avslag"}


def _mac(pepper: str, secret: str) -> str:
    """modulonboarding._mac-formen (api.app._mac, speilet — drift
    importerer aldri app-modulen): serversiden lagrer kun MAC-en,
    pepperet bor i prosessen (LoadCredential) og aldri i basen."""
    return hmac.new(pepper.encode("utf-8"), secret.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def _tidsvalg_oppsett() -> tuple[str, str] | None:
    """(pepper, host) når tidsvalg-lenker kan myntes, ellers None.

    Manglende pepper/host er en DRIFTSTILSTAND (smtp_ikke_konfigurert-
    dommen): invitasjonsrader RØRES ikke — et klaim uten token ville
    enten sendt en død lenke eller brent forsøkstelleren på config."""
    pepper = os.environ.get("DISPONIT_TOKEN_PEPPER", "")
    host = os.environ.get("DISPONIT_HOST", "")
    if len(pepper) < 32 or not host:
        return None
    return pepper, host


def kjor(conn, *, send=None, oppsett=None, sprak=None) -> dict:
    """Én kjøring: uviss-merking → tenanter → klaim → flett → send →
    kvitter. Returnerer tellere; exit-koden er kjøringens egen helse —
    en avvist adresse er data på raden, ikke en driftsfeil."""
    fra = time.monotonic()
    tekster = _locale(sprak or SPRAK)
    utfall = {"sendt": 0, "feilet": 0, "uviss_merket": 0, "mistet": 0,
              "stanset": "tom"}
    # Døde klaim merkes FØRST, i egen transaksjon: et uvisst utfall skal
    # stå som uvisst før denne kjøringen begynner å velge arbeid.
    utfall["uviss_merket"] = conn.execute(
        "SELECT m57_merk_uviss(make_interval(mins => %s))",
        (LEASE_MIN,)).fetchone()[0]
    conn.commit()
    smtp = oppsett if oppsett is not None else _smtp_oppsett()
    if send is None:
        if smtp is None:
            # Manglende oppsett er en DRIFTSTILSTAND: køen røres ikke,
            # ingen forsøksteller brennes (varselsender-dommen).
            utfall["stanset"] = "smtp_ikke_konfigurert"
            return utfall

        def send(til, emne, tekst):          # pragma: no cover - nettet
            _send_ekte(smtp, til, emne, tekst)

    from modules.m57_ats import maler

    tidsvalg = _tidsvalg_oppsett()
    tenanter = [r[0] for r in conn.execute(
        "SELECT m57_sendeklare_tenanter(%s, %s)",
        (GRENSE, MAKS_FORSOK)).fetchall()]
    conn.rollback()
    for tenant in tenanter:
        conn.execute(
            "SELECT set_config('disponit.tenant', %s, true),"
            "       set_config('disponit.aktor', 'm57-utsender', true),"
            "       set_config('disponit.request_id', %s, true)",
            (tenant, uuid.uuid4().hex[:12]))
        rader = conn.execute(
            "SELECT * FROM m57_neste_sendinger(%s, %s, %s)",
            (tenant, GRENSE, MAKS_FORSOK)).fetchall()
        conn.rollback()
        for (liste_id, listetype, _malversjon, kandidat_id, _m, felter,
             ft_ref, ft_versjon, ft_tekst) in rader:
            # Fristen sjekkes FØR et nytt klaim — det ene punktet der
            # ingenting er i luften (varselsender-dommen, ordrett).
            if time.monotonic() - fra > FRIST_S:
                utfall["stanset"] = "frist"
                return utfall
            # M-8 (082): en invitasjon UTEN mulig tidsvalg-lenke klaimes
            # aldri — manglende pepper/host er en driftstilstand, ikke
            # en feilet rad (raden står sendeklar til config er på plass).
            if listetype == "invitasjon" and tidsvalg is None:
                utfall["tidsvalg_stanset"] = utfall.get(
                    "tidsvalg_stanset", 0) + 1
                continue
            klaim = uuid.uuid4()
            conn.execute(
                "SELECT set_config('disponit.tenant', %s, true),"
                "       set_config('disponit.aktor', 'm57-utsender',"
                "                  true),"
                "       set_config('disponit.request_id', %s, true)",
                (tenant, uuid.uuid4().hex[:12]))
            rad = conn.execute(
                "SELECT ut_frigivelse, ut_mottaker FROM"
                " m57_start_sending(%s,%s,%s,%s,%s)",
                (tenant, liste_id, kandidat_id, klaim,
                 MAKS_FORSOK)).fetchone()
            conn.commit()                     # klaimet står FØR SMTP
            if rad is None:
                continue                      # noens klaim / reapet
            _frig, mottaker = rad
            # M-8 (082, §5): lenken flyttes fra lager til UTSTEDELSE —
            # tokenet myntes lokalt og committes i EGEN transaksjon FØR
            # send(): en e-post med død lenke er urepresenterbar
            # (rekkefølgeporten). Døren setter ev. eksisterende aktiv
            # token `erstattet`, så et nytt forsøk etter `feilet` aldri
            # etterlater to levende kapabiliteter; ved `uviss` kvitteres
            # ingenting og tokenet står aktivt (e-posten KAN være ute).
            tidsvalg_lenke = None
            if listetype == "invitasjon":
                pepper, host = tidsvalg
                token_id = secrets.token_hex(16)
                hemmelighet = secrets.token_hex(32)
                try:
                    conn.execute(
                        "SELECT set_config('disponit.tenant', %s, true),"
                        "       set_config('disponit.aktor',"
                        "                  'm57-utsender', true),"
                        "       set_config('disponit.request_id', %s,"
                        "                  true)",
                        (tenant, uuid.uuid4().hex[:12]))
                    conn.execute(
                        "SELECT m8_utsted_tidsvalgtoken(%s,%s,%s,%s,%s,"
                        "%s)",
                        (tenant, liste_id, kandidat_id, token_id,
                         _mac(pepper, hemmelighet),
                         TIDSVALG_LEVETID_DOGN))
                    conn.commit()             # tokenet står FØR SMTP
                except Exception as e:        # noqa: BLE001
                    # Beviselig FØR aksept: ingen e-post er sendt, så
                    # raden kvitteres `feilet` og prøves igjen (nytt
                    # forsøk minter nytt token).
                    conn.rollback()
                    conn.execute(
                        "SELECT set_config('disponit.tenant', %s, true),"
                        "       set_config('disponit.aktor',"
                        "                  'm57-utsender', true),"
                        "       set_config('disponit.request_id', %s,"
                        "                  true)",
                        (tenant, uuid.uuid4().hex[:12]))
                    conn.execute(
                        "SELECT m57_fullfor_sending(%s,%s,%s,%s,"
                        "'feilet',%s)",
                        (tenant, liste_id, kandidat_id, klaim,
                         f"tidsvalg_token_feilet: {type(e).__name__}"))
                    conn.commit()
                    utfall["feilet"] += 1
                    continue
                # Fragmentet (#) forlater aldri klienten — lenken kan
                # logges hos mottakerens e-posttjener, men serverloggen
                # vår ser aldri tokenet.
                tidsvalg_lenke = (f"https://{host}/tidsvalg"
                                  f"#tid_{token_id}.{hemmelighet}")
            try:
                # Lagerets flettefelt kan bære felter for BEGGE maltyper
                # (seedens form); malen får nøyaktig sine egne — et
                # manglende felt er fortsatt en ærlig Malfeil (port 14).
                mine = {k: v for k, v in dict(felter or {}).items()
                        if k in maler.MALER[listetype]["felter"]}
                if tidsvalg_lenke is not None:
                    # OVERSKRIV, aldri setdefault (§5): lagerets felt er
                    # historikk — lenken er UTSTEDELSENS, og bare den
                    # peker på et token som faktisk finnes.
                    mine["tidsvalg_lenke"] = tidsvalg_lenke
                # Tonen er LISTENS (083): signataren autoriserte den
                # eksakte versjonen, og døren JOINet teksten — None er
                # fortsatt 079-kontraktens ekte «ingen tone».
                tone = (maler.KundeeidFirmatekst(str(ft_ref), ft_versjon,
                                                 ft_tekst)
                        if ft_ref is not None and ft_tekst is not None
                        else None)
                flettet = maler.flett(listetype, mine, firmatekst=tone)
                emne = rendre(tekster, _EMNE[listetype],
                              {"stilling": (felter or {}).get(
                                  "stilling", "")})
                send(mottaker, emne, flettet["tekst"])
                status, feil = "sendt", None
                utfall["sendt"] += 1
            except maler.Malfeil as e:
                status, feil = "feilet", f"flettefeil: {e.kode}"
                utfall["feilet"] += 1
            except _FEIL_FOER_AKSEPT as e:
                status, feil = "feilet", f"{type(e).__name__}: {e}"
                utfall["feilet"] += 1
            except Exception as e:            # noqa: BLE001 — uviss
                # Utfallet er UVISST: ingen kvittering — klaimet står,
                # og lease-utløpet merker raden `uviss`, terminalt.
                utfall["uviss_underveis"] = utfall.get(
                    "uviss_underveis", 0) + 1
                utfall.setdefault("uviss_detalj", []).append(
                    f"{type(e).__name__}: {e}")
                continue
            conn.execute(
                "SELECT set_config('disponit.tenant', %s, true),"
                "       set_config('disponit.aktor', 'm57-utsender',"
                "                  true),"
                "       set_config('disponit.request_id', %s, true)",
                (tenant, uuid.uuid4().hex[:12]))
            ok = conn.execute(
                "SELECT m57_fullfor_sending(%s,%s,%s,%s,%s,%s)",
                (tenant, liste_id, kandidat_id, klaim, status,
                 feil)).fetchone()[0]
            conn.commit()
            if not ok:
                # Klaimet var ikke lenger vårt: utfallet eies av
                # uviss-merkingen, aldri av denne kjøringen.
                utfall["mistet"] += 1
    if utfall["stanset"] == "tom" and tenanter:
        utfall["stanset"] = "grense"
    return utfall
