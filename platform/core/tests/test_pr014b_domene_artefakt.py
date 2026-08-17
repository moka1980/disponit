"""PR-014b CP1: domenekontroll · egress · artefakt — datamodell + integritet.

DB-en håndhever kontrakten: `domenekontroll`/`artefakt` er tenant-isolert
(RLS+FORCE) med statemaskiner; `domenekontroll_hendelse`/`artefakttype_register`
er append-only; ÉN verifisert eier per hostname og ÉN promotert artefakt per
(oppdrag, type); default-deny GRANT (runtime skriver aldri, ser aldri
`hostname_binding`; egress ser KUN visningen — aldri `challenge_token_hash`).
Hver invariant har en mutasjon som MÅ feile. (De herdede skrivefunksjonene
kommer i CP2; her er skjemaet + isolasjonen.)
"""
import secrets

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, TENANT, ANNEN_TENANT, migrator, miljo  # noqa: F401
from .test_m37 import _lag_sak, _lag_oppdrag, _sett_kontekst

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _host():
    return "d" + secrets.token_hex(6) + ".example"


def _dk(conn, tenant, hostname, *, status="ventende", gen=0, verifisert=False):
    _sett_kontekst(conn, tenant)
    conn.execute(
        "INSERT INTO domenekontroll (tenant, hostname, status,"
        " autorisasjonsgenerasjon, verifisert_ts, siste_vellykkede_revalidering,"
        " utloper) VALUES (%s,%s,%s,%s,"
        + ("now(), now(), now()+interval '90 days'" if verifisert
           else "NULL, NULL, NULL") + ")",
        (tenant, hostname, status, gen))
    conn.commit()


# ---------------- domenekontroll ----------------

@pg
def test_domenekontroll_rls_tenant_isolasjon(migrator):
    h = _host()
    _dk(migrator, TENANT, h)
    _sett_kontekst(migrator, ANNEN_TENANT)
    n = migrator.execute("SELECT count(*) FROM domenekontroll WHERE hostname=%s",
                         (h,)).fetchone()[0]
    migrator.rollback()
    assert n == 0, "en annen tenant så domenekontroll-raden (RLS-brudd)"


@pg
def test_domenekontroll_statemaskin_og_monoton_generasjon(migrator):
    h = _host()
    _dk(migrator, TENANT, h, status="verifisert", gen=2, verifisert=True)
    _sett_kontekst(migrator, TENANT)
    # verifisert → ventende er ulovlig.
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE domenekontroll SET status='ventende'"
                         " WHERE tenant=%s AND hostname=%s", (TENANT, h))
    migrator.rollback()
    # generasjonen kan ikke reduseres.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE domenekontroll SET autorisasjonsgenerasjon=1"
                         " WHERE tenant=%s AND hostname=%s", (TENANT, h))
    migrator.rollback()
    # (tenant, hostname) er frosset.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE domenekontroll SET hostname=%s"
                         " WHERE tenant=%s AND hostname=%s", (h + "x", TENANT, h))
    migrator.rollback()


@pg
def test_en_verifisert_per_hostname(migrator):
    h = _host()
    _dk(migrator, TENANT, h, status="verifisert", verifisert=True)
    # samme hostname, verifisert, annen tenant → delindeks avviser.
    with pytest.raises(psycopg.errors.UniqueViolation):
        _dk(migrator, ANNEN_TENANT, h, status="verifisert", verifisert=True)
    migrator.rollback()


@pg
def test_domenekontroll_hendelse_append_only(migrator):
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "INSERT INTO domenekontroll_hendelse (tenant, hostname, hendelse,"
        " aktor) VALUES (%s,%s,'utstedt','sys')", (TENANT, _host()))
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE domenekontroll_hendelse SET hendelse='x'"
                         " WHERE tenant=%s", (TENANT,))
    migrator.rollback()


@pg
def test_domenekontroll_og_hostname_binding_kan_ikke_slettes(migrator):
    h = _host()
    _dk(migrator, TENANT, h)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("DELETE FROM domenekontroll WHERE tenant=%s AND"
                         " hostname=%s", (TENANT, h))
    migrator.rollback()
    migrator.execute("INSERT INTO hostname_binding (hostname, tenant)"
                     " VALUES (%s,%s)", (h, TENANT))
    migrator.commit()
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("DELETE FROM hostname_binding WHERE hostname=%s", (h,))
    migrator.rollback()


# ---------------- artefakt ----------------

def _artefakttype(conn, modul, kh, at, ver=1):
    """kontrakt + artefakttype (begge direkte som migrator; immutabelt)."""
    conn.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,%s,%s,'p','k','krever_outbox','kompenserende')"
        " ON CONFLICT DO NOTHING", (modul, ver, kh))
    conn.execute(
        "INSERT INTO artefakttype_register (artefakttype, eiermodul,"
        " kontraktversjon, kontrakt_hash, skjema_hash) VALUES (%s,%s,%s,%s,%s)",
        (at, modul, ver, kh, "sh-" + secrets.token_hex(4)))
    conn.commit()


def _artefakt(conn, tenant, oppdrag_id, at, modul, kh, *, jti=None, ver=1):
    from db import kryptering
    _sett_kontekst(conn, tenant)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
    # Noncen skrives med: `artefakt_payload_struktur` (016) krever at et
    # artefakt med payload er STRUKTURELT dekrypterbart — ct||tag + 12-byte
    # nonce. Denne hjelperen skrev tidligere ciphertext uten nonce, altså
    # nøyaktig den udekrypterbare raden constrainten finnes for.
    ct, nonce = kryptering.krypter(dek, {"rapport": "x"}, tenant, key_id)
    jti = jti or ("jti-" + secrets.token_hex(8))
    aid = conn.execute(
        "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype, modul_id,"
        " release_id, kontraktversjon, kontrakt_hash, module_epoch, tilstand,"
        " storrelse_bytes, klartekst_sha256, ciphertext, nonce, dek_ref,"
        " kapabilitet_jti)"
        " VALUES (%s,%s,%s,%s,'r1',%s,%s,0,'staged',100,%s,%s,%s,%s,%s)"
        " RETURNING artefakt_id",
        (tenant, oppdrag_id, at, modul, ver, kh,
         "h-" + secrets.token_hex(8), ct, nonce, key_id, jti)).fetchone()[0]
    conn.commit()
    return aid


@pg
def test_artefakt_statemaskin_og_frosset_binding(migrator):
    at = f"at.t{secrets.token_hex(4)}.kvittering"; modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    aid = _artefakt(migrator, TENANT, opp, at, modul, kh)
    # identitet/hash frosset.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE artefakt SET klartekst_sha256='x'"
                         " WHERE artefakt_id=%s", (aid,))
    migrator.rollback()
    # staged → promotert er lovlig.
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE artefakt SET tilstand='promotert',"
                     " promotert_ts=now() WHERE artefakt_id=%s", (aid,))
    migrator.commit()
    # promotert er terminal.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE artefakt SET tilstand='forkastet'"
                         " WHERE artefakt_id=%s", (aid,))
    migrator.rollback()


@pg
def test_artefakt_ciphertext_kan_kun_nulles(migrator):
    at = f"at.t{secrets.token_hex(4)}.kvittering"; modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    aid = _artefakt(migrator, TENANT, opp, at, modul, kh)
    # Codex: nulling UTEN forkastelsen er forbudt — ellers kunne en feilaktig
    # privilegert UPDATE tømme nyttelasten mens tilstand + hash fortsatt påsto
    # at evidensen fantes.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE artefakt SET ciphertext=NULL"
                         " WHERE artefakt_id=%s", (aid,))
    migrator.rollback()
    # ... og et retained artefakt kan heller ikke tømmes.
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE artefakt SET tilstand='karantene'"
                     " WHERE artefakt_id=%s", (aid,))
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE artefakt SET ciphertext=NULL"
                         " WHERE artefakt_id=%s", (aid,))
    migrator.rollback()
    # ... og forkastelsen må ta BEGGE feltene: en halvtom rad (ciphertext NULL,
    # nonce igjen) er udekrypterbar evidens og avvises av statemaskinen — samme
    # invariant som CHECK-en artefakt_payload_struktur håndhever på skrivesiden.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE artefakt SET tilstand='forkastet', ciphertext=NULL"
                         " WHERE artefakt_id=%s", (aid,))
    migrator.rollback()
    # nulling av BEGGE I SAMME overgang staged → forkastet er lov.
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE artefakt SET tilstand='forkastet',"
                     " ciphertext=NULL, nonce=NULL WHERE artefakt_id=%s", (aid,))
    migrator.commit()
    # men å sette ciphertext til NYTT innhold er forbudt (frosset terminal + verd).
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE artefakt SET ciphertext=%s WHERE artefakt_id=%s",
                         (b"nytt", aid))
    migrator.rollback()


@pg
def test_ett_promotert_per_oppdrag(migrator):
    at = f"at.t{secrets.token_hex(4)}.kvittering"; modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    a1 = _artefakt(migrator, TENANT, opp, at, modul, kh)
    a2 = _artefakt(migrator, TENANT, opp, at, modul, kh)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE artefakt SET tilstand='promotert' WHERE"
                     " artefakt_id=%s", (a1,)); migrator.commit()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.UniqueViolation):
        migrator.execute("UPDATE artefakt SET tilstand='promotert' WHERE"
                         " artefakt_id=%s", (a2,))
    migrator.rollback()


@pg
def test_artefakttype_immutable(migrator):
    at = f"at.t{secrets.token_hex(4)}.kvittering"; modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE artefakttype_register SET skjema_hash='x'"
                         " WHERE artefakttype=%s", (at,))
    migrator.rollback()


# ---------------- GRANT-modell (port 17, 41) ----------------

@pg
def test_grant_runtime_default_deny_og_ingen_hostname_binding(migrator):
    # Port 41: runtime skriver ingen av de tre, og ser ikke hostname_binding.
    q = lambda sql, a: migrator.execute(sql, a).fetchone()[0]
    assert q("SELECT has_table_privilege('disponit','domenekontroll','INSERT')", ())         is False
    assert q("SELECT has_table_privilege('disponit','artefakt','UPDATE')", ())               is False
    assert q("SELECT has_table_privilege('disponit','artefakttype_register','INSERT')", ())  is False
    assert q("SELECT has_table_privilege('disponit','hostname_binding','SELECT')", ())       is False
    # men runtime LESER domenekontroll/artefakt/artefakttype_register.
    assert q("SELECT has_table_privilege('disponit','domenekontroll','SELECT')", ())         is True
    assert q("SELECT has_table_privilege('disponit','artefakt','SELECT')", ())               is True
    migrator.rollback()


@pg
def test_grant_egress_kun_visningen(migrator):
    # Port 17: egress ser visningen, ikke basistabellene, aldri hemmeligheten.
    q = lambda sql: migrator.execute(sql).fetchone()[0]
    assert q("SELECT has_table_privilege('disponit_egress','v_domeneautorisasjon','SELECT')") is True
    assert q("SELECT has_table_privilege('disponit_egress','hostname_binding','SELECT')")     is False
    assert q("SELECT has_table_privilege('disponit_egress','artefakt','SELECT')")             is False
    assert q("SELECT has_table_privilege('disponit_egress','artefakttype_register','SELECT')") is False
    # security_invoker krever kolonne-SELECT paa visningens kolonner — men ALDRI
    # paa hemmeligheten.
    assert q("SELECT has_column_privilege('disponit_egress','domenekontroll','hostname','SELECT')") is True
    assert q("SELECT has_column_privilege('disponit_egress','domenekontroll','challenge_token_hash','SELECT')") is False
    migrator.rollback()


# ================= CP2: herdede §2-funksjoner =================

def _admin():
    """migrator SET ROLE domains_admin (committed → overlever rollback)."""
    from db.pg import koble
    c = koble(MIGRATOR_DSN)
    c.execute("SET ROLE disponit_domains_admin")
    c.commit()
    return c


def _rt_call(sql, args):
    """Kall en artefaktfunksjon som RUNTIME (den har EXECUTE)."""
    from db.pg import koble
    c = koble(DSN)
    try:
        rad = c.execute(sql, args).fetchone()
        c.commit()
        return rad
    finally:
        c.close()


def _dkrow(conn, tenant, hostname):
    _sett_kontekst(conn, tenant)
    r = conn.execute("SELECT status, autorisasjonsgenerasjon,"
                     " challenge_token_hash FROM domenekontroll"
                     " WHERE tenant=%s AND hostname=%s",
                     (tenant, hostname)).fetchone()
    conn.rollback()
    return r


def _binding(conn, hostname):
    r = conn.execute("SELECT tenant FROM hostname_binding WHERE hostname=%s",
                     (hostname,)).fetchone()
    conn.rollback()
    return r[0] if r else None


@pg
def test_utsted_challenge_lagrer_hash_ikke_klartekst(migrator):
    # Port 7: kun hash lagres; reutstedelse virker.
    h = _host(); a = _admin()
    try:
        a.execute("SELECT utsted_challenge(%s,%s,false,'sha-1','sys')", (TENANT, h))
        a.commit()
        assert _dkrow(migrator, TENANT, h) == ("ventende", 0, "sha-1")
        a.execute("SELECT utsted_challenge(%s,%s,false,'sha-2','sys')", (TENANT, h))
        a.commit()
        assert _dkrow(migrator, TENANT, h)[2] == "sha-2", "reutstedelse virket ikke"
    finally:
        a.close()


@pg
def test_verifiser_setter_verifisert_og_binding(migrator):
    h = _host(); a = _admin()
    try:
        res = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                        (TENANT, h)).fetchone()[0]
        a.commit()
        assert res == "verifisert"
    finally:
        a.close()
    row = _dkrow(migrator, TENANT, h)
    assert row[0] == "verifisert" and row[1] == 1
    assert _binding(migrator, h) == TENANT


@pg
def test_b4_takeover_port10(migrator):
    # Port 10: aktiv A + B verifiser → A tilbakekalt, B avklaring_kreves,
    # 'konflikt:A', binding → B.
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        res = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                        (ANNEN_TENANT, h)).fetchone()[0]
        a.commit()
        assert res == "konflikt:" + TENANT
    finally:
        a.close()
    assert _dkrow(migrator, TENANT, h)[0] == "tilbakekalt"
    b = _dkrow(migrator, ANNEN_TENANT, h)
    assert b[0] == "avklaring_kreves"
    assert _binding(migrator, h) == ANNEN_TENANT


@pg
def test_b4_rad2_utlopt_a_gir_b_direkte_port13(migrator):
    # Port 13: A tilbakekalt → B verifiseres direkte (ingen avklaring).
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT tilbakekall_domenekontroll(%s,%s,'opphort','sys')",
                  (TENANT, h)); a.commit()
        res = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                        (ANNEN_TENANT, h)).fetchone()[0]
        a.commit()
        assert res == "verifisert"
    finally:
        a.close()
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "verifisert"
    assert _binding(migrator, h) == ANNEN_TENANT


@pg
def test_samme_tenant_reverifiser_ingen_avklaring_port14(migrator):
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        res = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                        (TENANT, h)).fetchone()[0]
        a.commit()
        assert res == "verifisert"
    finally:
        a.close()
    assert _dkrow(migrator, TENANT, h)[0] == "verifisert"


@pg
def test_avgjor_overtakelse_port12(migrator):
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (ANNEN_TENANT, h)); a.commit()   # B → avklaring_kreves
        gen = _dkrow(migrator, ANNEN_TENANT, h)[1]
        # godkjent → B verifisert (saken avgjøres for NØYAKTIG denne generasjonen)
        a.execute("SELECT avgjor_domeneovertakelse(%s,%s,%s,true,'sys')",
                  (ANNEN_TENANT, h, gen)); a.commit()
    finally:
        a.close()
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "verifisert"


@pg
def test_avgjor_avvist_gir_tilbakekalt(migrator):
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (ANNEN_TENANT, h)); a.commit()
        gen = _dkrow(migrator, ANNEN_TENANT, h)[1]
        a.execute("SELECT avgjor_domeneovertakelse(%s,%s,%s,false,'sys')",
                  (ANNEN_TENANT, h, gen)); a.commit()
    finally:
        a.close()
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "tilbakekalt"


@pg
def test_foreldet_overtakelsessak_avvises(migrator):
    """Codex: avgjørelsen er GJERDET av overtakelsesgenerasjonen.

    En gammel M-37-sak (generasjon N) skal ikke kunne autorisere en NYERE
    overtakelse (generasjon N+2) bare fordi raden tilfeldigvis står i
    `avklaring_kreves` igjen.

    MUTASJONEN SOM DREPER DENNE: fjern generasjonssjekken i
    `avgjor_domeneovertakelse` — da godkjenner den foreldede saken den nye
    konflikten.
    """
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (ANNEN_TENANT, h)); a.commit()      # B → avklaring (gen N)
        gammel_gen = _dkrow(migrator, ANNEN_TENANT, h)[1]
        # Overtakelse 1 AVVISES → B tilbakekalt (gen N+1).
        a.execute("SELECT avgjor_domeneovertakelse(%s,%s,%s,false,'sys')",
                  (ANNEN_TENANT, h, gammel_gen)); a.commit()
        # A verifiserer på nytt og B tar over igjen → ny konflikt (gen N+2).
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (ANNEN_TENANT, h)); a.commit()
        ny_gen = _dkrow(migrator, ANNEN_TENANT, h)[1]
        assert ny_gen > gammel_gen
        # Den GAMLE sakens godkjenning treffer den nye konflikten → avvist.
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            a.execute("SELECT avgjor_domeneovertakelse(%s,%s,%s,true,'sys')",
                      (ANNEN_TENANT, h, gammel_gen))
        a.rollback()
    finally:
        a.close()
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "avklaring_kreves", \
        "en foreldet sak autoriserte en nyere overtakelse"


@pg
def test_forbigatt_utfordrer_kan_ikke_godkjennes(migrator):
    """Codex P1: generasjonsgjerdet er tenant-lokalt — hostnavnet er globalt.

    Tar en TREDJE tenant C over mens B står i avklaring, flyttes
    `hostname_binding` til C, mens B-radens status og generasjon står helt urørt.
    B sin eldre sak kunne derfor godkjennes etterpå: B ble verifisert og skrev
    bindingen TILBAKE til seg selv, mens C sin nyere konflikt fortsatt lå
    uavgjort. Godkjenning gjerdes derfor også mot den gjeldende bindingshaveren.
    AVVISNING står fortsatt åpen — en forbigått utfordrer må kunne ryddes ut.

    PR-015 rydder B ut automatisk (degraderingstriggeren på `hostname_binding`,
    migrasjon 019 §3.25), så B når normalt aldri dette gjerdet lenger. Gjerdet
    beholdes og måles likevel: det er den siste skansen dersom en forbigått rad
    på noe vis står i `avklaring_kreves` mens bindingen ligger hos en annen —
    derfor gjenskaper testen nettopp den tilstanden direkte.

    MUTASJONEN SOM DREPER DENNE: fjern hostname_binding-sjekken i
    `avgjor_domeneovertakelse`."""
    tredje = "t-api-tredje"
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        # B tar over → B i avklaring, bindingen på B.
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (ANNEN_TENANT, h)); a.commit()
        # C tar over mens B venter → bindingen flyttes til C, og B degraderes
        # i samme transaksjon (PR-015 §3).
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (tredje, h)); a.commit()
        assert _binding(migrator, h) == tredje
        assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "tilbakekalt", \
            "den forbigåtte utfordreren ble stående i avklaring"
        # Gjenskap den forbigåtte-i-avklaring-tilstanden gjerdet finnes for.
        _sett_kontekst(migrator, ANNEN_TENANT)
        migrator.execute("UPDATE domenekontroll SET status='avklaring_kreves'"
                         " WHERE tenant=%s AND hostname=%s", (ANNEN_TENANT, h))
        migrator.commit()
        b_gen = _dkrow(migrator, ANNEN_TENANT, h)[1]
        # B sin sak er FORBIGÅTT: godkjenning avvises på bindingen.
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            a.execute("SELECT avgjor_domeneovertakelse(%s,%s,%s,true,'sys')",
                      (ANNEN_TENANT, h, b_gen))
        a.rollback()
        # ...men den kan fortsatt AVVISES, slik at B ikke blir stående for alltid.
        a.execute("SELECT avgjor_domeneovertakelse(%s,%s,%s,false,'sys')",
                  (ANNEN_TENANT, h, b_gen)); a.commit()
    finally:
        a.close()
    assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "tilbakekalt"
    assert _binding(migrator, h) == tredje, \
        "en forbigått utfordrer tok bindingen tilbake"


@pg
def test_reverifisering_under_avklaring_blokkeres(migrator):
    """Codex: `avklaring_kreves` er terminal for verifiseringsveien.

    Etter at B har overtatt står B i avklaring MED bindingen på seg. Et retry
    av samme verifisering hopper da over overtakelsesgrenen (eieren er B selv)
    og traff upserten, som satte B rett til `verifisert` — hele M-37-avgjørelsen
    omgått.

    MUTASJONEN SOM DREPER DENNE: fjern avklaring-sjekken øverst i
    `verifiser_domenekontroll`.
    """
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        res = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                        (ANNEN_TENANT, h)).fetchone()[0]
        a.commit()
        assert res == "konflikt:" + TENANT
        gen = _dkrow(migrator, ANNEN_TENANT, h)[1]
        # RETRY av samme verifisering.
        res2 = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                         (ANNEN_TENANT, h)).fetchone()[0]
        a.commit()
        assert res2 == "avklaring_kreves"
    finally:
        a.close()
    rad = _dkrow(migrator, ANNEN_TENANT, h)
    assert rad[0] == "avklaring_kreves", "retry verifiserte forbi M-37-avgjørelsen"
    assert rad[1] == gen, "retry flyttet generasjonen"


@pg
def test_revalider_endrer_ikke_status(migrator):
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT revalider_domenekontroll(%s,%s,'sys')", (TENANT, h))
        a.commit()
    finally:
        a.close()
    assert _dkrow(migrator, TENANT, h)[0] == "verifisert"


@pg
def test_tilbakekall_idempotent(migrator):
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT tilbakekall_domenekontroll(%s,%s,'grunn','sys')",
                  (TENANT, h)); a.commit()
        g1 = _dkrow(migrator, TENANT, h)[1]
        a.execute("SELECT tilbakekall_domenekontroll(%s,%s,'igjen','sys')",
                  (TENANT, h)); a.commit()   # idempotent, ingen gen++
    finally:
        a.close()
    row = _dkrow(migrator, TENANT, h)
    assert row[0] == "tilbakekalt" and row[1] == g1


@pg
def test_runtime_kan_ikke_kalle_domenefunksjoner(migrator):
    from db.pg import koble
    c = koble(DSN)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT verifiser_domenekontroll('t','h',false,'x')")
        c.rollback()
    finally:
        c.close()


# ---------------- artefakt-funksjoner ----------------

def _artefakt_oppsett(migrator):
    at = f"at.t{secrets.token_hex(4)}.kvittering"; modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    from db import kryptering
    _sett_kontekst(migrator, TENANT)
    key_id, _ = kryptering.hent_eller_opprett_aktiv_dek(migrator, TENANT)
    migrator.commit()
    return at, modul, kh, opp, key_id


# port 36 (idempotent (jti,hash) + konflikt) dekkes nå av
# test_pr014b_artefakt_api::test_upload_* via den ekte kapabilitetsflyten
# (lagre_artefakt_staged validerer+forbruker kapabiliteten atomisk, 016:502).


@pg
def test_promoter_epoch_avvik_port37(migrator):
    at = f"at.t{secrets.token_hex(4)}.kvittering"; modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    aid = _artefakt(migrator, TENANT, opp, at, modul, kh)
    _sett_kontekst(migrator, TENANT)
    rel, h = migrator.execute("SELECT release_id, klartekst_sha256 FROM artefakt"
                              " WHERE artefakt_id=%s", (aid,)).fetchone()
    migrator.rollback()
    # epoch-avvik (5 <> stemplet 0) → avvist, ingen promotering.
    with pytest.raises(psycopg.errors.Error):
        _rt_call("SELECT promoter_artefakt(%s,%s,%s,%s,5,%s,'sys')",
                 (aid, TENANT, opp, rel, h))
    # riktig epoch → promotert.
    _rt_call("SELECT promoter_artefakt(%s,%s,%s,%s,0,%s,'sys')",
             (aid, TENANT, opp, rel, h))
    _sett_kontekst(migrator, TENANT)
    st = migrator.execute("SELECT tilstand FROM artefakt WHERE artefakt_id=%s",
                          (aid,)).fetchone()[0]
    migrator.rollback()
    assert st == "promotert"


@pg
def test_promoter_idempotent_validerer_binding(migrator):
    """Den idempotente returen for et alt promotert artefakt må ligge ETTER
    binding/hash/epoch-kontrollen (Codex 016:799).

    MUTASJONEN SOM DREPER DENNE: flytt `IF r.tilstand = 'promotert' THEN RETURN`
    tilbake foran sammenligningene — da svarer en herdet SECURITY DEFINER-
    funksjon «promotert» på et kall med feil oppdrag, feil release, feil epoch
    eller feil signert hash, og applikasjonen leser det som verifisert evidens.
    """
    at = f"at.t{secrets.token_hex(4)}.kvittering"; modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    # «Feil oppdrag» må ha sin EGEN sak: `en_aktiv_reparasjon_per_sak` tillater
    # bare én aktiv reparasjon per sak, så en andre `_lag_oppdrag` på SAMME sak
    # traff `ON CONFLICT DO NOTHING` og etterlot ingen reparasjonsoperasjon for
    # den nye repair_operation_id-en — oppdraget feilet da på FK-en i stedet for
    # å teste noe.
    sak2, logg2 = _lag_sak(migrator, TENANT)
    opp2, _ = _lag_oppdrag(migrator, TENANT, sak2, logg2)
    aid = _artefakt(migrator, TENANT, opp, at, modul, kh)
    _sett_kontekst(migrator, TENANT)
    rel, h = migrator.execute("SELECT release_id, klartekst_sha256 FROM artefakt"
                              " WHERE artefakt_id=%s", (aid,)).fetchone()
    migrator.rollback()
    _rt_call("SELECT promoter_artefakt(%s,%s,%s,%s,0,%s,'sys')",
             (aid, TENANT, opp, rel, h))

    # Identisk retry er fortsatt idempotent suksess.
    _rt_call("SELECT promoter_artefakt(%s,%s,%s,%s,0,%s,'sys')",
             (aid, TENANT, opp, rel, h))

    # …men ingen av de avvikende parametersettene slipper gjennom.
    for args in ((aid, TENANT, opp2, rel, 0, h),          # feil oppdrag
                 (aid, TENANT, opp, "r-annen", 0, h),     # feil release
                 (aid, TENANT, opp, rel, 7, h),           # feil epoch
                 (aid, TENANT, opp, rel, 0, "h-feil")):   # feil signert hash
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _rt_call("SELECT promoter_artefakt(%s,%s,%s,%s,%s,%s,'sys')", args)


def _gammelt_staged_artefakt(migrator, *, evidensfrist):
    """Staged artefakt > 24 t gammelt på et oppdrag med gitt evidensfrist."""
    at = f"at.t{secrets.token_hex(4)}.kvittering"; modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg,
                          utforelsesfrist="-3 hours", evidensfrist=evidensfrist)
    aid = _artefakt(migrator, TENANT, opp, at, modul, kh)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE artefakt SET opprettet=now()-interval '25 hours'"
                     " WHERE artefakt_id=%s", (aid,)); migrator.commit()
    return aid


@pg
def test_rydd_staged_forkaster_og_nuller_port40(migrator):
    # Evidensfristen er ute → 24 t-regelen gjelder.
    aid = _gammelt_staged_artefakt(migrator, evidensfrist="-2 hours")
    a = _admin()
    try:
        n = a.execute("SELECT rydd_staged_artefakter()").fetchone()[0]
        a.commit()
        assert n >= 1
    finally:
        a.close()
    _sett_kontekst(migrator, TENANT)
    row = migrator.execute("SELECT tilstand, ciphertext FROM artefakt"
                           " WHERE artefakt_id=%s", (aid,)).fetchone()
    migrator.rollback()
    assert row[0] == "forkastet" and row[1] is None


@pg
def test_rydd_venter_paa_evidensfristen(migrator):
    """Codex P1/P2: oppryddingen må ikke løpe foran evidensfristen.

    Oppdraget tar imot signert evidens til `evidensfrist` (produksjon: 30 døgn),
    mens oppryddingen forkastet alt staged etter 24 t. En kvittering som landet i
    vinduet mellom fristene ble godtatt som `sen_kvittering`, men `bevar_artefakt`
    oppdaterer kun `staged`-rader — hadde oppryddingen kjørt først, pekte den
    godtatte evidensen på en rapport med nullet ciphertext.

    MUTASJONEN SOM DREPER DENNE: fjern evidensfrist-vilkåret i
    `rydd_staged_artefakter` — da forkastes artefaktet under, midt i vinduet der
    evidens fortsatt kan leveres."""
    aid = _gammelt_staged_artefakt(migrator, evidensfrist="30 days")
    a = _admin()
    try:
        a.execute("SELECT rydd_staged_artefakter()"); a.commit()
    finally:
        a.close()
    _sett_kontekst(migrator, TENANT)
    row = migrator.execute("SELECT tilstand, ciphertext IS NOT NULL FROM artefakt"
                           " WHERE artefakt_id=%s", (aid,)).fetchone()
    migrator.rollback()
    assert row == ("staged", True), \
        "oppryddingen forkastet et artefakt før oppdragets evidensfrist"


@pg
def test_b4_overtakelsessak_idempotent_port11(migrator):
    # Port 11: konflikt → ÉN M-37-sak (familie domeneovertakelse), idempotent per
    # overtakelsesgenerasjon; ny generasjon → ny sak.
    from api.domeneovertakelse import opprett_overtakelsessak
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        res = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                        (ANNEN_TENANT, h)).fetchone()[0]
        a.commit()
        assert res == "konflikt:" + TENANT
    finally:
        a.close()
    tapt = res.split(":", 1)[1]
    gen = _dkrow(migrator, ANNEN_TENANT, h)[1]   # B-generasjon etter overtakelse
    _sett_kontekst(migrator, ANNEN_TENANT)
    uid1 = opprett_overtakelsessak(migrator, tenant_ny=ANNEN_TENANT, hostname=h,
                                   tenant_tapt=tapt, generasjon=gen, aktor="sys")
    migrator.commit()
    _sett_kontekst(migrator, ANNEN_TENANT)
    row = migrator.execute("SELECT kategori, sakstype, status FROM unntak"
                           " WHERE tenant=%s AND id=%s",
                           (ANNEN_TENANT, uid1)).fetchone()
    migrator.rollback()
    assert row == ("domeneovertakelse", "sikkerhet", "ny")
    # idempotent: samme konflikt (samme generasjon) → samme sak.
    _sett_kontekst(migrator, ANNEN_TENANT)
    uid2 = opprett_overtakelsessak(migrator, tenant_ny=ANNEN_TENANT, hostname=h,
                                   tenant_tapt=tapt, generasjon=gen, aktor="sys")
    migrator.commit()
    assert uid2 == uid1
    # ny overtakelsesgenerasjon → ny sak.
    _sett_kontekst(migrator, ANNEN_TENANT)
    uid3 = opprett_overtakelsessak(migrator, tenant_ny=ANNEN_TENANT, hostname=h,
                                   tenant_tapt=tapt, generasjon=gen + 1, aktor="sys")
    migrator.commit()
    assert uid3 != uid1


@pg
def test_karantene_bevares_gjennom_rydd(migrator):
    # Codex §7 pkt. 8: et karantenesatt artefakt (uverifisert kvittering) må
    # OVERLEVE oppryddingen — den rører kun 'staged'.
    at = f"at.t{secrets.token_hex(4)}.kvittering"; modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    aid = _artefakt(migrator, TENANT, opp, at, modul, kh)
    from db.pg import koble
    c = koble(DSN)
    try:
        c.execute("SELECT karantenesett_artefakt(%s,%s,%s)", (aid, TENANT, opp))
        c.commit()
    finally:
        c.close()
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute("SELECT tilstand FROM artefakt WHERE artefakt_id=%s",
                            (aid,)).fetchone()[0] == "karantene"
    migrator.execute("UPDATE artefakt SET opprettet=now()-interval '25 hours'"
                     " WHERE artefakt_id=%s", (aid,)); migrator.commit()
    a = _admin()
    try:
        a.execute("SELECT rydd_staged_artefakter()"); a.commit()
    finally:
        a.close()
    _sett_kontekst(migrator, TENANT)
    st = migrator.execute("SELECT tilstand FROM artefakt WHERE artefakt_id=%s",
                          (aid,)).fetchone()[0]
    migrator.rollback()
    assert st == "karantene", "rydd forkastet et karantene-artefakt"


@pg
def test_append_only_tabeller_ingen_truncate(migrator):
    """Codex P2: TRUNCATE omgår rad-triggere i PostgreSQL. De append-only
    tabellene har nå statement-nivå TRUNCATE-vakt slik at skjemaeieren ikke kan
    tømme dem tross immutabilitets-invarianten.

    MUTASJONEN SOM DREPER DENNE: fjern BEFORE TRUNCATE-triggerne."""
    # domenekontroll_hendelse har ingen innkommende FK → triggeren er det ENESTE
    # som stopper en TRUNCATE.
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("TRUNCATE domenekontroll_hendelse")
    migrator.rollback()
    # artefakttype_register: FK fra artefakt blokkerer plain TRUNCATE, men CASCADE
    # (som ellers ville tømt BEGGE) fanges nettopp av statement-triggeren.
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("TRUNCATE artefakttype_register CASCADE")
    migrator.rollback()


@pg
def test_overtakelse_baerer_ny_wildcard(migrator):
    """Codex P2: overtakelsen bærer den NETTOPP forespurte wildcard-scopen inn i
    avklaringsraden, ikke den gamle. Ellers kunne en tenant med en gammel
    wildcard-rad fullføre en eksakt-host-overtakelse og etter M-37 bli verifisert
    med feil scope.

    MUTASJONEN SOM DREPER DENNE: fjern `wildcard = p_wildcard` fra ON CONFLICT."""
    h = _host(); a = _admin()
    try:
        # B har en rad med wildcard=false; settes tilbakekalt (behold gammel rad).
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (ANNEN_TENANT, h)); a.commit()
        _sett_kontekst(migrator, ANNEN_TENANT)
        migrator.execute("UPDATE domenekontroll SET status='tilbakekalt',"
                         " autorisasjonsgenerasjon=autorisasjonsgenerasjon+1"
                         " WHERE tenant=%s AND hostname=%s", (ANNEN_TENANT, h))
        migrator.commit()
        # A blir eier.
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        # B tar over igjen, nå med wildcard=TRUE.
        a.execute("SELECT verifiser_domenekontroll(%s,%s,true,'sys')",
                  (ANNEN_TENANT, h)); a.commit()
    finally:
        a.close()
    _sett_kontekst(migrator, ANNEN_TENANT)
    rad = migrator.execute("SELECT status, wildcard FROM domenekontroll"
                           " WHERE tenant=%s AND hostname=%s",
                           (ANNEN_TENANT, h)).fetchone()
    migrator.rollback()
    assert rad == ("avklaring_kreves", True), \
        "overtakelsen beholdt den gamle wildcard-scopen"


@pg
def test_wildcard_ikke_utvidbar_under_avklaring(migrator):
    """Codex: mens en rad avventer M-37-avklaring kan scope IKKE endres. Ellers
    kunne B reutstede en wildcard-challenge i avklaring_kreves, og avgjor godkjenne
    den utvidede scopen uten at wildcard-challengen ble verifisert.

    MUTASJONEN SOM DREPER DENNE: fjern 'avklaring_kreves' fra CASE-en i
    utsted_challenge sin ON CONFLICT."""
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (ANNEN_TENANT, h)); a.commit()   # B → avklaring_kreves (wildcard=false)
        # B reutsteder en WILDCARD-challenge mens den avventer avklaring.
        a.execute("SELECT utsted_challenge(%s,%s,true,%s,'sys')",
                  (ANNEN_TENANT, h, "th-" + secrets.token_hex(8))); a.commit()
    finally:
        a.close()
    _sett_kontekst(migrator, ANNEN_TENANT)
    rad = migrator.execute("SELECT status, wildcard FROM domenekontroll"
                           " WHERE tenant=%s AND hostname=%s",
                           (ANNEN_TENANT, h)).fetchone()
    migrator.rollback()
    assert rad == ("avklaring_kreves", False), \
        "wildcard-scopen ble utvidet mens raden avventet avklaring"


@pg
def test_tredje_tenant_gaar_i_avklaring_ikke_direkte(migrator):
    """Codex: mens et hostnavn er under aktiv M-37-avklaring må en TREDJE tenant
    OGSÅ gå i avklaring — aldri direkte verifisert. Ellers omgår en DNS-kontrollør
    M-37 ved å forsøke overtakelsen to ganger under ulike tenanter (det andre
    forsøket falt gjennom overtakelsesgrenen til direkte-verifisering).

    MUTASJONEN SOM DREPER DENNE: fjern ELSIF v_status_a='avklaring_kreves'-grenen."""
    tredje = "t3-" + secrets.token_hex(4)
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (ANNEN_TENANT, h)); a.commit()   # B4-overtakelse → B avklaring
        # En TREDJE tenant forsøker samme hostnavn mens B avventer avklaring.
        res = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                        (tredje, h)).fetchone()[0]
        a.commit()
        assert res == "konflikt:" + ANNEN_TENANT, \
            "tredje tenant ble ikke behandlet som en konflikt"
    finally:
        a.close()
    assert _dkrow(migrator, tredje, h)[0] == "avklaring_kreves", \
        "tredje tenant ble direkte verifisert forbi M-37"
    # Ingen er verifisert på hostnavnet.
    _sett_kontekst(migrator, tredje)
    assert _dkrow(migrator, tredje, h)[0] != "verifisert"


@pg
def test_revalider_avviser_ikke_verifisert(migrator):
    """Codex: revalidering registrerer KUN en hendelse når nøyaktig én verifisert
    rad ble oppdatert. Racet mot tilbakekalling/overtakelse (eller kalt for et
    ukjent/ikke-verifisert hostnavn) traff UPDATE-en null rader — men loggen påsto
    likevel en vellykket revalidering etter at autorisasjonen var trukket.

    MUTASJONEN SOM DREPER DENNE: fjern IF NOT FOUND-sjekken."""
    h = _host(); a = _admin()
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            a.execute("SELECT revalider_domenekontroll(%s,%s,'sys')", (TENANT, h))
        a.rollback()
    finally:
        a.close()
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute("SELECT count(*) FROM domenekontroll_hendelse"
                         " WHERE hostname=%s AND hendelse='revalidert'",
                         (h,)).fetchone()[0]
    migrator.rollback()
    assert n == 0, "en revalidert-hendelse ble logget uten en verifisert rad"


@pg
def test_artefakt_dek_ref_frosset(migrator):
    """Codex: dek_ref er frosset. Krypteringen binder AES-GCM-AAD til tenant|key_id,
    så en repointing til en annen nøkkel gjør artefaktet udekrypterbart mens
    ciphertext/hash/tilstand står urørt.

    MUTASJONEN SOM DREPER DENNE: fjern dek_ref fra frys-sjekken i artefakt_statemaskin."""
    at = f"at.t{secrets.token_hex(4)}.kvittering"; modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(migrator, modul, kh, at)
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag(migrator, TENANT, sak, logg)
    aid = _artefakt(migrator, TENANT, opp, at, modul, kh)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE artefakt SET dek_ref='annen-key'"
                         " WHERE artefakt_id=%s", (aid,))
    migrator.rollback()


@pg
def test_visning_eksponerer_wildcard_scope(migrator):
    """Codex: v_domeneautorisasjon MÅ eksponere scope-biten, ellers kan egress
    ikke skille en eksakt-host- fra en wildcard-verifisering."""
    cols = migrator.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_name='v_domeneautorisasjon'").fetchall()
    migrator.rollback()
    assert ("wildcard_scope",) in cols, "visningen mangler scope-biten"
    # egress har KOLONNE-SELECT på wildcard (security_invoker leser basiskolonnen).
    ok = migrator.execute("SELECT has_column_privilege('disponit_egress',"
                          "'domenekontroll','wildcard','SELECT')").fetchone()[0]
    migrator.rollback()
    assert ok is True, "egress mangler SELECT på wildcard-kolonnen"


@pg
def test_avvist_kandidat_ma_readjudikeres(migrator):
    """Codex: en kandidat AVVIST av M-37 står `tilbakekalt` men fortsatt som
    bindingseier. En re-verifisering ser da seg selv som eier, hopper over alle
    fremmed-eier-grenene og ville upsertet seg rett til verifisert — omgått
    avvisningen. Den tvinges nå tilbake gjennom avklaring.

    Codex (neste runde): og den må gi KONFLIKTSIGNALET, ikke `avklaring_kreves`.
    `opprett_overtakelsessak` lages kun fra `konflikt:<tapt-tenant>`; uten det
    ble reapplikasjonen stående i avklaring uten noen sak som kunne avgjøre den.
    Generasjonen økes, så idempotensnøkkelen gir en NY sak.

    MUTASJONEN SOM DREPER DENNE: fjern tilbakekalt+bindingseier-grenen i
    verifiser, eller la den returnere 'avklaring_kreves' igjen."""
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (ANNEN_TENANT, h)); a.commit()   # B avklaring, binding=B
        gen = _dkrow(migrator, ANNEN_TENANT, h)[1]
        a.execute("SELECT avgjor_domeneovertakelse(%s,%s,%s,false,'sys')",
                  (ANNEN_TENANT, h, gen)); a.commit()   # avvist → B tilbakekalt
        assert _dkrow(migrator, ANNEN_TENANT, h)[0] == "tilbakekalt"
        gen_avvist = _dkrow(migrator, ANNEN_TENANT, h)[1]
        # B re-verifiserer → IKKE verifisert, men NY konflikt (ny sak) mot A.
        res = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                        (ANNEN_TENANT, h)).fetchone()[0]
        a.commit()
        assert res == "konflikt:" + TENANT, \
            "reapplikasjonen ga ikke konfliktsignalet en ny M-37-sak lages fra"
    finally:
        a.close()
    rad = _dkrow(migrator, ANNEN_TENANT, h)
    assert rad[0] == "avklaring_kreves", \
        "en avvist kandidat re-verifiserte seg forbi M-37"
    assert rad[1] > gen_avvist, \
        "generasjonen sto stille — saken ville kollidert med den avgjorte"


@pg
def test_ordinaer_tilbakekalling_laaser_ikke_ute_reverifisering(migrator):
    """Motstykket: en tenant som ble tilbakekalt UTEN noen M-37-konflikt (ordinær
    tilbakekalling, ingen motpart) har ingen sak å gjenåpne og ingen avgjørelse å
    omgå. Tvang vi DEN inn i `avklaring_kreves`, ville den blitt stående der for
    alltid: `avgjor_domeneovertakelse` krever en sak, og ingen sak kan lages uten
    en tapt tenant. Den følger derfor den dokumenterte veien og verifiserer på
    nytt.

    MUTASJONEN SOM DREPER DENNE: fjern `konflikt_motpart IS NOT NULL` fra
    reapplication-grenen i verifiser."""
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        a.execute("SELECT tilbakekall_domenekontroll(%s,%s,'driftsavvik','sys')",
                  (TENANT, h)); a.commit()
        assert _dkrow(migrator, TENANT, h)[0] == "tilbakekalt"
        res = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                        (TENANT, h)).fetchone()[0]
        a.commit()
        assert res == "verifisert", \
            "en ordinært tilbakekalt eier ble låst inne i en avklaring uten motpart"
    finally:
        a.close()
    assert _dkrow(migrator, TENANT, h)[0] == "verifisert"


@pg
def test_hendelse_stempler_generasjon(migrator):
    """Codex: hver transisjonshendelse stemples med resulterende
    autorisasjonsgenerasjon (sto NULL) → den append-only historikken kan
    rekonstruere hvilken generasjon som ble tilbakekalt / gikk i avklaring.

    MUTASJONEN SOM DREPER DENNE: fjern hendelse_stamp_gen-triggeren."""
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
    finally:
        a.close()
    _sett_kontekst(migrator, TENANT)
    gen = migrator.execute("SELECT autorisasjonsgenerasjon FROM domenekontroll"
                           " WHERE tenant=%s AND hostname=%s",
                           (TENANT, h)).fetchone()[0]
    rows = migrator.execute("SELECT autorisasjonsgenerasjon FROM"
                            " domenekontroll_hendelse WHERE tenant=%s AND hostname=%s",
                            (TENANT, h)).fetchall()
    migrator.rollback()
    assert rows, "ingen hendelse ble logget"
    assert all(r[0] is not None for r in rows), \
        "en transisjonshendelse mangler autorisasjonsgenerasjon"
    assert any(r[0] == gen for r in rows), \
        "hendelsens generasjon matcher ikke domenekontroll-raden"


# ---------------- §0 kanonisk hostname ----------------

# Ikke-kanoniske former av ET DNS-navn + former som ikke er DNS-soner i det
# hele tatt. Hver av dem MÅ avvises, ellers finnes det mer enn én nøkkel for
# samme navn.
IKKE_KANONISKE = [
    "EXAMPLE.example",       # versaler — DNS er case-insensitivt, PG er ikke
    "Example.Example",       # blandet kasus
    "example.example.",      # avsluttende rot-punktum
    "eksempel.æøå",          # U-label (unicode) — må punycode-kodes til A-label
    " example.example",      # ledende blank
    "example.example ",      # etterfølgende blank
    "example..example",      # tom label
    "-example.example",      # bindestrek først i label
    "example-.example",      # bindestrek sist i label
    "example",               # ikke en FQDN (kun én label)
    "192.168.0.1",           # IP-literal — ingen DNS-sone å verifisere
    "",                      # tom
]


@pg
@pytest.mark.parametrize("dolent", IKKE_KANONISKE)
def test_ikke_kanonisk_hostname_avvises_av_funksjonene(migrator, dolent):
    """Codex P1: hver herdet §2-inngang krever kanonisk A-label FØR den låser
    eller skriver. Uten gjerdet ble `example.example` og `EXAMPLE.example` to
    ULIKE nøkler i PK-en, i `en_verifisert_per_hostname` og i
    `hostname_binding` — og advisory-låsen (avledet av hostnavnet) sprikte
    likedan, så de to verifiseringene serialiserte ikke engang mot hverandre.

    MUTASJONEN SOM DREPER DENNE: fjern `krev_kanonisk_hostname`-kallet fra
    en av §2-funksjonene."""
    a = _admin()
    try:
        for sql in ("SELECT utsted_challenge(%s,%s,false,'h','sys')",
                    "SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                    "SELECT revalider_domenekontroll(%s,%s,'sys')",
                    "SELECT tilbakekall_domenekontroll(%s,%s,'g','sys')"):
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                a.execute(sql, (TENANT, dolent))
            a.rollback()
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            a.execute("SELECT avgjor_domeneovertakelse(%s,%s,1,true,'sys')",
                      (TENANT, dolent))
        a.rollback()
    finally:
        a.close()


@pg
@pytest.mark.parametrize("dolent", IKKE_KANONISKE)
def test_ikke_kanonisk_hostname_avvises_ved_lagring(migrator, dolent):
    """Lagringsgjerdet: CHECK-en stenger også en PRIVILEGERT direkte INSERT
    utenom §2 — migrator eier tabellene og ville ellers kunnet plante den
    andre formen selv.

    MUTASJONEN SOM DREPER DENNE: drop CHECK-en på én av de tre tabellene."""
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute("INSERT INTO domenekontroll (tenant, hostname)"
                         " VALUES (%s,%s)", (TENANT, dolent))
    migrator.rollback()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute("INSERT INTO domenekontroll_hendelse (tenant, hostname,"
                         " hendelse, aktor) VALUES (%s,%s,'utstedt','sys')",
                         (TENANT, dolent))
    migrator.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute("INSERT INTO hostname_binding (hostname, tenant)"
                         " VALUES (%s,%s)", (dolent, TENANT))
    migrator.rollback()


@pg
def test_kanonisk_hostname_slipper_gjennom(migrator):
    """Gjerdet må ikke være for stramt: ordinære A-labels, punycode og lange
    label-kjeder er GYLDIGE og skal fortsatt kunne verifiseres."""
    u = secrets.token_hex(6)
    for h in ("a." + u + ".example",                  # ett-tegns label
              "xn--eksempel-cxa." + u + ".example",   # punycode A-label
              "sub.dyp.kjede." + u + ".example",      # dyp label-kjede
              "d1-2." + u + ".example"):              # siffer + intern bindestrek
        a = _admin()
        try:
            a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                      (TENANT, h))
            a.commit()
        finally:
            a.close()
        assert _dkrow(migrator, TENANT, h)[0] == "verifisert", \
            f"kanonisk hostname {h} ble avvist"


@pg
def test_to_tenanter_kan_ikke_dele_hostname_via_kasus(migrator):
    """PORTEN Codex fant: to tenanter `verifisert` for SAMME DNS-navn.

    Uten §0 ga `EXAMPLE`-formen en egen PK-rad, en egen delindeksrad og en
    egen `hostname_binding` — begge tenanter sto verifisert samtidig, og B4-
    overtakelsesadjudikeringen (som er hele poenget med `hostname_binding`)
    ble aldri utløst. Nå tvinges den andre tenanten inn i samme nøkkel og får
    `konflikt:` — altså den ordentlige M-37-veien."""
    h = _host()
    a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        # Samme navn, annen tekstlig form: MÅ avvises, ikke bli en parallell eier.
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                      (ANNEN_TENANT, h.upper()))
        a.rollback()
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                      (ANNEN_TENANT, h + "."))
        a.rollback()
        # Kanonisk form fra samme tenant → den ekte konfliktveien.
        res = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                        (ANNEN_TENANT, h)).fetchone()[0]
        a.commit()
    finally:
        a.close()
    assert res == "konflikt:" + TENANT, \
        "andre tenant kom forbi uten å utløse overtakelsesadjudikering"
    assert _binding(migrator, h.upper()) is None, \
        "en parallell binding ble opprettet for en ikke-kanonisk form"


@pg
def test_overtakelsessak_ignorerer_fremmed_idempotensnokkel(migrator):
    """Codex: `revisjonslogg.idempotency_key` er et DELT, KALLERSTYRT navnerom
    (`/v1/beslutning` skriver klientens Idempotency-Key rett inn) med kun en
    IKKE-unik indeks. En fremmed loggpost som alt heter
    `domeneovertakelse:<hostname>:<generasjon>` skal verken kapre eller
    ødelegge idempotensen: saken slås opp via `unntak` scopet til familien.

    MUTASJONEN SOM DREPER DENNE: slå opp loggposten først og let etter et
    hvilket som helst `unntak` på den."""
    from api.domeneovertakelse import (opprett_overtakelsessak,
                                       idempotensnokkel, FAMILIE)
    h = _host(); a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')", (TENANT, h))
        a.commit()
        res = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                        (ANNEN_TENANT, h)).fetchone()[0]
        a.commit()
    finally:
        a.close()
    tapt = res.split(":", 1)[1]
    gen = _dkrow(migrator, ANNEN_TENANT, h)[1]
    key = idempotensnokkel(h, gen)

    # En FREMMED loggpost med NØYAKTIG samme idempotensnøkkel, skrevet av en
    # annen kilde — nøyaktig det `/v1/beslutning` produserer.
    _sett_kontekst(migrator, ANNEN_TENANT)
    fremmed = int(migrator.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash, policy_id,"
        " beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'klient','beslutning','ih','pol','TILLAT','[]',%s)"
        " RETURNING id", (ANNEN_TENANT, key)).fetchone()[0])
    migrator.commit()

    _sett_kontekst(migrator, ANNEN_TENANT)
    uid1 = opprett_overtakelsessak(migrator, tenant_ny=ANNEN_TENANT, hostname=h,
                                   tenant_tapt=tapt, generasjon=gen, aktor="sys")
    migrator.commit()
    # Retry MÅ gi samme sak — den fremmede raden skal ikke sende oss i INSERT igjen.
    _sett_kontekst(migrator, ANNEN_TENANT)
    uid2 = opprett_overtakelsessak(migrator, tenant_ny=ANNEN_TENANT, hostname=h,
                                   tenant_tapt=tapt, generasjon=gen, aktor="sys")
    migrator.commit()
    assert uid2 == uid1, \
        "en fremmed loggpost med samme nøkkel brøt idempotensen (duplikat M-37-sak)"

    # ...og saken er VÅR — ikke hengt på den fremmede loggposten.
    _sett_kontekst(migrator, ANNEN_TENANT)
    rad = migrator.execute(
        "SELECT u.loggpost_id, u.kategori, r.kilde FROM unntak u"
        " JOIN revisjonslogg r ON r.tenant=u.tenant AND r.id=u.loggpost_id"
        " WHERE u.tenant=%s AND u.id=%s", (ANNEN_TENANT, uid1)).fetchone()
    n = migrator.execute(
        "SELECT count(*) FROM unntak WHERE tenant=%s AND kategori=%s",
        (ANNEN_TENANT, FAMILIE)).fetchone()[0]
    migrator.rollback()
    assert rad[0] != fremmed, "saken ble hengt på den fremmede loggposten"
    assert (rad[1], rad[2]) == (FAMILIE, FAMILIE)
    assert n == 1, f"{n} overtakelsessaker for én konflikt (skulle vært 1)"
