"""Kjører validatoren mot alle tre bransjemaler med syntetiske hendelser.

Formål (README steg 2): bevise at motoren er bedriftsuavhengig —
samme kode, tre ulike policyer — og at ingen blokkert hendelse
noensinne får TILLAT. Skriver M-2-logg til examples/audit.jsonl.
"""
from __future__ import annotations

import random
import sys
from collections import Counter
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # platform/core
from policy_validator.audit import lag_loggpost, skriv  # noqa: E402
from policy_validator.engine import TILLAT, evaluate  # noqa: E402
from policy_validator.schema import valider_policy  # noqa: E402

RNG = random.Random(42)  # deterministisk kjøring
POLICIES = Path(__file__).resolve().parents[3] / "policies"
LOGG = Path(__file__).resolve().parent / "audit.jsonl"


def synt_hendelser(policy: dict, antall: int) -> list[dict]:
    """Genererer en blanding: gyldige, over grense, manglende vilkår,
    feil rolle og helt ukjente handlinger."""
    handlinger = policy.get("handlinger") or []
    ut: list[dict] = []
    for _ in range(antall):
        h = RNG.choice(handlinger)
        grenser = h.get("grenser") or {}
        maks = grenser.get("belop_maks") or 20000
        e: dict = {"handling": h["id"], "aktor_rolle": "agent",
                   "valuta": "NOK", "tidspunkt": "2026-08-03T10:00:00",
                   "dataklasser": list(h.get("dataklasser_tillatt") or [])[:1],
                   "vilkaar": {}}
        # oppfyll alle vilkår som utgangspunkt
        for krav in h.get("vilkaar") or []:
            if isinstance(krav, str):
                e["vilkaar"][krav] = True
            elif isinstance(krav, dict):
                navn, terskel = next(iter(krav.items()))
                e["vilkaar"][navn] = terskel if isinstance(terskel, str) \
                    else terskel + 1
        e["belop"] = RNG.randint(100, int(maks))
        # muter en andel til å bryte policy på ulike måter
        stil = RNG.random()
        if stil < 0.15:
            e["belop"] = int(maks) * 3                       # over grense
        elif stil < 0.25 and e["vilkaar"]:
            e["vilkaar"].pop(RNG.choice(list(e["vilkaar"])))  # mangler data
        elif stil < 0.32:
            e["aktor_rolle"] = "praktikant"                   # ukjent rolle
        elif stil < 0.38:
            e["handling"] = "system.slett_database"           # ukjent handling
        ut.append(e)
    return ut


def main() -> int:
    LOGG.unlink(missing_ok=True)
    total = Counter()
    for fil in sorted(POLICIES.glob("bransjemal-*.yaml")):
        policy = yaml.safe_load(fil.read_text())
        feil = valider_policy(policy)
        if feil:
            print(f"AVVIST: {fil.name} består ikke skjema: {feil}")
            return 1
        teller = Counter()
        for e in synt_hendelser(policy, 60):
            d = evaluate(policy, e)
            teller[d.beslutning] += 1
            skriv(LOGG, lag_loggpost(d, e, policy))
            # Invariant: blokkert kan aldri bli TILLAT — dobbeltsjekk
            if d.beslutning == TILLAT:
                assert d.unntak_kategori is None and d.effekt is None
        total.update(teller)
        navn = policy["meta"]["bransjemal"]
        print(f"{navn:32s} TILLAT={teller['TILLAT']:3d} "
              f"STOPP={teller['STOPP']:3d} UNNTAK={teller['UNNTAK']:3d}")
    print("-" * 62)
    print(f"{'TOTALT (180 hendelser)':32s} TILLAT={total['TILLAT']:3d} "
          f"STOPP={total['STOPP']:3d} UNNTAK={total['UNNTAK']:3d}")
    print(f"M-2-logg: {LOGG} ({sum(total.values())} poster, append-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
