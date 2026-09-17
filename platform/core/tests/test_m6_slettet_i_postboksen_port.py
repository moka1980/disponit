"""Porten for 210: en melding slettet i postboksen slettes i registeret.

Eiers funn 17/9: «eposter som jeg slettet fra m365/outlook blir ikke
oppdatert på disponit.com». To veier, begge målt gjennom `hent_en` med
rigget Graph, i den ekte basen med planarbeiderens rolle:

  1. DELTA: en `@removed`-post for en hentet melding → meldingen viskes
     (kropp borte, `slettet_ts` satt), én evidensrad
     `epost.melding_fjernet_i_kilden` med innhenteren som aktør. En
     `@removed` for noe vi aldri hentet bokfører ingenting.
  2. AVSTEMMING: det som alt var slettet spørres om — 404 fra Graph på
     `/me/messages/{id}` → viskes; det som finnes får `kilde_sjekket_ts`;
     en annen feil (503) stopper avstemmingen uten å røre noe.
  3. Idempotent: samme `@removed` én gang til → ingen ny evidensrad.

MUTASJONER SOM DREPER DENNE: hopp over `@removed` igjen (1); slett også
ved 503 (2); skriv evidens ved gjentak (3).
"""
from __future__ import annotations

import secrets

from .test_api import DSN, MIGRATOR_DSN, TENANT, migrator, miljo  # noqa: F401
from .test_m37 import _sett_kontekst
from .test_m6_inntak_port import (PLAN_DSN, _kilde, _m365, _melding,  # noqa: F401
                                  _pa, _veksler, pg_plan)


def _rad(m, kid, lev_id):
    _sett_kontekst(m, TENANT)
    r = m.execute(
        "SELECT kropp_kryptert IS NULL, slettet_ts IS NOT NULL,"
        " kilde_sjekket_ts IS NOT NULL FROM epost_melding"
        " WHERE tenant=%s AND kilde_id=%s AND leverandor_melding_id=%s",
        (TENANT, kid, lev_id)).fetchone()
    m.rollback()
    return r


def _evidens(m):
    _sett_kontekst(m, TENANT)
    n = m.execute("SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
                  " AND kilde='m06_epost'"
                  " AND handling='epost.melding_fjernet_i_kilden'"
                  " AND aktor=%s", (TENANT, "agent:epost")).fetchone()[0]
    m.rollback()
    return int(n)


def _graf(sider, borte: set, ustabil: set, kall):
    from plan.epost import GraphFeil

    def graf(access, url, *, tekstkropp=False):
        kall.append(url)
        if "/me/messages/" in url:
            mid = url.split("/me/messages/")[1].split("?")[0]
            if "$select=id" in url:
                if mid in borte:
                    raise GraphFeil(404, "borte")
                if mid in ustabil:
                    raise GraphFeil(503, "ustabil")
                return {"id": mid}
            return {"body": {"contentType": "text", "content": "Hei"}}
        return sider.pop(0) if sider else {"value": [], "@odata.deltaLink": "x"}
    return graf


def _hent(pa, kid, graf):
    from plan.epost import hent_en
    from db.pg import sett_kontekst
    sett_kontekst(pa, TENANT, "agent:epost", "t")
    rad = pa.execute("SELECT tenant, kilde_id, postboks, delta_token,"
                     " sist_hentet_ts FROM epost_kilde WHERE tenant=%s"
                     " AND kilde_id=%s", (TENANT, kid)).fetchone()
    pa.rollback()
    return hent_en(pa, rad, graf=graf, veksler=_veksler({}))


@pg_plan
def test_slettet_i_postboksen_slettes_i_registeret(migrator, miljo, monkeypatch):
    from plan.epost import GRAPH, AKTOR
    assert AKTOR == "agent:epost"
    _m365(monkeypatch)
    kid, _k, _d = _kilde(migrator)
    m1, m2, m3 = _melding(1), _melding(2), _melding(3)
    delta = GRAPH + "/me/mailFolders/inbox/messages/delta?$deltatoken=a"
    pa = _pa()
    try:
        # runde 1: tre meldinger inn — alle finnes i postboksen
        kall: list = []
        res = _hent(pa, kid, _graf([{"value": [m1, m2, m3], "@odata.deltaLink": delta}],
                                   set(), set(), kall))
        assert res["nye"] == 3 and res.get("fjernet", 0) == 0, res
        assert res["avstemt"] == 3 and res["fjernet_ved_avstemming"] == 0, res
        assert _rad(migrator, kid, m1["id"]) == (False, False, True)
        for_ev = _evidens(migrator)

        # runde 2 (delta): m1 slettet i postboksen, pluss en @removed for
        # noe vi aldri hentet; avstemmingen finner m2 borte (404), m3 ustabil
        res = _hent(pa, kid, _graf(
            [{"value": [{"id": m1["id"], "@removed": {"reason": "deleted"}},
                        {"id": "aldri-hentet", "@removed": {"reason": "deleted"}}],
              "@odata.deltaLink": delta}],
            borte={m2["id"]}, ustabil={m3["id"]}, kall=[]))
        assert res.get("fjernet") == 1, res
        assert res["fjernet_ved_avstemming"] == 1, res
        assert _rad(migrator, kid, m1["id"]) == (True, True, True), "m1 ikke visket"
        assert _rad(migrator, kid, m2["id"]) == (True, True, True), "m2 (404) ikke visket"
        assert _rad(migrator, kid, m3["id"]) == (False, False, True), "m3 (503) rørt"
        assert _evidens(migrator) == for_ev + 2

        # runde 3: samme @removed én gang til — idempotent, ingen ny evidens
        res = _hent(pa, kid, _graf(
            [{"value": [{"id": m1["id"], "@removed": {"reason": "deleted"}}],
              "@odata.deltaLink": delta}], set(), set(), []))
        assert res.get("fjernet", 0) == 0, res
        assert _evidens(migrator) == for_ev + 2
    finally:
        pa.close()
