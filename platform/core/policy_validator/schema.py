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
from pathlib import Path
from zoneinfo import ZoneInfo

import jsonschema

_SKJEMA_STI = Path(__file__).resolve().parents[3] / "policies" / "policy-schema-v0.2.json"

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
    import jsonschema
    try:
        st = _SKJEMA_STI.stat()
        nokkel = (str(_SKJEMA_STI), st.st_mtime_ns, st.st_size)
    except OSError:
        nokkel = (str(_SKJEMA_STI), None, None)
    v = _VALIDATOR_CACHE.get(nokkel)
    if v is None:
        v = jsonschema.Draft202012Validator(_last_skjema())
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
