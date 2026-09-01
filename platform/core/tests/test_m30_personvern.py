"""M-30 personvern- og datasubjektagent v1 (migrasjon 099) — grensens
seks invarianter, målt.

Grensen `m30-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR koden
(§0-regelen). Hver invariant har en port her, og hver port måler BÅDE at
den lovlige veien virker og at bruddet avvises — en port som bare måler
det som skal gå igjennom har ikke målt en invariant.

  * `modulen_slettet_persondata` — V1-DOMMEN, og den viktigste i hele
    klyngen. Målt to ganger: STATISK (migrasjonen og sveipen bærer ingen
    DELETE/TRUNCATE mot noe utenfor modulens egne tabeller, og kaller
    ingen `reap_*`/`makuler_*`/`rydd_*`) og FUNKSJONELT (radantallet i
    M-4s lagre er uendret etter en sveip over en oversittet
    sletteforespørsel som dekker dem).
  * `forespørsel_uten_eier` (`sak_uten_eier` i ASCII) — direkte DML uten
    eier avvises (NOT NULL/FK), og døren avviser en «eier» som ikke er
    aktivt medlem av tenanten. To lag, samme sannhet.
  * `forespørsel_lukket_uten_svar` (`sak_lukket_uten_svar`) — CHECK-en
    avviser en `besvart` sak uten svarhenvisning ved DIREKTE DML, vakten
    avviser en statusovergang uten navngitt aktør, døren avviser en tom
    referanse. TRE LAG. Og TIDEN LUKKER INGENTING: fristen får passere,
    sveipen kjøres, og saken står fortsatt `apen`.
  * `oversittet_frist_uten_funn` — en sak forbi frist gir funn; to sveip
    gir ETT funn.
  * `tenantlekkasje_i_forespørselsregister` (`tenantlekkasje_i_sakregister`)
    — tenant A ser aldri tenant Bs forespørsler, verken ved direkte DML
    eller over API-et.
  * `ui_axe_alvorlige_brudd` — bor i
    `platform/core/ui/test/personvern.test.js` (jsdom + axe-core), som
    kjøres av `npm test`, ikke herfra.

MERK NAVNENE. Invariantene i `KRAVGRENSER` bruker `ø`
(`forespørsel_uten_eier`, `forespørsel_lukket_uten_svar`,
`tenantlekkasje_i_forespørselsregister`) og står NØYAKTIG som registrert
— grensen ble felt før koden og skal ikke endres. Det er bare
SQL-identifikatorer og filnavn som er ASCII, av den grunnen §1.1 i 099
skriver ut: en identifikator som må kvoteres for å virke blir før eller
senere skrevet uten kvoteringen.

I tillegg: art. 12-forlengelsen (uten begrunnelse avvist, forbi to
måneder fra `mottatt` avvist), M-4-koblingen (et ukjent lager avvises av
vakten), evidenskjeden får sin rad, og migrasjonen er ren DDL (SP-10s
premiss) og navngir aldri runtime-rollen.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo)
from .test_m37 import _sett_kontekst

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "099_m30_personvernregister.sql")
SVEIP_DSN = __import__("os").environ.get("DISPONIT_TEST_PERSONVERNSVEIP_DSN")

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")


# ---------------------------------------------------------------------------
# Riggen
# ---------------------------------------------------------------------------

def _rt():
    """Runtime-rollen — den som HAR EXECUTE på de seks API-dørene og
    ingen tabellrettighet på registeret."""
    from db.pg import koble
    return koble(DSN)


def _sv():
    """Sveiperollen — den som har EXECUTE på sveipen og ingenting annet."""
    from db.pg import koble
    return koble(SVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    """Egen tenant per test. Sveipen er kryss-tenant og ser HELE basen,
    så en delt tenant ville gjort testene rekkefølgeavhengige — og en
    test som består fordi naboen ryddet er ingen port."""
    return f"t-m30-{merke}-{secrets.token_hex(4)}"


def _bruker(m, tenant, *, aktiv=True, roller=("admin",)):
    """En identitet med medlemskap i tenanten."""
    _sett_kontekst(m, tenant)
    bid = m.execute(
        "INSERT INTO brukeridentitet (issuer, sub, profil)"
        " VALUES ('https://m30.test', %s, '{}'::jsonb) RETURNING bruker_id",
        ("s30-" + secrets.token_hex(6),)).fetchone()[0]
    m.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller, aktiv)"
        " VALUES (%s,%s,%s,%s)", (tenant, bid, list(roller), aktiv))
    m.commit()
    return bid


def _registrer(c, tenant, eier, *, saktype="innsyn", subjekt="DSR-1",
               mottatt="current_date", lagre=None, sid=None, aktor="u-test"):
    """Én forespørsel gjennom døren, som runtime. `mottatt` er et
    SQL-uttrykk, så testene kan skrive «for 40 døgn siden» uten å regne
    på kalenderen."""
    sid = sid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m30_registrer_sak(%s,%s,%s,%s," + mottatt + ",%s,%s,%s)",
        (tenant, sid, saktype, subjekt, eier, lagre, aktor))
    c.commit()
    return sid


def _sveip(v, vindu=7):
    """Kjør sveipen én gang. -> (tenanter, nye, oppdaterte, lukkede).

    TALLENE ER PLATTFORMVIDE, ikke tenantens: sveipen er kryss-tenant per
    konstruksjon og ser hver åpen sak i basen, også dem andre tester har
    lagt igjen. Assertene under teller derfor tenantens EGNE funn
    (`_funn`), ikke returverdien — en test som stolte på totalen ville
    vært rekkefølgeavhengig.
    """
    rad = v.execute("SELECT * FROM m30_sveip_frister(%s)", (vindu,)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant, *, bare_apne=True):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT sak_id, funntype, apen FROM personvernfunn WHERE tenant=%s"
        + (" AND apen" if bare_apne else "")
        + " ORDER BY funntype, sak_id", (tenant,)).fetchall()
    m.rollback()
    return rader


def _sak(m, tenant, sid):
    _sett_kontekst(m, tenant)
    rad = m.execute(
        "SELECT status, svar_ref, svar_ts, avvist_begrunnelse, lukket_av,"
        "       frist, forlenget_til, mottatt"
        "  FROM personvernsak WHERE tenant=%s AND sak_id=%s",
        (tenant, sid)).fetchone()
    m.rollback()
    return rad


# ---------------------------------------------------------------------------
# INVARIANT 1: modulen_slettet_persondata — V1-DOMMEN
# ---------------------------------------------------------------------------

#: Modulens EGNE tabeller. Alt annet er noen andres, og en DELETE mot
#: noe utenfor denne listen er nøyaktig den andre sletteveien M-4 ble
#: bygget for å hindre.
EGNE_TABELLER = {"personvernsak", "personvernsak_lager", "personvernfunn"}


def test_invariant_modulen_slettet_persondata_statisk():
    """V1-DOMMEN, målt i KILDEN — og den er den viktigste i hele klyngen.

    Katalogen lover en agent som finner, samler og SLETTER
    personopplysninger på tvers av lagrene. Sletting er allerede eid av
    M-4s retensjonsregnskap (093) og de seks reaperne som kjører, og en
    ANDRE slettevei ved siden av dem er nøyaktig det M-4 ble bygget for
    å hindre: to veier som sletter det samme kan aldri holdes i takt.

    Tre målinger over migrasjonen og sveipearbeideren:

      1. Ingen DELETE- eller TRUNCATE-setning i det hele tatt. Parset med
         `pglast`, ikke med et regex over teksten — en kommentar som
         nevner «DELETE» skal ikke felle porten, og en `DELETE` gjemt i
         en `EXECUTE format(...)` skal ikke slippe unna en tekstsøking
         som bare så etter setninger.
      2. Ingen UPDATE mot en tabell utenfor modulens egne tre. Dørene
         oppdaterer `personvernsak` og `personvernfunn`; alt annet i
         basen er noen andres tilstand.
      3. Ingen referanse til `reap_*`, `makuler_*` eller `rydd_*` — heller
         ikke som streng inne i en `EXECUTE`. Modulen skal ikke engang
         KJENNE navnet på en slettevei.

    MUTASJONEN SOM DREPER DENNE: la `m30_besvar_sak` kalle en reaper for
    lagrene saken dekker «siden den nå er besvart».
    """
    import pglast
    sql = MIGRASJON.read_text(encoding="utf-8")
    toppnivaa = [type(r.stmt).__name__ for r in pglast.parse_sql(sql)]
    assert "DeleteStmt" not in toppnivaa and "TruncateStmt" not in toppnivaa, \
        "099 bærer DELETE/TRUNCATE på toppnivå — modulen sletter ingenting"

    # 1b. Funksjonskroppene. pglast parser ikke plpgsql, så de måles på
    #     ordnivå — men bare i KODEN, aldri i kommentarene: hele
    #     hodekommentaren handler om hvorfor modulen IKKE sletter, og en
    #     port som felte den ville tvunget fram en fil som ikke forklarer
    #     sin egen viktigste dom.
    kode = _uten_kommentarer(sql)
    assert "DELETE FROM" not in kode.upper(), \
        "099 bærer «DELETE FROM» i kode — modulen sletter ingenting"
    # TRUNCATE forekommer nøyaktig ÉN form lovlig: `BEFORE TRUNCATE ON`,
    # altså SPERREN mot det. Alt annet er en truncate, og porten måler
    # forskjellen framfor å forby ordet — en modul som ikke får lov å
    # sperre for truncate ville vært svakere, ikke sterkere.
    uten_sperrer = re.sub(r"[a-z0-9_]*ingen_truncate", "", kode,
                          flags=re.IGNORECASE)
    uten_sperrer = re.sub(r"BEFORE\s+TRUNCATE\s+ON\s+[a-z_]+", "",
                          uten_sperrer, flags=re.IGNORECASE)
    assert "TRUNCATE" not in uten_sperrer.upper(), \
        "099 bærer en TRUNCATE som ikke er en sperre mot TRUNCATE"

    # 2. UPDATE bare mot egne tabeller. Mønsteret krever `SET` etter
    #    tabellnavnet, så `BEFORE UPDATE OR DELETE ON ...` i en
    #    triggerdeklarasjon ikke leses som en oppdatering — sperren mot
    #    en endring er ikke en endring.
    for m in re.finditer(
            r"UPDATE\s+(?:public\.)?([a-z_][a-z0-9_]*)"
            r"(?:\s+[a-z][a-z0-9_]*)?\s+SET\b", kode, re.IGNORECASE):
        assert m.group(1).lower() in EGNE_TABELLER, \
            (f"099 oppdaterer «{m.group(1)}», som ikke er modulens egen"
             " tabell — registeret endrer ingen annens tilstand")

    # 3. Ikke engang navnet på en slettevei.
    for arbeider in (kode,
                     (ROT / "platform" / "drift" / "personvernsveip.py")
                     .read_text(encoding="utf-8"),
                     (ROT / "platform" / "drift" / "kjor_personvernsveip.py")
                     .read_text(encoding="utf-8"),
                     (ROT / "platform" / "core" / "api" / "personvern.py")
                     .read_text(encoding="utf-8")):
        for m in re.finditer(r"\b(reap_|makuler_|rydd_)[a-z0-9_]+",
                             arbeider, re.IGNORECASE):
            pytest.fail(
                f"M-30 nevner sletteveien «{m.group(0)}» — sletting eies"
                " av M-4, og en andre vei er det M-4 ble bygget for å"
                " hindre")


def _uten_kommentarer(sql: str) -> str:
    """SQL-en uten `--`-kommentarer. Hele hodekommentaren i 099 forklarer
    hvorfor modulen IKKE sletter; en port som leste den som kode ville
    tvunget fram en fil uten sin egen viktigste begrunnelse."""
    return "\n".join(re.sub(r"--.*$", "", linje) for linje in sql.splitlines())


@pg
def test_invariant_modulen_slettet_persondata_funksjonelt(migrator):
    """V1-DOMMEN, målt mot BASEN. Statikken over kan bare se det som er
    skrevet; denne teller rader.

    Riggen er den skarpeste situasjonen som finnes: en OVERSITTET
    SLETTEFORESPØRSEL som eksplisitt dekker to av M-4s persondatalagre.
    Er det noe sted en modul ville «hjelpe til» med å slette, er det her.
    Radantallet i lagrene måles før og etter en sveip, og skal være
    identisk — og saken skal fortsatt stå `apen`, med et funn på seg.

    MUTASJONEN SOM DREPER DENNE: la sveipen kalle en reaper for lagrene
    en oversittet sletteforespørsel dekker.
    """
    ten = _tenantnavn("sletter")
    eier = _bruker(migrator, ten)
    lagre = _m4_lagre(migrator, 2)
    foer = _radantall(migrator, lagre)
    c, v = _rt(), _sv()
    try:
        sid = _registrer(c, ten, eier, saktype="sletting",
                         mottatt="current_date - 40", lagre=lagre)
        _sveip(v)
        etter = _radantall(migrator, lagre)
        assert etter == foer, (
            "radantallet i M-4s lagre endret seg av en personvernsveip —"
            f" {foer} → {etter}. Modulen sletter ingenting.")
        # …og porten har faktisk MÅLT noe: saken er oversittet, står
        # fortsatt åpen, og har fått funnet sitt.
        assert _sak(migrator, ten, sid)[0] == "apen"
        assert [(r[0], r[1]) for r in _funn(migrator, ten)] == \
            [(sid, "frist_oversittet")]
    finally:
        c.close()
        v.close()


def _m4_lagre(m, n: int) -> list[str]:
    """`n` lager-id-er fra M-4s register, med klasse `persondata`. Lest
    som lagerets eier — personvernregisteret har KOLONNEGRANT på
    `lager_id` alene og kan ikke se klassen (099 §3)."""
    m.execute("SET LOCAL ROLE disponit_lager_eier")
    rader = m.execute(
        "SELECT lager_id, relasjon FROM retensjonslager"
        " WHERE klasse='persondata' ORDER BY lager_id LIMIT %s",
        (n,)).fetchall()
    m.rollback()
    assert len(rader) == n, "M-4s register har ikke nok persondatalagre"
    _M4_RELASJON.update(dict(rader))
    return [r[0] for r in rader]


#: lager_id → relasjon, fylt av `_m4_lagre`. Tellingen må gå mot den
#: EKTE tabellen, ikke mot registerraden som navngir den.
_M4_RELASJON: dict[str, str] = {}


def _radantall(m, lagre: list[str]) -> dict[str, int]:
    """Rader i hvert navngitt M-4-lager, på tvers av tenanter.

    Lest som migrator (tabelleieren) og UTEN tenantkontekst, med
    RLS-en av veien der den finnes: porten skal se HELE lageret, ikke
    én tenants utsnitt — en sletting i en annen tenant er like mye et
    brudd på v1-dommen.
    """
    ut = {}
    m.execute("SELECT set_config('disponit.tenant','',true)")
    for lager in lagre:
        rel = _M4_RELASJON[lager]
        ut[lager] = m.execute(
            f"SELECT count(*) FROM public.{rel}").fetchone()[0]
    m.rollback()
    return ut


# ---------------------------------------------------------------------------
# INVARIANT 2: forespørsel_uten_eier  (ASCII: sak_uten_eier)
# ---------------------------------------------------------------------------

@pg
def test_invariant_sak_uten_eier(migrator):
    """«Forespørsler uten eier» er ikke en rapport i v1 — det er en NOT
    NULL. To lag måles her, fordi ett ville vært for lite:

      1. DIREKTE DML, som dørenes eier: en INSERT uten `eier_bruker_id`
         avvises av NOT NULL, og en med en ukjent bruker-id av
         fremmednøkkelen. Det er den bindende porten — den gjelder
         enhver skrivevei, også en fremtidig som glemmer døren.
      2. DØREN: en «eier» som ikke er AKTIVT MEDLEM av tenanten avvises.
         FK-en alene sier bare at id-en finnes ET STED i plattformen, og
         en innsynsforespørsel eid av en fremmed tenants bruker er
         nøyaktig like lite besvart som en uten eier.

    MUTASJONEN SOM DREPER DENNE: fjern NOT NULL på `eier_bruker_id`,
    eller fjern medlemskapssjekken i `m30_registrer_sak`.
    """
    ten = _tenantnavn("eier")
    _bruker(migrator, ten)
    # 1a. Ingen eier i det hele tatt.
    _sett_kontekst(migrator, ten)
    migrator.execute("SET LOCAL ROLE disponit_personvern_eier")
    with pytest.raises(psycopg.errors.NotNullViolation):
        migrator.execute(
            "INSERT INTO personvernsak (tenant, sak_id, type, subjekt_ref,"
            "  mottatt, frist, opprettet_av)"
            " VALUES (%s,%s,'innsyn','DSR-x', current_date,"
            "         current_date + 30, 'test')", (ten, uuid.uuid4()))
    migrator.rollback()

    # 1b. En eier som ikke finnes som identitet.
    _sett_kontekst(migrator, ten)
    migrator.execute("SET LOCAL ROLE disponit_personvern_eier")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        migrator.execute(
            "INSERT INTO personvernsak (tenant, sak_id, type, subjekt_ref,"
            "  mottatt, frist, eier_bruker_id, opprettet_av)"
            " VALUES (%s,%s,'innsyn','DSR-x', current_date,"
            "         current_date + 30, 'bid_finnes_ikke', 'test')",
            (ten, uuid.uuid4()))
    migrator.rollback()

    # 2. Døren: en bruker fra en ANNEN tenant er ikke en eier her.
    annen = _tenantnavn("eier-annen")
    fremmed = _bruker(migrator, annen)
    c = _rt()
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _registrer(c, ten, fremmed)
        c.rollback()
        # …og et INAKTIVT medlem av EGEN tenant er heller ikke en eier:
        # en forespørsel hos noen som har sluttet er en ingen besvarer.
        sovende = _bruker(migrator, ten, aktiv=False)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _registrer(c, ten, sovende)
        c.rollback()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# INVARIANT 3: forespørsel_lukket_uten_svar (ASCII: sak_lukket_uten_svar)
# ---------------------------------------------------------------------------

@pg
def test_invariant_sak_lukket_uten_svar(migrator):
    """AKSEPTKRAVET som invariant, i TRE LAG.

      1. CHECK-en: en direkte INSERT med `status='besvart'` uten
         svarhenvisning avvises — også som dørenes egen eier. Målt på en
         INSERT og ikke på en UPDATE, og det er ikke et smutthull:
         radvakten er en BEFORE-trigger og fyrer FØR constraint-sjekken,
         så en UPDATE ville målt vakten (lag 2) og ikke CHECK-en.
         INSERT-veien har ingen vakt foran seg — den treffer CHECK-en
         rent, og det er nettopp den formen som gjelder enhver fremtidig
         skrivevei noen måtte finne på å lage.
      2. VAKTEN: en overgang som HAR svarhenvisningen, men ingen navngitt
         aktør i sesjonen, avvises også. En statusovergang er FORFATTET,
         aldri avledet — og en jobb som skulle lukke fordi tiden gikk har
         ingen aktør å skrive.
      3. DØREN: en tom svarhenvisning avvises med en melding som sier
         hvorfor.

    Dette er strengere enn M-21s kvitteringskrav, og det er med vilje: en
    oversittet innsynsforespørsel er et LOVBRUDD, ikke en forsinkelse.

    MUTASJONEN SOM DREPER DENNE: dropp
    `personvernsak_besvart_krever_svar`, eller la vakten slippe en
    statusovergang uten aktør igjennom.
    """
    ten = _tenantnavn("svar")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        sid = _registrer(c, ten, eier)

        # 1. CHECK-en, direkte DML som eieren.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_personvern_eier")
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(
                "INSERT INTO personvernsak (tenant, sak_id, type,"
                "  subjekt_ref, mottatt, frist, eier_bruker_id, status,"
                "  opprettet_av)"
                " VALUES (%s,%s,'innsyn','DSR-y', current_date,"
                "         current_date + 30, %s, 'besvart', 'test')",
                (ten, uuid.uuid4(), eier))
        migrator.rollback()
        # …og `avvist` uten begrunnelse, samme lag, samme sannhet.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_personvern_eier")
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(
                "INSERT INTO personvernsak (tenant, sak_id, type,"
                "  subjekt_ref, mottatt, frist, eier_bruker_id, status,"
                "  svar_ts, lukket_av, opprettet_av)"
                " VALUES (%s,%s,'innsyn','DSR-z', current_date,"
                "         current_date + 30, %s, 'avvist', now(), 'u',"
                "         'test')", (ten, uuid.uuid4(), eier))
        migrator.rollback()

        # 2. Vakten: fullt svar, men ingen aktør i sesjonen.
        migrator.execute(
            "SELECT set_config('disponit.tenant',%s,true),"
            "       set_config('disponit.aktor','',true)", (ten,))
        migrator.execute("SET LOCAL ROLE disponit_personvern_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "UPDATE personvernsak SET status='besvart',"
                "       svar_ref='ARK-1', svar_ts=now(), lukket_av='noen'"
                " WHERE tenant=%s AND sak_id=%s", (ten, sid))
        migrator.rollback()

        # 3. Døren: tom referanse.
        _sett_kontekst(c, ten)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute("SELECT m30_besvar_sak(%s,%s,%s,%s)",
                      (ten, sid, "   ", "u-test"))
        c.rollback()

        # …og den lovlige veien går igjennom.
        _sett_kontekst(c, ten)
        c.execute("SELECT m30_besvar_sak(%s,%s,%s,%s)",
                  (ten, sid, "ARK-2026-4711", "u-test"))
        c.commit()
        rad = _sak(migrator, ten, sid)
        assert rad[0] == "besvart"
        assert rad[1] == "ARK-2026-4711"
        assert rad[2] is not None and rad[4] == "u-test"

        # …og terminalt er terminalt: en besvart sak gjenåpnes ikke.
        _sett_kontekst(c, ten)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute("SELECT m30_avvis_sak(%s,%s,%s,%s)",
                      (ten, sid, "ombestemte oss", "u-test"))
        c.rollback()
    finally:
        c.close()


@pg
def test_svaret_er_frosset_etter_lukking(migrator):
    """CodeRabbit (alvorlig): svarfeltene kunne skrives om ETTER lukking,
    så lenge `status` selv sto stille.

    Aktørkravet i vakten gjelder bare når `status` ENDRER seg, og det var
    hullet: en sak som alt sto `avvist` kunne få en ny
    `avvist_begrunnelse`, en `besvart` sak en annen `svar_ref`, uten at
    noen overgang fant sted og dermed uten at noen aktør ble navngitt. Et
    tilsyn leser nøyaktig disse fire feltene bakover, og en begrunnelse
    som kan skrives om i ettertid er ingen begrunnelse.

    MUTASJONEN SOM DREPER DENNE: fjern `OLD.status <> 'apen'`-grenen fra
    `m30_sak_vakt` — CHECK-ene slipper endringen gjennom, for FORMEN er
    fortsatt lovlig.
    """
    ten = _tenantnavn("frossen")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        besvart = _registrer(c, ten, eier, subjekt="DSR-b")
        avvist = _registrer(c, ten, eier, subjekt="DSR-a")
        _sett_kontekst(c, ten)
        c.execute("SELECT m30_besvar_sak(%s,%s,%s,%s)",
                  (ten, besvart, "ARK-1", "u-test"))
        c.execute("SELECT m30_avvis_sak(%s,%s,%s,%s)",
                  (ten, avvist, "Åpenbart grunnløs.", "u-test"))
        c.commit()

        # Fire skrivforsøk, alle med aktør i sesjonen og alle med en form
        # CHECK-ene godtar — det er nettopp derfor vakten må ta dem.
        for sid, setning in ((besvart, "svar_ref='ARK-OMSKREVET'"),
                             (besvart, "svar_ts=now()"),
                             (avvist,
                              "avvist_begrunnelse='noe helt annet'"),
                             (avvist, "lukket_av='en annen'")):
            migrator.execute(
                "SELECT set_config('disponit.tenant',%s,true),"
                "       set_config('disponit.aktor','u-test',true)", (ten,))
            migrator.execute("SET LOCAL ROLE disponit_personvern_eier")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                migrator.execute(
                    f"UPDATE personvernsak SET {setning}"
                    " WHERE tenant=%s AND sak_id=%s", (ten, sid))
            migrator.rollback()

        # …og innholdet står urørt etterpå.
        rad = _sak(migrator, ten, besvart)
        assert rad[1] == "ARK-1" and rad[4] == "u-test"
    finally:
        c.close()


@pg
def test_forlengelsen_kan_ikke_trekkes_tilbake(migrator):
    """En forlengelse er UNDERRETTET. Art. 12 nr. 3 krever at den
    registrerte får vite om den innen én måned — og et varsel kan ikke
    usendes.

    `NULL < dato` er NULL og ikke sant, så en ren «sett begge feltene
    tilbake til NULL» ville gått rett gjennom framover-sammenlikningen i
    vakten, og CHECK-en `personvernsak_forlengelse_er_hel` godtar
    NULL+NULL som den lovlige «ingen forlengelse»-formen. Nullveien måtte
    derfor fanges for seg.

    MUTASJONEN SOM DREPER DENNE: slå de to grenene i vakten sammen til
    den ene `NEW.forlenget_til < OLD.forlenget_til`.
    """
    ten = _tenantnavn("tilbake")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        sid = _registrer(c, ten, eier)
        _sett_kontekst(c, ten)
        c.execute("SELECT m30_forleng_frist(%s,%s,"
                  " (current_date + 40)::date,%s,%s)",
                  (ten, sid, "kompleks sak", "u-test"))
        c.commit()
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_personvern_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "UPDATE personvernsak SET forlenget_til = NULL,"
                "   forlengelse_begrunnelse = NULL"
                " WHERE tenant=%s AND sak_id=%s", (ten, sid))
        migrator.rollback()
        assert _sak(migrator, ten, sid)[6] is not None
    finally:
        c.close()


@pg
def test_mottatt_kan_ikke_ligge_fram_i_tid(migrator):
    """Mottaksdatoen er NULLPUNKTET fristen regnes fra, og vakten fryser
    den. En dato i framtiden ville skjøvet hele art. 12-klokka med seg —
    og feilen ville vært umulig å rette uten å registrere saken på nytt.

    Registeret kan ikke vite når anmodningen faktisk kom; det stoler på
    det som skrives. Men en dato i framtiden er ALLTID en
    inntastingsfeil, og den er den ene som kan avvises uten å gjette.
    """
    ten = _tenantnavn("framtid")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _registrer(c, ten, eier, mottatt="current_date + 1")
        c.rollback()
        # I DAG er lovlig — grensen er «fram i tid», ikke «i dag».
        sid = _registrer(c, ten, eier, mottatt="current_date")
        rad = _sak(migrator, ten, sid)
        assert rad[7] == dt.date.today()
        # …og fristen er ÉN KALENDERMÅNED fram, ikke 30 døgn. Loven
        # teller måneder, og porten regner det samme tallet registeret
        # regner — ikke et omtrentlig ett.
        assert rad[5] == _en_maaned(dt.date.today())
    finally:
        c.close()


def _en_maaned(d: dt.date) -> dt.date:
    """Én kalendermåned fram, som `date + interval '1 month'` i basen —
    ikke 30 døgn. Loven teller måneder, og porten skal måle det samme
    tallet registeret regner."""
    aar, mnd = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    dag = d.day
    while True:
        try:
            return dt.date(aar, mnd, dag)
        except ValueError:
            dag -= 1


@pg
def test_tiden_lukker_ingenting(migrator):
    """Fristen får PASSERE, sveipen kjøres, og saken står fortsatt
    `apen`.

    Dette er den andre halvdelen av dommen «en forespørsel lukkes av et
    skrevet svar, aldri av at fristen passerer»: det finnes ingen jobb i
    099 som setter `status`. Sveipen REISER FUNN — og at den gjør nøyaktig
    det, og ikke noe mer, er hva porten måler. Den oversittede saken får
    funnet sitt (den skal ikke ties i hjel), og status er urørt.

    Og porten måler det TO GANGER: også etter at fristen er forlenget og
    DEN har passert. En forlengelse er ikke en ny sjanse til å bli lukket
    av tiden.

    MUTASJONEN SOM DREPER DENNE: la sveipen sette `status='besvart'` på
    saker der fristen er passert, «siden de nok er ferdigbehandlet».
    """
    ten = _tenantnavn("tid")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _sv()
    try:
        sid = _registrer(c, ten, eier, mottatt="current_date - 40",
                         lagre=_m4_lagre(migrator, 1))
        _sveip(v)
        assert _sak(migrator, ten, sid)[0] == "apen", \
            "sveipen lukket en forespørsel — tiden skal ikke kunne lukke noe"
        assert [(r[0], r[1]) for r in _funn(migrator, ten)] == \
            [(sid, "frist_oversittet")]

        # …og en forlenget frist som SELV passerer lukker ingenting.
        # Forlengelsen settes direkte (døren krever en dato fram i tid),
        # med aktør i sesjonen — vakten krever den for statusoverganger,
        # ikke for fristflytting, men konteksten settes likevel som i en
        # ekte dørkjøring.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_personvern_eier")
        # Forlengelsen legges FEM DØGN TILBAKE i tid: den er senere enn
        # den opprinnelige fristen (som er ti døgn gammel) og ligger godt
        # innenfor art. 12-taket, men den er SELV passert. Det er den
        # skarpeste formen — en sak som har brukt opp både den ordinære
        # fristen og forlengelsen sin, og som fortsatt ikke skal kunne
        # lukkes av noe annet enn et skrevet svar.
        migrator.execute(
            "UPDATE personvernsak SET forlenget_til = current_date - 5,"
            "   forlengelse_begrunnelse='kompleks'"
            " WHERE tenant=%s AND sak_id=%s", (ten, sid))
        migrator.commit()
        _sveip(v)
        assert _sak(migrator, ten, sid)[0] == "apen"
        assert [(r[0], r[1]) for r in _funn(migrator, ten)] == \
            [(sid, "frist_oversittet")], \
            "en oversittet forlengelse er fortsatt et funn, ikke en lukking"
    finally:
        c.close()
        v.close()


# ---------------------------------------------------------------------------
# INVARIANT 4: oversittet_frist_uten_funn
# ---------------------------------------------------------------------------

@pg
def test_invariant_oversittet_frist_uten_funn(migrator):
    """EN SAK FORBI FRIST GIR FUNN, OG TO SVEIP GIR ETT FUNN.

    En oversittet innsynsfrist er et lovbrudd. Den skal ikke være en
    stille gammel rad — den skal være et funn noen ser. Og funnlisten
    skal ikke vokse med kadensen: en daglig sveip over en sak som har
    vært oversittet i et halvt år skal gi ETT funn, ikke 180. En
    funnliste som vokser er en funnliste folk lærer seg å overse, og da
    forsvinner de viktige med dem.

    Porten måler i tillegg at `frist_naermer_seg` og `frist_oversittet`
    er TO funn og ikke to navn på ett, og at overgangen mellom dem lukker
    det første.

    MUTASJONEN SOM DREPER DENNE: bytt `ON CONFLICT ... DO NOTHING` i
    sveipens steg 2 mot en ren INSERT, eller fjern `funntype` fra
    funnets primærnøkkel.
    """
    ten = _tenantnavn("funn")
    eier = _bruker(migrator, ten)
    lagre = _m4_lagre(migrator, 1)
    c, v = _rt(), _sv()
    try:
        # En sak med fristen tre døgn fram: innenfor vinduet på sju, ikke
        # oversittet. Den skal gi `frist_naermer_seg` og ingenting annet.
        naer = _registrer(c, ten, eier, subjekt="DSR-naer",
                          mottatt="current_date - 28", lagre=lagre)
        _sveip(v)
        assert [(r[0], r[1]) for r in _funn(migrator, ten)] == \
            [(naer, "frist_naermer_seg")]
        # To sveip til: ETT funn, ikke tre.
        _sveip(v)
        _sveip(v)
        assert len(_funn(migrator, ten)) == 1
        # …og `sist_sett_sveip` HAR flyttet seg: idempotensen er at raden
        # oppdateres, ikke at sveipen lot være å gjøre noe.
        _sett_kontekst(migrator, ten)
        forst, sist = migrator.execute(
            "SELECT forst_sett, sist_sett_sveip FROM personvernfunn"
            " WHERE tenant=%s AND sak_id=%s", (ten, naer)).fetchone()
        migrator.rollback()
        assert sist > forst, "funnet ble ikke oppdatert av sveip nummer to"

        # TO FUNNTYPER, IKKE TO NAVN PÅ ETT. En sak forbi fristen gir
        # `frist_oversittet`; den nære gir fortsatt `frist_naermer_seg`.
        # Saken kan ikke flyttes bakover i tid — `mottatt` og `frist` er
        # frosset av vakten, nettopp fordi de er nullpunktet fristen
        # måles fra — så den andre saken registreres med gammel
        # mottaksdato.
        sen = _registrer(c, ten, eier, subjekt="DSR-sen",
                         mottatt="current_date - 40", lagre=lagre)
        _sveip(v)
        apne = {(r[0], r[1]) for r in _funn(migrator, ten)}
        assert (sen, "frist_oversittet") in apne
        assert (naer, "frist_naermer_seg") in apne
        alle = _funn(migrator, ten, bare_apne=False)
        assert len(alle) == 2, alle
    finally:
        c.close()
        v.close()


@pg
def test_sak_uten_lagre_er_et_funn_og_lukkes_naar_svaret_kommer(migrator):
    """En åpen sak uten ett eneste av M-4s lagre er et FUNN.

    Det er ikke et formfeil: en innsyns- eller sletteforespørsel som ikke
    sier HVOR den gjelder, kan ingen etterprøve svaret på — og «vi
    svarte» blir en påstand uten en flate å måle den mot.

    Og funnet lukkes i SAMME TRANSAKSJON som svaret, ikke først ved neste
    sveip: en funnliste som er et døgn på etterskudd er en funnliste
    ingen stoler på. Raden består — at saken VAR uten lagre er historikk.
    """
    ten = _tenantnavn("ulagre")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _sv()
    try:
        sid = _registrer(c, ten, eier, lagre=None)
        _sveip(v)
        assert [(r[0], r[1]) for r in _funn(migrator, ten)] == \
            [(sid, "sak_uten_lagre")]
        _sett_kontekst(c, ten)
        c.execute("SELECT m30_besvar_sak(%s,%s,%s,%s)",
                  (ten, sid, "ARK-9", "u-test"))
        c.commit()
        assert _funn(migrator, ten) == [], \
            "funnet står åpent på en besvart sak — listen er et døgn bak"
        assert len(_funn(migrator, ten, bare_apne=False)) == 1, \
            "funnraden ble slettet — et lukket funn er historikk"
    finally:
        c.close()
        v.close()


# ---------------------------------------------------------------------------
# INVARIANT 5: tenantlekkasje_i_forespørselsregister
#              (ASCII: tenantlekkasje_i_sakregister)
# ---------------------------------------------------------------------------

@pg
def test_invariant_tenantlekkasje_i_sakregister(migrator):
    """Tenant A ser aldri tenant Bs forespørsler — verken ved direkte DML
    eller gjennom lesedøren.

    Isolasjonen er RLS-ens (`tenant_isolasjon`, ENABLE + FORCE), ikke et
    WHERE-ledd i en spørring noen kan glemme. Porten måler begge veier:
    en lesning i A-kontekst ser bare A, og lesedøren kalt med B som
    parameter mens konteksten er A avvises av SP-1-porten
    (`krev_tenantkontekst`) — en dør som tok kallerens ord for hvilken
    tenant den spør på vegne av, ville vært hele isolasjonen omgått med
    én parameter.

    MUTASJONEN SOM DREPER DENNE: fjern FORCE på `personvernsak`, eller
    fjern `krev_tenantkontekst`-kallet fra `m30_saker`.
    """
    a, b = _tenantnavn("lekk-a"), _tenantnavn("lekk-b")
    eier_a, eier_b = _bruker(migrator, a), _bruker(migrator, b)
    c = _rt()
    try:
        sid_a = _registrer(c, a, eier_a, subjekt="DSR-A")
        sid_b = _registrer(c, b, eier_b, subjekt="DSR-B")

        # 1. Direkte DML som dørenes eier: RLS-en er den bindende.
        _sett_kontekst(migrator, a)
        migrator.execute("SET LOCAL ROLE disponit_personvern_eier")
        rader = migrator.execute(
            "SELECT sak_id, subjekt_ref FROM personvernsak").fetchall()
        migrator.rollback()
        assert [r[0] for r in rader] == [sid_a]
        assert all(r[1] != "DSR-B" for r in rader)

        # 2. Lesedøren, i A-kontekst men med Bs tenant som parameter.
        #    SP-1-porten binder de to, så dette er ikke et oppslag — det
        #    er en avvisning.
        _sett_kontekst(c, a)
        with pytest.raises(psycopg.Error):
            c.execute("SELECT * FROM m30_saker(%s,%s)", (b, 50)).fetchall()
        c.rollback()

        # 3. …og den lovlige veien ser NØYAKTIG sin egen tenant.
        _sett_kontekst(c, a)
        egne = c.execute("SELECT * FROM m30_saker(%s,%s)",
                         (a, 50)).fetchall()
        c.rollback()
        assert [r[0] for r in egne] == [sid_a]
        assert [r[2] for r in egne] == ["DSR-A"]
    finally:
        c.close()


@pg
def test_tenantlekkasje_over_api(migrator, klient):
    """…og over HTTP: en økt i TENANT ser ikke en annen tenants saker.

    Konteksten settes av sesjonen, aldri av kroppen — og lesedøren krever
    den. Porten måler at svaret er tomt for den fremmede tenanten, ikke
    bare at det er filtrert: et filter kan glippe, en RLS-policy kan det
    ikke.
    """
    annen = _tenantnavn("api-annen")
    eier = _bruker(migrator, annen)
    c = _rt()
    try:
        _registrer(c, annen, eier, subjekt="DSR-FREMMED")
    finally:
        c.close()
    cookie, _csrf = _browserokt(migrator, ["sikkerhet"])
    r = klient.get("/v1/personvern", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    assert "DSR-FREMMED" not in r.text
    assert r.json()["saker"] == []


# ---------------------------------------------------------------------------
# Art. 12-forlengelsen, M-4-koblingen og evidenskjeden
# ---------------------------------------------------------------------------

@pg
def test_forlengelse_uten_begrunnelse_avvises(migrator):
    """Art. 12 nr. 3 gir to måneder ekstra MOT en årsak, ikke på
    forespørsel. Målt i to lag: CHECK-en avviser en `forlenget_til` uten
    begrunnelse ved direkte DML, og døren avviser en tom begrunnelse med
    en melding som sier hvorfor.

    MUTASJONEN SOM DREPER DENNE: gjør `forlengelse_begrunnelse`
    valgfri i `personvernsak_forlengelse_er_hel`.
    """
    ten = _tenantnavn("forleng")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        sid = _registrer(c, ten, eier)
        # 1. CHECK-en, direkte DML.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_personvern_eier")
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(
                "UPDATE personvernsak SET forlenget_til = frist + 30"
                " WHERE tenant=%s AND sak_id=%s", (ten, sid))
        migrator.rollback()
        # 2. Døren.
        _sett_kontekst(c, ten)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute("SELECT m30_forleng_frist(%s,%s,"
                      " (current_date + 40)::date,%s,%s)",
                      (ten, sid, "  ", "u-test"))
        c.rollback()
        # 3. …og den lovlige veien går igjennom, med begrunnelsen skrevet.
        _sett_kontekst(c, ten)
        ny = c.execute(
            "SELECT m30_forleng_frist(%s,%s,(current_date + 40)::date,%s,%s)",
            (ten, sid, "Saken omfatter fire lagre.", "u-test")).fetchone()[0]
        c.commit()
        rad = _sak(migrator, ten, sid)
        assert rad[6] == ny and ny == dt.date.today() + dt.timedelta(days=40)
    finally:
        c.close()


@pg
def test_forlengelse_forbi_to_maaneder_avvises(migrator):
    """ART. 12s TAK, i to lag. «Fristen kan forlenges med to måneder» —
    ikke tre, ikke fem. Taket er tre måneder fra `mottatt` totalt (én
    måned ordinær frist + to måneder forlengelse), og det står i
    SKJEMAET: en CHECK, ikke en sjekk i et API-lag som kunne omgås.

    MUTASJONEN SOM DREPER DENNE: fjern `forlenget_til <= mottatt + 3
    months` fra `personvernsak_forlengelse_er_hel`.
    """
    ten = _tenantnavn("tak")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        sid = _registrer(c, ten, eier)
        # 1. Døren, med sin egen melding om hva taket ER.
        _sett_kontekst(c, ten)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
            c.execute("SELECT m30_forleng_frist(%s,%s,"
                      " (current_date + 120)::date,%s,%s)",
                      (ten, sid, "veldig kompleks", "u-test"))
        assert "TO måneder" in str(ei.value)
        c.rollback()
        # 2. CHECK-en, direkte DML — den bindende, som gjelder enhver
        #    skrivevei også en fremtidig som glemmer døren.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_personvern_eier")
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(
                "UPDATE personvernsak SET"
                "   forlenget_til = mottatt + interval '4 months',"
                "   forlengelse_begrunnelse = 'veldig kompleks'"
                " WHERE tenant=%s AND sak_id=%s", (ten, sid))
        migrator.rollback()
        # 3. Nøyaktig på taket er lovlig: to måneder ekstra, ikke mindre.
        _sett_kontekst(c, ten)
        c.execute(
            "SELECT m30_forleng_frist(%s,%s,"
            " (%s::date + interval '3 months')::date,%s,%s)",
            (ten, sid, dt.date.today(), "kompleks", "u-test"))
        c.commit()
        assert _sak(migrator, ten, sid)[6] is not None
    finally:
        c.close()


@pg
def test_ukjent_lager_avvises_av_m4_vakten(migrator):
    """KOBLINGEN MOT M-4, målt begge veier.

    En sak kan bare dekke lagre M-4s retensjonsregister faktisk navngir
    — ellers ville registeret båret en peker ingen kan følge, og
    «hvilke lagre dekker denne forespørselen» ville vært fritekst.

    Vakten er en TRIGGER og ikke en fremmednøkkel (099 §1.2), og porten
    måler at den gjelder også for DIREKTE DML: en vakt som bare virket
    gjennom døren ville vært en port på én skrivevei.
    """
    ten = _tenantnavn("lager")
    eier = _bruker(migrator, ten)
    ekte = _m4_lagre(migrator, 1)
    c = _rt()
    try:
        # 1. Døren: et lager som ikke finnes i M-4s register.
        _sett_kontekst(c, ten)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute(
                "SELECT m30_registrer_sak(%s,%s,'innsyn','DSR-q',"
                "  current_date,%s,%s,'u-test')",
                (ten, uuid.uuid4(), eier, ["finnes_ikke_i_m4"]))
        c.rollback()
        # 2. Den lovlige veien: et ekte lager føres på.
        sid = _registrer(c, ten, eier, lagre=ekte)
        _sett_kontekst(migrator, ten)
        rader = migrator.execute(
            "SELECT lager_id FROM personvernsak_lager WHERE tenant=%s"
            "   AND sak_id=%s", (ten, sid)).fetchall()
        migrator.rollback()
        assert [r[0] for r in rader] == ekte
        # 3. Direkte DML, som eieren: vakten gjelder også der.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_personvern_eier")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            migrator.execute(
                "INSERT INTO personvernsak_lager (tenant, sak_id, lager_id)"
                " VALUES (%s,%s,'finnes_ikke_i_m4')", (ten, sid))
        migrator.rollback()
        # 4. …og listen er APPEND-ONLY: hvilke lagre en forespørsel
        #    dekket er det et tilsyn etterprøver svaret mot.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_personvern_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "DELETE FROM personvernsak_lager WHERE tenant=%s"
                "   AND sak_id=%s", (ten, sid))
        migrator.rollback()
    finally:
        c.close()


@pg
def test_evidenskjeden_far_hver_handling(migrator):
    """Manifestets første reelle avhengighet (m02), målt: hver
    registrering, hvert svar og hver forlengelse skriver sin egen rad i
    evidenskjeden, i SAMME transaksjon som handlingen.

    OG DEN SKARPE HALVDELEN: `subjekt_ref` står ALDRI i evidensraden. En
    evidenskjede som arkiverte hvem som hadde bedt om innsyn ville gjort
    selve dokumentasjonen av personvernarbeidet til et nytt
    persondatalager — og et som er append-only og aldri kan rettes.
    """
    ten = _tenantnavn("evidens")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        sid = _registrer(c, ten, eier, subjekt="DSR-HEMMELIG",
                         lagre=_m4_lagre(migrator, 1))
        _sett_kontekst(c, ten)
        c.execute("SELECT m30_forleng_frist(%s,%s,"
                  " (current_date + 40)::date,%s,%s)",
                  (ten, sid, "kompleks sak", "u-test"))
        c.execute("SELECT m30_besvar_sak(%s,%s,%s,%s)",
                  (ten, sid, "ARK-77", "u-test"))
        c.commit()
        _sett_kontekst(migrator, ten)
        rader = migrator.execute(
            "SELECT handling, begrunnelse::text FROM revisjonslogg"
            " WHERE tenant=%s AND kilde='m30_personvern'"
            " ORDER BY id", (ten,)).fetchall()
        migrator.rollback()
        assert [r[0] for r in rader] == [
            "personvernsak.registrert", "personvernsak.frist_forlenget",
            "personvernsak.besvart"]
        for _handling, begrunnelse in rader:
            assert "DSR-HEMMELIG" not in begrunnelse, \
                "subjektreferansen står i evidenskjeden"
    finally:
        c.close()


# ---------------------------------------------------------------------------
# HTTP-veien
# ---------------------------------------------------------------------------

@pg
@dekker("personvernsak_ulovlig_tilstand")
def test_http_svar_uten_referanse_er_409(migrator, klient):
    """Kroppen ER velformet; det er TILSTANDEN (og innholdskravet basen
    håndhever) som sier nei. Forskjellen på 400 og 409 er hele
    forklaringen mennesket i flaten trenger.
    """
    cookie, csrf = _browserokt(migrator, ["admin"])
    eier = _bruker(migrator, TENANT)
    r = _post(klient, cookie, csrf, "/v1/personvern",
              {"type": "innsyn", "subjekt_ref": "DSR-http",
               "eier_bruker_id": eier,
               "mottatt": dt.date.today().isoformat(),
               "lager_id": []})
    assert r.status_code == 200, r.text
    sid = r.json()["sak_id"]

    # 1. Uten `svar_ref` i det hele tatt: KROPPEN er feil → 400.
    r = _post(klient, cookie, csrf, f"/v1/personvern/{sid}/svar", {})
    assert r.status_code == 400, r.text
    assert r.json()["feil"] == "request_feilformet"

    # 2. Med en TOM referanse: kroppen er fortsatt feilformet (flaten
    #    krever en ikke-tom streng), og det er riktig svar — 400 sier
    #    «rett kroppen», 409 ville sagt «saken er i feil tilstand».
    r = _post(klient, cookie, csrf, f"/v1/personvern/{sid}/svar",
              {"svar_ref": "   "})
    assert r.status_code == 400, r.text

    # 3. En forlengelse forbi art. 12-taket: kroppen ER velformet, det er
    #    LOVEN basen håndhever som sier nei → 409.
    r = _post(klient, cookie, csrf, f"/v1/personvern/{sid}/forleng",
              {"forlenget_til": (dt.date.today()
                                 + dt.timedelta(days=200)).isoformat(),
               "begrunnelse": "veldig kompleks"})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "personvernsak_ulovlig_tilstand"

    # 4. …og den lovlige veien går igjennom.
    r = _post(klient, cookie, csrf, f"/v1/personvern/{sid}/svar",
              {"svar_ref": "ARK-2026-99"})
    assert r.status_code == 200, r.text
    # 5. En gang til på den samme saken: nå er den terminal → 409.
    r = _post(klient, cookie, csrf, f"/v1/personvern/{sid}/svar",
              {"svar_ref": "ARK-2026-99"})
    assert r.status_code == 409, r.text


@pg
def test_http_registrering_er_idempotent_paa_nokkelen(migrator, klient):
    """SP-2 (m35/096-formen): serveren UTLEDER `sak_id` av
    Idempotency-Key-en, så en tapt respons + nytt klikk GJENSPILLER i
    stedet for å føde forespørselen en gang til. Samme nøkkel med ANNET
    innhold er en materiell konflikt — og materialiteten dekker
    LAGERLISTEN, fordi den er hele koblingen mot M-4.
    """
    cookie, csrf = _browserokt(migrator, ["admin"])
    eier = _bruker(migrator, TENANT)
    lagre = _m4_lagre(migrator, 1)
    nokkel = secrets.token_urlsafe(24)
    kropp = {"type": "sletting", "subjekt_ref": "DSR-idem",
             "eier_bruker_id": eier,
             "mottatt": dt.date.today().isoformat(), "lager_id": lagre}
    r1 = _post(klient, cookie, csrf, "/v1/personvern", kropp, idem=nokkel)
    assert r1.status_code == 200 and r1.json()["ny"] is True, r1.text
    r2 = _post(klient, cookie, csrf, "/v1/personvern", kropp, idem=nokkel)
    assert r2.status_code == 200 and r2.json()["ny"] is False
    assert r2.json()["sak_id"] == r1.json()["sak_id"]
    # …og en ENDRET lagerliste på samme nøkkel er en materiell konflikt.
    # Et stille ja her ville endret hva saken FAKTISK gjelder uten at
    # noen fikk vite det.
    endret = dict(kropp, lager_id=[])
    r3 = _post(klient, cookie, csrf, "/v1/personvern", endret, idem=nokkel)
    assert r3.status_code == 409, r3.text


# ---------------------------------------------------------------------------
# Migrasjonens form: SP-10-premisset og rettighetsspeilet
# ---------------------------------------------------------------------------

@pg
def test_migrasjonen_er_kjort_og_bytebundet(migrator):
    """099 står i `migrasjoner` med checksum lik sha256 av filbytene i
    treet — den TOMME kjøringen målt direkte, og samme byte-binding
    fasiten pinner mot main."""
    cs = migrator.execute(
        "SELECT checksum FROM migrasjoner WHERE versjon=99").fetchone()
    migrator.rollback()
    assert cs is not None, "099 er ikke kjørt i testbasen"
    fil_sha = hashlib.sha256(MIGRASJON.read_bytes()).hexdigest()
    assert cs[0] == fil_sha, \
        "099 i treet er ikke bytene basen kjørte — historikk er immutable"
    fasit = json.loads(
        (ROT / "platform" / "core" / "db" / "migrasjons-fasit.json")
        .read_text(encoding="utf-8"))
    assert fasit.get("099_m30_personvernregister.sql") == fil_sha, \
        "fasiten pinner andre bytes enn treet bærer"


def test_migrasjonen_er_ren_ddl():
    """SP-10 BEGGE VEIER, og for 099 er de to det SAMME utsagnet — målt,
    ikke antatt.

    Premisset (047-klassen): masse-DML i en migrasjon kan køe utsatte
    triggerhendelser som ALTER-setninger nekter å passere. 099 har ingen
    slik seed — den er ren DDL, den rører ingen ALT BEBODD tabell med
    ALTER, og den utvider ingen CHECK på en tabell som har rader (til
    forskjell fra 096, som splicer varselenumene). DA er «grønn fra tom
    base» og «grønn mot seedet base» det samme utsagnet, målt av den
    tomme kjøringen over pluss CI-kjøringen mot en bebodd base.

    Porten måler begge halvdelene: ingen toppnivå-DML, og ingen ALTER
    TABLE mot en tabell som ikke er modulens egen.
    """
    import pglast
    sql = MIGRASJON.read_text(encoding="utf-8")
    dml = [type(raa.stmt).__name__
           for raa in pglast.parse_sql(sql)
           if type(raa.stmt).__name__ in ("InsertStmt", "UpdateStmt",
                                          "DeleteStmt")]
    assert not dml, (
        f"099 bærer toppnivå-DML {dml} — da er den en backfill og skal"
        " registrere seed+måling i sp10-provekjoring.py")
    # ALTER bare mot egne tabeller (RLS-en). En ALTER mot `varsel`,
    # `revisjonslogg` eller et av M-4s lagre ville vært 047-klassen, og
    # da måtte porten over vært en helt annen måling.
    for m in re.finditer(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?"
                         r"(?:ONLY\s+)?(?:public\.)?([a-z_][a-z0-9_]*)",
                         _uten_kommentarer(sql), re.IGNORECASE):
        assert m.group(1).lower() in EGNE_TABELLER, \
            (f"099 gjør ALTER TABLE på «{m.group(1)}» — en bebodd tabell"
             " gjør «tom base» og «seedet base» til to ulike utsagn")


def test_migrasjonen_navngir_aldri_runtime_rollen():
    """056/057/089/096-formen: `disponit` er bare LOKALNAVNET på
    web-API-rollen, og `migrer.py` er eneste rettighetskilde for den
    konfigurerte rollen. En GRANT til runtime i migrasjonen ville lagt
    rettighetsmodellen to steder — og det ene stedet ville vært usant på
    enhver installasjon som kaller rollen noe annet. REVOKE-en er lovlig
    og nødvendig (091-formen): en rettighet som bare slutter å bli gitt er
    ikke trukket tilbake."""
    for linje in MIGRASJON.read_text(encoding="utf-8").splitlines():
        if linje.lstrip().startswith("--"):
            continue
        assert "TO disponit;" not in linje, \
            f"099 grantar direkte til runtime-rollen: {linje!r}"


def test_kjoreren_speiler_099_rettighetene():
    """Rettighetsspeilet i `migrer.py` (057-portformen), og den SKARPESTE
    delen av det: registeret har INGEN tabellrettigheter for noen rolle
    utenom dørenes egen eier.

      * runtime får EXECUTE på de to lesedørene og de fire skrivedørene —
        og ALDRI på sveipen eller kandidatpredikatet (kryss-tenant,
        038-reaperens snitt);
      * sveiperollen får EXECUTE på sveipen og ingenting annet;
      * ingen SELECT/INSERT/UPDATE/DELETE på `personvernsak`,
        `personvernsak_lager` eller `personvernfunn` noe sted i kjøreren.
    """
    kjorer = (ROT / "deploy" / "staging" / "migrer.py").read_text(
        encoding="utf-8")
    for dor in ("m30_saker(TEXT, INT)",
                "m30_apne_funn(TEXT, INT)",
                "m30_registrer_sak(TEXT, UUID, TEXT, TEXT, DATE, TEXT,"
                " TEXT[], TEXT)",
                "m30_besvar_sak(TEXT, UUID, TEXT, TEXT)",
                "m30_avvis_sak(TEXT, UUID, TEXT, TEXT)",
                "m30_forleng_frist(TEXT, UUID, DATE, TEXT, TEXT)"):
        assert f"GRANT EXECUTE ON FUNCTION {dor} TO {{rolle}};" in kjorer, dor
    assert "REVOKE ALL ON FUNCTION m30_sveip_frister(INT) FROM {rolle};" \
        in kjorer, "runtime får beholde kryss-tenant-sveipen"
    assert ("REVOKE ALL ON FUNCTION m30_sveipkandidater(TEXT, DATE, INT)"
            " FROM {rolle};") in kjorer, \
        "runtime får en lesevei rundt SP-1 gjennom kandidatpredikatet"
    assert "PERSONVERNSVEIP_RETTIGHETER" in kjorer
    assert ("GRANT EXECUTE ON FUNCTION m30_sveip_frister(INT) TO {rolle};"
            in kjorer)
    for tabell in ("personvernsak", "personvernsak_lager", "personvernfunn"):
        for verb in ("SELECT ON", "INSERT ON", "UPDATE ON", "DELETE ON"):
            assert f"{verb} {tabell}" not in kjorer, \
                f"en rolle har fått {verb} {tabell} utenom dørene"


@pg
def test_runtime_har_ingen_tabellrettighet_og_ingen_sveip(migrator):
    """SP-7 målt mot BASEN, ikke mot kjøreren: runtime-rollen har ingen
    SELECT på registerets tre tabeller, og ingen EXECUTE på sveipen.

    Dette er den andre halvdelen av porten over. Kjøreren sier hva som
    GIS; denne sier hva som FAKTISK står i basen — og det er den
    forskjellen en manuell GRANT på en vert ville gjemt seg i.
    """
    rolle = migrator.execute(
        "SELECT rolname FROM pg_roles WHERE rolname='disponit'").fetchone()
    if rolle is None:
        pytest.skip("runtime-rollen heter noe annet lokalt")
    for tabell in ("personvernsak", "personvernsak_lager", "personvernfunn"):
        har = migrator.execute(
            "SELECT has_table_privilege('disponit',%s,'SELECT')",
            (tabell,)).fetchone()[0]
        assert not har, f"runtime har SELECT på {tabell} — den skal gå" \
            " gjennom dørene"
    for fn in ("m30_sveip_frister(int)",
               "m30_sveipkandidater(text,date,int)"):
        har = migrator.execute(
            "SELECT has_function_privilege('disponit',%s,'EXECUTE')",
            (fn,)).fetchone()[0]
        assert not har, f"runtime kan kjøre {fn} — det er kryss-tenant"
    migrator.rollback()


def test_grensen_dekker_manifestets_seks_invarianter():
    """Grensen `m30-v1` ble registrert FØR koden (§0-regelen). Porten
    pinner den mot planen, ikke mot listen selv: seks invarianter, null
    tillatte brudd, og `ddl_begge_kjoringer_gronne` som eneste ja-punkt.

    Og den pinner NAVNENE. De tre med `ø` står nøyaktig som registrert —
    at SQL-identifikatorene og filnavnene er ASCII er en annen sak, og
    den skal ikke få lekke tilbake inn i en grense som alt er felt.
    """
    from manifestskjema import KRAVGRENSER, M30_INVARIANTER
    g = KRAVGRENSER["m30-v1"]
    assert len(M30_INVARIANTER) == len(set(M30_INVARIANTER)) == 6
    assert g["invarianter"] is M30_INVARIANTER
    assert g["krav_ja"] == ("ddl_begge_kjoringer_gronne",)
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    # Punktbindingen er TOM MED VILJE — uflippbar til målingene finnes.
    assert g["punktbinding"] == {}
    assert M30_INVARIANTER[0] == "modulen_slettet_persondata", \
        "v1-dommen står ikke først i grensen"
    for navn in ("forespørsel_uten_eier", "forespørsel_lukket_uten_svar",
                 "tenantlekkasje_i_forespørselsregister"):
        assert navn in M30_INVARIANTER, \
            f"{navn} er endret — grensen ble registrert før koden"


def test_rutene_og_flaten_er_registrert():
    """`Route()` og `RUTESCOPE` bindes toveis av `test_pr008`; her måles
    SCOPEVALGET, som er en dom og ikke en detalj.

    LESINGEN BÆRER `security:read`, IKKE `decisions:read`. Det siste har
    ALLE kunderollene — også `leser`, `godkjenner` og `policyforvalter` —
    og dette registeret sier hvem i virksomheten som har krevd innsyn i,
    retting av eller sletting av sine egne personopplysninger.
    `security:read` er compliance/ops-klassen (`sikkerhet` og `admin`),
    den samme `/v1/drift/*`, `/v1/datakvalitet` og `/v1/retensjon` ligger
    i. Scopet står dessuten i `LESESCOPES`, som er det `_autentiser`
    krever av en browserøkt.

    SKRIVEVEIENE GJENBRUKER `bestilling:opprett` — et nytt scope skal
    ikke oppstå av vane. Konsekvensen er tilsiktet og måles her:
    `sikkerhet` kan SE registeret og kan IKKE endre det.
    """
    from api.app import LESESCOPES, RUTESCOPE
    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert RUTESCOPE[("GET", "/v1/personvern")] == "security:read"
    assert "security:read" in LESESCOPES
    for sti in ("/v1/personvern",
                "/v1/personvern/{sak_id:uuid}/svar",
                "/v1/personvern/{sak_id:uuid}/avvis",
                "/v1/personvern/{sak_id:uuid}/forleng"):
        assert RUTESCOPE[("POST", sti)] == "bestilling:opprett"
    # DOMMEN, målt: `sikkerhet` leser og skriver ikke; `leser` ser
    # ingenting; `admin` gjør begge deler.
    assert "security:read" in ROLLE_TIL_SCOPES["sikkerhet"]
    assert "bestilling:opprett" not in ROLLE_TIL_SCOPES["sikkerhet"]
    assert "security:read" not in ROLLE_TIL_SCOPES["leser"]
    assert "security:read" in ROLLE_TIL_SCOPES["admin"]
    assert "bestilling:opprett" in ROLLE_TIL_SCOPES["admin"]
    sitekart = (ROT / "platform" / "core" / "ui" / "static" / "js"
                / "sitekart.js").read_text(encoding="utf-8")
    assert ('{ nokkel: "personvern", scope: "security:read",'
            ' modulflate: 30 }') in sitekart


def test_sveipen_har_sin_egen_rolle_og_sin_egen_enhet():
    """Sveipen er en EGEN TIMER, ikke et forpass i varselsenderen som
    M-21/M-22. Grunnen er at den ikke VARSLER: den reiser FUNN, på M-9s
    form, og en funnreiser har ingenting i varselkøens rytme, backoff og
    idempotens å gjøre.

    Porten måler at kjeden faktisk henger sammen: enheten finnes, timeren
    finnes, `opp.sh` installerer, stopper OG starter den, og DSN-en er
    gatet FØR første mutasjon — en jobb som installeres uten å startes er
    en jobb som aldri kjører, og en som startes uten DSN står med exit 2
    hver natt.
    """
    d = ROT / "deploy" / "staging"
    service = (d / "disponit-personvernsveip.service").read_text(
        encoding="utf-8")
    assert "drift.kjor_personvernsveip" in service
    assert "DISPONIT_PERSONVERNSVEIP_URL" in service
    assert "StateDirectory=disponit" in service
    assert (d / "disponit-personvernsveip.timer").exists()
    opp = (d / "opp.sh").read_text(encoding="utf-8")
    for bit in ("disponit-personvernsveip.service"
                " disponit-personvernsveip.timer",
                "systemctl enable --now disponit-personvernsveip.timer",
                "systemctl stop disponit-personvernsveip.timer",
                "skriv_cred personvernsveip DISPONIT_PERSONVERNSVEIP_URL",
                'DISPONIT_PERSONVERNSVEIP_URL:-'):
        assert bit in opp, bit
    # SELVREVERS: en timer utrullingen stopper må også stå i settet den
    # starter igjen når vinduet feiler.
    selvrevers = opp[opp.index("SELVREVERS_ENHETER="):]
    selvrevers = selvrevers[:selvrevers.index('"', selvrevers.index('="') + 2)]
    assert "disponit-personvernsveip.timer" in selvrevers


def test_eierskapsdesignet_dekker_alle_eier_eide_funksjoner():
    """`eierskap-reparasjon.sql` er designmodellen: hvert objekt de
    privilegerte rollene skal eie, med full signatur. En eier-eid
    funksjon som ikke står der ville blitt klassifisert som strøgods og
    flyttet til migrator ved neste reparasjon — og da ville dørene kjørt
    med migrators rettigheter i stedet for eierens.

    Porten leser signaturene ut av MIGRASJONEN og krever et treff for
    hver. Merk hva som IKKE skal stå der: `m30_sak_vakt` og
    `m30_funn_vakt` er migrators, som resten av husets radvakter — det er
    bare `m30_lager_vakt` som er eier-eid, fordi den er SECURITY DEFINER
    og leser M-4s register.
    """
    design = (ROT / "deploy" / "staging" / "eierskap-reparasjon.sql").read_text(
        encoding="utf-8")
    for sig in ("m30_evidens(text,uuid,text,text,jsonb)",
                "m30_ordinaer_frist(date)",
                "m30_lager_vakt()",
                "m30_registrer_sak(text,uuid,text,text,date,text,text[],text)",
                "m30_besvar_sak(text,uuid,text,text)",
                "m30_avvis_sak(text,uuid,text,text)",
                "m30_forleng_frist(text,uuid,date,text,text)",
                "m30_saker(text,integer)",
                "m30_apne_funn(text,integer)",
                "m30_sveipkandidater(text,date,integer)",
                "m30_sveip_frister(integer)"):
        assert f"'{sig}'" in design, sig
        assert design.count(f"'{sig}'") == 1, sig
    for ikke in ("m30_sak_vakt()", "m30_funn_vakt()"):
        assert f"'{ikke}'" not in design, \
            f"{ikke} er migrators radvakt og hører ikke i designtabellen"


def test_sp1_i_hver_tenantbundet_definer():
    """SP-1: hver SECURITY DEFINER som tar en tenant kaller
    `krev_tenantkontekst` FØRST. Målt på KILDEN, fordi en dør som glemte
    porten ville sett helt riktig ut i en funksjonell test — den ville
    bare tatt kallerens ord for hvilken tenant den spør på vegne av.

    Unntakene er navngitt og begrunnet: `m30_lager_vakt` er en trigger
    uten tenantparameter som leser et GLOBALT register,
    `m30_ordinaer_frist` regner en dato av en dato, `m30_sveipkandidater`
    er sveipens indre (den kalles av en definer som har satt konteksten
    selv), og `m30_sveip_frister` er kryss-tenant og NEKTER en kontekst.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    unntak = {"m30_lager_vakt", "m30_ordinaer_frist", "m30_sveipkandidater",
              "m30_sveip_frister", "m30_sak_vakt", "m30_funn_vakt"}
    funnet = 0
    for m in re.finditer(
            r"CREATE FUNCTION (m30_[a-z_]+)\((.*?)\)\s*\n?RETURNS",
            sql, re.DOTALL):
        navn = m.group(1)
        if navn in unntak:
            continue
        kropp = sql[m.end():]
        kropp = kropp[:kropp.index("END $$;")]
        forste = re.search(r"BEGIN\s*\n\s*(.+?);", kropp, re.DOTALL)
        assert forste and "krev_tenantkontekst" in forste.group(1), \
            f"{navn}: første setning er ikke krev_tenantkontekst (SP-1)"
        funnet += 1
    assert funnet == 7, \
        f"porten målte bare {funnet} tenantbundne dører — oppslaget er galt"


# ---------------------------------------------------------------------------
# Sveipearbeideren: artefaktryddingens form, ORDRETT
# ---------------------------------------------------------------------------

sveiperolle = pytest.mark.skipif(
    not SVEIP_DSN,
    reason="DISPONIT_TEST_PERSONVERNSVEIP_DSN ikke satt")


@pg
@sveiperolle
def test_sveipen_nekter_aa_kjore_med_tenantkontekst():
    """Sveipen er KRYSS-TENANT og kjøres uten kontekst. En kaller som har
    satt en, ber om noe annet enn det funksjonen gjør — og en sveip som
    stille hadde godtatt den ville reist funn for én tenant og tiet om
    resten, uten at noe sa fra.
    """
    v = _sv()
    try:
        v.execute("SELECT set_config('disponit.tenant','t-x',true)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            v.execute("SELECT * FROM m30_sveip_frister(7)")
        v.rollback()
    finally:
        v.close()


@pg
@sveiperolle
def test_overlappende_sveip_hopper_over_og_rorer_ikke_feiltelleren(tmp_path):
    """`artefaktrydding`-formen, ordrett: en kjøring som fant
    arbeidernøkkelen opptatt har verken lyktes eller feilet.

    Skrev den 0 her, ville en overlappende kjøring (manuell drift, flere
    verter, en henger som holder låsen) slettet en alt opptelt feil, og
    alarmen etter to sammenhengende feil ville aldri nådd fram.
    """
    import os

    from drift import kjor_personvernsveip as kjorer
    from drift import personvernsveip

    holder = psycopg.connect(MIGRATOR_DSN, autocommit=True)
    v = _sv()
    try:
        holder.execute("SELECT pg_advisory_lock(%s)",
                       (personvernsveip.ARBEIDERNOKKEL,))
        r = personvernsveip.kjor(v, tidligere_feil=1)
        assert r.hoppet_over is True
        assert r.feilet is False and r.alarm_utlost is False
        assert (r.tenanter, r.nye, r.oppdaterte, r.lukkede) == (0, 0, 0, 0)

        # …og `main()` lar telleren stå NØYAKTIG som den sto.
        tilstand = tmp_path / "personvernsveip.json"
        tilstand.write_text(json.dumps({"feil": 1}), encoding="utf-8")
        os.environ["DISPONIT_PERSONVERNSVEIPTILSTAND"] = str(tilstand)
        os.environ["DISPONIT_PERSONVERNSVEIP_URL"] = SVEIP_DSN
        try:
            kode = kjorer.main()
        finally:
            os.environ.pop("DISPONIT_PERSONVERNSVEIPTILSTAND", None)
            os.environ.pop("DISPONIT_PERSONVERNSVEIP_URL", None)
        assert kode == 0
        assert json.loads(tilstand.read_text(encoding="utf-8"))["feil"] == 1, \
            "den hoppet over kjøringen slettet en alt opptelt feil"
    finally:
        holder.execute("SELECT pg_advisory_unlock(%s)",
                       (personvernsveip.ARBEIDERNOKKEL,))
        holder.close()
        v.close()


def test_alarm_etter_to_sammenhengende_feilede_kjoringer(tmp_path,
                                                        monkeypatch):
    """En stille fristsveip er et register som eldes uten at noen ser
    det — og en oversittet innsynsfrist ingen fikk vite om er den ene
    feilen denne modulen finnes for å gjøre umulig.

    Første feil teller opp uten alarm; den ANDRE alarmerer — og
    JSON-linja bærer begge tallene, så journalen kan svare på spørsmålet
    uten å måtte lese tilstandsfilen.
    """
    from drift import kjor_personvernsveip as kjorer

    tilstand = tmp_path / "personvernsveip.json"
    monkeypatch.setenv("DISPONIT_PERSONVERNSVEIPTILSTAND", str(tilstand))
    monkeypatch.setenv("DISPONIT_PERSONVERNSVEIP_URL",
                       "postgresql://finnes-ikke@127.0.0.1:1/nei")
    monkeypatch.setattr(kjorer, "_koble",
                        lambda dsn: (_ for _ in ()).throw(RuntimeError("nede")))

    linjer = []
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
    ikke EXECUTE på sveipen (099 REVOKEr den), så en fallback ville bare
    byttet en tydelig oppstartsnekt mot «permission denied» i journalen
    hver natt — og en jobb som feiler likt hver natt er en jobb ingen
    leser."""
    from drift import kjor_personvernsveip as kjorer
    monkeypatch.setenv("DISPONIT_PERSONVERNSVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.delenv("DISPONIT_PERSONVERNSVEIP_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://skal-ikke-brukes/x")
    assert kjorer.main() == 2
    kode = "\n".join(
        re.sub(r"#.*$", "", linje) for linje in
        (ROT / "platform" / "drift" / "kjor_personvernsveip.py")
        .read_text(encoding="utf-8").splitlines())
    assert "DATABASE_URL" not in kode, \
        "kjøreren har fått en fallback til runtime-DSN-en"


@pg
@sveiperolle
def test_sveipekjoringen_gir_en_json_linje_med_tallene(migrator, tmp_path,
                                                      monkeypatch):
    """Én JSON-linje per kjøring, med tallene jobben faktisk målte — en
    jobb som ikke kunne måle rapporterer FUNN, aldri null."""
    from drift import kjor_personvernsveip as kjorer
    ten = _tenantnavn("json")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        _registrer(c, ten, eier, mottatt="current_date - 40",
                   lagre=_m4_lagre(migrator, 1))
    finally:
        c.close()
    monkeypatch.setenv("DISPONIT_PERSONVERNSVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.setenv("DISPONIT_PERSONVERNSVEIP_URL", SVEIP_DSN)
    linjer = []
    monkeypatch.setattr("builtins.print",
                        lambda *a, **k: linjer.append(a[0]) if not k.get(
                            "file") else None)
    assert kjorer.main() == 0
    linje = json.loads(linjer[-1])
    assert linje["hendelse"] == "personvernsveip"
    assert linje["feilet"] == 0 and linje["hoppet_over"] == 0
    assert linje["nye_funn"] >= 1 and linje["tenanter"] >= 1
    assert set(linje) == {"hendelse", "tenanter", "nye_funn",
                          "oppdaterte_funn", "lukkede_funn", "feilet",
                          "hoppet_over", "sammenhengende_feil", "alarm",
                          "tilstand_lagret"}


def test_v1_koer_ingen_varsel_og_sier_hvorfor():
    """AVGRENSNINGEN, PINNET.

    Manifestteksten sier «den varsler før fristen og gjør en oversittet
    frist til et funn». v1 gjør det andre fullt ut og det første som et
    FUNN (`frist_naermer_seg`), ikke som en e-post i varselkøen.

    Porten finnes for at den dagen noen legger til varselveien, skal de
    måtte slette DENNE testen — og da lese begrunnelsen som står rett
    ved siden av: grensen `m30-v1` ble registrert FØR koden og har ingen
    invariant om varselidempotens. M-21s grense HAR en
    (`varsel_duplisert_per_varslingspunkt`), og den finnes fordi en
    varslingsvei uten den er en vei å sende det samme varselet hver
    kadens til folk slutter å lese dem. Å bygge veien uten å ha felt
    dommen om den ville vært å legge til en fullmakt utenfor grensen.

    Konsekvensen er også pinnet: 099 rører ALDRI `varsel`-tabellen, og
    utvider derfor ingen CHECK på en alt bebodd tabell — det er den ene
    setningen som ellers ville gjort «grønn fra tom base» og «grønn mot
    seedet base» til to ulike utsagn (se `test_migrasjonen_er_ren_ddl`).
    """
    # Kommentarene OG strengliteralene strippes: hodekommentaren
    # forklarer nettopp hvorfor det ikke finnes en varselvei, og
    # vaktens RAISE-tekst forklarer at en forlengelse ER underrettet.
    # En port som felte de setningene ville tvunget fram en fil som ikke
    # kan si hva den ikke gjør. Det som måles er IDENTIFIKATORER.
    kode = re.sub(r"'[^']*'", "''",
                  _uten_kommentarer(MIGRASJON.read_text(encoding="utf-8")))
    for ident in ("varsel", "varselvalg", "varsel_art_chk"):
        assert ident not in kode.lower(), \
            (f"099 rører «{ident}» — en varselvei krever en registrert"
             " invariant om idempotens, og grensen har ingen")
    from manifestskjema import M30_INVARIANTER
    assert not any("varsel" in i for i in M30_INVARIANTER), \
        "grensen har fått en varselinvariant — da skal veien bygges"


def test_varselvinduet_og_kadensen_hoerer_sammen():
    """VINDUET ER SJU DØGN, ikke M-9s tretti, og det er en dom.

    Hele fristen her er ÉN MÅNED (art. 12 nr. 3), ikke et år: et
    30-døgnsvindu ville reist `frist_naermer_seg` i samme øyeblikk saken
    ble registrert, og et funn som alltid står er et funn ingen leser.
    Porten binder tallet til begrunnelsen sin, og til at timeren er
    DAGLIG — de to hører sammen og skal endres sammen.
    """
    from drift import personvernsveip
    assert personvernsveip.VARSELVINDU_DOGN == 7
    assert personvernsveip.ALARM_ETTER_FEIL == 2
    timer = (ROT / "deploy" / "staging"
             / "disponit-personvernsveip.timer").read_text(encoding="utf-8")
    assert re.search(r"OnCalendar=\*-\*-\* \d\d:\d\d:\d\d UTC", timer), \
        "timeren er ikke daglig — vinduet er 7 × kadensen"
    assert "Persistent=true" in timer
    assert "RandomizedDelaySec" in timer


# ---------------------------------------------------------------------------
# Små hjelpere for HTTP-veien (m35/096-formen)
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
        " ('https://m30.test', %s) RETURNING bruker_id",
        ("s30h-" + secrets.token_hex(6),)).fetchone()[0]
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
