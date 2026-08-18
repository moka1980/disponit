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
    # INGEN except her, med vilje (Codex P2). Slipper en uventet databasefeil
    # ut av passet — funksjonen er ikke utrullet, grantet eller eierskapet er
    # feil, SQL-en har en programmeringsfeil — skal unitten bli RØD. En
    # oneshot som svarer 0 mens hver challenge sto ubehandlet ser ut som et
    # vellykket pass i `systemctl status`, og selvbetjeningen kunne stått
    # stille i dager uten at noe pekte på den. Sporet (traceback) hører til i
    # journalen, ikke i en sanitert JSON-linje.
    try:
        r = dr.kjor_ventende(conn, res_konf)
    finally:
        conn.close()
    print(json.dumps({"hendelse": "verifiseringspass", **r}))
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
