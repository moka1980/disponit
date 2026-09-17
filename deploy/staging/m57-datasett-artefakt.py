#!/usr/bin/env python3
"""Datasettartefaktet for M-57 (`m57-datasett-v1`).

Punktet heter «syntetisk datasett likt lokalt» og måles i M-02s
fordelingsform: DATASETTETS BYTES bæres av artefaktet fra staging-leddet
(`datasett_sha_staging` — golden-fila driveren faktisk leste på verten),
og porten (`_grenser_m57_datasett`) krever likhet med de innsjekkede
bytene treet bærer (`datasett_sha_lokal`, re-regnet av porten). Glir de
fra hverandre, er punktet rødt.

FASITEN er golden v2s `forventet_oppfylt` — et REGRESJONSANKER: modellens
målte, deterministiske dom (31/8, temp 0). Bunten er de 24 tekstene
EKSAKT (ingen hilsen, intet navn i teksten — ytelsesbunten 17/9 la til
«Med vennlig hilsen Kandidat0001», som blindingen maskerte til «[NAVN-1]»,
og fem tekster ble dømt annerledes av det). Skriptet bestiller ingenting
og dømmer ingenting selv: det leser kandidatartefaktene i lageret,
matcher hver på golden-teksten og teller avvik fra ankeret per tekst —
tallene REGNES AV RADENE, og porten re-summerer dem.

BRUK (på verten, migratorens DSN i miljøet):
    DISPONIT_MIGRATOR_URL=... /opt/disponit/.venv/bin/python \\
        deploy/staging/m57-datasett-artefakt.py --oppdrag 146 --tenant t-m57fasit [--ut …]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))

from manifestskjema import (M57_DATASETT_FIL, _sjekk_grenser,  # noqa: E402
                            m57_datasett_bevisrot_sha256,
                            valider_artefaktformat)

GOLDEN = REPO / M57_DATASETT_FIL


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--oppdrag", type=int, required=True)
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--vert", default="disponit-srv")
    ap.add_argument("--lokal-sha", required=True,
                    help="sha256 av golden-fila regnet LOKALT (CI-leddet) —"
                         " den andre halvdelen av «likt lokalt»")
    ap.add_argument("--ut", type=Path)
    a = ap.parse_args()
    dsn = os.environ.get("DISPONIT_MIGRATOR_URL")
    if not dsn:
        print("AVBRUTT: DISPONIT_MIGRATOR_URL mangler", file=sys.stderr)
        return 2
    golden_bytes = GOLDEN.read_bytes()
    golden = json.loads(golden_bytes.decode("utf-8"))
    import psycopg
    with psycopg.connect(dsn) as c:
        c.execute("SELECT set_config('disponit.tenant', %s, true)", (a.tenant,))
        rad = c.execute(
            "SELECT status, kvittering->>'resultat', forste_claim_ts, status_ts"
            " FROM oppdrag WHERE id=%s AND tenant=%s",
            (a.oppdrag, a.tenant)).fetchone()
        if rad is None:
            print(f"AVBRUTT: oppdrag {a.oppdrag} finnes ikke i {a.tenant}",
                  file=sys.stderr)
            return 2
        status, resultat, t0, t1 = rad
        rader = c.execute(
            "SELECT e.artefakt->>'kildetekst', e.artefakt->'oppfylt'"
            " FROM kandidat_evalueringsartefakt e"
            " JOIN rekrutteringsprosess p ON p.tenant=e.tenant"
            "  AND p.prosess_id=e.prosess_id"
            " WHERE p.tenant=%s AND p.oppdrag_id=%s AND e.slettet_ts IS NULL",
            (a.tenant, a.oppdrag)).fetchall()
        c.rollback()

    # Matchingen er på TEKSTEN: kildeteksten i artefaktet er søknadens
    # tekst slik modellen så den — BLINDET, så navn kan stå som [NAVN-n].
    # Derfor matches de første 80 tegnene (unike i ankeret, pinnet av
    # porten), og en søknad som ikke matcher nøyaktig én golden-tekst
    # telles som umatchet — aldri som «riktig».
    per: dict[str, dict] = {g["id"]: {"golden_id": g["id"], "antall": 0,
                                       "forventet": g["forventet_oppfylt"],
                                       "dommer": {}, "avvik": 0}
                            for g in golden}
    umatchet = 0
    for kildetekst, oppfylt in rader:
        treff = [g for g in golden
                 if (kildetekst or "").strip()[:80] == g["tekst"].strip()[:80]]
        if len(treff) != 1:
            umatchet += 1
            continue
        p = per[treff[0]["id"]]
        dom = json.dumps(oppfylt, sort_keys=True, ensure_ascii=False)
        p["antall"] += 1
        p["dommer"][dom] = p["dommer"].get(dom, 0) + 1
        if oppfylt != p["forventet"]:
            p["avvik"] += 1
    ts = datetime.now(timezone.utc).isoformat()
    art = {
        "krav_id": "m57-datasett-v1", "ts": ts, "bestatt": True,
        "oppsett": {"modul": "m57_ats", "vert": a.vert, "tenant": a.tenant,
                    "oppdrag_id": a.oppdrag, "datasett_fil": M57_DATASETT_FIL,
                    "bevisrot_sha256": m57_datasett_bevisrot_sha256(),
                    "resultat": resultat or status,
                    "forste_claim_ts": t0.isoformat() if t0 else None,
                    "status_ts": t1.isoformat() if t1 else None},
        "maalt": {"bunt_soknader": sum(p["antall"] for p in per.values()),
                  "fasitavvik": sum(p["avvik"] for p in per.values()),
                  "umatchet": umatchet,
                  "datasett_sha_lokal": a.lokal_sha.strip().lower(),
                  "datasett_sha_staging": hashlib.sha256(golden_bytes).hexdigest(),
                  "per_golden": sorted(per.values(), key=lambda p: p["golden_id"])},
    }
    formfeil = valider_artefaktformat(art, "m57-datasett-v1")
    grensefeil = _sjekk_grenser("m57-datasett-v1", art)
    art["bestatt"] = not formfeil and not grensefeil
    ut = a.ut or (REPO / "deploy/staging/artefakter"
                  / f"m57-datasett-v1-{ts[:19].replace(':', '').replace('-', '')}Z.json")
    ut.parent.mkdir(parents=True, exist_ok=True)
    ut.write_text(json.dumps(art, indent=2, ensure_ascii=False, sort_keys=True)
                  + "\n", encoding="utf-8")
    print(f"skrev {ut} (bestatt={art['bestatt']}, {art['maalt']['bunt_soknader']}"
          f" søknader, {art['maalt']['fasitavvik']} avvik, {umatchet} umatchet)")
    for f in formfeil + grensefeil:
        print(f"  RØDT: {f}")
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":
    sys.exit(main())
