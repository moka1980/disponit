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

#: Modulens ene oppdragstype — nøkkelen inn i plattformens
#: `OPPDRAGSTYPER`, som eier både feltbredden og verdikontrakten.
#: Modulen gjentar ingen av delene, den slår dem opp (m56s `OPPDRAGSTYPE`,
#: samme rolle): to sett regler ville betydd at bestillingsveien og
#: utføreren kunne vært uenige om hva et lovlig oppdrag er.
OPPDRAGSTYPE = "rekruttering.evaluering"

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

#: Kroppsstatusene som betyr at plattformen FAKTISK skiftet status på
#: oppdraget — de `/v1/oppdrag/kvittering` gir sammen med 2xx.
#: `idempotent` er med fordi en RETRY av en kvittering som allerede
#: avsluttet oppdraget er den dokumenterte suksessveien, ikke et avvik.
#: Endepunktet er plattformens felles kvitteringsvei, delt med m56 —
#: ordene er derfor de samme (`api.app`: `status: "utfort"|"feilet"` ved
#: statusskifte, `idempotent` ved gjenkjent gjentakelse).
_STATUSSKIFTE = ("utfort", "feilet", "idempotent")


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
    """Skiftet plattformen status på oppdraget?

    2xx alene er IKKE nok (m56s Codex P1, speilet). Fullføres kjøringen
    etter `utforelsesfrist` men før evidensfristen, svarer
    `/v1/oppdrag/kvittering` 202 med `status:
    "lagret_uten_statusendring"`: evidensen bevares, og det er HELE det
    som skjedde — oppdraget står bevisst ufullført hos plattformen. Å
    lese den 202-en som en kvittering ga `utfall: "utfort"` for et
    oppdrag plattformen selv regner som uferdig, og planleggeren som tror
    på modulens ord slutter da å følge opp noe som aldri ble avsluttet.

    En kropp vi ikke kan lese (ikke JSON, ikke et objekt) er heller ingen
    bekreftelse: da vet vi ikke hva som skjedde, og «vet ikke» skal
    behandles som uferdig — fail-closed, aldri ferdig."""
    if not 200 <= getattr(rk, "status_code", 0) < 300:
        return False
    try:
        kropp = rk.json()
    except (ValueError, TypeError):
        return False
    return isinstance(kropp, dict) and kropp.get("status") in _STATUSSKIFTE


def _feilutfall(rk, grunn: str, **ekstra) -> dict:
    """Utfallet for en kjøring som feilet — AVLEDET av `_kvittert`, ikke
    antatt (m56s Codex P1, speilet).

    Feilgrenene meldte `avbrutt` uansett hva plattformen svarte på
    feil-kvitteringen. `avbrutt` betyr «oppdraget er FERDIG mislykket»,
    og det er nettopp det plattformen ikke har bekreftet når kvitteringen
    ble avvist med 409/5xx, tapt i transporten (`_Uteblitt`, status 0)
    eller lagret som sen evidens med 202: da står oppdraget fortsatt
    claimet og uferdig der, akkurat som når en SUKSESS-kvittering blir
    avvist. Suksessgrenen leste `_kvittert`; feilgrenene gjorde det ikke,
    og forskjellen var vilkårlig — en planlegger som tror på `avbrutt`
    slutter å følge et oppdrag som aldri ble avsluttet.

    `grunn` og `kvittering_status` blir stående uansett utfall: hvorfor
    kjøringen feilet er like sant om kvitteringen kom frem eller ikke."""
    kvittert = _kvittert(rk)
    return {"utfall": "avbrutt" if kvittert else "ukvittert",
            "grunn": grunn, "kvittert": kvittert,
            "kvittering_status": getattr(rk, "status_code", 0), **ekstra}


def _evalueringsfrist(claim: dict) -> int | None:
    """Sekundene evalueringen FAKTISK har på seg — eller None når claimet
    ikke bærer et lesbart vindu (m56s `_skannefrist`, speilet).

    Vinduet er den TIDLIGSTE av grensene claimet selv navngir:

      * `utforelsesfrist` — etter den kan ikke kvitteringen lenger
        avslutte oppdraget (endepunktet svarer 202
        `lagret_uten_statusendring`),
      * `opplasting.utloper` — etter den kan rapporten ikke lastes opp,
      * `kvittering_utloper` — etter den kan kvitteringen ikke sendes.

    Alle tre er absolutte, så den første som inntreffer er den som
    gjelder. `AVSLUTNINGSMARGIN_S` trekkes fra: rapportbygging,
    opplasting og signert kvittering er det som gjør et fullført arbeid
    til et AVSLUTTET oppdrag, og en evaluering som får bruke helt frem
    til grensen leverer ingenting.

    En frist uten tidssone gir None: å gjette sonen er timer feil vei, og
    plattformen sender alltid UTC-offset.

    PORTEN, IKKE ET BUDSJETT (K1): tallet brukes til å AVVISE et claim
    som er dødfødt før bunten hentes. Å stoppe en evaluering som ble
    startet i tide, men løper forbi vinduet, krever en frist HELT NED i
    `kjoring.kjor_bunt` og modellklienten — ny maskin, ikke en fiks. Det
    heartbeatet (063) fornyer er leasen, ikke disse grensene."""
    if claim.get("utforelsesfrist") is None:
        return None
    from datetime import datetime, timezone
    grenser = []
    for raa in (claim.get("utforelsesfrist"),
                claim.get("kvittering_utloper"),
                (claim.get("opplasting") or {}).get("utloper")):
        if raa is None:
            continue
        try:
            t = datetime.fromisoformat(str(raa))
        except ValueError:
            return None
        if t.tzinfo is None:
            return None
        grenser.append(t)
    igjen = (min(grenser) - datetime.now(timezone.utc)).total_seconds()
    return int(igjen) - int(AVSLUTNINGSMARGIN_S)


def _payloadbrudd(payload: dict) -> str | None:
    """Bestillingens gjennomførbarhet, lest FØR noe arbeid — eller None.

    KONTRAKTEN ER PLATTFORMENS, IKKE MODULENS (m56s `_kontraktsbrudd`,
    speilet). Den håndrullede sjekken her leste bare profilen og at
    `antall_soknader >= 1`, og var dermed en ANNEN port enn den som
    slapp oppdraget gjennom ved opprettelsen: `oppdragskontrakt` binder
    også `omfang` til «bunt», `antall_soknader` til 1–5000 (klarsignalet
    §4 — 5001 avvises, aldri stille avkorting), `slettefrist_dogn` til
    30–365 og `stillingsprofil_ref` til en ikke-tom streng. Et eldre
    eller korrupt claim med `antall_soknader: 5001` kunne derfor hentes
    og evalueres av utføreren selv om bestillingsveien ville avvist det.

    To sett regler betyr at de to sidene kan være uenige om hva et lovlig
    oppdrag er. Den samme tabellen leses nå begge steder. Porten står
    likevel HER og ikke bare der: raden kan være skrevet av en eldre
    release, og en utfører som stoler på at noen andre har sjekket,
    sjekker ikke.

    Profilens INDRE form er modulens egen og blir stående ved siden av:
    kontrakten krever at `stillingsprofil` finnes, mens det er
    controlleren som leser `krav[].kravnavn/vekt` ut til vektkartet.
    Bare feltNAVN rapporteres videre, aldri verdier — grunnen havner i
    driftsloggen, og en søknadsbestilling er saksdata."""
    if not isinstance(payload, dict):
        return "payload"
    from oppdragskontrakt import bryter_feltkontrakten, mangler_paakrevde
    brudd = sorted({*mangler_paakrevde(OPPDRAGSTYPE, payload),
                    *bryter_feltkontrakten(OPPDRAGSTYPE, payload)})
    if brudd:
        return ",".join(brudd)
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

    frist_s = _evalueringsfrist(claim)
    if frist_s is None or frist_s <= 0:
        # FRISTEN ER EN DEL AV BESTILLINGEN (m56s Codex P1, speilet). Er
        # vinduet uleselig eller alt oppbrukt, kan denne kjøringen aldri
        # bli et avsluttet oppdrag — og da skal den ikke koste
        # PERSONDATA og modellkall, av samme grunn som `_payloadbrudd` og
        # kapabilitetssjekken over. m57s versjon av m56-doktrinen: der
        # skaden er den unødvendige forespørselen ut, er den her den
        # unødvendige utleveringen av søknadsbunten INN i containeren.
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "frist_utilstrekkelig"})
        return _feilutfall(rk, "frist_utilstrekkelig", frist_s=frist_s)

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
        if puls.tapt:
            # AUTORITETEN ER TAPT, OG DA LEVERES DET IKKE. En terminal
            # 4xx på `/v1/oppdrag/forny` betyr at plattformen har gitt
            # oppdraget til noen andre eller lukket det: leasen er ikke
            # vår lenger, `owner_generation` er utdatert, og evalueringen
            # ble ferdig uten gyldig autoritet.
            #
            # Uten denne porten ble taperen stående og laste opp likevel
            # — og resultatet var enten en avvisning på plattformens egne
            # porter (samme utfall, men etter at rapporten var sendt) —
            # eller, om vinduet så vidt holdt, et artefakt fra en utfører
            # som ikke lenger eier oppdraget. `tapt` ble bare rapportert
            # som et ekstra felt PÅ vei ut av en opplasting som allerede
            # hadde feilet; nå stopper den før den.
            #
            # Kvitteringen sendes uansett: taushet er det §10 forbyr, og
            # feilkoden navngir nettopp at det var autoriteten som falt
            # bort, ikke arbeidet.
            rk = kvitter({**kvittering_basis, "resultat": "feilet",
                          "feilkode": "lease_tapt"})
            return _feilutfall(rk, "lease_tapt", lease_tapt=puls.tapt)
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
        # `lease_tapt` sto her som et ekstra felt. Porten rett over
        # returnerer nå FØR opplastingen når leasen er tapt, så feltet
        # kunne per konstruksjon aldri være annet enn None her.
        return _feilutfall(rk, "opplasting_avvist",
                           opplasting_status=ro.status_code)
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
