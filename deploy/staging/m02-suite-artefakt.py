#!/usr/bin/env python3
"""Suiteartefaktet for m02 (m02-aksept-klarsignalet §3).

Kjører HELE testsuiten på staging-verten og skriver resultatet som
artefakt — med M-2s andel NAVNGITT og målt for seg (delingsbetingelsen
i RUTINER.md: et delt løp må navngi hvilken måling som beviser punktet
for nettopp denne modulen; fritekst er ikke en binding).

M-2s andel er PINNET i `manifestskjema.M02_SUITE_ANDEL` og leses derfra:
append-only-portene i test_pg_og_attestering.py og revisjonsloggens fire
porter i test_api.py (evidensfelter, aktør fra serverkontekst, idempotent
replay uten ny loggpost, rullet loggpost ved feilet unntaksskriv).
Utvalget står ETT sted fordi porten og produsenten ellers kunne gli fra
hverandre — og da hadde artefaktet navngitt ett utvalg mens akseptporten
godtok et annet.

BRUK (på verten, med testbase og testmiljø satt opp):
    DISPONIT_TEST_DSN=... DISPONIT_TEST_MIGRATOR_DSN=... \
    /opt/disponit/.venv/bin/python deploy/staging/m02-suite-artefakt.py \
        [--ut deploy/staging/artefakter/...json]

Tallene leses av pytests EGEN maskinlesbare rapport (--junit-xml) —
aldri av en regex over menneskelig oppsummeringstekst.

Artefaktet MÅLES her, men VALIDERES av evidensporten: `bestatt` og
exitkoden settes av `valider_artefaktformat` + `_sjekk_grenser` — samme
funksjoner CI kjører. En produsent med sine egne, mildere betingelser
melder grønt om artefakter porten feller, og bruker opp oppmerksomheten
før noen ser porten.
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
sys.path.insert(0, str(REPO / "platform/core"))

#: M-2s andel og grensene den måles mot — hentet fra akseptporten selv,
#: aldri gjentatt her. To lister som skal være like, er før eller siden to
#: ulike lister; denne leser den ENE.
from manifestskjema import (M02_SUITE_ANDEL as M2_ANDEL,  # noqa: E402
                            _sjekk_grenser, valider_artefaktformat)


def _kjor(mal: list[str], junit: Path) -> tuple[int, int, int, int]:
    """pytest over `mal`. -> (tester, feilet+error, hoppet, exitkode).

    `skipped` MÅLES ved siden av `tests`: junit teller en hoppet test i
    `tests` og rapporterer null failures og null errors for den. Hele
    M-2-andelen er `skipif(not DSN)` (både test_pg_og_attestering.py og
    test_api.py), så en testbase som ikke er satt opp ga en andel med null
    feilede — og et artefakt som påsto at M-2s andel var grønn uten at én
    av dem hadde kjørt. En hoppet test er ikke en bestått test.

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
        m2, m2_roede, m2_hoppet, m2_kode = _kjor(
            [str(REPO / p) if "::" not in p
             else str(REPO / p.split("::")[0]) + "::" + p.split("::", 1)[1]
             for p in M2_ANDEL], Path(tmp) / "m2.xml")
    ts = datetime.now(timezone.utc).isoformat()
    # `bestatt` settes provisorisk true så form- og grensekontrollen har et
    # komplett artefakt å måle; deretter er den sann HVIS OG BARE HVIS
    # begge er tomme. Samme form som feilinjisering-behandling.py.
    art = {"krav_id": "m02-suite-v1", "ts": ts, "bestatt": True,
           "oppsett": {"modul": "m02_revisjonslogg", "commit": commit,
                       "vert": vert, "m2_filer": list(M2_ANDEL)},
           "maalt": {"tester_totalt": totalt, "tester_feilet": roede,
                     "tester_hoppet": hoppet,
                     "m2_tester": m2, "m2_feilet": m2_roede,
                     "m2_hoppet": m2_hoppet,
                     "suite_exitkode": kode, "m2_exitkode": m2_kode}}
    # De håndskrevne betingelsene her kjente ikke GULVENE: «null feilede
    # og minst én kjørt test» er sant også for en kjøring som samlet inn
    # 40 tester etter en sti- eller konfigendring. Skriptet meldte da
    # `bestatt: true` og returnerte 0 — mens akseptporten senere feller
    # nøyaktig det artefaktet på `min_tester`. En produsent som sier
    # grønt om noe porten kaller rødt, er verre enn ingen måling: den
    # bruker opp oppmerksomheten før noen ser porten.
    #
    # Derfor spørres PORTEN, ikke en kopi av tallene dens: samme funksjon,
    # samme grenser, samme svar. Artefaktet valideres uansett på nytt i CI
    # — `bestatt` er produsentens påstand, aldri beviset.
    formfeil = valider_artefaktformat(art, "m02-suite-v1")
    grensefeil = _sjekk_grenser("m02-suite-v1", art)
    art["bestatt"] = not formfeil and not grensefeil
    ut = a.ut or (REPO / "deploy/staging/artefakter" /
                  f"m02-suite-v1-{ts[:19].replace(':', '').replace('-', '')}.json")
    ut.write_text(json.dumps(art, indent=2, ensure_ascii=False,
                             sort_keys=True) + "\n", encoding="utf-8")
    print(f"skrev {ut} (bestatt={art['bestatt']},"
          f" {totalt - hoppet} kjørte av {totalt} tester,"
          f" m2-andel {m2 - m2_hoppet} av {m2})")
    for f in formfeil + grensefeil:
        print(f"  RØDT: {f}")
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":
    sys.exit(main())
