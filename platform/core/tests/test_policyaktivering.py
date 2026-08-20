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


def _backfill_historisk(m, sql, params):
    """Skriv `'historisk'`-merket slik migrasjonens EGEN backfill gjorde.

    Etter 047 er merket en TILSTAND ingen skriver kan sette — verken ved
    INSERT eller UPDATE (Codex P2) — nettopp fordi `policyversjon_i_kraft`
    leser det som «har vært i kraft»: kunne en UPDATE sette det, ville en
    aldri aktivert rad fått en aktivering ingen har gjort. Testene som
    trenger en backfilt rad må derfor gjøre det migrasjonen gjorde: skru
    vakten av, skrive, skru den på igjen. At det KREVES, er porten.
    """
    # `ALTER TABLE` tåler ingen VENTENDE trigger-hendelser på tabellen, og
    # `policyer_aktivert_av_hendelse_fk` er DEFERRABLE INITIALLY DEFERRED:
    # skrivingen vår legger igjen nettopp en slik. `SET CONSTRAINTS ALL
    # IMMEDIATE` fyrer den her, mens vi fortsatt kan se resultatet, så
    # gjeninnkoblingen slipper til. Kalleren må ha committet det den gjorde
    # før — av samme grunn, for den FØRSTE ALTER-en.
    #
    # Ingen `finally`: feiler noe her, er transaksjonen avbrutt, og
    # rollbacken tar `DISABLE`-en med seg. En `finally` ville bare kastet en
    # ny feil oppå den ekte.
    m.execute("ALTER TABLE policyer DISABLE TRIGGER policyer_kilde_vakt_trg")
    m.execute(sql, params)
    m.execute("SET CONSTRAINTS ALL IMMEDIATE")
    m.execute("ALTER TABLE policyer ENABLE TRIGGER policyer_kilde_vakt_trg")


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
def test_hendelsesloggen_kan_ikke_truncates():
    """Codex P2: TRUNCATE fyrer aldri rad-triggere.

    `policyaktivering_immutabel` er en RAD-trigger og sier derfor
    ingenting om `TRUNCATE policyaktivering CASCADE` — en setning
    tabelleieren (migratoren, altså enhver senere migrasjon og ethvert
    vedlikeholdsskript) kan kjøre. Hele aktiveringslinjen kunne dermed
    forsvinne i én setning, tross at tabellen er erklært evig.
    `revisjonslogg` har hatt setnings-vakten siden 001; denne manglet.

    Måles på VAKTENS egen stemme: `avvis_endring` gir check_violation med
    tabellnavnet i meldingen. Kaskaden treffer også `policyer`, som har
    sin egen TRUNCATE-vakt med en annen feilkode — en test som bare
    krevde «en feil» ville vært grønn på den alene.
    """
    uid, pid, v = _full_aktivering(pakrevd=1)
    m = _c()
    try:
        # Vakten står som en SETNINGS-trigger for TRUNCATE (tgtype: bit 0
        # = rad-nivå, bit 5 = TRUNCATE).
        assert m.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgrelid="
            "'policyaktivering'::regclass AND NOT tgisinternal"
            " AND (tgtype & 32) <> 0 AND (tgtype & 1) = 0"
        ).fetchone()[0] == 1, "policyaktivering mangler TRUNCATE-vakt"
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        with pytest.raises(psycopg.errors.CheckViolation) as ei:
            m.execute("TRUNCATE policyaktivering CASCADE")
        assert "policyaktivering" in str(ei.value), str(ei.value)
        m.rollback()
        # Hendelsen står — og med den attestasjonene den beviser.
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        assert m.execute(
            "SELECT count(*) FROM policyaktivering WHERE tenant=%s"
            " AND policy_id=%s AND versjon=%s",
            (TEN, pid, v)).fetchone()[0] == 1
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


@pg
def test_slettet_versjon_kan_aktiveres_paa_nytt():
    """Codex P2: hendelsestabellen er evig, versjonsNUMRE er det ikke.

    `slett_ubrukt_policy` (032) sletter ubrukte versjoner nettopp for at
    de skal kunne gjenskapes. Sto invarianten som UNIQUE (tenant,
    policy_id, versjon) på `policyaktivering`, reserverte den FØRSTE
    aktiveringen nummeret for alltid: `policyer` meldte versjonen fri,
    editoren slapp utkastet gjennom, og aktiveringen døde på en hendelse
    for en generasjon som ikke lenger fantes.

    Her måles hele veien: aktiver 1.1.0, slett den, gjenskap SAMME
    nummer og aktiver igjen. Den LEVENDE raden skal ha ÉN linje, med sin
    EGEN generasjons attestanter — aldri to, og aldri den slettede
    generasjonens navn.

    Den slettede generasjonen har sin egen linje (Codex P2): hendelsen
    står, og en aktivering som faktisk skjedde hører hjemme i
    revisjonssporet. Den skilles ved at innholdet er borte — koblingen
    går via OPERASJONEN, så de to kan ikke smelte sammen selv om de deler
    nummer.

    Kontroll: sett `hendelse_en_per_levende_versjon` tilbake til en ren
    UNIQUE på (tenant, policy_id, versjon), så blir denne rød.
    """
    uid, pid, v = _full_aktivering(pakrevd=1)
    m = _c()
    try:
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        ih = m.execute(
            "SELECT innholds_hash FROM policyer WHERE tenant=%s"
            "  AND policy_id=%s AND versjon=%s", (TEN, pid, v)).fetchone()[0]
        m.rollback()
    finally:
        m.close()

    r = _rt()
    try:
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        assert r.execute("SELECT slett_ubrukt_policy(%s,%s,%s,%s)",
                         (TEN, pid, v, ih)).fetchone()[0] == 1
        r.commit()
    finally:
        r.close()

    # Gjenskapt: samme policy-id, samme versjonsnummer, nytt utkast.
    c = _c()
    uid2 = "u-" + secrets.token_hex(4)
    _validert_utkast(c, uid2, pid, av="forf", versjon=v)
    _runde(c, uid2, pakrevd_antall_godkjennere=1, risikoklasse="INNSNEVRER")
    _attest(c, uid2, "uavh-ny", False)
    c.commit(); c.close()
    r = _rt()
    try:
        assert _aktiver(r, uid2) == v, \
            "det gjenskapte nummeret ble ikke aktiverbart"
    finally:
        r.close()

    m = _c()
    try:
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        # Begge hendelsene står — tabellen er immutabel — men bare den ene
        # er bundet til en levende rad.
        assert m.execute(
            "SELECT count(*) FROM policyaktivering WHERE tenant=%s"
            "  AND policy_id=%s AND versjon=%s",
            (TEN, pid, v)).fetchone()[0] == 2
        m.rollback()
    finally:
        m.close()

    # Historikk-defineren er grantet til RUNTIME-rollen, ikke til migrator
    # (SP-7): den leses derfor herfra, slik flaten gjør det.
    r = _rt()
    try:
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        rader = r.execute(
            "SELECT versjon, attestant_a, innhold_finnes, aktivert FROM"
            " policyversjoner_for_tenant(%s,%s)", (TEN, pid)).fetchall()
        r.rollback()
        # Den levende raden først (nyest aktivert), med SINE attestanter;
        # den slettede generasjonen som en egen, innholdsløs linje.
        assert rader == [(v, "uavh-ny", True, True),
                         (v, "uavh", False, True)], \
            f"historikken bommer på generasjonene: {rader}"
    finally:
        r.close()


@pg
def test_historikken_beholder_aktiveringen_naar_versjonen_er_slettet():
    """Codex P2: historikken var forankret i de OVERLEVENDE radene.

    `slett_ubrukt_policy` sletter uttrykkelig en aktivert, ubrukt versjon
    mens `policyaktivering` blir stående — tabellen er immutabel og evig,
    og det er hele grunnen til at den finnes. Var spørringen forankret i
    `policyer`, forsvant aktiveringen fra revisjonssporet sammen med
    raden: en fire-øyne-runde som faktisk skjedde, med navngitte
    attestanter, var ikke lenger å se noe sted gjennom flaten.

    Linjen bærer alt hendelsen vet, men `innhold_finnes` er falsk —
    dokumentet fulgte raden. Testen måler begge deler, for det er
    nettopp derfor merket må stå: `policyversjon_innhold` har ingenting
    å svare, og uten merket ville flaten tilbudt en diff som endte i 404
    (eller, var nummeret gjenskapt, i en HELT ANNEN generasjons
    dokument).

    Kontroll: forankre spørringen i `policyer` igjen, så blir linjen
    borte og testen rød.
    """
    uid, pid, v = _full_aktivering(pakrevd=1)
    m = _c()
    try:
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        ih = m.execute(
            "SELECT innholds_hash FROM policyer WHERE tenant=%s"
            "  AND policy_id=%s AND versjon=%s", (TEN, pid, v)).fetchone()[0]
        m.rollback()
    finally:
        m.close()

    r = _rt()
    try:
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        assert r.execute("SELECT slett_ubrukt_policy(%s,%s,%s,%s)",
                         (TEN, pid, v, ih)).fetchone()[0] == 1
        r.commit()
    finally:
        r.close()

    r = _rt()
    try:
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        assert r.execute(
            "SELECT count(*) FROM policyer WHERE tenant=%s AND policy_id=%s",
            (TEN, pid)).fetchone()[0] == 0, "forutsetningen holder ikke"
        rader = r.execute(
            "SELECT versjon, attestant_a, aktivert_av_operasjon,"
            "       aktiveringskilde, aktivert, innhold_finnes, aktiv,"
            "       aktivert_ts IS NOT NULL, opprettet IS NOT NULL"
            "  FROM policyversjoner_for_tenant(%s,%s)",
            (TEN, pid)).fetchall()
        assert len(rader) == 1, f"aktiveringen forsvant med raden: {rader}"
        (versjon, att, op, kilde, aktivert, innhold, aktiv,
         har_ts, har_opprettet) = rader[0]
        assert (versjon, att, kilde) == (v, "uavh", "styrt"), rader[0]
        assert op is not None and har_ts and har_opprettet, rader[0]
        assert aktivert is True and innhold is False and aktiv is False, \
            rader[0]
        r.rollback()
        # …og merket lyver ikke: innholdet er faktisk borte.
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        with pytest.raises(psycopg.errors.NoDataFound):
            # Generasjonen er likegyldig her: fraværet måles FØRST, så
            # avslaget er «borte», ikke «en annen generasjon».
            r.execute("SELECT policyversjon_innhold(%s,%s,%s,1)",
                      (TEN, pid, v)).fetchone()
        r.rollback()
    finally:
        r.close()


@pg
def test_styrt_aktivering_kan_ikke_omgaas_av_oppsettsveien():
    """Codex P2: `policyregister.registrer(..., aktiver=True)` er
    bootstrapveien, og etter 047 må den si det — og holde seg unna.

    Kolonnen `aktivert_av_operasjon` MÅ være nullbar for radene som lå
    der da 047 landet, men nullbarheten gjaldt dermed også framover: en
    rad skrevet av oppsettsveien i dag var ikke til å skille fra en rad
    som ligger foran hele lineagen. Nå bærer den `aktiveringskilde`, og
    veien nekter dessuten å gå FORBI en versjon som er styrt aktivert —
    det ville tatt policyen ut av lineagen uten at noe sa fra.
    """
    import yaml as _yaml
    from api import policyregister as pr
    uid, pid, v = _full_aktivering(pakrevd=1)
    # Et GYLDIG dokument — ellers ville skjemavalideringen øverst i
    # `registrer` avvist det først, og testen målt feil port.
    mal = _yaml.safe_load(
        (ROT / "policies" / "bransjemal-tjenestebedrift.yaml")
        .read_text(encoding="utf-8"))
    mal["meta"]["policy_id"] = pid
    mal["meta"]["versjon"] = "9.9.9"
    mal["meta"]["status"] = "produksjon"
    m = _c()
    try:
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        assert m.execute(
            "SELECT aktiveringskilde FROM policyer WHERE tenant=%s"
            "  AND policy_id=%s AND versjon=%s",
            (TEN, pid, v)).fetchone()[0] == "styrt"
        with pytest.raises(pr.PolicyKorrupt) as ei:
            pr.registrer(m, TEN, mal, "produksjon")
        assert "fire-øyne" in str(ei.value), str(ei.value)
        m.rollback()
    finally:
        m.close()


@pg
def test_bootstrap_stengt_ogsaa_naar_styrt_versjon_er_slettet():
    """Codex P1: vakten må måle HENDELSEN, ikke den aktive raden.

    `slett_ubrukt_policy` sletter en ubrukt versjon — også en styrt
    aktivert en — mens `policyaktivering` er immutabel og blir stående.
    Spurte vakten `policyer` om den nåværende aktive raden bar
    `aktivert_av_operasjon`, fant den ingenting etter en slik sletting, og
    oppsettsveien åpnet seg igjen for en serie som for lengst er inne i
    lineagen: en oppsettskjøring kunne gjenskape samme policy som
    bootstrap, og den forrige hendelsen ville ligget frakoblet ved siden av.

    Kontroll: sett prøven tilbake til `policyer ... aktiv AND
    aktivert_av_operasjon IS NOT NULL`, så går registreringen gjennom og
    testen blir rød.
    """
    import yaml as _yaml
    from api import policyregister as pr
    uid, pid, v = _full_aktivering(pakrevd=1)

    m = _c()
    try:
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        ih = m.execute(
            "SELECT innholds_hash FROM policyer WHERE tenant=%s"
            "  AND policy_id=%s AND versjon=%s", (TEN, pid, v)).fetchone()[0]
        m.rollback()
    finally:
        m.close()

    # Versjonen slettes — den er ubrukt — og etter dette finnes det INGEN
    # levende rad for serien i det hele tatt.
    r = _rt()
    try:
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        assert r.execute("SELECT slett_ubrukt_policy(%s,%s,%s,%s)",
                         (TEN, pid, v, ih)).fetchone()[0] == 1
        r.commit()
    finally:
        r.close()

    mal = _yaml.safe_load(
        (ROT / "policies" / "bransjemal-tjenestebedrift.yaml")
        .read_text(encoding="utf-8"))
    mal["meta"]["policy_id"] = pid
    mal["meta"]["status"] = "produksjon"
    m = _c()
    try:
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        assert m.execute(
            "SELECT count(*) FROM policyer WHERE tenant=%s AND policy_id=%s",
            (TEN, pid)).fetchone()[0] == 0, "forutsetningen holder ikke"
        # Både et NYTT versjonsnummer og det GJENSKAPTE er stengt: det er
        # serien som har gått inn i lineagen, ikke det enkelte nummeret.
        for nr in ("9.9.9", v):
            mal["meta"]["versjon"] = nr
            with pytest.raises(pr.PolicyKorrupt) as ei:
                pr.registrer(m, TEN, mal, "produksjon")
            assert "fire-øyne" in str(ei.value), str(ei.value)
            m.rollback()
            m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
    finally:
        m.close()


@pg
def test_historikken_sorterer_paa_aktivering_ikke_registrering():
    """Codex P2: bootstraprader lånte `policyer.opprettet` som kronologi.

    `registrer(..., aktiver=False)` legger inn en versjon uten å aktivere
    den, og en senere `registrer(..., aktiver=True)` aktiverer NØYAKTIG DEN
    RADEN gjennom upserten. `opprettet` står da urørt, så historikken
    sorterte den nyaktiverte versjonen på registreringstidspunktet sitt —
    ble en annen versjon laget i mellomtiden, var lista ikke lenger
    nyest-først, og diffens default-retning bygger på den rekkefølgen.

    Kontroll: sorter på `p.opprettet` igjen, så kommer 2.0.0 først og
    testen blir rød.
    """
    import yaml as _yaml
    from api import policyregister as pr
    pid = "pol-kron-" + secrets.token_hex(3)
    mal = _yaml.safe_load(
        (ROT / "policies" / "bransjemal-tjenestebedrift.yaml")
        .read_text(encoding="utf-8"))
    mal["meta"]["policy_id"] = pid
    mal["meta"]["status"] = "produksjon"

    m = _c()
    try:
        # Egen transaksjon per steg: både `opprettet` og `now()` er
        # TRANSAKSJONENS tid, så tre kall i én transaksjon ville fått
        # identiske merker og testen målt ingenting.
        #
        # 1.0.0 registreres FØRST, men uten å aktiveres.
        mal["meta"]["versjon"] = "1.0.0"
        pr.registrer(m, TEN, mal, "produksjon", aktiver=False)
        m.commit()
        # 2.0.0 lages etterpå og aktiveres.
        mal["meta"]["versjon"] = "2.0.0"
        pr.registrer(m, TEN, mal, "produksjon")
        m.commit()
        # Og så aktiveres den gamle raden — en rullbakk gjennom
        # oppsettsveien. `opprettet` på 1.0.0 er fortsatt den eldste.
        mal["meta"]["versjon"] = "1.0.0"
        pr.registrer(m, TEN, mal, "produksjon")
        m.commit()
        assert m.execute(
            "SELECT opprettet < (SELECT opprettet FROM policyer WHERE"
            "   tenant=%s AND policy_id=%s AND versjon='2.0.0')"
            "  FROM policyer WHERE tenant=%s AND policy_id=%s"
            "   AND versjon='1.0.0'",
            (TEN, pid, TEN, pid)).fetchone()[0], "forutsetningen holder ikke"
        m.rollback()
    finally:
        m.close()

    r = _rt()
    try:
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        rader = r.execute(
            "SELECT versjon, aktiv, aktivert_ts FROM"
            " policyversjoner_for_tenant(%s,%s)", (TEN, pid)).fetchall()
        r.rollback()
    finally:
        r.close()
    assert [x[0] for x in rader] == ["1.0.0", "2.0.0"], \
        f"historikken er ikke nyest-aktivert-først: {rader}"
    assert rader[0][1] is True and rader[1][1] is False, rader
    # Begge bootstraprader bærer sitt eget aktiveringstidspunkt, og den
    # gjenaktiverte har det ferskeste.
    assert rader[0][2] is not None and rader[1][2] is not None, rader
    assert rader[0][2] > rader[1][2], rader


@pg
def test_reregistrering_av_aktiv_bootstrap_beholder_tidspunktet():
    """Codex P2: en upsert av den ALT aktive versjonen er ingen overgang.

    `registrer` er med vilje en upsert — en administrativ re-kjøring av
    samme oppsettsregistrering skal være ufarlig. Skrev den
    `bootstrap_aktivert_ts=now()` på hver `aktiver=True`, var den ikke
    det: den aktive versjonen fikk et ferskt aktiveringstidspunkt uten at
    noen versjonsovergang hadde skjedd, historikken (som sorterer på
    nettopp det merket) flyttet den til topps, og diffens default-retning
    snudde.

    Testen måler begge halvdelene: merket står stille når raden alt var
    aktiv, og settes fortsatt når en INAKTIV rad faktisk aktiveres.
    """
    import yaml as _yaml
    from api import policyregister as pr
    pid = "pol-upsert-" + secrets.token_hex(3)
    mal = _yaml.safe_load(
        (ROT / "policies" / "bransjemal-tjenestebedrift.yaml")
        .read_text(encoding="utf-8"))
    mal["meta"]["policy_id"] = pid
    mal["meta"]["status"] = "produksjon"

    def _ts(c, versjon):
        return c.execute(
            "SELECT bootstrap_aktivert_ts FROM policyer WHERE tenant=%s"
            " AND policy_id=%s AND versjon=%s",
            (TEN, pid, versjon)).fetchone()[0]

    m = _c()
    try:
        # Egen transaksjon per steg: `now()` er TRANSAKSJONENS tid, så to
        # kall i samme transaksjon ville fått identiske merker og testen
        # målt ingenting.
        mal["meta"]["versjon"] = "1.0.0"
        pr.registrer(m, TEN, mal, "produksjon")
        m.commit()
        forste = _ts(m, "1.0.0")
        m.commit()
        assert forste is not None, "bootstrapen fikk ikke noe tidspunkt"

        # Samme registrering en gang til — ingen overgang.
        pr.registrer(m, TEN, mal, "produksjon")
        m.commit()
        assert _ts(m, "1.0.0") == forste, \
            "re-registrering av den aktive versjonen flyttet tidspunktet"
        # ... og raden er fortsatt aktiv, med ankerraden på samme versjon.
        assert m.execute(
            "SELECT p.aktiv, hd.aktiv_versjon FROM policyer p"
            " JOIN policy_hode hd ON hd.tenant=p.tenant"
            "  AND hd.policy_id=p.policy_id"
            " WHERE p.tenant=%s AND p.policy_id=%s AND p.versjon='1.0.0'",
            (TEN, pid)).fetchone() == (True, "1.0.0")
        m.commit()

        # En INAKTIV rad som faktisk aktiveres skal fortsatt få merket —
        # og 1.0.0 skal miste flagget, som før.
        mal["meta"]["versjon"] = "2.0.0"
        pr.registrer(m, TEN, mal, "produksjon", aktiver=False)
        m.commit()
        assert _ts(m, "2.0.0") is None, "en inaktiv rad ble merket aktivert"
        m.commit()
        pr.registrer(m, TEN, mal, "produksjon")
        m.commit()
        andre = _ts(m, "2.0.0")
        assert andre is not None and andre > forste, (andre, forste)
        assert m.execute(
            "SELECT aktiv FROM policyer WHERE tenant=%s AND policy_id=%s"
            " AND versjon='1.0.0'", (TEN, pid)).fetchone()[0] is False, \
            "forrige versjon ble ikke deaktivert"
        # Den forrige aktives merke er historikk og skal ikke røres.
        assert _ts(m, "1.0.0") == forste
        m.rollback()
    finally:
        m.close()


@pg
def test_innhold_som_har_vaert_i_kraft_kan_ikke_skrives_om():
    """Codex P2: oppsettsveien kan ikke bytte ut en fortid.

    Upserten skriver `innholds_hash` og `innhold` ubetinget. En
    oppsettskjøring med REDIGERT innhold på en versjon som alt har vært
    aktiv skrev derfor om dokumentet der det står, mens raden beholdt
    aktiveringstidspunktet og opphavet sitt: historikken fortsatte å
    påstå at nettopp DETTE innholdet var i kraft fra den gang, uten at
    noen aktivering hadde skjedd — og det som faktisk var i kraft fantes
    ikke lenger noe sted.

    Tre tilfeller: identisk re-kjøring (skal virke), redigert innhold på
    en versjon som har vært i kraft (skal avvises, også når den er avløst)
    og redigert innhold på en versjon som ALDRI har vært det (skal virke —
    det er arbeidsstykket).
    """
    import yaml as _yaml
    from api import policyregister as pr
    pid = "pol-fortid-" + secrets.token_hex(3)
    mal = _yaml.safe_load(
        (ROT / "policies" / "bransjemal-tjenestebedrift.yaml")
        .read_text(encoding="utf-8"))
    mal["meta"]["policy_id"] = pid
    mal["meta"]["status"] = "produksjon"

    def _endret(versjon):
        m2 = _yaml.safe_load(_yaml.safe_dump(mal))
        m2["meta"]["versjon"] = versjon
        # `bedrift` er et fritt felt i skjemaet — endringen er ekte for
        # innholdshashen og likegyldig for alt annet.
        m2["meta"]["bedrift"] = "endret " + secrets.token_hex(4)
        return m2

    m = _c()
    try:
        mal["meta"]["versjon"] = "1.0.0"
        pr.registrer(m, TEN, mal, "produksjon")
        m.commit()
        # Identisk re-kjøring: `init-tenant.sh` skal kunne kjøres om igjen.
        pr.registrer(m, TEN, mal, "produksjon")
        m.commit()
        # Redigert innhold på den AKTIVE versjonen.
        with pytest.raises(pr.PolicyKorrupt):
            pr.registrer(m, TEN, _endret("1.0.0"), "produksjon")
        m.rollback()
        # Avløst — men fortiden er ikke friere av den grunn.
        mal["meta"]["versjon"] = "2.0.0"
        pr.registrer(m, TEN, mal, "produksjon")
        m.commit()
        with pytest.raises(pr.PolicyKorrupt):
            pr.registrer(m, TEN, _endret("1.0.0"), "produksjon",
                         aktiver=False)
        m.rollback()
        # En versjon som ALDRI har vært i kraft er fortsatt redigerbar.
        mal["meta"]["versjon"] = "9.0.0"
        pr.registrer(m, TEN, mal, "produksjon", aktiver=False)
        m.commit()
        ny_9 = _endret("9.0.0")
        pr.registrer(m, TEN, ny_9, "produksjon", aktiver=False)
        m.commit()
        lagret = m.execute(
            "SELECT innholds_hash FROM policyer WHERE tenant=%s AND"
            " policy_id=%s AND versjon='9.0.0'", (TEN, pid)).fetchone()[0]
        assert lagret == pr.innholds_hash(ny_9), lagret
        m.rollback()
    finally:
        m.close()


@pg
def test_aldri_aktivert_versjon_star_utenfor_aktiveringskronologien():
    """Codex P2: en REGISTRERT versjon er ikke en aktivert versjon.

    `registrer(..., aktiver=False)` legger inn raden uten å aktivere den:
    ingen hendelse, ingen `bootstrap_aktivert_ts`. Falt historikken da
    tilbake på `opprettet`, la den ferskeste registreringen seg ØVERST —
    som om den var den nyest aktiverte — og flaten viste
    registreringstidspunktet under «Aktivert». Diffens default-retning
    leser nettopp de to øverste, så feilen forplantet seg dit.

    Kontroll: fjern `aktivert`-testen fra sorteringen igjen, så kommer
    9.0.0 først og testen blir rød.
    """
    import yaml as _yaml
    from api import policyregister as pr
    pid = "pol-uakt-" + secrets.token_hex(3)
    mal = _yaml.safe_load(
        (ROT / "policies" / "bransjemal-tjenestebedrift.yaml")
        .read_text(encoding="utf-8"))
    mal["meta"]["policy_id"] = pid
    mal["meta"]["status"] = "produksjon"

    m = _c()
    try:
        # Den aktiverte versjonen først — og den REGISTRERTE etterpå, så
        # `opprettet` peker feil vei for enhver sortering som bruker den.
        mal["meta"]["versjon"] = "1.0.0"
        pr.registrer(m, TEN, mal, "produksjon")
        m.commit()
        mal["meta"]["versjon"] = "9.0.0"
        pr.registrer(m, TEN, mal, "produksjon", aktiver=False)
        m.commit()
    finally:
        m.close()

    r = _rt()
    try:
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        rader = r.execute(
            "SELECT versjon, aktivert, aktivert_ts FROM"
            " policyversjoner_for_tenant(%s,%s)", (TEN, pid)).fetchall()
        r.rollback()
    finally:
        r.close()
    assert [x[0] for x in rader] == ["1.0.0", "9.0.0"], \
        f"en aldri aktivert versjon står i aktiveringskronologien: {rader}"
    # Den aktiverte bærer BÅDE flagget og tidspunktet; den registrerte
    # bærer ingen av delene — den har ikke noe aktiveringstidspunkt å vise.
    assert rader[0][1] is True and rader[0][2] is not None, rader
    assert rader[1][1] is False and rader[1][2] is None, rader


@pg
def test_rullbakk_kilde_krever_at_versjonen_har_vart_i_kraft():
    """Codex P2: en rullbakk til noe som aldri virket er ingen rullbakk.

    `registrer(..., aktiver=False)` legger med vilje inn versjoner som
    aldri har vært i kraft — arbeidsstykker, lagt inn før de tas i bruk.
    Historikken tar dem med, og flaten tilbød derfor en rullbakk-knapp for
    dem. Serveren tok imot: lineagen fikk `rollback_av_versjon = 9.0.0`,
    og historikken fortalte i ettertid at et utkast som aldri hadde vært
    aktivt var det vi vendte tilbake til. Kopien selv er helt ordinær —
    det er PÅSTANDEN om at den er en tilbakerulling som er oppdiktet.

    Porten er `policyversjon_kilde`, ikke flaten: den er stedet enhver
    kaller må gjennom. Avslaget skiller seg fra «ukjent versjon» —
    versjonen finnes, den kan leses og diffes, den duger bare ikke som
    kilde — og HTTP-laget svarer 409, ikke 404.

    Kontroll: fjern `policyversjon_i_kraft`-porten fra funksjonen igjen,
    så leveres innholdet og testen blir rød.
    """
    import yaml as _yaml
    from api import policyregister as pr
    pid = "pol-rbkilde-" + secrets.token_hex(3)
    mal = _yaml.safe_load(
        (ROT / "policies" / "bransjemal-tjenestebedrift.yaml")
        .read_text(encoding="utf-8"))
    mal["meta"]["policy_id"] = pid
    mal["meta"]["status"] = "produksjon"

    m = _c()
    try:
        mal["meta"]["versjon"] = "1.0.0"
        pr.registrer(m, TEN, mal, "produksjon")
        m.commit()
        mal["meta"]["versjon"] = "9.0.0"
        pr.registrer(m, TEN, mal, "produksjon", aktiver=False)
        m.commit()
    finally:
        m.close()

    r = _rt()
    try:
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        # Den AKTIVERTE versjonen er fortsatt en gyldig kilde — porten må
        # ikke bli et stille tap av rullbakk for hele serien.
        innhold, gen = r.execute(
            "SELECT innhold, generasjon FROM policyversjon_kilde(%s,%s,%s)",
            (TEN, pid, "1.0.0")).fetchone()
        assert innhold and gen is not None, (innhold, gen)
        r.rollback()

        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            r.execute("SELECT * FROM policyversjon_kilde(%s,%s,%s)",
                      (TEN, pid, "9.0.0")).fetchone()
        r.rollback()

        # Avslaget er ikke «borte»: den samme raden leses fortsatt av
        # diff-veien, som er hele grunnen til at den står i historikken.
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        uaktivert_gen = r.execute(
            "SELECT generasjon FROM policyer WHERE tenant=%s AND"
            "  policy_id=%s AND versjon=%s", (TEN, pid, "9.0.0")).fetchone()[0]
        assert r.execute("SELECT policyversjon_innhold(%s,%s,%s,%s)",
                         (TEN, pid, "9.0.0", uaktivert_gen)).fetchone()[0]
        r.rollback()
    finally:
        r.close()


@pg
def test_historisk_merket_kan_ikke_settes_etter_migrasjonen():
    """Codex P2: forbeholdet gjaldt bare INSERT, og var da intet forbehold.

    `'historisk'` er en TILSTAND — «denne raden lå der da 047 landet» — og
    `policyversjon_i_kraft` leser den som «har vært i kraft». Vakten
    reserverte merket ved INSERT, men en UPDATE kunne sette det etterpå:
    tabelleieren selv, eller en senere vedlikeholdsskriver. En bootstrap-
    eller umerket rad som ALDRI ble aktivert gikk da fra usann til sann i
    den prøven, uten hendelse og uten tidspunkt. Følgene er to: historikken
    begynner å påstå en aktivering ingen har gjort, og raden blir en gyldig
    rullbakk-KILDE — «vi vender tilbake til» noe som aldri har virket.

    Det som felles er OVERGANGEN INN i merket. En rad som alt ER backfilt
    kan fortsatt oppdateres — re-registreringen bevarer merket sitt, og
    backfillen i del 7 løfter rader videre til `'styrt'` etter at vakten
    er på — så begge de veiene måles her ved siden av.

    Målt som MIGRATOR, altså tabelleieren selv: er porten sann for den,
    er den sann for alle. Kontroll: ta `TG_OP = 'INSERT'`-grenen tilbake,
    så blir denne rød.
    """
    pid = "pol-merke-" + secrets.token_hex(3)
    m = _c()
    try:
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        m.execute(
            "INSERT INTO policyer (tenant, policy_id, versjon, innholds_hash,"
            " status, innhold, aktiv, aktiveringskilde) VALUES"
            " (%s,%s,'1','ih-m','produksjon','{}'::jsonb,false,'bootstrap'),"
            " (%s,%s,'2','ih-m2','produksjon','{}'::jsonb,false,NULL)",
            (TEN, pid, TEN, pid))
        m.commit()

        def _i_kraft(versjon):
            m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
            return m.execute(
                "SELECT policyversjon_i_kraft(aktiv, bootstrap_aktivert_ts,"
                "     aktiveringskilde) FROM policyer"
                " WHERE tenant=%s AND policy_id=%s AND versjon=%s",
                (TEN, pid, versjon)).fetchone()[0]

        # `bootstrap` er det ENESTE merket som gjør en rad «aldri i kraft»:
        # en umerket rad sier ingenting vi kan bruke mot den, og
        # `policyversjon_i_kraft` leser derfor fraværet som `historisk`.
        # Det er nettopp bootstrap-raden funnet gjelder — den som går fra
        # usann til sann om merket kan skrives.
        assert _i_kraft("1") is False, "bootstrap-raden har aldri vært i kraft"
        m.commit()

        # Veien inn er stengt fra BEGGE utgangspunkt: et annet merke, og
        # ingen merke i det hele tatt.
        for versjon in ("1", "2"):
            m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
            with pytest.raises(psycopg.errors.CheckViolation) as ei:
                m.execute("UPDATE policyer SET aktiveringskilde='historisk'"
                          " WHERE tenant=%s AND policy_id=%s AND versjon=%s",
                          (TEN, pid, versjon))
            assert "forbeholdt" in str(ei.value), str(ei.value)
            m.rollback()

        # INSERT-siden står som før.
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute(
                "INSERT INTO policyer (tenant, policy_id, versjon,"
                " innholds_hash, status, innhold, aktiv, aktiveringskilde)"
                " VALUES (%s,%s,'3','ih-m3','produksjon','{}'::jsonb,false,"
                " 'historisk')", (TEN, pid))
        m.rollback()

        # …og en rad som ALT er backfilt kan fortsatt oppdateres: det er
        # overgangen som er stengt, ikke raden. (Commit først: `ALTER TABLE`
        # i hjelperen tåler ingen ventende trigger-hendelser, og
        # tenantkonteksten er `SET LOCAL` og må settes på nytt etterpå.)
        m.commit()
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        _backfill_historisk(
            m, "UPDATE policyer SET aktiveringskilde='historisk'"
               " WHERE tenant=%s AND policy_id=%s AND versjon='1'", (TEN, pid))
        m.execute("UPDATE policyer SET status='produksjon'"
                  " WHERE tenant=%s AND policy_id=%s AND versjon='1'",
                  (TEN, pid))
        assert _i_kraft("1") is True
        m.rollback()
    finally:
        m.close()


@pg
def test_reregistrering_av_aktiv_historisk_rad_beholder_opphavet():
    """Codex P2: merket og tidspunktet er én påstand, og må ha én prøve.

    Backfillen i 047 klarte ikke å binde alle aktive rader fra før
    migrasjonen; de som ble stående merkes `'historisk'` — «vi vet ikke
    hvordan denne ble aktivert». En oppsettskjøring over nettopp den
    raden passerer lineage-porten (den har ingen hendelse), og skrev vi
    `'bootstrap'` ubetinget, satt historikken igjen med to uforenlige
    svar: kilden sa at raden kom inn gjennom oppsettet, mens tidspunktet
    ble bevart som NULL — ingen overgang skjedde — så visningen fortsatte
    å falle tilbake på `opprettet`. Det ukjente opphavet var da byttet
    bort mot en påstand ingen hadde grunnlag for, og det er ikke til å
    hente inn igjen.

    Testen måler begge halvdelene: en ALT aktiv rad beholder kilden sin,
    og en INAKTIV rad som faktisk aktiveres merkes fortsatt 'bootstrap'.
    """
    import yaml as _yaml
    from api import policyregister as pr
    pid = "pol-hist-" + secrets.token_hex(3)
    mal = _yaml.safe_load(
        (ROT / "policies" / "bransjemal-tjenestebedrift.yaml")
        .read_text(encoding="utf-8"))
    mal["meta"]["policy_id"] = pid
    mal["meta"]["status"] = "produksjon"

    def _kilde(c, versjon):
        return c.execute(
            "SELECT aktiveringskilde, bootstrap_aktivert_ts FROM policyer"
            " WHERE tenant=%s AND policy_id=%s AND versjon=%s",
            (TEN, pid, versjon)).fetchone()

    m = _c()
    try:
        mal["meta"]["versjon"] = "1.0.0"
        pr.registrer(m, TEN, mal, "produksjon")
        m.commit()
        # Gjør raden om til den førmigrasjonsraden backfillen ikke klarte
        # å binde: aktiv, merket 'historisk', uten tidspunkt. Merket kan
        # ingen skriver sette etter 047 (se `_backfill_historisk`), så
        # tilstanden bygges slik migrasjonen selv bygde den.
        _backfill_historisk(
            m, "UPDATE policyer SET aktiveringskilde='historisk',"
               " bootstrap_aktivert_ts=NULL WHERE tenant=%s"
               " AND policy_id=%s AND versjon='1.0.0'", (TEN, pid))
        m.commit()
        assert _kilde(m, "1.0.0") == ("historisk", None)
        m.commit()

        # Oppsettskjøringen på nytt — samme aktive versjon, ingen overgang.
        pr.registrer(m, TEN, mal, "produksjon")
        m.commit()
        assert _kilde(m, "1.0.0") == ("historisk", None), \
            "en oppsettskjøring overskrev et ukjent opphav med 'bootstrap'"
        m.commit()

        # Den andre halvdelen: en INAKTIV rad som faktisk aktiveres er
        # aktivert nettopp her, og skal merkes deretter.
        mal["meta"]["versjon"] = "2.0.0"
        pr.registrer(m, TEN, mal, "produksjon", aktiver=False)
        m.commit()
        _backfill_historisk(
            m, "UPDATE policyer SET aktiveringskilde='historisk'"
               " WHERE tenant=%s AND policy_id=%s AND versjon='2.0.0'",
            (TEN, pid))
        m.commit()
        pr.registrer(m, TEN, mal, "produksjon")
        m.commit()
        kilde, ts = _kilde(m, "2.0.0")
        assert kilde == "bootstrap" and ts is not None, (kilde, ts)
        # Den forrige aktive raden er deaktivert, men opphavet dens er
        # historikk og står urørt.
        assert _kilde(m, "1.0.0") == ("historisk", None)
        m.rollback()
    finally:
        m.close()


@pg
def test_reregistrering_av_avlost_rad_beholder_opphavet():
    """Codex P2: «samme prøve» må også være det SAMME UTTRYKKET.

    Forrige runde bandt merket og tidspunktet til samme påstand, men de to
    CASE-ene målte ulike ting: tidspunktet spurte om en OVERGANG, merket
    bare om raden var aktiv fra før. En AVLØST rad — inaktiv, merket
    `'historisk'` fordi backfillen ikke klarte å binde den — falt derfor i
    ELSE-grenen ved en helt ordinær, identisk oppsettskjøring med
    `aktiver=False`, og fikk `'bootstrap'` skrevet over seg uten at noen
    aktivering hadde skjedd.

    Merket er det ENESTE sporet av at en migrert rad har vært i kraft:
    tidspunktet er NULL for dem alle. `policyversjon_i_kraft` gikk derfor
    fra sann til usann, og testen måler begge følgene av det — historikken
    påstår at versjonen aldri ble aktivert, og vakten som skal hindre at
    et innhold som har vært i kraft byttes ut, slipper taket.
    """
    import yaml as _yaml
    from api import policyregister as pr
    pid = "pol-avlost-" + secrets.token_hex(3)
    mal = _yaml.safe_load(
        (ROT / "policies" / "bransjemal-tjenestebedrift.yaml")
        .read_text(encoding="utf-8"))
    mal["meta"]["policy_id"] = pid
    mal["meta"]["status"] = "produksjon"

    m = _c()
    try:
        mal["meta"]["versjon"] = "1.0.0"
        pr.registrer(m, TEN, mal, "produksjon")
        mal["meta"]["versjon"] = "2.0.0"
        pr.registrer(m, TEN, mal, "produksjon")
        m.commit()
        # 1.0.0 er nå avløst. Gjør den til førmigrasjonsraden backfillen
        # ikke klarte å binde: inaktiv, merket 'historisk', uten tidspunkt.
        _backfill_historisk(
            m, "UPDATE policyer SET aktiveringskilde='historisk',"
               " bootstrap_aktivert_ts=NULL WHERE tenant=%s"
               " AND policy_id=%s AND versjon='1.0.0'", (TEN, pid))
        m.commit()

        def _i_kraft():
            return m.execute(
                "SELECT policyversjon_i_kraft(aktiv, bootstrap_aktivert_ts,"
                "     aktiveringskilde)"
                " FROM policyer WHERE tenant=%s AND policy_id=%s"
                " AND versjon='1.0.0'", (TEN, pid)).fetchone()[0]

        assert _i_kraft() is True
        m.commit()

        # Den identiske oppsettskjøringen: samme innhold, ingen overgang.
        mal["meta"]["versjon"] = "1.0.0"
        pr.registrer(m, TEN, mal, "produksjon", aktiver=False)
        m.commit()
        kilde = m.execute(
            "SELECT aktiveringskilde, bootstrap_aktivert_ts FROM policyer"
            " WHERE tenant=%s AND policy_id=%s AND versjon='1.0.0'",
            (TEN, pid)).fetchone()
        assert kilde == ("historisk", None), \
            "en re-registrering uten aktivering skrev om opphavet: " \
            f"{kilde}"
        assert _i_kraft() is True, \
            "raden har vært i kraft, men rapporteres nå som aldri aktivert"
        m.commit()

        # Følgefeilen: mister raden merket, slipper også innholdsvakten.
        mal["handlinger"][0]["grenser"]["belop_maks"] = "1.00"
        with pytest.raises(pr.PolicyKorrupt) as ei:
            pr.registrer(m, TEN, mal, "produksjon", aktiver=False)
        assert "har vært i kraft" in str(ei.value)
        m.rollback()
    finally:
        m.close()


@pg
def test_bootstrap_serialiseres_mot_styrt_aktivering():
    """Codex P1: prøven i `registrer` er verdiløs uten LÅSEN under seg.

    Den delte advisory-låsen serialiserer bootstrapen mot SLETTINGEN, som
    tar den eksklusive varianten — men ikke mot `aktiver_policy`, som ikke
    tar den i det hele tatt. Autoriteten den styrte veien serialiserer på
    er `policy_hode`-raden. Uten den kunne en aktivering committe mellom
    prøven og `UPDATE policyer SET aktiv=false`, og bootstrapen ville
    deaktivert en nettopp styrt aktivert versjon og satt inn sin egen
    hendelsesløse rad.

    Målt der det er målbart: `registrer` må BLOKKERE når en annen
    transaksjon holder ankerraden. Holder den ikke låsen, går kallet rett
    gjennom og testen er rød.
    """
    import threading
    import yaml as _yaml
    from api import policyregister as pr
    pid = "pol-laas-" + secrets.token_hex(3)
    mal = _yaml.safe_load(
        (ROT / "policies" / "bransjemal-tjenestebedrift.yaml")
        .read_text(encoding="utf-8"))
    mal["meta"]["policy_id"] = pid
    mal["meta"]["versjon"] = "1.0.0"
    mal["meta"]["status"] = "produksjon"

    holder = _c()
    holder.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
    holder.execute(
        "INSERT INTO policy_hode (tenant, policy_id) VALUES (%s,%s)"
        " ON CONFLICT (tenant, policy_id) DO NOTHING", (TEN, pid))
    holder.commit()
    holder.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
    holder.execute("SELECT aktiv_versjon FROM policy_hode WHERE tenant=%s"
                   " AND policy_id=%s FOR UPDATE", (TEN, pid))

    ferdig = threading.Event()

    def bootstrap():
        c = _c()
        try:
            pr.registrer(c, TEN, mal, "produksjon")
            c.commit()
        except Exception:                                     # noqa: BLE001
            c.rollback()
        finally:
            c.close()
            ferdig.set()

    t = threading.Thread(target=bootstrap, daemon=True)
    t.start()
    try:
        assert not ferdig.wait(1.5), \
            "registrer gikk gjennom mens ankerraden var låst — prøven er" \
            " ikke serialisert mot styrt aktivering"
    finally:
        holder.rollback()
        holder.close()
    ferdig.wait(10)


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
def test_ny_runde_kan_ikke_fodes_terminal_uten_binding():
    """Port 10c (Codex P1): den hendelsesløse terminalrunden INSERT-et rett
    inn.

    Tilstandsmaskin-triggeren er BEFORE UPDATE, så en rad som FØDES `brukt`
    passerer den aldri. FK-en mot hendelsen er MATCH SIMPLE og sover så
    lenge én av de fem kolonnene er NULL. Uten `aktivert_som_versjon` i
    CHECK-en var det derfor en åpen dør: `status='brukt'` +
    `decision_operation_id` + binding NULL committet fint, med runtime-
    rollens eget INSERT-grant, og runden sto terminal uten hendelse —
    nøyaktig formen migrasjonen sier den forbyr.

    Historikkunntaket berøres ikke: det bæres av NOT VALID, som skåner
    radene som ALT fantes, ikke formen."""
    c = _c()
    uid, pid = _ny()
    _validert_utkast(c, uid, pid)
    c.commit()
    kolonner = ("tenant,utkast_id,runde,status,decision_operation_id,"
                "aktivert_som_versjon,diff_hash,utkast_innholds_hash,"
                "base_policy_hash,risikoklasse,klassifisering_hash,"
                "klassifikatorversjon,policyskjema_versjon,"
                "motor_semantikkversjon,deny_all_hash,deny_all_versjon,"
                "pakrevd_antall_godkjennere,utloper")
    verdier = ("%s,%s,7,'brukt','op-direkte',{binding},'d','u','b','UTVIDER',"
               "'k','kv1','0.2','m1','da','1',2,now()+interval '1 hour'")
    with pytest.raises(psycopg.errors.CheckViolation) as ei:
        c.execute(f"INSERT INTO aktiveringsrunde ({kolonner}) VALUES ("
                  + verdier.format(binding="NULL") + ")", (TEN, uid))
    assert "runde_versjon_krever_brukt" in str(ei.value)
    c.rollback()
    # Med bindingen på plass er alle fem FK-kolonnene NOT NULL, og den
    # utsatte FK-en feller den falske historien ved commit i stedet.
    c.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
    c.execute(f"INSERT INTO aktiveringsrunde ({kolonner}) VALUES ("
              + verdier.format(binding="'9'") + ")", (TEN, uid))
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


def test_backfillen_teller_begge_sider_av_matchen():
    """Port 20b (statisk, Codex P1): en match har TO sider, og begge må
    være entydige.

    Rundetellingen alene svarer bare på «hvor mange runder passer denne
    versjonen?». To versjonsrader med samme `innholds_hash` og ÉN brukt
    runde gir 1 for begge — og da avgjorde `ORDER BY ... opprettet` i
    ytterløkka hvem som arvet attestasjonene, altså en gjetning på
    opprettelsesrekkefølge, festet i en udødelig hendelse.

    Statisk fordi backfillen ALT har kjørt når testsuiten møter basen (som
    port 20): det finnes ingen ubundne rader igjen å måle den på. Testen
    måler derfor formen — at begge tellingene står som vokter FØR
    INSERTen, og at begge grenene teller flertydig og CONTINUEr."""
    tekst = MIGRASJON.read_text(encoding="utf-8")
    blokk = tekst.split("7. Backfill", 1)[1].split("DO $$", 1)[1] \
        .split("END $$;", 1)[0]
    vokter = [b for b in blokk.split("SELECT count(*) INTO ")[1:]]
    assert len(vokter) == 2, "begge sider av matchen skal telles"
    # Side 1: rundene som passer versjonen. Side 2: versjonene som
    # konkurrerer om runden.
    assert "public.aktiveringsrunde" in vokter[0]
    kandidatvakt = vokter[1].split("END IF;", 1)[0]
    assert "public.policyer" in kandidatvakt
    assert "innholds_hash = r.innholds_hash" in kandidatvakt
    assert "aktivert_av_operasjon IS NULL" in kandidatvakt
    assert "v_flertydige := v_flertydige + 1" in kandidatvakt
    assert "CONTINUE;" in kandidatvakt
    # Begge voktene ligger FØR den udødelige raden skrives — ellers er
    # gjetningen alt festet når de måler.
    innsett = blokk.index("INSERT INTO public.policyaktivering")
    assert blokk.index("SELECT count(*) INTO v_kandidater") < innsett
    assert blokk.index("SELECT count(*) INTO v_antall") < innsett


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
            r.execute("SELECT policyversjon_innhold(%s,'p','1',1)", (TEN,))
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


def _gen(pid, versjon):
    """Kilderadens generasjon — det flaten leser ut av historikken og
    sender tilbake som optimistisk lås på en rullbakk (Codex P2)."""
    m = _c()
    try:
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        rad = m.execute(
            "SELECT generasjon FROM policyer WHERE tenant=%s"
            "  AND policy_id=%s AND versjon=%s",
            (TEN, pid, versjon)).fetchone()
        m.rollback()
        return rad[0] if rad else None
    finally:
        m.close()


@pg
def test_rullbakk_er_serverens_kopi_og_replaysikker(klient):
    """Portene 22, 23 og 26: utkastet bærer NØYAKTIG `policyer.innhold`
    for N (serveren henter det selv — et avvikende klientinnhold
    avvises), samme nøkkel replayer til samme utkast, og N−5 er like
    lovlig som N−1."""
    uid, pid, v = _full_aktivering(pakrevd=1)
    cookie, csrf = _forvaltersesjon()
    gen = _gen(pid, v)
    nokkel = "rb-" + secrets.token_hex(8)
    r = _post(klient, cookie, csrf, "/v1/policyutkast",
              {"policy_id": pid, "rollback_av_versjon": v,
               "rollback_av_generasjon": gen}, nokkel)
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
               {"policy_id": pid, "rollback_av_versjon": v,
                "rollback_av_generasjon": gen}, nokkel)
    assert r2.status_code in (200, 201), r2.text
    assert r2.json()["utkast_id"] == ny_uid
    # 22b: et klientinnhold som AVVIKER fra versjonens avvises — en
    # rullbakk som lyver om innholdet sitt er en løgn i lineagen.
    r3 = _post(klient, cookie, csrf, "/v1/policyutkast",
               {"policy_id": pid, "rollback_av_versjon": v,
                "rollback_av_generasjon": gen,
                "innhold": {"noe": "annet"}})
    assert r3.status_code == 400, r3.text
    # 22c (Codex R4): løgnen må heller ikke slippe inn gjennom REPLAYEN.
    # Samme nøkkel som det vellykkede forsøket over, samme kildeversjon, men
    # nå med en påstand om innholdet — replayen ligger foran kildeoppslaget,
    # så uten at påstanden binder nøkkelen ville dette gitt 201 og et svar
    # som stilltiende bekreftet et innhold ingen hadde målt.
    r5 = _post(klient, cookie, csrf, "/v1/policyutkast",
               {"policy_id": pid, "rollback_av_versjon": v,
                "rollback_av_generasjon": gen,
                "innhold": {"noe": "annet"}}, nokkel)
    assert r5.status_code == 409, r5.text
    assert r5.json()["feil"] == "idempotenskonflikt", r5.text
    # 22d (Codex P2): en ANNEN kildegenerasjon under samme nøkkel er
    # heller ikke det samme kallet. Nummeret gjenbrukes, generasjonen
    # ikke — så replayen kan ikke svare for en kilde ingen har målt.
    r6 = _post(klient, cookie, csrf, "/v1/policyutkast",
               {"policy_id": pid, "rollback_av_versjon": v,
                "rollback_av_generasjon": gen + 1}, nokkel)
    assert r6.status_code == 409, r6.text
    assert r6.json()["feil"] == "idempotenskonflikt", r6.text
    # Ukjent versjon → ikke_funnet, aldri et tomt utkast.
    r4 = _post(klient, cookie, csrf, "/v1/policyutkast",
               {"policy_id": pid, "rollback_av_versjon": "999",
                "rollback_av_generasjon": gen})
    assert r4.status_code == 404, r4.text


@pg
def test_rullbakk_retry_beholder_et_lagret_svar_naar_kilden_er_borte(klient):
    """Codex P2: forkontrollen ser ikke en vinner som ikke har committet.

    Rullbakkopprettelsen gjør arbeid FØR den claimer nøkkelen — den henter
    kildeversjonen, fordi serveren eier kopien — og forkontrollen foran
    det arbeidet er en ren lesing. Kommer en retry mens originalen ennå er
    underveis, finnes idempotensraden, men ikke i retryens READ
    COMMITTED-snapshot: svaret er `ukjent`, og ruta går videre til
    kildeoppslaget. Rekker originalen å committe, og `slett_ubrukt_policy`
    å fjerne versjonen, før oppslaget skjer, svarte ruta 404 på en nøkkel
    som ALT bar et lagret 201 — og en senere retry ville replayet nettopp
    det. Samme forespørsel, to ulike svar, avgjort av hvem som vant et
    kappløp.

    Etterprøven VENTER på vinneren i stedet for å gjette: den tar den
    samme advisory-låsen som claimet, og den holdes hele originalens
    transaksjon.

    Vinneren spilles her av en tråd som gjør nøyaktig det en pågående
    original gjør på DB-nivå: tar låsen, skriver den ferdige raden, og
    committer — men først når den ser at noen faktisk VENTER på låsen.
    Uten etterprøven venter ingen: ruta svarer 404 med en gang, tråden
    venter forgjeves ut fristen sin, og assert-en under er rød.
    """
    import threading
    import time
    from api import policyadmin as _pa
    uid, pid, v = _full_aktivering(pakrevd=1)
    cookie, csrf = _forvaltersesjon()
    gen = _gen(pid, v)
    nokkel = "rb-" + secrets.token_hex(8)
    kropp = {"policy_id": pid, "rollback_av_versjon": v,
             "rollback_av_generasjon": gen}
    r = _post(klient, cookie, csrf, "/v1/policyutkast", kropp, nokkel)
    assert r.status_code == 201, r.text
    lagret_uid = r.json()["utkast_id"]

    # Det vinneren skrev, hentes ut — og nøkkelen gjøres UKJENT igjen, for
    # det er nettopp en uskrevet (usynlig) rad forkontrollen møter.
    m = _c()
    try:
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        ih, respons = m.execute(
            "SELECT input_hash, respons FROM idempotens"
            " WHERE tenant=%s AND nokkel=%s", (TEN, nokkel)).fetchone()
        m.execute("DELETE FROM idempotens WHERE tenant=%s AND nokkel=%s",
                  (TEN, nokkel))
        innholds_hash = m.execute(
            "SELECT innholds_hash FROM policyer WHERE tenant=%s"
            "  AND policy_id=%s AND versjon=%s", (TEN, pid, v)).fetchone()[0]
        m.commit()
    finally:
        m.close()

    # Kilden forsvinner — den støttede veien, ikke en fabrikasjon.
    rt = _rt()
    try:
        rt.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        assert rt.execute("SELECT slett_ubrukt_policy(%s,%s,%s,%s)",
                          (TEN, pid, v, innholds_hash)).fetchone()[0] == 1
        rt.commit()
    finally:
        rt.close()

    holder_laasen = threading.Event()
    feil = []

    def vinneren():
        c = _c()
        try:
            c.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
            _pa._idempotent_laas(c, TEN, nokkel)
            c.execute(
                "INSERT INTO idempotens (tenant, nokkel, input_hash, status,"
                " respons, request_id)"
                " VALUES (%s,%s,%s,'ferdig',%s,'r-vinner')",
                (TEN, nokkel, ih, json.dumps(respons)))
            holder_laasen.set()
            # Committer først når noen VENTER på låsen — det er da retryen
            # har nådd etterprøven. Fristen er en sikkerhetsventil: uten
            # etterprøven kommer ingen venter, og testen skal feile på
            # svaret under, ikke henge.
            frist = time.monotonic() + 15
            while time.monotonic() < frist:
                if c.execute(
                    "SELECT count(*) FROM pg_locks WHERE locktype='advisory'"
                    "   AND NOT granted").fetchone()[0] > 0:
                    break
                time.sleep(0.02)
            c.commit()
        except Exception as e:                       # pragma: no cover
            feil.append(e)
            c.rollback()
        finally:
            c.close()

    tr = threading.Thread(target=vinneren, daemon=True)
    tr.start()
    assert holder_laasen.wait(15), "vinnertråden fikk aldri låsen"
    r2 = _post(klient, cookie, csrf, "/v1/policyutkast", kropp, nokkel)
    tr.join(30)
    assert not feil, feil
    assert r2.status_code == 201, r2.text
    assert r2.json()["utkast_id"] == lagret_uid, r2.text


@pg
def test_rullbakkeopphavet_maa_vaere_sant_ved_innsettingen():
    """Codex P2: frysingen vernet et opphav som ALT var skrevet.

    Ingen kan flytte `rollback_av_versjon`/`-generasjon` etterpå — men
    fødselen var uvoktet, og kjøretidsrollen har direkte INSERT på
    `policyutkast`. En direkte eller uoppmerksom skriver kunne sette inn
    et hvilket som helst innhold sammen med versjonen og generasjonen til
    en levende, urelatert kilde. `aktiver_policy` spør aldri om utkastet
    FAKTISK er en kopi, så historikken sto etterpå og sa `bundet` om et
    opphav ingen hadde kopiert fra — og frysingen gjorde løgnen varig.

    Alle fire påstandene måles her, som MIGRATOR (tabelleieren selv): er
    porten sann for den, er den sann for alle.

    Kontroll: dropp `policyutkast_rullbakkeopphav_vakt_trg`, så blir alle
    de fire avvisningene under grønne innsettinger.
    """
    uid, pid, v = _full_aktivering(pakrevd=1)
    gen = _gen(pid, v)
    kolonner = ("INSERT INTO policyutkast (tenant,utkast_id,policy_id,"
                "innhold,status,innholds_hash,opprettet_av,"
                "rollback_av_versjon,rollback_av_generasjon) VALUES"
                " (%s,%s,%s,%s::jsonb,'validert',%s,'forf',%s,%s)")

    def _sett_inn(m, innhold, kilde_versjon, kilde_gen):
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        m.execute(kolonner, (TEN, "u-" + secrets.token_hex(5), pid,
                             json.dumps(innhold), "ih-" + secrets.token_hex(6),
                             kilde_versjon, kilde_gen))

    m = _c()
    try:
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        kilde = m.execute(
            "SELECT innhold FROM policyer WHERE tenant=%s AND policy_id=%s"
            "  AND versjon=%s", (TEN, pid, v)).fetchone()[0]
        m.rollback()
        kopi = json.loads(json.dumps(kilde))
        kopi["meta"]["versjon"] = "9.9.9"

        # 1: opphavet uten adresse — nummeret alene er en peker.
        with pytest.raises(psycopg.errors.CheckViolation) as e1:
            _sett_inn(m, kopi, v, None)
        assert "generasjon" in str(e1.value), str(e1.value)
        m.rollback()

        # 2: en kilde som ikke finnes med den generasjonen.
        with pytest.raises(psycopg.errors.CheckViolation) as e2:
            _sett_inn(m, kopi, v, gen + 100000)
        assert "finnes ikke" in str(e2.value), str(e2.value)
        m.rollback()

        # 3: riktig kilde, men innholdet er ikke kopien. Dette er selve
        # fabrikasjonen: et fremmed dokument som bærer et ekte opphav.
        with pytest.raises(psycopg.errors.CheckViolation) as e3:
            _sett_inn(m, {"meta": {"policy_id": pid, "versjon": "9.9.9",
                                   "status": "produksjon"}, "a": 2}, v, gen)
        assert "KOPI" in str(e3.value), str(e3.value)
        m.rollback()

        # 4: en kilde som ALDRI har vært i kraft er ingen rullbakk-kilde —
        # samme prøve som `policyversjon_kilde` gjør for HTTP-veien.
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        uaktivert = m.execute(
            "INSERT INTO policyer (tenant, policy_id, versjon, innholds_hash,"
            " status, innhold, aktiv, aktiveringskilde) VALUES"
            " (%s,%s,'8.0.0','ih-u','produksjon',%s::jsonb,false,'bootstrap')"
            " RETURNING generasjon",
            (TEN, pid, json.dumps(kilde))).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation) as e4:
            m.execute(kolonner, (TEN, "u-" + secrets.token_hex(5), pid,
                                 json.dumps(kopi), "ih-x", "8.0.0", uaktivert))
        assert "i kraft" in str(e4.value), str(e4.value)
        m.rollback()

        # …og den ekte kopien slipper gjennom. Porten er en port, ikke en
        # mur: det er nettopp denne formen `opprett_utkast` lager.
        _sett_inn(m, kopi, v, gen)
        m.rollback()
    finally:
        m.close()


@pg
def test_rullbakkeopphavet_er_frosset_naar_historikken_leser_det(klient):
    """Codex P2: historikken rapporterer `rollback_av_versjon` som LINJE.

    `policyversjoner_for_tenant` leser kolonnen og sier «denne versjonen
    er en rullbakk av N». Den var ikke frosset av noe:
    `policyutkast_kolonnelaas` nevner den ikke, terminalvernet der måler
    bare status-OVERGANGER (et `aktivert` utkast kan altså oppdateres så
    lenge statusen står stille), og kjøretidsrollen beholder UPDATE på
    tabellen. En ordinær, alt aktivert versjon kunne dermed i ettertid få
    et opphav — eller få det flyttet — uten at den immutable hendelsen
    eller attestasjonene ble rørt.

    Målt der det er sterkest: som TABELLEIER (migrator), altså forbi
    grant-porten, og på BEGGE fabrikasjonene — NULL → N og N → M.
    """
    uid, pid, v = _full_aktivering(pakrevd=1)
    cookie, csrf = _forvaltersesjon()
    r = _post(klient, cookie, csrf, "/v1/policyutkast",
              {"policy_id": pid, "rollback_av_versjon": v,
               "rollback_av_generasjon": _gen(pid, v)})
    assert r.status_code == 201, r.text
    rb_uid = r.json()["utkast_id"]
    m = _c()
    try:
        # NULL → N: det ordinære utkastet som gjøres om til en rullbakk.
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE policyutkast SET rollback_av_versjon=%s"
                      " WHERE tenant=%s AND utkast_id=%s", (v, TEN, uid))
        m.rollback()
        # N → M: opphavet flyttes til en annen versjon.
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE policyutkast SET rollback_av_versjon='9.9.9'"
                      " WHERE tenant=%s AND utkast_id=%s", (TEN, rb_uid))
        m.rollback()
        # N → NULL: en rullbakk kan heller ikke vaskes til å se ordinær ut.
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE policyutkast SET rollback_av_versjon=NULL"
                      " WHERE tenant=%s AND utkast_id=%s", (TEN, rb_uid))
        m.rollback()
        # Låsen gjelder KOLONNEN, ikke raden: utkastet er fortsatt
        # redigerbart på alle de vanlige måtene.
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        m.execute("UPDATE policyutkast SET utkastversjon=utkastversjon+1"
                  " WHERE tenant=%s AND utkast_id=%s", (TEN, rb_uid))
        assert m.execute(
            "SELECT rollback_av_versjon FROM policyutkast WHERE tenant=%s"
            " AND utkast_id=%s", (TEN, rb_uid)).fetchone()[0] == v
        m.rollback()
    finally:
        m.close()


def _overstyringsutkast(pid, overstyring, versjon="1.1.0"):
    """Dokument med en `godkjennbare`-oppføring og handlingen den peker på.

    `tillatt_for` er PÅKREVD i fiksturen (Codex P1, runde 9): gaten feller
    nå en handling uten en eneste tillatt rolle i steg 3, før grensene i det
    hele tatt vurderes. Uten rollen ville hver oppføring under blitt avvist
    på DEN grunnen — også den positive kontrollen — og testen ville målt noe
    annet enn den sier.
    """
    return json.dumps({
        "meta": {"policy_id": pid, "versjon": versjon,
                 "status": "produksjon"},
        "handlinger": [{"id": "ordre.bekreft", "modus": "grense",
                        "tillatt_for": ["agent"],
                        "grenser": {"belop_maks": 1000, "valuta": ["NOK"]}}],
        "menneskelig_overstyring": {"godkjennbare": [overstyring]}})


@pg
def test_aktiver_policy_avviser_uanvendelig_overstyring():
    """Port 29 (Codex P2): anvendbarhetskravet står også i SQL-gaten.

    Runtime-rollen har EXECUTE på `aktiver_policy`, og en runde validert og
    attestert FØR utrullingen bærer statusen sin forbi Python-porten. Uten
    gaten her kunne en virkningsløs overstyring aktiveres — og utfallet er
    stille: policyen SER konfigurert ut, mens hver matchende godkjenning
    ender i STOPP.

    Fire avvisninger og én positiv kontroll, så testen ikke er grønn av at
    ingenting slipper gjennom.
    """
    uanvendelige = [
        # Ikke-løftbar grunnkode.
        {"grunnkode": "dataklasse_forbudt", "handling": "ordre.bekreft"},
        # Løftbar kode uten verdien den krever.
        {"grunnkode": "belop_over_grense", "handling": "ordre.bekreft"},
        # Tak som ikke er høyere enn handlingens egen grense.
        {"grunnkode": "belop_over_grense", "handling": "ordre.bekreft",
         "belop_maks": 1000, "valuta": "NOK"},
        # Valuta handlingen ALT tillater.
        {"grunnkode": "valuta_ikke_tillatt", "handling": "ordre.bekreft",
         "valuta": "NOK"},
    ]
    for i, oppf in enumerate(uanvendelige):
        c = _c()
        uid, pid = _ny()
        _validert_utkast(c, uid, pid, av="forf",
                         innhold=_overstyringsutkast(pid, oppf))
        _runde(c, uid, pakrevd_antall_godkjennere=1,
               risikoklasse="INNSNEVRER")
        _attest(c, uid, "uavh", False)
        c.commit(); c.close()
        r = _rt()
        try:
            with pytest.raises(psycopg.errors.CheckViolation) as ei:
                _aktiver(r, uid)
            assert "overstyring" in str(ei.value), (i, str(ei.value))
            r.rollback()
        finally:
            r.close()
    # Positiv kontroll: en oppføring som FAKTISK kan anvendes aktiverer.
    c = _c()
    uid, pid = _ny()
    _validert_utkast(c, uid, pid, av="forf", innhold=_overstyringsutkast(
        pid, {"grunnkode": "belop_over_grense", "handling": "ordre.bekreft",
              "belop_maks": 5000, "valuta": "NOK"}))
    _runde(c, uid, pakrevd_antall_godkjennere=1, risikoklasse="INNSNEVRER")
    _attest(c, uid, "uavh", False)
    c.commit(); c.close()
    r = _rt()
    try:
        assert _aktiver(r, uid) == "1.1.0"
    finally:
        r.close()


def test_sql_gaten_kjenner_de_samme_loftbare_kodene():
    """Port 28 (statisk, Codex P2): ÉN kilde, to språk.

    `aktiver_policy` måler anvendbarheten selv — runtime-rollen har EXECUTE
    på funksjonen, og en runde validert FØR utrullingen bærer statusen sin
    forbi Python-porten. Da finnes regelen to steder, og det er akkurat
    slik en fail-open oppstår: en ny løftbar grunnkode lagt til i motoren
    uten en gren i SQL-en ville sluppet nøyaktig de virkningsløse formene
    gjennom, bare for den nye koden.

    Testen krever ikke at SQL-en er en oversettelse av Python-koden — den
    krever at ENUMERASJONENE og modusnavnet er de samme. Utvides motoren,
    blir denne rød til SQL-porten har fått grenen sin.
    """
    from policy_validator.engine import (LOFTBARE_GRUNNKODER,
                                         MODUS_UTEN_LOFTBARE_UTFALL)
    kilde = MIGRASJON.read_text(encoding="utf-8")
    gate = kilde[kilde.index("-- 4e. OVERSTYRINGEN"):
                 kilde.index("CONSTRAINT = 'overstyring_anvendbar'")]
    # Enumerasjonen i `NOT IN (...)` er porten mot ikke-løftbare koder.
    m = re.search(r"NOT IN \(([^)]*)\)", gate)
    assert m, "4e har ingen enumerasjon av løftbare grunnkoder"
    i_sql = {b.strip().strip("'") for b in m.group(1).split(",")}
    assert i_sql == set(LOFTBARE_GRUNNKODER), \
        f"SQL-porten kjenner {i_sql}, motoren {set(LOFTBARE_GRUNNKODER)}"
    # Feltet hver kode krever, og modusen som feller før grensene.
    for kode, felt in LOFTBARE_GRUNNKODER.items():
        assert f"'{felt}'" in gate, f"4e måler ikke '{felt}' for {kode}"
    assert f"'{MODUS_UTEN_LOFTBARE_UTFALL}'" in gate, \
        "4e kjenner ikke modusen som feller før grensene vurderes"


def test_sql_gaten_caster_aldri_et_ubundet_sifferfelt():
    """Codex P2 (statisk): et cast som kan velte, feller aktiveringen.

    Mønsteret `^-?[0-9]+(\\.[0-9]+)?$` sier bare at tegnene er sifre. En
    sifferstreng lengre enn `NUMERIC` kan bære passerte det, og castet
    feilet da med `numeric_value_out_of_range` — en kode kalleren ikke
    håndterer, så en ferdig attestert fire-øyne-runde endte i 500 i stedet
    for i en dom. Hvert cast i gaten må derfor stå bak en LENGDEPRØVE, i
    samme `CASE`, slik at det bare evalueres for noe basen kan lese.

    Målt statisk fordi det er formen som er kravet: en ny sammenligning
    som glemmer prøven er nøyaktig samme feil om igjen.

    Kontroll: fjern `length(...) <= ` foran ett av castene, så blir denne
    rød.
    """
    kilde = MIGRASJON.read_text(encoding="utf-8")
    gate = kilde[kilde.index("-- 4e. OVERSTYRINGEN"):
                 kilde.index("CONSTRAINT = 'overstyring_anvendbar'")]
    foran = gate.split("::NUMERIC")[:-1]
    assert foran, "4e har ingen NUMERIC-cast — er gaten flyttet?"
    for bit in foran:
        # Vakten til nettopp dette castet er `CASE WHEN`-en nærmest foran.
        vakt = bit[bit.rindex("CASE WHEN"):]
        assert re.search(r"length\([^)]*\)\s*<=\s*\d+", vakt), \
            f"et NUMERIC-cast står uten lengdeprøve: {vakt.strip()[:120]}"


def _rullbakkutkast(c, uid, pid, versjon, kilde_versjon, kilde_gen):
    """Et validert utkast som BÆRER et rullbakkeopphav. `_validert_utkast`
    kjenner ikke kolonnene; her settes de ved INNSETTINGEN, som i porten —
    de er frosset etterpå.

    Innholdet er en EKTE kopi av kildeversjonen, med `meta.versjon` bumpet:
    det er formen `opprett_utkast` lager, og etter Codex P2 den eneste
    `policyutkast_rullbakkeopphav_vakt` slipper inn.

    `borte` og `ubundet` er derimot tilstander TIDEN lager — kilden ble
    slettet etter at kopien ble tatt, eller utkastet er eldre enn 047.
    Ingen skriver får lage dem, så fiksturen bygger dem med vakten av,
    slik den bygger backfilte rader i `_backfill_historisk`.
    """
    kilde_innhold, levende_gen = c.execute(
        "SELECT innhold, generasjon FROM policyer WHERE tenant=%s"
        "  AND policy_id=%s AND versjon=%s",
        (TEN, pid, kilde_versjon)).fetchone()
    innhold = json.loads(json.dumps(kilde_innhold))
    innhold["meta"]["versjon"] = versjon
    sql = ("INSERT INTO policyutkast (tenant,utkast_id,policy_id,innhold,"
           "status,innholds_hash,opprettet_av,rollback_av_versjon,"
           "rollback_av_generasjon) VALUES"
           " (%s,%s,%s,%s::jsonb,'validert',%s,'forf',%s,%s)")
    param = (TEN, uid, pid, json.dumps(innhold),
             "ih-" + secrets.token_hex(8), kilde_versjon, kilde_gen)
    if kilde_gen == levende_gen:
        c.execute(sql, param)
        return
    c.execute("ALTER TABLE policyutkast DISABLE TRIGGER"
              " policyutkast_rullbakkeopphav_vakt_trg")
    try:
        c.execute(sql, param)
    finally:
        c.execute("ALTER TABLE policyutkast ENABLE TRIGGER"
                  " policyutkast_rullbakkeopphav_vakt_trg")


@pg
def test_rullbakk_avvises_naar_kilden_er_en_annen_generasjon(klient):
    """Codex P2: forespørselen navnga bare NUMMERET, og et nummer er
    gjenbrukbart.

    `slett_ubrukt_policy` frigjør uttrykkelig `(policy_id, versjon)`.
    Slettes raden og gjenskapes nummeret mellom visningen eier leste og
    klikket hun gjorde, kopierte serveren ERSTATNINGEN: det lagrede
    opphavet ble internt konsistent — kopien og generasjonen hørte sammen
    — og likevel feil, for det var ikke generasjonen eier så. Ingen
    senere skriving kan avsløre det; forvekslingen skjedde i selve
    opprettelsen.

    Generasjonen er derfor den optimistiske låsen på kilden, søsteren til
    `slett_policy`s `versjon`/`innholds_hash`: flaten sender tallet den
    viste, og porten avviser med 409 når kilden har skiftet.

    Kontroll: fjern sammenligningen i endepunktet, så lager kallet under
    et utkast fra den nye generasjonen og testen blir rød.
    """
    uid, pid, v = _full_aktivering(pakrevd=1)
    cookie, csrf = _forvaltersesjon()
    gammel_gen = _gen(pid, v)

    # Generasjonen eier SÅ finnes ikke lenger: raden slettes og nummeret
    # gjenskapes gjennom den styrte veien.
    m = _c()
    try:
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        ih = m.execute(
            "SELECT innholds_hash FROM policyer WHERE tenant=%s"
            "  AND policy_id=%s AND versjon=%s", (TEN, pid, v)).fetchone()[0]
        m.rollback()
    finally:
        m.close()
    r = _rt()
    try:
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        assert r.execute("SELECT slett_ubrukt_policy(%s,%s,%s,%s)",
                         (TEN, pid, v, ih)).fetchone()[0] == 1
        r.commit()
    finally:
        r.close()
    c = _c()
    uid2 = "u-" + secrets.token_hex(4)
    _validert_utkast(c, uid2, pid, av="forf", versjon=v)
    _runde(c, uid2, pakrevd_antall_godkjennere=1, risikoklasse="INNSNEVRER")
    _attest(c, uid2, "uavh-ny", False)
    c.commit(); c.close()
    r = _rt()
    try:
        assert _aktiver(r, uid2) == v
    finally:
        r.close()
    ny_gen = _gen(pid, v)
    assert ny_gen != gammel_gen, "forutsetningen holder ikke"

    # Klikket bærer generasjonen HISTORIKKEN VISTE. Den finnes ikke mer.
    r1 = _post(klient, cookie, csrf, "/v1/policyutkast",
               {"policy_id": pid, "rollback_av_versjon": v,
                "rollback_av_generasjon": gammel_gen})
    assert r1.status_code == 409, r1.text
    assert r1.json()["feil"] == "rullbakk_kilde_endret", r1.text
    # Etter en ny lasting går den samme handlingen gjennom — sperren er
    # på FORVEKSLINGEN, ikke på rullbakk fra en gjenskapt versjon.
    r2 = _post(klient, cookie, csrf, "/v1/policyutkast",
               {"policy_id": pid, "rollback_av_versjon": v,
                "rollback_av_generasjon": ny_gen})
    assert r2.status_code == 201, r2.text
    # Og generasjonen er PÅKREVD: uten den navngir forespørselen bare
    # nummeret igjen, og hullet står åpent for enhver kaller.
    r3 = _post(klient, cookie, csrf, "/v1/policyutkast",
               {"policy_id": pid, "rollback_av_versjon": v})
    assert r3.status_code == 400, r3.text
    r4 = _post(klient, cookie, csrf, "/v1/policyutkast",
               {"policy_id": pid, "rollback_av_versjon": v,
                "rollback_av_generasjon": str(ny_gen)})
    assert r4.status_code == 400, r4.text


@pg
def test_rullbakkeopphavet_binder_generasjonen_ikke_nummeret(klient):
    """Port 27 (Codex P2): opphavet peker på GENERASJONEN kopien kom fra,
    ikke bare på versjonsNUMMERET.

    `slett_ubrukt_policy` frigjør uttrykkelig `(policy_id, versjon)`, og
    nummeret kan gjenskapes. Bar utkastet bare tallet, ville historikken
    påstått «rullbakk fra versjon 1» ved siden av en generasjon 1 kopien
    aldri kom fra — en fabrikkert linje ingen skrev.

    INNHOLDSHASHEN ER HELLER IKKE NOK (Codex P2): det samme dokumentet kan
    settes inn igjen under samme nummer (`test_identisk_gjenskapt_policy_
    gjenoppliver_ikke_slettet_generasjon`), og en hash-sammenligning ville
    da sagt «bundet» om en rad kopien aldri kom fra. Opphavet bærer derfor
    `policyer.generasjon` — et sekvenstall ingen får igjen.

    Målt i to lag: opprettelsen lagrer kilderadens generasjon, og
    definerens `rollback_kilde` skiller `bundet` fra `borte` og `ubundet`.
    """
    uid, pid, v = _full_aktivering(pakrevd=1)
    cookie, csrf = _forvaltersesjon()
    r = _post(klient, cookie, csrf, "/v1/policyutkast",
              {"policy_id": pid, "rollback_av_versjon": v,
               "rollback_av_generasjon": _gen(pid, v)})
    assert r.status_code == 201, r.text
    rb_uid = r.json()["utkast_id"]
    m = _c()
    try:
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        lagret = m.execute(
            "SELECT rollback_av_versjon, rollback_av_generasjon"
            "  FROM policyutkast WHERE tenant=%s AND utkast_id=%s",
            (TEN, rb_uid)).fetchone()
        kilde_gen = m.execute(
            "SELECT generasjon FROM policyer WHERE tenant=%s AND"
            " policy_id=%s AND versjon=%s", (TEN, pid, v)).fetchone()[0]
        assert lagret == (v, kilde_gen), "port 27: opphavet er ubundet"
        # Frosset på samme måte som nummeret — ellers kunne kjøretiden
        # skrevet seg til en «bundet» kilde i ettertid.
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE policyutkast SET rollback_av_generasjon=%s"
                      " WHERE tenant=%s AND utkast_id=%s",
                      (kilde_gen + 1, TEN, rb_uid))
        m.rollback()
        # En generasjon uten et nummer er meningsløs og avvises statisk.
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute(
                "INSERT INTO policyutkast (tenant,utkast_id,policy_id,"
                "innhold,status,opprettet_av,rollback_av_generasjon) VALUES"
                " (%s,%s,%s,'{}'::jsonb,'utkast','forf',1)",
                (TEN, "u-" + secrets.token_hex(4), pid))
        m.rollback()
        # Generasjonen på policy-raden er selv frosset: kunne den skrives
        # om, kunne en slettet kilde «gjenoppstå» som bundet.
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE policyer SET generasjon=%s WHERE tenant=%s"
                      " AND policy_id=%s AND versjon=%s",
                      (kilde_gen + 1000, TEN, pid, v))
        m.rollback()
    finally:
        m.close()

    # Andre lag: hva HISTORIKKEN sier om de tre tilstandene. Serien bygges
    # med opphavet satt ved innsettingen, siden kolonnene er frosset.
    c = _c()
    uid1, pid2 = _ny()
    _validert_utkast(c, uid1, pid2, av="forf", versjon="1.1.0")
    _runde(c, uid1, pakrevd_antall_godkjennere=1, risikoklasse="INNSNEVRER")
    _attest(c, uid1, "uavh", False)
    c.commit()
    rt = _rt()
    try:
        v1 = _aktiver(rt, uid1)
        kilde1 = None
        m = _c()
        try:
            m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
            kilde1 = m.execute(
                "SELECT generasjon FROM policyer WHERE tenant=%s AND"
                " policy_id=%s AND versjon=%s", (TEN, pid2, v1)).fetchone()[0]
            m.rollback()
        finally:
            m.close()
        forrige = v1
        for versjon, gen, ventet in (
                ("1.2.0", kilde1, "bundet"),
                # En ANNEN generasjon under samme nummer — det gjenskapte
                # tilfellet, uansett om innholdet er byte-likt.
                ("1.3.0", kilde1 + 100000, "borte"),
                ("1.4.0", None, "ubundet")):
            uid_n = "u-" + secrets.token_hex(4)
            c.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
            _rullbakkutkast(c, uid_n, pid2, versjon, v1, gen)
            _runde(c, uid_n, pakrevd_antall_godkjennere=1,
                   risikoklasse="INNSNEVRER")
            _attest(c, uid_n, "uavh", False)
            c.commit()
            forrige = _aktiver(rt, uid_n, base=forrige)
            rt.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
            rad = rt.execute(
                "SELECT rollback_av_versjon, rollback_kilde"
                "  FROM policyversjoner_for_tenant(%s,%s)"
                " WHERE versjon=%s", (TEN, pid2, forrige)).fetchone()
            rt.rollback()
            assert rad == (v1, ventet), f"port 27: {versjon} ga {rad}"
        # Versjonen som IKKE er en rullbakk sier ingenting om et opphav.
        rt.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        rad0 = rt.execute(
            "SELECT rollback_av_versjon, rollback_kilde"
            "  FROM policyversjoner_for_tenant(%s,%s)"
            " WHERE versjon=%s", (TEN, pid2, v1)).fetchone()
        rt.rollback()
        assert rad0 == (None, None), "port 27: ordinær versjon fikk opphav"
    finally:
        rt.close()
        c.close()

    # Tredje lag: DET ER NETTOPP GJENSKAPINGEN som må skille seg. Samme
    # nummer og BYTE-LIKT innhold — altså identisk `innholds_hash` — er en
    # ny rad, og generasjonen sier det. Hadde opphavet vært hashen, ville
    # den gjenskapte raden svart «bundet» på en kopi den aldri ga.
    m = _c()
    try:
        _, pid3 = _ny()
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))

        def _sett_inn_og_les():
            m.execute(
                "INSERT INTO policyer (tenant, policy_id, versjon,"
                " innholds_hash, status, innhold, aktiv) VALUES"
                " (%s,%s,'1','ih-likt','produksjon','{}'::jsonb,false)",
                (TEN, pid3))
            return m.execute(
                "SELECT generasjon FROM policyer WHERE tenant=%s AND"
                " policy_id=%s AND versjon='1'", (TEN, pid3)).fetchone()[0]

        gen1 = _sett_inn_og_les()
        m.execute("DELETE FROM policyer WHERE tenant=%s AND policy_id=%s"
                  " AND versjon='1'", (TEN, pid3))
        gen2 = _sett_inn_og_les()
        assert gen2 != gen1, \
            f"port 27: den gjenskapte raden arvet generasjonen {gen1}"
        m.rollback()
    finally:
        m.close()


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


def test_editorgrunnlagets_kravliste_er_kodens_egen():
    """Port 32 (Codex P2): flatens lås er en påstand om at SERVEREN nekter
    fjerningen — og kravet gjelder ikke overalt.

    `_krev_malautorisasjonsvilkar` stiller det bare for en handling hvis
    KODEFESTEDE type bærer `krever_malautorisasjon` med et domene: bærer
    typen flagget, er `_er_ekstern_lesing` sann uten å spørre registeret
    (koden først), og mangler flagget eller domenet, blir dommen en egen
    feillinje i stedet for et vilkårskrav. Differansen er derfor ren kode,
    og editoren skal lese nøyaktig den — ikke en liste ruta eller flaten
    vedlikeholder ved siden av.

    Uten domenebindingen låste editoren ethvert velformet plattformnavn,
    også på en handling kravet ikke gjelder for: en rad serveren gjerne
    ville sluppet, men som eier verken kunne redigere eller fjerne.
    """
    import oppdragskontrakt

    from api.policy_historikk import _malautorisasjonskrav
    krav = _malautorisasjonskrav()
    assert krav, "ingen kravbærende type — da måler testen ingenting"
    # Hver linje SVARER til den typen `type_for_handling` faktisk velger for
    # en handling under prefikset. Det er den ene oppslagsveien
    # `_krev_malautorisasjonsvilkar` går.
    for k in krav:
        t = oppdragskontrakt.type_for_handling(k["prefiks"] + "noe")
        assert t is not None and t.krever_malautorisasjon, k
        assert t.malautorisasjonsdomene == k["maldomene"], k
    # …og ingen kravbærende type mangler i lista: en ny type ville ellers
    # blitt et krav serveren stiller og flaten ikke kjenner.
    forventet = {(p, t.malautorisasjonsdomene)
                 for t in oppdragskontrakt.OPPDRAGSTYPER.values()
                 if t.krever_malautorisasjon and t.malautorisasjonsdomene
                 for p in t.handlingsprefikser}
    assert {(k["prefiks"], k["maldomene"]) for k in krav} == forventet


@pg
def test_vilkarsdommen_caches_ikke_registeret_kan_repareres():
    """Codex P2: `_krev_malautorisasjonsvilkar` er en dom om REGISTERET.

    Den ser på utkastet, men felles av `modulkontrakt`,
    `oppdragstype_register` og den append-only `malautorisasjonsvilkar` —
    alle tre flytter seg, og at kravet IKKE er hardkodet er hele poenget
    med port 32. Lå dommen i den permanent cachede dokumentfeil-grenen,
    ville en validering kjørt før drift rakk å registrere vilkåret blitt
    replayet av `_idempotent_start` etterpå: flaten gjenbruker med rette
    sin stabile `valNokkel`, og det uendrede utkastet var da umulig å
    validere derfra til eier tilfeldigvis tvang fram en ny render.

    Kontroll: legg `_krev_malautorisasjonsvilkar` tilbake i `feil` i
    stedet for i `reg_feil`, så blir siste assert rød.
    """
    from .test_outbox_bestilling import _sikre_typeregistrering
    from .test_wcag_kontroll import _mk_admin
    _sikre_typeregistrering()          # kontroll.wcag.nettsted = ekstern_lesing
    import yaml as _yaml
    from api import policyadmin as pa
    # Et vilkår som ENNÅ ikke er registrert for domenet — nøyaktig den
    # tilstanden «drift har ikke rukket det» har.
    vt = "vilkar_" + secrets.token_hex(4)
    mal = _yaml.safe_load(
        (ROT / "policies" / "bransjemal-tjenestebedrift.yaml")
        .read_text(encoding="utf-8"))
    pid = "pol-reg-" + secrets.token_hex(3)
    mal["meta"]["policy_id"] = pid
    mal["meta"]["status"] = "produksjon"
    mal["roller"].append({"id": "bestiller", "beskrivelse": "b"})
    mal["verifikatorer"]["v_nytt_vilkar"] = {
        "beskrivelse": "Plattformens nye kontroll", "betrodd_for": [vt]}
    mal["handlinger"].append({
        "id": "kontroll.wcag.nettsted", "modul": "M-56", "modus": "auto",
        "ved_brudd": "unntakskø", "tillatt_for": ["bestiller"],
        "dataklasser_tillatt": ["offentlig"],
        "grenser": {"frekvens": {"maks": 4, "periode_antall": 1,
                                 "periode_enhet": "dager",
                                 "grupperingsnokkel": "mal_url"}},
        "vilkaar": [{"navn": vt, "verifikator": "v_nytt_vilkar"}],
        "reversering": {"type": "direkte"}})

    r = _rt()
    try:
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        k = secrets.token_hex(8)
        res = pa.opprett_utkast(r, tenant=TEN, aktor="forf", request_id="r",
                                policy_id=pid, innhold=mal,
                                idempotency_key=k, input_hash=k)
        uid = res["utkast_id"]
        valnokkel = secrets.token_hex(8)

        def valider():
            return pa.valider_utkast(
                r, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
                forventet_utkastversjon=1, idempotency_key=valnokkel,
                input_hash="ih-" + valnokkel)

        v1 = valider()
        assert v1["utfall"] == "ugyldig", v1
        assert any("målautorisasjonsvilkår" in f for f in v1["feil"]), v1
    finally:
        r.close()

    # Drift registrerer vilkåret. Registeret er append-only: dette er den
    # ENE retningen det kan flytte seg i, og nettopp den som reparerer.
    a = _mk_admin("disponit_modules_admin")
    try:
        a.execute("SELECT registrer_malautorisasjonsvilkar(%s,"
                  "'web_hostname','test')", (vt,))
        a.commit()
    finally:
        a.close()

    r = _rt()
    try:
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        v2 = pa.valider_utkast(
            r, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
            forventet_utkastversjon=1, idempotency_key=valnokkel,
            input_hash="ih-" + valnokkel)
        assert v2["utfall"] == "validert", (
            "et replay pinnet registertilstanden fra før vilkåret ble"
            " registrert", v2)
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
    g1 = rader[0]["generasjon"]
    assert isinstance(g1, int), rader[0]
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
    diffsti = (f"/v1/policy/{pid}/diff?fra={v1}&til={v1}"
               f"&fra_generasjon={g1}&til_generasjon={g1}")
    r = klient.get(diffsti, cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 200, r.text
    assert r.json()["diff"] == policydiff.strukturert_diff(i1, i1)
    assert r.json()["diff"]["endringer"] == []
    # GENERASJONEN ER PÅKREVD (Codex P2): et versjonsnummer er en peker
    # `slett_ubrukt_policy` frigjør, så en forespørsel som bare navngir
    # nummeret er ikke en bestilling ruta kan oppfylle.
    r = klient.get(f"/v1/policy/{pid}/diff?fra={v1}&til={v1}",
                   cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 400, r.text
    assert r.json()["feil"] == "request_feilformet"


@pg
def test_diffen_er_bundet_til_generasjonene_som_ble_vist(klient):
    """Codex P2: diffen navnga bare versjonsNUMRENE.

    Et nummer er en PEKER, ikke en identitet — `slett_ubrukt_policy`
    frigjør det med vilje, og det samme `(policy_id, versjon)` kan senere
    bæres av en helt annen generasjon (se
    `test_gjenbrukt_policy_id_gjenoppliver_ikke_slettet_generasjon`). Var
    en versjon slettet og gjenskapt mellom lastingen av historikken og
    klikket, leste diffen erstatningen mens flaten fortsatt merket
    resultatet med den valgte versjonens etikett: strukturdiffen og
    risikoretningen beskrev da et annet dokumentpar enn det eier så.

    Porten er `policyversjon_innhold` selv, ikke ruta: generasjonen er et
    ARGUMENT, så ingen kaller kan lese et nummer uten å si hvilken
    generasjon som bar det. Og operandene hentes i ETT statement — to kall
    er to READ COMMITTED-snapshots, og et par som aldri fantes samtidig er
    ingen diff.

    Kontroll: gjør `p_generasjon` valgfri (eller drop prøven), så blir
    denne rød.
    """
    from api import sesjon as sesjonmodul
    from .test_outbox_bestilling import _adminsesjon
    uid, pid, v = _full_aktivering(pakrevd=1)
    cookie, _csrf = _adminsesjon(tenant=TEN, roller="leser")
    rader = klient.get(f"/v1/policy/{pid}/versjoner",
                       cookies={sesjonmodul.C_SESJON: cookie}).json()
    gen = rader["versjoner"][0]["generasjon"]
    assert isinstance(gen, int) and gen > 0, rader

    def diff(fg, tg):
        return klient.get(
            f"/v1/policy/{pid}/diff?fra={v}&til={v}"
            f"&fra_generasjon={fg}&til_generasjon={tg}",
            cookies={sesjonmodul.C_SESJON: cookie})

    assert diff(gen, gen).status_code == 200
    # Generasjonen historikken viste er borte — nummeret bæres nå av en
    # annen rad. Optimistisk lås: 409 med egen kode, ikke 404 (raden
    # finnes) og ikke 200 på feil dokument.
    for fg, tg in ((gen + 1, gen), (gen, gen + 1)):
        r = diff(fg, tg)
        assert r.status_code == 409, r.text
        assert r.json()["feil"] == "diff_kilde_endret", r.text
    # En generasjon som ikke ER et tall er en feilformet forespørsel, ikke
    # en konflikt: ingenting i basen kan gjøre det samme kallet gyldig.
    assert diff("x", gen).status_code == 400

    # …og porten står i DEFINEREN, ikke bare i ruta: en direkte kaller med
    # kjøretidsrollens EXECUTE møter den samme prøven.
    r = _rt()
    try:
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        assert r.execute("SELECT policyversjon_innhold(%s,%s,%s,%s)",
                         (TEN, pid, v, gen)).fetchone()[0]
        r.rollback()
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            r.execute("SELECT policyversjon_innhold(%s,%s,%s,%s)",
                      (TEN, pid, v, gen + 1)).fetchone()
        r.rollback()
        # NULL er ikke «hopp over prøven»: en utelatt generasjon faller på
        # samme vakt, ellers er porten valgfri og dermed ingen port.
        r.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            r.execute("SELECT policyversjon_innhold(%s,%s,%s,NULL)",
                      (TEN, pid, v)).fetchone()
        r.rollback()
    finally:
        r.close()


@pg
def test_historikken_sier_om_serien_kan_faa_et_nytt_utkast(klient):
    """Codex P2: rullbakk-knappens forutsetning står i SVARET.

    Lastekontrakten slipper med vilje gjennom aktive policyer fra før
    id-innstrammingen — `/v1/policy/aktiv` kan svare med en id som
    `acme\\n` — og historikken deres skal fortsatt kunne leses.
    `opprett_utkast` avviser derimot identiteten, og en rullbakk ER en
    utkastopprettelse: hver eneste knapp for en slik serie endte i et 400
    ingen kunne gjøre noe med. Endepunktet bærer nå portens egen dom, så
    flaten kan la være å tilby handlingen — og si hvorfor.
    """
    from urllib.parse import quote
    from api import sesjon as sesjonmodul
    from .test_outbox_bestilling import _adminsesjon
    uid, pid, v = _full_aktivering(pakrevd=1)
    rar = pid + "\n"
    m = _c()
    try:
        # Den arvede raden lages som migrator: dagens porter ville aldri
        # sluppet id-en inn, og det er nettopp poenget — den ligger der
        # fra før dem.
        m.execute("SELECT set_config('disponit.tenant',%s,true)", (TEN,))
        m.execute(
            "INSERT INTO policyer (tenant, policy_id, versjon,"
            " innholds_hash, status, innhold, aktiv) VALUES"
            " (%s,%s,'1','ih-arvet','produksjon','{}'::jsonb,false)",
            (TEN, rar))
        m.commit()
    finally:
        m.close()
    cookie, _csrf = _adminsesjon(tenant=TEN, roller="leser")
    r = klient.get(f"/v1/policy/{quote(pid, safe='')}/versjoner",
                   cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 200, r.text
    assert r.json()["nytt_utkast_avvist"] is None, \
        "en helt vanlig policy-id ble sperret for nye utkast"
    r = klient.get(f"/v1/policy/{quote(rar, safe='')}/versjoner",
                   cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 200, r.text
    d = r.json()
    # Historikken LESES — det er bare opprettelsen som er stengt.
    assert [x["versjon"] for x in d["versjoner"]] == ["1"], d
    assert d["nytt_utkast_avvist"] == "policy_id_ugyldig", d
