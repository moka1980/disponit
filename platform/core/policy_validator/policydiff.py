"""Strukturert, deterministisk policy-diff (PR-013 §4).

Godkjenneren attesterer `diff_hash`, ikke versjonsnummeret — derfor må diffen
være en KANONISK representasjon av hva som endres, uavhengig av nøkkelrekkefølge
og formatering. Beregnes server-side mellom aktiv versjon (evt. `DENY_ALL_V1`)
og utkastets frosne innhold. `diff_hash = SHA-256(JCS(strukturert diff))`.

Diffen er FLAT (leaf-sti → verdi) så godkjenneren ser nøyaktig hvilke felt som
lagt til / fjernet / endret. Klassifiseringen (risikoklasse) er en SEPARAT
vurdering (`klassifikator`), bundet ved siden av diffen i runden.
"""
from __future__ import annotations

import hashlib
import json


def _flat(obj, prefix: str = "") -> dict:
    """Flat oppslag: leaf-sti → verdi. Arrays indekseres, dicts nøkles."""
    ut: dict = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            ut.update(_flat(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            ut.update(_flat(v, f"{prefix}[{i}]"))
    else:
        ut[prefix] = obj
    return ut


def strukturert_diff(base: dict, ny: dict) -> dict:
    """Kanonisk, sortert diff mellom to policyer. -> {"endringer": [...]} med
    hver endring `{sti, type, fra?, til?}`. Deterministisk: samme input → samme
    diff, uavhengig av nøkkelrekkefølge."""
    fb, fn = _flat(base or {}), _flat(ny or {})
    endringer = []
    for sti in sorted(set(fb) | set(fn)):
        i_b, i_n = sti in fb, sti in fn
        if i_b and not i_n:
            endringer.append({"sti": sti, "type": "fjernet", "fra": fb[sti]})
        elif i_n and not i_b:
            endringer.append({"sti": sti, "type": "lagt_til", "til": fn[sti]})
        elif fb[sti] != fn[sti]:
            endringer.append({"sti": sti, "type": "endret",
                              "fra": fb[sti], "til": fn[sti]})
    return {"endringer": endringer}


def diff_hash(diff: dict) -> str:
    return hashlib.sha256(json.dumps(
        diff, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def diff_og_hash(base: dict, ny: dict) -> tuple[dict, str]:
    d = strukturert_diff(base, ny)
    return d, diff_hash(d)
