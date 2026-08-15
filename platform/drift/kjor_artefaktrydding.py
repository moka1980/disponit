"""Inngangspunkt for `disponit-artefaktrydding.service`.

Telleren for sammenhengende feil lever i en liten tilstandsfil, ikke i minnet:
hver kjøring er en egen prosess (`Type=oneshot`), så «to feilede kjøringer på
rad» kan ikke observeres av kjøringen selv. Uten filen ville alarmen i §6 vært
umulig å utløse — den ville krevd at én prosess overlevde begge feilene.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import artefaktrydding

TILSTAND = Path(os.environ.get("DISPONIT_RYDDETILSTAND",
                               "/var/lib/disponit/artefaktrydding.json"))


def _les_feiltelling() -> int:
    try:
        return int(json.loads(TILSTAND.read_text(encoding="utf-8"))["feil"])
    except Exception:
        # Manglende/ødelagt fil betyr «vi vet ikke om forrige kjøring feilet».
        # Da er 0 riktig: en alarm som utløses av en tapt fil er en falsk
        # alarm, og §6 handler om en jobb som faktisk har vært nede to ganger.
        return 0


def _skriv_feiltelling(n: int) -> None:
    try:
        TILSTAND.parent.mkdir(parents=True, exist_ok=True)
        TILSTAND.write_text(json.dumps({"feil": n}), encoding="utf-8")
    except OSError:
        pass          # tilstandsfilen er drift, ikke korrekthet


def main() -> int:
    from db.pg import koble
    dsn = os.environ.get("DISPONIT_DOMAINS_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "grunn": "DISPONIT_DOMAINS_URL mangler"}),
              file=sys.stderr)
        return 2

    tidligere = _les_feiltelling()
    try:
        conn = koble(dsn)
    except Exception:
        # Databasen utilgjengelig ER en feilet kjøring (§6) — telleren skal
        # øke akkurat som ved en feilet `rydd_staged_artefakter()`, ellers
        # utløser en vedvarende tilkoblingsfeil aldri alarmen den skal.
        n = tidligere + 1
        _skriv_feiltelling(n)
        print(json.dumps({
            "hendelse": "ryddekjoring", "forkastet": 0, "batcher": 0,
            "karantene_bevart": 0, "feilet": 1, "sammenhengende_feil": n,
            "alarm": int(n >= artefaktrydding.ALARM_ETTER_FEIL),
            "grunn": "tilkobling_feilet",
        }))
        return 1

    try:
        # §6: 500 per KJØRING, ikke per batch. `maks_batcher=1` sammen med
        # `grense=BATCHGRENSE` (500) er selve grensen — flere batcher her ville
        # latt én timeraktivering forkaste et multiplum av 500.
        r = artefaktrydding.kjor(conn, maks_batcher=1, tidligere_feil=tidligere)
    finally:
        conn.close()

    _skriv_feiltelling(tidligere + 1 if r.feilet else 0)
    print(json.dumps({
        "hendelse": "ryddekjoring",
        "forkastet": r.forkastet,
        "batcher": r.batcher,
        "karantene_bevart": r.karantene_bevart,
        "feilet": int(r.feilet),
        "sammenhengende_feil": tidligere + 1 if r.feilet else 0,
        "alarm": int(r.alarm_utlost),
    }))
    return 1 if r.feilet else 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
