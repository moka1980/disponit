#!/usr/bin/env python3
"""Suiteartefaktet for m02 (m02-aksept-klarsignalet §3).

Kjører HELE testsuiten på staging-verten og skriver resultatet som
artefakt — med M-2s andel NAVNGITT og målt for seg (delingsbetingelsen
i RUTINER.md: et delt løp må navngi hvilken måling som beviser punktet
for nettopp denne modulen; fritekst er ikke en binding).

M-2s andel er append-only-portene (hele test_kjorer_og_kryptering.py)
og revisjonsloggens tre navngitte porter i test_api.py — evidensfelter,
aktør fra serverkontekst, idempotent replay uten ny loggpost.

BRUK (på verten, med testbase og testmiljø satt opp):
    DISPONIT_TEST_DSN=... DISPONIT_TEST_MIGRATOR_DSN=... \
    /opt/disponit/.venv/bin/python deploy/staging/m02-suite-artefakt.py \
        [--ut deploy/staging/artefakter/...json]

Tallene leses av pytests EGEN maskinlesbare rapport (--junit-xml) —
aldri av en regex over menneskelig oppsummeringstekst.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: M-2s andel — NAVNGITT, ikke antatt. Filen i sin helhet der modulen
#: eier hele måleflaten, node-id-er der den deler fil med M-1.
M2_ANDEL = (
    "platform/core/tests/test_kjorer_og_kryptering.py",
    "platform/core/tests/test_api.py::"
    "test_tillat_gir_loggpost_med_evidensfelter",
    "platform/core/tests/test_api.py::"
    "test_unntakshistorikk_far_aktor_fra_serverkontekst",
    "platform/core/tests/test_api.py::"
    "test_idempotent_replay_er_byteidentisk_uten_ny_loggpost",
    "platform/core/tests/test_api.py::"
    "test_unntaksskriv_feilet_ruller_ogsa_loggposten",
)


def _kjor(mal: list[str], junit: Path) -> tuple[int, int, int]:
    """pytest over `mal`. -> (tester, feilet+error, exitkode).

    Exitkoden MÅLES, den kastes ikke: junit-XML-en skrives også når
    kjøringen ble avbrutt underveis, og da beskriver den bare testene som
    rakk å bli ferdige — alle grønne, null failures, null errors. Et
    KeyboardInterrupt sent i suiten gir exit 2 med nettopp en slik XML,
    og uten koden er den umulig å skille fra en hel kjøring. En hel
    grønn suite er exit 0; alt annet er ikke et grønt artefakt.
    """
    p = subprocess.run(
        [sys.executable, "-m", "pytest", *mal, "-q",
         f"--junit-xml={junit}"],
        cwd=REPO / "platform/core",
        env={**__import__("os").environ,
             "PYTHONPATH": f"{REPO}/platform/core:{REPO}/platform"},
        capture_output=True)
    rot = ET.parse(junit).getroot()
    suite = rot if rot.tag == "testsuite" else rot.find("testsuite")
    tester = int(suite.get("tests", 0))
    roede = int(suite.get("failures", 0)) + int(suite.get("errors", 0))
    return tester, roede, p.returncode


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
        totalt, roede, kode = _kjor(["tests"], Path(tmp) / "alle.xml")
        m2, m2_roede, m2_kode = _kjor([str(REPO / p) if "::" not in p
                                       else str(REPO / p.split("::")[0])
                                       + "::" + p.split("::", 1)[1]
                                       for p in M2_ANDEL],
                                      Path(tmp) / "m2.xml")
    ts = datetime.now(timezone.utc).isoformat()
    art = {"krav_id": "m02-suite-v1", "ts": ts,
           "bestatt": roede == 0 and m2_roede == 0 and totalt > 0
           and kode == 0 and m2_kode == 0,
           "oppsett": {"modul": "m02_revisjonslogg", "commit": commit,
                       "vert": vert, "m2_filer": list(M2_ANDEL)},
           "maalt": {"tester_totalt": totalt, "tester_feilet": roede,
                     "m2_tester": m2, "m2_feilet": m2_roede,
                     "suite_exitkode": kode, "m2_exitkode": m2_kode}}
    ut = a.ut or (REPO / "deploy/staging/artefakter" /
                  f"m02-suite-v1-{ts[:19].replace(':', '').replace('-', '')}.json")
    ut.write_text(json.dumps(art, indent=2, ensure_ascii=False,
                             sort_keys=True) + "\n", encoding="utf-8")
    print(f"skrev {ut} (bestatt={art['bestatt']}, {totalt} tester,"
          f" m2-andel {m2})")
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":
    sys.exit(main())
