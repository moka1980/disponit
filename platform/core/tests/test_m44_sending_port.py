"""Ende-til-ende-porten for ARC B kampanje, PR 4: kampanjen går ut.

Hele kjeden mot ekte base og ekte app: mottaker med KRYPTERT adresse
(153) og samtykke → kampanje med innhold, i planen → utløseren bestiller
som agent (155), samtykket attesteres (154) → oppdrag → modulen
onboardes (035) og claimer over API-et → claim-svaret bærer adressen
DEKRYPTERT og teksten i `utforelse` (156), aldri i payloaden → modulen
leverer (stub-SMTP) → signert kvittering → oppdraget er `utfort`, og
kvitteringen er evidensen (ressursbundet, uten adresse).

  1. Den gyldne veien, over.
  2. Samtykket trukket MELLOM bestilling og claim: claim-svaret bærer
     hindringen, ingenting leveres, oppdraget lukkes `feilet`.
  3. Avsenderprofilen: `POST /v1/kampanje/avsender` setter navn og
     svar-til; e-posten bærer dem. Ugyldig svar-til → 400 med detalj.
  4. Claim-svaret bærer `utforelse` for purring.send OG kampanje.send —
     m56/m57-kontrakten er urørt.

MUTASJONER SOM DREPER DENNE: dropp kampanjegrenen i claim-veien (1 →
feilet utforelse_mangler), la `utforelse_for_sending` hoppe over
samtykket (2), eller legg adressen i payloaden (1).
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_bestilling_kampanje_port import (I_DAG, _kampanjepolicy, _klar,
                                            _payload)
from .test_m37 import _sett_kontekst, _signer_kvittering
from .test_m44_kampanje import _rt, _samtykke
from .test_m44_kampanjeutloser_port import _pa, _runde
from .test_m44_kontakt_port import _post
from .test_modul_onboarding_http import _onboard_token

PLAN_DSN = __import__("os").environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")


class _Sender:
    def __init__(self):
        self.sendt = []

    def __call__(self, til, emne, tekst, *, avsender_navn=None,
                 svar_til=None):
        self.sendt.append({"til": til, "emne": emne, "tekst": tekst,
                           "avsender_navn": avsender_navn,
                           "svar_til": svar_til})
        return {"melding_id": "<e2e@fjordlys.example>"}


def _signer(kropp):
    return _signer_kvittering(kropp, verifikator="v_samtykke")


def _klar_med_ekte_adresse(klient, tok, adresse):
    """Riggen fra bestillingsporten, men adressen går gjennom API-et så
    chifferteksten er EKTE og lar seg dekryptere i claim-veien."""
    kid, mid = _klar(med_kontakt=False)
    r = _post(klient, tok, f"/v1/kampanje/mottaker/{mid}/kontakt",
              {"kontakt": adresse})
    assert r.status_code == 200, r.text
    return kid, mid


def _release(migrator):
    rel = migrator.execute(
        "SELECT release_id FROM moduldeployment WHERE modul_id='m44_kampanje'"
        " AND livslop='claiming' LIMIT 1").fetchone()[0]
    migrator.rollback()
    return rel


def _oppdrag(migrator, oid):
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT status, kvittering, oppdragstype, eiermodul FROM oppdrag"
        " WHERE tenant=%s AND id=%s", (TENANT, oid)).fetchone()
    migrator.rollback()
    return rad


def _kjor_til(klient, mtk, sender, signer, kid, *, maks=12):
    """Claim til modulen får DENNE kampanjens oppdrag. Planrunden
    bestiller alt som er klart i tenanten — også par tidligere tester la
    i planen (falsk chiffertekst fra 114-døra, trukket samtykke); dem
    kvitterer modulen `feilet` på, og sender ingenting — det måles."""
    from modules.m44_kampanje import controller
    for _ in range(maks):
        for_ = len(sender.sendt)
        ut = controller.kjor_en(klient, mtk, sender, signer)
        if ut.get("utfall") == "tomt" or ut.get("kampanje") == str(kid):
            return ut
        assert ut.get("utfall") == "avbrutt" and len(sender.sendt) == for_, ut
    raise AssertionError("fant aldri kampanjens oppdrag")


def _bestilt_av_utloseren(app, kid):
    pa = _pa()
    try:
        res = _runde(app, pa)
    finally:
        pa.close()
    mine = [x for x in res["resultater"] if x["kampanje"] == str(kid)]
    assert mine and mine[0]["utfall"] == "tillat", res
    return mine[0]["oppdrag_id"]


@pg_plan
def test_kampanjen_gaar_ut_hele_veien(migrator, miljo, app, klient, token):
    from modules.m44_kampanje import controller
    controller._sov = lambda s: None
    _kampanjepolicy(migrator)
    tok, _ = token(rolle="bestiller", scopes=("bestilling:opprett",))
    r = _post(klient, tok, "/v1/kampanje/avsender",
              {"avsender_navn": "Fjordlys Elektro AS",
               "svar_til": "post@fjordlys.example"})
    assert r.status_code == 200, r.text
    adresse = "kari-" + secrets.token_hex(3) + "@example.com"
    kid, mid = _klar_med_ekte_adresse(klient, tok, adresse)
    oid = _bestilt_av_utloseren(app, kid)
    mtk, _ = _onboard_token(klient, migrator, "m44_kampanje",
                            _release(migrator))
    sender = _Sender()
    ut = _kjor_til(klient, mtk, sender, _signer, kid)
    assert ut["utfall"] == "utfort", ut
    assert ut["kvittering_status"] == 200, ut
    # ÉN e-post, til den DEKRYPTERTE adressen, i tenantens navn, med
    # tenantens tekst, mottakerens navn og avmeldingslenken.
    assert len(sender.sendt) == 1
    e = sender.sendt[0]
    assert e["til"] == adresse
    assert e["avsender_navn"] == "Fjordlys Elektro AS"
    assert e["svar_til"] == "post@fjordlys.example"
    assert e["emne"] == "Høstsjekk"
    assert "høstsjekk" in e["tekst"] and "Meld deg av her" in e["tekst"]
    assert "{navn}" not in e["tekst"]
    # Oppdraget er UTFØRT, og kvitteringen er evidensen — uten adressen.
    status, kv, otype, eier = _oppdrag(migrator, oid)
    assert (status, otype, eier) == ("utfort", "kampanje.send",
                                     "m44_kampanje"), (status, otype, eier)
    assert kv["resultat"] == "utfort"
    assert kv["ressurs_id"] == f"kampanje:{kid}:{mid}"
    assert kv["malversjon"] == "kampanje-v1"
    assert kv["planlagt_sendt"] == I_DAG
    assert adresse not in str(kv) and "høstsjekk" not in str(kv).lower()
    # …og adressen står heller ikke i oppdragets payload.
    assert adresse not in str(_payload(migrator, oid))
    # Neste runde: ingenting igjen for modulen — og ingen ny e-post,
    # heller ikke for rester andre tester la i planen.
    assert _kjor_til(klient, mtk, sender, _signer, kid) == {"utfall": "tomt"}
    assert len(sender.sendt) == 1


@pg_plan
def test_samtykke_trukket_mellom_bestilling_og_claim_leverer_ikke(
        migrator, miljo, app, klient, token):
    from modules.m44_kampanje import controller
    controller._sov = lambda s: None
    _kampanjepolicy(migrator)
    tok, _ = token(rolle="bestiller", scopes=("bestilling:opprett",))
    adresse = "per-" + secrets.token_hex(3) + "@example.com"
    kid, mid = _klar_med_ekte_adresse(klient, tok, adresse)
    oid = _bestilt_av_utloseren(app, kid)
    c = _rt()
    try:
        _samtykke(c, TENANT, mid, "trukket", I_DAG)
    finally:
        c.close()
    mtk, _ = _onboard_token(klient, migrator, "m44_kampanje",
                            _release(migrator))
    sender = _Sender()
    ut = _kjor_til(klient, mtk, sender, _signer, kid)
    assert ut["utfall"] == "avbrutt" and ut["grunn"] == "samtykke_ugyldig", ut
    assert sender.sendt == []
    status, kv, _, _ = _oppdrag(migrator, oid)
    assert status == "feilet", status
    assert kv["feilkode"] == "samtykke_ugyldig"
    assert adresse not in str(kv)


@pg
def test_avsenderprofilen_valideres(miljo, klient, token, migrator):
    tok, _ = token(rolle="bestiller", scopes=("bestilling:opprett",))
    r = _post(klient, tok, "/v1/kampanje/avsender",
              {"avsender_navn": "Fjordlys Elektro AS",
               "svar_til": "ikke en adresse"})
    assert r.status_code == 400, r.text
    assert "svar_til" in r.json().get("detalj", ""), r.text
    r = _post(klient, tok, "/v1/kampanje/avsender", {"svar_til": "a@b.no"})
    assert r.status_code == 400, r.text
    assert "avsender_navn" in r.json().get("detalj", ""), r.text
    r = _post(klient, tok, "/v1/kampanje/avsender",
              {"avsender_navn": "  Fjordlys Elektro AS "})
    assert r.status_code == 200, r.text
    assert r.json()["avsender_navn"] == "Fjordlys Elektro AS"
    assert r.json()["svar_til"] is None
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT avsender_navn, svar_til FROM kampanjeavsender"
        " WHERE tenant=%s", (TENANT,)).fetchone()
    n = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND kilde='m44_kampanje' AND handling='avsender.satt'",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert rad == ("Fjordlys Elektro AS", None) and n >= 1


def test_claim_svaret_barer_utforelse_for_eiermodulene():
    """Statisk: `utforelse` legges på svaret for purring.send,
    kampanje.send, kundeservice.svar.send og faktura.bokfor(_stor) — og
    bare dem; m56/m57-kontrakten er urørt."""
    from pathlib import Path
    kode = (Path(__file__).resolve().parents[1] / "api" / "app.py"
            ).read_text(encoding="utf-8")
    i = kode.index("utforelse = utforelse_for_sending(")
    blokk = kode[i - 400:i + 900]
    assert 'if oppdragstype == "purring.send":' in blokk
    assert 'elif oppdragstype == "kampanje.send":' in blokk
    assert 'elif oppdragstype == "kundeservice.svar.send":' in kode
    assert kode.count("utforelse = utforelse_for_sending(") == 3
    # M-14 (ARC B bokføring): bilaget, ikke en sending — egen funksjon.
    assert 'elif oppdragstype in ("faktura.bokfor", "faktura.bokfor_stor"):' \
        in kode
    assert kode.count("utforelse = utforelse_for_bokforing(") == 1
    assert 'if utforelse is not None:\n            svar["utforelse"]' in kode
