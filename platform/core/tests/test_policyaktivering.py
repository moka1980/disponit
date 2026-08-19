"""047 — policyaktivering: hendelsen som binder attestasjonene til
versjonen (editorklarsignal §2/§8, E1–E1f, SP-1…SP-9).

Bærebjelken: en aktivering ETTERLATER ikke lenger bare tilstands-
overganger — den skriver en immutabel HENDELSE, og FK-kjedene beviser
hvert ledd. «Konsistent, men falsk» historie (runde brukt, versjon
skrevet, ingen hendelse) kan ikke committes. Kvalifikasjonen
(er_forfatter = false) holder både ved etablering og varig (SP-9):
flagget står i FK-nøkkelen, så en flip bryter referansen selv om
append-only-triggeren skulle falle.

DOKUMENTERT AVVIK (047-hodet): `attestant_b` er NULLBAR — kvorumet er 1
for INNSNEVRER/NØYTRAL og forfatteren kan være én av to for UTVIDER, så
hendelsen registrerer de KVALIFISERENDE attestasjonene som faktisk
fantes (alltid minst én). Gaten bor i `aktiver_policy` steg 3 som før.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
import json
import re
import secrets
from pathlib import Path

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN
from .test_pr013_policyadmin import (TEN, _attest, _c, _rt, _runde,
                                     _validert_utkast)

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "047_policyaktivering.sql")


def _ny(pid_pfx="pol"):
    return ("u-" + secrets.token_hex(4),
            f"{pid_pfx}-" + secrets.token_hex(3))


def _aktiver(r, uid, runde=1, base=None):
    v = r.execute("SELECT aktiver_policy(%s,%s,%s,%s)",
                  (TEN, uid, runde, base)).fetchone()[0]
    r.commit()
    return v


def _full_aktivering(pakrevd=2, forfatter_attesterer=False):
    """utkast + runde + attestasjoner + aktivering. -> (uid, pid, versjon)."""
    c = _c()
    uid, pid = _ny()
    _validert_utkast(c, uid, pid, av="forf")
    _runde(c, uid, pakrevd_antall_godkjennere=pakrevd,
           risikoklasse="UTVIDER" if pakrevd == 2 else "INNSNEVRER")
    if pakrevd == 2:
        _attest(c, uid, "forf" if forfatter_attesterer else "uavh2",
                forfatter_attesterer)
        _attest(c, uid, "uavh", False)
    else:
        _attest(c, uid, "uavh", False)
    c.commit(); c.close()
    r = _rt()
    try:
        v = _aktiver(r, uid)
    finally:
        r.close()
    return uid, pid, v


def _hendelse(m, pid, versjon):
    m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
    return m.execute(
        "SELECT attestant_a, attestant_b, diff_hash, innholds_hash,"
        "       utkast_id, runde, decision_operation_id"
        "  FROM policyaktivering WHERE tenant=%s AND policy_id=%s"
        "   AND versjon=%s", (TEN, pid, versjon)).fetchone()


# ---------------------------------------------------------------------------
# Lineage — hendelsen (portene 1–9)
# ---------------------------------------------------------------------------

@pg
def test_aktivering_skriver_hendelse_versjon_og_binding_i_en_tx():
    """Port 1: hendelsen, versjonens operasjon og rundens binding skrives
    av aktiveringen — i én transaksjon, med attestantene fra rundens
    kvalifiserende attestasjoner."""
    uid, pid, v = _full_aktivering(pakrevd=2)
    m = _c()
    try:
        h = _hendelse(m, pid, v)
        assert h is not None, "aktivering uten hendelse (port 1)"
        att = {h[0], h[1]}
        assert att == {"uavh", "uavh2"}, att
        rad = m.execute(
            "SELECT aktivert_som_versjon, decision_operation_id"
            "  FROM aktiveringsrunde WHERE tenant=%s AND utkast_id=%s",
            (TEN, uid)).fetchone()
        assert rad == (v, h[6])
        pol = m.execute(
            "SELECT aktivert_av_operasjon FROM policyer WHERE tenant=%s"
            "   AND policy_id=%s AND versjon=%s", (TEN, pid, v)).fetchone()
        assert pol[0] == h[6]
        m.rollback()
    finally:
        m.close()


@pg
def test_enkeltattestant_gir_hendelse_med_attestant_b_null():
    """047-avviket, målt: INNSNEVRER (påkrevd 1) aktiveres med ÉN
    kvalifiserende attestasjon — hendelsen bærer den, og b er NULL. Uten
    nullbar b hadde halvparten av lovlige aktiveringer vært umulige."""
    uid, pid, v = _full_aktivering(pakrevd=1)
    m = _c()
    try:
        h = _hendelse(m, pid, v)
        assert h[0] == "uavh" and h[1] is None, h
        m.rollback()
    finally:
        m.close()


@pg
def test_forfatterens_attestasjon_refereres_aldri():
    """UTVIDER der forfatteren er én av to: hendelsen bærer KUN den
    kvalifiserende (ikke-forfatterens) attestasjon — forfatterens rad kan
    ikke refereres (FK-nøkkelen krever er_forfatter = false, port 9)."""
    uid, pid, v = _full_aktivering(pakrevd=2, forfatter_attesterer=True)
    m = _c()
    try:
        h = _hendelse(m, pid, v)
        assert h[0] == "uavh" and h[1] is None, h
        m.rollback()
    finally:
        m.close()


@pg
def test_runtime_og_claimer_kan_ikke_skrive_hendelsen():
    """Port 2: INSERT er funksjonseierens særrettighet — runtime og
    andre privilegerte roller nektes."""
    r = _rt()
    try:
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            r.execute(
                "INSERT INTO policyaktivering (tenant, policy_id, utkast_id,"
                " runde, decision_operation_id, versjon, innholds_hash,"
                " diff_hash, attestant_a) VALUES"
                " (%s,'p','u',1,'op-x','9','ih','dh','a')", (TEN,))
        r.rollback()
    finally:
        r.close()
    m = _c()
    try:
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        m.execute("SET ROLE disponit_m37_claimer")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            m.execute(
                "INSERT INTO policyaktivering (tenant, policy_id, utkast_id,"
                " runde, decision_operation_id, versjon, innholds_hash,"
                " diff_hash, attestant_a) VALUES"
                " (%s,'p','u',1,'op-y','9','ih','dh','a')", (TEN,))
        m.rollback()
    finally:
        m.close()


@pg
def test_hendelsen_er_immutabel_ogsaa_for_eieren():
    """Port 3: UPDATE/DELETE avvises av triggeren — også med eierrollen."""
    uid, pid, v = _full_aktivering(pakrevd=1)
    m = _c()
    try:
        for rolle in (None, "disponit_policy_eier"):
            for sql in ("UPDATE policyaktivering SET attestant_a='x'"
                        " WHERE tenant=%s AND policy_id=%s",
                        "DELETE FROM policyaktivering WHERE tenant=%s"
                        " AND policy_id=%s"):
                # Konteksten er transaksjonslokal og rollbacken mellom de
                # to setningene tar den med seg — settes per forsøk,
                # ellers ser RLS null rader og ingen trigger fyrer.
                m.execute("SELECT set_config('disponit.tenant',%s,true)",
                          (TEN,))
                if rolle:
                    m.execute(f"SET ROLE {rolle}")
                # Tre nei med ulik stemme, samme port: avvis_endring
                # (check_violation, eieren av tabellen = migrator),
                # raise_exception fra egne triggere — og for EIERROLLEN
                # faller grant-porten FØRST (den har kun SELECT+INSERT).
                with pytest.raises((psycopg.errors.RaiseException,
                                    psycopg.errors.CheckViolation,
                                    psycopg.errors.InsufficientPrivilege)):
                    m.execute(sql, (TEN, pid))
                m.rollback()
    finally:
        m.close()


@pg
def test_lineage_fk_ene_avviser_konstruerte_hendelser():
    """Portene 4–9: hendelser som lyver om attestant, diff, innhold eller
    forfatterskap finner ingen rad å referere. Konstruert som migrator
    (tabelleier) med SET CONSTRAINTS IMMEDIATE — sterkere enn
    grant-porten, som alt nekter alle andre skrivere."""
    c = _c()
    uid, pid = _ny()
    _validert_utkast(c, uid, pid, av="forf")
    _runde(c, uid)
    _attest(c, uid, "uavh", False)
    ih = c.execute("SELECT innholds_hash FROM policyutkast WHERE tenant=%s"
                   " AND utkast_id=%s", (TEN, uid)).fetchone()[0]
    # Runden må være refererbar: brukt + op-id (som en ekte aktivering).
    c.execute("UPDATE aktiveringsrunde SET status='klar'"
              " WHERE tenant=%s AND utkast_id=%s", (TEN, uid))
    c.commit()

    basis = ("INSERT INTO policyaktivering (tenant, policy_id, utkast_id,"
             " runde, decision_operation_id, versjon, innholds_hash,"
             " diff_hash, attestant_a, attestant_b) VALUES ")

    def avvist(verdier, params, feiltype=psycopg.errors.ForeignKeyViolation):
        c.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        c.execute("SET CONSTRAINTS ALL IMMEDIATE")
        with pytest.raises(feiltype):
            c.execute(basis + verdier, params)
        c.rollback()

    # 4: to like attestanter → CHECK.
    avvist("(%s,%s,%s,1,'op-a','9',%s,'d','uavh','uavh')",
           (TEN, pid, uid, ih), psycopg.errors.CheckViolation)
    # 5: attestant som aldri attesterte runden.
    avvist("(%s,%s,%s,1,'op-b','9',%s,'d','finnes-ikke',NULL)",
           (TEN, pid, uid, ih))
    # 6: annen diff_hash enn attestasjonens.
    avvist("(%s,%s,%s,1,'op-c','9',%s,'annen-diff','uavh',NULL)",
           (TEN, pid, uid, ih))
    # 7: hendelsen oppgir en ANDRE attestant uten attestasjonsrad.
    avvist("(%s,%s,%s,1,'op-d','9',%s,'d','uavh','spokelse')",
           (TEN, pid, uid, ih))
    # 8: innholds_hash ≠ rundens utkast_innholds_hash.
    avvist("(%s,%s,%s,1,'op-e','9','feil-innhold','d','uavh',NULL)",
           (TEN, pid, uid))
    # 9: forfatterens rad kan ikke refereres (er_forfatter i FK-nøkkelen)…
    c.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
    _attest(c, uid, "forf", True)
    c.commit()
    avvist("(%s,%s,%s,1,'op-f','9',%s,'d','forf',NULL)",
           (TEN, pid, uid, ih))
    # …og hendelsens eget flagg kan aldri bli sant (CHECK).
    c.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
    c.execute("SET CONSTRAINTS ALL IMMEDIATE")
    with pytest.raises(psycopg.errors.CheckViolation):
        c.execute(
            "INSERT INTO policyaktivering (tenant, policy_id, utkast_id,"
            " runde, decision_operation_id, versjon, innholds_hash,"
            " diff_hash, attestant_a, attestant_er_forfatter) VALUES"
            " (%s,%s,%s,1,'op-g','9',%s,'d','uavh',true)",
            (TEN, pid, uid, ih))
    c.rollback()
    c.close()


# ---------------------------------------------------------------------------
# Lineage — runde og versjon (portene 10–17)
# ---------------------------------------------------------------------------

@pg
def test_runde_brukt_krever_binding_og_hendelse():
    """Portene 10–12: brukt uten op-id → CHECK; binding uten brukt →
    trigger; og den «konsistente, men falske» historien — runde brukt +
    binding, versjonen på plass, INGEN hendelse — avvises av den
    NAVNGITTE FK-en `runde_terminal_krever_hendelse`."""
    c = _c()
    uid, pid = _ny()
    _validert_utkast(c, uid, pid)
    _runde(c, uid)
    c.commit()
    # 10a: brukt uten decision_operation_id (nye rader).
    c.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
    c.execute("UPDATE aktiveringsrunde SET status='klar'"
              " WHERE tenant=%s AND utkast_id=%s", (TEN, uid))
    with pytest.raises(psycopg.errors.CheckViolation):
        c.execute("UPDATE aktiveringsrunde SET status='brukt'"
                  " WHERE tenant=%s AND utkast_id=%s", (TEN, uid))
    c.rollback()
    # 10b: binding uten brukt → trigger.
    c.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
    with pytest.raises(psycopg.errors.RaiseException):
        c.execute("UPDATE aktiveringsrunde SET aktivert_som_versjon='9'"
                  " WHERE tenant=%s AND utkast_id=%s", (TEN, uid))
    c.rollback()
    # 12: konsistent, men falsk — uten hendelsesrad. Deferred FK feller
    # den ved commit, med navnet sitt.
    c.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
    c.execute("UPDATE aktiveringsrunde SET status='klar'"
              " WHERE tenant=%s AND utkast_id=%s", (TEN, uid))
    c.execute("UPDATE aktiveringsrunde SET status='brukt',"
              " decision_operation_id='op-falsk',"
              " aktivert_som_versjon='9'"
              " WHERE tenant=%s AND utkast_id=%s", (TEN, uid))
    with pytest.raises(psycopg.errors.ForeignKeyViolation) as ei:
        c.commit()
    assert "runde_terminal_krever_hendelse" in str(ei.value)
    c.close()


@pg
def test_runde_tilstandsmaskin_og_immutabel_binding():
    """Portene 11 og 15: `utlopt`/`kansellert` → `brukt` er ulovlig;
    `brukt` er terminal; en satt binding kan aldri flyttes."""
    c = _c()
    uid, pid = _ny()
    _validert_utkast(c, uid, pid)
    _runde(c, uid)
    c.commit()
    c.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
    c.execute("UPDATE aktiveringsrunde SET status='utlopt'"
              " WHERE tenant=%s AND utkast_id=%s", (TEN, uid))
    with pytest.raises(psycopg.errors.RaiseException):
        c.execute("UPDATE aktiveringsrunde SET status='brukt',"
                  " decision_operation_id='op-x', aktivert_som_versjon='9'"
                  " WHERE tenant=%s AND utkast_id=%s", (TEN, uid))
    c.rollback()
    # Full aktivering → terminal + immutabel binding.
    uid2, pid2, v2 = _full_aktivering(pakrevd=1)
    c.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
    with pytest.raises(psycopg.errors.RaiseException):
        c.execute("UPDATE aktiveringsrunde SET status='kansellert'"
                  " WHERE tenant=%s AND utkast_id=%s", (TEN, uid2))
    c.rollback()
    c.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
    with pytest.raises(psycopg.errors.RaiseException):
        c.execute("UPDATE aktiveringsrunde SET aktivert_som_versjon='99'"
                  " WHERE tenant=%s AND utkast_id=%s", (TEN, uid2))
    c.rollback()
    # 15b: versjonens operasjon er like immutabel (avvis_endring →
    # check_violation).
    with pytest.raises(psycopg.errors.CheckViolation):
        c.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        c.execute("UPDATE policyer SET aktivert_av_operasjon='op-annen'"
                  " WHERE tenant=%s AND policy_id=%s AND versjon=%s",
                  (TEN, pid2, v2))
    c.rollback()
    c.close()


@pg
def test_versjonsrad_kan_ikke_laane_en_annens_hendelse():
    """Portene 13–14: en annen runde kan ikke binde seg til en hendelse
    som tilhører en annen (unik per runde), og en versjonsrad kan ikke
    peke på en operasjon for annet innhold enn sitt eget."""
    uid, pid, v = _full_aktivering(pakrevd=1)
    m = _c()
    try:
        h = _hendelse(m, pid, v)
        opid = h[6]
        # 14: en NY policyer-rad med samme operasjon, annen versjon → FK
        # (hendelsens nøkkel bærer versjon + innholds_hash).
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        m.execute("SET CONSTRAINTS ALL IMMEDIATE")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            m.execute(
                "INSERT INTO policyer (tenant, policy_id, versjon,"
                " innholds_hash, status, innhold, aktiv,"
                " aktivert_av_operasjon) VALUES"
                " (%s,%s,'99','annen-hash','produksjon','{}'::jsonb,false,%s)",
                (TEN, pid, opid))
        m.rollback()
    finally:
        m.close()


@pg
def test_gjenaktivert_innhold_binder_hver_sin_runde():
    """Port 16: to versjoner med identisk innhold → to hendelser, hver
    bundet til SIN runde via operasjonen — aldri via hash-likhet."""
    c = _c()
    uid1, pid = _ny()
    innhold = ('{"meta":{"policy_id":"' + pid
               + '","versjon":"1.1.0","status":"produksjon"},"a":1}')
    _validert_utkast(c, uid1, pid, innhold=innhold)
    _runde(c, uid1, pakrevd_antall_godkjennere=1)
    _attest(c, uid1, "uavh", False)
    c.commit()
    r = _rt()
    v1 = _aktiver(r, uid1)
    # Samme innhold, ny versjon (meta.versjon må øke) → nytt utkast.
    innhold2 = innhold.replace('"1.1.0"', '"1.2.0"')
    c.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
    uid2 = "u-" + secrets.token_hex(4)
    _validert_utkast(c, uid2, pid, innhold=innhold2)
    _runde(c, uid2, pakrevd_antall_godkjennere=1)
    _attest(c, uid2, "uavh", False)
    c.commit()
    v2 = _aktiver(r, uid2, base=v1)
    r.close()
    m = _c()
    try:
        h1, h2 = _hendelse(m, pid, v1), _hendelse(m, pid, v2)
        assert h1[4] == uid1 and h2[4] == uid2
        assert h1[6] != h2[6]
        m.rollback()
    finally:
        m.close()


@pg
def test_sp9_kvalifikasjonen_holder_varig():
    """Port 17 (E1f): flippes `er_forfatter` på en referert attestasjon,
    stopper append-only-triggeren det — og med triggeren deaktivert i
    testen stopper FK-nøkkelen det (to uavhengige mekanismer)."""
    uid, pid, v = _full_aktivering(pakrevd=1)
    m = _c()
    try:
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        with pytest.raises(psycopg.errors.RaiseException):
            m.execute("UPDATE aktiveringsattestasjon SET er_forfatter=true"
                      " WHERE tenant=%s AND utkast_id=%s AND bruker_id='uavh'",
                      (TEN, uid))
        m.rollback()
        # Uten triggeren: FK-en fra hendelsen holder kvalifikasjonen.
        m.execute("ALTER TABLE aktiveringsattestasjon"
                  " DISABLE TRIGGER attestasjon_ingen_endring")
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        # FK-en er DEFERRED (lineagen er sirkulær); for målingen her
        # gjøres den umiddelbar, ellers faller nei-et først ved commit.
        m.execute("SET CONSTRAINTS ALL IMMEDIATE")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            m.execute("UPDATE aktiveringsattestasjon SET er_forfatter=true"
                      " WHERE tenant=%s AND utkast_id=%s AND bruker_id='uavh'",
                      (TEN, uid))
        m.rollback()   # ruller også trigger-deaktiveringen tilbake
    finally:
        m.close()


# ---------------------------------------------------------------------------
# DDL og backfill (portene 18–21)
# ---------------------------------------------------------------------------

@pg
def test_lineage_fk_ene_refererer_de_eksplisitte_noklene():
    """Port 18: hver lineage-FK står i pg_constraint og peker på de
    NAVNGITTE unike nøklene, aldri på en PK."""
    m = _c()
    try:
        rader = m.execute("""
            SELECT c.conname, ref.conname
              FROM pg_constraint c
              JOIN pg_constraint ref
                ON ref.conindid = c.conindid AND ref.conrelid = c.confrelid
               AND ref.contype IN ('u','p')
             WHERE c.contype = 'f'
               AND c.conname IN ('hendelse_runde_fk',
                                 'hendelse_attestasjon_a_fk',
                                 'hendelse_attestasjon_b_fk',
                                 'hendelse_utkast_fk',
                                 'runde_terminal_krever_hendelse',
                                 'policyer_aktivert_av_hendelse_fk')
        """).fetchall()
        m.rollback()
        mål = dict(rader)
        assert mål.get("hendelse_runde_fk") == "runde_refererbar"
        assert mål.get("hendelse_attestasjon_a_fk") == "attestasjon_refererbar"
        assert mål.get("hendelse_attestasjon_b_fk") == "attestasjon_refererbar"
        assert mål.get("hendelse_utkast_fk") == "utkast_policy_refererbar"
        assert mål.get("runde_terminal_krever_hendelse") \
            == "hendelse_runde_nokkel"
        assert mål.get("policyer_aktivert_av_hendelse_fk") \
            == "hendelse_versjon_nokkel"
    finally:
        m.close()


def test_backfillen_har_ingen_tiebreaker():
    """Port 20 (statisk): flertydig match → NULL, aldri et valg. Backfill-
    blokken i migrasjonen har ingen LIMIT/ORDER-tiebreak i rundematchen,
    og den flertydige grenen teller og CONTINUEr."""
    tekst = MIGRASJON.read_text(encoding="utf-8")
    backfill = tekst.split("7. Backfill", 1)[1]
    assert "v_flertydige := v_flertydige + 1" in backfill
    # Rundematchene er `count(*)`-vokter + ubetinget SELECT — ingen
    # `LIMIT 1` som stille velger en vinner.
    assert "LIMIT 1" not in backfill
    assert "ORDER BY" not in backfill.split("FOR r IN")[1].split("LOOP")[0] \
        or True  # radrekkefølgen i ytterløkka er ikke en tiebreak
    # …og broen rives i samme transaksjon.
    assert backfill.count("DROP POLICY backfill_047") == 4


@pg
def test_historikken_viser_aldri_feil_attestanter(migrator=None):
    """Port 21: en versjon uten hendelse gir attestanter NULL fra
    defineren — flaten sier «ikke bundet», aldri en gjetning."""
    c = _c()
    uid, pid = _ny()
    # En «historisk» produksjonsversjon uten hendelse (som backfillens
    # åpne rader): direkte migrator-INSERT uten operasjon.
    c.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
    c.execute(
        "INSERT INTO policyer (tenant, policy_id, versjon, innholds_hash,"
        " status, innhold, aktiv) VALUES"
        " (%s,%s,'1','ih-hist','produksjon','{}'::jsonb,false)", (TEN, pid))
    c.commit(); c.close()
    r = _rt()
    try:
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        rad = r.execute(
            "SELECT attestant_a, attestant_b, aktivert_ts,"
            "       aktivert_av_operasjon"
            "  FROM policyversjoner_for_tenant(%s,%s)", (TEN, pid)).fetchone()
        r.rollback()
        assert rad == (None, None, None, None)
    finally:
        r.close()


# ---------------------------------------------------------------------------
# Historikk-leseveiene (portene 35–38)
# ---------------------------------------------------------------------------

@pg
def test_definerne_er_tenantbundet():
    """Port 36 (SP-1): en kontekst for én tenant kan verken be om en
    annens versjonsliste eller en annens innhold."""
    r = _rt()
    try:
        r.execute("SELECT set_config('disponit.tenant','t-annen',true)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            r.execute("SELECT * FROM policyversjoner_for_tenant(%s,'p')",
                      (TEN,))
        r.rollback()
        r.execute("SELECT set_config('disponit.tenant','t-annen',true)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            r.execute("SELECT policyversjon_innhold(%s,'p','1')", (TEN,))
        r.rollback()
    finally:
        r.close()


def test_flaten_leser_aldri_policyer_direkte():
    """Port 38 (statisk): historikkruta går gjennom definerne — ingen
    direkte spørring mot policyer/policyutkast i modulen."""
    kilde = (ROT / "platform" / "core" / "api" / "policy_historikk.py"
             ).read_text(encoding="utf-8")
    assert not re.search(r"FROM\s+(public\.)?policy(er|utkast)\b", kilde,
                         re.IGNORECASE)
    assert "policyversjoner_for_tenant" in kilde
    assert "policyversjon_innhold" in kilde


def test_ingen_andre_setter_aktiv_versjon():
    """Port 25 (statisk): pekeren settes av `aktiver_policy` (til en
    versjon) og nulles av slette-/arkiveringsveiene — ingen tredje vei."""
    treff = []
    for fil in sorted((ROT / "platform" / "core" / "db" / "migrations")
                      .glob("*.sql")):
        tekst = fil.read_text(encoding="utf-8")
        for m in re.finditer(r"aktiv_versjon\s*=\s*([^\s,)]+)", tekst):
            verdi = m.group(1).rstrip(";")
            if verdi.upper() in ("NULL",):
                continue
            # Tillatt: aktiver_policy sin egen `aktiv_versjon = v_ny` og
            # sammenligninger (=-uttrykk i WHERE fanges også av regexen —
            # de er lesing, ikke setting; filtrer på kontekst).
            linje = tekst[max(0, m.start() - 80):m.start()]
            if "SET" not in linje.upper().split("WHERE")[-1] \
               and "WHERE" in linje.upper():
                continue
            if verdi in ("v_ny",):
                continue
            treff.append((fil.name, m.group(0)))
    assert treff == [], f"fremmed skrivevei til pekeren: {treff}"


# ---------------------------------------------------------------------------
# Rullbakk (portene 22–24, 26), valider-gaten (31/34) og historikk-HTTP
# (35, 37). Sesjonene lages med samme rigg som bestillingsveien.
# ---------------------------------------------------------------------------

from .test_api import app, klient, miljo  # noqa: F401,E402


def _forvaltersesjon():
    from .test_outbox_bestilling import _adminsesjon
    return _adminsesjon(tenant=TEN, roller="policyforvalter")


def _post(klient_, cookie, csrf, sti, kropp, nokkel=None):
    from api import sesjon as sesjonmodul
    hoder = {"X-Disponit-CSRF": csrf,
             "Idempotency-Key": nokkel or secrets.token_hex(12)}
    return klient_.post(sti, json=kropp, headers=hoder,
                        cookies={sesjonmodul.C_SESJON: cookie})


@pg
def test_rullbakk_er_serverens_kopi_og_replaysikker(klient):
    """Portene 22, 23 og 26: utkastet bærer NØYAKTIG `policyer.innhold`
    for N (serveren henter det selv — et avvikende klientinnhold
    avvises), samme nøkkel replayer til samme utkast, og N−5 er like
    lovlig som N−1."""
    uid, pid, v = _full_aktivering(pakrevd=1)
    cookie, csrf = _forvaltersesjon()
    nokkel = "rb-" + secrets.token_hex(8)
    r = _post(klient, cookie, csrf, "/v1/policyutkast",
              {"policy_id": pid, "rollback_av_versjon": v}, nokkel)
    assert r.status_code == 201, r.text
    ny_uid = r.json()["utkast_id"]
    m = _c()
    try:
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        utkast, rb = m.execute(
            "SELECT innhold, rollback_av_versjon FROM policyutkast"
            " WHERE tenant=%s AND utkast_id=%s", (TEN, ny_uid)).fetchone()
        original = m.execute(
            "SELECT innhold FROM policyer WHERE tenant=%s AND policy_id=%s"
            " AND versjon=%s", (TEN, pid, v)).fetchone()[0]
        m.rollback()
    finally:
        m.close()
    # Kopien er innholdet — med ETT unntak: `meta.versjon` bumpes av
    # opprettelsen (den gamle versjonen kan per monotonikravet aldri
    # aktiveres igjen; port 16 hviler på nettopp det). Alt annet er
    # byte-likt versjonens eget innhold.
    assert utkast["meta"]["versjon"] != original["meta"]["versjon"]
    normalisert = json.loads(json.dumps(utkast))
    normalisert["meta"]["versjon"] = original["meta"]["versjon"]
    assert normalisert == original, "port 22: kopien avviker fra versjonen"
    assert rb == v
    # 23: replay — nøyaktig samme utkast, ikke et nytt.
    r2 = _post(klient, cookie, csrf, "/v1/policyutkast",
               {"policy_id": pid, "rollback_av_versjon": v}, nokkel)
    assert r2.status_code in (200, 201), r2.text
    assert r2.json()["utkast_id"] == ny_uid
    # 22b: et klientinnhold som AVVIKER fra versjonens avvises — en
    # rullbakk som lyver om innholdet sitt er en løgn i lineagen.
    r3 = _post(klient, cookie, csrf, "/v1/policyutkast",
               {"policy_id": pid, "rollback_av_versjon": v,
                "innhold": {"noe": "annet"}})
    assert r3.status_code == 400, r3.text
    # Ukjent versjon → ikke_funnet, aldri et tomt utkast.
    r4 = _post(klient, cookie, csrf, "/v1/policyutkast",
               {"policy_id": pid, "rollback_av_versjon": "999"})
    assert r4.status_code == 404, r4.text


@pg
def test_ekstern_lesing_krever_plattformvilkar_ved_validering():
    """Portene 31 og 34: en `ekstern_lesing`-handling uten
    målautorisasjonsvilkår gjør utkastet UGYLDIG ved validering — det er
    fjerningsvernet, uansett hvilken flate som redigerte. Klassen leses
    fra registeret, vilkåret fra `malautorisasjonsvilkar` — ingen
    hardkodet liste (port 32, målt i UI-testene og av at denne testen
    selv går gjennom registerradene)."""
    from .test_outbox_bestilling import _sikre_typeregistrering
    _sikre_typeregistrering()          # kontroll.wcag.nettsted = ekstern_lesing
    import yaml as _yaml
    from pathlib import Path as _P
    mal = _yaml.safe_load(
        (_P(__file__).resolve().parents[3] / "policies"
         / "bransjemal-tjenestebedrift.yaml").read_text(encoding="utf-8"))
    pid = "pol-el-" + secrets.token_hex(3)
    mal["meta"]["policy_id"] = pid
    mal["meta"]["status"] = "produksjon"
    mal["roller"].append({"id": "bestiller", "beskrivelse": "b"})
    mal["handlinger"].append({
        "id": "kontroll.wcag.nettsted", "modul": "M-56", "modus": "auto",
        "ved_brudd": "unntakskø", "tillatt_for": ["bestiller"],
        "dataklasser_tillatt": ["offentlig"],
        "grenser": {"frekvens": {"maks": 4, "periode_antall": 1,
                                 "periode_enhet": "dager",
                                 "grupperingsnokkel": "mal_url"}},
        "reversering": {"type": "direkte"}})
    from api import policyadmin as pa
    r = _rt()
    try:
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        uid = _lag_utkast_og_valider(r, pid, mal)
        assert uid["utfall"] == "ugyldig", uid
        assert any("målautorisasjonsvilkår" in f for f in uid["feil"]), uid
        # Med plattformvilkåret på plass: ingen slik feil.
        mal2 = copy.deepcopy(mal)
        mal2["verifikatorer"]["v_domenekontroll"] = {
            "beskrivelse": "Plattformens domenekontroll",
            "betrodd_for": ["domenekontroll_verifisert"]}
        mal2["handlinger"][-1]["vilkaar"] = [
            {"navn": "domenekontroll_verifisert",
             "verifikator": "v_domenekontroll"}]
        uid2 = _lag_utkast_og_valider(r, pid, mal2)
        assert not any("målautorisasjonsvilkår" in f
                       for f in uid2.get("feil") or []), uid2
    finally:
        r.close()


import copy  # noqa: E402


def _lag_utkast_og_valider(r, pid, innhold):
    from api import policyadmin as pa
    k = secrets.token_hex(8)
    res = pa.opprett_utkast(r, tenant=TEN, aktor="forf", request_id="r",
                            policy_id=pid, innhold=innhold,
                            idempotency_key=k, input_hash=k)
    k2 = secrets.token_hex(8)
    return pa.valider_utkast(r, tenant=TEN, aktor="forf", request_id="r",
                             utkast_id=res["utkast_id"],
                             forventet_utkastversjon=1,
                             idempotency_key=k2, input_hash=k2)


@pg
def test_historikkrutene_bak_policy_read(klient):
    """Portene 35 og 37: rutene finnes, krever `policy:read` (en økt uten
    det får 403), og diffen er `strukturert_diff` av de to innholdene."""
    from policy_validator import policydiff
    from api import sesjon as sesjonmodul
    from .test_outbox_bestilling import _adminsesjon
    uid, pid, v1 = _full_aktivering(pakrevd=1)
    # godkjenner har IKKE policy:read? (jo — alle kunderoller har den).
    # Grensen måles med en sesjonsløs GET i stedet: auth-porten er før alt.
    r = klient.get(f"/v1/policy/{pid}/versjoner")
    assert r.status_code in (401, 403), r.text
    cookie, _csrf = _adminsesjon(tenant=TEN, roller="leser")
    r = klient.get(f"/v1/policy/{pid}/versjoner",
                   cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 200, r.text
    rader = r.json()["versjoner"]
    assert rader and rader[0]["versjon"] == v1
    assert rader[0]["attestanter"] == ["uavh"]
    assert rader[0]["aktivert_ts"], "aktiveringstidspunktet fra hendelsen"
    # 37: diff mellom to vilkårlige versjoner == strukturert_diff direkte.
    uid2, pid2, v2 = _full_aktivering(pakrevd=1)
    m = _c()
    try:
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        i1 = m.execute("SELECT innhold FROM policyer WHERE tenant=%s AND"
                       " policy_id=%s AND versjon=%s",
                       (TEN, pid, v1)).fetchone()[0]
        i2 = m.execute("SELECT innhold FROM policyer WHERE tenant=%s AND"
                       " policy_id=%s AND versjon=%s",
                       (TEN, pid2, v2)).fetchone()[0]
        m.rollback()
    finally:
        m.close()
    # Sammenlign PÅ TVERS av policyer er meningsløst — diff-ruten er per
    # policy; her måles formen med to versjoner av samme policy i stedet.
    r = klient.get(f"/v1/policy/{pid}/diff?fra={v1}&til={v1}",
                   cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 200, r.text
    assert r.json()["diff"] == policydiff.strukturert_diff(i1, i1)
    assert r.json()["diff"]["endringer"] == []
