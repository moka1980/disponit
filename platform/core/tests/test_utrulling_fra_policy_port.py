"""Porten for PR 5: et nyregistrert firma ser sine egne moduler.

MÅLT FØR DENNE: `_UTRULLING` er en statisk tuple i kildekoden med tre
pilotkunder. Kommentaren over den sier det selv — «fortsatt statisk
pilotdata». Et firma som registrerte seg selv sto ikke der, fikk
`moduler: null`, og venstremenyen viste «Modultildelingen er ikke
tilgjengelig». Kunden kom gjennom hele registreringen og landet i et skall
uten moduler — og hver ny kunde ville krevd en kodeendring og en deploy.

Det er nøyaktig symptomet eier selv målte 24/8 da plattformens egen rad
manglet; det står navngitt i `utrulling.py`.

BRANSJEMALENE NAVNGIR MODULENE SELV, i `handlinger[].modul`. Å lese dem
derfra er ikke en utledning vi finner på — det er den samme autoriteten som
styrer hva agenten får GJØRE.

MUTASJONENE SOM DREPER DISSE:
  * la `svar_for` foretrekke `nytt_firma` over `_UTRULLING`  → port 4
  * la `moduler_fra_policy` godta hvilken som helst streng    → port 2
  * fjern utledningen fra ruten                               → port 5
"""
import secrets

import pytest
import yaml

from .test_api import (DSN, MIGRATOR_DSN, app, klient,  # noqa: F401
                       migrator, miljo, pg)
from .test_m37 import _sett_kontekst

MALER = ("tjenestebedrift", "handverk-bygg", "netthandel")


def _mal(navn):
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3] / "policies"
    return yaml.safe_load((rot / f"bransjemal-{navn}.yaml")
                          .read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1-3. Utledningen selv — ren funksjon, ingen base.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("navn,forventet", [
    ("tjenestebedrift", [6, 14, 23, 24, 26]),
    ("handverk-bygg", [23, 24, 25, 26, 27, 39]),
    ("netthandel", [17, 25, 26, 27, 41, 44]),
])
def test_hver_bransjemal_gir_sitt_eget_modulsett(navn, forventet):
    """Tre distinkte sett. Var de like, ville «bransje» vært en etikett uten
    innhold — og kunden hadde valgt noe som ikke betyr noe."""
    from api.utrulling import moduler_fra_policy

    assert moduler_fra_policy(_mal(navn)) == forventet


def test_bare_ekte_modulnumre_slipper_gjennom():
    from api.utrulling import moduler_fra_policy

    p = {"handlinger": [
        {"modul": "M-14"}, {"modul": "M-14"},            # dublett
        {"modul": "M-999"},                              # tresifret er lovlig
        {"modul": "M-"}, {"modul": "modul-14"},          # feil form
        # DISSE TO SKILLER EN ANKRET REGEX FRA EN LØS. Uten dem var porten
        # grønn også med `M-?(\d+)` — altså målte den ikke det docstringen
        # påsto. (Mutasjonen falt ikke; testdataene var for snille.)
        {"modul": "M-14 fakturakontroll"},               # hale etter tallet
        {"modul": "M-1234"},                             # fire sifre
        {"modul": None}, {"modul": 14}, {},              # ikke strenger
        "ikke et objekt",
    ]}
    assert moduler_fra_policy(p) == [14, 999]
    # En policy uten handlinger er ikke en feil — den gir null moduler.
    assert moduler_fra_policy({}) == []
    assert moduler_fra_policy(None) == []
    assert moduler_fra_policy("ikke en policy") == []


def test_malene_gir_moduler_i_det_hele_tatt():
    """Positiv kontroll mot en fraværstest som går grønn på søppel: hvis
    alle tre hadde gitt tom liste, ville portene over også vært grønne om
    regexen sluttet å matche."""
    from api.utrulling import moduler_fra_policy

    for navn in MALER:
        assert len(moduler_fra_policy(_mal(navn))) >= 5, navn


# ---------------------------------------------------------------------------
# 4. Pilotdataene vinner. En håndpleid rad skal ikke overstyres i stillhet.
# ---------------------------------------------------------------------------

def test_utrullingstabellen_vinner_over_utledningen():
    from api.utrulling import egen_rad, svar_for

    pilot = egen_rad("disponit")
    assert pilot is not None, "forutsetningen holder ikke — piloten er borte"
    falsk = {"id": "disponit", "navn": "Feil", "plan": "prove",
             "moduler": [999], "neste": ""}
    svar = svar_for("disponit", [], "nb", nytt_firma=falsk)
    assert svar["moduler"] == pilot["moduler"], (
        "utledningen overstyrte en håndpleid pilotrad")


# ---------------------------------------------------------------------------
# 5. Hele veien: et nyregistrert firma får sine moduler over HTTP.
# ---------------------------------------------------------------------------

def _registrer(migrator, bransje="netthandel"):
    """Et firma som om det registrerte seg selv: firma + policy."""
    from api.firmaregistrering import _aktiver_bransjemal

    t = "t-utr-" + secrets.token_hex(3)
    _sett_kontekst(migrator, t)
    migrator.execute("SELECT firma_registrer(%s,'Nyregistrert AS',NULL,30,"
                     "'bruker:bid_x')", (t,))
    _aktiver_bransjemal(migrator, t, bransje)
    migrator.commit()
    return t


@pg
def test_nyregistrert_firma_ser_sine_moduler(migrator):
    from api.lesing import _utrulling_fra_firmaet

    t = _registrer(migrator, "netthandel")
    _sett_kontekst(migrator, t)
    rad = _utrulling_fra_firmaet(migrator, t, "nb")

    assert rad is not None, (
        "et nyregistrert firma fikk ingen utrullingsrad — venstremenyen "
        "ville sagt «Modultildelingen er ikke tilgjengelig»")
    assert rad["moduler"] == [17, 25, 26, 27, 41, 44]
    assert rad["navn"] == "Nyregistrert AS"
    # PLAN ER LIVSSYKLUSEN, ikke en pilotbetegnelse: det er det mest
    # sannferdige et nyregistrert firma kan si om seg selv.
    assert rad["plan"] == "prove"
    # Og «neste steg» er prøveperiodens frist — for en ny kunde er det
    # faktisk det neste som skjer.
    assert "Prøveperioden varer til" in rad["neste"]


@pg
def test_et_firma_uten_policy_sier_vet_ikke_ikke_ingen_moduler(migrator):
    """En flate som ikke vet, skal si det.

    Første utgave av denne porten skrev nettopp det i docstringen — og
    asserterte `moduler == []` rett under, som sier noe helt annet: at
    firmaet ER kartlagt og ikke har noen moduler. `None` er husets form,
    sagt i `egen_rad`s egen kommentar. (CodeRabbit fant motsigelsen.)
    """
    from api.lesing import _utrulling_fra_firmaet

    t = "t-utr-" + secrets.token_hex(3)
    _sett_kontekst(migrator, t)
    migrator.execute("SELECT firma_registrer(%s,'Uten policy AS',NULL,30,"
                     "'bruker:bid_x')", (t,))
    migrator.commit()
    _sett_kontekst(migrator, t)

    assert _utrulling_fra_firmaet(migrator, t, "nb") is None


@pg
def test_flere_aktive_policyer_gir_UNIONEN_av_modulene(migrator):
    """`en_aktiv_per_policy` er unik per POLICY_ID, ikke per tenant: et firma
    kan ha flere aktive serier samtidig. `LIMIT 1` ville skjult modulene i
    alle unntatt én — og kunden hadde manglet moduler policyen ga den
    fullmakt over (CodeRabbit)."""
    from api.lesing import _utrulling_fra_firmaet

    t = _registrer(migrator, "tjenestebedrift")      # M-6,14,23,24,26
    _sett_kontekst(migrator, t)
    # En andre, uavhengig serie — som en tilleggsavtale ville sett ut.
    migrator.execute(
        "INSERT INTO policyer (tenant, policy_id, versjon, innholds_hash,"
        " status, innhold, aktiv, aktiveringskilde)"
        " VALUES (%s,'tillegg','1.0.0',%s,'utkast',%s,true,'bootstrap')",
        (t, "b" * 64, '{"handlinger": [{"modul": "M-44"}]}'))
    migrator.commit()
    _sett_kontekst(migrator, t)

    rad = _utrulling_fra_firmaet(migrator, t, "nb")
    assert rad["moduler"] == [6, 14, 23, 24, 26, 44], (
        "bare én aktiv policy ble lest — LIMIT 1 er tilbake")


@pg
def test_ukjent_tenant_gir_ingen_rad(migrator):
    from api.lesing import _utrulling_fra_firmaet

    t = "t-utr-finnes-ikke"
    _sett_kontekst(migrator, t)
    assert _utrulling_fra_firmaet(migrator, t, "nb") is None


# ---------------------------------------------------------------------------
# 6. Gjennom HTTP-døra — den som måler at RUTEN bruker utledningen.
#
# Portene over kaller hjelperen direkte. De ville vært grønne selv om
# koblingen i `utrulling()` forsvant, og da hadde kunden fått `moduler: null`
# igjen uten at noe sa fra.
# ---------------------------------------------------------------------------

def _okt_som_admin(migrator, tenant):
    """Nøyaktig tilstanden etter registrering: hun er admin i sitt firma."""
    from api import sesjon as sesjonmodul

    _sett_kontekst(migrator, tenant)
    bid = migrator.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://utr.test', %s) RETURNING bruker_id",
        ("u-" + secrets.token_hex(6),)).fetchone()[0]
    migrator.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller, aktiv)"
        " VALUES (%s,%s,ARRAY['admin'],true)", (tenant, bid))
    ver = migrator.execute(
        "SELECT authz_version FROM brukermedlemskap WHERE tenant=%s"
        " AND bruker_id=%s", (tenant, bid)).fetchone()[0]
    cookie, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    migrator.execute(
        "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
        " authz_snapshot, csrf_hash, utloper)"
        " VALUES (%s,%s,%s,%s,%s, now() + interval '10 hours')",
        (sesjonmodul._hash(cookie), tenant, bid, ver,
         sesjonmodul._hash(csrf)))
    migrator.commit()
    return cookie


@pg
def test_ruten_gir_et_nyregistrert_firma_sine_moduler(miljo, migrator, klient):
    from api import sesjon as sesjonmodul

    t = _registrer(migrator, "handverk-bygg")
    cookie = _okt_som_admin(migrator, t)

    r = klient.get("/v1/utrulling?sprak=nb",
                   cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 200, r.text
    svar = r.json()
    assert svar["moduler"] == [23, 24, 25, 26, 27, 39], (
        "ruten leverte ikke firmaets egne moduler — er utledningen koblet "
        "inn i `utrulling()`?")
    # `null` er det gamle svaret, og det er nettopp det som ga
    # «Modultildelingen er ikke tilgjengelig» i venstremenyen.
    assert svar["moduler"] is not None
    assert svar["tenanter"] and svar["tenanter"][0]["plan"] == "prove"
