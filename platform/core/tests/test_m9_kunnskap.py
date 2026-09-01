"""M-9 v1 (migrasjon 095) — kravgrensens seks invarianter + de to ja-punktene.

`manifestskjema.M9_INVARIANTER` er kravlisten, og hver invariant har en
test som måler BÅDE forsøket og bruddet:

  1. `begrep_uten_kilde` — et begrep uten kilde er urepresenterbart.
     Målt med DIREKTE DML (CHECKen), ikke bare gjennom døren: en dør som
     kontrollerer noe basen ikke gjør, er en dør noen kan gå utenom.
  2. `sok_uten_eksplisitt_regconfig` — FALLGRUVEN, målt STATISK over
     modulens SQL og Python. Porten er verifisert mot en konstruert
     `to_tsvector(term)`-streng: en port som aldri har vært rød, er en
     port ingen vet virker.
  3. `utlopt_begrep_uten_funn` — et begrep forbi `gyldig_til` er et FUNN.
     Og: sveipen kjørt to ganger gir ETT funn, ikke to.
  4. `tenantlekkasje_i_begrepssok` — tenant A treffer aldri tenant Bs
     begreper, verken med direkte DML eller over API. Isolasjonen er
     RLS sin, ikke et WHERE-ledd.
  5. `begrep_endret_uten_ny_versjon` — UPDATE av term/forklaring/kilde
     avvises av vakten, for ENHVER rolle. Ny versjon gjennom døren lar
     den gamle raden bestå og flytter `gjeldende`.
  6. `ui_axe_alvorlige_brudd` — bor i
     `platform/core/ui/test/kunnskap.test.js` (jsdom + axe-core), kjørt
     av `npm test`. Porten her måler at testfilen finnes og faktisk
     kjører axe over flaten.

De to ja-punktene:

  * `ddl_begge_kjoringer_gronne` — 095 er ren DDL (pglast), kjørt og
    byte-bundet i denne basen, og fasit-pinnet.
  * `sok_ytelse_maalt_for_og_etter` — MÅLINGEN FELTE GIN-INDEKSEN.
    `test_gin_paa_tsvector_er_uraakelig_under_rls` holder funnet fast:
    `ts_match_vq` er ikke LEAKPROOF, så `sok @@ q` kan aldri bli en
    indeksbetingelse på en RLS-tabell. Tallene står i migrasjonens
    hodekommentar og i commit-teksten.

I tillegg: SØKET VIRKER PÅ NORSK, med et FASITSETT som sier både hva
som SKAL treffe og hva som IKKE skal. En søketest uten fasit måler bare
at spørringen ikke kastet feil.

Alle DB-tester konstruerer egen tilstand; ingen delt fixture.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, ANNEN_TENANT,  # noqa: F401
                       TENANT, app, klient, migrator, miljo)
from .test_m37 import _sett_kontekst

ROT = Path(__file__).resolve().parents[3]
MODULROT = ROT / "platform" / "modules" / "m09_kunnskap"
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "095_m9_begrepsregister.sql")
SVEIPEN = ROT / "platform" / "drift" / "begrepssveip.py"
KJOREREN = ROT / "platform" / "drift" / "kjor_begrepssveip.py"
API_MODUL = ROT / "platform" / "core" / "api" / "kunnskap.py"
FLATEN = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
          / "kunnskap.js")
UI_TEST = (ROT / "platform" / "core" / "ui" / "test" / "kunnskap.test.js")

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

#: Sveipens EGEN innlogging. Den er den eneste rollen med EXECUTE på
#: `m9_sveip_utlopte` — at migrator IKKE har det, er selv en måling:
#: kryss-tenant-sveipen er sveiperollens og ingen annens.
SVEIP_DSN = os.environ.get("DISPONIT_TEST_KUNNSKAPSSVEIP_DSN", "")
sveiperolle = pytest.mark.skipif(
    not SVEIP_DSN, reason="DISPONIT_TEST_KUNNSKAPSSVEIP_DSN ikke satt")

I_MORGEN = date.today() + timedelta(days=1)
I_GAAR = date.today() - timedelta(days=1)

#: Hvilken INVARIANT hver test dekker. Egen akse, og med vilje ikke
#: `test_api.DEKNING`: den er FEILVEI-registeret, og
#: `test_api_porter.test_hver_feilvei_har_en_test` krever at hver nøkkel
#: der finnes i `api.feil.FEIL`. En invariant er ikke en feilvei — å
#: låne registeret ville gjort begge portene til noe annet enn de er.
M9_DEKNING: dict[str, list[str]] = {}


def invariant(*navn: str):
    """Merker en test som dekning for én eller flere M-9-invarianter.

    Merkelappen er ikke dokumentasjon: `test_grensen_dekker_...` under
    krever at HVER invariant i `M9_INVARIANTER` har minst én test. En
    invariant uten test er en formulering.
    """
    def dekorator(fn):
        for n in navn:
            M9_DEKNING.setdefault(n, []).append(fn.__name__)
        return fn
    return dekorator


# ---------------------------------------------------------------------------
# Riggen
# ---------------------------------------------------------------------------

def _dor(m, sql, args, tenant=TENANT):
    """Ett dørkall som eieren, med tenantkonteksten satt først."""
    _sett_kontekst(m, tenant)
    m.execute("SET ROLE disponit_kunnskap_eier")
    ut = m.execute(sql, args).fetchone()
    m.execute("RESET ROLE")
    m.commit()
    return ut


def _registrer(m, term, forklaring="En forklaring.", *, tenant=TENANT,
               eier="juridisk", kilde="kilde://intern/1",
               gyldig_til=None, aktor="test", bid=None):
    return _dor(m, "SELECT m9_registrer_begrep(%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, term, forklaring, eier, kilde,
                 gyldig_til or I_MORGEN, aktor, bid), tenant)[0]


def _ny_versjon(m, term, forklaring, *, tenant=TENANT, eier="juridisk",
                kilde="kilde://intern/2", gyldig_til=None, aktor="test",
                bid=None):
    return _dor(m, "SELECT m9_ny_begrepsversjon(%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, term, forklaring, eier, kilde,
                 gyldig_til or I_MORGEN, aktor, bid), tenant)[0]


def _sok(m, sporring, *, tenant=TENANT, grense=50):
    _sett_kontekst(m, tenant)
    m.execute("SET ROLE disponit_kunnskap_eier")
    rader = m.execute("SELECT * FROM m9_sok(%s,%s,%s)",
                      (tenant, sporring, grense)).fetchall()
    m.execute("RESET ROLE")
    m.rollback()
    return rader


def _sveip(m, vindu=30):
    """Sveipen kjøres UTEN tenantkontekst — den er kryss-tenant."""
    m.rollback()
    m.execute("SELECT set_config('disponit.tenant','',false)")
    m.execute("SET ROLE disponit_kunnskap_eier")
    rad = m.execute("SELECT * FROM m9_sveip_utlopte(%s)", (vindu,)).fetchone()
    m.execute("RESET ROLE")
    m.commit()
    return rad


def _funn(m, tenant=TENANT):
    _sett_kontekst(m, tenant)
    m.execute("SET ROLE disponit_kunnskap_eier")
    rader = m.execute("SELECT * FROM m9_apne_funn(%s,%s)",
                      (tenant, 500)).fetchall()
    m.execute("RESET ROLE")
    m.rollback()
    return rader


@pytest.fixture()
def rent(migrator):
    """Tømmer M-9-tabellene for de to testtenantene. Vaktene nekter
    DELETE — som de skal — så oppryddingen må skru dem av, og AT den må
    det er selv et bevis: ingen rolle, heller ikke eieren, kan viske ut
    en begrepshistorikk i drift."""
    for tabell, trigger in (("begrepsfunn", "m9_funn_vakt"),
                            ("begrep", "m9_begrep_vakt")):
        migrator.execute(f"ALTER TABLE {tabell} DISABLE TRIGGER {trigger}")
    for tenant in (TENANT, ANNEN_TENANT):
        _sett_kontekst(migrator, tenant)
        migrator.execute("DELETE FROM begrepsfunn")
        migrator.execute("DELETE FROM begrep")
    for tabell, trigger in (("begrepsfunn", "m9_funn_vakt"),
                            ("begrep", "m9_begrep_vakt")):
        migrator.execute(f"ALTER TABLE {tabell} ENABLE TRIGGER {trigger}")
    migrator.commit()
    yield migrator


# ---------------------------------------------------------------------------
# Invariant 1: begrep_uten_kilde
# ---------------------------------------------------------------------------

@pg
@invariant("begrep_uten_kilde")
def test_begrep_uten_kilde_er_urepresenterbart_i_direkte_dml(rent):
    """DEN BÆRENDE: «svar uten tilstrekkelig kildegrunnlag avvises» er en
    DATAMODELL, ikke en policy.

    Målt med DIREKTE DML som EIEREN — altså den rollen som HAR INSERT på
    tabellen. En dør som sjekker noe basen ikke gjør, er en dør noen kan
    gå utenom; her er det basen som sier nei, og døren gir bare en bedre
    setning.

    Fire former måles: `NULL` (NOT NULL) og tre rene blanktegnsverdier
    (CHECKen `kilde ~ '[^[:space:]]'`). Bare NOT NULL ville sluppet
    gjennom ett mellomrom — og en kilde som er ett mellomrom er nøyaktig
    påstanden katalogen sier skal avvises. `'\t\n '` står i listen fordi
    den FELTE husets vanlige form: en `btrim`-lengde trimmer bare
    mellomrom, så tabulator og linjeskift passerte den. Regexformen
    krever ett tegn som ikke er blankt, uansett klasse.

    MUTASJONEN SOM DREPER DENNE: fjern CHECKen på `kilde`, la `kilde`
    bli nullable, eller sett formen tilbake til en `btrim`-lengde.
    """
    for kilde, forventet in ((None, psycopg.errors.NotNullViolation),
                             ("   ", psycopg.errors.CheckViolation),
                             ("", psycopg.errors.CheckViolation),
                             ("\t\n ", psycopg.errors.CheckViolation)):
        _sett_kontekst(rent, TENANT)
        rent.execute("SET ROLE disponit_kunnskap_eier")
        with pytest.raises(forventet):
            rent.execute(
                "INSERT INTO begrep (tenant, begrep_id, term, forklaring,"
                " eier, kilde, gyldig_til, versjonsnr, gjeldende,"
                " opprettet_av) VALUES (%s,%s,'avtale','x','eier',%s,"
                " %s,1,true,'test')",
                (TENANT, uuid.uuid4(), kilde, I_MORGEN))
        rent.rollback()
    # …og døren gir den SETNINGEN, ikke en constraint-melding.
    with pytest.raises(psycopg.errors.CheckViolation) as ei:
        _registrer(rent, "avtale", kilde="  ")
    assert "kildegrunnlag" in str(ei.value)
    rent.rollback()
    # Den lovlige veien er upåvirket.
    assert _registrer(rent, "avtale", kilde="kilde://intern/1") is not None


@pg
@invariant("begrep_uten_kilde")
def test_kildekravet_gjelder_ogsaa_nye_versjoner(rent):
    """En kilde kan ikke forsvinne ved versjonering heller. Uten dette
    kunne et begrep fødes med kilde og miste den i versjon to — og
    kildekravet ville vært en portal, ikke en invariant."""
    _registrer(rent, "frist")
    with pytest.raises(psycopg.errors.CheckViolation):
        _ny_versjon(rent, "frist", "ny tekst", kilde=" ")
    rent.rollback()
    rader = _sok(rent, "frist")
    assert len(rader) == 1 and rader[0][6] == 1, \
        "det avviste versjonsforsøket etterlot en halv tilstand"


# ---------------------------------------------------------------------------
# Invariant 2: sok_uten_eksplisitt_regconfig — FALLGRUVEN, målt statisk
# ---------------------------------------------------------------------------

#: Alle tekstsøkfunksjonene som har en ETT-arguments overload som leser
#: `default_text_search_config` fra sesjonen. Listen er lukket med vilje:
#: en ny funksjon i klassen skal legges til her BEVISST.
TSFUNKSJONER = ("to_tsvector", "to_tsquery", "plainto_tsquery",
                "phraseto_tsquery", "websearch_to_tsquery")

#: Filene porten dekker: ALT som kan komme til å bygge en tsvector eller
#: en tsquery i denne modulen.
DEKTE_FILER = ("MIGRASJON", "SVEIPEN", "KJOREREN", "API_MODUL", "FLATEN")


def _uten_kommentarer(tekst: str, suffiks: str) -> str:
    """Kommentarer strippet, linjenummereringen bevart.

    Porten måler KODE, ikke prosa. Migrasjonens egen hodekommentar
    forklarer fallgruven ved å SKRIVE `to_tsvector(x)` — og en port som
    felte den, ville tvunget neste person til å beskrive fellen uten å
    nevne den. Linjene erstattes med tomme linjer i stedet for å
    fjernes, så bruddmeldingen fortsatt peker på riktig linjenummer.
    """
    merke = {".sql": "--", ".py": "#", ".js": "//"}[suffiks]
    ut = []
    for linje in tekst.splitlines():
        i = linje.find(merke)
        ut.append(linje if i < 0 else linje[:i])
    return "\n".join(ut)


def _regconfigbrudd(tekst: str) -> list[str]:
    """Selve målingen, med teksten som parameter.

    Utløst av samme grunn som fasitportens `_bytefeil`: negativtesten
    under må kunne spille av `to_tsvector(term)` gjennom NØYAKTIG denne
    funksjonen. En negativtest som gjenskaper sammenligningen sin egen
    vei beviser at kopien virker, ikke at porten gjør det.

    Regelen: hvert kall må ha et FØRSTE argument som er en
    regconfig-literal — `'norwegian'` eller `'norwegian'::regconfig`.
    Alt annet (én-arguments, eller en variabel som første argument) er
    et brudd: en variabel kunne båret hva som helst, og da er
    konfigurasjonen igjen noe utenfor filen bestemmer.
    """
    brudd = []
    for fn in TSFUNKSJONER:
        for m in re.finditer(rf"\b{fn}\s*\(", tekst):
            # Finn det balanserte argumentuttrykket etter parentesen.
            i = m.end()
            dybde, arg = 1, []
            while i < len(tekst) and dybde:
                c = tekst[i]
                if c == "(":
                    dybde += 1
                elif c == ")":
                    dybde -= 1
                    if not dybde:
                        break
                arg.append(c)
                i += 1
            argument = "".join(arg).strip()
            forste = argument.split(",")[0].strip()
            if not re.fullmatch(r"'[a-z_]+'(::regconfig)?", forste):
                linje = tekst[:m.start()].count("\n") + 1
                brudd.append(
                    f"linje {linje}: {fn}( … ) uten eksplisitt regconfig"
                    f" som første argument (fikk {forste!r}) — én-"
                    "arguments-formen leser default_text_search_config"
                    " fra SESJONEN, og en indeks bygget under én"
                    " konfigurasjon slutter STILLE å treffe spørringer"
                    " kjørt under en annen")
    return brudd


@invariant("sok_uten_eksplisitt_regconfig")
def test_ingen_tekstsoek_uten_eksplisitt_regconfig():
    """FALLGRUVEN. `to_tsvector(x)` og `to_tsquery(x)` med ETT argument
    leser `default_text_search_config` fra sesjonen — og riggen denne
    modulen ble bygget i har den satt til `pg_catalog.english`. En norsk
    ordliste indeksert slik ville stemmet «avtaler» til «avtaler» og
    aldri truffet «avtale»: søket ville sett ut som om det virket, og
    bare aldri truffet.

    Porten er STATISK og dekker BÅDE SQL og Python — og flaten, som ikke
    har noen grunn til å bygge en tsquery i det hele tatt, men som blir
    målt så den ikke begynner.

    MUTASJONEN SOM DREPER DENNE: svekk `_regconfigbrudd` — godta et
    variabelt første argument, dropp en funksjon fra `TSFUNKSJONER`,
    eller la `DEKTE_FILER` falle sammen.
    """
    globaler = globals()
    assert len(DEKTE_FILER) == 5
    brudd = []
    for navn in DEKTE_FILER:
        sti = globaler[navn]
        assert sti.exists(), f"{navn} finnes ikke: {sti}"
        kode = _uten_kommentarer(sti.read_text(encoding="utf-8"), sti.suffix)
        for b in _regconfigbrudd(kode):
            brudd.append(f"{sti.name}: {b}")
    assert not brudd, "\n".join(brudd)


@invariant("sok_uten_eksplisitt_regconfig")
def test_regconfigporten_feller_en_konstruert_ettargumentsform():
    """NEGATIVEN: porten over er grønn i dag uansett om den måler noe.
    Den er først bevist når mutasjonen gjør den rød. Fallgruven spilles
    derfor av i minnet, gjennom NØYAKTIG samme funksjon — én-arguments-
    formen, og den snikere varianten der første argument er en VARIABEL
    (som kunne båret hva som helst).
    """
    for farlig in (
            "SELECT to_tsvector(b.term)",
            "SELECT to_tsvector(term) @@ to_tsquery('norwegian', q)",
            "conn.execute(\"SELECT websearch_to_tsquery(%s)\", (q,))",
            "SELECT to_tsvector(v_konfig, b.term)",
            "SELECT plainto_tsquery(p_sporring)"):
        assert _regconfigbrudd(farlig), \
            f"porten så ikke fallgruven i {farlig!r}"
    # …og kommentarstrippingen gjør ikke porten blind: den samme
    # fallgruven i KODE felles fortsatt, selv om en kommentar over den
    # beskriver den ordrett.
    kode = _uten_kommentarer(
        "-- fellen er to_tsvector(term) uten regconfig\n"
        "SELECT to_tsvector(b.term);\n", ".sql")
    assert len(_regconfigbrudd(kode)) == 1, \
        "kommentarstrippingen skjulte eller doblet fallgruven"
    assert not _regconfigbrudd(_uten_kommentarer(
        "-- fellen er to_tsvector(term)\n"
        "SELECT to_tsvector('norwegian', b.term);\n", ".sql")), \
        "porten felte en fallgruve som bare STO I EN KOMMENTAR"
    # …og den TRYGGE formen er grønn, i begge skrivemåter.
    for trygg in (
            "SELECT to_tsvector('norwegian', b.term)",
            "SELECT to_tsvector('norwegian'::regconfig, b.term)",
            "websearch_to_tsquery('norwegian', p_sporring)",
            "setweight(to_tsvector('norwegian', coalesce(term,'')), 'A')"):
        assert not _regconfigbrudd(trygg), \
            f"porten felte den korrekte formen {trygg!r}"


@pg
@invariant("sok_uten_eksplisitt_regconfig")
def test_den_generte_kolonnen_baerer_norsk_konfigurasjon_i_basen(rent):
    """Statisk port + LEVENDE måling. Kildeteksten kan si «norwegian» og
    basen likevel bære noe annet (en migrasjon kjørt fra en eldre fil,
    en kolonne bygget om for hånd). Her leses den faktiske
    kolonnedefinisjonen ut av katalogen — OG riggens
    `default_text_search_config` måles, så testen dokumenterer at de to
    er FORSKJELLIGE: det er nettopp derfor porten finnes."""
    rent.rollback()
    uttrykk = rent.execute(
        "SELECT pg_get_expr(d.adbin, d.adrelid) FROM pg_attrdef d"
        " JOIN pg_attribute a ON a.attrelid = d.adrelid"
        "                    AND a.attnum = d.adnum"
        " WHERE d.adrelid = 'begrep'::regclass AND a.attname = 'sok'"
    ).fetchone()[0]
    rent.rollback()
    assert "'norwegian'::regconfig" in uttrykk, uttrykk
    # Fallgruven er ikke teoretisk: sesjonen står på noe ANNET.
    sesjon = rent.execute("SHOW default_text_search_config").fetchone()[0]
    rent.rollback()
    assert sesjon != "pg_catalog.norwegian", (
        "riggen har tilfeldigvis norsk som sesjonskonfigurasjon — da"
        " måler ikke denne testen forskjellen den finnes for; sett"
        " default_text_search_config til noe annet")


# ---------------------------------------------------------------------------
# Søket VIRKER på norsk — med FASIT for hva som IKKE treffer
# ---------------------------------------------------------------------------

@pg
def test_soekefasit_boeying_treffer_og_sammensetning_treffer_ikke_baklengs(rent):
    """FASITSETTET. En søketest uten fasit måler bare at spørringen ikke
    kastet feil.

    SKAL TREFFE (norsk snowball-stemming, `to_tsvector('norwegian', …)`):
      * bøyde former av samme ord — «avtaler», «avtalen», «avtalene»,
        «avtales» stemmer alle til `avtal` og treffer «avtale»;
      * et treff i FORKLARINGEN, ikke bare i termen — «tjeneste» finner
        «leveranseavtale» fordi forklaringen nevner tjenester;
      * SAMMENSETNINGEN som helhet — «leveranseavtaler» finner
        «leveranseavtale».

    SKAL IKKE TREFFE, og det er ikke en mangel men en EGENSKAP ved
    snowball-konfigurasjonen som er verdt å skrive ned:
      * «leveranseavtale» dekomponeres IKKE. Et søk på «avtale» treffer
        derfor den raden bare hvis ordet står i forklaringen — ikke i
        kraft av at termen inneholder det. Norsk sammensetning krever en
        ordbok (`ispell`/`hunspell`) plattformen ikke har, og å påstå
        noe annet ville vært å love et søk vi ikke har bygget.
      * avledninger med annen endelse: «sletting» stemmer til `sletting`
        og «slette» til `slett` — de treffer ikke hverandre.
      * rene stoppord gir en TOM tsquery og null treff. Det er et annet
        svar enn listingen (tom søkestreng), og de to skal ikke smelte
        sammen.

    VEKTINGEN måles også: et treff i TERMEN rangeres over et treff bare
    i forklaringen (`setweight` A over B).
    """
    _registrer(rent, "avtale", "En bindende enighet mellom to parter.")
    _registrer(rent, "leveranseavtale",
               "Avtale som regulerer leveranser av tjenester.")
    _registrer(rent, "sletting", "Fjerning av data etter fristen.")

    def termer(q):
        return [r[1] for r in _sok(rent, q)]

    # SKAL TREFFE — bøying.
    for q in ("avtale", "avtaler", "avtalen", "avtalene", "avtales"):
        assert "avtale" in termer(q), f"bøyningen {q!r} traff ikke «avtale»"
    # SKAL TREFFE — sammensetningen som helhet.
    assert termer("leveranseavtaler") == ["leveranseavtale"]
    # SKAL TREFFE — forklaringen, ikke bare termen.
    assert termer("tjenester") == ["leveranseavtale"]

    # SKAL IKKE TREFFE — «sletting» og «slette» er ikke samme stamme.
    assert termer("slette") == []
    assert "sletting" in termer("slettingen")

    # SKAL IKKE TREFFE — rent stoppord gir tom tsquery, altså null treff.
    # Og det er noe ANNET enn listingen.
    assert termer("og") == []
    assert len(termer("")) == 3, "tom søkestreng er LISTINGEN, ikke et søk"

    # VEKTINGEN: termtreffet rangeres over forklaringstreffet.
    rader = _sok(rent, "avtale")
    assert [r[1] for r in rader] == ["avtale", "leveranseavtale"], rader
    assert rader[0][8] > rader[1][8], (
        "et treff i selve termen skal rangeres over et treff i en"
        " forklaring som bare nevner ordet (setweight A over B)")
    # …og at «leveranseavtale» er med, er FORKLARINGENS fortjeneste:
    # termen alene ville ikke truffet, fordi sammensetningen ikke
    # dekomponeres.
    rent.rollback()
    assert not rent.execute(
        "SELECT to_tsvector('norwegian','leveranseavtale')"
        " @@ websearch_to_tsquery('norwegian','avtale')").fetchone()[0], (
        "konfigurasjonen dekomponerer sammensetninger nå — da er"
        " fasiten over utdatert og skal skrives om, ikke slettes")
    rent.rollback()


# ---------------------------------------------------------------------------
# Invariant 5: begrep_endret_uten_ny_versjon
# ---------------------------------------------------------------------------

@pg
@invariant("begrep_endret_uten_ny_versjon")
def test_publisert_begrep_kan_ikke_endres_paa_plass(rent):
    """Ordlisten er en TIDSLINJE, ikke en tilstand. Vakten gjelder
    ENHVER rolle — også eieren, som er den eneste med UPDATE i det hele
    tatt. En ordliste uten historikk kan ikke svare på «hva sa vi den
    gangen», og det spørsmålet er hele grunnen til at en bedrift fører
    ordliste.

    MUTASJONEN SOM DREPER DENNE: la vakten slippe gjennom en av de
    frosne kolonnene.
    """
    bid = _registrer(rent, "avtale", "Første forklaring.")
    for kolonne, verdi in (("term", "avtalen"),
                           ("forklaring", "Omskrevet i det stille."),
                           ("kilde", "kilde://noe/annet"),
                           ("eier", "en annen"),
                           ("versjonsnr", 7)):
        _sett_kontekst(rent, TENANT)
        rent.execute("SET ROLE disponit_kunnskap_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as ei:
            rent.execute(
                f"UPDATE begrep SET {kolonne} = %s WHERE begrep_id = %s",
                (verdi, bid))
        assert "frosset" in str(ei.value), kolonne
        rent.rollback()
    # gyldig_til er også frosset — en fornyet dato er en NY versjon.
    _sett_kontekst(rent, TENANT)
    rent.execute("SET ROLE disponit_kunnskap_eier")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        rent.execute("UPDATE begrep SET gyldig_til = %s WHERE begrep_id = %s",
                     (I_MORGEN + timedelta(days=365), bid))
    rent.rollback()
    # DELETE avvises også — et publisert begrep slettes aldri.
    _sett_kontekst(rent, TENANT)
    rent.execute("SET ROLE disponit_kunnskap_eier")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        rent.execute("DELETE FROM begrep WHERE begrep_id = %s", (bid,))
    rent.rollback()


@pg
@invariant("begrep_endret_uten_ny_versjon")
def test_ny_versjon_lar_den_gamle_bestaa_og_flytter_gjeldende(rent):
    """Den positive halvdelen: veien FRAM er en ny rad, ikke en
    redigering. Den gamle raden står uendret med sin egen kilde og sin
    egen dato — det er nettopp den som svarer på «hva sa vi den gangen»."""
    v1 = _registrer(rent, "avtale", "Første forklaring.",
                    kilde="kilde://intern/v1")
    v2 = _ny_versjon(rent, "avtale", "Andre forklaring.",
                     kilde="kilde://intern/v2")
    assert v1 != v2

    rent.rollback()
    _sett_kontekst(rent, TENANT)
    rader = {r[0]: r for r in rent.execute(
        "SELECT begrep_id, term, forklaring, kilde, versjonsnr, gjeldende"
        " FROM begrep ORDER BY versjonsnr").fetchall()}
    rent.rollback()
    assert len(rader) == 2, "den gamle versjonen forsvant"
    gammel, ny = rader[v1], rader[v2]
    assert gammel[2] == "Første forklaring." and gammel[3] == "kilde://intern/v1"
    assert gammel[4] == 1 and gammel[5] is False
    assert ny[2] == "Andre forklaring." and ny[3] == "kilde://intern/v2"
    assert ny[4] == 2 and ny[5] is True

    # Søket viser BARE den gjeldende — historikken er evidens, ikke støy.
    treff = _sok(rent, "avtale")
    assert len(treff) == 1 and treff[0][2] == "Andre forklaring."


@pg
@invariant("begrep_endret_uten_ny_versjon")
def test_to_gjeldende_versjoner_av_samme_term_er_urepresenterbart(rent):
    """Den partielle unike indeksen gjør «ett gjeldende begrep per term»
    UMULIG framfor usannsynlig. To gjeldende versjoner er ikke en rar
    tilstand — det er to ulike svar på samme spørsmål, og da er
    ordlisten verdiløs."""
    _registrer(rent, "avtale", "Første.")
    _sett_kontekst(rent, TENANT)
    rent.execute("SET ROLE disponit_kunnskap_eier")
    with pytest.raises(psycopg.errors.UniqueViolation):
        rent.execute(
            "INSERT INTO begrep (tenant, begrep_id, term, forklaring, eier,"
            " kilde, gyldig_til, versjonsnr, gjeldende, opprettet_av)"
            " VALUES (%s,%s,'avtale','Andre','eier','k',%s,2,true,'test')",
            (TENANT, uuid.uuid4(), I_MORGEN))
    rent.rollback()
    # …og en avløst versjon blir ALDRI gjeldende igjen.
    _ny_versjon(rent, "avtale", "Andre.")
    _sett_kontekst(rent, TENANT)
    gammel = rent.execute(
        "SELECT begrep_id FROM begrep WHERE NOT gjeldende").fetchone()[0]
    rent.execute("SET ROLE disponit_kunnskap_eier")
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as ei:
        rent.execute("UPDATE begrep SET gjeldende = true WHERE begrep_id = %s",
                     (gammel,))
    assert "aldri gjeldende igjen" in str(ei.value)
    rent.rollback()


@pg
def test_gjenspill_er_stille_ja_og_annet_innhold_er_konflikt(rent):
    """SP-2-materialiteten (056-formen), på begge skrivedørene."""
    bid = uuid.uuid4()
    a = _registrer(rent, "avtale", "Tekst.", bid=bid)
    b = _registrer(rent, "avtale", "Tekst.", bid=bid)
    assert a == b == bid, "gjenspill skal være et stille ja"
    with pytest.raises(psycopg.errors.UniqueViolation) as ei:
        _registrer(rent, "avtale", "En ANNEN tekst.", bid=bid)
    assert "materiell idempotenskonflikt" in str(ei.value)
    rent.rollback()
    # …og en registrering av en term som ALT finnes, peker på riktig dør.
    with pytest.raises(psycopg.errors.UniqueViolation) as ei:
        _registrer(rent, "avtale", "Noe helt annet.")
    assert "m9_ny_begrepsversjon" in str(ei.value)
    rent.rollback()


# ---------------------------------------------------------------------------
# Invariant 3: utlopt_begrep_uten_funn
# ---------------------------------------------------------------------------

@pg
@invariant("utlopt_begrep_uten_funn")
def test_utlopt_begrep_gir_funn_og_sveipen_er_idempotent(rent):
    """Et begrep forbi `gyldig_til` er et FUNN, ikke en stille gammel
    sannhet folk fortsetter å lese.

    OG IDEMPOTENSEN: sveipen kjørt to ganger gir ETT funn, ikke to.
    Funnlisten vokser ikke med kadensen — en daglig sveip over et begrep
    som har vært utløpt i et år skal gi ett funn, ikke 365. `sist_sett_
    sveip` flyttes, `forst_sett` står.
    """
    _registrer(rent, "avtale", "Utløpt tekst.", gyldig_til=I_GAAR)
    _registrer(rent, "frist", "Snart utløpt.",
               gyldig_til=date.today() + timedelta(days=5))
    _registrer(rent, "kunde", "Står lenge.",
               gyldig_til=date.today() + timedelta(days=400))

    første = _sveip(rent)
    funn = {f[2]: f for f in _funn(rent)}
    assert set(funn) == {"avtale", "frist"}, funn
    assert funn["avtale"][1] == "utlopt"
    assert funn["frist"][1] == "utloper_snart"
    assert "kunde" not in funn, "et begrep langt fra fristen er ikke et funn"
    forst_sett = funn["avtale"][4]
    sist_sett = funn["avtale"][5]
    assert første[1] >= 2, f"to nye funn forventet, fikk {første}"

    andre = _sveip(rent)
    funn2 = {f[2]: f for f in _funn(rent)}
    assert set(funn2) == {"avtale", "frist"}, \
        "sveip nummer to duplisert funnene — funnlisten vokser med kadensen"
    # Tellerne i returraden er KRYSS-TENANT (sveipen er det), så de kan
    # ikke assertes eksakt i en delt testbase. Idempotensen måles der den
    # bor: på MINE rader.
    assert andre[2] >= 2, f"andre sveip oppdaterte ikke funnene: {andre}"
    assert funn2["avtale"][4] == forst_sett, "forst_sett ble flyttet"
    assert funn2["avtale"][5] >= sist_sett, "sist_sett_sveip ble ikke flyttet"

    # Direkte radtelling: ETT funn per (begrep, funntype), ikke to.
    rent.rollback()
    _sett_kontekst(rent, TENANT)
    n = rent.execute("SELECT count(*) FROM begrepsfunn").fetchone()[0]
    rent.rollback()
    assert n == 2, f"{n} funnrader etter to sveip — forventet 2"


@pg
@invariant("utlopt_begrep_uten_funn")
def test_funn_lukkes_naar_en_ny_versjon_fornyer_datoen(rent):
    """Funnet er ikke evig. Fornyes begrepet — altså skrives en NY
    versjon med ny dato — lukkes funnet på den gamle raden. Raden består
    (at noe VAR utløpt er også historikk), men den er ikke lenger åpen."""
    _registrer(rent, "avtale", "Utløpt tekst.", gyldig_til=I_GAAR)
    _sveip(rent)
    assert len(_funn(rent)) == 1

    _ny_versjon(rent, "avtale", "Fornyet tekst.",
                gyldig_til=date.today() + timedelta(days=400))
    resultat = _sveip(rent)
    assert _funn(rent) == [], "funnet ble ikke lukket av fornyelsen"
    assert resultat[3] >= 1, f"forventet minst ett lukket funn: {resultat}"

    rent.rollback()
    _sett_kontekst(rent, TENANT)
    rad = rent.execute(
        "SELECT apen, lukket_ts FROM begrepsfunn").fetchone()
    rent.rollback()
    assert rad is not None, "funnraden ble SLETTET — historikken forsvant"
    assert rad[0] is False and rad[1] is not None


@pg
@invariant("utlopt_begrep_uten_funn")
def test_funn_slettes_aldri_og_ferskheten_gaar_aldri_bakover(rent):
    """Vakten på funnet, målt direkte: DELETE avvises, identiteten er
    frosset, og `sist_sett_sveip` kan ikke settes tilbake — en ferskhet
    som kan settes tilbake er ingen ferskhet."""
    _registrer(rent, "avtale", "Utløpt.", gyldig_til=I_GAAR)
    _sveip(rent)
    _sett_kontekst(rent, TENANT)
    rent.execute("SET ROLE disponit_kunnskap_eier")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        rent.execute("DELETE FROM begrepsfunn")
    rent.rollback()
    _sett_kontekst(rent, TENANT)
    rent.execute("SET ROLE disponit_kunnskap_eier")
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as ei:
        rent.execute("UPDATE begrepsfunn SET sist_sett_sveip ="
                     " sist_sett_sveip - interval '1 day'")
    assert "aldri bakover" in str(ei.value)
    rent.rollback()


@pg
@invariant("utlopt_begrep_uten_funn")
def test_sveipen_nekter_aa_kjore_med_tenantkontekst(rent):
    """Sveipen er KRYSS-TENANT og kjøres uten kontekst — det er
    forutsetningen for at policyen `m9_sveip_leser_uten_tenantkontekst`
    skal være det SNEVRE vinduet den er ment å være. En kaller som har
    satt en kontekst ber om noe annet enn det funksjonen gjør, og skal
    få vite det i stedet for å få et halvt svar."""
    rent.rollback()
    _sett_kontekst(rent, TENANT)
    rent.execute("SET ROLE disponit_kunnskap_eier")
    with pytest.raises(psycopg.errors.InsufficientPrivilege) as ei:
        rent.execute("SELECT * FROM m9_sveip_utlopte(30)")
    assert "KRYSS-TENANT" in str(ei.value)
    rent.rollback()


# ---------------------------------------------------------------------------
# Invariant 4: tenantlekkasje_i_begrepssok
# ---------------------------------------------------------------------------

@pg
@invariant("tenantlekkasje_i_begrepssok")
def test_soek_treffer_aldri_en_annen_tenants_begreper(rent):
    """SP-1 + RLS. Tenant B har et begrep med NØYAKTIG samme term og
    samme ord i forklaringen — så et treff ville vært et treff, ikke en
    tilfeldighet.

    Målt på tre nivåer:
      * gjennom DØREN (som er den eneste veien runtime har);
      * med DIREKTE DML som eieren — altså rollen som HAR SELECT;
      * og med et kall der `p_tenant` PÅSTÅR den andre tenanten mens
        konteksten er min egen: `krev_tenantkontekst` binder de to, så
        parameteret alene er aldri autoritet.
    """
    _registrer(rent, "avtale", "Vår egen definisjon av avtale.")
    _registrer(rent, "avtale", "Naboens hemmelige definisjon av avtale.",
               tenant=ANNEN_TENANT)

    mine = _sok(rent, "avtale")
    assert len(mine) == 1
    assert mine[0][2] == "Vår egen definisjon av avtale."

    deres = _sok(rent, "avtale", tenant=ANNEN_TENANT)
    assert len(deres) == 1
    assert deres[0][2] == "Naboens hemmelige definisjon av avtale."

    # p_tenant er ikke autoritet: SP-1-porten binder den til konteksten.
    _sett_kontekst(rent, TENANT)
    rent.execute("SET ROLE disponit_kunnskap_eier")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        rent.execute("SELECT * FROM m9_sok(%s,'avtale',50)", (ANNEN_TENANT,))
    rent.rollback()


@pg
@invariant("tenantlekkasje_i_begrepssok")
def test_tsvector_kvalen_omgaar_ikke_rls_selv_som_eierrollen(rent):
    """DEN SKARPE: en SØKEINDEKS er en annen lesevei enn et vanlig
    predikat, og spørsmålet er om den veien kan komme UTENOM
    radpolitikken. Her spørres tabellen direkte, som EIERROLLEN, med
    NØYAKTIG den kvalen søket bruker — og med en tenantkontekst satt.

    Svaret skal være at RLS filtrerer uansett. Det er sant i to lag:
    policyen `tenant_isolasjon` gjelder for enhver rolle (FORCE), og
    eierrollens ENESTE kryss-tenant-policy krever at det IKKE er satt
    noen kontekst — som `krev_tenantkontekst` utelukker i hver dør.

    MUTASJONEN SOM DREPER DENNE: gi eierrollen en policy på
    057/088-formen (`USING (CURRENT_USER = 'disponit_kunnskap_eier')`),
    slik at dørene selv blir kryss-tenant og isolasjonen faller ned på
    et WHERE-ledd noen kan refaktorere bort.
    """
    _registrer(rent, "avtale", "Vår.")
    _registrer(rent, "avtale", "Naboens.", tenant=ANNEN_TENANT)
    rent.rollback()
    _sett_kontekst(rent, TENANT)
    rent.execute("SET ROLE disponit_kunnskap_eier")
    rader = rent.execute(
        "SELECT tenant, forklaring FROM begrep"
        " WHERE sok @@ websearch_to_tsquery('norwegian','avtale')"
    ).fetchall()
    rent.execute("RESET ROLE")
    rent.rollback()
    assert [r[0] for r in rader] == [TENANT], (
        "tsvector-kvalen så andre tenanters rader som eierrollen —"
        f" fikk {rader}")
    # …og uten kontekst er kryss-tenant-vinduet ÅPENT (det er det
    # sveipen bruker). At det finnes måles her, så ingen tror
    # isolasjonen kommer av at eieren ikke har rettigheter.
    rent.execute("SELECT set_config('disponit.tenant','',false)")
    rent.execute("SET ROLE disponit_kunnskap_eier")
    alle = rent.execute("SELECT DISTINCT tenant FROM begrep").fetchall()
    rent.execute("RESET ROLE")
    rent.rollback()
    assert {r[0] for r in alle} >= {TENANT, ANNEN_TENANT}, (
        "kryss-tenant-vinduet uten tenantkontekst er STENGT — da kan"
        " sveipen ikke finne tenantene, og isolasjonen over måler noe"
        " annet enn den tror")


@pg
@invariant("tenantlekkasje_i_begrepssok")
def test_http_soek_ser_bare_egen_tenant(rent, klient):
    """Samme invariant OVER API, med et ekte token. Naboens begrep har
    samme term og samme ord — så et treff ville vært et treff."""
    from .test_pr008 import _lesetoken
    _registrer(rent, "avtale", "Vår egen definisjon.")
    _registrer(rent, "avtale", "Naboens definisjon.", tenant=ANNEN_TENANT)
    tok, _ = _lesetoken(rent, TENANT, scopes=("decisions:read",))
    r = klient.get("/v1/kunnskap", params={"q": "avtaler"},
                   headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    kropp = r.json()
    assert [b["term"] for b in kropp["begreper"]] == ["avtale"]
    assert kropp["begreper"][0]["forklaring"] == "Vår egen definisjon."
    assert "Naboens" not in r.text


# ---------------------------------------------------------------------------
# YTELSEN — ja-punktet `sok_ytelse_maalt_for_og_etter`
# ---------------------------------------------------------------------------

@pg
def test_gin_paa_tsvector_er_uraakelig_under_rls(rent):
    """FUNNET SOM FELTE GIN-INDEKSEN, holdt fast som en port.

    `ts_match_vq` — operatoren `tsvector @@ tsquery` — er ikke LEAKPROOF.
    En ikke-leakproof kval kan ikke evalueres FØR en RLS-sikkerhetskval,
    og en indeksbetingelse er per definisjon det som evalueres først. På
    en tabell med ENABLE + FORCE ROW LEVEL SECURITY blir `sok @@ q`
    derfor ALLTID et `Filter:` og ALDRI et `Index Cond:` — uansett hvor
    mange GIN-indekser som står der.

    Målt i migrasjonens hodekommentar (50 006 begreper, median av 15):
    20.956 ms uten GIN, 20.362 ms MED GIN (samme plan), 0.264 ms med
    samme GIN når RLS er av. Indeksen virker; den er bare utilgjengelig
    så lenge RLS står — og RLS står.

    Denne testen BYGGER indeksen, måler at planen ikke rører den, og
    river den igjen. Uten den ville neste person lagt den inn i god tro,
    betalt skriving og disk for ingenting, og trodd søket var indeksert.

    MUTASJONEN SOM DREPER DENNE: `ts_match_vq` blir LEAKPROOF, eller
    tenantgrensen i søkeveien flyttes fra RLS til et predikat. Begge
    deler er reelle vedtak som SKAL felle denne testen — og da er det
    tallene over som skal måles på nytt, ikke assertene som skal fjernes.
    """
    rent.rollback()
    lekk = rent.execute(
        "SELECT proleakproof FROM pg_proc WHERE proname = 'ts_match_vq'"
    ).fetchone()
    rent.rollback()
    assert lekk is not None and lekk[0] is False, (
        "`ts_match_vq` er ikke lenger merket som ikke-leakproof — hele"
        " begrunnelsen for å utelate GIN-indeksen skal da måles på nytt")

    # Ingen GIN-indeks i treet: det er selve vedtaket. Kommentarene
    # strippes — hodekommentaren FORKLARER hvorfor indeksen ikke står,
    # og en port som felte den ville tvunget neste person til å beskrive
    # vedtaket uten å nevne det.
    assert "USING gin" not in _uten_kommentarer(
        MIGRASJON.read_text(encoding="utf-8"), ".sql"), (
        "095 har fått en GIN-indeks igjen — MÅL FØRST (se"
        " hodekommentaren i migrasjonen)")

    _registrer(rent, "avtale", "En bindende enighet.")
    rent.rollback()
    rent.execute("CREATE INDEX m9_gin_proev ON begrep USING gin (sok)")
    rent.commit()          # indeksen må OVERLEVE rollbacken i finally
    try:
        rent.execute("ANALYZE begrep")
        _sett_kontekst(rent, TENANT)
        # `enable_seqscan = off` er ikke pynt: uten den kunne planen
        # valgt seq scan av kostnadsgrunner, og testen ville målt en
        # kostnadsmodell i stedet for sikkerhetsregelen. Med den er
        # planleggeren PRESSET til å bruke en indeks hvis den kan — og
        # den kan fortsatt ikke bruke GIN-indeksen til `@@`.
        rent.execute("SET enable_seqscan = off")
        plan = "\n".join(r[0] for r in rent.execute(
            "EXPLAIN SELECT b.begrep_id FROM begrep b WHERE b.gjeldende"
            " AND b.sok @@ websearch_to_tsquery('norwegian','avtale')"
        ).fetchall())
    finally:
        rent.rollback()
        rent.execute("DROP INDEX m9_gin_proev")
        rent.commit()
    assert "m9_gin_proev" not in plan, (
        "planleggeren brukte GIN-indeksen under RLS — da er funnet"
        f" utdatert og tallene skal måles på nytt:\n{plan}")
    assert "Filter:" in plan and "sok @@" in plan, plan


@pg
def test_sveipeindeksen_er_naabar_og_daekker_kandidatutvalget(rent):
    """DEN ANDRE HALVDELEN AV YTELSESMÅLINGEN: indeksen som FAKTISK
    virker. `begrep_gjeldende_gyldig_til` bærer sveipens kandidatutvalg,
    og den er nåbar under RLS fordi `date_le` ER leakproof — i motsetning
    til `ts_match_vq` over. Målt: 40.976 → 0.013 ms over 50 006
    begreper (hodekommentaren i 095).

    Her måles EGENSKAPEN som gjør tallet mulig, ikke tallet: at
    datokvalen blir en `Index Cond`."""
    rent.rollback()
    assert rent.execute(
        "SELECT proleakproof FROM pg_proc WHERE proname = 'date_le'"
    ).fetchone()[0] is True
    rent.rollback()
    # Nok rader til at planleggeren HAR et valg: med femti rader er
    # enhver indeks like god, og testen ville målt tilfeldigheten.
    # Radene settes inn direkte (som eieren) — dette er en
    # ytelsesmåling, ikke en dørtest.
    _sett_kontekst(rent, TENANT)
    rent.execute("SET ROLE disponit_kunnskap_eier")
    rent.execute(
        "INSERT INTO begrep (tenant, begrep_id, term, forklaring, eier,"
        " kilde, gyldig_til, versjonsnr, gjeldende, opprettet_av)"
        " SELECT %s, gen_random_uuid(), 'ytelsesbegrep-' || i,"
        "        'forklaring ' || i, 'eier', 'kilde://intern/' || i,"
        "        current_date + 400, 1, true, 'test'"
        "   FROM generate_series(1, 4000) i", (TENANT,))
    rent.execute("RESET ROLE")
    rent.commit()
    rent.execute("ANALYZE begrep")
    _sett_kontekst(rent, TENANT)
    rent.execute("SET enable_seqscan = off")
    plan = "\n".join(r[0] for r in rent.execute(
        "EXPLAIN SELECT b.begrep_id FROM begrep b"
        " WHERE b.gjeldende AND b.gyldig_til <= current_date + 30"
    ).fetchall())
    rent.rollback()
    assert "begrep_gjeldende_gyldig_til" in plan, plan
    assert "Index Cond" in plan and "gyldig_til" in plan, plan


# ---------------------------------------------------------------------------
# Sveipen som DRIFTSJOBB: hoppet over, alarm etter to feil, JSON-linja
# ---------------------------------------------------------------------------

@pg
@sveiperolle
def test_overlappende_sveip_hopper_over_og_rorer_ikke_feiltelleren(rent, tmp_path):
    """`artefaktrydding`-formen, ordrett: en kjøring som fant
    arbeidernøkkelen opptatt har verken lyktes eller feilet.

    Skrev den 0 her, ville en overlappende kjøring (manuell drift, flere
    verter, en henger som holder låsen) slettet en alt opptelt feil, og
    alarmen etter to sammenhengende feil ville aldri nådd frem.
    """
    from drift import begrepssveip
    from drift import kjor_begrepssveip as kjorer

    holder = psycopg.connect(MIGRATOR_DSN, autocommit=True)
    try:
        holder.execute("SELECT pg_advisory_lock(%s)",
                       (begrepssveip.ARBEIDERNOKKEL,))
        r = begrepssveip.kjor(rent, tidligere_feil=1)
        assert r.hoppet_over is True
        assert r.feilet is False and r.alarm_utlost is False
        assert (r.tenanter, r.nye, r.oppdaterte, r.lukkede) == (0, 0, 0, 0)

        # …og `main()` lar telleren stå NØYAKTIG som den sto.
        tilstand = tmp_path / "begrepssveip.json"
        tilstand.write_text(json.dumps({"feil": 1}), encoding="utf-8")
        import os
        os.environ["DISPONIT_BEGREPSSVEIPTILSTAND"] = str(tilstand)
        os.environ["DISPONIT_KUNNSKAPSSVEIP_URL"] = SVEIP_DSN
        try:
            kode = kjorer.main()
        finally:
            os.environ.pop("DISPONIT_BEGREPSSVEIPTILSTAND", None)
            os.environ.pop("DISPONIT_KUNNSKAPSSVEIP_URL", None)
        assert kode == 0
        assert json.loads(tilstand.read_text(encoding="utf-8"))["feil"] == 1, \
            "den hoppet over kjøringen slettet en alt opptelt feil"
    finally:
        holder.execute("SELECT pg_advisory_unlock(%s)",
                       (begrepssveip.ARBEIDERNOKKEL,))
        holder.close()


def test_alarm_etter_to_sammenhengende_feilede_kjoringer(tmp_path,
                                                         monkeypatch):
    """En stille utløpssveip er en ordliste som eldes uten at noen ser
    det. Første feil teller opp uten alarm; den ANDRE alarmerer — og
    JSON-linja bærer begge tallene, så journalen kan svare på spørsmålet
    uten å måtte lese tilstandsfilen."""
    from drift import kjor_begrepssveip as kjorer

    tilstand = tmp_path / "begrepssveip.json"
    monkeypatch.setenv("DISPONIT_BEGREPSSVEIPTILSTAND", str(tilstand))
    monkeypatch.setenv("DISPONIT_KUNNSKAPSSVEIP_URL",
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

    # …og en vellykket kjøring nullstiller telleren igjen.
    assert json.loads(tilstand.read_text(encoding="utf-8"))["feil"] == 2


def test_sveipen_nekter_aa_starte_uten_egen_dsn(tmp_path, monkeypatch):
    """INGEN fallback til `DATABASE_URL`. Runtime-rollen har med vilje
    ikke EXECUTE på sveipen (095 REVOKEr den), så en fallback ville bare
    byttet en tydelig oppstartsnekt mot «permission denied» i journalen
    hver natt — og en jobb som feiler likt hver natt er en jobb ingen
    leser."""
    from drift import kjor_begrepssveip as kjorer
    monkeypatch.setenv("DISPONIT_BEGREPSSVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.delenv("DISPONIT_KUNNSKAPSSVEIP_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://skal-ikke-brukes/x")
    assert kjorer.main() == 2
    kode = _uten_kommentarer(KJOREREN.read_text(encoding="utf-8"), ".py")
    assert "DATABASE_URL" not in kode, \
        "kjøreren har fått en fallback til runtime-DSN-en"


@pg
@sveiperolle
def test_sveipekjoringen_gir_en_json_linje_med_tallene(rent, tmp_path,
                                                       monkeypatch):
    """Én JSON-linje per kjøring, med tallene jobben faktisk målte — en
    jobb som ikke kunne måle rapporterer FUNN, aldri null."""
    from drift import kjor_begrepssveip as kjorer
    _registrer(rent, "avtale", "Utløpt.", gyldig_til=I_GAAR)
    monkeypatch.setenv("DISPONIT_BEGREPSSVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.setenv("DISPONIT_KUNNSKAPSSVEIP_URL", SVEIP_DSN)
    linjer = []
    monkeypatch.setattr("builtins.print",
                        lambda *a, **k: linjer.append(a[0]) if not k.get(
                            "file") else None)
    assert kjorer.main() == 0
    linje = json.loads(linjer[-1])
    assert linje["hendelse"] == "begrepssveip"
    assert linje["feilet"] == 0 and linje["hoppet_over"] == 0
    assert linje["nye_funn"] >= 1 and linje["tenanter"] >= 1
    assert set(linje) == {"hendelse", "tenanter", "nye_funn",
                          "oppdaterte_funn", "lukkede_funn", "feilet",
                          "hoppet_over", "sammenhengende_feil", "alarm",
                          "tilstand_lagret"}


# ---------------------------------------------------------------------------
# Ja-punkt: ddl_begge_kjoringer_gronne
# ---------------------------------------------------------------------------

@pg
def test_migrasjonen_er_kjort_og_bytebundet(migrator):
    """Den tomme kjøringen er målt direkte: 095 står i `migrasjoner` med
    checksum lik sha256 av filbytene i treet — samme byte-binding
    fasiten pinner mot main."""
    cs = migrator.execute(
        "SELECT checksum FROM migrasjoner WHERE versjon=95").fetchone()
    migrator.rollback()
    assert cs is not None, "095 er ikke kjørt i testbasen"
    fil_sha = hashlib.sha256(MIGRASJON.read_bytes()).hexdigest()
    assert cs[0] == fil_sha, \
        "095 i treet er ikke bytene basen kjørte — historikk er immutable"
    fasit = json.loads(
        (ROT / "platform" / "core" / "db" / "migrasjons-fasit.json")
        .read_text(encoding="utf-8"))
    assert fasit.get("095_m9_begrepsregister.sql") == fil_sha, \
        "fasiten pinner andre bytes enn treet bærer"


def test_migrasjonen_er_ren_ddl():
    """047-klassen: masse-DML i en migrasjon kan køe utsatte
    triggerhendelser som ALTER-setninger nekter å passere. 095 har ingen
    DML i det hele tatt og rører ingen EKSISTERENDE tabell — derfor er
    «grønn mot bebodd base» en EGENSKAP og ikke et håp, og derfor
    trenger den ingen seed i `sp10-provekjoring.py`."""
    import pglast
    sql = MIGRASJON.read_text(encoding="utf-8")
    dml, alter = [], []
    for raa in pglast.parse_sql(sql):
        navn = type(raa.stmt).__name__
        if navn in ("InsertStmt", "UpdateStmt", "DeleteStmt"):
            dml.append(navn)
        if navn == "AlterTableStmt":
            rel = raa.stmt.relation.relname
            if rel not in ("begrep", "begrepsfunn"):
                alter.append(rel)
    assert not dml, f"095 bærer toppnivå-DML {dml} — da er den en backfill"
    assert not alter, \
        f"095 ALTERer eksisterende tabeller {alter} — SP-10 krever seed"


def test_095_navngir_aldri_runtime_rollen_i_en_grant():
    """056/057-formen: `disponit` er lokalnavnet, og `migrer.py` er
    eneste rettighetskilde. Den ENESTE gangen `disponit` nevnes i 095 er
    i en betinget REVOKE — en rettighet som bare slutter å bli gitt er
    ikke trukket tilbake (035)."""
    sql = MIGRASJON.read_text(encoding="utf-8")
    for linje in sql.splitlines():
        if linje.lstrip().startswith("--"):
            continue
        assert "TO disponit;" not in linje, \
            f"095 grantar direkte til runtime-rollen: {linje!r}"


def test_kjoreren_speiler_095_rettighetene():
    """Rettighetsspeilet i `migrer.py`: runtime får EXECUTE på KUN de to
    LESEdørene, og skrivedørene + sveipen REVOKEs eksplisitt. Ingen
    tabellrettigheter på `begrep`/`begrepsfunn` noe sted — all lesing
    går gjennom dørene (SP-7)."""
    kjorer = (ROT / "deploy" / "staging" / "migrer.py").read_text(
        encoding="utf-8")
    assert "GRANT EXECUTE ON FUNCTION m9_sok(TEXT, TEXT, INT) TO {rolle};" \
        in kjorer
    assert "GRANT EXECUTE ON FUNCTION m9_apne_funn(TEXT, INT) TO {rolle};" \
        in kjorer
    for dor in ("m9_registrer_begrep", "m9_ny_begrepsversjon",
                "m9_sveip_utlopte"):
        assert f"GRANT EXECUTE ON FUNCTION {dor}(" not in kjorer or \
            f"REVOKE ALL ON FUNCTION {dor}(" in kjorer, \
            f"{dor} er grantet til runtime uten en tilsvarende REVOKE"
    for tabell in ("begrep", "begrepsfunn"):
        for verb in ("SELECT ON", "INSERT ON", "UPDATE ON", "DELETE ON"):
            assert f"{verb} {tabell} TO" not in kjorer, \
                f"runtime har fått {verb} {tabell} utenom dørene"
    # Sveiperollen får NØYAKTIG én EXECUTE og ingen tabellrettighet.
    assert "KUNNSKAPSSVEIP_RETTIGHETER" in kjorer
    mal = kjorer.split("KUNNSKAPSSVEIP_RETTIGHETER = \"\"\"")[1].split(
        "\"\"\"")[0]
    grants = [ln for ln in mal.splitlines()
              if ln.strip().startswith("GRANT")
              and "USAGE ON SCHEMA" not in ln]
    assert grants == [
        "GRANT EXECUTE ON FUNCTION m9_sveip_utlopte(INT) TO {rolle};"], grants


@pg
def test_alle_fem_dorene_eies_av_modulens_egen_rolle(migrator):
    """SECURITY DEFINER-dører som IKKE eies av `disponit_kunnskap_eier`
    ville kjørt som migrator — altså med eierens rettigheter, forbi hele
    modellen. Eierskapet står også i `eierskap-reparasjon.sql`; her
    måles basen."""
    rader = dict(migrator.execute(
        "SELECT p.proname, r.rolname FROM pg_proc p"
        " JOIN pg_roles r ON r.oid = p.proowner"
        " WHERE p.proname LIKE 'm9\\_%' AND p.prosecdef").fetchall())
    migrator.rollback()
    assert set(rader) == {"m9_registrer_begrep", "m9_ny_begrepsversjon",
                          "m9_sok", "m9_apne_funn", "m9_sveip_utlopte"}
    assert set(rader.values()) == {"disponit_kunnskap_eier"}

    eierskap = (ROT / "deploy" / "staging" / "eierskap-reparasjon.sql") \
        .read_text(encoding="utf-8")
    for dor in rader:
        assert f"'{dor}(" in eierskap, \
            f"{dor} mangler i eierskap-reparasjon.sql"


@pg
def test_hver_definer_kaller_krev_tenantkontekst_forst(migrator):
    """SP-1, målt på KILDEN i basen og ikke på filen: de fire
    tenantbundne dørene skal ha `krev_tenantkontekst` som første
    setning. Sveipen er unntaket, og den er unntaket EKSPLISITT — den er
    kryss-tenant og avviser tvert imot en kaller som HAR satt en
    kontekst."""
    kropper = dict(migrator.execute(
        "SELECT proname, prosrc FROM pg_proc"
        " WHERE proname LIKE 'm9\\_%' AND prosecdef").fetchall())
    migrator.rollback()
    for navn in ("m9_registrer_begrep", "m9_ny_begrepsversjon", "m9_sok",
                 "m9_apne_funn"):
        kropp = kropper[navn]
        setninger = [ln.strip() for ln in kropp.splitlines()
                     if ln.strip() and not ln.strip().startswith("--")]
        start = setninger.index("BEGIN")
        assert "krev_tenantkontekst" in setninger[start + 1], \
            f"{navn}: første setning er ikke SP-1-porten"
    assert "krev_tenantkontekst" not in kropper["m9_sveip_utlopte"]
    assert "KRYSS-TENANT" in kropper["m9_sveip_utlopte"]


@pg
def test_rls_staar_paa_begge_tabellene_med_force(migrator):
    """ENABLE + FORCE + `tenant_isolasjon`, og ingen BYPASSRLS noe sted i
    kjeden. Uten FORCE ville eieren — som er den ENESTE rollen med
    rettigheter her — sett alt."""
    for tabell in ("begrep", "begrepsfunn"):
        rls, force = migrator.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
            " WHERE relname = %s", (tabell,)).fetchone()
        assert rls and force, f"{tabell}: RLS {rls}, FORCE {force}"
        navn = {r[0] for r in migrator.execute(
            "SELECT polname FROM pg_policy"
            " WHERE polrelid = %s::regclass", (tabell,)).fetchall()}
        assert "tenant_isolasjon" in navn, (tabell, navn)
    migrator.rollback()
    # Kryss-tenant-policyen finnes KUN på `begrep`, KUN for SELECT.
    rader = migrator.execute(
        "SELECT c.relname, p.polcmd, pg_get_expr(p.polqual, p.polrelid)"
        " FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid"
        " WHERE p.polname = 'm9_sveip_leser_uten_tenantkontekst'"
    ).fetchall()
    migrator.rollback()
    assert len(rader) == 1 and rader[0][0] == "begrep", rader
    assert rader[0][1] == "r", "kryss-tenant-policyen er ikke KUN FOR SELECT"
    assert "disponit.tenant" in rader[0][2] and "IS NULL" in rader[0][2], \
        rader[0][2]
    for rolle in ("disponit_kunnskap_eier", "disponit_kunnskapssveip"):
        assert migrator.execute(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = %s",
            (rolle,)).fetchone()[0] is False, f"{rolle} har BYPASSRLS"
    migrator.rollback()


@pg
def test_sveiperollen_har_ingen_tabellrettigheter(migrator):
    """«NULL tabellrettigheter, EXECUTE på nøyaktig én funksjon.» Målt i
    basen, ikke i skriptet: en sveiperolle med SELECT på `begrep` ville
    vært en kryss-tenant lesevei ved siden av den ene som er tenkt."""
    rader = migrator.execute(
        "SELECT table_name, privilege_type FROM information_schema"
        ".table_privileges WHERE grantee = 'disponit_kunnskapssveip'"
    ).fetchall()
    migrator.rollback()
    assert rader == [], f"sveiperollen har tabellrettigheter: {rader}"


# ---------------------------------------------------------------------------
# Invariant 6: ui_axe_alvorlige_brudd — porten bor i npm-suiten
# ---------------------------------------------------------------------------

@invariant("ui_axe_alvorlige_brudd")
def test_flateporten_finnes_og_kjorer_axe():
    """Axe-porten kjøres av `npm test`, ikke herfra. Denne porten måler
    at den FINNES og faktisk kaller axe over flaten — en invariant uten
    en test er en formulering."""
    assert UI_TEST.exists(), f"axe-porten mangler: {UI_TEST}"
    tekst = UI_TEST.read_text(encoding="utf-8")
    assert "alvorligeBrudd" in tekst and "visKunnskap" in tekst
    assert "aria-live" in tekst, \
        "resultattellingen skal måles for aria-live i flateporten"


def test_flaten_har_ingen_innerhtml_og_ingen_hardkodet_tekst():
    """Husets stående regel, målt statisk på DENNE flaten: aldri
    `innerHTML`, all tekst gjennom `t(…)`."""
    js = FLATEN.read_text(encoding="utf-8")
    assert "innerHTML" not in js
    # Ingen norske strengliteraler utenom locale-nøkler og klassenavn.
    for m in re.finditer(r'text:\s*"([^"]+)"', js):
        raise AssertionError(
            f"hardkodet tekst i flaten: {m.group(1)!r} — bruk t(...)")


def test_locale_paritet_for_ui_kunnskap():
    """nb OG en. `t()` faller tilbake til NØKKELEN, ikke til nb — en
    manglende engelsk nøkkel ville vist `ui.kunnskap.kolonne.kilde` midt
    i en tabell."""
    nb = json.loads((ROT / "locales" / "nb.json").read_text(encoding="utf-8"))
    en = json.loads((ROT / "locales" / "en.json").read_text(encoding="utf-8"))
    mine = [k for k in nb if k.startswith("ui.kunnskap.")
            or k == "ui.nav.kunnskap"]
    assert len(mine) >= 20, f"for få nøkler i porten: {len(mine)}"
    mangler = [k for k in mine
               if not isinstance(en.get(k), str) or not en[k].strip()]
    assert not mangler, f"en.json mangler {mangler}"
    # …og hver nøkkel flaten slår opp, finnes.
    js = FLATEN.read_text(encoding="utf-8")
    for m in re.finditer(r't\("(ui\.kunnskap\.[a-z_.]+)"', js):
        assert m.group(1) in nb, f"flaten slår opp ukjent nøkkel {m.group(1)}"


# ---------------------------------------------------------------------------
# Registreringen: manifest, rute, enhet
# ---------------------------------------------------------------------------

def test_manifestet_er_gyldig_og_aerlig():
    """Manifestet validerer, sier under_utvikling/ikke_i_drift, og INGEN
    sjekklistepunkter er flippet uten måling. v1 er ordlisten — ingen
    RAG, ingen vektorbase, ingen eksterne kilder — og manifestet skal
    fortsatt si det etter at koden finnes."""
    import yaml
    from manifestskjema import valider_manifest
    tekst = (MODULROT / "manifest.yaml").read_text(encoding="utf-8")
    m = yaml.safe_load(tekst)
    assert not valider_manifest(m), valider_manifest(m)
    assert m["status"] == "under_utvikling"
    assert m["driftstilstand"] == "ikke_i_drift"
    assert m["avhengigheter"] == []
    for punkt, verdi in m["staging_sjekkliste"].items():
        assert verdi["status"] == "nei", (
            f"{punkt} er flippet til {verdi['status']} — et ja krever et"
            " artefakt, og v1 har ingen aksepthendelse")


def test_grensen_dekker_de_seks_invariantene_og_de_to_ja_punktene():
    """Grensen ble registrert FØR byggingen (§0-regelen). Denne porten
    måler at den ikke har flyttet seg underveis, og at HVER invariant
    har minst én test som dekker den."""
    from manifestskjema import KRAVGRENSER, M9_INVARIANTER
    grense = KRAVGRENSER["m9-v1"]
    assert grense["invarianter"] == M9_INVARIANTER
    assert grense["maks_brudd"] == 0 and grense["min_forsok"] >= 1
    assert set(grense["krav_ja"]) == {"ddl_begge_kjoringer_gronne",
                                      "sok_ytelse_maalt_for_og_etter"}
    udekket = [i for i in M9_INVARIANTER if not M9_DEKNING.get(i)]
    assert not udekket, f"invarianter uten test: {udekket}"
    ukjente = sorted(set(M9_DEKNING) - set(M9_INVARIANTER))
    assert not ukjente, f"tester merket med ukjente invarianter: {ukjente}"


def test_ruten_er_registrert_med_lesescopet_og_uten_skrivevei():
    """`Route(...)` og `RUTESCOPE`-linja i SAMME commit (test_pr008
    binder dem toveis). Her måles VALGET: `decisions:read`, og INGEN
    skriverute under `/v1/kunnskap`."""
    from api.app import RUTESCOPE, LESESCOPES
    assert RUTESCOPE[("GET", "/v1/kunnskap")] == "decisions:read"
    assert "decisions:read" in LESESCOPES
    skriv = [(m, s) for (m, s) in RUTESCOPE
             if s.startswith("/v1/kunnskap") and m != "GET"]
    assert not skriv, (
        f"v1 har fått en HTTP-skrivevei inn i ordlisten: {skriv} — den"
        " krever sitt eget scope, sin egen browsermutasjonsport og sin"
        " egen CSRF, ikke en GET-nabo")


def test_enheten_er_registrert_i_utrullingen():
    """Enheten, `SELVREVERS_ENHETER` og DSN-porten hører modulen til —
    aldri fundamentet, ellers stopper fundamentets egen deploy."""
    opp = (ROT / "deploy" / "staging" / "opp.sh").read_text(encoding="utf-8")
    assert "disponit-begrepssveip.service disponit-begrepssveip.timer" in opp
    assert "disponit-begrepssveip.timer\"" in opp, \
        "enheten mangler i SELVREVERS_ENHETER"
    assert "DISPONIT_KUNNSKAPSSVEIP_URL" in opp, "DSN-porten mangler"
    assert "systemctl enable --now disponit-begrepssveip.timer" in opp
    for navn in ("disponit-begrepssveip.service",
                 "disponit-begrepssveip.timer"):
        fil = ROT / "deploy" / "staging" / navn
        assert fil.exists(), f"{navn} mangler"
    timer = (ROT / "deploy" / "staging"
             / "disponit-begrepssveip.timer").read_text(encoding="utf-8")
    assert "RandomizedDelaySec" in timer and "Persistent=true" in timer
    tjeneste = (ROT / "deploy" / "staging"
                / "disponit-begrepssveip.service").read_text(encoding="utf-8")
    assert "LoadCredential=DISPONIT_KUNNSKAPSSVEIP_URL:" in tjeneste
    assert "StateDirectory=" in tjeneste, \
        "uten StateDirectory kan feiltelleren aldri persisteres"
