"""Porten mot vertsnavnet som eneste vei inn: firmaet kommer fra BRUKEREN.

Målt i prod før denne PR-en: seks firmaer har egen krypteringsnøkkel, fire
har medlemmer, og ETT kan logge inn — fordi bare ett har en rad i
`tenant_oidc_provider`. Årsaken er `_tenant_fra_host`, som tar første ledd
av vertsnavnet, og som derfor krever DNS, sertifikat og nginx-blokk per
kunde, alle tre som root.

Kjernen er at `brukermedlemskap` IKKE kan svare på «hvilket firma hører
denne brukeren til» før man allerede vet svaret: den har FORCE ROW LEVEL
SECURITY, som gjelder også tabelleieren. Migrasjon 189 speiler de aktive
medlemskapene til `bruker_tenant` uten RLS — samme form som
`brukeridentitet` og `brukersesjon`, som står uten RLS av nøyaktig samme
grunn.

MUTASJONENE SOM DREPER DISSE:
  * la `_firma_for_bruker` lese `brukermedlemskap` i stedet for speilet
    → null rader uten kontekst → hver innlogging avvist (port 1 og 3);
  * la den returnere `rader[0][0]` når det er flere → noen logges inn i
    feil firma uten å få vite det (port 4);
  * fjern `DELETE`-grenen i triggeren `bruker_tenant_speil` → et
    deaktivert medlemskap slipper fortsatt inn (port 2).
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT, pg,  # noqa: F401
                       app, klient, migrator, miljo)
from .test_m37 import _sett_kontekst

PLATTFORM = "_plattform"


def _bruker(migrator) -> str:
    """En identitet uten noe medlemskap ennå."""
    _sett_kontekst(migrator, TENANT)
    return migrator.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://innlogging189.test', %s) RETURNING bruker_id",
        ("i189-" + secrets.token_hex(6),)).fetchone()[0]


def _meld_inn(migrator, bid: str, tenant: str, *, aktiv: bool = True) -> None:
    _sett_kontekst(migrator, tenant)
    migrator.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller, aktiv)"
        " VALUES (%s,%s,%s,%s)", (tenant, bid, ["leser"], aktiv))


def _speil(migrator, bid: str) -> list[str]:
    """Oppslaget slik innloggingen gjør det: UTEN tenantkontekst."""
    migrator.execute("SELECT set_config('disponit.tenant', '', true)")
    return [r[0] for r in migrator.execute(
        "SELECT tenant FROM bruker_tenant WHERE bruker_id=%s ORDER BY tenant",
        (bid,)).fetchall()]


# ---------------------------------------------------------------------------
# 1. Hele grunnen til at speilet finnes.
# ---------------------------------------------------------------------------

def test_speilet_svarer_uten_kontekst_kilden_gjor_ikke(migrator):
    bid = _bruker(migrator)
    _meld_inn(migrator, bid, TENANT)

    assert _speil(migrator, bid) == [TENANT]

    # Samme spørsmål til KILDEN, samme tilkobling, samme (tomme) kontekst.
    # FORCE RLS gjelder også eieren: null rader, uten feilmelding. Det er
    # dette som gjør at innloggingen ikke kan spørre `brukermedlemskap`.
    migrator.execute("SELECT set_config('disponit.tenant', '', true)")
    fra_kilden = migrator.execute(
        "SELECT tenant FROM brukermedlemskap WHERE bruker_id=%s",
        (bid,)).fetchall()
    assert fra_kilden == [], (
        "kilden svarte uten kontekst — da er RLS ikke på, og hele "
        "speilets begrunnelse er borte")


# ---------------------------------------------------------------------------
# 2. Triggeren er eneste skriver, og den følger `aktiv`.
# ---------------------------------------------------------------------------

def test_triggeren_folger_aktiv_gjennom_hele_livslopet(migrator):
    bid = _bruker(migrator)
    assert _speil(migrator, bid) == [], "speilet var ikke tomt før innmelding"

    _meld_inn(migrator, bid, TENANT)
    assert _speil(migrator, bid) == [TENANT]

    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE brukermedlemskap SET aktiv=false"
                     " WHERE tenant=%s AND bruker_id=%s", (TENANT, bid))
    assert _speil(migrator, bid) == [], "deaktivert medlemskap står igjen"

    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE brukermedlemskap SET aktiv=true"
                     " WHERE tenant=%s AND bruker_id=%s", (TENANT, bid))
    assert _speil(migrator, bid) == [TENANT], "reaktivering nådde ikke speilet"

    _sett_kontekst(migrator, TENANT)
    migrator.execute("DELETE FROM brukermedlemskap"
                     " WHERE tenant=%s AND bruker_id=%s", (TENANT, bid))
    assert _speil(migrator, bid) == [], "slettet medlemskap står igjen"


# ---------------------------------------------------------------------------
# 3-5. Døra selv.
# ---------------------------------------------------------------------------

def test_ett_medlemskap_gir_firmaet(migrator):
    from api.sesjon import _firma_for_bruker
    bid = _bruker(migrator)
    _meld_inn(migrator, bid, TENANT)
    migrator.execute("SELECT set_config('disponit.tenant', '', true)")

    assert _firma_for_bruker(migrator, bid, _Ident()) == TENANT


def test_flere_medlemskap_gir_avvisning_ikke_en_gjetning(migrator):
    """Den dyre feilen: å logge noen inn i FEIL firma uten å si det.

    To firmaer som ser like ut er ikke ett — samme klasse som
    partsregisteret måtte rettes for. Døra skal si nei til den kommer en
    firmavelger, ikke velge først i alfabetet.
    """
    from api.sesjon import SesjonFeil, _firma_for_bruker
    bid = _bruker(migrator)
    _meld_inn(migrator, bid, TENANT)
    _meld_inn(migrator, bid, "t-firma-nummer-to")
    migrator.execute("SELECT set_config('disponit.tenant', '', true)")

    assert sorted(_speil(migrator, bid)) == sorted([TENANT, "t-firma-nummer-to"])
    with pytest.raises(SesjonFeil) as f:
        _firma_for_bruker(migrator, bid, _Ident())
    assert f.value.kode == "firma_ikke_valgt"
    # Og den skal ikke brenne `medlemskap`-bremsen: en bruker med to
    # arbeidsgivere har ikke gjort noe galt.
    assert f.value.http == 401


def test_ingen_medlemskap_gir_ingen_tilgang(migrator):
    from api.sesjon import SesjonFeil, _firma_for_bruker
    bid = _bruker(migrator)
    migrator.execute("SELECT set_config('disponit.tenant', '', true)")

    with pytest.raises(SesjonFeil) as f:
        _firma_for_bruker(migrator, bid, _Ident())
    assert f.value.kode == "ingen_tilgang"


class _Ident:
    """Nok av en oidc.Identitet til bremsenøkkelen.

    `sub` MÅ være unik per instans. Bremsen `medlemskap` er 5 forsøk per 15
    minutter per `issuer|sub`, og `_rate` committer — en fast `sub` ville
    delt bøtte på tvers av kjøringer, og porten ville blitt rød av sin egen
    historikk etter femte kjøring. (Den ble det: 429 i stedet for 401.)
    """
    issuer = "https://innlogging189.test"

    def __init__(self):
        self.sub = "brems-" + secrets.token_hex(8)


# ---------------------------------------------------------------------------
# 6. Bryteren selv, gjennom HTTP-døra: hvem bestemmer tenant ved /start.
#
# Dette er målingen som beviser at vertsnavnet faktisk slipper taket. De fem
# portene over måler oppslaget; denne måler at ruten BRUKER det.
# ---------------------------------------------------------------------------

def _plattformbinding(migrator, *, finnes: bool) -> None:
    from .test_pr010_db import _ctx
    _ctx(migrator, PLATTFORM)
    if finnes:
        migrator.execute(
            "INSERT INTO tenant_oidc_provider (tenant, provider_id,"
            " redirect_uris) VALUES (%s,'pflyt',ARRAY[%s])"
            " ON CONFLICT DO NOTHING",
            (PLATTFORM, "https://disponit.com/v1/oidc/callback"))
    else:
        migrator.execute("DELETE FROM tenant_oidc_provider WHERE tenant=%s",
                         (PLATTFORM,))
    # Fixturen er autocommit=False (`db.pg.koble`). Uten denne ser appen,
    # som har sin EGEN tilkobling, aldri raden — og porten ville målt at
    # bryteren ikke virket når det i virkeligheten var testen som ikke
    # hadde skrevet noe. Samme grunn som `_seed` committer.
    migrator.commit()


def _kandidat(migrator) -> str:
    from .test_pr010_db import _ctx
    _ctx(migrator, PLATTFORM)
    rad = migrator.execute(
        "SELECT tenant_kandidat FROM oidc_logintransaksjon"
        " ORDER BY utloper DESC LIMIT 1").fetchone()
    migrator.commit()
    assert rad is not None, "ingen logintransaksjon å måle — /start skrev ingen"
    return rad[0]


@pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
def test_uten_plattformraden_bestemmer_verten_som_for(klient, migrator,
                                                      monkeypatch):
    from .test_pr010_flyt import TFLYT, _seed, _start
    _seed(migrator)
    _plattformbinding(migrator, finnes=False)

    assert _start(klient, monkeypatch).status_code == 303
    assert _kandidat(migrator) == TFLYT, (
        "uten plattformraden skal vertsveien være uendret")


@pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
def test_med_plattformraden_leses_verten_ikke_i_det_hele_tatt(klient, migrator,
                                                              monkeypatch):
    """Samme forespørsel, samme vert — men nå avgjør raden, ikke vertsnavnet.

    Forespørselen kommer fortsatt fra `t-oidc-flyt.example`, altså en vert
    som PEKER på et ekte firma. Blir kandidaten likevel `_plattform`, er
    vertsnavnet ute av beslutningen — og et nytt firma trenger hverken DNS,
    sertifikat eller nginx-blokk for å kunne logge inn.
    """
    from .test_pr010_flyt import TFLYT, _seed, _start
    _seed(migrator)
    _plattformbinding(migrator, finnes=True)
    try:
        assert _start(klient, monkeypatch).status_code == 303
        assert _kandidat(migrator) == PLATTFORM, (
            "plattformraden fantes, men ruten brukte fortsatt vertsnavnet")
        assert _kandidat(migrator) != TFLYT
    finally:
        # Raden er global: la den ikke bli igjen til nabotestene.
        _plattformbinding(migrator, finnes=False)


# ---------------------------------------------------------------------------
# 8. Feilsiden sier ÉN ting mer enn før — og ikke noe utover det.
# ---------------------------------------------------------------------------

def test_feilsiden_slipper_gjennom_kun_allowlisten():
    """En bruker med to firmaer skal ikke få samme blindvei som en avvist.

    Men utvidelsen må ikke bli en generell kanal: alt som ikke står på
    allowlisten kollapses til `innlogging_feilet`. Ellers ville en fremtidig
    `SesjonFeil("ukjent_bruker")` fortalt en angriper at kontoen ikke finnes.
    """
    import json as _json

    from api.sesjon import _feil_side

    assert _json.loads(_feil_side("rid", "firma_ikke_valgt").body)["feil"] \
        == "firma_ikke_valgt"
    for lekkasje in ("ingen_tilgang", "ukjent_bruker", "rate_grense_login", ""):
        assert _json.loads(_feil_side("rid", lekkasje).body)["feil"] \
            == "innlogging_feilet", f"{lekkasje!r} slapp gjennom"
    assert _json.loads(_feil_side("rid").body)["feil"] == "innlogging_feilet"


# ---------------------------------------------------------------------------
# 9. Den reserverte navneplassen er beskyttet av kode, ikke av en kommentar.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("host,forventet", [
    ("t-oidc-flyt.example", "t-oidc-flyt"),
    ("disponit.com", "disponit"),
    ("_plattform.example", ""),      # reservert — aldri et firma
    ("_oidc.example", ""),           # den andre reserverte konteksten
    ("_.example", ""),
    ("", ""),
])
def test_verten_kan_aldri_gi_en_reservert_tenant(host, forventet):
    """`init-tenant.sh` validerer ikke tenantnavnet, og `X-Disponit-Host`
    kommer fra en nginx-mal. Uten denne vakten hviler hele skillet mellom
    plattformkontekst og kundekontekst på at ingen skriver feil.
    """
    from api.sesjon import _tenant_fra_host

    class _Req:
        headers = {"x-disponit-host": host}

    assert _tenant_fra_host(None, _Req()) == forventet


# ---------------------------------------------------------------------------
# 10. Oppslaget gjennom RUNTIME-rollen — veien innloggingen faktisk går.
# ---------------------------------------------------------------------------

@pg
def test_runtime_kan_faktisk_slaa_opp_firmaet(migrator):
    """Porten som manglet, og feilen den ville fanget.

    189 la `GRANT SELECT ON bruker_tenant TO disponit` i migrasjonen. Men
    `deploy/staging/migrer.py` kjører `NULLSTILL_TABELLER` ETTER
    migrasjonene — den trekker tilbake alt runtime har på hver tabell
    migrator eier — og gir så tilbake fra en kanonisk liste. Grantet ble
    visket ut av neste steg i samme deploy, stille.

    Prod sto med `INGEN` rettigheter for runtime på tabellen. Ingen merket
    det, fordi den delte påloggingen ikke var skrudd på ennå — og fordi de
    andre portene her kaller døra som MIGRATOR, en vei ingen ekte kaller
    går. Samme klasse som «alle testene mine brukte Bearer-tokens».

    Grantet hører derfor hjemme i `RETTIGHETER`, ikke i migrasjonen.
    """
    from db.pg import koble
    from api.sesjon import _firma_for_bruker

    bid = _bruker(migrator)
    _meld_inn(migrator, bid, TENANT)
    migrator.commit()

    c = koble(DSN)
    try:
        c.execute("SELECT set_config('disponit.tenant', '', true)")
        # Nøyaktig samme kall som `_opprett_sesjon` gjør i callbacken.
        assert _firma_for_bruker(c, bid, _Ident()) == TENANT
    finally:
        c.close()
