"""M-18 kunde-onboardingagent v1 (migrasjon 103) — ONBOARDINGREGISTERET.

Grensen `m18-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR koden
(§0-regelen), og hver invariant der har minst én port her.

DEN SKARPESTE PORTEN ER `steg_fullfort_for_forgjenger`. Et løp er en
SEKVENS, og uten regelen er «hvor står løpet» et spørsmål uten svar: tre
av fem steg gjort sier ingenting hvis det er de tre siste. Regelen bor i
VAKTEN og gjelder derfor også direkte DML.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import ast
import json
import os
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo)
from .test_m37 import _sett_kontekst

#: Sveiperollen. `m18_sveip_onboarding` er BARE hennes (kryss-tenant).
ONBOARDINGSVEIP_DSN = os.environ.get("DISPONIT_TEST_ONBOARDINGSVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "103_m18_onboardingregister.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "onboarding.js")
MODULFILER = (
    ROT / "platform" / "core" / "api" / "onboarding.py",
    ROT / "platform" / "drift" / "onboardingsveip.py",
    ROT / "platform" / "drift" / "kjor_onboardingsveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")

STEG = [
    {"navn": "Kontrakt", "beskrivelse": "Signert avtale",
     "frist_dogn": 2, "obligatorisk": True},
    {"navn": "Betaling", "beskrivelse": "Første faktura",
     "frist_dogn": 7, "obligatorisk": True},
    {"navn": "Velkomstmøte", "beskrivelse": "Gjennomgang",
     "frist_dogn": 10, "obligatorisk": False},
    {"navn": "Workspace", "beskrivelse": "Oppsett",
     "frist_dogn": 14, "obligatorisk": True},
]


# ---------------------------------------------------------------------------
# Riggen
# ---------------------------------------------------------------------------

def _rt():
    from db.pg import koble
    return koble(DSN)


def _sv():
    from db.pg import koble
    return koble(ONBOARDINGSVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    return f"t-m18-{merke}-{secrets.token_hex(4)}"


def _bruker(m, tenant, *, navn=None, aktiv=True, roller=("admin",)):
    profil = {"visningsnavn": navn} if navn else {}
    _sett_kontekst(m, tenant)
    bid = m.execute(
        "INSERT INTO brukeridentitet (issuer, sub, profil)"
        " VALUES ('https://m18.test', %s, %s::jsonb) RETURNING bruker_id",
        ("s18-" + secrets.token_hex(6), json.dumps(profil))).fetchone()[0]
    m.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller, aktiv)"
        " VALUES (%s,%s,%s,%s)", (tenant, bid, list(roller), aktiv))
    m.commit()
    return bid


def _mal(c, tenant, *, navn=None, steg=None, mid=None, aktor="u-test"):
    mid = mid or uuid.uuid4()
    navn = navn or ("Mal-" + secrets.token_hex(3))
    _sett_kontekst(c, tenant)
    c.execute("SELECT m18_registrer_mal(%s,%s,%s,%s)",
              (tenant, mid, navn, aktor))
    c.commit()
    _sett_kontekst(c, tenant)
    c.execute("SELECT m18_sett_malsteg(%s,%s,%s::jsonb,%s)",
              (tenant, mid, json.dumps(STEG if steg is None else steg),
               aktor))
    c.commit()
    return mid


def _lop(c, tenant, mal, eier, *, dager_siden=0, kunde="Nordvik AS",
         lid=None, aktor="u-test"):
    lid = lid or uuid.uuid4()
    _sett_kontekst(c, tenant)
    c.execute(
        "SELECT m18_start_lop(%s,%s,%s,%s,%s,current_date - %s::int,%s)",
        (tenant, lid, mal, kunde, eier, dager_siden, aktor))
    c.commit()
    return lid


def _fullfor(c, tenant, lop, nr, *, aktor="u-test"):
    _sett_kontekst(c, tenant)
    ut = c.execute("SELECT m18_fullfor_steg(%s,%s,%s,NULL,%s)",
                   (tenant, lop, nr, aktor)).fetchone()[0]
    c.commit()
    return ut


def _sveip(v, grense=500, stille=14):
    rad = v.execute("SELECT * FROM m18_sveip_onboarding(%s,%s)",
                    (grense, stille)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant, *, bare_apne=True):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT funntype, steg_nr, dogn_over_grense, apen"
        "  FROM onboardingfunn WHERE tenant=%s AND (%s IS FALSE OR apen)"
        " ORDER BY funntype, steg_nr", (tenant, bare_apne)).fetchall()
    m.rollback()
    return rader


# ---------------------------------------------------------------------------
# INVARIANT 1: modulen_provisjonerte — V1-DOMMEN
# ---------------------------------------------------------------------------

def test_invariant_modulen_provisjonerte_statisk():
    """Katalogteksten lover 0 minutter per ny kunde — registrering,
    betaling, workspace og oppsett maskinelt. v1 registrerer LØPET.

    Porten er en AST-analyse, ikke et delstrengsøk: en import inne i en
    funksjon ville sluppet unna et `startswith("import ")`, og det er
    nøyaktig formen en provisjoneringsvei ville hatt her.

    MUTASJONEN SOM DREPER DENNE: legg `import httpx` inne i en funksjon i
    `api/onboarding.py`.
    """
    forbudt = {"http", "httpx", "requests", "urllib", "aiohttp", "socket",
               "smtplib", "email", "ftplib", "telnetlib", "webbrowser",
               "ssl", "asyncio", "subprocess"}
    for fil in MODULFILER:
        tre = ast.parse(fil.read_text(encoding="utf-8"))
        for node in ast.walk(tre):
            navn = []
            if isinstance(node, ast.Import):
                navn = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                navn = [node.module or ""] if node.level == 0 else []
            for n in navn:
                assert n.split(".")[0] not in forbudt, \
                    f"{fil.name} importerer {n} — v1 provisjonerer ingenting"
                assert not n.endswith("ssrf"), \
                    f"{fil.name} importerer egressveien {n}"


def test_invariant_modulen_provisjonerte_har_ingen_utgaaende_vei():
    """ANDRE HALVDEL, målt på DATAMODELLEN og på rutene.

    En provisjoneringsvei kan ikke finnes uten et sted å provisjonere
    TIL. 103 har ingen kontotabell, ingen leverandørkobling og ingen
    adressekolonne; `app.py` registrerer nøyaktig åtte onboarding-ruter,
    og ingen av dem er en provisjonering.

    MUTASJONEN SOM DREPER DENNE: legg til en `.../provisjoner`-rute.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    kode = "\n".join(l for l in sql.splitlines()
                     if not l.lstrip().startswith("--"))
    for ord_ in ("provisjoner", "endepunkt", "webhook", "mottaker",
                 "api_nokkel", "passord"):
        assert ord_ not in kode.lower(), \
            f"103 bærer «{ord_}» — v1 provisjonerer ingenting"

    from api.app import RUTESCOPE
    mine = sorted(sti for _m, sti in RUTESCOPE
                  if sti.startswith("/v1/onboarding"))
    assert mine == [
        "/v1/onboarding",
        "/v1/onboarding/lop",
        "/v1/onboarding/lop/{lop_id:uuid}/avslutt",
        "/v1/onboarding/lop/{lop_id:uuid}/steg",
        "/v1/onboarding/lop/{lop_id:uuid}/steg/{steg_nr:int}/eier",
        "/v1/onboarding/lop/{lop_id:uuid}/steg/{steg_nr:int}/fullfor",
        "/v1/onboarding/mal",
        "/v1/onboarding/mal/{mal_id:uuid}/steg",
    ], mine


@pg
def test_invariant_modulen_provisjonerte_funksjonelt(migrator):
    """TREDJE HALVDEL, målt på VIRKELIGHETEN: en full sveip endrer ikke
    ett eneste radantall utenfor modulens egne fem tabeller.

    Dette er den eneste som ville fanget en provisjoneringsvei skrevet i
    ren SQL inne i en definer — der finnes verken en import eller et
    tabellnavn portene over leter etter.

    MUTASJONEN SOM DREPER DENNE: la `m18_sveip_onboarding` skrive én rad
    i en hvilken som helst annen tabell.
    """
    tenant = _tenantnavn("prov")
    eier = _bruker(migrator, tenant)
    c = _rt()
    try:
        mal = _mal(c, tenant)
        _lop(c, tenant, mal, eier, dager_siden=40)
    finally:
        c.close()
    egne = {"onboardingmal", "onboardingmalsteg", "onboardinglop",
            "lopsteg", "onboardingfunn"}
    tabeller = [r[0] for r in migrator.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
        " ORDER BY tablename").fetchall()]
    migrator.rollback()

    def tell():
        ut = {}
        for tab in tabeller:
            if tab in egne:
                continue
            try:
                ut[tab] = migrator.execute(
                    f'SELECT count(*) FROM public."{tab}"').fetchone()[0]
            except psycopg.errors.InsufficientPrivilege:
                migrator.rollback()
        migrator.rollback()
        return ut

    for_ = tell()
    assert len(for_) > 20, \
        f"porten teller bare {len(for_)} tabeller — den måler ingenting"
    with _sv() as v:
        _sveip(v)
    etter = tell()
    assert for_ == etter, \
        ("sveipen endret radantall utenfor registeret: "
         + str({k: (for_[k], etter[k]) for k in for_
                if for_[k] != etter[k]}))


# ---------------------------------------------------------------------------
# INVARIANT 2: tilgangstilstand_speilet_fra_m12
# ---------------------------------------------------------------------------

def test_invariant_tilgangstilstand_speilet_fra_m12():
    """M-12 (097) eier TILGANGENE. Et onboardingsteg kan NEVNE en tilgang
    i sin egen tekst; det finnes ingen kolonne, ingen fremmednøkkel og
    ingen dør her som sier noe om hvem som HAR den.

    To registre som begge påstår å vite hvem som har hva, kan aldri
    holdes i takt — og da er det andre registeret verre enn ingenting.

    MUTASJONEN SOM DREPER DENNE: legg en `tilgang_id`-kolonne på
    `lopsteg`, eller la en dør lese `tilgangsregister`.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    kode = "\n".join(l for l in sql.splitlines()
                     if not l.lstrip().startswith("--"))
    # M-12s egne tabeller og dører, hentet fra 097. Ingen av dem skal
    # nevnes i 103 i det hele tatt.
    for navn in ("tilgangsregister", "tilgangsfunn", "m12_",
                 "tilgang_id", "hjemmel"):
        assert navn not in kode.lower(), \
            f"103 nevner «{navn}» — registeret speiler M-12"
    for fil in MODULFILER:
        tekst = fil.read_text(encoding="utf-8").lower()
        uten = "\n".join(l for l in tekst.splitlines()
                         if not l.lstrip().startswith("#"))
        assert "m12_" not in uten, f"{fil.name} kaller en M-12-dør"


# ---------------------------------------------------------------------------
# INVARIANT 3: steg_fullfort_for_forgjenger — DEN SKARPESTE
# ---------------------------------------------------------------------------

@pg
def test_invariant_steg_fullfort_for_forgjenger(migrator):
    """ET LØP ER EN SEKVENS. Et obligatorisk steg kan ikke stå som
    fullført mens et LAVERE nummerert obligatorisk steg ikke er det.

    VALGFRIE STEG ER UNNTATT I BEGGE RETNINGER: de kan gjøres når som
    helst, og de blokkerer ingen. Det er hele grunnen til at
    `obligatorisk` finnes som eget felt og ikke som en konvensjon om
    nummerering.

    Målt gjennom døren OG på direkte DML — vakten er den bindende.

    MUTASJONEN SOM DREPER DENNE: fjern sekvensblokken fra
    `m18_steg_vakt`.
    """
    tenant = _tenantnavn("sekvens")
    eier = _bruker(migrator, tenant)
    c = _rt()
    try:
        mal = _mal(c, tenant)
        lop = _lop(c, tenant, mal, eier)
        # Steg 2 er obligatorisk og steg 1 er ikke gjort.
        with pytest.raises(psycopg.Error) as ei:
            _fullfor(c, tenant, lop, 2)
        assert "obligatorisk og ikke fullført" in str(ei.value)
        c.rollback()
        # Steg 3 er VALGFRITT og går gjennom uansett.
        assert _fullfor(c, tenant, lop, 3) is True
        # …og etter steg 1 går steg 2.
        assert _fullfor(c, tenant, lop, 1) is True
        assert _fullfor(c, tenant, lop, 2) is True
        # Samme steg igjen er et STILLE JA.
        assert _fullfor(c, tenant, lop, 2) is False
    finally:
        c.close()
    # DIREKTE DML, forbi døren: steg 4 mens... alle lavere er gjort nå,
    # så testen bygger et nytt løp der de ikke er det.
    c = _rt()
    try:
        lop2 = _lop(c, tenant, mal, eier, kunde="Andre AS")
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_onboarding_eier")
    with pytest.raises(psycopg.Error) as ei:
        migrator.execute(
            "UPDATE lopsteg SET fullfort_ts=now(), fullfort_av='test'"
            " WHERE tenant=%s AND lop_id=%s AND steg_nr=4",
            (tenant, lop2))
    assert "obligatorisk og ikke fullført" in str(ei.value)
    migrator.rollback()


@pg
def test_fullfort_lop_krever_at_de_obligatoriske_stegene_er_gjort(migrator):
    """«Fullført» er ikke et ord man kan klikke. Uten kravet ville
    registeret hatt en tilstand det ikke kan vise noe bak — og hele
    poenget med å måle et løp er at «vi er ferdige» skal bety noe.

    `avbrutt` har IKKE kravet, men koster en begrunnelse: «vi ga opp»
    uten hvorfor er den ene opplysningen ingen kan lære noe av senere.

    MUTASJONEN SOM DREPER DENNE: fjern `v_ufullforte`-blokken fra
    `m18_lop_vakt`.
    """
    tenant = _tenantnavn("fullfort")
    eier = _bruker(migrator, tenant)
    c = _rt()
    try:
        mal = _mal(c, tenant)
        lop = _lop(c, tenant, mal, eier)
        _fullfor(c, tenant, lop, 1)
        _fullfor(c, tenant, lop, 2)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m18_avslutt_lop(%s,%s,'fullfort',NULL,'u')",
                      (tenant, lop))
        assert "ufullførte" in str(ei.value)
        c.rollback()
        # Et AVBRUTT løp uten begrunnelse avvises av døren.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m18_avslutt_lop(%s,%s,'avbrutt',NULL,'u')",
                      (tenant, lop))
        assert "begrunnelse" in str(ei.value)
        c.rollback()
        # …og med begrunnelse går det.
        _sett_kontekst(c, tenant)
        assert c.execute(
            "SELECT m18_avslutt_lop(%s,%s,'avbrutt','kunden trakk seg','u')",
            (tenant, lop)).fetchone()[0] is True
        c.commit()
        # Et avsluttet løp tar ikke imot flere steg, og gjenåpnes ikke.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m18_fullfor_steg(%s,%s,4,NULL,'u')",
                      (tenant, lop))
        assert "avsluttet løp" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_onboarding_eier")
    with pytest.raises(psycopg.Error) as ei:
        migrator.execute(
            "UPDATE onboardinglop SET status='paagaar', avsluttet_ts=NULL,"
            " avsluttet_av=NULL, avbrutt_begrunnelse=NULL"
            " WHERE tenant=%s AND lop_id=%s", (tenant, lop))
    assert "gjenåpnes ikke" in str(ei.value)
    migrator.rollback()


@pg
def test_snapshotet_er_frosset_og_malen_laases_av_paagaende_lop(migrator):
    """DOM 2: stegene snapshottes fra malen ved start, og snapshotet er
    frosset. Et løp som endret seg fordi noen redigerte malen etterpå,
    ville gjort hver eldre statusvurdering til en påstand om noe annet
    enn det som faktisk gjaldt.

    MALEN LÅSES i tillegg mens den har pågående løp — ellers ville
    versjonen løpt fra løpene som peker på den.

    MUTASJONEN SOM DREPER DENNE: fjern `m18_malsteg_vakt`, eller
    snapshot-frysingen fra `m18_steg_vakt`.
    """
    tenant = _tenantnavn("snapshot")
    eier = _bruker(migrator, tenant)
    c = _rt()
    try:
        mal = _mal(c, tenant)
        lop = _lop(c, tenant, mal, eier)
        # Malen har nå et pågående løp: den kan ikke endres.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m18_sett_malsteg(%s,%s,%s::jsonb,'u')",
                      (tenant, mal, json.dumps(STEG[:2])))
        assert "pågående løp" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    for sql in (
        "UPDATE lopsteg SET obligatorisk=false WHERE tenant=%s"
        " AND lop_id=%s AND steg_nr=1",
        "UPDATE lopsteg SET frist_dogn=999 WHERE tenant=%s"
        " AND lop_id=%s AND steg_nr=1",
        "UPDATE lopsteg SET navn='endret' WHERE tenant=%s"
        " AND lop_id=%s AND steg_nr=1",
        "DELETE FROM lopsteg WHERE tenant=%s AND lop_id=%s AND steg_nr=1",
    ):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_onboarding_eier")
        with pytest.raises(psycopg.Error), migrator.transaction():
            migrator.execute(sql, (tenant, lop))
        migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 4: steg_uten_eier
# ---------------------------------------------------------------------------

@pg
def test_invariant_steg_uten_eier(migrator):
    """Et steg uten eier er urepresenterbart (NOT NULL + FK), og eieren
    må være AKTIVT MEDLEM av tenanten — FK-en alene sier bare at
    bruker-id-en finnes et sted i plattformen, og et løp eid av en
    fremmed tenants bruker er et løp ingen her gjør.

    MUTASJONEN SOM DREPER DENNE: fjern medlemskapssjekken fra
    `m18_start_lop`.
    """
    tenant = _tenantnavn("eier")
    annen = _tenantnavn("eier-annen")
    eier = _bruker(migrator, tenant)
    fremmed = _bruker(migrator, annen)
    sluttet = _bruker(migrator, tenant, aktiv=False)
    c = _rt()
    try:
        mal = _mal(c, tenant)
        for kandidat, hva in ((fremmed, "fremmed tenant"),
                              (sluttet, "sluttet")):
            _sett_kontekst(c, tenant)
            with pytest.raises(psycopg.Error) as ei:
                c.execute(
                    "SELECT m18_start_lop(%s,%s,%s,'X',%s,current_date,'u')",
                    (tenant, uuid.uuid4(), mal, kandidat))
            assert "aktivt medlem" in str(ei.value), hva
            c.rollback()
        lop = _lop(c, tenant, mal, eier)
        # …og stegeieren må også være aktivt medlem.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m18_sett_stegeier(%s,%s,1,%s,'u')",
                      (tenant, lop, sluttet))
        assert "aktivt medlem" in str(ei.value)
        c.rollback()
    finally:
        c.close()
    # NOT NULL i katalogen.
    rad = migrator.execute(
        "SELECT is_nullable FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name='lopsteg'"
        "   AND column_name='eier_bruker_id'").fetchone()
    migrator.rollback()
    assert rad[0] == "NO"


# ---------------------------------------------------------------------------
# INVARIANT 5: stoppet_lop_uten_funn
# ---------------------------------------------------------------------------

@pg
def test_invariant_stoppet_lop_uten_funn(migrator):
    """Et stoppet løp er et FUNN, ikke en rad som stille blir gammel.

    STILLE MÅLES FRA SISTE FULLFØRING, ikke fra starten. Uten det ville
    et løp der noen jobbet i går blitt et funn fordi det ble startet for
    en måned siden — og funnlisten ville fylt seg med løp som går som de
    skal.

    IDEMPOTENSEN måles i samme test: sveip nummer to gir NULL nye rader.

    MUTASJONEN SOM DREPER DENNE: mål stille fra `l.startet` i stedet for
    fra siste fullføring.
    """
    tenant = _tenantnavn("stoppet")
    eier = _bruker(migrator, tenant)
    c = _rt()
    try:
        mal = _mal(c, tenant)
        stille = _lop(c, tenant, mal, eier, dager_siden=40,
                      kunde="Stille AS")
        # Aktivt løp: like gammelt, men noe ble gjort i dag.
        aktivt = _lop(c, tenant, mal, eier, dager_siden=40,
                      kunde="Aktiv AS")
        _fullfor(c, tenant, aktivt, 1)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    funn = _funn(migrator, tenant)
    typer = {r[0] for r in funn}
    assert "stoppet_lop" in typer
    # …og det AKTIVE løpet har intet stoppet-funn.
    _sett_kontekst(migrator, tenant)
    stoppede = {r[0] for r in migrator.execute(
        "SELECT lop_id FROM onboardingfunn WHERE tenant=%s"
        " AND funntype='stoppet_lop' AND apen", (tenant,)).fetchall()}
    migrator.rollback()
    assert stille in stoppede
    assert aktivt not in stoppede, \
        "et løp der noen jobbet i dag ble merket som stoppet"

    forst = {(r[0], r[1]) for r in _funn(migrator, tenant)}
    with _sv() as v:
        rad = _sveip(v)
    assert rad[1] == 0, "sveip nummer to skrev nye rader"
    assert {(r[0], r[1]) for r in _funn(migrator, tenant)} == forst


@pg
def test_forsinket_steg_og_foreldreloest_lop_blir_funn(migrator):
    """De to andre funntypene. `lop_uten_aktiv_eier` har INGEN
    aldersgrense: et foreldreløst løp blir ikke mindre foreldreløst av å
    vente, og uten funnet blir det liggende til kunden ringer.
    """
    tenant = _tenantnavn("forsinket")
    eier = _bruker(migrator, tenant)
    c = _rt()
    try:
        mal = _mal(c, tenant)
        lop = _lop(c, tenant, mal, eier, dager_siden=10)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    # ETT FUNN PER STEG, ikke ett per løp. Løpet er 10 døgn gammelt, og
    # steg 1 (frist 2) og steg 2 (frist 7) er begge forbi sin egen —
    # steg 3 forfaller nøyaktig i dag (10) og er derfor IKKE over, og
    # steg 4 (14) har fortsatt tid. At grensen er STRENGT større er
    # dommen: en frist er ikke brutt den dagen den forfaller.
    forsinket = {(r[1], r[2]) for r in _funn(migrator, tenant)
                 if r[0] == "steg_over_frist"}
    assert forsinket == {(1, 8), (2, 3)}, forsinket
    assert "lop_uten_aktiv_eier" not in {r[0] for r in _funn(migrator,
                                                             tenant)}

    _sett_kontekst(migrator, tenant)
    migrator.execute(
        "UPDATE brukermedlemskap SET aktiv=false WHERE tenant=%s"
        " AND bruker_id=%s", (tenant, eier))
    migrator.commit()
    with _sv() as v:
        _sveip(v)
    assert "lop_uten_aktiv_eier" in {r[0] for r in _funn(migrator, tenant)}


@pg
def test_funnet_lukkes_naar_lopet_avsluttes(migrator):
    """Et funn som ikke lenger gjelder lukkes — og RADEN BESTÅR."""
    tenant = _tenantnavn("lukkfunn")
    eier = _bruker(migrator, tenant)
    c = _rt()
    try:
        mal = _mal(c, tenant)
        lop = _lop(c, tenant, mal, eier, dager_siden=40)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert len(_funn(migrator, tenant)) >= 1
    c = _rt()
    try:
        _sett_kontekst(c, tenant)
        c.execute("SELECT m18_avslutt_lop(%s,%s,'avbrutt','stoppet','u')",
                  (tenant, lop))
        c.commit()
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert _funn(migrator, tenant) == []
    lukkede = _funn(migrator, tenant, bare_apne=False)
    assert lukkede and all(r[3] is False for r in lukkede)


# ---------------------------------------------------------------------------
# INVARIANT 6: tenantlekkasje_i_onboardingregister
# ---------------------------------------------------------------------------

@pg
def test_invariant_tenantlekkasje_direkte(migrator):
    """RLS ENABLE+FORCE på alle fem tabellene, og SP-1 i hver dør."""
    a = _tenantnavn("lekk-a")
    b = _tenantnavn("lekk-b")
    ea = _bruker(migrator, a)
    eb = _bruker(migrator, b)
    c = _rt()
    try:
        _lop(c, a, _mal(c, a), ea)
        _lop(c, b, _mal(c, b), eb)
        _sett_kontekst(c, b)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT * FROM m18_onboardingstatus(%s)", (a,))
        assert "tenantkontekst" in str(ei.value)
        c.rollback()
        _sett_kontekst(c, a)
        assert c.execute("SELECT * FROM m18_onboardingstatus(%s)",
                         (a,)).fetchone()[0] == 1
        c.rollback()
    finally:
        c.close()
    for tab in ("onboardingmal", "onboardingmalsteg", "onboardinglop",
                "lopsteg", "onboardingfunn"):
        rad = migrator.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
            " WHERE oid = %s::regclass", (f"public.{tab}",)).fetchone()
        assert rad == (True, True), f"{tab}: RLS ikke ENABLE+FORCE"
    migrator.rollback()


@pg
def test_invariant_tenantlekkasje_over_api(migrator, klient):
    """…og over HTTP: økten hos A får aldri se Bs løp."""
    fremmed = _tenantnavn("fremmed")
    ef = _bruker(migrator, fremmed)
    ee = _bruker(migrator, TENANT)
    c = _rt()
    try:
        _lop(c, TENANT, _mal(c, TENANT), ee, kunde="EGEN-KUNDE")
        _lop(c, fremmed, _mal(c, fremmed), ef, kunde="FREMMED-KUNDE")
    finally:
        c.close()
    cookie, _csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/onboarding", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    kropp = json.dumps(r.json())
    assert "EGEN-KUNDE" in kropp
    assert "FREMMED-KUNDE" not in kropp


# ---------------------------------------------------------------------------
# INVARIANT 7: ui_axe_alvorlige_brudd
# ---------------------------------------------------------------------------

def test_invariant_ui_axe_bor_i_js_suiten():
    """Axe-porten kjøres i `platform/core/ui` (node --test), ikke her."""
    fil = (ROT / "platform" / "core" / "ui" / "test"
           / "onboarding.test.js")
    assert fil.exists(), "onboarding.test.js mangler"
    assert "axe" in fil.read_text(encoding="utf-8"), \
        "UI-suiten kjører ingen axe-port for flaten"


# ---------------------------------------------------------------------------
# Sveipearbeideren
# ---------------------------------------------------------------------------

@pg
def test_sveipen_nekter_kontekst(migrator):
    with _sv() as v:
        v.execute("SELECT set_config('disponit.tenant','t',false)")
        with pytest.raises(psycopg.Error) as ei:
            v.execute("SELECT * FROM m18_sveip_onboarding(500,14)")
        assert "KRYSS-TENANT" in str(ei.value)
        v.rollback()


@pg
def test_sveiperollen_har_ingen_tabellrettigheter(migrator):
    if not ONBOARDINGSVEIP_DSN:
        pytest.skip("DISPONIT_TEST_ONBOARDINGSVEIP_DSN ikke satt")
    rader = migrator.execute(
        "SELECT table_name, privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_onboardingsveip'").fetchall()
    migrator.rollback()
    assert rader == [], rader


def test_overlappende_sveip_hopper_over_og_rorer_ikke_feiltelleren():
    from drift import onboardingsveip

    class Falsk:
        def execute(self, sql, *a):
            class R:
                @staticmethod
                def fetchone():
                    return (False,)
            return R()

        def commit(self):
            pass

    r = onboardingsveip.kjor(Falsk(), tidligere_feil=1)
    assert r.hoppet_over is True
    assert r.feilet is False and r.alarm_utlost is False


def test_sveipen_nekter_aa_starte_uten_egen_dsn(tmp_path, monkeypatch):
    from drift import kjor_onboardingsveip
    monkeypatch.delenv("DISPONIT_ONBOARDINGSVEIP_URL", raising=False)
    monkeypatch.setenv("DISPONIT_ONBOARDINGSVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.setattr("db.hemmeligheter.last_credentials", lambda: None)
    assert kjor_onboardingsveip.main() == 2


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
        " ('https://m18h.test', %s) RETURNING bruker_id",
        ("s18h-" + secrets.token_hex(6),)).fetchone()[0]
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
@dekker("onboarding_ulovlig_tilstand")
def test_http_sekvensbruddet_er_409_og_ikke_400(migrator, klient):
    """FEILVEIEN `onboarding_ulovlig_tilstand`, ende til ende.

    Sekvensbruddet kommer fra VAKTEN som `insufficient_privilege`, og
    `_doerfeil` gjør det til 409: det er en TILSTAND som sier nei, ikke
    en feilformet kropp. Et 400 her ville sagt at brukeren skrev feil,
    når sannheten er at et tidligere steg mangler.

    Dette er også testen `test_api_porter.test_hver_feilvei_har_en_test`
    krever for den nye feilveien.
    """
    eier = _bruker(migrator, TENANT)
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _hpost(klient, cookie, csrf, "/v1/onboarding/mal",
               {"navn": "HTTP-mal-" + secrets.token_hex(3)})
    assert r.status_code in (200, 201), r.text
    mid = r.json()["mal_id"]
    r = _hpost(klient, cookie, csrf,
               f"/v1/onboarding/mal/{mid}/steg", {"steg": STEG})
    assert r.status_code in (200, 201), r.text
    r = _hpost(klient, cookie, csrf, "/v1/onboarding/lop",
               {"mal_id": mid, "kunde_ref": "HTTP AS",
                "eier_bruker_id": eier, "startet": "2026-09-01"})
    assert r.status_code in (200, 201), r.text
    lid = r.json()["lop_id"]
    # Steg 2 før steg 1: TILSTANDEN sier nei.
    r = _hpost(klient, cookie, csrf,
               f"/v1/onboarding/lop/{lid}/steg/2/fullfor", {})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "onboarding_ulovlig_tilstand"
    # En ukjent status er 400: KROPPEN er feil.
    r = _hpost(klient, cookie, csrf,
               f"/v1/onboarding/lop/{lid}/avslutt", {"status": "oppdiktet"})
    assert r.status_code == 400, r.text
    assert r.json()["feil"] == "request_feilformet"
    # …og steg 1 går.
    r = _hpost(klient, cookie, csrf,
               f"/v1/onboarding/lop/{lid}/steg/1/fullfor", {})
    assert r.status_code in (200, 201), r.text


@pg
def test_http_maler_og_lop_er_idempotente(migrator, klient):
    """SP-2: samme Idempotency-Key + samme innhold gir SAMME id. Et
    dobbelt startet løp ville gitt to sett steg for den samme kunden, og
    «hvor står vi» to svar.
    """
    eier = _bruker(migrator, TENANT)
    cookie, csrf = _browserokt(migrator, ["admin"])
    nokkel = secrets.token_urlsafe(24)
    kropp = {"navn": "Idem-mal-" + secrets.token_hex(3)}
    a = _hpost(klient, cookie, csrf, "/v1/onboarding/mal", kropp,
               idem=nokkel)
    b = _hpost(klient, cookie, csrf, "/v1/onboarding/mal", kropp,
               idem=nokkel)
    assert a.status_code in (200, 201) and b.status_code in (200, 201)
    assert a.json()["mal_id"] == b.json()["mal_id"]
    assert a.json()["ny"] is True and b.json()["ny"] is False


# ---------------------------------------------------------------------------
# §0: grensen ble registrert FØR koden
# ---------------------------------------------------------------------------

def test_grensen_ble_registrert_for_koden():
    """Grensen `m18-v1` sto i `KRAVGRENSER` fra klynge 3-fundamentet, før
    en eneste linje av modulen fantes (§0-regelen)."""
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m18-v1"]
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["punktbinding"] == {}
    egen = Path(__file__).read_text(encoding="utf-8")
    for inv in g["invarianter"]:
        assert inv in egen, f"invarianten {inv} har ingen port"
