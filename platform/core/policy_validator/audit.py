"""M-2 Revisjonslogg og evidens — loggformat v0.1.

Krav fra M-1-aksept i prototype v7.2:
  «100 % av skrivehandlinger har policy-ID, aktør, input-hash og
   begrunnelse; blokkerte handlinger utføres aldri.»

Loggen er append-only JSONL. Hashen er deterministisk (kanonisk JSON),
slik at samme hendelse alltid gir samme input-hash — det gjør
beslutninger etterprøvbare og dubletter oppdagbare.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .engine import Decision


def input_hash(event: dict) -> str:
    kanonisk = json.dumps(event, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), default=str)
    return hashlib.sha256(kanonisk.encode("utf-8")).hexdigest()


def lag_loggpost(decision: Decision, event: dict, policy: dict) -> dict[str, Any]:
    meta = policy.get("meta") or {}
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "input_hash": input_hash(event),
        "aktor": event.get("aktor_rolle"),
        "policy_id": decision.policy_id,
        "bransjemal": meta.get("bransjemal"),
        "mal_status": meta.get("status"),
        "schema_version": policy.get("schema_version"),
        "beslutning": decision.beslutning,
        "unntak_kategori": decision.unntak_kategori,
        "effekt": decision.effekt,
        "begrunnelse": [g.to_dict() for g in decision.begrunnelse],
    }


def skriv(loggfil: Path, post: dict) -> None:
    """Append-only. Filen åpnes i append-modus; ingenting overskrives."""
    loggfil.parent.mkdir(parents=True, exist_ok=True)
    with loggfil.open("a", encoding="utf-8") as f:
        f.write(json.dumps(post, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# PR-002: logg-før-utførelse-kontrakten (review spm. 3: revisjonsloggfeil
# og «bare TILLAT kan utløse sideeffekt»).
# ---------------------------------------------------------------------------
from .engine import STOPP, Decision, EvaluationContext, Grunn, TellerLager  # noqa: E402


def sikker_beslutning(policy: dict, context, event: dict, loggfil: Path,
                      teller: "TellerLager | None" = None,
                      naa=None) -> Decision:
    """ENESTE lovlige inngang for moduler som skal utføre skrivehandlinger.

    Kontrakt (fail-closed i alle grener):
      1. evaluate() kastes aldri videre — uventet exception => STOPP.
      2. Beslutningen logges FØR den returneres. Kan loggen ikke skrives,
         returneres STOPP (teknisk_feil) — en skrivehandling uten sikret
         revisjonslogg er forbudt (M-1-aksept).
      3. Frekvensforekomst registreres i det betrodde telleret KUN ved
         TILLAT med sikret logg.
      4. Kalleren får utføre sideeffekten HVIS OG BARE HVIS returverdien
         er TILLAT. STOPP, UNNTAK, exception og timeout er alle nei.
    """
    from .engine import evaluate  # lokal import unngår sirkularitet
    from datetime import datetime, timezone
    naa = naa or datetime.now(timezone.utc)
    try:
        d = evaluate(policy, context, event, teller=teller, naa=naa)
    except Exception as e:  # fail-closed, aldri gjetting
        d = Decision(STOPP, str(event.get("handling")), "ukjent",
                     [Grunn("motor_exception", {"type": type(e).__name__})])
    try:
        skriv(loggfil, lag_loggpost(d, event, policy))
    except Exception:
        return Decision(STOPP, d.handling, d.policy_id,
                        d.begrunnelse + [Grunn("logging_feilet")])
    if d.beslutning == "TILLAT" and d.frekvensnokkel and teller is not None:
        teller.registrer(d.frekvensnokkel, naa)
    return d
