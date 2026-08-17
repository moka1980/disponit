"""Modul-onboarding (migrasjon 035) — lagringskontrakten og de herdede
funksjonene.

Klarsignalets bærende skille: TOKENET AUTENTISERER, REGISTERET AUTORISERER.
Her prøves fase 1+2 (hemmelighet → token), rotasjonen, familiehorisonten og
— viktigst — at LAGRINGEN håndhever kontrakten for alle roller, inkludert
funksjonseierne (portene 35–42: `familiefrist.flyttet_framover_via_noen_
skrivevei = 0`). HTTP-veiene (innløsningsendepunkt, claim, kapabilitet i
claim-svar) prøves i `test_modul_onboarding_http.py`.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, VARSEL_DSN

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _c():
    from db.pg import koble
    return koble(MIGRATOR_DSN)


def _rt():
    from db.pg import koble
    return koble(DSN)


def _vs():
    """Senderrollen — den ENESTE som får kalle familiehorisont-sveipen.

    Sveipen er kryss-tenant (den setter den oppgitte tenantens RLS-kontekst),
    så den hører til `disponit_varselsender` alene, ikke til web-runtime.
    Faller tilbake til migratoren i miljøer uten senderens DSN; CI setter den,
    og der er det senderrollen som faktisk prøves.
    """
    from db.pg import koble
    return koble(VARSEL_DSN or MIGRATOR_DSN)


def _mid():
    return "m-" + secrets.token_hex(4)


def _hex64():
    return secrets.token_hex(32)


def _deployment_med_typer(c, *, status="aktiv", livslop="claiming",
                          miljo="staging", typer=1):
    """Full kjede modulhode→kontrakt→release→deployment (+ registrerte
    oppdragstyper under releasens kontrakt). -> (modul, rel, ver, khash)."""
    modul, rel, ver = _mid(), "r-" + secrets.token_hex(3), 1
    khash = "k-" + secrets.token_hex(8)
    c.execute("INSERT INTO modulhode (modul_id,status) VALUES (%s,%s)",
              (modul, status))
    c.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,%s,%s,'p','k','krever_outbox',"
        "'kompenserende')", (modul, ver, khash))
    c.execute(
        "INSERT INTO modulrelease (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,manifest_hash,artifact_digest) VALUES"
        " (%s,%s,%s,%s,'mh','ad')", (modul, rel, ver, khash))
    c.execute(
        "INSERT INTO moduldeployment (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,miljo,livslop) VALUES (%s,%s,%s,%s,%s,%s)",
        (modul, rel, ver, khash, miljo, livslop))
    for i in range(typer):
        c.execute(
            "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
            "kontraktversjon,kontrakt_hash) VALUES (%s,%s,%s,%s)",
            (f"t{secrets.token_hex(4)}.{i}", modul, ver, khash))
    c.commit()
    return modul, rel, ver, khash


def _utsted(rt, modul, miljo, rel, *, dager=365, ttl=60, oid=None,
            hemmelighet_hash=None):
    oid = oid or uuid.uuid4()
    return oid, rt.execute(
        "SELECT * FROM utsted_onboarding_hemmelighet(%s,%s,%s,%s,%s,%s,%s,"
        "'test')", (modul, miljo, rel, oid,
                    hemmelighet_hash or _hex64(), dager, ttl)).fetchone()


def _innlos(rt, oid, hemmelighet_hash, *, dager=30, tid=None, mac=None):
    tid = tid or uuid.uuid4()
    rad = rt.execute(
        "SELECT * FROM innlos_onboarding(%s,%s,%s,%s,%s,'test')",
        (oid, hemmelighet_hash, tid, mac or _hex64(), dager)).fetchone()
    return tid, rad


# --------------------------------------------------------------------------
# Fase 1: utstedelse (portene 1–3 på DB-nivå; scope-porten prøves i HTTP)
# --------------------------------------------------------------------------

@pg
def test_utstedelse_krever_claiming_aktiv_og_registrert_type():
    """Port 2: maskinverifisert deploymentevidens — ikke bare scope. Et token
    uten claimbart arbeid er en hemmelighet på avveie som venter."""
    m = _c()
    rt = _rt()
    try:
        # draining deployment → avvist
        modul, rel, _, _ = _deployment_med_typer(m, livslop="draining")
        with pytest.raises(psycopg.errors.NoDataFound):
            _utsted(rt, modul, "staging", rel)
        rt.rollback()
        # status installert → avvist
        modul, rel, _, _ = _deployment_med_typer(m, status="installert")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _utsted(rt, modul, "staging", rel)
        rt.rollback()
        # ingen oppdragstype under releasens kontrakt → avvist
        modul, rel, _, _ = _deployment_med_typer(m, typer=0)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _utsted(rt, modul, "staging", rel)
        rt.rollback()
        # alt på plass → utstedt, med frister fra SERVERENS argumenter
        modul, rel, _, _ = _deployment_med_typer(m)
        oid, rad = _utsted(rt, modul, "staging", rel)
        rt.commit()
        assert rad[0] == oid and rad[1] < rad[2], rad
    finally:
        rt.close()
        m.close()


@pg
def test_ett_ubrukt_onboarding_per_deployment_men_utlopt_erstattes():
    """Unik-indeksen stopper to VENTENDE hemmeligheter; en glemt (utløpt,
    ubrukt) skal derimot ikke blokkere for alltid — den har aldri produsert
    et token og ryddes av neste utstedelse."""
    m = _c()
    rt = _rt()
    try:
        modul, rel, _, _ = _deployment_med_typer(m)
        oid1, _ = _utsted(rt, modul, "staging", rel)
        rt.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            _utsted(rt, modul, "staging", rel)
        rt.rollback()
        # La den utløpe (migrator kan sette klokka på en UBRUKT rad? Nei —
        # utloper er frosset av triggeren. Konstruer i stedet en rad som ER
        # utløpt: direkte INSERT som migrator, forbi funksjonens TTL.)
        modul2, rel2, _, _ = _deployment_med_typer(m)
        m.execute(
            "INSERT INTO modul_onboarding (onboarding_id,modul_id,miljo,"
            "release_id,hemmelighet_hash,familie_utloper,utstedt_av,utloper)"
            " VALUES (%s,%s,'staging',%s,%s,now()+interval '1 day','t',"
            " now()-interval '1 minute')",
            (uuid.uuid4(), modul2, rel2, _hex64()))
        m.commit()
        oid3, _ = _utsted(rt, modul2, "staging", rel2)
        rt.commit()
        assert oid3 is not None
    finally:
        rt.close()
        m.close()


# --------------------------------------------------------------------------
# Fase 2: innløsning (portene 4–6)
# --------------------------------------------------------------------------

@pg
def test_innlosning_er_engangs(monkeypatch=None):
    """Port 4: innløst to ganger → andre avvist, kun ett token. Og feil
    hemmelighet er SAMME feil utad som brukt hemmelighet (intet orakel).

    Codex P2: avvisningen RAISER ikke — den returnerer `avvist` satt, så
    `avvist_bruk`-hendelsen overlever committen. Kontroll: bytt returen i
    `innlos_onboarding` tilbake til RAISE, så blir sporet tomt her."""
    m = _c()
    rt = _rt()
    try:
        modul, rel, _, _ = _deployment_med_typer(m)
        hh = _hex64()
        oid, _ = _utsted(rt, modul, "staging", rel, hemmelighet_hash=hh)
        rt.commit()
        tid, rad = _innlos(rt, oid, hh)
        rt.commit()
        assert rad[1] == modul and rad[4] == 0     # modul_id, utstedt_epoch
        assert rad[7] is None                      # avvist
        # ... andre gang med RIKTIG hemmelighet, og en gang med FEIL:
        # samme utfall, og begge havner i sporet.
        for hash_ in (hh, _hex64()):
            _, avslag = _innlos(rt, oid, hash_)
            assert avslag[7] == "innlosning_avvist", avslag
            assert avslag[0] is None               # ingen token_id
            rt.commit()
        assert m.execute("SELECT count(*) FROM modultoken WHERE"
                         " modul_id=%s", (modul,)).fetchone()[0] == 1
        # Revisjonssporet HUSKER forsøkene — det er hele poenget med at
        # avvisningen committes i stedet for å raise.
        rader = m.execute(
            "SELECT detalj->>'grunn' FROM modultoken_hendelse"
            " WHERE onboarding_id=%s AND hendelse='avvist_bruk'",
            (oid,)).fetchall()
        m.rollback()
        assert rader == [("innlosning_avvist",)] * 2, rader
    finally:
        rt.close()
        m.close()


@pg
def test_to_samtidige_innlosninger_gir_noyaktig_ett_token():
    """Port 5: radlåsen serialiserer; taperen ser innlost_ts og avvises."""
    m = _c()
    try:
        modul, rel, _, _ = _deployment_med_typer(m)
        hh = _hex64()
        rt0 = _rt()
        oid, _ = _utsted(rt0, modul, "staging", rel, hemmelighet_hash=hh)
        rt0.commit()
        rt0.close()
        utfall = []

        def prov():
            rt = _rt()
            try:
                _, rad = _innlos(rt, oid, hh)
                rt.commit()
                utfall.append("avvist" if rad[7] is not None else "ok")
            except psycopg.errors.InvalidParameterValue:
                rt.rollback()
                utfall.append("avvist")
            finally:
                rt.close()

        t1, t2 = threading.Thread(target=prov), threading.Thread(target=prov)
        t1.start(); t2.start(); t1.join(); t2.join()
        assert sorted(utfall) == ["avvist", "ok"], utfall
        assert m.execute("SELECT count(*) FROM modultoken WHERE modul_id=%s",
                         (modul,)).fetchone()[0] == 1
    finally:
        m.close()


@pg
def test_utlopt_hemmelighet_avvises():
    """Port 6: > TTL → avvist. TTL-en er serverens, ikke requestens.

    Også utløpet auditeres nå (Codex P2) — grunnen skiller seg i SPORET,
    aldri i svaret utad."""
    m = _c()
    rt = _rt()
    try:
        modul, rel, _, _ = _deployment_med_typer(m)
        hh = _hex64()
        oid = uuid.uuid4()
        m.execute(
            "INSERT INTO modul_onboarding (onboarding_id,modul_id,miljo,"
            "release_id,hemmelighet_hash,familie_utloper,utstedt_av,utloper)"
            " VALUES (%s,%s,'staging',%s,%s,now()+interval '365 days','t',"
            " now()-interval '1 second')", (oid, modul, rel, hh))
        m.commit()
        _, rad = _innlos(rt, oid, hh)
        rt.commit()
        assert rad[7] == "innlosning_utlopt" and rad[0] is None, rad
        spor = m.execute(
            "SELECT detalj->>'grunn' FROM modultoken_hendelse"
            " WHERE onboarding_id=%s AND hendelse='avvist_bruk'",
            (oid,)).fetchall()
        m.rollback()
        assert spor == [("innlosning_utlopt",)], spor
        # ... og hemmeligheten er IKKE merket brukt av forsøket.
        assert m.execute("SELECT innlost_ts FROM modul_onboarding WHERE"
                         " onboarding_id=%s", (oid,)).fetchone()[0] is None
        m.rollback()
    finally:
        rt.close()
        m.close()


# --------------------------------------------------------------------------
# Rotasjon og familiehorisont (portene 20–23, 27–31)
# --------------------------------------------------------------------------

def _token(rt, m, *, familie_dager=365, token_dager=30):
    modul, rel, _, _ = _deployment_med_typer(m)
    hh = _hex64()
    oid, _ = _utsted(rt, modul, "staging", rel, dager=familie_dager,
                     hemmelighet_hash=hh)
    tid, rad = _innlos(rt, oid, hh, dager=token_dager)
    rt.commit()
    return modul, tid, rad


@pg
def test_rotasjon_ny_virker_forgjenger_faar_naade_kjeden_sporbar():
    """Port 20: etterfølgeren arver familie + epoch; forgjengeren
    tilbakekalles med 15 minutters nåde (fremtidig tilbakekalt_ts) og er
    GYLDIG i vinduet — in-flight-requests skal ikke dø av en rotasjon."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        ny_mac = _hex64()
        ny = rt.execute(
            "SELECT * FROM roter_modultoken(%s,%s,%s,30,'test')",
            (tid, uuid.uuid4(), ny_mac)).fetchone()
        rt.commit()
        rad = m.execute(
            "SELECT forgjenger, utstedt_epoch FROM modultoken WHERE"
            " token_id=%s", (ny[0],)).fetchone()
        assert rad[0] == tid and rad[1] == 0
        g = m.execute(
            "SELECT tilbakekalt_ts > now(), tilbakekalt_grunn FROM modultoken"
            " WHERE token_id=%s", (tid,)).fetchone()
        assert g[0] is True and g[1] == "rotert", g
        # ... og verifiseringen godtar forgjengeren i nådevinduet
        gammel_mac = m.execute("SELECT token_mac FROM modultoken WHERE"
                               " token_id=%s", (tid,)).fetchone()[0]
        assert rt.execute("SELECT * FROM verifiser_modultoken(%s)",
                          (gammel_mac,)).fetchone() is not None
        rt.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_gjentatt_rotasjon_med_samme_noekkel_gjenoppretter_tapt_svar():
    """Codex P1: hemmeligheten mynter serveren og viser den ÉN gang. Går
    201-svaret tapt, holder INGEN etterfølgeren — men raden opptar
    forgjengerens eneste etterfølgerplass, og modulen var før dette ute av
    drift så snart nåden løp ut, til et menneske onboardet på nytt. Med
    idempotensnøkkelen mynter det gjentatte forsøket neste forsøk i SAMME
    rotasjon; en ANNEN nøkkel (eller ingen) er fortsatt en konflikt — det er
    nettopp forskjellen på «samme forsøk om igjen» og «to rotasjoner».

    Codex P1 (runde 3): forsøkene tar IKKE livet av hverandre. Serveren vet
    ikke om det forrige svaret gikk tapt eller bare var forsinket, så en
    tilbakekalling av forrige forsøk kunne drept nettopp den hemmeligheten
    deploymenten hadde lagret. BEGGE lever, og deploymenten bruker den den
    fikk.

    Kontroll: fjern `rotasjon_id`-grenen i `roter_modultoken`, så gir det
    gjentatte forsøket `UniqueViolation` i stedet for et token."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        nokkel = uuid.uuid4()
        forste, forste_mac = uuid.uuid4(), _hex64()
        rt.execute("SELECT * FROM roter_modultoken(%s,%s,%s,30,'test',%s)",
                   (tid, forste, forste_mac, nokkel))
        rt.commit()                       # ... og svaret kom kanskje aldri frem

        fersk, mac = uuid.uuid4(), _hex64()
        rad = rt.execute(
            "SELECT * FROM roter_modultoken(%s,%s,%s,30,'test',%s)",
            (tid, fersk, mac, nokkel)).fetchone()
        rt.commit()
        assert rad[0] == fersk, rad
        # BEGGE forsøkene lever: serveren kan ikke vite hvilket svar som kom
        # frem, og skal ikke drepe en hemmelighet som kan være i bruk.
        assert rt.execute("SELECT * FROM verifiser_modultoken(%s)",
                          (mac,)).fetchone() is not None
        assert rt.execute("SELECT * FROM verifiser_modultoken(%s)",
                          (forste_mac,)).fetchone() is not None
        assert m.execute(
            "SELECT rotasjon_forsok, tilbakekalt_ts FROM modultoken"
            " WHERE token_id=%s", (fersk,)).fetchone() == (2, None)
        # ... og forsøket står i det append-only sporet.
        assert (fersk,) in m.execute(
            "SELECT token_id FROM modultoken_hendelse WHERE hendelse="
            "'rotert' AND detalj->>'forsok'='2'").fetchall()
        m.rollback()

        # KONVERGENS: rotasjonen videre fra det tokenet deploymenten faktisk
        # holder, dreper søskenet den aldri tok i bruk.
        rt.execute("SELECT * FROM roter_modultoken(%s,%s,%s,30,'test',%s)",
                   (fersk, uuid.uuid4(), _hex64(), uuid.uuid4()))
        rt.commit()
        assert m.execute(
            "SELECT tilbakekalt_ts <= now(), tilbakekalt_grunn FROM"
            " modultoken WHERE token_id=%s", (forste,)).fetchone() \
            == (True, "soesken_ikke_valgt")
        m.rollback()

        # En ANNEN nøkkel er en ekte konflikt — og det er også fraværet av
        # nøkkel: et forsøk som ikke identifiserer seg kan ikke gjenkjennes.
        for arg in (uuid.uuid4(), None):
            with pytest.raises(psycopg.errors.UniqueViolation):
                rt.execute(
                    "SELECT * FROM roter_modultoken(%s,%s,%s,30,'test',%s)",
                    (tid, uuid.uuid4(), _hex64(), arg))
            rt.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_gjentatte_forsok_har_et_tak_i_lagringen():
    """Retten til å prøve på nytt er ikke en rett til å mynte i det
    uendelige: fem forsøk per rotasjon, håndhevet av CHECK-en i lagringen
    (og av funksjonen, som svarer 409 før den treffer den).

    Kontroll: fjern taksjekken i `roter_modultoken`, så faller kallet på
    CheckViolation i stedet — og fjernes CHECK-en også, blir denne rød."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        nokkel = uuid.uuid4()
        for _ in range(5):
            rt.execute(
                "SELECT * FROM roter_modultoken(%s,%s,%s,30,'test',%s)",
                (tid, uuid.uuid4(), _hex64(), nokkel))
            rt.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            rt.execute(
                "SELECT * FROM roter_modultoken(%s,%s,%s,30,'test',%s)",
                (tid, uuid.uuid4(), _hex64(), nokkel))
        rt.rollback()
        assert m.execute("SELECT count(*) FROM modultoken WHERE forgjenger=%s",
                         (tid,)).fetchone()[0] == 5
    finally:
        rt.close()
        m.close()


@pg
def test_soesken_hoerer_til_samme_rotasjon():
    """Vakten i lagringen: et forsøk > 1 er neste forsøk i den rotasjonen
    forsøk 1 startet. Uten den kunne to ULIKE rotasjoner delt forgjengeren
    bare de valgte hvert sitt nummer — nettopp familiegreningen
    én-rotasjon-regelen finnes for å stoppe.

    Kontroll: fjern `modultoken_soesken`-triggeren, så blir denne rød."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        rt.execute("SELECT * FROM roter_modultoken(%s,%s,%s,30,'test',%s)",
                   (tid, uuid.uuid4(), _hex64(), uuid.uuid4()))
        rt.commit()
        rad = m.execute("SELECT onboarding_id, familie_utloper, modul_id,"
                        " miljo, release_id, utstedt_epoch FROM modultoken"
                        " WHERE token_id=%s", (tid,)).fetchone()
        # Migratoren selv — annen rotasjonsnøkkel, ledig forsøksnummer.
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute(
                "INSERT INTO modultoken (token_id,token_mac,onboarding_id,"
                "familie_utloper,modul_id,miljo,release_id,utstedt_epoch,"
                "forgjenger,rotasjon_id,rotasjon_forsok,utloper)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,2,now()+"
                "interval '1 day')",
                (uuid.uuid4(), _hex64()) + tuple(rad) + (tid, uuid.uuid4()))
        m.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_en_forgjenger_faar_noyaktig_en_etterfolger():
    """Portene 21/30: unikheten på (`forgjenger`, `rotasjon_forsok`) er
    garantien I LAGRINGEN — to ULIKE rotasjoner setter begge inn sitt forsøk
    nummer 1, og den andre taper uansett timing. (Gjentatte forsøk i SAMME
    rotasjon er en annen sak; de har egen test.)"""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        rt.execute("SELECT * FROM roter_modultoken(%s,%s,%s,30,'test')",
                   (tid, uuid.uuid4(), _hex64()))
        rt.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            rt.execute("SELECT * FROM roter_modultoken(%s,%s,%s,30,'test')",
                       (tid, uuid.uuid4(), _hex64()))
        rt.rollback()
        # ... og lagringen tar den også når funksjonens gren omgås: samme
        # forsøksnummer to ganger på samme forgjenger.
        rad = m.execute("SELECT onboarding_id, familie_utloper, modul_id,"
                        " miljo, release_id, utstedt_epoch FROM modultoken"
                        " WHERE token_id=%s", (tid,)).fetchone()
        with pytest.raises(psycopg.errors.UniqueViolation):
            m.execute(
                "INSERT INTO modultoken (token_id,token_mac,onboarding_id,"
                "familie_utloper,modul_id,miljo,release_id,utstedt_epoch,"
                "forgjenger,utloper) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "now()+interval '1 day')",
                (uuid.uuid4(), _hex64()) + tuple(rad) + (tid,))
        m.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_rotasjonen_returnerer_forgjengerens_faktiske_frist():
    """Codex P2: svaret sa alltid «15 minutter», uansett hva forgjengeren
    faktisk hadde igjen. Nåden er et kvarter FRA NÅ bare når forgjengeren
    var urørt; sto den alt i et nådevindu fra en tidligere hendelse, gjelder
    den gamle fristen (et satt tilbakekalt_ts kan bare fremskyndes). Og
    `verifiser_modultoken` håndhever `utloper` også, så en rotasjon rett før
    tokenets egen utløp gir sekunder, ikke et kvarter. En klient som planla
    overlappende overlevering på et kvarter som ikke fantes, fikk
    overleveringen kuttet midt i.

    Tre tilfeller, én funksjon: det urørte tokenet (nåden gjelder), det som
    utløper før nåden er omme (utløpet gjelder), og det gjentatte forsøket
    (arver forgjengerens frist, den forlenges ikke).

    Kontroll: sett `forgjenger_gyldig_til` til `now() + interval
    '15 minutes'` uten LEAST-en, så blir denne rød."""
    m = _c()
    rt = _rt()
    try:
        # 1. Urørt forgjenger: fristen ER nåden, og den er et kvarter unna.
        modul, tid, _ = _token(rt, m)
        ny = rt.execute("SELECT * FROM roter_modultoken(%s,%s,%s,30,'test')",
                        (tid, uuid.uuid4(), _hex64())).fetchone()
        rt.commit()
        faktisk = m.execute(
            "SELECT tilbakekalt_ts FROM modultoken WHERE token_id=%s",
            (tid,)).fetchone()[0]
        assert ny[3] == faktisk, (ny[3], faktisk)
        # `clock_timestamp`, ikke `now()`: sistnevnte er transaksjonens
        # starttid, og denne forbindelsen har alt stått en stund.
        naa = m.execute("SELECT clock_timestamp()").fetchone()[0]
        assert 840 < (ny[3] - naa).total_seconds() <= 900, ny[3]
        m.rollback()

        # 2. Forgjenger som utløper FØR nåden er omme: utløpet er fristen.
        modul2, rel2, _, _ = _deployment_med_typer(m)
        o2, t2 = uuid.uuid4(), uuid.uuid4()
        m.execute(
            "INSERT INTO modul_onboarding (onboarding_id,modul_id,miljo,"
            "release_id,hemmelighet_hash,familie_utloper,utstedt_av,utloper,"
            "innlost_ts) VALUES (%s,%s,'staging',%s,%s,"
            "now()+interval '4 minutes','t',now(),now())",
            (o2, modul2, rel2, _hex64()))
        m.execute(
            "INSERT INTO modultoken (token_id,token_mac,onboarding_id,"
            "familie_utloper,modul_id,miljo,release_id,utstedt_epoch,"
            "utloper) SELECT %s,%s,onboarding_id,familie_utloper,modul_id,"
            "miljo,release_id,0,familie_utloper FROM modul_onboarding"
            " WHERE onboarding_id=%s", (t2, _hex64(), o2))
        m.commit()
        kort = m.execute("SELECT utloper FROM modultoken WHERE token_id=%s",
                         (t2,)).fetchone()[0]
        ny2 = rt.execute("SELECT * FROM roter_modultoken(%s,%s,%s,30,'test')",
                         (t2, uuid.uuid4(), _hex64())).fetchone()
        rt.commit()
        assert ny2[3] == kort, (ny2[3], kort)

        # 3. Gjentatt forsøk: forgjengeren står alt i nåde fra forsøk 1, og
        #    den fristen FORLENGES IKKE av at det kommer et forsøk til.
        modul3, tid3, _ = _token(rt, m)
        rot = uuid.uuid4()
        a1 = rt.execute(
            "SELECT * FROM roter_modultoken(%s,%s,%s,30,'test',%s)",
            (tid3, uuid.uuid4(), _hex64(), rot)).fetchone()
        rt.commit()
        a2 = rt.execute(
            "SELECT * FROM roter_modultoken(%s,%s,%s,30,'test',%s)",
            (tid3, uuid.uuid4(), _hex64(), rot)).fetchone()
        rt.commit()
        assert a2[3] == a1[3], (a1[3], a2[3])
    finally:
        rt.close()
        m.close()


@pg
def test_rotasjon_kappes_mot_familiehorisonten_og_stopper_ved_den():
    """Portene 27/29: nær fristen → utloper == familie_utloper, aldri
    senere; etter fristen → avvist, ny onboarding kreves."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, rad = _token(rt, m, familie_dager=1, token_dager=30)
        # tokenets levetid var alt kappet ved innløsningen
        assert rad[5] == rad[6], rad                    # utloper == familie
        ny = rt.execute("SELECT * FROM roter_modultoken(%s,%s,%s,30,'test')",
                        (tid, uuid.uuid4(), _hex64())).fetchone()
        rt.commit()
        assert ny[1] == ny[2], ny                       # kappet igjen
        # Konstruer et token i en familie som ER passert (migrator, direkte)
        modul2, rel2, _, _ = _deployment_med_typer(m)
        o2, t2 = uuid.uuid4(), uuid.uuid4()
        m.execute(
            "INSERT INTO modul_onboarding (onboarding_id,modul_id,miljo,"
            "release_id,hemmelighet_hash,familie_utloper,utstedt_av,utloper,"
            "innlost_ts) VALUES (%s,%s,'staging',%s,%s,"
            "now()+interval '1 second','t',now(),now())",
            (o2, modul2, rel2, _hex64()))
        m.execute(
            "INSERT INTO modultoken (token_id,token_mac,onboarding_id,"
            "familie_utloper,modul_id,miljo,release_id,utstedt_epoch,"
            "utloper) SELECT %s,%s,onboarding_id,familie_utloper,modul_id,"
            "miljo,release_id,0,familie_utloper FROM modul_onboarding"
            " WHERE onboarding_id=%s", (t2, _hex64(), o2))
        m.commit()
        import time
        time.sleep(1.2)                                  # fristen passerer
        # CHECK-en (utloper <= familie_utloper) gjør at et token ALDRI kan
        # overleve familien sin — «etter fristen» treffer derfor alltid
        # utløps-avvisningen først, og familie-grenen i funksjonen er
        # belte-og-seler. Begge tekstene er samme avslag: rotasjonen skjer
        # ikke, ny onboarding kreves.
        with pytest.raises(psycopg.errors.InvalidParameterValue,
                           match="utlopt|familiehorisont"):
            rt.execute("SELECT * FROM roter_modultoken(%s,%s,%s,30,'test')",
                       (t2, uuid.uuid4(), _hex64()))
        rt.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_tilbakekalling_kapper_rotasjonsnaaden_umiddelbart():
    """Port 22: nåden er for in-flight-requests, ikke for kompromitterte
    tokener. Roterer man først (forgjengeren får 15 minutters nåde) og
    tilbakekaller forgjengeren eksplisitt etterpå, skal den dø NÅ — ikke
    om 15 minutter. Kontroll: gjør triggeren uforanderlig igjen (avvis
    all endring av et satt tilbakekalt_ts), så blir denne rød."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        mac = m.execute("SELECT token_mac FROM modultoken WHERE token_id=%s",
                        (tid,)).fetchone()[0]
        rt.execute("SELECT * FROM roter_modultoken(%s,%s,%s,30,'test')",
                   (tid, uuid.uuid4(), _hex64()))
        rt.commit()
        # nåden gjelder: forgjengeren verifiserer fortsatt
        assert rt.execute("SELECT * FROM verifiser_modultoken(%s)",
                          (mac,)).fetchone() is not None
        rt.rollback()
        rt.execute("SELECT tilbakekall_modultoken(%s,'kompromittert','test')",
                   (tid,))
        rt.commit()
        assert rt.execute("SELECT * FROM verifiser_modultoken(%s)",
                          (mac,)).fetchone() is None
        rt.rollback()
        # FRISK snapshot: `now()` er transaksjonens starttid, og m-en har
        # stått åpen siden før tilbakekallingen — uten dette sammenlignes
        # fristen med en klokke som er eldre enn den.
        m.rollback()
        rad = m.execute(
            "SELECT tilbakekalt_ts <= now(), tilbakekalt_grunn FROM"
            " modultoken WHERE token_id=%s", (tid,)).fetchone()
        assert rad == (True, "kompromittert"), rad
        # ... og revisjonssporet har begge hendelsene
        assert m.execute(
            "SELECT count(*) FROM modultoken_hendelse WHERE token_id=%s"
            " AND hendelse='tilbakekalt'", (tid,)).fetchone()[0] == 1
    finally:
        rt.close()
        m.close()


@pg
def test_nodstopp_kapper_ogsaa_tokener_i_rotasjonsnaade():
    """Port 23: et nødstopp er unntakstilstanden nåden finnes for å unngå
    — også tokener med fremtidig tilbakekalt_ts kappes til now()."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        mac = m.execute("SELECT token_mac FROM modultoken WHERE token_id=%s",
                        (tid,)).fetchone()[0]
        rt.execute("SELECT * FROM roter_modultoken(%s,%s,%s,30,'test')",
                   (tid, uuid.uuid4(), _hex64()))
        rt.commit()
        m.execute("SET ROLE disponit_modules_admin")
        m.execute("SELECT noddeaktiver_modul(%s,'test av 035','test')",
                  (modul,))
        m.execute("RESET ROLE")
        m.commit()
        assert rt.execute("SELECT * FROM verifiser_modultoken(%s)",
                          (mac,)).fetchone() is None
        rt.rollback()
        rad = m.execute(
            "SELECT tilbakekalt_ts <= now(), tilbakekalt_grunn FROM"
            " modultoken WHERE token_id=%s", (tid,)).fetchone()
        assert rad == (True, "epoch_okning_nodstopp"), rad
    finally:
        rt.close()
        m.close()


@pg
def test_tilbakekalt_token_avvises_umiddelbart_og_gjenopplives_aldri():
    """Portene 22/42: eksplisitt tilbakekalling er umiddelbar; nulling av
    tilbakekalt_ts avvises av lagringen — også for migrator/eier."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        mac = m.execute("SELECT token_mac FROM modultoken WHERE token_id=%s",
                        (tid,)).fetchone()[0]
        rt.execute("SELECT tilbakekall_modultoken(%s,'kompromittert','test')",
                   (tid,))
        rt.commit()
        assert rt.execute("SELECT * FROM verifiser_modultoken(%s)",
                          (mac,)).fetchone() is None
        rt.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE modultoken SET tilbakekalt_ts=NULL WHERE"
                      " token_id=%s", (tid,))
        m.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE modultoken SET tilbakekalt_ts=now()+interval"
                      " '1 day' WHERE token_id=%s", (tid,))
        m.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_epoch_okning_terminerer_familien_og_rotasjon_arver_aldri_ny():
    """Port 23: nødstopp/reaktivering tilbakekaller alle levende tokener i
    SAMME transaksjon som epoch-bumpen; rotasjon ARVER forgjengerens epoch
    og kan aldri plukke opp den nye. Kontroll: fjern 035-blokken i
    `noddeaktiver_modul`, så blir denne rød."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        m.execute("SET ROLE disponit_modules_admin")
        m.execute("SELECT noddeaktiver_modul(%s,'test av 035','test')",
                  (modul,))
        m.execute("RESET ROLE")
        m.commit()
        rad = m.execute(
            "SELECT tilbakekalt_ts <= now(), tilbakekalt_grunn FROM"
            " modultoken WHERE token_id=%s", (tid,)).fetchone()
        assert rad == (True, "epoch_okning_nodstopp"), rad
        # rotasjon av det tilbakekalte tokenet → avvist (ingen vei tilbake
        # uten ny onboarding)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            rt.execute("SELECT * FROM roter_modultoken(%s,%s,%s,30,'test')",
                       (tid, uuid.uuid4(), _hex64()))
        rt.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_mynting_tar_modullaasen_som_epoch_endringene():
    """Codex P1: nødstoppet er en UPDATE på et snapshot. Et token som fødes
    inne i det snapshotet blir aldri sett — og altså aldri drept — av
    stoppet, og overlever som et levende token i en terminert familie.
    Serialiseringen finnes allerede (`modul:<id>`-låsen som
    `noddeaktiver_modul`/`reaktiver_modul` tar); den manglet i BEGGE
    myntingsveiene.

    Prøven er direkte: hold låsen i en annen transaksjon, og se at både
    innløsning og rotasjon VENTER på den. Kontroll: fjern
    `pg_advisory_xact_lock`-linja i `innlos_onboarding`/`roter_modultoken`,
    så går kallet gjennom og `LockNotAvailable` uteblir.
    """
    m = _c()
    rt = _rt()
    holder = _c()
    try:
        modul, rel, _, _ = _deployment_med_typer(m)
        hh = _hex64()
        oid, _ = _utsted(rt, modul, "staging", rel, hemmelighet_hash=hh)
        rt.commit()

        holder.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('modul:'||%s,0))",
            (modul,))                       # holdes til rollback under

        rt.execute("SET LOCAL lock_timeout='400ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            _innlos(rt, oid, hh)
        rt.rollback()

        holder.rollback()                   # låsen slippes
        tid, rad = _innlos(rt, oid, hh)
        rt.commit()
        assert rad[7] is None, rad

        # ... og rotasjonen står i samme kø.
        holder.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('modul:'||%s,0))",
            (modul,))
        rt.execute("SET LOCAL lock_timeout='400ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            rt.execute("SELECT * FROM roter_modultoken(%s,%s,%s,30,'test')",
                       (tid, uuid.uuid4(), _hex64()))
        rt.rollback()
        holder.rollback()
    finally:
        holder.close()
        rt.close()
        m.close()


@pg
def test_innlosning_etter_nodstopp_myntes_ikke():
    """Codex P1, andre halvdel: for rotasjonen holder låsen alene — et
    nødstopp har da tilbakekalt forgjengeren, og rotasjonen faller på det.
    Innløsningen har ingen forgjenger å falle på: uten et vilkår ville en
    innløsning som starter ETTER stoppet født et helt nytt, levende token
    for en nettopp terminert modul. Hemmeligheten er urørt — den kan brukes
    når modulen er tilbake.

    Kontroll: fjern `innlosning_modul_stengt`-grenen, så blir denne rød."""
    m = _c()
    rt = _rt()
    try:
        modul, rel, _, _ = _deployment_med_typer(m)
        hh = _hex64()
        oid, _ = _utsted(rt, modul, "staging", rel, hemmelighet_hash=hh)
        rt.commit()
        m.execute("SET ROLE disponit_modules_admin")
        m.execute("SELECT noddeaktiver_modul(%s,'test av 035','test')",
                  (modul,))
        m.execute("RESET ROLE")
        m.commit()

        _, rad = _innlos(rt, oid, hh)
        rt.commit()
        assert rad[7] == "innlosning_modul_stengt" and rad[0] is None, rad
        assert m.execute("SELECT count(*) FROM modultoken WHERE modul_id=%s",
                         (modul,)).fetchone()[0] == 0
        # Avvisningen står i sporet, og hemmeligheten er IKKE brukt opp.
        assert m.execute(
            "SELECT detalj->>'grunn' FROM modultoken_hendelse"
            " WHERE onboarding_id=%s AND hendelse='avvist_bruk'",
            (oid,)).fetchall() == [("innlosning_modul_stengt",)]
        assert m.execute("SELECT innlost_ts FROM modul_onboarding"
                         " WHERE onboarding_id=%s", (oid,)).fetchone()[0] \
            is None
        m.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_hemmelighet_fra_for_nodstoppet_myntes_ikke_etter_reaktivering():
    """Codex P1: statussjekken fanger et PÅGÅENDE nødstopp, ikke et
    overstått. Etter reaktivering står status igjen på `staging_verifisert`
    mens epochen har steget to ganger — og en ubrukt hemmelighet fra før
    stoppet (fortsatt innenfor sine 60 minutter) ville myntet et fullt
    gjeldende token, forbi kravet om NY onboarding etter reaktivering.
    Hemmeligheten bærer nå epochen sin, og innløsningen krever likhet.

    Kontroll: fjern `innlosning_epoch_endret`-grenen, så blir denne rød.
    """
    m = _c()
    rt = _rt()
    try:
        modul, rel, _, _ = _deployment_med_typer(m)
        hh = _hex64()
        oid, _ = _utsted(rt, modul, "staging", rel, hemmelighet_hash=hh)
        rt.commit()
        assert m.execute("SELECT utstedt_epoch FROM modul_onboarding"
                         " WHERE onboarding_id=%s", (oid,)).fetchone()[0] == 0
        m.execute("SET ROLE disponit_modules_admin")
        m.execute("SELECT noddeaktiver_modul(%s,'test av 035','test')",
                  (modul,))
        m.execute("SELECT reaktiver_modul(%s,1,'test')", (modul,))
        m.execute("RESET ROLE")
        m.commit()
        # Modulen er tilbake i en status innløsningen ellers godtar.
        assert m.execute("SELECT status, module_epoch FROM modulhode"
                         " WHERE modul_id=%s", (modul,)).fetchone() \
            == ("staging_verifisert", 2)

        _, rad = _innlos(rt, oid, hh)
        rt.commit()
        assert rad[7] == "innlosning_epoch_endret" and rad[0] is None, rad
        assert m.execute("SELECT count(*) FROM modultoken WHERE modul_id=%s",
                         (modul,)).fetchone()[0] == 0
        assert m.execute(
            "SELECT detalj->>'grunn' FROM modultoken_hendelse"
            " WHERE onboarding_id=%s AND hendelse='avvist_bruk'",
            (oid,)).fetchall() == [("innlosning_epoch_endret",)]
        m.rollback()
    finally:
        rt.close()
        m.close()


# --------------------------------------------------------------------------
# Lagringskontrakten (portene 25, 31, 35–42) — den holder for ALLE roller,
# også migrator (tabelleier) og dermed funksjonseierne.
# --------------------------------------------------------------------------

@pg
def test_runtime_kan_ikke_skrive_noen_av_tabellene():
    """Port 25. Kontroll: gi disponit INSERT på modultoken, så blir denne
    rød."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        for sql in [
            "INSERT INTO modul_onboarding (onboarding_id,modul_id,miljo,"
            "release_id,hemmelighet_hash,familie_utloper,utstedt_av,utloper)"
            " VALUES (gen_random_uuid(),'x','staging','r','" + "a" * 64
            + "',now(),'t',now())",
            "UPDATE modultoken SET tilbakekalt_grunn='x'",
            "DELETE FROM modultoken_hendelse",
            "SELECT count(*) FROM modultoken",     # heller ikke LESE direkte
        ]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rt.execute(sql)
            rt.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_familiefristen_kan_ikke_flyttes_av_noen_skrivevei():
    """Portene 35–37, 39–41: `familiefrist.flyttet_framover_via_noen_
    skrivevei = 0`. Migrator er tabelleier — klarer ikke DEN, klarer ingen
    funksjonseier det heller."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        oid = m.execute("SELECT onboarding_id FROM modultoken WHERE"
                        " token_id=%s", (tid,)).fetchone()[0]
        # 35: flytt familiefristen (med tokener på familien)
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE modul_onboarding SET familie_utloper ="
                      " familie_utloper + interval '365 days'"
                      " WHERE onboarding_id=%s", (oid,))
        m.rollback()
        # 35 (uten tokener): også en UBRUKT familie er frosset
        modul2, rel2, _, _ = _deployment_med_typer(m)
        o2 = uuid.uuid4()
        m.execute(
            "INSERT INTO modul_onboarding (onboarding_id,modul_id,miljo,"
            "release_id,hemmelighet_hash,familie_utloper,utstedt_av,utloper)"
            " VALUES (%s,%s,'staging',%s,%s,now()+interval '1 day','t',"
            "now()+interval '1 hour')", (o2, modul2, rel2, _hex64()))
        m.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE modul_onboarding SET familie_utloper ="
                      " familie_utloper + interval '365 days'"
                      " WHERE onboarding_id=%s", (o2,))
        m.rollback()
        # 36: tokenets denormaliserte kopi er like frosset
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE modultoken SET familie_utloper ="
                      " familie_utloper + interval '1 day'"
                      " WHERE token_id=%s", (tid,))
        m.rollback()
        # 37/41: INSERT med frist/deployment som ikke matcher familieraden
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            m.execute(
                "INSERT INTO modultoken (token_id,token_mac,onboarding_id,"
                "familie_utloper,modul_id,miljo,release_id,utstedt_epoch,"
                "utloper) SELECT gen_random_uuid(),%s,onboarding_id,"
                "familie_utloper + interval '1 day',modul_id,miljo,"
                "release_id,0,familie_utloper FROM modul_onboarding"
                " WHERE onboarding_id=%s", (_hex64(), oid))
        m.rollback()
        # 39: reparenting til annen familie + senere frist i ÉN setning
        modul3, tid3, _ = _token(rt, m, familie_dager=700)
        o3 = m.execute("SELECT onboarding_id FROM modultoken WHERE"
                       " token_id=%s", (tid3,)).fetchone()[0]
        with pytest.raises((psycopg.errors.CheckViolation,
                            psycopg.errors.ForeignKeyViolation)):
            m.execute(
                "UPDATE modultoken SET onboarding_id=%s, familie_utloper="
                "(SELECT familie_utloper FROM modul_onboarding WHERE"
                " onboarding_id=%s) WHERE token_id=%s", (o3, o3, tid))
        m.rollback()
        # 31: direkte DML med utloper > familie_utloper → CHECK
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE modultoken SET utloper = familie_utloper"
                      " + interval '1 day' WHERE token_id=%s", (tid,))
        m.rollback()
        # 38: DELETE av familierad med levende tokener
        with pytest.raises((psycopg.errors.CheckViolation,
                            psycopg.errors.ForeignKeyViolation)):
            m.execute("DELETE FROM modul_onboarding WHERE onboarding_id=%s",
                      (oid,))
        m.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_identitetsfeltene_er_immutable_og_hendelser_append_only():
    """`modultoken.identitetsfelt_endret = 0` + append-only-sporet."""
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        for kolonne, verdi in [("modul_id", "'x'"), ("release_id", "'x'"),
                               ("utstedt_epoch", "99"),
                               ("utloper", "now()"),
                               ("token_mac", "'" + "b" * 64 + "'")]:
            with pytest.raises(psycopg.errors.CheckViolation):
                m.execute(f"UPDATE modultoken SET {kolonne}={verdi}"
                          " WHERE token_id=%s", (tid,))
            m.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE modultoken_hendelse SET aktor='x'")
        m.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("DELETE FROM modultoken WHERE token_id=%s", (tid,))
        m.rollback()
    finally:
        rt.close()
        m.close()


@pg
def test_grunn_alene_er_ingen_tilbakekalling():
    """Codex P2: en ren grunn-endring på et LEVENDE token var usynlig for
    identitetstriggeren — den så bare på OLD.tilbakekalt_ts. Da kunne
    eieren skrive en tilbakekallingsgrunn på et token som fortsatt virker,
    og sporet ville lyve om tilstanden. Grunnen følger døden: den kan bare
    settes i samme UPDATE som flytter tilbakekalt_ts.

    Kontroll: fjern det siste OR-leddet i `modultoken_identitet_immutable`,
    så blir denne rød.
    """
    m = _c()
    rt = _rt()
    try:
        modul, tid, _ = _token(rt, m)
        for grunn in ("erstattet_etter_tapt_svar", "rotert", "hva som helst"):
            with pytest.raises(psycopg.errors.CheckViolation):
                m.execute("UPDATE modultoken SET tilbakekalt_grunn=%s"
                          " WHERE token_id=%s", (grunn, tid))
            m.rollback()
        # Kontrollen den andre veien: grunn SAMMEN med en ekte død går inn.
        m.execute("UPDATE modultoken SET tilbakekalt_ts=now(),"
                  " tilbakekalt_grunn='kompromittert' WHERE token_id=%s",
                  (tid,))
        m.commit()
        assert m.execute("SELECT tilbakekalt_grunn FROM modultoken"
                         " WHERE token_id=%s", (tid,)).fetchone()[0] \
            == "kompromittert"
    finally:
        rt.close()
        m.close()


@pg
def test_append_only_tabellene_taaler_ikke_truncate():
    """Codex P2: TRUNCATE fyrer INGEN rad-trigger. Uten en statement-vakt
    kunne tabelleieren tømt hele det annonserte revisjonssporet — og
    tokenkjeden og familieankrene med det — uten å møte en eneste av
    immutabilitetsvaktene.

    CASCADE så FK-sperren (FeatureNotSupported) ikke skygger for vakten som
    faktisk prøves: BEFORE TRUNCATE fyrer først. Kontroll: fjern
    `*_ingen_truncate`-triggerne i 035, så blir denne rød.
    """
    m = _c()
    try:
        for t in ("modultoken_hendelse", "modultoken", "modul_onboarding"):
            with pytest.raises(psycopg.errors.CheckViolation):
                m.execute(f"TRUNCATE {t} CASCADE")
            m.rollback()
    finally:
        m.close()


@pg
def test_hemmeligheten_finnes_kun_hashet():
    """Port 3 (DB-halvdelen): kolonnen KAN ikke bære klartekst — CHECK
    krever 64 hex. Klartekst-halvdelen (vist én gang) prøves i HTTP."""
    m = _c()
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute(
                "INSERT INTO modul_onboarding (onboarding_id,modul_id,miljo,"
                "release_id,hemmelighet_hash,familie_utloper,utstedt_av,"
                "utloper) VALUES (gen_random_uuid(),'m','staging','r',"
                "'klartekst-hemmelighet',now(),'t',now())")
        m.rollback()
    finally:
        m.close()


@pg
def test_registrer_artefakttype_navneform_og_prefiksoverlapp():
    """Klarsignalet §4/§8: lukket navneform + overlappssjekk under global
    lås. Kontroll: fjern overlappssjekken i 035-kroppen, så blir denne rød."""
    m = _c()
    try:
        modul, rel, ver, khash = _deployment_med_typer(m)
        m.execute("SET ROLE disponit_modules_admin")
        def reg(navn):
            m.execute("SELECT registrer_artefakttype(%s,%s,%s,%s,%s,'test')",
                      (navn, modul, ver, khash, _hex64()))
        stamme = f"a{secrets.token_hex(3)}"
        reg(f"{stamme}.b.c")
        m.commit()
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            reg("UPPER.ikke.lov")
        m.rollback()
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            reg("bare_to.ledd")
        m.rollback()
        with pytest.raises(psycopg.errors.UniqueViolation):
            reg(f"{stamme}.b.c.d")               # under eksisterende
        m.rollback()
        reg(f"{stamme}.b.cd")                    # punktumgrense: IKKE overlapp
        m.commit()
    finally:
        m.close()


@pg
def test_seedet_testtype_er_registrert_og_reservert():
    """§8: `test.onboarding.kvittering` finnes, eid av testkontrakten.
    (Utlednings-porten — aldri for produksjonsmiljø — prøves i HTTP.)"""
    m = _c()
    try:
        rad = m.execute(
            "SELECT eiermodul FROM artefakttype_register WHERE"
            " artefakttype='test.onboarding.kvittering'").fetchone()
        assert rad == ("m_test_onboarding",)
        status = m.execute("SELECT status FROM modulhode WHERE"
                           " modul_id='m_test_onboarding'").fetchone()[0]
        assert status != "aktiv"
    finally:
        m.close()


# --------------------------------------------------------------------------
# Familiehorisont-varslene (port 32): 30/7/1 døgn, idempotent, kun levende
# familier, kun plattformtenantens aktive admin-medlemmer.
# --------------------------------------------------------------------------

@pg
def test_familieutlop_varsles_30_7_1_idempotent():
    """Kontroll: fjern EXISTS-leddet for levende token i
    `varsle_tokenfamilie_utlop`, så blir dødfamilie-asserten rød; fjern
    ON CONFLICT-nøkkelen, så dobler antallet ved andre kjøring."""
    m = _c()
    vs = _vs()
    ten = "t-famvarsel-" + secrets.token_hex(3)
    try:
        # Plattformtenant med én aktiv admin og én ikke-admin.
        admin = m.execute(
            "INSERT INTO brukeridentitet (issuer, sub) VALUES (%s,%s)"
            " RETURNING bruker_id",
            ("https://idp.example", f"{ten}-adm")).fetchone()[0]
        leser = m.execute(
            "INSERT INTO brukeridentitet (issuer, sub) VALUES (%s,%s)"
            " RETURNING bruker_id",
            ("https://idp.example", f"{ten}-leser")).fetchone()[0]
        from db.pg import sett_kontekst
        sett_kontekst(m, ten, "sys", "r0")
        m.execute("INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
                  " VALUES (%s,%s,ARRAY['admin','leser'])", (ten, admin))
        m.execute("INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
                  " VALUES (%s,%s,ARRAY['leser'])", (ten, leser))
        m.commit()

        def familie(dager_igjen, med_levende_token=True):
            modul, rel, _, _ = _deployment_med_typer(m)
            o = uuid.uuid4()
            m.execute(
                "INSERT INTO modul_onboarding (onboarding_id,modul_id,miljo,"
                "release_id,hemmelighet_hash,familie_utloper,utstedt_av,"
                "utloper,innlost_ts) VALUES (%s,%s,'staging',%s,%s,"
                "now()+make_interval(days => %s),'t',now(),now())",
                (o, modul, rel, _hex64(), dager_igjen))
            m.execute(
                "INSERT INTO modultoken (token_id,token_mac,onboarding_id,"
                "familie_utloper,modul_id,miljo,release_id,utstedt_epoch,"
                "utloper,tilbakekalt_ts,tilbakekalt_grunn)"
                " SELECT %s,%s,onboarding_id,familie_utloper,modul_id,miljo,"
                "release_id,0,familie_utloper,%s,%s FROM modul_onboarding"
                " WHERE onboarding_id=%s",
                (uuid.uuid4(), _hex64(), None, None, o))
            if not med_levende_token:
                m.execute("UPDATE modultoken SET tilbakekalt_ts=now(),"
                          " tilbakekalt_grunn='drept' WHERE onboarding_id=%s"
                          " AND tilbakekalt_ts IS NULL", (o,))
            m.commit()
            return o

        naer = familie(5)             # innenfor 30 OG 7, ikke 1
        dod = familie(5, med_levende_token=False)
        fjern = familie(200)          # utenfor alle tersklene

        vs.execute("SELECT varsle_tokenfamilie_utlop(%s)", (ten,))
        vs.commit()
        sett_kontekst(m, ten, "sys", "r1")
        # Sveipen er PLATTFORMVID (den skal se alle familier, også dem andre
        # tester har etterlatt) — asserten scopes derfor til DENNE testens
        # familier.
        mine = {str(naer), str(dod), str(fjern)}
        rader = [r for r in m.execute(
            "SELECT bruker_id, ressurs_id, hendelse FROM varsel"
            " WHERE tenant=%s AND art='tokenfamilie_utloper'"
            " ORDER BY hendelse", (ten,)).fetchall() if r[1] in mine]
        m.rollback()
        assert {(r[1], r[2]) for r in rader} == {(str(naer), "30"),
                                                 (str(naer), "7")}, rader
        assert all(r[0] == admin for r in rader), \
            "varselet traff andre enn plattformadminene"
        assert not any(r[1] == str(dod) for r in rader), \
            "en familie uten levende token ble varslet"
        assert not any(r[1] == str(fjern) for r in rader)

        # Idempotent: andre sveip legger ingenting til.
        vs.execute("SELECT varsle_tokenfamilie_utlop(%s)", (ten,))
        vs.commit()
        sett_kontekst(m, ten, "sys", "r2")
        n = m.execute(
            "SELECT count(*) FROM varsel WHERE tenant=%s"
            " AND art='tokenfamilie_utloper' AND ressurs_id = ANY(%s)",
            (ten, list(mine))).fetchone()[0]
        m.rollback()
        assert n == 2, n
    finally:
        vs.close()
        m.close()


@pg
def test_familievarselet_respekterer_kun_portal():
    """Codex P1: `varsel.epost_status` har DEFAULT 'koet', så en insert som
    utelot kolonnen sendte e-post også til dem som har valgt `kun_portal`.
    Sveipen leser nå kanalvalget under SAMME advisory-lås som
    `varsel.opprett`. Kontroll: dropp epost_status-kolonnen fra INSERT-en i
    `varsle_tokenfamilie_utlop`, så blir denne rød."""
    from api.varsel import KANALVALGNOKKEL
    # Nøkkelen står som en literal i migrasjonen (SQL kan ikke importere
    # Python) — ulike nøkler ville betydd at de to veiene ikke serialiserer
    # mot hverandre i det hele tatt, altså nøyaktig kappløpet låsen finnes
    # for. Denne asserten er det eneste som binder dem sammen.
    assert KANALVALGNOKKEL == 615774026

    m = _c()
    vs = _vs()
    ten = "t-famkanal-" + secrets.token_hex(3)
    try:
        avmeldt = m.execute(
            "INSERT INTO brukeridentitet (issuer, sub) VALUES (%s,%s)"
            " RETURNING bruker_id",
            ("https://idp.example", f"{ten}-portal")).fetchone()[0]
        paameldt = m.execute(
            "INSERT INTO brukeridentitet (issuer, sub) VALUES (%s,%s)"
            " RETURNING bruker_id",
            ("https://idp.example", f"{ten}-epost")).fetchone()[0]
        from db.pg import sett_kontekst
        sett_kontekst(m, ten, "sys", "r0")
        for b in (avmeldt, paameldt):
            m.execute("INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
                      " VALUES (%s,%s,ARRAY['admin'])", (ten, b))
        m.execute("INSERT INTO varselvalg (tenant,bruker_id,kanal)"
                  " VALUES (%s,%s,'kun_portal')", (ten, avmeldt))
        # ... og den andre har INGEN rad: et fravær er ikke «av».
        m.commit()

        modul, rel, _, _ = _deployment_med_typer(m)
        o = uuid.uuid4()
        m.execute(
            "INSERT INTO modul_onboarding (onboarding_id,modul_id,miljo,"
            "release_id,hemmelighet_hash,familie_utloper,utstedt_av,"
            "utloper,innlost_ts) VALUES (%s,%s,'staging',%s,%s,"
            "now()+make_interval(days => 5),'t',now(),now())",
            (o, modul, rel, _hex64()))
        m.execute(
            "INSERT INTO modultoken (token_id,token_mac,onboarding_id,"
            "familie_utloper,modul_id,miljo,release_id,utstedt_epoch,utloper)"
            " SELECT %s,%s,onboarding_id,familie_utloper,modul_id,miljo,"
            "release_id,0,familie_utloper FROM modul_onboarding"
            " WHERE onboarding_id=%s", (uuid.uuid4(), _hex64(), o))
        m.commit()

        vs.execute("SELECT varsle_tokenfamilie_utlop(%s)", (ten,))
        vs.commit()
        sett_kontekst(m, ten, "sys", "r1")
        rader = dict(m.execute(
            "SELECT bruker_id, epost_status FROM varsel WHERE tenant=%s"
            " AND art='tokenfamilie_utloper' AND ressurs_id=%s"
            " AND hendelse='7'", (ten, str(o))).fetchall())
        m.rollback()
        assert rader == {avmeldt: "ikke_aktuelt", paameldt: "koet"}, rader
    finally:
        vs.close()
        m.close()


@pg
def test_familiesveipen_er_stengt_for_web_runtime():
    """Codex P1: sveipen er KRYSS-TENANT — den tar tenanten som parameter,
    setter dens RLS-kontekst, leser dens administratorer og skriver varsler
    til dem. Da hører den til `disponit_varselsender` alene, akkurat som
    `varsel_klaim_epost`/`varsel_rekoe`: et grant til web-runtime ville gitt
    en kompromittert forespørselsvei nøyaktig det vinduet senderrollen finnes
    for å nekte den.

    Kontroll: gi `GRANT EXECUTE ... TO disponit` tilbake i 035 (eller fjern
    REVOKE-en i `migrer.py`), så blir denne rød."""
    rt = _rt()
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT varsle_tokenfamilie_utlop('disponit')")
        rt.rollback()
    finally:
        rt.close()
