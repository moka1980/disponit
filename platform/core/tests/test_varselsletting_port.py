"""Sletting av varsler. Eier: «det blir mange dag etter dag.»

MÅLT FØR DETTE: innboksen hadde ingen vei ut. `merk_lest` flyttet et varsel
nedover i lista, men ingenting fjernet det — og med flere varsler hver dag er
en innboks uten sletting en liste som bare vokser.

DET FARLIGE ER IKKE SLETTINGEN, DET ER HVEM SOM FÅR SLETTE.
RLS skiller tenanter, ikke mennesker inne i samme tenant. Uten `bruker_id` i
WHERE kunne én kollega slettet en annens varsel — og dermed skjult at noe
ventet på henne, denne gangen uten at det engang sto igjen som lest.
"""
import secrets

import pytest

from db.pg import koble
from .test_api import DSN, MIGRATOR_DSN, migrator, miljo  # noqa: F401

pg = pytest.mark.skipif(not (DSN and MIGRATOR_DSN), reason="test-DSN ikke satt")

T = "t-varselslett"


def _ctx(conn, tenant=T):
    conn.execute("SELECT set_config('disponit.tenant',%s,true),"
                 "       set_config('disponit.aktor','test',true)", (tenant,))


def _varsel(conn, bruker_id, *, epost_status="koet"):
    _ctx(conn)
    return conn.execute(
        "INSERT INTO varsel (tenant, bruker_id, art, ressurs_type,"
        " ressurs_id, tekstnokkel, parametre, epost_status)"
        " VALUES (%s,%s,'attestering_venter','policyutkast',%s,"
        "         'varsel.attestering_venter','{}',%s) RETURNING id",
        (T, bruker_id, secrets.token_hex(4), epost_status)).fetchone()[0]


def _identitet(conn):
    """EKTE identiteter. `varsel.bruker_id` har fremmednøkkel til
    `brukeridentitet` (målt: `varsel_bruker_id_fkey`), så en oppdiktet
    streng blir avvist av basen — og en test som omgikk døra ville målt
    noe annet enn verten gjør."""
    return conn.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES (%s,%s)"
        " RETURNING bruker_id",
        ("https://idp.test", secrets.token_hex(8))).fetchone()[0]


@pytest.fixture
def to_brukere(migrator):  # noqa: F811
    a = _identitet(migrator)
    b = _identitet(migrator)
    migrator.commit()
    yield a, b
    _ctx(migrator)
    migrator.execute("DELETE FROM varsel WHERE tenant=%s AND bruker_id IN (%s,%s)",
                     (T, a, b))
    migrator.execute("DELETE FROM brukeridentitet WHERE bruker_id IN (%s,%s)",
                     (a, b))
    migrator.commit()


@pg
def test_jeg_kan_slette_mitt_eget(migrator, to_brukere):  # noqa: F811
    from api import varsel as v

    a, _b = to_brukere
    vid = _varsel(migrator, a)
    migrator.commit()
    _ctx(migrator)
    assert v.slett(migrator, tenant=T, bruker_id=a, varsel_id=vid) is True
    assert migrator.execute("SELECT count(*) FROM varsel WHERE id=%s",
                            (vid,)).fetchone()[0] == 0
    migrator.commit()


@pg
def test_jeg_kan_IKKE_slette_en_kollegas(migrator, to_brukere):  # noqa: F811
    """SELVE VERNET.

    Begge står i SAMME tenant, så RLS slipper begge radene gjennom. Det er
    `bruker_id` i WHERE som er forskjellen — og uten den ville A kunnet
    fjerne varselet som ventet på B.

    MUTASJON SOM FELLER: ta `bruker_id` ut av WHERE i `varsel.slett`.
    """
    from api import varsel as v

    a, b = to_brukere
    vid = _varsel(migrator, b)          # varselet er B SITT
    migrator.commit()
    _ctx(migrator)
    assert v.slett(migrator, tenant=T, bruker_id=a, varsel_id=vid) is False, \
        "A fikk slette Bs varsel"
    assert migrator.execute("SELECT count(*) FROM varsel WHERE id=%s",
                            (vid,)).fetchone()[0] == 1, "raden forsvant likevel"
    migrator.commit()


@pg
def test_slett_mange_tar_bare_mine(migrator, to_brukere):  # noqa: F811
    """En kollegas id i lista skal ikke gi tilgang til kollegas rad.

    Id-ene AUTORISERER ingenting — de peker bare ut hva som skal bort. Uten
    `bruker_id` i WHERE ville A kunnet sende Bs id og få den slettet.

    MUTASJON SOM FELLER: ta `bruker_id` ut av WHERE i `slett_mange`.
    """
    from api import varsel as v

    a, b = to_brukere
    mine = [_varsel(migrator, a) for _ in range(3)]
    bid = _varsel(migrator, b)
    migrator.commit()
    _ctx(migrator)
    # Bs id sendes MED, med vilje.
    assert v.slett_mange(migrator, tenant=T, bruker_id=a,
                         ider=mine + [bid]) == 3
    assert migrator.execute("SELECT count(*) FROM varsel WHERE id=%s",
                            (bid,)).fetchone()[0] == 1, \
        "kollegaens varsel ble slettet med"
    migrator.commit()


@pg
def test_en_gjentatt_tomming_tar_ikke_det_som_kom_imellom(migrator,  # noqa: F811
                                                          to_brukere):
    """HVORFOR DEN TAR ID-ER I DET HELE TATT.

    Varselrutene har ingen idempotensrad — de er «naturlig idempotente» fordi
    «lest» og «kanal» er TILSTANDER. «Slett alt som finnes nå» er derimot en
    HENDELSE: et gjentatt kall etter en nettverkshikke ville tatt varsler som
    kom imellom, usett. Med id-er sletter andre kall de samme radene, altså
    ingenting.

    MUTASJON SOM FELLER: la døra slette alt for brukeren igjen, uten id-er.
    """
    from api import varsel as v

    a, _b = to_brukere
    forste = [_varsel(migrator, a) for _ in range(2)]
    migrator.commit()
    _ctx(migrator)
    assert v.slett_mange(migrator, tenant=T, bruker_id=a, ider=forste) == 2
    migrator.commit()

    # Et nytt varsel kommer — og SÅ kommer det gjentatte kallet.
    nytt = _varsel(migrator, a)
    migrator.commit()
    _ctx(migrator)
    assert v.slett_mange(migrator, tenant=T, bruker_id=a, ider=forste) == 0
    assert migrator.execute("SELECT count(*) FROM varsel WHERE id=%s",
                            (nytt,)).fetchone()[0] == 1, \
        "det gjentatte kallet tok varselet som kom imellom"
    migrator.commit()


@pg
def test_en_rad_i_et_SMTP_kall_blir_staaende(migrator, to_brukere):  # noqa: F811
    """`under_sending` står utenfor, som i `I_KO`.

    Raden er i et SMTP-kall akkurat nå, og en e-post som er ute kan ikke
    kalles hjem. Senderen tåler at raden forsvinner — fullføringen er
    token-bundet — men da produserer vi en «mistet»-advarsel i journalen med
    vilje, for å slippe å vente noen sekunder.

    MUTASJON SOM FELLER: fjern `epost_status IS DISTINCT FROM 'under_sending'`.
    """
    from api import varsel as v

    a, _b = to_brukere
    vid = _varsel(migrator, a, epost_status="under_sending")
    migrator.commit()
    _ctx(migrator)
    assert v.slett(migrator, tenant=T, bruker_id=a, varsel_id=vid) is False
    assert v.slett_mange(migrator, tenant=T, bruker_id=a, ider=[vid]) == 0, \
        "tømmingen tok raden som lå i et SMTP-kall"
    migrator.commit()


@pg
def test_slettingen_avlyser_e_posten(migrator, to_brukere):  # noqa: F811
    """Raden er borte, så senderen finner den ikke.

    Samme regel som `merk_lest`: portalen er varselet, e-posten er en kopi.
    Her måles det på køens egne premisser — en `koet` rad som slettes skal
    ikke lenger være synlig for klaimet.
    """
    from api import varsel as v

    a, _b = to_brukere
    vid = _varsel(migrator, a, epost_status="koet")
    migrator.commit()
    _ctx(migrator)
    v.slett(migrator, tenant=T, bruker_id=a, varsel_id=vid)
    assert migrator.execute(
        "SELECT count(*) FROM varsel WHERE id=%s AND epost_status='koet'",
        (vid,)).fetchone()[0] == 0
    migrator.commit()


@pg
def test_runtime_har_DELETE_paa_varsel(miljo, to_brukere):  # noqa: F811
    """Granten står i `migrer.py`, ikke i en migrasjon — og den må VIRKE.

    189 grantet inne i en migrasjon, deployen fjernet det, og prod sto med
    ingen rettigheter for runtime. Porten merket ingenting fordi den kalte
    døra som MIGRATOR — altså en vei ingen ekte kaller går. Denne går
    runtime-veien.

    MUTASJON SOM FELLER: `REVOKE DELETE ON varsel FROM disponit`.
    """
    # Identiteten lages av MIGRATOR (fiksturen): runtime har `arw` på
    # `brukeridentitet`, ikke `d`, så oppryddingen hadde dødd på rettigheter
    # — og da målte testen ryddingen i stedet for granten den finnes for.
    bid, _annen = to_brukere
    with koble(DSN) as c:
        _ctx(c)
        vid = c.execute(
            "INSERT INTO varsel (tenant, bruker_id, art, ressurs_type,"
            " ressurs_id, tekstnokkel, parametre)"
            " VALUES (%s,%s,'attestering_venter','policyutkast',%s,"
            "         'varsel.attestering_venter','{}') RETURNING id",
            (T, bid, secrets.token_hex(4))).fetchone()[0]
        c.commit()
        _ctx(c)
        from api import varsel as v
        assert v.slett(c, tenant=T, bruker_id=bid, varsel_id=vid) is True, \
            "runtime kunne ikke slette — mangler DELETE-granten"
        c.commit()
