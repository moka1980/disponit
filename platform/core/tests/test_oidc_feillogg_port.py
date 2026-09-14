"""En feilet innlogging skal ETTERLATE ET SPOR.

MÅLT I PROD 13/9: en ekte Microsoft-innlogging feilet, brukeren fikk
`innlogging_feilet`, og journalen hadde IKKE ÉN LINJE. Ingen av de fem
`_feil_side`-veiene i callbacken kalte loggeren. Svaret måtte graves ut av
`oidc_logintransaksjon` — en vei som finnes for den som har psql og kjenner
skjemaet, ikke for den som drifter klokka tre om natta.

DE TO TINGENE SKAL VÆRE ULIKE, OG DET ER HELE POENGET:
* UTAD er svaret generisk (v5 §6). Det skiller aldri «ukjent bruker» fra
  «feil token» — å skille dem er å la noen prøve seg fram.
* I LOGGEN skal det stå nøyaktig hva som skjedde.

Porten måler begge halvdelene. Uten den andre kunne «fiksen» vært å vise
grunnen til brukeren, og det ville vært en sikkerhetsregresjon.
"""
import secrets

import pytest

from .test_api import DSN, MIGRATOR_DSN, app, klient, miljo  # noqa: F401

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _egen_ip():
    """En UNIK avsender-IP per kall.

    Rate-grensen nøkles på `X-Forwarded-For` (`_ip_prefiks`), og uten dette
    delte alle kjøringene én bøtte: etter nok kjøringer svarte hver test
    `rate_grense_login` i stedet for det den skulle måle. Jeg oppdaget det
    midt i en mutasjonsrunde der ALT var rødt — også etter gjenoppretting.
    En rød som ikke er funnet er verre enn ingen rød.

    Formen er produksjonens egen: nginx setter headeren, og nøkkelen er
    /32 per klient.
    """
    return {"x-forwarded-for": "198.51.100." + str(secrets.randbelow(254) + 1)}


def _linjer(app):
    return list(app.tjeneste.logg.linjer)


def _nye(app, foer):
    return _linjer(app)[len(foer):]


@pg
def test_callback_uten_parametere_etterlater_et_spor(app, klient):  # noqa: F811
    """DEN ENKLESTE FEILVEIEN, og den som var taus.

    MUTASJON SOM FELLER: fjern `_logg_callbackfeil`-kallet fra grenen.
    """
    foer = _linjer(app)
    r = klient.get("/v1/oidc/callback", headers=_egen_ip())
    assert r.status_code == 400, r.text

    nye = _nye(app, foer)
    assert nye, "en feilet innlogging skrev ikke én eneste logglinje"
    sisteste = nye[-1]
    assert sisteste.get("grunn") == "mangler_parametere", \
        f"loggen sier ikke HVA som manglet: {sisteste}"


@pg
def test_ugyldig_state_etterlater_et_spor(app, klient):  # noqa: F811
    """En state ingen kjenner — den vanligste formen for et gjenbrukt
    eller utløpt tilbakekall."""
    # KONSTANTEN FRA MODULEN, ikke navnet skrevet av for haand. Foerste
    # utkast gjettet `__Host-disponit_oidcbinding`; den heter
    # `__Host-disponit_oidc`. Cookien kom da aldri fram, og testen falt i
    # «mangler_parametere»-grenen — altsaa i en HELT ANNEN vei enn den den
    # paastod aa maale.
    from api import sesjon as sesjonmodul

    foer = _linjer(app)
    r = klient.get("/v1/oidc/callback",
                   params={"code": "c-" + secrets.token_hex(4),
                           "state": "s-" + secrets.token_hex(8)},
                   cookies={sesjonmodul.C_BINDING:
                            "b-" + secrets.token_hex(8)},
                   headers=_egen_ip())
    assert r.status_code == 400, r.text
    nye = _nye(app, foer)
    assert any(l.get("grunn") == "state_ikke_gyldig" for l in nye), \
        f"loggen sier ikke at state-en var ugyldig: {nye}"


@pg
def test_svaret_UTAD_er_fortsatt_generisk(app, klient):  # noqa: F811
    """POSITIV KONTROLL — og vernet mot en «fiks» som lekker.

    Loggen skal si hvorfor; SVARET skal ikke. Uten denne kunne man ha
    «løst» taushetsproblemet ved å vise grunnen til brukeren, og da hadde
    den som prøver seg fram fått et orakel.

    MUTASJON SOM FELLER: la `_feil_side` returnere `grunn` i kroppen.
    """
    r = klient.get("/v1/oidc/callback", headers=_egen_ip())
    assert r.status_code == 400
    kropp = r.json()
    assert kropp == {"feil": "innlogging_feilet"}, \
        f"svaret bærer mer enn den generiske koden: {kropp}"
    for lekkasje in ("mangler_parametere", "state_ikke_gyldig", "grunn",
                     "ingen_tilgang", "token_"):
        assert lekkasje not in r.text, f"svaret lekker «{lekkasje}»"


@pg
def test_loggingen_velter_ikke_innloggingsveien(app, klient):  # noqa: F811
    """En logger som kaster skal ikke gjøre en feilside til en 500.

    En tapt linje er mindre verdt enn en tapt innlogging — og en
    feilhåndtering som selv kan feile er ikke en feilhåndtering.

    MUTASJON SOM FELLER: fjern `try/except` rundt `hendelse`-kallet.
    """
    ekte = app.tjeneste.logg.hendelse

    def kaster(*a, **k):
        raise RuntimeError("loggen er nede")

    app.tjeneste.logg.hendelse = kaster
    try:
        r = klient.get("/v1/oidc/callback", headers=_egen_ip())
        assert r.status_code == 400, \
            f"en feilende logger endret svaret: {r.status_code} {r.text}"
        assert r.json() == {"feil": "innlogging_feilet"}
    finally:
        app.tjeneste.logg.hendelse = ekte
