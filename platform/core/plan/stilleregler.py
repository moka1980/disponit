"""M-17 (204) — stille avsendere: regelklassifisering i planrunden.

Runden kaller ÉN definer, `m17_klassifiser_etter_regel`, og får to tall
tilbake. Regelen — hvilke avsendere som er stille, og hva de skal
klassifiseres som — eies av BASEN og settes av tenanten gjennom
`m17_sett_stilleregler`. Denne fila bærer ingen liste av domener; en
konstant her ville vært nøyaktig den fullmakten planrunden ikke skal ha.

HVORFOR DEN FINNES: broen (203) gjorde 18 e-poster til henvendelser, og
alle 18 var systemvarsler som sto åpne og uklassifiserte til et menneske
klikket seg gjennom dem. Dommen (eier 15/9): REGEL FØRST, MODELL SENERE.

KUN ÅPNE, UKLASSIFISERTE henvendelser røres — det håndhever definereren,
ikke denne fila. Kill-switch: `DISPONIT_STILLEREGLER=av`.
"""
from __future__ import annotations

import json
import os

GRENSE = 500


def er_av() -> bool:
    return os.environ.get("DISPONIT_STILLEREGLER", "").strip().lower() \
        in ("av", "0", "false", "nei")


def kjor_en_runde(conn, *, grense: int = GRENSE) -> dict:
    if er_av():
        print(json.dumps({"hendelse": "stilleregler_av"}), flush=True)
        return {"av": True, "tenanter": 0, "klassifisert": 0}
    rad = conn.execute("SELECT * FROM m17_klassifiser_etter_regel(%s)",
                       (grense,)).fetchone()
    conn.commit()
    res = {"tenanter": int(rad[0]), "klassifisert": int(rad[1])}
    if res["klassifisert"]:
        print(json.dumps({"hendelse": "stilleregler_runde", **res}),
              flush=True)
    return res
