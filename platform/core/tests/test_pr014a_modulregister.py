"""PR-014a CP1: migrasjon 014 — modulregister-datamodell + integritetstriggere.

DB-en håndhever kontrakten, ikke koden: `modulkontrakt`/`modulrelease` er
immutable, `moduldeployment` har ÉN `claiming` per (modul, miljø, kontrakt),
og status-/livsløps-statemaskinene avviser ulovlige overganger. Hver invariant
muteres bort av en operasjon som MÅ feile.
"""
import secrets

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
H = "h-" + secrets.token_hex(8)


def _c():
    from db.pg import koble
    return koble(MIGRATOR_DSN)


def _rt():
    from db.pg import koble
    return koble(DSN)


def _mid():
    return "m-" + secrets.token_hex(4)


def _kontrakt(c, modul, ver=1, khash=None):
    khash = khash or ("k-" + secrets.token_hex(8))
    c.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,%s,%s,'p','k','krever_outbox',"
        "'kompenserende')", (modul, ver, khash))
    return khash


def _hode(c, modul, status="installert"):
    c.execute("INSERT INTO modulhode (modul_id,status) VALUES (%s,%s)",
              (modul, status))


def _release(c, modul, rel, ver, khash):
    c.execute(
        "INSERT INTO modulrelease (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,manifest_hash,artifact_digest) VALUES"
        " (%s,%s,%s,%s,'mh','ad')", (modul, rel, ver, khash))


def _deployment(c, modul, rel, ver, khash, miljo="staging", livslop="claiming"):
    c.execute(
        "INSERT INTO moduldeployment (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,miljo,livslop) VALUES (%s,%s,%s,%s,%s,%s)",
        (modul, rel, ver, khash, miljo, livslop))


@pg
def test_runtime_leser_men_skriver_ikke_registeret():
    # Codex-port 17: runtime har KUN SELECT — alle fem registertabellene nekter
    # direkte skriving (all mutasjon går via de herdede funksjonene, CP2).
    r = _rt()
    try:
        r.execute("SELECT count(*) FROM modulkontrakt").fetchone()   # SELECT OK
        r.execute("SELECT count(*) FROM modulhode").fetchone()
        skriv = [
            "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
            "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
            "reversibilitet) VALUES ('x',1,'h','p','k','sideeffektfri','direkte')",
            "INSERT INTO modulhode (modul_id) VALUES ('x')",
            "INSERT INTO modulrelease (modul_id,release_id,kontraktversjon,"
            "kontrakt_hash,manifest_hash,artifact_digest) VALUES"
            " ('x','r',1,'h','m','a')",
            "INSERT INTO moduldeployment (modul_id,release_id,kontraktversjon,"
            "kontrakt_hash,miljo,livslop) VALUES ('x','r',1,'h','staging','claiming')",
            "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
            "kontraktversjon,kontrakt_hash) VALUES ('t','x',1,'h')",
        ]
        for sql in skriv:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                r.execute(sql)
            r.rollback()
    finally:
        r.close()


@pg
def test_kontrakt_immutable():
    c = _c(); m = _mid()
    try:
        kh = _kontrakt(c, m); c.commit()
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE modulkontrakt SET kontrakt_hash='x' WHERE"
                      " modul_id=%s", (m,))
        c.rollback()
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("DELETE FROM modulkontrakt WHERE modul_id=%s", (m,))
        c.rollback()
    finally:
        c.close()


@pg
def test_release_immutable_og_fk():
    c = _c(); m = _mid()
    try:
        kh = _kontrakt(c, m)
        # release mot en kontrakt som ikke finnes → FK avviser.
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _release(c, m, "r1", 1, "finnes-ikke")
        c.rollback()
        _kontrakt(c, m) if False else None
        kh = _kontrakt(c, m + "b")
        _release(c, m + "b", "r1", 1, kh); c.commit()
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE modulrelease SET manifest_hash='x' WHERE"
                      " modul_id=%s", (m + "b",))
        c.rollback()
    finally:
        c.close()


@pg
def test_deployment_fk_krever_release_kontrakt():
    c = _c(); m = _mid()
    try:
        kh = _kontrakt(c, m); _release(c, m, "r1", 1, kh); c.commit()
        # deployment med kontrakt som avviker fra releasen → FK avviser.
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _deployment(c, m, "r1", 1, "annen-hash")
        c.rollback()
        _deployment(c, m, "r1", 1, kh); c.commit()   # riktig → OK
    finally:
        c.close()


@pg
def test_en_claiming_per_kontrakt():
    c = _c(); m = _mid()
    try:
        kh = _kontrakt(c, m)
        _release(c, m, "r1", 1, kh); _release(c, m, "r2", 1, kh)
        _deployment(c, m, "r1", 1, kh, livslop="claiming"); c.commit()
        # to claiming for samme (modul, miljø, kontrakt) → delindeks avviser.
        with pytest.raises(psycopg.errors.UniqueViolation):
            _deployment(c, m, "r2", 1, kh, livslop="claiming")
        c.rollback()
        # draining ved siden av claiming er OK (mange tillatt).
        _deployment(c, m, "r2", 1, kh, livslop="draining"); c.commit()
    finally:
        c.close()


@pg
def test_hode_statemaskin_avviser_hopp():
    c = _c(); m = _mid()
    try:
        _hode(c, m, "installert"); c.commit()
        # installert → aktiv (hopper over staging_verifisert) er ulovlig.
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE modulhode SET status='aktiv' WHERE modul_id=%s",
                      (m,))
        c.rollback()
        # lovlig fremover.
        c.execute("UPDATE modulhode SET status='staging_verifisert' WHERE"
                  " modul_id=%s", (m,)); c.commit()
        # epoch kan ikke gå bakover.
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE modulhode SET module_epoch=-1 WHERE modul_id=%s",
                      (m,))
        c.rollback()
    finally:
        c.close()


@pg
def test_deployment_livslop_fremover_og_frosset():
    c = _c(); m = _mid()
    try:
        kh = _kontrakt(c, m); _release(c, m, "r1", 1, kh)
        _deployment(c, m, "r1", 1, kh, livslop="claiming"); c.commit()
        # kontraktidentitet frosset.
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE moduldeployment SET kontrakt_hash='x' WHERE"
                      " modul_id=%s AND release_id='r1'", (m,))
        c.rollback()
        # claiming → retired (hopper over draining) er ulovlig.
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE moduldeployment SET livslop='retired' WHERE"
                      " modul_id=%s AND release_id='r1'", (m,))
        c.rollback()
        # lovlig: claiming → draining → retired.
        c.execute("UPDATE moduldeployment SET livslop='draining' WHERE"
                  " modul_id=%s AND release_id='r1'", (m,))
        c.execute("UPDATE moduldeployment SET livslop='retired' WHERE"
                  " modul_id=%s AND release_id='r1'", (m,)); c.commit()
    finally:
        c.close()


# ---- CP2: herdede overgangsfunksjoner ----

def _admin():
    from db.pg import koble
    c = koble(MIGRATOR_DSN)
    c.execute("SET ROLE disponit_modules_admin")   # EXECUTE på overgangsfunksjonene
    c.commit()   # gjør rolleendringen varig — ellers ruller en senere rollback
                 # (f.eks. etter en forventet FK-feil) den tilbake til migrator
    return c


@pg
def test_registrer_oppdragstype_global_og_prefiks_overlapp():
    # Codex-port 18-familien: globalt unik, prefiks-overlapp avvist (dispatch-
    # kollisjon). FK binder oppdragstypen til en eksisterende kontrakt.
    # oppdragstype er GLOBAL og committes — kjør-unike navn holder prefiks-
    # relasjonen intakt uten å kollidere med tidligere kjøringer.
    tok = secrets.token_hex(4)
    full = f"audit{tok}.revider"; pref = f"audit{tok}"; annen = f"ordre{tok}"
    c = _c(); m = _mid(); kh = _kontrakt(c, m); c.commit(); c.close()
    a = _admin()
    try:
        a.execute("SELECT registrer_oppdragstype(%s,%s,1,%s,'sys')",
                  (full, m, kh)); a.commit()
        # 'auditXXXX' er prefiks av 'auditXXXX.revider' → overlapp avvist.
        with pytest.raises(psycopg.errors.UniqueViolation):
            a.execute("SELECT registrer_oppdragstype(%s,%s,1,%s,'sys')",
                      (pref, m, kh))
        a.rollback()
        # divergerende type er OK.
        a.execute("SELECT registrer_oppdragstype(%s,%s,1,%s,'sys')",
                  (annen, m, kh)); a.commit()
    finally:
        a.close()


@pg
def test_sett_modulstatus_statemaskin_og_aktiv_krever_claiming():
    # Codex-port 13: 'aktiv' uten claiming-deployment avvises av funksjonen.
    c = _c(); m = _mid(); kh = _kontrakt(c, m); _release(c, m, "r1", 1, kh)
    c.commit(); c.close()
    a = _admin()
    try:
        a.execute("SELECT installer_modul(%s,'sys')", (m,)); a.commit()
        a.execute("SELECT sett_modulstatus(%s,'staging_verifisert',NULL,'sys')",
                  (m,)); a.commit()
        # aktiv uten claiming → avvist.
        with pytest.raises(psycopg.errors.Error):
            a.execute("SELECT sett_modulstatus(%s,'aktiv',NULL,'sys')", (m,))
        a.rollback()
    finally:
        a.close()
    # legg til en claiming-deployment (migrator eier tabellen) → aktiv OK.
    c = _c(); _deployment(c, m, "r1", 1, kh, livslop="claiming"); c.commit(); c.close()
    a = _admin()
    try:
        a.execute("SELECT sett_modulstatus(%s,'aktiv',%s,'sys')", (m, "r1"))
        a.commit()
    finally:
        a.close()
    # modules_admin har KUN EXECUTE — les statusen via runtime (SELECT).
    r = _rt()
    try:
        s = r.execute("SELECT status FROM modulhode WHERE modul_id=%s",
                      (m,)).fetchone()[0]
        assert s == "aktiv"
    finally:
        r.close()


@pg
def test_runtime_kan_ikke_kalle_overgangsfunksjoner():
    # Runtime har ikke EXECUTE på overgangsfunksjonene (kun modules_admin).
    r = _rt()
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            r.execute("SELECT sett_modulstatus('x','aktiv',NULL,'sys')")
        r.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            r.execute("SELECT registrer_oppdragstype('t','x',1,'h','sys')")
        r.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            r.execute("SELECT installer_modul('x','sys')")
        r.rollback()
    finally:
        r.close()


# ---- CP3: release-livsløp (registrer_kontrakt/release, bytt_release, pensjoner) ----

def _livslop(modul, rel, miljo="staging"):
    """Leser livsløp via runtime (SELECT) — modules_admin har ikke tabelltilgang."""
    r = _rt()
    try:
        rad = r.execute("SELECT livslop FROM moduldeployment WHERE modul_id=%s"
                        " AND miljo=%s AND release_id=%s", (modul, miljo, rel)
                        ).fetchone()
        return rad[0] if rad else None
    finally:
        r.close()


def _reg_kontrakt(a, modul, kh, ver=1):
    a.execute("SELECT registrer_kontrakt(%s,%s,%s,'p','k','krever_outbox',"
              "'kompenserende','sys')", (modul, ver, kh))


def _reg_release(a, modul, rel, kh, ver=1):
    a.execute("SELECT registrer_release(%s,%s,%s,%s,'mh','ad','sys')",
              (modul, rel, ver, kh))


@pg
def test_registrer_kontrakt_idempotent_og_immutable():
    m = _mid(); kh = "k-" + secrets.token_hex(8)
    a = _admin()
    try:
        _reg_kontrakt(a, m, kh); a.commit()
        _reg_kontrakt(a, m, kh); a.commit()          # samme hash → no-op
        # samme (modul, versjon), avvikende hash → immutable, avvist.
        with pytest.raises(psycopg.errors.UniqueViolation):
            _reg_kontrakt(a, m, "k-annen")
        a.rollback()
    finally:
        a.close()


@pg
def test_registrer_release_fk_og_immutable():
    m = _mid(); kh = "k-" + secrets.token_hex(8)
    a = _admin()
    try:
        # release før kontrakten finnes → FK avviser (port 1).
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _reg_release(a, m, "r1", kh)
        a.rollback()
        _reg_kontrakt(a, m, kh); _reg_release(a, m, "r1", kh); a.commit()
        _reg_release(a, m, "r1", kh); a.commit()      # samme innhold → no-op
        # samme release_id, avvikende innhold → immutable.
        with pytest.raises(psycopg.errors.UniqueViolation):
            a.execute("SELECT registrer_release(%s,'r1',1,%s,'ANNEN','ad','sys')",
                      (m, kh))
        a.rollback()
    finally:
        a.close()


@pg
def test_bytt_release_atomisk_og_ingen_reclaim():
    m = _mid(); kh = "k-" + secrets.token_hex(8)
    a = _admin()
    try:
        _reg_kontrakt(a, m, kh)
        _reg_release(a, m, "r1", kh); _reg_release(a, m, "r2", kh); a.commit()
        # første bytte: r1 blir claiming.
        a.execute("SELECT bytt_release(%s,'staging','r1',1,%s,'sys')", (m, kh))
        a.commit()
        assert _livslop(m, "r1") == "claiming"
        # bytte til r2: r1 → draining, r2 → claiming, ATOMISK (nøyaktig én claiming).
        a.execute("SELECT bytt_release(%s,'staging','r2',1,%s,'sys')", (m, kh))
        a.commit()
        assert _livslop(m, "r1") == "draining"
        assert _livslop(m, "r2") == "claiming"
        # idempotent: bytte til r2 igjen → no-op.
        a.execute("SELECT bytt_release(%s,'staging','r2',1,%s,'sys')", (m, kh))
        a.commit()
        assert _livslop(m, "r2") == "claiming"
        # r1 er draining → kan ALDRI reclaimes.
        with pytest.raises(psycopg.errors.Error):
            a.execute("SELECT bytt_release(%s,'staging','r1',1,%s,'sys')", (m, kh))
        a.rollback()
    finally:
        a.close()
    # nøyaktig én claiming for kontrakten (invarianten, målt via runtime).
    r = _rt()
    try:
        n = r.execute("SELECT count(*) FROM moduldeployment WHERE modul_id=%s"
                      " AND kontrakt_hash=%s AND livslop='claiming'", (m, kh)
                      ).fetchone()[0]
        assert n == 1
    finally:
        r.close()


@pg
def test_pensjoner_release_idempotent_og_claiming_avvist():
    m = _mid(); kh = "k-" + secrets.token_hex(8)
    a = _admin()
    try:
        _reg_kontrakt(a, m, kh)
        _reg_release(a, m, "r1", kh); _reg_release(a, m, "r2", kh); a.commit()
        a.execute("SELECT bytt_release(%s,'staging','r1',1,%s,'sys')", (m, kh))
        a.execute("SELECT bytt_release(%s,'staging','r2',1,%s,'sys')", (m, kh))
        a.commit()                                    # r1 draining, r2 claiming
        # claiming kan ikke pensjoneres direkte.
        with pytest.raises(psycopg.errors.Error):
            a.execute("SELECT pensjoner_release(%s,'staging','r2','sys')", (m,))
        a.rollback()
        # draining → retired.
        a.execute("SELECT pensjoner_release(%s,'staging','r1','sys')", (m,))
        a.commit()
        assert _livslop(m, "r1") == "retired"
        # idempotent: pensjoner igjen → no-op.
        a.execute("SELECT pensjoner_release(%s,'staging','r1','sys')", (m,))
        a.commit()
        assert _livslop(m, "r1") == "retired"
    finally:
        a.close()


@pg
def test_runtime_kan_ikke_kalle_cp3_funksjoner():
    r = _rt()
    try:
        for sql in (
            "SELECT registrer_kontrakt('x',1,'h','p','k','sideeffektfri',"
            "'direkte','sys')",
            "SELECT registrer_release('x','r',1,'h','m','a','sys')",
            "SELECT bytt_release('x','staging','r',1,'h','sys')",
            "SELECT pensjoner_release('x','staging','r','sys')",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                r.execute(sql)
            r.rollback()
    finally:
        r.close()
