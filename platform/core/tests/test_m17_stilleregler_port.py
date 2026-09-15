"""Porten for stille avsendere (migrasjon 204).

SETT PÅ SKJERMEN 15/9: broen (203) gjorde 18 e-poster til henvendelser,
og alle 18 var systemvarsler som sto ÅPNE og UKLASSIFISERTE til et
menneske klikket seg gjennom dem. Dommen: REGEL FØRST, MODELL SENERE.

SJU PORTER:
  1. En avsender tenanten har navngitt som stille (domene) klassifiseres
     av seg selv i planrunden — kilde `regel`, uten digest, med evidens.
  2. Adresseregler matcher på HASH: adressen finnes aldri i regeltabellen.
  3. En avsender UTEN regel røres ikke.
  4. ET MENNESKES DOM STÅR: en alt klassifisert henvendelse
     omklassifiseres aldri av en regel.
  5. Tenantskille: tenant As regel når aldri tenant Bs henvendelser.
  6. Runde nummer to gjør ingenting — idempotent.
  7. Kill-switch stopper runden uten å røre noe.

MUTASJONENE ER KJØRT, og to av tre falt IKKE — og det står her fordi
det er sant:

  * «match på hele masken i stedet for domenet etter @» → port 1 FALLER.
  * «fjern NOT EXISTS (… klassifisering …)» → port 4 står. Garantien
    er PRIMÆRNØKKELEN (tenant, henvendelse_id) + `ON CONFLICT DO NOTHING`:
    en rad som finnes kan ikke skrives over av definereren uansett.
    `NOT EXISTS` er en billigere vei forbi, ikke vernet.
  * «slett r.tenant = h.tenant i JOIN-en» → port 5 står. Garantien er
    RLS på BEGGE tabellene: med `disponit.tenant = v_t` satt ser
    definereren bare v_ts rader i både `henvendelse` og
    `kundeserviceregel`. Join-betingelsen er belte i tillegg til
    bukseselene.

Port 4 og 5 måler altså at definereren IKKE DEFEATER de to garantiene —
ikke at den lager dem. Å påstå noe annet ville vært en port som lyver om
sin egen mekanisme.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid

import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo  # noqa: F401
from .test_m37 import _sett_kontekst

PLAN_DSN = os.environ.get("DISPONIT_TEST_PLAN_DSN")
pg = pytest.mark.skipif(not (DSN and MIGRATOR_DSN and PLAN_DSN),
                        reason="test-DSN/PLAN_DSN ikke satt")


def _rt():
    from db.pg import koble
    return koble(DSN)


def _pa():
    from db.pg import koble
    return koble(PLAN_DSN)


def _tenant(merke):
    return f"t-204-{merke}-{secrets.token_hex(4)}"


def _nokkel(c, tenant):
    from db import kryptering
    _sett_kontekst(c, tenant)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(c, tenant)
    c.commit()
    return key_id, dek


def _hash(adr):
    return hashlib.sha256(adr.strip().lower().encode()).hexdigest()


def _henvendelse(c, tenant, key_id, dek, avsender):
    """Gjennom inntaksdøra, 15-arg-formen — som broen gjør det, så raden
    bærer avsender_maske OG avsender_hash."""
    from api.kundeservice import (_AAD_AVSENDER, _avsendermaske, _krypter)
    hid = uuid.uuid4()
    e, en = _krypter(dek, key_id, tenant, "Emne")
    k, kn = _krypter(dek, key_id, tenant, "Tekst")
    a, an = _krypter(dek, key_id, tenant, avsender, aad=_AAD_AVSENDER)
    _sett_kontekst(c, tenant)
    rad = c.execute(
        "SELECT * FROM m17_ta_imot(%s,%s,'epost',%s,now(),%s,%s,%s,%s,%s,"
        "                          %s,'u-test',%s,%s,%s)",
        (tenant, hid, "MSG-" + secrets.token_hex(4), _hash(avsender),
         e, en, k, kn, key_id, _avsendermaske(avsender), a, an)).fetchone()
    c.commit()
    return rad[1]


def _regler(c, tenant, regler):
    _sett_kontekst(c, tenant)
    n = c.execute("SELECT m17_sett_stilleregler(%s,%s::jsonb,'u-test')",
                  (tenant, json.dumps(regler))).fetchone()[0]
    c.commit()
    return n


def _klassifisering(m, tenant, hid):
    _sett_kontekst(m, tenant)
    r = m.execute("SELECT handlingstype, kilde, modell_digest, opprettet_av"
                  "  FROM klassifisering"
                  " WHERE tenant=%s AND henvendelse_id=%s",
                  (tenant, hid)).fetchone()
    m.rollback()
    return r


def _runde(**kw):
    from plan.stilleregler import kjor_en_runde
    pa = _pa()
    try:
        return kjor_en_runde(pa, **kw)
    finally:
        pa.close()


@pg
def test_domeneregel_klassifiserer_i_planrunden(migrator, miljo,  # noqa: F811
                                                monkeypatch):
    """PORT 1 og 3."""
    monkeypatch.delenv("DISPONIT_STILLEREGLER", raising=False)
    t = _tenant("dom")
    c = _rt()
    try:
        kid, dek = _nokkel(c, t)
        assert _regler(c, t, [{"art": "domene",
                               "monster": "accountprotection.microsoft.com",
                               "handlingstype": "til_info"}]) == 1
        stille = _henvendelse(c, t, kid, dek,
                              "Account@AccountProtection.Microsoft.com")
        kunde = _henvendelse(c, t, kid, dek, "kari@nordvik.example")
    finally:
        c.close()

    res = _runde()
    assert res["klassifisert"] >= 1, res

    k = _klassifisering(migrator, t, stille)
    assert k is not None, "den stille avsenderen ble ikke klassifisert"
    handling, kilde, digest, av = k
    assert (handling, kilde, digest) == ("til_info", "regel", None)
    assert av == "agent:stilleregel"
    # PORT 3: kunden røres ikke.
    assert _klassifisering(migrator, t, kunde) is None, \
        "en avsender uten regel ble klassifisert"
    # …og evidensen står, med kilden.
    _sett_kontekst(migrator, t)
    n = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND kilde='m17_kundeservice'"
        " AND handling='henvendelse.klassifisert'"
        " AND aktor='agent:stilleregel'", (t,)).fetchone()[0]
    migrator.rollback()
    assert n == 1, f"evidens for regelklassifisering: {n}"


@pg
def test_adresseregel_matcher_paa_hash(migrator, miljo,  # noqa: F811
                                       monkeypatch):
    """PORT 2. Adressen finnes ALDRI i regeltabellen."""
    monkeypatch.delenv("DISPONIT_STILLEREGLER", raising=False)
    t = _tenant("adr")
    c = _rt()
    try:
        kid, dek = _nokkel(c, t)
        _regler(c, t, [{"art": "adresse", "monster": _hash("NoReply@x.no"),
                        "handlingstype": "nyhetsbrev"}])
        h = _henvendelse(c, t, kid, dek, "noreply@x.no")
    finally:
        c.close()
    _runde()
    k = _klassifisering(migrator, t, h)
    assert k and k[0] == "nyhetsbrev" and k[1] == "regel"
    _sett_kontekst(migrator, t)
    r = migrator.execute(
        "SELECT monster FROM kundeserviceregel WHERE tenant=%s",
        (t,)).fetchone()[0]
    migrator.rollback()
    assert "@" not in r and len(r) == 64, "adressen ligger i klartekst"


@pg
def test_et_menneskes_dom_staar(migrator, miljo, monkeypatch):  # noqa: F811
    """PORT 4."""
    monkeypatch.delenv("DISPONIT_STILLEREGLER", raising=False)
    t = _tenant("dom4")
    c = _rt()
    try:
        kid, dek = _nokkel(c, t)
        _regler(c, t, [{"art": "domene", "monster": "x.no"}])
        h = _henvendelse(c, t, kid, dek, "noen@x.no")
        _sett_kontekst(c, t)
        c.execute("SELECT m17_klassifiser(%s,%s,'hoy','klage','svar_kreves',"
                  "'menneske',NULL,'u-test')", (t, h))
        c.commit()
    finally:
        c.close()
    _runde()
    k = _klassifisering(migrator, t, h)
    assert k[:2] == ("svar_kreves", "menneske"), \
        f"regelen overstyrte et menneske: {k}"


@pg
def test_tenantskille_og_idempotens(migrator, miljo,  # noqa: F811
                                    monkeypatch):
    """PORT 5 og 6."""
    monkeypatch.delenv("DISPONIT_STILLEREGLER", raising=False)
    a, b = _tenant("a"), _tenant("b")
    c = _rt()
    try:
        ka, da = _nokkel(c, a)
        kb, db = _nokkel(c, b)
        _regler(c, a, [{"art": "domene", "monster": "stille.no"}])
        ha = _henvendelse(c, a, ka, da, "p@stille.no")
        hb = _henvendelse(c, b, kb, db, "p@stille.no")
    finally:
        c.close()
    r1 = _runde()
    assert _klassifisering(migrator, a, ha) is not None
    assert _klassifisering(migrator, b, hb) is None, \
        "tenant As regel nådde tenant B"
    r2 = _runde()
    assert r2["klassifisert"] == 0, f"runde to klassifiserte igjen: {r2}"
    assert r1["klassifisert"] >= 1


@pg
def test_kill_switch(migrator, miljo, monkeypatch):  # noqa: F811
    """PORT 7."""
    monkeypatch.setenv("DISPONIT_STILLEREGLER", "av")
    t = _tenant("av")
    c = _rt()
    try:
        kid, dek = _nokkel(c, t)
        _regler(c, t, [{"art": "domene", "monster": "stille.no"}])
        h = _henvendelse(c, t, kid, dek, "p@stille.no")
    finally:
        c.close()
    res = _runde()
    assert res.get("av") is True
    assert _klassifisering(migrator, t, h) is None


@pg
def test_domeneregel_tar_underdomener_men_ikke_naboer(  # noqa: F811
        migrator, miljo, monkeypatch):
    """205. MÅLT PÅ VERTEN: en regel for «microsoft.com» lot
    «emailnotifications.microsoft.com» stå uklassifisert. Suffikset
    krever punktumet foran: «notmicrosoft.com» er et annet domene.

    MUTASJONEN SOM DREPER DENNE: fjern `OR … LIKE '%.' || r.monster`
    (underdomenet står uklassifisert), eller slipp punktumet i LIKE-en
    (naboen blir klassifisert).
    """
    monkeypatch.delenv("DISPONIT_STILLEREGLER", raising=False)
    t = _tenant("sub")
    c = _rt()
    try:
        kid, dek = _nokkel(c, t)
        _regler(c, t, [{"art": "domene", "monster": "microsoft.com"}])
        under = _henvendelse(c, t, kid, dek,
                             "no-reply@emailnotifications.microsoft.com")
        nabo = _henvendelse(c, t, kid, dek, "noen@notmicrosoft.com")
    finally:
        c.close()
    _runde()
    assert _klassifisering(migrator, t, under) is not None, \
        "underdomenet ble ikke klassifisert"
    assert _klassifisering(migrator, t, nabo) is None, \
        "et nabodomene uten punktum foran ble klassifisert"


def test_rutene_er_registrert_med_scope():
    """Begge rutene finnes med scope — GET med et LESESCOPE
    (`test_pr008` håndhever den regelen for alle GET-er)."""
    from pathlib import Path
    kilde = (Path(__file__).resolve().parents[1] / "api" / "app.py"
             ).read_text(encoding="utf-8")
    assert '("GET",  "/v1/kundeservice/stilleregler"):  "decisions:read"' \
        in kilde
    assert '("POST", "/v1/kundeservice/stilleregler"):  "bestilling:opprett"' \
        in kilde
