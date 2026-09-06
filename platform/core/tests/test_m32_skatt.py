"""M-32 global lokaliserings- og skatteagent v1 (138) — KLYNGE 10s ANDRE.

Grensen `m32-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

KLYNGENS DELTE DOM:

  EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN ANGRES IKKE AV EN
  ROLLBACK.

En innberettet mva-oppgave er hos skattemyndigheten. En rollback her
gjør den ikke usendt; den gjør bare at vi ikke lenger vet hva vi
sendte.

DEN TYNGSTE GRUPPEN PORTER MÅLER AT LANDREGISTERET IKKE KAN RØRES.
`landpakke` og `landsats` er globale og tenantløse, og
`disponit_skatt_eier` har SELECT og ingenting annet på begge.
`landpakke_endret_gjennom_dor` er umulig fordi RETTIGHETEN ikke finnes
— ikke fordi ingen dør bruker den.

EN ANNEN GRUPPE MÅLER AT MODULEN ALDRI SER EN ADRESSE. Arven fra M-19
er en KOLONNEGRANT (093s form): `land`, `gjelder_fra`, `versjon_id` —
aldri gate, postnummer eller poststed. At skattemodulen ikke leser
persondata skal være en egenskap ved BASEN, ikke ved disiplinen.

FIRE PORTER MÅLER ET FRAVÆR SOM ER ET BEVIS:
`transaksjon_uten_jurisdiksjon`, `sats_uten_regelversjon`,
`sats_uten_komplett_landpakke` og `landpakke_endret_gjennom_dor` står i
funntypesettet OG kan aldri reises.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import os
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN  # noqa: F401

SKATTESVEIP_DSN = os.environ.get("DISPONIT_TEST_SKATTESVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "138_m32_skatt.sql")
FUNDAMENT = ROT / "docs" / "KLYNGE10-FUNDAMENT.md"

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

#: MODULENS EGNE TENANTTABELLER.
EGNE = ("skattekrav", "jurisdiksjonsvurdering", "skattefunn")

#: DE GLOBALE. Tenantløse, uten RLS, og modulen har SELECT og
#: ingenting annet.
GLOBALE = ("landpakke", "landsats")

ROLLENE = ("disponit_skatt_eier", "disponit_skattesveip")

#: KOLONNENE MODULEN SKAL SE PÅ ADRESSEN — og de den ikke skal.
ADRESSE_SYNLIG = ("tenant", "versjon_id", "subjekt_id", "land",
                  "gjelder_fra")
ADRESSE_SKJULT = ("linje1_original", "linje2_original",
                  "postnr_original", "poststed_original",
                  "linje1_normalisert", "postnr_normalisert",
                  "poststed_normalisert", "notat")


@contextlib.contextmanager
def _to():
    """RUNTIME for dørene, MIGRATOR for tabellene (SP-7)."""
    from db.pg import koble
    rt = koble(DSN)
    mg = koble(MIGRATOR_DSN)
    try:
        yield rt, mg
    finally:
        for c in (rt, mg):
            try:
                c.rollback()
                c.close()
            except Exception:
                pass


def _sv():
    from db.pg import koble
    return koble(SKATTESVEIP_DSN or MIGRATOR_DSN)


def _sett_kontekst(conn, tenant):
    conn.execute("SELECT set_config('disponit.tenant', %s, false)",
                 (tenant,))


def _tenantnavn(merke: str) -> str:
    return f"t-m32-{merke}-{secrets.token_hex(4)}"


I_DAG = dt.date.today()


# =====================================================================
# BYGGEKLOSSER.
# =====================================================================

def _krav(rt, t, *, selgerland="NO", grense=1_000_000, frist=14):
    _sett_kontekst(rt, t)
    v = rt.execute("SELECT m32_sett_krav(%s,%s,%s,%s,%s)",
                   (t, selgerland, grense, frist, "u-test")).fetchone()[0]
    rt.commit()
    return v


def _adresse(mg, t, *, land="NO"):
    """En adresseversjon å lese landet fra.

    SKRIVES SOM MIGRATOR: M-19s dører er ikke M-32s, og porten skal
    måle skatteberegningen — ikke adressemodulen.
    """
    _sett_kontekst(mg, t)
    sid = uuid.uuid4()
    vid = uuid.uuid4()
    mg.execute(
        "INSERT INTO adressesubjekt (tenant, subjekt_id, ekstern_ref,"
        " navn, opprettet_av) VALUES (%s,%s,%s,'Testkunde AS','u-test')",
        (t, sid, f"k-{secrets.token_hex(3)}"))
    mg.execute(
        "INSERT INTO adresseversjon (tenant, versjon_id, subjekt_id,"
        " linje1_original, postnr_original, poststed_original, land,"
        " linje1_normalisert, postnr_normalisert, poststed_normalisert,"
        " kilde, kilde_ref, notat, gjelder_fra, registrert_av)"
        " VALUES (%s,%s,%s,'Storgata 1','0155','Oslo',%s,"
        " 'STORGATA 1','0155','OSLO','manuell','u-test','porten',"
        " %s,'u-test')",
        (t, vid, sid, land, I_DAG - dt.timedelta(days=30)))
    mg.commit()
    return vid


def _beregn(rt, t, kv, vid, *, satskode="standard", belop=100_000,
            dato=None, ref=None):
    _sett_kontekst(rt, t)
    uid = uuid.uuid4()
    rad = rt.execute(
        "SELECT * FROM m32_beregn(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (t, uid, ref or f"tx-{secrets.token_hex(4)}", kv, vid, satskode,
         belop, dato or I_DAG, "u-test")).fetchone()
    rt.commit()
    return uid, rad


# =====================================================================
# GRENSEN OG DOMMEN.
# =====================================================================

def test_grensen_ble_registrert_for_koden():
    """§0-REGELEN: `m32-v1` sto i KRAVGRENSER før 138 fantes."""
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m32-v1"]
    assert g["maks_brudd"] == 0
    assert "ddl_begge_kjoringer_gronne" in g["krav_ja"]
    for navn in ("modulen_innberettet_skatt",
                 "sats_uten_komplett_landpakke",
                 "transaksjon_uten_jurisdiksjon",
                 "sats_uten_regelversjon",
                 "regelversjon_endret_etter_bruk",
                 "landpakke_endret_gjennom_dor"):
        assert navn in g["invarianter"], navn


@pg
def test_landregisteret_kan_ikke_rores_gjennom_en_dor():
    """DEN VIKTIGSTE PORTEN I FILA.

    En skattesats er en REGEL, ikke data. Kunne en tenant endret satsen
    for et land gjennom en dør, ville den regelen ikke lenger vært
    landets — den ville vært vår, og vi ville ikke visst når den
    sluttet å stemme.

    `landpakke_endret_gjennom_dor` er umulig fordi RETTIGHETEN IKKE
    FINNES. Eieren har SELECT og ingenting annet; sveipen har ingenting
    i det hele tatt.

    MUTASJONEN SOM DREPER DENNE: legg
    `GRANT UPDATE ON landsats TO disponit_skatt_eier` i 138.
    """
    with _to() as (_rt, mg):
        funnet = {}
        for rolle in ROLLENE:
            for tab in GLOBALE:
                skriv = mg.execute(
                    "SELECT p FROM unnest(ARRAY['INSERT','UPDATE',"
                    "'DELETE','TRUNCATE']) p"
                    " WHERE has_table_privilege(%s, %s, p)",
                    (rolle, tab)).fetchall()
                if skriv:
                    funnet[f"{rolle}.{tab}"] = [r[0] for r in skriv]
        assert funnet == {}, (
            "landregisteret kan skrives gjennom en rolle: " + repr(funnet))
        # …OG EIEREN SKAL LESE. En port som bare målte fraværet ville
        # vært grønn på en modul uten tilgang i det hele tatt.
        for tab in GLOBALE:
            assert mg.execute(
                "SELECT has_table_privilege('disponit_skatt_eier', %s,"
                " 'SELECT')", (tab,)).fetchone()[0], tab


@pg
def test_ingen_dor_skriver_i_landregisteret():
    """…OG DET FINNES INGEN DØR SOM PRØVER.

    Porten over måler rettigheten. Denne måler KODEN: ingen
    `m32_*`-funksjon nevner `landpakke` eller `landsats` i en
    skrivesetning.

    Begge trengs. En rettighet uten en dør er en åpen låsdør ingen har
    tatt i; en dør uten rettighet er en som feiler i produksjon framfor
    i en test.
    """
    import re
    kilde = MIGRASJON.read_text(encoding="utf-8")
    uten_kommentar = "\n".join(
        l for l in kilde.splitlines() if not l.lstrip().startswith("--"))
    for mal in (r"INSERT\s+INTO\s+public\.landpakke",
                r"INSERT\s+INTO\s+public\.landsats",
                r"UPDATE\s+public\.landpakke",
                r"UPDATE\s+public\.landsats",
                r"DELETE\s+FROM\s+public\.land"):
        assert not re.search(mal, uten_kommentar, re.I), mal
    # SEEDINGEN I MIGRASJONEN STÅR — den er `INSERT INTO landpakke`
    # UTEN skjemaprefiks, kjørt som migrator FØR dørene lages. Det er
    # nettopp skillet: dommene felles i git, ikke gjennom en dør.
    assert re.search(r"INSERT INTO landpakke", uten_kommentar)


@pg
def test_modulen_ser_landet_men_aldri_adressen():
    """093s KOLONNEGRANT, ANVENDT PÅ ARVEN FRA M-19.

    M-32 trenger landet for å vite hvilken jurisdiksjon som gjaldt. Den
    trenger ikke gata.

    AT SKATTEMODULEN ALDRI LESER EN ADRESSE SKAL VÆRE EN EGENSKAP VED
    BASEN, IKKE VED DISIPLINEN. En tabellgrant ville gjort det til en
    disiplin.

    MUTASJONEN SOM DREPER DENNE: bytt kolonngranten mot
    `GRANT SELECT ON adresseversjon`.
    """
    with _to() as (_rt, mg):
        ser = {}
        for kol in ADRESSE_SYNLIG + ADRESSE_SKJULT:
            ser[kol] = mg.execute(
                "SELECT has_column_privilege('disponit_skatt_eier',"
                " 'adresseversjon', %s, 'SELECT')", (kol,)).fetchone()[0]
        mangler = [k for k in ADRESSE_SYNLIG if not ser[k]]
        lekker = [k for k in ADRESSE_SKJULT if ser[k]]
        assert mangler == [], f"modulen mangler {mangler}"
        assert lekker == [], f"modulen ser adressen: {lekker}"


@pg
def test_de_fire_umulige_staar_i_settet_og_kan_ikke_reises():
    """AT DE STÅR DER OG ER UMULIGE ER BEVISET."""
    with _to() as (_rt, mg):
        cd = mg.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'skattefunn_funntype_lukket'").fetchone()[0]
        for umulig in ("transaksjon_uten_jurisdiksjon",
                       "sats_uten_regelversjon",
                       "sats_uten_komplett_landpakke",
                       "landpakke_endret_gjennom_dor"):
            assert umulig in cd, umulig

        nullbar = {r[0]: r[1] for r in mg.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns"
            " WHERE table_name = 'jurisdiksjonsvurdering'").fetchall()}
        assert nullbar["jurisdiksjon"] == "NO"
        assert nullbar["regelversjon"] == "NO"
        assert nullbar["adresseversjon_id"] == "NO"

        # FREMMEDNØKLENE, IKKE BARE NOT NULL. Satsen som ble brukt må
        # finnes, i den versjonen som ble brukt, for det landet.
        fk = {r[0] for r in mg.execute(
            "SELECT conname FROM pg_constraint WHERE contype = 'f'"
            " AND conrelid IN ('jurisdiksjonsvurdering'::regclass,"
            "                  'landsats'::regclass)").fetchall()}
        assert "jurisdiksjonsvurdering_sats_fk" in fk
        assert "landsats_pakke_fk" in fk


@pg
def test_modulen_innberetter_ingenting():
    """V1-DOMMEN, MÅLT PÅ HELE MODULEN.

    Ingen kolonne noe sted registrerer en innsending, og ingen dør
    sender noe. `m32_bildet.innberetninger` er derfor alltid 0 — ikke
    som en telling, men som en påstand om at kolonnen ikke finnes.

    MUTASJONEN SOM DREPER DENNE: legg til `innsendt_ts` på
    `jurisdiksjonsvurdering`.
    """
    t = _tenantnavn("innberet")
    with _to() as (rt, mg):
        kolonner = {r[0] for r in mg.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name IN ('jurisdiksjonsvurdering','skattekrav',"
            "                      'landpakke','landsats')").fetchall()}
        for forbudt in ("innsendt_ts", "innsendt", "innberettet_ts",
                        "sendt_ts", "kvittering", "innsendingsref"):
            assert forbudt not in kolonner, forbudt

        kv = _krav(rt, t)
        vid = _adresse(mg, t)
        _beregn(rt, t, kv, vid)
        _sett_kontekst(rt, t)
        bilde = rt.execute("SELECT * FROM m32_bildet(%s)", (t,)).fetchone()
        assert bilde[0] == 1, "vurderingen ble ikke skrevet"
        assert bilde[4] == 0, "modulen rapporterer en innberetning"


# =====================================================================
# «USIKKER JURISDIKSJON STOPPER TRANSAKSJONEN.»
# =====================================================================

@pg
def test_land_uten_pakke_stopper_beregningen():
    """VAKTSETNINGEN, MÅLT.

    Et land uten komplett pakke HAR INGEN RAD. Uten rad finnes ingen
    sats, og uten sats stopper beregningen — den gjetter ikke, og den
    faller ikke tilbake på selgerlandets sats.

    Et fallback her ville vært den farligste linjen i modulen: en
    transaksjon til Tyskland ville fått norsk mva, og regnskapet ville
    sett riktig ut.

    MUTASJONEN SOM DREPER DENNE: la `m32_beregn` falle tilbake på
    selgerlandets pakke.
    """
    t = _tenantnavn("upakket")
    with _to() as (rt, mg):
        kv = _krav(rt, t)
        # DE er ikke i registeret. Tre land er felt i 138; et fjerde
        # legges til av en migrasjon, ikke av en kunde som trenger det.
        vid = _adresse(mg, t, land="DE")
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute(
                "SELECT * FROM m32_beregn(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), "tx-de", kv, vid, "standard",
                 100_000, I_DAG, "u-test"))
        rt.rollback()
        # …OG INGEN VURDERING BLE SKREVET.
        _sett_kontekst(rt, t)
        n = rt.execute("SELECT count(*) FROM m32_vurderingene(%s,%s)",
                       (t, 100)).fetchone()[0]
        assert n == 0, "en vurdering ble skrevet uten en landpakke"


@pg
def test_ukjent_satskode_stopper_beregningen():
    """EN PAKKE UTEN DEN SATSEN ER IKKE KOMPLETT FOR DENNE LINJEN."""
    t = _tenantnavn("satskode")
    with _to() as (rt, mg):
        kv = _krav(rt, t)
        vid = _adresse(mg, t, land="DK")
        # DK har `standard` og `nullsats`, ikke `lav`.
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute(
                "SELECT * FROM m32_beregn(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), "tx-dk", kv, vid, "lav", 100_000,
                 I_DAG, "u-test"))
        rt.rollback()


@pg
def test_jurisdiksjonen_leses_fra_adressen_ikke_fra_en_parameter():
    """EN PARAMETER FOR JURISDIKSJONEN VILLE GJORT MODULEN TIL EN
    KALKULATOR SOM REGNER PÅ DET DEN FÅR BESKJED OM.

    Kalleren sier hvilken adresseversjon som gjelder; landet leses
    DERFRA. Porten måler begge halvdeler: at inn-signaturen ikke har
    noe jurisdiksjonsargument, og at svaret følger adressens land.

    MUTASJONEN SOM DREPER DENNE: legg til `p_jurisdiksjon CHAR(2)`.
    """
    t = _tenantnavn("utledet")
    with _to() as (rt, mg):
        args = mg.execute(
            "SELECT pg_get_function_arguments(p.oid) FROM pg_proc p"
            " WHERE p.proname = 'm32_beregn'").fetchone()[0]
        for forbudt in ("jurisdiksjon", "kjoperland", "p_land"):
            assert forbudt not in args, f"{forbudt} er en parameter: {args}"

        kv = _krav(rt, t, selgerland="NO")
        # KJØPEREN ER SVENSK. Svaret skal være SE, ikke selgerens NO.
        vid = _adresse(mg, t, land="SE")
        _uid, rad = _beregn(rt, t, kv, vid, satskode="lav", belop=100_000)
        assert rad[0] == "SE", rad
        # SE `lav` er 6 %: 100 000 øre → 6 000.
        assert rad[2] == 60, rad
        assert rad[3] == 6_000, rad
        assert rad[4] == "SEK", rad


@pg
def test_pakken_som_gjaldt_da_ikke_den_som_gjelder_naa():
    """KLYNGE 7s DOM, ANVENDT PÅ EN SKATTEREGEL.

    En sats som gjaldt i fjor skal forklare fjorårets transaksjon. En
    beregning som brukte dagens pakke for en gammel dato ville vært
    feil på nøyaktig den måten «en foreldet regel ser nøyaktig ut som
    en riktig regel» advarer mot.

    Porten legger en NY regelversjon som gjelder FRA I DAG, og krever
    at en transaksjon fra i går fortsatt får den gamle.

    MUTASJONEN SOM DREPER DENNE: la `m32_beregn` velge pakken etter
    `current_date` i stedet for `p_transaksjonsdato`.
    """
    t = _tenantnavn("gammel")
    with _to() as (rt, mg):
        # EN NY NORSK REGELVERSJON, GJELDENDE FRA I DAG. Skrives som
        # MIGRATOR: registeret felles i git, og porten etterligner den
        # migrasjonen som en dag gjør det.
        mg.execute(
            "INSERT INTO landpakke (landkode, regelversjon, valuta,"
            " desimaler, avrundingsregel, dokumentformat, gyldig_fra,"
            " signert_av, dom_migrasjon)"
            " VALUES ('NO',2,'NOK',2,'halv_opp','EHF 3.0',%s,"
            " 'u-test','test')"
            " ON CONFLICT DO NOTHING", (I_DAG,))
        mg.execute(
            "INSERT INTO landsats (landkode, regelversjon, satskode,"
            " promille, begrunnelse)"
            " VALUES ('NO',2,'standard',300,'hypotetisk 30 %')"
            " ON CONFLICT DO NOTHING")
        mg.execute("UPDATE landpakke SET gyldig_til = %s"
                   " WHERE landkode='NO' AND regelversjon=1",
                   (I_DAG - dt.timedelta(days=1),))
        mg.commit()
        try:
            kv = _krav(rt, t)
            vid = _adresse(mg, t, land="NO")
            # I GÅR: versjon 1, 25 %.
            _u1, gammel = _beregn(rt, t, kv, vid, belop=100_000,
                                  dato=I_DAG - dt.timedelta(days=1))
            assert gammel[1] == 1, gammel
            assert gammel[2] == 250, gammel
            assert gammel[3] == 25_000, gammel
            # I DAG: versjon 2, 30 %.
            _u2, ny = _beregn(rt, t, kv, vid, belop=100_000, dato=I_DAG)
            assert ny[1] == 2, ny
            assert ny[2] == 300, ny
            assert ny[3] == 30_000, ny
        finally:
            # REGISTERET ER GLOBALT — porten rydder etter seg, ellers
            # ville neste test målt en base denne endret.
            mg.execute("DELETE FROM jurisdiksjonsvurdering"
                       " WHERE regelversjon = 2 AND jurisdiksjon = 'NO'")
            mg.execute("DELETE FROM landsats"
                       " WHERE landkode='NO' AND regelversjon=2")
            mg.execute("DELETE FROM landpakke"
                       " WHERE landkode='NO' AND regelversjon=2")
            mg.execute("UPDATE landpakke SET gyldig_til = NULL"
                       " WHERE landkode='NO' AND regelversjon=1")
            mg.commit()


@pg
def test_avrundingen_er_landets_og_deterministisk():
    """«AVRUNDING OG VALUTA AVSTEMMES» — AKSEPTANSEKRAVET, MÅLT.

    Regelen ligger i LANDPAKKEN, og porten måler at den ANVENDES: to
    land med samme sats og samme beløp får ULIKT resultat fordi
    avrundingsregelen er ulik.

    PORTEN MÅLER GJENNOM DØRA, ikke gjennom `m32_avrund`. Den
    funksjonen er REVOKEt fra PUBLIC og gis ingen — den er modulens
    indre. En port som kalte den ville målt noe kjøretiden aldri ser.

    Det midlertidige landet felles som MIGRATOR, slik en migrasjon en
    dag gjør det: dommene felles i git, ikke gjennom en dør.

    MUTASJONEN SOM DREPER DENNE: la `m32_beregn` bruke `halv_opp`
    uansett hva pakken sier.
    """
    t = _tenantnavn("avrund")
    with _to() as (rt, mg):
        # TO HYPOTETISKE LAND, LIK SATS, ULIK AVRUNDING.
        # 10 øre × 250 promille = 2,5 → 3 opp, 2 ned.
        mg.execute(
            "INSERT INTO landpakke (landkode, regelversjon, valuta,"
            " desimaler, avrundingsregel, dokumentformat, gyldig_fra,"
            " signert_av, dom_migrasjon) VALUES"
            " ('XA',1,'XAA',2,'halv_opp','test',%s,'u-test','test'),"
            " ('XB',1,'XBB',2,'halv_ned','test',%s,'u-test','test')"
            " ON CONFLICT DO NOTHING",
            (I_DAG - dt.timedelta(days=1), I_DAG - dt.timedelta(days=1)))
        mg.execute(
            "INSERT INTO landsats (landkode, regelversjon, satskode,"
            " promille, begrunnelse) VALUES"
            " ('XA',1,'standard',250,'testsats'),"
            " ('XB',1,'standard',250,'testsats')"
            " ON CONFLICT DO NOTHING")
        mg.commit()
        try:
            kv = _krav(rt, t)
            va = _adresse(mg, t, land="XA")
            vb = _adresse(mg, t, land="XB")
            _u1, opp = _beregn(rt, t, kv, va, belop=10)
            _u2, ned = _beregn(rt, t, kv, vb, belop=10)
            assert opp[3] == 3, opp
            assert ned[3] == 2, ned
            # …OG VALUTAEN ER LANDETS.
            assert opp[4] == "XAA" and ned[4] == "XBB", (opp, ned)
            # NULLSATS ER NULL, ikke en avrundingsfeil.
            mg.execute(
                "INSERT INTO landsats (landkode, regelversjon, satskode,"
                " promille, begrunnelse)"
                " VALUES ('XA',1,'nullsats',0,'testsats')"
                " ON CONFLICT DO NOTHING")
            mg.commit()
            _u3, null = _beregn(rt, t, kv, va, satskode="nullsats",
                                belop=99_999)
            assert null[3] == 0, null
        finally:
            # REGISTERET ER GLOBALT — porten rydder etter seg.
            mg.execute("DELETE FROM jurisdiksjonsvurdering"
                       " WHERE jurisdiksjon IN ('XA','XB')")
            mg.execute("DELETE FROM landsats"
                       " WHERE landkode IN ('XA','XB')")
            mg.execute("DELETE FROM landpakke"
                       " WHERE landkode IN ('XA','XB')")
            mg.commit()


@pg
def test_samme_transaksjon_vurderes_en_gang():
    """TO VURDERINGER AV SAMME TRANSAKSJON ER TO SVAR PÅ ETT SPØRSMÅL.

    Den som leser regnskapet vet ikke hvilket som gjaldt.
    """
    t = _tenantnavn("dobbel")
    with _to() as (rt, mg):
        kv = _krav(rt, t)
        vid = _adresse(mg, t)
        _beregn(rt, t, kv, vid, ref="tx-samme")
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.UniqueViolation):
            rt.execute(
                "SELECT * FROM m32_beregn(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), "tx-samme", kv, vid, "standard",
                 200_000, I_DAG, "u-test"))
        rt.rollback()


@pg
def test_vurderingen_kan_ikke_endres_etterpaa():
    """«REGELVERSJON LAGRES PER TRANSAKSJON» — AKSEPTANSEKRAVET.

    Kunne raden endres, ville det som ER lagret vært det som gjelder
    NÅ, og oppslaget ville sett like riktig ut.
    """
    with _to() as (_rt, mg):
        for tab in ("jurisdiksjonsvurdering", "skattekrav"):
            kan = mg.execute(
                "SELECT has_table_privilege('disponit_skatt_eier', %s,"
                " 'UPDATE')", (tab,)).fetchone()[0]
            assert not kan, f"{tab} kan endres av eieren"


@pg
def test_hver_egen_tabell_har_force_rls_og_de_globale_har_ingen():
    """TO ARTER TABELLER, TO REGIMER — OG BEGGE MÅLES.

    De tre tenanttabellene har FORCE RLS. De to globale har INGEN, og
    det er riktig: det er ikke tenantdata, det er verdens regler. En
    RLS-policy der ville krevd en tenantkolonne som ikke finnes.
    """
    with _to() as (_rt, mg):
        for tab in EGNE:
            rad = mg.execute(
                "SELECT c.relrowsecurity, c.relforcerowsecurity,"
                "  EXISTS (SELECT 1 FROM pg_policy p"
                "           WHERE p.polrelid = c.oid"
                "             AND p.polname = 'tenant_isolasjon')"
                " FROM pg_class c WHERE c.relname = %s", (tab,)).fetchone()
            assert rad and all(rad), (tab, rad)
        for tab in GLOBALE:
            rad = mg.execute(
                "SELECT c.relrowsecurity FROM pg_class c"
                " WHERE c.relname = %s", (tab,)).fetchone()
            assert rad and not rad[0], f"{tab} har RLS uten tenantkolonne"


@pg
def test_en_tenant_ser_ikke_en_annens_vurdering():
    a, b = _tenantnavn("a"), _tenantnavn("b")
    with _to() as (rt, mg):
        kv = _krav(rt, a)
        vid = _adresse(mg, a)
        uid, _ = _beregn(rt, a, kv, vid)
        _krav(rt, b)
        _sett_kontekst(rt, b)
        rader = rt.execute("SELECT * FROM m32_vurderingene(%s,%s)",
                           (b, 100)).fetchall()
        assert all(r[0] != uid for r in rader)


# =====================================================================
# SVEIPEN.
# =====================================================================

@pg
def test_sveipen_finner_den_store_vurderingen_ingen_har_sett_paa():
    """MODULEN KAN IKKE RETTE EN BEREGNING.

    Da er «en stor vurdering som har stått ukontrollert lenger enn
    tenantens frist» noe av det eneste den kan si fra om — og grensen
    er TENANTENS: hva som er stort nok til å kontrolleres er en
    forretningsvurdering, ikke husets.

    PORTEN MÅLER AT SVEIPEN FAKTISK SÅ NOE. En sveip som kjørte mot
    null rader ville rapportert null funn med grønn exit-kode (130s
    lærdom).
    """
    t = _tenantnavn("stor")
    with _to() as (rt, mg):
        kv = _krav(rt, t, grense=1_000, frist=1)
        vid = _adresse(mg, t)
        uid, _ = _beregn(rt, t, kv, vid, belop=500_000)
        # VURDERINGEN ELDES HER. `beregnet_ts` har DEFAULT now() og
        # settes aldri av kalleren — den sier når MODULEN regnet, ikke
        # når transaksjonen skjedde. Migrator flytter den; kjøretiden
        # har ingen tabellrettighet (SP-7).
        _sett_kontekst(mg, t)
        mg.execute(
            "UPDATE jurisdiksjonsvurdering SET beregnet_ts = now()"
            " - INTERVAL '10 days' WHERE tenant = %s AND vurdering_id = %s",
            (t, uid))
        mg.commit()
    sv = _sv()
    try:
        rad = sv.execute("SELECT * FROM m32_sveip_skatt(%s)",
                         (10_000,)).fetchone()
        sv.commit()
        assert rad[0] >= 1, "sveipen saa ingen tenanter i det hele tatt"
    finally:
        sv.close()
    with _to() as (rt, _mg):
        _sett_kontekst(rt, t)
        funn = rt.execute("SELECT * FROM m32_skattefunn(%s,%s)",
                          (t, 100)).fetchall()
        typer = {f[1] for f in funn}
        assert "stor_vurdering_ukontrollert" in typer, typer
        assert any(f[2] == str(uid) for f in funn
                   if f[1] == "stor_vurdering_ukontrollert")


@pg
def test_sveipen_bruker_den_gjeldende_grensen_ikke_den_laveste():
    """137s LÆRDOM, ARVET FRA FØRSTE LINJE.

    `skattekrav` er append-only, så hver tenant får flere rader. En
    sveip som leste grensen med `min()` ville målt mot den laveste
    grensen som noen gang er satt — og en tenant som HEVET grensen
    ville fortsatt fått funn om alt over den gamle.

    Porten setter først en LAV grense, så en HØY, og krever at
    vurderingen IKKE blir et funn.

    MUTASJONEN SOM DREPER DENNE: bytt `ORDER BY kravversjon DESC LIMIT
    1` mot `min(manuell_kontroll_over_ore)` i sveipen.
    """
    t = _tenantnavn("grense")
    with _to() as (rt, mg):
        _krav(rt, t, grense=1_000, frist=1)          # den lave, FØRST
        kv = _krav(rt, t, grense=9_000_000, frist=1)  # den gjeldende
        vid = _adresse(mg, t)
        uid, _ = _beregn(rt, t, kv, vid, belop=500_000)
        _sett_kontekst(mg, t)
        mg.execute(
            "UPDATE jurisdiksjonsvurdering SET beregnet_ts = now()"
            " - INTERVAL '10 days' WHERE tenant = %s AND vurdering_id = %s",
            (t, uid))
        mg.commit()
    sv = _sv()
    try:
        sv.execute("SELECT * FROM m32_sveip_skatt(%s)", (10_000,))
        sv.commit()
    finally:
        sv.close()
    with _to() as (rt, _mg):
        _sett_kontekst(rt, t)
        funn = [f for f in rt.execute(
            "SELECT * FROM m32_skattefunn(%s,%s)", (t, 100)).fetchall()
            if f[1] == "stor_vurdering_ukontrollert"]
        assert not funn, (
            "sveipen maalte mot den laveste grensen som noen gang sto,"
            " ikke mot den som gjelder")


@pg
def test_sveipen_sier_fra_naar_landpakken_er_borte():
    """PAKKEN KAN UTLØPE ETTER AT TRANSAKSJONEN SKJEDDE.

    Døra nektet ikke da — pakken gjaldt. Men neste transaksjon til
    samme land vil stoppe, og det skal noen få vite FØR den gjør det.

    DET SVEIPEN RYDDER ETTER ER TIDEN.
    """
    t = _tenantnavn("borte")
    with _to() as (rt, mg):
        kv = _krav(rt, t)
        vid = _adresse(mg, t, land="DK")
        _beregn(rt, t, kv, vid)
        # PAKKEN UTLØPER I GÅR.
        mg.execute("UPDATE landpakke SET gyldig_til = %s"
                   " WHERE landkode='DK' AND regelversjon=1",
                   (I_DAG - dt.timedelta(days=1),))
        mg.commit()
    try:
        sv = _sv()
        try:
            sv.execute("SELECT * FROM m32_sveip_skatt(%s)", (10_000,))
            sv.commit()
        finally:
            sv.close()
        with _to() as (rt, _mg):
            _sett_kontekst(rt, t)
            funn = [f for f in rt.execute(
                "SELECT * FROM m32_skattefunn(%s,%s)", (t, 100)).fetchall()
                if f[1] == "jurisdiksjon_uten_pakke"]
            assert funn, "sveipen sa ikke fra om at pakken var borte"
            assert funn[0][2] == "DK", funn
    finally:
        with _to() as (_rt, mg):
            mg.execute("UPDATE landpakke SET gyldig_til = NULL"
                       " WHERE landkode='DK' AND regelversjon=1")
            mg.commit()


@pg
def test_sveiperollen_naar_en_funksjon_og_bare_den():
    """SP-7, MÅLT PÅ SVEIPEROLLEN."""
    with _to() as (_rt, mg):
        rader = mg.execute(
            "SELECT p.proname FROM pg_proc p"
            " WHERE p.pronamespace = 'public'::regnamespace"
            "   AND p.proname LIKE 'm32\\_%%'"
            "   AND has_function_privilege('disponit_skattesveip',"
            "                              p.oid, 'EXECUTE')"
            " ORDER BY 1").fetchall()
        assert [r[0] for r in rader] == ["m32_sveip_skatt"], rader


@pg
def test_sveipen_uten_tenantkontekst_ser_ikke_null_rader():
    """130s LÆRDOM: EN BLIND SVEIP RAPPORTERER NULL MED GRØNN EXIT."""
    t = _tenantnavn("blind")
    with _to() as (rt, _mg):
        _krav(rt, t)
    sv = _sv()
    try:
        rad = sv.execute("SELECT * FROM m32_sveip_skatt(%s)",
                         (10_000,)).fetchone()
        sv.commit()
        assert rad[0] >= 1, (
            "sveipen saa null tenanter — den er blind, ikke ren")
    finally:
        sv.close()


@pg
def test_de_umulige_funnene_kan_ingen_lukke():
    """132s FORM: HVEM SOM KAN LUKKE HVA."""
    t = _tenantnavn("lukkefunn")
    sveipens = ("landpakke_utloper_snart", "landpakke_uten_sats",
                "jurisdiksjon_uten_pakke", "krav_mangler")
    menneskets = ("transaksjon_uten_jurisdiksjon", "sats_uten_regelversjon",
                  "sats_uten_komplett_landpakke",
                  "landpakke_endret_gjennom_dor",
                  "stor_vurdering_ukontrollert")
    with _to() as (rt, mg):
        _sett_kontekst(mg, t)
        ider = {}
        for typ in sveipens + menneskets:
            fid = uuid.uuid4()
            ider[typ] = fid
            mg.execute(
                "INSERT INTO skattefunn (tenant, funn_id, funntype,"
                " referanse, detalj) VALUES (%s,%s,%s,'r','d')",
                (t, fid, typ))
        mg.commit()
        _sett_kontekst(rt, t)
        flagg = {r[1]: r[4] for r in rt.execute(
            "SELECT * FROM m32_skattefunn(%s,%s)", (t, 100)).fetchall()}
        for typ in sveipens:
            assert flagg[typ] is True, typ
        for typ in menneskets:
            assert flagg[typ] is False, typ
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute("SELECT m32_lukk_funn(%s,%s,%s,%s)",
                       (t, ider["landpakke_uten_sats"], "fordi", "u-test"))
        rt.rollback()
        # …MEN ET MENNESKE KAN LUKKE ET AV DE ANDRE.
        _sett_kontekst(rt, t)
        rt.execute("SELECT m32_lukk_funn(%s,%s,%s,%s)",
                   (t, ider["stor_vurdering_ukontrollert"], "kontrollert",
                    "u-test"))
        rt.commit()
        _sett_kontekst(rt, t)
        igjen = {r[1] for r in rt.execute(
            "SELECT * FROM m32_skattefunn(%s,%s)", (t, 100)).fetchall()}
        assert "stor_vurdering_ukontrollert" not in igjen
