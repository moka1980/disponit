"""v1-restansen inn i kandidatdatagrensen (BESLUTNING-168 §2, migrasjon
073): prosesser reapet av 057-reaperen FØR 067/069-vaktene fantes står
med promotert v1-rapport som fortsatt bærer payload, utestengt av
`slettet_ts`-predikatet for alltid. 073 gir reaperen en restanse-arm
som tømmer ved kundefristen, dog senest på dommens dato (eiervalget
30/8: 14. september 2026) — samme reaper, samme dør, ingen ny
mekanisme, ingen omskrevet historikk.

Riggen skriver restansetilstanden med radvaktene AV (059-formen), fordi
poenget med 069-vakten er at tilstanden ikke KAN oppstå lenger — testen
gjenskaper historikken vakten kom for sent til. Tidsstemplene er FASTE
datoer, ikke now()-avstander: armen er bundet av 31/8-grensen, og en
test som regnet seg bakover fra kjøredagen ville krysset grensen i
september og blitt stille grønn på feil premiss."""
import secrets

import psycopg  # noqa: F401 — feilklassene i portene under
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       migrator, miljo)
from .test_m57_kandidatlagre import (FIXTUR, _prosess, _reaperkobling,
                                     _sett_kontekst)
from .test_m57_utsending import _rt, pg
from .test_skjemaversjon import _nytt_v2, _testtype, _versjonsdør

#: 057-reapet før vaktene (< armens 31/8-grense).
SLETTET = "2026-08-29T12:00:00+00:00"
#: Lukket med kundefristen (90 døgn) alt utløpt: 1/5 + 90 døgn = 30/7 —
#: passert for enhver kjøredag etter det, uansett når suiten kjører.
#: Armen tar da radene ved FRISTEN, uavhengig av dommens dato-tak.
LUKKET = "2026-05-01T00:00:00+00:00"


def _restanse(migrator, rt, ttype, *, versjon=1, innhold=None,
              slettet=SLETTET):
    """Prosess i restansetilstand + promotert artefakt på oppdraget."""
    from db import kryptering
    oid, pid = _prosess(migrator, rt, frist=90)
    rt.commit()   # prosessraden fødes på runtime-koblingen
    _sett_kontekst(migrator, TENANT)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator, TENANT)
    ct, nonce = kryptering.krypter(
        dek, innhold or {"rapporttype": ttype,
                         "kandidater": {"k1": {"kildetekst": FIXTUR}}},
        TENANT, key_id)
    migrator.execute(
        "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype,"
        " modul_id, release_id, kontraktversjon, kontrakt_hash,"
        " module_epoch, tilstand, storrelse_bytes, klartekst_sha256,"
        " ciphertext, nonce, dek_ref, kapabilitet_jti, promotert_ts,"
        " skjemaversjon)"
        " SELECT %s, %s, %s, 'm57_ats', r.release_id, 1,"
        "        k.kontrakt_hash, h.module_epoch, 'promotert', 10, 'h',"
        "        %s, %s, %s, %s, now(), %s"
        "   FROM moduldeployment r, modulkontrakt k, modulhode h"
        "  WHERE r.modul_id='m57_ats' AND k.modul_id='m57_ats'"
        "    AND k.kontraktversjon=1"
        "    AND h.modul_id='m57_ats' LIMIT 1",
        (TENANT, oid, ttype, ct, nonce, key_id,
         "jti-" + secrets.token_hex(6), versjon))
    # Historikken vaktene kom for sent til: reapet-merket satt mens
    # rapporten bærer payload. Radvaktene av i nøyaktig denne
    # skrivingen — 069-vakten ville med rette nektet den.
    migrator.execute("ALTER TABLE rekrutteringsprosess"
                     " DISABLE TRIGGER USER")
    migrator.execute(
        "UPDATE rekrutteringsprosess SET lukket_ts=%s, slettet_ts=%s"
        " WHERE tenant=%s AND prosess_id=%s",
        (LUKKET, slettet, TENANT, pid))
    migrator.execute("ALTER TABLE rekrutteringsprosess"
                     " ENABLE TRIGGER USER")
    migrator.commit()
    return oid, pid


def _sveip():
    rp, _timer = _reaperkobling()
    try:
        rader = rp.execute("SELECT * FROM reap_kandidatdata(50)").fetchall()
        rp.commit()
        return [(r[0], r[1]) for r in rader]
    finally:
        rp.close()


@pg
def test_restansen_tommes_ved_frist_og_raden_bestaar(migrator, miljo):
    """Dommens §2 + §5 i ett: payloaden tømmes ved den kortede fristen,
    og tømmingen er IKKE en tilstandsendring — raden består promotert
    med hashen sin, og prosessens historiske merker står urørt."""
    ttype = _testtype(migrator)
    oid, pid = _restanse(migrator, _rt(), ttype)
    tatt = _sveip()
    assert (TENANT, pid) in tatt
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT tilstand, makulert_ts IS NOT NULL, ciphertext IS NULL,"
        "       nonce IS NULL, klartekst_sha256"
        "  FROM artefakt WHERE tenant=%s AND oppdrag_id=%s",
        (TENANT, oid)).fetchone()
    assert rad == ("promotert", True, True, True, "h")
    prosess = migrator.execute(
        "SELECT slettet_ts, lukket_ts FROM rekrutteringsprosess"
        " WHERE tenant=%s AND prosess_id=%s", (TENANT, pid)).fetchone()
    migrator.rollback()
    assert [t.isoformat() for t in prosess] == \
        ["2026-08-29T12:00:00+00:00", "2026-05-01T00:00:00+00:00"]
    # Andre sveip er et stille nei: alt er tømt, raden rapporteres ikke
    # som reapet igjen.
    assert (TENANT, pid) not in _sveip()


@pg
def test_restansearmen_er_bundet_av_31_august(migrator, miljo):
    """En prosess reapet ETTER grensen kan ikke være restanse —
    069-vakten garanterer at den er payloadfri, og armen skal aldri
    vokse. (Riggen bryter garantien med vilje; armen skal likevel la
    raden ligge, for settet er lukket bakover.)"""
    ttype = _testtype(migrator)
    oid, pid = _restanse(migrator, _rt(), ttype,
                         slettet="2026-09-02T00:00:00+00:00")
    assert (TENANT, pid) not in _sveip()
    _sett_kontekst(migrator, TENANT)
    urort = migrator.execute(
        "SELECT makulert_ts IS NULL AND ciphertext IS NOT NULL"
        "  FROM artefakt WHERE tenant=%s AND oppdrag_id=%s",
        (TENANT, oid)).fetchone()[0]
    migrator.rollback()
    assert urort


@pg
def test_payloadfri_restanse_bestaar_og_rapporteres_ikke(migrator, miljo):
    """Et payloadfritt beslutningsspor (v2) på en restanseprosess er
    varig evidens: døren hopper over det (072), sveipet melder ikke
    raden, og ciphertexten består."""
    ttype = _testtype(migrator)
    _sett_kontekst(migrator, TENANT)
    v2 = _nytt_v2(migrator)
    _versjonsdør(migrator, ttype, 2, v2, True, "test")
    migrator.commit()
    oid, pid = _restanse(
        migrator, _rt(), ttype, versjon=2,
        innhold={"rapporttype": ttype,
                 "rangering": [{"kandidat_id": "k1", "poeng": 3}]})
    assert (TENANT, pid) not in _sveip()
    _sett_kontekst(migrator, TENANT)
    urort = migrator.execute(
        "SELECT makulert_ts IS NULL AND ciphertext IS NOT NULL"
        "  FROM artefakt WHERE tenant=%s AND oppdrag_id=%s",
        (TENANT, oid)).fetchone()[0]
    migrator.rollback()
    assert urort
