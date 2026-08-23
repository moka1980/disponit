"""M-57s kandidatlagre og kandidatdatagrensen (057) — klarsignalet §5.

Portene 18–20: etter reaping finnes fixture-strengen i NULL av de seks
lagrene (18); reaperen tømmer aldri ett lager alene, og settet av lagre
er målt mot katalogen, ikke mot en liste i testen (19); fristen kan ikke
forlenges av noen — ikke modulen, ikke runtime, ikke eieren (20).

Alle tester konstruerer egen tilstand; ingen delt fixture. Tidskontroll
skjer gjennom `lukk_rekrutteringsprosess` sitt `p_lukket_ts`, som BARE
kan peke bakover — å tidlegge lukkingen korter fristen, og det er den
eneste retningen som finnes (forlengelses-forsøket er sin egen port).
"""
from __future__ import annotations

import secrets
import uuid

import psycopg
import pytest

from .test_api import (ANNEN_TENANT, DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       migrator, miljo)
from .test_m37 import _sett_kontekst
from .test_m57_utsending import _evaluering, _grunnlag, _rt, pg

FIXTUR = "KANDIDATFIXTUR-" + secrets.token_hex(6)

#: De seks lagrene og payloadkolonnene deres — SPEILET i testens egen
#: pinning, men fasiten måles mot katalogen (port 19b), så et syvende
#: lager uten reap-dekning feller testen, ikke listen her.
LAGRE = {
    "kandidat_originaldokument": ("dokument", "filnavn", "innholdstype"),
    "kandidat_parsettekst": ("tekst",),
    "kandidat_evalueringsartefakt": ("artefakt",),
    "kandidat_intervjusporsmal": ("sporsmal",),
    "kandidat_utsendingsdata": ("mottaker_ref", "flettefelt"),
    "kandidat_avmaskering": ("felter",),
}


def _prosess(m, rt, *, frist=90):
    """Evalueringsoppdrag + prosess, gjennom den herdede veien. Setter
    konteksten selv — `krev_tenantkontekst` binder parameteret dit."""
    oid, _ = _evaluering(m)
    _sett_kontekst(rt, TENANT)
    pid = rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,%s)",
                     (TENANT, oid, frist)).fetchone()[0]
    return oid, pid


def _fyll_lagrene(rt, pid, kandidat=None):
    """Én kandidat med fixture-strengen i payloaden i ALLE seks lagre."""
    kid = kandidat or uuid.uuid4()
    did = uuid.uuid4()
    sha = secrets.token_hex(32)
    rt.execute(
        "INSERT INTO kandidat_originaldokument (tenant, prosess_id,"
        " kandidat_id, dokument_id, filnavn, innholdstype, dokument,"
        " storrelse_bytes, innhold_sha256) VALUES"
        " (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (TENANT, pid, kid, did, f"{FIXTUR}.pdf",
         f"application/pdf; kandidat={FIXTUR}", FIXTUR.encode(),
         len(FIXTUR.encode()), sha))
    rt.execute(
        "INSERT INTO kandidat_parsettekst (tenant, prosess_id,"
        " kandidat_id, dokument_id, tekst, innhold_sha256)"
        " VALUES (%s,%s,%s,%s,%s,%s)",
        (TENANT, pid, kid, did, f"CV-tekst {FIXTUR}", sha))
    rt.execute(
        "INSERT INTO kandidat_evalueringsartefakt (tenant, prosess_id,"
        " kandidat_id, artefakt, innhold_sha256)"
        " VALUES (%s,%s,%s,%s,%s)",
        (TENANT, pid, kid, f'{{"funn": "{FIXTUR}"}}', sha))
    rt.execute(
        "INSERT INTO kandidat_intervjusporsmal (tenant, prosess_id,"
        " kandidat_id, sporsmal, innhold_sha256)"
        " VALUES (%s,%s,%s,%s,%s)",
        (TENANT, pid, kid, f'["Hva mente du med {FIXTUR}?"]', sha))
    rt.execute(
        "INSERT INTO kandidat_utsendingsdata (tenant, prosess_id,"
        " kandidat_id, mottaker_ref, flettefelt, innhold_sha256)"
        " VALUES (%s,%s,%s,%s,%s,%s)",
        (TENANT, pid, kid, f"{FIXTUR}@example.org",
         f'{{"navn": "{FIXTUR}"}}', sha))
    rt.execute(
        "INSERT INTO kandidat_avmaskering (tenant, prosess_id,"
        " kandidat_id, felter, innhold_sha256)"
        " VALUES (%s,%s,%s,%s,%s)",
        (TENANT, pid, kid, f'{{"[NAVN-1]": "{FIXTUR}"}}', sha))
    return kid


def _reaperkobling():
    """(kobling, timerrolle) for `reap_kandidatdata` — 038-formen, som
    057 nå speiler ordrett (Codex P1): finnes `disponit_domener`, EIER den
    reaperen og runtime er NEKTET den. Testen deler koblingsvalget med
    evidensreaperen i stedet for å anta lokal-oppsettet — å anta det gjorde
    038-testene stille røde på verten, der `InsufficientPrivilege` er
    grantet som VIRKER, ikke en feil."""
    from .test_outbox_bestilling import _reaperkobling as felles
    return felles()


def _tell_fixtur(m, pid):
    """Antall payloadfelter over ALLE seks lagre som fortsatt bærer
    fixture-strengen — målt kolonne for kolonne, ikke via en visning
    reaperen kunne dele feil med."""
    _sett_kontekst(m, TENANT)
    treff = 0
    for tabell, kolonner in LAGRE.items():
        for kol in kolonner:
            uttrykk = (f"convert_from({kol}, 'UTF8')"
                       if (tabell, kol) ==
                       ("kandidat_originaldokument", "dokument")
                       else f"{kol}::text")
            treff += m.execute(
                f"SELECT count(*) FROM {tabell}"
                f" WHERE tenant=%s AND prosess_id=%s AND {kol} IS NOT NULL"
                f" AND {uttrykk} LIKE %s",
                (TENANT, pid, f"%{FIXTUR}%")).fetchone()[0]
    return treff


@pg
def test_prosessen_krever_evalueringsoppdrag(migrator):
    """Porten i `opprett_rekrutteringsprosess`: et oppdrag av en annen
    type får ingen kandidatprosess."""
    rt = _rt()
    try:
        oid, _ = _grunnlag(migrator, oppdragstype="wcag.kontroll")
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                       (TENANT, oid))
        rt.rollback()
        # Positiv kontroll i samme test: den riktige typen går.
        oid2, _ = _evaluering(migrator)
        _sett_kontekst(rt, TENANT)
        pid = rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                         (TENANT, oid2)).fetchone()[0]
        assert pid is not None
        rt.commit()
    finally:
        rt.close()


@pg
def test_prosessen_er_idempotent_men_fristen_er_materiell(migrator):
    """Samme oppdrag + samme frist → samme prosess. Samme oppdrag + NY
    frist → konflikt: «opprett på nytt» er ikke en vei rundt §5."""
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        oid, pid = _prosess(migrator, rt, frist=45)
        pid2 = rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,45)",
                          (TENANT, oid)).fetchone()[0]
        assert pid2 == pid
        with pytest.raises(psycopg.errors.UniqueViolation):
            rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,180)",
                       (TENANT, oid))
        rt.rollback()
    finally:
        rt.close()


@pg
def test_opprett_prosess_er_idempotent_under_kapplop(migrator):
    """Codex P2: SELECT-så-INSERT lot kappløpstaperen dø på unik-bruddet.
    Nå får taperen vinnerens prosess-id: A setter inn uten å committe;
    B (egen tilkobling) kaller funksjonen og blokkerer på indeksen til A
    committer — og skal da returnere A-radens id, ikke `unique_violation`.
    Samme form som `test_frigi_er_idempotent_under_kapplop` (056)."""
    import threading

    oid, _ = _evaluering(migrator)
    a = _rt()
    b = _rt()
    try:
        _sett_kontekst(a, TENANT)
        pid_a = a.execute("SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                          (TENANT, oid)).fetchone()[0]
        resultat: dict = {}

        def taper():
            _sett_kontekst(b, TENANT)
            try:
                resultat["pid"] = b.execute(
                    "SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                    (TENANT, oid)).fetchone()[0]
                b.commit()
            except Exception as feil:            # pragma: no cover
                resultat["feil"] = feil

        t = threading.Thread(target=taper)
        t.start()
        t.join(timeout=2)
        assert t.is_alive(), "B skulle blokkere på As ucommittede rad"
        a.commit()
        t.join(timeout=10)
        assert not t.is_alive(), "B kom aldri gjennom etter As commit"
        assert "feil" not in resultat, resultat.get("feil")
        assert resultat["pid"] == pid_a, "taperen fikk en ANNEN prosess"
    finally:
        a.close(); b.close()


@pg
def test_fristen_utenfor_spennet_avvises(migrator):
    """§4: 30–365 døgn. Begge kantene utenfor felles av CHECK-en —
    og begge kantene INNENFOR går (grensetesten måler grensen, ikke
    bare midten)."""
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        for frist in (29, 366, 0, -1):
            oid, _ = _evaluering(migrator)
            _sett_kontekst(rt, TENANT)
            with pytest.raises(psycopg.errors.CheckViolation):
                rt.execute(
                    "SELECT opprett_rekrutteringsprosess(%s,%s,%s)",
                    (TENANT, oid, frist))
            rt.rollback()
        for frist in (30, 365):
            oid, _ = _evaluering(migrator)
            _sett_kontekst(rt, TENANT)
            rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,%s)",
                       (TENANT, oid, frist))
        rt.commit()
    finally:
        rt.close()


@pg
def test_port20_fristen_kan_ikke_forlenges_av_noen(migrator):
    """Porten gjelder også EIEREN: et UPDATE på `slettefrist_dogn` er
    trigger-avvist uansett rolle — verifisert, ikke antatt."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt, frist=30)
        rt.commit()
        _sett_kontekst(migrator, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "UPDATE rekrutteringsprosess SET slettefrist_dogn=365"
                " WHERE tenant=%s AND prosess_id=%s", (TENANT, pid))
        migrator.rollback()
        # ... og runtime har ikke engang UPDATE-rettigheten (statisk).
        for tabell in ("rekrutteringsprosess", *LAGRE):
            for priv in ("UPDATE", "DELETE"):
                har = migrator.execute(
                    "SELECT has_table_privilege('disponit', %s, %s)",
                    (tabell, priv)).fetchone()[0]
                assert har is False, (tabell, priv)
        migrator.rollback()
    finally:
        rt.close()


@pg
def test_ankeret_fodes_kun_gjennom_funksjonen(migrator):
    """Codex P1: radvakten er BEFORE UPDATE OR DELETE og ser ingen fødsel.
    Et tabell-INSERT på `rekrutteringsprosess` ville derfor gått utenom
    BEGGE portene i `opprett_rekrutteringsprosess` — oppdragstypen og
    «lukket_ts aldri frem i tid» (som ville skjøvet hele slettefristen).
    Runtime har derfor KUN SELECT på ankeret; lagrene beholder INSERT."""
    har = migrator.execute(
        "SELECT has_table_privilege('disponit', 'rekrutteringsprosess',"
        " 'INSERT')").fetchone()[0]
    assert har is False, "runtime kan føde en prosess utenom funksjonen"
    for tabell in LAGRE:
        assert migrator.execute(
            "SELECT has_table_privilege('disponit', %s, 'INSERT')",
            (tabell,)).fetchone()[0] is True, tabell
    migrator.rollback()
    # ... og forsøket feller i praksis, ikke bare i katalogen.
    rt = _rt()
    try:
        oid, _ = _evaluering(migrator)
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute(
                "INSERT INTO rekrutteringsprosess (tenant, prosess_id,"
                " oppdrag_id, slettefrist_dogn, lukket_ts)"
                " VALUES (%s,%s,%s,365, now() + interval '3650 days')",
                (TENANT, uuid.uuid4(), oid))
        rt.rollback()
    finally:
        rt.close()


def test_057_rettighetene_er_parameterisert_pa_rollenavnet():
    """Cursor P1 (samme form som gate14b port 18): migrasjonens `TO
    disponit` er test/lokal-fallbacken — driftssannheten står i den
    parameteriserte blokken i `migrer.py`. Uten EXECUTE der får en
    installasjon med et annet runtime-rollenavn `permission denied` på
    prosessfødselen og på lukkingen (som starter slettefristen).

    Reaperen skal IKKE stå der: den er kryss-tenant og hører til
    timerrollen — 038-formen, betinget DO-blokk i migrasjonen."""
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    kjorer = (rot / "deploy" / "staging"
              / "migrer.py").read_text(encoding="utf-8")
    for sign in ("opprett_rekrutteringsprosess(TEXT, BIGINT, INT)",
                 "lukk_rekrutteringsprosess(TEXT, UUID, TIMESTAMPTZ)"):
        assert f"GRANT EXECUTE ON FUNCTION {sign} TO {{rolle}}" in kjorer, \
            sign
    assert "GRANT EXECUTE ON FUNCTION reap_kandidatdata" not in kjorer, \
        "kryss-tenant-reaperen lekker til en parameterisert rolle"
    # Tabellspeilet: ankeret KUN SELECT, lagrene SELECT + INSERT.
    assert "GRANT SELECT ON rekrutteringsprosess TO {rolle};" in kjorer
    assert ("GRANT SELECT, INSERT ON rekrutteringsprosess"
            not in kjorer), "runtime får INSERT på ankeret i kjøreren"


@pg
def test_port20_lukkingen_kan_ikke_sta_frem_i_tid(migrator):
    """Fristen løper fra lukkingen — en lukking frem i tid ville
    forlenget den. Bakover korter den bare, og går."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        rt.commit()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute(
                "SELECT lukk_rekrutteringsprosess(%s,%s,"
                " now() + interval '1 day')", (TENANT, pid))
        rt.rollback()
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '1 hour')", (TENANT, pid))
        # ... og en satt lukking flyttes ikke (heller ikke bakover:
        # enda tidligere ville endret et løp som alt er i gang).
        with pytest.raises(psycopg.errors.UniqueViolation):
            rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                       " now() - interval '2 hour')", (TENANT, pid))
        rt.rollback()
    finally:
        rt.close()


@pg
def test_port18_reaping_tommer_alle_seks_lagrene(migrator):
    """Kjerneporten: fixture-strengen står i payloaden i alle seks lagre
    FØR reaping (positiv kontroll — en fraværstest uten den går grønn på
    søppel), og i NULL av dem etter. Radene består med hash og
    `slettet_ts` — minimal revisjonsevidens, ikke sporløshet."""
    rt = _rt()
    rp = None
    try:
        _, pid = _prosess(migrator, rt, frist=30)
        _fyll_lagrene(rt, pid)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '31 days')", (TENANT, pid))
        rt.commit()
        assert _tell_fixtur(migrator, pid) == 9, \
            "positiv kontroll: fixturen skal stå i alle payloadfeltene"
        migrator.rollback()
        rp, timerrolle = _reaperkobling()
        if timerrolle:
            # Sikkerhetsegenskapen 038/057 begrunner grantet med: en
            # kompromittert web-API-rolle skal ikke kunne trigge
            # retensjonsarbeid på tvers av alle tenanter.
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rt.execute("SELECT * FROM reap_kandidatdata(50)")
            rt.rollback()
        reapet = rp.execute("SELECT * FROM reap_kandidatdata(50)"
                            ).fetchall()
        rp.commit()
        assert (TENANT, pid) in [(r[0], r[1]) for r in reapet]
        assert _tell_fixtur(migrator, pid) == 0
        # Radene og revisjonsevidensen består — i alle seks + ankeret.
        for tabell in LAGRE:
            rad = migrator.execute(
                f"SELECT count(*), count(*) FILTER (WHERE slettet_ts IS"
                f" NOT NULL), count(*) FILTER (WHERE innhold_sha256 IS"
                f" NOT NULL) FROM {tabell}"
                f" WHERE tenant=%s AND prosess_id=%s",
                (TENANT, pid)).fetchone()
            assert rad[0] >= 1 and rad[0] == rad[1] == rad[2], tabell
        assert migrator.execute(
            "SELECT slettet_ts IS NOT NULL FROM rekrutteringsprosess"
            " WHERE tenant=%s AND prosess_id=%s",
            (TENANT, pid)).fetchone()[0]
        migrator.rollback()
    finally:
        rt.close()
        if rp is not None:
            rp.close()


@pg
def test_reaping_respekterer_fristen(migrator):
    """Motstykket til port 18: en prosess som er lukket, men der fristen
    IKKE er løpt ut, røres ikke — et reap-kall er ikke en sletteknapp."""
    rt = _rt()
    rp = None
    try:
        _, pid = _prosess(migrator, rt, frist=30)
        _fyll_lagrene(rt, pid)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '29 days')", (TENANT, pid))
        rt.commit()
        rp, _timerrolle = _reaperkobling()
        reapet = rp.execute("SELECT * FROM reap_kandidatdata(50)"
                            ).fetchall()
        rp.commit()
        assert (TENANT, pid) not in [(r[0], r[1]) for r in reapet]
        assert _tell_fixtur(migrator, pid) == 9
        migrator.rollback()
        # ... og en ÅPEN prosess som ennå er innenfor levetiden røres
        # ikke (den forlatte, som HAR passert den, er sin egen test).
        _, pid2 = _prosess(migrator, rt, frist=30)
        _fyll_lagrene(rt, pid2)
        rt.commit()
        rp.execute("SELECT * FROM reap_kandidatdata(50)")
        rp.commit()
        assert _tell_fixtur(migrator, pid2) == 9
        migrator.rollback()
    finally:
        rt.close()
        if rp is not None:
            rp.close()


@pg
def test_port18_insert_etter_reap_avvises(migrator):
    """Codex P1: FK-en krever bare at prosessen FINNES, og reaperen
    utelukker for alltid en prosess med `slettet_ts`. Uten en INSERT-vakt
    kunne en forsinket eller retriet skriver derfor gjenoppstå persondata
    på en reapet prosess — uten noen vei til å slette dem igjen."""
    rt = _rt()
    rp = None
    try:
        _, pid = _prosess(migrator, rt, frist=30)
        _fyll_lagrene(rt, pid)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '31 days')", (TENANT, pid))
        rt.commit()
        rp, _timerrolle = _reaperkobling()
        rp.execute("SELECT * FROM reap_kandidatdata(50)")
        rp.commit()
        assert _tell_fixtur(migrator, pid) == 0
        migrator.rollback()
        # Den forsinkede skriveren, på hvert eneste lager.
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _fyll_lagrene(rt, pid)
        rt.rollback()
        for tabell in LAGRE:
            _sett_kontekst(rt, TENANT)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rt.execute(
                    f"INSERT INTO {tabell} (tenant, prosess_id,"
                    f" kandidat_id, innhold_sha256) VALUES (%s,%s,%s,'0')",
                    (TENANT, pid, uuid.uuid4()))
            rt.rollback()
        assert _tell_fixtur(migrator, pid) == 0
        migrator.rollback()
        # Positiv kontroll: en ÅPEN, ikke-reapet prosess tar imot payload
        # — vakten er en reap-port, ikke en skrivesperre.
        _, pid2 = _prosess(migrator, rt)
        _fyll_lagrene(rt, pid2)
        rt.commit()
        assert _tell_fixtur(migrator, pid2) == 9
        migrator.rollback()
    finally:
        rt.close()
        if rp is not None:
            rp.close()


@pg
def test_port19_settet_av_lagre_er_maalt_mot_katalogen(migrator):
    """Port 19s virkelige form: «alle seks» er ikke en liste noen husker,
    men en MÅLING. Fasiten er katalogens — hver tabell med FK til
    `rekrutteringsprosess` og nullable payload — og reaperens kildekode
    må nevne hver av dem. Et syvende kandidatlager uten reap-dekning
    feller denne, ikke en kodegjennomgang måneder senere."""
    fk_tabeller = {
        r[0] for r in migrator.execute(
            "SELECT c.conrelid::regclass::text FROM pg_constraint c"
            " WHERE c.confrelid = 'rekrutteringsprosess'::regclass"
            "   AND c.contype = 'f'").fetchall()}
    fk_tabeller |= {
        r[0] for r in migrator.execute(
            "SELECT c.conrelid::regclass::text FROM pg_constraint c"
            " WHERE c.confrelid = 'kandidat_originaldokument'::regclass"
            "   AND c.contype = 'f'").fetchall()}
    fk_tabeller.discard("kandidat_originaldokument")
    assert fk_tabeller | {"kandidat_originaldokument"} == set(LAGRE), \
        "kandidatlagrene i katalogen er ikke testens seks"
    kilde = migrator.execute(
        "SELECT pg_get_functiondef('reap_kandidatdata(int)'::regprocedure)"
    ).fetchone()[0]
    for tabell, kolonner in LAGRE.items():
        assert tabell in kilde, f"reaperen nevner ikke {tabell}"
        for kol in kolonner:
            assert f"{kol} = NULL" in kilde, \
                f"reaperen nuller ikke {tabell}.{kol}"
    migrator.rollback()


@pg
def test_port19_reapet_prosess_har_ingen_halvtomte_lagre(migrator):
    """Invarianten et delvis reap ville brutt: for en prosess med
    `slettet_ts` finnes ingen levende lagerrad. Målt over hele basen —
    dagens OG gårsdagens reap-kjøringer."""
    for tabell in LAGRE:
        halve = migrator.execute(
            f"SELECT count(*) FROM {tabell} k"
            f" JOIN rekrutteringsprosess p USING (tenant, prosess_id)"
            f" WHERE p.slettet_ts IS NOT NULL AND k.slettet_ts IS NULL"
        ).fetchone()[0]
        assert halve == 0, tabell
    migrator.rollback()


@pg
def test_lagervakten_avviser_alt_annet_enn_reap_overgangen(migrator):
    """Append-only med ETT unntak: payload → NULL + slettet_ts satt, i
    samme setning. Innholdsendring, hashendring og DELETE avvises — også
    for eieren."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        kid = _fyll_lagrene(rt, pid)
        rt.commit()
        _sett_kontekst(migrator, TENANT)
        for setning in (
                "UPDATE kandidat_parsettekst SET tekst='omskrevet'"
                " WHERE tenant=%s AND prosess_id=%s",
                "UPDATE kandidat_parsettekst SET tekst=NULL"
                " WHERE tenant=%s AND prosess_id=%s",  # payload uten merke
                "UPDATE kandidat_parsettekst SET innhold_sha256='0',"
                " tekst=NULL, slettet_ts=now()"
                " WHERE tenant=%s AND prosess_id=%s",  # evidens endret
                "DELETE FROM kandidat_parsettekst"
                " WHERE tenant=%s AND prosess_id=%s"):
            # Konteksten er transaksjonslokal og dør i forrige rollback —
            # uten den treffer setningen null rader og «består» tomt.
            _sett_kontekst(migrator, TENANT)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                migrator.execute(setning, (TENANT, pid))
            migrator.rollback()
        # Positiv kontroll: selve reap-overgangen GÅR for eieren …
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "UPDATE kandidat_parsettekst SET tekst=NULL, slettet_ts=now()"
            " WHERE tenant=%s AND prosess_id=%s AND kandidat_id=%s",
            (TENANT, pid, kid))
        # … og en reapet rad er død: ny UPDATE avvises.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "UPDATE kandidat_parsettekst SET slettet_ts=now()"
                " WHERE tenant=%s AND prosess_id=%s AND kandidat_id=%s",
                (TENANT, pid, kid))
        migrator.rollback()
    finally:
        rt.close()


@pg
def test_enkeltfilgrensen_star_i_basen(migrator):
    """§4: 25 MB per fil — også som CHECK, ikke bare i parseren, og målt
    på de LAGREDE bytene (Codex P2): en påstand om størrelsen er ikke en
    måling av den, så `storrelse_bytes` er bundet til `octet_length`."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.CheckViolation):
            rt.execute(
                "INSERT INTO kandidat_originaldokument (tenant,"
                " prosess_id, kandidat_id, dokument_id, filnavn,"
                " innholdstype, dokument, storrelse_bytes,"
                " innhold_sha256) VALUES (%s,%s,%s,%s,'a.pdf','x',"
                " %s, 26*1024*1024, '0')",
                (TENANT, pid, uuid.uuid4(), uuid.uuid4(), b"x"))
        rt.rollback()
        # ... og en LØGN om størrelsen er like avvist: `storrelse_bytes`
        # på 1 med et dokument på 32 byte er ikke en 1-bytes fil.
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.CheckViolation):
            rt.execute(
                "INSERT INTO kandidat_originaldokument (tenant,"
                " prosess_id, kandidat_id, dokument_id, filnavn,"
                " innholdstype, dokument, storrelse_bytes,"
                " innhold_sha256) VALUES (%s,%s,%s,%s,'a.pdf','x',"
                " %s, 1, '0')",
                (TENANT, pid, uuid.uuid4(), uuid.uuid4(), b"x" * 32))
        rt.rollback()
        # Positiv kontroll: sann størrelse går.
        _sett_kontekst(rt, TENANT)
        rt.execute(
            "INSERT INTO kandidat_originaldokument (tenant,"
            " prosess_id, kandidat_id, dokument_id, filnavn,"
            " innholdstype, dokument, storrelse_bytes,"
            " innhold_sha256) VALUES (%s,%s,%s,%s,'a.pdf','x',"
            " %s, 32, '0')",
            (TENANT, pid, uuid.uuid4(), uuid.uuid4(), b"x" * 32))
        rt.rollback()
    finally:
        rt.close()


@pg
def test_kandidatlagrene_er_tenantisolert(migrator):
    """RLS-porten: en annen tenants kontekst ser ingen rader, og en
    INSERT på fremmed tenant avvises."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        _fyll_lagrene(rt, pid)
        rt.commit()
        _sett_kontekst(rt, ANNEN_TENANT)
        assert rt.execute(
            "SELECT count(*) FROM kandidat_parsettekst WHERE prosess_id=%s",
            (pid,)).fetchone()[0] == 0
        with pytest.raises(psycopg.errors.Error):
            rt.execute(
                "INSERT INTO kandidat_avmaskering (tenant, prosess_id,"
                " kandidat_id, felter, innhold_sha256)"
                " VALUES (%s,%s,%s,'{}','0')",
                (TENANT, pid, uuid.uuid4()))
        rt.rollback()
    finally:
        rt.close()
