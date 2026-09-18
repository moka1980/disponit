#!/usr/bin/env python3
"""Ytelse for en SVEIPMODUL — samme produsent for alle.

Punktet «ytelse innenfor budsjett» er for en sveip et spørsmål om
KOSTNADEN PER RUNDE: timeren gir jobben et vindu, og en sveip som vokser
ut av det vinduet stopper stille å levere.

Målingen er veggklokke rundt modulens egen `kjor()`, på den ekte
tilkoblingen, mot den ekte basen — ikke rundt SQL-funksjonen alene.
Låsen, kontraktvalideringen og committen er en del av kostnaden.

TRE KJØRINGER, OG DEN DÅRLIGSTE ER DOMMEN. Én kjøring er en anekdote;
et snitt skjuler nettopp runden som sprakk taket. Første kjøring gjør
dessuten jobben (skriver funnene) og de neste finner dem igjen — begge
formene skal holde seg innenfor budsjettet, og det er den dyreste av dem
taket måles mot.

BRUK (på verten som root, `staging.env` sourcet):
    python deploy/staging/sveip-ytelse.py --modul m19_adresse [--ut …]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

from manifestskjema import (SVEIPMODULER, _sjekk_grenser,  # noqa: E402
                            sveip_ytelse_bevisrot_sha256,
                            valider_artefaktformat)

KJORINGER = 3


def _log(*a):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}]", *a, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modul", required=True)
    ap.add_argument("--vert", default=os.uname().nodename)
    ap.add_argument("--kjoringer", type=int, default=KJORINGER)
    ap.add_argument("--ut", type=Path)
    a = ap.parse_args()
    if a.modul not in SVEIPMODULER:
        raise SystemExit(f"AVBRUTT: {a.modul} er ikke registrert som"
                         " sveipmodul i manifestskjema.SVEIPMODULER")
    k = SVEIPMODULER[a.modul]
    sv_dsn = os.environ.get(k["dsn_variabel"])
    if not sv_dsn:
        raise SystemExit(f"AVBRUTT: {k['dsn_variabel']} mangler")
    modul = __import__(f"drift.{k['modul_fil']}", fromlist=["kjor"])

    tider: list[float] = []
    resultater = []
    feilet = False
    tenanter = 0
    for i in range(a.kjoringer):
        sv = psycopg.connect(sv_dsn)
        t0 = time.monotonic()
        res = modul.kjor(sv)
        tider.append(round(time.monotonic() - t0, 4))
        sv.close()
        if res.hoppet_over:
            raise SystemExit("AVBRUTT: kjøringen ble HOPPET OVER"
                             " (arbeidernøkkelen opptatt) — en varighet"
                             " uten kjøring er ingen måling")
        feilet = feilet or bool(res.feilet)
        tenanter = max(tenanter, int(res.tenanter))
        resultater.append({"tenanter": res.tenanter, "nye": res.nye,
                           "oppdaterte": res.oppdaterte,
                           "lukkede": res.lukkede})
        _log(f"kjøring {i + 1}: {tider[-1]:.3f}s, {res.tenanter} tenanter,"
             f" nye={res.nye} oppdaterte={res.oppdaterte}"
             f" lukkede={res.lukkede} feilet={res.feilet}")

    verst = max(tider)
    ts = datetime.now(timezone.utc).isoformat()
    art = {
        "krav_id": k["ytelse_krav"], "ts": ts, "bestatt": True,
        "oppsett": {"modul": a.modul, "vert": a.vert,
                    "sveip": k["modul_fil"],
                    "bevisrot_sha256": sveip_ytelse_bevisrot_sha256()},
        "maalt": {
            "sekunder": verst,
            "kjoringer": tider,
            "tenanter": tenanter,
            "sekunder_per_tenant": round(verst / tenanter, 4) if tenanter else 0,
            "feilet": feilet,
            "per_kjoring": resultater,
        },
    }
    feil = valider_artefaktformat(art, k["ytelse_krav"]) \
        + _sjekk_grenser(k["ytelse_krav"], art)
    art["bestatt"] = not feil
    if feil:
        art["feil"] = feil
    ut = a.ut or (REPO / "deploy/staging/artefakter"
                  / f"{k['ytelse_krav']}-"
                    f"{ts[:19].replace(':', '').replace('-', '')}Z.json")
    ut.parent.mkdir(parents=True, exist_ok=True)
    ut.write_text(json.dumps(art, indent=2, ensure_ascii=False, sort_keys=True)
                  + "\n", encoding="utf-8")
    _log(f"skrev {ut} (bestatt={art['bestatt']})")
    for f in feil:
        _log("  FEIL:", f)
    return 0 if not feil else 1


if __name__ == "__main__":
    raise SystemExit(main())
