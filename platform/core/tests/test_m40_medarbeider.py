"""M-40 HR- og medarbeideragent v1 (140) — KLYNGE 10s FJERDE OG SISTE.

Grensen `m40-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

KLYNGENS DELTE DOM, FOR SISTE GANG:

  EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN ANGRES IKKE AV EN
  ROLLBACK.

M-28 sa det om en bil på veien. Her er det tyngre: en oppsigelse som
ble rullet tilbake er fortsatt en samtale som fant sted.

DE TRE ANDRE MODULENE I KLYNGEN HOLDT TILBAKE EN FULLMAKT, OG PORTENE
DERES MÅLTE ET FRAVÆR. Denne holder også tilbake — men den må i tillegg
BYGGE noe som faktisk er sant, og det er pulsmålingen.

Derfor deler portene her seg i tre:

  1. FRAVÆRSPORTENE. Ingen beslutningsdør, ingen score-kolonne, ingen
     `taker_id` på et pulssvar. Formen fra 137/138/139.
  2. RETTIGHETSPORTENE. Terskelen kan ikke endres fordi ingen har
     UPDATE på kolonnen. Kontrakten kan ikke endres fordi ingen har
     UPDATE på tabellen. Det er ikke sjekker som kjører — det er
     handlinger som ikke kan forsøkes.
  3. AGGREGATPORTENE. Døra nekter å svare under terskelen, og den
     nekter mot MÅLINGENS terskel og ikke mot dagens krav.

OG ÉN PORT MÅLER AT M-40 IKKE BYGDE ET ANDRE ANSATTREGISTER: ingen av
modulens tabeller bærer et navn. `lonnstaker` (M-39) er husets eneste,
og M-40 spør den framfor å svare selv.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import contextlib
import os
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN  # noqa: F401

MEDARBEIDERSVEIP_DSN = os.environ.get("DISPONIT_TEST_MEDARBEIDERSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "140_m40_medarbeider.sql")

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

EGNE = ("medarbeiderkrav", "ansattlop", "ansattlopsteg",
        "medarbeiderkontrakt", "medarbeiderkontraktfelt",
        "pulsmaaling", "pulssvar", "medarbeiderfunn")
ROLLENE = ("disponit_medarbeider_eier", "disponit_medarbeidersveip")

#: KOLONNER SOM ALDRI SKAL FINNES PÅ ET PULSSVAR.
#:
#: Hver av dem ville vært en personnøkkel. En nullbar `taker_id` er et
#: løfte om at ingen fyller den; en hashet er et løfte om at ingen slår
#: den opp. En kolonne som ikke finnes er ingen av delene.
FORBUDTE_PAA_SVAR = ("taker_id", "ansatt_id", "person_id", "bruker",
                     "bruker_id", "epost", "navn", "ekstern_ref",
                     "pseudonym", "svarer", "avgitt_av", "ip",
                     "sesjon", "sesjon_id")

#: KOLONNER SOM ALDRI SKAL FINNES NOE STED I MODULEN.
#:
#: En produktivitetsscore er ikke forbudt fordi den er upresis. Den er
#: forbudt fordi et tall om et menneske, lagret av en maskin, blir lest
#: som en dom uansett hvor mange forbehold som står ved siden av.
FORBUDTE_OVERALT = ("score", "poengsum", "rangering", "produktivitet",
                    "ytelse", "prestasjon", "risiko", "sannsynlighet",
                    "oppsigelse", "vurdering", "karakter", "profil")


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


def _sv():
    from db.pg import koble
    return koble(MEDARBEIDERSVEIP_DSN or MIGRATOR_DSN)


def _sett_kontekst(conn, tenant):
    conn.execute("SELECT set_config('disponit.tenant', %s, false)",
                 (tenant,))


def _tenantnavn(merke: str) -> str:
    return f"t-m40-{merke}-{secrets.token_hex(4)}"


# =====================================================================
# BYGGEKLOSSER.
# =====================================================================

def _krav(rt, t, *, terskel=5, frist=14):
    _sett_kontekst(rt, t)
    v = rt.execute("SELECT m40_sett_krav(%s,%s,%s,%s)",
                   (t, terskel, frist, "u-test")).fetchone()[0]
    rt.commit()
    return v


def _ansatt(mg, t, *, aktiv=True, alder_dogn=0):
    """En lonnstaker.

    SKRIVES SOM MIGRATOR: `lonnstaker` er M-39s tabell, og M-40 har
    bare leserett der. Porten skal måle medarbeidermodulen — ikke
    lønnsmodulen.
    """
    _sett_kontekst(mg, t)
    tid = uuid.uuid4()
    mg.execute(
        "INSERT INTO lonnstaker (tenant, taker_id, ekstern_ref, navn,"
        " aktiv, opprettet, opprettet_av)"
        " VALUES (%s,%s,%s,'Kari Testesen',%s,"
        " now() - make_interval(days => %s),'u-test')",
        (t, tid, f"ans-{secrets.token_hex(3)}", aktiv, alder_dogn))
    mg.commit()
    return tid


def _mal(mg, t, *, status="publisert", felter=("stilling", "startdato")):
    """En malfamilie med én versjon og noen felter (M-5, 094).

    VERSJONEN FØDES SOM `utkast` OG FLYTTES ETTERPÅ, fordi M-5s egen
    `m5_innhold_vakt` nekter å skrive et felt på noe annet enn et
    utkast: «en publisert mal får aldri nytt innhold, den etterfølges».

    Det er M-5 som har rett, og porten skal bygge tilstanden slik den
    faktisk oppstår — ikke slik det var raskest å skrive.
    """
    _sett_kontekst(mg, t)
    fid, vid = uuid.uuid4(), uuid.uuid4()
    mg.execute(
        "INSERT INTO malfamilie (tenant, familie_id, navn, opprettet_av,"
        " innhold_hash) VALUES (%s,%s,'Ansettelseskontrakt','u-test',"
        " %s)", (t, fid, secrets.token_hex(16)))
    mg.execute(
        "INSERT INTO malversjon (tenant, versjon_id, familie_id,"
        " versjonsnr, status, opprettet_av, innhold_hash)"
        " VALUES (%s,%s,%s,1,'utkast','u-test',%s)",
        (t, vid, fid, secrets.token_hex(16)))
    for n in felter:
        mg.execute(
            "INSERT INTO malfelt (tenant, versjon_id, feltnokkel,"
            " paakrevd, felttype, beskrivelse)"
            " VALUES (%s,%s,%s,true,'tekst','porten')", (t, vid, n))
    if status == "publisert":
        mg.execute(
            "UPDATE malversjon SET status='publisert',"
            " publisert_ts=now(), publisert_av='u-test'"
            " WHERE tenant=%s AND versjon_id=%s", (t, vid))
    elif status == "tilbaketrukket":
        # `utkast → tilbaketrukket` ER IKKE EN LOVLIG OVERGANG i M-5.
        # De to er `utkast→publisert` og `publisert→tilbaketrukket`,
        # og `m5_versjon_vakt` sier fra. Porten går veien malen
        # faktisk går.
        mg.execute(
            "UPDATE malversjon SET status='publisert',"
            " publisert_ts=now(), publisert_av='u-test'"
            " WHERE tenant=%s AND versjon_id=%s", (t, vid))
        mg.execute(
            "UPDATE malversjon SET status='tilbaketrukket',"
            " tilbaketrukket_ts=now(), tilbaketrukket_av='u-test'"
            " WHERE tenant=%s AND versjon_id=%s", (t, vid))
    mg.commit()
    return vid


def _lop(rt, t, taker, kv):
    _sett_kontekst(rt, t)
    lid = uuid.uuid4()
    rt.execute("SELECT m40_start_lop(%s,%s,%s,%s,%s)",
               (t, lid, taker, kv, "u-test"))
    rt.commit()
    return lid


def _maaling(rt, t, *, terskel=5):
    _sett_kontekst(rt, t)
    mid = uuid.uuid4()
    rt.execute("SELECT m40_apne_maaling(%s,%s,'Trivsel Q1',%s,%s)",
               (t, mid, terskel, "u-test"))
    rt.commit()
    return mid


def _svar(rt, t, mid, gruppe, antall, verdi=4):
    _sett_kontekst(rt, t)
    for _ in range(antall):
        rt.execute("SELECT m40_avgi_puls(%s,%s,%s,%s,%s)",
                   (t, uuid.uuid4(), mid, gruppe, verdi))
    rt.commit()


def test_grensen_ble_registrert_for_koden():
    """§0-REGELEN: `m40-v1` sto i KRAVGRENSER før 140 fantes.

    OG DEN ER KLYNGENS SISTE. Med denne er UBYGDE_GRENSER tom for
    klynge 10, og katalogen er 57 av 57.
    """
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m40-v1"]
    assert g["maks_brudd"] == 0
    assert "ddl_begge_kjoringer_gronne" in g["krav_ja"]
    for navn in ("beslutning_med_rettsvirkning", "individprofil_bygget",
                 "puls_identifiserte_en_person",
                 "aggregat_under_minste_gruppe",
                 "gruppeterskel_endret_etter_maaling",
                 "kontrakt_uten_malversjon", "juridisk_klausul_endret",
                 "modulen_bygget_eget_ansattregister",
                 "tenantlekkasje_i_medarbeiderregister"):
        assert navn in g["invarianter"], navn


# =====================================================================
# 1. FRAVÆRSPORTENE — DET SOM IKKE FINNES.
# =====================================================================

@pg
def test_pulssvar_har_ingen_personnokkel():
    """`puls_identifiserte_en_person` GJORT UREPRESENTERBAR.

    Dette er klyngens viktigste port, og den måler et fravær av
    kolonner framfor en oppførsel.

    En modul som lovet å ikke fylle en `taker_id` ville hatt et løfte å
    bryte. Denne har ingen kolonne å fylle — og dermed heller ingen
    feilmelding som kan lekke den, ingen feilsøking som kan finne den
    og ingen framtidig utvikler som «bare midlertidig» kan sette den.
    """
    with _to() as (_rt, mg):
        kolonner = {r[0] for r in mg.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema='public' AND table_name='pulssvar'")}
    assert kolonner == {"tenant", "svar_id", "maaling_id", "gruppe",
                        "verdi", "avgitt_ts"}, (
        f"pulssvar har fatt en kolonne til: {kolonner}")
    for f in FORBUDTE_PAA_SVAR:
        assert f not in kolonner, (
            f"`{f}` pa pulssvar er en personnokkel, og da er malingen"
            " ikke anonym uansett hva dommen sier")


@pg
def test_ingen_tabell_i_modulen_baerer_et_tall_om_et_menneske():
    """`individprofil_bygget` GJORT UREPRESENTERBAR.

    Porten går gjennom ALLE åtte tabellene, ikke bare de opplagte. En
    score gjemt i onboardingløpet ville vært like mye en profil som en
    score i en tabell som het det.
    """
    with _to() as (_rt, mg):
        funn = mg.execute(
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_schema='public' AND table_name = ANY(%s)",
            (list(EGNE),)).fetchall()
    for tabell, kolonne in funn:
        for forbudt in FORBUDTE_OVERALT:
            assert forbudt not in kolonne.lower(), (
                f"{tabell}.{kolonne} ser ut som et tall om et menneske")


@pg
def test_modulen_har_ingen_beslutningsdor():
    """`beslutning_med_rettsvirkning` GJORT UREPRESENTERBAR.

    Ikke ved at en dør nekter, men ved at ingen dør finnes. Porten
    leser modulens FAKTISKE funksjonsnavn fra katalogen — ikke fra
    migrasjonsfila, som kunne inneholdt en kommentert-ut dør.
    """
    with _to() as (_rt, mg):
        navn = {r[0] for r in mg.execute(
            "SELECT p.proname FROM pg_proc p"
            " WHERE p.pronamespace = 'public'::regnamespace"
            "   AND p.proname LIKE 'm40\\_%'")}
    forbudte = ("ansett", "si_opp", "oppsig", "avskjed", "lonn",
                "fastsett", "innvilg", "avslag", "beslutt", "vurder",
                "rangér", "ranger", "score", "profil")
    for n in navn:
        for f in forbudte:
            assert f not in n, (
                f"{n} ser ut som en dor som avgjor noe om et menneske")
    # OG DEN SKAL FAKTISK HA DØRER — en tom katalog ville bestått over.
    assert len(navn) >= 15, f"bare {len(navn)} m40-funksjoner"


@pg
def test_modulen_bygget_ikke_et_eget_ansattregister():
    """`modulen_bygget_eget_ansattregister` MÅLT I KOLONNENE.

    To registre over de samme menneskene gir to svar på «jobber hun
    her», og det er ett for mange.

    Beviset er at ingen av M-40s tabeller bærer et navn eller en
    ekstern referanse: uten dem er `ansattlop` en peker inn i M-39s
    register, ikke et register i seg selv.
    """
    with _to() as (_rt, mg):
        funn = mg.execute(
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_schema='public' AND table_name = ANY(%s)",
            (list(EGNE),)).fetchall()
    for tabell, kolonne in funn:
        assert kolonne not in ("navn", "ekstern_ref", "fodselsnummer",
                               "epost", "telefon"), (
            f"{tabell}.{kolonne} gjor M-40 til et andre ansattregister")


@pg
def test_leseretten_paa_lonnstaker_er_en_kolonnegrant_uten_navn():
    """ARVEN ER EN KOLONNEGRANT, OG `navn` ER IKKE MED.

    093s form. Modulen trenger å vite AT hun er ansatt, ikke hva hun
    heter — navnet hører hjemme der noen skal skrive det på en slipp.
    """
    with _to() as (_rt, mg):
        kolonner = {r[0] for r in mg.execute(
            "SELECT column_name FROM information_schema.column_privileges"
            " WHERE table_name='lonnstaker'"
            "   AND grantee='disponit_medarbeider_eier'"
            "   AND privilege_type='SELECT'")}
    assert kolonner == {"tenant", "taker_id", "ekstern_ref", "aktiv",
                        "opprettet"}, (
        f"granten pa lonnstaker er ikke den avtalte: {kolonner}")
    # DE TO SOM BETYR NOE: navnet er ikke med, og `opprettet` ER det —
    # sveipens modningstid finnes ikke uten den.
    assert "navn" not in kolonner
    assert "opprettet" in kolonner


# =====================================================================
# 2. RETTIGHETSPORTENE — EN HANDLING SOM IKKE KAN FORSØKES.
# =====================================================================

@pg
def test_ingen_har_update_paa_gruppeterskelen():
    """`gruppeterskel_endret_etter_maaling` GJORT UMULIG Å FORSØKE.

    DETTE ER DEN PORTEN SOM SKILLER EN TERSKEL FRA EN INNSTILLING.

    En terskel som kan endres i ettertid verner ingenting: den som vil
    lese en for liten gruppe, senker den først. Vernet er derfor ikke
    en CHECK som nekter endringen, men en RETT SOM IKKE FINNES.

    Porten måler begge halvdeler av kolonnegranten — at `gruppeterskel`
    mangler, OG at lukkekolonnene er der. Uten den andre halvdelen
    ville en migrasjon som glemte hele granten bestått.
    """
    with _to() as (_rt, mg):
        oppdaterbare = {r[0] for r in mg.execute(
            "SELECT column_name FROM information_schema.column_privileges"
            " WHERE table_name='pulsmaaling'"
            "   AND grantee='disponit_medarbeider_eier'"
            "   AND privilege_type='UPDATE'")}
    assert "gruppeterskel" not in oppdaterbare, (
        "terskelen kan endres etter at svarene er samlet inn, og da er"
        " den ikke en terskel")
    assert oppdaterbare == {"lukket_ts", "lukket_av"}, (
        f"kolonnegranten er ikke den avtalte: {oppdaterbare}")


@pg
def test_doren_selv_kan_ikke_skrive_terskelen():
    """OG DET GJELDER OGSÅ MODULENS EGEN ROLLE, MÅLT I EN TRANSAKSJON.

    Porten over leser katalogen. Denne prøver faktisk å skrive, som den
    rollen dørene kjører under — for en grant kan se riktig ut i
    `information_schema` og likevel være overstyrt av en annen.
    """
    t = _tenantnavn("terskel")
    with _to() as (rt, mg):
        _krav(rt, t)
        mid = _maaling(rt, t, terskel=7)
        mg.execute("SET LOCAL ROLE disponit_medarbeider_eier")
        _sett_kontekst(mg, t)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            mg.execute("UPDATE pulsmaaling SET gruppeterskel = 2"
                       " WHERE tenant=%s AND maaling_id=%s", (t, mid))
        mg.rollback()
        # LESES SOM MIGRATOR, IKKE SOM KJØRETIDEN: SP-7 gir `disponit`
        # EXECUTE på dørene og ingen tabellrett i det hele tatt. En
        # port som leste raden direkte som kjøretiden ville målt
        # SP-7 og ikke terskelen.
        _sett_kontekst(mg, t)
        assert mg.execute(
            "SELECT gruppeterskel FROM pulsmaaling WHERE tenant=%s"
            " AND maaling_id=%s", (t, mid)).fetchone()[0] == 7


@pg
def test_kontrakten_kan_ikke_endres_etter_utstedelse():
    """`juridisk_klausul_endret` GJORT UMULIG.

    En signert klausul endres ikke. `REVOKE UPDATE` på tabellen gjør
    det til noe ingen kan forsøke — også den som skriver neste dør.
    """
    with _to() as (_rt, mg):
        rettigheter = {r[0] for r in mg.execute(
            "SELECT privilege_type FROM information_schema.table_privileges"
            " WHERE table_name='medarbeiderkontrakt'"
            "   AND grantee='disponit_medarbeider_eier'")}
    assert "UPDATE" not in rettigheter, (
        "en kontrakt som kan skrives om er ingen kontrakt")
    assert "INSERT" in rettigheter and "SELECT" in rettigheter


@pg
def test_kravet_er_append_only():
    """135/137/138/139s form, arvet.

    En grense som kunne endres etter at en måling pekte på den, ville
    gjort «gulvet som gjaldt» til «gulvet som gjelder nå».
    """
    with _to() as (_rt, mg):
        rettigheter = {r[0] for r in mg.execute(
            "SELECT privilege_type FROM information_schema.table_privileges"
            " WHERE table_name='medarbeiderkrav'"
            "   AND grantee='disponit_medarbeider_eier'")}
    assert "UPDATE" not in rettigheter


@pg
def test_pulssvaret_kan_verken_endres_eller_slettes_av_modulen():
    """EN AVGITT PULS ER EN MÅLING, IKKE ET UTKAST.

    Og siden raden ikke bærer persondata, er det heller ingen
    sletteplikt å oppfylle — bare en måling å bevare.
    """
    with _to() as (_rt, mg):
        rettigheter = {r[0] for r in mg.execute(
            "SELECT privilege_type FROM information_schema.table_privileges"
            " WHERE table_name='pulssvar'"
            "   AND grantee='disponit_medarbeider_eier'")}
    assert "UPDATE" not in rettigheter and "DELETE" not in rettigheter


# =====================================================================
# 3. AGGREGATPORTENE — DØRA SOM NEKTER Å SVARE.
# =====================================================================

@pg
def test_aggregatet_nekter_under_terskelen():
    """`aggregat_under_minste_gruppe` MÅLT MOT EN EKTE GRUPPE.

    Fire svar fra en gruppe på fire er anonyme hver for seg og fullt
    identifiserende til sammen. Døra svarer ikke før gruppen er stor
    nok — og «ikke svarer» betyr her at raden ikke finnes, ikke at
    tallet er maskert. Et maskert tall er fortsatt et tall.
    """
    t = _tenantnavn("terskelsvar")
    with _to() as (rt, _mg):
        _krav(rt, t, terskel=5)
        mid = _maaling(rt, t, terskel=5)
        _svar(rt, t, mid, "utvikling", 4)
        _sett_kontekst(rt, t)
        rader = rt.execute("SELECT * FROM m40_pulsbildet(%s,%s)",
                           (t, mid)).fetchall()
        assert rader == [], "fire svar i en gruppe pa fem ble lest ut"
        _svar(rt, t, mid, "utvikling", 1)
        _sett_kontekst(rt, t)
        rader = rt.execute(
            "SELECT gruppe, antall FROM m40_pulsbildet(%s,%s)",
            (t, mid)).fetchall()
        assert rader == [("utvikling", 5)]


@pg
def test_den_store_gruppen_skjuler_ikke_den_lille():
    """OG TERSKELEN GJELDER PER GRUPPE, IKKE PER MÅLING.

    En måling med én stor og én liten gruppe skal svare for den store
    og TIE om den lille. Et aggregat som slapp gjennom hele målingen
    fordi totalen var stor nok, ville lekket den lille gruppen i
    samme svar.
    """
    t = _tenantnavn("togrupper")
    with _to() as (rt, _mg):
        _krav(rt, t, terskel=5)
        mid = _maaling(rt, t, terskel=5)
        _svar(rt, t, mid, "utvikling", 8)
        _svar(rt, t, mid, "ledelse", 2)
        _sett_kontekst(rt, t)
        grupper = {r[0] for r in rt.execute(
            "SELECT gruppe FROM m40_pulsbildet(%s,%s)", (t, mid))}
    assert grupper == {"utvikling"}, (
        f"den lille gruppen ble lest ut sammen med den store: {grupper}")


@pg
def test_terskelen_som_gjelder_er_malingens_ikke_dagens_krav():
    """DEN VIKTIGSTE HALVDELEN AV «LAGRET SAMMEN MED MÅLINGEN».

    Porten hever tenantens gulv ETTER at målingen er åpnet, og krever
    at målingen fortsatt leses under sin egen terskel.

    Uten dette ville terskelen vært en oppslagsverdi og ikke en
    egenskap ved målingen — og da kunne en tenant gjort gårsdagens
    lesbare måling ulesbar, eller verre: gjort en ulesbar måling
    lesbar ved å senke gulvet.
    """
    t = _tenantnavn("gjeldende")
    with _to() as (rt, _mg):
        _krav(rt, t, terskel=5)
        mid = _maaling(rt, t, terskel=5)
        _svar(rt, t, mid, "drift", 6)
        # Gulvet heves i ettertid. Malingen skal ikke merke det.
        _krav(rt, t, terskel=50)
        _sett_kontekst(rt, t)
        rader = rt.execute(
            "SELECT gruppe, antall FROM m40_pulsbildet(%s,%s)",
            (t, mid)).fetchall()
    assert rader == [("drift", 6)], (
        "malingen ble lest mot dagens krav og ikke mot sin egen terskel")


@pg
def test_doren_nekter_en_terskel_under_tenantens_gulv():
    """EN TENANT KAN VERNE BEDRE ENN HUSET KREVER, ALDRI DÅRLIGERE."""
    t = _tenantnavn("gulv")
    with _to() as (rt, _mg):
        _krav(rt, t, terskel=10)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute("SELECT m40_apne_maaling(%s,%s,'For smatt',%s,%s)",
                       (t, uuid.uuid4(), 3, "u-test"))
        rt.rollback()
        _sett_kontekst(rt, t)
        rt.execute("SELECT m40_apne_maaling(%s,%s,'Stort nok',%s,%s)",
                   (t, uuid.uuid4(), 25, "u-test"))
        rt.commit()


@pg
def test_oversikten_teller_lesbare_grupper_og_ikke_svar():
    """ET TOTALTALL VILLE OMGÅTT TERSKELEN VIA OVERSIKTEN.

    En måling med én gruppe på tre: hvis listen viste «3 svar», ville
    den fortalt nøyaktig det aggregatet nekter å fortelle. Derfor
    teller `m40_maalingene` LESBARE GRUPPER, som her er null.
    """
    t = _tenantnavn("oversikt")
    with _to() as (rt, _mg):
        _krav(rt, t, terskel=5)
        mid = _maaling(rt, t, terskel=5)
        _svar(rt, t, mid, "smaa", 3)
        _sett_kontekst(rt, t)
        rad = rt.execute(
            "SELECT lesbare_grupper FROM m40_maalingene(%s,%s)",
            (t, 50)).fetchone()
    assert rad[0] == 0, "oversikten rapporterte en gruppe ingen far lese"


# =====================================================================
# 4. KONTRAKTEN — SPORET TIL MALVERSJON OG KILDEFELT.
# =====================================================================

@pg
def test_kontrakt_uten_malversjon_kan_ikke_skrives():
    """`kontrakt_uten_malversjon` GJORT UREPRESENTERBAR."""
    with _to() as (_rt, mg):
        nullbar = mg.execute(
            "SELECT is_nullable FROM information_schema.columns"
            " WHERE table_schema='public'"
            "   AND table_name='medarbeiderkontrakt'"
            "   AND column_name='malversjon_id'").fetchone()[0]
    assert nullbar == "NO"


@pg
def test_kontrakten_krever_en_publisert_mal():
    """ET UTKAST ER INGEN HJEMMEL, OG EN TRUKKET MAL ER EN FJERNET EN.

    Begge nektes av samme oppslag, og porten måler begge — en dør som
    bare nektet utkast ville sluppet gjennom det verre tilfellet.
    """
    t = _tenantnavn("mal")
    with _to() as (rt, mg):
        kv = _krav(rt, t)
        taker = _ansatt(mg, t)
        for status in ("utkast", "tilbaketrukket"):
            vid = _mal(mg, t, status=status)
            _sett_kontekst(rt, t)
            with pytest.raises(psycopg.errors.RaiseException):
                rt.execute(
                    "SELECT m40_utsted_kontrakt(%s,%s,%s,%s,%s,%s)",
                    (t, uuid.uuid4(), taker, vid, ["stilling"], "u-test"))
            rt.rollback()
        assert kv == 1


@pg
def test_kontrakten_fester_malens_hash_og_versjonsnummer():
    """«KONTRAKTER KAN ALLTID SPORES TIL MALVERSJON OG KILDEFELT».

    Hashen KOPIERES ved utstedelse. Sammen med `REVOKE UPDATE` er det
    forskjellen på en referanse og et bevis: en referanse peker på det
    malen er NÅ, en kopiert hash på det den VAR.
    """
    t = _tenantnavn("hash")
    with _to() as (rt, mg):
        kv = _krav(rt, t)
        taker = _ansatt(mg, t)
        vid = _mal(mg, t)
        _sett_kontekst(mg, t)
        forventet = mg.execute(
            "SELECT innhold_hash FROM malversjon WHERE tenant=%s"
            " AND versjon_id=%s", (t, vid)).fetchone()[0]
        kid = uuid.uuid4()
        _sett_kontekst(rt, t)
        rt.execute("SELECT m40_utsted_kontrakt(%s,%s,%s,%s,%s,%s)",
                   (t, kid, taker, vid, ["stilling", "startdato"],
                    "u-test"))
        rt.commit()
        _sett_kontekst(mg, t)
        h, nr = mg.execute(
            "SELECT malversjon_hash, malversjonsnr FROM"
            " medarbeiderkontrakt WHERE tenant=%s AND kontrakt_id=%s",
            (t, kid)).fetchone()
        assert h == forventet and nr == 1
        felt = {r[0] for r in mg.execute(
            "SELECT feltnokkel FROM medarbeiderkontraktfelt"
            " WHERE tenant=%s AND kontrakt_id=%s", (t, kid))}
        assert felt == {"stilling", "startdato"}
        assert kv == 1


@pg
def test_kontrakten_nekter_et_felt_malen_ikke_har():
    """EN KONTRAKT SOM VISER TIL ET UKJENT FELT ER IKKE SPORBAR."""
    t = _tenantnavn("ukjentfelt")
    with _to() as (rt, mg):
        _krav(rt, t)
        taker = _ansatt(mg, t)
        vid = _mal(mg, t, felter=("stilling",))
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute("SELECT m40_utsted_kontrakt(%s,%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), taker, vid,
                        ["stilling", "finnes_ikke"], "u-test"))
        rt.rollback()


@pg
def test_kontraktfeltene_baerer_ingen_verdi():
    """MODULEN SVARER PÅ HVILKE FELTER, ALDRI PÅ HVA SOM STO I DEM.

    En kontraktverdi er persondata, og v1 har ingen grunn til å eie
    den. Porten måler at det ikke finnes noe sted å legge den.
    """
    with _to() as (_rt, mg):
        kolonner = {r[0] for r in mg.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema='public'"
            "   AND table_name='medarbeiderkontraktfelt'")}
    assert kolonner == {"tenant", "kontrakt_id", "feltnokkel",
                        "opprettet"}, (
        f"kontraktfeltet har fatt et innhold: {kolonner}")


# =====================================================================
# 5. ANSATTOPPSLAGET — M-39s REGISTER, IKKE M-40s.
# =====================================================================

@pg
def test_loep_krever_en_aktiv_lonnstaker():
    """«JOBBER HUN HER» BESVARES ÉTT STED I HUSET."""
    t = _tenantnavn("ukjent")
    with _to() as (rt, mg):
        kv = _krav(rt, t)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute("SELECT m40_start_lop(%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), uuid.uuid4(), kv, "u-test"))
        rt.rollback()
        sluttet = _ansatt(mg, t, aktiv=False)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute("SELECT m40_start_lop(%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), sluttet, kv, "u-test"))
        rt.rollback()


@pg
def test_ett_apent_loep_per_ansatt():
    """TO PARALLELLE FØRSTEUKER ER IKKE EN TILSTAND SOM BETYR NOE."""
    t = _tenantnavn("dobbelt")
    with _to() as (rt, mg):
        kv = _krav(rt, t)
        taker = _ansatt(mg, t)
        _lop(rt, t, taker, kv)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.UniqueViolation):
            rt.execute("SELECT m40_start_lop(%s,%s,%s,%s,%s)",
                       (t, uuid.uuid4(), taker, kv, "u-test"))
        rt.rollback()


@pg
def test_den_ekte_vakten_er_indeksen_ikke_dora():
    """M-28s LÆRDOM, ARVET UTEN AT DEN MÅTTE GJENTAS SOM FEIL.

    Porten over kaller døra og ser en `UniqueViolation`. Men den ville
    bestått også om vakten var en `IF EXISTS` i døra — og da ville to
    samtidige kallere sluppet gjennom begge to.

    Denne porten måler INDEKSEN, ikke døra. En mutasjonstest på M-28
    viste at det er forskjell.
    """
    with _to() as (_rt, mg):
        rad = mg.execute(
            "SELECT indexdef FROM pg_indexes"
            " WHERE tablename='ansattlop'"
            "   AND indexname='ansattlop_ett_apent_per_taker'").fetchone()
    assert rad is not None, "den partielle unike indeksen er borte"
    d = rad[0].lower()
    assert "unique" in d and "where" in d and "'apent'" in d


# =====================================================================
# 6. SVEIPEN OG FUNNENE.
# =====================================================================

@pg
def test_funn_er_sveipens_matcher_noyaktig_det_sveipen_reiser():
    """M-28s LÆRDOM, ARVET SOM EN PORT OG IKKE SOM EN HUSKEREGEL.

    En klassifisering som ikke matcher sveipen gjør døra til en
    høflighetssjekk: den nekter et menneske å lukke noe ingen reiser,
    og slipper henne til på noe som kommer tilbake neste natt.

    PORTEN LESER BEGGE SIDER FRA KILDEN. Funntypene sveipen faktisk
    skriver, hentet ut av migrasjonens egen tekst, mot settet
    funksjonen svarer ja på — ingen av dem er en liste porten fører.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    start = sql.index("CREATE FUNCTION m40_sveip_medarbeider")
    slutt = sql.index("RETTIGHETENE. SP-7", start)
    kropp = sql[start:slutt]
    reist = set()
    for bit in kropp.split("SELECT v_t, '")[1:]:
        reist.add(bit.split("'", 1)[0])
    assert len(reist) == 4, f"fant {reist} i sveipen, ventet fire"
    with _to() as (_rt, mg):
        # FUNKSJONEN ER `REVOKE ALL ... FROM PUBLIC` (SP-7). Den leses
        # som modulrollen, som er den eneste som har den.
        mg.execute("SET LOCAL ROLE disponit_medarbeider_eier")
        sveipens = {t for t in reist
                    if mg.execute("SELECT m40_funn_er_sveipens(%s)",
                                  (t,)).fetchone()[0]}
        # OG INGEN ANDRE: de seks umulige og `krav_mangler` skal IKKE
        # vaere sveipens — de reises aldri, og en dor som slapp et
        # menneske til pa dem ville latt henne lukke ingenting.
        aldri = ("beslutning_med_rettsvirkning", "individprofil_bygget",
                 "puls_identifiserte_en_person", "gruppeterskel_endret",
                 "kontrakt_uten_malversjon", "krav_mangler")
        for a in aldri:
            assert not mg.execute("SELECT m40_funn_er_sveipens(%s)",
                                  (a,)).fetchone()[0], (
                f"{a} reises aldri og kan ikke vaere sveipens")
    assert sveipens == reist, (
        f"sveipen reiser {reist - sveipens} uten a eie dem")


@pg
def test_doren_nekter_et_menneske_a_lukke_sveipens_funn():
    """Å LUKKE EN MÅLING ER IKKE Å LUKKE EN SAK (132s form)."""
    t = _tenantnavn("lukk")
    with _to() as (rt, mg):
        _krav(rt, t)
        _sett_kontekst(mg, t)
        fid = uuid.uuid4()
        mg.execute(
            "INSERT INTO medarbeiderfunn (tenant, funn_id, funntype,"
            " referanse, detalj) VALUES (%s,%s,'apent_lop_over_frist',"
            " %s,'porten')", (t, fid, str(uuid.uuid4())))
        mg.commit()
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute("SELECT m40_lukk_funn(%s,%s,%s,%s)",
                       (t, fid, "jeg sa fra", "u-test"))
        rt.rollback()


@pg
def test_lesedora_for_funn_gir_typen_referansen_og_hvem_som_eier_den():
    """PORTEN SOM MANGLET, OG SOM ER GRUNNEN TIL AT TO FEIL SLAPP GJENNOM.

    `m40_medarbeiderfunn` hadde `referanse UUID` i sin `RETURNS TABLE`
    mens kolonnen er TEXT — døra ville feilet ved første kall. Og
    API-ets radbygger leste `sveipens` på indeks 4 og `forst_sett` på 5,
    arvet fra en tidligere form av tabellen uten `sist_sett`.

    INGEN AV DELENE BLE FANGET, FORDI INGEN PORT KALTE DØRA. De 33
    andre portene måler fravær av kolonner og fravær av rettigheter —
    og et fravær kan måles uten å åpne døra. CodeRabbit fant begge 6/9.

    LÆRDOMMEN ER IKKE «SKRIV FLERE PORTER». Den er at en dør uten port
    er usett uansett hvor mange porter modulen ellers har: dekning
    måles per dør, ikke per modul.
    """
    t = _tenantnavn("lesefunn")
    with _to() as (rt, mg):
        _krav(rt, t)
        _sett_kontekst(mg, t)
        ref = str(uuid.uuid4())
        mg.execute(
            "INSERT INTO medarbeiderfunn (tenant, funntype, referanse,"
            " detalj) VALUES (%s,'apent_lop_over_frist',%s,'porten')",
            (t, ref))
        # OG ETT SOM ET MENNESKE EIER, så porten måler BEGGE utfallene
        # av `sveipens` og ikke bare det ene.
        mg.execute(
            "INSERT INTO medarbeiderfunn (tenant, funntype, referanse,"
            " detalj) VALUES (%s,'krav_mangler',%s,'porten')",
            (t, str(uuid.uuid4())))
        mg.commit()
        _sett_kontekst(rt, t)
        rader = rt.execute(
            "SELECT funn_id, funntype, referanse, detalj, forst_sett,"
            " sist_sett, sveipens FROM m40_medarbeiderfunn(%s,%s)",
            (t, 50)).fetchall()
    etter_type = {r[1]: r for r in rader}
    assert set(etter_type) == {"apent_lop_over_frist", "krav_mangler"}
    sveipens = etter_type["apent_lop_over_frist"]
    # REFERANSEN KOMMER TILBAKE SOM DEN GIKK INN — en TEXT-kolonne
    # deklarert som UUID ville feilet her, ikke stille gitt noe annet.
    assert sveipens[2] == ref
    assert isinstance(sveipens[2], str)
    assert sveipens[6] is True
    # OG DEN SISTE KOLONNEN ER `sveipens`, IKKE `sist_sett`. Rekkefølgen
    # er dørens kontrakt, og API-ets radbygger leser den etter indeks.
    assert etter_type["krav_mangler"][6] is False
    assert sveipens[4] is not None and sveipens[5] is not None


@pg
def test_api_ets_funnrad_leser_dorens_kolonner_i_riktig_rekkefolge():
    """OG RADBYGGEREN MÅLES MOT DØRA, IKKE MOT EN HUSKELISTE.

    Porten over sikrer at DØRA gir sju felt i rett rekkefølge. Denne
    sikrer at API-et LESER dem slik — uten den ville en riktig dør og
    en gal radbygger fortsatt gitt en tidsstempel som «sveipens».
    """
    import sys
    sys.path.insert(0, str(ROT / "platform" / "core"))
    from api.medarbeider import _funnrad
    import datetime as dt
    fid = uuid.uuid4()
    naa = dt.datetime(2026, 9, 6, 4, 0, tzinfo=dt.timezone.utc)
    rad = _funnrad((fid, "apent_lop_over_frist", "ref-1", "detalj",
                    naa, naa, True))
    assert rad["funn_id"] == str(fid)
    assert rad["referanse"] == "ref-1"
    assert rad["sveipens"] is True
    assert rad["forst_sett"] == naa.isoformat()


@pg
def test_sveipen_reiser_og_lukker_apent_loep_over_frist():
    t = _tenantnavn("sveip1")
    with _to() as (rt, mg):
        kv = _krav(rt, t, frist=1)
        taker = _ansatt(mg, t)
        lid = _lop(rt, t, taker, kv)
        _sett_kontekst(mg, t)
        mg.execute("UPDATE ansattlop SET startet = now()"
                   " - make_interval(days => 5) WHERE tenant=%s"
                   " AND lop_id=%s", (t, lid))
        mg.commit()
        sv = _sv()
        try:
            sv.execute("SELECT m40_sveip_medarbeider(1000)")
            sv.commit()
        finally:
            sv.close()
        _sett_kontekst(mg, t)
        typer = {r[0] for r in mg.execute(
            "SELECT funntype FROM medarbeiderfunn WHERE tenant=%s"
            " AND apen", (t,))}
        assert "apent_lop_over_frist" in typer
        # OG DEN LUKKES NAR TILSTANDEN ER BORTE.
        _sett_kontekst(rt, t)
        rt.execute("SELECT m40_avslutt_lop(%s,%s,'fullfort',%s)",
                   (t, lid, "u-test"))
        rt.commit()
        sv = _sv()
        try:
            sv.execute("SELECT m40_sveip_medarbeider(1000)")
            sv.commit()
        finally:
            sv.close()
        _sett_kontekst(mg, t)
        apne = {r[0] for r in mg.execute(
            "SELECT funntype FROM medarbeiderfunn WHERE tenant=%s"
            " AND apen", (t,))}
    assert "apent_lop_over_frist" not in apne


@pg
def test_sveipen_reiser_maaling_uten_lesbar_gruppe():
    """KLYNGENS SKARPESTE FUNN: SVAR INGEN NOEN GANG FÅR SE.

    Det er ikke en teknisk feil — aggregatet gjør nøyaktig det det
    skal. Det er en tenant som har spurt uten å kunne lytte, og hun
    skal få vite det.
    """
    t = _tenantnavn("ulesbar")
    with _to() as (rt, mg):
        _krav(rt, t, terskel=5)
        mid = _maaling(rt, t, terskel=5)
        _svar(rt, t, mid, "ledelse", 2)
        _sett_kontekst(rt, t)
        rt.execute("SELECT m40_lukk_maaling(%s,%s,%s)", (t, mid, "u-test"))
        rt.commit()
        sv = _sv()
        try:
            sv.execute("SELECT m40_sveip_medarbeider(1000)")
            sv.commit()
        finally:
            sv.close()
        _sett_kontekst(mg, t)
        typer = {r[0] for r in mg.execute(
            "SELECT funntype FROM medarbeiderfunn WHERE tenant=%s"
            " AND apen", (t,))}
    assert "maaling_uten_lesbar_gruppe" in typer


@pg
def test_sveipen_leser_aldri_en_pulsverdi():
    """SVEIPEN TELLER, DEN LESER IKKE.

    En sveip som var unntatt fra terskelen ville vært hullet i den.
    Porten leser sveipens egen SQL og krever at `verdi` ikke er nevnt
    noe sted i den.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    start = sql.index("CREATE FUNCTION m40_sveip_medarbeider")
    slutt = sql.index("RETTIGHETENE. SP-7", start)
    kropp = sql[start:slutt]
    assert "s.verdi" not in kropp and "avg(" not in kropp, (
        "sveipen rorer en pulsverdi")


@pg
def test_sveipen_ser_alle_tenanter_og_ikke_null():
    """130s LÆRDOM: EN BLIND SVEIP MELDER NULL FUNN OG SER FRISK UT."""
    a, b = _tenantnavn("sa"), _tenantnavn("sb")
    with _to() as (rt, _mg):
        for t in (a, b):
            _krav(rt, t)
    sv = _sv()
    try:
        n = sv.execute("SELECT tenanter FROM m40_sveip_medarbeider(1000)"
                       ).fetchone()[0]
        sv.commit()
    finally:
        sv.close()
    assert n >= 2, f"sveipen sa bare {n} tenanter"


@pg
def test_tenantvakten_nekter_en_annen_tenants_data():
    """`tenantlekkasje_i_medarbeiderregister`."""
    a, b = _tenantnavn("va"), _tenantnavn("vb")
    with _to() as (rt, _mg):
        _krav(rt, a)
        _sett_kontekst(rt, a)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT * FROM m40_bildet(%s)", (b,))
        rt.rollback()


@pg
def test_bildet_teller_alltid_null_beslutninger_og_profiler():
    """ET TALL SOM ALLTID ER NULL ER IKKE PYNT.

    Det er stedet et menneske kan se etter for å oppdage den dagen det
    ikke er det.
    """
    t = _tenantnavn("bilde")
    with _to() as (rt, mg):
        kv = _krav(rt, t)
        taker = _ansatt(mg, t)
        _lop(rt, t, taker, kv)
        _sett_kontekst(rt, t)
        rad = rt.execute(
            "SELECT apne_lop, beslutninger, individprofiler"
            " FROM m40_bildet(%s)", (t,)).fetchone()
    assert rad == (1, 0, 0)


@pg
def test_alle_lagre_er_registrert_i_retensjonsregisteret():
    """093s KRAV: HVERT LAGER SKAL VÆRE KJENT, MED EN DOM.

    Og pulssvaret er det ene som får `uten_frist_akseptert` framfor
    `uten_frist_apen` — fordi fraværet av frist der er VALGT og ikke
    ubestemt: det finnes ingen persondata å sette en frist for.
    """
    with _to() as (_rt, mg):
        mg.execute("SET LOCAL ROLE disponit_lager_eier")
        rader = dict(mg.execute(
            "SELECT relasjon, dom FROM retensjonslager"
            " WHERE dom_migrasjon='140'").fetchall())
        mg.rollback()
    assert set(rader) == set(EGNE), (
        f"lagre uten dom: {set(EGNE) - set(rader)}")
    assert rader["pulssvar"] == "uten_frist_akseptert"
    assert rader["ansattlop"] == "uten_frist_apen"
