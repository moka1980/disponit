"""M-28 logistikk- og transportagent v1 (139) — KLYNGE 10s TREDJE.

Grensen `m28-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

KLYNGENS DELTE DOM, OG INGEN MODUL VISER DEN TYDELIGERE:

  EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN ANGRES IKKE AV EN
  ROLLBACK.

Bilen kjører uansett hva basen sier. En booking som ble rullet tilbake
er fortsatt en bil på veien, en pakke i en terminal og en faktura fra
en transportør.

DEN TYNGSTE GRUPPEN PORTER MÅLER ET FRAVÆR AV KOLONNER.
`transportforslag` har ingen `bestilt_ts`, ingen `booking_ref`, ingen
`sporingsnummer` og ingen `transportor`. Forslaget ER endestasjonen —
samme form som `inngrepsforslag` fikk i 137.

EN ANNEN GRUPPE MÅLER ARVEN FRA 138. `landpakke` sier hvilke land
huset HAR LEST REGLENE FOR, og `transportforslag.landpakke_regelversjon`
er NOT NULL med fremmednøkkel dit. `farlig_gods_uten_landregel` kan
derfor aldri reises.

OG ÉN MÅLER AT FAREKLASSEN ALDRI UTLEDES: `fareklasse_oppgitt_av` er
NOT NULL, og ingen dør regner den ut. En gal påstand der er en brann i
en lastebil, ikke en feil i en rapport.

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

TRANSPORTSVEIP_DSN = os.environ.get("DISPONIT_TEST_TRANSPORTSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "139_m28_transport.sql")

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("transportkrav", "kolli", "transportforslag", "transportfunn")
ROLLENE = ("disponit_transport_eier", "disponit_transportsveip")

#: KOLONNER SOM ALDRI SKAL FINNES PÅ ET FORSLAG.
#:
#: Hver av dem ville vært en booking, og en booking angres ikke av en
#: rollback.
FORBUDTE = ("bestilt_ts", "bestilt", "booking_ref", "bookingref",
            "sporingsnummer", "transportor", "etikett", "kvittering",
            "sendt_ts", "hentet_ts")

#: ADRs NI KLASSER PLUSS `ingen`. Settet er den internasjonale
#: standarden, ikke vår oppfinnelse — og derfor komplett uten en
#: `annet`-verdi.
FAREKLASSER = (
    "ingen", "klasse_1_eksplosiver", "klasse_2_gasser",
    "klasse_3_brannfarlige_vaesker",
    "klasse_4_brannfarlige_faste_stoffer", "klasse_5_oksiderende",
    "klasse_6_giftige_og_smittefarlige", "klasse_7_radioaktive",
    "klasse_8_etsende", "klasse_9_ovrige_farlige")


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
    return koble(TRANSPORTSVEIP_DSN or MIGRATOR_DSN)


def _sett_kontekst(conn, tenant):
    conn.execute("SELECT set_config('disponit.tenant', %s, false)",
                 (tenant,))


def _tenantnavn(merke: str) -> str:
    return f"t-m28-{merke}-{secrets.token_hex(4)}"


I_DAG = dt.date.today()


# =====================================================================
# BYGGEKLOSSER.
# =====================================================================

def _krav(rt, t, *, avsenderland="NO", maks=50_000, manuell=20_000,
          frist=14):
    _sett_kontekst(rt, t)
    v = rt.execute("SELECT m28_sett_krav(%s,%s,%s,%s,%s,%s)",
                   (t, avsenderland, maks, manuell, frist,
                    "u-test")).fetchone()[0]
    rt.commit()
    return v


def _adresse(mg, t, *, land="NO", godkjent=True):
    """En adresseversjon med en kontroll.

    SKRIVES SOM MIGRATOR: M-19s dører er ikke M-28s, og porten skal
    måle transportplanen — ikke adressemodulen.
    """
    _sett_kontekst(mg, t)
    sid, vid = uuid.uuid4(), uuid.uuid4()
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
    if godkjent:
        mg.execute(
            "INSERT INTO adressekontroll (tenant, kontroll_id,"
            " versjon_id, metode, utfall, kontrollor, kilde_ref,"
            " begrunnelse, kontrollert, registrert_av)"
            " VALUES (%s,%s,%s,'dokumentert','godkjent','u-kari',"
            " 'sak-1','adressen er bekreftet',now(),'u-test')",
            (t, uuid.uuid4(), vid))
    mg.commit()
    return vid


def _kolli(rt, t, kv, *, vekt=1_000, fareklasse="ingen", ref=None):
    _sett_kontekst(rt, t)
    kid = uuid.uuid4()
    rt.execute(
        "SELECT m28_registrer_kolli(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (t, kid, ref or f"kolli-{secrets.token_hex(4)}", vekt, 300, 200,
         150, fareklasse, "u-lagermedarbeider", kv, "u-test"))
    rt.commit()
    return kid


def _foresla(rt, t, kv, kid, vid, *, grunn="raskeste rute innen SLA"):
    _sett_kontekst(rt, t)
    fid = uuid.uuid4()
    rad = rt.execute("SELECT * FROM m28_foresla(%s,%s,%s,%s,%s,%s,%s)",
                     (t, fid, kid, kv, vid, grunn, "u-test")).fetchone()
    rt.commit()
    return fid, rad


# =====================================================================
# GRENSEN OG DOMMEN.
# =====================================================================

def test_grensen_ble_registrert_for_koden():
    """§0-REGELEN: `m28-v1` sto i KRAVGRENSER før 139 fantes."""
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m28-v1"]
    assert g["maks_brudd"] == 0
    assert "ddl_begge_kjoringer_gronne" in g["krav_ja"]
    for navn in ("modulen_bestilte_transport", "modulen_ombooket",
                 "kolli_bestilt_to_ganger",
                 "forslag_uten_validert_adresse",
                 "fareklasse_utledet_av_maskin",
                 "farlig_gods_uten_landregel"):
        assert navn in g["invarianter"], navn


@pg
def test_forslaget_har_ingen_bestilling():
    """DER VEIEN SLUTTER.

    `transportforslag` har ingen `bestilt_ts`, ingen `booking_ref`,
    ingen `sporingsnummer` og ingen `transportor`. Forslaget ER
    endestasjonen, og det er ikke en forglemmelse — det er v1-dommen
    skrevet som kolonner.

    BILEN KJØRER UANSETT HVA BASEN SIER. En booking som ble rullet
    tilbake er fortsatt en bil på veien.

    MUTASJONEN SOM DREPER DENNE: legg til `bestilt_ts TIMESTAMPTZ`.
    """
    t = _tenantnavn("slutt")
    with _to() as (rt, mg):
        kolonner = {r[0] for r in mg.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name IN ('transportforslag','kolli')").fetchall()}
        for forbudt in FORBUDTE:
            assert forbudt not in kolonner, forbudt

        kv = _krav(rt, t)
        vid = _adresse(mg, t)
        kid = _kolli(rt, t, kv)
        _foresla(rt, t, kv, kid, vid)
        _sett_kontekst(rt, t)
        bilde = rt.execute("SELECT * FROM m28_bildet(%s)", (t,)).fetchone()
        assert bilde[2] == 1, "forslaget ble ikke skrevet"
        assert bilde[5] == 0, "modulen rapporterer en bestilling"


@pg
def test_fareklassen_oppgis_og_utledes_aldri():
    """EN GAL PÅSTAND OM FARLIG GODS ER EN BRANN I EN LASTEBIL.

    `kolli.fareklasse_oppgitt_av` er NOT NULL, og ingen dør regner
    klassen ut. En modul som utledet den av en produktbeskrivelse ville
    PÅSTÅTT noe om farlig gods.

    PORTEN MÅLER TRE TING: at kolonnen er NOT NULL, at
    `m28_registrer_kolli` KREVER den, og at settet er ADRs ni klasser
    uten en `annet`-verdi.

    MUTASJONEN SOM DREPER DENNE: gjør `fareklasse_oppgitt_av` nullbar.
    """
    t = _tenantnavn("fare")
    with _to() as (rt, mg):
        nullbar = mg.execute(
            "SELECT is_nullable FROM information_schema.columns"
            " WHERE table_name = 'kolli'"
            "   AND column_name = 'fareklasse_oppgitt_av'").fetchone()[0]
        assert nullbar == "NO", "fareklassen kan oppgis uten et navn"

        cd = mg.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'kolli_fareklasse_lukket'").fetchone()[0]
        for klasse in FAREKLASSER:
            assert klasse in cd, klasse
        # INGEN ÅPEN DØR I ADRs SETT. Det er den internasjonale
        # standarden og er komplett; en `annet`-verdi ville gjort et
        # lukket sett til et åpent.
        for apen in ("annet", "andre", "ukjent", "custom", "fritekst"):
            assert apen not in cd, f"«{apen}» er en aapen doer"

        # …OG DØRA KREVER NAVNET.
        kv = _krav(rt, t)
        _sett_kontekst(rt, t)
        with pytest.raises((psycopg.errors.NotNullViolation,
                            psycopg.errors.CheckViolation)):
            rt.execute(
                "SELECT m28_registrer_kolli(%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "%s,%s)",
                (t, uuid.uuid4(), "k-1", 1000, 300, 200, 150, "ingen",
                 "   ", kv, "u-test"))
        rt.rollback()


@pg
def test_ingen_dor_utleder_en_fareklasse():
    """…OG DET FINNES INGEN DØR SOM PRØVER.

    Porten over måler kolonnen. Denne måler SIGNATURENE: ingen
    `m28_*`-funksjon tar imot noe som ville latt den regne ut en
    fareklasse av innholdet — ingen produktbeskrivelse, ingen varekode,
    ingen HS-kode.

    HS-KODEN ER M-52s. En transportmodul som tok imot den ville før
    eller siden utledet fareklassen av den, og da ville
    `fareklasse_oppgitt_av` pekt på et menneske som aldri så pakken.
    """
    import re
    with _to() as (_rt, mg):
        rader = mg.execute(
            "SELECT p.proname, pg_get_function_arguments(p.oid)"
            "  FROM pg_proc p"
            " WHERE p.pronamespace = 'public'::regnamespace"
            "   AND p.proname LIKE 'm28\\_%%' ORDER BY 1").fetchall()
        assert len(rader) >= 8, f"porten maaler nesten ingenting: {rader}"
        mistenkelig = re.compile(
            r"p_(beskrivelse|varetekst|hs_?kode|varenummer|innhold"
            r"|produkt|nomenklatur)")
        funnet = {n: a for n, a in rader if mistenkelig.search(a or "")}
        assert funnet == {}, funnet


@pg
def test_et_land_uten_landpakke_stopper_planen():
    """ARVEN FRA 138, MÅLT.

    `landpakke` sier hvilke land HUSET HAR LEST REGLENE FOR. Et land
    uten pakke er et land ingen har sjekket — og for farlig gods er det
    ikke en formalitet.

    `farlig_gods_uten_landregel` kan aldri reises fordi
    `landpakke_regelversjon` er NOT NULL med fremmednøkkel: et forslag
    til et ulest land lar seg ikke skrive.

    MUTASJONEN SOM DREPER DENNE: la `m28_foresla` skrive et forslag
    uten en landpakke.
    """
    t = _tenantnavn("ulest")
    with _to() as (rt, mg):
        kv = _krav(rt, t)
        kid = _kolli(rt, t, kv, fareklasse="klasse_3_brannfarlige_vaesker")
        # DE er ikke i registeret — tre land er felt i 138.
        vid = _adresse(mg, t, land="DE")
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute("SELECT * FROM m28_foresla(%s,%s,%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), kid, kv, vid, "fordi", "u-test"))
        rt.rollback()
        _sett_kontekst(rt, t)
        n = rt.execute("SELECT count(*) FROM m28_forslagene(%s,%s)",
                       (t, 100)).fetchone()[0]
        assert n == 0, "en plan ble skrevet til et land ingen har lest"


@pg
def test_adressen_maa_vaere_godkjent_ikke_bare_finnes():
    """«ADRESSE OG TJENESTE VALIDERES FØR BOOKING» — AKSEPTANSEKRAVET.

    Tjenesten finnes ikke; adressen gjør. Døra krever en
    `adressekontroll` med `utfall = 'godkjent'` for versjonen — ikke
    bare at adressen er skrevet inn.

    MUTASJONEN SOM DREPER DENNE: fjern kontrollsjekken fra
    `m28_foresla`.
    """
    t = _tenantnavn("ukontrollert")
    with _to() as (rt, mg):
        kv = _krav(rt, t)
        kid = _kolli(rt, t, kv)
        # ADRESSEN FINNES, MEN INGEN HAR SETT PÅ DEN.
        vid = _adresse(mg, t, godkjent=False)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute("SELECT * FROM m28_foresla(%s,%s,%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), kid, kv, vid, "fordi", "u-test"))
        rt.rollback()
        # …OG MED EN GODKJENT KONTROLL GÅR DEN.
        _sett_kontekst(mg, t)
        mg.execute(
            "INSERT INTO adressekontroll (tenant, kontroll_id,"
            " versjon_id, metode, utfall, kontrollor, kilde_ref,"
            " begrunnelse, kontrollert, registrert_av)"
            " VALUES (%s,%s,%s,'dokumentert','godkjent','u-kari',"
            " 'sak-2','sett etterpaa',now(),'u-test')",
            (t, uuid.uuid4(), vid))
        mg.commit()
        _fid, rad = _foresla(rt, t, kv, kid, vid)
        assert rad[0] == "NO", rad


@pg
def test_en_avvist_kontroll_teller_ikke_som_godkjent():
    """EN KONTROLL ER IKKE ET UTFALL.

    Uten `utfall = 'godkjent'` i spørringen ville en AVVIST adresse
    talt som validert — og det er nettopp den formen kravet finnes for.
    """
    t = _tenantnavn("avvist")
    with _to() as (rt, mg):
        kv = _krav(rt, t)
        kid = _kolli(rt, t, kv)
        vid = _adresse(mg, t, godkjent=False)
        _sett_kontekst(mg, t)
        mg.execute(
            "INSERT INTO adressekontroll (tenant, kontroll_id,"
            " versjon_id, metode, utfall, kontrollor, kilde_ref,"
            " begrunnelse, kontrollert, registrert_av)"
            " VALUES (%s,%s,%s,'visuell','avvist','u-kari','sak-3',"
            " 'gata finnes ikke',now(),'u-test')",
            (t, uuid.uuid4(), vid))
        mg.commit()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute("SELECT * FROM m28_foresla(%s,%s,%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), kid, kv, vid, "fordi", "u-test"))
        rt.rollback()


@pg
def test_ett_apent_forslag_per_kolli():
    """«SAMME KOLLI BESTILLES ALDRI TO GANGER» — AKSEPTANSEKRAVET.

    I v1 bestilles ingenting i det hele tatt, men formen står likevel:
    to åpne planer for samme kolli er to biler til samme pakke.

    ET FORKASTET FORSLAG SPERRER IKKE. En plan som ble vraket skal
    kunne erstattes — og den vrakede står, fordi sletting ville fjernet
    beviset på at vi hadde den (M-50s dom, 124).

    MUTASJONEN SOM DREPER DENNE: fjern den partielle unike indeksen.
    """
    t = _tenantnavn("dobbel")
    with _to() as (rt, mg):
        kv = _krav(rt, t)
        vid = _adresse(mg, t)
        kid = _kolli(rt, t, kv)
        fid, _ = _foresla(rt, t, kv, kid, vid)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute("SELECT * FROM m28_foresla(%s,%s,%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), kid, kv, vid, "en gang til",
                        "u-test"))
        rt.rollback()
        # FORKAST, OG DEN NESTE GÅR.
        _sett_kontekst(rt, t)
        rt.execute("SELECT m28_forkast(%s,%s,%s,%s)",
                   (t, fid, "kunden avbestilte", "u-test"))
        rt.commit()
        fid2, _ = _foresla(rt, t, kv, kid, vid, grunn="ny plan etter avbud")
        assert fid2 != fid
        # …OG DEN FORKASTEDE STÅR.
        _sett_kontekst(rt, t)
        rader = rt.execute("SELECT * FROM m28_forslagene(%s,%s)",
                           (t, 100)).fetchall()
        assert len(rader) == 2, rader
        assert {r[10] for r in rader} == {"apen", "forkastet"}


@pg
def test_kolliet_kan_ikke_endres_etter_at_det_er_maalt():
    """MÅLENE ER DET ET MENNESKE MÅLTE.

    Kunne de endres, ville planen hvilt på noe annet enn det som ble
    målt — og `fareklasse_oppgitt_av` ville pekt på feil person.

    Samme for kravet: en grense som kunne endres etter at et forslag
    pekte på den ville gjort «grensen som gjaldt» til «grensen som
    gjelder nå» (137s lærdom).
    """
    with _to() as (_rt, mg):
        for tab in ("kolli", "transportkrav"):
            kan = mg.execute(
                "SELECT has_table_privilege('disponit_transport_eier',"
                " %s, 'UPDATE')", (tab,)).fetchone()[0]
            assert not kan, f"{tab} kan endres av eieren"


@pg
def test_kravet_er_append_only_med_dortildelt_versjon():
    """137/138s FORM, ARVET FRA FØRSTE LINJE.

    En versjon kalleren velger er ingen versjon: to kallere kunne valgt
    samme tall, og den siste ville stille overskrevet grensen hver plan
    pekte på.
    """
    t = _tenantnavn("krav")
    with _to() as (rt, mg):
        args = mg.execute(
            "SELECT pg_get_function_arguments(p.oid) FROM pg_proc p"
            " WHERE p.proname = 'm28_sett_krav'").fetchone()[0]
        assert "kravversjon" not in args, args
        a = _krav(rt, t, maks=50_000)
        b = _krav(rt, t, maks=60_000)
        assert b == a + 1, (a, b)
        _sett_kontekst(mg, t)
        n = mg.execute("SELECT count(*) FROM transportkrav WHERE tenant=%s",
                       (t,)).fetchone()[0]
        assert n == 2, "den gamle kravraden forsvant"


@pg
def test_hver_egen_tabell_har_force_rls_og_tenantpolicy():
    """`tenantlekkasje_i_transportregister`, MÅLT."""
    with _to() as (_rt, mg):
        mangler = []
        for tab in EGNE:
            rad = mg.execute(
                "SELECT c.relrowsecurity, c.relforcerowsecurity,"
                "  EXISTS (SELECT 1 FROM pg_policy p"
                "           WHERE p.polrelid = c.oid"
                "             AND p.polname = 'tenant_isolasjon')"
                " FROM pg_class c WHERE c.relname = %s", (tab,)).fetchone()
            if not rad or not all(rad):
                mangler.append((tab, rad))
        assert mangler == [], mangler


@pg
def test_en_tenant_ser_ikke_en_annens_kolli():
    a, b = _tenantnavn("a"), _tenantnavn("b")
    with _to() as (rt, mg):
        kv = _krav(rt, a)
        kid = _kolli(rt, a, kv)
        _krav(rt, b)
        _sett_kontekst(rt, b)
        rader = rt.execute("SELECT * FROM m28_kolliene(%s,%s)",
                           (b, 100)).fetchall()
        assert all(r[0] != kid for r in rader)


@pg
def test_modulen_kan_ikke_skrive_i_landregisteret():
    """ARVEN ER LESERETT, IKKE SKRIVERETT.

    M-32 eier landregisteret, og det felles i git. M-28 er en leser der
    som alle andre — `landpakke_endret_gjennom_dor` er M-32s invariant,
    og den ville vært verdiløs om nabomodulen kunne skrive.
    """
    with _to() as (_rt, mg):
        funnet = {}
        for rolle in ROLLENE:
            skriv = mg.execute(
                "SELECT p FROM unnest(ARRAY['INSERT','UPDATE','DELETE'])"
                " p WHERE has_table_privilege(%s, 'landpakke', p)",
                (rolle,)).fetchall()
            if skriv:
                funnet[rolle] = [r[0] for r in skriv]
        assert funnet == {}, funnet
        assert mg.execute(
            "SELECT has_table_privilege('disponit_transport_eier',"
            " 'landpakke', 'SELECT')").fetchone()[0]


@pg
def test_modulen_ser_landet_men_aldri_adressen():
    """093s KOLONNEGRANT, ARVET FRA 138.

    HVORFOR TRENGER EN TRANSPORTMODUL IKKE ADRESSEN? Fordi v1 ikke
    sender noe. Den dagen den gjør det, må granten utvides — og da skal
    det være en synlig endring i en migrasjon, ikke noe som alt lå der.
    """
    synlig = ("tenant", "versjon_id", "subjekt_id", "land", "gjelder_fra")
    skjult = ("linje1_original", "linje2_original", "postnr_original",
              "poststed_original", "linje1_normalisert",
              "postnr_normalisert", "poststed_normalisert", "notat")
    with _to() as (_rt, mg):
        ser = {}
        for kol in synlig + skjult:
            ser[kol] = mg.execute(
                "SELECT has_column_privilege('disponit_transport_eier',"
                " 'adresseversjon', %s, 'SELECT')", (kol,)).fetchone()[0]
        assert [k for k in synlig if not ser[k]] == []
        assert [k for k in skjult if ser[k]] == []


# =====================================================================
# SVEIPEN.
# =====================================================================

@pg
def test_sveipen_finner_planen_ingen_har_sett_paa():
    """DET SVEIPEN RYDDER ETTER ER TIDEN.

    Døra nektet ikke da planen ble laget — adressen var godkjent og
    landpakken gjaldt. Så gikk det døgn, og ingen gjorde noe med den.
    """
    t = _tenantnavn("sveip")
    with _to() as (rt, mg):
        kv = _krav(rt, t, frist=1)
        vid = _adresse(mg, t)
        kid = _kolli(rt, t, kv)
        fid, _ = _foresla(rt, t, kv, kid, vid)
        # PLANEN ELDES HER. `foreslatt_ts` har DEFAULT now() og settes
        # aldri av kalleren; migrator flytter den (SP-7).
        _sett_kontekst(mg, t)
        mg.execute(
            "UPDATE transportforslag SET foreslatt_ts = now()"
            " - INTERVAL '10 days' WHERE tenant = %s AND forslag_id = %s",
            (t, fid))
        mg.commit()
    sv = _sv()
    try:
        rad = sv.execute("SELECT * FROM m28_sveip_transport(%s)",
                         (10_000,)).fetchone()
        sv.commit()
        assert rad[0] >= 1, "sveipen saa ingen tenanter i det hele tatt"
    finally:
        sv.close()
    with _to() as (rt, _mg):
        _sett_kontekst(rt, t)
        funn = rt.execute("SELECT * FROM m28_transportfunn(%s,%s)",
                          (t, 100)).fetchall()
        typer = {f[1] for f in funn}
        assert "apent_forslag_over_frist" in typer, typer
        assert any(f[2] == str(fid) for f in funn
                   if f[1] == "apent_forslag_over_frist")


@pg
def test_sveipen_bruker_den_gjeldende_fristen_ikke_den_lengste():
    """137s LÆRDOM, ARVET FRA FØRSTE LINJE.

    Kravet er append-only, så hver tenant får flere rader. En sveip som
    leste fristen med `max()` ville målt mot den lengste som noen gang
    er satt — og en tenant som STRAMMET fristen ville ikke fått funnet.

    Porten setter først en LANG frist, så en KORT.

    MUTASJONEN SOM DREPER DENNE: bytt `ORDER BY kravversjon DESC LIMIT
    1` mot `max(forslagsfrist_dogn)` i sveipen.
    """
    t = _tenantnavn("frist")
    with _to() as (rt, mg):
        _krav(rt, t, frist=300)          # den lange, FØRST
        kv = _krav(rt, t, frist=1)       # den gjeldende
        vid = _adresse(mg, t)
        kid = _kolli(rt, t, kv)
        fid, _ = _foresla(rt, t, kv, kid, vid)
        _sett_kontekst(mg, t)
        mg.execute(
            "UPDATE transportforslag SET foreslatt_ts = now()"
            " - INTERVAL '10 days' WHERE tenant = %s AND forslag_id = %s",
            (t, fid))
        mg.commit()
    sv = _sv()
    try:
        sv.execute("SELECT * FROM m28_sveip_transport(%s)", (10_000,))
        sv.commit()
    finally:
        sv.close()
    with _to() as (rt, _mg):
        _sett_kontekst(rt, t)
        funn = [f for f in rt.execute(
            "SELECT * FROM m28_transportfunn(%s,%s)", (t, 100)).fetchall()
            if f[1] == "apent_forslag_over_frist"]
        assert funn, (
            "sveipen maalte mot den lengste fristen som noen gang sto")


@pg
def test_sveiperollen_naar_en_funksjon_og_bare_den():
    """SP-7, MÅLT PÅ SVEIPEROLLEN."""
    with _to() as (_rt, mg):
        rader = mg.execute(
            "SELECT p.proname FROM pg_proc p"
            " WHERE p.pronamespace = 'public'::regnamespace"
            "   AND p.proname LIKE 'm28\\_%%'"
            "   AND has_function_privilege('disponit_transportsveip',"
            "                              p.oid, 'EXECUTE')"
            " ORDER BY 1").fetchall()
        assert [r[0] for r in rader] == ["m28_sveip_transport"], rader


@pg
def test_de_umulige_funnene_kan_ingen_lukke():
    """132s FORM: HVEM SOM KAN LUKKE HVA."""
    t = _tenantnavn("lukkefunn")
    # SETTET ER NØYAKTIG DET SVEIPEN REISER.
    #
    # `tungt_kolli_ukontrollert` sto som menneskets i første utgave, og
    # sveipen reiser den. Et menneske kan ikke gjøre planen yngre, så
    # lukkingen ville blitt reist på nytt neste natt.
    #
    # `krav_mangler` reises ALDRI — løkka går over tenanter som HAR et
    # krav — og hører derfor ikke til sveipens.
    sveipens = ("apent_forslag_over_frist", "kolli_uten_forslag",
                "tungt_kolli_ukontrollert", "land_uten_pakke")
    menneskets = ("kolli_bestilt_to_ganger",
                  "fareklasse_utledet_av_maskin",
                  "farlig_gods_uten_landregel",
                  "forslag_uten_validert_adresse",
                  "krav_mangler")
    with _to() as (rt, mg):
        _sett_kontekst(mg, t)
        ider = {}
        for typ in sveipens + menneskets:
            fid = uuid.uuid4()
            ider[typ] = fid
            mg.execute(
                "INSERT INTO transportfunn (tenant, funn_id, funntype,"
                " referanse, detalj) VALUES (%s,%s,%s,'r','d')",
                (t, fid, typ))
        mg.commit()
        _sett_kontekst(rt, t)
        flagg = {r[1]: r[4] for r in rt.execute(
            "SELECT * FROM m28_transportfunn(%s,%s)", (t, 100)).fetchall()}
        for typ in sveipens:
            assert flagg[typ] is True, typ
        for typ in menneskets:
            assert flagg[typ] is False, typ
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute("SELECT m28_lukk_funn(%s,%s,%s,%s)",
                       (t, ider["land_uten_pakke"], "fordi", "u-test"))
        rt.rollback()
        # …OG DØRA NEKTER OGSÅ FOR `tungt_kolli_ukontrollert`, som
        # sveipen reiser. Et menneske kan ikke gjøre planen yngre.
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute("SELECT m28_lukk_funn(%s,%s,%s,%s)",
                       (t, ider["tungt_kolli_ukontrollert"],
                        "kontrollert", "u-test"))
        rt.rollback()
        # …MEN LAR ET MENNESKE LUKKE ET AV DE ANDRE. Uten denne
        # halvdelen kunne doera nektet ALT og porten vaert groenn.
        _sett_kontekst(rt, t)
        rt.execute("SELECT m28_lukk_funn(%s,%s,%s,%s)",
                   (t, ider["farlig_gods_uten_landregel"], "avklart",
                    "u-test"))
        rt.commit()
        _sett_kontekst(rt, t)
        igjen = {r[1] for r in rt.execute(
            "SELECT * FROM m28_transportfunn(%s,%s)", (t, 100)).fetchall()}
        assert "farlig_gods_uten_landregel" not in igjen


@pg
def test_den_ekte_vakten_er_indeksen_ikke_dora():
    """DØRA GIR EN LESBAR FEIL. INDEKSEN ER DEN SOM BITER.

    `test_ett_apent_forslag_per_kolli` over måler DØRA — og den var
    grønn da den partielle unike indeksen ble droppet i en
    mutasjonstest 6/9, fordi dørens egen `IF EXISTS` fanget det først.

    EN PORT SOM BARE MÅLER DØRA GJØR DEN STRUKTURELLE GARANTIEN
    USYNLIG. Faller indeksen bort, står bare høflighetssjekken igjen —
    og den kan omgås av enhver som skriver i tabellen på en annen vei.

    Derfor to porter: én på oppførselen, én på katalogen. Det er samme
    par som `playbooksteg` fikk i 137 — CHECK-en er vakten, døra er
    beskjeden.

    MUTASJONEN SOM DREPER DENNE: `DROP INDEX
    transportforslag_ett_apent_per_kolli`.
    """
    t = _tenantnavn("indeks")
    with _to() as (rt, mg):
        rad = mg.execute(
            "SELECT pg_get_indexdef(i.indexrelid)"
            "  FROM pg_index i"
            " WHERE i.indexrelid ="
            "       'transportforslag_ett_apent_per_kolli'::regclass"
        ).fetchone()
        assert rad, "den partielle unike indeksen finnes ikke"
        d = rad[0]
        assert "UNIQUE" in d, d
        assert "kolli_id" in d, d
        # PARTIELL: et FORKASTET forslag skal ikke sperre for et nytt.
        assert "WHERE" in d and "apen" in d, d

        # …OG DEN BITER, MÅLT UTENOM DØRA.
        #
        # Skrives som MIGRATOR: kjøretiden har ingen tabellrettighet
        # (SP-7), og poenget er nettopp å omgå dørens høflighetssjekk.
        kv = _krav(rt, t)
        vid = _adresse(mg, t)
        kid = _kolli(rt, t, kv)
        fid, _ = _foresla(rt, t, kv, kid, vid)
        _sett_kontekst(mg, t)
        rad = mg.execute(
            "SELECT kravversjon, adressekontroll_id, mottakerland,"
            " avsenderland, landpakke_regelversjon, fareklasse"
            " FROM transportforslag WHERE tenant=%s AND forslag_id=%s",
            (t, fid)).fetchone()
        with pytest.raises(psycopg.errors.UniqueViolation):
            mg.execute(
                "INSERT INTO transportforslag (tenant, forslag_id,"
                " kolli_id, kravversjon, adresseversjon_id,"
                " adressekontroll_id, mottakerland, avsenderland,"
                " landpakke_regelversjon, fareklasse, begrunnelse,"
                " foreslatt_av)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'utenom doera',"
                " 'u-test')",
                (t, uuid.uuid4(), kid, rad[0], vid, rad[1], rad[2],
                 rad[3], rad[4], rad[5]))
        mg.rollback()
