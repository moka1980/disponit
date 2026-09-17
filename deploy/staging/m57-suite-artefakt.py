#!/usr/bin/env python3
"""Suiteartefaktet for M-57 (sertifiseringen 17/9 — suitepunktet alene; de fem andre
punktene i `m57-v1` er dokumentert blokkert i manifestet).

Kjører HELE testsuiten på staging-verten og skriver resultatet som
artefakt — med M-57s andel NAVNGITT og målt for seg. Andelen er PINNET i
`manifestskjema.M57_SUITE_ANDEL` og leses derfra.

EGEN FIL, IKKE EN PARAMETER TIL m23s: `m23-suite-artefakt.py` ligger i
`M23_BEVISROT_FILER` — bytene er bevismateriale for et innsjekket
artefakt, og en delt hjelpemodul ville gjort det artefaktet ugyldig.

BRUK (på verten, fra et FULLT utsjekk, med FERSK testbase):
    DISPONIT_TEST_DSN=... DISPONIT_TEST_MIGRATOR_DSN=... \
    /opt/disponit/.venv/bin/python deploy/staging/m57-suite-artefakt.py

ALLE ROLLE-DSN-ENE ER PÅKREVD: M-57s åtte filer hopper uten dem, og
porten krever null hoppede i andelen.

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

from manifestskjema import (M57_SUITE_ANDEL,  # noqa: E402
                            _sjekk_grenser, m57_bevisrot_sha256,
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
        m57, m57_roede, m57_hoppet, m57_kode = _kjor(
            [str(REPO / sti) for sti in M57_SUITE_ANDEL],
            Path(tmp) / "m57.xml")
    ts = datetime.now(timezone.utc).isoformat()
    art = {"krav_id": "m57-suite-v1", "ts": ts, "bestatt": True,
           "oppsett": {"modul": "m57_ats", "commit": commit,
                       "vert": vert, "m57_filer": list(M57_SUITE_ANDEL),
                       "bevisrot_sha256": m57_bevisrot_sha256()},
           "maalt": {"tester_totalt": totalt, "tester_feilet": roede,
                     "tester_hoppet": hoppet,
                     "m57_tester": m57, "m57_feilet": m57_roede,
                     "m57_hoppet": m57_hoppet,
                     "suite_exitkode": kode, "m57_exitkode": m57_kode}}
    formfeil = valider_artefaktformat(art, "m57-suite-v1")
    grensefeil = _sjekk_grenser("m57-suite-v1", art)
    art["bestatt"] = not formfeil and not grensefeil
    ut = a.ut or (
        REPO / "deploy/staging/artefakter"
        / f"m57-suite-v1-{ts[:19].replace(':', '').replace('-', '')}Z.json")
    ut.parent.mkdir(parents=True, exist_ok=True)
    ut.write_text(json.dumps(art, indent=2, ensure_ascii=False,
                             sort_keys=True) + "\n", encoding="utf-8")
    print(f"skrev {ut} (bestatt={art['bestatt']},"
          f" {totalt - hoppet} kjørte av {totalt} tester,"
          f" m57-andel {m57 - m57_hoppet} av {m57})")
    for f in formfeil + grensefeil:
        print(f"  RØDT: {f}")
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":
    sys.exit(main())
