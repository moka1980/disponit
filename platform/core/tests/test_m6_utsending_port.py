"""Porten for M-6 (181): bakgrunnsprosessen sender svaret.

Siste ledd i eiervedtaket 10/9. Sendingen bor HER og ikke i web-API-et
fordi nøkkelen til postboksen ligger her (088) — prisen er inntil fem
minutter, og flaten sier det.

  1. Et svar i kø går ut som en `reply` i den opprinnelige tråden, og
     blir `sendt` med tidspunkt og evidens. Vi oppgir ALDRI en mottaker:
     Graph svarer avsenderen selv, så plattformen kan ikke sende til
     feil adresse.
  2. Idempotens: et svar som alt er sendt, sendes ikke igjen — verken av
     en ny runde eller av et gjenspill mot døra.
  3. En 5xx fra Graph er DRIFT: utkastet står i kø og prøves igjen. En
     401/403 er en dom: `feilet` med kode, og mennesket kan sende på
     nytt.
  4. Feilgrunnen er en KODE, aldri leverandørens tekst — den kan bære
     adresser.
  5. Bare et utkast i kø sendes: `foreslatt` og `forkastet` er aldri
     kandidater, og kryss-tenant-døra nekter tenantkontekst.

MUTASJONER SOM DREPER DENNE: send til `/sendMail` med egen mottaker i
stedet for `reply` (1); behandle 503 som en dom (3).
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_m6_inntak_port import _m365
from .test_m6_meldinger_port import _kilde_med_melding
from .test_m6_svarutkast_port import SVAR, _adm, _post
from .test_m37 import _sett_kontekst

PLAN_DSN = __import__("os").environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")


def _pa():
    from db.pg import koble
    return koble(PLAN_DSN)


def _veksler(sett=None):
    def veksler(konfig, refresh):
        if sett is not None:
            sett["refresh"] = refresh
        return {"access_token": "kortlivet-" + secrets.token_hex(4)}
    return veksler


def _i_ko(klient, migrator, token, *, tekst=SVAR):
    """Ett utkast, skrevet og sendt av et menneske."""
    kid, mid = _kilde_med_melding(migrator)
    tok = _adm(token)
    uid = _post(klient, tok, f"/v1/epost/meldinger/{mid}/svarutkast",
                {"tekst": tekst}).json()["utkast_id"]
    r = _post(klient, tok, f"/v1/epost/utkast/{uid}/send", {})
    assert r.status_code == 200 and r.json()["status"] == "sendes", r.text
    return kid, mid, uid, tok


def _tilstand(migrator, uid):
    _sett_kontekst(migrator, TENANT)
    r = migrator.execute(
        "SELECT status, sendt_ts, feilgrunn FROM epost_utkast"
        " WHERE tenant=%s AND utkast_id=%s", (TENANT, uid)).fetchone()
    ev = {h: n for h, n in migrator.execute(
        "SELECT handling, count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND kilde='m06_epost' GROUP BY handling", (TENANT,)).fetchall()}
    migrator.rollback()
    return r, ev


@pg_plan
def test_svaret_gaar_ut_som_reply_i_traaden(migrator, miljo, app, klient,
                                             token, capsys, monkeypatch):
    from plan.epost import GRAPH, send_runde
    _m365(monkeypatch)
    _, _, uid, _ = _i_ko(klient, migrator, token)
    kall = []

    def poster(access, url, kropp):
        kall.append((url, kropp, access))
    pa = _pa()
    try:
        res = send_runde(app.tjeneste, pa, poster=poster,
                         veksler=_veksler())
        mine = [r for r in res["resultater"] if r["utkast"] == str(uid)[:8]]
        assert mine and mine[0].get("sendt") is True, res
        # Reply, ikke sendMail: mottakeren er Microsofts egen.
        assert len(kall) == 1
        url, kropp, _acc = kall[0]
        assert url.startswith(GRAPH + "/me/messages/") and url.endswith("/reply")
        assert kropp == {"comment": SVAR}
        assert "toRecipients" not in kropp and "message" not in kropp
        rad, ev = _tilstand(migrator, uid)
        assert rad[0] == "sendt" and rad[1] is not None and rad[2] is None
        assert ev.get("epost.svar_sendt") == 1
        # Runde to: ikke kandidat lenger, og ingen nye kall.
        res2 = send_runde(app.tjeneste, pa, poster=poster, veksler=_veksler())
        assert not [r for r in res2["resultater"] if r["utkast"] == str(uid)[:8]]
        assert len(kall) == 1, "svaret ble sendt to ganger"
    finally:
        pa.close()
    logg = capsys.readouterr().out
    assert SVAR[:20] not in logg, "svarteksten havnet i loggen"


@pg_plan
def test_5xx_er_drift_men_401_er_en_dom(migrator, miljo, app, klient, token,
                                        monkeypatch):
    from plan.epost import GraphFeil, send_runde
    _m365(monkeypatch)
    _, _, uid, tok = _i_ko(klient, migrator, token)
    pa = _pa()
    try:
        def p503(access, url, kropp):
            raise GraphFeil(503)
        res = send_runde(app.tjeneste, pa, poster=p503, veksler=_veksler())
        mine = [r for r in res["resultater"] if r["utkast"] == str(uid)[:8]]
        assert mine[0].get("forbigaende") == "graph_503" and "feilet" not in mine[0]
        rad, _ = _tilstand(migrator, uid)
        assert rad[0] == "sendes", "en 5xx konsumerte køen"

        def p401(access, url, kropp):
            raise GraphFeil(401)
        res = send_runde(app.tjeneste, pa, poster=p401, veksler=_veksler())
        mine = [r for r in res["resultater"] if r["utkast"] == str(uid)[:8]]
        assert mine[0].get("feilet") == "sendetilgang_avvist"
        rad, ev = _tilstand(migrator, uid)
        assert rad[0] == "feilet" and rad[2] == "sendetilgang_avvist"
        assert ev.get("epost.svar_feilet") == 1
        # Mennesket kan sende på nytt.
        r = _post(klient, tok, f"/v1/epost/utkast/{uid}/send", {})
        assert r.status_code == 200 and r.json()["status"] == "sendes", r.text
        assert _tilstand(migrator, uid)[0][2] is None, "feilgrunnen ble stående"
    finally:
        pa.close()


@pg_plan
def test_bare_et_utkast_i_ko_sendes(migrator, miljo, app, klient, token,
                                    monkeypatch):
    import psycopg

    _m365(monkeypatch)

    from plan.epost import sendekandidater, send_runde
    kid, mid = _kilde_med_melding(migrator)
    tok = _adm(token)
    foreslatt = _post(klient, tok, f"/v1/epost/meldinger/{mid}/svarutkast",
                      {"tekst": SVAR}).json()["utkast_id"]
    forkastet = _post(klient, tok, f"/v1/epost/meldinger/{mid}/svarutkast",
                      {"tekst": SVAR}).json()["utkast_id"]
    _post(klient, tok, f"/v1/epost/utkast/{forkastet}/dom",
          {"status": "forkastet"})
    pa = _pa()
    try:
        ider = {str(r[1]) for r in sendekandidater(pa)}
        assert foreslatt not in ider and forkastet not in ider
        res = send_runde(app.tjeneste, pa,
                         poster=lambda *a: (_ for _ in ()).throw(
                             AssertionError("sendte noe som ikke sto i kø")),
                         veksler=_veksler())
        assert all(r["utkast"] not in (foreslatt[:8], forkastet[:8])
                   for r in res["resultater"])
        # Kryss-tenant-døra nekter tenantkontekst.
        _sett_kontekst(pa, TENANT)
        with pytest.raises(psycopg.Error):
            pa.execute("SELECT * FROM m6_sendekandidater(5)")
        pa.rollback()
    finally:
        pa.close()


@pg_plan
def test_feilgrunnen_er_en_kode(migrator, miljo, klient, token):
    import psycopg
    _, _, uid, _ = _i_ko(klient, migrator, token)
    # Døra kalles av PLANARBEIDEREN — det er den som får svaret fra
    # leverandøren og må gjøre det om til en kode. Web-API-rollen har
    # den ikke, og skal ikke ha den: forespørselsveien vet ingenting om
    # hvordan sendingen gikk. Merket er `pg_plan` og ikke `pg`
    # (CodeRabbit): uten planens DSN skal porten HOPPES OVER, ikke
    # feile på en tilkobling til None.
    pa = _pa()
    try:
        _sett_kontekst(pa, TENANT)
        with pytest.raises(psycopg.Error) as e:
            pa.execute("SELECT m6_svar_feilet(%s,%s,%s,'x')",
                       (TENANT, uid,
                        "550 5.7.1 Rejected for kari@kunde.example"))
        assert "KODE" in str(e.value), str(e.value)
        pa.rollback()
        # Positiv kontroll: en KODE går gjennom samme dør. Uten denne
        # ville porten vært grønn også om alt ble avvist. Konteksten
        # settes på nytt — `set_config(..., true)` er transaksjonslokal,
        # og rollbacken over kastet den.
        _sett_kontekst(pa, TENANT)
        pa.execute("SELECT m6_svar_feilet(%s,%s,'graph_550','x')",
                   (TENANT, uid))
        pa.rollback()
        # NULL ER HELLER INGEN KODE (182, CodeRabbit). `NULL !~ mønster`
        # er NULL i SQL, ikke sant — så 181s IF kjørte ikke grenen sin,
        # og et NULL-kall skrev `feilgrunn = NULL` på en rad merket
        # `feilet`. Flaten viser grunnen bare når den finnes, så raden
        # hadde stått som feilet uten å si hvorfor.
        _sett_kontekst(pa, TENANT)
        with pytest.raises(psycopg.Error) as e2:
            pa.execute("SELECT m6_svar_feilet(%s,%s,NULL,'x')",
                       (TENANT, uid))
        assert "KODE" in str(e2.value), str(e2.value)
        pa.rollback()
        # ...og raden bærer fortsatt ingen feilgrunn: vakten stoppet
        # FØR skrivingen, den ryddet ikke opp etterpå.
        _sett_kontekst(pa, TENANT)
        rad = pa.execute("SELECT status, feilgrunn FROM epost_utkast"
                         " WHERE tenant=%s AND utkast_id=%s",
                         (TENANT, uid)).fetchone()
        assert rad == ("sendes", None), rad
        pa.rollback()
    finally:
        pa.close()


def test_utsendingen_bruker_reply_og_aldri_en_egen_mottaker():
    """Statisk: UTSENDINGSVEIEN har ingen mottaker å ta feil av.

    `toRecipients` finnes i INNTAKET (delta-spørringen leser hvem
    meldingen gikk til), og det er riktig. Porten måler derfor
    sendefunksjonene for seg — det er der en mottaker ville vært en
    adresse plattformen kunne sende til, i stedet for å svare den som
    skrev."""
    import inspect
    from pathlib import Path

    from plan import epost as modul
    kilde = (Path(__file__).resolve().parents[1] / "plan" / "epost.py"
             ).read_text(encoding="utf-8")
    assert "/reply" in kilde
    for forbudt in ("sendMail", "/messages/send"):
        assert forbudt not in kilde, forbudt
    sendeveien = "".join(inspect.getsource(f) for f in
                         (modul.send_ett, modul.graph_post, modul.send_runde))
    for forbudt in ("toRecipients", "emailAddress", "address"):
        assert forbudt not in sendeveien, forbudt
