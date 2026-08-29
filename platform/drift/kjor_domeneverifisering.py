"""Inngangspunkt for `disponit-domeneverifisering.service` (039).

Førstegangsverifiseringen av selvbetjente domenechallenges — lite og
hyppig (5 min), adskilt fra den timeplanlagte revalideringen. Samme
resolver-diversitetskrav, samme oppstartsnekt uten det — og samme
ALARMKONTRAKT: slår terskelen for bred resolverfeil inn, avsluttes
prosessen med feilkode, slik at systemd setter unitten i `failed`. Et
JSON-felt alene er en alarm ingenting lytter på.
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
    if r.get("alarm_utlost"):
        # TERSKELEN SKAL FØRE ET STED (Codex P2). Er begge resolverne nede,
        # telles hver rad som `uenige` og hver eneste selvbetjening står
        # stille — men passet returnerte 0, så `systemctl status` viste en
        # vellykket aktivering hvert femte minutt mens ingen kunde ble
        # verifisert. Ingen journalkonsument leser et JSON-felt; en feilkode
        # setter unitten i `failed`, synlig for `systemctl --failed` og for
        # enhver OnFailure drift senere henger på. Samme kontrakt som
        # revalideringens §2.4-alarm.
        #
        # Radene som FAKTISK ble bekreftet er alt committet (én commit per
        # rad), så feilkoden kaster ikke arbeid: den sier at passet ikke kan
        # stås inne for, ikke at det ikke skjedde.
        print(json.dumps({"hendelse": "verifiseringsalarm",
                          "grunn": "bred_resolverfeil",
                          "andel_terskel": dr.ALARM_ANDEL,
                          "uenige": r["uenige"], "vurdert": r["vurdert"]}),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
