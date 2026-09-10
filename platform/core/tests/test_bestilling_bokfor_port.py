"""Porten for ARC B bokføring, PR 1: bestillingstypene `faktura.bokfor`
og `faktura.bokfor_stor`, og registerets kontrollrader som vilkår.

Én kontrollert inngående faktura → ett bilag. Det som måles, mot ekte
base gjennom HTTP-døra, med bransjemalen som den ER — de to handlingene
har stått der merket `auto` siden M-1 uten å fyre én gang
(KLYNGE4-FUNDAMENT):

  1. Typene er deklarert og LUKKET: én referanse og ett omfang — ingen
     beløp, ingen leverandør i kroppen.
  2. En faktura med tre rene kontroller under den lille grensen →
     TILLAT og et oppdrag hvis payload er referansen og det bilaget
     trenger — aldri mer.
  3. Et mva-avvik → brudd `attestasjon_negativ` (mva_validert usant) →
     sak. Registerets kontrollrad er vilkåret; API-et regner ikke selv.
  4. Ukjent leverandør → brudd (leverandor_i_register usant).
  5. Beløp over den lille grensen bestilt som `faktura.bokfor` →
     `belop_over_grense`; som `faktura.bokfor_stor` med manuell kontroll
     → TILLAT. Policyen måler beløpet, ikke bestilleren.
  6. Målportene FØR kvote: over tenantens beløpsgrense uten manuell
     kontroll → 409 `faktura_ikke_klar_for_bokforing`; avvist → 409;
     ukjent faktura → 404 `faktura_ukjent`.
  7. Uten `v_register`-nøkkel mintes ingen attestasjon → brudd
     `attestasjon_mangler`.

MUTASJONER SOM DREPER DENNE: attester `mva_validert` alltid sant (3),
la målporten slippe en avvist faktura (6), eller sløyf
`leverandor_i_register` (4).
"""
import secrets
import uuid

import pytest
import yaml as _yaml

from .test_api import (DSN, MIGRATOR_DSN, POLICIES, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo, pg, token)
from .test_bestilling_kampanje_port import _payload
from .test_m14_faktura import _faktura, _sats, _terskler
from .test_m24_leverandor import _part
from .test_m37 import _sett_kontekst

MODUL = "m14_fakturakontroll"


def test_bestillingstypene_er_deklarert_og_lukket():
    from api.bestilling import BESTILLINGSTYPER, Bestillingsfeil, normaliser
    from oppdragskontrakt import (FELTVERDIER, OPPDRAGSTYPER,
                                  UTFORELSESFRIST_VALG, type_for_handling)
    for navn in ("faktura.bokfor", "faktura.bokfor_stor"):
        bt = BESTILLINGSTYPER[navn]
        assert bt.eiermodul == MODUL and bt.omfang == ("bilag",)
        assert bt.skjemafelt == frozenset({"bestillingstype", "faktura_ref",
                                           "omfang"})
        ot = OPPDRAGSTYPER[navn]
        assert ot.paakrevde == frozenset({"faktura_id", "fakturanummer",
                                          "leverandor_ref", "brutto_ore",
                                          "omfang"})
        assert type_for_handling(navn).navn == navn
        assert FELTVERDIER[navn]["omfang"] == ("bilag",)
        assert UTFORELSESFRIST_VALG[navn] == ("omfang", {"bilag": 15 * 60})
    fid = str(uuid.uuid4())
    n = normaliser(TENANT, {"bestillingstype": "faktura.bokfor",
                            "faktura_ref": f"faktura:{fid}",
                            "omfang": "bilag"})
    assert n == {"tenant": TENANT, "bestillingstype": "faktura.bokfor",
                 "faktura_id": fid, "omfang": "bilag"}
    for kropp in ({"bestillingstype": "faktura.bokfor",
                   "faktura_ref": f"faktura:{fid}", "omfang": "bilag",
                   "brutto_ore": 100},
                  {"bestillingstype": "faktura.bokfor",
                   "faktura_ref": f"faktura:{fid}", "omfang": "alt"},
                  {"bestillingstype": "faktura.bokfor",
                   "faktura_ref": "faktura:ikke-uuid", "omfang": "bilag"}):
        with pytest.raises(Bestillingsfeil):
            normaliser(TENANT, kropp)


def _bokforpolicy(m, *, tillatt_for=("agent", "bestiller")):
    """Bransjemalen SOM DEN ER — ingen utvidelse: `faktura.bokfor` og
    `faktura.bokfor_stor` har stått der siden M-1. Portene som måler
    vilkårene over HTTP åpner handlingene for `bestiller` (et Bearer-
    token er aldri agenten); porten for rollen bruker malen urørt."""
    from api import policyregister
    p = _yaml.safe_load((POLICIES / "bransjemal-tjenestebedrift.yaml")
                        .read_text(encoding="utf-8"))
    if not any(r.get("id") == "bestiller" for r in p["roller"]):
        p["roller"].append({"id": "bestiller",
                            "beskrivelse": "Bestiller bokføring"})
    for h in p["handlinger"]:
        if h["id"] in ("faktura.bokfor", "faktura.bokfor_stor"):
            h["tillatt_for"] = list(tillatt_for)
    policyregister.registrer(m, TENANT, p, p["meta"]["status"])
    m.commit()
    _sikre_m14_claimbar(m)


def _sikre_m14_claimbar(m):
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
    for ot in ("faktura.bokfor", "faktura.bokfor_stor"):
        if m.execute("SELECT 1 FROM oppdragstype_register"
                     " WHERE oppdragstype=%s", (ot,)).fetchone() is None:
            m.execute(
                "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
                "kontraktversjon,kontrakt_hash) VALUES (%s,%s,1,%s)",
                (ot, MODUL, khash))
    if m.execute(
            f"SELECT 1 FROM moduldeployment WHERE modul_id='{MODUL}'"
            " AND miljo=%s AND livslop='claiming'", (mv,)).fetchone() is None:
        rel = f"r14-{secrets.token_hex(6)}"
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


def _rigg(migrator, *, belopsgrense=2500000):
    """Terskler, mva-sats (25 %) og en kjent leverandør — gjennom
    runtime-rollens dører, som registeret selv."""
    from db.pg import koble
    c = koble(DSN)
    try:
        _terskler(c, TENANT, grense=belopsgrense)
        _sats(c, TENANT)
        _part(c, TENANT, navn="Nordisk Drift AS")
    finally:
        c.close()


def _manuell(migrator, fid, utfall="ok"):
    from db.pg import koble
    c = koble(DSN)
    try:
        _sett_kontekst(c, TENANT)
        c.execute(
            "SELECT m14_registrer_kontroll(%s,%s,%s,%s,"
            " 'sett av controller', 'u-test')",
            (TENANT, uuid.uuid4(), fid, utfall))
        c.commit()
    finally:
        c.close()


def _fakt(migrator, **kw):
    from db.pg import koble
    c = koble(DSN)
    try:
        return _faktura(c, TENANT, **kw)
    finally:
        c.close()


def _avvis(migrator, fid):
    from db.pg import koble
    c = koble(DSN)
    try:
        _sett_kontekst(c, TENANT)
        c.execute("SELECT m14_avgjor_faktura(%s,%s,'avvist','feil','u-x')",
                  (TENANT, fid))
        c.commit()
    finally:
        c.close()


def _bestill(klient, tok, fid, *, type_="faktura.bokfor"):
    return klient.post("/v1/bestilling",
                       json={"bestillingstype": type_,
                             "faktura_ref": f"faktura:{fid}",
                             "omfang": "bilag"},
                       headers={"authorization": f"Bearer {tok}",
                                "Idempotency-Key":
                                    "fb-" + secrets.token_hex(8)})


def _tok(token):
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "decisions:read"))
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


# ---------------------------------------------------------------------------
# Portene
# ---------------------------------------------------------------------------

@pg
def test_ren_faktura_under_grensen_gir_tillat_og_et_oppdrag_med_referansen(
        klient, migrator, token):
    _bokforpolicy(migrator); _rigg(migrator)
    fid = _fakt(migrator, netto=1000000, mva=250000)
    r = _bestill(klient, _tok(token), fid)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["beslutning"] == "tillat" and d["oppdrag_id"], d
    p = _payload(migrator, d["oppdrag_id"])
    assert p["faktura_id"] == str(fid) and p["omfang"] == "bilag"
    assert p["brutto_ore"] == 1250000
    assert p["leverandor_ref"] == "Nordisk Drift AS"
    assert set(p) == {"faktura_id", "fakturanummer", "leverandor_ref",
                      "brutto_ore", "omfang"}, p


@pg
def test_mva_avvik_er_en_usann_attestasjon_og_en_sak(klient, migrator,
                                                     token):
    _bokforpolicy(migrator); _rigg(migrator)
    fid = _fakt(migrator, netto=1000000, mva=200000)  # 20 %
    r = _bestill(klient, _tok(token), fid)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["beslutning"] == "brudd" and d["unntak_id"], d
    assert "attestasjon_negativ:mva_validert" in _sak(migrator, d["unntak_id"])


@pg
def test_ukjent_leverandor_er_en_usann_attestasjon(klient, migrator, token):
    _bokforpolicy(migrator); _rigg(migrator)
    fid = _fakt(migrator, ref="Ukjent Kabel AS",
                   netto=1000000, mva=250000)
    d = _bestill(klient, _tok(token), fid).json()
    assert d["beslutning"] == "brudd", d
    assert "attestasjon_negativ:leverandor_i_register" in \
        _sak(migrator, d["unntak_id"])


@pg
def test_belopet_maales_av_policyen_ikke_av_bestilleren(klient, migrator,
                                                        token):
    """30 000 brutto: som `faktura.bokfor` → belop_over_grense; som
    `faktura.bokfor_stor` med manuell kontroll → tillat."""
    _bokforpolicy(migrator); _rigg(migrator)
    fid = _fakt(migrator, netto=2400000, mva=600000)
    _manuell(migrator, fid)
    d = _bestill(klient, _tok(token), fid).json()
    assert d["beslutning"] == "brudd", d
    assert any(k.startswith("belop_over_grense") for k in
               _sak(migrator, d["unntak_id"]))
    d2 = _bestill(klient, _tok(token), fid,
                  type_="faktura.bokfor_stor").json()
    assert d2["beslutning"] == "tillat" and d2["oppdrag_id"], d2


@pg
@dekker("faktura_ikke_klar_for_bokforing", "faktura_ukjent")
def test_maalportene_stopper_foer_kvote(klient, migrator, token):
    _bokforpolicy(migrator); _rigg(migrator)
    tok = _tok(token)
    # Over tenantens beløpsgrense uten manuell kontroll.
    stor = _fakt(migrator, netto=2400000, mva=600000)
    r = _bestill(klient, tok, stor, type_="faktura.bokfor_stor")
    assert r.status_code == 409 and \
        r.json()["feil"] == "faktura_ikke_klar_for_bokforing", r.text
    # Avvist.
    avvist = _fakt(migrator, netto=1000000, mva=250000)
    _avvis(migrator, avvist)
    r = _bestill(klient, tok, avvist)
    assert r.status_code == 409 and \
        r.json()["feil"] == "faktura_ikke_klar_for_bokforing", r.text
    # Ukjent.
    r = _bestill(klient, tok, uuid.uuid4())
    assert r.status_code == 404 and r.json()["feil"] == "faktura_ukjent"


@pg
def test_malen_uroert_gir_bare_agenten_bokforingen(klient, migrator, token):
    """Bransjemalen som den ER: `tillatt_for: [agent]`. Et menneske (og
    et Bearer-token) som bestiller får `rolle_ikke_tillatt` — utløserens
    runde (PR 2) er den som bestiller som agent."""
    _bokforpolicy(migrator, tillatt_for=("agent",)); _rigg(migrator)
    ren = _fakt(migrator, netto=1000000, mva=250000)
    d = _bestill(klient, _tok(token), ren).json()
    assert d["beslutning"] == "brudd" and \
        "rolle_ikke_tillatt:" in _sak(migrator, d["unntak_id"]), d


@pg
def test_uten_registernokkel_mintes_ingen_attestasjon(klient, migrator, token,
                                                      app, monkeypatch):
    _bokforpolicy(migrator); _rigg(migrator)
    fid = _fakt(migrator, netto=1000000, mva=250000)
    monkeypatch.delitem(app.tjeneste.nokler, "v_register")
    d = _bestill(klient, _tok(token), fid).json()
    assert d["beslutning"] == "brudd", d
    assert any(k.startswith("attestasjon_mangler") for k in
               _sak(migrator, d["unntak_id"])), _sak(migrator, d["unntak_id"])
