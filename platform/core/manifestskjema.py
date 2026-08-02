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

import json
from pathlib import Path

SKJEMA_STI = Path(__file__).resolve().parent / "manifest-skjema.json"


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
