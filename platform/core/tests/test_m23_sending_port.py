"""Ende-til-ende-porten for ARC B, PR 4: purringen går ut.

Hele kjeden mot ekte base og ekte app: fordring med KRYPTERT adresse
(146) → sveip → utløseren bestiller som agent (148) → oppdrag →
modulen onboardes (035) og claimer over API-et → claim-svaret bærer
adressen DEKRYPTERT i `utforelse` (149), aldri i payloaden → modulen
sender (stub-SMTP) → signert kvittering → oppdraget er `utfort`, og
kvitteringen er evidensen (ressursbundet, uten adresse).

  1. Den gyldne veien, over.
  2. Uten adresse: oppdraget claimes, ingenting sendes, kvitteringen er
     `feilet mottaker_mangler`, oppdraget lukkes.
  3. Avsenderprofilen: `POST /v1/fordring/avsender` setter navn og
     svar-til; e-posten bærer dem. Ugyldig svar-til → 400 med detalj.
  4. Claim-svaret for en annen type bærer ikke `utforelse` (kontrakten
     til m56/m57 er urørt).

MUTASJONER SOM DREPER DENNE: dropp `utforelse` i claim-svaret (1 →
feilet utforelse_mangler), legg adressen i payloaden (1: asserten på
oppdragets kvittering/payload), eller la claim-veien hoppe over
`m23_for_sending` for avsluttede fordringer (2).
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo, pg, token)
from .test_bestilling_purring_port import _bestill, _purring_policy
from .test_m23_fordring import _fordring, _plan
from .test_m23_purringsutloser_port import (_bransjemal, _pa, _runde,
                                            _sveip)
from .test_m37 import _sett_kontekst, _signer_kvittering
from .test_modul_onboarding_http import _onboard_token
from .test_outbox_bestilling import _adminsesjon

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


def _post(klient, tok, sti, kropp):
    return klient.post(sti, json=kropp,
                       headers={"authorization": f"Bearer {tok}",
                                "Idempotency-Key": secrets.token_urlsafe(24)})


def _fordring_med_adresse(klient, tok, adresse, *, forfall_siden=20):
    from .test_m23_fordring import _rt
    c = _rt()
    try:
        _plan(c, TENANT)
        fid = _fordring(c, TENANT, belop=250000, forfall_siden=forfall_siden,
                        nummer="F-" + secrets.token_hex(3))
    finally:
        c.close()
    if adresse:
        r = _post(klient, tok, f"/v1/fordring/{fid}/mottaker",
                  {"mottaker_epost": adresse})
        assert r.status_code == 200, r.text
    _sveip()
    return fid


def _release(migrator):
    rel = migrator.execute(
        "SELECT release_id FROM moduldeployment WHERE modul_id='m23_fordring'"
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


@pg_plan
def test_purringen_gaar_ut_hele_veien(migrator, miljo, app, klient, token):
    from modules.m23_fordring import controller
    controller._sov = lambda s: None
    _bransjemal(migrator)
    tok, _ = token(rolle="bestiller", scopes=("bestilling:opprett",))
    adresse = "kunde-" + secrets.token_hex(3) + "@nordvik.example"
    r = _post(klient, tok, "/v1/fordring/avsender",
              {"avsender_navn": "Fjordlys Elektro AS",
               "svar_til": "regnskap@fjordlys.example"})
    assert r.status_code == 200, r.text
    fid = _fordring_med_adresse(klient, tok, adresse)
    pa = _pa()
    try:
        res = _runde(app, pa)
    finally:
        pa.close()
    mine = [x for x in res["resultater"] if x["fordring"] == str(fid)]
    assert mine and mine[0]["utfall"] == "tillat", res
    oid = mine[0]["oppdrag_id"]
    mtk, _ = _onboard_token(klient, migrator, "m23_fordring",
                            _release(migrator))
    sender = _Sender()
    ut = controller.kjor_en(klient, mtk, sender, _signer_kvittering)
    assert ut["utfall"] == "utfort", ut
    assert ut["kvittering_status"] == 200, ut
    # ÉN e-post, til den DEKRYPTERTE adressen, i tenantens navn.
    assert len(sender.sendt) == 1
    e = sender.sendt[0]
    assert e["til"] == adresse
    assert e["avsender_navn"] == "Fjordlys Elektro AS"
    assert e["svar_til"] == "regnskap@fjordlys.example"
    assert e["emne"].startswith("Påminnelse om faktura F-")
    assert "2 500,00 kr" in e["tekst"]
    # Oppdraget er UTFØRT, og kvitteringen er evidensen — uten adressen.
    status, kv, otype, eier = _oppdrag(migrator, oid)
    assert (status, otype, eier) == ("utfort", "purring.send",
                                     "m23_fordring"), (status, otype, eier)
    assert kv["resultat"] == "utfort"
    assert kv["ressurs_id"] == f"fordring:{fid}"
    assert kv["trinn"] == 1 and kv["handling_trinn"] == "paaminnelse"
    assert kv["malversjon"] == "paaminnelse-v1"
    assert adresse not in str(kv)
    # …og adressen står heller ikke i oppdragets payload.
    from .test_bestilling_purring_port import _payload
    assert adresse not in str(_payload(migrator, oid))
    # Neste runde: ingenting igjen for modulen.
    assert controller.kjor_en(klient, mtk, sender, _signer_kvittering) \
        == {"utfall": "tomt"}
    assert len(sender.sendt) == 1


@pg_plan
def test_uten_adresse_sendes_ingenting_og_oppdraget_lukkes_feilet(
        migrator, miljo, app, klient, token):
    """Utløseren bestiller aldri uten adresse — men et menneske kan
    (policy med bestiller). Da må MODULEN si nei: claim-svaret bærer
    hindringen, ingenting sendes, oppdraget lukkes `feilet`."""
    from modules.m23_fordring import controller
    controller._sov = lambda s: None
    _purring_policy(migrator)
    tok, _ = token(rolle="bestiller", scopes=("bestilling:opprett",))
    fid = _fordring_med_adresse(klient, tok, None)
    cookie, csrf = _adminsesjon()
    r = _bestill(klient, cookie, csrf, fid)
    assert r.status_code == 200 and r.json()["beslutning"] == "tillat", r.text
    oid = r.json()["oppdrag_id"]
    mtk, _ = _onboard_token(klient, migrator, "m23_fordring",
                            _release(migrator))
    sender = _Sender()
    ut = controller.kjor_en(klient, mtk, sender, _signer_kvittering)
    assert ut["utfall"] == "avbrutt" and ut["grunn"] == "mottaker_mangler", ut
    assert sender.sendt == []
    status, kv, _, _ = _oppdrag(migrator, oid)
    assert status == "feilet", status
    assert kv["feilkode"] == "mottaker_mangler"


@pg
def test_avsenderprofilen_valideres(miljo, klient, token, migrator):
    tok, _ = token(rolle="bestiller", scopes=("bestilling:opprett",))
    r = _post(klient, tok, "/v1/fordring/avsender",
              {"avsender_navn": "Fjordlys Elektro AS",
               "svar_til": "ikke en adresse"})
    assert r.status_code == 400, r.text
    assert "svar_til" in r.json().get("detalj", ""), r.text
    r = _post(klient, tok, "/v1/fordring/avsender", {"svar_til": "a@b.no"})
    assert r.status_code == 400, r.text
    assert "avsender_navn" in r.json().get("detalj", ""), r.text
    r = _post(klient, tok, "/v1/fordring/avsender",
              {"avsender_navn": "  Fjordlys Elektro AS "})
    assert r.status_code == 200, r.text
    assert r.json()["avsender_navn"] == "Fjordlys Elektro AS"
    assert r.json()["svar_til"] is None
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT avsender_navn, svar_til FROM purreplan WHERE tenant=%s",
        (TENANT,)).fetchone()
    n = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND kilde='m23_fordring' AND handling='avsender.satt'",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert rad == ("Fjordlys Elektro AS", None) and n >= 1


# Den statiske porten på claim-veien bor i test_m44_sending_port
# (`test_claim_svaret_barer_utforelse_for_begge_eiermodulene`) siden
# ARC B kampanje PR 4: to eiermoduler, én form.
