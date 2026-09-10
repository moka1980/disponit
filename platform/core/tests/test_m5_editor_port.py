"""Porten for M-5-editoren: formen flaten bygger, er formen døra tar.

Editoren i `dokumentmal.js` setter sammen komponentlista og
feltdeklarasjonene selv og sender dem i ETT kall. Denne porten kjører
NØYAKTIG den kroppen hele veien — utkast, publisering, utfylling — så en
form flaten finner på, aldri kan bli en mal ingen kan bruke.

  1. Rundturen: editorens kropp → utkast → publisert → utfylt dokument
     med feltet dekket og den låste klausulen urørt.
  2. Formene editoren nekter, stopper også i basen — men på ULIKT
     tidspunkt, og porten sier hvilket: en tom komponentliste og en
     ulovlig feltnøkkel avvises av OPPRETTELSEN, mens en feltkomponent
     uten deklarasjon lagres som utkast og avvises av PUBLISERINGEN
     (094s design: utkastet er en arbeidsflate, publiseringen er dommen
     om helhet). Editoren gjerder alle tre i flaten, så mennesket får
     svaret mens det skriver — ikke først når det trykker publiser.
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_m5_forkast_port import _familie, _post, _tok

#: Kroppen `versjonseditor` bygger for: tekst, felt, låst klausul.
KOMPONENTER = [
    {"komponenttype": "tekst", "innhold": "Avtale mellom partene."},
    {"komponenttype": "felt", "feltnokkel": "kunde_navn"},
    {"komponenttype": "klausul", "innhold": "Betaling innen 14 dager.",
     "laast": True},
]
FELT = [{"feltnokkel": "kunde_navn", "paakrevd": True, "felttype": "tekst",
         "beskrivelse": "Kundens navn"}]


def _ny(klient, tok, fid, komponenter=None, felt=None):
    return klient.post(
        "/v1/dokumentmal/versjoner",
        json={"familie_id": fid,
              "komponenter": KOMPONENTER if komponenter is None else komponenter,
              "felt": FELT if felt is None else felt},
        headers={"authorization": f"Bearer {tok}",
                 "Idempotency-Key": "e-" + secrets.token_hex(8)})


@pg
def test_editorens_kropp_gaar_hele_veien(migrator, miljo, klient, token):
    tok = _tok(token)
    fid = _familie(klient, tok)
    r = _ny(klient, tok, fid)
    assert r.status_code == 200, r.text
    vid = r.json()["versjon_id"]
    assert _post(klient, tok,
                 f"/v1/dokumentmal/versjon/{vid}/publiser").status_code == 200
    r = klient.post(f"/v1/dokumentmal/versjon/{vid}/utfylling",
                    json={"verdier": {"kunde_navn": "Nordvik Sameie"}},
                    headers={"authorization": f"Bearer {tok}",
                             "Idempotency-Key": "u-" + secrets.token_hex(8)})
    assert r.status_code == 200, r.text
    deler = r.json()["komponenter"]
    assert [d["komponenttype"] for d in deler] == ["tekst", "felt", "klausul"]
    assert deler[0]["tekst"] == "Avtale mellom partene."
    assert deler[1]["dekket"] is True and deler[1]["tekst"] == "Nordvik Sameie"
    assert deler[1]["paakrevd"] is True
    # Den låste klausulen bæres urørt ut, uten feltnøkkel å treffes på.
    assert deler[2]["laast"] is True and deler[2]["feltnokkel"] is None \
        and deler[2]["tekst"] == "Betaling innen 14 dager."


@pg
def test_formene_editoren_nekter_nekter_dora_ogsaa(migrator, miljo, klient,
                                                    token):
    tok = _tok(token)
    fid = _familie(klient, tok)
    # Tom komponentliste — editoren krever minst én del.
    assert _ny(klient, tok, fid, komponenter=[]).status_code == 400
    # Feltnøkkel utenfor mønsteret — editoren sier det før innsending.
    r = _ny(klient, tok, fid,
            komponenter=[{"komponenttype": "felt", "feltnokkel": "Kunde Navn"}],
            felt=[{"feltnokkel": "Kunde Navn", "paakrevd": True,
                   "felttype": "tekst", "beskrivelse": "x"}])
    assert r.status_code in (400, 409), r.text
    # En `felt`-komponent uten deklarasjon: utkastet fødes (094 lar en
    # arbeidsflate være halvferdig), men PUBLISERINGEN nekter — et
    # påkrevd felt uten deklarasjon kan aldri fylles ut. Editoren fanger
    # det i flaten, så ingen kommer hit.
    r = _ny(klient, tok, fid,
            komponenter=[{"komponenttype": "felt", "feltnokkel": "kunde_navn"}],
            felt=[])
    assert r.status_code == 200, r.text
    hull = r.json()["versjon_id"]
    assert _post(klient, tok,
                 f"/v1/dokumentmal/versjon/{hull}/publiser").status_code == 409
