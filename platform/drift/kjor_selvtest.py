"""Inngangspunkt for `disponit-selvtest.service` (M-11, 091).

Én JSON-linje per kjøring, som de andre drift-jobbene. Linjen bærer
`kjoring_id`, samlet-dommen basen felte, og status per probe — aldri
`maalt`-innholdet: journalen er ikke rapportens hjem, tabellen er det, og
en linje som gjentok hele runden ville vært det ene stedet en fremtidig
probe kunne lekke noe uten at porten så det.

EXIT-KODENE ER OM SELVTESTEN VIRKET, IKKE OM PLATTFORMEN ER FRISK:

  0   runden ble registrert (også når prober er røde — det er en MÅLING,
      og varslene for dem er alt køet i samme transaksjon)
  1   runden kunne ikke registreres (basen utilgjengelig eller avviste)
  2   jobben kunne ikke starte (DSN mangler)

Forskjellen er hele poenget. En rød probe som ga exit 1 ville gjort
`systemctl status disponit-selvtest` rød av at noe ANNET er galt, og da
hadde vi mistet signalet om at selvtesten selv er nede — som er det ene
`varsle_selvtest_uteblitt` ikke kan se fra innsiden.
"""
from __future__ import annotations

import json
import os
import sys

from . import selvtest


def main() -> int:
    from db.hemmeligheter import last_credentials
    from db.pg import koble
    last_credentials()
    # Egen rolle (`disponit_selvtest`) med nøyaktig én rettighet: EXECUTE
    # på skrivedøren. Ingen `DATABASE_URL`-fallback — den ville gitt
    # jobben runtimes fullmakter, og rollen finnes for å slippe dem.
    dsn = os.environ.get("DISPONIT_SELVTEST_URL")
    if not dsn:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "grunn": "DISPONIT_SELVTEST_URL mangler"}),
              file=sys.stderr)
        return 2
    try:
        conn = koble(dsn)
    except Exception as e:                                    # noqa: BLE001
        print(json.dumps({"hendelse": "selvtestkjoring",
                          "grunn": "tilkobling_feilet",
                          "feiltype": type(e).__name__}))
        return 1
    try:
        res = selvtest.kjor(conn)
    except Exception as e:                                    # noqa: BLE001
        # Feiltypen, aldri teksten: en unntaksmelding fra basen kan bære
        # parameterverdier, og runden bærer et oppsettsbilde.
        print(json.dumps({"hendelse": "selvtestkjoring",
                          "grunn": "registrering_feilet",
                          "feiltype": type(e).__name__}))
        return 1
    finally:
        try:
            conn.close()
        except Exception:                                     # noqa: BLE001
            pass
    # STATUS PER PROBE, ALDRI `maalt`. Se modulteksten.
    statuser = {navn: p["status"] for navn, p in res["prober"].items()}
    print(json.dumps({"hendelse": "selvtestkjoring",
                      "kjoring_id": res["kjoring_id"],
                      "ny": res["ny"],
                      "rode": sorted(n for n, s in statuser.items()
                                     if s == selvtest.ROD),
                      "statuser": statuser}))
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
