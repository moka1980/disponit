"""PR-014c: skjemalageret, skjemavalideringen og målautorisasjonsregisteret.

Migrasjon 036 lukker CP5-hullet fra 014b: `skjema_hash` var en påstand
ingen kunne slå opp. Her prøves lageret (innholdsadressert, immutabelt for
alltid), den positive regelen i `registrer_artefakttype`, valideringen ved
OPPLASTING og ved PROMOTERING, sideeffektklassen `ekstern_lesing` og
`malautorisasjonsvilkar`-registeret.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
import hashlib
import json
import secrets

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT, app, klient,  # noqa: F401
                       dekker, migrator, miljo, token)          # noqa: F401
from .test_m37 import _sett_kontekst
from .test_pr014b_artefakt_api import (_kvitteringskap, _kvitteringskropp,
                                       _oppdrag_owner, _post, _utsted_cap)
from .test_pr014b_artefaktkapabilitet import _plukket_oppdrag_med_binding

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _jcs_hash(skjema: dict) -> tuple[str, str]:
    from policy_validator import jcs
    kanon = jcs.kanoniske_bytes(skjema)
    return kanon.decode("utf-8"), hashlib.sha256(kanon).hexdigest()


#: Et strengt skjema: nøyaktig ett felt, lukket.
STRENGT = {"type": "object", "additionalProperties": False,
           "required": ["resultat"],
           "properties": {"resultat": {"enum": ["ok", "feil"]}}}


def _mk_admin(rolle):
    """migrator SET ROLE <rolle>, committed (varig på tvers av rollback) —
    speiler hjelperen i test_pr014b_artefaktkapabilitet."""
    from db.pg import koble
    c = koble(MIGRATOR_DSN)
    c.execute(f"SET ROLE {rolle}")
    c.commit()
    return c


def _registrer_skjema(skjema: dict) -> str:
    kanon, h = _jcs_hash(skjema)
    c = _mk_admin("disponit_modules_admin")
    try:
        c.execute("SELECT registrer_artefaktskjema(%s,%s,'test')", (kanon, h))
        c.commit()
    finally:
        c.close()
    return h


def _streng_type(migrator_, modul, kh, *, skjema=None) -> str:
    """Registrer en artefakttype bundet til det strenge skjemaet, under en
    kontrakt som alt finnes (fixturen fra 014b lager kontrakten)."""
    h = _registrer_skjema(skjema or STRENGT)
    at = f"kontroll.t{secrets.token_hex(4)}.rapport"
    da = _mk_admin("disponit_domains_admin")
    try:
        da.execute("SELECT registrer_artefakttype(%s,%s,1,%s,%s,'test')",
                   (at, modul, kh, h))
        da.commit()
    finally:
        da.close()
    return at


# --------------------------------------------------------------------------
# Lageret (portene 15–17, 26–28)
# --------------------------------------------------------------------------

@pg
def test_skjemaregistrering_rekalkulerer_hashen(migrator):
    """Port 16: oppgitt hash må matche innholdet — funksjonen regner selv."""
    kanon, h = _jcs_hash({"type": "object", "x": secrets.token_hex(3)})
    c = _mk_admin("disponit_modules_admin")
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute("SELECT registrer_artefaktskjema(%s,%s,'test')",
                      (kanon, "0" * 64))
        c.rollback()
        assert c.execute("SELECT registrer_artefaktskjema(%s,%s,'test')",
                         (kanon, h)).fetchone()[0] == h
        c.execute("SELECT registrer_artefaktskjema(%s,%s,'test')",
                  (kanon, h))                        # idempotent
        c.commit()
    finally:
        c.close()


@pg
def test_skjemalageret_er_immutabelt_for_alltid(migrator):
    """Portene 17, 26–28: UPDATE og DELETE avvises ALLTID — også for en rad
    ingen artefakttype refererer, og også for migrator (tabelleieren).
    Kontroll: fjern `artefaktskjema_immutable`-triggeren i 036, så blir
    denne rød."""
    kanon, h = _jcs_hash({"type": "object", "u": secrets.token_hex(3)})
    _registrer_skjema(json.loads(kanon))
    for sql in [
        "UPDATE artefaktskjema SET skjema='{}'::jsonb WHERE skjema_hash=%s",
        "UPDATE artefaktskjema SET skjema_hash=%s WHERE skjema_hash=%s",
        "DELETE FROM artefaktskjema WHERE skjema_hash=%s",
    ]:
        params = ((h,) if sql.count("%s") == 1
                  else ("f" * 64, h))
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(sql, params)
        migrator.rollback()


@pg
def test_artefakttype_krever_registrert_skjema(migrator):
    """Port 15 (registersiden): positiv regel, fail-closed. Kontroll: fjern
    eksistenssjekken i 036-kroppen, så blir denne rød."""
    modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _plukket_oppdrag_med_binding(migrator, modul, kh)   # lager kontrakten
    da = _mk_admin("disponit_domains_admin")
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue,
                           match="finnes ikke"):
            da.execute("SELECT registrer_artefakttype(%s,%s,1,%s,%s,'test')",
                       (f"kontroll.x{secrets.token_hex(3)}.rapport", modul,
                        kh, "e" * 64))
        da.rollback()
    finally:
        da.close()


# --------------------------------------------------------------------------
# Valideringen ved OPPLASTING (portene 13, 15) og PROMOTERING (14)
# --------------------------------------------------------------------------

@pg
@dekker("artefakt_skjemabrudd", "artefaktskjema_mangler")
def test_opplasting_valideres_mot_typens_skjema(migrator, klient, token):
    """Port 13: brudd avvises FØR kryptering — ingen staged rad; gyldig
    innhold går gjennom. Port 15 (opplastingssiden): en type med hash uten
    skjemarad (grandfathered via direkte INSERT) avvises som
    konfigurasjonsfeil. Kontroll: fjern valideringsblokken i
    `_artefakt_upload`, så blir denne rød."""
    modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    opp, _ = _plukket_oppdrag_med_binding(migrator, modul, kh)
    at = _streng_type(migrator, modul, kh)
    tok, _ = token(rolle=modul, scopes=("artifacts:upload",))

    jti = _utsted_cap(opp, modul, kh, at)
    r = _post(klient, tok, jti, {"resultat": "ok", "smugling": 1})
    assert (r.status_code, r.json()["feil"]) == (422, "artefakt_skjemabrudd"), \
        r.text
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute("SELECT count(*) FROM artefakt WHERE"
                         " kapabilitet_jti=%s", (jti,)).fetchone()[0]
    migrator.rollback()
    assert n == 0, "et skjemabrudd etterlot en staged rad"

    # Kapabiliteten er engangs og BLE innløst av forsøket — riktig: den var
    # gyldig, innholdet var det ikke. Ny kapabilitet for det gyldige.
    jti2 = _utsted_cap(opp, modul, kh, at)
    r2 = _post(klient, tok, jti2, {"resultat": "ok"})
    assert r2.status_code == 200, r2.text

    # Grandfathered type: hash uten skjemarad → 422, driftskoden.
    at3 = f"kontroll.g{secrets.token_hex(3)}.rapport"
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "INSERT INTO artefakttype_register (artefakttype, eiermodul,"
        " kontraktversjon, kontrakt_hash, skjema_hash)"
        " VALUES (%s,%s,1,%s,%s)", (at3, modul, kh, "d" * 64))
    migrator.commit()
    jti3 = _utsted_cap(opp, modul, kh, at3)
    r3 = _post(klient, tok, jti3, {"resultat": "ok"})
    assert (r3.status_code,
            r3.json()["feil"]) == (422, "artefaktskjema_mangler"), r3.text


@pg
def test_promotering_revaliderer_og_karantenesetter_brudd(migrator, klient,
                                                          token):
    """Port 14: innhold som omgikk opplastingsvalideringen (direkte insert —
    «en fremtidig opplastingsvei glemte punkt 1») promoteres ALDRI:
    kvitteringen får 409, artefaktet karantenesettes, oppdraget avsluttes
    ikke. Kontroll: fjern revalideringsblokken i kvittering-ingesten, så
    blir denne rød."""
    from db import kryptering
    from .test_m37 import _signer_kvittering
    modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    opp, _ = _plukket_oppdrag_med_binding(migrator, modul, kh)
    at = _streng_type(migrator, modul, kh)
    oc, rep, gen = _oppdrag_owner(migrator, opp)

    # Direkte staged insert med SKJEMABRYTENDE innhold (feltene matcher
    # `_artefakt`-hjelperen i domene_artefakt, men innholdet er vårt).
    from policy_validator import jcs
    innhold = {"rapport": "smuglet forbi opplastingen"}
    kanon = jcs.kanoniske_bytes(innhold)
    kts = hashlib.sha256(kanon).hexdigest()
    _sett_kontekst(migrator, TENANT)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator, TENANT)
    ct, nonce = kryptering.krypter(dek, innhold, TENANT, key_id)
    aid = migrator.execute(
        "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype, modul_id,"
        " release_id, kontraktversjon, kontrakt_hash, module_epoch, tilstand,"
        " storrelse_bytes, klartekst_sha256, ciphertext, nonce, dek_ref,"
        " kapabilitet_jti)"
        " VALUES (%s,%s,%s,%s,'r1',1,%s,0,'staged',%s,%s,%s,%s,%s,%s)"
        " RETURNING artefakt_id",
        (TENANT, opp, at, modul, kh, len(kanon), kts, ct, nonce, key_id,
         "jti-" + secrets.token_hex(8))).fetchone()[0]
    migrator.commit()

    kjti = _kvitteringskap(opp, oc, gen)
    kv = _signer_kvittering(
        _kvitteringskropp(opp, kjti, rep, oc, gen, str(aid), kts))
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    rk = klient.post("/v1/oppdrag/kvittering", json=kv,
                     headers={"authorization": f"Bearer {tok2}"})
    assert rk.status_code == 409, rk.text
    _sett_kontekst(migrator, TENANT)
    art_st = migrator.execute("SELECT tilstand FROM artefakt WHERE"
                              " artefakt_id=%s", (aid,)).fetchone()[0]
    opp_st = migrator.execute("SELECT status FROM oppdrag WHERE tenant=%s"
                              " AND id=%s", (TENANT, opp)).fetchone()[0]
    migrator.rollback()
    assert (art_st, opp_st) == ("karantene", "plukket"), (art_st, opp_st)


# --------------------------------------------------------------------------
# Sideeffektklassen (portene 29–30) og målautorisasjonsregisteret
# --------------------------------------------------------------------------

@pg
def test_sideeffektklassen_ekstern_lesing(migrator):
    """Port 29–30: `ekstern_lesing` godtas, ukjent verdi avvises, og en
    eksisterende kontrakt kan ikke omklassifiseres (modulkontrakt tåler
    ingen UPDATE — 014a-invarianten bærer kravet)."""
    modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    migrator.execute("INSERT INTO modulhode (modul_id) VALUES (%s)", (modul,))
    migrator.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,1,%s,'p','k','ekstern_lesing','direkte')",
        (modul, kh))
    migrator.commit()
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "INSERT INTO modulkontrakt (modul_id,kontraktversjon,"
            "kontrakt_hash,payload_schema_hash,kvittering_schema_hash,"
            "sideeffektklasse,reversibilitet)"
            " VALUES (%s,2,%s,'p','k','fri_flyt','direkte')",
            (modul, "k2-" + secrets.token_hex(4)))
    migrator.rollback()
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE modulkontrakt SET"
                         " sideeffektklasse='krever_outbox'"
                         " WHERE modul_id=%s", (modul,))
    migrator.rollback()


@pg
def test_malautorisasjonsvilkar_er_lukket_og_immutabelt(migrator):
    """§3/§6: seedet vilkår finnes; nye går via herdet funksjon (idempotent,
    aldri omregistrering til annet domene); ukjent maldomene avvises av
    CHECK. Tom liste er default — bare rader teller."""
    rad = migrator.execute(
        "SELECT maldomene FROM malautorisasjonsvilkar"
        " WHERE vilkar_type='domenekontroll_verifisert'").fetchone()
    migrator.rollback()
    assert rad == ("web_hostname",)
    c = _mk_admin("disponit_modules_admin")
    try:
        vt = "vilkar_" + secrets.token_hex(3)
        c.execute("SELECT registrer_malautorisasjonsvilkar(%s,"
                  "'web_hostname','test')", (vt,))
        c.execute("SELECT registrer_malautorisasjonsvilkar(%s,"
                  "'web_hostname','test')", (vt,))          # idempotent
        c.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("SELECT registrer_malautorisasjonsvilkar(%s,"
                      "'dns_zone','test')", ("v2_" + secrets.token_hex(3),))
        c.rollback()
    finally:
        c.close()


# --------------------------------------------------------------------------
# Aktiveringsporten (§6, portene 31, 34–37)
# --------------------------------------------------------------------------

def _ekstern_lesing_modul(migrator_):
    modul = "m-" + secrets.token_hex(4)
    migrator_.execute("INSERT INTO modulhode (modul_id) VALUES (%s)", (modul,))
    migrator_.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,1,%s,'p','k','ekstern_lesing','direkte')",
        (modul, "k-" + secrets.token_hex(8)))
    migrator_.commit()
    return modul


def _handling(modul, *, frekvens=True, vilkaar=("domenekontroll_verifisert",),
              hid="kontroll.wcag.nettsted"):
    h = {"id": hid, "modul": modul, "modus": "auto",
         "ved_brudd": "unntakskø",
         "vilkaar": [{"navn": v, "verifikator": "v1"} for v in vilkaar],
         "reversering": {"type": "direkte"}}
    if frekvens:
        h["grenser"] = {"frekvens": {"maks": 4, "periode_antall": 1,
                                     "periode_enhet": "dager"}}
    return h


@pg
def test_aktiveringsporten_for_ekstern_lesing(migrator):
    """Portene 31, 34–37 på funksjonsnivå (kallstedene prøves i
    integrasjonstesten under). Kontroll: fjern frekvens- eller
    vilkårsgrenen i `_krev_ekstern_lesing_port`, så blir hver sin gren rød."""
    from api import policyadmin
    from db.pg import koble
    modul = _ekstern_lesing_modul(migrator)
    c = _mk_admin("disponit_modules_admin")
    try:
        c.execute("SELECT registrer_malautorisasjonsvilkar("
                  "'gyldig_men_ikke_mal','web_hostname','test')")
        c.commit()
    finally:
        c.close()
    rt = koble(DSN)
    try:
        def port(h):
            policyadmin._krev_ekstern_lesing_port(rt, {"handlinger": [h]})

        # 31: uten frekvensgrense → avvist under låsen.
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            port(_handling(modul, frekvens=False))
        assert e.value.kode == "ekstern_lesing_uten_frekvens"
        # 34: gyldig, men IKKE målautoriserende vilkår → avvist. Vilkåret
        # `forfall_passert_dager` finnes i policyer — det har bare ingen rad.
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            port(_handling(modul, vilkaar=("forfall_passert_dager",)))
        assert e.value.kode == "malautorisasjon_mangler"
        # 36: navnelikhet teller aldri — bare rader.
        with pytest.raises(policyadmin.Aktiveringsfeil):
            port(_handling(modul, vilkaar=("domenekontroll_verifisert2",)))
        # 37: rad finnes, men for FEIL måldomene? (Alle rader er
        # web_hostname i v1 — probes med et vilkår registrert riktig, mot en
        # handling hvis TYPE mangler målautorisasjonsbegrep.)
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            port(_handling(modul, hid="kontroll.wcag_ukjent.ting"))
        assert "oppdragstype" in (e.value.detalj or "")
        # 35: positiv motsats — domenekontroll_verifisert + frekvens godtas.
        port(_handling(modul))
        # ... og en handling mot en modul UTEN ekstern_lesing er urørt.
        policyadmin._krev_ekstern_lesing_port(
            rt, {"handlinger": [_handling("m-finnes-ikke", frekvens=False,
                                          vilkaar=())]})
        rt.rollback()
    finally:
        rt.close()


@pg
def test_aktiveringsporten_haandheves_ved_rundeaapning(migrator):
    """Integrasjonen: kallstedet i `opprett_aktiveringsrunde` (samme mønster
    som `_krev_innforingskrav`). Kontroll: fjern
    `_krev_ekstern_lesing_port`-kallet der, så blir denne rød."""
    from .test_pr013_policyadmin_flyt import TEN, _apne, _medlem, _utkast
    from api import policyadmin
    modul = _ekstern_lesing_modul(migrator)
    forf = _medlem("wcagforf-" + secrets.token_hex(2), ["policyforvalter"])
    pid = "pol-" + secrets.token_hex(3)
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, forf, {"roller": [{"id": "r1"}],
                             "handlinger": [_handling(modul, vilkaar=())]})
    from db.pg import koble
    rt = koble(DSN)
    try:
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _apne(rt, uid, forf)
        assert e.value.kode == "malautorisasjon_mangler", e.value.kode
        rt.rollback()
    finally:
        rt.close()
    # ... og med vilkåret på plass åpner runden.
    uid2 = "utk-" + secrets.token_hex(3)
    _utkast(uid2, pid, forf, {"roller": [{"id": "r1"}],
                              "handlinger": [_handling(modul)]})
    rt = koble(DSN)
    try:
        r = _apne(rt, uid2, forf)
        assert r["diff_hash"]
    finally:
        rt.close()


# --------------------------------------------------------------------------
# Deploy-portene (§5, portene 6 og 32)
# --------------------------------------------------------------------------

@pg
def test_deployportene_register_mot_kodefestet_type(migrator, monkeypatch):
    """Port 6: registerrad uten kodefestet type → rød. Port 32:
    ekstern_lesing-kontrakt med type uten krever_malautorisasjon → rød.
    Grønn tilstand er den positive motsatsen. Kontroll: fjern LEFT JOIN-en
    (port 32-grenen) i `kontroller()`, så blir andre halvdel grønn på
    feil grunnlag."""
    import importlib.util
    from pathlib import Path
    sti = (Path(__file__).resolve().parents[3]
           / "deploy/staging/deployport-modultyper.py")
    spec = importlib.util.spec_from_file_location("deployport_test", sti)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import oppdragskontrakt as ok

    modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    migrator.execute("INSERT INTO modulhode (modul_id) VALUES (%s)", (modul,))
    migrator.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,1,%s,'p','k','ekstern_lesing','direkte')",
        (modul, kh))
    ukjent = f"deployport{secrets.token_hex(3)}"
    migrator.execute(
        "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
        "kontraktversjon,kontrakt_hash) VALUES (%s,%s,1,%s)",
        (ukjent, modul, kh))
    migrator.commit()

    # Port 6: raden er ukjent for koden → rød med typenavnet i meldingen.
    feil = mod.kontroller(migrator)
    migrator.rollback()
    assert any(ukjent in f for f in feil), feil

    # Port 32: kodefest typen, men UTEN målautorisasjonsflagget → fortsatt
    # rød, nå på autorisasjonsbegrepet.
    t = ok.Oppdragstype(navn=ukjent, handlingsprefikser=(f"{ukjent}.",),
                        felter=frozenset({"mal_url"}), paakrevde=frozenset(),
                        eiermodul=modul)
    monkeypatch.setitem(ok.OPPDRAGSTYPER, ukjent, t)
    feil = mod.kontroller(migrator)
    migrator.rollback()
    assert any("krever_malautorisasjon" in f and ukjent in f for f in feil), \
        feil

    # Grønn motsats: flagg + domene på plass → ingen feil for VÅR rad.
    t2 = ok.Oppdragstype(navn=ukjent, handlingsprefikser=(f"{ukjent}.",),
                         felter=frozenset({"mal_url"}), paakrevde=frozenset(),
                         eiermodul=modul, krever_malautorisasjon=True,
                         malautorisasjonsdomene="web_hostname")
    monkeypatch.setitem(ok.OPPDRAGSTYPER, ukjent, t2)
    feil = mod.kontroller(migrator)
    migrator.rollback()
    assert not any(ukjent in f for f in feil), feil


# --------------------------------------------------------------------------
# Rapportbygging og sanitering (portene 8–12) — modulen selv.
# --------------------------------------------------------------------------

def _kontekst():
    return {"axe_versjon": "4.10.0", "chromium_versjon": "127.0",
            "container_image_digest": "sha256:" + "a" * 64,
            "viewport": "1280x800", "locale": "nb-NO",
            "timezone": "Europe/Oslo"}


def _motorresultat(**over):
    from modules.wcag_audit.motor import Motorresultat
    basis = dict(
        regelsett_versjon="axe-4.10", varighet_ms=1234,
        sider=({"url": "https://kunde.example/side?sporing=1#topp",
                "status": "ok"},),
        funn=({"regel_id": "color-contrast", "alvorlighet": "alvorlig",
               "antall": 3, "eksempler": ["#a", "x" * 500]},),
        blokkert=({"vert": "fonts.example", "antall": 2, "art": "font"},),
        avkortet=(False, None, None))
    basis.update(over)
    return Motorresultat(**basis)


def test_rapporten_saneres_og_validerer():
    """Portene 11–12 + skjemarunden: URL uten query/fragment, selektor
    kappet til 200 tegn, maks 10 eksempler, miljø fra SERVERKONTEKSTEN —
    og resultatet validerer mot det innholdsadresserte skjemaet."""
    import jsonschema
    from modules.wcag_audit import rapportskjema
    from modules.wcag_audit.rapport import bygg
    r = bygg(_motorresultat(),
             payload={"kravsett": "wcag21_aa", "mal_url": "https://k.no/",
                      "omfang": "enkeltside"},
             kontekst=_kontekst())
    jsonschema.Draft202012Validator(rapportskjema.SKJEMA).validate(r)
    assert r["sider_kontrollert"][0]["url"] == "https://kunde.example/side"
    assert len(r["funn"][0]["eksempler"][1]) == 200
    assert r["miljo"]["container_image_digest"].startswith("sha256:")
    assert r["manuelle_kriterier_vurdert"] is False
    assert r["dekningsbegrensninger"][0] == {"vert": "fonts.example",
                                             "antall": 2, "art": "font"}


def test_rapporten_kutter_aerlig_over_500_funn():
    """Port 11: over 500 funn kappes — og `avkortet` SIER det (aldri mer
    fullstendighet enn innholdet bærer). Kontroll: fjern
    truffet-oppdateringen i `bygg`, så blir denne rød."""
    from modules.wcag_audit.rapport import bygg
    mange = tuple({"regel_id": f"r{i}", "alvorlighet": "lav", "antall": 1,
                   "eksempler": []} for i in range(600))
    r = bygg(_motorresultat(funn=mange),
             payload={"kravsett": "wcag21_aa"}, kontekst=_kontekst())
    assert len(r["funn"]) == 500
    assert r["avkortet"]["truffet"] is True and r["avkortet"]["verdi"] == 600
    # ... men SAMMENDRAGET teller alt motoren fant — kappingen gjelder
    # eksempellisten, ikke sannheten om omfanget.
    assert r["sammendrag"]["lav"] == 600


def test_motorutdata_er_ubetrodd():
    """Port 12/§2: ikke-https-URL og uleselige poster er Motorfeil — aldri
    en rapport. Digester fra motoren finnes ikke som begrep: miljøblokka
    tar KUN serverkontekstens nøkler (port 10)."""
    from modules.wcag_audit.motor import Motorfeil
    from modules.wcag_audit.rapport import bygg
    with pytest.raises(Motorfeil):
        bygg(_motorresultat(sider=({"url": "http://klartekst.example/",
                                    "status": "ok"},)),
             payload={"kravsett": "wcag21_aa"}, kontekst=_kontekst())
    with pytest.raises(Motorfeil):
        bygg(_motorresultat(sider=()), payload={"kravsett": "wcag21_aa"},
             kontekst=_kontekst())
    with pytest.raises(KeyError):
        # En kontekst uten digest er en konfigurasjonsfeil hos OSS —
        # den skal smelle, ikke fylles fra motorens påstander.
        bygg(_motorresultat(), payload={"kravsett": "wcag21_aa"},
             kontekst={k: v for k, v in _kontekst().items()
                       if k != "container_image_digest"})
    # Codex P1: et uleselig ANTALL er også ubetrodd inndata. Konverteringen
    # ga ValueError, som controlleren ikke fanger — da hadde unntaket
    # sluppet ut av kjøreløkka og latt oppdraget stå claimet i stedet for
    # å bli kvittert som feilet. Begge tellingene, funn og blokkert.
    for over in ({"funn": ({"regel_id": "r", "alvorlighet": "lav",
                            "antall": "ukjent", "eksempler": []},)},
                 {"blokkert": ({"vert": "f.example", "antall": {"a": 1},
                                "art": "font"},)}):
        with pytest.raises(Motorfeil):
            bygg(_motorresultat(**over), payload={"kravsett": "wcag21_aa"},
                 kontekst=_kontekst())


# --------------------------------------------------------------------------
# Controlleren ende-til-ende med FakeMotor (port 23 + 25s CI-halvdel:
# kjeden bevarer tellingene; motor-ekte fasit måles på staging).
# --------------------------------------------------------------------------

class FakeMotor:
    def __init__(self, resultat=None, feil=None):
        self.resultat, self.feil = resultat, feil
        self.payloads = []

    def kjor(self, payload):
        from modules.wcag_audit.motor import Motorfeil
        self.payloads.append(payload)
        if self.feil:
            raise Motorfeil(self.feil)
        return self.resultat


def _wcag_kjede(migrator_, monkeypatch):
    """Modulkjede + oppdrag for et ALIAS av wcag-typen (unike navn per
    kjøring — den delte testbasen tåler ikke det globale navnet; den EKTE
    registreringen gjøres av deploy-skriptet og prøves på staging)."""
    import oppdragskontrakt as ok
    from modules.wcag_audit import rapportskjema
    u = secrets.token_hex(4)
    typenavn = f"kontroll.w{u}.nettsted"
    at = f"kontroll.w{u}.rapport"
    ekte = ok.OPPDRAGSTYPER["kontroll.wcag.nettsted"]
    monkeypatch.setitem(ok.OPPDRAGSTYPER, typenavn, ok.Oppdragstype(
        navn=typenavn, handlingsprefikser=(f"kontroll.w{u}.",),
        felter=ekte.felter, paakrevde=ekte.paakrevde,
        eiermodul=f"m-{u}", krever_malautorisasjon=True,
        malautorisasjonsdomene="web_hostname"))

    modul, rel = f"m-{u}", f"r-{u}"
    kh = "k-" + secrets.token_hex(8)
    migrator_.execute("INSERT INTO modulhode (modul_id,status) VALUES"
                      " (%s,'aktiv')", (modul,))
    migrator_.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,1,%s,'p','k','ekstern_lesing','direkte')",
        (modul, kh))
    migrator_.execute(
        "INSERT INTO modulrelease (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,manifest_hash,artifact_digest)"
        " VALUES (%s,%s,1,%s,'mh','ad')", (modul, rel, kh))
    migrator_.execute(
        "INSERT INTO moduldeployment (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,miljo,livslop) VALUES (%s,%s,1,%s,'staging',"
        "'claiming')", (modul, rel, kh))
    migrator_.execute(
        "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
        "kontraktversjon,kontrakt_hash) VALUES (%s,%s,1,%s)",
        (typenavn, modul, kh))
    migrator_.execute(
        "INSERT INTO artefaktskjema (skjema_hash, skjema) VALUES (%s,%s)"
        " ON CONFLICT (skjema_hash) DO NOTHING",
        (rapportskjema.skjema_hash(),
         rapportskjema.kanonisk().decode("utf-8")))
    migrator_.execute(
        "INSERT INTO artefakttype_register (artefakttype,eiermodul,"
        "kontraktversjon,kontrakt_hash,skjema_hash) VALUES (%s,%s,1,%s,%s)",
        (at, modul, kh, rapportskjema.skjema_hash()))
    migrator_.commit()

    # Oppdraget: M-37-forankret (outboxens NOT NULL-trio), payload = den
    # LUKKEDE fire-felts-formen + ressurs_id (som minimeres bort — port 5).
    from db import kryptering
    from .test_m37 import _lag_sak
    sak, logg = _lag_sak(migrator_, TENANT)
    rid = secrets.token_hex(32)
    handling = f"kontroll.w{u}.nettsted"
    _sett_kontekst(migrator_, TENANT)
    migrator_.execute(
        "INSERT INTO reparasjonsoperasjoner (tenant, unntak_id,"
        " repair_operation_id, repair_generation, handler_id,"
        " handler_versjon, maalhandling, input_hash, kategori)"
        " VALUES (%s,%s,%s,0,'wcag','1',%s,%s,'manglende_data')",
        (TENANT, sak, rid, handling, secrets.token_hex(32)))
    beslutning = migrator_.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'test','arbeidskapabilitet','ih2','p@1.0.0/x.y',"
        " 'TILLAT','[]',%s) RETURNING id", (TENANT, rid)).fetchone()[0]
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator_, TENANT)
    payload = {"mal_url": "https://kunde.example/", "kravsett": "wcag21_aa",
               "omfang": "enkeltside", "maks_sider": 1,
               "ressurs_id": "hemmelig-ref"}
    ct, nonce = kryptering.krypter(dek, payload, TENANT, key_id)
    opp = migrator_.execute(
        "INSERT INTO oppdrag (tenant, unntak_id, loggpost_id,"
        " repair_operation_id, oppdragstype, handling, eiermodul,"
        " payload_kryptert, key_id, nonce, utforelsesfrist, evidensfrist,"
        " beslutning_loggpost_id, koblingsstatus)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        " now()+interval '30 minutes', now()+interval '30 minutes',"
        " %s,'KOBLET') RETURNING id",
        (TENANT, sak, logg, rid, typenavn, handling, modul, ct, key_id,
         nonce, beslutning)).fetchone()[0]
    migrator_.commit()
    return modul, rel, int(opp)


@pg
def test_controlleren_hele_veien_med_fakemotor(migrator, miljo, monkeypatch):
    """Hele kjeden gjennom EKTE plattform (onboarding → claim m/ token →
    minimert payload (port 5) → rapportbygging → skjemavalidert opplasting →
    signert kvittering → PROMOTERT artefakt). FakeMotor bærer fasiten:
    tellingene inn == tellingene i det promoterte artefaktet."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    from db import kryptering
    from modules.wcag_audit import controller
    from .test_m37 import _signer_kvittering
    from .test_modul_onboarding_http import _onboard_token

    modul, rel, opp = _wcag_kjede(migrator, monkeypatch)
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, modul, rel)
            motor = FakeMotor(resultat=_motorresultat())
            res = controller.kjor_en(c, mtk, motor, _kontekst(),
                                     _signer_kvittering)
            assert res["utfall"] == "utfort", res
            assert res["kvittering_status"] == 200, res
            # Port 5: modulen så KUN de fire payloadfeltene.
            assert set(motor.payloads[0]) == {"mal_url", "kravsett",
                                              "omfang", "maks_sider"}
            _sett_kontekst(migrator, TENANT)
            tilstand, ct, nonce, ref = migrator.execute(
                "SELECT tilstand, ciphertext, nonce, dek_ref FROM artefakt"
                " WHERE artefakt_id=%s", (res["artefakt_id"],)).fetchone()
            assert tilstand == "promotert", tilstand
            dek = kryptering.hent_dek(migrator, TENANT, ref)
            rapport = kryptering.dekrypter(dek, bytes(ct), bytes(nonce),
                                           TENANT, ref)
            migrator.rollback()
            # Fasiten: tellingen fra motoren står ordrett i evidensen.
            assert rapport["sammendrag"]["alvorlig"] == 3
            assert rapport["sider_kontrollert"][0]["url"] \
                == "https://kunde.example/side"
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_motorfeil_gir_avbrutt_uten_artefakt(migrator, miljo, monkeypatch):
    """§10 siste rad: skjemabrudd/motorfeil → oppdraget feiler, INGEN delvis
    artefakt — og plattformen får en kvittering som sier det."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    from modules.wcag_audit import controller
    from .test_m37 import _signer_kvittering
    from .test_modul_onboarding_http import _onboard_token

    modul, rel, opp = _wcag_kjede(migrator, monkeypatch)
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, modul, rel)
            motor = FakeMotor(feil="chromium krasjet")
            res = controller.kjor_en(c, mtk, motor, _kontekst(),
                                     _signer_kvittering)
            assert res["utfall"] == "avbrutt", res
            _sett_kontekst(migrator, TENANT)
            n = migrator.execute(
                "SELECT count(*) FROM artefakt WHERE tenant=%s AND"
                " oppdrag_id=%s", (TENANT, opp)).fetchone()[0]
            migrator.rollback()
            assert n == 0, "motorfeil etterlot et delvis artefakt"
    finally:
        a.tjeneste.pool.lukk()


# --------------------------------------------------------------------------
# Kvitteringssvaret (Codex P1) — ingen Postgres: kjeden mot en stubklient.
# --------------------------------------------------------------------------

class _Svar:
    def __init__(self, status, kropp=None):
        self.status_code, self._kropp = status, kropp or {}

    def json(self):
        return self._kropp

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"uventet {self.status_code}")


class _Stubklient:
    """Claim → opplasting → kvittering, med valgbar kvitteringsstatus."""

    def __init__(self, kvitteringsstatus):
        self.kvitteringsstatus = kvitteringsstatus

    def post(self, sti, json=None, headers=None):
        if sti == "/v1/oppdrag/claim":
            return _Svar(200, {
                "oppdrag_id": 1, "tenant": TENANT, "kvittering_jti": "j",
                "repair_operation_id": "r", "owner_claim_id": "o",
                "owner_generation": 0,
                "payload": {"mal_url": "https://kunde.example/",
                            "kravsett": "wcag21_aa", "omfang": "enkeltside"},
                "opplasting": {"jti": "kap"}})
        if sti == "/v1/artefakt":
            return _Svar(200, {"artefakt_id": "a-1",
                               "klartekst_sha256": "b" * 64})
        assert sti == "/v1/oppdrag/kvittering", sti
        return _Svar(self.kvitteringsstatus, {})


def test_avvist_kvittering_er_ikke_utfort():
    """Codex P1: 409 fra kvitteringsendepunktet (fencing, hashavvik,
    avvist promotering) eller 5xx betyr at oppdraget står IGJEN uferdig hos
    plattformen. Meldte controlleren `utfort` uansett, ville en planlegger
    tro at kjøringen var i havn — modulens ord mot plattformens tilstand.
    Kontroll: fjern _kvittert-sjekken i controlleren, så blir denne rød."""
    from modules.wcag_audit import controller
    motor = FakeMotor(resultat=_motorresultat())
    for status in (409, 500):
        res = controller.kjor_en(_Stubklient(status), "tk", motor,
                                 _kontekst(), lambda k: k)
        assert res["utfall"] == "ukvittert", res
        assert res["kvittering_status"] == status
        # Artefaktet ER lastet opp — utfallet skjuler ikke det, det nekter
        # bare å kalle kjøringen ferdig.
        assert res["artefakt_id"] == "a-1"
    ok = controller.kjor_en(_Stubklient(200), "tk", motor, _kontekst(),
                            lambda k: k)
    assert ok["utfall"] == "utfort", ok
