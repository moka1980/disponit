"""Porten for M-6: mennesket sletter en hentet melding NÅ (176).

  1. Slettingen tømmer ALLE lagrene i samme transaksjon: teksten,
     adressen og emnet er borte, tidspunktet og hashene består, og
     evidensen `epost.melding_slettet` er skrevet uten persondata.
     Gjenspill er et stille ja (`ny: false`). Flaten viser meldingen som
     slettet etterpå.
  2. Scopet er forvaltningens: `epost:read` alene får 403, og en annen
     tenants melding finnes ikke.

MUTASJONER SOM DREPER DENNE: la kroppen stå (1); la `epost:read` slette
(2).
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_m6_meldinger_port import ADRESSE, EMNE, KROPP, _kilde_med_melding
from .test_m37 import _sett_kontekst


def _tilstand(migrator, mid):
    _sett_kontekst(migrator, TENANT)
    r = migrator.execute(
        "SELECT kropp_kryptert, nonce, key_id, slettet_ts, avsender_hash,"
        " emne_hash, mottatt_ts, epost_melding::text FROM epost_melding"
        " WHERE tenant=%s AND melding_id=%s", (TENANT, mid)).fetchone()
    ev = migrator.execute(
        "SELECT count(*), coalesce(max(begrunnelse::text),'') FROM revisjonslogg"
        " WHERE tenant=%s AND kilde='m06_epost'"
        " AND handling='epost.melding_slettet'", (TENANT,)).fetchall()[0]
    migrator.rollback()
    return r, ev


@pg
def test_mennesket_sletter_naa_og_sporet_bestar(migrator, miljo, klient, token):
    kid, mid = _kilde_med_melding(migrator)
    _, ev_for = _tilstand(migrator, mid)
    adm, _ = token(rolle="admin", scopes=("epost:read", "epost:kilde:administrer"))
    hode = {"authorization": f"Bearer {adm}",
            "Idempotency-Key": "slett-" + secrets.token_hex(8)}
    r = klient.post(f"/v1/epost/meldinger/{mid}/slett", json={}, headers=hode)
    assert r.status_code == 200, r.text
    assert r.json()["slettet"] is True and r.json()["ny"] is True
    rad, ev = _tilstand(migrator, mid)
    assert rad[0] is None and rad[1] is None and rad[2] is None
    assert rad[3] is not None, "slettet_ts er ikke satt"
    assert rad[4] and rad[5] and rad[6], "sporet skal bestå"
    assert ADRESSE not in rad[7] and EMNE not in rad[7] and KROPP[:20] not in rad[7]
    assert ev[0] == ev_for[0] + 1
    assert ADRESSE not in ev[1] and EMNE not in ev[1]
    # Gjenspill: stille ja, og evidensen skrives ikke to ganger.
    r = klient.post(f"/v1/epost/meldinger/{mid}/slett", json={},
                    headers={"authorization": f"Bearer {adm}",
                             "Idempotency-Key": "slett-" + secrets.token_hex(8)})
    assert r.status_code == 200 and r.json()["ny"] is False, r.text
    assert _tilstand(migrator, mid)[1][0] == ev[0]
    # Flaten: slettet, uten tekst — og den sier at MENNESKET tok den,
    # ikke fristen. Det var eiers funn 10/9: «slettet etter tidsfristen»
    # på noe hun nettopp slettet selv.
    les, _ = token(rolle="leser", scopes=("epost:read",))
    r = klient.get(f"/v1/epost/meldinger?kilde={kid}",
                   headers={"authorization": f"Bearer {les}"})
    m = r.json()["meldinger"][0]
    assert m["reapet"] is True and m["emne"] is None and m["fra"] is None
    assert m["slettet_for_fristen"] is True, \
        "en melding slettet før fristen skal ikke se ut som retensjon"
    assert m["slettet_ts"] and m["slettet_ts"] < m["slettes_ts"]
    r = klient.get(f"/v1/epost/meldinger/{mid}",
                   headers={"authorization": f"Bearer {les}"})
    assert r.status_code == 200 and r.json()["kropp"] == ""


@pg
def test_slettingen_krever_forvaltningsscopet(migrator, miljo, klient, token):
    from api.app import RUTESCOPE
    assert RUTESCOPE[("POST", "/v1/epost/meldinger/{melding_id:uuid}/slett")] \
        == "epost:kilde:administrer"
    _, mid = _kilde_med_melding(migrator)
    les, _ = token(rolle="leser", scopes=("epost:read",))
    r = klient.post(f"/v1/epost/meldinger/{mid}/slett", json={},
                    headers={"authorization": f"Bearer {les}",
                             "Idempotency-Key": "s-" + secrets.token_hex(8)})
    assert r.status_code == 403, r.text
    assert _tilstand(migrator, mid)[0][0] is not None, "meldingen ble slettet"
    # Ukjent melding: 404, aldri en stille suksess.
    adm, _ = token(rolle="admin", scopes=("epost:read", "epost:kilde:administrer"))
    r = klient.post(
        "/v1/epost/meldinger/11111111-1111-4111-8111-111111111111/slett",
        json={}, headers={"authorization": f"Bearer {adm}",
                          "Idempotency-Key": "s-" + secrets.token_hex(8)})
    assert r.status_code == 404, r.text
