#!/usr/bin/env python3
"""Suiteartefaktet for M-23 (sertifiseringen 14/9).

Kjører HELE testsuiten på staging-verten og skriver resultatet som
artefakt — med M-23s andel NAVNGITT og målt for seg (delingsbetingelsen
i RUTINER.md: et delt løp må navngi hvilken måling som beviser punktet
for nettopp denne modulen; fritekst er ikke en binding).

M-23s andel er PINNET i `manifestskjema.M23_SUITE_ANDEL` og leses derfra:
tolv filer modulen eier i sin helhet. Utvalget står ETT sted fordi porten
og produsenten ellers kunne gli fra hverandre.

HVORFOR DENNE ER EN EGEN FIL OG IKKE EN PARAMETER TIL `m02-suite-artefakt.py`
Den delen av koden som kjører pytest og leser junit-XML er nesten
ordrett den samme, og en delt hjelpemodul ville vært renere kode. Men
`m02-suite-artefakt.py` ligger i `M02_BEVISROT_FILER`: bytene er
BEVISMATERIALE for et innsjekket m02-artefakt, og å endre dem ville gjort
det artefaktet ugyldig — bevisroten er nettopp en hash over disse filene.
En refaktorering her koster en sertifisering der. Derfor står de to ved
siden av hverandre, og denne kommentaren er grunnen.

BRUK (på verten, med testbase og testmiljø satt opp):
    DISPONIT_TEST_DSN=... DISPONIT_TEST_MIGRATOR_DSN=... \
    DISPONIT_TEST_FORDRINGSVEIP_DSN=... \
    /opt/disponit/.venv/bin/python deploy/staging/m23-suite-artefakt.py \
        [--ut deploy/staging/artefakter/...json]

SVEIPEROLLENS DSN ER PÅKREVD, ikke valgfri: `test_m23_fasit_port.py`
hopper uten den, og porten krever null hoppede i andelen. En kjøring uten
den rapporterer ærlig rødt i stedet for en grønn andel der fasiten aldri
ble målt.

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
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))

#: M-23s andel og grensene den måles mot — hentet fra akseptporten selv,
#: aldri gjentatt her. To lister som skal være like, er før eller siden to
#: ulike lister; denne leser den ENE.
from manifestskjema import (M23_SUITE_ANDEL,  # noqa: E402
                            _sjekk_grenser, m23_bevisrot_sha256,
                            valider_artefaktformat)


def _kjor(mal: list[str], junit: Path) -> tuple[int, int, int, int]:
    """pytest over `mal`. -> (tester, feilet+error, hoppet, exitkode).

    `skipped` MÅLES ved siden av `tests`: junit teller en hoppet test i
    `tests` og rapporterer null failures og null errors for den. Hele
    M-23-andelen er `skipif(not DSN)`, så en testbase som ikke er satt
    opp ga en andel med null feilede — og et artefakt som påsto at
    andelen var grønn uten at én av dem hadde kjørt.

    Exitkoden MÅLES, den kastes ikke: junit-XML-en skrives også når
    kjøringen ble avbrutt underveis, og beskriver da bare testene som
    rakk å bli ferdige — alle grønne, null failures. En hel grønn suite
    er exit 0; alt annet er ikke et grønt artefakt.
    """
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
        m23, m23_roede, m23_hoppet, m23_kode = _kjor(
            [str(REPO / sti) for sti in M23_SUITE_ANDEL],
            Path(tmp) / "m23.xml")
    ts = datetime.now(timezone.utc).isoformat()
    # `bestatt` settes provisorisk true så form- og grensekontrollen har
    # et komplett artefakt å måle; deretter er den sann HVIS OG BARE HVIS
    # begge er tomme.
    art = {"krav_id": "m23-suite-v1", "ts": ts, "bestatt": True,
           "oppsett": {"modul": "m23_fordring", "commit": commit,
                       "vert": vert, "m23_filer": list(M23_SUITE_ANDEL),
                       "bevisrot_sha256": m23_bevisrot_sha256()},
           "maalt": {"tester_totalt": totalt, "tester_feilet": roede,
                     "tester_hoppet": hoppet,
                     "m23_tester": m23, "m23_feilet": m23_roede,
                     "m23_hoppet": m23_hoppet,
                     "suite_exitkode": kode, "m23_exitkode": m23_kode}}
    # PORTEN SPØRRES, ikke en kopi av tallene dens: samme funksjon, samme
    # grenser, samme svar. En produsent som sier grønt om noe porten
    # kaller rødt, er verre enn ingen måling.
    formfeil = valider_artefaktformat(art, "m23-suite-v1")
    grensefeil = _sjekk_grenser("m23-suite-v1", art)
    art["bestatt"] = not formfeil and not grensefeil
    ut = a.ut or (
        REPO / "deploy/staging/artefakter"
        / f"m23-suite-v1-{ts[:19].replace(':', '').replace('-', '')}Z.json")
    ut.parent.mkdir(parents=True, exist_ok=True)
    ut.write_text(json.dumps(art, indent=2, ensure_ascii=False,
                             sort_keys=True) + "\n", encoding="utf-8")
    print(f"skrev {ut} (bestatt={art['bestatt']},"
          f" {totalt - hoppet} kjørte av {totalt} tester,"
          f" m23-andel {m23 - m23_hoppet} av {m23})")
    for f in formfeil + grensefeil:
        print(f"  RØDT: {f}")
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":
    sys.exit(main())
