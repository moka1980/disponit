"""En FEILET kvittering på en KOMPENSERENDE kontrakt gir en sak for
oppdraget (M-57s SP-3-punkt: «leverte en post i unntakskøen»).

En bunt som aldri ble evaluert har en kunde som venter, og bestillingen
sto `feilet` uten at noe menneske så den. Nå knyttes en `sak_for_oppdrag`
(årsak utforelse_feilet, snapshot 0 — aldri automatikk) til oppdraget i
M-37s kø. Direkte kontrakter er uendret (negativ kontroll), og en
kontraktløs legacy-rad får heller ingen sak.

Riggen er cp5s: en REGISTRERT oppdragstype med eiermodul, kontrakt
(`registrer_kontrakt`, reversibilitet som parameter) og claiming-
deployment; claimet gjøres som runtime med deployment-identitet (som
claim-endepunktet), kvitteringskapabiliteten utstedes deploymentløs (som
for et legacy-token), og kvitteringen postes over HTTP.

MUTASJONEN SOM DREPER DENNE: fjern `sikre_sak_for_oppdrag`-kallet i
`not vellykket`-grenen i kvitteringsendepunktet.
"""
from __future__ import annotations

import secrets

from .test_api import DSN, TENANT, migrator, miljo, token  # noqa: F401
from .test_m37 import _lag_sak, _sett_kontekst, _signer_kvittering, pg  # noqa: F401
from .test_pr014a_cp5_claim import _lag_oppdrag_type
from .test_pr014a_modulregister import _admin, _reg_release


def _rigg(migrator, reversibilitet: str):
    """-> (modul, oppdragstype, oppdrag_id)."""
    t = secrets.token_hex(4)
    modul, ot, kh = f"komp-{t}", f"komp-{t}", "k-" + t
    a = _admin()
    try:
        a.execute("SELECT registrer_kontrakt(%s,1,%s,'p','k','krever_outbox',"
                  "%s,'sys')", (modul, kh, reversibilitet))
        a.execute("SELECT registrer_oppdragstype(%s,%s,1,%s,'sys')", (ot, modul, kh))
        _reg_release(a, modul, "r1", kh)
        a.execute("SELECT installer_modul(%s,'sys')", (modul,))
        a.execute("SELECT sett_modulstatus(%s,'staging_verifisert',NULL,'sys')",
                  (modul,))
        a.execute("SELECT bytt_release(%s,'staging','r1',1,%s,'sys')", (modul, kh))
        a.execute("SELECT sett_modulstatus(%s,'aktiv','r1','sys')", (modul,))
        a.commit()
    finally:
        a.close()
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag_type(migrator, TENANT, sak, logg,
                               oppdragstype=ot, eiermodul=modul)
    return modul, ot, opp


def _claim_med_kapabilitet(modul, ot, opp):
    """Claimet som claim-endepunktet gjør det (runtime, deployment-
    identitet), og kapabiliteten deploymentløs — innløsbar av et
    legacy-API-token."""
    from db.pg import koble
    cid = secrets.token_hex(16)
    jti = secrets.token_hex(16)
    c = koble(DSN)
    try:
        c.execute("SELECT set_config('disponit.aktor',%s,true),"
                  "       set_config('disponit.request_id','r',true)", (modul,))
        rad = c.execute(
            "SELECT id, owner_generation, repair_operation_id FROM"
            " claim_neste_oppdrag(%s,%s,%s,%s,%s,%s,%s)",
            (modul, ["purring."], cid, 300, "r1", "staging", 0)).fetchone()
        assert rad is not None and rad[0] == opp, "claimet ikke oppdraget"
        c.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
        kap = c.execute(
            "SELECT jti FROM utsted_kvitteringskapabilitet(%s,%s,%s,%s,%s,%s)",
            (opp, cid, rad[1], jti, None, None)).fetchone()
        c.commit()
        assert kap is not None
        return {"kvittering_jti": jti, "owner_claim_id": cid,
                "owner_generation": rad[1], "repair_operation_id": rad[2]}
    finally:
        c.close()


def _kvittering(opp: int, kap: dict, resultat: str) -> dict:
    return _signer_kvittering({
        "oppdrag_id": opp, "tenant": TENANT,
        "kvittering_jti": kap["kvittering_jti"],
        "repair_operation_id": kap["repair_operation_id"],
        "owner_claim_id": kap["owner_claim_id"],
        "owner_generation": kap["owner_generation"],
        "resultat": resultat, "ressurs_id": "fak-1"})


def _saker(migrator, opp: int):
    _sett_kontekst(migrator, TENANT)
    rader = migrator.execute(
        "SELECT sakskilde, arsak, status, maks_auto_forsok_snapshot FROM unntak"
        " WHERE tenant=%s AND oppdrag_id=%s ORDER BY id", (TENANT, opp)).fetchall()
    migrator.rollback()
    return rader


def _kjor(migrator, token, reversibilitet: str, resultat: str) -> int:
    from starlette.testclient import TestClient
    from api.app import lag_app
    modul, ot, opp = _rigg(migrator, reversibilitet)
    kap = _claim_med_kapabilitet(modul, ot, opp)
    app = lag_app(DSN)
    with TestClient(app) as c:
        tok, _ = token(rolle=modul, scopes=("orders:execute:purring.",))
        h = {"authorization": f"Bearer {tok}"}
        r = c.post("/v1/oppdrag/kvittering", json=_kvittering(opp, kap, resultat),
                   headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == resultat, r.text
        # samme kvittering én gang til: idempotent, og INGEN ny sak
        r2 = c.post("/v1/oppdrag/kvittering", json=_kvittering(opp, kap, resultat),
                    headers=h)
        assert r2.status_code == 200, r2.text
    return opp


@pg
def test_feilet_kvittering_paa_kompenserende_kontrakt_gir_sak_for_oppdraget(
        migrator, miljo, token):
    opp = _kjor(migrator, token, "kompenserende", "feilet")
    assert _saker(migrator, opp) == [("oppdrag", "utforelse_feilet", "ny", 0)]


@pg
def test_utfort_kvittering_gir_ingen_sak(migrator, miljo, token):
    opp = _kjor(migrator, token, "kompenserende", "utfort")
    assert _saker(migrator, opp) == []


@pg
def test_feilet_paa_direkte_kontrakt_gir_ingen_sak(migrator, miljo, token):
    opp = _kjor(migrator, token, "direkte", "feilet")
    assert _saker(migrator, opp) == []
