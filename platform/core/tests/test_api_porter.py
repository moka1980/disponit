"""PR-005b: portene som krever en EKTE server, ekte tråder eller ekte roller.

Delt fra `test_api.py` fordi kravene her ikke kan måles med en testklient:
byte-grensen må se chunked transfer over en socket, idempotensen må ha
faktisk samtidighet, og rolleskillet må spørre databasen som den rollen det
gjelder. En testklient som later som er ikke et bevis.

De tre Codex-portene fra korreksjonsdokumentet ligger nederst.
"""
import json
import os
import socket
import threading
import time
from datetime import timedelta

import psycopg
import pytest
import yaml

from . import test_api as felles
from .test_api import (ANNEN_TENANT, DSN, KEK, MIGRATOR_DSN, NOKLER, PEPPER,
                       TENANT, _lag_token, _naa, _rydd, attestasjon, hendelse,
                       hendelse_uten_attestasjoner, pg)

TOKEN_ADMIN_DSN = (os.environ.get("DISPONIT_TEST_TOKEN_ADMIN_DSN")
                   or (MIGRATOR_DSN or "").replace(
                       "disponit_migrator:mig", "disponit_token_admin:tok"))

# Fixturene gjenbrukes direkte — samme oppsett, samme opprydding.
miljo = felles.miljo
migrator = felles.migrator
malpolicy = felles.malpolicy
policy = felles.policy
token = felles.token
app = felles.app
klient = felles.klient


# ---------------------------------------------------------------------------
# Ekte server
# ---------------------------------------------------------------------------

@pytest.fixture()
def server(app):
    """Uvicorn på loopback, ephemeral port.

    Nødvendig for tre ting testklienten ikke kan gi: chunked transfer,
    ekte samtidige forbindelser, og en klient-IP som faktisk er 127.0.0.1
    (testklienten oppgir «testclient», og /ready avviser den — korrekt).
    """
    import uvicorn
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="error", access_log=False)
    srv = uvicorn.Server(config)
    traad = threading.Thread(target=srv.run, daemon=True)
    traad.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    assert srv.started, "uvicorn startet ikke"
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    traad.join(10)


def _post(base, policy, event, token, nokkel):
    import httpx
    return httpx.post(f"{base}/v1/beslutning",
                      json={"policy_id": policy["meta"]["policy_id"],
                            "event": event},
                      headers={"authorization": f"Bearer {token}",
                               "idempotency-key": nokkel}, timeout=30.0)


# ---------------------------------------------------------------------------
# v3-delta pkt. 6: idempotens under samtidighet — den BINDENDE testen
# ---------------------------------------------------------------------------

@pg
def test_tjue_samtidige_gir_en_evaluering_en_loggpost_ett_svar(
        server, policy, token, migrator, monkeypatch):
    """20 samtidige requests, samme tenant/nøkkel/input.

    Kravet er fire tall på én gang: nøyaktig én evaluering, nøyaktig én
    revisjonsrad, maks én unntaksrad, og 20 byte-identiske svar. Ett av dem
    alene beviser ingenting — et API som svarte det samme uten å evaluere
    ville bestått «ett svar», og et API som evaluerte 20 ganger og skrev
    én rad ville bestått «én rad».
    """
    from api import kjerne as kjernemodul
    tok, _ = token()
    e = hendelse(policy)
    teller = {"n": 0}
    laas = threading.Lock()
    ekte = kjernemodul.sikker_beslutning_pg

    def tellende(*a, **kw):
        if kw.get("portbrudd") is None:
            with laas:
                teller["n"] += 1
        return ekte(*a, **kw)

    monkeypatch.setattr(kjernemodul, "sikker_beslutning_pg", tellende)

    svar: list = []
    start = threading.Barrier(20)

    def kjor():
        start.wait(30)
        svar.append(_post(server, policy, e, tok, "samtidig"))

    traader = [threading.Thread(target=kjor) for _ in range(20)]
    for t in traader:
        t.start()
    for t in traader:
        t.join(60)

    assert len(svar) == 20
    assert {r.status_code for r in svar} == {200}, \
        [(r.status_code, r.text[:120]) for r in svar if r.status_code != 200]
    kropper = {r.content for r in svar}
    assert len(kropper) == 1, f"{len(kropper)} ulike svar — ikke byte-identisk"
    assert teller["n"] == 1, f"motoren ble kalt {teller['n']} ganger, ikke 1"

    migrator.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    logg = migrator.execute("SELECT count(*) FROM revisjonslogg WHERE tenant=%s",
                            (TENANT,)).fetchone()[0]
    saker = migrator.execute("SELECT count(*) FROM unntak WHERE tenant=%s",
                             (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert logg == 1, f"{logg} revisjonsrader for én beslutning"
    assert saker <= 1


@pg
def test_jti_kapplop_gir_noyaktig_en_vinner(server, migrator, malpolicy, token):
    """Replay-vernet under ekte kappløp: 20 forsøk på samme jti, én vinner.

    Hver forespørsel har sin EGEN idempotensnøkkel — ellers ville
    idempotensen alene gitt ett svar, og testen bevist feil mekanisme.
    """
    from api import policyregister
    p = yaml.safe_load(yaml.safe_dump(malpolicy))
    p["meta"]["policy_id"] = "irr-kapplop"
    for h in p["handlinger"]:
        if h["id"] == "purring.send":
            h["reversering"] = {"type": "irreversibel"}
    policyregister.registrer(migrator, TENANT, p, p["meta"]["status"])
    migrator.commit()

    tok, _ = token()
    e = hendelse(p, ressurs="fak-jti")
    resultater: list = []
    start = threading.Barrier(20)

    def kjor(i):
        start.wait(30)
        r = _post(server, p, e, tok, f"jti-{i}")
        resultater.append(r.json().get("beslutning"))

    traader = [threading.Thread(target=kjor, args=(i,)) for i in range(20)]
    for t in traader:
        t.start()
    for t in traader:
        t.join(60)

    assert len(resultater) == 20
    assert resultater.count("TILLAT") == 1, \
        f"{resultater.count('TILLAT')} vinnere — replay-vernet holder ikke"
    assert resultater.count("STOPP") == 19

    migrator.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    antall = migrator.execute(
        "SELECT count(*) FROM attestasjon_jti WHERE tenant=%s",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert antall == 2, f"{antall} konsumerte jti-er (forventet 2, én per vilkår)"


# ---------------------------------------------------------------------------
# Kroppsgrensen — chunked og lyvende Content-Length
# ---------------------------------------------------------------------------

def _raa_http(base: str, raa: bytes) -> bytes:
    vert, port = base.replace("http://", "").split(":")
    s = socket.create_connection((vert, int(port)), timeout=15)
    try:
        ut = b""
        s.settimeout(15)
        try:
            s.sendall(raa)
            while len(ut) < 8192:
                bit = s.recv(4096)
                if not bit:
                    break
                ut += bit
        except (socket.timeout, ConnectionResetError, BrokenPipeError):
            # Serveren kan kutte forbindelsen i stedet for å svare når
            # protokollen brytes (uvicorn/h11 gjør det ved for mye kropp).
            # Det er også et avslag — testen måler at forespørselen ALDRI
            # blir et 200-svar, ikke hvilken form avslaget tar.
            pass
        return ut
    finally:
        s.close()


@pg
def test_chunked_kropp_over_grensen_avvises(server, policy, token):
    """Chunked transfer oppgir INGEN Content-Length.

    En grense som bare leser headeren slipper dette rett gjennom. Testen
    sender 300 KiB i biter og krever 413 — det er hele grunnen til at
    middlewaren teller faktisk mottatte bytes.
    """
    tok, _ = token()
    vert, port = server.replace("http://", "").split(":")
    hode = (b"POST /v1/beslutning HTTP/1.1\r\n"
            b"Host: " + vert.encode() + b"\r\n"
            b"Authorization: Bearer " + tok.encode() + b"\r\n"
            b"Idempotency-Key: chunk\r\n"
            b"Content-Type: application/json\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n")
    bit = b"a" * 16384
    kropp = b"".join(b"%x\r\n%s\r\n" % (len(bit), bit) for _ in range(20))
    svar = _raa_http(server, hode + kropp + b"0\r\n\r\n")
    assert b"413" in svar.split(b"\r\n")[0], svar[:200]


@pg
def test_lyvende_content_length_avvises(server, policy, token, migrator):
    """Content-Length er en PÅSTAND. Her sier den 10 og det kommer 300 KiB.

    To ting kan skje, og begge er akseptable: uvicorn kutter kroppen ved
    den oppgitte lengden (og JSON-parsingen feiler), eller den bryter
    forbindelsen fordi protokollen ikke stemmer. Det som IKKE kan skje, er
    at de overskytende bytene blir tolket som en forespørsel. Derfor måles
    to ting: aldri 200, og ingen beslutning i revisjonsloggen.
    """
    tok, _ = token()
    vert, _p = server.replace("http://", "").split(":")
    hode = (b"POST /v1/beslutning HTTP/1.1\r\n"
            b"Host: " + vert.encode() + b"\r\n"
            b"Authorization: Bearer " + tok.encode() + b"\r\n"
            b"Idempotency-Key: lyv\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 10\r\n\r\n")
    svar = _raa_http(server, hode + b"x" * (300 * 1024))
    forste = svar.split(b"\r\n")[0] if svar else b""
    assert b" 200 " not in forste, svar[:200]

    migrator.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    antall = migrator.execute("SELECT count(*) FROM revisjonslogg"
                              " WHERE tenant=%s", (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert antall == 0, "en overdimensjonert kropp ble behandlet som beslutning"


@pg
def test_ready_krever_loopback_og_er_uten_tenantkontekst(server, klient):
    import httpx
    r = httpx.get(f"{server}/ready", timeout=10)
    assert r.status_code == 200 and r.json()["status"] == "ok"
    # Ingen versjonsdetaljer ut (v2 Del 3.1: «ok/ikke-ok, ikke versjoner»).
    assert set(r.json()) == {"status"}
    # Testklienten har klient-vert «testclient» — ikke loopback => 404.
    assert klient.get("/ready").status_code == 404


@pg
def test_live_svarer_uten_database(server, app, monkeypatch):
    import httpx
    monkeypatch.setattr(app.tjeneste.pool, "hent",
                        lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    assert httpx.get(f"{server}/live", timeout=10).status_code == 200
    assert httpx.get(f"{server}/ready", timeout=10).status_code == 503


# ---------------------------------------------------------------------------
# Boot-nekt (v2 Del 8)
# ---------------------------------------------------------------------------

@pg
@pytest.mark.parametrize("mangel", ["nokler", "kek", "pepper", "bind"])
def test_boot_nekter(miljo, monkeypatch, mangel):
    """Prosessen skal ikke starte. En advarsel med exit 0 er ingen port —
    samme lærdom som migrasjonskjøreren i 005a."""
    from api.app import BootNekt, lag_app
    if mangel == "nokler":
        monkeypatch.delenv("DISPONIT_ATT_NOKLER", raising=False)
        monkeypatch.setenv("DISPONIT_ATT_NOKLER_FIL", "/finnes/ikke")
    elif mangel == "kek":
        monkeypatch.delenv("DISPONIT_KEK", raising=False)
    elif mangel == "pepper":
        monkeypatch.setenv("DISPONIT_TOKEN_PEPPER", "kort")
    with pytest.raises((BootNekt, RuntimeError, FileNotFoundError)):
        if mangel == "bind":
            lag_app(DSN, bind_vert="0.0.0.0")
        else:
            lag_app(DSN)


@pg
def test_ikke_loopback_tillates_kun_med_tls_flagget(miljo, monkeypatch):
    from api.app import lag_app
    monkeypatch.setenv("DISPONIT_TLS_AKTIV", "1")
    a = lag_app(DSN, bind_vert="10.0.0.5")     # nå lovlig — flagget er satt
    a.tjeneste.pool.lukk()


@pg
def test_boot_nekter_ved_feil_migrasjonstilstand(miljo, monkeypatch):
    from api import app as appmodul
    monkeypatch.setattr(appmodul, "forventede_migrasjoner",
                        lambda: [1, 2, 3, 4, 99])
    with pytest.raises(appmodul.BootNekt) as e:
        appmodul.lag_app(DSN)
    assert "migrasjonstilstanden" in str(e.value)


# ---------------------------------------------------------------------------
# Codex-port (b): rolleskillet, begge veier
# ---------------------------------------------------------------------------

@pg
def test_runtime_kan_ikke_lese_api_tokener_men_kan_kalle_funksjonen(
        miljo, migrator, token):
    """Korreksjon 2: runtime har KUN EXECUTE på verifiser_token.

    Begge halvdeler måles. Bare den negative ville bestått på en database
    der funksjonen heller ikke virket — altså på en ødelagt installasjon.
    """
    import hashlib
    import hmac
    from db.pg import koble
    tok, token_id = token()
    secret = tok.split(".", 1)[1]
    runtime = koble(DSN)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute("SELECT secret_mac FROM api_tokener").fetchall()
        runtime.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute("SELECT token_id FROM api_tokener").fetchall()
        runtime.rollback()
        mac = hmac.new(PEPPER.encode(), secret.encode(),
                       hashlib.sha256).hexdigest()
        rad = runtime.execute("SELECT tenant, rolle, scopes FROM"
                              " verifiser_token(%s,%s)",
                              (token_id, mac)).fetchone()
        assert rad is not None and rad[0] == TENANT
        runtime.rollback()
    finally:
        runtime.close()


@pg
def test_token_admin_kan_administrere_men_ikke_verifisere(miljo, migrator,
                                                          token):
    """Motsatt vei: token-admin oppretter og deaktiverer, men kan verken
    kalle verifiser_token eller lese secret_mac.

    Uten kolonnenivå-GRANT ville en kompromittert token-admin kunne lese ut
    alle MAC-ene, og da er skillet mellom «administrere» og «bruke» borte.
    """
    from db.pg import koble
    tok, token_id = token()
    admin = koble(TOKEN_ADMIN_DSN)
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            admin.execute("SELECT secret_mac FROM api_tokener").fetchall()
        admin.rollback()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            admin.execute("SELECT * FROM verifiser_token('x','y')").fetchall()
        admin.rollback()
        # Det den SKAL kunne: lese metadata og deaktivere.
        rad = admin.execute("SELECT tenant, aktiv FROM api_tokener"
                            " WHERE token_id=%s", (token_id,)).fetchone()
        assert rad == (TENANT, True)
        admin.execute("UPDATE api_tokener SET aktiv=false WHERE token_id=%s",
                      (token_id,))
        admin.commit()
    finally:
        admin.close()


@pg
def test_token_admin_eier_ingenting(miljo, migrator):
    """«Eier ingenting» er en målbar egenskap, ikke en intensjon."""
    eide = migrator.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n"
        " ON n.oid=c.relnamespace WHERE n.nspname='public'"
        "   AND c.relkind IN ('r','p')"
        "   AND pg_get_userbyid(c.relowner)='disponit_token_admin'").fetchall()
    migrator.rollback()
    assert eide == [], f"token-admin eier tabeller: {eide}"


@pg
def test_ukjent_mac_gir_ingen_rad(miljo, migrator, token):
    """Migrasjon 004: format-guard + konstant-tids sammenligning.

    Timingen selv er ikke testbar her (samme ærlige begrensning som
    `compare_digest` i PR-002 — den mutanten var ekvivalent). Det som ER
    testbart, er at ingen kandidat utenom den rette gir en rad, og at
    ugyldig format avvises før sammenligningen.
    """
    from db.pg import koble
    _tok, token_id = token()
    runtime = koble(DSN)
    try:
        for kandidat in ("0" * 64, "f" * 64, "kort", "G" * 64, "", None):
            rad = runtime.execute("SELECT * FROM verifiser_token(%s,%s)",
                                  (token_id, kandidat)).fetchone()
            assert rad is None, kandidat
            runtime.rollback()
    finally:
        runtime.close()


@pg
def test_last_used_at_oppdateres_maks_en_gang_per_minutt(miljo, migrator,
                                                         token):
    import hashlib
    import hmac
    from db.pg import koble
    tok, token_id = token()
    mac = hmac.new(PEPPER.encode(), tok.split(".", 1)[1].encode(),
                   hashlib.sha256).hexdigest()
    runtime = koble(DSN)
    try:
        for _ in range(3):
            runtime.execute("SELECT * FROM verifiser_token(%s,%s)",
                            (token_id, mac)).fetchone()
            runtime.commit()
    finally:
        runtime.close()
    forste = migrator.execute("SELECT last_used_at FROM api_tokener"
                              " WHERE token_id=%s", (token_id,)).fetchone()[0]
    migrator.rollback()
    assert forste is not None, "last_used_at ble aldri satt"
    # Tre kall innenfor samme minutt skal ha gitt nøyaktig én skriving.
    # Vi kan ikke se antall UPDATE-er direkte, men vi kan se at tiden ikke
    # flyttet seg ved kall to og tre — som er den observerbare effekten.
    runtime = koble(DSN)
    try:
        runtime.execute("SELECT * FROM verifiser_token(%s,%s)",
                        (token_id, mac)).fetchone()
        runtime.commit()
    finally:
        runtime.close()
    etterpaa = migrator.execute("SELECT last_used_at FROM api_tokener"
                                " WHERE token_id=%s", (token_id,)).fetchone()[0]
    migrator.rollback()
    assert etterpaa == forste, "last_used_at ble skrevet oftere enn 1/minutt"


# ---------------------------------------------------------------------------
# Codex-port (a): transaksjonseierskapet
# ---------------------------------------------------------------------------

@pg
def test_vakten_stopper_commit_under_behandle(miljo, policy, token, migrator):
    """Korreksjon 1 som KJØRETIDSEGENSKAP.

    Mutasjonen som ellers ville sluppet gjennom: en `conn.commit()` lagt
    inn i `sikker_beslutning_pg`. Med den ville loggposten blitt committet
    mens resten av flyten fortsatt kunne feile — altså nøyaktig den halve
    beslutningen eierskapet finnes for å hindre. Her feiler den høylytt.
    """
    from api import kjerne as kjernemodul
    from db.pg import koble
    from policy_validator.engine import EvaluationContext

    ekte = kjernemodul.sikker_beslutning_pg

    def committer(policyarg, ctx, event, conn, **kw):
        d = ekte(policyarg, ctx, event, conn, **kw)
        conn.commit()          # forbudt: eieren er behandle()
        return d

    conn = koble(DSN)
    try:
        kjernemodul.sikker_beslutning_pg = committer
        with pytest.raises(kjernemodul.Transaksjonsbrudd):
            kjernemodul.behandle(
                conn, EvaluationContext(TENANT, "agent", True, "api_token"),
                policy_id=policy["meta"]["policy_id"],
                event=hendelse(policy), idempotency_key="vakt",
                request_id="r1", aktor="token:test", nokler=NOKLER)
    finally:
        kjernemodul.sikker_beslutning_pg = ekte
        conn.close()


@pg
def test_vakten_stopper_rollback_under_behandle(miljo, policy, token):
    from api import kjerne as kjernemodul
    from db.pg import koble
    from policy_validator.engine import EvaluationContext
    ekte = kjernemodul.sikker_beslutning_pg

    def ruller(policyarg, ctx, event, conn, **kw):
        conn.rollback()
        return ekte(policyarg, ctx, event, conn, **kw)

    conn = koble(DSN)
    try:
        kjernemodul.sikker_beslutning_pg = ruller
        with pytest.raises(kjernemodul.Transaksjonsbrudd):
            kjernemodul.behandle(
                conn, EvaluationContext(TENANT, "agent", True, "api_token"),
                policy_id=policy["meta"]["policy_id"],
                event=hendelse(policy), idempotency_key="vakt2",
                request_id="r2", aktor="token:test", nokler=NOKLER)
    finally:
        kjernemodul.sikker_beslutning_pg = ekte
        conn.close()


def test_kjerne_har_ingen_commit_utenfor_behandle():
    """Statisk halvdel av port (a).

    Vakten over dekker alt som KALLES under behandle. Denne dekker
    kjernemodulen selv: `conn.commit()` og `conn.rollback()` skal bare
    finnes i `behandle`, som er transaksjonens eier.
    """
    import ast
    import inspect
    from api import kjerne as kjernemodul

    tre = ast.parse(inspect.getsource(kjernemodul))
    funn = []
    for node in ast.walk(tre):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for indre in ast.walk(node):
            if (isinstance(indre, ast.Call)
                    and isinstance(indre.func, ast.Attribute)
                    and indre.func.attr in ("commit", "rollback")
                    and node.name not in ("behandle", "commit", "rollback")):
                funn.append(f"{node.name}:{indre.func.attr}")
    assert funn == [], f"transaksjonsstyring utenfor behandle(): {funn}"


@pg
def test_pgtellerlager_ruller_ikke_tilbake_med_ytre_transaksjon(miljo,
                                                                migrator):
    """Den konkrete fellen bak korreksjon 1.

    `PgTellerLager.antall()` gjorde `conn.rollback()` etter det rådgivende
    oppslaget. Frittstående er det riktig. Under en ytre transaksjon ville
    nøyaktig samme linje kastet eierens SET LOCAL, sluppet advisory-låsen
    på idempotensnøkkelen og annullert claimet — midt i flyten, uten at noe
    feilet. Testen måler at sesjonsvariabelen overlever oppslaget.
    """
    from db.pg import PgTellerLager, sett_kontekst
    conn = None
    from db.pg import koble
    conn = koble(DSN)
    try:
        sett_kontekst(conn, TENANT, "token:test", "rid-1")
        PgTellerLager(conn, ytre_transaksjon=True).antall(
            (TENANT, "purring.send", "faktura_id", "g"), _naa())
        igjen = conn.execute(
            "SELECT current_setting('disponit.tenant', true),"
            "       current_setting('disponit.aktor', true)").fetchone()
        assert igjen == (TENANT, "token:test"), \
            "konteksten forsvant — noen rullet tilbake eierens transaksjon"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Codex-port (c): uautentisert request rører ingen tenanttabeller
# ---------------------------------------------------------------------------

TENANTTABELLER = ("revisjonslogg", "unntak", "unntak_historikk", "idempotens",
                  "policyer", "tenant_nokler", "attestasjon_jti",
                  "frekvens_hendelser")


class SporendeTilkobling:
    """Registrerer hvert eneste statement som sendes."""

    def __init__(self, conn, logg):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_logg", logg)

    def execute(self, sql, *a, **kw):
        self._logg.append(str(sql))
        return self._conn.execute(sql, *a, **kw)

    def transaction(self, *a, **kw):
        return self._conn.transaction(*a, **kw)

    def __getattr__(self, navn):
        return getattr(self._conn, navn)


@pg
def test_uautentisert_request_rorer_ingen_tenanttabeller(app, klient,
                                                          monkeypatch):
    """Korreksjon 3 / Codex-port (c).

    Pre-auth-transaksjonen kjenner ingen tenant. Da skal den heller ikke
    kunne røre noe som er tenantbundet — verken lese, skrive eller sette
    `disponit.tenant`. Uten dette ville en uautentisert forespørsel kunne
    havne på reservetenanten `<ukjent>` og fylle tabeller med søppel fra
    hvem som helst.
    """
    logg: list[str] = []
    ekte_hent = app.tjeneste.pool.hent

    def sporende_hent(*a, **kw):
        return SporendeTilkobling(ekte_hent(*a, **kw), logg)

    monkeypatch.setattr(app.tjeneste.pool, "hent", sporende_hent)

    r = klient.post("/v1/beslutning",
                    json={"policy_id": "p", "event": {}},
                    headers={"authorization": "Bearer tk_finnes.ikke",
                             "idempotency-key": "i"})
    assert r.status_code == 401
    assert logg, "ingen statements ble sporet — testen måler ingenting"
    samlet = " ".join(logg).lower()
    for tabell in TENANTTABELLER:
        assert tabell not in samlet, \
            f"uautentisert forespørsel rørte {tabell}: {logg}"
    assert "disponit.tenant" not in samlet, \
        f"tenantkontekst ble satt uten gyldig token: {logg}"
    assert any("verifiser_token" in s for s in logg), \
        "pre-auth kalte ikke verifiser_token — sporingen fanget feil vei"


@pg
def test_autentisert_request_setter_kontekst_forst(app, klient, policy,
                                                   token, monkeypatch):
    """Speilbildet: med gyldig token SKAL konteksten settes, og den skal
    settes FØR noe tenantbundet røres. Uten denne ville testen over
    bestått på et API som ikke gjorde noe som helst."""
    logg: list[str] = []
    ekte_hent = app.tjeneste.pool.hent
    monkeypatch.setattr(app.tjeneste.pool, "hent",
                        lambda *a, **kw: SporendeTilkobling(
                            ekte_hent(*a, **kw), logg))
    tok, _ = token()
    assert felles.post(klient, policy, hendelse(policy), tok).status_code == 200

    forste_kontekst = next(i for i, s in enumerate(logg)
                           if "disponit.tenant" in s)
    forste_tabell = next(
        (i for i, s in enumerate(logg)
         if any(t in s.lower() for t in TENANTTABELLER)), len(logg))
    assert forste_kontekst < forste_tabell, \
        f"tenanttabell ble rørt før SET LOCAL: {logg[:5]}"
    assert "verifiser_token" in logg[0], \
        f"første statement var ikke pre-auth: {logg[0]}"


# ---------------------------------------------------------------------------
# Scope, cursor og paginering
# ---------------------------------------------------------------------------

@pg
def test_sikkerhetssaker_krever_eget_scope(klient, policy, token, migrator):
    """v3-delta pkt. 5: `exceptions:read` ser KUN normal kø."""
    tok, _ = token()
    e = hendelse(policy)
    e["attestasjoner"]["ingen_aktiv_tvist"]["resultat"] = False   # signaturbrudd
    kropp = felles.post(klient, policy, e, tok).json()
    assert kropp["beslutning"] == "STOPP"

    h = {"authorization": f"Bearer {tok}"}
    normal = klient.get("/v1/unntak", headers=h).json()
    assert normal["saker"] == [], "sikkerhetssak lekket inn i ordinær kø"

    r = klient.get("/v1/unntak?sakstype=sikkerhet", headers=h)
    assert r.status_code == 403 and r.json()["feil"] == "scope_mangler"

    tok2, _ = token(scopes=["exceptions:read", "security:read"])
    r2 = klient.get("/v1/unntak?sakstype=sikkerhet",
                    headers={"authorization": f"Bearer {tok2}"})
    assert r2.status_code == 200 and len(r2.json()["saker"]) == 1
    sak = r2.json()["saker"][0]
    assert sak["sakstype"] == "sikkerhet"
    # Metadata KUN — payloadfeltene finnes ikke i svaret i det hele tatt.
    assert set(sak) == {"id", "ts", "handling", "kategori", "prioritet",
                        "status", "sakstype"}


@pg
def test_keyset_paginering_med_signert_cursor(klient, policy, token):
    tok, _ = token()
    for i in range(5):
        felles.post(klient, policy,
                    hendelse_uten_attestasjoner(ressurs=f"fak-p{i}",
                                                handling="finnes.ikke"),
                    tok, nokkel=f"side-{i}")
    h = {"authorization": f"Bearer {tok}"}
    side1 = klient.get("/v1/unntak?limit=2", headers=h).json()
    assert len(side1["saker"]) == 2 and side1["neste_cursor"]
    side2 = klient.get(f"/v1/unntak?limit=2&cursor={side1['neste_cursor']}",
                       headers=h).json()
    assert len(side2["saker"]) == 2
    ider = [s["id"] for s in side1["saker"] + side2["saker"]]
    assert len(set(ider)) == 4, "keyset ga overlappende sider"
    assert ider == sorted(ider, reverse=True)


@pg
def test_kryss_tenant_lesing_er_umulig(klient, policy, token, migrator,
                                       malpolicy):
    """RLS + tenantbundet spørring. To lag, og testen krever begge."""
    from api import policyregister
    tok_a, _ = token()
    felles.post(klient, policy,
                hendelse_uten_attestasjoner(handling="finnes.ikke"), tok_a,
                nokkel="a-sak")

    annen = yaml.safe_load(yaml.safe_dump(malpolicy))
    policyregister.registrer(migrator, ANNEN_TENANT, annen,
                             annen["meta"]["status"])
    migrator.commit()
    tok_b, _ = _lag_token(migrator, ANNEN_TENANT, "agent",
                          ["decision:write", "exceptions:read"])
    saker = klient.get("/v1/unntak",
                       headers={"authorization": f"Bearer {tok_b}"}).json()
    assert saker["saker"] == [], "en annen tenants saker var synlige"


# ---------------------------------------------------------------------------
# Token-CLI (korreksjon 2)
# ---------------------------------------------------------------------------

def _cli():
    import importlib.util
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    spek = importlib.util.spec_from_file_location(
        "token_cli", rot / "deploy/staging/token-cli.py")
    modul = importlib.util.module_from_spec(spek)
    spek.loader.exec_module(modul)
    return modul


@pg
def test_cli_oppretter_token_som_faktisk_virker(miljo, migrator, klient,
                                                policy):
    """Rundtur: CLI-et lager tokenet, API-et godtar det, og hemmeligheten
    finnes ikke i databasen."""
    from db.pg import koble
    cli = _cli()
    admin = koble(TOKEN_ADMIN_DSN)
    try:
        token_id, secret = cli.opprett(admin, PEPPER, TENANT, "agent",
                                       ["decision:write", "exceptions:read"])
        admin.commit()
    finally:
        admin.close()

    r = felles.post(klient, policy, hendelse(policy), f"{token_id}.{secret}",
                    nokkel="cli-1")
    assert r.status_code == 200 and r.json()["beslutning"] == "TILLAT"

    rad = migrator.execute("SELECT secret_mac FROM api_tokener"
                           " WHERE token_id=%s", (token_id,)).fetchone()
    # `revisjonslogg` har RLS med FORCE — også for skjemaeieren. Uten
    # `disponit.tenant` ser selv migrator null rader, og testen ville
    # rapportert «ikke revisjonslogget» om en post som lå der hele tiden.
    migrator.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    logg = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s AND handling=%s",
        (TENANT, "token.opprett")).fetchone()[0]
    migrator.rollback()
    assert secret not in rad[0], "hemmeligheten ligger i databasen"
    assert len(rad[0]) == 64
    assert logg == 1, "tokenopprettelsen ble ikke revisjonslogget"


@pg
def test_cli_rotasjon_lager_ny_for_gammel_deaktiveres(miljo, migrator, klient,
                                                       policy, monkeypatch):
    """Rekkefølgen ER kontrakten: feiler deaktiveringen, virker den gamle
    fortsatt. Testen sprekker deaktiveringen med vilje og krever at BEGGE
    tokens da er brukbare — aldri null."""
    from db.pg import koble
    cli = _cli()
    admin = koble(TOKEN_ADMIN_DSN)
    try:
        gammel_id, gammel_secret = cli.opprett(
            admin, PEPPER, TENANT, "agent", ["decision:write"])
        admin.commit()

        def sprekk(conn, token_id):
            raise psycopg.errors.InsufficientPrivilege("konstruert feil")

        monkeypatch.setattr(cli, "deaktiver", sprekk)
        with pytest.raises(psycopg.Error):
            cli.roter(admin, PEPPER, gammel_id)
        admin.rollback()
    finally:
        admin.close()

    # Den gamle virker fortsatt — kunden er ikke låst ute.
    r = felles.post(klient, policy, hendelse(policy),
                    f"{gammel_id}.{gammel_secret}", nokkel="rot-gammel")
    assert r.status_code == 200, r.text
    # Og den nye ble committet i transaksjon 1, altså FØR forsøket over.
    nye = migrator.execute(
        "SELECT count(*) FROM api_tokener WHERE tenant=%s AND aktiv",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert nye == 2, f"{nye} aktive tokens — ny skal være committet først"


@pg
def test_cli_rotasjon_fullfort_deaktiverer_gammel(miljo, migrator, klient,
                                                  policy):
    from db.pg import koble
    cli = _cli()
    admin = koble(TOKEN_ADMIN_DSN)
    try:
        gammel_id, gammel_secret = cli.opprett(
            admin, PEPPER, TENANT, "agent", ["decision:write"])
        admin.commit()
        ny_id, ny_secret, _ = cli.roter(admin, PEPPER, gammel_id)
    finally:
        admin.close()
    assert felles.post(klient, policy, hendelse(policy),
                       f"{ny_id}.{ny_secret}", nokkel="rot-ny").status_code == 200
    assert felles.post(klient, policy, hendelse(policy),
                       f"{gammel_id}.{gammel_secret}",
                       nokkel="rot-d").status_code == 401


def test_cli_viser_ikke_hemmelighet_uten_tty():
    """Uten TTY havner hemmeligheten i den filen noen omdirigerte til."""
    import io
    cli = _cli()
    ut = io.StringIO()                      # ikke en terminal
    cli.vis_hemmelighet("tk_abc", "SUPERHEMMELIG", bootstrap=False, ut=ut)
    tekst = ut.getvalue()
    assert "SUPERHEMMELIG" not in tekst
    assert "tk_abc" in tekst and "ikke en terminal" in tekst

    ut2 = io.StringIO()
    cli.vis_hemmelighet("tk_abc", "SUPERHEMMELIG", bootstrap=True, ut=ut2)
    assert "SUPERHEMMELIG" in ut2.getvalue()


def test_cli_tar_aldri_hemmelighet_som_argument():
    """Et `--secret`-flagg ville lagt hemmeligheten i shell-historikken,
    i `ps`-utskriften og i enhver `set -x`-logg."""
    from pathlib import Path
    kilde = (Path(__file__).resolve().parents[3]
             / "deploy/staging/token-cli.py").read_text(encoding="utf-8")
    for forbudt in ('"--secret"', "'--secret'", '"--hemmelighet"'):
        assert forbudt not in kilde, forbudt
    assert "argparse" in kilde


# ---------------------------------------------------------------------------
# Porten på testplanen selv
# ---------------------------------------------------------------------------

def test_hver_feilvei_har_en_test():
    """«Én test per rad i feilveitabellen» — målt, ikke påstått.

    Legger noen til en rad i `feil.FEILVEIER` uten en test, faller denne.
    Fjerner noen en test, faller den også. Det er den eneste måten kravet
    kan holdes over tid.
    """
    from api.feil import FEIL
    from .test_api import DEKNING
    udekket = sorted(set(FEIL) - set(DEKNING))
    assert udekket == [], f"feilveier uten test: {udekket}"
    ukjente = sorted(set(DEKNING) - set(FEIL))
    assert ukjente == [], f"tester peker på ukjente feilveier: {ukjente}"


@pg
def test_dek_bootstrap_taaler_samtidige_forstegangsskrivinger(server, migrator,
                                                              malpolicy, token,
                                                              monkeypatch):
    """Kappløpet lasttesten avslørte.

    En tenant uten DEK som treffes av 20 samtidige saksskrivinger: alle ser
    «ingen aktiv DEK». Uten serialisering vinner én og de 19 andre får
    unikbrudd mot `en_aktiv_dek_per_tenant` — som blir `unntaksskriv_feilet`
    og ruller HELE beslutningen, inkludert loggposten.

    Feilen finnes bare i det ene øyeblikket en tenant får sin første sak.
    Hver enkelttest passerte; det var lasttesten som fant den. Nå står
    kappløpet som en fast test, ikke som noe man må huske å laste-teste.
    """
    from api import policyregister
    p = yaml.safe_load(yaml.safe_dump(malpolicy))
    policyregister.registrer(migrator, TENANT, p, p["meta"]["status"])
    migrator.commit()

    # Nullstill DEK-en slik at tenanten faktisk mangler en.
    migrator.execute("ALTER TABLE tenant_nokler DISABLE TRIGGER"
                     " tenant_nokler_ingen_delete")
    migrator.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    migrator.execute("DELETE FROM tenant_nokler WHERE tenant=%s", (TENANT,))
    migrator.execute("ALTER TABLE tenant_nokler ENABLE TRIGGER"
                     " tenant_nokler_ingen_delete")
    migrator.commit()

    # TVING VINDUET ÅPENT. Uten dette beviser testen ingenting: mutasjonstest
    # viste at den besto også med advisory-låsen fjernet, fordi
    # dobbeltsjekk-SELECT-en rakk å se vinnerens committede rad før de andre
    # kom til INSERT. Kappløpet oppsto bare ikke under den tilfeldige
    # timingen — samme felle som trådtesten i PR-002.
    #
    # Med en treg nøkkelgenerering rekker ALLE tjue inn i
    # opprettelsesgrenen før noen committer. Da taper en implementasjon uten
    # lås garantert: 19 unikbrudd mot `en_aktiv_dek_per_tenant`.
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from db import kryptering as krypteringsmodul
    ekte_generer = AESGCM.generate_key

    def treg_generer(bit_length):
        time.sleep(0.4)
        return ekte_generer(bit_length)

    monkeypatch.setattr(krypteringsmodul.AESGCM, "generate_key",
                        staticmethod(treg_generer))

    tok, _ = token()
    statuser: list = []
    start = threading.Barrier(20)

    def kjor(i):
        start.wait(30)
        r = _post(server, p, hendelse_uten_attestasjoner(
            ressurs=f"fak-dek{i}", handling="finnes.ikke"), tok, f"dek-{i}")
        statuser.append(r.status_code)

    traader = [threading.Thread(target=kjor, args=(i,)) for i in range(20)]
    for t in traader:
        t.start()
    for t in traader:
        t.join(60)

    assert statuser.count(200) == 20, \
        f"{20 - statuser.count(200)} saker feilet under DEK-bootstrap: {statuser}"

    migrator.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    dekker = migrator.execute("SELECT count(*) FROM tenant_nokler"
                              " WHERE tenant=%s AND aktiv",
                              (TENANT,)).fetchone()[0]
    saker = migrator.execute("SELECT count(*) FROM unntak WHERE tenant=%s",
                             (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert dekker == 1, f"{dekker} aktive DEK-er — bootstrap kjørte flere ganger"
    assert saker == 20
