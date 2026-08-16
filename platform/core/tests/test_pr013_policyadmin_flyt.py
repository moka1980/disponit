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

import psycopg
import pytest

from api import policyadmin
from api import policyregister as pr
from api.mac_register import MacRegister

from .test_api import DSN, MIGRATOR_DSN

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
TEN = "t-pflyt-" + secrets.token_hex(3)
MAC = MacRegister({"mk1": {"rolle": "signerer", "hemmelighet": "m" * 40}})

#: Utkastinnhold som UTVIDER fra DENY_ALL_V1 → to påkrevde godkjennere. Brukt
#: der terskelen selv er poenget (drift-/gjenopptakelsestestene).
_UTVIDER_INNHOLD = {"roller": [{"id": "r1"}], "handlinger": [{"id": "h1"}]}


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


def _med_meta(pid, innhold, versjon):
    """Utkast-/basefragment + den `meta` en ekte policy alltid bærer.

    Aktiveringen lagrer nå policyens EGEN `meta.versjon` som registerets
    `versjon` (migrasjon 020) — et fragment uten `meta` er derfor ikke lenger
    et utkast som kan aktiveres, og var det i praksis aldri: skjemaet krever
    `meta.versjon` på formen 1.2.3 for at et utkast skal kunne valideres.
    Feltene her er alle klassifisert NØYTRALE, så risikoklassen i testene
    avgjøres fortsatt av regelendringen alene.
    """
    if "meta" in innhold:
        return innhold
    return {**innhold,
            "meta": {"policy_id": pid, "versjon": versjon,
                     "bransjemal": "test", "status": "produksjon"}}


def _utkast(uid, pid, opprettet_av, innhold, status="validert",
            versjon="1.1.0"):
    m = _mig()
    innhold = _med_meta(pid, innhold, versjon)
    h = pr.innholds_hash(innhold)
    m.execute(
        "INSERT INTO policyutkast (tenant,utkast_id,policy_id,innhold,"
        "innholds_hash,status,opprettet_av) VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s)",
        (TEN, uid, pid, json.dumps(innhold), h, status, opprettet_av))
    m.commit()
    m.close()
    return h


def _aktiv_base(pid, innhold, versjon="1.0.0"):
    """Sett en aktiv base-policy direkte (konsistent peker) for INNSNEVRER-/
    rebaseringstestene, uten å kjøre hele førstegangsaktiveringen."""
    m = _mig()
    innhold = _med_meta(pid, innhold, versjon)
    h = pr.innholds_hash(innhold)
    m.execute(
        "INSERT INTO policyer (tenant,policy_id,versjon,innholds_hash,status,"
        "innhold,aktiv) VALUES (%s,%s,%s,%s,'produksjon',%s::jsonb,true)",
        (TEN, pid, versjon, h, json.dumps(innhold)))
    m.execute(
        "INSERT INTO policy_hode (tenant,policy_id,aktiv_versjon,revisjon)"
        " VALUES (%s,%s,%s,1)", (TEN, pid, versjon))
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
        assert r2["versjon"] == "1.1.0", "registeret skal bære utkastets egen versjon"
    finally:
        rt.close()

    assert _aktiv_versjon(pid) == "1.1.0"
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
        # Uavhengig → aktivert (utkastets versjon, forrige deaktivert).
        r2 = _attester(rt, uid, b, dh)
        assert r2["utfall"] == "aktivert"
        assert r2["versjon"] == "1.1.0"
    finally:
        rt.close()
    assert _aktiv_versjon(pid) == "1.1.0"


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
    assert _aktiv_versjon(pid) == "1.0.0"


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
    # Ulike versjoner: to reelle, uavhengige utkast på samme policy. Taperen
    # stoppes av at BASEN flyttet seg (rebasering), ikke av et versjonskrasj.
    _utkast(d1, pid, a, {"roller": [{"id": "r1"}], "handlinger": [{"id": "h1"}]},
            versjon="1.1.0")
    _utkast(d2, pid, b, {"roller": [{"id": "r2"}], "handlinger": [{"id": "h2"}]},
            versjon="1.2.0")
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
def test_laas_godkjenner_serialiserer_mot_tilbakekalling():
    # Codex R4: `FOR UPDATE` i laas_godkjenner MÅ serialisere mot en samtidig
    # medlemskapstilbakekalling — ellers kan revokeringen committe mens
    # aktiveringen leser gammel autorisasjon. Muterings-drepende bevis: mens én
    # tx HOLDER laas_godkjenner-låsen på raden, blokkeres en UPDATE på samme rad
    # (lock_timeout → LockNotAvailable). En plain `SELECT` ville ikke låst, og
    # UPDATE-en ville gått rett gjennom → denne testen ville feilet.
    import psycopg
    from db.pg import sett_kontekst
    bid = _medlem("laasX", ["policyforvalter"])
    holder = _rt()
    revoker = _mig()
    try:
        sett_kontekst(holder, TEN, "x", "r")
        rad = holder.execute("SELECT roller FROM laas_godkjenner(%s,%s)",
                             (TEN, bid)).fetchone()
        assert rad is not None                       # låsen holdes nå (ingen commit)
        revoker.execute("SET lock_timeout='800ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            revoker.execute("UPDATE brukermedlemskap SET aktiv=false WHERE"
                            " tenant=%s AND bruker_id=%s", (TEN, bid))
        revoker.rollback()
        # Slipp låsen → nå går tilbakekallingen gjennom.
        holder.rollback()
        sett_kontekst(revoker, TEN, "x", "r")
        revoker.execute("UPDATE brukermedlemskap SET aktiv=false WHERE tenant=%s"
                        " AND bruker_id=%s", (TEN, bid))
        naa = revoker.execute("SELECT aktiv FROM brukermedlemskap WHERE tenant=%s"
                             " AND bruker_id=%s", (TEN, bid)).fetchone()[0]
        revoker.commit()
        assert naa is False
    finally:
        holder.close(); revoker.close()


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


# --------------------------------------------------------------------------
# Pekerdrift (Codex P1): peker og flagg som spriker skal stoppe runden FØR
# noen signerer, ikke først når `en_aktiv_per_policy` velter aktiveringen.
# --------------------------------------------------------------------------

def _drift(pid, versjon="9"):
    """Nøyaktig prod-tilstanden: en AKTIV policyrad uten ankerrad.

    Slik så en tenant ut etter `init-tenant.sh` før denne PR-en — og det er
    tilstanden runden ikke har lov til å bygge på.
    """
    innhold = {"roller": [{"id": "rDrift"}]}
    m = _mig()
    m.execute(
        "INSERT INTO policyer (tenant,policy_id,versjon,innholds_hash,status,"
        "innhold,aktiv) VALUES (%s,%s,%s,%s,'produksjon',%s::jsonb,true)",
        (TEN, pid, versjon, pr.innholds_hash(innhold), json.dumps(innhold)))
    m.commit()
    m.close()


@pg
def test_usynk_peker_stopper_runde_apning():
    """En runde skal ikke ÅPNES på en base pekeren ikke er enig i.

    Uten kontrollen leser runde-åpningen NULL-pekeren, differ mot DENY_ALL_V1
    og lar godkjennerne signere en klassifisering som er regnet ut fra feil
    base — en runde som uansett ikke kan aktiveres etter en reparasjon.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, {"roller": [{"id": "r1"}]})
    _drift(pid)
    rt = _rt()
    try:
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _apne(rt, uid, a)
        assert e.value.kode == "aktiv_peker_usynk", e.value.kode
        rt.rollback()
    finally:
        rt.close()
    m = _mig()
    try:
        assert m.execute("SELECT count(*) FROM aktiveringsrunde WHERE tenant=%s"
                         " AND utkast_id=%s", (TEN, uid)).fetchone()[0] == 0, (
            "runden ble åpnet på en usynk base")
    finally:
        m.rollback(); m.close()


@pg
def test_usynk_peker_stopper_attestering_for_signaturen_skrives():
    """Oppstår driften ETTER at runden åpnet, stoppes den ved attesteringen.

    Og den stoppes FØR attestasjonen skrives: en signatur på et grunnlag som
    ikke kan aktiveres er ingen godkjenning. Eier skal få vite at dataene må
    repareres — ikke at «handlingen feilet».
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, {"roller": [{"id": "r1"}]})
    rt = _rt()
    try:
        r = _apne(rt, uid, a)          # ren base: ingen aktiv rad, ingen peker
        _drift(pid)                    # ... og så kommer driften
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _attester(rt, uid, a, r["diff_hash"])
        assert e.value.kode == "aktiv_peker_usynk", e.value.kode
        rt.rollback()
    finally:
        rt.close()
    m = _mig()
    try:
        assert m.execute(
            "SELECT count(*) FROM aktiveringsattestasjon WHERE tenant=%s AND"
            " utkast_id=%s", (TEN, uid)).fetchone()[0] == 0, (
            "attestasjonen ble skrevet på et grunnlag som ikke kan aktiveres")
    finally:
        m.rollback(); m.close()


@pg
def test_terskelattestasjonen_overlever_drift_i_aktiveringsvinduet(monkeypatch):
    """Drift som oppstår ETTER kontrollen skal ikke spise godkjenningen.

    Attestasjonen ligger i SAMME transaksjon som aktiveringsforsøket. En full
    rollback ville tatt den siste godkjennerens signatur med i fallet: runden
    ville stått åpen og under terskel igjen, og hun måtte attestert på nytt —
    stikk i strid med at en usynk peker skal REPARERES, ikke re-attesteres.

    Vinduet mellom kontrollen i steg 5b og selve aktiveringen er ekte, men
    trangt (en samtidig commit). Kontrollen nøytraliseres derfor her for å
    treffe nøyaktig det vinduet deterministisk — det som testes er hva
    aktiveringsforsøket gjør når delindeksen slår til, ikke kontrollen.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    b = _medlem("uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, {"roller": [{"id": "r1"}],
                          "handlinger": [{"id": "h1"}]})   # UTVIDER, pakrevd 2
    idem = secrets.token_hex(8)
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        dh = r["diff_hash"]
        assert r["pakrevd_antall_godkjennere"] == 2
        assert _attester(rt, uid, a, dh)["utfall"] == "venter_godkjennere"
        _drift(pid)                    # samtidig commit i vinduet
        monkeypatch.setattr(policyadmin, "_krev_peker_synk",
                            lambda *_a, **_k: None)
        r2 = _attester(rt, uid, b, dh, idem=idem)
        assert r2["utfall"] == "aktiv_peker_usynk", r2
        # Deterministisk: en replay med samme nøkkel gir NØYAKTIG samme svar.
        r3 = _attester(rt, uid, b, dh, idem=idem)
        assert r3["utfall"] == "aktiv_peker_usynk" and r3.get("replay") is True
    finally:
        rt.close()
    m = _mig()
    try:
        antall = m.execute(
            "SELECT count(*) FROM aktiveringsattestasjon WHERE tenant=%s AND"
            " utkast_id=%s", (TEN, uid)).fetchone()[0]
        rstatus = m.execute("SELECT status FROM aktiveringsrunde WHERE tenant=%s"
                            " AND utkast_id=%s", (TEN, uid)).fetchone()[0]
        aktive = m.execute(
            "SELECT versjon FROM policyer WHERE tenant=%s AND policy_id=%s AND"
            " aktiv", (TEN, pid)).fetchall()
    finally:
        m.rollback(); m.close()
    assert antall == 2, "terskel-attestasjonen gikk tapt sammen med aktiveringen"
    assert rstatus == "apen", "runden skal stå — dataene repareres, ikke runden"
    assert [x[0] for x in aktive] == ["9"], "noe ble aktivert tross usynk peker"
    assert _aktiv_versjon(pid) is None


@pg
def test_bootstrappet_tenant_aktiveres_og_kan_LESES_av_beslutningsveien():
    """Hele poenget med ankerraden, målt der det faktisk teller.

    En tenant satt opp som `init-tenant.sh` gjør det (registrer + aktiver),
    kjører en full styrt runde — og resultatet må være LESBART for
    beslutningsveien. Det var det ikke: aktiveringen allokerte versjonen fra
    telleren `neste_versjon` og lagret «1», mens dokumentet bar «1.1.0».
    `hent_aktiv` krever at de to er enige, så hver forespørsel etterpå avviste
    den ferske policyen som KORRUPT — etter at runden hadde svart «aktivert».

    Kontroll: lar man aktiveringen allokere fra en teller igjen, blir denne rød
    på `PolicyKorrupt`, ikke på aktiveringen.
    """
    from api import policyregister
    from .test_bootstrap_ankerrad import _policy
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    b = _medlem("uavh", ["policyforvalter"])

    m = _mig()
    pr.registrer(m, TEN, _policy("1.0.0", policy_id=pid), "produksjon")
    m.commit()
    m.close()

    ny = _policy("1.1.0", policy_id=pid)
    ny["roller"] = [*ny["roller"], {"id": "agent2"}]
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, ny)
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        dh = r["diff_hash"]
        _attester(rt, uid, a, dh)
        r2 = _attester(rt, uid, b, dh)
        assert r2["utfall"] == "aktivert", r2
        assert r2["versjon"] == "1.1.0", r2
    finally:
        rt.close()

    m = _mig()
    try:
        innhold, _h = policyregister.hent_aktiv(m, TEN, pid)
    finally:
        m.rollback()
        m.close()
    assert innhold["meta"]["versjon"] == "1.1.0"
    assert {r["id"] for r in innhold["roller"]} == {"agent", "agent2"}


@pg
def test_porten_nullpadder_gammel_aktiv_versjon_i_monotonikontrollen():
    """En aktiv «2» fra den gamle telleren er 2.0.0 — ikke mindre enn den.

    Migrasjon 020 lar de gamle radene stå, så monotonikontrollen må kunne måle
    et semantisk dokument mot en versjon med FÆRRE ledd. Uten nullpadding
    sorterer (2, 0, 0) over (2,) — likt prefiks, lengst vinner — og porten
    ville åpnet en runde på et utkast som bærer nøyaktig den versjonen den
    aktive raden allerede har. To signaturer på en aktivering som skulle vært
    stoppet før den første.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    _aktiv_base(pid, {"roller": [{"id": "r1"}]}, versjon="2")   # gammel teller
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, {"roller": [{"id": "r1"}, {"id": "r2"}]},
            versjon="2.0.0")
    rt = _rt()
    try:
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _apne(rt, uid, a)
        assert e.value.kode == "versjon_i_bruk", e.value.kode
        rt.rollback()
        # Et dokument som FAKTISK ligger over 2.0.0 åpner fortsatt.
        uid2 = "utk-" + secrets.token_hex(3)
        _utkast(uid2, pid, a, {"roller": [{"id": "r1"}, {"id": "r2"}]},
                versjon="2.0.1")
        assert _apne(rt, uid2, a)["diff_hash"]
    finally:
        rt.close()


@pg
def test_porten_avviser_utkast_som_oppgir_en_annen_policy():
    """🔴 P1: ingen runde åpnes på et dokument med fremmed identitet.

    Et utkast validert FØR identitetskontrollen fantes kan bære avviket inn i
    en runde. Da ville godkjennerne signert en aktivering som indekserer
    policyen under én id mens motoren leser en annen ut av dokumentet — og
    `hent_aktiv` ville avvist resultatet som korrupt etterpå. Porten stopper
    det før noen signerer, med en kode eier kan handle på.

    Kontroll: fjern `_krev_dokumentidentitet` i `opprett_aktiveringsrunde`, så
    åpner runden og feilen flyttes til etter to signaturer.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    # Dokumentet oppgir en ANNEN policy enn raden det ligger under.
    _utkast(uid, pid, a, {**_UTVIDER_INNHOLD,
                          "meta": {"policy_id": "en-annen-policy",
                                   "versjon": "1.1.0", "bransjemal": "test",
                                   "status": "produksjon"}})
    rt = _rt()
    try:
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _apne(rt, uid, a)
        assert e.value.kode == "policy_id_avvik", e.value.kode
    finally:
        rt.rollback()
        rt.close()


@pg
def test_porten_avviser_utkast_som_ikke_sier_produksjon():
    """🔴 P1: et utkast merket `utkast` kan ikke aktiveres som `produksjon`.

    Aktiveringen skriver alltid registerstatus `produksjon`, og `hent_aktiv`
    krever at dokumentet sier det samme. Et skjemagyldig utkast med
    `meta.status: utkast` gikk derfor hele veien gjennom fire-øyne og ble en
    aktiv policy beslutningsveien avviste som korrupt. Statusen kan ikke rettes
    etterpå — innholdet er frosset — så kravet må stå FØR noen signerer.

    Kontroll: fjern `_krev_produksjonsstatus` i `opprett_aktiveringsrunde`, så
    åpner runden, og feilen flytter seg til etter to signaturer.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, {**_UTVIDER_INNHOLD,
                          "meta": {"policy_id": pid, "versjon": "1.1.0",
                                   "bransjemal": "test", "status": "utkast"}})
    rt = _rt()
    try:
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _apne(rt, uid, a)
        assert e.value.kode == "status_ikke_produksjon", e.value.kode
    finally:
        rt.rollback()
        rt.close()


@pg
def test_hent_aktiv_avviser_rad_med_fremmed_dokumentidentitet():
    """🔴 P1: beslutningsveien nekter å bruke en policy som ikke vet hvem den er.

    Status og versjon revalideres mot registeret ved lasting; identiteten var
    den ENESTE av de tre dokumentet fikk oppgi fritt. Spriker den, bygger
    motoren policyreferansen sin fra en id ingen kan slå opp igjen — og
    M-37-gjenopprettingen leter etter en aktiv rad som ikke finnes. Fail-closed
    er det eneste ærlige: `PolicyKorrupt`, ikke en beslutning under feil navn.
    """
    from .test_bootstrap_ankerrad import _policy
    pid = "pol-" + secrets.token_hex(3)
    # Fullgyldig policy — skjema, hash, status og versjon skal ALLE passere, så
    # det eneste som kan felle lasten er identiteten.
    innhold = _policy("1.0.0", policy_id="en-annen-policy")
    m = _mig()
    try:
        m.execute(
            "INSERT INTO policyer (tenant,policy_id,versjon,innholds_hash,"
            "status,innhold,aktiv) VALUES"
            " (%s,%s,'1.0.0',%s,'produksjon',%s::jsonb,true)",
            (TEN, pid, pr.innholds_hash(innhold), json.dumps(innhold)))
        m.commit()
        from db.pg import sett_kontekst
        sett_kontekst(m, TEN, "sys", "r1")
        with pytest.raises(pr.PolicyKorrupt) as e:
            pr.hent_aktiv(m, TEN, pid)
        assert "meta.policy_id" in str(e.value), str(e.value)
    finally:
        m.rollback()
        m.close()


def test_versjonsnokkel_maaler_uten_ovre_grense():
    """🔴 P2: porten hadde databasens 32-bits-problem i sin egen form.

    `_versjonsnokkel` brukte `int()`, og CPython nekter å konvertere strenger
    over 4300 sifre. Skjemaet setter ingen grense på `meta.versjon`, så et
    skjemagyldig utkast kunne felt PORTEN med `ValueError` — HTTP 500 der det
    skulle stått et utfall. Nøkkelen sammenligner nå (antall sifre, sifrene):
    ubegrenset, og nøyaktig tallorden for ikke-negative heltall.

    Kjører uten database: kontrollen er ren.
    """
    n = policyadmin._versjonsnokkel
    assert n("2", 3) == n("2.0.0", 3), "«2» fra den gamle telleren ER 2.0.0"
    assert n("2.0.1", 3) > n("2.0.0", 3)
    assert n("10.0.0", 3) > n("9.0.0", 3), "sammenlignet som tekst, ikke tall"
    assert n("2147483648.0.0", 3) > n("2147483647.0.0", 3)   # over int4
    stor = "9" * 5000                       # over CPythons int-grense (4300)
    assert n(f"{stor}.0.0", 3) > n("2147483648.0.0", 3)
    # Og over Postgres' `numeric`-tak (131 072 sifre), som var det ANDRE taket
    # Codex fant: begge gatene måler nå det samme, uten tak noe sted.
    enorm = "9" * 140000
    assert n(f"{enorm}.0.0", 3) > n(f"{'9' * 139999}.0.0", 3)


@pg
def test_porten_avviser_versjon_registeret_ikke_kan_lagre():
    """🔴 En versjon som ikke KAN lagres skal ikke koste to signaturer.

    `versjon` er del av primærnøkkelen, og btree-oppføringen har et hardt tak.
    Skjemaet setter ingen grense og API-ets kroppsgrense slipper gjennom ledd på
    titusener av sifre, så uten dette passerte en slik versjon alle kontrollene
    og veltet først på INSERT-en inne i `aktiver_policy` — som
    `ProgramLimitExceeded`, altså en uhåndtert 500 etter at godkjennerne hadde
    signert.

    Kontroll: fjern lengdekravet i `_krev_ny_versjon`, så blir denne rød ved at
    runden ÅPNER på en versjon som aldri kan skrives.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, dict(_UTVIDER_INNHOLD), versjon="9" * 600 + ".0.0")
    rt = _rt()
    try:
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _apne(rt, uid, a)
        assert e.value.kode == "versjon_mangler", e.value.kode
    finally:
        rt.rollback()
        rt.close()


def _ekte_pk_kollisjon(pid):
    """-> en EKTE `UniqueViolation` fra `policyer_pkey`.

    Ikke en konstruert exception: routingen leser `diag.constraint_name`, og en
    håndlaget feil har ingen. Da ville testen bevist at fallbacken virker, ikke
    at PK-bruddet gjenkjennes. Denne kommer fra databasen, med navnet i seg.
    """
    innhold = {"roller": [{"id": "rPk"}]}
    m = _mig()
    try:
        rad = (TEN, pid, "7.7.7", pr.innholds_hash(innhold),
               json.dumps(innhold))
        sql = ("INSERT INTO policyer (tenant,policy_id,versjon,innholds_hash,"
               "status,innhold,aktiv) VALUES (%s,%s,%s,%s,'produksjon',"
               "%s::jsonb,false)")
        m.execute(sql, rad)
        m.commit()
        from db.pg import sett_kontekst
        sett_kontekst(m, TEN, "sys", "r1")
        try:
            m.execute(sql, rad)
        except psycopg.errors.UniqueViolation as e:
            assert e.diag.constraint_name == "policyer_pkey", \
                e.diag.constraint_name
            return e
        raise AssertionError("dubletten ble akseptert — PK-en er borte")
    finally:
        m.rollback()
        m.close()


class _KollisjonIAktiveringen:
    """Forbindelsen, men `aktiver_policy` reiser den oppgitte feilen.

    Vinduet finnes bare inne i funksjonen — mellom dens egen ubrukt-kontroll og
    INSERT-en — og kan ikke treffes utenfra uten å skrive om funksjonen. Alt
    ANNET i veien er ekte: savepointet, kanselleringen, idempotenslagringen og
    committen kjører mot databasen som vanlig.
    """

    def __init__(self, ekte, feil):
        self._ekte, self._feil = ekte, feil

    def execute(self, sql, params=None, **kw):
        if "aktiver_policy" in str(sql):
            raise self._feil
        return self._ekte.execute(sql, params, **kw)

    def __getattr__(self, navn):
        return getattr(self._ekte, navn)


@pg
def test_pk_kollisjon_kansellerer_runden_i_stedet_for_a_meldes_som_drift():
    """🔴 P2: en tapt versjon er ikke en usynk peker.

    INSERT-en i `aktiver_policy` kan bryte to ulike unike krav, og de betyr
    motsatte ting for eier. `en_aktiv_per_policy` = pekeren må repareres, og
    runden er gyldig etterpå. `policyer_pkey` = versjonen ble registrert av en
    annen skriver i vinduet, pekeren er i synk, og runden er DØD: innholdet er
    frosset, så versjonen kan ikke økes uten et nytt utkast.

    Uten routing meldte begge `aktiv_peker_usynk`: eier ble sendt for å
    reparere en usynk som ikke fantes, og runden ble stående åpen og se levende
    ut selv om den aldri kunne aktiveres.

    Kontroll: fjern `policyer_pkey`-grenen, så blir denne rød med utfall
    `aktiv_peker_usynk` og en runde som fortsatt står åpen.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    b = _medlem("uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _UTVIDER_INNHOLD)             # UTVIDER, pakrevd 2
    feil = _ekte_pk_kollisjon(pid)
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        dh = r["diff_hash"]
        assert _attester(rt, uid, a, dh)["utfall"] == "venter_godkjennere"
        svar = _attester(_KollisjonIAktiveringen(rt, feil), uid, b, dh)
        assert svar["utfall"] == "versjon_i_bruk", svar
    finally:
        rt.close()
    m = _mig()
    try:
        rstatus = m.execute("SELECT status FROM aktiveringsrunde WHERE tenant=%s"
                            " AND utkast_id=%s", (TEN, uid)).fetchone()[0]
        antall = m.execute(
            "SELECT count(*) FROM aktiveringsattestasjon WHERE tenant=%s AND"
            " utkast_id=%s", (TEN, uid)).fetchone()[0]
    finally:
        m.rollback(); m.close()
    assert rstatus == "kansellert", (
        "en runde som beviselig aldri kan aktiveres ble stående åpen")
    assert antall == 2, "signaturene skal bestå — de er sporet av hva som ble godkjent"


def _reparer(pid, versjon="9"):
    """Eiers reparasjon: den forvillede aktive raden ryddes, så peker og flagg
    er enige igjen (begge «ingen aktiv» — nøyaktig basen runden ble åpnet på)."""
    m = _mig()
    m.execute("UPDATE policyer SET aktiv=false WHERE tenant=%s AND policy_id=%s"
              " AND versjon=%s", (TEN, pid, versjon))
    m.commit()
    m.close()


@pg
def test_terskelrunde_kan_aktiveres_paa_nytt_etter_reparasjon(monkeypatch):
    """En bevart runde må kunne FULLFØRES når dataene er reparert.

    Å bevare attestasjonen er bare halve jobben: uten en vei tilbake inn er
    runden fanget. Samme idempotensnøkkel replayer bare `aktiv_peker_usynk`,
    og en ny nøkkel fra samme godkjenner traff `allerede_attestert` — så en
    runde på NØYAKTIG terskel sto fast til en fjerde, unødvendig person
    signerte. Her sender den samme godkjenneren inn på nytt etter
    reparasjonen: ingen ny signatur skrives (append-only står), men terskel,
    reautorisering, rekalk og aktivering kjøres om igjen, og runden fullføres.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    b = _medlem("uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _UTVIDER_INNHOLD)             # UTVIDER, pakrevd 2
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        dh = r["diff_hash"]
        assert r["pakrevd_antall_godkjennere"] == 2
        assert _attester(rt, uid, a, dh)["utfall"] == "venter_godkjennere"
        _drift(pid)                    # samtidig commit i aktiveringsvinduet
        monkeypatch.setattr(policyadmin, "_krev_peker_synk",
                            lambda *_a, **_k: None)
        assert _attester(rt, uid, b, dh)["utfall"] == "aktiv_peker_usynk"
        # Eier reparerer. Kontrollen settes tilbake: den EKTE kontrollen skal
        # slippe gjennom nå — ellers er ikke dataene reparert.
        monkeypatch.undo()
        _reparer(pid)
        r2 = _attester(rt, uid, b, dh)          # ny nøkkel, samme godkjenner
        assert r2["utfall"] == "aktivert", r2
    finally:
        rt.close()
    m = _mig()
    try:
        antall = m.execute(
            "SELECT count(*) FROM aktiveringsattestasjon WHERE tenant=%s AND"
            " utkast_id=%s", (TEN, uid)).fetchone()[0]
        rstatus = m.execute("SELECT status FROM aktiveringsrunde WHERE tenant=%s"
                            " AND utkast_id=%s", (TEN, uid)).fetchone()[0]
    finally:
        m.rollback(); m.close()
    assert antall == 2, "gjenopptakelsen skrev en ny signatur"
    assert rstatus == "brukt"
    assert _aktiv_versjon(pid) == r2["versjon"]


@pg
def test_dublett_under_terskel_er_fortsatt_konflikt():
    """Gjenopptakelsen åpner IKKE for dubletter generelt.

    Venter runden på ANDRE godkjennere, er det ingenting å gjenoppta — da er en
    ny innsending fra en som alt har signert nøyaktig det den alltid har vært:
    en konflikt. Fire-øyne-gaten kan ikke omgås ved å sende inn to ganger.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _UTVIDER_INNHOLD)             # UTVIDER, pakrevd 2
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        assert _attester(rt, uid, a, r["diff_hash"])["utfall"] \
            == "venter_godkjennere"
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _attester(rt, uid, a, r["diff_hash"])
        assert e.value.kode == "allerede_attestert", e.value.kode
        rt.rollback()
    finally:
        rt.close()
    assert _aktiv_versjon(pid) is None
