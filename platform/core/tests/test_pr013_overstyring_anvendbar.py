"""Codex P1 på PR #111: en menneskelig overstyring motoren ikke KAN anvende.

`menneskelig_overstyring.godkjennbare` beskriver hvilke blokkerende utfall et
menneske kan godkjenne seg forbi. Godkjenningen virker bare hvis motoren klarer
å bygge et LØFT av den — og `_loft_policy` uttrykker nøyaktig to ting:
`belop_maks` og `valuta` (`engine.LOFTBARE_GRUNNKODER`). Alt annet gir None, og
fail-closed betyr da STOPP.

Feilen var at skjemaet slapp begge de virkningsløse formene gjennom:

  * en grunnkode motoren ikke kan løfte i det hele tatt (`utenfor_tidsvindu`,
    `frekvensgrense_naadd`, …), og
  * en løftbar grunnkode UTEN verdien å løfte til (`belop_over_grense` uten
    `belop_maks`).

Eier kunne dermed aktivere det som så ut som en konfigurert overstyring, mens
HVER matchende godkjenning endte i STOPP — uten at noe sa fra.

Kravet bor i INNFØRINGSKONTRAKTEN (`valider_ny_policy`), ikke i lastekontrakten
(`valider_policy`): en policy som allerede er aktiv med en slik oppføring virker
akkurat som før (den ene overstyringen har aldri gjort noe), og skal ikke bli
korrupt ved lasting i det utrullingen lander. Testene under fester begge
halvdelene, og at motoren og kontrakten leser SAMME kilde.

Rene validator-/motortester (ingen DB).
"""
import copy
from pathlib import Path

import yaml

from policy_validator import engine
from policy_validator.engine import LOFTBARE_GRUNNKODER
from policy_validator.schema import (IKKE_MENNESKELIG_GODKJENNBARE,
                                     valider_ny_policy, valider_policy)

_BASE = yaml.safe_load(
    (Path(__file__).resolve().parents[3] / "policies"
     / "bransjemal-tjenestebedrift.yaml").read_text(encoding="utf-8"))

_HANDLING = "faktura.bokfor"


def _med_overstyring(*oppforinger):
    """Basen med `menneskelig_overstyring` satt til de gitte oppføringene."""
    p = copy.deepcopy(_BASE)
    p["menneskelig_overstyring"] = {
        "godkjennbare": [dict(o) for o in oppforinger],
        "krever_rolle": "daglig_leder"}
    return p


def _oppforing(grunnkode, **felt):
    return {"grunnkode": grunnkode, "handling": _HANDLING, **felt}


# --- innføringskontrakten avviser det virkningsløse ------------------------

def test_loftbar_oppforing_med_verdien_sin_passerer():
    # Fanger at innstrammingen ikke avviser den formen som FAKTISK virker.
    for gk, felt in LOFTBARE_GRUNNKODER.items():
        verdi = {"belop_maks": "50000.00", "valuta": "NOK"}[felt]
        ekstra = {felt: verdi}
        if felt == "belop_maks":
            ekstra["valuta"] = "NOK"        # dependentRequired i skjemaet
        p = _med_overstyring(_oppforing(gk, **ekstra))
        assert valider_ny_policy(p) == [], gk


def test_ikke_loftbar_grunnkode_avvises_ved_innforing():
    # Selve funnet: motoren har ingen gren for disse, så en godkjenning
    # ender alltid i STOPP.
    for gk in ("utenfor_tidsvindu", "frekvensgrense_naadd",
               "dataklasse_ikke_tillatt", "rolle_ikke_tillatt",
               "modus_alltid_stopp"):
        feil = valider_ny_policy(_med_overstyring(_oppforing(gk)))
        assert feil, f"{gk} kan ikke løftes og må avvises"
        assert any("menneskelig_overstyring" in f for f in feil), feil


def test_loftbar_grunnkode_uten_verdien_sin_avvises():
    # Den andre halvdelen: koden er løftbar, men det finnes ingen verdi å
    # løfte TIL — like virkningsløst som en ikke-løftbar kode.
    feil = valider_ny_policy(_med_overstyring(_oppforing("belop_over_grense")))
    assert feil, "belop_over_grense uten belop_maks må avvises"
    assert any("belop_maks" in f for f in feil), feil

    feil = valider_ny_policy(_med_overstyring(_oppforing("valuta_ikke_tillatt")))
    assert feil, "valuta_ikke_tillatt uten valuta må avvises"
    assert any("valuta" in f for f in feil), feil


def test_feilmeldingen_navngir_de_loftbare_kodene():
    # Eier skal kunne rette uten å lese motorkoden.
    feil = valider_ny_policy(_med_overstyring(_oppforing("utenfor_tidsvindu")))
    assert any(all(gk in f for gk in LOFTBARE_GRUNNKODER) for f in feil), feil


def test_oppforingens_indeks_star_i_feilen():
    # Med flere oppføringer må eier få vite HVILKEN som er ubrukelig.
    p = _med_overstyring(
        _oppforing("belop_over_grense", belop_maks="50000.00", valuta="NOK"),
        _oppforing("utenfor_tidsvindu"))
    feil = valider_ny_policy(p)
    assert any("[1]" in f for f in feil), feil
    assert not any("[0]" in f for f in feil), feil


# --- lastekontrakten er urørt (alt aktive policyer virker som før) ---------

def test_lastekontrakten_slipper_den_alt_aktive_policyen_gjennom():
    """Kravet er FRAMOVERRETTET. `hent_aktiv` revaliderer den LAGREDE policyen
    ved hver forespørsel; en innstramming i lastekontrakten ville gjort en alt
    aktiv policy korrupt i det utrullingen landet — tenanten mister da alle
    policystyrte beslutninger på grunn av én overstyring som uansett aldri har
    gjort noe."""
    for oppf in (_oppforing("utenfor_tidsvindu"),
                 _oppforing("belop_over_grense")):
        assert valider_policy(_med_overstyring(oppf)) == [], oppf


def test_basen_er_fortsatt_gyldig():
    assert valider_policy(copy.deepcopy(_BASE)) == []
    assert valider_ny_policy(copy.deepcopy(_BASE)) == []


# --- kontrakten og motoren kan ikke komme fra hverandre --------------------

def test_hver_loftbar_grunnkode_gir_faktisk_et_loft():
    """Kilden kontrakten avviser mot ER motorens uttrykkskraft. Står en kode i
    `LOFTBARE_GRUNNKODER` uten en gren i `_loft_policy`, slipper kontrakten
    gjennom en oppføring som likevel ender i STOPP — nøyaktig feilen dette
    fikset. Da skal denne bli rød."""
    for gk, felt in LOFTBARE_GRUNNKODER.items():
        verdi = {"belop_maks": "50000.00", "valuta": "NOK"}[felt]
        entry = _oppforing(gk, **{felt: verdi})
        loftet = engine._loft_policy(copy.deepcopy(_BASE), _HANDLING, gk, entry)
        assert loftet is not None, f"{gk} står som løftbar, men gir ikke løft"


def test_en_kode_utenfor_lista_gir_ingen_loft():
    # Den andre retningen: fail-closed er fortsatt fail-closed.
    entry = _oppforing("utenfor_tidsvindu")
    assert engine._loft_policy(
        copy.deepcopy(_BASE), _HANDLING, "utenfor_tidsvindu", entry) is None


def test_loftbar_uten_verdi_gir_ingen_loft():
    for gk in LOFTBARE_GRUNNKODER:
        assert engine._loft_policy(
            copy.deepcopy(_BASE), _HANDLING, gk, _oppforing(gk)) is None, gk


def test_editorgrunnlaget_leser_samme_kilde_som_kontrakten():
    """Den tredje leseren. Tilbyr flaten en kode kontrakten avviser, kan eier
    bygge et utkast som ikke lar seg aktivere; tilbyr den for få, er en lovlig
    overstyring uoppnåelig fra editoren. Begge deler er utelukket så lenge alle
    tre leser `LOFTBARE_GRUNNKODER`."""
    from api.policy_historikk import _loftbare
    assert _loftbare() is LOFTBARE_GRUNNKODER


def test_de_loftbare_kodene_er_ikke_i_deny_settet():
    """De to listene er forskjellige spørsmål — «SKAL et menneske få godkjenne
    dette?» og «KAN motoren løfte det?» — men de må ikke motsi hverandre: en
    kode vi tilbyr og validerer mot kan ikke samtidig være en et menneske aldri
    skal få godkjenne."""
    assert not (set(LOFTBARE_GRUNNKODER) & IKKE_MENNESKELIG_GODKJENNBARE)
