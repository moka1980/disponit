"""Porten mot #421: en ugyldig parameter i en lesevei er et 400, ikke en 500.

`_Avbrudd` bærer et ferdig HTTP-svar ut av en hjelper. Skriveveienes
ramme `_med_conn` fanget den; leseveienes ramme `_les` gjorde det ikke —
og to handlere kastet den dessuten FØR rammen ble kalt. `GET
/v1/hendelse/signaler` uten `fra` og `GET /v1/skatt/land/no/1` svarte
500 (Fjordlys-kampanjen 8/9).

Tre ting måles mot ekte base gjennom HTTP-døra, pluss et kildeskann som
feller nye toppnivå-kast i endepunkter som ender i `_les`.

MUTASJONEN SOM DREPER DENNE: fjern `except _Avbrudd` i `_les`.
"""
import ast
from pathlib import Path

from .test_api import (DSN, MIGRATOR_DSN, TENANT, pg,  # noqa: F401
                       app, klient, migrator, miljo, token)

API = Path(__file__).resolve().parents[1] / "api"


def _get(klient, tok, sti):
    return klient.get(sti, headers={"authorization": f"Bearer {tok}"})


@pg
def test_signaler_uten_fra_er_400(miljo, migrator, klient, token):
    tok, _ = token(rolle="sikkerhet", scopes=("security:read",))
    r = _get(klient, tok, "/v1/hendelse/signaler")
    assert r.status_code == 400 and r.json()["feil"] == "request_feilformet", r.text
    r = _get(klient, tok, "/v1/hendelse/signaler?fra=i-gaar")
    assert r.status_code == 400, r.text
    # …og den gyldige veien er uendret.
    r = _get(klient, tok, "/v1/hendelse/signaler?fra=2026-09-01T00:00:00%2B00:00")
    assert r.status_code == 200, r.text
    assert "kandidater" in r.json()


@pg
def test_skatt_land_med_ugyldig_landkode_er_400(miljo, migrator, klient, token):
    tok, _ = token(rolle="bruker", scopes=("okonomi:read",))
    r = _get(klient, tok, "/v1/skatt/land/no/1")
    assert r.status_code == 400 and r.json()["feil"] == "request_feilformet", r.text


def test_ingen_lesevei_kaster_avbrudd_utenfor_rammen():
    """Et endepunkt som ender i `return _les(...)` må gjøre all
    parameterkontroll inne i `_fn`. Kildeskann: ingen `raise _Avbrudd`
    og ingen kastende hjelper (`_tidspunkt_str`, `_sti_uuid`, `_valg`,
    `_heltall`, `_kropp`) på toppnivå i en slik funksjon."""
    kastere = ("_tidspunkt_str", "_sti_uuid", "_valg", "_heltall", "_kropp")
    gale = []
    for fil in sorted(API.glob("*.py")):
        tre = ast.parse(fil.read_text(encoding="utf-8"))
        for fn in tre.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            if not fn.name.endswith("_endepunkt"):
                continue
            src = ast.dump(fn)
            if "id='_les'" not in src:
                continue
            for stmt in fn.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.Import, ast.ImportFrom)):
                    continue
                d = ast.dump(stmt)
                if "_Avbrudd" in d or any(f"id='{k}'" in d for k in kastere):
                    gale.append((fil.name, fn.name, stmt.lineno))
    assert not gale, gale
