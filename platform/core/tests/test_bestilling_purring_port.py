"""Porten for ARC B, PR 2: bestillingstypen `purring.send` og
verifikatoren `v_fordring`.

Eiervedtaket (8/9): purring er selvbetjening — en bestiller ber om
purring på ÉN fordring, policyen avgjør, og trinnet er dørens (104:
«neste trinn»), aldri bestillerens. Det som måles her, mot ekte base
gjennom HTTP-døra:

  1. Bestillingstypen er deklarert og LUKKET (speiler M-35-porten).
  2. En moden fordring (20 døgn over forfall, purreplan satt) gir TILLAT
     og et oppdrag hvis payload bærer referanser og tall — aldri adressen.
  3. Samme faktura innen 14 dager: frekvensgrensen fra bransjemalen
     stopper den andre bestillingen (BRUDD → unntakskø), uansett hvor mange
     idempotensnøkler bestilleren finner på.
  4. Fem døgn over forfall: døra finner et neste trinn (påminnelse, 3
     døgn), men attestasjonen sier 5 og policyen krever 14 — unntak.
  5. Betalt fordring: 409 `fordring_ikke_klar_for_purring`, FØR kvote.
  6. Ukjent fordring: 404 `fordring_ukjent`, FØR kvote.
  7. Uten `v_fordring`-nøkkel i registeret mintes ingen attestasjon, og
     beslutningen tar vilkårsveien — den blir aldri TILLAT på et vilkår
     ingen har bevitnet.

MUTASJONER SOM DREPER DENNE: sløyf målporten i `bestilling.py` (5 og 6
blir 200), sett `resultat` uten `verdi` i attestasjonen (4 blir tillat),
eller la `faktura_id` falle ut av hendelsen (3 blir tillat).
"""
import secrets
import uuid

import pytest
import yaml as _yaml

from .test_api import (DSN, MIGRATOR_DSN, POLICIES, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo, pg)
from .test_m23_fordring import _betal, _fordring, _plan, _rt
from .test_m37 import _sett_kontekst
from .test_outbox_bestilling import _adminsesjon


def test_bestillingstypen_er_deklarert_og_lukket():
    """Kroppen er referansen og omfanget — ikke trinnet, ikke adressen,
    ikke beløpet. Alt det er basens (147) og døras (104)."""
    from api.bestilling import BESTILLINGSTYPER, Bestillingsfeil, normaliser
    from oppdragskontrakt import (FELTGRENSER, FELTVERDIER, OPPDRAGSTYPER,
                                  UTFORELSESFRIST_VALG, type_for_handling)
    bt = BESTILLINGSTYPER["purring.send"]
    assert bt.eiermodul == "m23_fordring"
    assert bt.omfang == ("trinn",)
    assert bt.skjemafelt == frozenset({"bestillingstype", "fordring_ref",
                                       "omfang"})
    assert bt.intensjonsfelt == ("tenant", "bestillingstype",
                                 "fordring_id", "omfang")

    ot = OPPDRAGSTYPER["purring.send"]
    assert ot.eiermodul == "m23_fordring"
    assert ot.paakrevde == frozenset({"fordring_id", "fakturanummer",
                                      "trinn", "handling_trinn",
                                      "rest_ore", "omfang"})
    assert ot.handlingsprefikser == ("purring.send",)
    assert type_for_handling("purring.send").navn == "purring.send"
    # Trinnhandlingene er de tre bransjemalen kjenner — «inkasso» er
    # IKKE en av dem: den sendes aldri automatisk (eiervedtaket).
    assert FELTVERDIER["purring.send"]["handling_trinn"] == (
        "paaminnelse", "purring", "inkassovarsel")
    assert FELTGRENSER["purring.send"]["trinn"] == (1, 20)
    assert UTFORELSESFRIST_VALG["purring.send"] == ("omfang",
                                                     {"trinn": 15 * 60})

    fid = str(uuid.uuid4())
    ok = normaliser("t", {"bestillingstype": "purring.send",
                          "fordring_ref": "fordring:" + fid,
                          "omfang": "trinn"})
    assert ok == {"tenant": "t", "bestillingstype": "purring.send",
                  "fordring_id": fid, "omfang": "trinn"}
    for kropp in ({"bestillingstype": "purring.send", "omfang": "trinn"},
                  {"bestillingstype": "purring.send",
                   "fordring_ref": "fordring:" + fid},
                  {"bestillingstype": "purring.send",
                   "fordring_ref": "fordring:" + fid, "omfang": "alle"},
                  {"bestillingstype": "purring.send",
                   "fordring_ref": "faktura:" + fid, "omfang": "trinn"},
                  {"bestillingstype": "purring.send",
                   "fordring_ref": "fordring:ikke-en-uuid",
                   "omfang": "trinn"},
                  {"bestillingstype": "purring.send",
                   "fordring_ref": "fordring:" + fid, "omfang": "trinn",
                   "trinn": 3}):
        with pytest.raises(Bestillingsfeil):
            normaliser("t", kropp)


# ---------------------------------------------------------------------------
# Riggen
# ---------------------------------------------------------------------------

def _purring_policy(m, *, tillatt_for=("bestiller",)):
    """Bransjemalen SOM DEN ER — `purring.send` står der fra første dag
    med frekvens 1/14 d per faktura og vilkårene fra `v_fordring`. Det
    eneste som legges til er rollen bestilleren bærer i
    `EvaluationContext` (samme grep som `_rekr_policy`)."""
    from api import policyregister
    p = _yaml.safe_load(
        (POLICIES / "bransjemal-tjenestebedrift.yaml")
        .read_text(encoding="utf-8"))
    if not any(r.get("id") == "bestiller" for r in p["roller"]):
        p["roller"].append({"id": "bestiller",
                            "beskrivelse": "Bestiller purringer"})
    h = next(h for h in p["handlinger"] if h["id"] == "purring.send")
    h["tillatt_for"] = sorted(set(h["tillatt_for"]) | set(tillatt_for))
    policyregister.registrer(m, TENANT, p, p["meta"]["status"])
    m.commit()
    _sikre_m23_claimbar(m)
    return p


def _sikre_m23_claimbar(m):
    """Claim-vaktens vilkår for `purring.send`: registerrad med rett eier,
    aktivt modulhode og en claiming-deployment i DETTE miljøet —
    idempotent, speilet fra `_sikre_m57_claimbar`."""
    from miljo import gjeldende_miljo
    mv = gjeldende_miljo()
    m.execute("INSERT INTO modulhode (modul_id,status)"
              " VALUES ('m23_fordring','aktiv') ON CONFLICT DO NOTHING")
    m.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,"
        "kontrakt_hash,payload_schema_hash,kvittering_schema_hash,"
        "sideeffektklasse,reversibilitet)"
        " VALUES ('m23_fordring',1,%s,'p','k','krever_outbox',"
        "'kompenserende') ON CONFLICT DO NOTHING",
        ("k-" + secrets.token_hex(8),))
    khash = m.execute(
        "SELECT kontrakt_hash FROM modulkontrakt"
        " WHERE modul_id='m23_fordring' AND kontraktversjon=1").fetchone()[0]
    reg = m.execute(
        "SELECT eiermodul FROM oppdragstype_register"
        " WHERE oppdragstype='purring.send'").fetchone()
    if reg is None:
        m.execute(
            "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
            "kontraktversjon,kontrakt_hash)"
            " VALUES ('purring.send','m23_fordring',1,%s)", (khash,))
    rad = m.execute(
        "SELECT release_id FROM moduldeployment"
        " WHERE modul_id='m23_fordring' AND miljo=%s AND livslop='claiming'"
        " LIMIT 1", (mv,)).fetchone()
    if rad is None:
        rel = f"r23-{secrets.token_hex(6)}"
        m.execute(
            "INSERT INTO modulrelease (modul_id,release_id,"
            "kontraktversjon,kontrakt_hash,manifest_hash,artifact_digest)"
            " VALUES ('m23_fordring',%s,1,%s,'mh','ad')", (rel, khash))
        m.execute(
            "INSERT INTO moduldeployment (modul_id,release_id,"
            "kontraktversjon,kontrakt_hash,miljo,livslop)"
            " VALUES ('m23_fordring',%s,1,%s,%s,'claiming')",
            (rel, khash, mv))
    m.commit()


def _med_rt(fn, *a, **kw):
    """M-23s dører kjøres som RUNTIME-rollen (den som har EXECUTE), ikke
    som migrator — samme grep som `test_m23_fordring` selv."""
    c = _rt()
    try:
        return fn(c, *a, **kw)
    finally:
        c.close()


def _bestill(klient, cookie, csrf, fid, idem=None):
    from api import sesjon as sesjonmodul
    return klient.post("/v1/bestilling",
                       json={"bestillingstype": "purring.send",
                             "fordring_ref": f"fordring:{fid}",
                             "omfang": "trinn"},
                       cookies={sesjonmodul.C_SESJON: cookie},
                       headers={"X-Disponit-CSRF": csrf,
                                "Idempotency-Key":
                                    idem or "pu-" + secrets.token_hex(8)})


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
def test_moden_fordring_gir_tillat_og_et_oppdrag_uten_adresse(
        klient, migrator, miljo):
    _purring_policy(migrator)
    _med_rt(_plan, TENANT)
    fid = _med_rt(_fordring, TENANT, belop=250000, forfall_siden=20,
                    nummer="F-" + secrets.token_hex(4))
    cookie, csrf = _adminsesjon()

    r = _bestill(klient, cookie, csrf, fid)
    assert r.status_code == 200, r.text
    assert r.json()["beslutning"] == "tillat", r.text
    oid = r.json()["oppdrag_id"]
    assert isinstance(oid, int)

    payload = _payload(migrator, oid)
    # Trinnet er DØRAS: fordringen står på 0, neste er 1 = påminnelse.
    assert payload["fordring_id"] == str(fid)
    assert payload["trinn"] == 1
    assert payload["handling_trinn"] == "paaminnelse"
    assert payload["rest_ore"] == 250000
    assert payload["omfang"] == "trinn"
    assert payload["fakturanummer"].startswith("F-")
    # …og ALDRI adressen, i noen form.
    assert not {k for k in payload if "mottaker" in k or "epost" in k}, \
        payload


@pg
def test_samme_faktura_innen_14_dager_stoppes_av_frekvensgrensen(
        klient, migrator, miljo):
    _purring_policy(migrator)
    _med_rt(_plan, TENANT)
    fid = _med_rt(_fordring, TENANT, forfall_siden=20)
    cookie, csrf = _adminsesjon()
    r1 = _bestill(klient, cookie, csrf, fid)
    assert r1.status_code == 200 and r1.json()["beslutning"] == "tillat", \
        r1.text
    # Ny idempotensnøkkel, samme faktura: frekvensen teller per
    # `faktura_id` (bransjemalen), ikke per nøkkel.
    r2 = _bestill(klient, cookie, csrf, fid)
    assert r2.status_code == 200, r2.text
    # `ved_brudd: unntakskø` → beslutningen er BRUDD med en unntak_id;
    # begrunnelsen navngir frekvensgrensen.
    assert r2.json()["beslutning"] == "brudd", r2.text
    assert r2.json()["unntak_id"], r2.text
    assert any(k.startswith("frekvensgrense") for k in
               r2.json()["begrunnelse"]), r2.text


@pg
def test_fem_dogn_over_forfall_er_under_policyens_minimum(
        klient, migrator, miljo):
    """Døra finner et neste trinn (påminnelse ved 3 døgn) — men policyen
    krever 14 døgn, og attestasjonen bærer BASENS tall (5). Vilkåret
    faller, og beslutningen går unntaksveien. Det er nettopp skillet
    mellom «modulen er klar» og «policyen tillater»."""
    _purring_policy(migrator)
    _med_rt(_plan, TENANT)
    fid = _med_rt(_fordring, TENANT, forfall_siden=5)
    cookie, csrf = _adminsesjon()
    r = _bestill(klient, cookie, csrf, fid)
    assert r.status_code == 200, r.text
    assert r.json()["beslutning"] == "brudd", r.text
    assert r.json()["unntak_id"], r.text
    assert "attestasjon_under_terskel" in r.json()["begrunnelse"], r.text
    assert not r.json().get("oppdrag_id"), r.text


def _spor(migrator):
    """Hvor mange spor purringen har satt: oppdrag og unntakssaker for
    handlingen. En 4xx FØR beslutningen skal ikke flytte tallet."""
    _sett_kontekst(migrator, TENANT)
    o = migrator.execute(
        "SELECT count(*) FROM oppdrag WHERE tenant=%s"
        " AND handling='purring.send'", (TENANT,)).fetchone()[0]
    u = migrator.execute(
        "SELECT count(*) FROM unntak WHERE tenant=%s"
        " AND handling='purring.send'", (TENANT,)).fetchone()[0]
    migrator.rollback()
    return (o, u)


@pg
@dekker("fordring_ikke_klar_for_purring")
def test_betalt_fordring_er_409_for_beslutningen(klient, migrator, miljo):
    _purring_policy(migrator)
    _med_rt(_plan, TENANT)
    fid = _med_rt(_fordring, TENANT, belop=100000, forfall_siden=20)
    _med_rt(_betal, TENANT, fid, 100000)
    cookie, csrf = _adminsesjon()
    for_ = _spor(migrator)
    r = _bestill(klient, cookie, csrf, fid)
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "fordring_ikke_klar_for_purring"
    # …og ingen kvote brant: ingen beslutning, ingen sak, intet oppdrag.
    assert _spor(migrator) == for_


@pg
@dekker("fordring_ukjent")
def test_ukjent_fordring_er_404_for_beslutningen(klient, migrator, miljo):
    _purring_policy(migrator)
    cookie, csrf = _adminsesjon()
    for_ = _spor(migrator)
    r = _bestill(klient, cookie, csrf, uuid.uuid4())
    assert r.status_code == 404, r.text
    assert r.json()["feil"] == "fordring_ukjent"
    assert _spor(migrator) == for_


@pg
def test_uten_purreplan_er_fordringen_ikke_klar(klient, migrator, miljo):
    """Ingen plan = ingen neste trinn = ingenting å purre med. Døra sier
    det, ikke en tom sending."""
    _purring_policy(migrator)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("DELETE FROM purretrinn WHERE tenant=%s", (TENANT,))
    migrator.commit()
    fid = _med_rt(_fordring, TENANT, forfall_siden=20)
    cookie, csrf = _adminsesjon()
    r = _bestill(klient, cookie, csrf, fid)
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "fordring_ikke_klar_for_purring"


@pg
def test_uten_v_fordring_nokkel_tas_vilkaarsveien(klient, migrator, miljo,
                                                  app, monkeypatch):
    """Uten nøkkel mintes ingen attestasjon. Da mangler vilkårene, og
    motoren kan ikke si TILLAT på noe ingen har bevitnet."""
    _purring_policy(migrator)
    _med_rt(_plan, TENANT)
    fid = _med_rt(_fordring, TENANT, forfall_siden=20)
    monkeypatch.delitem(app.tjeneste.nokler, "v_fordring")
    cookie, csrf = _adminsesjon()
    r = _bestill(klient, cookie, csrf, fid)
    assert r.status_code == 200, r.text
    assert r.json()["beslutning"] == "brudd", r.text
    assert "attestasjon_mangler" in r.json()["begrunnelse"], r.text
    assert not r.json().get("oppdrag_id"), r.text
