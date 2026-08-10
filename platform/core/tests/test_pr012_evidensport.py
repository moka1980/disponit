"""PR-012 CP7: evidensporten for `behandling-m37-v1`.

Porten er selve poenget: den REGNER UT invariantene på nytt og leser aldri
produsentens `bestatt`-flagg (lærdommen fra PR #8 runde 3). Her muteres et
gyldig artefakt bort felt for felt — hver mutasjon MÅ gi rødt.
"""
import copy

import pytest

from manifestskjema import _sjekk_grenser, valider_artefaktformat


def _gyldig():
    return {
        "krav_id": "behandling-m37-v1", "ts": "2026-08-10T00:00:00Z",
        "bestatt": True,
        "oppsett": {"injisert_antall": 12,
                    "kategorier": ["avvis", "godkjenn", "sideeffekt",
                                   "fire_oyne"]},
        "maalt": {
            "kategorier_dekket": ["avvis", "godkjenn", "sideeffekt",
                                  "fire_oyne"],
            "avvis": {"injisert": 3, "terminal": 3},
            "godkjenn": {"injisert": 3, "ny_beslutning": 3},
            "sideeffekt": {"injisert": 3, "lost": 3},
            "fire_oyne": {"injisert": 3, "fullfort": 3},
            "saksversjonskonflikt_409": 2,
            "saksversjonskonflikt_sideeffekt": 0,
            "samtidig_konkurranser": 2, "samtidig_dobbel_vinner": 0,
            "klartekst_treff": 0, "handlinger_med_aktor": 40,
            "handlinger_totalt": 40, "varighet_sek": 12.5,
        },
    }


def test_gyldig_artefakt_passerer_bade_skjema_og_grenser():
    a = _gyldig()
    assert valider_artefaktformat(a, "behandling-m37-v1") == []
    assert _sjekk_grenser("behandling-m37-v1", a) == []


def test_lukket_skjema_avviser_ukjent_noekkel():
    a = _gyldig()
    a["maalt"]["snik"] = 1
    assert valider_artefaktformat(a, "behandling-m37-v1") != []


def _muter(sti, verdi):
    a = _gyldig()
    d = a
    for k in sti[:-1]:
        d = d[k]
    d[sti[-1]] = verdi
    return a


@pytest.mark.parametrize("sti, verdi, hint", [
    (["oppsett", "injisert_antall"], 11, "for få injisert"),
    (["maalt", "avvis", "terminal"], 2, "avvis ikke terminal"),
    (["maalt", "godkjenn", "injisert"], 0, "godkjenn-vei aldri prøvd"),
    (["maalt", "sideeffekt", "lost"], 2, "sideeffekt ikke løst"),
    (["maalt", "fire_oyne", "fullfort"], 2, "fire-øyne ikke fullført"),
    (["maalt", "saksversjonskonflikt_409"], 0, "409-vei aldri prøvd"),
    (["maalt", "saksversjonskonflikt_sideeffekt"], 1, "konflikt m/ sideeffekt"),
    (["maalt", "samtidig_konkurranser"], 0, "ingen konkurranse kjørt"),
    (["maalt", "samtidig_dobbel_vinner"], 1, "to vinnere"),
    (["maalt", "klartekst_treff"], 1, "klartekst i logg/dump"),
    (["maalt", "handlinger_med_aktor"], 39, "handling uten aktør"),
    (["bestatt"], False, "produsenten sier ikke bestått"),
])
def test_hver_mutasjon_gir_roedt(sti, verdi, hint):
    feil = _sjekk_grenser("behandling-m37-v1", _muter(sti, verdi))
    assert feil, f"mutasjon skulle gi rødt: {hint}"


def test_kategori_med_null_injisert_er_ikke_bestatt():
    # 0/0 er ikke 1.0 — en vei uten forsøk er ikke en bestått vei.
    a = _gyldig()
    a["maalt"]["fire_oyne"] = {"injisert": 0, "fullfort": 0}
    assert _sjekk_grenser("behandling-m37-v1", a)
