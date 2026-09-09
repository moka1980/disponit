"""Porten for ARC B, PR 8: inkassovarsel er et menneskes bestilling —
aldri agentens; inkasso sendes aldri av systemet (eiervedtak 9/9, valg 2).

  1. Policyen bærer valget: utvidelsen `policies/utvidelser/
     purring-inkassovarsel.yaml` har `purring.send.inkassovarsel` (samme
     oppdragstype som `purring.send` — segmentprefikset), tillatt for
     `bestiller`, aldri `agent`. Bransjemalen røres ikke (M-02-bindingen).
  2. Utløseren: en fordring hvis neste trinn er inkassovarsel bokføres
     «krever et menneske» — ingen beslutning, ingen sak, intet oppdrag;
     runde to ser den ikke igjen. Samme for `inkasso`.
  3. Selv om agenten prøvde (utlos_en direkte): policyen svarer brudd
     `rolle_ikke_tillatt`.
  4. Et menneske bestiller fra flaten: bestillingsveien bytter handling
     til `purring.send.inkassovarsel`, policyen gir tillat, oppdraget
     bærer den handlingen, modulen sender `inkassovarsel-v1`, kvitteringen
     flytter fordringen til trinnet.
  5. Flaten bærer nok: `GET …/hendelser` viser `menneske_kreves` for
     trinnet, og adressen står ingen steder.

MUTASJONER SOM DREPER DENNE: la utløseren bestille inkassovarsel (2),
sett `tillatt_for: [agent]` på handlingen i utvidelsen (1/3), eller
la bestillingsveien beholde `purring.send` som handling (4).
"""
import secrets

import pytest
import yaml as _yaml

from .test_api import (DSN, MIGRATOR_DSN, POLICIES, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_m23_fordring import PLAN, _fordring, _plan, _rt, _trinn
from .test_m23_purringsutloser_port import (_bransjemal, _kandidat_fider,
                                            _mottaker, _pa, _runde, _sveip)
from .test_m23_sending_port import _Sender, _oppdrag, _post, _release
from .test_m37 import _sett_kontekst, _signer_kvittering
from .test_modul_onboarding_http import _onboard_token

PLAN_DSN = __import__("os").environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")
PLAN4 = PLAN + [{"navn": "Inkasso", "dogn_etter_forfall": 42,
                 "handling": "inkasso", "gebyr_ore": 0}]


def test_policyen_barer_valget():
    from api.bestilling import INKASSOVARSEL_HANDLING
    from oppdragskontrakt import type_for_handling
    p = _yaml.safe_load((POLICIES / "bransjemal-tjenestebedrift.yaml")
                        .read_text(encoding="utf-8"))
    utv = _yaml.safe_load((POLICIES / "utvidelser"
                           / "purring-inkassovarsel.yaml")
                          .read_text(encoding="utf-8"))
    h = {x["id"]: x for x in p["handlinger"] + utv["handlinger"]}
    assert INKASSOVARSEL_HANDLING == "purring.send.inkassovarsel"
    # Utvidelsen er en EGEN fil: bransjemalen er byte-bundet til M-02s
    # akseptartefakt (M02_BEVISROT_FILER) og skal ikke endres av dette.
    assert INKASSOVARSEL_HANDLING not in {x["id"] for x in p["handlinger"]}
    assert [r["id"] for r in utv["roller"]] == ["bestiller"]
    ink = h[INKASSOVARSEL_HANDLING]
    assert ink["tillatt_for"] == ["bestiller"], ink["tillatt_for"]
    assert "agent" not in ink["tillatt_for"]
    assert h["purring.send"]["tillatt_for"] == ["agent"]
    assert ink["modus"] == "auto" and ink["ved_brudd"] == "unntakskø"
    assert [v["navn"] for v in ink["vilkaar"]] == \
        [v["navn"] for v in h["purring.send"]["vilkaar"]]
    # Segmentprefikset: handlingen hører til purring.send-typen, og en
    # handling uten segmentgrense gjør det IKKE (reinnsending).
    assert type_for_handling(INKASSOVARSEL_HANDLING).navn == "purring.send"
    assert type_for_handling("purring.send_inkassovarsel").navn \
        == "reinnsending"


def _fordring_paa_trinn(trinn: int, *, forfall_siden: int, plan=None,
                        med_mottaker=True):
    c = _rt()
    try:
        _plan(c, TENANT, plan)
        fid = _fordring(c, TENANT, forfall_siden=forfall_siden)
        for _ in range(trinn):
            _trinn(c, TENANT, fid)
        if med_mottaker:
            _mottaker(c, TENANT, fid)
    finally:
        c.close()
    _sveip()
    return fid


def _bokfort(migrator, fid):
    _sett_kontekst(migrator, TENANT)
    r = migrator.execute(
        "SELECT trinn, handling_trinn, utfall, oppdrag_id, unntak_id,"
        " detalj->>'grunn' FROM purringsbestilling WHERE tenant=%s"
        " AND fordring_id=%s ORDER BY trinn", (TENANT, fid)).fetchall()
    migrator.rollback()
    return r


@pg_plan
def test_utloseren_bokforer_krever_et_menneske_og_bestiller_aldri(
        migrator, miljo, app):
    _bransjemal(migrator)
    fid_ink = _fordring_paa_trinn(2, forfall_siden=40)     # neste: 3 = inkassovarsel
    fid_inkasso = _fordring_paa_trinn(3, forfall_siden=50, plan=PLAN4)
    pa = _pa()
    try:
        k = _kandidat_fider(pa)
        assert (TENANT, str(fid_ink), 3) in k and (TENANT, str(fid_inkasso), 4) in k
        _sett_kontekst(migrator, TENANT)
        for_ = migrator.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
            " AND handling LIKE 'purring.send%%'", (TENANT,)).fetchone()[0]
        migrator.rollback()
        res = _runde(app, pa)
        mine = {r["fordring"]: r for r in res["resultater"]}
        assert mine[str(fid_ink)]["utfall"] == "menneske_kreves"
        assert mine[str(fid_ink)]["grunn"] == "inkassovarsel_krever_menneske"
        assert mine[str(fid_inkasso)]["grunn"] == "inkasso_aldri_automatisk"
        assert mine[str(fid_ink)]["bokfort"] and mine[str(fid_inkasso)]["bokfort"]
        assert _bokfort(migrator, fid_ink) == [
            (3, "inkassovarsel", "menneske_kreves", None, None,
             "inkassovarsel_krever_menneske")]
        assert _bokfort(migrator, fid_inkasso)[-1][2] == "menneske_kreves"
        # Ingen beslutning brent, ingen sak, intet oppdrag.
        _sett_kontekst(migrator, TENANT)
        etter = migrator.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
            " AND handling LIKE 'purring.send%%'", (TENANT,)).fetchone()[0]
        migrator.rollback()
        assert etter == for_
        # Runde to: ikke kandidat lenger.
        k = _kandidat_fider(pa)
        assert (TENANT, str(fid_ink), 3) not in k
        assert (TENANT, str(fid_inkasso), 4) not in k
        # …og prøvde agenten LIKEVEL (utlos_en rett på kandidaten), svarer
        # policyen nei — rollen er ikke tillatt.
        from plan.purring import utlos_en
        r = utlos_en(app.tjeneste, pa,
                     (TENANT, fid_ink, 3, "inkassovarsel", 40))
        assert r["utfall"] == "brudd", r
        _sett_kontekst(migrator, TENANT)
        rad = migrator.execute(
            "SELECT begrunnelse FROM revisjonslogg WHERE tenant=%s"
            " AND handling='purring.send.inkassovarsel'"
            " ORDER BY id DESC LIMIT 1", (TENANT,)).fetchone()
        migrator.rollback()
        assert rad and "rolle_ikke_tillatt" in str(rad[0]), rad
    finally:
        pa.close()


@pg_plan
def test_et_menneske_bestiller_inkassovarselet_og_modulen_sender_det(
        migrator, miljo, app, klient, token):
    from modules.m23_fordring import controller
    from .test_bestilling_purring_port import _bestill
    from .test_outbox_bestilling import _adminsesjon
    controller._sov = lambda s: None
    _bransjemal(migrator)
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "okonomi:read"))
    adresse = "k-" + secrets.token_hex(3) + "@nordvik.example"
    fid = _fordring_paa_trinn(2, forfall_siden=40, med_mottaker=False)
    r = _post(klient, tok, f"/v1/fordring/{fid}/mottaker",
              {"mottaker_epost": adresse})
    assert r.status_code == 200, r.text
    _sveip()
    pa = _pa()
    try:
        _runde(app, pa)                       # bokfører «krever et menneske»
    finally:
        pa.close()
    hode = {"authorization": f"Bearer {tok}"}
    r = klient.get(f"/v1/fordring/{fid}/hendelser", headers=hode)
    p = r.json()["purringer"]
    assert p and p[-1]["utfall"] == "menneske_kreves" \
        and p[-1]["handling_trinn"] == "inkassovarsel" and p[-1]["trinn"] == 3
    assert adresse not in r.text
    # Mennesket bestiller (samme kropp som alltid — ingen trinn, ingen
    # handling): tillat, og oppdraget bærer inkassovarsel-handlingen.
    cookie, csrf = _adminsesjon()
    r = _bestill(klient, cookie, csrf, fid)
    assert r.status_code == 200 and r.json()["beslutning"] == "tillat", r.text
    oid = r.json()["oppdrag_id"]
    _sett_kontekst(migrator, TENANT)
    handling = migrator.execute(
        "SELECT handling FROM oppdrag WHERE tenant=%s AND id=%s",
        (TENANT, oid)).fetchone()[0]
    logg = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND handling='purring.send.inkassovarsel' AND beslutning='TILLAT'",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert handling == "purring.send.inkassovarsel" and logg >= 1
    # Modulen claimer og sender inkassovarselet i tenantens navn.
    mtk, _ = _onboard_token(klient, migrator, "m23_fordring",
                            _release(migrator))
    sender = _Sender()
    ut = controller.kjor_en(klient, mtk, sender, _signer_kvittering)
    assert ut["utfall"] == "utfort", ut
    assert len(sender.sendt) == 1 and sender.sendt[0]["til"] == adresse
    assert sender.sendt[0]["emne"].startswith("Inkassovarsel")
    assert "inkassoloven" in sender.sendt[0]["tekst"]
    status, kv, _, _ = _oppdrag(migrator, oid)
    assert status == "utfort" and kv["malversjon"] == "inkassovarsel-v1"
    _sett_kontekst(migrator, TENANT)
    trinn = migrator.execute(
        "SELECT trinn FROM fordring WHERE tenant=%s AND fordring_id=%s",
        (TENANT, fid)).fetchone()[0]
    migrator.rollback()
    assert trinn == 3
