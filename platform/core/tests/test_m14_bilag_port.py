"""Ende-til-ende-porten for ARC B bokføring, PR 3: bilaget kvitteres.

Hele kjeden mot ekte base og ekte app: faktura med rene kontroller (106)
→ utløseren bestiller som agent (166), kontrollradene attesteres (165)
→ oppdrag → modulen onboardes og claimer over API-et → claim-svaret
bærer bilaget fakturaen skal bli i `utforelse` (165), lest av registeret
i API-ets tillit → modulen bygger bilaget → signert kvittering →
oppdraget er `utfort`, og kvitteringen er evidensen (ressursbundet,
bilagets tall).

  1. Den gyldne veien, over — og claim nummer to er tomt.
  2. Fakturaen avvist MELLOM bestilling og claim: claim-svaret bærer
     hindringen, oppdraget lukkes `feilet`.

MUTASJONER SOM DREPER DENNE: dropp bokføringsgrenen i claim-veien (1 →
feilet utforelse_mangler), la `utforelse_for_bokforing` hoppe over
avvist-sjekken (2).
"""
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_bestilling_bokfor_port import _avvis, _bokforpolicy, _fakt, _rigg
from .test_m14_bokforingsutloser_port import _pa, _runde
from .test_m37 import _sett_kontekst, _signer_kvittering
from .test_modul_onboarding_http import _onboard_token

PLAN_DSN = __import__("os").environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")
MODUL = "m14_fakturakontroll"


def _signer(kropp):
    return _signer_kvittering(kropp, verifikator="v_regnskap")


def _release(migrator):
    rel = migrator.execute(
        "SELECT release_id FROM moduldeployment"
        f" WHERE modul_id='{MODUL}' AND livslop='claiming' LIMIT 1"
    ).fetchone()[0]
    migrator.rollback()
    return rel


def _oppdrag(migrator, oid):
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT status, kvittering, oppdragstype, eiermodul FROM oppdrag"
        " WHERE tenant=%s AND id=%s", (TENANT, oid)).fetchone()
    migrator.rollback()
    return rad


def _bestilt_av_utloseren(app, fid):
    pa = _pa()
    try:
        res = _runde(app, pa)
    finally:
        pa.close()
    mine = [x for x in res["resultater"] if x["faktura"] == str(fid)]
    assert mine and mine[0]["utfall"] == "tillat", res
    return mine[0]["oppdrag_id"]


def _kjor_til(klient, mtk, signer, fid, *, maks=12):
    """Claim til modulen får DENNE fakturaens oppdrag (rester fra andre
    tester kvitteres `feilet` — det måles)."""
    from modules.m14_fakturakontroll import controller
    for _ in range(maks):
        ut = controller.kjor_en(klient, mtk, signer)
        if ut.get("utfall") == "tomt" or ut.get("faktura") == str(fid):
            return ut
        assert ut.get("utfall") == "avbrutt", ut
    raise AssertionError("fant aldri fakturaens oppdrag")


@pg_plan
def test_bilaget_kvitteres_hele_veien(migrator, miljo, app, klient, token):
    from modules.m14_fakturakontroll import controller
    controller._sov = lambda s: None
    _bokforpolicy(migrator); _rigg(migrator)
    fid = _fakt(migrator, netto=1000000, mva=250000, nummer="NK-2026-4471")
    oid = _bestilt_av_utloseren(app, fid)
    mtk, _ = _onboard_token(klient, migrator, MODUL, _release(migrator))
    ut = _kjor_til(klient, mtk, _signer, fid)
    assert ut["utfall"] == "utfort", ut
    assert ut["kvittering_status"] == 200 and \
        ut["bilagsnummer"] == "LF-NK-2026-4471", ut
    status, kv, otype, eier = _oppdrag(migrator, oid)
    assert (status, otype, eier) == ("utfort", "faktura.bokfor", MODUL), \
        (status, otype, eier)
    assert kv["resultat"] == "utfort"
    assert kv["ressurs_id"] == f"faktura:{fid}"
    assert kv["bilagsnummer"] == "LF-NK-2026-4471" and kv["retning"] == "ut"
    assert kv["belop_ore"] == 1250000
    assert kv["motpart"] == "Nordisk Drift AS"
    assert kv["malversjon"] == "bilag-v1"
    assert _kjor_til(klient, mtk, _signer, fid) == {"utfall": "tomt"}


@pg_plan
def test_avvist_mellom_bestilling_og_claim_bokfores_ikke(migrator, miljo,
                                                          app, klient, token):
    from modules.m14_fakturakontroll import controller
    controller._sov = lambda s: None
    _bokforpolicy(migrator); _rigg(migrator)
    fid = _fakt(migrator, netto=1100000, mva=275000)
    oid = _bestilt_av_utloseren(app, fid)
    _avvis(migrator, fid)
    mtk, _ = _onboard_token(klient, migrator, MODUL, _release(migrator))
    ut = _kjor_til(klient, mtk, _signer, fid)
    assert ut["utfall"] == "avbrutt" and ut["grunn"] == "faktura_avvist", ut
    status, kv, _, _ = _oppdrag(migrator, oid)
    assert status == "feilet" and kv["feilkode"] == "faktura_avvist"
