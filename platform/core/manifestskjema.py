"""Validering av modulmanifester mot manifest-skjema.json (v3-delta pkt. 7).

Registeret (`registry.py`) leser manifester for å bestemme avhengigheter og
aktivering. Det bryr seg ikke om staging-sjekklisten. Sjekklisten er
derimot den ENESTE maskinlesbare kilden til om en modul faktisk er bevist
klar — og uten et skjema er «ja» og «nei» fritekst som kan endres til
hva som helst uten at noe protesterer.

Kjøres i CI. Kaster aldri: feilformet manifest gir feilliste, ikke
exception — samme kontrakt som `policy_validator.schema.valider_policy`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SKJEMA_STI = Path(__file__).resolve().parent / "manifest-skjema.json"
ARTEFAKTSKJEMA_STI = Path(__file__).resolve().parent / "artefakt-skjema.json"
REPOROT = Path(__file__).resolve().parents[2]

#: Grensene ytelsesporten faktisk krever (v2 Del 6). De står HER, som data
#: CI leser, og ikke bare i et manifestnotat: et tall i en kommentar kan
#: ikke gjøre en kjøring rød.
KRAVGRENSER: dict[str, dict] = {
    "perf-m01-v1": {
        "min_antall": 6000,
        "maks_feil": 0,
        "maks_rate_begrenset": 0,
        "maks_p95_ms": 150.0,
        # Lastprofilen er en del av kravet, ikke pynt. 6 000 forespørsler
        # sier ingenting om de ble sendt på ett minutt eller på to timer.
        "min_rate_per_sek": 100.0,
        "min_samtidige": 20,
        # Open-loop-generatoren treffer ikke nominell rate på desimalen, og
        # artefaktet runder til én desimal. 1 % gir rom for det uten å gi
        # rom for en kjøring på halv fart.
        "rate_slingringsmonn": 0.01,
        "varighet_slingringsmonn": 0.05,
    },
}


def _skjema() -> dict:
    return json.loads(SKJEMA_STI.read_text(encoding="utf-8"))


def valider_manifest(manifest: object) -> list[str]:
    """Tom liste == gyldig."""
    try:
        import jsonschema
        validator = jsonschema.Draft202012Validator(_skjema())
        return sorted(
            f"{'/'.join(str(p) for p in e.absolute_path) or '<rot>'}: {e.message}"
            for e in validator.iter_errors(manifest))
    except Exception as e:  # siste skanse — aldri ukontrollert exception
        return [f"intern valideringsfeil ({type(e).__name__}): {e}"]


def valider_alle(modulrot: Path) -> dict[str, list[str]]:
    """-> {modul-id: feilliste}. Alle nøkler med tom liste == alt gyldig."""
    import yaml
    ut: dict[str, list[str]] = {}
    for fil in sorted(Path(modulrot).glob("*/manifest.yaml")):
        data = yaml.safe_load(fil.read_text(encoding="utf-8"))
        ut[fil.parent.name] = valider_manifest(data)
    return ut


def _les_artefakt(sti: Path) -> tuple[dict | None, str | None, str]:
    """-> (innhold, sha256, feilmelding). Åpner og hasher i ETT lesesteg.

    Leses filen to ganger — én gang for hashen og én for innholdet — er det
    i prinsippet to forskjellige filer som valideres. Her hashes nøyaktig de
    bytene som deretter tolkes.
    """
    try:
        raa = sti.read_bytes()
    except OSError as e:
        return None, None, f"artefaktet kan ikke åpnes: {type(e).__name__}"
    sha = hashlib.sha256(raa).hexdigest()
    try:
        data = json.loads(raa.decode("utf-8"))
    except Exception as e:
        return None, sha, f"artefaktet er ikke gyldig JSON ({type(e).__name__})"
    if not isinstance(data, dict):
        return None, sha, "artefaktet er ikke et JSON-objekt"
    return data, sha, ""


def _tall(kilde: object, navn: str, felt: str) -> tuple[float | None, str]:
    """Fail-closed avlesning av en numerisk måling. -> (verdi, feilmelding).

    To feller dette finnes for, begge fant Codex i PR #8:

    * `bool` er en SUBKLASSE av `int` i Python. En `isinstance(x, int)`-test
      slipper derfor `feil: false` gjennom og leser den som 0 — altså
      «ingen feil» fordi feltet var en boolsk verdi, ikke et tall.
    * NaN gjør enhver sammenligning False. `p95: NaN` ville passert
      `p95 >= 150` og dermed ethvert tak vi setter. Et tak man ikke kan
      bryte er ikke et tak.
    """
    if not isinstance(kilde, dict):
        return None, f"{navn}: mangler (ingen `{felt}`-blokk)"
    verdi = kilde.get(felt)
    if isinstance(verdi, bool) or not isinstance(verdi, (int, float)):
        return None, f"{navn}={verdi!r} er ikke et tall"
    tall = float(verdi)
    if tall != tall or tall in (float("inf"), float("-inf")):
        return None, f"{navn}={verdi!r} er ikke et endelig tall"
    return tall, ""


def _teller(kilde: object, navn: str, felt: str) -> tuple[int | None, str]:
    """En TELLING: heltall >= 0. -> (verdi, feilmelding).

    Codex' P1 nr. 4 på PR #8: `_tall()` håndhevet at verdien var et endelig
    tall, men ikke hva tallet BETYR. Fire umulige artefakter passerte:
    negativt antall feil (−5 er «<= 0» og besto taket), negative
    beslutningstellinger som matematisk utlignet hverandre til riktig sum,
    og brøkdeler av forespørsler.

    Ingen av dem kan oppstå i en ekte kjøring. En validator som godtar dem
    validerer aritmetikk, ikke virkelighet.

    Flyttall avvises helt, også `6000.0`: produsenten teller med `len()` og
    heltallsaddisjon, så en float i et telle-felt betyr at noe har regnet
    der det skulle telt.
    """
    if not isinstance(kilde, dict):
        return None, f"{navn}: mangler (ingen `{felt}`-blokk)"
    verdi = kilde.get(felt)
    if isinstance(verdi, bool) or not isinstance(verdi, int):
        return None, (f"{navn}={verdi!r} er ikke en heltallstelling"
                      f" ({type(verdi).__name__})")
    if verdi < 0:
        return None, f"{navn}={verdi} er negativ — en telling kan ikke være det"
    return verdi, ""


def _positiv(kilde: object, navn: str, felt: str) -> tuple[float | None, str]:
    """En STØRRELSE som må være strengt positiv: tid, rate, svartid.

    Null er ikke en gyldig måling her. En kjøring som varte 0 sekunder har
    ikke skjedd, og en rate på 0 er ingen last. Kravet er skilt fra
    `_teller` fordi disse ER kontinuerlige — 60,03 sekunder er riktig.
    """
    tall, feil = _tall(kilde, navn, felt)
    if feil:
        return None, feil
    if tall <= 0:
        return None, f"{navn}={tall:g} må være > 0"
    return tall, ""


def valider_artefaktformat(art: object) -> list[str]:
    """Artefaktet mot det LUKKEDE skjemaet. Tom liste == gyldig.

    Codex' P1 nr. 5 på PR #8: `_sjekk_grenser` leste feltene den kjente til
    og var blind for alt annet. Tre artefakter passerte derfor:
    `sikkerhet: 500` blant kø-tellingene, `UKJENT: 500` blant
    beslutningsutfallene, og `feiltyper: false` i stedet for en liste (falsy
    ⇒ «ingen feiltyper»).

    `additionalProperties: false` snur standarden: en ukjent nøkkel er en
    FEIL, ikke stillhet. Utvider noen artefaktformatet, må de utvide porten
    i samme slengen — det er hele poenget.
    """
    try:
        import jsonschema
        skjema = json.loads(ARTEFAKTSKJEMA_STI.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(skjema)
        return sorted(
            f"{'/'.join(str(p) for p in e.absolute_path) or '<rot>'}: {e.message}"
            for e in validator.iter_errors(art))
    except Exception as e:
        return [f"intern valideringsfeil ({type(e).__name__}): {e}"]


def _sjekk_grenser(krav_id: str, art: dict) -> list[str]:
    """Håndhever KRAVGRENSER og artefaktets INTERNE invarianter.

    Dette er selve poenget med porten: `bestatt: true` inne i artefaktet er
    produsentens EGEN påstand, og det samme gjelder `en_til_en` og
    `routing_stemmer`. Codex' P1 nr. 3 på PR #8 viste hvorfor det ikke
    holder å lese sammendragsboolene: et artefakt med
    `unntaksrader_per_sakstype.normal = 0`, `forventede_normalsaker = 9999`
    og `routing_stemmer: true` passerte porten. Tallene motsa flagget, og
    bare flagget ble lest.

    Derfor REGNES invariantene ut på nytt her, og lastprofilen håndheves:
    6 000 forespørsler sier ingenting om de ble sendt på ett minutt eller
    på to timer, og en kjøring med rate 1/s og samtidighet 1 er ikke den
    porten kravet beskriver.
    """
    grense = KRAVGRENSER.get(krav_id)
    if grense is None:
        return [f"ukjent krav_id {krav_id!r} — ingen grenser å håndheve"]
    feil: list[str] = []
    if art.get("krav_id") != krav_id:
        feil.append(f"artefaktet gjelder {art.get('krav_id')!r}, "
                    f"manifestet påstår {krav_id!r}")
    if art.get("bestatt") is not True:
        feil.append("artefaktet sier ikke bestatt: true")

    m = art.get("maalt")
    if not isinstance(m, dict):
        return feil + ["artefaktet mangler `maalt`"]
    oppsett = art.get("oppsett")
    if not isinstance(oppsett, dict):
        return feil + ["artefaktet mangler `oppsett` — lastprofilen er ukjent"]

    # --- Volum, feil og svartid -------------------------------------------
    # Alt som TELLES leses med `_teller`: heltall >= 0. Alt som MÅLES med
    # `_positiv`: endelig og > 0.
    antall, m_feil = _teller(m, "antall", "antall")
    if m_feil:
        feil.append(m_feil)
    elif antall < grense["min_antall"]:
        feil.append(f"antall={antall}, krever >= {grense['min_antall']}")

    # Konfigurert antall må stemme med målt antall. Feltet lå i artefaktet
    # og ble aldri lest: et oppsett som ba om 6 000 og en måling som
    # rapporterte noe annet, er to ulike kjøringer i samme fil.
    oppsett_antall, m_feil = _teller(oppsett, "oppsett.antall", "antall")
    if m_feil:
        feil.append(m_feil)
    elif antall is not None and oppsett_antall != antall:
        feil.append(f"oppsett.antall={oppsett_antall} != maalt.antall={antall}")

    for felt, tak in (("feil", grense["maks_feil"]),
                      ("rate_begrenset", grense["maks_rate_begrenset"])):
        verdi, m_feil = _teller(m, felt, felt)
        if m_feil:
            feil.append(m_feil)
        elif verdi > tak:
            feil.append(f"{felt}={verdi}, krever <= {tak}")
    # `feiltyper` MÅ være en liste. `false` er falsy og ble lest som «ingen
    # feiltyper» — skjemaet tar det nå, men kontrollen står også her, fordi
    # denne funksjonen kalles direkte fra testene.
    feiltyper = m.get("feiltyper")
    if not isinstance(feiltyper, list):
        feil.append(f"feiltyper={feiltyper!r} er ikke en liste")
    elif feiltyper:
        feil.append(f"artefaktet har feiltyper: {feiltyper}")

    p95, m_feil = _positiv(m.get("svartid_ms"), "p95", "p95")
    if m_feil:
        feil.append(m_feil)
    elif p95 >= grense["maks_p95_ms"]:
        feil.append(f"p95={p95} ms, krever < {grense['maks_p95_ms']} ms")

    # --- Lastprofilen: rate, samtidighet, varighet ------------------------
    krevd_rate = grense["min_rate_per_sek"]
    nedre_rate = krevd_rate * (1.0 - grense["rate_slingringsmonn"])
    for leser, kilde, navn, felt, nedre in (
            (_positiv, oppsett, "oppsett.rate_per_sek", "rate_per_sek",
             krevd_rate),
            (_positiv, m, "maalt.oppnadd_rate", "oppnadd_rate", nedre_rate),
            (_teller, oppsett, "oppsett.samtidige", "samtidige",
             grense["min_samtidige"])):
        verdi, m_feil = leser(kilde, navn, felt)
        if m_feil:
            feil.append(m_feil)
        elif verdi < nedre:
            feil.append(f"{navn}={verdi:g}, krever >= {nedre:g}")

    # `_positiv` avviser 0 og negativ FØR sammenligningen under. Tidligere
    # sto det `elif antall is not None and varighet > 0:` — altså hoppet en
    # varighet på 0 over sin egen kontroll. En vakt som lar ugyldige verdier
    # slippe forbi kontrollen sin, er fail-open.
    varighet, m_feil = _positiv(m, "maalt.varighet_sek", "varighet_sek")
    if m_feil:
        feil.append(m_feil)
    elif antall is not None:
        # Varigheten MÅ stemme med antall/rate. Uten denne kunne et
        # artefakt oppgi 6 000 forespørsler, rate 100 og varighet 3 600 s:
        # hvert enkelt tall består sin egen grense, mens kjøringen i
        # virkeligheten gikk på 1,7/s.
        forventet = antall / krevd_rate
        avvik = abs(varighet - forventet) / forventet
        if avvik > grense["varighet_slingringsmonn"]:
            feil.append(
                f"varighet_sek={varighet:g} passer ikke med antall={antall:.0f}"
                f" @ {krevd_rate:g}/s (forventet ~{forventet:g} s,"
                f" avvik {avvik * 100:.0f} %)")

    # --- Interne invarianter: tallene mot hverandre, ikke mot flagg -------
    b = m.get("beslutninger")
    unntak_besluttet = None
    if not isinstance(b, dict) or not b:
        feil.append("artefaktet mangler `beslutninger`")
    else:
        # `_teller` er avgjørende her: med `_tall` kunne TILLAT=6000,
        # STOPP=-1200 og UNNTAK=1200 summere seg til 6 000 og bestå. En
        # negativ beslutningstelling finnes ikke, men aritmetikken bryr seg
        # ikke — den utligner bare.
        sum_b = 0
        gyldig = True
        for utfall in ("TILLAT", "STOPP", "UNNTAK"):
            verdi, m_feil = _teller(b, f"beslutninger.{utfall}", utfall)
            if m_feil:
                feil.append(m_feil)
                gyldig = False
            else:
                sum_b += verdi
                if utfall == "UNNTAK":
                    unntak_besluttet = verdi
        if gyldig and antall is not None and sum_b != antall:
            feil.append(f"summen av beslutningene ({sum_b}) er ikke lik"
                        f" antall ({antall})")

    k = art.get("etterkontroll")
    if not isinstance(k, dict):
        feil.append("artefaktet mangler `etterkontroll`")
        return feil

    svar, f1 = _teller(k, "auditerte_svar", "auditerte_svar")
    rader, f2 = _teller(k, "revisjonsrader", "revisjonsrader")
    for m_feil in (f1, f2):
        if m_feil:
            feil.append(m_feil)
    if not f1 and not f2 and antall is not None:
        if not (svar == rader == antall):
            feil.append(f"auditerte_svar={svar}, revisjonsrader={rader},"
                        f" antall={antall} — alle tre må være like")
    if k.get("en_til_en") is not True:
        feil.append("etterkontroll: en_til_en er ikke true")

    # Routing: sammenlign TALLENE. Flagget leses i tillegg, aldri i stedet.
    per_sakstype = k.get("unntaksrader_per_sakstype")
    if not isinstance(per_sakstype, dict):
        feil.append("etterkontroll mangler `unntaksrader_per_sakstype`")
    else:
        normale, f3 = _teller(per_sakstype, "unntaksrader normal", "normal")
        forventet, f4 = _teller(k, "forventede_normalsaker",
                                "forventede_normalsaker")
        for m_feil in (f3, f4):
            if m_feil:
                feil.append(m_feil)
        if not f3 and not f4:
            if normale != forventet:
                feil.append(f"normal-kørader ({normale}) != forventede"
                            f" normalsaker ({forventet})")
            if unntak_besluttet is not None and normale != unntak_besluttet:
                feil.append(f"normal-kørader ({normale}) != antall"
                            f" UNNTAK-beslutninger ({unntak_besluttet})")
        # ALLE sakstyper telles med, ikke bare `normal`. `sikkerhet` og
        # `drift` er lovlige sakstyper — men den syntetiske miksen i
        # perf-m01-v1 produserer bare normalsaker, så en rad i en annen kø
        # betyr at kjøringen gjorde noe annet enn den rapporterer.
        # Skjemaet stopper en UKJENT sakstype; dette stopper en kjent
        # sakstype med et uventet antall.
        sum_koer = 0
        alle_gyldige = True
        for sakstype in sorted(per_sakstype):
            verdi, m_feil = _teller(per_sakstype, f"unntaksrader {sakstype}",
                                    sakstype)
            if m_feil:
                if sakstype != "normal":       # normal er alt rapportert over
                    feil.append(m_feil)
                alle_gyldige = False
            else:
                sum_koer += verdi
        if alle_gyldige and unntak_besluttet is not None \
                and sum_koer != unntak_besluttet:
            feil.append(f"summen av alle kø-rader ({sum_koer}) != antall"
                        f" UNNTAK-beslutninger ({unntak_besluttet})"
                        f" — fordeling: {dict(sorted(per_sakstype.items()))}")
    if k.get("routing_stemmer") is not True:
        feil.append("etterkontroll: routing_stemmer er ikke true")
    return feil


def valider_artefakter(manifest: dict, rot: Path | None = None) -> list[str]:
    """Håndhever evidenskjeden for hvert `ja` med krav_id. Tom liste == ok.

    Codex' P1 på PR #8: skjemaet krevde bare at `artefakt` var en ikke-tom
    STRENG. `artefakt: tull.json` passerte da like fint som en ekte måling,
    og hashen alene beviser bare at noen kjenner en streng. Her åpnes filen
    faktisk, hashen verifiseres mot innholdet, formatet valideres og
    tallene måles mot KRAVGRENSER.
    """
    rot = Path(rot) if rot is not None else REPOROT
    sjekkliste = (manifest or {}).get("staging_sjekkliste") or {}
    feil: list[str] = []
    for navn, p in sorted(sjekkliste.items()):
        if not isinstance(p, dict) or p.get("status") != "ja":
            continue
        krav_id = p.get("krav_id")
        if not krav_id:
            continue                      # ja uten krav_id krever ikke artefakt
        sti_tekst = p.get("artefakt")
        forventet = p.get("artefakt_sha256")
        if not sti_tekst or not forventet:
            feil.append(f"{navn}: ja med krav_id mangler artefakt/artefakt_sha256")
            continue
        sti = (rot / sti_tekst).resolve()
        try:
            sti.relative_to(rot.resolve())
        except ValueError:
            feil.append(f"{navn}: artefaktstien peker utenfor repoet")
            continue
        data, sha, melding = _les_artefakt(sti)
        if melding:
            feil.append(f"{navn}: {melding} ({sti_tekst})")
            continue
        if sha != forventet:
            feil.append(f"{navn}: sha256 stemmer ikke — manifestet sier "
                        f"{forventet[:12]}…, filen er {sha[:12]}…")
            continue
        # BEGGE lag kjører, alltid — formatet stopper ikke måletallene.
        #
        # Første utkast gjorde `continue` ved formatfeil. Det så ryddig ut,
        # men maskerte domenekontrollene: en negativ varighet bryter både
        # skjemaet (`exclusiveMinimum: 0`) og `_positiv`, og med et tidlig
        # avbrudd var det bare skjemaet som ble prøvd. Svekkes skjemaet
        # senere, ville domenetestene fortsatt vært grønne uten å ha kjørt.
        # To uavhengige lag er bare uavhengige hvis begge faktisk måles.
        feil += [f"{navn}: format — {m}" for m in valider_artefaktformat(data)]
        feil += [f"{navn}: {m}" for m in _sjekk_grenser(krav_id, data)]
    return feil


def uavklarte_punkter(manifest: dict) -> list[str]:
    """Sjekklistepunkter som IKKE er `ja`.

    Regelen som aldri fravikes (RUTINER pkt. 2): en modul settes ikke til
    `aktiv` før alle punkter er ja. Funksjonen gjør regelen målbar i stedet
    for å be noen huske den.
    """
    sjekkliste = (manifest or {}).get("staging_sjekkliste") or {}
    return sorted(navn for navn, p in sjekkliste.items()
                  if not isinstance(p, dict) or p.get("status") != "ja")


def aktiv_uten_bevis(manifest: dict) -> list[str]:
    """Tom liste med mindre modulen er `aktiv` OG har uavklarte punkter."""
    if (manifest or {}).get("status") != "aktiv":
        return []
    return uavklarte_punkter(manifest)
