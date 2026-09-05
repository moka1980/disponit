"""M-54 EHF- og Peppol-avviksretter v1 (121) — FORMEN, IKKE INNHOLDET.

Grensen `m54-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

DEN SKARPESTE PORTEN ER `validering_mot_utlopt_skjema`, og den måler
TO ting som hører sammen: døra NEKTER å validere mot et utløpt
regelsett, og sveipen MELDER hver dom som alt er felt under et sett
som siden har gått ut.

Hvorfor det er den skarpeste: EN FORELDET REGEL SER NØYAKTIG UT SOM EN
RIKTIG REGEL. Det er forskjellen fra en feil — en feil gir et avvik
noen ser, mens en foreldet dom er velformet, selvsikker og gal.

DEN NEST SKARPESTE ER `retting_uten_avviksreferanse`, og den måler et
FRAVÆR: `ehfretting.avvik_id` er NOT NULL med fremmednøkkel. En
retting uten et avvik å rette er en endring av en faktura uten en
grunn noen kan peke på — og en faktura er et betalingskrav.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import ast
import datetime
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

EHFSVEIP_DSN = os.environ.get("DISPONIT_TEST_EHFSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "121_m54_ehf_avvik.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "ehf.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "ehf.py",
    ROT / "platform" / "drift" / "ehfsveip.py",
    ROT / "platform" / "drift" / "kjor_ehfsveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("ehfkrav", "ehfregelsett", "ehfregel", "ehfdokument",
        "ehffelt", "ehfvalidering", "ehfavvik", "ehfretting",
        "ehffunn")

_STRENG = re.compile(
    r"'''.*?'''" r'|""".*?"""'
    r"|`(?:[^`\\]|\\.)*`"
    r"|'(?:[^'\\]|\\.|'')*'"
    r'|"(?:[^"\\]|\\.)*"', re.S)


def _bare_kode(fil: Path, *, uten_strenger: bool = False) -> str:
    """Filens innhold uten kommentarer OG uten docstrings (m24-formen).

    PORTEN SKAL MÅLE HANDLINGEN, IKKE BEGRUNNELSEN. Klynge 6 lærte det
    tre ganger: en port som leter i rå filtekst treffer kommentaren som
    forklarer HVORFOR et mønster er unngått.
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
    ut = "\n".join(l for l in linjer
                   if not l.lstrip().startswith(merke))
    return _STRENG.sub("''", ut) if uten_strenger else ut


def _rt():
    from db.pg import koble
    return koble(DSN)


def _sv():
    from db.pg import koble
    return koble(EHFSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m54-{merke}-{secrets.token_hex(4)}"


def _krav(c, tenant, *, utlop=30, avvik=7, aktor="u-test",
          nokkel=None):
    _sett_kontekst(c, tenant)
    v = c.execute("SELECT m54_sett_krav(%s,%s,%s,%s,%s)",
                  (tenant, utlop, avvik, aktor,
                   nokkel or secrets.token_hex(8))).fetchone()[0]
    c.commit()
    return v


def _regelsett(c, tenant, *, standard="ehf", versjon="3.0",
               fra="2024-01-01", til=None, sid=None, aktor="u-test"):
    sid = sid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m54_registrer_regelsett("
        "%s,%s,%s,%s,%s::date,%s::date,%s,%s,%s)",
        (tenant, sid, standard, versjon, fra, til,
         secrets.token_hex(32), None, aktor))
    c.commit()
    return sid


def _regel(c, tenant, sid, *, kode=None, sti="Invoice/ID",
           krav="finnes", kodeverdi=(), sum_sti=None,
           alvorlighet="feil", gid=None, aktor="u-test"):
    gid = gid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m54_registrer_regel(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant, gid, sid, kode or f"R-{secrets.token_hex(3)}", sti,
         krav, list(kodeverdi), sum_sti, alvorlighet,
         f"regel {sti}", aktor))
    c.commit()
    return gid


def _dokument(c, tenant, *, retning="utgaaende", ref=None,
              motpart="Kunde AS", dato="2026-09-01", did=None,
              aktor="u-test"):
    did = did or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m54_registrer_dokument("
        "%s,%s,%s,%s,%s,%s::date,%s,%s,%s,%s)",
        (tenant, did, retning, ref or f"F-{secrets.token_hex(3)}",
         motpart, dato, secrets.token_hex(32), 8192,
         f"artefakt/{secrets.token_hex(3)}", aktor))
    c.commit()
    return did


def _felter(c, tenant, did, rader, *, aktor="u-test"):
    """`rader` er (sti, forekomst, verdi, ore)-firlinger."""
    _sett_kontekst(c, tenant)
    n = c.execute(
        "SELECT m54_registrer_felter(%s,%s,%s,%s,%s,%s,%s)",
        (tenant, did, [r[0] for r in rader], [r[1] for r in rader],
         [r[2] for r in rader], [r[3] for r in rader], aktor)
    ).fetchone()[0]
    c.commit()
    return n


def _valider(c, tenant, did, sid, *, vid=None, aktor="u-test"):
    vid = vid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    rad = c.execute("SELECT * FROM m54_valider_dokument(%s,%s,%s,%s,%s)",
                    (tenant, did, sid, vid, aktor)).fetchone()
    c.commit()
    return vid, rad


def _sveip(v, grense=500):
    rad = v.execute("SELECT * FROM m54_sveip_ehf(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(c, tenant, *, bare_apne=True):
    _sett_kontekst(c, tenant)
    rader = c.execute(
        "SELECT funntype, over_grense, detalj, apen"
        "  FROM m54_funnene(%s,%s) ORDER BY funntype",
        (tenant, bare_apne)).fetchall()
    c.rollback()
    return rader


# --------------------------------------------------------------------
# §0: hver invariant i `m54-v1` har minst én port.
# --------------------------------------------------------------------

def test_grensen_er_registrert_og_hver_invariant_har_en_port():
    """§0-regelen, målt."""
    from manifestskjema import KRAVGRENSER
    inv = set(KRAVGRENSER["m54-v1"]["invarianter"])
    assert inv
    tekst = Path(__file__).read_text(encoding="utf-8")
    mangler = sorted(i for i in inv if i not in tekst)
    assert mangler == [], f"invarianter uten port: {mangler}"


# --------------------------------------------------------------------
# FRAVÆRENE.
# --------------------------------------------------------------------

def test_ingen_kolonne_og_ingen_dor_sender_faktura():
    """`modulen_sendte_faktura` — FRAVÆRET er porten.

    En faktura sendt to ganger er et DOBBELT BETALINGSKRAV, og en
    rettet faktura som gikk ut uten at noen så på rettingen er nettopp
    det.
    """
    sql = _bare_kode(MIGRASJON, uten_strenger=True).lower()
    for forbudt in ("m54_send", "sendt", "mottaker", "utboks",
                    "outbox"):
        assert forbudt not in sql, forbudt
    api = _bare_kode(MODULFILER[0], uten_strenger=True).lower()
    for forbudt in ("send_faktura", "sendfaktura", "m54_send",
                    "mottaker", "outbox"):
        assert forbudt not in api, forbudt
    js = _bare_kode(FLATE, uten_strenger=True).lower()
    for forbudt in ("sendfaktura", "send_faktura", "mottaker"):
        assert forbudt not in js, forbudt
    # …og tilstanden HOS OSS finnes, med sitt eget navn.
    assert "klar_til_signering" in _bare_kode(MIGRASJON)


def test_modulen_signerer_ingenting():
    """`modulen_signerte_utsending` — signaturen hører til v2.

    En signatur et menneske setter på noe det ikke har lest, flytter
    ansvaret uten å flytte kontrollen. Vi vet ikke ennå hvor ofte
    klargjøringen tar feil.
    """
    for fil in list(MODULFILER) + [MIGRASJON]:
        kode = _bare_kode(fil, uten_strenger=True).lower()
        for forbudt in ("signatur", "signer(", "attester",
                        "attestasjon"):
            assert forbudt not in kode, f"{fil.name}: {forbudt}"
    # `klar_til_signering` er en TILSTAND, ikke en signatur — den er
    # unntaket fra ordsjekken over, og den skal finnes.
    assert "klar_til_signering" in _bare_kode(MIGRASJON)


def test_rettingen_kan_ikke_uttrykkes_uten_avvik():
    """`retting_uten_avviksreferanse` — DEN MÅLER ET FRAVÆR.

    En retting uten et avvik å rette er en endring av en faktura uten
    en grunn noen kan peke på — og en faktura er et betalingskrav.

    MUTASJONEN SOM DREPER DENNE: gjør `avvik_id` nullbar.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    retting = sql[sql.index("CREATE TABLE ehfretting"):
                  sql.index("CREATE INDEX ehfretting_avvik_idx")]
    assert "avvik_id UUID NOT NULL" in retting
    assert "ehfretting_avvik_fk" in retting
    assert "REFERENCES ehfavvik" in retting
    # …og alle tre delene av rettingen er påkrevd som konsept:
    # «rett feltet» uten fra-verdien er en endring ingen kan
    # kontrollere i ettertid.
    assert "felt_sti TEXT NOT NULL" in retting
    assert "til_verdi TEXT NOT NULL" in retting
    assert "begrunnelse TEXT NOT NULL" in retting


def test_valideringen_kan_ikke_uttrykkes_uten_regelsett():
    """`validering_uten_skjemaversjon` — FORMEN PÅ TABELLEN.

    BÅDE fremmednøkkel OG snapshot: fremmednøkkelen binder til raden,
    snapshotet binder til TEKSTEN, og det er snapshotet som svarer på
    «hvilken versjon» år senere uten et oppslag.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    val = sql[sql.index("CREATE TABLE ehfvalidering"):
              sql.index("CREATE INDEX ehfvalidering_dokument_idx")]
    assert "regelsett_id UUID NOT NULL" in val
    assert "ehfvalidering_sett_fk" in val
    assert "standard_ved_validering TEXT NOT NULL" in val
    assert "versjon_ved_validering TEXT NOT NULL" in val
    # …og dommen er GENERERT, ikke skrevet.
    assert "GENERATED ALWAYS AS (antall_feil = 0) STORED" in val


def test_regelspraket_er_lite_og_lesbart():
    """v1s REGELSPRÅK ER FIRE KRAV OG INGEN FRI XPath.

    Et generelt uttrykksspråk ville gjort hver regel til kode ingen kan
    lese uten å kjøre den — og en regel man må kjøre for å forstå er
    ikke et regelsett, det er et program med et regelsetts navn.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    regel = sql[sql.index("CREATE TABLE ehfregel ("):
                sql.index("CREATE INDEX ehfregel_sett_idx")]
    m = re.search(r"krav IN \(([^)]*)\)", regel)
    assert m, regel
    krav = [x for x in re.findall(r"'([a-z_]+)'", m.group(1))]
    assert krav == ["finnes", "ikke_tom", "i_kodeliste", "lik_sum"]
    # STIEN ER BOKSTAVELIG: ingen jokertegn, ingen uttrykk.
    assert "sti TEXT NOT NULL CHECK (sti ~ '^[A-Za-z0-9_./-]+$')" \
        in regel
    # KRAVET OG PARAMETEREN HENGER SAMMEN: en `i_kodeliste` uten
    # kodeliste ville vært STILLE GRØNN, den verste tilstanden en
    # regel kan ha.
    assert "ehfregel_parameter_folger_krav" in regel
    # …og ingen av modulfilene evaluerer et uttrykk.
    for fil in MODULFILER:
        kode = _bare_kode(fil, uten_strenger=True).lower()
        for forbudt in ("eval(", "exec(", "xpath", "compile("):
            assert forbudt not in kode, f"{fil.name}: {forbudt}"


# --------------------------------------------------------------------
# DOMMENE, MÅLT MOT BASEN.
# --------------------------------------------------------------------

@pg
def test_dommen_nektes_mot_et_utlopt_regelsett(miljo):
    """`validering_mot_utlopt_skjema`, halvdel én: DØRA NEKTER.

    EN FORELDET REGEL SER NØYAKTIG UT SOM EN RIKTIG REGEL. Det er
    forskjellen fra en feil: en feil gir et avvik noen ser, mens en
    foreldet HS-kode — eller her, en foreldet EHF-dom — er velformet,
    selvsikker og gal.

    MUTASJONEN SOM DREPER DENNE: fjern gyldighetssjekken i
    `m54_valider_dokument`.
    """
    tenant = _tenantnavn("utlopt")
    with _rt() as c:
        _krav(c, tenant)
        # ET ALT UTLØPT SETT KAN REGISTRERES — det er ARKIVET, og
        # modulen finnes for å kunne svare på hva standarden sa den
        # gangen. Å forby det ville vært å forby spørsmålet.
        gammel = _regelsett(c, tenant, versjon="2.0",
                            fra="2019-01-01", til="2023-12-31")
        _regel(c, tenant, gammel)
        did = _dokument(c, tenant)
        _felter(c, tenant, did, [("Invoice/ID", 0, "F-1", None)])
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT * FROM m54_valider_dokument(%s,%s,%s,%s,%s)",
                      (tenant, did, gammel, uuid.uuid4(), "u-test"))
        assert "ikke gyldig i dag" in str(e.value)
        c.rollback()


@pg
def test_de_tre_utfallene_og_det_tredje_er_poenget(miljo):
    """`feil`, `advarsel` og `uten_grunnlag`.

    DET TREDJE ER DET SOM SKILLER MODULEN FRA EN VANLIG VALIDATOR: en
    regel som nevner et felt vi ikke har trukket ut, er IKKE stille
    grønn. Et manglende grunnlag skal si fra.

    `finnes` ER UNNTAKET: der ER fraværet selve avviket.

    MUTASJONEN SOM DREPER DENNE: la en regel uten grunnlag telle som
    bestått.
    """
    tenant = _tenantnavn("utfall")
    with _rt() as c:
        _krav(c, tenant)
        sid = _regelsett(c, tenant)
        _regel(c, tenant, sid, kode="R-1", sti="Invoice/ID",
               krav="finnes")
        _regel(c, tenant, sid, kode="R-2", sti="Invoice/Currency",
               krav="i_kodeliste", kodeverdi=("NOK", "EUR"))
        _regel(c, tenant, sid, kode="R-3", sti="Invoice/Total",
               krav="lik_sum", sum_sti="Invoice/Line/Amount")
        _regel(c, tenant, sid, kode="R-4", sti="Invoice/Note",
               krav="ikke_tom", alvorlighet="advarsel")
        _regel(c, tenant, sid, kode="R-5", sti="Invoice/BuyerRef",
               krav="ikke_tom")
        did = _dokument(c, tenant)
        _felter(c, tenant, did, [
            ("Invoice/ID", 0, "F-1", None),
            ("Invoice/Currency", 0, "USD", None),
            ("Invoice/Total", 0, "125000", 125_000),
            ("Invoice/Note", 0, "", None),
            ("Invoice/Line/Amount", 1, "100000", 100_000),
            ("Invoice/Line/Amount", 2, "20000", 20_000),
        ])
        # `Invoice/BuyerRef` er MED VILJE ikke trukket ut.
        vid, rad = _valider(c, tenant, did, sid)
        regler, feil, advarsler, utenfor, gyldig = rad[:5]
        assert (regler, feil, advarsler, utenfor) == (5, 2, 1, 1)
        assert gyldig is False

        _sett_kontekst(c, tenant)
        avvik = {r[0]: (r[1], r[2], r[3], r[4]) for r in c.execute(
            "SELECT regelkode, alvorlighet, sti, funnet_verdi,"
            "       forventet FROM m54_avvikene(%s,%s)",
            (tenant, vid)).fetchall()}
        # R-1 HOLDT — feltet fantes.
        assert "R-1" not in avvik
        assert avvik["R-2"][0] == "feil"
        assert avvik["R-2"][3] == "NOK, EUR"
        # SUMMEN REGNES I HELTALL (106s dom), og den står som forventet.
        assert avvik["R-3"] == ("feil", "Invoice/Total", "125000",
                                "120000")
        # TOMT FELT OG MANGLENDE FELT ER TO FORSKJELLIGE AVVIK.
        assert avvik["R-4"][0] == "advarsel"
        assert avvik["R-4"][2] == "", "tomt felt ble lest som fravær"
        assert avvik["R-5"][0] == "uten_grunnlag"
        assert avvik["R-5"][2] is None, "fravær ble lest som tomt"
        c.rollback()


@pg
def test_et_regelsett_uten_regler_kan_ikke_domme(miljo):
    """CodeRabbits funn: «0 regler, 0 feil, GYLDIG».

    Uten nekten ville en validering mot et tomt sett erklært
    dokumentet i orden — og svaret ville vært velformet og
    selvsikkert. Det er den verste formen for feil modulen kan gjøre.

    MUTASJONEN SOM DREPER DENNE: fjern regeltellingen i
    `m54_valider_dokument`.
    """
    tenant = _tenantnavn("tomtsett")
    with _rt() as c:
        _krav(c, tenant)
        sid = _regelsett(c, tenant)      # ingen regler
        did = _dokument(c, tenant)
        _felter(c, tenant, did, [("Invoice/ID", 0, "F-1", None)])
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT * FROM m54_valider_dokument(%s,%s,%s,%s,%s)",
                      (tenant, did, sid, uuid.uuid4(), "u-test"))
        assert "ingen regler" in str(e.value)
        c.rollback()


@pg
def test_et_felt_som_bare_finnes_paa_linjeniva_er_uten_grunnlag(miljo):
    """CodeRabbits funn: `finnes` er FOREKOMST-AGNOSTISK, men verdien
    leses på DOKUMENTNIVÅ.

    Uten skillet ville en `ikke_tom`-regel på `Invoice/Line/Amount`
    sett at feltet finnes (forekomst 1 og 2), lest verdien som NULL
    fordi det ikke finnes på forekomst 0, og meldt en FEIL som ikke
    finnes. En oppdiktet formfeil på en faktura er ikke støy — det er
    en retting noen ville gjort på et betalingskrav.

    MUTASJONEN SOM DREPER DENNE: la de andre kravene dømme på `finnes`
    i stedet for `har_verdi`.
    """
    tenant = _tenantnavn("linjeniva")
    with _rt() as c:
        _krav(c, tenant)
        sid = _regelsett(c, tenant)
        # `finnes` SKAL holde: feltet finnes, om enn bare på linjenivå.
        _regel(c, tenant, sid, kode="R-1",
               sti="Invoice/Line/Amount", krav="finnes")
        # …men `ikke_tom` har intet dokumentnivå å dømme på.
        _regel(c, tenant, sid, kode="R-2",
               sti="Invoice/Line/Amount", krav="ikke_tom")
        did = _dokument(c, tenant)
        _felter(c, tenant, did, [
            ("Invoice/Line/Amount", 1, "100000", 100_000),
            ("Invoice/Line/Amount", 2, "20000", 20_000),
        ])
        vid, rad = _valider(c, tenant, did, sid)
        _regler, feil, _adv, utenfor = rad[:4]
        assert feil == 0, "en oppdiktet formfeil på linjenivå"
        assert utenfor == 1
        _sett_kontekst(c, tenant)
        avvik = {r[0]: r[1] for r in c.execute(
            "SELECT regelkode, alvorlighet FROM m54_avvikene(%s,%s)",
            (tenant, vid)).fetchall()}
        assert "R-1" not in avvik, "`finnes` skal holde på linjenivå"
        assert avvik["R-2"] == "uten_grunnlag"
        c.rollback()


@pg
def test_et_utlopt_regelsett_meldes_ogsa_uten_terskler(miljo):
    """CodeRabbits funn: `CROSS JOIN krav` mot en TOM `krav` gir null
    rader.

    En tenant som ikke har satt terskler ville dermed ALDRI fått vite
    at regelsettet er gått ut — og det er modulens viktigste beskjed.
    Funnet avhenger ikke av en terskel, så det skal ikke være koblet
    til en.

    MUTASJONEN SOM DREPER DENNE: sett `CROSS JOIN krav k` tilbake på
    `regelsett_utlopt`.
    """
    tenant = _tenantnavn("utenterskel")
    with _rt() as c:
        # INGEN `_krav` — det er hele poenget.
        sid = _regelsett(c, tenant, versjon="3.0")
        _regel(c, tenant, sid, sti="Invoice/ID", krav="finnes")
        did = _dokument(c, tenant)
        _felter(c, tenant, did, [("Invoice/ID", 0, "F-1", None)])
        _valider(c, tenant, did, sid)
        _sett_kontekst(c, tenant)
        c.execute("SELECT m54_sett_gyldig_til(%s,%s,"
                  "current_date - 1,%s)", (tenant, sid, "u-test"))
        c.commit()
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        typer = {r[0] for r in _funn(c, tenant)}
        assert "regelsett_utlopt" in typer, typer
        assert "validering_mot_utlopt_regelsett" in typer, typer
        assert "ingen_krav" in typer, typer
        # KRAVVERSJONEN ER NULL, og det er et ærlig svar.
        _sett_kontekst(c, tenant)
        kv = c.execute(
            "SELECT kravversjon FROM m54_funnene(%s,true)"
            " WHERE funntype = 'regelsett_utlopt'",
            (tenant,)).fetchone()[0]
        assert kv is None
        c.rollback()


@pg
def test_et_dokument_uten_felter_kan_ikke_valideres(miljo):
    """En validering mot null felter ville gitt «alt uten grunnlag» og
    sett ut som en kjøring."""
    tenant = _tenantnavn("uparset")
    with _rt() as c:
        _krav(c, tenant)
        sid = _regelsett(c, tenant)
        _regel(c, tenant, sid)
        did = _dokument(c, tenant)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT * FROM m54_valider_dokument(%s,%s,%s,%s,%s)",
                      (tenant, did, sid, uuid.uuid4(), "u-test"))
        assert "ikke parset" in str(e.value)
        c.rollback()


@pg
def test_felterlistene_ma_ha_samme_lengde(miljo):
    """Ulik lengde ville stilltiende kappet den korteste — og et felt
    som forsvant i kappingen ville blitt `uten_grunnlag` uten at noen
    skrev det."""
    tenant = _tenantnavn("lengde")
    with _rt() as c:
        did = _dokument(c, tenant)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute(
                "SELECT m54_registrer_felter(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, did, ["a", "b"], [0], ["x", "y"],
                 [None, None], "u-test"))
        assert "ulik" in str(e.value)
        c.rollback()


@pg
def test_en_ny_regelsettversjon_gir_en_ny_dom(miljo):
    """SAMME DOKUMENT MOT SAMME SETT ER ÉN DOM; mot et NYTT sett er det
    en ny rad ved siden av den gamle.

    Og den nye dommen felles mot NØYAKTIG DE SAMME FELTENE — det er
    hele grunnen til at dokumentet parses til rader én gang.
    """
    tenant = _tenantnavn("nydom")
    with _rt() as c:
        _krav(c, tenant)
        s1 = _regelsett(c, tenant, versjon="3.0")
        _regel(c, tenant, s1, kode="R-1", sti="Invoice/ID",
               krav="finnes")
        did = _dokument(c, tenant)
        _felter(c, tenant, did, [("Invoice/ID", 0, "F-1", None)])
        _valider(c, tenant, did, s1)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute("SELECT * FROM m54_valider_dokument(%s,%s,%s,%s,%s)",
                      (tenant, did, s1, uuid.uuid4(), "u-test"))
        c.rollback()

        # NYTT SETT MED EN STRENGERE REGEL → NY DOM, mot samme felter.
        s2 = _regelsett(c, tenant, versjon="3.1")
        _regel(c, tenant, s2, kode="R-1", sti="Invoice/ID",
               krav="finnes")
        _regel(c, tenant, s2, kode="R-2", sti="Invoice/BuyerRef",
               krav="finnes")
        _vid, rad = _valider(c, tenant, did, s2)
        assert rad[0] == 2 and rad[1] == 1
        _sett_kontekst(c, tenant)
        rader = c.execute(
            "SELECT versjon, antall_feil FROM m54_valideringene(%s,%s)"
            " ORDER BY versjon", (tenant, did)).fetchall()
        assert rader == [("3.0", 0), ("3.1", 1)]
        c.rollback()


@pg
def test_regelsettets_identitet_er_frosset_men_sluttdatoen_kan_settes(
        miljo):
    """KLYNGENS EGEN DOM, OG DEN BLE OPPDAGET VED Å PRØVE Å TESTE DEN
    MOTSATTE.

    Settet var først HELT frosset, og da kunne modulen ikke skrive ned
    at myndigheten hadde kunngjort en sluttdato. Et standardorgan
    varsler i juni at EHF 3.0 trekkes 31. desember — uten en vei til å
    registrere det måtte vi latt som vi ikke visste. Å nekte å skrive
    ned endringen er å nekte klyngens egen doktrine.

    ALT ANNET ER FROSSET: identiteten er det som gjør en gammel
    validering etterprøvbar.

    MUTASJONEN SOM DREPER DENNE: gi eieren full UPDATE på
    `ehfregelsett`.
    """
    tenant = _tenantnavn("frosset")
    with _rt() as c:
        _krav(c, tenant)
        sid = _regelsett(c, tenant, versjon="3.0")
        c.rollback()

    from db.pg import koble
    with koble(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        m.execute("SET LOCAL ROLE disponit_ehf_eier")
        # IDENTITETEN: kolonnegranten nekter.
        for kolonne, ny in (("versjon", "'4.0'"),
                            ("standard", "'ubl'"),
                            ("gyldig_fra", "'2020-01-01'"),
                            ("innhold_sha256", "repeat('f',64)")):
            with pytest.raises(
                    psycopg.errors.InsufficientPrivilege) as e:
                m.execute(f"UPDATE ehfregelsett SET {kolonne} = {ny}"
                          " WHERE regelsett_id = %s", (sid,))
            assert "permission denied" in str(e.value).lower()
            m.execute("ROLLBACK; BEGIN")
            m.execute("SET LOCAL ROLE disponit_ehf_eier")
            _sett_kontekst(m, tenant)
        # …MEN SLUTTDATOEN KAN SETTES.
        m.execute("UPDATE ehfregelsett SET gyldig_til = current_date"
                  " WHERE regelsett_id = %s", (sid,))
        m.rollback()

    # …og døra gjør det samme, med evidens.
    with _rt() as c:
        _sett_kontekst(c, tenant)
        c.execute("SELECT m54_sett_gyldig_til(%s,%s,%s::date,%s)",
                  (tenant, sid, "2026-12-31", "u-test"))
        c.commit()
        _sett_kontekst(c, tenant)
        rad = c.execute(
            "SELECT gyldig_til, versjon FROM m54_regelsettene(%s,500)",
            (tenant,)).fetchone()
        assert rad[0] == datetime.date(2026, 12, 31)
        assert rad[1] == "3.0", "identiteten flyttet seg"
        c.rollback()


@pg
def test_dommen_er_frosset(miljo):
    """`validering_overskrevet`.

    `ehfregelsett`, `ehfregel`, `ehfdokument`, `ehffelt`,
    `ehfvalidering` og `ehfavvik` har ikke full UPDATE i det hele tatt
    — det er en RETTIGHET som ikke finnes, ikke en vakt som kan gjøre
    en feil. En dom som kunne endres i ettertid, ville gjort hele
    modulen til et arkiv over meninger.
    """
    tenant = _tenantnavn("domfrosset")
    with _rt() as c:
        _krav(c, tenant)
        sid = _regelsett(c, tenant)
        _regel(c, tenant, sid, sti="Invoice/ID", krav="finnes")
        did = _dokument(c, tenant)
        _felter(c, tenant, did, [("Invoice/ID", 0, "F-1", None)])
        vid, _rad = _valider(c, tenant, did, sid)
        c.rollback()

    from db.pg import koble
    for tabell, nokkel, verdi in (
            ("ehfvalidering", "validering_id", vid),
            ("ehffelt", "dokument_id", did),
            ("ehfdokument", "dokument_id", did),
            ("ehfregel", "regelsett_id", sid)):
        # EGEN TILKOBLING PER TABELL: `rollback()` tilbakestiller
        # `SET ROLE` (klynge 6s målte lærdom).
        with koble(MIGRATOR_DSN) as m:
            _sett_kontekst(m, tenant)
            m.execute("SET LOCAL ROLE disponit_ehf_eier")
            with pytest.raises(
                    psycopg.errors.InsufficientPrivilege) as e:
                m.execute(f"UPDATE {tabell} SET tenant = tenant"
                          f" WHERE {nokkel} = %s", (verdi,))
            assert "permission denied" in str(e.value).lower()
            m.rollback()

    # …OG SLETTING ER ALDRI LOVLIG.
    for tabell in EGNE:
        with koble(MIGRATOR_DSN) as m:
            _sett_kontekst(m, tenant)
            m.execute("SET LOCAL ROLE disponit_ehf_eier")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                m.execute(f"DELETE FROM {tabell}")
            m.rollback()


@pg
def test_rettingen_nektes_paa_et_avvik_uten_grunnlag(miljo):
    """En retting der vi ikke kunne dømme, ville endret fakturaen fordi
    vi manglet data — ikke fordi noe var galt."""
    tenant = _tenantnavn("utengrunn")
    with _rt() as c:
        _krav(c, tenant)
        sid = _regelsett(c, tenant)
        _regel(c, tenant, sid, kode="R-1", sti="Invoice/ID",
               krav="finnes")
        _regel(c, tenant, sid, kode="R-2", sti="Invoice/BuyerRef",
               krav="ikke_tom")
        did = _dokument(c, tenant)
        _felter(c, tenant, did, [("Invoice/ID", 0, "F-1", None)])
        vid, _rad = _valider(c, tenant, did, sid)
        _sett_kontekst(c, tenant)
        aid = c.execute(
            "SELECT avvik_id FROM m54_avvikene(%s,%s)"
            " WHERE alvorlighet = 'uten_grunnlag'",
            (tenant, vid)).fetchone()[0]
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute(
                "SELECT m54_registrer_retting(%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, uuid.uuid4(), aid, "Invoice/BuyerRef", None,
                 "REF-1", "fyller inn", "u-test"))
        assert "uten_grunnlag" in str(e.value)
        c.rollback()


@pg
def test_klarmerking_nektes_med_urettet_formfeil(miljo):
    """Å merke klar mens en formfeil står urettet, er å be et menneske
    signere på at noe er i orden som ikke er det.

    LÅSEN LESES PÅ NYTT ETTER `FOR UPDATE` (klynge 6s lærdom, skrevet
    feil fem ganger der).
    """
    tenant = _tenantnavn("klar")
    with _rt() as c:
        _krav(c, tenant)
        sid = _regelsett(c, tenant)
        _regel(c, tenant, sid, kode="R-1", sti="Invoice/A",
               krav="finnes")
        _regel(c, tenant, sid, kode="R-2", sti="Invoice/B",
               krav="finnes")
        did = _dokument(c, tenant)
        _felter(c, tenant, did, [("Invoice/C", 0, "x", None)])
        vid, rad = _valider(c, tenant, did, sid)
        assert rad[1] == 2
        _sett_kontekst(c, tenant)
        avvik = [r[0] for r in c.execute(
            "SELECT avvik_id FROM m54_avvikene(%s,%s)"
            " ORDER BY regelkode", (tenant, vid)).fetchall()]
        r1 = uuid.uuid4()
        c.execute("SELECT m54_registrer_retting(%s,%s,%s,%s,%s,%s,%s,%s)",
                  (tenant, r1, avvik[0], "Invoice/A", None, "a",
                   "legger til A", "u-test"))
        c.commit()
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT m54_merk_klar(%s,%s,%s)",
                      (tenant, r1, "u-test"))
        assert "formfeil uten retting" in str(e.value)
        c.rollback()

        # BEGGE RETTET → klar, og null udekkede.
        r2 = uuid.uuid4()
        _sett_kontekst(c, tenant)
        c.execute("SELECT m54_registrer_retting(%s,%s,%s,%s,%s,%s,%s,%s)",
                  (tenant, r2, avvik[1], "Invoice/B", None, "b",
                   "legger til B", "u-test"))
        c.commit()
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT m54_merk_klar(%s,%s,%s)",
                         (tenant, r1, "u-test")).fetchone()[0] == 0
        c.commit()

    # …OG EN KLAR RETTING ER FROSSET.
    from db.pg import koble
    with koble(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        m.execute("SET LOCAL ROLE disponit_ehf_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as e:
            m.execute("UPDATE ehfretting SET til_verdi = 'noe annet'"
                      " WHERE retting_id = %s", (r1,))
        assert "frosset" in str(e.value)
        m.rollback()

    # LÅSEN LESES PÅ NYTT ETTER `FOR UPDATE` i døra.
    sql = _bare_kode(MIGRASJON)
    kropp = sql[sql.index("CREATE FUNCTION m54_merk_klar"):
                sql.index("REVOKE ALL ON FUNCTION m54_merk_klar")]
    laas = kropp.index("FOR UPDATE")
    assert "klar_til_signering" in kropp[laas:]


@pg
def test_tenantisolasjon(miljo):
    """`tenantlekkasje_i_ehfregister`.

    Hver tabell har RLS med FORCE, og lesedørene krever tenantkontekst.
    En faktura navngir en motpart og et beløp; en lekkasje her er ikke
    en tellefeil.
    """
    a, b = _tenantnavn("iso-a"), _tenantnavn("iso-b")
    with _rt() as c:
        for t in (a, b):
            _krav(c, t)
            _regelsett(c, t)
            _dokument(c, t, ref=f"F-{t[-4:]}")
        _sett_kontekst(c, a)
        refs = [r[2] for r in c.execute(
            "SELECT * FROM m54_dokumentene(%s,500)", (a,)).fetchall()]
        assert len(refs) == 1 and refs[0].endswith(a[-4:])
        with pytest.raises(psycopg.Error):
            c.execute("SELECT * FROM m54_dokumentene(%s,500)", (b,))
        c.rollback()
    sql = _bare_kode(MIGRASJON)
    for tabell in EGNE:
        assert f"'{tabell}'" in sql, tabell
    assert "FORCE ROW LEVEL" in sql


@pg
def test_kjoretidsrollen_har_ingen_tabellrettigheter(miljo):
    """SP-7. Kjøretidsrollen har dørene, aldri tabellene."""
    with _rt() as c:
        _sett_kontekst(c, TENANT)
        for tabell in EGNE:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(f"SELECT 1 FROM {tabell} LIMIT 1")
            c.rollback()
            _sett_kontekst(c, TENANT)
        c.rollback()


@pg
def test_sveipen_er_ikke_kjoretidsrollens(miljo):
    """Sveipen er kryss-tenant. Runtime har ikke EXECUTE på den."""
    with _rt() as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM m54_sveip_ehf(10)")
        c.rollback()


# --------------------------------------------------------------------
# SVEIPEN.
# --------------------------------------------------------------------

@pg
def test_sveipen_finner_dommer_felt_under_en_regel_som_gikk_ut(miljo):
    """`validering_mot_utlopt_skjema`, halvdel to: SVEIPEN MELDER.

    Dette er scenarioet som faktisk skjer: settet var gyldig da dommen
    ble felt, og myndigheten kunngjorde sluttdatoen etterpå. Uten
    sveipen ville dommen blitt stående som om den fortsatt gjaldt.

    OG FUNNET KAN IKKE LUKKES AV ET MENNESKE: det forsvinner når
    dokumentet valideres på nytt mot et gyldig sett, og det er en
    HANDLING, ikke en mening. Samme figur som M-49s bekreftede treff
    (117), M-46s udekkede absolutte krav (118), M-51s takfunn (119) og
    M-55s uhenviste forveksling (120).

    MUTASJONEN SOM DREPER DENNE: ta funntypen ut av nekten i
    `m54_lukk_funn`.
    """
    tenant = _tenantnavn("sveip")
    with _rt() as c:
        _krav(c, tenant)
        sid = _regelsett(c, tenant, versjon="3.0")
        _regel(c, tenant, sid, sti="Invoice/ID", krav="finnes")
        did = _dokument(c, tenant)
        _felter(c, tenant, did, [("Invoice/ID", 0, "F-1", None)])
        _valider(c, tenant, did, sid)
        # MYNDIGHETEN KUNNGJØR SLUTTDATOEN ETTERPÅ.
        _sett_kontekst(c, tenant)
        c.execute("SELECT m54_sett_gyldig_til(%s,%s,"
                  "current_date - 1,%s)", (tenant, sid, "u-test"))
        c.commit()

    with _sv() as v:
        tenanter, nye, _o, _l = _sveip(v)
        assert tenanter >= 1 and nye >= 1
        # …OG DEN ER IDEMPOTENT.
        _t2, nye2, oppd2, _l2 = _sveip(v)
        assert nye2 == 0 and oppd2 >= 1

    with _rt() as c:
        typer = {r[0] for r in _funn(c, tenant)}
        assert "validering_mot_utlopt_regelsett" in typer
        # UTEN EN GYLDIG ETTERFØLGER er settet også et funn.
        assert "regelsett_utlopt" in typer
        _sett_kontekst(c, tenant)
        fid = c.execute(
            "SELECT funn_id FROM m54_funnene(%s,true)"
            " WHERE funntype = 'validering_mot_utlopt_regelsett'",
            (tenant,)).fetchone()[0]
        with pytest.raises(psycopg.errors.InvalidParameterValue) as e:
            c.execute("SELECT m54_lukk_funn(%s,%s,%s,%s)",
                      (tenant, fid, "ikke viktig", "u-test"))
        assert "validering_mot_utlopt_regelsett" in str(e.value)
        c.rollback()

        # NY VALIDERING MOT ET GYLDIG SETT → sveipen lukker det selv.
        ny = _regelsett(c, tenant, versjon="3.1")
        _regel(c, tenant, ny, sti="Invoice/ID", krav="finnes")
        _valider(c, tenant, did, ny)
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        apne = {r[0] for r in _funn(c, tenant)}
        assert "validering_mot_utlopt_regelsett" not in apne
        assert "regelsett_utlopt" not in apne, \
            "et arkivert sett ved siden av et gyldig er historikk"
        alle = _funn(c, tenant, bare_apne=False)
        lukket = [r for r in alle
                  if r[0] == "validering_mot_utlopt_regelsett"]
        assert lukket and lukket[0][3] is False


@pg
def test_et_arkivert_regelsett_er_ikke_et_funn(miljo):
    """IKKE ETHVERT UTLØPT SETT.

    Et arkivert EHF 2.0 ved siden av et gyldig 3.0 er HISTORIKK, ikke
    et problem — og et funn på det ville vært støy hver natt for
    alltid. Det som ER et problem, er å stå uten noe gyldig å validere
    MED: da slutter modulen å virke, stille.
    """
    tenant = _tenantnavn("arkiv")
    with _rt() as c:
        _krav(c, tenant)
        _regelsett(c, tenant, versjon="2.0", fra="2019-01-01",
                   til="2023-12-31")
        _regelsett(c, tenant, versjon="3.0", fra="2024-01-01")
        c.rollback()
    with _sv() as v:
        _sveip(v)
    with _rt() as c:
        typer = {r[0] for r in _funn(c, tenant)}
        assert "regelsett_utlopt" not in typer, typer


@pg
def test_sveipen_ser_alle_tenanter(miljo):
    """Kryss-tenant, med tenantlista MATERIALISERT før løkka (112s
    lærdom)."""
    a, b = _tenantnavn("flere-a"), _tenantnavn("flere-b")
    with _rt() as c:
        for t in (a, b):
            _krav(c, t, avvik=1)
            _regelsett(c, t)
        c.rollback()
    with _sv() as v:
        tenanter, _n, _o, _l = _sveip(v)
        assert tenanter >= 2
    sveip = _bare_kode(MIGRASJON)
    assert "array_agg(DISTINCT r.tenant" in sveip
    assert "FOREACH v_t IN ARRAY v_tenanter" in sveip


@pg
def test_evidenskjeden_baerer_hvert_steg(miljo):
    """Hver skrivedør skriver en revisjonslogglinje."""
    tenant = _tenantnavn("evidens")
    with _rt() as c:
        _krav(c, tenant)
        sid = _regelsett(c, tenant)
        _regel(c, tenant, sid, sti="Invoice/ID", krav="finnes")
        did = _dokument(c, tenant)
        _felter(c, tenant, did, [("Invoice/ID", 0, "F-1", None)])
        _valider(c, tenant, did, sid)
        _sett_kontekst(c, tenant)
        c.execute("SELECT m54_sett_gyldig_til(%s,%s,%s::date,%s)",
                  (tenant, sid, "2027-12-31", "u-test"))
        c.commit()
    from db.pg import koble
    with koble(MIGRATOR_DSN) as m:
        _sett_kontekst(m, tenant)
        handlinger = {r[0] for r in m.execute(
            "SELECT handling FROM revisjonslogg"
            " WHERE tenant=%s AND kilde='m54_ehf'",
            (tenant,)).fetchall()}
        m.rollback()
    assert handlinger >= {"ehfkrav_satt", "ehfregelsett_registrert",
                          "ehfregel_registrert",
                          "ehfdokument_registrert",
                          "ehffelt_registrert",
                          "ehfvalidering_gjort",
                          "ehfregelsett_gyldig_til_satt"}


@pg
def test_terskeldoera_er_idempotent_paa_nokkelen(miljo):
    """119s lærdom, tatt med fra første linje."""
    tenant = _tenantnavn("idem")
    nokkel = secrets.token_hex(8)
    with _rt() as c:
        v1 = _krav(c, tenant, utlop=30, nokkel=nokkel)
        assert _krav(c, tenant, utlop=30, nokkel=nokkel) == v1
        with pytest.raises(psycopg.errors.UniqueViolation):
            _krav(c, tenant, utlop=45, nokkel=nokkel)
        c.rollback()
        assert _krav(c, tenant, utlop=45) == v1 + 1
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT * FROM m54_kravene(%s)",
                         (tenant,)).fetchone()[0] == 45
        c.rollback()


# --------------------------------------------------------------------
# API, FLATE OG DRIFT.
# --------------------------------------------------------------------

def test_rutene_er_registrert_med_riktig_scope():
    """LESING `okonomi:read`, SKRIVING `bestilling:opprett`."""
    from api.app import RUTESCOPE
    forventet = {
        ("GET", "/v1/ehf"): "okonomi:read",
        ("GET", "/v1/ehf/funn"): "okonomi:read",
        ("GET", "/v1/ehf/regelsett/{regelsett_id:uuid}/regler"):
            "okonomi:read",
        ("GET", "/v1/ehf/validering/{validering_id:uuid}/avvik"):
            "okonomi:read",
        ("GET", "/v1/ehf/dokument/{dokument_id:uuid}/valideringer"):
            "okonomi:read",
        ("POST", "/v1/ehf/krav"): "bestilling:opprett",
        ("POST", "/v1/ehf/regelsett"): "bestilling:opprett",
        ("POST", "/v1/ehf/regel"): "bestilling:opprett",
        ("POST", "/v1/ehf/dokument"): "bestilling:opprett",
        ("POST", "/v1/ehf/regelsett/{regelsett_id:uuid}/gyldig-til"):
            "bestilling:opprett",
        ("POST", "/v1/ehf/dokument/{dokument_id:uuid}/felter"):
            "bestilling:opprett",
        ("POST", "/v1/ehf/dokument/{dokument_id:uuid}/valider"):
            "bestilling:opprett",
        ("POST", "/v1/ehf/avvik/{avvik_id:uuid}/retting"):
            "bestilling:opprett",
        ("POST", "/v1/ehf/retting/{retting_id:uuid}/klar"):
            "bestilling:opprett",
        ("POST", "/v1/ehf/funn/{funn_id:uuid}/lukk"):
            "bestilling:opprett",
    }
    for nokkel, scope in forventet.items():
        assert RUTESCOPE.get(nokkel) == scope, nokkel


def test_ingen_rute_sender_en_faktura():
    """`modulen_sendte_faktura`, sett fra rutetabellen."""
    from api.app import RUTESCOPE
    stier = [sti for (_m, sti) in RUTESCOPE
             if sti.startswith("/v1/ehf")]
    assert stier
    for sti in stier:
        for forbudt in ("send", "utsend", "signer", "lever"):
            assert forbudt not in sti, sti
    # …og `/klar` finnes, som en tilstand hos oss.
    assert any(sti.endswith("/klar") for sti in stier)


def test_valideringsruten_returnerer_alle_fire_tallene():
    """`antall_uten_grunnlag` MÅ STÅ PÅ SVARET.

    En leser som bare ser «2 feil» vet ikke om resten var grønn eller
    udømt — og tallet som mangler er det farligste av dem.
    """
    api = MODULFILER[0].read_text(encoding="utf-8")
    rute = api[api.index("def valider_endepunkt"):
               api.index("def registrer_retting_endepunkt")]
    for felt in ('"antall_regler"', '"antall_feil"',
                 '"antall_advarsler"', '"antall_uten_grunnlag"',
                 '"gyldig"', '"versjon"'):
        assert felt in rute, felt


def test_flaten_viser_aldri_versjonen_uten_gyldigheten():
    """En versjon uten om den gjelder i dag er nøyaktig den
    opplysningen som gjør en foreldet dom umulig å skille fra en
    riktig."""
    js = FLATE.read_text(encoding="utf-8")
    assert '"ui.ehf.regelsett_gyldig"' in js
    assert '"ui.ehf.regelsett_utlopt"' in js
    assert "regelsettTekst" in js
    # …og dommen bærer alle fire tallene, `utenfor` inkludert.
    assert "{utenfor}" in js


def test_flaten_skiller_tomt_felt_fra_manglende_felt():
    """Å vise begge som «–» ville visket ut det første et menneske
    spør om."""
    js = FLATE.read_text(encoding="utf-8")
    assert '"ui.ehf.feltet_fantes_ikke"' in js
    assert '"ui.ehf.feltet_var_tomt"' in js


def test_flaten_sier_hva_modulen_ikke_gjor():
    js = FLATE.read_text(encoding="utf-8")
    assert 't("ui.ehf.oversikt.hvorfor")' in js
    from json import loads
    nb = loads((ROT / "locales" / "nb.json").read_text(
        encoding="utf-8"))
    assert "sender ingen faktura" in nb["ui.ehf.oversikt.hvorfor"]


def test_sluttdatoruten_krever_at_nokkelen_star():
    """CodeRabbits funn: utelatt nøkkel og eksplisitt `null` er IKKE
    det samme på `/gyldig-til`.

    En klient som GLEMMER feltet ville ellers stilltiende nullstilt
    sluttdatoen — og gjort «utgår 31. desember» om til «gjelder
    fortsatt». Det er nøyaktig feilen modulen finnes for å hindre: en
    regel som ER gått ut, som ser ut som en som ikke er det. At den
    kunne oppstå fra en glemt JSON-nøkkel gjør den verre.

    VED OPPRETTELSE er de to det samme, og det er riktig: et nytt
    regelsett uten sluttdato gjelder fortsatt.

    MUTASJONEN SOM DREPER DENNE: bruk `_dato_valgfri` på ruten igjen.
    """
    api = MODULFILER[0].read_text(encoding="utf-8")
    rute = api[api.index("def sett_gyldig_til_endepunkt"):
               api.index("def registrer_regel_endepunkt")]
    assert "_dato_som_kan_vaere_null" in rute
    assert "_dato_valgfri" not in rute
    # …og hjelperen skiller faktisk på det.
    hjelper = api[api.index("def _dato_som_kan_vaere_null"):
                  api.index("def _sha256")]
    assert "if felt not in kropp:" in hjelper
    assert "if kropp[felt] is None:" in hjelper
    # OPPRETTELSEN bruker fortsatt den milde varianten.
    opprett = api[api.index("def registrer_regelsett_endepunkt"):
                  api.index("def sett_gyldig_til_endepunkt")]
    assert '_dato_valgfri(kropp, "gyldig_til", rid)' in opprett


def test_flaten_sier_ikke_en_grunn_som_kanskje_ikke_gjelder():
    """CodeRabbits funn: 409 har TO grunner på valideringsruten.

    Et utløpt regelsett, og et dokument som alt er dømt mot nettopp
    dette settet. Å alltid si «utløpt» ville sendt den som gjentok en
    validering på jakt etter et problem som ikke fantes.
    """
    js = FLATE.read_text(encoding="utf-8")
    assert '"ui.ehf.feil.validering_avvist"' in js
    assert '"ui.ehf.feil.utlopt_regelsett"' not in js, \
        "en foreldreløs nøkkel skal ikke bli staaende"


def test_flaten_tilbyr_bare_gyldige_regelsett():
    """Døra nekter mot et utløpt sett, og en knapp som alltid feiler er
    verre enn en valgmulighet som ikke finnes."""
    js = FLATE.read_text(encoding="utf-8")
    assert "r.gyldig_naa === true" in js
    assert 't("ui.ehf.validering.ingen_gyldige")' in js


def test_sveipen_leser_fire_felt_og_ikke_flere():
    """#358s lærdom."""
    sveip = MODULFILER[1].read_text(encoding="utf-8")
    assert "KONTRAKTFELT = 4" in sveip
    assert "rader[0][:KONTRAKTFELT]" in sveip


def test_kjoreskriptet_har_ingen_fallback_til_database_url():
    kjor = MODULFILER[2].read_text(encoding="utf-8")
    assert "DISPONIT_EHFSVEIP_URL" in kjor
    assert "DATABASE_URL" not in _bare_kode(MODULFILER[2])


def test_manglende_dsn_teller_som_en_feilet_kjoring():
    """CodeRabbits funn på 118 og 120, tatt med fra første linje."""
    kjor = MODULFILER[2].read_text(encoding="utf-8")
    gren = kjor[kjor.index("if not dsn:"):
                kjor.index("tidligere = _les_feiltelling()")]
    assert "_les_feiltelling() + 1" in gren
    assert "_skriv_feiltelling(n)" in gren
    assert '"alarm"' in gren
    assert "negativ feiltelling" in kjor
    assert "isinstance(raa, int)" in kjor
    assert "isinstance(raa, bool)" in kjor


def test_timeren_er_klynge_sjus_forste_plass():
    """10:05 — og sveipestatus står fortsatt BAKERST.

    ENDRET I M-33s PR (130): porten pinnet `11:20` som et LITERAL, og
    da M-33 la seg på 11:35 måtte statussveipen flytte til 12:05. Et
    literal som må rettes hver gang stigen vokser, måler ikke det den
    later som — det den skal måle er REKKEFØLGEN.

    Nå leses begge klokkeslettene og sammenlignes. Da holder porten
    uansett hvor stigen ender, og den faller hvis noen legger en sveip
    BAK statussveipen — som er den eneste feilen den kan fange.
    """
    sti_t = ROT / "deploy" / "staging" / "disponit-ehfsveip.timer"
    timer = sti_t.read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 10:05:00 UTC" in timer
    assert "Persistent=true" in timer
    sti_status = (ROT / "deploy" / "staging"
                  / "disponit-sveipestatus.timer")
    status = sti_status.read_text(encoding="utf-8")
    egen = re.search(r"OnCalendar=\*-\*-\* (\d\d:\d\d):00 UTC", timer)
    bak = re.search(r"OnCalendar=\*-\*-\* (\d\d:\d\d):00 UTC", status)
    assert egen and bak, "et klokkeslett mangler"
    assert bak.group(1) > egen.group(1), (
        f"sveipestatus ({bak.group(1)}) står ikke etter EHF-sveipen"
        f" ({egen.group(1)})")
    sti_s = ROT / "deploy" / "staging" / "disponit-ehfsveip.service"
    tjeneste = sti_s.read_text(encoding="utf-8")
    assert ("LoadCredential=DISPONIT_EHFSVEIP_URL:"
            "/etc/disponit/ehfsveip/DISPONIT_EHFSVEIP_URL"
            in tjeneste)
    # …og beskrivelsen navngir SIN EGEN jobb (arvefeilen fra 116–118).
    assert "EHF-sveip" in tjeneste.split("Description=")[1][:70]
    for arvet in ("adresser", "uavklarte treff", "udekkede krav",
                  "estimater uten grunnlag", "uhenviste funn"):
        assert arvet not in tjeneste, arvet


def test_sveipen_staar_i_flaaterosteret():
    from drift.sveipestatus import FLAATEN
    assert FLAATEN.get("ehfsveip") == 30, FLAATEN


def test_ui_axe_dekning():
    """`ui_axe_alvorlige_brudd` — flaten er registrert der axe kjører."""
    app_js = (ROT / "platform" / "core" / "ui" / "static" / "js"
              / "app.js").read_text(encoding="utf-8")
    assert "ehf: visEhf," in app_js
    sitekart = (ROT / "platform" / "core" / "ui" / "static" / "js"
                / "sitekart.js").read_text(encoding="utf-8")
    assert '{ nokkel: "ehf", scope: "okonomi:read"' in sitekart
