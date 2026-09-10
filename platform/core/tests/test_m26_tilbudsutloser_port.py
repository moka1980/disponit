"""Porten for ARC B tilbud, PR 3: tilbudsutløseren — registeret bestiller
når et menneske har godkjent, policyen avgjør.

Målt mot ekte base, som planarbeideren (`DISPONIT_TEST_PLAN_DSN`) og med
API-ens egen `Tjeneste`, med bransjemalen + utvidelsen `tilbud-generer`:

  1. Kandidatdøra: bare GODKJENTE, gyldige tilbud med linjer; utkast,
     forkastet, utløpt eller alt bestilt → ikke kandidat. Kryss-tenant-
     døra nekter tenantkontekst.
  2. Én runde: tillat → oppdrag, bokført rad med oppdrag_id, evidens.
     Runde to: tilbudet er ikke kandidat lenger.
  3. Policy uten handlingen: ingenting bestilles, ingen beslutning.
  4. Kill-switch `DISPONIT_TILBUD_UTLOSER=av`: ingenting skjer.
  5. Forbigående feil bokføres ikke; en dom (`tilbud_ukjent`) bokføres
     som `feil:`.
  6. Et godkjent tilbud under rabattgrensen: brudd → unntakskø, bokført
     med unntak_id, IKKE prøvd igjen — et rettet tilbud er et nytt.
  7. Planrunden kaller utløseren; dørene står i eierskapsdesignet.

MUTASJONER SOM DREPER DENNE: fjern `status = 'godkjent'` i døra (1),
fjern bokføringen i `utlos_en` (2), fjern policysjekken (3).
"""
import os
import uuid

import pytest
import yaml as _yaml

from .test_api import (DSN, MIGRATOR_DSN, POLICIES, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_bestilling_tilbud_port import (_btok, _klar, _sikre_m26_claimbar,
                                          _tilbudpolicy)
from .test_m17_avsender_port import _post
from .test_m26_tilbud_port import _rigg
from .test_m37 import _sett_kontekst

PLAN_DSN = os.environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")


def _pa():
    from db.pg import koble
    return koble(PLAN_DSN)


def _policy_uten_tilbud(m):
    from api import policyregister
    p = _yaml.safe_load((POLICIES / "bransjemal-tjenestebedrift.yaml")
                        .read_text(encoding="utf-8"))
    p["handlinger"] = [h for h in p["handlinger"] if h["id"] != "tilbud.generer"]
    policyregister.registrer(m, TENANT, p, p["meta"]["status"])
    m.commit()
    _sikre_m26_claimbar(m)


def _kandidater(pa):
    from plan.tilbud import kandidater
    return {(r[0], str(r[1])) for r in kandidater(pa, 500)}


def _bokfort(migrator, tid):
    _sett_kontekst(migrator, TENANT)
    rader = migrator.execute(
        "SELECT utfall, oppdrag_id, unntak_id FROM tilbudsbestilling"
        " WHERE tenant=%s AND tilbud_id=%s", (TENANT, tid)).fetchall()
    migrator.rollback()
    return rader


def _runde(app, pa):
    from plan.tilbud import kjor_en_runde
    return kjor_en_runde(app.tjeneste, pa)


def _mine(res, tid):
    return [r for r in res["resultater"] if r["tilbud"] == str(tid)]


def _antall_beslutninger(migrator):
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND handling='tilbud.generer'", (TENANT,)).fetchone()[0]
    migrator.rollback()
    return n


@pg_plan
def test_kandidatdora_krever_godkjent_gyldig_med_linjer(migrator, miljo,
                                                          klient, token):
    _tilbudpolicy(migrator)
    kabel, _, _ = _rigg()
    tok = _btok(token)
    klar, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 1}])
    utkast, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 2}],
                      godkjent=False)
    forkastet, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 3}],
                         godkjent=False)
    _post(klient, tok, f"/v1/tilbud/{forkastet}/dom", {"status": "forkastet"})
    utlopt, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 4}],
                      tilbudsdato="2026-01-10", gyldig_til="2026-02-10")
    pa = _pa()
    try:
        k = _kandidater(pa)
        assert (TENANT, klar) in k, k
        for tid in (utkast, forkastet, utlopt):
            assert (TENANT, tid) not in k, (tid, k)
        import psycopg
        _sett_kontekst(pa, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pa.execute("SELECT * FROM m26_tilbudskandidater(10)")
        pa.rollback()
    finally:
        pa.close()


@pg_plan
def test_en_runde_bestiller_og_bokforer_og_runde_to_gjor_ingenting(
        migrator, miljo, app, klient, token):
    _tilbudpolicy(migrator)
    kabel, _, _ = _rigg()
    tid, _ = _klar(klient, _btok(token), [{"produkt_id": str(kabel), "antall": 5}])
    pa = _pa()
    try:
        r1 = _runde(app, pa)
        mine = _mine(r1, tid)
        assert len(mine) == 1, r1
        assert mine[0]["utfall"] == "tillat" and mine[0]["bokfort"], mine
        oid = mine[0]["oppdrag_id"]
        assert _bokfort(migrator, tid) == [("tillat", oid, None)]
        _sett_kontekst(migrator, TENANT)
        rad = migrator.execute(
            "SELECT oppdragstype, handling, eiermodul FROM oppdrag"
            " WHERE tenant=%s AND id=%s", (TENANT, oid)).fetchone()
        ev = migrator.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
            " AND kilde='m26_prisbok' AND handling='tilbud.bestilt'"
            " AND aktor='agent:tilbud'", (TENANT,)).fetchone()[0]
        migrator.rollback()
        assert rad == ("tilbud.generer", "tilbud.generer", "m26_prisbok"), rad
        assert ev >= 1
        assert (TENANT, tid) not in _kandidater(pa)
        r2 = _runde(app, pa)
        assert not _mine(r2, tid), r2
        assert len(_bokfort(migrator, tid)) == 1
    finally:
        pa.close()


@pg_plan
def test_policy_uten_handlingen_bestiller_ingenting(migrator, miljo, app,
                                                    klient, token):
    _policy_uten_tilbud(migrator)
    kabel, _, _ = _rigg()
    tid, _ = _klar(klient, _btok(token), [{"produkt_id": str(kabel), "antall": 1}])
    pa = _pa()
    try:
        assert (TENANT, tid) in _kandidater(pa)
        for_ = _antall_beslutninger(migrator)
        r = _runde(app, pa)
        assert r["uten_policy"] >= 1 and not _mine(r, tid), r
        assert _bokfort(migrator, tid) == []
        assert _antall_beslutninger(migrator) == for_
    finally:
        pa.close()


@pg_plan
def test_kill_switch_stopper_utloseren(migrator, miljo, app, klient, token,
                                       monkeypatch):
    _tilbudpolicy(migrator)
    kabel, _, _ = _rigg()
    tid, _ = _klar(klient, _btok(token), [{"produkt_id": str(kabel), "antall": 1}])
    monkeypatch.setenv("DISPONIT_TILBUD_UTLOSER", "av")
    pa = _pa()
    try:
        r = _runde(app, pa)
        assert r == {"av": True, "plukket": 0, "resultater": []}, r
        assert _bokfort(migrator, tid) == []
        monkeypatch.delenv("DISPONIT_TILBUD_UTLOSER")
        assert (TENANT, tid) in _kandidater(pa)
    finally:
        pa.close()


@pg_plan
def test_forbigaende_bokfores_ikke_men_en_dom_gjor(migrator, miljo, app,
                                                   klient, token, monkeypatch):
    import api.bestilling as bestilling
    _tilbudpolicy(migrator)
    kabel, _, _ = _rigg()
    tid, _ = _klar(klient, _btok(token), [{"produkt_id": str(kabel), "antall": 1}])
    pa = _pa()
    try:
        for kode in ("db_utilgjengelig", "bestillingstype_utilgjengelig",
                     "policy_ukjent"):
            monkeypatch.setattr(bestilling, "utfor_bestilling",
                                lambda *a, _k=kode, **kw: ("feil", _k))
            r = _runde(app, pa)
            mine = _mine(r, tid)
            assert mine and mine[0].get("forbigaende") == kode, (kode, r)
            assert _bokfort(migrator, tid) == []
            assert (TENANT, tid) in _kandidater(pa)
        monkeypatch.setattr(bestilling, "utfor_bestilling",
                            lambda *a, **kw: ("feil", "tilbud_ukjent"))
        r = _runde(app, pa)
        mine = _mine(r, tid)
        assert mine and mine[0]["utfall"] == "feil:tilbud_ukjent", r
        assert (TENANT, tid) not in _kandidater(pa)
    finally:
        pa.close()


@pg_plan
def test_rabatt_under_grensen_gir_brudd_en_gang(migrator, miljo, app, klient,
                                                token):
    _tilbudpolicy(migrator)
    kabel, _, _ = _rigg(rabatt=100)
    tid, _ = _klar(klient, _btok(token), [{"produkt_id": str(kabel), "antall": 10,
                                            "enhetspris_ore": 1100}])
    pa = _pa()
    try:
        r1 = _runde(app, pa)
        mine = _mine(r1, tid)
        assert mine and mine[0]["utfall"] == "brudd" and mine[0]["unntak_id"], r1
        rader = _bokfort(migrator, tid)
        assert len(rader) == 1 and rader[0][0] == "brudd"
        assert (TENANT, tid) not in _kandidater(pa)
        assert not _mine(_runde(app, pa), tid)
    finally:
        pa.close()


def test_planrunden_kaller_utloseren_og_dorene_er_designert():
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    kode = (rot / "platform" / "core" / "plan" / "materialiser.py"
            ).read_text(encoding="utf-8")
    assert "from plan.tilbud import kjor_en_runde" in kode
    design = (rot / "deploy" / "staging" / "eierskap-reparasjon.sql"
              ).read_text(encoding="utf-8")
    for d in ("m26_tilbudskandidater(integer)",
              "m26_bokfor_tilbudsbestilling(text,uuid,text,text,bigint,"
              "bigint,text,jsonb)",
              "m26_tilbudsbestillingen(text,uuid)"):
        assert d in design, d
    from plan.tilbud import idempotensnokkel
    n = idempotensnokkel(uuid.UUID(int=1))
    assert 8 <= len(n) <= 200 and n.startswith("tilbud:")
