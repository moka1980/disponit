"""Formell policyvalidering v0.2 (ChatGPT-review PR-001, funn B og spm. 3).

To lag:
  1. JSON Schema (policies/policy-schema-v0.2.json) med
     additionalProperties: false — datatyper, enums, mønstre, påkrevde felt.
  2. Semantiske kontroller skjemaspråket ikke dekker: tidssone finnes i
     IANA-databasen, rolle-/dataklasse-/verifikator-referanser er gyldige,
     unike handlings-IDer, irreversible handlinger har harde rammer.

Alt er kontrollert: funksjonen kaster ALDRI — feilformet policy gir
feilliste, ikke exception (review: «AttributeError i validatoren»).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import jsonschema

_SKJEMA_STI = Path(__file__).resolve().parents[3] / "policies" / "policy-schema-v0.2.json"


# --------------------------------------------------------------------------
# Mønstre måles med ECMA-262-ankre, ikke Pythons (Codex P2).
# --------------------------------------------------------------------------
# JSON Schema definerer `pattern` som ECMA-262, og der betyr `$` — uten
# `m`-flagget — slutten på strengen. Pythons `re` gjør det ikke: `$` matcher
# OGSÅ rett før en avsluttende linjeskift. `jsonschema` kjører mønstrene
# gjennom `re`, så hele skjemaet arvet den lekkasjen: `"1.2.3\n"` matchet
# `^\d+\.\d+\.\d+$` og var fullt skjemagyldig.
#
# Halen kom ikke gratis. Databasen leser `$` som ekte slutt (migrasjon 020–025
# bruker samme form), så utkastet ble FRYST og attestert her, og bruddet dukket
# opp først inne i `aktiver_policy` — der runden ble kansellert som
# `versjon_i_bruk`: feil beskjed, og på et tidspunkt der `meta.versjon` ikke
# lenger kan rettes. Det samme gjelder de andre mønstrene, hver på sin måte:
# en `handlinger[].id` med hale er skjemagyldig, men `engine.les_policyref`
# avviser den som uleselig policyreferanse, altså evidens uten identitet.
#
# Derfor oversettes ankrene før mønsteret kompileres: `^` → `\A`, `$` → `\Z`.
# Det er ECMA-semantikken bokstavelig, ikke en innstramming vi finner på — og
# den gjelder ALLE skjemaets mønstre, så ingen av dem kan glemmes.
#
# MEN den hører hjemme i INNFØRINGSkontrakten, ikke lastekontrakten (se
# `valider_ny_policy`). Lastekontrakten revalideres av `hent_aktiv` ved hver
# eneste forespørsel, og en policy med hale i en `handlinger[].id` KAN være
# aktiv i dag: skjemaet slapp den gjennom, og bare `meta.policy_id`/
# `meta.versjon` har en DB-kontroll som fanget den. Strammet vi lastekontrakten,
# ville en slik tenant mistet alle policystyrte beslutninger i det utrullingen
# lander — `PolicyKorrupt`, uten sjanse til å aktivere en rettet versjon.
# Kravet gjelder derfor FRAMOVER: den gamle policyen leses og virker som før,
# men neste versjon må rette halen for å slippe inn.
def _ecma_ankre(monster: str) -> str:
    """Skjemaets ECMA-mønster med ankre Python leser likt: `^`→`\\A`, `$`→`\\Z`.

    Escapede tegn (`\\$`) og tegnklasser (`[^a]`, `[$]`) røres ikke — der er
    `^`/`$` ikke ankre, og en blind erstatning ville endret hva mønsteret
    matcher.
    """
    ut: list[str] = []
    i, n, i_klasse = 0, len(monster), False
    while i < n:
        c = monster[i]
        if c == "\\" and i + 1 < n:
            ut.append(monster[i:i + 2])
            i += 2
            continue
        if i_klasse:
            if c == "]":
                i_klasse = False
        elif c == "[":
            i_klasse = True
        elif c == "^":
            c = r"\A"
        elif c == "$":
            c = r"\Z"
        ut.append(c)
        i += 1
    return "".join(ut)


#: Mønstrene kommer fra skjemafilen, altså en lukket mengde — cachen vokser ikke
#: med trafikken. Samme grunn som validatorcachen under: revalidering skjer per
#: forespørsel, og rekompilering per mønster ville kostet der.
_MONSTER_CACHE: dict[str, re.Pattern] = {}


def _strengt_monster(monster: str) -> re.Pattern:
    kompilert = _MONSTER_CACHE.get(monster)
    if kompilert is None:
        kompilert = re.compile(_ecma_ankre(monster))
        _MONSTER_CACHE[monster] = kompilert
    return kompilert


def _pattern_ecma(validator, monster, instans, skjema):
    """`pattern`-nøkkelordet som KUN DIFFERANSEN mot lastekontrakten: strengen
    matcher Pythons lesning av mønsteret, men ikke ECMA-262 sin.

    Differansen, ikke hele kontrollen. En streng som feiler BEGGE lesningene —
    `handlinger[].id` = `'h1'`, som mangler punktumet mønsteret krever — er en
    helt vanlig skjemafeil, og lastekontrakten sier alt fra om den. Rapporterte
    vi den her også, ville `valider_ny_policy` meldt den to ganger, og
    `_krev_innforingskrav` (som kjører denne ALENE, på et frosset utkast) ville
    kansellert runden med «bryter et nytt krav» for et dokument som ganske
    enkelt er strukturelt ødelagt — nøyaktig sammenblandingen kontrakten ble
    delt i to for å unngå.

    Feilteksten er `jsonschema`s egen, så feillistene kallerne leser ser
    uendret ut."""
    if not validator.is_type(instans, "string"):
        return
    if re.search(monster, instans) \
            and not _strengt_monster(monster).search(instans):
        yield jsonschema.ValidationError(
            f"{instans!r} does not match {monster!r}")


#: Skjemaet målt med ECMA-ankre. Brukes KUN av innføringskontrakten; alt annet
#: går gjennom `Draft202012Validator` som før.
_StrengValidator = jsonschema.validators.extend(
    jsonschema.Draft202012Validator, {"pattern": _pattern_ecma})

# Validatoren bygges ÉN gang per skjemaversjon, ikke per kall.
#
# Målt på PR-005b-lasttesten: `valider_policy` brukte ~20 ms median, og
# nesten alt var fillesing + rekompilering av JSON Schema-validatoren.
# API-veien revaliderer policyen ved HVER forespørsel (v2 1.5, fail-closed
# mot DB-korrupsjon), så 20 ms per request ble ~1,6 CPU-sekunder per sekund
# ved ytelseskravet på 100/s — mer enn hele Cloud Server S sine 2 vCPU, på
# revalideringen alene.
#
# Merk hva som IKKE caches: POLICYEN. Den leses fortsatt fra databasen og
# valideres på nytt for hver eneste forespørsel — kontrakten er uendret.
# Det som caches er selve skjemaet, som er kode og følger utrullingen.
# Nøkkelen inkluderer mtime og størrelse, så en endret skjemafil plukkes
# opp uten omstart og cachen aldri kan servere et utdatert skjema.
#
# Nøkkelen bærer også validatorKLASSEN: last- og innføringskontrakten leser
# samme skjemafil, men måler mønstrene ulikt (se `_pattern_ecma`), og to
# instanser med samme nøkkel ville servert feil av dem til én av kallerne.
_VALIDATOR_CACHE: dict[tuple, object] = {}


def _last_skjema() -> dict:
    return json.loads(_SKJEMA_STI.read_text(encoding="utf-8"))


def _validator(klasse=jsonschema.Draft202012Validator):
    try:
        st = _SKJEMA_STI.stat()
        nokkel = (str(_SKJEMA_STI), st.st_mtime_ns, st.st_size, klasse)
    except OSError:
        nokkel = (str(_SKJEMA_STI), None, None, klasse)
    v = _VALIDATOR_CACHE.get(nokkel)
    if v is None:
        v = klasse(_last_skjema())
        # Kun ÉN skjemaversjon om gangen — men begge kontraktenes validatorer
        # for den versjonen. Filens mtime/størrelse er lik i begge nøklene, så
        # en endret skjemafil tømmer fortsatt hele cachen.
        for gammel in [k for k in _VALIDATOR_CACHE if k[:3] != nokkel[:3]]:
            del _VALIDATOR_CACHE[gammel]
        _VALIDATOR_CACHE[nokkel] = v
    return v


def valider_policy(policy: object) -> list[str]:
    """LASTEKONTRAKTEN. Returnerer komplett feilliste. Tom == gyldig. Kaster aldri.

    Dette er kravet en policy må oppfylle for å kunne TOLKES — og det er den
    `hent_aktiv` revaliderer mot ved hver eneste forespørsel (v2 1.5). Derfor
    er den bakoverkompatibel: en policy som var gyldig den dagen den ble
    aktivert, må fortsatt være gyldig i dag. Nye krav hører hjemme i
    `valider_ny_policy`.
    """
    try:
        return _valider(policy)
    except Exception as e:  # siste skanse — aldri ukontrollert exception
        return [f"intern valideringsfeil ({type(e).__name__}): {e}"]


#: Skilletegnene i den flate diffstien (`policydiff._flat`):
#: `verifikatorer.<id>.<felt>` skjøtes med punktum, lister med klammer.
_DIFFSTI_SKILLETEGN = (".", "[")


def valider_ny_policy(policy: object) -> list[str]:
    """INNFØRINGSKONTRAKTEN: lastekontrakten + krav som bare gjelder policyer
    som skal INN (registrering, utkastvalidering, malene vi selv leverer).

    Hvorfor to kontrakter i stedet for én innstramming i skjemaet (Codex P1 på
    PR #63): skjemaet er også lastekontrakten, og `hent_aktiv` revaliderer den
    LAGREDE policyen mot det ved hver forespørsel. Strammer vi skjemaet, blir
    enhver allerede aktiv policy som brøt det nye kravet `PolicyKorrupt` i det
    utrullingen lander — tenanten mister alle policystyrte beslutninger
    umiddelbart, uten å få sjansen til å aktivere en rettet versjon. Et krav
    som først oppstår i dag kan derfor bare gjelde FRAMOVER: den gamle
    policyen leses og virker som før, men neste versjon må rette id-en for å
    slippe inn. Å migrere lagrede rader er ikke et alternativ — en verifikator-
    id-endring er en semantisk policyendring som skal gjennom fire-øyne-veien
    som alt annet, ikke en skjult skriving i en utrulling.

    Rekkefølgen er bevisst: er lastekontrakten brutt, returneres KUN den.
    Kravene under forutsetter at strukturen er på plass.
    """
    feil = valider_policy(policy)
    if feil:
        return feil
    return valider_innforingskrav(policy)


def valider_innforingskrav(policy: object) -> list[str]:
    """KUN differansen mellom innførings- og lastekontrakten: de framoverrettede
    kravene, uten lastekontrakten foran. Tom == oppfylt. Kaster aldri.

    Hvorfor den er eksponert alene (Codex P2 på PR #63): `valider_utkast` er en
    ENGANGS-port, og et utkast som fikk status `validert` før et slikt krav
    fantes bærer statusen videre inn i aktiveringen. Aktiveringsveien må derfor
    kunne stille kravet på nytt — men BARE dette kravet. Å kjøre hele
    `valider_ny_policy` der ville dratt lastekontrakten inn i en kontroll som
    handler om noe annet, og gjort «utkastet bryter et nytt krav» umulig å
    skille fra «utkastet er strukturelt ødelagt».

    To krav bor her nå: entydig verifikator-id, og mønstre målt med ECMA-ankre
    (Codex P2 — Pythons `$` godtar en avsluttende linjeskift, databasens gjør
    det ikke). Begge har samme form: de gjelder FRAMOVER, fordi en alt aktiv
    policy som bryter dem må fortsette å virke.
    """
    if not isinstance(policy, dict):
        return ["policy er ikke et objekt"]
    try:
        return _valider_innforing(policy)
    except Exception as e:  # siste skanse — aldri ukontrollert exception
        return [f"intern valideringsfeil ({type(e).__name__}): {e}"]


class ValideringUtilgjengelig(RuntimeError):
    """Validatoren kunne ikke KJØRE — det er ikke en dom over policyen.

    Skjemafilen kan mangle eller være uleselig i en halvlandet utrulling. Da
    vet vi ingenting om innholdet, og «utkastet bryter et krav» er en påstand
    vi ikke har dekning for.
    """


def valider_innforingskrav_strengt(policy: object) -> list[str]:
    """Som `valider_innforingskrav`, men SKILLER intern svikt fra innholdsbrudd:
    en feil som ikke er policyens skyld kastes som `ValideringUtilgjengelig`.

    Hvorfor begge finnes (Codex P2 på PR #64). `valider_innforingskrav` sluker
    alt og legger «intern valideringsfeil» i feillista, og det er riktig der den
    brukes som en ren rapport — en lesesti skal aldri kunne velte på en
    validator. Men den som bruker svaret til å ta en IRREVERSIBEL avgjørelse
    trenger forskjellen: `attester_aktivering` kansellerer runden på et
    innholdsbrudd, fordi det frosne dokumentet aldri kan rettes. En manglende
    skjemafil er derimot reparerbar — og en runde som ble kansellert mens
    utrullingen var halvveis, kommer ikke tilbake når filen gjør det.

    Kaster altså for det som er VÅR feil, og returnerer bare det som er
    policyens.
    """
    if not isinstance(policy, dict):
        return ["policy er ikke et objekt"]
    try:
        return _valider_innforing(policy)
    except Exception as e:
        raise ValideringUtilgjengelig(f"{type(e).__name__}: {e}") from e


def _valider_innforing(policy: dict) -> list[str]:
    # Mønstrene måles med ECMA-ankre (se `_pattern_ecma`): `$` er slutten på
    # strengen, ikke «slutten, eller rett før en avsluttende linjeskift» som
    # Pythons `re` leser den. Nøkkelordet gir alt KUN differansen mot
    # lastekontrakten; filteret her tar resten av skjemaet (type, required,
    # additionalProperties), som er lastekontraktens ansvar og som
    # `valider_ny_policy` alt har kjørt.
    feil: list[str] = [
        f"skjema: {'/'.join(str(p) for p in e.absolute_path) or '<rot>'}: "
        f"{e.message}"
        for e in _validator(_StrengValidator).iter_errors(policy)
        if e.validator == "pattern"]
    # Verifikator-id-en er den ENESTE frie nøkkelen i en ellers lukket policy,
    # og den havner UTOLKET i diffstien godkjenneren attesterer. Med id-ene
    # `foo` og `foo.beskrivelse` er `verifikatorer.foo.beskrivelse` både
    # beskrivelsen til `foo` og roten til den andre verifikatoren — og
    # godkjenneren kan tilskrive en tillitsendring FEIL verifikator.
    # Skilletegnene forbys derfor i id-en i stedet for å gjettes ut av stien i
    # etterkant. Bevisst minimal: bare de to tegnene som skaper flertydigheten,
    # ikke husmønsteret `^[a-z0-9_]+$`, så id-er som allerede gir en entydig
    # diff fortsatt slipper gjennom. Tom id er også ute: den gir stien
    # `verifikatorer.` og et blad uten eier.
    for felt in ("verifikatorer", "verifikator_prioritet"):
        for vid in policy.get(felt) or {}:
            if not isinstance(vid, str):
                continue                            # skjemaet har alt sagt fra
            if not vid:
                feil.append(f"{felt}: tom verifikator-id gir ingen entydig"
                            " diffsti")
            elif any(t in vid for t in _DIFFSTI_SKILLETEGN):
                feil.append(
                    f"{felt}: verifikator-id '{vid}' inneholder skilletegn fra"
                    " diffstien (. eller [) og gjør stien flertydig")
    feil += _overstyring_kan_anvendes(policy)
    return sorted(feil)


def _overstyring_kan_anvendes(policy: dict) -> list[str]:
    """En `godkjennbare`-oppføring motoren ikke KAN anvende (Codex P1).

    `IKKE_MENNESKELIG_GODKJENNBARE` er deny-siden: grunnkoder et menneske
    aldri SKAL få godkjenne. Dette er den andre siden — grunnkoder motoren
    ikke KAN løfte. `_loft_policy` uttrykker bare `belop_maks` og `valuta`
    (`engine.LOFTBARE_GRUNNKODER`); alt annet gir None, og da ender HVER
    godkjenning i STOPP. En slik oppføring passerte lastekontrakten fint,
    så eier kunne aktivere det som så ut som en konfigurert menneskelig
    overstyring — og aldri se den virke, uten at noe sa fra. Feltet
    grunnkoden krever måles samme sted og av samme grunn: et løft uten
    verdien å løfte TIL er like virkningsløst som en ikke-løftbar kode.

    At verdien FINNES er heller ikke nok (Codex P1, runde 7): den må ligge
    slik at det finnes et blokkert utfall løftet faktisk flytter. Et
    `belop_maks` som ikke er HØYERE enn handlingens egen grense løfter
    ingenting — hvert beløp som utløste `belop_over_grense` er per
    definisjon over den grensen, altså også over overstyringens tak, og
    steg 7 i `_anvend_menneskelig_godkjenning` stopper det. En `valuta`
    handlingen ALT tillater er samme sak fra den andre siden: en hendelse
    blokkert på `valuta_ikke_tillatt` bærer nødvendigvis en annen valuta,
    og steg 7 krever likhet. Verdien måles derfor mot handlingen
    oppføringen peker på, ikke bare mot sitt eget nærvær.

    Kravet bor i INNFØRINGSkontrakten, ikke i lastekontrakten: en policy
    som allerede er aktiv med en slik oppføring virker som før (den ene
    overstyringen har aldri gjort noe), og skal ikke bli `PolicyKorrupt`
    ved lasting i det utrullingen lander. Neste versjon må rette den.
    """
    from .engine import LOFTBARE_GRUNNKODER
    mo = policy.get("menneskelig_overstyring")
    if not isinstance(mo, dict):
        return []
    feil: list[str] = []
    lovlige = ", ".join(sorted(LOFTBARE_GRUNNKODER))
    handlinger = {h["id"]: h for h in policy.get("handlinger") or []
                  if isinstance(h, dict) and isinstance(h.get("id"), str)}
    for i, e in enumerate(mo.get("godkjennbare") or []):
        if not isinstance(e, dict) or not isinstance(e.get("grunnkode"), str):
            continue                     # lastekontrakten har alt sagt fra
        gk = e["grunnkode"]
        krav = LOFTBARE_GRUNNKODER.get(gk)
        if krav is None:
            feil.append(
                f"menneskelig_overstyring[{i}]: grunnkode '{gk}' kan ikke"
                " løftes av motoren — en godkjenning ville alltid endt i"
                f" STOPP. Løftbare grunnkoder: {lovlige}")
        elif e.get(krav) is None:
            feil.append(
                f"menneskelig_overstyring[{i}]: grunnkode '{gk}' krever"
                f" '{krav}' i oppføringen — uten en verdi å løfte til kan"
                " motoren ikke bygge løftet, og godkjenningen ender i STOPP")
        else:
            feil += _loftet_flytter_noe(i, gk, e, handlinger.get(e.get("handling")))
    return feil


#: Grunnkodene `_loftet_flytter_noe` faktisk MÅLER mot handlingen. Skal til
#: enhver tid være hele `engine.LOFTBARE_GRUNNKODER` — en ny løftbar kode uten
#: en gren der er en stille fail-open, altså nøyaktig hullet runde 7 lukket.
#: `test_hver_loftbar_grunnkode_maales_mot_handlingen` er vakten.
ANVENDBARHET_MALT = frozenset({"belop_over_grense", "valuta_ikke_tillatt"})


def _loftet_flytter_noe(i: int, gk: str, e: dict, h: dict | None) -> list[str]:
    """Verdien i oppføringen målt mot handlingen den peker på.

    Ukjent handling sier lastekontrakten (`_valider`) alt fra om; her ville
    den bare blitt en andre stemme om det samme, så da måler vi ingenting.
    Det samme gjelder en uleselig grense: `belop_ugyldig`/
    `policy_belopsgrense_ugyldig` er andres dom, og en verdi vi ikke kan
    lese kan vi ikke påstå noe om.
    """
    from .engine import parse_belop
    if h is None:
        return []
    grenser = h.get("grenser") if isinstance(h.get("grenser"), dict) else {}
    hid = h["id"]
    feil: list[str] = []
    if gk == "belop_over_grense":
        hgrense = grenser.get("belop_maks")
        if hgrense is None:
            # Uten en beløpsgrense på handlingen kan `belop_over_grense`
            # aldri oppstå for den — oppføringen venter på et utfall som
            # ikke finnes.
            return [f"menneskelig_overstyring[{i}]: handling '{hid}' har ingen"
                    " 'grenser.belop_maks', så 'belop_over_grense' kan aldri"
                    " oppstå for den og overstyringen kan aldri anvendes"]
        hmaks, emaks = parse_belop(hgrense), parse_belop(e["belop_maks"])
        if hmaks is not None and emaks is not None and emaks <= hmaks:
            feil.append(
                f"menneskelig_overstyring[{i}]: 'belop_maks' {emaks} er ikke"
                f" høyere enn grensen {hmaks} på handling '{hid}' — hvert"
                " beløp som utløser 'belop_over_grense' er over den grensen,"
                " altså også over overstyringens tak, og godkjenningen ender"
                " i STOPP")
        # `belop_maks` drar `valuta` med seg (dependentRequired), og steg 7
        # krever at hendelsens valuta ER den. Er ikke DEN valutaen tillatt
        # for handlingen, stopper den gjenopptatte evalueringen på valuta i
        # stedet — løftet hevet jo bare beløpet.
        lovlige_v = grenser.get("valuta")
        v = e.get("valuta")
        if isinstance(lovlige_v, list) and lovlige_v and v is not None \
                and v not in lovlige_v:
            feil.append(
                f"menneskelig_overstyring[{i}]: valutaen '{v}' er ikke tillatt"
                f" for handling '{hid}' ({', '.join(map(str, lovlige_v))}) —"
                " løftet hever beløpet, ikke valutaen, så evalueringen"
                " stopper på 'valuta_ikke_tillatt' uansett")
    elif gk == "valuta_ikke_tillatt":
        lovlige_v = grenser.get("valuta")
        if not isinstance(lovlige_v, list) or not lovlige_v:
            return [f"menneskelig_overstyring[{i}]: handling '{hid}' har ingen"
                    " 'grenser.valuta', så 'valuta_ikke_tillatt' kan aldri"
                    " oppstå for den og overstyringen kan aldri anvendes"]
        if e["valuta"] in lovlige_v:
            feil.append(
                f"menneskelig_overstyring[{i}]: valutaen '{e['valuta']}' er"
                f" allerede tillatt for handling '{hid}' — en hendelse som"
                " blokkeres på 'valuta_ikke_tillatt' bærer en ANNEN valuta,"
                " og godkjenningen ender i STOPP")
    return feil


def _valider(policy: object) -> list[str]:
    if not isinstance(policy, dict):
        return ["policy er ikke et objekt"]

    # Lag 1: formelt JSON Schema — samle ALLE brudd, ikke bare første
    validator = _validator()
    feil = [f"skjema: {'/'.join(str(p) for p in e.absolute_path) or '<rot>'}: "
            f"{e.message}" for e in validator.iter_errors(policy)]
    if feil:
        return sorted(feil)  # strukturfeil først; semantikk krever gyldig struktur

    # Lag 2: semantikk
    try:
        ZoneInfo(policy["tidssone"])
    except Exception:
        feil.append(f"tidssone: '{policy['tidssone']}' finnes ikke i IANA-databasen")

    roller = {r["id"] for r in policy["roller"]}
    klasser = set(policy["dataklasser"])
    verifikatorer = policy["verifikatorer"]

    sett: set[str] = set()
    for h in policy["handlinger"]:
        hid = h["id"]
        if hid in sett:
            feil.append(f"handling '{hid}': duplisert id")
        sett.add(hid)
        for rolle in h.get("tillatt_for") or []:
            if rolle not in roller:
                feil.append(f"handling '{hid}': ukjent rolle '{rolle}'")
        for k in h.get("dataklasser_tillatt") or []:
            if k not in klasser:
                feil.append(f"handling '{hid}': ukjent dataklasse '{k}'")
        for vk in h.get("vilkaar") or []:
            vid = vk["verifikator"]
            if vid not in verifikatorer:
                feil.append(f"handling '{hid}': vilkår '{vk['navn']}' peker på "
                            f"uregistrert verifikator '{vid}'")
            elif vk["navn"] not in verifikatorer[vid]["betrodd_for"]:
                feil.append(f"handling '{hid}': verifikator '{vid}' er ikke "
                            f"betrodd for vilkår '{vk['navn']}'")
        if h["reversering"]["type"] == "irreversibel":
            if not (h.get("grenser") or h.get("vilkaar")):
                feil.append(f"handling '{hid}': irreversibel uten grenser/vilkår")
            # En irreversibel handling som KAN utføres automatisk må ha minst
            # ett vilkår. Grunnen er replay-vernet: jti-ene API-veien
            # konsumerer kommer fra attestasjonene, og en attestasjon finnes
            # bare fordi et vilkår krever den (`engine.py` → attestasjon_mangler).
            # Uten vilkår er `jti_liste` tom (`kjerne.py`), ingen jti
            # konsumeres, og den irreversible handlingen kan spilles av på
            # nytt — mens grensene alene bare begrenser hvor stor hver enkelt
            # avspilling er. `alltid_stopp` trenger ikke vilkåret: den
            # utføres aldri automatisk, så det finnes ingen avspilling å verne.
            elif h["modus"] in ("auto", "auto_med_vilkaar") \
                    and not (h.get("vilkaar") or []):
                feil.append(f"handling '{hid}': irreversibel handling med modus "
                            f"'{h['modus']}' krever minst ett vilkår — uten "
                            "attestasjon konsumeres ingen jti og replay-vernet "
                            "finnes ikke")
        # `auto_med_vilkaar` UTEN vilkår degenererer til ren `auto` — fullmakt
        # uten port. Håndheves her i den KANONISKE validatoren (PR-014 R2), ikke
        # i en parallell validator. Typene er garantert (skjemaet passerte over).
        if h["modus"] == "auto_med_vilkaar" and not (h.get("vilkaar") or []):
            feil.append(f"handling '{hid}': modus 'auto_med_vilkaar' krever "
                        "minst ett vilkår")

    kategorier = set(policy["unntak"]["kategorier"])
    for obligatorisk in ("manglende_data", "over_grense", "regelkonflikt",
                         "teknisk_feil", "ugyldig_data", "ukjent"):
        if obligatorisk not in kategorier:
            feil.append(f"unntak.kategorier mangler obligatorisk '{obligatorisk}'")

    # PR-012: menneskelig_overstyring — LUKKET (grunnkode, handling)-mapping.
    # Et menneske kan ikke «godkjenne bort» en teknisk feil eller dikte
    # manglende data; kun eksplisitt godkjennbare vilkår. Deny-by-default:
    # mangler feltet, er ingen menneskelig godkjenning mulig (håndheves i
    # beslutningsveien, ikke her).
    if "menneskelig_overstyring" in policy:
        mo = policy["menneskelig_overstyring"]
        if mo.get("krever_rolle") not in roller:
            feil.append(f"menneskelig_overstyring: ukjent rolle "
                        f"'{mo.get('krever_rolle')}'")
        par: set[tuple[str, str]] = set()
        for i, e in enumerate(mo["godkjennbare"]):
            gk, hn = e["grunnkode"], e["handling"]
            if hn not in sett:
                feil.append(f"menneskelig_overstyring[{i}]: ukjent handling "
                            f"'{hn}'")
            if gk in IKKE_MENNESKELIG_GODKJENNBARE:
                feil.append(f"menneskelig_overstyring[{i}]: grunnkode '{gk}' "
                            f"kan aldri godkjennes menneskelig")
            if (gk, hn) in par:
                feil.append(f"menneskelig_overstyring[{i}]: duplisert "
                            f"(grunnkode, handling) ({gk}, {hn})")
            par.add((gk, hn))
    return feil


#: Grunnkoder et menneske ALDRI kan godkjenne (v6 §6): tekniske/data-feil
#: løses av M-37 eller avvises, ikke «godkjennes bort». Lukket deny-liste.
IKKE_MENNESKELIG_GODKJENNBARE = frozenset({
    "teknisk_feil", "manglende_data", "ugyldig_data", "motor_exception",
    "logging_feilet", "uautentisert_kontekst", "dataklassifisering_mangler",
    "dataklassifisering_ubetrodd_kilde",
})
