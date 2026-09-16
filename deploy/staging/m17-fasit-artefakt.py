#!/usr/bin/env python3
"""Staging-leddet for M-17s fasitartefakt (sertifiseringen 16/9).

Driver NØYAKTIG samme sett som CI-leddet (`m17_fasit.bygg_sett`) gjennom
de EKTE dørene på verten — runtime-rollen inn, regelrunden som
PLANARBEIDEREN, henvendelsessveipen som SVEIPEROLLEN, og køens eget svar
ut — og skriver artefaktet fra avvikene målingen fant.

BRUK (på verten, som root, med basen oppe):
    /opt/disponit/.venv/bin/python deploy/staging/m17-fasit-artefakt.py \
        [--vert https://disponit.com] \
        [--ut deploy/staging/artefakter/m17-fasit-v1-<ts>.json]

TRE DSN-ER, OG DET ER POENGET: runtime-rollen kan verken kjøre sveipen
(102 REVOKEr den) eller regelrunden (204 gir den til planarbeideren),
sveiperollen kan ikke skrive en henvendelse, og planarbeideren kan ikke
lese køen. Kjøringen må gå alle tre veier for å bevise kjeden — et skript
som gjorde alt med én allmektig rolle ville bevist en vei ingen går.

TILLITSGRENSEN: artefaktet beviser KJØRINGEN — settet, artefaktbyggeren,
sveipedriveren og regelrunden som innsjekkede bytes (`bevisrot_sha256`)
— ALDRI verten. Vertens tilstand bindes av deploymentkjeden.

RYDDER IKKE ETTER SEG, som m23: `henvendelse` er append-only (102
avviser DELETE). Tenantnavnene bærer runden, så to bevisrunder
kolliderer aldri, og radene står igjen som det de er: evidens.
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
    "m17_fasit", Path(__file__).with_name("m17_fasit.py"))
lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lib)

#: Unitenes EGNE credential-filer (`LoadCredential=`), som m23. Verdiene
#: når verken artefaktet, utskriften eller loggen.
CREDFILER = {
    "DATABASE_URL": "/etc/disponit/api/DATABASE_URL",
    "DISPONIT_HENVENDELSESVEIP_URL":
        "/etc/disponit/henvendelsessveip/DISPONIT_HENVENDELSESVEIP_URL",
    # disponit-plan.service: LoadCredential=DISPONIT_DATABASE_URL:…
    "DISPONIT_PLAN_URL": "/etc/disponit/plan/DISPONIT_DATABASE_URL",
    # KEK-EN, og det er forskjellen fra m23: settet skriver KRYPTERT tekst
    # (emne, kropp, adresse) for to nye tenanter, og hver tenant får sin
    # DEK pakket under KEK-en (`hent_eller_opprett_aktiv_dek`). Uten den
    # nekter `kryptering._kek()` å starte. Samme kilde som
    # `m6-etterslep-til-m17.py`: API-ets egen credential-fil, lest av
    # root, aldri skrevet ut.
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
    dsn.pop("DISPONIT_KEK")             # bare i miljøet, aldri i hendene
    if mangler:
        # NEKTER Å STARTE framfor å kjøre halve kjeden.
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "grunn": f"mangler {', '.join(mangler)}"}),
              file=sys.stderr)
        return 2

    from drift import henvendelsessveip
    from plan import stilleregler
    sveipetid_ms = 0
    sveip_tenanter = 0

    def regelrunde():
        v = _koble(dsn["DISPONIT_PLAN_URL"])
        try:
            r = stilleregler.kjor_en_runde(v)
        finally:
            v.close()
        if r.get("av"):
            raise SystemExit("AVBRUTT: regelrunden er slått av"
                             " (DISPONIT_STILLEREGLER=av) — en fasit uten"
                             " regelrunden måler ikke klassifiseringen")

    def sveip():
        nonlocal sveipetid_ms, sveip_tenanter
        v = _koble(dsn["DISPONIT_HENVENDELSESVEIP_URL"])
        t0 = time.monotonic()
        try:
            r = henvendelsessveip.kjor(v)
        finally:
            v.close()
        sveipetid_ms = int((time.monotonic() - t0) * 1000)
        sveip_tenanter = r.tenanter
        if r.feilet or r.hoppet_over or r.avkortet:
            raise SystemExit(
                f"AVBRUTT: sveipen feilet={r.feilet}"
                f" hoppet_over={r.hoppet_over} avkortet={r.avkortet}"
                " — en fasit målt på en halv sveip er ikke fasiten")

    rt = _koble(dsn["DATABASE_URL"])
    try:
        kjoring = lib.kjor_sett(lib.ny_runde(), rt, regelrunde, sveip)
    finally:
        rt.close()

    from manifestskjema import m17_bevisrot_sha256
    ts = datetime.now(timezone.utc)
    art = lib.artefakt(kjoring, a.vert, ts.isoformat(), sveipetid_ms,
                       sveip_tenanter, m17_bevisrot_sha256())
    ut = Path(a.ut) if a.ut else (
        REPO / "deploy/staging/artefakter"
        / f"m17-fasit-v1-{ts.strftime('%Y%m%dT%H%M%SZ')}.json")
    ut.parent.mkdir(parents=True, exist_ok=True)
    ut.write_text(json.dumps(art, ensure_ascii=False, indent=2,
                             sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"hendelse": "m17_fasit", "artefakt": str(ut),
                      "bestatt": art["bestatt"],
                      **{k: art["maalt"][k] for k in lib.AKSER},
                      "sveipetid_ms": sveipetid_ms,
                      "sveip_tenanter": sveip_tenanter},
                     ensure_ascii=False))
    # EXIT 1 VED RØDT: filen skrives likevel — et rødt artefakt som
    # finnes er ærligere enn et grønt som ble valgt.
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
