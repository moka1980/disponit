"""PR-014b CP5 (del 2): POST /v1/artefakt — opplastingsendepunktet."""
import secrets

import pytest

from .test_api import DSN, TENANT, migrator, miljo, token, klient, app  # noqa: F401
from .test_m37 import _sett_kontekst
from .test_pr014b_artefaktkapabilitet import _plukket_oppdrag_med_binding

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _utsted_cap(opp, modul, kh, at):
    from db.pg import koble
    jti = secrets.token_hex(16)
    c = koble(DSN)
    try:
        c.execute("SELECT jti FROM utsted_artefaktkapabilitet(%s,%s,%s,'r1',1,%s,"
                  "0,%s,%s,900)", (TENANT, opp, modul, kh, at, jti))
        c.commit()
    finally:
        c.close()
    return jti


def _post(klient, tok, jti, rapport):
    return klient.post("/v1/artefakt",
                       json={"kapabilitet_jti": jti, "rapport": rapport},
                       headers={"authorization": f"Bearer {tok}"})


@pg
def test_upload_ok_og_idempotent(migrator, klient, token):
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    jti = _utsted_cap(opp, modul, kh, at)
    tok, _ = token(rolle=modul, scopes=("artifacts:upload",))
    r = _post(klient, tok, jti, {"funn": 3, "sider": ["a"]})
    assert r.status_code == 200, r.text
    aid = r.json()["artefakt_id"]
    assert len(r.json()["klartekst_sha256"]) == 64
    # idempotent: samme jti + samme rapport → samme artefakt_id.
    r2 = _post(klient, tok, jti, {"sider": ["a"], "funn": 3})   # ulik nøkkelorden
    assert r2.status_code == 200 and r2.json()["artefakt_id"] == aid, \
        "JCS-kanonisering ga ikke samme id for samme dokument"
    _sett_kontekst(migrator, TENANT)
    st = migrator.execute("SELECT tilstand, nonce IS NOT NULL FROM artefakt"
                          " WHERE artefakt_id=%s", (aid,)).fetchone()
    migrator.rollback()
    assert st == ("staged", True)


@pg
def test_upload_konflikt_samme_jti_annet_dokument(migrator, klient, token):
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    jti = _utsted_cap(opp, modul, kh, at)
    tok, _ = token(rolle=modul, scopes=("artifacts:upload",))
    assert _post(klient, tok, jti, {"a": 1}).status_code == 200
    r = _post(klient, tok, jti, {"a": 2})   # samme jti, ANNET dokument
    assert r.status_code == 409, r.text


@pg
def test_upload_uten_scope_avvises(migrator, klient, token):
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    jti = _utsted_cap(opp, modul, kh, at)
    tok, _ = token(rolle=modul, scopes=("orders:execute:x.",))
    assert _post(klient, tok, jti, {"a": 1}).status_code == 403


@pg
def test_upload_ugyldig_kapabilitet(migrator, klient, token):
    modul = "m-" + secrets.token_hex(4)
    tok, _ = token(rolle=modul, scopes=("artifacts:upload",))
    r = _post(klient, tok, secrets.token_hex(16), {"a": 1})   # ukjent jti
    assert r.status_code == 401
