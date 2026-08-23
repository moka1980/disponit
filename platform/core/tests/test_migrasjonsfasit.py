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


HENDELSEN_056 = "056_m57_utsending.sql"


def _bytefeil(les):
    """Selve målingen, med fil-leseren som parameter.

    Grunnen til at den er utløst: negativtesten under må kunne spille av
    23/8-hendelsen gjennom NØYAKTIG denne løkken uten å skrive til treet.
    En negativtest som gjenskaper sammenligningen sin egen vei beviser at
    kopien virker, ikke at porten gjør det.
    """
    avvik = []
    for navn, ventet in FASIT.items():
        fil = KATALOG / navn
        if not fil.exists():
            avvik.append(f"{navn}: pinnet i fasiten, men borte fra treet")
            continue
        faktisk = hashlib.sha256(les(fil)).hexdigest()
        if faktisk != ventet:
            avvik.append(
                f"{navn}: {faktisk[:12]}… ≠ fasit {ventet[:12]}… — "
                "kjørt historikk er immutable; senere vedtak dokumenteres"
                " i issuer/nye filer, aldri her")
    return avvik


def test_kjorte_migrasjoner_er_byte_identiske_med_fasiten():
    assert not _bytefeil(Path.read_bytes), \
        "\n".join(_bytefeil(Path.read_bytes))


def test_kommentarlinje_etter_prod_felles_i_fasitporten():
    """Negativtesten: hendelsen porten ble laget for, spilt av.

    23/8 ble en REN KOMMENTAR lagt inn i 056 etter at den var kjørt i
    prod. Positivtesten over er grønn i dag uansett om porten måler noe
    som helst — det er først når mutasjonen gjør den rød at porten er
    bevist. Hendelsen spilles derfor av i minnet: 056 leses med den
    tilføyde kommentaren, resten av treet urørt.

    MUTASJONEN SOM DREPER DENNE: svekk `_bytefeil` — fjern
    sammenligningen, sammenlign noe annet enn bytene (tekst med
    normaliserte linjeskift, lengde, mtime), eller la 056-pinnen falle ut
    av fasiten. Da finner mutasjonen ingen avvik, og denne blir rød.
    """
    kommentaren = (b"\n-- AVGJORT (eier, 2026-08-23 i #153): pseudonymnokkel."
                   b"\n")

    def med_kommentaren(fil):
        byte = fil.read_bytes()
        return byte + kommentaren if fil.name == HENDELSEN_056 else byte

    avvik = _bytefeil(med_kommentaren)

    assert len(avvik) == 1, (
        "hendelsen skal treffe 056 og BARE 056 — porten fant "
        f"{len(avvik)} avvik: {avvik}")
    assert avvik[0].startswith(HENDELSEN_056), avvik[0]
    assert "immutable" in avvik[0], avvik[0]

    # …og treet slik det faktisk står er fortsatt grønt: mutasjonen levde
    # i minnet, den skrev ikke til disk.
    assert not _bytefeil(Path.read_bytes)


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
