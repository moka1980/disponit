"""M-57s kandidatlagre og kandidatdatagrensen (057) — klarsignalet §5.

Portene 18–20: etter reaping finnes fixture-strengen i NULL av de seks
lagrene (18); reaperen tømmer aldri ett lager alene, og settet av lagre
er målt mot katalogen, ikke mot en liste i testen (19); fristen kan ikke
forlenges av noen — ikke modulen, ikke runtime, ikke eieren (20).

Alle tester konstruerer egen tilstand; ingen delt fixture. Tidskontroll
skjer gjennom `lukk_rekrutteringsprosess` sitt `p_lukket_ts`, som BARE
kan peke bakover — å tidlegge lukkingen korter fristen, og det er den
eneste retningen som finnes (forlengelses-forsøket er sin egen port).
"""
from __future__ import annotations

import secrets
import uuid

import psycopg
import pytest

from .test_api import (ANNEN_TENANT, DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       migrator, miljo)
from .test_m37 import _sett_kontekst
from .test_m57_utsending import _grunnlag, _rt, pg

FIXTUR = "KANDIDATFIXTUR-" + secrets.token_hex(6)

#: De seks lagrene og payloadkolonnene deres — SPEILET i testens egen
#: pinning, men fasiten måles mot katalogen (port 19b), så et syvende
#: lager uten reap-dekning feller testen, ikke listen her.
LAGRE = {
    "kandidat_originaldokument": ("dokument", "filnavn", "innholdstype",
                                  "storrelse_bytes"),
    "kandidat_parsettekst": ("tekst",),
    "kandidat_evalueringsartefakt": ("artefakt",),
    "kandidat_intervjusporsmal": ("sporsmal",),
    "kandidat_utsendingsdata": ("mottaker_ref", "flettefelt"),
    "kandidat_avmaskering": ("felter",),
}

#: 075 (#157): reapingens MEDLEMMER er de seks payloadlagrene pluss
#: ankeret — det syvende har ingen payload (identiteten er en UUID),
#: men reaperen må nevne og merke det, og samlet-porten teller det.
MEDLEMMER = {"kandidat": (), **LAGRE}


def _claimet(m):
    """Et AKTIVT CLAIMET `rekruttering.evaluering`-oppdrag — den ENESTE
    tilstanden en kandidatprosess fødes i (Codex P1).

    `_evaluering` (056-nabofilen) gir et FULLFØRT oppdrag, fordi det er
    det en utsendingsliste kan promoteres fra. Kandidatprosessen står i
    motsatt ende av samme livsløp: den fødes MENS kjøringen står på, og
    et `utfort` oppdrag betyr at kjøringen som skulle lukket prosessen
    alt er ferdig."""
    return _grunnlag(m, oppdragstype="rekruttering.evaluering",
                     status="plukket")


def _prosess(m, rt, *, frist=90):
    """Evalueringsoppdrag + prosess, gjennom den herdede veien. Setter
    konteksten selv — `krev_tenantkontekst` binder parameteret dit."""
    oid, _ = _claimet(m)
    _sett_kontekst(rt, TENANT)
    pid = rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,%s)",
                     (TENANT, oid, frist)).fetchone()[0]
    return oid, pid


def _fyll_lagrene(rt, pid, kandidat=None):
    """Én kandidat med fixture-strengen i payloaden i ALLE seks lagre."""
    kid = kandidat or uuid.uuid4()
    did = uuid.uuid4()
    sha = secrets.token_hex(32)
    # 075 (#157): ankeret fødes FØRST, gjennom døren — lagrene FK-er det.
    rt.execute("SELECT opprett_kandidat(%s,%s,%s)", (TENANT, pid, kid))
    rt.execute(
        "INSERT INTO kandidat_originaldokument (tenant, prosess_id,"
        " kandidat_id, dokument_id, filnavn, innholdstype, dokument,"
        " storrelse_bytes, innhold_sha256) VALUES"
        " (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (TENANT, pid, kid, did, f"{FIXTUR}.pdf",
         f"application/pdf; kandidat={FIXTUR}", FIXTUR.encode(),
         len(FIXTUR.encode()), sha))
    rt.execute(
        "INSERT INTO kandidat_parsettekst (tenant, prosess_id,"
        " kandidat_id, dokument_id, tekst, innhold_sha256)"
        " VALUES (%s,%s,%s,%s,%s,%s)",
        (TENANT, pid, kid, did, f"CV-tekst {FIXTUR}", sha))
    rt.execute(
        "INSERT INTO kandidat_evalueringsartefakt (tenant, prosess_id,"
        " kandidat_id, artefakt, innhold_sha256)"
        " VALUES (%s,%s,%s,%s,%s)",
        (TENANT, pid, kid, f'{{"funn": "{FIXTUR}"}}', sha))
    rt.execute(
        "INSERT INTO kandidat_intervjusporsmal (tenant, prosess_id,"
        " kandidat_id, sporsmal, innhold_sha256)"
        " VALUES (%s,%s,%s,%s,%s)",
        (TENANT, pid, kid, f'["Hva mente du med {FIXTUR}?"]', sha))
    rt.execute(
        "INSERT INTO kandidat_utsendingsdata (tenant, prosess_id,"
        " kandidat_id, mottaker_ref, flettefelt, innhold_sha256)"
        " VALUES (%s,%s,%s,%s,%s,%s)",
        (TENANT, pid, kid, f"{FIXTUR}@example.org",
         f'{{"navn": "{FIXTUR}"}}', sha))
    rt.execute(
        "INSERT INTO kandidat_avmaskering (tenant, prosess_id,"
        " kandidat_id, felter, innhold_sha256)"
        " VALUES (%s,%s,%s,%s,%s)",
        (TENANT, pid, kid, f'{{"[NAVN-1]": "{FIXTUR}"}}', sha))
    return kid


def _reaperkobling():
    """(kobling, timerrolle) for `reap_kandidatdata` — 038-formen, som
    057 nå speiler ordrett (Codex P1): finnes `disponit_domener`, EIER den
    reaperen og runtime er NEKTET den. Testen deler koblingsvalget med
    evidensreaperen i stedet for å anta lokal-oppsettet — å anta det gjorde
    038-testene stille røde på verten, der `InsufficientPrivilege` er
    grantet som VIRKER, ikke en feil."""
    from .test_outbox_bestilling import _reaperkobling as felles
    return felles()


def _tell_fixtur(m, pid):
    """Antall payloadfelter over ALLE seks lagre som fortsatt bærer
    fixture-strengen — målt kolonne for kolonne, ikke via en visning
    reaperen kunne dele feil med."""
    _sett_kontekst(m, TENANT)
    treff = 0
    for tabell, kolonner in LAGRE.items():
        for kol in kolonner:
            uttrykk = (f"convert_from({kol}, 'UTF8')"
                       if (tabell, kol) ==
                       ("kandidat_originaldokument", "dokument")
                       else f"{kol}::text")
            treff += m.execute(
                f"SELECT count(*) FROM {tabell}"
                f" WHERE tenant=%s AND prosess_id=%s AND {kol} IS NOT NULL"
                f" AND {uttrykk} LIKE %s",
                (TENANT, pid, f"%{FIXTUR}%")).fetchone()[0]
    return treff


def _tell_storrelser(m, pid):
    """Antall originaldokumentrader som fortsatt bærer `storrelse_bytes`.

    Egen måling fordi kolonnen er TALL: `_tell_fixtur` leter etter
    fixture-strengen, og en `BIGINT` kan ikke bære den — men størrelsen
    er per-kandidat-metadata om dokumentet og reapes med det."""
    _sett_kontekst(m, TENANT)
    return m.execute(
        "SELECT count(*) FROM kandidat_originaldokument"
        " WHERE tenant=%s AND prosess_id=%s AND storrelse_bytes IS NOT NULL",
        (TENANT, pid)).fetchone()[0]


@pg
def test_prosessen_krever_evalueringsoppdrag(migrator):
    """Porten i `opprett_rekrutteringsprosess`: et oppdrag av en annen
    type får ingen kandidatprosess."""
    rt = _rt()
    try:
        oid, _ = _grunnlag(migrator, oppdragstype="wcag.kontroll")
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                       (TENANT, oid))
        rt.rollback()
        # Positiv kontroll i samme test: den riktige typen går.
        oid2, _ = _claimet(migrator)
        _sett_kontekst(rt, TENANT)
        pid = rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                         (TENANT, oid2)).fetchone()[0]
        assert pid is not None
        rt.commit()
    finally:
        rt.close()


@pg
def test_prosessen_krever_m57_eiermodul(migrator):
    """Cursor P2: fødselsporten leste TYPEN, ikke eieren.

    `claim_neste_oppdrag` plukker på `oppdrag.eiermodul`, så et oppdrag av
    riktig type med en annen eiermodul kan aldri claimes av `m57_ats` —
    men det fikk kandidatprosess og persondatalagre likevel. Ingen modul
    kommer da for å lukke prosessen, og payloaden ligger til reaperens
    maks levetid i stedet for fristen fra faktisk avslutning.

    MUTASJONEN SOM DREPER DENNE: fjern `o.eiermodul = 'm57_ats'` fra
    fødselsporten."""
    rt = _rt()
    try:
        feil_eier, _ = _grunnlag(migrator,
                                 oppdragstype="rekruttering.evaluering",
                                 eiermodul="m_wcag_audit")
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                       (TENANT, feil_eier))
        rt.rollback()
        # ... og ingen prosess ble lagt igjen.
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT count(*) FROM rekrutteringsprosess WHERE tenant=%s"
            " AND oppdrag_id=%s", (TENANT, feil_eier)).fetchone()[0] == 0
        migrator.rollback()
        # Positiv kontroll: riktig par går.
        oid, _ = _claimet(migrator)
        _sett_kontekst(rt, TENANT)
        assert rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                          (TENANT, oid)).fetchone()[0] is not None
        rt.commit()
    finally:
        rt.close()


@pg
def test_prosessen_er_idempotent_men_fristen_er_materiell(migrator):
    """Samme oppdrag + samme frist → samme prosess. Samme oppdrag + NY
    frist → konflikt: «opprett på nytt» er ikke en vei rundt §5."""
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        oid, pid = _prosess(migrator, rt, frist=45)
        pid2 = rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,45)",
                          (TENANT, oid)).fetchone()[0]
        assert pid2 == pid
        with pytest.raises(psycopg.errors.UniqueViolation):
            rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,180)",
                       (TENANT, oid))
        rt.rollback()
    finally:
        rt.close()


@pg
def test_opprett_prosess_er_idempotent_under_kapplop(migrator):
    """Codex P2: SELECT-så-INSERT lot kappløpstaperen dø på unik-bruddet.
    Nå får taperen vinnerens prosess-id: A setter inn uten å committe;
    B (egen tilkobling) kaller funksjonen og blokkerer på indeksen til A
    committer — og skal da returnere A-radens id, ikke `unique_violation`.
    Samme form som `test_frigi_er_idempotent_under_kapplop` (056)."""
    import threading

    oid, _ = _claimet(migrator)
    a = _rt()
    b = _rt()
    try:
        _sett_kontekst(a, TENANT)
        pid_a = a.execute("SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                          (TENANT, oid)).fetchone()[0]
        resultat: dict = {}

        def taper():
            _sett_kontekst(b, TENANT)
            try:
                resultat["pid"] = b.execute(
                    "SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                    (TENANT, oid)).fetchone()[0]
                b.commit()
            except Exception as feil:            # pragma: no cover
                resultat["feil"] = feil

        t = threading.Thread(target=taper)
        t.start()
        t.join(timeout=2)
        assert t.is_alive(), "B skulle blokkere på As ucommittede rad"
        a.commit()
        t.join(timeout=10)
        assert not t.is_alive(), "B kom aldri gjennom etter As commit"
        assert "feil" not in resultat, resultat.get("feil")
        assert resultat["pid"] == pid_a, "taperen fikk en ANNEN prosess"
    finally:
        a.close(); b.close()


@pg
def test_mislykket_terminalstatus_foder_ingen_prosess(migrator):
    """Cursor P2: ankeret spurte bare om OPPDRAGSTYPEN, aldri om status.

    Fristen løper fra LUKKINGEN (§5), og lukkingen er noe kjøringen gjør
    når den er ferdig. Et `feilet`- eller `kansellert`-oppdrag er alt
    over: kjøringen som skulle lukket prosessen kommer aldri, så
    persondataene ville ligget til reaperens MAKS LEVETID fra fødselen i
    stedet for fristen fra faktisk avslutning — og det for data som aldri
    skulle vært skrevet.

    Codex P1, samme port én runde senere: den NEGATIVE formen var en
    liste over tilstandene noen kom på, og `utfort` sto ikke i den. Kom
    det FØRSTE kallet etter at kjøringen var kvittert ut, fødtes en åpen
    prosess på et avsluttet oppdrag — og da gjelder nøyaktig skaden over,
    bare med `utfort` i stedet for `feilet`. `opprettet` er samme klasse
    fra den andre enden: ingen har claimet oppdraget, så ingen kommer for
    å lukke prosessen.

    Porten er derfor POSITIV: fødselen krever `plukket`, altså et aktivt
    claimet oppdrag. Den er fortsatt IKKE `= 'utfort'` som
    promoteringsvakten — ankeret fødes MENS kjøringen står på (modulen
    trenger et sted å legge parset tekst der og da), og et `utfort`-krav
    ville snudd livsløpet. Testen måler begge retninger, og i tillegg det
    Codex ba om å bevare: den idempotente GJENLESNINGEN overlever at
    oppdraget blir ferdig.

    Codex P2, tredje runde på samme port: «bevare» holdt bare for
    `utfort`. Statusporten sto FØR oppslaget, så et retry etter en
    tvetydig commit — kallet gikk igjennom, svaret gikk tapt, oppdraget
    rakk å bli `feilet`/`kansellert` — fikk `invalid_parameter_value` i
    stedet for prosess-id-en. Idempotensen brøt nøyaktig der den trengs.
    Porten hører til FØDSELEN: gjenlesningen skjer først, statusporten
    bare på veien som faktisk setter inn.

    MUTASJONEN SOM DREPER DENNE: sett porten tilbake til
    `status NOT IN ('feilet','kansellert')`, eller flytt statusporten
    tilbake foran oppslaget av `rekrutteringsprosess`."""
    for status in (None, "feilet", "kansellert", "utfort"):
        oid, _ = _grunnlag(migrator, oppdragstype="rekruttering.evaluering",
                           status=status)
        rt = _rt()
        try:
            _sett_kontekst(rt, TENANT)
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                           (TENANT, oid))
            rt.rollback()
            # ... og ingen prosess ble lagt igjen.
            _sett_kontekst(migrator, TENANT)
            assert migrator.execute(
                "SELECT count(*) FROM rekrutteringsprosess WHERE tenant=%s"
                " AND oppdrag_id=%s", (TENANT, oid)).fetchone()[0] == 0, \
                status
            migrator.rollback()
        finally:
            rt.close()
    # Positiv kontroll: det claimede oppdraget går. Uten denne halvdelen
    # ville en port som avviser ALT bestått testen.
    #
    # Og GJENLESNINGEN overlever ALLE tre terminaltilstandene, ikke bare
    # `utfort` (Codex P2). Retryet etter en tvetydig commit er nettopp
    # det som skjer når kjøringen feiler: kallet gikk igjennom, svaret
    # gikk tapt, oppdraget ble `feilet`. Da skal den som rydder få SAMME
    # id, ikke en avvisning. `kansellert` nås ikke direkte fra `plukket`
    # (005s statusmaskin), så veien dit går via `opprettet`.
    for vei in (("utfort",), ("feilet",), ("opprettet", "kansellert")):
        oid, _ = _claimet(migrator)
        rt = _rt()
        try:
            _sett_kontekst(rt, TENANT)
            pid = rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                             (TENANT, oid)).fetchone()[0]
            assert pid is not None
            rt.commit()
            _sett_kontekst(migrator, TENANT)
            for status in vei:
                migrator.execute("UPDATE oppdrag SET status=%s WHERE"
                                 " tenant=%s AND id=%s",
                                 (status, TENANT, oid))
            migrator.commit()
            _sett_kontekst(rt, TENANT)
            assert rt.execute(
                "SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                (TENANT, oid)).fetchone()[0] == pid, vei
            rt.rollback()
        finally:
            rt.close()


@pg
def test_terminalstatus_under_kapplop_foder_ingen_prosess(migrator):
    """Cursor P2: statusporten var en påstand om FORTIDEN.

    Det ulåste `EXISTS` leste oppdraget fra transaksjonens snapshot, og
    under READ COMMITTED kunne kjøringen gå til `feilet`/`kansellert`
    mellom sjekken og INSERT-en. Prosessen ble da født på et oppdrag som
    alt var terminalt — nøyaktig tilstanden porten finnes for å nekte, og
    den statiske testen over kan ikke se det.

    A (egen tilkobling) holder en ucommittet terminalovergang; B kaller
    fødselen og skal BLOKKERE på radlåsen, ikke lese forbi den. Etter As
    commit re-evaluerer PostgreSQL predikatet, raden faller ut av
    treffet, og B får en ærlig avvisning.

    MUTASJONEN SOM DREPER DENNE: bytt `FOR SHARE`-lesningen tilbake til
    et ulåst `EXISTS`."""
    import threading

    # PLUKKET, ikke `opprettet` (Codex P1): fødselsporten er positiv nå,
    # og en rad som ikke matcher predikatet i det hele tatt blir aldri
    # forsøkt låst — da ville B falt igjennom med en gang og testen målt
    # avvisningen i stedet for LÅSEN. A tar oppdraget til `feilet`, som
    # er den lovlige terminalovergangen fra `plukket` (005s statusmaskin).
    oid, _ = _claimet(migrator)
    a = _rt()
    b = _rt()
    try:
        _sett_kontekst(a, TENANT)
        a.execute("UPDATE oppdrag SET status='feilet' WHERE tenant=%s"
                  " AND id=%s", (TENANT, oid))
        resultat: dict = {}

        def foedsel():
            _sett_kontekst(b, TENANT)
            try:
                resultat["pid"] = b.execute(
                    "SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                    (TENANT, oid)).fetchone()[0]
                b.commit()
            except Exception as feil:
                resultat["feil"] = feil
                b.rollback()

        t = threading.Thread(target=foedsel)
        t.start()
        t.join(timeout=2)
        assert t.is_alive(), \
            "B leste forbi As ucommittede terminalovergang i stedet for å låse"
        a.commit()
        t.join(timeout=10)
        assert not t.is_alive(), "B kom aldri gjennom etter As commit"
        assert isinstance(resultat.get("feil"),
                          psycopg.errors.InvalidParameterValue), resultat
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT count(*) FROM rekrutteringsprosess WHERE tenant=%s"
            " AND oppdrag_id=%s", (TENANT, oid)).fetchone()[0] == 0
        migrator.rollback()
    finally:
        a.close(); b.close()


@pg
def test_opprett_prosess_krever_read_committed(migrator):
    """Cursor P2: kappløpstesten over kjørte BARE under READ COMMITTED.

    Idempotensløftet er utledet av en LESNING: `ON CONFLICT DO NOTHING`
    svelger taperens unik-brudd uten feil, og gjenlesningen rett etter må
    se VINNERENS rad. Under REPEATABLE READ står snapshotet fast fra
    transaksjonens første setning, så gjenlesningen er blind for en
    samtidig committet prosess — `v_id` blir NULL og et legitimt retry
    får «kunne hverken opprettes eller leses» der kontrakten lover den
    samme id-en tilbake. Samme klasse og samme ratifiserte form som
    056s `frigi_utsendelse`/`signer_utsendingsliste`.

    Testen måler nivåporten der den betyr noe: en konkurrent HAR
    committet prosessen, og retryet kommer inn under et fastholdt
    snapshot. Forventningen er `invalid_transaction_state` — en ærlig
    avvisning — ikke `unique_violation` og ikke det stille feilsvaret.

    MUTASJONEN SOM DREPER DENNE: slipp `serializable` gjennom porten
    igjen."""
    oid, _ = _claimet(migrator)
    vinner = _rt()
    try:
        _sett_kontekst(vinner, TENANT)
        pid = vinner.execute("SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                             (TENANT, oid)).fetchone()[0]
        vinner.commit()
    finally:
        vinner.close()
    for niva in (psycopg.IsolationLevel.REPEATABLE_READ,
                 psycopg.IsolationLevel.SERIALIZABLE):
        rt = _rt()
        try:
            # Isolasjonsnivået kan bare byttes utenfor en åpen transaksjon.
            rt.commit()
            rt.isolation_level = niva
            _sett_kontekst(rt, TENANT)
            with pytest.raises(psycopg.errors.InvalidTransactionState):
                rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                           (TENANT, oid))
            rt.rollback()
        finally:
            rt.close()
    # Positiv kontroll: READ COMMITTED-veien er uendret og fortsatt
    # idempotent — porten avviser NIVÅET, ikke kallet.
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        assert rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                          (TENANT, oid)).fetchone()[0] == pid
        rt.rollback()
    finally:
        rt.close()


@pg
def test_fristen_utenfor_spennet_avvises(migrator):
    """§4: 30–365 døgn. Begge kantene utenfor felles av CHECK-en —
    og begge kantene INNENFOR går (grensetesten måler grensen, ikke
    bare midten)."""
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        for frist in (29, 366, 0, -1):
            oid, _ = _claimet(migrator)
            _sett_kontekst(rt, TENANT)
            with pytest.raises(psycopg.errors.CheckViolation):
                rt.execute(
                    "SELECT opprett_rekrutteringsprosess(%s,%s,%s)",
                    (TENANT, oid, frist))
            rt.rollback()
        for frist in (30, 365):
            oid, _ = _claimet(migrator)
            _sett_kontekst(rt, TENANT)
            rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,%s)",
                       (TENANT, oid, frist))
        rt.commit()
    finally:
        rt.close()


@pg
def test_port20_fristen_kan_ikke_forlenges_av_noen(migrator):
    """Porten gjelder også EIEREN: et UPDATE på `slettefrist_dogn` er
    trigger-avvist uansett rolle — verifisert, ikke antatt."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt, frist=30)
        rt.commit()
        _sett_kontekst(migrator, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "UPDATE rekrutteringsprosess SET slettefrist_dogn=365"
                " WHERE tenant=%s AND prosess_id=%s", (TENANT, pid))
        migrator.rollback()
        # ... og runtime har ikke engang UPDATE-rettigheten (statisk).
        for tabell in ("rekrutteringsprosess", *LAGRE):
            for priv in ("UPDATE", "DELETE"):
                har = migrator.execute(
                    "SELECT has_table_privilege('disponit', %s, %s)",
                    (tabell, priv)).fetchone()[0]
                assert har is False, (tabell, priv)
        migrator.rollback()
    finally:
        rt.close()


@pg
def test_ankeret_fodes_kun_gjennom_funksjonen(migrator):
    """Codex P1: radvakten er BEFORE UPDATE OR DELETE og ser ingen fødsel.
    Et tabell-INSERT på `rekrutteringsprosess` ville derfor gått utenom
    BEGGE portene i `opprett_rekrutteringsprosess` — oppdragstypen og
    «lukket_ts aldri frem i tid» (som ville skjøvet hele slettefristen).
    Runtime har derfor KUN SELECT på ankeret; lagrene beholder INSERT."""
    har = migrator.execute(
        "SELECT has_table_privilege('disponit', 'rekrutteringsprosess',"
        " 'INSERT')").fetchone()[0]
    assert har is False, "runtime kan føde en prosess utenom funksjonen"
    for tabell in LAGRE:
        assert migrator.execute(
            "SELECT has_table_privilege('disponit', %s, 'INSERT')",
            (tabell,)).fetchone()[0] is True, tabell
    migrator.rollback()
    # ... og forsøket feller i praksis, ikke bare i katalogen.
    rt = _rt()
    try:
        oid, _ = _claimet(migrator)
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute(
                "INSERT INTO rekrutteringsprosess (tenant, prosess_id,"
                " oppdrag_id, slettefrist_dogn, lukket_ts)"
                " VALUES (%s,%s,%s,365, now() + interval '3650 days')",
                (TENANT, uuid.uuid4(), oid))
        rt.rollback()
    finally:
        rt.close()


def test_057_rettighetene_er_parameterisert_pa_rollenavnet():
    """Cursor P1 (samme form som gate14b port 18): migrasjonens `TO
    disponit` er test/lokal-fallbacken — driftssannheten står i den
    parameteriserte blokken i `migrer.py`. Uten EXECUTE der får en
    installasjon med et annet runtime-rollenavn `permission denied` på
    prosessfødselen og på lukkingen (som starter slettefristen).

    Reaperen skal IKKE stå der: den er kryss-tenant og hører til
    timerrollen — 038-formen, betinget DO-blokk i migrasjonen."""
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    kjorer = (rot / "deploy" / "staging"
              / "migrer.py").read_text(encoding="utf-8")
    for sign in ("opprett_rekrutteringsprosess(TEXT, BIGINT, INT)",
                 "lukk_rekrutteringsprosess(TEXT, UUID, TIMESTAMPTZ)"):
        assert f"GRANT EXECUTE ON FUNCTION {sign} TO {{rolle}}" in kjorer, \
            sign
    assert "GRANT EXECUTE ON FUNCTION reap_kandidatdata" not in kjorer, \
        "kryss-tenant-reaperen lekker til en parameterisert rolle"
    # Tabellspeilet: ankeret KUN SELECT, lagrene SELECT + INSERT.
    assert "GRANT SELECT ON rekrutteringsprosess TO {rolle};" in kjorer
    assert ("GRANT SELECT, INSERT ON rekrutteringsprosess"
            not in kjorer), "runtime får INSERT på ankeret i kjøreren"


def test_057_navngir_aldri_runtime_rollen():
    """Cursor P2, samme form som `test_056_navngir_aldri_runtime_rollen`.

    057 gjentok 056s gamle feilklasse: `disponit` er LOKAL-/TESTNAVNET på
    runtime-rollen, og `migrer.py` tar navnet som argument. Står grantene
    i migrasjonen, har den to utfall og begge er gale — finnes ikke rollen,
    ruller hele 057 tilbake; finnes navnet som en urelatert eller UTRANGERT
    innlogging, får DEN varig EXECUTE på prosessfødselen og på lukkingen
    (som starter slettefristen) og SELECT/INSERT på alle seks
    kandidatlagrene, for kjørerens nullstilling gjelder den KONFIGURERTE
    rollen, ikke alle roller.

    Den betingede reaperblokken er unntaket og står igjen med vilje: den
    er 038-formen ORDRETT, der `REVOKE ... FROM disponit` er selve poenget
    (et grant som bare slutter å bli gitt, er ikke trukket tilbake).
    Testen fjerner nettopp den blokken før den måler, slik at unntaket er
    SYNLIG her og ikke et hull noen kan utvide i stillhet.

    MUTASJONEN SOM DREPER DENNE: legg grant-blokkene tilbake i §7."""
    import re
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    sql = (rot / "platform" / "core" / "db" / "migrations"
           / "057_m57_kandidatlagre.sql").read_text(encoding="utf-8")
    # Anker BEGGE ender av unntaket. En lat `.*?` foran ville startet på
    # den FØRSTE `DO $$` i filen og slukt alt imellom — altså skjult
    # nettopp de grantene testen finnes for å måle.
    reaperblokk = re.compile(
        r"DO \$\$\s*BEGIN\s*IF EXISTS \(SELECT 1 FROM pg_roles"
        r" WHERE rolname = 'disponit_domener'\)[^$]*?END \$\$;", re.S)
    assert reaperblokk.search(sql), \
        "den betingede reaperblokken er borte — unntaket testen tar høyde" \
        " for finnes ikke lenger, og fritaket under er da et hull"
    uten_reaper = reaperblokk.sub("", sql)
    treff = list(re.finditer(r"TO disponit\b\s*;", uten_reaper))
    assert not treff, (
        "057 navngir runtime-rollen ved lokalnavn — kjøreren er eneste "
        "rettighetskilde: " + repr([
            uten_reaper[max(0, t.start() - 120):t.end()][-120:]
            for t in treff]))


@pg
def test_fodselsporten_gjelder_ogsa_claimeren(migrator):
    """Cursor P2: rettighetsgrensen kunne bare halve jobben.

    Runtime ble fratatt tabell-INSERT på ankeret i forrige runde, men
    CLAIMEREN må ha det — den er definer for
    `opprett_rekrutteringsprosess`. Direkte DML som claimer gikk derfor
    rett forbi hele fødselsporten, og vakten var BEFORE UPDATE OR DELETE
    og så ingen INSERT. En vakt som bare gjelder de rettighetsløse er
    ingen vakt.

    Målt for claimeren, altså den rollen som HAR rettigheten: feil
    oppdragstype, feil eiermodul, IKKE-CLAIMET status (avbrutt, ferdig og
    ikke plukket ennå — Codex P1) og en fødsel som alt er lukket — og
    positiv kontroll på at den lovlige fødselen fortsatt går. Backstoppen
    skal være nøyaktig like sterk som funksjonen den backstopper; her var
    den svakere i samme retning som funksjonen.

    MUTASJONEN SOM DREPER DENNE: sett triggeren tilbake til
    BEFORE UPDATE OR DELETE."""
    feil_type, _ = _grunnlag(migrator, oppdragstype="wcag.kontroll")
    feil_eier, _ = _grunnlag(migrator,
                             oppdragstype="rekruttering.evaluering",
                             eiermodul="m_wcag_audit")
    avbrutt, _ = _grunnlag(migrator, oppdragstype="rekruttering.evaluering",
                           status="kansellert")
    ferdig, _ = _grunnlag(migrator, oppdragstype="rekruttering.evaluering",
                          status="utfort")
    uplukket, _ = _grunnlag(migrator,
                            oppdragstype="rekruttering.evaluering",
                            status=None)
    lovlig, _ = _claimet(migrator)
    bakover, _ = _claimet(migrator)
    for oid, lukket in ((feil_type, None), (feil_eier, None),
                        (avbrutt, None), (ferdig, None), (uplukket, None),
                        (lovlig, "now()")):
        _sett_kontekst(migrator, TENANT)
        migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "INSERT INTO rekrutteringsprosess (tenant, prosess_id,"
                " oppdrag_id, slettefrist_dogn, lukket_ts)"
                f" VALUES (%s,%s,%s,90,{lukket or 'NULL'})",
                (TENANT, uuid.uuid4(), oid))
        migrator.rollback()
    # `opprettet` er den ANDRE enden av fristen (Cursor P2): reaperens
    # maks-levetid-arm regner fra `coalesce(lukket_ts, opprettet)`, og
    # kolonnen er immutabel etterpå. En fødsel frem i tid ville skjøvet
    # utløpet for en forlatt prosess stille — samme forlengelse som port
    # 20 nekter, bare gjennom den andre kolonnen.
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "INSERT INTO rekrutteringsprosess (tenant, prosess_id,"
            " oppdrag_id, slettefrist_dogn, opprettet)"
            " VALUES (%s,%s,%s,90, now() + interval '30 days')",
            (TENANT, uuid.uuid4(), lovlig))
    migrator.rollback()
    # Positiv kontroll: den lovlige, ÅPNE fødselen går — ellers ville en
    # vakt som avviser alle INSERT sett like grønn ut her. Bakover i tid
    # er også lovlig: det KORTER levetiden (og er formen den forlatte
    # prosessens reap-test bygger på).
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
    migrator.execute(
        "INSERT INTO rekrutteringsprosess (tenant, prosess_id,"
        " oppdrag_id, slettefrist_dogn) VALUES (%s,%s,%s,90)",
        (TENANT, uuid.uuid4(), lovlig))
    migrator.execute(
        "INSERT INTO rekrutteringsprosess (tenant, prosess_id,"
        " oppdrag_id, slettefrist_dogn, opprettet)"
        " VALUES (%s,%s,%s,90, now() - interval '30 days')",
        (TENANT, uuid.uuid4(), bakover))
    migrator.rollback()


@pg
def test_backstoppen_laser_oppdragsraden_som_funksjonen(migrator):
    """Codex P2: backstoppen var svakere enn funksjonen den backstopper.

    `opprett_rekrutteringsprosess` leser oppdraget `FOR SHARE`; vaktens
    INSERT-gren leste det ULÅST. Under READ COMMITTED kunne et samtidig
    UPDATE til `feilet`/`kansellert` committe mellom sjekken og INSERT-en,
    og en direkte claimer-INSERT fødte prosessen på et alt terminalt
    oppdrag. FK-en fanger det ikke: den tar `FOR KEY SHARE`, og et UPDATE
    av statuskolonnen er ikke i konflikt med den låsen.

    Samme oppsett som funksjonens kappløpstest: A holder en ucommittet
    terminalovergang, B gjør claimer-INSERT-en direkte og skal BLOKKERE på
    radlåsen. Etter As commit re-evaluerer PostgreSQL predikatet, raden
    faller ut av treffet, og vakten avviser.

    MUTASJONEN SOM DREPER DENNE: bytt `FOR SHARE`-lesningen i vaktens
    INSERT-gren tilbake til et ulåst `EXISTS`."""
    import threading

    from db.pg import koble

    # PLUKKET, ikke `opprettet` (Codex P1): fødselsporten er positiv nå,
    # og en rad som ikke matcher predikatet i det hele tatt blir aldri
    # forsøkt låst — da ville B falt igjennom med en gang og testen målt
    # avvisningen i stedet for LÅSEN. A tar oppdraget til `feilet`, som
    # er den lovlige terminalovergangen fra `plukket` (005s statusmaskin).
    oid, _ = _claimet(migrator)
    a = _rt()
    # B er den direkte claimer-DML-en, og den går gjennom en EGEN
    # eierkobling med `SET LOCAL ROLE`: runtime er fratatt tabell-INSERT
    # på ankeret og er ikke medlem av claimeren, så en `_rt()` her ville
    # feilet momentant — med samme unntakstype som porten kaster, altså
    # en test som ser grønn ut uten å ha rørt låsen.
    b = koble(MIGRATOR_DSN)
    try:
        _sett_kontekst(a, TENANT)
        a.execute("UPDATE oppdrag SET status='feilet' WHERE tenant=%s"
                  " AND id=%s", (TENANT, oid))
        resultat: dict = {}

        def foedsel():
            try:
                _sett_kontekst(b, TENANT)
                b.execute("SET LOCAL ROLE disponit_m37_claimer")
                b.execute(
                    "INSERT INTO rekrutteringsprosess (tenant, prosess_id,"
                    " oppdrag_id, slettefrist_dogn) VALUES (%s,%s,%s,90)",
                    (TENANT, uuid.uuid4(), oid))
                b.commit()
                resultat["kom_gjennom"] = True
            except Exception as feil:
                resultat["feil"] = feil
                b.rollback()

        t = threading.Thread(target=foedsel)
        t.start()
        t.join(timeout=2)
        assert t.is_alive(), \
            "B leste forbi As ucommittede terminalovergang i stedet for å låse"
        a.commit()
        t.join(timeout=10)
        assert not t.is_alive(), "B kom aldri gjennom etter As commit"
        # Avvisningen må komme fra FØDSELSPORTEN, ikke fra en
        # rettighetsnekt: begge er `insufficient_privilege`, og bare den
        # ene er det denne testen måler.
        assert isinstance(resultat.get("feil"),
                          psycopg.errors.InsufficientPrivilege), resultat
        assert "AKTIVT CLAIMET" in str(resultat["feil"]), resultat["feil"]
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT count(*) FROM rekrutteringsprosess WHERE tenant=%s"
            " AND oppdrag_id=%s", (TENANT, oid)).fetchone()[0] == 0
        migrator.rollback()
    finally:
        a.close(); b.close()


def test_057_navngir_disponit_kun_under_eksistensvakt():
    """Codex P1: `REVOKE ... FROM disponit` er en FEIL, ikke en no-op.

    Unntaket over — den betingede reaperblokken — navngir lokalnavnet på
    runtime-rollen i begge armer. PostgreSQL har ingen `IF EXISTS` på
    REVOKE: navngir en migrasjon en rolle som ikke finnes, avbrytes hele
    migrasjonen. En installasjon som HAR timerrollen `disponit_domener`,
    men kjører `migrer.py` med sitt eget runtime-rollenavn, mistet dermed
    hele 057 på en linje som skulle vært virkningsløs — og det er nettopp
    kombinasjonen 057s egen rettighetsseksjon sier at den støtter.

    Porten måler formen, ikke stedet: ENHVER setning i 057 som navngir
    `disponit` ved lokalnavn må stå under en `pg_roles`-vakt på samme
    navn, og ingen slik setning får stå utenfor en DO-blokk.

    MUTASJONEN SOM DREPER DENNE: fjern den indre `IF EXISTS`-en, eller
    gjør `ELSIF` om til `ELSE` igjen."""
    import re
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    sql = (rot / "platform" / "core" / "db" / "migrations"
           / "057_m57_kandidatlagre.sql").read_text(encoding="utf-8")
    blokk_re = re.compile(r"DO \$\$.*?END \$\$;", re.S)
    navngir = re.compile(r"(?:TO|FROM) disponit\s*;")
    funnet = 0
    for blokk in blokk_re.findall(sql):
        for treff in navngir.finditer(blokk):
            funnet += 1
            assert "rolname = 'disponit'" in blokk[:treff.start()], (
                "057 navngir runtime-rollen uten eksistensvakt: "
                + repr(blokk[max(0, treff.start() - 200):treff.end()]))
    assert funnet >= 2, (
        "reaperblokkens to armer navngir ikke lenger lokalnavnet — er"
        " unntaket borte, skal denne testen og fritaket i"
        " test_057_navngir_aldri_runtime_rollen fjernes sammen")
    utenfor = blokk_re.sub("", sql)
    assert not navngir.search(utenfor), \
        "057 navngir runtime-rollen utenfor enhver eksistensvakt"


@pg
def test_port20_lukkingen_kan_ikke_sta_frem_i_tid(migrator):
    """Fristen løper fra lukkingen — en lukking frem i tid ville
    forlenget den. Bakover korter den bare, og går."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        rt.commit()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute(
                "SELECT lukk_rekrutteringsprosess(%s,%s,"
                " now() + interval '1 day')", (TENANT, pid))
        rt.rollback()
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '1 hour')", (TENANT, pid))
        # ... og en satt lukking flyttes ikke (heller ikke bakover:
        # enda tidligere ville endret et løp som alt er i gang).
        with pytest.raises(psycopg.errors.UniqueViolation):
            rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                       " now() - interval '2 hour')", (TENANT, pid))
        rt.rollback()
    finally:
        rt.close()


@pg
def test_lukking_uten_tidspunkt_er_idempotent_ved_retry(migrator):
    """Codex P2: med `now()` som DEFAULT fikk hver retry et nytt
    tidspunkt, så den vanligste feilformen som finnes — kallet
    committet, men svaret gikk tapt — traff «lukkingen flyttes ikke» og
    fikk `unique_violation` for en operasjon som hadde lykkes. Uten et
    eksplisitt tidspunkt insisterer kalleren ikke på noe, og retryen er
    idempotent; et eksplisitt tidspunkt er fortsatt materielt."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        rt.commit()
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s)", (TENANT, pid))
        rt.commit()
        _sett_kontekst(rt, TENANT)
        forst = rt.execute(
            "SELECT lukket_ts FROM rekrutteringsprosess WHERE prosess_id=%s",
            (pid,)).fetchone()[0]
        assert forst is not None
        # Retryen: samme kall, ingen feil — og tidspunktet står stille.
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s)", (TENANT, pid))
        rt.commit()
        _sett_kontekst(rt, TENANT)
        assert rt.execute(
            "SELECT lukket_ts FROM rekrutteringsprosess WHERE prosess_id=%s",
            (pid,)).fetchone()[0] == forst, \
            "retryen flyttet lukkingen — fristen ville løpt på nytt"
        # ... og et EKSPLISITT, annet tidspunkt er fortsatt en konflikt.
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.UniqueViolation):
            rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                       " now() - interval '2 hour')", (TENANT, pid))
        rt.rollback()
        # ... mens det SAMME eksplisitte tidspunktet fortsatt er idempotent.
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,%s)",
                   (TENANT, pid, forst))
        rt.rollback()
    finally:
        rt.close()


@pg
def test_port18_reaping_tommer_alle_seks_lagrene(migrator):
    """Kjerneporten: fixture-strengen står i payloaden i alle seks lagre
    FØR reaping (positiv kontroll — en fraværstest uten den går grønn på
    søppel), og i NULL av dem etter. Radene består med hash og
    `slettet_ts` — minimal revisjonsevidens, ikke sporløshet."""
    rt = _rt()
    rp = None
    try:
        _, pid = _prosess(migrator, rt, frist=30)
        _fyll_lagrene(rt, pid)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '31 days')", (TENANT, pid))
        rt.commit()
        assert _tell_fixtur(migrator, pid) == 9, \
            "positiv kontroll: fixturen skal stå i alle payloadfeltene"
        # `storrelse_bytes` er payload uten å kunne bære fixture-strengen
        # (Codex P2): den er per-kandidat-metadata OM dokumentet, og den
        # sto igjen for alltid fordi kolonnen var `NOT NULL` og dermed
        # utenfor reap-overgangen. Den måles derfor for seg — positiv
        # kontroll her, fravær etter reapingen.
        assert _tell_storrelser(migrator, pid) >= 1, \
            "positiv kontroll: dokumentstørrelsen skal stå før reaping"
        migrator.rollback()
        rp, timerrolle = _reaperkobling()
        if timerrolle:
            # Sikkerhetsegenskapen 038/057 begrunner grantet med: en
            # kompromittert web-API-rolle skal ikke kunne trigge
            # retensjonsarbeid på tvers av alle tenanter.
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rt.execute("SELECT * FROM reap_kandidatdata(50)")
            rt.rollback()
        reapet = rp.execute("SELECT * FROM reap_kandidatdata(50)"
                            ).fetchall()
        rp.commit()
        assert (TENANT, pid) in [(r[0], r[1]) for r in reapet]
        assert _tell_fixtur(migrator, pid) == 0
        assert _tell_storrelser(migrator, pid) == 0, \
            "dokumentstørrelsen er payload og skal være borte etter reaping"
        # Radene og revisjonsevidensen består — i alle seks + ankeret.
        for tabell in LAGRE:
            rad = migrator.execute(
                f"SELECT count(*), count(*) FILTER (WHERE slettet_ts IS"
                f" NOT NULL), count(*) FILTER (WHERE innhold_sha256 IS"
                f" NOT NULL) FROM {tabell}"
                f" WHERE tenant=%s AND prosess_id=%s",
                (TENANT, pid)).fetchone()
            assert rad[0] >= 1 and rad[0] == rad[1] == rad[2], tabell
        # 075 (#157): det SYVENDE medlemmet merkes i samme sveip — raden
        # består som evidens (identiteten er en UUID, uten payload).
        anker = migrator.execute(
            "SELECT count(*), count(*) FILTER (WHERE slettet_ts IS"
            " NOT NULL) FROM kandidat WHERE tenant=%s AND prosess_id=%s",
            (TENANT, pid)).fetchone()
        assert anker[0] >= 1 and anker[0] == anker[1], \
            "ankeret ble ikke merket sammen med lagrene"
        assert migrator.execute(
            "SELECT slettet_ts IS NOT NULL FROM rekrutteringsprosess"
            " WHERE tenant=%s AND prosess_id=%s",
            (TENANT, pid)).fetchone()[0]
        migrator.rollback()
    finally:
        rt.close()
        if rp is not None:
            rp.close()


@pg
def test_port18_kandidatreaperen_kalles_fra_driftsveien(migrator):
    """Codex P1: `reap_kandidatdata` var definert, testet og GRANTet til
    timerrollen — og kalt fra INGEN driftsvei.

    Testene over kaller funksjonen direkte, og direktekallet beviser at
    REGELEN virker, ikke at noen kjører den. Den deployede veien er
    `disponit-evidensreaper.service` → `drift.kjor_evidensreaper` →
    `evidensreaper.kjor`, og fram til nå kalte den bare
    `reap_evidensfrister`: hver prosess forbi sin 30–365-døgnsfrist
    beholdt alle seks lagrene i det uendelige.

    Testen går derfor gjennom `evidensreaper.kjor` — samme funksjon
    tjenesten kaller, over timerrollens egen forbindelse — i stedet for
    å gjenta SQL-en.

    MUTASJONEN SOM DREPER DENNE: fjern `reap_kandidatdata`-blokken fra
    `evidensreaper.kjor`. Alle direktekallende tester over er grønne."""
    from drift import evidensreaper
    rt = _rt()
    rp = None
    try:
        _, pid = _prosess(migrator, rt, frist=30)
        _fyll_lagrene(rt, pid)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '31 days')", (TENANT, pid))
        rt.commit()
        assert _tell_fixtur(migrator, pid) == 9, \
            "positiv kontroll: fixturen skal stå i alle payloadfeltene"
        migrator.rollback()
        rp, _timerrolle = _reaperkobling()
        r = evidensreaper.kjor(rp)
        assert not r.kandidatdata_feilet, \
            "timerrollen har EXECUTE — en nekt her er et rettighetshull"
        assert (TENANT, str(pid)) in r.kandidatdata
        assert _tell_fixtur(migrator, pid) == 0
        migrator.rollback()
    finally:
        rt.close()
        if rp is not None:
            rp.close()


@pg
def test_reaping_respekterer_fristen(migrator):
    """Motstykket til port 18: en prosess som er lukket, men der fristen
    IKKE er løpt ut, røres ikke — et reap-kall er ikke en sletteknapp."""
    rt = _rt()
    rp = None
    try:
        _, pid = _prosess(migrator, rt, frist=30)
        _fyll_lagrene(rt, pid)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '29 days')", (TENANT, pid))
        rt.commit()
        rp, _timerrolle = _reaperkobling()
        reapet = rp.execute("SELECT * FROM reap_kandidatdata(50)"
                            ).fetchall()
        rp.commit()
        assert (TENANT, pid) not in [(r[0], r[1]) for r in reapet]
        assert _tell_fixtur(migrator, pid) == 9
        migrator.rollback()
        # ... og en ÅPEN prosess som ennå er innenfor levetiden røres
        # ikke (den forlatte, som HAR passert den, er sin egen test).
        _, pid2 = _prosess(migrator, rt, frist=30)
        _fyll_lagrene(rt, pid2)
        rt.commit()
        rp.execute("SELECT * FROM reap_kandidatdata(50)")
        rp.commit()
        assert _tell_fixtur(migrator, pid2) == 9
        migrator.rollback()
    finally:
        rt.close()
        if rp is not None:
            rp.close()


@pg
def test_forlatt_apen_prosess_reapes_etter_maks_levetid(migrator):
    """Codex P1: en kjøring som krasjer før `lukk_rekrutteringsprosess`
    etterlot en prosess som ALDRI ble lukket — og et predikat på
    `lukket_ts IS NOT NULL` utelukket den for alltid fra reaperen.
    Persondataene ble stående i det uendelige.

    Maks levetid er den samme fristen målt fra fødselen. Prosessen
    konstrueres direkte som eier (fødselen setter `opprettet` til now(),
    og kolonnen er immutabel — det er nettopp porten fristen hviler på)."""
    rt = _rt()
    rp = None
    try:
        oid, _ = _claimet(migrator)
        pid = uuid.uuid4()
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "INSERT INTO rekrutteringsprosess (tenant, prosess_id,"
            " oppdrag_id, slettefrist_dogn)"
            " VALUES (%s,%s,%s,30)",
            (TENANT, pid, oid))
        migrator.commit()
        # Payloaden skrives MENS prosessen lever (#164: vinduet er
        # stengt etter fristen, også for en forlatt prosess) — så
        # forlates den: fødselen backdateres med vaktene av, nøyaktig
        # historien en krasjet kjøring etterlater.
        _sett_kontekst(rt, TENANT)
        _fyll_lagrene(rt, pid)
        rt.commit()
        migrator.execute("ALTER TABLE rekrutteringsprosess"
                         " DISABLE TRIGGER USER")
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "UPDATE rekrutteringsprosess SET"
            " opprettet = now() - interval '31 days'"
            " WHERE tenant=%s AND prosess_id=%s", (TENANT, pid))
        migrator.execute("ALTER TABLE rekrutteringsprosess"
                         " ENABLE TRIGGER USER")
        migrator.commit()
        assert _tell_fixtur(migrator, pid) == 9
        migrator.rollback()
        rp, _timerrolle = _reaperkobling()
        reapet = rp.execute("SELECT * FROM reap_kandidatdata(50)"
                            ).fetchall()
        rp.commit()
        assert (TENANT, pid) in [(r[0], r[1]) for r in reapet]
        assert _tell_fixtur(migrator, pid) == 0
        # Prosessen er lukket ved FØDSELEN, ikke ved reapingen: fristen
        # ble aldri forlenget, og `prosess_reapet_krever_lukket` holder.
        lukket, opprettet, slettet = migrator.execute(
            "SELECT lukket_ts, opprettet, slettet_ts FROM"
            " rekrutteringsprosess WHERE tenant=%s AND prosess_id=%s",
            (TENANT, pid)).fetchone()
        assert lukket == opprettet and slettet is not None
        migrator.rollback()
    finally:
        rt.close()
        if rp is not None:
            rp.close()


@pg
def test_reapmerket_krever_at_lagrene_faktisk_er_tomme(migrator):
    """Cursor P1: reap-merket var en PÅSTAND, ikke en konklusjon.

    Reaperen velger bare prosesser med `slettet_ts IS NULL`. Et merke satt
    direkte — claimeren har UPDATE på ankeret, og eieren har det alltid —
    uten at lagrene er tømt, utelukket derfor prosessen fra reaping for
    alltid: payloaden blir stående, mens evidensen sier at den er slettet.
    §5s løfte brutt og målingen selv gjort blind, i én setning.

    MUTASJONEN SOM DREPER DENNE: fjern lagersjekken i vaktens
    `slettet_ts`-gren."""
    rt = _rt()
    rp = None
    try:
        _, pid = _prosess(migrator, rt, frist=30)
        _fyll_lagrene(rt, pid)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '31 days')", (TENANT, pid))
        rt.commit()
        assert _tell_fixtur(migrator, pid) == 9
        migrator.rollback()
        # Merket, satt av den rollen som HAR rettigheten, uten reaping.
        _sett_kontekst(migrator, TENANT)
        migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "UPDATE rekrutteringsprosess SET slettet_ts=now()"
                " WHERE tenant=%s AND prosess_id=%s", (TENANT, pid))
        migrator.rollback()
        # ... og payloaden står fortsatt der, altså fortsatt synlig for
        # reaperen — som er hele poenget med å nekte merket.
        assert _tell_fixtur(migrator, pid) == 9
        migrator.rollback()
        # Positiv kontroll: den ekte reaperen tømmer først og merker
        # etterpå, i samme transaksjon, og går uendret gjennom vakten.
        rp, _timerrolle = _reaperkobling()
        reapet = rp.execute("SELECT * FROM reap_kandidatdata(50)").fetchall()
        rp.commit()
        assert (TENANT, pid) in [(r[0], r[1]) for r in reapet]
        assert _tell_fixtur(migrator, pid) == 0
        assert migrator.execute(
            "SELECT slettet_ts IS NOT NULL FROM rekrutteringsprosess"
            " WHERE tenant=%s AND prosess_id=%s",
            (TENANT, pid)).fetchone()[0] is True
        migrator.rollback()
    finally:
        rt.close()
        if rp is not None:
            rp.close()


@pg
def test_port18_insert_etter_reap_avvises(migrator):
    """Codex P1: FK-en krever bare at prosessen FINNES, og reaperen
    utelukker for alltid en prosess med `slettet_ts`. Uten en INSERT-vakt
    kunne en forsinket eller retriet skriver derfor gjenoppstå persondata
    på en reapet prosess — uten noen vei til å slette dem igjen."""
    rt = _rt()
    rp = None
    try:
        _, pid = _prosess(migrator, rt, frist=30)
        _fyll_lagrene(rt, pid)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '31 days')", (TENANT, pid))
        rt.commit()
        rp, _timerrolle = _reaperkobling()
        rp.execute("SELECT * FROM reap_kandidatdata(50)")
        rp.commit()
        assert _tell_fixtur(migrator, pid) == 0
        migrator.rollback()
        # Den forsinkede skriveren, på hvert eneste lager.
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _fyll_lagrene(rt, pid)
        rt.rollback()
        for tabell in LAGRE:
            _sett_kontekst(rt, TENANT)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rt.execute(
                    f"INSERT INTO {tabell} (tenant, prosess_id,"
                    f" kandidat_id, innhold_sha256) VALUES (%s,%s,%s,'0')",
                    (TENANT, pid, uuid.uuid4()))
            rt.rollback()
        assert _tell_fixtur(migrator, pid) == 0
        migrator.rollback()
        # Positiv kontroll: en ÅPEN, ikke-reapet prosess tar imot payload
        # — vakten er en reap-port, ikke en skrivesperre.
        _, pid2 = _prosess(migrator, rt)
        _fyll_lagrene(rt, pid2)
        rt.commit()
        assert _tell_fixtur(migrator, pid2) == 9
        migrator.rollback()
    finally:
        rt.close()
        if rp is not None:
            rp.close()


@pg
def test_port18_kandidatrad_fodes_levende(migrator):
    """Cursor P1: vakten håndhevet fødselsformen for PROSESSEN, aldri for
    raden — og payload-CHECK-en tillater den reapede formen
    (`slettet_ts NOT NULL` ∧ payload NULL), for det er formen en reapet
    rad skal ha ETTERPÅ.

    En skriver med INSERT kunne derfor føde en GRAVSTEIN på en fersk,
    umerket prosess. Den committer: `m57_lagrene_reapes_samlet` ser bare
    den reapede armen, altså ingen blanding. Fra da av er prosessen
    brent — enhver legitim fylling lager nettopp blandingen porten
    forbyr og feiler ved COMMIT, raden kan ikke slettes (DELETE forbudt)
    og ikke rettes (reapet rad er immutabel), og ett oppdrag har én
    prosess. Én INSERT tok hele evalueringsoppdraget ut av drift, for
    alltid.

    MUTASJONEN SOM DREPER DENNE: fjern `IF NEW.slettet_ts IS NOT NULL`-
    armen i `m57_kandidatlager_vakt`s INSERT-gren."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        rt.commit()
        # Gravsteinen — på et lager med payload og på et uten dokument-FK.
        # Meldingen måles, ikke bare feilklassen: `insufficient_privilege`
        # er også svaret fra de andre armene i samme vakt, og en test som
        # godtar hvilken som helst av dem ville vært grønn på feil port.
        for tabell in ("kandidat_avmaskering", "kandidat_originaldokument"):
            _sett_kontekst(rt, TENANT)
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as e:
                rt.execute(
                    f"INSERT INTO {tabell} (tenant, prosess_id, kandidat_id,"
                    f" innhold_sha256, slettet_ts)"
                    f" VALUES (%s,%s,%s,'0',now())",
                    (TENANT, pid, uuid.uuid4()))
            assert "fødes LEVENDE" in str(e.value), str(e.value)
            rt.rollback()
        # Avvisningen kommer ved INSERT, ikke først når en senere,
        # legitim fylling støter på blandingen: prosessen er uskadd.
        # Konteksten settes på nytt — `set_config(..., true)` er
        # transaksjonslokal, og rollbacken over tok den med seg.
        _sett_kontekst(rt, TENANT)
        _fyll_lagrene(rt, pid)
        rt.commit()
        assert _tell_fixtur(migrator, pid) == 9
        migrator.rollback()
    finally:
        rt.close()


@pg
def test_port18_insert_uten_tenantkontekst_avvises(migrator):
    """Cursor P1: vakten er `SECURITY DEFINER` eid av MIGRATOR, og
    `FORCE RLS` gjelder også eieren — så prosesslesningen går gjennom
    `tenant_isolasjon`, som krever at `disponit.tenant` er satt.

    `disponit_m37_claimer` har INSERT på lagrene og sin egen
    kryss-tenant-policy (`m57_reaper`), altså en vei inn UTEN
    tenantkontekst. Da er forelderen usynlig for defineren, og en vakt
    som bare spurte `IF v_slettet IS NOT NULL` leste «ingen rad» som
    «prosessen lever»: FK-en kjører ikke under RLS og claimeren ser
    forelderen, så payloaden landet på en reapet prosess — persondata
    gjenoppstått, uten noen vei til å slette dem igjen.

    MUTASJONEN SOM DREPER DENNE: fjern `IF NOT FOUND`-armen i
    `m57_kandidatlager_vakt`s INSERT-gren."""
    rt = _rt()
    rp = None
    try:
        _, pid = _prosess(migrator, rt, frist=30)
        _fyll_lagrene(rt, pid)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '31 days')", (TENANT, pid))
        rt.commit()
        rp, _timerrolle = _reaperkobling()
        rp.execute("SELECT * FROM reap_kandidatdata(50)")
        rp.commit()
        assert _tell_fixtur(migrator, pid) == 0
        migrator.rollback()
        # Claimeren, uten tenantkontekst: den ene rollen som kan skrive
        # forbi `tenant_isolasjon` er også den ene som gjør forelderen
        # usynlig for vakten.
        for tabell in LAGRE:
            migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
            migrator.execute("SELECT set_config('disponit.tenant','',true)")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                migrator.execute(
                    f"INSERT INTO {tabell} (tenant, prosess_id,"
                    f" kandidat_id, innhold_sha256) VALUES (%s,%s,%s,'0')",
                    (TENANT, pid, uuid.uuid4()))
            migrator.rollback()
        assert _tell_fixtur(migrator, pid) == 0
        migrator.rollback()
        # Positiv kontroll: med kontekst satt tar en ÅPEN prosess fortsatt
        # imot payload — armen er en synlighetsport, ikke en skrivesperre.
        _, pid2 = _prosess(migrator, rt)
        _fyll_lagrene(rt, pid2)
        rt.commit()
        assert _tell_fixtur(migrator, pid2) == 9
        migrator.rollback()
    finally:
        rt.close()
        if rp is not None:
            rp.close()


@pg
def test_port18_insert_serialiseres_mot_reaping(migrator):
    """Codex P1: vakten LESTE prosessen, men låste den ikke — og et
    snapshot fra før reaperen committet viser en levende prosess.

    Rekkefølgen som slapp payload gjennom: vakten ser levende prosess →
    FK-sjekken blokkerer på reaperens radlås → reaperen committer →
    FK-en er fortsatt oppfylt, for prosessraden BLIR stående → payloaden
    committes under en prosess som alt er merket slettet, og som
    reaperen for alltid utelukker. Med `FOR SHARE` venter vakten på
    samme sted FK-en ville ventet, og leser radens nye versjon. Samme
    kappløpsform som idempotenstesten over."""
    import threading

    rt = _rt()
    rp = None
    try:
        _, pid = _prosess(migrator, rt, frist=30)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '31 days')", (TENANT, pid))
        rt.commit()
        # A: reaperen merker prosessen og HOLDER radlåsen — ucommittet.
        rp, _timerrolle = _reaperkobling()
        assert rp.execute("SELECT count(*) FROM reap_kandidatdata(50)"
                          ).fetchone()[0] >= 1
        resultat: dict = {}

        def forsinket_skriver():
            _sett_kontekst(rt, TENANT)
            try:
                _fyll_lagrene(rt, pid)
                rt.commit()
                resultat["kom_gjennom"] = True
            except Exception as feil:
                resultat["feil"] = feil

        t = threading.Thread(target=forsinket_skriver)
        t.start()
        t.join(timeout=2)
        assert t.is_alive(), "skriveren skulle blokkere på reaperens radlås"
        rp.commit()
        t.join(timeout=10)
        assert not t.is_alive(), "skriveren sto fast etter reaperens commit"
        assert "kom_gjennom" not in resultat, \
            "payload committet under en prosess som alt var reapet"
        assert isinstance(resultat.get("feil"),
                          psycopg.errors.InsufficientPrivilege), resultat
        rt.rollback()
        assert _tell_fixtur(migrator, pid) == 0
        migrator.rollback()
    finally:
        rt.close()
        if rp is not None:
            rp.close()


@pg
def test_port19_settet_av_lagre_er_maalt_mot_katalogen(migrator):
    """Port 19s virkelige form: «alle seks» er ikke en liste noen husker,
    men en MÅLING. Fasiten er katalogens — hver tabell med FK til
    `rekrutteringsprosess` og nullable payload — og reaperens kildekode
    må nevne hver av dem. Et syvende kandidatlager uten reap-dekning
    feller denne, ikke en kodegjennomgang måneder senere."""
    fk_tabeller = {
        r[0] for r in migrator.execute(
            "SELECT c.conrelid::regclass::text FROM pg_constraint c"
            " WHERE c.confrelid = 'rekrutteringsprosess'::regclass"
            "   AND c.contype = 'f'").fetchall()}
    fk_tabeller |= {
        r[0] for r in migrator.execute(
            "SELECT c.conrelid::regclass::text FROM pg_constraint c"
            " WHERE c.confrelid = 'kandidat_originaldokument'::regclass"
            "   AND c.contype = 'f'").fetchall()}
    fk_tabeller.discard("kandidat_originaldokument")
    assert fk_tabeller | {"kandidat_originaldokument"} == set(MEDLEMMER), \
        "medlemmene i katalogen er ikke testens sju (#157: ankeret er med)"
    kilde = migrator.execute(
        "SELECT pg_get_functiondef('reap_kandidatdata(int)'::regprocedure)"
    ).fetchone()[0]
    for tabell, kolonner in MEDLEMMER.items():
        # Kvalifisert referanse, ikke delstreng (CodeRabbit): «kandidat»
        # er en delstreng av alle seks lagrene — «public.kandidat k» er
        # nøyaktig reaperens egen form for medlemmet.
        assert f"public.{tabell} k" in kilde, \
            f"reaperen nevner ikke {tabell}"
        for kol in kolonner:
            assert f"{kol} = NULL" in kilde, \
                f"reaperen nuller ikke {tabell}.{kol}"
    migrator.rollback()


@pg
def test_port19_ingen_prosess_har_halvtomme_lagre(migrator):
    """Invarianten et delvis reap ville brutt — nå målt uten å gå veien om
    prosessmerket (Cursor P2).

    Den gamle formen krevde `p.slettet_ts IS NOT NULL`, altså at ANKERET
    alt var merket. Nettopp den halvtomme tilstanden port 19 finnes for —
    ett lager reapet, resten levende, prosessen ennå umerket — slapp
    dermed gjennom. Fasiten er lagrene selv: for én prosess er ALLE
    lagerradene enten levende eller reapet, aldri begge deler.

    Målingen kjører SOM REAPERROLLEN: tabellene har FORCE ROW LEVEL
    SECURITY, så en spørring uten tenantkontekst ser null rader og en
    invariant-test over «hele basen» ville vært grønn på ingenting.
    `m57_reaper`-policyen er den ene eksplisitte kryss-tenant-veien."""
    unionen = " UNION ALL ".join(
        f"SELECT tenant, prosess_id, slettet_ts FROM {t}" for t in MEDLEMMER)
    rt = _rt()
    rp = None
    try:
        # Egen tilstand: én levende prosess og én ferdig reapet — begge
        # skal være hele, og uten dem måler invarianten en tom base.
        _, levende = _prosess(migrator, rt)
        _fyll_lagrene(rt, levende)
        _, reapet = _prosess(migrator, rt, frist=30)
        _fyll_lagrene(rt, reapet)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '31 days')", (TENANT, reapet))
        rt.commit()
        rp, _timerrolle = _reaperkobling()
        rp.execute("SELECT * FROM reap_kandidatdata(50)")
        rp.commit()

        migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
        halve = migrator.execute(
            f"SELECT tenant, prosess_id FROM ({unionen}) k"
            f" GROUP BY tenant, prosess_id"
            f" HAVING count(*) FILTER (WHERE slettet_ts IS NOT NULL) > 0"
            f"    AND count(*) FILTER (WHERE slettet_ts IS NULL) > 0"
        ).fetchall()
        # Positiv kontroll på at målingen ser noe i det hele tatt: uten
        # den er en tom base og en oppfylt invariant samme grønne test.
        begge = migrator.execute(
            f"SELECT count(*) FILTER (WHERE slettet_ts IS NULL),"
            f"       count(*) FILTER (WHERE slettet_ts IS NOT NULL)"
            f"  FROM ({unionen}) k").fetchone()
        migrator.execute("RESET ROLE")
        assert halve == [], halve
        assert begge[0] > 0 and begge[1] > 0, \
            "invarianten så hverken levende eller reapede lagerrader"
        migrator.rollback()
    finally:
        rt.close()
        if rp is not None:
            rp.close()


@pg
def test_lagervakten_avviser_alt_annet_enn_reap_overgangen(migrator):
    """Append-only med ETT unntak: payload → NULL + slettet_ts satt, i
    samme setning. Innholdsendring, hashendring og DELETE avvises — også
    for eieren."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        kid = _fyll_lagrene(rt, pid)
        rt.commit()
        _sett_kontekst(migrator, TENANT)
        for setning in (
                "UPDATE kandidat_parsettekst SET tekst='omskrevet'"
                " WHERE tenant=%s AND prosess_id=%s",
                "UPDATE kandidat_parsettekst SET tekst=NULL"
                " WHERE tenant=%s AND prosess_id=%s",  # payload uten merke
                "UPDATE kandidat_parsettekst SET innhold_sha256='0',"
                " tekst=NULL, slettet_ts=now()"
                " WHERE tenant=%s AND prosess_id=%s",  # evidens endret
                "DELETE FROM kandidat_parsettekst"
                " WHERE tenant=%s AND prosess_id=%s"):
            # Konteksten er transaksjonslokal og dør i forrige rollback —
            # uten den treffer setningen null rader og «består» tomt.
            _sett_kontekst(migrator, TENANT)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                migrator.execute(setning, (TENANT, pid))
            migrator.rollback()
        # Positiv kontroll: selve reap-overgangen GÅR for eieren …
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "UPDATE kandidat_parsettekst SET tekst=NULL, slettet_ts=now()"
            " WHERE tenant=%s AND prosess_id=%s AND kandidat_id=%s",
            (TENANT, pid, kid))
        # … og en reapet rad er død: ny UPDATE avvises.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "UPDATE kandidat_parsettekst SET slettet_ts=now()"
                " WHERE tenant=%s AND prosess_id=%s AND kandidat_id=%s",
                (TENANT, pid, kid))
        migrator.rollback()
    finally:
        rt.close()


@pg
def test_enkeltfilgrensen_star_i_basen(migrator):
    """§4: 25 MB per fil — også som CHECK, ikke bare i parseren, og målt
    på de LAGREDE bytene (Codex P2): en påstand om størrelsen er ikke en
    måling av den, så `storrelse_bytes` er bundet til `octet_length`."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        # Prosessen må BESTÅ rollbacken under: hver negative form ruller
        # tilbake, og uten commit her forsvinner forelderen med den
        # første — og neste INSERT feller på FK i stedet for på grensen.
        rt.commit()
        _sett_kontekst(rt, TENANT)
        stor_kid = uuid.uuid4()
        rt.execute("SELECT opprett_kandidat(%s,%s,%s)",
                   (TENANT, pid, stor_kid))
        with pytest.raises(psycopg.errors.CheckViolation):
            rt.execute(
                "INSERT INTO kandidat_originaldokument (tenant,"
                " prosess_id, kandidat_id, dokument_id, filnavn,"
                " innholdstype, dokument, storrelse_bytes,"
                " innhold_sha256) VALUES (%s,%s,%s,%s,'a.pdf','x',"
                " %s, 26*1024*1024, '0')",
                (TENANT, pid, stor_kid, uuid.uuid4(), b"x"))
        rt.rollback()
        # ... og en LØGN om størrelsen er like avvist: `storrelse_bytes`
        # på 1 med et dokument på 32 byte er ikke en 1-bytes fil.
        _sett_kontekst(rt, TENANT)
        logn_kid = uuid.uuid4()
        rt.execute("SELECT opprett_kandidat(%s,%s,%s)",
                   (TENANT, pid, logn_kid))
        with pytest.raises(psycopg.errors.CheckViolation):
            rt.execute(
                "INSERT INTO kandidat_originaldokument (tenant,"
                " prosess_id, kandidat_id, dokument_id, filnavn,"
                " innholdstype, dokument, storrelse_bytes,"
                " innhold_sha256) VALUES (%s,%s,%s,%s,'a.pdf','x',"
                " %s, 1, '0')",
                (TENANT, pid, logn_kid, uuid.uuid4(), b"x" * 32))
        rt.rollback()
        # Positiv kontroll: sann størrelse går.
        _sett_kontekst(rt, TENANT)
        sann_kid = uuid.uuid4()
        rt.execute("SELECT opprett_kandidat(%s,%s,%s)",
                   (TENANT, pid, sann_kid))
        rt.execute(
            "INSERT INTO kandidat_originaldokument (tenant,"
            " prosess_id, kandidat_id, dokument_id, filnavn,"
            " innholdstype, dokument, storrelse_bytes,"
            " innhold_sha256) VALUES (%s,%s,%s,%s,'a.pdf','x',"
            " %s, 32, '0')",
            (TENANT, pid, sann_kid, uuid.uuid4(), b"x" * 32))
        rt.rollback()
    finally:
        rt.close()


#: Reap-formen per lager: payloadkolonnene som skal bli NULL.
_REAP_SETNINGER = (
    ("kandidat_originaldokument",
     "dokument=NULL, filnavn=NULL, innholdstype=NULL,"
     " storrelse_bytes=NULL"),
    ("kandidat_parsettekst", "tekst=NULL"),
    ("kandidat_evalueringsartefakt", "artefakt=NULL"),
    ("kandidat_intervjusporsmal", "sporsmal=NULL"),
    ("kandidat_utsendingsdata", "mottaker_ref=NULL, flettefelt=NULL"),
    ("kandidat_avmaskering", "felter=NULL"),
    # 075 (#157): ankeret er det syvende medlemmet — ingen payload å
    # nulle, bare merket (uttrykket er en no-op-kolonne så malen står).
    ("kandidat", "kandidat_id=kandidat_id"),
)


@pg
def test_port19_ett_lager_kan_ikke_reapes_alene(migrator):
    """Cursor P2: «aldri ett lager alene» var dokumentert i reaperen, ikke
    håndhevet på skrivetidspunkt.

    Lagervakten ser ÉN RAD, og kan derfor si at reap-overgangen er lovlig
    i formen — men ikke at de seks lagrene reapes SAMMEN. Claimeren har
    UPDATE på alle seks (den MÅ, den er definer for reaperen), så direkte
    DML kunne etterlate en varig halvtom prosess: ett lager reapet, resten
    levende, ankeret uten merke. Ankervakten fanger den motsatte
    retningen; dette er den siste.

    Porten er en UTSATT constraint-trigger: den stiller spørsmålet ved
    COMMIT, når reaperens seks UPDATE-er er ferdige — og gjelder enhver
    rolle, også eieren.

    MUTASJONEN SOM DREPER DENNE: fjern
    `*_reapes_samlet`-constraint-triggerne i 057."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        _fyll_lagrene(rt, pid)
        rt.commit()
        # ETT lager alene: setningen går, COMMIT-en gjør det ikke.
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "UPDATE kandidat_parsettekst SET tekst=NULL, slettet_ts=now()"
            " WHERE tenant=%s AND prosess_id=%s", (TENANT, pid))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.commit()
        migrator.rollback()
        # Payloaden står urørt: transaksjonen ble aldri til noe.
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT count(*) FROM kandidat_parsettekst WHERE tenant=%s"
            " AND prosess_id=%s AND tekst IS NOT NULL",
            (TENANT, pid)).fetchone()[0] == 1
        # Positiv kontroll: alle seks i SAMME transaksjon — reaperens
        # egen form — går gjennom.
        for tab, payload in _REAP_SETNINGER:
            migrator.execute(
                f"UPDATE {tab} SET {payload}, slettet_ts=now()"
                " WHERE tenant=%s AND prosess_id=%s", (TENANT, pid))
        migrator.commit()
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT count(*) FROM kandidat_parsettekst WHERE tenant=%s"
            " AND prosess_id=%s AND slettet_ts IS NOT NULL",
            (TENANT, pid)).fetchone()[0] == 1
        migrator.rollback()
    finally:
        rt.close()


@pg
def test_port19_forsinket_insert_kan_ikke_lage_blandingen(migrator):
    """Codex P2: constraint-triggeren sto bare på UPDATE.

    Runde 5 begrunnet det med at «en INSERT kan ikke lage blandingen».
    Premissen holdt ikke. Porten måler BLANDINGEN, ikke merket, så en
    skriver som reaper ALLE seks lagrene i én transaksjon uten å merke
    ankeret ser ingen levende payload igjen ved COMMIT og slipper
    gjennom — og ankervakten fanger bare den motsatte retningen (merke
    uten tømte lagre). Tilstanden er altså stabil, ikke umulig; den er
    nøyaktig den positive kontrollen i testen over.

    Fra den tilstanden er lagervakten blind på riktig grunnlag: ankeret
    ER umerket, så en forsinket INSERT er lovlig for den — og resultatet
    er den varige blandingen av levende og reapet payload port 19
    forbyr.

    MUTASJONEN SOM DREPER DENNE: sett `*_reapes_samlet`-triggerne
    tilbake til `AFTER UPDATE`."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        _fyll_lagrene(rt, pid)
        rt.commit()
        # Den stabile tilstanden: alle seks reapet, ankeret UMERKET.
        _sett_kontekst(migrator, TENANT)
        for tab, payload in _REAP_SETNINGER:
            migrator.execute(
                f"UPDATE {tab} SET {payload}, slettet_ts=now()"
                " WHERE tenant=%s AND prosess_id=%s", (TENANT, pid))
        migrator.commit()
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT slettet_ts FROM rekrutteringsprosess WHERE tenant=%s"
            " AND prosess_id=%s", (TENANT, pid)).fetchone()[0] is None
        migrator.rollback()
        # Den forsinkede INSERT-en: setningen går (lagervakten ser et
        # umerket anker), COMMIT-en gjør det ikke.
        _sett_kontekst(rt, TENANT)
        _fyll_lagrene(rt, pid)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.commit()
        rt.rollback()
        # Ingen levende payload kom inn: transaksjonen ble aldri til noe.
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT count(*) FROM kandidat_originaldokument WHERE tenant=%s"
            " AND prosess_id=%s AND slettet_ts IS NULL",
            (TENANT, pid)).fetchone()[0] == 0
        migrator.rollback()
    finally:
        rt.close()


@pg
def test_port19_forste_insert_pa_fersk_prosess_gar(migrator):
    """Positiv kontroll til testen over: INSERT-armen må ikke felle den
    vanlige veien inn. En fersk prosess har ingen reapet arm å treffe, og
    hele fyllingen — seks lagre, én transaksjon — skal committe."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        _fyll_lagrene(rt, pid)
        _fyll_lagrene(rt, pid)
        rt.commit()
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT count(*) FROM kandidat_originaldokument WHERE tenant=%s"
            " AND prosess_id=%s AND slettet_ts IS NULL",
            (TENANT, pid)).fetchone()[0] == 2
        migrator.rollback()
    finally:
        rt.close()


@pg
def test_port19_maaler_ogsaa_naar_konteksten_er_borte_ved_commit(migrator):
    """Codex P1: porten kjørte UTSATT, men leste som INVOKER.

    Den utsatte constraint-triggeren stiller spørsmålet sitt ved COMMIT —
    og ved COMMIT er skriverens rolle og skriverens tenant-kontekst ikke
    nødvendigvis den samme som da UPDATE-en gikk. `reap_kandidatdata` er
    SECURITY DEFINER og nullstiller `disponit.tenant` før den returnerer,
    så som invoker leste porten med tom kontekst gjennom
    `tenant_isolasjon`: null rader, ingen blanding, commit. En vakt som
    aldri så noe kan ikke felle noe.

    (Driftsformens andre ende — timerrollen `disponit_domener`, som har
    EXECUTE på reaperen og ingenting på lagrene, får `permission denied`
    ved COMMIT og ruller hele reapen tilbake — krever den rollen og måles
    på verten, ikke her. Blindheten kan måles i ETHVERT oppsett, og det
    er nøyaktig samme rotårsak.)

    Porten måler derfor egenskapen direkte: den halvtomme prosessen skal
    avvises ved COMMIT selv når konteksten som gjorde radene synlige er
    nullstilt i samme transaksjon.

    MUTASJONEN SOM DREPER DENNE: ta `SECURITY DEFINER` av
    `m57_lagrene_reapes_samlet` (eller flytt den ut av claimer-blokka, så
    den eies av migrator igjen)."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        _fyll_lagrene(rt, pid)
        rt.commit()
        # ETT lager alene — og så forsvinner konteksten før COMMIT,
        # nøyaktig slik reaperen nullstiller sin egen.
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "UPDATE kandidat_parsettekst SET tekst=NULL, slettet_ts=now()"
            " WHERE tenant=%s AND prosess_id=%s", (TENANT, pid))
        migrator.execute("SELECT set_config('disponit.tenant','',true)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.commit()
        migrator.rollback()
        # Payloaden står urørt.
        assert _tell_fixtur(migrator, pid) == 9
        migrator.rollback()
    finally:
        rt.close()


@pg
def test_port19_porten_eies_av_claimeren_og_er_definer(migrator):
    """Speilet til testen over, målt på selve funksjonen: den utsatte
    porten må lese med claimerens `m57_reaper`-policy uansett hvem som
    committer. Eierskapet ER lesetilgangen — en migrator-eid invoker-port
    er blind for den ene rollen som faktisk kjører reapen i drift."""
    eier, definer = migrator.execute(
        "SELECT pg_get_userbyid(proowner), prosecdef FROM pg_proc"
        " WHERE proname = 'm57_lagrene_reapes_samlet'").fetchone()
    assert eier == "disponit_m37_claimer", eier
    assert definer is True


@pg
def test_innhold_sha256_utledes_av_payloaden_ikke_av_kalleren(migrator):
    """Codex P2: hashen var kallerens PÅSTAND om innholdet.

    `innhold_sha256` er den eneste evidensen som består etter reaping —
    payloaden blir NULL, og raden står igjen som revisjonsspor. Ingen
    CHECK og ingen trigger målte den mot `dokument`/`tekst`/`artefakt`,
    så en skriver som satte tom, feil eller fremmed streng korrumperte
    sporet PERMANENT: reap-overgangen er den eneste lovlige UPDATE, og
    resten av raden er immutabel, så det finnes ingen vei til å rette
    det igjen.

    Porten måler EGENSKAPEN, ikke formelen: kallerens verdi overlever
    ikke, lik payload gir lik hash, og ulik payload gir ulik.

    MUTASJONEN SOM DREPER DENNE: fjern `NEW.innhold_sha256 := ...` fra
    INSERT-grenen i `m57_kandidatlager_vakt`."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        _sett_kontekst(rt, TENANT)
        kid_a, kid_b, kid_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        logn = "0" * 64
        for kid, tekst in ((kid_a, "samme tekst"), (kid_b, "samme tekst"),
                           (kid_c, "en annen tekst")):
            did = uuid.uuid4()
            rt.execute("SELECT opprett_kandidat(%s,%s,%s)",
                       (TENANT, pid, kid))
            rt.execute(
                "INSERT INTO kandidat_originaldokument (tenant,"
                " prosess_id, kandidat_id, dokument_id, filnavn,"
                " innholdstype, dokument, storrelse_bytes,"
                " innhold_sha256) VALUES (%s,%s,%s,%s,'a.pdf','x',"
                " %s,%s,%s)",
                (TENANT, pid, kid, did, b"x", 1, logn))
            rt.execute(
                "INSERT INTO kandidat_parsettekst (tenant, prosess_id,"
                " kandidat_id, dokument_id, tekst, innhold_sha256)"
                " VALUES (%s,%s,%s,%s,%s,%s)",
                (TENANT, pid, kid, did, tekst, logn))
        rader = dict(rt.execute(
            "SELECT kandidat_id::text, innhold_sha256 FROM"
            " kandidat_parsettekst WHERE prosess_id=%s",
            (pid,)).fetchall())
        assert len(rader) == 3
        assert logn not in rader.values(), \
            "kallerens påstand overlevde — hashen er ikke utledet"
        for sha in rader.values():
            assert len(sha) == 64 and set(sha) <= set("0123456789abcdef")
        assert rader[str(kid_a)] == rader[str(kid_b)], \
            "samme payload må gi samme hash"
        assert rader[str(kid_a)] != rader[str(kid_c)], \
            "ulik payload må gi ulik hash"
        # ... og den overlever reapingen, som revisjonsevidensen den er.
        beholdt = rader[str(kid_c)]
        rt.commit()
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "UPDATE kandidat_parsettekst SET tekst=NULL, slettet_ts=now()"
            " WHERE tenant=%s AND prosess_id=%s AND kandidat_id=%s",
            (TENANT, pid, kid_c))
        assert migrator.execute(
            "SELECT innhold_sha256 FROM kandidat_parsettekst WHERE"
            " tenant=%s AND prosess_id=%s AND kandidat_id=%s",
            (TENANT, pid, kid_c)).fetchone()[0] == beholdt
        migrator.rollback()
    finally:
        rt.close()


@pg
def test_opprettet_settes_av_basen_ikke_av_kalleren(migrator):
    """Codex P2: `opprettet` var kallerens påstand om NÅR raden ble til.

    DEFAULT now() gjelder bare når kolonnen utelates, og runtime har
    INSERT rett på de seks tabellene — den er ikke tvunget gjennom
    defaulten. `opprettet` er ikke payload, så den overlever reapingen
    som revisjonsevidens, og UPDATE-vakten gjør den immutabel: et falskt
    eller feil tidspunkt ble dermed PERMANENT, ved siden av et innhold
    som er målt. Da er «hva ble skrevet, når» halvveis evidens.

    Porten måler EGENSKAPEN: kallerens verdi overlever ikke, verken
    frem eller bakover i tid — til forskjell fra ANKERET, der en fødsel
    bakover er lovlig fordi den KORTER levetiden.

    MUTASJONEN SOM DREPER DENNE: fjern `NEW.opprettet := pg_catalog.now()`
    fra INSERT-grenen i `m57_kandidatlager_vakt`."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        _sett_kontekst(rt, TENANT)
        kid_frem, kid_bak = uuid.uuid4(), uuid.uuid4()
        for kid, forskyvning in ((kid_frem, "+ interval '400 days'"),
                                 (kid_bak, "- interval '400 days'")):
            rt.execute("SELECT opprett_kandidat(%s,%s,%s)",
                       (TENANT, pid, kid))
            rt.execute(
                "INSERT INTO kandidat_evalueringsartefakt (tenant,"
                " prosess_id, kandidat_id, artefakt, innhold_sha256,"
                f" opprettet) VALUES (%s,%s,%s,'{{}}','0',"
                f" now() {forskyvning})",
                (TENANT, pid, kid))
        rader = dict(rt.execute(
            "SELECT kandidat_id::text, opprettet FROM"
            " kandidat_evalueringsartefakt WHERE prosess_id=%s", (pid,)
        ).fetchall())
        naa, ett_dogn = rt.execute(
            "SELECT now(), interval '1 day'").fetchone()
        for kid in (kid_frem, kid_bak):
            avvik = abs(rader[str(kid)] - naa)
            assert avvik < ett_dogn, \
                (f"kallerens tidspunkt overlevde ({rader[str(kid)]}) —"
                 " opprettet er ikke utledet av basen")
        rt.rollback()
    finally:
        rt.close()


@pg
def test_reap_utvalget_er_indeksert_paa_de_ureapede(migrator):
    """Codex P2: timerens utvalg hadde ingen indeks.

    Prosessraden BESTÅR reapingen — den er evidensen om at dataene ble
    slettet — så tabellen vokser monotont med all historisk bruk, mens de
    ureapede alltid er en liten hale. Uten indeks betalte timeren hvert
    femte minutt et fullt skann pluss en sortering av den halen, og
    kostnaden vokste med bruk som for lengst var slettet. Det er
    sletteFRISTEN som glipper til slutt: reaperen tar 50 rader per kall.

    Porten måler KOBLINGEN, ikke indeksnavnet: det må finnes en indeks på
    ankeret hvis partielle predikat er reaperens eget «ennå ikke reapet»,
    og hvis nøkkeluttrykk er den enden reaperen både filtrerer og
    sorterer på. Endrer reaperen uttrykket sitt, faller denne — det er
    hele poenget, for da er indeksen ubrukelig uten at noe annet sier fra.

    MUTASJONEN SOM DREPER DENNE: fjern `CREATE INDEX
    rekrutteringsprosess_ureapet_frist` fra 057, eller gjør den total ved
    å droppe `WHERE slettet_ts IS NULL`."""
    kilde = migrator.execute(
        "SELECT pg_get_functiondef('reap_kandidatdata(int)'::regprocedure)"
    ).fetchone()[0]
    assert "coalesce(p.lukket_ts, p.opprettet)" in kilde, \
        "reaperen regner ikke lenger fra coalesce(lukket_ts, opprettet)"
    assert "p.slettet_ts IS NULL" in kilde, \
        "reaperen velger ikke lenger på «ennå ikke reapet»"
    indekser = migrator.execute(
        "SELECT pg_get_expr(i.indpred, i.indrelid),"
        "       pg_get_expr(i.indexprs, i.indrelid)"
        "  FROM pg_index i"
        " WHERE i.indrelid = 'rekrutteringsprosess'::regclass"
        "   AND i.indpred IS NOT NULL").fetchall()
    treff = [(pred, uttrykk) for pred, uttrykk in indekser
             if pred is not None and "slettet_ts IS NULL" in pred]
    assert treff, \
        ("ingen PARTIELL indeks på de ureapede prosessene — timeren"
         f" skanner hele historikken. Partielle indekser: {indekser}")
    assert any(uttrykk is not None
               and "COALESCE" in uttrykk.upper()
               and "lukket_ts" in uttrykk and "opprettet" in uttrykk
               for _pred, uttrykk in treff), \
        ("den partielle indeksen bærer ikke fristbasen"
         f" coalesce(lukket_ts, opprettet): {treff}")
    migrator.rollback()


@pg
def test_kandidatlagrene_er_tenantisolert(migrator):
    """RLS-porten: en annen tenants kontekst ser ingen rader, og en
    INSERT på fremmed tenant avvises."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        _fyll_lagrene(rt, pid)
        rt.commit()
        _sett_kontekst(rt, ANNEN_TENANT)
        assert rt.execute(
            "SELECT count(*) FROM kandidat_parsettekst WHERE prosess_id=%s",
            (pid,)).fetchone()[0] == 0
        with pytest.raises(psycopg.errors.Error):
            rt.execute(
                "INSERT INTO kandidat_avmaskering (tenant, prosess_id,"
                " kandidat_id, felter, innhold_sha256)"
                " VALUES (%s,%s,%s,'{}','0')",
                (TENANT, pid, uuid.uuid4()))
        rt.rollback()
    finally:
        rt.close()


def _promotert_rapport(m, oid, *, tilstand="promotert"):
    """Et artefakt i en payloadbærende retained-tilstand på PROSESSENS
    oppdrag — bygget med 014b-riggens ekte form (kontrakt, type,
    release, strukturelt dekrypterbar payload), aldri en FK-omgåelse.
    Promoteringen tas som tabelleier gjennom statemaskinen
    (staged → promotert/bevart/karantene er lovlige overganger);
    kapabilitetsmaskineriet er 014b-testenes eget bevisområde."""
    from .test_pr014b_domene_artefakt import _artefakt, _artefakttype
    at = f"at.t{secrets.token_hex(4)}.rapport"
    modul = "m-" + secrets.token_hex(4)
    kh = "k-" + secrets.token_hex(8)
    _artefakttype(m, modul, kh, at)
    aid = _artefakt(m, TENANT, oid, at, modul, kh)
    _sett_kontekst(m, TENANT)
    m.execute("UPDATE artefakt SET tilstand=%s WHERE artefakt_id=%s",
              (tilstand, aid))
    m.commit()
    return aid


def _artefaktrad(m, aid):
    _sett_kontekst(m, TENANT)
    rad = m.execute(
        "SELECT tilstand, ciphertext IS NULL, nonce IS NULL,"
        " makulert_ts IS NOT NULL, klartekst_sha256 IS NOT NULL"
        " FROM artefakt WHERE artefakt_id=%s", (aid,)).fetchone()
    m.rollback()
    return rad


@pg
def test_222_reaping_makulerer_den_promoterte_rapporten(migrator):
    """#222 (andre halvdel av Codex P1-2 på #220): `reap_kandidatdata`
    nullet de seks lagrene og merket prosessen — men rørte aldri
    `artefakt`. Den promoterte rapporten bærer funn, intervjuspørsmål og
    hele den blindede kildeteksten per kandidat, og besto forbi
    retensjonsfristen kunden kjøpte. #220 lukket lesesiden; dette er
    selve makuleringen, i SAMME iterasjon og transaksjon som lagrene.

    `bevart` og `karantene` makuleres av samme grunn som `promotert`:
    §5-fristen ser payloaden, ikke tilstandsmaskinen vår. Tilstanden og
    hashen består — raden er fortsatt evidensen om at rapporten fantes.

    MUTASJONEN SOM DREPER DENNE: fjern
    `makuler_artefakter_for_prosess`-kallet fra `reap_kandidatdata` —
    alle port 18-testene er grønne, for de måler bare de seks lagrene."""
    rt = _rt()
    rp = None
    try:
        oid, pid = _prosess(migrator, rt, frist=30)
        _fyll_lagrene(rt, pid)
        aid = _promotert_rapport(migrator, oid)
        kid = _promotert_rapport(migrator, oid, tilstand="karantene")
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '31 days')", (TENANT, pid))
        rt.commit()
        for a in (aid, kid):
            rad = _artefaktrad(migrator, a)
            assert rad[1] is False and rad[2] is False and rad[3] is False, \
                f"positiv kontroll: payloaden skal stå før reaping: {rad}"
        rp, _timerrolle = _reaperkobling()
        reapet = rp.execute("SELECT * FROM reap_kandidatdata(50)"
                            ).fetchall()
        rp.commit()
        assert (TENANT, pid) in [(r[0], r[1]) for r in reapet]
        for a, tilstand in ((aid, "promotert"), (kid, "karantene")):
            rad = _artefaktrad(migrator, a)
            assert rad == (tilstand, True, True, True, True), \
                (f"makuleringen skal nulle payloaden og sette merket,"
                 f" og la tilstand + hash bestå: {rad}")
    finally:
        rt.close()
        if rp is not None:
            rp.close()


@pg
def test_222_makuleringen_er_en_navngitt_form(migrator):
    """Statemaskinporten: nulling av payload UTEN merket er fortsatt
    korrupsjon, merket UTEN nulling er fortsatt en løgn, og merket
    settes én gang — det kan hverken fjernes eller flyttes etterpå.

    MUTASJONEN SOM DREPER DENNE: fjern makulert_ts-armen i
    `artefakt_statemaskin` (066) — da er den første UPDATE-en under
    stille korrupsjon i stedet for en avvisning."""
    rt = _rt()
    try:
        oid, _pid = _prosess(migrator, rt, frist=30)
        rt.commit()
        aid = _promotert_rapport(migrator, oid)
        _sett_kontekst(migrator, TENANT)
        # Nulling uten merke: den gamle korrupsjonsklassen, fortsatt rød.
        with pytest.raises(psycopg.errors.RaiseException):
            migrator.execute(
                "UPDATE artefakt SET ciphertext=NULL, nonce=NULL"
                " WHERE artefakt_id=%s", (aid,))
        migrator.rollback()
        # Merke uten nulling: løgnen andre veien.
        _sett_kontekst(migrator, TENANT)
        with pytest.raises(psycopg.errors.RaiseException):
            migrator.execute(
                "UPDATE artefakt SET makulert_ts=now()"
                " WHERE artefakt_id=%s", (aid,))
        migrator.rollback()
        # Den lovlige formen: begge nulles og merket settes, i ETT update.
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "UPDATE artefakt SET ciphertext=NULL, nonce=NULL,"
            " makulert_ts=now() WHERE artefakt_id=%s", (aid,))
        migrator.commit()
        # Merket er satt én gang: aldri fjernet, aldri flyttet.
        for ny in ("NULL", "now() + interval '1 day'"):
            _sett_kontekst(migrator, TENANT)
            with pytest.raises(psycopg.errors.RaiseException):
                migrator.execute(
                    f"UPDATE artefakt SET makulert_ts={ny}"
                    f" WHERE artefakt_id=%s", (aid,))
            migrator.rollback()
    finally:
        rt.close()


@pg
def test_173_skriveveien_er_claimbundet_og_idempotent(migrator, miljo):
    """#173 (eiers valg b + i): skriveveien inn i kandidatlagrene.

    Måler hele kontrakten i én kjede: (1) et gyldig claim-par skriver
    originaldokument + parsettekst i samme kall (FK-kjeden — valg i) og
    evalueringsartefakt i sitt; (2) identitetene er DØRENS og
    deterministiske (valg b) — samme kall to ganger gir samme UUID-er og
    ingen ny rad, og kandidat-UUID-en er den samme på tvers av de to
    rutene; (3) et avvikende re-skriv under samme nøkkel felles som
    `kandidatdata_konflikt` — på dokumentbytene, på parsetteksten under
    SAMME byte, og i kandidatgrenen på både artefaktet og
    avmaskeringskartet; (4) feil claim-par og reapet anker er samme
    `kandidatdata_avvist` — et oppslagsverk over claims skal ikke
    finnes.

    MUTASJONEN SOM DREPER DENNE: fjern claim-leddet i dørens
    radoppslag, eller bytt ON CONFLICT-likhetsmålingen med et stille ja.
    """
    import base64

    from starlette.testclient import TestClient
    from api.app import lag_app
    from .test_api import DSN as API_DSN, dekker  # noqa: F401
    from .test_bestilling_rekruttering import _sikre_m57_claimbar
    from .test_modul_onboarding_http import _onboard_token
    from miljo import gjeldende_miljo

    _sikre_m57_claimbar(migrator)
    rel = migrator.execute(
        "SELECT release_id FROM moduldeployment"
        " WHERE modul_id='m57_ats' AND miljo=%s AND livslop='claiming'"
        " LIMIT 1", (gjeldende_miljo(),)).fetchone()[0]
    migrator.commit()

    rt = _rt()
    try:
        # `frist=30` FORDI (4b) FAKTISK SKAL REAPE. Kjeden lukket
        # prosessen 31 døgn tilbake, men opprettet den med default
        # `frist=90`: reaperens predikat er
        # `now() > lukket_ts + slettefrist_dogn`, altså
        # `now() > now() + 59 døgn` — usant. `reap_kandidatdata` plukket
        # aldri raden, `p.slettet_ts` forble NULL, og dørens
        # claim-oppslag (som krever `p.slettet_ts IS NULL`) slapp
        # skrivet inn i en prosess forbi kundens frist: (4b) svarte 200
        # der den påsto å måle `kandidatdata_avvist`. Armen målte altså
        # ingenting, og CI sto rød på den fra `b0365a7`. Samme paring
        # som de fem andre reap-testene i fila: `frist=30` + 31 døgn.
        oid, pid = _prosess(migrator, rt, frist=30)
        # `_prosess` COMMITER IKKE — kalleren gjør det, og her må den.
        # To grunner, og begge er harde: (1) fødselsvakten i 057 tar
        # `FOR SHARE` på oppdragsraden og HOLDER den til transaksjonen
        # slutter, så migrator-UPDATE-en under ville stått og ventet på
        # en `rt` som aldri committer — testen hang, den feilet ikke;
        # (2) døren er en EGEN forbindelse, og en ucommittet
        # `rekrutteringsprosess` finnes ikke i dens JOIN, så hvert kall
        # ville svart `kandidatdata_avvist` av feil grunn.
        rt.commit()
        # Claim-paret settes på raden (kolonnelåsen tillater owner- og
        # statusfeltene) — riggen er claim-tilstanden, ikke claim-veien:
        # det som måles her er DØRENS binding til den.
        _sett_kontekst(migrator, TENANT)
        # DEPLOYMENTEN STEMPLES OGSÅ (Codex P1). Claim-porten skriver
        # `claim_release_id`/`claim_miljo` med den deploymenten den
        # verifiserte (049:294), og døren binder nå mot dem; en rigg som
        # lot dem stå NULL ville målt en tilstand claim-døren aldri
        # produserer. Kolonnene er claim-vaktens (049:98) og kan KUN
        # settes av `disponit_m37_claimer` — samme form som `_pluk` i
        # resolvertestene.
        migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
        migrator.execute(
            "UPDATE oppdrag SET owner_claim_id=%s, owner_generation=1,"
            " owner_lease_utloper=now()+interval '10 minutes',"
            " claim_release_id=%s, claim_miljo=%s"
            " WHERE tenant=%s AND id=%s",
            ("c" * 22, rel, gjeldende_miljo(), TENANT, oid))
        migrator.execute("RESET ROLE")
        migrator.commit()

        a = lag_app(DSN)
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, "m57_ats", rel)
            hode = {"authorization": f"Bearer {mtk}"}
            trippel = {"tenant": TENANT, "oppdrag_id": oid,
                       "owner_claim_id": "c" * 22,
                       "owner_generation": 1}
            dok = {**trippel, "kandidat_id": "k1",
                   "dokumentnavn": "k1/cv.pdf",
                   "dokument_b64": base64.b64encode(
                       b"%PDF-1.7 " + FIXTUR.encode()).decode(),
                   "tekst": f"CV-tekst {FIXTUR}"}
            r1 = c.post("/v1/rekruttering/kandidatdokument",
                        json=dok, headers=hode)
            assert r1.status_code == 200, r1.text
            kid_uuid = r1.json()["kandidat_id"]
            did = r1.json()["dokument_id"]
            # Idempotent: samme byte, samme dør, samme identiteter.
            r2 = c.post("/v1/rekruttering/kandidatdokument",
                        json=dok, headers=hode)
            assert r2.status_code == 200, r2.text
            assert r2.json()["kandidat_id"] == kid_uuid
            assert r2.json()["dokument_id"] == did

            # `avmaskering` er KREVD på denne veien (Codex P1): uten
            # kartet er den blindede `kildetekst` over lagret med tokener
            # ingen autorisert leser kan løse opp, og et VALGFRITT felt
            # ville gitt nøyaktig den stille ikke-lagringen funnet gjaldt.
            art = {**trippel, "kandidat_id": "k1",
                   "artefakt": {"funn": [], "oppfylt": {"krav": True},
                                "kildetekst": f"[NAVN-1] {FIXTUR}"},
                   "avmaskering": {"[NAVN-1]": f"Kari {FIXTUR}"},
                   "intervjusporsmal": None}
            r3u = c.post("/v1/rekruttering/kandidatartefakt",
                         json={k: v for k, v in art.items()
                               if k != "avmaskering"}, headers=hode)
            assert r3u.status_code == 400, r3u.text
            assert r3u.json()["feil"] == "request_feilformet"
            r3 = c.post("/v1/rekruttering/kandidatartefakt",
                        json=art, headers=hode)
            assert r3.status_code == 200, r3.text
            assert r3.json()["kandidat_id"] == kid_uuid, \
                "kandidat-UUID-en skal være dørens ENE utledning (valg b)"

            # (3) Avvikende re-skriv: to sannheter committes aldri.
            r4 = c.post("/v1/rekruttering/kandidatdokument",
                        json={**dok, "dokument_b64": base64.b64encode(
                            b"%PDF-1.7 noe-annet").decode()},
                        headers=hode)
            assert r4.status_code == 409, r4.text
            assert r4.json()["feil"] == "kandidatdata_konflikt"

            # (3b) SAMME dokument, ANNEN parsettekst (Cursor P2-5). Den
            # armen var ubevist, og den er den sannsynlige: bytene er
            # arkivets og endrer seg ikke, mens teksten kommer fra en
            # uttrekker som kan bytte versjon mellom to retries. Da er
            # `ON CONFLICT DO NOTHING` på originaldokumentet et stille ja
            # — likhetsmålingen slipper det gjennom — og bare
            # parsettekstens egen måling står igjen mellom to sannheter
            # om samme dokument.
            r4b = c.post("/v1/rekruttering/kandidatdokument",
                         json={**dok, "tekst": f"ANNEN tekst {FIXTUR}"},
                         headers=hode)
            assert r4b.status_code == 409, r4b.text
            assert r4b.json()["feil"] == "kandidatdata_konflikt"

            # (3c) KANDIDATGRENEN HAR SAMME LØFTE (Cursor P2-4).
            # Konflikten var kun bevist på dokumentveien, så
            # `kandidatdata_konflikt` for artefakt og avmaskering var to
            # ubeviste armer i den grenen som faktisk bærer evalueringen.
            # Endret `kildetekst` = endret artefakt:
            r4c = c.post("/v1/rekruttering/kandidatartefakt",
                         json={**art, "artefakt": {
                             **art["artefakt"],
                             "kildetekst": f"[NAVN-1] ANNEN {FIXTUR}"}},
                         headers=hode)
            assert r4c.status_code == 409, r4c.text
            assert r4c.json()["feil"] == "kandidatdata_konflikt"

            # (3d) IDENTISK artefakt, ANNET kart. Den er den viktigste av
            # de to: artefakt-INSERT-en er da et stille ja
            # (`ON CONFLICT DO NOTHING` + likhetsmåling), så bare
            # avmaskeringens egen måling står mellom to nøkler til samme
            # blindede tekst — og feil nøkkel løser opp til feil person.
            r4d = c.post("/v1/rekruttering/kandidatartefakt",
                         json={**art, "avmaskering": {
                             "[NAVN-1]": f"Ola {FIXTUR}"}},
                         headers=hode)
            assert r4d.status_code == 409, r4d.text
            assert r4d.json()["feil"] == "kandidatdata_konflikt"

            # (4a) Feil claim-par: ETT svar.
            r5 = c.post("/v1/rekruttering/kandidatdokument",
                        json={**dok, "owner_claim_id": "x" * 22},
                        headers=hode)
            assert r5.status_code == 409, r5.text
            assert r5.json()["feil"] == "kandidatdata_avvist"

            # Radene: nøyaktig én per lager, payload intakt, FK-kjeden hel.
            _sett_kontekst(migrator, TENANT)
            rader = migrator.execute(
                "SELECT (SELECT count(*) FROM kandidat_originaldokument"
                "         WHERE tenant=%s AND prosess_id=%s),"
                " (SELECT count(*) FROM kandidat_parsettekst"
                "         WHERE tenant=%s AND prosess_id=%s),"
                " (SELECT count(*) FROM kandidat_evalueringsartefakt"
                "         WHERE tenant=%s AND prosess_id=%s),"
                " (SELECT count(*) FROM kandidat_avmaskering"
                "         WHERE tenant=%s AND prosess_id=%s)",
                (TENANT, pid) * 4).fetchone()
            assert rader == (1, 1, 1, 1), rader
            tekst = migrator.execute(
                "SELECT tekst FROM kandidat_parsettekst"
                " WHERE tenant=%s AND prosess_id=%s",
                (TENANT, pid)).fetchone()[0]
            # Kartet løser opp nøyaktig tokenet den lagrede kildeteksten
            # bærer — lageret 057 definerer for det, skrevet i SAMME
            # transaksjon som artefaktet.
            felter = migrator.execute(
                "SELECT felter FROM kandidat_avmaskering"
                " WHERE tenant=%s AND prosess_id=%s",
                (TENANT, pid)).fetchone()[0]
            migrator.rollback()
            assert FIXTUR in tekst
            assert felter == {"[NAVN-1]": f"Kari {FIXTUR}"}, felter

            # (5) INTERVJUSPØRSMÅL: ABSENS LAGRES IKKE, MEN MÅLES
            # (Codex P2, runde 2 på samme arm). Cursor P2-2 gjorde
            # «ingen spørsmål» til en LAGRET sannhet — `[]` skrevet
            # ubetinget — fordi et stille hopp over lageret ikke måler
            # noen divergens. Men lageret er append-only med
            # `(tenant, prosess_id, kandidat_id)` som primærnøkkel og
            # ingen UPDATE av payload, og hver evaluering produserer med
            # vilje null spørsmål (#225): plassen innkallings-/
            # shortlist-steget skal skrive i ble dermed permanent
            # okkupert av tomhet for HVER kandidat.
            #
            # Begge kravene måles her: ingen rad når det ikke er noe å
            # lagre, og fortsatt konflikt når en lagret liste motsies.
            # Står ETTER radtellingen over, som er en port på nøyaktig
            # én rad per lager for k1.
            #
            # (5a) r3 skrev `intervjusporsmal: null`. Da står det INGEN
            # rad — absens er ikke materialisert.
            _sett_kontekst(migrator, TENANT)
            antall = migrator.execute(
                "SELECT count(*) FROM kandidat_intervjusporsmal"
                " WHERE tenant=%s AND prosess_id=%s"
                "   AND kandidat_id=%s::uuid",
                (TENANT, pid, kid_uuid)).fetchone()[0]
            migrator.rollback()
            assert antall == 0, antall

            # (5b) …og nettopp derfor kan en liste skrives etterpå: det
            # er en FØRSTE sannhet under nøkkelen, ikke en motsigelse.
            # Dette er flyten funnet handler om — uten (5a) svarte
            # døren 409 her, og lageret var stengt for det steget det
            # er utpekt som kilde for.
            r7 = c.post("/v1/rekruttering/kandidatartefakt",
                        json={**art, "intervjusporsmal": ["Hvorfor?"]},
                        headers=hode)
            assert r7.status_code == 200, r7.text
            _sett_kontekst(migrator, TENANT)
            sp = migrator.execute(
                "SELECT sporsmal FROM kandidat_intervjusporsmal"
                " WHERE tenant=%s AND prosess_id=%s"
                "   AND kandidat_id=%s::uuid",
                (TENANT, pid, kid_uuid)).fetchone()
            migrator.rollback()
            assert sp is not None and sp[0] == ["Hvorfor?"], sp

            # (5c) Den STILLE retningen er fortsatt stengt, på egen
            # kandidat: liste først, `null` etterpå. Armen skriver ikke,
            # men den MÅLER — og en lagret liste som motsies er den
            # samme `kandidatdata_konflikt` som de to lagrene over.
            art2 = {**art, "kandidat_id": "k2",
                    "intervjusporsmal": ["Hvorfor?"]}
            r8 = c.post("/v1/rekruttering/kandidatartefakt",
                        json=art2, headers=hode)
            assert r8.status_code == 200, r8.text
            r9 = c.post("/v1/rekruttering/kandidatartefakt",
                        json={**art2, "intervjusporsmal": None},
                        headers=hode)
            assert r9.status_code == 409, r9.text
            assert r9.json()["feil"] == "kandidatdata_konflikt"

            # (4b) Reapet anker: samme avvisning — døren skriver aldri
            # inn i en prosess forbi kundens frist.
            _sett_kontekst(rt, TENANT)
            rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                       " now() - interval '31 days')", (TENANT, pid))
            rt.commit()
            rp, _timer = _reaperkobling()
            try:
                rp.execute("SELECT * FROM reap_kandidatdata(50)")
                rp.commit()
            finally:
                rp.close()
            r6 = c.post("/v1/rekruttering/kandidatartefakt",
                        json=art, headers=hode)
            assert r6.status_code == 409, r6.text
            assert r6.json()["feil"] == "kandidatdata_avvist"
    finally:
        rt.close()


@pg
def test_173_doed_lease_stenger_doren_midt_i_stroemmen(migrator, miljo):
    """#173 (Cursor P2-5): leasen dør MENS strømmen går.

    Kjedetesten måler feil claim-par og reapet anker. Dette er den
    tredje veien inn i samme avvisning, og den som faktisk skjer i
    drift: claimet er riktig, ankeret lever, men heartbeatet er tapt og
    `owner_lease_utloper` har passert. Fencing-leddet i døren er det
    eneste som står igjen — en utfører uten levende lease er ikke
    lenger oppdragets utfører, og skal ikke få skrive det neste
    dokumentet inn i prosessen.

    Riggen er strømmen: ETT dokument lander på levende lease, så dør
    leasen, så kommer det neste. Å måle bare det andre kallet ville
    ikke skilt «leasen var død» fra «riggen var feil».

    MUTASJONEN SOM DREPER DENNE: fjern
    `owner_lease_utloper > now()`-leddet i dørens radoppslag."""
    import base64

    from starlette.testclient import TestClient
    from api.app import lag_app
    from .test_bestilling_rekruttering import _sikre_m57_claimbar
    from .test_modul_onboarding_http import _onboard_token
    from miljo import gjeldende_miljo

    _sikre_m57_claimbar(migrator)
    rel = migrator.execute(
        "SELECT release_id FROM moduldeployment"
        " WHERE modul_id='m57_ats' AND miljo=%s AND livslop='claiming'"
        " LIMIT 1", (gjeldende_miljo(),)).fetchone()[0]
    migrator.commit()

    rt = _rt()
    try:
        oid, pid = _prosess(migrator, rt)
        rt.commit()                     # jf. fødselsvaktens `FOR SHARE`
        _sett_kontekst(migrator, TENANT)
        # DEPLOYMENTEN STEMPLES OGSÅ (Codex P1). Claim-porten skriver
        # `claim_release_id`/`claim_miljo` med den deploymenten den
        # verifiserte (049:294), og døren binder nå mot dem; en rigg som
        # lot dem stå NULL ville målt en tilstand claim-døren aldri
        # produserer. Kolonnene er claim-vaktens (049:98) og kan KUN
        # settes av `disponit_m37_claimer` — samme form som `_pluk` i
        # resolvertestene.
        migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
        migrator.execute(
            "UPDATE oppdrag SET owner_claim_id=%s, owner_generation=1,"
            " owner_lease_utloper=now()+interval '10 minutes',"
            " claim_release_id=%s, claim_miljo=%s"
            " WHERE tenant=%s AND id=%s",
            ("c" * 22, rel, gjeldende_miljo(), TENANT, oid))
        migrator.execute("RESET ROLE")
        migrator.commit()

        a = lag_app(DSN)
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, "m57_ats", rel)
            hode = {"authorization": f"Bearer {mtk}"}
            dok = {"tenant": TENANT, "oppdrag_id": oid,
                   "owner_claim_id": "c" * 22, "owner_generation": 1,
                   "kandidat_id": "k1", "dokumentnavn": "k1/cv.pdf",
                   "dokument_b64": base64.b64encode(
                       b"%PDF-1.7 " + FIXTUR.encode()).decode(),
                   "tekst": f"CV-tekst {FIXTUR}"}
            r1 = c.post("/v1/rekruttering/kandidatdokument",
                        json=dok, headers=hode)
            assert r1.status_code == 200, r1.text

            # Heartbeatet er tapt: leasen har passert. Claim-paret og
            # ankeret er UENDRET — det er nettopp poenget.
            _sett_kontekst(migrator, TENANT)
            migrator.execute(
                "UPDATE oppdrag SET owner_lease_utloper=now()"
                " - interval '1 second' WHERE tenant=%s AND id=%s",
                (TENANT, oid))
            migrator.commit()

            r2 = c.post("/v1/rekruttering/kandidatdokument",
                        json={**dok, "dokumentnavn": "k1/attest.pdf"},
                        headers=hode)
            assert r2.status_code == 409, r2.text
            assert r2.json()["feil"] == "kandidatdata_avvist"
            # Artefaktveien står i samme dør.
            r3 = c.post("/v1/rekruttering/kandidatartefakt",
                        json={"tenant": TENANT, "oppdrag_id": oid,
                              "owner_claim_id": "c" * 22,
                              "owner_generation": 1, "kandidat_id": "k1",
                              "artefakt": {"funn": [], "oppfylt": {},
                                           "kildetekst": "x"},
                              "avmaskering": {}}, headers=hode)
            assert r3.status_code == 409, r3.text
            assert r3.json()["feil"] == "kandidatdata_avvist"

            # Det FØRSTE dokumentet står — avvisningen gjelder skrivene
            # etter at leasen døde, ikke det som var lovlig lagret.
            _sett_kontekst(migrator, TENANT)
            rader = migrator.execute(
                "SELECT (SELECT count(*) FROM kandidat_originaldokument"
                "         WHERE tenant=%s AND prosess_id=%s),"
                " (SELECT count(*) FROM kandidat_evalueringsartefakt"
                "         WHERE tenant=%s AND prosess_id=%s)",
                (TENANT, pid) * 2).fetchone()
            migrator.rollback()
            assert rader == (1, 0), rader
    finally:
        rt.close()


def test_173_leaseleddet_maaler_veggklokken_ikke_transaksjonsstarten():
    """#173 (Codex P2): dørens lease-ledd skal måle `clock_timestamp()`.

    Leddet spør «lever holdet NÅ», og `now()` er ikke nå — den er
    fastfrosset ved transaksjonens START. Dørens transaksjon åpnes FØR
    base64-dekodingen av inntil 25 MiB og før `FOR SHARE OF o` har
    ventet ut en samtidig claimer, så den kan stå åpen vilkårlig lenge
    mens `now()` peker på tiden før ventingen. En lease som døde i
    nettopp det vinduet ble da autorisert, og persondata committet på en
    fullmakt reaperen alt hadde inndratt.

    Målt på KILDEN, ikke på et lås-kappløp (K1): den behavioural formen
    krever at predikatet kan kalles to ganger i samme transaksjon med
    veggklokken flyttet imellom, og dørens SQL er ikke en kallbar
    funksjon slik `hent_inndata_for_oppdrag` er. Porten er derfor samme
    form som `test_m57_rapportflate` alt bruker på `_anker_lever`s
    re-sjekk — og den dreper nøyaktig mutasjonen.

    Retningen er trygg og bør ikke forveksles med en strengere port som
    kan felle noe lovlig: `clock_timestamp()` er alltid ≥ `now()`, så
    leddet slipper aldri gjennom noe reclaimeren (005:894-895) alt har
    tatt.

    MUTASJONEN SOM DREPER DENNE: sett `clock_timestamp()` tilbake til
    `now()` i `_kandidatdata`s radoppslag."""
    import inspect

    from api import app as appmod

    kilde = inspect.getsource(appmod._kandidatdata)
    assert "AND o.owner_lease_utloper > clock_timestamp()" in kilde, \
        "dørens lease-ledd skal måle clock_timestamp(), ikke now()"
    assert "> now()" not in kilde.replace("`now()`", ""), \
        "now() i dørens SQL er transaksjonsstart — poengløs fencing"


def test_173_leasen_maales_ogsaa_ved_skrivegrensen():
    """#173 (Codex P2): ÉN tidsmåling er én for få på skriveveien.

    Leddet over står FØR base64-dekodingen, hashingen og INSERT-ene av
    inntil 25–50 MiB. Radlåsen (`FOR SHARE OF o`) serialiserer
    tilstandsendringer — claim-tyven og `forny_oppdragslease` tar begge
    `FOR UPDATE` og venter på oss — men den stopper ikke veggklokken. Og
    så lenge VI holder delelåsen, kan heartbeaten ikke ta sin egen: leasen
    kan ikke engang fornyes mens skrivingen pågår. En forespørsel med lite
    tid igjen passerte derfor porten, brukte sekundene sine på å skrive,
    og committet persondata på en fullmakt som var utløpt da raden landet.

    Målt på KILDEN, ikke på et kappløp (K1), av nøyaktig samme grunn som
    porten over: den behavioural formen krever at veggklokken flyttes
    MELLOM to punkter i én transaksjon i døren, og dørens SQL er ikke en
    kallbar funksjon. To målinger med `clock_timestamp()`, én før og én
    ved skrivegrensen, er det porten håndhever.

    MUTASJONEN SOM DREPER DENNE: fjern re-målingen foran `conn.commit()`.
    """
    import inspect

    from api import app as appmod

    kilde = inspect.getsource(appmod._kandidatdata)
    assert kilde.count("owner_lease_utloper > clock_timestamp()") >= 2, \
        "leasen skal måles både i porten og ved skrivegrensen"
    # Re-målingen står FORAN committen, ikke etter — ellers er den en
    # observasjon av noe som alt er varig.
    foran = kilde.index("owner_lease_utloper > clock_timestamp()",
                        kilde.index("owner_lease_utloper >"
                                    " clock_timestamp()") + 1)
    assert foran < kilde.index("conn.commit()"), \
        "re-målingen skal stå foran conn.commit()"
    assert "lease_utlopt_under_skriving" in kilde, \
        "utfallet skal være skillbart i driftsloggen"


@pg
def test_173_nullbyte_er_feilformet_ikke_doed_base(migrator, miljo):
    """#173 (Codex P2): NUL skal kodes som request-feil, ikke som drift.

    PostgreSQL kan ikke lagre en nullbyte i `TEXT` eller `jsonb` i det
    hele tatt. Uten porten nådde den INSERT-en og kom ut som en rå
    `psycopg.Error`, som handlerens catch-all oversetter til
    `db_utilgjengelig` — en 5xx. Modulens `lever` leser 5xx som DRIFT,
    brenner hele retrykjeden mot en base som er frisk, og feller til
    slutt evalueringen som `kandidatlagring_feilet`. Prisen er ikke bare
    den ene kjøringen: driftsloggen får en falsk infrastrukturalarm, så
    den som står med vakttelefonen leter etter en base som aldri var
    nede.

    Begge veiene måles. `jsonb` avviser nullbyten like hardt som `TEXT`,
    og `kildetekst` i artefaktet ER den samme uttrekksteksten
    dokumentveien bærer — så en port på bare dokumentveien ville latt
    nøyaktig samme byte gå inn gjennom nabodøren.

    MUTASJONEN SOM DREPER DENNE: fjern NUL-leddene i `_kandidatdata`.
    Da svarer døren 500 `db_utilgjengelig` i stedet for 400."""
    import base64

    from starlette.testclient import TestClient
    from api.app import lag_app
    from .test_bestilling_rekruttering import _sikre_m57_claimbar
    from .test_modul_onboarding_http import _onboard_token
    from miljo import gjeldende_miljo

    _sikre_m57_claimbar(migrator)
    rel = migrator.execute(
        "SELECT release_id FROM moduldeployment"
        " WHERE modul_id='m57_ats' AND miljo=%s AND livslop='claiming'"
        " LIMIT 1", (gjeldende_miljo(),)).fetchone()[0]
    migrator.commit()

    rt = _rt()
    try:
        oid, pid = _prosess(migrator, rt)
        rt.commit()                     # jf. fødselsvaktens `FOR SHARE`
        _sett_kontekst(migrator, TENANT)
        migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
        migrator.execute(
            "UPDATE oppdrag SET owner_claim_id=%s, owner_generation=1,"
            " owner_lease_utloper=now()+interval '10 minutes',"
            " claim_release_id=%s, claim_miljo=%s"
            " WHERE tenant=%s AND id=%s",
            ("c" * 22, rel, gjeldende_miljo(), TENANT, oid))
        migrator.execute("RESET ROLE")
        migrator.commit()

        a = lag_app(DSN)
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, "m57_ats", rel)
            hode = {"authorization": f"Bearer {mtk}"}
            trippel = {"tenant": TENANT, "oppdrag_id": oid,
                       "owner_claim_id": "c" * 22, "owner_generation": 1}
            b64 = base64.b64encode(b"%PDF-1.7 " + FIXTUR.encode()).decode()

            # Dokumentveien: NUL i parseteksten.
            r = c.post("/v1/rekruttering/kandidatdokument",
                       json={**trippel, "kandidat_id": "k1",
                             "dokumentnavn": "k1/cv.pdf",
                             "dokument_b64": b64,
                             "tekst": "CV-tekst\x00 med nullbyte"},
                       headers=hode)
            assert r.status_code == 400, r.text
            assert r.json()["feil"] == "request_feilformet"

            # … og i dokumentnavnet, som går i uuid5, i en TEXT-kolonne
            # og i loggens detalj.
            r = c.post("/v1/rekruttering/kandidatdokument",
                       json={**trippel, "kandidat_id": "k1",
                             "dokumentnavn": "k1/cv\x00.pdf",
                             "dokument_b64": b64, "tekst": "CV-tekst"},
                       headers=hode)
            assert r.status_code == 400, r.text
            assert r.json()["feil"] == "request_feilformet"

            # Artefaktveien: NUL i `kildetekst` (jsonb) …
            r = c.post("/v1/rekruttering/kandidatartefakt",
                       json={**trippel, "kandidat_id": "k1",
                             "artefakt": {"funn": [], "oppfylt": {},
                                          "kildetekst": "tekst\x00her"},
                             "avmaskering": {}}, headers=hode)
            assert r.status_code == 400, r.text
            assert r.json()["feil"] == "request_feilformet"

            # … og i avmaskeringskartets verdi, som er et utsnitt AV
            # nøyaktig den samme teksten.
            r = c.post("/v1/rekruttering/kandidatartefakt",
                       json={**trippel, "kandidat_id": "k1",
                             "artefakt": {"funn": [], "oppfylt": {},
                                          "kildetekst": "x"},
                             "avmaskering": {"[NAVN-1]": "Kari\x00"}},
                       headers=hode)
            assert r.status_code == 400, r.text
            assert r.json()["feil"] == "request_feilformet"

            # Ikke-endelige tall er samme klasse (#260 P2-1): `json.loads`
            # tar imot NaN-tokenet, `jsonb` gjør det ikke — uten porten
            # er kjeden nullbytens (db_utilgjengelig, falsk driftsalarm).
            import json as _json
            kropp = _json.dumps({**trippel, "kandidat_id": "k1",
                                 "artefakt": {"funn": [], "oppfylt": {},
                                              "kildetekst": "x",
                                              "poeng": float("nan")},
                                 "avmaskering": {}})
            assert "NaN" in kropp
            r = c.post("/v1/rekruttering/kandidatartefakt",
                       content=kropp.encode(),
                       headers={**hode,
                                "content-type": "application/json"})
            assert r.status_code == 400, r.text
            assert r.json()["feil"] == "request_feilformet"

            # Konvolutten måles også (#260 P2-2): et løsrevet surrogat i
            # `tenant` nådde sett_kontekst og reiste UnicodeEncodeError
            # — en UKODET 500 på hvert retry. Kroppen bygges som rå
            # bytes: klientens egen UTF-8-koding ville ellers felt den.
            kropp = _json.dumps({**trippel, "tenant": "SURRPLASS",
                                 "kandidat_id": "k1",
                                 "artefakt": {"funn": [], "oppfylt": {},
                                              "kildetekst": "x"},
                                 "avmaskering": {}}
                                ).replace('"SURRPLASS"', '"\\ud800"')
            r = c.post("/v1/rekruttering/kandidatartefakt",
                       content=kropp.encode(),
                       headers={**hode,
                                "content-type": "application/json"})
            assert r.status_code == 400, r.text
            assert r.json()["feil"] == "request_feilformet"

            # Uten nullbyten går nøyaktig de samme kroppene inn — porten
            # felte tegnet, ikke forespørselen.
            assert c.post("/v1/rekruttering/kandidatdokument",
                          json={**trippel, "kandidat_id": "k1",
                                "dokumentnavn": "k1/cv.pdf",
                                "dokument_b64": b64,
                                "tekst": "CV-tekst med nullbyte"},
                          headers=hode).status_code == 200
            assert c.post("/v1/rekruttering/kandidatartefakt",
                          json={**trippel, "kandidat_id": "k1",
                                "artefakt": {"funn": [], "oppfylt": {},
                                             "kildetekst": "teksther"},
                                "avmaskering": {"[NAVN-1]": "Kari"}},
                          headers=hode).status_code == 200

            _sett_kontekst(migrator, TENANT)
            rader = migrator.execute(
                "SELECT (SELECT count(*) FROM kandidat_originaldokument"
                "         WHERE tenant=%s AND prosess_id=%s),"
                " (SELECT count(*) FROM kandidat_evalueringsartefakt"
                "         WHERE tenant=%s AND prosess_id=%s)",
                (TENANT, pid) * 2).fetchone()
            migrator.rollback()
            assert rader == (1, 1), rader
    finally:
        rt.close()


def test_173_losrevet_surrogat_er_feilformet_ikke_ukodet_500():
    """#173 (Codex P2): `\\ud800` skal kodes som request-feil, ikke 500.

    `json.loads` gjør escapen `\\ud800` til et ekte lone surrogate i
    Python-strengen, og en slik streng er ikke en gyldig
    Unicode-scalar-sekvens: `str.encode("utf-8")` reiser
    `UnicodeEncodeError`. Det unntaket er verken `psycopg.Error` eller
    noe portene i `_kandidatdata` fanger, så det falt ut som en UKODET
    500 — og siden samme kropp gir samme unntak, på hvert eneste
    retryforsøk, helt til evalueringen ble felt uten at noe sa hva som
    var galt.

    Veien inn er helt vanlig: manifestpredikatene slipper verdien
    gjennom når et søskeninnslag matcher, og `avmaskering` beholder
    hver deklarert verdi.

    Predikatet måles direkte her, ikke over HTTP. Det er porten begge
    grenene spør, og det er den ENE avgjørelsen funnet handler om —
    HTTP-runden rundt den er alt dekket av
    `test_173_nullbyte_er_feilformet_ikke_doed_base`, som går gjennom
    nøyaktig samme `if`. Denne trenger derfor ingen database.

    BEGGE GRENENE, ikke bare den meldte: funnet ble skrevet på
    artefaktveien (`r.encode` i størrelsesporten), men dokumentveien
    har samme defekt én gren unna — `uuid.uuid5` encoder navnet sitt.
    Derfor står predikatet foran første koding begge steder.

    MUTASJONEN SOM DREPER DENNE: fjern surrogatarmen i
    `_har_ulagringsbart_tegn`, eller flytt tegnporten tilbake BAK
    størrelsessummen i artefaktgrenen (`or` evaluerer venstre side
    først, så `r.encode` møter surrogatet igjen).
    """
    import json as _json

    from api.app import _har_ulagringsbart_tegn

    # Slik verdien FAKTISK kommer inn: som en escape på wire, gjennom
    # `json.loads`. Ingen håndlaget `"\ud800"`-literal i testen.
    kropp = _json.loads('{"artefakt": {"kildetekst": "Kari\\ud800"},'
                        ' "avmaskering": {"[NAVN-1]": "Kari"}}')
    surrogat = kropp["artefakt"]["kildetekst"]
    with pytest.raises(UnicodeEncodeError):
        surrogat.encode("utf-8")

    assert _har_ulagringsbart_tegn(kropp["artefakt"]) is True
    # Også i et avmaskeringskarts VERDI …
    assert _har_ulagringsbart_tegn({"[NAVN-1]": surrogat}) is True
    # … og i en NØKKEL, som `json.dumps(sort_keys=True)` også encoder.
    assert _har_ulagringsbart_tegn({surrogat: "Kari"}) is True
    # … og nestet i intervjuspørsmålslisten.
    assert _har_ulagringsbart_tegn([{"sporsmal": [surrogat]}]) is True

    # Nullbyten er fortsatt fanget — armen ble utvidet, ikke byttet.
    assert _har_ulagringsbart_tegn({"a": ["x\x00y"]}) is True

    # Og en helt vanlig kropp slipper gjennom: porten feller tegnet,
    # ikke forespørselen.
    assert _har_ulagringsbart_tegn(kropp["avmaskering"]) is False
    assert _har_ulagringsbart_tegn(
        {"funn": [], "oppfylt": {"drift": True},
         "kildetekst": "Kari Nordmann, æøå, 漢字, \U0001f600"}) is False


@pg
def test_173_budsjettet_dekker_alle_tre_payloadene(migrator, miljo,
                                                   monkeypatch):
    """#173 (Codex P2): kandidatbudsjettet gjelder alle TRE lagrene.

    Døren målte bare `artefakt` mot `_KANDIDAT_ARTEFAKT_MAKS`.
    `avmaskering` og `intervjusporsmal` er like fullt PERSISTERTE
    payloads — hver sin JSONB-rad, hver sin hashing, hver sin
    likhetssammenligning ved retry — og gikk inn uten noe dekodet tak.
    Det eneste som bandt dem var wire-taket
    `MAKS_KANDIDATARTEFAKT_KROPP`, som per konstruksjon er ~6×
    budsjettet (JSON-eskapefaktoren): en autentisert claimant kunne
    lagre ~301 MiB kart under et uttalt 50 MiB-budsjett.

    Taket senkes kunstig i stedet for å sende 50 MiB gjennom CI — det
    er formen Cursor-passet selv foreslo for denne klassen, og den
    måler nøyaktig leddet funnet gjelder: HVILKE payloads som telles
    med.

    SUMMEN måles, ikke tre tak: budsjettet er KANDIDATENS, og tre
    uavhengige tak à 50 MiB ville vært 150 MiB under samme navn. Siste
    arm beviser det — tre payloads som hver for seg er innenfor (260,
    235, 224 byte mot taket 600), men som til sammen er over.

    MUTASJONEN SOM DREPER DENNE: mål bare `raa_a` igjen, eller bytt
    summen mot tre separate sammenligninger."""
    from starlette.testclient import TestClient
    from api import app as appmod
    from api.app import lag_app
    from .test_bestilling_rekruttering import _sikre_m57_claimbar
    from .test_modul_onboarding_http import _onboard_token
    from miljo import gjeldende_miljo

    _sikre_m57_claimbar(migrator)
    rel = migrator.execute(
        "SELECT release_id FROM moduldeployment"
        " WHERE modul_id='m57_ats' AND miljo=%s AND livslop='claiming'"
        " LIMIT 1", (gjeldende_miljo(),)).fetchone()[0]
    migrator.commit()

    # Bare dørens egen måling flyttes. Middlewarens rutetak er utledet
    # ved import og står urørt på 301 MiB — det er nettopp avstanden
    # mellom de to funnet handler om.
    monkeypatch.setattr(appmod, "_KANDIDAT_ARTEFAKT_MAKS", 600)

    rt = _rt()
    try:
        oid, pid = _prosess(migrator, rt)
        rt.commit()                     # jf. fødselsvaktens `FOR SHARE`
        _sett_kontekst(migrator, TENANT)
        migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
        migrator.execute(
            "UPDATE oppdrag SET owner_claim_id=%s, owner_generation=1,"
            " owner_lease_utloper=now()+interval '10 minutes',"
            " claim_release_id=%s, claim_miljo=%s"
            " WHERE tenant=%s AND id=%s",
            ("c" * 22, rel, gjeldende_miljo(), TENANT, oid))
        migrator.execute("RESET ROLE")
        migrator.commit()

        a = lag_app(DSN)
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, "m57_ats", rel)
            hode = {"authorization": f"Bearer {mtk}"}
            trippel = {"tenant": TENANT, "oppdrag_id": oid,
                       "owner_claim_id": "c" * 22, "owner_generation": 1}
            liten = {"funn": [], "oppfylt": {}, "kildetekst": "x"}

            def _post(kid, **felt):
                return c.post("/v1/rekruttering/kandidatartefakt",
                              json={**trippel, "kandidat_id": kid,
                                    "artefakt": liten, "avmaskering": {},
                                    **felt}, headers=hode)

            # DEN POSITIVE ARMEN FØRST — uten den ville en dør som
            # avviser ALT vært like grønn under et kunstig lavt tak.
            assert _post("k-ok").status_code == 200

            # `avmaskering` alene over taket (1434 byte).
            r = _post("k-kart",
                      avmaskering={f"[NAVN-{i}]": "n" * 20
                                   for i in range(40)})
            assert r.status_code == 400, r.text
            assert r.json()["feil"] == "request_feilformet"

            # `intervjusporsmal` alene over taket (1764 byte).
            r = _post("k-sp", intervjusporsmal=["s" * 40 for _ in range(40)])
            assert r.status_code == 400, r.text
            assert r.json()["feil"] == "request_feilformet"

            # SUMMEN: tre payloads som HVER FOR SEG er innenfor 600
            # byte, men som til sammen er 719. Tre separate tak ville
            # sluppet nettopp denne gjennom.
            r = _post("k-sum",
                      artefakt={"funn": [], "oppfylt": {},
                                "kildetekst": "a" * 220},
                      avmaskering={"[NAVN-1]": "b" * 220},
                      intervjusporsmal=["c" * 220])
            assert r.status_code == 400, r.text
            assert r.json()["feil"] == "request_feilformet"

            # Bare den lovlige kandidaten står i lagrene — avvisningene
            # rullet tilbake, de skrev ikke halve rader.
            _sett_kontekst(migrator, TENANT)
            rader = migrator.execute(
                "SELECT (SELECT count(*) FROM kandidat_evalueringsartefakt"
                "         WHERE tenant=%s AND prosess_id=%s),"
                " (SELECT count(*) FROM kandidat_avmaskering"
                "         WHERE tenant=%s AND prosess_id=%s),"
                " (SELECT count(*) FROM kandidat_intervjusporsmal"
                "         WHERE tenant=%s AND prosess_id=%s)",
                (TENANT, pid) * 3).fetchone()
            migrator.rollback()
            # `k-ok` sendte ingen `intervjusporsmal`, og absens
            # materialiseres ikke (Codex P2): null rader i det lageret er
            # riktig utfall, ikke en manglende skriving.
            assert rader == (1, 1, 0), rader
    finally:
        rt.close()


@pg
def test_222_fristfeiling_lukker_ankeret(migrator):
    """Eiers tillegg på #222 (Codex på #220 `9ca3aca4`):
    `reap_evidensfrister` flytter et utløpt claimet M-57-oppdrag til
    `feilet` UTENFOR `_ingest_kvittering`, så kvitteringsveiens
    ankerlukking aldri nås. Fristen falt da til forlatt-fallbacken målt
    fra `opprettet` — kandidatdata kunne reapes inntil hele kjøretiden
    for tidlig i forhold til kundens frist målt fra AVSLUTNINGEN.

    MUTASJONEN SOM DREPER DENNE: fjern ankerlukkingen fra
    `reap_evidensfrister` (066) — oppdraget feiles fortsatt, og alle
    eldre reaper-tester er grønne."""
    from db import kryptering
    rt = _rt()
    rp = None
    try:
        # `_grunnlag`s form, men med UTLØPTE frister — de er frosset ved
        # fødselen (056-kolonnelåsen), så avviket må oppgis i INSERT-en.
        _sett_kontekst(migrator, TENANT)
        logg = migrator.execute(
            "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
            " policy_id, beslutning, begrunnelse, idempotency_key)"
            " VALUES (%s,'test','api_token','ih','p@1.0.0/x.y','TILLAT',"
            "'[]',%s) RETURNING id",
            (TENANT, secrets.token_hex(8))).fetchone()[0]
        key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(
            migrator, TENANT)
        ct, nonce = kryptering.krypter(dek, {"m57": True}, TENANT, key_id)
        oid = migrator.execute(
            "INSERT INTO oppdrag (opprinnelse, tenant,"
            " beslutning_loggpost_id, oppdragstype, handling, eiermodul,"
            " payload_kryptert, key_id, nonce, utforelsesfrist,"
            " evidensfrist, koblingsstatus)"
            " VALUES ('beslutning',%s,%s,'rekruttering.evaluering',"
            "'rekruttering.evaluering','m57_ats',%s,%s,%s,"
            " now()-interval '2 minutes', now()-interval '1 minute',"
            "'KOBLET') RETURNING id",
            (TENANT, logg, ct, key_id, nonce)).fetchone()[0]
        migrator.execute("UPDATE oppdrag SET status='plukket'"
                         " WHERE tenant=%s AND id=%s", (TENANT, oid))
        migrator.commit()
        _sett_kontekst(rt, TENANT)
        pid = rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,%s)",
                         (TENANT, oid, 90)).fetchone()[0]
        rt.commit()
        rp, _timerrolle = _reaperkobling()
        rader = rp.execute("SELECT tenant, oppdrag_id"
                           " FROM reap_evidensfrister(200)").fetchall()
        rp.commit()
        assert (TENANT, oid) in rader, \
            f"reaperen lot det utløpte oppdraget stå: {rader!r}"
        _sett_kontekst(migrator, TENANT)
        status, lukket, i_tide = migrator.execute(
            "SELECT o.status, p.lukket_ts IS NOT NULL,"
            " p.lukket_ts >= now() - interval '1 minute'"
            " FROM oppdrag o JOIN rekrutteringsprosess p"
            " ON p.tenant = o.tenant AND p.oppdrag_id = o.id"
            " WHERE o.tenant=%s AND o.id=%s", (TENANT, oid)).fetchone()
        migrator.rollback()
        assert status == "feilet"
        assert lukket and i_tide, \
            ("ankeret skal lukkes av SAMME transaksjon som feiler"
             f" oppdraget, ved frist-feilingen: {(lukket, i_tide, pid)}")
    finally:
        rt.close()
        if rp is not None:
            rp.close()
def test_173_doren_binder_deploymenten_ikke_bare_modulen(migrator, miljo):
    """#173 (Codex P1): claim-trippelet er ikke nok — deploymenten måles.

    `o.eiermodul` er DELT av hver levende deployment av modulen: staging
    og produksjon, gammel release og ny, svarer alle `m57_ats`. Et
    claim-trippel som lekker eller replayes til en annen deployment av
    samme modul kunne derfor skrive persondata inn i en prosess den
    aldri claimet — og siden lagrene er append-only, ville den LOVLIGE
    utføreren etterpå møtt `kandidatdata_konflikt` på sin egen kandidat
    og felt hele evalueringen.

    Riggen er den lovlige claim-tilstanden med ÉN forskjell: stempelet
    peker på en annen release enn tokenets. Alt annet — claim-par,
    generasjon, lease, anker — er gyldig, så leddet som avviser står
    alene. Formen er `hent_inndata_for_oppdrag` sin (060:102–103), og
    dette er samme funn som #202 lukket på leseveien.

    MUTASJONEN SOM DREPER DENNE: fjern
    `claim_release_id IS NOT DISTINCT FROM %s` fra dørens radoppslag.
    """
    import base64

    from starlette.testclient import TestClient
    from api.app import lag_app
    from .test_bestilling_rekruttering import _sikre_m57_claimbar
    from .test_modul_onboarding_http import _onboard_token
    from miljo import gjeldende_miljo

    _sikre_m57_claimbar(migrator)
    rel = migrator.execute(
        "SELECT release_id FROM moduldeployment"
        " WHERE modul_id='m57_ats' AND miljo=%s AND livslop='claiming'"
        " LIMIT 1", (gjeldende_miljo(),)).fetchone()[0]
    migrator.commit()

    rt = _rt()
    try:
        oid, pid = _prosess(migrator, rt)
        rt.commit()                     # jf. fødselsvaktens `FOR SHARE`
        _sett_kontekst(migrator, TENANT)
        migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
        migrator.execute(
            "UPDATE oppdrag SET owner_claim_id=%s, owner_generation=1,"
            " owner_lease_utloper=now()+interval '10 minutes',"
            " claim_release_id=%s, claim_miljo=%s"
            " WHERE tenant=%s AND id=%s",
            ("c" * 22, rel + "-en-annen", gjeldende_miljo(), TENANT, oid))
        migrator.execute("RESET ROLE")
        migrator.commit()

        a = lag_app(DSN)
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, "m57_ats", rel)
            hode = {"authorization": f"Bearer {mtk}"}
            trippel = {"tenant": TENANT, "oppdrag_id": oid,
                       "owner_claim_id": "c" * 22, "owner_generation": 1}
            r1 = c.post("/v1/rekruttering/kandidatdokument",
                        json={**trippel, "kandidat_id": "k1",
                              "dokumentnavn": "k1/cv.pdf",
                              "dokument_b64": base64.b64encode(
                                  b"%PDF-1.7 " + FIXTUR.encode()).decode(),
                              "tekst": f"CV-tekst {FIXTUR}"},
                        headers=hode)
            assert r1.status_code == 409, r1.text
            assert r1.json()["feil"] == "kandidatdata_avvist"
            # Artefaktveien står i samme dør.
            r2 = c.post("/v1/rekruttering/kandidatartefakt",
                        json={**trippel, "kandidat_id": "k1",
                              "artefakt": {"funn": [], "oppfylt": {},
                                           "kildetekst": "x"},
                              "avmaskering": {}}, headers=hode)
            assert r2.status_code == 409, r2.text
            assert r2.json()["feil"] == "kandidatdata_avvist"

            # MILJØLEDDET MÅLES ALENE (samme lærdom som #202 runde 10:
            # en regresjon som droppet det ene leddet ville ellers vært
            # grønn på det andre). Riktig release, feil miljø.
            _sett_kontekst(migrator, TENANT)
            migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
            migrator.execute(
                "UPDATE oppdrag SET claim_release_id=%s, claim_miljo=%s"
                " WHERE tenant=%s AND id=%s",
                (rel, gjeldende_miljo() + "-annet", TENANT, oid))
            migrator.execute("RESET ROLE")
            migrator.commit()
            r3 = c.post("/v1/rekruttering/kandidatartefakt",
                        json={**trippel, "kandidat_id": "k1",
                              "artefakt": {"funn": [], "oppfylt": {},
                                           "kildetekst": "x"},
                              "avmaskering": {}}, headers=hode)
            assert r3.status_code == 409, r3.text
            assert r3.json()["feil"] == "kandidatdata_avvist"

            # OG DEN RIKTIGE DEPLOYMENTEN SLIPPER GJENNOM — uten denne
            # armen ville en dør som avviser ALT vært like grønn.
            _sett_kontekst(migrator, TENANT)
            migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
            migrator.execute(
                "UPDATE oppdrag SET claim_release_id=%s, claim_miljo=%s"
                " WHERE tenant=%s AND id=%s",
                (rel, gjeldende_miljo(), TENANT, oid))
            migrator.execute("RESET ROLE")
            migrator.commit()
            r4 = c.post("/v1/rekruttering/kandidatartefakt",
                        json={**trippel, "kandidat_id": "k1",
                              "artefakt": {"funn": [], "oppfylt": {},
                                           "kildetekst": "x"},
                              "avmaskering": {}}, headers=hode)
            assert r4.status_code == 200, r4.text

            # Lagrene bærer nøyaktig det ENE lovlige skrivet.
            _sett_kontekst(migrator, TENANT)
            rader = migrator.execute(
                "SELECT (SELECT count(*) FROM kandidat_originaldokument"
                "         WHERE tenant=%s AND prosess_id=%s),"
                " (SELECT count(*) FROM kandidat_evalueringsartefakt"
                "         WHERE tenant=%s AND prosess_id=%s)",
                (TENANT, pid) * 2).fetchone()
            migrator.rollback()
            assert rader == (0, 1), rader
    finally:
        rt.close()


@pg
def test_173_claimtyveri_i_skrivevinduet_feller_doren(migrator, miljo):
    """#173 (Cursor P2-3): TOCTOU mot claim-tyveri, med TO FORBINDELSER.

    Kjedetesten over måler at FEIL claim-par avvises — det er den
    deterministiske halvdelen. Dette er selve VINDUET: uten radlås var
    dørens autorisasjon et snapshot, og en ny claimer kunne committe
    `UPDATE oppdrag SET owner_generation…` mellom oppslaget og
    INSERT-ene. Døren skrev likevel: INSERT-ene måler ikke claimet på
    nytt, og lagervakten (057) måler `slettet_ts`, ikke leasen. En
    utfører som HADDE mistet oppdraget skrev da persondata inn i
    prosessen på vegne av en fullmakt som var borte.

    Riggen trenger ingen instrumentert søm — LÅSEN er sømmen. Tyven tar
    `FOR UPDATE` på oppdragsraden FØRST, så dørens `FOR SHARE OF o`
    blokkerer på nøyaktig det stedet vinduet lå. At den venter DER måles
    positivt med `pg_blocking_pids` mot tyvens backend-pid — låsmanageren
    selv, ikke en sleep som håper at vinduet var åpent.

    Når tyveriet committer, re-evaluerer PostgreSQL predikatet mot den
    NYE radversjonen — samme mekanikk 057s fødselsvakt bruker — og
    generasjonsleddet faller. Døren svarer `kandidatdata_avvist`, og
    lagrene står tomme.

    MUTASJONEN SOM DREPER DENNE: fjern `FOR SHARE OF o` i
    `_kandidatdata`. Da venter ingen backend, ventepollen utløper, og
    forespørselen har for lengst svart 200 på et gammelt snapshot."""
    import base64
    import threading
    import time

    from starlette.testclient import TestClient
    from api.app import lag_app
    from .test_bestilling_rekruttering import _sikre_m57_claimbar
    from .test_modul_onboarding_http import _onboard_token
    from miljo import gjeldende_miljo

    _sikre_m57_claimbar(migrator)
    rel = migrator.execute(
        "SELECT release_id FROM moduldeployment"
        " WHERE modul_id='m57_ats' AND miljo=%s AND livslop='claiming'"
        " LIMIT 1", (gjeldende_miljo(),)).fetchone()[0]
    migrator.commit()

    rt = _rt()
    tyv = psycopg.connect(MIGRATOR_DSN)
    obs = psycopg.connect(MIGRATOR_DSN, autocommit=True)
    try:
        oid, pid = _prosess(migrator, rt)
        # Fødselsvakten holder `FOR SHARE` på oppdragsraden til `rt`
        # committer — uten denne linjen ville riggen selv okkupert
        # nøyaktig låsen tyven under skal eie.
        rt.commit()
        _sett_kontekst(migrator, TENANT)
        # DEPLOYMENTEN STEMPLES OGSÅ (Codex P1). Claim-porten skriver
        # `claim_release_id`/`claim_miljo` med den deploymenten den
        # verifiserte (049:294), og døren binder nå mot dem; en rigg som
        # lot dem stå NULL ville målt en tilstand claim-døren aldri
        # produserer. Kolonnene er claim-vaktens (049:98) og kan KUN
        # settes av `disponit_m37_claimer` — samme form som `_pluk` i
        # resolvertestene.
        migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
        migrator.execute(
            "UPDATE oppdrag SET owner_claim_id=%s, owner_generation=1,"
            " owner_lease_utloper=now()+interval '10 minutes',"
            " claim_release_id=%s, claim_miljo=%s"
            " WHERE tenant=%s AND id=%s",
            ("c" * 22, rel, gjeldende_miljo(), TENANT, oid))
        migrator.execute("RESET ROLE")
        migrator.commit()

        utfall: list = []
        a = lag_app(DSN)
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, "m57_ats", rel)
            hode = {"authorization": f"Bearer {mtk}"}
            dok = {"tenant": TENANT, "oppdrag_id": oid,
                   "owner_claim_id": "c" * 22, "owner_generation": 1,
                   "kandidat_id": "k1", "dokumentnavn": "k1/cv.pdf",
                   "dokument_b64": base64.b64encode(
                       b"%PDF-1.7 " + FIXTUR.encode()).decode(),
                   "tekst": f"CV-tekst {FIXTUR}"}

            # TYVEN FØRST: eksklusiv lås på oppdragsraden, og den nye
            # generasjonen skrevet men IKKE committet. Dette er stillingen
            # en samtidig `plukk`/overtakelse står i.
            _sett_kontekst(tyv, TENANT)
            tyvpid = tyv.execute("SELECT pg_backend_pid()").fetchone()[0]
            tyv.execute("SELECT 1 FROM oppdrag WHERE tenant=%s AND id=%s"
                        " FOR UPDATE", (TENANT, oid))
            tyv.execute(
                "UPDATE oppdrag SET owner_claim_id=%s, owner_generation=2,"
                " owner_lease_utloper=now()+interval '10 minutes'"
                " WHERE tenant=%s AND id=%s", ("d" * 22, TENANT, oid))

            def _skriv():
                try:
                    utfall.append(c.post("/v1/rekruttering/kandidatdokument",
                                         json=dok, headers=hode))
                except Exception as e:      # noqa: BLE001 — til asserten
                    utfall.append(e)

            traad = threading.Thread(target=_skriv, daemon=True)
            traad.start()
            try:
                # `pg_blocking_pids` leser låsmanageren direkte og krever
                # ingen stats-privilegier — `pid` er synlig for alle, og
                # observatøren er autocommit, så ingen transaksjon cacher
                # backend-statusen mellom rundene.
                frist = time.monotonic() + 20
                while time.monotonic() < frist:
                    if obs.execute(
                            "SELECT count(*) FROM pg_stat_activity"
                            " WHERE %s = ANY(pg_blocking_pids(pid))",
                            (tyvpid,)).fetchone()[0]:
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError(
                        "ingen backend ble blokkert av tyvens radlås innen"
                        " 20 s — døren leste claimet ULÅST og skrev på et"
                        f" gammelt snapshot. Utfall så langt: {utfall}")
                # Vinduet lukkes: tyveriet committer MENS døren venter.
                tyv.commit()
            finally:
                tyv.rollback()
                traad.join(30)
            assert not traad.is_alive(), \
                "skrivetråden lever etter commit + 30 s join"
            assert len(utfall) == 1, utfall
            r = utfall[0]
            assert not isinstance(r, Exception), r
            assert r.status_code == 409, r.text
            assert r.json()["feil"] == "kandidatdata_avvist"

            # Ingenting ble skrevet på den tapte fullmakten.
            _sett_kontekst(migrator, TENANT)
            rader = migrator.execute(
                "SELECT (SELECT count(*) FROM kandidat_originaldokument"
                "         WHERE tenant=%s AND prosess_id=%s),"
                " (SELECT count(*) FROM kandidat_parsettekst"
                "         WHERE tenant=%s AND prosess_id=%s)",
                (TENANT, pid) * 2).fetchone()
            migrator.rollback()
            assert rader == (0, 0), rader
    finally:
        obs.close()
        tyv.close()
        rt.close()


# Dekningsporten: begge kodene bevises av kjedetesten over.
from .test_api import dekker as _dekker173  # noqa: E402

test_173_skriveveien_er_claimbundet_og_idempotent = _dekker173(
    "kandidatdata_avvist", "kandidatdata_konflikt")(
    test_173_skriveveien_er_claimbundet_og_idempotent)




def _utlopt_beslutning(m, tenant, *, oppdragstype, eiermodul, status,
                       alder_min):
    """Beslutningsoppdrag født med fristene alt passert, i EN gitt tenant.

    Fristene er frosset ved fødselen (056-kolonnelåsen), så avviket må
    oppgis i INSERT-en — samme form som
    `test_222_fristfeiling_lukker_ankeret` over. `alder_min` styrer
    REKKEFØLGEN i sveipet: loopen er `ORDER BY o.evidensfrist`.
    """
    from db import kryptering
    _sett_kontekst(m, tenant)
    logg = m.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'test','api_token','ih','p@1.0.0/x.y','TILLAT',"
        "'[]',%s) RETURNING id",
        (tenant, secrets.token_hex(8))).fetchone()[0]
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(m, tenant)
    ct, nonce = kryptering.krypter(dek, {"m57": True}, tenant, key_id)
    oid = m.execute(
        "INSERT INTO oppdrag (opprinnelse, tenant,"
        " beslutning_loggpost_id, oppdragstype, handling, eiermodul,"
        " payload_kryptert, key_id, nonce, utforelsesfrist,"
        " evidensfrist, koblingsstatus)"
        " VALUES ('beslutning',%s,%s,%s,%s,%s,%s,%s,%s,"
        f" now()-interval '{alder_min + 1} minutes',"
        f" now()-interval '{alder_min} minutes','KOBLET') RETURNING id",
        (tenant, logg, oppdragstype, oppdragstype, eiermodul, ct, key_id,
         nonce)).fetchone()[0]
    if status != "opprettet":
        m.execute("UPDATE oppdrag SET status=%s WHERE tenant=%s AND id=%s",
                  (status, tenant, oid))
    m.commit()
    return int(oid)


@pg
def test_222_ankerlukkingen_ser_bare_sitt_eget_oppdrag(migrator):
    """Cursor P1 på #252 (runde 2) — MÅLING AV EN PÅSTAND OM PL/pgSQL.

    Funnet var at `SELECT … INTO` skal la målvariabelen stå UENDRET når
    spørringen ikke gir rader, slik at et sveip som først lukker tenant
    A-s anker treffer neste, ankerløse oppdrag med A-s `v_pid` i hånda
    og kaller `lukk_rekrutteringsprosess(B, A-s pid, …)` →
    `invalid_parameter_value` → hele batchen ruller. Samme påstand for
    `v_kandidat`: stale sak-id → `FOR UPDATE … NOT FOUND` → stille
    `CONTINUE`, altså et utløpt oppdrag som aldri feiles.

    PostgreSQL dokumenterer det motsatte («target will be set to the
    first row returned by the query, or to nulls if the query returned
    no rows»), og 058/059 skriver den semantikken ut i klartekst i sine
    egne kommentarer. Men dokumentasjon er ikke en måling, og formen
    påstanden handler om — TO utløpte oppdrag i ETT sveip, i HVER SIN
    tenant, der bare det første har anker og sak — fantes ikke i suiten.
    Nå gjør den det, og basen avgjør spørsmålet.

    Begge armene er bebodd med vilje: A har både et ÅPENT anker og en
    åpen `evidensfrist`-sak, B har ingen av delene og ligger etter A i
    `ORDER BY o.evidensfrist`. Holder påstanden, er testen rød på
    nøyaktig de to måtene funnet beskriver — ellers grønn.
    """
    from .test_api import ANNEN_TENANT
    rt = _rt()
    rp = None
    try:
        # A: claimet M-57-oppdrag med anker, ELDST i sveipet.
        oid_a = _utlopt_beslutning(
            migrator, TENANT, oppdragstype="rekruttering.evaluering",
            eiermodul="m57_ats", status="plukket", alder_min=10)
        _sett_kontekst(rt, TENANT)
        pid_a = rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,%s)",
                           (TENANT, oid_a, 90)).fetchone()[0]
        # …og en ÅPEN evidensfrist-sak, så `v_kandidat` faktisk får en
        # verdi å være stale med. Låsen må være ledig når sveipet går,
        # derav commit — en åpen transaksjon her ville fått reaperen til
        # å hoppe over A av en helt annen grunn enn den testen måler.
        rt.execute("SELECT sikre_sak_for_oppdrag(%s,%s,'evidensfrist',"
                   "'test','r-forhaand')", (TENANT, oid_a))
        rt.commit()

        # B: ANNEN tenant, WCAG — har per konstruksjon verken
        # rekrutteringsprosess eller evidensfrist-sak, og kommer ETTER A.
        oid_b = _utlopt_beslutning(
            migrator, ANNEN_TENANT,
            oppdragstype="kontroll.wcag.nettsted",
            eiermodul="m_wcag_audit", status="opprettet", alder_min=9)

        rp, _timerrolle = _reaperkobling()
        # Selve målingen: ETT sveip over begge. Stale `v_pid` ville felt
        # dette kallet med `invalid_parameter_value`.
        rader = rp.execute("SELECT tenant, oppdrag_id"
                           " FROM reap_evidensfrister(200)").fetchall()
        rp.commit()
        assert (TENANT, oid_a) in rader, f"A ble ikke reapet: {rader!r}"
        assert (ANNEN_TENANT, oid_b) in rader, \
            ("B falt ut av sveipet — en stale `v_kandidat` gir nettopp"
             f" dette stille `CONTINUE`-et: {rader!r}")

        _sett_kontekst(migrator, TENANT)
        status_a, lukket_a = migrator.execute(
            "SELECT o.status, p.lukket_ts IS NOT NULL FROM oppdrag o"
            " JOIN rekrutteringsprosess p ON p.tenant = o.tenant"
            " AND p.oppdrag_id = o.id WHERE o.tenant=%s AND o.id=%s",
            (TENANT, oid_a)).fetchone()
        migrator.rollback()
        _sett_kontekst(migrator, ANNEN_TENANT)
        status_b = migrator.execute(
            "SELECT status FROM oppdrag WHERE tenant=%s AND id=%s",
            (ANNEN_TENANT, oid_b)).fetchone()[0]
        # …og B fikk ikke A-s anker dyttet over på seg på veien.
        ankre_b = migrator.execute(
            "SELECT count(*) FROM rekrutteringsprosess WHERE tenant=%s",
            (ANNEN_TENANT,)).fetchone()[0]
        migrator.rollback()
        assert (status_a, lukket_a) == ("feilet", True), \
            f"A: {(status_a, lukket_a, pid_a)}"
        assert status_b == "feilet", \
            f"B ble stående utenfor sin egen frist-feiling: {status_b}"
        assert ankre_b == 0, \
            f"ankerlukkingen skrev inn i feil tenant: {ankre_b}"
    finally:
        rt.close()
        if rp is not None:
            rp.close()


@pg
def test_reapmerket_krever_ogsaa_at_rapporten_er_makulert(migrator):
    """Cursor P1 på #252: vakten målte de SEKS lagrene, ikke `artefakt`.

    `slettet_ts`-armen finnes fordi et merke satt uten at payloaden er
    tømt utelukker prosessen fra reaperen for alltid — den plukker bare
    `slettet_ts IS NULL`. Etter #222 er den promoterte rapporten samme
    slags kandidatpayload som lagrene (funn, intervjuspørsmål, hele den
    blindede kildeteksten per kandidat), men den var ikke med i
    målingen: med tomme lagre og en levende rapport slapp merket
    gjennom, og rapporten ble stående for alltid.

    Reaperen gjør det riktige av seg selv (den makulerer FØR den
    merker), så dette er porten som gjør den rekkefølgen umulig å miste
    — også for en fremtidig vei som bare merker.

    MUTASJONEN SOM DREPER DENNE: fjern artefakt-EXISTS i vaktens
    `slettet_ts`-gren (066). Alle port 18-testene er grønne under den:
    de måler bare de seks lagrene."""
    rt = _rt()
    try:
        oid, pid = _prosess(migrator, rt, frist=30)
        # De SEKS lagrene er tomme — den gamle vakten hadde dermed
        # ingenting å innvende, og det er nettopp hullet.
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '31 days')", (TENANT, pid))
        rt.commit()
        aid = _promotert_rapport(migrator, oid)
        assert _artefaktrad(migrator, aid)[1] is False, \
            "positiv kontroll: rapporten skal bære payload før merket"

        # Merket, satt av den rollen som HAR rettigheten, uten at
        # rapporten er makulert.
        _sett_kontekst(migrator, TENANT)
        migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "UPDATE rekrutteringsprosess SET slettet_ts=now()"
                " WHERE tenant=%s AND prosess_id=%s", (TENANT, pid))
        migrator.rollback()
        # … og rapporten står fortsatt der, altså fortsatt synlig for
        # reaperen — som er hele poenget med å nekte merket.
        assert _artefaktrad(migrator, aid)[3] is False

        # Etter LOVLIG makulering gjennom døren tillates merket, i samme
        # rekkefølge reaperen alt bruker.
        _sett_kontekst(migrator, TENANT)
        migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
        assert migrator.execute(
            "SELECT makuler_artefakter_for_prosess(%s,%s,now())",
            (TENANT, oid)).fetchone()[0] == 1
        migrator.execute(
            "UPDATE rekrutteringsprosess SET slettet_ts=now()"
            " WHERE tenant=%s AND prosess_id=%s", (TENANT, pid))
        migrator.commit()
        assert _artefaktrad(migrator, aid) == \
            ("promotert", True, True, True, True)
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT slettet_ts IS NOT NULL FROM rekrutteringsprosess"
            " WHERE tenant=%s AND prosess_id=%s",
            (TENANT, pid)).fetchone()[0] is True
        migrator.rollback()
    finally:
        rt.close()


def test_sp10_daekker_067():
    """Cursor P2 på #252: 067 er en BACKFILL, og en backfill uten SP-10
    er umålt der den faktisk kjører.

    Engangs-makuleringen møter én form ved oppgradering som ingen
    tom-base-`migrer` kan vise: en prosess som ALT er reapet, med den
    promoterte rapportens payload i live. Uten seedet måler CI bare at
    setningene parser — og en fjernet løkke ville vært grønn hele veien
    til prod. Porten speiler `test_sp10_daekker_049`/`_056`/`_059`:
    seed + måling registrert i SEEDS, og CI-pekeren på plass, ellers
    kjører den aldri i pipelinen.

    CI-pekeren (`sp10-provekjoring.py 67` i SP-10-steget) er på plass
    (sak #263, skrevet med eiers auth under ny prosess — den gamle
    sløyfas app-token manglet workflows-retten), så porten krever den
    her på samme måte som 049/056/059 gjør — uten linja kjører seedet
    aldri i pipelinen, og en rød `_mal_067` ville ikke stoppet merge."""
    import re
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    ci = (rot / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8")
    assert re.search(r"sp10-provekjoring\.py 67\b", ci), (
        "SP-10-prøvekjøringen for 067 mangler i CI")
    sp10 = (rot / "deploy" / "staging" / "sp10-provekjoring.py").read_text(
        encoding="utf-8")
    assert "67: (_seed_067, _mal_067)" in sp10, \
        "067 har ingen registrert seed+måling i SEEDS"
    # Seedet må FAKTISK bebo klassen 067 finnes for: en REAPET prosess
    # (merket satt før 067) med en promotert rapport som fortsatt bærer
    # payload. Et seed som bare fødte en levende prosess ville gjort
    # målingen vacuous.
    seed = sp10[sp10.index("def _seed_067"):sp10.index("def _mal_067")]
    assert "slettet_ts = now()" in seed, \
        "seedet setter aldri reap-merket — den bebodde formen mangler"
    assert "'promotert'" in seed and "ciphertext" in seed, \
        "seedet legger ingen promotert rapport med payload på oppdraget"
    mal = sp10[sp10.index("def _mal_067"):sp10.index("SEEDS = {")]
    assert "makulert_ts" in mal and "ciphertext" in mal, \
        "målingen ser hverken merket eller den tømte payloaden"


@pg
def test_069_tidligsletting_reapes_i_forste_sveip(migrator):
    """069: «Slett» i flaten er en BESTILLING reaperen fullbyrder.

    Fristen og lukkingen er immutable (port 20) — tidligslettingen er
    et eget, enveis merke: døren setter det (og lukker en ulukket
    prosess), reaperens predikat tar bestilte slettinger uavhengig av
    fristen, og alle reap-portene (seks lagre + makulering) gjelder.
    Merket kan aldri fjernes eller flyttes — heller ikke av eieren.

    MUTASJONEN SOM DREPER DENNE: fjern `slett_bestilt_ts`-leddet fra
    reaperens predikat — prosessen med frist 90 døgn består da sveipet."""
    rt = _rt()
    rp = None
    try:
        oid, pid = _prosess(migrator, rt, frist=90)
        _fyll_lagrene(rt, pid)
        rt.commit()
        assert _tell_fixtur(migrator, pid) == 9
        migrator.rollback()
        # Døren: idempotent, lukker den åpne prosessen, setter merket.
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT bestill_tidligsletting(%s,%s)", (TENANT, pid))
        rt.execute("SELECT bestill_tidligsletting(%s,%s)", (TENANT, pid))
        rt.commit()
        _sett_kontekst(migrator, TENANT)
        rad = migrator.execute(
            "SELECT lukket_ts IS NOT NULL, slett_bestilt_ts IS NOT NULL"
            "  FROM rekrutteringsprosess"
            " WHERE tenant=%s AND prosess_id=%s", (TENANT, pid)).fetchone()
        assert rad == (True, True), rad
        # Merket er enveis — flytting og fjerning felles av vakten.
        # Konteksten er TRANSAKSJONSLOKAL og rollback tar den med seg
        # (FORCE RLS gjelder også eieren) — settes derfor INNE i løkka.
        for ny in ("NULL", "now() - interval '1 hour'"):
            _sett_kontekst(migrator, TENANT)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                migrator.execute(
                    f"UPDATE rekrutteringsprosess SET slett_bestilt_ts={ny}"
                    f" WHERE tenant=%s AND prosess_id=%s", (TENANT, pid))
            migrator.rollback()
        # En prosess kan aldri FØDES med merket (CodeRabbit på 069):
        # gravsteinsklassen fra lagervakten, på ankeret.
        o2, _ = _claimet(migrator)
        _sett_kontekst(migrator, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.execute(
                "INSERT INTO rekrutteringsprosess (tenant, prosess_id,"
                " oppdrag_id, slett_bestilt_ts)"
                " VALUES (%s, gen_random_uuid(), %s, now())",
                (TENANT, o2))
        migrator.rollback()
        # Reaperen tar den i FØRSTE sveip — 90-døgnsfristen har så vidt
        # begynt å løpe.
        rp, _timer = _reaperkobling()
        reapet = rp.execute("SELECT * FROM reap_kandidatdata(50)").fetchall()
        rp.commit()
        assert (TENANT, pid) in [(r[0], r[1]) for r in reapet], reapet
        assert _tell_fixtur(migrator, pid) == 0
        migrator.rollback()
    finally:
        rt.close()
        if rp is not None:
            rp.close()


@pg
def test_069_slett_og_avbryt_endepunktene(migrator, miljo):
    """Flatens Slett og Avbryt går gjennom sine egne dører (069).

    Avbryt: `plukket` går maskinens EGEN vei (plukket -> opprettet ->
    kansellert i samme transaksjon), ankeret lukkes (frist fra
    avslutningen, sak 222-formen), og en terminal evaluering svarer
    `evaluering_terminal` — flaten oppfrisker i stedet for å tro på
    skjermbildet. Slett: bestiller tidligsletting via døren; ukjent
    oppdrag er `ikke_funnet`."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    from api import sesjon as sesjonmodul
    from .test_bestilling_rekruttering import _adminsesjon, _sikre_m57_claimbar
    from .test_api import DSN as API_DSN, dekker  # noqa: F401

    _sikre_m57_claimbar(migrator)
    migrator.commit()
    rt = _rt()
    try:
        oid, pid = _prosess(migrator, rt, frist=90)
        rt.commit()
        a = lag_app(API_DSN)
        with TestClient(a) as c:
            cookie, csrf = _adminsesjon()
            hode = {"X-Disponit-CSRF": csrf}
            ck = {sesjonmodul.C_SESJON: cookie,
                  "__Host-disponit_csrf": csrf}
            r1 = c.post(f"/v1/rekruttering/evaluering/{oid}/avbryt",
                        cookies=ck, headers=hode)
            assert r1.status_code == 200, r1.text
            _sett_kontekst(migrator, TENANT)
            rad = migrator.execute(
                "SELECT o.status, p.lukket_ts IS NOT NULL"
                "  FROM oppdrag o JOIN rekrutteringsprosess p"
                "    ON p.tenant=o.tenant AND p.oppdrag_id=o.id"
                " WHERE o.tenant=%s AND o.id=%s", (TENANT, oid)).fetchone()
            migrator.rollback()
            assert rad == ("kansellert", True), rad
            # BARE AKTIVE LØP AUTO-VISES i prosessindeksen (eiers funn
            # 30/8): den kansellerte er uten kandidatdata og står ikke i
            # indeksen i det heletatt — og ingenting velger seg selv.
            rp = c.get("/v1/rekruttering/prosesser",
                       cookies=ck, headers=hode)
            assert rp.status_code == 200, rp.text
            assert rp.json()["valgt_prosess_id"] is None, (
                "et terminalt løp valgte seg selv — gamle rapporter"
                " stabler seg i dypdykket igjen: " + rp.text)
            # Terminal: 409 med egen kode.
            r2 = c.post(f"/v1/rekruttering/evaluering/{oid}/avbryt",
                        cookies=ck, headers=hode)
            assert r2.status_code == 409, r2.text
            assert r2.json()["feil"] == "evaluering_terminal"
            # Slett: bestiller tidligsletting.
            r3 = c.post(f"/v1/rekruttering/evaluering/{oid}/slett",
                        cookies=ck, headers=hode)
            assert r3.status_code == 200, r3.text
            _sett_kontekst(migrator, TENANT)
            assert migrator.execute(
                "SELECT slett_bestilt_ts IS NOT NULL"
                "  FROM rekrutteringsprosess"
                " WHERE tenant=%s AND prosess_id=%s",
                (TENANT, pid)).fetchone()[0]
            migrator.rollback()
            # SLETT BETYR AT RADEN FORSVINNER (071, eiers funn 30/8):
            # listen slipper raden i svaret på selve slett-kallet —
            # historikken består i basen med enveis-merket satt.
            r5 = c.get("/v1/rekruttering/evalueringer",
                       cookies=ck, headers=hode)
            assert r5.status_code == 200, r5.text
            assert all(e["oppdrag_id"] != oid
                       for e in r5.json()["evalueringer"]), r5.text
            _sett_kontekst(migrator, TENANT)
            assert migrator.execute(
                "SELECT liste_skjult_ts IS NOT NULL FROM oppdrag"
                " WHERE tenant=%s AND id=%s",
                (TENANT, oid)).fetchone()[0], "skjule-merket ble aldri satt"
            migrator.rollback()
            r6 = c.get("/v1/rekruttering/prosesser",
                       cookies=ck, headers=hode)
            assert r6.status_code == 200, r6.text
            assert all(p["prosess_id"] != str(pid)
                       for p in r6.json()["prosesser"]), r6.text
            # Ukjent oppdrag: intet anker å slette.
            r4 = c.post("/v1/rekruttering/evaluering/99999999/slett",
                        cookies=ck, headers=hode)
            assert r4.status_code == 404, r4.text
            # EIERS FUNN 30/8: en FEILET evaluering som aldri rakk å føde
            # prosessen har ingenting å slette — og det skal sies med et
            # ærlig 200, ikke et 404 flaten bare kan vise som generisk
            # handlingsfeil. Oppdraget finnes i tenantens egen liste, så
            # skillet mot ukjent-404 er ikke et orakel. Og listen bærer
            # fakta knappene trenger: har_anker.
            # … og en AKTIV evaluering uten anker (køstående — kjøringen
            # skal jo produsere data) avvises med egen kode: veien er
            # Avbryt, aldri en «ingenting lagret»-løgn om fremtiden.
            o_aktiv, _g2 = _grunnlag(migrator,
                                     oppdragstype="rekruttering.evaluering",
                                     status="opprettet")
            r6b = c.post(f"/v1/rekruttering/evaluering/{o_aktiv}/slett",
                         cookies=ck, headers=hode)
            assert r6b.status_code == 409, r6b.text
            assert r6b.json()["feil"] == "evaluering_aktiv"
            o_uten, _g = _grunnlag(migrator,
                                   oppdragstype="rekruttering.evaluering",
                                   status="feilet")
            r7 = c.post(f"/v1/rekruttering/evaluering/{o_uten}/slett",
                        cookies=ck, headers=hode)
            assert r7.status_code == 200, r7.text
            assert r7.json()["ingenting_lagret"] is True
            assert r7.json()["slett_bestilt"] is False
            # … og raden er UTE av listen (071): det var hele poenget —
            # en feilet evaluering uten data ble ellers stående for
            # alltid uten noen vei bort.
            r8 = c.get("/v1/rekruttering/evalueringer",
                       cookies=ck, headers=hode)
            assert all(e["oppdrag_id"] != o_uten
                       for e in r8.json()["evalueringer"]), r8.text
            # Skjule-merket er ENVEIS og kun for terminale løp — samme
            # disiplin som 069-merket, målt mot vakten selv.
            _sett_kontekst(migrator, TENANT)
            import pytest as _pt
            from psycopg.errors import InsufficientPrivilege as _IP
            with _pt.raises(_IP):
                migrator.execute(
                    "UPDATE oppdrag SET liste_skjult_ts = NULL"
                    " WHERE tenant=%s AND id=%s", (TENANT, o_uten))
            migrator.rollback()
            _sett_kontekst(migrator, TENANT)
            with _pt.raises(_IP):
                migrator.execute(
                    "UPDATE oppdrag SET liste_skjult_ts = now()"
                    " WHERE tenant=%s AND id=%s", (TENANT, o_aktiv))
            migrator.rollback()
    finally:
        rt.close()


from .test_api import dekker as _dekker069  # noqa: E402

test_069_slett_og_avbryt_endepunktene = _dekker069(
    "evaluering_terminal")(test_069_slett_og_avbryt_endepunktene)
test_069_slett_og_avbryt_endepunktene = _dekker069(
    "evaluering_aktiv")(test_069_slett_og_avbryt_endepunktene)


@pg
def test_157_ankerdoren_er_eneste_fodselsvei(migrator):
    """#157: kandidatens fødsel er DØRENS — runtime har ingen rå INSERT
    på ankeret, uten tenantkontekst finnes ingen fødsel, en reapet
    prosess føder ingen kandidat, og samme trippel er et stille ja
    (dokumentstrømmen kaller døren per medlem).

    MUTASJONEN SOM DREPER DENNE: GRANT INSERT ON kandidat til runtime,
    eller fjern reapet-armen i `opprett_kandidat`."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        rt.commit()
        kid = uuid.uuid4()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute(
                "INSERT INTO kandidat (tenant, prosess_id, kandidat_id)"
                " VALUES (%s,%s,%s)", (TENANT, pid, kid))
        rt.rollback()
        # Uten kontekst: kontekstporten feller før noe skrives.
        with pytest.raises(psycopg.errors.Error):
            rt.execute("SELECT opprett_kandidat(%s,%s,%s)",
                       (TENANT, pid, kid))
        rt.rollback()
        # Idempotent gjennom døren — én rad, ikke to.
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT opprett_kandidat(%s,%s,%s)", (TENANT, pid, kid))
        rt.execute("SELECT opprett_kandidat(%s,%s,%s)", (TENANT, pid, kid))
        rt.commit()
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT count(*) FROM kandidat WHERE tenant=%s"
            " AND prosess_id=%s AND kandidat_id=%s",
            (TENANT, pid, kid)).fetchone()[0] == 1
        migrator.rollback()
        # En reapet prosess føder ingen kandidat.
        _, pid2 = _prosess(migrator, rt, frist=30)
        _fyll_lagrene(rt, pid2)
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '31 days')", (TENANT, pid2))
        rt.commit()
        rp, _timer = _reaperkobling()
        try:
            rp.execute("SELECT * FROM reap_kandidatdata(50)")
            rp.commit()
        finally:
            rp.close()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT opprett_kandidat(%s,%s,%s)",
                       (TENANT, pid2, uuid.uuid4()))
        rt.rollback()
    finally:
        rt.close()


@pg
def test_157_rekkefolgebeviset_reap_uten_anker_felles(migrator):
    """#157s bevispunkt, ordrett fra eiers dom: porten skal være RØD når
    ankeret er lagt til uten at reaperen lærte det. Tilstanden en slik
    reaper ville etterlatt — de seks lagrene reapet, ankeret levende —
    felles ved COMMIT; med ankeret i samme transaksjon går det.

    MUTASJONEN SOM DREPER DENNE: fjern kandidat-armene fra
    `m57_lagrene_reapes_samlet` (tilbake til 057-kroppen)."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        _fyll_lagrene(rt, pid)
        rt.commit()
        # «Reaperen som ikke lærte det syvende medlemmet»: de seks
        # lagrene alene. Setningene går — COMMIT-en gjør det ikke.
        _sett_kontekst(migrator, TENANT)
        for tab, payload in _REAP_SETNINGER:
            if tab == "kandidat":
                continue
            migrator.execute(
                f"UPDATE {tab} SET {payload}, slettet_ts=now()"
                " WHERE tenant=%s AND prosess_id=%s", (TENANT, pid))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrator.commit()
        migrator.rollback()
        # Positiv kontroll: alle SJU i samme transaksjon går gjennom.
        _sett_kontekst(migrator, TENANT)
        for tab, payload in _REAP_SETNINGER:
            migrator.execute(
                f"UPDATE {tab} SET {payload}, slettet_ts=now()"
                " WHERE tenant=%s AND prosess_id=%s", (TENANT, pid))
        migrator.commit()
    finally:
        rt.close()


@pg
def test_157_kapplop_fodsel_mot_reaper_gir_ingen_halvtom(migrator):
    """#157s kappløpspunkt: dørens `FOR SHARE` serialiseres mot reaperens
    `FOR UPDATE`. A (reaperen) holder en ucommittet reap; B (døren)
    blokkerer på prosessradens lås, ser den NYE versjonen etter As
    commit, og avvises — aldri en kandidat født på en prosess som
    nettopp ble slettet, aldri en halvtom prosess.

    MUTASJONEN SOM DREPER DENNE: bytt dørens `FOR SHARE` til et ulåst
    `EXISTS`."""
    import threading

    rt = _rt()
    a = None
    try:
        _, pid = _prosess(migrator, rt, frist=30)
        _fyll_lagrene(rt, pid)
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '31 days')", (TENANT, pid))
        rt.commit()
        a, _timer = _reaperkobling()
        a.execute("SELECT * FROM reap_kandidatdata(50)")
        # As transaksjon står åpen: prosessraden er FOR UPDATE-låst.
        resultat: dict = {}

        def fodsel():
            try:
                _sett_kontekst(rt, TENANT)
                rt.execute("SELECT opprett_kandidat(%s,%s,%s)",
                           (TENANT, pid, uuid.uuid4()))
                rt.commit()
                resultat["kom_gjennom"] = True
            except Exception as feil:
                resultat["feil"] = feil
                rt.rollback()

        t = threading.Thread(target=fodsel)
        t.start()
        t.join(timeout=2)
        assert t.is_alive(), \
            "døren leste forbi reaperens ucommittede FOR UPDATE"
        a.commit()
        t.join(timeout=10)
        assert not t.is_alive(), "døren kom aldri gjennom etter reapen"
        assert isinstance(resultat.get("feil"),
                          psycopg.errors.InsufficientPrivilege), resultat
        assert "reapet" in str(resultat["feil"]), resultat["feil"]
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT count(*) FROM kandidat WHERE tenant=%s"
            " AND prosess_id=%s AND slettet_ts IS NULL",
            (TENANT, pid)).fetchone()[0] == 0, \
            "en levende kandidat på en reapet prosess er halvtomheten"
        migrator.rollback()
    finally:
        if a is not None:
            a.close()
        rt.close()


@pg
def test_163_samletporten_staar_pa_ankeret_ikke_per_rad(migrator):
    """#163 (K2-dom B): invariantens NIVÅ og krokens nivå er det samme —
    den utsatte porten bor på prosessankeret (én gang per prosess),
    medlemmene bærer bare den billige markøren som armerer den. De
    behavioralporte over (ett lager alene, forsinket INSERT,
    rekkefølgebeviset) beviser at armeringen virker; denne beviser at
    per-rad-formen ikke kan snike seg tilbake.

    MUTASJONEN SOM DREPER DENNE: flytt constraint-triggeren tilbake på
    lagrene (057-formen), eller mist en markør."""
    utsatte = {r[0]: r[1] for r in migrator.execute(
        "SELECT tgrelid::regclass::text, count(*) FROM pg_trigger"
        " WHERE tgname LIKE '%reapes_samlet' AND NOT tgisinternal"
        " GROUP BY 1").fetchall()}
    assert utsatte == {"rekrutteringsprosess": 1}, utsatte
    markorer = {r[0] for r in migrator.execute(
        "SELECT tgrelid::regclass::text FROM pg_trigger"
        " WHERE tgname LIKE '%\\_beroert' AND NOT tgisinternal"
    ).fetchall()}
    assert markorer == set(MEDLEMMER), markorer
    migrator.rollback()


@pg
def test_163_forfalsket_markortabell_feller_skrivet(migrator):
    """CodeRabbit på #163-formen: TEMP-retten er PUBLICs, så en kaller
    kunne pre-lage markørtabellen ferdig seedet og kvele armeringen for
    sine egne skriv. Porten eier tabellen sin: feil eier feller SKRIVET,
    fail-closed — aldri bare markeringen."""
    rt = _rt()
    try:
        _, pid = _prosess(migrator, rt)
        rt.commit()
        _sett_kontekst(rt, TENANT)
        rt.execute(
            "CREATE TEMP TABLE m57_beroerte_prosesser ("
            " tenant TEXT NOT NULL, prosess_id UUID NOT NULL,"
            " PRIMARY KEY (tenant, prosess_id)) ON COMMIT DROP")
        rt.execute(
            "INSERT INTO pg_temp.m57_beroerte_prosesser VALUES (%s,%s)",
            (TENANT, pid))
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as e:
            _fyll_lagrene(rt, pid)
        assert "markørtabellen" in str(e.value)
        rt.rollback()
        # Positiv kontroll: uten forfalskningen går samme skriv.
        _sett_kontekst(rt, TENANT)
        _fyll_lagrene(rt, pid)
        rt.commit()
    finally:
        rt.close()


@pg
def test_164_payloadvinduet_stenger_forsinkede_skriv(migrator):
    """#164 (eiers B): forutsetningene utledes av ankerets tilstands-
    maskin — én kilde. Passert frist (før reaperens batch) og bestilt
    tidligsletting stenger BÅDE lagrenes INSERT og ankerdøren; lukking
    innenfor fristen er en friststart, aldri en skrivesperre (eiers
    presisering i #153, målt som positiv kontroll).

    MUTASJONEN SOM DREPER DENNE: sett vaktens/dørens forutsetning
    tilbake til `slettet_ts` alene."""
    rt = _rt()
    try:
        # (1) Passert frist, reaperen HAR IKKE kjørt: vinduet er stengt
        # — målt på BEGGE portene (CodeRabbit): kandidaten fødes MENS
        # vinduet er åpent, så den direkte lager-INSERT-en etterpå måler
        # LAGERVAKTENS vindu-arm, ikke bare dørens.
        _, pid = _prosess(migrator, rt, frist=30)
        _fyll_lagrene(rt, pid)
        _sett_kontekst(rt, TENANT)
        aapen_kid = uuid.uuid4()
        rt.execute("SELECT opprett_kandidat(%s,%s,%s)",
                   (TENANT, pid, aapen_kid))
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '31 days')", (TENANT, pid))
        rt.commit()
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT slettet_ts IS NULL FROM rekrutteringsprosess"
            " WHERE tenant=%s AND prosess_id=%s",
            (TENANT, pid)).fetchone()[0], \
            "positiv kontroll: reaperen skal IKKE ha kjørt"
        migrator.rollback()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as e1:
            rt.execute(
                "INSERT INTO kandidat_evalueringsartefakt (tenant,"
                " prosess_id, kandidat_id, artefakt, innhold_sha256)"
                " VALUES (%s,%s,%s,'{}','0')",
                (TENANT, pid, aapen_kid))
        assert "payloadvinduet" in str(e1.value), \
            "lagervaktens EGEN vindu-arm skal felle skrivet"
        rt.rollback()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as e2:
            rt.execute("SELECT opprett_kandidat(%s,%s,%s)",
                       (TENANT, pid, uuid.uuid4()))
        assert "payloadvinduet" in str(e2.value)
        rt.rollback()

        # (2) Bestilt tidligsletting stenger umiddelbart — før batchen,
        # målt på lagervakten med en alt født kandidat (samme grep).
        _, pid2 = _prosess(migrator, rt)
        _fyll_lagrene(rt, pid2)
        _sett_kontekst(rt, TENANT)
        bestilt_kid = uuid.uuid4()
        rt.execute("SELECT opprett_kandidat(%s,%s,%s)",
                   (TENANT, pid2, bestilt_kid))
        rt.execute("SELECT bestill_tidligsletting(%s,%s)", (TENANT, pid2))
        rt.commit()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as e3:
            rt.execute(
                "INSERT INTO kandidat_evalueringsartefakt (tenant,"
                " prosess_id, kandidat_id, artefakt, innhold_sha256)"
                " VALUES (%s,%s,%s,'{}','0')",
                (TENANT, pid2, bestilt_kid))
        assert "payloadvinduet" in str(e3.value)
        rt.rollback()

        # (3) Lukket INNENFOR fristen: friststart, ikke skrivesperre —
        # payload etter lukking reapes med prosessen (kortere levetid).
        _, pid3 = _prosess(migrator, rt)
        _fyll_lagrene(rt, pid3)
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s, now())",
                   (TENANT, pid3))
        rt.commit()
        _sett_kontekst(rt, TENANT)
        _fyll_lagrene(rt, pid3)
        rt.commit()
    finally:
        rt.close()
