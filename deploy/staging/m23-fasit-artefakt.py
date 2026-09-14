#!/usr/bin/env python3
"""Staging-leddet for M-23s fasitartefakt (sertifiseringen 14/9).

Driver NØYAKTIG samme sett som CI-leddet (`m23_fasit.bygg_sett`) gjennom
de EKTE dørene på verten — runtime-rollen inn, fordringssveipen som
sveiperollen, og flatens eget svar ut — og skriver artefaktet fra
avvikene målingen fant.

BRUK (på verten, som root, med basen oppe):
    /opt/disponit/.venv/bin/python deploy/staging/m23-fasit-artefakt.py \
        [--vert https://disponit.com] \
        [--ut deploy/staging/artefakter/m23-fasit-v1-<ts>.json]

TO DSN-ER, OG DET ER POENGET: runtime-rollen kan ikke kjøre sveipen (104
REVOKEr den), og sveiperollen kan ikke skrive en fordring. Kjøringen må
derfor gå begge veier for å bevise kjeden, og et skript som gjorde alt
med én allmektig rolle ville bevist en vei ingen går.

TILLITSGRENSEN: artefaktet beviser KJØRINGEN — settet, artefaktbyggeren
og sveipedriveren som innsjekkede bytes (`bevisrot_sha256`) — ALDRI
verten. Vertens tilstand bindes av deploymentkjeden.

RYDDER IKKE ETTER SEG, og det er villet: `fordring` kan ikke slettes
(vakten i 104 avviser både DELETE og TRUNCATE — et krav ettergis med
begrunnelse). Tenantnavnene bærer runden, så to bevisrunder kolliderer
aldri, og radene står igjen som det de er: evidens.
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
    "m23_fasit", Path(__file__).with_name("m23_fasit.py"))
lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lib)

#: DE SAMME FILENE UNITENE PEKER PÅ, og det er hele poenget: DSN-ene er
#: `LoadCredential=`, ikke `EnvironmentFile=` — de står ikke i
#: `staging.env`, bare i hver sin root-eide fil. En produsent som leste
#: miljøfilen ville fått `None` for begge og nektet å starte, eller —
#: verre — falt tilbake på en DSN som ikke er jobbens egen.
#:
#: Utenfor systemd er `db.hemmeligheter.last_credentials()` en no-op
#: (ingen `$CREDENTIALS_DIRECTORY`), så filene leses her, direkte, som
#: root. Verdiene blir stående i prosessens eget miljø og når verken
#: artefaktet, utskriften eller loggen.
CREDFILER = {
    # disponit-api.service: LoadCredential=DATABASE_URL:…
    "DATABASE_URL": "/etc/disponit/api/DATABASE_URL",
    # disponit-fordringssveip.service:
    #   LoadCredential=DISPONIT_FORDRINGSVEIP_URL:…
    "DISPONIT_FORDRINGSVEIP_URL":
        "/etc/disponit/fordringssveip/DISPONIT_FORDRINGSVEIP_URL",
}


def _last_miljo() -> None:
    """Hydrerer DSN-ene fra unitenes egne credential-filer.

    `setdefault` gjør rekkefølgen ufarlig: en eksplisitt satt variabel
    vinner alltid over filen, så en kjøring mot en testbase aldri kan
    arve stagings hemmeligheter ved et uhell. Samme form og samme
    begrunnelse som `db.hemmeligheter`.
    """
    from db.hemmeligheter import last_credentials
    last_credentials()          # under systemd: den vanlige veien
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
    runtime_dsn = os.environ.get("DATABASE_URL")
    sveip_dsn = os.environ.get("DISPONIT_FORDRINGSVEIP_URL")
    mangler = [f"{n} ({CREDFILER[n]})"
               for n, v in (("DATABASE_URL", runtime_dsn),
                            ("DISPONIT_FORDRINGSVEIP_URL", sveip_dsn))
               if not v]
    if mangler:
        # NEKTER Å STARTE framfor å kjøre halve kjeden: en runde som
        # registrerte settet og aldri sveipet ville rapportert 21
        # funnavvik og sett ut som en regresjon i modulen.
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "grunn": f"mangler {', '.join(mangler)}"}),
              file=sys.stderr)
        return 2

    from drift import fordringssveip
    sveipetid_ms = 0
    sveip_tenanter = 0

    def sveip():
        nonlocal sveipetid_ms, sveip_tenanter
        v = _koble(sveip_dsn)
        t0 = time.monotonic()
        try:
            r = fordringssveip.kjor(v)
        finally:
            v.close()
        sveipetid_ms = int((time.monotonic() - t0) * 1000)
        sveip_tenanter = r.tenanter
        if r.feilet or r.hoppet_over or r.avkortet:
            raise SystemExit(
                f"AVBRUTT: sveipen feilet={r.feilet}"
                f" hoppet_over={r.hoppet_over} avkortet={r.avkortet}"
                " — en fasit målt på en halv sveip er ikke fasiten")

    rt = _koble(runtime_dsn)
    try:
        kjoring = lib.kjor_sett(lib.ny_runde(), rt, sveip)
    finally:
        rt.close()

    sys.path.insert(0, str(REPO / "platform/core"))
    from manifestskjema import m23_bevisrot_sha256

    ts = datetime.now(timezone.utc)
    art = lib.artefakt(kjoring, a.vert, ts.isoformat(), sveipetid_ms,
                       sveip_tenanter, m23_bevisrot_sha256())
    ut = Path(a.ut) if a.ut else (
        REPO / "deploy/staging/artefakter"
        / f"m23-fasit-v1-{ts.strftime('%Y%m%dT%H%M%SZ')}.json")
    ut.parent.mkdir(parents=True, exist_ok=True)
    ut.write_text(json.dumps(art, ensure_ascii=False, indent=2,
                             sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"hendelse": "m23_fasit", "artefakt": str(ut),
                      "bestatt": art["bestatt"],
                      "funnavvik": art["maalt"]["funnavvik"],
                      "botteavvik": art["maalt"]["botteavvik"],
                      "sveipetid_ms": sveipetid_ms,
                      "sveip_tenanter": sveip_tenanter},
                     ensure_ascii=False))
    # EXIT 1 VED RØDT: et artefakt som ikke består skal ikke se ut som en
    # vellykket kjøring i journalen. Filen skrives likevel — et rødt
    # artefakt som finnes er ærligere enn et grønt som ble valgt.
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
