"""Porten mot #415: dørens egen setning når klienten uansett ERRCODE.

Dørene reiser gjerne `RAISE EXCEPTION '…' USING ERRCODE =
'invalid_parameter_value'`. Da er psycopg-klassen `InvalidParameterValue`,
ikke `RaiseException`, og `_doerdetalj` — som skilte på klassen — mistet
setningen: `POST /v1/likviditet/prognose/{id}/maaling` svarte
`400 request_feilformet` uten «uke 1 er ikke over (slutter …)»
(Fjordlys-kampanjen 8/9). Skillet går på HVOR feilen kom fra:
`diag.source_function == 'exec_stmt_raise'` er plpgsql-RAISE, alt annet
(constraint-brudd, delt på null) er husets indre og blir hjemme.

Og de tretti `_doerfeil` som aldri sendte `detalj` speiler nå de tolv
fra #412 — porten leser kilden så ingen ny modul faller tilbake.

MUTASJONEN SOM DREPER DENNE: bytt `source_function`-testen tilbake til
`isinstance(e, RaiseException)`, eller fjern `detalj=` i én `_doerfeil`.
"""
import re
from pathlib import Path

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, pg, migrator, miljo  # noqa: F401

API = Path(__file__).resolve().parents[1] / "api"


def _reis(migrator, sql):
    with pytest.raises(psycopg.Error) as ei:
        migrator.execute(sql)
    migrator.rollback()
    return ei.value


@pg
def test_raise_med_errcode_slipper_setningen_ut(miljo, migrator):
    from api.policyadmin_http import _doerdetalj
    e = _reis(migrator,
              "DO $$ BEGIN RAISE EXCEPTION 'm15: uke 1 er ikke over"
              " (slutter 2026-09-14). En måling av en uke som løper'"
              " USING ERRCODE = 'invalid_parameter_value'; END $$")
    assert isinstance(e, psycopg.errors.InvalidParameterValue)
    assert not isinstance(e, psycopg.errors.RaiseException)
    assert _doerdetalj(e) == ("m15: uke 1 er ikke over (slutter 2026-09-14)."
                              " En måling av en uke som løper")
    e = _reis(migrator,
              "DO $$ BEGIN RAISE EXCEPTION 'm54: ukjent regelsett'"
              " USING ERRCODE = 'no_data_found'; END $$")
    assert _doerdetalj(e) == "m54: ukjent regelsett"


@pg
def test_postgres_egne_ord_blir_hjemme(miljo, migrator):
    from api.policyadmin_http import _doerdetalj
    e = _reis(migrator,
              "CREATE TEMP TABLE t415 (a int PRIMARY KEY);"
              " INSERT INTO t415 VALUES (1), (1)")
    assert isinstance(e, psycopg.errors.UniqueViolation)
    assert _doerdetalj(e) is None
    e = _reis(migrator, "SELECT 1/0")
    assert _doerdetalj(e) is None


def test_hver_doerfeil_sender_detalj():
    funnet, gale = [], []
    for fil in sorted(API.glob("*.py")):
        kilde = fil.read_text(encoding="utf-8")
        m = re.search(r"^def _doerfeil\(e, rid\):\n(.*?)(?=^def |^class |\Z)",
                      kilde, re.S | re.M)
        if not m:
            continue
        funnet.append(fil.name)
        kropp = m.group(1)
        kall = re.findall(r"_feil\(", kropp)
        med = re.findall(r"detalj=_doerdetalj\(e\)", kropp)
        if not kall or len(med) != len(kall):
            gale.append((fil.name, len(kall), len(med)))
    assert len(funnet) >= 40, f"porten fant bare {len(funnet)} — regexen er gal"
    assert not gale, (
        "disse _doerfeil oversetter et dørnekt uten dørens setning"
        f" (fil, _feil-kall, med detalj): {gale}")
