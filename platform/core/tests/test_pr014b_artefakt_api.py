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


def _oppdrag_owner(migrator, opp):
    _sett_kontekst(migrator, TENANT)
    r = migrator.execute("SELECT owner_claim_id, repair_operation_id FROM oppdrag"
                         " WHERE tenant=%s AND id=%s", (TENANT, opp)).fetchone()
    migrator.rollback()
    return r


def _kvitteringskap(opp, owner_claim):
    from db.pg import koble
    jti = secrets.token_hex(16)
    c = koble(DSN)
    try:
        c.execute("SELECT jti FROM utsted_kvitteringskapabilitet(%s,%s,0,%s)",
                  (opp, owner_claim, jti))
        c.commit()
    finally:
        c.close()
    return jti


def _last_opp_artefakt(migrator, klient, token):
    """Bygg bundet, plukket oppdrag + last opp et staged artefakt. Returnerer
    (opp, modul, kh, artefakt_id, owner_claim, repair_operation_id)."""
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    oc, rep = _oppdrag_owner(migrator, opp)
    ajti = _utsted_cap(opp, modul, kh, at)
    tok, _ = token(rolle=modul, scopes=("artifacts:upload",))
    aid = _post(klient, tok, ajti, {"funn": 1}).json()["artefakt_id"]
    return opp, modul, kh, aid, oc, rep


@pg
def test_kvittering_promoterer_artefakt(migrator, klient, token):
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep = _last_opp_artefakt(migrator, klient, token)
    kjti = _kvitteringskap(opp, oc)
    kv = _signer_kvittering({
        "oppdrag_id": opp, "tenant": TENANT, "kvittering_jti": kjti,
        "repair_operation_id": rep, "owner_claim_id": oc, "owner_generation": 0,
        "resultat": "utfort", "ressurs_id": "fak-1", "artefakt_id": aid})
    tok2, _ = token(rolle="eiermodul:reinnsending",
                    scopes=("orders:execute:purring.",))
    rk = klient.post("/v1/oppdrag/kvittering", json=kv,
                     headers={"authorization": f"Bearer {tok2}"})
    assert rk.status_code == 200 and rk.json()["status"] == "utfort", rk.text
    _sett_kontekst(migrator, TENANT)
    st = migrator.execute("SELECT tilstand FROM artefakt WHERE artefakt_id=%s",
                          (aid,)).fetchone()[0]
    migrator.rollback()
    assert st == "promotert", "artefaktet ble ikke promotert av kvitteringen"


@pg
def test_kvittering_med_epoch_drift_karantenesetter(migrator, klient, token):
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep = _last_opp_artefakt(migrator, klient, token)
    # epoch-drift: oppdragets epoch flyttes forbi artefaktets (0).
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE oppdrag SET module_epoch=5 WHERE tenant=%s AND id=%s",
                     (TENANT, opp)); migrator.commit()
    kjti = _kvitteringskap(opp, oc)
    kv = _signer_kvittering({
        "oppdrag_id": opp, "tenant": TENANT, "kvittering_jti": kjti,
        "repair_operation_id": rep, "owner_claim_id": oc, "owner_generation": 0,
        "resultat": "utfort", "ressurs_id": "fak-1", "artefakt_id": aid})
    tok2, _ = token(rolle="eiermodul:reinnsending",
                    scopes=("orders:execute:purring.",))
    rk = klient.post("/v1/oppdrag/kvittering", json=kv,
                     headers={"authorization": f"Bearer {tok2}"})
    assert rk.status_code == 409, rk.text   # ikke godtatt
    _sett_kontekst(migrator, TENANT)
    row = migrator.execute("SELECT a.tilstand, o.status FROM artefakt a JOIN oppdrag o"
                           " ON o.tenant=a.tenant AND o.id=a.oppdrag_id"
                           " WHERE a.artefakt_id=%s", (aid,)).fetchone()
    migrator.rollback()
    assert row == ("staged", "plukket"), \
        "epoch-drift skulle karantenesatt (artefakt bevart, oppdrag ikke avsluttet)"
