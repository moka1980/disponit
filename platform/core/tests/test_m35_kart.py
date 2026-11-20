"""M-35: veien inn til tjenestekartet og kontaktene (ADR-008).

089 bygget dørene; ingen HTTP-vei kalte dem. Portene her måler veien
ENDE TIL ENDE gjennom samme dør som en browser går, og de to tingene
API-et gjør selv fordi dørene ikke kan: playbooken bindes av serveren,
og en kontakt må være medlem av firmaet.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from pathlib import Path

import pytest

import playbookkatalog
from .test_api import (DSN, MIGRATOR_DSN, ANNEN_TENANT,  # noqa: F401
                       TENANT, app, klient, migrator, miljo)
from .test_m35_kontinuitet import _browserokt, _bruker
from .test_m37 import _sett_kontekst
from .test_rekruttering_http import _post

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

ROT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Katalogen
# ---------------------------------------------------------------------------

def test_katalogen_binder_bytes_og_holder_basens_form():
    """Referansen er `navn@sha256` av filens BYTES, og formen er den
    089s CHECK krever — et navn katalogen godtar kan aldri avvises av
    basen."""
    kat = playbookkatalog.katalog()
    assert len(kat) >= 4
    sjekk = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}@[0-9a-f]{64}$")
    migrasjon = (ROT / "platform/core/db/migrations"
                 / "089_m35_kontinuitet.sql").read_text(encoding="utf-8")
    # Uttrykket over ER basens — står det ikke ordrett i 089, måler
    # testen en form basen ikke har.
    assert sjekk.pattern in migrasjon
    assert [p["navn"] for p in kat] == sorted(p["navn"] for p in kat)
    for p in kat:
        fil = playbookkatalog.ROT / f"{p['navn']}.yaml"
        assert p["sha256"] == hashlib.sha256(fil.read_bytes()).hexdigest()
        assert p["ref"] == f"{p['navn']}@{p['sha256']}"
        assert sjekk.fullmatch(p["ref"])
        assert p["tittel"] and p["naar"] and p["steg"]
    assert playbookkatalog.ref_for("finnes-ikke") is None
    assert playbookkatalog.ref_for(None) is None


@pytest.mark.parametrize("innhold, grunn", [
    ("navn: a\ntittel: T\nnaar: N\nsteg: [x]\n", "filnavnet"),
    ("navn: feil\ntittel: T\nnaar: N\n", "feltene"),
    ("navn: feil\ntittel: T\nnaar: N\nsteg: []\n", "steg"),
    ("navn: Feil\ntittel: T\nnaar: N\nsteg: [x]\n", "navn"),
    ("navn: feil\ntittel: T\nnaar: N\nsteg: [x]\nekstra: 1\n", "feltene"),
    ("navn: feil\ntittel: ''\nnaar: N\nsteg: [x]\n", "tittel"),
])
def test_en_playbook_som_ikke_holder_formen_felles(tmp_path, innhold, grunn):
    """En halv katalog er verre enn ingen: en fil som ikke holder formen
    feller lastingen, den hoppes ikke over."""
    fil = tmp_path / "feil.yaml"
    fil.write_text(innhold, encoding="utf-8")
    with pytest.raises(playbookkatalog.Katalogfeil, match=grunn):
        playbookkatalog._les(fil)


def test_rutene_og_scopene():
    from api.app import RUTESCOPE
    assert RUTESCOPE[("GET", "/v1/kontinuitet/playbooks")] == \
        "kontinuitet:read"
    for sti in ("/v1/kontinuitet/tjenester",
                "/v1/kontinuitet/tjeneste/{tjeneste_id:str}",
                "/v1/kontinuitet/kontakter",
                "/v1/kontinuitet/kontakt/{kontakt_id:str}/bekreft"):
        assert RUTESCOPE[("POST", sti)] == "kontinuitet:write", sti


# ---------------------------------------------------------------------------
# HTTP-veien
# ---------------------------------------------------------------------------

def _tjeneste(**over):
    kropp = {"referent_type": "ekstern",
             "referent_id": "regnskap-" + secrets.token_hex(4),
             "kritikalitet": "kritisk", "rto_maal_s": 3600,
             "rpo_maal_s": 86400, "playbook": "leverandor-utfall",
             "kontaktrolle": "driftsvakt"}
    kropp.update(over)
    return kropp


def _les(klient, cookie):
    from api import sesjon as sesjonmodul
    r = klient.get("/v1/kontinuitet", cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 200, r.text
    return r.json()


@pg
def test_http_katalogen_leses_med_lesescopet(migrator, klient):
    from api import sesjon as sesjonmodul
    cookie, _csrf = _browserokt(migrator, ["leser"])
    r = klient.get("/v1/kontinuitet/playbooks",
                   cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 200, r.text
    assert [p["ref"] for p in r.json()["playbooks"]] == \
        [p["ref"] for p in playbookkatalog.katalog()]


@pg
def test_http_kartinnslag_bindes_av_serveren_og_kan_oppdateres(
        migrator, klient):
    """Klienten sender NAVNET; raden bærer `navn@sha256` av bytes i
    releasen. Gjenspill med samme nøkkel er et stille ja, samme tjeneste
    en gang til er en konflikt, og en oppdatering flytter innholdet uten
    å røre identiteten.

    MUTASJONEN SOM DREPER DENNE: la endepunktet lese en ferdig
    `playbook_ref` fra kroppen.
    """
    cookie, csrf = _browserokt(migrator, ["admin"])
    kropp, idem = _tjeneste(), secrets.token_urlsafe(24)
    r = _post(klient, cookie, csrf, "/v1/kontinuitet/tjenester", kropp, idem)
    assert r.status_code in (200, 201), r.text
    tid = r.json()["tjeneste_id"]
    ref = playbookkatalog.ref_for("leverandor-utfall")
    assert r.json()["playbook_ref"] == ref

    r2 = _post(klient, cookie, csrf, "/v1/kontinuitet/tjenester", kropp, idem)
    assert r2.status_code in (200, 201), r2.text
    assert r2.json()["tjeneste_id"] == tid

    # Samme tjeneste, ny intensjon: én rad per referent.
    r3 = _post(klient, cookie, csrf, "/v1/kontinuitet/tjenester", kropp)
    assert r3.status_code == 409, r3.text

    rad = [t for t in _les(klient, cookie)["tjenester"]
           if t["tjeneste_id"] == tid]
    assert len(rad) == 1 and rad[0]["playbook_ref"] == ref
    assert rad[0]["referent_id"] == kropp["referent_id"]

    # En ferdig referanse i kroppen blir IKKE trodd.
    falsk = "leverandor-utfall@" + "0" * 64
    r = _post(klient, cookie, csrf, "/v1/kontinuitet/tjenester",
              _tjeneste(playbook=falsk))
    assert r.status_code == 400, r.text
    r = _post(klient, cookie, csrf, "/v1/kontinuitet/tjenester",
              {**_tjeneste(), "playbook_ref": falsk, "playbook": None})
    assert r.status_code == 400, r.text

    r = _post(klient, cookie, csrf, f"/v1/kontinuitet/tjeneste/{tid}",
              {"kritikalitet": "viktig", "rto_maal_s": 7200,
               "rpo_maal_s": 3600, "playbook": "tjeneste-nede",
               "kontaktrolle": "daglig-leder"})
    assert r.status_code in (200, 201), r.text
    rad = [t for t in _les(klient, cookie)["tjenester"]
           if t["tjeneste_id"] == tid][0]
    assert (rad["kritikalitet"], rad["rto_maal_s"], rad["rpo_maal_s"],
            rad["kontaktrolle"]) == ("viktig", 7200, 3600, "daglig-leder")
    assert rad["playbook_ref"] == playbookkatalog.ref_for("tjeneste-nede")
    assert rad["referent_id"] == kropp["referent_id"]

    import uuid
    r = _post(klient, cookie, csrf,
              f"/v1/kontinuitet/tjeneste/{uuid.uuid4()}",
              {"kritikalitet": "viktig", "rto_maal_s": 7200,
               "rpo_maal_s": 3600, "playbook": "tjeneste-nede",
               "kontaktrolle": "daglig-leder"})
    assert r.status_code == 404, r.text


@pg
@pytest.mark.parametrize("over", [
    {"referent_type": "database"}, {"referent_id": "  "},
    {"kritikalitet": "hoy"}, {"rto_maal_s": 0}, {"rpo_maal_s": -5},
    {"rto_maal_s": True}, {"rpo_maal_s": "3600"},
    {"rto_maal_s": 400 * 86400}, {"playbook": "finnes-ikke"},
    {"kontaktrolle": ""},
])
def test_http_ugyldig_kartinnslag_er_400_og_skriver_ingenting(
        migrator, klient, over):
    cookie, csrf = _browserokt(migrator, ["admin"])
    kropp = _tjeneste(**over)
    r = _post(klient, cookie, csrf, "/v1/kontinuitet/tjenester", kropp)
    assert r.status_code == 400, r.text
    assert r.json()["feil"] == "request_feilformet"
    assert kropp["referent_id"].strip() == "" or not [
        t for t in _les(klient, cookie)["tjenester"]
        if t["referent_id"] == kropp["referent_id"]]


@pg
def test_http_kontakt_maa_vaere_medlem_og_bekreftes_for_seg(
        migrator, klient):
    """Døren ser bare at brukeren finnes på plattformen. API-et krever
    et AKTIVT medlemskap i firmaet — ellers kunne et firma føre opp en
    annens bruker.

    MUTASJONEN SOM DREPER DENNE: fjern medlemskapsspørringen i
    `kontakter_endepunkt`.
    """
    cookie, csrf = _browserokt(migrator, ["admin"])
    rolle = "vakt-" + secrets.token_hex(4)

    fremmed = _bruker(migrator, ANNEN_TENANT)
    migrator.commit()
    r = _post(klient, cookie, csrf, "/v1/kontinuitet/kontakter",
              {"rolle": rolle, "prioritet": 1, "bruker_id": str(fremmed)})
    assert r.status_code == 400, r.text
    assert not [k for k in _les(klient, cookie)["kontakter"]
                if k["rolle"] == rolle]

    medlem = _bruker(migrator)
    migrator.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller, aktiv)"
        " VALUES (%s,%s,%s,true)", (TENANT, medlem, ["leser"]))
    migrator.commit()
    for prioritet in (0, 10, True, "1"):
        r = _post(klient, cookie, csrf, "/v1/kontinuitet/kontakter",
                  {"rolle": rolle, "prioritet": prioritet,
                   "bruker_id": str(medlem)})
        assert r.status_code == 400, (prioritet, r.text)

    r = _post(klient, cookie, csrf, "/v1/kontinuitet/kontakter",
              {"rolle": rolle, "prioritet": 1, "bruker_id": str(medlem)})
    assert r.status_code in (200, 201), r.text
    kid = r.json()["kontakt_id"]
    k = [k for k in _les(klient, cookie)["kontakter"]
         if k["kontakt_id"] == kid][0]
    assert k["bekreftet"] is None and k["bruker_id"] == str(medlem)

    # Samme rolle og prioritet en gang til: plassen er tatt.
    r = _post(klient, cookie, csrf, "/v1/kontinuitet/kontakter",
              {"rolle": rolle, "prioritet": 1, "bruker_id": str(medlem)})
    assert r.status_code == 409, r.text

    r = _post(klient, cookie, csrf,
              f"/v1/kontinuitet/kontakt/{kid}/bekreft", {})
    assert r.status_code in (200, 201), r.text
    k = [k for k in _les(klient, cookie)["kontakter"]
         if k["kontakt_id"] == kid][0]
    assert k["bekreftet"] is not None and k["bekreftet_av"]

    import uuid
    r = _post(klient, cookie, csrf,
              f"/v1/kontinuitet/kontakt/{uuid.uuid4()}/bekreft", {})
    assert r.status_code == 404, r.text


@pg
def test_http_en_leser_kan_ikke_skrive_kart_eller_kontakter(
        migrator, klient):
    cookie, csrf = _browserokt(migrator, ["leser"])
    import uuid
    for sti, kropp in (
            ("/v1/kontinuitet/tjenester", _tjeneste()),
            (f"/v1/kontinuitet/tjeneste/{uuid.uuid4()}", _tjeneste()),
            ("/v1/kontinuitet/kontakter",
             {"rolle": "x", "prioritet": 1, "bruker_id": "y"}),
            (f"/v1/kontinuitet/kontakt/{uuid.uuid4()}/bekreft", {})):
        r = _post(klient, cookie, csrf, sti, kropp)
        assert r.status_code in (401, 403), (sti, r.text)
