"""M-57s egen rapportflate ("ats"-diskriminatoren): den promoterte
evalueringsrapporten leses via SIN rute, aldri via WCAG-rendrerens — og
motsatt. 200-og-feiler-under-rendring-klassen er umulig per
konstruksjon når flatene filtrerer på hver sin diskriminator."""
import secrets

import pytest

from .test_api import (ANNEN_TENANT, DSN, MIGRATOR_DSN,  # noqa: F401
                       _lag_token, klient, migrator, miljo)
from .test_bestilling_rekruttering import (_adminsesjon, _bestill,
                                           _evalkropp, _profil,
                                           _rekr_policy,
                                           _sikre_m57_claimbar,
                                           _sett_kontekst)
from .test_inndata_http import inndata_rot  # noqa: F401
from .test_m57_controller import (_MAALINGER, _Modell, _Uttrekker,
                                  _bunt_via_http, _registrer_rapporttypen)

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

TENANT = "t-api"


def _utfort_oppdrag(migrator, klient_ubrukt, monkeypatch):
    """Hele kjeden til promotert rapport — controllertestens rigg."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    from modules.m57_ats import controller
    from .test_m37 import _signer_kvittering
    from .test_modul_onboarding_http import _onboard_token

    _rekr_policy(migrator)
    _sikre_m57_claimbar(migrator)
    _registrer_rapporttypen(migrator)
    rel = migrator.execute(
        "SELECT release_id FROM moduldeployment WHERE modul_id='m57_ats'"
        " AND livslop='claiming' LIMIT 1").fetchone()[0]
    migrator.rollback()
    a = lag_app(DSN)
    c = TestClient(a)
    c.__enter__()
    cookie, csrf = _adminsesjon()
    ref = _bunt_via_http(c, cookie, csrf)
    profilref = _profil(migrator)
    r = _bestill(c, cookie, csrf, _evalkropp(ref, profilref),
                 "n-" + secrets.token_hex(8))
    assert r.status_code == 200 and r.json()["beslutning"] == "tillat", r.text
    oppdrag_id = r.json()["oppdrag_id"]
    mtk, _ = _onboard_token(c, migrator, "m57_ats", rel)
    res = controller.kjor_en(c, mtk, _Modell(), _Uttrekker(),
                             _MAALINGER, _signer_kvittering)
    assert res["utfall"] == "utfort", res
    return c, cookie, csrf, oppdrag_id


def _fremmed_artefakttype(migrator) -> str:
    """En registrert artefakttype som IKKE er kontraktens rapporttype,
    bundet til samme m57-kontrakt (så konvolutten under kan gjenbrukes)."""
    from .test_wcag_kontroll import _streng_type
    kh = migrator.execute(
        "SELECT kontrakt_hash FROM modulkontrakt"
        " WHERE modul_id='m57_ats' AND kontraktversjon=1").fetchone()[0]
    migrator.rollback()
    return _streng_type(migrator, "m57_ats", kh)


def _promoter_kopi(migrator, fra_oid, til_oid, artefakttype):
    """Promoter et artefakt av `artefakttype` på `til_oid`, med samme
    konvolutt som den ekte rapporten på `fra_oid` — og NYERE enn den.

    Konvolutten kopieres nettopp fordi innholdet er likegyldig her: det
    er artefakt*typen* leseveien skal dømme på, ikke payloaden."""
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype, modul_id,"
        " release_id, kontraktversjon, kontrakt_hash, module_epoch,"
        " tilstand, storrelse_bytes, klartekst_sha256, ciphertext, nonce,"
        " dek_ref, kapabilitet_jti, promotert_ts)"
        " SELECT tenant, %s, %s, modul_id, release_id, kontraktversjon,"
        "        kontrakt_hash, module_epoch, 'promotert', storrelse_bytes,"
        "        klartekst_sha256, ciphertext, nonce, dek_ref, %s,"
        "        now() + interval '1 minute'"
        "   FROM artefakt"
        "  WHERE tenant=%s AND oppdrag_id=%s AND tilstand='promotert'"
        "  ORDER BY promotert_ts DESC LIMIT 1",
        (til_oid, artefakttype, "jti-" + secrets.token_hex(8),
         TENANT, fra_oid))
    migrator.commit()


@pg
def test_rapporten_leses_paa_sin_egen_flate(migrator, miljo, inndata_rot,
                                            monkeypatch):
    from api import sesjon as sesjonmodul

    import oppdragskontrakt

    c, cookie, csrf, oid = _utfort_oppdrag(migrator, klient, monkeypatch)
    try:
        ck = {sesjonmodul.C_SESJON: cookie}
        r = c.get(f"/v1/rekruttering/rapport/{oid}", cookies=ck)
        assert r.status_code == 200, r.text
        k = r.json()
        rapport = k["rapport"]
        assert rapport["rapporttype"] == "rekruttering.evaluering.rapport"
        assert rapport["rangering"][0]["kandidat_id"] == "k1"
        # Lageret er kryptert i ro, og dekrypteringen skjer på serveren —
        # klienten skal aldri se konvolutten (speil av WCAG-veien).
        for hemmelig in ("ciphertext", "nonce", "dek_ref"):
            assert hemmelig not in k, f"{hemmelig} lekket til klienten"
        # KRYSS-FLATE-ISOLASJON, begge veier: WCAG-rendrerens rute skal
        # aldri servere ats-formen — 404, ikke 200-og-feiler-hos-klienten.
        # (Den andre retningen står i `test_rapport_lese_api`, der en
        # promotert WCAG-rapport finnes.)
        rw = c.get(f"/v1/rapport/{oid}", cookies=ck)
        assert rw.status_code == 404, rw.text

        # Listeveien: raden finnes, status utført, rapport klar.
        rl = c.get("/v1/rekruttering/evalueringer", cookies=ck)
        assert rl.status_code == 200, rl.text
        rad = next(e for e in rl.json()["evalueringer"]
                   if e["oppdrag_id"] == oid)
        assert rad["status"] == "utfort" and rad["rapport_klar"] is True

        # Upromotert og ukjent nummer er samme 404.
        assert c.get("/v1/rekruttering/rapport/999999",
                     cookies=ck).status_code == 404

        # EN FREMMED ARTEFAKTTYPE ER IKKE EN RAPPORT (Cursor P2, speil av
        # WCAG-veiens negativer). Ruta plukker det NYESTE promoterte
        # artefaktet på oppdraget, så uten typefilteret avgjør rekkefølgen
        # hva flaten får — og `evalueringSeksjon` dereferer
        # `rapport.rangering`/`profil` med en gang. To halvdeler:
        at = oppdragskontrakt.OPPDRAGSTYPER[
            "rekruttering.evaluering"].rapport_artefakttype
        fremmed = _fremmed_artefakttype(migrator)

        #   (a) et NYERE fremmed artefakt skygger ikke for rapporten,
        _promoter_kopi(migrator, oid, oid, fremmed)
        r3 = c.get(f"/v1/rekruttering/rapport/{oid}", cookies=ck)
        assert r3.status_code == 200, r3.text
        assert r3.json()["artefakttype"] == at, \
            "et fremmed artefakt skygget for rapporten"

        #   (b) ... og et oppdrag som BARE har et fremmed artefakt er 404,
        #       samme dokumenterte «ikke funnet» som uten promotering.
        r_b = _bestill(c, cookie, csrf,
                       _evalkropp(_bunt_via_http(c, cookie, csrf),
                                  _profil(migrator)),
                       "n-" + secrets.token_hex(8))
        assert r_b.status_code == 200 and r_b.json()["beslutning"] == "tillat", \
            r_b.text
        oid2 = r_b.json()["oppdrag_id"]
        _promoter_kopi(migrator, oid, oid2, fremmed)
        assert c.get(f"/v1/rekruttering/rapport/{oid2}",
                     cookies=ck).status_code == 404
        # … og listen sier det samme: ingen rapport å vise.
        rad2 = next(e for e in c.get("/v1/rekruttering/evalueringer",
                                     cookies=ck).json()["evalueringer"]
                    if e["oppdrag_id"] == oid2)
        assert rad2["rapport_klar"] is False

        # IDENTISK 404 FOR UKJENT OG ANNEN TENANTS OPPDRAG (Cursor P2,
        # PR-008-porten for lese-API). RLS og `auth.tenant`-leddet finnes
        # i spørringen, men var ubevist for denne flaten: uten porten er
        # detaljruten et orakel over andres oppdragsnumre — «404 ikke
        # funnet» mot «404 finnes, men ikke for deg» skiller seg i det
        # øyeblikket de to svarene ikke er byte-like. `oid` er her et
        # oppdrag som beviselig svarer 200 for SIN tenant, så et 404 til
        # naboen kan bare komme fra tenantfilteret.
        annen, _ = _lag_token(migrator, ANNEN_TENANT, "bruker",
                              ["decisions:read"])
        # Naboen er en MASKINPRINSIPAL, og de to prinsipalveiene er
        # gjensidig utelukkende (`dobbel_principal`, v2 §8): en
        # sesjonskake liggende i klientens krukke ville gjort svaret 400
        # i stedet for det 404-et testen måler. Krukken tømmes derfor
        # eksplisitt — browserøkten er ferdig brukt her.
        c.cookies.clear()
        hode = {"authorization": f"Bearer {annen}"}
        ukjent = c.get("/v1/rekruttering/rapport/999999999", headers=hode)
        fremmed = c.get(f"/v1/rekruttering/rapport/{oid}", headers=hode)
        assert ukjent.status_code == fremmed.status_code == 404, \
            (ukjent.text, fremmed.text)
        a1, a2 = ukjent.json(), fremmed.json()
        a1.pop("request_id"), a2.pop("request_id")
        assert a1 == a2 == {"feil": "ikke_funnet"}
        # ... og listen lekker ikke naboens oppdrag inn i egen historikk.
        rn = c.get("/v1/rekruttering/evalueringer", headers=hode)
        assert rn.status_code == 200, rn.text
        assert not [e for e in rn.json()["evalueringer"]
                    if e["oppdrag_id"] in (oid, oid2)], \
            "en annen tenants evalueringer sto i listen"
    finally:
        c.__exit__(None, None, None)


@pg
def test_leserutene_krever_decisions_read(migrator, miljo):
    """Begge de nye rutene bærer dekryptert evalueringspayload
    (detaljen) og oppdragsmeta (listen) under `decisions:read` — og
    porten er ubevist så lenge suitene bare kjører happy path.

    Kontroll: fjern scope-deklarasjonen for en av rutene i `app.py`, så
    blir denne rød."""
    from starlette.testclient import TestClient
    from api.app import lag_app

    # `exceptions:read` er et EKTE lesescope: tokenet er gyldig og
    # rollen riktig, det er nøyaktig `decisions:read` som mangler.
    uten, _ = _lag_token(migrator, TENANT, "bruker", ["exceptions:read"])
    med, _ = _lag_token(migrator, TENANT, "bruker", ["decisions:read"])
    a = lag_app(DSN)
    with TestClient(a) as c:
        for sti in ("/v1/rekruttering/rapport/999999999",
                    "/v1/rekruttering/evalueringer"):
            r = c.get(sti, headers={"authorization": f"Bearer {uten}"})
            assert (r.status_code, r.json()["feil"]) \
                == (403, "scope_mangler"), f"{sti}: {r.text}"
            # Uten prinsipal i det hele tatt finnes ingen lesevei.
            ru = c.get(sti)
            assert (ru.status_code, ru.json()["feil"]) \
                == (401, "token_ugyldig"), f"{sti}: {ru.text}"
            # ... og MED scopet slipper den samme forespørselen forbi
            # porten (404/200, aldri 403) — ellers ville negativene over
            # kunne bestå av feil grunn.
            rm = c.get(sti, headers={"authorization": f"Bearer {med}"})
            assert rm.status_code != 403, f"{sti}: {rm.text}"


@pg
def test_listeveien_viser_ogsaa_uferdige(migrator, miljo, inndata_rot,
                                         monkeypatch):
    """Et nettopp bestilt oppdrag står i listen som ventende med
    `rapport_klar: false` — flaten skal kunne vise fremdrift, ikke bare
    fasit."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    from api import sesjon as sesjonmodul

    _rekr_policy(migrator)
    _sikre_m57_claimbar(migrator)
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            cookie, csrf = _adminsesjon()
            ref = _bunt_via_http(c, cookie, csrf)
            profilref = _profil(migrator)
            r = _bestill(c, cookie, csrf, _evalkropp(ref, profilref),
                         "n-" + secrets.token_hex(8))
            assert r.status_code == 200, r.text
            oid = r.json()["oppdrag_id"]
            rl = c.get("/v1/rekruttering/evalueringer",
                       cookies={sesjonmodul.C_SESJON: cookie})
            rad = next(e for e in rl.json()["evalueringer"]
                       if e["oppdrag_id"] == oid)
            assert rad["rapport_klar"] is False
            assert c.get(f"/v1/rekruttering/rapport/{oid}",
                         cookies={sesjonmodul.C_SESJON: cookie}
                         ).status_code == 404
    finally:
        pass
