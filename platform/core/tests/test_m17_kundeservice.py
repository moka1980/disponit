"""M-17 kundeserviceagent v1, PR-A (migrasjon 102) — HENVENDELSESREGISTERET.

Grensen `m17-v1` i `manifestskjema.KRAVGRENSER` ble registrert FØR koden
(§0-regelen), og hver invariant der har minst én port her — MED ÉN
ÆRLIG UNNTAKELSE, som er skrevet ned i stedet for skjult:

  `modellinput_umaskert_felt` HAR NULL FORSØK I PR-A. Det finnes ingen
  modellinput ennå — klassifiseringen er menneskelig, og modellarmen er
  PR-B. Null brudd uten forsøk er RØDT i parformen, ikke grønt, og
  porten under (`test_grensen_ble_registrert_for_koden`) sier det med
  rene ord i stedet for å late som invarianten er oppfylt.

DEN SKARPESTE PORTEN ER `henvendelse_tapt_ved_feil`. En tapt henvendelse
er verre enn en uklassifisert, og den er usynlig: ingen vet at noen
spurte. Formen er strukturell — `henvendelse` er append-only mot både
UPDATE og DELETE på innholdet, og veien til M-37s kø peker på raden i
stedet for å kopiere den.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
from __future__ import annotations

import ast
import hashlib
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

#: Sveiperollen. `m17_sveip_henvendelser` er BARE hennes (kryss-tenant,
#: 038-reaperens snitt).
HENVENDELSESVEIP_DSN = os.environ.get("DISPONIT_TEST_HENVENDELSESVEIP_DSN")

ROT = Path(__file__).resolve().parents[3]
MIGRASJON = (ROT / "platform" / "core" / "db" / "migrations"
             / "102_m17_henvendelsesregister.sql")
FLATE = (ROT / "platform" / "core" / "ui" / "static" / "js" / "flater"
         / "kundeservice.js")
#: Modulens EGNE Python-filer. Fraværet av en sendevei skal kunne måles
#: på nøyaktig disse tre.
MODULFILER = (
    ROT / "platform" / "core" / "api" / "kundeservice.py",
    ROT / "platform" / "drift" / "henvendelsessveip.py",
    ROT / "platform" / "drift" / "kjor_henvendelsessveip.py",
)

pg = pytest.mark.skipif(
    not (DSN and MIGRATOR_DSN),
    reason="DISPONIT_TEST_DSN/DISPONIT_TEST_MIGRATOR_DSN ikke satt")


# ---------------------------------------------------------------------------
# Riggen
# ---------------------------------------------------------------------------

def _rt():
    from db.pg import koble
    return koble(DSN)


def _sv():
    from db.pg import koble
    return koble(HENVENDELSESVEIP_DSN or MIGRATOR_DSN)


def _tenantnavn(merke: str) -> str:
    """Egen tenant per test. Sveipen er kryss-tenant og ser HELE basen."""
    return f"t-m17-{merke}-{secrets.token_hex(4)}"


def _nokkel(c, tenant):
    from db import kryptering
    _sett_kontekst(c, tenant)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(c, tenant)
    c.commit()
    return key_id, dek


def _kr(dek, key_id, tenant, tekst, aad=None):
    from db import kryptering
    return kryptering.krypter(dek, {"t": tekst}, tenant, key_id,
                              ekstra_aad=aad)


def _ta_imot(c, tenant, key_id, dek, *, ref=None, dager_siden=0,
             kanal="epost", emne="Emne", tekst="Kundens tekst",
             avsender="kunde@eksempel.no", hid=None, aktor="u-test"):
    hid = hid or uuid.uuid4()
    ref = ref or ("MSG-" + secrets.token_hex(4))
    e, en = _kr(dek, key_id, tenant, emne)
    k, kn = _kr(dek, key_id, tenant, tekst)
    _sett_kontekst(c, tenant)
    rad = c.execute(
        "SELECT * FROM m17_ta_imot(%s,%s,%s,%s,"
        "       now() - make_interval(days => %s),%s,%s,%s,%s,%s,%s,%s)",
        (tenant, hid, kanal, ref, dager_siden,
         hashlib.sha256(avsender.strip().lower().encode()).hexdigest(),
         e, en, k, kn, key_id, aktor)).fetchone()
    c.commit()
    return rad[1]


def _klassifiser(c, tenant, hid, *, prioritet="normal", tema="annet",
                 handlingstype="svar_kreves", aktor="u-test"):
    _sett_kontekst(c, tenant)
    ny = c.execute(
        "SELECT m17_klassifiser(%s,%s,%s,%s,%s,'menneske',NULL,%s)",
        (tenant, hid, prioritet, tema, handlingstype, aktor)).fetchone()[0]
    c.commit()
    return ny


def _til_koe(c, tenant, hid, key_id, dek, *, begrunnelse="uavklart",
             aktor="u-test"):
    ct, nonce = _kr(dek, key_id, tenant,
                    json.dumps({"h": str(hid), "b": begrunnelse}),
                    aad=b"m17:unntakskoe")
    _sett_kontekst(c, tenant)
    sak = c.execute("SELECT m17_til_unntakskoe(%s,%s,%s,%s,%s,%s,%s)",
                    (tenant, hid, begrunnelse, ct, nonce, key_id,
                     aktor)).fetchone()[0]
    c.commit()
    return sak


def _utkast(c, tenant, hid, key_id, dek, *, tekst="Et svarutkast",
            uid=None, aktor="u-test"):
    uid = uid or uuid.uuid4()
    ct, nonce = _kr(dek, key_id, tenant, tekst)
    _sett_kontekst(c, tenant)
    c.execute("SELECT m17_lagre_utkast(%s,%s,%s,%s,%s,%s,%s,'menneske',"
              "                        NULL,%s)",
              (tenant, uid, hid, ct, nonce, key_id, [], aktor))
    c.commit()
    return uid


def _sveip(v, grense=500, ukl=2, ube=5):
    rad = v.execute("SELECT * FROM m17_sveip_henvendelser(%s,%s,%s)",
                    (grense, ukl, ube)).fetchone()
    v.commit()
    return rad


def _funn(m, tenant, *, bare_apne=True):
    _sett_kontekst(m, tenant)
    rader = m.execute(
        "SELECT funntype, henvendelse_id, dogn_over_grense, apen"
        "  FROM henvendelsesfunn WHERE tenant=%s AND (%s IS FALSE OR apen)"
        " ORDER BY funntype", (tenant, bare_apne)).fetchall()
    m.rollback()
    return rader


# ---------------------------------------------------------------------------
# INVARIANT 1: modulen_sendte_svar — V1-DOMMEN
# ---------------------------------------------------------------------------

def test_invariant_modulen_sendte_svar_statisk():
    """Katalogteksten lover at repeterende henvendelser løses
    AUTOMATISK. v1 lagrer et utkast, og et menneske sender.

    Porten er en AST-analyse, ikke et delstrengsøk: en import inne i en
    funksjon ville sluppet unna et `startswith("import ")`, og det er
    nøyaktig formen en sendevei ville hatt her — modulens egne importer
    ER late.

    MUTASJONEN SOM DREPER DENNE: legg `import smtplib` inne i en funksjon
    i `api/kundeservice.py`.
    """
    forbudt = {"smtplib", "email", "http", "httpx", "requests", "urllib",
               "aiohttp", "socket", "ftplib", "telnetlib", "webbrowser",
               "ssl", "asyncio"}
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
                    f"{fil.name} importerer {n} — v1 sender ingenting"
                assert not n.endswith("ssrf"), \
                    f"{fil.name} importerer egressveien {n}"


def test_invariant_modulen_sendte_svar_har_ingen_sendestatus():
    """ANDRE HALVDEL, målt på DATAMODELLEN og på rutene.

    En sendevei kan ikke finnes uten en tilstand som sier at noe ble
    sendt. `svarutkast.status` har nøyaktig tre verdier, og ingen av dem
    heter `sendt`; `app.py` registrerer ni kundeservice-ruter, og ingen
    av dem er en sending.

    Dette er halvdelen som ville overlevd at noen skrev sin egen
    socket-kode uten å importere noe: uten en tilstand å skrive ned,
    finnes det ingenting å hevde ble sendt.

    MUTASJONEN SOM DREPER DENNE: legg `sendt` i status-CHECKen, eller en
    tiende rute som heter `.../send`.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    kode = "\n".join(l for l in sql.splitlines()
                     if not l.lstrip().startswith("--"))
    for ord_ in ("'sendt'", "smtp", "mottakeradresse", "utgaaende_ko",
                 "webhook"):
        assert ord_ not in kode.lower(), \
            f"102 bærer «{ord_}» — v1 sender ingenting"
    assert "'brukt_manuelt'" in kode, \
        "utkastets eneste positive dom skal si at et MENNESKE brukte det"

    from api.app import RUTESCOPE
    mine = sorted(sti for _m, sti in RUTESCOPE
                  if sti.startswith("/v1/kundeservice"))
    assert mine == [
        "/v1/kundeservice",
        "/v1/kundeservice/henvendelse",
        "/v1/kundeservice/henvendelse/{henvendelse_id:uuid}/innhold",
        "/v1/kundeservice/henvendelse/{henvendelse_id:uuid}/klassifiser",
        "/v1/kundeservice/henvendelse/{henvendelse_id:uuid}/lukk",
        "/v1/kundeservice/henvendelse/{henvendelse_id:uuid}/unntakskoe",
        "/v1/kundeservice/henvendelse/{henvendelse_id:uuid}/utkast",
        "/v1/kundeservice/henvendelse/{henvendelse_id:uuid}/utkast/ny",
        "/v1/kundeservice/utkast/{utkast_id:uuid}/dom",
    ], mine


@pg
def test_invariant_modulen_sendte_svar_lukking_krever_menneskets_spor(
        migrator):
    """TREDJE HALVDEL, målt på VIRKELIGHETEN — og den er dommens kjerne:
    en henvendelse kan bare lukkes som «besvart» hvis et MENNESKE har
    merket et utkast som brukt.

    Uten kravet ville «besvart» vært et ord noen kunne klikke, og
    registeret ville hatt en tilstand det ikke kan vise noe bak. MED
    kravet er sporet etter menneskets handling forutsetningen for
    påstanden — og det er nøyaktig grensen mellom «vi svarte» og «vi kan
    vise at vi svarte».

    MUTASJONEN SOM DREPER DENNE: fjern `brukt_manuelt`-kravet fra
    `m17_lukk` ELLER fra `m17_henvendelse_vakt`. Porten måler begge.
    """
    tenant = _tenantnavn("lukk")
    c = _rt()
    try:
        key_id, dek = _nokkel(c, tenant)
        hid = _ta_imot(c, tenant, key_id, dek)
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m17_lukk(%s,%s,'besvart','u-test')",
                      (tenant, hid))
        assert "brukt_manuelt" in str(ei.value)
        c.rollback()
        # `ikke_aktuell` har ikke kravet — den sier nettopp at det ikke
        # skulle svares.
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT m17_lukk(%s,%s,'ikke_aktuell','u-test')",
                         (tenant, hid)).fetchone()[0] is True
        c.commit()
    finally:
        c.close()
    # …og VAKTEN sier det samme mot direkte DML, som er veien forbi døren.
    tenant2 = _tenantnavn("lukk2")
    c = _rt()
    try:
        key_id, dek = _nokkel(c, tenant2)
        h2 = _ta_imot(c, tenant2, key_id, dek)
    finally:
        c.close()
    _sett_kontekst(migrator, tenant2)
    migrator.execute("SET LOCAL ROLE disponit_kundeservice_eier")
    with pytest.raises(psycopg.Error) as ei:
        migrator.execute(
            "UPDATE henvendelse SET lukket_ts=now(), lukket_av='test',"
            " lukket_utfall='besvart' WHERE tenant=%s AND henvendelse_id=%s",
            (tenant2, h2))
    assert "brukt_manuelt" in str(ei.value)
    migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 2: henvendelse_tapt_ved_feil — DEN SKARPESTE
# ---------------------------------------------------------------------------

@pg
def test_invariant_henvendelse_tapt_ved_feil(migrator):
    """En henvendelse forsvinner ALDRI. Målt på direkte DML som
    tabellens eier — veien som ville omgått dørene.

    DELETE er avvist, og innholdet er append-only. Ingen kodevei kan
    gjøre det usant at noen spurte.

    MUTASJONEN SOM DREPER DENNE: fjern `m17_henvendelse_vakt`.
    """
    tenant = _tenantnavn("tapt")
    c = _rt()
    try:
        key_id, dek = _nokkel(c, tenant)
        hid = _ta_imot(c, tenant, key_id, dek)
    finally:
        c.close()
    for sql in (
        "DELETE FROM henvendelse WHERE tenant=%s AND henvendelse_id=%s",
        "UPDATE henvendelse SET kropp_kryptert='\\x00'::bytea"
        " WHERE tenant=%s AND henvendelse_id=%s",
        "UPDATE henvendelse SET ekstern_ref='endret'"
        " WHERE tenant=%s AND henvendelse_id=%s",
        "UPDATE henvendelse SET avsender_hash=repeat('a',64)"
        " WHERE tenant=%s AND henvendelse_id=%s",
        "UPDATE henvendelse SET mottatt=now()"
        " WHERE tenant=%s AND henvendelse_id=%s",
    ):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_kundeservice_eier")
        with pytest.raises(psycopg.Error), migrator.transaction():
            migrator.execute(sql, (tenant, hid))
        migrator.rollback()


@pg
def test_koveien_peker_paa_raden_og_kopierer_den_ikke(migrator):
    """DET UAVKLARTE GÅR TIL M-37s KØ — og køen bærer en HENVISNING, ikke
    innholdet. En kopi av kundens tekst i køens payload ville vært det
    samme persondatasettet i to lagre med hver sin retensjon, altså
    nøyaktig den formen M-4s retensjonsregnskap finnes for å hindre.

    KØEN ER IDEMPOTENT: to klikk på «kan ikke avgjøres» gir én sak.

    MUTASJONEN SOM DREPER DENNE: legg henvendelsens kropp i køens
    payload, eller fjern `unntak_id IS NOT NULL`-sjekken fra døren.
    """
    tenant = _tenantnavn("koe")
    hemmelig = "HEMMELIG-KUNDETEKST-" + secrets.token_hex(6)
    c = _rt()
    try:
        key_id, dek = _nokkel(c, tenant)
        hid = _ta_imot(c, tenant, key_id, dek, tekst=hemmelig)
        _klassifiser(c, tenant, hid, prioritet="hoy")
        sak = _til_koe(c, tenant, hid, key_id, dek)
        sak2 = _til_koe(c, tenant, hid, key_id, dek, begrunnelse="igjen")
    finally:
        c.close()
    assert sak == sak2, "to klikk ga to saker"
    _sett_kontekst(migrator, tenant)
    rad = migrator.execute(
        "SELECT sakskilde, payload_type, prioritet, sakstype,"
        "       maks_auto_forsok_snapshot, policy_versjon,"
        "       encode(payload_kryptert,'hex')"
        "  FROM unntak WHERE tenant=%s AND id=%s", (tenant, sak)).fetchone()
    kobling = migrator.execute(
        "SELECT unntak_id FROM henvendelse WHERE tenant=%s"
        " AND henvendelse_id=%s", (tenant, hid)).fetchone()[0]
    migrator.rollback()
    assert rad[0] == "henvendelse"
    assert rad[1] == "kryptert"
    # PRIORITETEN ARVES: en `hoy` henvendelse havner ikke bakerst.
    assert rad[2] == "hoy"
    assert rad[3] == "normal"
    # SNAPSHOT-TRIOEN STÅR NULL: det fantes ingen policybeslutning bak.
    assert rad[4] is None and rad[5] is None
    # …og teksten er ikke der. Ciphertexten kan ikke inneholde
    # klarteksten, men porten måler det likevel: en senere endring som
    # la payloaden inn i klartekst ville falt på nøyaktig denne linjen.
    assert hemmelig.encode().hex() not in rad[6]
    assert kobling == sak


@pg
def test_mistenkelig_blir_sikkerhetssak(migrator):
    """`mistenkelig` er den ene handlingstypen der køens KLASSE betyr noe
    annet enn hastverk: saken blir `sikkerhet`, ikke `normal`. En
    phishing-henvendelse i den alminnelige køen er en phishing-henvendelse
    ingen sikkerhetsansvarlig ser.
    """
    tenant = _tenantnavn("mistenk")
    c = _rt()
    try:
        key_id, dek = _nokkel(c, tenant)
        hid = _ta_imot(c, tenant, key_id, dek)
        _klassifiser(c, tenant, hid, prioritet="lav",
                     handlingstype="mistenkelig")
        sak = _til_koe(c, tenant, hid, key_id, dek)
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    rad = migrator.execute(
        "SELECT sakstype, prioritet FROM unntak WHERE tenant=%s AND id=%s",
        (tenant, sak)).fetchone()
    migrator.rollback()
    # PRIORITETEN OVERSTYRES OGSÅ: `lav` var klassifiseringens ord om
    # hvor mye det haster å SVARE, ikke om hvor mye det haster å se på
    # en mulig angriper.
    assert rad == ("sikkerhet", "hoy")


# ---------------------------------------------------------------------------
# INVARIANT 3: andre_unntakskø_opprettet
# ---------------------------------------------------------------------------

def test_invariant_andre_unntakskoe_opprettet():
    """En andre kø ved siden av M-37s er nøyaktig det M-37 ble bygget for
    å hindre. 102 oppretter ingen tabell som er en kø, og modulens kode
    skriver bare i `unntak`.

    MUTASJONEN SOM DREPER DENNE: legg en `henvendelseskoe`-tabell i 102.
    """
    sql = MIGRASJON.read_text(encoding="utf-8")
    kode = "\n".join(l for l in sql.splitlines()
                     if not l.lstrip().startswith("--"))
    import re
    tabeller = set(re.findall(r"CREATE TABLE (\w+)", kode))
    assert tabeller == {"henvendelse", "klassifisering", "svarutkast",
                        "henvendelsesfunn"}, tabeller
    for navn in tabeller:
        assert "koe" not in navn and "kø" not in navn, navn
    # …og modulens kode nevner ingen annen kø enn `unntak`.
    assert "INSERT INTO public.unntak" in kode
    for fil in MODULFILER:
        tekst = fil.read_text(encoding="utf-8")
        uten = "\n".join(l for l in tekst.splitlines()
                         if not l.lstrip().startswith("#"))
        assert "INSERT INTO" not in uten.upper(), \
            f"{fil.name} skriver SQL direkte — dørene eier skrivingen"


# ---------------------------------------------------------------------------
# INVARIANT 4: klassifisering_utenfor_lukket_sett
# ---------------------------------------------------------------------------

@pg
def test_invariant_klassifisering_utenfor_lukket_sett(migrator):
    """TRE LUKKEDE AKSER i basen, og FLATEN kjenner nøyaktig de samme
    verdiene. En modell som får finne på egne kategorier gir en kø ingen
    kan sortere — og settet må være lukket FØR modellen kommer (PR-B),
    ikke etterpå.

    MUTASJONEN SOM DREPER DENNE: legg en verdi i en CHECK uten å lære
    flaten den.
    """
    tenant = _tenantnavn("lukket")
    c = _rt()
    try:
        key_id, dek = _nokkel(c, tenant)
        hid = _ta_imot(c, tenant, key_id, dek)
    finally:
        c.close()
    for felt, verdi in (("prioritet", "oppdiktet"), ("tema", "oppdiktet"),
                        ("handlingstype", "oppdiktet")):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_kundeservice_eier")
        kolonner = {"prioritet": "normal", "tema": "annet",
                    "handlingstype": "svar_kreves"}
        kolonner[felt] = verdi
        with pytest.raises(psycopg.errors.CheckViolation), \
                migrator.transaction():
            migrator.execute(
                "INSERT INTO klassifisering (tenant, henvendelse_id,"
                " prioritet, tema, handlingstype, kilde, opprettet_av)"
                " VALUES (%s,%s,%s,%s,%s,'menneske','u')",
                (tenant, hid, kolonner["prioritet"], kolonner["tema"],
                 kolonner["handlingstype"]))
        migrator.rollback()

    sql = MIGRASJON.read_text(encoding="utf-8")
    flate = FLATE.read_text(encoding="utf-8")
    from api.kundeservice import HANDLINGSTYPER, PRIORITETER, TEMAER
    for verdi in (*PRIORITETER, *TEMAER, *HANDLINGSTYPER):
        assert f"'{verdi}'" in sql, f"{verdi} står ikke i 102"
        assert f'"{verdi}"' in flate, f"flaten kjenner ikke {verdi}"


@pg
def test_en_modelldom_kan_ikke_staa_uten_digest(migrator):
    """PR-B-FORBEREDELSEN, målt nå: en klassifisering med kilde `modell`
    KREVER en digest, og en med kilde `menneske` kan ikke ha en. Uten
    kravet ville en modelldom vært usporbar den dagen modellen byttes
    (M-31s dom), og formen ville måttet oppfinnes i hastverk.

    MUTASJONEN SOM DREPER DENNE: fjern
    `klassifisering_modell_krever_digest`.
    """
    tenant = _tenantnavn("digest")
    c = _rt()
    try:
        key_id, dek = _nokkel(c, tenant)
        hid = _ta_imot(c, tenant, key_id, dek)
    finally:
        c.close()
    for kilde, digest in (("modell", None), ("menneske", "sha256:abc")):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_kundeservice_eier")
        with pytest.raises(psycopg.errors.CheckViolation), \
                migrator.transaction():
            migrator.execute(
                "INSERT INTO klassifisering (tenant, henvendelse_id,"
                " prioritet, tema, handlingstype, kilde, modell_digest,"
                " opprettet_av) VALUES"
                " (%s,%s,'normal','annet','til_info',%s,%s,'u')",
                (tenant, hid, kilde, digest))
        migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 5: utkast_endret_etter_innsetting
# ---------------------------------------------------------------------------

@pg
def test_invariant_utkast_endret_etter_innsetting(migrator):
    """Utkastet er APPEND-ONLY på teksten. Et utkast som endres under
    føttene på den som leser det, er et utkast ingen kan stå for å ha
    sendt — og siden `brukt_manuelt` er sporet som lukker en henvendelse
    som «besvart», ville en endret tekst gjort selve beviset usant.

    Det eneste som lovlig beveger seg er `status`, og bare FRA
    `foreslatt`.

    MUTASJONEN SOM DREPER DENNE: fjern `m17_utkast_vakt`.
    """
    tenant = _tenantnavn("utkast")
    c = _rt()
    try:
        key_id, dek = _nokkel(c, tenant)
        hid = _ta_imot(c, tenant, key_id, dek)
        uid = _utkast(c, tenant, hid, key_id, dek)
        # Regenerering er en NY rad, ikke en endring.
        _utkast(c, tenant, hid, key_id, dek, tekst="Nytt forsøk")
        _sett_kontekst(c, tenant)
        c.execute("SELECT m17_avgjor_utkast(%s,%s,'brukt_manuelt','u')",
                  (tenant, uid))
        c.commit()
        # …og en avgjørelse går ikke om igjen.
        _sett_kontekst(c, tenant)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT m17_avgjor_utkast(%s,%s,'forkastet','u')",
                      (tenant, uid))
        assert "alt avgjort" in str(ei.value)
        c.rollback()
        # Samme dom to ganger er et STILLE JA.
        _sett_kontekst(c, tenant)
        assert c.execute(
            "SELECT m17_avgjor_utkast(%s,%s,'brukt_manuelt','u')",
            (tenant, uid)).fetchone()[0] is False
        c.commit()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    assert migrator.execute(
        "SELECT count(*) FROM svarutkast WHERE tenant=%s"
        " AND henvendelse_id=%s", (tenant, hid)).fetchone()[0] == 2
    migrator.rollback()
    for sql in (
        "UPDATE svarutkast SET tekst_kryptert='\\x00'::bytea"
        " WHERE tenant=%s AND utkast_id=%s",
        "UPDATE svarutkast SET status='foreslatt'"
        " WHERE tenant=%s AND utkast_id=%s",
        "DELETE FROM svarutkast WHERE tenant=%s AND utkast_id=%s",
    ):
        _sett_kontekst(migrator, tenant)
        migrator.execute("SET LOCAL ROLE disponit_kundeservice_eier")
        with pytest.raises(psycopg.Error), migrator.transaction():
            migrator.execute(sql, (tenant, uid))
        migrator.rollback()


# ---------------------------------------------------------------------------
# INVARIANT 6: tenantlekkasje_i_henvendelsesregister
# ---------------------------------------------------------------------------

@pg
def test_101s_opphevingscheck_har_ingen_null_hull(migrator):
    """SAMME TRESTILLEDE FELLE, i 101s `avstemming_opphevet_helhet` —
    funnet av porten over og rettet i 102.

    `NULL ~ '...'` er NULL, ikke FALSE, og en CHECK som evaluerer til
    NULL PASSERER. En oppheving med tidsstempel og aktør, men UTEN
    begrunnelse, slapp derfor gjennom via direkte DML. I praksis var
    hullet utilgjengelig (døren krever begrunnelsen), men en invariant
    som bare holder fordi ingen gikk utenom døren, er en vane.

    MUTASJONEN SOM DREPER DENNE: fjern rettelsesblokken fra 102.
    """
    tenant = _tenantnavn("nullhull")
    c = _rt()
    try:
        _sett_kontekst(c, tenant)
        k = uuid.uuid4()
        c.execute("SELECT m13_registrer_konto(%s,%s,'K','12345678901',"
                  "                           'NOK','u')", (tenant, k))
        c.commit()
        _sett_kontekst(c, tenant)
        pid, bid = uuid.uuid4(), uuid.uuid4()
        c.execute("SELECT m13_registrer_bilag(%s,%s,'F-1','inn',1000,'M',"
                  "                           current_date,NULL,'u')",
                  (tenant, bid))
        c.execute("SELECT * FROM m13_registrer_post(%s,%s,%s,'B-1',"
                  "        current_date,1000,'tekst',NULL,'u')",
                  (tenant, pid, k))
        c.commit()
        aid = uuid.uuid4()
        _sett_kontekst(c, tenant)
        c.execute("SELECT m13_avstem(%s,%s,%s,%s,'manuell','fordi','u')",
                  (tenant, aid, pid, bid))
        c.commit()
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    migrator.execute("SET LOCAL ROLE disponit_avstemming_eier")
    # `opphevet_av` MÅ være kontekstens aktør: vakten (BEFORE UPDATE)
    # kjører FØR CHECK-en og feller ellers sin egen dom først. Testen
    # skal måle CHECK-en, ikke vakten — og at vakten IKKE ser på
    # begrunnelsen er nettopp derfor CHECK-en må gjøre det.
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "UPDATE avstemming SET opphevet_ts=now(), opphevet_av='test',"
            " opphevet_begrunnelse=NULL WHERE tenant=%s"
            " AND avstemming_id=%s", (tenant, aid))
    migrator.rollback()


@pg
def test_invariant_tenantlekkasje_direkte(migrator):
    """RLS ENABLE+FORCE + `tenant_isolasjon` på hver av de fire
    tabellene, og dørene binder tenanten til KONTEKSTEN (SP-1).
    """
    a = _tenantnavn("lekk-a")
    b = _tenantnavn("lekk-b")
    c = _rt()
    try:
        ka, da = _nokkel(c, a)
        _ta_imot(c, a, ka, da)
        kb, db = _nokkel(c, b)
        _ta_imot(c, b, kb, db)
        _sett_kontekst(c, b)
        with pytest.raises(psycopg.Error) as ei:
            c.execute("SELECT * FROM m17_kostatus(%s)", (a,))
        assert "tenantkontekst" in str(ei.value)
        c.rollback()
        _sett_kontekst(c, a)
        assert c.execute("SELECT * FROM m17_kostatus(%s)",
                         (a,)).fetchone()[0] == 1
        c.rollback()
    finally:
        c.close()
    for tab in ("henvendelse", "klassifisering", "svarutkast",
                "henvendelsesfunn"):
        rad = migrator.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
            " WHERE oid = %s::regclass", (f"public.{tab}",)).fetchone()
        assert rad == (True, True), f"{tab}: RLS ikke ENABLE+FORCE"
    migrator.rollback()


@pg
def test_invariant_tenantlekkasje_over_api(migrator, klient):
    """…og over HTTP: økten hos A får aldri se Bs henvendelser."""
    fremmed = _tenantnavn("fremmed")
    c = _rt()
    try:
        ke, de = _nokkel(c, TENANT)
        _ta_imot(c, TENANT, ke, de, ref="EGEN-REF")
        kf, df = _nokkel(c, fremmed)
        _ta_imot(c, fremmed, kf, df, ref="FREMMED-REF")
    finally:
        c.close()
    cookie, _csrf = _browserokt(migrator, ["admin"])
    r = klient.get("/v1/kundeservice", cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    kropp = json.dumps(r.json())
    assert "EGEN-REF" in kropp
    assert "FREMMED-REF" not in kropp


# ---------------------------------------------------------------------------
# INVARIANT 7: ui_axe_alvorlige_brudd
# ---------------------------------------------------------------------------

def test_invariant_ui_axe_bor_i_js_suiten():
    """Axe-porten kjøres i `platform/core/ui` (node --test), ikke her."""
    fil = (ROT / "platform" / "core" / "ui" / "test"
           / "kundeservice.test.js")
    assert fil.exists(), "kundeservice.test.js mangler"
    assert "axe" in fil.read_text(encoding="utf-8"), \
        "UI-suiten kjører ingen axe-port for flaten"


# ---------------------------------------------------------------------------
# Persondata
# ---------------------------------------------------------------------------

@pg
def test_innholdet_ligger_kryptert_og_avsenderen_er_en_hash(migrator):
    """Emne og kropp er tenant-DEK-kryptert (058/088-formen), og
    avsenderen lagres som sha256 — registeret trenger å kunne kjenne
    igjen den samme avsenderen, ikke å kunne lese adressen.

    NORMALISERINGEN måles i samme test: «Kunde@Eksempel.no » og
    «kunde@eksempel.no» er den SAMME avsenderen. Uten den ville
    registeret hatt to kunder som er én.

    MUTASJONEN SOM DREPER DENNE: lagre emne/kropp i en TEXT-kolonne, ELLER
    fjern `lower()` fra `_avsenderhash`.
    """
    from api.kundeservice import _avsenderhash
    assert _avsenderhash("Kunde@Eksempel.no ") == \
        _avsenderhash("kunde@eksempel.no")
    tenant = _tenantnavn("krypto")
    hemmelig = "HEMMELIG-" + secrets.token_hex(6)
    c = _rt()
    try:
        key_id, dek = _nokkel(c, tenant)
        hid = _ta_imot(c, tenant, key_id, dek, tekst=hemmelig,
                       avsender="Kunde@Eksempel.no")
    finally:
        c.close()
    _sett_kontekst(migrator, tenant)
    rad = migrator.execute(
        "SELECT encode(kropp_kryptert,'hex'), avsender_hash,"
        "       octet_length(nonce_kropp)"
        "  FROM henvendelse WHERE tenant=%s AND henvendelse_id=%s",
        (tenant, hid)).fetchone()
    kolonner = [r[0] for r in migrator.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name='henvendelse'"
    ).fetchall()]
    migrator.rollback()
    assert hemmelig.encode().hex() not in rad[0]
    assert rad[1] == _avsenderhash("kunde@eksempel.no")
    assert rad[2] == 12
    # Ingen kolonne som kunne holdt klartekst.
    for forbudt in ("emne", "kropp", "avsender"):
        assert forbudt not in kolonner, kolonner


@pg
def test_samme_innboks_lest_to_ganger_gir_de_samme_radene(migrator):
    """DEN VIRKELIGE IDEMPOTENSEN er `ekstern_ref`. En dobbelt registrert
    henvendelse ville sett ut som at kunden spurte to ganger — og da
    svarer noen to ganger.

    OG DØREN GIR TILBAKE RADENS ID, ikke kallerens utledede: er
    henvendelsen alt tatt inn under en annen nøkkel, ville en id ingen
    rad har gjort neste kall til «finnes ikke» om noe som beviselig
    finnes (101s CodeRabbit-funn, samme form).
    """
    tenant = _tenantnavn("import")
    c = _rt()
    try:
        key_id, dek = _nokkel(c, tenant)
        forste = _ta_imot(c, tenant, key_id, dek, ref="MSG-X")
        annen_nokkel = uuid.uuid4()
        # KONTEKSTEN ER TRANSAKSJONSLOKAL: `_ta_imot` committet, så den
        # er borte. Uten denne linjen måler testen SP-1 i stedet for
        # idempotensen — og består av feil grunn hvis SP-1 forsvinner.
        _sett_kontekst(c, tenant)
        rad = c.execute(
            "SELECT * FROM m17_ta_imot(%s,%s,'epost','MSG-X',now(),%s,"
            "       %s,%s,%s,%s,%s,'u')",
            (tenant, annen_nokkel,
             hashlib.sha256(b"x").hexdigest(),
             *_kr(dek, key_id, tenant, "emne"),
             *_kr(dek, key_id, tenant, "kropp"), key_id)).fetchone()
        c.commit()
        assert rad[0] is False
        assert rad[1] == forste
        assert rad[1] != annen_nokkel
        _sett_kontekst(c, tenant)
        assert c.execute("SELECT * FROM m17_kostatus(%s)",
                         (tenant,)).fetchone()[0] == 1
        c.rollback()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Sveipen
# ---------------------------------------------------------------------------

@pg
def test_sveipen_reiser_de_tre_funntypene_og_er_idempotent(migrator):
    """Tre funntyper, og KRAVET OM SVAR ER KLASSIFISERINGENS: en
    `til_info`-henvendelse blir aldri et `ubesvart`-funn. Uten det leddet
    ville funnlisten fylt seg med nyhetsbrev, og de virkelige ubesvarte
    druknet.

    `mistenkelig_uten_behandling` har INGEN aldersgrense: å vente to døgn
    på den ville vært å gi angriperen to døgn.

    MUTASJONEN SOM DREPER DENNE: fjern `handlingstype='svar_kreves'` fra
    kandidatene, eller gi mistenkelig-grenen en aldersgrense.
    """
    tenant = _tenantnavn("sveip")
    c = _rt()
    try:
        key_id, dek = _nokkel(c, tenant)
        ukl = _ta_imot(c, tenant, key_id, dek, dager_siden=10)
        ube = _ta_imot(c, tenant, key_id, dek, dager_siden=10)
        mis = _ta_imot(c, tenant, key_id, dek, dager_siden=0)
        info = _ta_imot(c, tenant, key_id, dek, dager_siden=10)
        _klassifiser(c, tenant, ube, handlingstype="svar_kreves")
        _klassifiser(c, tenant, mis, handlingstype="mistenkelig")
        _klassifiser(c, tenant, info, handlingstype="til_info")
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    funn = {r[0]: r for r in _funn(migrator, tenant)}
    assert set(funn) == {"uklassifisert_over_grense",
                         "ubesvart_over_grense",
                         "mistenkelig_uten_behandling"}, sorted(funn)
    assert funn["uklassifisert_over_grense"][1] == ukl
    assert funn["ubesvart_over_grense"][1] == ube
    assert funn["mistenkelig_uten_behandling"][1] == mis
    # `info` er ti døgn gammel og har INGEN funn: den ba ikke om svar.
    assert all(r[1] != info for r in _funn(migrator, tenant))

    forst = {(r[0], r[1]) for r in _funn(migrator, tenant)}
    with _sv() as v:
        rad = _sveip(v)
    assert rad[1] == 0, "sveip nummer to skrev nye rader"
    assert {(r[0], r[1]) for r in _funn(migrator, tenant)} == forst


@pg
def test_funnet_lukkes_naar_henvendelsen_behandles(migrator):
    """Et funn som ikke lenger gjelder lukkes — og RADEN BESTÅR. En
    henvendelse satt i unntakskøen er ikke oversett, den er TILDELT, og
    et funn på den ville vært støy.
    """
    tenant = _tenantnavn("lukkfunn")
    c = _rt()
    try:
        key_id, dek = _nokkel(c, tenant)
        hid = _ta_imot(c, tenant, key_id, dek, dager_siden=10)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert len(_funn(migrator, tenant)) == 1
    c = _rt()
    try:
        _klassifiser(c, tenant, hid, handlingstype="svar_kreves")
        _til_koe(c, tenant, hid, key_id, dek)
    finally:
        c.close()
    with _sv() as v:
        _sveip(v)
    assert _funn(migrator, tenant) == []
    lukket = _funn(migrator, tenant, bare_apne=False)
    assert len(lukket) == 1 and lukket[0][3] is False


@pg
def test_sveipen_nekter_kontekst(migrator):
    """Sveipen er KRYSS-TENANT og kjøres uten tenantkontekst."""
    with _sv() as v:
        v.execute("SELECT set_config('disponit.tenant','t',false)")
        with pytest.raises(psycopg.Error) as ei:
            v.execute("SELECT * FROM m17_sveip_henvendelser(500,2,5)")
        assert "KRYSS-TENANT" in str(ei.value)
        v.rollback()


@pg
def test_sveiperollen_har_ingen_tabellrettigheter(migrator):
    """`disponit_henvendelsessveip` har nøyaktig ÉN rettighet i basen. Og
    her er snittet strengere enn noe annet sted i klyngen: en henvendelse
    er PERSONDATA, så en sveiperolle med SELECT ville kunnet lese
    kundetekster den ikke har noe med.
    """
    if not HENVENDELSESVEIP_DSN:
        pytest.skip("DISPONIT_TEST_HENVENDELSESVEIP_DSN ikke satt")
    rader = migrator.execute(
        "SELECT table_name, privilege_type"
        "  FROM information_schema.table_privileges"
        " WHERE grantee='disponit_henvendelsessveip'").fetchall()
    migrator.rollback()
    assert rader == [], rader


def test_overlappende_sveip_hopper_over_og_rorer_ikke_feiltelleren():
    """En kjøring som fant arbeidernøkkelen opptatt har VERKEN lyktes
    ELLER feilet."""
    from drift import henvendelsessveip

    class Falsk:
        def execute(self, sql, *a):
            class R:
                @staticmethod
                def fetchone():
                    return (False,)
            return R()

        def commit(self):
            pass

    r = henvendelsessveip.kjor(Falsk(), tidligere_feil=1)
    assert r.hoppet_over is True
    assert r.feilet is False and r.alarm_utlost is False


def test_sveipen_nekter_aa_starte_uten_egen_dsn(tmp_path, monkeypatch):
    """INGEN fallback til `DATABASE_URL`."""
    from drift import kjor_henvendelsessveip
    monkeypatch.delenv("DISPONIT_HENVENDELSESVEIP_URL", raising=False)
    monkeypatch.setenv("DISPONIT_HENVENDELSESVEIPTILSTAND",
                       str(tmp_path / "t.json"))
    monkeypatch.setattr("db.hemmeligheter.last_credentials", lambda: None)
    assert kjor_henvendelsessveip.main() == 2


# ---------------------------------------------------------------------------
# HTTP-riggen
# ---------------------------------------------------------------------------

def _C_SESJON():
    from api import sesjon as sesjonmodul
    return sesjonmodul.C_SESJON


def _browserokt(migrator, roller):
    """Minirigg: en innlogget browserøkt med gitte roller i TENANT."""
    from api import sesjon as sesjonmodul
    _sett_kontekst(migrator, TENANT)
    bid = migrator.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://m17.test', %s) RETURNING bruker_id",
        ("s17h-" + secrets.token_hex(6),)).fetchone()[0]
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
@dekker("henvendelse_ulovlig_tilstand")
def test_http_ulovlig_tilstand_er_409_og_ikke_400(migrator, klient):
    """FEILVEIEN `henvendelse_ulovlig_tilstand`. Kroppen ER velformet —
    det er BASEN som sier nei — og forskjellen er selve svaret.

    Dette er også testen `test_api_porter.test_hver_feilvei_har_en_test`
    krever for den nye feilveien.
    """
    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _hpost(klient, cookie, csrf, "/v1/kundeservice/henvendelse",
               {"kanal": "epost", "ekstern_ref": "HTTP-1",
                "avsender": "kunde@eksempel.no", "emne": "Hei",
                "kropp": "Spørsmål", "mottatt": "2026-09-01T10:00:00Z"})
    assert r.status_code in (200, 201), r.text
    hid = r.json()["henvendelse_id"]
    # «besvart» uten et brukt utkast: velformet kropp, TILSTANDEN sier nei.
    r = _hpost(klient, cookie, csrf,
               f"/v1/kundeservice/henvendelse/{hid}/lukk",
               {"utfall": "besvart"})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "henvendelse_ulovlig_tilstand"
    # …og en ukjent handlingstype er 400: KROPPEN er feil.
    r = _hpost(klient, cookie, csrf,
               f"/v1/kundeservice/henvendelse/{hid}/klassifiser",
               {"prioritet": "normal", "tema": "annet",
                "handlingstype": "oppdiktet"})
    assert r.status_code == 400, r.text
    assert r.json()["feil"] == "request_feilformet"


@pg
def test_http_innholdet_krever_sitt_eget_scope(migrator, klient):
    """Å SE KØEN OG Å LESE INNHOLDET ER TO HANDLINGER. Køen ligger bak
    `decisions:read`; teksten bak `kundeservice:innhold`.

    Porten måler at scopene faktisk er forskjellige i `RUTESCOPE` — en
    flate som lovet at listen kan vises sier ikke at hver celle kan
    åpnes.
    """
    from api.app import RUTESCOPE
    assert RUTESCOPE[("GET", "/v1/kundeservice")] == "decisions:read"
    assert RUTESCOPE[
        ("GET", "/v1/kundeservice/henvendelse/{henvendelse_id:uuid}"
                "/innhold")] == "kundeservice:innhold"
    from api.autorisasjon import ROLLE_TIL_SCOPES
    for rolle in ("leser", "admin"):
        assert "kundeservice:innhold" in ROLLE_TIL_SCOPES[rolle], rolle
    for rolle in ("godkjenner", "policyforvalter", "domeneadjudikator"):
        assert "kundeservice:innhold" not in ROLLE_TIL_SCOPES[rolle], \
            rolle

    cookie, csrf = _browserokt(migrator, ["admin"])
    r = _hpost(klient, cookie, csrf, "/v1/kundeservice/henvendelse",
               {"kanal": "skjema", "ekstern_ref": "HTTP-2",
                "avsender": "kunde@eksempel.no", "emne": "Emnet",
                "kropp": "Selve teksten",
                "mottatt": "2026-09-01T10:00:00Z"})
    assert r.status_code in (200, 201), r.text
    hid = r.json()["henvendelse_id"]
    r = klient.get(f"/v1/kundeservice/henvendelse/{hid}/innhold",
                   cookies={_C_SESJON(): cookie})
    assert r.status_code == 200, r.text
    # RUNDTUREN: klartekst inn, ciphertext i basen, klartekst ut.
    assert r.json()["emne"] == "Emnet"
    assert r.json()["kropp"] == "Selve teksten"

    # …og en økt uten scopet får 403 på innholdet, men ser køen.
    cookie2, _ = _browserokt(migrator, ["godkjenner"])
    r = klient.get(f"/v1/kundeservice/henvendelse/{hid}/innhold",
                   cookies={_C_SESJON(): cookie2})
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# §0: grensen ble registrert FØR koden
# ---------------------------------------------------------------------------

def test_grensen_ble_registrert_for_koden():
    """Grensen `m17-v1` sto i `KRAVGRENSER` fra klynge 3-fundamentet, før
    en eneste linje av modulen fantes (§0-regelen).

    ÉN INVARIANT HAR NULL FORSØK I PR-A, og porten SIER DET i stedet for
    å late som den er oppfylt: `modellinput_umaskert_felt` måler
    maskeringen av det som sendes til en modell, og PR-A har ingen
    modell. Null brudd uten forsøk er RØDT i parformen. Invarianten får
    sin måling i PR-B, sammen med modellarmen.
    """
    from manifestskjema import KRAVGRENSER
    g = KRAVGRENSER["m17-v1"]
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    assert g["punktbinding"] == {}
    umalt = {"modellinput_umaskert_felt"}
    egen = Path(__file__).read_text(encoding="utf-8")
    for inv in g["invarianter"]:
        if inv in umalt:
            # Skrevet ned, ikke skjult: invarianten står i denne
            # docstringen med begrunnelsen for at den ikke er målt.
            assert inv in egen
            continue
        assert inv in egen, f"invarianten {inv} har ingen port"
    assert umalt <= set(g["invarianter"])
