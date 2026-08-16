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
# Halen kom ikke gratis. Databasen leser `$` som ekte slutt (migrasjon 020–024
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
    """`pattern`-nøkkelordet med ECMA-ankre. Feilteksten er `jsonschema`s egen,
    så feillistene kallerne allerede leser ser uendret ut."""
    if validator.is_type(instans, "string") \
            and not _strengt_monster(monster).search(instans):
        yield jsonschema.ValidationError(
            f"{instans!r} does not match {monster!r}")


_Validator = jsonschema.validators.extend(
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
_VALIDATOR_CACHE: dict[tuple, object] = {}


def _last_skjema() -> dict:
    return json.loads(_SKJEMA_STI.read_text(encoding="utf-8"))


def _validator():
    try:
        st = _SKJEMA_STI.stat()
        nokkel = (str(_SKJEMA_STI), st.st_mtime_ns, st.st_size)
    except OSError:
        nokkel = (str(_SKJEMA_STI), None, None)
    v = _VALIDATOR_CACHE.get(nokkel)
    if v is None:
        v = _Validator(_last_skjema())
        _VALIDATOR_CACHE.clear()      # kun én versjon om gangen
        _VALIDATOR_CACHE[nokkel] = v
    return v


def valider_policy(policy: object) -> list[str]:
    """Returnerer komplett feilliste. Tom liste == gyldig. Kaster aldri."""
    try:
        return _valider(policy)
    except Exception as e:  # siste skanse — aldri ukontrollert exception
        return [f"intern valideringsfeil ({type(e).__name__}): {e}"]


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
        if h["reversering"]["type"] == "irreversibel" \
                and not (h.get("grenser") or h.get("vilkaar")):
            feil.append(f"handling '{hid}': irreversibel uten grenser/vilkår")
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
