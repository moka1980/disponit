"""Porten for ARC B kundeservice, PR 2: bestillingstypen
`kundeservice.svar.send`, godkjenningen som vilkår og DLP-heuristikken
som registerets vitne.

Ett godkjent utkast til én henvendelse. Det som måles, mot ekte base
gjennom HTTP-døra, med bransjemalen + utvidelsen
`policies/utvidelser/kundeservice-svar.yaml`:

  1. Typen er deklarert og LUKKET: to referanser og ett omfang — ingen
     adresse, ingen tekst i kroppen.
  2. Godkjent utkast, ren tekst, åpen henvendelse med adresse → TILLAT
     og et oppdrag hvis payload er referanser — aldri adressen, aldri
     teksten.
  3. Et utkast som ikke er godkjent → brudd `attestasjon_negativ`
     (svar_godkjent usant) → sak. Et menneskes ja er vilkåret.
  4. Teksten bærer et fødselsnummer → brudd (dlp_sjekk usant); teksten
     lover rabatt → brudd (ingen_okonomiske_lofter usant). Saken bærer
     KODENE, aldri teksten.
  5. Policyens frekvens (3 per døgn per henvendelse) stopper det fjerde.
  6. Målportene FØR kvote: lukket / uten adresse (skjema) → 409
     `svar_ikke_klart_for_sending`; ukjent henvendelse eller et utkast
     som ikke er henvendelsens → 404 `henvendelse_ukjent`.
  7. Uten `v_kundeservice`-nøkkel mintes ingen attestasjon → brudd
     `attestasjon_mangler`.

MUTASJONER SOM DREPER DENNE: attester `svar_godkjent` alltid sant (3),
la `dlp_funn` returnere tomt (4), eller sløyf målporten (6).
"""
import secrets
import uuid

import pytest
import yaml as _yaml

from .test_api import (DSN, MIGRATOR_DSN, POLICIES, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo, pg, token)
from .test_bestilling_kampanje_port import _payload
from .test_m17_avsender_port import _post
from .test_m17_kundeservice import _nokkel, _ta_imot, _utkast
from .test_m37 import _sett_kontekst
from .test_outbox_bestilling import _adminsesjon

REN = "Takk for henvendelsen. Vi ringer deg i morgen formiddag."


def test_bestillingstypen_er_deklarert_og_lukket():
    from api.bestilling import BESTILLINGSTYPER, Bestillingsfeil, normaliser
    from oppdragskontrakt import (FELTVERDIER, OPPDRAGSTYPER,
                                  UTFORELSESFRIST_VALG, type_for_handling)
    bt = BESTILLINGSTYPER["kundeservice.svar.send"]
    assert bt.eiermodul == "m17_kundeservice" and bt.omfang == ("svar",)
    assert bt.skjemafelt == frozenset({"bestillingstype", "henvendelse_ref",
                                       "utkast_ref", "omfang"})
    ot = OPPDRAGSTYPER["kundeservice.svar.send"]
    assert ot.paakrevde == frozenset({"henvendelse_id", "utkast_id", "omfang"})
    assert type_for_handling("kundeservice.svar.send").navn == \
        "kundeservice.svar.send"
    assert FELTVERDIER["kundeservice.svar.send"]["omfang"] == ("svar",)
    assert UTFORELSESFRIST_VALG["kundeservice.svar.send"] == \
        ("omfang", {"svar": 15 * 60})
    h, u = str(uuid.uuid4()), str(uuid.uuid4())
    assert normaliser("t", {"bestillingstype": "kundeservice.svar.send",
                            "henvendelse_ref": "henvendelse:" + h,
                            "utkast_ref": "utkast:" + u, "omfang": "svar"}) \
        == {"tenant": "t", "bestillingstype": "kundeservice.svar.send",
            "henvendelse_id": h, "utkast_id": u, "omfang": "svar"}
    for kropp in ({"bestillingstype": "kundeservice.svar.send",
                   "henvendelse_ref": "henvendelse:" + h, "omfang": "svar"},
                  {"bestillingstype": "kundeservice.svar.send",
                   "henvendelse_ref": "henvendelse:" + h,
                   "utkast_ref": "utkast:" + u, "omfang": "svar",
                   "tekst": "Hei"},
                  {"bestillingstype": "kundeservice.svar.send",
                   "henvendelse_ref": "henvendelse:" + h,
                   "utkast_ref": "utkast:" + u, "omfang": "alle"}):
        with pytest.raises(Bestillingsfeil):
            normaliser("t", kropp)


# ---------------------------------------------------------------------------
# Riggen
# ---------------------------------------------------------------------------

def _svarpolicy(m, *, tillatt_for=("agent",)):
    from api import policyregister
    p = _yaml.safe_load((POLICIES / "bransjemal-tjenestebedrift.yaml")
                        .read_text(encoding="utf-8"))
    utv = _yaml.safe_load((POLICIES / "utvidelser" / "kundeservice-svar.yaml")
                          .read_text(encoding="utf-8"))
    if not any(r.get("id") == "bestiller" for r in p["roller"]):
        p["roller"].append({"id": "bestiller",
                            "beskrivelse": "Bestiller svar"})
    p.setdefault("verifikatorer", {}).update(utv["verifikatorer"])
    for h in utv["handlinger"]:
        h = dict(h); h["tillatt_for"] = list(tillatt_for)
        p["handlinger"].append(h)
    policyregister.registrer(m, TENANT, p, p["meta"]["status"])
    m.commit()
    _sikre_m17_claimbar(m)


def _sikre_m17_claimbar(m):
    from miljo import gjeldende_miljo
    mv = gjeldende_miljo()
    m.execute("INSERT INTO modulhode (modul_id,status)"
              " VALUES ('m17_kundeservice','aktiv') ON CONFLICT DO NOTHING")
    m.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,"
        "kontrakt_hash,payload_schema_hash,kvittering_schema_hash,"
        "sideeffektklasse,reversibilitet)"
        " VALUES ('m17_kundeservice',1,%s,'p','k','krever_outbox',"
        "'kompenserende') ON CONFLICT DO NOTHING",
        ("k-" + secrets.token_hex(8),))
    khash = m.execute(
        "SELECT kontrakt_hash FROM modulkontrakt"
        " WHERE modul_id='m17_kundeservice' AND kontraktversjon=1"
    ).fetchone()[0]
    if m.execute("SELECT 1 FROM oppdragstype_register"
                 " WHERE oppdragstype='kundeservice.svar.send'"
                 ).fetchone() is None:
        m.execute(
            "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
            "kontraktversjon,kontrakt_hash)"
            " VALUES ('kundeservice.svar.send','m17_kundeservice',1,%s)",
            (khash,))
    if m.execute(
            "SELECT 1 FROM moduldeployment WHERE modul_id='m17_kundeservice'"
            " AND miljo=%s AND livslop='claiming'", (mv,)).fetchone() is None:
        rel = f"r17-{secrets.token_hex(6)}"
        m.execute(
            "INSERT INTO modulrelease (modul_id,release_id,"
            "kontraktversjon,kontrakt_hash,manifest_hash,artifact_digest)"
            " VALUES ('m17_kundeservice',%s,1,%s,'mh','ad')", (rel, khash))
        m.execute(
            "INSERT INTO moduldeployment (modul_id,release_id,"
            "kontraktversjon,kontrakt_hash,miljo,livslop)"
            " VALUES ('m17_kundeservice',%s,1,%s,%s,'claiming')",
            (rel, khash, mv))
    m.commit()


def _klar(klient, tok, *, godkjent=True, tekst=REN, kanal="epost",
          adresse=None):
    """Henvendelse over HTTP (adressen kryptert når kanalen er e-post)
    + utkast gjennom døra + dom → (henvendelse_id, utkast_id)."""
    from db.pg import koble
    adresse = adresse or ("kunde-" + secrets.token_hex(3) + "@nordvik.example")
    r = _post(klient, tok, "/v1/kundeservice/henvendelse",
              {"kanal": kanal, "ekstern_ref": "MSG-" + secrets.token_hex(4),
               "avsender": adresse, "emne": "Spørsmål",
               "kropp": "Hei, når kommer dere?",
               "mottatt": "2026-09-09T10:00:00+00:00"})
    assert r.status_code == 200, r.text
    hid = r.json()["henvendelse_id"]
    c = koble(DSN)
    try:
        key_id, dek = _nokkel(c, TENANT)
        uid = _utkast(c, TENANT, hid, key_id, dek, tekst=tekst)
        if godkjent:
            _sett_kontekst(c, TENANT)
            c.execute("SELECT m17_avgjor_utkast(%s,%s,'godkjent','u-test')",
                      (TENANT, uid))
            c.commit()
    finally:
        c.close()
    return hid, str(uid)


def _bestill(klient, cookie, csrf, hid, uid):
    from api import sesjon as sesjonmodul
    return klient.post("/v1/bestilling",
                       json={"bestillingstype": "kundeservice.svar.send",
                             "henvendelse_ref": f"henvendelse:{hid}",
                             "utkast_ref": f"utkast:{uid}",
                             "omfang": "svar"},
                       cookies={sesjonmodul.C_SESJON: cookie},
                       headers={"X-Disponit-CSRF": csrf,
                                "Idempotency-Key":
                                    "sv-" + secrets.token_hex(8)})


def _tok(token):
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "decisions:read"))
    return tok


# ---------------------------------------------------------------------------
# Portene
# ---------------------------------------------------------------------------

@pg
def test_godkjent_og_rent_utkast_gir_tillat_og_et_oppdrag_uten_tekst(
        klient, migrator, miljo, token):
    _svarpolicy(migrator, tillatt_for=("agent", "bestiller"))
    adresse = "kari-" + secrets.token_hex(3) + "@nordvik.example"
    hid, uid = _klar(klient, _tok(token), adresse=adresse)
    cookie, csrf = _adminsesjon()
    r = _bestill(klient, cookie, csrf, hid, uid)
    assert r.status_code == 200, r.text
    assert r.json()["beslutning"] == "tillat", r.text
    oid = r.json()["oppdrag_id"]
    payload = _payload(migrator, oid)
    assert payload == {"henvendelse_id": hid, "utkast_id": uid,
                       "omfang": "svar"}
    assert adresse not in str(payload) and REN not in str(payload)
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT oppdragstype, handling, eiermodul FROM oppdrag"
        " WHERE tenant=%s AND id=%s", (TENANT, oid)).fetchone()
    migrator.rollback()
    assert rad == ("kundeservice.svar.send", "kundeservice.svar.send",
                   "m17_kundeservice")


@pg
def test_et_utkast_som_ikke_er_godkjent_er_en_sak(klient, migrator, miljo,
                                                  token):
    _svarpolicy(migrator, tillatt_for=("agent", "bestiller"))
    hid, uid = _klar(klient, _tok(token), godkjent=False)
    cookie, csrf = _adminsesjon()
    r = _bestill(klient, cookie, csrf, hid, uid)
    assert r.status_code == 200 and r.json()["beslutning"] == "brudd", r.text
    assert "attestasjon_negativ" in r.json()["begrunnelse"], r.text
    assert r.json()["unntak_id"] and not r.json().get("oppdrag_id")


@pg
def test_dlp_funn_og_lofter_er_saker_med_koder_aldri_tekst(
        klient, migrator, miljo, token):
    _svarpolicy(migrator, tillatt_for=("agent", "bestiller"))
    tok = _tok(token)
    cookie, csrf = _adminsesjon()
    for tekst, kode in (("Vi har registrert fødselsnummer 010190 12345.",
                         "fodselsnummer"),
                        ("Du får 20 % rabatt på neste besøk.", "rabatt")):
        hid, uid = _klar(klient, tok, tekst=tekst)
        r = _bestill(klient, cookie, csrf, hid, uid)
        assert r.status_code == 200 and r.json()["beslutning"] == "brudd", \
            (tekst, r.text)
        assert "attestasjon_negativ" in r.json()["begrunnelse"], r.text
        assert r.json()["unntak_id"]
        # Loggen bærer aldri teksten.
        _sett_kontekst(migrator, TENANT)
        n = migrator.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
            " AND begrunnelse::text ILIKE %s",
            (TENANT, "%" + kode + "%")).fetchone()[0]
        migrator.rollback()
        assert n == 0


@pg
def test_policyens_frekvens_stopper_det_fjerde_svaret(klient, migrator,
                                                      miljo, token):
    _svarpolicy(migrator, tillatt_for=("agent", "bestiller"))
    tok = _tok(token)
    cookie, csrf = _adminsesjon()
    hid, uid = _klar(klient, tok)
    from db.pg import koble
    utkast = [uid]
    c = koble(DSN)
    try:
        key_id, dek = _nokkel(c, TENANT)
        for _ in range(3):
            u = _utkast(c, TENANT, hid, key_id, dek, tekst=REN)
            _sett_kontekst(c, TENANT)
            c.execute("SELECT m17_avgjor_utkast(%s,%s,'godkjent','u-test')",
                      (TENANT, u))
            c.commit()
            utkast.append(str(u))
    finally:
        c.close()
    for u in utkast[:3]:
        assert _bestill(klient, cookie, csrf, hid, u).json()["beslutning"] \
            == "tillat"
    r = _bestill(klient, cookie, csrf, hid, utkast[3])
    assert r.status_code == 200 and r.json()["beslutning"] == "brudd", r.text
    assert any(k.startswith("frekvensgrense") for k in
               r.json()["begrunnelse"]), r.text


@pg
@dekker("svar_ikke_klart_for_sending")
def test_malportene_stopper_for_kvote(klient, migrator, miljo, token):
    _svarpolicy(migrator, tillatt_for=("agent", "bestiller"))
    tok = _tok(token)
    cookie, csrf = _adminsesjon()
    # Skjema: ingen adresse å svare til.
    hid, uid = _klar(klient, tok, kanal="skjema", adresse="Ola Kunde")
    r = _bestill(klient, cookie, csrf, hid, uid)
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "svar_ikke_klart_for_sending"
    # Lukket henvendelse.
    hid, uid = _klar(klient, tok)
    r = _post(klient, tok, f"/v1/kundeservice/henvendelse/{hid}/lukk",
              {"utfall": "ikke_aktuell"})
    assert r.status_code == 200, r.text
    r = _bestill(klient, cookie, csrf, hid, uid)
    assert r.status_code == 409, r.text
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND handling='kundeservice.svar.send'", (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert n == 0


@pg
@dekker("henvendelse_ukjent")
def test_ukjent_henvendelse_eller_fremmed_utkast_er_404(klient, migrator,
                                                        miljo, token):
    _svarpolicy(migrator, tillatt_for=("agent", "bestiller"))
    tok = _tok(token)
    cookie, csrf = _adminsesjon()
    hid, uid = _klar(klient, tok)
    annen_hid, annet_uid = _klar(klient, tok)
    r = _bestill(klient, cookie, csrf, uuid.uuid4(), uid)
    assert r.status_code == 404 and r.json()["feil"] == "henvendelse_ukjent"
    r = _bestill(klient, cookie, csrf, hid, annet_uid)
    assert r.status_code == 404 and r.json()["feil"] == "henvendelse_ukjent"
    r = _bestill(klient, cookie, csrf, hid, uuid.uuid4())
    assert r.status_code == 404, r.text


@pg
def test_uten_v_kundeservice_nokkel_tas_vilkaarsveien(klient, migrator, miljo,
                                                      token, app, monkeypatch):
    _svarpolicy(migrator, tillatt_for=("agent", "bestiller"))
    hid, uid = _klar(klient, _tok(token))
    monkeypatch.delitem(app.tjeneste.nokler, "v_kundeservice")
    cookie, csrf = _adminsesjon()
    r = _bestill(klient, cookie, csrf, hid, uid)
    assert r.status_code == 200 and r.json()["beslutning"] == "brudd", r.text
    assert "attestasjon_mangler" in r.json()["begrunnelse"], r.text
