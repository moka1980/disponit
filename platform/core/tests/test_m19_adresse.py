"""M-19 adressevalidering v1 (112) — REGISTERET, IKKE OPPSLAGET.

Grensen `m19-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

DEN SKARPESTE PORTEN ER `modulen_slo_opp_eksternt`, og den er
annerledes enn søskenmodulenes. For M-41 var faren at modulen skulle
GJØRE noe farlig. Her er faren at den skal SPØRRE noen: et oppslag mot
et adresseregister er en utgående kanal med personopplysninger i —
kundens navn og adresse ut av huset, til en tredjepart vi ikke har
databehandleravtale med. Og svaret ville uansett vært feil vare: at en
adresse FINNES i et register sier ikke at pakken kommer fram til den
som skal ha den.

DEN NEST SKARPESTE er `normalisering_uten_original`. Blander man de to
formene, kan ingen etterpå se om en feillevering skyldtes det kunden
skrev eller det vi gjorde med det.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import ast
import json
import os
import re
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo)
from .test_m37 import _sett_kontekst

ADRESSESVEIP_DSN = os.environ.get("DISPONIT_TEST_ADRESSESVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "112_m19_adresseregister.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "adresse.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "adresse.py",
    ROT / "platform" / "drift" / "adressesveip.py",
    ROT / "platform" / "drift" / "kjor_adressesveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("adressekrav", "adressesubjekt", "adresseversjon",
        "adressekontroll", "adressefunn")

_STRENG = re.compile(
    r"'''.*?'''" r'|""".*?"""'
    r"|`(?:[^`\\]|\\.)*`"
    r"|'(?:[^'\\]|\\.|'')*'"
    r'|"(?:[^"\\]|\\.)*"', re.S)


def _bare_kode(fil: Path, *, uten_strenger: bool = False) -> str:
    """Filens innhold uten kommentarer OG uten docstrings (m24-formen)."""
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
    ut = "\n".join(l for l in linjer
                   if not l.lstrip().startswith(merke))
    return _STRENG.sub("''", ut) if uten_strenger else ut


def _rt():
    from db.pg import koble
    return koble(DSN)


def _sv():
    from db.pg import koble
    return koble(ADRESSESVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m19-{merke}-{secrets.token_hex(4)}"


def _krav(c, tenant, *, ukontrollert=14, gyldig=365,
          metoder=("bekreftet_av_kunde", "dokumentert"), aktor="u-test"):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m19_sett_krav(%s,%s,%s,%s,%s)",
                  (tenant, ukontrollert, gyldig, list(metoder),
                   aktor)).fetchone()[0]
    c.commit()
    return v


def _subjekt(c, tenant, *, ref=None, navn="Kari Kunde", sid=None,
             aktor="u-test"):
    sid = sid or uuid.uuid4()
    ref = ref or ("ORD-" + secrets.token_hex(4))
    _sett_kontekst(c, tenant)
    c.execute("SELECT m19_registrer_subjekt(%s,%s,%s,%s,%s)",
              (tenant, sid, ref, navn, aktor))
    c.commit()
    return sid


def _adresse(c, tenant, sid, *, linje1="Storgata 5", linje2=None,
             postnr="0155", poststed="Oslo", land="NO",
             kilde="oppgitt_av_kunde", kilde_ref=None, fra="2026-01-10",
             notat="oppgitt", vid=None, aktor="u-test"):
    vid = vid or uuid.uuid4()
    kilde_ref = kilde_ref or ("k_" + secrets.token_hex(4))
    _sett_kontekst(c, tenant)
    endret = c.execute(
        "SELECT m19_registrer_adresse("
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::date,%s,%s)",
        (tenant, vid, sid, linje1, linje2, postnr, poststed, land,
         kilde, kilde_ref, fra, notat, aktor)).fetchone()[0]
    c.commit()
    return vid, endret


def _kontroll(c, tenant, vid, *, metode="bekreftet_av_kunde",
              utfall="godkjent", kontrollor="Per", kilde_ref=None,
              begrunnelse=None, dato="2026-01-12", aktor="u-test"):
    kid = uuid.uuid4()
    kilde_ref = kilde_ref or ("kk_" + secrets.token_hex(4))
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m19_registrer_kontroll(%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s::date,%s)",
        (tenant, kid, vid, metode, utfall, kontrollor, kilde_ref,
         begrunnelse, dato, aktor))
    c.commit()
    return kid


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m19_sveip_adresser(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT funntype, over_grense, siste_metode, siste_utfall, apen"
        "  FROM adressefunn WHERE tenant=%s ORDER BY funntype",
        (tenant,)).fetchall()
    m.rollback()
    return rader


def _tell_utenfor(m):
    tabeller = [r[0] for r in m.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
        " ORDER BY tablename").fetchall()]
    m.rollback()
    ut = {}
    for tab in tabeller:
        if tab in EGNE:
            continue
        try:
            ut[tab] = m.execute(
                f'SELECT count(*) FROM public."{tab}"').fetchone()[0]
        except psycopg.errors.InsufficientPrivilege:
            m.rollback()
    m.rollback()
    assert len(ut) > 20, \
        f"porten teller bare {len(ut)} tabeller — den måler ingenting"
    return ut


# ---------------------------------------------------------------------------
# INVARIANT 1: modulen_slo_opp_eksternt
# ---------------------------------------------------------------------------

def test_invariant_modulen_slo_opp_eksternt():
    """MODULENS SKARPESTE DOM, målt på IMPORTENE, KODEN og RUTENE.

    Et oppslag mot et adresseregister er en utgående kanal med
    personopplysninger i. Modulen har ingen HTTP-klient, ingen socket,
    og ingen dør som ligner et oppslag.

    MUTASJONEN SOM DREPER DENNE: `import httpx` i `adresse.py`.
    """
    for fil in MODULFILER:
        for node in ast.walk(ast.parse(fil.read_text(encoding="utf-8"))):
            navn = []
            if isinstance(node, ast.Import):
                navn = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                navn = [node.module or ""] if node.level == 0 else []
            for n in navn:
                assert n.split(".")[0] not in {
                    "httpx", "requests", "aiohttp", "urllib", "http",
                    "socket", "smtplib", "ftplib", "telnetlib",
                    "xmlrpc", "ssl"}, \
                    f"{fil.name} importerer {n} — v1 har ingen utgående" \
                    " kanal mot et adresseregister"
    for fil in (MIGRASJON, FLATE, *MODULFILER):
        uten = _bare_kode(fil, uten_strenger=True).lower()
        for ord_ in (r"\boppslag", r"\bslaa_opp", r"\bslå_opp",
                     "urlopen", "aiohttp", r"requests\.", "postnummer",
                     "kartverket", "bring", "posten_api", "geocod",
                     r"http://", r"https://"):
            assert not re.search(ord_, uten), \
                f"{fil.name} bærer «{ord_}» — v1 slår ingenting opp"

    # …OG DET FINNES INGEN DØR SOM GJØR DET.
    sql = MIGRASJON.read_text(encoding="utf-8")
    doerer = re.findall(r"CREATE FUNCTION (m19_\w+)", sql)
    assert len(doerer) >= 12, doerer
    for navn in doerer:
        for ord_ in ("oppslag", "slaa_opp", "hent_ekstern", "verifiser",
                     "attester", "signer", "valider"):
            assert ord_ not in navn, navn

    from api.app import RUTESCOPE
    mine = sorted(sti for _m, sti in RUTESCOPE
                  if sti.startswith("/v1/adresse"))
    assert mine == [
        "/v1/adresse",
        "/v1/adresse/krav",
        "/v1/adresse/subjekt",
        "/v1/adresse/versjon/{versjon_id:uuid}/kontroll",
        "/v1/adresse/versjon/{versjon_id:uuid}/kontroller",
        "/v1/adresse/{subjekt_id:uuid}/aktiv",
        "/v1/adresse/{subjekt_id:uuid}/historikk",
        "/v1/adresse/{subjekt_id:uuid}/versjon",
    ], mine


def test_ingen_kontrollmetode_er_et_oppslag():
    """METODESETTET ER V1-DOMMEN, SKREVET UT.

    Skulle noen en dag legge til `oppslag`, må de gjøre det i
    migrasjonen, i API-ets sett, i flatens sett OG i grensen `m19-v1` —
    fire steder, alle røde til noen bestemmer seg.

    MUTASJONEN SOM DREPER DENNE: legg `oppslag` i CHECK-en i 112.
    """
    from api.adresse import METODER, UTFALL
    assert set(METODER) == {"visuell", "bekreftet_av_kunde",
                            "dokumentert", "levering_bekreftet"}
    assert set(UTFALL) == {"godkjent", "avvist", "ukontrollerbar"}
    sql = MIGRASJON.read_text(encoding="utf-8")
    i = sql.index("adressekontroll_metode_lukket")
    lukket = sql[i:sql.index("))", i)]
    for m in METODER:
        assert f"'{m}'" in lukket, m
    assert len(re.findall(r"'[a-z_]+'", lukket)) == len(METODER)


def test_flaten_slaar_ingenting_opp():
    uten = _bare_kode(FLATE, uten_strenger=True)
    for ord_ in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon",
                 "EventSource"):
        assert ord_ not in uten, f"flaten bærer «{ord_}»"
    api = (ROT / "platform" / "core" / "ui" / "static" / "js"
           / "api.js").read_text(encoding="utf-8")
    # PRESISERT DA M-48 (116) KOM, OG STRAMMET SAMTIDIG.
    #
    # Sjekken var `export const (slaaOpp|validerAdresse)` — en
    # PREFIKSMATCH i en DELT fil. Den forbød dermed enhver modul å ha
    # en oppslagshjelper, en rekkevidde M-19s invariant aldri krevde:
    # dommen her er at ADRESSEmodulen ikke slår opp, ikke at
    # plattformen aldri skal spørre noen om noe. M-48 har siden fått
    # ett oppslag mot foretaksregisteret (eierbeslutning 3/9), og
    # `slaaOppMotpart` traff prefikset.
    #
    # Navnesjekken er nå bundet til ADRESSE, og det er lagt til en
    # SEMANTISK sjekk som er sterkere enn navnet: ingen hjelper her
    # peker på en oppslagssti under `/v1/adresse`. Et omdøpt
    # `hentAdressefasit` ville sluppet forbi den gamle sjekken, men
    # ikke forbi denne.
    assert not re.search(
        r"export const (slaaOpp\w*Adresse|validerAdresse)", api)
    assert not re.search(r"/v1/adresse[^\"`\n]*oppslag", api)
    # ALLE FEM SKRIVEVEIENE sender en Idempotency-Key.
    for n in ("settAdressekrav", "registrerAdressesubjekt",
              "registrerAdresse", "registrerAdressekontroll",
              "settAdressesubjektAktiv"):
        i = api.index(f"export const {n} =")
        j = api.find("\n\n", i)
        kropp = api[i:j if j != -1 else len(api)]
        assert "idem || nyIdempotensnokkel()" in kropp, n


# ---------------------------------------------------------------------------
# INVARIANT 2: modulen_signerte_attestasjon
# ---------------------------------------------------------------------------

@pg
def test_invariant_modulen_signerte_attestasjon(migrator):
    """KLYNGENS FELLESDOM: modulen tar ikke attestasjonsfullmakten.

    `adresse_validert` er vilkåret M-25s `ordre.bekreft_og_fakturer`
    hviler på. v1 REGISTRERER grunnlaget og attesterer ingenting.

    MUTASJONEN SOM DREPER DENNE: en attestasjonskolonne på
    `adressekontroll`.
    """
    kolonner = migrator.execute(
        "SELECT table_name, column_name"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND (column_name LIKE '%%attest%%'"
        "        OR column_name LIKE '%%signat%%'"
        "        OR column_name LIKE '%%validert%%')",
        (list(EGNE),)).fetchall()
    migrator.rollback()
    assert kolonner == [], kolonner
    sql = _bare_kode(MIGRASJON, uten_strenger=True).lower()
    for ord_ in ("attestasjon", "attester", "signatur", "signer"):
        assert ord_ not in sql, ord_
    # …og evidensen er EVIDENS, ikke en dom: beslutningen er alltid
    # `TILLAT` på en registrering modulen selv gjorde.
    i = sql.index("create function m19_evidens(")
    assert "revisjonslogg" in sql[i:sql.index("end $$;", i)]


# ---------------------------------------------------------------------------
# INVARIANT 3: adresse_uten_kilde_og_metode
# ---------------------------------------------------------------------------

@pg
def test_invariant_adresse_uten_kilde_og_metode(migrator):
    """HVER ADRESSE HAR EN KILDE, HVER KONTROLL EN METODE OG EN HVEM.

    MUTASJONEN SOM DREPER DENNE: gjør `kontrollor` NULLbar.
    """
    for tabell, felter in (
            ("adresseversjon", ("kilde", "kilde_ref", "gjelder_fra",
                                "notat", "registrert_av")),
            ("adressekontroll", ("metode", "utfall", "kontrollor",
                                 "kilde_ref", "kontrollert",
                                 "registrert_av"))):
        kolonner = {r[0]: r[1] for r in migrator.execute(
            "SELECT column_name, is_nullable"
            "  FROM information_schema.columns"
            " WHERE table_schema='public' AND table_name=%s",
            (tabell,)).fetchall()}
        migrator.rollback()
        for felt in felter:
            assert kolonner.get(felt) == "NO", (tabell, felt)

    tenant = _tenantnavn("kilde")
    c = _rt()
    try:
        _krav(c, tenant)
        sid = _subjekt(c, tenant)
        # KILDEN ER ET LUKKET SETT.
        for kilde in ("gjetning", "oppslag", "", None):
            _sett_kontekst(c, tenant)
            with pytest.raises(psycopg.Error):
                c.execute(
                    "SELECT m19_registrer_adresse(%s,%s,%s,'A 1',NULL,"
                    "'0001','Oslo','NO',%s,'r','2026-01-01','n','u')",
                    (tenant, uuid.uuid4(), sid, kilde))
            c.rollback()
        vid, _ = _adresse(c, tenant, sid, kilde_ref="k1")
        # METODEN LIKESÅ — og `oppslag` er ikke blant dem.
        for metode in ("oppslag", "gjetning", "", None):
            _sett_kontekst(c, tenant)
            with pytest.raises(psycopg.Error):
                c.execute(
                    "SELECT m19_registrer_kontroll(%s,%s,%s,%s,"
                    "'godkjent','Per','r',NULL,'2026-01-12','u')",
                    (tenant, uuid.uuid4(), vid, metode))
            c.rollback()
        # …og KONTROLLØREN er obligatorisk.
        for hvem in ("", "   ", None):
            _sett_kontekst(c, tenant)
            with pytest.raises(psycopg.Error):
                c.execute(
                    "SELECT m19_registrer_kontroll(%s,%s,%s,'visuell',"
                    "'godkjent',%s,'r',NULL,'2026-01-12','u')",
                    (tenant, uuid.uuid4(), vid, hvem))
            c.rollback()
        # SAMME KILDEHENDELSE REGISTRERES ÉN GANG. En import som kjøres
        # to ganger er ikke to adresseendringer.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m19_registrer_adresse(%s,%s,%s,'B 2',NULL,"
                "'0002','Oslo','NO','oppgitt_av_kunde','k1',"
                "'2026-02-01','dublett','u')",
                (tenant, uuid.uuid4(), sid))
        assert "kilde_unik" in str(ei.value)
        c.rollback()
    finally:
        c.close()


@pg
def test_et_avslag_uten_begrunnelse_er_ingen_vurdering(migrator):
    """DOM 5s andre halvdel: `godkjent` trenger ingen begrunnelse — da
    er metoden og kontrolløren hele svaret. Alt annet gjør det."""
    tenant = _tenantnavn("begrunnelse")
    c = _rt()
    try:
        _krav(c, tenant)
        sid = _subjekt(c, tenant)
        vid, _ = _adresse(c, tenant, sid)
        for utfall in ("avvist", "ukontrollerbar"):
            _sett_kontekst(c, tenant)
            with pytest.raises(psycopg.Error) as ei:
                c.execute(
                    "SELECT m19_registrer_kontroll(%s,%s,%s,'visuell',"
                    "%s,'Per','r',NULL,'2026-01-12','u')",
                    (tenant, uuid.uuid4(), vid, utfall))
            assert "begrunnelse" in str(ei.value), utfall
            c.rollback()
            _kontroll(c, tenant, vid, utfall=utfall,
                      begrunnelse="finnes ikke", metode="visuell",
                      kilde_ref="ok-" + utfall)
        _kontroll(c, tenant, vid, utfall="godkjent", begrunnelse=None)
    finally:
        c.close()


# ---------------------------------------------------------------------------
# INVARIANT 4: adressehistorikk_overskrevet
# ---------------------------------------------------------------------------

@pg
def test_invariant_adressehistorikk_overskrevet(migrator):
    """DEN GJELDENDE ADRESSEN ER DEN SISTE VERSJONEN.

    Det finnes ingen kolonne som holder den, og hver versjon OG hver
    kontroll er frosset. To gjerder: eieren har ikke rettigheten, og
    VAKTEN stanser den som likevel har den.
    """
    rader = migrator.execute(
        "SELECT table_name, column_name"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND column_name IN ('gjeldende_adresse','naavaerende_adresse',"
        "                       'siste_adresse')"
        " ORDER BY 1,2", (list(EGNE),)).fetchall()
    migrator.rollback()
    assert rader == [], rader

    tenant = _tenantnavn("historikk")
    c = _rt()
    try:
        _krav(c, tenant)
        sid = _subjekt(c, tenant)
        _adresse(c, tenant, sid, linje1="Storgata 5", fra="2026-01-10",
                 kilde_ref="a")
        _adresse(c, tenant, sid, linje1="Lilleveien 2", postnr="0250",
                 fra="2026-03-01", kilde_ref="b")
        _sett_kontekst(c, tenant)
        rader = c.execute(
            "SELECT linje1_original, gjelder_fra, endret FROM"
            " m19_adressehistorikken(%s,%s,200)",
            (tenant, sid)).fetchall()
        c.rollback()
    finally:
        c.close()
    # NYESTE ØVERST, og skiftet er merket.
    assert [(r[0], r[2]) for r in rader] == [
        ("Lilleveien 2", True), ("Storgata 5", False)], rader

    for tabell in ("adresseversjon", "adressekontroll"):
        rettigheter = migrator.execute(
            "SELECT DISTINCT privilege_type"
            "  FROM information_schema.table_privileges"
            " WHERE grantee='disponit_adresse_eier' AND table_name=%s"
            " ORDER BY 1", (tabell,)).fetchall()
        migrator.rollback()
        assert [r[0] for r in rettigheter] == ["INSERT", "SELECT"], tabell

    for sql, ord_ in (
            ("UPDATE adresseversjon SET linje1_original='Hacket'",
             "FROSSET"),
            ("DELETE FROM adresseversjon", "DELETE avvist")):
        _sett_kontekst(migrator, tenant)
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(sql + " WHERE tenant=%s", (tenant,))
        assert ord_ in str(ei.value), sql
        migrator.rollback()


@pg
def test_en_framtidig_dato_kan_ikke_skjule_et_adresseskifte(migrator):
    """Sveipen måler «siste versjon med dato <= i dag». En
    framtidsdatert rad ville vært den siste for DØREN og usynlig for
    SVEIPEN (110/111s lærdom) — og funnet ville pekt på en adresse som
    ikke lenger var den gjeldende.

    MUTASJONEN SOM DREPER DENNE: fjern datosjekken fra
    `m19_registrer_adresse`.
    """
    tenant = _tenantnavn("framtid")
    c = _rt()
    try:
        _krav(c, tenant)
        sid = _subjekt(c, tenant)
        _adresse(c, tenant, sid)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m19_registrer_adresse(%s,%s,%s,'X 1',NULL,"
                "'0001','Oslo','NO','manuell','f',current_date + 1,"
                "'i morgen','u')", (tenant, uuid.uuid4(), sid))
        assert "framtida" in str(ei.value)
        c.rollback()
        vid, _ = _adresse(c, tenant, sid, fra="2026-02-01",
                          kilde_ref="k2")
        # …og en KONTROLL kan heller ikke være gjort i framtida.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m19_registrer_kontroll(%s,%s,%s,'visuell',"
                "'godkjent','Per','r',NULL,current_date + 1,'u')",
                (tenant, uuid.uuid4(), vid))
        assert "framtida" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    n = migrator.execute(
        "SELECT count(*) FROM adresseversjon WHERE tenant=%s",
        (tenant,)).fetchone()[0]
    migrator.rollback()
    assert n == 2


# ---------------------------------------------------------------------------
# INVARIANT 5: normalisering_uten_original
# ---------------------------------------------------------------------------

@pg
def test_invariant_normalisering_uten_original(migrator):
    """BEGGE FORMENE STÅR PÅ RADEN, og originalen er urørt.

    MUTASJONEN SOM DREPER DENNE: la døren skrive den normaliserte
    formen i originalkolonnen.
    """
    kolonner = {r[0] for r in migrator.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name='adresseversjon'"
    ).fetchall()}
    migrator.rollback()
    for felt in ("linje1_original", "postnr_original",
                 "poststed_original", "linje1_normalisert",
                 "postnr_normalisert", "poststed_normalisert"):
        assert felt in kolonner, felt

    tenant = _tenantnavn("normalisering")
    c = _rt()
    try:
        _krav(c, tenant)
        sid = _subjekt(c, tenant)
        vid, _ = _adresse(c, tenant, sid, linje1="  Storgt.   5 ",
                          poststed="  Oslo  ", land="no")
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    rad = migrator.execute(
        "SELECT linje1_original, linje1_normalisert, poststed_original,"
        " poststed_normalisert, land FROM adresseversjon"
        " WHERE versjon_id=%s", (vid,)).fetchone()
    migrator.rollback()
    # ORIGINALEN ER TRIMMET I YTTERKANTENE, MEN IKKE RØRT INNI: det
    # kunden skrev mellom første og siste tegn står som det sto.
    assert rad[0] == "Storgt.   5", rad[0]
    assert rad[1] == "storgt. 5", rad[1]
    assert rad[2] == "Oslo" and rad[3] == "oslo"
    assert rad[4] == "NO"

    # …OG NORMALISERINGEN GJETTER IKKE. «Storgt.» blir ikke «Storgata»,
    # og postnummeret slås ikke opp mot poststedet. En normalisering som
    # gjetter er et oppslag i forkledning.
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_adresse_eier")
    for inn, ut in (("Storgt. 5", "storgt. 5"),
                    ("St. Olavs  gate 3", "st. olavs gate 3"),
                    ("  A\tB  ", "a b"),
                    ("ÅSVEIEN 1", "åsveien 1"),
                    ("", ""), (None, "")):
        assert migrator.execute("SELECT m19_normaliser(%s)",
                                (inn,)).fetchone()[0] == ut, inn
    migrator.rollback()
    # …og den er IMMUTABLE: samme streng må gi samme svar om fem år.
    volatil = migrator.execute(
        "SELECT provolatile FROM pg_proc WHERE proname='m19_normaliser'"
    ).fetchone()[0]
    migrator.rollback()
    assert volatil == "i", volatil


@pg
def test_to_skrivemater_av_samme_adresse_er_ikke_et_skifte(migrator):
    """DET NORMALISERINGEN ER TIL FOR.

    Rettes en skrivefeil, er det ikke en flytting — og en flate som
    viste det som et skifte ville gjort hver renskriving til en
    hendelse noen måtte se på.
    """
    tenant = _tenantnavn("skrivemate")
    c = _rt()
    try:
        _krav(c, tenant)
        sid = _subjekt(c, tenant)
        _, e1 = _adresse(c, tenant, sid, linje1="  Storgata   5 ",
                         poststed="Oslo", fra="2026-01-10",
                         kilde_ref="a")
        _, e2 = _adresse(c, tenant, sid, linje1="Storgata 5",
                         poststed="OSLO", fra="2026-02-01",
                         kilde_ref="b")
        _, e3 = _adresse(c, tenant, sid, linje1="Lilleveien 2",
                         postnr="0250", fra="2026-03-01",
                         kilde_ref="c")
    finally:
        c.close()
    # FØRSTE er alltid et skifte (det fantes ingen før), ANNEN er samme
    # adresse skrevet annerledes, TREDJE er en faktisk flytting.
    assert (e1, e2, e3) == (True, False, True)


# ---------------------------------------------------------------------------
# INVARIANT 6: valideringskrav_hardkodet
# ---------------------------------------------------------------------------

def test_invariant_valideringskrav_hardkodet():
    for fil in MODULFILER:
        for m in re.finditer(
                r"^([A-Z_]*(?:GRENSE|KRAV|TERSKEL|DOGN|FRIST)"
                r"[A-Z_]*)\s*=\s*(\d+)", _bare_kode(fil), re.M):
            assert m.group(1) in ("GRENSE",), \
                f"{fil.name} har kravkonstanten {m.group(1)}"
    from drift import adressesveip
    assert adressesveip.GRENSE == 500
    kode = _bare_kode(MIGRASJON)
    assert "m19_sveip_adresser(p_grense INT DEFAULT 500)" in kode
    assert "FROM public.adressekrav" in kode
    from api.adresse import krav_endepunkt
    doc = krav_endepunkt.__doc__ or ""
    assert "M-1" in doc and "gap" in doc.lower()


@pg
def test_kravene_versjoneres_og_kan_ikke_slettes(migrator):
    tenant = _tenantnavn("kravversjon")
    c = _rt()
    try:
        assert _krav(c, tenant, ukontrollert=14) == 1
        assert _krav(c, tenant, ukontrollert=30) == 2
        # ET KRAV UTEN METODER er en konfigurasjonsfeil, ikke en
        # streng policy: hver adresse ville blitt et funn.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m19_sett_krav(%s,14,365,%s,'u')",
                      (tenant, []))
        assert "minst én metode" in str(ei.value)
        c.rollback()
        # …og en metode utenfor det lukkede settet er nektet. UTEN
        # DENNE kunne v1-dommen vært omgått gjennom en
        # konfigurasjonsverdi framfor gjennom kode.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m19_sett_krav(%s,14,365,%s,'u')",
                      (tenant, ["oppslag"]))
        assert "kontrollmetode" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("DELETE FROM adressekrav WHERE tenant=%s",
                         (tenant,))
    assert "DELETE avvist" in str(ei.value)
    migrator.rollback()
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "UPDATE adressekrav SET ukontrollert_dogn=1 WHERE tenant=%s",
            (tenant,))
    assert "versjonen må øke" in str(ei.value)
    migrator.rollback()


def test_invariant_kravet_over_api():
    from api.adresse import _bool, _heltall, _land, _metoder, _valg
    from api.policyadmin_http import _Avbrudd
    for verdi in (1.5, True, False, "3", None, -1, 3651):
        with pytest.raises(_Avbrudd):
            _heltall({"n": verdi}, "n", "r", 0, 3650)
    assert _heltall({"n": 0}, "n", "r", 0, 3650) == 0
    # METODESETTET ER DET YTRE GJERDET rundt v1-dommen.
    for verdi in ([], ["oppslag"], "dokumentert", None,
                  ["visuell", "visuell"],
                  ["visuell", "dokumentert", "oppslag"]):
        with pytest.raises(_Avbrudd):
            _metoder({"m": verdi}, "m", "r")
    assert _metoder({"m": ["visuell"]}, "m", "r") == ["visuell"]
    for verdi in ("NOR", "n", "", 12, None, "N1"):
        with pytest.raises(_Avbrudd):
            _land({"l": verdi}, "l", "r")
    assert _land({"l": " no "}, "l", "r") == "NO"
    for verdi in (1, 0, "ja", None):
        with pytest.raises(_Avbrudd):
            _bool({"b": verdi}, "b", "r")
    with pytest.raises(_Avbrudd):
        _valg({"s": "gjetning"}, "s", "r", ("a", "b"))


# ---------------------------------------------------------------------------
# Funnene, og skillet mellom dem
# ---------------------------------------------------------------------------

@pg
def test_de_fem_funntypene_og_skillet_mellom_dem(migrator):
    """MODULENS EGENTLIGE VERDI ER SKILLET.

    «Ingen har sett på den», «noen så på den og sa nei», «noen så på den
    men ikke godt nok», og «den ble godkjent for lenge siden» er fire
    helt forskjellige samtaler — og en modul som slo dem sammen ville
    vært ubrukelig som grunnlag for `adresse_validert`.
    """
    tenant = _tenantnavn("funn")
    c = _rt()
    try:
        _krav(c, tenant, ukontrollert=14, gyldig=365)
        ingen = _subjekt(c, tenant, ref="A-ukontrollert")
        _adresse(c, tenant, ingen, fra="2026-01-01", kilde_ref="a")

        ok = _subjekt(c, tenant, ref="B-godkjent")
        v_ok, _ = _adresse(c, tenant, ok, fra="2026-08-01",
                           kilde_ref="b")
        _kontroll(c, tenant, v_ok, metode="bekreftet_av_kunde",
                  dato="2026-08-02")

        avvist = _subjekt(c, tenant, ref="C-avvist")
        v_av, _ = _adresse(c, tenant, avvist, fra="2026-08-01",
                           kilde_ref="c")
        _kontroll(c, tenant, v_av, metode="visuell", utfall="avvist",
                  begrunnelse="finnes ikke", dato="2026-08-02")

        svak = _subjekt(c, tenant, ref="D-svak")
        v_sv, _ = _adresse(c, tenant, svak, fra="2026-08-01",
                           kilde_ref="d")
        _kontroll(c, tenant, v_sv, metode="visuell", dato="2026-08-02")

        utl = _subjekt(c, tenant, ref="E-utlopt")
        v_ut, _ = _adresse(c, tenant, utl, fra="2024-01-01",
                           kilde_ref="e")
        _kontroll(c, tenant, v_ut, metode="dokumentert",
                  dato="2024-06-01")

        ukb = _subjekt(c, tenant, ref="F-ukontrollerbar")
        v_uk, _ = _adresse(c, tenant, ukb, fra="2026-01-01",
                           kilde_ref="f")
        _kontroll(c, tenant, v_uk, metode="visuell",
                  utfall="ukontrollerbar",
                  begrunnelse="ingen postkasse", dato="2026-01-05")
    finally:
        c.close()

    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_adresse_eier")
    funn = {}
    for ref, ft, og in migrator.execute(
            "SELECT s.ekstern_ref, k.funntype, k.over_grense"
            "  FROM m19_funnkandidater(%s, '2026-09-03'::date) k"
            "  JOIN adressesubjekt s ON s.tenant=%s"
            "   AND s.subjekt_id=k.subjekt_id ORDER BY 1,2",
            (tenant, tenant)).fetchall():
        funn.setdefault(ref, []).append((ft, og))
    migrator.rollback()

    # EN GODKJENT ADRESSE MED EN METODE SOM TELLER ER IKKE ET FUNN.
    assert "B-godkjent" not in funn, funn
    # 2026-09-03 minus 2026-01-01 er 245 døgn, minus fristen 14.
    assert funn["A-ukontrollert"] == [("ukontrollert_adresse", 231)]
    assert funn["C-avvist"] == [("avvist_adresse", 0)]
    assert funn["D-svak"] == [("utilstrekkelig_metode", 0)]
    # 2026-09-03 minus 2024-06-01 er 824 døgn, minus vinduet 365.
    assert funn["E-utlopt"] == [("kontroll_utlopt", 459)]
    # «UKONTROLLERBAR» TELLER IKKE SOM EN KONTROLL. Å telle den ville
    # gjort «vi klarte ikke å sjekke» om til «vi har sjekket».
    assert funn["F-ukontrollerbar"] == [("ukontrollert_adresse", 231)]


@pg
def test_en_tenant_uten_krav_er_et_funn(migrator):
    tenant = _tenantnavn("utenkrav")
    c = _rt()
    try:
        sid = _subjekt(c, tenant)
        _adresse(c, tenant, sid)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert [f[0] for f in _funn(migrator, tenant)] == ["ingen_krav"]


@pg
def test_en_ny_adresse_lukker_kontrollen_som_gjaldt_den_gamle(migrator):
    """MODULENS FINESTE SKILLE, og grunnen til at kontrollene er nøklet
    på VERSJONEN.

    Flytter kunden, er fjorårets godkjenning fortsatt sann om den gamle
    adressen — og sier ingenting om den nye. En modul som lot
    godkjenningen følge SUBJEKTET ville attestert den nye adressen på
    et grunnlag som aldri så den.

    MUTASJONEN SOM DREPER DENNE: nøkle `adressekontroll` på subjektet.
    """
    tenant = _tenantnavn("flytting")
    c = _rt()
    try:
        _krav(c, tenant, ukontrollert=14, gyldig=365)
        sid = _subjekt(c, tenant)
        gammel, _ = _adresse(c, tenant, sid, fra="2026-01-01",
                             kilde_ref="a")
        _kontroll(c, tenant, gammel, metode="dokumentert",
                  dato="2026-01-05")
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert [f[0] for f in _funn(migrator, tenant) if f[4]] == []

    c = _rt()
    try:
        _adresse(c, tenant, sid, linje1="Lilleveien 2", postnr="0250",
                 fra="2026-06-01", kilde_ref="b")
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    # DEN NYE ADRESSEN ER UKONTROLLERT. Godkjenningen ble IKKE med.
    assert [f[0] for f in _funn(migrator, tenant) if f[4]] == \
        ["ukontrollert_adresse"]


@pg
def test_sveipen_er_idempotent_og_lukker_uten_aa_slette(migrator):
    tenant = _tenantnavn("idempotens")
    c = _rt()
    try:
        _krav(c, tenant, ukontrollert=14)
        sid = _subjekt(c, tenant)
        vid, _ = _adresse(c, tenant, sid, fra="2026-01-01")
    finally:
        c.close()
    with _sv() as v:
        rad1 = _sveip(v)
    assert rad1[1] >= 1, rad1
    with _sv() as v:
        rad2 = _sveip(v)
    # NYE = 0, OPPDATERTE > 0: raden ble friskmeldt, ikke skrevet på ny.
    assert rad2[1] == 0, "sveip nummer to skrev nye rader"
    assert rad2[2] >= 1, rad2

    c = _rt()
    try:
        _kontroll(c, tenant, vid, metode="dokumentert",
                  dato="2026-01-15")
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    # FUNNET ER LUKKET, MEN RADEN STÅR: at et funn HAR stått er også en
    # måling.
    assert [f[0] for f in _funn(migrator, tenant) if f[4]] == []
    assert len(_funn(migrator, tenant)) == 1


@pg
def test_oppdaterte_funn_akkumuleres_over_tenantene(migrator):
    """CodeRabbit, alvorlig og REELT: `INTO v_oppdaterte` SATTE summen
    på nytt for hver tenant, så andre sveip bare rapporterte den siste.

    Porten trenger TO tenanter for å se det. Røyktesten min hadde to,
    men målte bare at `nye` var null — og null er null uansett hvor
    mange tenanter som ble overskrevet.

    MUTASJONEN SOM DREPER DENNE: bytt `+ coalesce(v_m, 0)` mot en
    tilordning.
    """
    a = _tenantnavn("akk-a")
    b = _tenantnavn("akk-b")
    for tenant, antall in ((a, 2), (b, 3)):
        c = _rt()
        try:
            _krav(c, tenant, ukontrollert=14)
            for i in range(antall):
                sid = _subjekt(c, tenant, ref=f"R{i}")
                _adresse(c, tenant, sid, fra="2026-01-01",
                         kilde_ref=f"k{i}")
        finally:
            c.close()
    with _sv() as v:
        forste = _sveip(v)
    assert forste[1] >= 5, forste
    with _sv() as v:
        andre = _sveip(v)
    # ANDRE SVEIP: ingen nye, og ALLE FEM friskmeldt — ikke bare de tre
    # fra den siste tenanten.
    assert andre[1] == 0, andre
    assert andre[2] >= 5, \
        f"oppdaterte ble overskrevet per tenant: {andre}"


@pg
def test_en_senere_ukontrollerbar_opphever_ikke_en_godkjenning(migrator):
    """CodeRabbit, mindre og REELT: armen leste bare den SISTE
    kontrollen.

    En adresse noen FAKTISK har godkjent ville stått som aldri
    kontrollert bare fordi et senere forsøk ikke lot seg gjennomføre —
    og `kontroll_utlopt` ville sagt det motsatte om samme rad.

    MUTASJONEN SOM DREPER DENNE: fjern `NOT EXISTS godkjent`-gjerdet
    fra `ukontrollert_adresse`-armen.
    """
    tenant = _tenantnavn("ukb-etter-ok")
    c = _rt()
    try:
        _krav(c, tenant, ukontrollert=14, gyldig=365)
        sid = _subjekt(c, tenant)
        vid, _ = _adresse(c, tenant, sid, fra="2026-08-01")
        _kontroll(c, tenant, vid, metode="dokumentert",
                  utfall="godkjent", dato="2026-08-02")
        # …og så et SENERE forsøk som ikke lot seg gjennomføre.
        _kontroll(c, tenant, vid, metode="visuell",
                  utfall="ukontrollerbar",
                  begrunnelse="postkassen var borte", dato="2026-08-20")
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_adresse_eier")
    funn = migrator.execute(
        "SELECT funntype FROM m19_funnkandidater(%s,'2026-09-03'::date)"
        " ORDER BY 1", (tenant,)).fetchall()
    migrator.rollback()
    # GODKJENNINGEN STÅR. Ingen av de to armene fyrer.
    assert funn == [], funn


@pg
def test_et_deaktivert_subjekt_lukker_funnene_og_beholder_historikken(
        migrator):
    tenant = _tenantnavn("deaktiver")
    c = _rt()
    try:
        _krav(c, tenant, ukontrollert=14)
        sid = _subjekt(c, tenant)
        vid, _ = _adresse(c, tenant, sid, fra="2026-01-01")
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert _funn(migrator, tenant)
    c = _rt()
    try:
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT m19_sett_subjektaktiv(%s,%s,false,'u')",
                         (tenant, sid)).fetchone()[0] is True
        c.commit()
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT m19_sett_subjektaktiv(%s,%s,false,'u')",
                         (tenant, sid)).fetchone()[0] is False
        c.commit()
        # ET DEAKTIVERT SUBJEKT TAR IKKE IMOT NYE ADRESSER…
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m19_registrer_adresse(%s,%s,%s,'Z 9',NULL,"
                "'0009','Oslo','NO','manuell','z','2026-02-01','x','u')",
                (tenant, uuid.uuid4(), sid))
        assert "deaktivert" in str(ei.value)
        c.rollback()
        # …OG INGEN NYE KONTROLLER. En kontroll av en adresse ingen
        # sender noe til er ingen måling.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m19_registrer_kontroll(%s,%s,%s,'visuell',"
                "'godkjent','Per','z',NULL,'2026-02-01','u')",
                (tenant, uuid.uuid4(), vid))
        assert "deaktivert" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    assert [f[0] for f in _funn(migrator, tenant) if f[4]] == []
    _sett_kontekst(migrator, tenant)
    n = migrator.execute(
        "SELECT count(*) FROM adresseversjon WHERE tenant=%s",
        (tenant,)).fetchone()[0]
    migrator.rollback()
    assert n == 1
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("DELETE FROM adressesubjekt WHERE tenant=%s",
                         (tenant,))
    assert "DELETE avvist" in str(ei.value)
    migrator.rollback()


@pg
def test_sveipen_slaar_ingenting_opp_og_rorer_ingen_versjon(migrator):
    """Målt på VIRKELIGHETEN: en full sveip endrer ikke ett radantall
    utenfor registeret — OG DEN RØRER INGEN ADRESSE."""
    tenant = _tenantnavn("sveip")
    c = _rt()
    try:
        _krav(c, tenant, ukontrollert=14)
        sid = _subjekt(c, tenant)
        _adresse(c, tenant, sid, fra="2026-01-01")
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    for_bok = migrator.execute(
        "SELECT versjon_id, linje1_original, linje1_normalisert"
        "  FROM adresseversjon WHERE tenant=%s ORDER BY versjon_id",
        (tenant,)).fetchall()
    migrator.rollback()
    for_ = _tell_utenfor(migrator)
    with _sv() as v:
        _sveip(v)
    etter = _tell_utenfor(migrator)
    assert "revisjonslogg" in for_
    for_.pop("revisjonslogg")
    etter.pop("revisjonslogg")
    assert for_ == etter, \
        ("sveipen endret radantall utenfor registeret: "
         + str({k: (for_[k], etter[k]) for k in for_
                if for_[k] != etter[k]}))
    _sett_kontekst(migrator, tenant)
    etter_bok = migrator.execute(
        "SELECT versjon_id, linje1_original, linje1_normalisert"
        "  FROM adresseversjon WHERE tenant=%s ORDER BY versjon_id",
        (tenant,)).fetchall()
    migrator.rollback()
    assert for_bok == etter_bok, "sveipen rørte adressene"


# ---------------------------------------------------------------------------
# INVARIANT 7: tenantlekkasje_i_adresseregister
# ---------------------------------------------------------------------------

@pg
def test_invariant_tenantlekkasje_direkte(migrator):
    a = _tenantnavn("lekk-a")
    b = _tenantnavn("lekk-b")
    c = _rt()
    try:
        _krav(c, a)
        _subjekt(c, a)
        _krav(c, b)
        _sett_kontekst(c, b)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT * FROM m19_adressestatus(%s)", (a,))
        assert "tenantkontekst" in str(ei.value)
        c.rollback()
        _sett_kontekst(c, a)
        assert c.execute("SELECT * FROM m19_adressestatus(%s)",
                         (a,)).fetchone()[0] == 1
        c.rollback()
    finally:
        c.close()
    for tab in EGNE:
        rad = migrator.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
            " WHERE oid = %s::regclass", (f"public.{tab}",)).fetchone()
        assert rad == (True, True), f"{tab}: RLS ikke ENABLE+FORCE"
    migrator.rollback()


@pg
def test_kryss_tenant_policyen_er_snever(migrator):
    """SVEIPENS ENESTE KRYSS-TENANT-SVAR er «hvilke tenanter finnes».

    Policyen står på SUBJEKTTABELLEN ALENE, bare FOR SELECT, bare til
    eieren, og bare når ingen kontekst står — tre gjerder (111s form).

    MUTASJONEN SOM DREPER DENNE: legg den samme policyen på
    `adresseversjon`.
    """
    rader = migrator.execute(
        "SELECT tablename, cmd, roles::text, qual FROM pg_policies"
        " WHERE schemaname='public' AND policyname LIKE 'm19_%'"
        " ORDER BY 1").fetchall()
    migrator.rollback()
    assert len(rader) == 1, rader
    tabell, cmd, roller, qual = rader[0]
    assert tabell == "adressesubjekt"
    assert cmd == "SELECT"
    assert roller == "{disponit_adresse_eier}"
    assert "disponit.tenant" in qual and "IS NULL" in qual.upper()


@pg
def test_invariant_tenantlekkasje_over_api(migrator, klient):
    fremmed = _tenantnavn("fremmed")
    # REFERANSENE ER UNIKE PER KJØRING: `TENANT` deles med resten av
    # suiten, og en fast streng ville kollidert med forrige kjøring i
    # samme base.
    egen = "EGEN-" + secrets.token_hex(4)
    fremmed_ref = "FREMMED-" + secrets.token_hex(4)
    c = _rt()
    try:
        _krav(c, TENANT)
        _subjekt(c, TENANT, ref=egen)
        _krav(c, fremmed)
        _subjekt(c, fremmed, ref=fremmed_ref)
    finally:
        c.close()
    cookie, _csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/adresse", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    kropp = json.dumps(r.json())
    assert egen in kropp
    assert fremmed_ref not in kropp


@pg
def test_ingen_av_de_fem_tabellene_kan_tommes(migrator):
    tenant = _tenantnavn("truncate")
    c = _rt()
    try:
        _krav(c, tenant, ukontrollert=14)
        sid = _subjekt(c, tenant)
        vid, _ = _adresse(c, tenant, sid, fra="2026-01-01")
        _kontroll(c, tenant, vid, dato="2026-01-05")
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    for tab in EGNE:
        _sett_kontekst(migrator, tenant)
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(f"TRUNCATE public.{tab}")
        assert ("TRUNCATE avvist" in str(ei.value)
                or "foreign key" in str(ei.value)), f"{tab}: {ei.value}"
        migrator.rollback()
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("TRUNCATE public.adressesubjekt CASCADE")
    assert "TRUNCATE avvist" in str(ei.value), ei.value
    migrator.rollback()


def test_skrivedorene_som_leser_en_tilstand_laser_raden():
    """…OG DE LÅSER EN RAD DE HAR UPDATE PÅ.

    `SELECT ... FOR UPDATE` krever UPDATE-retten, og de to frosne
    tabellene har den ikke. Kontrolldøren låser derfor SUBJEKTET, ikke
    versjonen — M-42s lærdom (110), gjentatt her fordi den kostet en
    runde til.
    """
    sql = _bare_kode(MIGRASJON)
    for doer in ("m19_registrer_adresse", "m19_registrer_kontroll",
                 "m19_sett_subjektaktiv"):
        i = sql.index(f"CREATE FUNCTION {doer}(")
        kropp = sql[i:sql.index("END $$;", i)]
        assert "FOR UPDATE" in kropp, doer
        # Låsen står på `adressesubjekt` — den ENESTE av de tre
        # tabellene dørene rører som eieren har UPDATE på. Målt mot
        # setningens EGEN SELECT, ikke mot et tegnvindu en kommentar
        # kan skyve ut av rekkevidde.
        for m in re.finditer(r"FOR UPDATE", kropp):
            start = kropp.rfind("SELECT", 0, m.start())
            assert start != -1, doer
            setning = kropp[start:m.end()]
            assert "public.adressesubjekt" in setning, (doer, setning)


# ---------------------------------------------------------------------------
# Sveipearbeideren
# ---------------------------------------------------------------------------

@pg
def test_sveipen_nekter_kontekst(migrator):
    with _sv() as v:
        v.execute("SELECT set_config('disponit.tenant','t',false)")
        with pytest.raises(psycopg.Error) as ei:
            v.execute("SELECT * FROM m19_sveip_adresser(500)")
        assert "KRYSS-TENANT" in str(ei.value)
        v.rollback()


@pg
def test_sveiperollen_har_ingen_tabellrettigheter(migrator):
    if not ADRESSESVEIP_DSN:
        pytest.skip("DISPONIT_TEST_ADRESSESVEIP_DSN ikke satt")
    rader = migrator.execute(
        "SELECT table_name, privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_adressesveip'").fetchall()
    migrator.rollback()
    assert rader == [], rader


def test_sveipen_nekter_aa_starte_uten_egen_dsn(tmp_path, monkeypatch):
    from drift import kjor_adressesveip
    monkeypatch.delenv("DISPONIT_ADRESSESVEIP_URL", raising=False)
    monkeypatch.setenv("DISPONIT_ADRESSESVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.setattr("db.hemmeligheter.last_credentials", lambda: None)
    assert kjor_adressesveip.main() == 2


def test_arbeidernokkelen_er_modulens_egen():
    from drift import (adressesveip, betalingssveip, kontovaktsveip,
                       lagersveip, prisboksveip, prosjektsveip)
    nokler = [m.ARBEIDERNOKKEL for m in
              (betalingssveip, kontovaktsveip, lagersveip, prisboksveip,
               prosjektsveip)]
    assert adressesveip.ARBEIDERNOKKEL not in nokler


# ---------------------------------------------------------------------------
# INVARIANT 8: ui_axe_alvorlige_brudd
# ---------------------------------------------------------------------------

def test_invariant_ui_axe_bor_i_js_suiten():
    fil = (ROT / "platform" / "core" / "ui" / "test" / "adresse.test.js")
    assert fil.exists(), "adresse.test.js mangler"
    assert "axe" in fil.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# HTTP-riggen
# ---------------------------------------------------------------------------

def _C_SESJON():
    from api import sesjon as sesjonmodul
    return sesjonmodul.C_SESJON


def _browserokt(migrator, roller):
    from api import sesjon as sesjonmodul
    _sett_kontekst(migrator, TENANT)
    bid = migrator.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://m19.test', %s) RETURNING bruker_id",
        ("s19-" + secrets.token_hex(6),)).fetchone()[0]
    migrator.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller, aktiv)"
        " VALUES (%s,%s,%s,true)", (TENANT, bid, list(roller)))
    cookie, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    ver = migrator.execute(
        "SELECT authz_version FROM brukermedlemskap WHERE tenant=%s"
        " AND bruker_id=%s", (TENANT, bid)).fetchone()[0]
    migrator.execute(
        "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
        " authz_snapshot, csrf_hash, opprettet, siste_bruk, utloper,"
        " tilbakekalt) VALUES (%s,%s,%s,%s,%s, now(), now(),"
        " now()+interval '1 hour', false)",
        (sesjonmodul._hash(cookie), TENANT, bid, ver,
         sesjonmodul._hash(csrf)))
    migrator.commit()
    return cookie, csrf


def _hpost(klient, cookie, csrf, sti, kropp, idem=None):
    from api import sesjon as sesjonmodul
    return klient.post(sti, json=kropp,
                       cookies={sesjonmodul.C_SESJON: cookie},
                       headers={"X-Disponit-CSRF": csrf,
                                "Idempotency-Key":
                                    idem or secrets.token_urlsafe(24)})


@pg
@dekker("adresse_ulovlig_tilstand")
def test_http_adresse_i_framtida_er_409(migrator, klient):
    """FEILVEIEN `adresse_ulovlig_tilstand`, ende til ende.

    Kroppen ER velformet: landkoden har riktig form, kilden er fra det
    lukkede settet, notatet står der. Det er BASEN som sier at en
    adresse ikke kan gjelde fra framtida.

    Dette er også testen `test_api_porter.test_hver_feilvei_har_en_test`
    krever for den nye feilveien.
    """
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _hpost(klient, cookie, csrf, "/v1/adresse/krav",
               {"ukontrollert_dogn": 14, "kontroll_gyldig_dogn": 365,
                "godkjente_metoder": ["bekreftet_av_kunde",
                                      "dokumentert"]})
    assert r.status_code in (200, 201), r.text
    ref = "H-" + secrets.token_hex(4)
    r = _hpost(klient, cookie, csrf, "/v1/adresse/subjekt",
               {"ekstern_ref": ref, "navn": "HTTP Kunde"})
    assert r.status_code in (200, 201), r.text
    sid = r.json()["subjekt_id"]
    # …og et felt som ALLTID er null står ikke i svaret.
    assert "ny" not in r.json()

    idem = secrets.token_urlsafe(24)
    kropp = {"linje1": "  Storgt.   5 ", "linje2": None,
             "postnr": "0155", "poststed": "Oslo", "land": "no",
             "kilde": "oppgitt_av_kunde", "kilde_ref": "evt_h1",
             "gjelder_fra": "2026-01-10", "notat": "oppgitt"}
    r = _hpost(klient, cookie, csrf, f"/v1/adresse/{sid}/versjon",
               kropp, idem=idem)
    assert r.status_code in (200, 201), r.text
    vid = r.json()["versjon_id"]
    assert r.json()["endret"] is True
    # SP-2: SAMME NØKKEL GIR IKKE TO VERSJONER.
    r2 = _hpost(klient, cookie, csrf, f"/v1/adresse/{sid}/versjon",
                kropp, idem=idem)
    assert r2.status_code == 409, r2.text

    # FRAMTIDIG DATO: TILSTANDEN sier nei.
    from datetime import date, timedelta
    i_morgen = (date.today() + timedelta(days=1)).isoformat()
    r = _hpost(klient, cookie, csrf, f"/v1/adresse/{sid}/versjon",
               {**kropp, "kilde_ref": "evt_h2",
                "gjelder_fra": i_morgen})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "adresse_ulovlig_tilstand"
    # SAMME REFERANSE TO GANGER: også en tilstand.
    r = _hpost(klient, cookie, csrf, "/v1/adresse/subjekt",
               {"ekstern_ref": ref, "navn": "Dublett"})
    assert r.status_code == 409, r.text

    # …og en ukjent kilde, metode eller landkode er 400: KROPPEN er feil.
    for felt, verdi in (("kilde", "brevdue"), ("kilde", None),
                        ("land", "NOR"), ("linje1", "")):
        r = _hpost(klient, cookie, csrf, f"/v1/adresse/{sid}/versjon",
                   {**kropp, "kilde_ref": "evt_x", felt: verdi})
        assert r.status_code == 400, (felt, verdi, r.text)
    # `oppslag` ER IKKE EN METODE, og API-et sier det som 400.
    for felt, verdi in (("metode", "oppslag"), ("metode", "gjetning"),
                        ("utfall", "kanskje")):
        r = _hpost(klient, cookie, csrf,
                   f"/v1/adresse/versjon/{vid}/kontroll",
                   {"metode": "visuell", "utfall": "godkjent",
                    "kontrollor": "Per", "kilde_ref": "k1",
                    "kontrollert": "2026-01-12", felt: verdi})
        assert r.status_code == 400, (felt, verdi, r.text)
    # …og et AVSLAG UTEN BEGRUNNELSE likeså.
    r = _hpost(klient, cookie, csrf,
               f"/v1/adresse/versjon/{vid}/kontroll",
               {"metode": "visuell", "utfall": "avvist",
                "kontrollor": "Per", "kilde_ref": "k2",
                "kontrollert": "2026-01-12"})
    assert r.status_code == 400, r.text
    # `aktiv` ER PÅKREVD.
    r = _hpost(klient, cookie, csrf, f"/v1/adresse/{sid}/aktiv", {})
    assert r.status_code == 400, r.text

    # EN GYLDIG KONTROLL, og så er historikken beviset.
    r = _hpost(klient, cookie, csrf,
               f"/v1/adresse/versjon/{vid}/kontroll",
               {"metode": "bekreftet_av_kunde", "utfall": "godkjent",
                "kontrollor": "Kari Kontrollør", "kilde_ref": "k3",
                "kontrollert": "2026-01-12"})
    assert r.status_code in (200, 201), r.text

    r = klient.get(f"/v1/adresse/{sid}/historikk",
                   cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    linjer = r.json()["versjoner"]
    assert len(linjer) == 1
    # ORIGINALEN, IKKE NORMALISERINGEN — og trimmet bare i ytterkantene.
    assert linjer[0]["linje1"] == "Storgt.   5"
    assert linjer[0]["land"] == "NO"
    assert linjer[0]["kilde"] == "oppgitt_av_kunde"
    assert linjer[0]["siste_utfall"] == "godkjent"
    assert linjer[0]["siste_metode"] == "bekreftet_av_kunde"
    # …OG DEN NORMALISERTE FORMEN LEKKER IKKE UT AV BASEN. Den er noe vi
    # sammenligner på, ikke noe vi presenterer som kundens adresse.
    assert "normalisert" not in json.dumps(r.json())

    r = klient.get(f"/v1/adresse/versjon/{vid}/kontroller",
                   cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    k = r.json()["kontroller"]
    assert len(k) == 1
    assert k[0]["kontrollor"] == "Kari Kontrollør"
    assert k[0]["begrunnelse"] is None


@pg
def test_http_lesingen_bruker_okonomi_read(migrator, klient):
    from api.app import RUTESCOPE
    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert RUTESCOPE[("GET", "/v1/adresse")] == "okonomi:read"
    assert "okonomi:read" in ROLLE_TIL_SCOPES["admin"]
    for rolle in ("leser", "sikkerhet", "godkjenner"):
        assert "okonomi:read" not in ROLLE_TIL_SCOPES[rolle], rolle
    cookie, _c = _browserokt(migrator, ["leser"])
    r = klient.get("/v1/adresse", cookies={_C_SESJON(): cookie})
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# §0
# ---------------------------------------------------------------------------

def test_grensen_ble_registrert_for_koden():
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m19-v1"]
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["punktbinding"] == {}
    egen = Path(__file__).read_text(encoding="utf-8")
    for inv in g["invarianter"]:
        assert inv in egen, f"invarianten {inv} har ingen port"
