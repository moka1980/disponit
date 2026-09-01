"""M-4 v1 (migrasjon 093) — retensjonsregnskapets porter.

Modulen SLETTER INGENTING utenfor sine egne målerader. Den navngir hvert
lager, skriver ned hvilken frist og hvilken reaper som gjelder, måler
beholdning og alder — og gjør et lager UTEN SKREVET DOM til et funn.
Portene under måler nøyaktig de fire tingene som kan gjøre den påstanden
usann:

  1. REGISTERETS SANNHET. Et lager kan ikke PÅSTÅ at det står under frist
     uten å navngi reaperen, fristkilden og reap-markøren (CHECK), og
     påstanden må være sann mot BASEN: relasjonen og hver navngitte
     kolonne må finnes, og reaperen må ha et treff i `pg_proc`. Vi måler
     BEGGE veier — også den negative: funksjonen droppes, raden settes
     inn, og avvisningen kreves.
  2. PRIVILEGIENE, MÅLT MOT BASEN OG IKKE MOT KILDEKODEN. Måleren har
     null skriverett i HELE basen, og ingen payloadkolonne i noe grant.
     At måleren aldri leser persondata skal være en egenskap ved basen.
  3. MÅLINGENS ÆRLIGHET. Et lager som ikke kunne måles er et FUNN, aldri
     en null — og en avbrutt kjøring rapporteres som avbrutt, aldri som
     komplett. Begge injiseres: grantet revokes, og tidsgrensen settes
     under målekostnaden.
  4. KJØRINGENS FORM. Overlapp gir `hoppet_over` med feiltelleren urørt,
     to sammenhengende feil gir alarm, og to kjøringer på en uendret base
     gir to målinger og INGEN nye funnrader.

Axe-porten for flaten bor i `platform/core/ui/test/retensjon.test.js`
(jsdom + axe-core); den kjøres av `npm test`, ikke herfra.

Alle DB-tester konstruerer egen tilstand; ingen delt fixture.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, ANNEN_TENANT,  # noqa: F401
                       TENANT, app, klient, migrator, miljo, token)

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "093_m4_retensjonsregister.sql")
DRIFT = ROT / "platform" / "drift"
API = ROT / "platform" / "core" / "api" / "retensjon.py"
#: Målerollens EGEN innlogging. CI setter den (ci.yml), og
#: «Port: ingen DB-tester ble hoppet over» gjør et manglende sett til en
#: rød kjøring — en hoppet test er ikke en bestått test.
LAGERMAALER_DSN = os.environ.get("DISPONIT_TEST_LAGERMAALER_DSN")

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")
maaler = pytest.mark.skipif(
    not LAGERMAALER_DSN,
    reason="DISPONIT_TEST_LAGERMAALER_DSN ikke satt")


# ---------------------------------------------------------------------------
# Riggen. Hver test bygger og river sin egen tilstand.
# ---------------------------------------------------------------------------

def _eier(conn):
    """`SET LOCAL ROLE` til registerets eier. Migrator er MEDLEM (WITH
    INHERIT FALSE) — SET ROLE og ingenting annet."""
    conn.execute("SET LOCAL ROLE disponit_lager_eier")


def _reset(conn):
    conn.execute("RESET ROLE")


RAD = ("lager_id, relasjon, klasse, tenantkolonne, alderskolonne,"
       " reapetkolonne, fristkilde, frist_dogn, reaper, dom,"
       " dom_begrunnelse, dom_migrasjon")


def _sett_inn_lager(conn, **felt):
    """Én registerrad. Standardverdiene er en LOVLIG rad — hver test
    endrer nøyaktig det ene feltet den måler."""
    v = {"lager_id": "t-" + secrets.token_hex(4),
         "relasjon": "retensjonsmaaling", "klasse": "driftsspor",
         "tenantkolonne": None, "alderskolonne": "startet_ts",
         "reapetkolonne": "reapet_ts", "fristkilde": "test",
         "frist_dogn": 30, "reaper": "m4_reap_egne_maalinger",
         "dom": "under_frist", "dom_begrunnelse": "porttest",
         "dom_migrasjon": "093-test"}
    v.update(felt)
    conn.execute(
        f"INSERT INTO retensjonslager ({RAD}) VALUES"
        " (%(lager_id)s,%(relasjon)s,%(klasse)s,%(tenantkolonne)s,"
        "  %(alderskolonne)s,%(reapetkolonne)s,%(fristkilde)s,"
        "  %(frist_dogn)s,%(reaper)s,%(dom)s,%(dom_begrunnelse)s,"
        "  %(dom_migrasjon)s)", v)
    return v["lager_id"]


def _testlager(migrator, navn=None):
    """En EKTE tabell + registerrad, revet ned igjen etterpå.

    Navnet starter på `a_` med vilje: målingens rekkefølge er «lengst
    siden målt» med `NULLS FIRST`, så et aldri målt lager kommer først —
    og blant flere aldri målte avgjør `lager_id`. Da vet testen HVILKET
    lager `m4_mal_lagre(1)` tar.
    """
    navn = navn or ("a_m4test_" + secrets.token_hex(4))
    migrator.execute(
        f"CREATE TABLE {navn} (tenant text NOT NULL,"
        " rad_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
        " hemmelighet text NOT NULL DEFAULT 'payload',"
        " opprettet timestamptz NOT NULL DEFAULT now(),"
        " slettet_ts timestamptz)")
    migrator.execute(
        f"GRANT SELECT (tenant, opprettet, slettet_ts) ON {navn}"
        " TO disponit_lager_eier")
    migrator.commit()
    _eier(migrator)
    _sett_inn_lager(migrator, lager_id=navn, relasjon=navn,
                    klasse="persondata", tenantkolonne="tenant",
                    alderskolonne="opprettet", reapetkolonne="slettet_ts",
                    fristkilde=f"{navn}.frist", frist_dogn=90,
                    reaper="m4_reap_egne_maalinger")
    _reset(migrator)
    migrator.commit()
    return navn


def _riv_testlager(migrator, navn):
    # Rives OGSÅ etter en feilet assert: står transaksjonen i «aborted»,
    # avvises hver setning, og opprydningen ville etterlatt en
    # registerrad som forgifter hver senere test i suiten.
    migrator.rollback()
    _eier(migrator)
    migrator.execute("ALTER TABLE retensjonsbeholdning DISABLE TRIGGER"
                     " retensjonsbeholdning_vakt")
    migrator.execute("ALTER TABLE retensjonsstorrelse DISABLE TRIGGER"
                     " retensjonsstorrelse_vakt")
    migrator.execute("DELETE FROM retensjonsbeholdning WHERE lager_id=%s",
                     (navn,))
    migrator.execute("DELETE FROM retensjonsstorrelse WHERE lager_id=%s",
                     (navn,))
    migrator.execute("ALTER TABLE retensjonsbeholdning ENABLE TRIGGER"
                     " retensjonsbeholdning_vakt")
    migrator.execute("ALTER TABLE retensjonsstorrelse ENABLE TRIGGER"
                     " retensjonsstorrelse_vakt")
    migrator.execute("DELETE FROM retensjonsfunn WHERE lager_id=%s", (navn,))
    migrator.execute("DELETE FROM retensjonslager WHERE lager_id=%s", (navn,))
    _reset(migrator)
    migrator.execute(f"DROP TABLE IF EXISTS {navn}")
    migrator.commit()


def _maalerkobling():
    from db.pg import koble
    return koble(LAGERMAALER_DSN)


def _full_maaling():
    """Kjør til `ferdig`, så registeret står med ferske målt-tidspunkter."""
    from drift import retensjonsmaaling
    c = _maalerkobling()
    try:
        for _ in range(20):
            r = retensjonsmaaling.kjor(c, grense=200)
            if r.ferdig or r.feilet:
                return r
        return r
    finally:
        c.close()


# ===========================================================================
# 1. REGISTERETS SANNHET
# ===========================================================================

@pg
def test_frist_uten_reaper_avvises_av_vakten(migrator):     # noqa: F811
    """`under_frist` uten reaper/fristkilde/reap-markør er
    UREPRESENTERBART — ikke usannsynlig.

    MUTASJONEN SOM DREPER DENNE: gjør `retensjonslager_dom_vakt` til en
    trigger som advarer, eller fjern ett av de tre leddene fra CHECKen.
    """
    _eier(migrator)
    for felt in ({"reaper": None}, {"fristkilde": None},
                 {"reapetkolonne": None}):
        with pytest.raises(psycopg.errors.CheckViolation):
            _sett_inn_lager(migrator, **felt)
        migrator.rollback()
        _eier(migrator)
    # ... og den positive veien: en komplett rad går inn.
    lid = _sett_inn_lager(migrator)
    assert migrator.execute(
        "SELECT 1 FROM retensjonslager WHERE lager_id=%s", (lid,)).fetchone()
    migrator.rollback()


@pg
def test_uten_frist_med_reaper_avvises(migrator):           # noqa: F811
    """Den andre halvdelen av vakten: en dom som SIER «uten frist» kan
    ikke samtidig navngi en reaper eller en frist. Halve påstander er
    verre enn ingen — de leses som hele."""
    _eier(migrator)
    for dom in ("uten_frist_akseptert", "uten_frist_apen"):
        for felt in ({"reaper": "m4_reap_egne_maalinger"},
                     {"fristkilde": "noe"}, {"frist_dogn": 5}):
            v = {"dom": dom, "reaper": None, "fristkilde": None,
                 "frist_dogn": None}
            v.update(felt)
            with pytest.raises(psycopg.errors.CheckViolation):
                _sett_inn_lager(migrator, **v)
            migrator.rollback()
            _eier(migrator)
    migrator.rollback()


@pg
def test_lager_uten_relasjon_felles_av_triggeren(migrator):  # noqa: F811
    """En relasjon eller kolonne som ikke finnes skal felle SKRIVINGEN —
    ikke gi stille null ved neste måling."""
    _eier(migrator)
    with pytest.raises(psycopg.errors.RaiseException) as ei:
        _sett_inn_lager(migrator, relasjon="finnes_ikke_" + secrets.token_hex(3))
    assert "finnes ikke" in str(ei.value)
    migrator.rollback()
    _eier(migrator)
    with pytest.raises(psycopg.errors.RaiseException) as ei:
        _sett_inn_lager(migrator, alderskolonne="ikke_en_kolonne")
    assert "ikke_en_kolonne" in str(ei.value)
    migrator.rollback()
    _eier(migrator)
    with pytest.raises(psycopg.errors.RaiseException):
        _sett_inn_lager(migrator, tenantkolonne="ikke_en_kolonne")
    migrator.rollback()


@pg
def test_reaper_uten_funksjon_felles_av_triggeren(migrator):  # noqa: F811
    """DEN NEGATIVE VEIEN, målt og ikke antatt: funksjonen droppes, raden
    settes inn, og avvisningen KREVES.

    En reaper som ikke finnes er en løgn registeret ikke skal kunne bære:
    uten denne triggeren ville registeret rapportert «under frist» om et
    lager ingenting lenger rydder i.
    """
    navn = "m4_falsk_reaper_" + secrets.token_hex(4)
    _eier(migrator)
    # Først den positive veien: med funksjonen på plass går raden inn.
    migrator.execute(
        f"CREATE FUNCTION {navn}() RETURNS INT LANGUAGE sql AS"
        " $$ SELECT 0 $$")
    lid = _sett_inn_lager(migrator, reaper=navn)
    assert migrator.execute(
        "SELECT reaper FROM retensjonslager WHERE lager_id=%s",
        (lid,)).fetchone()[0] == navn
    migrator.execute("DELETE FROM retensjonslager WHERE lager_id=%s", (lid,))
    # ... og så uten den.
    migrator.execute(f"DROP FUNCTION {navn}()")
    with pytest.raises(psycopg.errors.RaiseException) as ei:
        _sett_inn_lager(migrator, reaper=navn)
    assert navn in str(ei.value) and "pg_proc" in str(ei.value)
    migrator.rollback()


@pg
def test_funntype_utenfor_lukket_sett_avvises(migrator):    # noqa: F811
    """Funntypene er et LUKKET SETT (M-6s form). En ukjent art kan ikke
    skrives — en klassifisering ingen har definert er ikke en dom."""
    _eier(migrator)
    mid = str(uuid.uuid4())
    migrator.execute("INSERT INTO retensjonsmaaling (maaling_id)"
                     " VALUES (%s)", (mid,))
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "INSERT INTO retensjonsfunn (funn_id, lager_id, relasjon,"
            " funntype, oppdaget_maaling, sist_sett_maaling)"
            " VALUES (%s,'x','retensjonsmaaling','noe_nytt',%s,%s)",
            (str(uuid.uuid4()), mid, mid))
    migrator.rollback()


@pg
def test_maaling_er_append_only(migrator):                  # noqa: F811
    """En registrert måling endres ikke og slettes ikke.

    De to LOVLIGE overgangene (lukkingen og reap-markeringen) er
    eksplisitte i vakten; alt annet heves. En måling som kunne redigeres
    i ettertid er ikke evidens.
    """
    _eier(migrator)
    mid = str(uuid.uuid4())
    migrator.execute("INSERT INTO retensjonsmaaling (maaling_id, fullfort_ts,"
                     " avbrutt) VALUES (%s, now(), false)", (mid,))
    migrator.commit()
    _eier(migrator)
    for setning, params in (
            ("UPDATE retensjonsmaaling SET antall_funn = 0"
             " WHERE maaling_id=%s", (mid,)),
            ("UPDATE retensjonsmaaling SET avbrutt = true"
             " WHERE maaling_id=%s", (mid,)),
            ("UPDATE retensjonsmaaling SET startet_ts = now()"
             " WHERE maaling_id=%s", (mid,)),
            ("DELETE FROM retensjonsmaaling WHERE maaling_id=%s", (mid,))):
        with pytest.raises(psycopg.errors.RaiseException):
            migrator.execute(setning, params)
        migrator.rollback()
        _eier(migrator)
    # Reap-markeringen ER lovlig — og bare den.
    migrator.execute("UPDATE retensjonsmaaling SET reapet_ts = now()"
                     " WHERE maaling_id=%s", (mid,))
    migrator.commit()
    _eier(migrator)
    migrator.execute("ALTER TABLE retensjonsmaaling DISABLE TRIGGER"
                     " retensjonsmaaling_vakt")
    migrator.execute("DELETE FROM retensjonsmaaling WHERE maaling_id=%s",
                     (mid,))
    migrator.execute("ALTER TABLE retensjonsmaaling ENABLE TRIGGER"
                     " retensjonsmaaling_vakt")
    _reset(migrator)
    migrator.commit()


# ===========================================================================
# 2. PRIVILEGIENE — målt mot BASEN, ikke mot kildekoden
# ===========================================================================

@pg
def test_maaleren_har_ingen_skriverett_i_hele_basen(migrator):  # noqa: F811
    """`disponit_lagermaaler` har NULL INSERT/UPDATE/DELETE/TRUNCATE i
    HELE basen — målt i `information_schema.role_table_grants`.

    Kildekoden kan si hva den vil; dette er hva basen tillater.
    """
    rader = migrator.execute(
        "SELECT table_name, privilege_type"
        "  FROM information_schema.role_table_grants"
        " WHERE grantee = 'disponit_lagermaaler'"
        "   AND privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE')"
    ).fetchall()
    assert rader == [], rader
    # ... og heller ingen SELECT: rollen når basen KUN gjennom sin ene
    # funksjon.
    lese = migrator.execute(
        "SELECT table_name FROM information_schema.role_table_grants"
        " WHERE grantee='disponit_lagermaaler'").fetchall()
    assert lese == [], lese
    kolonner = migrator.execute(
        "SELECT table_name, column_name"
        "  FROM information_schema.column_privileges"
        " WHERE grantee='disponit_lagermaaler'").fetchall()
    assert kolonner == [], kolonner


@pg
def test_maaleren_har_execute_paa_noyaktig_en_funksjon(migrator):  # noqa: F811
    """Fraværet av alt annet ER rollen. Én EXECUTE, og den er måledøren.

    Reaperen står bevisst IKKE her: den kalles av `m4_mal_lagre` når
    kjøringen lukker seg, og en reaper med sin egen inngang er en
    slettevei til.
    """
    # ACL-en leses EKSPLISITT: `has_function_privilege` er sann også for
    # det PUBLIC har, og en port som ikke skiller dem ville sagt at
    # måleren har rettigheter den bare arver av å være en rolle.
    rader = migrator.execute(
        "SELECT p.proname FROM pg_proc p"
        "  JOIN pg_namespace n ON n.oid = p.pronamespace,"
        "  LATERAL aclexplode(p.proacl) a"
        " WHERE n.nspname='public' AND a.privilege_type='EXECUTE'"
        "   AND a.grantee = (SELECT oid FROM pg_roles"
        "                     WHERE rolname='disponit_lagermaaler')"
        " ORDER BY 1").fetchall()
    navn = sorted({r[0] for r in rader})
    assert navn == ["m4_mal_lagre"], navn


@pg
def test_eieren_har_delete_kun_paa_egne_tabeller(migrator):  # noqa: F811
    """`disponit_lager_eier` kan slette i retensjons*-tabellene og INGEN
    andre. Modulen som fører retensjonsregnskapet skal ikke kunne bli en
    slettevei."""
    rader = migrator.execute(
        "SELECT c.relname FROM pg_class c"
        "  JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname='public' AND c.relkind IN ('r','p')"
        "   AND (has_table_privilege('disponit_lager_eier', c.oid, 'DELETE')"
        "     OR has_table_privilege('disponit_lager_eier', c.oid,"
        "                            'TRUNCATE')"
        "     OR has_table_privilege('disponit_lager_eier', c.oid, 'INSERT')"
        "     OR has_table_privilege('disponit_lager_eier', c.oid, 'UPDATE'))"
        " ORDER BY 1").fetchall()
    navn = sorted(r[0] for r in rader)
    assert all(n.startswith("retensjons") for n in navn), navn
    assert set(navn) == {"retensjonsbeholdning", "retensjonsfunn",
                         "retensjonslager", "retensjonsmaaling",
                         "retensjonsstorrelse"}, navn


@pg
def test_kolonnegrantene_er_delmengde_av_registerets_tre(migrator):  # noqa: F811
    """DEN SKARPESTE PORTEN: hver kolonne `disponit_lager_eier` kan lese i
    et MÅLT lager, må stå i registeret som tenant-, alders- eller
    reap-kolonne.

    En payloadkolonne i grantet feller porten — og det er hele poenget:
    at måleren aldri leser persondata skal være en egenskap ved BASEN,
    ikke ved disiplinen i koden.

    MUTASJONEN SOM DREPER DENNE: bytt `GRANT SELECT (…)` i 093 §6.3 mot
    `GRANT SELECT ON`.
    """
    lovlige = {}
    _eier(migrator)
    for relasjon, tk, ak, rk in migrator.execute(
            "SELECT relasjon, tenantkolonne, alderskolonne, reapetkolonne"
            "  FROM retensjonslager WHERE dom_migrasjon = '093'").fetchall():
        lovlige[relasjon] = {k for k in (tk, ak, rk) if k}
    _reset(migrator)
    grantet = migrator.execute(
        "SELECT table_name, column_name"
        "  FROM information_schema.column_privileges"
        " WHERE grantee='disponit_lager_eier' AND privilege_type='SELECT'"
    ).fetchall()
    assert grantet, "ingen kolonnegrant i det hele tatt — porten måler ingenting"
    for tabell, kolonne in grantet:
        assert tabell in lovlige, (tabell, "grant til et uregistrert lager")
        assert kolonne in lovlige[tabell], (tabell, kolonne, lovlige[tabell])
    # ... og INGEN tabellgrant på de målte lagrene: et `GRANT SELECT ON`
    # ville gitt alle kolonner uten å dukke opp over.
    tabellgrant = migrator.execute(
        "SELECT table_name FROM information_schema.role_table_grants"
        " WHERE grantee='disponit_lager_eier' AND privilege_type='SELECT'"
        "   AND table_name NOT LIKE 'retensjons%'").fetchall()
    assert tabellgrant == [], tabellgrant


@pg
def test_kryss_tenant_er_policy_aldri_bypassrls(migrator):  # noqa: F811
    """Kryss-tenant-autoriteten er en EKSPLISITT policy per målt tabell,
    ikke en rolleattributt. `BYPASSRLS` ville gjeldt overalt, for alltid
    og for hver framtidig tabell."""
    for rolle in ("disponit_lager_eier", "disponit_lagermaaler"):
        assert migrator.execute(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname=%s",
            (rolle,)).fetchone()[0] is False, rolle
    _eier(migrator)
    # KUN de MIGRASJONSSEEDEDE radene: en testinjisert registerrad har
    # ingen policy, og porten skal måle det 093 la inn.
    maalte = [r[0] for r in migrator.execute(
        "SELECT relasjon FROM retensjonslager WHERE reapetkolonne IS NOT NULL"
        "   AND dom_migrasjon = '093'"
        "   AND relasjon NOT LIKE 'retensjons%'").fetchall()]
    _reset(migrator)
    for relasjon in maalte:
        assert migrator.execute(
            "SELECT 1 FROM pg_policies WHERE schemaname='public'"
            "   AND tablename=%s AND policyname='m4_maaler'",
            (relasjon,)).fetchone(), relasjon
    # Beholdningen har BEGGE: tenant-isolasjon for alle, og målerens egen.
    for navn in ("tenant_isolasjon", "m4_maaler"):
        assert migrator.execute(
            "SELECT 1 FROM pg_policies WHERE schemaname='public'"
            "   AND tablename='retensjonsbeholdning' AND policyname=%s",
            (navn,)).fetchone(), navn
    rls = migrator.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
        " WHERE oid='public.retensjonsbeholdning'::regclass").fetchone()
    assert rls == (True, True), rls


# ---------------------------------------------------------------------------
# Statisk port: modulen er ingen slettevei
# ---------------------------------------------------------------------------

def _uten_kommentar_og_streng(sql: str) -> str:
    """Fjerner SQL-kommentarer og strengliteraler.

    Uten dette ville porten reagert på reapernavn som står som DATA i
    seedingen og som prosa i kommentarene — og en port som feiler på sin
    egen dokumentasjon blir skrudd av.
    """
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    return sql


def _modulkilde() -> list[tuple[str, str]]:
    filer = [MIGRASJON, DRIFT / "retensjonsmaaling.py",
             DRIFT / "kjor_retensjonsmaaling.py", API]
    return [(f.name, f.read_text(encoding="utf-8")) for f in filer]


def test_modulen_sletter_ingenting_utenfor_egne_tabeller():
    """v1-DOMMEN som statisk port: ingen DELETE, ingen TRUNCATE og ingen
    UPDATE mot en tabell utenfor `retensjons*`.

    Modulen som fører retensjonsregnskapet skal ikke kunne bli en
    slettevei. Sletting fortsetter å skje nøyaktig der den skjer i dag.
    """
    funnet = 0
    for navn, kilde in _modulkilde():
        ren = _uten_kommentar_og_streng(kilde)
        maal = []
        maal += re.findall(r"\bDELETE\s+FROM\s+(?:public\.)?(\w+)", ren,
                           re.I)
        maal += re.findall(r"\bTRUNCATE\s+(?:TABLE\s+)?(?:public\.)?(\w+)",
                           ren, re.I)
        maal += re.findall(
            r"\bUPDATE\s+(?:ONLY\s+)?(?:public\.)?(\w+)\s+(?:\w+\s+)?SET\b",
            ren, re.I)
        funnet += len(maal)
        for tabell in maal:
            assert tabell.lower().startswith("retensjons"), (navn, tabell)
    # PORTEN MÅ IKKE VÆRE TOM. Strengfjerningen over kan i prinsippet
    # svelge kode hvis en apostrof står alene et sted — og da ville
    # løkken vært grønn fordi den ikke så noe. Modulen HAR skrivinger
    # (reapen og målingen), så et null-treff er et bevis på at porten
    # måler feil, ikke på at modulen er ren.
    assert funnet >= 4, (
        f"porten fant {funnet} skrivinger — den ser ikke kilden lenger")


def test_modulen_kaller_ingen_fremmed_reaper():
    """Registeret NAVNGIR reaperne — det KALLER dem aldri.

    Forskjellen er hele v1-dommen: en ny slettevei ved siden av de seks
    som alt kjører ville vært den farligste koden i huset. Modulens ene
    unntak er `m4_reap_egne_maalinger`, som rører egne aggregatrader.
    """
    for navn, kilde in _modulkilde():
        ren = _uten_kommentar_og_streng(kilde)
        kall = re.findall(r"\b((?:reap|makuler|rydd)_\w+)\s*\(", ren, re.I)
        fremmede = [k for k in kall if k != "m4_reap_egne_maalinger"]
        assert fremmede == [], (navn, fremmede)


def test_maaledoren_bygger_identifikatorer_fra_registeret_aldri_fra_argument():
    """`format(%I)` fra registerets VERIFISERTE kolonnenavn — aldri fra et
    kallargument. Døren har to argumenter: en INT-grense og et
    BOOLEAN-flagg, og ingen av dem kan bli en identifikator."""
    sql = MIGRASJON.read_text(encoding="utf-8")
    kropp = sql[sql.index("FUNCTION m4_mal_lagre"):]
    kropp = kropp[:kropp.index("$$;")]
    assert "p_grense INT" in kropp and "p_umaalbar BOOLEAN" in kropp
    # Ingen strenginterpolering av et argument inn i dynamisk SQL.
    for arg in ("p_grense", "p_umaalbar"):
        assert f"%I', {arg}" not in kropp and f"|| {arg}" not in kropp, arg
    assert "format(" in kropp and "%I" in kropp


# ===========================================================================
# 3. MÅLINGENS ÆRLIGHET
# ===========================================================================

@pg
@maaler
def test_umaalbart_lager_blir_funn_aldri_null(migrator):    # noqa: F811
    """DEN BÆRENDE REGELEN, INJISERT: grantet revokes på ett lager, og
    målingen skal gi FUNN — ikke en beholdningsrad med 0.

    En rapport som sier «0 ureapede rader» fordi grantet manglet, er ikke
    en grønn måling — den er en måling som ikke kjørte.
    """
    from drift import retensjonsmaaling
    _full_maaling()                     # alle seedede lagre får et målt-ts
    navn = _testlager(migrator)
    try:
        migrator.execute(
            f"INSERT INTO {navn} (tenant) VALUES (%s)", (TENANT,))
        migrator.execute(f"REVOKE SELECT ON {navn} FROM disponit_lager_eier")
        migrator.commit()
        c = _maalerkobling()
        try:
            r = retensjonsmaaling.kjor(c, grense=1)
        finally:
            c.close()
        assert r.umaalbare == 1, r
        assert r.umaalbare_lagre == [navn], r.umaalbare_lagre
        assert not r.feilet
        _eier(migrator)
        funn = migrator.execute(
            "SELECT funntype FROM retensjonsfunn WHERE lager_id=%s"
            "   AND lukket_maaling IS NULL", (navn,)).fetchall()
        assert funn == [("umaalbar",)], funn
        # ... og INGEN beholdningsrad. En null her ville vært løgnen.
        assert migrator.execute(
            "SELECT count(*) FROM retensjonsbeholdning WHERE lager_id=%s",
            (navn,)).fetchone()[0] == 0
        assert migrator.execute(
            "SELECT count(*) FROM retensjonsstorrelse WHERE lager_id=%s",
            (navn,)).fetchone()[0] == 0
        _reset(migrator)
        migrator.commit()
    finally:
        _riv_testlager(migrator, navn)


@pg
@maaler
def test_tidsgrense_under_maalekostnaden_gir_funn_ikke_null(migrator):  # noqa: F811
    """Samme regel, andre injeksjon: `statement_timeout` settes under
    målekostnaden. En timeout er en måling som ikke kjørte."""
    from drift import retensjonsmaaling
    _full_maaling()
    navn = _testlager(migrator)
    try:
        migrator.execute(
            f"INSERT INTO {navn} (tenant) SELECT %s FROM generate_series(1,50)",
            (TENANT,))
        migrator.commit()
        c = _maalerkobling()
        try:
            # 1 ms er under kostnaden for ENHVER måling — og det er
            # poenget: veien skal kunne injiseres, ikke bare resonneres.
            r = retensjonsmaaling.kjor(c, grense=1, tidsgrense_ms=1)
        finally:
            c.close()
        assert r.umaalbare == 1 and r.malt == 0, r
        assert r.umaalbare_lagre == [navn], r.umaalbare_lagre
        _eier(migrator)
        assert migrator.execute(
            "SELECT funntype FROM retensjonsfunn WHERE lager_id=%s"
            "   AND lukket_maaling IS NULL", (navn,)).fetchall() \
            == [("umaalbar",)]
        assert migrator.execute(
            "SELECT count(*) FROM retensjonsbeholdning WHERE lager_id=%s",
            (navn,)).fetchone()[0] == 0
        _reset(migrator)
        migrator.commit()
    finally:
        _riv_testlager(migrator, navn)


@pg
@maaler
def test_avbrutt_kjoring_star_som_avbrutt(migrator):        # noqa: F811
    """En kjøring som ikke rakk ferdig står som `avbrutt = true`.

    Raden fødes avbrutt og blir bare komplett når kjøringen lukker seg —
    så et krasj midt i en måling kan ikke etterlate en rad som SER
    komplett ut.
    """
    from drift import retensjonsmaaling
    _full_maaling()
    navn = _testlager(migrator)
    try:
        c = _maalerkobling()
        try:
            r = retensjonsmaaling.kjor(c, grense=1)
        finally:
            c.close()
        assert not r.ferdig, "grensen på 1 skulle ikke rukket hele registeret"
        _eier(migrator)
        rad = migrator.execute(
            "SELECT avbrutt, fullfort_ts FROM retensjonsmaaling"
            " WHERE maaling_id=%s", (r.maaling_id,)).fetchone()
        _reset(migrator)
        assert rad[0] is True and rad[1] is None, rad
        # ... og kjøres den ferdig, lukker den seg ÆRLIG.
        _full_maaling()
        _eier(migrator)
        rad = migrator.execute(
            "SELECT avbrutt, fullfort_ts FROM retensjonsmaaling"
            " WHERE maaling_id=%s", (r.maaling_id,)).fetchone()
        _reset(migrator)
        assert rad[0] is False and rad[1] is not None, rad
    finally:
        _riv_testlager(migrator, navn)


@pg
@maaler
def test_ny_tabell_blir_uregistrert_funn(migrator):         # noqa: F811
    """Et lager UTEN SKREVET DOM er et FUNN. Det er hele modulen.

    En ny tabell i katalogen som ingen har felt en dom over, skal være
    synlig — ikke stille.
    """
    navn = "m4_ukjent_" + secrets.token_hex(4)
    migrator.execute(f"CREATE TABLE {navn} (id int PRIMARY KEY)")
    migrator.commit()
    try:
        _full_maaling()
        _eier(migrator)
        funn = migrator.execute(
            "SELECT funntype, relasjon FROM retensjonsfunn"
            " WHERE relasjon=%s AND lukket_maaling IS NULL",
            (navn,)).fetchall()
        _reset(migrator)
        assert funn == [("uregistrert", navn)], funn
    finally:
        migrator.execute(f"DROP TABLE IF EXISTS {navn}")
        migrator.commit()
        _full_maaling()             # funnet lukkes når grunnen forsvinner
        _eier(migrator)
        migrator.execute("DELETE FROM retensjonsfunn WHERE relasjon=%s",
                         (navn,))
        _reset(migrator)
        migrator.commit()


@pg
@maaler
def test_to_kjoringer_gir_to_maalinger_og_ingen_nye_funn(migrator):  # noqa: F811
    """IDEMPOTENS: funnlisten er ikke en logg som vokser med kadensen.

    ETT funn per (lager, funntype) holdes åpent og oppdateres med
    `sist_sett_maaling`. En funnliste som vokser er en funnliste ingen
    leser — og da forsvinner de viktige funnene med de gamle.
    """
    _full_maaling()
    _eier(migrator)
    for_rader = migrator.execute(
        "SELECT count(*) FROM retensjonsfunn").fetchone()[0]
    for_maalinger = migrator.execute(
        "SELECT count(*) FROM retensjonsmaaling").fetchone()[0]
    _reset(migrator)
    migrator.commit()

    r = _full_maaling()
    assert r.ferdig
    _eier(migrator)
    etter_rader = migrator.execute(
        "SELECT count(*) FROM retensjonsfunn").fetchone()[0]
    etter_maalinger = migrator.execute(
        "SELECT count(*) FROM retensjonsmaaling").fetchone()[0]
    sist_sett = migrator.execute(
        "SELECT count(*) FROM retensjonsfunn"
        " WHERE lukket_maaling IS NULL AND sist_sett_maaling=%s",
        (r.maaling_id,)).fetchone()[0]
    apne = migrator.execute(
        "SELECT count(*) FROM retensjonsfunn"
        " WHERE lukket_maaling IS NULL").fetchone()[0]
    _reset(migrator)
    migrator.commit()
    assert etter_maalinger == for_maalinger + 1
    assert etter_rader == for_rader, "funnlisten vokste av en ny kjøring"
    assert sist_sett == apne, "et åpent funn ble ikke sett i siste kjøring"


# ===========================================================================
# 4. KJØRINGENS FORM
# ===========================================================================

@pg
@maaler
def test_overlappende_kjoring_hopper_over_uten_aa_feile():
    """En kjøring som finner arbeidernøkkelen opptatt har verken lyktes
    eller feilet. Skrev den 0 i feiltelleren, ville en henger som holder
    låsen slettet en alt opptelt feil ved hver aktivering."""
    from drift import retensjonsmaaling
    holder = _maalerkobling()
    annen = _maalerkobling()
    try:
        assert holder.execute(
            "SELECT pg_try_advisory_lock(%s)",
            (retensjonsmaaling.ARBEIDERNOKKEL,)).fetchone()[0] is True
        r = retensjonsmaaling.kjor(annen, tidligere_feil=1)
        assert r.hoppet_over is True
        assert r.feilet is False and r.alarm_utlost is False
        assert r.malt == 0 and r.umaalbare == 0
    finally:
        annen.close()
        holder.close()


def test_hoppet_over_lar_feiltelleren_staa_uroert(tmp_path, monkeypatch):
    """Telleren skal stå NØYAKTIG som den sto. Målt på det `main()`
    faktisk gjør med tilstandsfila, ikke på en etterligning."""
    from drift import kjor_retensjonsmaaling as kjm
    from drift import retensjonsmaaling
    fil = tmp_path / "tilstand.json"
    fil.write_text(json.dumps({"feil": 1}), encoding="utf-8")
    monkeypatch.setenv("DISPONIT_MAALETILSTAND", str(fil))
    monkeypatch.setenv("DISPONIT_LAGERMAALER_URL", "postgresql://ugyldig")
    monkeypatch.setattr(kjm, "_koble", lambda dsn: object())
    monkeypatch.setattr(
        retensjonsmaaling, "kjor",
        lambda *a, **k: retensjonsmaaling.Maaleresultat(hoppet_over=True))
    assert kjm.main() == 0
    assert json.loads(fil.read_text(encoding="utf-8"))["feil"] == 1


def test_to_sammenhengende_feil_utloser_alarm(tmp_path, monkeypatch, capsys):
    """En stille målejobb er et retensjonsregnskap som slutter å stemme
    uten at noen ser det. Alarmen krever TO — én feilet kjøring er en
    hendelse, to er en tilstand."""
    from drift import kjor_retensjonsmaaling as kjm
    fil = tmp_path / "tilstand.json"
    monkeypatch.setenv("DISPONIT_MAALETILSTAND", str(fil))
    monkeypatch.setenv("DISPONIT_LAGERMAALER_URL", "postgresql://ugyldig")

    def _nekt(dsn):
        raise RuntimeError("ingen tilkobling")
    monkeypatch.setattr(kjm, "_koble", _nekt)

    assert kjm.main() == 1
    forste = json.loads(capsys.readouterr().out.strip())
    assert forste["feilet"] == 1 and forste["alarm"] == 0
    assert json.loads(fil.read_text(encoding="utf-8"))["feil"] == 1

    assert kjm.main() == 1
    andre = json.loads(capsys.readouterr().out.strip())
    assert andre["sammenhengende_feil"] == 2 and andre["alarm"] == 1


def test_manglende_dsn_nekter_oppstart(monkeypatch, capsys):
    """En jobb uten sin egen DSN skal STOPPE, ikke falle tilbake til
    runtime-rollen — som ikke har måledøren i det hele tatt."""
    from drift import kjor_retensjonsmaaling as kjm
    monkeypatch.delenv("DISPONIT_LAGERMAALER_URL", raising=False)
    monkeypatch.setattr(kjm, "_koble",
                        lambda dsn: pytest.fail("koblet uten DSN"))
    assert kjm.main() == 2


# ===========================================================================
# 5. ISOLASJON OG FLATE
# ===========================================================================

@pg
def test_runtime_har_ingen_direkte_lesing_av_maalelagrene(migrator):  # noqa: F811
    """Runtime når registeret KUN gjennom lesedørene. Det finnes ingen
    SELECT-rettighet å falle tilbake på (SP-7/090-formen)."""
    for tabell in ("retensjonslager", "retensjonsmaaling",
                   "retensjonsstorrelse", "retensjonsbeholdning",
                   "retensjonsfunn"):
        assert migrator.execute(
            "SELECT has_table_privilege('disponit', %s, 'SELECT')",
            (tabell,)).fetchone()[0] is False, tabell


@pg
@maaler
def test_tenant_ser_aldri_en_annen_tenants_beholdning(
        migrator, klient, token):                           # noqa: F811
    """Isolasjonen måles BEGGE veier: direkte DML gjennom
    `tenant_isolasjon`, og over API-et gjennom lesedørens filter."""
    _full_maaling()
    navn = _testlager(migrator)
    try:
        migrator.execute(
            f"INSERT INTO {navn} (tenant) SELECT %s FROM generate_series(1,3)",
            (TENANT,))
        migrator.execute(
            f"INSERT INTO {navn} (tenant) SELECT %s FROM generate_series(1,7)",
            (ANNEN_TENANT,))
        migrator.commit()
        from drift import retensjonsmaaling
        c = _maalerkobling()
        try:
            r = retensjonsmaaling.kjor(c, grense=1)
        finally:
            c.close()
        assert r.malt == 1, r
        _eier(migrator)
        # Målingen SÅ begge tenantene (kryss-tenant på 038-formen).
        rader = dict(migrator.execute(
            "SELECT tenant, rader_ureapet FROM retensjonsbeholdning"
            " WHERE lager_id=%s", (navn,)).fetchall())
        _reset(migrator)
        migrator.commit()
        assert rader == {TENANT: 3, ANNEN_TENANT: 7}, rader

        # ... og INGEN annen rolle kommer til radene i det hele tatt.
        # Det er sterkere enn RLS: måleren og runtime har ikke SELECT,
        # så det finnes ingen tenantkontekst å sette feil.
        for dsn in (LAGERMAALER_DSN, DSN):
            from db.pg import koble
            c = koble(dsn)
            try:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    c.execute("SELECT * FROM retensjonsbeholdning")
            finally:
                c.close()

        # ... og over API-et ser økten KUN sin egen rad.
        tok, _ = token(tenant=TENANT, rolle="admin",
                       scopes=["security:read"])
        svar = klient.get("/v1/retensjon",
                          headers={"authorization": f"Bearer {tok}"})
        assert svar.status_code == 200, svar.text
        mine = [l for l in svar.json()["lagre"] if l["lager_id"] == navn]
        assert len(mine) == 1 and mine[0]["rader_ureapet"] == 3, mine
    finally:
        _riv_testlager(migrator, navn)


@pg
@maaler
def test_security_read_ser_registeret_men_verken_funn_eller_bytes(
        migrator, klient, token):                           # noqa: F811
    """SNITTET. `security:read` ser registeret og egen beholdning;
    katalogtallene og funnlisten er kontrollplanets.

    `null` og `[]` er ULIKE i svaret: det første sier «du ser ikke denne
    delen», det andre «det finnes ingen». En flate som ikke kan skille
    dem, viser manglende tilgang som en ren rapport.
    """
    _full_maaling()
    tok, _ = token(tenant=TENANT, rolle="admin", scopes=["security:read"])
    svar = klient.get("/v1/retensjon",
                      headers={"authorization": f"Bearer {tok}"})
    assert svar.status_code == 200, svar.text
    k = svar.json()
    assert k["plattformdrift"] is False
    assert k["funn"] is None and k["katalog"] is None
    assert k["lagre"], "registeret skal være synlig for security:read"
    assert k["maaling"] and k["maaling"]["avbrutt"] is False
    for lager in k["lagre"]:
        assert "dom" in lager and lager["dom_begrunnelse"]
        assert "bytes_totalt" not in lager
        assert "rader_estimat" not in lager

    adm, _ = token(tenant=TENANT, rolle="admin",
                   scopes=["security:read", "platform:admin"])
    svar = klient.get("/v1/retensjon",
                      headers={"authorization": f"Bearer {adm}"})
    assert svar.status_code == 200, svar.text
    k = svar.json()
    assert k["plattformdrift"] is True
    assert isinstance(k["katalog"], list) and k["katalog"]
    assert isinstance(k["funn"], list)
    assert any(r["bytes_totalt"] is not None for r in k["katalog"])


@pg
def test_endepunktet_krever_scope_og_har_ingen_skrivevei(
        klient, token):                                     # noqa: F811
    """Ruten er `security:read`, og mutasjonen finnes ikke som HTTP:
    målingen skrives av timerens egen rolle, og registerets dommer felles
    i migrasjon."""
    uten, _ = token(scopes=["decisions:read"])
    svar = klient.get("/v1/retensjon",
                      headers={"authorization": f"Bearer {uten}"})
    assert svar.status_code == 403, svar.text
    med, _ = token(rolle="admin", scopes=["security:read"])
    h = {"authorization": f"Bearer {med}"}
    for metode in ("post", "put", "delete", "patch"):
        svar = getattr(klient, metode)("/v1/retensjon", headers=h)
        assert svar.status_code == 405, (metode, svar.status_code)


def test_ruten_er_security_read_og_ikke_platform_admin():
    """DEN KONKRETE GRUNNEN, pinnet: `platform:admin` står ikke i
    `LESESCOPES`, og en browserøkt mot et scope utenfor det settet
    avvises. En rute deklarert `platform:admin` ville gitt 403 for hver
    eneste innlogging — derfor er kontrollplanet en UTVIDELSE av svaret.
    """
    from api.app import RUTESCOPE, LESESCOPES
    assert RUTESCOPE[("GET", "/v1/retensjon")] == "security:read"
    assert "security:read" in LESESCOPES
    assert "platform:admin" not in LESESCOPES
    from api import retensjon as modul
    assert modul.PLATTFORMDRIFT == "platform:admin"


# ===========================================================================
# 6. GRENSEN — hver invariant målt som FORSØK og som BRUDD
# ===========================================================================

def _gront_artefakt():
    from manifestskjema import M4_INVARIANTER
    maalt = {}
    for navn in M4_INVARIANTER:
        maalt[f"{navn}_forsok"] = 1
        maalt[f"{navn}_brudd"] = 0
    maalt["ddl_begge_kjoringer_gronne"] = True
    return {"krav_id": "m4-v1", "ts": "2026-09-01T00:00:00+00:00",
            "bestatt": True,
            "oppsett": {"modul": "m04_dataforvalter", "commit": "0" * 40,
                        "vert": "lokal", "tenant": "t-test"},
            "maalt": maalt, "funn": []}


def test_grensen_dekker_de_fjorten_invariantene():
    """Grensen ble registrert FØR koden (§0-regelen). Testen pinner den
    mot ANTALLET og mot ja-punktet, ikke mot listen selv."""
    from manifestskjema import KRAVGRENSER, M4_INVARIANTER
    g = KRAVGRENSER["m4-v1"]
    assert len(M4_INVARIANTER) == len(set(M4_INVARIANTER)) == 14
    assert g["invarianter"] is M4_INVARIANTER
    assert g["krav_ja"] == ("ddl_begge_kjoringer_gronne",)
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["punktbinding"] == {}


def test_grensen_maaler_hver_invariant_som_forsok_og_som_brudd():
    """Hver invariant SKAL måles begge veier: ett brudd feller, og null
    forsøk feller. En invariant ingen forsøkte å bryte er ikke målt."""
    from manifestskjema import M4_INVARIANTER, _sjekk_grenser
    assert _sjekk_grenser("m4-v1", _gront_artefakt()) == []
    for navn in M4_INVARIANTER:
        art = _gront_artefakt()
        art["maalt"][f"{navn}_brudd"] = 1
        assert any(f"{navn}_brudd=1" in f
                   for f in _sjekk_grenser("m4-v1", art)), navn
        art = _gront_artefakt()
        art["maalt"][f"{navn}_forsok"] = 0
        assert any(f"{navn}_forsok=0" in f
                   for f in _sjekk_grenser("m4-v1", art)), navn
    for verdi in (False, None, 1, "ja"):
        art = _gront_artefakt()
        art["maalt"]["ddl_begge_kjoringer_gronne"] = verdi
        assert any("ddl_begge_kjoringer_gronne" in f
                   for f in _sjekk_grenser("m4-v1", art)), verdi


# ===========================================================================
# 7. SP-10 — migrasjonen grønn fra TOM base og mot SEEDET base
# ===========================================================================

@pg
def test_migrasjonen_er_idempotent_mot_seedet_base(migrator):  # noqa: F811
    """SP-10, andre halvdel: migrasjonen kjøres på nytt mot en base der
    den ALT har kjørt, og skal være grønn.

    (Første halvdel — tom base — er CI-riggens egen kjede: 093 kjøres
    der fra 001 og opp, hver gang.)
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    migrator.execute(sql)
    migrator.commit()
    _eier(migrator)
    antall = migrator.execute(
        "SELECT count(*) FROM retensjonslager").fetchone()[0]
    dommer = dict(migrator.execute(
        "SELECT dom, count(*) FROM retensjonslager GROUP BY 1").fetchall())
    _reset(migrator)
    migrator.commit()
    # Seedet er uendret av en ny kjøring (ON CONFLICT DO NOTHING).
    assert antall >= 18, antall
    assert dommer.get("under_frist", 0) >= 17, dommer
    assert dommer.get("uten_frist_akseptert", 0) >= 1, dommer


@pg
def test_seedet_er_verifisert_mot_de_faktiske_kolonnene(migrator):  # noqa: F811
    """Hver seedet rad peker på en kolonne som FINNES. Triggerne felte
    den ellers ved innsetting — denne porten sier at de gjorde jobben."""
    _eier(migrator)
    rader = migrator.execute(
        "SELECT lager_id, relasjon, tenantkolonne, alderskolonne,"
        "       reapetkolonne, reaper FROM retensjonslager").fetchall()
    _reset(migrator)
    assert rader
    for lager_id, relasjon, tk, ak, rk, reaper in rader:
        assert migrator.execute(
            "SELECT to_regclass('public.' || quote_ident(%s))",
            (relasjon,)).fetchone()[0] is not None, lager_id
        for kolonne in (tk, ak, rk):
            if kolonne is None:
                continue
            # `pg_attribute`, ikke `information_schema.columns`:
            # sistnevnte viser bare kolonner den KALLENDE rollen har
            # rettigheter på, og migrator har ingen på eierens tabeller —
            # porten ville da vært grønn fordi den ikke så noe.
            assert migrator.execute(
                "SELECT 1 FROM pg_attribute a"
                " WHERE a.attrelid = to_regclass('public.'"
                "                                || quote_ident(%s))"
                "   AND a.attname = %s AND a.attnum > 0"
                "   AND NOT a.attisdropped", (relasjon, kolonne)).fetchone(), \
                (lager_id, kolonne)
        if reaper:
            assert migrator.execute(
                "SELECT 1 FROM pg_proc WHERE proname=%s",
                (reaper,)).fetchone(), (lager_id, reaper)
