"""Rapportbygging + sanitering (PR-014c §7): motorens rå utdata inn,
skjemagyldig rapport ut — eller Motorfeil, aldri et delvis artefakt.

Saneringen er ÆRLIG kutting: URL-er strippes for query/fragment,
selektorer kappes til 200 tegn, maks 10 eksempler per regel, maks 500
funn og maks 200 dekningsbegrensninger — og kappes EN AV LISTENE, sier
`avkortet` det (den lyver aldri om at alt kom med). Miljøblokka er
SERVERKONTEKSTENS, aldri motorens.

Og sidene er MÅLETS: `sider_kontrollert` bindes til den verten oppdraget
er autorisert for (Codex P1), med samme avledning som kvitteringens
`ressurs_id`. En rapport om et annet nettsted er ikke en avkortet rapport,
det er evidens om noe ingen har bedt om.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .motor import Motorfeil, Motorresultat, heltall

#: SAMME mønster som `rapportskjema._HOSTNAME`. Det står ikke importert,
#: men gjentatt med en test som binder de to sammen: skjemaet er
#: innholdsadressert og hashet, og en import ville gjort saneringen
#: avhengig av at ingen noen gang endrer mønsteret der uten å tenke på
#: hva det gjør med kappingen her.
_VERT = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
    r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+\Z")

_ALVOR = ("kritisk", "alvorlig", "moderat", "lav")
#: Oppdragets `omfang` — manifestets lukkede enum (frister 30 min for
#: `enkeltside`, 60 min for `nettsted`). Den står her og ikke i en gjetning
#: per kallsted: en verdi utenfor settet er en feil, ikke stillhet, på
#: samme måte som `kravsett` er en lukket enum ved OPPRETTELSEN.
_OMFANG = ("enkeltside", "nettsted")
MAKS_FUNN = 500
MAKS_EKSEMPLER = 10
MAKS_SELEKTOR = 200
MAKS_BEGRENSNINGER = 200
#: Artefaktets harde grense (014b §7, DB-CHECK + `/v1/artefakt`): 1 MiB
#: JCS-KANONISERTE byte. Antallsgrensene over holder den IKKE av seg selv —
#: 500 funn à ti 200-tegns eksempler er alene over en megabyte, og skjemaet
#: godtar den rapporten. Måles derfor i byte, med SAMME kanonisering som
#: serveren bruker, før opplasting.
MAKS_BYTES = 1 << 20
#: Eksempeltak vi faller ned gjennom når rapporten er for stor. Eksemplene
#: er ILLUSTRASJON (selektorer); regel_id, alvorlighet og antall er selve
#: funnet. Derfor ofres eksemplene først, funnlisten sist.
_NEDTRAPPING = (5, 2, 0)


def _kanoniske_bytes(rapport: dict) -> bytes:
    """Rapporten slik SERVEREN vil måle den.

    `/v1/artefakt` avviser på `len(jcs.kanoniske_bytes(rapport)) > 1 MiB`,
    så modulen må måle med nøyaktig den funksjonen. En egen tilnærming her
    ville vært et annet tall enn det som faktisk avgjør — og differansen
    ville vist seg som en avvist opplasting, ikke som en for stor rapport.

    `Ikkekanoniserbar` oversettes til Motorfeil (Codex P1). Den er en
    `TypeError`, ikke noe `controller.kjor_en` fanger, og den kan nås av
    tall som hver for seg passerte inngangsporten: `sammendrag` SUMMERER
    500 funn, og summen kan gå over JCS sitt trygge område selv om hvert
    ledd lå under. Dette er siste skanse — porten i `motor.heltall` tar
    enkeltverdiene, denne tar alt som kan oppstå etterpå.

    `UnicodeEncodeError` hører til SAMME skanse (Codex P1): en escaped
    ensom surrogate fra motoren — `{"regel_id": "\\ud800"}` — er lovlig
    JSON-tekst, så `json.loads` gir den fra seg som en helt vanlig `str`,
    og `_tekst` ser en ikke-tom streng. Først `kanoniser(...).encode(
    "utf-8")` oppdager at kodepunktet ikke KAN uttrykkes i UTF-8, og den
    kaster UnicodeEncodeError (en ValueError), ikke Ikkekanoniserbar.
    Den fanges hverken her eller av `controller.kjor_en`, så unntaket
    forlot kjøringen uten feil-kvittering og lot det claimede oppdraget
    stå ufullført til fristen — nøyaktig taushetens utfall §10 forbyr.
    `/v1/artefakt` oversetter allerede den samme feilen til
    `request_feilformet`; modulen skal ikke sende det serveren uansett
    avviser.

    Fangsten står HER og ikke i `_tekst` med vilje: den dekker hver
    eneste streng i rapporten — `regel_id`, eksempler, `vert`, URL-er,
    `regelsett_versjon` — fordi `bygg` alltid ender i `_under_taket`, og
    ikke bare de feltene som tilfeldigvis går gjennom én port.
    """
    from policy_validator import jcs
    try:
        return jcs.kanoniske_bytes(rapport)
    except (jcs.Ikkekanoniserbar, UnicodeEncodeError) as e:
        raise Motorfeil(f"rapporten kan ikke kanoniseres: {e}") from e


def _autorisert_vert(payload: dict) -> str:
    """Verten oppdraget faktisk er autorisert for — eller Motorfeil.

    Avledningen er plattformens (`oppdragskontrakt.malvert`), den samme
    som gir kvitteringens `ressurs_id` i `controller._ressursbinding`. En
    egen normalisering her ville vært en annen streng ved første rotprikk
    eller store bokstaver, og da hadde bindingen under vært pynt.

    -> Motorfeil når målet ikke lar seg lese. Controlleren har alt avvist
    det claimet før motoren startes, så tilstanden er ikke nåbar i drift;
    den står likevel fordi et `bygg` uten autorisert vert ikke har noe å
    binde sidene TIL, og fail-open er den ene tilstanden denne fiksen
    finnes for å hindre.
    """
    from oppdragskontrakt import malvert
    from . import OPPDRAGSTYPE
    vert = malvert(OPPDRAGSTYPE, payload)
    if vert is None:
        raise Motorfeil("oppdragets mål lar seg ikke lese")
    return vert


def _ren_url(raa: str, autorisert_vert: str) -> str:
    """https-URL uten query, fragment og credentials, PÅ DET AUTORISERTE
    MÅLET — eller Motorfeil.

    HELE parsingen står innenfor vakten (Codex P1), også `d.port`: den er
    en property som SELV kaster ValueError på `https://x.example:not-a-
    port/` og på portnumre utenfor 1–65535. Stod den utenfor, ville en
    naken ValueError sluppet forbi `controller.kjor_en` (som kun fanger
    Motorfeil og ValidationError) og latt det claimede oppdraget stå
    ufullført til fristen — samme taushet §10 forbyr for `_antall`.

    VERTEN BINDES (Codex P1). Sidelista kom rått fra motoren, og kravet
    var kun https: en motor som fulgte en redirect — eller løy — kunne
    levere en skjemagyldig rapport der `sider_kontrollert` navngir
    `evil.example`, mens den signerte kvitteringen og hele
    autorisasjonskjeden navngir den bestilte verten. Konsumenten fikk da
    evidens om ET ANNET mål under en autorisasjonskjede som ser gyldig ut
    hele veien — nøyaktig den løgnen promotert evidens ikke skal kunne
    bære. Målautorisasjonen er per VERT (`web_hostname`), så en side
    utenfor den verten er utenfor det noen har autorisert, uansett hvor
    kontrollen kom fra.

    Sammenligningen bruker `normaliser_vertsnavn` på RÅSTRENGEN, ikke
    `d.hostname`: det er den funksjonen som avviser formene Python og
    nettleseren leser ulikt (`\\`, prosentkoding, IDNA), og en side
    ingen kan lese entydig er like ubrukelig som en side på feil vert.
    Porten bæres videre som før — `web_hostname` autoriserer en vert, ikke
    et portnummer.

    Bindingen ligger SIST, etter skjema- og portkontrollen, slik at en
    uleselig URL fortsatt melder sin egen feil. Og når den holder, skrives
    verten som `autorisert_vert`, ikke som `d.hostname`: de to navngir
    samme vert, men bare den første er den formen plattformen faktisk
    autoriserte (`kunde.example.` og `kunde.example` er ett navn, og
    rapporten skal ikke by leseren på to skrivemåter av målet sitt).
    """
    return _delt_url(raa, autorisert_vert)[0]


def _delt_url(raa: str, autorisert_vert: str) -> tuple[str, str]:
    """-> (rapport_url, identitets_url) for samme rå URL.

    Den FØRSTE er den som havner i rapporten: uten query, fragment og
    credentials, altså den `_ren_url` alltid har gitt. Den ANDRE er den
    samme URL-en MED query, og den brukes utelukkende til å avgjøre om to
    URL-er navngir samme side (Codex P1).

    De to må skilles fordi de svarer på hver sin ting. Rapporten er
    PROMOTERT evidens som lagres og leses av mennesker, og en query kan
    bære persondata (`?kunde=...`, tokens, søkeord) — den redigeres bort,
    som før. Men query-en er samtidig en BÆRENDE del av sideidentiteten:
    `/rapport?id=1` og `/rapport?id=2` er to forskjellige sider for hver
    eneste applikasjon som ruter på query. Da `enkeltside`-porten
    sammenlignet de REDIGERTE formene, sammenlignet den to strenger der
    nettopp det som skiller sidene var strøket, og en bestilling av
    `/rapport?id=1` godtok en kontroll av `/rapport?id=2` som «samme
    side» — samme løgn som feil sti bærer, ett nivå ned igjen.

    FRAGMENTET er bevisst IKKE med i identiteten. Det sendes aldri til
    serveren, så to URL-er som bare skiller seg der ber om nøyaktig samme
    dokument, og motoren er ikke forpliktet til å gjenta det den fikk.
    Å kreve likhet der ville avvist ærlige kjøringer uten å hindre en
    eneste forveksling av to sider serveren kan skille.

    Query-en sammenlignes RÅTT, tegn for tegn, uten omskriving av
    prosentkoding, parameterrekkefølge eller `+` mot `%20`: to
    skrivemåter kan bety det samme for én server og noe helt annet for en
    annen, og en normalisering som gjetter feil vei gjør to ULIKE sider
    like. Feilretningen her skal være avvisning (Motorfeil), ikke en
    stille sammenslåing.
    """
    from oppdragskontrakt import normaliser_vertsnavn, rapporturl
    try:
        d = urlsplit(str(raa))
        vert = d.hostname
    except ValueError as e:
        raise Motorfeil("uleselig url fra motoren") from e
    if d.scheme != "https" or not vert:
        raise Motorfeil("motoren rapporterte en ikke-https-url")
    if normaliser_vertsnavn(raa) != autorisert_vert:
        raise Motorfeil(
            "motoren rapporterte en side utenfor det autoriserte målet")
    # SELVE AVLEDNINGEN ER PLATTFORMENS (Codex P2), som `malvert` er det:
    # `oppdragskontrakt.rapporturl` skriver verten i normalform, dropper
    # standardporten (`:443` og ingenting er samme ressurs) og gir stien
    # slik nettleseren både koder og leser den — prosentkoding og
    # punktsegmenter. Den samme funksjonen måler lengdegrensa i
    # `bryter_feltkontrakten`, så forhåndsporten og rapporten kan ikke bli
    # uenige om hvilken URL bestillingen faktisk navngir.
    #
    # Vaktene over står igjen her fordi de bærer hver sin MOTORFEIL: en
    # side utenfor målet er noe annet enn en uleselig URL, og `rapporturl`
    # svarer bare None.
    ren = rapporturl(raa)
    if ren is None:
        raise Motorfeil("uleselig url fra motoren")
    return (ren, ren + (f"?{d.query}" if d.query else ""))


def _antall(raa, standard: int) -> int:
    """Motorens telling som heltall — eller Motorfeil, ALDRI en naken
    ValueError/TypeError/OverflowError.

    Motorutdata er ubetrodd (§2): `{"antall": "ukjent"}` skal gi den
    dokumenterte feil-kvitteringen. `controller.kjor_en` fanger kun
    Motorfeil og ValidationError, så en konverteringsfeil herfra ville
    sluppet ut av controllerløkka og latt det claimede oppdraget stå
    ufullført til fristen — nøyaktig taushetenes utfall §10 forbyr.

    Selve konverteringen er `motor.heltall` (Codex P1): den fanger også
    overflyt (`1e309`) og tall over JCS sitt trygge område, som ellers
    hadde smelt først under kanoniseringen rett før opplasting.

    UNDER ÉN ER UTENFOR KONTRAKTEN (Codex P1, runde 11). Begge feltene
    dette tallet ender i har `minimum: 1` i rapportskjemaet, og et tall
    under det er ubetrodde utdata vi ikke kan lese — ikke noe modulen
    skal reparere for motoren. De to reparasjonene som sto her gjorde
    hver sin løgn skjemagyldig:

      * funn: `antall < 1` ble hoppet over, og HELE funnet forsvant.
        `heltall` fanger alt `antall: 0.9` nettopp fordi den stillheten
        er skaden — men `-3` og `0` er ekte heltall, så de slapp forbi
        porten og traff `continue`. Rapporten ble da promotert med en
        kortere funnliste enn motoren fant, uten et eneste felt som sier
        fra: `sammendrag` teller det ikke, `avkortet` gjelder tak, ikke
        forkastede rader.
      * dekningsbegrensninger: `max(1, ...)` skrev `0` og `-5` om til
        `1` — en telling modulen fant på selv, i det feltet 014b B3 har
        for å si hva rapporten IKKE dekker.

    `standard` gjelder KUN når feltet mangler. Er standarden selv under
    1 (funn), betyr det at feltet er påkrevd — og da er en manglende
    telling like uleselig som en ugyldig.

    Gulvet sendes med til porten (`minst=1`), så et oppgitt tall møter
    grensen samme sted som resten av tallvakten. Sjekken under står
    likevel igjen: den dekker `standard`-veien, der ingen port er
    involvert i det hele tatt.
    """
    n = standard if raa is None or raa == "" else heltall(raa, minst=1)
    if n < 1:
        raise Motorfeil("antall fra motoren er under 1")
    return n


def _tekst(raa, felt: str) -> str:
    """Et tekstfelt fra motoren som EKTE streng — eller Motorfeil (Codex P1).

    `str(...)` fant på verdier i stedet for å avvise dem, og fabrikatet var
    skjemagyldig hele veien:

      * `regel_id` mangler → `str(None)` er `"None"`, som passerer
        `minLength: 1`. Rapporten ble PROMOTERT med en regel-id motoren
        aldri rapporterte, og en leser som slår opp `None` i regelsettet
        finner ingenting og tror det er regelsettet som er utdatert.
      * `regel_id: {"id": 1}` → `"{'id': 1}"`. Samme sak, men verre: nå
        står det en repr av en datastruktur der leseren venter en id.
      * Et eksempel som ikke er en streng (`{"selector": ".x"}`, `5`) ble
        på samme vis en «selektor» ingen kan kjøre — fabrikert evidens i
        akkurat det feltet som skal la noen etterprøve funnet.

    Motorutdata er ubetrodd (§2): et felt vi ikke kan lese er en motorfeil,
    ikke noe modulen skal gjette seg til. Tomme strenger avvises også —
    `""` bryter skjemaets `minLength: 1` for `regel_id`, og en tom selektor
    er ikke et eksempel.
    """
    if not isinstance(raa, str):
        raise Motorfeil(f"{felt} fra motoren er ikke en streng")
    if not raa:
        raise Motorfeil(f"{felt} fra motoren er tom")
    return raa


def _eksempelliste(raa) -> list:
    """Motorens eksempler som liste — eller Motorfeil (Codex P1).

    `list(raa or [])` var to feil i én linje, og begge er stille:

      * `"button.x"` er iterabel, så listen ble ETT ELEMENT PER TEGN.
        Kappet til `MAKS_EKSEMPLER` ble det ti enkelttegn som ser ut som
        selektorer og går videre som PROMOTERT evidens — eksempler
        modulen fant på selv, ikke noe motoren rapporterte. Samtidig satt
        det `maks_eksempler_sett` og kunne slå `avkortet` på.
      * `5` er ikke iterabel: `list(5)` er en naken TypeError, og
        `controller.kjor_en` fanger kun Motorfeil og ValidationError. Det
        claimede oppdraget ble stående ufullført til fristen — taushetens
        utfall §10 forbyr.

    Bare list/tuple er en eksempelliste. `None` og tom liste betyr «ingen
    eksempler», som er lovlig; alt annet er utdata vi ikke kan lese.
    """
    if raa is None:
        return []
    if not isinstance(raa, (list, tuple)):
        raise Motorfeil("eksempellisten fra motoren er ikke en liste")
    return list(raa)


def _sidebudsjett(payload: dict) -> tuple:
    """-> (omfang, maks_sider): sidebudsjettet oppdraget FAKTISK ba om.

    Payloaden er plattformens, ikke motorens, men den er like påkrevd å
    kunne lese: er `omfang` borte eller ukjent, vet modulen ikke hva den
    ble bedt om, og da kan den heller ikke se at motoren gikk utenfor.
    Fail-closed er samme positur som `_autorisert_vert` har for målet —
    en `bygg` uten bestilt omfang har ingenting å måle omfanget MOT.

    `enkeltside` er ett sidebudsjett på 1, uansett hva `maks_sider` sier:
    står de to mot hverandre, er det den strengeste som er bestillingen
    (`maks_sider: 50` gjør ikke en enkeltsidekontroll til en crawl).
    """
    omfang = payload.get("omfang")
    if omfang not in _OMFANG:
        raise Motorfeil("oppdragets omfang lar seg ikke lese")
    maks = payload.get("maks_sider")
    if maks is not None and (isinstance(maks, bool)
                             or not isinstance(maks, int) or maks < 1):
        raise Motorfeil("oppdragets maks_sider lar seg ikke lese")
    if omfang == "enkeltside":
        maks = 1 if maks is None else min(maks, 1)
    return omfang, maks


def _bestilt_url(payload: dict, autorisert_vert: str) -> str:
    """Den BESTILTE sidens IDENTITET, kanonisert med NØYAKTIG samme
    funksjon som motorens sider (`_delt_url`).

    To normaliseringer ville vært to svar på `https://kunde.example` mot
    `https://kunde.example/`, og da hadde sammenligningen under vært pynt
    — samme grunn som `_autorisert_vert` låner plattformens `malvert` i
    stedet for å normalisere verten selv.

    Det er identitetsformen (MED query) som hentes, ikke rapportformen:
    bestillingens query er det som skiller `/rapport?id=1` fra
    `/rapport?id=2`, og en sammenligning uten den godtar den andre siden
    som den første (Codex P1). Se `_delt_url`.

    Feilteksten skrives om: kommer `_delt_url` til kort her, er det
    OPPDRAGET som ikke lar seg lese, ikke motoren.
    """
    try:
        return _delt_url(payload.get("mal_url"), autorisert_vert)[1]
    except Motorfeil as e:
        raise Motorfeil("oppdragets mål lar seg ikke lese") from e


def _sidestatus(raa) -> str:
    """Motorens sidestatus som EKTE enumverdi — eller Motorfeil (Codex P1).

    `raa if raa in ("ok", "feilet") else "feilet"` var en STILLE OMSKRIVING,
    og den skrev alltid samme vei: mot en påstand om at siden feilet.

      * `status` mangler helt → rapporten sier `feilet` for en side
        motoren aldri sa noe om.
      * `status: "OK"`, `status: "ferdig"`, `status: null`, `status: 3` →
        samme sak. Verdien er skjemagyldig, så den går rett gjennom
        valideringen og videre til PROMOTERT evidens.

    Det er ikke en sanering, det er fabrikert evidens: rapporten hevder en
    sidefeil hos kunden som motoren ikke rapporterte, og det er nøyaktig
    den slags påstand promotert evidens ikke skal kunne bære — en leser som
    ser `feilet` tror kontrollen faktisk møtte en side som ikke lot seg
    kontrollere. At retningen er «pessimistisk» gjør den ikke ufarlig: en
    WCAG-rapport med fabrikerte sidefeil er et funn mot et nettsted ingen
    har målt.

    Ubetrodde utdata vi ikke kan lese er en motorfeil (§2), og den
    dokumenterte veien for uleselig motorutdata er feil-kvitteringen — ikke
    en rapport modulen har fylt hullene i selv. `isinstance(..., str)` står
    der fordi enumen er strenger: uten den ville `True` og `1` blitt
    sammenlignet med tall-likhet et annet sted i koden en dag.
    """
    if not isinstance(raa, str) or raa not in ("ok", "feilet"):
        raise Motorfeil("status fra motoren er ikke 'ok' eller 'feilet'")
    return raa


def bygg(resultat: Motorresultat, *, payload: dict, kontekst: dict) -> dict:
    """-> rapport-dict, klar for skjemavalidering (som controlleren ALLTID
    kjører selv før opplasting — serveren validerer uansett, men modulen
    skal ikke sende noe den selv kan se er ugyldig).

    `kontekst` er controllerens serverkontekst:
    {axe_versjon, chromium_versjon, container_image_digest, viewport,
     locale, timezone} — fra config/digest, aldri fra motoren.
    """
    # Målet FØRST: hver side som kommer med må ligge på den verten
    # oppdraget er autorisert for (Codex P1) — se `_ren_url`.
    autorisert_vert = _autorisert_vert(payload)
    sider = []
    # Sidenes IDENTITET (med query) beholdes ved siden av rapportformen:
    # rapporten skal ikke bære query-en, men `enkeltside`-porten under må
    # sammenligne med den (Codex P1) — se `_delt_url`.
    identiteter = []
    for s in resultat.sider:
        if not isinstance(s, dict):
            raise Motorfeil("side-post uleselig")
        ren, identitet = _delt_url(s.get("url"), autorisert_vert)
        identiteter.append(identitet)
        sider.append({"url": ren, "status": _sidestatus(s.get("status"))})
    if not sider:
        raise Motorfeil("motoren kontrollerte ingen sider")
    # ... og OMFANGET er oppdragets (Codex P1). Vertsbindingen over sier
    # bare at sidene ligger på riktig nettsted; den sier ingenting om HVOR
    # MANGE av dem noen har bedt om. En motor kunne derfor levere femti
    # sider på den autoriserte verten under et oppdrag som ba om
    # `enkeltside` / `maks_sider: 1`, og både skjemaet (maxItems: 50) og
    # vertsbindingen slapp den gjennom til PROMOTERT evidens.
    #
    # To skader, ikke én:
    #   * evidensen motsier bestillingen — et artefakt om et helt nettsted
    #     under en beslutning som autoriserte én side, og leseren har
    #     ingenting i rapporten som sier at det skjedde.
    #   * crawlbudsjettet er en EKSTERN grense: `ekstern_lesing` er
    #     observerbar trafikk mot noen andres nettsted, og modulen var den
    #     eneste som kunne se at motoren gikk over det oppdraget tillot.
    #     Uten porten var overskridelsen usynlig for hele plattformen.
    #
    # For `enkeltside` holder det ikke å telle til én: den ene siden må
    # VÆRE den bestilte. En rapport om `/annet` på riktig vert er evidens
    # om noe ingen har bestilt — samme løgn som feil vert bærer, bare ett
    # nivå ned i URL-en.
    omfang, maks_sider = _sidebudsjett(payload)
    if maks_sider is not None and len(sider) > maks_sider:
        raise Motorfeil(
            f"motoren kontrollerte {len(sider)} sider, oppdraget ba om"
            f" {maks_sider}")
    if omfang == "enkeltside":
        bestilt = _bestilt_url(payload, autorisert_vert)
        if identiteter[0] != bestilt:
            raise Motorfeil(
                "motoren kontrollerte en annen side enn den bestilte")

    sammendrag = {k: 0 for k in _ALVOR}
    funn = []
    # Flest eksempler ETT enkelt funn hadde (Codex P2): kappet vi en
    # eksempelliste, er rapporten avkortet, og da skal `avkortet` si det.
    maks_eksempler_sett = 0
    for f in resultat.funn:
        if not isinstance(f, dict) or f.get("alvorlighet") not in _ALVOR:
            raise Motorfeil("funn-post uleselig")
        # Standarden er 0, altså under skjemaets minimum: `antall` er
        # PÅKREVD i et funn, og `_antall` avviser både det manglende og
        # det ugyldige i stedet for å slette funnet (Codex P1).
        antall = _antall(f.get("antall"), 0)
        sammendrag[f["alvorlighet"]] += antall
        if len(funn) < MAKS_FUNN:
            eksempler = _eksempelliste(f.get("eksempler"))
            maks_eksempler_sett = max(maks_eksempler_sett, len(eksempler))
            funn.append({
                "regel_id": _tekst(f.get("regel_id"), "regel_id")[:128],
                "alvorlighet": f["alvorlighet"],
                "antall": antall,
                "eksempler": [_tekst(e, "eksempel")[:MAKS_SELEKTOR]
                              for e in eksempler[:MAKS_EKSEMPLER]]})

    truffet, tak, verdi = (tuple(resultat.avkortet) + (None, None, None))[:3]
    # `truffet` er en PÅSTAND, ikke et hint (Codex P2): `bool(truffet)`
    # gjorde `[0, null, null]` om til det skjemagyldige `truffet: false`
    # og `"false"` om til `true`. Den falske retningen er den farlige —
    # promotert evidens som påstår at ingenting var utelatt, i det ene
    # feltet leseren har for å vite hva rapporten IKKE dekker (014b B3),
    # nettopp når controlleren ikke klarte å lese dekningssignalet.
    if not isinstance(truffet, bool):
        raise Motorfeil("avkortet.truffet fra motoren er ikke boolsk")
    # `tak` og `verdi` er RÅ motortall som går rett inn i et heltallsfelt
    # uten øvre skjemagrense — samme eksponering som `antall`, og derfor
    # gjennom samme port (Codex P1). `max(0, ...)` er borte (Codex P2):
    # den gjorde `verdi: -5` om til en `0` motoren aldri oppga, i det ene
    # feltet som forteller hvor mye rapporten utelot. Porten håndhever
    # gulvet, så et negativt dekningstall er Motorfeil som alt annet
    # uleselig.
    tak = None if tak is None else heltall(tak)
    verdi = None if verdi is None else heltall(verdi)
    # ... og trippelen må være enig med seg selv (Codex P2): en telling
    # OVER sitt eget tak er per definisjon et truffet tak. `(false, 10, 25)`
    # er ikke en beskjeden rapport, det er to motstridende påstander, og
    # å velge den ene for motoren ville vært å finne på et dekningssignal.
    if not truffet and tak is not None and verdi is not None and verdi > tak:
        raise Motorfeil(
            "avkortet motsier seg selv: verdi over taket, men truffet er usann")
    # Kappet VI en eksempelliste, er rapporten avkortet (Codex P2). Uten
    # dette kunne den promoterte evidensen påstå `truffet: false` samtidig
    # som den utelot kjente eksempler — feltet skal aldri love mer
    # fullstendighet enn det som faktisk står i rapporten. Verdien er det
    # STØRSTE observerte eksempelantallet: taket er per funn, så det er den
    # tellingen taket ble målt mot.
    if maks_eksempler_sett > MAKS_EKSEMPLER:
        truffet = True
        tak = tak if tak is not None else MAKS_EKSEMPLER
        verdi = verdi if verdi is not None else maks_eksempler_sett
    # Kappet VI funnlisten, er rapporten avkortet uansett hva proxyen sa —
    # `avkortet` skal aldri love mer fullstendighet enn det som står i den.
    if len(resultat.funn) > MAKS_FUNN:
        truffet, tak = True, (tak if tak is not None else MAKS_FUNN)
        verdi = verdi if verdi is not None else len(resultat.funn)

    # Dekningsbegrensningene SLÅS SAMMEN per (vert, art) før taket måles
    # (Codex P2): proxyen teller per subressurs, så den samme verten kan
    # komme igjen mange ganger. Summeringen er ærlig — tallet blir det
    # totale — og den gjør at taket sjelden treffer i det hele tatt.
    #
    # En uleselig rad FEILER, den forkastes ikke (Codex P2). En tom
    # `dekningsbegrensninger` betyr i rapportkontrakten «ingen kjente
    # begrensninger» — ikke «vi klarte ikke lese dem». Ble radene stille
    # droppet, kunne en ødelagt proxy produsere PROMOTERT evidens som
    # påstår ren dekning nettopp når dekningen er ukjent. Det er den ene
    # løgnen hele feltet finnes for å hindre (014b B3), og den er verre enn
    # et feilet oppdrag: et feilet oppdrag ser man.
    samlet: dict[tuple, dict] = {}
    for b in resultat.blokkert:
        if not isinstance(b, dict):
            raise Motorfeil("blokkert-post uleselig")
        vert = str(b.get("vert") or "").split("/")[0].split("?")[0].lower()
        # Verten må også være en vert. Uten denne ville en «nesten-vert»
        # (`http://x.example/`, en tom streng, et tall) blitt til en
        # ValidationError langt unna i stedet for den dokumenterte
        # feil-kvitteringen — samme utfall som `_ren_url` finnes for.
        if not _VERT.match(vert) or len(vert) > 253:
            raise Motorfeil("blokkert-post har ikke et lesbart vertsnavn")
        art = (b.get("art") if b.get("art") in
               ("stilark", "font", "skript", "bilde", "annet") else "annet")
        nokkel = (vert, art)
        post = samlet.setdefault(nokkel, {"vert": nokkel[0], "antall": 0,
                                          "art": art})
        # Ingen `max(1, ...)`: `_antall` avviser en telling under 1 i
        # stedet for at modulen finner på en (Codex P1). Standarden 1
        # gjelder bare den manglende tellingen — raden ER en kjent
        # begrensning, og «minst én» er da det raden selv sier.
        post["antall"] += _antall(b.get("antall"), 1)
    # Størst først: treffer taket likevel, er det de STØRSTE
    # begrensningene som kommer med, ikke de tilfeldig første.
    begrensninger = sorted(samlet.values(),
                           key=lambda p: (-p["antall"], p["vert"], p["art"]))
    # ... og kappes lista, SIER `avkortet` det. Uten dette kunne den
    # promoterte evidensen påstå at ingenting var utelatt samtidig som den
    # utelot kjente dekningsbegrensninger — stikk i strid med hele
    # poenget med feltet (014b B3).
    if len(begrensninger) > MAKS_BEGRENSNINGER:
        truffet = True
        tak = tak if tak is not None else MAKS_BEGRENSNINGER
        verdi = verdi if verdi is not None else len(begrensninger)
        begrensninger = begrensninger[:MAKS_BEGRENSNINGER]

    rapport = {
        "kravsett": payload.get("kravsett"),
        "regelsett_versjon": resultat.regelsett_versjon,
        "kjort_ts": datetime.now(timezone.utc).isoformat(),
        "varighet_ms": resultat.varighet_ms,
        "sider_kontrollert": sider,
        "funn": funn,
        "sammendrag": sammendrag,
        "avkortet": {"truffet": bool(truffet), "tak": tak, "verdi": verdi},
        "dekningsbegrensninger": begrensninger,
        "miljo": {k: kontekst[k] for k in
                  ("axe_versjon", "chromium_versjon",
                   "container_image_digest", "viewport", "locale",
                   "timezone")},
        "manuelle_kriterier_vurdert": False,
    }
    return _under_taket(rapport)


def _under_taket(rapport: dict) -> dict:
    """Rapporten under 1 MiB kanonisk — ærlig kappet, eller Motorfeil.

    Antallsgrensene over er IKKE nok (Codex P1): en støyende kontroll nær
    skjemamaksima — 500 funn à ti 200-tegns eksempler og 128-tegns
    regel-id-er, pluss 50 lange URL-er — passerer skjemavalideringen og
    blir likevel avvist av `/v1/artefakt` på 1 048 576 byte. Da falt
    `ro.raise_for_status()` i controlleren ut UTEN feil-kvittering, og
    oppdraget ble stående claimet til fristen.

    Kappingen er ærlig på samme måte som de andre takene: eksemplene
    ofres først (de illustrerer), funnlisten sist (den ER funnene), og
    `avkortet.truffet` settes i det vi rører noe. `sammendrag` røres
    ALDRI — det teller alt motoren fant, og er sannheten om omfanget selv
    når listene er kortet ned.
    """
    n = len(_kanoniske_bytes(rapport))
    if n <= MAKS_BYTES:
        return rapport

    # Første kapping vinner tak/verdi, som ellers i `bygg` — men `truffet`
    # settes uansett, og det er det feltet en leser stoler på.
    def _si_fra():
        a = rapport["avkortet"]
        a["truffet"] = True
        if a["tak"] is None:
            a["tak"], a["verdi"] = MAKS_BYTES, n

    for tak in _NEDTRAPPING:
        for f in rapport["funn"]:
            del f["eksempler"][tak:]
        _si_fra()
        if len(_kanoniske_bytes(rapport)) <= MAKS_BYTES:
            return rapport

    # Eksemplene er borte og den er fortsatt for stor: kapp funnlisten.
    # Halvering, ikke ett og ett — kanoniseringen er ikke gratis, og en
    # rapport skal ikke koste 500 serialiseringer å pakke.
    while rapport["funn"]:
        del rapport["funn"][len(rapport["funn"]) // 2:]      # 1 → 0, tømmes
        if len(_kanoniske_bytes(rapport)) <= MAKS_BYTES:
            return rapport
    if len(_kanoniske_bytes(rapport)) <= MAKS_BYTES:
        return rapport
    # Uten funn i det hele tatt er resten av rapporten taklagt av skjemaet
    # (50 sider, 200 begrensninger) og kan ikke nå 1 MiB — kommer vi hit,
    # er noe annet galt enn støy. Da feiler oppdraget ÆRLIG, med den
    # dokumenterte feil-kvitteringen, i stedet for på en avvist opplasting.
    raise Motorfeil("rapporten er over 1 MiB selv uten funn")
