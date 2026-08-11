"""PR-013 CP5c — aktiveringsflyten (fire-øyne på fullmaktsendring).

Beviser at DB-en + porten sammen håndhever V6/V7/V9/V10 ende-til-ende:
- første policy (mot DENY_ALL_V1) er UTVIDER → to godkjennere, forfatter alene
  utilstrekkelig;
- en innsnevring krever én godkjenner som IKKE er forfatteren;
- godkjenneren attesterer DIFFEN (feil diff_hash avvises);
- en flyttet base under låsen → rebasering, aldri stille aktivering;
- aktiveringen går KUN via den herdede `aktiver_policy` (deaktiver+innsett i én
  tx) og etterlater nøyaktig én aktiv versjon + en `brukt` runde + et
  `aktivert` utkast;
- manglende scope stopper handlingen; replay er idempotent.
"""
import json
import secrets
import threading
from datetime import datetime, timezone

import pytest

from api import policyadmin
from api import policyregister as pr
from api.mac_register import MacRegister

from .test_api import DSN, MIGRATOR_DSN

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
TEN = "t-pflyt-" + secrets.token_hex(3)
MAC = MacRegister({"mk1": {"rolle": "signerer", "hemmelighet": "m" * 40}})


def _naa():
    return datetime.now(timezone.utc)


def _mig():
    from db.pg import koble, sett_kontekst
    c = koble(MIGRATOR_DSN)
    sett_kontekst(c, TEN, "sys", "r0")
    return c


def _rt():
    from db.pg import koble
    return koble(DSN)


def _medlem(sub, roller):
    """Opprett identitet + medlemskap via migrator (som i drift). -> bruker_id."""
    from db.pg import sett_kontekst
    from .test_pr010_db import _identitet
    m = _mig()
    sett_kontekst(m, TEN, "sys", "r0")
    bid = _identitet(m, sub=f"{TEN}-{sub}")
    arr = "ARRAY[" + ",".join(f"'{r}'" for r in roller) + "]"
    m.execute(f"INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
              f" VALUES (%s,%s,{arr}) ON CONFLICT (tenant,bruker_id)"
              f" DO UPDATE SET roller=EXCLUDED.roller, aktiv=true", (TEN, bid))
    m.commit()
    m.close()
    return bid


def _utkast(uid, pid, opprettet_av, innhold, status="validert"):
    m = _mig()
    h = pr.innholds_hash(innhold)
    m.execute(
        "INSERT INTO policyutkast (tenant,utkast_id,policy_id,innhold,"
        "innholds_hash,status,opprettet_av) VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s)",
        (TEN, uid, pid, json.dumps(innhold), h, status, opprettet_av))
    m.commit()
    m.close()
    return h


def _aktiv_base(pid, innhold, versjon="1"):
    """Sett en aktiv base-policy direkte (konsistent peker) for INNSNEVRER-/
    rebaseringstestene, uten å kjøre hele førstegangsaktiveringen."""
    m = _mig()
    h = pr.innholds_hash(innhold)
    m.execute(
        "INSERT INTO policyer (tenant,policy_id,versjon,innholds_hash,status,"
        "innhold,aktiv) VALUES (%s,%s,%s,%s,'produksjon',%s::jsonb,true)",
        (TEN, pid, versjon, h, json.dumps(innhold)))
    m.execute(
        "INSERT INTO policy_hode (tenant,policy_id,neste_versjon,aktiv_versjon,"
        "revisjon) VALUES (%s,%s,%s,%s,1)",
        (TEN, pid, int(versjon) + 1, versjon))
    m.commit()
    m.close()


def _apne(rt, uid, aktor):
    idem = secrets.token_hex(8)
    r = policyadmin.opprett_aktiveringsrunde(
        rt, tenant=TEN, utkast_id=uid, aktor=aktor, request_id="r",
        idempotency_key=idem, input_hash=f"{TEN}\x1f{uid}\x1fapne\x1f{idem}",
        naa=_naa())
    return r


def _attester(rt, uid, aktor, diff_hash, idem=None):
    idem = idem or secrets.token_hex(8)
    ih = f"{TEN}\x1f{uid}\x1f{aktor}\x1f{diff_hash}\x1f{idem}"
    return policyadmin.attester_aktivering(
        rt, MAC, tenant=TEN, aktor=aktor, request_id="r", utkast_id=uid,
        forventet_diff_hash=diff_hash, idempotency_key=idem, input_hash=ih,
        naa=_naa())


def _aktiv_versjon(pid):
    m = _mig()
    rad = m.execute("SELECT aktiv_versjon FROM policy_hode WHERE tenant=%s AND"
                    " policy_id=%s", (TEN, pid)).fetchone()
    m.rollback()
    m.close()
    return rad[0] if rad else None


# --------------------------------------------------------------------------

@pg
def test_forste_policy_utvider_krever_to_godkjennere_aktiveres():
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    b = _medlem("uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, {"roller": [{"id": "r1"}],
                          "handlinger": [{"id": "h1"}]})
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        assert r["risikoklasse"] == "UTVIDER"
        assert r["pakrevd_antall_godkjennere"] == 2
        dh = r["diff_hash"]

        # Forfatteren attesterer først → venter (én av to, ingen uavhengig ennå).
        r1 = _attester(rt, uid, a, dh)
        assert r1["utfall"] == "venter_godkjennere", r1
        assert r1["gjenstaar"] == 1

        # Uavhengig godkjenner → terskel nådd → aktivert via herdet funksjon.
        r2 = _attester(rt, uid, b, dh)
        assert r2["utfall"] == "aktivert", r2
        assert r2["versjon"] == "1"
    finally:
        rt.close()

    assert _aktiv_versjon(pid) == "1"
    m = _mig()
    try:
        antall_aktiv = m.execute(
            "SELECT count(*) FROM policyer WHERE tenant=%s AND policy_id=%s AND"
            " aktiv", (TEN, pid)).fetchone()[0]
        ustatus = m.execute("SELECT status FROM policyutkast WHERE tenant=%s AND"
                            " utkast_id=%s", (TEN, uid)).fetchone()[0]
        rstatus = m.execute("SELECT status FROM aktiveringsrunde WHERE tenant=%s"
                           " AND utkast_id=%s", (TEN, uid)).fetchone()[0]
    finally:
        m.rollback(); m.close()
    assert antall_aktiv == 1
    assert ustatus == "aktivert"
    assert rstatus == "brukt"


@pg
def test_forfatter_alene_kan_ikke_aktivere():
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, {"roller": [{"id": "r1"}]})
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        r1 = _attester(rt, uid, a, r["diff_hash"])
        assert r1["utfall"] == "venter_godkjennere"
        # Samme forfatter en gang til → append-only UNIQUE stopper det.
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _attester(rt, uid, a, r["diff_hash"])
        assert e.value.kode == "allerede_attestert"
    finally:
        rt.close()
    assert _aktiv_versjon(pid) is None   # aldri aktivert av forfatter alene


@pg
def test_innsnevrer_krever_en_uavhengig_godkjenner():
    pid = "pol-" + secrets.token_hex(3)
    _aktiv_base(pid, {"roller": [{"id": "r1"}, {"id": "r2"}]})
    a = _medlem("forf", ["policyforvalter"])
    b = _medlem("uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, {"roller": [{"id": "r1"}]})   # fjerner r2 → INNSNEVRER
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        assert r["risikoklasse"] == "INNSNEVRER"
        assert r["pakrevd_antall_godkjennere"] == 1
        dh = r["diff_hash"]
        # Forfatteren alene teller ikke (må være ≠ forfatter).
        r1 = _attester(rt, uid, a, dh)
        assert r1["utfall"] == "venter_godkjennere"
        assert r1["mangler_uavhengig"] is True
        # Uavhengig → aktivert (ny versjon 2, forrige deaktivert).
        r2 = _attester(rt, uid, b, dh)
        assert r2["utfall"] == "aktivert"
        assert r2["versjon"] == "2"
    finally:
        rt.close()
    assert _aktiv_versjon(pid) == "2"


@pg
def test_diff_utdatert_avvises():
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    b = _medlem("uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, {"roller": [{"id": "r1"}]})
    rt = _rt()
    try:
        _apne(rt, uid, a)
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _attester(rt, uid, b, "feil-diff-hash")
        assert e.value.kode == "diff_utdatert"
    finally:
        rt.close()


@pg
def test_scope_mangler_stopper_attestering():
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    leser = _medlem("leser", ["leser"])          # ingen policy:activate
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, {"roller": [{"id": "r1"}]})
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _attester(rt, uid, leser, r["diff_hash"])
        assert e.value.kode == "scope_mangler"
    finally:
        rt.close()


@pg
def test_rebasering_ved_flyttet_base():
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    b = _medlem("uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, {"roller": [{"id": "r1"}]})
    rt = _rt()
    try:
        r = _apne(rt, uid, a)      # base = DENY_ALL (ingen hoderad ennå)
        dh = r["diff_hash"]
        # En konkurrerende aktivering flytter basen etter at runden åpnet.
        _aktiv_base(pid, {"roller": [{"id": "rX"}]})
        r1 = _attester(rt, uid, a, dh)
        assert r1["utfall"] == "venter_godkjennere"
        r2 = _attester(rt, uid, b, dh)
        assert r2["utfall"] == "rebasering_kreves", r2
    finally:
        rt.close()
    # Basen fra den konkurrerende aktiveringen står — utkastet ble ikke aktivert.
    assert _aktiv_versjon(pid) == "1"


@pg
def test_tidligere_godkjenner_deautorisert_blokkerer():
    # Codex P1 R2: den siste attestasjonen utløser aktiveringen, men en TIDLIGERE
    # godkjenner som mister fullmakten i mellomtiden skal stoppe den.
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    b = _medlem("uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, {"roller": [{"id": "r1"}],
                          "handlinger": [{"id": "h1"}]})     # UTVIDER, pakrevd 2
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        _attester(rt, uid, a, r["diff_hash"])                # forfatter (venter)
        # Forfatteren mister policy:activate FØR den uavhengige fullfører.
        m = _mig()
        m.execute("UPDATE brukermedlemskap SET aktiv=false WHERE tenant=%s AND"
                  " bruker_id=%s", (TEN, a))
        m.commit(); m.close()
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _attester(rt, uid, b, r["diff_hash"])            # ville nådd terskel
        assert e.value.kode == "godkjenner_deautorisert"
    finally:
        rt.close()
    assert _aktiv_versjon(pid) is None                       # ikke aktivert


@pg
def test_godkjenner_authz_endret_blokkerer():
    # Codex R2: en godkjenner hvis roller ENDRES (authz_version bumpes) etter
    # attestasjonen — men som fortsatt HAR policy:activate — skal likevel stoppe
    # aktiveringen: attestasjonen ble gitt under en ANNEN autorisasjon.
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    b = _medlem("uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, {"roller": [{"id": "r1"}],
                          "handlinger": [{"id": "h1"}]})
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        _attester(rt, uid, a, r["diff_hash"])
        # Forfatteren får en EKSTRA rolle → authz_version bumpes, men beholder
        # policyforvalter (og dermed scopet). Attestasjonen er nå stale.
        m = _mig()
        m.execute("UPDATE brukermedlemskap SET roller="
                  "ARRAY['policyforvalter','leser'] WHERE tenant=%s AND"
                  " bruker_id=%s", (TEN, a))
        m.commit(); m.close()
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _attester(rt, uid, b, r["diff_hash"])
        assert e.value.kode == "godkjenner_deautorisert"
    finally:
        rt.close()
    assert _aktiv_versjon(pid) is None


@pg
def test_samtidig_aktivering_deler_godkjennere_ingen_vranglaas():
    # Codex R3: to samtidige aktiveringer på samme policy som deler godkjennere
    # i MOTSATT attestasjonsrekkefølge (D1: a,b · D2: b,a) må ikke vranglåse —
    # medlemskapslåsene tas i deterministisk (sortert) rekkefølge. Deterministisk
    # utfall: nøyaktig én vinner, den andre rebaserer; ingen deadlock.
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    b = _medlem("uavh", ["policyforvalter"])
    d1 = "utk-" + secrets.token_hex(3)
    d2 = "utk-" + secrets.token_hex(3)
    _utkast(d1, pid, a, {"roller": [{"id": "r1"}], "handlinger": [{"id": "h1"}]})
    _utkast(d2, pid, b, {"roller": [{"id": "r2"}], "handlinger": [{"id": "h2"}]})
    s = _rt()
    try:
        r1 = _apne(s, d1, a)
        r2 = _apne(s, d2, b)
        _attester(s, d1, a, r1["diff_hash"])       # forfatter D1 (venter)
        _attester(s, d2, b, r2["diff_hash"])       # forfatter D2 (venter)
    finally:
        s.close()

    barriere = threading.Barrier(2)
    res: dict = {}

    def kjor(navn, uid, aktor, dh):
        c = _rt()
        try:
            barriere.wait(timeout=10)
            res[navn] = _attester(c, uid, aktor, dh)["utfall"]
        except policyadmin.Aktiveringsfeil as e:
            res[navn] = e.kode
        except Exception as e:                      # deadlock ville havne her
            res[navn] = f"EXC:{type(e).__name__}"
        finally:
            c.close()

    t1 = threading.Thread(target=kjor, args=("t1", d1, b, r1["diff_hash"]))
    t2 = threading.Thread(target=kjor, args=("t2", d2, a, r2["diff_hash"]))
    t1.start(); t2.start()
    t1.join(timeout=30); t2.join(timeout=30)
    assert not t1.is_alive() and not t2.is_alive(), "vranglås/heng"
    assert not any(str(v).startswith("EXC:") for v in res.values()), res
    assert list(res.values()).count("aktivert") == 1, res
    assert "rebasering_kreves" in res.values(), res
    assert _aktiv_versjon(pid) is not None


@pg
def test_idempotent_replay():
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, {"roller": [{"id": "r1"}]})
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        idem = secrets.token_hex(8)
        r1 = _attester(rt, uid, a, r["diff_hash"], idem=idem)
        r2 = _attester(rt, uid, a, r["diff_hash"], idem=idem)
        assert r2.get("replay") is True
        assert r2["utfall"] == r1["utfall"] == "venter_godkjennere"
    finally:
        rt.close()
