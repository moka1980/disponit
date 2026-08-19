"""044 — periodisk kontroll: planen bestiller, motoren beslutter.

Portene fra klarsignalet §10, målt mot lagringen, materialisereren,
klassifisereren og HTTP-flaten. Bærebjelkene:

  * Kvoten frigis ALDRI — planens tick deler motorens teller med
    manuelle bestillinger, og ingen kodesti sletter fra
    `frekvens_hendelser` (statisk vern).
  * Ett tick per vindu, håndhevet av LAGRINGEN (PK + FK + trigger +
    eneste skriver), ikke av arbeiderens disiplin.
  * Missede vinduer tas aldri igjen: en WCAG-rapport for tirsdag er
    verdiløs på torsdag.
  * Planveien skriver aldri i `oppdrag` — bestillingen går gjennom
    NØYAKTIG samme `utfor_bestilling` som browserendepunktet.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
import ast
import json
import re
import secrets
from pathlib import Path

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, TENANT, app, dekker, klient, \
    migrator, miljo, token  # noqa: F401
from .test_m37 import _sett_kontekst
from .test_outbox_bestilling import (_adminsesjon, _beslutningsoppdrag,
                                     _bestill, _gyldig_kropp,
                                     _verifiser_domene, _wcag_policy)

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "044_periodisk_kontroll.sql")

PLANTABELLER = ("bestillingsplan", "bestillingsplan_aktiv_periode",
                "bestillingsplan_vindu", "bestillingsplan_tick",
                "bestillingsplan_hendelse")


def _rt():
    from db.pg import koble
    return koble(DSN)


def _mig():
    from db.pg import koble
    return koble(MIGRATOR_DSN)


def _param(host):
    return {"hostname": host, "sti": "/", "kravsett": "wcag21_aa",
            "omfang": "enkeltside", "maks_sider": 1}


def _oslo_forfalt_time(conn, delta=-1):
    """En `time_lokal` hvis dagens vindu ALT er forfalt (spredningen er
    0–59 min etter vindu_start) men ennå ikke utløpt (daglig = 2 t)."""
    h = conn.execute("SELECT extract(hour FROM now() AT TIME ZONE"
                     " 'Europe/Oslo')::int").fetchone()[0]
    return (h + delta) % 24


def _plan(rt, *, host, rytme="daglig", time_lokal=None, ukedag=None,
          manedsdag=None, aktiver=True, aktor="test:plan"):
    _sett_kontekst(rt, TENANT)
    if time_lokal is None:
        time_lokal = _oslo_forfalt_time(rt)
    pid = rt.execute(
        "SELECT opprett_plan(%s,'kontroll.wcag.nettsted',%s,%s,%s,%s,%s,"
        "'Europe/Oslo',%s,'r-plan')",
        (TENANT, json.dumps(_param(host)), rytme, ukedag, manedsdag,
         time_lokal, aktor)).fetchone()[0]
    if aktiver:
        rt.execute("SELECT aktiver_plan(%s,%s,%s,'r-plan')",
                   (TENANT, pid, aktor))
    rt.commit()
    return pid


def _ekte_bruker(sub):
    """En reell brukeridentitet: varselets bruker_id er FK mot
    brukeridentitet, så varslingstestene trenger en som finnes."""
    from db.pg import koble, sett_kontekst
    from .test_pr010_db import _identitet
    m = koble(MIGRATOR_DSN)
    try:
        sett_kontekst(m, TENANT, "sys", "r0")
        bid = _identitet(m, sub=f"{TENANT}-{sub}")
        m.commit()
    finally:
        m.close()
    return bid


def _aktiver_i_fortid(m, pid, *, dager=0, timer=0):
    """Aktivering med fortidig `fra_ts`. Perioderadene er immutable også
    for migratorrollen (identiteten inkluderer fra_ts), så fortiden
    KONSTRUERES ved innsetting — aldri ved å flytte en eksisterende rad.
    Speiler nøyaktig det aktiver_plan skriver, minus hendelsen."""
    _sett_kontekst(m, TENANT)
    m.execute("UPDATE bestillingsplan SET status='aktiv',"
              " aktivert_av='test:plan'"
              " WHERE plan_id=%s AND status='utkast'", (pid,))
    m.execute("INSERT INTO bestillingsplan_aktiv_periode"
              " (plan_id, tenant, fra_ts) VALUES"
              " (%s,%s, now() - make_interval(days => %s, hours => %s))",
              (pid, TENANT, dager, timer))
    m.commit()


def _plan_forfalt(rt, m, *, host, aktiver_dager=1, **kw):
    """Aktiv plan hvis dagens vindu ER kvalifisert: aktiveringen legges i
    går, ellers ville fra_ts > forfall gjort planen til port
    32-tilfellet (aktivert etter forfall = ingen rad)."""
    pid = _plan(rt, host=host, aktiver=False, **kw)
    _aktiver_i_fortid(m, pid, dager=aktiver_dager)
    return pid


def _mine_forfalte(conn, pid, maks=500):
    _sett_kontekst(conn, "__plan_sveip__", "plansveip", "plansveip")
    rader = conn.execute(
        "SELECT plan_id, tenant, vindu_start, vindu_slutt, forfall,"
        " bestillingstype, parametre FROM forfalte_planvinduer(%s)",
        (maks,)).fetchall()
    conn.commit()
    return [r for r in rader if r[0] == pid]


def _syntetisk_vindu(m, pid, *, start_h, slutt_h, tilstand="terminal",
                     lease_h=None):
    _sett_kontekst(m, TENANT)
    start = m.execute(
        "INSERT INTO bestillingsplan_vindu (plan_id, tenant, vindu_start,"
        " vindu_slutt, tilstand, terminalisert_ts, claim_id, lease_utloper)"
        " VALUES (%s,%s, now()+make_interval(hours=>%s),"
        " now()+make_interval(hours=>%s), %s,"
        " CASE WHEN %s='terminal' THEN now() END,"
        " CASE WHEN %s='aktivt' THEN gen_random_uuid() END,"
        " CASE WHEN %s='aktivt' THEN now()+make_interval(hours=>%s) END)"
        " RETURNING vindu_start",
        (pid, TENANT, start_h, slutt_h, tilstand, tilstand, tilstand,
         tilstand, lease_h or 1)).fetchone()[0]
    m.commit()
    return start


def _syntetisk_tick(m, pid, vindu_start, utfall, *, oppdrag_id=None):
    _sett_kontekst(m, TENANT)
    m.execute(
        "INSERT INTO bestillingsplan_tick (plan_id, tenant, vindu_start,"
        " idempotensnokkel, utfall, oppdrag_id)"
        " VALUES (%s,%s,%s,%s,%s,%s)",
        (pid, TENANT, vindu_start, "t-" + secrets.token_hex(8), utfall,
         oppdrag_id))
    m.commit()


def _post_plan(klient_, cookie, csrf, sti, kropp, idem=None):
    from api import sesjon as sesjonmodul
    # Opprettelsen krever `Idempotency-Key`; overgangene ignorerer den.
    # Uten en eksplisitt nøkkel er hvert kall sin egen operasjon.
    return klient_.post(sti, json=kropp,
                        headers={"X-Disponit-CSRF": csrf,
                                 "Idempotency-Key":
                                     idem or "idem-" + secrets.token_hex(8)},
                        cookies={sesjonmodul.C_SESJON: cookie})


def _plan_kropp(host, **over):
    k = {"bestillingstype": "kontroll.wcag.nettsted", "hostname": host,
         "kravsett": "wcag21_aa", "omfang": "enkeltside", "rytme": "daglig",
         "time_lokal": 8, "tidssone": "Europe/Oslo"}
    k.update(over)
    return k


# ---------------------------------------------------------------------------
# Plan og skjema (portene 1–8)
# ---------------------------------------------------------------------------

@pg
def test_ugyldig_bestillingstype_avvises(migrator, klient, capsys):
    """Port 1: ukjent type avvises i skjemaet; deploy-porten VARSLER om
    planrader dagens kode ikke kjenner (aldri rød — vakten bor i
    materialisereren, jf. 18/8-lærdommen om fremtidig tilstand)."""
    cookie, csrf = _adminsesjon()
    r = _post_plan(klient, cookie, csrf, "/v1/plan",
                   _plan_kropp("p1.example", bestillingstype="finnes.ikke"))
    assert (r.status_code, r.json()["feil"]) == (400, "request_feilformet"), \
        r.text
    # Deploy-porten: en konstruert rad med ukjent type → MERK, exit grønn.
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "INSERT INTO bestillingsplan (tenant, bestillingstype, parametre,"
        " rytme, time_lokal, tidssone, opprettet_av) VALUES"
        " (%s,'utdodd.type','{}','daglig',8,'Europe/Oslo','test')", (TENANT,))
    migrator.commit()
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "deployport_modultyper",
        ROT / "deploy" / "staging" / "deployport-modultyper.py")
    dp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dp)
    rt = _rt()
    try:
        assert dp.kontroller_planbestillingstyper(rt) == []
    finally:
        rt.close()
    assert "utdodd.type" in capsys.readouterr().out


@pg
def test_skjemagrensene_haandheves_i_begge_lag(migrator, klient):
    """Portene 2–4: månedsdag 31, ukjent tidssone og daglig+ukedag avvises
    av API-et — og av LAGRINGEN, uavhengig av API-ets disiplin."""
    cookie, csrf = _adminsesjon()
    for kropp in (
            _plan_kropp("p2.example", rytme="manedlig", manedsdag=31),
            _plan_kropp("p3.example", tidssone="Mars/Olympus"),
            _plan_kropp("p4.example", ukedag=3)):
        r = _post_plan(klient, cookie, csrf, "/v1/plan", kropp)
        assert (r.status_code, r.json()["feil"]) == (
            400, "request_feilformet"), r.text
    basis = ("INSERT INTO bestillingsplan (tenant, bestillingstype,"
             " parametre, rytme, ukedag, manedsdag, time_lokal, tidssone,"
             " opprettet_av) VALUES (%s,'kontroll.wcag.nettsted','{}',")
    for hale, args in (
            ("'manedlig',NULL,31,8,'Europe/Oslo','t')", ()),
            ("'daglig',3,NULL,8,'Europe/Oslo','t')", ()),
            ("'ukentlig',NULL,NULL,8,'Europe/Oslo','t')", ()),
            ("'daglig',NULL,NULL,25,'Europe/Oslo','t')", ())):
        _sett_kontekst(migrator, TENANT)
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(basis + hale, (TENANT, *args))
        migrator.rollback()


@pg
def test_pause_aarsak_og_status_er_koblet(migrator):
    """Port 5: `pause_aarsak` finnes hvis og BARE hvis planen er pauset."""
    rt = _rt()
    try:
        pid = _plan(rt, host="p5.example")
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute("UPDATE bestillingsplan SET"
                         " pause_aarsak='policy_stopper'"
                         " WHERE plan_id=%s", (pid,))
    migrator.rollback()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute("UPDATE bestillingsplan SET status='pauset'"
                         " WHERE plan_id=%s", (pid,))
    migrator.rollback()


def test_planen_har_ingen_frist():
    """Port 6 (statisk): ingen fristkolonne i planlagringen — fristen
    leses fra `UTFORELSESFRIST_VALG` i bestillingsveien, som eier den."""
    ddl = MIGRASJON.read_text(encoding="utf-8")
    uten_kommentar = "\n".join(l for l in ddl.splitlines()
                               if not l.lstrip().startswith("--"))
    for blokk in re.findall(r"CREATE TABLE bestillingsplan\w*\s*\(([^;]+)\)",
                            uten_kommentar):
        assert "frist" not in blokk, "planlagringen har fått en fristkolonne"
    import oppdragskontrakt
    assert oppdragskontrakt.UTFORELSESFRIST_VALG
    from api.plan import SKJEMAFELT
    assert not any("frist" in f for f in SKJEMAFELT)
    for fil in ("materialiser.py", "klassifiser.py"):
        kilde = (ROT / "platform" / "core" / "plan" / fil).read_text(
            encoding="utf-8")
        assert "frist" not in kilde.replace("utforelsesfrist_s", ""), fil


@pg
def test_runtime_har_ingen_tabelltilgang(migrator):
    """Port 7: runtime når planene KUN gjennom de herdede funksjonene."""
    rt = _rt()
    try:
        for t in PLANTABELLER:
            _sett_kontekst(rt, TENANT)
            for sql in (f"SELECT count(*) FROM {t}",
                        f"UPDATE {t} SET tenant = tenant",
                        f"DELETE FROM {t}"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    rt.execute(sql)
                rt.rollback()
    finally:
        rt.close()


@pg
def test_evidensen_er_append_only(migrator):
    """Portene 8, 50, 52, 53: tick/hendelse tåler ingen UPDATE/DELETE,
    terminal er endelig for HELE raden, tick krever terminalt vindu, og
    tilstandskombinasjonene er komplette."""
    rt = _rt()
    try:
        pid = _plan(rt, host="p8.example")
    finally:
        rt.close()
    vs = _syntetisk_vindu(migrator, pid, start_h=-30, slutt_h=-26)
    _syntetisk_tick(migrator, pid, vs, "hoppet_over")
    # 8: tick og hendelse er urørlige.
    _sett_kontekst(migrator, TENANT)
    for sql in ("UPDATE bestillingsplan_tick SET utfall='tillat'"
                " WHERE plan_id=%s",
                "DELETE FROM bestillingsplan_tick WHERE plan_id=%s",
                "UPDATE bestillingsplan_hendelse SET hendelse='stanset'"
                " WHERE plan_id=%s",
                "DELETE FROM bestillingsplan_hendelse WHERE plan_id=%s",
                "DELETE FROM bestillingsplan_vindu WHERE plan_id=%s",
                "DELETE FROM bestillingsplan_aktiv_periode WHERE plan_id=%s",
                "DELETE FROM bestillingsplan WHERE plan_id=%s"):
        # Triggerne melder seg med check_violation (plan_append_only) —
        # avvis_endring med raise_exception; begge er lagringens nei.
        with pytest.raises((psycopg.errors.RaiseException,
                            psycopg.errors.CheckViolation)):
            migrator.execute(sql, (pid,))
        migrator.rollback()
        _sett_kontekst(migrator, TENANT)
    # 50: terminal er endelig — tilstand, ts og claim kan ikke røres.
    for sql in ("UPDATE bestillingsplan_vindu SET tilstand='ledig'"
                " WHERE plan_id=%s AND vindu_start=%s",
                "UPDATE bestillingsplan_vindu SET"
                " terminalisert_ts=now()-interval '1 hour'"
                " WHERE plan_id=%s AND vindu_start=%s",
                "UPDATE bestillingsplan_vindu SET claim_id=gen_random_uuid()"
                " WHERE plan_id=%s AND vindu_start=%s"):
        with pytest.raises((psycopg.errors.RaiseException,
                            psycopg.errors.CheckViolation)):
            migrator.execute(sql, (pid, vs))
        migrator.rollback()
        _sett_kontekst(migrator, TENANT)
    # 52: tick mot et ikke-terminalt vindu avvises av lagringen.
    vs2 = _syntetisk_vindu(migrator, pid, start_h=-8, slutt_h=-4,
                           tilstand="ledig")
    _sett_kontekst(migrator, TENANT)
    with pytest.raises((psycopg.errors.RaiseException,
                        psycopg.errors.CheckViolation)):
        migrator.execute(
            "INSERT INTO bestillingsplan_tick (plan_id, tenant, vindu_start,"
            " idempotensnokkel, utfall) VALUES (%s,%s,%s,'nokkel-52',"
            "'hoppet_over')", (pid, TENANT, vs2))
    migrator.rollback()
    # 53: komplette kombinasjoner — aktivt uten claim, terminal uten ts.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "INSERT INTO bestillingsplan_vindu (plan_id, tenant,"
            " vindu_start, vindu_slutt, tilstand) VALUES"
            " (%s,%s,now(),now()+interval '2 hours','aktivt')",
            (pid, TENANT))
    migrator.rollback()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "INSERT INTO bestillingsplan_vindu (plan_id, tenant,"
            " vindu_start, vindu_slutt, tilstand) VALUES"
            " (%s,%s,now(),now()+interval '2 hours','terminal')",
            (pid, TENANT))
    migrator.rollback()


# ---------------------------------------------------------------------------
# Materialisering (9–16, 24, 25, 48) — hele veien, med ekte motor
# ---------------------------------------------------------------------------

@pg
def test_ett_tick_per_vindu_hele_veien(migrator, app):
    """Portene 9, 10, 16, 24, 48: aktiv plan → nøyaktig ETT tick (tillat,
    med oppdrag), andre kjøring er en no-op, planen åpner ingen M-37-sak,
    og tick + terminal ligger i samme transaksjon."""
    from plan.materialiser import materialiser_en
    _wcag_policy(migrator)
    _verifiser_domene(migrator, "p9.example")
    rt = _rt()
    try:
        pid = _plan_forfalt(rt, migrator, host="p9.example")
        rader = _mine_forfalte(rt, pid)
        assert len(rader) == 1, "planen skulle hatt nøyaktig ett forfalt vindu"
        res = materialiser_en(app.tjeneste, rt, rader[0])
        assert res["utfall"] == "tillat", res
        assert res["dom"] == "terminalisert", res
        # 10/48: neste kjøring finner ingenting å gjøre.
        assert _mine_forfalte(rt, pid) == []
        res2 = materialiser_en(app.tjeneste, rt, rader[0])
        assert res2.get("hoppet") == "terminal", res2
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    tick = migrator.execute(
        "SELECT utfall, oppdrag_id FROM bestillingsplan_tick"
        " WHERE plan_id=%s", (pid,)).fetchall()
    assert len(tick) == 1 and tick[0][0] == "tillat" and tick[0][1], tick
    # 24: ingen M-37-sak av planveien.
    assert migrator.execute(
        "SELECT unntak_id FROM oppdrag WHERE tenant=%s AND id=%s",
        (TENANT, tick[0][1])).fetchone()[0] is None
    # 16: tick uten terminal finnes ikke (vinduet ER terminalt).
    assert migrator.execute(
        "SELECT tilstand FROM bestillingsplan_vindu WHERE plan_id=%s",
        (pid,)).fetchone()[0] == "terminal"
    migrator.rollback()


@pg
def test_krasj_og_gjenspill_brenner_ingen_kvote(migrator, app):
    """Portene 11, 46, 47: krasj mellom claim og POST → overtakelse etter
    lease-utløp; krasj mellom POST-commit og tick → replay med UENDRET
    svarkropp, null nye frekvensrader, tick med faktisk utfall."""
    from api.bestilling import utfor_bestilling
    from plan.materialiser import (LEASE_S, idempotensnokkel,
                                   materialiser_en)
    from db.pg import sett_kontekst
    _wcag_policy(migrator)
    _verifiser_domene(migrator, "p11.example")
    rt = _rt()
    try:
        pid = _plan_forfalt(rt, migrator, host="p11.example")
        rad = _mine_forfalte(rt, pid)[0]
        vs = rad[2]
        nokkel = idempotensnokkel(pid, vs)
        # Krasj nr. 1: claim committes, prosessen dør før POST.
        sett_kontekst(rt, TENANT, f"plan:{pid}", "r-krasj")
        utfall, _claim = rt.execute(
            "SELECT utfall, claim_id FROM claim_planvindu(%s,%s,%s,%s)",
            (TENANT, pid, vs, LEASE_S)).fetchone()
        rt.commit()
        assert utfall == "claimet"
        # Før leasen dør eier forsøket vinduet (46).
        sett_kontekst(rt, TENANT, f"plan:{pid}", "r-krasj")
        assert rt.execute(
            "SELECT utfall FROM claim_planvindu(%s,%s,%s,%s)",
            (TENANT, pid, vs, LEASE_S)).fetchone()[0] == "aktivt"
        rt.commit()
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "UPDATE bestillingsplan_vindu SET"
            " lease_utloper = now() - interval '1 second'"
            " WHERE plan_id=%s AND vindu_start=%s", (pid, vs))
        migrator.commit()
        # Krasj nr. 2: bestillingen committer, prosessen dør før ticket.
        res1 = utfor_bestilling(app.tjeneste, rt, TENANT, f"plan:{pid}",
                                {"bestillingstype": rad[5], **rad[6]},
                                nokkel, "r-krasj2")
        assert res1[0] == "ok" and res1[1]["beslutning"] == "tillat", res1
        _sett_kontekst(migrator, TENANT)
        frek_foer = migrator.execute(
            "SELECT count(*) FROM frekvens_hendelser WHERE tenant=%s",
            (TENANT,)).fetchone()[0]
        migrator.rollback()
        # Gjenoppstart: full materialisering av samme rad — overtar
        # claimet, GJENSPILLER bestillingen, feller det faktiske utfallet.
        res = materialiser_en(app.tjeneste, rt, rad)
        assert res["utfall"] == "tillat" and res["dom"] == "terminalisert", \
            res
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    frek_etter = migrator.execute(
        "SELECT count(*) FROM frekvens_hendelser WHERE tenant=%s",
        (TENANT,)).fetchone()[0]
    tick = migrator.execute(
        "SELECT utfall, oppdrag_id FROM bestillingsplan_tick"
        " WHERE plan_id=%s", (pid,)).fetchall()
    migrator.rollback()
    assert frek_etter == frek_foer, "gjenspillet brant en ny kvoteplass"
    assert len(tick) == 1 and tick[0] == ("tillat", res1[1]["oppdrag_id"])


@pg
def test_terminalisering_vinner_gir_null_http(migrator, app, monkeypatch):
    """Port 45: er vinduet alt terminalt, avbryter materialiseringen FØR
    bestillingsveien — målt som null kall, ikke som fravær av rad."""
    from plan import materialiser
    rt = _rt()
    try:
        pid = _plan(rt, host="p45.example")
        vs = _syntetisk_vindu(migrator, pid, start_h=-30, slutt_h=-26)
        _syntetisk_tick(migrator, pid, vs, "hoppet_over")

        def aldri(*a, **k):
            raise AssertionError("bestillingsveien ble kalt for et"
                                 " terminalt vindu")
        import api.bestilling
        monkeypatch.setattr(api.bestilling, "utfor_bestilling", aldri)
        rad = (pid, TENANT, vs, None, None, "kontroll.wcag.nettsted",
               _param("p45.example"))
        res = materialiser.materialiser_en(app.tjeneste, rt, rad)
        assert res.get("hoppet") == "terminal", res
    finally:
        rt.close()


@pg
def test_hoppet_over_portene(migrator):
    """Portene 37, 51 og §5-nekten: `hoppet_over` krever utløpt vindu,
    intet levende forsøk og INTET idempotenstreff."""
    rt = _rt()
    try:
        pid = _plan(rt, host="p37.example")
        # 37: før vindu_slutt.
        vs_aapent = _syntetisk_vindu(migrator, pid, start_h=-1, slutt_h=1,
                                     tilstand="ledig")
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT terminaliser_planvindu(%s,%s,%s,NULL,"
                       "'n-37xxxxx','hoppet_over',NULL,NULL)",
                       (TENANT, pid, vs_aapent))
        rt.rollback()
        # 51: levende lease eier vinduet.
        vs_lease = _syntetisk_vindu(migrator, pid, start_h=-8, slutt_h=-4,
                                    tilstand="aktivt", lease_h=1)
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT terminaliser_planvindu(%s,%s,%s,NULL,"
                       "'n-51xxxxx','hoppet_over',NULL,NULL)",
                       (TENANT, pid, vs_lease))
        rt.rollback()
        # §5: finnes en bestilling på nøkkelen, BLE det bestilt.
        vs_bestilt = _syntetisk_vindu(migrator, pid, start_h=-14,
                                      slutt_h=-10, tilstand="ledig")
        nokkel = "n-bestilt-" + secrets.token_hex(4)
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "INSERT INTO bestilling_idempotens (tenant, idempotensnokkel,"
            " intensjonshash, beslutning, svarkropp) VALUES"
            " (%s,%s,%s,'tillat','{}')",
            (TENANT, nokkel, "0" * 64))
        migrator.commit()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT terminaliser_planvindu(%s,%s,%s,NULL,%s,"
                       "'hoppet_over',NULL,NULL)",
                       (TENANT, pid, vs_bestilt, nokkel))
        rt.rollback()
    finally:
        rt.close()


@pg
def test_avvik_er_sikkerhetssak(migrator):
    """Port 49: terminal gjenbesøkt med ANNET utfall → `avvik:<x>` og en
    sikkerhetshendelse skrevet ATOMISK av funksjonen selv; samme utfall
    → `idempotent`, ingen hendelse."""
    rt = _rt()
    try:
        pid = _plan(rt, host="p49.example")
        vs = _syntetisk_vindu(migrator, pid, start_h=-30, slutt_h=-26,
                              tilstand="ledig")
        _sett_kontekst(rt, TENANT)
        assert rt.execute(
            "SELECT terminaliser_planvindu(%s,%s,%s,NULL,'n-49xxxxx',"
            "'hoppet_over',NULL,NULL)",
            (TENANT, pid, vs)).fetchone()[0] == "terminalisert"
        rt.commit()
        _sett_kontekst(rt, TENANT)
        assert rt.execute(
            "SELECT terminaliser_planvindu(%s,%s,%s,NULL,'n-49xxxxx',"
            "'hoppet_over',NULL,NULL)",
            (TENANT, pid, vs)).fetchone()[0] == "idempotent"
        rt.commit()
        _sett_kontekst(rt, TENANT)
        assert rt.execute(
            "SELECT terminaliser_planvindu(%s,%s,%s,NULL,'n-49xxxxx',"
            "'tillat',NULL,NULL)",
            (TENANT, pid, vs)).fetchone()[0] == "avvik:hoppet_over"
        rt.commit()
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    avvik = migrator.execute(
        "SELECT detalj FROM bestillingsplan_hendelse WHERE plan_id=%s"
        " AND hendelse='sikkerhetsavvik'", (pid,)).fetchall()
    migrator.rollback()
    assert len(avvik) == 1 and avvik[0][0]["fant"] == "hoppet_over", avvik


@pg
def test_to_materialiserere_en_claim(migrator):
    """Port 48 (mutexen isolert): andre claim på samme vindu får
    `aktivt` — aldri et claim til."""
    rt = _rt()
    try:
        pid = _plan(rt, host="p48.example")
        vs = _syntetisk_vindu(migrator, pid, start_h=-1, slutt_h=1,
                              tilstand="ledig")
        _sett_kontekst(rt, TENANT)
        u1 = rt.execute("SELECT utfall FROM claim_planvindu(%s,%s,%s,120)",
                        (TENANT, pid, vs)).fetchone()[0]
        rt.commit()
        _sett_kontekst(rt, TENANT)
        u2 = rt.execute("SELECT utfall FROM claim_planvindu(%s,%s,%s,120)",
                        (TENANT, pid, vs)).fetchone()[0]
        rt.commit()
        assert (u1, u2) == ("claimet", "aktivt")
    finally:
        rt.close()


@pg
def test_claimet_nekter_et_utlopt_vindu(migrator, app, monkeypatch):
    """Codex P1: utløpet må sjekkes ATOMISK med claimet, ikke bare i
    plukket.

    Plukket gir en BATCH som arbeides ned sekvensielt, og hver bestilling
    er et HTTP-kall. En rad som lå innenfor vinduet da batchen ble valgt,
    kan være minutter utenfor når turen kommer til den — og uten
    kontrollen her ble et misset vindu til en INNHENTING, stikk i strid
    med §5s aldri-ta-igjen."""
    from plan import materialiser
    rt = _rt()
    try:
        pid = _plan(rt, host="p-utlopt.example")
        # Vinduet er lukket: batchen var fersk, turen kom for sent.
        vs = _syntetisk_vindu(migrator, pid, start_h=-8, slutt_h=-4,
                              tilstand="ledig")
        _sett_kontekst(rt, TENANT)
        u = rt.execute("SELECT utfall FROM claim_planvindu(%s,%s,%s,120)",
                       (TENANT, pid, vs)).fetchone()[0]
        rt.commit()
        assert u == "utlopt"

        def aldri(*a, **k):
            raise AssertionError("et utløpt vindu nådde bestillingsveien")
        import api.bestilling
        monkeypatch.setattr(api.bestilling, "utfor_bestilling", aldri)
        rad = (pid, TENANT, vs, None, None, "kontroll.wcag.nettsted",
               _param("p-utlopt.example"))
        res = materialiser.materialiser_en(app.tjeneste, rt, rad)
        assert res.get("hoppet") == "utlopt", res
        # Vinduet er urørt og står til klassifisereren: ingen innhenting,
        # men heller ingen tapt evidens.
        _sett_kontekst(migrator, TENANT)
        vindu = migrator.execute(
            "SELECT tilstand, claim_id FROM bestillingsplan_vindu"
            " WHERE plan_id=%s AND vindu_start=%s", (pid, vs)).fetchone()
        migrator.rollback()
        assert vindu == ("ledig", None), vindu
    finally:
        rt.close()


def test_nokkelen_er_deterministisk():
    """Port 12: nøkkelen er en ren funksjon av plan + vindu, innenfor
    8–200 tegn — og klassifisereren avleder NØYAKTIG samme form uten å
    importere materialiseringen."""
    import uuid
    from datetime import datetime, timezone
    from plan.klassifiser import _nokkel
    from plan.materialiser import idempotensnokkel
    pid = uuid.UUID("11111111-2222-4333-8444-555555555555")
    ts = datetime(2026, 8, 18, 6, 0, tzinfo=timezone.utc)
    n = idempotensnokkel(pid, ts)
    assert n == idempotensnokkel(pid, ts) == _nokkel(pid, ts)
    assert n.startswith("plan:") and 8 <= len(n) <= 200


@pg
def test_spredning_og_tak(migrator):
    """Portene 13 og 36: forfallsminuttet sprer jevnt (maks-andel ≤ 0,10,
    jf. evidensgrensen), og taket FORSINKER overskuddet — det
    materialiseres i neste kjøring, aldri konsumeres."""
    rt0 = _rt()
    try:
        _sett_kontekst(rt0, TENANT)
        minutter = [r[0] for r in rt0.execute(
            "SELECT plan_forfallsminutt(gen_random_uuid())"
            " FROM generate_series(1, 200)").fetchall()]
        rt0.rollback()
    finally:
        rt0.close()
    assert all(0 <= m <= 59 for m in minutter)
    assert len(set(minutter)) >= 30
    verste = max(minutter.count(m) for m in set(minutter))
    assert verste <= 20, f"spredningen klumper ({verste}/200 på ett minutt)"
    # Taket: tre forfalte planer, plukk 2 — den tredje har INGEN rad ennå.
    rt = _rt()
    try:
        pids = [_plan_forfalt(rt, migrator, host=f"tak{i}.example")
                for i in range(3)]
        _sett_kontekst(rt, "__plan_sveip__", "plansveip", "plansveip")
        plukk1 = [r[0] for r in rt.execute(
            "SELECT plan_id FROM forfalte_planvinduer(2)").fetchall()
            if r[0] in pids]
        rt.commit()
        assert len(plukk1) <= 2
        _sett_kontekst(migrator, TENANT)
        uten_rad = [p for p in pids if migrator.execute(
            "SELECT count(*) FROM bestillingsplan_vindu WHERE plan_id=%s",
            (p,)).fetchone()[0] == 0]
        migrator.rollback()
        assert uten_rad, "overskuddet fikk likevel en rad (konsumert)"
        # Neste kjøring: resten kommer, innenfor toleransen.
        rest = [r[0] for r in _rt_forfalte(rt) if r[0] in uten_rad]
        assert set(rest) == set(uten_rad)
    finally:
        rt.close()


def _rt_forfalte(rt):
    _sett_kontekst(rt, "__plan_sveip__", "plansveip", "plansveip")
    rader = rt.execute(
        "SELECT plan_id FROM forfalte_planvinduer(500)").fetchall()
    rt.commit()
    return rader


# ---------------------------------------------------------------------------
# Kvalifisering og klassifisering (14, 32–35, 38, 39, 44)
# ---------------------------------------------------------------------------

@pg
def test_kvalifisering_folger_forfallet(migrator):
    """Portene 32 og 38: aktivert ETTER forfall → ingen rad; aktivert
    etter vindu_start men FØR forfall → kjører."""
    rt = _rt()
    try:
        # Aktivert nå, vinduet forfalt for en time siden → fra_ts > forfall.
        pid = _plan(rt, host="p32.example")
        assert _mine_forfalte(rt, pid) == [], \
            "en plan aktivert etter forfall fikk et vindu (port 32)"
        # Port 38: en ANNEN plan, aktivert to timer tilbake — dvs. etter
        # midnatt men FØR forfallet (kvalifiseringen ser kun på
        # forfallet, §4) — får vinduet sitt.
        pid38 = _plan(rt, host="p38.example", aktiver=False)
        _aktiver_i_fortid(migrator, pid38, timer=2)
        assert len(_mine_forfalte(rt, pid38)) == 1, "port 38: skulle kjørt"
    finally:
        rt.close()


@pg
def test_klassifisereren_feller_hoppet_over(migrator, app):
    """Portene 14, 33, 39: nedetid over et vindu → nøyaktig én
    `hoppet_over` etter vindu_slutt, ingen innhenting, ingen bestilling
    — og kjøringen er idempotent."""
    from plan.klassifiser import klassifiser_vinduer
    rt = _rt()
    try:
        pid = _plan(rt, host="p33.example", aktiver=False)
        _aktiver_i_fortid(migrator, pid, dager=3)
        _sett_kontekst(migrator, TENANT)
        idem_foer = migrator.execute(
            "SELECT count(*) FROM bestilling_idempotens WHERE tenant=%s",
            (TENANT,)).fetchone()[0]
        migrator.rollback()
        klassifiser_vinduer(rt)
        _sett_kontekst(migrator, TENANT)
        tick = migrator.execute(
            "SELECT utfall, vindu_start FROM bestillingsplan_tick"
            " WHERE plan_id=%s ORDER BY vindu_start", (pid,)).fetchall()
        idem_etter = migrator.execute(
            "SELECT count(*) FROM bestilling_idempotens WHERE tenant=%s",
            (TENANT,)).fetchone()[0]
        migrator.rollback()
        # 3 døgn tilbake gir 2–3 utløpte vinduer (dagens er kanskje ennå
        # åpent) — alle hoppet_over, aldri innhentet som bestilling.
        assert tick and all(u == "hoppet_over" for u, _ in tick), tick
        assert idem_etter == idem_foer, "klassifisereren BESTILTE (port 14)"
        # Idempotent: en ny kjøring feller ingen nye dommer.
        n = len(tick)
        klassifiser_vinduer(rt)
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT count(*) FROM bestillingsplan_tick WHERE plan_id=%s",
            (pid,)).fetchone()[0] == n
        migrator.rollback()
    finally:
        rt.close()


@pg
def test_klassifisereren_leser_fasiten_fra_idempotens(migrator):
    """§5: finnes en rad i `bestilling_idempotens` på vinduets nøkkel,
    BLE det bestilt — utfallet hentes derfra, aldri `hoppet_over`."""
    from plan.klassifiser import _nokkel, klassifiser_vinduer
    rt = _rt()
    try:
        pid = _plan(rt, host="p5b.example", aktiver=False)
        _aktiver_i_fortid(migrator, pid, dager=2)
        # Finn gårsdagens vindu ved å la utlopte_planvinduer materialisere.
        _sett_kontekst(rt, "__plan_sveip__", "planklassifisering", "pk")
        rt.execute("SELECT count(*) FROM utlopte_planvinduer(30, 200)")
        rt.commit()
        _sett_kontekst(migrator, TENANT)
        vs = migrator.execute(
            "SELECT vindu_start FROM bestillingsplan_vindu WHERE plan_id=%s"
            " AND tilstand <> 'terminal' ORDER BY vindu_start LIMIT 1",
            (pid,)).fetchone()[0]
        migrator.execute(
            "INSERT INTO bestilling_idempotens (tenant, idempotensnokkel,"
            " intensjonshash, oppdrag_id, beslutning, svarkropp) VALUES"
            " (%s,%s,%s,NULL,'tillat','{\"oppdrag_id\": 424242}')",
            (TENANT, _nokkel(pid, vs), "1" * 64))
        migrator.commit()
        klassifiser_vinduer(rt)
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    tick = migrator.execute(
        "SELECT utfall, oppdrag_id, detalj FROM bestillingsplan_tick"
        " WHERE plan_id=%s AND vindu_start=%s", (pid, vs)).fetchone()
    migrator.rollback()
    assert tick[0] == "tillat" and tick[1] == 424242, tick
    assert tick[2]["kilde"] == "bestilling_idempotens"


@pg
def test_klassifisereren_venter_paa_levende_forsok(migrator):
    """Port 44: klokken passerte vindu_slutt mens et forsøk lever —
    klassifisereren skriver INGENTING; vinduet ender med faktisk utfall."""
    from plan.klassifiser import klassifiser_vinduer
    rt = _rt()
    try:
        pid = _plan(rt, host="p44.example")
        vs = _syntetisk_vindu(migrator, pid, start_h=-8, slutt_h=-4,
                              tilstand="aktivt", lease_h=1)
        res = klassifiser_vinduer(rt)
        assert any(v["plan"] == str(pid) for v in res.get("ventet", [])), res
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT count(*) FROM bestillingsplan_tick WHERE plan_id=%s",
            (pid,)).fetchone()[0] == 0
        migrator.rollback()
        # Forsøket lander med sitt claim — det faktiske utfallet står.
        _sett_kontekst(migrator, TENANT)
        claim = migrator.execute(
            "SELECT claim_id FROM bestillingsplan_vindu WHERE plan_id=%s"
            " AND vindu_start=%s", (pid, vs)).fetchone()[0]
        migrator.rollback()
        _sett_kontekst(rt, TENANT)
        assert rt.execute(
            "SELECT terminaliser_planvindu(%s,%s,%s,%s,'n-44xxxxx',"
            "'tillat',777,NULL)",
            (TENANT, pid, vs, claim)).fetchone()[0] == "terminalisert"
        rt.commit()
    finally:
        rt.close()


@pg
def test_nedetid_over_30_dogn_aggregeres(migrator):
    """Port 35: vinduer eldre enn tilbakeblikket får ALDRI enkeltrader —
    én aggregert hendelse per plan, og radene som alt finnes
    terminaliseres uten enkelthendelser. Ingen rad før tidligste fra_ts."""
    from plan.klassifiser import klassifiser_vinduer
    rt = _rt()
    try:
        pid = _plan(rt, host="p35.example", aktiver=False)
        _aktiver_i_fortid(migrator, pid, dager=40)
        # To gamle vindusrader (fra en tenkt kjøring for 35 døgn siden).
        for d in (35, 33):
            _syntetisk_vindu(migrator, pid, start_h=-24 * d,
                             slutt_h=-24 * d + 4, tilstand="ledig")
        klassifiser_vinduer(rt)
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    agg = migrator.execute(
        "SELECT count(*) FROM bestillingsplan_hendelse WHERE plan_id=%s"
        " AND hendelse='nedetid_aggregert'", (pid,)).fetchone()[0]
    # ≥ 2: de to syntetiske radene — grensedøgnet (dag 30, som
    # enumereringen materialiserer og aggregatet straks feller) kan gi
    # en tredje, avhengig av klokkeslettet testen kjører.
    gamle_tick = migrator.execute(
        "SELECT count(*) FROM bestillingsplan_tick WHERE plan_id=%s"
        " AND vindu_start < now() - interval '30 days'"
        " AND detalj->>'kilde' = 'nedetid_aggregert'",
        (pid,)).fetchone()[0]
    # Enumereringen skapte ingen rader eldre enn tilbakeblikket eller
    # før fra_ts (fra_ts er 40 døgn; tilbakeblikket 30 → grensen er 30).
    for_gamle_rader = migrator.execute(
        "SELECT count(*) FROM bestillingsplan_vindu WHERE plan_id=%s"
        " AND vindu_start < now() - interval '32 days'"
        " AND tilstand <> 'terminal'", (pid,)).fetchone()[0]
    migrator.rollback()
    assert agg == 1, "aggregatet manglet eller kom flere ganger"
    assert gamle_tick >= 2, "de gamle radene ble ikke terminalisert"
    assert for_gamle_rader == 0, "enumereringen skapte rader utenfor" \
        " tilbakeblikket"


def test_klassifisereren_har_ingen_bestillingsvei():
    """Port 34 (statisk AST): klassifisereren importerer verken
    HTTP-klienter eller bestillingsveien — heller ikke inne i funksjoner,
    og heller ikke transitivt via materialisereren."""
    kilde = (ROT / "platform" / "core" / "plan" / "klassifiser.py"
             ).read_text(encoding="utf-8")
    tre = ast.parse(kilde)
    forbudt = ("http", "urllib", "requests", "httpx", "aiohttp", "socket",
               "api", "plan.materialiser")
    for node in ast.walk(tre):
        navn = []
        if isinstance(node, ast.Import):
            navn = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            navn = [node.module or ""]
        for n in navn:
            rot = n.split(".")[0]
            assert rot not in forbudt and n not in forbudt, \
                f"klassifisereren importerer {n}"
            assert n != "materialiser", "transitiv bestillingsvei"


def test_planveien_skriver_aldri_oppdrag():
    """Port 15 (statisk): ingen INSERT mot `oppdrag` eller
    `frekvens_hendelser` i planmodulene eller migrasjonen — bestillingen
    går gjennom `utfor_bestilling`, og BARE den."""
    filer = [ROT / "platform" / "core" / "plan" / "materialiser.py",
             ROT / "platform" / "core" / "plan" / "klassifiser.py",
             ROT / "platform" / "core" / "api" / "plan.py",
             MIGRASJON]
    monster = re.compile(r"INSERT\s+INTO\s+(public\.)?(oppdrag|"
                         r"frekvens_hendelser|bestilling_idempotens|"
                         r"revisjonslogg)\b", re.IGNORECASE)
    for fil in filer:
        tekst = fil.read_text(encoding="utf-8")
        assert not monster.search(tekst), f"{fil.name} skriver selv"
    kilde = (ROT / "platform" / "core" / "plan" / "materialiser.py"
             ).read_text(encoding="utf-8")
    assert "utfor_bestilling" in kilde


def test_ingen_slettevei_for_frekvens():
    """Port 27 (statisk regresjonsvern): INGEN kodesti sletter fra
    `frekvens_hendelser` — kvoten frigis aldri, heller ikke av en
    fremtidig oppryddingsjobb som virker rimelig i en PR."""
    monster = re.compile(r"(DELETE\s+FROM|TRUNCATE(\s+TABLE)?)\s+"
                         r"(public\.)?frekvens_hendelser\b", re.IGNORECASE)
    treff = []
    for katalog in (ROT / "platform", ROT / "deploy"):
        for fil in katalog.rglob("*"):
            if fil.suffix not in (".py", ".sql") or "tests" in fil.parts \
                    or "__pycache__" in fil.parts:
                continue
            if monster.search(fil.read_text(encoding="utf-8",
                                            errors="ignore")):
                treff.append(str(fil.relative_to(ROT)))
    assert treff == [], f"slettevei for kvoten funnet: {treff}"


# ---------------------------------------------------------------------------
# Pausereglene (17–24) og kvoten (25–26)
# ---------------------------------------------------------------------------

@pg
def test_menneskelig_avvis_pauser_forste_gang(migrator, app):
    """Portene 17 og 18: `menneskelig_avvis` på et tick-oppdrag pauser
    UMIDDELBART, første gang, auditert og varslet — annen kansellering
    pauser ikke."""
    from plan.materialiser import pausesveip
    bid = _ekte_bruker("p17-eier")
    rt = _rt()
    try:
        pid = _plan(rt, host="p17.example", aktor=f"bruker:{bid}")
        oid, _logg = _beslutningsoppdrag(rt, migrator)
        vs = _syntetisk_vindu(migrator, pid, start_h=-30, slutt_h=-26)
        _syntetisk_tick(migrator, pid, vs, "tillat", oppdrag_id=oid)
        # Annen kansellering først: årsaksløs → INGEN pause (port 18).
        m = _mig()
        _sett_kontekst(m, TENANT)
        m.execute("SET ROLE disponit_m37_claimer")
        m.execute("UPDATE oppdrag SET status='kansellert'"
                  " WHERE tenant=%s AND id=%s", (TENANT, oid))
        m.commit()
        assert pausesveip(rt) == []
        # Så det menneskelige nei-et — på et NYTT oppdrag/tick (årsaken
        # er immutabel når satt, og settes i samme setning som overgangen).
        oid2, _ = _beslutningsoppdrag(rt, migrator)
        vs2 = _syntetisk_vindu(migrator, pid, start_h=-54, slutt_h=-50)
        _syntetisk_tick(migrator, pid, vs2, "tillat", oppdrag_id=oid2)
        _sett_kontekst(m, TENANT)
        m.execute("SET ROLE disponit_m37_claimer")
        m.execute("UPDATE oppdrag SET status='kansellert',"
                  " kansellert_aarsak='menneskelig_avvis'"
                  " WHERE tenant=%s AND id=%s", (TENANT, oid2))
        m.commit()
        m.close()
        assert (str(pid), "menneskelig_avvis") in pausesveip(rt)
        # Første gang: sveipen er idempotent på en alt pauset plan.
        assert pausesveip(rt) == []
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    plan = migrator.execute(
        "SELECT status, pause_aarsak FROM bestillingsplan WHERE plan_id=%s",
        (pid,)).fetchone()
    hendelse = migrator.execute(
        "SELECT detalj->>'aarsak' FROM bestillingsplan_hendelse"
        " WHERE plan_id=%s AND hendelse='pauset'", (pid,)).fetchall()
    varsel = migrator.execute(
        "SELECT tekstnokkel, parametre->>'aarsak', bruker_id FROM varsel"
        " WHERE tenant=%s AND art='plan_pauset' AND ressurs_id=%s",
        (TENANT, str(pid))).fetchall()
    migrator.rollback()
    assert plan == ("pauset", "menneskelig_avvis")
    assert hendelse == [("menneskelig_avvis",)]
    # Varselet gikk til den som AKTIVERTE planen — som reell bruker-id
    # (aktørstrengen alene ville brutt FK-en og blitt stille slukt).
    assert varsel == [("varsel.plan_pauset", "menneskelig_avvis", bid)]


@pg
def test_stopp_pauser_med_policy_stopper(migrator, app):
    """Portene 19, 20, 26: uverifisert/tilbakekalt domene → `stopp` →
    pauset `policy_stopper` uten egen kodesti — og en pauset plan
    reserverer INGENTING."""
    from plan.materialiser import materialiser_en
    _wcag_policy(migrator)   # policy finnes; domenet er IKKE verifisert
    rt = _rt()
    try:
        pid = _plan_forfalt(rt, migrator,
                            host="p19-uverifisert.example")
        rad = _mine_forfalte(rt, pid)[0]
        _sett_kontekst(migrator, TENANT)
        frek_foer = migrator.execute(
            "SELECT count(*) FROM frekvens_hendelser WHERE tenant=%s",
            (TENANT,)).fetchone()[0]
        migrator.rollback()
        res = materialiser_en(app.tjeneste, rt, rad)
        assert res["utfall"] == "stopp", res
        # 26: pauset plan er usynlig for plukket og brant ingen kvote.
        assert _mine_forfalte(rt, pid) == []
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    plan = migrator.execute(
        "SELECT status, pause_aarsak FROM bestillingsplan WHERE plan_id=%s",
        (pid,)).fetchone()
    frek_etter = migrator.execute(
        "SELECT count(*) FROM frekvens_hendelser WHERE tenant=%s",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert plan == ("pauset", "policy_stopper")
    assert frek_etter == frek_foer, "et stoppet tick reserverte kvote"


def test_modul_utilgjengelig_har_sin_pausegrunn():
    """§7-raden om utilgjengelig modul: bestillingsveiens
    `bestillingstype_utilgjengelig` (typen kan ikke claimes — modul nede,
    ingen claiming-deployment) mapper til pausegrunnen
    `modul_utilgjengelig`. Mekanismen er samme `stopp`-vei som port 19;
    her låses selve oversettelsen."""
    from plan.materialiser import _PAUSE_FOR_FEIL
    assert _PAUSE_FOR_FEIL["bestillingstype_utilgjengelig"] == \
        "modul_utilgjengelig"
    assert _PAUSE_FOR_FEIL["bestilling_hostname_uverifisert"] == \
        "policy_stopper"


def test_driftsfeil_er_forbigaende_ikke_dom():
    """Codex P1: et driftsuhell er ingen dom over planen.

    `db_utilgjengelig`, `logging_feilet` og `intern_feil` er rutet til
    `drift` i feilveitabellen — de skal gi INTET tick og INGEN pause.
    Ble de terminalisert som `stopp`, konsumerte et minutts
    databasetrøbbel vinduet permanent og pauset planen som
    `policy_stopper`. De to §7-kodene er unntaket: de ER dommer, og
    beholder sin pausegrunn selv om den ene er drift-rutet i HTTP-laget.
    """
    from plan.materialiser import _tick_utfall, er_forbigaende
    for kode in ("db_utilgjengelig", "logging_feilet", "intern_feil",
                 "unntaksskriv_feilet", "tenantnokkel_mangler"):
        assert er_forbigaende(kode), kode
        assert _tick_utfall(("feil", kode))[0] is None, kode
    for kode in ("bestilling_hostname_uverifisert",
                 "bestillingstype_utilgjengelig", "request_feilformet",
                 "idempotenskonflikt", "policy_ukjent"):
        assert not er_forbigaende(kode), kode
        assert _tick_utfall(("feil", kode))[0] == "stopp", kode


@pg
def test_forbigaende_feil_frigir_vinduet(migrator, app, monkeypatch):
    """Codex P1, ende til ende: en drift-feil fra bestillingsveien gir
    vinduet TILBAKE (`ledig`), uten tick og uten pause — og neste kjøring
    plukker det samme vinduet igjen. Claimet gis tilbake i stedet for å
    vente ut leasen: vinduet kan ha sekunder igjen."""
    from plan import materialiser
    rt = _rt()
    try:
        pid = _plan_forfalt(rt, migrator, host="p-drift.example")
        rad = _mine_forfalte(rt, pid)[0]
        import api.bestilling
        monkeypatch.setattr(api.bestilling, "utfor_bestilling",
                            lambda *a, **k: ("feil", "db_utilgjengelig"))
        res = materialiser.materialiser_en(app.tjeneste, rt, rad)
        assert res["forbigaende"] == "db_utilgjengelig", res
        assert res["frigitt"] == "frigitt", res
        _sett_kontekst(migrator, TENANT)
        vindu = migrator.execute(
            "SELECT tilstand, claim_id, lease_utloper, terminalisert_ts"
            "  FROM bestillingsplan_vindu WHERE plan_id=%s", (pid,)
        ).fetchall()
        tick = migrator.execute(
            "SELECT count(*) FROM bestillingsplan_tick WHERE plan_id=%s",
            (pid,)).fetchone()[0]
        plan = migrator.execute(
            "SELECT status, pause_aarsak FROM bestillingsplan"
            " WHERE plan_id=%s", (pid,)).fetchone()
        migrator.rollback()
        assert vindu == [("ledig", None, None, None)], vindu
        assert tick == 0, "et driftsuhell skrev evidens"
        assert plan == ("aktiv", None), plan
        # Vinduet er fortsatt til å ta: retten til å forsøke er intakt.
        assert _mine_forfalte(rt, pid), "det frigitte vinduet forsvant"
    finally:
        rt.close()


@pg
def test_gjentatt_uten_resultat(migrator):
    """Port 22: tre `tillat`-tick på rad i gjeldende åpne periode uten
    promotert artefakt → pauset `gjentatt_uten_resultat`."""
    from plan.materialiser import pausesveip
    rt = _rt()
    try:
        pid = _plan(rt, host="p22.example")
        for i in range(3):
            vs = _syntetisk_vindu(migrator, pid, start_h=-30 - 24 * i,
                                  slutt_h=-26 - 24 * i)
            _syntetisk_tick(migrator, pid, vs, "tillat",
                            oppdrag_id=900000 + i)
        assert (str(pid), "gjentatt_uten_resultat") in pausesveip(rt)
    finally:
        rt.close()


@pg
def test_et_brudd_bryter_stripen_uten_resultat(migrator):
    """Codex P2: strekken måles på de tre SISTE tickene, ikke de tre siste
    VELLYKKEDE.

    Med `utfall = 'tillat'` filtrert FØR `LIMIT 3` ble `tillat, brudd,
    tillat, tillat` lest som tre sammenhengende `tillat` og pauset planen
    — enda `brudd`-et imellom nettopp bryter stripen."""
    from plan.materialiser import pausesveip
    rt = _rt()
    try:
        pid = _plan(rt, host="p22b.example")
        # Eldst → nyest: tillat, tillat, brudd, tillat.
        for i, utfall in enumerate(("tillat", "tillat", "brudd", "tillat")):
            vs = _syntetisk_vindu(migrator, pid, start_h=-30 + 4 * i,
                                  slutt_h=-28 + 4 * i)
            _syntetisk_tick(migrator, pid, vs, utfall,
                            oppdrag_id=920000 + i if utfall == "tillat"
                            else None)
        assert pausesveip(rt) == [], "et brudd i stripen pauset likevel"
        # To `tillat` til gjør de TRE SISTE sammenhengende — da pauser den.
        for i, start_h in enumerate((-14, -10)):
            vs = _syntetisk_vindu(migrator, pid, start_h=start_h,
                                  slutt_h=start_h + 2)
            _syntetisk_tick(migrator, pid, vs, "tillat",
                            oppdrag_id=920090 + i)
        assert (str(pid), "gjentatt_uten_resultat") in pausesveip(rt)
    finally:
        rt.close()


@pg
def test_tre_brudd_varsles_men_pauser_aldri(migrator):
    """Port 21: `brudd` pauser ALDRI — men tre på rad gir ETT varsel,
    dempet til stripen brytes."""
    from plan.materialiser import pausesveip
    bid = _ekte_bruker("p21-eier")
    rt = _rt()
    try:
        pid = _plan(rt, host="p21.example", aktor=f"bruker:{bid}")
        for i in range(3):
            vs = _syntetisk_vindu(migrator, pid, start_h=-30 - 24 * i,
                                  slutt_h=-26 - 24 * i)
            _syntetisk_tick(migrator, pid, vs, "brudd")
        assert pausesveip(rt) == []      # ingen pause av brudd
        _sett_kontekst(migrator, TENANT)
        plan_status = migrator.execute(
            "SELECT status FROM bestillingsplan WHERE plan_id=%s",
            (pid,)).fetchone()[0]
        varsler = migrator.execute(
            "SELECT count(*) FROM varsel WHERE tenant=%s AND"
            " art='plan_gjentatt_brudd' AND ressurs_id=%s",
            (TENANT, str(pid))).fetchone()[0]
        migrator.rollback()
        assert plan_status == "aktiv"
        assert varsler == 1, "tre brudd på rad ga ikke nøyaktig ett varsel"
        # Dempet: sveip igjen, og selv et fjerde brudd varsler ikke på ny.
        pausesveip(rt)
        vs4 = _syntetisk_vindu(migrator, pid, start_h=-6, slutt_h=-2)
        _syntetisk_tick(migrator, pid, vs4, "brudd")
        pausesveip(rt)
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    varsler = migrator.execute(
        "SELECT count(*) FROM varsel WHERE tenant=%s AND"
        " art='plan_gjentatt_brudd' AND ressurs_id=%s",
        (TENANT, str(pid))).fetchone()[0]
    migrator.rollback()
    assert varsler == 1, "dempingen holdt ikke"


@pg
def test_andre_bruddstripe_varsles_uten_a_velte_sveipen(migrator):
    """Codex P1: stripe nummer to har sin EGEN forekomst.

    Med `hendelse = 'varslet'` som konstant literal var varselnøkkelen
    (tenant, bruker, art, 'plan', plan_id, 'varslet') den samme for hver
    stripe. En andre stripe — korrekt gjenåpnet av et mellomliggende
    utfall — traff `varsel_en_per_hendelse`, og siden det ikke var fanget
    aborterte HELE sveiptransaksjonen: dempings-hendelsen ble rullet bort
    med den, og hvert påfølgende sveip feilet likt."""
    from plan.materialiser import pausesveip
    bid = _ekte_bruker("p21b-eier")
    rt = _rt()
    try:
        pid = _plan(rt, host="p21b.example", aktor=f"bruker:{bid}")
        for i in range(3):
            vs = _syntetisk_vindu(migrator, pid, start_h=-30 - 24 * i,
                                  slutt_h=-26 - 24 * i)
            _syntetisk_tick(migrator, pid, vs, "brudd")
        pausesveip(rt)
        # Stripen brytes av et annet utfall ...
        vs = _syntetisk_vindu(migrator, pid, start_h=-24, slutt_h=-20)
        _syntetisk_tick(migrator, pid, vs, "tillat", oppdrag_id=None)
        # ... og en HELT NY stripe bygges over den.
        for i in range(3):
            vs = _syntetisk_vindu(migrator, pid, start_h=-18 + 4 * i,
                                  slutt_h=-16 + 4 * i)
            _syntetisk_tick(migrator, pid, vs, "brudd")
        # Sveipen står — og det andre varselet nådde fram.
        assert pausesveip(rt) == []
        # Sveipen etterpå er fortsatt frisk (transaksjonen aborterte ikke).
        assert pausesveip(rt) == []
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    varsler = migrator.execute(
        "SELECT count(*) FROM varsel WHERE tenant=%s AND"
        " art='plan_gjentatt_brudd' AND ressurs_id=%s",
        (TENANT, str(pid))).fetchone()[0]
    dempinger = migrator.execute(
        "SELECT count(*) FROM bestillingsplan_hendelse WHERE plan_id=%s"
        " AND hendelse='varslet' AND detalj->>'grunn'='gjentatt_brudd'",
        (pid,)).fetchone()[0]
    migrator.rollback()
    assert varsler == 2, "stripe nummer to nådde ikke mottakeren"
    assert dempinger == 2, "dempings-hendelsen overlevde ikke"


@pg
def test_gjenopptak_krever_scope_og_nullstiller(migrator, klient):
    """Port 23: gjenopptak uten scope → nektet; med scope → ny periode og
    tellerne nullstilt (gamle tick teller ikke i den nye perioden)."""
    from plan.materialiser import pausesveip
    rt = _rt()
    try:
        pid = _plan(rt, host="p23.example")
        for i in range(3):
            vs = _syntetisk_vindu(migrator, pid, start_h=-30 - 24 * i,
                                  slutt_h=-26 - 24 * i)
            _syntetisk_tick(migrator, pid, vs, "tillat",
                            oppdrag_id=910000 + i)
        assert (str(pid), "gjentatt_uten_resultat") in pausesveip(rt)
        # Uten scope: `leser` har decisions:read, ikke plan:gjenoppta.
        cookie_l, csrf_l = _adminsesjon(sub="p23-leser", roller="leser")
        r = _post_plan(klient, cookie_l, csrf_l,
                       f"/v1/plan/{pid}/gjenoppta", {})
        assert r.status_code == 403, r.text
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT status FROM bestillingsplan WHERE plan_id=%s",
            (pid,)).fetchone()[0] == "pauset"
        migrator.rollback()
        # Med scope: admin bærer plan:gjenoppta.
        cookie, csrf = _adminsesjon(sub="p23-admin")
        r = _post_plan(klient, cookie, csrf, f"/v1/plan/{pid}/gjenoppta", {})
        assert r.status_code == 200, r.text
        # Tellerne er nullstilt: de tre gamle tickene hører til den
        # LUKKEDE perioden — sveipen pauser ikke på nytt.
        assert pausesveip(rt) == []
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    perioder = migrator.execute(
        "SELECT til_ts IS NULL FROM bestillingsplan_aktiv_periode"
        " WHERE plan_id=%s ORDER BY fra_ts", (pid,)).fetchall()
    status = migrator.execute(
        "SELECT status, pause_aarsak FROM bestillingsplan WHERE plan_id=%s",
        (pid,)).fetchone()
    migrator.rollback()
    assert status == ("aktiv", None)
    assert perioder == [(False,), (True,)], "gjenopptaket åpnet ikke en" \
        " ny periode"


@pg
def test_gjenopptak_etter_menneskelig_avvis_virker(migrator, klient):
    """Codex P1: et gjenopptak etter `menneskelig_avvis` må FESTE.

    Det kansellerte oppdraget er immutabelt og blir liggende for alltid.
    Fant sveipen det på nytt, pauset den planen igjen i samme sekund, og
    gjenopptaket var virkningsløst nettopp for den grunnen det oftest
    brukes mot. Hver avvisning skal pause ÉN gang — dempet av
    `pauset`-hendelsen som bærer oppdraget."""
    from plan.materialiser import pausesveip
    rt = _rt()
    try:
        pid = _plan(rt, host="p-avvis-gjenoppta.example")
        oid, _ = _beslutningsoppdrag(rt, migrator)
        vs = _syntetisk_vindu(migrator, pid, start_h=-30, slutt_h=-26)
        _syntetisk_tick(migrator, pid, vs, "tillat", oppdrag_id=oid)
        m = _mig()
        _sett_kontekst(m, TENANT)
        m.execute("SET ROLE disponit_m37_claimer")
        m.execute("UPDATE oppdrag SET status='kansellert',"
                  " kansellert_aarsak='menneskelig_avvis'"
                  " WHERE tenant=%s AND id=%s", (TENANT, oid))
        m.commit()
        m.close()
        assert (str(pid), "menneskelig_avvis") in pausesveip(rt)
        cookie, csrf = _adminsesjon(sub="p-avvis-admin")
        r = _post_plan(klient, cookie, csrf, f"/v1/plan/{pid}/gjenoppta", {})
        assert r.status_code == 200, r.text
        # Det samme nei-et er alt håndtert: planen står.
        assert pausesveip(rt) == []
        _sett_kontekst(migrator, TENANT)
        status = migrator.execute(
            "SELECT status, pause_aarsak FROM bestillingsplan"
            " WHERE plan_id=%s", (pid,)).fetchone()
        migrator.rollback()
        assert status == ("aktiv", None), status
        # ... men et NYTT nei pauser igjen: dempingen er per oppdrag.
        oid2, _ = _beslutningsoppdrag(rt, migrator)
        vs2 = _syntetisk_vindu(migrator, pid, start_h=-78, slutt_h=-74)
        _syntetisk_tick(migrator, pid, vs2, "tillat", oppdrag_id=oid2)
        m = _mig()
        _sett_kontekst(m, TENANT)
        m.execute("SET ROLE disponit_m37_claimer")
        m.execute("UPDATE oppdrag SET status='kansellert',"
                  " kansellert_aarsak='menneskelig_avvis'"
                  " WHERE tenant=%s AND id=%s", (TENANT, oid2))
        m.commit()
        m.close()
        assert (str(pid), "menneskelig_avvis") in pausesveip(rt)
    finally:
        rt.close()


@pg
def test_kvoten_deles_med_manuelle_bestillinger(migrator, app, klient):
    """Port 25: plan og menneske deler motorens teller — fire manuelle
    TILLAT, planens femte får `brudd` (og pauser ikke)."""
    from plan.materialiser import materialiser_en
    _wcag_policy(migrator)
    _verifiser_domene(migrator, "p25.example")
    cookie, csrf = _adminsesjon()
    for i in range(4):
        r = _bestill(klient, cookie, csrf, _gyldig_kropp("p25.example"))
        assert r.json()["beslutning"] == "tillat", (i, r.text)
    rt = _rt()
    try:
        pid = _plan_forfalt(rt, migrator, host="p25.example")
        rad = _mine_forfalte(rt, pid)[0]
        res = materialiser_en(app.tjeneste, rt, rad)
        assert res["utfall"] == "brudd", res
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    status = migrator.execute(
        "SELECT status FROM bestillingsplan WHERE plan_id=%s",
        (pid,)).fetchone()[0]
    migrator.rollback()
    assert status == "aktiv", "brudd skal aldri pause (kvoten er ikke brukt)"


# ---------------------------------------------------------------------------
# HTTP-flaten (§6)
# ---------------------------------------------------------------------------

@pg
@dekker("plan_ulovlig_tilstand")
def test_http_flaten_ende_til_ende(migrator, klient):
    """§6: opprett (201, utkast) → aktiver → liste → historikk → stans;
    ulovlig overgang → 409; lesing krever bare decisions:read."""
    cookie, csrf = _adminsesjon(sub="http-e2e")
    r = _post_plan(klient, cookie, csrf, "/v1/plan",
                   _plan_kropp("e2e.example", rytme="ukentlig", ukedag=2))
    assert r.status_code == 201, r.text
    pid = r.json()["plan_id"]
    assert r.json()["status"] == "utkast"
    # Aktiver; deretter er en ny aktivering ulovlig tilstand.
    r = _post_plan(klient, cookie, csrf, f"/v1/plan/{pid}/aktiver", {})
    assert r.status_code == 200, r.text
    r = _post_plan(klient, cookie, csrf, f"/v1/plan/{pid}/aktiver", {})
    assert (r.status_code, r.json()["feil"]) == (
        409, "plan_ulovlig_tilstand"), r.text
    # Liste og historikk med LESE-rollen.
    from api import sesjon as sesjonmodul
    cookie_l, csrf_l = _adminsesjon(sub="http-leser", roller="leser")
    r = klient.get("/v1/plan", cookies={sesjonmodul.C_SESJON: cookie_l})
    assert r.status_code == 200, r.text
    mine = [p for p in r.json()["planer"] if p["plan_id"] == pid]
    assert mine and mine[0]["status"] == "aktiv" \
        and mine[0]["rytme"] == "ukentlig" and mine[0]["ukedag"] == 2
    r = klient.get(f"/v1/plan/{pid}/historikk",
                   cookies={sesjonmodul.C_SESJON: cookie_l})
    assert r.status_code == 200 and r.json()["hendelser"], r.text
    # ... men leseren kan ikke opprette.
    r = _post_plan(klient, cookie_l, csrf_l, "/v1/plan",
                   _plan_kropp("e2e2.example"))
    assert r.status_code == 403, r.text
    # Stans er endelig.
    r = _post_plan(klient, cookie, csrf, f"/v1/plan/{pid}/stans", {})
    assert r.status_code == 200, r.text
    r = _post_plan(klient, cookie, csrf, f"/v1/plan/{pid}/gjenoppta", {})
    assert (r.status_code, r.json()["feil"]) == (
        409, "plan_ulovlig_tilstand"), r.text
    # Ukjent plan → ikke_funnet, ikke tilstandslekkasje.
    r = _post_plan(klient, cookie, csrf,
                   "/v1/plan/00000000-0000-4000-8000-000000000000/aktiver",
                   {})
    assert r.status_code == 404, r.text


@pg
@dekker("idempotensnokkel_mangler")
def test_opprettelsen_er_replay_sikker(migrator, klient):
    """Codex P1: et tapt svar skal GJENSPILLE planen, ikke lage nummer to.

    Uten operasjonsnøkkel var opprettelsen den ene skriveruten her uten
    gjenspill: committet serveren kallet og mistet svaret, laget andre
    klikk en NY plan med identiske parametre. Aktivert konsumerte de hver
    sin kvoteplass og bestilte samme kontroll i all fremtid."""
    cookie, csrf = _adminsesjon(sub="replay-admin")
    kropp = _plan_kropp("replay.example")
    # Nøkkelen er PÅKREVD.
    from api import sesjon as sesjonmodul
    r = klient.post("/v1/plan", json=kropp,
                    headers={"X-Disponit-CSRF": csrf},
                    cookies={sesjonmodul.C_SESJON: cookie})
    assert (r.status_code, r.json()["feil"]) == (
        400, "idempotensnokkel_mangler"), r.text
    # Første kall lager planen ...
    idem = "plan-replay-" + secrets.token_hex(8)
    r1 = _post_plan(klient, cookie, csrf, "/v1/plan", kropp, idem=idem)
    assert r1.status_code == 201, r1.text
    pid = r1.json()["plan_id"]
    # ... og gjenspillet gir NØYAKTIG samme plan-id, ikke en ny plan.
    r2 = _post_plan(klient, cookie, csrf, "/v1/plan", kropp, idem=idem)
    assert r2.status_code == 201, r2.text
    assert r2.json()["plan_id"] == pid, "gjenspillet ga en annen plan"
    _sett_kontekst(migrator, TENANT)
    antall = migrator.execute(
        "SELECT count(*) FROM bestillingsplan WHERE tenant=%s AND"
        " parametre->>'hostname'=%s", (TENANT, "replay.example")).fetchone()[0]
    migrator.rollback()
    assert antall == 1, "gjenspillet opprettet en dublettplan"
    # Samme nøkkel, ANNEN plan er en annen operasjon → konflikt, aldri et
    # gjenspill av feil plan-id.
    r3 = _post_plan(klient, cookie, csrf, "/v1/plan",
                    _plan_kropp("replay-annen.example"), idem=idem)
    assert (r3.status_code, r3.json()["feil"]) == (
        409, "idempotenskonflikt"), r3.text
    # En AVVIST kropp brenner ingen nøkkel: claimet rulles med avvisningen.
    idem2 = "plan-replay-" + secrets.token_hex(8)
    r4 = _post_plan(klient, cookie, csrf, "/v1/plan",
                    _plan_kropp("replay2.example", rytme="manedlig",
                                manedsdag=31), idem=idem2)
    assert r4.status_code == 400, r4.text
    r5 = _post_plan(klient, cookie, csrf, "/v1/plan",
                    _plan_kropp("replay2.example"), idem=idem2)
    assert r5.status_code == 201, r5.text
