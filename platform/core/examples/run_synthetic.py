"""Syntetisk kjøring v0.2 mot alle tre bransjemaler.

Bevis: samme motor, tre ulike policyer (bedriftsuavhengighet) — og
invarianten fra M-1: en blokkert hendelse blir ALDRI TILLAT, og ingen
sideeffekt uten sikret logg (sikker_beslutning-kontrakten).
"""
from __future__ import annotations

import random
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from policy_validator.audit import sikker_beslutning  # noqa: E402
from policy_validator.engine import (  # noqa: E402
    TILLAT, EvaluationContext, MinneTellerLager)
from policy_validator.schema import valider_policy  # noqa: E402

RNG = random.Random(42)
POLICIES = Path(__file__).resolve().parents[3] / "policies"
LOGG = Path(__file__).resolve().parent / "audit.jsonl"
NAA = datetime(2026, 8, 3, 8, 30, tzinfo=timezone.utc)  # mandag formiddag
CTX = EvaluationContext("tenant-demo", "agent", True, "system_jobb")


def attest(policy: dict, h: dict, ressurs: str, gyldig: bool = True) -> dict:
    """Bygger attestasjoner fra policyens betrodde verifikatorer."""
    ut = {}
    for vk in h.get("vilkaar") or []:
        a = {"verifikator": vk["verifikator"], "ressurs_id": ressurs,
             "utloper": (NAA + timedelta(hours=6)).isoformat()}
        if "min" in vk:
            a["verdi"] = vk["min"] + (0.1 if gyldig else -0.1) \
                if isinstance(vk["min"], float) else vk["min"] + (5 if gyldig else -5)
        else:
            a["resultat"] = gyldig
        ut[vk["navn"]] = a
    return ut


def synt(policy: dict, antall: int):
    for i in range(antall):
        h = RNG.choice(policy["handlinger"])
        ressurs = f"res-{i}"
        grenser = h.get("grenser") or {}
        maks = grenser.get("belop_maks")
        e = {"handling": h["id"], "ressurs_id": ressurs, "valuta": "NOK",
             "tidspunkt": (NAA + timedelta(minutes=i)).isoformat(),
             "dataklasser": list(h.get("dataklasser_tillatt") or ["intern"])[:1],
             "dataklasser_kilde": "connector",
             "attestasjoner": attest(policy, h, ressurs)}
        if (fr := grenser.get("frekvens")):
            e[fr["grupperingsnokkel"]] = f"grp-{i % 7}"
        if maks is not None:
            e["belop"] = f"{RNG.randint(1, int(float(maks)) or 1)}.00"
        stil = RNG.random()
        if stil < 0.12 and maks:
            e["belop"] = f"{int(float(maks)) * 3}.00"            # over grense
        elif stil < 0.20:
            e["attestasjoner"] = attest(policy, h, ressurs, gyldig=False)
        elif stil < 0.26 and e["attestasjoner"]:
            e["attestasjoner"].popitem()                          # mangler bevis
        elif stil < 0.30:
            e["handling"] = "system.slett_database"               # deny by default
        elif stil < 0.34:
            e["dataklasser_kilde"] = "selvrapportert"             # fail-closed
        elif stil < 0.37 and maks:
            e["belop"] = True                                     # bool-angrep
        yield e


def main() -> int:
    LOGG.unlink(missing_ok=True)
    total = Counter()
    for fil in sorted(POLICIES.glob("bransjemal-*.yaml")):
        policy = yaml.safe_load(fil.read_text(encoding="utf-8"))
        feil = valider_policy(policy)
        if feil:
            print(f"AVVIST: {fil.name}: {feil[:3]}"); return 1
        teller = MinneTellerLager()
        c = Counter()
        for e in synt(policy, 60):
            d = sikker_beslutning(policy, CTX, e, LOGG, teller=teller, naa=NAA)
            c[d.beslutning] += 1
            if d.beslutning == TILLAT:
                assert d.unntak_kategori is None and d.effekt is None
        total.update(c)
        print(f"{policy['meta']['policy_id']:24s} TILLAT={c['TILLAT']:3d} "
              f"STOPP={c['STOPP']:3d} UNNTAK={c['UNNTAK']:3d}")
    print("-" * 58)
    print(f"{'TOTALT (180 hendelser)':24s} TILLAT={total['TILLAT']:3d} "
          f"STOPP={total['STOPP']:3d} UNNTAK={total['UNNTAK']:3d}")
    n_logg = LOGG.read_text(encoding="utf-8").count("\n")
    assert n_logg == sum(total.values()), "hver beslutning skal ha loggpost"
    print(f"M-2-logg: {n_logg} poster — 1:1 med beslutninger (logg-før-utførelse)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
