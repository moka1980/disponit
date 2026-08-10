"""PR-013 CP1: migrasjon 012 — datamodell + integritetstriggere.

DB-en håndhever fullmaktsreglene, ikke koden: `er_forfatter` server-utledet
(V7), avledet `policyer.aktiv` ⇔ `policy_hode.aktiv_versjon` (V1/v5§1),
utkast-/runde-statemaskiner, append-only attestasjon. Hver trigger muteres bort
av en ulovlig operasjon som MÅ feile.
"""
import secrets

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
# Fersk tenant per kjøring: DB-en truncates ikke mellom kjøringer.
TEN = "t-pol-" + secrets.token_hex(3)


def _c():
    from db.pg import koble
    c = koble(MIGRATOR_DSN)
    # SESJONS-nivå GUC (is_local=false), så tenant-konteksten overlever commit
    # — ellers ville RLS skjult radene etter første commit og triggerne aldri
    # fyrt (0 rader oppdatert).
    c.execute("SELECT set_config('disponit.tenant',%s,false),"
              " set_config('disponit.aktor','sys',false)", (TEN,))
    return c


def _policyrad(c, pid, versjon, aktiv=False):
    c.execute(
        "INSERT INTO policyer (tenant,policy_id,versjon,innholds_hash,status,"
        "innhold,aktiv) VALUES (%s,%s,%s,%s,'validert_pilot','{}'::jsonb,%s)"
        " ON CONFLICT DO NOTHING",
        (TEN, pid, versjon, secrets.token_hex(32), aktiv))


def _hode(c, pid, aktiv_versjon=None, neste=1):
    c.execute("INSERT INTO policy_hode (tenant,policy_id,neste_versjon,"
              "aktiv_versjon) VALUES (%s,%s,%s,%s)", (TEN, pid, neste, aktiv_versjon))


def _utkast(c, uid, pid, av="bruker-a", status="utkast", innhold='{"a":1}'):
    c.execute(
        "INSERT INTO policyutkast (tenant,utkast_id,policy_id,innhold,status,"
        "opprettet_av) VALUES (%s,%s,%s,%s::jsonb,%s,%s)",
        (TEN, uid, pid, innhold, status, av))


def _runde(c, uid, runde=1, **over):
    felt = dict(diff_hash="d", utkast_innholds_hash="u", base_policy_hash="b",
                risikoklasse="UTVIDER", klassifisering_hash="k",
                klassifikatorversjon="kv1", policyskjema_versjon="0.2",
                motor_semantikkversjon="m1", deny_all_hash="da",
                deny_all_versjon="1", pakrevd_antall_godkjennere=2,
                utloper="now()+interval '1 hour'")
    felt.update(over)
    c.execute(
        "INSERT INTO aktiveringsrunde (tenant,utkast_id,runde,diff_hash,"
        "utkast_innholds_hash,base_policy_hash,risikoklasse,klassifisering_hash,"
        "klassifikatorversjon,policyskjema_versjon,motor_semantikkversjon,"
        "deny_all_hash,deny_all_versjon,pakrevd_antall_godkjennere,utloper)"
        f" VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,{felt['utloper']})",
        (TEN, uid, runde, felt["diff_hash"], felt["utkast_innholds_hash"],
         felt["base_policy_hash"], felt["risikoklasse"], felt["klassifisering_hash"],
         felt["klassifikatorversjon"], felt["policyskjema_versjon"],
         felt["motor_semantikkversjon"], felt["deny_all_hash"],
         felt["deny_all_versjon"], felt["pakrevd_antall_godkjennere"]))


def _attest(c, uid, bruker, er_forfatter, runde=1, jti=None):
    c.execute(
        "INSERT INTO aktiveringsattestasjon (tenant,utkast_id,runde,bruker_id,"
        "rolle,authz_version,er_forfatter,diff_hash,klassifisering_hash,"
        "risikoklasse,konvoluttversjon,konvolutt_hash,mac,mac_key_id,jti,utloper)"
        " VALUES (%s,%s,%s,%s,'okonomi',1,%s,'d','k','UTVIDER',1,'h','m','mk1',%s,"
        "now()+interval '1 hour')",
        (TEN, uid, runde, bruker, er_forfatter, jti or secrets.token_hex(16)))


@pg
def test_er_forfatter_trigger_avviser_feil_boolean():
    c = _c()
    try:
        _utkast(c, "u1", "p1", av="bruker-a")
        _policyrad(c, "p1", "1")
        _runde(c, "u1")
        # Riktig: bruker-a ER forfatter.
        _attest(c, "u1", "bruker-a", True)
        # Feil: en fremmed bruker påstår er_forfatter=True → trigger avviser.
        with pytest.raises(psycopg.errors.RaiseException) as ei:
            _attest(c, "u1", "bruker-b", True)
        assert "er_forfatter" in str(ei.value)
        c.rollback()
        # Feil andre vei: forfatteren merkes som IKKE-forfatter → avvist.
        c2 = _c()
        try:
            _utkast(c2, "u1b", "p1b", av="bruker-a")
            _policyrad(c2, "p1b", "1")
            _runde(c2, "u1b")
            with pytest.raises(psycopg.errors.RaiseException):
                _attest(c2, "u1b", "bruker-a", False)
            c2.rollback()
        finally:
            c2.close()
    finally:
        c.close()


@pg
def test_avledet_aktiv_peker_konsistens():
    c = _c()
    try:
        _policyrad(c, "p2", "1", aktiv=True)
        _policyrad(c, "p2", "2", aktiv=False)   # finnes, men ikke aktiv
        _hode(c, "p2", aktiv_versjon="1", neste=3)
        c.commit()                       # konsistent: peker=1, aktiv-rad=1 → OK
        # Flytt PEKEREN til versjon 2 mens den aktive raden fortsatt er 1 —
        # delindeksen fanger ikke dette (kun én aktiv rad); den DEFERRED
        # peker-triggeren gjør det, ved commit.
        c.execute("UPDATE policy_hode SET aktiv_versjon='2' WHERE tenant=%s AND"
                  " policy_id='p2'", (TEN,))
        with pytest.raises(psycopg.errors.RaiseException):
            c.commit()
        c.rollback()
    finally:
        c.close()


@pg
def test_peker_uten_aktiv_rad_avvises():
    c = _c()
    try:
        _policyrad(c, "p3", "1", aktiv=False)   # ingen aktiv rad
        _hode(c, "p3", aktiv_versjon="1")        # men pekeren peker
        with pytest.raises(psycopg.errors.RaiseException):
            c.commit()
        c.rollback()
    finally:
        c.close()


@pg
def test_utkast_statemaskin_og_optimistisk_laas():
    c = _c()
    try:
        _utkast(c, "u2", "p4", status="utkast")
        c.commit()
        # Innholdsendring uten versjonsøkning → avvist.
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE policyutkast SET innhold='{\"a\":2}'::jsonb"
                      " WHERE tenant=%s AND utkast_id='u2'", (TEN,))
        c.rollback()
        # Med versjonsøkning → OK.
        c.execute("UPDATE policyutkast SET innhold='{\"a\":2}'::jsonb,"
                  " utkastversjon=2 WHERE tenant=%s AND utkast_id='u2'", (TEN,))
        c.commit()
        # Ulovlig statusovergang utkast→aktivert → avvist.
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE policyutkast SET status='aktivert' WHERE tenant=%s"
                      " AND utkast_id='u2'", (TEN,))
        c.rollback()
    finally:
        c.close()


@pg
def test_frosset_innholds_hash_kan_ikke_endres():
    c = _c()
    try:
        _utkast(c, "u3", "p5")
        c.execute("UPDATE policyutkast SET status='validert', innholds_hash=%s"
                  " WHERE tenant=%s AND utkast_id='u3'", (secrets.token_hex(32), TEN))
        c.commit()
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE policyutkast SET innholds_hash=%s WHERE tenant=%s"
                      " AND utkast_id='u3'", (secrets.token_hex(32), TEN))
        c.rollback()
    finally:
        c.close()


@pg
def test_en_aktiv_aktiveringsrunde_delindeks():
    c = _c()
    try:
        _utkast(c, "u4", "p6")
        _runde(c, "u4", runde=1)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _runde(c, "u4", runde=2)       # to åpne runder for samme utkast
        c.rollback()
    finally:
        c.close()


@pg
def test_runde_bindingsfelt_frosset_og_statemaskin():
    c = _c()
    try:
        _utkast(c, "u5", "p7")
        _runde(c, "u5")
        c.commit()
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE aktiveringsrunde SET diff_hash='endret' WHERE"
                      " tenant=%s AND utkast_id='u5'", (TEN,))
        c.rollback()
        # apen→brukt er ulovlig (må via klar).
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE aktiveringsrunde SET status='brukt' WHERE tenant=%s"
                      " AND utkast_id='u5'", (TEN,))
        c.rollback()
    finally:
        c.close()


@pg
def test_attestasjon_append_only_og_unik_jti():
    c = _c()
    try:
        _utkast(c, "u6", "p8", av="bruker-a")
        _runde(c, "u6")
        j = secrets.token_hex(16)
        _attest(c, "u6", "bruker-a", True, jti=j)
        c.commit()
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE aktiveringsattestasjon SET rolle='x' WHERE tenant=%s"
                      " AND utkast_id='u6'", (TEN,))
        c.rollback()
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("DELETE FROM aktiveringsattestasjon WHERE tenant=%s AND"
                      " utkast_id='u6'", (TEN,))
        c.rollback()
        # Gjenbrukt jti → engangsbruk brytes.
        _utkast(c, "u6b", "p8b", av="bruker-a")
        _runde(c, "u6b")
        with pytest.raises(psycopg.errors.UniqueViolation):
            _attest(c, "u6b", "bruker-a", True, jti=j)
        c.rollback()
    finally:
        c.close()
