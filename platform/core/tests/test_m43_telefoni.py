"""M-43 tale- og telefoniagent v1 (135) — KLYNGE 9s TREDJE.

Grensen `m43-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

KLYNGENS DELTE DOM, OG HER ER DEN BOKSTAVELIG:

  EN YTRING AVGITT I HUSETS NAVN KAN IKKE TAS TILBAKE — OG DEN SOM
  LESER DEN VET IKKE AT EN MASKIN SKREV DEN.

Den andre parten HØRER en stemme, og en stemme høres ikke ut som en
maskin lenger. Den som tror hun snakker med et menneske, svarer
annerledes: hun sier ting hun ikke ville sagt til et system.

DEN TYNGSTE GRUPPEN PORTER MÅLER REKKEFØLGE, IKKE TILSTAND. Kolonnen
`identifisert_ts` sier AT vi identifiserte oss; `m43_registrer_linje`
sier at INGENTING BLE SAGT FØR VI SA HVA VI ER. Begge trengs: en
kolonne alene kunne stått med et tidspunkt ingen linje respekterte.

FIRE PORTER MÅLER ET FRAVÆR SOM ER ET BEVIS: `opptak_uten_hjemmel`,
`opptak_uten_varsling`, `agenten_skjulte_at_den_er_automatisert` og
`eskalering_uten_regel` står i funntypesettet OG kan aldri reises.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import ast
import contextlib
import datetime as dt
import os
import re
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN  # noqa: F401

TELEFONISVEIP_DSN = os.environ.get("DISPONIT_TEST_TELEFONISVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "135_m43_telefoni.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "telefoni.js")
FUNDAMENT = ROT / "docs" / "KLYNGE9-FUNDAMENT.md"
MODULFILER = (
    ROT / "platform" / "core" / "api" / "telefoni.py",
    ROT / "platform" / "drift" / "telefonisveip.py",
    ROT / "platform" / "drift" / "kjor_telefonisveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

#: TABELLENE MODULEN EIER. `opptakshjemmel` STÅR IKKE HER, og det er
#: hele arven: den er M-7s (133) og de to modulene deler den.
EGNE = ("telefonikrav", "eskaleringsregel", "samtale", "samtaleopptak",
        "transkripsjonslinje", "eskalering", "telefonifunn")

_STRENG = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")


def _bare_kode(fil: Path, *, uten_strenger: bool = False) -> str:
    """Filens innhold uten kommentarer OG uten docstrings (m24-formen).

    PORTEN SKAL MÅLE HANDLINGEN, IKKE BEGRUNNELSEN. Kommentarene her
    er fulle av ordene «avtale» og «løfte», nettopp fordi modulen ikke
    gir noen av delene.
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
    return koble(TELEFONISVEIP_DSN or MIGRATOR_DSN)


def _sett_kontekst(conn, tenant):
    conn.execute("SELECT set_config('disponit.tenant', %s, false)",
                 (tenant,))


def _tenantnavn(merke: str) -> str:
    return f"t-m43-{merke}-{secrets.token_hex(4)}"


def _naa(**kw):
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(**kw)


I_DAG = dt.date.today()


def _nektes(mg, t, sql, args, *, teller_sql, teller_args):
    """EN VAKT SOM IKKE FÅR NOE Å BITE I, BITER IKKE.

    `set_config('disponit.tenant', ..., false)` settes inne i en
    transaksjon og RULLES TILBAKE MED DEN. En port som gjør
    `rollback()` og deretter prøver neste setning uten å sette
    konteksten på nytt, treffer NULL RADER under FORCE RLS: ingen
    trigger fyrer, og porten er grønn uten å ha prøvd vakten. 134s
    lærdom, målt her.
    """
    _sett_kontekst(mg, t)
    synlig = mg.execute(teller_sql, teller_args).fetchone()[0]
    assert synlig == 1, (
        f"porten ville vaert tom: {synlig} rader synlige")
    with pytest.raises(psycopg.errors.RaiseException):
        mg.execute(sql, args)
    mg.rollback()


# =====================================================================
# BYGGEKLOSSER.
# =====================================================================

def _krav(rt, t, *, terskel=7000, identfrist=10, eskfrist=3, tak=24):
    _sett_kontekst(rt, t)
    v = rt.execute("SELECT m43_sett_krav(%s,%s,%s,%s,%s,%s)",
                   (t, terskel, identfrist, eskfrist, tak, "u-test")
                   ).fetchone()[0]
    rt.commit()
    return v


def _samtale(rt, t, *, ident_sek=4, start=None, motpart="+4790000000"):
    _sett_kontekst(rt, t)
    sid = uuid.uuid4()
    start = start or _naa()
    rt.execute("SELECT * FROM m43_start_samtale(%s,%s,%s,%s,%s,%s,%s,%s)",
               (t, sid, "inngaaende", motpart, start,
                start + dt.timedelta(seconds=ident_sek),
                "Hei, du snakker med en automatisk assistent", "u-test"))
    rt.commit()
    return sid, start


def _linje(rt, t, sid, start, *, nr=1, sikkerhet=9000, kilde="transkripsjon",
           retter=None, sek=8):
    _sett_kontekst(rt, t)
    lid = uuid.uuid4()
    rad = rt.execute(
        "SELECT * FROM m43_registrer_linje(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (t, lid, sid, nr, "motpart", start + dt.timedelta(seconds=sek),
         "Jeg lurer paa fakturaen", kilde, sikkerhet, retter,
         "u-test")).fetchone()
    rt.commit()
    return lid, rad


def _hjemmel(rt, t, *, gyldig_til=None, fra=None):
    _sett_kontekst(rt, t)
    hid = uuid.uuid4()
    rt.execute("SELECT m43_registrer_hjemmel(%s,%s,%s,%s,%s,%s,%s,%s)",
               (t, hid, "berettiget_interesse",
                "Opptak av kundesamtaler etter vedtak 12/24",
                "kvalitetssikring", fra or I_DAG, gyldig_til, "u-test"))
    rt.commit()
    return hid


def _regel(rt, t, *, gyldig_til=None, fra=None):
    _sett_kontekst(rt, t)
    rid = uuid.uuid4()
    rt.execute("SELECT m43_registrer_regel(%s,%s,%s,%s,%s,%s,%s)",
               (t, rid, "Sinte kunder gaar til vakthavende", "vakt@acme",
                fra or I_DAG, gyldig_til, "u-test"))
    rt.commit()
    return rid


def _eldre_samtale(mg, t, sid, *, timer):
    """SAMTALEN BLIR GAMMEL, OG DET KAN BARE TIDEN GJØRE.

    `m43_samtalevakt` fryser starttidspunktet, og det er riktig —
    derfor må vakten kobles ut for å fabrikkere tilstanden. Migrator
    eier tabellen og kan det; INGEN ANNEN KAN. At fabrikkeringen
    krever dette er selv en måling.
    """
    _sett_kontekst(mg, t)
    mg.execute("ALTER TABLE samtale DISABLE TRIGGER m43_samtalevakt")
    mg.execute("UPDATE samtale SET startet_ts = now()"
               " - make_interval(hours => %s),"
               " identifisert_ts = now() - make_interval(hours => %s)"
               " WHERE tenant=%s AND samtale_id=%s",
               (timer, timer, t, sid))
    mg.execute("ALTER TABLE samtale ENABLE TRIGGER m43_samtalevakt")
    mg.commit()


def _eldre_eskalering(mg, t, eid, *, dogn):
    _sett_kontekst(mg, t)
    mg.execute("ALTER TABLE eskalering DISABLE TRIGGER m43_eskaleringsvakt")
    mg.execute("UPDATE eskalering SET eskalert_ts = now()"
               " - make_interval(days => %s)"
               " WHERE tenant=%s AND eskalering_id=%s", (dogn, t, eid))
    mg.execute("ALTER TABLE eskalering ENABLE TRIGGER m43_eskaleringsvakt")
    mg.commit()


# =====================================================================
# §0: GRENSEN.
# =====================================================================

def test_grensen_er_registrert_og_hver_invariant_har_en_port():
    from manifestskjema import KRAVGRENSER
    grense = KRAVGRENSER["m43-v1"]
    assert grense["maks_brudd"] == 0
    tekst = Path(__file__).read_text(encoding="utf-8")
    uten = [i for i in grense["invarianter"] if i not in tekst]
    assert uten == [], f"invarianter uten port: {uten}"


def test_ui_axe_alvorlige_brudd_dekkes_av_flatens_egen_suite():
    js = ROT / "platform" / "core" / "ui" / "test" / "telefoni.test.js"
    assert js.exists(), "flatens egen suite mangler"
    tekst = js.read_text(encoding="utf-8")
    assert "axe" in tekst.lower() or "aria" in tekst.lower()


# =====================================================================
# `agenten_skjulte_at_den_er_automatisert` — MODULENS KJERNE.
# =====================================================================

@pg
def test_en_samtale_uten_identifikasjon_er_urepresenterbar():
    """`agenten_skjulte_at_den_er_automatisert` — OG DEN KAN ALDRI
    REISES.

    Ikke fordi sveipen er flink, men fordi `identifisert_ts` er NOT
    NULL. Den som tror hun snakker med et menneske, svarer annerledes.

    MUTASJONEN SOM DREPER DENNE: gjør kolonnen nullable.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("uidentifisert")
        _krav(rt, t)
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.NotNullViolation):
            mg.execute(
                "INSERT INTO samtale (tenant, samtale_id, retning,"
                " motpart, startet_ts, identifisert_ts,"
                " identifikasjonstekst, registrert_av)"
                " VALUES (%s,%s,'inngaaende','+47',now(),NULL,"
                " 'noe','u')", (t, uuid.uuid4()))
        mg.rollback()
        # …OG TEKSTEN ER PÅKREVD. «Agenten identifiserte seg» er en
        # påstand; teksten er hva den faktisk sa.
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.CheckViolation):
            mg.execute(
                "INSERT INTO samtale (tenant, samtale_id, retning,"
                " motpart, startet_ts, identifisert_ts,"
                " identifikasjonstekst, registrert_av)"
                " VALUES (%s,%s,'inngaaende','+47',now(),now(),"
                " '  ','u')", (t, uuid.uuid4()))
        mg.rollback()


@pg
def test_identifikasjonen_kan_ikke_dateres_for_samtalen():
    """EN IDENTIFIKASJON INGEN KUNNE HØRT ER INGEN IDENTIFIKASJON.

    Målt to steder: i CHECK-en, og i døra med en setning bak.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("bakover")
        _krav(rt, t)
        start = _naa()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute(
                "SELECT * FROM m43_start_samtale(%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), "inngaaende", "+47", start,
                 start - dt.timedelta(seconds=5), "Jeg er en maskin",
                 "u-test"))
        assert "foer samtalen startet" in str(e.value)
        rt.rollback()
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.CheckViolation):
            mg.execute(
                "INSERT INTO samtale (tenant, samtale_id, retning,"
                " motpart, startet_ts, identifisert_ts,"
                " identifikasjonstekst, registrert_av)"
                " VALUES (%s,%s,'inngaaende','+47',now(),"
                " now() - interval '1 minute','Jeg er en maskin','u')",
                (t, uuid.uuid4()))
        mg.rollback()


@pg
def test_identifikasjonen_maa_komme_innen_tenantens_egen_frist():
    """DEN SOM HAR SNAKKET I ETT MINUTT FØR HUN FÅR VITE HVA HUN
    SNAKKER MED, HAR ALLEREDE SVART SOM TIL ET MENNESKE.

    FRISTEN ER TENANTENS. En bestilling av pizza og en samtale om
    oppsigelse tåler ikke det samme, og et tall låst i modulen ville
    vært en påstand om at de gjør det.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("sent")
        _krav(rt, t, identfrist=5)
        start = _naa()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute(
                "SELECT * FROM m43_start_samtale(%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), "inngaaende", "+47", start,
                 start + dt.timedelta(seconds=6), "Jeg er en maskin",
                 "u-test"))
        assert "fristen er 5" in str(e.value)
        rt.rollback()
        # …OG PÅ FRISTEN ER LOV.
        _sett_kontekst(rt, t)
        rad = rt.execute(
            "SELECT * FROM m43_start_samtale(%s,%s,%s,%s,%s,%s,%s,%s)",
            (t, uuid.uuid4(), "inngaaende", "+47", start,
             start + dt.timedelta(seconds=5), "Jeg er en maskin",
             "u-test")).fetchone()
        rt.commit()
        assert rad[1] == 5
        del mg


@pg
def test_ingenting_ble_sagt_for_vi_sa_hva_vi_er():
    """DEN ANDRE HALVDELEN, OG DEN ER DEN SOM BETYR NOE.

    Kolonnen sier AT vi identifiserte oss. Denne døra sier at ingen
    linje kan dateres FØR den. Uten dette kunne `identifisert_ts` stått
    med et tidspunkt ingen linje respekterte — og da måler kolonnen
    ingenting.

    MUTASJONEN SOM DREPER DENNE: fjern sammenligningen
    `p_linje_ts < v_samtale.identifisert_ts` i `m43_registrer_linje`.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("forst")
        _krav(rt, t)
        sid, start = _samtale(rt, t, ident_sek=4)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute(
                "SELECT * FROM m43_registrer_linje"
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), sid, 1, "motpart",
                 start + dt.timedelta(seconds=1), "Hallo?",
                 "transkripsjon", 9000, None, "u-test"))
        assert "foer agenten sa hva den er" in str(e.value)
        rt.rollback()
        # PÅ IDENTIFIKASJONSTIDSPUNKTET ER LOV — det er agentens egen
        # første linje.
        lid, _ = _linje(rt, t, sid, start, sek=4)
        assert lid
        del mg


@pg
def test_identifikasjonen_er_frossen():
    """En identifikasjon som kunne flyttes i ettertid ville vært en
    identifikasjon som passet til linjene, ikke omvendt."""
    with _to() as (rt, mg):
        t = _tenantnavn("frossen")
        _krav(rt, t)
        sid, _ = _samtale(rt, t)
        for felt, verdi in (("identifisert_ts", "now() + interval '1 h'"),
                            ("identifikasjonstekst", "'noe annet'"),
                            ("startet_ts", "now()"),
                            ("motpart", "'+4799999999'")):
            _nektes(mg, t,
                    f"UPDATE samtale SET {felt}={verdi}"
                    " WHERE tenant=%s AND samtale_id=%s", (t, sid),
                    teller_sql="SELECT count(*) FROM samtale"
                               " WHERE tenant=%s AND samtale_id=%s",
                    teller_args=(t, sid))
        _nektes(mg, t,
                "DELETE FROM samtale WHERE tenant=%s AND samtale_id=%s",
                (t, sid),
                teller_sql="SELECT count(*) FROM samtale"
                           " WHERE tenant=%s AND samtale_id=%s",
                teller_args=(t, sid))


# =====================================================================
# V1-DOMMEN: INGEN AVTALE, INGEN ØKONOMISKE LØFTER.
# =====================================================================

@pg
def test_modulen_inngikk_avtale_er_umulig_i_datamodellen():
    """`modulen_inngikk_avtale`.

    MÅLT SOM ET FRAVÆR I SKJEMAET, ikke som en avslått kodevei. Det
    finnes ingen kolonne i modulen som binder noe: ingen status som
    heter «akseptert», ingen motpartssignatur, ingen bekreftelse.

    En transkripsjonslinje kan INNEHOLDE at noen sa «da er vi enige» —
    modulen NEDTEGNER det. Forskjellen på å nedtegne og å binde er at
    ingen annen del av huset leser `transkripsjonslinje` som en avtale,
    og at ingen dør her skriver noe annet sted.
    """
    with _mig() as mg:
        kolonner = [r[0] for r in mg.execute(
            "SELECT c.table_name || '.' || c.column_name"
            "  FROM information_schema.columns c"
            " WHERE c.table_name = ANY(%s)"
            "   AND (c.column_name ~ 'avtale|signatur|akseptert|"
            "bindende|samtykket')", (list(EGNE),)).fetchall()]
    assert kolonner == [], f"modulen har en bindende kolonne: {kolonner}"


@pg
def test_modulen_ga_okonomisk_lofte_er_umulig_i_datamodellen():
    """`modulen_ga_okonomisk_lofte`.

    INGEN KOLONNE BÆRER ET BELØP. Ikke i ører, ikke i kroner, ikke som
    en rabatt eller en pris. En modul som kunne lagre et tall med en
    valuta bak ville hatt et sted å legge løftet.
    """
    with _mig() as mg:
        kolonner = [r[0] for r in mg.execute(
            "SELECT c.table_name || '.' || c.column_name"
            "  FROM information_schema.columns c"
            " WHERE c.table_name = ANY(%s)"
            "   AND (c.column_name ~ 'belop|pris|rabatt|kroner|ore|"
            "sum|valuta|kompensasjon')", (list(EGNE),)).fetchall()]
    assert kolonner == [], f"modulen har en beloepskolonne: {kolonner}"


def test_ingen_kodevei_inngar_avtale_eller_lover_penger():
    """MÅLT I KODEN, IKKE BARE I SKJEMAET.

    KOMMENTARER OG STRENGER FJERNES FØRST (128s lærdom): filhodene her
    er fulle av ordene «avtale» og «løfte» nettopp fordi modulen ikke
    gjør noen av delene.
    """
    for fil in MODULFILER:
        if not fil.exists():
            continue
        kode = _bare_kode(fil, uten_strenger=True).lower()
        for forbudt in ("inngaa_avtale", "inngaaavtale", "gi_rabatt",
                        "lov_belop", "bekreft_avtale", "aksepter_tilbud"):
            assert forbudt not in kode, f"{fil.name}: {forbudt}"


@pg
def test_sveipen_ringer_ingen_og_lukker_ingen_eskalering():
    """SVEIPEN SIER FRA, OG DER STOPPER DEN.

    Målt mot rettighetene og mot funksjonskroppen: sveiperollen har
    EXECUTE på ÉN funksjon, og den skriver bare i funntabellen.
    """
    with _mig() as mg:
        naar = [r[0] for r in mg.execute(
            "SELECT p.proname FROM pg_proc p"
            " WHERE p.proname LIKE 'm43\\_%'"
            "   AND has_function_privilege('disponit_telefonisveip',"
            "                              p.oid, 'EXECUTE')").fetchall()]
        kropp = mg.execute(
            "SELECT pg_get_functiondef(p.oid) FROM pg_proc p"
            " WHERE p.proname = 'm43_sveip_telefoni'").fetchone()[0]
    assert sorted(naar) == ["m43_sveip_telefoni"]
    ren = _STRENG.sub("''", kropp)
    for tabell in ("samtale", "eskalering", "transkripsjonslinje",
                   "samtaleopptak", "eskaleringsregel"):
        for form in (f"public.{tabell}", tabell):
            assert f"INSERT INTO {form}" not in ren, (tabell, form)
            assert f"UPDATE {form}" not in ren, (tabell, form)


# =====================================================================
# OPPTAKET — 133s FIRE NEKT, ARVET.
# =====================================================================

@pg
def test_opptak_uten_hjemmel_er_urepresenterbart():
    """`opptak_uten_hjemmel` — OG DEN KAN ALDRI REISES.

    `hjemmel_id` er NOT NULL med fremmednøkkel til den DELTE hjemmelen
    fra 133. Å oppdage et ulovlig opptak i en nattlig sveip er å
    oppdage en skade, ikke å hindre den.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("utenhjemmel")
        _krav(rt, t)
        sid, _ = _samtale(rt, t)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute(
                "SELECT * FROM m43_start_opptak(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), sid, uuid.uuid4(), _naa(), "u-kari",
                 ["+47"], _naa(seconds=2), "u-test"))
        assert "ukjent opptakshjemmel" in str(e.value)
        rt.rollback()
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.NotNullViolation):
            mg.execute(
                "INSERT INTO samtaleopptak (tenant, opptak_id,"
                " samtale_id, hjemmel_id, varslet_ts, varslet_av,"
                " varslede, startet_ts, registrert_av)"
                " VALUES (%s,%s,%s,NULL,now(),'u',ARRAY['a'],now(),'u')",
                (t, uuid.uuid4(), sid))
        mg.rollback()


@pg
def test_opptak_uten_varsling_er_urepresenterbart():
    """`opptak_uten_varsling` — OG REKKEFØLGEN ER DOMMEN.

    ET NEKT SOM KOMMER ETTER MIKROFONEN ER IKKE ET NEKT. 133s
    `varsling_kom_forst`, arvet ordrett.

    MUTASJONEN SOM DREPER DENNE: snu ulikheten i
    `samtaleopptak_varsling_kom_forst`.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("utenvarsel")
        _krav(rt, t)
        sid, _ = _samtale(rt, t)
        hid = _hjemmel(rt, t)
        start = _naa()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute(
                "SELECT * FROM m43_start_opptak(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), sid, hid, start + dt.timedelta(seconds=30),
                 "u-kari", ["+47"], start, "u-test"))
        assert "etter at opptaket startet" in str(e.value)
        rt.rollback()
        # …OG TOM VARSLINGSLISTE ER OGSÅ ET NEKT.
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute(
                "SELECT * FROM m43_start_opptak(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), sid, hid, start, "u-kari", [], start,
                 "u-test"))
        assert "ingen er varslet" in str(e.value)
        rt.rollback()
        # …OG BASEN NEKTER FORBI DØRA.
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.CheckViolation):
            mg.execute(
                "INSERT INTO samtaleopptak (tenant, opptak_id,"
                " samtale_id, hjemmel_id, varslet_ts, varslet_av,"
                " varslede, startet_ts, registrert_av)"
                " VALUES (%s,%s,%s,%s,now() + interval '1 h','u',"
                " ARRAY['a'],now(),'u')", (t, uuid.uuid4(), sid, hid))
        mg.rollback()


@pg
def test_en_utloept_hjemmel_nektes_med_m7s_egen_regel():
    """EN UTLØPT HJEMMEL SER NØYAKTIG UT SOM EN GYLDIG — klynge 7s dom.

    OG REGELEN ER M-7s, IKKE EN KOPI. `m7_hjemmel_gyldig` er den eneste
    funksjonen som avgjør om en hjemmel gjelder; to funksjoner ville
    gitt to svar på «hadde vi lov».

    MUTASJONEN SOM DREPER DENNE: bytt kallet mot en lokal kopi av
    regelen.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("utloept")
        _krav(rt, t)
        sid, _ = _samtale(rt, t)
        hid = _hjemmel(rt, t, fra=I_DAG - dt.timedelta(days=30),
                       gyldig_til=I_DAG - dt.timedelta(days=1))
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute(
                "SELECT * FROM m43_start_opptak(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), sid, hid, _naa(), "u-kari", ["+47"],
                 _naa(seconds=2), "u-test"))
        assert "utloept" in str(e.value)
        rt.rollback()
        del mg
    # …OG DET ER M-7s FUNKSJON SOM KALLES.
    kode = _bare_kode(MIGRASJON)
    assert "m7_hjemmel_gyldig" in kode
    assert "m43_hjemmel_gyldig" not in kode, (
        "modulen har laget sin egen kopi av regelen")


@pg
def test_modulen_arver_hjemmelen_og_lager_ikke_et_nummer_to():
    """FUNDAMENTETS EGEN AVKLARING, MÅLT.

    «M-7 og M-43 deler ÉN opptakshjemmel, og den bygges i M-7s runde.»
    Porten faller hvis noen lager nummer to.
    """
    with _mig() as mg:
        egne = sorted(r[0] for r in mg.execute(
            "SELECT c.relname FROM pg_class c"
            " WHERE c.relnamespace='public'::regnamespace"
            "   AND c.relkind='r'"
            "   AND (c.relname LIKE '%telefoni%' OR c.relname LIKE"
            "        'samtale%' OR c.relname LIKE 'eskaler%'"
            "     OR c.relname='transkripsjonslinje')").fetchall())
        fk = mg.execute(
            "SELECT confrelid::regclass::text FROM pg_constraint"
            " WHERE conname='samtaleopptak_hjemmel_fk'").fetchone()
    assert egne == sorted(EGNE), egne
    assert fk == ("opptakshjemmel",), (
        "opptaket peker ikke paa husets delte hjemmel")


# =====================================================================
# TRANSKRIPSJONEN OG ESKALERINGEN.
# =====================================================================

@pg
def test_transkripsjon_uten_usikkerhet_er_urepresenterbar():
    """`transkripsjon_uten_usikkerhet`.

    En transkripsjon uten usikkerhet er en PÅSTAND OM AT MASKINEN HØRTE
    RIKTIG. `sikkerhet_bp` er NOT NULL, terskelen som gjaldt DA står på
    raden, og `ubekreftet` er bundet til de to av en CHECK.

    MUTASJONEN SOM DREPER DENNE: fjern
    `transkripsjonslinje_flagget_stemmer`.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("usikker")
        _krav(rt, t, terskel=7000)
        sid, start = _samtale(rt, t)
        _, rad = _linje(rt, t, sid, start, sikkerhet=4000)
        assert rad[1] == 7000 and rad[2] is True
        _, rad2 = _linje(rt, t, sid, start, nr=2, sikkerhet=9000)
        assert rad2[2] is False
        # ET MENNESKE SOM SKREV SELV, HØRTE IKKE FEIL.
        _, rad3 = _linje(rt, t, sid, start, nr=3, sikkerhet=1,
                         kilde="manuell")
        assert rad3[2] is False
        _sett_kontekst(mg, t)
        bp = mg.execute(
            "SELECT sikkerhet_bp FROM transkripsjonslinje"
            " WHERE tenant=%s AND kilde='manuell'", (t,)).fetchone()[0]
        mg.rollback()
        assert bp == 10000, "manuell linje ble ikke tvunget til full sikkerhet"
        # …OG EN LØGN OM MERKINGEN ER UMULIG.
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.CheckViolation):
            mg.execute(
                "INSERT INTO transkripsjonslinje (tenant, linje_id,"
                " samtale_id, rekkefolge, taler, linje_ts, tekst,"
                " kilde, sikkerhet_bp, terskel_bp, ubekreftet,"
                " registrert_av)"
                " VALUES (%s,%s,%s,9,'agent',now(),'x','transkripsjon',"
                " 100,7000,false,'u')", (t, uuid.uuid4(), sid))
        mg.rollback()


@pg
def test_terskelen_oppgis_aldri_av_kalleren():
    """EN KALLER SOM FIKK SETTE SIN EGEN TERSKEL KUNNE SATT DEN TIL 1
    OG FÅTT ALT BEKREFTET.

    Døra leser den fra tenantens krav. Målt på signaturen: det finnes
    ingen terskel-parameter.
    """
    with _mig() as mg:
        args = mg.execute(
            "SELECT pg_get_function_arguments(p.oid) FROM pg_proc p"
            " WHERE p.proname='m43_registrer_linje'").fetchone()[0]
    assert "terskel" not in args, args
    # …OG DEN SKRIVES PÅ RADEN, så «hvorfor er dette merket?» kan
    # besvares etter at grensen er justert.
    with _to() as (rt, mg):
        t = _tenantnavn("terskel")
        _krav(rt, t, terskel=6000)
        sid, start = _samtale(rt, t)
        _linje(rt, t, sid, start, sikkerhet=5000)
        _krav(rt, t, terskel=9000)
        _sett_kontekst(rt, t)
        rad = rt.execute("SELECT * FROM m43_transkripsjonen(%s,%s)",
                         (t, sid)).fetchone()
        rt.rollback()
        assert rad[7] == 6000, "terskelen fulgte ikke med raden"
        del mg


@pg
def test_samtale_overskrevet_er_umulig_en_rettelse_er_en_ny_linje():
    """`samtale_overskrevet`.

    Transkripsjonen er append-only, og en rettelse er en NY LINJE som
    peker på den gamle. Begge står: den som leser skal se at noe ble
    korrigert, ikke at det aldri sto der.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("append")
        _krav(rt, t)
        sid, start = _samtale(rt, t)
        lid, _ = _linje(rt, t, sid, start, sikkerhet=4000)
        for felt, verdi in (("tekst", "'noe annet'"),
                            ("sikkerhet_bp", "10000"),
                            ("ubekreftet", "false")):
            _nektes(mg, t,
                    f"UPDATE transkripsjonslinje SET {felt}={verdi}"
                    " WHERE tenant=%s AND linje_id=%s", (t, lid),
                    teller_sql="SELECT count(*) FROM transkripsjonslinje"
                               " WHERE tenant=%s AND linje_id=%s",
                    teller_args=(t, lid))
        _nektes(mg, t,
                "DELETE FROM transkripsjonslinje"
                " WHERE tenant=%s AND linje_id=%s", (t, lid),
                teller_sql="SELECT count(*) FROM transkripsjonslinje"
                           " WHERE tenant=%s AND linje_id=%s",
                teller_args=(t, lid))
        # RETTELSEN ER EN NY LINJE, og den gamle er SYNLIG SOM RETTET.
        _linje(rt, t, sid, start, nr=2, kilde="manuell", sikkerhet=10000,
               retter=lid)
        _sett_kontekst(rt, t)
        rader = rt.execute("SELECT * FROM m43_transkripsjonen(%s,%s)",
                           (t, sid)).fetchall()
        rt.rollback()
        assert len(rader) == 2
        assert rader[0][10] is True, "den rettede linjen er ikke merket"
        assert rader[1][9] == lid


@pg
def test_eskalering_uten_regel_er_urepresenterbar():
    """`eskalering_uten_regel` — OG DEN KAN ALDRI REISES.

    «Eskaleringsregler er kundens.» En eskalering uten en regel å peke
    på er MODULENS EGEN BESLUTNING om at noe var viktig nok til å vekke
    et menneske.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("utenregel")
        _krav(rt, t)
        sid, _ = _samtale(rt, t)
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.NotNullViolation):
            mg.execute(
                "INSERT INTO eskalering (tenant, eskalering_id,"
                " samtale_id, regel_id, mottaker, begrunnelse,"
                " eskalert_av)"
                " VALUES (%s,%s,%s,NULL,'vakt','fordi noe','u')",
                (t, uuid.uuid4(), sid))
        mg.rollback()
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            mg.execute(
                "INSERT INTO eskalering (tenant, eskalering_id,"
                " samtale_id, regel_id, mottaker, begrunnelse,"
                " eskalert_av)"
                " VALUES (%s,%s,%s,%s,'vakt','fordi noe','u')",
                (t, uuid.uuid4(), sid, uuid.uuid4()))
        mg.rollback()


@pg
def test_en_avviklet_regel_baerer_ingen_ny_eskalering():
    """EN ESKALERING PÅ ET GAMMELT PAPIR ER MODULENS EGEN BESLUTNING.

    Regelen kan avvikles i morgen; de eskaleringene den ALT bar står,
    og mottakeren er kopiert inn på dem.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("avviklet")
        _krav(rt, t)
        sid, _ = _samtale(rt, t)
        # REGELEN GJALDT FRA I FJOR. En regel kan ikke avvikles FØR den
        # gjaldt — døra nekter det, og det er riktig: et papir kan ikke
        # trekkes tilbake før det ble skrevet.
        rid = _regel(rt, t, fra=I_DAG - dt.timedelta(days=365))
        _sett_kontekst(rt, t)
        rt.execute("SELECT * FROM m43_eskaler(%s,%s,%s,%s,%s,%s)",
                   (t, uuid.uuid4(), sid, rid, "kunden var sint",
                    "u-test"))
        rt.commit()
        _sett_kontekst(rt, t)
        rt.execute("SELECT m43_avvikle_regel(%s,%s,%s,%s)",
                   (t, rid, I_DAG - dt.timedelta(days=1), "u-kari"))
        rt.commit()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute("SELECT * FROM m43_eskaler(%s,%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), sid, rid, "sint igjen",
                        "u-test"))
        assert "avviklet" in str(e.value)
        rt.rollback()
        # DEN GAMLE STÅR, MED MOTTAKEREN KOPIERT INN.
        _sett_kontekst(mg, t)
        rad = mg.execute(
            "SELECT mottaker FROM eskalering WHERE tenant=%s", (t,)
        ).fetchone()
        mg.rollback()
    assert rad == ("vakt@acme",)


@pg
def test_tenantlekkasje_i_samtaleregister_er_umulig():
    """FORCE RLS PÅ ALLE SJU, målt fra to kanter."""
    with _to() as (rt, mg):
        t1 = _tenantnavn("egen")
        t2 = _tenantnavn("annen")
        for t in (t1, t2):
            _krav(rt, t)
        sid, _ = _samtale(rt, t1, motpart="+4790000001")
        _sett_kontekst(rt, t2)
        sett = rt.execute("SELECT count(*) FROM m43_samtaleregister(%s,%s)",
                          (t1, 50)).fetchone()[0]
        rt.rollback()
        assert sett == 0, "en annen tenants samtaler var synlige"
        _sett_kontekst(rt, t2)
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as e:
            rt.execute("SELECT m43_sett_krav(%s,%s,%s,%s,%s,%s)",
                       (t1, 100, 5, 1, 1, "u-tyv"))
        assert "kallerens tenantkontekst" in str(e.value)
        rt.rollback()
        with _mig() as mg2:
            mangler = [r[0] for r in mg2.execute(
                "SELECT c.relname FROM pg_class c"
                " WHERE c.relname = ANY(%s)"
                "   AND NOT (c.relrowsecurity AND c.relforcerowsecurity)",
                (list(EGNE),)).fetchall()]
        assert mangler == [], f"uten FORCE ser eieren forbi policyen: {mangler}"
        del mg, sid


@pg
def test_runtime_har_ingen_tabellrettigheter():
    """SP-7: kjøretiden når dørene og ingenting annet."""
    with _mig() as mg:
        rader = mg.execute(
            "SELECT table_name, privilege_type FROM"
            " information_schema.table_privileges"
            " WHERE grantee='disponit' AND table_name = ANY(%s)",
            (list(EGNE) + ["opptakshjemmel"],)).fetchall()
    assert rader == [], f"runtime har tabellrettigheter: {rader}"


# =====================================================================
# SVEIPEN.
# =====================================================================

@pg
def test_en_samtale_som_aldri_ble_avsluttet_reises_og_lukkes_av_avslutningen():
    """EN HENGENDE INTEGRASJON ETTERLATER SAMTALEN ÅPEN, og en åpen
    samtale er et opptak som formelt fortsatt går.

    Taket er TENANTENS (`samtaletak_timer`).
    """
    with _to() as (rt, mg):
        t = _tenantnavn("hengende")
        _krav(rt, t, tak=4)
        sid, _ = _samtale(rt, t)
        with _sv() as sv:
            sv.execute("SELECT * FROM m43_sveip_telefoni(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        assert mg.execute(
            "SELECT count(*) FROM telefonifunn WHERE tenant=%s AND apen",
            (t,)).fetchone()[0] == 0
        mg.rollback()

        _eldre_samtale(mg, t, sid, timer=9)
        with _sv() as sv:
            sv.execute("SELECT * FROM m43_sveip_telefoni(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        funn = mg.execute(
            "SELECT funntype, referanse, over_grense FROM telefonifunn"
            " WHERE tenant=%s AND apen", (t,)).fetchall()
        mg.rollback()
        assert funn == [("samtale_uten_avslutning", sid, 9)], funn

        _sett_kontekst(rt, t)
        rt.execute("SELECT m43_avslutt_samtale(%s,%s,%s,%s)",
                   (t, sid, _naa(), "u-kari"))
        rt.commit()
        with _sv() as sv:
            sv.execute("SELECT * FROM m43_sveip_telefoni(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        etter = mg.execute(
            "SELECT apen, lukket_av FROM telefonifunn WHERE tenant=%s"
            "   AND funntype='samtale_uten_avslutning'", (t,)).fetchone()
        mg.rollback()
    assert etter == (False, "m43_sveip")


@pg
def test_en_eskalering_ingen_tok_reises_og_lukkes_av_lukkingen():
    """DEN DYRESTE STILLHETEN I MODULEN.

    Modulen vekket et menneske etter kundens egen regel, og så skjedde
    det ingenting — mens den andre parten fikk beskjed om at noen
    skulle ta over.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("glemt")
        _krav(rt, t, eskfrist=2)
        sid, _ = _samtale(rt, t)
        rid = _regel(rt, t)
        _sett_kontekst(rt, t)
        eid = uuid.uuid4()
        rt.execute("SELECT * FROM m43_eskaler(%s,%s,%s,%s,%s,%s)",
                   (t, eid, sid, rid, "kunden var sint", "u-test"))
        rt.commit()
        _eldre_eskalering(mg, t, eid, dogn=5)
        with _sv() as sv:
            sv.execute("SELECT * FROM m43_sveip_telefoni(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        funn = mg.execute(
            "SELECT funntype, over_grense FROM telefonifunn"
            " WHERE tenant=%s AND apen"
            "   AND funntype='eskalering_over_frist'", (t,)).fetchall()
        mg.rollback()
        assert funn == [("eskalering_over_frist", 5)], funn
        _sett_kontekst(rt, t)
        rt.execute("SELECT m43_lukk_eskalering(%s,%s,%s,%s)",
                   (t, eid, "haandtert", "u-kari"))
        rt.commit()
        with _sv() as sv:
            sv.execute("SELECT * FROM m43_sveip_telefoni(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        etter = mg.execute(
            "SELECT apen, lukket_av FROM telefonifunn WHERE tenant=%s"
            "   AND funntype='eskalering_over_frist'", (t,)).fetchone()
        mg.rollback()
    assert etter == (False, "m43_sveip")


@pg
def test_et_menneske_kan_ikke_lukke_sveipens_funn():
    """`samtale_uten_avslutning` og `eskalering_over_frist` lukkes av at
    TILSTANDEN opphører, ikke av at noen huker av."""
    with _to() as (rt, mg):
        t = _tenantnavn("nekt")
        _krav(rt, t, tak=1)
        sid, _ = _samtale(rt, t)
        _eldre_samtale(mg, t, sid, timer=5)
        with _sv() as sv:
            sv.execute("SELECT * FROM m43_sveip_telefoni(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        fid = mg.execute(
            "SELECT funn_id FROM telefonifunn WHERE tenant=%s"
            "   AND funntype='samtale_uten_avslutning'", (t,)).fetchone()[0]
        mg.rollback()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException) as e:
            rt.execute("SELECT m43_lukk_funn(%s,%s,%s,%s)",
                       (t, fid, "vi tar det senere", "u-test"))
        assert "lukkes av at tilstanden opphoerer" in str(e.value)
        rt.rollback()


@pg
def test_ubekreftet_linje_kan_avklares_av_et_menneske_og_forblir_lukket():
    """«VI HAR HØRT OPPTAKET, DET STEMMER» er en legitim avklaring med
    et navn på — og 131s lærdom gjelder: sveipen skal ikke gjenåpne den
    natten etter.

    MUTASJONEN SOM DREPER DENNE: fjern `NOT EXISTS`-leddet på lukkede
    funn i sveipens tredje blokk.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("avklart")
        _krav(rt, t, terskel=7000)
        sid, start = _samtale(rt, t)
        _linje(rt, t, sid, start, sikkerhet=4000)
        with _sv() as sv:
            sv.execute("SELECT * FROM m43_sveip_telefoni(500)")
            sv.commit()
        _sett_kontekst(rt, t)
        funn = [f for f in rt.execute("SELECT * FROM m43_telefonifunn(%s,%s)",
                                      (t, 50)).fetchall()
                if f[1] == "ubekreftet_linje_uavklart"]
        rt.rollback()
        assert funn, "den ubekreftede linjen ble ikke sett"
        assert funn[0][9] is True, "et menneske skal kunne avklare denne"
        _sett_kontekst(rt, t)
        rt.execute("SELECT m43_lukk_funn(%s,%s,%s,%s)",
                   (t, funn[0][0], "vi har hoert opptaket, det stemmer",
                    "u-kari"))
        rt.commit()
        # NATTEN ETTER.
        with _sv() as sv:
            sv.execute("SELECT * FROM m43_sveip_telefoni(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        apne = mg.execute(
            "SELECT count(*) FROM telefonifunn WHERE tenant=%s"
            "   AND funntype='ubekreftet_linje_uavklart' AND apen",
            (t,)).fetchone()[0]
        mg.rollback()
    assert apne == 0, "sveipen gjenaapnet en avklaring"


@pg
def test_en_rettet_linje_lukkes_av_rettelsen():
    """DEN ANDRE VEIEN UT: linjen rettes i stedet for å avklares.

    Da er tilstanden borte, og sveipen lukker sitt eget funn.
    """
    with _to() as (rt, mg):
        t = _tenantnavn("rettet")
        _krav(rt, t, terskel=7000)
        sid, start = _samtale(rt, t)
        lid, _ = _linje(rt, t, sid, start, sikkerhet=4000)
        with _sv() as sv:
            sv.execute("SELECT * FROM m43_sveip_telefoni(500)")
            sv.commit()
        _linje(rt, t, sid, start, nr=2, kilde="manuell", sikkerhet=10000,
               retter=lid)
        with _sv() as sv:
            sv.execute("SELECT * FROM m43_sveip_telefoni(500)")
            sv.commit()
        _sett_kontekst(mg, t)
        etter = mg.execute(
            "SELECT apen, lukket_av FROM telefonifunn WHERE tenant=%s"
            "   AND funntype='ubekreftet_linje_uavklart'"
            "   AND referanse=%s", (t, lid)).fetchone()
        mg.rollback()
    assert etter == (False, "m43_sveip")


@pg
def test_sveipen_ser_ingenting_uten_kryss_tenant_policyen():
    """130s LÆRDOM: en sveip uten `disponit.tenant` ville sett NULL
    RADER under FORCE RLS og rapportert null funn — MED GRØNN
    EXIT-KODE."""
    with _mig() as mg:
        rad = mg.execute(
            "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy"
            " WHERE polrelid='telefonikrav'::regclass"
            "   AND polname='m43_sveip_tenantliste'").fetchone()
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
                "SELECT * FROM m43_sveip_telefoni(500)").fetchall()
            sv.commit()
        del mg, t
    assert len(rader) == 1 and len(rader[0]) == 4
    assert rader[0][0] >= 1


@pg
def test_funntabellen_staar_i_m36s_katalog_med_lesretten():
    """133/134s LÆRDOM. Raden alene er bare en lovnad."""
    with _mig() as mg:
        rad = mg.execute(
            "SELECT modul, typekolonne, apenform FROM m36_funnregister"
            " WHERE relasjon='telefonifunn'").fetchone()
        les = mg.execute(
            "SELECT count(*) FROM information_schema.table_privileges"
            " WHERE table_name='telefonifunn' AND privilege_type='SELECT'"
            "   AND grantee='disponit_optimalisator_eier'").fetchone()[0]
    assert rad == ("m43_telefoni", "funntype", "apen_kolonne"), rad
    assert les == 1, "registrert uten lesrett — det ser komplett ut"


def test_sveipens_arbeidernokkel_er_modulens_egen():
    nokler = {}
    for fil in sorted((ROT / "platform" / "drift").glob("*sveip.py")):
        m = re.search(r"ARBEIDERNOKKEL = ([\d_]+)",
                      fil.read_text(encoding="utf-8"))
        if m:
            nokler.setdefault(m.group(1), []).append(fil.name)
    delte = {k: v for k, v in nokler.items() if len(v) > 1}
    assert delte == {}, f"delte arbeidernoekler: {delte}"


def test_driftsfila_navngir_sin_egen_jobb():
    """Arvefeilen fra 116-118, og fra kjørerne i 130/132/133/134."""
    sti = ROT / "deploy" / "staging" / "disponit-telefonisveip.service"
    tj = sti.read_text(encoding="utf-8")
    beskrivelse = tj.split("Description=")[1][:130]
    assert "telefoni" in beskrivelse.lower()
    for arvet in ("likviditet", "rangering", "EHF", "HMS", "møte",
                  "innhold", "kontantbane"):
        assert arvet not in beskrivelse, f"arvet ord: {arvet}"
    assert ("LoadCredential=DISPONIT_TELEFONISVEIP_URL:"
            "/etc/disponit/telefonisveip/DISPONIT_TELEFONISVEIP_URL" in tj)
    kjorer = (ROT / "platform" / "drift"
              / "kjor_telefonisveip.py").read_text(encoding="utf-8")
    assert "m43_sveip_telefoni()" in kjorer
    for arvet in ("m33_sveip_prognose", "m36_sveip_optimalisering",
                  "m7_sveip_moter", "m20_sveip_innhold"):
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
    assert _tid("disponit-telefonisveip.timer") == (6 * 60 + 45, 4)


def test_sveipens_dsn_star_i_ci():
    """127s LÆRDOM. Navnet hentes fra KJØREREN, ikke fra filnavnet."""
    kjorer = ROT / "platform" / "drift" / "kjor_telefonisveip.py"
    url = re.findall(r"DISPONIT_[A-Z0-9_]+_URL",
                     kjorer.read_text(encoding="utf-8"))
    assert url, "kjoereren leser ingen DSN"
    ventet = url[0].replace("DISPONIT_", "DISPONIT_TEST_", 1)
    ventet = ventet[:-len("_URL")] + "_DSN"
    ci = (ROT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert f"{ventet}:" in ci, f"{ventet} mangler i ci.yml"
    opp = (ROT / "deploy" / "staging" / "opp.sh").read_text(encoding="utf-8")
    assert url[0] in opp, f"{url[0]} mangler i opp.sh"


@pg
def test_hver_skrivedoer_legger_igjen_et_spor():
    """134s LÆRDOM: porten spør KATALOGEN, ikke en liste."""
    skrivende = ("m43_sett_krav", "m43_registrer_hjemmel",
                 "m43_registrer_regel", "m43_avvikle_regel",
                 "m43_start_samtale", "m43_avslutt_samtale",
                 "m43_start_opptak", "m43_registrer_linje",
                 "m43_eskaler", "m43_lukk_eskalering", "m43_lukk_funn")
    with _mig() as mg:
        uten = []
        for navn in skrivende:
            kropp = mg.execute(
                "SELECT pg_get_functiondef(p.oid) FROM pg_proc p"
                " WHERE p.proname=%s", (navn,)).fetchone()[0]
            if "m43_evidens" not in kropp:
                uten.append(navn)
    assert uten == [], f"skrivedoerer uten evidensspor: {uten}"


def test_fundamentet_navngir_modulen_og_migrasjonen():
    tekst = FUNDAMENT.read_text(encoding="utf-8")
    assert "135" in tekst and "M-43" in tekst
    assert MIGRASJON.exists()
