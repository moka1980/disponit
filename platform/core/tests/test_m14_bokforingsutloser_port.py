"""Porten for ARC B bokføring, PR 2: bokføringsutløseren — registeret
bestiller når kontrollene er rene, policyens grenser velger handlingen.

Målt mot ekte base, som planarbeideren (`DISPONIT_TEST_PLAN_DSN`) og med
API-ens egen `Tjeneste`, med bransjemalen som den ER (`faktura.bokfor`
≤ 25 000 og `faktura.bokfor_stor` ≤ 100 000, begge for `agent`):

  1. Kandidatdøra: bare fakturaer i `mottatt`/`kontrollert` med rene
     dublett-, mva- og leverandørkontroller — og over tenantens egen
     beløpsgrense bare med en manuell kontroll; avvist, mva-avvik, ukjent
     leverandør, over grensen uten manuell, eller alt bestilt → ikke
     kandidat. Kryss-tenant-døra nekter tenantkontekst.
  2. Én runde: liten faktura → `faktura.bokfor`, tillat → oppdrag,
     bokført rad, evidens. Runde to: fakturaen er ikke kandidat lenger.
  3. Beløpet velger handlingen: 30 000 med manuell kontroll →
     `faktura.bokfor_stor`; 150 000 → `menneske_kreves` uten beslutning.
  4. Policy uten handlingene: ingenting bestilles, ingen beslutning.
  5. Kill-switch `DISPONIT_BOKFORING_UTLOSER=av`: ingenting skjer.
  6. Forbigående feil og plattformtilstand bokføres ikke; en dom
     (`faktura_ukjent`) bokføres som `feil:`.
  7. Planrunden kaller utløseren; dørene står i eierskapsdesignet.

MUTASJONER SOM DREPER DENNE: fjern mva-vilkåret i døra (1), fjern
bokføringen i `utlos_en` (2 bestiller to ganger), la `velg_handling`
alltid velge den lille (3).
"""
import os
import uuid

import pytest
import yaml as _yaml

from .test_api import (DSN, MIGRATOR_DSN, POLICIES, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_bestilling_bokfor_port import (_avvis, _bokforpolicy, _fakt,
                                          _manuell, _rigg,
                                          _sikre_m14_claimbar)
from .test_m37 import _sett_kontekst

PLAN_DSN = os.environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")


def _pa():
    from db.pg import koble
    return koble(PLAN_DSN)


def _policy_uten_bokforing(m):
    from api import policyregister
    p = _yaml.safe_load((POLICIES / "bransjemal-tjenestebedrift.yaml")
                        .read_text(encoding="utf-8"))
    p["handlinger"] = [h for h in p["handlinger"]
                       if h["id"] not in ("faktura.bokfor",
                                          "faktura.bokfor_stor")]
    policyregister.registrer(m, TENANT, p, p["meta"]["status"])
    m.commit()
    _sikre_m14_claimbar(m)


def _kandidater(pa):
    """Alle kandidater i basen (taket per tenant er 500): andre tester i
    samme sesjon kan ha etterlatt fakturaer i tenanten."""
    from plan.faktura import kandidater
    return {(r[0], str(r[1])) for r in kandidater(pa, 500)}


def _bokfort(migrator, fid):
    _sett_kontekst(migrator, TENANT)
    rader = migrator.execute(
        "SELECT handling, utfall, oppdrag_id, unntak_id"
        " FROM bokforingsbestilling WHERE tenant=%s AND faktura_id=%s",
        (TENANT, fid)).fetchall()
    migrator.rollback()
    return rader


def _runde(app, pa):
    from plan.faktura import kjor_en_runde
    return kjor_en_runde(app.tjeneste, pa)


def _mine(res, fid):
    return [r for r in res["resultater"] if r["faktura"] == str(fid)]


def _antall_beslutninger(migrator):
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND handling LIKE 'faktura.bokfor%%'", (TENANT,)).fetchone()[0]
    migrator.rollback()
    return n


def test_velg_handling_er_policyens_grenser():
    from plan.faktura import velg_handling
    g = {"faktura.bokfor": (2500000, ("NOK",)),
         "faktura.bokfor_stor": (10000000, ("NOK",))}
    assert velg_handling(g, 1250000, "NOK") == "faktura.bokfor"
    assert velg_handling(g, 2500000, "NOK") == "faktura.bokfor"
    assert velg_handling(g, 3000000, "NOK") == "faktura.bokfor_stor"
    assert velg_handling(g, 15000000, "NOK") is None
    assert velg_handling(g, 1000, "EUR") is None
    assert velg_handling({"faktura.bokfor": (None, ())}, 10 ** 9, "NOK") == \
        "faktura.bokfor"


def test_en_uleselig_grense_holder_handlingen_utenfor(monkeypatch):
    """En `belop_maks` som ikke kan leses er ikke «ingen grense»: den
    handlingen finnes ikke for runden før policyen er rettet."""
    from plan import faktura

    class _Conn:
        def execute(self, *_a, **_k):
            return self

        def fetchall(self):
            return [({"handlinger": [
                {"id": "faktura.bokfor",
                 "grenser": {"belop_maks": "tjuefem tusen", "valuta": ["NOK"]}},
                {"id": "faktura.bokfor_stor",
                 "grenser": {"belop_maks": "100000.00", "valuta": ["NOK"]}}]},)]

        def rollback(self):
            pass

    monkeypatch.setattr("db.pg.sett_kontekst", lambda *a, **k: None)
    g = faktura.policygrenser(_Conn(), "t")
    assert g == {"faktura.bokfor_stor": (10000000, ("NOK",))}, g
    assert faktura.velg_handling(g, 1000, "NOK") == "faktura.bokfor_stor"


@pg_plan
def test_kandidatdora_krever_rene_kontroller_og_manuell_over_grensen(
        migrator, miljo, klient, token):
    _bokforpolicy(migrator); _rigg(migrator)
    # Ulike beløp: to fakturaer fra samme leverandør med samme brutto
    # innenfor dublettvinduet ER en nær-dublett for registeret (106), og
    # den andre ville fått dublettkontrollen `avvik` — riktig, men ikke
    # det denne porten måler.
    ren = _fakt(migrator, netto=1000000, mva=250000)
    mva_avvik = _fakt(migrator, netto=1100000, mva=220000)
    ukjent = _fakt(migrator, ref="Ukjent Kabel AS", netto=1200000,
                   mva=300000)
    stor_uten = _fakt(migrator, netto=2400000, mva=600000)
    stor_med = _fakt(migrator, netto=2800000, mva=700000)
    _manuell(migrator, stor_med)
    avvist = _fakt(migrator, netto=1300000, mva=325000)
    _avvis(migrator, avvist)
    pa = _pa()
    try:
        k = _kandidater(pa)
        assert (TENANT, str(ren)) in k and (TENANT, str(stor_med)) in k, k
        for fid in (mva_avvik, ukjent, stor_uten, avvist):
            assert (TENANT, str(fid)) not in k, (fid, k)
        import psycopg
        _sett_kontekst(pa, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pa.execute("SELECT * FROM m14_bokforingskandidater(10)")
        pa.rollback()
    finally:
        pa.close()


@pg_plan
def test_en_runde_bestiller_og_bokforer_og_runde_to_gjor_ingenting(
        migrator, miljo, app, klient, token):
    _bokforpolicy(migrator); _rigg(migrator)
    fid = _fakt(migrator, netto=1000000, mva=250000)
    pa = _pa()
    try:
        r1 = _runde(app, pa)
        mine = _mine(r1, fid)
        assert len(mine) == 1, r1
        assert mine[0]["handling"] == "faktura.bokfor"
        assert mine[0]["utfall"] == "tillat" and mine[0]["bokfort"], mine
        oid = mine[0]["oppdrag_id"]
        assert _bokfort(migrator, fid) == [("faktura.bokfor", "tillat", oid,
                                            None)]
        _sett_kontekst(migrator, TENANT)
        rad = migrator.execute(
            "SELECT oppdragstype, handling, eiermodul FROM oppdrag"
            " WHERE tenant=%s AND id=%s", (TENANT, oid)).fetchone()
        ev = migrator.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
            " AND kilde='m14_faktura'"
            " AND handling='bokforing.bestilt' AND aktor='agent:faktura'",
            (TENANT,)).fetchone()[0]
        migrator.rollback()
        assert rad == ("faktura.bokfor", "faktura.bokfor",
                       "m14_fakturakontroll"), rad
        assert ev >= 1
        assert (TENANT, str(fid)) not in _kandidater(pa)
        r2 = _runde(app, pa)
        assert not _mine(r2, fid), r2
        assert len(_bokfort(migrator, fid)) == 1
    finally:
        pa.close()


@pg_plan
def test_belopet_velger_handlingen_og_over_taket_krever_et_menneske(
        migrator, miljo, app, klient, token):
    _bokforpolicy(migrator); _rigg(migrator)
    stor = _fakt(migrator, netto=2400000, mva=600000)       # 30 000
    _manuell(migrator, stor)
    over = _fakt(migrator, netto=12000000, mva=3000000)     # 150 000
    _manuell(migrator, over)
    pa = _pa()
    try:
        for_ = _antall_beslutninger(migrator)
        r = _runde(app, pa)
        ms = _mine(r, stor)
        assert ms and ms[0]["handling"] == "faktura.bokfor_stor" \
            and ms[0]["utfall"] == "tillat", r
        mo = _mine(r, over)
        assert mo and mo[0]["utfall"] == "menneske_kreves" and mo[0]["bokfort"]
        assert _bokfort(migrator, over) == [("ingen", "menneske_kreves",
                                             None, None)]
        assert _antall_beslutninger(migrator) == for_ + 1
        assert (TENANT, str(over)) not in _kandidater(pa)
    finally:
        pa.close()


@pg_plan
def test_policy_uten_handlingene_bestiller_ingenting(migrator, miljo, app,
                                                     klient, token):
    _policy_uten_bokforing(migrator); _rigg(migrator)
    fid = _fakt(migrator, netto=1000000, mva=250000)
    pa = _pa()
    try:
        assert (TENANT, str(fid)) in _kandidater(pa)
        for_ = _antall_beslutninger(migrator)
        r = _runde(app, pa)
        assert r["uten_policy"] >= 1 and not _mine(r, fid), r
        assert _bokfort(migrator, fid) == []
        assert _antall_beslutninger(migrator) == for_
    finally:
        pa.close()


@pg_plan
def test_kill_switch_stopper_utloseren(migrator, miljo, app, klient, token,
                                       monkeypatch):
    _bokforpolicy(migrator); _rigg(migrator)
    fid = _fakt(migrator, netto=1000000, mva=250000)
    monkeypatch.setenv("DISPONIT_BOKFORING_UTLOSER", "av")
    pa = _pa()
    try:
        r = _runde(app, pa)
        assert r == {"av": True, "plukket": 0, "resultater": []}, r
        assert _bokfort(migrator, fid) == []
        monkeypatch.delenv("DISPONIT_BOKFORING_UTLOSER")
        assert (TENANT, str(fid)) in _kandidater(pa)
    finally:
        pa.close()


@pg_plan
def test_forbigaende_bokfores_ikke_men_en_dom_gjor(migrator, miljo, app,
                                                   klient, token, monkeypatch):
    import api.bestilling as bestilling
    _bokforpolicy(migrator); _rigg(migrator)
    fid = _fakt(migrator, netto=1000000, mva=250000)
    pa = _pa()
    try:
        for kode in ("db_utilgjengelig", "bestillingstype_utilgjengelig",
                     "policy_ukjent"):
            monkeypatch.setattr(bestilling, "utfor_bestilling",
                                lambda *a, _k=kode, **kw: ("feil", _k))
            r = _runde(app, pa)
            mine = _mine(r, fid)
            assert mine and mine[0].get("forbigaende") == kode, (kode, r)
            assert _bokfort(migrator, fid) == []
            assert (TENANT, str(fid)) in _kandidater(pa)
        # `faktura_ikke_klar_for_bokforing` er drift-rutet (409) og dermed
        # forbigående for planen; dommen som bokføres er `faktura_ukjent`.
        monkeypatch.setattr(bestilling, "utfor_bestilling",
                            lambda *a, **kw: ("feil", "faktura_ukjent"))
        r = _runde(app, pa)
        mine = _mine(r, fid)
        assert mine and mine[0]["utfall"] == "feil:faktura_ukjent", r
        assert (TENANT, str(fid)) not in _kandidater(pa)
    finally:
        pa.close()


def test_planrunden_kaller_utloseren_og_dorene_er_designert():
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    kode = (rot / "platform" / "core" / "plan" / "materialiser.py"
            ).read_text(encoding="utf-8")
    assert "from plan.faktura import kjor_en_runde" in kode
    design = (rot / "deploy" / "staging" / "eierskap-reparasjon.sql"
              ).read_text(encoding="utf-8")
    for d in ("m14_bokforingskandidater(integer)",
              "m14_bokfor_bokforingsbestilling(text,uuid,text,text,text,"
              "bigint,bigint,text,jsonb)",
              "m14_bokforingsbestillingen(text,uuid)"):
        assert d in design, d
    from plan.faktura import idempotensnokkel
    n = idempotensnokkel(uuid.UUID(int=1))
    assert 8 <= len(n) <= 200 and n.startswith("bokforing:")
