"""Controlleren (PR-014c §2): claimer, styrer motoren, pakker resultatet,
laster opp, kvitterer. Den har credentials (modultokenet fra onboarding);
motoren har ingen.

Én kjøring per kall (`kjor_en`) — timerdrevet i drift, direkte i test.
Alle nettverkskall går gjennom en injisert `klient` (httpx-kompatibel /
Starlette TestClient), og signering av kvitteringen gjennom en injisert
`signer` — kontrakten (PR-006) eies av plattformen, ikke av denne fila.

Feilhåndteringens utfall:
  * Ingen oppdrag (204) → stille retur.
  * UUTFØRBART claim → kvittering `avbrutt` FØR motoren startes. Både
    en bestilling utenfor typens verdikontrakt (`oppdrag_ugyldig`, Codex
    P1), en claim uten opplastingskapabilitet
    (`ingen_opplastingskapabilitet`, Codex P2) og et claim uten et
    lesbart tidsvindu igjen (`frist_utilstrekkelig`, Codex P1) er kjent
    uutførbare av claimet alene. Det samme gjelder en serverkontekst som
    ikke kan gi et gyldig `miljo`-felt (`kontekst_ugyldig`, Codex P2) —
    der er det utføreren som ikke kan levere, men forespørselen ut er
    like unødvendig. `ekstern_lesing` er observerbar trafikk
    mot noen andres nettsted, og den forespørselen er selve skaden når
    rapporten aldri kunne blitt gyldig, aldri kunne blitt levert eller
    aldri kunne blitt avsluttet i tide.
  * Motorfeil / skjemabrudd i egen rapport → kvittering `avbrutt` UTEN
    artefakt: et delvis artefakt finnes ikke (§10 siste rad), men
    plattformen skal få vite at oppdraget er FERDIG mislykket — taushet
    ville latt fristen gjøre jobben og M-37 gjette.
  * Opplastingen AVVIST (Codex P1) → samme `avbrutt`, med feilkode
    `opplasting_avvist`. Rapporten ble bygget, men kom ikke frem, og et
    unntak ut av kjøreløkka her ville etterlatt oppdraget claimet uten et
    ord til plattformen.
  * Full suksess → artefakt + kvittering `utfort` med den serverberegnede
    hashen fra opplastingssvaret (den, og bare den, signeres).
  * Kvittering AVVIST (Codex P1) → `ukvittert`. Kvitteringsendepunktet er
    ikke en varsling som alltid går igjennom: fencing, hashvalidering og
    artefaktpromotering kan avvise den (409), og serveren kan feile (5xx).
    Da er oppdraget IKKE ferdig hos plattformen — artefaktet kan til og
    med være karantenesatt — og et `utfort` herfra ville fått en
    planlegger til å tro at kjøringen var i havn. Utfallet er eget nettopp
    fordi det ikke er det samme som en ærlig motorfeil.
  * SEN EVIDENS (Codex P1) → samme `ukvittert`. Blir kjøringen ferdig
    etter `utforelsesfrist`, men før `evidensfrist`, svarer endepunktet
    202 med `lagret_uten_statusendring`: evidensen er bevart, men
    plattformen har BEVISST latt oppdraget være ufullført. En 2xx er
    derfor ikke i seg selv et statusskifte — se `_kvittert`.
  * GJENTATT kvittering (Codex P2) → utfallet den forrige ga. Kvitteringen
    er idempotent med vilje, så en utfører som mistet svaret skal kunne
    sende NØYAKTIG den samme på nytt. Avsluttet den forrige oppdraget,
    svarer plattformen 200 `idempotent`, og da er dette `utfort`/`avbrutt`
    som første gang. Ble den bare bevart som sen evidens, svarer den
    `idempotent_uten_statusendring`, og da står `ukvittert` — samme
    tilstand, samme ord.
  * ... og controlleren GJENTAR DEN SELV (Codex P2) ved forbigående feil:
    5xx eller et tapt svar sendes på nytt, med nøyaktig de samme signerte
    bytene, så lenge kvitteringskapabiliteten er gyldig. Idempotensen var
    bygget for dette, men bare den andre veien ble brukt. Ingen kaller
    retryer `kjor_en`, den returnerte verdien bærer ikke det som skal til
    for å bygge forespørselen på nytt, og leasen sperrer et ferskt claim
    frem til utførelsesfristen — etter den er raden ikke claimbar. Ett
    tapt svar kostet altså hele oppdraget, inkludert den eksterne
    kontrollen som alt var gjort. 4xx retryes aldri: 409 er plattformens
    overlagte avvisning, ikke en forbigående feil. Se `kvitter`.

Kvitteringen bindes til den KONTROLLERTE VERTEN (Codex P1): `ressurs_id`
er det normaliserte vertsnavnet fra `mal_url`, samme verdi og samme
funksjon som `oppdragskontrakt.malbindingsbrudd` krever av hendelsen — se
`_ressursbinding`.

`avbrutt` over betyr FERDIG mislykket, og forutsetter derfor at
plattformen tok imot feil-kvitteringen. Gjorde den ikke det, er utfallet
`ukvittert` også på feilveiene (Codex P1) — se `_feilutfall`. `grunn` står
uansett: hvorfor kjøringen feilet er like sant om kvitteringen kom frem
eller ikke.
"""
from __future__ import annotations

from datetime import datetime, timezone

import jsonschema

from . import OPPDRAGSTYPE, rapportskjema
from .motor import AVSLUTNINGSMARGIN_S, Motorfeil
from .rapport import bygg


#: Kroppsstatusene som betyr at plattformen FAKTISK skiftet status på
#: oppdraget — de `/v1/oppdrag/kvittering` gir sammen med 200.
#:
#: `idempotent` er med (Codex P2): en RETRY av en kvittering som allerede
#: avsluttet oppdraget er den dokumenterte suksessveien, ikke et avvik.
#: Uten den meldte controlleren `ukvittert` for et oppdrag plattformen for
#: lengst hadde gjort ferdig — og en planlegger som tror på det, følger
#: opp noe som er avsluttet. Retryen er ikke hypotetisk: kvitteringen ER
#: idempotent nettopp for at en utfører som mistet svaret skal kunne
#: sende den samme kvitteringen på nytt.
#:
#: `idempotent_uten_statusendring` er IKKE med, og det er hele grunnen til
#: at plattformen skiller de to: gjentar man en SEN kvittering (202
#: `lagret_uten_statusendring`), er evidensen bevart mens oppdraget står
#: bevisst ufullført. Ett ord for begge ville gjort denne linja til
#: valget mellom to løgner — se `_idempotent_svar` i `api.app`.
_STATUSSKIFTE = ("utfort", "feilet", "idempotent")

#: Antall ganger den SAMME kroppen sendes til plattformen før controlleren
#: gir opp, og pausen mellom forsøkene (Codex P2).
#:
#: Gjelder BEGGE leveringsstegene etter kontrollen — opplastingen av
#: artefaktet og kvitteringen. De er begge idempotente med vilje, nettopp
#: fordi en utfører som mistet svaret ikke vet om plattformen rakk å ta
#: imot, men controlleren utnyttet det aldri selv: den sendte én gang, og
#: et forbigående 5xx eller et tapt svar ga `ukvittert`, `feilet` eller et
#: unntak ut av kjøreløkka. Ingen kaller retryer `kjor_en`, den returnerte
#: verdien bærer ikke det som skal til for å bygge forespørselen på nytt,
#: og leasen sperrer et ferskt claim frem til utførelsesfristen — etter
#: den er raden ikke claimbar. Ett tapt svar kostet altså hele oppdraget,
#: inkludert den eksterne kontrollen som alt var gjort.
#:
#: Retryen er billig og trygg nettopp fordi kroppen er IDENTISK: samme
#: `kapabilitet_jti` og samme kanoniske rapport på opplastingen, samme
#: `kvittering_jti` og samme signerte bytes på kvitteringen. Plattformen
#: kjenner dem igjen framfor å lage et nytt artefakt eller en ny
#: statusendring.
LEVERINGSFORSOK = 4
LEVERINGSPAUSE_S = 2.0

#: `lever` kjøres TO ganger i en avslutning: opplastingen og kvitteringen.
LEVERINGSRUNDER = 2
#: Arbeidet MELLOM kallene — kanonisering av rapporten, skjemavalidering
#: og signering av kvitteringen. Grovt, og med vilje romslig: det som
#: trekkes fra her blir ikke brukt på en HTTP-frist.
AVSLUTNINGSARBEID_S = 20.0


def http_frist_s(margin_s: float = AVSLUTNINGSMARGIN_S) -> float:
    """Den lengste ETT HTTP-kall kan få og likevel holde hele avslutningens
    VERSTEFALL innenfor lukkevinduet (Codex P2, runde 5).

    `_skannefrist` reserverer `AVSLUTNINGSMARGIN_S` til alt som skjer etter
    skanningen, og motoren får resten. Men retryen over har ingen
    tidsbudsjett-forbindelse til den marginen: en plattform som tar imot
    forbindelsen og så tier, bruker HELE per-kall-fristen, og `lever`
    prøver fire ganger — for opplastingen, og igjen for kvitteringen. Med
    en 120-sekunders klientfrist er verstefallet da over 480 sekunder på
    opplastingen ALENE, mot de 300 marginen ga hele avslutningen. En
    skanning som ble ferdig nær fristen sin kunne dermed la
    kvitteringskapabiliteten løpe ut mens den fortsatt ventet, og
    oppdraget sto ufullført.

    Fristen AVLEDES derfor av marginen i stedet for å stå ved siden av
    den: åtte kall, pausene mellom dem, og arbeidet i mellom skal til
    sammen få plass. Skrus `LEVERINGSFORSOK` opp, krymper hvert kall —
    budsjettet er det samme.

    Gulvet på ett sekund finnes for at en absurd liten margin skal gi en
    kort frist og ikke en negativ: et kall som ikke kan tas er ikke en
    innstramming."""
    pauser = LEVERINGSRUNDER * sum(LEVERINGSPAUSE_S * f
                                   for f in range(LEVERINGSFORSOK))
    kall = LEVERINGSFORSOK * LEVERINGSRUNDER
    return max(1.0, (margin_s - AVSLUTNINGSARBEID_S - pauser) / kall)


def _sov(sekunder: float) -> None:
    """Pausen mellom kvitteringsforsøkene.

    Egen funksjon slik at testene kan bytte den ut: de skal bevise
    RETRY-adferden, ikke vente i sanntid på den."""
    import time
    time.sleep(sekunder)


class _Uteblitt:
    """Stedfortrederen for et svar som aldri kom (Codex P2).

    Alle kallstedene til `kvitter` leser `rk.status_code` og `_kvittert(rk)`
    videre. Et transportunntak som slapp ut derfra tok med seg hele
    `kjor_en` — også på feilveiene, der oppdraget da stod claimet uten et
    ord til plattformen, nøyaktig den taushetslinjen §10 forbyr.

    `status_code = 0` er ingen HTTP-status og kan ikke forveksles med en:
    `_kvittert` leser den som «ikke 2xx», altså uferdig, og
    `kvittering_status: 0` i utfallet sier ærlig at det aldri kom noe
    svar å lese."""

    status_code = 0

    def __init__(self, grunn: str = "intet svar"):
        self.grunn = grunn

    def json(self):
        raise ValueError(self.grunn)


def _vindu_apent(raa: object) -> bool:
    """Er kapabiliteten som bærer denne leveringen fortsatt gyldig?

    Gjelder KUN kapabiliteter uten gjenspillingsvei — i praksis
    kvitteringen, der `innlos_kvitteringskapabilitet` krever `utloper >
    now()` uten unntak og retryen derfor ikke har noen verdi etter
    utløpet. Opplastingen er den motsatte: en FORBRUKT kapabilitet er
    innløsbar uansett utløp, og den leveringen sender
    `gjenlosbar_etter_utlop=True` i stedet for å spørre her.

    Mangler feltet, eller lar det seg ikke lese, retryer vi likevel.
    Fail-closed-posituren ellers i fila verner om ÉN ting: en unødvendig
    forespørsel mot kundens nettsted. Denne går til vår egen plattform,
    og `LEVERINGSFORSOK` er allerede taket. Å slå av retryen på et felt
    vi ikke kan lese ville ofret et fullført oppdrag for å spare
    forespørsler ingen andre merker."""
    if raa is None:
        return True
    try:
        t = datetime.fromisoformat(str(raa))
    except ValueError:
        return True
    if t.tzinfo is None:
        return True
    return t > datetime.now(timezone.utc)


def _kvitteringsvindu_apent(claim: dict) -> bool:
    """`_vindu_apent` for kvitteringskapabiliteten."""
    return _vindu_apent(claim.get("kvittering_utloper"))


def _ressursbinding(payload: dict) -> str | None:
    """Kvitteringens `ressurs_id`: den kontrollerte VERTEN (Codex P1).

    `payload.get("ressurs_id", "")` ga tom streng for HVER ENESTE
    WCAG-kvittering. Det er ikke et uhell i payloaden — `oppdragskontrakt`
    minimerer typen til `mal_url`, `kravsett`, `omfang` og `maks_sider`
    med vilje, så feltet finnes ikke å hente. Controlleren signerte altså
    hver kvittering, både suksess og feil, med en TOM ressursbinding, mens
    `resultathash` på plattformsiden regner `ressurs_id` som en del av
    resultatet.

    Den autoriserte ressursen er det normaliserte vertsnavnet fra
    `mal_url` — det er nøyaktig den likheten `malbindingsbrudd` krever av
    hendelsen (`event["ressurs_id"] == normaliser_vertsnavn(mal_url)`).
    Kvitteringen bindes derfor til SAMME verdi, med SAMME funksjon: en
    egen normalisering her ville vært en annen streng ved første
    rotprikk eller store bokstaver, og da hadde bindingen vært pynt.

    Verten er heller ikke ny eksponering: den er en del av `mal_url`, som
    alt står i det tenantskopede oppdraget kvitteringen hører til.

    Selve avledningen er plattformens (`oppdragskontrakt.malvert`), ikke
    modulens: den slår opp måldomenet på TYPEN og normaliserer med samme
    funksjon som `malbindingsbrudd`. `rapport.bygg` binder sidene i
    rapporten med nøyaktig det samme kallet, så kvittering og evidens kan
    ikke navngi hver sin vert.

    `oppdragskontrakt` er allerede en kjøretidsavhengighet for modulen
    (`rapport` kanoniserer med `policy_validator`), men importen står ved
    kallstedet av samme grunn som der: modulens IMPORTTID skal ikke være
    bundet til plattformkjernen.

    -> None når målet ikke lar seg lese. Da har modulen ingenting å binde
    kvitteringen til, og fail-closed er posituren: se `kjor_en`.
    """
    from oppdragskontrakt import malvert
    return malvert(OPPDRAGSTYPE, payload)


def _kontraktsbrudd(payload: dict) -> list[str]:
    """Feltene i claimet som gjør oppdraget uutførbart — FØR motoren
    startes (Codex P1).

    Bare `mal_url` ble lest før den eksterne skanningen; `omfang`,
    `maks_sider` og `kravsett` ble først sett av `rapport.bygg`, altså
    ETTER at motoren hadde vært ute på kundens nettsted. Et claim med
    `omfang: "alt"`, `maks_sider: 0` eller et ukjent `kravsett` kunne
    derfor aldri gi en gyldig rapport — men det ga observerbar,
    ekstern trafikk mot et nettsted som ikke er vårt, hver eneste gang.
    `ekstern_lesing` er nettopp klassen der en unødvendig forespørsel er
    selve skaden; da skal den ikke sendes for en bestilling vi allerede
    kan se at ingen rapport kan oppfylle.

    Kontrakten er PLATTFORMENS, ikke modulens (`oppdragskontrakt`): den
    samme tabellen stopper oppdraget ved OPPRETTELSEN. To sett regler
    ville betydd at bestillingsveien og utføreren kunne være uenige om
    hva som er et lovlig oppdrag, og da hadde denne porten vært en annen
    port enn den som slapp oppdraget gjennom. Den står likevel her og
    ikke bare der: raden kan være skrevet av en eldre release, og en
    utfører som stoler på at noen andre alt har sjekket, sjekker ikke.

    `mal_url` er med for ÉN ting: at rapportformen av den får plass i
    `sider_kontrollert[].url` (Codex P2). Et fullt lovlig https-mål —
    riktig vert, riktig omfang — kunne ha en sti som gjorde den ferdige
    URL-en lengre enn skjemaets `maxLength: 2000`. Både denne porten og
    `_ressursbinding` slapp den gjennom, målet ble kontrollert eksternt,
    og bestillingen ble avvist først da `rapportskjema.SKJEMA` validerte
    siden som kom tilbake — nøyaktig den unødvendige forespørselen
    `ekstern_lesing` handler om. Grensa står i den delte kontrakten
    (`FELTURLLENGDER`) og måles på rapportformen (`rapporturl`), ikke på
    råstrengen: prosentkodingen ekspanderer, og det er rapportformen
    skjemaet måler.

    Ellers leses `mal_url` av `_ressursbinding` rett over, med den samme
    funksjonen målbindingen bruker, og et ULESELIG mål har sin egen
    feilkode — det er derfor ikke et brudd her.
    """
    from oppdragskontrakt import bryter_feltkontrakten, mangler_paakrevde
    return sorted({*mangler_paakrevde(OPPDRAGSTYPE, payload),
                   *bryter_feltkontrakten(OPPDRAGSTYPE, payload)})


def _kontekstbrudd(kontekst) -> str | None:
    """Grunnen til at serverkonteksten ikke kan gi en gyldig rapport —
    eller None (Codex P2).

    `bygg` leser `kontekst[k]` for de seks `miljo`-feltene, og de seks er
    controllerens EGEN konfigurasjon (digest, versjoner, viewport), ikke
    noe motoren eller oppdraget bestemmer. Uten denne porten var det to
    utfall, begge gale, og begge ETTER at motoren hadde vært ute:

      * mangler en nøkkel (typisk `container_image_digest` i en
        feilkonfigurert deployment) → naken `KeyError` fra `bygg`.
        `kjor_en` fanger kun Motorfeil og ValidationError, så unntaket
        gikk ut av kjøreløkka og lot det claimede oppdraget stå
        ufullført til fristen — taushetslinjen §10 forbyr.
      * ugyldig verdi (en digest uten `sha256:`-form) → oppdaget først av
        skjemavalideringen, altså etter en full, observerbar kontroll av
        kundens nettsted. `ekstern_lesing` er klassen der den unødvendige
        forespørselen ER skaden, og denne er unødvendig på nøyaktig
        samme måte som en ugyldig bestilling: vi kunne visst det før.

    Kravet leses fra SKJEMAET (`miljo`-delskjemaet), ikke fra en
    håndskrevet liste her: da kan ikke porten og rapportformen gli fra
    hverandre når skjemaet får et felt til.

    Teksten som returneres, beskriver SKJEMAET (felt + nøkkelord), aldri
    verdien — samme disiplin som `api.artefaktskjema._bruddkode`. Her er
    verdiene riktignok driftskonfigurasjon og ikke kundedata, men grunnen
    havner i driftsloggen, og en digest er ikke noe loggen trenger.
    """
    miljo = rapportskjema.SKJEMA["properties"]["miljo"]
    if not isinstance(kontekst, dict):
        return "kontekst er ikke et objekt"
    mangler = [k for k in miljo["required"] if k not in kontekst]
    if mangler:
        return "mangler " + ",".join(sorted(mangler))
    v = jsonschema.Draft202012Validator(miljo)
    feil = sorted(v.iter_errors({k: kontekst[k] for k in miljo["required"]}),
                  key=lambda e: list(e.absolute_path))
    if feil:
        e = feil[0]
        felt = ".".join(str(p) for p in e.absolute_path) or "<miljo>"
        return f"{felt}:{e.validator}"
    return None


def _skannefrist(claim: dict) -> int | None:
    """Sekundene motoren FAKTISK har på seg — eller None når claimet ikke
    bærer et lesbart vindu (Codex P1).

    Motorens `tidsavbrudd_s` er et TAK for den lengste kontrollen typen
    kan bestille. Det er ikke det samme som fristen DETTE oppdraget har:
    en `enkeltside`-kontroll med 30 minutters annonsert frist fikk 55
    minutter å bruke, og motoren ble drept lenge etter at oppdraget hadde
    oversittet fristen sin. Kjøringen kunne da hverken kvitteres som
    utført eller stoppes i tide — den bare fortsatte å lese kundens
    nettsted forbi vinduet noen hadde autorisert.

    Vinduet er den TIDLIGSTE av grensene claimet selv navngir:

      * `utforelsesfrist` — etter den kan ikke kvitteringen lenger
        avslutte oppdraget (endepunktet svarer 202
        `lagret_uten_statusendring`),
      * `opplasting.utloper` — etter den kan rapporten ikke lastes opp,
      * `kvittering_utloper` — etter den kan kvitteringen ikke sendes.

    Alle tre er absolutte og uten fornyelsesvei, så den første som
    inntreffer er den som gjelder. `AVSLUTNINGSMARGIN_S` trekkes fra:
    kanonisering, opplasting og signert kvittering er det som gjør et
    fullført arbeid til et AVSLUTTET oppdrag, og en motor som får bruke
    helt frem til grensen leverer ingenting.

    -> None når `utforelsesfrist` mangler eller ikke lar seg lese. Da har
    modulen intet vindu å kjøre innenfor, og `kjor_en` avviser claimet
    før motoren startes i stedet for å falle tilbake på taket: en frist
    vi ikke kan lese er ikke en frist som er romslig.
    """
    if claim.get("utforelsesfrist") is None:
        return None
    grenser = []
    for raa in (claim.get("utforelsesfrist"), claim.get("kvittering_utloper"),
                (claim.get("opplasting") or {}).get("utloper")):
        if raa is None:
            continue
        try:
            t = datetime.fromisoformat(str(raa))
        except ValueError:
            return None
        # En frist uten tidssone kan ikke sammenlignes med «nå» uten å
        # gjette hvilken sone den var ment i, og et gjett her er timer
        # feil vei. Plattformen sender alltid UTC-offset.
        if t.tzinfo is None:
            return None
        grenser.append(t)
    igjen = (min(grenser) - datetime.now(timezone.utc)).total_seconds()
    return int(igjen) - AVSLUTNINGSMARGIN_S


def _kvittert(rk) -> bool:
    """Skiftet plattformen status på oppdraget?

    Kun 2xx teller — 409 (fencing, hashavvik, avvist promotering) og 5xx
    betyr at oppdraget står igjen uferdig hos plattformen.

    Men 2xx er IKKE nok (Codex P1). Fullfører kjøringen etter
    `utforelsesfrist`, men før `evidensfrist`, svarer endepunktet 202 med
    `status: "lagret_uten_statusendring"`: evidensen er bevart, og det er
    HELE det som skjedde — `unntak.status` står urørt, oppdraget er
    bevisst latt ufullført. Å lese den 202-en som en kvittering ga
    `utfall: "utfort"` for et oppdrag plattformen selv regner som uferdig,
    og en planlegger som tror på modulens ord slutter da å følge opp noe
    som aldri ble avsluttet.

    Derfor kreves BÅDE 2xx og en kroppsstatus som navngir skiftet. En
    kropp vi ikke kan lese (ikke JSON, ikke et objekt) er heller ingen
    bekreftelse: da vet vi ikke hva som skjedde, og «vet ikke» skal
    behandles som uferdig, ikke som ferdig."""
    if not 200 <= rk.status_code < 300:
        return False
    try:
        kropp = rk.json()
    except ValueError:
        return False
    return isinstance(kropp, dict) and kropp.get("status") in _STATUSSKIFTE


def _feilutfall(rk, grunn: str, **ekstra) -> dict:
    """Utfallet for en kjøring som feilet — avledet av `_kvittert`, ikke
    antatt (Codex P1).

    Feilgrenene meldte `avbrutt` uansett hva plattformen svarte på
    feil-kvitteringen. `avbrutt` betyr «oppdraget er FERDIG mislykket», og
    det er nettopp det plattformen ikke har bekreftet når kvitteringen ble
    avvist med 409/5xx eller lagret som sen evidens med 202: da står
    oppdraget fortsatt claimet og uferdig der, akkurat som når en
    SUKSESS-kvittering blir avvist. Suksessgrenen har lest `_kvittert`
    siden forrige runde; feilgrenene gjorde det ikke, og forskjellen var
    vilkårlig — en planlegger som tror på `avbrutt` slutter å følge et
    oppdrag som aldri ble avsluttet.

    `grunn` og `kvittering_status` blir stående uansett utfall: hvorfor
    kjøringen feilet er like sant om kvitteringen kom frem eller ikke."""
    kvittert = _kvittert(rk)
    return {"utfall": "avbrutt" if kvittert else "ukvittert",
            "grunn": grunn, "kvittering_status": rk.status_code,
            "kvittert": kvittert, **ekstra}


def kjor_en(klient, token: str, motor, kontekst: dict, signer) -> dict:
    """-> {"utfall": "tomt"|"utfort"|"avbrutt"|"ukvittert", ...}."""
    hode = {"authorization": f"Bearer {token}"}
    r = klient.post("/v1/oppdrag/claim", json={}, headers=hode)
    if r.status_code == 204:
        return {"utfall": "tomt"}
    r.raise_for_status()
    claim = r.json()
    payload = claim["payload"]

    vert = _ressursbinding(payload)
    kvittering_basis = {
        "oppdrag_id": claim["oppdrag_id"], "tenant": claim["tenant"],
        "kvittering_jti": claim["kvittering_jti"],
        "repair_operation_id": claim["repair_operation_id"],
        "owner_claim_id": claim["owner_claim_id"],
        "owner_generation": claim["owner_generation"],
        "ressurs_id": vert or "",
    }

    def lever(sti, kropp, utloper, *, gjenlosbar_etter_utlop=False):
        """Send den SAMME kroppen til plattformen, og send den på nytt ved
        forbigående feil (Codex P2).

        ÉN løkke for begge leveringsstegene etter kontrollen. Retryen
        fantes bare for kvitteringen, og da var opplastingen ETT steg
        tidligere i nøyaktig samme situasjon: et tapt svar der rev med seg
        `kjor_en`, og et forbigående 5xx ble lest som en avvist rapport.
        Begge kastet en ferdig, dyr kontroll av kundens nettsted på en
        feil plattformen selv sier man skal spørre om igjen.

        Kroppen bygges av kalleren og er IDENTISK for hvert forsøk — det
        er hele grunnen til at retryen er trygg. Kvitteringen signeres
        derfor før løkka, ikke i den: ny signering kunne gitt en ny `jti`
        eller et nytt tidsstempel, og da hadde plattformen sett to
        forskjellige kvitteringer i stedet for én gjentatt. Opplastingen
        er idempotent på samme måte, på `kapabilitet_jti` og den kanoniske
        rapporten.

        Hva som retryes, og hva som ikke gjør det:

          * 5xx og et TAPT SVAR (transportunntak) er forbigående. Det er
            nettopp her idempotensen finnes: utføreren vet ikke om
            plattformen rakk å ta imot, og skal kunne spørre igjen.
          * 4xx retryes ALDRI — heller ikke 409. Fencing, hashavvik og
            avvist promotering er plattformens overlagte avvisninger, og
            å gjenta dem er å mase om et svar som ikke kommer til å endre
            seg.
          * 2xx er ferdig, uansett hva kroppen meldte. Det er et svar vi
            FIKK.

        `gjenlosbar_etter_utlop` sier om DENNE kapabiliteten kan innløses
        etter utløpet når den ALT er forbrukt (Codex P2). De to
        leveringsstegene er ikke like her, og forskjellen står i SQL-en:

          * `innlos_artefaktkapabilitet` (035) tar `k.status = 'brukt' OR
            k.utloper > now()`, og `lagre_artefakt_staged` (017) returnerer
            det opprinnelige `artefakt_id` for samme hash. Det er en
            UTTALT gjenspillingsvei, skrevet nettopp for at en utfører som
            mistet svaret skal få det igjen.
          * `innlos_kvitteringskapabilitet` (035) krever `k.utloper >
            now()` uten unntak. Der er utløpet endelig, og et forsøk etter
            det er bare støy.

        Uten flagget stanset `_vindu_apent` retryen for BEGGE. For
        opplastingen betød det at et tapt svar rett før nominelt utløp —
        altså nøyaktig kappløpet gjenspillingsveien finnes for — ble til
        `opplasting_avvist` og en feilkvittering, mens artefaktet i
        virkeligheten lå staget på plattformen. Kontrollen av kundens
        nettsted var gjort, resultatet var lagret, og modulen meldte at
        oppdraget mislyktes.

        Å slippe retryen forbi utløpet er billig når den ikke hjelper:
        er kapabiliteten utløpt UTEN å være forbrukt, finner innløsningen
        ingen rad og endepunktet svarer `kapabilitet_ugyldig` (401). Det
        er en 4xx, så løkka bryter på FØRSTE forsøk. Prisen for å ta feil
        er én forespørsel mot vår egen plattform; prisen for å la være er
        en tapt crawl av kundens nettsted.

        Gir alle forsøkene tapt svar, returneres `_Uteblitt` i stedet for
        å la unntaket rive med seg `kjor_en`: da blir utfallet `ukvittert`
        eller en ærlig feilkvittering med status `0`, og plattformen har i
        det minste et ord fra modulen om at kjøringen ikke ble avsluttet.
        """
        rk = _Uteblitt()
        for forsok in range(LEVERINGSFORSOK):
            if forsok:
                if not gjenlosbar_etter_utlop and not _vindu_apent(utloper):
                    break
                _sov(LEVERINGSPAUSE_S * forsok)
            try:
                rk = klient.post(sti, json=kropp, headers=hode)
            except Exception as e:                      # noqa: BLE001
                # Transportfeilene kommer fra en INJISERT klient, så
                # klassene deres er ikke våre å navngi. Det som er vårt,
                # er at ingen av dem skal kunne rive med seg kjøreløkka.
                rk = _Uteblitt(f"{type(e).__name__}: intet svar")
                continue
            if rk.status_code < 500:
                break
        return rk

    def kvitter(kropp):
        """Kvitteringen gjennom `lever` — signert ÉN gang, utenfor løkka."""
        return lever("/v1/oppdrag/kvittering", signer(kropp),
                     claim.get("kvittering_utloper"))

    if vert is None:
        # Lar målet seg ikke lese, har modulen ingenting å binde
        # kvitteringen til — og et claim som kom hit BURDE ha et lesbart
        # `mal_url`: `malbindingsbrudd` avviste alt annet da oppdraget ble
        # opprettet. Å kontrollere «noe» og signere en tom binding er den
        # ene tilstanden denne fiksen finnes for å hindre, så motoren
        # startes ikke i det hele tatt. Plattformen får en ærlig feilkode
        # i stedet for taushet.
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "malbinding_mangler"})
        return _feilutfall(rk, "malbinding_mangler")

    brudd = _kontraktsbrudd(payload)
    if brudd:
        # HELE bestillingen leses før motoren startes, ikke bare målet
        # (Codex P1): et claim som ikke kan gi en gyldig rapport skal
        # ikke koste kundens nettsted en eneste forespørsel. Feilen er
        # oppdragets, ikke motorens, og feilkoden sier det.
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "oppdrag_ugyldig"})
        return _feilutfall(rk, f"oppdrag_ugyldig:{brudd}")

    opplasting = claim.get("opplasting")
    if not opplasting:
        # Claim uten opplastingskapabilitet for denne typen er en
        # konfigurasjonstilstand (port 15-adferden på plattformsiden):
        # artefakttypen mangler, er tvetydig, eller er filtrert bort for
        # denne deploymenten. Rapporten kan da ikke leveres uansett hva
        # kontrollen finner.
        #
        # Sjekken står FØR motoren (Codex P2). Sto den etter, gjorde
        # controlleren hele den eksterne kontrollen — full crawl av
        # kundens nettsted — for så å kaste rapporten på en betingelse
        # den kunne lest av claimet før første forespørsel. Det er samme
        # regnestykke som `_kontraktsbrudd` over: leveringsveien er en
        # del av bestillingens gjennomførbarhet, og en umulig levering
        # skal ikke koste noen andre trafikk.
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "ingen_opplastingskapabilitet"})
        return _feilutfall(rk, "ingen_kapabilitet")

    kbrudd = _kontekstbrudd(kontekst)
    if kbrudd:
        # SERVERKONTEKSTEN ER OGSÅ EN FORUTSETNING (Codex P2). De tre
        # portene over leser bestillingen; denne leser vår egen
        # konfigurasjon. Utfallet er det samme: kan ingen gyldig rapport
        # bli til, skal kundens nettsted ikke kontaktes — og den
        # manglende nøkkelen skal bli en ærlig feilkvittering her, ikke
        # en `KeyError` ut av kjøreløkka fra `bygg` senere.
        #
        # Feilkoden er modulens egen, ikke oppdragets: bestillingen er
        # feilfri, det er DENNE utføreren som ikke kan levere.
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "kontekst_ugyldig"})
        return _feilutfall(rk, f"kontekst_ugyldig:{kbrudd}")

    frist_s = _skannefrist(claim)
    if frist_s is None or frist_s <= 0:
        # FRISTEN ER EN DEL AV BESTILLINGEN (Codex P1). Er vinduet
        # uleselig eller alt oppbrukt, kan denne kjøringen aldri bli et
        # avsluttet oppdrag — og da skal den ikke koste kundens nettsted
        # en eneste forespørsel, av samme grunn som `_kontraktsbrudd` og
        # kapabilitetssjekken over. Motoren får resten som `frist_s`, så
        # den blir stoppet av OPPDRAGETS frist og ikke av sitt eget tak.
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "frist_utilstrekkelig"})
        return _feilutfall(rk, "frist_utilstrekkelig", frist_s=frist_s)

    try:
        resultat = motor.kjor(payload, frist_s=frist_s)
        rapport = bygg(resultat, payload=payload, kontekst=kontekst)
        # Egen validering FØR innsending: serveren validerer uansett (mot
        # samme innholdsadresserte skjema), men modulen skal aldri sende
        # noe den selv kan se er ugyldig — og en avvist opplasting her er
        # en MOTORFEIL sett fra oppdraget.
        jsonschema.Draft202012Validator(rapportskjema.SKJEMA).validate(
            rapport)
    except (Motorfeil, jsonschema.ValidationError) as e:
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "motor_avbrutt"})
        return _feilutfall(rk, type(e).__name__)

    # OPPLASTINGEN GJENTAS SOM KVITTERINGEN (Codex P2). Den gikk én gang:
    # et transportunntak her slapp rett ut av `kjor_en` — ingen kvittering,
    # oppdraget claimet og tyst til fristen — og et forbigående 5xx ble
    # lest som «rapporten kom ikke frem» og kvittert `feilet`. Begge kastet
    # en ferdig crawl av kundens nettsted på en feil endepunktet er
    # idempotent nettopp for at man SKAL kunne spørre om igjen: samme
    # `kapabilitet_jti` og samme kanoniske rapport gir samme artefakt.
    #
    # Og retryen stanser IKKE ved nominelt utløp (Codex P2): er
    # kapabiliteten alt forbrukt, er den innløsbar uansett utløp, og
    # `lagre_artefakt_staged` gir det opprinnelige artefaktet tilbake for
    # samme hash. Se `lever`.
    ro = lever("/v1/artefakt",
               {"kapabilitet_jti": opplasting["jti"], "rapport": rapport},
               opplasting.get("utloper"), gjenlosbar_etter_utlop=True)
    if not 200 <= ro.status_code < 300:
        # AVVIST OPPLASTING (Codex P1): `ro.raise_for_status()` kastet ut
        # av kjøreløkka uten kvittering, og oppdraget ble stående claimet
        # til fristen — akkurat den taushetslinjen §10 forbyr, og det
        # motsatte av det denne fila selv sier («en avvist opplasting her
        # er en MOTORFEIL sett fra oppdraget»). Rapporten er bygget, men
        # den kom ikke frem: da er oppdraget ærlig mislykket. Etter retryen
        # over dekker denne grenen de OVERLAGTE avvisningene (4xx) og de
        # forbigående som ikke ga seg — inkludert `_Uteblitt` med status 0.
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "opplasting_avvist"})
        return _feilutfall(rk, "opplasting_avvist",
                           opplasting_status=ro.status_code)
    try:
        artefakt = ro.json()
        artefakt_id = artefakt["artefakt_id"]
        klartekst_sha256 = artefakt["klartekst_sha256"]
    except (ValueError, TypeError, KeyError) as e:
        # 2xx med en kropp vi ikke kan lese er ingen kvitteringsgrunn: uten
        # `artefakt_id` og hashen finnes det ikke en `utfort`-kvittering å
        # signere. Den nakne feilen ville ellers gått samme vei som
        # transportunntaket over — ut av kjøreløkka, uten et ord.
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "opplasting_avvist"})
        return _feilutfall(rk, f"opplasting_uleselig:{type(e).__name__}",
                           opplasting_status=ro.status_code)

    rk = kvitter({**kvittering_basis, "resultat": "utfort",
                  "artefakt_id": artefakt_id,
                  "klartekst_sha256": klartekst_sha256})
    svar = {"artefakt_id": artefakt_id,
            "kvittering_status": rk.status_code,
            "sider": len(rapport["sider_kontrollert"])}
    if not _kvittert(rk):
        # Rapporten er bygget og lastet opp, men plattformen tok IKKE imot
        # kvitteringen — oppdraget er uferdig der, og artefaktet kan være
        # karantenesatt. Å melde `utfort` her ville vært modulens ord mot
        # plattformens tilstand, og planleggeren ville trodd på modulen.
        return {"utfall": "ukvittert", **svar}
    return {"utfall": "utfort", **svar}
