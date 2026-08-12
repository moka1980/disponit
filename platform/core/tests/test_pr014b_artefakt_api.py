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
    r = migrator.execute("SELECT owner_claim_id, repair_operation_id,"
                         " owner_generation FROM oppdrag"
                         " WHERE tenant=%s AND id=%s", (TENANT, opp)).fetchone()
    migrator.rollback()
    return r


def _kvitteringskap(opp, owner_claim, gen):
    from db.pg import koble
    jti = secrets.token_hex(16)
    c = koble(DSN)
    try:
        c.execute("SELECT jti FROM utsted_kvitteringskapabilitet(%s,%s,%s,%s)",
                  (opp, owner_claim, gen, jti))
        c.commit()
    finally:
        c.close()
    return jti


def _last_opp_artefakt(migrator, klient, token):
    """Bygg bundet, plukket oppdrag + last opp et staged artefakt. Returnerer
    (opp, modul, kh, artefakt_id, owner_claim, repair_operation_id, gen)."""
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    oc, rep, gen = _oppdrag_owner(migrator, opp)
    ajti = _utsted_cap(opp, modul, kh, at)
    tok, _ = token(rolle=modul, scopes=("artifacts:upload",))
    aid = _post(klient, tok, ajti, {"funn": 1}).json()["artefakt_id"]
    return opp, modul, kh, aid, oc, rep, gen


def _kvitteringskropp(opp, kjti, rep, oc, gen, aid):
    return {"oppdrag_id": opp, "tenant": TENANT, "kvittering_jti": kjti,
            "repair_operation_id": rep, "owner_claim_id": oc,
            "owner_generation": gen, "resultat": "utfort",
            "ressurs_id": "fak-1", "artefakt_id": aid}


@pg
def test_kvittering_promoterer_artefakt(migrator, klient, token):
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep, gen = _last_opp_artefakt(migrator, klient, token)
    kjti = _kvitteringskap(opp, oc, gen)
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid))
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    rk = klient.post("/v1/oppdrag/kvittering", json=kv,
                     headers={"authorization": f"Bearer {tok2}"})
    assert rk.status_code == 200 and rk.json()["status"] == "utfort", rk.text
    _sett_kontekst(migrator, TENANT)
    st = migrator.execute("SELECT tilstand FROM artefakt WHERE artefakt_id=%s",
                          (aid,)).fetchone()[0]
    migrator.rollback()
    assert st == "promotert", "artefaktet ble ikke promotert av kvitteringen"


@pg
def test_kvittering_med_fremmed_artefakt_karantenesetter(migrator, klient, token):
    # §7 pkt. 8: refererer kvitteringen et artefakt som IKKE er bundet til dette
    # oppdraget, promoteres ingenting, oppdraget avsluttes ikke, og det
    # karantenesettes (409). Bindingsavviket måles UTEN å røre epoch direkte
    # (det ville krevd claim-funksjonen).
    from .test_m37 import _signer_kvittering
    opp, modul, kh, aid, oc, rep, gen = _last_opp_artefakt(migrator, klient, token)
    # et ANNET oppdrag + artefakt (aid2 hører til opp2, ikke opp).
    _, _, _, aid2, _, _, _ = _last_opp_artefakt(migrator, klient, token)
    kjti = _kvitteringskap(opp, oc, gen)
    kv = _signer_kvittering(_kvitteringskropp(opp, kjti, rep, oc, gen, aid2))
    tok2, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
    rk = klient.post("/v1/oppdrag/kvittering", json=kv,
                     headers={"authorization": f"Bearer {tok2}"})
    assert rk.status_code == 409, rk.text
    _sett_kontekst(migrator, TENANT)
    art_st = migrator.execute("SELECT tilstand FROM artefakt WHERE artefakt_id=%s",
                              (aid2,)).fetchone()[0]
    opp_st = migrator.execute("SELECT status FROM oppdrag WHERE tenant=%s AND id=%s",
                              (TENANT, opp)).fetchone()[0]
    migrator.rollback()
    assert (art_st, opp_st) == ("staged", "plukket"), \
        "bindingsavvik skulle karantenesatt (fremmed artefakt bevart, ikke avsluttet)"
