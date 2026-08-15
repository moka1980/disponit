"""Modulkatalogen på forsiden: fersk, komplett og formriktig.

Katalogen er generert fra `docs/spesifikasjon/disponit-prototype-v7.html` av
`tools/gen_katalog.py`. En generator uten en port i CI er bare en vennlig
anbefaling: den dagen noen redigerer `katalog.js` for hånd, eller endrer
spesifikasjonen uten å kjøre generatoren, driver de to kildene fra hverandre —
og forsiden viser da et produktomfang ingen har bestemt.

Testene her er derfor fem porter (Codex P2 på PR #43):
  1. KILDE     — generatoren leser sannhetskilden, ikke arkivet i `prototype/`.
  2. FERSKHET  — regenerering i en temp-rot gir NØYAKTIG det som ligger i repoet.
  3. OMDØPING  — nytt navn i kilden stopper genereringen til oversettelsen er
                 vurdert på nytt, så nb og en ikke kan drive fra hverandre.
  4. FORM      — 45 moduler, elleve områder, faser 1–4, alle representert.
  5. TEKST     — hvert modul- og områdenavn har nøkkel i BEGGE locale-sett.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROT = Path(__file__).resolve().parents[3]
GENERATOR = ROT / "tools" / "gen_katalog.py"
KATALOG_JS = ROT / "platform" / "core" / "ui" / "static" / "js" / "katalog.js"
# Sannhetskilden slik README.md og docs/STRUKTUR.md utpeker den. Stien står
# som en LITERAL her, ikke importert fra generatoren: en port som henter
# kildestien fra det den skal vokte, godkjenner enhver sti generatoren måtte
# bytte til.
KILDE_REL = ("docs", "spesifikasjon", "disponit-prototype-v7.html")
KILDE = ROT.joinpath(*KILDE_REL)
ARKIV = ROT / "prototype"
LOCALER = {s: ROT / "locales" / f"{s}.json" for s in ("nb", "en")}

MODULER = 45
OMRADER = 11
FASER = {1, 2, 3, 4}


def _katalog_js() -> tuple[list[dict], list[dict]]:
    """(KATALOG, OMRADER) lest ut av den genererte JS-fila.

    Fila er data i JS-syntaks; her leses den med regex i stedet for en
    JS-motor, slik at porten ikke trenger node for å kjøre.
    """
    tekst = KATALOG_JS.read_text(encoding="utf-8")
    katalog = [
        {"n": int(n), "omrade": o, "fase": int(f)}
        for n, o, f in re.findall(
            r"\{\s*n:\s*(\d+),\s*omrade:\s*\"([^\"]+)\",\s*fase:\s*(\d+)\s*\}",
            tekst)
    ]
    omrader = [
        {"id": i, "moduler": json.loads(m)}
        for i, m in re.findall(
            r"\{\s*id:\s*\"([^\"]+)\",\s*moduler:\s*(\[[^\]]*\])\s*\}", tekst)
    ]
    return katalog, omrader


def test_generatoren_leser_sannhetskilden():
    """Kilden skal være spesifikasjonen, ikke arkivet (Codex P2 på PR #43).

    Generatoren leste `prototype/AI-bedriftsagent-prototype-v7.html` — v7.0 —
    mens `README.md` peker på `docs/spesifikasjon/disponit-prototype-v7.html`
    (v7.2) som sannhetskilden og `docs/STRUKTUR.md` kaller `prototype/` et
    historisk arkiv som aldri endres. De to filene gir identisk katalog i dag,
    så ferskhetsporten under ville stått grønn uansett: den måler at
    `katalog.js` stemmer med det generatoren leser, ikke at generatoren leser
    riktig fil. Derfor denne, som er den eneste som fanger at kilden peker feil.
    """
    kilde = "/".join(KILDE_REL)
    assert KILDE.exists(), f"sannhetskilden mangler: {kilde}"
    tekst = GENERATOR.read_text(encoding="utf-8")
    assert kilde in tekst, (
        f"generatoren nevner ikke sannhetskilden {kilde}")
    # Arkivet skal ikke være INNDATA. Det kan nevnes i prosa (kommentaren som
    # forklarer hvorfor kilden ble byttet), men ingen filsti dit skal bygges.
    for arkivfil in sorted(ARKIV.glob("*.html")):
        assert f'"{arkivfil.name}"' not in tekst, (
            f"generatoren bygger fortsatt en sti til arkivet: {arkivfil.name}")


def _temprot(tmp_path: Path) -> Path:
    """Kopi av det generatoren leser og skriver, utenfor repoet.

    Generatoren kjøres alltid mot en KOPI: en test som «verifiserer» ved å
    oppdatere fila den sjekker, kan ikke feile.
    """
    (tmp_path / "docs" / "spesifikasjon").mkdir(parents=True)
    (tmp_path / "locales").mkdir()
    (tmp_path / "platform/core/ui/static/js").mkdir(parents=True)
    shutil.copy2(KILDE, tmp_path.joinpath(*KILDE_REL))
    for sprak, sti in LOCALER.items():
        shutil.copy2(sti, tmp_path / "locales" / f"{sprak}.json")
    return tmp_path


def test_katalogen_er_fersk(tmp_path):
    """Regenerering skal gi byte-identisk resultat.

    Uten denne porten kunne `katalog.js` vært håndredigert, eller
    spesifikasjonen endret uten en ny kjøring, og ingenting ville sagt fra.
    """
    _temprot(tmp_path)
    r = subprocess.run([sys.executable, str(GENERATOR), str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    ny = (tmp_path / "platform/core/ui/static/js/katalog.js").read_text(encoding="utf-8")
    assert ny == KATALOG_JS.read_text(encoding="utf-8"), (
        "katalog.js er ikke fersk — kjør tools/gen_katalog.py")
    for sprak, sti in LOCALER.items():
        forventet = json.loads((tmp_path / "locales" / f"{sprak}.json")
                               .read_text(encoding="utf-8"))
        faktisk = json.loads(sti.read_text(encoding="utf-8"))
        nokler = {k: v for k, v in forventet.items()
                  if k.startswith(("site.katalog.m", "site.omrade."))}
        for k, v in nokler.items():
            assert faktisk.get(k) == v, f"{sprak}: {k} er ikke fersk"


def test_navneendring_krever_ny_oversettelse(tmp_path):
    """Et omdøpt modulnavn i kilden skal stoppe genereringen (Codex P2 på #43).

    `MODUL_EN` slo tidligere opp på modulnummer alene. Fikk en modul nytt navn i
    spesifikasjonen uten nytt nummer, skrev generatoren det nye navnet i
    `nb.json` og BEHOLDT det gamle produktnavnet i `en.json` — og ingen av de
    andre portene her kunne se det: utdata var byte-identisk med seg selv
    (ferskhetsporten), og den engelske nøkkelen var ikke tom (tekstporten). De
    to offentlige katalogene kunne altså si hver sin ting i det uendelige.
    Denne porten er derfor den eneste som fanger drift MELLOM språkene.

    Navnet som døpes om leses ut av `nb.json`, ikke skrevet inn her: en literal
    ville vært en tredje avskrift av katalogen, og den ville råtnet stille den
    dagen modulen faktisk fikk nytt navn.
    """
    rot = _temprot(tmp_path)
    spek = rot.joinpath(*KILDE_REL)
    navn = json.loads(LOCALER["nb"].read_text(encoding="utf-8"))[
        "site.katalog.m42.navn"]
    tekst = spek.read_text(encoding="utf-8")
    omdopt = tekst.replace(f"n:42,name:'{navn}'", "n:42,name:'Transaksjonsvakt'")
    assert omdopt != tekst, f"fant ikke «{navn}» som modul 42 i kilden"
    spek.write_text(omdopt, encoding="utf-8")

    r = subprocess.run([sys.executable, str(GENERATOR), str(rot)],
                       capture_output=True, text=True)
    assert r.returncode != 0, (
        "generatoren godtok et omdøpt modulnavn — den engelske katalogen ville "
        "beholdt det gamle produktnavnet uten at noe sa fra")
    melding = r.stderr + r.stdout
    assert "M-42" in melding, f"feilmeldingen navngir ikke modulen: {melding}"
    assert navn in melding and "Transaksjonsvakt" in melding, (
        f"feilmeldingen viser ikke hva navnet ble endret fra og til: {melding}")
    # Ingenting skal være skrevet: en generator som stopper halvveis ville
    # etterlatt en katalog og et locale-sett som ikke hører sammen.
    assert not (rot / "platform/core/ui/static/js/katalog.js").exists(), (
        "generatoren skrev katalog.js selv om den avviste kilden")
    for sprak, sti in LOCALER.items():
        assert (rot / "locales" / f"{sprak}.json").read_text(encoding="utf-8") \
            == sti.read_text(encoding="utf-8"), (
                f"{sprak}.json ble skrevet selv om genereringen ble avvist")


def test_katalogen_har_forventet_form():
    katalog, omrader = _katalog_js()
    assert len(katalog) == MODULER, f"forventet {MODULER} moduler"
    assert {m["n"] for m in katalog} == set(range(1, MODULER + 1)), (
        "modulnumrene er ikke 1..45 — duplikat eller hull")
    assert len(omrader) == OMRADER, f"forventet {OMRADER} områder"
    assert {m["fase"] for m in katalog} == FASER, (
        "katalogen dekker ikke fase 1–4")
    # Hvert område må ha minst én modul, og områdelistene må til sammen dekke
    # katalogen nøyaktig én gang: en modul i to områder ville stått to steder
    # på forsiden, og en modul i ingen ville vært usynlig.
    fra_omrader: list[int] = []
    for o in omrader:
        assert o["moduler"], f"området {o['id']} har ingen moduler"
        fra_omrader += o["moduler"]
    assert sorted(fra_omrader) == sorted(m["n"] for m in katalog), (
        "områdene dekker ikke katalogen nøyaktig én gang")


@pytest.mark.parametrize("sprak", sorted(LOCALER))
def test_hvert_navn_finnes_paa_begge_sprak(sprak):
    katalog, omrader = _katalog_js()
    d = json.loads(LOCALER[sprak].read_text(encoding="utf-8"))
    for m in katalog:
        nokkel = f"site.katalog.m{m['n']}.navn"
        assert d.get(nokkel), f"{nokkel} mangler i {sprak}.json"
    for o in omrader:
        nokkel = f"site.omrade.{o['id']}"
        assert d.get(nokkel), f"{nokkel} mangler i {sprak}.json"
