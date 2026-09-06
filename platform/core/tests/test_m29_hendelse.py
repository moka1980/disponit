"""M-29 sikkerhets- og hendelsesagent v1 (137) — KLYNGE 10s FØRSTE.

Grensen `m29-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR
koden (§0-regelen), og hver invariant der har minst én port her.

KLYNGENS DELTE DOM:

  EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN ANGRES IKKE AV EN
  ROLLBACK.

Klynge 9s ytring kunne ikke tas tilbake fordi noen hadde LEST den.
Denne trenger ingen leser: kontoen er stengt, hemmeligheten er rullet,
og tokenet den gamle klienten holdt er dødt. Databasen kan rulles
tilbake til sekundet før — klienten er fortsatt logget ut.

DEN TYNGSTE GRUPPEN PORTER MÅLER ET FRAVÆR AV RETTIGHETER, IKKE EN
OPPFØRSEL. M-29 er den ENESTE modulen i klyngen der fullmaktsmålene
allerede ligger i basen: `api_tokener`, `modultoken`, `brukersesjon`,
`tenant_pseudonymnokkel` og `brukeridentitet`. En modul med UPDATE på
dem kunne stengt huset ute av seg selv.

`test_modulen_har_ingen_rett_paa_noe_den_kunne_isolert` måler det mot
`has_table_privilege` — ikke mot prosaen i migrasjonen.

FIRE PORTER MÅLER ET FRAVÆR SOM ER ET BEVIS: `inngrep_uten_playbook`,
`fri_kommando_kjort`, `hendelse_uten_score` og `score_uten_regel` står
i funntypesettet OG kan aldri reises.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import secrets
import contextlib
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN  # noqa: F401

HENDELSESSVEIP_DSN = os.environ.get("DISPONIT_TEST_HENDELSESSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "137_m29_hendelse.sql")
FUNDAMENT = ROT / "docs" / "KLYNGE10-FUNDAMENT.md"

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

#: TABELLENE MODULEN EIER.
EGNE = ("hendelseskrav", "sikkerhetsregel", "playbook", "playbooksteg",
        "sikkerhetshendelse", "hendelsessignal", "inngrepsforslag",
        "hendelsesfunn")

#: FULLMAKTSMÅLENE. Alle fem finnes i basen FØR M-29, og alle fem er
#: nøyaktig det en «isoler konto og roter secrets»-modul ville skrevet
#: i. Lista er PINNET og ikke avledet: en tabell som forsvant herfra
#: fordi noen omdøpte den, ville gjort porten grønn av seg selv.
MAALENE = ("api_tokener", "modultoken", "brukersesjon",
           "tenant_pseudonymnokkel", "brukeridentitet")

#: ROLLENE MODULEN HAR.
ROLLENE = ("disponit_hendelse_eier", "disponit_hendelsessveip")


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
    return koble(HENDELSESSVEIP_DSN or MIGRATOR_DSN)


def _sett_kontekst(conn, tenant):
    conn.execute("SELECT set_config('disponit.tenant', %s, false)",
                 (tenant,))


def _tenantnavn(merke: str) -> str:
    return f"t-m29-{merke}-{secrets.token_hex(4)}"


def _naa(**kw):
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(**kw)


I_DAG = dt.date.today()


def _nektes(mg, t, sql, args, *, teller_sql, teller_args):
    """EN VAKT SOM IKKE FÅR NOE Å BITE I, BITER IKKE.

    `set_config('disponit.tenant', ..., false)` settes inne i en
    transaksjon og RULLES TILBAKE MED DEN. En port som gjør
    `rollback()` og deretter prøver neste setning uten å sette
    konteksten på nytt, treffer NULL RADER under FORCE RLS: ingen vakt
    fyrer, og porten er grønn uten å ha prøvd noe. 134s lærdom.
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

def _krav(rt, t, *, vindu=60, terskel=100, frist=7, tak=50):
    """VERSJONEN TILDELES AV DØRA, IKKE AV KALLEREN.

    En versjon kalleren velger er ingen versjon: to kallere kunne valgt
    samme tall, og den siste ville stille overskrevet forklaringen på
    hver hendelse som alt pekte dit.
    """
    _sett_kontekst(rt, t)
    v = rt.execute("SELECT m29_sett_krav(%s,%s,%s,%s,%s,%s)",
                   (t, vindu, terskel, frist, tak, "u-test")).fetchone()[0]
    rt.commit()
    return v


def _regel(rt, t, *, signaltype="unntak_gjentatt", poeng=50,
           terskel_treff=2, fra=None, til=None):
    _sett_kontekst(rt, t)
    rid = uuid.uuid4()
    rt.execute(
        "SELECT m29_registrer_regel(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (t, rid, f"regel-{secrets.token_hex(3)}", signaltype, poeng,
         terskel_treff, "fordi gjentatte unntak er et moenster",
         fra or (I_DAG - dt.timedelta(days=1)), til, "u-test"))
    rt.commit()
    return rid


def _playbook(rt, t, *, steg=None, tofaktor=True, fra=None, til=None):
    _sett_kontekst(rt, t)
    pid = uuid.uuid4()
    rt.execute(
        "SELECT m29_registrer_playbook(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (t, pid, f"pb-{secrets.token_hex(3)}",
         "naar det samme unntaket gjentar seg", tofaktor,
         steg or ["varsle_sikkerhetsansvarlig", "samle_tidslinje"],
         fra or (I_DAG - dt.timedelta(days=1)), til, "u-test"))
    rt.commit()
    return pid


def _korreler(rt, t, rid, kv, *, n=3, naar=None):
    _sett_kontekst(rt, t)
    hid = uuid.uuid4()
    naar = naar or _naa(minutes=-30)
    rad = rt.execute(
        "SELECT * FROM m29_korreler(%s,%s,%s,%s,%s,%s,%s,%s)",
        (t, hid, rid, kv,
         [10_000 + i for i in range(n)],
         [f"aktor-{i}" for i in range(n)],
         [naar + dt.timedelta(seconds=i) for i in range(n)],
         "u-test")).fetchone()
    rt.commit()
    return hid, rad


# =====================================================================
# GRENSEN OG DOMMEN.
# =====================================================================

def test_grensen_ble_registrert_for_koden():
    """§0-REGELEN: `m29-v1` sto i KRAVGRENSER før 137 fantes.

    Klynge 10-fundamentet (#403) registrerte den; denne fila landet
    etterpå. At grensen er skrevet av noen som ennå ikke visste hvor
    vanskelig den ble å holde, er hele poenget med rekkefølgen.
    """
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m29-v1"]
    assert g["maks_brudd"] == 0
    assert "ddl_begge_kjoringer_gronne" in g["krav_ja"]
    for navn in ("modulen_isolerte_konto", "modulen_roterte_hemmelighet",
                 "inngrep_uten_playbook", "fri_kommando_kjort",
                 "hendelse_uten_score", "score_uten_regel",
                 "leste_sitt_eget_spor_som_signal"):
        assert navn in g["invarianter"], navn


@pg
def test_modulen_har_ingen_rett_paa_noe_den_kunne_isolert():
    """DEN VIKTIGSTE PORTEN I FILA.

    M-29 er den ENESTE modulen i klyngen der fullmaktsmålene allerede
    ligger i basen. `api_tokener.secret_mac`, `modultoken.tilbakekalt_ts`,
    `brukersesjon.tilbakekalt`, `tenant_pseudonymnokkel.nokkel` og
    `brukeridentitet` er nøyaktig de radene en «isoler konto, token
    eller workload og roter secrets»-modul ville skrevet i.

    En modul med UPDATE på dem kunne stengt huset ute av seg selv, og
    den ville gjort det raskere enn noe menneske rakk å lese
    logglinjen.

    PORTEN MÅLER `has_table_privilege`, IKKE PROSAEN I MIGRASJONEN.
    Verken eieren eller sveipen skal ha NOEN rett — ikke engang SELECT:
    en modul som kunne LESE `secret_mac` har allerede hemmeligheten.

    MUTASJONEN SOM DREPER DENNE: legg
    `GRANT SELECT ON api_tokener TO disponit_hendelse_eier` i 137.
    """
    with _to() as (_rt, mg):
        funnet = {}
        for rolle in ROLLENE:
            for tab in MAALENE:
                rader = mg.execute(
                    "SELECT p FROM unnest(ARRAY['SELECT','INSERT',"
                    "'UPDATE','DELETE','TRUNCATE','REFERENCES']) p"
                    " WHERE has_table_privilege(%s, %s, p)",
                    (rolle, tab)).fetchall()
                if rader:
                    funnet[f"{rolle}.{tab}"] = [r[0] for r in rader]
        assert funnet == {}, (
            "M-29 har rettigheter paa fullmaktsmaalene: " + repr(funnet))


@pg
def test_modulen_har_heller_ingen_rett_paa_ovrige_tabeller():
    """…OG IKKE PÅ NOE ANNET HELLER, MED TO NAVNGITTE UNNTAK.

    Porten over navngir fem tabeller. Den er nødvendig, men ikke
    tilstrekkelig: en sjette hemmelighetstabell i en senere migrasjon
    ville ikke stått på lista, og porten ville vært grønn.

    DENNE MÅLER DET MOTSATTE: hva modulen HAR, framfor hva den ikke
    har. Settet skal være modulens egne åtte, pluss `revisjonslogg` —
    signalkilden, med SELECT for å lese og INSERT for husets evidens.

    OG IKKE `retensjonslager`. Migrasjonen skriver dommene sine der,
    men den gjør det som `disponit_lager_eier` og gir seg selv INGEN
    stående rett. Det er strengere enn en grant ville vært: dommen
    felles ÉN gang, i git, og modulen kan ikke endre den etterpå —
    093s egen form, «dommene felles i git, ikke gjennom en dør».

    MUTASJONEN SOM DREPER DENNE: gi eieren SELECT på en vilkårlig
    tabell til — for eksempel `retensjonslager`.
    """
    with _to() as (_rt, mg):
        rader = mg.execute(
            "SELECT c.relname, string_agg(DISTINCT pr, ',' ORDER BY pr)"
            "  FROM pg_class c"
            "  JOIN pg_namespace n ON n.oid = c.relnamespace,"
            "       unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE']) pr"
            " WHERE n.nspname = 'public' AND c.relkind = 'r'"
            "   AND has_table_privilege('disponit_hendelse_eier',"
            "                           c.oid, pr)"
            " GROUP BY 1 ORDER BY 1").fetchall()
        har = {r[0]: r[1] for r in rader}
        ventet = set(EGNE) | {"revisjonslogg"}
        assert set(har) == ventet, (
            "eieren naar noe annet enn sine egne:"
            f" {sorted(set(har) - ventet)}, mangler:"
            f" {sorted(ventet - set(har))}")
        # SIGNALKILDEN LESES, DEN ENDRES IKKE.
        assert har["revisjonslogg"] == "INSERT,SELECT", har["revisjonslogg"]


@pg
def test_playbooken_kan_ikke_uttrykke_en_fri_kommando():
    """«INGEN FRI KOMMANDOKJØRING» ER EN GRAMMATIKK, IKKE EN POLICY.

    Et steg er ET NAVN FRA ET LUKKET SETT, og `playbooksteg` har ingen
    kolonne som kan bære en parameter, en streng, en sti eller et
    skript.

    Det er forskjellen på å forby noe og å gjøre det uuttrykkelig:
    `isoler_konto` pluss en fri parameterstreng ER en fri kommando med
    et pent navn.

    PORTEN MÅLER BEGGE HALVDELER: at settet er lukket i basen, og at
    det ikke finnes noe sted å legge argumentet.

    MUTASJONEN SOM DREPER DENNE: legg til
    `argumenter TEXT` på `playbooksteg`.
    """
    with _to() as (_rt, mg):
        kolonner = {r[0]: r[1] for r in mg.execute(
            "SELECT column_name, data_type FROM information_schema.columns"
            " WHERE table_name = 'playbooksteg'").fetchall()}
        assert set(kolonner) == {
            "tenant", "playbook_id", "stegnr", "stegtype", "opprettet"}, (
            "playbooksteg har faatt et sted aa legge et argument:"
            f" {sorted(kolonner)}")
        # DET LUKKEDE SETTET, LEST FRA BASEN.
        cd = mg.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'playbooksteg_stegtype_lukket'"
        ).fetchone()
        assert cd, "stegtype er ikke lukket"
        assert "isoler_konto" in cd[0] and "roter_hemmelighet" in cd[0]
        # …OG INGEN ÅPEN DØR I DET LUKKEDE SETTET. Et sett med en
        # `annet`-verdi er et åpent sett med en pen fasade — 116s
        # `klassifisering_utenfor_lukket_sett` anvendt på seg selv.
        for apen in ("annet", "andre", "fritekst", "kommando", "custom"):
            assert apen not in cd[0], f"«{apen}» er en aapen doer"


@pg
def test_et_fritt_steg_avvises_av_basen():
    """…OG SETTET BITER, MÅLT.

    Porten over leser CHECK-en. Denne prøver den: et stegnavn utenfor
    settet skal avvises av BASEN, ikke av en instruks.
    """
    t = _tenantnavn("steg")
    with _to() as (rt, _mg):
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.CheckViolation):
            rt.execute(
                "SELECT m29_registrer_playbook(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), "pb", "naar", True,
                 ["kjor_shell:rm -rf /"], I_DAG, None, "u-test"))
        rt.rollback()


@pg
def test_inngrepsforslaget_har_ingen_utforelse():
    """DER VEIEN SLUTTER.

    `inngrepsforslag` har ingen `utfort_ts`, ingen `resultat`, ingen
    `kvittering` og ingen `status` som kan bli `utfort`. Forslaget ER
    endestasjonen, og det er ikke en forglemmelse — det er v1-dommen
    skrevet som kolonner.

    `m29_bildet.inngrep_utfort` er derfor alltid 0, og tallet er ikke
    en telling: det er en påstand om at kolonnen ikke finnes.

    MUTASJONEN SOM DREPER DENNE: legg til `utfort_ts TIMESTAMPTZ`.
    """
    t = _tenantnavn("slutt")
    with _to() as (rt, mg):
        kolonner = {r[0] for r in mg.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'inngrepsforslag'").fetchall()}
        for forbudt in ("utfort_ts", "utfort", "resultat", "kvittering",
                        "status", "gjennomfort_ts"):
            assert forbudt not in kolonner, forbudt

        kv = _krav(rt, t)
        rid = _regel(rt, t)
        pid = _playbook(rt, t)
        hid, _ = _korreler(rt, t, rid, kv)
        _sett_kontekst(rt, t)
        rt.execute("SELECT m29_foresla_inngrep(%s,%s,%s,%s,%s,%s)",
                   (t, uuid.uuid4(), hid, pid, "gjentatt unntak", "u-test"))
        rt.commit()
        _sett_kontekst(rt, t)
        bilde = rt.execute("SELECT * FROM m29_bildet(%s)", (t,)).fetchone()
        assert bilde[4] == 1, "forslaget ble ikke skrevet"
        assert bilde[5] == 0, "modulen rapporterer et utfoert inngrep"


@pg
def test_de_fire_umulige_staar_i_settet_og_kan_ikke_reises():
    """AT DE STÅR DER OG ER UMULIGE ER BEVISET.

    Et sett som ikke navnga dem ville ikke sagt noe. Et sett som navnga
    dem og kunne fylles ville sagt at vernet er en sveip.

    PORTEN MÅLER BEGGE HALVDELER: at navnene står i det lukkede settet
    i basen, og at datamodellen utelukker hver enkelt av dem.

    MUTASJONEN SOM DREPER DENNE: gjør `sikkerhetshendelse.score`
    nullbar.
    """
    with _to() as (_rt, mg):
        cd = mg.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'hendelsesfunn_funntype_lukket'"
        ).fetchone()[0]
        for umulig in ("inngrep_uten_playbook", "fri_kommando_kjort",
                       "hendelse_uten_score", "score_uten_regel"):
            assert umulig in cd, umulig

        # …OG DATAMODELLEN UTELUKKER DEM, KOLONNE FOR KOLONNE.
        nullbar = {r[0]: r[1] for r in mg.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns"
            " WHERE table_name = 'sikkerhetshendelse'").fetchall()}
        assert nullbar["score"] == "NO", "hendelse_uten_score er mulig"
        assert nullbar["regel_id"] == "NO", "score_uten_regel er mulig"

        pb = {r[0]: r[1] for r in mg.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns"
            " WHERE table_name = 'inngrepsforslag'").fetchall()}
        assert pb["playbook_id"] == "NO", "inngrep_uten_playbook er mulig"

        # FREMMEDNØKLENE, IKKE BARE NOT NULL. En NOT NULL uten
        # fremmednøkkel ville tillatt en regel-id som ikke finnes —
        # altså en score med en forklaring ingen kan slå opp.
        fk = {r[0] for r in mg.execute(
            "SELECT conname FROM pg_constraint WHERE contype = 'f'"
            " AND conrelid IN ('sikkerhetshendelse'::regclass,"
            "                  'inngrepsforslag'::regclass)").fetchall()}
        for navn in ("sikkerhetshendelse_regel_fk",
                     "sikkerhetshendelse_krav_fk",
                     "inngrepsforslag_playbook_fk"):
            assert navn in fk, navn


@pg
def test_scoren_er_regelens_ikke_kallerens():
    """132s LÆRDOM, ANVENDT PÅ EN SIKKERHETSSCORE.

    «Treffet regnes av båndet, ikke av kalleren.» Ingen parameter i
    `m29_korreler` setter en score; den regnes av `poeng * treff` mot
    regelens egen terskel.

    En dør som tok imot en score ville gjort «forklarbare regler» til
    pynt: forklaringen ville pekt på en regel, mens tallet kom fra et
    helt annet sted.

    MUTASJONEN SOM DREPER DENNE: legg til en `p_score INT`-parameter.
    """
    t = _tenantnavn("score")
    with _to() as (rt, mg):
        # `pg_get_function_arguments` gir INN-signaturen. `proargnames`
        # ville tatt med OUT-parameterne — altså `score`, `alvor` og
        # `signaler`, som er det funksjonen RETURNERER — og porten ville
        # felt seg selv på sitt eget svar. Samme skygging som bet i 134
        # og 135.
        args = mg.execute(
            "SELECT pg_get_function_arguments(p.oid) FROM pg_proc p"
            " WHERE p.proname = 'm29_korreler'").fetchone()[0]
        assert "score" not in args, (
            f"m29_korreler tar imot en score: {args}")
        # …OG HELLER IKKE ET ALVOR. Alvoret er scoren mot tenantens
        # terskel; en kaller som kunne oppgi det ville satt karakteren
        # på hendelsen selv.
        assert "alvor" not in args, args

        kv = _krav(rt, t, terskel=100)
        rid = _regel(rt, t, poeng=50, terskel_treff=2)
        _hid, rad = _korreler(rt, t, rid, kv, n=3)
        # 50 poeng * 3 treff = 150, over terskelen paa 100.
        assert rad[0] == 150, rad
        assert rad[1] == "over_terskel", rad


@pg
def test_under_regelens_terskel_gir_null_poeng():
    """ÉN FEILINNLOGGING ER IKKE EN HENDELSE.

    Regelens `terskel_treff` er ikke en bekvemmelighet. Uten den ville
    hvert enkeltsignal gitt poeng, og et deteksjonsapparat som slår ut
    på alt sier ingenting.
    """
    t = _tenantnavn("terskel")
    with _to() as (rt, _mg):
        kv = _krav(rt, t, terskel=10)
        rid = _regel(rt, t, poeng=50, terskel_treff=5)
        _hid, rad = _korreler(rt, t, rid, kv, n=2)
        assert rad[0] == 0, rad
        assert rad[1] == "under_terskel", rad


@pg
def test_alvoret_lagres_og_folger_ikke_en_senere_terskel():
    """KLYNGE 7s DOM, ANVENDT PÅ EN SIKKERHETSTERSKEL.

    Alvor er AVLEDET av score mot terskel — men det LAGRES. Endres
    terskelen i morgen, skal gårsdagens hendelse ikke stille skifte
    alvor.

    En foreldet terskel ser nøyaktig ut som en riktig terskel, og en
    hendelse som var «over terskel» da noen så på den, var det.

    MUTASJONEN SOM DREPER DENNE: regn alvoret i lesedøra i stedet for
    å lagre det.
    """
    t = _tenantnavn("alvor")
    with _to() as (rt, _mg):
        kv = _krav(rt, t, terskel=100)
        rid = _regel(rt, t, poeng=50, terskel_treff=2)
        hid, rad = _korreler(rt, t, rid, kv, n=3)
        assert rad[1] == "over_terskel"

        # TERSKELEN HEVES LANGT OVER SCOREN.
        kv2 = _krav(rt, t, terskel=9000)
        assert kv2 == kv + 1, (
            "doera gjenbrukte kravversjonen i stedet for aa tildele en ny")
        _sett_kontekst(rt, t)
        rader = rt.execute("SELECT * FROM m29_hendelsene(%s,%s)",
                           (t, 10)).fetchall()
        rad2 = [r for r in rader if r[0] == hid][0]
        assert rad2[4] == "over_terskel", (
            "alvoret fulgte den nye terskelen — hendelsen skiftet"
            " karakter uten at noe skjedde")


@pg
def test_samme_loggrad_teller_en_gang():
    """EN HENDELSE SKAL IKKE VOKSE AV Å BLI SETT PÅ.

    `hendelsessignal_kilden_telles_en_gang` gjør at en gjentatt
    korrelasjonskjøring over de samme loggradene ikke legger til nye
    signaler.

    Uten den ville en sveip som kjørte to ganger doblet grunnlaget for
    en hendelse uten at noe nytt hadde skjedd — og scoren ville
    fortalt om systemets kadens framfor om verden.

    MUTASJONEN SOM DREPER DENNE: fjern UNIQUE-constrainten.
    """
    t = _tenantnavn("dobbelt")
    with _to() as (rt, mg):
        kv = _krav(rt, t)
        rid = _regel(rt, t)
        hid, _ = _korreler(rt, t, rid, kv, n=3)
        # SAMME KILDEREFERANSER EN GANG TIL, PÅ SAMME HENDELSE.
        #
        # SKRIVES SOM MIGRATOR, IKKE SOM KJØRETIDEN: SP-7 sier at
        # kjøretiden når DØRENE og ingen tabeller, og et INSERT herfra
        # som `disponit` ville feilet på rettigheter framfor på
        # constrainten — altså en port som var grønn av feil grunn.
        _sett_kontekst(mg, t)
        mg.execute(
            "INSERT INTO hendelsessignal (tenant, signal_id, hendelse_id,"
            " signaltype, kilde_ref, aktor, observert_ts)"
            " SELECT %s, gen_random_uuid(), %s, 'unntak_gjentatt',"
            " %s, 'igjen', now()"
            " ON CONFLICT ON CONSTRAINT"
            " hendelsessignal_kilden_telles_en_gang DO NOTHING",
            (t, hid, 10_000))
        mg.commit()
        _sett_kontekst(rt, t)
        n = rt.execute(
            "SELECT count(*) FROM m29_tidslinjen(%s,%s)", (t, hid)
        ).fetchone()[0]
        assert n == 3, f"hendelsen vokste til {n} signaler"


@pg
def test_modulen_leser_ikke_sitt_eget_spor_som_signal():
    """MODULEN LESER NOE DEN OGSÅ SKRIVER I, OG DEN ER ALENE OM DET.

    `revisjonslogg` (M-2) er husets eneste applikasjonslogg OG M-29s
    viktigste signalkilde. Hver `m29_evidens`-rad er en ny rad der.

    Uten filteret ville hver evidensrad blitt et nytt signal,
    korrelasjonen ville plukket det opp, scoren ville steget — og
    HENDELSEN VILLE VOKST AV Å BLI SETT PÅ.

    PORTEN SKRIVER FAKTISK EN EVIDENSRAD OG SER AT DEN IKKE KOMMER UT
    IGJEN. En port som bare leste SQL-teksten ville vært grønn på en
    filterlinje som sto feil sted i spørringen.

    MUTASJONEN SOM DREPER DENNE: fjern
    `AND r.kilde <> 'm29_hendelse'` fra `m29_signalkilden`.
    """
    t = _tenantnavn("spor")
    with _to() as (rt, mg):
        fra = _naa(minutes=-1)
        # EN HANDLING SOM ETTERLATER EVIDENS.
        _krav(rt, t)
        _sett_kontekst(mg, t)
        egne = mg.execute(
            "SELECT count(*) FROM revisjonslogg"
            " WHERE tenant = %s AND kilde = 'm29_hendelse'", (t,)
        ).fetchone()[0]
        assert egne >= 1, (
            "porten maaler ingenting: modulen skrev ingen evidens")

        _sett_kontekst(rt, t)
        sett = rt.execute(
            "SELECT count(*) FROM m29_signalkilden(%s,%s)", (t, fra)
        ).fetchone()[0]
        kilder = [r[0] for r in rt.execute(
            "SELECT DISTINCT kilde FROM m29_signalkilden(%s,%s)", (t, fra)
        ).fetchall()]
        assert "m29_hendelse" not in kilder, (
            f"modulen leser sitt eget spor som signal: {kilder}")
        assert sett == 0 or "m29_hendelse" not in kilder


@pg
def test_hver_egen_tabell_har_force_rls_og_tenantpolicy():
    """`tenantlekkasje_i_hendelsesregister`, MÅLT.

    FORCE er forskjellen: uten den ser eieren av tabellen forbi sin
    egen policy, og en SECURITY DEFINER-dør som eide tabellen ville
    lest alle tenanter uten å vite det.
    """
    with _to() as (_rt, mg):
        mangler = []
        for tab in EGNE:
            rad = mg.execute(
                "SELECT c.relrowsecurity, c.relforcerowsecurity,"
                "  EXISTS (SELECT 1 FROM pg_policy p"
                "           WHERE p.polrelid = c.oid"
                "             AND p.polname = 'tenant_isolasjon')"
                " FROM pg_class c WHERE c.relname = %s", (tab,)
            ).fetchone()
            if not rad or not all(rad):
                mangler.append((tab, rad))
        assert mangler == [], mangler


@pg
def test_en_tenant_ser_ikke_en_annens_hendelse():
    """…OG RADVAKTEN BITER, MÅLT MOT TO EKTE TENANTER."""
    a, b = _tenantnavn("a"), _tenantnavn("b")
    with _to() as (rt, _mg):
        kv = _krav(rt, a)
        rid = _regel(rt, a)
        hid, _ = _korreler(rt, a, rid, kv)
        _krav(rt, b)
        _sett_kontekst(rt, b)
        rader = rt.execute("SELECT * FROM m29_hendelsene(%s,%s)",
                           (b, 100)).fetchall()
        assert all(r[0] != hid for r in rader), (
            "tenant b ser tenant a sin hendelse")


@pg
def test_signalene_kan_ikke_endres_i_ettertid():
    """APPEND-ONLY MÅLT SOM EN RETTIGHET, IKKE SOM EN TRIGGER.

    Et signal som kunne endres i ettertid ville gjort tidslinjen til en
    påstand. Og forslaget er endestasjonen — kunne det oppdateres,
    ville noen før eller siden lagt en `utfort`-verdi i det.
    """
    with _to() as (_rt, mg):
        for tab in ("hendelsessignal", "inngrepsforslag", "playbooksteg"):
            kan = mg.execute(
                "SELECT has_table_privilege('disponit_hendelse_eier',"
                " %s, 'UPDATE')", (tab,)).fetchone()[0]
            assert not kan, f"{tab} kan oppdateres av eieren"


@pg
def test_en_playbook_uten_steg_avvises():
    """EN PLAYBOOK UTEN STEG FORKLARER INGENTING.

    Den ville tilfredsstilt fremmednøkkelen i `inngrepsforslag` og sagt
    ingenting — altså nøyaktig den fail-open-formen resten av modulen
    handler om.
    """
    t = _tenantnavn("tom")
    with _to() as (rt, _mg):
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute(
                "SELECT m29_registrer_playbook(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), "pb", "naar", True, [], I_DAG, None,
                 "u-test"))
        rt.rollback()


@pg
def test_ulike_lange_lister_avvises():
    """TRE LISTER ER ÉN TABELL SNUDD PÅ SIDEN.

    Er de ulike lange, ville løkka stilltiende brukt den korteste og
    tapt signaler — en hendelse som mangler halvparten av grunnlaget
    sitt og ikke sier fra.
    """
    t = _tenantnavn("lister")
    with _to() as (rt, _mg):
        kv = _krav(rt, t)
        rid = _regel(rt, t)
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute(
                "SELECT * FROM m29_korreler(%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), rid, kv, [1, 2, 3], ["a", "b"],
                 [_naa(), _naa(seconds=1), _naa(seconds=2)], "u-test"))
        rt.rollback()


@pg
def test_en_utlopt_regel_kan_ikke_forklare_en_ny_score():
    """EN FORELDET REGEL SER NØYAKTIG UT SOM EN RIKTIG REGEL.

    Klynge 7s dom. Regelen dør med en dato, ikke med en sletting — en
    score forklart av en regel som er BORTE er en score uten
    forklaring.
    """
    t = _tenantnavn("utlopt")
    with _to() as (rt, _mg):
        kv = _krav(rt, t)
        rid = _regel(rt, t, fra=I_DAG - dt.timedelta(days=30),
                     til=I_DAG - dt.timedelta(days=1))
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute(
                "SELECT * FROM m29_korreler(%s,%s,%s,%s,%s,%s,%s,%s)",
                (t, uuid.uuid4(), rid, kv, [1, 2], ["a", "b"],
                 [_naa(), _naa(seconds=1)], "u-test"))
        rt.rollback()
        # …MEN REGELEN STÅR, OG FORKLARER FORTSATT DE GAMLE.
        _sett_kontekst(rt, t)
        n = rt.execute("SELECT count(*) FROM m29_reglene(%s)",
                       (t,)).fetchone()[0]
        assert n == 1, "regelen ble slettet i stedet for aa utloepe"


# =====================================================================
# SVEIPEN.
# =====================================================================

@pg
def test_sveipen_finner_hendelsen_som_star_uten_forslag():
    """SVEIPENS VIKTIGSTE FUNN, OG DER v1 ER ÆRLIG.

    Modulen kan IKKE gjøre noe med hendelsen. Da er «å stå over
    terskel uten et eneste forslag» den eneste feilen den kan oppdage
    i seg selv.

    PORTEN MÅLER AT SVEIPEN FAKTISK SÅ NOE. En sveip som kjørte mot
    null rader ville rapportert null funn med grønn exit-kode — 130s
    lærdom, og den gjelder her.
    """
    t = _tenantnavn("sveip")
    with _to() as (rt, _mg):
        kv = _krav(rt, t, terskel=100)
        rid = _regel(rt, t, poeng=50, terskel_treff=2)
        hid, rad = _korreler(rt, t, rid, kv, n=3)
        assert rad[1] == "over_terskel", rad
    sv = _sv()
    try:
        rader = sv.execute("SELECT * FROM m29_sveip_hendelse(%s)",
                           (10_000,)).fetchone()
        sv.commit()
        assert rader[0] >= 1, "sveipen saa ingen tenanter i det hele tatt"
    finally:
        sv.close()
    with _to() as (rt, _mg):
        _sett_kontekst(rt, t)
        funn = rt.execute("SELECT * FROM m29_hendelsesfunn(%s,%s)",
                          (t, 100)).fetchall()
        typer = {f[1] for f in funn}
        assert "hendelse_uten_forslag" in typer, typer
        # …OG FUNNET PEKER PÅ DEN EKTE HENDELSEN.
        assert any(f[2] == str(hid) for f in funn
                   if f[1] == "hendelse_uten_forslag")


@pg
def test_sveipen_lukker_funnet_naar_forslaget_er_skrevet():
    """FUNNLISTEN ER IKKE EN LOGG SOM VOKSER MED KADENSEN.

    Sveipens egne funn lukkes av sveipen når tilstanden er borte. Et
    funn som ble stående etter at grunnen forsvant ville gjort
    funnlisten til en historikk framfor en tilstand.
    """
    t = _tenantnavn("lukk")
    with _to() as (rt, _mg):
        kv = _krav(rt, t, terskel=100)
        rid = _regel(rt, t, poeng=50, terskel_treff=2)
        hid, _ = _korreler(rt, t, rid, kv, n=3)
    sv = _sv()
    try:
        sv.execute("SELECT * FROM m29_sveip_hendelse(%s)", (10_000,))
        sv.commit()
    finally:
        sv.close()
    with _to() as (rt, _mg):
        _sett_kontekst(rt, t)
        apne = [f for f in rt.execute(
            "SELECT * FROM m29_hendelsesfunn(%s,%s)", (t, 100)).fetchall()
            if f[1] == "hendelse_uten_forslag" and f[2] == str(hid)]
        assert apne, "porten maaler ingenting: funnet ble aldri reist"
        pid = _playbook(rt, t)
        _sett_kontekst(rt, t)
        rt.execute("SELECT m29_foresla_inngrep(%s,%s,%s,%s,%s,%s)",
                   (t, uuid.uuid4(), hid, pid, "gjentatt", "u-test"))
        rt.commit()
    sv = _sv()
    try:
        sv.execute("SELECT * FROM m29_sveip_hendelse(%s)", (10_000,))
        sv.commit()
    finally:
        sv.close()
    with _to() as (rt, _mg):
        _sett_kontekst(rt, t)
        igjen = [f for f in rt.execute(
            "SELECT * FROM m29_hendelsesfunn(%s,%s)", (t, 100)).fetchall()
            if f[1] == "hendelse_uten_forslag" and f[2] == str(hid)]
        assert not igjen, "funnet sto igjen etter at grunnen forsvant"


@pg
def test_sveiperollen_naar_en_funksjon_og_bare_den():
    """SP-7, MÅLT PÅ SVEIPEROLLEN.

    Sveipen får EXECUTE på ÉN funksjon. Uten REVOKE-løkka ville
    Postgres' egen PUBLIC-grant gitt den alle dørene i modulen — også
    de som skriver.
    """
    with _to() as (_rt, mg):
        rader = mg.execute(
            "SELECT p.proname FROM pg_proc p"
            " WHERE p.pronamespace = 'public'::regnamespace"
            "   AND p.proname LIKE 'm29\\_%%'"
            "   AND has_function_privilege('disponit_hendelsessveip',"
            "                              p.oid, 'EXECUTE')"
            " ORDER BY 1").fetchall()
        assert [r[0] for r in rader] == ["m29_sveip_hendelse"], rader


@pg
def test_sveipen_uten_tenantkontekst_ser_ikke_null_rader():
    """130s LÆRDOM: EN BLIND SVEIP RAPPORTERER NULL MED GRØNN EXIT.

    Under FORCE RLS ser en spørring uten `disponit.tenant` NULL RADER.
    `m29_sveip_tenantliste` er den ene policyen som lar sveipen lese
    tenantlista i det hele tatt — og uten den ville løkka aldri
    startet, og sveipen ville meldt «null funn» om en base full av dem.

    MUTASJONEN SOM DREPER DENNE: fjern `m29_sveip_tenantliste`.
    """
    t = _tenantnavn("blind")
    with _to() as (rt, _mg):
        _krav(rt, t)
    sv = _sv()
    try:
        rad = sv.execute("SELECT * FROM m29_sveip_hendelse(%s)",
                         (10_000,)).fetchone()
        sv.commit()
        assert rad[0] >= 1, (
            "sveipen saa null tenanter — den er blind, ikke ren")
    finally:
        sv.close()


@pg
def test_de_umulige_funnene_kan_ingen_lukke():
    """132s FORM: HVEM SOM KAN LUKKE HVA.

    Sveipens egne funn lukkes av sveipen når tilstanden er borte. En
    dør som lot et menneske lukke dem ville sagt at de KAN oppstå.

    PORTEN LESER GJENNOM LESEDØRA, IKKE GJENNOM `m29_funn_er_sveipens`
    DIREKTE. Den funksjonen er REVOKEt fra PUBLIC og gis ingen — den
    er modulens indre, ikke en dør. En port som kalte den ville målt
    noe kjøretiden aldri ser.
    """
    t = _tenantnavn("lukkefunn")
    sveipens = ("apen_hendelse_over_frist", "hendelse_uten_forslag",
                "regel_uten_treff", "playbook_uten_steg",
                "signaltak_naadd", "krav_mangler")
    menneskets = ("inngrep_uten_playbook", "fri_kommando_kjort",
                  "hendelse_uten_score", "score_uten_regel")
    with _to() as (rt, mg):
        _sett_kontekst(mg, t)
        ider = {}
        for typ in sveipens + menneskets:
            fid = uuid.uuid4()
            ider[typ] = fid
            mg.execute(
                "INSERT INTO hendelsesfunn (tenant, funn_id, funntype,"
                " referanse, detalj) VALUES (%s,%s,%s,'r','d')",
                (t, fid, typ))
        mg.commit()

        _sett_kontekst(rt, t)
        flagg = {r[1]: r[4] for r in rt.execute(
            "SELECT * FROM m29_hendelsesfunn(%s,%s)", (t, 100)).fetchall()}
        assert len(flagg) == len(sveipens) + len(menneskets), flagg
        for typ in sveipens:
            assert flagg[typ] is True, typ
        for typ in menneskets:
            assert flagg[typ] is False, typ

        # …OG DØRA NEKTER Å LUKKE SVEIPENS.
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute("SELECT m29_lukk_funn(%s,%s,%s,%s)",
                       (t, ider["regel_uten_treff"], "fordi", "u-test"))
        rt.rollback()

        # …MEN LAR ET MENNESKE LUKKE ET AV DE ANDRE. Uten denne
        # halvdelen kunne doera nektet ALT og porten vaert groenn.
        _sett_kontekst(rt, t)
        rt.execute("SELECT m29_lukk_funn(%s,%s,%s,%s)",
                   (t, ider["score_uten_regel"], "avklart", "u-test"))
        rt.commit()
        _sett_kontekst(rt, t)
        igjen = {r[1] for r in rt.execute(
            "SELECT * FROM m29_hendelsesfunn(%s,%s)", (t, 100)).fetchall()}
        assert "score_uten_regel" not in igjen


@pg
def test_migrasjonen_gir_ingen_dor_en_kommandostreng():
    """«INGEN FRI KOMMANDOKJØRING», MÅLT PÅ HELE MODULEN.

    Portene over måler `playbooksteg`. Denne måler DØRENE: ingen
    funksjon i modulen tar imot et argument som kunne vært en kommando,
    en sti eller et skript.

    En modul som holdt tilbake fullmakten i tabellene, men tok imot en
    `p_kommando TEXT` i en dør, ville holdt igjen på feil sted.

    MUTASJONEN SOM DREPER DENNE: legg til en dør med et
    `p_kommando`-argument.
    """
    mistenkelig = re.compile(
        r"p_(kommando|command|cmd|skript|script|sql|sti|path|payload"
        r"|argument|parametre|eval|exec)")
    with _to() as (_rt, mg):
        rader = mg.execute(
            "SELECT p.proname, pg_get_function_arguments(p.oid)"
            "  FROM pg_proc p"
            " WHERE p.pronamespace = 'public'::regnamespace"
            "   AND p.proname LIKE 'm29\\_%%' ORDER BY 1").fetchall()
        assert len(rader) >= 10, f"porten maaler nesten ingenting: {rader}"
        funnet = {navn: args for navn, args in rader
                  if mistenkelig.search(args or "")}
        assert funnet == {}, funnet


@pg
def test_kravet_kan_ikke_endres_etter_at_en_hendelse_peker_paa_det():
    """«TERSKELEN SOM GJALDT» MÅ VÆRE GJENFINNBAR.

    `sikkerhetshendelse.kravversjon` er en fremmednøkkel til
    `hendelseskrav`, og hele poenget med den er at scoren skal kunne
    forklares i ettertid. Kunne raden endres, ville oppslaget gitt
    DAGENS terskel og sett like riktig ut.

    FØRSTE UTGAVE AV 137 LOT KALLEREN VELGE VERSJONEN og gjorde
    `ON CONFLICT DO UPDATE`. To kallere kunne da valgt samme tall, og
    den siste ville stille overskrevet forklaringen på hver hendelse
    som alt pekte dit.

    PORTEN MÅLER BEGGE HALVDELER: at eieren ikke har UPDATE, og at døra
    tildeler et NYTT nummer framfor å gjenbruke det gamle.

    MUTASJONEN SOM DREPER DENNE: gi eieren UPDATE på `hendelseskrav`.
    """
    t = _tenantnavn("krav")
    with _to() as (rt, mg):
        kan = mg.execute(
            "SELECT has_table_privilege('disponit_hendelse_eier',"
            " 'hendelseskrav', 'UPDATE')").fetchone()[0]
        assert not kan, "kravet kan endres etter at det er brukt"

        a = _krav(rt, t, terskel=100)
        b = _krav(rt, t, terskel=9000)
        assert b == a + 1, (a, b)
        # …OG BEGGE STÅR. En ny versjon skal ikke ha spist den gamle.
        _sett_kontekst(mg, t)
        n = mg.execute("SELECT count(*) FROM hendelseskrav WHERE tenant=%s",
                       (t,)).fetchone()[0]
        assert n == 2, f"{n} kravrader — den gamle forsvant"


@pg
def test_korrelasjonen_trenger_ingen_las_fordi_kravet_er_ufranderlig():
    """136 TRENGTE `FOR UPDATE`. DENNE GJØR IKKE, OG DET ER MÅLT.

    M-45 låste perioden fordi den kunne lukkes mellom lesing og
    skriving. Her KAN ikke kravet endres — raden er append-only, og
    eieren har ingen UPDATE.

    En lås mot en endring som er umulig måler ingenting. Verre: den
    ville KREVD den UPDATE-retten vi nettopp fjernet, og dermed brutt
    døra i stedet for å verne den.

    PORTEN LESER KILDEN. Det er svakere enn å måle oppførsel, men her
    er fraværet av en setning nettopp det som skal måles — og en
    `FOR UPDATE` som snek seg inn ville felt `m29_korreler` med
    «permission denied» i produksjon, ikke i en test.
    """
    kilde = MIGRASJON.read_text(encoding="utf-8")
    start = kilde.index("CREATE FUNCTION m29_korreler")
    slutt = kilde.index("CREATE FUNCTION", start + 10)
    kropp = kilde[start:slutt]
    # KOMMENTARENE NEVNER `FOR UPDATE` MED VILJE — porten skal måle
    # koden, ikke begrunnelsen.
    uten_kommentar = "\n".join(
        l for l in kropp.splitlines() if not l.lstrip().startswith("--"))
    assert "FOR UPDATE" not in uten_kommentar, (
        "m29_korreler har faatt en FOR UPDATE paa en uforanderlig rad")


@pg
def test_avviklingen_er_enveis_i_basen_ikke_bare_i_prosaen():
    """EN REGEL SOM KUNNE GJENOPPLIVES GIR TO SVAR PÅ ETT SPØRSMÅL.

    `m29_avvikle_regel` sa ENVEIS i docstringen sin, men UPDATE-en
    hadde ingen `gyldig_til IS NULL`: et andre kall med en senere dato
    ville gjenopplivet regelen. «Hvilken regel forklarte denne scoren»
    ville da hatt to svar, avhengig av når man spurte.

    CodeRabbit fant det 6/9.

    MUTASJONEN SOM DREPER DENNE: ta `AND r.gyldig_til IS NULL` ut av
    `m29_avvikle_regel`.
    """
    t = _tenantnavn("enveis")
    with _to() as (rt, _mg):
        rid = _regel(rt, t)
        _sett_kontekst(rt, t)
        rt.execute("SELECT m29_avvikle_regel(%s,%s,%s,%s)",
                   (t, rid, I_DAG, "u-test"))
        rt.commit()
        # ANDRE FORSØK MED EN SENERE DATO SKAL NEKTES.
        _sett_kontekst(rt, t)
        with pytest.raises(psycopg.errors.RaiseException):
            rt.execute("SELECT m29_avvikle_regel(%s,%s,%s,%s)",
                       (t, rid, I_DAG + dt.timedelta(days=365), "u-test"))
        rt.rollback()
        # …OG DATOEN STÅR SOM DEN BLE SATT, ikke som det andre kallet
        # ville hatt den.
        #
        # `gjelder_i_dag` er FORTSATT True, og det er riktig:
        # `gyldig_til = i dag` betyr gyldig UT i dag. En regel som
        # sluttet å gjelde i samme øyeblikk den ble avviklet, ville
        # etterlatt dagens scorer uten forklaring.
        _sett_kontekst(rt, t)
        rad = [r for r in rt.execute("SELECT * FROM m29_reglene(%s)",
                                     (t,)).fetchall() if r[0] == rid][0]
        assert rad[6] == I_DAG, rad[6]


@pg
def test_sveipen_bruker_den_gjeldende_fristen_ikke_den_lengste():
    """RETTELSEN ÉN DØR LENGER INNE SKAPTE FEILEN HER.

    Da `hendelseskrav` ble APPEND-ONLY, fikk hver tenant flere
    kravrader. Sveipen leste fristen med `max()` — altså DEN LENGSTE
    FRISTEN SOM NOEN GANG ER SATT.

    En tenant som strammet fristen fra 30 til 7 døgn ville fortsatt
    blitt målt mot 30, og funnet ville uteblitt i tre uker.

    Porten setter først en LANG frist, så en KORT, og krever at
    hendelsen blir et funn. Med `max()` er den grønn bare hvis
    rekkefølgen tilfeldigvis er den motsatte — derfor er den lange
    satt FØRST.

    MUTASJONEN SOM DREPER DENNE: sett `max(k.apen_hendelse_frist_dogn)`
    tilbake i `m29_sveip_hendelse`.
    """
    t = _tenantnavn("frist")
    with _to() as (rt, _mg):
        _krav(rt, t, frist=300)          # den lange, satt FØRST
        kv = _krav(rt, t, frist=1)       # den gjeldende
        rid = _regel(rt, t)
        hid, _ = _korreler(rt, t, rid, kv, n=3, naar=_naa(days=-10))
        # HENDELSEN ELDES HER, IKKE I DØRA.
        #
        # `oppdaget_ts` har `DEFAULT now()` og settes ALDRI av
        # kalleren: den sier når MODULEN korrelerte, ikke når signalene
        # falt. Det er riktig — men da må porten flytte den selv for å
        # måle en frist. Migrator gjør det; kjøretiden har ingen
        # tabellrettighet (SP-7).
        _sett_kontekst(_mg, t)
        _mg.execute(
            "UPDATE sikkerhetshendelse SET oppdaget_ts = %s"
            " WHERE tenant = %s AND hendelse_id = %s",
            (_naa(days=-10), t, hid))
        _mg.commit()
    sv = _sv()
    try:
        sv.execute("SELECT * FROM m29_sveip_hendelse(%s)", (10_000,))
        sv.commit()
    finally:
        sv.close()
    with _to() as (rt, _mg):
        _sett_kontekst(rt, t)
        funn = [f for f in rt.execute(
            "SELECT * FROM m29_hendelsesfunn(%s,%s)", (t, 100)).fetchall()
            if f[1] == "apen_hendelse_over_frist" and f[2] == str(hid)]
        assert funn, (
            "sveipen maalte mot den lengste fristen som noen gang sto,"
            " ikke mot den som gjelder")
