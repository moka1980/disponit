"""PR-012 gate 14a: avvis på sak med utestående oppdrag/kapabilitet.

Avvis er KUN trygt når HVER relatert rad positivt er trygg (intet, kansellert
oppdrag, terminal kapabilitet). Én levende rad → `avklaring_kreves` + 409,
ALDRI `avvist`. P3: gjentatt forsøk (ulike nøkler) mot SAMME utestående
tilstand gir samme 409 uten ny versjonsøkning eller historikkrad.
"""
import pytest

from api.unntaksbehandling import Godkjenningsfeil
from .test_api import DSN, KEK, MIGRATOR_DSN  # noqa: F401
from .test_pr012_behandle import (conn, _oppsett, _medlem, _macreg, _kall,  # noqa: F401,E501
                                  _status, _sv as _saksversjon, TEN)

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _oppdrag(uid, status="opprettet", gen=0, rep_status="aktiv"):
    """Full ekte kjede for ett oppdrag: reparasjonsoperasjon (64-hex id) →
    fase-2-TILLAT-beslutning (koblingsvakten) → KOBLET oppdrag. Returnerer
    repair_operation_id. Kansellering gjøres etterpå via en lovlig overgang."""
    import secrets
    from db.pg import koble, sett_kontekst
    m = koble(MIGRATOR_DSN)
    sett_kontekst(m, TEN, "sys", "r0")
    lid, key_id = m.execute("SELECT loggpost_id, key_id FROM unntak WHERE"
                            " tenant=%s AND id=%s", (TEN, uid)).fetchone()
    rop, ih = secrets.token_hex(32), secrets.token_hex(32)
    m.execute(
        "INSERT INTO reparasjonsoperasjoner (tenant,unntak_id,"
        "repair_operation_id,repair_generation,handler_id,handler_versjon,"
        "maalhandling,input_hash,kategori,status) VALUES (%s,%s,%s,%s,'h','v',"
        "'faktura.bokfor',%s,'over_grense',%s)",
        (TEN, uid, rop, gen, ih, rep_status))
    blid = m.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse,idempotency_key,kilde) VALUES (%s,'h','p','TILLAT',"
        "'[]'::jsonb,%s,'arbeidskapabilitet') RETURNING id",
        (TEN, rop)).fetchone()[0]
    m.execute(
        "INSERT INTO oppdrag (tenant,unntak_id,loggpost_id,repair_operation_id,"
        "oppdragstype,handling,eiermodul,status,payload_kryptert,key_id,nonce,"
        "utforelsesfrist,evidensfrist,koblingsstatus,beslutning_loggpost_id)"
        " VALUES (%s,%s,%s,%s,'reparasjon','faktura.bokfor','eier',%s,%s,%s,%s,"
        "now()+interval '1 hour',now()+interval '2 hour','KOBLET',%s)",
        (TEN, uid, lid, rop, status, b"\x00", key_id, b"\x00" * 12, blid))
    m.commit()
    m.close()
    return rop


def _kansellert_oppdrag(uid, gen=0):
    rop = _oppdrag(uid, "opprettet", gen=gen, rep_status="superseded")
    from db.pg import koble, sett_kontekst
    m = koble(MIGRATOR_DSN)
    sett_kontekst(m, TEN, "sys", "r0")
    m.execute("UPDATE oppdrag SET status='kansellert' WHERE tenant=%s AND"
              " unntak_id=%s AND repair_operation_id=%s", (TEN, uid, rop))
    m.commit()
    m.close()


def _historikk_teller(conn, uid, hendelse="avklaring_kreves"):
    from db.pg import sett_tenant
    sett_tenant(conn, TEN)
    n = conn.execute("SELECT count(*) FROM unntak_historikk WHERE tenant=%s AND"
                     " unntak_id=%s AND hendelse=%s",
                     (TEN, uid, hendelse)).fetchone()[0]
    conn.rollback()
    return n


def test_port8_ingen_oppdrag_kapabilitet_insert_uten_sakslas():
    """Port 8 (P2, statisk): ingen INSERT mot `oppdrag`/`arbeidskapabiliteter`
    skjer utenfor de GJENNOMGÅTTE, saks-låste veiene. Låsen er en `claim`
    (holdt hele transaksjonen), ikke en per-setnings FOR UPDATE — derfor en
    ALLOWLIST: en NY innsettingsvei tvinger en review av om den låser saken
    først. Det er dette som holder P2 sant over tid, også for fremtidige veier.
    """
    import re
    from pathlib import Path
    rot = Path(__file__).resolve().parents[1]     # platform/core
    mons = re.compile(
        r"INSERT\s+INTO\s+(?:public\.)?(oppdrag|arbeidskapabiliteter)\b", re.I)
    # Python: kun m37/arbeider.py — den claimer saken (claim_neste_sak låser)
    # FØR den oppretter oppdraget.
    py_tillatt = {("m37", "arbeider.py")}
    for py in rot.glob("**/*.py"):
        if py.name.startswith("test_") or "tests" in py.parts \
                or "node_modules" in py.parts:
            continue
        if mons.search(py.read_text(encoding="utf-8")):
            assert (py.parent.name, py.name) in py_tillatt, \
                f"oppdrag/kapabilitet-INSERT utenfor saks-låst vei: {py}"
    # SQL: kun de gjennomgåtte migrasjonsfunksjonene (utsted_arbeidskapabilitet),
    # som kalles etter en claim som holder saks­låsen.
    sql_tillatt = {"005_m37_behandling.sql", "007_r1_tofase.sql"}
    for sql in (rot / "db" / "migrations").glob("*.sql"):
        if mons.search(sql.read_text(encoding="utf-8")):
            assert sql.name in sql_tillatt, \
                f"oppdrag/kapabilitet-INSERT i uventet migrasjon: {sql.name}"


@pg
def test_port1_sak_uten_oppdrag_avvis_virker(conn):
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "avvist"
    assert _status(conn, uid) == "avvist"


@pg
def test_port2_kansellert_oppdrag_avvis_virker(conn):
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    _kansellert_oppdrag(uid)
    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "avvist"


@pg
def test_port3_levende_oppdrag_gir_409_og_avklaring(conn):
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    _oppdrag(uid, "opprettet")
    sv0 = _saksversjon(conn, uid)
    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "utestaaende_oppdrag" and res["http"] == 409
    # avklaring_kreves committet, saksversjon økt, saken IKKE avvist.
    assert _status(conn, uid) != "avvist"
    assert _saksversjon(conn, uid) == sv0 + 1
    assert _historikk_teller(conn, uid) == 1


def _kapabilitet(uid, status="utstedt"):
    """Én utestående arbeidskapabilitet for saken (via migrator)."""
    import secrets
    from db.pg import koble, sett_kontekst
    m = koble(MIGRATOR_DSN)
    sett_kontekst(m, TEN, "sys", "r0")
    # Kapabilitetens utløp må ligge innenfor sakens claim_utloper (GO-vilkår V1).
    cid = secrets.token_hex(16)
    m.execute("UPDATE unntak SET claim_id=%s, claim_utloper=now()+interval"
              " '2 hour' WHERE tenant=%s AND id=%s", (cid, TEN, uid))
    # arbeidskapabiliteter eies av m37_claimer; migrator er medlem og kan SET
    # ROLE for innsettingen (som i drift går via utsted_arbeidskapabilitet).
    m.execute("SET ROLE disponit_m37_claimer")
    m.execute(
        "INSERT INTO arbeidskapabiliteter (jti,tenant,unntak_id,claim_id,"
        "claim_generation,repair_operation_id,tillatt_handling,status,utloper)"
        " VALUES (%s,%s,%s,%s,0,%s,'faktura.bokfor',%s,now()+interval '1 hour')",
        (secrets.token_hex(16), TEN, uid, cid, secrets.token_hex(32), status))
    m.execute("RESET ROLE")
    m.commit()
    m.close()


@pg
def test_port6_utestaaende_kapabilitet_uten_oppdrag_gir_409(conn):
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    _kapabilitet(uid, "utstedt")           # utestående kapabilitet, ingen oppdrag
    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "utestaaende_oppdrag"


@pg
def test_port5_baade_kansellert_og_levende_gir_409(conn):
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    _kansellert_oppdrag(uid, gen=0)
    _oppdrag(uid, "plukket", gen=1)   # én levende blant kansellerte
    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "utestaaende_oppdrag"


@pg
def test_port9_gjentatt_ulik_noekkel_samme_409_ingen_ny_versjon_eller_historikk(conn):
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    _oppdrag(uid, "opprettet")
    r1 = _kall(conn, uid, "avvis", bid, _macreg(), idem=f"a-{uid}")
    assert r1["utfall"] == "utestaaende_oppdrag"
    sv1 = _saksversjon(conn, uid)
    # Nytt forsøk, ANNEN nøkkel, SAMME utestående tilstand → samme 409, men
    # ingen ny versjonsøkning og ingen ny historikkrad (P3).
    r2 = _kall(conn, uid, "avvis", bid, _macreg(), saksversjon=sv1,
               idem=f"b-{uid}")
    assert r2["utfall"] == "utestaaende_oppdrag"
    assert _saksversjon(conn, uid) == sv1          # ingen ny versjonsøkning
    assert _historikk_teller(conn, uid) == 1       # ingen ny historikkrad
