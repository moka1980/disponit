#!/usr/bin/env python3
"""Suiteartefaktet for M-14 (sertifiseringen 17/9).

Kjører HELE testsuiten på staging-verten og skriver resultatet som
artefakt — med M-14s andel NAVNGITT og målt for seg. Andelen er PINNET i
`manifestskjema.M14_SUITE_ANDEL` og leses derfra.

EGEN FIL, IKKE EN PARAMETER TIL m23s: `m23-suite-artefakt.py` ligger i
`M23_BEVISROT_FILER` — bytene er bevismateriale for et innsjekket
artefakt, og en delt hjelpemodul ville gjort det artefaktet ugyldig.

BRUK (på verten, fra et FULLT utsjekk, med FERSK testbase):
    DISPONIT_TEST_DSN=... DISPONIT_TEST_MIGRATOR_DSN=... \
    DISPONIT_TEST_FAKTURASVEIP_DSN=... \
    /opt/disponit/.venv/bin/python deploy/staging/m14-suite-artefakt.py

SVEIPEROLLENS DSN ER PÅKREVD: `test_m14_fasit_port.py` hopper uten den,
og porten krever null hoppede i andelen.

Tallene leses av pytests EGEN junit-XML, aldri av oppsummeringsteksten,
og `bestatt` settes av evidensportens egne funksjoner.
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

from manifestskjema import (M14_SUITE_ANDEL,  # noqa: E402
                            _sjekk_grenser, m14_bevisrot_sha256,
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
    ap.add_argument("--ut", type=Path)
    a = ap.parse_args()
    commit = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    vert = subprocess.run(["hostname"], capture_output=True,
                          text=True).stdout.strip() or "ukjent"
    with tempfile.TemporaryDirectory() as tmp:
        totalt, roede, hoppet, kode = _kjor(["tests"],
                                            Path(tmp) / "alle.xml")
        m14, m14_roede, m14_hoppet, m14_kode = _kjor(
            [str(REPO / sti) for sti in M14_SUITE_ANDEL],
            Path(tmp) / "m14.xml")
    ts = datetime.now(timezone.utc).isoformat()
    art = {"krav_id": "m14-suite-v1", "ts": ts, "bestatt": True,
           "oppsett": {"modul": "m14_fakturakontroll", "commit": commit,
                       "vert": vert, "m14_filer": list(M14_SUITE_ANDEL),
                       "bevisrot_sha256": m14_bevisrot_sha256()},
           "maalt": {"tester_totalt": totalt, "tester_feilet": roede,
                     "tester_hoppet": hoppet,
                     "m14_tester": m14, "m14_feilet": m14_roede,
                     "m14_hoppet": m14_hoppet,
                     "suite_exitkode": kode, "m14_exitkode": m14_kode}}
    formfeil = valider_artefaktformat(art, "m14-suite-v1")
    grensefeil = _sjekk_grenser("m14-suite-v1", art)
    art["bestatt"] = not formfeil and not grensefeil
    ut = a.ut or (
        REPO / "deploy/staging/artefakter"
        / f"m14-suite-v1-{ts[:19].replace(':', '').replace('-', '')}Z.json")
    ut.parent.mkdir(parents=True, exist_ok=True)
    ut.write_text(json.dumps(art, indent=2, ensure_ascii=False,
                             sort_keys=True) + "\n", encoding="utf-8")
    print(f"skrev {ut} (bestatt={art['bestatt']},"
          f" {totalt - hoppet} kjørte av {totalt} tester,"
          f" m14-andel {m14 - m14_hoppet} av {m14})")
    for f in formfeil + grensefeil:
        print(f"  RØDT: {f}")
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":
    sys.exit(main())
