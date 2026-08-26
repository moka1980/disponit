"""Evidensgrensen `m57-v1` — registrert FØR modulen bygges (M-57-
klarsignalet §0/§10), og målt her med begge retninger per invariant.

Grensens form er PARET (forsøk, brudd): null brudd beviser ingenting
uten minst ett forsøk (en fraværstest går grønn på søppel), og settet
av invarianter er PINNET i `M57_INVARIANTER` — et artefakt kan ikke
definere bort et punkt ved å utelate det. Skjemaet er lukket begge
veier: manglende felt felles av `required`, fremmede av
`additionalProperties`.
"""
from __future__ import annotations

from manifestskjema import (KRAVGRENSER, M57_INVARIANTER, _sjekk_grenser,
                            valider_artefaktformat)


def _gront_artefakt() -> dict:
    """Bygger et artefakt der hver invariant er PRØVD og holdt."""
    maalt: dict = {}
    for navn in M57_INVARIANTER:
        maalt[f"{navn}_forsok"] = 3
        maalt[f"{navn}_brudd"] = 0
    maalt["ui_tastaturgjennomgang_dokumentert"] = True
    maalt["ddl_begge_kjoringer_gronne"] = True
    maalt["ytelse_full_bunt_soknader"] = 5000
    maalt["ytelse_full_bunt_minutter"] = 212.5
    return {
        "krav_id": "m57-v1",
        "ts": "2026-08-23T00:00:00+00:00",
        "bestatt": True,
        "oppsett": {"modul": "m57_ats", "commit": "0" * 40, "vert": "lokal"},
        "maalt": maalt,
    }


def test_grensen_dekker_klarsignalets_punkter():
    """§10 teller 8 sikkerhetsinvarianter + 11 øvrige numeriske + 2
    ja-punkter. Tallene er pinnet MOT KLARSIGNALET, ikke mot listen selv
    — krymper settet, er det denne som skal rødne, ikke bare validatoren
    som stille måler færre punkter."""
    g = KRAVGRENSER["m57-v1"]
    assert len(M57_INVARIANTER) == 19
    assert len(g["krav_ja"]) == 2
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    # Settet er unikt og grensen bærer det pinnede settet, ikke en kopi.
    assert len(set(M57_INVARIANTER)) == 19
    assert g["invarianter"] is M57_INVARIANTER


def test_gront_artefakt_bestar_begge_portene():
    art = _gront_artefakt()
    assert valider_artefaktformat(art, "m57-v1") == []
    assert _sjekk_grenser("m57-v1", art) == []


def test_ett_brudd_feller_uansett_hvilken_invariant():
    for navn in M57_INVARIANTER:
        art = _gront_artefakt()
        art["maalt"][f"{navn}_brudd"] = 1
        feil = _sjekk_grenser("m57-v1", art)
        assert any(f"{navn}_brudd=1" in f for f in feil), navn


def test_null_forsok_feller_selv_med_null_brudd():
    """Selve poenget med parformen: 0 brudd over 0 forsøk er en port som
    aldri kjørte, og den er RØD — for hver invariant."""
    for navn in M57_INVARIANTER:
        art = _gront_artefakt()
        art["maalt"][f"{navn}_forsok"] = 0
        feil = _sjekk_grenser("m57-v1", art)
        assert any(f"{navn}_forsok=0" in f for f in feil), navn


def test_ja_punktene_krever_bokstavelig_true():
    """Alt annet enn `True` er nei (§10 siste linje) — også sannhets-
    lignende verdier som 1 og "ja", som en produsent kunne skrive i god
    tro."""
    for navn in ("ui_tastaturgjennomgang_dokumentert",
                 "ddl_begge_kjoringer_gronne"):
        for verdi in (False, None, 1, "ja"):
            art = _gront_artefakt()
            art["maalt"][navn] = verdi
            assert any(navn in f for f in _sjekk_grenser("m57-v1", art)), \
                (navn, verdi)


def test_ytelsespunktet_er_en_maling_ikke_et_ja_punkt():
    """Codex P1: `staging_sjekkliste.ytelse_bestatt` pekte på `m57-v1`,
    men grensen bar bare invariantpar og to booleans. Et skjemagyldig,
    grønt artefakt kunne dermed krysse av for ytelse uten at noen hadde
    kjørt en eneste søknad — og en modul som ikke er levedyktig ville
    passert aktiveringen.

    De to tallene måles SAMMEN med vilje: en varighet uten last er en tom
    kjøring, og en full bunt uten varighet er bare en påstand om at det
    gikk. Begge retninger felles her, og skjemaet feller fraværet
    uavhengig — samme to-lags-form som invariantene."""
    g = KRAVGRENSER["m57-v1"]
    # For lite last: en prøve på 4999 er ikke den fulle bunten.
    art = _gront_artefakt()
    art["maalt"]["ytelse_full_bunt_soknader"] = g["ytelse_min_soknader"] - 1
    assert any("ytelse_full_bunt_soknader" in f
               for f in _sjekk_grenser("m57-v1", art))
    # For lang tid: ett minutt over §4s frist er ikke bestått.
    art = _gront_artefakt()
    art["maalt"]["ytelse_full_bunt_minutter"] = g["ytelse_maks_minutter"] + 1
    assert any("ytelse_full_bunt_minutter" in f
               for f in _sjekk_grenser("m57-v1", art))
    # En kjøring som varte 0 minutter har ikke skjedd.
    for verdi in (0, -1):
        art = _gront_artefakt()
        art["maalt"]["ytelse_full_bunt_minutter"] = verdi
        assert any("ytelse_full_bunt_minutter" in f
                   for f in _sjekk_grenser("m57-v1", art)), verdi
    # Og fraværet felles av BEGGE lag, som for invariantene.
    for felt in ("ytelse_full_bunt_soknader", "ytelse_full_bunt_minutter"):
        art = _gront_artefakt()
        del art["maalt"][felt]
        assert valider_artefaktformat(art, "m57-v1") != [], felt
        assert any(felt in f for f in _sjekk_grenser("m57-v1", art)), felt


def test_ytelsesgrensen_er_klarsignalets_tall():
    """Grensen skal være DE SAMME tallene kontrakten håndhever, ikke to
    tall som ligner. `antall_soknader`-taket er den fulle bunten, og
    akseptkonvolutten er klarsignalets 240 minutter (§4).

    ORDREFRISTEN er derimot ikke konvolutten (Codex P1 på #210): den er
    løftet til KUNDEN, og et løfte utover autoriteten som faktisk
    utstedes (claim-leasen/opplastingskapabilitetens 3600 s, uten
    fornyelsesvei før #165) kunne ingen holde — etter første time kunne
    en annen kontrollør reclaime og duplisere evalueringen av samme
    persondatabunt. Fristen er derfor min(konvolutt, autoritet), og
    porten her er skrevet slik at #165 (fornyelsen) GJENÅPNER 240
    automatisk: når autoritetstaket heves, faller min() tilbake på
    konvolutten, og dette spesialtilfellet dør av seg selv."""
    import oppdragskontrakt as ok
    g = KRAVGRENSER["m57-v1"]
    _, tak = ok.FELTGRENSER["rekruttering.evaluering"]["antall_soknader"]
    assert g["ytelse_min_soknader"] == tak
    _, frister = ok.UTFORELSESFRIST_VALG["rekruttering.evaluering"]
    # Autoriteten leses fra PRODUKSJONSKONTRAKTEN (Codex P2, runde 2):
    # et testlokalt 3600 kunne flyttes uten at noen runtime-mekanisme
    # fulgte med. Nå endres porten kun MED mekanismen: #165 hever
    # `UTSTEDT_AUTORITET_S` (som også klemmer kapabiliteten i `app.py`),
    # og min() gjenåpner konvolutten av seg selv.
    assert frister["bunt"] == min(g["ytelse_maks_minutter"] * 60,
                                  ok.UTSTEDT_AUTORITET_S)


def test_utelatt_invariant_felles_av_begge_lag():
    """Et artefakt uten et av parfeltene: skjemaet feller det
    (`required`), og grensesjekken feller det uavhengig — to lag, samme
    dom, så ingen av dem kan råtne usett."""
    art = _gront_artefakt()
    del art["maalt"]["arkiv_utpakking_utenfor_grense_brudd"]
    assert valider_artefaktformat(art, "m57-v1") != []
    assert any("arkiv_utpakking_utenfor_grense_brudd" in f
               for f in _sjekk_grenser("m57-v1", art))


def test_fremmede_felter_avvises_av_skjemaet():
    """Lukket skjema: en produsent kan ikke smugle inn en «egen»
    måling og senere sitere den som om grensen dekket den."""
    art = _gront_artefakt()
    art["maalt"]["egen_maaling_forsok"] = 5
    assert valider_artefaktformat(art, "m57-v1") != []


def test_skjemaets_feltsett_er_generert_fra_settet():
    """Skjemafilen er avledet av `M57_INVARIANTER` — driver de fra
    hverandre, er det denne porten som sier ifra, ikke en aksept-
    kjøring måneder senere."""
    import json
    from pathlib import Path
    skjema = json.loads(
        (Path(__file__).resolve().parents[1] / "artefakt-m57-skjema.json")
        .read_text(encoding="utf-8"))
    felter = set(skjema["properties"]["maalt"]["properties"])
    ventet = {f"{n}_{s}" for n in M57_INVARIANTER for s in ("forsok", "brudd")}
    ventet |= {"ui_tastaturgjennomgang_dokumentert",
               "ddl_begge_kjoringer_gronne",
               "ytelse_full_bunt_soknader", "ytelse_full_bunt_minutter"}
    assert felter == ventet
    assert set(skjema["properties"]["maalt"]["required"]) == ventet
