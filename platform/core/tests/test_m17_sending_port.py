"""Ende-til-ende-porten for ARC B kundeservice, PR 4: svaret går ut.

Hele kjeden mot ekte base og ekte app: henvendelse med KRYPTERT adresse
(160) → utkast → et menneske godkjenner → utløseren bestiller som agent
(162), godkjenningen og teksten attesteres (161) → oppdrag → modulen
onboardes og claimer over API-et → claim-svaret bærer adressen, emnet og
utkastet DEKRYPTERT i `utforelse` (163), aldri i payloaden → modulen
sender (stub-SMTP) → signert kvittering → oppdraget er `utfort`, og
kvitteringen er evidensen (ressursbundet, uten adresse og tekst).

  1. Den gyldne veien, over.
  2. Henvendelsen lukket MELLOM bestilling og claim: claim-svaret bærer
     hindringen, ingenting sendes, oppdraget lukkes `feilet`.
  3. Avsenderprofilen: `POST /v1/kundeservice/avsender` setter navn,
     svar-til og signatur; svaret bærer dem. Ugyldig svar-til → 400.

MUTASJONER SOM DREPER DENNE: dropp kundeservicegrenen i claim-veien (1
→ feilet utforelse_mangler), la `utforelse_for_sending` hoppe over
lukket-sjekken (2), eller legg teksten i payloaden (1).
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_bestilling_kampanje_port import _payload
from .test_bestilling_svar_port import REN, _klar, _svarpolicy, _tok
from .test_m17_avsender_port import _post
from .test_m17_svarutloser_port import _pa, _runde
from .test_m37 import _sett_kontekst, _signer_kvittering
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
    return _signer_kvittering(kropp, verifikator="v_kundeservice")


def _release(migrator):
    rel = migrator.execute(
        "SELECT release_id FROM moduldeployment"
        " WHERE modul_id='m17_kundeservice' AND livslop='claiming' LIMIT 1"
    ).fetchone()[0]
    migrator.rollback()
    return rel


def _oppdrag(migrator, oid):
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT status, kvittering, oppdragstype, eiermodul FROM oppdrag"
        " WHERE tenant=%s AND id=%s", (TENANT, oid)).fetchone()
    migrator.rollback()
    return rad


def _bestilt_av_utloseren(app, hid):
    pa = _pa()
    try:
        res = _runde(app, pa)
    finally:
        pa.close()
    mine = [x for x in res["resultater"] if x["henvendelse"] == str(hid)]
    assert mine and mine[0]["utfall"] == "tillat", res
    return mine[0]["oppdrag_id"]


def _kjor_til(klient, mtk, sender, signer, hid, *, maks=12):
    """Claim til modulen får DENNE henvendelsens oppdrag (rester fra
    andre tester kvitteres `feilet`, og sender ingenting — det måles)."""
    from modules.m17_kundeservice import controller
    for _ in range(maks):
        for_ = len(sender.sendt)
        ut = controller.kjor_en(klient, mtk, sender, signer)
        if ut.get("utfall") == "tomt" or ut.get("henvendelse") == str(hid):
            return ut
        assert ut.get("utfall") == "avbrutt" and len(sender.sendt) == for_, ut
    raise AssertionError("fant aldri henvendelsens oppdrag")


@pg_plan
def test_svaret_gaar_ut_hele_veien(migrator, miljo, app, klient, token):
    from modules.m17_kundeservice import controller
    controller._sov = lambda s: None
    _svarpolicy(migrator)
    tok = _tok(token)
    r = _post(klient, tok, "/v1/kundeservice/avsender",
              {"avsender_navn": "Fjordlys Elektro AS",
               "svar_til": "post@fjordlys.example",
               "signatur": "Fjordlys Elektro AS · Tromsø"})
    assert r.status_code == 200, r.text
    adresse = "kari-" + secrets.token_hex(3) + "@nordvik.example"
    hid, uid = _klar(klient, tok, adresse=adresse)
    oid = _bestilt_av_utloseren(app, hid)
    mtk, _ = _onboard_token(klient, migrator, "m17_kundeservice",
                            _release(migrator))
    sender = _Sender()
    ut = _kjor_til(klient, mtk, sender, _signer, hid)
    assert ut["utfall"] == "utfort", ut
    assert ut["kvittering_status"] == 200, ut
    assert len(sender.sendt) == 1
    e = sender.sendt[0]
    assert e["til"] == adresse
    assert e["avsender_navn"] == "Fjordlys Elektro AS"
    assert e["svar_til"] == "post@fjordlys.example"
    assert e["emne"] == "Re: Spørsmål"
    assert e["tekst"].startswith(REN)
    assert e["tekst"].rstrip().endswith("Fjordlys Elektro AS · Tromsø")
    status, kv, otype, eier = _oppdrag(migrator, oid)
    assert (status, otype, eier) == ("utfort", "kundeservice.svar.send",
                                     "m17_kundeservice"), (status, otype, eier)
    assert kv["resultat"] == "utfort"
    assert kv["ressurs_id"] == f"henvendelse:{hid}:{uid}"
    assert kv["malversjon"] == "svar-v1"
    assert adresse not in str(kv) and REN[:20] not in str(kv)
    assert adresse not in str(_payload(migrator, oid))
    assert REN[:20] not in str(_payload(migrator, oid))
    assert _kjor_til(klient, mtk, sender, _signer, hid) == {"utfall": "tomt"}
    assert len(sender.sendt) == 1


@pg_plan
def test_lukket_mellom_bestilling_og_claim_sender_ikke(migrator, miljo, app,
                                                       klient, token):
    from modules.m17_kundeservice import controller
    controller._sov = lambda s: None
    _svarpolicy(migrator)
    tok = _tok(token)
    adresse = "per-" + secrets.token_hex(3) + "@nordvik.example"
    hid, uid = _klar(klient, tok, adresse=adresse)
    oid = _bestilt_av_utloseren(app, hid)
    r = _post(klient, tok, f"/v1/kundeservice/henvendelse/{hid}/lukk",
              {"utfall": "ikke_aktuell"})
    assert r.status_code == 200, r.text
    mtk, _ = _onboard_token(klient, migrator, "m17_kundeservice",
                            _release(migrator))
    sender = _Sender()
    ut = _kjor_til(klient, mtk, sender, _signer, hid)
    assert ut["utfall"] == "avbrutt" and ut["grunn"] == "henvendelse_lukket", ut
    assert sender.sendt == []
    status, kv, _, _ = _oppdrag(migrator, oid)
    assert status == "feilet" and kv["feilkode"] == "henvendelse_lukket"
    assert adresse not in str(kv)


@pg
def test_avsenderprofilen_valideres(miljo, klient, token, migrator):
    tok = _tok(token)
    r = _post(klient, tok, "/v1/kundeservice/avsender",
              {"avsender_navn": "Fjordlys Elektro AS",
               "svar_til": "ikke en adresse"})
    assert r.status_code == 400 and "svar_til" in r.json().get("detalj", "")
    r = _post(klient, tok, "/v1/kundeservice/avsender", {"svar_til": "a@b.no"})
    assert r.status_code == 400, r.text
    r = _post(klient, tok, "/v1/kundeservice/avsender",
              {"avsender_navn": "  Fjordlys Elektro AS "})
    assert r.status_code == 200, r.text
    assert r.json()["avsender_navn"] == "Fjordlys Elektro AS"
    assert r.json()["svar_til"] is None and r.json()["signatur"] is None
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT avsender_navn, svar_til, signatur FROM kundeserviceavsender"
        " WHERE tenant=%s", (TENANT,)).fetchone()
    n = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND kilde='m17_kundeservice' AND handling='avsenderprofil.satt'",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert rad == ("Fjordlys Elektro AS", None, None) and n >= 1
