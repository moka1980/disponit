"""«Likt lokalt»-leddet for M-17s fasitartefakt — STÅENDE måling.

Det samme settet (`deploy/staging/m17_fasit.py`) som staging-artefaktet
drives av, kjøres her gjennom de EKTE dørene lokalt, og ALLE fasitene
måles: funnene, klassifiseringen (regelen), køens intakthet og
evidenskjeden.

HELE VEIEN, IKKE BARE SQL-EN. Settet legges inn med kundens dører
(`m17_ta_imot`, `m17_klassifiser`, `m17_til_unntakskoe`, `m17_lukk`,
`m17_lagre_utkast`, `m17_sett_stilleregler` — alle som runtime-rollen),
regelen kjøres av `plan/stilleregler.kjor_en_runde()` som PLANARBEIDEREN,
funnene skrives av `drift/henvendelsessveip.kjor()` som SVEIPEROLLEN, og
alt leses gjennom `api.kundeservice.svar_for` — funksjonen
`GET /v1/kundeservice` selv kaller. Det er den kjeden natten kjører.

MUTASJONENE SOM FELLER DENNE, hver KJØRT mot basen under eierrollen:
  1. `> v_ukl` → `>= v_ukl` i `m17_funnkandidater`:
     «dagen_for_uklassifisert_kant» blir et funn.
  2. `> v_ube` → `>= v_ube`: «svar_kreves_dagen_for» blir et funn.
  3. `AND h.unntak_id IS NULL` fjernet: de to i unntakskøen blir funn.
  4. `%.` → `%` i domeneregelen (205): «lignende_domene_treffer_ikke»
     klassifiseres av regelen.
  5. `m17_evidens`-kallet fjernet fra regelrunden: køen er riktig,
     evidenskjeden mangler tre dommer.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo  # noqa: F401

ROT = Path(__file__).resolve().parents[3]
SVEIP_DSN = os.environ.get("DISPONIT_TEST_HENVENDELSESVEIP_DSN")
PLAN_DSN = os.environ.get("DISPONIT_TEST_PLAN_DSN")

pg = pytest.mark.skipif(not (DSN and MIGRATOR_DSN),
                        reason="test-DSN ikke satt")
#: Sveipen og regelrunden har hver sin login-rolle, og runtime har med
#: vilje ingen av dem. Uten dem kan ikke fasiten måles der den skal
#: måles — så da hoppes det heller enn å måle et indre ledd.
roller = pytest.mark.skipif(not (SVEIP_DSN and PLAN_DSN),
                            reason="HENVENDELSESVEIP/PLAN-DSN ikke satt")


def _last(navn: str, fil: str):
    spec = importlib.util.spec_from_file_location(navn, ROT / fil)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _lib():
    return _last("m17_fasit", "deploy/staging/m17_fasit.py")


def _rt():
    from db.pg import koble
    return koble(DSN)


def test_settet_er_fasiten():
    """Settet ER kantene — og hver av dem er DEKKET, ikke bare talt."""
    m = _lib()
    sett = dict(m.bygg_sett())
    assert m.bygg_sett() == m.bygg_sett()              # deterministisk
    med = sett["med_regler"]
    dogn_ukl = {r[1] for r in med if r[3] is None and r[6] is None}
    # Uklassifisert-kanten: dagen før, nøyaktig på, og godt over.
    assert {m.DOGN_UKLASSIFISERT, m.DOGN_UKLASSIFISERT + 1} <= dogn_ukl
    dogn_svar = {r[1] for r in med
                 if r[3] and r[3][2] == "svar_kreves" and r[4] is None}
    assert {m.DOGN_UBESVART, m.DOGN_UBESVART + 1} <= dogn_svar
    # Mistenkelig på dag 0 — med og uten sak.
    mist = [r for r in med if r[3] and r[3][2] == "mistenkelig"]
    assert {r[1] for r in mist} == {0}
    assert {r[4] for r in mist} == {None, "unntakskoe"}
    # Regelen: domene, underdomene, lignende domene, adresse, annen adresse.
    regel = {r[2] for r in med if r[6] and r[6][3] == "regel"}
    assert regel == {m.STILLE, m.STILLE_UNDER, m.NYHETSBREV}
    ikke = {r[2] for r in med if r[6] is None and r[1] > m.DOGN_UKLASSIFISERT}
    assert {m.LIGNENDE, m.NYHETSBREV_ANNEN} <= ikke
    # …og et menneskes dom på en stille avsender STÅR.
    assert any(r[2] == m.STILLE and r[3] and r[6][3] == "menneske"
               for r in med)
    # En lukket, en i køen (to), ett utkast.
    assert sum(1 for r in med if r[4] == "lukk") == 1
    assert sum(1 for r in med if r[4] == "unntakskoe") == 2
    assert sum(1 for r in med if r[4] == "utkast") == 1
    # Uten regler: den stille avsenderen ER et funn.
    uten = sett["uten_regler"]
    assert any(r[2] == m.STILLE and "uklassifisert_over_grense" in r[5]
               for r in uten)

    # GRENSENE ER BUNDET TIL SETTET: krymper det, faller porten.
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m17-fasit-v1"]
    assert sum(len(r) for r in sett.values()) == g["henvendelser_eksakt"]
    assert sum(len(r[5]) for rader in sett.values() for r in rader
               if r[5] != m.LUKKET) == g["ventede_funn_eksakt"]
    assert sum(1 for rader in sett.values() for r in rader
               if r[6] and r[6][3] == "regel") == g["regelklassifisert_eksakt"]
    assert sum(sum(m.forventet_evidens(r, k == "med_regler").values())
               for k, r in sett.items()) == g["evidenshendelser_eksakt"]


def test_settet_er_bundet_til_bytene_ikke_til_navnet_sitt():
    import hashlib
    m = _lib()
    assert m.sett_sha256() == hashlib.sha256(
        (ROT / "deploy/staging/m17_fasit.py").read_bytes()).hexdigest()


def _kjor(m):
    from db.pg import koble
    tenanter = 0

    def regelrunde():
        from plan import stilleregler
        v = koble(PLAN_DSN)
        try:
            r = stilleregler.kjor_en_runde(v)
        finally:
            v.close()
        assert not r.get("av"), "regelrunden er slått av"

    def sveip():
        nonlocal tenanter
        from drift import henvendelsessveip
        v = koble(SVEIP_DSN)
        try:
            r = henvendelsessveip.kjor(v)
        finally:
            v.close()
        assert not r.feilet, "henvendelsessveipen feilet"
        assert not r.hoppet_over, "sveipen fant arbeidernøkkelen opptatt"
        assert r.avkortet == 0, "sveipen traff taket — fasiten er avkortet"
        tenanter = r.tenanter

    rt = _rt()
    try:
        kjoring = m.kjor_sett(m.ny_runde(), rt, regelrunde, sveip)
    finally:
        rt.close()
    return kjoring, tenanter


@pg
@roller
def test_fasiten_er_lik_lokalt(migrator):  # noqa: F811
    """HELE settet gjennom hele kjeden lokalt. DENNE er «likt lokalt»."""
    m = _lib()
    kjoring, _ = _kjor(m)
    avvik = []
    for rolle, d in sorted(kjoring.items()):
        for akse in m.AKSER:
            avvik += [f"{rolle}/{akse}: {a}" for a in d["avvik"][akse]]
    assert not avvik, "avvik mot fasit:\n  " + "\n  ".join(avvik)


@pg
@roller
def test_artefaktet_bestar_sitt_eget_skjema(migrator):  # noqa: F811
    """Artefaktet CI bygger av en lokal kjøring må passere NØYAKTIG de
    portene staging-artefaktet passerer."""
    from manifestskjema import (_sjekk_grenser, m17_bevisrot_sha256,
                                valider_artefaktformat)
    m = _lib()
    kjoring, tenanter = _kjor(m)
    art = m.artefakt(kjoring, "lokal", "2026-09-16T00:00:00+00:00", 12,
                     tenanter, m17_bevisrot_sha256())
    assert art["bestatt"] is True, art["avvik"]
    assert valider_artefaktformat(art, "m17-fasit-v1") == []
    assert _sjekk_grenser("m17-fasit-v1", art) == []

    annen = dict(art, oppsett=dict(art["oppsett"], sett_sha256="0" * 64))
    assert any("sett_sha256" in f
               for f in _sjekk_grenser("m17-fasit-v1", annen))
    # Ett avvik feller punktet — på hver av de fire aksene.
    for akse in m.AKSER:
        rodt = dict(art, maalt=dict(art["maalt"], **{akse: 1}))
        assert _sjekk_grenser("m17-fasit-v1", rodt), \
            f"porten så ikke {akse}"
    # Et krympet eller utvidet sett er et annet sett.
    for delta in (-1, +1):
        annet = dict(art, maalt=dict(
            art["maalt"],
            henvendelser=art["maalt"]["henvendelser"] + delta))
        assert _sjekk_grenser("m17-fasit-v1", annet), \
            f"porten så ikke et sett med {delta:+d} henvendelse"
    # En regelrunde som aldri klassifiserte noe er ikke fasiten.
    uten = dict(art, maalt=dict(art["maalt"], regelklassifisert=0))
    assert any("regelklassifisert" in f
               for f in _sjekk_grenser("m17-fasit-v1", uten))
    umalt = dict(art, maalt=dict(art["maalt"], sveipetid_ms=0))
    assert any("tok tiden" in f
               for f in _sjekk_grenser("m17-fasit-v1", umalt))
    tom = dict(art, maalt=dict(art["maalt"], sveip_tenanter=1))
    assert any("gjøre ingenting" in f
               for f in _sjekk_grenser("m17-fasit-v1", tom))
