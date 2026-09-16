#!/usr/bin/env python3
"""Suiteartefaktet for M-26 (sertifiseringen 17/9).

Kjører HELE testsuiten på staging-verten og skriver resultatet som
artefakt — med M-26s andel NAVNGITT og målt for seg. Andelen er PINNET i
`manifestskjema.M26_SUITE_ANDEL` og leses derfra.

EGEN FIL, IKKE EN PARAMETER TIL m23s: `m23-suite-artefakt.py` ligger i
`M23_BEVISROT_FILER` — bytene er bevismateriale for et innsjekket
artefakt, og en delt hjelpemodul ville gjort det artefaktet ugyldig.

BRUK (på verten, fra et FULLT utsjekk, med FERSK testbase):
    DISPONIT_TEST_DSN=... DISPONIT_TEST_MIGRATOR_DSN=... \
    DISPONIT_TEST_PRISBOKSVEIP_DSN=... \
    /opt/disponit/.venv/bin/python deploy/staging/m26-suite-artefakt.py

SVEIPEROLLENS DSN ER PÅKREVD: `test_m26_fasit_port.py` hopper uten den,
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

from manifestskjema import (M26_SUITE_ANDEL,  # noqa: E402
                            _sjekk_grenser, m26_bevisrot_sha256,
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
        m26, m26_roede, m26_hoppet, m26_kode = _kjor(
            [str(REPO / sti) for sti in M26_SUITE_ANDEL],
            Path(tmp) / "m26.xml")
    ts = datetime.now(timezone.utc).isoformat()
    art = {"krav_id": "m26-suite-v1", "ts": ts, "bestatt": True,
           "oppsett": {"modul": "m26_prisbok", "commit": commit,
                       "vert": vert, "m26_filer": list(M26_SUITE_ANDEL),
                       "bevisrot_sha256": m26_bevisrot_sha256()},
           "maalt": {"tester_totalt": totalt, "tester_feilet": roede,
                     "tester_hoppet": hoppet,
                     "m26_tester": m26, "m26_feilet": m26_roede,
                     "m26_hoppet": m26_hoppet,
                     "suite_exitkode": kode, "m26_exitkode": m26_kode}}
    formfeil = valider_artefaktformat(art, "m26-suite-v1")
    grensefeil = _sjekk_grenser("m26-suite-v1", art)
    art["bestatt"] = not formfeil and not grensefeil
    ut = a.ut or (
        REPO / "deploy/staging/artefakter"
        / f"m26-suite-v1-{ts[:19].replace(':', '').replace('-', '')}Z.json")
    ut.parent.mkdir(parents=True, exist_ok=True)
    ut.write_text(json.dumps(art, indent=2, ensure_ascii=False,
                             sort_keys=True) + "\n", encoding="utf-8")
    print(f"skrev {ut} (bestatt={art['bestatt']},"
          f" {totalt - hoppet} kjørte av {totalt} tester,"
          f" m26-andel {m26 - m26_hoppet} av {m26})")
    for f in formfeil + grensefeil:
        print(f"  RØDT: {f}")
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":
    sys.exit(main())
