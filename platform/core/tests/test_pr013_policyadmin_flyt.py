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


def test_versjonsformen_krever_ascii_sifre():
    """🔴 P2: «١.٠.٠» er skjemagyldig for Python, men ikke for databasen.

    `\\d` i Python — og dermed i `jsonschema` — matcher HELE Unicodes
    desimalsiffer-kategori, mens migrasjonene bruker `[0-9]`. Med en
    unicode-versjon godtok porten et utkast databasen ville avvist, og
    sifferstrengen sorterer dessuten feil: «١» ligger over «2» i tekst. Runden
    åpnet, godkjennerne signerte, og bruddet kom først i aktiveringen — som en
    kansellert runde.

    Kjører uten database: kontrollen er ren.
    """
    from api.policyadmin import _SEMVER, _TALLVERSJON, _dokumentavvik
    assert _SEMVER.match("1.0.0")
    assert not _SEMVER.match("١.٠.٠"), "unicode-sifre slipper gjennom porten"
    assert not _TALLVERSJON.match("٢")
    # Og valideringen sier fra MENS utkastet kan rettes.
    avvik = _dokumentavvik("p", {"meta": {"policy_id": "p", "versjon": "١.٠.٠",
                                          "status": "produksjon"}})
    assert any("meta.versjon" in a for a in avvik), avvik


def test_versjonsformen_har_ekte_slutt():
    """🔴 P2: `"1.2.3\\n"` er ikke en versjon — men Pythons `$` synes det.

    `$` matcher også rett FØR en avsluttende linjeskift, så både skjemaet og
    `_SEMVER.match()` godtok halen. Migrasjonenes `$` gjør det ikke, så
    utkastet ble frosset og attestert, og bruddet kom i `aktiver_policy` — der
    runden ble kansellert som `versjon_i_bruk`, en beskjed eier ikke kan handle
    på fordi `meta.versjon` da ikke lenger kan rettes.

    Samme hale på RADENS identitet er like dødfødt: `"acme\\n"` kan aldri
    skrives inn i dokumentet, og raden kan ikke rettes.

    Kontroll: bytt `fullmatch` tilbake til `match`, så blir denne rød.
    """
    from api.policyadmin import (_POLICY_ID, _SEMVER, _TALLVERSJON,
                                 _dokumentavvik)
    assert _SEMVER.fullmatch("1.2.3")
    assert not _SEMVER.fullmatch("1.2.3\n"), "hale slipper gjennom porten"
    assert not _TALLVERSJON.fullmatch("2\n")
    assert _POLICY_ID.fullmatch("acme")
    assert not _POLICY_ID.fullmatch("acme\n")
    # Og valideringen sier fra MENS utkastet kan rettes.
    avvik = _dokumentavvik("p", {"meta": {"policy_id": "p", "versjon": "1.2.3\n",
                                          "status": "produksjon"}})
    assert any("meta.versjon" in a for a in avvik), avvik


@pg
def test_porten_avviser_versjon_registeret_ikke_kan_lagre():
    """🔴 En versjon som ikke KAN lagres skal ikke koste to signaturer.

    `policyer_pkey` er (tenant, policy_id, versjon), og btree-oppføringen har et
    hardt tak de tre DELER — så nøkkelen må måles samlet, ikke feltvis. Skjemaet
    setter ingen grense på noen av dem, og API-ets kroppsgrense slipper gjennom
    ledd på titusener av sifre; uten dette passerte en slik nøkkel alle
    kontrollene og veltet først på INSERT-en inne i `aktiver_policy` — som
    `ProgramLimitExceeded`, altså en uhåndtert 500 etter at godkjennerne hadde
    signert.

    Kontroll: fjern nøkkelkravet i `_krev_ny_versjon`, så blir denne rød ved at
    runden ÅPNER på en versjon som aldri kan skrives.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, dict(_UTVIDER_INNHOLD), versjon="9" * 2500 + ".0.0")
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


# --------------------------------------------------------------------------
# Codex P2 på #63: et framoverrettet krav må gjelde ALT som aktiveres etter
# utrullingen — også utkast som alt sto `validert` da den landet.
# --------------------------------------------------------------------------

#: Utkast med en verifikator-id som gjør diffstien flertydig. Nøyaktig formen
#: `valider_utkast` avviser i dag — men en rad kan ha fått `validert` FØR den
#: porten stilte kravet, og `_utkast` skriver akkurat en slik rad direkte.
_FLERTYDIG_INNHOLD = {
    "roller": [{"id": "r1"}],
    "verifikatorer": {"v_reg.beskrivelse": {"betrodd_for": ["fire_oyne"],
                                            "beskrivelse": "test"}}}


@pg
def test_gammelt_validert_utkast_kan_ikke_apne_runde():
    """Statusen `validert` er ingen kvittering på DAGENS krav.

    `valider_utkast` er en engangs-port: den kjørte den gangen raden gikk til
    `validert`, og statusen ble stående. Leser runde-åpningen bare status +
    hash, aktiveres utkastet uten noen gang å ha møtt kravet — og «gjelder
    framover» ville i praksis unntatt nøyaktig de utkastene som lander først
    etter utrullingen.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _FLERTYDIG_INNHOLD)           # status='validert'
    rt = _rt()
    try:
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _apne(rt, uid, a)
        assert e.value.kode == "utkast_ugyldig", e.value.kode
        assert "flertydig" in e.value.detalj, e.value.detalj
        rt.rollback()
    finally:
        rt.close()
    m = _mig()
    try:
        assert m.execute("SELECT count(*) FROM aktiveringsrunde WHERE tenant=%s"
                         " AND utkast_id=%s", (TEN, uid)).fetchone()[0] == 0, (
            "runden ble åpnet på et utkast som ikke kan aktiveres")
    finally:
        m.rollback(); m.close()
    assert _aktiv_versjon(pid) is None


@pg
def test_apen_runde_fra_for_utrullingen_kan_ikke_attesteres(monkeypatch):
    """Runde-åpningen alene er ikke nok: runden kan ha vært ÅPEN da kravet kom.

    Kontrollen nøytraliseres under åpningen for å gjenskape nøyaktig den
    tilstanden utrullingen arver. Da må attesteringen ta den — og ta den FØR
    signaturen skrives: en attestasjon på et utkast som ikke kan aktiveres er
    ingen godkjenning, bare et spor som må ryddes.

    Og runden må LUKKES (Codex P2). Kravet måler det frosne innholdet, så
    avslaget er permanent — rullet vi bare tilbake, sto runden `apen` og så
    levende ut: flaten tilbyr bare «attester», hvert forsøk gir samme feil, og
    `forkast_utkast` nekter et utkast med en levende runde. Eier sto fast til
    runden utløp av seg selv. Siste ledd her er derfor at hun kommer VIDERE.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _FLERTYDIG_INNHOLD)
    rt = _rt()
    try:
        monkeypatch.setattr(policyadmin, "_krev_innforingskrav",
                            lambda *_a, **_k: None)
        r = _apne(rt, uid, a)                  # runden fra «før utrullingen»
        monkeypatch.undo()                     # ... og så lander den
        svar = _attester(rt, uid, a, r["diff_hash"])
        assert svar["utfall"] == "utkast_ugyldig", svar
    finally:
        rt.close()
    m = _mig()
    try:
        assert m.execute(
            "SELECT count(*) FROM aktiveringsattestasjon WHERE tenant=%s AND"
            " utkast_id=%s", (TEN, uid)).fetchone()[0] == 0, (
            "signaturen ble skrevet på et utkast som ikke kan aktiveres")
        rstatus = m.execute(
            "SELECT status FROM aktiveringsrunde WHERE tenant=%s AND"
            " utkast_id=%s", (TEN, uid)).fetchone()[0]
    finally:
        m.rollback(); m.close()
    assert rstatus == "kansellert", rstatus
    assert _aktiv_versjon(pid) is None

    # Veien ut er åpen: uten kanselleringen nektet `forkast_utkast` fordi
    # runden var levende, og eier hadde ingen handling igjen på flaten.
    rt = _rt()
    try:
        idem = secrets.token_hex(8)
        f = policyadmin.forkast_utkast(
            rt, tenant=TEN, aktor=a, request_id="r", utkast_id=uid,
            forventet_utkastversjon=1, idempotency_key=idem, input_hash=idem,
            naa=_naa())
        assert f["utfall"] == "forkastet", f
    finally:
        rt.close()


@pg
def test_intern_valideringsfeil_kansellerer_ikke_runden(monkeypatch):
    """🔴 Codex P2: «validatoren er nede» er ingen dom over utkastet.

    Kanselleringen over hviler på at avslaget er PERMANENT — det frosne
    innholdet kan ikke rettes. Men kontrollen kan også feile fordi VI er nede:
    skjemafilen kan mangle eller være uleselig i en halvlandet utrulling, og
    `valider_innforingskrav` gjorde et hvilket som helst slikt avbrudd om til
    en oppføring i feillista. Da ble en reparerbar driftsfeil lest som et
    innholdsbrudd, og runden lukket for godt: retter man utrullingen, kommer
    runden ikke tilbake.

    `valider_innforingskrav_strengt` kaster i stedet, `_krev_innforingskrav`
    oversetter det til `valideringsfeil_intern` (HTTP 503), og
    `_FROSNE_DOKUMENTBRUDD` slipper bare de permanente kodene til
    kanselleringen.

    Kontroll: legg `valideringsfeil_intern` inn i `_FROSNE_DOKUMENTBRUDD`, så
    blir denne rød ved at runden er `kansellert` — og siste ledd, at eier
    kommer helt i mål etter reparasjonen, blir umulig.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    b = _medlem("uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _UTVIDER_INNHOLD)             # UTVIDER, pakrevd 2
    rt = _rt()
    try:
        r = _apne(rt, uid, a)

        def _nede(*_a, **_k):
            raise policyadmin._schema.ValideringUtilgjengelig(
                "FileNotFoundError: policy-schema-v0.2.json")

        monkeypatch.setattr(policyadmin._schema,
                            "valider_innforingskrav_strengt", _nede)
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _attester(rt, uid, a, r["diff_hash"])
        assert e.value.kode == "valideringsfeil_intern", e.value.kode
        rt.rollback()
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
    assert rstatus == "apen", (
        "en reparerbar driftsfeil lukket en runde som var helt i orden")
    assert antall == 0, "ingen signatur skal være skrevet"

    # Og reparasjonen holder: utrullingen rettes, og runden går helt i mål.
    monkeypatch.undo()
    rt = _rt()
    try:
        assert _attester(rt, uid, a, r["diff_hash"])["utfall"] \
            == "venter_godkjennere"
        assert _attester(rt, uid, b, r["diff_hash"])["utfall"] == "aktivert"
    finally:
        rt.close()
    assert _aktiv_versjon(pid) == "1.1.0"


@pg
def test_lovlig_utkast_slipper_gjennom_den_nye_porten():
    """Motprøven: porten avviser IKKE et utkast med en entydig verifikator-id.
    Uten den ville en for bred kontroll (hele lastekontrakten) stanset all
    aktivering — også det som er helt i orden."""
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, {"roller": [{"id": "r1"}],
                          "verifikatorer": {"v_reg": {"betrodd_for": [],
                                                      "beskrivelse": "t"}}})
    rt = _rt()
    try:
        assert _apne(rt, uid, a)["runde"] == 1
        rt.rollback()
    finally:
        rt.close()


@pg
def test_ferdig_attestert_runde_fra_for_utrullingen_aktiverer_ikke(monkeypatch):
    """🔴 Codex P2 på #63: den herdede grensen må bære kravet, ikke bare Python.

    Runde-åpning og attestering er BEGGE passert i det utrullingen lander på en
    runde som alt hadde nok signaturer (f.eks. bevart fra et forsøk som stoppet
    på en usynk peker). Da er det bare `aktiver_policy` igjen — og den er
    dessuten grantet direkte til runtime-rollen, så et kall utenom
    orkestreringen ender samme sted. Migrasjon 022 stopper det der.

    Utfallet må dessuten si hva som er galt: kontrollen deler SQLSTATE med
    versjonsinvariantene, og uten `CONSTRAINT`-skillet ville eier fått
    «versjonen er i bruk» om en verifikator-id.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    b = _medlem("uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _FLERTYDIG_INNHOLD)
    rt = _rt()
    try:
        # Hele Python-veien er «fra før utrullingen»: runden åpnes og begge
        # signaturene avgis uten at kravet noen gang ble stilt.
        monkeypatch.setattr(policyadmin, "_krev_innforingskrav",
                            lambda *_a, **_k: None)
        r = _apne(rt, uid, a)
        assert _attester(rt, uid, a, r["diff_hash"])["utfall"] \
            == "venter_godkjennere"
        # Terskelen nås → `aktiver_policy` kalles → DB-grensen tar den.
        siste = _attester(rt, uid, b, r["diff_hash"])
        assert siste["utfall"] == "utkast_ugyldig", siste
    finally:
        rt.close()

    assert _aktiv_versjon(pid) is None, "utkastet ble aktivert"
    m = _mig()
    try:
        assert m.execute(
            "SELECT count(*) FROM policyer WHERE tenant=%s AND policy_id=%s",
            (TEN, pid)).fetchone()[0] == 0
        rstatus = m.execute("SELECT status FROM aktiveringsrunde WHERE tenant=%s"
                            " AND utkast_id=%s", (TEN, uid)).fetchone()[0]
        antall_attest = m.execute(
            "SELECT count(*) FROM aktiveringsattestasjon WHERE tenant=%s AND"
            " utkast_id=%s", (TEN, uid)).fetchone()[0]
        ustatus = m.execute("SELECT status FROM policyutkast WHERE tenant=%s AND"
                            " utkast_id=%s", (TEN, uid)).fetchone()[0]
    finally:
        m.rollback(); m.close()
    # Runden er beviselig død (innholdet er frosset) → kansellert med det
    # samme, men signaturene består: de er sporet av hva som ble godkjent.
    assert rstatus == "kansellert", rstatus
    assert antall_attest == 2
    assert ustatus == "validert", ustatus


# --------------------------------------------------------------------------
# Codex P2 på denne PR-en: ankerkravet er også et framoverrettet krav, og også
# det må stå i SQL — ikke bare i `schema._valider_innforing`.
# --------------------------------------------------------------------------

#: Handlings-id med en avsluttende linjeskift. Skjemagyldig for Pythons `re`
#: (`$` matcher rett FØR en avsluttende linjeskift), ulesbar for alle andre:
#: `engine.les_policyref` måler med `fullmatch`, og databasens `~` leser `$`
#: som ekte slutt.
_ANKERHALE_INNHOLD = {
    "roller": [{"id": "r1"}],
    "handlinger": [{"id": "faktura.send\n"}]}


@pg
def test_ankerhale_stoppes_av_db_grensen_nar_python_porten_er_passert(monkeypatch):
    """🔴 Codex P2: `_valider_innforing` fikk ankerkravet — SQL-grensen ikke.

    Samme to hull som verifikator-id-kravet: et utkast kan ha blitt validert og
    fullt attestert FØR utrullingen, og `aktiver_policy` er grantet direkte til
    runtime-rollen. Slapp halen gjennom her, ble policyen AKTIVERT — og først
    da oppdages det: `les_policyref` klarer ikke lese
    `<policy_id>@<versjon>/<handling>`, så hver beslutning under policyen
    produserer evidens uten policyidentitet.

    Utfallet må dessuten være `utkast_ugyldig` og ikke `versjon_i_bruk`:
    kontrollen deler SQLSTATE med versjonsinvariantene fra 020, og bare
    `CONSTRAINT`-navnet skiller dem.

    Kontroll: fjern 4d fra migrasjon 025, så aktiveres utkastet.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    b = _medlem("uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _ANKERHALE_INNHOLD)
    rt = _rt()
    try:
        monkeypatch.setattr(policyadmin, "_krev_innforingskrav",
                            lambda *_a, **_k: None)
        r = _apne(rt, uid, a)
        assert _attester(rt, uid, a, r["diff_hash"])["utfall"] \
            == "venter_godkjennere"
        siste = _attester(rt, uid, b, r["diff_hash"])
        assert siste["utfall"] == "utkast_ugyldig", siste
    finally:
        rt.close()
    assert _aktiv_versjon(pid) is None, "utkastet ble aktivert"


@pg
def test_db_grensen_maler_bare_differansen_ikke_hele_lastekontrakten():
    """Motprøven, og den er hele grunnen til at 4d er skrevet som den er.

    `h1` mangler punktumet mønsteret krever og feiler BEGGE lesningene — det er
    en helt vanlig skjemafeil som lastekontrakten sier fra om ved validering.
    Målte SQL-grensen hele mønsteret i stedet for differansen, ville en runde
    blitt KANSELLERT med «bryter et nytt krav» for et dokument som ganske
    enkelt er strukturelt ødelagt. Det er nøyaktig sammenblandingen
    innførings- og lastekontrakten ble delt i to for å unngå, og den samme
    feilen `_pattern_ecma` alt er rettet for på Python-siden.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    b = _medlem("uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _UTVIDER_INNHOLD)            # handlinger[].id = 'h1'
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        assert _attester(rt, uid, a, r["diff_hash"])["utfall"] \
            == "venter_godkjennere"
        assert _attester(rt, uid, b, r["diff_hash"])["utfall"] == "aktivert"
    finally:
        rt.close()
    assert _aktiv_versjon(pid) == "1.1.0"


@pg
def test_apen_runde_varsler_dem_som_kan_bringe_den_videre():
    """🔴 P1 (Codex): åpningen av en runde må FAKTISK varsle.

    En åpen runde venter på et menneske, og fram til nå fikk hun aldri vite
    det — i praksis måtte eier si fra utenom systemet. Tjenestelaget fantes,
    men ingen produksjonsvei kalte det: `varsle_runde_venter` var bare
    referert fra sin egen modul og sin egen test, så flyten skrev aldri en
    eneste `varsel`-rad.

    Testen går den EKTE veien — runtime-rollen, `opprett_aktiveringsrunde`,
    committet — så den måler samtidig at rollen har rettighetene den trenger.

    Kontroll: fjern `varsel.varsle_runde_venter`-kallet i
    `opprett_aktiveringsrunde`, så blir denne rød.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("varsel-forf", ["policyforvalter"])
    b = _medlem("varsel-uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _UTVIDER_INNHOLD)
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        assert r["runde"] == 1
        assert r["pakrevd_antall_godkjennere"] == 2
    finally:
        rt.close()

    m = _mig()
    try:
        rader = {x[0]: x for x in m.execute(
            "SELECT bruker_id, art, hendelse, tekstnokkel, parametre,"
            " epost_status FROM varsel WHERE tenant=%s"
            " AND ressurs_type='policyutkast' AND ressurs_id=%s",
            (TEN, uid)).fetchall()}
    finally:
        m.rollback(); m.close()

    # Begge kan bringe runden videre: ingen har attestert ennå, og forfatteren
    # teller — hun kan bare ikke fullføre fire øyne alene.
    assert {a, b} <= set(rader), (
        f"runden ble åpnet uten å varsle noen; mottakere={set(rader)}")
    _, art, hendelse, nokkel, param, epost = rader[b]
    assert art == "attestering_venter"
    assert hendelse == "1", "rundenummeret må være varselets hendelsesidentitet"
    assert nokkel == "varsel.attestering_venter", (
        "teksten lagres ikke — bare nøkkelen, så varselet kan leses på "
        "MOTTAKERENS språk")
    assert param["policy_id"] == pid and param["runde"] == 1
    assert param["gjenstaar"] == 2
    assert epost == "koet", "standardvalget er e-post OG portal"


@pg
def test_replay_forsoner_en_varsling_som_feilet(monkeypatch):
    """Codex P2: en feilet varsling var ENDELIG.

    Varslingen er best effort med vilje — en fullmaktsendring skal ikke kunne
    velte fordi varslingen gjorde det. Men prisen var at feilen ikke kunne
    repareres av noe som helst: runden ble committet, den sto åpen og ventet
    på godkjennere som aldri fikk beskjed, og klienten som prøvde på nytt med
    samme idempotensnøkkel gikk ut i `replay`-grenen FØR varslingen ble
    forsøkt. Retryen som skulle vært reparasjonen, hoppet over den.

    Her feiler varslingen på første forsøk (samme utfall som en transient
    databasefeil: savepointet rulles tilbake, runden committes likevel), og
    retryen med SAMME nøkkel og input skal opprette det som mangler.

    Kontroll: fjern `_forson_rundevarsling`-kallet i replay-grenen, så blir
    denne rød — ingen varsler etter retryen.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forson-forf", ["policyforvalter"])
    b = _medlem("forson-uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _UTVIDER_INNHOLD)

    # Første forsøk: varslingen gjør ingenting, som om hvert steg hadde feilet
    # og blitt rullet tilbake til savepointet sitt.
    ekte = policyadmin.varsel.varsle_runde_venter
    monkeypatch.setattr(policyadmin.varsel, "varsle_runde_venter",
                        lambda *a, **k: 0)
    idem = secrets.token_hex(8)
    ih = f"{TEN}\x1f{uid}\x1fapne\x1f{idem}"
    rt = _rt()
    try:
        r1 = policyadmin.opprett_aktiveringsrunde(
            rt, tenant=TEN, utkast_id=uid, aktor=a, request_id="r",
            idempotency_key=idem, input_hash=ih, naa=_naa())
    finally:
        rt.close()
    assert r1["runde"] == 1
    assert _varsler(uid) == {}, "forutsetningen holder ikke: noe ble varslet"

    # Retry med samme nøkkel og input. Svaret er det lagrede — men hullet
    # lukkes.
    monkeypatch.setattr(policyadmin.varsel, "varsle_runde_venter", ekte)
    rt = _rt()
    try:
        r2 = policyadmin.opprett_aktiveringsrunde(
            rt, tenant=TEN, utkast_id=uid, aktor=a, request_id="r2",
            idempotency_key=idem, input_hash=ih, naa=_naa())
    finally:
        rt.close()
    assert r2.get("replay") is True, "retryen åpnet en NY runde"
    assert r2["runde"] == r1["runde"] and r2["diff_hash"] == r1["diff_hash"]

    etter = _varsler(uid)
    assert {a, b} <= set(etter), (
        f"replayen forsonet ikke varslingen; mottakere={set(etter)}")
    assert all(lest is None for lest, _ in etter.values()), \
        "et forsonet varsel skal være ULEST — det venter fortsatt"


@pg
def test_replay_varsler_ikke_om_en_runde_som_ikke_lenger_venter(monkeypatch):
    """Forsoningen skal reparere et hull, ikke lage en ny løgn.

    Er runden aktivert, kansellert eller forfalt i mellomtiden, venter den ikke
    på noen — og et varsel opprettet da ville bedt godkjennere om å attestere
    noe som ikke kan attesteres. Samme predikat som skrive- og lesestien
    (`_runde_status`), så de tre aldri blir uenige om hva «forfalt» betyr.

    Kontroll: fjern `_runde_status`-sjekken i `_forson_rundevarsling`, så blir
    denne rød.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forfall-forf", ["policyforvalter"])
    _medlem("forfall-uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _UTVIDER_INNHOLD)

    ekte = policyadmin.varsel.varsle_runde_venter
    monkeypatch.setattr(policyadmin.varsel, "varsle_runde_venter",
                        lambda *a, **k: 0)
    idem = secrets.token_hex(8)
    ih = f"{TEN}\x1f{uid}\x1fapne\x1f{idem}"
    rt = _rt()
    try:
        policyadmin.opprett_aktiveringsrunde(
            rt, tenant=TEN, utkast_id=uid, aktor=a, request_id="r",
            idempotency_key=idem, input_hash=ih, naa=_naa())
    finally:
        rt.close()

    # Runden forfaller før retryen kommer.
    m = _mig()
    try:
        m.execute("UPDATE aktiveringsrunde SET utloper=now() - interval '1 h'"
                  " WHERE tenant=%s AND utkast_id=%s AND runde=1", (TEN, uid))
        m.commit()
    finally:
        m.close()

    monkeypatch.setattr(policyadmin.varsel, "varsle_runde_venter", ekte)
    rt = _rt()
    try:
        policyadmin.opprett_aktiveringsrunde(
            rt, tenant=TEN, utkast_id=uid, aktor=a, request_id="r2",
            idempotency_key=idem, input_hash=ih, naa=_naa())
    finally:
        rt.close()

    assert _varsler(uid) == {}, (
        "forsoningen varslet om en runde som ikke lenger venter på noen")


@pg
def test_forsoningen_holder_runden_laast_gjennom_varselopprettelsen(monkeypatch):
    """Codex P2: «åpen» var bare sant i det øyeblikket spørringen svarte.

    Forsoningen leste rundens status og satte DERETTER inn varslene. I vinduet
    imellom kunne den siste attesteringen lukke runden og kjøre
    `pensjoner_runde` — som ikke traff noe, fordi radene ennå ikke fantes. For
    nettopp de mottakerne forsoningen finnes for (den opprinnelige raden
    MANGLER) kan `ON CONFLICT` heller ikke fange dem, så de satt igjen med et
    ulest, e-postkøet varsel om en runde som var ferdig. Veien som skulle
    reparere en løgn, laget en ny.

    Målt slik det virker: forsoningen kjøres på én forbindelse UTEN å committe,
    og en annen forbindelse prøver å ta rundens rad `FOR UPDATE NOWAIT` — den
    låsen attesteringen tar i steg 4. Blir den nektet, kan ingen attestering
    lukke runden før varslene er på plass og committet.

    `NOWAIT` og ikke en tråd med tidsavbrudd: en test som VENTER på en lås
    beviser bare at noe tok tid. Denne svarer ja eller nei med det samme.

    Kontroll: ta `FOR UPDATE` ut av `_les_under_laas`, og NOWAIT-kallet går
    gjennom.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("laas-forf", ["policyforvalter"])
    _medlem("laas-uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _UTVIDER_INNHOLD)

    # Åpningen med varslingen slått av: hullet forsoningen skal fylle.
    monkeypatch.setattr(policyadmin.varsel, "varsle_runde_venter",
                        lambda *a, **k: 0)
    idem = secrets.token_hex(8)
    ih = f"{TEN}\x1f{uid}\x1fapne\x1f{idem}"
    rt = _rt()
    try:
        lagret = policyadmin.opprett_aktiveringsrunde(
            rt, tenant=TEN, utkast_id=uid, aktor=a, request_id="r",
            idempotency_key=idem, input_hash=ih, naa=_naa())
    finally:
        rt.close()
    assert _varsler(uid) == {}, "forutsetningen holder ikke: noe ble varslet"
    monkeypatch.undo()

    from db.pg import sett_kontekst
    forsoner = _rt()
    annen = _rt()
    try:
        sett_kontekst(forsoner, TEN, a, "r-forson")
        antall = policyadmin._forson_rundevarsling(
            forsoner, TEN, a, "r-forson", lagret, _naa())
        assert antall > 0, "forsoningen opprettet ingen varsler å verne om"

        sett_kontekst(annen, TEN, a, "r-annen")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            annen.execute(
                "SELECT 1 FROM aktiveringsrunde WHERE tenant=%s"
                " AND utkast_id=%s AND runde=1 FOR UPDATE NOWAIT",
                (TEN, uid)).fetchone()
        annen.rollback()
        forsoner.commit()

        # …og etter commiten er runden fri igjen: låsen varer så lenge den
        # trengs, ikke lenger.
        sett_kontekst(annen, TEN, a, "r-annen2")
        assert annen.execute(
            "SELECT 1 FROM aktiveringsrunde WHERE tenant=%s"
            " AND utkast_id=%s AND runde=1 FOR UPDATE NOWAIT",
            (TEN, uid)).fetchone() is not None
        annen.rollback()
    finally:
        forsoner.close()
        annen.close()

    assert set(_varsler(uid)), "forsoningen etterlot ingen varsler"


def _varsler(uid, runde=1):
    """{bruker_id: (lest_ts, epost_status)} for rundens varsler."""
    m = _mig()
    try:
        return {r[0]: (r[1], r[2]) for r in m.execute(
            "SELECT bruker_id, lest_ts, epost_status FROM varsel"
            " WHERE tenant=%s AND ressurs_type='policyutkast'"
            " AND ressurs_id=%s AND hendelse=%s",
            (TEN, uid, str(runde))).fetchall()}
    finally:
        m.rollback(); m.close()


@pg
def test_attestering_pensjonerer_aktoerens_eget_varsel():
    """Codex P2: et varsel skal slutte å vente når handlingen er gjort.

    Varselet er en OPPFORDRING — «attesteringen venter på deg» — ikke en
    kvittering. I den vanligste flyten attesterer forfatteren rett etter at hun
    har åpnet runden, og uten dette ber innboksen hennes om at hun skal gjøre
    det hun nettopp gjorde. En innboks som lyver om hva som venter, blir en
    innboks folk slutter å se på.

    Men KUN hennes rad: runden venter fortsatt på den uavhengige godkjenneren,
    og hans varsel skal stå urørt. Det er den grensen som gjør dette til en
    pensjonering og ikke en tømming.

    Kontroll: fjern kallet i steg 7c, så blir første assert rød; utvid det til
    hele runden (dropp `bruker_id`), så blir den andre det.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("pens-forf", ["policyforvalter"])
    b = _medlem("pens-uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _UTVIDER_INNHOLD)
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        assert _varsler(uid)[a][0] is None, "forfatteren ble varslet — og venter"
        r1 = _attester(rt, uid, a, r["diff_hash"])
        assert r1["utfall"] == "venter_godkjennere", r1
    finally:
        rt.close()

    v = _varsler(uid)
    assert v[a][0] is not None, (
        "forfatteren har attestert, men innboksen hennes ber henne fortsatt "
        "om å attestere")
    assert v[a][1] == "ikke_aktuelt", (
        "varselet er ryddet, men e-posten står fortsatt i kø — senderen ville "
        "bedt henne om det samme i den kanalen hun ikke kan lukke selv")
    assert v[b][0] is None, (
        "den uavhengige godkjenneren fikk varselet sitt ryddet bort av en "
        "ANNENS attestering — runden venter fortsatt på ham")
    assert v[b][1] == "koet"


@pg
def test_gjenstaaende_attesteringer_telles_ned_i_varslene():
    """Codex P2: «{gjenstaar} attestasjon(er) gjenstår» må fortsatt være sant.

    Parametrene ble skrevet én gang, da runden åpnet. Krever runden to
    godkjenninger, sto det `2` i hvert varsel for alltid — også etter at
    forfatteren attesterte. Den uavhengige godkjenneren, som er den eneste
    som faktisk kan bringe runden videre, leste da at to gjenstår når det bare
    var hans egen igjen. Tallet skiller «du er den siste» fra «dette kan
    vente», og e-posten sier det samme: den rendres fra de samme parametrene,
    ved sending.

    Forfatterens eget varsel er pensjonert av steg 7c og skal IKKE skrives om
    — et lest varsel er historie.

    Kontroll: fjern `varsel.oppdater_gjenstaar`-kallet i steg 8, så blir denne
    rød med `gjenstaar == 2` i det varselet som fortsatt venter.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("teller-forf", ["policyforvalter"])
    b = _medlem("teller-uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _UTVIDER_INNHOLD)
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        assert r["pakrevd_antall_godkjennere"] == 2
        assert _attester(rt, uid, a, r["diff_hash"])["utfall"] == (
            "venter_godkjennere")
    finally:
        rt.close()

    m = _mig()
    try:
        param = {x[0]: x[1] for x in m.execute(
            "SELECT bruker_id, parametre FROM varsel WHERE tenant=%s"
            " AND ressurs_type='policyutkast' AND ressurs_id=%s"
            " AND hendelse='1'", (TEN, uid)).fetchall()}
    finally:
        m.rollback(); m.close()

    assert param[b]["gjenstaar"] == 1, (
        "godkjenneren som er den siste som gjenstår, blir fortalt at to "
        f"attesteringer gjenstår: {param[b]}")
    assert param[a]["gjenstaar"] == 2, (
        "forfatterens alt leste varsel ble skrevet om under henne")


@pg
def test_manglende_uavhengig_attestasjon_telles_som_gjenstaaende():
    """Codex P2: nedtellingen må telle BEGGE betingelsene i terskelen.

    En `INNSNEVRER`-runde krever bare én attestasjon — men den må komme fra en
    som ikke er forfatter. Attesterer forfatteren først, blir `pakrevd - antall`
    null mens runden fortsatt står åpen og venter på nøyaktig den ene personen
    som ennå ikke har svart. Han leste da at null attestasjoner gjenstår, og
    e-posten hans sa det samme.

    Kontroll: bytt `_gjenstaar_effektivt` i steg 8 tilbake til
    `max(0, r_pakrevd - antall)`, så blir denne rød med `gjenstaar == 0` både i
    svaret og i varselet til den uavhengige godkjenneren.
    """
    pid = "pol-" + secrets.token_hex(3)
    _aktiv_base(pid, {"roller": [{"id": "r1"}, {"id": "r2"}]})
    a = _medlem("uavh-forf", ["policyforvalter"])
    b = _medlem("uavh-godk", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, {"roller": [{"id": "r1"}]})   # fjerner r2 → INNSNEVRER
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        assert r["pakrevd_antall_godkjennere"] == 1
        r1 = _attester(rt, uid, a, r["diff_hash"])
        assert r1["utfall"] == "venter_godkjennere"
        assert r1["mangler_uavhengig"] is True
        assert r1["gjenstaar"] == 1, (
            "svaret sier at ingenting gjenstår, men runden venter fortsatt "
            f"på en uavhengig godkjenner: {r1}")
    finally:
        rt.close()

    m = _mig()
    try:
        param = {x[0]: x[1] for x in m.execute(
            "SELECT bruker_id, parametre FROM varsel WHERE tenant=%s"
            " AND ressurs_type='policyutkast' AND ressurs_id=%s"
            " AND hendelse='1'", (TEN, uid)).fetchall()}
    finally:
        m.rollback(); m.close()

    assert param[b]["gjenstaar"] == 1, (
        "den uavhengige godkjenneren — den eneste som kan bringe runden "
        f"videre — får beskjed om at ingenting gjenstår: {param[b]}")


@pg
def test_aktivering_pensjonerer_hele_rundens_varsler():
    """Når runden er brukt, venter den ikke på noen — heller ikke på dem som
    aldri rakk å svare.

    Uten dette blir hvert gjenstående varsel stående ulest for alltid, og
    eneste vei ut er at hver enkelt trykker «merk som lest» på et varsel om noe
    som ikke finnes lenger.

    Kontroll: fjern `pensjoner_runde`-kallet etter `RELEASE SAVEPOINT
    aktiveringsforsok`, så blir denne rød.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("brukt-forf", ["policyforvalter"])
    b = _medlem("brukt-uavh", ["policyforvalter"])
    c = _medlem("brukt-taus", ["policyforvalter"])   # svarer aldri
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _UTVIDER_INNHOLD)
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        _attester(rt, uid, a, r["diff_hash"])
        assert _attester(rt, uid, b, r["diff_hash"])["utfall"] == "aktivert"
    finally:
        rt.close()

    v = _varsler(uid)
    assert {a, b, c} <= set(v), f"forventet varsel til alle tre, fikk {set(v)}"
    for bid, hvem in ((a, "forfatteren"), (b, "godkjenneren"),
                      (c, "den som aldri svarte")):
        assert v[bid][0] is not None, (
            f"{hvem} har fortsatt et ulest varsel om en runde som er brukt")
        assert v[bid][1] == "ikke_aktuelt", (
            f"e-posten til {hvem} står fortsatt i kø etter at runden er lukket")


@pg
def test_replay_forsoner_en_pensjonering_som_feilet(monkeypatch):
    """Codex P2: en feilet pensjonering var ENDELIG.

    Motstykket til forsoningen av VARSLINGEN, og samme mekanikk: oppryddingen
    er skjermet med vilje — en fullmaktsendring skal ikke velte fordi den
    feilet — men runden ble aktivert og committet likevel, mens godkjennernes
    uleste, e-postkøede varsler ble stående og be dem attestere noe som var
    ferdig. Klientens retry kunne ikke reparere det: replay-grenen svarte med
    det lagrede utfallet før noen pensjonering ble forsøkt. Og senderen fanger
    det ikke opp — den er kryss-tenant og vet med vilje ingenting om runder,
    så e-posten går ut.

    Her feiler pensjoneringen på den attesteringen som aktiverer (samme utfall
    som en transient databasefeil: savepointet rulles tilbake, runden
    aktiveres likevel), og retryen med SAMME nøkkel og input skal rydde det
    som ble stående.

    Kontroll: fjern `_forson_rundepensjonering`-kallet i replay-grenen, så
    blir denne rød — varslene står igjen uleste og køet.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forsonp-forf", ["policyforvalter"])
    b = _medlem("forsonp-uavh", ["policyforvalter"])
    c = _medlem("forsonp-taus", ["policyforvalter"])   # svarer aldri
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _UTVIDER_INNHOLD)

    idem = secrets.token_hex(8)
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        # Pensjoneringen gjør ingenting, som om hvert kall hadde feilet og
        # blitt rullet tilbake til savepointet sitt. Den slås av FØR
        # forfatterens attestering: steg 7c rydder hennes eget varsel, og et
        # hull som allerede var lukket kunne ikke skilt fiksen fra fraværet.
        monkeypatch.setattr(policyadmin.varsel, "pensjoner_runde",
                            lambda *a, **k: 0)
        _attester(rt, uid, a, r["diff_hash"])
        assert _attester(rt, uid, b, r["diff_hash"],
                         idem=idem)["utfall"] == "aktivert"
    finally:
        rt.close()

    v = _varsler(uid)
    assert all(lest is None for lest, _ in v.values()), (
        "forutsetningen holder ikke: noe ble pensjonert likevel")

    # Retry med samme nøkkel og input. Svaret er det lagrede — men køen ryddes.
    monkeypatch.undo()
    rt = _rt()
    try:
        r2 = _attester(rt, uid, b, r["diff_hash"], idem=idem)
    finally:
        rt.close()
    assert r2["utfall"] == "aktivert", "retryen gjorde noe annet enn å replaye"

    etter = _varsler(uid)
    assert {a, b, c} <= set(etter), f"varsler forsvant: {set(etter)}"
    for bid, hvem in ((a, "forfatteren"), (b, "godkjenneren"),
                      (c, "den som aldri svarte")):
        assert etter[bid][0] is not None, (
            f"{hvem} har fortsatt et ulest varsel om en runde som er brukt")
        assert etter[bid][1] == "ikke_aktuelt", (
            f"e-posten til {hvem} står fortsatt i kø etter replayen")


@pg
def test_replay_pensjonerer_ikke_en_runde_som_fortsatt_venter(monkeypatch):
    """Forsoningen skal rydde et hull, ikke rive et levende varsel bort.

    Venter runden fortsatt på andre godkjennere, er deres varsler SANNE — det
    er bare aktørens eget som er ferdig (steg 7c). En forsoning som pensjonerte
    hele runden her ville gjort innboksen taus om noe som faktisk venter, som
    er den samme feilen som en innboks som lyver — bare motsatt vei.

    Kontroll: bytt `bruker_id=aktor`-grenen i `_forson_rundepensjonering` med
    en full pensjonering, så blir denne rød.
    """
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forsonv-forf", ["policyforvalter"])
    b = _medlem("forsonv-uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _UTVIDER_INNHOLD)

    idem = secrets.token_hex(8)
    rt = _rt()
    try:
        r = _apne(rt, uid, a)
        monkeypatch.setattr(policyadmin.varsel, "pensjoner_runde",
                            lambda *a, **k: 0)
        # Forfatteren attesterer; runden venter fortsatt på den uavhengige.
        assert _attester(rt, uid, a, r["diff_hash"],
                         idem=idem)["utfall"] == "venter_godkjennere"
    finally:
        rt.close()
    assert all(lest is None for lest, _ in _varsler(uid).values())

    monkeypatch.undo()
    rt = _rt()
    try:
        _attester(rt, uid, a, r["diff_hash"], idem=idem)
    finally:
        rt.close()

    etter = _varsler(uid)
    assert etter[a][0] is not None, \
        "aktørens eget varsel ble ikke ryddet av forsoningen"
    assert etter[b][0] is None, (
        "forsoningen pensjonerte et varsel om en runde som fortsatt venter"
        " på nettopp den godkjenneren")
    assert etter[b][1] == "koet", "e-posten til den som venter ble avlyst"


@pg
def test_forfalt_runde_pensjonerer_varslene_sine():
    """Samme regel på den stille veien ut: en runde som forfaller.

    Ingen handling utløser den — den skjer fordi tiden gikk — så uten
    pensjonering her er det nettopp de rundene ingen fulgte opp som blir
    stående og maser i innboksen for alltid.

    Kontroll: fjern `pensjoner_runde`-kallet i `_lukk_forfalt_runde`.
    """
    from datetime import timedelta
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("utl-forf", ["policyforvalter"])
    b = _medlem("utl-uavh", ["policyforvalter"])
    uid = "utk-" + secrets.token_hex(3)
    _utkast(uid, pid, a, _UTVIDER_INNHOLD)
    rt = _rt()
    try:
        _apne(rt, uid, a)
        assert all(x[0] is None for x in _varsler(uid).values())
        # Forfallet oppdages av neste handling på utkastet — her forkastingen,
        # som er den ENESTE veien eier har til å rydde et dødt forslag.
        uv = _mig()
        try:
            versjon = uv.execute(
                "SELECT utkastversjon FROM policyutkast WHERE tenant=%s AND"
                " utkast_id=%s", (TEN, uid)).fetchone()[0]
        finally:
            uv.rollback(); uv.close()
        idem = secrets.token_hex(8)
        policyadmin.forkast_utkast(
            rt, tenant=TEN, aktor=a, request_id="r", utkast_id=uid,
            forventet_utkastversjon=versjon, idempotency_key=idem,
            input_hash=idem, naa=_naa() + timedelta(days=30))
    finally:
        rt.close()

    v = _varsler(uid)
    for bid, hvem in ((a, "forfatteren"), (b, "godkjenneren")):
        assert v[bid][0] is not None, (
            f"runden forfalt, men {hvem} blir bedt om å attestere den fortsatt")
        assert v[bid][1] == "ikke_aktuelt"


# ---------------------------------------------------------------------------
# M-38 — valideringsmemoisering i `hent_aktiv` (portene a, b, d, e).
# Hjemmet er valgt fordi lastekontrakten til `hent_aktiv` allerede testes
# her med FULLGYLDIGE policyer (se identitetsporten over); port (c) —
# sletting gir PolicyUkjent, intet gjenferd — bor i test_slett_policy, der
# sletteflaten testes.
# ---------------------------------------------------------------------------

def _tellende_validator(monkeypatch):
    """Teller kallene til `valider_policy` uten å endre svaret.

    `hent_aktiv` importerer funksjonen fra `policy_validator.schema` ved
    hvert kall, så en patch på modulattributtet er den faktiske veien inn.
    """
    import policy_validator.schema as skjema
    ekte = skjema.valider_policy
    teller = {"n": 0}

    def talt(policy):
        teller["n"] += 1
        return ekte(policy)

    monkeypatch.setattr(skjema, "valider_policy", talt)
    return teller


@pg
def test_m38_andre_lesning_kaller_ikke_valider_policy(monkeypatch):
    """M-38 port (a): identiske bytes måles mot skjemaet ÉN gang.

    Alt annet i lastekontrakten kjøres begge gangene — det er bare
    skjemavandringen som memoiseres, nøklet på den rekomputerte hashen.
    """
    from db.pg import sett_kontekst
    from .test_bootstrap_ankerrad import _policy
    pid = "pol-" + secrets.token_hex(3)
    m = _mig()
    try:
        pr.registrer(m, TEN, _policy("1.0.0", policy_id=pid), "produksjon")
        m.commit()
        teller = _tellende_validator(monkeypatch)
        sett_kontekst(m, TEN, "sys", "r1")
        forste = pr.hent_aktiv(m, TEN, pid)
        m.rollback()
        assert teller["n"] == 1, "første lesning skal måle skjemaet"
        sett_kontekst(m, TEN, "sys", "r2")
        andre = pr.hent_aktiv(m, TEN, pid)
        m.rollback()
        assert teller["n"] == 1, \
            "andre lesning av samme innhold skal treffe memoiseringen"
        assert andre == forste, "treffet skal ikke endre svaret"
    finally:
        m.rollback()
        m.close()


@pg
def test_m38_aktivering_synes_umiddelbart_i_hent_aktiv():
    """M-38 port (b): styrt aktivering → NESTE `hent_aktiv` gir ny versjon.

    Cachen varmes med den gamle versjonen FØR runden, nettopp for å bevise
    at det ikke finnes noe å invalidere: nytt innhold er en ny nøkkel, og
    radlesningen i samme transaksjon ser den nye raden.
    """
    from db.pg import sett_kontekst
    from .test_bootstrap_ankerrad import _policy
    pid = "pol-" + secrets.token_hex(3)
    a = _medlem("forf", ["policyforvalter"])
    b = _medlem("uavh", ["policyforvalter"])

    m = _mig()
    try:
        pr.registrer(m, TEN, _policy("1.0.0", policy_id=pid), "produksjon")
        m.commit()
        sett_kontekst(m, TEN, "sys", "r1")
        gammel, _ = pr.hent_aktiv(m, TEN, pid)      # varmer memoiseringen
        m.rollback()
        assert gammel["meta"]["versjon"] == "1.0.0"

        ny = _policy("1.1.0", policy_id=pid)
        ny["roller"] = [*ny["roller"], {"id": "agent2"}]
        uid = "utk-" + secrets.token_hex(3)
        _utkast(uid, pid, a, ny)
        rt = _rt()
        try:
            r = _apne(rt, uid, a)
            _attester(rt, uid, a, r["diff_hash"])
            r2 = _attester(rt, uid, b, r["diff_hash"])
            assert r2["utfall"] == "aktivert", r2
        finally:
            rt.close()

        sett_kontekst(m, TEN, "sys", "r2")
        innhold, _h = pr.hent_aktiv(m, TEN, pid)
        m.rollback()
        assert innhold["meta"]["versjon"] == "1.1.0", \
            "hent_aktiv serverte en foreldet versjon etter aktivering"
    finally:
        m.rollback()
        m.close()


@pg
def test_m38_rekomputeringsporten_star_etter_memoisering():
    """M-38 port (d): korrupt innhold med uendret hash-kolonne felles ENNÅ.

    v2 1.5-kontrakten: hashen REKOMPUTERES fra innholdet ved hver lasting
    og måles mot kolonnen — memoiseringen står bak den porten, aldri foran.
    At policyen alt lå i cachen da korrupsjonen skjedde, hjelper den ikke.
    """
    from db.pg import sett_kontekst
    from .test_bootstrap_ankerrad import _policy
    pid = "pol-" + secrets.token_hex(3)
    m = _mig()
    try:
        pr.registrer(m, TEN, _policy("1.0.0", policy_id=pid), "produksjon")
        m.commit()
        sett_kontekst(m, TEN, "sys", "r1")
        pr.hent_aktiv(m, TEN, pid)                  # varmer memoiseringen
        m.rollback()

        # DB-korrupsjon: innholdet endres, hash-kolonnen står urørt.
        sett_kontekst(m, TEN, "sys", "r2")
        m.execute(
            "UPDATE policyer SET innhold ="
            " jsonb_set(innhold, '{tidssone}', '\"Mars/Olympus\"')"
            " WHERE tenant=%s AND policy_id=%s AND aktiv", (TEN, pid))
        m.commit()

        sett_kontekst(m, TEN, "sys", "r3")
        with pytest.raises(pr.PolicyKorrupt) as e:
            pr.hent_aktiv(m, TEN, pid)
        assert "innholds_hash" in str(e.value), str(e.value)
    finally:
        m.rollback()
        m.close()


@pg
def test_m38_negativ_validering_memoiseres_aldri(monkeypatch):
    """M-38 port (e): et feilende innhold re-måles ved HVER lasting.

    Bare beståtte valideringer legges inn — feillisten i `PolicyKorrupt`
    skal alltid være fersk og komplett, aldri et memoisert ekko.
    """
    from db.pg import sett_kontekst
    pid = "pol-" + secrets.token_hex(3)
    # Riktig hash og konsistent meta — men innholdet består ikke skjemaet.
    innhold = {"meta": {"policy_id": pid, "versjon": "1.0.0",
                        "status": "produksjon"}}
    m = _mig()
    try:
        m.execute(
            "INSERT INTO policyer (tenant,policy_id,versjon,innholds_hash,"
            "status,innhold,aktiv) VALUES"
            " (%s,%s,'1.0.0',%s,'produksjon',%s::jsonb,true)",
            (TEN, pid, pr.innholds_hash(innhold), json.dumps(innhold)))
        m.commit()
        teller = _tellende_validator(monkeypatch)
        for forsok in (1, 2):
            sett_kontekst(m, TEN, "sys", f"r{forsok}")
            with pytest.raises(pr.PolicyKorrupt):
                pr.hent_aktiv(m, TEN, pid)
            m.rollback()
            assert teller["n"] == forsok, \
                "feilende innhold skal måles på nytt ved hver lasting"
    finally:
        m.rollback()
        m.close()
