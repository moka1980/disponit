#!/usr/bin/env python3
"""Staging-leddet for M-6s fasitartefakt (sertifiseringen 16/9).

Driver NØYAKTIG samme sett som CI-leddet (`m6_fasit.kjor_sett`) gjennom
de EKTE dørene på verten — kilden, utkastet og slettingen som runtime,
inntaket og utsendingen som PLANARBEIDEREN (rollen som gjør det i
planrunden), Graph byttet ut med den riggede transporten — og skriver
artefaktet fra avvikene målingen fant.

BRUK (på verten, som root, med basen oppe):
    /opt/disponit/.venv/bin/python deploy/staging/m6-fasit-artefakt.py \
        [--vert https://disponit.com] [--ut deploy/staging/artefakter/…]

TO DSN-ER OG KEK-EN: runtime kan ikke lese kildens credential-trio
(innhenterens rolle har den), planarbeideren kan ikke skrive et utkast;
begge trengs for å bevise kjeden. KEK-en pakker DEK-en for de to nye
tenantene. M365-miljøet settes til FASIT-VERDIER som aldri brukes: den
riggede veksleren returnerer tokenet selv, og ingen kall når Microsoft.

RYDDER IKKE ETTER SEG (som m17/m23): tenantnavnene bærer runden.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

spec = importlib.util.spec_from_file_location(
    "m6_fasit", Path(__file__).with_name("m6_fasit.py"))
lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lib)

CREDFILER = {
    "DATABASE_URL": "/etc/disponit/api/DATABASE_URL",
    "DISPONIT_PLAN_URL": "/etc/disponit/plan/DISPONIT_DATABASE_URL",
    "DISPONIT_KEK": "/etc/disponit/api/DISPONIT_KEK",
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
    # M365-konfigurasjonen må FINNES for at innhenteren skal starte —
    # verdiene brukes aldri: veksleren er rigget.
    for n in ("DISPONIT_M365_CLIENT_ID", "DISPONIT_M365_CLIENT_SECRET",
              "DISPONIT_M365_TENANT"):
        os.environ.setdefault(n, "fasit")


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
    dsn.pop("DISPONIT_KEK")
    from plan import epost
    if epost.er_av() or not epost._bro_pa():
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "grunn": "inntaket eller broen er slått av — en"
                                   " fasit uten dem måler ikke kjeden"}),
              file=sys.stderr)
        return 2
    rt = _koble(dsn["DATABASE_URL"])
    pa = _koble(dsn["DISPONIT_PLAN_URL"])
    try:
        kjoring = lib.kjor_sett(lib.ny_runde(), rt, pa)
    finally:
        rt.close()
        pa.close()
    from manifestskjema import m6_bevisrot_sha256
    ts = datetime.now(timezone.utc)
    art = lib.artefakt(kjoring, a.vert, ts.isoformat(), m6_bevisrot_sha256())
    ut = Path(a.ut) if a.ut else (
        REPO / "deploy/staging/artefakter"
        / f"m6-fasit-v1-{ts.strftime('%Y%m%dT%H%M%SZ')}.json")
    ut.parent.mkdir(parents=True, exist_ok=True)
    ut.write_text(json.dumps(art, ensure_ascii=False, indent=2,
                             sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"hendelse": "m6_fasit", "artefakt": str(ut),
                      "bestatt": art["bestatt"],
                      **{k: art["maalt"][k] for k in lib.AKSER},
                      "innhentingstid_ms": art["maalt"]["innhentingstid_ms"]},
                     ensure_ascii=False))
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
