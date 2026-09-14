"""«Likt lokalt»-leddet for M-23s fasitartefakt — STÅENDE måling.

Det samme settet (`deploy/staging/m23_fasit.py`) som staging-artefaktet
drives av, kjøres her gjennom de EKTE dørene lokalt, og BEGGE fasitene
måles: aldersbøttene og funnene. Da er «det syntetiske datasettet er likt
lokalt» en port CI feller ved hver kjøring — ikke et minne fra en runde.

HELE VEIEN, IKKE BARE SQL-EN. Settet legges inn med kundens dører
(`m23_registrer_fordring`, `m23_sett_purreplan`, `m23_neste_trinn`, alle
som runtime-rollen), funnene skrives av `drift/fordringssveip.kjor()` som
SVEIPEROLLEN, og alt leses gjennom `api.fordring.svar_for` — funksjonen
`GET /v1/fordring` selv kaller. Det er nøyaktig den kjeden natten kjører.
En fasit målt på `m23_funnkandidater` direkte ville vært grønn selv om
sveipen aldri skrev en rad.

MUTASJONENE SOM FELLER DENNE — ni, hver KJØRT som `CREATE OR REPLACE`
mot basen under den ekte eierrollen, og hver med sin egen navngitte rad i
fallet. Ikke en liste over hva som burde felle den:

  1. `>=` → `>` på trinnkanten: «noyaktig_trinn1/2/3» mister funnet.
  2. `<` → `<=` i `ingen_purreplan`: «forfaller_i_dag» blir et funn.
  3. `> 90` → `>= 90`: «nitti_med_plan» får `forfalt_uten_trinn` i
     tillegg.
  4. `AND f.trinn = 0` fjernet: «over_90_paa_topptrinn» blir et funn, og
     en kunde som har purret ferdig får varselet om igjen hver natt.
  5.–8. Bøttekantene (`<= forfall`, `<= 30`, `<= 60`, `<= 90`): hver av
     dem flytter én navngitt fordring, og BÅDE antallet og SUMMEN i to
     bøtter endres — de unike beløpene gjør summen til et fingeravtrykk
     og ikke bare antallet ganget med en konstant.
  9. `m23_evidens`-kallet fjernet fra `m23_neste_trinn`: trinnet flyttes
     fortsatt, funnene blir fortsatt riktige, og evidenskjeden går fra 21
     til 0 hendelser. Det er nettopp den stille varianten punktet
     `revisjonslogg_korrekt` finnes for.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo  # noqa: F401

ROT = Path(__file__).resolve().parents[3]
FORDRINGSVEIP_DSN = os.environ.get("DISPONIT_TEST_FORDRINGSVEIP_DSN")

pg = pytest.mark.skipif(not (DSN and MIGRATOR_DSN),
                        reason="test-DSN ikke satt")
#: Sveipen har sin EGEN login-rolle, og runtime har med vilje ikke
#: EXECUTE på den (104 REVOKEr den). Uten den rollen kan ikke fasiten
#: måles der den skal måles — på radene sveipen faktisk skrev — så da
#: hoppes det heller enn å måle et indre ledd og kalle det sertifisering.
sveip = pytest.mark.skipif(not FORDRINGSVEIP_DSN,
                           reason="DISPONIT_TEST_FORDRINGSVEIP_DSN ikke satt")


def _last(navn: str, fil: str):
    spec = importlib.util.spec_from_file_location(navn, ROT / fil)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _lib():
    return _last("m23_fasit", "deploy/staging/m23_fasit.py")


def _rt():
    from db.pg import koble
    return koble(DSN)


def test_settet_er_fasiten():
    """Settet ER kantene, deterministisk — og hver av dem er DEKKET.

    Porten teller ikke rader; den krever at hver KANT finnes i settet.
    En fasit som mistet «noyaktig_trinn2» ville fortsatt hatt 16 rader.
    """
    m = _lib()
    sett = dict(m.bygg_sett())
    assert m.bygg_sett() == m.bygg_sett()              # deterministisk

    dogn = {merke: d for merke, d, _t, _f, _b in sett["med_plan"]}
    # Bøttekantene: nøyaktig på, og én på hver side.
    for kant in (0, 1, 30, 31, 60, 61, 90, 91):
        assert kant in dogn.values(), f"bøttekanten {kant} døgn mangler"
    # Trinnkantene fra PLAN, og dagen før hver av dem.
    for trinn in m.PLAN:
        g = trinn["dogn_etter_forfall"]
        assert g in dogn.values(), f"trinnkanten {g} døgn mangler"
        assert g - 1 in dogn.values(), f"dagen før {g} døgn mangler"
    # …og en fordring som bærer TO funn samtidig.
    assert any(len(f) == 2 for _m, _d, _t, f, _b in sett["med_plan"]), \
        "settet har ingen fordring med to samtidige funn"
    # …og en på topptrinnet, eldre enn 90 døgn.
    assert any(t == len(m.PLAN) and d > 90
               for _m, d, t, _f, _b in sett["med_plan"]), \
        "settet har ingen gammel fordring på topptrinnet"

    # Uten plan: kanten `forfall < p_dag` krever BÅDE 0 og 1 døgn.
    u = {merke: d for merke, d, _t, _f, _b in sett["uten_plan"]}
    assert 0 in u.values() and 1 in u.values()
    assert all(f == {("ingen_purreplan", None)} or not f
               for _m, _d, _t, f, _b in sett["uten_plan"])

    # EVIDENSFASITEN følger av de samme radene, og gulvet i KRAVGRENSER
    # er bundet til den: krymper settet, faller porten — den kan ikke
    # bestå mot en fasit den selv har gjort mindre.
    from manifestskjema import KRAVGRENSER
    sum_hendelser = sum(
        sum(m.forventet_evidens(rader, rolle == "med_plan").values())
        for rolle, rader in sett.items())
    g = KRAVGRENSER["m23-fasit-v1"]
    assert sum_hendelser == g["evidenshendelser_eksakt"]
    assert sum(len(r) for r in sett.values()) == g["fordringer_eksakt"]
    assert sum(len(f) for rader in sett.values()
               for (_m, _d, _t, f, _b) in rader) == g["ventede_funn_eksakt"]


def test_settet_er_bundet_til_bytene_ikke_til_navnet_sitt():
    """«Likt lokalt» er en påstand om SETTET, ikke om summene.

    `sett_versjon` er en håndholdt streng. Et staging-ledd på en eldre
    utrulling kunne drevet helt andre fordringer til de samme bøttene og
    valideres som det samme settet. Bytene er bindingen, og de hashes i
    BEGGE ledd.
    """
    import hashlib
    m = _lib()
    assert m.sett_sha256() == hashlib.sha256(
        (ROT / "deploy/staging/m23_fasit.py").read_bytes()).hexdigest()


def test_bottefasiten_er_skrevet_ikke_utledet():
    """Bøttesummene er et FINGERAVTRYKK, ikke antallet ganget med en
    konstant: unike beløp gjør at to fordringer som bytter bøtte endrer
    summen selv når antallet står.

    MUTASJONEN SOM FELLER DENNE: la `belop_ore` returnere en konstant.
    """
    m = _lib()
    rader = dict(m.bygg_sett())["med_plan"]
    f = m.forventet_fordeling(rader)
    assert sum(a for a, _o in f.values()) == len(rader)
    # Flytt én fordring én bøtte til side: summen MÅ endre seg.
    flyttet = [(mk, d, t, fn, "31_60" if b == "1_30" else b)
               for mk, d, t, fn, b in rader]
    assert m.forventet_fordeling(flyttet) != f
    # …og beløpene er faktisk unike.
    belop = [m.belop_ore(i) for i in range(len(rader))]
    assert len(set(belop)) == len(belop)


@pg
@sveip
def test_fasiten_er_lik_lokalt(migrator):  # noqa: F811
    """HELE settet gjennom hele kjeden lokalt: kundens dører inn,
    fordringssveipen som sveiperollen, og flatens eget svar ut.

    DENNE er «likt lokalt». Testene over måler settet som DATA — de rører
    aldri basen. Uten dette leddet er `kjor_sett` uten kaller i hele
    treet, og en regresjon i registrering, sveip eller flate lar porten
    stå grønn mens den lokale fasiten ikke lenger stemmer.
    """
    m = _lib()

    def sveip_en_gang():
        from db.pg import koble
        from drift import fordringssveip
        v = koble(FORDRINGSVEIP_DSN)
        try:
            r = fordringssveip.kjor(v)
        finally:
            v.close()
        assert not r.feilet, "fordringssveipen feilet"
        assert not r.hoppet_over, "sveipen fant arbeidernøkkelen opptatt"
        assert r.avkortet == 0, "sveipen traff taket — fasiten er avkortet"

    rt = _rt()
    try:
        kjoring = m.kjor_sett(m.ny_runde(), rt, sveip_en_gang)
    finally:
        rt.close()

    avvik = []
    for rolle, d in sorted(kjoring.items()):
        avvik += [f"{rolle}/funn: {a}" for a in d["avvik"]["funnavvik"]]
        avvik += [f"{rolle}/bøtte: {a}" for a in d["avvik"]["botteavvik"]]
    assert not avvik, "avvik mot fasit:\n  " + "\n  ".join(avvik)


@pg
@sveip
def test_artefaktet_bestar_sitt_eget_skjema(migrator):  # noqa: F811
    """Artefaktet CI bygger av en lokal kjøring må passere NØYAKTIG de
    portene staging-artefaktet passerer — ellers oppdages formfeilen
    først når bevisrunden er kjørt og ikke kan gjøres om."""
    from manifestskjema import (_sjekk_grenser, m23_bevisrot_sha256,
                                valider_artefaktformat)
    m = _lib()

    tenanter = 0

    def sveip_en_gang():
        nonlocal tenanter
        from db.pg import koble
        from drift import fordringssveip
        v = koble(FORDRINGSVEIP_DSN)
        try:
            tenanter = fordringssveip.kjor(v).tenanter
        finally:
            v.close()

    rt = _rt()
    try:
        kjoring = m.kjor_sett(m.ny_runde(), rt, sveip_en_gang)
    finally:
        rt.close()

    art = m.artefakt(kjoring, "lokal", "2026-09-14T00:00:00+00:00", 12,
                     tenanter, m23_bevisrot_sha256())
    assert art["bestatt"] is True, art["avvik"]
    assert valider_artefaktformat(art, "m23-fasit-v1") == []
    assert _sjekk_grenser("m23-fasit-v1", art) == []

    # Et sett som ikke er det innsjekkede — samme tall, annet sett.
    annen = dict(art, oppsett=dict(art["oppsett"], sett_sha256="0" * 64))
    assert any("sett_sha256" in f
               for f in _sjekk_grenser("m23-fasit-v1", annen))
    # …og ett avvik er nok til å felle punktet — på hver av de tre
    # aksene. En port som bare så funnene ville latt en tapt
    # aldersfordeling eller en stum evidenskjede passere.
    for akse in ("funnavvik", "botteavvik", "evidensavvik"):
        rodt = dict(art, maalt=dict(art["maalt"], **{akse: 1}))
        assert _sjekk_grenser("m23-fasit-v1", rodt), \
            f"porten så ikke {akse}"
    # …og et sett som er KRYMPET eller UTVIDET er et annet sett, ikke et
    # dårligere. En nedre grense ville sluppet det utvidede gjennom.
    for delta in (-1, +1):
        annet = dict(art, maalt=dict(
            art["maalt"],
            fordringer=art["maalt"]["fordringer"] + delta))
        assert _sjekk_grenser("m23-fasit-v1", annet), \
            f"porten så ikke et sett med {delta:+d} fordring"
    # …og en sveip ingen tok tiden på er ikke en ytelsesmåling.
    umalt = dict(art, maalt=dict(art["maalt"], sveipetid_ms=0))
    assert any("tok tiden" in f
               for f in _sjekk_grenser("m23-fasit-v1", umalt))
    # …og en rask sveip som ikke rørte tenantene er ikke en ytelse.
    tom = dict(art, maalt=dict(art["maalt"], sveip_tenanter=1))
    assert any("gjøre ingenting" in f
               for f in _sjekk_grenser("m23-fasit-v1", tom))
