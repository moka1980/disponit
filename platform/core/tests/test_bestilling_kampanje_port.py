"""Porten for ARC B kampanje, PR 2: bestillingstypen `kampanje.send` og
samtykkevitnet `v_samtykke`.

Én mottaker i én kampanje på sendedagen. Det som måles, mot ekte base
gjennom HTTP-døra, med bransjemalen + utvidelsen
`policies/utvidelser/kampanje-send.yaml`:

  1. Typen er deklarert og LUKKET: to referanser og ett omfang — ingen
     dato, intet innhold, ingen adresse i kroppen.
  2. Planlagt mottaker med gyldig samtykke, adresse og innhold → TILLAT
     og et oppdrag hvis payload er referanser og datoen — aldri
     adressen, aldri teksten.
  3. Policyens frekvens (2 per 30 døgn per mottaker) stopper den tredje
     bestillingen (brudd → unntakskø).
  4. Samtykket trukket ETTER planleggingen: registeret attesterer USANT,
     policyen gir brudd `attestasjon_negativ` — en sak, aldri en stille
     levering.
  5. Målportene FØR kvote: ikke i planen / uten innhold / uten adresse →
     409 `kampanje_ikke_klar_for_levering`; ukjent → 404 `kampanje_ukjent`.
  6. Uten `v_samtykke`-nøkkel mintes ingen attestasjon → brudd
     `attestasjon_mangler`.

MUTASJONER SOM DREPER DENNE: sløyf målporten (5 blir 200), attester
`samtykke_gyldig` alltid sant (4 blir tillat), eller ta `mottaker_id`
ut av hendelsen (3 blir tillat).
"""
import secrets
import uuid
from datetime import date, timedelta

import pytest
import yaml as _yaml

from .test_api import (DSN, MIGRATOR_DSN, POLICIES, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo, pg)
from .test_m37 import _sett_kontekst
from .test_m44_kampanje import (_grense, _kampanje, _mottaker, _plan, _rt,
                                _samtykke)
from .test_outbox_bestilling import _adminsesjon

I_DAG = date.today().isoformat()
# Samtykket er 130 døgn gammelt uansett når testen kjører — godt innenfor
# riggens vindu på 730 døgn, så «tillat» er stabil over tid.
SAMTYKKEDATO = (date.today() - timedelta(days=130)).isoformat()


def test_bestillingstypen_er_deklarert_og_lukket():
    from api.bestilling import BESTILLINGSTYPER, Bestillingsfeil, normaliser
    from oppdragskontrakt import (FELTVERDIER, OPPDRAGSTYPER,
                                  UTFORELSESFRIST_VALG, type_for_handling)
    bt = BESTILLINGSTYPER["kampanje.send"]
    assert bt.eiermodul == "m44_kampanje" and bt.omfang == ("mottaker",)
    assert bt.skjemafelt == frozenset({"bestillingstype", "kampanje_ref",
                                       "mottaker_ref", "omfang"})
    ot = OPPDRAGSTYPER["kampanje.send"]
    assert ot.eiermodul == "m44_kampanje"
    assert ot.paakrevde == frozenset({"kampanje_id", "mottaker_id", "omfang",
                                      "planlagt_sendt"})
    assert type_for_handling("kampanje.send").navn == "kampanje.send"
    assert FELTVERDIER["kampanje.send"]["omfang"] == ("mottaker",)
    assert UTFORELSESFRIST_VALG["kampanje.send"] == ("omfang",
                                                      {"mottaker": 15 * 60})
    k, m = str(uuid.uuid4()), str(uuid.uuid4())
    ok = normaliser("t", {"bestillingstype": "kampanje.send",
                          "kampanje_ref": "kampanje:" + k,
                          "mottaker_ref": "mottaker:" + m,
                          "omfang": "mottaker"})
    assert ok == {"tenant": "t", "bestillingstype": "kampanje.send",
                  "kampanje_id": k, "mottaker_id": m, "omfang": "mottaker"}
    for kropp in ({"bestillingstype": "kampanje.send",
                   "kampanje_ref": "kampanje:" + k, "omfang": "mottaker"},
                  {"bestillingstype": "kampanje.send",
                   "kampanje_ref": "kampanje:" + k,
                   "mottaker_ref": "mottaker:" + m, "omfang": "alle"},
                  {"bestillingstype": "kampanje.send",
                   "kampanje_ref": "kampanje:" + k,
                   "mottaker_ref": "mottaker:" + m, "omfang": "mottaker",
                   "planlagt_sendt": I_DAG},
                  {"bestillingstype": "kampanje.send",
                   "kampanje_ref": "fordring:" + k,
                   "mottaker_ref": "mottaker:" + m, "omfang": "mottaker"}):
        with pytest.raises(Bestillingsfeil):
            normaliser("t", kropp)


# ---------------------------------------------------------------------------
# Riggen
# ---------------------------------------------------------------------------

def _kampanjepolicy(m, *, tillatt_for=("agent",)):
    """Bransjemalen + utvidelsen `kampanje-send.yaml` (verifikator og
    handling) + rollen bestiller, registrert for testtenanten."""
    from api import policyregister
    p = _yaml.safe_load((POLICIES / "bransjemal-tjenestebedrift.yaml")
                        .read_text(encoding="utf-8"))
    utv = _yaml.safe_load((POLICIES / "utvidelser" / "kampanje-send.yaml")
                          .read_text(encoding="utf-8"))
    if not any(r.get("id") == "bestiller" for r in p["roller"]):
        p["roller"].append({"id": "bestiller",
                            "beskrivelse": "Bestiller kampanjer"})
    p.setdefault("verifikatorer", {}).update(utv["verifikatorer"])
    for h in utv["handlinger"]:
        h = dict(h); h["tillatt_for"] = list(tillatt_for)
        p["handlinger"].append(h)
    policyregister.registrer(m, TENANT, p, p["meta"]["status"])
    m.commit()
    _sikre_m44_claimbar(m)


def _sikre_m44_claimbar(m):
    from miljo import gjeldende_miljo
    mv = gjeldende_miljo()
    m.execute("INSERT INTO modulhode (modul_id,status)"
              " VALUES ('m44_kampanje','aktiv') ON CONFLICT DO NOTHING")
    m.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,"
        "kontrakt_hash,payload_schema_hash,kvittering_schema_hash,"
        "sideeffektklasse,reversibilitet)"
        " VALUES ('m44_kampanje',1,%s,'p','k','krever_outbox',"
        "'kompenserende') ON CONFLICT DO NOTHING",
        ("k-" + secrets.token_hex(8),))
    khash = m.execute(
        "SELECT kontrakt_hash FROM modulkontrakt"
        " WHERE modul_id='m44_kampanje' AND kontraktversjon=1").fetchone()[0]
    if m.execute("SELECT 1 FROM oppdragstype_register"
                 " WHERE oppdragstype='kampanje.send'").fetchone() is None:
        m.execute(
            "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
            "kontraktversjon,kontrakt_hash)"
            " VALUES ('kampanje.send','m44_kampanje',1,%s)", (khash,))
    if m.execute(
            "SELECT 1 FROM moduldeployment WHERE modul_id='m44_kampanje'"
            " AND miljo=%s AND livslop='claiming'", (mv,)).fetchone() is None:
        rel = f"r44-{secrets.token_hex(6)}"
        m.execute(
            "INSERT INTO modulrelease (modul_id,release_id,"
            "kontraktversjon,kontrakt_hash,manifest_hash,artifact_digest)"
            " VALUES ('m44_kampanje',%s,1,%s,'mh','ad')", (rel, khash))
        m.execute(
            "INSERT INTO moduldeployment (modul_id,release_id,"
            "kontraktversjon,kontrakt_hash,miljo,livslop)"
            " VALUES ('m44_kampanje',%s,1,%s,%s,'claiming')",
            (rel, khash, mv))
    m.commit()


def _kontakt(c, tenant, mid):
    _sett_kontekst(c, tenant)
    c.execute("SELECT m44_sett_kontakt(%s,%s,%s,%s,%s,%s)",
              (tenant, mid, b"\x01" * 24, b"\x02" * 12, "k1", "u-test"))
    c.commit()


def _innhold(c, tenant, kid):
    _sett_kontekst(c, tenant)
    c.execute("SELECT m44_sett_innhold(%s,%s,%s,%s,%s)",
              (tenant, kid, "Høstsjekk", "Hei {navn}, vi tilbyr høstsjekk.",
               "u-test"))
    c.commit()


def _klar(*, med_kontakt=True, med_innhold=True, i_plan=True,
          samtykke=("bekreftet", SAMTYKKEDATO), dato=I_DAG, mid=None):
    """Grense (5 per 7 d, gyldig 730 d) + mottaker + samtykke + kampanje
    med innhold + plan → (kampanje_id, mottaker_id)."""
    c = _rt()
    try:
        _grense(c, TENANT, maks=5, periode=7, gyldig=730)
        if mid is None:
            mid, _ = _mottaker(c, TENANT)
            if samtykke:
                _samtykke(c, TENANT, mid, samtykke[0], samtykke[1])
            if med_kontakt:
                _kontakt(c, TENANT, mid)
        kid = _kampanje(c, TENANT, dato=dato)
        if med_innhold:
            _innhold(c, TENANT, kid)
        if i_plan:
            _plan(c, TENANT, kid, mid)
    finally:
        c.close()
    return kid, mid


def _bestill(klient, cookie, csrf, kid, mid, idem=None):
    from api import sesjon as sesjonmodul
    return klient.post("/v1/bestilling",
                       json={"bestillingstype": "kampanje.send",
                             "kampanje_ref": f"kampanje:{kid}",
                             "mottaker_ref": f"mottaker:{mid}",
                             "omfang": "mottaker"},
                       cookies={sesjonmodul.C_SESJON: cookie},
                       headers={"X-Disponit-CSRF": csrf,
                                "Idempotency-Key":
                                    idem or "ka-" + secrets.token_hex(8)})


def _payload(migrator, oid):
    from db import kryptering
    _sett_kontekst(migrator, TENANT)
    prad = migrator.execute(
        "SELECT payload_kryptert, key_id, nonce FROM oppdrag"
        " WHERE tenant=%s AND id=%s", (TENANT, oid)).fetchone()
    nok = migrator.execute(
        "SELECT wrapped_dek FROM tenant_nokler WHERE tenant=%s AND"
        " key_id=%s", (TENANT, prad[1])).fetchone()[0]
    migrator.rollback()
    dek = kryptering._pakk_ut((prad[1], nok), TENANT)[1]
    return kryptering.dekrypter(dek, bytes(prad[0]), bytes(prad[2]),
                                TENANT, prad[1])


# ---------------------------------------------------------------------------
# Portene
# ---------------------------------------------------------------------------

@pg
def test_klar_mottaker_gir_tillat_og_et_oppdrag_uten_adresse_og_tekst(
        klient, migrator, miljo):
    _kampanjepolicy(migrator, tillatt_for=("agent", "bestiller"))
    kid, mid = _klar()
    cookie, csrf = _adminsesjon()
    r = _bestill(klient, cookie, csrf, kid, mid)
    assert r.status_code == 200, r.text
    assert r.json()["beslutning"] == "tillat", r.text
    oid = r.json()["oppdrag_id"]
    payload = _payload(migrator, oid)
    assert payload == {"kampanje_id": str(kid), "mottaker_id": str(mid),
                       "planlagt_sendt": I_DAG, "omfang": "mottaker"}
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT oppdragstype, handling, eiermodul FROM oppdrag"
        " WHERE tenant=%s AND id=%s", (TENANT, oid)).fetchone()
    migrator.rollback()
    assert rad == ("kampanje.send", "kampanje.send", "m44_kampanje")


@pg
def test_policyens_frekvens_stopper_den_tredje(klient, migrator, miljo):
    _kampanjepolicy(migrator, tillatt_for=("agent", "bestiller"))
    kid1, mid = _klar()
    cookie, csrf = _adminsesjon()
    assert _bestill(klient, cookie, csrf, kid1, mid).json()["beslutning"] \
        == "tillat"
    kid2, _ = _klar(mid=mid)
    assert _bestill(klient, cookie, csrf, kid2, mid).json()["beslutning"] \
        == "tillat"
    kid3, _ = _klar(mid=mid)
    r = _bestill(klient, cookie, csrf, kid3, mid)
    assert r.status_code == 200 and r.json()["beslutning"] == "brudd", r.text
    assert any(k.startswith("frekvensgrense") for k in
               r.json()["begrunnelse"]), r.text
    assert r.json()["unntak_id"]


@pg
def test_trukket_samtykke_etter_planleggingen_er_en_sak(klient, migrator,
                                                       miljo):
    _kampanjepolicy(migrator, tillatt_for=("agent", "bestiller"))
    kid, mid = _klar()
    c = _rt()
    try:
        _samtykke(c, TENANT, mid, "trukket", I_DAG)   # etter planen
    finally:
        c.close()
    cookie, csrf = _adminsesjon()
    r = _bestill(klient, cookie, csrf, kid, mid)
    assert r.status_code == 200 and r.json()["beslutning"] == "brudd", r.text
    assert "attestasjon_negativ" in r.json()["begrunnelse"], r.text
    assert not r.json().get("oppdrag_id")


@pg
@dekker("kampanje_ikke_klar_for_levering")
def test_malportene_stopper_for_kvote(klient, migrator, miljo):
    _kampanjepolicy(migrator, tillatt_for=("agent", "bestiller"))
    cookie, csrf = _adminsesjon()
    for lag, grunn in ((dict(i_plan=False), "ikke_i_planen"),
                       (dict(med_innhold=False), "uten_innhold"),
                       (dict(med_kontakt=False), "uten_adresse")):
        kid, mid = _klar(**lag)
        r = _bestill(klient, cookie, csrf, kid, mid)
        assert r.status_code == 409, (grunn, r.text)
        assert r.json()["feil"] == "kampanje_ikke_klar_for_levering", grunn
    # Avlyst kampanje → 409, og ingen beslutning er tatt for noen av dem.
    kid, mid = _klar()
    c = _rt()
    try:
        _sett_kontekst(c, TENANT)
        c.execute("SELECT m44_avlys_kampanje(%s,%s,%s)", (TENANT, kid, "u"))
        c.commit()
    finally:
        c.close()
    r = _bestill(klient, cookie, csrf, kid, mid)
    assert r.status_code == 409, r.text
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND handling='kampanje.send'", (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert n == 0


@pg
@dekker("kampanje_ukjent")
def test_ukjent_kampanje_eller_mottaker_er_404(klient, migrator, miljo):
    _kampanjepolicy(migrator, tillatt_for=("agent", "bestiller"))
    cookie, csrf = _adminsesjon()
    kid, mid = _klar()
    r = _bestill(klient, cookie, csrf, uuid.uuid4(), mid)
    assert r.status_code == 404 and r.json()["feil"] == "kampanje_ukjent"
    r = _bestill(klient, cookie, csrf, kid, uuid.uuid4())
    assert r.status_code == 404 and r.json()["feil"] == "kampanje_ukjent"


@pg
def test_uten_v_samtykke_nokkel_tas_vilkaarsveien(klient, migrator, miljo,
                                                  app, monkeypatch):
    _kampanjepolicy(migrator, tillatt_for=("agent", "bestiller"))
    kid, mid = _klar()
    monkeypatch.delitem(app.tjeneste.nokler, "v_samtykke")
    cookie, csrf = _adminsesjon()
    r = _bestill(klient, cookie, csrf, kid, mid)
    assert r.status_code == 200 and r.json()["beslutning"] == "brudd", r.text
    assert "attestasjon_mangler" in r.json()["begrunnelse"], r.text
