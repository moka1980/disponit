"""Ende-til-ende-porten for ARC B tilbud, PR 4: tilbudet går ut.

Hele kjeden mot ekte base og ekte app: tilbud med KRYPTERT adresse (169)
→ et menneske godkjenner → utløseren bestiller som agent (171), faktaene
attesteres (170) → oppdrag → modulen onboardes og claimer over API-et →
claim-svaret bærer adressen, linjene og den bundne klausulteksten
DEKRYPTERT i `utforelse` (172), aldri i payloaden → modulen sender
(stub-SMTP) → signert kvittering → oppdraget er `utfort`.

  1. Den gyldne veien, over — med avsenderprofilen.
  2. Tilbudet utløpt MELLOM bestilling og claim: hindring, ingenting
     sendes, oppdraget lukkes `feilet`.
  3. Avsenderprofilen: `POST /v1/tilbud/avsender` setter navn, svar-til
     og signatur; `GET /v1/tilbud` bærer den. Ugyldig svar-til → 400.

MUTASJONER SOM DREPER DENNE: dropp tilbudsgrenen i claim-veien (1 →
feilet utforelse_mangler), la `utforelse_for_sending` hoppe over
status-sjekken (2), eller legg adressen i payloaden (1).
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_bestilling_kampanje_port import _payload
from .test_bestilling_tilbud_port import _btok, _klar, _tilbudpolicy
from .test_m17_avsender_port import _post
from .test_m26_tilbud_port import _rigg
from .test_m26_tilbudsutloser_port import _pa, _runde
from .test_m37 import _sett_kontekst, _signer_kvittering
from .test_modul_onboarding_http import _onboard_token

PLAN_DSN = __import__("os").environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")
MODUL = "m26_prisbok"


class _Sender:
    def __init__(self):
        self.sendt = []

    def __call__(self, til, emne, tekst, *, avsender_navn=None, svar_til=None):
        self.sendt.append({"til": til, "emne": emne, "tekst": tekst,
                           "avsender_navn": avsender_navn, "svar_til": svar_til})
        return {"melding_id": "<e2e@fjordlys.example>"}


def _signer(kropp):
    return _signer_kvittering(kropp, verifikator="v_prisbok")


def _release(migrator):
    rel = migrator.execute(
        "SELECT release_id FROM moduldeployment"
        f" WHERE modul_id='{MODUL}' AND livslop='claiming' LIMIT 1").fetchone()[0]
    migrator.rollback()
    return rel


def _oppdrag(migrator, oid):
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT status, kvittering, oppdragstype, eiermodul FROM oppdrag"
        " WHERE tenant=%s AND id=%s", (TENANT, oid)).fetchone()
    migrator.rollback()
    return rad


def _bestilt_av_utloseren(app, tid):
    pa = _pa()
    try:
        res = _runde(app, pa)
    finally:
        pa.close()
    mine = [x for x in res["resultater"] if x["tilbud"] == str(tid)]
    assert mine and mine[0]["utfall"] == "tillat", res
    return mine[0]["oppdrag_id"]


def _kjor_til(klient, mtk, sender, signer, tid, *, maks=12):
    from modules.m26_prisbok import controller
    for _ in range(maks):
        for_ = len(sender.sendt)
        ut = controller.kjor_en(klient, mtk, sender, signer)
        if ut.get("utfall") == "tomt" or ut.get("tilbud") == str(tid):
            return ut
        assert ut.get("utfall") == "avbrutt" and len(sender.sendt) == for_, ut
    raise AssertionError("fant aldri tilbudets oppdrag")


@pg_plan
def test_tilbudet_gaar_ut_hele_veien(migrator, miljo, app, klient, token):
    from modules.m26_prisbok import controller
    controller._sov = lambda s: None
    _tilbudpolicy(migrator)
    kabel, pumpe, _ = _rigg()
    tok = _btok(token)
    r = _post(klient, tok, "/v1/tilbud/avsender",
              {"avsender_navn": "Fjordlys Elektro AS",
               "svar_til": "post@fjordlys.example",
               "signatur": "Fjordlys Elektro AS · Tromsø"})
    assert r.status_code == 200, r.text
    adresse = "styret-" + secrets.token_hex(3) + "@nordvik.example"
    tid, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 40},
                                 {"produkt_id": str(pumpe), "antall": 1}],
                   kunde_epost=adresse)
    oid = _bestilt_av_utloseren(app, tid)
    mtk, _ = _onboard_token(klient, migrator, MODUL, _release(migrator))
    sender = _Sender()
    ut = _kjor_til(klient, mtk, sender, _signer, tid)
    assert ut["utfall"] == "utfort" and ut["kvittering_status"] == 200, ut
    assert len(sender.sendt) == 1
    e = sender.sendt[0]
    assert e["til"] == adresse
    assert e["avsender_navn"] == "Fjordlys Elektro AS"
    assert e["svar_til"] == "post@fjordlys.example"
    assert e["emne"].startswith("Tilbud fra Fjordlys Elektro AS: 29400,00 NOK")
    assert "Kabel PFXP 3x2,5: 40 m à 12,50" in e["tekst"]
    assert "14 dager" in e["tekst"]
    assert e["tekst"].rstrip().endswith("Fjordlys Elektro AS · Tromsø")
    status, kv, otype, eier = _oppdrag(migrator, oid)
    assert (status, otype, eier) == ("utfort", "tilbud.generer", MODUL)
    assert kv["resultat"] == "utfort" and kv["ressurs_id"] == f"tilbud:{tid}"
    assert kv["malversjon"] == "tilbud-v1"
    assert adresse not in str(kv) and adresse not in str(_payload(migrator, oid))
    assert _kjor_til(klient, mtk, sender, _signer, tid) == {"utfall": "tomt"}
    assert len(sender.sendt) == 1


@pg_plan
def test_utlopt_mellom_bestilling_og_claim_sender_ikke(migrator, miljo, app,
                                                          klient, token):
    from modules.m26_prisbok import controller
    controller._sov = lambda s: None
    _tilbudpolicy(migrator)
    kabel, _, _ = _rigg()
    tok = _btok(token)
    adresse = "per-" + secrets.token_hex(3) + "@nordvik.example"
    tid, _ = _klar(klient, tok, [{"produkt_id": str(kabel), "antall": 2}],
                   kunde_epost=adresse)
    oid = _bestilt_av_utloseren(app, tid)
    # Et godkjent tilbud kan ikke forkastes (vakten: status går bare
    # framover), så tilstandsendringen MELLOM bestilling og claim rigges
    # som TIDEN: gyldigheten utløper. Raden er frosset — vakten kobles av
    # for denne ene riggen, som klokka ville gjort det.
    _sett_kontekst(migrator, TENANT)
    migrator.execute("ALTER TABLE tilbud DISABLE TRIGGER m26_tilbud_vakt")
    migrator.execute("UPDATE tilbud SET tilbudsdato = current_date - 10,"
                     " gyldig_til = current_date - 1"
                     " WHERE tenant=%s AND tilbud_id=%s", (TENANT, tid))
    migrator.execute("ALTER TABLE tilbud ENABLE TRIGGER m26_tilbud_vakt")
    migrator.commit()
    mtk, _ = _onboard_token(klient, migrator, MODUL, _release(migrator))
    sender = _Sender()
    ut = _kjor_til(klient, mtk, sender, _signer, tid)
    assert ut["utfall"] == "avbrutt" and ut["grunn"] == "tilbud_utlopt", ut
    assert sender.sendt == []
    status, kv, _, _ = _oppdrag(migrator, oid)
    assert status == "feilet" and kv["feilkode"] == "tilbud_utlopt"
    assert adresse not in str(kv)


@pg
def test_avsenderprofilen_valideres_og_baeres(miljo, klient, token, migrator):
    tok = _btok(token)
    r = _post(klient, tok, "/v1/tilbud/avsender",
              {"avsender_navn": "Fjordlys Elektro AS", "svar_til": "ikke en adresse"})
    assert r.status_code == 400 and "svar_til" in r.json().get("detalj", "")
    r = _post(klient, tok, "/v1/tilbud/avsender", {"svar_til": "a@b.no"})
    assert r.status_code == 400, r.text
    r = _post(klient, tok, "/v1/tilbud/avsender",
              {"avsender_navn": "  Fjordlys Elektro AS "})
    assert r.status_code == 200, r.text
    assert r.json()["avsender_navn"] == "Fjordlys Elektro AS"
    hode = {"authorization": f"Bearer {tok}"}
    a = klient.get("/v1/tilbud", headers=hode).json()["avsenderprofil"]
    assert a["avsender_navn"] == "Fjordlys Elektro AS" and a["svar_til"] is None
