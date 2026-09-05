"""125 sveipevernet — ET MENNESKES LUKKING SKAL STÅ.

CodeRabbit fant det på 124: sveipen gjenåpnet HVER NATT et funn et
menneske hadde lukket, mens tilstanden var uendret. `apen = true` var
ubetinget i `DO UPDATE`, og lukkeknappen var pynt.

MIN EGEN PORT BEKREFTET PYNTEN. Den målte at lukkedøra SVARTE
`apen = false`, og kjørte aldri sveipen etterpå. Det er samme klasse
som safe-area-regelen uten `viewport-fit=cover`: en regel som ser ut
som et gjerde, en port som bekrefter at regelen står der, og ingen som
måler at den virker.

DERFOR ER FORMEN PÅ HVER PORT HER DEN SAMME: gjør handlingen, kjør
etterpå det som skulle ha ødelagt den, og les raden PÅ NYTT.

Portene måler også min egen RETTING AV MÅLINGEN.
`docs/FUNN-SVEIPEN-GJENAAPNER.md` listet ti migrasjoner; det riktige
tallet er ni andre, og fire av dem manglet `lukket_av` helt. Lista står
i `TABELLER` under og telles av en port — en liste i en dokumentfil
råtner, en liste med en port gjør det ikke.
"""
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN  # noqa: F401
from .test_m37 import _sett_kontekst

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "125_sveipevern.sql")
FUNNDOK = ROT / "docs" / "FUNN-SVEIPEN-GJENAAPNER.md"

pg = pytest.mark.skipif(
    not MIGRATOR_DSN,
    reason="DISPONIT_TEST_MIGRATOR_DSN ikke satt")

# TABELL -> (sveipnavn, migrasjon, har_lukkenotat_fra_fodselen)
#
# NI TABELLER I NI MIGRASJONER. 112, 113 og 114 er IKKE med: de har
# ingen lukkedør, og en ubetinget gjenåpning bryter da ikke noe. 120
# treffer `merkevarevarsel` og ikke `merkevarefunn` — et navnesøk på
# «funn» gikk forbi den da jeg skrev funndokumentet.
TABELLER = {
    "motpartsfunn": ("m48_sveip", "116", False),
    "sanksjonsfunn": ("m49_sveip", "117", False),
    "anbudsfunn": ("m46_sveip", "118", False),
    "tilskuddsfunn": ("m51_sveip", "119", False),
    "merkevarevarsel": ("m55_sveip_merkevare", "120", True),
    "ehffunn": ("m54_sveip_ehf", "121", True),
    "tollfunn": ("m52_sveip_tollkode", "122", True),
    "myndighetsfunn": ("m47_sveip", "123", True),
    "journalfunn": ("m50_sveip", "124", True),
}


def _mig():
    from db.pg import koble
    return koble(MIGRATOR_DSN)


def _tenant() -> str:
    return f"t-125-{secrets.token_hex(4)}"


# ---------------------------------------------------------------------
# STRUKTUR — porter som ikke trenger base.
# ---------------------------------------------------------------------

def test_alle_ni_tabellene_har_en_vakt_i_migrasjonen():
    """Vakten skal stå på ALLE ni, ikke på dem jeg husket.

    MUTASJONEN SOM DREPER DENNE: fjern én CREATE TRIGGER-linje.
    """
    tekst = MIGRASJON.read_text(encoding="utf-8")
    for tabell, (sveip, mig, _) in TABELLER.items():
        assert re.search(
            r"CREATE TRIGGER\s+" + tabell + r"_lukkevern\s+BEFORE UPDATE"
            r"\s+ON\s+" + tabell, tekst), (
            f"{tabell} ({mig}) mangler lukkevern-triggeren")
        assert f"sveipefunn_lukkevern('{sveip}')" in tekst, (
            f"{tabell} har feil eller manglende sveipenavn")


def test_funndokumentet_er_rettet():
    """Dokumentet listet ti migrasjoner. Det var galt på tre måter.

    En liste i en markdownfil råtner uten at noe sier fra. Denne porten
    krever at dokumentet nevner BÅDE 120 (som manglet) OG at 112–114
    ikke hører hjemme der.
    """
    tekst = FUNNDOK.read_text(encoding="utf-8")
    assert "120" in tekst, "120 (M-55) manglet på lista"
    assert "116" in tekst and "124" in tekst
    assert re.search(r"112|113|114", tekst), (
        "rettingen av 112–114 skal stå skrevet, ikke bare fjernes")


def test_vakten_feiler_ikke_den_retter():
    """En exception i vakten ville drept hele nattens sveip.

    Vernet skal ikke bli et driftsavbrudd: den første raden noen hadde
    lukket ville avbrutt transaksjonen for alle de andre.

    MUTASJONEN SOM DREPER DENNE: bytt den stille rettingen mot en
    RAISE EXCEPTION.
    """
    tekst = MIGRASJON.read_text(encoding="utf-8")
    kropp = re.search(
        r"CREATE FUNCTION sveipefunn_lukkevern\(\).*?\nEND \$\$;",
        tekst, re.S)
    assert kropp, "vaktfunksjonen finnes ikke"
    kode = "\n".join(l for l in kropp.group(0).splitlines()
                     if not l.lstrip().startswith("--"))
    assert "RAISE EXCEPTION" not in kode, (
        "vakten skal rette stille — en exception her dreper sveipen")


def test_ingen_volatilitetsloegn_igjen():
    """`IMMUTABLE` på en funksjon som leser klokka.

    Planleggeren har lov til å folde en IMMUTABLE funksjon til en
    konstant og gjenbruke den i en bufret plan. Jeg skrev det feil to
    ganger (123 og 124); denne porten leser ALLE migrasjonene, slik at
    den tredje gangen fanges før den merges.
    """
    mapp = ROT / "platform" / "core" / "db" / "migrations"
    fasit = MIGRASJON.read_text(encoding="utf-8")
    brudd = []
    for fil in sorted(mapp.glob("*.sql")):
        tekst = fil.read_text(encoding="utf-8")
        for m in re.finditer(
                r"CREATE (?:OR REPLACE )?FUNCTION\s+([a-z0-9_]+)\s*\("
                r".*?\$\$.*?\$\$;", tekst, re.S):
            blokk = m.group(0)
            hode = blokk[:blokk.index("$$")]
            if "IMMUTABLE" not in hode:
                continue
            kropp = "\n".join(
                l for l in blokk.splitlines()
                if not l.lstrip().startswith("--"))
            if re.search(r"current_date|current_timestamp|\bnow\(\)"
                         r"|localtimestamp", kropp):
                # 125 retter dem — men BARE for migrasjoner FØR
                # 125. Første utgave av porten fritok navnet uansett
                # hvor det sto, så en ny IMMUTABLE-erklæring i 126
                # ville sluppet forbi den porten som ble skrevet for å
                # fange nettopp den (CodeRabbit).
                nr = int(fil.name.split("_", 1)[0])
                if (nr < 125
                        and f"CREATE OR REPLACE FUNCTION {m.group(1)}"
                        in fasit):
                    continue
                brudd.append(f"{fil.name}:{m.group(1)}")
    assert not brudd, (
        "IMMUTABLE på funksjon som leser klokka: " + ", ".join(brudd))


def test_hver_lukkedoer_nekter_en_tom_aktoer():
    """`m50_lukk_funn` godtok NULL, og `false OR NULL` ble NULL.

    Det er `cardinality(NULL)` om igjen — samme NULL-form jeg selv fant
    i 122 og skrev en port for, gjeninnført i selve rettelsen.

    MUTASJONEN SOM DREPER DENNE: fjern én av nektene.
    """
    tekst = MIGRASJON.read_text(encoding="utf-8")
    doerer = ("m48_lukk_funn", "m49_lukk_funn", "m46_lukk_funn",
              "m51_lukk_funn", "m55_lukk_funn", "m55_lukk_varsel",
              "m54_lukk_funn", "m52_lukk_funn", "m47_lukk_funn",
              "m50_lukk_funn")
    for doer in doerer:
        blokk = re.search(
            r"CREATE OR REPLACE FUNCTION " + doer + r"\(.*?\nEND \$\$;",
            tekst, re.S)
        assert blokk, f"{doer} gjenskapes ikke i 125"
        assert "p_aktor IS NULL OR btrim(p_aktor) = ''" in blokk.group(0), (
            f"{doer} nekter ikke en tom aktør")


# ---------------------------------------------------------------------
# ATFERD — mot basen.
# ---------------------------------------------------------------------

@pg
def test_menneskets_lukking_staar_natten_over():
    """PORTEN SOM MANGLET.

    Lukk som menneske, kjør DERETTER sveipens ubetingede gjenåpning
    ordrett som den står i 116, og les raden på nytt.

    MUTASJONEN SOM DREPER DENNE: DROP TRIGGER motpartsfunn_lukkevern.
    Verifisert — uten vakten står raden igjen som ÅPEN med `kari` i
    `lukket_av`, altså i en tilstand ingen flate kan vise riktig.
    """
    t = _tenant()
    with _mig() as c:
        _sett_kontekst(c, t)
        mid = "11111111-1111-1111-1111-111111111111"
        c.execute("INSERT INTO motpartssubjekt (tenant, motpart_id,"
                  " organisasjonsnummer, navn_oppgitt, opprettet_av)"
                  " VALUES (%s,%s,'123456789','A','test')", (t, mid))
        c.execute("INSERT INTO motpartsfunn (tenant, motpart_id,"
                  " funntype) VALUES (%s,%s,'ingen_krav')", (t, mid))
        c.execute("UPDATE motpartsfunn SET apen=false, lukket_ts=now(),"
                  " lukket_av='kari', lukkenotat='sett, folger opp'"
                  " WHERE tenant=%s", (t,))
        # SVEIPENS EGEN SETNING, ordrett fra 116.
        c.execute("UPDATE motpartsfunn SET sist_sett_sveip=now(),"
                  " apen=true, lukket_ts=NULL WHERE tenant=%s", (t,))
        rad = c.execute(
            "SELECT apen, lukket_av, lukkenotat, lukket_ts IS NOT NULL"
            " FROM motpartsfunn WHERE tenant=%s", (t,)).fetchone()
        c.rollback()
    assert rad[0] is False, "sveipen gjenåpnet et menneskes lukking"
    assert rad[1] == "kari"
    assert rad[2] == "sett, folger opp"
    assert rad[3] is True, "lukketidspunktet forsvant"


@pg
def test_sveipens_egen_lukking_gjenaapnes_og_sporet_ryddes():
    """Sveipens lukking betyr «tilstanden var borte».

    Er tilstanden tilbake, skal funnet være tilbake — og gårsdagens
    lukkespor skal IKKE bli stående på et åpent funn.
    """
    t = _tenant()
    with _mig() as c:
        _sett_kontekst(c, t)
        mid = "22222222-2222-2222-2222-222222222222"
        c.execute("INSERT INTO motpartssubjekt (tenant, motpart_id,"
                  " organisasjonsnummer, navn_oppgitt, opprettet_av)"
                  " VALUES (%s,%s,'123456789','A','test')", (t, mid))
        c.execute("INSERT INTO motpartsfunn (tenant, motpart_id,"
                  " funntype) VALUES (%s,%s,'uvurdert_motpart')",
                  (t, mid))
        # Sveipens lukking navngir seg ikke — vakten stempler.
        c.execute("UPDATE motpartsfunn SET apen=false, lukket_ts=now()"
                  " WHERE tenant=%s", (t,))
        navn = c.execute("SELECT lukket_av FROM motpartsfunn"
                         " WHERE tenant=%s", (t,)).fetchone()[0]
        assert navn == "m48_sveip", (
            "en navnløs lukking skal stemples med sveipens navn")
        c.execute("UPDATE motpartsfunn SET apen=true, lukket_ts=NULL"
                  " WHERE tenant=%s", (t,))
        rad = c.execute(
            "SELECT apen, lukket_av, lukkenotat, lukket_ts"
            " FROM motpartsfunn WHERE tenant=%s", (t,)).fetchone()
        c.rollback()
    assert rad[0] is True, "sveipen fikk ikke gjenåpne sin egen lukking"
    assert rad[1] is None and rad[2] is None and rad[3] is None, (
        "lukkesporet ble stående på et åpent funn")


@pg
def test_en_lukket_rad_kan_ikke_miste_navnet():
    """§2 gjør NULL-en CodeRabbit beskrev UREPRESENTERBAR.

    Med denne på plass er `apen OR lukket_av = '...'` TOTAL: på en åpen
    rad gir venstresiden sant, og på en lukket rad er høyresiden aldri
    NULL. Det er grunnen til at de ni sveipefunksjonene ikke gjenskapes.
    """
    t = _tenant()
    with _mig() as c:
        _sett_kontekst(c, t)
        mid = "33333333-3333-3333-3333-333333333333"
        c.execute("INSERT INTO motpartssubjekt (tenant, motpart_id,"
                  " organisasjonsnummer, navn_oppgitt, opprettet_av)"
                  " VALUES (%s,%s,'123456789','A','test')", (t, mid))
        c.execute("INSERT INTO motpartsfunn (tenant, motpart_id,"
                  " funntype) VALUES (%s,%s,'ingen_krav')", (t, mid))
        c.execute("UPDATE motpartsfunn SET apen=false, lukket_ts=now(),"
                  " lukket_av='kari', lukkenotat='sett' WHERE tenant=%s",
                  (t,))
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("UPDATE motpartsfunn SET lukket_av=NULL"
                      " WHERE tenant=%s", (t,))
        c.rollback()


@pg
def test_volatiliteten_er_stable_i_basen():
    """Ikke bare i filen — i katalogen.

    En port som bare leser SQL-teksten ville ikke fanget at
    `CREATE OR REPLACE` faktisk endret merkingen.
    """
    with _mig() as c:
        rader = dict(c.execute(
            "SELECT proname, provolatile FROM pg_proc WHERE proname IN"
            " ('m50_kilde_gyldig','m47_regelverk_gyldig')").fetchall())
        c.rollback()
    assert rader.get("m50_kilde_gyldig") == "s"
    assert rader.get("m47_regelverk_gyldig") == "s"


@pg
def test_vakten_staar_paa_alle_ni_i_basen():
    """Migrasjonsteksten kan si det uten at basen gjorde det."""
    with _mig() as c:
        navn = {r[0] for r in c.execute(
            "SELECT tgname FROM pg_trigger WHERE tgname LIKE"
            " '%%\\_lukkevern'").fetchall()}
        c.rollback()
    mangler = {f"{t}_lukkevern" for t in TABELLER} - navn
    assert not mangler, f"vakter mangler i basen: {sorted(mangler)}"


@pg
def test_jsonb_runden_bevarer_hver_kolonne():
    """Vakten går veien om `to_jsonb`/`jsonb_populate_record`.

    Det er formen som gjør ÉN vakt mulig for ni tabeller med ulike
    kolonner — men den sender HELE raden gjennom jsonb, ikke bare de
    fire nøklene vakten rører. En type som ikke tåler rundturen ville
    endret en verdi ingen ba den om å endre, stille.

    `journalfunn` er valgt fordi den har uuid, int, text, timestamptz
    og boolean i samme rad.

    MUTASJONEN SOM DREPER DENNE: la vakten bygge `v_ny` fra
    `jsonb_build_object(...)` alene i stedet for fra `to_jsonb(NEW) ||`
    — da forsvinner alt den ikke nevner.
    """
    t = _tenant()
    kolonner = ("funntype", "kilde_id", "post_id", "person_id",
                "over_grense", "detalj", "kravversjon", "forst_sett",
                "sist_sett_sveip")
    with _mig() as c:
        _sett_kontekst(c, t)
        pid = "44444444-4444-4444-4444-444444444444"
        c.execute(
            "INSERT INTO journalfunn (tenant, funn_id, person_id,"
            " funntype, over_grense, detalj, kravversjon)"
            " VALUES (%s, gen_random_uuid(), %s,"
            " 'slettefrist_naermer_seg', 7, 'en detalj', 3)", (t, pid))
        c.execute("UPDATE journalfunn SET apen=false, lukket_ts=now(),"
                  " lukket_av='kari', lukkenotat='sett' WHERE tenant=%s",
                  (t,))
        sql = "SELECT " + ", ".join(kolonner) + \
              " FROM journalfunn WHERE tenant=%s"
        for_ = c.execute(sql, (t,)).fetchone()
        # Sveipens gjenåpning, som vakten skal rulle tilbake.
        c.execute("UPDATE journalfunn SET apen=true, lukket_ts=NULL,"
                  " lukket_av=NULL, lukkenotat=NULL, over_grense=9"
                  " WHERE tenant=%s", (t,))
        etter = c.execute(sql, (t,)).fetchone()
        apen = c.execute("SELECT apen FROM journalfunn WHERE tenant=%s",
                         (t,)).fetchone()[0]
        c.rollback()
    assert apen is False, "lukkingen ble ikke bevart"
    endret = {k: (a, b) for k, a, b in zip(kolonner, for_, etter)
              if a != b and k != "over_grense"}
    assert not endret, f"jsonb-rundturen endret kolonner: {endret}"
    # `over_grense` SKAL være oppdatert: sveipen får skrive alt annet
    # enn selve gjenåpningen.
    assert etter[kolonner.index("over_grense")] == 9, (
        "sveipen mistet retten til å oppdatere tilstanden")


@pg
def test_volatilitetsbyttet_river_ingen_indeks():
    """IMMUTABLE → STABLE kan gjøre en indeks ugyldig i stillhet.

    PostgreSQL krever IMMUTABLE i et indeksuttrykk og i en LAGRET
    generert kolonne, men `CREATE OR REPLACE FUNCTION` nekter ikke et
    bytte som bryter kravet — indeksen blir bare stående og lyve.

    Porten måler mot katalogen, ikke mot migrasjonsteksten: den ville
    fanget en indeks lagt til av en SENERE migrasjon like godt.
    """
    with _mig() as c:
        idx = c.execute(
            "SELECT indexrelid::regclass::text FROM pg_index"
            " WHERE pg_get_indexdef(indexrelid) ~"
            " 'm50_kilde_gyldig|m47_regelverk_gyldig'").fetchall()
        gen = c.execute(
            "SELECT attrelid::regclass::text || '.' || attname"
            "  FROM pg_attribute a"
            "  JOIN pg_attrdef d ON d.adrelid = a.attrelid"
            "                   AND d.adnum = a.attnum"
            " WHERE a.attgenerated <> ''"
            "   AND pg_get_expr(d.adbin, d.adrelid) ~"
            "       'm50_kilde_gyldig|m47_regelverk_gyldig'").fetchall()
        c.rollback()
    assert not idx, f"indeks bruker en nå-STABLE funksjon: {idx}"
    assert not gen, f"generert kolonne bruker en nå-STABLE: {gen}"
