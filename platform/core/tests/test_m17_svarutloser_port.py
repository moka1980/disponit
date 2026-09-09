"""Porten for ARC B kundeservice, PR 3: svarutløseren — registeret
bestiller når et menneske har godkjent, policyen avgjør.

Målt mot ekte base, som planarbeideren (`DISPONIT_TEST_PLAN_DSN`) og med
API-ens egen `Tjeneste`, med bransjemalen + utvidelsen
`kundeservice-svar` (`kundeservice.svar.send` tillatt for `agent`):

  1. Kandidatdøra: bare GODKJENTE utkast på åpne henvendelser med
     adresse og e-postkanal, som ikke står i unntakskøen; foreslått,
     forkastet, lukket, skjema, uten adresse, i unntakskøen eller alt
     bestilt → ikke kandidat. Kryss-tenant-døra nekter tenantkontekst.
  2. Én runde: tillat → oppdrag, bokført rad med oppdrag_id, evidens
     uten tekst. Runde to: utkastet er ikke kandidat lenger.
  3. Policy uten handlingen: ingenting bestilles, ingen beslutning brennes.
  4. Kill-switch `DISPONIT_SVAR_UTLOSER=av`: ingenting skjer.
  5. Forbigående feil og plattformtilstand bokføres ikke; en dom
     (`henvendelse_ukjent`) bokføres som `feil:`.
  6. Et godkjent utkast med et fødselsnummer: brudd → unntakskø, bokført
     med unntak_id, IKKE prøvd igjen — et rettet, nytt utkast er en ny
     kandidat.
  7. Planrunden kaller utløseren; dørene står i eierskapsdesignet.

MUTASJONER SOM DREPER DENNE: fjern `u.status = 'godkjent'` i døra (1),
fjern bokføringen i `utlos_en` (2/6 bestiller to ganger), fjern
policysjekken (3).
"""
import os
import uuid

import pytest
import yaml as _yaml

from .test_api import (DSN, MIGRATOR_DSN, POLICIES, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_bestilling_svar_port import (REN, _klar, _sikre_m17_claimbar,
                                        _svarpolicy, _tok)
from .test_m17_avsender_port import _post
from .test_m17_kundeservice import _nokkel, _utkast
from .test_m37 import _sett_kontekst

PLAN_DSN = os.environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")


def _pa():
    from db.pg import koble
    return koble(PLAN_DSN)


def _policy_uten_svar(m):
    from api import policyregister
    p = _yaml.safe_load((POLICIES / "bransjemal-tjenestebedrift.yaml")
                        .read_text(encoding="utf-8"))
    assert not any(h["id"] == "kundeservice.svar.send" for h in p["handlinger"])
    policyregister.registrer(m, TENANT, p, p["meta"]["status"])
    m.commit()
    _sikre_m17_claimbar(m)


def _kandidater(pa):
    from plan.kundeservice import kandidater
    return {(r[0], str(r[1]), str(r[2])) for r in kandidater(pa)}


def _bokfort(migrator, hid):
    _sett_kontekst(migrator, TENANT)
    rader = migrator.execute(
        "SELECT utkast_id::text, utfall, oppdrag_id, unntak_id"
        " FROM svarbestilling WHERE tenant=%s AND henvendelse_id=%s"
        " ORDER BY bestilt_ts", (TENANT, hid)).fetchall()
    migrator.rollback()
    return rader


def _runde(app, pa):
    from plan.kundeservice import kjor_en_runde
    return kjor_en_runde(app.tjeneste, pa)


def _mine(res, hid):
    return [r for r in res["resultater"] if r["henvendelse"] == str(hid)]


def _antall_beslutninger(migrator):
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND handling='kundeservice.svar.send'", (TENANT,)).fetchone()[0]
    migrator.rollback()
    return n


def _dom(hid_uid, status):
    from db.pg import koble
    c = koble(DSN)
    try:
        _sett_kontekst(c, TENANT)
        c.execute("SELECT m17_avgjor_utkast(%s,%s,%s,'u-test')",
                  (TENANT, hid_uid[1], status))
        c.commit()
    finally:
        c.close()


@pg_plan
def test_kandidatdora_krever_godkjent_apen_adresse_og_svarvei(
        migrator, miljo, klient, token):
    _svarpolicy(migrator)
    tok = _tok(token)
    klar = _klar(klient, tok)
    foreslatt = _klar(klient, tok, godkjent=False)
    forkastet = _klar(klient, tok, godkjent=False); _dom(forkastet, "forkastet")
    skjema = _klar(klient, tok, kanal="skjema", adresse="Ola")
    lukket = _klar(klient, tok)
    r = _post(klient, tok, f"/v1/kundeservice/henvendelse/{lukket[0]}/lukk",
              {"utfall": "ikke_aktuell"})
    assert r.status_code == 200, r.text
    pa = _pa()
    try:
        k = _kandidater(pa)
        assert (TENANT, klar[0], klar[1]) in k, k
        for hid, uid in (foreslatt, forkastet, skjema, lukket):
            assert (TENANT, hid, uid) not in k, (hid, k)
        import psycopg
        _sett_kontekst(pa, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pa.execute("SELECT * FROM m17_svarkandidater(10)")
        pa.rollback()
    finally:
        pa.close()


@pg_plan
def test_en_runde_bestiller_og_bokforer_og_runde_to_gjor_ingenting(
        migrator, miljo, app, klient, token):
    _svarpolicy(migrator)
    hid, uid = _klar(klient, _tok(token))
    pa = _pa()
    try:
        r1 = _runde(app, pa)
        mine = _mine(r1, hid)
        assert len(mine) == 1, r1
        assert mine[0]["utfall"] == "tillat" and mine[0]["bokfort"], mine
        oid = mine[0]["oppdrag_id"]
        assert _bokfort(migrator, hid) == [(uid, "tillat", oid, None)]
        _sett_kontekst(migrator, TENANT)
        rad = migrator.execute(
            "SELECT oppdragstype, handling, eiermodul FROM oppdrag"
            " WHERE tenant=%s AND id=%s", (TENANT, oid)).fetchone()
        ev = migrator.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
            " AND kilde='m17_kundeservice' AND handling='svar.bestilt'"
            " AND aktor='agent:kundeservice'", (TENANT,)).fetchone()[0]
        lekk = migrator.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
            " AND begrunnelse::text ILIKE %s", (TENANT, "%" + REN[:20] + "%")
        ).fetchone()[0]
        migrator.rollback()
        assert rad == ("kundeservice.svar.send", "kundeservice.svar.send",
                       "m17_kundeservice"), rad
        assert ev >= 1 and lekk == 0
        assert (TENANT, hid, uid) not in _kandidater(pa)
        r2 = _runde(app, pa)
        assert not _mine(r2, hid), r2
        assert len(_bokfort(migrator, hid)) == 1
    finally:
        pa.close()


@pg_plan
def test_policy_uten_handlingen_bestiller_ingenting(migrator, miljo, app,
                                                    klient, token):
    _policy_uten_svar(migrator)
    hid, uid = _klar(klient, _tok(token))
    pa = _pa()
    try:
        assert (TENANT, hid, uid) in _kandidater(pa)
        for_ = _antall_beslutninger(migrator)
        r = _runde(app, pa)
        assert r["uten_policy"] >= 1 and not _mine(r, hid), r
        assert _bokfort(migrator, hid) == []
        assert _antall_beslutninger(migrator) == for_
    finally:
        pa.close()


@pg_plan
def test_kill_switch_stopper_utloseren(migrator, miljo, app, klient, token,
                                       monkeypatch):
    _svarpolicy(migrator)
    hid, uid = _klar(klient, _tok(token))
    monkeypatch.setenv("DISPONIT_SVAR_UTLOSER", "av")
    pa = _pa()
    try:
        r = _runde(app, pa)
        assert r == {"av": True, "plukket": 0, "resultater": []}, r
        assert _bokfort(migrator, hid) == []
        monkeypatch.delenv("DISPONIT_SVAR_UTLOSER")
        assert (TENANT, hid, uid) in _kandidater(pa)
    finally:
        pa.close()


@pg_plan
def test_forbigaende_bokfores_ikke_men_en_dom_gjor(migrator, miljo, app,
                                                   klient, token, monkeypatch):
    import api.bestilling as bestilling
    _svarpolicy(migrator)
    hid, uid = _klar(klient, _tok(token))
    pa = _pa()
    try:
        for kode in ("db_utilgjengelig", "bestillingstype_utilgjengelig",
                     "policy_ukjent"):
            monkeypatch.setattr(bestilling, "utfor_bestilling",
                                lambda *a, _k=kode, **kw: ("feil", _k))
            r = _runde(app, pa)
            mine = _mine(r, hid)
            assert mine and mine[0].get("forbigaende") == kode, (kode, r)
            assert _bokfort(migrator, hid) == []
            assert (TENANT, hid, uid) in _kandidater(pa)
        monkeypatch.setattr(bestilling, "utfor_bestilling",
                            lambda *a, **kw: ("feil", "henvendelse_ukjent"))
        r = _runde(app, pa)
        mine = _mine(r, hid)
        assert mine and mine[0]["utfall"] == "feil:henvendelse_ukjent", r
        assert (TENANT, hid, uid) not in _kandidater(pa)
    finally:
        pa.close()


@pg_plan
def test_dlp_funn_gir_brudd_en_gang_og_et_rettet_utkast_er_ny_kandidat(
        migrator, miljo, app, klient, token):
    _svarpolicy(migrator)
    hid, uid = _klar(klient, _tok(token),
                     tekst="Fødselsnummer 010190 12345 er registrert.")
    pa = _pa()
    try:
        r1 = _runde(app, pa)
        mine = _mine(r1, hid)
        assert mine and mine[0]["utfall"] == "brudd" and mine[0]["unntak_id"], r1
        rader = _bokfort(migrator, hid)
        assert len(rader) == 1 and rader[0][1] == "brudd"
        assert (TENANT, hid, uid) not in _kandidater(pa)
        assert not _mine(_runde(app, pa), hid)
        # Et rettet utkast er en NY rad — og en ny kandidat når det godkjennes.
        from db.pg import koble
        c = koble(DSN)
        try:
            key_id, dek = _nokkel(c, TENANT)
            uid2 = str(_utkast(c, TENANT, hid, key_id, dek, tekst=REN))
        finally:
            c.close()
        _dom((hid, uid2), "godkjent")
        assert (TENANT, hid, uid2) in _kandidater(pa)
        r3 = _runde(app, pa)
        mine = _mine(r3, hid)
        assert mine and mine[0]["utkast"] == uid2 \
            and mine[0]["utfall"] == "tillat", r3
    finally:
        pa.close()


def test_planrunden_kaller_utloseren_og_dorene_er_designert():
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    kode = (rot / "platform" / "core" / "plan" / "materialiser.py"
            ).read_text(encoding="utf-8")
    assert "from plan.kundeservice import kjor_en_runde" in kode
    design = (rot / "deploy" / "staging" / "eierskap-reparasjon.sql"
              ).read_text(encoding="utf-8")
    for d in ("m17_svarkandidater(integer)",
              "m17_bokfor_svarbestilling(text,uuid,uuid,text,text,"
              "bigint,bigint,text,jsonb)",
              "m17_svarbestillingene(text,uuid)"):
        assert d in design, d
    from plan.kundeservice import idempotensnokkel
    n = idempotensnokkel(uuid.UUID(int=1), uuid.UUID(int=2))
    assert 8 <= len(n) <= 200 and n.startswith("svar:")
