"""`m57-ytelse-v1` — ytelsen målt på det taket verten faktisk bærer.

Eiers vedtak natt til 17/9: «vi kan gå videre med maks søknader som kan
godta den maskinen vi har i dag». Klarsignalets 5000 står som kontraktens
konvolutt (registrert kontraktversjon 1); utførerens harde tak og
grensens bunt er ETT tall, pinnet her — endres det ene uten det andre,
faller denne.

MUTASJONENE SOM FELLER DENNE: sett `parsing.MAKS_KANDIDATER` til 301
(bindingen til grensen); la `_grenser_m57_ytelse` godta 299 søknader,
241 minutter, 0 minutter eller `feilet`; fjern feltet fra skjemaet.
"""
from __future__ import annotations

import json
from pathlib import Path

ROT = Path(__file__).resolve().parents[3]


def _art(m, **over):
    maalt = {"ytelse_full_bunt_soknader": m.M57_YTELSE_MAKS_SOKNADER,
             "ytelse_full_bunt_minutter": 200, "resultat": "utfort",
             "forste_claim_ts": "2026-09-17T07:06:39+00:00",
             "status_ts": "2026-09-17T10:26:39+00:00"}
    maalt.update(over)
    return {"krav_id": "m57-ytelse-v1", "ts": "2026-09-17T11:00:00+00:00",
            "bestatt": True,
            "oppsett": {"modul": "m57_ats", "vert": "disponit-srv",
                        "oppdrag_id": 111, "tenant": "t-m57fasit",
                        "bevisrot_sha256": m.m57_ytelse_bevisrot_sha256(),
                        "modell_digest": "357c53fb"},
            "maalt": maalt}


def test_taket_er_ett_tall_i_grense_utforer_og_flate():
    import manifestskjema as m
    from modules.m57_ats import parsing
    g = m.KRAVGRENSER["m57-ytelse-v1"]
    assert g["ytelse_min_soknader"] == m.M57_YTELSE_MAKS_SOKNADER == 300
    assert parsing.MAKS_KANDIDATER == m.M57_YTELSE_MAKS_SOKNADER
    assert g["ytelse_maks_minutter"] == 240 and g["krev_utfort"] is True
    assert set(g["punktbinding"]) == {"ytelse_bestatt"}
    js = (ROT / "platform/core/ui/static/js/flater/rekruttering.js") \
        .read_text(encoding="utf-8")
    assert f'max: "{m.M57_YTELSE_MAKS_SOKNADER}"' in js, \
        "flaten lover et annet tak enn utføreren bærer"
    assert 'max: "5000"' not in js
    # Kontraktens konvolutt står urørt (registrert kontraktversjon 1).
    kontrakt = json.loads((ROT / "platform/modules/m57_ats/kontrakt/"
                           "payload-skjema.json").read_text(encoding="utf-8"))
    assert kontrakt["properties"]["antall_soknader"]["maximum"] == 5000


def test_utforeren_avviser_en_bunt_over_taket(tmp_path):
    import zipfile
    from modules.m57_ats import parsing
    n = parsing.MAKS_KANDIDATER + 1
    arkiv = tmp_path / "bunt.zip"
    with zipfile.ZipFile(arkiv, "w") as z:
        rader = []
        for i in range(n):
            z.writestr(f"k{i}/cv.html", "<p>x</p>")
            rader.append({"kandidat_id": f"k{i}", "filer": [f"k{i}/cv.html"]})
        z.writestr("soknader.json", json.dumps({"soknader": rader}))
    import pytest
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.les_manifest(arkiv, parsing.inspiser_bunt(arkiv))
    assert e.value.kode == "manifest_feilformet"


def test_porten_feller_hver_akse():
    import manifestskjema as m
    from manifestskjema import _sjekk_grenser, valider_artefaktformat
    ok = _art(m)
    assert valider_artefaktformat(ok, "m57-ytelse-v1") == []
    assert _sjekk_grenser("m57-ytelse-v1", ok) == []
    for felt, verdi in (("ytelse_full_bunt_soknader",
                         m.M57_YTELSE_MAKS_SOKNADER - 1),
                        ("ytelse_full_bunt_soknader",
                         m.M57_YTELSE_MAKS_SOKNADER + 1),
                        ("ytelse_full_bunt_minutter", 241),
                        ("ytelse_full_bunt_minutter", 0),
                        ("ytelse_full_bunt_minutter", 199),
                        ("status_ts", "2026-09-17T07:00:00+00:00"),
                        ("status_ts", "ikke-en-tid"),
                        ("resultat", "feilet")):
        assert _sjekk_grenser("m57-ytelse-v1", _art(m, **{felt: verdi})), \
            (felt, verdi)
    # Minuttene er RE-REGNET av tidsstemplene, rundet opp.
    assert _sjekk_grenser("m57-ytelse-v1", _art(
        m, status_ts="2026-09-17T10:26:40+00:00",
        ytelse_full_bunt_minutter=201)) == []
    annen = dict(ok, oppsett=dict(ok["oppsett"], bevisrot_sha256="0" * 64))
    assert any("bevisrot" in f for f in _sjekk_grenser("m57-ytelse-v1", annen))
    for felt in ("ytelse_full_bunt_soknader", "ytelse_full_bunt_minutter",
                 "resultat"):
        uten = dict(ok, maalt={k: v for k, v in ok["maalt"].items()
                               if k != felt})
        assert valider_artefaktformat(uten, "m57-ytelse-v1") != [], felt
    fremmed = dict(ok, maalt=dict(ok["maalt"], ekstra=1))
    assert valider_artefaktformat(fremmed, "m57-ytelse-v1") != []
