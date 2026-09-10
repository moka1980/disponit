"""Porten for M-6 (179): mennesket skriver svaret og godkjenner det.

Eiervedtak 10/9: svar skal gå fra kundens egen postboks. Første ledd er
at et menneske skriver teksten og sier ja — INGENTING går ut av dette
lageret; sendingen er en egen vei gjennom policyporten (PR 3–5).

  1. Utkastet skrives kryptert (basen ser aldri svaret i klartekst),
     fødes `foreslatt`, og detaljen bærer det tilbake med teksten
     dekryptert for økten.
  2. Dommen: `godkjent` og `forkastet` bærer aktør og tidspunkt;
     gjenspill er et stille ja; `godkjent → forkastet` er lov (angre),
     men `forkastet` er terminal.
  3. «sendt» er ALDRI en dom et menneske setter — verken over HTTP eller
     gjennom døra.
  4. Teksten er append-only, og et utkast kan ikke skrives til en
     slettet melding.

MUTASJONER SOM DREPER DENNE: la døra godta status «sendt» (3); la
`forkastet` gå videre til «godkjent» (2).
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo, pg, token)
from .test_m6_meldinger_port import _kilde_med_melding
from .test_m37 import _sett_kontekst

SVAR = "Hei Per, takk for henvendelsen. Vi kommer torsdag kl. 09."


def _adm(token):
    # 088 registrerte `epost:utkast:behandle` for nettopp dette: å skrive
    # og avgjøre et utkast er ikke å administrere en tilkobling.
    t, _ = token(rolle="admin", scopes=("epost:read", "epost:utkast:behandle"))
    return t


def _post(klient, tok, sti, kropp):
    return klient.post(sti, json=kropp,
                       headers={"authorization": f"Bearer {tok}",
                                "Idempotency-Key": "u-" + secrets.token_hex(8)})


def _detalj(klient, tok, mid):
    r = klient.get(f"/v1/epost/meldinger/{mid}",
                   headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    return r.json()


@pg
def test_utkastet_skrives_kryptert_og_fodes_foreslatt(migrator, miljo, klient,
                                                       token):
    _, mid = _kilde_med_melding(migrator)
    tok = _adm(token)
    r = _post(klient, tok, f"/v1/epost/meldinger/{mid}/svarutkast",
              {"tekst": SVAR})
    assert r.status_code == 200, r.text
    uid = r.json()["utkast_id"]
    assert r.json()["status"] == "foreslatt"
    # Basen ser aldri svaret.
    _sett_kontekst(migrator, TENANT)
    raa = migrator.execute("SELECT epost_utkast::text FROM epost_utkast"
                           " WHERE tenant=%s AND utkast_id=%s",
                           (TENANT, uid)).fetchone()[0]
    ev = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND kilde='m06_epost' AND handling='epost.utkast_skrevet'",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert "torsdag" not in raa and "Hei Per" not in raa
    assert ev >= 1
    # Detaljen bærer det tilbake, dekryptert for økten.
    d = _detalj(klient, tok, mid)
    mine = [u for u in d["utkast"] if u["utkast_id"] == uid]
    assert mine and mine[0]["tekst"] == SVAR and mine[0]["status"] == "foreslatt"
    assert mine[0]["avgjort_ts"] is None and mine[0]["slettet"] is False
    # Tom tekst er ikke et utkast.
    assert _post(klient, tok, f"/v1/epost/meldinger/{mid}/svarutkast",
                 {"tekst": "   "}).status_code == 400


@pg
@dekker("epost_ulovlig_tilstand")
def test_dommen_baerer_aktor_og_felles_en_gang(migrator, miljo, klient, token):
    _, mid = _kilde_med_melding(migrator)
    tok = _adm(token)
    uid = _post(klient, tok, f"/v1/epost/meldinger/{mid}/svarutkast",
                {"tekst": SVAR}).json()["utkast_id"]
    r = _post(klient, tok, f"/v1/epost/utkast/{uid}/dom", {"status": "godkjent"})
    assert r.status_code == 200 and r.json()["status"] == "godkjent", r.text
    d = _detalj(klient, tok, mid)
    u = [x for x in d["utkast"] if x["utkast_id"] == uid][0]
    assert u["status"] == "godkjent" and u["avgjort_ts"] and u["avgjort_av"]
    # Gjenspill: stille ja, samme dom.
    r = _post(klient, tok, f"/v1/epost/utkast/{uid}/dom", {"status": "godkjent"})
    assert r.status_code == 200 and r.json()["status"] == "godkjent"
    # Angre FØR sendingen er lov.
    r = _post(klient, tok, f"/v1/epost/utkast/{uid}/dom", {"status": "forkastet"})
    assert r.status_code == 200 and r.json()["status"] == "forkastet", r.text
    # ...men forkastet er terminal.
    r = _post(klient, tok, f"/v1/epost/utkast/{uid}/dom", {"status": "godkjent"})
    assert r.status_code == 409, r.text
    assert _post(klient, tok, "/v1/epost/utkast/"
                 "11111111-1111-4111-8111-111111111111/dom",
                 {"status": "godkjent"}).status_code == 404


@pg
def test_sendt_er_aldri_en_dom(migrator, miljo, klient, token):
    import psycopg

    from db.pg import koble
    _, mid = _kilde_med_melding(migrator)
    tok = _adm(token)
    uid = _post(klient, tok, f"/v1/epost/meldinger/{mid}/svarutkast",
                {"tekst": SVAR}).json()["utkast_id"]
    # Over HTTP: 400, formen er ikke en dom.
    assert _post(klient, tok, f"/v1/epost/utkast/{uid}/dom",
                 {"status": "sendt"}).status_code == 400
    # Gjennom DØRA: dens egen setning, ikke bare vaktens.
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.Error) as e:
            rt.execute("SELECT m6_avgjor_utkast(%s,%s,'sendt','x')",
                       (TENANT, uid))
        assert "kvitteringens vei" in str(e.value), str(e.value)
        rt.rollback()
    finally:
        rt.close()
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT status FROM epost_utkast WHERE tenant=%s AND utkast_id=%s",
        (TENANT, uid)).fetchone()[0] == "foreslatt"
    migrator.rollback()


@pg
def test_utkast_til_en_slettet_melding_finnes_ikke(migrator, miljo, klient,
                                                    token):
    _, mid = _kilde_med_melding(migrator, reapet=True)
    tok = _adm(token)
    r = _post(klient, tok, f"/v1/epost/meldinger/{mid}/svarutkast",
              {"tekst": SVAR})
    assert r.status_code == 404, r.text
    assert _detalj(klient, tok, mid)["utkast"] == []


@pg
def test_scopet_er_utkastets_ikke_kildens(migrator, miljo, klient, token):
    """088s `epost:utkast:behandle` er scopet for å skrive og avgjøre et
    utkast. En økt som bare administrerer TILKOBLINGER, skal ikke kunne
    skrive et svar i kundens navn (CodeRabbit)."""
    from api.app import RUTESCOPE
    for sti in ("/v1/epost/meldinger/{melding_id:uuid}/svarutkast",
                "/v1/epost/utkast/{utkast_id:uuid}/dom"):
        assert RUTESCOPE[("POST", sti)] == "epost:utkast:behandle", sti
    _, mid = _kilde_med_melding(migrator)
    kun_kilde, _ = token(rolle="admin",
                         scopes=("epost:read", "epost:kilde:administrer"))
    r = _post(klient, kun_kilde, f"/v1/epost/meldinger/{mid}/svarutkast",
              {"tekst": SVAR})
    assert r.status_code == 403, r.text

