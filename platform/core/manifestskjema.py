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
    # --- PR-006 -----------------------------------------------------------
    # Begge grensene defineres FØR arbeidet som skal måles (brief §5).
    # Rekkefølgen er ikke pedanteri: `rollback-m01-v1` har manglet i denne
    # dict-en helt siden PR-005c, og et `ja` ble derfor avvist med «ukjent
    # krav_id». Fail-closed var riktig, men det betød at den som skulle
    # gjøre rollback-arbeidet ikke hadde noen fasit å måle mot.
    "feilinjisering-m01-v1": {
        "min_injisert": 20,
        "min_kategorier": 3,
        # Andelene er 1.0 og ikke «minst 0.95». En injisert feil som ikke
        # ble behandlet, er nettopp den tilstanden punktet
        # `feilinjisering_til_unntakskø` skal bevise at ikke finnes.
        "krev_terminal_andel": 1.0,
        "krev_lost_andel": 1.0,
        "krev_manuell_andel": 1.0,
        "maks_varighet_sek": 300.0,
        # Målt MENS arbeideren kjører. Det er hele beviset for
        # prosessisolasjonen: er tallet innenfor mens M-37 maler på samme
        # boks, spiste ikke behandlingen ytelsesmarginen.
        "maks_p95_api_under_last_ms": 150.0,
        # Minst én sak SKAL gjennom lease-tap + re-claim (v2-delta pkt. 8).
        # Uten den er gjenopptaksveien udokumentert — og en gjenopptaksvei
        # som aldri er kjørt er en hypotese.
        "min_lease_tap_re_claim": 1,
    },
    "rollback-m01-v1": {
        "maks_deaktivering_s": 5.0,
        "maks_reaktivering_s": 5.0,
        "maks_tapte_loggposter": 0,
        "krev_avvist_andel": 1.0,
        "maks_halvferdige": 0,
    },
    # --- PR-012 (menneskelig unntaksbehandling) -------------------------
    # 12 saker over 4 kategorier (spec §10 + v2-delta): avvis-vei terminal ·
    # godkjenn-vei ny beslutning · sideeffekt → venter_utførelse → løst ·
    # fire-øyne to brukere. Pluss de harde invariantene: saksversjonskonflikt
    # gir 409 UTEN sideeffekt · samtidig arbeider + menneske → nøyaktig én
    # vinner · ingen klartekst i logg/dump · alle handlinger med aktør.
    # Andelene er 1.0: en injisert sak som ikke nådde sin terminaltilstand er
    # nettopp hullet artefaktet skal bevise at ikke finnes.
    "behandling-m37-v1": {
        "min_injisert": 12,
        # Kategorimengden må være EKSAKT de fire kontraktskategoriene (avvis,
        # godkjenn, sideeffekt, fire_oyne) — håndheves som settlikhet, ikke
        # som «minst fire» — og hver kategori krever `utfall == injisert > 0`.
        "krev_avvis_terminal_andel": 1.0,
        "krev_godkjenn_beslutning_andel": 1.0,
        # PR-012s menneskelige vei ender ved `venter_utførelse` (levert til
        # M-37-outboxen); →løst tilhører M-37 og bevises av `feilinjisering-m01`.
        "krev_sideeffekt_utforelse_andel": 1.0,
        "krev_fire_oyne_andel": 1.0,
        # Minst én saksversjonskonflikt SKAL kjøres — en 409-vei som aldri er
        # utløst er en hypotese — og den skal ALDRI ha en sideeffekt.
        "min_saksversjonskonflikt": 1,
        "maks_saksversjonskonflikt_sideeffekt": 0,
        # Minst én ekte konkurranse; «nøyaktig én vinner» håndheves fra
        # råtellinger (startet/fullført/vinnere/tapere), ikke et flagg.
        "min_samtidig_konkurranse": 1,
        # Scope-beslutningen §3: den EKTE kvitterings-vs-avvis-rasen (ikke to
        # menneskelige godkjenn). Minst én kjøring; begge tråder fullfører;
        # avvis flagger avklaring og kvitteringen (`bruk_kvitteringskapabilitet`)
        # bevares — og saken påstår ALDRI `avvist` mens et oppdrag lever
        # (`falskt_avvist` = 0, ikke «lite»).
        "min_kvitteringsrace": 1,
        "maks_kvitteringsrace_falskt_avvist": 0,
        # Ingen klartekst-begrunnelse i logg eller DB-dump — 0, ikke «lite».
        "maks_klartekst_treff": 0,
        "maks_varighet_sek": 300.0,
    },
    # PR-013: policyadministrasjon. Fire kategori-veier beviser
    # fire-øyne-fullmaktsmodellen; de harde invariantene beviser V10 (runtime
    # kan ikke skrive policyer), atomisiteten (aldri flere aktive) og at
    # godkjenneren attesterte DIFFEN. Alle andeler er 1.0: en injisert vei som
    # ikke nådde sin kontraktstilstand er nettopp hullet artefaktet skal
    # avkrefte.
    "policyadmin-v1": {
        # 4 kategorier × 2 = 8 (én kjøring er en anekdote; to per vei).
        "min_injisert": 8,
        # Kategorimengden EKSAKT (settlikhet), hver vei `utfall == injisert > 0`.
        "krev_utvider_aktivert_andel": 1.0,
        "krev_forfatter_alene_stopp_andel": 1.0,
        "krev_innsnevrer_aktivert_andel": 1.0,
        "krev_rebasering_andel": 1.0,
        # Atomisiteten (V10/V1): INGEN policy ender med mer enn én aktiv rad.
        # 0, ikke «lite».
        "maks_flere_aktive": 0,
        # V10: runtime MÅ nektes direkte skriving til `policyer` (målt: 1).
        "krev_runtime_skrivenekt": 1,
        # Godkjenneren attesterer DIFFEN: hver attestasjons `diff_hash` MÅ matche
        # rundens (treff == totalt > 0).
        "krev_diff_binding_full": True,
        "maks_varighet_sek": 300.0,
    },
}

#: Hvilket LUKKEDE skjema som gjelder for hvilket krav. Uten dette ville
#: alle artefakter blitt målt mot ytelsesskjemaet, og et feilinjiserings-
#: artefakt ville feilet på «mangler `krav`» i stedet for å bli validert.
ARTEFAKTSKJEMAER: dict[str, str] = {
    "perf-m01-v1": "artefakt-skjema.json",
    "feilinjisering-m01-v1": "artefakt-feilinjisering-skjema.json",
    "rollback-m01-v1": "artefakt-rollback-skjema.json",
    "behandling-m37-v1": "artefakt-behandling-skjema.json",
    "policyadmin-v1": "artefakt-policyadmin-skjema.json",
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


def _andel(kilde: object, navn: str, felt: str) -> tuple[float | None, str]:
    """En ANDEL: endelig tall i [0, 1]. -> (verdi, feilmelding).

    Egen leser fordi `_tall` ville sluppet gjennom 1.5 og −0.2. En andel på
    1.5 er ikke et høyt tall — det er en umulig måling, på samme måte som
    en negativ telling er det. Samme lærdom som ga `_teller` og `_positiv`
    i PR #8 runde 4: spør hva tallet ER, ikke bare hvor stort det er.
    """
    tall, feil = _tall(kilde, navn, felt)
    if feil:
        return None, feil
    if not (0.0 <= tall <= 1.0):
        return None, f"{navn}={tall:g} er ikke en andel i [0, 1]"
    return tall, ""


def valider_artefaktformat(art: object,
                           krav_id: str | None = None) -> list[str]:
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
        filnavn = ARTEFAKTSKJEMAER.get(krav_id or "")
        sti = (ARTEFAKTSKJEMA_STI.parent / filnavn) if filnavn \
            else ARTEFAKTSKJEMA_STI
        skjema = json.loads(sti.read_text(encoding="utf-8"))
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

    # Hvert krav har sine egne domenegrenser. Felleskontrollene over
    # (krav_id stemmer, bestatt er true) gjelder alle; alt under er
    # kravspesifikt, og en felles «les tallene»-rutine ville uansett måttet
    # kjenne hvert felt for å kunne si noe om hva det BETYR.
    if krav_id == "feilinjisering-m01-v1":
        return feil + _grenser_feilinjisering(grense, art)
    if krav_id == "rollback-m01-v1":
        return feil + _grenser_rollback(grense, art)
    if krav_id == "behandling-m37-v1":
        return feil + _grenser_behandling(grense, art)
    if krav_id == "policyadmin-v1":
        return feil + _grenser_policyadmin(grense, art)

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


def _grenser_behandling(grense: dict, art: dict) -> list[str]:
    """`behandling-m37-v1` — de fire kategori-veiene + de harde invariantene.

    Invariantene REGNES UT på nytt her, aldri lest ut av et flagg (lærdommen
    fra PR #8 runde 3): hver kategori-andel sjekkes mot `antall/injisert`, og
    en kategori med `injisert = 0` er en vei som aldri ble prøvd — ikke en
    bestått. De harde grensene (saksversjonskonflikt uten sideeffekt, nøyaktig
    én vinner, ingen klartekst) måles mot råtellingene.
    """
    feil: list[str] = []
    m, oppsett = art.get("maalt"), art.get("oppsett")
    if not isinstance(m, dict) or not isinstance(oppsett, dict):
        return ["artefaktet mangler `maalt` og/eller `oppsett`"]

    injisert, f = _teller(oppsett, "oppsett.injisert_antall", "injisert_antall")
    if f:
        feil.append(f)
    elif injisert < grense["min_injisert"]:
        feil.append(f"injisert_antall={injisert},"
                    f" krever >= {grense['min_injisert']}")

    # Kategorimengden må være EKSAKT de fire kontraktskategoriene — ikke bare
    # «minst fire». Ellers består et sett med en oppdiktet kategori som fyller
    # tallet mens en ekte mangler.
    KONTRAKT = {"avvis", "godkjenn", "sideeffekt", "fire_oyne"}
    for navn, verdi in (("oppsett.kategorier", oppsett.get("kategorier")),
                        ("kategorier_dekket", m.get("kategorier_dekket"))):
        if not isinstance(verdi, list) or set(verdi) != KONTRAKT \
                or len(verdi) != len(KONTRAKT):
            feil.append(f"{navn}={verdi!r}, krever NØYAKTIG {sorted(KONTRAKT)}")

    # Hver kategori: andelen er 1.0, så kravet er EKSAKT `utfall == injisert`
    # med `injisert > 0`. `utfall/injisert >= 1.0` alene godtar teller > nevner
    # (2 av 1 = 200 %); det er en umulig måling, ikke en høy andel.
    sum_inj = 0
    for grp, antallsfelt in (("avvis", "terminal"),
                             ("godkjenn", "ny_beslutning"),
                             ("sideeffekt", "til_utforelse"),
                             ("fire_oyne", "fullfort")):
        u = m.get(grp)
        if not isinstance(u, dict):
            feil.append(f"maalt.{grp} mangler")
            continue
        inj, f1 = _teller(u, f"{grp}.injisert", "injisert")
        ant, f2 = _teller(u, f"{grp}.{antallsfelt}", antallsfelt)
        if f1 or f2:
            feil.extend(x for x in (f1, f2) if x)
            continue
        sum_inj += inj
        if inj == 0:
            feil.append(f"{grp}.injisert er 0 — veien ble aldri prøvd")
        elif ant != inj:
            feil.append(f"{grp}: {antallsfelt}={ant} != injisert={inj}"
                        f" — krever eksakt likhet (andel 1.0; teller > nevner"
                        f" er umulig)")

    # Summen av kategori-nevnerne MÅ være totalen: ellers består total=12 med
    # fire kategorier på 1/1 (bare fire faktiske forsøk).
    if sum_inj != injisert:
        feil.append(f"sum av kategori-injisert ({sum_inj}) != total"
                    f" injisert_antall ({injisert})")

    # «Nøyaktig én vinner» fra RÅTELLINGER, ikke et flagg: begge tråder må ha
    # FULLFØRT (ingen henger), og det skal være akkurat én vinner og resten
    # tapere per konkurranse. Null vinnere eller en hengende tråd = rødt.
    konk, fk = _teller(m, "samtidig_konkurranser", "samtidig_konkurranser")
    startet, fs = _teller(m, "samtidig_startet", "samtidig_startet")
    fullfort, ff = _teller(m, "samtidig_fullfort", "samtidig_fullfort")
    vinnere, fv = _teller(m, "samtidig_vinnere", "samtidig_vinnere")
    tapere, ft = _teller(m, "samtidig_tapere", "samtidig_tapere")
    if any((fk, fs, ff, fv, ft)):
        feil.extend(x for x in (fk, fs, ff, fv, ft) if x)
    else:
        if konk < grense["min_samtidig_konkurranse"]:
            feil.append(f"samtidig_konkurranser={konk}, krever >="
                        f" {grense['min_samtidig_konkurranse']}")
        if startet != 2 * konk:
            feil.append(f"samtidig_startet={startet} != 2*konkurranser"
                        f" ({2 * konk}) — to tråder per konkurranse")
        if fullfort != startet:
            feil.append(f"samtidig_fullfort={fullfort} != startet={startet}"
                        f" — en tråd fullførte ikke (hang / manglende resultat)")
        if vinnere != konk:
            feil.append(f"samtidig_vinnere={vinnere} != konkurranser={konk}"
                        f" — krever NØYAKTIG én vinner per konkurranse")
        if tapere != startet - vinnere:
            feil.append(f"samtidig_tapere={tapere} != startet-vinnere"
                        f" ({startet - vinnere})")

    # Kvitterings-vs-avvis-rasen (scope-beslutningen §3): begge tråder
    # fullfører, avvis flagger avklaring, kvitteringen bevares — og INGEN sak
    # påstår `avvist` mens et oppdrag lever. Alt fra råtellinger.
    krace, fkr = _teller(m, "kvitteringsrace_konkurranser",
                         "kvitteringsrace_konkurranser")
    kfull, fkf = _teller(m, "kvitteringsrace_fullfort", "kvitteringsrace_fullfort")
    kflagg, fkfl = _teller(m, "kvitteringsrace_avvis_flagget",
                           "kvitteringsrace_avvis_flagget")
    kbrukt, fkb = _teller(m, "kvitteringsrace_kvittering_brukt",
                          "kvitteringsrace_kvittering_brukt")
    kfalsk, fkfa = _teller(m, "kvitteringsrace_falskt_avvist",
                           "kvitteringsrace_falskt_avvist")
    if any((fkr, fkf, fkfl, fkb, fkfa)):
        feil.extend(x for x in (fkr, fkf, fkfl, fkb, fkfa) if x)
    else:
        if krace < grense["min_kvitteringsrace"]:
            feil.append(f"kvitteringsrace_konkurranser={krace}, krever >="
                        f" {grense['min_kvitteringsrace']}")
        if kfull != 2 * krace:
            feil.append(f"kvitteringsrace_fullfort={kfull} != 2*konkurranser"
                        f" ({2 * krace}) — avvis-tråd + kvittering-tråd må begge fullføre")
        if kflagg != krace:
            feil.append(f"kvitteringsrace_avvis_flagget={kflagg} != konkurranser"
                        f" ({krace}) — avvis skal flagge avklaring hver gang")
        if kbrukt != krace:
            feil.append(f"kvitteringsrace_kvittering_brukt={kbrukt} != konkurranser"
                        f" ({krace}) — kvitteringen skal bevares hver gang")
        if kfalsk > grense["maks_kvitteringsrace_falskt_avvist"]:
            feil.append(f"kvitteringsrace_falskt_avvist={kfalsk}, krever <="
                        f" {grense['maks_kvitteringsrace_falskt_avvist']} —"
                        f" saken påsto `avvist` mens et oppdrag levde")

    for felt, tak, notat in (
            ("saksversjonskonflikt_sideeffekt",
             grense["maks_saksversjonskonflikt_sideeffekt"],
             "en konflikt skal ALDRI ha sideeffekt"),
            ("klartekst_treff", grense["maks_klartekst_treff"],
             "ingen klartekst i logg/dump")):
        v, f = _teller(m, felt, felt)
        if f:
            feil.append(f)
        elif v > tak:
            feil.append(f"{felt}={v}, krever <= {tak} ({notat})")

    v409, f = _teller(m, "saksversjonskonflikt_409", "saksversjonskonflikt_409")
    if f:
        feil.append(f)
    elif v409 < grense["min_saksversjonskonflikt"]:
        feil.append(f"saksversjonskonflikt_409={v409}, krever >="
                    f" {grense['min_saksversjonskonflikt']}")

    med, f1 = _teller(m, "handlinger_med_aktor", "handlinger_med_aktor")
    tot, f2 = _teller(m, "handlinger_totalt", "handlinger_totalt")
    if f1 or f2:
        feil.extend(x for x in (f1, f2) if x)
    elif tot == 0 or med != tot:
        feil.append(f"handlinger_med_aktor={med} != handlinger_totalt={tot}"
                    f" — alle handlinger i revisjonsloggen MÅ ha aktør")

    varighet, f = _positiv(m, "maalt.varighet_sek", "varighet_sek")
    if f:
        feil.append(f)
    elif varighet > grense["maks_varighet_sek"]:
        feil.append(f"varighet_sek={varighet:g},"
                    f" krever <= {grense['maks_varighet_sek']:g}")
    return feil


def _grenser_policyadmin(grense: dict, art: dict) -> list[str]:
    """`policyadmin-v1` — de fire fullmakts-veiene + de harde invariantene.

    Som `_grenser_behandling`: hver andel REGNES UT på nytt fra råtellinger, og
    en kategori med `injisert = 0` er en vei som aldri ble prøvd — ikke en
    bestått. Kategorimengden håndheves som EKSAKT settlikhet (ikke «minst
    fire»), så et sett med en oppdiktet kategori som fyller tallet mens en ekte
    mangler, avvises. De harde invariantene (aldri flere aktive, runtime kan
    ikke skrive policyer, diff-binding full) måles mot råtellingene, aldri et
    flagg.
    """
    feil: list[str] = []
    m, oppsett = art.get("maalt"), art.get("oppsett")
    if not isinstance(m, dict) or not isinstance(oppsett, dict):
        return ["artefaktet mangler `maalt` og/eller `oppsett`"]

    injisert, f = _teller(oppsett, "oppsett.injisert_antall", "injisert_antall")
    if f:
        feil.append(f)
    elif injisert < grense["min_injisert"]:
        feil.append(f"injisert_antall={injisert},"
                    f" krever >= {grense['min_injisert']}")

    KONTRAKT = {"utvider", "forfatter_alene", "innsnevrer", "rebasering"}
    for navn, verdi in (("oppsett.kategorier", oppsett.get("kategorier")),
                        ("kategorier_dekket", m.get("kategorier_dekket"))):
        if not isinstance(verdi, list) or set(verdi) != KONTRAKT \
                or len(verdi) != len(KONTRAKT):
            feil.append(f"{navn}={verdi!r}, krever NØYAKTIG {sorted(KONTRAKT)}")

    # Hver kategori: andelen er 1.0 → EKSAKT `utfall == injisert` med
    # `injisert > 0`. `>= 1.0` alene godtar teller > nevner (umulig måling).
    for grp, utfallsfelt in (("utvider", "aktivert"),
                             ("forfatter_alene", "stoppet"),
                             ("innsnevrer", "aktivert"),
                             ("rebasering", "rebasert")):
        u = m.get(grp)
        if not isinstance(u, dict):
            feil.append(f"maalt.{grp} mangler")
            continue
        inj, f1 = _teller(u, f"{grp}.injisert", "injisert")
        ant, f2 = _teller(u, f"{grp}.{utfallsfelt}", utfallsfelt)
        if f1 or f2:
            feil.append(f1 or f2)
            continue
        if inj <= 0:
            feil.append(f"maalt.{grp}.injisert={inj} — veien ble aldri prøvd")
        elif ant != inj:
            feil.append(f"maalt.{grp}: {utfallsfelt}={ant} != injisert={inj}"
                        f" (andelen skal være 1.0)")

    # V10/V1: INGEN policy ender med mer enn én aktiv rad.
    flere, f = _teller(m, "maalt.policyer_med_flere_aktive",
                       "policyer_med_flere_aktive")
    if f:
        feil.append(f)
    elif flere > grense["maks_flere_aktive"]:
        feil.append(f"policyer_med_flere_aktive={flere},"
                    f" krever <= {grense['maks_flere_aktive']}")

    # V10: runtime MÅ nektes direkte skriving til `policyer`.
    nekt, f = _teller(m, "maalt.runtime_skrivenekt", "runtime_skrivenekt")
    if f:
        feil.append(f)
    elif nekt < grense["krev_runtime_skrivenekt"]:
        feil.append(f"runtime_skrivenekt={nekt},"
                    f" krever >= {grense['krev_runtime_skrivenekt']}"
                    " (runtime kunne skrive policyer direkte — V10 brutt)")

    # Godkjenneren attesterte DIFFEN: hver attestasjons diff_hash == rundens.
    treff, f1 = _teller(m, "maalt.diff_binding_treff", "diff_binding_treff")
    tot, f2 = _teller(m, "maalt.diff_binding_totalt", "diff_binding_totalt")
    if f1 or f2:
        feil.append(f1 or f2)
    elif grense.get("krev_diff_binding_full"):
        if tot <= 0:
            feil.append("diff_binding_totalt=0 — ingen attestasjon å binde")
        elif treff != tot:
            feil.append(f"diff_binding: treff={treff} != totalt={tot}"
                        " (en attestasjon bandt ikke diffen den så)")

    varighet, f = _positiv(m, "maalt.varighet_sek", "varighet_sek")
    if f:
        feil.append(f)
    elif varighet > grense["maks_varighet_sek"]:
        feil.append(f"varighet_sek={varighet:g},"
                    f" krever <= {grense['maks_varighet_sek']:g}")
    return feil


def _grenser_feilinjisering(grense: dict, art: dict) -> list[str]:
    """`feilinjisering-m01-v1` — de ni målene fra v1 §5 og v2-delta pkt. 8.

    Invariantene REGNES UT på nytt her, aldri lest ut av et flagg. Det er
    lærdommen fra PR #8 runde 3: `bestatt`, `en_til_en` og `routing_stemmer`
    er alle produsentens EGEN påstand, og en port som leser konklusjonen
    validerer ingenting. Her betyr det at `terminal_andel` sjekkes mot
    `terminal_antall / injisert_antall`, ikke bare mot 1.0.
    """
    feil: list[str] = []
    m, oppsett = art.get("maalt"), art.get("oppsett")
    if not isinstance(m, dict) or not isinstance(oppsett, dict):
        return ["artefaktet mangler `maalt` og/eller `oppsett`"]
    k = art.get("etterkontroll")
    if not isinstance(k, dict):
        return ["artefaktet mangler `etterkontroll`"]

    injisert, f = _teller(oppsett, "oppsett.injisert_antall", "injisert_antall")
    if f:
        feil.append(f)
    elif injisert < grense["min_injisert"]:
        feil.append(f"injisert_antall={injisert},"
                    f" krever >= {grense['min_injisert']}")

    kategorier = m.get("kategorier_dekket")
    if not isinstance(kategorier, list):
        feil.append(f"kategorier_dekket={kategorier!r} er ikke en liste")
    elif len(set(kategorier)) < grense["min_kategorier"]:
        feil.append(f"kategorier_dekket har {len(set(kategorier))} unike,"
                    f" krever >= {grense['min_kategorier']}")

    # Andelene mot tellingene. En andel oppgitt som 1.0 mens tellingene
    # sier noe annet, er to ulike kjøringer i samme fil.
    for andelsfelt, antallsfelt, nevnerfelt, krav in (
            ("terminal_andel", "terminal_antall", None,
             grense["krev_terminal_andel"]),
            ("lost_andel", "lost_antall", "reparerbare",
             grense["krev_lost_andel"]),
            ("manuell_andel", "manuell_antall", "ikke_reparerbare",
             grense["krev_manuell_andel"])):
        andel, f1 = _andel(m, andelsfelt, andelsfelt)
        antall, f2 = _teller(m, antallsfelt, antallsfelt)
        nevner, f3 = ((injisert, "") if nevnerfelt is None
                      else _teller(m, nevnerfelt, nevnerfelt))
        for melding in (f1, f2, f3):
            if melding:
                feil.append(melding)
        if f1 or f2 or f3:
            continue
        if andel < krav:
            feil.append(f"{andelsfelt}={andel:g}, krever >= {krav:g}")
        if nevner == 0:
            # 0/0 er ikke 1.0. Et testsett uten reparerbare saker beviser
            # ikke at reparerbare saker blir løst — det beviser at ingen
            # ble prøvd. Uten denne kunne artefaktet oppgitt
            # reparerbare=0, lost=0, andel=1.0 og bestått.
            if antall == 0 and krav > 0:
                feil.append(f"{andelsfelt}: nevneren ({nevnerfelt or 'injisert'})"
                            f" er 0 — andelen er udefinert, ikke oppfylt")
            continue
        utregnet = antall / nevner
        if abs(utregnet - andel) > 1e-9:
            feil.append(f"{andelsfelt}={andel:g} stemmer ikke med"
                        f" {antallsfelt}/{nevnerfelt or 'injisert_antall'}"
                        f" = {antall}/{nevner} = {utregnet:g}")

    varighet, f = _positiv(m, "maalt.varighet_sek", "varighet_sek")
    if f:
        feil.append(f)
    elif varighet > grense["maks_varighet_sek"]:
        feil.append(f"varighet_sek={varighet:g},"
                    f" krever <= {grense['maks_varighet_sek']:g}")

    p95, f = _positiv(m, "p95_api_under_last_ms", "p95_api_under_last_ms")
    if f:
        feil.append(f)
    elif p95 >= grense["maks_p95_api_under_last_ms"]:
        feil.append(f"p95_api_under_last_ms={p95:g}, krever <"
                    f" {grense['maks_p95_api_under_last_ms']:g} — målt MENS"
                    " arbeideren kjører, ellers beviser tallet ingenting om"
                    " prosessisolasjonen")

    reclaim, f = _teller(m, "lease_tap_re_claim", "lease_tap_re_claim")
    if f:
        feil.append(f)
    elif reclaim < grense["min_lease_tap_re_claim"]:
        feil.append(f"lease_tap_re_claim={reclaim}, krever >="
                    f" {grense['min_lease_tap_re_claim']} — gjenopptaksveien"
                    " må være KJØRT, ikke bare implementert")

    if k.get("historikk_komplett") is not True:
        feil.append("etterkontroll: historikk_komplett er ikke true")
    if k.get("klartekst_payload_funnet") is not False:
        feil.append("etterkontroll: klartekst_payload_funnet er ikke false")
    if k.get("eiermodul_kun_api") is not True:
        feil.append("etterkontroll: eiermodul_kun_api er ikke true")
    canary = k.get("canary_verdier")
    if not isinstance(canary, list) or not canary:
        # Et grep uten kjente kanarifugler beviser bare at grep-mønsteret
        # ikke traff noe — ikke at klarteksten ikke var der (v2-delta pkt. 8).
        feil.append("etterkontroll: canary_verdier mangler — et grep uten"
                    " kjente verdier beviser ingenting om klartekst")

    # PID-ene er separat-prosess-beviset. Er de like, kjørte arbeideren
    # inne i API-prosessen, og hele arkitekturbeslutningen fra §0 er brutt
    # uten at noe annet tall ville avslørt det.
    api_pid, f1 = _teller(oppsett, "oppsett.api_pid", "api_pid")
    m37_pid, f2 = _teller(oppsett, "oppsett.m37_pid", "m37_pid")
    for melding in (f1, f2):
        if melding:
            feil.append(melding)
    if not f1 and not f2 and api_pid == m37_pid:
        feil.append(f"api_pid == m37_pid ({api_pid}) — arbeideren kjørte i"
                    " API-prosessen, i strid med PR-006 §0")

    fordeling = k.get("status_fordeling")
    if not isinstance(fordeling, dict):
        feil.append("etterkontroll mangler `status_fordeling`")
    else:
        sum_alle, gyldig = 0, True
        for status in sorted(fordeling):
            verdi, melding = _teller(fordeling, f"status_fordeling.{status}",
                                     status)
            if melding:
                feil.append(melding)
                gyldig = False
            else:
                sum_alle += verdi
        if gyldig and injisert is not None and sum_alle != injisert:
            feil.append(f"summen av status_fordeling ({sum_alle}) !="
                        f" injisert_antall ({injisert})"
                        f" — fordeling: {dict(sorted(fordeling.items()))}")
        # Terminal er `løst|avvist|manuell` og INGENTING annet.
        # `venter_utførelse` er en sak som venter på en kvittering, og en
        # sak som venter er ikke en sak som er behandlet ferdig.
        terminale = sum(fordeling.get(s, 0) for s in
                        ("løst", "avvist", "manuell")
                        if isinstance(fordeling.get(s), int)
                        and not isinstance(fordeling.get(s), bool))
        terminal_antall = m.get("terminal_antall")
        if isinstance(terminal_antall, int) \
                and not isinstance(terminal_antall, bool) \
                and terminale != terminal_antall:
            feil.append(f"terminal_antall={terminal_antall} != summen av"
                        f" løst/avvist/manuell i status_fordeling ({terminale})")
    return feil


def _grenser_rollback(grense: dict, art: dict) -> list[str]:
    """`rollback-m01-v1` — grensene som har manglet siden PR-005c.

    Den bindende delen er ikke tidene, men `halvferdige_transaksjoner = 0`
    og at radtellingene for de ANDRE tabellene er uendret. En rollback som
    er rask og etterlater en halv transaksjon er verre enn en treg som
    ikke gjør det.
    """
    feil: list[str] = []
    m, k = art.get("maalt"), art.get("etterkontroll")
    if not isinstance(m, dict) or not isinstance(k, dict):
        return ["artefaktet mangler `maalt` og/eller `etterkontroll`"]

    for felt, tak in (("deaktivering_effektiv_s", grense["maks_deaktivering_s"]),
                      ("reaktivering_effektiv_s", grense["maks_reaktivering_s"])):
        verdi, melding = _positiv(m, felt, felt)
        if melding:
            feil.append(melding)
        elif verdi > tak:
            feil.append(f"{felt}={verdi:g}, krever <= {tak:g}")

    for felt, tak in (("tapte_loggposter", grense["maks_tapte_loggposter"]),
                      ("halvferdige_transaksjoner", grense["maks_halvferdige"])):
        verdi, melding = _teller(m, felt, felt)
        if melding:
            feil.append(melding)
        elif verdi > tak:
            feil.append(f"{felt}={verdi}, krever <= {tak}")

    # ANDELEN REGNES UT PÅ NYTT FRA RÅTALLENE (Codex P1, runde 6).
    #
    # Porten leste tidligere bare det ferdigregnede tallet. Da besto blant
    # annet disse umulige artefaktene:
    #   requests_under_rollback=113, avviste_requests=1,  andel=1.0
    #   requests_under_rollback=0,   avviste_requests=0,  andel=1.0
    # Begge er «>= 1.0» og ingen av dem kan oppstå i en ekte kjøring.
    # Samme prinsipp som `bestatt` fra PR #8: når råtallene ligger i
    # artefaktet, er produsentens konklusjon ikke beviset.
    oppsett = art.get("oppsett")
    n, f1 = _teller(oppsett, "oppsett.requests_under_rollback",
                    "requests_under_rollback")
    avvist, f2 = _teller(m, "avviste_requests", "avviste_requests")
    andel, f3 = _andel(m, "paagaaende_requests_korrekt_avvist",
                       "paagaaende_requests_korrekt_avvist")
    for melding in (f1, f2, f3):
        if melding:
            feil.append(melding)
    if not (f1 or f2 or f3):
        if n == 0:
            # 0/0 er ikke 1.0. En rollback uten en eneste forespørsel i
            # av-vinduet beviser ikke at forespørsler blir avvist — den
            # beviser at ingen ble prøvd. Samme regel som for
            # `reparerbare = 0` i feilinjiseringen.
            feil.append("requests_under_rollback=0 — en rollback uten"
                        " trafikk i av-vinduet beviser ingen avvisning")
        elif avvist > n:
            feil.append(f"avviste_requests={avvist} >"
                        f" requests_under_rollback={n} — flere avvisninger"
                        " enn forespørsler")
        else:
            # Toleransen er halvparten av siste siffer produsenten runder
            # til (6 desimaler). Eksakt likhet ville gjort 1/3 umulig å
            # rapportere; en større slingring ville gjort kontrollen
            # meningsløs.
            faktisk = avvist / n
            if abs(andel - faktisk) > 5e-7:
                feil.append(
                    f"paagaaende_requests_korrekt_avvist={andel:g} stemmer"
                    f" ikke med {avvist}/{n}={faktisk:.6f} — andelen er"
                    " regnet ut, ikke lest")
            elif faktisk < grense["krev_avvist_andel"]:
                feil.append(f"paagaaende_requests_korrekt_avvist={faktisk:g},"
                            f" krever >= {grense['krev_avvist_andel']:g}")

    # AVVISNINGSKODEN er en del av kontrakten, ikke en fritekstetikett.
    # Skjemaet låser den også (`const`), men gaten kontrollerer den selv:
    # `_sjekk_grenser` kalles også uten formatvalidering, og en kontrakt som
    # bare håndheves ett sted er håndhevet i ett tilfelle.
    if m.get("avvisningskode") != "modul_inaktiv":
        feil.append(f"avvisningskode={m.get('avvisningskode')!r} —"
                    " kontrakten er 503 `modul_inaktiv`, og et artefakt kan"
                    " ikke bevise den med en annen kode")

    if k.get("andre_tabeller_uendret") is not True:
        feil.append("etterkontroll: andre_tabeller_uendret er ikke true")

    # Flagget over er produsentens påstand. Tallene er beviset — og de
    # sammenlignes her, ikke bare oppgis.
    for_, etter = k.get("radtelling_for"), k.get("radtelling_etter")
    if not isinstance(for_, dict) or not isinstance(etter, dict):
        feil.append("etterkontroll mangler radtelling_for/radtelling_etter")
    elif sorted(for_) != sorted(etter):
        feil.append(f"radtellingene dekker ulike tabeller: {sorted(for_)}"
                    f" vs {sorted(etter)}")
    else:
        for tabell in sorted(for_):
            a, f1 = _teller(for_, f"radtelling_for.{tabell}", tabell)
            b, f2 = _teller(etter, f"radtelling_etter.{tabell}", tabell)
            if f1 or f2:
                feil += [x for x in (f1, f2) if x]
            elif a != b:
                feil.append(f"{tabell}: {a} rader før, {b} etter —"
                            " rollbacken rørte en tabell den ikke skulle")
    return feil


def _slaa_opp(art: object, sti: str):
    """Følg en punktseparert sti inn i artefaktet. -> (verdi, funnet).

    Bevisst enkel: bare oppslag i objekter. En sti som må indeksere lister
    eller gjøre betingede valg for å treffe, er ikke en peker til én måling
    — den er et lite program, og et program i et manifest kan ikke leses av
    den som skal etterprøve påstanden.
    """
    node = art
    for ledd in sti.split("."):
        if not isinstance(node, dict) or ledd not in node:
            return None, False
        node = node[ledd]
    return node, True


def _bevismaalinger_finnes(art: dict, punkt: dict, navn: str) -> list[str]:
    """Hver oppgitte måling må FINNES i artefaktet (Codex P1, PR #15).

    Delingsregelen i RUTINER.md krever at et manifest navngir hvilken måling
    i et delt artefakt som beviser punktet for nettopp den modulen. Den ble
    først håndhevet som «notatet er ikke tomt og ikke identisk med naboens»
    — og det består av `notat: "banan_maaling = true"`. Ikke-tom og unik
    fritekst er ingen binding til data.

    Dette beviser ikke at målingen er RELEVANT for modulen; det er
    reviewansvar. Det beviser at den påberopte målingen finnes i evidensen,
    og det er minstekravet en maskin kan og skal holde.
    """
    stier = punkt.get("bevismaalinger")
    if not isinstance(stier, list) or not stier:
        return [f"{navn}: peker på et artefakt uten å navngi hvilken måling"
                f" som beviser punktet (`bevismaalinger`)"]
    feil = []
    for sti in stier:
        if not isinstance(sti, str):
            feil.append(f"{navn}: bevismaaling {sti!r} er ikke en streng")
            continue
        _, funnet = _slaa_opp(art, sti)
        if not funnet:
            feil.append(f"{navn}: bevismaaling '{sti}' finnes ikke i"
                        f" artefaktet — en påstand om en måling som ikke er"
                        f" der, er ikke evidens")
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
        feil += [f"{navn}: format — {m}"
                 for m in valider_artefaktformat(data, krav_id)]
        feil += [f"{navn}: {m}" for m in _sjekk_grenser(krav_id, data)]
        # TREDJE LAG: hvilken måling manifestet PÅBEROPER SEG (PR #15).
        #
        # De to over spør om artefaktet er gyldig og består grensene — det
        # samme svaret for alle moduler som deler filen. Dette laget spør
        # hva NETTOPP DETTE manifestet henter ut av den, og det er den
        # eneste kontrollen som skiller legitim deling fra en lånt
        # konklusjon. Kjøres etter hashkontrollen, så stien slås opp i en
        # fil vi har bevist er den manifestet mener.
        feil += _bevismaalinger_finnes(data, p, navn)
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
