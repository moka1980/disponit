"""063/#165: fornyelsesveien — heartbeat fra den sittende utføreren.

Løftet: en LEVENDE utfører holder autoriteten sin gjennom hele
utførelsesfristen (lease + fersk opplastingskapabilitet, vindu for
vindu), og INGEN andre kan det — død lease er død, fremmed identitet er
én kode, og en rullet modulepoch feller heartbeatet.
"""
import pytest

from .test_api import (DSN, MIGRATOR_DSN, app, dekker, klient,  # noqa: F401
                       migrator, miljo)
from db.pg import sett_kontekst


def _ktx(migrator, claim):
    sett_kontekst(migrator, claim["tenant"], "test", "r-lease")

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _kjede_og_claim(migrator, monkeypatch, **kjede):
    """WCAG-kjeden er den etablerte claim-riggen (modul + release +
    oppdrag + modultoken) — fornyelsen er typeagnostisk, så veien inn er
    likegyldig; det som måles er claim-livssyklusen."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    from .test_modul_onboarding_http import _onboard_token
    from .test_wcag_kontroll import _wcag_kjede

    modul, rel, opp = _wcag_kjede(migrator, monkeypatch, **kjede)
    a = lag_app(DSN)
    c = TestClient(a)
    c.__enter__()
    mtk, _ = _onboard_token(c, migrator, modul, rel)
    hode = {"authorization": f"Bearer {mtk}"}
    claim = c.post("/v1/oppdrag/claim", json={}, headers=hode).json()
    assert claim.get("oppdrag_id"), claim
    return c, hode, claim


def _forny(c, hode, claim, **over):
    kropp = {"oppdrag_id": claim["oppdrag_id"],
             "owner_claim_id": claim["owner_claim_id"],
             "owner_generation": claim["owner_generation"],
             "lease_s": 600}
    kropp.update(over)
    return c.post("/v1/oppdrag/forny", json=kropp, headers=hode)


@pg
def test_fornyelsen_holder_leasen_og_reutsteder_kapabiliteten(
        migrator, miljo, monkeypatch):
    """Happy path: leasen står i framtiden, aldri forbi fristen, og gikk
    ALDRI bakover — pluss en FERSK opplastingskapabilitet (claimens egen
    var klemt til sitt eget vindu og skal ikke være svaret).

    Riggens frist ligger INNENFOR grant-taket, så 037 strakk leasen helt
    til fristen ved claim. Da er heartbeatet en no-op, og det er selve
    porten: en fornyelse som skrev `now() + lease_s` rått ville KORTET
    eksklusiviteten fra «til fristen» til «ett vindu» og åpnet reclaim
    midt i eierens lovlige arbeid (Cursor P1). Den EKTE forlengelsen —
    frist forbi taket — måles i
    `test_fornyelsen_kjeder_grant_over_taket_uten_reclaim`.
    """
    c, hode, claim = _kjede_og_claim(migrator, monkeypatch)
    try:
        _ktx(migrator, claim)
        for_lease, frist = migrator.execute(
            "SELECT owner_lease_utloper, utforelsesfrist"
            " FROM oppdrag WHERE id=%s",
            (claim["oppdrag_id"],)).fetchone()
        migrator.rollback()
        # Premisset, uttalt: uten 037-strekket måler testen under noe
        # annet enn den tror.
        assert for_lease == frist, \
            "riggen bærer ikke 037-strekket — porten under er tannløs"
        r = _forny(c, hode, claim)
        assert r.status_code == 200, r.text
        svar = r.json()
        _ktx(migrator, claim)
        rad = migrator.execute(
            "SELECT owner_lease_utloper,"
            " owner_lease_utloper > now(),"
            " owner_lease_utloper <= utforelsesfrist,"
            " owner_lease_utloper >= %s"
            " FROM oppdrag WHERE id=%s",
            (for_lease, claim["oppdrag_id"])).fetchone()
        migrator.rollback()
        assert rad[1], "leasen ble ikke fornyet inn i framtiden"
        assert rad[2], "fornyelsen gikk forbi utførelsesfristen"
        assert rad[3], \
            "fornyelsen KORTET leasen 037 alt hadde strukket til fristen"
        assert svar["owner_lease_utloper"] == rad[0].isoformat()
        opl = svar["opplasting"]
        if claim.get("opplasting"):
            assert opl is not None, \
                "claimen fikk kapabilitet, fornyelsen skal også"
            assert opl["jti"] != claim["opplasting"]["jti"], \
                "fornyelsen resirkulerte claimens jti"
    finally:
        c.__exit__(None, None, None)


@pg
def test_fornyelsen_kjeder_grant_over_taket_uten_reclaim(
        migrator, miljo, monkeypatch):
    """#165s EGET scenario, som ellers ingen test rører: en frist LENGRE
    enn ett grant-vindu.

    Riggens standardfrist ligger innenfor `UTSTEDT_AUTORITET_S`, så
    testene rundt måler bare at fornyelsen ikke SKADER — ikke at den
    virker. Her fødes oppdraget med en frist langt forbi taket (fristene
    er immutable, så den kan ikke skrues på i etterkant), leasen settes
    nær utløp — nøyaktig tilstanden en levende utfører står i mot slutten
    av et vindu — og heartbeatet skal kjede autoriteten videre: leasen
    FRAM, aldri forbi taket eller fristen, og køen forblir stengt for alle
    andre så lenge leasen lever. Uten denne porten kunne både
    UPDATE-formelen og grant-kjeden regrere grønt.
    """
    import oppdragskontrakt as ok
    tak = ok.UTSTEDT_AUTORITET_S
    c, hode, claim = _kjede_og_claim(migrator, monkeypatch, frist_s=tak * 4)
    try:
        _ktx(migrator, claim)
        # Claimen tok hele taket (037 strekker mot fristen); spol fram til
        # vindusslutt. Owner-feltene er de eneste `oppdrag_kolonnelaas`
        # slipper gjennom — fristen er det ikke, og det er derfor riggen
        # måtte føde den.
        migrator.execute(
            "UPDATE oppdrag SET owner_lease_utloper = now()+interval '30 s'"
            " WHERE id=%s", (claim["oppdrag_id"],))
        migrator.commit()
        _ktx(migrator, claim)
        for_lease = migrator.execute(
            "SELECT owner_lease_utloper FROM oppdrag WHERE id=%s",
            (claim["oppdrag_id"],)).fetchone()[0]
        migrator.rollback()

        r = _forny(c, hode, claim, lease_s=600)
        assert r.status_code == 200, r.text
        _ktx(migrator, claim)
        rad = migrator.execute(
            "SELECT utforelsesfrist > now() + %s * interval '1 s',"
            " owner_lease_utloper > %s,"
            " owner_lease_utloper <= now() + interval '600 s',"
            " owner_lease_utloper <= utforelsesfrist"
            " FROM oppdrag WHERE id=%s",
            (tak, for_lease, claim["oppdrag_id"])).fetchone()
        migrator.rollback()
        assert rad[0], "riggen fødte ikke en frist forbi grant-taket"
        assert rad[1], "heartbeatet kjedet ikke autoriteten videre"
        assert rad[2], "fornyelsen ga mer enn det bedte vinduet"
        assert rad[3], "fornyelsen gikk forbi utførelsesfristen"
        # …og ingen ANNEN utfører kommer inn mens leasen lever: riggen
        # bærer ett oppdrag, reclaim-grenen krever en DØD lease, og køen
        # svarer derfor 204 (tom) — ikke raden vi nettopp fornyet.
        p = c.post("/v1/oppdrag/claim", json={}, headers=hode)
        assert p.status_code == 204, p.text
    finally:
        c.__exit__(None, None, None)


@pg
@dekker("lease_ikke_fornybar")
def test_fremmed_identitet_er_en_kode(migrator, miljo, monkeypatch):
    """Feil claim_id, feil generasjon og fremmed oppdrag er SAMME 404 —
    et oppslagsverk over andres claims skal ikke finnes."""
    c, hode, claim = _kjede_og_claim(migrator, monkeypatch)
    try:
        for over in ({"owner_claim_id": "andres-claim-000000000000"},
                     {"owner_generation": claim["owner_generation"] + 1},
                     {"oppdrag_id": claim["oppdrag_id"] + 999983}):
            r = _forny(c, hode, claim, **over)
            assert r.status_code == 404, (over, r.text)
            assert r.json()["feil"] == "lease_ikke_fornybar", r.text
        # …og raden står URØRT av avvisningene.
        _ktx(migrator, claim)
        rad = migrator.execute(
            "SELECT owner_generation FROM oppdrag WHERE id=%s",
            (claim["oppdrag_id"],)).fetchone()
        migrator.rollback()
        assert rad[0] == claim["owner_generation"]
    finally:
        c.__exit__(None, None, None)


@pg
@dekker("lease_utlopt")
def test_dod_lease_kan_aldri_fornyes(migrator, miljo, monkeypatch):
    """Fencing-kjernen: etter utløp kan en annen utfører lovlig ha
    reclaimet — en gjenoppstandelse ville slåss med generasjonen i
    stedet for å respektere den. Positiv kontroll først: samme kall
    VIRKET mens leasen levde."""
    c, hode, claim = _kjede_og_claim(migrator, monkeypatch)
    try:
        assert _forny(c, hode, claim).status_code == 200
        _ktx(migrator, claim)
        migrator.execute(
            "UPDATE oppdrag SET owner_lease_utloper=now()-interval '1 s'"
            " WHERE id=%s", (claim["oppdrag_id"],))
        migrator.commit()
        r = _forny(c, hode, claim)
        assert r.status_code == 409, r.text
        assert r.json()["feil"] == "lease_utlopt", r.text
        _ktx(migrator, claim)
        rad = migrator.execute(
            "SELECT owner_lease_utloper < now() FROM oppdrag WHERE id=%s",
            (claim["oppdrag_id"],)).fetchone()
        migrator.rollback()
        assert rad[0], "avvisningen gjenopplivet leasen"
    finally:
        c.__exit__(None, None, None)


@pg
def test_rullet_modulepoch_feller_heartbeatet(migrator, miljo, monkeypatch):
    """Port 24-formen: nøddeaktivering løfter modulhode.module_epoch, og
    en deployment som er rullet forbi skal ikke kunne holde liv i et
    gammelt claim. Målt mot LEVENDE modulhode i døren."""
    c, hode, claim = _kjede_og_claim(migrator, monkeypatch)
    try:
        _ktx(migrator, claim)
        rad = migrator.execute(
            "SELECT modul_id, module_epoch FROM oppdrag WHERE id=%s",
            (claim["oppdrag_id"],)).fetchone()
        if rad[1] is None:
            pytest.skip("claimen bar ingen epoch (legacy-vei)")
        migrator.execute(
            "UPDATE modulhode SET module_epoch = module_epoch + 1"
            " WHERE modul_id=%s", (rad[0],))
        migrator.commit()
        r = _forny(c, hode, claim)
        assert r.status_code == 403, r.text
        assert r.json()["feil"] == "modulepoch_utdatert", r.text
    finally:
        # Epoken er MONOTON (modulhode_statemaskin) — den rulles aldri
        # tilbake. Riggen føder ferske moduler per test, så løftet
        # etterlater ingenting delt.
        c.__exit__(None, None, None)


@pg
def test_fornyelsen_leser_en_og_samme_klokke(migrator):
    """Sjekk og skriving må lese SAMME klokke — ellers er heartbeatet
    selv TOCTOU-en.

    Blandet (`clock_timestamp()` i sjekken, `now()` i skrivingen) kan en
    TX som har levd en stund — og radlåsen i døren er nettopp der en
    fornyelse venter — få sjekken til å si «leasen lever» mens UPDATE
    skriver en utløper som alt ligger bak veggklokken: commit av en død
    lease, altså et reclaim-vindu åpnet av veien som skulle holde
    autoriteten.

    Klokka er veggklokken, ikke `now()`: 062/#205 felte defektklassen for
    `modultoken_fortsatt_autorisert`, og 060:101 måler claim-leasen på
    samme klokke. Porten måles på den UTRULLEDE kroppen — filen kan ligge
    der uavspilt, og det er formen basen kjører som holder leasen.

    Repro-veien Cursor foreslo (åpen TX + `pg_sleep` forbi `v_lease`)
    krever at API-transaksjonen selv gjøres treg, altså ny maskin i en
    fiksrunde (K1). Invarianten pinnes derfor der den bor.
    """
    import re
    kropp = migrator.execute(
        "SELECT pg_get_functiondef('public.forny_oppdragslease"
        "(bigint,text,text,int,int)'::regprocedure)").fetchone()[0]
    migrator.rollback()
    # Kommentarene i kroppen SITERER 037s `now()`-predikat med vilje, så
    # de strykes før målingen. Linjekommentarer er entydige her: ingen
    # strengliteral i funksjonen inneholder «--».
    kode = re.sub(r"--[^\n]*", "", kropp)
    assert "now()" not in kode.lower(), \
        ("fornyelsen blander klokker — `now()` i den utrullede kroppen:\n"
         + kropp)
    assert kode.lower().count("clock_timestamp()") >= 3, \
        ("veggklokken mangler i sjekk eller skriving:\n" + kropp)


@pg
def test_uautentisert_heartbeat_avvises(klient, miljo):
    """Uten modultoken finnes ingen deployment å spørre — 401 før noe
    leses."""
    r = klient.post("/v1/oppdrag/forny",
                    json={"oppdrag_id": 1, "owner_claim_id": "x" * 22,
                          "owner_generation": 1})
    assert r.status_code == 401, r.text
    assert r.json()["feil"] == "token_ugyldig"
