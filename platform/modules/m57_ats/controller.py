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

import base64
import hashlib
import tempfile
import threading
import time
from datetime import datetime, timezone
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
#: Hvor mange PÅFØLGENDE stumme fornyelser (5xx/transport) som får passere
#: før autoriteten regnes som tapt — men BARE før den første bekreftede
#: fornyelsen. Etter den er horisonten serverens egen
#: (`owner_lease_utloper`), ikke et avledet tall her; se `_Heartbeat._utlopt`.
#: Avledningen står som den er for det ene vinduet den fortsatt gjelder:
#: etter k stumme pulser er det gått `k * FORNY_INTERVALL_S` siden tråden
#: startet, og et grant-vindu varer `FORNY_LEASE_S`. Er neste puls uansett
#: utenfor det vinduet, finnes det ingen lease igjen å redde — da er det
#: å fortsette bare arbeid uten autoritet.
FORNY_TAPT_ETTER = int(FORNY_LEASE_S // FORNY_INTERVALL_S)
#: Margin reservert til rapportbygging + levering + kvittering.
AVSLUTNINGSMARGIN_S = 120.0
#: `lever` kjøres TO ganger i en avslutning: opplastingen og kvitteringen.
LEVERINGSRUNDER = 2
#: Arbeidet MELLOM kallene — bygging og skjemavalidering av rapporten, og
#: signeringen av kvitteringen. Grovt, og med vilje romslig: det som
#: trekkes fra her blir ikke brukt på en HTTP-frist.
AVSLUTNINGSARBEID_S = 20.0

#: Kroppsstatusene som betyr at plattformen FAKTISK skiftet status på
#: oppdraget — de `/v1/oppdrag/kvittering` gir sammen med 2xx.
#: `idempotent` er med fordi en RETRY av en kvittering som allerede
#: avsluttet oppdraget er den dokumenterte suksessveien, ikke et avvik.
#: Endepunktet er plattformens felles kvitteringsvei, delt med m56 —
#: ordene er derfor de samme (`api.app`: `status: "utfort"|"feilet"` ved
#: statusskifte, `idempotent` ved gjenkjent gjentakelse).
_STATUSSKIFTE = ("utfort", "feilet", "idempotent")


def http_frist_s(margin_s: float = AVSLUTNINGSMARGIN_S) -> float:
    """Den lengste ETT HTTP-kall kan få og likevel holde hele
    avslutningens VERSTEFALL innenfor lukkevinduet (m56s formel,
    speilet).

    Fristen var `margin / (2 * LEVERINGSFORSOK)` — altså delt på kallene
    ALENE. PAUSENE mellom forsøkene sto utenfor budsjettet, og de er
    store her: `LEVERINGSPAUSE_S = 5.0` gir 0+5+10+15 = 30 sekunder per
    runde, 60 over begge. Med de gamle 15 sekundene per kall ble
    verstefallet 8·15 + 60 = 180 sekunder mot de 120 marginen ga hele
    avslutningen — en plattform som tar imot forbindelsen og så tier
    kunne dermed la kvitteringsvinduet løpe ut MIDT i retryen, og
    oppdraget sto ufullført.

    Fristen avledes derfor av marginen i stedet for å stå ved siden av
    den: åtte kall, pausene mellom dem, og arbeidet i mellom skal til
    sammen få plass. Skrus `LEVERINGSFORSOK` eller `LEVERINGSPAUSE_S`
    opp, krymper hvert kall — budsjettet er det samme, og at det er
    trangt blir dermed synlig her i stedet for å bli oppdaget som et
    utløpt vindu i drift.

    Fristen er en SOCKET-frist hos arbeideren (`urllib`s `timeout`), ikke
    et tak på hele overføringen: det den måler er taushet, og en
    plattform som sender jevnt bruker den aldri opp.

    Gulvet på ett sekund finnes for at en absurd liten margin skal gi en
    kort frist og ikke en negativ: et kall som ikke kan tas er ikke en
    innstramming."""
    pauser = LEVERINGSRUNDER * sum(LEVERINGSPAUSE_S * f
                                   for f in range(LEVERINGSFORSOK))
    kall = LEVERINGSFORSOK * LEVERINGSRUNDER
    return max(1.0, (margin_s - AVSLUTNINGSARBEID_S - pauser) / kall)


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


def _tidspunkt(raa: object) -> datetime | None:
    """Et ISO-tidspunkt fra plattformen, eller None om det ikke er lesbart.

    En frist uten tidssone gir None: å gjette sonen er timer feil vei, og
    plattformen sender alltid UTC-offset."""
    if raa is None:
        return None
    try:
        t = datetime.fromisoformat(str(raa))
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


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
    heartbeatet (063) fornyer er leasen, ikke disse grensene.

    En kjøring som løper forbi vinduet stoppes derfor først ved
    LEVERING: kvitteringen svarer 202 `lagret_uten_statusendring` etter
    fristen, og `_kvittert` leser det som `ukvittert` — aldri et falskt
    `utfort`. Utsatt til #173 sammen med lease-avbruddet, som vil ha
    samme signal inn i samme løkke; se KONTRAKT.md,
    `dom-klasse: kjoring-avbrudd-og-frist`."""
    if claim.get("utforelsesfrist") is None:
        return None
    grenser = []
    for raa in (claim.get("utforelsesfrist"),
                claim.get("kvittering_utloper"),
                (claim.get("opplasting") or {}).get("utloper")):
        if raa is None:
            continue
        t = _tidspunkt(raa)
        if t is None:
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
    her).

    TAUSHET ER OGSÅ TAP (Cursor P2, runde 2): en fornyelse som aldri får
    svar — 5xx eller transportfeil — sier ingenting om hvem som eier
    oppdraget, men leasen løper ut like fullt. Når det ikke er noen lease
    igjen å fornye, melder tråden tap på samme måte som ved 4xx. Uten det
    fortsatte evalueringen til ende på en død lease: persondata og
    regnekraft brukt på et oppdrag plattformen alt kunne ha gitt til noen
    andre — nøyaktig kostnaden fristsjekken og kapabilitetssjekken finnes
    for å slippe.

    NÅR leasen er ute er serverens svar, ikke vår aritmetikk (Cursor P2,
    runde 5) — se `_utlopt`."""

    def __init__(self, klient, hode, claim):
        self._klient = klient
        self._hode = hode
        self._kropp = {"oppdrag_id": claim["oppdrag_id"],
                       "owner_claim_id": claim["owner_claim_id"],
                       "owner_generation": claim["owner_generation"],
                       "lease_s": FORNY_LEASE_S}
        self._stopp = threading.Event()
        self._traad = threading.Thread(target=self._lopp, daemon=True)
        #: Siste horisont SERVEREN har oppgitt (`owner_lease_utloper`).
        #: None til første bekreftede fornyelse — claim-svaret bærer den
        #: ikke.
        self._horisont: datetime | None = None
        self.fersk_opplasting = None
        self.tapt: str | None = None

    def __enter__(self):
        self._traad.start()
        return self

    def __exit__(self, *unntak):
        self._stopp.set()
        self._traad.join(timeout=http_frist_s() * 2 + 1)

    def _utlopt(self, stumme: int) -> bool:
        """Er autoriteten borte etter `stumme` påfølgende stumme pulser?

        HORISONTEN ER SERVERENS, IKKE VÅR (Cursor P2, runde 5). Hver
        bekreftede fornyelse svarer med `owner_lease_utloper`, og 063
        skriver den som `least(tak, least(utforelsesfrist,
        greatest(gammel, nå + lease_s)))`. Den er derfor ALDRI bare
        «siste bekreftede fornyelse + `FORNY_LEASE_S`»: på et claim der
        037 alt strakk leasen til `utforelsesfrist`, er fornyelsen en
        no-op og horisonten ligger timer fram — nøyaktig tilfellet #165
        finnes for. En avledet teller kan ikke vite det. Den gamle felte
        leasen ett puls-vindu FØR den var brukt opp (to stumme pulser =
        480 s mot et vindu på 600 s), og kastet en evaluering som
        fortsatt hadde gyldig autoritet: falsk `lease_tapt`, full bunt
        evaluert, rapporten kastet.

        Før den FØRSTE bekreftede fornyelsen finnes ingen horisont fra
        serveren — claim-svaret bærer ikke `owner_lease_utloper` — og da
        gjelder telleren som før. Å regne den ut selv ville vært å speile
        037s formel i klienten: en annen kilde til sannhet om det samme,
        som driver fra hverandre ved neste migrasjon. Det hullet lukkes
        der det bor: claim-svaret må bære feltet fornyelsen alt
        returnerer — egen sak, egen PR, fordi `/v1/oppdrag/claim` er
        plattformens DELTE flate (m56 claimer gjennom den) →
        [#219](https://github.com/moka1980/disponit/issues/219).

        KJENT BEGRENSNING I MELLOMTIDEN, PARKERT AV EIER (K2-kjennelse
        på #218, valg 3). Telleren feller leasen etter
        `FORNY_TAPT_ETTER` × `FORNY_INTERVALL_S` = 480 s, mens 037 skrev
        den initielle leasen som `least(nå + 3600 s, greatest(nå +
        lease_s, utforelsesfrist))` — for `bunt` (frist 240 min) altså
        3600 s. MÅLT KONSEKVENS: det kreves at plattformen er
        SAMMENHENGENDE utilgjengelig gjennom hele de første ~8
        minuttene av en kjøring, FØR den første vellykkede fornyelsen.
        Utfallet er fail-closed — en falsk `lease_tapt`, aldri et falskt
        `utfort`. Se KONTRAKT.md,
        `dom-klasse: lease-horisont-foer-foerste-fornyelse`."""
        if self._horisont is None:
            return stumme >= FORNY_TAPT_ETTER
        return datetime.now(timezone.utc) >= self._horisont

    def _lopp(self):
        stumme = 0                      # påfølgende pulser uten svar
        while not self._stopp.wait(FORNY_INTERVALL_S):
            try:
                r = self._klient.post("/v1/oppdrag/forny",
                                      json=self._kropp,
                                      headers=self._hode)
            except Exception:                       # noqa: BLE001
                r = None                            # transport: intet svar
            if r is None or r.status_code >= 500:
                stumme += 1
                if self._utlopt(stumme):
                    # Ingen lease igjen å fornye; å fortsette er arbeid
                    # uten autoritet.
                    self.tapt = "forny_utilgjengelig"
                    return
                continue                # ennå innenfor: neste puls prøver
            if 200 <= r.status_code < 300:
                stumme = 0              # leasen er bekreftet fornyet
                try:
                    kropp = r.json()
                except ValueError:
                    continue
                #: Horisonten holdes til en NYERE lesning avløser den: et
                #: 2xx uten feltet sier ikke at leasen ble kortere.
                self._horisont = (_tidspunkt(kropp.get("owner_lease_utloper"))
                                  or self._horisont)
                opp = kropp.get("opplasting")
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
        # SINKENE (#173): kandidatlagrene fylles UNDERVEIS gjennom den
        # claim-bundne skriveveien — fullmakten er claimets, som
        # fornyelsen og kvitteringen. Et ikke-2xx-svar er en feil sinket
        # reiser rått; `kjor_bunt` oversetter den til det kodede
        # utfallet (`kandidatlagring_feilet`), aldri en rå exception ut.
        claim_trippel = {"tenant": claim["tenant"],
                         "oppdrag_id": claim["oppdrag_id"],
                         "owner_claim_id": claim["owner_claim_id"],
                         "owner_generation": claim["owner_generation"]}

        def lagre_dokument(kandidat_id, medlemsnavn, data, tekst):
            r = lever("/v1/rekruttering/kandidatdokument", {
                **claim_trippel, "kandidat_id": kandidat_id,
                "dokumentnavn": medlemsnavn,
                "dokument_b64": base64.b64encode(data).decode("ascii"),
                "tekst": tekst}, claim.get("utforelsesfrist"))
            if not 200 <= r.status_code < 300:
                raise RuntimeError(
                    f"kandidatdokument {medlemsnavn}: {r.status_code}")

        def lagre_kandidat(kandidat_id, resultat):
            # AVMASKERINGEN FØLGER MED (Codex P1). `artefakt`-dicten
            # plukket funn/oppfylt/kildetekst og lot `avmaskering` ligge,
            # og den promoterte rapporten stripper kartet med vilje
            # (rapportskjema.py: de to skal aldri reise sammen). Da fantes
            # token→klartekst bare i arbeiderens minne, og forsvant når
            # prosessen døde — igjen sto blindet kildetekst med
            # `[NAVN-1]`-tokener og ingen varig vei tilbake for en
            # autorisert leser. `kandidat_avmaskering` (057) er lageret
            # som finnes for nettopp dette kartet, og den claim-bundne
            # skriveveien er den ENESTE veien dit.
            #
            # Eget toppnivåfelt, ikke inne i `artefakt`: lagrene er hver
            # sin rad med hver sin reaping, og å legge kartet i
            # artefakt-JSON-en ville gitt `kandidat_evalueringsartefakt`
            # en klartekst-kopi som overlever nøyaktig det
            # `kandidat_avmaskering` reapes for.
            r = lever("/v1/rekruttering/kandidatartefakt", {
                **claim_trippel, "kandidat_id": kandidat_id,
                "artefakt": {"funn": resultat["funn"],
                             "oppfylt": resultat["oppfylt"],
                             "kildetekst": resultat["kildetekst"]},
                "avmaskering": resultat["avmaskering"],
                "intervjusporsmal": resultat.get("intervjusporsmal") or
                None}, claim.get("utforelsesfrist"))
            if not 200 <= r.status_code < 300:
                raise RuntimeError(
                    f"kandidatartefakt {kandidat_id}: {r.status_code}")

        with _Heartbeat(klient, hode, claim) as puls:
            try:
                resultat = kjoring.kjor_bunt(
                    sti, modell, vekter=vekter,
                    tekst_for=uttrekker.tekst_for,
                    biasmaalinger=biasmaalinger,
                    antall_soknader=payload["antall_soknader"],
                    lagre_dokument=lagre_dokument,
                    lagre_kandidat=lagre_kandidat)
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
            #
            # Porten står HER, etter `with`-blokka, og ikke inni
            # evalueringsløkka: et tap midtveis stopper leveransen, men
            # ikke arbeidet som alt er i gang. Å polle `tapt` per
            # kandidat krever samme avbruddssignal inn i `kjor_bunt` som
            # det løpende fristtaket — utsatt til #173, se KONTRAKT.md,
            # `dom-klasse: kjoring-avbrudd-og-frist`.
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
