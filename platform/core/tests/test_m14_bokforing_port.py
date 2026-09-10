"""Porten for ARC B bokføring, PR 4: kvitteringen når registeret —
fakturaen er bokført, bilaget står i M-13.

  1. Hele veien: utløser → oppdrag → modulen kvitterer `utfort` med
     bilaget → fakturaen er `bokfort` med bilagets identitet, bilaget står
     i M-13s register (`ut`, fakturaens brutto, leverandøren som motpart),
     evidensen `faktura.bokfort`. Samme kvittering én gang til er
     idempotent: ett bilag, én evidens. Flaten viser `bokfort`.
  2. `bokfort` er aldri en dom: `POST …/avgjor {bokfort}` → 400, og døra
     nekter en avvist faktura.
  3. Bilaget er en AVSKRIFT: en kvittering med et annet beløp enn
     fakturaens bokføres ikke (døra nekter, avviket i driftsloggen).
  4. En kvittering for en annen faktura / uten gyldig ressurs aksepteres,
     men bokføres ikke.
  5. En brudd-sak fra utløseren (mva-avvik) bærer referansen og tallene
     i sakspayloaden, og M-37s R1 planlegger et KOMPLETT oppdrag av den.
  6. En faktura et menneske alt har `kontrollert` bokføres med
     menneskets avgjørelse intakt.

MUTASJONER SOM DREPER DENNE: fjern kroken i `_ingest_kvittering` (1),
sløyf beløpssammenligningen i døra (3), sløyf ressurssammenligningen i
`bokfor_faktura_bokfort` (4).
"""
import uuid

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_bestilling_bokfor_port import _bokforpolicy, _fakt, _rigg, _tok
from .test_m17_avsender_port import _post
from .test_m14_bilag_port import (MODUL, _bestilt_av_utloseren, _kjor_til,
                                  _oppdrag, _release, _signer)
from .test_m14_bokforingsutloser_port import _pa, _runde
from .test_m37 import _sett_kontekst
from .test_modul_onboarding_http import _onboard_token

PLAN_DSN = __import__("os").environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")


def _tilstand(migrator, fid):
    _sett_kontekst(migrator, TENANT)
    f = migrator.execute(
        "SELECT status, bilag_id, bilagsnummer, bokfort_oppdrag_id,"
        " avgjort_av FROM inngaaende_faktura WHERE tenant=%s AND faktura_id=%s",
        (TENANT, fid)).fetchone()
    b = migrator.execute(
        "SELECT bilagsnummer, retning, belop_ore, motpart, opprettet_av"
        " FROM bilag WHERE tenant=%s AND bilag_id=%s",
        (TENANT, f[1])).fetchall() if f and f[1] else []
    ev = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND kilde='m14_faktura' AND handling='faktura.bokfort'",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    return f, b, ev


def _kvitter_selv(klient, mtk, kropp):
    return klient.post("/v1/oppdrag/kvittering", json=kropp,
                       headers={"authorization": f"Bearer {mtk}"})


@pg_plan
def test_kvitteringen_bokforer_fakturaen_og_bilaget_en_gang(
        migrator, miljo, app, klient, token):
    from modules.m14_fakturakontroll import controller
    controller._sov = lambda s: None
    _bokforpolicy(migrator); _rigg(migrator)
    fid = _fakt(migrator, netto=1000000, mva=250000, nummer="NK-2026-9001")
    oid = _bestilt_av_utloseren(app, fid)
    _, _, ev_for = _tilstand(migrator, fid)
    mtk, _ = _onboard_token(klient, migrator, MODUL, _release(migrator))
    kvitteringer = []

    def signer(kropp):
        signert = _signer(kropp)
        kvitteringer.append(signert)
        return signert
    ut = _kjor_til(klient, mtk, signer, fid)
    assert ut["utfall"] == "utfort", ut
    f, b, ev = _tilstand(migrator, fid)
    assert f[0] == "bokfort" and f[2] == "LF-NK-2026-9001" and f[3] == oid, f
    assert f[4].startswith("token:"), f          # modulens aktør avgjorde
    assert b == [("LF-NK-2026-9001", "ut", 1250000, "Nordisk Drift AS", f[4])]
    assert ev == ev_for + 1
    # Samme kvittering én gang til: idempotent — ett bilag, én evidens.
    r = _kvitter_selv(klient, mtk, kvitteringer[-1])
    assert r.status_code == 200, r.text
    assert _tilstand(migrator, fid) == (f, b, ev_for + 1)
    assert _oppdrag(migrator, oid)[0] == "utfort"
    # Flaten viser bokført (lesingen bærer okonomi:read).
    lesetok, _ = token(rolle="bestiller", scopes=("okonomi:read",))
    r = klient.get("/v1/faktura",
                   headers={"authorization": f"Bearer {lesetok}"})
    mine = [x for x in r.json()["fakturaer"] if x["faktura_id"] == str(fid)]
    assert mine and mine[0]["status"] == "bokfort", mine
    # Ikke kandidat lenger, og en ny runde rører den ikke.
    pa = _pa()
    try:
        res = _runde(app, pa)
    finally:
        pa.close()
    assert not [x for x in res["resultater"] if x["faktura"] == str(fid)]


@pg
def test_bokfort_er_aldri_en_dom(migrator, miljo, klient, token):
    from db.pg import koble
    _bokforpolicy(migrator); _rigg(migrator)
    tok = _tok(token)
    fid = _fakt(migrator, netto=1100000, mva=275000)
    r = _post(klient, tok, f"/v1/faktura/{fid}/avgjor",
              {"status": "bokfort", "begrunnelse": "x"})
    assert r.status_code == 400, r.text
    avvist = _fakt(migrator, netto=1200000, mva=300000)
    from .test_bestilling_bokfor_port import _avvis
    _avvis(migrator, avvist)
    import psycopg
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.Error) as ei:
            rt.execute(
                "SELECT * FROM m14_faktura_bokfort(%s,%s,1,'LF-x','ut',"
                "1500000,'Nordisk Drift AS',current_date,current_date+30,"
                "now(),'bilag-v1','test')", (TENANT, avvist))
        assert "avvist" in str(ei.value)
        rt.rollback()
    finally:
        rt.close()
    assert _tilstand(migrator, fid)[0][0] == "mottatt"


@pg
def test_bilaget_er_en_avskrift_et_annet_belop_bokfores_ikke(migrator, miljo,
                                                              klient, token):
    from db.pg import koble
    _bokforpolicy(migrator); _rigg(migrator)
    fid = _fakt(migrator, netto=1300000, mva=325000)
    import psycopg
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.Error) as ei:
            rt.execute(
                "SELECT * FROM m14_faktura_bokfort(%s,%s,1,'LF-x','ut',"
                "1000000,'Nordisk Drift AS',%s::date,%s::date,now(),"
                "'bilag-v1','test')",
                (TENANT, fid, "2026-08-01", "2026-08-31"))
        assert "avviker" in str(ei.value)
        rt.rollback()
    finally:
        rt.close()
    f, b, _ = _tilstand(migrator, fid)
    assert f[0] == "mottatt" and f[1] is None and b == []


@pg
def test_kvittering_for_en_annen_faktura_bokfores_ikke(migrator, miljo, klient,
                                                       token):
    from api.bokforing import bokfor_faktura_bokfort
    from db.pg import koble
    _bokforpolicy(migrator); _rigg(migrator)
    fid = _fakt(migrator, netto=1400000, mva=350000)
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        for kv, grunn in (
                ({"ressurs_id": "henvendelse:x:y"}, "ressurs_id_ikke_faktura"),
                ({"ressurs_id": "faktura:ikke-uuid"},
                 "kvittering_uten_gyldig_ressurs"),
                ({"ressurs_id": f"faktura:{fid}"}, "oppdrag_ukjent")):
            ut = bokfor_faktura_bokfort(rt, TENANT, 990200, kv, "test")
            assert ut.get("avvik") == grunn, (kv, ut)
        rt.rollback()
    finally:
        rt.close()


@pg_plan
def test_kvittering_som_navngir_en_annen_faktura_enn_oppdragets(
        migrator, miljo, app, klient, token):
    from api.bokforing import bokfor_faktura_bokfort
    from db.pg import koble
    _bokforpolicy(migrator); _rigg(migrator)
    fid = _fakt(migrator, netto=1500000, mva=375000)
    oid = _bestilt_av_utloseren(app, fid)
    annen = _fakt(migrator, netto=1600000, mva=400000)
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        ut = bokfor_faktura_bokfort(
            rt, TENANT, oid, {"ressurs_id": f"faktura:{annen}",
                              "bilagsnummer": "LF-x", "retning": "ut",
                              "belop_ore": 2000000, "motpart": "Nordisk Drift AS",
                              "utstedt": "2026-08-01"}, "test")
        rt.rollback()
    finally:
        rt.close()
    assert ut == {"avvik": "ressurs_avvik"}, ut
    assert _tilstand(migrator, fid)[0][0] == "mottatt"
    assert _tilstand(migrator, annen)[0][0] == "mottatt"


@pg_plan
def test_brudd_saken_kan_bli_et_komplett_oppdrag(migrator, miljo, app,
                                                  klient, token):
    from db import kryptering
    from m37 import reparasjoner
    _bokforpolicy(migrator); _rigg(migrator)
    fid = _fakt(migrator, netto=1700000, mva=340000)      # 20 %: mva-avvik
    # Ikke kandidat for utløseren (kontrollen er ikke ren) — bestilt av et
    # menneske gjennom samme vei, som brudd-saken viser.
    from .test_bestilling_bokfor_port import _bestill
    d = _bestill(klient, _tok(token), fid).json()
    assert d["beslutning"] == "brudd" and d["unntak_id"], d
    sak = d["unntak_id"]
    _sett_kontekst(migrator, TENANT)
    ct, key_id, nonce = migrator.execute(
        "SELECT payload_kryptert, key_id, nonce FROM unntak"
        " WHERE tenant=%s AND id=%s", (TENANT, sak)).fetchone()
    nok = migrator.execute(
        "SELECT wrapped_dek FROM tenant_nokler WHERE tenant=%s AND key_id=%s",
        (TENANT, key_id)).fetchone()[0]
    migrator.rollback()
    dek = kryptering._pakk_ut((key_id, nok), TENANT)[1]
    payload = kryptering.dekrypter(dek, bytes(ct), bytes(nonce), TENANT,
                                   key_id)
    assert payload["handling"] == "faktura.bokfor"
    assert payload["faktura_id"] == str(fid) and payload["omfang"] == "bilag"
    assert payload["brutto_ore"] == 2040000
    plan = reparasjoner._r1_reinnsending(payload, None)
    assert plan.utfall == "oppdrag", (plan.utfall, plan.grunn)
    assert plan.oppdragstype == "faktura.bokfor"
    assert set(plan.reparasjonsinput) == {"faktura_id", "fakturanummer",
                                          "leverandor_ref", "brutto_ore",
                                          "omfang"}


@pg_plan
def test_en_kontrollert_faktura_bokfores_med_menneskets_avgjorelse_intakt(
        migrator, miljo, app, klient, token):
    from modules.m14_fakturakontroll import controller
    controller._sov = lambda s: None
    _bokforpolicy(migrator); _rigg(migrator)
    tok = _tok(token)
    fid = _fakt(migrator, netto=1800000, mva=450000)
    r = _post(klient, tok, f"/v1/faktura/{fid}/avgjor",
              {"status": "kontrollert", "begrunnelse": "sett av controller"})
    assert r.status_code == 200, r.text
    oid = _bestilt_av_utloseren(app, fid)
    mtk, _ = _onboard_token(klient, migrator, MODUL, _release(migrator))
    ut = _kjor_til(klient, mtk, _signer, fid)
    assert ut["utfall"] == "utfort", ut
    f, b, _ = _tilstand(migrator, fid)
    assert f[0] == "bokfort" and f[3] == oid
    assert f[4].startswith("bruker:") or f[4].startswith("token:"), f
    assert len(b) == 1 and b[0][2] == 2250000
