"""Inngangspunkt for `disponit-sveipestatus.service` (115).

Én JSON-linje per kjøring — samme kontrakt som de andre drift-jobbene:
utfallet skal kunne leses av `journalctl` uten å kjenne koden.

EXIT-KODENE BÆRER DOMMEN:

  0, `tause=0`      hele flåten har kjørt innenfor sitt vindu
  0, `tause=n`      n sveip er tause — RADENE ER FØRT, og varselet går
                    fra varselsenderen, ikke herfra
  1, `grunn=...`    basen avviste føringen. INGEN rad ble skrevet.
  2                 jobben kunne ikke starte (DSN mangler)

TAUSHET GIR EXIT 0, og det er et bevisst valg med samme begrunnelse som
`backupstatus` (090): en jobb som er rød hver gang tilstanden den måler
er dårlig, er en jobb noen slår av. Denne jobben lykkes når den klarer
å FØRE tilstanden; tilstanden selv er varselsenderens sak.

DEN VARSLER IKKE SELV. Den skriver rader; `varsle_sveip_uteblitt` i
varselsenderen leser dem og køer varselet — der det når et menneske i
stedet for en journal. Det er hele poenget med modulen: alarmen i de
atten sveipene har aldri hatt en konsument.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from . import sveipestatus


def main() -> int:
    from db.hemmeligheter import last_credentials
    from db.pg import koble
    # `LoadCredential` legger hemmeligheten som FIL i
    # $CREDENTIALS_DIRECTORY og setter ingen miljøvariabel.
    last_credentials()
    # SAMME ROLLE SOM M-10s lesejobb, og det er ikke gjenbruk av
    # bekvemmelighet: `disponit_driftstatus` finnes for nøyaktig denne
    # jobbklassen — en drift-observatør som leser filsystemtilstand og
    # fører den inn i basen. Den har null tabellrettigheter, og porten
    # måler at den fortsatt bare har EXECUTE på de to skrivedørene.
    #
    # INGEN FALLBACK TIL `DATABASE_URL`: den ville gitt jobben
    # runtime-rollens fullmakter, og rollen finnes for å slippe dem.
    dsn = os.environ.get("DISPONIT_DRIFTSTATUS_URL")
    if not dsn:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "grunn": "DISPONIT_DRIFTSTATUS_URL mangler"}),
              file=sys.stderr)
        return 2

    # FLÅTEN LESES FØR TILKOBLINGEN. Filsystemet er kilden, og en
    # lesefeil skal felles på sin egen ordlyd — ikke på en
    # databasefeil som skjer etterpå (090s form).
    rader = sveipestatus.les_flaaten()
    aktor = "sveipestatus"

    try:
        conn = koble(dsn)
    except Exception:                                        # noqa: BLE001
        print(json.dumps({"hendelse": "sveipestatus", "fort": 0,
                          "tause": 0, "i_alarm": 0, "feilet": 1,
                          "grunn": "tilkobling_feilet"}))
        return 1

    fort = 0
    try:
        for r in rader:
            sist = (datetime.fromtimestamp(r.sist_kjort_epoch,
                                           tz=timezone.utc)
                    if r.sist_kjort_epoch is not None else None)
            # `ulesbar` FØLGER MED. Uten det ville en korrupt
            # tilstandsfil sett helt frisk ut i basen: fila finnes, så
            # sveipen er ikke taus, og telleren er NULL — som
            # `coalesce(..., 0)` gjør til «ingen feil».
            conn.execute(
                "SELECT registrer_sveipestatus(%s,%s,%s,%s,%s,%s,%s)",
                (r.sveip, sist, r.sammenhengende_feil,
                 r.forventet_timer, r.uten_tilstandsfil, r.ulesbar,
                 aktor))
            fort += 1
        conn.commit()
    except Exception as e:                                   # noqa: BLE001
        try:
            conn.rollback()
        except Exception:                                    # noqa: BLE001
            pass
        print(json.dumps({"hendelse": "sveipestatus", "fort": 0,
                          "tause": 0, "i_alarm": 0, "feilet": 1,
                          "grunn": f"{type(e).__name__}"}))
        return 1
    finally:
        try:
            conn.close()
        except Exception:                                    # noqa: BLE001
            pass

    # TALLENE I LINJA REGNES AV DENNE JOBBEN, men de er en GJENGIVELSE
    # av det basen alt kan svare på — `sveipeflaaten` er autoritativ.
    # Linja finnes for at `journalctl` skal si noe uten et databasekall.
    naa = datetime.now(tz=timezone.utc).timestamp()
    tause = sum(1 for r in rader
                if r.sist_kjort_epoch is None
                or naa - r.sist_kjort_epoch > r.forventet_timer * 3600)
    alarm = sum(1 for r in rader
                if (r.sammenhengende_feil or 0) >= 2)
    ulesbare = sum(1 for r in rader if r.ulesbar)
    print(json.dumps({
        "hendelse": "sveipestatus",
        "fort": fort,
        "i_flaaten": len(rader),
        "tause": tause,
        "i_alarm": alarm,
        "ulesbare": ulesbare,
        "feilet": 0,
    }))
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
