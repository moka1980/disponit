"""PR-013 CP8: evidensporten for `policyadmin-v1`.

Porten REGNER UT invariantene på nytt og leser aldri produsentens `bestatt`-
flagg (lærdommen fra PR #8 runde 3). Et gyldig artefakt muteres bort felt for
felt — hver mutasjon MÅ gi rødt. Det gyldige speiler det driver-skriptet
(`deploy/staging/policyadmin-evidens.py`) faktisk måler.
"""
import copy

import pytest

from manifestskjema import _sjekk_grenser, valider_artefaktformat


def _gyldig():
    return {
        "krav_id": "policyadmin-v1", "ts": "2026-08-10T23:00:00Z",
        "bestatt": True,
        "oppsett": {"injisert_antall": 8,
                    "kategorier": ["utvider", "forfatter_alene", "innsnevrer",
                                   "rebasering"]},
        "maalt": {
            "kategorier_dekket": ["utvider", "forfatter_alene", "innsnevrer",
                                  "rebasering"],
            "utvider": {"injisert": 2, "aktivert": 2},
            "forfatter_alene": {"injisert": 2, "stoppet": 2},
            "innsnevrer": {"injisert": 2, "aktivert": 2},
            "rebasering": {"injisert": 2, "rebasert": 2},
            "aktiveringer_totalt": 4,
            "policyer_med_flere_aktive": 0,
            "runtime_skrivenekt": 1,
            "diff_binding_treff": 12, "diff_binding_totalt": 12,
            "handlinger_totalt": 12, "varighet_sek": 2.9,
        },
    }


def test_gyldig_artefakt_bestaar():
    assert _sjekk_grenser("policyadmin-v1", _gyldig()) == []


def test_skjema_er_lukket():
    # LUKKET format: en ukjent nøkkel er en feil, ikke stillhet.
    a = _gyldig()
    a["maalt"]["oppdiktet"] = 1
    assert valider_artefaktformat(a, "policyadmin-v1")


@pytest.mark.parametrize("muter, grunn", [
    (lambda a: a["maalt"]["utvider"].__setitem__("aktivert", 1),
     "UTVIDER-vei ikke fullført → andel < 1.0"),
    (lambda a: a["maalt"]["utvider"].__setitem__("injisert", 0),
     "UTVIDER-vei aldri prøvd"),
    (lambda a: a["maalt"]["forfatter_alene"].__setitem__("stoppet", 1),
     "forfatter-alene ble likevel aktivert et sted"),
    (lambda a: a["maalt"]["innsnevrer"].__setitem__("aktivert", 1),
     "INNSNEVRER-vei ikke fullført"),
    (lambda a: a["maalt"]["rebasering"].__setitem__("rebasert", 1),
     "rebasering ikke utløst hver gang"),
    (lambda a: a["maalt"].__setitem__("policyer_med_flere_aktive", 1),
     "en policy endte med to aktive (atomisitet brutt)"),
    (lambda a: a["maalt"].__setitem__("runtime_skrivenekt", 0),
     "runtime kunne skrive policyer direkte (V10 brutt)"),
    (lambda a: a["maalt"].__setitem__("diff_binding_treff", 11),
     "en attestasjon bandt ikke diffen den så"),
    (lambda a: a["maalt"].__setitem__("diff_binding_totalt", 0),
     "ingen attestasjon å binde"),
    (lambda a: a["oppsett"].__setitem__("injisert_antall", 4),
     "for få injiserte (én kjøring er en anekdote)"),
    (lambda a: a["oppsett"].__setitem__(
        "kategorier", ["utvider", "x", "innsnevrer", "rebasering"]),
     "en oppdiktet kategori fyller tallet mens en ekte mangler"),
    (lambda a: a["maalt"].__setitem__(
        "kategorier_dekket", ["utvider", "innsnevrer", "rebasering"]),
     "ikke alle fire kontraktskategorier dekket"),
    (lambda a: a.__setitem__("bestatt", False),
     "produsenten selv sier ikke bestått"),
])
def test_mutasjon_gir_rodt(muter, grunn):
    a = _gyldig()
    muter(a)
    assert _sjekk_grenser("policyadmin-v1", a), f"mutasjon slapp gjennom: {grunn}"
