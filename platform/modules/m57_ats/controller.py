"""Controlleren for m57_ats: claim → hent bunt → heartbeat → evaluer →
rapport → kvittering. m56-formen (`m56_wcag_audit.controller`), speilet
— avvikene er buntveien (060-resolveren i stedet for mal_url) og
heartbeatet (063-fornyelsen: evalueringer kan lovlig vare lenger enn
ett grant-vindu).

Alt som kan hindre en gyldig leveranse måles FØR arbeidet starter
(m56-doktrinen: en umulig levering skal ikke koste noen data/regnekraft)
— og hvert utfall er et KODET ord til plattformen, aldri taushet.
"""
from __future__ import annotations

import hashlib
import tempfile
import threading
import time
from pathlib import Path

import os

import jsonschema

from . import kjoring, rapportskjema

#: m56-formen: leveringsforsøk og pause per steg.
LEVERINGSFORSOK = 4
LEVERINGSPAUSE_S = 5.0
#: Heartbeatets rytme: godt innenfor per-grant-taket (3600 s), og
#: vinduet det ber om er lite med vilje — en utfører som slutter å
#: puste mister autoriteten raskt (063s egen doktrine).
FORNY_INTERVALL_S = 240.0
FORNY_LEASE_S = 600
#: Margin reservert til rapportbygging + levering + kvittering.
AVSLUTNINGSMARGIN_S = 120.0


def http_frist_s(margin_s: float = AVSLUTNINGSMARGIN_S) -> float:
    """Transportfristen per kall, avledet av avslutningsbudsjettet delt
    på kallene som faktisk gjøres (m56-formen)."""
    return margin_s / (2 * LEVERINGSFORSOK)


def _sov(sekunder: float) -> None:
    time.sleep(sekunder)


class _Uteblitt:
    """Et svar som aldri kom: status 0, uleselig kropp — samme form som
    m56, så alle porter kan behandle tap og avvisning likt."""

    status_code = 0

    def __init__(self, grunn: str = "intet svar"):
        self.grunn = grunn

    def json(self):
        raise ValueError(self.grunn)


def _vindu_apent(raa: object) -> bool:
    if not isinstance(raa, str):
        return False
    try:
        from datetime import datetime, timezone
        frist = datetime.fromisoformat(raa)
        if frist.tzinfo is None:
            # Plattformens tider er UTC; en naiv ISO-form leses som det
            # i stedet for å felle sammenligningen med TypeError.
            frist = frist.replace(tzinfo=timezone.utc)
        return frist > datetime.now(timezone.utc)
    except ValueError:
        return False


def _kvittert(rk) -> bool:
    return 200 <= getattr(rk, "status_code", 0) < 300


def _feilutfall(rk, grunn: str, **ekstra) -> dict:
    return {"utfall": "avbrutt", "grunn": grunn,
            "kvittering_status": getattr(rk, "status_code", 0), **ekstra}


def _payloadbrudd(payload: dict) -> str | None:
    """Bestillingens gjennomførbarhet, lest FØR noe arbeid: profilen og
    tallet må være der kontrakten (payload-skjemaet) lover."""
    if not isinstance(payload, dict):
        return "payload"
    profil = payload.get("stillingsprofil")
    if not isinstance(profil, dict):
        return "stillingsprofil"
    krav = profil.get("krav")
    if not isinstance(krav, list) or not krav:
        return "krav"
    for rad in krav:
        if not isinstance(rad, dict) \
                or not isinstance(rad.get("kravnavn"), str) \
                or not isinstance(rad.get("vekt"), int) \
                or isinstance(rad.get("vekt"), bool):
            return "krav"
    antall = payload.get("antall_soknader")
    if not isinstance(antall, int) or isinstance(antall, bool) \
            or antall < 1:
        return "antall_soknader"
    return None


class _Heartbeat:
    """063-fornyelsen i egen tråd: holder leasen levende gjennom
    evalueringen og bytter til FERSK opplastingskapabilitet når
    fornyelsen re-utsteder en. En fornyelse plattformen AVVISER (4xx)
    betyr at autoriteten er tapt — tråden stopper og taper-koden står
    igjen til utfallsrapporten; selve leveringsforsøket avgjøres uansett
    av plattformens egne porter (ærlig avvisning der, aldri gjetting
    her)."""

    def __init__(self, klient, hode, claim):
        self._klient = klient
        self._hode = hode
        self._kropp = {"oppdrag_id": claim["oppdrag_id"],
                       "owner_claim_id": claim["owner_claim_id"],
                       "owner_generation": claim["owner_generation"],
                       "lease_s": FORNY_LEASE_S}
        self._stopp = threading.Event()
        self._traad = threading.Thread(target=self._lopp, daemon=True)
        self.fersk_opplasting = None
        self.tapt: str | None = None

    def __enter__(self):
        self._traad.start()
        return self

    def __exit__(self, *unntak):
        self._stopp.set()
        self._traad.join(timeout=http_frist_s() * 2 + 1)

    def _lopp(self):
        while not self._stopp.wait(FORNY_INTERVALL_S):
            try:
                r = self._klient.post("/v1/oppdrag/forny",
                                      json=self._kropp,
                                      headers=self._hode)
            except Exception:                       # noqa: BLE001
                continue                # transport: neste puls prøver igjen
            if 200 <= r.status_code < 300:
                try:
                    opp = r.json().get("opplasting")
                except ValueError:
                    continue
                if opp:
                    self.fersk_opplasting = opp
            elif 400 <= r.status_code < 500:
                try:
                    self.tapt = r.json().get("feil", str(r.status_code))
                except ValueError:
                    self.tapt = str(r.status_code)
                return


def kjor_en(klient, token: str, modell, uttrekker, biasmaalinger,
            signer) -> dict:
    """-> {"utfall": "tomt"|"utfort"|"avbrutt"|"ukvittert", ...}."""
    hode = {"authorization": f"Bearer {token}"}
    r = klient.post("/v1/oppdrag/claim", json={}, headers=hode)
    if r.status_code == 204:
        return {"utfall": "tomt"}
    r.raise_for_status()
    claim = r.json()
    payload = claim["payload"]

    kvittering_basis = {
        "oppdrag_id": claim["oppdrag_id"], "tenant": claim["tenant"],
        "kvittering_jti": claim["kvittering_jti"],
        "repair_operation_id": claim["repair_operation_id"],
        "owner_claim_id": claim["owner_claim_id"],
        "owner_generation": claim["owner_generation"],
        "ressurs_id": f"oppdrag:{claim['oppdrag_id']}",
    }

    def lever(sti, kropp, utloper, *, gjenlosbar_etter_utlop=False):
        """m56s leveringsløkke, ordrett i semantikk: 5xx/tapt svar
        retryes (idempotente endepunkter), 4xx aldri, 2xx er ferdig."""
        rk = _Uteblitt()
        for forsok in range(LEVERINGSFORSOK):
            if forsok:
                if not gjenlosbar_etter_utlop and not _vindu_apent(utloper):
                    break
                _sov(LEVERINGSPAUSE_S * forsok)
            try:
                rk = klient.post(sti, json=kropp, headers=hode)
            except Exception as e:                  # noqa: BLE001
                rk = _Uteblitt(f"{type(e).__name__}: intet svar")
                continue
            if rk.status_code < 500:
                break
        return rk

    def kvitter(kropp):
        return lever("/v1/oppdrag/kvittering", signer(kropp),
                     claim.get("kvittering_utloper"))

    brudd = _payloadbrudd(payload)
    if brudd:
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "oppdrag_ugyldig"})
        return _feilutfall(rk, f"oppdrag_ugyldig:{brudd}")

    opplasting = claim.get("opplasting")
    if not opplasting:
        # Uten leveringsvei skal ingen persondata hentes i det hele tatt
        # (m56-regnestykket, med data i stedet for trafikk).
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "ingen_opplastingskapabilitet"})
        return _feilutfall(rk, "ingen_kapabilitet")

    # BUNTEN: 060-resolveren — payloaden navngir den aldri (#200 valg B);
    # retten er claimet selv.
    rb = lever(f"/v1/inndata/hent-for-oppdrag/{claim['oppdrag_id']}",
               {"owner_claim_id": claim["owner_claim_id"]},
               claim.get("utforelsesfrist"))
    if not 200 <= rb.status_code < 300:
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "bunt_uhentbar"})
        return _feilutfall(rk, "bunt_uhentbar", bunt_status=rb.status_code)
    raa = getattr(rb, "content", None)
    if not raa:
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "bunt_uhentbar"})
        return _feilutfall(rk, "bunt_tom")

    profil = payload["stillingsprofil"]
    vekter = {rad["kravnavn"]: rad["vekt"] for rad in profil["krav"]}

    with tempfile.TemporaryDirectory(prefix="m57-bunt-") as katalog:
        filsti = Path(katalog) / "bunt.zip"
        filsti.write_bytes(raa)
        # INSTANSBINDING VIA INODE (eierdom #217 runde 7): kjøringen får
        # /proc/self/fd-stien til VÅR åpne fd — alle parsingens åpninger
        # treffer da samme inode, og et stibytte i vinduet mellom
        # manifest- og innholdslesing kan per konstruksjon ikke nå den.
        fd = os.open(filsti, os.O_RDONLY)
        sti = f"/proc/self/fd/{fd}"
        with _Heartbeat(klient, hode, claim) as puls:
            try:
                resultat = kjoring.kjor_bunt(
                    sti, modell, vekter=vekter,
                    tekst_for=uttrekker.tekst_for,
                    biasmaalinger=biasmaalinger,
                    antall_soknader=payload["antall_soknader"])
                rapport = rapportskjema.bygg(
                    resultat, profil=profil,
                    antall_soknader=payload["antall_soknader"])
                jsonschema.Draft202012Validator(
                    rapportskjema.SKJEMA).validate(rapport)
            except kjoring.Kjoringsfeil as e:
                rk = kvitter({**kvittering_basis, "resultat": "feilet",
                              "feilkode": "kjoring_avbrutt"})
                return _feilutfall(rk, f"kjoring_avbrutt:{e.kode}")
            except jsonschema.ValidationError:
                rk = kvitter({**kvittering_basis, "resultat": "feilet",
                              "feilkode": "kjoring_avbrutt"})
                return _feilutfall(rk, "rapport_ugyldig")
            finally:
                os.close(fd)
        if puls.fersk_opplasting:
            # Fornyelsen re-utstedte leveringsretten — claimens
            # opprinnelige kan være død av sitt eget grant-vindu.
            opplasting = puls.fersk_opplasting

    ro = lever("/v1/artefakt",
               {"kapabilitet_jti": opplasting["jti"], "rapport": rapport},
               opplasting.get("utloper"), gjenlosbar_etter_utlop=True)
    if not 200 <= ro.status_code < 300:
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "opplasting_avvist"})
        return _feilutfall(rk, "opplasting_avvist",
                           opplasting_status=ro.status_code,
                           lease_tapt=puls.tapt)
    try:
        artefakt = ro.json()
        artefakt_id = artefakt["artefakt_id"]
        klartekst_sha256 = artefakt["klartekst_sha256"]
    except (ValueError, TypeError, KeyError) as e:
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "opplasting_avvist"})
        return _feilutfall(rk, f"opplasting_uleselig:{type(e).__name__}")

    rk = kvitter({**kvittering_basis, "resultat": "utfort",
                  "artefakt_id": artefakt_id,
                  "klartekst_sha256": klartekst_sha256})
    svar = {"artefakt_id": artefakt_id,
            "kvittering_status": rk.status_code,
            "kandidater": len(rapport["rangering"]),
            "bunt_sha256": hashlib.sha256(raa).hexdigest()}
    if not _kvittert(rk):
        return {"utfall": "ukvittert", **svar}
    return {"utfall": "utfort", **svar}
