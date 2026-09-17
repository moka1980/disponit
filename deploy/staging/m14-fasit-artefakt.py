#!/usr/bin/env python3
"""Staging-leddet for M-14s fasitartefakt (sertifiseringen 17/9, natt).

Driver NØYAKTIG samme sett som CI-leddet (`m14_fasit.kjor_sett`) gjennom
de EKTE dørene på verten — kundens dører som runtime, fakturasveipen som SVEIPEROLLEN, og
flaten lest gjennom `api.faktura.svar_for` — og skriver artefaktet fra
avvikene målingen fant.

BRUK (på verten, som root, med basen oppe):
    /opt/disponit/.venv/bin/python deploy/staging/m14-fasit-artefakt.py \
        [--vert https://disponit.com] [--ut …]

RYDDER IKKE ETTER SEG (som de andre): tenantnavnene bærer runden.
Fasit-tenantene har ingen policy — ingenting bokføres.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

spec = importlib.util.spec_from_file_location(
    "m14_fasit", Path(__file__).with_name("m14_fasit.py"))
lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lib)

CREDFILER = {
    "DATABASE_URL": "/etc/disponit/api/DATABASE_URL",
    "DISPONIT_FAKTURASVEIP_URL":
        "/etc/disponit/fakturasveip/DISPONIT_FAKTURASVEIP_URL",
}


def _last_miljo() -> None:
    from db.hemmeligheter import last_credentials
    last_credentials()
    for nokkel, sti in CREDFILER.items():
        if nokkel in os.environ:
            continue
        f = Path(sti)
        if f.exists():
            os.environ[nokkel] = f.read_text(encoding="utf-8").strip()


def _koble(dsn: str):
    from db.pg import koble
    return koble(dsn)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vert", default="https://disponit.com")
    ap.add_argument("--ut", default=None)
    a = ap.parse_args()
    _last_miljo()
    dsn = {n: os.environ.get(n) for n in CREDFILER}
    mangler = [f"{n} ({CREDFILER[n]})" for n, v in dsn.items() if not v]
    if mangler:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "grunn": f"mangler {', '.join(mangler)}"}),
              file=sys.stderr)
        return 2
    from drift import fakturasveip
    sveipetid_ms = 0
    sveip_tenanter = 0

    def sveip():
        nonlocal sveipetid_ms, sveip_tenanter
        v = _koble(dsn["DISPONIT_FAKTURASVEIP_URL"])
        t0 = time.monotonic()
        try:
            r = fakturasveip.kjor(v)
        finally:
            v.close()
        sveipetid_ms = int((time.monotonic() - t0) * 1000)
        sveip_tenanter = r.tenanter
        if r.feilet or r.hoppet_over:
            raise SystemExit(f"AVBRUTT: sveipen feilet={r.feilet}"
                             f" hoppet_over={r.hoppet_over}")

    rt = _koble(dsn["DATABASE_URL"])
    try:
        kjoring = lib.kjor_sett(lib.ny_runde(), rt, sveip)
    finally:
        rt.close()
    from manifestskjema import m14_bevisrot_sha256
    ts = datetime.now(timezone.utc)
    art = lib.artefakt(kjoring, a.vert, ts.isoformat(), sveipetid_ms,
                       sveip_tenanter, m14_bevisrot_sha256())
    ut = Path(a.ut) if a.ut else (
        REPO / "deploy/staging/artefakter"
        / f"m14-fasit-v1-{ts.strftime('%Y%m%dT%H%M%SZ')}.json")
    ut.parent.mkdir(parents=True, exist_ok=True)
    ut.write_text(json.dumps(art, ensure_ascii=False, indent=2,
                             sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"hendelse": "m14_fasit", "artefakt": str(ut),
                      "bestatt": art["bestatt"],
                      **{k: art["maalt"][k] for k in lib.AKSER},
                      "sveipetid_ms": sveipetid_ms,
                      "sveip_tenanter": sveip_tenanter},
                     ensure_ascii=False))
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
