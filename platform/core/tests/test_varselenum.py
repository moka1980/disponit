"""Varselenumene: skriptets deklarasjon og kjedens resultat SKAL være
like.

Driften som gjorde denne filen nødvendig var usynlig i to måneder fordi
ingen port sammenlignet produksjonens CHECK-er med en fasit. 044 spleiset
inn i det den fant, og det den fant var — på grunn av en `SELECT ... INTO`
uten `contype`-filter — noen ganger NOT NULL-raden i stedet for CHECKen.
Spliceen ble da en stille no-op.

Portene her måler tre ting:

1. En base som har kjørt HELE kjeden har nøyaktig de kanoniske settene.
2. `varselenum-reparasjon.sql` deklarerer NØYAKTIG de samme settene. Uten
   dette kan skriptet og kjeden drive fra hverandre igjen — bare langsommere.
3. Reparasjonen virker BEGGE veier: en base i produksjonens drevne form
   blir kanonisk, og en base som alt er kanonisk står urørt.
"""
import re
from pathlib import Path

import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo  # noqa: F401

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

REPARASJON = (Path(__file__).resolve().parents[3]
              / "deploy" / "staging" / "varselenum-reparasjon.sql")

#: Fasiten. Rekkefølgen er del av den: CHECK-definisjonen gjengis av
#: `pg_get_constraintdef` i deklarert rekkefølge, og en omstokking ville
#: gitt en ny definisjonsstreng som spliceene i 090/091 ikke gjenkjenner.
KANONISK = {
    "varsel_art_chk": (
        "attestering_venter", "validering_venter", "runde_apnet",
        "tokenfamilie_utloper", "domeneovertakelse", "plan_pauset",
        "plan_gjentatt_brudd", "backupverifisering_uteblitt",
        "selvtest_rodt", "selvtest_uteblitt"),
    "varsel_ressurs_type_chk": (
        "policyutkast", "modultoken", "domene", "plan",
        "backupverifisering", "selvtest"),
}

_VARIABEL = {"varsel_art_chk": "v_art",
             "varsel_ressurs_type_chk": "v_ressurs"}


def _deklarert_i_skriptet() -> dict[str, tuple[str, ...]]:
    """Leser ARRAY-blokkene i reparasjonsfilen.

    Parsingen er bevisst streng: den krever ett element per linje med
    enkle fnutter, og den teller elementene den fant. En for slapp parser
    er nøyaktig feilen denne filen finnes for å hindre — jf.
    eierskap-reparasjonens VALUES-blokk, der et semikolon i en kommentar
    kuttet listen stille.
    """
    tekst = REPARASJON.read_text(encoding="utf-8")
    ut: dict[str, tuple[str, ...]] = {}
    for navn, var in _VARIABEL.items():
        m = re.search(rf"{var} TEXT\[\] := ARRAY\[(.*?)\];", tekst, re.S)
        assert m, f"fant ikke ARRAY-blokken for {var} i {REPARASJON.name}"
        ut[navn] = tuple(re.findall(r"'([a-z_]+)'", m.group(1)))
    return ut


def _def(conn, navn: str) -> str | None:
    rad = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conrelid = 'varsel'::regclass AND conname = %s"
        "   AND contype = 'c'", (navn,)).fetchone()
    return rad[0] if rad else None


def _onsket(navn: str) -> str:
    kolonne = "art" if navn == "varsel_art_chk" else "ressurs_type"
    verdier = ", ".join(f"'{v}'::text" for v in KANONISK[navn])
    return f"CHECK (({kolonne} = ANY (ARRAY[{verdier}])))"


def test_skriptet_deklarerer_fasiten():
    """Port 2 — uten denne kan skript og kjede drive fra hverandre igjen."""
    assert _deklarert_i_skriptet() == {k: tuple(v)
                                       for k, v in KANONISK.items()}


def test_reparasjonsfilen_filtrerer_paa_contype():
    """Rotårsaken, som statisk port.

    `SELECT ... INTO` mot pg_constraint UTEN `contype`-filter treffer også
    NOT NULL-radene (PostgreSQL 17+) og plukker vilkårlig. Filen som
    reparerer driften skal ikke kunne gjeninnføre den.
    """
    tekst = REPARASJON.read_text(encoding="utf-8")
    kropp = tekst.split("DO $$", 1)[1]
    assert "contype = 'c'" in kropp, (
        "reparasjonen slår opp begrensningen uten contype-filter — det er"
        " nøyaktig 044-feilen på nytt")


@pg
def test_kjeden_gir_de_kanoniske_settene(migrator):
    """Port 1 — en fullmigrert base MÅ ha fasiten.

    Dette er porten som ville sett produksjonsdriften i august. Den
    spør basen, ikke kildekoden: en splice som ble en no-op er usynlig i
    diffen og synlig her.
    """
    for navn in KANONISK:
        faktisk = _def(migrator, navn)
        assert faktisk is not None, f"{navn} finnes ikke på varsel"
        assert faktisk == _onsket(navn), (
            f"{navn} avviker fra fasiten.\n  er:  {faktisk}\n"
            f"  skal: {_onsket(navn)}")
    migrator.rollback()


@pg
def test_reparasjonen_kanoniserer_den_drevne_formen(migrator):
    """Port 3 — begge veier, i én transaksjon som rulles tilbake.

    Den drevne formen er produksjonens EGEN, ordrett fra 090s feilmelding
    1. september: policyutkast/modultoken/domene og ikke noe mer.
    """
    sql = REPARASJON.read_text(encoding="utf-8")
    try:
        migrator.execute(
            "ALTER TABLE varsel DROP CONSTRAINT varsel_ressurs_type_chk")
        migrator.execute(
            "ALTER TABLE varsel ADD CONSTRAINT varsel_ressurs_type_chk"
            " CHECK ((ressurs_type = ANY (ARRAY['policyutkast'::text,"
            " 'modultoken'::text, 'domene'::text])))")
        assert "'plan'::text" not in _def(migrator, "varsel_ressurs_type_chk")

        migrator.execute(sql)
        assert _def(migrator, "varsel_ressurs_type_chk") == \
            _onsket("varsel_ressurs_type_chk")
        # …og art-armen ble IKKE rørt: den var alt kanonisk. En reparasjon
        # som skriver om det som er riktig, er en reparasjon man ikke tør
        # kjøre.
        assert _def(migrator, "varsel_art_chk") == _onsket("varsel_art_chk")

        # Idempotens: en andre kjøring er en no-op.
        migrator.execute(sql)
        assert _def(migrator, "varsel_ressurs_type_chk") == \
            _onsket("varsel_ressurs_type_chk")
    finally:
        migrator.rollback()


@pg
def test_reparasjonen_stopper_framfor_aa_slette_ulovlige_rader(migrator):
    """En rad utenfor det kanoniske settet er en tilstand filen ikke
    forstår. Da skal den STOPPE — ikke slette raden for å få
    begrensningen på plass.

    Scenarioet er det ekte: noen har utvidet CHECKen for hånd og satt inn
    rader på den nye verdien. Kanoniseringen ville da SNEVRET INN, og en
    innsnevring som lykkes er en innsnevring som har mistet data.
    """
    sql = REPARASJON.read_text(encoding="utf-8")
    utvidet = _onsket("varsel_ressurs_type_chk").replace(
        "'selvtest'::text", "'selvtest'::text, 'ukjent_type'::text")
    try:
        migrator.execute(
            "ALTER TABLE varsel DROP CONSTRAINT varsel_ressurs_type_chk")
        migrator.execute("ALTER TABLE varsel ADD CONSTRAINT"
                         f" varsel_ressurs_type_chk {utvidet}")
        rad = migrator.execute(
            "SELECT b.bruker_id FROM brukeridentitet b LIMIT 1").fetchone()
        if rad is None:
            pytest.skip("ingen brukeridentitet å henge varselet på")
        # FORCE RLS gjelder også migrator: uten tenantkonteksten avvises
        # innsettingen av policyen, ikke av CHECKen — og da hadde porten
        # målt noe helt annet enn den skal.
        migrator.execute(
            "SELECT set_config('disponit.tenant', 't-varselenum', true)")
        migrator.execute(
            "INSERT INTO varsel (tenant, bruker_id, art, ressurs_type,"
            " ressurs_id, hendelse, tekstnokkel)"
            " VALUES ('t-varselenum', %s, 'runde_apnet', 'ukjent_type',"
            "         'x', 'h', 'varsel.runde_apnet')", (rad[0],))
        with pytest.raises(Exception, match="utenfor det kanoniske"):
            migrator.execute(sql)
    finally:
        migrator.rollback()
