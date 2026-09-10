"""Porten for ARC B tilbud, PR 2: bestillingstypen `tilbud.generer`, og
registerets fakta som vilkår.

Ett godkjent tilbud til én kunde. Det som måles, mot ekte base gjennom
HTTP-døra, med bransjemalen + utvidelsen
`policies/utvidelser/tilbud-generer.yaml` (samme handling, med
persondata i dataklassene — sendingen går til kundens adresse):

  1. Typen er deklarert og LUKKET: én referanse og ett omfang — ingen
     sum, ingen priser, ingen adresse i kroppen.
  2. Godkjent tilbud innenfor boka → TILLAT og et oppdrag hvis payload
     er referansen, summen og gyldigheten — aldri adressen.
  3. En linje under rabattgrensen → brudd `attestasjon_negativ`
     (priser_fra_prisbok usant) → sak. Registerets faktum er vilkåret.
  4. En klausul erstattet etter tilbudet → brudd (laste_klausuler_uendret
     usant).
  5. Summen over policyens 150 000 → `belop_over_grense`.
  6. Målportene FØR kvote: utkast / forkastet / utløpt → 409
     `tilbud_ikke_klart_for_sending`; ukjent → 404 `tilbud_ukjent`.
  7. Uten `v_prisbok`-nøkkel mintes ingen attestasjon → brudd
     `attestasjon_mangler`. Malen urørt (bare agent) → rolle_ikke_tillatt.

MUTASJONER SOM DREPER DENNE: attester `priser_fra_prisbok` alltid sant
(3), la målporten slippe et utkast (6), sløyf klausulvilkåret (4).
"""
import secrets
import uuid

import pytest
import yaml as _yaml

from .test_api import (DSN, MIGRATOR_DSN, POLICIES, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo, pg, token)
from .test_bestilling_kampanje_port import _payload
from .test_m17_avsender_port import _post
from .test_m26_tilbud_port import _klausul, _kropp, _rigg, _rt, _tok
from .test_m37 import _sett_kontekst

MODUL = "m26_prisbok"


def test_bestillingstypen_er_deklarert_og_lukket():
    from api.bestilling import BESTILLINGSTYPER, Bestillingsfeil, normaliser
    from oppdragskontrakt import (FELTVERDIER, OPPDRAGSTYPER,
                                  UTFORELSESFRIST_VALG, type_for_handling)
    bt = BESTILLINGSTYPER["tilbud.generer"]
    assert bt.eiermodul == MODUL and bt.omfang == ("tilbud",)
    assert bt.skjemafelt == frozenset({"bestillingstype", "tilbud_ref",
                                       "omfang"})
    ot = OPPDRAGSTYPER["tilbud.generer"]
    assert ot.paakrevde == frozenset({"tilbud_id", "sum_ore", "gyldig_til",
                                      "omfang"})
    assert type_for_handling("tilbud.generer").navn == "tilbud.generer"
    assert FELTVERDIER["tilbud.generer"]["omfang"] == ("tilbud",)
    assert UTFORELSESFRIST_VALG["tilbud.generer"] == ("omfang",
                                                      {"tilbud": 15 * 60})
    tid = str(uuid.uuid4())
    n = normaliser(TENANT, {"bestillingstype": "tilbud.generer",
                            "tilbud_ref": f"tilbud:{tid}", "omfang": "tilbud"})
    assert n == {"tenant": TENANT, "bestillingstype": "tilbud.generer",
                 "tilbud_id": tid, "omfang": "tilbud"}
    for kropp in ({"bestillingstype": "tilbud.generer",
                   "tilbud_ref": f"tilbud:{tid}", "omfang": "tilbud",
                   "sum_ore": 1},
                  {"bestillingstype": "tilbud.generer",
                   "tilbud_ref": f"tilbud:{tid}", "omfang": "alt"},
                  {"bestillingstype": "tilbud.generer",
                   "tilbud_ref": "tilbud:x", "omfang": "tilbud"}):
        with pytest.raises(Bestillingsfeil):
            normaliser(TENANT, kropp)


def _tilbudpolicy(m, *, tillatt_for=("agent", "bestiller")):
    """Bransjemalen + utvidelsen (handlingen erstattes på id). Portene som
    måler vilkårene over HTTP åpner handlingen for `bestiller`; porten
    for rollen bruker utvidelsen som den er."""
    from api import policyregister
    p = _yaml.safe_load((POLICIES / "bransjemal-tjenestebedrift.yaml")
                        .read_text(encoding="utf-8"))
    utv = _yaml.safe_load((POLICIES / "utvidelser" / "tilbud-generer.yaml")
                          .read_text(encoding="utf-8"))
    if not any(r.get("id") == "bestiller" for r in p["roller"]):
        p["roller"].append({"id": "bestiller",
                            "beskrivelse": "Bestiller tilbud"})
    p.setdefault("verifikatorer", {}).update(utv["verifikatorer"])
    for h in utv["handlinger"]:
        h = dict(h); h["tillatt_for"] = list(tillatt_for)
        p["handlinger"] = [x for x in p["handlinger"] if x["id"] != h["id"]]
        p["handlinger"].append(h)
    policyregister.registrer(m, TENANT, p, p["meta"]["status"])
    m.commit()
    _sikre_m26_claimbar(m)


def _sikre_m26_claimbar(m):
    from miljo import gjeldende_miljo
    mv = gjeldende_miljo()
    m.execute("INSERT INTO modulhode (modul_id,status)"
              f" VALUES ('{MODUL}','aktiv') ON CONFLICT DO NOTHING")
    m.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,"
        "kontrakt_hash,payload_schema_hash,kvittering_schema_hash,"
        "sideeffektklasse,reversibilitet)"
        f" VALUES ('{MODUL}',1,%s,'p','k','krever_outbox',"
        "'kompenserende') ON CONFLICT DO NOTHING",
        ("k-" + secrets.token_hex(8),))
    khash = m.execute(
        "SELECT kontrakt_hash FROM modulkontrakt"
        f" WHERE modul_id='{MODUL}' AND kontraktversjon=1").fetchone()[0]
    # Typen registreres gjennom den HERDEDE funksjonen, som vertsteget.
    if m.execute("SELECT 1 FROM oppdragstype_register"
                 " WHERE oppdragstype='tilbud.generer'").fetchone() is None:
        m.execute("SET ROLE disponit_modules_admin")
        m.execute("SELECT registrer_oppdragstype('tilbud.generer', %s, 1,"
                  " %s, 'test')", (MODUL, khash))
        m.execute("RESET ROLE")
    if m.execute(
            f"SELECT 1 FROM moduldeployment WHERE modul_id='{MODUL}'"
            " AND miljo=%s AND livslop='claiming'", (mv,)).fetchone() is None:
        rel = f"r26-{secrets.token_hex(6)}"
        m.execute(
            "INSERT INTO modulrelease (modul_id,release_id,"
            "kontraktversjon,kontrakt_hash,manifest_hash,artifact_digest)"
            f" VALUES ('{MODUL}',%s,1,%s,'mh','ad')", (rel, khash))
        m.execute(
            "INSERT INTO moduldeployment (modul_id,release_id,"
            "kontraktversjon,kontrakt_hash,miljo,livslop)"
            f" VALUES ('{MODUL}',%s,1,%s,%s,'claiming')",
            (rel, khash, mv))
    m.commit()


def _klar(klient, tok, linjer, *, godkjent=True, **over):
    r = _post(klient, tok, "/v1/tilbud", _kropp(linjer, **over))
    assert r.status_code == 200, r.text
    tid = r.json()["tilbud_id"]
    if godkjent:
        r = _post(klient, tok, f"/v1/tilbud/{tid}/dom", {"status": "godkjent"})
        assert r.status_code == 200, r.text
    return tid, r.json()


def _bestill(klient, tok, tid):
    return klient.post("/v1/bestilling",
                       json={"bestillingstype": "tilbud.generer",
                             "tilbud_ref": f"tilbud:{tid}",
                             "omfang": "tilbud"},
                       headers={"authorization": f"Bearer {tok}",
                                "Idempotency-Key":
                                    "tb-" + secrets.token_hex(8)})


def _btok(token):
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "decisions:read",
                           "okonomi:read"))
    return tok


def _sak(migrator, uid):
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT r.begrunnelse FROM unntak u JOIN revisjonslogg r"
        " ON r.id=u.loggpost_id WHERE u.tenant=%s AND u.id=%s",
        (TENANT, uid)).fetchone()
    migrator.rollback()
    return [g["kode"] + ":" + str(g.get("params", {}).get("vilkaar", ""))
            for g in rad[0]]


@pg
def test_godkjent_tilbud_innenfor_boka_gir_tillat_og_et_oppdrag_med_referansen(
        klient, migrator, token):
    _tilbudpolicy(migrator)
    kabel, pumpe, _ = _rigg()
    tok = _btok(token)
    kropp = _kropp([{"produkt_id": str(kabel), "antall": 40},
                    {"produkt_id": str(pumpe), "antall": 1}])
    tid, _ = _klar(klient, tok, kropp["linjer"], kunde_epost=kropp["kunde_epost"])
    r = _bestill(klient, tok, tid)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["beslutning"] == "tillat" and d["oppdrag_id"], d
    p = _payload(migrator, d["oppdrag_id"])
    assert p["tilbud_id"] == tid and p["omfang"] == "tilbud"
    assert p["sum_ore"] == 40 * 1250 + 2890000
    assert set(p) == {"tilbud_id", "sum_ore", "gyldig_til", "omfang"}, p
    assert kropp["kunde_epost"] not in str(p)


@pg
def test_en_pris_under_rabattgrensen_er_en_usann_attestasjon(klient, migrator,
                                                             token):
    _tilbudpolicy(migrator)
    kabel, _, _ = _rigg(rabatt=100)
    tok = _btok(token)
    tid, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 10,
                                  "enhetspris_ore": 1100}])   # 12 % > 10 %
    d = _bestill(klient, tok, tid).json()
    assert d["beslutning"] == "brudd" and d["unntak_id"], d
    assert "attestasjon_negativ:priser_fra_prisbok" in _sak(migrator, d["unntak_id"])


@pg
def test_en_erstattet_klausul_er_en_usann_attestasjon(klient, migrator, token):
    _tilbudpolicy(migrator)
    kabel, _, _ = _rigg()
    tok = _btok(token)
    tid, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 10}])
    hode = {"authorization": f"Bearer {tok}"}
    kode = klient.get(f"/v1/tilbud/{tid}", headers=hode).json()["klausuler"][0]["kode"]
    c = _rt()
    try:
        _klausul(c, TENANT, kode=kode, tekst="30 dager", fra="2026-09-01")
    finally:
        c.close()
    d = _bestill(klient, tok, tid).json()
    assert d["beslutning"] == "brudd", d
    assert "attestasjon_negativ:laste_klausuler_uendret" in \
        _sak(migrator, d["unntak_id"])


@pg
def test_summen_over_policyens_tak_stopper(klient, migrator, token):
    _tilbudpolicy(migrator)
    _, pumpe, _ = _rigg()
    tok = _btok(token)
    tid, _ = _klar(klient, tok, [{"produkt_id": str(pumpe), "antall": 6}])  # 173 400
    d = _bestill(klient, tok, tid).json()
    assert d["beslutning"] == "brudd", d
    assert any(k.startswith("belop_over_grense") for k in
               _sak(migrator, d["unntak_id"]))


@pg
@dekker("tilbud_ikke_klart_for_sending", "tilbud_ukjent")
def test_maalportene_stopper_foer_kvote(klient, migrator, token):
    _tilbudpolicy(migrator)
    kabel, _, _ = _rigg()
    tok = _btok(token)
    utkast, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 1}],
                      godkjent=False)
    r = _bestill(klient, tok, utkast)
    assert r.status_code == 409 and \
        r.json()["feil"] == "tilbud_ikke_klart_for_sending", r.text
    forkastet, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 2}],
                         godkjent=False)
    _post(klient, tok, f"/v1/tilbud/{forkastet}/dom", {"status": "forkastet"})
    assert _bestill(klient, tok, forkastet).status_code == 409
    utlopt, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 3}],
                      tilbudsdato="2026-01-10", gyldig_til="2026-02-10")
    assert _bestill(klient, tok, utlopt).status_code == 409
    r = _bestill(klient, tok, uuid.uuid4())
    assert r.status_code == 404 and r.json()["feil"] == "tilbud_ukjent"


@pg
def test_uten_prisboknokkel_mintes_ingen_attestasjon(klient, migrator, token,
                                                     app, monkeypatch):
    _tilbudpolicy(migrator)
    kabel, _, _ = _rigg()
    tok = _btok(token)
    tid, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 1}])
    monkeypatch.delitem(app.tjeneste.nokler, "v_prisbok")
    d = _bestill(klient, tok, tid).json()
    assert d["beslutning"] == "brudd", d
    assert any(k.startswith("attestasjon_mangler") for k in
               _sak(migrator, d["unntak_id"]))


@pg
def test_utvidelsen_uroert_gir_bare_agenten_sendingen(klient, migrator, token):
    _tilbudpolicy(migrator, tillatt_for=("agent",))
    kabel, _, _ = _rigg()
    tok = _btok(token)
    tid, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 1}])
    d = _bestill(klient, tok, tid).json()
    assert d["beslutning"] == "brudd" and \
        "rolle_ikke_tillatt:" in _sak(migrator, d["unntak_id"]), d
