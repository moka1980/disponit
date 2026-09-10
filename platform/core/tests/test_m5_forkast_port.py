"""Porten for M-5 (177): et utkast kan forkastes.

Eiers funn 10/9: «når man lager en mal, er det ikke mulig å endre eller
slette den». Å ENDRE en publisert mal skal fortsatt være umulig — den
etterfølges av en ny versjon, fordi dokumenter viser tilbake til
versjonen de ble laget fra. Men et UTKAST kom aldri i kraft, og sto
likevel for alltid.

  1. `POST …/forkast` på et utkast → `forkastet`, med tidspunkt og
     aktør; raden og innholdet består; versjonsnummeret er brukt opp, så
     neste utkast får det neste nummeret.
  2. Døra nekter alt annet: en publisert versjon forkastes ikke (den
     trekkes tilbake), en tilbaketrukket heller ikke, og et forkastet
     utkast er terminalt — det kan verken publiseres eller forkastes på
     nytt. `m5_fyll_mal` nekter det som før.
  3. Gjerdet er basens, i to lag: web-API-rollen har ikke UPDATE på
     `malversjon` i det hele tatt (veien er døra), og vakten avviser
     `publisert → forkastet` selv for den som eier tabellen.

MUTASJONER SOM DREPER DENNE: la vakten tillate `forkastet → publisert`
(2). En dør som godtar `publisert` overlever HTTP-veien alene — vakten
gjerder overgangen uansett — så porten leser DØRENS egen setning
direkte, som i M-26.
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_m37 import _sett_kontekst

KOMP = [{"komponenttype": "tekst", "innhold": "Avtale mellom partene."},
        {"komponenttype": "felt", "feltnokkel": "kunde_navn"}]
FELT = [{"feltnokkel": "kunde_navn", "paakrevd": True, "felttype": "tekst",
         "beskrivelse": "Kundens navn"}]


def _tok(token):
    t, _ = token(rolle="bestiller",
                 scopes=("decisions:read", "bestilling:opprett"))
    return t


def _familie(klient, tok, navn=None):
    r = klient.post("/v1/dokumentmal/familier",
                    json={"navn": navn or ("Avtale " + secrets.token_hex(3))},
                    headers={"authorization": f"Bearer {tok}",
                             "Idempotency-Key": "fam-" + secrets.token_hex(8)})
    assert r.status_code == 200, r.text
    return r.json()["familie_id"]


def _versjon(klient, tok, fid):
    r = klient.post("/v1/dokumentmal/versjoner",
                    json={"familie_id": fid, "komponenter": KOMP, "felt": FELT},
                    headers={"authorization": f"Bearer {tok}",
                             "Idempotency-Key": "ver-" + secrets.token_hex(8)})
    assert r.status_code == 200, r.text
    return r.json()["versjon_id"], r.json()["versjonsnr"]


def _post(klient, tok, sti):
    return klient.post(sti, json={},
                       headers={"authorization": f"Bearer {tok}",
                                "Idempotency-Key": "o-" + secrets.token_hex(8)})


def _rad(migrator, vid):
    _sett_kontekst(migrator, TENANT)
    r = migrator.execute(
        "SELECT status, forkastet_ts, forkastet_av, publisert_ts,"
        " tilbaketrukket_ts, versjonsnr FROM malversjon"
        " WHERE tenant=%s AND versjon_id=%s", (TENANT, vid)).fetchone()
    n = migrator.execute(
        "SELECT count(*) FROM malkomponent WHERE tenant=%s AND versjon_id=%s",
        (TENANT, vid)).fetchone()[0]
    migrator.rollback()
    return r, n


@pg
def test_utkastet_forkastes_og_sporet_bestar(migrator, miljo, klient, token):
    tok = _tok(token)
    fid = _familie(klient, tok)
    vid, nr = _versjon(klient, tok, fid)
    assert nr == 1
    r = _post(klient, tok, f"/v1/dokumentmal/versjon/{vid}/forkast")
    assert r.status_code == 200, r.text
    assert r.json()["versjonsnr"] == 1
    rad, komp = _rad(migrator, vid)
    assert rad[0] == "forkastet" and rad[1] is not None and rad[2]
    assert rad[3] is None and rad[4] is None, \
        "et forkastet utkast ble aldri publisert"
    assert komp == len(KOMP), "innholdet består — append-only"
    # Nummeret er brukt opp: neste utkast er 2, ikke 1 om igjen.
    vid2, nr2 = _versjon(klient, tok, fid)
    assert nr2 == 2
    # Flaten viser den forkastede versjonen med sin status.
    r = klient.get("/v1/dokumentmal", headers={"authorization": f"Bearer {tok}"})
    fam = [f for f in r.json()["familier"] if f["familie_id"] == fid][0]
    st = {v["versjon_id"]: v["status"] for v in fam["versjoner"]}
    assert st[vid] == "forkastet" and st[vid2] == "utkast"


@pg
def test_bare_et_utkast_kan_forkastes(migrator, miljo, klient, token):
    tok = _tok(token)
    fid = _familie(klient, tok)
    vid, _ = _versjon(klient, tok, fid)
    assert _post(klient, tok,
                 f"/v1/dokumentmal/versjon/{vid}/publiser").status_code == 200
    # Publisert: forkasting avvises, tilbaketrekking er veien.
    r = _post(klient, tok, f"/v1/dokumentmal/versjon/{vid}/forkast")
    assert r.status_code == 409, r.text
    assert _rad(migrator, vid)[0][0] == "publisert"
    # DØRENS egen setning, ikke bare vaktens: begge gjerder, men det er
    # døra som forklarer hvorfor, og den forklaringen er kontrakten.
    import psycopg

    from db.pg import koble
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.Error) as e:
            rt.execute("SELECT m5_forkast_malversjon(%s,%s,'x')", (TENANT, vid))
        assert "ikke utkast" in str(e.value), str(e.value)
        rt.rollback()
    finally:
        rt.close()
    assert _post(klient, tok, f"/v1/dokumentmal/versjon/{vid}/trekk-tilbake"
                 ).status_code == 200
    r = _post(klient, tok, f"/v1/dokumentmal/versjon/{vid}/forkast")
    assert r.status_code == 409, r.text
    # Forkastet er terminalt.
    vid2, _ = _versjon(klient, tok, fid)
    assert _post(klient, tok,
                 f"/v1/dokumentmal/versjon/{vid2}/forkast").status_code == 200
    for sti in ("forkast", "publiser", "trekk-tilbake"):
        r = _post(klient, tok, f"/v1/dokumentmal/versjon/{vid2}/{sti}")
        assert r.status_code == 409, (sti, r.text)
    # Utfyllingen nekter den, som før.
    r = klient.post(f"/v1/dokumentmal/versjon/{vid2}/utfylling",
                    json={"verdier": {"kunde_navn": "Nordvik"}},
                    headers={"authorization": f"Bearer {tok}",
                             "Idempotency-Key": "u-" + secrets.token_hex(8)})
    assert r.status_code == 409, r.text


@pg
def test_gjerdet_er_basens_i_to_lag(migrator, miljo, klient, token):
    import psycopg

    from db.pg import koble
    tok = _tok(token)
    fid = _familie(klient, tok)
    vid, _ = _versjon(klient, tok, fid)
    assert _post(klient, tok,
                 f"/v1/dokumentmal/versjon/{vid}/publiser").status_code == 200
    # Lag 1: web-API-rollen har ingen UPDATE på malversjon — veien er døra.
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("UPDATE malversjon SET status='forkastet',"
                       " forkastet_ts=now(), forkastet_av='x'"
                       " WHERE tenant=%s AND versjon_id=%s", (TENANT, vid))
        rt.rollback()
    finally:
        rt.close()
    # Lag 2: vakten avviser overgangen for den som EIER tabellen også.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.Error) as e:
        migrator.execute("UPDATE malversjon SET status='forkastet',"
                         " forkastet_ts=now(), forkastet_av='x'"
                         " WHERE tenant=%s AND versjon_id=%s", (TENANT, vid))
    assert "lovlig overgang" in str(e.value), str(e.value)
    migrator.rollback()
    assert _rad(migrator, vid)[0][0] == "publisert"
