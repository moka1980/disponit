"""Porten for ARC B kampanje, PR 3: kampanjeutløseren — registeret
bestiller på sendedagen, policyen avgjør.

Målt mot ekte base, som planarbeideren (`DISPONIT_TEST_PLAN_DSN`) og med
API-ens egen `Tjeneste`, med bransjemalen + utvidelsen `kampanje-send`
(`kampanje.send` tillatt for `agent`):

  1. Kandidatdøra: bare planlagte par der kampanjen er registrert med
     innhold, datoen er nådd, mottakeren er aktiv med adresse OG har en
     samtykkehistorikk; uten adresse, uten innhold, utenfor planen, i
     framtida, aldri samtykket, eller alt bestilt → ikke kandidat.
     Kryss-tenant-døra nekter en kaller MED tenantkontekst.
  2. Én runde: tillat → oppdrag, bokført rad med oppdrag_id, evidens i
     revisjonsloggen (uten adresse). Runde to: paret er ikke kandidat
     lenger, og ingen ny bestilling.
  3. Policy uten `kampanje.send`: ingenting bestilles, ingenting
     bokføres, ingen beslutning brennes.
  4. Kill-switch `DISPONIT_KAMPANJE_UTLOSER=av`: ingenting skjer.
  5. Forbigående feil og plattformtilstand fra bestillingsveien: ingen
     bokføring, paret er kandidat igjen; en dom (`kampanje_ukjent`)
     bokføres som `feil:` og prøves ikke igjen.
  6. Samtykket trukket etter planleggingen: brudd → unntakskø, bokført
     med unntak_id, IKKE prøvd igjen — en sak, aldri en stille levering.
  7. Planrunden kaller utløseren; dørene står i eierskapsdesignet.

MUTASJONER SOM DREPER DENNE: fjern `kontakt_kryptert IS NOT NULL` i døra
(1), fjern bokføringen i `utlos_en` (2/6 bestiller to ganger), fjern
policysjekken (3).
"""
import os
import uuid
from datetime import date, timedelta

import pytest
import yaml as _yaml

from .test_api import (DSN, MIGRATOR_DSN, POLICIES, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg)
from .test_bestilling_kampanje_port import (I_DAG, _kampanjepolicy, _klar,
                                            _sikre_m44_claimbar)
from .test_m37 import _sett_kontekst
from .test_m44_kampanje import _rt, _samtykke

PLAN_DSN = os.environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")


# ---------------------------------------------------------------------------
# Riggen
# ---------------------------------------------------------------------------

def _pa():
    from db.pg import koble
    return koble(PLAN_DSN)


def _policy_uten_kampanje(m):
    """Bransjemalen SOM DEN ER (ingen kampanje-utvidelse), modulen
    claimbar — så bare policyen mangler."""
    from api import policyregister
    p = _yaml.safe_load((POLICIES / "bransjemal-tjenestebedrift.yaml")
                        .read_text(encoding="utf-8"))
    assert not any(h["id"] == "kampanje.send" for h in p["handlinger"])
    policyregister.registrer(m, TENANT, p, p["meta"]["status"])
    m.commit()
    _sikre_m44_claimbar(m)


def _kandidater(pa):
    from plan.kampanje import kandidater
    return {(r[0], str(r[1]), str(r[2])) for r in kandidater(pa)}


def _bokfort(migrator, kid):
    _sett_kontekst(migrator, TENANT)
    rader = migrator.execute(
        "SELECT mottaker_id::text, utfall, oppdrag_id, unntak_id"
        " FROM kampanjebestilling WHERE tenant=%s AND kampanje_id=%s"
        " ORDER BY bestilt_ts", (TENANT, kid)).fetchall()
    migrator.rollback()
    return rader


def _runde(app, pa):
    from plan.kampanje import kjor_en_runde
    return kjor_en_runde(app.tjeneste, pa)


def _mine(res, kid):
    return [r for r in res["resultater"] if r["kampanje"] == str(kid)]


def _antall_beslutninger(migrator):
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND handling='kampanje.send'", (TENANT,)).fetchone()[0]
    migrator.rollback()
    return n


# ---------------------------------------------------------------------------
# Portene
# ---------------------------------------------------------------------------

@pg_plan
def test_kandidatdora_krever_innhold_adresse_plan_dato_og_samtykkehistorikk(
        migrator, miljo):
    _kampanjepolicy(migrator)
    klar, klar_mid = _klar()
    uten_kontakt, _ = _klar(med_kontakt=False)
    uten_innhold, _ = _klar(med_innhold=False)
    utenfor_plan, _ = _klar(i_plan=False)
    # Plandøra nekter en mottaker uten samtykke (#423) — raden legges
    # UTENOM døra, slik rader fra før 142 ser ut. Kandidatdøra skal
    # likevel ikke se den: ingenting å attestere.
    aldri_samtykket, as_mid = _klar(samtykke=None, i_plan=False)
    from .test_m44_kampanje import _plan_direkte
    _plan_direkte(migrator, TENANT, aldri_samtykket, as_mid)
    i_morgen, _ = _klar(dato=(date.today() + timedelta(days=1)).isoformat())
    # Trukket ETTER planleggingen (plandøra nekter et trukket samtykke
    # ved planlegging): fortsatt kandidat — det er policyens sak, ikke
    # døras (port 6).
    trukket, tr_mid = _klar()
    c = _rt()
    try:
        _samtykke(c, TENANT, tr_mid, "trukket", I_DAG)
    finally:
        c.close()
    pa = _pa()
    try:
        k = _kandidater(pa)
        assert (TENANT, str(klar), str(klar_mid)) in k, k
        assert any(t == TENANT and c == str(trukket) for t, c, _ in k), k
        for kid in (uten_kontakt, uten_innhold, utenfor_plan,
                    aldri_samtykket, i_morgen):
            assert not any(t == TENANT and c == str(kid) for t, c, _ in k), \
                (kid, k)
        import psycopg
        _sett_kontekst(pa, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pa.execute("SELECT * FROM m44_kampanjekandidater(10)")
        pa.rollback()
    finally:
        pa.close()


@pg_plan
def test_en_runde_bestiller_og_bokforer_og_runde_to_gjor_ingenting(
        migrator, miljo, app):
    _kampanjepolicy(migrator)
    kid, mid = _klar()
    pa = _pa()
    try:
        r1 = _runde(app, pa)
        mine = _mine(r1, kid)
        assert len(mine) == 1, r1
        assert mine[0]["utfall"] == "tillat" and mine[0]["bokfort"], mine
        oid = mine[0]["oppdrag_id"]
        assert isinstance(oid, int)
        assert _bokfort(migrator, kid) == [(str(mid), "tillat", oid, None)]
        _sett_kontekst(migrator, TENANT)
        rad = migrator.execute(
            "SELECT oppdragstype, handling, eiermodul FROM oppdrag"
            " WHERE tenant=%s AND id=%s", (TENANT, oid)).fetchone()
        ev = migrator.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
            " AND kilde='m44_kampanje' AND handling='kampanje_bestilt'"
            " AND aktor='agent:kampanje'", (TENANT,)).fetchone()[0]
        lekk = migrator.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
            " AND begrunnelse::text ILIKE '%%example.com%%'",
            (TENANT,)).fetchone()[0]
        migrator.rollback()
        assert rad == ("kampanje.send", "kampanje.send", "m44_kampanje"), rad
        assert ev >= 1 and lekk == 0
        assert (TENANT, str(kid), str(mid)) not in _kandidater(pa)
        r2 = _runde(app, pa)
        assert not _mine(r2, kid), r2
        assert len(_bokfort(migrator, kid)) == 1
    finally:
        pa.close()


@pg_plan
def test_policy_uten_kampanje_bestiller_ingenting(migrator, miljo, app):
    _policy_uten_kampanje(migrator)
    kid, mid = _klar()
    pa = _pa()
    try:
        assert (TENANT, str(kid), str(mid)) in _kandidater(pa)
        for_ = _antall_beslutninger(migrator)
        r = _runde(app, pa)
        assert r["uten_policy"] >= 1, r
        assert not _mine(r, kid), r
        assert _bokfort(migrator, kid) == []
        assert _antall_beslutninger(migrator) == for_, \
            "en beslutning ble brent uten policy"
    finally:
        pa.close()


@pg_plan
def test_kill_switch_stopper_utloseren(migrator, miljo, app, monkeypatch):
    _kampanjepolicy(migrator)
    kid, mid = _klar()
    monkeypatch.setenv("DISPONIT_KAMPANJE_UTLOSER", "av")
    pa = _pa()
    try:
        r = _runde(app, pa)
        assert r == {"av": True, "plukket": 0, "resultater": []}, r
        assert _bokfort(migrator, kid) == []
        monkeypatch.delenv("DISPONIT_KAMPANJE_UTLOSER")
        assert (TENANT, str(kid), str(mid)) in _kandidater(pa)
    finally:
        pa.close()


@pg_plan
def test_forbigaende_og_plattformtilstand_bokfores_ikke_men_en_dom_gjor(
        migrator, miljo, app, monkeypatch):
    import api.bestilling as bestilling
    _kampanjepolicy(migrator)
    kid, mid = _klar()
    pa = _pa()
    try:
        for kode in ("db_utilgjengelig", "bestillingstype_utilgjengelig",
                     "policy_ukjent"):
            monkeypatch.setattr(bestilling, "utfor_bestilling",
                                lambda *a, _k=kode, **kw: ("feil", _k))
            r = _runde(app, pa)
            mine = _mine(r, kid)
            assert mine and mine[0].get("forbigaende") == kode, (kode, r)
            assert _bokfort(migrator, kid) == []
            assert (TENANT, str(kid), str(mid)) in _kandidater(pa)
        monkeypatch.setattr(bestilling, "utfor_bestilling",
                            lambda *a, **kw: ("feil", "kampanje_ukjent"))
        r = _runde(app, pa)
        mine = _mine(r, kid)
        assert mine and mine[0]["utfall"] == "feil:kampanje_ukjent", r
        assert (TENANT, str(kid), str(mid)) not in _kandidater(pa)
    finally:
        pa.close()


@pg_plan
def test_trukket_samtykke_gir_brudd_en_gang(migrator, miljo, app):
    _kampanjepolicy(migrator)
    kid, mid = _klar()
    c = _rt()
    try:
        _samtykke(c, TENANT, mid, "trukket", I_DAG)
    finally:
        c.close()
    pa = _pa()
    try:
        r1 = _runde(app, pa)
        mine = _mine(r1, kid)
        assert mine and mine[0]["utfall"] == "brudd", r1
        assert mine[0]["unntak_id"], mine
        rader = _bokfort(migrator, kid)
        assert len(rader) == 1 and rader[0][1] == "brudd" \
            and rader[0][3] == mine[0]["unntak_id"], rader
        assert (TENANT, str(kid), str(mid)) not in _kandidater(pa)
        r2 = _runde(app, pa)
        assert not _mine(r2, kid), r2
    finally:
        pa.close()


def test_planrunden_kaller_utloseren_og_dorene_er_designert():
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    kode = (rot / "platform" / "core" / "plan" / "materialiser.py"
            ).read_text(encoding="utf-8")
    assert "from plan.kampanje import kjor_en_runde" in kode
    design = (rot / "deploy" / "staging" / "eierskap-reparasjon.sql"
              ).read_text(encoding="utf-8")
    for d in ("m44_kampanjekandidater(integer)",
              "m44_bokfor_kampanjebestilling(text,uuid,uuid,text,text,"
              "bigint,bigint,text,jsonb)",
              "m44_kampanjebestillingene(text,uuid)"):
        assert d in design, d
    from plan.kampanje import idempotensnokkel
    n = idempotensnokkel(uuid.UUID(int=1), uuid.UUID(int=2))
    assert 8 <= len(n) <= 200 and n.startswith("kampanje:")
