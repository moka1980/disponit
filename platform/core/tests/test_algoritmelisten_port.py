"""197: en TOM algoritmeliste slår av algoritmepinningen — stille.

MÅLT 13/9, i denne rekkefølgen, før noe ble skrevet:

  1. `INSERT INTO oidc_provider (… tillatte_algoritmer) VALUES (… '{}')`
     BLE AKSEPTERT av basen. CHECK-en fra 010 var
     `array_length(tillatte_algoritmer,1) >= 1`; for et tomt array er
     `array_length` NULL, `NULL >= 1` er NULL, og en CHECK SLIPPER NULL
     GJENNOM. Samme feilklasse som 194 (`roller`) og 182 (`NULL !~ mønster`).
  2. `jwt.decode(rs256_token, nøkkel, algorithms=[])` AKSEPTERTE tokenet.
     Med `algorithms=["HS256"]` ble det samme tokenet avvist med
     `UnsupportedAlgorithmError`. joserfc leser en tom liste som FRAVÆR av
     begrensning, ikke som «ingenting tillatt».
  3. `alg=none` avvises av biblioteket selv, så dette er ikke full
     signaturomgåelse — men vernet mot algoritmeforvirring er borte, og
     det vernet er hele grunnen til at kolonnen finnes.

Ingen kunde skriver disse tabellene. Men provider-rader skrives FOR HÅND på
verten, og en tom liste er nøyaktig den tastefeilen CHECK-en finnes for.

TO LAG, TO PORTER: basen skal nekte raden, og laget som BRUKER lista skal
nekte den uansett hvor den kom fra.
"""
import pytest

from api import oidc
from .test_api import MIGRATOR_DSN, migrator, miljo  # noqa: F401

pg = pytest.mark.skipif(not MIGRATOR_DSN,
                        reason="DISPONIT_TEST_MIGRATOR_DSN ikke satt")


class _Avbryt(Exception):
    """Ruller tilbake en INSERT som lyktes men IKKE skulle lykkes.

    Malt: forste form brukte bare `pytest.raises(CheckViolation)` rundt en
    `with conn.transaction()`. Under mutasjonen kastet INSERT-en ingenting,
    transaksjonen COMMITTET, og raden med tom liste ble liggende igjen i
    basen — der den sa blokkerte gjenopprettingen av selve CHECK-en. En port
    som forurenser basen nar den feiler, gjor neste kjoring tilstandsavhengig.
    """


def _ctx(conn, tenant):
    """`tenant_oidc_provider` har RLS, og FORCE gjelder ogsa migrator — uten
    kontekst svarer doren `InsufficientPrivilege` FOR CHECK-en rekker a kjore.
    Malt: uten denne linjen falt porten pa feil grunn og hadde bevist ingenting.
    """
    conn.execute("SELECT set_config('disponit.tenant',%s,true),"
                 "       set_config('disponit.aktor','test',true)", (tenant,))


# ---------------------------------------------------------------------------
# Lag 1: basen
# ---------------------------------------------------------------------------

@pg
def test_basen_nekter_tom_algoritmeliste(migrator):  # noqa: F811
    """Mutasjon som feller: bytt `cardinality` tilbake til `array_length`."""
    import psycopg

    try:
        with migrator.transaction():
            migrator.execute(
                "INSERT INTO oidc_provider (provider_id, issuer,"
                " discovery_url, client_id, client_secret_ref,"
                " tillatte_algoritmer)"
                " VALUES ('p-tomliste','https://tomliste.example',"
                "         'https://tomliste.example/.well-known','cid','ref',"
                "         '{}')")
            raise _Avbryt()
    except psycopg.errors.CheckViolation:
        pass                      # doren nektet — det er dommen vi vil ha
    except _Avbryt:
        pytest.fail("tom algoritmeliste ble AKSEPTERT av basen")


@pg
def test_basen_nekter_tom_redirect_liste(migrator):  # noqa: F811
    """Samme hull, samme kur, andre tabell: en tenantkobling uten en eneste
    redirect-URI ville blitt akseptert av `array_length`-formen."""
    import psycopg

    migrator.execute(
        "INSERT INTO oidc_provider (provider_id, issuer, discovery_url,"
        " client_id, client_secret_ref, tillatte_algoritmer)"
        " VALUES ('p-uri','https://uri.example',"
        "         'https://uri.example/.well-known','cid','ref',"
        "         ARRAY['RS256'])"
        " ON CONFLICT (provider_id) DO NOTHING")
    migrator.commit()
    try:
        try:
            with migrator.transaction():
                _ctx(migrator, "t-uri")
                migrator.execute(
                    "INSERT INTO tenant_oidc_provider (tenant, provider_id,"
                    " redirect_uris) VALUES ('t-uri','p-uri','{}')")
                raise _Avbryt()
        except psycopg.errors.CheckViolation:
            pass
        except _Avbryt:
            pytest.fail("tom redirect-liste ble AKSEPTERT av basen")
    finally:
        # `finally`, ikke etter assert-en (CodeRabbit): faller porten, skal
        # raden likevel bort. Koblingen FØR provideren — FK-en peker den
        # veien. Malt i denne okten: en etterlatt rad blokkerte
        # gjenopprettingen av selve CHECK-en i mutasjonsrunden.
        _ctx(migrator, "t-uri")
        migrator.execute("DELETE FROM tenant_oidc_provider"
                         " WHERE provider_id='p-uri'")
        migrator.execute("DELETE FROM oidc_provider WHERE provider_id='p-uri'")
        migrator.commit()


@pg
def test_en_ekte_liste_slipper_fortsatt_inn(migrator):  # noqa: F811
    """POSITIV KONTROLL. Uten denne kunne en CHECK som nektet ALT sett like
    grønn ut som den riktige — en fraværstest går grønn på søppel."""
    migrator.execute(
        "INSERT INTO oidc_provider (provider_id, issuer, discovery_url,"
        " client_id, client_secret_ref, tillatte_algoritmer)"
        " VALUES ('p-ekte','https://ekte.example',"
        "         'https://ekte.example/.well-known','cid','ref',"
        "         ARRAY['RS256'])"
        " ON CONFLICT (provider_id) DO NOTHING")
    _ctx(migrator, "t-ekte")
    migrator.execute(
        "INSERT INTO tenant_oidc_provider (tenant, provider_id, redirect_uris)"
        " VALUES ('t-ekte','p-ekte',ARRAY['https://ekte.example/cb'])"
        " ON CONFLICT DO NOTHING")
    migrator.commit()
    try:
        # Konteksten er TRANSAKSJONSLOKAL (`set_config(...,true)`) —
        # `commit()` over kastet den, og uten denne linjen teller vi 0 og tror
        # CHECK-en nektet en gyldig rad. Malt: porten falt her forst, pa feil
        # grunn.
        _ctx(migrator, "t-ekte")
        n = migrator.execute(
            "SELECT count(*) FROM oidc_provider p JOIN tenant_oidc_provider t"
            "  ON t.provider_id=p.provider_id WHERE p.provider_id='p-ekte'"
        ).fetchone()[0]
        assert n == 1, "en gyldig provider skal fortsatt kunne opprettes"
    finally:
        _ctx(migrator, "t-ekte")
        migrator.execute("DELETE FROM tenant_oidc_provider"
                         " WHERE provider_id='p-ekte'")
        migrator.execute("DELETE FROM oidc_provider"
                         " WHERE provider_id='p-ekte'")
        migrator.commit()


# ---------------------------------------------------------------------------
# Lag 2: koden som BRUKER lista
# ---------------------------------------------------------------------------

def test_provider_nekter_tom_liste_uansett_hvor_raden_kom_fra():
    """Mutasjon som feller: fjern `__post_init__` fra `Provider`.

    Basen er ikke det eneste stedet en Provider kan bli til — testrigger,
    fremtidige kilder og en rad eldre enn 197 finnes alle. Vernet må stå der
    lista BRUKES, ikke bare der den lagres.
    """
    with pytest.raises(oidc.OidcFeil) as ei:
        oidc.Provider("p", "https://i.example", "https://i.example/.well-known",
                      "cid", "hemmelig", tillatte_algoritmer=())
    assert "algoritme" in str(ei.value).lower()


def test_en_ekte_provider_bygges_fortsatt():
    """POSITIV KONTROLL for vernet over."""
    p = oidc.Provider("p", "https://i.example", "https://i.example/.well-known",
                      "cid", "hemmelig", tillatte_algoritmer=("RS256",))
    assert p.tillatte_algoritmer == ("RS256",)


def test_tom_liste_betyr_alt_tillatt_i_biblioteket():
    """DOKUMENTASJONSPORT — måler PREMISSET, ikke vår egen kode.

    Hele 197 hviler på at `algorithms=[]` er «ingen begrensning». Skulle
    joserfc en dag snu om på det, er vernet ikke lenger nødvendig av den
    grunnen — og da skal denne testen si fra, ikke stå igjen som en påstand
    ingen lenger måler.
    """
    from joserfc import jwt as _jwt
    from joserfc.jwk import RSAKey

    n = RSAKey.generate_key(2048)
    token = _jwt.encode({"alg": "RS256"}, {"iss": "a"}, n)

    _jwt.decode(token, n, algorithms=[])          # tom liste: slipper gjennom
    with pytest.raises(Exception):
        _jwt.decode(token, n, algorithms=["HS256"])   # ikke-tom: virker
