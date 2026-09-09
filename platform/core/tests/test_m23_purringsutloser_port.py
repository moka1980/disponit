"""Porten for ARC B, PR 3: purringsutløseren — registeret bestiller når
trinnet forfaller, policyen avgjør.

Målt mot ekte base, som planarbeideren (`DISPONIT_TEST_PLAN_DSN`) og med
API-ens egen `Tjeneste`, med bransjemalen SOM DEN ER (`purring.send`
tillatt for `agent`):

  1. Kandidatdøra: bare åpne fordringer med forfalt neste trinn OG en
     mottaker; uten mottaker, betalt, eller alt bestilt → ikke kandidat.
  2. Én runde: tillat → oppdrag, bokført rad med oppdrag_id, evidens i
     revisjonsloggen. Runde to: fordringen er ikke kandidat lenger, og
     ingen ny bestilling.
  3. Policy uten `purring.send`: ingenting bestilles, ingenting bokføres,
     og ingen beslutning brennes.
  4. Kill-switch `DISPONIT_PURRING_UTLOSER=av`: ingenting skjer.
  5. Forbigående feil og plattformtilstand (modulen ikke claimbar ennå)
     fra bestillingsveien: ingen bokføring, fordringen er kandidat igjen;
     en dom over fordringen (`fordring_ukjent`) bokføres som `feil:`.
  6. Rollen følger aktøren: et menneske med samme policy får brudd med
     `rolle_ikke_tillatt`; utløseren (agent) får tillat.
  7. Under policyens minimum (5 døgn, min 14): brudd → unntakskø, bokført
     med unntak_id, og IKKE prøvd igjen neste runde.
  8. Planrunden kaller utløseren (materialisereren er den ene inngangen).

MUTASJONER SOM DREPER DENNE: fjern `mottaker_hash IS NOT NULL` i døra
(1), fjern bokføringen i `utlos_en` (2/7 bestiller to ganger), fjern
policysjekken (3), eller sett rollen tilbake til `bestiller` (6).
"""
import os
import secrets
import uuid

import pytest
import yaml as _yaml

from .test_api import (DSN, MIGRATOR_DSN, POLICIES, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg)
from .test_bestilling_purring_port import (_bestill, _med_rt,
                                           _sikre_m23_claimbar)
from .test_m23_fordring import _betal, _fordring, _plan, _rt, _sv
from .test_m37 import _sett_kontekst
from .test_outbox_bestilling import _adminsesjon

PLAN_DSN = os.environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")


# ---------------------------------------------------------------------------
# Riggen
# ---------------------------------------------------------------------------

def _pa():
    from db.pg import koble
    return koble(PLAN_DSN)


def _bransjemal(m, *, uten_purring=False):
    """Bransjemalen SOM DEN ER for testtenanten — ingen rolle lagt til:
    utløseren er `agent`, og det er den rollen malen alt tillater."""
    from api import policyregister
    p = _yaml.safe_load(
        (POLICIES / "bransjemal-tjenestebedrift.yaml")
        .read_text(encoding="utf-8"))
    if uten_purring:
        p["handlinger"] = [h for h in p["handlinger"]
                           if h["id"] != "purring.send"]
    policyregister.registrer(m, TENANT, p, p["meta"]["status"])
    m.commit()
    _sikre_m23_claimbar(m)
    return p


def _mottaker(c, tenant, fid):
    _sett_kontekst(c, tenant)
    c.execute("SELECT m23_sett_mottaker(%s,%s,%s,%s,%s,%s,%s,%s)",
              (tenant, fid, "k****@fjordlys.no", b"\x01" * 24, b"\x02" * 12,
               "k1", secrets.token_hex(32), "u-test"))
    c.commit()


def _sveip():
    with _sv() as v:
        v.execute("SELECT * FROM m23_sveip_fordringer(500)").fetchone()
        v.commit()


def _moden_fordring(*, forfall_siden=20, med_mottaker=True, belop=250000):
    """Plan (3/14/28 døgn) + fordring + mottaker + sveip → `trinn_forfalt`."""
    c = _rt()
    try:
        _plan(c, TENANT)
        fid = _fordring(c, TENANT, belop=belop, forfall_siden=forfall_siden)
        if med_mottaker:
            _mottaker(c, TENANT, fid)
    finally:
        c.close()
    _sveip()
    return fid


def _kandidat_fider(pa):
    from plan.purring import kandidater
    return {(r[0], str(r[1]), r[2]) for r in kandidater(pa)}


def _bokfort(migrator, fid):
    _sett_kontekst(migrator, TENANT)
    rader = migrator.execute(
        "SELECT trinn, utfall, oppdrag_id, unntak_id"
        " FROM purringsbestilling WHERE tenant=%s AND fordring_id=%s"
        " ORDER BY trinn", (TENANT, fid)).fetchall()
    migrator.rollback()
    return rader


def _runde(app, pa, monkeypatch=None):
    from plan.purring import kjor_en_runde
    return kjor_en_runde(app.tjeneste, pa)


# ---------------------------------------------------------------------------
# Portene
# ---------------------------------------------------------------------------

@pg_plan
def test_kandidatdora_krever_apen_fordring_forfalt_trinn_og_mottaker(
        migrator, miljo):
    _bransjemal(migrator)
    med = _moden_fordring(forfall_siden=20)
    uten = _moden_fordring(forfall_siden=20, med_mottaker=False)
    betalt = _moden_fordring(forfall_siden=20, belop=1000)
    _med_rt(_betal, TENANT, betalt, 1000)
    fersk = _moden_fordring(forfall_siden=0)      # ikke forfalt trinn
    pa = _pa()
    try:
        k = _kandidat_fider(pa)
        # Neste trinn er 1 (påminnelse) — fordringen står på 0.
        assert (TENANT, str(med), 1) in k, k
        for fid in (uten, betalt, fersk):
            assert not any(t == TENANT and f == str(fid) for t, f, _ in k), \
                (fid, k)
        # Kryss-tenant-døra nekter en kaller MED tenantkontekst.
        import psycopg
        _sett_kontekst(pa, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pa.execute("SELECT * FROM m23_purringskandidater(10)")
        pa.rollback()
    finally:
        pa.close()


@pg_plan
def test_en_runde_bestiller_og_bokforer_og_runde_to_gjor_ingenting(
        migrator, miljo, app):
    _bransjemal(migrator)
    fid = _moden_fordring(forfall_siden=20)
    pa = _pa()
    try:
        r1 = _runde(app, pa)
        mine = [r for r in r1["resultater"] if r["fordring"] == str(fid)]
        assert len(mine) == 1, r1
        assert mine[0]["utfall"] == "tillat" and mine[0]["bokfort"], mine
        oid = mine[0]["oppdrag_id"]
        assert isinstance(oid, int)
        assert _bokfort(migrator, fid) == [(1, "tillat", oid, None)]
        # Oppdraget er purringens, av typen bestillingsveien lager.
        _sett_kontekst(migrator, TENANT)
        rad = migrator.execute(
            "SELECT oppdragstype, handling, eiermodul FROM oppdrag"
            " WHERE tenant=%s AND id=%s", (TENANT, oid)).fetchone()
        ev = migrator.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
            " AND kilde='m23_fordring' AND handling='purring_bestilt'"
            " AND aktor='agent:purring'", (TENANT,)).fetchone()[0]
        migrator.rollback()
        assert rad == ("purring.send", "purring.send", "m23_fordring"), rad
        assert ev >= 1
        # Runde to: fordringen er bokført for trinn 1 → ikke kandidat.
        assert (TENANT, str(fid), 1) not in _kandidat_fider(pa)
        r2 = _runde(app, pa)
        assert not [r for r in r2["resultater"]
                    if r["fordring"] == str(fid)], r2
        assert len(_bokfort(migrator, fid)) == 1
    finally:
        pa.close()


@pg_plan
def test_policy_uten_purring_bestiller_ingenting(migrator, miljo, app):
    _bransjemal(migrator, uten_purring=True)
    fid = _moden_fordring(forfall_siden=20)
    pa = _pa()
    try:
        assert (TENANT, str(fid), 1) in _kandidat_fider(pa)
        _sett_kontekst(migrator, TENANT)
        for_ = migrator.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
            " AND handling='purring.send'", (TENANT,)).fetchone()[0]
        migrator.rollback()
        r = _runde(app, pa)
        assert r["uten_policy"] >= 1, r
        assert not [x for x in r["resultater"]
                    if x["fordring"] == str(fid)], r
        assert _bokfort(migrator, fid) == []
        _sett_kontekst(migrator, TENANT)
        etter = migrator.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
            " AND handling='purring.send'", (TENANT,)).fetchone()[0]
        migrator.rollback()
        assert etter == for_, "en beslutning ble brent uten policy"
    finally:
        pa.close()
        _med_rt(_betal, TENANT, fid, 250000)      # rydd: ikke kandidat mer


@pg_plan
def test_kill_switch_stopper_utloseren(migrator, miljo, app, monkeypatch):
    _bransjemal(migrator)
    fid = _moden_fordring(forfall_siden=20)
    monkeypatch.setenv("DISPONIT_PURRING_UTLOSER", "av")
    pa = _pa()
    try:
        r = _runde(app, pa)
        assert r == {"av": True, "plukket": 0, "resultater": []}, r
        assert _bokfort(migrator, fid) == []
        monkeypatch.delenv("DISPONIT_PURRING_UTLOSER")
        assert (TENANT, str(fid), 1) in _kandidat_fider(pa)
    finally:
        pa.close()
        _med_rt(_betal, TENANT, fid, 250000)


@pg_plan
def test_forbigaende_feil_bokfores_ikke(migrator, miljo, app, monkeypatch):
    import api.bestilling as bestilling
    _bransjemal(migrator)
    fid = _moden_fordring(forfall_siden=20)
    monkeypatch.setattr(bestilling, "utfor_bestilling",
                        lambda *a, **kw: ("feil", "db_utilgjengelig"))
    pa = _pa()
    try:
        r = _runde(app, pa)
        mine = [x for x in r["resultater"] if x["fordring"] == str(fid)]
        assert mine and mine[0].get("forbigaende") == "db_utilgjengelig", r
        assert _bokfort(migrator, fid) == []
        assert (TENANT, str(fid), 1) in _kandidat_fider(pa)
    finally:
        pa.close()
        _med_rt(_betal, TENANT, fid, 250000)


@pg_plan
def test_modul_som_ikke_er_claimbar_enna_bokfores_ikke(migrator, miljo, app,
                                                        monkeypatch):
    """CodeRabbit på PR 3: `bestillingstype_utilgjengelig` er
    plattformtilstand (registreringsskriptet ikke kjørt), ikke en dom over
    fordringen. Bokført som `feil:` ville trinnet aldri blitt prøvd igjen
    etter at modulen ble registrert."""
    import api.bestilling as bestilling
    _bransjemal(migrator)
    fid = _moden_fordring(forfall_siden=20)
    monkeypatch.setattr(
        bestilling, "utfor_bestilling",
        lambda *a, **kw: ("feil", "bestillingstype_utilgjengelig"))
    pa = _pa()
    try:
        r = _runde(app, pa)
        mine = [x for x in r["resultater"] if x["fordring"] == str(fid)]
        assert mine and mine[0].get("forbigaende") \
            == "bestillingstype_utilgjengelig", r
        assert _bokfort(migrator, fid) == []
        assert (TENANT, str(fid), 1) in _kandidat_fider(pa)
        # …mens en DOM over fordringen bokføres og ikke prøves igjen.
        monkeypatch.setattr(bestilling, "utfor_bestilling",
                            lambda *a, **kw: ("feil", "fordring_ukjent"))
        r = _runde(app, pa)
        mine = [x for x in r["resultater"] if x["fordring"] == str(fid)]
        assert mine and mine[0]["utfall"] == "feil:fordring_ukjent", r
        assert (TENANT, str(fid), 1) not in _kandidat_fider(pa)
    finally:
        pa.close()


@pg_plan
def test_rollen_folger_aktoren(migrator, miljo, app, klient):
    """Samme policy, to aktører: mennesket (bestiller) avvises av
    `tillatt_for: [agent]`; utløseren ER agenten og får tillat."""
    _bransjemal(migrator)
    fid = _moden_fordring(forfall_siden=20)
    cookie, csrf = _adminsesjon()
    r = _bestill(klient, cookie, csrf, fid)
    assert r.status_code == 200, r.text
    # `ved_brudd: unntakskø` → brudd med sak; begrunnelsen er rollen.
    assert r.json()["beslutning"] == "brudd", r.text
    assert r.json()["begrunnelse"] == ["rolle_ikke_tillatt"], r.text
    assert not r.json().get("oppdrag_id"), r.text
    pa = _pa()
    try:
        res = _runde(app, pa)
        mine = [x for x in res["resultater"] if x["fordring"] == str(fid)]
        assert mine and mine[0]["utfall"] == "tillat", res
    finally:
        pa.close()


@pg_plan
def test_under_policyens_minimum_gir_brudd_en_gang(migrator, miljo, app):
    """Planen sier påminnelse ved 3 døgn, policyen krever 14: utløseren
    bestiller, motoren sier brudd (unntakskø), og trinnet prøves ikke
    igjen — saken er et menneskes nå."""
    _bransjemal(migrator)
    fid = _moden_fordring(forfall_siden=5)
    pa = _pa()
    try:
        r1 = _runde(app, pa)
        mine = [x for x in r1["resultater"] if x["fordring"] == str(fid)]
        assert mine and mine[0]["utfall"] == "brudd", r1
        assert mine[0]["unntak_id"], mine
        rader = _bokfort(migrator, fid)
        assert len(rader) == 1 and rader[0][1] == "brudd" \
            and rader[0][3] == mine[0]["unntak_id"], rader
        assert (TENANT, str(fid), 1) not in _kandidat_fider(pa)
        r2 = _runde(app, pa)
        assert not [x for x in r2["resultater"]
                    if x["fordring"] == str(fid)], r2
    finally:
        pa.close()


def test_planrunden_kaller_utloseren_og_bokforingsdora_er_designert():
    """Materialisereren er den ENE inngangen (samme timer, samme rolle);
    dørene står i eierskapsdesignet (ellers flates de ut på verten)."""
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    kode = (rot / "platform" / "core" / "plan" / "materialiser.py"
            ).read_text(encoding="utf-8")
    assert "from plan.purring import kjor_en_runde" in kode
    design = (rot / "deploy" / "staging" / "eierskap-reparasjon.sql"
              ).read_text(encoding="utf-8")
    for d in ("m23_purringskandidater(integer)",
              "m23_bokfor_purringsbestilling(text,uuid,integer,text,text,"
              "text,bigint,bigint,text,jsonb)",
              "m23_purringsbestillingene(text,uuid)"):
        assert d in design, d
    from plan.purring import idempotensnokkel
    n = idempotensnokkel(uuid.UUID(int=1), 2)
    assert 8 <= len(n) <= 200 and n.endswith(":2")
