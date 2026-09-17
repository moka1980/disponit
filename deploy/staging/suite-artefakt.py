#!/usr/bin/env python3
"""Suiteartefaktet for ÉN modul — samme produsent for alle (17/9).

De sju første modulene fikk hver sin kopi av dette skriptet: 103 linjer,
forskjellig bare i modul-id og fillisten. Med 46 moduler igjen er det
ikke en lest, det er en avskrift. Her er den ene formen; modulen velges
med `--modul`, og andelen leses av `manifestskjema.SUITE_ANDEL`, som
pinner den.

Skriptet KJØRER og TELLER, det dømmer ikke: tallene kommer fra pytests
egen junit-XML (aldri fra oppsummeringsteksten), og
`manifestskjema._grenser_generisk_suite` avgjør. To kjøringer, som i de
sju: hele suiten først, så modulens andel for seg — en andel som bare
telles inne i totalen kan skjule at nettopp den var hoppet over.

BRUK (på verten, fra utsjekket, med rolle-DSN-ene i miljøet):
    python deploy/staging/suite-artefakt.py --modul m19_adresse [--ut …]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))

from manifestskjema import (SUITE_ANDEL, KRAVGRENSER,  # noqa: E402
                            _sjekk_grenser,
                            generisk_suite_bevisrot_sha256,
                            valider_artefaktformat)


def _kjor(mal: list[str], junit: Path) -> tuple[int, int, int, int]:
    """pytest over `mal`. -> (tester, feilet+error, hoppet, exitkode)."""
    p = subprocess.run(
        [sys.executable, "-m", "pytest", *mal, "-q",
         "-p", "no:cacheprovider", f"--junit-xml={junit}"],
        cwd=REPO / "platform/core",
        env={**os.environ,
             "PYTHONPATH": f"{REPO}/platform/core:{REPO}/platform"},
        capture_output=True)
    rot = ET.parse(junit).getroot()
    suite = rot if rot.tag == "testsuite" else rot.find("testsuite")
    tester = int(suite.get("tests", 0))
    roede = int(suite.get("failures", 0)) + int(suite.get("errors", 0))
    hoppet = int(suite.get("skipped", 0))
    return tester, roede, hoppet, p.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modul", required=True,
                    help="modul-id, f.eks. m19_adresse")
    ap.add_argument("--ut", type=Path)
    a = ap.parse_args()
    if a.modul not in SUITE_ANDEL:
        raise SystemExit(
            f"AVBRUTT: {a.modul} har ingen registrert suiteandel —"
            " `registrer_suitegrense` i manifestskjema pinner den FØR"
            " kjøringen (§0), og en andel utledet her ville vært"
            " produsentens egen påstand")
    krav = next(k for k, g in KRAVGRENSER.items()
                if isinstance(g, dict) and g.get("modul") == a.modul
                and k.endswith("-suite-v1"))
    andel = SUITE_ANDEL[a.modul]
    commit = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    vert = subprocess.run(["hostname"], capture_output=True,
                          text=True).stdout.strip() or "ukjent"
    with tempfile.TemporaryDirectory() as tmp:
        totalt, roede, hoppet, kode = _kjor(["tests"], Path(tmp) / "alle.xml")
        am, a_roede, a_hoppet, a_kode = _kjor(
            [str(REPO / sti) for sti in andel], Path(tmp) / "andel.xml")
    ts = datetime.now(timezone.utc).isoformat()
    art = {"krav_id": krav, "ts": ts, "bestatt": True,
           "oppsett": {"modul": a.modul, "commit": commit, "vert": vert,
                       "andel_filer": list(andel),
                       "bevisrot_sha256": generisk_suite_bevisrot_sha256(a.modul)},
           "maalt": {"tester_totalt": totalt, "tester_feilet": roede,
                     "tester_hoppet": hoppet,
                     "andel_tester": am, "andel_feilet": a_roede,
                     "andel_hoppet": a_hoppet,
                     "suite_exitkode": kode, "andel_exitkode": a_kode}}
    formfeil = valider_artefaktformat(art, krav)
    grensefeil = _sjekk_grenser(krav, art)
    art["bestatt"] = not formfeil and not grensefeil
    ut = a.ut or (REPO / "deploy/staging/artefakter"
                  / f"{krav}-{ts[:19].replace(':', '').replace('-', '')}Z.json")
    ut.parent.mkdir(parents=True, exist_ok=True)
    ut.write_text(json.dumps(art, indent=2, ensure_ascii=False,
                             sort_keys=True) + "\n", encoding="utf-8")
    print(f"skrev {ut} (bestatt={art['bestatt']},"
          f" {totalt - hoppet} kjørte av {totalt} tester,"
          f" {a.modul}-andel {am - a_hoppet} av {am})")
    for f in formfeil + grensefeil:
        print(f"  RØDT: {f}")
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":
    sys.exit(main())
