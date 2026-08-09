"""PR-012 CP4c (sømmen): en verifisert menneskelig godkjenning matet gjennom
den ENE lovlige beslutningsskriveren, `sikker_beslutning_pg`.

Beviser mot EKTE Postgres at:
  * uten godkjenning er en over-grense-sak UNNTAK (regresjon — sømmen endrer
    ikke den ordinære veien),
  * en gyldig godkjenning gir TILLAT OG en revisjonslogg-rad (den ene
    skriveveien bærer beslutningen),
  * en godkjenning som ikke matcher hendelsen gir STOPP med
    `krever_sikkerhetsrouting` (porten skal rute V3-evidens).
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from policy_validator.engine import (
    STOPP, TILLAT, UNNTAK, EvaluationContext, MenneskeligGodkjenning,
    _policy_innholds_hash)
from .test_api import DSN, MIGRATOR_DSN

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

NAA = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
TEN = "t-mg-pg"
CTX = EvaluationContext(TEN, "agent", True, "api_token")

POL = {
    "schema_version": "0.2.0",
    "meta": {"policy_id": "test-mg-pg", "versjon": "1.0.0"},
    "tidssone": "Europe/Oslo",
    "handlinger": [{
        "id": "faktura.bokfor", "modus": "auto", "ved_brudd": "unntakskø",
        "tillatt_for": ["agent"],
        "grenser": {"belop_maks": "25000.00", "valuta": ["NOK"]},
    }],
    "menneskelig_overstyring": {
        "godkjennbare": [{"grunnkode": "belop_over_grense",
                          "handling": "faktura.bokfor",
                          "belop_maks": "50000.00", "valuta": "NOK"}],
        "krever_rolle": "okonomi"},
}


def _ev():
    return {"handling": "faktura.bokfor", "belop": "45000.00", "valuta": "NOK",
            "ressurs_id": "fak-1", "hi_integritet_hash": "a" * 64}


def _godkjenning():
    return MenneskeligGodkjenning(
        tenant=TEN, target_action="faktura.bokfor", ressurs_id="fak-1",
        belop=Decimal("45000.00"), valuta="NOK", hi_integritet_hash="a" * 64,
        bundet_grunnkode="belop_over_grense", unntak_id=1, runde=1,
        godkjennere=(("bruker-a", "okonomi", 3),),
        godkjennings_policy_hash=_policy_innholds_hash(POL),
        utloper=NAA + timedelta(hours=1))


@pytest.fixture()
def conn():
    from db.pg import koble, migrer
    m = koble(MIGRATOR_DSN)
    migrer(m)
    m.commit()
    m.close()
    c = koble(DSN)
    yield c
    c.close()


@pg
def test_menneskelig_godkjenning_gjennom_beslutningsskriver(conn):
    from db.pg import sett_tenant, sikker_beslutning_pg

    # Uten godkjenning: over grensen => UNNTAK (den ordinære veien uendret).
    d0 = sikker_beslutning_pg(POL, CTX, _ev(), conn, naa=NAA, nokler=None)
    assert d0.beslutning == UNNTAK
    conn.rollback()

    # Med gyldig godkjenning: TILLAT, og loggført av den ene skriveveien.
    d1 = sikker_beslutning_pg(POL, CTX, _ev(), conn, naa=NAA, nokler=None,
                              menneskelig_godkjenning=_godkjenning())
    assert d1.beslutning == TILLAT
    assert any(g.kode == "menneskelig_godkjenning_anvendt"
               for g in d1.begrunnelse)
    sett_tenant(conn, TEN)
    siste = conn.execute("SELECT beslutning FROM revisjonslogg WHERE tenant=%s"
                         " ORDER BY id DESC LIMIT 1", (TEN,)).fetchone()[0]
    conn.rollback()
    assert siste == TILLAT

    # Konvolutt som ikke matcher hendelsen (feil beløp) => STOPP + routing.
    g2 = replace(_godkjenning(), belop=Decimal("1.00"))
    d2 = sikker_beslutning_pg(POL, CTX, _ev(), conn, naa=NAA, nokler=None,
                              menneskelig_godkjenning=g2)
    assert d2.beslutning == STOPP
    assert d2.krever_sikkerhetsrouting is True
    conn.rollback()
