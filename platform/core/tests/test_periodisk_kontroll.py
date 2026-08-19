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


def _aktiver_i_fortid(m, pid, *, dager=0, timer=0, aktor="test:plan"):
    """Aktivering med fortidig `fra_ts`. Perioderadene er immutable også
    for migratorrollen (identiteten inkluderer fra_ts), så fortiden
    KONSTRUERES ved innsetting — aldri ved å flytte en eksisterende rad.
    Speiler nøyaktig det aktiver_plan skriver, minus hendelsen —
    `aktivert_av` inkludert: varselveien finner mottakeren i nettopp den
    kolonnen, så en hardkodet aktør ville gjort planen mottakerløs."""
    _sett_kontekst(m, TENANT)
    m.execute("UPDATE bestillingsplan SET status='aktiv',"
              " aktivert_av=%s"
              " WHERE plan_id=%s AND status='utkast'", (aktor, pid))
    m.execute("INSERT INTO bestillingsplan_aktiv_periode"
              " (plan_id, tenant, fra_ts) VALUES"
              " (%s,%s, now() - make_interval(days => %s, hours => %s))",
              (pid, TENANT, dager, timer))
    m.commit()


def _plan_forfalt(rt, m, *, host, aktiver_dager=1, **kw):
    """Aktiv plan hvis dagens vindu ER kvalifisert: aktiveringen legges i
    går, ellers ville fra_ts > forfall gjort planen til port
    32-tilfellet (aktivert etter forfall = ingen rad).

    Den samme konstruksjonen er nødvendig for ALT syntetisk tick-arkiv:
    sveipene måler tickets periodetilhørighet på FORFALLET, og et vindu
    som forfalt før planen noensinne var aktiv kunne uansett aldri blitt
    claimet (port 32) — altså heller aldri fått et tick. `aktiver_dager`
    må derfor dekke det ELDSTE syntetiske vinduet i testen."""
    pid = _plan(rt, host=host, aktiver=False, **kw)
    _aktiver_i_fortid(m, pid, dager=aktiver_dager,
                      aktor=kw.get("aktor", "test:plan"))
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
def test_boolsk_ukedag_er_400_ikke_driftsalarm(migrator, klient, capsys):
    """Codex P2: `bool` er en subklasse av `int` i Python.

    `{"ukedag": true}` besto derfor både `isinstance(..., int)` og
    `1 <= True <= 7`. Psycopg bandt så verdien som PostgreSQL `boolean`
    mot en `SMALLINT`-parameter: funksjonsoppslaget feilet,
    `_med_browserkontekst` leste det som en generisk databasefeil, og en
    feilformet kropp ble til 503 `db_utilgjengelig` MED driftshendelse —
    en falsk alarm en klient kan utløse på kommando. `time_lokal` har
    utelukket bool hele tiden; disse to gjør det nå også."""
    cookie, csrf = _adminsesjon(sub="boolsk-ukedag")
    capsys.readouterr()
    for kropp in (
            _plan_kropp("pb1.example", rytme="ukentlig", ukedag=True),
            _plan_kropp("pb2.example", rytme="ukentlig", ukedag=False),
            _plan_kropp("pb3.example", rytme="manedlig", manedsdag=True)):
        r = _post_plan(klient, cookie, csrf, "/v1/plan", kropp)
        assert (r.status_code, r.json()["feil"]) == (
            400, "request_feilformet"), r.text
    assert "db_utilgjengelig" not in capsys.readouterr().out, \
        "en feilformet kropp førte en driftshendelse"


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
def test_definerne_binder_tenanten_til_konteksten(migrator):
    """Codex P1: `p_tenant` er kallerens ord, tenantkonteksten er ikke.

    Definer-funksjonene kjører som claimeren og ser dermed forbi FORCE
    RLS. Uten porten kunne en kompromittert runtime-credential lese en
    ANNEN tenants planflate — eller opprette og aktivere en plan der — ved
    å sende tenantnavnet som parameter. Porten binder parameteret til
    GUC-en `sett_kontekst` setter, og er fail-closed uten kontekst.
    """
    pid = None
    rt = _rt()
    try:
        pid = _plan(rt, host="port-tenantbinding.example", aktiver=False)
        annen = f"{TENANT}-fremmed"
        # 1. Feil tenant i parameteret, riktig kontekst → avvist.
        for sql, arg in (
                ("SELECT count(*) FROM hent_planer(%s)", (annen,)),
                ("SELECT count(*) FROM hent_plan_tick(%s,%s,50)",
                 (annen, pid)),
                ("SELECT count(*) FROM hent_plan_hendelser(%s,%s,50)",
                 (annen, pid)),
                # `9::smallint`, ikke `9`: literalen er `integer`, og
                # integer→smallint er ingen implisitt cast i
                # funksjonsoppslaget (psycopg binder derimot små int-er
                # som int2, derfor virker `_plan` uten cast).
                ("SELECT opprett_plan(%s,'kontroll.wcag.nettsted',%s,"
                 "'daglig',NULL,NULL,9::smallint,'Europe/Oslo','test:x',"
                 "'r-x')",
                 (annen, json.dumps(_param("fremmed.example")))),
                ("SELECT aktiver_plan(%s,%s,'test:x','r-x')", (annen, pid)),
                ("SELECT stans_plan(%s,%s,'test:x','r-x')", (annen, pid)),
                ("SELECT claim_planvindu(%s,%s,now(),120)", (annen, pid)),
        ):
            _sett_kontekst(rt, TENANT)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rt.execute(sql, arg)
            rt.rollback()

        # 2. INGEN kontekst i det hele tatt → avvist (fail-closed), selv
        #    med planens EGEN tenant i parameteret.
        rt.execute("SELECT set_config('disponit.tenant','',true)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT count(*) FROM hent_planer(%s)", (TENANT,))
        rt.rollback()

        # 3. Riktig kontekst → uendret vei inn. Porten stenger ikke døra
        #    for den som faktisk står i tenanten.
        _sett_kontekst(rt, TENANT)
        assert rt.execute("SELECT count(*) FROM hent_planer(%s)",
                          (TENANT,)).fetchone()[0] >= 1
        rt.rollback()
    finally:
        rt.close()


@pg
def test_vindusfunksjonene_binder_raden_til_tenanten(migrator):
    """Codex P1: kontekstporten beviser hvem kalleren ER, ikke at raden
    hører til den.

    Angrepet forrige runde IKKE stengte: kalleren setter sin EGEN,
    lovlige kontekst og sender sin EGEN tenant som `p_tenant` — porten
    slipper den glatt gjennom — men oppgir en ANNEN tenants plan-id.
    Oppslaget i vindusfunksjonene sto på (plan_id, vindu_start) alene, og
    et `ledig` vindu krever ikke noe claim: `terminaliser_planvindu` ville
    lukket offerets vindu og skrevet et tick merket ANGRIPERENS tenant.

    To uavhengige mekanismer måles: tenantleddet i hvert oppslag, og den
    sammensatte FK-en som gjør et feilmerket tick ulagrbart uansett.
    """
    offer = f"{TENANT}-offer"
    m = _mig()
    rt = _rt()
    try:
        # Offerets plan og et ÅPENT vindu (ledig, forfalt, ikke utløpt).
        _sett_kontekst(m, offer)
        opid = m.execute(
            "INSERT INTO bestillingsplan (tenant, bestillingstype,"
            " parametre, rytme, time_lokal, tidssone, opprettet_av, status)"
            " VALUES (%s,'kontroll.wcag.nettsted',%s,'daglig',8,"
            " 'Europe/Oslo','test:offer','aktiv') RETURNING plan_id",
            (offer, json.dumps(_param("offer.example")))).fetchone()[0]
        vs = m.execute(
            "INSERT INTO bestillingsplan_vindu (plan_id, tenant,"
            " vindu_start, vindu_slutt) VALUES (%s,%s,"
            " now()-make_interval(hours=>1), now()+make_interval(hours=>1))"
            " RETURNING vindu_start", (opid, offer)).fetchone()[0]
        m.commit()

        # Angriperen står lovlig i SIN egen tenant hele veien.
        _sett_kontekst(rt, TENANT)
        assert rt.execute("SELECT utfall FROM claim_planvindu(%s,%s,%s,120)",
                          (TENANT, opid, vs)).fetchone()[0] == "ukjent"
        assert rt.execute("SELECT frigi_planvindu(%s,%s,%s,NULL)",
                          (TENANT, opid, vs)).fetchone()[0] == "ukjent"
        with pytest.raises(psycopg.errors.NoDataFound):
            rt.execute(
                "SELECT terminaliser_planvindu(%s,%s,%s,NULL,%s,'tillat',"
                "NULL,NULL)", (TENANT, opid, vs, "n-" + secrets.token_hex(8)))
        rt.rollback()

        # Offerets vindu står urørt, og har fortsatt ikke noe tick.
        _sett_kontekst(m, offer)
        assert m.execute("SELECT tilstand FROM bestillingsplan_vindu"
                         " WHERE plan_id=%s", (opid,)).fetchone()[0] == "ledig"
        assert m.execute("SELECT count(*) FROM bestillingsplan_tick"
                         " WHERE plan_id=%s", (opid,)).fetchone()[0] == 0
        m.rollback()

        # Den sammensatte FK-en: et tick kan ikke bære en annen eier enn
        # vinduet det lukker — heller ikke skrevet direkte, forbi enhver
        # funksjon. Samme vern for vindusraden mot planen.
        _sett_kontekst(m, TENANT)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            m.execute(
                "INSERT INTO bestillingsplan_tick (plan_id, tenant,"
                " vindu_start, idempotensnokkel, utfall)"
                " VALUES (%s,%s,%s,%s,'tillat')",
                (opid, TENANT, vs, "n-" + secrets.token_hex(8)))
        m.rollback()
        _sett_kontekst(m, TENANT)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            m.execute(
                "INSERT INTO bestillingsplan_vindu (plan_id, tenant,"
                " vindu_start, vindu_slutt) VALUES (%s,%s,"
                " now()+make_interval(hours=>5),"
                " now()+make_interval(hours=>7))", (opid, TENANT))
        m.rollback()
    finally:
        m.close()
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
        # Aktiveringen legges i går: claimet revaliderer kvalifiseringen
        # (forfallet i en aktiv periode), og en plan aktivert NÅ dekker
        # ikke et vindu som forfalt for en time siden.
        pid = _plan_forfalt(rt, migrator, host="p48.example")
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


@pg
def test_claimet_nekter_en_stanset_plan(migrator, app, monkeypatch):
    """Codex P1: planens tilstand må revalideres ATOMISK med claimet.

    Plukket kvalifiserer en BATCH i sin egen, committede transaksjon, og
    radene arbeides ned sekvensielt med et HTTP-kall hver. Stanser en
    administrator planen i mellomtiden, sto bare vindusraden imot — og en
    stanset plan kunne fortsatt konsumere en kvoteplass og starte en
    ekstern skanning. Ordren skal gjelde fra den committes."""
    from plan import materialiser
    rt = _rt()
    try:
        pid = _plan_forfalt(rt, migrator, host="p-stanset.example")
        rad = _mine_forfalte(rt, pid)[0]
        vs = rad[2]
        # Administratoren stanser planen ETTER at batchen er plukket.
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT stans_plan(%s,%s,'test:admin','r-stans')",
                   (TENANT, pid))
        rt.commit()

        _sett_kontekst(rt, TENANT)
        assert rt.execute("SELECT utfall FROM claim_planvindu(%s,%s,%s,120)",
                          (TENANT, pid, vs)).fetchone()[0] == "ikke_aktiv"
        rt.commit()

        def aldri(*a, **k):
            raise AssertionError("en stanset plan nådde bestillingsveien")
        import api.bestilling
        monkeypatch.setattr(api.bestilling, "utfor_bestilling", aldri)
        res = materialiser.materialiser_en(app.tjeneste, rt, rad)
        assert res.get("hoppet") == "ikke_aktiv", res
        # Vinduet er urørt: ingen bestilling, intet tick, ingen kvote.
        _sett_kontekst(migrator, TENANT)
        vindu = migrator.execute(
            "SELECT tilstand, claim_id FROM bestillingsplan_vindu"
            " WHERE plan_id=%s AND vindu_start=%s", (pid, vs)).fetchone()
        tick = migrator.execute(
            "SELECT count(*) FROM bestillingsplan_tick WHERE plan_id=%s",
            (pid,)).fetchone()[0]
        migrator.rollback()
        assert (vindu, tick) == (("ledig", None), 0), (vindu, tick)
    finally:
        rt.close()


@pg
def test_claimet_nekter_en_pauset_plan(migrator):
    """Samme port, den andre veien inn: en PAUSE mellom plukket og turen
    stopper bestillingen.

    Andre halvdel er like viktig: claimet skal aldri være STRENGERE enn
    plukket. Gjenopptas planen mens vinduet fortsatt er åpent, hører
    forfallet fortsatt til den perioden planen var aktiv i, og plukket
    ville delt raden ut på nytt — da skal claimet også gi den. Ellers
    ville et pause/gjenoppta-par inne i et åpent vindu stille konsumert
    det, uten tick og uten at noen ba om det."""
    rt = _rt()
    try:
        pid = _plan_forfalt(rt, migrator, host="p-pauset-claim.example")
        vs = _mine_forfalte(rt, pid)[0][2]
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT pause_plan(%s,%s,'policy_stopper','test:admin',"
                   "'r-pause',NULL)", (TENANT, pid))
        rt.commit()
        _sett_kontekst(rt, TENANT)
        assert rt.execute("SELECT utfall FROM claim_planvindu(%s,%s,%s,120)",
                          (TENANT, pid, vs)).fetchone()[0] == "ikke_aktiv"
        rt.commit()
        # Gjenopptatt, og vinduet står fortsatt åpent: forfallet ligger i
        # perioden planen VAR aktiv i (lukket ved pausen, altså etter
        # forfallet), så plukket ville delt raden ut igjen — og claimet
        # måler nøyaktig samme regel.
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT gjenoppta_plan(%s,%s,'test:admin','r-gjen')",
                   (TENANT, pid))
        rt.commit()
        assert len(_mine_forfalte(rt, pid)) == 1, "plukket ga ingen rad"
        _sett_kontekst(rt, TENANT)
        assert rt.execute("SELECT utfall FROM claim_planvindu(%s,%s,%s,120)",
                          (TENANT, pid, vs)).fetchone()[0] == "claimet"
        rt.commit()
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
def test_fullfort_forsok_gjenopprettes_etter_utlopt_claim(migrator):
    """Codex P1: et vindu som ER bestilt skal få sitt tick, også når
    arbeideren døde mellom commit og terminalisering.

    Da står vinduet `aktivt` med et claim ingen lenger holder. Plukket tar
    det aldri igjen (`now() < vindu_slutt` holder ikke lenger), og
    klassifisereren eier per §5 intet claim — så fencingen avviste den, og
    sveipen meldte `ventet` om igjen og om igjen, for alltid.

    Gjenopprettingen er smal: intet claim fra kalleren, DØD lease, og
    utfallet verifisert mot den immutable `bestilling_idempotens`-raden.
    Klassifisereren gjetter aldri — den skriver ned det bestillingsveien
    alt har besluttet."""
    from plan.klassifiser import _nokkel, klassifiser_vinduer
    rt = _rt()
    try:
        pid = _plan(rt, host="p-gjenoppr.example")
        # Arbeideren claimet, bestilte og døde: vinduet står `aktivt` med
        # en lease som siden løp ut, og klokken er forbi vindu_slutt.
        vs = _syntetisk_vindu(migrator, pid, start_h=-8, slutt_h=-4,
                              tilstand="aktivt", lease_h=-2)
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "INSERT INTO bestilling_idempotens (tenant, idempotensnokkel,"
            " intensjonshash, oppdrag_id, beslutning, svarkropp) VALUES"
            " (%s,%s,%s,NULL,'tillat','{\"oppdrag_id\": 515151}')",
            (TENANT, _nokkel(pid, vs), "2" * 64))
        migrator.commit()

        # Fencingen står fortsatt for et FEIL claim og for et utfall som
        # ikke er fasitens.
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT terminaliser_planvindu(%s,%s,%s,"
                       "gen_random_uuid(),%s,'tillat',NULL,NULL)",
                       (TENANT, pid, vs, _nokkel(pid, vs)))
        rt.rollback()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT terminaliser_planvindu(%s,%s,%s,NULL,%s,"
                       "'brudd',NULL,NULL)",
                       (TENANT, pid, vs, _nokkel(pid, vs)))
        rt.rollback()

        res = klassifiser_vinduer(rt)
        assert not any(v["plan"] == str(pid) for v in res.get("ventet", [])), \
            res
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    tick = migrator.execute(
        "SELECT utfall, oppdrag_id, detalj FROM bestillingsplan_tick"
        " WHERE plan_id=%s AND vindu_start=%s", (pid, vs)).fetchone()
    tilstand = migrator.execute(
        "SELECT tilstand FROM bestillingsplan_vindu WHERE plan_id=%s"
        " AND vindu_start=%s", (pid, vs)).fetchone()[0]
    migrator.rollback()
    assert tick is not None, "det bestilte vinduet fikk aldri sitt tick"
    assert (tick[0], tick[1]) == ("tillat", 515151), tick
    assert tick[2]["kilde"] == "bestilling_idempotens"
    assert tilstand == "terminal"


@pg
def test_ledig_vindu_kan_ikke_terminaliseres_uten_claim_eller_fasit(migrator):
    """Codex P1 (#105, etter merge): eierskap er ikke autoritet.

    Claim-sjekken sto som `ELSIF v.tilstand = 'aktivt' AND ...`. For et
    `ledig` vindu var betingelsen usann, ingen gren kjørte, og et hvilket
    som helst ikke-`hoppet_over`-utfall gikk rett gjennom til UPDATE +
    INSERT — uten verken claim eller idempotensrad.

    Angrepet står i sin EGEN, lovlige tenantkontekst og på sitt EGET
    vindu, så begge de tidligere gjerdene slipper det glatt gjennom:
    `krev_tenantkontekst` beviser hvem kalleren er, tenantleddet at raden
    hører til den. Ingen av dem måler at forsøket har RETT til å felle
    vinduet. Resultatet var et `tillat`-tick som aldri passerte policy,
    kvote eller bestillingsvei — vinduet konsumert, og evidensen påstår
    at kontrollen ble kjørt.

    Tre halvdeler måles: forfalskningen avvises, det legitime claimet
    slipper gjennom, og fasitveien for et `ledig` vindu står (en
    `frigi_planvindu` etter et driftsuhell KAN etterlate et ledig vindu
    med idempotensrad — der er fasiten like bindende)."""
    from plan.klassifiser import _nokkel
    rt = _rt()
    try:
        pid = _plan_forfalt(rt, migrator, host="p105-p1.example")
        # Et ÅPENT, ledig vindu — nøyaktig det plukket ville delt ut.
        vs = _syntetisk_vindu(migrator, pid, start_h=-1, slutt_h=1,
                              tilstand="ledig")
        _sett_kontekst(rt, TENANT)
        for utfall in ("tillat", "brudd", "stopp"):
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                rt.execute(
                    "SELECT terminaliser_planvindu(%s,%s,%s,NULL,%s,%s,"
                    "NULL,NULL)", (TENANT, pid, vs, _nokkel(pid, vs), utfall))
            rt.rollback()
        # Et oppdiktet claim hjelper heller ikke: vinduet er ikke claimet.
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT terminaliser_planvindu(%s,%s,%s,"
                       "gen_random_uuid(),%s,'tillat',NULL,NULL)",
                       (TENANT, pid, vs, _nokkel(pid, vs)))
        rt.rollback()

        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT count(*) FROM bestillingsplan_tick WHERE plan_id=%s",
            (pid,)).fetchone()[0] == 0, "forfalsket tick kom inn"
        assert migrator.execute(
            "SELECT tilstand FROM bestillingsplan_vindu WHERE plan_id=%s"
            " AND vindu_start=%s", (pid, vs)).fetchone()[0] == "ledig"
        migrator.rollback()

        # Den LEGITIME veien er urørt: claim → terminalisering.
        _sett_kontekst(rt, TENANT)
        claim = rt.execute(
            "SELECT claim_id FROM claim_planvindu(%s,%s,%s,120)",
            (TENANT, pid, vs)).fetchone()[0]
        assert claim is not None
        assert rt.execute(
            "SELECT terminaliser_planvindu(%s,%s,%s,%s,%s,'tillat',NULL,NULL)",
            (TENANT, pid, vs, claim, _nokkel(pid, vs))).fetchone()[0] \
            == "terminalisert"
        rt.commit()

        # Fasitveien for et LEDIG vindu: frigitt etter et driftsuhell, men
        # bestillingen hadde alt committet. Verifisert utfall slipper inn.
        pid2 = _plan_forfalt(rt, migrator, host="p105-p1b.example")
        vs2 = _syntetisk_vindu(migrator, pid2, start_h=-1, slutt_h=1,
                               tilstand="ledig")
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "INSERT INTO bestilling_idempotens (tenant, idempotensnokkel,"
            " intensjonshash, oppdrag_id, beslutning, svarkropp) VALUES"
            " (%s,%s,%s,NULL,'brudd','{}')",
            (TENANT, _nokkel(pid2, vs2), "3" * 64))
        migrator.commit()
        _sett_kontekst(rt, TENANT)
        # Et utfall som IKKE er fasitens avvises fortsatt.
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT terminaliser_planvindu(%s,%s,%s,NULL,%s,"
                       "'tillat',NULL,NULL)",
                       (TENANT, pid2, vs2, _nokkel(pid2, vs2)))
        rt.rollback()
        _sett_kontekst(rt, TENANT)
        assert rt.execute(
            "SELECT terminaliser_planvindu(%s,%s,%s,NULL,%s,'brudd',"
            "NULL,NULL)",
            (TENANT, pid2, vs2, _nokkel(pid2, vs2))).fetchone()[0] \
            == "terminalisert"
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


@pg
def test_gammelt_bestilt_vindu_velter_ikke_aggregatet(migrator):
    """Codex P1: aggregatløkken TVANG `hoppet_over` på hver eneste rad.

    Committet arbeideren bestillingen og døde før terminaliseringen, og
    vinduet siden ble eldre enn tilbakeblikket, havnet det her — med en
    idempotensrad. `terminaliser_planvindu` nekter da `hoppet_over` (med
    rette: det BLE bestilt), og exception-en rullet tilbake hele
    aggregatet og veltet hver eneste senere timerkjøring på samme rad:
    planen fikk aldri sin nedetidshendelse, og vinduet aldri sitt tick.

    Fasiten leses nå her også, og hvert vindu står på sitt eget
    savepoint.
    """
    from plan.klassifiser import _nokkel, klassifiser_vinduer
    rt = _rt()
    try:
        pid = _plan(rt, host="p35d.example", aktiver=False)
        _aktiver_i_fortid(migrator, pid, dager=40)
        # Vinduet arbeideren døde midt i: `aktivt`, men med DØD lease.
        vs = _syntetisk_vindu(migrator, pid, start_h=-24 * 35,
                              slutt_h=-24 * 35 + 4, tilstand="aktivt",
                              lease_h=-24 * 34)
        # ... og et vanlig missed vindu ved siden av, som skal bli
        # `hoppet_over` i samme runde.
        vs_tomt = _syntetisk_vindu(migrator, pid, start_h=-24 * 34,
                                   slutt_h=-24 * 34 + 4, tilstand="ledig")
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "INSERT INTO bestilling_idempotens (tenant, idempotensnokkel,"
            " intensjonshash, oppdrag_id, beslutning, svarkropp) VALUES"
            " (%s,%s,%s,NULL,'tillat','{\"oppdrag_id\": 909090}')",
            (TENANT, _nokkel(pid, vs), "3" * 64))
        migrator.commit()

        res = klassifiser_vinduer(rt)          # skal IKKE kaste
        assert not any(v["plan"] == str(pid) for v in res.get("ventet", [])), \
            res
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    tick = dict(migrator.execute(
        "SELECT vindu_start, utfall FROM bestillingsplan_tick"
        " WHERE plan_id=%s", (pid,)).fetchall())
    agg = migrator.execute(
        "SELECT count(*) FROM bestillingsplan_hendelse WHERE plan_id=%s"
        " AND hendelse='nedetid_aggregert'", (pid,)).fetchone()[0]
    migrator.rollback()
    assert agg == 1, "aggregatet overlevde ikke det bestilte vinduet"
    assert tick.get(vs) == "tillat", tick
    assert tick.get(vs_tomt) == "hoppet_over", tick


@pg
def test_bestilte_vinduer_telles_ikke_som_nedetid(migrator):
    """Codex P2 (runde 4): aggregatet ble SKREVET før fasiten ble lest.

    `plan_nedetid_kandidater` teller hver gammel ikke-terminal rad som
    savnet — også en `aktivt`-rad der arbeideren committet bestillingen og
    døde. Hendelsen meldte den som savnet, og løkken rett etterpå skrev et
    `tillat`-tick for nøyaktig det samme vinduet: to sanne kilder som sier
    motsatt ting om det samme vinduet, og hendelsen er den bare et
    menneske leser.

    Målt som en DIFFERANSE, ikke som et absolutt tall: to identiske planer
    leses i SAMME kandidatspørring (ett `now()`), og bare den ene har
    idempotensraden. Da er antallet forekomster likt per konstruksjon, og
    forskjellen er nøyaktig det ene bestilte vinduet.

    MUTASJONEN SOM DREPER DENNE: flytt `plan_nedetid_aggregert` tilbake
    foran løkken og send `antall` uendret.
    """
    from plan.klassifiser import _nokkel, klassifiser_vinduer
    rt = _rt()
    vs = {}
    try:
        pid_a = _plan(rt, host="p35e-a.example", aktiver=False)
        pid_b = _plan(rt, host="p35e-b.example", aktiver=False)
        for pid in (pid_a, pid_b):
            _aktiver_i_fortid(migrator, pid, dager=40)
            vs[pid] = _syntetisk_vindu(migrator, pid, start_h=-24 * 35,
                                       slutt_h=-24 * 35 + 4,
                                       tilstand="aktivt", lease_h=-24 * 34)
        # KUN plan A har fasiten: bestillingen ER committet.
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "INSERT INTO bestilling_idempotens (tenant, idempotensnokkel,"
            " intensjonshash, oppdrag_id, beslutning, svarkropp) VALUES"
            " (%s,%s,%s,NULL,'tillat','{\"oppdrag_id\": 909191}')",
            (TENANT, _nokkel(pid_a, vs[pid_a]), "4" * 64))
        migrator.commit()
        klassifiser_vinduer(rt)
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    tall = dict(migrator.execute(
        "SELECT plan_id, (detalj->>'vinduer')::int"
        "  FROM bestillingsplan_hendelse WHERE plan_id = ANY(%s)"
        "   AND hendelse='nedetid_aggregert'",
        ([pid_a, pid_b],)).fetchall())
    tick = migrator.execute(
        "SELECT utfall FROM bestillingsplan_tick"
        " WHERE plan_id=%s AND vindu_start=%s",
        (pid_a, vs[pid_a])).fetchone()
    migrator.rollback()
    assert tick == ("tillat",), tick
    assert len(tall) == 2, tall
    assert tall[pid_b] - tall[pid_a] == 1, \
        f"det bestilte vinduet ble talt som nedetid: {tall}"


@pg
def test_langt_avbrudd_telles_fra_rytmen(migrator):
    """Codex P2: aggregatet må telle FOREKOMSTER, ikke bare rader.

    Et 90-døgns avbrudd etterlater INGEN vindusrad for døgn 90→30:
    `utlopte_planvinduer` enumererer kun tilbakeblikket. Et aggregat som
    bare grupperte eksisterende rader meldte derfor enten ingenting eller
    kun grenseraden — og §5s løfte om ÉN hendelse som forteller sant om
    avbruddet var ikke innfridd. Og fortsatt ÉN: ikke én per sveip."""
    from plan.klassifiser import klassifiser_vinduer
    rt = _rt()
    try:
        pid = _plan(rt, host="p35b.example", aktiver=False)
        _aktiver_i_fortid(migrator, pid, dager=90)
        klassifiser_vinduer(rt)
        _sett_kontekst(migrator, TENANT)
        agg = migrator.execute(
            "SELECT detalj FROM bestillingsplan_hendelse WHERE plan_id=%s"
            " AND hendelse='nedetid_aggregert'", (pid,)).fetchall()
        migrator.rollback()
        assert len(agg) == 1, agg
        detalj = agg[0][0]
        # Døgn 90→30 er ~60 daglige forekomster, ingen av dem materialisert.
        assert detalj["vinduer"] >= 50, detalj
        assert detalj["avkortet"] is False, detalj
        # ... og neste sveip melder INGENTING nytt: `til` er dempingen.
        klassifiser_vinduer(rt)
        _sett_kontekst(migrator, TENANT)
        antall = migrator.execute(
            "SELECT count(*) FROM bestillingsplan_hendelse WHERE plan_id=%s"
            " AND hendelse='nedetid_aggregert'", (pid,)).fetchone()[0]
        migrator.rollback()
        assert antall == 1, "avbruddet ble meldt på nytt i neste sveip"
    finally:
        rt.close()


@pg
def test_stanset_plan_faar_ogsaa_sitt_nedetidsaggregat(migrator):
    """Codex P2: et statusfilter gjorde stansen til en stille utelatelse.

    Var planleggeren nede i mer enn 30 døgn og en administrator stanset
    planen før klassifiseringen kom i gang igjen, falt planen ut av
    kandidatene for godt: det nære tilbakeblikket ble fortsatt klassifisert
    av `utlopte_planvinduer` (uten et slikt filter), mens alt eldre
    forsvant — og en stanset plan kan ikke gjenopptas for å bli kandidat
    igjen. Historikken, som er nettopp det som blir igjen etter en stans,
    manglet da hendelsen §5 lover.

    Stansen holdes fortsatt ute av PERIODENE, ikke av et statusfilter:
    forekomster etter `til_ts` kvalifiserer ikke."""
    from plan.klassifiser import klassifiser_vinduer
    rt = _rt()
    try:
        pid = _plan(rt, host="p35c.example", aktiver=False)
        _aktiver_i_fortid(migrator, pid, dager=90)
        # Aktiv i 90→60 døgn, stanset for 60 døgn siden. Perioderaden
        # lukkes der stansen skjedde; en lukket periode er endelig, så
        # fortiden konstrueres ved lukkingen selv (som _aktiver_i_fortid).
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "UPDATE bestillingsplan_aktiv_periode"
            "   SET til_ts = now() - interval '60 days',"
            "       aarsak_slutt = 'stanset'"
            " WHERE plan_id=%s AND til_ts IS NULL", (pid,))
        migrator.execute("UPDATE bestillingsplan SET status='stanset'"
                         " WHERE plan_id=%s", (pid,))
        migrator.commit()
        klassifiser_vinduer(rt)
        _sett_kontekst(migrator, TENANT)
        agg = migrator.execute(
            "SELECT detalj FROM bestillingsplan_hendelse WHERE plan_id=%s"
            " AND hendelse='nedetid_aggregert'", (pid,)).fetchall()
        migrator.rollback()
        assert len(agg) == 1, agg
        detalj = agg[0][0]
        # Døgn 90→60 er ~30 daglige forekomster. Ikke ~60: forekomstene
        # ETTER stansen ligger utenfor enhver aktiv periode og er ikke
        # nedetid — det er PERIODENE som holder stansen ute, ikke et
        # statusfilter.
        assert 20 <= detalj["vinduer"] <= 40, detalj
        assert detalj["avkortet"] is False, detalj
        # ... og fortsatt ÉN: dempingen gjelder også for en stanset plan.
        klassifiser_vinduer(rt)
        _sett_kontekst(migrator, TENANT)
        antall = migrator.execute(
            "SELECT count(*) FROM bestillingsplan_hendelse WHERE plan_id=%s"
            " AND hendelse='nedetid_aggregert'", (pid,)).fetchone()[0]
        migrator.rollback()
        assert antall == 1, "avbruddet ble meldt på nytt i neste sveip"
    finally:
        rt.close()


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


def test_planenheten_har_apiets_pinnede_semantikkmiljo():
    """Codex P1 (statisk): arbeideren kjører samme motor — og må derfor
    kjøre under samme VERIFISERTE miljø.

    `verifiser_oppstartsmiljo()` hopper over tzdata-sammenligningen når
    `DISPONIT_SEMANTIKK_MILJO` MANGLER. Enheten lastet den ikke, og
    `opp.sh` skrev den kun for API-et: en tzdata-drift på verten ville
    dermed gitt nøyaktig utfallet porten finnes for å hindre — API-et
    nekter å starte (riktig), mens den planlagte veien, den som beslutter
    UTEN et menneske til stede, fortsetter i stillhet på en semantikk
    ingen har verifisert.
    """
    enhet = (ROT / "deploy" / "staging" / "disponit-plan.service"
             ).read_text(encoding="utf-8")
    assert ("LoadCredential=DISPONIT_SEMANTIKK_MILJO:"
            "/etc/disponit/plan/DISPONIT_SEMANTIKK_MILJO") in enhet
    opp = (ROT / "deploy" / "staging" / "opp.sh").read_text(encoding="utf-8")
    assert re.search(r"skriv_cred\s+plan\s+DISPONIT_SEMANTIKK_MILJO", opp), \
        "opp.sh provisjonerer ikke signaturen for planenheten"
    # ... og fra SAMME måling: to kall til miljosignatur() kunne i
    # prinsippet gitt to verdier, og da ville de to enhetene startet på
    # hver sin semantikk uten at noen sjekk fanget det.
    assert opp.count("semantikk.miljosignatur()") == 1, \
        "signaturen måles flere ganger — enhetene kan divergere"


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


@pg
def test_stopp_uten_pause_gjenopprettes(migrator):
    """Codex P1: et terminalt `stopp` uten sin pause er en dom som aldri
    ble fullbyrdet.

    Commiten mellom ticket og pausen gjorde ticket varig FØRST: døde
    prosessen i mellomrommet, ble vinduet aldri plukket igjen (terminal er
    absorberende) og ingen sveip lette etter `stopp`-tick uten pause —
    planen sto aktiv etter en policy- eller moduldom og bestilte videre i
    hvert eneste vindu. De to er nå én transaksjon, men den samme
    tilstanden kan fortsatt oppstå via klassifisererens gjenoppretting,
    som skriver evidens uten å felle pausedommer. Denne sveipen tar den.

    Dempingen er per TICK (`idempotensnokkel`), ikke per plan: et
    gjenopptak skal ikke straks pause planen om igjen på den samme,
    fullbyrdede dommen.
    """
    from plan.materialiser import pausesveip
    rt = _rt()
    try:
        pid = _plan_forfalt(rt, migrator, host="p19b.example",
                            aktiver_dager=3)
        vs = _syntetisk_vindu(migrator, pid, start_h=-30, slutt_h=-26)
        _sett_kontekst(migrator, TENANT)
        nokkel = "t-" + secrets.token_hex(8)
        migrator.execute(
            "INSERT INTO bestillingsplan_tick (plan_id, tenant, vindu_start,"
            " idempotensnokkel, utfall, detalj)"
            " VALUES (%s,%s,%s,%s,'stopp',%s)",
            (pid, TENANT, vs, nokkel,
             json.dumps({"feil": "bestillingstype_utilgjengelig"})))
        migrator.commit()

        assert (str(pid), "modul_utilgjengelig") in pausesveip(rt), \
            "stopp-ticket uten pause ble ikke gjenopprettet"
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT status, pause_aarsak FROM bestillingsplan"
            " WHERE plan_id=%s", (pid,)).fetchone() == ("pauset",
                                                        "modul_utilgjengelig")
        migrator.rollback()

        # Idempotent: en ny sveip pauser ikke om igjen ...
        assert pausesveip(rt) == []
        # ... og heller ikke ETTER et gjenopptak. Dommen er fullbyrdet;
        # uten per-tick-demping ville planen blitt pauset i sekundet den
        # ble gjenopptatt, og gjenopptaket vært virkningsløst.
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT gjenoppta_plan(%s,%s,'test:x','r-x')",
                   (TENANT, pid))
        rt.commit()
        assert pausesveip(rt) == []
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT status FROM bestillingsplan WHERE plan_id=%s",
            (pid,)).fetchone()[0] == "aktiv"
        migrator.rollback()
    finally:
        rt.close()


@pg
def test_pause_nummer_to_varsles_ogsaa(migrator, klient):
    """Codex P2: hver pause har sin EGEN varselforekomst.

    Med `hendelse = 'pauset'` som konstant literal var varselnøkkelen
    (tenant, bruker, 'plan_pauset', 'plan', plan_id, 'pauset') den samme
    for hver pause på planen. Pause nummer to — etter et gjenopptak, og
    her med en helt annen grunn — traff `varsel_en_per_hendelse` og ble
    slukt av `WHEN OTHERS`: overgangen sto, men eieren fikk verken
    varselet eller `varslet`-sporet."""
    bid = _ekte_bruker("p-pause2-eier")
    rt = _rt()
    try:
        pid = _plan(rt, host="p-pause2.example", aktor=f"bruker:{bid}")
        _sett_kontekst(rt, TENANT)
        assert rt.execute(
            "SELECT pause_plan(%s,%s,'policy_stopper','test','rid1',NULL)",
            (TENANT, pid)).fetchone()[0]
        rt.commit()
        cookie, csrf = _adminsesjon(sub="p-pause2-admin")
        r = _post_plan(klient, cookie, csrf, f"/v1/plan/{pid}/gjenoppta", {})
        assert r.status_code == 200, r.text
        _sett_kontekst(rt, TENANT)
        assert rt.execute(
            "SELECT pause_plan(%s,%s,'modul_utilgjengelig','test','rid2',"
            "NULL)", (TENANT, pid)).fetchone()[0]
        rt.commit()
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    varsler = migrator.execute(
        "SELECT parametre->>'aarsak' FROM varsel WHERE tenant=%s AND"
        " art='plan_pauset' AND ressurs_id=%s ORDER BY opprettet",
        (TENANT, str(pid))).fetchall()
    varslet = migrator.execute(
        "SELECT count(*) FROM bestillingsplan_hendelse WHERE plan_id=%s"
        " AND hendelse='varslet'", (pid,)).fetchone()[0]
    migrator.rollback()
    assert varsler == [("policy_stopper",), ("modul_utilgjengelig",)], varsler
    assert varslet == 2, "pause nummer to etterlot intet varslet-spor"


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


def test_opptatt_nokkel_er_forbigaende_ikke_dom():
    """Codex P2: `idempotenskonflikt` bar TO betydninger.

    Den ene er terminal — det står en rad på nøkkelen med en ANNEN
    intensjon. Den andre er et sammenstøt i tid: sesjonslåsen på nøkkelen
    er opptatt fordi en forespørsel fortsatt arbeider med den. Bruker et
    forsøk lengre tid enn planleasen (120 s), tar en ny arbeider vinduet
    lovlig, møter låsen — og med begge betydningene i én kode ble det
    andre forsøket en terminal `stopp`: vinduet konsumert og planen
    pauset permanent, enda det FØRSTE forsøket kunne lykkes rett etter.

    Utad er de fortsatt den samme 409-en; skillet er planveiens, som er
    den eneste som gjør en feilkode om til varig tilstand.

    MUTASJONEN SOM DREPER DENNE: la låsegrenen returnere
    `idempotenskonflikt` igjen, eller fjern koden fra `_FORBIGAENDE`.
    """
    from api.bestilling import KLIENTKODE, OPPTATT
    from plan.materialiser import _FORBIGAENDE, _tick_utfall, er_forbigaende
    # De to endene er bundet: koden bestillingsveien sender er koden
    # planen kjenner igjen.
    assert OPPTATT in _FORBIGAENDE
    assert er_forbigaende(OPPTATT)
    assert _tick_utfall(("feil", OPPTATT))[0] is None
    # ... og klientens kontrakt på nøkkelen er uendret.
    assert KLIENTKODE[OPPTATT] == "idempotenskonflikt"
    assert not er_forbigaende("idempotenskonflikt")


@pg
def test_opptatt_nokkel_frigir_vinduet(migrator, app):
    """Codex P2, ende til ende: en opptatt nøkkel gir vinduet TILBAKE.

    Låsen holdes av en tredje forbindelse — nøyaktig formen et forsøk
    som fortsatt arbeider har — og `utfor_bestilling` er den EKTE
    funksjonen her, ikke en attrapp: det er dens egen `pg_try_advisory_lock`
    som avgjør. Vinduet skal stå `ledig` igjen, uten tick og uten pause.
    """
    from db.pg import koble
    from plan import materialiser
    from api import bestilling as bm
    rt = _rt()
    laas = koble(DSN)
    try:
        pid = _plan_forfalt(rt, migrator, host="p-opptatt.example")
        rad = _mine_forfalte(rt, pid)[0]
        navn = bm.laasenavn_for(TENANT, materialiser.idempotensnokkel(
            pid, rad[2]))
        assert laas.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
            (navn,)).fetchone()[0] is True
        res = materialiser.materialiser_en(app.tjeneste, rt, rad)
        assert res["forbigaende"] == bm.OPPTATT, res
        assert res["frigitt"] == "frigitt", res
    finally:
        laas.close()
        rt.close()
    _sett_kontekst(migrator, TENANT)
    vindu = migrator.execute(
        "SELECT tilstand FROM bestillingsplan_vindu WHERE plan_id=%s",
        (pid,)).fetchall()
    tick = migrator.execute(
        "SELECT count(*) FROM bestillingsplan_tick WHERE plan_id=%s",
        (pid,)).fetchone()[0]
    plan = migrator.execute(
        "SELECT status, pause_aarsak FROM bestillingsplan WHERE plan_id=%s",
        (pid,)).fetchone()
    migrator.rollback()
    assert vindu == [("ledig",)], vindu
    assert tick == 0, "en opptatt nøkkel skrev evidens"
    assert plan == ("aktiv", None), "en opptatt nøkkel pauset planen"


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
def test_avvist_terminalisering_pauser_ikke_planen(migrator, app,
                                                   monkeypatch):
    """Codex P2: en dom lagringen forkastet skal ikke bli plantilstand.

    `terminaliser_planvindu` svarer `avvik:<kanonisk utfall>` når vinduet
    ALT er terminalt med et annet utfall: den kanoniske raden står urørt,
    og funksjonen fører sin egen sikkerhetshendelse. Materialisereren
    pauset likevel planen på SITT lokale `stopp`. Et utgjerdet forsøk —
    en nyere arbeider terminaliserte `tillat`, mens dette gamle forsøket
    fikk et forbigående modulsvar — kunne dermed pause planen permanent,
    og bare et menneske kan oppheve den pausen.

    MUTASJONEN SOM DREPER DENNE: fjern `and not avvist` fra
    pausebetingelsen i `materialiser_en`.
    """
    from plan import materialiser
    rt = _rt()
    try:
        pid = _plan_forfalt(rt, migrator, host="p-avvik-pause.example")
        rad = _mine_forfalte(rt, pid)[0]
        vindu_start = rad[2]

        def nyere_arbeider(*a, **k):
            # Mens dette forsøket står i modulen, rekker en NYERE arbeider
            # å terminalisere vinduet med det kanoniske utfallet.
            m = _mig()
            _sett_kontekst(m, TENANT)
            m.execute("UPDATE bestillingsplan_vindu SET tilstand='terminal',"
                      " terminalisert_ts=now(), claim_id=NULL,"
                      " lease_utloper=NULL WHERE plan_id=%s"
                      " AND vindu_start=%s", (pid, vindu_start))
            m.execute("INSERT INTO bestillingsplan_tick (plan_id, tenant,"
                      " vindu_start, idempotensnokkel, utfall)"
                      " VALUES (%s,%s,%s,%s,'tillat')",
                      (pid, TENANT, vindu_start,
                       materialiser.idempotensnokkel(pid, vindu_start)))
            m.commit()
            m.close()
            # ... og DETTE forsøket ender i en moduldom.
            return ("feil", "bestillingstype_utilgjengelig")

        import api.bestilling
        monkeypatch.setattr(api.bestilling, "utfor_bestilling",
                            nyere_arbeider)
        res = materialiser.materialiser_en(app.tjeneste, rt, rad)
        assert res["dom"] == "avvik:tillat", res
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    plan = migrator.execute(
        "SELECT status, pause_aarsak FROM bestillingsplan WHERE plan_id=%s",
        (pid,)).fetchone()
    tick = migrator.execute(
        "SELECT utfall FROM bestillingsplan_tick WHERE plan_id=%s",
        (pid,)).fetchall()
    avvik = migrator.execute(
        "SELECT count(*) FROM bestillingsplan_hendelse WHERE plan_id=%s"
        " AND hendelse='sikkerhetsavvik'", (pid,)).fetchone()[0]
    migrator.rollback()
    assert plan == ("aktiv", None), \
        "et utgjerdet forsøk pauset planen på en forkastet dom"
    assert tick == [("tillat",)], "den kanoniske raden ble rørt"
    assert avvik == 1, "avviket ble ikke ført som sikkerhetshendelse"


@pg
def test_gjentatt_uten_resultat(migrator):
    """Port 22: tre `tillat`-tick på rad i gjeldende åpne periode uten
    promotert artefakt → pauset `gjentatt_uten_resultat`."""
    from plan.materialiser import pausesveip
    rt = _rt()
    try:
        pid = _plan_forfalt(rt, migrator, host="p22.example",
                            aktiver_dager=5)
        for i in range(3):
            vs = _syntetisk_vindu(migrator, pid, start_h=-30 - 24 * i,
                                  slutt_h=-26 - 24 * i)
            _syntetisk_tick(migrator, pid, vs, "tillat",
                            oppdrag_id=900000 + i)
        assert (str(pid), "gjentatt_uten_resultat") in pausesveip(rt)
    finally:
        rt.close()


@pg
def test_uten_resultat_revalideres_under_planlaasen(migrator):
    """Codex P2: kandidatlesningen og pausen var to øyeblikk.

    Sveipen leste kandidatene i én transaksjon og pauset i en annen.
    Endret verden seg imellom — det tredje oppdragets artefakt ble
    PROMOTERT av arbeiderveien, som er helt uavhengig av plansveipen —
    pauset den likevel planen som `gjentatt_uten_resultat` enda et
    resultat forelå idet overgangen committet. Bare et menneske kan
    oppheve den pausen. Kappløpet krever altså ikke to samtidige sveip.

    Rekkefølgen måles deterministisk: en tredje forbindelse holder
    planlåsen, sveipen settes i vente på den, og predikatet gjøres FALSKT
    og committes mens den står der. Her brytes stripen av et fjerde tick
    — samme predikat, samme lås, og en skriver like uavhengig av sveipen
    som artefaktpromoteringen er.

    MUTASJONEN SOM DREPER DENNE: la sveipen kalle `pause_plan` direkte
    igjen, uten revalideringen i `pause_gjentatt_uten_resultat`.
    """
    import threading
    import time
    from db.pg import koble
    rt = _rt()
    blokk = koble(MIGRATOR_DSN)
    svar = {}
    try:
        pid = _plan_forfalt(rt, migrator, host="p22c.example",
                            aktiver_dager=5)
        for i in range(3):
            vs = _syntetisk_vindu(migrator, pid, start_h=-30 - 24 * i,
                                  slutt_h=-26 - 24 * i)
            _syntetisk_tick(migrator, pid, vs, "tillat",
                            oppdrag_id=940000 + i)
        # Vindusraden for det fjerde ticket lages nå; PREDIKATET rører
        # ikke vindusrader, så kandidaten står uendret.
        vs_nytt = _syntetisk_vindu(migrator, pid, start_h=-6, slutt_h=-2)

        _sett_kontekst(blokk, TENANT)
        blokk.execute("SELECT status FROM bestillingsplan WHERE plan_id=%s"
                      " FOR UPDATE", (pid,))

        def sveip():
            c = _rt()
            try:
                _sett_kontekst(c, TENANT)
                svar["pauset"] = c.execute(
                    "SELECT pause_gjentatt_uten_resultat(%s,%s,'plansveip',"
                    "'r-sveip')", (TENANT, pid)).fetchone()[0]
                c.commit()
            finally:
                c.close()

        t = threading.Thread(target=sveip)
        t.start()
        time.sleep(1.5)
        assert "pauset" not in svar, "sveipen tok aldri planlåsen"
        # Verden endrer seg mens sveipen står i lås-køen.
        blokk.execute(
            "INSERT INTO bestillingsplan_tick (plan_id, tenant, vindu_start,"
            " idempotensnokkel, utfall) VALUES (%s,%s,%s,%s,'brudd')",
            (pid, TENANT, vs_nytt, "t-" + secrets.token_hex(8)))
        blokk.commit()
        t.join(timeout=20)
        assert not t.is_alive(), "sveipen kom aldri forbi låsen"
    finally:
        blokk.close()
        rt.close()
    assert svar.get("pauset") is False, svar
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT status FROM bestillingsplan WHERE plan_id=%s",
        (pid,)).fetchone()[0] == "aktiv", "planen ble pauset på et utdatert" \
        " øyeblikksbilde"
    migrator.rollback()


def _promoter_artefakt(conn, key_id, oppdrag_id):
    """Et promotert artefakt for oppdraget — UTEN commit.

    Nyttelasten er syntetisk (predikatet leser bare tilstanden), men
    lengdene følger `artefakt_payload_struktur`. Poenget er overgangen
    til `promotert`: den er det `artefakt_resultatlas` henger på.
    """
    _sett_kontekst(conn, TENANT)
    kh = conn.execute(
        "SELECT kontrakt_hash FROM artefakttype_register"
        " WHERE artefakttype='test.onboarding.kvittering'").fetchone()[0]
    conn.execute(
        "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype, modul_id,"
        " release_id, kontraktversjon, kontrakt_hash, module_epoch, tilstand,"
        " storrelse_bytes, klartekst_sha256, ciphertext, nonce, dek_ref,"
        " kapabilitet_jti, promotert_ts)"
        " VALUES (%s,%s,'test.onboarding.kvittering','m_test_onboarding',"
        " 'r1',1,%s,0,'promotert',2,%s,%s,%s,%s,%s,now())",
        (TENANT, oppdrag_id, kh, "0" * 64, b"c" * 32, b"n" * 12, key_id,
         "jti-" + secrets.token_hex(8)))


@pg
def test_promoteringen_og_predikatet_deler_laasen(migrator, app):
    """Codex P2 (runde 4): planlåsen alene serialiserer bare SVEIPENE.

    Revalideringen under planlåsen flyttet kappløpet, den fjernet det
    ikke: promoteringen tar ingen planlås — den låser artefaktraden og
    ingenting annet — så en promotering som committer ETTER predikatet,
    men FØR pausen, ga fortsatt en plan pauset som
    `gjentatt_uten_resultat` med et promotert tredje resultat. En pause
    bare et menneske kan oppheve.

    Låsen ligger nå på FAKTUMET: `artefakt_resultatlas` tar
    `oppdragsresultat:<oppdrag_id>` i selve overgangen til `promotert`,
    og `pause_gjentatt_uten_resultat` tar de samme nøklene før predikatet
    leses. Målt deterministisk: promoteringen står ÅPEN (triggeren har
    tatt nøkkelen, raden er usynlig for alle andre), og sveipen må vente
    på den — for så å se resultatet og la planen stå.

    MUTASJONEN SOM DREPER DENNE: fjern triggeren, eller
    `pg_advisory_xact_lock`-leddet i `pause_gjentatt_uten_resultat`. Da
    løper sveipen forbi den åpne promoteringen og pauser planen.
    """
    import threading
    import time
    from db import kryptering
    rt = _rt()
    blokk = _mig()
    svar = {}
    try:
        _sett_kontekst(migrator, TENANT)
        key_id, _dek = kryptering.hent_eller_opprett_aktiv_dek(
            migrator, TENANT)
        migrator.commit()
        pid = _plan_forfalt(rt, migrator, host="p22d.example",
                            aktiver_dager=5)
        oids = []
        for i in range(3):
            oid, _logg = _beslutningsoppdrag(rt, migrator)
            oids.append(oid)
            vs = _syntetisk_vindu(migrator, pid, start_h=-30 - 24 * i,
                                  slutt_h=-26 - 24 * i)
            _syntetisk_tick(migrator, pid, vs, "tillat", oppdrag_id=oid)
        # Uten promoteringen ER planen en kandidat — ellers måler testen
        # ingenting. Utvalget leses med RUNTIME-rollen: definerne er
        # REVOKEd fra PUBLIC og granted til `disponit` alene (port 7).
        _sett_kontekst(rt, TENANT)
        assert rt.execute(
            "SELECT count(*) FROM planer_gjentatt_uten_resultat()"
            " WHERE plan_id=%s", (pid,)).fetchone()[0] == 1
        rt.commit()

        _promoter_artefakt(blokk, key_id, oids[0])   # åpen: ingen commit

        def sveip():
            c = _rt()
            try:
                _sett_kontekst(c, TENANT)
                svar["pauset"] = c.execute(
                    "SELECT pause_gjentatt_uten_resultat(%s,%s,'plansveip',"
                    "'r-sveip')", (TENANT, pid)).fetchone()[0]
                c.commit()
            finally:
                c.close()

        t = threading.Thread(target=sveip)
        t.start()
        time.sleep(1.5)
        assert "pauset" not in svar, \
            "sveipen løp forbi en åpen promotering — låsen deles ikke"
        blokk.commit()
        t.join(timeout=20)
        assert not t.is_alive(), "sveipen kom aldri forbi låsen"
    finally:
        blokk.close()
        rt.close()
    assert svar.get("pauset") is False, svar
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT status FROM bestillingsplan WHERE plan_id=%s",
        (pid,)).fetchone()[0] == "aktiv", \
        "planen ble pauset enda det tredje resultatet ble promotert"
    migrator.rollback()
    # ... og HELE sveipen lar den stå. Den direkte veien over måler
    # låsen; dette måler veien planen faktisk går. `pausesveip` leser
    # utvalget i sin EGEN transaksjon, og et promotert resultat skal
    # holde planen ute av begge.
    from plan.materialiser import pausesveip
    sveipet = _rt()
    try:
        rest = pausesveip(sveipet)
        _sett_kontekst(sveipet, TENANT)
        kandidater = sveipet.execute(
            "SELECT count(*) FROM planer_gjentatt_uten_resultat()"
            " WHERE plan_id=%s", (pid,)).fetchone()[0]
    finally:
        sveipet.close()
    _sett_kontekst(migrator, TENANT)
    fakta = migrator.execute(
        "SELECT tenant, oppdrag_id, tilstand FROM artefakt"
        " WHERE oppdrag_id = ANY(%s)", (oids,)).fetchall()
    ticks = migrator.execute(
        "SELECT vindu_start, utfall, oppdrag_id FROM bestillingsplan_tick"
        " WHERE plan_id=%s ORDER BY vindu_start DESC", (pid,)).fetchall()
    migrator.rollback()
    assert kandidater == 0, \
        f"planen står igjen som kandidat: artefakt={fakta} tick={ticks}"
    assert not any(p == str(pid) for p, _ in rest), \
        f"sveipen pauset planen enda resultatet er promotert: {rest}" \
        f" artefakt={fakta} tick={ticks}"


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
        pid = _plan_forfalt(rt, migrator, host="p22b.example",
                            aktiver_dager=3)
        # Eldst → nyest: tillat, tillat, brudd, tillat.
        for i, utfall in enumerate(("tillat", "tillat", "brudd", "tillat")):
            vs = _syntetisk_vindu(migrator, pid, start_h=-30 + 4 * i,
                                  slutt_h=-28 + 4 * i)
            _syntetisk_tick(migrator, pid, vs, utfall,
                            oppdrag_id=920000 + i if utfall == "tillat"
                            else None)
        sveip = pausesveip(rt)
        if sveip:
            _sett_kontekst(migrator, TENANT)
            hvem = migrator.execute(
                "SELECT parametre->>'hostname', status, ("
                "  SELECT string_agg(t.utfall || ':' || coalesce("
                "         t.oppdrag_id::text,'-') || ':' || coalesce((SELECT"
                "         string_agg(a.tenant || '/' || a.tilstand, '+')"
                "           FROM artefakt a"
                "          WHERE a.oppdrag_id = t.oppdrag_id),'ingen'),"
                "         ',' ORDER BY t.vindu_start DESC)"
                "    FROM bestillingsplan_tick t"
                "   WHERE t.plan_id = b.plan_id)"
                " FROM bestillingsplan b WHERE b.plan_id::text = ANY(%s)",
                ([p for p, _ in sveip],)).fetchall()
            migrator.rollback()
        else:
            hvem = []
        assert sveip == [], \
            f"et brudd i stripen pauset likevel: {hvem} (denne: {pid})"
        # To `tillat` til gjør de TRE SISTE sammenhengende — da pauser den.
        for i, start_h in enumerate((-14, -10)):
            vs = _syntetisk_vindu(migrator, pid, start_h=start_h,
                                  slutt_h=start_h + 2)
            _syntetisk_tick(migrator, pid, vs, "tillat",
                            oppdrag_id=920090 + i)
        assert (str(pid), "gjentatt_uten_resultat") in pausesveip(rt)
    finally:
        rt.close()


def _velg_kanal(m, bid, kanal):
    _sett_kontekst(m, TENANT)
    m.execute("INSERT INTO varselvalg (tenant, bruker_id, kanal)"
              " VALUES (%s,%s,%s) ON CONFLICT (tenant, bruker_id)"
              " DO UPDATE SET kanal = EXCLUDED.kanal", (TENANT, bid, kanal))
    m.commit()


@pg
def test_planvarslene_respekterer_kun_portal(migrator):
    """Codex P1: planvarslene var de eneste produsentene som verken leste
    `varselvalg` eller tok kanalvalg-låsen.

    Raden ble født `koet` — altså E-POST — uansett hva mottakeren hadde
    valgt, så `kun_portal` var uten virkning her. Og uten låsen er
    lesningen og innsettingen to uavhengige øyeblikk: en samtidig
    `sett_kanal('kun_portal')` merker alt den ser i køen `ikke_aktuelt` og
    committer, hvorpå varselet setter inn en fersk `koet`-rad på det
    valget som nettopp ble forlatt — og ingen rydder den.

    MUTASJONEN SOM DREPER DENNE: fjern `pg_advisory_xact_lock` fra
    `pause_plan`/`varsle_plan_brudd`, eller nøkle den med noe annet enn
    615774026 + hashen av tenant + bruker.
    """
    from db.pg import koble
    from api.varsel import KANALVALGNOKKEL
    from plan.materialiser import pausesveip
    # Nøkkelen står som literal i migrasjonen (SQL kan ikke importere
    # Python); denne asserten binder de to veiene til samme nøkkel.
    assert KANALVALGNOKKEL == 615774026

    bid = _ekte_bruker("p-kanal-eier")
    _velg_kanal(migrator, bid, "kun_portal")
    rt = _rt()
    try:
        # 1. Pausevarselet: `kun_portal` → `ikke_aktuelt`, ikke `koet`.
        pid = _plan(rt, host="p-kanal.example", aktor=f"bruker:{bid}")
        _sett_kontekst(rt, TENANT)
        assert rt.execute(
            "SELECT pause_plan(%s,%s,'policy_stopper','test','r-k1',NULL)",
            (TENANT, pid)).fetchone()[0]
        rt.commit()
        _sett_kontekst(migrator, TENANT)
        status = migrator.execute(
            "SELECT epost_status FROM varsel WHERE tenant=%s AND"
            " art='plan_pauset' AND ressurs_id=%s", (TENANT, str(pid))
        ).fetchall()
        migrator.rollback()
        assert status == [("ikke_aktuelt",)], status

        # 2. Bruddvarselet, samme vei.
        pid2 = _plan_forfalt(rt, migrator, host="p-kanal2.example",
                             aktiver_dager=5, aktor=f"bruker:{bid}")
        for i in range(3):
            vs = _syntetisk_vindu(migrator, pid2, start_h=-30 - 24 * i,
                                  slutt_h=-26 - 24 * i)
            _syntetisk_tick(migrator, pid2, vs, "brudd")
        pausesveip(rt)
        _sett_kontekst(migrator, TENANT)
        status2 = migrator.execute(
            "SELECT epost_status FROM varsel WHERE tenant=%s AND"
            " art='plan_gjentatt_brudd' AND ressurs_id=%s",
            (TENANT, str(pid2))).fetchall()
        migrator.rollback()
        assert status2 == [("ikke_aktuelt",)], status2

        # 3. LÅSEN: holder avmeldingsveien den, kommer varselet ikke forbi
        #    — og port 41 gjør resten: pausen står, varselet uteblir.
        holder = koble(MIGRATOR_DSN)
        try:
            holder.execute(
                "SELECT pg_advisory_xact_lock(615774026, hashtext(%s))",
                (f"{TENANT}\x1f{bid}",))
            rt.execute("SET lock_timeout = '750ms'")
            rt.commit()
            pid3 = _plan(rt, host="p-kanal3.example", aktor=f"bruker:{bid}")
            _sett_kontekst(rt, TENANT)
            assert rt.execute(
                "SELECT pause_plan(%s,%s,'policy_stopper','test','r-k3',"
                "NULL)", (TENANT, pid3)).fetchone()[0]
            rt.commit()
        finally:
            holder.rollback()
            holder.close()
            rt.execute("SET lock_timeout = 0")
            rt.commit()
        _sett_kontekst(migrator, TENANT)
        laast = migrator.execute(
            "SELECT status, (SELECT count(*) FROM varsel v WHERE"
            " v.tenant=%s AND v.art='plan_pauset' AND v.ressurs_id=%s)"
            "  FROM bestillingsplan WHERE plan_id=%s",
            (TENANT, str(pid3), pid3)).fetchone()
        migrator.rollback()
        assert laast == ("pauset", 0), laast
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
        pid = _plan_forfalt(rt, migrator, host="p21.example",
                            aktiver_dager=5, aktor=f"bruker:{bid}")
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


def _bruddvarsler(m, pid):
    _sett_kontekst(m, TENANT)
    n = m.execute("SELECT count(*) FROM varsel WHERE tenant=%s AND"
                  " art='plan_gjentatt_brudd' AND ressurs_id=%s",
                  (TENANT, str(pid))).fetchone()[0]
    m.rollback()
    return n


@pg
def test_dempingen_holder_hele_bruddstripen(migrator):
    """Codex P2: dempingen skal gjelde HELE det ubrutte løpet.

    Grensen var `min(registrert)` av de tre SISTE tickene — altså et
    vindu som VANDRER. Etter at tick 1–3 ga det første varselet, ble tick
    4–6 alle registrert ETTER hendelsen, grensen skjøv seg forbi den, og
    det sjette bruddet varslet på nytt. Det gjentok seg for hvert tredje
    brudd i et løp som aldri ble brutt av et annet utfall — stikk i strid
    med løftet om ETT varsel per stripe, og verst nettopp for planen som
    har det verst.

    Fire brudd fanget det ikke: de tre siste var da fortsatt DELVIS de
    som lå bak varselet. Seks er det minste som viser det."""
    from plan.materialiser import pausesveip
    bid = _ekte_bruker("p21c-eier")
    rt = _rt()
    try:
        pid = _plan_forfalt(rt, migrator, host="p21c.example",
                            aktiver_dager=12, aktor=f"bruker:{bid}")
        # Tick 1–3: stripen begynner, ett varsel går ut.
        for i in range(3):
            vs = _syntetisk_vindu(migrator, pid, start_h=-200 + 4 * i,
                                  slutt_h=-198 + 4 * i)
            _syntetisk_tick(migrator, pid, vs, "brudd")
        pausesveip(rt)
        assert _bruddvarsler(migrator, pid) == 1, \
            "tre brudd på rad ga ikke nøyaktig ett varsel"
        # Tick 4–6: registrert ETTER varselet — men stripen er den samme.
        for i in range(3):
            vs = _syntetisk_vindu(migrator, pid, start_h=-188 + 4 * i,
                                  slutt_h=-186 + 4 * i)
            _syntetisk_tick(migrator, pid, vs, "brudd")
        pausesveip(rt)
        assert _bruddvarsler(migrator, pid) == 1, \
            "dempingen vandret med de tre siste tickene"
        # ... og et annet utfall BRYTER løpet: neste tre varsler igjen.
        vs = _syntetisk_vindu(migrator, pid, start_h=-176, slutt_h=-174)
        _syntetisk_tick(migrator, pid, vs, "tillat")
        for i in range(3):
            vs = _syntetisk_vindu(migrator, pid, start_h=-172 + 4 * i,
                                  slutt_h=-170 + 4 * i)
            _syntetisk_tick(migrator, pid, vs, "brudd")
        pausesveip(rt)
        assert _bruddvarsler(migrator, pid) == 2, \
            "en brutt stripe armerte ikke dempingen på nytt"
        _sett_kontekst(migrator, TENANT)
        status = migrator.execute(
            "SELECT status FROM bestillingsplan WHERE plan_id=%s",
            (pid,)).fetchone()[0]
        migrator.rollback()
        assert status == "aktiv", "brudd pauset planen"
    finally:
        rt.close()


@pg
def test_dempingen_leses_under_planlaasen(migrator):
    """Codex P2: to samtidige sveip på samme bruddstripe gir ETT varsel.

    Sto kandidatsjekken før planlåsen, kunne begge lese «ingen demping
    ennå» før noen av dem hadde låst. Den andre ventet så pent på den
    første — og fortsatte deretter uten å se etter om verden hadde endret
    seg: en andre `varslet`-hendelse og et andre varsel. Varselnøkkelen
    kan ikke fange det, for forekomsten ER hendelses-id-en, og de to
    hendelsene har hver sin.

    Kappløpet kjøres deterministisk: en tredje forbindelse holder
    planlåsen, taperen settes i vente på den, og VINNERENS demping skrives
    og committes mens taperen står der. Slipper låsen først etterpå, må
    taperen lese predikatet på nytt for å oppdage den.

    MUTASJONEN SOM DREPER DENNE: flytt `SELECT ... FOR UPDATE` tilbake
    under kandidatsjekken i `varsle_plan_brudd`.
    """
    import threading
    import time
    from db.pg import koble
    bid = _ekte_bruker("p-demping-eier")
    rt = _rt()
    blokk = koble(MIGRATOR_DSN)
    svar = {}
    try:
        pid = _plan_forfalt(rt, migrator, host="p-demping.example",
                            aktiver_dager=5, aktor=f"bruker:{bid}")
        for i in range(3):
            vs = _syntetisk_vindu(migrator, pid, start_h=-30 - 24 * i,
                                  slutt_h=-26 - 24 * i)
            _syntetisk_tick(migrator, pid, vs, "brudd")

        # Vinnerens transaksjon: tar planlåsen og holder den.
        _sett_kontekst(blokk, TENANT)
        blokk.execute("SELECT aktivert_av FROM bestillingsplan"
                      " WHERE plan_id=%s FOR UPDATE", (pid,))

        def taper():
            c = _rt()
            try:
                _sett_kontekst(c, TENANT)
                svar["taper"] = c.execute(
                    "SELECT varsle_plan_brudd(%s,%s,'plansveip','r-taper')",
                    (TENANT, pid)).fetchone()[0]
                c.commit()
            finally:
                c.close()

        t = threading.Thread(target=taper)
        t.start()
        time.sleep(1.5)          # taperen står i lås-køen
        assert "taper" not in svar, "taperen tok aldri planlåsen"
        # Vinneren skriver dempingen og committer — nøyaktig raden
        # `varsle_plan_brudd` selv legger igjen.
        blokk.execute(
            "INSERT INTO bestillingsplan_hendelse (plan_id, tenant,"
            " hendelse, aktor, request_id, detalj) VALUES"
            " (%s,%s,'varslet','plansveip','r-vinner',"
            " jsonb_build_object('grunn','gjentatt_brudd','bruker',%s::text))",
            (pid, TENANT, bid))
        blokk.commit()
        t.join(timeout=20)
        assert not t.is_alive(), "taperen kom aldri forbi låsen"
    finally:
        blokk.close()
        rt.close()
    assert svar.get("taper") is False, svar
    _sett_kontekst(migrator, TENANT)
    varslet = migrator.execute(
        "SELECT count(*) FROM bestillingsplan_hendelse WHERE plan_id=%s"
        " AND hendelse='varslet'", (pid,)).fetchone()[0]
    varsler = migrator.execute(
        "SELECT count(*) FROM varsel WHERE tenant=%s AND"
        " art='plan_gjentatt_brudd' AND ressurs_id=%s",
        (TENANT, str(pid))).fetchone()[0]
    migrator.rollback()
    assert (varslet, varsler) == (1, 0), (varslet, varsler)


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
        pid = _plan_forfalt(rt, migrator, host="p21b.example",
                            aktiver_dager=5, aktor=f"bruker:{bid}")
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
        pid = _plan_forfalt(rt, migrator, host="p23.example",
                            aktiver_dager=5)
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
def test_sen_gjenoppretting_hoerer_til_forfallets_periode(migrator):
    """Codex P2: tickets periode er FORFALLETS, ikke `registrert`s.

    `registrert` er tidspunktet TERMINALISERINGEN skjedde, og
    klassifisererens gjenoppretting skriver ticket lenge etter at
    arbeideren bestilte. Rekkefølgen «tre `tillat` bestilt i gammel
    periode → arbeideren dør før terminaliseringen → planen pauses og
    gjenopptas → klassifisereren gjenoppretter de tre» ga tre ferske
    `registrert` INNENFOR den nye perioden, og sveipen pauset den nettopp
    gjenopptatte planen som `gjentatt_uten_resultat` — en pause bare et
    menneske kan oppheve. Løftet om at gjenopptaket nullstiller tellerne
    var altså brutt av sen evidens alene.

    Måles som en DIFFERANSE i ett og samme sveip: to identiske planer med
    identiske vinduer og tick, den ene gjenopptatt etter at vinduene
    forfalt, den andre ikke. Bare gjenopptaket skiller dem."""
    from plan.materialiser import pausesveip
    rt = _rt()
    try:
        sen = _plan_forfalt(rt, migrator, host="p44-sen.example",
                            aktiver_dager=5)
        kontroll = _plan_forfalt(rt, migrator, host="p44-kontroll.example",
                                 aktiver_dager=5)
        # Vinduene forfalt i den FØRSTE perioden, men står uten tick:
        # arbeideren rakk å bestille, ikke å terminalisere.
        vinduer = {p: [_syntetisk_vindu(migrator, p, start_h=-30 - 24 * i,
                                        slutt_h=-26 - 24 * i)
                       for i in range(3)]
                   for p in (sen, kontroll)}
        # Pause og gjenopptak FØR gjenopprettingen rekker fram.
        _sett_kontekst(rt, TENANT)
        assert rt.execute(
            "SELECT pause_plan(%s,%s,'policy_stopper','test','r-sen',NULL)",
            (TENANT, sen)).fetchone()[0]
        rt.execute("SELECT gjenoppta_plan(%s,%s,'test:x','r-sen2')",
                   (TENANT, sen))
        rt.commit()
        # ... og FØRST NÅ skrives evidensen, med fersk `registrert`.
        for p, vs_er in vinduer.items():
            for i, vs in enumerate(vs_er):
                _syntetisk_tick(migrator, p, vs, "tillat",
                                oppdrag_id=970000 + i
                                + (0 if p == sen else 100))
        sveip = pausesveip(rt)
    finally:
        rt.close()
    assert (str(kontroll), "gjentatt_uten_resultat") in sveip, \
        f"kontrollplanen ble ikke pauset i det hele tatt: {sveip}"
    assert (str(sen), "gjentatt_uten_resultat") not in sveip, \
        "sen gjenoppretting flyttet gamle tick inn i den nye perioden"


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
    # Spredningen SENDES (Codex P2): forfallet er time_lokal pluss dette
    # minuttet, avledet av en sha256 av plan-id-en — flaten kan ikke regne
    # det ut selv, og viste derfor «neste kjøring» på hel time. Verdien er
    # basens egen avledning, ikke en andrehånds implementasjon.
    # Leses med RUNTIME-rollen: `plan_forfallsminutt` er REVOKEd fra
    # PUBLIC og granted til `disponit` alene — migratorrollen har den ikke.
    rt = _rt()
    try:
        fasit = rt.execute("SELECT plan_forfallsminutt(%s)",
                           (pid,)).fetchone()[0]
        rt.rollback()
    finally:
        rt.close()
    assert mine[0]["forfallsminutt"] == fasit, mine[0]
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
def test_planvarselet_naar_mottakeren_sin_innboks(migrator, klient):
    """Codex P2: varselet ble skrevet, men ingen kunne se det.

    Pause- og bruddvarslene går til administratoren som aktiverte planen,
    og `admin` har verken `policy:write` eller `policy:activate`. Skallet
    ga henne derfor aldri varselruten (og pollet aldri `/v1/varsel`), og
    de to POST-ene sto bak `policy:write` — enda begge kun rører HENNES
    EGNE rader, med bruker-id fra økten. Hun kunne altså motta et varsel
    hun verken kunne se, kvittere ut eller endre kanalvalget for. Hadde
    hun valgt `kun_portal`, satt hun igjen uten både e-post og portalspor.

    MUTASJONEN SOM DREPER DENNE: sett `policy:write` tilbake på
    `/v1/varsel/{id}/lest` eller `/v1/varselvalg`.
    """
    from api import sesjon as sesjonmodul
    bid = _ekte_bruker("varselvei")
    cookie, csrf = _adminsesjon(sub="varselvei")
    rt = _rt()
    try:
        pid = _plan(rt, host="varselvei.example", aktor=f"bruker:{bid}")
        _sett_kontekst(rt, TENANT)
        assert rt.execute(
            "SELECT pause_plan(%s,%s,'policy_stopper','test','r-vv',NULL)",
            (TENANT, pid)).fetchone()[0]
        rt.commit()
    finally:
        rt.close()
    ck = {sesjonmodul.C_SESJON: cookie}
    r = klient.get("/v1/varsel", cookies=ck)
    assert r.status_code == 200, r.text
    mine = [v for v in r.json()["varsler"] if v["ressurs_id"] == str(pid)]
    assert len(mine) == 1, r.json()
    assert mine[0]["art"] == "plan_pauset", mine
    # ... og hun kan kvittere det ut og styre kanalen sin.
    hode = {"X-Disponit-CSRF": csrf,
            "Idempotency-Key": "idem-" + secrets.token_hex(8)}
    r2 = klient.post(f"/v1/varsel/{mine[0]['id']}/lest", headers=hode,
                     cookies=ck)
    assert r2.status_code == 200, r2.text
    r3 = klient.post("/v1/varselvalg", json={"kanal": "kun_portal"},
                     headers={"X-Disponit-CSRF": csrf,
                              "Idempotency-Key": "idem-"
                              + secrets.token_hex(8)},
                     cookies=ck)
    assert r3.status_code == 200, r3.text


@pg
def test_historikken_viser_manuell_avvisning(migrator, klient):
    """Codex P2: en senere kansellering skal SES, uten at evidensen røres.

    Ticket er append-only og forblir `tillat` — det ER hva motoren svarte
    da vinduet ble terminalisert. Men et oppdrag som siden ble avvist av
    et menneske sto fortsatt som «Bestilt» i flaten, og
    `avvist_av_menneske` hadde ingen skriver i det hele tatt. Nå avledes
    diskriminatoren i lesingen: `utfall` er sporet, `vist_utfall` er nå."""
    cookie, csrf = _adminsesjon(sub="hist-avvis")
    rt = _rt()
    try:
        pid = _plan(rt, host="hist-avvis.example")
        oid, _ = _beslutningsoppdrag(rt, migrator)
        vs = _syntetisk_vindu(migrator, pid, start_h=-30, slutt_h=-26)
        _syntetisk_tick(migrator, pid, vs, "tillat", oppdrag_id=oid)
    finally:
        rt.close()
    from api import sesjon as sesjonmodul
    r = klient.get(f"/v1/plan/{pid}/historikk",
                   cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 200, r.text
    t0 = r.json()["tick"][0]
    assert (t0["utfall"], t0["vist_utfall"]) == ("tillat", "tillat"), t0
    # ... og så sier et menneske nei.
    m = _mig()
    _sett_kontekst(m, TENANT)
    m.execute("SET ROLE disponit_m37_claimer")
    m.execute("UPDATE oppdrag SET status='kansellert',"
              " kansellert_aarsak='menneskelig_avvis'"
              " WHERE tenant=%s AND id=%s", (TENANT, oid))
    m.commit()
    m.close()
    r = klient.get(f"/v1/plan/{pid}/historikk",
                   cookies={sesjonmodul.C_SESJON: cookie})
    t1 = r.json()["tick"][0]
    assert t1["vist_utfall"] == "avvist_av_menneske", t1
    # Evidensen er URØRT: revisjonssporet sier fortsatt hva motoren svarte.
    assert t1["utfall"] == "tillat", t1
    _sett_kontekst(migrator, TENANT)
    lagret = migrator.execute(
        "SELECT utfall FROM bestillingsplan_tick WHERE plan_id=%s",
        (pid,)).fetchone()[0]
    migrator.rollback()
    assert lagret == "tillat", "historikken skrev om revisjonssporet"


@pg
def test_ugyldig_plan_id_er_404_ikke_driftsalarm(migrator, klient, capsys):
    """Codex P2: en ugyldig plan-ID er klientinput, ikke en driftshendelse.

    Rutene sto som `{id:str}`, mens funksjonene tar UUID:
    `/v1/plan/not-a-uuid/aktiver` reiste `InvalidTextRepresentation`, ble
    fanget som en generisk databasefeil og svarte 503 `db_utilgjengelig`
    — med drifthendelse. `{id:uuid}` gjør stien til 404 fra ROUTEREN, før
    noen kodevei og uten falsk alarm."""
    cookie, csrf = _adminsesjon(sub="uuid-rute")
    capsys.readouterr()
    for sti in ("/v1/plan/not-a-uuid/aktiver", "/v1/plan/12345/gjenoppta",
                "/v1/plan/x/stans"):
        r = _post_plan(klient, cookie, csrf, sti, {})
        assert r.status_code == 404, (sti, r.status_code, r.text)
        assert "db_utilgjengelig" not in r.text, sti
    from api import sesjon as sesjonmodul
    r = klient.get("/v1/plan/not-a-uuid/historikk",
                   cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 404, r.text
    assert "db_utilgjengelig" not in capsys.readouterr().out, \
        "ordinær klientinput utløste en driftshendelse"


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
