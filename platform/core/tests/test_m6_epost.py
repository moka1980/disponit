"""M-6 PR-A (fundamentet, migrasjon 088) — planens §8-porter.

  1. Innhentingsidempotensen: duplikat `leverandor_melding_id` → ÉN rad
     (UNIQUE + ON CONFLICT, målt med direkte INSERT-rigg).
  2. Credentials: et direkte SELECT gir bare ciphertext — det finnes
     ingen klartekst-kolonne å lese.
  3. `slettefrist_dogn` er immutabel etter INSERT — også for eieren
     (057 port 20-formen), og `mottatt_ts` (fristens andre ende) med.
  4. Reaping: payload i ALLE lagre før, i NULL av dem etter — og ett
     lager alene er rødt ved COMMIT (057 port 18/19-invariantene, målt
     på ankernivå i 076/#163-formen: markører på barna, ÉN utsatt port
     på meldingen).
  5. Statisk: modulen har ingen sendevei (SMTP/Graph-write) og ingen
     direkte INSERT mot oppdrag/revisjonslogg.
  6. `epost.behandling` uten beslutning → ingen oppdrag (samme
     opphavsport som alle andre; regresjon på eksisterende opprinnelser
     bor i test_outbox_bestilling).
  7. Migrasjonen er grønn og byte-bundet i denne basen, og er REN DDL —
     ingen masse-DML som kan køe utsatte hendelser mot bebodd tilstand
     (047-klassen), så «begge kjøringer» (tom + bebodd) hviler ikke på
     et SP-10-seed den ikke har.

Alle DB-tester konstruerer egen tilstand; ingen delt fixture.
Grensetestene for `m6-v1` (planens §7-invarianter) og kjører-/
rettighetsspeilene ligger nederst.
"""
from __future__ import annotations

import re
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       migrator, miljo)
from .test_m37 import _sett_kontekst

ROT = Path(__file__).resolve().parents[3]
MODULROT = ROT / "platform" / "modules" / "m06_epost"
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "088_m6_epost.sql")

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

FIXTUR = "EPOSTFIXTUR-" + secrets.token_hex(6)
HEMMELIG = "refresh-token-" + secrets.token_hex(8)

#: Payload-lagrene og payloadkolonnene deres — testens egen pinning av
#: 088-formen (057-testens `LAGRE`-form). Meldingen er sitt eget
#: frist-anker OG det første lageret.
LAGRE = {
    "epost_melding": ("kropp_kryptert", "nonce", "key_id"),
    "epost_klassifisering": ("sammendrag_kryptert", "nonce", "key_id"),
    "epost_utkast": ("tekst_kryptert", "nonce", "key_id"),
    "epost_vedlegg": ("navn_kryptert", "nonce", "key_id"),
}


def _rt():
    from db.pg import koble
    return koble(DSN)


def _reaperkobling():
    """(kobling, timerrolle) for `reap_epostdata` — deler koblingsvalget
    med evidensreaperen (038/057-formen): finnes `disponit_domener`,
    EIER den reaperen og runtime er NEKTET den."""
    from .test_outbox_bestilling import _reaperkobling as felles
    return felles()


def _dek(m):
    from db import kryptering
    _sett_kontekst(m, TENANT)
    return kryptering.hent_eller_opprett_aktiv_dek(m, TENANT)


def _kilde(m):
    """Én tilkoblet postboks, credentials kryptert med tenant-DEK.
    Direkte INSERT som eier — PR-A har ingen dører (OAuth er PR-B)."""
    from db import kryptering
    key_id, dek = _dek(m)
    ct, nonce = kryptering.krypter(dek, {"refresh_token": HEMMELIG},
                                   TENANT, key_id)
    kid = m.execute(
        "INSERT INTO epost_kilde (tenant, leverandor, postboks,"
        " auth_kryptert, nonce, key_id) VALUES"
        " (%s,'m365',%s,%s,%s,%s) RETURNING kilde_id",
        (TENANT, f"postboks-{secrets.token_hex(4)}@example.org",
         ct, nonce, key_id)).fetchone()[0]
    return kid, key_id, dek


def _melding(m, kid, key_id, dek, *, dager_siden=0, frist=90,
             lev_id=None):
    from db import kryptering
    _sett_kontekst(m, TENANT)
    ct, nonce = kryptering.krypter(
        dek, {"kropp": f"Hei, dette er {FIXTUR}"}, TENANT, key_id)
    mid = m.execute(
        "INSERT INTO epost_melding (tenant, kilde_id,"
        " leverandor_melding_id, mottatt_ts, retning, avsender_hash,"
        " emne_hash, kropp_kryptert, nonce, key_id, slettefrist_dogn)"
        " VALUES (%s,%s,%s, now() - %s * interval '1 day', 'inn',"
        " %s,%s,%s,%s,%s,%s) RETURNING melding_id",
        (TENANT, kid, lev_id or f"AAMk-{secrets.token_hex(8)}",
         dager_siden, secrets.token_hex(32), secrets.token_hex(32),
         ct, nonce, key_id, frist)).fetchone()[0]
    return mid


def _fyll_lagrene(m, mid, key_id, dek):
    """Fixture-strengen inn i ALLE tre barnelagrene (kroppen bærer den
    alt fra `_melding`)."""
    from db import kryptering
    _sett_kontekst(m, TENANT)
    ct, nonce = kryptering.krypter(dek, {"sammendrag": FIXTUR},
                                   TENANT, key_id)
    m.execute(
        "INSERT INTO epost_klassifisering (tenant, melding_id, prioritet,"
        " handlingstype, sammendrag_kryptert, nonce, key_id,"
        " modell_digest) VALUES (%s,%s,'normal','til_info',%s,%s,%s,%s)",
        (TENANT, mid, ct, nonce, key_id, "sha256:" + "0" * 64))
    ct, nonce = kryptering.krypter(dek, {"tekst": f"Utkast om {FIXTUR}"},
                                   TENANT, key_id)
    m.execute(
        "INSERT INTO epost_utkast (tenant, melding_id, tekst_kryptert,"
        " nonce, key_id) VALUES (%s,%s,%s,%s,%s)",
        (TENANT, mid, ct, nonce, key_id))
    ct, nonce = kryptering.krypter(dek, {"navn": f"{FIXTUR}.pdf"},
                                   TENANT, key_id)
    m.execute(
        "INSERT INTO epost_vedlegg (tenant, melding_id, navn_kryptert,"
        " nonce, key_id, innholdstype, storrelse_bytes, leverandor_hash)"
        " VALUES (%s,%s,%s,%s,%s,'application/pdf',1234,%s)",
        (TENANT, mid, ct, nonce, key_id, secrets.token_hex(32)))


def _tell_payload(m, mid):
    """Antall payloadfelter over alle fire lagre som fortsatt er satt —
    målt kolonne for kolonne (057-testens `_tell_fixtur`-form; payloaden
    er kryptert, så målingen er NOT NULL, og at fixturen faktisk LÅ der
    måles ved dekryptering i den positive kontrollen)."""
    _sett_kontekst(m, TENANT)
    treff = 0
    for tabell, kolonner in LAGRE.items():
        for kol in kolonner:
            treff += m.execute(
                f"SELECT count(*) FROM {tabell}"
                f" WHERE tenant=%s AND melding_id=%s"
                f" AND {kol} IS NOT NULL",
                (TENANT, mid)).fetchone()[0]
    return treff


# ---------------------------------------------------------------------------
# Port 1: innhentingsidempotensen
# ---------------------------------------------------------------------------

@pg
def test_port1_duplikat_leverandormelding_er_en_rad(migrator):
    """Samme leverandørmelding sett to ganger er ÉN rad: rå INSERT nr. 2
    får `unique_violation`, og innhenterens form (ON CONFLICT DO
    NOTHING) svelger duplikatet uten ny rad."""
    kid, key_id, dek = _kilde(migrator)
    lev = f"AAMk-{secrets.token_hex(8)}"
    _melding(migrator, kid, key_id, dek, lev_id=lev)
    migrator.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):
        _melding(migrator, kid, key_id, dek, lev_id=lev)
    migrator.rollback()
    from db import kryptering
    _sett_kontekst(migrator, TENANT)
    ct, nonce = kryptering.krypter(dek, {"kropp": "duplikat"}, TENANT,
                                   key_id)
    migrator.execute(
        "INSERT INTO epost_melding (tenant, kilde_id,"
        " leverandor_melding_id, mottatt_ts, retning, avsender_hash,"
        " emne_hash, kropp_kryptert, nonce, key_id)"
        " VALUES (%s,%s,%s, now(), 'inn', %s,%s,%s,%s,%s)"
        " ON CONFLICT ON CONSTRAINT melding_en_per_leverandormelding"
        " DO NOTHING",
        (TENANT, kid, lev, secrets.token_hex(32), secrets.token_hex(32),
         ct, nonce, key_id))
    antall = migrator.execute(
        "SELECT count(*) FROM epost_melding WHERE tenant=%s"
        " AND kilde_id=%s AND leverandor_melding_id=%s",
        (TENANT, kid, lev)).fetchone()[0]
    assert antall == 1, "duplikatet la igjen en andre rad"
    migrator.rollback()


# ---------------------------------------------------------------------------
# Port 2: credentials er ciphertext
# ---------------------------------------------------------------------------

@pg
def test_port2_credentials_er_ciphertext_aldri_klartekst(migrator):
    """Credentials-porten i to lag: i BASEN finnes bare ciphertext
    (ingen klartekst-kolonne — målt som eier, som ser alt), og
    web-API-rollen når ikke engang ciphertexten — kildegranten er
    kolonnenivå uten credential-trioen (CodeRabbit på PR-A). Positivt:
    hemmeligheten ER der, bak tenant-DEK-en — en fraværstest uten den
    går grønn på søppel."""
    from db import kryptering
    kid, key_id, dek = _kilde(migrator)
    migrator.commit()
    # Eieren ser alle kolonnene — og ingen av dem bærer klartekst.
    _sett_kontekst(migrator, TENANT)
    som_tekst = migrator.execute(
        "SELECT k::text FROM epost_kilde k"
        " WHERE tenant=%s AND kilde_id=%s",
        (TENANT, kid)).fetchone()[0]
    assert HEMMELIG not in som_tekst, \
        "refresh-tokenet ligger i klartekst i en kolonne"
    migrator.rollback()
    rt = _rt()
    try:
        # Runtime leser driftskolonnene (RLS-gated)...
        _sett_kontekst(rt, TENANT)
        rad = rt.execute(
            "SELECT leverandor, postboks, status, sist_hentet_ts,"
            " opprettet FROM epost_kilde"
            " WHERE tenant=%s AND kilde_id=%s",
            (TENANT, kid)).fetchone()
        assert rad is not None and rad[0] == "m365"
        rt.rollback()
        # ... men ALDRI credential-trioen eller leverandørcursoren: et
        # kompromittert web-API skal ikke engang kunne eksfiltrere
        # ciphertext.
        for kol in ("auth_kryptert", "nonce", "key_id", "delta_token"):
            _sett_kontekst(rt, TENANT)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rt.execute(f"SELECT {kol} FROM epost_kilde"
                           " WHERE tenant=%s", (TENANT,))
            rt.rollback()
    finally:
        rt.close()
    # Positiv kontroll: tenant-DEK-en åpner den (som eier).
    _sett_kontekst(migrator, TENANT)
    ct, nonce = migrator.execute(
        "SELECT auth_kryptert, nonce FROM epost_kilde"
        " WHERE tenant=%s AND kilde_id=%s", (TENANT, kid)).fetchone()
    apnet = kryptering.dekrypter(dek, ct, nonce, TENANT, key_id)
    assert apnet == {"refresh_token": HEMMELIG}
    migrator.rollback()


@pg
def test_port2b_runtime_kan_ikke_skrive_kilden(migrator):
    """PR-A gir runtime KUN SELECT: ingen dører finnes, så heller ingen
    tabellrettigheter å gå utenom dem med (dørene kommer i PR-B/C)."""
    _kilde(migrator)
    migrator.commit()
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute(
                "INSERT INTO epost_kilde (tenant, leverandor, postboks,"
                " auth_kryptert, nonce, key_id) VALUES"
                " (%s,'m365','x@example.org','\\x00'::bytea,"
                " '\\x000000000000000000000000'::bytea,'k')", (TENANT,))
        rt.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("UPDATE epost_kilde SET status='deaktivert'"
                       " WHERE tenant=%s", (TENANT,))
        rt.rollback()
    finally:
        rt.close()


# ---------------------------------------------------------------------------
# Port 3: fristen er immutabel
# ---------------------------------------------------------------------------

@pg
def test_port3_slettefristen_er_immutabel_etter_insert(migrator):
    """057 port 20, ordrett for M-6: INGEN overgang endrer fristen —
    heller ikke eieren (en vakt som bare gjelder de rettighetsløse er
    ingen vakt). `mottatt_ts` er fristens andre ende og like låst, og
    en fødsel med `mottatt_ts` frem i tid avvises (forlengelse gjennom
    den andre kolonnen)."""
    kid, key_id, dek = _kilde(migrator)
    mid = _melding(migrator, kid, key_id, dek, frist=30)
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "UPDATE epost_melding SET slettefrist_dogn=365"
            " WHERE tenant=%s AND melding_id=%s", (TENANT, mid))
    migrator.rollback()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "UPDATE epost_melding SET mottatt_ts=now()"
            " WHERE tenant=%s AND melding_id=%s", (TENANT, mid))
    migrator.rollback()
    from db import kryptering
    _sett_kontekst(migrator, TENANT)
    ct, nonce = kryptering.krypter(dek, {"kropp": "x"}, TENANT, key_id)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "INSERT INTO epost_melding (tenant, kilde_id,"
            " leverandor_melding_id, mottatt_ts, retning, avsender_hash,"
            " emne_hash, kropp_kryptert, nonce, key_id)"
            " VALUES (%s,%s,%s, now() + interval '2 days', 'inn',"
            " %s,%s,%s,%s,%s)",
            (TENANT, kid, secrets.token_hex(8), secrets.token_hex(32),
             secrets.token_hex(32), ct, nonce, key_id))
    migrator.rollback()
    # ... og spennet er basens: 3650 døgn kan ikke uttrykkes.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.CheckViolation):
        _melding(migrator, kid, key_id, dek, frist=3650)
    migrator.rollback()


# ---------------------------------------------------------------------------
# Port 4: reaping — alle lagre sammen, aldri ett alene
# ---------------------------------------------------------------------------

@pg
def test_port4_reaping_tommer_alle_lagrene(migrator):
    """Fixture-strengen ligger (dekrypterbart) i payloaden i alle fire
    lagre FØR reaping — positiv kontroll — og payloadkolonnene står i
    NULL av dem etter. Radene består med hasher og `slettet_ts`:
    minimal revisjonsevidens, ikke sporløshet. Kilden og oppfølgingen
    røres ikke."""
    from db import kryptering
    kid, key_id, dek = _kilde(migrator)
    mid = _melding(migrator, kid, key_id, dek, dager_siden=31, frist=30)
    _fyll_lagrene(migrator, mid, key_id, dek)
    migrator.execute(
        "INSERT INTO epost_oppfolging (tenant, trad_ref, type, frist_ts)"
        " VALUES (%s,%s,'ubesvart', now() + interval '2 days')",
        (TENANT, f"trad-{mid}"))
    migrator.commit()
    assert _tell_payload(migrator, mid) == 12, \
        "positiv kontroll: payloadtrioen skal stå i alle fire lagre"
    ct, nonce = migrator.execute(
        "SELECT kropp_kryptert, nonce FROM epost_melding"
        " WHERE tenant=%s AND melding_id=%s", (TENANT, mid)).fetchone()
    assert FIXTUR in kryptering.dekrypter(
        dek, ct, nonce, TENANT, key_id)["kropp"], \
        "positiv kontroll: fixturen skal ligge bak ciphertexten"
    migrator.rollback()
    rp, timerrolle = _reaperkobling()
    try:
        if timerrolle:
            # 038/057/088: en kompromittert web-API-rolle skal ikke kunne
            # trigge retensjonsarbeid på tvers av alle tenanter.
            rt = _rt()
            try:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    rt.execute("SELECT * FROM reap_epostdata(50)")
                rt.rollback()
            finally:
                rt.close()
        reapet = rp.execute("SELECT * FROM reap_epostdata(50)").fetchall()
        rp.commit()
        assert (TENANT, mid) in [(r[0], r[1]) for r in reapet]
    finally:
        rp.close()
    assert _tell_payload(migrator, mid) == 0, \
        "payload står igjen etter reaping"
    for tabell in LAGRE:
        rad = migrator.execute(
            f"SELECT count(*), count(*) FILTER (WHERE slettet_ts IS"
            f" NOT NULL) FROM {tabell}"
            f" WHERE tenant=%s AND melding_id=%s",
            (TENANT, mid)).fetchone()
        assert rad[0] >= 1 and rad[0] == rad[1], tabell
    # Evidensen består: hasher på meldingen, digest på klassifiseringen.
    rad = migrator.execute(
        "SELECT avsender_hash, emne_hash FROM epost_melding"
        " WHERE tenant=%s AND melding_id=%s", (TENANT, mid)).fetchone()
    assert rad[0] and rad[1], "hashene er revisjonsevidens og består"
    # Kilden (credentials) og oppfølgingen (metadata) består reaping.
    assert migrator.execute(
        "SELECT auth_kryptert IS NOT NULL FROM epost_kilde"
        " WHERE tenant=%s AND kilde_id=%s", (TENANT, kid)).fetchone()[0]
    assert migrator.execute(
        "SELECT count(*) FROM epost_oppfolging WHERE tenant=%s",
        (TENANT,)).fetchone()[0] >= 1
    migrator.rollback()


@pg
def test_port4b_reaping_respekterer_fristen(migrator):
    """Motstykket: en melding innenfor fristen røres ikke — et reap-kall
    er ikke en sletteknapp."""
    kid, key_id, dek = _kilde(migrator)
    mid = _melding(migrator, kid, key_id, dek, dager_siden=29, frist=30)
    _fyll_lagrene(migrator, mid, key_id, dek)
    migrator.commit()
    rp, _timerrolle = _reaperkobling()
    try:
        reapet = rp.execute("SELECT * FROM reap_epostdata(50)").fetchall()
        rp.commit()
        assert (TENANT, mid) not in [(r[0], r[1]) for r in reapet]
    finally:
        rp.close()
    assert _tell_payload(migrator, mid) == 12
    migrator.rollback()


@pg
def test_port4c_ett_lager_alene_er_rodt_ved_commit(migrator):
    """057 port 19-formen: en direkte DML som reaper ETT lager og lar
    resten leve, felles av den utsatte porten når transaksjonen skal
    committe — også for eieren."""
    kid, key_id, dek = _kilde(migrator)
    mid = _melding(migrator, kid, key_id, dek)
    _fyll_lagrene(migrator, mid, key_id, dek)
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "UPDATE epost_klassifisering SET sammendrag_kryptert=NULL,"
        " nonce=NULL, key_id=NULL, slettet_ts=now()"
        " WHERE tenant=%s AND melding_id=%s", (TENANT, mid))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.commit()
    migrator.rollback()
    assert _tell_payload(migrator, mid) == 12, \
        "den halvreapede transaksjonen skulle rullet tilbake"
    migrator.rollback()


@pg
def test_port4d_meldingen_kan_ikke_merkes_med_levende_barn(migrator):
    """Ankersiden (057s ankervakt): reap-merket på meldingen er en
    KONKLUSJON om at barnelagrene alt er tømt — et merke satt mens de
    lever ville utelukket meldingen fra reaperen for alltid."""
    kid, key_id, dek = _kilde(migrator)
    mid = _melding(migrator, kid, key_id, dek)
    _fyll_lagrene(migrator, mid, key_id, dek)
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "UPDATE epost_melding SET kropp_kryptert=NULL, nonce=NULL,"
            " key_id=NULL, slettet_ts=now()"
            " WHERE tenant=%s AND melding_id=%s", (TENANT, mid))
    migrator.rollback()


@pg
def test_port4e_reapet_melding_tar_ikke_ny_payload(migrator):
    """057 port 18: en forsinket eller retriet skriver kan ikke
    gjenoppstå persondata på en reapet melding — det finnes ingen vei
    til å slette dem igjen."""
    from db import kryptering
    kid, key_id, dek = _kilde(migrator)
    mid = _melding(migrator, kid, key_id, dek, dager_siden=31, frist=30)
    migrator.commit()
    rp, _timerrolle = _reaperkobling()
    try:
        rp.execute("SELECT * FROM reap_epostdata(50)")
        rp.commit()
    finally:
        rp.close()
    _sett_kontekst(migrator, TENANT)
    ct, nonce = kryptering.krypter(dek, {"sammendrag": "for sent"},
                                   TENANT, key_id)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "INSERT INTO epost_klassifisering (tenant, melding_id,"
            " prioritet, handlingstype, sammendrag_kryptert, nonce,"
            " key_id, modell_digest)"
            " VALUES (%s,%s,'normal','til_info',%s,%s,%s,%s)",
            (TENANT, mid, ct, nonce, key_id, "sha256:" + "1" * 64))
    migrator.rollback()


@pg
def test_port4f_reaperen_kalles_fra_driftsveien(migrator):
    """057s Codex P1 skal ikke gjentas for M-6: `reap_epostdata` er ikke
    bare definert og GRANTet — den KALLES av den deployede veien
    (`disponit-evidensreaper.service` → `drift.kjor_evidensreaper` →
    `evidensreaper.kjor`). Testen går gjennom `evidensreaper.kjor`,
    samme funksjon tjenesten kaller.

    MUTASJONEN SOM DREPER DENNE: fjern `reap_epostdata`-blokken fra
    `evidensreaper.kjor` — alle direktekallende tester over er grønne."""
    from drift import evidensreaper
    kid, key_id, dek = _kilde(migrator)
    mid = _melding(migrator, kid, key_id, dek, dager_siden=31, frist=30)
    _fyll_lagrene(migrator, mid, key_id, dek)
    migrator.commit()
    rp, _timerrolle = _reaperkobling()
    try:
        r = evidensreaper.kjor(rp)
        assert not r.epostdata_feilet, \
            "timerrollen har EXECUTE — en nekt her er et rettighetshull"
        assert (TENANT, str(mid)) in r.epostdata
    finally:
        rp.close()
    assert _tell_payload(migrator, mid) == 0
    migrator.rollback()


# ---------------------------------------------------------------------------
# Utkastets egne overganger (append-only tekst)
# ---------------------------------------------------------------------------

@pg
def test_utkastets_tekst_er_append_only_men_dommen_felles(migrator):
    """Planens vakt: regenerering er en NY rad — teksten kan aldri
    endres. Flatens dom (foreslått → forkastet | brukt_manuelt) er den
    ENE lovlige overgangen utenom reap, og den felles én gang."""
    from db import kryptering
    kid, key_id, dek = _kilde(migrator)
    mid = _melding(migrator, kid, key_id, dek)
    _fyll_lagrene(migrator, mid, key_id, dek)
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    uid = migrator.execute(
        "SELECT utkast_id FROM epost_utkast WHERE tenant=%s"
        " AND melding_id=%s", (TENANT, mid)).fetchone()[0]
    ct, nonce = kryptering.krypter(dek, {"tekst": "omskrevet"}, TENANT,
                                   key_id)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "UPDATE epost_utkast SET tekst_kryptert=%s, nonce=%s"
            " WHERE tenant=%s AND utkast_id=%s",
            (ct, nonce, TENANT, uid))
    migrator.rollback()
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "UPDATE epost_utkast SET status='forkastet'"
        " WHERE tenant=%s AND utkast_id=%s", (TENANT, uid))
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "UPDATE epost_utkast SET status='brukt_manuelt'"
            " WHERE tenant=%s AND utkast_id=%s", (TENANT, uid))
    migrator.rollback()
    # ... og et utkast fødes foreslått, aldri ferdig dømt.
    _sett_kontekst(migrator, TENANT)
    ct, nonce = kryptering.krypter(dek, {"tekst": "x"}, TENANT, key_id)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "INSERT INTO epost_utkast (tenant, melding_id,"
            " tekst_kryptert, nonce, key_id, status)"
            " VALUES (%s,%s,%s,%s,%s,'brukt_manuelt')",
            (TENANT, mid, ct, nonce, key_id))
    migrator.rollback()


# ---------------------------------------------------------------------------
# Port 5: statisk — ingen sendevei, ingen direkte evidensskriving
# ---------------------------------------------------------------------------

def test_port5_modulen_har_ingen_sendevei():
    """Dommen pkt. 2: v1 er KUN lesende. Porten dekker modulens
    FREMTIDIGE filer (PR-A bærer bare manifestet): ingen import av en
    sendekanal (SMTP/Graph-send), og ingen direkte INSERT mot
    `oppdrag`/`revisjonslogg` — modulens vei til begge er kjernens
    (AST/grep-formen fra port 13/SP-1-familien).

    GRENSEN FOR HVA PORTEN BEVISER (m57s egen presisering): den måler
    importgrafen og SQL-strengene i modulens filer — den kan ikke se en
    dataflyt gjennom en kaller utenfor modulen. Den flyten måles der
    den bor (bestillings- og outbox-portene)."""
    import ast
    assert (MODULROT / "manifest.yaml").exists()
    FORBUDTE_IMPORTER = {"smtplib", "aiosmtplib", "exchangelib"}
    FORBUDT_SQL = re.compile(
        r"INSERT\s+INTO\s+(?:public\.)?(?:oppdrag|revisjonslogg)\b",
        re.IGNORECASE)
    FORBUDTE_KALL = re.compile(r"sendMail|/sendMail|messages/send",
                               re.IGNORECASE)
    for fil in sorted(MODULROT.rglob("*.py")):
        kilde = fil.read_text(encoding="utf-8")
        tre = ast.parse(kilde)
        importerte: set[str] = set()
        for node in ast.walk(tre):
            if isinstance(node, ast.Import):
                importerte |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                importerte.add(node.module.split(".")[0])
        assert not importerte & FORBUDTE_IMPORTER, \
            f"{fil.name}: sendevei-import i en kun-lesende modul"
        assert not FORBUDT_SQL.search(kilde), \
            f"{fil.name}: direkte INSERT mot oppdrag/revisjonslogg"
        assert not FORBUDTE_KALL.search(kilde), \
            f"{fil.name}: Graph-sendekall i en kun-lesende modul"


# ---------------------------------------------------------------------------
# Port 6: epost.behandling uten beslutning → ingen oppdrag
# ---------------------------------------------------------------------------

@pg
def test_port6_epostoppdrag_uten_beslutning_avvises(migrator):
    """Opphavsporten gjelder også den nye typen: et beslutningsoppdrag
    uten beslutnings-FK avvises av lagringen (038-portformen fra
    `test_opphavskombinasjonene_er_uttommende`), og den lovlige formen
    — TILLAT-loggpost + FK — går gjennom. Regresjonen på de
    eksisterende opprinnelsene bor i test_outbox_bestilling."""
    from db import kryptering
    _sett_kontekst(migrator, TENANT)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator,
                                                          TENANT)
    # DEK-en committes FØR den negative armen: rollbacken under skal
    # felle oppdraget, ikke rive med seg nøkkelen den lovlige armen
    # trenger.
    migrator.commit()
    ct, nonce = kryptering.krypter(dek, {"kilde_id": str(uuid.uuid4()),
                                         "omfang": "postboks"},
                                   TENANT, key_id)
    basis = ("payload_kryptert, key_id, nonce, utforelsesfrist,"
             " evidensfrist")
    verdier = "%s,%s,%s,now()+interval '30 min',now()+interval '1 day'"
    _sett_kontekst(migrator, TENANT)
    with pytest.raises((psycopg.errors.CheckViolation,
                        psycopg.errors.RaiseException)):
        migrator.execute(
            f"INSERT INTO oppdrag (opprinnelse, tenant, oppdragstype,"
            f" handling, eiermodul, {basis}) VALUES ('beslutning',%s,"
            f"'epost.behandling','epost.behandling','m06_epost',"
            f"{verdier})",
            (TENANT, ct, key_id, nonce))
    migrator.rollback()
    # Den lovlige formen: TILLAT-loggpost + beslutnings-FK.
    _sett_kontekst(migrator, TENANT)
    logg = migrator.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'test','api_token','ih','p@1.0.0/x.y','TILLAT',"
        "'[]',%s) RETURNING id",
        (TENANT, secrets.token_hex(8))).fetchone()[0]
    oid = migrator.execute(
        f"INSERT INTO oppdrag (opprinnelse, tenant,"
        f" beslutning_loggpost_id, oppdragstype, handling, eiermodul,"
        f" {basis}, koblingsstatus) VALUES ('beslutning',%s,%s,"
        f"'epost.behandling','epost.behandling','m06_epost',{verdier},"
        f"'KOBLET') RETURNING id",
        (TENANT, logg, ct, key_id, nonce)).fetchone()[0]
    assert oid is not None
    migrator.rollback()


def test_port6b_bestillingstypen_er_deklarert_og_lukket():
    """Registreringen (planen §3): bestillingstype og oppdragstype er
    samme navn, eiermodulen er m06_epost, omfanget er lukket til
    «postboks», fristen er kontraktens 30 min, og grensene leses fra
    KONTRAKTEN — normaliseringen kanoniserer eksplisitt standardfrist
    til fravær (samme intensjon, samme hash)."""
    import oppdragskontrakt as ok
    from api.bestilling import BESTILLINGSTYPER, intensjonshash, normaliser
    bt = BESTILLINGSTYPER["epost.behandling"]
    t = ok.OPPDRAGSTYPER["epost.behandling"]
    assert bt.oppdragstype == "epost.behandling"
    assert bt.eiermodul == t.eiermodul == "m06_epost"
    assert bt.omfang == ("postboks",)
    assert ok.FELTVERDIER["epost.behandling"]["omfang"] == ("postboks",)
    assert ok.FELTGRENSER["epost.behandling"]["slettefrist_dogn"] == \
        (30, 365)
    assert ok.UTFORELSESFRIST_VALG["epost.behandling"] == \
        ("omfang", {"postboks": 30 * 60})
    # Fristen dekkes av ETT grant — ingen fornyelseskjede å kreve.
    assert 30 * 60 <= ok.UTSTEDT_AUTORITET_S
    assert ok.type_for_handling("epost.behandling") is t
    assert ok.type_for_handling("epost.behandling.oppfolging") is t
    assert ok.type_for_handling("epost.behandlingx") is not t
    kid = str(uuid.uuid4())
    a = normaliser("t", {"bestillingstype": "epost.behandling",
                         "kilde_ref": f"kilde:{kid}",
                         "omfang": "postboks"})
    b = normaliser("t", {"bestillingstype": "epost.behandling",
                         "kilde_ref": f"kilde:{kid}",
                         "omfang": "postboks",
                         "slettefrist_dogn":
                             ok.SLETTEFRIST_STANDARD_DOGN})
    assert a == b and intensjonshash(a) == intensjonshash(b)
    c = normaliser("t", {"bestillingstype": "epost.behandling",
                         "kilde_ref": f"kilde:{kid}",
                         "omfang": "postboks", "slettefrist_dogn": 30})
    assert intensjonshash(c) != intensjonshash(a)
    from api.bestilling import Bestillingsfeil
    for kropp in (
            {"bestillingstype": "epost.behandling",
             "kilde_ref": "kilde:ikke-en-uuid", "omfang": "postboks"},
            {"bestillingstype": "epost.behandling",
             "kilde_ref": f"kilde:{kid}", "omfang": "alt"},
            {"bestillingstype": "epost.behandling",
             "kilde_ref": f"kilde:{kid}", "omfang": "postboks",
             "slettefrist_dogn": 3650},
            {"bestillingstype": "epost.behandling",
             "kilde_ref": f"kilde:{kid}", "omfang": "postboks",
             "hostname": "x.example"}):
        with pytest.raises(Bestillingsfeil):
            normaliser("t", kropp)


# ---------------------------------------------------------------------------
# Port 7: migrasjonen — grønn, byte-bundet, ren DDL
# ---------------------------------------------------------------------------

@pg
def test_port7_migrasjonen_er_kjort_og_bytebundet(migrator):
    """Den tomme kjøringen er målt direkte: 088 står i `migrasjoner`
    med checksum lik sha256 av filbytene i treet — samme byte-binding
    fasiten (`migrasjons-fasit.json`) pinner mot main."""
    import hashlib
    import json
    cs = migrator.execute(
        "SELECT checksum FROM migrasjoner WHERE versjon=88").fetchone()
    migrator.rollback()
    assert cs is not None, "088 er ikke kjørt i testbasen"
    fil_sha = hashlib.sha256(MIGRASJON.read_bytes()).hexdigest()
    assert cs[0] == fil_sha, \
        "088 i treet er ikke bytene basen kjørte — historikk er immutable"
    fasit = json.loads(
        (ROT / "platform" / "core" / "db" / "migrasjons-fasit.json")
        .read_text(encoding="utf-8"))
    assert fasit.get("088_m6_epost.sql") == fil_sha, \
        "fasiten pinner andre bytes enn treet bærer"


def test_port7b_migrasjonen_er_ren_ddl():
    """Den bebodde kjøringen (047-klassen): masse-DML i en migrasjon kan
    køe utsatte triggerhendelser som ALTER-setninger nekter å passere —
    det er nettopp derfor SP-10 finnes for backfills. 088 har ingen
    seed der, og denne porten måler premisset: ingen UPDATE/DELETE og
    ingen INSERT mot bebodde datatabeller på toppnivå.

    ETT unntak, navngitt: `rolle_scope`-seedet (043 §6b-mønsteret,
    044-formen ordrett) — et idempotent config-seed port 26 KREVER, mot
    en tabell uten utsatte triggere og uten tenantdata. Alt annet er
    backfill-klassen og skal registrere seed+måling i SP-10. Fail-closed
    på pglast, som katalogporten."""
    import pglast
    sql = MIGRASJON.read_text(encoding="utf-8")
    dml = []
    for raa in pglast.parse_sql(sql):
        navn = type(raa.stmt).__name__
        if navn == "InsertStmt" and raa.stmt.relation.relname == \
                "rolle_scope":
            continue
        if navn in ("InsertStmt", "UpdateStmt", "DeleteStmt"):
            dml.append(navn)
    assert not dml, (
        f"088 bærer toppnivå-DML {dml} — da er den en backfill og skal"
        " registrere seed+måling i SP-10 (sp10-provekjoring.py)")


def test_088_navngir_aldri_runtime_rollen():
    """056/057-formen, ordrett for 088: `disponit` er lokalnavnet, og
    `migrer.py` er eneste rettighetskilde. Den betingede reaperblokken
    er unntaket (038-formen, der REVOKE FROM disponit er poenget) og
    fjernes før målingen, så fritaket er synlig og ikke kan utvides i
    stillhet."""
    sql = MIGRASJON.read_text(encoding="utf-8")
    reaperblokk = re.compile(
        r"DO \$\$\s*BEGIN\s*IF EXISTS \(SELECT 1 FROM pg_roles"
        r" WHERE rolname = 'disponit_domener'\)[^$]*?END \$\$;", re.S)
    assert reaperblokk.search(sql), \
        "den betingede reaperblokken er borte — fritaket under er da" \
        " et hull"
    uten_reaper = reaperblokk.sub("", sql)
    treff = list(re.finditer(r"TO disponit\b\s*;", uten_reaper))
    assert not treff, (
        "088 navngir runtime-rollen ved lokalnavn: " + repr([
            uten_reaper[max(0, t.start() - 120):t.end()][-120:]
            for t in treff]))


@pg
def test_163_formen_er_fodt_riktig_for_m6(migrator):
    """#163s dom gjelder fra fødselen: den utsatte porten bor på
    ANKERET (`epost_melding`, én gang per melding), barnelagrene bærer
    bare markøren. Den basevide fasiten bor i
    `test_m57_kandidatlagre.test_163_samletporten_staar_pa_ankeret_-
    ikke_per_rad`; dette er M-6s egen avlesning av den.

    MUTASJONEN SOM DREPER DENNE: flytt constraint-triggeren ut på
    barnelagrene (057s per-rad-form), eller mist en markør."""
    utsatte = {r[0]: r[1] for r in migrator.execute(
        "SELECT tgrelid::regclass::text, count(*) FROM pg_trigger"
        " WHERE tgname = 'epost_melding_reapes_samlet'"
        " AND NOT tgisinternal GROUP BY 1").fetchall()}
    assert utsatte == {"epost_melding": 1}, utsatte
    markorer = {r[0] for r in migrator.execute(
        "SELECT tgrelid::regclass::text FROM pg_trigger"
        " WHERE tgname LIKE 'epost%\\_beroert' AND NOT tgisinternal"
    ).fetchall()}
    assert markorer == {"epost_klassifisering", "epost_utkast",
                        "epost_vedlegg"}, markorer
    migrator.rollback()


@pg
def test_163_forfalsket_markortabell_feller_skrivet_m6(migrator):
    """076s CodeRabbit-port, M-6-siden: TEMP-retten er PUBLICs, så en
    kaller kunne pre-lage markørtabellen ferdig seedet og kvele
    armeringen for sine egne skriv. Porten eier tabellen sin: feil eier
    feller SKRIVET, fail-closed."""
    kid, key_id, dek = _kilde(migrator)
    mid = _melding(migrator, kid, key_id, dek)
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "CREATE TEMP TABLE m6_beroerte_meldinger ("
        " tenant TEXT NOT NULL, melding_id UUID NOT NULL,"
        " PRIMARY KEY (tenant, melding_id)) ON COMMIT DROP")
    migrator.execute(
        "INSERT INTO pg_temp.m6_beroerte_meldinger VALUES (%s,%s)",
        (TENANT, mid))
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as e:
        _fyll_lagrene(migrator, mid, key_id, dek)
    assert "markørtabellen" in str(e.value)
    migrator.rollback()
    # Positiv kontroll: uten forfalskningen går samme skriv.
    _fyll_lagrene(migrator, mid, key_id, dek)
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    migrator.rollback()


def test_kjoreren_speiler_088_rettighetene():
    """Tabellspeilet i `migrer.py` (057-portformen): runtime får KUN
    SELECT på alle seks tabellene i PR-A — ingen INSERT (innhenteren er
    PR-C) — og kryss-tenant-reaperen lekker aldri til en parameterisert
    rolle."""
    kjorer = (ROT / "deploy" / "staging" / "migrer.py").read_text(
        encoding="utf-8")
    # Kilden: KOLONNEGRANT uten credential-trioen og cursoren
    # (CodeRabbit på PR-A) — web-API-rollen skal ikke engang kunne
    # eksfiltrere ciphertext.
    assert ("GRANT SELECT (tenant, kilde_id, leverandor, postboks,"
            " status,\n    sist_hentet_ts, opprettet) ON epost_kilde"
            " TO {rolle};") in kjorer
    assert ("GRANT SELECT ON epost_melding, epost_klassifisering,"
            "\n    epost_utkast, epost_oppfolging,"
            " epost_vedlegg TO {rolle};") in kjorer
    for kol in ("auth_kryptert", "nonce", "key_id", "delta_token"):
        assert f"{kol}" not in kjorer.split(
            "GRANT SELECT (tenant, kilde_id")[1].split(";")[0], \
            f"credential-kolonnen {kol} har sneket seg inn i kildegranten"
    assert "GRANT EXECUTE ON FUNCTION reap_epostdata" not in kjorer, \
        "kryss-tenant-reaperen lekker til en parameterisert rolle"
    for tabell in ("epost_kilde", "epost_melding", "epost_klassifisering",
                   "epost_utkast", "epost_vedlegg", "epost_oppfolging"):
        assert f"INSERT ON {tabell}" not in kjorer, \
            f"runtime har fått INSERT på {tabell} før noen skrivevei" \
            " finnes"


# ---------------------------------------------------------------------------
# m6-v1-grensen (planens §7) — registrert FØR bygging (§0)
# ---------------------------------------------------------------------------

def _gront_artefakt() -> dict:
    from manifestskjema import M6_INVARIANTER
    maalt: dict = {}
    for navn in M6_INVARIANTER:
        maalt[f"{navn}_forsok"] = 3
        maalt[f"{navn}_brudd"] = 0
    maalt["ddl_begge_kjoringer_gronne"] = True
    return {"krav_id": "m6-v1", "ts": "2026-08-31T00:00:00+00:00",
            "bestatt": True,
            "oppsett": {"modul": "m06_epost", "commit": "0" * 40,
                        "vert": "lokal"},
            "maalt": maalt}


def test_m6_grensen_dekker_planens_punkter():
    """Planen §7 teller 7 invarianter + 1 ja-punkt. Pinnet MOT PLANEN,
    ikke mot listen selv (m57-grensens form)."""
    from manifestskjema import KRAVGRENSER, M6_INVARIANTER
    g = KRAVGRENSER["m6-v1"]
    assert len(M6_INVARIANTER) == 7
    assert len(set(M6_INVARIANTER)) == 7
    assert g["invarianter"] is M6_INVARIANTER
    assert g["krav_ja"] == ("ddl_begge_kjoringer_gronne",)
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    # Punktbindingen er TOM MED VILJE — hvert sjekklistepunkt er
    # uflippbart til målingene finnes (PR-C/D).
    assert g["punktbinding"] == {}


def test_m6_grensen_maaler_parene_og_ja_punktet():
    from manifestskjema import M6_INVARIANTER, _sjekk_grenser
    assert _sjekk_grenser("m6-v1", _gront_artefakt()) == []
    for navn in M6_INVARIANTER:
        art = _gront_artefakt()
        art["maalt"][f"{navn}_brudd"] = 1
        assert any(f"{navn}_brudd=1" in f
                   for f in _sjekk_grenser("m6-v1", art)), navn
        art = _gront_artefakt()
        art["maalt"][f"{navn}_forsok"] = 0
        assert any(f"{navn}_forsok=0" in f
                   for f in _sjekk_grenser("m6-v1", art)), navn
    for verdi in (False, None, 1, "ja"):
        art = _gront_artefakt()
        art["maalt"]["ddl_begge_kjoringer_gronne"] = verdi
        assert any("ddl_begge_kjoringer_gronne" in f
                   for f in _sjekk_grenser("m6-v1", art)), verdi


def test_m6_manifestet_er_gyldig_og_aerlig():
    """Manifestet validerer mot skjemaet, sier under_utvikling/
    ikke_i_drift, bærer de REELLE avhengighetene (M-6 BESTILLER gjennom
    policyporten — ulikt m16/m38), og ingen punkter er flippet uten
    måling."""
    import yaml

    from manifestskjema import valider_manifest
    m = yaml.safe_load((MODULROT / "manifest.yaml")
                       .read_text(encoding="utf-8"))
    assert valider_manifest(m) == []
    assert m["id"] == "m06_epost" == MODULROT.name
    assert m["status"] == "under_utvikling"
    assert m["driftstilstand"] == "ikke_i_drift"
    assert m["avhengigheter"] == ["m01_policy", "m02_revisjonslogg"]
    assert m["i18n_prefiks"] == "m06epost"
    for punkt, innhold in m["staging_sjekkliste"].items():
        assert innhold["status"] == "nei", \
            f"{punkt} er flippet uten at noen måling finnes"
