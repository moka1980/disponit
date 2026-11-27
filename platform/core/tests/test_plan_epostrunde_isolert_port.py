"""Porten: én treg postboks tar ikke ned planrunden.

MÅLT I DRIFT 26/11 og 27/11: `httpx.ReadTimeout` fra Microsoft Graph
boblet ut av M-6-innhenteren og felte HELE `disponit-plan` — IMAP-runden
og stillereglene etter den kjørte ikke den gangen.

MUTASJON SOM DREPER DENNE: kall `epostrunde` direkte igjen i
`materialiser.kjor_en_runde`.
"""
import os

import pytest

from .test_api import DSN, app, miljo  # noqa: F401

PLAN_DSN = os.environ.get("DISPONIT_TEST_PLAN_DSN")
pg = pytest.mark.skipif(not (DSN and PLAN_DSN),
                        reason="DISPONIT_TEST_DSN/PLAN_DSN ikke satt")


@pg
def test_et_graph_tidsavbrudd_stopper_ikke_resten_av_runden(app, monkeypatch,
                                                           capsys):
    import httpx

    import plan.epost
    import plan.epost_imap
    import plan.stilleregler
    from db.pg import koble
    from plan.materialiser import kjor_en_runde

    def treg(*a, **kw):
        raise httpx.ReadTimeout("The read operation timed out")

    kalt = []
    monkeypatch.setattr(plan.epost, "kjor_en_runde", treg)
    monkeypatch.setattr(plan.epost_imap, "kjor_en_runde",
                        lambda *a, **kw: kalt.append("imap") or {"ok": 1})
    ekte_stille = plan.stilleregler.kjor_en_runde
    monkeypatch.setattr(plan.stilleregler, "kjor_en_runde",
                        lambda c: kalt.append("stille") or ekte_stille(c))
    pa = koble(PLAN_DSN)
    try:
        res = kjor_en_runde(app.tjeneste, pa)
    finally:
        pa.close()
    assert res["epost"] == {"avbrutt": "ReadTimeout"}
    assert kalt == ["imap", "stille"], "runden stoppet ved M365-feilen"
    assert '"plan_epostrunde_avbrutt"' in capsys.readouterr().out
