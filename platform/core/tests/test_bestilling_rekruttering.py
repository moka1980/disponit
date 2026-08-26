"""#162 PR-3: evalueringsbestillingen — referanser inn, binding i
oppdragets fødselstransaksjon.

Hele kjeden over HTTP: kunde laster opp bunt (PR-1-veien), har en
stillingsprofil (#189/061), og POST /v1/bestilling med
`rekruttering.evaluering` + de to referansene gir TILLAT → oppdrag —
med bunten BUNDET i samme transaksjon (X1) og payloaden i B-form
(#200): profil-øyeblikksbilde bygget server-side, ingen
soknadsbunt_ref.
"""
import json
import secrets
import uuid

import psycopg
import pytest
import yaml as _yaml

from .test_api import (DSN, MIGRATOR_DSN, POLICIES, TENANT, app,  # noqa: F401
                       klient, migrator, miljo)
from .test_inndata_http import inndata_rot  # noqa: F401
from .test_m37 import _sett_kontekst
from .test_api import dekker
from .test_outbox_bestilling import _adminsesjon

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _rekr_policy(migrator_):
    """Aktiv policy med rekrutteringshandlingen — bransjemalen +
    `rekruttering.evaluering` (modus auto, persondata tillatt: det er
    CV-er, og klassen skal være et EKSPLISITT policyvalg)."""
    from api import policyregister
    p = _yaml.safe_load(
        (POLICIES / "bransjemal-tjenestebedrift.yaml")
        .read_text(encoding="utf-8"))
    p["roller"].append({"id": "bestiller",
                        "beskrivelse": "Bestiller evalueringer"})
    p["handlinger"].append({
        "id": "rekruttering.evaluering", "modul": "M-57",
        "modus": "auto", "ved_brudd": "unntakskø",
        "tillatt_for": ["bestiller"],
        "dataklasser_tillatt": ["persondata"],
        "reversering": {"type": "direkte"}})
    policyregister.registrer(migrator_, TENANT, p, p["meta"]["status"])
    migrator_.commit()
    _sikre_m57_claimbar(migrator_)


def _sikre_m57_claimbar(m):
    """Claim-vaktens fire vilkår for `rekruttering.evaluering`:
    registerrad m/ rett eier (finnes fra migrasjonene), aktivt
    modulhode, og en claiming-deployment i DETTE miljøet — idempotent
    (samme mønster som resolver-testenes m57-rigg)."""
    from miljo import gjeldende_miljo
    mv = gjeldende_miljo()
    m.execute("INSERT INTO modulhode (modul_id,status)"
              " VALUES ('m57_ats','aktiv') ON CONFLICT DO NOTHING")
    m.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,"
        "kontrakt_hash,payload_schema_hash,kvittering_schema_hash,"
        "sideeffektklasse,reversibilitet)"
        " VALUES ('m57_ats',1,%s,'p','k','krever_outbox','kompenserende')"
        " ON CONFLICT DO NOTHING", ("k-" + secrets.token_hex(8),))
    khash = m.execute(
        "SELECT kontrakt_hash FROM modulkontrakt"
        " WHERE modul_id='m57_ats' AND kontraktversjon=1").fetchone()[0]
    reg = m.execute(
        "SELECT eiermodul FROM oppdragstype_register"
        " WHERE oppdragstype='rekruttering.evaluering'").fetchone()
    if reg is None:
        m.execute(
            "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
            "kontraktversjon,kontrakt_hash)"
            " VALUES ('rekruttering.evaluering','m57_ats',1,%s)", (khash,))
    rad = m.execute(
        "SELECT release_id FROM moduldeployment"
        " WHERE modul_id='m57_ats' AND miljo=%s AND livslop='claiming'"
        " LIMIT 1", (mv,)).fetchone()
    if rad is None:
        rel = f"r57b-{secrets.token_hex(6)}"
        m.execute(
            "INSERT INTO modulrelease (modul_id,release_id,"
            "kontraktversjon,kontrakt_hash,manifest_hash,artifact_digest)"
            " VALUES ('m57_ats',%s,1,%s,'mh','ad')", (rel, khash))
        m.execute(
            "INSERT INTO moduldeployment (modul_id,release_id,"
            "kontraktversjon,kontrakt_hash,miljo,livslop)"
            " VALUES ('m57_ats',%s,1,%s,%s,'claiming')", (rel, khash, mv))
    m.commit()


def _profil(m):
    """En profilversjon via 061-døren (dørens eier)."""
    _sett_kontekst(m, TENANT)
    m.execute("SET LOCAL ROLE disponit_domene_eier")
    rad = m.execute(
        "SELECT ut_profil_id, ut_versjon FROM"
        " opprett_stillingsprofil_versjon(%s,NULL,%s,'test',"
        "%s::jsonb,%s)",
        (TENANT, "Driftskonsulent",
         json.dumps([{"kravnavn": "Drift", "vekt": 3},
                     {"kravnavn": "Norsk", "vekt": 1}]),
         secrets.token_hex(12))).fetchone()
    m.execute("RESET ROLE")
    m.commit()
    return f"{rad[0]}@{rad[1]}"


def _bunt(klient, m, cookie, csrf):
    """Reservert+lastet bunt over HTTP med bestiller-økten."""
    from api import sesjon as sesjonmodul
    import hashlib
    import io
    import zipfile
    r = klient.post("/v1/inndata/reserver",
                    json={"eiermodul": "m57_ats",
                          "formaal": "soknadsbunt"},
                    cookies={sesjonmodul.C_SESJON: cookie},
                    headers={"X-Disponit-CSRF": csrf,
                             "Idempotency-Key": secrets.token_hex(12)})
    assert r.status_code == 201, r.text
    jti = r.json()["reservasjon_jti"]
    ref = r.json()["inndata_ref"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("cv1.pdf", b"x" * 64)
    kropp = buf.getvalue()
    r2 = klient.put(f"/v1/inndata/opplast/{jti}", content=kropp,
                    cookies={sesjonmodul.C_SESJON: cookie},
                    headers={"X-Disponit-CSRF": csrf,
                             "content-type": "application/zip"})
    assert r2.status_code == 201, r2.text
    return ref


def _bestill(klient, cookie, csrf, kropp, idem=None):
    from api import sesjon as sesjonmodul
    return klient.post("/v1/bestilling", json=kropp,
                       cookies={sesjonmodul.C_SESJON: cookie},
                       headers={"X-Disponit-CSRF": csrf,
                                "Idempotency-Key":
                                    idem or "n-" + secrets.token_hex(8)})


@pg
def test_evalueringsbestillingen_binder_bunten_i_fodselstransaksjonen(
        klient, migrator, miljo, inndata_rot):
    _rekr_policy(migrator)
    cookie, csrf = _adminsesjon()
    ref = _bunt(klient, migrator, cookie, csrf)
    profilref = _profil(migrator)

    r = _bestill(klient, cookie, csrf,
                 {"bestillingstype": "rekruttering.evaluering",
                  "inndata_ref": ref, "stillingsprofil_ref": profilref,
                  "antall_soknader": 1, "omfang": "bunt"})
    assert r.status_code == 200, r.text
    assert r.json()["beslutning"] == "tillat"
    oid = r.json()["oppdrag_id"]
    assert isinstance(oid, int)

    # Bindingen skjedde i SAMME transaksjon som fødselen (X1): raden er
    # bundet til nøyaktig dette oppdraget.
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT status, oppdrag_id FROM inndata_artefakt"
        " WHERE tenant=%s AND inndata_id=%s",
        (TENANT, ref.split(":", 1)[1])).fetchone()
    migrator.rollback()
    assert rad == ("bundet", oid)

    # Payloaden er B-formen (#200): øyeblikksbilde, ingen
    # soknadsbunt_ref.
    from db import kryptering
    _sett_kontekst(migrator, TENANT)
    prad = migrator.execute(
        "SELECT payload_kryptert, key_id, nonce FROM oppdrag"
        " WHERE tenant=%s AND id=%s", (TENANT, oid)).fetchone()
    nok = migrator.execute(
        "SELECT wrapped_dek FROM tenant_nokler WHERE tenant=%s AND"
        " key_id=%s", (TENANT, prad[1])).fetchone()[0]
    migrator.rollback()
    dek = kryptering._pakk_ut((prad[1], nok), TENANT)[1]
    payload = kryptering.dekrypter(dek, bytes(prad[0]), bytes(prad[2]),
                                   TENANT, prad[1])
    assert "soknadsbunt_ref" not in payload
    assert payload["stillingsprofil_ref"] == profilref
    snap = payload["stillingsprofil"]
    assert snap["krav"] == [{"kravnavn": "Drift", "vekt": 3},
                            {"kravnavn": "Norsk", "vekt": 1}]
    assert payload["antall_soknader"] == 1


@pg
@dekker("stillingsprofil_ukjent")
@dekker("inndata_ubrukelig")
def test_referanser_som_ikke_kan_brukes_avvises_for_beslutningen(
        klient, migrator, miljo, inndata_rot):
    """Forhåndsportene: ukjent profil 404, ubrukelig bunt 409 — begge
    FØR beslutningen (ingen kvote brent), og formfeil 400."""
    _rekr_policy(migrator)
    cookie, csrf = _adminsesjon()
    ref = _bunt(klient, migrator, cookie, csrf)
    profilref = _profil(migrator)

    r = _bestill(klient, cookie, csrf,
                 {"bestillingstype": "rekruttering.evaluering",
                  "inndata_ref": ref,
                  "stillingsprofil_ref": f"{uuid.uuid4()}@1",
                  "antall_soknader": 1, "omfang": "bunt"})
    assert r.status_code == 404, r.text
    assert r.json()["feil"] == "stillingsprofil_ukjent"

    r2 = _bestill(klient, cookie, csrf,
                  {"bestillingstype": "rekruttering.evaluering",
                   "inndata_ref": f"inndata:{uuid.uuid4()}",
                   "stillingsprofil_ref": profilref,
                   "antall_soknader": 1, "omfang": "bunt"})
    assert r2.status_code == 409, r2.text
    assert r2.json()["feil"] == "inndata_ubrukelig"

    for kropp in (
        {"bestillingstype": "rekruttering.evaluering",
         "inndata_ref": "inndata:ikke-uuid",
         "stillingsprofil_ref": profilref,
         "antall_soknader": 1, "omfang": "bunt"},
        {"bestillingstype": "rekruttering.evaluering",
         "inndata_ref": ref, "stillingsprofil_ref": profilref,
         "antall_soknader": 5001, "omfang": "bunt"},
        {"bestillingstype": "rekruttering.evaluering",
         "inndata_ref": ref, "stillingsprofil_ref": profilref,
         "antall_soknader": 1, "omfang": "alt"},
        {"bestillingstype": "rekruttering.evaluering",
         "inndata_ref": ref, "stillingsprofil_ref": profilref,
         "antall_soknader": 1, "omfang": "bunt", "hostname": "x.no"},
    ):
        rf = _bestill(klient, cookie, csrf, kropp)
        assert rf.status_code == 400, (kropp, rf.text)


def test_uhashbar_bestillingstype_er_400_ikke_500():
    """CodeRabbit major: `BESTILLINGSTYPER.get(liste)` reiser TypeError
    (uhashbar) — kroppen er klientens feil og skal dømmes 400, aldri 500."""
    from api.bestilling import Bestillingsfeil, normaliser
    for kropp in ({"bestillingstype": ["kontroll.wcag.nettsted"]},
                  {"bestillingstype": {"a": 1}},
                  {"bestillingstype": 7}):
        with pytest.raises(Bestillingsfeil) as ei:
            normaliser("t-x", kropp)
        assert ei.value.kode == "request_feilformet"


@pg
def test_en_bunt_kan_bare_bestilles_en_gang(klient, migrator, miljo, inndata_rot):
    """Andre bestilling på samme bunt: forhåndsporten ser `bundet` og
    svarer 409 uten å brenne kvote — og uten et andre oppdrag."""
    _rekr_policy(migrator)
    cookie, csrf = _adminsesjon()
    ref = _bunt(klient, migrator, cookie, csrf)
    profilref = _profil(migrator)
    r = _bestill(klient, cookie, csrf,
                 {"bestillingstype": "rekruttering.evaluering",
                  "inndata_ref": ref, "stillingsprofil_ref": profilref,
                  "antall_soknader": 1, "omfang": "bunt"})
    assert r.status_code == 200, r.text
    r2 = _bestill(klient, cookie, csrf,
                  {"bestillingstype": "rekruttering.evaluering",
                   "inndata_ref": ref, "stillingsprofil_ref": profilref,
                   "antall_soknader": 2, "omfang": "bunt"})
    assert r2.status_code == 409, r2.text
    assert r2.json()["feil"] == "inndata_ubrukelig"
