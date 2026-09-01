"""M-12 identitets- og tilgangsagent v1 (migrasjon 097) — kravgrensens
åtte invarianter, målt.

Grensen `m12-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR koden
(§0-regelen). Hver invariant har en port her, og hver port måler BÅDE at
den lovlige veien virker og at bruddet avvises — en port som bare måler
det som skal gå igjennom har ikke målt en invariant.

  * `tilgang_endret_utenfor_registeret` — V1-DOMMEN, og den viktigste
    porten i modulen. Målt STATISK (ingen identitetsklient i modulens
    Python, ingen DML mot en fremmed tabell i 097 utenom den ene
    evidensinnsettingen) OG FUNKSJONELT (radantallet utenfor modulens
    egne tre lagre er uendret etter en sveip — også i evidenskjeden).
  * `tilgang_uten_eier` — direkte DML uten eier avvises (NOT NULL og
    fremmednøkkel), og døren avviser en «eier» som ikke er aktivt medlem
    av tenanten. To lag, samme sannhet.
  * `tilgang_uten_hjemmel` — direkte DML uten hjemmel avvises, og en
    hjemmel som bare er en TABULATOR avvises også. Det siste er ikke en
    kuriositet: `length(btrim(x)) > 0` slipper en tabulator gjennom,
    fordi `btrim` som standard bare trimmer mellomrom. Funnet i M-9;
    CHECKen her er derfor skrevet `~ '[^[:space:]]'`.
  * `utlopt_gjennomgang_uten_funn` — en tilgang forbi fristen gir funn,
    og sveipen kjørt to ganger gir ETT funn. Positiv kontroll ved siden
    av: en tilgang innenfor fristen gir INGEN.
  * `funntype_utenfor_lukket_sett` — CHECKen avviser en ukjent funntype,
    og godtar hver av de fire i settet.
  * `tenantlekkasje_i_tilgangsregister` — tenant A ser aldri tenant Bs
    tilganger, verken ved direkte DML eller over API-et.
  * `registerrad_endret_etter_innsetting` — vakten avviser enhver
    endring av registerradens substans og enhver sletting, for enhver
    rolle. Det ENE som kan flyttes er gjennomgangsmerket, framover, med
    en navngitt aktør.
  * `ui_axe_alvorlige_brudd` — bor i
    `platform/core/ui/test/tilgang.test.js` (jsdom + axe-core), som
    kjøres av `npm test`, ikke herfra. Porten her måler at den FINNES og
    faktisk kjører axe over flaten.

I tillegg: sveipen som DRIFTSJOBB (hoppet over ved overlapp, alarm etter
TO sammenhengende feilede kjøringer, én JSON-linje med tallene),
SP-10-premisset (ren DDL, byte-bundet, fasit-pinnet), rettighetsspeilet
i `migrer.py`, dør-eierskapet og SP-1-porten målt i BASEN.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT, dekker,  # noqa: F401
                       app, klient, migrator, miljo)
from .test_m37 import _sett_kontekst

ROT = Path(__file__).resolve().parents[3]
MODULROT = ROT / "platform" / "modules" / "m12_tilgang"
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "097_m12_tilgangsregister.sql")
SVEIPEN = ROT / "platform" / "drift" / "tilgangssveip.py"
KJOREREN = ROT / "platform" / "drift" / "kjor_tilgangssveip.py"
API_MODUL = ROT / "platform" / "core" / "api" / "tilgang.py"
FLATEN = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
          / "tilgang.js")
UI_TEST = ROT / "platform" / "core" / "ui" / "test" / "tilgang.test.js"

#: Modulens EGNE lagre. Alt annet i basen er «utenfor», og den
#: forskjellen er hele invariant nummer én.
EGNE_LAGRE = ("tilgangsobjekt", "tilgang", "tilgangsfunn")

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

#: Sveipens EGEN innlogging. Den er den eneste rollen med EXECUTE på
#: `m12_sveip_gjennomganger` — at migrator IKKE har det, er selv en
#: måling: kryss-tenant-sveipen er sveiperollens og ingen annens.
SVEIP_DSN = os.environ.get("DISPONIT_TEST_TILGANGSSVEIP_DSN", "")
sveiperolle = pytest.mark.skipif(
    not SVEIP_DSN, reason="DISPONIT_TEST_TILGANGSSVEIP_DSN ikke satt")

#: Hvilken INVARIANT hver test dekker. Egen akse, og med vilje ikke
#: `test_api.DEKNING`: den er FEILVEI-registeret, og en invariant er ikke
#: en feilvei — å låne registeret ville gjort begge portene til noe annet
#: enn de er (M-9s form).
M12_DEKNING: dict[str, list[str]] = {}


def invariant(*navn: str):
    """Merker en test som dekning for én eller flere M-12-invarianter.

    Merkelappen er ikke dokumentasjon: `test_grensen_dekker_...` under
    krever at HVER invariant i `M12_INVARIANTER` har minst én test. En
    invariant uten test er en formulering.
    """
    def dekorator(fn):
        for n in navn:
            M12_DEKNING.setdefault(n, []).append(fn.__name__)
        return fn
    return dekorator


# ---------------------------------------------------------------------------
# Riggen
# ---------------------------------------------------------------------------

def _rt():
    """Runtime-rollen — den som HAR EXECUTE på de fem API-dørene og ingen
    tabellrettighet på registeret."""
    from db.pg import koble
    return koble(DSN)


def _sv():
    """Sveiperollen — den som har EXECUTE på sveipen og ingenting annet."""
    from db.pg import koble
    return koble(SVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    """Egen tenant per test. Sveipen er kryss-tenant og ser HELE basen, så
    en delt tenant ville gjort testene rekkefølgeavhengige — og en test
    som består fordi naboen ryddet er ingen port."""
    return f"t-m12-{merke}-{secrets.token_hex(4)}"


def _bruker(m, tenant, *, navn=None, aktiv=True, roller=("admin",)):
    """En identitet med medlemskap i tenanten."""
    profil = {"visningsnavn": navn} if navn else {}
    _sett_kontekst(m, tenant)
    bid = m.execute(
        "INSERT INTO brukeridentitet (issuer, sub, profil)"
        " VALUES ('https://m12.test', %s, %s::jsonb) RETURNING bruker_id",
        ("s12-" + secrets.token_hex(6), json.dumps(profil))).fetchone()[0]
    m.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller, aktiv)"
        " VALUES (%s,%s,%s,%s)", (tenant, bid, list(roller), aktiv))
    m.commit()
    return bid


def _objekt(c, tenant, *, system="Fagsystem", navn="Modul A",
            kritikalitet="hoy", oid=None, aktor="u-test"):
    """Ett objekt gjennom døren, som runtime."""
    oid = oid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute("SELECT m12_registrer_objekt(%s,%s,%s,%s,%s,%s)",
              (tenant, oid, system, navn, kritikalitet, aktor))
    c.commit()
    return oid


def _tilgang(c, tenant, oid, eier, *, subjekt="konto@eksempel.test",
             subjekttype="person", niva="admin", hjemmel="Rolle R-1",
             dogn=90, tid=None, aktor="u-test"):
    """Én tilgang gjennom døren, som runtime."""
    tid = tid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m12_registrer_tilgang(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant, tid, oid, subjekt, subjekttype, niva, eier, hjemmel,
         dogn, aktor))
    c.commit()
    return tid


def _forfalt(m, tenant, oid, eier, *, dager=400, subjekt="gammel@x.test",
             hjemmel="Rolle R-0", dogn=1):
    """En tilgang som ER forfalt, skrevet direkte som eieren.

    BAKDATERINGEN MÅ SKJE VED INNSETTINGEN, og det er ikke en snarvei —
    det er selve invarianten: `opprettet_dato` er FROSSET av vakten, så
    en test som forsøkte å bakdatere en eksisterende rad ville blitt
    avvist. Riggen er derfor tvunget til å gå den ene veien registeret
    tillater, og det er riktig.
    """
    tid = uuid.uuid4()
    _sett_kontekst(m, tenant)
    m.execute("SET LOCAL ROLE disponit_tilgang_eier")
    m.execute(
        "INSERT INTO tilgang (tenant, tilgang_id, objekt_id, subjekt,"
        " subjekttype, niva, eier_bruker_id, hjemmel, gjennomgang_dogn,"
        " opprettet_dato, opprettet_av)"
        " VALUES (%s,%s,%s,%s,'person','les',%s,%s,%s,"
        "         current_date - %s, 'rigg')",
        (tenant, tid, oid, subjekt, eier, hjemmel, dogn, dager))
    m.commit()
    return tid


def _sveip(v, grense=500):
    """Kjør sveipen én gang, som sveiperollen.

    TALLENE ER PLATTFORMVIDE, ikke tenantens: sveipen er kryss-tenant per
    konstruksjon og ser hver tilgang i basen, også dem andre tester har
    lagt igjen. Assertene under teller derfor tenantens EGNE funn
    (`_funn`), ikke returverdien.
    """
    v.execute("SELECT set_config('disponit.tenant','',true)")
    rad = v.execute("SELECT * FROM m12_sveip_gjennomganger(%s)",
                    (grense,)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant, *, bare_apne=True):
    _sett_kontekst(m, tenant)
    m.execute("SET LOCAL ROLE disponit_tilgang_eier")
    rader = m.execute(
        "SELECT tilgang_id, funntype, subjekt, system, frist, apen"
        "  FROM tilgangsfunn WHERE tenant=%s"
        + ("   AND apen" if bare_apne else "")
        + " ORDER BY funntype, subjekt", (tenant,)).fetchall()
    m.rollback()
    return rader


def _radbilde(m, tenant) -> dict[str, int]:
    """Radantallet i HVER tabell i public utenom modulens tre egne lagre,
    lest med tenantkonteksten satt.

    Dette er måleinstrumentet for invariant nummer én. Konteksten er
    satt med vilje: da teller RLS-tabellene tenantens egne rader, og en
    sveip som skrev noe som helst i tenantens navn — en evidensrad, et
    varsel, en oppdragsrad — ville flyttet et av tallene. Tabeller
    migrator ikke har SELECT på (de privilegerte eiernes) hoppes over,
    og at de gjør det er ufarlig: modulen har ingen rettigheter der
    heller.
    """
    _sett_kontekst(m, tenant)
    tabeller = [r[0] for r in m.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n"
        "   ON n.oid = c.relnamespace"
        " WHERE n.nspname='public' AND c.relkind IN ('r','p')"
        " ORDER BY c.relname").fetchall()]
    ut: dict[str, int] = {}
    for tabell in tabeller:
        if tabell in EGNE_LAGRE:
            continue
        try:
            ut[tabell] = m.execute(
                f'SELECT count(*) FROM "{tabell}"').fetchone()[0]
        except psycopg.Error:
            m.rollback()
            _sett_kontekst(m, tenant)
    m.rollback()
    return ut


# ---------------------------------------------------------------------------
# INVARIANT 1: tilgang_endret_utenfor_registeret — V1-DOMMEN
# ---------------------------------------------------------------------------

@invariant("tilgang_endret_utenfor_registeret")
def test_invariant_ingen_provisjoneringsvei_i_koden():
    """DEN STATISKE HALVDELEN, og den viktigste porten i modulen.

    Katalogteksten lover JML — joiner, mover, leaver — altså at modulen
    OPPRETTER, FLYTTER og FJERNER tilganger automatisk. v1 gjør ingen av
    delene, og det skal ikke kunne gli inn ved et uhell. Tre lag måles:

      1. INGEN IDENTITETSKLIENT i modulens Python. Ingen HTTP-klient,
         ingen Graph/Entra/LDAP-bibliotek, ingen socket. En modul som
         ikke kan NÅ en identitetsleverandør kan ikke provisjonere i
         den.
      2. INGEN DML MOT EN FREMMED TABELL i 097. Hver `INSERT`/`UPDATE`/
         `DELETE` i migrasjonen treffer et av modulens tre egne lagre —
         med NØYAKTIG ett unntak, `revisjonslogg`, som er evidenskjeden
         og bare skrives fra `m12_evidens`.
      3. SVEIPEN SKRIVER IKKE ENGANG EVIDENS. `m12_evidens` kalles av de
         to skrivedørene (menneskelige handlinger) og ALDRI av
         sveipefunksjonene — ellers måtte den funksjonelle porten under
         unnta sin egen jobb.

    MUTASJONEN SOM DREPER DENNE: legg et `import httpx` i `tilgang.py`,
    eller la sveipen kalle `m12_evidens`.
    """
    forbudte = ("httpx", "requests", "urllib", "http.client", "socket",
                "msal", "ldap", "graph", "azure", "boto3", "aiohttp")
    for fil in (API_MODUL, SVEIPEN, KJOREREN):
        kilde = fil.read_text(encoding="utf-8")
        kode = "\n".join(ln for ln in kilde.splitlines()
                         if not ln.lstrip().startswith("#"))
        for navn in forbudte:
            assert f"import {navn}" not in kode, \
                f"{fil.name}: importerer {navn} — v1 provisjonerer ingenting"

    sql = MIGRASJON.read_text(encoding="utf-8")
    kode = "\n".join(ln for ln in sql.splitlines()
                     if not ln.lstrip().startswith("--"))
    # TRIGGERDEFINISJONER OG RETTIGHETSSETNINGER FJERNES FØRST.
    # `BEFORE UPDATE OR DELETE ON tilgangsobjekt` og
    # `GRANT SELECT, INSERT, UPDATE ON tilgang TO ...` bærer begge ordet
    # UPDATE uten å være DML, og en port som leser «OR» og «ON» som
    # tabellnavn feiler på sin egen syntaks i stedet for på funnet.
    uten_trigger = re.sub(r"CREATE TRIGGER[\s\S]*?;", " ", kode,
                          flags=re.IGNORECASE)
    dml_kode = re.sub(r"(?im)^\s*(?:GRANT|REVOKE)\b[\s\S]*?;", " ",
                      uten_trigger)
    mål = set()
    for monster in (r"\bINSERT\s+INTO\s+(?:public\.)?([a-z_][a-z0-9_]*)",
                    r"\bDELETE\s+FROM\s+(?:public\.)?([a-z_][a-z0-9_]*)",
                    r"\bUPDATE\s+(?:public\.)?([a-z_][a-z0-9_]*)"):
        for m in re.finditer(monster, dml_kode, re.IGNORECASE):
            mål.add(m.group(1).lower())
    lovlige = set(EGNE_LAGRE) | {"revisjonslogg"}
    assert mål <= lovlige, \
        f"097 skriver til fremmede tabeller: {sorted(mål - lovlige)}"
    assert "revisjonslogg" in mål, \
        "evidenskjeden skrives ikke i det hele tatt — porten måler ingenting"

    # …og evidensen skrives KUN av de menneskelige dørene.
    for navn in ("m12_sveip_for_tenant", "m12_sveip_gjennomganger"):
        start = kode.index(f"CREATE FUNCTION {navn}(")
        slutt = kode.index("REVOKE ALL ON FUNCTION " + navn, start)
        assert "m12_evidens" not in kode[start:slutt], \
            f"{navn} skriver evidens — da er den ikke en ren observasjon"


@pg
@sveiperolle
@invariant("tilgang_endret_utenfor_registeret")
def test_invariant_sveipen_rorer_ingenting_utenfor_egne_lagre(migrator):
    """DEN FUNKSJONELLE HALVDELEN: radantallet utenfor modulens tre egne
    lagre er UENDRET etter en sveip.

    Målt over HVER tabell i `public` (utenom de tre), med
    tenantkonteksten satt — så en evidensrad, et varsel eller en
    oppdragsrad skrevet i tenantens navn ville flyttet et tall.

    OG PORTEN HAR EN POSITIV KONTROLL: sveipen gjorde faktisk noe. En
    kjøring som ikke reiste et eneste funn ville bestått denne testen
    uten å ha målt noe som helst — det er den enkleste måten en slik
    port kan være grønn og verdiløs på.

    MUTASJONEN SOM DREPER DENNE: la sveipen kalle `m12_evidens`, eller
    la den køe et varsel om utløpte gjennomganger.
    """
    ten = _tenantnavn("utenfor")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _sv()
    try:
        oid = _objekt(c, ten)
        _forfalt(migrator, ten, oid, eier)
        for_ = _radbilde(migrator, ten)
        rad = _sveip(v)
        etter = _radbilde(migrator, ten)
        assert rad[0] >= 1, "sveipen så ingen tenanter i det hele tatt"
        # POSITIV KONTROLL: sveipen GJORDE noe i sitt eget lager.
        funn = _funn(migrator, ten)
        assert len(funn) == 1 and funn[0][1] == "gjennomgang_utlopt", funn
        # …og INGENTING utenfor det.
        endret = {t: (for_[t], etter[t]) for t in for_
                  if for_.get(t) != etter.get(t)}
        assert endret == {}, \
            f"sveipen endret rader utenfor egne lagre: {endret}"
    finally:
        c.close()
        v.close()


# ---------------------------------------------------------------------------
# INVARIANT 2: tilgang_uten_eier
# ---------------------------------------------------------------------------

@pg
@invariant("tilgang_uten_eier")
def test_invariant_tilgang_uten_eier(migrator):
    """«Hvem eier denne tilgangen» er hele spørsmålet registeret finnes
    for, og i v1 er svaret en NOT NULL — ikke en rapport. To lag:

      1. DIREKTE DML, som eieren av tabellen: en INSERT uten
         `eier_bruker_id` avvises av NOT NULL, og en med en ukjent
         bruker-id av fremmednøkkelen. Det er den bindende porten — den
         gjelder enhver skrivevei, også en fremtidig som glemmer døren.
      2. DØREN: en «eier» som ikke er AKTIVT MEDLEM av tenanten avvises.
         FK-en alene sier bare at id-en finnes ET STED i plattformen, og
         en tilgang eid av en fremmed tenants bruker er nøyaktig like
         lite etterprøvd som en uten eier.

    MUTASJONEN SOM DREPER DENNE: fjern NOT NULL på `eier_bruker_id`,
    eller fjern medlemskapssjekken i `m12_registrer_tilgang`.
    """
    ten = _tenantnavn("eier")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        oid = _objekt(c, ten)

        # 1a. Ingen eier i det hele tatt.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        with pytest.raises(psycopg.errors.NotNullViolation):
            migrator.execute(
                "INSERT INTO tilgang (tenant, tilgang_id, objekt_id,"
                " subjekt, subjekttype, niva, hjemmel, gjennomgang_dogn,"
                " opprettet_av)"
                " VALUES (%s,%s,%s,'x','person','les','h',30,'t')",
                (ten, uuid.uuid4(), oid))
        migrator.rollback()

        # 1b. En eier som ikke finnes som identitet.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            migrator.execute(
                "INSERT INTO tilgang (tenant, tilgang_id, objekt_id,"
                " subjekt, subjekttype, niva, eier_bruker_id, hjemmel,"
                " gjennomgang_dogn, opprettet_av)"
                " VALUES (%s,%s,%s,'x','person','les','bid_finnes_ikke',"
                "         'h',30,'t')", (ten, uuid.uuid4(), oid))
        migrator.rollback()

        # 2. Døren: en bruker fra en ANNEN tenant er ikke en eier her.
        annen = _tenantnavn("eier-annen")
        fremmed = _bruker(migrator, annen)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _tilgang(c, ten, oid, fremmed)
        c.rollback()
        # …og et INAKTIVT medlem av EGEN tenant er heller ikke en eier:
        # en tilgang eid av en som har sluttet er en tilgang ingen
        # etterprøver.
        sovende = _bruker(migrator, ten, aktiv=False)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _tilgang(c, ten, oid, sovende)
        c.rollback()

        # POSITIV KONTROLL: den lovlige veien går igjennom.
        tid = _tilgang(c, ten, oid, eier)
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        assert migrator.execute(
            "SELECT eier_bruker_id FROM tilgang WHERE tenant=%s"
            "   AND tilgang_id=%s", (ten, tid)).fetchone()[0] == eier
        migrator.rollback()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# INVARIANT 3: tilgang_uten_hjemmel
# ---------------------------------------------------------------------------

@pg
@invariant("tilgang_uten_hjemmel")
def test_invariant_tilgang_uten_hjemmel(migrator):
    """En tilgang ingen kan begrunne er et funn selv om den har en eier —
    og i v1 er den URESPRESENTERBAR.

    DEN SKARPE DELEN ER TABULATOREN. `length(btrim(hjemmel)) > 0` ser
    riktig ut og er det ikke: `btrim` trimmer som standard BARE
    mellomrom, så en hjemmel som er ett tabulatortegn ville sluppet
    gjennom — en tom hjemmel med en usynlig maske på. Funnet i M-9;
    CHECKen her er derfor `hjemmel ~ '[^[:space:]]'`, som er sann bare
    når det finnes minst ett IKKE-blanktegn.

    MUTASJONEN SOM DREPER DENNE: bytt CHECKen tilbake til
    `length(btrim(hjemmel)) > 0`.
    """
    ten = _tenantnavn("hjemmel")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        oid = _objekt(c, ten)
        # NBSP (U+00A0) står i listen fordi den ER fanget: `[[:space:]]`
        # alene er ASCII-blanktegn i denne basens ctype, og det harde
        # mellomrommet man får med på kjøpet ved lim inn fra Word eller
        # en nettside slapp gjennom den rene klassen. Funnet under
        # byggingen av 097 — nøyaktig samme feilklasse som tabulatoren,
        # med en enda vanligere opprinnelse. Klassen bærer derfor NBSP
        # eksplisitt.
        for tom in ("", "   ", "\t", "\t\t ", "\n", "\u00a0",
                    " \u00a0\t"):
            _sett_kontekst(migrator, ten)
            migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
            with pytest.raises(psycopg.errors.CheckViolation):
                migrator.execute(
                    "INSERT INTO tilgang (tenant, tilgang_id, objekt_id,"
                    " subjekt, subjekttype, niva, eier_bruker_id, hjemmel,"
                    " gjennomgang_dogn, opprettet_av)"
                    " VALUES (%s,%s,%s,'x','person','les',%s,%s,30,'t')",
                    (ten, uuid.uuid4(), oid, eier, tom))
            migrator.rollback()

        # …og NULL er heller ikke en hjemmel.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        with pytest.raises(psycopg.errors.NotNullViolation):
            migrator.execute(
                "INSERT INTO tilgang (tenant, tilgang_id, objekt_id,"
                " subjekt, subjekttype, niva, eier_bruker_id,"
                " gjennomgang_dogn, opprettet_av)"
                " VALUES (%s,%s,%s,'x','person','les',%s,30,'t')",
                (ten, uuid.uuid4(), oid, eier))
        migrator.rollback()

        # POSITIV KONTROLL: en hjemmel med innhold slipper igjennom —
        # også en som BEGYNNER med blanktegn. Kravet er at det finnes
        # noe, ikke at teksten er pen.
        tid = _tilgang(c, ten, oid, eier, hjemmel="\tVedtak 2026-4")
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        assert migrator.execute(
            "SELECT hjemmel FROM tilgang WHERE tenant=%s AND tilgang_id=%s",
            (ten, tid)).fetchone()[0] == "\tVedtak 2026-4"
        migrator.rollback()
    finally:
        c.close()


def test_ingen_ikke_tom_check_bruker_btrim_formen():
    """STATISK PORT på hele migrasjonen, ikke bare på `hjemmel`.

    M-9-funnet var ikke at ÉN kolonne hadde feil form — det var at
    formen `length(btrim(x)) > 0` ser riktig ut og slipper en tabulator.
    Porten måler derfor at 097 ikke bruker den formen på noen av de
    kolonnene der tomhet er dommen. `tenant` er unntaket og står i
    listen med vilje: den formen er kopiert fra 089-096-familien for at
    kolonnen skal ha NØYAKTIG samme CHECK som i naboene, og en tenant er
    aldri kundens frie tekst.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    kode = "\n".join(ln for ln in sql.splitlines()
                     if not ln.lstrip().startswith("--"))
    btrim = re.findall(r"length\(btrim\(([a-z_]+)\)\)\s*>\s*0", kode)
    assert set(btrim) <= {"tenant"}, \
        f"097 bruker btrim-formen på {sorted(set(btrim) - {'tenant'})}" \
        " — den slipper en tabulator gjennom"
    for kolonne in ("system", "navn", "subjekt", "hjemmel"):
        assert f"{kolonne} ~ '[^[:space:]" + chr(92) + "u00a0]'" in kode, \
            f"{kolonne} mangler den ikke-tomme CHECKen (med NBSP i klassen)"


# ---------------------------------------------------------------------------
# INVARIANT 4: utlopt_gjennomgang_uten_funn
# ---------------------------------------------------------------------------

@pg
@sveiperolle
@invariant("utlopt_gjennomgang_uten_funn")
def test_invariant_utlopt_gjennomgang_uten_funn(migrator):
    """En tilgang som har stått urørt lenger enn sin egen
    gjennomgangsfrist er et FUNN, ikke en rad som stille blir gammel.

    Og porten måler BEGGE retninger:

      * den forfalte gir funn,
      * den som er innenfor fristen gir INGEN — uten den positive
        kontrollen kunne sveipen ha reist funn på alt og bestått,
      * SVEIPEN KJØRT TO GANGER GIR ETT FUNN. Idempotensen bor i basen:
        andre kjøring flytter `sist_sett_sveip` og skriver ingen ny rad.
        En daglig sveip over en tilgang som har vært uetterprøvd i et år
        skal gi ETT funn, ikke 365.

    MUTASJONEN SOM DREPER DENNE: fjern `NOT EXISTS`-leddet fra
    innsettingen i `m12_sveip_for_tenant`, eller ta `funntype` ut av
    funnets primærnøkkel.
    """
    ten = _tenantnavn("utlopt")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _sv()
    try:
        oid = _objekt(c, ten)
        forfalt = _forfalt(migrator, ten, oid, eier, subjekt="forfalt@x.test")
        # …og en som er godt innenfor fristen.
        _tilgang(c, ten, oid, eier, subjekt="frisk@x.test", niva="les",
                 dogn=365)

        _sveip(v)
        funn = _funn(migrator, ten)
        assert len(funn) == 1, funn
        assert funn[0][0] == forfalt and funn[0][1] == "gjennomgang_utlopt"
        assert funn[0][2] == "forfalt@x.test"
        forst = migrator.execute("SELECT 1").fetchone()  # noqa: F841
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        sett1 = migrator.execute(
            "SELECT forst_sett, sist_sett_sveip FROM tilgangsfunn"
            " WHERE tenant=%s", (ten,)).fetchone()
        migrator.rollback()

        # ANDRE KJØRING: ETT funn fortsatt, med samme førstegangsobservasjon
        # og en NYERE ferskhet.
        _sveip(v)
        assert len(_funn(migrator, ten)) == 1, "sveipen doblet funnet"
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        sett2 = migrator.execute(
            "SELECT forst_sett, sist_sett_sveip FROM tilgangsfunn"
            " WHERE tenant=%s", (ten,)).fetchone()
        migrator.rollback()
        assert sett2[0] == sett1[0], "førstegangsobservasjonen ble skrevet om"
        assert sett2[1] >= sett1[1], "ferskheten gikk bakover"
    finally:
        c.close()
        v.close()


@pg
@sveiperolle
@invariant("utlopt_gjennomgang_uten_funn")
def test_funnet_lukkes_naar_noen_faktisk_gjennomgar(migrator):
    """PORTEN ER IKKE «ALDRI LUKK». En registrert gjennomgang flytter
    fristen fram, og neste sveip LUKKER funnet — men raden består: at en
    tilgang VAR uetterprøvd er også historikk, og det er den historikken
    som gjør at «vi har ryddet opp» kan etterprøves.

    MUTASJONEN SOM DREPER DENNE: la lukkesteget slette raden i stedet
    for å lukke den, eller fjern lukkesteget helt.
    """
    ten = _tenantnavn("lukk")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _sv()
    try:
        oid = _objekt(c, ten)
        tid = _forfalt(migrator, ten, oid, eier, dogn=30)
        _sveip(v)
        assert len(_funn(migrator, ten)) == 1

        _sett_kontekst(c, ten)
        frist = c.execute("SELECT m12_registrer_gjennomgang(%s,%s,%s)",
                          (ten, tid, "u-gjennomgaar")).fetchone()[0]
        c.commit()
        assert frist is not None

        _sveip(v)
        assert _funn(migrator, ten) == [], "funnet ble ikke lukket"
        # …men raden står, lukket og med et tidspunkt.
        alle = _funn(migrator, ten, bare_apne=False)
        assert len(alle) == 1 and alle[0][5] is False, alle
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        assert migrator.execute(
            "SELECT lukket_ts IS NOT NULL FROM tilgangsfunn"
            " WHERE tenant=%s", (ten,)).fetchone()[0] is True
        migrator.rollback()
    finally:
        c.close()
        v.close()


# ---------------------------------------------------------------------------
# INVARIANT 5: funntype_utenfor_lukket_sett
# ---------------------------------------------------------------------------

@pg
@invariant("funntype_utenfor_lukket_sett")
def test_invariant_funntype_utenfor_lukket_sett(migrator):
    """CHECKen avviser en funntype utenfor settet, og godtar hver av de
    fire i det.

    TRE AV DE FIRE ER UREPRESENTERBARE I v1 og kan derfor ikke oppstå av
    en sveip: `uten_eier` er utelukket av NOT NULL, `uten_hjemmel` av den
    ikke-tomme CHECKen, og `ukjent_objekt` av fremmednøkkelen. At de ikke
    KAN oppstå er poenget — en invariant som holder er en funntype som
    står tom. De står likevel i settet fordi den dagen tilgangene LESES
    INN fra en identitetsleverandør (v2) kommer det rader som mangler
    begge deler, og da skal funntypen alt finnes framfor at en migrasjon
    må utvide en CHECK under en bebodd funntabell.

    Porten måler nettopp det: at settet ER lukket, og at alle fire er i
    det.
    """
    ten = _tenantnavn("funntype")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        oid = _objekt(c, ten)
        tid = _tilgang(c, ten, oid, eier)
    finally:
        c.close()

    for lovlig in ("uten_eier", "uten_hjemmel", "gjennomgang_utlopt",
                   "ukjent_objekt"):
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        migrator.execute(
            "INSERT INTO tilgangsfunn (tenant, tilgang_id, funntype,"
            " subjekt, system) VALUES (%s,%s,%s,'x','y')",
            (ten, tid, lovlig))
        migrator.rollback()          # hver type prøves for seg

    for ulovlig in ("for_bred_tilgang", "UTEN_EIER", "", "gjennomgang"):
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(
                "INSERT INTO tilgangsfunn (tenant, tilgang_id, funntype,"
                " subjekt, system) VALUES (%s,%s,%s,'x','y')",
                (ten, tid, ulovlig))
        migrator.rollback()


def test_funntypesettet_i_migrasjonen_er_de_fire():
    """…og settet er nøyaktig de fire, målt på migrasjonens tekst. En
    femte funntype som glir inn uten en sveip som kan reise den, er en
    kolonne som lover noe registeret ikke gjør."""
    sql = MIGRASJON.read_text(encoding="utf-8")
    m = re.search(
        r"funntype TEXT NOT NULL\s*\n\s*CHECK \(funntype IN \(([^)]+)\)\)",
        sql)
    assert m, "fant ikke funntype-CHECKen i 097"
    typer = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    assert typer == {"uten_eier", "uten_hjemmel", "gjennomgang_utlopt",
                     "ukjent_objekt"}, typer


# ---------------------------------------------------------------------------
# INVARIANT 6: tenantlekkasje_i_tilgangsregister
# ---------------------------------------------------------------------------

@pg
@invariant("tenantlekkasje_i_tilgangsregister")
def test_invariant_tenantlekkasje_i_tilgangsregister(migrator):
    """Tenant A ser aldri tenant Bs tilganger — verken ved direkte DML
    eller gjennom dørene.

    Tre lag måles:
      1. RLS: med A-kontekst er B-radene ikke der, heller ikke for
         tabellens eier (FORCE ROW LEVEL SECURITY).
      2. SP-1: lesedøren kalt med B som parameter, men A i konteksten,
         avvises av `krev_tenantkontekst` — parameteret er aldri
         kallerens frie valg.
      3. Kryss-tenant-policyen er SNEVER: så snart en tenantkontekst
         står, ser eieren bare den ene tenanten. Sveipens vindu finnes
         nøyaktig når det ikke er noen kontekst å bryte.

    MUTASJONEN SOM DREPER DENNE: gjør `m12_sveip_tenantliste`
    betingelsesløs (`USING (true)`), eller fjern `krev_tenantkontekst`
    fra `m12_tilgangsbilde`.
    """
    a, b = _tenantnavn("lek-a"), _tenantnavn("lek-b")
    eier_a, eier_b = _bruker(migrator, a), _bruker(migrator, b)
    c = _rt()
    try:
        oid_a = _objekt(c, a, system="A-system")
        oid_b = _objekt(c, b, system="B-system")
        tid_a = _tilgang(c, a, oid_a, eier_a, subjekt="a@x.test")
        tid_b = _tilgang(c, b, oid_b, eier_b, subjekt="b@x.test")

        # 1. RLS, direkte DML som eieren.
        _sett_kontekst(migrator, a)
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        synlige = [r[0] for r in migrator.execute(
            "SELECT tilgang_id FROM tilgang ORDER BY tilgang_id").fetchall()]
        migrator.rollback()
        assert synlige == [tid_a], synlige

        # 3. Kryss-tenant-policyen slår seg AV så snart konteksten står.
        #    Uten kontekst ser eieren begge (det er sveipens vindu) —
        #    men KUN på `tilgang`, og KUN for SELECT.
        migrator.execute("SELECT set_config('disponit.tenant','',true)")
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        uten = {r[0] for r in migrator.execute(
            "SELECT tilgang_id FROM tilgang").fetchall()}
        migrator.rollback()
        assert {tid_a, tid_b} <= uten, \
            "sveipens vindu finnes ikke — den ville aldri sett en tenant"
        # …og vinduet gjelder IKKE objektene eller funnene: sveipen gjør
        # alt arbeid med radens tenant satt, så et bredere vindu ville
        # vært autoritet ingen bruker.
        migrator.execute("SELECT set_config('disponit.tenant','',true)")
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        assert migrator.execute(
            "SELECT count(*) FROM tilgangsobjekt").fetchone()[0] == 0
        assert migrator.execute(
            "SELECT count(*) FROM tilgangsfunn").fetchone()[0] == 0
        migrator.rollback()

        # 2. SP-1: parameteret er ikke kallerens frie valg.
        _sett_kontekst(c, a)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM m12_tilgangsbilde(%s,%s)",
                      (b, 50)).fetchall()
        c.rollback()
        _sett_kontekst(c, a)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM m12_apne_funn(%s,%s)", (b, 50)).fetchall()
        c.rollback()

        # …og lesedøren i EGEN kontekst gir bare egne rader.
        _sett_kontekst(c, a)
        rader = c.execute(
            "SELECT tilgang_id, system FROM m12_tilgangsbilde(%s,%s)",
            (a, 50)).fetchall()
        c.rollback()
        assert [r[1] for r in rader] == ["A-system"]
        assert [r[0] for r in rader] == [tid_a]

        # …og at oid_b faktisk ble laget (ellers målte punkt 1 ingenting).
        assert oid_a != oid_b
    finally:
        c.close()


@pg
@invariant("tenantlekkasje_i_tilgangsregister")
def test_tenantlekkasje_over_api(migrator, klient):
    """Samme invariant, over HTTP: økten hos A får aldri se Bs tilganger.

    Tenanten kommer fra ØKTEN, aldri fra kroppen eller en parameter —
    her måles at det faktisk er slik hele veien ut til svaret.
    """
    b = _tenantnavn("api-b")
    eier_a = _bruker(migrator, TENANT)
    eier_b = _bruker(migrator, b)
    c = _rt()
    try:
        oid_a = _objekt(c, TENANT, system="A-over-API")
        oid_b = _objekt(c, b, system="B-over-API")
        _tilgang(c, TENANT, oid_a, eier_a, subjekt="a-api@x.test")
        _tilgang(c, b, oid_b, eier_b, subjekt="b-api@x.test")
    finally:
        c.close()
    cookie, _csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/tilgang", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    kropp = r.json()
    subjekter = [t["subjekt"] for t in kropp["tilganger"]]
    systemer = [o["system"] for o in kropp["objekter"]]
    assert "a-api@x.test" in subjekter
    assert "b-api@x.test" not in subjekter
    assert "A-over-API" in systemer
    assert "B-over-API" not in systemer


# ---------------------------------------------------------------------------
# INVARIANT 7: registerrad_endret_etter_innsetting
# ---------------------------------------------------------------------------

@pg
@invariant("registerrad_endret_etter_innsetting")
def test_invariant_registerrad_endret_etter_innsetting(migrator):
    """En registerrad endres ALDRI etter innsettingen — for enhver
    rolle, også tabellens egen eier.

    En annen hjemmel, et annet nivå eller et annet subjekt er en ANNEN
    TILGANG, ikke en redigering av denne. Uten den regelen kunne
    historikken bak et funn skifte mening under føttene på den som leser
    den: «hvem hadde admin på lønnsmappa i mars» ville hatt et annet svar
    etter at noen rettet raden.

    Objektet er TOTALT append-only av samme grunn: et system som skifter
    navn er et NYTT objekt, ellers ville tilgangene registrert til
    «Fileserver» stått som tilganger til noe helt annet.

    POSITIV KONTROLL: det ENE som kan flyttes — gjennomgangsmerket —
    flyttes faktisk, gjennom døren.

    MUTASJONEN SOM DREPER DENNE: ta `hjemmel` eller `niva` ut av
    vaktens frosne liste.
    """
    ten = _tenantnavn("frosset")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        oid = _objekt(c, ten)
        tid = _tilgang(c, ten, oid, eier)

        endringer = (
            ("hjemmel", "'en helt annen hjemmel'"),
            ("niva", "'les'"),
            ("subjekt", "'en annen konto'"),
            ("subjekttype", "'tjenestekonto'"),
            ("eier_bruker_id", "eier_bruker_id"),   # settes under
            ("gjennomgang_dogn", "365"),
            ("opprettet_dato", "current_date - 10"),
            ("opprettet_av", "'noen andre'"),
        )
        annen_eier = _bruker(migrator, ten)
        for kolonne, verdi in endringer:
            if kolonne == "eier_bruker_id":
                verdi = f"'{annen_eier}'"
            _sett_kontekst(migrator, ten)
            migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                migrator.execute(
                    f"UPDATE tilgang SET {kolonne} = {verdi}"
                    " WHERE tenant=%s AND tilgang_id=%s", (ten, tid))
            migrator.rollback()

        # DELETE avvises — også for eieren.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute("DELETE FROM tilgang WHERE tenant=%s"
                             "   AND tilgang_id=%s", (ten, tid))
        migrator.rollback()

        # Objektet er TOTALT append-only: verken UPDATE eller DELETE.
        for sql in ("UPDATE tilgangsobjekt SET navn='nytt navn'",
                    "UPDATE tilgangsobjekt SET kritikalitet='lav'",
                    "DELETE FROM tilgangsobjekt"):
            _sett_kontekst(migrator, ten)
            migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                migrator.execute(sql + " WHERE tenant=%s AND objekt_id=%s",
                                 (ten, oid))
            migrator.rollback()

        # POSITIV KONTROLL: gjennomgangsmerket KAN flyttes, gjennom døren.
        _sett_kontekst(c, ten)
        frist = c.execute("SELECT m12_registrer_gjennomgang(%s,%s,%s)",
                          (ten, tid, "u-attestant")).fetchone()[0]
        c.commit()
        assert frist is not None
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        rad = migrator.execute(
            "SELECT sist_gjennomgatt, sist_gjennomgatt_av, gjennomgang_frist"
            "  FROM tilgang WHERE tenant=%s AND tilgang_id=%s",
            (ten, tid)).fetchone()
        migrator.rollback()
        assert rad[1] == "u-attestant"
        assert rad[2] == frist
    finally:
        c.close()


@pg
@invariant("registerrad_endret_etter_innsetting")
def test_gjennomgangen_er_forfattet_aldri_avledet(migrator):
    """Gjennomgangsmerket er det ENE feltet vakten slipper — og det er
    gjerdet tre ganger:

      1. Uten en navngitt aktør i sesjonen avvises endringen. TIDEN
         ETTERPRØVER INGENTING: en jobb som skulle kvittert ut en
         gjennomgang fordi fristen nærmet seg har ingen aktør å skrive.
      2. `sist_gjennomgatt_av` MÅ være aktøren. Ellers kunne registeret
         si at én person attesterte det en annen klikket på.
      3. Datoen kan bare gå FRAMOVER. En dato som kan settes tilbake er
         en frist som kan skyves inn i fortiden for å lukke et funn —
         altså nøyaktig det registeret finnes for å hindre.

    MUTASJONEN SOM DREPER DENNE: fjern aktørkravet fra vakten, eller
    tillat at datoen går bakover.
    """
    ten = _tenantnavn("forfattet")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        oid = _objekt(c, ten)
        tid = _tilgang(c, ten, oid, eier)

        # 1. Ingen aktør i sesjonen.
        migrator.execute(
            "SELECT set_config('disponit.tenant',%s,true),"
            "       set_config('disponit.aktor','',true)", (ten,))
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "UPDATE tilgang SET sist_gjennomgatt = current_date,"
                "       sist_gjennomgatt_av = 'noen'"
                " WHERE tenant=%s AND tilgang_id=%s", (ten, tid))
        migrator.rollback()

        # 2. Aktøren og navnet i raden er ikke den samme.
        migrator.execute(
            "SELECT set_config('disponit.tenant',%s,true),"
            "       set_config('disponit.aktor','kari',true)", (ten,))
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "UPDATE tilgang SET sist_gjennomgatt = current_date,"
                "       sist_gjennomgatt_av = 'ola'"
                " WHERE tenant=%s AND tilgang_id=%s", (ten, tid))
        migrator.rollback()

        # Døren gjør det riktig.
        _sett_kontekst(c, ten)
        c.execute("SELECT m12_registrer_gjennomgang(%s,%s,%s)",
                  (ten, tid, "kari"))
        c.commit()

        # 3. …og datoen kan ikke settes tilbake etterpå.
        migrator.execute(
            "SELECT set_config('disponit.tenant',%s,true),"
            "       set_config('disponit.aktor','kari',true)", (ten,))
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "UPDATE tilgang SET sist_gjennomgatt = current_date - 30"
                " WHERE tenant=%s AND tilgang_id=%s", (ten, tid))
        migrator.rollback()

        # …og den kan ikke tas bort.
        migrator.execute(
            "SELECT set_config('disponit.tenant',%s,true),"
            "       set_config('disponit.aktor','kari',true)", (ten,))
        migrator.execute("SET LOCAL ROLE disponit_tilgang_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "UPDATE tilgang SET sist_gjennomgatt = NULL,"
                "       sist_gjennomgatt_av = NULL"
                " WHERE tenant=%s AND tilgang_id=%s", (ten, tid))
        migrator.rollback()

        # DØREN KREVER ET NAVN, ikke bare et tidspunkt.
        for tomt in (None, "", "   ", "\t"):
            _sett_kontekst(c, ten)
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                c.execute("SELECT m12_registrer_gjennomgang(%s,%s,%s)",
                          (ten, tid, tomt))
            c.rollback()
    finally:
        c.close()


@pg
def test_gjenspill_av_gjennomgangen_skriver_ikke_evidens_paa_nytt(migrator):
    """096s P1-lærdom, i M-12s form: en tapt respons + nytt klikk samme
    dag skal ikke skrive evidensraden en gang til.

    Vakten ville sluppet UPDATEen gjennom (datoen går ikke bakover når
    den er lik), så uten gjenspillgrenen ville evidenskjeden vist to
    etterprøvinger der det bare var én. To identiske attestasjoner ser ut
    som to ganger arbeid, og en revisjon som teller dem teller feil.
    """
    ten = _tenantnavn("gjenspill")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        oid = _objekt(c, ten)
        tid = _tilgang(c, ten, oid, eier)
        _sett_kontekst(c, ten)
        forste = c.execute("SELECT m12_registrer_gjennomgang(%s,%s,%s)",
                           (ten, tid, "kari")).fetchone()[0]
        c.commit()
        _sett_kontekst(c, ten)
        andre = c.execute("SELECT m12_registrer_gjennomgang(%s,%s,%s)",
                          (ten, tid, "kari")).fetchone()[0]
        c.commit()
        assert andre == forste
        _sett_kontekst(migrator, ten)
        assert migrator.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
            "   AND handling='tilgang.gjennomgatt'",
            (ten,)).fetchone()[0] == 1
        migrator.rollback()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# INVARIANT 8: ui_axe_alvorlige_brudd — porten bor i npm-suiten
# ---------------------------------------------------------------------------

@invariant("ui_axe_alvorlige_brudd")
def test_flateporten_finnes_og_kjorer_axe():
    """Axe-porten kjøres av `npm test`, ikke herfra. Denne porten måler
    at den FINNES og faktisk kaller axe over flaten — en invariant uten
    en test er en formulering."""
    assert UI_TEST.exists(), f"axe-porten mangler: {UI_TEST}"
    tekst = UI_TEST.read_text(encoding="utf-8")
    assert "alvorligeBrudd" in tekst and "visTilgang" in tekst
    assert "tablewrap" in tekst, \
        "flateporten måler ikke at tabellene ligger i en .tablewrap"
    assert "celle-tekst" in tekst and "celle-id" in tekst, \
        "flateporten måler ikke cellebredde-klassene (klynge 1s lærdom 2/3)"


def test_flaten_har_ingen_innerhtml_og_ingen_hardkodet_tekst():
    """Husets stående regel, målt statisk på DENNE flaten: aldri
    `innerHTML`, all tekst gjennom `t(…)`."""
    js = FLATEN.read_text(encoding="utf-8")
    assert "innerHTML" not in js
    for m in re.finditer(r'text:\s*"([^"]+)"', js):
        raise AssertionError(
            f"hardkodet tekst i flaten: {m.group(1)!r} — bruk t(...)")


def test_locale_paritet_for_ui_tilgang():
    """nb OG en. `t()` faller tilbake til NØKKELEN, ikke til nb — en
    manglende engelsk nøkkel ville vist `ui.tilgang.kolonne.hjemmel` midt
    i en tabell.

    Og LENKETEKST = FLATETITTEL i begge språk (klynge 1s lærdom 7): en
    generell port måler det for hver rute som har begge nøklene, men den
    er billig å måle her også, der begrunnelsen står.
    """
    nb = json.loads((ROT / "locales" / "nb.json").read_text(encoding="utf-8"))
    en = json.loads((ROT / "locales" / "en.json").read_text(encoding="utf-8"))
    mine = [k for k in nb if k.startswith("ui.tilgang.")
            or k == "ui.nav.tilgang"]
    assert len(mine) >= 40, f"for få nøkler i porten: {len(mine)}"
    mangler = [k for k in mine
               if not isinstance(en.get(k), str) or not en[k].strip()]
    assert not mangler, f"en.json mangler {mangler}"
    for sett in (nb, en):
        assert sett["ui.nav.tilgang"] == sett["ui.tilgang.tittel"]
    # …og hver nøkkel flaten slår opp, finnes.
    js = FLATEN.read_text(encoding="utf-8")
    for m in re.finditer(r't\("(ui\.tilgang\.[a-z_.]+)"', js):
        assert m.group(1) in nb, f"flaten slår opp ukjent nøkkel {m.group(1)}"


# ---------------------------------------------------------------------------
# Sveipen som DRIFTSJOBB: hoppet over, alarm etter to feil, JSON-linja
# ---------------------------------------------------------------------------

@pg
@sveiperolle
def test_overlappende_sveip_hopper_over_og_rorer_ikke_feiltelleren(
        migrator, tmp_path):
    """`artefaktrydding`-formen, ordrett: en kjøring som fant
    arbeidernøkkelen opptatt har verken lyktes eller feilet.

    Skrev den 0 her, ville en overlappende kjøring (manuell drift, flere
    verter, en henger som holder låsen) slettet en alt opptelt feil, og
    alarmen etter to sammenhengende feil ville aldri nådd frem.
    """
    from drift import kjor_tilgangssveip as kjorer
    from drift import tilgangssveip

    holder = psycopg.connect(MIGRATOR_DSN, autocommit=True)
    v = _sv()
    try:
        holder.execute("SELECT pg_advisory_lock(%s)",
                       (tilgangssveip.ARBEIDERNOKKEL,))
        r = tilgangssveip.kjor(v, tidligere_feil=1)
        assert r.hoppet_over is True
        assert r.feilet is False and r.alarm_utlost is False
        assert (r.tenanter, r.nye, r.oppdaterte, r.lukkede) == (0, 0, 0, 0)
        assert r.avkortet is False

        # …og `main()` lar telleren stå NØYAKTIG som den sto.
        tilstand = tmp_path / "tilgangssveip.json"
        tilstand.write_text(json.dumps({"feil": 1}), encoding="utf-8")
        os.environ["DISPONIT_TILGANGSSVEIPTILSTAND"] = str(tilstand)
        os.environ["DISPONIT_TILGANGSSVEIP_URL"] = SVEIP_DSN
        try:
            kode = kjorer.main()
        finally:
            os.environ.pop("DISPONIT_TILGANGSSVEIPTILSTAND", None)
            os.environ.pop("DISPONIT_TILGANGSSVEIP_URL", None)
        assert kode == 0
        assert json.loads(tilstand.read_text(encoding="utf-8"))["feil"] == 1, \
            "den hoppet over kjøringen slettet en alt opptelt feil"
    finally:
        holder.execute("SELECT pg_advisory_unlock(%s)",
                       (tilgangssveip.ARBEIDERNOKKEL,))
        holder.close()
        v.close()


def test_alarm_etter_to_sammenhengende_feilede_kjoringer(tmp_path,
                                                        monkeypatch):
    """En stille gjennomgangssveip er et tilgangsregister som eldes uten
    at noen ser det — og den tilgangen ingen har sett på er den som lar
    en tidligere ansatt beholde en nøkkel hun ikke skulle hatt.

    Første feil teller opp uten alarm; den ANDRE alarmerer — og
    JSON-linja bærer begge tallene, så journalen kan svare på spørsmålet
    uten å måtte lese tilstandsfilen."""
    from drift import kjor_tilgangssveip as kjorer

    tilstand = tmp_path / "tilgangssveip.json"
    monkeypatch.setenv("DISPONIT_TILGANGSSVEIPTILSTAND", str(tilstand))
    monkeypatch.setenv("DISPONIT_TILGANGSSVEIP_URL",
                       "postgresql://finnes-ikke@127.0.0.1:1/nei")
    monkeypatch.setattr(
        kjorer, "_koble",
        lambda dsn: (_ for _ in ()).throw(RuntimeError("nede")))

    linjer: list[str] = []
    monkeypatch.setattr("builtins.print",
                        lambda *a, **k: linjer.append(a[0]) if not k.get(
                            "file") else None)
    assert kjorer.main() == 1
    forste = json.loads(linjer[-1])
    assert forste["feilet"] == 1 and forste["sammenhengende_feil"] == 1
    assert forste["alarm"] == 0, "alarm etter ÉN feil er en falsk alarm"

    assert kjorer.main() == 1
    andre = json.loads(linjer[-1])
    assert andre["sammenhengende_feil"] == 2
    assert andre["alarm"] == 1, \
        "to sammenhengende feilede kjøringer alarmerte ikke"
    assert andre["tilstand_lagret"] == 1
    assert json.loads(tilstand.read_text(encoding="utf-8"))["feil"] == 2


def test_sveipen_nekter_aa_starte_uten_egen_dsn(tmp_path, monkeypatch):
    """INGEN fallback til `DATABASE_URL`. Runtime-rollen har med vilje
    ikke EXECUTE på sveipen (097 REVOKEr den), så en fallback ville bare
    byttet en tydelig oppstartsnekt mot «permission denied» i journalen
    hver natt — og en jobb som feiler likt hver natt er en jobb ingen
    leser."""
    from drift import kjor_tilgangssveip as kjorer
    monkeypatch.setenv("DISPONIT_TILGANGSSVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.delenv("DISPONIT_TILGANGSSVEIP_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://skal-ikke-brukes/x")
    assert kjorer.main() == 2
    kode = "\n".join(
        ln for ln in KJOREREN.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#"))
    assert "DATABASE_URL" not in kode, \
        "kjøreren har fått en fallback til runtime-DSN-en"


@pg
@sveiperolle
def test_sveipekjoringen_gir_en_json_linje_med_tallene(migrator, tmp_path,
                                                      monkeypatch):
    """Én JSON-linje per kjøring, med tallene jobben faktisk målte — en
    jobb som ikke kunne måle rapporterer FUNN, aldri null.

    `avkortet` står i linja fordi en kjøring som traff taket sitt ikke
    har MÅLT hele registeret, og linja skal ikke kunne leses som «alt er
    sett på»."""
    from drift import kjor_tilgangssveip as kjorer
    ten = _tenantnavn("json")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        oid = _objekt(c, ten)
        _forfalt(migrator, ten, oid, eier)
    finally:
        c.close()
    monkeypatch.setenv("DISPONIT_TILGANGSSVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.setenv("DISPONIT_TILGANGSSVEIP_URL", SVEIP_DSN)
    linjer: list[str] = []
    monkeypatch.setattr("builtins.print",
                        lambda *a, **k: linjer.append(a[0]) if not k.get(
                            "file") else None)
    assert kjorer.main() == 0
    linje = json.loads(linjer[-1])
    assert linje["hendelse"] == "tilgangssveip"
    assert linje["feilet"] == 0 and linje["hoppet_over"] == 0
    assert linje["nye_funn"] >= 1 and linje["tenanter"] >= 1
    assert linje["avkortet"] == 0
    assert set(linje) == {"hendelse", "tenanter", "nye_funn",
                          "oppdaterte_funn", "lukkede_funn", "avkortet",
                          "feilet", "hoppet_over", "sammenhengende_feil",
                          "alarm", "tilstand_lagret"}


@pg
@sveiperolle
def test_avkortet_kjoring_sier_fra_at_den_ikke_maalte_alt(migrator):
    """«En jobb som ikke kunne måle rapporterer FUNN, aldri null.»

    Taket per tenant finnes for at et register som plutselig vokser ikke
    skal gjøre én natts kjøring uendelig. Prisen er at en kjøring kan
    være ferdig uten å ha sett hele registeret — og da SKAL den si det.
    En kjøring som traff taket og rapporterte som om alt var sett på er
    nøyaktig den feilen som ikke oppdages.

    Målt med tak = 1 og TO forfalte tilganger: første kjøring reiser ett
    funn og melder `avkortet`, andre kjøring tar den siste og melder
    ferdig.
    """
    ten = _tenantnavn("avkortet")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _sv()
    try:
        oid = _objekt(c, ten)
        _forfalt(migrator, ten, oid, eier, subjekt="en@x.test", dager=500)
        _forfalt(migrator, ten, oid, eier, subjekt="to@x.test", dager=400)
        rad = _sveip(v, grense=1)
        assert rad[4] is True, "en avkortet kjøring meldte seg som ferdig"
        assert len(_funn(migrator, ten)) == 1
        # …og den MEST forfalte kom først: treffer sveipen taket, er det
        # de eldste avvikene som er ute.
        assert _funn(migrator, ten)[0][2] == "en@x.test"
        rad = _sveip(v, grense=1)
        assert rad[4] is False, "kjøringen meldte avkortet uten å være det"
        assert len(_funn(migrator, ten)) == 2
    finally:
        c.close()
        v.close()


@pg
@sveiperolle
def test_sveipen_nekter_aa_kjore_med_tenantkontekst(migrator):
    """095s form: en kaller som HAR satt en tenantkontekst ber om noe
    annet enn det denne funksjonen gjør, og å svare den med et delvis
    kryss-tenant-resultat ville vært å gjette hva den mente."""
    v = _sv()
    try:
        v.execute("SELECT set_config('disponit.tenant','t-noen',true)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            v.execute("SELECT * FROM m12_sveip_gjennomganger(10)").fetchone()
        v.rollback()
    finally:
        v.close()


# ---------------------------------------------------------------------------
# HTTP-veien
# ---------------------------------------------------------------------------

@pg
@dekker("tilgang_ulovlig_tilstand")
def test_http_registrering_og_gjennomgang_ende_til_ende(migrator, klient):
    """FEILVEIEN OG DEN LOVLIGE VEIEN, ende til ende.

    En eier som ikke er medlem av tenanten svarer 409
    `tilgang_ulovlig_tilstand`: kroppen ER velformet, det er TILSTANDEN
    som sier nei. En ukjent kritikalitet svarer 400 — da er det kroppen
    som er feil. Forskjellen er hele forklaringen mennesket i flaten
    trenger, og ingen av dem skal være 500.

    Merk hvem som feller dommen: API-et sjekker ikke medlemskapet. Det
    kaller døren og oversetter dørens ERRCODE. En flate eller et API som
    sjekket selv ville vært en ANDRE sannhet å komme i utakt med.
    """
    eier = _bruker(migrator, TENANT)
    cookie, csrf = _browserokt(migrator, ["admin"])

    # 400: ukjent kritikalitet — kroppen er feilformet.
    r = _post(klient, cookie, csrf, "/v1/tilgang/objekt",
              {"system": "Fagsystem", "navn": "Modul", "kritikalitet": "ekstrem"})
    assert r.status_code == 400, r.text
    assert r.json()["feil"] == "request_feilformet"

    r = _post(klient, cookie, csrf, "/v1/tilgang/objekt",
              {"system": "HTTP-system", "navn": "Modul H",
               "kritikalitet": "hoy"})
    assert r.status_code in (200, 201), r.text
    oid = r.json()["objekt_id"]
    assert r.json()["ny"] is True

    # 409: en eier som ikke er medlem av tenanten. Tilstanden sier nei.
    r = _post(klient, cookie, csrf, "/v1/tilgang",
              {"objekt_id": oid, "subjekt": "http@x.test",
               "subjekttype": "person", "niva": "admin",
               "eier_bruker_id": "bid_finnes_ikke",
               "hjemmel": "Rolle R-H", "gjennomgang_dogn": 90})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "tilgang_ulovlig_tilstand"

    # 400: en hjemmel som bare er blanktegn stoppes alt i API-laget —
    # kroppen mangler innhold, den er ikke i feil tilstand.
    r = _post(klient, cookie, csrf, "/v1/tilgang",
              {"objekt_id": oid, "subjekt": "http@x.test",
               "subjekttype": "person", "niva": "admin",
               "eier_bruker_id": eier, "hjemmel": "   ",
               "gjennomgang_dogn": 90})
    assert r.status_code == 400, r.text

    # Den lovlige veien.
    r = _post(klient, cookie, csrf, "/v1/tilgang",
              {"objekt_id": oid, "subjekt": "http@x.test",
               "subjekttype": "person", "niva": "admin",
               "eier_bruker_id": eier, "hjemmel": "Rolle R-H",
               "gjennomgang_dogn": 90})
    assert r.status_code in (200, 201), r.text
    tid = r.json()["tilgang_id"]

    # 409 igjen: SAMME tildeling (objekt, subjekt, nivå) en gang til, med
    # en HELT NY idempotensnøkkel. Unikhetskravet er registerets, ikke
    # API-ets — og feilkoden skal si TILSTAND og ikke
    # `idempotenskonflikt`: nøkkelen er fersk, det er tildelingen som
    # finnes fra før. De to dommene deler SQLSTATE (23505) og skilles på
    # constraint-navnet.
    r = _post(klient, cookie, csrf, "/v1/tilgang",
              {"objekt_id": oid, "subjekt": "http@x.test",
               "subjekttype": "person", "niva": "admin",
               "eier_bruker_id": eier, "hjemmel": "En annen hjemmel",
               "gjennomgang_dogn": 30})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "tilgang_ulovlig_tilstand"

    # Gjennomgangen: 404 på en ukjent tilgang, og et svar med neste frist
    # på den ekte.
    r = _post(klient, cookie, csrf,
              f"/v1/tilgang/{uuid.uuid4()}/gjennomgang", {})
    assert r.status_code == 404, r.text
    r = _post(klient, cookie, csrf, f"/v1/tilgang/{tid}/gjennomgang", {})
    assert r.status_code in (200, 201), r.text
    assert r.json()["neste_frist"] is not None

    # …og lesedøren viser den, med attestanten. HVEM SOM GJENNOMGIKK ER
    # ØKTENS bruker-id — aldri kroppens.
    r = klient.get("/v1/tilgang", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    rad = next(t for t in r.json()["tilganger"] if t["tilgang_id"] == tid)
    assert rad["sist_gjennomgatt_av"] and rad["sist_gjennomgatt"]
    assert rad["hjemmel"] == "Rolle R-H"


@pg
def test_http_registrering_er_idempotent_paa_nokkelen(migrator, klient):
    """SP-2 (m35/096-formen): samme Idempotency-Key + samme innhold gir
    SAMME rad og et STILLE JA — en tapt respons + nytt klikk skal aldri
    føde raden en gang til. Samme nøkkel med ANNET innhold er en materiell
    konflikt kalleren skal se.

    …OG DE TO NAVNEROMMENE MÅLES: den samme nøkkelen brukt på objektveien
    og tilgangsveien skal gi TO forskjellige id-er. Ett felles navnerom
    ville gitt en UUID-kollisjon mellom to helt ulike rader.
    """
    eier = _bruker(migrator, TENANT)
    cookie, csrf = _browserokt(migrator, ["admin"])
    nokkel = secrets.token_urlsafe(24)
    kropp = {"system": "Idem-system", "navn": "Modul I",
             "kritikalitet": "middels"}

    r1 = _post(klient, cookie, csrf, "/v1/tilgang/objekt", kropp, idem=nokkel)
    assert r1.status_code in (200, 201), r1.text
    assert r1.json()["ny"] is True
    r2 = _post(klient, cookie, csrf, "/v1/tilgang/objekt", kropp, idem=nokkel)
    assert r2.json()["objekt_id"] == r1.json()["objekt_id"]
    assert r2.json()["ny"] is False, "gjenspillet fødte et nytt objekt"

    endret = dict(kropp, kritikalitet="kritisk")
    r3 = _post(klient, cookie, csrf, "/v1/tilgang/objekt", endret,
               idem=nokkel)
    assert r3.status_code == 409, r3.text
    assert r3.json()["feil"] == "idempotenskonflikt"

    # SAMME nøkkel på tilgangsveien: et annet navnerom, altså en annen id.
    tkropp = {"objekt_id": r1.json()["objekt_id"], "subjekt": "idem@x.test",
              "subjekttype": "tjenestekonto", "niva": "skriv",
              "eier_bruker_id": eier, "hjemmel": "Rolle R-I",
              "gjennomgang_dogn": 120}
    r4 = _post(klient, cookie, csrf, "/v1/tilgang", tkropp, idem=nokkel)
    assert r4.status_code in (200, 201), r4.text
    assert r4.json()["tilgang_id"] != r1.json()["objekt_id"]
    r5 = _post(klient, cookie, csrf, "/v1/tilgang", tkropp, idem=nokkel)
    assert r5.json()["tilgang_id"] == r4.json()["tilgang_id"]
    assert r5.json()["ny"] is False


# ---------------------------------------------------------------------------
# Migrasjonens form: SP-10-premisset, eierskap, RLS og rettighetsspeilet
# ---------------------------------------------------------------------------

@pg
def test_migrasjonen_er_kjort_og_bytebundet(migrator):
    """097 står i `migrasjoner` med checksum lik sha256 av filbytene i
    treet — den TOMME kjøringen målt direkte, og samme byte-binding
    fasiten pinner mot main."""
    cs = migrator.execute(
        "SELECT checksum FROM migrasjoner WHERE versjon=97").fetchone()
    migrator.rollback()
    assert cs is not None, "097 er ikke kjørt i testbasen"
    fil_sha = hashlib.sha256(MIGRASJON.read_bytes()).hexdigest()
    assert cs[0] == fil_sha, \
        "097 i treet er ikke bytene basen kjørte — historikk er immutable"
    fasit = json.loads(
        (ROT / "platform" / "core" / "db" / "migrasjons-fasit.json")
        .read_text(encoding="utf-8"))
    assert fasit.get("097_m12_tilgangsregister.sql") == fil_sha, \
        "fasiten pinner andre bytes enn treet bærer"


def test_migrasjonen_er_ren_ddl_og_rorer_ingen_eksisterende_tabell():
    """SP-10s premiss (047-klassen): masse-DML i en migrasjon kan køe
    utsatte triggerhendelser som ALTER-setninger nekter å passere. 097 har
    ingen toppnivå-DML i det hele tatt og ALTERer ingen EKSISTERENDE
    tabell — derfor er «grønn mot bebodd base» en EGENSKAP og ikke et
    håp, og derfor trenger den ingen seed i `sp10-provekjoring.py`.

    Den siste halvdelen er ikke en detalj: 096 måtte splice
    `varsel`-enumene og fikk en egen port for nettopp den setningen.
    097 rører ikke en eneste tabell som fantes før den — den er ren
    nyskaping, og da er «tom base» og «bebodd base» det samme utsagnet.
    """
    import pglast
    sql = MIGRASJON.read_text(encoding="utf-8")
    dml, alter = [], []
    for raa in pglast.parse_sql(sql):
        navn = type(raa.stmt).__name__
        if navn in ("InsertStmt", "UpdateStmt", "DeleteStmt"):
            dml.append(navn)
        if navn == "AlterTableStmt":
            rel = raa.stmt.relation.relname
            if rel not in EGNE_LAGRE:
                alter.append(rel)
    assert not dml, f"097 bærer toppnivå-DML {dml} — da er den en backfill"
    assert not alter, \
        f"097 ALTERer eksisterende tabeller {alter} — SP-10 krever seed"


def test_migrasjonen_navngir_aldri_runtime_rollen_i_en_grant():
    """056/057/089-formen: `disponit` er bare LOKALNAVNET på web-API-
    rollen, og `migrer.py` er eneste rettighetskilde for den konfigurerte
    rollen. En GRANT her ville lagt rettighetsmodellen to steder — og det
    ene stedet ville vært usant på enhver installasjon som kaller rollen
    noe annet. REVOKE-en er lovlig og nødvendig (091-formen): en rettighet
    som bare slutter å bli gitt er ikke trukket tilbake."""
    for linje in MIGRASJON.read_text(encoding="utf-8").splitlines():
        if linje.lstrip().startswith("--"):
            continue
        assert "TO disponit;" not in linje, \
            f"097 grantar direkte til runtime-rollen: {linje!r}"


def test_kjoreren_speiler_097_rettighetene():
    """Rettighetsspeilet i `migrer.py` (057-portformen), og den SKARPESTE
    delen av det: registeret har INGEN tabellrettigheter for noen rolle
    utenom dørenes egen eier.

      * runtime får EXECUTE på de tre lesedørene og de tre skrivedørene —
        og ALDRI på sveipen eller per-tenant-armen (kryss-tenant,
        038-reaperens snitt);
      * sveiperollen får EXECUTE på sveipen og ingenting annet;
      * ingen SELECT/INSERT/UPDATE/DELETE på `tilgangsobjekt`, `tilgang`
        eller `tilgangsfunn` noe sted i kjøreren.
    """
    kjorer = (ROT / "deploy" / "staging" / "migrer.py").read_text(
        encoding="utf-8")
    for dor in ("m12_tilgangsbilde(TEXT, INT)",
                "m12_objekter(TEXT, INT)",
                "m12_apne_funn(TEXT, INT)",
                "m12_registrer_objekt(TEXT, UUID, TEXT, TEXT, TEXT, TEXT)",
                "m12_registrer_tilgang(TEXT, UUID, UUID, TEXT, TEXT, TEXT,"
                " TEXT, TEXT, INT, TEXT)",
                "m12_registrer_gjennomgang(TEXT, UUID, TEXT)"):
        assert f"GRANT EXECUTE ON FUNCTION {dor} TO {{rolle}};" in kjorer, dor
    for kryss in ("m12_sveip_gjennomganger(INT)",
                  "m12_sveip_for_tenant(TEXT, INT)"):
        assert f"REVOKE ALL ON FUNCTION {kryss} FROM {{rolle}};" in kjorer, \
            f"runtime får beholde {kryss}"
    for tabell in EGNE_LAGRE:
        for verb in ("SELECT ON", "INSERT ON", "UPDATE ON", "DELETE ON"):
            assert f"{verb} {tabell}" not in kjorer, \
                f"en rolle har fått {verb} {tabell} utenom dørene"
    # Sveiperollen får NØYAKTIG én EXECUTE og ingen tabellrettighet.
    assert "TILGANGSSVEIP_RETTIGHETER" in kjorer
    mal = kjorer.split('TILGANGSSVEIP_RETTIGHETER = """')[1].split('"""')[0]
    grants = [ln for ln in mal.splitlines()
              if ln.strip().startswith("GRANT")
              and "USAGE ON SCHEMA" not in ln]
    assert grants == [
        "GRANT EXECUTE ON FUNCTION m12_sveip_gjennomganger(INT)"
        " TO {rolle};"], grants


@pg
def test_alle_dorene_eies_av_modulens_egen_rolle(migrator):
    """SECURITY DEFINER-dører som IKKE eies av `disponit_tilgang_eier`
    ville kjørt som migrator — altså med eierens rettigheter, forbi hele
    modellen. Eierskapet står også i `eierskap-reparasjon.sql`; her måles
    basen."""
    rader = dict(migrator.execute(
        "SELECT p.proname, r.rolname FROM pg_proc p"
        " JOIN pg_roles r ON r.oid = p.proowner"
        " WHERE p.proname LIKE 'm12\\_%' AND p.prosecdef").fetchall())
    migrator.rollback()
    assert set(rader) == {
        "m12_evidens", "m12_registrer_objekt", "m12_registrer_tilgang",
        "m12_registrer_gjennomgang", "m12_tilgangsbilde", "m12_objekter",
        "m12_apne_funn", "m12_sveip_for_tenant", "m12_sveip_gjennomganger"}
    assert set(rader.values()) == {"disponit_tilgang_eier"}

    eierskap = (ROT / "deploy" / "staging" / "eierskap-reparasjon.sql") \
        .read_text(encoding="utf-8")
    for dor in rader:
        assert f"'{dor}(" in eierskap, \
            f"{dor} mangler i eierskap-reparasjon.sql"

    # RADVAKTENE er migrators, som resten av husets vakter — de opprettes
    # utenfor SET ROLE-vinduet, og en vakt eid av dørenes eier ville vært
    # en trigger som kunne endres av den samme rollen den vokter.
    vakter = dict(migrator.execute(
        "SELECT p.proname, r.rolname FROM pg_proc p"
        " JOIN pg_roles r ON r.oid = p.proowner"
        " WHERE p.proname IN ('m12_objekt_vakt','m12_tilgang_vakt',"
        "                     'm12_funn_vakt')").fetchall())
    migrator.rollback()
    assert len(vakter) == 3
    assert set(vakter.values()) == {"disponit_migrator"}


@pg
def test_hver_tenantbundet_definer_kaller_krev_tenantkontekst_forst(migrator):
    """SP-1, målt på KILDEN i basen og ikke på filen: hver tenantbundet
    dør skal ha `krev_tenantkontekst` som første setning.

    `m12_sveip_gjennomganger` er unntaket, og den er unntaket EKSPLISITT
    — den er kryss-tenant og avviser tvert imot en kaller som HAR satt en
    kontekst. Per-tenant-armen `m12_sveip_for_tenant` er nettopp derfor
    en egen funksjon: sveipen binder konteksten til RADENS tenant og
    kaller dit, så porten gjelder også for sveipens eget arbeid.
    """
    kropper = dict(migrator.execute(
        "SELECT proname, prosrc FROM pg_proc"
        " WHERE proname LIKE 'm12\\_%' AND prosecdef").fetchall())
    migrator.rollback()
    for navn in ("m12_evidens", "m12_registrer_objekt",
                 "m12_registrer_tilgang", "m12_registrer_gjennomgang",
                 "m12_tilgangsbilde", "m12_objekter", "m12_apne_funn",
                 "m12_sveip_for_tenant"):
        kropp = kropper[navn]
        setninger = [ln.strip() for ln in kropp.splitlines()
                     if ln.strip() and not ln.strip().startswith("--")]
        start = setninger.index("BEGIN")
        assert "krev_tenantkontekst" in setninger[start + 1], \
            f"{navn}: første setning er ikke SP-1-porten"
    assert "krev_tenantkontekst" not in kropper["m12_sveip_gjennomganger"]
    assert "KRYSS-TENANT" in kropper["m12_sveip_gjennomganger"]


@pg
def test_rls_staar_paa_alle_tre_tabellene_med_force(migrator):
    """ENABLE + FORCE + `tenant_isolasjon`, og ingen BYPASSRLS noe sted i
    kjeden. Uten FORCE ville eieren — som er den ENESTE rollen med
    rettigheter her — sett alt."""
    for tabell in EGNE_LAGRE:
        rls, force = migrator.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
            " WHERE relname = %s", (tabell,)).fetchone()
        assert rls and force, f"{tabell}: RLS {rls}, FORCE {force}"
        navn = {r[0] for r in migrator.execute(
            "SELECT polname FROM pg_policy"
            " WHERE polrelid = %s::regclass", (tabell,)).fetchall()}
        assert "tenant_isolasjon" in navn, (tabell, navn)
    migrator.rollback()
    # Kryss-tenant-policyen finnes KUN på `tilgang`, KUN for SELECT, og
    # KUN når det ikke står en tenantkontekst.
    rader = migrator.execute(
        "SELECT c.relname, p.polcmd, pg_get_expr(p.polqual, p.polrelid)"
        " FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid"
        " WHERE p.polname = 'm12_sveip_tenantliste'").fetchall()
    migrator.rollback()
    assert len(rader) == 1 and rader[0][0] == "tilgang", rader
    assert rader[0][1] == "r", "kryss-tenant-policyen er ikke KUN FOR SELECT"
    assert "disponit.tenant" in rader[0][2] and "IS NULL" in rader[0][2], \
        rader[0][2]
    for rolle in ("disponit_tilgang_eier", "disponit_tilgangssveip"):
        assert migrator.execute(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = %s",
            (rolle,)).fetchone()[0] is False, f"{rolle} har BYPASSRLS"
    migrator.rollback()


@pg
def test_sveiperollen_har_ingen_tabellrettigheter(migrator):
    """«NULL tabellrettigheter, EXECUTE på nøyaktig én funksjon.» Målt i
    basen, ikke i skriptet: en sveiperolle med SELECT på `tilgang` ville
    vært en kryss-tenant lesevei ved siden av den ene som er tenkt — og
    her ville den leseveien vært kartet over hvem som har admin på hva,
    i hver eneste tenant."""
    rader = migrator.execute(
        "SELECT table_name, privilege_type FROM information_schema"
        ".table_privileges WHERE grantee = 'disponit_tilgangssveip'"
    ).fetchall()
    migrator.rollback()
    assert rader == [], f"sveiperollen har tabellrettigheter: {rader}"
    # …og EXECUTE på nøyaktig ÉN funksjon.
    funksjoner = {r[0] for r in migrator.execute(
        "SELECT p.proname FROM pg_proc p JOIN pg_namespace n"
        "   ON n.oid = p.pronamespace"
        " WHERE n.nspname='public'"
        "   AND has_function_privilege('disponit_tilgangssveip', p.oid,"
        "                              'EXECUTE')"
        "   AND p.proname LIKE 'm12\\_%'").fetchall()}
    migrator.rollback()
    assert funksjoner == {"m12_sveip_gjennomganger"}, funksjoner


@pg
def test_runtime_har_ingen_tabellrettigheter_paa_registeret(migrator):
    """SP-7: hele registeret nås KUN gjennom dørene. En SELECT på
    `tilgang` for web-API-rollen ville vært en lesevei som ikke går
    gjennom `krev_tenantkontekst`."""
    rader = migrator.execute(
        "SELECT table_name, privilege_type FROM information_schema"
        ".table_privileges WHERE grantee = 'disponit'"
        "   AND table_name = ANY(%s)", (list(EGNE_LAGRE),)).fetchall()
    migrator.rollback()
    assert rader == [], f"runtime har tabellrettigheter: {rader}"
    # …og runtime kan ikke kjøre sveipen.
    for navn in ("m12_sveip_gjennomganger(int)",
                 "m12_sveip_for_tenant(text,integer)"):
        assert migrator.execute(
            "SELECT has_function_privilege('disponit', %s, 'EXECUTE')",
            (navn,)).fetchone()[0] is False, navn
    migrator.rollback()


# ---------------------------------------------------------------------------
# Grensen, manifestet og rutene
# ---------------------------------------------------------------------------

def test_grensen_dekker_manifestets_atte_invarianter():
    """Grensen `m12-v1` ble registrert FØR koden (§0-regelen). Porten
    pinner den mot planen, ikke mot listen selv: åtte invarianter, null
    tillatte brudd, og `ddl_begge_kjoringer_gronne` som eneste ja-punkt.

    …OG HVER INVARIANT HAR MINST ÉN TEST. En invariant uten en test er en
    formulering."""
    from manifestskjema import KRAVGRENSER, M12_INVARIANTER
    g = KRAVGRENSER["m12-v1"]
    assert len(M12_INVARIANTER) == len(set(M12_INVARIANTER)) == 8
    assert g["invarianter"] is M12_INVARIANTER
    assert g["krav_ja"] == ("ddl_begge_kjoringer_gronne",)
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    # Punktbindingen er TOM MED VILJE — uflippbar til målingene finnes.
    assert g["punktbinding"] == {}
    udekket = [n for n in M12_INVARIANTER if not M12_DEKNING.get(n)]
    assert udekket == [], f"invarianter uten en eneste test: {udekket}"


def test_manifestet_staar_som_registrert():
    """Modulkatalogen er én fil, og den er v1-dommen. Porten måler at
    ingen har flippet en akse mens koden ble skrevet: koden er ikke en
    aksepthendelse, og en modul som får kode blir ikke `aktiv` av det."""
    import yaml
    m = yaml.safe_load((MODULROT / "manifest.yaml").read_text(
        encoding="utf-8"))
    assert m["id"] == "m12_tilgang"
    assert m["status"] == "under_utvikling"
    assert m["driftstilstand"] == "ikke_i_drift"
    assert m["i18n_prefiks"] == "tilgang"
    assert m["avhengigheter"] == []


def test_rutene_og_flaten_er_registrert():
    """`Route()` og `RUTESCOPE` bindes toveis av `test_pr008`; her måles
    SCOPEVALGET, som er en dom og ikke en detalj.

    LESINGEN BÆRER `security:read`, ikke `decisions:read` — og det er
    NETTOPP der M-12 skiller lag med M-21. En pliktliste sier hva som
    skal gjøres innen når; den er tenantens driftstilstand, og enhver
    kunderolle skal se den. Et tilgangsregister sier HVEM SOM HAR ADMIN
    PÅ HVA: et kart over angrepsflaten, med kritikalitet per system og
    eier per nøkkel. Med `decisions:read` ville hver `leser`,
    `godkjenner` og `policyforvalter` fått det kartet.

    Skriveveiene GJENBRUKER `bestilling:opprett` (M-21-presedensen) — et
    nytt scope er en registrering i autorisasjonslaget med egen port, og
    skal ikke oppstå av vane.

    SVEIPEN STÅR IKKE I RUTESCOPE, og det er en sikkerhetsdom: den er
    kryss-tenant og kjøres av sin egen rolle fra sin egen timer.
    """
    from api.app import BROWSER_MUTASJONSSCOPES, LESESCOPES, RUTESCOPE
    assert RUTESCOPE[("GET", "/v1/tilgang")] == "security:read"
    assert "security:read" in LESESCOPES
    for sti in ("/v1/tilgang", "/v1/tilgang/objekt",
                "/v1/tilgang/{tilgang_id:uuid}/gjennomgang"):
        assert RUTESCOPE[("POST", sti)] == "bestilling:opprett"
    assert "bestilling:opprett" in BROWSER_MUTASJONSSCOPES
    assert not any("sveip" in sti for _m, sti in RUTESCOPE), \
        "en sveip har fått en HTTP-rute"

    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert "security:read" in ROLLE_TIL_SCOPES["admin"]
    assert "security:read" in ROLLE_TIL_SCOPES["sikkerhet"]
    assert "bestilling:opprett" in ROLLE_TIL_SCOPES["admin"]
    # …og den brede leserrollen har det IKKE. Det er hele forskjellen
    # mellom denne modulen og pliktregisteret ved siden av.
    assert "security:read" not in ROLLE_TIL_SCOPES["leser"]

    sitekart = (ROT / "platform" / "core" / "ui" / "static" / "js"
                / "sitekart.js").read_text(encoding="utf-8")
    assert ('{ nokkel: "tilgang", scope: "security:read",'
            ' modulflate: 12 }') in sitekart
    appjs = (ROT / "platform" / "core" / "ui" / "static" / "js"
             / "app.js").read_text(encoding="utf-8")
    assert "tilgang: visTilgang," in appjs


def test_driftsenheten_er_wiret_i_utrullingen():
    """En timer som ikke står i `UNITS` blir aldri installert; en som
    ikke står i `enable --now` blir aldri startet; og en uten DSN-porten
    i preflighten ville startet rett i «permission denied» hver natt.
    Fire steder, og alle fire må stemme."""
    opp = (ROT / "deploy" / "staging" / "opp.sh").read_text(encoding="utf-8")
    for bit in ("disponit-tilgangssveip.service",
                "disponit-tilgangssveip.timer",
                "DISPONIT_TILGANGSSVEIP_URL",
                "skriv_cred tilgangssveip",
                "systemctl enable --now disponit-tilgangssveip.timer"):
        assert bit in opp, f"opp.sh mangler {bit}"
    # MEDLEMSKAP I BLOKKEN, ikke «står sist i den»: en assert på det
    # avsluttende anførselstegnet er grønn til neste modul legger sin
    # timer etter, og da blir DENNE testen rød av en fremmed endring.
    # (Samme felle traff M-9s port da 097 landet.)
    selvrevers = opp.split("SELVREVERS_ENHETER=")[1].split('"')[1]
    assert "disponit-tilgangssveip.timer" in selvrevers.split(), \
        "timeren står ikke i SELVREVERS_ENHETER"
    for fil in ("disponit-tilgangssveip.service",
                "disponit-tilgangssveip.timer"):
        assert (ROT / "deploy" / "staging" / fil).exists(), fil
    service = (ROT / "deploy" / "staging"
               / "disponit-tilgangssveip.service").read_text(encoding="utf-8")
    assert "LoadCredential=DISPONIT_TILGANGSSVEIP_URL:" in service
    assert "drift.kjor_tilgangssveip" in service
    timer = (ROT / "deploy" / "staging"
             / "disponit-tilgangssveip.timer").read_text(encoding="utf-8")
    assert "RandomizedDelaySec" in timer and "Persistent=true" in timer


# ---------------------------------------------------------------------------
# Små hjelpere for HTTP-veien (m21/m35-formen)
# ---------------------------------------------------------------------------

def _C_SESJON():
    from api import sesjon as sesjonmodul
    return sesjonmodul.C_SESJON


def _browserokt(migrator, roller):
    """Minirigg: en innlogget browserøkt med gitte roller i TENANT.
    -> (sesjonscookie, csrf-token)."""
    from api import sesjon as sesjonmodul
    _sett_kontekst(migrator, TENANT)
    bid = migrator.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://m12.test', %s) RETURNING bruker_id",
        ("s12h-" + secrets.token_hex(6),)).fetchone()[0]
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


def _post(klient, cookie, csrf, sti, kropp, idem=None):
    from api import sesjon as sesjonmodul
    return klient.post(sti, json=kropp,
                       cookies={sesjonmodul.C_SESJON: cookie},
                       headers={"X-Disponit-CSRF": csrf,
                                "Idempotency-Key":
                                    idem or secrets.token_urlsafe(24)})
