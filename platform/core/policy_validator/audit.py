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
        "begrunnelse": decision.begrunnelse,
    }


def skriv(loggfil: Path, post: dict) -> None:
    """Append-only. Filen åpnes i append-modus; ingenting overskrives."""
    loggfil.parent.mkdir(parents=True, exist_ok=True)
    with loggfil.open("a", encoding="utf-8") as f:
        f.write(json.dumps(post, ensure_ascii=False) + "\n")
