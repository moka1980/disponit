"""Modul-onboarding over HTTP (035): hemmelighet → token → claim → kapabilitet.

DB-lagets kontrakt prøves i `test_modul_onboarding.py`; her prøves
NETTVERKSVEIENE: scope-porten på utstedelsen, lukkede skjemaer (portene
7–8), at «du har ikke lov» og «det finnes ikke arbeid» aldri ser like ut
(18–19), draining-avslaget (10), rotasjon over HTTP (20–21), og den fulle
kjeden helt til `artifacts:upload`-kapabiliteten i claim-svaret (14–16).

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
import json
import secrets
import uuid

import pytest

from .test_api import (DSN, MIGRATOR_DSN, PEPPER, TENANT, _lag_token,  # noqa: F401
                       dekker, migrator, miljo, token)                 # noqa: F401
from .test_m37 import _lag_sak, _sett_kontekst
from .test_pr014a_cp5_claim import _lag_oppdrag_type

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _u():
    return secrets.token_hex(4)


def _kjede(conn, *, status="aktiv", livslop="claiming", miljo_="staging",
           typenavn=None, artefakttype=None):
    """modulhode→kontrakt→release→deployment (+ register-rader). Returnerer
    (modul, rel). Typenavnet må kalleren selv koble til `oppdragskontrakt`
    (monkeypatch) hvis claim-utledningen skal finne prefikser."""
    u = _u()
    modul, rel = f"m-{u}", f"r-{u}"
    khash = "k-" + secrets.token_hex(8)
    conn.execute("INSERT INTO modulhode (modul_id,status) VALUES (%s,%s)",
                 (modul, status))
    conn.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES (%s,1,%s,'p','k','krever_outbox',"
        "'kompenserende')", (modul, khash))
    conn.execute(
        "INSERT INTO modulrelease (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,manifest_hash,artifact_digest)"
        " VALUES (%s,%s,1,%s,'mh','ad')", (modul, rel, khash))
    conn.execute(
        "INSERT INTO moduldeployment (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,miljo,livslop) VALUES (%s,%s,1,%s,%s,%s)",
        (modul, rel, khash, miljo_, livslop))
    if typenavn:
        conn.execute(
            "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
            "kontraktversjon,kontrakt_hash) VALUES (%s,%s,1,%s)",
            (typenavn, modul, khash))
    if artefakttype:
        conn.execute(
            "INSERT INTO artefakttype_register (artefakttype,eiermodul,"
            "kontraktversjon,kontrakt_hash,skjema_hash)"
            " VALUES (%s,%s,1,%s,%s)",
            (artefakttype, modul, khash, secrets.token_hex(32)))
    conn.commit()
    return modul, rel


def _kjent_type(monkeypatch, typenavn, prefiks):
    """Gjør registerets typenavn kjent for den LUKKEDE typeregistreringen i
    `oppdragskontrakt` — utledningen (og minimeringen) er fail-closed mot
    ukjente navn, og testene skal ikke legge varige rader i den delte
    registertabellen under navn koden eier."""
    import oppdragskontrakt as ok
    t = ok.Oppdragstype(
        navn=typenavn, handlingsprefikser=(prefiks,),
        felter=frozenset({"handling", "ressurs_id"}),
        paakrevde=frozenset({"handling", "ressurs_id"}),
        beskrivelse="035-test")
    monkeypatch.setitem(ok.OPPDRAGSTYPER, typenavn, t)


def _onboard_token(klient, migrator_, modul, rel, *, miljo_="staging"):
    """Fase 1 + 2 over HTTP. -> (modultoken-streng, hemmelighet-svaret)."""
    ops, _ = _lag_token(migrator_, TENANT, "drift", ["modules:onboard"])
    r = klient.post("/v1/modul/onboarding",
                    json={"modul_id": modul, "miljo": miljo_,
                          "release_id": rel},
                    headers={"authorization": f"Bearer {ops}"})
    assert r.status_code == 201, r.text
    svar = r.json()
    r2 = klient.post("/v1/modul/onboarding/innlos",
                     json={"hemmelighet": svar["hemmelighet"]})
    assert r2.status_code == 201, r2.text
    return r2.json()["token"], svar


@pg
@dekker("onboarding_avvist")
def test_full_kjede_hemmelighet_token_claim_kapabilitet(migrator, miljo,
                                                        monkeypatch):
    """Den gyldne veien, hele veien (portene 3–5, 9-motstykket, 14, 16):
    utsted (hemmelighet finnes kun hashet i basen), innløs (engangs),
    claim med EKTE deployment-identitet fra tokenet, og
    `artifacts:upload`-kapabilitet i claim-svaret — bundet og brukbar
    mot POST /v1/artefakt, mens kvitterings-jti-en ikke er det."""
    from starlette.testclient import TestClient
    from api.app import lag_app

    typenavn = f"t{_u()}"
    prefiks = f"h{_u()}."
    _kjent_type(monkeypatch, typenavn, prefiks)
    modul, rel = _kjede(migrator, typenavn=typenavn,
                        artefakttype=f"kvit.o{_u()}.selvtest")
    sak, logg = _lag_sak(migrator, TENANT)
    opp_id, _ = _lag_oppdrag_type(migrator, TENANT, sak, logg,
                                  oppdragstype=typenavn, eiermodul=modul,
                                  handling=prefiks + "send")
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            mtk, utstedt = _onboard_token(c, migrator, modul, rel)
            # Port 3: hemmeligheten og tokenet finnes KUN som MAC i basen.
            secret_del = utstedt["hemmelighet"].split(".", 1)[1]
            for tabell, kolonne in (("modul_onboarding", "hemmelighet_hash"),
                                    ("modultoken", "token_mac")):
                rader = migrator.execute(
                    f"SELECT {kolonne} FROM {tabell}").fetchall()
                assert all(secret_del != r[0]
                           and mtk.split(".", 1)[1] != r[0]
                           for r in rader), "klartekst i basen"
            migrator.rollback()
            # Port 4 over HTTP: andre innløsning avvises, samme svar utad.
            r = c.post("/v1/modul/onboarding/innlos",
                       json={"hemmelighet": utstedt["hemmelighet"]})
            assert (r.status_code, r.json()["feil"]) == (
                403, "onboarding_avvist"), r.text
            # Codex P2: og det avviste forsøket står i sporet ETTER at
            # requesten er ferdig — avvisningen committes, den raiser ikke.
            spor = migrator.execute(
                "SELECT count(*) FROM modultoken_hendelse WHERE"
                " onboarding_id=%s AND hendelse='avvist_bruk'",
                (utstedt["onboarding_id"],)).fetchone()[0]
            migrator.rollback()
            assert spor == 1, "avvist innløsning ble ikke revidert"

            r = c.post("/v1/oppdrag/claim", json={},
                       headers={"authorization": f"Bearer {mtk}"})
            assert r.status_code == 200, r.text
            svar = r.json()
            assert svar["oppdrag_id"] == opp_id
            assert svar["payload"]["handling"] == prefiks + "send"
            # Port 14: opplastingskapabilitet i claim-svaret, adskilt fra
            # kvitteringens (port 16 — ulik jti er selve skillet; kryssbruk
            # avvises strukturelt i 017 og er bevist der).
            assert svar["opplasting"] is not None, svar
            assert svar["opplasting"]["jti"] != svar["kvittering_jti"]
            # ... og bindingen i basen bærer modultokenets deployment.
            _sett_kontekst(migrator, TENANT)
            binding = migrator.execute(
                "SELECT modul_id, release_id, module_epoch FROM"
                " artefaktkapabilitet WHERE jti=%s",
                (svar["opplasting"]["jti"],)).fetchone()
            migrator.rollback()
            assert binding == (modul, rel, 0), binding
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_utstedelse_krever_scope_og_modultoken_kan_ikke_onboarde(migrator,
                                                                 miljo,
                                                                 monkeypatch):
    """Port 1 + formeringsvernet: uten `modules:onboard` → 403; et
    MODULTOKEN kan aldri utstede nye hemmeligheter (401)."""
    from starlette.testclient import TestClient
    from api.app import lag_app

    typenavn = f"t{_u()}"
    _kjent_type(monkeypatch, typenavn, f"h{_u()}.")
    modul, rel = _kjede(migrator, typenavn=typenavn)
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            uten, _ = _lag_token(migrator, TENANT, "drift",
                                 ["exceptions:read"])
            r = c.post("/v1/modul/onboarding",
                       json={"modul_id": modul, "miljo": "staging",
                             "release_id": rel},
                       headers={"authorization": f"Bearer {uten}"})
            assert (r.status_code, r.json()["feil"]) == (403, "scope_mangler")

            mtk, _ = _onboard_token(c, migrator, modul, rel)
            r2 = c.post("/v1/modul/onboarding",
                        json={"modul_id": modul, "miljo": "staging",
                              "release_id": rel},
                        headers={"authorization": f"Bearer {mtk}"})
            assert r2.status_code == 401, r2.text
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_lukkede_skjemaer_avviser_identitetsfelt(migrator, miljo,
                                                 monkeypatch):
    """Portene 7–8: innløsning med modul_id/release_id i kroppen avvises;
    claim med release/miljø/epoch avvises — IKKE ignoreres."""
    from starlette.testclient import TestClient
    from api.app import lag_app

    typenavn = f"t{_u()}"
    _kjent_type(monkeypatch, typenavn, f"h{_u()}.")
    modul, rel = _kjede(migrator, typenavn=typenavn)
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            ops, _ = _lag_token(migrator, TENANT, "drift",
                                ["modules:onboard"])
            r = c.post("/v1/modul/onboarding",
                       json={"modul_id": modul, "miljo": "staging",
                             "release_id": rel},
                       headers={"authorization": f"Bearer {ops}"})
            hemmelighet = r.json()["hemmelighet"]
            r2 = c.post("/v1/modul/onboarding/innlos",
                        json={"hemmelighet": hemmelighet,
                              "modul_id": "noe-annet"})
            assert (r2.status_code,
                    r2.json()["feil"]) == (400, "request_feilformet"), r2.text

            # ... og hemmeligheten er IKKE brent av den avviste requesten:
            r3 = c.post("/v1/modul/onboarding/innlos",
                        json={"hemmelighet": hemmelighet})
            assert r3.status_code == 201, r3.text
            mtk = r3.json()["token"]

            r4 = c.post("/v1/oppdrag/claim",
                        json={"release_id": "r-x"},
                        headers={"authorization": f"Bearer {mtk}"})
            assert (r4.status_code,
                    r4.json()["feil"]) == (400, "request_feilformet"), r4.text
    finally:
        a.tjeneste.pool.lukk()


@pg
@dekker("modulepoch_utdatert", "modul_ikke_claimbar")
def test_epoch_og_draining_er_403_aldri_204(migrator, miljo, monkeypatch):
    """Portene 10, 18–19: «du har ikke lov» og «det finnes ikke arbeid» må
    aldri se like ut. Tom kø med gyldig token → 204; draining deployment →
    403 modul_ikke_claimbar; utdatert epoch → 403 modulepoch_utdatert."""
    from starlette.testclient import TestClient
    from api.app import lag_app

    # To adskilte moduler: livsløpet er en enveiskjørt statemaskin
    # (draining kan aldri bli claiming igjen), så draining- og epoch-
    # tilfellene må bo i hver sin kjede.
    t1, t2 = f"t{_u()}", f"t{_u()}"
    _kjent_type(monkeypatch, t1, f"h{_u()}.")
    _kjent_type(monkeypatch, t2, f"h{_u()}.")
    modul1, rel1 = _kjede(migrator, typenavn=t1)
    modul2, rel2 = _kjede(migrator, typenavn=t2)
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            mtk1, _ = _onboard_token(c, migrator, modul1, rel1)
            hode1 = {"authorization": f"Bearer {mtk1}"}
            r = c.post("/v1/oppdrag/claim", json={}, headers=hode1)
            assert r.status_code == 204, r.text          # tom kø, med lov

            migrator.execute(
                "UPDATE moduldeployment SET livslop='draining'"
                " WHERE modul_id=%s", (modul1,))
            migrator.commit()
            r = c.post("/v1/oppdrag/claim", json={}, headers=hode1)
            assert (r.status_code, r.json()["feil"]) == (
                403, "modul_ikke_claimbar"), r.text

            mtk2, _ = _onboard_token(c, migrator, modul2, rel2)
            migrator.execute(
                "UPDATE modulhode SET module_epoch=module_epoch+1"
                " WHERE modul_id=%s", (modul2,))
            migrator.commit()
            r = c.post("/v1/oppdrag/claim", json={},
                       headers={"authorization": f"Bearer {mtk2}"})
            assert (r.status_code, r.json()["feil"]) == (
                403, "modulepoch_utdatert"), r.text
    finally:
        a.tjeneste.pool.lukk()


@pg
@dekker("modul_ikke_claimbar")
def test_tapt_autorisasjon_under_claimen_er_403_ikke_204(migrator, miljo,
                                                         monkeypatch):
    """Codex P2 / port 18–19: pre-porten leses UTEN modul-låsen. Mister
    modulen lov til å claime ETTER den lesningen, men før
    `claim_neste_oppdrag` får låsen, forkaster SQL-funksjonen hver kandidat
    og returnerer ingen rad. Svaret skal da være 403 modul_ikke_claimbar —
    et 204 ville sagt «ingen jobber» til en modul som nettopp mistet
    fullmakten, og fått den til å polle videre i stedet for å stanse.

    Vinduet treffes deterministisk: `claim_id = secrets.token_hex(16)` er
    det siste steget mellom porten og claimen, så dreneringen legges inn
    derfra — fra en egen tilkobling, siden testtråden står blokkert i
    requesten."""
    import psycopg
    from starlette.testclient import TestClient
    from api import app as appmodul
    from api.app import lag_app

    t = f"t{_u()}"
    _kjent_type(monkeypatch, t, f"h{_u()}.")
    modul, rel = _kjede(migrator, typenavn=t)
    a = lag_app(DSN)
    ekte_token_hex = secrets.token_hex
    traff = []

    def _drener_i_vinduet(n=32):
        if n == 16 and not traff:
            traff.append(True)
            sidekanal = psycopg.connect(MIGRATOR_DSN)
            try:
                sidekanal.execute(
                    "UPDATE moduldeployment SET livslop='draining'"
                    " WHERE modul_id=%s", (modul,))
                sidekanal.commit()
            finally:
                sidekanal.close()
        return ekte_token_hex(n)

    try:
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, modul, rel)
            monkeypatch.setattr(appmodul.secrets, "token_hex",
                                _drener_i_vinduet)
            r = c.post("/v1/oppdrag/claim", json={},
                       headers={"authorization": f"Bearer {mtk}"})
            assert traff, "dreneringen traff aldri claim-vinduet"
            assert (r.status_code, r.json()["feil"]) == (
                403, "modul_ikke_claimbar"), r.text
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_token_kan_ikke_claime_annen_modul_eller_release(migrator, miljo,
                                                         monkeypatch):
    """Portene 9/11: tokenets deployment-identitet er hele autorisasjonen —
    modul A-s token ser aldri modul B-s oppdrag, og et token for release A
    kan ikke claime det som krever B."""
    from starlette.testclient import TestClient
    from api.app import lag_app

    ta, tb = f"t{_u()}", f"t{_u()}"
    pa, pb = f"ha{_u()}.", f"hb{_u()}."
    _kjent_type(monkeypatch, ta, pa)
    _kjent_type(monkeypatch, tb, pb)
    modul_a, rel_a = _kjede(migrator, typenavn=ta)
    modul_b, rel_b = _kjede(migrator, typenavn=tb)
    sak, logg = _lag_sak(migrator, TENANT)
    _lag_oppdrag_type(migrator, TENANT, sak, logg, oppdragstype=tb,
                      eiermodul=modul_b, handling=pb + "send")
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            mtk_a, _ = _onboard_token(c, migrator, modul_a, rel_a)
            r = c.post("/v1/oppdrag/claim", json={},
                       headers={"authorization": f"Bearer {mtk_a}"})
            # Modul B-s oppdrag er USYNLIG for A — tom kø, ikke lekkasje.
            assert r.status_code == 204, r.text
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_innlosning_rates_selv_nar_kalleren_varierer_id_en(migrator, miljo,
                                                           monkeypatch):
    """Codex P1: ratenøkkelen var UTELUKKENDE onboarding-id-en fra kroppen,
    altså kallerens egen input. En angriper som sender en fersk, gyldig
    UUID i hver request traff aldri samme bøtte, slapp alltid gjennom, tok
    en pool-tilkobling og kjørte `innlos_onboarding` mot Postgres — uten å
    kjenne verken hemmelighet eller en eneste ekte onboarding-id.

    Rutebudsjettet er nøkkelen ingen valgt id kan gå utenom. Tallet
    monkeypatches ned her; det som prøves er at grensen finnes og at den
    ikke lar seg omgå av variasjon.

    MUTASJONEN SOM DREPER DENNE: fjern `onb:innlos`-sjekken og la bare
    `onb:<id>` stå igjen."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    import api.modulonboarding as mo

    monkeypatch.setattr(mo, "INNLOS_RATE_PER_MIN", 3)
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            koder = []
            for _ in range(5):
                # FERSK id hver gang: per-id-bøtta ser aldri samme nøkkel.
                falsk = f"onb_{uuid.uuid4()}.{secrets.token_hex(32)}"
                r = c.post("/v1/modul/onboarding/innlos",
                           json={"hemmelighet": falsk})
                koder.append(r.status_code)
            assert koder[:3] == [403, 403, 403], koder
            assert koder[3:] == [429, 429], koder
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_claim_med_dypt_nostet_kropp_er_request_feil(migrator, miljo,
                                                     monkeypatch):
    """Codex P2: `json.loads` er REKURSIV. Claim-rutens LUKKEDE skjema
    parser kroppen for å kunne avvise parametre — og et syntaktisk gyldig,
    dypt nøstet dokument på noen få kilobyte (langt under kroppsgrensen på
    256 KiB) traff rekursjonsgrensen. RecursionError er en RuntimeError,
    ikke en ValueError, så `except ValueError` alene slapp den ut som
    generisk 500 i stedet for det dokumenterte `request_feilformet`.

    Kroppen bygges som TEKST: `json.dumps` av en 5 000-dyp struktur ville
    tatt livet av testen selv, ikke serveren.

    MUTASJONEN SOM DREPER DENNE: fjern RecursionError fra except-en rundt
    claim-kroppens `json.loads`."""
    from starlette.testclient import TestClient
    from api.app import lag_app

    typenavn = f"t{_u()}"
    _kjent_type(monkeypatch, typenavn, f"h{_u()}.")
    modul, rel = _kjede(migrator, typenavn=typenavn)
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, modul, rel)
            dybde = 5000
            kropp = '{"a":' * dybde + "1" + "}" * dybde
            assert len(kropp) < 100_000, "testkroppen skal være liten"
            r = c.post("/v1/oppdrag/claim", content=kropp,
                       headers={"authorization": f"Bearer {mtk}",
                                "content-type": "application/json"})
            assert (r.status_code, r.json()["feil"]) == (
                400, "request_feilformet"), r.text
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_rotasjon_over_http_og_tilbakekalling(migrator, miljo, monkeypatch):
    """Portene 20–22 over nettverket: rotasjonen svarer med nytt token (vist
    én gang), forgjengeren har nådevindu, en andre rotasjon av samme
    forgjenger er konflikt, og eksplisitt tilbakekalling dreper umiddelbart."""
    from starlette.testclient import TestClient
    from api.app import lag_app

    typenavn = f"t{_u()}"
    _kjent_type(monkeypatch, typenavn, f"h{_u()}.")
    modul, rel = _kjede(migrator, typenavn=typenavn)
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, modul, rel)
            r = c.post("/v1/modul/token/roter", json={},
                       headers={"authorization": f"Bearer {mtk}"})
            assert r.status_code == 201, r.text
            nytt = r.json()["token"]
            # Etterfølgeren virker; forgjengeren virker OGSÅ (nådevindu) —
            # men kan ikke rotere en gang til (én etterfølger).
            for tok, kode in ((nytt, 204), (mtk, 204)):
                rr = c.post("/v1/oppdrag/claim", json={},
                            headers={"authorization": f"Bearer {tok}"})
                assert rr.status_code == kode, (tok[:12], rr.text)
            r2 = c.post("/v1/modul/token/roter", json={},
                        headers={"authorization": f"Bearer {mtk}"})
            assert r2.status_code == 409, r2.text

            # Eksplisitt tilbakekalling av ETTERFØLGEREN: umiddelbar.
            ops, _ = _lag_token(migrator, TENANT, "drift",
                                ["modules:onboard"])
            tid = nytt.split(".", 1)[0][4:]
            r3 = c.post("/v1/modul/token/tilbakekall",
                        json={"token_id": tid, "grunn": "test"},
                        headers={"authorization": f"Bearer {ops}"})
            assert r3.status_code == 200, r3.text
            r4 = c.post("/v1/oppdrag/claim", json={},
                        headers={"authorization": f"Bearer {nytt}"})
            assert r4.status_code == 401, r4.text
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_rotasjon_taaler_at_svaret_gikk_tapt(migrator, miljo, monkeypatch):
    """Codex P1 over nettverket: deploymenten roterer, svaret kommer aldri
    frem (tidsavbrudd, død proxy), og den prøver igjen med SITT fortsatt
    gyldige token. Før fikk den 409 og var ute av drift 15 minutter senere
    — etterfølgerens hemmelighet fantes ikke hos noen, men okkuperte
    plassen. Sender den samme `rotasjon_id`, får den i stedet et ferskt,
    brukbart token.

    Codex P1 (runde 3): og det FØRSTE svaret lever fortsatt. Serveren vet
    ikke om det gikk tapt eller bare var forsinket, så begge tokenene må
    virke — ellers kan et gjentatt forsøk drepe nettopp den hemmeligheten
    deploymenten satt igjen med.

    En ANNEN nøkkel er fortsatt 409: det er da to rotasjoner, ikke ett
    forsøk om igjen."""
    from starlette.testclient import TestClient
    from api.app import lag_app

    typenavn = f"t{_u()}"
    _kjent_type(monkeypatch, typenavn, f"h{_u()}.")
    modul, rel = _kjede(migrator, typenavn=typenavn)
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, modul, rel)
            nokkel = str(uuid.uuid4())
            r = c.post("/v1/modul/token/roter", json={"rotasjon_id": nokkel},
                       headers={"authorization": f"Bearer {mtk}"})
            assert r.status_code == 201, r.text
            tapt = r.json()["token"]        # ... svaret «kom aldri frem»

            r2 = c.post("/v1/modul/token/roter", json={"rotasjon_id": nokkel},
                        headers={"authorization": f"Bearer {mtk}"})
            assert r2.status_code == 201, r2.text
            fersk = r2.json()["token"]
            assert fersk != tapt
            # BEGGE virker: deploymenten bruker den den faktisk fikk.
            for tok in (fersk, tapt):
                rr = c.post("/v1/oppdrag/claim", json={},
                            headers={"authorization": f"Bearer {tok}"})
                assert rr.status_code == 204, (tok[:12], rr.text)

            # Ny nøkkel fra samme forgjenger = ekte konflikt.
            r3 = c.post("/v1/modul/token/roter",
                        json={"rotasjon_id": str(uuid.uuid4())},
                        headers={"authorization": f"Bearer {mtk}"})
            assert r3.status_code == 409, r3.text
            # ... og en nøkkel som ikke er en UUID er en formfeil, ikke et
            # forsøk: lukket skjema hele veien.
            r4 = c.post("/v1/modul/token/roter", json={"rotasjon_id": "x"},
                        headers={"authorization": f"Bearer {mtk}"})
            assert (r4.status_code,
                    r4.json()["feil"]) == (400, "request_feilformet"), r4.text
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_innlosningen_taaler_at_svaret_gikk_tapt(migrator, miljo,
                                                 monkeypatch):
    """Codex P1: samme tapte pakke som rotasjonen tåler, på utvekslingen som
    STARTER familien. Deploymenten innløser, databasen committer, og 201-et
    kommer aldri frem. Da holder ingen tokenet — det finnes bare som MAC —
    mens hemmeligheten er stemplet brukt. Før svarte et nytt forsøk
    `onboarding_avvist`, og modulen måtte onboardes på nytt av et menneske
    fordi en pakke ble borte.

    Med samme `innlosning_id` mynter serveren neste forsøk i samme
    innløsning, og BEGGE tokenene lever: serveren vet ikke om det første
    svaret gikk tapt eller bare var forsinket, og skal ikke drepe en
    credential deploymenten kan ha lagret.

    Uten nøkkel — og med en ANNEN nøkkel — er en brukt hemmelighet fortsatt
    avvist. Og konvergensen: roterer deploymenten fra det ene tokenet, er
    det bevist hvilket den holder, og søskenet kappes.

    MUTASJONEN SOM DREPER DENNE: fjern `p_innlosning_id`-grenen i
    `innlos_onboarding`, så faller det gjentatte forsøket til 403."""
    from starlette.testclient import TestClient
    from api.app import lag_app

    typenavn = f"t{_u()}"
    _kjent_type(monkeypatch, typenavn, f"h{_u()}.")
    modul, rel = _kjede(migrator, typenavn=typenavn)
    ops, _ = _lag_token(migrator, TENANT, "drift", ["modules:onboard"])
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            r = c.post("/v1/modul/onboarding",
                       json={"modul_id": modul, "miljo": "staging",
                             "release_id": rel},
                       headers={"authorization": f"Bearer {ops}"})
            assert r.status_code == 201, r.text
            hemmelighet = r.json()["hemmelighet"]

            nokkel = str(uuid.uuid4())
            r1 = c.post("/v1/modul/onboarding/innlos",
                        json={"hemmelighet": hemmelighet,
                              "innlosning_id": nokkel})
            assert r1.status_code == 201, r1.text
            tapt = r1.json()["token"]      # ... svaret «kom aldri frem»

            # Uten nøkkel er hemmeligheten brukt opp, som før.
            assert c.post("/v1/modul/onboarding/innlos",
                          json={"hemmelighet": hemmelighet}
                          ).status_code == 403
            # En ANNEN nøkkel er ikke det samme forsøket om igjen.
            assert c.post("/v1/modul/onboarding/innlos",
                          json={"hemmelighet": hemmelighet,
                                "innlosning_id": str(uuid.uuid4())}
                          ).status_code == 403

            r2 = c.post("/v1/modul/onboarding/innlos",
                        json={"hemmelighet": hemmelighet,
                              "innlosning_id": nokkel})
            assert r2.status_code == 201, r2.text
            fersk = r2.json()["token"]
            assert fersk != tapt
            # BEGGE virker: deploymenten bruker den den faktisk fikk.
            for tok in (fersk, tapt):
                rr = c.post("/v1/oppdrag/claim", json={},
                            headers={"authorization": f"Bearer {tok}"})
                assert rr.status_code == 204, (tok[:12], rr.text)

            # KONVERGENS: rotasjon fra det ene beviser hvilket som er i
            # bruk — søskenet kappes umiddelbart.
            rr = c.post("/v1/modul/token/roter", json={},
                        headers={"authorization": f"Bearer {fersk}"})
            assert rr.status_code == 201, rr.text
            rr = c.post("/v1/oppdrag/claim", json={},
                        headers={"authorization": f"Bearer {tapt}"})
            assert rr.status_code == 401, rr.text

            # En nøkkel som ikke er en UUID er en formfeil, ikke et forsøk.
            rr = c.post("/v1/modul/onboarding/innlos",
                        json={"hemmelighet": hemmelighet,
                              "innlosning_id": "x"})
            assert (rr.status_code,
                    rr.json()["feil"]) == (400, "request_feilformet"), rr.text
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_testprefikset_utledes_aldri_for_produksjon(migrator, miljo,
                                                    monkeypatch):
    """§8/deploy-port: en `test.`-artefakttype gir INGEN opplastings-
    kapabilitet når kontraktens claiming-deployment står i produksjon —
    claimen lykkes, kapabiliteten uteblir (fail-closed, som port 15)."""
    from starlette.testclient import TestClient
    from api.app import lag_app

    typenavn = f"t{_u()}"
    prefiks = f"h{_u()}."
    _kjent_type(monkeypatch, typenavn, prefiks)
    modul, rel = _kjede(migrator, typenavn=typenavn, miljo_="produksjon",
                        artefakttype=f"test.o{_u()}.kvittering")
    sak, logg = _lag_sak(migrator, TENANT)
    _lag_oppdrag_type(migrator, TENANT, sak, logg, oppdragstype=typenavn,
                      eiermodul=modul, handling=prefiks + "send")
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, modul, rel,
                                    miljo_="produksjon")
            r = c.post("/v1/oppdrag/claim", json={},
                       headers={"authorization": f"Bearer {mtk}"})
            assert r.status_code == 200, r.text
            assert r.json()["opplasting"] is None, (
                "test.-artefakttype utledet for produksjon")
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_testprefikset_ser_tokenets_eget_miljo_ikke_kontraktens(migrator,
                                                                miljo,
                                                                monkeypatch):
    """Codex P2: test-artefaktporten spurte «finnes denne kontrakten i
    produksjon et sted?» og ikke «hvilket miljø claimet nå?». Er samme
    kontrakt deployet i BÅDE staging og produksjon, forsvant staging-
    deploymentens `test.`-type i det produksjonsdeploymenten kom til —
    selvtesten sluttet å virke uten at noe sa fra. Miljøet står i
    modultokenet. Kontroll: la `er_produksjon` gå på EXISTS-oppslaget for
    ModulAutentisert igjen, så blir denne rød."""
    from starlette.testclient import TestClient
    from api.app import lag_app

    typenavn = f"t{_u()}"
    prefiks = f"h{_u()}."
    _kjent_type(monkeypatch, typenavn, prefiks)
    modul, rel = _kjede(migrator, typenavn=typenavn, miljo_="staging",
                        artefakttype=f"test.o{_u()}.selvtest")
    # SAMME release og kontrakt, også deployet i produksjon og claiming.
    khash = migrator.execute(
        "SELECT kontrakt_hash FROM modulrelease WHERE modul_id=%s"
        " AND release_id=%s", (modul, rel)).fetchone()[0]
    migrator.execute(
        "INSERT INTO moduldeployment (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,miljo,livslop) VALUES (%s,%s,1,%s,'produksjon',"
        "'claiming')", (modul, rel, khash))
    sak, logg = _lag_sak(migrator, TENANT)
    _lag_oppdrag_type(migrator, TENANT, sak, logg, oppdragstype=typenavn,
                      eiermodul=modul, handling=prefiks + "send")
    migrator.commit()
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, modul, rel,
                                    miljo_="staging")
            r = c.post("/v1/oppdrag/claim", json={},
                       headers={"authorization": f"Bearer {mtk}"})
            assert r.status_code == 200, r.text
            svar = r.json()
            assert svar["opplasting"] is not None, (
                "staging-tokenet mistet selvtest-kapabiliteten fordi"
                " kontrakten OGSÅ står i produksjon")
            assert svar["opplasting"]["artefakttype"].startswith("test.")
            # ... og bindingen bærer tokenets EGEN release.
            _sett_kontekst(migrator, TENANT)
            binding = migrator.execute(
                "SELECT release_id FROM artefaktkapabilitet WHERE jti=%s",
                (svar["opplasting"]["jti"],)).fetchone()
            migrator.rollback()
            assert binding == (rel,), binding
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_modultoken_faar_bruke_kapabilitetene_claimen_delte_ut(migrator, miljo,
                                                               monkeypatch):
    """Codex P1: et modultoken bærer ingen scopes, så de gamle
    scope-portene på kvitteringen og opplastingen låste onboardet arbeid
    inne — claimen lyktes, men verken resultatet eller artefaktet kom
    noen vei, og token-cli utsteder ikke lenger et legacy-token som kunne
    tatt jobben. Fullmakten er OPPDRAGETS (jti-en fra claimen), ikke
    tokenets. Kontroll: fjern ModulAutentisert-unntakene i
    `_artefakt_upload`/`_oppdrag_kvittering`, så blir denne rød."""
    from starlette.testclient import TestClient
    from api.app import lag_app

    typenavn = f"t{_u()}"
    prefiks = f"h{_u()}."
    _kjent_type(monkeypatch, typenavn, prefiks)
    modul, rel = _kjede(migrator, typenavn=typenavn,
                        artefakttype=f"kvit.o{_u()}.selvtest")
    sak, logg = _lag_sak(migrator, TENANT)
    opp_id, _ = _lag_oppdrag_type(migrator, TENANT, sak, logg,
                                  oppdragstype=typenavn, eiermodul=modul,
                                  handling=prefiks + "send")
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            mtk, _ = _onboard_token(c, migrator, modul, rel)
            hode = {"authorization": f"Bearer {mtk}"}
            r = c.post("/v1/oppdrag/claim", json={}, headers=hode)
            assert r.status_code == 200, r.text
            svar = r.json()

            # Opplastingen går HELE veien — ikke `scope_mangler`.
            r = c.post("/v1/artefakt",
                       json={"kapabilitet_jti": svar["opplasting"]["jti"],
                             "rapport": {"funn": 0}}, headers=hode)
            assert r.status_code == 200, r.text
            assert len(r.json()["klartekst_sha256"]) == 64

            # Kvitteringen slipper forbi scope-porten og inn i den
            # jti-bundne innløsningen: en usignert kropp faller på
            # SIGNATUREN, som er den porten som faktisk skal ta den.
            r = c.post("/v1/oppdrag/kvittering",
                       json={"kvittering_jti": svar["kvittering_jti"],
                             "oppdrag_id": opp_id, "resultat": "ok"},
                       headers=hode)
            assert r.json().get("feil") != "scope_mangler", r.text
            assert r.status_code == 403 and \
                r.json()["feil"] == "kvittering_signatur_ugyldig", r.text
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_kapabiliteten_innloses_kun_av_deploymenten_som_claimet(migrator,
                                                                miljo,
                                                                monkeypatch):
    """Codex P1: opplastingsunntaket slipper ALLE modultokener for modulen
    forbi scope-porten, og innløsningen sammenlignet bare modul-id-en. Er
    samme modul deployet i både staging og produksjon, kunne en
    staging-arbeider som fikk en jti utstedt til produksjonsdeploymenten
    levere rapporten — og API-et ville ført evidensen på produksjons-
    releasen, altså attestert en deployment som ikke autentiserte
    requesten. Kapabiliteten bærer nå miljøet sitt, og innløsningen krever
    HELE den autentiserte deploymenten.

    Kontroll: fjern miljø-/release-leddene i `innlos_artefaktkapabilitet`
    (eller send NULL fra `_artefakt_upload`), så blir denne rød.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app

    typenavn = f"t{_u()}"
    prefiks = f"h{_u()}."
    _kjent_type(monkeypatch, typenavn, prefiks)
    modul, rel = _kjede(migrator, typenavn=typenavn, miljo_="produksjon",
                        artefakttype=f"kvit.o{_u()}.rapport")
    # SAMME release og kontrakt, også deployet i staging og claiming: to
    # levende deployments av én modul, med hvert sitt modultoken.
    khash = migrator.execute(
        "SELECT kontrakt_hash FROM modulrelease WHERE modul_id=%s"
        " AND release_id=%s", (modul, rel)).fetchone()[0]
    migrator.execute(
        "INSERT INTO moduldeployment (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,miljo,livslop) VALUES (%s,%s,1,%s,'staging',"
        "'claiming')", (modul, rel, khash))
    sak, logg = _lag_sak(migrator, TENANT)
    _lag_oppdrag_type(migrator, TENANT, sak, logg, oppdragstype=typenavn,
                      eiermodul=modul, handling=prefiks + "send")
    migrator.commit()
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            prod = _onboard_token(c, migrator, modul, rel,
                                  miljo_="produksjon")[0]
            stg = _onboard_token(c, migrator, modul, rel, miljo_="staging")[0]
            r = c.post("/v1/oppdrag/claim", json={},
                       headers={"authorization": f"Bearer {prod}"})
            assert r.status_code == 200, r.text
            jti = r.json()["opplasting"]["jti"]

            # Staging-tokenet slipper forbi scope-porten (unntaket), men
            # kapabiliteten er produksjonsdeploymentens.
            r = c.post("/v1/artefakt",
                       json={"kapabilitet_jti": jti, "rapport": {"funn": 0}},
                       headers={"authorization": f"Bearer {stg}"})
            # `kapabilitet_ugyldig` er 401 i feiltabellen: en fullmakt som
            # ikke er denne deploymentens, er ikke en fullmakt.
            assert r.status_code == 401 and \
                r.json()["feil"] == "kapabilitet_ugyldig", r.text

            # Og deploymenten som faktisk claimet, kommer fortsatt inn.
            r = c.post("/v1/artefakt",
                       json={"kapabilitet_jti": jti, "rapport": {"funn": 0}},
                       headers={"authorization": f"Bearer {prod}"})
            assert r.status_code == 200, r.text
            _sett_kontekst(migrator, TENANT)
            assert migrator.execute(
                "SELECT miljo FROM artefaktkapabilitet WHERE jti=%s",
                (jti,)).fetchone() == ("produksjon",)
            migrator.rollback()
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_kvitteringskapabiliteten_innloses_kun_av_deploymenten_som_claimet(
        migrator, miljo, monkeypatch):
    """Codex P1: samme hull som over, på KVITTERINGEN — og der er innsatsen
    høyere, siden kvitteringen avslutter oppdraget. Kvitteringsveien slipper
    alle modultokener for modulen forbi scope-porten (retten ER
    kapabilitetens), og innløsningen sammenlignet bare `auth.rolle`, som er
    modulens id. En delt eller feilrutet `kvittering_jti` lot dermed
    staging-deploymenten levere resultatet for produksjonsdeploymentens
    claim. Kapabiliteten bærer nå miljø + release, og innløsningen krever
    HELE den autentiserte deploymenten.

    Porten prøves der den er: staging-tokenet skal falle på
    `kapabilitet_ugyldig` (401), mens produksjonstokenet kommer forbi den og
    videre til SIGNATUR-porten — som er den neste ekte porten på veien.

    Kontroll: send NULL i stedet for `d_miljo`/`d_release` fra
    `_ingest_kvittering`, eller fjern miljøleddene i
    `innlos_kvitteringskapabilitet`, så blir denne rød."""
    from starlette.testclient import TestClient
    from api.app import lag_app

    typenavn = f"t{_u()}"
    prefiks = f"h{_u()}."
    _kjent_type(monkeypatch, typenavn, prefiks)
    modul, rel = _kjede(migrator, typenavn=typenavn, miljo_="produksjon")
    khash = migrator.execute(
        "SELECT kontrakt_hash FROM modulrelease WHERE modul_id=%s"
        " AND release_id=%s", (modul, rel)).fetchone()[0]
    migrator.execute(
        "INSERT INTO moduldeployment (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,miljo,livslop) VALUES (%s,%s,1,%s,'staging',"
        "'claiming')", (modul, rel, khash))
    sak, logg = _lag_sak(migrator, TENANT)
    opp_id, _ = _lag_oppdrag_type(migrator, TENANT, sak, logg,
                                  oppdragstype=typenavn, eiermodul=modul,
                                  handling=prefiks + "send")
    migrator.commit()
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            prod = _onboard_token(c, migrator, modul, rel,
                                  miljo_="produksjon")[0]
            stg = _onboard_token(c, migrator, modul, rel, miljo_="staging")[0]
            r = c.post("/v1/oppdrag/claim", json={},
                       headers={"authorization": f"Bearer {prod}"})
            assert r.status_code == 200, r.text
            jti = r.json()["kvittering_jti"]
            kropp = {"kvittering_jti": jti, "oppdrag_id": opp_id,
                     "resultat": "ok"}

            r = c.post("/v1/oppdrag/kvittering", json=kropp,
                       headers={"authorization": f"Bearer {stg}"})
            assert r.status_code == 401 and \
                r.json()["feil"] == "kapabilitet_ugyldig", r.text

            # Deploymenten som claimet kommer forbi kapabilitetsporten og
            # helt frem til signaturen (kroppen her er usignert).
            r = c.post("/v1/oppdrag/kvittering", json=kropp,
                       headers={"authorization": f"Bearer {prod}"})
            assert r.status_code == 403 and \
                r.json()["feil"] == "kvittering_signatur_ugyldig", r.text

            # Tabellen eies av m37_claimer; migrator er medlem WITH INHERIT
            # FALSE og må ta rollen eksplisitt for å lese den.
            migrator.execute("SET LOCAL ROLE disponit_m37_claimer")
            assert migrator.execute(
                "SELECT miljo, release_id FROM kvitteringskapabiliteter"
                " WHERE jti=%s", (jti,)).fetchone() == ("produksjon", rel)
            migrator.rollback()
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_nodstopp_etter_preauth_stopper_innlosningen(migrator, miljo,
                                                     monkeypatch):
    """Codex P1: `preauth` eier og LUKKER sin egen transaksjon, og
    forretningstransaksjonen starter etterpå. Committer et nødstopp mellom
    de to, er tokenet drept i basen — men `ModulAutentisert`-objektet lever
    videre i requesten som alt er i gang, og innløsningsfunksjonene
    sammenligner kun IDENTITET (modul/miljø/release). Uten revalideringen
    kom både kvitteringen og artefaktet fra den nødstoppede deploymenten
    inn, og «terminerer tokenfamilien øyeblikkelig» var et løfte
    plattformen ikke holdt. Claim-veien hadde aldri hullet —
    `claim_neste_oppdrag` re-verifiserer under modul-låsen.

    Vinneren av kappløpet er avgjort på forhånd: nødstoppet legges inn fra
    en EGEN tilkobling i det `preauth` har returnert, altså nøyaktig i
    vinduet mellom de to committene. Etter det er tokenet dødt, så hver vei
    får sin egen modul og sitt eget claim — et andre forsøk med samme token
    ville stoppet alt i `preauth` og bevist ingenting.

    Svaret er `modul_ikke_claimbar` (403), det SAMME claim-veien gir for et
    nødstopp: den samme hendelsen skal se lik ut uansett hvilken dør
    deploymenten står i.

    Kontroll: fjern `_modultoken_revalidert`-kallet foran én av de to
    innløsningene, så blir den veien 200/403-signatur igjen og denne rød."""
    import psycopg
    from starlette.testclient import TestClient
    from api import app as appmodul
    from api.app import lag_app

    ekte_preauth = appmodul.preauth
    bevaepnet, traff = [], []

    def _nodstopp_i_vinduet(tjeneste, conn, authorization, request_id=""):
        auth = ekte_preauth(tjeneste, conn, authorization, request_id)
        if bevaepnet and isinstance(auth, appmodul.ModulAutentisert):
            modul = bevaepnet.pop()
            traff.append(modul)
            sidekanal = psycopg.connect(MIGRATOR_DSN)
            try:
                sidekanal.execute("SET ROLE disponit_modules_admin")
                sidekanal.execute(
                    "SELECT noddeaktiver_modul(%s,'nodstopp i vinduet','test')",
                    (modul,))
                sidekanal.commit()
            finally:
                sidekanal.close()
        return auth

    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            for vei in ("artefakt", "kvittering"):
                t = f"t{_u()}"
                prefiks = f"h{_u()}."
                _kjent_type(monkeypatch, t, prefiks)
                modul, rel = _kjede(migrator, typenavn=t,
                                    artefakttype=f"kvit.o{_u()}.rapport")
                sak, logg = _lag_sak(migrator, TENANT)
                opp_id, _ = _lag_oppdrag_type(
                    migrator, TENANT, sak, logg, oppdragstype=t,
                    eiermodul=modul, handling=prefiks + "send")
                migrator.commit()
                mtk, _ignorert = _onboard_token(c, migrator, modul, rel)
                r = c.post("/v1/oppdrag/claim", json={},
                           headers={"authorization": f"Bearer {mtk}"})
                assert r.status_code == 200, r.text
                claim = r.json()

                # Herfra og til svaret er nødstoppet ladd.
                monkeypatch.setattr(appmodul, "preauth", _nodstopp_i_vinduet)
                bevaepnet.append(modul)
                if vei == "artefakt":
                    r = c.post("/v1/artefakt",
                               json={"kapabilitet_jti":
                                     claim["opplasting"]["jti"],
                                     "rapport": {"funn": 0}},
                               headers={"authorization": f"Bearer {mtk}"})
                else:
                    r = c.post("/v1/oppdrag/kvittering",
                               json={"kvittering_jti": claim["kvittering_jti"],
                                     "oppdrag_id": opp_id, "resultat": "ok"},
                               headers={"authorization": f"Bearer {mtk}"})
                monkeypatch.setattr(appmodul, "preauth", ekte_preauth)
                assert traff and traff[-1] == modul, \
                    f"nødstoppet traff aldri vinduet ({vei})"
                assert (r.status_code, r.json()["feil"]) == (
                    403, "modul_ikke_claimbar"), (vei, r.text)

                # Og ingenting ble forbrukt: kapabiliteten står ubrukt, og
                # oppdraget er fortsatt plukket — nødstoppet stoppet, det
                # forstyrret ikke.
                _sett_kontekst(migrator, TENANT)
                assert migrator.execute(
                    "SELECT status FROM oppdrag WHERE tenant=%s AND id=%s",
                    (TENANT, opp_id)).fetchone() == ("plukket",)
                migrator.rollback()
    finally:
        a.tjeneste.pool.lukk()


@pg
def test_draining_deployment_far_fortsatt_levere(migrator, miljo, monkeypatch):
    """Motstykket til testen over, og grunnen til at revalideringen leser
    STATUS og epoch — aldri livsløpet. En `draining` release skal få levere
    ferdig det den alt har claimet; det er hele poenget med en graceful
    drain, og feilkatalogen lover det (`modul_ikke_claimbar`). Et nødstopp
    skiller seg fra dreneringen ved epoch-bumpen og tilbakekallingen, ikke
    ved livsløpet — begge setter deploymenten `draining`.

    Kontroll: legg `livslop = 'claiming'` inn i
    `modultoken_fortsatt_autorisert`, så blir denne rød mens den over
    fortsatt er grønn."""
    from starlette.testclient import TestClient
    from api.app import lag_app

    t = f"t{_u()}"
    prefiks = f"h{_u()}."
    _kjent_type(monkeypatch, t, prefiks)
    modul, rel = _kjede(migrator, typenavn=t,
                        artefakttype=f"kvit.o{_u()}.rapport")
    sak, logg = _lag_sak(migrator, TENANT)
    _lag_oppdrag_type(migrator, TENANT, sak, logg, oppdragstype=t,
                      eiermodul=modul, handling=prefiks + "send")
    migrator.commit()
    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            mtk, _ignorert = _onboard_token(c, migrator, modul, rel)
            r = c.post("/v1/oppdrag/claim", json={},
                       headers={"authorization": f"Bearer {mtk}"})
            assert r.status_code == 200, r.text
            jti = r.json()["opplasting"]["jti"]

            # Dreneringen: samme livsløpsovergang nødstoppet gjør, men uten
            # epoch-bump og uten tilbakekalling.
            migrator.execute("UPDATE moduldeployment SET livslop='draining'"
                             " WHERE modul_id=%s", (modul,))
            migrator.commit()

            r = c.post("/v1/artefakt",
                       json={"kapabilitet_jti": jti, "rapport": {"funn": 0}},
                       headers={"authorization": f"Bearer {mtk}"})
            assert r.status_code == 200, r.text
    finally:
        a.tjeneste.pool.lukk()


def test_bootstrap_veien_kan_ikke_utstede_claimdyktige_tokener():
    """Port 24 (deploy-port): token-cli nekter `orders:execute`-scopes —
    claim-fullmakt kommer KUN fra onboardingen. Kontroll: fjern vaktleddet i
    `opprett()`, så blir denne rød."""
    import importlib.util
    from pathlib import Path
    sti = (Path(__file__).resolve().parents[3]
           / "deploy/staging/token-cli.py")
    spec = importlib.util.spec_from_file_location("token_cli_test", sti)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with pytest.raises(SystemExit, match="orders:execute"):
        mod.opprett(None, "p" * 40, "t", "rolle",
                    ["orders:execute:purring."])
