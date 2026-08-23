"""Kjørte migrasjoner er byte-immutable — målt i CI, ikke først i deploy.

23/8: en REN KOMMENTAR ble lagt inn i 056 etter at den var kjørt i prod.
CI bygger frisk base for hver kjøring og skriver checksummene på nytt,
så avviket var usynlig helt til `opp.sh` stoppet tjenestene og kjøreren
nektet («historikk er immutable») — prod sto nede i to minutter på en
kommentarlinje. Kjørerens vern er riktig og står; denne porten flytter
målingen dit den hører hjemme: FØR merge.

Fasiten (`migrasjons-fasit.json`) pinner sha256 for hver migrasjon som
er KJØRT i prod. En ny migrasjon legges til fasiten i SAMME commit som
den fødes (etter deploy er den kjørt); en endring i en pinnet fil er
rød her — dokumentasjon av senere vedtak hører til i issuer, PR-tråder
eller NYE filer, aldri i kjørt historikk.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROT = Path(__file__).resolve().parents[1]
FASIT = json.loads((ROT / "db/migrasjons-fasit.json").read_text(
    encoding="utf-8"))
KATALOG = ROT / "db/migrations"


def test_kjorte_migrasjoner_er_byte_identiske_med_fasiten():
    avvik = []
    for navn, ventet in FASIT.items():
        fil = KATALOG / navn
        if not fil.exists():
            avvik.append(f"{navn}: pinnet i fasiten, men borte fra treet")
            continue
        faktisk = hashlib.sha256(fil.read_bytes()).hexdigest()
        if faktisk != ventet:
            avvik.append(
                f"{navn}: {faktisk[:12]}… ≠ fasit {ventet[:12]}… — "
                "kjørt historikk er immutable; senere vedtak dokumenteres"
                " i issuer/nye filer, aldri her")
    assert not avvik, "\n".join(avvik)


def test_fasiten_dekker_alle_migrasjonene_i_treet():
    """Motsatt retning: HVER migrasjon i katalogen må stå i fasiten — et
    hull er en fil noen kan redigere usett.

    Grensen leses fra KATALOGEN, ikke fra fasiten. En fasit som er sin
    egen øvre grense kan senkes stille: slett siste linje mens du endrer
    den migrasjonen, så slutter porten over å se filen OG grensen faller
    med den — alle tre testene blir grønne selv om en pinnet, prod-kjørt
    migrasjon er fjernet og endret. Katalogen kan ikke synke når fasiten
    gjør det: å fjerne pinnen fjerner ikke filen.
    """
    upinnet = [fil.name for fil in sorted(KATALOG.glob("*.sql"))
               if fil.name not in FASIT]
    assert not upinnet, (
        "migrasjoner i treet uten pin i fasiten: "
        + ", ".join(upinnet)
        + " — hver migrasjon pinnes i samme commit som den fødes; en"
          " upinnet fil kan endres etter at deploy har kjørt den, og"
          " avviket dukker først opp når tjenestene alt er stoppet")


def test_fasiten_er_regnet_ikke_skrevet():
    """Fasitverdiene skal LIGNE sha256 — en hånd som skriver «TODO» inn
    i fasiten skal felles her, ikke i deploy."""
    for navn, verdi in FASIT.items():
        assert isinstance(verdi, str) and len(verdi) == 64 and \
            all(c in "0123456789abcdef" for c in verdi), navn
