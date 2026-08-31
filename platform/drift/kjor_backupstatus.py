"""Inngangspunkt for `disponit-backupstatus.service` (M-10, 090).

Én JSON-linje per kjøring — samme kontrakt som de andre drift-jobbene:
utfallet skal kunne leses av `journalctl` uten å kjenne koden.

EXIT-KODENE BÆRER DOMMEN, og de tre er ikke like:

  0, `skrevet=1`   ny verifisering registrert
  0, `skrevet=0`   rapporten var alt registrert (idempotent gjenspilling)
  0, `mangler`     ingen rapport ennå — en fersk vert, ikke en feil
  1, `grunn=...`   rapporten fantes og var ugyldig, eller basen avviste
                   den. INGEN rad ble skrevet.
  2                jobben kunne ikke starte (DSN mangler)

Grunnen til at `mangler` er 0 og ikke 1 står i `backupstatus`: en vert
uten backuphistorikk ville ellers vært rød hvert 30. minutt, og en jobb
som alltid er rød er en jobb ingen ser på. Tilstanden fanges av
`varsle_backupverifisering_uteblitt` i varselsenderen i stedet — der den
når et menneske i stedet for en journal.
"""
from __future__ import annotations

import json
import os
import sys

from . import backupstatus


def main() -> int:
    from db.hemmeligheter import last_credentials
    from db.pg import koble
    # `LoadCredential` legger hemmeligheten som FIL i
    # $CREDENTIALS_DIRECTORY og setter ingen miljøvariabel.
    last_credentials()
    # Lesejobben har sin EGEN rolle (`disponit_driftstatus`) med nøyaktig
    # én rettighet. `DATABASE_URL` er ikke en fallback her, slik den er i
    # reaperen: den ville gitt jobben runtime-rollens fullmakter, og
    # rollen finnes nettopp for å slippe dem.
    dsn = os.environ.get("DISPONIT_DRIFTSTATUS_URL")
    if not dsn:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "grunn": "DISPONIT_DRIFTSTATUS_URL mangler"}),
              file=sys.stderr)
        return 2
    sti = os.environ.get("DISPONIT_BACKUPRAPPORT",
                         backupstatus.STANDARDSTI)
    # RAPPORTEN LESES FØR TILKOBLINGEN. En vert uten backuphistorikk skal
    # ikke koble til databasen i det hele tatt for å konstatere det, og
    # en ugyldig rapport skal felles på sin egen ordlyd — ikke på en
    # tilkoblingsfeil som tilfeldigvis kom først.
    try:
        argumenter = backupstatus.les_rapport(sti)
    except backupstatus.UgyldigRapport as e:
        print(json.dumps({"hendelse": "backupstatus", "skrevet": 0,
                          "grunn": f"ugyldig rapport: {e}"}))
        return 1
    if argumenter is None:
        print(json.dumps({"hendelse": "backupstatus", "skrevet": 0,
                          "mangler": True, "sti": sti}))
        return 0
    try:
        conn = koble(dsn)
    except Exception as e:                                    # noqa: BLE001
        print(json.dumps({"hendelse": "backupstatus", "skrevet": 0,
                          "grunn": "tilkobling_feilet",
                          "feiltype": type(e).__name__}))
        return 1
    try:
        res = backupstatus.kjor(conn, sti=sti)
    finally:
        try:
            conn.close()
        except Exception:                                     # noqa: BLE001
            pass
    linje = {"hendelse": "backupstatus", "skrevet": res.skrevet}
    if res.grunn:
        linje["grunn"] = res.grunn
    print(json.dumps(linje))
    return 1 if res.grunn else 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
