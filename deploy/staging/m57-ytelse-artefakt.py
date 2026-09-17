#!/usr/bin/env python3
"""Ytelsesartefaktet for M-57 (`m57-ytelse-v1`, 17/9).

ÉN EKTE BUNT PÅ TAKET gjennom hele kjeden på verten — bestilt som
bestiller gjennom API-et, claimet og evaluert av `disponit-m57` mot den
lokale modellen, promotert og kvittert — og MÅLT på oppdragsradens egne
tidsstempler: første claim → terminalstatus. Skriptet bestiller ingenting
og regner ingenting selv; det leser raden og kandidatlageret og skriver
tallene. Antallet er de EVALUERTE kandidatene (artefaktene), ikke det
bestilte tallet — en bunt som ble avkortet skal vise det.

BRUK (på verten, med migratorens DSN i miljøet):
    DISPONIT_MIGRATOR_URL=... /opt/disponit/.venv/bin/python \\
        deploy/staging/m57-ytelse-artefakt.py --oppdrag 111 --tenant t-m57fasit [--ut …]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))

from manifestskjema import (_sjekk_grenser, m57_ytelse_bevisrot_sha256,  # noqa: E402
                            valider_artefaktformat)


def _modell_digest() -> str:
    """Konfigurasjonens påstand om modellen — arbeiderens miljøfil.
    FAIL-CLOSED: et ytelsesartefakt uten modellidentitet beviser en
    kjøring av en ukjent modell, og skrives ikke."""
    digest = os.environ.get("DISPONIT_M57_MODELL_DIGEST", "")
    try:
        for linje in Path("/etc/disponit/m57/konfig").read_text(
                encoding="utf-8").splitlines():
            if linje.startswith("DISPONIT_M57_MODELL_DIGEST="):
                digest = linje.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    if not digest:
        raise SystemExit("AVBRUTT: DISPONIT_M57_MODELL_DIGEST mangler —"
                         " artefaktet skrives ikke uten modellidentitet")
    return digest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--oppdrag", type=int, required=True)
    ap.add_argument("--tenant", required=True,
                    help="oppdragets tenant — RLS (FORCE) viser raden bare"
                         " i sin egen kontekst")
    ap.add_argument("--vert", default="disponit-srv")
    ap.add_argument("--ut", type=Path)
    a = ap.parse_args()
    dsn = os.environ.get("DISPONIT_MIGRATOR_URL")
    if not dsn:
        print("AVBRUTT: DISPONIT_MIGRATOR_URL mangler", file=sys.stderr)
        return 2
    import psycopg
    with psycopg.connect(dsn) as c:
        # RLS med FORCE: raden finnes bare i sin egen tenantkontekst.
        c.execute("SELECT set_config('disponit.tenant', %s, true)", (a.tenant,))
        rad = c.execute(
            "SELECT tenant, status, forste_claim_ts, status_ts,"
            " kvittering->>'resultat' FROM oppdrag WHERE id=%s AND tenant=%s",
            (a.oppdrag, a.tenant)).fetchone()
        if rad is None:
            print(f"AVBRUTT: oppdrag {a.oppdrag} finnes ikke i {a.tenant}",
                  file=sys.stderr)
            return 2
        tenant, status, claim, slutt, resultat = rad
        evaluerte = c.execute(
            "SELECT count(*) FROM kandidat_evalueringsartefakt e"
            " JOIN rekrutteringsprosess p ON p.tenant=e.tenant"
            "  AND p.prosess_id=e.prosess_id"
            " WHERE p.tenant=%s AND p.oppdrag_id=%s",
            (tenant, a.oppdrag)).fetchone()[0]
        c.rollback()
    if claim is None or slutt is None:
        print(f"AVBRUTT: oppdrag {a.oppdrag} har ikke begge tidsstemplene"
              f" (status {status})", file=sys.stderr)
        return 2
    minutter = math.ceil((slutt - claim).total_seconds() / 60)
    ts = datetime.now(timezone.utc).isoformat()
    art = {"krav_id": "m57-ytelse-v1", "ts": ts, "bestatt": True,
           "oppsett": {"modul": "m57_ats", "vert": a.vert,
                       "oppdrag_id": a.oppdrag, "tenant": tenant,
                       "bevisrot_sha256": m57_ytelse_bevisrot_sha256(),
                       "modell_digest": _modell_digest()},
           "maalt": {"ytelse_full_bunt_soknader": int(evaluerte),
                     "ytelse_full_bunt_minutter": int(minutter),
                     "resultat": resultat or status,
                     "forste_claim_ts": claim.isoformat(),
                     "status_ts": slutt.isoformat()}}
    formfeil = valider_artefaktformat(art, "m57-ytelse-v1")
    grensefeil = _sjekk_grenser("m57-ytelse-v1", art)
    art["bestatt"] = not formfeil and not grensefeil
    ut = a.ut or (REPO / "deploy/staging/artefakter"
                  / f"m57-ytelse-v1-{ts[:19].replace(':', '').replace('-', '')}Z.json")
    ut.parent.mkdir(parents=True, exist_ok=True)
    ut.write_text(json.dumps(art, indent=2, ensure_ascii=False, sort_keys=True)
                  + "\n", encoding="utf-8")
    print(f"skrev {ut} (bestatt={art['bestatt']}, {evaluerte} søknader på"
          f" {minutter} min, resultat {art['maalt']['resultat']})")
    for f in formfeil + grensefeil:
        print(f"  RØDT: {f}")
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":
    sys.exit(main())
