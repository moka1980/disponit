"""Strukturell validering av en policyfil mot skjema v0.1.

Kjøres FØR en policy tas i bruk (port i utrullingsløypen, steg 3):
en policy som ikke består, kan aldri aktiveres for en kunde.
"""
from __future__ import annotations

GYLDIG_MODUS = {"auto", "auto_med_vilkaar", "alltid_stopp"}
GYLDIG_VED_BRUDD = {"unntakskø", "stopp_og_varsle", "frys"}
PAKREVDE_TOPPNIVAA = ["schema_version", "meta", "roller", "handlinger", "unntak"]


def valider_policy(policy: dict) -> list[str]:
    """Returnerer liste av feil. Tom liste == gyldig."""
    feil: list[str] = []
    if not isinstance(policy, dict):
        return ["policy er ikke et objekt"]

    for k in PAKREVDE_TOPPNIVAA:
        if k not in policy:
            feil.append(f"mangler toppnivåfelt '{k}'")

    handlinger = policy.get("handlinger") or []
    sett_ider: set[str] = set()
    for i, h in enumerate(handlinger):
        ref = f"handlinger[{i}]"
        hid = h.get("id")
        if not hid:
            feil.append(f"{ref}: mangler id")
            continue
        if hid in sett_ider:
            feil.append(f"{ref}: duplisert id '{hid}'")
        sett_ider.add(hid)
        if h.get("modus") not in GYLDIG_MODUS:
            feil.append(f"{ref} '{hid}': ugyldig modus '{h.get('modus')}'")
        if h.get("ved_brudd", "unntakskø") not in GYLDIG_VED_BRUDD:
            feil.append(f"{ref} '{hid}': ugyldig ved_brudd '{h.get('ved_brudd')}'")
        if h.get("modus") in {"auto", "auto_med_vilkaar"} and not h.get("tillatt_for"):
            feil.append(f"{ref} '{hid}': auto-modus krever tillatt_for")
        if "reversibel" not in h and h.get("modus") != "alltid_stopp":
            feil.append(f"{ref} '{hid}': mangler eksplisitt 'reversibel'")
        if h.get("reversibel") is False:
            # Irreversible handlinger må ha harde rammer (v7-prinsipp)
            if not (h.get("grenser") or h.get("vilkaar")):
                feil.append(f"{ref} '{hid}': irreversibel handling uten "
                            "grenser eller vilkår er ikke tillatt")

    roller = {r.get("id") for r in (policy.get("roller") or [])}
    for h in handlinger:
        for rolle in h.get("tillatt_for") or []:
            if rolle not in roller:
                feil.append(f"handling '{h.get('id')}': ukjent rolle '{rolle}'")
    return feil
