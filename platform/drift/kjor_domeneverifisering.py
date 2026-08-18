"""Inngangspunkt for `disponit-domeneverifisering.service` (039).

Førstegangsverifiseringen av selvbetjente domenechallenges — lite og
hyppig (5 min), adskilt fra den timeplanlagte revalideringen. Samme
resolver-diversitetskrav, samme oppstartsnekt uten det.
"""
from __future__ import annotations

import json
import os
import sys

from . import domenerevalidering as dr
from .kjor_revalidering import _koble, resolvere


def main() -> int:
    from db.hemmeligheter import last_credentials
    last_credentials()
    try:
        res_konf = resolvere()
    except dr.Diversitetsfeil as e:
        print(json.dumps({"hendelse": "oppstart_nektet", "grunn": str(e)}),
              file=sys.stderr)
        return 2
    dsn = os.environ.get("DISPONIT_DOMAINS_URL") or os.environ.get(
        "DATABASE_URL")
    if not dsn:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "grunn": "DISPONIT_DOMAINS_URL mangler"}),
              file=sys.stderr)
        return 2
    conn = _koble(dsn)
    try:
        r = dr.kjor_ventende(conn, res_konf)
    finally:
        conn.close()
    print(json.dumps({"hendelse": "verifiseringspass", **r}))
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
