"""Porten for 230: svarmaler — firmaets egne formuleringer.

  1. En mal lagres, vises, endres og slettes; et tapt svar og et nytt
     klikk gir SAMME mal, ikke to.
  2. Ett levende navn per firma; et slettet navn er ledig igjen.
  3. Ugyldig innhold avvises: tomt, for langt, ukjent tema.
  4. Malene er tenantbundet, og en leser kan se men ikke skrive.
  5. Hver lagring og sletting står i revisjonsloggen, uten malteksten.

MUTASJONEN SOM DREPER DENNE: fjern den unike indeksen på levende navn (2),
eller la `m17_svarmalene` returnere slettede (1).
"""
import secrets

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_m37 import _sett_kontekst

SKRIV = ("bestilling:opprett", "decisions:read")
STI = "/v1/kundeservice/svarmaler"


def _post(klient, tok, sti, kropp, idem=None):
    return klient.post(sti, json=kropp, headers={
        "authorization": f"Bearer {tok}",
        "Idempotency-Key": idem or secrets.token_urlsafe(24)})


def _maler(klient, tok):
    r = klient.get(STI, headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    return r.json()["maler"]


@pg
def test_en_mal_lagres_endres_og_slettes(migrator, miljo, klient, token):
    tok, _ = token(rolle="bestiller", scopes=SKRIV)
    navn = "Fakturaspørsmål " + secrets.token_hex(3)
    tekst = "Hei,\n\ntakk for at du tok kontakt om fakturaen. Vi ser på den."
    idem = secrets.token_urlsafe(24)
    r = _post(klient, tok, STI, {"navn": navn, "tekst": tekst,
                                 "tema": "faktura"}, idem)
    assert r.status_code == 200, r.text
    mal_id = r.json()["mal_id"]
    assert r.json()["ny"] is True
    # Samme nøkkel én gang til: samme mal, ikke en til.
    r2 = _post(klient, tok, STI, {"navn": navn, "tekst": tekst,
                                  "tema": "faktura"}, idem)
    assert r2.status_code == 200 and r2.json()["mal_id"] == mal_id
    mine = [m for m in _maler(klient, tok) if m["navn"] == navn]
    assert len(mine) == 1
    assert mine[0] == {**mine[0], "mal_id": mal_id, "tema": "faktura",
                       "tekst": tekst}

    # Endring: samme id, ny tekst, tema fjernet.
    r = _post(klient, tok, STI, {"mal_id": mal_id, "navn": navn,
                                 "tekst": "Ny tekst."})
    assert r.status_code == 200 and r.json()["ny"] is False, r.text
    m = [x for x in _maler(klient, tok) if x["mal_id"] == mal_id][0]
    assert m["tekst"] == "Ny tekst." and m["tema"] is None

    # Sletting: borte fra lista, og navnet er ledig igjen.
    r = _post(klient, tok, f"{STI}/{mal_id}/slett", {})
    assert r.status_code == 200 and r.json()["ny"] is True
    assert not [x for x in _maler(klient, tok) if x["mal_id"] == mal_id]
    assert _post(klient, tok, f"{STI}/{mal_id}/slett",
                 {}).json()["ny"] is False
    r = _post(klient, tok, STI, {"navn": navn, "tekst": "Igjen."})
    assert r.status_code == 200 and r.json()["mal_id"] != mal_id


@pg
def test_ett_levende_navn_og_gyldig_innhold(migrator, miljo, klient, token):
    tok, _ = token(rolle="bestiller", scopes=SKRIV)
    navn = "Befaring " + secrets.token_hex(3)
    assert _post(klient, tok, STI,
                 {"navn": navn, "tekst": "En."}).status_code == 200
    # Samme navn, annen skrivemåte: to maler ingen kan skille i en liste.
    r = _post(klient, tok, STI, {"navn": f"  {navn.upper()} ",
                                 "tekst": "To."})
    assert r.status_code == 409, r.text
    assert len([m for m in _maler(klient, tok)
                if m["navn"].lower() == navn.lower()]) == 1

    for ugyldig in ({"navn": "", "tekst": "x"}, {"navn": "x", "tekst": "  "},
                    {"navn": "x" * 81, "tekst": "x"},
                    {"navn": "x", "tekst": "y" * 4001},
                    {"navn": "x", "tekst": "y", "tema": "ukjent"},
                    {"navn": "x", "tekst": "y", "mal_id": "ikke-uuid"},
                    {"tekst": "y"}):
        r = _post(klient, tok, STI, ugyldig)
        assert r.status_code in (400, 409), (ugyldig, r.text)
        assert r.status_code == 400 or "tema" not in ugyldig


@pg
def test_malene_er_firmaets_og_en_leser_kan_ikke_skrive(
        migrator, miljo, klient, token):
    tok, _ = token(rolle="bestiller", scopes=SKRIV)
    navn = "Ordrestatus " + secrets.token_hex(3)
    hemmelig = "Formulering-" + secrets.token_hex(5)
    mal_id = _post(klient, tok, STI, {"navn": navn,
                                      "tekst": hemmelig}).json()["mal_id"]
    annen = "t-230-" + secrets.token_hex(4)
    atok, _ = token(tenant=annen, rolle="bestiller", scopes=SKRIV)
    assert _maler(klient, atok) == []
    # En annen tenant kan ikke slette den heller: stille nei, malen står.
    assert _post(klient, atok, f"{STI}/{mal_id}/slett",
                 {}).json()["ny"] is False
    assert [m for m in _maler(klient, tok) if m["mal_id"] == mal_id]

    les, _ = token(rolle="leser", scopes=("decisions:read",))
    assert [m for m in _maler(klient, les) if m["mal_id"] == mal_id]
    assert _post(klient, les, STI,
                 {"navn": "x", "tekst": "y"}).status_code == 403
    assert _post(klient, les, f"{STI}/{mal_id}/slett",
                 {}).status_code == 403

    # Sporet: lagringen står i revisjonsloggen — uten malteksten.
    _sett_kontekst(migrator, TENANT)
    logg = migrator.execute(
        "SELECT handling, revisjonslogg::text FROM revisjonslogg"
        " WHERE tenant=%s AND kilde='m17_kundeservice'"
        "   AND handling LIKE 'svarmal.%%' ORDER BY id DESC LIMIT 5",
        (TENANT,)).fetchall()
    migrator.rollback()
    assert logg and logg[0][0] == "svarmal.lagret"
    assert all(hemmelig not in r[1] for r in logg)
