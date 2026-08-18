"""Inngangspunkt for `disponit-evidensreaper.service`.

Én JSON-linje per kjøring — samme kontrakt som de andre drift-jobbene:
utfallet skal kunne leses av `journalctl` uten å kjenne koden. Exit 1 på
feilet kjøring, så `systemctl status` og helsesjekken ser det.
"""
from __future__ import annotations

import json
import os
import sys

from . import evidensreaper


def _koble(dsn: str):
    from db.pg import koble
    return koble(dsn)


def main() -> int:
    from db.hemmeligheter import last_credentials
    last_credentials()  # PR-009 §5: LoadCredential før env-lesing under
    dsn = os.environ.get("DISPONIT_DOMAINS_URL") or os.environ.get(
        "DATABASE_URL")
    if not dsn:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "grunn": "DISPONIT_DOMAINS_URL mangler"}),
              file=sys.stderr)
        return 2
    try:
        conn = _koble(dsn)
    except Exception as e:
        print(json.dumps({"hendelse": "reaperkjoring", "reapet": 0,
                          "feilet": 1, "grunn": "tilkobling_feilet",
                          "feiltype": type(e).__name__}))
        return 1
    try:
        r = evidensreaper.kjor(conn)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    # Id-ene logges — de er referanser, aldri innhold (payloaden er og
    # forblir kryptert; klartekst finnes ikke i denne prosessen).
    print(json.dumps({"hendelse": "reaperkjoring",
                      "reapet": len(r.reapet),
                      "saker": [{"tenant": t, "oppdrag_id": o,
                                 "unntak_id": u} for t, o, u in r.reapet],
                      "feilet": int(r.feilet)}))
    return 1 if r.feilet else 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
