"""201: kunden sier opp sitt eget abonnement — og bare det.

Eier: «kunden kan avbryte prøveperioden og samme etter 30 dager ikke
fortsette.»

MÅLT FØR DETTE: `firma_sett_status` er grantet til MIGRATOR ALENE. En kunde
kunne altså ikke si opp uten å be eieren gjøre det for seg. Et abonnement du
ikke kommer ut av selv er ikke en prøveperiode, det er en binding.

DET FARLIGE ER IKKE OPPSIGELSEN, DET ER RETNINGEN.
`firma_sett_status` tar status som PARAMETER; grantet til runtime kunne et
firma satt seg selv `aktiv` — gitt seg selv et betalt abonnement gratis. Det
funnet er alt gjort én gang (CodeRabbit, 190), og grantet ble fjernet. Døra
her går derfor ÉN vei.
"""
import secrets

import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo  # noqa: F401
from .test_m37 import _sett_kontekst

pg = pytest.mark.skipif(not (DSN and MIGRATOR_DSN), reason="test-DSN ikke satt")


def _t() -> str:
    return "t-oppsi-" + secrets.token_hex(3)


def _firma(c, tenant, status="prove"):
    _sett_kontekst(c, tenant)
    c.execute("SELECT firma_registrer(%s,%s,NULL,30,'oppsett')",
              (tenant, tenant))
    if status != "prove":
        c.execute("SELECT firma_sett_status(%s,%s,'oppsett')",
                  (tenant, status))
    c.commit()
    return tenant


def _status(c, tenant):
    _sett_kontekst(c, tenant)
    return c.execute("SELECT status FROM firma WHERE tenant=%s",
                     (tenant,)).fetchone()[0]


def _rydd(c, *tenanter):
    for t in tenanter:
        _sett_kontekst(c, t)
        c.execute("DELETE FROM firma WHERE tenant=%s", (t,))
    c.commit()


# ---------------------------------------------------------------------------
# Veien ut
# ---------------------------------------------------------------------------

@pg
@pytest.mark.parametrize("fra", ["prove", "aktiv"])
def test_kunden_kan_si_opp_fra_prove_og_aktiv(migrator, fra):  # noqa: F811
    """Begge de levende tilstandene. Eier ba om begge: avbryte prøven
    underveis, og ikke fortsette etter at den er over."""
    t = _firma(migrator, _t(), fra)
    try:
        _sett_kontekst(migrator, t)
        migrator.execute("SELECT firma_kunde_avslutt(%s,'bruker:x')", (t,))
        migrator.commit()
        assert _status(migrator, t) == "stengt"
    finally:
        _rydd(migrator, t)


@pg
def test_oppsigelsen_setter_spor(migrator):  # noqa: F811
    """190s dør gjør arbeidet, så `stengt_ts` og `stengt_av` fylles.

    MUTASJON SOM FELLER: la døra skrive `firma` direkte i stedet for å kalle
    `firma_sett_status`.
    """
    t = _firma(migrator, _t())
    try:
        _sett_kontekst(migrator, t)
        migrator.execute("SELECT firma_kunde_avslutt(%s,'bruker:kari')", (t,))
        migrator.commit()
        _sett_kontekst(migrator, t)
        rad = migrator.execute(
            "SELECT stengt_ts IS NOT NULL, stengt_av FROM firma"
            " WHERE tenant=%s", (t,)).fetchone()
        assert rad == (True, "bruker:kari"), \
            f"oppsigelsen etterlot ikke et spor: {rad}"
    finally:
        _rydd(migrator, t)


# ---------------------------------------------------------------------------
# Veien som IKKE finnes
# ---------------------------------------------------------------------------

@pg
def test_doera_har_ingen_statusparameter(migrator):  # noqa: F811
    """SELVE VERNET — OG DET ER STRUKTUREN, IKKE EN SJEKK.

    Kunden kan ikke gi seg selv `aktiv` fordi døra ikke TAR imot en status:
    den har to argumenter, og målet er hardkodet til `stengt`. Det er en
    egenskap ingen kan omgå med en feil i en IF.

    Jeg skrev først en test som het «kunden kan ikke gjenåpne» og kalte døra
    to ganger. Den var grønn — men av FEIL GRUNN: det var 190s
    overgangstabell som avviste `stengt → stengt`, ikke noe i denne døra.
    Fjernet jeg min egen retningssjekk, sto den fortsatt grønn. En port som
    består på naboens vern måler naboen.

    MUTASJON SOM FELLER: gi døra en `p_status`-parameter.
    """
    args = migrator.execute(
        "SELECT pg_get_function_arguments(oid) FROM pg_proc"
        " WHERE proname='firma_kunde_avslutt'").fetchone()[0]
    assert args == "p_tenant text, p_aktor text", \
        f"døra tar imot noe mer enn tenant og aktør: {args}"
    kilde = migrator.execute(
        "SELECT pg_get_functiondef(oid) FROM pg_proc"
        " WHERE proname='firma_kunde_avslutt'").fetchone()[0]
    assert kilde.count("firma_sett_status") == 1 and "'stengt'" in kilde, \
        "målet er ikke lenger hardkodet til `stengt`"


@pg
def test_et_UTLOPT_firma_kan_ikke_sies_opp(migrator):  # noqa: F811
    """DETTE ER ALT MIN RETNINGSSJEKK FAKTISK LEGGER TIL.

    190s tabell TILLATER `utlopt → stengt`; det er denne døra som nekter.
    Fra `utlopt` er prøven alt over, og firmaet venter på en beslutning som
    ikke er kundens — å la henne «si opp» da ville vært å la henne lukke en
    dør eieren holder åpen.

    De andre overgangene mine er redundante med 190, og det er greit: en dør
    som er selvforklarende der den står, er lettere å lese enn en som
    forutsetter at du husker naboens tabell.

    MUTASJON SOM FELLER: ta bort `IF v_naa NOT IN ('prove','aktiv')`.
    """
    import psycopg

    t = _firma(migrator, _t(), "utlopt")
    try:
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
            with migrator.transaction():
                _sett_kontekst(migrator, t)
                migrator.execute("SELECT firma_kunde_avslutt(%s,'bruker:x')",
                                 (t,))
        assert _status(migrator, t) == "utlopt"
    finally:
        _rydd(migrator, t)


@pg
def test_hun_kan_ikke_si_opp_ET_ANNET_firma(migrator):  # noqa: F811
    """038s form: tenanten bindes til KONTEKSTEN, aldri til parameteret.

    TO DØRER HÅNDHEVER DETTE, og det er verdt å si: `firma_sett_status` gjør
    sitt eget `krev_tenantkontekst`, så fjerner man kallet HER, faller testen
    likevel ikke — jeg målte det. Vernet er altså naboens, og linja i denne
    døra er belte til seler.

    Den står likevel, fordi en dør som selv sier hva den krever er lettere å
    lese enn en som forutsetter at du åpner den neste. Men porten lover ikke
    lenger å måle noe den ikke måler.
    """
    import psycopg

    mitt, ditt = _firma(migrator, _t()), _firma(migrator, _t())
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with migrator.transaction():
                _sett_kontekst(migrator, mitt)      # jeg står i MITT
                migrator.execute("SELECT firma_kunde_avslutt(%s,'bruker:x')",
                                 (ditt,))           # …og peker på DITT
        assert _status(migrator, ditt) == "prove", "et annet firma ble stengt"
    finally:
        _rydd(migrator, mitt, ditt)


# ---------------------------------------------------------------------------
# Rettighetene
# ---------------------------------------------------------------------------

@pg
def test_runtime_kan_kalle_doera_men_ikke_sette_status_selv(miljo):  # noqa: F811
    """Hele poenget med at dette er en EGEN dør.

    Runtime skal kunne si opp (gjennom denne), men ikke kunne velge status
    (gjennom `firma_sett_status`). Går det andre grantet inn igjen, kan et
    firma sette seg selv `aktiv`.

    MUTASJON SOM FELLER: `GRANT EXECUTE ON firma_sett_status TO disponit`.
    """
    import psycopg

    from db.pg import koble

    t = _t()
    m = koble(MIGRATOR_DSN)
    try:
        _firma(m, t)
    finally:
        m.close()
    try:
        with koble(DSN) as c:
            _sett_kontekst(c, t)
            c.execute("SELECT firma_kunde_avslutt(%s,'bruker:x')", (t,))
            c.commit()
            _sett_kontekst(c, t)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with c.transaction():
                    c.execute("SELECT firma_sett_status(%s,'aktiv','x')",
                              (t,))
    finally:
        m = koble(MIGRATOR_DSN)
        try:
            _rydd(m, t)
        finally:
            m.close()


@pg
def test_doera_er_ikke_apen_for_PUBLIC(migrator):  # noqa: F811
    """Måler `proacl` direkte. NULL ER standardrettighetene, og for en
    funksjon er standarden EXECUTE TO PUBLIC — `unnest(NULL)` gir null
    rader, så en ren `=%`-test ville vært grønn på nettopp den tilstanden.
    """
    rad = migrator.execute(
        "SELECT coalesce(proacl::text,'NULL = PUBLIC') FROM pg_proc"
        " WHERE proname='firma_kunde_avslutt'").fetchone()[0]
    assert rad != "NULL = PUBLIC", "døra står med standardrettigheter"
    assert "=X/" not in rad.replace("disponit=X/", "").replace(
        "disponit_migrator=X/", ""), f"PUBLIC har EXECUTE: {rad}"
