"""M-20 nettside- og innholdsagent v1 (134) — KLYNGE 9s ANDRE.

Grensen `m20-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

KLYNGENS DELTE DOM:

  EN YTRING AVGITT I HUSETS NAVN KAN IKKE TAS TILBAKE — OG DEN SOM
  LESER DEN VET IKKE AT EN MASKIN SKREV DEN.

En rollback fjerner siden. Den fjerner ikke at noen leste den. Derfor
måler den tyngste gruppen porter her at veien TILBAKE finnes FØR veien
FRAM tas — ikke at den kan finnes ut av etterpå.

TRE PORTER MÅLER ET FRAVÆR SOM ER ET BEVIS:

  `paastand_uten_kilde`, `publisering_uten_forhaandsvisning` og
  `publisering_uten_rollbackvei` står i funntypesettet OG kan aldri
  reises. Det er 133s form: et nekt som kommer etter er ikke et nekt.

DEN FJERDE GRUPPEN MÅLER AT MODULEN ARVET RIKTIG. `kildedokument`
(118) er husets kilderegister, og en port her faller hvis noen lager
et nummer to.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import ast
import contextlib
import datetime
import json
import os
import re
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN  # noqa: F401

INNHOLDSSVEIP_DSN = os.environ.get("DISPONIT_TEST_INNHOLDSSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "134_m20_innhold.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "innhold.js")
FUNDAMENT = ROT / "docs" / "KLYNGE9-FUNDAMENT.md"
MODULFILER = (
    ROT / "platform" / "core" / "api" / "innhold.py",
    ROT / "platform" / "drift" / "innholdssveip.py",
    ROT / "platform" / "drift" / "kjor_innholdssveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

#: TABELLENE MODULEN EIER. `kildedokument` STÅR IKKE HER, og det er
#: hele arven: den er M-46s (118) og huset deler den.
EGNE = ("innholdskrav", "innholdsutkast", "innholdspaastand",
        "innholdsvisning", "innholdspublisering", "innholdsfunn")

_STRENG = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")


def _bare_kode(fil: Path, *, uten_strenger: bool = False) -> str:
    """Filens innhold uten kommentarer OG uten docstrings (m24-formen).

    PORTEN SKAL MÅLE HANDLINGEN, IKKE BEGRUNNELSEN. Kommentarene her
    er fulle av ordet «publiser», nettopp fordi modulen ikke gjør det.
    """
    tekst = fil.read_text(encoding="utf-8")
    linjer = tekst.splitlines()
    if fil.suffix == ".py":
        for node in ast.walk(ast.parse(tekst)):
            krop = getattr(node, "body", None)
            if not isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef,
                                     ast.AsyncFunctionDef)) or not krop:
                continue
            forst = krop[0]
            if (isinstance(forst, ast.Expr)
                    and isinstance(forst.value, ast.Constant)
                    and isinstance(forst.value.value, str)):
                for i in range(forst.lineno - 1, forst.end_lineno):
                    linjer[i] = ""
        merke = "#"
    else:
        merke = "--" if fil.suffix == ".sql" else "//"
    ut = "\n".join(x for x in linjer if not x.lstrip().startswith(merke))
    return _STRENG.sub("''", ut) if uten_strenger else ut


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


def _mig():
    from db.pg import koble
    return koble(MIGRATOR_DSN)


def _sv():
    from db.pg import koble
    return koble(INNHOLDSSVEIP_DSN or MIGRATOR_DSN)


def _sett_kontekst(conn, tenant):
    conn.execute("SELECT set_config('disponit.tenant', %s, false)",
                 (tenant,))


def _tenantnavn(merke: str) -> str:
    return f"t-m20-{merke}-{secrets.token_hex(4)}"


I_DAG = datetime.date.today()


# =====================================================================
# BYGGEKLOSSER.
# =====================================================================

def _krav(rt, t, *, kilde_dogn=365, visning_min=60, varsel=30):
    _sett_kontekst(rt, t)
    v = rt.execute("SELECT m20_sett_krav(%s,%s,%s,%s,%s)",
                   (t, kilde_dogn, visning_min, varsel, "u-test")
                   ).fetchone()[0]
    rt.commit()
    return v


def _kilde(rt, t, *, tittel=None, type_="testrapport", gyldig_til=None):
    _sett_kontekst(rt, t)
    kid = uuid.uuid4()
    tittel = tittel or f"Kilde {secrets.token_hex(3)}"
    sum_ = secrets.token_hex(32)
    kid = rt.execute("SELECT m20_registrer_kilde(%s,%s,%s,%s,%s,%s,%s)",
                     (t, kid, tittel, type_, gyldig_til, sum_, "u-test")
                     ).fetchone()[0]
    rt.commit()
    return kid


def _utkast(rt, t, side, tekst, kilde, *, basert=None, rollback=None,
            med_paastand=True):
    _sett_kontekst(rt, t)
    uid = uuid.uuid4()
    v = rt.execute("SELECT m20_registrer_utkast(%s,%s,%s,%s,%s,%s,%s)",
                   (t, uid, side, json.dumps({"h1": tekst}), basert,
                    rollback, "u-test")).fetchone()[0]
    if med_paastand:
        rt.execute("SELECT m20_registrer_paastand(%s,%s,%s,%s,%s,%s,%s)",
                   (t, uuid.uuid4(), uid, 1, tekst, kilde, "u-test"))
    rt.commit()
    return uid, v


def _visning(rt, t, uid, *, for_="u-per"):
    _sett_kontekst(rt, t)
    vid = uuid.uuid4()
    rt.execute("SELECT m20_registrer_visning(%s,%s,%s,%s,%s)",
               (t, vid, uid, for_, "u-test"))
    rt.commit()
    return vid


def _klar(rt, t, uid):
    _sett_kontekst(rt, t)
    rt.execute("SELECT m20_merk_klar(%s,%s,%s)", (t, uid, "u-test"))
    rt.commit()


def _publiser(rt, t, uid, vid, *, av="u-per"):
    _sett_kontekst(rt, t)
    pid = uuid.uuid4()
    rad = rt.execute("SELECT * FROM m20_publiser(%s,%s,%s,%s,%s,%s)",
                     (t, pid, uid, vid, av, "u-test")).fetchone()
    rt.commit()
    return rad


def _side(rt, t, side, tekst, kilde, **kw):
    """Hele veien fra utkast til publisert, som ett steg."""
    uid, v = _utkast(rt, t, side, tekst, kilde, **kw)
    vid = _visning(rt, t, uid)
    _klar(rt, t, uid)
    return uid, vid, v, _publiser(rt, t, uid, vid)


def _nektes(mg, t, sql, args, *, teller_sql, teller_args):
    """EN VAKT SOM IKKE FÅR NOE Å BITE I, BITER IKKE.

    `set_config('disponit.tenant', ..., false)` er SESJONSNIVÅ, men den
    settes inne i en transaksjon — og RULLES TILBAKE MED DEN. En port
    som gjør `rollback()` og deretter prøver neste setning uten å sette
    konteksten på nytt, treffer NULL RADER under FORCE RLS: ingen
    trigger fyrer, ingenting reises, og porten er grønn uten å ha
    prøvd vakten.

    Jeg skrev nøyaktig den feilen 5/9, og den så ut som en manglende
    vakt i migrasjonen. Derfor gjør denne hjelperen tre ting i
    rekkefølge: setter konteksten, MÅLER at raden faktisk er synlig,
    og først da forventer nektet.
    """
    _sett_kontekst(mg, t)
    synlig = mg.execute(teller_sql, teller_args).fetchone()[0]
    assert synlig == 1, (
        f"porten ville vaert tom: {synlig} rader synlige — konteksten"
        " er borte, og da maaler ingen vakt noe")
    with pytest.raises(psycopg.errors.RaiseException):
        mg.execute(sql, args)
    mg.rollback()


def _eldes_visning(mg, t, visning_id, *, minutter):
    """VISNINGEN BLIR GAMMEL, OG DET KAN BARE TIDEN GJØRE.

    Raden er frosset av `m20_visningsvakt`, og det er riktig — derfor
    må vakten kobles ut for å fabrikkere tilstanden. Migrator eier
    tabellen og kan det; INGEN ANNEN KAN. At fabrikkeringen krever
    dette er selv en måling: uten `DISABLE TRIGGER` finnes det ingen
    vei til en endret visning i det hele tatt.
    """
    _sett_kontekst(mg, t)
    mg.execute("ALTER TABLE innholdsvisning DISABLE TRIGGER m20_visningsvakt")
    mg.execute("UPDATE innholdsvisning SET vist_ts = now()"
               " - make_interval(mins => %s)"
               " WHERE tenant=%s AND visning_id=%s", (minutter, t, visning_id))
    mg.execute("ALTER TABLE innholdsvisning ENABLE TRIGGER m20_visningsvakt")
    mg.commit()


def _eldes_kilde(mg, t, kilde_id, *, dager_siden_utlop=1):
    """KILDEN UTLØPER ETTER AT SIDEN STO UTE.

    Dette kan ikke gjøres gjennom en dør, og det er meningen:
    `m20_registrer_paastand` NEKTER på en utløpt kilde. Tilstanden
    sveipen finnes for oppstår bare med tidens gang, og her
    fabrikkeres den ærlig — som i 133s `_aldre_mote`.
    """
    _sett_kontekst(mg, t)
    mg.execute("UPDATE kildedokument SET gyldig_til = current_date - %s"
               " WHERE tenant=%s AND kilde_id=%s",
               (dager_siden_utlop, t, kilde_id))
    mg.commit()


# =====================================================================
# §0: GRENSEN, OG HVER INVARIANT SIN PORT.
# =====================================================================

def test_grensen_er_registrert_og_hver_invariant_har_en_port():
    """§0-REGELEN, MÅLT.

    Grensen ble registrert før koden. Denne porten faller hvis noen
    legger til en invariant uten å måle den — eller måler noe grensen
    ikke navngir.
    """
    from manifestskjema import KRAVGRENSER
    grense = KRAVGRENSER["m20-v1"]
    assert grense["maks_brudd"] == 0
    tekst = Path(__file__).read_text(encoding="utf-8")
    uten = [i for i in grense["invarianter"] if i not in tekst]
    assert uten == [], f"invarianter uten port: {uten}"


def test_ui_axe_alvorlige_brudd_dekkes_av_flatens_egen_suite():
    """PEKER, IKKE KOPI: to steder som måler det samme kunne gitt to
    svar."""
    js = ROT / "platform" / "core" / "ui" / "test" / "innhold.test.js"
    assert js.exists(), "flatens egen suite mangler"
    tekst = js.read_text(encoding="utf-8")
    assert "axe" in tekst.lower() or "aria" in tekst.lower()


# =====================================================================
# DE TRE FUNNENE SOM ALDRI KAN REISES. BEVISET.
# =====================================================================

@pg
def test_en_paastand_uten_kilde_er_urepresenterbar():
    """`paastand_uten_kilde` — OG DEN KAN ALDRI REISES.

    Ikke fordi sveipen er flink, men fordi kolonnen er NOT NULL med
    fremmednøkkel til `kildedokument`. Det finnes ingen kallform som
    lager raden, og derfor ingen sveip som kan finne den.

    MUTASJONEN SOM DREPER DENNE PORTEN: gjør `kilde_id` nullable.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("utenkilde")
        _krav(rt, t)
        kid = _kilde(rt, t)
        uid, _ = _utkast(rt, t, "forsiden", "Raskest", kid)
        # DØRA TAR IMOT EN NULL, MEN BASEN GJØR DET IKKE.
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.Error):
            rt.execute("SELECT m20_registrer_paastand(%s,%s,%s,%s,%s,"
                       "%s,%s)",
                       (t, uuid.uuid4(), uid, 2, "Uten kilde", None,
                        "u-test"))
        rt.rollback()
        # …OG HELLER IKKE FORBI DØRA.
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.NotNullViolation):
            mg.execute(
                "INSERT INTO innholdspaastand (tenant, paastand_id,"
                " utkast_id, rekkefolge, tekst, kilde_id, kilde_sha256,"
                " registrert_av) VALUES (%s,%s,%s,3,'x',NULL,%s,'u')",
                (t, uuid.uuid4(), uid, "0" * 64))
        mg.rollback()
        # …og en kilde som ikke finnes er heller ingen kilde.
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            mg.execute(
                "INSERT INTO innholdspaastand (tenant, paastand_id,"
                " utkast_id, rekkefolge, tekst, kilde_id, kilde_sha256,"
                " registrert_av) VALUES (%s,%s,%s,4,'x',%s,%s,'u')",
                (t, uuid.uuid4(), uid, uuid.uuid4(), "0" * 64))
        mg.rollback()


@pg
def test_en_publisering_uten_forhaandsvisning_er_urepresenterbar():
    """`publisering_uten_forhaandsvisning`. Samme form, samme bevis.

    `visning_id` er NOT NULL med fremmednøkkel. Det finnes ingen vei
    forbi forhåndsvisningen — heller ikke en som logger at man gikk
    forbi.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("utenvisning")
        _krav(rt, t)
        kid = _kilde(rt, t)
        uid, _ = _utkast(rt, t, "forsiden", "Raskest", kid)
        _klar(rt, t, uid)
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.NotNullViolation):
            mg.execute(
                "INSERT INTO innholdspublisering (tenant,"
                " publisering_id, utkast_id, side_id, versjon,"
                " visning_id, publisert_av, rollbackform)"
                " VALUES (%s,%s,%s,'forsiden',1,NULL,'u','avpublisering')",
                (t, uuid.uuid4(), uid))
        mg.rollback()


@pg
def test_en_publisering_uten_rollbackvei_er_urepresenterbar():
    """`publisering_uten_rollbackvei`.

    `rollbackform` er et LUKKET SETT MED TO VERDIER, og begge er en
    vei. Den første publiseringen av en side har ingen forrige versjon
    — da er veien AVPUBLISERING, og den er fortsatt en vei. «Ingen vei
    tilbake» er ikke en verdi i settet, og kan ikke skrives inn i det.

    MUTASJONEN SOM DREPER DENNE: legg 'ingen' til i CHECK-en.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("utenvei")
        _krav(rt, t)
        kid = _kilde(rt, t)
        uid, vid, _, rad = _side(rt, t, "forsiden", "Raskest", kid)
        assert rad[1] == "avpublisering" and rad[2] is None
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.CheckViolation):
            mg.execute(
                "INSERT INTO innholdspublisering (tenant,"
                " publisering_id, utkast_id, side_id, versjon,"
                " visning_id, publisert_av, rollbackform)"
                " VALUES (%s,%s,%s,'forsiden',9,%s,'u','ingen')",
                (t, uuid.uuid4(), uid, vid))
        mg.rollback()
        # …OG FORMEN UTEN SIN PEKER ER OGSÅ UMULIG: «forrige_versjon»
        # uten et nummer å gå tilbake til er «ingen vei» skrevet med et
        # pent ord.
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.CheckViolation):
            mg.execute(
                "INSERT INTO innholdspublisering (tenant,"
                " publisering_id, utkast_id, side_id, versjon,"
                " visning_id, publisert_av, rollbackform)"
                " VALUES (%s,%s,%s,'forsiden',9,%s,'u','forrige_versjon')",
                (t, uuid.uuid4(), uid, vid))
        mg.rollback()


@pg
def test_de_tre_umulige_staar_likevel_i_funntypesettet():
    """AT DE STÅR OG ER UMULIGE ER HELE BEVISET.

    Et sett som ikke navnga dem ville ikke sagt noe. Et sett som
    navnga dem OG kunne fylles ville sagt at vernet er en sveip.
    """
    with _mig() as mg:
        uttrykk = mg.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'innholdsfunn_type_lukket'").fetchone()[0]
    for umulig in ("paastand_uten_kilde",
                   "publisering_uten_forhaandsvisning",
                   "publisering_uten_rollbackvei"):
        assert umulig in uttrykk, umulig


# =====================================================================
# V1-DOMMEN: MODULEN PUBLISERER INGENTING SELV.
# =====================================================================

@pg
def test_modulen_publiserte_selv_er_umulig_publisering_uten_menneske():
    """`modulen_publiserte_selv` OG `publisering_uten_menneske`.

    `publisert_av` er NOT NULL, døra nekter på tomt, og ingen kodevei
    skriver en publisering uten et navn. De to invariantene er samme
    kolonne målt fra to kanter: den ene at modulen ikke kan, den andre
    at ingen kan uten å si hvem.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("navnloes")
        _krav(rt, t)
        kid = _kilde(rt, t)
        uid, _ = _utkast(rt, t, "forsiden", "Raskest", kid)
        vid = _visning(rt, t, uid)
        _klar(rt, t, uid)
        for tom in ("", "   "):
            _sett_kontekst(rt, t)
            with pytest.raises(psycopg.errors.RaiseException) as e:
                rt.execute("SELECT * FROM m20_publiser(%s,%s,%s,%s,%s,%s)",
                           (t, uuid.uuid4(), uid, vid, tom, "u-test"))
            assert "publisert_av mangler" in str(e.value)
            rt.rollback()
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.NotNullViolation):
            mg.execute(
                "INSERT INTO innholdspublisering (tenant,"
                " publisering_id, utkast_id, side_id, versjon,"
                " visning_id, publisert_av, rollbackform)"
                " VALUES (%s,%s,%s,'forsiden',1,%s,NULL,'avpublisering')",
                (t, uuid.uuid4(), uid, vid))
        mg.rollback()


def test_ingen_kodevei_publiserer_uten_et_menneske():
    """MÅLT I KODEN, IKKE BARE I BASEN.

    KOMMENTARER OG STRENGER FJERNES FØRST (128s lærdom): filhodene her
    er fulle av ordet «publiser» nettopp fordi modulen ikke gjør det,
    og en port mot rå filtekst ville truffet forklaringen.
    """
    for fil in MODULFILER:
        if not fil.exists():
            continue
        kode = _bare_kode(fil, uten_strenger=True).lower()
        for forbudt in ("autopubliser", "auto_publiser",
                        "publiser_selv", "publiser_automatisk"):
            assert forbudt not in kode, f"{fil.name}: {forbudt}"


@pg
def test_sveipen_publiserer_ingenting_og_lukker_ingen_side():
    """SVEIPEN SIER FRA, OG DER STOPPER DEN.

    Målt mot rettighetene og ikke mot koden: sveiperollen har EXECUTE
    på ÉN funksjon, og den funksjonen skriver bare i funntabellen.
    """
    with _mig() as mg:
        naar = [r[0] for r in mg.execute(
            "SELECT p.proname FROM pg_proc p"
            " WHERE p.proname LIKE 'm20\\_%'"
            "   AND has_function_privilege('disponit_innholdssveip',"
            "                              p.oid, 'EXECUTE')").fetchall()]
        skriver = mg.execute(
            "SELECT pg_get_functiondef(p.oid) FROM pg_proc p"
            " WHERE p.proname = 'm20_sveip_innhold'").fetchone()[0]
    assert sorted(naar) == ["m20_sveip_innhold"]
    kropp = _STRENG.sub("''", skriver)
    # BEGGE SKRIVEMÅTENE. `public.`-prefikset er en VANE i denne
    # filen, ikke en regel: en fremtidig setning uten det ville gått
    # rett forbi en port som bare lette etter den kvalifiserte formen,
    # og porten ville stått grønn.
    for tabell in ("innholdspublisering", "innholdsutkast",
                   "innholdspaastand", "innholdsvisning"):
        for form in (f"public.{tabell}", tabell):
            assert f"INSERT INTO {form}" not in kropp, (tabell, form)
            assert f"UPDATE {form}" not in kropp, (tabell, form)


# =====================================================================
# KILDEKRAVET.
# =====================================================================

@pg
def test_en_utloept_kilde_nektes_i_doera_ikke_i_en_sveip():
    """133s FORM: ET NEKT SOM KOMMER ETTER ER IKKE ET NEKT.

    En påstand som ble skrevet på et utløpt datablad og oppdaget
    natten etter, er en påstand som sto ute i et døgn.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("utloept")
        _krav(rt, t)
        gammel = _kilde(rt, t, gyldig_til=I_DAG - datetime.timedelta(days=1))
        uid, _ = _utkast(rt, t, "forsiden", "Raskest", gammel,
                         med_paastand=False)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute("SELECT m20_registrer_paastand(%s,%s,%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), uid, 1, "Raskest", gammel,
                        "u-test"))
        assert "utloept" in str(e.value)
        rt.rollback()
        del mg


@pg
def test_klar_nektes_naar_en_kilde_utloep_etter_at_paastanden_ble_skrevet():
    """DEN VANSKELIGE: kilden var gyldig da påstanden ble skrevet.

    Da hjelper det ikke at døra nektet den gangen. `m20_merk_klar`
    måler PÅ NYTT, fordi et utkast som ble klart med et utløpt datablad
    er klart til å publisere en udokumentert påstand.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("utloeptetter")
        _krav(rt, t)
        kid = _kilde(rt, t)
        uid, _ = _utkast(rt, t, "forsiden", "Raskest", kid)
        _eldes_kilde(mg, t, kid)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute("SELECT m20_merk_klar(%s,%s,%s)", (t, uid, "u-test"))
        assert "utloept kilde" in str(e.value)
        rt.rollback()


@pg
def test_publisering_nektes_naar_kilden_utloep_mellom_klar_og_publiser():
    """OG ÉN GANG TIL, I DEN SISTE DØRA.

    Vinduet mellom «klar» og «publiser» er der et menneske tenker seg
    om. En kilde kan utløpe i det vinduet, og da er det siste målingen
    som gjelder — ikke den som ble gjort da noen sa ja.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("mellomrom")
        _krav(rt, t)
        kid = _kilde(rt, t)
        uid, _ = _utkast(rt, t, "forsiden", "Raskest", kid)
        vid = _visning(rt, t, uid)
        _klar(rt, t, uid)
        _eldes_kilde(mg, t, kid)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute("SELECT * FROM m20_publiser(%s,%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), uid, vid, "u-per", "u-test"))
        assert "utloept kilde" in str(e.value)
        rt.rollback()


@pg
def test_paastanden_baerer_kildesummen_slik_den_var():
    """`kilde_sha256` KOPIERES INN, og det er ikke pynt.

    Uten den kan ingen etterpå vise at det var NØYAKTIG denne
    versjonen av testrapporten som ble sitert. 118s ord, og de gjelder
    her.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("sum")
        _krav(rt, t)
        kid = _kilde(rt, t)
        uid, _ = _utkast(rt, t, "forsiden", "Raskest", kid)
        _sett_kontekst(rt, t)
        rad = rt.execute("SELECT * FROM m20_utkastet(%s,%s)",
                         (t, uid)).fetchone()
        kilde = mg is not None
        rt.rollback()
        _sett_kontekst(mg, t)
        fasit = mg.execute("SELECT innhold_sha256 FROM kildedokument"
                           " WHERE tenant=%s AND kilde_id=%s",
                           (t, kid)).fetchone()[0]
        mg.rollback()
    assert kilde
    assert rad[6] == fasit, "paastanden baerer ikke kildens sum"


# =====================================================================
# FORHÅNDSVISNINGEN OG ROLLBACKVEIEN.
# =====================================================================

@pg
def test_forhaandsvisningen_maa_gjelde_noeyaktig_dette_utkastet():
    """`publisering_uten_forhaandsvisning` — DEN VANSKELIGE HALVDELEN.

    At det FINNES en visning måles av fremmednøkkelen. At den gjelder
    DET SOM PUBLISERES måles her.

    OG DET ER ALT SOM TRENGS, fordi utkastet er append-only: en endring
    er en NY versjon med en NY rad, og den nye raden har ingen visning.
    «Utkastet ble endret under visningen» er derfor ikke en tilstand
    modulen kan komme i — den er utelukket av formen, ikke oppdaget av
    en sjekk. Summenkontrollen i `m20_publiser` blir stående som vern
    mot en fremtidig migrasjon som gjør utkastet muterbart, og porten
    under måler at den forutsetningen faktisk holder i dag.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("visningen")
        _krav(rt, t)
        kid = _kilde(rt, t)
        u1, _ = _utkast(rt, t, "forsiden", "Raskest i klassen", kid)
        v1 = _visning(rt, t, u1)
        # NY VERSJON = NY RAD, og den har ingen visning.
        u2, _ = _utkast(rt, t, "forsiden", "Nest raskest", kid)
        _klar(rt, t, u2)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute("SELECT * FROM m20_publiser(%s,%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), u2, v1, "u-per", "u-test"))
        assert "annet utkast" in str(e.value)
        rt.rollback()
        # FORUTSETNINGEN, MÅLT: hver visning bærer sitt utkasts sum, og
        # utkastet kan ikke endres. Faller denne, er summenkontrollen i
        # døra plutselig det eneste vernet — og da skal noen vite det.
        _sett_kontekst(mg, t)
        avvik = mg.execute(
            "SELECT count(*) FROM innholdsvisning v"
            "  JOIN innholdsutkast u ON u.tenant=v.tenant"
            "   AND u.utkast_id=v.utkast_id"
            " WHERE v.tenant=%s AND v.vist_hash <> u.innholds_hash",
            (t,)).fetchone()[0]
        mg.rollback()
        assert avvik == 0
        _nektes(mg, t,
                "UPDATE innholdsutkast SET innhold='{\"h1\":\"x\"}'"
                " WHERE tenant=%s AND utkast_id=%s", (t, u1),
                teller_sql="SELECT count(*) FROM innholdsutkast"
                           " WHERE tenant=%s AND utkast_id=%s",
                teller_args=(t, u1))


@pg
def test_en_for_gammel_forhaandsvisning_er_ingen_forhaandsvisning():
    """ET MENNESKE SOM SÅ NOE FOR TRE UKER SIDEN HAR IKKE SETT DETTE.

    Vinduet er TENANTENS (`visning_gyldig_min`), ikke vårt: en
    nettbutikk og en legemiddelprodusent tåler ikke det samme.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("gammelvisning")
        _krav(rt, t, visning_min=10)
        kid = _kilde(rt, t)
        uid, _ = _utkast(rt, t, "forsiden", "Raskest", kid)
        vid = _visning(rt, t, uid)
        _klar(rt, t, uid)
        _eldes_visning(mg, t, vid, minutter=11)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute("SELECT * FROM m20_publiser(%s,%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), uid, vid, "u-per", "u-test"))
        assert "eldre enn" in str(e.value)
        rt.rollback()


@pg
def test_rollbackveien_regnes_ut_foer_veien_fram_tas_og_fryses():
    """DEN VIKTIGSTE PORTEN I MODULEN.

    En rollback som skulle vært funnet ut av etterpå er ingen rollback
    — det er et håp. Veien står på raden ved publisering, og
    radvakten fryser den.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("veien")
        _krav(rt, t)
        kid = _kilde(rt, t)
        # FØRSTE publisering: ingen forrige versjon å falle tilbake
        # til, og da er veien AVPUBLISERING — som fortsatt er en vei.
        _, _, _, rad1 = _side(rt, t, "forsiden", "Raskest", kid)
        assert (rad1[1], rad1[2]) == ("avpublisering", None)
        # ANDRE: nå finnes det en forrige, og den navngis med nummer.
        _, _, v2, rad2 = _side(rt, t, "forsiden", "Nest raskest", kid,
                               basert=1)
        assert (rad2[1], rad2[2]) == ("forrige_versjon", 1)
        assert v2 == 2
        # …OG DEN ER FROSSET.
        for felt, verdi in (("rollbackform", "'avpublisering'"),
                            ("rollback_til_versjon", "9")):
            _nektes(mg, t,
                    f"UPDATE innholdspublisering SET {felt}={verdi}"
                    " WHERE tenant=%s AND publisering_id=%s",
                    (t, rad2[0]),
                    teller_sql="SELECT count(*) FROM innholdspublisering"
                               " WHERE tenant=%s AND publisering_id=%s",
                    teller_args=(t, rad2[0]))


@pg
def test_to_levende_versjoner_av_samme_side_er_umulig():
    """«HVA STO DER» SKAL HA ETT SVAR.

    Publiseringen av versjon 2 ruller ut versjon 1 i SAMME
    transaksjon. Var det to steg, ville det funnes et vindu der siden
    hadde to svar — og et vindu er nok.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("tolevende")
        _krav(rt, t)
        kid = _kilde(rt, t)
        _side(rt, t, "forsiden", "Raskest", kid)
        _side(rt, t, "forsiden", "Nest raskest", kid, basert=1)
        _sett_kontekst(mg, t)
        levende = mg.execute(
            "SELECT versjon FROM innholdspublisering"
            " WHERE tenant=%s AND side_id='forsiden' AND tilbake_ts IS NULL",
            (t,)).fetchall()
        mg.rollback()
    assert [r[0] for r in levende] == [2], levende


@pg
def test_rollback_gjenoppretter_forrige_versjon_som_en_ny_periode():
    """HVER PERIODE EN VERSJON VAR LEVENDE ER SIN EGEN RAD.

    Første utkast hadde `UNIQUE (tenant, utkast_id)`, og den gjorde
    GJENOPPRETTING UREPRESENTERBAR — altså nøyaktig veien modulen
    krever at finnes. En rad som ble «levende igjen» ved at
    tilbakerullingen ble visket ut, ville dessuten ikke kunnet svare på
    hvor lenge siden faktisk sto ute.

    MUTASJONEN SOM DREPER DENNE: sett unikheten tilbake.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("gjenoppretting")
        _krav(rt, t)
        kid = _kilde(rt, t)
        _side(rt, t, "forsiden", "Raskest", kid)
        _, _, _, rad2 = _side(rt, t, "forsiden", "Nest raskest", kid,
                              basert=1)
        _sett_kontekst(rt, t)
        utfall = rt.execute("SELECT * FROM m20_rull_tilbake(%s,%s,%s,%s)",
                            (t, rad2[0], "u-kari", "u-test")).fetchone()
        rt.commit()
        assert utfall[:2] == ("forrige_gjenopprettet", 1)
        _sett_kontekst(rt, t)
        rader = rt.execute(
            "SELECT versjon, levende, publisert_av"
            "  FROM m20_publiseringene(%s,%s) ORDER BY publisert_ts",
            (t, 20)).fetchall()
        rt.rollback()
    # Tre PERIODER: v1 (u-per), v2 (u-per), v1 igjen — og den siste
    # bærer navnet på den som RULLET TILBAKE, ikke på den som
    # publiserte i utgangspunktet.
    assert [(r[0], r[1]) for r in rader] == [(1, False), (2, False),
                                             (1, True)]
    assert rader[2][2] == "u-kari"


@pg
def test_gjenoppretting_nektes_naar_den_gamle_sidens_kilde_er_utloept():
    """DEN TREDJE UTGANGEN, OG DEN ER RIKTIG.

    Å gjenopprette forrige versjon ER å publisere den. Et datablad som
    utløp i mellomtiden gjør den gamle siden like udokumentert som en
    ny ville vært.

    Å NEKTE TILBAKERULLINGEN ville låst huset til den nye siden det
    nettopp ville bort fra. Å gjenopprette ville publisert en
    udokumentert påstand. Tomrommet er det eneste av de tre som ikke
    påstår noe — så siden blir stående avpublisert, og døra SIER det.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("gammelkilde")
        _krav(rt, t)
        k1 = _kilde(rt, t, tittel="Rapport 2024")
        k2 = _kilde(rt, t, tittel="Rapport 2025")
        _side(rt, t, "forsiden", "Raskest", k1)
        _, _, _, rad2 = _side(rt, t, "forsiden", "Nest raskest", k2,
                              basert=1)
        _eldes_kilde(mg, t, k1)
        _sett_kontekst(rt, t)
        utfall = rt.execute("SELECT * FROM m20_rull_tilbake(%s,%s,%s,%s)",
                            (t, rad2[0], "u-kari", "u-test")).fetchone()
        rt.commit()
        assert utfall[0] == "forrige_ikke_gjenopprettet"
        assert utfall[1] == 1
        assert "utloept" in utfall[2]
        _sett_kontekst(mg, t)
        levende = mg.execute(
            "SELECT count(*) FROM innholdspublisering WHERE tenant=%s"
            "   AND tilbake_ts IS NULL", (t,)).fetchone()[0]
        mg.rollback()
    assert levende == 0, "en udokumentert side ble gjenopprettet"


@pg
def test_en_rollback_kan_ikke_gjoeres_to_ganger():
    """EN GANG ER EN GANG, og raden bærer begge tidspunktene."""
    with _to() as (rt, mg):
        t = _tenantnavn("engang")
        _krav(rt, t)
        kid = _kilde(rt, t)
        _, _, _, rad = _side(rt, t, "forsiden", "Raskest", kid)
        _sett_kontekst(rt, t)
        rt.execute("SELECT * FROM m20_rull_tilbake(%s,%s,%s,%s)",
                   (t, rad[0], "u-kari", "u-test"))
        rt.commit()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute("SELECT * FROM m20_rull_tilbake(%s,%s,%s,%s)",
                       (t, rad[0], "u-kari", "u-test"))
        assert "allerede rullet tilbake" in str(e.value)
        rt.rollback()
        del mg


# =====================================================================
# `utkast_overskrevet` OG `tenantlekkasje_i_innholdsregister`.
# =====================================================================

@pg
def test_utkast_overskrevet_er_umulig_en_rettelse_er_en_ny_versjon():
    """`utkast_overskrevet`.

    M-1s `policyutkast` er «eneste muterbare tilstand» med en
    optimistisk lås. Den formen ER RIKTIG FOR EN POLICY, som ingen
    leser før den er aktivert — men en lås hindrer bare at to skriver
    samtidig. DEN BEVARER INGENTING, og «hva sto her da mennesket sa
    ja» er hele spørsmålet denne modulen finnes for.

    Derfor arves M-1s KOLONNER og M-46s DISIPLIN: hver versjon er en ny
    rad.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("frossent")
        _krav(rt, t)
        kid = _kilde(rt, t)
        uid, v1 = _utkast(rt, t, "forsiden", "Raskest", kid)
        assert v1 == 1
        for felt, verdi in (("innhold", "'{\"h1\":\"noe annet\"}'::jsonb"),
                            ("innholds_hash", "'" + "e" * 64 + "'"),
                            ("versjon", "7"),
                            ("side_id", "'annen'"),
                            ("opprettet_av", "'u-tyv'")):
            _nektes(mg, t,
                    f"UPDATE innholdsutkast SET {felt}={verdi}"
                    " WHERE tenant=%s AND utkast_id=%s", (t, uid),
                    teller_sql="SELECT count(*) FROM innholdsutkast"
                               " WHERE tenant=%s AND utkast_id=%s",
                    teller_args=(t, uid))
        _nektes(mg, t,
                "DELETE FROM innholdsutkast WHERE tenant=%s AND utkast_id=%s",
                (t, uid),
                teller_sql="SELECT count(*) FROM innholdsutkast"
                           " WHERE tenant=%s AND utkast_id=%s",
                teller_args=(t, uid))
        # …OG EN RETTELSE ER EN NY RAD, med sin egen sum.
        uid2, v2 = _utkast(rt, t, "forsiden", "Nest raskest", kid, basert=1)
        assert v2 == 2 and uid2 != uid
        _sett_kontekst(mg, t)
        summer = mg.execute(
            "SELECT count(DISTINCT innholds_hash) FROM innholdsutkast"
            " WHERE tenant=%s AND side_id='forsiden'", (t,)).fetchone()[0]
        mg.rollback()
        assert summer == 2, "to versjoner deler sum — da maaler den ingenting"


@pg
def test_status_gaar_en_vei_og_publisert_er_terminal():
    """EN PUBLISERT SIDE SOM «GÅR TILBAKE TIL UTKAST» ville vært en side
    som aldri hadde vært publisert. Den finnes ikke: noen leste den."""
    with _to() as (rt, mg):
        t = _tenantnavn("enveis")
        _krav(rt, t)
        kid = _kilde(rt, t)
        uid, _, _, _ = _side(rt, t, "forsiden", "Raskest", kid)
        for status in ("utkast", "klar", "forkastet"):
            _nektes(mg, t,
                    f"UPDATE innholdsutkast SET status='{status}'"
                    " WHERE tenant=%s AND utkast_id=%s", (t, uid),
                    teller_sql="SELECT count(*) FROM innholdsutkast"
                               " WHERE tenant=%s AND utkast_id=%s",
                    teller_args=(t, uid))


@pg
def test_paastanden_og_visningen_er_helt_frosne():
    """En påstand som kunne endres etter at kilden ble registrert, ville
    vært en påstand som byttet kilde uten å si fra — og da måler
    fremmednøkkelen ingenting."""
    with _to() as (rt, mg):
        t = _tenantnavn("frosset2")
        _krav(rt, t)
        kid = _kilde(rt, t)
        uid, _ = _utkast(rt, t, "forsiden", "Raskest", kid)
        vid = _visning(rt, t, uid)
        _sett_kontekst(mg, t)
        pid = mg.execute("SELECT paastand_id FROM innholdspaastand"
                         " WHERE tenant=%s AND utkast_id=%s",
                         (t, uid)).fetchone()[0]
        mg.rollback()
        _nektes(mg, t,
                "UPDATE innholdspaastand SET kilde_id=%s"
                " WHERE tenant=%s AND paastand_id=%s",
                (kid, t, pid),
                teller_sql="SELECT count(*) FROM innholdspaastand"
                           " WHERE tenant=%s AND paastand_id=%s",
                teller_args=(t, pid))
        _nektes(mg, t,
                "UPDATE innholdsvisning SET vist_for='u-tyv'"
                " WHERE tenant=%s AND visning_id=%s", (t, vid),
                teller_sql="SELECT count(*) FROM innholdsvisning"
                           " WHERE tenant=%s AND visning_id=%s",
                teller_args=(t, vid))


@pg
def test_tenantlekkasje_i_innholdsregister_er_umulig():
    """FORCE RLS PÅ ALLE SEKS, målt fra to kanter: at policyen finnes
    OG at en annen tenants kontekst ikke ser raden."""
    with _to() as (rt, mg):
        t1 = _tenantnavn("egen")
        t2 = _tenantnavn("annen")
        for t in (t1, t2):
            _krav(rt, t)
            _kilde(rt, t)
        kid = _kilde(rt, t1, tittel="Hemmelig rapport")
        _utkast(rt, t1, "forsiden", "Raskest", kid)
        _sett_kontekst(rt, t2)
        sett = rt.execute("SELECT count(*) FROM m20_sideregister(%s,%s)",
                          (t1, 50)).fetchone()[0]
        rt.rollback()
        assert sett == 0, "en annen tenants sider var synlige"
        # …OG DØRA NEKTER Å BLI KALT MED FEIL TENANT I DET HELE TATT.
        #
        # NEKTET ER HUSETS, IKKE MODULENS: dørene kaller
        # `krev_tenantkontekst` (038) i stedet for å skrive sin egen
        # sammenligning, og den reiser `InsufficientPrivilege` — en
        # RETTIGHETSFEIL, ikke en inndatafeil. Skillet er riktig: å be
        # om en annen tenants data er ikke et feilformet kall.
        _sett_kontekst(rt, t2)
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as e:
            rt.execute("SELECT m20_sett_krav(%s,%s,%s,%s,%s)",
                       (t1, 10, 10, 1, "u-tyv"))
        assert "kallerens tenantkontekst" in str(e.value)
        rt.rollback()
        with _mig() as mg2:
            mangler = [r[0] for r in mg2.execute(
                "SELECT c.relname FROM pg_class c"
                " WHERE c.relname = ANY(%s)"
                "   AND NOT (c.relrowsecurity AND c.relforcerowsecurity)",
                (list(EGNE),)).fetchall()]
        assert mangler == [], f"uten FORCE ser eieren forbi sin egen policy: {mangler}"
        del mg


@pg
def test_runtime_har_ingen_tabellrettigheter():
    """SP-7: kjøretiden når dørene og ingenting annet."""
    with _mig() as mg:
        rader = mg.execute(
            "SELECT table_name, privilege_type FROM"
            " information_schema.table_privileges"
            " WHERE grantee='disponit' AND table_name = ANY(%s)",
            (list(EGNE) + ["kildedokument"],)).fetchall()
    assert rader == [], f"runtime har tabellrettigheter: {rader}"


# =====================================================================
# SVEIPEN.
# =====================================================================

@pg
def test_sveipen_reiser_funn_paa_en_levende_side_med_utloept_kilde():
    """SVEIPENS VIKTIGSTE FUNN, og det er et om en SKADE.

    Døra nektet da påstanden ble skrevet, og igjen ved `klar`, og igjen
    ved publisering. Så gikk det tid. Kilden utløp mens siden sto ute,
    og da er den udokumenterte påstanden allerede lest.

    DET ER DERFOR DETTE FUNNET FINNES OG IKKE ER MODULENS FØRSTE
    FORSVAR: sveipen rydder etter tiden, ikke etter dårlige valg.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("skade")
        _krav(rt, t)
        kid = _kilde(rt, t)
        _, _, _, rad = _side(rt, t, "forsiden", "Raskest", kid)
        with _sv() as sv:
            sv.execute("SELECT * FROM m20_sveip_innhold(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        for_ = mg.execute("SELECT count(*) FROM innholdsfunn WHERE tenant=%s"
                          "   AND apen", (t,)).fetchone()[0]
        mg.rollback()
        assert for_ == 0, "sveipen fant noe paa en frisk tenant"

        _eldes_kilde(mg, t, kid)
        with _sv() as sv:
            sv.execute("SELECT * FROM m20_sveip_innhold(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        funn = mg.execute(
            "SELECT funntype, referanse, over_grense FROM innholdsfunn"
            " WHERE tenant=%s AND apen", (t,)).fetchall()
        mg.rollback()
        assert funn == [("publisert_paastand_uten_gyldig_kilde",
                         rad[0], 1)], funn

        # KILDEN FORNYES → sveipen lukker sitt eget funn.
        _sett_kontekst(mg, t)
        mg.execute("UPDATE kildedokument SET gyldig_til = current_date + 400"
                   " WHERE tenant=%s AND kilde_id=%s", (t, kid))
        mg.commit()
        with _sv() as sv:
            sv.execute("SELECT * FROM m20_sveip_innhold(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        etter = mg.execute(
            "SELECT apen, lukket_av FROM innholdsfunn WHERE tenant=%s"
            "   AND funntype='publisert_paastand_uten_gyldig_kilde'",
            (t,)).fetchone()
        mg.rollback()
    assert etter == (False, "m20_sveip")


@pg
def test_et_menneske_kan_ikke_lukke_sveipens_funn():
    """`publisert_paastand_uten_gyldig_kilde` lukkes av at TILSTANDEN
    opphører — kilden fornyes eller siden avpubliseres — ikke av at
    noen huker av. En udokumentert påstand som står ute slutter ikke å
    stå ute fordi noen leste varselet."""
    with _to() as (rt, mg):
        t = _tenantnavn("nekt")
        _krav(rt, t)
        kid = _kilde(rt, t)
        _side(rt, t, "forsiden", "Raskest", kid)
        _eldes_kilde(mg, t, kid)
        with _sv() as sv:
            sv.execute("SELECT * FROM m20_sveip_innhold(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        fid = mg.execute("SELECT funn_id FROM innholdsfunn WHERE tenant=%s"
                         "   AND funntype='publisert_paastand_uten_gyldig_kilde'",
                         (t,)).fetchone()[0]
        mg.rollback()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute("SELECT m20_lukk_funn(%s,%s,%s,%s)",
                       (t, fid, "vi tar det senere", "u-test"))
        assert "lukkes av at tilstanden opphoerer" in str(e.value)
        rt.rollback()


@pg
def test_et_klart_utkast_ingen_har_sett_reises_og_lukkes_av_visningen():
    """FØRSTE UTKAST LETTE ETTER «utkast endret etter visning», OG DEN
    TILSTANDEN ER UREPRESENTERBAR: visningen kopierer utkastets sum, og
    utkastet er frosset. Et funn som aldri kan reises OG ikke er ment
    som et bevis, er dødt — det ser ut som en vakt og er det ikke.

    Det som FAKTISK kan skje er at noen merker siden klar uten at et
    eneste menneske har sett den.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("usett")
        _krav(rt, t)
        kid = _kilde(rt, t)
        uid, _ = _utkast(rt, t, "forsiden", "Raskest", kid)
        _klar(rt, t, uid)
        with _sv() as sv:
            sv.execute("SELECT * FROM m20_sveip_innhold(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        apne = mg.execute(
            "SELECT funntype FROM innholdsfunn WHERE tenant=%s AND apen",
            (t,)).fetchall()
        mg.rollback()
        assert apne == [("klart_utkast_uten_forhaandsvisning",)], apne
        _visning(rt, t, uid)
        with _sv() as sv:
            sv.execute("SELECT * FROM m20_sveip_innhold(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        etter = mg.execute(
            "SELECT apen, lukket_av FROM innholdsfunn WHERE tenant=%s"
            "   AND funntype='klart_utkast_uten_forhaandsvisning'",
            (t,)).fetchone()
        mg.rollback()
    assert etter == (False, "m20_sveip")


@pg
def test_kilde_som_snart_utloeper_kan_avklares_av_et_menneske_og_forblir_lukket():
    """«VI HAR SJEKKET, DOKUMENTET STÅR SEG» er en legitim avklaring med
    et navn på — og 131s lærdom gjelder: sveipen skal ikke gjenåpne den
    natten etter.

    MUTASJONEN SOM DREPER DENNE: fjern `NOT EXISTS`-leddet på lukkede
    funn i sveipens tredje blokk.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("snart")
        _krav(rt, t, varsel=30)
        kid = _kilde(rt, t, gyldig_til=I_DAG + datetime.timedelta(days=10))
        _side(rt, t, "forsiden", "Raskest", kid)
        with _sv() as sv:
            sv.execute("SELECT * FROM m20_sveip_innhold(500)")
            sv.commit()
        _sett_kontekst(rt, t)
        funn = [f for f in rt.execute("SELECT * FROM m20_innholdsfunn(%s,%s)",
                                      (t, 50)).fetchall()
                if f[1] == "kilde_utloper_snart_uavklart"]
        rt.rollback()
        assert funn, "kilden utloeper om ti doegn og baerer en levende paastand"
        assert funn[0][9] is True, "et menneske skal kunne avklare denne"
        _sett_kontekst(rt, t)
        rt.execute("SELECT m20_lukk_funn(%s,%s,%s,%s)",
                   (t, funn[0][0], "vi har sjekket, rapporten staar seg",
                    "u-kari"))
        rt.commit()
        # NATTEN ETTER.
        with _sv() as sv:
            sv.execute("SELECT * FROM m20_sveip_innhold(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        apne = mg.execute(
            "SELECT count(*) FROM innholdsfunn WHERE tenant=%s"
            "   AND funntype='kilde_utloper_snart_uavklart' AND apen",
            (t,)).fetchone()[0]
        mg.rollback()
    assert apne == 0, "sveipen gjenaapnet en avklaring"


@pg
def test_sveipen_ser_ingenting_uten_kryss_tenant_policyen():
    """130s LÆRDOM: en sveip uten `disponit.tenant` ville sett NULL
    RADER under FORCE RLS og rapportert null funn — MED GRØNN
    EXIT-KODE."""
    with _mig() as mg:
        rad = mg.execute(
            "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy"
            " WHERE polrelid='innholdskrav'::regclass"
            "   AND polname='m20_sveip_tenantliste'").fetchone()
    assert rad, "kryss-tenant-policyen mangler — sveipen ville vaert blind"
    assert "IS NULL" in rad[0], f"policyen er ikke snever nok: {rad[0]}"


@pg
def test_sveipen_teller_tenanter_og_gir_fire_felt():
    """Kontrakten driftsfila leser."""
    with _to() as (rt, mg):
        t = _tenantnavn("kontrakt")
        _krav(rt, t)
        with _sv() as sv:
            rader = sv.execute(
                "SELECT * FROM m20_sveip_innhold(500)").fetchall()
            sv.commit()
        del mg, t
    assert len(rader) == 1 and len(rader[0]) == 4
    assert rader[0][0] >= 1


# =====================================================================
# ARVEN. HUSET SKAL HA ETT KILDEREGISTER, IKKE TO.
# =====================================================================

@pg
def test_modulen_arver_husets_kilderegister_og_lager_ikke_et_nummer_to():
    """FUNDAMENTET SKREV AT KILDEKRAVET VAR NYTT FOR M-20. DET VAR DET
    IKKE.

    `kildedokument` (M-46, migrasjon 118) finnes, med samme doktrine og
    nesten samme ord: «Et utkastpunkt kan bare peke hit. Det er hele
    mekanismen bak utkast markerer hvert faktapunkt med kilde.»

    To kilderegistre ville gitt to svar på «kan vi belegge dette», og
    det er ett for mye — nøyaktig argumentet fundamentet selv brukte
    for at M-7 og M-43 skal dele ÉN opptakshjemmel.

    PORTEN FALLER HVIS NOEN LAGER NUMMER TO.
    """
    with _mig() as mg:
        egne = [r[0] for r in mg.execute(
            "SELECT c.relname FROM pg_class c"
            " WHERE c.relnamespace='public'::regnamespace AND c.relkind='r'"
            "   AND c.relname LIKE 'innholds%'").fetchall()]
        fk = mg.execute(
            "SELECT confrelid::regclass::text FROM pg_constraint"
            " WHERE conname='innholdspaastand_kilde_fk'").fetchone()
    assert sorted(egne) == sorted(EGNE), egne
    assert fk == ("kildedokument",), (
        "paastanden peker ikke paa husets kilderegister")


@pg
def test_de_to_doerene_skriver_i_det_samme_registeret():
    """SAMME SUM ER SAMME DOKUMENT, uansett hvilken dør som skrev det.

    Det er hele poenget med å dele registeret: en testrapport
    registrert av anbudsmodulen er den samme testrapporten
    innholdsmodulen siterer, og `kildedokument_sum_unik` gjør at det
    ikke kan bli to.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("delt")
        _krav(rt, t)
        _sett_kontekst(rt, t)
        sum_ = secrets.token_hex(32)
        a = rt.execute("SELECT m20_registrer_kilde(%s,%s,%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), "Rapport", "testrapport", None,
                        sum_, "u-test")).fetchone()[0]
        rt.commit()
        _sett_kontekst(rt, t)
        b = rt.execute("SELECT m20_registrer_kilde(%s,%s,%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), "Rapport (kopi)", "annet", None,
                        sum_, "u-test")).fetchone()[0]
        rt.commit()
        assert a == b, "samme dokument ble registrert to ganger"
        _sett_kontekst(mg, t)
        antall = mg.execute("SELECT count(*) FROM kildedokument"
                            " WHERE tenant=%s", (t,)).fetchone()[0]
        mg.rollback()
    assert antall == 1


@pg
def test_dokumenttypesettet_rommer_begge_modulenes_vokabular():
    """PRISEN FOR Å DELE, OG DEN ER ÆRLIG.

    Et lukket sett som tjener to moduler må romme begges vokabular,
    ellers tvinger det den ene til å skrive «annet» — OG «ANNET» ER
    INGEN KILDE. M-46s syv står urørt; M-20s fire er former en
    produktpåstand faktisk hviler på.
    """
    with _mig() as mg:
        uttrykk = mg.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname='kildedokument_type_lukket'").fetchone()[0]
    for m46 in ("sertifikat", "attest", "regnskap", "referanse",
                "policy", "cv", "annet"):
        assert f"'{m46}'" in uttrykk, f"M-46 mistet {m46}"
    for m20 in ("testrapport", "maaling", "datablad",
                "leverandorerklaering"):
        assert f"'{m20}'" in uttrykk, f"M-20 mangler {m20}"


@pg
def test_funntabellen_staar_i_m36s_katalog_med_lesretten():
    """133s LÆRDOM, GJENTATT UTEN Å BLI STOPPET.

    `innholdsfunn` er et nytt funnregister, og M-36 nekter å rangere
    med ett ukjent. RADEN ALENE ER BARE EN LOVNAD: `m36_apne_funn`
    løper som optimalisatoreieren og LESER tabellen.

    Kompletthet er M-36s invariant og måles der. Her står bare det
    denne modulen selv svarer for.
    """
    with _mig() as mg:
        rad = mg.execute(
            "SELECT modul, typekolonne, apenform FROM m36_funnregister"
            " WHERE relasjon='innholdsfunn'").fetchone()
        les = mg.execute(
            "SELECT count(*) FROM information_schema.table_privileges"
            " WHERE table_name='innholdsfunn' AND privilege_type='SELECT'"
            "   AND grantee='disponit_optimalisator_eier'").fetchone()[0]
        kolonne = mg.execute(
            "SELECT count(*) FROM information_schema.columns"
            " WHERE table_name='innholdsfunn' AND column_name='apen'"
            "   AND data_type='boolean'").fetchone()[0]
    assert rad == ("m20_innhold", "funntype", "apen_kolonne"), rad
    assert les == 1, "registrert uten lesrett — det ser komplett ut"
    assert kolonne == 1, "registeret lover en apen-kolonne som ikke finnes"


@pg
def test_grensen_mot_m1_staar_i_koden():
    """M-1 EIER POLICYER — regler huset håndhever mot seg selv. M-20
    eier INNHOLD — det huset sier til andre.

    En modul som utvidet `policyutkast` til å bære produktpåstander
    ville gjort policyforvaltning til markedsføring i stillhet.
    """
    kode = _bare_kode(MIGRASJON, uten_strenger=True)
    for m1 in ("policyutkast", "policy_hode", "aktiveringsrunde"):
        assert m1 not in kode, f"134 roerer M-1s {m1}"
    tekst = MIGRASJON.read_text(encoding="utf-8")
    assert "GRENSEN MOT M-1" in tekst, "grensen staar ikke i filhodet"


def test_sveipens_arbeidernokkel_er_modulens_egen():
    """To sveip som delte nøkkel ville blokkert hverandre i stillhet."""
    nokler = {}
    for fil in sorted((ROT / "platform" / "drift").glob("*sveip.py")):
        m = re.search(r"ARBEIDERNOKKEL = ([\d_]+)",
                      fil.read_text(encoding="utf-8"))
        if m:
            nokler.setdefault(m.group(1), []).append(fil.name)
    delte = {k: v for k, v in nokler.items() if len(v) > 1}
    assert delte == {}, f"delte arbeidernoekler: {delte}"


def test_driftsfila_navngir_sin_egen_jobb():
    """Arvefeilen fra 116-118, og fra kjørerne i 130/132/133."""
    sti = ROT / "deploy" / "staging" / "disponit-innholdssveip.service"
    tj = sti.read_text(encoding="utf-8")
    beskrivelse = tj.split("Description=")[1][:110]
    assert "innhold" in beskrivelse.lower()
    for arvet in ("likviditet", "bemanning", "rangering", "EHF", "HMS",
                  "møte", "kontantbane"):
        assert arvet not in beskrivelse, f"arvet ord: {arvet}"
    assert ("LoadCredential=DISPONIT_INNHOLDSSVEIP_URL:"
            "/etc/disponit/innholdssveip/DISPONIT_INNHOLDSSVEIP_URL" in tj)
    kjorer = (ROT / "platform" / "drift"
              / "kjor_innholdssveip.py").read_text(encoding="utf-8")
    assert "m20_sveip_innhold()" in kjorer
    for arvet in ("m33_sveip_prognose", "m36_sveip_optimalisering",
                  "m7_sveip_moter", "m50_sveip_postjournal"):
        assert arvet not in kjorer, f"arvet referanse: {arvet}"


def test_sveipen_rekker_aa_bli_ferdig_for_statussveipen_starter():
    """ET KLOKKESLETT ER IKKE EN REKKEFØLGE (132s lærdom)."""
    katalog = ROT / "deploy" / "staging"

    def _tid(fil):
        tekst = (katalog / fil).read_text(encoding="utf-8")
        kl = re.search(r"OnCalendar=\*-\*-\* (\d\d):(\d\d):00 UTC", tekst)
        sp = re.search(r"RandomizedDelaySec=(\d+)min", tekst)
        assert kl, f"{fil} har intet klokkeslett"
        return (int(kl.group(1)) * 60 + int(kl.group(2)),
                int(sp.group(1)) if sp else 0)

    status_start, _ = _tid("disponit-sveipestatus.timer")
    for fil in sorted(katalog.glob("disponit-*sveip.timer")):
        tekst = fil.read_text(encoding="utf-8")
        if "OnCalendar" not in tekst:
            continue
        start, spredning = _tid(fil.name)
        tj = katalog / fil.name.replace(".timer", ".service")
        m = (re.search(r"TimeoutStartSec=(\d+)min",
                       tj.read_text(encoding="utf-8"))
             if tj.exists() else None)
        slutt = start + spredning + (int(m.group(1)) if m else 0)
        assert slutt <= status_start, (
            f"{fil.name} kan holde paa til {slutt // 60}:{slutt % 60:02d}"
            f" mens statussveipen kan starte"
            f" {status_start // 60}:{status_start % 60:02d}")
    assert _tid("disponit-innholdssveip.timer") == (6 * 60 + 40, 4)


def test_sveipens_dsn_star_i_ci():
    """127s LÆRDOM. Navnet hentes fra KJØREREN, ikke fra filnavnet."""
    kjorer = ROT / "platform" / "drift" / "kjor_innholdssveip.py"
    url = re.findall(r"DISPONIT_[A-Z0-9_]+_URL",
                     kjorer.read_text(encoding="utf-8"))
    assert url, "kjoereren leser ingen DSN"
    ventet = url[0].replace("DISPONIT_", "DISPONIT_TEST_", 1)
    ventet = ventet[:-len("_URL")] + "_DSN"
    ci = (ROT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert f"{ventet}:" in ci, f"{ventet} mangler i ci.yml"
    opp = (ROT / "deploy" / "staging" / "opp.sh").read_text(encoding="utf-8")
    assert url[0] in opp, f"{url[0]} mangler i opp.sh"


def test_fundamentet_navngir_modulen_og_migrasjonen():
    tekst = FUNDAMENT.read_text(encoding="utf-8")
    assert "134" in tekst and "M-20" in tekst
    assert MIGRASJON.exists()


# =====================================================================
# EVIDENSSPORET.
# =====================================================================

@pg
def test_alle_tre_tilbakerullingsutfall_skriver_evidens():
    """ALLE TRE MUTERER PUBLISERINGSRADEN.

    Første utkast skrev evidens bare på `forrige_gjenopprettet`.
    CodeRabbit fant det 5/9, og funnet var riktig: et utfall uten en
    linje i sporet er en side som ble tatt ned uten at huset vet hvem
    som gjorde det.

    MUTASJONEN SOM DREPER DENNE: fjern ett av de tre
    `m20_evidens`-kallene i `m20_rull_tilbake`.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("spor")
        _krav(rt, t)
        k1 = _kilde(rt, t, tittel="Rapport A")
        k2 = _kilde(rt, t, tittel="Rapport B")

        def spor():
            _sett_kontekst(mg, t)
            n = mg.execute(
                "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
                "   AND kilde='m20_innhold' AND handling='rull_tilbake'",
                (t,)).fetchone()[0]
            mg.rollback()
            return n

        # UTFALL 1: avpublisering (første versjon av en side).
        _, _, _, a = _side(rt, t, "en", "Raskest", k1)
        _sett_kontekst(rt, t)
        assert rt.execute("SELECT * FROM m20_rull_tilbake(%s,%s,%s,%s)",
                          (t, a[0], "u-kari", "u-test")
                          ).fetchone()[0] == "avpublisert"
        rt.commit()
        assert spor() == 1, "avpublisering skrev ingen evidens"

        # UTFALL 2: forrige gjenopprettet.
        _side(rt, t, "to", "Raskest", k1)
        _, _, _, b = _side(rt, t, "to", "Nest raskest", k1, basert=1)
        _sett_kontekst(rt, t)
        assert rt.execute("SELECT * FROM m20_rull_tilbake(%s,%s,%s,%s)",
                          (t, b[0], "u-kari", "u-test")
                          ).fetchone()[0] == "forrige_gjenopprettet"
        rt.commit()
        assert spor() == 2, "gjenoppretting skrev ingen evidens"

        # UTFALL 3: forrige kunne IKKE gjenopprettes.
        _side(rt, t, "tre", "Gammel", k2)
        _, _, _, c = _side(rt, t, "tre", "Ny", k1, basert=1)
        _eldes_kilde(mg, t, k2)
        _sett_kontekst(rt, t)
        assert rt.execute("SELECT * FROM m20_rull_tilbake(%s,%s,%s,%s)",
                          (t, c[0], "u-kari", "u-test")
                          ).fetchone()[0] == "forrige_ikke_gjenopprettet"
        rt.commit()
        assert spor() == 3, "det tredje utfallet skrev ingen evidens"


@pg
def test_hver_skrivedoer_legger_igjen_et_spor():
    """HUSETS FORM, MÅLT OVER HELE MODULEN.

    Porten spør KATALOGEN og ikke en liste: en dør lagt til uten
    evidens ville ikke stått i en liste jeg måtte huske å oppdatere.
    """
    skrivende = ("m20_sett_krav", "m20_registrer_kilde",
                 "m20_registrer_utkast", "m20_registrer_paastand",
                 "m20_registrer_visning", "m20_merk_klar",
                 "m20_publiser", "m20_rull_tilbake", "m20_lukk_funn")
    with _mig() as mg:
        uten = []
        for navn in skrivende:
            kropp = mg.execute(
                "SELECT pg_get_functiondef(p.oid) FROM pg_proc p"
                " WHERE p.proname=%s", (navn,)).fetchone()[0]
            if "m20_evidens" not in kropp:
                uten.append(navn)
    assert uten == [], f"skrivedoerer uten evidensspor: {uten}"


@pg
def test_visningene_leses_fra_utkastet_og_baerer_sin_egen_id():
    """DØRA SOM MANGLET.

    Publiseringsveien trenger `visning_id`, og publiseringsraden bærer
    den ikke. Uten `m20_visningene` måtte flaten lete i
    publiseringslisten — og en side som publiseres FØR FØRSTE GANG har
    ingen publiseringer i det hele tatt.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("visningene")
        _krav(rt, t)
        kid = _kilde(rt, t)
        uid, _ = _utkast(rt, t, "forsiden", "Raskest", kid)
        vid = _visning(rt, t, uid, for_="u-per")
        _sett_kontekst(rt, t)
        rader = rt.execute("SELECT * FROM m20_visningene(%s,%s)",
                           (t, uid)).fetchall()
        rt.rollback()
        assert len(rader) == 1
        assert rader[0][0] == vid
        assert rader[0][3] == "u-per"
        # …OG DEN SIER OM VISNINGEN GJELDER DETTE INNHOLDET.
        assert rader[0][4] is True
        # EN VISNING AV ET ANNET UTKAST HØRER IKKE HJEMME HER.
        uid2, _ = _utkast(rt, t, "forsiden", "Nest raskest", kid)
        _visning(rt, t, uid2)
        _sett_kontekst(rt, t)
        antall = rt.execute("SELECT count(*) FROM m20_visningene(%s,%s)",
                            (t, uid)).fetchone()[0]
        rt.rollback()
        assert antall == 1
        del mg
