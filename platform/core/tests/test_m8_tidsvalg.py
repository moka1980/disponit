"""M-8 tidsvalg v1 (082): slots, kapabilitetstoken og kandidatens valg.

Portene er planens egne (Codex-skissen + dommene 1–5): valg uten gyldig
token avvises uniformt, kapasiteten serialiseres av radlåsen, valget er
ENDELIG, tidene er immutable, reaperen tømmer valget som et
kandidatlager — og utsenderen committer tokenet FØR send(), så en
e-post med død lenke er urepresenterbar.

Riggformene er m57-testenes: `_grunnlag`/`_prosess_med_kandidater`
importeres fra test_m57_utsending, utsender-riggen fra
test_m57_utsender.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import uuid

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, PEPPER, TENANT,  # noqa: F401
                       app, klient, migrator, miljo)
from .test_m37 import _sett_kontekst
from .test_m57_utsender import _signert_liste
from .test_m57_utsending import _prosess_med_kandidater, _sender, pg


def _rt():
    from db.pg import koble
    return koble(DSN)


def _slot(m, pid, *, timer=24, varighet_t=1, kap=1):
    """Én aktiv slot gjennom døren (claimer-eid)."""
    _sett_kontekst(m, TENANT)
    m.execute("SET ROLE disponit_m37_claimer")
    sid = m.execute(
        "SELECT m8_opprett_slot(%s,%s, now() + make_interval(hours=>%s),"
        " now() + make_interval(hours=>%s), %s)",
        (TENANT, pid, timer, timer + varighet_t, kap)).fetchone()[0]
    m.execute("RESET ROLE")
    m.commit()
    return sid


def _mac(secret: str) -> str:
    return hmac.new(PEPPER.encode(), secret.encode(),
                    hashlib.sha256).hexdigest()


def _utsted(m, lid, kid, *, levetid=30):
    """Token gjennom utstederdøren. -> (token_id, secret, utloper)."""
    token_id, secret = secrets.token_hex(16), secrets.token_hex(32)
    _sett_kontekst(m, TENANT)
    m.execute("SET ROLE disponit_m37_claimer")
    utl = m.execute(
        "SELECT m8_utsted_tidsvalgtoken(%s,%s,%s,%s,%s,%s)",
        (TENANT, lid, kid, token_id, _mac(secret), levetid)).fetchone()[0]
    m.execute("RESET ROLE")
    m.commit()
    return token_id, secret, utl


def _rigg(m, *, antall=1, kap=1):
    """Signert invitasjonsliste + aktiv slot + token for første kandidat.
    -> (pid, kids, slot_id, token_id, secret)."""
    oid, pid, kids, _ = _prosess_med_kandidater(m, antall)
    lid = _signert_liste(m, kids, oid)
    sid = _slot(m, pid, kap=kap)
    token_id, secret, _utl = _utsted(m, lid, kids[0])
    return pid, kids, lid, sid, token_id, secret


def _oppslag(c, token_id, secret):
    return c.execute("SELECT * FROM m8_tidsvalg_oppslag(%s,%s)",
                     (token_id, _mac(secret))).fetchall()


def _velg(c, token_id, secret, sid):
    return c.execute("SELECT * FROM m8_velg_slot(%s,%s,%s)",
                     (token_id, _mac(secret), sid)).fetchone()


# ---------------------------------------------------------------------------
# Slots: immutable tider, payloadvindu, deaktivering
# ---------------------------------------------------------------------------

@pg
def test_slot_tider_er_immutable_og_rader_slettes_aldri(migrator):
    """Codex-porten «slot-tid endret etter opprettelse → vaktavvist»:
    kandidaten valgte en TID — flytting er deaktiver + ny rad."""
    oid, pid, kids, _ = _prosess_med_kandidater(migrator, 1)
    sid = _slot(migrator, pid)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "UPDATE m8_slot SET start_ts = start_ts + interval '1 hour'"
            " WHERE tenant=%s AND slot_id=%s", (TENANT, sid))
    migrator.rollback()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute("DELETE FROM m8_slot WHERE tenant=%s"
                         " AND slot_id=%s", (TENANT, sid))
    migrator.rollback()
    # Den ENE lovlige overgangen — og aldri veien tilbake.
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE m8_slot SET status='deaktivert'"
                     " WHERE tenant=%s AND slot_id=%s", (TENANT, sid))
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute("UPDATE m8_slot SET status='aktiv'"
                         " WHERE tenant=%s AND slot_id=%s", (TENANT, sid))
    migrator.rollback()


@pg
def test_opprett_slot_krever_payloadvindu(migrator):
    """077 er den ene kilden: en prosess med bestilt sletting tilbyr
    ingen nye tider."""
    oid, pid, kids, _ = _prosess_med_kandidater(migrator, 1)
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT bestill_tidligsletting(%s,%s)", (TENANT, pid))
        rt.commit()
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "SELECT m8_opprett_slot(%s,%s, now() + interval '1 day',"
            " now() + interval '25 hours', 1)", (TENANT, pid))
    migrator.rollback()


@pg
def test_deaktivering_er_fail_closed_mot_bekreftet_valg(migrator):
    """DOM 3: valget er endelig — en slot et levende valg peker på kan
    ikke trekkes under kandidaten. Uten valg: deaktivert, idempotent."""
    pid, kids, lid, sid, token_id, secret = _rigg(migrator, kap=1)
    rt = _rt()
    try:
        assert _velg(rt, token_id, secret, sid)[0] == "valgt"
        rt.commit()
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        migrator.execute("SELECT m8_deaktiver_slot(%s,%s)", (TENANT, sid))
    migrator.rollback()
    # ... og en slot UTEN valg deaktiveres, stille ja andre gang.
    sid2 = _slot(migrator, pid, timer=48)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    for _ in range(2):
        assert migrator.execute("SELECT m8_deaktiver_slot(%s,%s)",
                                (TENANT, sid2)).fetchone()[0] is True
    migrator.execute("RESET ROLE")
    migrator.commit()


# ---------------------------------------------------------------------------
# Utstederdøren
# ---------------------------------------------------------------------------

@pg
def test_utsteder_krever_signert_invitasjonsliste(migrator):
    """Codex-porten «token på usignert liste → avvist i utstederdøren» —
    og en signert AVSLAGSLISTE er like avvist: kapabiliteten finnes bare
    for invitasjoner."""
    from .test_m57_utsender import _signert_liste as _sign
    from .test_m57_utsending import _signatar, _signer
    oid, pid, kids, _ = _prosess_med_kandidater(migrator, 1)
    # Usignert invitasjonsliste:
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    serie = uuid.uuid4()
    lid, ihash = migrator.execute(
        "SELECT * FROM opprett_utsendingsliste(%s,%s,NULL,%s,"
        "'invitasjon','invitasjon-v1',%s::uuid[])",
        (TENANT, serie, oid, kids)).fetchone()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "SELECT m8_utsted_tidsvalgtoken(%s,%s,%s,%s,%s,30)",
            (TENANT, lid, kids[0], secrets.token_hex(16),
             _mac(secrets.token_hex(32))))
    migrator.rollback()
    # Signert avslagsliste:
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    serie2 = uuid.uuid4()
    lid2, ihash2 = migrator.execute(
        "SELECT * FROM opprett_utsendingsliste(%s,%s,NULL,%s,"
        "'avslag','avslag-v1',%s::uuid[])",
        (TENANT, serie2, oid, kids)).fetchone()
    migrator.execute("RESET ROLE")
    migrator.commit()
    bid = _signatar(migrator)
    _signer(migrator, (lid2, serie2, ihash2), bid)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "SELECT m8_utsted_tidsvalgtoken(%s,%s,%s,%s,%s,30)",
            (TENANT, lid2, kids[0], secrets.token_hex(16),
             _mac(secrets.token_hex(32))))
    migrator.rollback()


@pg
def test_utloper_er_least_av_levetid_og_payloadvindu(migrator):
    """DOM 5: least(now() + levetid, vinduets slutt) — ETT tak, og
    kapabiliteten forlenger aldri kundens frist."""
    oid, pid, kids, _ = _prosess_med_kandidater(migrator, 1)
    lid = _signert_liste(migrator, kids, oid)
    # Prosessens frist er 365 døgn (riggen): levetiden 1 døgn vinner.
    _tid, _sec, utl = _utsted(migrator, lid, kids[0], levetid=1)
    _sett_kontekst(migrator, TENANT)
    naa, vindu = migrator.execute(
        "SELECT now(), coalesce(lukket_ts, opprettet)"
        " + slettefrist_dogn * interval '1 day'"
        " FROM rekrutteringsprosess WHERE tenant=%s AND prosess_id=%s",
        (TENANT, pid)).fetchone()
    migrator.rollback()
    assert abs((utl - naa).total_seconds() - 86400) < 120
    # ... og med levetid langt forbi fristen vinner VINDUET.
    _tid2, _sec2, utl2 = _utsted(migrator, lid, kids[0], levetid=9999)
    assert utl2 == vindu


@pg
def test_ny_token_erstatter_den_aktive(migrator):
    """Retry-dommen (§5): nytt forsøk minter nytt token, det gamle blir
    `erstattet` — én aktiv kapabilitet per manifestmedlem, og den gamle
    lenken er død i BEGGE dører."""
    pid, kids, lid, sid, gammel_id, gammel_secret = _rigg(migrator)
    ny_id, ny_secret, _ = _utsted(migrator, lid, kids[0])
    _sett_kontekst(migrator, TENANT)
    status = dict(migrator.execute(
        "SELECT token_id, status FROM m8_tidsvalgtoken"
        " WHERE tenant=%s AND liste_id=%s", (TENANT, lid)).fetchall())
    migrator.rollback()
    assert status[gammel_id] == "erstattet" and status[ny_id] == "aktiv"
    rt = _rt()
    try:
        assert _oppslag(rt, gammel_id, gammel_secret) == []
        assert _velg(rt, gammel_id, gammel_secret, sid) is None
        rt.rollback()
        assert len(_oppslag(rt, ny_id, ny_secret)) == 1
        rt.rollback()
    finally:
        rt.close()


# ---------------------------------------------------------------------------
# De offentlige dørene: uniform avvisning, valg, kapasitet
# ---------------------------------------------------------------------------

@pg
def test_oppslag_avviser_uniformt(migrator):
    """Codex-porten «MAC-avvik/utløpt/erstattet/reapet → samme kode»:
    alle fire er en TOM retur — API-et kan ikke si mer enn døren gir."""
    pid, kids, lid, sid, token_id, secret = _rigg(migrator)
    rt = _rt()
    try:
        # Feil MAC (riktig form, galt innhold) og ukjent token_id:
        assert _oppslag(rt, token_id, secrets.token_hex(32)) == []
        assert _oppslag(rt, secrets.token_hex(16), secret) == []
        rt.rollback()
        # Utløpt: raden settes inn direkte med utloper i fortiden
        # (bindingsfeltene er immutable, så fortiden må FØDES slik) —
        # som `brukt`, siden en_aktiv_token_per_medlem-indeksen ellers
        # ville avvist en andre aktiv, og et brukt token er GYLDIG for
        # oppslag så lenge det ikke er utløpt.
        ut_id, ut_secret = secrets.token_hex(16), secrets.token_hex(32)
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "INSERT INTO m8_tidsvalgtoken (token_id, tenant, prosess_id,"
            " kandidat_id, liste_id, mac, status, utloper)"
            " SELECT %s, tenant, prosess_id, kandidat_id, liste_id, %s,"
            "        'brukt', now() - interval '1 minute'"
            "   FROM m8_tidsvalgtoken WHERE token_id=%s",
            (ut_id, _mac(ut_secret), token_id))
        migrator.commit()
        assert _oppslag(rt, ut_id, ut_secret) == []
        rt.rollback()
        # Reapet/lukket vindu: tidligslettingen lukker payloadvinduet,
        # og den gyldige kapabiliteten dør MED det (077 er kilden).
        assert len(_oppslag(rt, token_id, secret)) == 1
        rt.rollback()
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT bestill_tidligsletting(%s,%s)", (TENANT, pid))
        rt.commit()
        assert _oppslag(rt, token_id, secret) == []
        assert _velg(rt, token_id, secret, sid) is None
        rt.rollback()
    finally:
        rt.close()


@pg
def test_valget_er_endelig_og_idempotent(migrator):
    """DOM 3: valg → token brukt i SAMME transaksjon; gjenspill med
    samme slot er et stille ja, annen slot avvises — og gjenbesøket
    (oppslaget) viser bekreftelsen."""
    pid, kids, lid, sid, token_id, secret = _rigg(migrator, kap=2)
    sid2 = _slot(migrator, pid, timer=48)
    rt = _rt()
    try:
        utfall, start, slutt = _velg(rt, token_id, secret, sid)
        assert utfall == "valgt" and start is not None
        rt.commit()
        _sett_kontekst(migrator, TENANT)
        rad = migrator.execute(
            "SELECT v.slot_id, t.status, t.brukt_ts"
            "  FROM m8_slotvalg v JOIN m8_tidsvalgtoken t"
            "    ON t.tenant=v.tenant AND t.prosess_id=v.prosess_id"
            "   AND t.kandidat_id=v.kandidat_id"
            " WHERE v.tenant=%s AND v.prosess_id=%s AND v.kandidat_id=%s",
            (TENANT, pid, kids[0])).fetchone()
        migrator.rollback()
        assert rad[0] == sid and rad[1] == "brukt" and rad[2] is not None
        # Gjenspill: samme slot → stille ja; annen slot → avvist.
        assert _velg(rt, token_id, secret, sid)[0] == "valgt"
        rt.commit()
        assert _velg(rt, token_id, secret, sid2)[0] == "valg_alt_registrert"
        rt.rollback()
        # Gjenbesøket: brukt token slår fortsatt opp, med valget markert.
        rader = _oppslag(rt, token_id, secret)
        rt.rollback()
        assert rader and all(r[0] == sid for r in rader)
    finally:
        rt.close()


@pg
def test_to_samtidige_valg_paa_siste_plass_gir_en_vinner(migrator):
    """Codex-porten: radlåsen på sloten serialiserer kapasiteten —
    telleren måles UNDER låsen, så to samtidige valg på siste plass gir
    nøyaktig én vinner og ett `slot_fullt`."""
    pid, kids, lid, sid, tok_a, sec_a = _rigg(migrator, antall=2, kap=1)
    tok_b, sec_b, _ = _utsted(migrator, lid, kids[1])
    c1, c2 = _rt(), _rt()
    utfall_b: dict = {}
    try:
        assert _velg(c1, tok_a, sec_a, sid)[0] == "valgt"  # ucommittet

        def taper():
            try:
                utfall_b["rad"] = _velg(c2, tok_b, sec_b, sid)
                c2.commit()
            except Exception as e:      # noqa: BLE001
                utfall_b["feil"] = e

        t = threading.Thread(target=taper)
        t.start()
        t.join(timeout=2)
        assert t.is_alive(), "kandidat B skulle blokkere på radlåsen"
        c1.commit()
        t.join(timeout=10)
        assert not t.is_alive()
        assert "feil" not in utfall_b, utfall_b
        assert utfall_b["rad"][0] == "slot_fullt"
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT count(*) FROM m8_slotvalg WHERE tenant=%s"
            " AND slot_id=%s AND slettet_ts IS NULL",
            (TENANT, sid)).fetchone()[0] == 1
        migrator.rollback()
    finally:
        c1.close()
        c2.close()


@pg
def test_direkte_dml_gjor_dobbeltvalg_og_fremmed_kandidat_urepresenterbar(
        migrator):
    """Codex-porten: PK-en (ETT valg per kandidat per prosess) og FK-ene
    holder også mot eieren — dørens dommer er lag to."""
    pid, kids, lid, sid, token_id, secret = _rigg(migrator, kap=5)
    rt = _rt()
    try:
        assert _velg(rt, token_id, secret, sid)[0] == "valgt"
        rt.commit()
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.UniqueViolation):
        migrator.execute(
            "INSERT INTO m8_slotvalg (tenant, prosess_id, kandidat_id,"
            " slot_id, innhold_sha256) VALUES (%s,%s,%s,%s,'')",
            (TENANT, pid, kids[0], sid))
    migrator.rollback()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        migrator.execute(
            "INSERT INTO m8_slotvalg (tenant, prosess_id, kandidat_id,"
            " slot_id, innhold_sha256) VALUES (%s,%s,%s,%s,'')",
            (TENANT, pid, uuid.uuid4(), sid))
    migrator.rollback()


# ---------------------------------------------------------------------------
# Reaping: valget er det åttende lageret
# ---------------------------------------------------------------------------

@pg
def test_reaping_tommer_valget_og_lar_sloten_besta(migrator):
    """Codex-porten «fixture i m8_slotvalg → null treff etter reaping»:
    valget følger prosessens frist og tidligslettingen gratis (077);
    sloten består som evidens, tokenraden består uten PII — og den
    gamle lenken er død by construction."""
    from .test_m57_kandidatlagre import _reaperkobling
    pid, kids, lid, sid, token_id, secret = _rigg(migrator)
    rt = _rt()
    try:
        assert _velg(rt, token_id, secret, sid)[0] == "valgt"
        rt.commit()
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT bestill_tidligsletting(%s,%s)", (TENANT, pid))
        rt.commit()
    finally:
        rt.close()
    rp, _rolle = _reaperkobling()
    try:
        reapet = {r[1] for r in rp.execute(
            "SELECT * FROM reap_kandidatdata(50)").fetchall()}
        rp.commit()
    finally:
        rp.close()
    assert pid in reapet
    _sett_kontekst(migrator, TENANT)
    valg = migrator.execute(
        "SELECT slot_id, slettet_ts FROM m8_slotvalg WHERE tenant=%s"
        " AND prosess_id=%s AND kandidat_id=%s",
        (TENANT, pid, kids[0])).fetchone()
    slot = migrator.execute(
        "SELECT status FROM m8_slot WHERE tenant=%s AND slot_id=%s",
        (TENANT, sid)).fetchone()
    token = migrator.execute(
        "SELECT status FROM m8_tidsvalgtoken WHERE token_id=%s",
        (token_id,)).fetchone()
    migrator.rollback()
    assert valg[0] is None and valg[1] is not None, \
        "valget skal reapes (payload til NULL)"
    assert slot is not None, "sloten er evidens og består"
    assert token is not None, "tokenraden bærer null PII og består"
    rt = _rt()
    try:
        assert _oppslag(rt, token_id, secret) == []
        rt.rollback()
    finally:
        rt.close()


@pg
def test_valget_kan_ikke_reapes_alene(migrator):
    """Port 19 (076-formen) med det åttende medlemmet: ETT lager alene
    er en blanding, og COMMIT-en avvises — også for eieren."""
    pid, kids, lid, sid, token_id, secret = _rigg(migrator)
    rt = _rt()
    try:
        assert _velg(rt, token_id, secret, sid)[0] == "valgt"
        rt.commit()
        # Et levende NABOLAGER, så blandingen finnes å måle.
        _sett_kontekst(rt, TENANT)
        rt.execute(
            "INSERT INTO kandidat_intervjusporsmal (tenant, prosess_id,"
            " kandidat_id, sporsmal, innhold_sha256)"
            " VALUES (%s,%s,%s,'[\"q\"]'::jsonb,'x')",
            (TENANT, pid, kids[0]))
        rt.commit()
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "UPDATE m8_slotvalg SET slot_id=NULL, slettet_ts=now()"
        " WHERE tenant=%s AND prosess_id=%s", (TENANT, pid))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.commit()
    migrator.rollback()


# ---------------------------------------------------------------------------
# Rettighetsgrensene
# ---------------------------------------------------------------------------

@pg
def test_runtime_naar_aldri_tokentabellen_eller_utstederen(migrator):
    """api_tokener-formen: runtime har NULL tabellrettigheter på
    kapabiliteten og ingen EXECUTE på utstederdøren — kun de to
    offentlige dørene og lesingen av slots/valg."""
    _prosess_med_kandidater(migrator, 1)  # sikrer levende skjemaobjekter
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT count(*) FROM m8_tidsvalgtoken")
        rt.rollback()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute(
                "SELECT m8_utsted_tidsvalgtoken(%s,%s,%s,%s,%s,30)",
                (TENANT, uuid.uuid4(), uuid.uuid4(),
                 secrets.token_hex(16), _mac(secrets.token_hex(32))))
        rt.rollback()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute(
                "INSERT INTO m8_slotvalg (tenant, prosess_id,"
                " kandidat_id, slot_id, innhold_sha256)"
                " VALUES (%s,%s,%s,%s,'')",
                (TENANT, uuid.uuid4(), uuid.uuid4(), uuid.uuid4()))
        rt.rollback()
    finally:
        rt.close()


# ---------------------------------------------------------------------------
# Utsenderen: e-post sendt => token committet
# ---------------------------------------------------------------------------

def _utsendingsdata_uten_lenke(m, pid, kids):
    """Lagerformen ETTER §5: flettefeltene bærer IKKE tidsvalg_lenke —
    den er utstedelsens felt og overskrives av utsenderen."""
    import json
    _sett_kontekst(m, TENANT)
    adresser = []
    for i, kid in enumerate(kids):
        adresse = f"m8-kandidat-{i}-{secrets.token_hex(3)}@eksempel.invalid"
        adresser.append(adresse)
        m.execute(
            "INSERT INTO kandidat_utsendingsdata (tenant, prosess_id,"
            " kandidat_id, mottaker_ref, flettefelt, innhold_sha256)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (TENANT, pid, kid, adresse,
             json.dumps({"kandidatnavn": "[NAVN-1]",
                         "stilling": "Demo-stilling"}), "0" * 64))
    m.commit()
    return adresser


_LENKEMONSTER = re.compile(
    r"https://kunde\.example/tidsvalg#tid_([0-9a-f]{32})\.([0-9a-f]{64})")


@pg
def test_epost_sendt_krever_committet_token(migrator, monkeypatch):
    """Codex-porten «e-post sendt ⇒ token committet» (§5,
    rekkefølgeporten): lenken i den sendte e-posten peker på et token
    som STÅR i basen med riktig MAC — og lagerets felt er overskrevet,
    aldri kilden."""
    from drift import m57_utsender
    monkeypatch.setenv("DISPONIT_HOST", "kunde.example")
    oid, pid, kids, _ = _prosess_med_kandidater(migrator, 2)
    _utsendingsdata_uten_lenke(migrator, pid, kids)
    lid = _signert_liste(migrator, kids, oid)
    _slot(migrator, pid)
    sendte = []
    snd = _sender()
    try:
        res = m57_utsender.kjor(
            snd, send=lambda til, emne, tekst: sendte.append((til, tekst)))
    finally:
        snd.close()
    assert res["sendt"] == 2, res
    _sett_kontekst(migrator, TENANT)
    tokener = {t_id: mac for t_id, mac in migrator.execute(
        "SELECT token_id, mac FROM m8_tidsvalgtoken WHERE tenant=%s"
        " AND liste_id=%s AND status='aktiv'", (TENANT, lid)).fetchall()}
    migrator.rollback()
    assert len(tokener) == 2
    for _til, tekst in sendte:
        m = _LENKEMONSTER.search(tekst)
        assert m, f"invitasjonen bærer ingen tidsvalg-lenke: {tekst!r}"
        token_id, secret = m.group(1), m.group(2)
        assert tokener.get(token_id) == _mac(secret), \
            "lenken peker ikke på et committet token med riktig MAC"


@pg
def test_uten_host_eller_pepper_roeres_ingen_invitasjon(migrator,
                                                        monkeypatch):
    """Driftstilstanden (smtp_ikke_konfigurert-dommen): mangler
    pepper/host klaimes ingen invitasjonsrad — ingen forsøksteller
    brennes, raden står sendeklar til config er på plass."""
    from drift import m57_utsender
    monkeypatch.delenv("DISPONIT_HOST", raising=False)
    oid, pid, kids, _ = _prosess_med_kandidater(migrator, 1)
    _utsendingsdata_uten_lenke(migrator, pid, kids)
    lid = _signert_liste(migrator, kids, oid)
    _slot(migrator, pid)
    snd = _sender()
    try:
        res = m57_utsender.kjor(
            snd, send=lambda *a: (_ for _ in ()).throw(
                AssertionError("sendt uten tidsvalg-oppsett!")))
    finally:
        snd.close()
    assert res["sendt"] == 0 and res.get("tidsvalg_stanset") == 1, res
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT count(*) FROM m57_utsendingskvittering WHERE tenant=%s"
        " AND liste_id=%s", (TENANT, lid)).fetchone()[0] == 0
    migrator.rollback()


class _Sabotor:
    """Kobling som feller NØYAKTIG første utstederkall — resten går
    urørt gjennom (feilet-mint-porten under)."""

    def __init__(self, conn):
        self._c = conn
        self.igjen = 1

    def execute(self, sql, *a, **k):
        if "m8_utsted_tidsvalgtoken" in sql and self.igjen > 0:
            self.igjen -= 1
            raise RuntimeError("injisert tokenfeil")
        return self._c.execute(sql, *a, **k)

    def commit(self):
        self._c.commit()

    def rollback(self):
        self._c.rollback()

    def close(self):
        self._c.close()


@pg
def test_feilet_minting_kvitteres_feilet_og_retry_minter_nytt(
        migrator, monkeypatch):
    """§5 retry-dommen: dør utstedelsen FØR aksept, kvitteres raden
    `feilet` (beviselig ingen e-post ute) — og neste kjøring minter et
    NYTT token og sender."""
    from drift import m57_utsender
    monkeypatch.setenv("DISPONIT_HOST", "kunde.example")
    oid, pid, kids, _ = _prosess_med_kandidater(migrator, 1)
    _utsendingsdata_uten_lenke(migrator, pid, kids)
    lid = _signert_liste(migrator, kids, oid)
    _slot(migrator, pid)
    snd = _Sabotor(_sender())
    try:
        res = m57_utsender.kjor(
            snd, send=lambda *a: (_ for _ in ()).throw(
                AssertionError("sendt uten token!")))
    finally:
        snd.close()
    assert res["sendt"] == 0 and res["feilet"] == 1, res
    _sett_kontekst(migrator, TENANT)
    status, feil = migrator.execute(
        "SELECT status, feil FROM m57_utsendingskvittering"
        " WHERE tenant=%s AND liste_id=%s AND kandidat_id=%s",
        (TENANT, lid, kids[0])).fetchone()
    migrator.rollback()
    assert status == "feilet" and "tidsvalg_token_feilet" in feil
    # Retry: nytt forsøk minter nytt token og sender.
    sendte = []
    snd2 = _sender()
    try:
        res2 = m57_utsender.kjor(
            snd2, send=lambda til, emne, tekst: sendte.append(tekst))
    finally:
        snd2.close()
    assert res2["sendt"] == 1, res2
    assert _LENKEMONSTER.search(sendte[0])


# ---------------------------------------------------------------------------
# HTTP: den offentlige ruteklassen
# ---------------------------------------------------------------------------

@pg
def test_http_offentlig_oppslag_og_valg(migrator, klient):
    """Ingen cookie, ingen sesjon, ingen CSRF — kapabiliteten i kroppen
    er credentialet. Uniform `tidsvalg_avvist` for hele
    avvisningsklassen; kun slot_fullt/valg_alt_registrert skilles."""
    pid, kids, lid, sid, token_id, secret = _rigg(migrator, kap=2)
    sid2 = _slot(migrator, pid, timer=48)
    token = f"tid_{token_id}.{secret}"
    r = klient.post("/v1/tidsvalg/oppslag", json={"token": token})
    assert r.status_code == 200, r.text
    svar = r.json()
    assert svar["valgt_slot"] is None
    assert {s["slot_id"] for s in svar["slots"]} == {str(sid), str(sid2)}
    assert all(s["ledig"] is True for s in svar["slots"])
    # DOM 4: aldri tellere — kun binært ledig/fullt.
    assert not any("kapasitet" in s or "antall" in str(sorted(s))
                   for s in svar["slots"])
    # Feil MAC og feilformet token: SAMME kode, 403.
    for daarlig in (f"tid_{token_id}.{secrets.token_hex(32)}",
                    "tid_kort.feil", ""):
        r2 = klient.post("/v1/tidsvalg/oppslag", json={"token": daarlig})
        assert r2.status_code == 403 and \
            r2.json()["feil"] == "tidsvalg_avvist", r2.text
    # Ikke-JSON er en FORMfeil, ikke en tokendom.
    r3 = klient.post("/v1/tidsvalg/oppslag", content=b"x=y",
                     headers={"content-type":
                              "application/x-www-form-urlencoded"})
    assert r3.status_code == 400
    # Valget: 200 med tiden gjentatt, og valget står i basen.
    r4 = klient.post("/v1/tidsvalg/velg",
                     json={"token": token, "slot_id": str(sid)})
    assert r4.status_code == 200, r4.text
    assert r4.json()["valgt"] is True and r4.json()["start"]
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT count(*) FROM m8_slotvalg WHERE tenant=%s AND slot_id=%s"
        " AND slettet_ts IS NULL", (TENANT, sid)).fetchone()[0] == 1
    migrator.rollback()
    # Annen slot etterpå: skillbart utfall, 409.
    r5 = klient.post("/v1/tidsvalg/velg",
                     json={"token": token, "slot_id": str(sid2)})
    assert r5.status_code == 409 and \
        r5.json()["feil"] == "valg_alt_registrert"
    # Gjenbesøket viser valget.
    r6 = klient.post("/v1/tidsvalg/oppslag", json={"token": token})
    assert r6.status_code == 200 and r6.json()["valgt_slot"] == str(sid)


@pg
def test_http_ratebotta_teller_ogsaa_avvisninger(migrator, klient,
                                                 monkeypatch):
    """App-bøtta (§3): nøklet på IP + token_id etter formatsjekk, og en
    avvist forespørsel teller — en skanner får 429, aldri fri måling."""
    from api import sesjon as sesjonmodul
    monkeypatch.setitem(sesjonmodul.RATE, "tidsvalg", (300, 1, 0))
    token = f"tid_{secrets.token_hex(16)}.{secrets.token_hex(32)}"
    r1 = klient.post("/v1/tidsvalg/oppslag", json={"token": token})
    assert r1.status_code == 403
    r2 = klient.post("/v1/tidsvalg/oppslag", json={"token": token})
    assert r2.status_code == 429 and r2.json()["feil"] == "rate_grense"


@pg
def test_to_samtidige_gjenspill_av_samme_token_serialiseres(migrator):
    """CodeRabbit på 082: uten lås på tokenraden kunne to samtidige
    gjenspill av SAMME token (dobbeltklikk/nettverksretry) kappløpe
    forbi valg-sjekken og ende i PK-en eller et falskt `slot_fullt`.
    FOR UPDATE i skriveveien serialiserer: begge får det stille jaet."""
    pid, kids, lid, sid, token_id, secret = _rigg(migrator, kap=1)
    c1, c2 = _rt(), _rt()
    utfall_b: dict = {}
    try:
        assert _velg(c1, token_id, secret, sid)[0] == "valgt"  # ucommittet

        def gjenspill():
            try:
                utfall_b["rad"] = _velg(c2, token_id, secret, sid)
                c2.commit()
            except Exception as e:      # noqa: BLE001
                utfall_b["feil"] = e

        t = threading.Thread(target=gjenspill)
        t.start()
        t.join(timeout=2)
        assert t.is_alive(), "gjenspillet skulle blokkere på tokenlåsen"
        c1.commit()
        t.join(timeout=10)
        assert not t.is_alive()
        assert "feil" not in utfall_b, utfall_b
        assert utfall_b["rad"][0] == "valgt", \
            "gjenspill av samme valg skal være et stille ja — aldri" \
            f" et kappløp: {utfall_b}"
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT count(*) FROM m8_slotvalg WHERE tenant=%s"
            " AND slot_id=%s AND slettet_ts IS NULL",
            (TENANT, sid)).fetchone()[0] == 1
        migrator.rollback()
    finally:
        c1.close()
        c2.close()


@pg
def test_http_slots_idempotens_gjelder_bunten(migrator, klient):
    """CodeRabbit: SP-2 på slots-POST-en gjelder BUNTEN — identisk
    gjenspill er et stille ja med samme id-er; samme nøkkel med en
    kortere, lengre eller endret bunt er en konflikt."""
    from .test_rekruttering_http import _post  # gjenbruk av POST-formen
    import json as _json
    oid, pid, kids, _ = _prosess_med_kandidater(migrator, 1)
    # Browserøkten må bo i m57-riggens TENANT — testens egen minirigg:
    _sett_kontekst(migrator, TENANT)
    bid = migrator.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://m8.test', %s) RETURNING bruker_id",
        ("s8-" + secrets.token_hex(6),)).fetchone()[0]
    migrator.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller, aktiv)"
        " VALUES (%s,%s,%s,true)", (TENANT, bid, ["admin"]))
    from api import sesjon as sesjonmodul
    cookie, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    ver = migrator.execute(
        "SELECT authz_version FROM brukermedlemskap WHERE tenant=%s"
        " AND bruker_id=%s", (TENANT, bid)).fetchone()[0]
    migrator.execute(
        "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
        " authz_snapshot, csrf_hash, opprettet, siste_bruk, utloper,"
        " tilbakekalt) VALUES (%s,%s,%s,%s,%s, now(), now(),"
        " now()+interval '1 hour', false)",
        (sesjonmodul._hash(cookie), TENANT, bid, ver,
         sesjonmodul._hash(csrf)))
    migrator.commit()
    a = {"start": "2027-01-10T09:00:00+00:00",
         "slutt": "2027-01-10T10:00:00+00:00", "kapasitet": 2}
    b = {"start": "2027-01-11T09:00:00+00:00",
         "slutt": "2027-01-11T10:00:00+00:00", "kapasitet": 2}
    nokkel = "m8-bunt-" + secrets.token_hex(6)
    kropp = {"prosess_id": str(pid), "slots": [a, b]}
    r1 = _post(klient, cookie, csrf, "/v1/rekruttering/tidsvalg/slots",
               kropp, idem=nokkel)
    assert r1.status_code == 201, r1.text
    # Identisk gjenspill: stille ja, SAMME id-er, ingen nye rader.
    r2 = _post(klient, cookie, csrf, "/v1/rekruttering/tidsvalg/slots",
               _json.loads(_json.dumps(kropp)), idem=nokkel)
    assert r2.status_code == 201, r2.text
    assert r2.json()["slot_ids"] == r1.json()["slot_ids"]
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT count(*) FROM m8_slot WHERE tenant=%s AND prosess_id=%s",
        (TENANT, pid)).fetchone()[0] == 2
    migrator.rollback()
    # Kortere, lengre og endret bunt på SAMME nøkkel: konflikt, aldri
    # en delvis opprettelse.
    for annen in ([a], [a, b, dict(b, start="2027-01-12T09:00:00+00:00",
                                   slutt="2027-01-12T10:00:00+00:00")],
                  [a, dict(b, kapasitet=3)]):
        r = _post(klient, cookie, csrf,
                  "/v1/rekruttering/tidsvalg/slots",
                  {"prosess_id": str(pid), "slots": annen}, idem=nokkel)
        assert r.status_code == 409, (annen, r.text)
        assert r.json()["feil"] == "idempotenskonflikt"
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT count(*) FROM m8_slot WHERE tenant=%s AND prosess_id=%s",
        (TENANT, pid)).fetchone()[0] == 2, "konflikten skapte rader"
    migrator.rollback()
