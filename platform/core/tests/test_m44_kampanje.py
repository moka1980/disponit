"""M-44 kampanjeregister v1 (114) — REGISTERET, IKKE UTSENDINGEN.

Grensen `m44-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

DEN SKARPESTE PORTEN ER `modulen_sendte`, og den er klyngens sterkeste
tilbakeholdelse.

M-44 er en annen figur enn de tre andre. De er manglende VERIFIKATORER
— betrodde parter som skal attestere et vilkår. M-44 er den manglende
AKTØREN: netthandelsmalen fører modulen som `modul:` på en
`auto`-handling, ikke i `verifikatorer`. Vilkårene har verifikatorer som
FINNES; det er handlingen selv som mangler en modul.

For de tre andre kunne man sagt at modulen bare mangler én evne. Her
finnes modulen FOR å sende, og v1 sender null. Det er hele dens grunn
til å eksistere som er holdt tilbake.

OG SE PÅ REVERSERINGEN MALEN FORESLÅR: `kompenserende`, med
`kampanje.send_korreksjon`. Botemiddelet for en feilsendt e-post er å
sende en TIL — en andre e-post til noen som ikke ville ha den første.

DEN NEST SKARPESTE er `samtykkehistorikk_overskrevet`. «Hadde vi lov
til å sende dette DEN DAGEN» er hele spørsmålet et tilsyn stiller, og
et samtykke som kunne oppdateres på stedet ville slettet svaret.

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

KAMPANJESVEIP_DSN = os.environ.get("DISPONIT_TEST_KAMPANJESVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "114_m44_kampanjeregister.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "kampanje.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "kampanje.py",
    ROT / "platform" / "drift" / "kampanjesveip.py",
    ROT / "platform" / "drift" / "kjor_kampanjesveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("kampanjegrense", "kampanjemottaker", "samtykkehendelse",
        "kampanje", "kampanjeplan", "kampanjefunn")

#: En oppdiktet e-postadresse. At den IKKE står i basen er en port.
KONTAKT = "Kari.Nordmann@Example.COM"

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
    return koble(KAMPANJESVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m44-{merke}-{secrets.token_hex(4)}"


def _grense(c, tenant, *, maks=2, periode=7, gyldig=730,
            aktor="u-test"):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m44_sett_grense(%s,%s,%s,%s,%s)",
                  (tenant, maks, periode, gyldig, aktor)).fetchone()[0]
    c.commit()
    return v


def _mottaker(c, tenant, *, ref=None, navn="Kari Kunde",
              kontakt=KONTAKT, mid=None, aktor="u-test"):
    mid = mid or uuid.uuid4()
    ref = ref or ("M-" + secrets.token_hex(4))
    _sett_kontekst(c, tenant)
    maske = c.execute(
        "SELECT m44_registrer_mottaker(%s,%s,%s,%s,%s,%s)",
        (tenant, mid, ref, navn, kontakt, aktor)).fetchone()[0]
    c.commit()
    return mid, maske


def _samtykke(c, tenant, mid, tilstand, dato, *,
              kanal="preferanseside", kilde_ref=None,
              formal="nyhetsbrev", notat="ok", aktor="u-test"):
    kilde_ref = kilde_ref or ("s_" + secrets.token_hex(4))
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m44_registrer_samtykke(%s,%s,%s,%s,%s,%s,%s,%s::date,"
        "%s,%s)",
        (tenant, uuid.uuid4(), mid, tilstand, kanal, kilde_ref, formal,
         dato, notat, aktor))
    c.commit()


def _kampanje(c, tenant, *, ref=None, dato="2026-08-10",
              lenke="https://x.example/avmeld", kid=None,
              aktor="u-test"):
    kid = kid or uuid.uuid4()
    ref = ref or ("K-" + secrets.token_hex(4))
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m44_registrer_kampanje(%s,%s,%s,'N','salg',%s,"
        "%s::date,%s)", (tenant, kid, ref, lenke, dato, aktor))
    c.commit()
    return kid


def _plan(c, tenant, kid, mid, *, aktor="u-test"):
    _sett_kontekst(c, tenant)
    n = c.execute("SELECT m44_legg_i_plan(%s,%s,%s,%s)",
                  (tenant, kid, mid, aktor)).fetchone()[0]
    c.commit()
    return n


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m44_sveip_kampanjer(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT funntype, over_grense, antall_i_periode, apen"
        "  FROM kampanjefunn WHERE tenant=%s ORDER BY funntype",
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
# INVARIANT 1: modulen_sendte
# ---------------------------------------------------------------------------

def test_invariant_modulen_sendte():
    """KLYNGENS STERKESTE DOM, målt på IMPORTENE, KODEN og RUTENE.

    Modulen finnes FOR å sende, og v1 sender null. Derfor er
    kanalmodulene ikke bare ubrukte — de er UIMPORTERTE.

    MUTASJONEN SOM DREPER DENNE: `import smtplib` i `kampanjesveip.py`.
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
                    "smtplib", "email", "httpx", "requests", "aiohttp",
                    "urllib", "http", "socket", "ssl", "ftplib",
                    "twilio", "sendgrid"}, \
                    f"{fil.name} importerer {n} — v1 sender ingenting"
    # FLATEN ER UTELATT HER og måles av `test_flaten_sender_ingenting`
    # i stedet: `send:` er NØKKELEN i den delte skjemarammen hver
    # eneste flate i huset bruker, ikke en utgående kanal.
    for fil in (MIGRASJON, MODULFILER[0], MODULFILER[1]):
        uten = _bare_kode(fil, uten_strenger=True).lower()
        for ord_ in (r"\bsend\b", r"\bsendt\b(?!_)", "smtp", "mailgun",
                     "sendgrid", "urlopen", r"requests\.", "utsending"):
            # `planlagt_sendt` er en KOLONNE, ikke en handling; den
            # fanges av `\bsendt\b(?!_)` bare når den står alene.
            treff = [m for m in re.finditer(ord_, uten)
                     if not uten[max(0, m.start() - 9):m.start()]
                     .endswith("planlagt_")]
            assert not treff, \
                f"{fil.name} bærer «{ord_}» — v1 sender ingenting"

    # …OG DET FINNES INGEN DØR SOM GJØR DET.
    sql = MIGRASJON.read_text(encoding="utf-8")
    doerer = re.findall(r"CREATE FUNCTION (m44_\w+)", sql)
    assert len(doerer) >= 12, doerer
    for navn in doerer:
        for ord_ in ("send", "utsend", "kjor", "publiser", "attester",
                     "signer", "distribuer"):
            assert ord_ not in navn, navn

    # …OG INGEN `sendt`-KOLONNE noen kunne satt for å late som.
    assert "sendt BOOLEAN" not in sql
    assert "sendt_ts" not in sql

    from api.app import RUTESCOPE
    # `set`: samtykkestien har BÅDE en GET og en POST, og de er to
    # rader i RUTESCOPE. Porten måler hvilke STIER som finnes.
    mine = sorted({sti for _m, sti in RUTESCOPE
                   if sti.startswith("/v1/kampanje")})
    assert mine == [
        "/v1/kampanje",
        "/v1/kampanje/grense",
        "/v1/kampanje/kampanje",
        "/v1/kampanje/kampanje/{kampanje_id:uuid}/avlys",
        "/v1/kampanje/kampanje/{kampanje_id:uuid}/plan",
        "/v1/kampanje/mottaker",
        "/v1/kampanje/mottaker/{mottaker_id:uuid}/aktiv",
        "/v1/kampanje/mottaker/{mottaker_id:uuid}/samtykke",
        "/v1/kampanje/mottaker/{mottaker_id:uuid}/samtykke/{dag:str}",
    ], mine


def test_flaten_sender_ingenting():
    kilde = FLATE.read_text(encoding="utf-8")
    uten = re.sub(r"^\s*//.*$", "", kilde, flags=re.M)
    for ord_ in ("fetch(", "XMLHttpRequest", "mailto:", "sendBeacon"):
        assert ord_ not in uten, f"flaten bærer «{ord_}»"
    api = (ROT / "platform" / "core" / "ui" / "static" / "js"
           / "api.js").read_text(encoding="utf-8")
    assert not re.search(
        r"export const (sendKampanje|utsendKampanje)", api)
    for n in ("settKampanjegrense", "registrerKampanjemottaker",
              "registrerSamtykke", "registrerKampanje",
              "avlysKampanje", "leggIKampanjeplan",
              "settKampanjemottakerAktiv"):
        i = api.index(f"export const {n} =")
        j = api.find("\n\n", i)
        kropp = api[i:j if j != -1 else len(api)]
        assert "idem || nyIdempotensnokkel()" in kropp, n


# ---------------------------------------------------------------------------
# INVARIANT 2: modulen_signerte_attestasjon
# ---------------------------------------------------------------------------

@pg
def test_invariant_modulen_signerte_attestasjon(migrator):
    """Malen betror `v_samtykke` (M-30) vilkårene `samtykke_gyldig` og
    `avmeldingslenke`. M-44 gir GRUNNLAGET for dem og attesterer
    ingen."""
    kolonner = migrator.execute(
        "SELECT table_name, column_name"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND (column_name LIKE '%%attest%%'"
        "        OR column_name LIKE '%%signat%%'"
        "        OR column_name LIKE '%%gyldig_samtykke%%')",
        (list(EGNE),)).fetchall()
    migrator.rollback()
    assert kolonner == [], kolonner
    sql = _bare_kode(MIGRASJON, uten_strenger=True).lower()
    for ord_ in ("attestasjon", "attester", "signatur", "signer"):
        assert ord_ not in sql, ord_
    i = sql.index("create function m44_evidens(")
    assert "revisjonslogg" in sql[i:sql.index("end $$;", i)]


# ---------------------------------------------------------------------------
# INVARIANT 3: mottaker_uten_samtykke
# ---------------------------------------------------------------------------

@pg
def test_invariant_mottaker_uten_samtykke(migrator):
    """EN PLANLAGT MOTTAKER UTEN SAMTYKKE ER ET FUNN.

    …og bare en PLANLAGT. En mottaker ingen har tenkt å sende til er
    ikke et problem, og et funnregister som listet hver kunde uten
    nyhetsbrevsamtykke ville vært ubrukelig fra første natt.
    """
    tenant = _tenantnavn("utensamtykke")
    c = _rt()
    try:
        _grense(c, tenant)
        uten_plan, _ = _mottaker(c, tenant, ref="UTEN-PLAN")
        i_plan, _ = _mottaker(c, tenant, ref="I-PLAN")
        med_ja, _ = _mottaker(c, tenant, ref="MED-JA")
        _samtykke(c, tenant, med_ja, "gitt", "2026-08-01")
        kid = _kampanje(c, tenant, dato="2026-08-10")
        _plan(c, tenant, kid, i_plan)
        _plan(c, tenant, kid, med_ja)
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_kampanje_eier")
    funn = {}
    for ref, ft in migrator.execute(
            "SELECT mo.ekstern_ref, k.funntype FROM"
            " m44_funnkandidater(%s,'2026-09-03'::date) k JOIN"
            " kampanjemottaker mo ON mo.tenant=%s AND"
            " mo.mottaker_id=k.mottaker_id ORDER BY 1,2",
            (tenant, tenant)).fetchall():
        funn.setdefault(ref, []).append(ft)
    migrator.rollback()
    assert funn.get("I-PLAN") == ["uten_samtykke"], funn
    # INGEN PLAN, INGEN SAK.
    assert "UTEN-PLAN" not in funn, funn
    assert "MED-JA" not in funn, funn


@pg
def test_kanalen_er_obligatorisk_og_lukket(migrator):
    """HVOR SAMTYKKET KOM FRA avgjør om det er et samtykke i det hele
    tatt. Et samtykke uten opphav er en påstand, og `samtykke_gyldig`
    ville hvilt på den."""
    kolonner = {r[0]: r[1] for r in migrator.execute(
        "SELECT column_name, is_nullable"
        "  FROM information_schema.columns"
        " WHERE table_schema='public'"
        "   AND table_name='samtykkehendelse'").fetchall()}
    migrator.rollback()
    for felt in ("tilstand", "kanal", "kilde_ref", "formal",
                 "inntruffet", "notat", "registrert_av"):
        assert kolonner.get(felt) == "NO", felt

    tenant = _tenantnavn("kanal")
    c = _rt()
    try:
        _grense(c, tenant)
        mid, _ = _mottaker(c, tenant)
        for kanal in ("gjetning", "", None):
            _sett_kontekst(c, tenant)
            with pytest.raises(psycopg.Error):
                c.execute(
                    "SELECT m44_registrer_samtykke(%s,%s,%s,'gitt',%s,"
                    "'r','nyhetsbrev','2026-08-01','x','u')",
                    (tenant, uuid.uuid4(), mid, kanal))
            c.rollback()
        for tilstand in ("kanskje", "", None):
            _sett_kontekst(c, tenant)
            with pytest.raises(psycopg.Error):
                c.execute(
                    "SELECT m44_registrer_samtykke(%s,%s,%s,%s,"
                    "'kasse','r','nyhetsbrev','2026-08-01','x','u')",
                    (tenant, uuid.uuid4(), mid, tilstand))
            c.rollback()
        # SAMME KILDEHENDELSE ÉN GANG: en preferanseside som postes to
        # ganger er ikke to samtykker.
        _samtykke(c, tenant, mid, "gitt", "2026-08-01", kanal="kasse",
                  kilde_ref="ref1")
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m44_registrer_samtykke(%s,%s,%s,'gitt',"
                "'kasse','ref1','nyhetsbrev','2026-08-02','x','u')",
                (tenant, uuid.uuid4(), mid))
        assert "kilde_unik" in str(ei.value)
        c.rollback()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# INVARIANT 4: kampanje_uten_avmeldingslenke
# ---------------------------------------------------------------------------

@pg
def test_invariant_kampanje_uten_avmeldingslenke(migrator):
    """EN KAMPANJE UTEN AVMELDINGSLENKE KAN IKKE REGISTRERES.

    Ikke fordi v1 sender — men fordi en kampanje som ikke KUNNE vært
    sendt lovlig heller ikke skal kunne stå i registeret som om den var
    klar.

    OG DEN MÅ VÆRE `https://`: en avmeldingslenke over ukryptert
    forbindelse lekker at mottakeren fikk kampanjen — til alle som ser
    trafikken.

    MUTASJONEN SOM DREPER DENNE: gjør `avmeldingslenke` NULLbar.
    """
    kolonner = {r[0]: r[1] for r in migrator.execute(
        "SELECT column_name, is_nullable"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name='kampanje'"
        ).fetchall()}
    migrator.rollback()
    assert kolonner.get("avmeldingslenke") == "NO"

    tenant = _tenantnavn("avmelding")
    c = _rt()
    try:
        _grense(c, tenant)
        for lenke in ("http://x.example/av", "", "   ", None,
                      "ikke en url", "https://", "https://a b"):
            _sett_kontekst(c, tenant)
            with pytest.raises(psycopg.Error) as ei:
                c.execute(
                    "SELECT m44_registrer_kampanje(%s,%s,'X','N',"
                    "'salg',%s,'2026-08-10','u')",
                    (tenant, uuid.uuid4(), lenke))
            assert "avmeldingslenken" in str(ei.value) \
                or "avmelding" in str(ei.value), (lenke, ei.value)
            c.rollback()
        kid = _kampanje(c, tenant, lenke="https://x.example/av?k=1")
    finally:
        c.close()
    # …OG DEN KAN IKKE FJERNES I ETTERTID.
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "UPDATE kampanje SET avmeldingslenke='https://annen'"
            " WHERE tenant=%s AND kampanje_id=%s", (tenant, kid))
    assert "FROSSET" in str(ei.value)
    migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 5: samtykkehistorikk_overskrevet
# ---------------------------------------------------------------------------

@pg
def test_invariant_samtykkehistorikk_overskrevet(migrator):
    """«HADDE VI LOV DEN DAGEN» MÅ KUNNE BESVARES I ETTERTID.

    Det finnes ingen `samtykke BOOLEAN`-kolonne noe sted; den gjeldende
    tilstanden ER den siste hendelsen. En avmelding er en HENDELSE man
    registrerer, ikke en rad man sletter.
    """
    rader = migrator.execute(
        "SELECT table_name, column_name"
        "  FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name = ANY(%s)"
        "   AND column_name IN ('samtykke','har_samtykke',"
        "                       'gjeldende_samtykke')",
        (list(EGNE),)).fetchall()
    migrator.rollback()
    assert rader == [], rader

    tenant = _tenantnavn("historikk")
    c = _rt()
    try:
        _grense(c, tenant)
        mid, _ = _mottaker(c, tenant)
        _samtykke(c, tenant, mid, "gitt", "2026-01-10", kanal="kasse")
        _samtykke(c, tenant, mid, "trukket", "2026-06-01",
                  kanal="avmeldingslenke")
        _sett_kontekst(c, tenant)
        rader = c.execute(
            "SELECT tilstand, kanal, endret FROM"
            " m44_samtykkehistorikken(%s,%s,200)",
            (tenant, mid)).fetchall()
        c.rollback()
        # SVARET PÅ «HADDE VI LOV DEN DAGEN» er forskjellig for to
        # forskjellige dager, og det er hele poenget.
        _sett_kontekst(c, tenant)
        for dag, fasit in (("2025-12-31", None), ("2026-03-01", "gitt"),
                           ("2026-08-01", "trukket")):
            rad = c.execute(
                "SELECT tilstand FROM m44_samtykke_paa_dato(%s,%s,"
                "%s::date)", (tenant, mid, dag)).fetchone()
            assert (rad[0] if rad else None) == fasit, (dag, rad)
        c.rollback()
    finally:
        c.close()
    assert rader == [("trukket", "avmeldingslenke", True),
                     ("gitt", "kasse", False)], rader

    rettigheter = migrator.execute(
        "SELECT DISTINCT privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_kampanje_eier'"
        "   AND table_name='samtykkehendelse' ORDER BY 1").fetchall()
    migrator.rollback()
    assert [r[0] for r in rettigheter] == ["INSERT", "SELECT"]
    for sql, ord_ in (("UPDATE samtykkehendelse SET tilstand='gitt'",
                       "FROSSET"),
                      ("DELETE FROM samtykkehendelse",
                       "DELETE avvist")):
        _sett_kontekst(migrator, tenant)
        with pytest.raises(psycopg.Error) as ei, migrator.transaction():
            migrator.execute(sql + " WHERE tenant=%s", (tenant,))
        assert ord_ in str(ei.value), sql
        migrator.rollback()


@pg
def test_en_avmelding_tas_alltid_imot(migrator):
    """OGSÅ FRA EN DEAKTIVERT MOTTAKER.

    Å nekte den ville vært å nekte noen å trekke samtykket sitt — og
    det er ikke en tilstand registeret får lov å ha.

    MUTASJONEN SOM DREPER DENNE: nekt alle hendelser fra en deaktivert
    mottaker.
    """
    tenant = _tenantnavn("avmelding-alltid")
    c = _rt()
    try:
        _grense(c, tenant)
        mid, _ = _mottaker(c, tenant)
        _samtykke(c, tenant, mid, "gitt", "2026-01-10")
        _sett_kontekst(c, tenant)
        c.execute("SELECT m44_sett_mottakeraktiv(%s,%s,false,'u')",
                  (tenant, mid))
        c.commit()
        # ET NYTT SAMTYKKE NEKTES…
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m44_registrer_samtykke(%s,%s,%s,'gitt',"
                "'kasse','r2','nyhetsbrev','2026-08-01','x','u')",
                (tenant, uuid.uuid4(), mid))
        assert "deaktivert" in str(ei.value)
        c.rollback()
        # …MEN EN AVMELDING TAS IMOT.
        _samtykke(c, tenant, mid, "trukket", "2026-08-02",
                  kanal="avmeldingslenke")
        _sett_kontekst(c, tenant)
        rad = c.execute(
            "SELECT tilstand FROM m44_samtykke_paa_dato(%s,%s,"
            "'2026-08-03'::date)", (tenant, mid)).fetchone()
        c.rollback()
    finally:
        c.close()
    assert rad[0] == "trukket"


@pg
def test_en_framtidig_dato_kan_ikke_skjule_et_samtykkeskifte(migrator):
    """110-113s lærdom, gjentatt: sveipen måler mot i dag."""
    tenant = _tenantnavn("framtid")
    c = _rt()
    try:
        _grense(c, tenant)
        mid, _ = _mottaker(c, tenant)
        _samtykke(c, tenant, mid, "gitt", "2026-01-10")
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute(
                "SELECT m44_registrer_samtykke(%s,%s,%s,'trukket',"
                "'kasse','f','nyhetsbrev',current_date + 1,'x','u')",
                (tenant, uuid.uuid4(), mid))
        assert "framtida" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    n = migrator.execute(
        "SELECT count(*) FROM samtykkehendelse WHERE tenant=%s",
        (tenant,)).fetchone()[0]
    migrator.rollback()
    assert n == 1


# ---------------------------------------------------------------------------
# Kontaktpunktet
# ---------------------------------------------------------------------------

@pg
def test_kontaktpunktet_lagres_aldri_i_klartekst(migrator):
    """M-41/M-42s form (110/111), her på en e-postadresse.

    Registeret trenger å kunne SKILLE to mottakere, ikke å kjenne
    adressen. Maske og per-rad-saltet hash holder.
    """
    tenant = _tenantnavn("kontakt")
    c = _rt()
    try:
        _grense(c, tenant)
        a, maske_a = _mottaker(c, tenant, ref="A", kontakt=KONTAKT)
        # SAMME ADRESSE, ANNEN SKRIVEMÅTE.
        b, maske_b = _mottaker(c, tenant, ref="B",
                               kontakt="  kari.nordmann@example.com ")
    finally:
        c.close()
    assert maske_a == "k****@example.com", maske_a
    assert maske_a == maske_b
    _sett_kontekst(migrator, tenant)
    rader = migrator.execute(
        "SELECT ekstern_ref, kontakt_maske, kontakt_hash"
        "  FROM kampanjemottaker WHERE tenant=%s ORDER BY ekstern_ref",
        (tenant,)).fetchall()
    migrator.rollback()
    for _ref, maske, hash_ in rader:
        assert "nordmann" not in maske.lower()
        assert re.fullmatch(r"[0-9a-f]{64}", hash_)
    # TO MOTTAKERE, SAMME ADRESSE → FORSKJELLIG hash (per-rad-salt).
    assert rader[0][2] != rader[1][2]
    # …og ADRESSEN STÅR INGEN STEDER, heller ikke i revisjonsloggen.
    _sett_kontekst(migrator, tenant)
    for kilde, uttrykk in (
            ("kampanjemottaker", "kontakt_maske || kontakt_hash || navn"),
            ("revisjonslogg", "coalesce(handling,'')"
                              " || coalesce(begrunnelse::text,'')")):
        treff = migrator.execute(
            f"SELECT count(*) FROM public.{kilde}"
            f" WHERE tenant=%s AND ({uttrykk}) ILIKE %s",
            (tenant, "%nordmann%")).fetchone()[0]
        assert treff == 0, kilde
    migrator.rollback()
    # …og SALTET ER FROSSET.
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "UPDATE kampanjemottaker SET hash_salt='a'||hash_salt"
            " WHERE tenant=%s", (tenant,))
    assert "FROSSET" in str(ei.value)
    migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 6 og 7: frekvensgrense_hardkodet /
#                   over_frekvensgrense_uten_funn
# ---------------------------------------------------------------------------

def test_invariant_frekvensgrense_hardkodet():
    """MALEN FORESLÅR 2 PER UKE — men et forslag i en bransjemal er
    ikke en grense noen tenant har vedtatt.

    MUTASJONEN SOM DREPER DENNE: `MAKS_PER_UKE = 2` i sveipen.
    """
    for fil in MODULFILER:
        for m in re.finditer(
                r"^([A-Z_]*(?:GRENSE|TAK|MAKS|PERIODE|FREKVENS)"
                r"[A-Z_]*)\s*=\s*(\d+)", _bare_kode(fil), re.M):
            # `MAKS_*` som gjelder SIDESTØRRELSE eller feltlengde er
            # ikke frekvensgrenser — de begrenser ett svar, ikke hvor
            # ofte noen kan kontaktes.
            assert m.group(1) in (
                "GRENSE", "GRENSER", "MAKS_MOTTAKERE", "MAKS_KAMPANJER",
                "MAKS_HISTORIKK", "MAKS_REF", "MAKS_NAVN",
                "MAKS_KONTAKT", "MAKS_LENKE", "MAKS_NOTAT"), \
                f"{fil.name} har grensekonstanten {m.group(1)}"
    from drift import kampanjesveip
    assert kampanjesveip.GRENSE == 500
    kode = _bare_kode(MIGRASJON)
    assert "m44_sveip_kampanjer(p_grense INT DEFAULT 500)" in kode
    assert "FROM public.kampanjegrense" in kode
    from api.kampanje import grense_endepunkt
    doc = grense_endepunkt.__doc__ or ""
    assert "M-1" in doc and "gap" in doc.lower()


@pg
def test_invariant_over_frekvensgrense_uten_funn(migrator):
    """ET BRUDD PÅ TAKET ER ET FUNN, og GRENSETILFELLET ER PORTEN:
    nøyaktig på taket er ikke et funn, én over er det.

    FREKVENSEN MÅLES I ET GLIDENDE VINDU. Et fast kalendervindu ville
    sluppet gjennom to kampanjer på søndag og to på mandag — og det er
    nøyaktig det mønsteret en travel markedsavdeling produserer.

    MUTASJONEN SOM DREPER DENNE: bytt `>` mot `>=` i funndøren.
    """
    tenant = _tenantnavn("tak")
    c = _rt()
    try:
        _grense(c, tenant, maks=2, periode=7)
        paa, _ = _mottaker(c, tenant, ref="PAA")
        over, _ = _mottaker(c, tenant, ref="OVER")
        spredt, _ = _mottaker(c, tenant, ref="SPREDT")
        for m_ in (paa, over, spredt):
            _samtykke(c, tenant, m_, "gitt", "2026-08-01")
        # PAA: nøyaktig 2 i vinduet.
        for d in ("2026-08-10", "2026-08-12"):
            _plan(c, tenant, _kampanje(c, tenant, dato=d), paa)
        # OVER: 3 i vinduet.
        for d in ("2026-08-10", "2026-08-12", "2026-08-14"):
            _plan(c, tenant, _kampanje(c, tenant, dato=d), over)
        # SPREDT: 3, men fordelt over mer enn 7 døgn.
        for d in ("2026-08-01", "2026-08-10", "2026-08-20"):
            _plan(c, tenant, _kampanje(c, tenant, dato=d), spredt)
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_kampanje_eier")
    funn = {}
    for ref, ft, og, ant in migrator.execute(
            "SELECT mo.ekstern_ref, k.funntype, k.over_grense,"
            " k.antall_i_periode FROM m44_funnkandidater(%s,"
            " '2026-09-03'::date) k JOIN kampanjemottaker mo ON"
            " mo.tenant=%s AND mo.mottaker_id=k.mottaker_id"
            " ORDER BY 1,2", (tenant, tenant)).fetchall():
        funn.setdefault(ref, []).append((ft, og, ant))
    migrator.rollback()
    # NØYAKTIG PÅ TAKET er IKKE et funn.
    assert funn.get("PAA") is None, funn
    # ÉN OVER er det.
    assert funn.get("OVER") == [("over_frekvensgrense", 1, 3)], funn
    # SPREDT UT er innafor — det er dét det glidende vinduet måler.
    assert funn.get("SPREDT") is None, funn


@pg
def test_en_avlyst_kampanje_teller_ikke_mot_taket(migrator):
    """En avlyst kampanje går ikke, og skal ikke telle."""
    tenant = _tenantnavn("avlyst")
    c = _rt()
    try:
        _grense(c, tenant, maks=2, periode=7)
        mid, _ = _mottaker(c, tenant)
        _samtykke(c, tenant, mid, "gitt", "2026-08-01")
        kids = []
        for d in ("2026-08-10", "2026-08-12", "2026-08-14"):
            k = _kampanje(c, tenant, dato=d)
            _plan(c, tenant, k, mid)
            kids.append(k)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert [f[0] for f in _funn(migrator, tenant) if f[3]] == \
        ["over_frekvensgrense"]
    c = _rt()
    try:
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT m44_avlys_kampanje(%s,%s,'u')",
                         (tenant, kids[2])).fetchone()[0] is True
        c.commit()
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT m44_avlys_kampanje(%s,%s,'u')",
                         (tenant, kids[2])).fetchone()[0] is False
        c.commit()
        # …OG EN AVLYST KAMPANJE TAR IKKE IMOT FLERE MOTTAKERE.
        annen, _ = _mottaker(c, tenant, ref="ANNEN")
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m44_legg_i_plan(%s,%s,%s,'u')",
                      (tenant, kids[2], annen))
        assert "avlyst" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    # FUNNET ER LUKKET, MEN RADEN STÅR.
    assert [f[0] for f in _funn(migrator, tenant) if f[3]] == []
    assert len(_funn(migrator, tenant)) == 1


@pg
def test_doren_svarer_med_antallet_i_perioden(migrator):
    """Den som planlegger skal få vite det MED ÉN GANG, og ikke først
    når sveipen har gått natta etter."""
    tenant = _tenantnavn("svar")
    c = _rt()
    try:
        _grense(c, tenant, maks=2, periode=7)
        mid, _ = _mottaker(c, tenant)
        svar = []
        for d in ("2026-08-10", "2026-08-12", "2026-08-14"):
            svar.append(_plan(c, tenant, _kampanje(c, tenant, dato=d),
                              mid))
    finally:
        c.close()
    assert svar == [1, 2, 3], svar


@pg
def test_samtykke_trukket_og_utlopt_er_egne_funn(migrator):
    tenant = _tenantnavn("samtykkefunn")
    c = _rt()
    try:
        _grense(c, tenant, maks=99, periode=7, gyldig=365)
        trukket, _ = _mottaker(c, tenant, ref="TRUKKET")
        utlopt, _ = _mottaker(c, tenant, ref="UTLOPT")
        fersk, _ = _mottaker(c, tenant, ref="FERSK")
        _samtykke(c, tenant, trukket, "gitt", "2026-01-01")
        _samtykke(c, tenant, trukket, "trukket", "2026-02-01",
                  kanal="avmeldingslenke")
        _samtykke(c, tenant, utlopt, "gitt", "2024-01-01")
        _samtykke(c, tenant, fersk, "bekreftet", "2026-08-01")
        kid = _kampanje(c, tenant, dato="2026-08-20")
        for m_ in (trukket, utlopt, fersk):
            _plan(c, tenant, kid, m_)
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_kampanje_eier")
    funn = {}
    for ref, ft, og in migrator.execute(
            "SELECT mo.ekstern_ref, k.funntype, k.over_grense FROM"
            " m44_funnkandidater(%s,'2026-09-03'::date) k JOIN"
            " kampanjemottaker mo ON mo.tenant=%s AND"
            " mo.mottaker_id=k.mottaker_id ORDER BY 1,2",
            (tenant, tenant)).fetchall():
        funn.setdefault(ref, []).append((ft, og))
    migrator.rollback()
    assert funn.get("TRUKKET") == [("samtykke_trukket", 0)], funn
    # 2026-09-03 minus 2024-01-01 er 976 døgn (2024 er skuddår),
    # minus gyldighetsvinduet 365.
    assert funn.get("UTLOPT") == [("samtykke_utlopt", 611)], funn
    assert "FERSK" not in funn, funn


@pg
def test_en_tenant_uten_grense_er_et_funn(migrator):
    tenant = _tenantnavn("utengrense")
    c = _rt()
    try:
        _mottaker(c, tenant)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert [f[0] for f in _funn(migrator, tenant)] == ["ingen_grense"]


@pg
def test_grensene_versjoneres_og_kan_ikke_slettes(migrator):
    tenant = _tenantnavn("grenseversjon")
    c = _rt()
    try:
        assert _grense(c, tenant, maks=2) == 1
        assert _grense(c, tenant, maks=4) == 2
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute("DELETE FROM kampanjegrense WHERE tenant=%s",
                         (tenant,))
    assert "DELETE avvist" in str(ei.value)
    migrator.rollback()
    _sett_kontekst(migrator, tenant)
    with pytest.raises(psycopg.Error) as ei, migrator.transaction():
        migrator.execute(
            "UPDATE kampanjegrense SET maks_per_periode=1"
            " WHERE tenant=%s", (tenant,))
    assert "versjonen må øke" in str(ei.value)
    migrator.rollback()


# ---------------------------------------------------------------------------
# Sveipen
# ---------------------------------------------------------------------------

@pg
def test_sveipen_er_idempotent_og_lukker_uten_aa_slette(migrator):
    tenant = _tenantnavn("idempotens")
    c = _rt()
    try:
        _grense(c, tenant, maks=1, periode=7)
        mid, _ = _mottaker(c, tenant)
        _samtykke(c, tenant, mid, "gitt", "2026-08-01")
        for d in ("2026-08-10", "2026-08-12"):
            _plan(c, tenant, _kampanje(c, tenant, dato=d), mid)
    finally:
        c.close()
    with _sv() as v:
        rad1 = _sveip(v)
    assert rad1[1] >= 1, rad1
    with _sv() as v:
        rad2 = _sveip(v)
    assert rad2[1] == 0, "sveip nummer to skrev nye rader"
    assert rad2[2] >= 1, rad2


@pg
def test_oppdaterte_funn_akkumuleres_over_tenantene(migrator):
    """112s CodeRabbit-lærdom, portet FØR den kunne oppstå her."""
    a = _tenantnavn("akk-a")
    b = _tenantnavn("akk-b")
    for tenant, antall in ((a, 2), (b, 3)):
        c = _rt()
        try:
            for i in range(antall):
                _mottaker(c, tenant, ref=f"R{i}")
        finally:
            c.close()
    with _sv() as v:
        forste = _sveip(v)
    assert forste[1] >= 5, forste
    with _sv() as v:
        andre = _sveip(v)
    assert andre[1] == 0, andre
    assert andre[2] >= 5, \
        f"oppdaterte ble overskrevet per tenant: {andre}"


@pg
def test_sveipen_sender_ingenting_og_rorer_ingen_hendelse(migrator):
    """Målt på VIRKELIGHETEN: en full sveip endrer ikke ett radantall
    utenfor registeret — OG DEN RØRER INGEN SAMTYKKEHENDELSE."""
    tenant = _tenantnavn("sveip")
    c = _rt()
    try:
        _grense(c, tenant, maks=1, periode=7)
        mid, _ = _mottaker(c, tenant)
        _samtykke(c, tenant, mid, "gitt", "2026-08-01")
        for d in ("2026-08-10", "2026-08-12"):
            _plan(c, tenant, _kampanje(c, tenant, dato=d), mid)
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    for_bok = migrator.execute(
        "SELECT hendelse_id, tilstand, kanal FROM samtykkehendelse"
        " WHERE tenant=%s ORDER BY hendelse_id", (tenant,)).fetchall()
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
        "SELECT hendelse_id, tilstand, kanal FROM samtykkehendelse"
        " WHERE tenant=%s ORDER BY hendelse_id", (tenant,)).fetchall()
    migrator.rollback()
    assert for_bok == etter_bok, "sveipen rørte samtykkehistorikken"


@pg
def test_sveipen_nekter_kontekst(migrator):
    with _sv() as v:
        v.execute("SELECT set_config('disponit.tenant','t',false)")
        with pytest.raises(psycopg.Error) as ei:
            v.execute("SELECT * FROM m44_sveip_kampanjer(500)")
        assert "KRYSS-TENANT" in str(ei.value)
        v.rollback()


@pg
def test_sveiperollen_har_ingen_tabellrettigheter(migrator):
    if not KAMPANJESVEIP_DSN:
        pytest.skip("DISPONIT_TEST_KAMPANJESVEIP_DSN ikke satt")
    rader = migrator.execute(
        "SELECT table_name, privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_kampanjesveip'").fetchall()
    migrator.rollback()
    assert rader == [], rader


def test_sveipen_nekter_aa_starte_uten_egen_dsn(tmp_path, monkeypatch):
    from drift import kjor_kampanjesveip
    monkeypatch.delenv("DISPONIT_KAMPANJESVEIP_URL", raising=False)
    monkeypatch.setenv("DISPONIT_KAMPANJESVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.setattr("db.hemmeligheter.last_credentials", lambda: None)
    assert kjor_kampanjesveip.main() == 2


def test_arbeidernokkelen_er_modulens_egen():
    from drift import (adressesveip, betalingssveip, kampanjesveip,
                       kontovaktsveip, lagersveip, lonnssveip)
    nokler = [m.ARBEIDERNOKKEL for m in
              (adressesveip, betalingssveip, kontovaktsveip, lagersveip,
               lonnssveip)]
    assert kampanjesveip.ARBEIDERNOKKEL not in nokler


# ---------------------------------------------------------------------------
# INVARIANT 8: tenantlekkasje_i_kampanjeregister
# ---------------------------------------------------------------------------

@pg
def test_invariant_tenantlekkasje_direkte(migrator):
    a = _tenantnavn("lekk-a")
    b = _tenantnavn("lekk-b")
    c = _rt()
    try:
        _grense(c, a)
        _mottaker(c, a)
        _grense(c, b)
        _sett_kontekst(c, b)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT * FROM m44_kampanjestatus(%s)", (a,))
        assert "tenantkontekst" in str(ei.value)
        c.rollback()
        _sett_kontekst(c, a)
        assert c.execute("SELECT * FROM m44_kampanjestatus(%s)",
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
    rader = migrator.execute(
        "SELECT tablename, cmd, roles::text, qual FROM pg_policies"
        " WHERE schemaname='public' AND policyname LIKE 'm44_%'"
        " ORDER BY 1").fetchall()
    migrator.rollback()
    assert len(rader) == 1, rader
    tabell, cmd, roller, qual = rader[0]
    assert tabell == "kampanjemottaker"
    assert cmd == "SELECT"
    assert roller == "{disponit_kampanje_eier}"
    assert "disponit.tenant" in qual and "IS NULL" in qual.upper()


@pg
def test_invariant_tenantlekkasje_over_api(migrator, klient):
    fremmed = _tenantnavn("fremmed")
    egen = "EGEN-" + secrets.token_hex(4)
    fremmed_ref = "FREMMED-" + secrets.token_hex(4)
    c = _rt()
    try:
        _grense(c, TENANT)
        _mottaker(c, TENANT, ref=egen)
        _grense(c, fremmed)
        _mottaker(c, fremmed, ref=fremmed_ref)
    finally:
        c.close()
    cookie, _csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/kampanje", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    kropp = json.dumps(r.json())
    assert egen in kropp
    assert fremmed_ref not in kropp


@pg
def test_ingen_av_tabellene_kan_tommes(migrator):
    tenant = _tenantnavn("truncate")
    c = _rt()
    try:
        _grense(c, tenant, maks=1)
        mid, _ = _mottaker(c, tenant)
        _samtykke(c, tenant, mid, "gitt", "2026-08-01")
        for d in ("2026-08-10", "2026-08-12"):
            _plan(c, tenant, _kampanje(c, tenant, dato=d), mid)
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


def test_skrivedorene_som_leser_en_tilstand_laser_raden():
    """…OG DE LÅSER EN RAD DE HAR UPDATE PÅ.

    `SELECT ... FOR UPDATE` krever UPDATE-retten, og `samtykkehendelse`
    og `kampanjeplan` har den ikke — begge er frosset. Dørene låser
    derfor MOTTAKEREN eller KAMPANJEN (M-42s lærdom, 110, og 112s
    gjentakelse).
    """
    sql = _bare_kode(MIGRASJON)
    for doer in ("m44_registrer_samtykke", "m44_sett_mottakeraktiv",
                 "m44_legg_i_plan", "m44_avlys_kampanje"):
        i = sql.index(f"CREATE FUNCTION {doer}(")
        kropp = sql[i:sql.index("END $$;", i)]
        assert "FOR UPDATE" in kropp, doer
        for m in re.finditer(r"FOR UPDATE", kropp):
            start = kropp.rfind("SELECT", 0, m.start())
            assert start != -1, doer
            setning = kropp[start:m.end()]
            assert ("public.kampanjemottaker" in setning
                    or "public.kampanje\n" in setning
                    or "public.kampanje " in setning), (doer, setning)

    # `m44_legg_i_plan` MÅ LÅSE BEGGE (CodeRabbit, alvorlig og REELT).
    #
    # Uten låsen på KAMPANJEN kunne en samtidig `m44_avlys_kampanje`
    # committet mellom statuslesningen og innsettingen — og en mottaker
    # ville blitt stående i planen til en avlyst kampanje.
    # `kampanjeplan` er append-only, så raden kunne ikke fjernes igjen.
    #
    # MUTASJONEN SOM DREPER DENNE: fjern `FOR UPDATE` fra
    # kampanjeoppslaget i `m44_legg_i_plan`.
    i = sql.index("CREATE FUNCTION m44_legg_i_plan(")
    kropp = sql[i:sql.index("END $$;", i)]
    laaste = set()
    for m in re.finditer(r"FOR UPDATE", kropp):
        setning = kropp[kropp.rfind("SELECT", 0, m.start()):m.end()]
        if "public.kampanjemottaker" in setning:
            laaste.add("mottaker")
        elif "public.kampanje" in setning:
            laaste.add("kampanje")
    assert laaste == {"mottaker", "kampanje"}, laaste
    # …OG REKKEFØLGEN ER MOTTAKER FØR KAMPANJE. Ingen annen dør låser
    # dem motsatt vei, så det er ingen vranglås — men rekkefølgen må
    # være målt, ikke antatt.
    assert (kropp.index("public.kampanjemottaker")
            < kropp.index("public.kampanje\n     WHERE")), kropp[:200]


# ---------------------------------------------------------------------------
# INVARIANT 9: ui_axe_alvorlige_brudd
# ---------------------------------------------------------------------------

def test_invariant_ui_axe_bor_i_js_suiten():
    fil = (ROT / "platform" / "core" / "ui" / "test"
           / "kampanje.test.js")
    assert fil.exists(), "kampanje.test.js mangler"
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
        " ('https://m44.test', %s) RETURNING bruker_id",
        ("s44-" + secrets.token_hex(6),)).fetchone()[0]
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
@dekker("kampanje_ulovlig_tilstand")
def test_http_samtykke_i_framtida_er_409(migrator, klient):
    """FEILVEIEN `kampanje_ulovlig_tilstand`, ende til ende.

    Dette er også testen `test_api_porter.test_hver_feilvei_har_en_test`
    krever for den nye feilveien.
    """
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _hpost(klient, cookie, csrf, "/v1/kampanje/grense",
               {"maks_per_periode": 2, "periode_dogn": 7,
                "samtykke_gyldig_dogn": 730})
    assert r.status_code in (200, 201), r.text
    ref = "H-" + secrets.token_hex(4)
    r = _hpost(klient, cookie, csrf, "/v1/kampanje/mottaker",
               {"ekstern_ref": ref, "navn": "HTTP Kunde",
                "kontakt": KONTAKT})
    assert r.status_code in (200, 201), r.text
    mid = r.json()["mottaker_id"]
    # SVARET ER MASKEN — aldri adressen.
    assert r.json()["kontakt_maske"] == "k****@example.com"
    assert "nordmann" not in json.dumps(r.json()).lower()

    idem = secrets.token_urlsafe(24)
    kropp = {"tilstand": "gitt", "kanal": "kasse",
             "kilde_ref": "evt_h1", "formal": "nyhetsbrev",
             "inntruffet": "2026-08-01", "notat": "avkrysset"}
    r = _hpost(klient, cookie, csrf,
               f"/v1/kampanje/mottaker/{mid}/samtykke", kropp,
               idem=idem)
    assert r.status_code in (200, 201), r.text
    # SP-2: SAMME NØKKEL GIR IKKE TO SAMTYKKER.
    r2 = _hpost(klient, cookie, csrf,
                f"/v1/kampanje/mottaker/{mid}/samtykke", kropp,
                idem=idem)
    assert r2.status_code == 409, r2.text

    # FRAMTIDIG DATO: TILSTANDEN sier nei.
    from datetime import date, timedelta
    i_morgen = (date.today() + timedelta(days=1)).isoformat()
    r = _hpost(klient, cookie, csrf,
               f"/v1/kampanje/mottaker/{mid}/samtykke",
               {**kropp, "kilde_ref": "evt_h2",
                "inntruffet": i_morgen})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "kampanje_ulovlig_tilstand"
    # SAMME REFERANSE TO GANGER likeså.
    r = _hpost(klient, cookie, csrf, "/v1/kampanje/mottaker",
               {"ekstern_ref": ref, "navn": "Dublett",
                "kontakt": KONTAKT})
    assert r.status_code == 409, r.text

    # …og en ukjent kanal eller tilstand er 400: KROPPEN er feil.
    for felt, verdi in (("kanal", "brevdue"), ("kanal", None),
                        ("tilstand", "kanskje"), ("formal", "")):
        r = _hpost(klient, cookie, csrf,
                   f"/v1/kampanje/mottaker/{mid}/samtykke",
                   {**kropp, "kilde_ref": "evt_x", felt: verdi})
        assert r.status_code == 400, (felt, verdi, r.text)

    # AVMELDINGSLENKEN: `http://` er 400, ikke 409 — det er KROPPEN.
    kref = "KH-" + secrets.token_hex(4)
    for lenke in ("http://x.example/av", "", "ikke en url",
                  "https://", "https://a b"):
        r = _hpost(klient, cookie, csrf, "/v1/kampanje/kampanje",
                   {"ekstern_ref": kref, "navn": "N", "formal": "salg",
                    "avmeldingslenke": lenke,
                    "planlagt_sendt": "2026-08-10"})
        assert r.status_code == 400, (lenke, r.text)
    r = _hpost(klient, cookie, csrf, "/v1/kampanje/kampanje",
               {"ekstern_ref": kref, "navn": "N", "formal": "salg",
                "avmeldingslenke": "https://x.example/av",
                "planlagt_sendt": "2026-08-10"})
    assert r.status_code in (200, 201), r.text
    kid = r.json()["kampanje_id"]

    # PLANEN SVARER MED ANTALLET I PERIODEN.
    r = _hpost(klient, cookie, csrf,
               f"/v1/kampanje/kampanje/{kid}/plan",
               {"mottaker_id": mid})
    assert r.status_code in (200, 201), r.text
    assert r.json()["i_periode"] == 1
    # `aktiv` ER PÅKREVD.
    r = _hpost(klient, cookie, csrf,
               f"/v1/kampanje/mottaker/{mid}/aktiv", {})
    assert r.status_code == 400, r.text

    # HISTORIKKEN ER BEVISET, og den bærer kanalen.
    r = klient.get(f"/v1/kampanje/mottaker/{mid}/samtykke",
                   cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    linjer = r.json()["hendelser"]
    assert len(linjer) == 1
    assert linjer[0]["kanal"] == "kasse"
    assert linjer[0]["formal"] == "nyhetsbrev"

    # …OG «HADDE VI LOV DEN DAGEN» HAR SITT EGET SVAR.
    r = klient.get(f"/v1/kampanje/mottaker/{mid}/samtykke/2025-01-01",
                   cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    assert r.json()["samtykke"] is None
    r = klient.get(f"/v1/kampanje/mottaker/{mid}/samtykke/2026-08-05",
                   cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    assert r.json()["samtykke"]["tilstand"] == "gitt"


@pg
def test_http_lesingen_bruker_okonomi_read(migrator, klient):
    from api.app import RUTESCOPE
    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert RUTESCOPE[("GET", "/v1/kampanje")] == "okonomi:read"
    assert "okonomi:read" in ROLLE_TIL_SCOPES["admin"]
    for rolle in ("leser", "sikkerhet", "godkjenner"):
        assert "okonomi:read" not in ROLLE_TIL_SCOPES[rolle], rolle
    cookie, _c = _browserokt(migrator, ["leser"])
    r = klient.get("/v1/kampanje", cookies={_C_SESJON(): cookie})
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# §0
# ---------------------------------------------------------------------------

def test_grensen_ble_registrert_for_koden():
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m44-v1"]
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["punktbinding"] == {}
    egen = Path(__file__).read_text(encoding="utf-8")
    for inv in g["invarianter"]:
        assert inv in egen, f"invarianten {inv} har ingen port"
