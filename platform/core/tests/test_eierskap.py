"""FIX-009 runde 2: eierskapsreparasjonen er objektspesifikk.

Testene kjører NØYAKTIG samme SQL-fil som oppsett-postgresql.sh bruker
(`deploy/staging/eierskap-reparasjon.sql`) og beviser BEGGE retninger:

  1. Et designobjekt som er FLATET UT (eies av migrator) repareres tilbake
     til sin designede eier.
  2. Et ORDINÆRT objekt som har havnet hos en privilegert rolle repareres
     til migrator — rollen allowlistes ikke, bare dens designobjekter.
  3. Legitime designobjekter står urørt gjennom en kjøring.

Driftene fabrikkeres med bare migrators egne rettigheter (SET ROLE til
eierrollene + medlemskap), slik at testen kan kjøre overalt suiten kjører
— ingen superbruker.
"""
import os
from pathlib import Path

import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo  # noqa: F401

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

REPARASJON = (Path(__file__).resolve().parents[3]
              / "deploy" / "staging" / "eierskap-reparasjon.sql")

#: Speil av designtabellen i SQL-filen — paritetstesten binder dem sammen.
DESIGN = {
    ("TABLE", "api_tokener"): "disponit_authenticator",
    ("FUNCTION", "verifiser_token(text,text)"): "disponit_authenticator",
    ("TABLE", "arbeidskapabiliteter"): "disponit_m37_claimer",
    ("TABLE", "kvitteringskapabiliteter"): "disponit_m37_claimer",
}


def _kjor_reparasjon(conn):
    """Kjører fil-SQL-en statement for statement i ÉN transaksjon.

    psycopg3s utvidede protokoll tar ett statement per execute; filens
    egen BEGIN/COMMIT erstattes av testens transaksjon (rollback-vernet i
    filen måles der den kjøres i drift — via psql i oppsettskriptet).
    """
    sql = REPARASJON.read_text(encoding="utf-8")
    sql = sql.replace("BEGIN;", "", 1)
    sql = sql.replace("\nCOMMIT;", "", 1)
    forspill, do_del = sql.split("DO $$", 1)
    for stmt in forspill.split(";"):
        if stmt.strip():
            conn.execute(stmt)
    conn.execute("DO $$" + do_del)
    conn.commit()


def _eier(conn, art, ident):
    if art == "TABLE":
        rad = conn.execute(
            "SELECT pg_get_userbyid(relowner) FROM pg_class"
            " WHERE oid = to_regclass('public.' || %s)", (ident,)).fetchone()
    else:
        rad = conn.execute(
            "SELECT pg_get_userbyid(proowner) FROM pg_proc"
            " WHERE oid = to_regprocedure('public.' || %s)",
            (ident,)).fetchone()
    return rad[0] if rad else None


def _design_fra_sql():
    """Designtabellen slik SQL-filen faktisk deklarerer den."""
    import re
    tekst = REPARASJON.read_text(encoding="utf-8")
    blokk = tekst.split("INSERT INTO _design VALUES", 1)[1].split(";", 1)[0]
    return {(art, ident): eier for art, ident, eier in re.findall(
        r"\('(TABLE|FUNCTION)',\s*'([^']+)',\s*'([^']+)'\)", blokk)}


def test_designtabellen_speiler_migrasjonene():
    """Paritet: SQL-filens design == det migrasjonene faktisk oppretter.

    Python-speilet over er bare et utdrag; fasiten er filens egen tabell,
    og denne testen binder formen (hver rad parsbar, kjente eierroller,
    Python-utdraget konsistent)."""
    design = _design_fra_sql()
    assert len(design) >= 23, "designtabellen har mistet rader"
    assert set(design.values()) == {"disponit_authenticator",
                                    "disponit_policy_eier",
                                    "disponit_m37_claimer"}
    for nokkel, eier in DESIGN.items():
        assert design.get(nokkel) == eier, f"utdraget spriker: {nokkel}"


@pg
def test_designtabellen_dekker_alle_privilegert_eide_objekter(migrator):
    """Motsatt paritet: ALT som faktisk eies av auth/claimer i en migrert
    base står i designtabellen — en ny SET ROLE-skapt funksjon i en
    fremtidig migrasjon kan ikke bli stille udekket."""
    design = _design_fra_sql()
    faktisk = migrator.execute(
        "SELECT 'TABLE', c.oid::regclass::text, pg_get_userbyid(c.relowner)"
        "  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname='public' AND c.relkind IN ('r','p')"
        "   AND pg_get_userbyid(c.relowner) IN"
        "       ('disponit_authenticator','disponit_m37_claimer','disponit_policy_eier')"
        " UNION ALL"
        " SELECT 'FUNCTION', p.oid::regprocedure::text,"
        "        pg_get_userbyid(p.proowner)"
        "  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname='public'"
        "   AND pg_get_userbyid(p.proowner) IN"
        "       ('disponit_authenticator','disponit_m37_claimer','disponit_policy_eier')"
    ).fetchall()
    migrator.rollback()
    udekket = [(a, i) for a, i, e in faktisk if (a, i) not in design]
    assert udekket == [], f"privilegert eide objekter utenfor designet: {udekket}"


@pg
def test_flatet_designobjekt_repareres_tilbake(migrator):
    """Retning 1: designobjekt hos migrator -> designeier.

    Flatingen fabrikkeres ærlig: funksjonen droppes (som eieren) og
    gjenskapes VERBATIM av migrator — nøyaktig tilstanden den gamle
    reparasjonen etterlot. ACL-en noteres og gjenopprettes, så testen ikke
    etterlater en annen base enn den fant.
    """
    ident = "tenanter_uten_policysnapshot()"
    definisjon = migrator.execute(
        "SELECT pg_get_functiondef(to_regprocedure('public.' || %s))",
        (ident,)).fetchone()[0]
    acl_for = migrator.execute(
        "SELECT proacl FROM pg_proc WHERE oid = to_regprocedure(%s)",
        (ident,)).fetchone()[0]
    # Robust mot etterlatt drift: dropp som DEN FAKTISKE eieren, uansett
    # hvem det er (en tidligere avbrutt kjøring kan ha etterlatt den hos
    # migrator — nettopp tilstanden denne testen fabrikkerer).
    naa_eier = _eier(migrator, "FUNCTION", ident)
    if naa_eier != "disponit_migrator":
        migrator.execute(f"SET ROLE {naa_eier}")
    migrator.execute(f"DROP FUNCTION {ident}")
    migrator.execute("RESET ROLE")
    migrator.execute(definisjon)          # gjenskapt som MIGRATOR — flatet
    migrator.commit()
    assert _eier(migrator, "FUNCTION", ident) == "disponit_migrator"

    try:
        _kjor_reparasjon(migrator)
        assert _eier(migrator, "FUNCTION", ident) == "disponit_m37_claimer", \
            "reparasjonen skal føre designobjektet HJEM, ikke til migrator"
    finally:
        # Gjenopprett ACL-en eksakt (gjenskapingen nullstilte den) — også
        # når assertion over feiler, så testen aldri etterlater en annen
        # base enn den fant.
        migrator.rollback()
        eier = _eier(migrator, "FUNCTION", ident)
        if eier != "disponit_migrator":
            migrator.execute(f"SET ROLE {eier}")
        migrator.execute(f"REVOKE ALL ON FUNCTION {ident} FROM PUBLIC")
        if acl_for:
            for grantee in {a.split("=")[0] for a in acl_for if "=" in a and
                            a.split("=")[0] and "X" in a.split("=")[1]}:
                migrator.execute(
                    f"GRANT EXECUTE ON FUNCTION {ident} TO {grantee}")
        migrator.execute("RESET ROLE")
        migrator.commit()


@pg
def test_feilplassert_objekt_hos_privilegert_rolle_repareres(migrator):
    """Retning 2 — Codex' P1: et ordinært objekt hos en privilegert rolle
    allowlistes IKKE. To utfall, begge målt:

    - Hos authenticator (migrator arver eierskapet): objektet REPARERES
      til migrator.
    - Hos m37_claimer (SET-only-medlemskap — migrator kan ikke ta det):
      kjøringen AVVISER hardt, og transaksjonsvernet ruller ALT tilbake —
      «reparer eller avvis», aldri stille bevaring og aldri halvgjort.
      (Superbruker-veien, som faktisk reparerer claimer-strøgods, er
      oppsettskriptets — målt på staging, ikke her.)
    """
    migrator.execute("CREATE TABLE IF NOT EXISTS feilplassert_hos_auth (id INT)")
    migrator.execute(
        "ALTER TABLE feilplassert_hos_auth OWNER TO disponit_authenticator")
    migrator.commit()
    try:
        _kjor_reparasjon(migrator)
        assert _eier(migrator, "TABLE", "feilplassert_hos_auth") \
            == "disponit_migrator", "auth-strøgods skal repareres"
    finally:
        migrator.rollback()
        migrator.execute("DROP TABLE IF EXISTS feilplassert_hos_auth")
        migrator.commit()

    migrator.execute("CREATE TABLE IF NOT EXISTS feilplassert_hos_auth (id INT)")
    migrator.execute(
        "ALTER TABLE feilplassert_hos_auth OWNER TO disponit_authenticator")
    migrator.execute("CREATE TABLE IF NOT EXISTS feilplassert_hos_m37 (id INT)")
    migrator.execute(
        "ALTER TABLE feilplassert_hos_m37 OWNER TO disponit_m37_claimer")
    migrator.commit()
    try:
        import psycopg
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _kjor_reparasjon(migrator)
        migrator.rollback()
        assert _eier(migrator, "TABLE", "feilplassert_hos_m37") \
            == "disponit_m37_claimer", "avvisning skal la strøgodset stå"
        assert _eier(migrator, "TABLE", "feilplassert_hos_auth") \
            == "disponit_authenticator", \
            "avvisningen skal rulle tilbake ALT — ingen delreparasjon"
    finally:
        migrator.rollback()
        migrator.execute("DROP TABLE IF EXISTS feilplassert_hos_auth")
        migrator.execute("SET ROLE disponit_m37_claimer")
        migrator.execute("DROP TABLE IF EXISTS feilplassert_hos_m37")
        migrator.execute("RESET ROLE")
        migrator.commit()


@pg
def test_legitime_designobjekter_star_urort(migrator):
    """Retning 3: en kjøring uten drift endrer INGENTING — og etterlater
    hvert designobjekt hos sin designede eier."""
    design = _design_fra_sql()
    def _alle():
        return {(a, i): _eier(migrator, a, i) for (a, i) in design}
    for_kjoring = _alle()
    _kjor_reparasjon(migrator)
    etter = _alle()
    assert for_kjoring == etter, "reparasjonen rørte noe uten drift"
    for nokkel, eier in design.items():
        if etter[nokkel] is not None:
            assert etter[nokkel] == eier, f"{nokkel} hos {etter[nokkel]}"
