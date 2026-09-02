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

#: DE PRIVILEGERTE EIERROLLENE — én kilde, brukt tre steder i denne
#: filen. Sto listen skrevet ut hver gang (og det gjorde den til 1/9),
#: måtte hver ny modul redigere de samme tre stedene, og fem parallelle
#: modul-PR-er ville kollidert i alle tre. Klyngen «orden i eget hus»
#: (092-096) la sine fem eiere inn her, i fundamentet, én gang.
#:
#: En rolle står i listen fordi den er DESIGNET til å eie objekter —
#: ikke fordi den eier noen ennå. En modul som ikke har landet eier
#: ingenting, og audit-testen under er derfor grønn for den uansett.
KJENTE_EIERROLLER = (
    "disponit_authenticator",
    "disponit_m37_claimer",
    "disponit_policy_eier",
    "disponit_modul_eier",
    "disponit_domene_eier",
    # Klyngen «orden i eget hus» — én eier per modul (#326/#327).
    "disponit_kvalitet_eier",   # M-3, migrasjon 092
    "disponit_lager_eier",      # M-4, migrasjon 093
    "disponit_mal_eier",        # M-5, migrasjon 094
    "disponit_kunnskap_eier",   # M-9, migrasjon 095
    "disponit_plikt_eier",      # M-21, migrasjon 096
    # Klynge 2 «tilgang, lisens og etterlevelse» (#klynge2).
    "disponit_tilgang_eier",    # M-12, migrasjon 097
    "disponit_lisens_eier",     # M-22, migrasjon 098
    "disponit_personvern_eier", # M-30, migrasjon 099
    "disponit_compliance_eier", # M-34, migrasjon 100
    # Klynge 3 «kundens livsløp og pengene».
    "disponit_avstemming_eier", # M-13, migrasjon 101
    "disponit_kundeservice_eier", # M-17, migrasjon 102
    "disponit_onboarding_eier", # M-18, migrasjon 103
    "disponit_fordring_eier",   # M-23, migrasjon 104
    "disponit_leverandor_eier", # M-24, migrasjon 105
    # Klynge 4 «det bransjemalene alt har lovet» (106-110).
    #
    # M-27s eier heter `disponit_beholdning_eier` og IKKE
    # `disponit_lager_eier`: det navnet står alt over, som M-4s. To
    # moduler som deler eierrolle er den fullmaktsdelingen «én rolle per
    # modul» finnes for å hindre — og porten under gjør kollisjonen til
    # en rød test framfor noe man må se.
    "disponit_faktura_eier",    # M-14, migrasjon 106
    "disponit_prosjekt_eier",   # M-25, migrasjon 107
    "disponit_prisbok_eier",    # M-26, migrasjon 108
    "disponit_beholdning_eier", # M-27, migrasjon 109
    "disponit_kontovakt_eier",  # M-42, migrasjon 110
)

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


def _rolleliste() -> str:
    """KJENTE_EIERROLLER som en SQL-literalliste. Skrevet som funksjon og
    ikke som f-streng i spørringen, så listen har ETT sted å vokse."""
    return ",".join("'" + r + "'" for r in KJENTE_EIERROLLER)


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
    # ... og INGEN rad er kuttet bort av parsingen. Både denne testen og
    # `_kjor_reparasjon` deler filen på setningsskilletegnet, så ett eneste
    # semikolon i en KOMMENTAR inne i VALUES-listen halverer designtabellen
    # — stille, med en fortsatt «grønn nok» radtelling. Fasiten er hvor
    # mange rader filen faktisk deklarerer.
    import re
    alle = len(re.findall(r"\('(?:TABLE|FUNCTION)',",
                          REPARASJON.read_text(encoding="utf-8")))
    assert len(design) == alle, (
        f"parsingen ser {len(design)} av {alle} deklarerte rader — et"
        " semikolon i en kommentar kutter VALUES-listen")
    # DELMENGDE, ikke likhet. Likheten fanget to ting: en ukjent eier i
    # designfilen (den PORTEN beholder vi — det er den som betyr noe),
    # og en rolle i listen uten en eneste designrad (den mister vi).
    # Byttet er bevisst: en modul som ennå ikke har landet har ingen
    # rader, og en likhetstest ville da tvunget hver modul-PR til å
    # redigere den samme litteralen — nøyaktig kollisjonen fundamentet
    # finnes for å unngå. At en designert eierrolle faktisk eier det den
    # skal, måles av `test_ingen_privilegert_eid_utenfor_designet` under,
    # som spør BASEN og ikke denne listen.
    ukjente = set(design.values()) - set(KJENTE_EIERROLLER)
    assert not ukjente, (
        f"designfilen navngir eierroller utenfor KJENTE_EIERROLLER:"
        f" {sorted(ukjente)} — legg rollen i konstanten øverst, eller"
        " rett raden")
    for nokkel, eier in DESIGN.items():
        assert design.get(nokkel) == eier, f"utdraget spriker: {nokkel}"
    # …og hver signatur er skrevet slik `regprocedure` skriver den.
    # Postgres kjenner igjen `timestamptz` i en DDL, men gjengir den
    # ALDRI: identiteten som kommer ut av basen heter «timestamp with
    # time zone». En rad med aliaset matcher derfor ingenting, og
    # reparasjonen lar objektet stå — det var bare paritetstesten mot en
    # migrert base som kunne se det, og den krever Postgres. Her ses det
    # uten base, der signaturen faktisk skrives.
    ALIAS = {"timestamptz": "timestamp with time zone",
             "timetz": "time with time zone", "int2": "smallint",
             "int4": "integer", "int8": "bigint", "bool": "boolean",
             "varchar": "character varying", "bpchar": "character",
             "float4": "real", "float8": "double precision",
             "decimal": "numeric"}
    for art, ident in design:
        argdel = ident.split("(", 1)[1].rstrip(")") if "(" in ident else ""
        for arg in (a.strip() for a in argdel.split(",")):
            assert arg not in ALIAS, (
                f"{ident}: {arg!r} er et alias — regprocedure skriver"
                f" {ALIAS.get(arg)!r}, så raden matcher ingen funksjon")


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
        "       (" + _rolleliste() + ")"
        " UNION ALL"
        " SELECT 'FUNCTION', p.oid::regprocedure::text,"
        "        pg_get_userbyid(p.proowner)"
        "  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname='public'"
        "   AND pg_get_userbyid(p.proowner) IN"
        "       (" + _rolleliste() + ")"
    ).fetchall()
    migrator.rollback()
    udekket = [(a, i) for a, i, e in faktisk if (a, i) not in design]
    assert udekket == [], f"privilegert eide objekter utenfor designet: {udekket}"


@pg
def test_gammel_claim_signatur_beholdes_hos_claimer(migrator):
    """Codex P1: reparasjonen kjører FØR migrer.py (oppsett-postgresql.sh).

    En base som ennå ikke har kjørt 015 har den GAMLE 4-args
    `claim_neste_oppdrag` installert og eid av m37_claimer. Står ikke den
    signaturen i designtabellen, klassifiserer steg 2 den som strøgods og
    flytter den til migrator — og 015, som dropper den under
    `SET LOCAL ROLE disponit_m37_claimer`, feiler på manglende eierskap
    (medlemskapet er `WITH INHERIT FALSE`, så migrator kan ikke droppe den
    på claimers vegne heller). Hele oppgraderingen fra 005 stopper.

    Tilstanden fabrikkeres ærlig: den gamle signaturen gjenskapes som
    m37_claimer (015 har droppet den i denne basen), reparasjonen kjøres,
    og eierskapet skal stå urørt.
    """
    gammel = "claim_neste_oppdrag(text,text[],text,integer)"
    assert _design_fra_sql().get(("FUNCTION", gammel)) \
        == "disponit_m37_claimer", "den gamle signaturen mangler i designet"
    migrator.execute("SET ROLE disponit_m37_claimer")
    migrator.execute("CREATE FUNCTION claim_neste_oppdrag(TEXT, TEXT[], TEXT,"
                     " INT) RETURNS VOID LANGUAGE plpgsql AS 'BEGIN RETURN;"
                     " END'")
    migrator.execute("RESET ROLE")
    migrator.commit()
    try:
        assert _eier(migrator, "FUNCTION", gammel) == "disponit_m37_claimer"
        _kjor_reparasjon(migrator)
        assert _eier(migrator, "FUNCTION", gammel) == "disponit_m37_claimer", \
            "reparasjonen strandet den gamle signaturen — 015 kan ikke droppe den"
    finally:
        # Ryddes ALLTID: med begge signaturene installert er et 4-args-kall
        # tvetydig (de tre nye parameterne har DEFAULT), og hver annen test
        # som claimer ville feilet.
        migrator.rollback()
        eier = _eier(migrator, "FUNCTION", gammel)
        if eier and eier != "disponit_migrator":
            migrator.execute(f"SET ROLE {eier}")
        migrator.execute("DROP FUNCTION IF EXISTS claim_neste_oppdrag(TEXT,"
                         " TEXT[], TEXT, INT)")
        migrator.execute("RESET ROLE")
        migrator.commit()


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


def test_ingen_eierrolle_er_ført_to_ganger():
    """ÉN ROLLE PER MODUL, målt på listen selv.

    Porten finnes fordi det skjedde: klynge 4-fundamentet ga først M-27
    navnet `disponit_lager_eier`, som er M-4s (migrasjon 093 og 099).
    To moduler ville da delt eier, og en feil i den ene ville båret den
    andres fullmakt — nøyaktig fullmaktsdelingen «én rolle per modul»
    finnes for å hindre.

    MUTASJONEN SOM DREPER DENNE: før en rolle opp to ganger.
    """
    assert len(set(KJENTE_EIERROLLER)) == len(KJENTE_EIERROLLER), \
        sorted(r for r in KJENTE_EIERROLLER
               if KJENTE_EIERROLLER.count(r) > 1)


def test_ci_oppretter_hver_rolle_nøyaktig_én_gang():
    """…og den samme dommen målt der den faktisk ble brutt: `ci.yml`.

    `CREATE ROLE` to ganger er rød CI uansett, men den rødheten sier
    «role already exists» og ikke «to moduler deler eier». Porten sier
    det andre, og den ser BÅDE eier- og sveiperollene — også dem som
    ikke står i `KJENTE_EIERROLLER`.
    """
    import re
    from pathlib import Path
    ci = (Path(__file__).resolve().parents[3]
          / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    navn = re.findall(r'CREATE ROLE (disponit_[a-z0-9_]+)', ci)
    assert len(navn) > 20, f"porten fant bare {len(navn)} roller"
    dubletter = sorted({n for n in navn if navn.count(n) > 1})
    assert not dubletter, f"opprettet to ganger i ci.yml: {dubletter}"

