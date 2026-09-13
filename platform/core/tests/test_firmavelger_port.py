"""Porten for 196: to medlemskap skal ikke låse noen ute.

MÅLT FØR DENNE: `_firma_for_bruker` svarte `firma_ikke_valgt` når en
identitet hørte til mer enn ett firma — og det gjaldt BEGGE. En ansatt som
fikk et medlemskap nummer to mistet tilgangen til firmaet hun alt jobbet i.

Begrunnelsen var god: å velge det første alfabetisk ville logget noen inn i
feil firma uten å si det. Men prisen var at et helt bruksmønster ikke fantes
— regnskapsføreren med to klienter, daglig leder i to selskaper, den som
blir invitert av en kunde.

ØNSKET ER EN PREFERANSE, ALDRI EN FULLMAKT. Det følger med inn i
innloggingsrunden fra `/v1/oidc/start`, og callbacken bruker det BARE hvis
medlemskapet finnes. En fremmed som gjetter et firmanavn har ikke kommet
nærmere noe — hun må uansett gjennom leverandøren og eie raden.

MUTASJONENE SOM DREPER DISSE:
  * la ønsket gjelde uten medlemskapssjekk        → port 3 (den viktigste)
  * bytt alfabetisk rekkefølge mot vilkårlig      → port 4
  * la `_firmaene_hennes` ta med `_registrering`  → port 6
"""
import secrets

import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo, pg  # noqa: F401
from .test_m37 import _sett_kontekst


def _bruker(c, merke="v"):
    _sett_kontekst(c, "t-velg-oppsett")
    bid = c.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://velg.test', %s) RETURNING bruker_id",
        (f"{merke}-" + secrets.token_hex(6),)).fetchone()[0]
    c.commit()
    return bid


def _meld_inn(c, bid, tenant):
    _sett_kontekst(c, tenant)
    c.execute("INSERT INTO brukermedlemskap (tenant, bruker_id, roller,"
              " aktiv) VALUES (%s,%s,ARRAY['leser'],true)", (tenant, bid))
    c.commit()


class _Ident:
    issuer = "https://velg.test"

    def __init__(self):
        self.sub = "s-" + secrets.token_hex(6)


# ---------------------------------------------------------------------------
# 1-2. Blindveien er borte.
# ---------------------------------------------------------------------------

@pg
def test_to_medlemskap_laaser_ikke_lenger_ute(migrator):
    from api.sesjon import _firma_for_bruker

    bid = _bruker(migrator)
    a, b = "t-velg-aaa", "t-velg-bbb"
    for t in (a, b):
        _meld_inn(migrator, bid, t)
    migrator.execute("SELECT set_config('disponit.tenant','',true)")

    # Før 196: SesjonFeil('firma_ikke_valgt') — låst ute av BEGGE.
    assert _firma_for_bruker(migrator, bid, _Ident()) == a


@pg
def test_ett_medlemskap_er_uendret(migrator):
    """Positiv kontroll: 196 skal ikke røre den vanlige veien."""
    from api.sesjon import _firma_for_bruker

    bid = _bruker(migrator)
    _meld_inn(migrator, bid, "t-velg-ene")
    migrator.execute("SELECT set_config('disponit.tenant','',true)")
    assert _firma_for_bruker(migrator, bid, _Ident()) == "t-velg-ene"


# ---------------------------------------------------------------------------
# 3. Den viktigste: ønsket er en preferanse, ikke en fullmakt.
# ---------------------------------------------------------------------------

@pg
def test_onsket_gjelder_bare_der_medlemskapet_finnes(migrator):
    """Uten denne sjekken ville et firmanavn i en POST-kropp vært nok til å
    havne i et fremmed firma. Ønsket er en PREFERANSE; medlemskapet er
    autoriteten."""
    from api.sesjon import _firma_for_bruker

    bid = _bruker(migrator)
    a, b = "t-velg-ccc", "t-velg-ddd"
    for t in (a, b):
        _meld_inn(migrator, bid, t)
    migrator.execute("SELECT set_config('disponit.tenant','',true)")

    # Ønsket hun HAR: respekteres, selv om det ikke er først alfabetisk.
    assert _firma_for_bruker(migrator, bid, _Ident(), onske=b) == b
    # Et firma hun IKKE er medlem i: ignoreres fullstendig.
    assert _firma_for_bruker(migrator, bid, _Ident(),
                             onske="t-et-fremmed-firma") == a
    # Og et ønske kan aldri gi tilgang til noe hun ikke har.
    assert _firma_for_bruker(migrator, bid, _Ident(),
                             onske="_plattform") == a


@pg
def test_uten_onske_er_valget_forutsigbart(migrator):
    """Alfabetisk, og dermed det SAMME hver gang. Et vilkårlig valg ville
    sendt henne til ulike firmaer på ulike innlogginger — verre enn et
    forutsigbart feil valg hun kan rette med ett klikk."""
    from api.sesjon import _firma_for_bruker

    bid = _bruker(migrator)
    for t in ("t-velg-zzz", "t-velg-mmm", "t-velg-eee"):
        _meld_inn(migrator, bid, t)
    migrator.execute("SELECT set_config('disponit.tenant','',true)")

    valg = {_firma_for_bruker(migrator, bid, _Ident()) for _ in range(5)}
    assert valg == {"t-velg-eee"}, f"valget varierte: {valg}"


# ---------------------------------------------------------------------------
# 5-6. Lista skallet tegner bytteren av.
# ---------------------------------------------------------------------------

@pg
def test_sesjonen_sier_hvilke_firmaer_hun_har(migrator):
    from api.sesjon import _firmaene_hennes

    bid = _bruker(migrator)
    for t in ("t-velg-nnn", "t-velg-kkk"):
        _meld_inn(migrator, bid, t)
    migrator.execute("SELECT set_config('disponit.tenant','',true)")
    assert _firmaene_hennes(migrator, bid) == ["t-velg-kkk", "t-velg-nnn"]


@pg
def test_reserverte_kontekster_er_ikke_firmaer(migrator):
    """`_registrering` er et sted å stå mens hun registrerer, ikke et firma.
    Sto den i lista, ville bytteren tilbudt henne å «bytte til» en
    plattformkontekst — og skallet ville tegnet en meny med et navn ingen
    kjenner."""
    from api.sesjon import _firmaene_hennes

    bid = _bruker(migrator)
    _meld_inn(migrator, bid, "t-velg-ppp")
    # RADEN SETTES DIREKTE, ikke gjennom `registrant_medlemskap`: den døra
    # svarer `false` for en som alt har et firma (vernet fra 192), så
    # tilstanden kan ikke oppstå den veien. Men den kan oppstå — en
    # plattformeier har en rad på `_plattform` OG kan være ansatt et sted —
    # og filteret finnes for nettopp det.
    _sett_kontekst(migrator, "_registrering")
    migrator.execute("INSERT INTO brukermedlemskap (tenant, bruker_id,"
                     " roller, aktiv) VALUES ('_registrering',%s,"
                     "ARRAY['registrant'],true)", (bid,))
    migrator.commit()
    migrator.execute("SELECT set_config('disponit.tenant','',true)")

    # Speilet HAR begge …
    alle = {r[0] for r in migrator.execute(
        "SELECT tenant FROM bruker_tenant WHERE bruker_id=%s",
        (bid,)).fetchall()}
    assert "_registrering" in alle
    # … men lista til skallet har bare firmaet.
    assert _firmaene_hennes(migrator, bid) == ["t-velg-ppp"]
