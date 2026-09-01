"""M-22 SaaS- og lisensagent v1 (migrasjon 098) — grensens seks
invarianter, målt.

Grensen `m22-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR koden
(§0-regelen). Hver invariant har en port her, og hver port måler BÅDE at
den lovlige veien virker og at bruddet avvises — en port som bare måler
det som skal gå igjennom har ikke målt en invariant.

  * `lisens_avsluttet_av_modulen` — V1-DOMMEN. Målt STATISK (sveipveien
    inneholder ingen UPDATE av `lisens`, og migrasjonen kaller ingen
    leverandør) OG FUNKSJONELT (en sveip endrer ikke ett eneste felt på
    én eneste lisensrad — hele raden sammenlignes, ikke bare `status`).
    I tillegg: en direkte UPDATE til `avsluttet` uten begrunnelse
    avvises av CHECK-en, og en statusovergang uten navngitt aktør av
    vakten.
  * `lisens_uten_eier` — direkte DML uten eier avvises (NOT NULL), en
    ukjent bruker-id av fremmednøkkelen, og døren avviser en «eier» som
    ikke er aktivt medlem av tenanten. Tre lag, samme sannhet.
  * `varsel_duplisert_per_varslingspunkt` — tre sveip på samme tilstand
    gir ETT varsel per punkt. Og porten er ikke «aldri varsle igjen»:
    en lisens som fornyes får sine varsler om NESTE periode.
  * OPPSIGELSESFRISTEN, som er hele grunnen til at modulen finnes:
    varslingspunktene regnes fra `fornyelsesdato -
    oppsigelsesfrist_dogn`, ikke fra fornyelsesdatoen. Porten måler
    begge halvdelene, og den andre er den skarpe: en lisens der en
    fornyelsesdato-basert regning ville køet NULL varsler får sine.
  * `forpass_stanset_ordinaer_sending` — DEN SKARPE. En injisert feil i
    M-22s forpass stanser verken den ordinære sendingen ELLER M-21s
    forpass, og feilen telles separat. Porten er MUTASJONSTESTET: samme
    sender uten `conn.rollback()` i M-22-blokken kjøres, og da SKAL den
    falle.
  * `tenantlekkasje_i_lisensregister` — tenant A ser aldri tenant Bs
    lisenser, verken ved direkte DML eller over API-et.
  * `ui_axe_alvorlige_brudd` — bor i
    `platform/core/ui/test/lisens.test.js` (jsdom + axe-core), som
    kjøres av `npm test`, ikke herfra.

I tillegg: varsel og anker skrives i SAMME transaksjon, evidenskjeden får
sin rad uten kundens produktnavn, migrasjonen er ren DDL (SP-10s premiss)
og grønn mot en BEBODD varseltabell, og den navngir aldri runtime-rollen.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import types
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT, VARSEL_DSN,  # noqa: F401
                       app, dekker, klient, migrator, miljo)
from .test_m37 import _sett_kontekst

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "098_m22_lisensregister.sql")
SENDER = ROT / "platform" / "drift" / "varselsender.py"

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")


# ---------------------------------------------------------------------------
# Riggen
# ---------------------------------------------------------------------------

def _rt():
    """Runtime-rollen — den som HAR EXECUTE på de fire API-dørene og
    ingen tabellrettighet på registeret."""
    from db.pg import koble
    return koble(DSN)


def _vs():
    """Varselsenderens rolle — den som har EXECUTE på sveipen og
    ingenting annet."""
    from db.pg import koble
    return koble(VARSEL_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    """Egen tenant per test. Sveipen er kryss-tenant og ser HELE basen,
    så en delt tenant ville gjort testene rekkefølgeavhengige — og en
    test som består fordi naboen ryddet er ingen port."""
    return f"t-m22-{merke}-{secrets.token_hex(4)}"


def _bruker(m, tenant, *, epost=None, aktiv=True, roller=("admin",)):
    """En identitet med medlemskap i tenanten. `epost` gjør den til en
    gyldig e-postmottaker for varselsenderen."""
    profil = {}
    if epost:
        profil = {"epost": epost, "epost_verifisert": True}
    _sett_kontekst(m, tenant)
    bid = m.execute(
        "INSERT INTO brukeridentitet (issuer, sub, profil)"
        " VALUES ('https://m22.test', %s, %s::jsonb) RETURNING bruker_id",
        ("s22-" + secrets.token_hex(6), json.dumps(profil))).fetchone()[0]
    m.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller, aktiv)"
        " VALUES (%s,%s,%s,%s)", (tenant, bid, list(roller), aktiv))
    m.commit()
    return bid


def _registrer(c, tenant, eier, *, produkt="Prosjektrom",
               leverandor="Nordvind AS", kilde="AVT-2026-7",
               fornyelse="current_date + 20", frist=None,
               fornyelsestype="automatisk", seter=25, kostnad="120000.00",
               valuta="NOK", punkter=None, lid=None, aktor="u-test"):
    """Én lisens gjennom døren, som runtime. `fornyelse` er et
    SQL-uttrykk, så testene kan skrive «om tjue døgn» uten å regne på
    kalenderen."""
    lid = lid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m22_registrer_lisens(%s,%s,%s,%s,%s,%s,%s::numeric,%s,"
        + fornyelse + ",%s,%s,%s,%s,%s)",
        (tenant, lid, leverandor, produkt, eier, seter, kostnad, valuta,
         fornyelsestype, frist, kilde, punkter, aktor))
    c.commit()
    return lid


def _sveip(v, grense=100):
    """Kjør sveipen én gang. -> totalt antall køede varsler.

    TALLET ER PLATTFORMVIDT, ikke tenantens: sveipen er kryss-tenant per
    konstruksjon og ser hver lisens i basen, også dem andre tester har
    lagt igjen. Assertene under teller derfor tenantens EGNE ankre
    (`_sveip_her`), ikke returverdien — en test som stolte på totalen
    ville vært rekkefølgeavhengig, og en test som består fordi naboen
    ryddet er ingen port.
    """
    n = v.execute("SELECT m22_koe_utlopsvarsler(%s)", (grense,)).fetchone()[0]
    v.commit()
    return n


def _sveip_her(v, m, tenant, grense=100):
    """Sveipen, målt som «hvor mange NYE ankre fikk NETTOPP denne
    tenanten» — den eneste tellingen som er sann uansett hva som ellers
    ligger i basen."""
    forut = len(_ankre(m, tenant))
    _sveip(v, grense)
    return len(_ankre(m, tenant)) - forut


def _varsler(m, tenant):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT ressurs_id, hendelse FROM varsel WHERE tenant=%s"
        "   AND art='lisensutlop' ORDER BY hendelse", (tenant,)).fetchall()
    m.rollback()
    return rader


def _ankre(m, tenant):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT lisens_id, dogn_for, fornyelsesdato, varsel_ref"
        "  FROM lisensvarsel_sendt WHERE tenant=%s"
        " ORDER BY fornyelsesdato, dogn_for", (tenant,)).fetchall()
    m.rollback()
    return rader


def _rad(m, tenant, lid):
    """HELE lisensraden, som en tuppel. Brukt av v1-dommens funksjonelle
    halvdel: porten sammenligner alt, ikke bare `status` — en sveip som
    endret `fornyelsesdato` eller `antall_seter` ville også vært modulen
    som rører en lisens."""
    _sett_kontekst(m, tenant)
    rad = m.execute("SELECT l.* FROM lisens l WHERE tenant=%s"
                    "   AND lisens_id=%s", (tenant, lid)).fetchone()
    m.rollback()
    return rad


# ---------------------------------------------------------------------------
# INVARIANT 1: lisens_avsluttet_av_modulen — V1-DOMMEN
# ---------------------------------------------------------------------------

def test_invariant_lisens_avsluttet_av_modulen_statisk():
    """V1-DOMMEN, målt på KILDEN — den halvdelen en testkjøring ikke kan
    se.

    Katalogteksten lover at modulen nedgraderer og avslutter abonnementer
    automatisk. Katalogens EGEN guard sier hvorfor det ikke kan være v1:
    «kritiske systemer og legal hold ekskluderes; tilgang kan
    gjenopprettes i angreperioden» — altså et unntaksregister, en
    angrefrist og en gjenopprettingsvei, tre mekanismer som ikke finnes.

    Porten måler tre ting på filen:

      1. SVEIPVEIEN INNEHOLDER INGEN UPDATE AV `lisens`. Verken
         `m22_koe_for_tenant` eller `m22_koe_utlopsvarsler` rører raden —
         de leser, og de skriver varsel, anker og evidens.
      2. INGEN LEVERANDØR-API. Migrasjonen snakker ikke med noen
         utenfor basen, og en `http`/`curl`/`dblink`-vei ville vært
         nøyaktig den oppsigelsen v1 ikke har mandat til.
      3. Det finnes ingen funksjon som setter `status='avsluttet'` uten
         en begrunnelsesparameter: `m22_marker_avsluttet` er den eneste,
         og den tar `p_begrunnelse`.

    MUTASJONEN SOM DREPER DENNE: legg en
    `UPDATE public.lisens SET status='avsluttet' …` i sveipen — den
    ville stått i §4 og blitt fanget her, også hvis den var gjerdet av en
    betingelse ingen test tilfeldigvis traff.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    sveip = sql[sql.index("-- 4. Sveipen"):sql.index("-- 5. Rettighetene")]
    # Kommentarene fjernes før målingen: teksten OVER beskriver med vilje
    # hva sveipen IKKE gjør, og en naiv delstrengsjekk ville felt sin egen
    # dokumentasjon.
    kode = "\n".join(l for l in sveip.splitlines()
                     if not l.lstrip().startswith("--"))
    for forbudt in ("UPDATE public.lisens", "UPDATE lisens",
                    "DELETE FROM public.lisens", "DELETE FROM lisens"):
        assert forbudt not in kode, (
            f"sveipen inneholder «{forbudt}» — modulen endrer en lisensrad,"
            " og v1-dommen er at den ikke gjør det")
    # Sveipen LESER `status` (den henter bare aktive lisenser) — det er
    # ikke det porten er ute etter. Den er ute etter at ordet
    # «avsluttet» ikke finnes i sveipveien i det hele tatt, og at ingen
    # SET-klausul rører kolonnen. En sveip som kunne skrive den terminale
    # verdien måtte ha nevnt den.
    assert "'avsluttet'" not in kode, \
        "sveipen nevner den terminale statusverdien — den skal ikke kunne" \
        " skrive den"
    assert "SET status" not in kode and "status =" not in kode.replace(
        "l.status = 'aktiv'", ""), \
        "sveipen tilordner statuskolonnen"

    hel = "\n".join(l for l in sql.splitlines()
                    if not l.lstrip().startswith("--"))
    for forbudt in ("dblink", "http_", "pg_curl", "COPY PROGRAM"):
        assert forbudt not in hel, (
            f"migrasjonen kaller ut av basen ({forbudt}) — en oppsigelse"
            " hos en leverandør er ikke v1")
    # Den ENESTE terminale veien, og den koster en begrunnelse.
    assert "m22_marker_avsluttet(\n    p_tenant TEXT, p_lisens_id UUID," \
           " p_begrunnelse TEXT, p_aktor TEXT)" in sql


@pg
def test_invariant_lisens_avsluttet_av_modulen_funksjonelt(migrator):
    """V1-DOMMENS ANDRE HALVDEL: en sveip endrer INGEN lisensrad.

    Riggen er den situasjonen katalogteksten ville handlet på: en lisens
    som er langt forbi beslutningsdatoen sin. Sveipen kjøres tre ganger,
    og HELE raden sammenlignes før og etter — ikke bare `status`. En
    sveip som hadde flyttet `fornyelsesdato` eller nullet `antall_seter`
    ville også vært modulen som rører en lisens, og en port som bare
    leste statuskolonnen ville sluppet den gjennom.

    Den forfalte lisensen får varslene sine (den skal ikke ties i hjel),
    og raden er urørt.

    MUTASJONEN SOM DREPER DENNE: la sveipen sette `status='avsluttet'`
    på lisenser der beslutningsdatoen er passert.
    """
    ten = _tenantnavn("dom")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _vs()
    try:
        lid = _registrer(c, ten, eier, fornyelse="current_date - 40")
        forut = _rad(migrator, ten, lid)
        assert forut is not None
        for _ in range(3):
            _sveip_her(v, migrator, ten)
        etter = _rad(migrator, ten, lid)
        assert etter == forut, \
            "sveipen endret en lisensrad — modulen avslutter, nedgraderer" \
            " eller rører ingenting i v1"
        # …og den forfalte lisensen ble faktisk VARSLET: alle tre punktene
        # er passert, så alle tre fyrer. En port der ingenting skjedde
        # ville bestått uten å ha målt noe.
        assert len(_varsler(migrator, ten)) == 3
    finally:
        c.close()
        v.close()


@pg
def test_avslutning_krever_begrunnelse_og_navngitt_aktor(migrator):
    """Den ANDRE halvdelen av v1-dommen: ingen kan avslutte en lisens i
    stillhet — heller ikke tabellens egen eier.

    Tre lag:
      1. CHECK-en: en direkte INSERT/UPDATE til `avsluttet` uten
         begrunnelse avvises. Målt på INSERT og ikke bare på UPDATE, og
         det er ikke et smutthull: radvakten er en BEFORE-trigger og fyrer
         FØR constraint-sjekken, så en UPDATE ville målt vakten (steg 2)
         og ikke CHECK-en.
      2. VAKTEN: en overgang som HAR begrunnelsen, men ingen navngitt
         aktør i sesjonen, avvises. En statusovergang er FORFATTET,
         aldri avledet — og en jobb som skulle avslutte fordi bruken var
         lav har ingen aktør å skrive.
      3. DØREN: en tom begrunnelse avvises med en melding som sier
         hvorfor.
    """
    ten = _tenantnavn("begrunn")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        lid = _registrer(c, ten, eier)

        # 1. CHECK-en, direkte DML som eieren.
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_lisens_eier")
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(
                "INSERT INTO lisens (tenant, lisens_id, leverandor, produkt,"
                " eier_bruker_id, fornyelsesdato, kilde, status,"
                " opprettet_av) VALUES (%s,%s,'X','Y',%s, current_date,"
                "                       'k','avsluttet','test')",
                (ten, uuid.uuid4(), eier))
        migrator.rollback()

        # 2. Vakten: full begrunnelse, men ingen aktør i sesjonen.
        migrator.execute(
            "SELECT set_config('disponit.tenant',%s,true),"
            "       set_config('disponit.aktor','',true)", (ten,))
        migrator.execute("SET LOCAL ROLE disponit_lisens_eier")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "UPDATE lisens SET status='avsluttet',"
                "       avslutt_begrunnelse='fordi', avsluttet_ts=now(),"
                "       avsluttet_av='noen'"
                " WHERE tenant=%s AND lisens_id=%s", (ten, lid))
        migrator.rollback()

        # 3. Døren: tom begrunnelse. Konteksten settes PER FORSØK —
        #    `set_config(..., true)` er transaksjonslokal, og rollbacken
        #    tar den med seg.
        for tom in (None, "", "   ", "\t\n "):
            _sett_kontekst(c, ten)
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                c.execute("SELECT m22_marker_avsluttet(%s,%s,%s,%s)",
                          (ten, lid, tom, "u-test"))
            c.rollback()

        # …og den lovlige veien går igjennom.
        _sett_kontekst(c, ten)
        c.execute("SELECT m22_marker_avsluttet(%s,%s,%s,%s)",
                  (ten, lid, "Erstattet av plattformens egen modul.",
                   "u-test"))
        c.commit()
        rad = _rad(migrator, ten, lid)
        _sett_kontekst(migrator, ten)
        status, begrunnelse, av = migrator.execute(
            "SELECT status, avslutt_begrunnelse, avsluttet_av FROM lisens"
            " WHERE tenant=%s AND lisens_id=%s", (ten, lid)).fetchone()
        migrator.rollback()
        assert status == "avsluttet" and av == "u-test"
        assert begrunnelse.startswith("Erstattet av")
        assert rad is not None

        # …og `avsluttet` er TERMINAL: det finnes ingen vei tilbake.
        _sett_kontekst(c, ten)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute("SELECT m22_marker_avsluttet(%s,%s,%s,%s)",
                      (ten, lid, "ombestemte meg", "u-test"))
        c.rollback()
    finally:
        c.close()


@pg
def test_lisensraden_slettes_aldri_og_identiteten_er_frosset(migrator):
    """Vakten, de tre andre reglene.

    En DELETE avvises (et lisensregister der rader forsvinner er et
    register ingen innkjøper kan lese bakover); identiteten er frosset (et
    annet produkt er en ANNEN lisens); og OPPSIGELSESFRISTEN er frosset,
    fordi `beslutningsdato` er avledet av den mens ankerets nøkkel bærer
    `fornyelsesdato` — en endret frist ville flyttet beslutningsdatoen bak
    ankre som alt er skrevet.
    """
    ten = _tenantnavn("vakt")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        lid = _registrer(c, ten, eier, frist=30)
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_lisens_eier")
        for sql, param in (
                ("DELETE FROM lisens WHERE tenant=%s AND lisens_id=%s",
                 (ten, lid)),
                ("UPDATE lisens SET produkt='Noe annet'"
                 " WHERE tenant=%s AND lisens_id=%s", (ten, lid)),
                ("UPDATE lisens SET leverandor='Noen andre'"
                 " WHERE tenant=%s AND lisens_id=%s", (ten, lid)),
                ("UPDATE lisens SET oppsigelsesfrist_dogn=90"
                 " WHERE tenant=%s AND lisens_id=%s", (ten, lid)),
                ("UPDATE lisens SET fornyelsesdato=current_date - 1"
                 " WHERE tenant=%s AND lisens_id=%s", (ten, lid))):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                migrator.execute(sql, param)
            migrator.rollback()
            _sett_kontekst(migrator, ten)
            migrator.execute("SET LOCAL ROLE disponit_lisens_eier")
        migrator.rollback()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# INVARIANT 2: lisens_uten_eier
# ---------------------------------------------------------------------------

@pg
def test_invariant_lisens_uten_eier(migrator):
    """En lisens uten eier er en lisens ingen forvalter — og i v1 er det
    en NOT NULL, ikke en rapport. Tre lag måles, fordi ett ville vært for
    lite:

      1. DIREKTE DML, som eieren av tabellen: en INSERT uten
         `eier_bruker_id` avvises av NOT NULL, og en med en ukjent
         bruker-id av fremmednøkkelen. Det er den bindende porten — den
         gjelder enhver skrivevei, også en fremtidig som glemmer døren.
      2. DØREN: en «eier» som ikke er AKTIVT MEDLEM av tenanten avvises.
         FK-en alene sier bare at id-en finnes ET STED i plattformen, og
         en lisens eid av en fremmed tenants bruker er nøyaktig like lite
         forvaltet som en uten eier.
      3. …og et INAKTIVT medlem av EGEN tenant er heller ikke en eier: en
         lisens hos noen som har sluttet er en lisens ingen sier opp.

    MUTASJONEN SOM DREPER DENNE: fjern NOT NULL på `eier_bruker_id`, eller
    fjern medlemskapssjekken i `m22_registrer_lisens`.
    """
    ten = _tenantnavn("eier")
    _bruker(migrator, ten)
    # 1a. Ingen eier i det hele tatt.
    _sett_kontekst(migrator, ten)
    migrator.execute("SET LOCAL ROLE disponit_lisens_eier")
    with pytest.raises(psycopg.errors.NotNullViolation):
        migrator.execute(
            "INSERT INTO lisens (tenant, lisens_id, leverandor, produkt,"
            " fornyelsesdato, kilde, opprettet_av)"
            " VALUES (%s,%s,'X','uten eier', current_date,'k','test')",
            (ten, uuid.uuid4()))
    migrator.rollback()

    # 1b. En eier som ikke finnes som identitet.
    _sett_kontekst(migrator, ten)
    migrator.execute("SET LOCAL ROLE disponit_lisens_eier")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        migrator.execute(
            "INSERT INTO lisens (tenant, lisens_id, leverandor, produkt,"
            " eier_bruker_id, fornyelsesdato, kilde, opprettet_av)"
            " VALUES (%s,%s,'X','fantom','bid_finnes_ikke', current_date,"
            "         'k','test')", (ten, uuid.uuid4()))
    migrator.rollback()

    # 2. Døren: en bruker fra en ANNEN tenant er ikke en eier her.
    annen = _tenantnavn("eier-annen")
    fremmed = _bruker(migrator, annen)
    c = _rt()
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _registrer(c, ten, fremmed)
        c.rollback()
        # 3. …og et INAKTIVT medlem av EGEN tenant heller ikke.
        sovende = _bruker(migrator, ten, aktiv=False)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _registrer(c, ten, sovende)
        c.rollback()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# OPPSIGELSESFRISTEN — det ene som skiller M-22 fra en generisk fristmodul
# ---------------------------------------------------------------------------

@pg
def test_oppsigelsesfristen_flytter_varslingspunktet(migrator):
    """DEN ENE TINGEN SOM GJØR M-22 TIL M-22.

    Et abonnement med 90 døgns oppsigelsesfrist må varsles 90+ døgn før
    fornyelse. Et varsel 30 døgn før en fornyelse man ikke lenger kan
    komme ut av er ikke et varsel — det er en regning som annonserer seg
    selv. Varslingspunktene regnes derfor fra `beslutningsdato =
    fornyelsesdato - oppsigelsesfrist_dogn`.

    Porten har to halvdeler, og den andre er den skarpe:

      A. 90 døgns frist og 60 døgn til fornyelse: beslutningsdatoen er
         PASSERT (`dogn_til_beslutning < 0`), og alle tre punktene fyrer.
      B. 90 døgns frist og 100 døgn til fornyelse: beslutningsdatoen er
         ti døgn fram, så 60- og 30-punktet er nådd mens 7 ikke er det —
         mens en regning FRA FORNYELSESDATOEN ville gitt NULL varsler.
         Den samme SQL-en kjøres begge veier i testen, så tallet 0 er
         målt og ikke påstått.

    MUTASJONEN SOM DREPER DENNE: bytt `l.beslutningsdato - v.dogn_for`
    mot `l.fornyelsesdato - v.dogn_for` i sveipen. Halvdel A ville
    fortsatt gitt varsler (bare ett), og halvdel B ville gitt null — som
    er nøyaktig lisensen som blir stille fornyet.
    """
    ten = _tenantnavn("frist")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _vs()
    try:
        # A. Fristen har alt gjort valget for oss.
        a = _registrer(c, ten, eier, produkt="Passert",
                       fornyelse="current_date + 60", frist=90)
        _sett_kontekst(c, ten)
        rad = c.execute(
            "SELECT fornyelsesdato, beslutningsdato, dogn_til_beslutning"
            "  FROM m22_lisenser(%s,%s) WHERE lisens_id=%s",
            (ten, 50, a)).fetchone()
        c.rollback()
        assert (rad[0] - rad[1]).days == 90, \
            "beslutningsdatoen er ikke fornyelsen minus oppsigelsesfristen"
        assert rad[2] == -30, rad
        assert _sveip_her(v, migrator, ten) == 3, \
            "en lisens som er forbi sin oppsigelsesfrist fikk ikke alle" \
            " sine varsler"

        # B. Den skarpe: fornyelsen er 100 døgn fram, men valget må tas om
        #    ti. Regnet fra fornyelsesdatoen ville INGEN punkter vært nådd.
        b = _tenantnavn("frist-b")
        eier_b = _bruker(migrator, b)
        _registrer(c, b, eier_b, produkt="Nesten",
                   fornyelse="current_date + 100", frist=90)
        naiv = migrator.execute(
            "SELECT count(*) FROM lisens l JOIN lisensvarsling vv"
            "    ON vv.tenant = l.tenant AND vv.lisens_id = l.lisens_id"
            " WHERE l.tenant = %s"
            "   AND l.fornyelsesdato - vv.dogn_for <= current_date",
            (b,)).fetchone()[0]
        migrator.rollback()
        assert naiv == 0, (
            "riggen måler ikke det den skal: en fornyelsesdato-basert"
            " regning skulle gitt NULL punkter her")
        assert _sveip_her(v, migrator, b) == 2, \
            "60- og 30-punktet er nådd fra beslutningsdatoen, 7 er det ikke"
        assert sorted(x[1] for x in _ankre(migrator, b)) == [30, 60]
    finally:
        c.close()
        v.close()


@pg
def test_uten_oppsigelsesfrist_er_beslutningsdatoen_fornyelsen(migrator):
    """NULL er ikke null døgn.

    En avtale uten oppsigelsesfrist finnes, og da er den siste dagen man
    kan velge selve fornyelsesdagen. `COALESCE(oppsigelsesfrist_dogn, 0)`
    gir nøyaktig det — og porten står her fordi den ANDRE lesningen
    («ingen frist betyr ingen varsling») ville gjort halve registeret
    taust.
    """
    ten = _tenantnavn("ingenfrist")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _vs()
    try:
        lid = _registrer(c, ten, eier, fornyelse="current_date + 20",
                         frist=None)
        _sett_kontekst(c, ten)
        rad = c.execute(
            "SELECT fornyelsesdato, beslutningsdato, oppsigelsesfrist_dogn"
            "  FROM m22_lisenser(%s,%s) WHERE lisens_id=%s",
            (ten, 50, lid)).fetchone()
        c.rollback()
        assert rad[0] == rad[1]
        assert rad[2] is None, "NULL ble til 0 — «vet ikke» ble til «ingen»"
        assert _sveip_her(v, migrator, ten) == 2
    finally:
        c.close()
        v.close()


# ---------------------------------------------------------------------------
# INVARIANT 3: varsel_duplisert_per_varslingspunkt
# ---------------------------------------------------------------------------

@pg
def test_invariant_varsel_duplisert_per_varslingspunkt(migrator):
    """TRE SVEIP PÅ SAMME TILSTAND → ETT VARSEL PER PUNKT.

    En fornyelse nærmer seg over mange sveip — timeren går hvert femte
    minutt — og et varsel per kjøring ville gjort varselet til støy folk
    lærer seg å overse. Da forsvinner de viktige med dem.

    Beslutningsdatoen ligger tjue døgn fram, så punktene 60 og 30 er nådd
    og 7 er det ikke. Det er med vilje: en port der ALLE punktene fyrer
    kan ikke skille «ett per punkt» fra «ett per lisens».

    MUTASJONEN SOM DREPER DENNE: fjern `NOT EXISTS`-leddet mot
    `lisensvarsel_sendt` i sveipen, eller `dogn_for` fra ankerets PK.
    """
    ten = _tenantnavn("idem")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _vs()
    try:
        lid = _registrer(c, ten, eier, fornyelse="current_date + 20")
        assert _sveip_her(v, migrator, ten) == 2, \
            "punktene 60 og 30 er nådd, 7 er det ikke"
        assert _sveip_her(v, migrator, ten) == 0
        assert _sveip_her(v, migrator, ten) == 0
        varsler = _varsler(migrator, ten)
        assert len(varsler) == 2, varsler
        assert {r[0] for r in varsler} == {str(lid)}
        # Ett anker per punkt, og hvert anker peker på SITT varsel.
        ankre = _ankre(migrator, ten)
        assert sorted(a[1] for a in ankre) == [30, 60]
        assert len({a[3] for a in ankre}) == 2, \
            "to ankre peker på samme varsel"
    finally:
        c.close()
        v.close()


@pg
def test_neste_periode_far_sitt_eget_varsel(migrator):
    """PORTEN ER IKKE «ALDRI VARSLE IGJEN».

    En lisens som fornyes får varsler om den NYE perioden. Det er nøyaktig
    derfor `fornyelsesdato` er med i ankerets primærnøkkel: uten leddet
    ville en lisens fått varsel om FØRSTE periode og aldri om de neste —
    og idempotensen ville blitt taushet.

    Riggen er den ekte situasjonen: en lisens som er ti døgn forbi sin
    beslutningsdato. Første sveip køer tre varsler. Så registreres
    fornyelsen — den nye fornyelsesdatoen ligger slik at beslutningsdatoen
    havner tjue døgn fram, der 60 og 30 er nådd med det samme mens 7 ikke
    er det. Neste sveip køer derfor NØYAKTIG to nye varsler: samme lisens,
    samme punkter, en annen fornyelsesdato i nøkkelen.

    MUTASJONEN SOM DREPER DENNE: ta `fornyelsesdato` ut av
    `lisensvarsel_sendt`s PK og ut av sveipens anti-join — da ville
    punktene stått som «alt varslet» for all framtid.
    """
    ten = _tenantnavn("fornyet")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _vs()
    try:
        lid = _registrer(c, ten, eier, fornyelse="current_date + 20",
                         frist=30)
        assert _sveip_her(v, migrator, ten) == 3, \
            "beslutningsdatoen er ti døgn forbi — alle tre punktene fyrer"
        assert _sveip_her(v, migrator, ten) == 0

        # FORNYELSEN. Ny fornyelsesdato 50 døgn fram, minus 30 døgns frist
        # = beslutningsdato 20 døgn fram: 60 og 30 nådd, 7 ikke.
        _sett_kontekst(c, ten)
        ny = c.execute(
            "SELECT m22_registrer_fornyelse(%s,%s,current_date + 50,%s)",
            (ten, lid, "u-test")).fetchone()[0]
        c.commit()
        assert ny is True

        # LISENSEN STÅR FORTSATT AKTIV — det er PERIODEN som ble fornyet.
        # Den opphører gjennom `m22_marker_avsluttet`, som koster en
        # skreven begrunnelse.
        _sett_kontekst(migrator, ten)
        status = migrator.execute(
            "SELECT status FROM lisens WHERE tenant=%s AND lisens_id=%s",
            (ten, lid)).fetchone()[0]
        migrator.rollback()
        assert status == "aktiv"

        assert _sveip_her(v, migrator, ten) == 2, \
            "neste periode fikk ikke sine varsler"
        assert _sveip_her(v, migrator, ten) == 0, \
            "…og den er idempotent på sine egne punkter"
        ankre = _ankre(migrator, ten)
        assert len(ankre) == 5
        # Fem ankre, TO fornyelsesdatoer: den nye perioden deler
        # `dogn_for` 60 og 30 med den gamle og skilles BARE av datoen.
        # Det er hele begrunnelsen for at `fornyelsesdato` står i PK-en.
        datoer = {a[2] for a in ankre}
        assert len(datoer) == 2
        ny_dato = max(datoer)
        assert sorted(a[1] for a in ankre if a[2] == ny_dato) == [30, 60]
    finally:
        c.close()
        v.close()


@pg
def test_fornyelsen_kan_bare_ga_framover_og_gjenspiller_stille(migrator):
    """En periode som flyttes BAKOVER ville gjemt seg bak et anker som alt
    er skrevet — og punktene for den «nye» perioden ville vært tause.
    Døren avviser det, og vakten gjør det uansett.

    Og gjenspillet er et STILLE JA: en tapt respons + nytt klikk med SAMME
    dato skal ikke skrive en ny evidensrad om en fornyelse som bare
    skjedde én gang.
    """
    ten = _tenantnavn("framover")
    eier = _bruker(migrator, ten)
    c = _rt()
    try:
        lid = _registrer(c, ten, eier, fornyelse="current_date + 20")
        _sett_kontekst(c, ten)
        assert c.execute(
            "SELECT m22_registrer_fornyelse(%s,%s,current_date + 400,%s)",
            (ten, lid, "u-test")).fetchone()[0] is True
        c.commit()
        # Gjenspill: samme dato igjen.
        _sett_kontekst(c, ten)
        assert c.execute(
            "SELECT m22_registrer_fornyelse(%s,%s,current_date + 400,%s)",
            (ten, lid, "u-test")).fetchone()[0] is False
        c.commit()
        # Bakover: avvist.
        _sett_kontekst(c, ten)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute(
                "SELECT m22_registrer_fornyelse(%s,%s,current_date + 10,%s)",
                (ten, lid, "u-test"))
        c.rollback()
        # …og gjenspillet skrev ingen ny evidensrad.
        _sett_kontekst(migrator, ten)
        assert migrator.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
            "   AND handling='lisens.fornyet'", (ten,)).fetchone()[0] == 1
        migrator.rollback()
    finally:
        c.close()


@pg
def test_sp2_materialiteten_dekker_hele_lisensen(migrator):
    """SP-2s materialitetssjekk dekker HELE lisensen, ikke bare hodet
    (096s CodeRabbit-lærdom, arvet).

    Oppsigelsesfristen avgjør NÅR noen får vite om fornyelsen, og
    varslingspunktene avgjør hvor mange ganger. Et gjenspill som endret
    ett av dem ville fått et stille ja på en lisens som varsler noe annet
    enn den kalleren tror den registrerte — og SP-2s hele poeng er at et
    gjenspill ikke skal kunne endre noe i det stille.
    """
    ten = _tenantnavn("sp2")
    eier = _bruker(migrator, ten)
    lid = uuid.uuid4()
    # FAST dato, ikke `current_date + …`: gjenspillet skal skille seg fra
    # det første kallet på NØYAKTIG det testen måler.
    dato = "2027-05-31"
    c = _rt()

    def _forsok(**endret):
        felt = dict(leverandor="Nordvind AS", produkt="Prosjektrom",
                    seter=25, kostnad="120000.00", valuta="NOK",
                    fornyelsestype="automatisk", frist=30,
                    kilde="AVT-2026-7", punkter=[60, 30, 7])
        felt.update(endret)
        _sett_kontekst(c, ten)
        return c.execute(
            "SELECT m22_registrer_lisens(%s,%s,%s,%s,%s,%s,%s::numeric,%s,"
            "                            %s::date,%s,%s,%s,%s,%s)",
            (ten, lid, felt["leverandor"], felt["produkt"], eier,
             felt["seter"], felt["kostnad"], felt["valuta"], dato,
             felt["fornyelsestype"], felt["frist"], felt["kilde"],
             felt["punkter"], "u-test")).fetchone()[0]

    try:
        assert _forsok() is True
        c.commit()
        # Identisk innhold: stille ja.
        assert _forsok() is False
        c.rollback()
        # …og hver av delene som ENDRER hva registeret varsler om.
        for endret in ({"frist": 90}, {"punkter": [90, 30]},
                       {"produkt": "Noe annet"}, {"seter": 26},
                       {"kostnad": "130000.00"}, {"valuta": "EUR"},
                       {"fornyelsestype": "manuell"},
                       {"kilde": "AVT-2026-8"}):
            with pytest.raises(psycopg.errors.UniqueViolation):
                _forsok(**endret)
            c.rollback()
    finally:
        c.close()


@pg
def test_varsel_og_anker_er_samme_transaksjon(migrator):
    """Et varsel køet uten anker, eller et anker uten varsel, skal være
    URESPRESENTERBART.

    Målt ved å rulle tilbake midt i: sveipen kalles, den rapporterer
    arbeidet sitt — og så ROLLBACK. Ingen av delene består. Hadde de to
    innsettingene ligget i hver sin transaksjon, ville den ene overlevd,
    og da ville idempotensen vært brutt i den ene retningen og varselet
    tapt i den andre.

    MUTASJONEN SOM DREPER DENNE: legg en COMMIT mellom varsel- og
    ankerinnsettingen (eller flytt den ene til en egen forbindelse).
    """
    ten = _tenantnavn("atomisk")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _vs()
    try:
        _registrer(c, ten, eier, fornyelse="current_date + 20")
        n = v.execute("SELECT m22_koe_utlopsvarsler(100)").fetchone()[0]
        assert n >= 2, "sveipen rapporterte ikke arbeidet den gjorde"
        v.rollback()
        assert _varsler(migrator, ten) == [], \
            "et varsel overlevde en rullet sveip — uten sitt anker"
        assert _ankre(migrator, ten) == [], \
            "et anker overlevde en rullet sveip — uten sitt varsel"
        # …og etter rollbacken er tilstanden uendret, så en ny sveip gjør
        # nøyaktig det samme arbeidet om igjen.
        assert _sveip_her(v, migrator, ten) == 2
        assert len(_varsler(migrator, ten)) == 2
        assert len(_ankre(migrator, ten)) == 2
    finally:
        c.close()
        v.close()


@pg
def test_ankeret_er_append_only(migrator):
    """Ankeret ER idempotensen: kunne raden fjernes eller endres, kunne
    varselet køes på nytt. Vakten nekter begge deler, også for eieren."""
    ten = _tenantnavn("anker")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _vs()
    try:
        _registrer(c, ten, eier, fornyelse="current_date + 20")
        assert _sveip_her(v, migrator, ten) == 2
        _sett_kontekst(migrator, ten)
        migrator.execute("SET LOCAL ROLE disponit_lisens_eier")
        for sql in ("DELETE FROM lisensvarsel_sendt WHERE tenant=%s",
                    "UPDATE lisensvarsel_sendt SET dogn_for=1"
                    " WHERE tenant=%s"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                migrator.execute(sql, (ten,))
            migrator.rollback()
            _sett_kontekst(migrator, ten)
            migrator.execute("SET LOCAL ROLE disponit_lisens_eier")
        migrator.rollback()
    finally:
        c.close()
        v.close()


# ---------------------------------------------------------------------------
# INVARIANT 4: forpass_stanset_ordinaer_sending — DEN SKARPE
# ---------------------------------------------------------------------------

def _injiser_feil(migrator):
    """Bytt `m22_koe_utlopsvarsler` med en som kaster. Returnerer den
    originale definisjonen, så kalleren kan legge den tilbake."""
    original = migrator.execute(
        "SELECT pg_get_functiondef("
        "    'm22_koe_utlopsvarsler(int)'::regprocedure)").fetchone()[0]
    migrator.rollback()
    migrator.execute("SET LOCAL ROLE disponit_lisens_eier")
    migrator.execute(
        "CREATE OR REPLACE FUNCTION m22_koe_utlopsvarsler(p_grense INT"
        " DEFAULT 100) RETURNS INT LANGUAGE plpgsql AS $$ BEGIN"
        " RAISE EXCEPTION 'injisert feil i M-22-forpasset'; END $$")
    migrator.commit()
    return original


def _legg_tilbake(migrator, original):
    migrator.execute("SET LOCAL ROLE disponit_lisens_eier")
    migrator.execute(original)
    migrator.commit()


def _ordinaert_varsel(migrator, ten, eier, hendelse):
    _sett_kontekst(migrator, ten)
    vid = migrator.execute(
        "INSERT INTO varsel (tenant, bruker_id, art, ressurs_type,"
        " ressurs_id, hendelse, tekstnokkel, parametre, epost_status)"
        " VALUES (%s,%s,'attestering_venter','policyutkast',%s,%s,"
        "         'varsel.attestering_venter','{}'::jsonb,'koet')"
        " RETURNING id",
        (ten, eier, "u-" + secrets.token_hex(4), hendelse)).fetchone()[0]
    migrator.commit()
    return vid


@pg
def test_invariant_forpass_stanset_ordinaer_sending(migrator, monkeypatch):
    """DEN SKARPESTE PORTEN I M-22.

    Utløpssveipen er lagt som FORPASS i varselsenderen fordi senderens
    rytme, backoff og idempotens er vunne argumenter et forpass arver
    gratis. PRISEN er denne invarianten: en feil i forpasset skal under
    INGEN omstendighet stanse den ordinære varselsendingen — OG den skal
    ikke kunne stanse M-21s forpass, som ligger rett foran i den samme
    funksjonen.

    Feilen INJISERES i `m22_koe_utlopsvarsler` — funksjonen erstattes med
    en som kaster, og legges tilbake til slutt. Deretter kjøres
    varselsenderen med et ordinært varsel i køen OG en plikt som skal
    varsles, og porten krever fire ting:

      * det ordinære varselet gikk UT (`sendt >= 1`), altså at forpassets
        fall ikke rev med seg noe;
      * M-21s FORPASS KJØRTE LIKEVEL: pliktens fristvarsel ble køet. Det
        er den halvdelen 096 ikke kunne måle, fordi M-22 ikke fantes da;
      * feilen ble TALT SEPARAT (`forpass_feil == 1`) — ikke slått
        sammen med `feilet`, som er e-poster køen selv retter opp med
        backoff. En sveip som ikke kjørte er noe helt annet: varsler som
        ALDRI BLE KØET, og som ingen backoff henter inn;
      * kontrollkjøringen UTEN injeksjon gir `forpass_feil == 0`, så
        telleren ikke bare alltid er 1.
    """
    from drift import varselsender

    ten = _tenantnavn("forpass")
    eier = _bruker(migrator, ten, epost="lisens@m22.test")
    monkeypatch.setenv("DISPONIT_PLATTFORMTENANT", ten)
    sendt_til: list[tuple] = []

    def _fanget(til, emne, tekst):
        sendt_til.append((til, emne, tekst))

    # KONTROLLKJØRINGEN først, uten injeksjon: telleren skal være 0.
    # Uten den ville `forpass_feil == 1` under bestått av en teller som
    # alltid er 1.
    _ordinaert_varsel(migrator, ten, eier, "r-kontroll")
    v = _vs()
    try:
        kontroll = varselsender.kjor(v, send=_fanget)
    finally:
        v.close()
    assert kontroll["forpass_feil"] == 0, kontroll
    assert kontroll["sendt"] >= 1, kontroll

    # Nytt ordinært varsel, en PLIKT som skal varsles (M-21s forpass), og
    # så INJEKSJONEN.
    vid2 = _ordinaert_varsel(migrator, ten, eier, "r-injisert")
    c = _rt()
    try:
        _sett_kontekst(c, ten)
        c.execute(
            "SELECT m21_registrer_plikt(%s,%s,%s,%s,%s,"
            "        now() + interval '5 days',%s,%s,%s)",
            (ten, uuid.uuid4(), "Nabo-plikten", eier, "sktl. par 8-3",
             "engang", None, "u-test"))
        c.commit()
    finally:
        c.close()
    sendt_til.clear()
    original = _injiser_feil(migrator)
    try:
        v = _vs()
        try:
            res = varselsender.kjor(v, send=_fanget)
        finally:
            v.close()
    finally:
        # Legg funksjonen tilbake uansett utfall — en injeksjon som blir
        # stående ville forgiftet hver senere test i suiten.
        _legg_tilbake(migrator, original)

    # DEN ORDINÆRE SENDINGEN GIKK, som om forpasset ikke fantes.
    assert res["sendt"] >= 1, res
    assert any(t[0] == "lisens@m22.test" for t in sendt_til)
    # FEILEN BLE TALT SEPARAT — og ikke som en feilet e-post.
    assert res["forpass_feil"] == 1, res
    assert res["feilet"] == 0, res
    # …og raden er faktisk merket sendt i basen, ikke bare i tellingen.
    _sett_kontekst(migrator, ten)
    assert migrator.execute(
        "SELECT epost_status FROM varsel WHERE tenant=%s AND id=%s",
        (ten, vid2)).fetchone()[0] == "sendt"
    # M-21s FORPASS OVERLEVDE M-22s FEIL. Uten den egne transaksjonen og
    # den egne except-grenen ville de to sveipene delt skjebne — og en
    # feil i den nyeste modulen ville gjort den eldstes varsler tause.
    # Plikten står med frist om fem døgn, så M-21s punkter 30 og 7 er nådd
    # mens 1 ikke er det — to varsler, ikke tre. Tallet er valgt slik med
    # vilje: en rigg der ALLE punktene fyrte kunne ikke skilt «forpasset
    # kjørte» fra «forpasset kjørte delvis».
    assert migrator.execute(
        "SELECT count(*) FROM varsel WHERE tenant=%s AND art='pliktfrist'",
        (ten,)).fetchone()[0] == 2, \
        "M-22s forpass rev med seg M-21s — de deler transaksjon"
    migrator.rollback()


@pg
def test_mutasjon_uten_rollback_faller_sendingen(migrator, monkeypatch):
    """MUTASJONSTESTEN, kjørt og ikke påstått.

    Porten over består hvis og bare hvis `conn.rollback()` står i M-22s
    except-gren. Denne testen BEVISER det: senderens kilde lastes, den
    ene linjen fjernes fra M-22-blokken, den muterte modulen kjøres mot
    den samme injiserte feilen — og da SKAL kjøringen falle.

    Uten rollbacken etterlater den kastende sveipen en abortert
    transaksjon, og hver eneste påfølgende setning i `kjor` — rekøingen,
    klaimet, statusskrivingen — feiler på «current transaction is
    aborted». Det er nøyaktig den formen en naiv `forpass(); ordinaer()`
    i samme transaksjon har.

    Faller denne testen (altså: den muterte senderen kjørte fint), er
    porten over ikke lenger skarp, og det er DA den skal si fra.
    """
    kilde = SENDER.read_text(encoding="utf-8")
    i = kilde.index("SELECT m22_koe_utlopsvarsler")
    hale = kilde[i:]
    j = hale.index("        conn.rollback()\n")
    mutert = kilde[:i] + hale[:j] + hale[j + len("        conn.rollback()\n"):]
    assert mutert != kilde and "m22_koe_utlopsvarsler" in mutert
    # …og M-21s rollback står FORTSATT der: mutasjonen skal treffe
    # nøyaktig én linje, ellers måler testen noe annet enn den tror.
    assert mutert.count("conn.rollback()") \
        == kilde.count("conn.rollback()") - 1

    modul = types.ModuleType("varselsender_mutert")
    modul.__file__ = str(SENDER)
    exec(compile(mutert, str(SENDER), "exec"), modul.__dict__)

    ten = _tenantnavn("mutasjon")
    eier = _bruker(migrator, ten, epost="mutasjon@m22.test")
    monkeypatch.setenv("DISPONIT_PLATTFORMTENANT", ten)
    _ordinaert_varsel(migrator, ten, eier, "r-mutasjon")

    original = _injiser_feil(migrator)
    try:
        v = _vs()
        try:
            with pytest.raises(psycopg.errors.InFailedSqlTransaction):
                modul.kjor(v, send=lambda *a: None)
        finally:
            v.rollback()
            v.close()
    finally:
        _legg_tilbake(migrator, original)


def test_forpasset_har_sin_egen_transaksjon_og_egen_telling():
    """STATISK PORT på senderen: M-22s forpass har sin EGEN blokk, sin
    egen rollback og sin egen telling — og den ligger ved siden av M-21s,
    ikke inni den.

    Grunnen til at dette måles på kildeteksten i tillegg til i basen: en
    refaktorering som slår de fem sveipene sammen i én transaksjon ville
    bestått enhver test der forpassene tilfeldigvis ikke feiler. Formen er
    den bindende.
    """
    kilde = SENDER.read_text(encoding="utf-8")
    assert "m22_koe_utlopsvarsler" in kilde, \
        "utløpssveipen er ikke koblet inn i senderens pre-pass"
    i_m21 = kilde.index("m21_koe_fristvarsler")
    i_m22 = kilde.index("m22_koe_utlopsvarsler")
    i_rekoe = kilde.index("varsel_rekoe")
    # Begge forpassene står FØR den ordinære sendingen (det er hele
    # poenget med et forpass), og M-22 etter M-21 — den yngste sist.
    assert i_m21 < i_m22 < i_rekoe, "forpassene kjører ikke før sendingen"
    blokk = kilde[i_m22:i_rekoe]
    assert "conn.rollback()" in blokk, \
        "M-22s forpass rydder ikke sin egen aborterte transaksjon — den" \
        " ordinære sendingen ville falt med den"
    assert "forpass_feil += 1" in blokk
    # …og M-21s blokk har fortsatt SIN egen: to sveiper i samme try ville
    # delt skjebne, og da ville invarianten vært halvt målt.
    assert "conn.rollback()" in kilde[i_m21:i_m22], \
        "M-21s forpass mistet sin egen rollback da M-22 ble lagt inn"
    assert '"forpass_feil": forpass_feil' in kilde


# ---------------------------------------------------------------------------
# INVARIANT 5: tenantlekkasje_i_lisensregister
# ---------------------------------------------------------------------------

@pg
def test_invariant_tenantlekkasje_i_lisensregister(migrator):
    """Tenant A ser aldri tenant Bs lisenser — verken ved direkte DML
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

    MUTASJONEN SOM DREPER DENNE: gjør `m22_sveip_tenantliste`
    betingelsesløs (`USING (true)`), eller fjern `krev_tenantkontekst`
    fra `m22_lisenser`.
    """
    a, b = _tenantnavn("lek-a"), _tenantnavn("lek-b")
    eier_a, eier_b = _bruker(migrator, a), _bruker(migrator, b)
    c = _rt()
    try:
        lid_a = _registrer(c, a, eier_a, produkt="A sin lisens")
        lid_b = _registrer(c, b, eier_b, produkt="B sin lisens")

        # 1. RLS, direkte DML som eieren.
        _sett_kontekst(migrator, a)
        migrator.execute("SET LOCAL ROLE disponit_lisens_eier")
        synlige = migrator.execute(
            "SELECT lisens_id FROM lisens ORDER BY lisens_id").fetchall()
        migrator.rollback()
        assert [r[0] for r in synlige] == [lid_a], synlige

        # 3. Kryss-tenant-policyen slår seg AV så snart konteksten står.
        #    Uten kontekst ser eieren begge (det er sveipens vindu).
        migrator.execute("SELECT set_config('disponit.tenant','',true)")
        migrator.execute("SET LOCAL ROLE disponit_lisens_eier")
        uten = {r[0] for r in migrator.execute(
            "SELECT lisens_id FROM lisens").fetchall()}
        migrator.rollback()
        assert {lid_a, lid_b} <= uten, \
            "sveipens vindu finnes ikke — den ville aldri sett en tenant"

        # 2. SP-1: parameteret er ikke kallerens frie valg.
        _sett_kontekst(c, a)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM m22_lisenser(%s,%s)", (b, 50)).fetchall()
        c.rollback()

        # …og lesedøren i EGEN kontekst gir bare egne rader.
        _sett_kontekst(c, a)
        rader = c.execute("SELECT lisens_id, produkt FROM m22_lisenser(%s,%s)",
                          (a, 50)).fetchall()
        c.rollback()
        assert [r[1] for r in rader] == ["A sin lisens"]
    finally:
        c.close()


@pg
def test_tenantlekkasje_over_api(migrator, klient):
    """Samme invariant, over HTTP: økten hos A får aldri se Bs lisenser.

    Tenanten kommer fra ØKTEN, aldri fra kroppen eller en parameter —
    her måles at det faktisk er slik hele veien ut til svaret.
    """
    b = _tenantnavn("api-b")
    eier_a = _bruker(migrator, TENANT)
    eier_b = _bruker(migrator, b)
    c = _rt()
    try:
        _registrer(c, TENANT, eier_a, produkt="A-lisens over API")
        _registrer(c, b, eier_b, produkt="B-lisens over API")
    finally:
        c.close()
    cookie, _csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/lisens", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    produkter = [p["produkt"] for p in r.json()["lisenser"]]
    assert "A-lisens over API" in produkter
    assert "B-lisens over API" not in produkter


# ---------------------------------------------------------------------------
# Evidenskjeden og HTTP-feilveien
# ---------------------------------------------------------------------------

@pg
def test_evidenskjeden_far_hver_handling_uten_produktnavnet(migrator):
    """Manifestet fører `m02_revisjonslogg` som REELL avhengighet: et
    utløpsvarsel skal kunne GJENFINNES i evidenskjeden, ikke bare i en
    varseltabell. Porten måler at det faktisk skjer — for registreringen,
    for fornyelsen, for avslutningen OG for hvert køet varsel.

    Formen er den ordinære (`payload_type='kryptert'`,
    `referansepayload IS NULL`): `revisjonslogg` har ingen
    ciphertext-kolonner (041 §4 dokumenterer det mot levende base), så
    det er formen HVER eksisterende skriver bruker.

    INNHOLDET ER IKKE ARKIVERT PÅ NYTT: leverandøren og produktnavnet er
    kundens tekst, og de skal ikke stå i evidenskjeden. At vi vet HVA en
    tenant betaler for er dessuten nettopp den opplysningen et
    lisensregister ikke skal spre videre i en logg ingen tenker på.
    """
    ten = _tenantnavn("evidens")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _vs()
    try:
        hemmelig = "Hemmelig produkt " + secrets.token_hex(4)
        leverandor = "Hemmelig leverandor " + secrets.token_hex(4)
        lid = _registrer(c, ten, eier, produkt=hemmelig,
                         leverandor=leverandor,
                         fornyelse="current_date + 20")
        _sveip(v)
        _sett_kontekst(c, ten)
        c.execute("SELECT m22_registrer_fornyelse(%s,%s,current_date+400,%s)",
                  (ten, lid, "u-test"))
        c.commit()
        _sett_kontekst(c, ten)
        c.execute("SELECT m22_marker_avsluttet(%s,%s,%s,%s)",
                  (ten, lid, "Vi bruker det ikke lenger.", "u-test"))
        c.commit()

        _sett_kontekst(migrator, ten)
        rader = migrator.execute(
            "SELECT handling, beslutning, kilde, aktor, payload_type,"
            "       referansepayload, input_hash, begrunnelse::text"
            "  FROM revisjonslogg WHERE tenant=%s ORDER BY id",
            (ten,)).fetchall()
        migrator.rollback()
        handlinger = [r[0] for r in rader]
        assert handlinger.count("lisens.registrert") == 1
        assert handlinger.count("lisens.utlopsvarsel_koet") == 2
        assert handlinger.count("lisens.fornyet") == 1
        assert handlinger.count("lisens.avsluttet") == 1
        for r in rader:
            assert r[1] == "TILLAT"
            assert r[2] == "m22_lisens"
            assert r[4] == "kryptert" and r[5] is None
            assert len(r[6]) == 64, "input_hash er ikke en sha256"
            assert hemmelig not in r[7]
            assert leverandor not in r[7]
        assert {r[3] for r in rader} == {"u-test", "lisenssveip"}
    finally:
        c.close()
        v.close()


@pg
@dekker("lisens_ulovlig_tilstand")
def test_http_avslutning_uten_begrunnelse_er_400_og_gjentakelse_409(
        migrator, klient):
    """FEILVEIEN, ende til ende.

    En begrunnelse som er tom svarer 400 (kroppen er feilformet — feltet
    mangler innhold), mens en lisens som ALT er avsluttet svarer 409
    `lisens_ulovlig_tilstand`: kroppen ER velformet, det er TILSTANDEN som
    sier nei. Forskjellen er hele forklaringen mennesket i flaten
    trenger, og den skal ikke være 500.

    Merk hvem som feller dommen: API-et sjekker ikke tilstanden. Det
    kaller døren og oversetter dørens ERRCODE. En flate eller et API som
    sjekket selv ville vært en ANDRE sannhet å komme i utakt med.

    MUTASJONEN SOM DREPER DENNE: la `_doerfeil` mappe
    `invalid_parameter_value` til 500, eller la endepunktet
    forhåndssjekke tilstanden og svare 400.
    """
    eier = _bruker(migrator, TENANT)
    cookie, csrf = _browserokt(migrator, ["admin"])

    r = _post(klient, cookie, csrf, "/v1/lisens",
              {"produkt": "Signeringstjeneste", "leverandor": "Havbris",
               "eier_bruker_id": eier, "kilde": "FAK-2026-1",
               "fornyelsesdato": "2027-05-31",
               "oppsigelsesfrist_dogn": 90,
               "kostnad_aar": "9800.50", "valuta": "EUR",
               "antall_seter": 5, "fornyelsestype": "automatisk"})
    assert r.status_code in (200, 201), r.text
    lid = r.json()["lisens_id"]
    assert r.json()["ny"] is True

    # Tom begrunnelse: kroppen er feilformet, ikke tilstanden.
    r = _post(klient, cookie, csrf, f"/v1/lisens/{lid}/avslutt",
              {"begrunnelse": "   "})
    assert r.status_code == 400, r.text
    assert r.json()["feil"] == "request_feilformet"

    # En dato som flyttes BAKOVER er derimot tilstanden som sier nei.
    r = _post(klient, cookie, csrf, f"/v1/lisens/{lid}/fornyelse",
              {"fornyelsesdato": "2026-01-01"})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "lisens_ulovlig_tilstand"

    # Den lovlige veien.
    r = _post(klient, cookie, csrf, f"/v1/lisens/{lid}/avslutt",
              {"begrunnelse": "Erstattet av plattformens egen signering."})
    assert r.status_code in (200, 201), r.text
    assert r.json()["avsluttet"] is True

    # …og en gang til: nå er det TILSTANDEN som sier nei.
    r = _post(klient, cookie, csrf, f"/v1/lisens/{lid}/avslutt",
              {"begrunnelse": "En gang til"})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "lisens_ulovlig_tilstand"

    # Begrunnelsen er den FØRSTE — det avviste kallet skrev ingenting.
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT avslutt_begrunnelse FROM lisens WHERE tenant=%s"
        "   AND lisens_id=%s", (TENANT, lid)).fetchone()[0] \
        == "Erstattet av plattformens egen signering."
    migrator.rollback()


@pg
def test_http_registrering_er_idempotent_paa_nokkelen(migrator, klient):
    """SP-2 (m35/m21-formen): samme Idempotency-Key + samme innhold gir
    SAMME lisens og et STILLE JA — en tapt respons + nytt klikk skal aldri
    føde lisensen en gang til. Samme nøkkel med ANNET innhold er en
    materiell konflikt kalleren skal se."""
    eier = _bruker(migrator, TENANT)
    cookie, csrf = _browserokt(migrator, ["admin"])
    nokkel = secrets.token_urlsafe(24)
    kropp = {"produkt": "Prosjektrom", "leverandor": "Nordvind AS",
             "eier_bruker_id": eier, "kilde": "AVT-2027-1",
             "fornyelsesdato": "2027-06-30", "oppsigelsesfrist_dogn": 60}

    r1 = _post(klient, cookie, csrf, "/v1/lisens", kropp, idem=nokkel)
    assert r1.status_code in (200, 201), r1.text
    assert r1.json()["ny"] is True
    r2 = _post(klient, cookie, csrf, "/v1/lisens", kropp, idem=nokkel)
    assert r2.status_code in (200, 201), r2.text
    assert r2.json()["lisens_id"] == r1.json()["lisens_id"]
    assert r2.json()["ny"] is False, "gjenspillet fødte en ny lisens"

    # …og en endret oppsigelsesfrist er MATERIELL: den flytter hele
    # varslingen.
    endret = dict(kropp, oppsigelsesfrist_dogn=90)
    r3 = _post(klient, cookie, csrf, "/v1/lisens", endret, idem=nokkel)
    assert r3.status_code == 409, r3.text
    assert r3.json()["feil"] == "idempotenskonflikt"


@pg
def test_http_kostnad_uten_valuta_er_400(migrator, klient):
    """Et beløp uten valuta er ikke et beløp — CHECK-en i 098 sier det,
    og API-et sier det FØRST, med 400: det er KROPPEN som er
    ufullstendig, ikke registeret som nekter."""
    eier = _bruker(migrator, TENANT)
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _post(klient, cookie, csrf, "/v1/lisens",
              {"produkt": "Uten valuta", "leverandor": "X",
               "eier_bruker_id": eier, "kilde": "k",
               "fornyelsesdato": "2027-06-30", "kostnad_aar": "1000.00"})
    assert r.status_code == 400, r.text
    assert r.json()["feil"] == "request_feilformet"


# ---------------------------------------------------------------------------
# Migrasjonens form: SP-10-premisset og rettighetsspeilet
# ---------------------------------------------------------------------------

@pg
def test_migrasjonen_er_kjort_og_bytebundet(migrator):
    """098 står i `migrasjoner` med checksum lik sha256 av filbytene i
    treet — den TOMME kjøringen målt direkte, og samme byte-binding
    fasiten pinner mot main."""
    cs = migrator.execute(
        "SELECT checksum FROM migrasjoner WHERE versjon=98").fetchone()
    migrator.rollback()
    assert cs is not None, "098 er ikke kjørt i testbasen"
    fil_sha = hashlib.sha256(MIGRASJON.read_bytes()).hexdigest()
    assert cs[0] == fil_sha, \
        "098 i treet er ikke bytene basen kjørte — historikk er immutable"
    fasit = json.loads(
        (ROT / "platform" / "core" / "db" / "migrasjons-fasit.json")
        .read_text(encoding="utf-8"))
    assert fasit.get("098_m22_lisensregister.sql") == fil_sha, \
        "fasiten pinner andre bytes enn treet bærer"


def test_migrasjonen_er_ren_ddl():
    """SP-10s premiss (047-klassen): masse-DML i en migrasjon kan køe
    utsatte triggerhendelser som ALTER-setninger nekter å passere. 098 har
    ingen slik seed — den er ren DDL — og DA er «grønn fra tom base» og
    «grønn mot seedet base» det samme utsagnet, målt av den tomme
    kjøringen over pluss CI-kjøringen mot en bebodd base."""
    import pglast
    dml = [type(raa.stmt).__name__
           for raa in pglast.parse_sql(
               MIGRASJON.read_text(encoding="utf-8"))
           if type(raa.stmt).__name__ in ("InsertStmt", "UpdateStmt",
                                          "DeleteStmt")]
    assert not dml, (
        f"098 bærer toppnivå-DML {dml} — da er den en backfill og skal"
        " registrere seed+måling i sp10-provekjoring.py")


@pg
def test_enumsplicen_er_gronn_mot_BEBODD_varseltabell(migrator):
    """SP-10s ANDRE halvdel, målt og ikke antatt.

    098 er ren DDL (porten over), og for de tre EGNE tabellene er «tom
    base» og «bebodd base» derfor det samme utsagnet — de finnes ikke før
    migrasjonen lager dem. Men §6 rører en tabell som ALT ER BEBODD:
    `varsel`, gjennom `ALTER TABLE ... DROP/ADD CONSTRAINT` på art- og
    ressurstype-CHECKen. Det er nøyaktig 047-klassen — en ALTER over rader
    som alt står der — og det er den ene setningen i 098 der «bebodd»
    kunne betydd noe annet enn «tom».

    Porten kjører derfor §6-blokken ORDRETT fra filen, en gang til, mot
    en `varsel`-tabell som har rader av BÅDE en gammel art og den nye.
    Blokken skal være grønn, og CHECK-ene skal bære BEGGE verdiene
    etterpå.

    MUTASJONEN SOM DREPER DENNE: gjør splicen ERSTATTENDE i stedet for
    additiv. Da er SQL-en fortsatt syntaktisk gyldig og en TOM base
    fortsatt grønn — men `ADD CONSTRAINT` valideres mot hver eksisterende
    rad, og den ene `attestering_venter`-raden feller hele migrasjonen.
    """
    ten = _tenantnavn("bebodd")
    eier = _bruker(migrator, ten)
    c, v = _rt(), _vs()
    try:
        _registrer(c, ten, eier, fornyelse="current_date - 40")
        _sveip_her(v, migrator, ten)
    finally:
        c.close()
        v.close()
    # …og en rad av en GAMMEL art ved siden av, så tabellen er bebodd med
    # mer enn M-22s egne rader.
    _ordinaert_varsel(migrator, ten, eier, "r-bebodd")
    assert len(_varsler(migrator, ten)) == 3, "tabellen er ikke bebodd"

    sql = MIGRASJON.read_text(encoding="utf-8")
    blokk = sql[sql.index("-- 6. Varselenumene"):]
    blokk = blokk[blokk.index("DO $$"):]
    migrator.execute(blokk)
    migrator.commit()

    definisjoner = dict(migrator.execute(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conrelid='varsel'::regclass AND conname IN"
        "       ('varsel_art_chk','varsel_ressurs_type_chk')").fetchall())
    migrator.rollback()
    assert "'lisensutlop'" in definisjoner["varsel_art_chk"]
    # …og de GAMLE artene står der fortsatt: splicen er ADDITIV, ikke en
    # omskriving. Et sett som mistet en verdi ville gjort hver
    # eksisterende rad ulovlig i det samme ALTER-et.
    assert "'attestering_venter'" in definisjoner["varsel_art_chk"]
    assert "'pliktfrist'" in definisjoner["varsel_art_chk"]
    assert "'lisens'" in definisjoner["varsel_ressurs_type_chk"]
    assert "'plikt'" in definisjoner["varsel_ressurs_type_chk"]


def test_varselenumets_fasit_er_utvidet_i_samme_commit():
    """PORTEN SOM FANGET M-21 PÅ FØRSTE FORSØK, arvet med vilje.

    En modul som legger til en `art` MÅ oppdatere BÅDE den deklarerte
    fasiten i `deploy/staging/varselenum-reparasjon.sql` OG `KANONISK` i
    `test_varselenum.py`, i samme commit som migrasjonen. Uten begge kan
    skriptet og kjeden drive fra hverandre igjen — bare langsommere.
    """
    from .test_varselenum import KANONISK
    assert "lisensutlop" in KANONISK["varsel_art_chk"]
    assert "lisens" in KANONISK["varsel_ressurs_type_chk"]
    rep = (ROT / "deploy" / "staging"
           / "varselenum-reparasjon.sql").read_text(encoding="utf-8")
    assert "'lisensutlop'" in rep and "'lisens'" in rep


def test_migrasjonen_navngir_aldri_runtime_rollen():
    """056/057/089/096-formen: `disponit` er bare LOKALNAVNET på web-API-
    rollen, og `migrer.py` er eneste rettighetskilde for den konfigurerte
    rollen. En GRANT til runtime i migrasjonen ville lagt
    rettighetsmodellen to steder — og det ene stedet ville vært usant på
    enhver installasjon som kaller rollen noe annet. REVOKE-en er lovlig
    og nødvendig (091-formen): en rettighet som bare slutter å bli gitt er
    ikke trukket tilbake."""
    for linje in MIGRASJON.read_text(encoding="utf-8").splitlines():
        if linje.lstrip().startswith("--"):
            continue
        assert "TO disponit;" not in linje, \
            f"098 grantar direkte til runtime-rollen: {linje!r}"


def test_kjoreren_speiler_098_rettighetene():
    """Rettighetsspeilet i `migrer.py` (057-portformen), og den
    SKARPESTE delen av det: registeret har INGEN tabellrettigheter for
    noen rolle utenom dørenes egen eier.

      * runtime får EXECUTE på lesedøren og de tre skrivedørene — og
        ALDRI på sveipen (kryss-tenant, 038-reaperens snitt);
      * senderrollen får EXECUTE på sveipen og ingenting annet;
      * ingen SELECT/INSERT/UPDATE/DELETE på `lisens`, `lisensvarsling`
        eller `lisensvarsel_sendt` noe sted i kjøreren.
    """
    kjorer = (ROT / "deploy" / "staging" / "migrer.py").read_text(
        encoding="utf-8")
    for dor in ("m22_lisenser(TEXT, INT)",
                "m22_registrer_lisens(TEXT, UUID, TEXT, TEXT, TEXT, INT,"
                " NUMERIC, TEXT, DATE, TEXT, INT, TEXT, INT[], TEXT)",
                "m22_registrer_fornyelse(TEXT, UUID, DATE, TEXT)",
                "m22_marker_avsluttet(TEXT, UUID, TEXT, TEXT)",
                "m22_koe_utlopsvarsler(INT)"):
        assert f"GRANT EXECUTE ON FUNCTION {dor} TO {{rolle}};" in kjorer, dor
    assert "REVOKE ALL ON FUNCTION m22_koe_utlopsvarsler(INT)" \
        " FROM {rolle};" in kjorer, "runtime får beholde kryss-tenant-sveipen"
    for tabell in ("lisens", "lisensvarsling", "lisensvarsel_sendt"):
        for verb in ("SELECT ON", "INSERT ON", "UPDATE ON", "DELETE ON"):
            assert f"{verb} {tabell}" not in kjorer, \
                f"en rolle har fått {verb} {tabell} utenom dørene"


@pg
def test_runtime_har_ingen_tabellrettighet_paa_registeret(migrator):
    """SP-7, målt mot BASEN og ikke mot kildeteksten. Hele registeret nås
    KUN gjennom dørene, og at det er slik skal være en egenskap ved basen
    — ikke ved koden som tilfeldigvis ikke gjør noe annet."""
    for tabell in ("lisens", "lisensvarsling", "lisensvarsel_sendt"):
        for verb in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            har = migrator.execute(
                "SELECT has_table_privilege('disponit', %s, %s)",
                (tabell, verb)).fetchone()[0]
            assert har is False, f"runtime har {verb} på {tabell}"
        # …og senderrollen enda mindre: den kan kjøre sveipen, ikke lese
        # en eneste lisens.
        assert migrator.execute(
            "SELECT has_table_privilege('disponit_varselsender', %s,"
            "                           'SELECT')", (tabell,)).fetchone()[0] \
            is False, f"senderrollen kan lese {tabell}"
    assert migrator.execute(
        "SELECT has_function_privilege('disponit_varselsender',"
        " 'm22_koe_utlopsvarsler(int)', 'EXECUTE')").fetchone()[0] is True
    migrator.rollback()


def test_grensen_dekker_manifestets_seks_invarianter():
    """Grensen `m22-v1` ble registrert FØR koden (§0-regelen). Porten
    pinner den mot planen, ikke mot listen selv: seks invarianter, null
    tillatte brudd, og `ddl_begge_kjoringer_gronne` som eneste
    ja-punkt."""
    from manifestskjema import KRAVGRENSER, M22_INVARIANTER
    g = KRAVGRENSER["m22-v1"]
    assert len(M22_INVARIANTER) == len(set(M22_INVARIANTER)) == 6
    assert g["invarianter"] is M22_INVARIANTER
    assert g["krav_ja"] == ("ddl_begge_kjoringer_gronne",)
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    # Punktbindingen er TOM MED VILJE — uflippbar til målingene finnes.
    assert g["punktbinding"] == {}


def test_rutene_og_flaten_er_registrert():
    """`Route()` og `RUTESCOPE` bindes toveis av `test_pr008`; her måles
    SCOPEVALGET, som er en dom og ikke en detalj: registrering, fornyelse
    og avslutning GJENBRUKER `bestilling:opprett` (et nytt scope skal
    ikke oppstå av vane), og lesingen bærer `decisions:read` — det scopet
    ALLE kunderollene har, og det eneste `LESESCOPES`-porten godtar for
    en `/v1/`-GET."""
    from api.app import BROWSER_MUTASJONSSCOPES, LESESCOPES, RUTESCOPE
    assert RUTESCOPE[("GET", "/v1/lisens")] == "decisions:read"
    assert "decisions:read" in LESESCOPES
    for sti in ("/v1/lisens", "/v1/lisens/{lisens_id:uuid}/fornyelse",
                "/v1/lisens/{lisens_id:uuid}/avslutt"):
        assert RUTESCOPE[("POST", sti)] == "bestilling:opprett"
    # …og skrivescopet er ALT tillatt for en browsersesjon: hadde det ikke
    # vært det, ville flaten møtt `scope_mangler` på hver knapp.
    assert "bestilling:opprett" in BROWSER_MUTASJONSSCOPES
    from api.autorisasjon import ROLLE_TIL_SCOPES
    assert "bestilling:opprett" in ROLLE_TIL_SCOPES["admin"]
    assert "decisions:read" in ROLLE_TIL_SCOPES["leser"]
    # SVEIPEN STÅR IKKE I RUTESCOPE, og det er en sikkerhetsdom: den er
    # kryss-tenant, og web-API-rollen skal ikke kunne kjøre den på
    # kommando.
    assert not any("utlopsvarsler" in sti for _m, sti in RUTESCOPE)
    sitekart = (ROT / "platform" / "core" / "ui" / "static" / "js"
                / "sitekart.js").read_text(encoding="utf-8")
    assert ('{ nokkel: "lisens", scope: "decisions:read",'
            ' modulflate: 22 }') in sitekart


def test_flatetittel_og_lenketekst_er_samme_streng():
    """Lærdom 7 fra klynge 1, målt for M-22 spesifikt: `ui.nav.lisens` og
    `ui.lisens.tittel` skal være SAMME streng i BEGGE språk. En meny som
    kaller flaten noe annet enn flaten kaller seg selv er to navn på det
    samme, og brukeren må gjette hvilket som gjelder."""
    for sett in ("nb", "en"):
        tekster = json.loads(
            (ROT / "locales" / f"{sett}.json").read_text(encoding="utf-8"))
        assert tekster["ui.nav.lisens"] == tekster["ui.lisens.tittel"], sett
    # …og varseltekstens nøkkel finnes i begge, ellers rendrer senderen
    # nøkkelen selv ut i e-posten.
    for sett in ("nb", "en"):
        tekster = json.loads(
            (ROT / "locales" / f"{sett}.json").read_text(encoding="utf-8"))
        assert "varsel.lisensutlop" in tekster, sett


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
        " ('https://m22.test', %s) RETURNING bruker_id",
        ("s22h-" + secrets.token_hex(6),)).fetchone()[0]
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
