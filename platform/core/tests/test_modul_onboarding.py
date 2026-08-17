"""Modul-onboarding (migrasjon 035) — lagringskontrakten og de herdede
funksjonene.

Klarsignalets bærende skille: TOKENET AUTENTISERER, REGISTERET AUTORISERER.
Her prøves fase 1+2 (hemmelighet → token), rotasjonen, familiehorisonten og
— viktigst — at LAGRINGEN håndhever kontrakten for alle roller, inkludert
funksjonseierne (portene 35–42: `familiefrist.flyttet_framover_via_noen_
skrivevei = 0`). HTTP-veiene (innløsningsendepunkt, claim, kapabilitet i
claim-svar) prøves i `test_modul_onboarding_http.py`.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _c():
    from db.pg import koble
    return koble(MIGRATOR_DSN)


def _rt():
    from db.pg import koble
    return koble(DSN)


def _mid():
    return "m-" + secrets.token_hex(4)


def _hex64():
    return secrets.token_hex(32)


def _deployment_med_typer(c, *, status="aktiv", livslop="claiming",
                          miljo="staging", typer=1):
    """Full kjede modulhode→kontrakt→release→deployment (+ registrerte
    oppdragstyper under releasens kontrakt). -> (modul, rel, ver, khash)."""
    modul, rel, ver = _mid(), "r-" + secrets.token_hex(3), 1
    khash = "k-" + secrets.token_hex(8)
    c.execute("INSERT INTO modulhode (modul_id,status) VALUES (%s,%s)",
              (modul, status))
    c.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,%s,%s,'p','k','krever_outbox',"
        "'kompenserende')", (modul, ver, khash))
    c.execute(
        "INSERT INTO modulrelease (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,manifest_hash,artifact_digest) VALUES"
        " (%s,%s,%s,%s,'mh','ad')", (modul, rel, ver, khash))
    c.execute(
        "INSERT INTO moduldeployment (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,miljo,livslop) VALUES (%s,%s,%s,%s,%s,%s)",
        (modul, rel, ver, khash, miljo, livslop))
    for i in range(typer):
        c.execute(
            "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
            "kontraktversjon,kontrakt_hash) VALUES (%s,%s,%s,%s)",
            (f"t{secrets.token_hex(4)}.{i}", modul, ver, khash))
    c.commit()
    return modul, rel, ver, khash


def _utsted(rt, modul, miljo, rel, *, dager=365, ttl=60, oid=None,
            hemmelighet_hash=None):
    oid = oid or uuid.uuid4()
    return oid, rt.execute(
        "SELECT * FROM utsted_onboarding_hemmelighet(%s,%s,%s,%s,%s,%s,%s,"
        "'test')", (modul, miljo, rel, oid,
                    hemmelighet_hash or _hex64(), dager, ttl)).fetchone()


def _innlos(rt, oid, hemmelighet_hash, *, dager=30, tid=None, mac=None):
    tid = tid or uuid.uuid4()
    rad = rt.execute(
        "SELECT * FROM innlos_onboarding(%s,%s,%s,%s,%s,'test')",
        (oid, hemmelighet_hash, tid, mac or _hex64(), dager)).fetchone()
    return tid, rad


# --------------------------------------------------------------------------
# Fase 1: utstedelse (portene 1–3 på DB-nivå; scope-porten prøves i HTTP)
# --------------------------------------------------------------------------

@pg
def test_utstedelse_krever_claiming_aktiv_og_registrert_type():
    """Port 2: maskinverifisert deploymentevidens — ikke bare scope. Et token
    uten claimbart arbeid er en hemmelighet på avveie som venter."""
    m = _c()
    rt = _rt()
    try:
        # draining deployment → avvist
        modul, rel, _, _ = _deployment_med_typer(m, livslop="draining")
        with pytest.raises(psycopg.errors.NoDataFound):
            _utsted(rt, modul, "staging", rel)
        rt.rollback()
        # status installert → avvist
        modul, rel, _, _ = _deployment_med_typer(m, status="installert")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _utsted(rt, modul, "staging", rel)
        rt.rollback()
        # ingen oppdragstype under releasens kontrakt → avvist
        modul, rel, _, _ = _deployment_med_typer(m, typer=0)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _utsted(rt, modul, "staging", rel)
        rt.rollback()
        # alt på plass → utstedt, med frister fra SERVERENS argumenter
        modul, rel, _, _ = _deployment_med_typer(m)
        oid, rad = _utsted(rt, modul, "staging", rel)
        rt.commit()
        assert rad[0] == oid and rad[1] < rad[2], rad
    finally:
        rt.close()
        m.close()


@pg
def test_ett_ubrukt_onboarding_per_deployment_men_utlopt_erstattes():
    """Unik-indeksen stopper to VENTENDE hemmeligheter; en glemt (utløpt,
    ubrukt) skal derimot ikke blokkere for alltid — den har aldri produsert
    et token og ryddes av neste utstedelse."""
    m = _c()
    rt = _rt()
    try:
        modul, rel, _, _ = _deployment_med_typer(m)
        oid1, _ = _utsted(rt, modul, "staging", rel)
        rt.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            _utsted(rt, modul, "staging", rel)
        rt.rollback()
        # La den utløpe (migrator kan sette klokka på en UBRUKT rad? Nei —
        # utloper er frosset av triggeren. Konstruer i stedet en rad som ER
        # utløpt: direkte INSERT som migrator, forbi funksjonens TTL.)
        modul2, rel2, _, _ = _deployment_med_typer(m)
        m.execute(
            "INSERT INTO modul_onboarding (onboarding_id,modul_id,miljo,"
            "release_id,hemmelighet_hash,familie_utloper,utstedt_av,utloper)"
            " VALUES (%s,%s,'staging',%s,%s,now()+interval '1 day','t',"
            " now()-interval '1 minute')",
            (uuid.uuid4(), modul2, rel2, _hex64()))
        m.commit()
        oid3, _ = _utsted(rt, modul2, "staging", rel2)
        rt.commit()
        assert oid3 is not None
    finally:
        rt.close()
        m.close()


# --------------------------------------------------------------------------
# Fase 2: innløsning (portene 4–6)
# --------------------------------------------------------------------------

@pg
def test_innlosning_er_engangs(monkeypatch=None):
    """Port 4: innløst to ganger → andre avvist, kun ett token. Og feil
    hemmelighet er SAMME feil utad som brukt hemmelighet (intet orakel)."""
    m = _c()
    rt = _rt()
    try:
        modul, rel, _, _ = _deployment_med_typer(m)
        hh = _hex64()
        oid, _ = _utsted(rt, modul, "staging", rel, hemmelighet_hash=hh)
        rt.commit()
        tid, rad = _innlos(rt, oid, hh)
        rt.commit()
        assert rad[1] == modul and rad[4] == 0     # modul_id, utstedt_epoch
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _innlos(rt, oid, hh)
        rt.rollback()
        assert m.execute("SELECT count(*) FROM modultoken WHERE"
                         " modul_id=%s", (modul,)).fetchone()[0] == 1
    finally:
        rt.close()
        m.close()


@pg
def test_to_samtidige_innlosninger_gir_noyaktig_ett_token():
    """Port 5: radlåsen serialiserer; taperen ser innlost_ts og avvises."""
    m = _c()
    try:
        modul, rel, _, _ = _deployment_med_typer(m)
        hh = _hex64()
        rt0 = _rt()
        oid, _ = _utsted(rt0, modul, "staging", rel, hemmelighet_hash=hh)
        rt0.commit()
        rt0.close()
        utfall = []

        def prov():
            rt = _rt()
            try:
                _innlos(rt, oid, hh)
                rt.commit()
                utfall.append("ok")
            except psycopg.errors.InvalidParameterValue:
                rt.rollback()
                utfall.append("avvist")
            finally:
                rt.close()

        t1, t2 = threading.Thread(target=prov), threading.Thread(target=prov)
        t1.start(); t2.start(); t1.join(); t2.join()
        assert sorted(utfall) == ["avvist", "ok"], utfall
        assert m.execute("SELECT count(*) FROM modultoken WHERE modul_id=%s",
                         (modul,)).fetchone()[0] == 1
    finally:
        m.close()


@pg
def test_utlopt_hemmelighet_avvises():
    """Port 6: > TTL → avvist. TTL-en er serverens, ikke requestens."""
    m = _c()
    rt = _rt()
    try:
        modul, rel, _, _ = _deployment_med_typer(m)
        hh = _hex64()
        oid = uuid.uuid4()
        m.execute(
            "INSERT INTO modul_onboarding (onboarding_id,modul_id,miljo,"
            "release_id,hemmelighet_hash,familie_utloper,utstedt_av,utloper)"
            " VALUES (%s,%s,'staging',%s,%s,now()+interval '365 days','t',"
            " now()-interval '1 second')", (oid, modul, rel, hh))
        m.commit()
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _innlos(rt, oid, hh)
        rt.rollback()
    finally:
        rt.close()
        m.close()


# --------------------------------------------------------------------------
# Rotasjon og familiehorisont (portene 20–23, 27–31)
# --------------------------------------------------------------------------

def _token(rt, m, *, familie_dager=365, token_dager=30):
    modul, rel, _, _ = _deployment_med_typer(m)
    hh = _hex64()
    oid, _ = _utsted(rt, modul, "staging", rel, dager=familie_dager,
                     hemmelighet_hash=hh)
    tid, rad = _innlos(rt, oid, hh, dager=token_dager)
    rt.commit()
    return modul, tid, rad


@pg
def test_rotasjon_ny_virker_forgjenger_faar_naade_kjeden_sporbar():
    """Port 20: etterfølgeren arver familie + epoch; forgjengeren
    tilbakekalles med 15 minutters nåde (fremtidig tilbakekalt_ts) og er
    GYLDIG i vinduet — in-flight-requests skal ikke dø av en rotasjon."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        ny_mac = _hex64()
        ny = rt.execute(
            "SELECT * FROM roter_modultoken(%s,%s,%s,30,'test')",
            (tid, uuid.uuid4(), ny_mac)).fetchone()
        rt.commit()
        rad = m.execute(
            "SELECT forgjenger, utstedt_epoch FROM modultoken WHERE"
            " token_id=%s", (ny[0],)).fetchone()
        assert rad[0] == tid and rad[1] == 0
        g = m.execute(
            "SELECT tilbakekalt_ts > now(), tilbakekalt_grunn FROM modultoken"
            " WHERE token_id=%s", (tid,)).fetchone()
        assert g[0] is True and g[1] == "rotert", g
        # ... og verifiseringen godtar forgjengeren i nådevinduet
        gammel_mac = m.execute("SELECT token_mac FROM modultoken WHERE"
                               " token_id=%s", (tid,)).fetchone()[0]
        assert rt.execute("SELECT * FROM verifiser_modultoken(%s)",
                          (gammel_mac,)).fetchone() is not None
        rt.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_en_forgjenger_faar_noyaktig_en_etterfolger():
    """Portene 21/30: UNIQUE(forgjenger) er garantien I LAGRINGEN — den
    andre rotasjonen taper uansett timing."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        rt.execute("SELECT * FROM roter_modultoken(%s,%s,%s,30,'test')",
                   (tid, uuid.uuid4(), _hex64()))
        rt.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            rt.execute("SELECT * FROM roter_modultoken(%s,%s,%s,30,'test')",
                       (tid, uuid.uuid4(), _hex64()))
        rt.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_rotasjon_kappes_mot_familiehorisonten_og_stopper_ved_den():
    """Portene 27/29: nær fristen → utloper == familie_utloper, aldri
    senere; etter fristen → avvist, ny onboarding kreves."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, rad = _token(rt, m, familie_dager=1, token_dager=30)
        # tokenets levetid var alt kappet ved innløsningen
        assert rad[5] == rad[6], rad                    # utloper == familie
        ny = rt.execute("SELECT * FROM roter_modultoken(%s,%s,%s,30,'test')",
                        (tid, uuid.uuid4(), _hex64())).fetchone()
        rt.commit()
        assert ny[1] == ny[2], ny                       # kappet igjen
        # Konstruer et token i en familie som ER passert (migrator, direkte)
        modul2, rel2, _, _ = _deployment_med_typer(m)
        o2, t2 = uuid.uuid4(), uuid.uuid4()
        m.execute(
            "INSERT INTO modul_onboarding (onboarding_id,modul_id,miljo,"
            "release_id,hemmelighet_hash,familie_utloper,utstedt_av,utloper,"
            "innlost_ts) VALUES (%s,%s,'staging',%s,%s,"
            "now()+interval '1 second','t',now(),now())",
            (o2, modul2, rel2, _hex64()))
        m.execute(
            "INSERT INTO modultoken (token_id,token_mac,onboarding_id,"
            "familie_utloper,modul_id,miljo,release_id,utstedt_epoch,"
            "utloper) SELECT %s,%s,onboarding_id,familie_utloper,modul_id,"
            "miljo,release_id,0,familie_utloper FROM modul_onboarding"
            " WHERE onboarding_id=%s", (t2, _hex64(), o2))
        m.commit()
        import time
        time.sleep(1.2)                                  # fristen passerer
        # CHECK-en (utloper <= familie_utloper) gjør at et token ALDRI kan
        # overleve familien sin — «etter fristen» treffer derfor alltid
        # utløps-avvisningen først, og familie-grenen i funksjonen er
        # belte-og-seler. Begge tekstene er samme avslag: rotasjonen skjer
        # ikke, ny onboarding kreves.
        with pytest.raises(psycopg.errors.InvalidParameterValue,
                           match="utlopt|familiehorisont"):
            rt.execute("SELECT * FROM roter_modultoken(%s,%s,%s,30,'test')",
                       (t2, uuid.uuid4(), _hex64()))
        rt.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_tilbakekalt_token_avvises_umiddelbart_og_gjenopplives_aldri():
    """Portene 22/42: eksplisitt tilbakekalling er umiddelbar; nulling av
    tilbakekalt_ts avvises av lagringen — også for migrator/eier."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        mac = m.execute("SELECT token_mac FROM modultoken WHERE token_id=%s",
                        (tid,)).fetchone()[0]
        rt.execute("SELECT tilbakekall_modultoken(%s,'kompromittert','test')",
                   (tid,))
        rt.commit()
        assert rt.execute("SELECT * FROM verifiser_modultoken(%s)",
                          (mac,)).fetchone() is None
        rt.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE modultoken SET tilbakekalt_ts=NULL WHERE"
                      " token_id=%s", (tid,))
        m.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE modultoken SET tilbakekalt_ts=now()+interval"
                      " '1 day' WHERE token_id=%s", (tid,))
        m.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_epoch_okning_terminerer_familien_og_rotasjon_arver_aldri_ny():
    """Port 23: nødstopp/reaktivering tilbakekaller alle levende tokener i
    SAMME transaksjon som epoch-bumpen; rotasjon ARVER forgjengerens epoch
    og kan aldri plukke opp den nye. Kontroll: fjern 035-blokken i
    `noddeaktiver_modul`, så blir denne rød."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        m.execute("SET ROLE disponit_modules_admin")
        m.execute("SELECT noddeaktiver_modul(%s,'test av 035','test')",
                  (modul,))
        m.execute("RESET ROLE")
        m.commit()
        rad = m.execute(
            "SELECT tilbakekalt_ts <= now(), tilbakekalt_grunn FROM"
            " modultoken WHERE token_id=%s", (tid,)).fetchone()
        assert rad == (True, "epoch_okning_nodstopp"), rad
        # rotasjon av det tilbakekalte tokenet → avvist (ingen vei tilbake
        # uten ny onboarding)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT * FROM roter_modultoken(%s,%s,%s,30,'test')",
                       (tid, uuid.uuid4(), _hex64()))
        rt.rollback()
    finally:
        rt.close()
        m.close()


# --------------------------------------------------------------------------
# Lagringskontrakten (portene 25, 31, 35–42) — den holder for ALLE roller,
# også migrator (tabelleier) og dermed funksjonseierne.
# --------------------------------------------------------------------------

@pg
def test_runtime_kan_ikke_skrive_noen_av_tabellene():
    """Port 25. Kontroll: gi disponit INSERT på modultoken, så blir denne
    rød."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        for sql in [
            "INSERT INTO modul_onboarding (onboarding_id,modul_id,miljo,"
            "release_id,hemmelighet_hash,familie_utloper,utstedt_av,utloper)"
            " VALUES (gen_random_uuid(),'x','staging','r','" + "a" * 64
            + "',now(),'t',now())",
            "UPDATE modultoken SET tilbakekalt_grunn='x'",
            "DELETE FROM modultoken_hendelse",
            "SELECT count(*) FROM modultoken",     # heller ikke LESE direkte
        ]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rt.execute(sql)
            rt.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_familiefristen_kan_ikke_flyttes_av_noen_skrivevei():
    """Portene 35–37, 39–41: `familiefrist.flyttet_framover_via_noen_
    skrivevei = 0`. Migrator er tabelleier — klarer ikke DEN, klarer ingen
    funksjonseier det heller."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        oid = m.execute("SELECT onboarding_id FROM modultoken WHERE"
                        " token_id=%s", (tid,)).fetchone()[0]
        # 35: flytt familiefristen (med tokener på familien)
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE modul_onboarding SET familie_utloper ="
                      " familie_utloper + interval '365 days'"
                      " WHERE onboarding_id=%s", (oid,))
        m.rollback()
        # 35 (uten tokener): også en UBRUKT familie er frosset
        modul2, rel2, _, _ = _deployment_med_typer(m)
        o2 = uuid.uuid4()
        m.execute(
            "INSERT INTO modul_onboarding (onboarding_id,modul_id,miljo,"
            "release_id,hemmelighet_hash,familie_utloper,utstedt_av,utloper)"
            " VALUES (%s,%s,'staging',%s,%s,now()+interval '1 day','t',"
            "now()+interval '1 hour')", (o2, modul2, rel2, _hex64()))
        m.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE modul_onboarding SET familie_utloper ="
                      " familie_utloper + interval '365 days'"
                      " WHERE onboarding_id=%s", (o2,))
        m.rollback()
        # 36: tokenets denormaliserte kopi er like frosset
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE modultoken SET familie_utloper ="
                      " familie_utloper + interval '1 day'"
                      " WHERE token_id=%s", (tid,))
        m.rollback()
        # 37/41: INSERT med frist/deployment som ikke matcher familieraden
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            m.execute(
                "INSERT INTO modultoken (token_id,token_mac,onboarding_id,"
                "familie_utloper,modul_id,miljo,release_id,utstedt_epoch,"
                "utloper) SELECT gen_random_uuid(),%s,onboarding_id,"
                "familie_utloper + interval '1 day',modul_id,miljo,"
                "release_id,0,familie_utloper FROM modul_onboarding"
                " WHERE onboarding_id=%s", (_hex64(), oid))
        m.rollback()
        # 39: reparenting til annen familie + senere frist i ÉN setning
        modul3, tid3, _ = _token(rt, m, familie_dager=700)
        o3 = m.execute("SELECT onboarding_id FROM modultoken WHERE"
                       " token_id=%s", (tid3,)).fetchone()[0]
        with pytest.raises((psycopg.errors.CheckViolation,
                            psycopg.errors.ForeignKeyViolation)):
            m.execute(
                "UPDATE modultoken SET onboarding_id=%s, familie_utloper="
                "(SELECT familie_utloper FROM modul_onboarding WHERE"
                " onboarding_id=%s) WHERE token_id=%s", (o3, o3, tid))
        m.rollback()
        # 31: direkte DML med utloper > familie_utloper → CHECK
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE modultoken SET utloper = familie_utloper"
                      " + interval '1 day' WHERE token_id=%s", (tid,))
        m.rollback()
        # 38: DELETE av familierad med levende tokener
        with pytest.raises((psycopg.errors.CheckViolation,
                            psycopg.errors.ForeignKeyViolation)):
            m.execute("DELETE FROM modul_onboarding WHERE onboarding_id=%s",
                      (oid,))
        m.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_identitetsfeltene_er_immutable_og_hendelser_append_only():
    """`modultoken.identitetsfelt_endret = 0` + append-only-sporet."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        for kolonne, verdi in [("modul_id", "'x'"), ("release_id", "'x'"),
                               ("utstedt_epoch", "99"),
                               ("utloper", "now()"),
                               ("token_mac", "'" + "b" * 64 + "'")]:
            with pytest.raises(psycopg.errors.CheckViolation):
                m.execute(f"UPDATE modultoken SET {kolonne}={verdi}"
                          " WHERE token_id=%s", (tid,))
            m.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE modultoken_hendelse SET aktor='x'")
        m.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("DELETE FROM modultoken WHERE token_id=%s", (tid,))
        m.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_hemmeligheten_finnes_kun_hashet():
    """Port 3 (DB-halvdelen): kolonnen KAN ikke bære klartekst — CHECK
    krever 64 hex. Klartekst-halvdelen (vist én gang) prøves i HTTP."""
    m = _c()
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute(
                "INSERT INTO modul_onboarding (onboarding_id,modul_id,miljo,"
                "release_id,hemmelighet_hash,familie_utloper,utstedt_av,"
                "utloper) VALUES (gen_random_uuid(),'m','staging','r',"
                "'klartekst-hemmelighet',now(),'t',now())")
        m.rollback()
    finally:
        m.close()


@pg
def test_registrer_artefakttype_navneform_og_prefiksoverlapp():
    """Klarsignalet §4/§8: lukket navneform + overlappssjekk under global
    lås. Kontroll: fjern overlappssjekken i 035-kroppen, så blir denne rød."""
    m = _c()
    try:
        modul, rel, ver, khash = _deployment_med_typer(m)
        m.execute("SET ROLE disponit_modules_admin")
        def reg(navn):
            m.execute("SELECT registrer_artefakttype(%s,%s,%s,%s,%s,'test')",
                      (navn, modul, ver, khash, _hex64()))
        stamme = f"a{secrets.token_hex(3)}"
        reg(f"{stamme}.b.c")
        m.commit()
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            reg("UPPER.ikke.lov")
        m.rollback()
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            reg("bare_to.ledd")
        m.rollback()
        with pytest.raises(psycopg.errors.UniqueViolation):
            reg(f"{stamme}.b.c.d")               # under eksisterende
        m.rollback()
        reg(f"{stamme}.b.cd")                    # punktumgrense: IKKE overlapp
        m.commit()
    finally:
        m.close()


@pg
def test_seedet_testtype_er_registrert_og_reservert():
    """§8: `test.onboarding.kvittering` finnes, eid av testkontrakten.
    (Utlednings-porten — aldri for produksjonsmiljø — prøves i HTTP.)"""
    m = _c()
    try:
        rad = m.execute(
            "SELECT eiermodul FROM artefakttype_register WHERE"
            " artefakttype='test.onboarding.kvittering'").fetchone()
        assert rad == ("m_test_onboarding",)
        status = m.execute("SELECT status FROM modulhode WHERE"
                           " modul_id='m_test_onboarding'").fetchone()[0]
        assert status != "aktiv"
    finally:
        m.close()


# --------------------------------------------------------------------------
# Familiehorisont-varslene (port 32): 30/7/1 døgn, idempotent, kun levende
# familier, kun plattformtenantens aktive admin-medlemmer.
# --------------------------------------------------------------------------

@pg
def test_familieutlop_varsles_30_7_1_idempotent():
    """Kontroll: fjern EXISTS-leddet for levende token i
    `varsle_tokenfamilie_utlop`, så blir dødfamilie-asserten rød; fjern
    ON CONFLICT-nøkkelen, så dobler antallet ved andre kjøring."""
    m = _c()
    rt = _rt()
    ten = "t-famvarsel-" + secrets.token_hex(3)
    try:
        # Plattformtenant med én aktiv admin og én ikke-admin.
        admin = m.execute(
            "INSERT INTO brukeridentitet (issuer, sub) VALUES (%s,%s)"
            " RETURNING bruker_id",
            ("https://idp.example", f"{ten}-adm")).fetchone()[0]
        leser = m.execute(
            "INSERT INTO brukeridentitet (issuer, sub) VALUES (%s,%s)"
            " RETURNING bruker_id",
            ("https://idp.example", f"{ten}-leser")).fetchone()[0]
        from db.pg import sett_kontekst
        sett_kontekst(m, ten, "sys", "r0")
        m.execute("INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
                  " VALUES (%s,%s,ARRAY['admin','leser'])", (ten, admin))
        m.execute("INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
                  " VALUES (%s,%s,ARRAY['leser'])", (ten, leser))
        m.commit()

        def familie(dager_igjen, med_levende_token=True):
            modul, rel, _, _ = _deployment_med_typer(m)
            o = uuid.uuid4()
            m.execute(
                "INSERT INTO modul_onboarding (onboarding_id,modul_id,miljo,"
                "release_id,hemmelighet_hash,familie_utloper,utstedt_av,"
                "utloper,innlost_ts) VALUES (%s,%s,'staging',%s,%s,"
                "now()+make_interval(days => %s),'t',now(),now())",
                (o, modul, rel, _hex64(), dager_igjen))
            m.execute(
                "INSERT INTO modultoken (token_id,token_mac,onboarding_id,"
                "familie_utloper,modul_id,miljo,release_id,utstedt_epoch,"
                "utloper,tilbakekalt_ts,tilbakekalt_grunn)"
                " SELECT %s,%s,onboarding_id,familie_utloper,modul_id,miljo,"
                "release_id,0,familie_utloper,%s,%s FROM modul_onboarding"
                " WHERE onboarding_id=%s",
                (uuid.uuid4(), _hex64(), None, None, o))
            if not med_levende_token:
                m.execute("UPDATE modultoken SET tilbakekalt_ts=now(),"
                          " tilbakekalt_grunn='drept' WHERE onboarding_id=%s"
                          " AND tilbakekalt_ts IS NULL", (o,))
            m.commit()
            return o

        naer = familie(5)             # innenfor 30 OG 7, ikke 1
        dod = familie(5, med_levende_token=False)
        fjern = familie(200)          # utenfor alle tersklene

        rt.execute("SELECT varsle_tokenfamilie_utlop(%s)", (ten,))
        rt.commit()
        sett_kontekst(m, ten, "sys", "r1")
        # Sveipen er PLATTFORMVID (den skal se alle familier, også dem andre
        # tester har etterlatt) — asserten scopes derfor til DENNE testens
        # familier.
        mine = {str(naer), str(dod), str(fjern)}
        rader = [r for r in m.execute(
            "SELECT bruker_id, ressurs_id, hendelse FROM varsel"
            " WHERE tenant=%s AND art='tokenfamilie_utloper'"
            " ORDER BY hendelse", (ten,)).fetchall() if r[1] in mine]
        m.rollback()
        assert {(r[1], r[2]) for r in rader} == {(str(naer), "30"),
                                                 (str(naer), "7")}, rader
        assert all(r[0] == admin for r in rader), \
            "varselet traff andre enn plattformadminene"
        assert not any(r[1] == str(dod) for r in rader), \
            "en familie uten levende token ble varslet"
        assert not any(r[1] == str(fjern) for r in rader)

        # Idempotent: andre sveip legger ingenting til.
        rt.execute("SELECT varsle_tokenfamilie_utlop(%s)", (ten,))
        rt.commit()
        sett_kontekst(m, ten, "sys", "r2")
        n = m.execute(
            "SELECT count(*) FROM varsel WHERE tenant=%s"
            " AND art='tokenfamilie_utloper' AND ressurs_id = ANY(%s)",
            (ten, list(mine))).fetchone()[0]
        m.rollback()
        assert n == 2, n
    finally:
        rt.close()
        m.close()
