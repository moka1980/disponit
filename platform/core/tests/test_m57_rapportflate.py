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


def _utfort_oppdrag(migrator, klient_ubrukt, monkeypatch, ekstra=None):
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
    kropp = _evalkropp(ref, profilref)
    if ekstra:
        kropp.update(ekstra)
    r = _bestill(c, cookie, csrf, kropp, "n-" + secrets.token_hex(8))
    assert r.status_code == 200 and r.json()["beslutning"] == "tillat", r.text
    oppdrag_id = r.json()["oppdrag_id"]
    mtk, _ = _onboard_token(c, migrator, "m57_ats", rel)
    res = controller.kjor_en(c, mtk, _Modell(), _Uttrekker(),
                             _MAALINGER, _signer_kvittering)
    assert res["utfall"] == "utfort", res
    return c, cookie, csrf, oppdrag_id


def _fremmed_artefakttype(migrator) -> str:
    """En registrert artefakttype som IKKE er kontraktens rapporttype.

    Bundet til en EGEN kontraktversjon (2), aldri claimens (v1):
    opplastingskapabiliteten utstedes bare når claim-kontrakten har
    NØYAKTIG ÉN registrert type (fail-closed, `app.py` `LIMIT 2`/
    `len==1`), og `artefakttype_register` er append-only — en fremmed
    type på v1 ville overlevd sesjonen og stille drept kapabiliteten i
    NESTE kjøring av riggen."""
    from .test_wcag_kontroll import STRENGT, _mk_admin, _registrer_skjema
    migrator.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,"
        "kontrakt_hash,payload_schema_hash,kvittering_schema_hash,"
        "sideeffektklasse,reversibilitet)"
        " VALUES ('m57_ats',2,%s,'p','k','krever_outbox','kompenserende')"
        " ON CONFLICT DO NOTHING", ("k2-" + secrets.token_hex(8),))
    migrator.commit()
    kh2 = migrator.execute(
        "SELECT kontrakt_hash FROM modulkontrakt"
        " WHERE modul_id='m57_ats' AND kontraktversjon=2").fetchone()[0]
    migrator.rollback()
    # `_streng_type` hardkoder versjon 1 — samme form, men mot v2.
    h = _registrer_skjema(STRENGT)
    at = f"kontroll.t{secrets.token_hex(4)}.rapport"
    da = _mk_admin("disponit_domains_admin")
    try:
        da.execute("SELECT registrer_artefakttype(%s,'m57_ats',2,%s,%s,"
                   "'test')", (at, kh2, h))
        da.commit()
    finally:
        da.close()
    return at


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


def _ankere(m, oid) -> tuple:
    """(alle, levende) retensjonsankere for oppdraget — leseveiens egen
    målestokk (`slettet_ts IS NULL` er nøyaktig EXISTS-leddet i
    `rekrutteringsrapport_detalj`). Egen transaksjon: claimen committet i
    appens pool, og en åpen migrator-transaksjon ville lest et snapshot
    fra før den."""
    m.rollback()
    _sett_kontekst(m, TENANT)
    rad = m.execute(
        "SELECT count(*), count(*) FILTER (WHERE slettet_ts IS NULL)"
        "  FROM rekrutteringsprosess WHERE tenant=%s AND oppdrag_id=%s",
        (TENANT, oid)).fetchone()
    m.rollback()
    return (rad[0], rad[1])


@pg
def test_rapporten_leses_paa_sin_egen_flate(migrator, miljo, inndata_rot,
                                            monkeypatch):
    from api import sesjon as sesjonmodul

    import oppdragskontrakt

    c, cookie, csrf, oid = _utfort_oppdrag(migrator, klient, monkeypatch)
    try:
        ck = {sesjonmodul.C_SESJON: cookie}
        # Retensjonsankeret: claimet fødte prosessen, og den terminale
        # kvitteringen LUKKET den — kundens frist løper fra avslutningen,
        # ikke fra claimet (Codex P2).
        _sett_kontekst(migrator, TENANT)
        anker = migrator.execute(
            "SELECT lukket_ts IS NOT NULL FROM rekrutteringsprosess"
            " WHERE tenant=%s AND oppdrag_id=%s", (TENANT, oid)).fetchone()
        migrator.rollback()
        assert anker is not None, "claimen skal ha født retensjonsankeret"
        assert anker[0], "terminal kvittering skal ha lukket ankeret"
        r = c.get(f"/v1/rekruttering/rapport/{oid}", cookies=ck)
        assert r.status_code == 200, r.text
        k = r.json()
        rapport = k["rapport"]
        # Nøkkelsubtraksjonen (Codex P2): kildeteksten — den tyngste og
        # mest persondatabærende delen — serveres ikke; funnene bærer
        # sine egne sitater, og de skal fortsatt være der.
        assert all("kildetekst" not in kand
                   for kand in rapport["kandidater"].values()), \
            "kildeteksten skal strippes fra lesesvaret"
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
        # En id forbi bigint er samme «ikke funnet» — aldri en
        # bind-/driftsfeil (Codex P2).
        r_stor = c.get("/v1/rekruttering/rapport/9223372036854775808",
                       cookies=ck)
        assert r_stor.status_code == 404, r_stor.text
        assert r_stor.json()["feil"] == "ikke_funnet"

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
            # Under vinduet: ingen avkorting å melde.
            assert rl.json()["flere"] is False
            assert c.get(f"/v1/rekruttering/rapport/{oid}",
                         cookies={sesjonmodul.C_SESJON: cookie}
                         ).status_code == 404
    finally:
        pass


@pg
def test_listen_avkorter_aldri_stille(migrator, miljo):
    """`flere` er MÅLT, ikke gjettet: nøyaktig 100 evalueringer er en
    komplett historikk (`flere: false`), 101 er avkortet (`flere: true`).
    Flaten skal kunne si at eldre finnes, aldri presentere de nyeste 100
    som alt (Cursor #220 P2-3) — og aldri påstå eldre som ikke finnes
    (Codex P2). Selve pagineringen bor i #221.

    MUTASJONEN SOM DREPER DENNE: `flere = len(rader) == 100` mot et
    `LIMIT 100`-vindu — grensearmen under rødner, fordi den påstanden
    ikke har SETT rad 101. Likeså: fjern `flere`-leddet, eller la den
    101. raden lekke ut i svaret."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    from api import sesjon as sesjonmodul
    from db import kryptering

    _sikre_m57_claimbar(migrator)
    _sett_kontekst(migrator, TENANT)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator, TENANT)
    ct, nonce = kryptering.krypter(dek, {"x": 1}, TENANT, key_id)

    def _seed(antall):
        # Konteksten er TRANSAKSJONSLOKAL (`set_config(...,true)`), og
        # `commit()` nedenfor avslutter transaksjonen den ble satt i.
        # Andre kall til `_seed` starter derfor en NY transaksjon uten
        # `disponit.tenant`, og RLS avviser da innsettingen i
        # `revisjonslogg`. Konteksten hører hjemme i hver seedende
        # transaksjon, ikke bare i den første.
        _sett_kontekst(migrator, TENANT)
        for _ in range(antall):
            # `oppdrag_en_per_beslutning`: hvert oppdrag krever sin egen
            # beslutningsloggpost.
            logg = migrator.execute(
                "INSERT INTO revisjonslogg (tenant, aktor, kilde,"
                " input_hash, policy_id, beslutning, begrunnelse,"
                " idempotency_key)"
                " VALUES (%s,'test','api_token','ih','p@1.0.0/x.y',"
                "'TILLAT','[]',%s) RETURNING id",
                (TENANT, secrets.token_hex(8))).fetchone()[0]
            migrator.execute(
                "INSERT INTO oppdrag (opprinnelse, tenant,"
                " beslutning_loggpost_id, oppdragstype, handling,"
                " eiermodul, payload_kryptert, key_id, nonce,"
                " utforelsesfrist, evidensfrist, koblingsstatus, status)"
                " VALUES ('beslutning',%s,%s,'rekruttering.evaluering',"
                "'rekruttering.evaluering','m57_ats',%s,%s,%s,"
                " now()+interval '4 hour', now()+interval '1 day',"
                "'KOBLET',"
                # Terminal fra fødselen: seedingen skal fylle VINDUET,
                # aldri bli claimet av en annen tests controller.
                "'kansellert')",
                (TENANT, logg, ct, key_id, nonce))
        migrator.commit()

    def _les(c, cookie):
        r = c.get("/v1/rekruttering/evalueringer",
                  cookies={sesjonmodul.C_SESJON: cookie})
        assert r.status_code == 200, r.text
        return r.json()

    _seed(100)
    a = lag_app(DSN)
    with TestClient(a) as c:
        cookie, _csrf = _adminsesjon()
        # GRENSEN (Codex P2): nøyaktig vindusstort er KOMPLETT, ikke
        # avkortet. `len(rader) == 100` mot et `LIMIT 100`-vindu ville
        # meldt `true` her uten å ha sett en eneste eldre rad.
        kropp = _les(c, cookie)
        assert len(kropp["evalueringer"]) == 100, \
            "vinduet skal være nøyaktig LIMIT-en"
        assert kropp["flere"] is False, \
            "nøyaktig 100 er hele historikken — ingen eldre å påstå"

        # Én rad OVER vinduet: nå FINNES det eldre, og først nå meldes det.
        _seed(1)
        kropp = _les(c, cookie)
        assert len(kropp["evalueringer"]) == 100, \
            "svaret skal aldri lekke den 101. beviseraden"
        assert kropp["flere"] is True, \
            "med rad 101 seedet skal avkortingen MELDES"


@pg
def test_reapet_prosess_stenger_rapporten(migrator, miljo):
    """SLETTEGRENSEN (Codex P1): det promoterte artefaktet er immutabelt
    og bærer funn, sitater og blindet kildetekst — men når prosessen er
    reapet skal rapporten være UTILGJENGELIG: identisk 404 på
    detaljruten, og listen slutter å reklamere (`rapport_klar: false`).

    Riggen er kandidatlagre-testenes (claimet oppdrag + prosess gjennom
    den herdede veien) pluss et direkte promotert artefakt — samme
    dømmekraft som `_promoter_kopi`: det er koblingen prosess→oppdrag
    leseveien dømmer på, ikke payloaden.

    MUTASJONEN SOM DREPER DENNE: fjern `NOT EXISTS(reapet prosess)`-leddet
    i én av de to spørringene."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    from api import sesjon as sesjonmodul
    from db import kryptering
    from .test_m57_kandidatlagre import _prosess, _reaperkobling
    from .test_m57_utsending import _rt as _rekrutt_rt

    from .test_m57_kandidatlagre import _claimet
    rt = _rekrutt_rt()
    try:
        # Fødselen deles opp: claimet oppdrag + artefakt FØRST, så måles
        # at en rapport UTEN retensjonsanker ikke serveres (EXISTS-formen
        # — mutasjonen tilbake til NOT EXISTS(reapet) rødner her), FØR
        # prosessen fødes og 200-armen tar over.
        oid, _ = _claimet(migrator)
        _sett_kontekst(migrator, TENANT)
        key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator,
                                                              TENANT)
        rapport = {"rapporttype": "rekruttering.evaluering.rapport"}
        ct, nonce = kryptering.krypter(dek, rapport, TENANT, key_id)
        kh = migrator.execute(
            "SELECT kontrakt_hash FROM modulkontrakt"
            " WHERE modul_id='m57_ats' AND kontraktversjon=1").fetchone()[0]
        rel = migrator.execute(
            "SELECT release_id FROM moduldeployment"
            " WHERE modul_id='m57_ats' AND livslop='claiming'"
            " LIMIT 1").fetchone()[0]
        epoch = migrator.execute(
            "SELECT module_epoch FROM modulhode"
            " WHERE modul_id='m57_ats'").fetchone()[0]
        migrator.execute(
            "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype,"
            " modul_id, release_id, kontraktversjon, kontrakt_hash,"
            " module_epoch, tilstand, storrelse_bytes, klartekst_sha256,"
            " ciphertext, nonce, dek_ref, kapabilitet_jti, promotert_ts)"
            " VALUES (%s,%s,'rekruttering.evaluering.rapport','m57_ats',"
            "%s,1,%s,%s,'promotert',10,'h',%s,%s,%s,%s, now())",
            (TENANT, oid, rel, kh, epoch, ct, nonce, key_id,
             "jti-" + secrets.token_hex(8)))
        migrator.commit()

        a = lag_app(DSN)
        with TestClient(a) as c:
            cookie, _csrf = _adminsesjon()
            ck = {sesjonmodul.C_SESJON: cookie}
            # UTEN anker: ikke funnet, og listen reklamerer ikke.
            assert c.get(f"/v1/rekruttering/rapport/{oid}",
                         cookies=ck).status_code == 404, \
                "en rapport uten retensjonsanker skal ikke serveres"
            rad0 = next(e for e in c.get("/v1/rekruttering/evalueringer",
                                         cookies=ck).json()["evalueringer"]
                        if e["oppdrag_id"] == oid)
            assert rad0["rapport_klar"] is False

            # Ankeret fødes (057-døren; claimet oppdrag er kravet).
            _sett_kontekst(rt, TENANT)
            pid = rt.execute(
                "SELECT opprett_rekrutteringsprosess(%s,%s,%s)",
                (TENANT, oid, 30)).fetchone()[0]
            rt.commit()

            # Positiv kontroll: med LEVENDE anker (åpen prosess, fristen
            # løper fra `opprettet` og ligger frem i tid) er rapporten
            # lesbar og listet som klar — en fraværstest uten den går
            # grønn på søppel.
            assert c.get(f"/v1/rekruttering/rapport/{oid}",
                         cookies=ck).status_code == 200
            rad = next(e for e in c.get("/v1/rekruttering/evalueringer",
                                        cookies=ck).json()["evalueringer"]
                       if e["oppdrag_id"] == oid)
            assert rad["rapport_klar"] is True

            # FRISTEN HÅNDHEVES AV LESEVEIEN SELV (Codex P1): lukket
            # forbi fristen — reaperen har IKKE kjørt, `slettet_ts` er
            # NULL — og rapporten er alt borte. En forsinket reaper
            # forlenger aldri tilgangen.
            _sett_kontekst(rt, TENANT)
            rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                       " now() - interval '31 days')", (TENANT, pid))
            rt.commit()
            assert c.get(f"/v1/rekruttering/rapport/{oid}",
                         cookies=ck).status_code == 404, \
                "utløpt frist skal stenge lesingen FØR reaperen rekker det"
            rad_frist = next(e for e in
                             c.get("/v1/rekruttering/evalueringer",
                                   cookies=ck).json()["evalueringer"]
                             if e["oppdrag_id"] == oid)
            assert rad_frist["rapport_klar"] is False
            assert rad_frist["slettet"] is True, \
                "fristen og reaperens merke er samme grense sett fra kunden"

            rp, _timer = _reaperkobling()
            try:
                rp.execute("SELECT * FROM reap_kandidatdata(50)")
                rp.commit()
            finally:
                rp.close()
            _sett_kontekst(migrator, TENANT)
            reapet = migrator.execute(
                "SELECT slettet_ts IS NOT NULL FROM rekrutteringsprosess"
                " WHERE tenant=%s AND prosess_id=%s",
                (TENANT, pid)).fetchone()[0]
            migrator.rollback()
            assert reapet, "positiv kontroll: prosessen skal være reapet"

            assert c.get(f"/v1/rekruttering/rapport/{oid}",
                         cookies=ck).status_code == 404, \
                "rapporten skal være borte etter retensjonsgrensen"
            rad2 = next(e for e in c.get("/v1/rekruttering/evalueringer",
                                         cookies=ck).json()["evalueringer"]
                        if e["oppdrag_id"] == oid)
            assert rad2["rapport_klar"] is False, \
                "listen skal slutte å reklamere for en reapet rapport"
            # … og reapingen er NAVNGITT (Codex P2): flaten skal aldri
            # vise et makulert oppdrag som «under arbeid».
            assert rad2["slettet"] is True
            assert rad["slettet"] is False, \
                "en levende evaluering skal ikke merkes slettet"
    finally:
        rt.close()


@pg
def test_claimen_foder_retensjonsankeret(migrator, miljo, inndata_rot):
    """CLAIM ⇒ LEVENDE RETENSJONSANKER (Cursor P2), målt der den fødes.

    Lesegrensen over er `EXISTS(ureapet prosess)`. Den er bare en TTL-port
    så lenge NOEN faktisk føder ankeret, og eneste produksjonsvei dit er
    claim-transaksjonen i `app.py`. Invarianten sto til nå bare implisitt
    i den tunge rapport-e2e-en (`_utfort_oppdrag` → 200): fjernes
    fødselsblokka, kan claimen fortsatt lykkes, controllertestene uten
    rapportlesing blir grønne, og bare e2e-en rødner — lett å miste i en
    smal fiksrunde. Porten her spør rett etter claimen, uten resten av
    kjeden.

    Bestillingen bærer ingen `slettefrist_dogn`, så det er DEFAULT-armen
    (basens 90) som måles — den claimen faktisk går gjennom.

    MUTASJONEN SOM DREPER DENNE: slett `opprett_rekrutteringsprosess`-
    blokka i `app.py`s claim-transaksjon."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    from .test_modul_onboarding_http import _onboard_token

    _rekr_policy(migrator)
    _sikre_m57_claimbar(migrator)
    rel = migrator.execute(
        "SELECT release_id FROM moduldeployment WHERE modul_id='m57_ats'"
        " AND livslop='claiming' LIMIT 1").fetchone()[0]
    migrator.rollback()
    a = lag_app(DSN)
    with TestClient(a) as c:
        cookie, csrf = _adminsesjon()
        ref = _bunt_via_http(c, cookie, csrf)
        profilref = _profil(migrator)
        r = _bestill(c, cookie, csrf, _evalkropp(ref, profilref),
                     "n-" + secrets.token_hex(8))
        assert r.status_code == 200, r.text
        oid = r.json()["oppdrag_id"]

        # Positiv kontroll: FØR claimen finnes ikke ankeret. Uten den
        # ville en test som bare teller rader gå grønn på en prosess
        # bestillingen tilfeldigvis hadde laget.
        assert _ankere(migrator, oid) == (0, 0), \
            "positiv kontroll: bestillingen alene føder ikke ankeret"

        mtk, _ = _onboard_token(c, migrator, "m57_ats", rel)
        rc = c.post("/v1/oppdrag/claim", json={},
                    headers={"authorization": f"Bearer {mtk}"})
        assert rc.status_code == 200, rc.text
        assert rc.json()["oppdrag_id"] == oid, rc.text

        assert _ankere(migrator, oid) == (1, 1), \
            "claimen skal etterlate nøyaktig ett LEVENDE retensjonsanker"


@pg
def test_ankersjekken_staar_etter_dekrypteringen(migrator, miljo):
    """TOCTOU-dommen (eierdom): re-sjekken `_anker_lever` måles i begge
    retninger mot basen, og PLASSERINGEN måles i kilden — den skal stå
    ETTER dekrypteringen og FØR 200, ellers er vinduet like bredt som
    før. Den interleavede toforbindelses-riggen bor i eget issue; dette
    er de deterministiske halvdelene.

    MUTASJONEN SOM DREPER DENNE: fjern re-sjekken, eller flytt den foran
    dekrypteringen."""
    import inspect

    from api import lesing
    from .test_m57_kandidatlagre import _prosess, _reaperkobling
    from .test_m57_utsending import _rt as _rekrutt_rt

    rt = _rekrutt_rt()
    try:
        oid, pid = _prosess(migrator, rt, frist=30)
        rt.commit()
        _sett_kontekst(migrator, TENANT)
        assert lesing._anker_lever(migrator, TENANT, oid) is True
        migrator.rollback()
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '31 days')", (TENANT, pid))
        rt.commit()
        rp, _timer = _reaperkobling()
        try:
            rp.execute("SELECT * FROM reap_kandidatdata(50)")
            rp.commit()
        finally:
            rp.close()
        _sett_kontekst(migrator, TENANT)
        assert lesing._anker_lever(migrator, TENANT, oid) is False
        migrator.rollback()
    finally:
        rt.close()

    kilde = inspect.getsource(lesing.rekrutteringsrapport_detalj)
    dekryptering = kilde.index("kryptering.dekrypter")
    sjekk = kilde.index("_anker_lever")
    svar = kilde.index("kanonisk_json")
    assert dekryptering < sjekk < svar, \
        "re-sjekken skal stå ETTER dekrypteringen og FØR 200-svaret"
    # ... og den måler et FERSKERE klokkeslett enn transaksjonsstarten:
    # `now()` er identisk med hovedspørringens og ville bestått på det
    # samme tidspunktet den alt besto med (Codex P2).
    hjelper = inspect.getsource(lesing._anker_lever)
    assert '"    AND clock_timestamp() <' in hjelper, \
        "re-sjekkens SQL skal måle clock_timestamp(), ikke now()"
    assert '" now()' not in hjelper and '"now()' not in hjelper and \
        "AND now()" not in hjelper, \
        "now() i re-sjekkens SQL er transaksjonsstart — poengløs re-sjekk"


@pg
def test_claimfoedselen_baerer_kundens_frist(migrator, miljo, inndata_rot,
                                             monkeypatch):
    """Claim-fødselen med EKSPLISITT `slettefrist_dogn` (pass-P2):
    kundens 31 skal ikke stille bli basens DEFAULT 90 — feltet er
    valgfritt i kontrakten, og nettopp da er den eneste beviste veien
    standardveien.

    MUTASJONEN SOM DREPER DENNE: slutt å lese `slettefrist_dogn` av det
    minimerte oppdraget i claim-fødselen (fall alltid til default)."""
    c, cookie, csrf, oid = _utfort_oppdrag(migrator, klient, monkeypatch,
                                           ekstra={"slettefrist_dogn": 31})
    try:
        _sett_kontekst(migrator, TENANT)
        rad = migrator.execute(
            "SELECT slettefrist_dogn, lukket_ts IS NOT NULL"
            "  FROM rekrutteringsprosess"
            " WHERE tenant=%s AND oppdrag_id=%s", (TENANT, oid)).fetchone()
        migrator.rollback()
        assert rad is not None, "claimen skal ha født ankeret"
        assert rad[0] == 31, \
            "kundens frist skal bæres fra det signerte oppdraget"
        assert rad[1], "terminal kvittering skal ha lukket ankeret"
    finally:
        c.__exit__(None, None, None)


def test_ankerlukkingen_hoerer_til_statusskiftet():
    """`sen_evidens` skal aldri lukke ankeret (pass-P2, regresjonsport):
    lukkingen bor ETTER det faktiske statusskiftet i `_ingest_kvittering`
    — kapabilitetsbruk-stedet (`brukt`/`sen_evidens`) kan ende i avvist
    promotering med jobben fortsatt plukket. Porten måles i KILDEN
    (samme valg som AST-portene i test_m37, og av samme grunn); den
    interleavede sen_evidens-riggen på et m57-oppdrag hører til #223-
    klassen.

    MUTASJONEN SOM DREPER DENNE: flytt lukkingen tilbake til
    kapabilitetsbruken, eller legg inn en lukking nr. 2 der."""
    import inspect

    from api import app as appmod

    kilde = inspect.getsource(appmod)
    kall = "SELECT lukk_rekrutteringsprosess("
    assert kilde.count(kall) == 1, \
        "nøyaktig ÉN lukking — en ekstra er en ny vei fristen kan starte på"
    posisjon = kilde.index(kall)
    terminal = kilde.index('"utfort" if vellykket else "feilet"')
    kapabilitet = kilde.index('in ("brukt", "sen_evidens")')
    assert posisjon > terminal > kapabilitet, \
        "lukkingen skal stå etter det faktiske statusskiftet, aldri ved" \
        " kapabilitetsbruken"
