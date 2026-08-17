"""Rapportbygging + sanitering (PR-014c §7): motorens rå utdata inn,
skjemagyldig rapport ut — eller Motorfeil, aldri et delvis artefakt.

Saneringen er ÆRLIG kutting: URL-er strippes for query/fragment,
selektorer kappes til 200 tegn, maks 10 eksempler per regel, maks 500
funn og maks 200 dekningsbegrensninger — og kappes EN AV LISTENE, sier
`avkortet` det (den lyver aldri om at alt kom med). Miljøblokka er
SERVERKONTEKSTENS, aldri motorens.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

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
    """
    from policy_validator import jcs
    try:
        return jcs.kanoniske_bytes(rapport)
    except jcs.Ikkekanoniserbar as e:
        raise Motorfeil(f"rapporten kan ikke kanoniseres: {e}") from e


def _ren_url(raa: str) -> str:
    """https-URL uten query, fragment og credentials — eller Motorfeil.

    HELE parsingen står innenfor vakten (Codex P1), også `d.port`: den er
    en property som SELV kaster ValueError på `https://x.example:not-a-
    port/` og på portnumre utenfor 1–65535. Stod den utenfor, ville en
    naken ValueError sluppet forbi `controller.kjor_en` (som kun fanger
    Motorfeil og ValidationError) og latt det claimede oppdraget stå
    ufullført til fristen — samme taushet §10 forbyr for `_antall`.
    """
    try:
        d = urlsplit(str(raa))
        vert, port = d.hostname, d.port
    except ValueError as e:
        raise Motorfeil("uleselig url fra motoren") from e
    if d.scheme != "https" or not vert:
        raise Motorfeil("motoren rapporterte en ikke-https-url")
    return urlunsplit(("https", vert + (f":{port}" if port else ""),
                       d.path or "/", "", ""))


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
    """
    if raa is None or raa == "":
        return standard
    return heltall(raa)


def bygg(resultat: Motorresultat, *, payload: dict, kontekst: dict) -> dict:
    """-> rapport-dict, klar for skjemavalidering (som controlleren ALLTID
    kjører selv før opplasting — serveren validerer uansett, men modulen
    skal ikke sende noe den selv kan se er ugyldig).

    `kontekst` er controllerens serverkontekst:
    {axe_versjon, chromium_versjon, container_image_digest, viewport,
     locale, timezone} — fra config/digest, aldri fra motoren.
    """
    sider = []
    for s in resultat.sider:
        if not isinstance(s, dict):
            raise Motorfeil("side-post uleselig")
        sider.append({"url": _ren_url(s.get("url")),
                      "status": s.get("status")
                      if s.get("status") in ("ok", "feilet") else "feilet"})
    if not sider:
        raise Motorfeil("motoren kontrollerte ingen sider")

    sammendrag = {k: 0 for k in _ALVOR}
    funn = []
    # Flest eksempler ETT enkelt funn hadde (Codex P2): kappet vi en
    # eksempelliste, er rapporten avkortet, og da skal `avkortet` si det.
    maks_eksempler_sett = 0
    for f in resultat.funn:
        if not isinstance(f, dict) or f.get("alvorlighet") not in _ALVOR:
            raise Motorfeil("funn-post uleselig")
        antall = _antall(f.get("antall"), 0)
        if antall < 1:
            continue
        sammendrag[f["alvorlighet"]] += antall
        if len(funn) < MAKS_FUNN:
            eksempler = list(f.get("eksempler") or [])
            maks_eksempler_sett = max(maks_eksempler_sett, len(eksempler))
            funn.append({
                "regel_id": str(f.get("regel_id"))[:128],
                "alvorlighet": f["alvorlighet"],
                "antall": antall,
                "eksempler": [str(e)[:MAKS_SELEKTOR]
                              for e in eksempler[:MAKS_EKSEMPLER]]})

    truffet, tak, verdi = (tuple(resultat.avkortet) + (None, None, None))[:3]
    # `tak` og `verdi` er RÅ motortall som går rett inn i et heltallsfelt
    # uten øvre skjemagrense — samme eksponering som `antall`, og derfor
    # gjennom samme port (Codex P1).
    tak = None if tak is None else max(0, heltall(tak))
    verdi = None if verdi is None else max(0, heltall(verdi))
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
        post["antall"] += max(1, _antall(b.get("antall"), 1))
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
