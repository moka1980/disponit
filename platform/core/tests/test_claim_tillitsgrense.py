"""048 (#108) — claim-tillitsgrensen: Codex-portene 1–18 fra klarsignalet.

Grensen: retten til å claime planvinduer flyttes fra runtimerollen til
`disponit_plan_arbeider` (varselsender-modellen), holderen av claimet
systemattesteres (`claimet_av = session_user`), og R47-1 gjør
kvorumsvilkåret til lagring: en styrt aktiveringshendelse uten målt
kvorum kan ikke fødes.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
import re
import secrets
from pathlib import Path

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, TENANT, dekker, migrator, \
    miljo  # noqa: F401
from .test_m37 import _sett_kontekst
from .test_periodisk_kontroll import (PLAN_DSN, _kjor_som_runtime, _mig,
                                      _pa, _plan, _plan_forfalt, _rt,
                                      _syntetisk_vindu)
from .test_pr013_policyadmin import (TEN, _attest, _c, _runde,
                                     _validert_utkast)
from .test_policyaktivering import _full_aktivering

pg = pytest.mark.skipif(
    not (DSN and PLAN_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_PLAN_DSN ikke satt")

ROT = Path(__file__).resolve().parents[3]
M048 = (ROT / "platform" / "core" / "db" / "migrations"
        / "048_claim_tillitsgrense.sql")
PLANROLLE = "disponit_plan_arbeider"

CLAIM_KALL = (
    ("SELECT claim_planvindu(%s, gen_random_uuid(), now(), 120)",),
    ("SELECT terminaliser_planvindu(%s, gen_random_uuid(), now(),"
     " NULL, 'nokkel-x', 'tillat', NULL, NULL)",),
    ("SELECT frigi_planvindu(%s, gen_random_uuid(), now(), NULL)",),
)


# ---------------------------------------------------------------------------
# Rolle og grense (1–7)
# ---------------------------------------------------------------------------

@pg
def test_runtime_har_mistet_claim_retten():
    """Port 1: GRANT-nekten kommer FØR kroppen — runtime når aldri inn i
    claim/terminaliser/frigi, uansett argumenter."""
    rt = _rt()
    try:
        for (sql,) in CLAIM_KALL:
            _sett_kontekst(rt, TENANT)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rt.execute(sql, (TENANT,))
            rt.rollback()
    finally:
        rt.close()


@pg
def test_plan_arbeideren_gaar_hele_veien(migrator):
    """Port 2 + 8 (live) + 10: claim → terminaliser → tick som
    plan-arbeideren; holderen er systemattestert underveis og står igjen
    som evidens på den terminale raden — der er den frosset."""
    from plan.klassifiser import _nokkel
    pa = _pa()
    try:
        pid = _plan_forfalt(pa, migrator, host="c108-e2e.example")
        vs = _syntetisk_vindu(migrator, pid, start_h=-1, slutt_h=1,
                              tilstand="ledig")
        _sett_kontekst(pa, TENANT)
        utfall, claim = pa.execute(
            "SELECT utfall, claim_id FROM claim_planvindu(%s,%s,%s,120)",
            (TENANT, pid, vs)).fetchone()
        assert (utfall, claim is not None) == ("claimet", True)
        pa.commit()
        # Port 8, live halvdel: holderen er ROLLEN SOM AUTENTISERTE,
        # skrevet av lagringen — ikke noe kalleren oppga (ingen parameter
        # finnes å oppgi den i).
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT claimet_av FROM bestillingsplan_vindu"
            " WHERE plan_id=%s AND vindu_start=%s",
            (pid, vs)).fetchone()[0] == PLANROLLE
        migrator.rollback()
        _sett_kontekst(pa, TENANT)
        assert pa.execute(
            "SELECT terminaliser_planvindu(%s,%s,%s,%s,%s,'tillat',"
            "NULL,NULL)", (TENANT, pid, vs, claim,
                           _nokkel(pid, vs))).fetchone()[0] \
            == "terminalisert"
        pa.commit()
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT tilstand, claimet_av FROM bestillingsplan_vindu"
            " WHERE plan_id=%s AND vindu_start=%s",
            (pid, vs)).fetchone() == ("terminal", PLANROLLE)
        assert migrator.execute(
            "SELECT count(*) FROM bestillingsplan_tick WHERE plan_id=%s"
            " AND vindu_start=%s", (pid, vs)).fetchone()[0] == 1
        migrator.rollback()
        # Port 10: terminal er endelig OGSÅ for holderkolonnen
        # (avvis_endring melder seg som check_violation, 035-kontrakten).
        _sett_kontekst(migrator, TENANT)
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(
                "UPDATE bestillingsplan_vindu SET claimet_av='noen_andre'"
                " WHERE plan_id=%s AND vindu_start=%s", (pid, vs))
        migrator.rollback()
    finally:
        pa.close()


def _execute_sett(conn, rolle):
    """Funksjonene (med signatur) `rolle` har EXECUTE på, blant dem
    claimeren eier. pg_catalog er fasiten — ikke en håndskrevet liste."""
    return {r[0] for r in conn.execute(
        "SELECT p.proname || '(' ||"
        " pg_get_function_identity_arguments(p.oid) || ')'"
        "  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname = 'public'"
        "   AND pg_get_userbyid(p.proowner) = 'disponit_m37_claimer'"
        "   AND has_function_privilege(%s, p.oid, 'EXECUTE')"
        # PUBLIC-kjørbare hjelpere (default-ACL på trigger-/vaktfunksjoner)
        # er ikke rollens egne grants og måles ikke av porten.
        "   AND NOT EXISTS (SELECT 1 FROM aclexplode("
        "         coalesce(p.proacl, acldefault('f', p.proowner))) a"
        "        WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE')",
        (rolle,)).fetchall()}


@pg
def test_planrollen_naar_ingenting_utenfor_settet(migrator):
    """Port 3: ikke saker, ikke varsel, ikke policy, ikke flate-CRUD —
    og ikke andre tenanters rader."""
    forbudt = ("claim_neste_sak", "claim_neste_oppdrag", "forny_claim",
               "reserver_kapabilitet", "bruk_kapabilitet",
               "aktiver_policy", "opprett_plan", "aktiver_plan",
               "stans_plan", "gjenoppta_plan", "hent_planer",
               "plan_forfallsminutt", "plan_bestillingstyper",
               "slett_ubrukt_policy", "registrer_verifikasjonsbevis")
    for navn in forbudt:
        rader = migrator.execute(
            "SELECT has_function_privilege(%s, p.oid, 'EXECUTE')"
            "  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace"
            " WHERE n.nspname='public' AND p.proname = %s",
            (PLANROLLE, navn)).fetchall()
        assert rader, f"{navn} finnes ikke lenger — oppdater porten"
        assert not any(r[0] for r in rader), \
            f"plan-arbeideren har EXECUTE på {navn}"
    migrator.rollback()
    # Skrive policy: bordnekt, ikke bare funksjonsnekt.
    pa = _pa()
    try:
        _sett_kontekst(pa, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pa.execute("INSERT INTO policyer (tenant,policy_id,versjon,"
                       "innholds_hash,status,innhold) VALUES"
                       " (%s,'p-x','1','h','produksjon','{}'::jsonb)",
                       (TENANT,))
        pa.rollback()
        # Kryss-tenant: leseflaten rollen HAR (policyer, SELECT) viser
        # bare egen kontekst — med positiv kontroll så porten ikke er
        # grønn av en tom base.
        ppid = "p-c108-" + secrets.token_hex(3)
        m = _mig()
        try:
            _sett_kontekst(m, TENANT)
            m.execute(
                "INSERT INTO policyer (tenant,policy_id,versjon,"
                "innholds_hash,status,innhold) VALUES (%s,%s,'1.0.0','h',"
                "'produksjon','{}'::jsonb)", (TENANT, ppid))
            m.commit()
        finally:
            m.close()
        _sett_kontekst(pa, TENANT)
        assert pa.execute("SELECT count(*) FROM policyer WHERE"
                          " policy_id=%s", (ppid,)).fetchone()[0] == 1
        pa.rollback()
        _sett_kontekst(pa, TENANT + "-fremmed")
        assert pa.execute("SELECT count(*) FROM policyer WHERE"
                          " policy_id=%s", (ppid,)).fetchone()[0] == 0
        pa.rollback()
    finally:
        pa.close()


@pg
def test_execute_settet_er_kallsettet(migrator):
    """Port 4: rollens EXECUTE-sett == det STATISK utledede kallsettet
    fra plan-modulene pluss bestillingsveien de kjører in-prosess
    (044-avviket, ratifisert). Drift i NOEN retning feiler porten —
    listen i migrer.py kan ikke vokse eller ruste i det stille."""
    kilder = [ROT / "platform" / "core" / "plan" / "klassifiser.py",
              ROT / "platform" / "core" / "plan" / "materialiser.py",
              ROT / "platform" / "core" / "api" / "bestilling.py"]
    kalt = set()
    for fil in kilder:
        kalt |= set(re.findall(r"(?:SELECT|FROM|CALL)\s+([a-z_0-9]+)\(",
                               fil.read_text(encoding="utf-8")))
    eide = {r[0] for r in migrator.execute(
        "SELECT p.proname FROM pg_proc p"
        "  JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname='public'"
        "   AND pg_get_userbyid(p.proowner)='disponit_m37_claimer'"
    ).fetchall()}
    utledet = kalt & eide
    faktisk = {navn.split("(")[0] for navn in _execute_sett(
        migrator, PLANROLLE)}
    migrator.rollback()
    assert faktisk == utledet, (
        f"drift: grantet-men-ikke-kalt {sorted(faktisk - utledet)},"
        f" kalt-men-ikke-grantet {sorted(utledet - faktisk)}")


@pg
def test_plan_uniten_faar_planrollens_dsn():
    """Port 5: opp.sh skriver plan-credens fra DISPONIT_PLAN_URL — aldri
    fra runtimens DATABASE_URL — og nekter å deploye uten den."""
    opp = (ROT / "deploy" / "staging" / "opp.sh").read_text(
        encoding="utf-8")
    linjer = [l for l in opp.splitlines()
              if re.search(r"^\s*skriv_cred plan DISPONIT_DATABASE_URL\b",
                           l)]
    assert len(linjer) == 1, "plan-DSN-creden mangler (eller er doblet)"
    assert '"$DISPONIT_PLAN_URL"' in linjer[0], linjer[0]
    assert "DATABASE_URL" not in linjer[0].replace(
        "DISPONIT_DATABASE_URL", ""), linjer[0]
    # Preflight-vakten: mangler DSN-en, avbrytes deployen FØR noe stoppes.
    assert re.search(r"DISPONIT_PLAN_URL.*AVBRUTT|AVBRUTT.*DISPONIT_PLAN_URL",
                     opp, re.S), "preflight-vakt for DISPONIT_PLAN_URL mangler"


@pg
def test_varselveien_er_uendret(migrator):
    """Port 6: regresjon — varselsenderens EXECUTE-sett fra 046 står, og
    verken runtime eller plan-arbeideren har fått varsel-funksjonene."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "migrer_c108", ROT / "deploy" / "staging" / "migrer.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fns = re.findall(r"GRANT EXECUTE ON FUNCTION ([a-z_0-9]+)\(",
                     mod.VARSLER_RETTIGHETER)
    assert fns, "VARSLER_RETTIGHETER uten EXECUTE-grants — porten er blind"
    for navn in ("varsel_klaim_epost",):
        assert navn in fns, f"{navn} borte fra varselsettet"
    for navn in set(fns):
        rader = migrator.execute(
            "SELECT pg_get_function_identity_arguments(p.oid),"
            "       has_function_privilege('disponit_varselsender',"
            "                              p.oid, 'EXECUTE'),"
            "       has_function_privilege('disponit', p.oid, 'EXECUTE'),"
            "       has_function_privilege(%s, p.oid, 'EXECUTE')"
            "  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace"
            " WHERE n.nspname='public' AND p.proname=%s",
            (PLANROLLE, navn)).fetchall()
        assert rader, f"{navn} finnes ikke i basen"
        for args, sender, runtime, plan in rader:
            assert sender, f"varselsenderen mistet {navn}({args})"
            if navn.startswith("varsel_klaim"):
                assert not runtime, f"runtime har {navn}({args})"
            assert not plan, f"plan-arbeideren har {navn}({args})"
    migrator.rollback()


@pg
def test_flyttet_execute_er_idempotent(migrator):
    """Port 7: EXECUTE-flyttet tåler å kjøres om igjen mot en base der
    rollen alt finnes og flyttet alt er gjort — matrisen er uendret."""
    for som_eier in (
            "REVOKE EXECUTE ON FUNCTION claim_planvindu(TEXT, UUID,"
            " TIMESTAMPTZ, INT) FROM disponit",
            "GRANT EXECUTE ON FUNCTION claim_planvindu(TEXT, UUID,"
            " TIMESTAMPTZ, INT) TO disponit_plan_arbeider"):
        migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
        migrator.execute(som_eier)
        migrator.execute("RESET ROLE")
    matrise = migrator.execute(
        "SELECT has_function_privilege('disponit', p.oid, 'EXECUTE'),"
        "       has_function_privilege(%s, p.oid, 'EXECUTE')"
        "  FROM pg_proc p WHERE p.proname='claim_planvindu'",
        (PLANROLLE,)).fetchall()
    migrator.commit()
    assert matrise == [(False, True)]


# ---------------------------------------------------------------------------
# Identitet (8–11)
# ---------------------------------------------------------------------------

@pg
def test_holderen_kan_ikke_oppgis_av_kalleren(migrator):
    """Port 8, statisk halvdel: `claim_planvindu` skriver `session_user`
    — og har INGEN parameter en kaller kunne smuglet identiteten inn i.
    `current_user` ville vært funksjonseieren (definer), aldri brukt."""
    args, kropp = migrator.execute(
        "SELECT pg_get_function_identity_arguments(p.oid),"
        "       pg_get_functiondef(p.oid)"
        "  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace"
        " WHERE n.nspname='public' AND p.proname='claim_planvindu'"
    ).fetchone()
    migrator.rollback()
    assert args == ("p_tenant text, p_plan uuid,"
                    " p_vindu timestamp with time zone,"
                    " p_lease_s integer"), args
    assert re.search(r"claimet_av\s*=\s*session_user", kropp), \
        "claimet_av settes ikke av session_user"
    assert not re.search(r"claimet_av\s*=\s*current_user", kropp), \
        "current_user i en definer er funksjonseieren — feil identitet"


@pg
def test_tilstandskomplettheten_dekker_holderen(migrator):
    """Port 9: `aktivt` uten holder og `ledig` med holder er begge
    umulige former — CHECK-en er totalformen (SP-5)."""
    pa = _pa()
    try:
        pid = _plan(pa, host="c108-check.example", aktiver=False)
    finally:
        pa.close()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "INSERT INTO bestillingsplan_vindu (plan_id, tenant,"
            " vindu_start, vindu_slutt, tilstand, claim_id, lease_utloper)"
            " VALUES (%s,%s,now(),now()+interval '2 hours','aktivt',"
            " gen_random_uuid(), now()+interval '1 hour')", (pid, TENANT))
    migrator.rollback()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "INSERT INTO bestillingsplan_vindu (plan_id, tenant,"
            " vindu_start, vindu_slutt, claimet_av)"
            " VALUES (%s,%s,now(),now()+interval '2 hours',%s)",
            (pid, TENANT, PLANROLLE))
    migrator.rollback()


@pg
def test_gammel_claimer_fencer_fortsatt(migrator):
    """Port 11 (regresjon fra 044 port 46): et forsinket kall med et
    FORELDET claim preller av på claim_id-fencingen — holderkolonnen
    endret ikke fencing-regelen."""
    from plan.klassifiser import _nokkel
    pa = _pa()
    try:
        pid = _plan_forfalt(pa, migrator, host="c108-fence.example")
        vs = _syntetisk_vindu(migrator, pid, start_h=-1, slutt_h=1,
                              tilstand="ledig")
        _sett_kontekst(pa, TENANT)
        c1 = pa.execute("SELECT claim_id FROM claim_planvindu(%s,%s,%s,120)",
                        (TENANT, pid, vs)).fetchone()[0]
        pa.commit()
        _sett_kontekst(pa, TENANT)
        assert pa.execute("SELECT frigi_planvindu(%s,%s,%s,%s)",
                          (TENANT, pid, vs, c1)).fetchone()[0] == "frigitt"
        pa.commit()
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT claimet_av FROM bestillingsplan_vindu"
            " WHERE plan_id=%s AND vindu_start=%s",
            (pid, vs)).fetchone()[0] is None, "frigi visket ikke holderen"
        migrator.rollback()
        _sett_kontekst(pa, TENANT)
        c2 = pa.execute("SELECT claim_id FROM claim_planvindu(%s,%s,%s,120)",
                        (TENANT, pid, vs)).fetchone()[0]
        pa.commit()
        assert c2 != c1
        _sett_kontekst(pa, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            pa.execute("SELECT terminaliser_planvindu(%s,%s,%s,%s,%s,"
                       "'tillat',NULL,NULL)",
                       (TENANT, pid, vs, c1, _nokkel(pid, vs)))
        pa.rollback()
        _sett_kontekst(pa, TENANT)
        assert pa.execute("SELECT terminaliser_planvindu(%s,%s,%s,%s,%s,"
                          "'tillat',NULL,NULL)",
                          (TENANT, pid, vs, c2,
                           _nokkel(pid, vs))).fetchone()[0] \
            == "terminalisert"
        pa.commit()
    finally:
        pa.close()


# ---------------------------------------------------------------------------
# R47-1 (12–16)
# ---------------------------------------------------------------------------

def _hendelse_insert(c, uid, pid, *, kilde, pakrevd=2, opid=None):
    c.execute(
        "INSERT INTO policyaktivering (tenant, policy_id, utkast_id,"
        " runde, decision_operation_id, versjon, innholds_hash, diff_hash,"
        " attestant_a, aktiveringskilde, pakrevd_antall) VALUES"
        " (%s,%s,%s,1,%s,'1.0.0','ih','d','uavh',%s,%s)",
        (TEN, pid, uid, opid or "op-" + secrets.token_hex(4), kilde,
         pakrevd))


@pg
def test_styrt_hendelse_uten_kvorum_kan_ikke_foedes():
    """Port 12: gaten teller ATTESTASJONSRADENE (SP-9s andre form) —
    null eller for få kvalifiserende rader stopper INSERT-en, også for
    funksjonseieren selv. Med kvorumet på plass slipper samme form inn
    (positiv kontroll)."""
    c = _c()
    try:
        uid, pid = "u-" + secrets.token_hex(4), "p-" + secrets.token_hex(3)
        _validert_utkast(c, uid, pid, av="forf")
        _runde(c, uid, pakrevd_antall_godkjennere=2)
        c.commit()  # rollbackene under skal bare ta selve forsøket
        with pytest.raises(psycopg.errors.CheckViolation) as ei:
            _hendelse_insert(c, uid, pid, kilde="styrt")
        assert ei.value.diag.constraint_name == "hendelse_kvorum_gate"
        c.rollback()
        # Én attestasjon der kravet er to: fortsatt stengt.
        _attest(c, uid, "uavh", False)
        c.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            _hendelse_insert(c, uid, pid, kilde="styrt")
        c.rollback()
        # Positiv kontroll: to attestasjoner → formen slipper inn.
        _attest(c, uid, "uavh2", False)
        c.commit()
        _hendelse_insert(c, uid, pid, kilde="styrt")
        c.rollback()
    finally:
        c.close()


@pg
def test_kilde_null_og_ukjent_avvises():
    """Port 13 + 14: NULL kilde er umulig (NOT NULL — negativporten fra
    ratifiseringen), ukjent verdi stoppes av `hendelse_kilde_gyldig`."""
    c = _c()
    try:
        uid, pid = "u-" + secrets.token_hex(4), "p-" + secrets.token_hex(3)
        _validert_utkast(c, uid, pid, av="forf")
        _runde(c, uid, pakrevd_antall_godkjennere=2)
        c.commit()
        with pytest.raises(psycopg.errors.NotNullViolation):
            _hendelse_insert(c, uid, pid, kilde=None)
        c.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            _hendelse_insert(c, uid, pid, kilde="magefolelse")
        c.rollback()
    finally:
        c.close()


@pg
def test_kilden_er_immutabel_etter_insert():
    """Port 15: en styrt hendelse kan ikke omdøpes til 'historisk' i
    ettertid — immutabilitetsvernet dekker hele raden, også for eieren."""
    _full_aktivering(pakrevd=1)
    c = _c()
    try:
        for rolle in (None, "disponit_policy_eier"):
            c.execute("SELECT set_config('disponit.tenant',%s,true)",
                      (TEN,))
            if rolle:
                c.execute(f"SET ROLE {rolle}")
            with pytest.raises((psycopg.errors.RaiseException,
                                psycopg.errors.CheckViolation,
                                psycopg.errors.InsufficientPrivilege)):
                c.execute("UPDATE policyaktivering SET"
                          " aktiveringskilde='historisk' WHERE tenant=%s",
                          (TEN,))
            c.rollback()
    finally:
        c.close()


@pg
def test_aktiver_policy_skriver_kilde_og_kvorumskrav():
    """R47-1 live: den respleisede `aktiver_policy` føder hendelsen med
    kilde 'styrt' og RUNDENS kvorumskrav — for begge kvorumklassene."""
    for pakrevd in (1, 2):
        uid, pid, v = _full_aktivering(pakrevd=pakrevd)
        c = _c()
        try:
            rad = c.execute(
                "SELECT aktiveringskilde, pakrevd_antall"
                "  FROM policyaktivering WHERE tenant=%s AND policy_id=%s"
                "   AND versjon=%s", (TEN, pid, v)).fetchone()
            c.rollback()
            assert rad == ("styrt", pakrevd), (pakrevd, rad)
        finally:
            c.close()


@pg
def test_backfillen_binder_via_versjonsbindingen():
    """Port 16, statisk: backfill-UPDATEn i 048 binder via
    tenant+policy_id+versjon mot policyer og runden — ikke gjetting — og
    rapporten TELLER utfallet."""
    sql = M048.read_text(encoding="utf-8")
    assert re.search(
        r"UPDATE policyaktivering pa SET aktiveringskilde = p\.aktiveringskilde"
        r"\s+FROM policyer p\s+WHERE p\.tenant = pa\.tenant"
        r"\s+AND p\.policy_id = pa\.policy_id"
        r"\s+AND p\.versjon = pa\.versjon", sql), \
        "kilde-backfillen bruker ikke versjonsbindingen"
    assert re.search(
        r"SET pakrevd_antall = ar\.pakrevd_antall_godkjennere", sql), \
        "kvorumskravet hentes ikke fra runden"
    assert "RAISE NOTICE '048 hendelser" in sql, "rapporten teller ikke"
    assert "RAISE NOTICE '048 vinduer" in sql


# ---------------------------------------------------------------------------
# Deploy (17–18)
# ---------------------------------------------------------------------------

@pg
def test_masse_skriving_fyrer_utsatte_kontroller():
    """Port 17, statisk halvdel: hver masse-skriving i 048 følges av
    `SET CONSTRAINTS ALL IMMEDIATE` FØR neste ALTER-klasse-setning —
    047-prod-stoppets klasse (SP-10-regelen)."""
    sql = M048.read_text(encoding="utf-8")
    for start, neste_ddl in (
            ("UPDATE bestillingsplan_vindu SET claimet_av",
             "DROP CONSTRAINT vindu_tilstand_komplett"),
            ("UPDATE policyaktivering SET aktiveringskilde = 'historisk'",
             "SET NOT NULL")):
        i = sql.index(start)
        assert "SET CONSTRAINTS ALL IMMEDIATE" in \
            sql[i:sql.index(neste_ddl, i)], \
            f"masse-skrivingen ved {start!r} fyrer ikke utsatte kontroller"


@pg
def test_begge_sp10_kjoringene_staar_i_ci():
    """Port 18: tom-base-kjøringen (den herdede kjøreren) OG den bebodde
    prøvekjøringen står begge i CI — og prøvekjøringen peker på 048."""
    ci = (ROT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8")
    assert "python deploy/staging/migrer.py disponit" in ci
    assert re.search(r"sp10-provekjoring\.py 48\b", ci), \
        "SP-10-prøvekjøringen mot bebodd base mangler i CI"
    sp10 = (ROT / "deploy" / "staging"
            / "sp10-provekjoring.py").read_text(encoding="utf-8")
    assert "48: (_seed_048, _mal_048)" in sp10, \
        "048 har ingen registrert seed+måling"
