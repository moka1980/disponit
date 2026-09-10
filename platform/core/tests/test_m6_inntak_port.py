"""Porten for M-6 inntak (PR-C a, migrasjon 175): planarbeiderens runde
henter postboksen inn i registeret.

  1. Én runde mot en rigget Graph: meldingene lander i `epost_melding`
     som ciphertext (kropp, avsender, emne — aldri klartekst i basen),
     hasher for avsender/emne, `sist_hentet_ts` og delta-cursoren
     skrives; samme delta-side én gang til → ingen nye rader (088 port
     1, `innhenting_duplikatmelding`). Kroppen dekrypteres bare med
     tenantens DEK.
  2. Loggen bærer aldri adresse, emne eller tekst.
  3. Kill-switch → ingenting hentes; en deaktivert kilde er aldri
     kandidat; kryss-tenant-døra nekter tenantkontekst.
  4. Autentiseringen svikter → kilden `feilet`, ingen rader; en
     forbigående Graph-feil rører ingenting (kilden står `aktiv`).
  5. Innhenteren har ingen sendevei og ber bare om det dommen ga
     (statisk).

MUTASJONER SOM DREPER DENNE: fjern ON CONFLICT DO NOTHING (1); logg
emnet (2); sett `feilet` også ved 5xx (4).
"""
import json
import re
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_m37 import _sett_kontekst

PLAN_DSN = __import__("os").environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")

REFRESH = "refresh-" + secrets.token_hex(8)
ADRESSE = "kunde-" + secrets.token_hex(3) + "@nordvik.example"
EMNE = "Befaring elbillader " + secrets.token_hex(2)
KROPP = "Hei, kan dere komme på befaring neste uke? Hilsen Per"


def _pa():
    from db.pg import koble
    return koble(PLAN_DSN)


def _kilde(m, status="aktiv"):
    from db import kryptering
    _sett_kontekst(m, TENANT)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(m, TENANT)
    ct, nonce = kryptering.krypter(dek, {"refresh_token": REFRESH},
                                   TENANT, key_id)
    kid = m.execute(
        "INSERT INTO epost_kilde (tenant, leverandor, postboks,"
        " auth_kryptert, nonce, key_id, status) VALUES"
        " (%s,'m365',%s,%s,%s,%s,%s) RETURNING kilde_id",
        (TENANT, f"postboks-{secrets.token_hex(4)}@example.org",
         ct, nonce, key_id, status)).fetchone()[0]
    m.commit()
    return kid, key_id, dek


def _m365(monkeypatch):
    monkeypatch.setenv("DISPONIT_M365_CLIENT_ID", "klient-id")
    monkeypatch.setenv("DISPONIT_M365_CLIENT_SECRET", "hemmelig")
    monkeypatch.setenv("DISPONIT_M365_TENANT", "common")
    monkeypatch.delenv("DISPONIT_EPOST_INNTAK", raising=False)


def _veksler(sett):
    def veksler(konfig, refresh):
        sett["refresh"] = refresh
        return {"access_token": "kortlivet-" + secrets.token_hex(4)}
    return veksler


def _melding(i, *, vedlegg=False):
    return {"id": f"AAMk{i}-{secrets.token_hex(3)}", "conversationId": "c-1",
            "receivedDateTime": f"2026-09-10T1{i}:00:00Z",
            "from": {"emailAddress": {"address": ADRESSE, "name": "Per"}},
            "toRecipients": [{"emailAddress": {"address": "post@fjordlys.example"}}],
            "subject": f"{EMNE} {i}", "bodyPreview": KROPP[:40],
            "hasAttachments": vedlegg}


def _graf_for(sider, kall):
    """Rigget Graph: delta-sidene i rekkefølge, kropp per melding."""
    def graf(access, url, *, tekstkropp=False):
        kall.append((url, tekstkropp, access))
        if "/me/messages/" in url:
            return {"body": {"contentType": "text", "content": KROPP}}
        return sider.pop(0) if sider else {"value": [],
                                           "@odata.deltaLink": "x"}
    return graf


def _rader(m, kid):
    _sett_kontekst(m, TENANT)
    r = m.execute(
        "SELECT melding_id, leverandor_melding_id, avsender_hash, emne_hash,"
        " kropp_kryptert, nonce, key_id, har_vedlegg, epost_melding::text"
        " FROM epost_melding WHERE tenant=%s AND kilde_id=%s"
        " ORDER BY mottatt_ts", (TENANT, kid)).fetchall()
    k = m.execute("SELECT status, sist_hentet_ts, delta_token FROM epost_kilde"
                  " WHERE tenant=%s AND kilde_id=%s", (TENANT, kid)).fetchone()
    m.rollback()
    return r, k


@pg_plan
def test_runden_henter_krypterer_og_er_idempotent(migrator, miljo, app,
                                                   monkeypatch, capsys):
    from db import kryptering
    from plan.epost import GRAPH, kjor_en_runde
    _m365(monkeypatch)
    kid, key_id, dek = _kilde(migrator)
    m1, m2 = _melding(1), _melding(2, vedlegg=True)
    delta = GRAPH + "/me/mailFolders/inbox/messages/delta?$deltatoken=abc"
    sider = [{"value": [m1], "@odata.nextLink": GRAPH + "/me/x?skip=1"},
             {"value": [m2, {"id": "gone", "@removed": {"reason": "deleted"}}],
              "@odata.deltaLink": delta}]
    kall, sett = [], {}
    pa = _pa()
    try:
        res = kjor_en_runde(app.tjeneste, pa, graf=_graf_for(sider, kall),
                            veksler=_veksler(sett))
        mine = [r for r in res["resultater"] if r["kilde"] == str(kid)[:8]]
        assert mine and mine[0]["nye"] == 2 and mine[0]["sett"] == 2, res
        assert sett["refresh"] == REFRESH
        assert all(u.startswith(GRAPH + "/") for u, _t, _a in kall)
        assert any(t for _u, t, _a in kall), "kroppen ble ikke hentet som tekst"
        rader, kilde = _rader(migrator, kid)
        assert len(rader) == 2
        assert kilde[0] == "aktiv" and kilde[1] is not None \
            and kilde[2] == delta
        for r in rader:
            assert ADRESSE not in r[8] and EMNE not in r[8] \
                and KROPP[:20] not in r[8], "klartekst i basen"
            assert re.fullmatch(r"[0-9a-f]{64}", r[2]) \
                and re.fullmatch(r"[0-9a-f]{64}", r[3])
        assert [r[7] for r in rader] == [False, True]
        klar = kryptering.dekrypter(dek, bytes(rader[0][4]), bytes(rader[0][5]),
                                    TENANT, rader[0][6])
        assert klar["fra"] == ADRESSE and klar["emne"] == m1["subject"] \
            and klar["kropp"] == KROPP and klar["til"] == ["post@fjordlys.example"]
        # Samme meldinger én gang til (gjensyn på delta): ingen nye rader.
        sider2 = [{"value": [m1, m2], "@odata.deltaLink": delta}]
        res2 = kjor_en_runde(app.tjeneste, pa, graf=_graf_for(sider2, []),
                             veksler=_veksler({}))
        mine2 = [r for r in res2["resultater"] if r["kilde"] == str(kid)[:8]]
        assert mine2[0]["sett"] == 2 and mine2[0]["nye"] == 0, res2
        assert len(_rader(migrator, kid)[0]) == 2
    finally:
        pa.close()
    logg = capsys.readouterr().out
    assert "epost_inntak_runde" in logg
    for hemmelig in (ADRESSE, EMNE, KROPP[:20], REFRESH, "kortlivet-"):
        assert hemmelig not in logg, "persondata/token i loggen"


@pg_plan
def test_kill_switch_deaktivert_kilde_og_tenantkontekst(migrator, miljo, app,
                                                          monkeypatch):
    import psycopg
    from plan.epost import kandidater, kjor_en_runde
    _m365(monkeypatch)
    kid, _, _ = _kilde(migrator)
    dod, _, _ = _kilde(migrator, status="deaktivert")
    pa = _pa()
    try:
        ids = {str(r[1]) for r in kandidater(pa)}
        assert str(kid) in ids and str(dod) not in ids
        monkeypatch.setenv("DISPONIT_EPOST_INNTAK", "av")
        res = kjor_en_runde(app.tjeneste, pa, graf=lambda *a, **k: 1 / 0)
        assert res == {"av": True, "plukket": 0, "resultater": []}
        monkeypatch.delenv("DISPONIT_EPOST_INNTAK")
        _sett_kontekst(pa, TENANT)
        with pytest.raises(psycopg.Error):
            pa.execute("SELECT * FROM m6_hentekandidater(5)")
        pa.rollback()
    finally:
        pa.close()
    assert _rader(migrator, kid)[1][1] is None


@pg_plan
def test_autfeil_setter_feilet_men_forbigaende_ror_ingenting(migrator, miljo,
                                                              app, monkeypatch):
    from api.epost_kilde import KildeFeil
    from plan.epost import GraphFeil, kjor_en_runde
    _m365(monkeypatch)
    kid, _, _ = _kilde(migrator)
    pa = _pa()
    try:
        def graf_503(access, url, *, tekstkropp=False):
            raise GraphFeil(503)
        res = kjor_en_runde(app.tjeneste, pa, graf=graf_503,
                            veksler=_veksler({}))
        mine = [r for r in res["resultater"] if r["kilde"] == str(kid)[:8]]
        assert mine[0].get("forbigaende") == "graph_503" and "feilet" not in mine[0]
        rader, kilde = _rader(migrator, kid)
        assert rader == [] and kilde[0] == "aktiv" and kilde[1] is None

        def veksler_nekt(konfig, refresh):
            raise KildeFeil("tokenfornyelse ga status 400")
        res = kjor_en_runde(app.tjeneste, pa, graf=graf_503,
                            veksler=veksler_nekt)
        mine = [r for r in res["resultater"] if r["kilde"] == str(kid)[:8]]
        assert mine[0]["feilet"] == "token"
        rader, kilde = _rader(migrator, kid)
        assert rader == [] and kilde[0] == "feilet"
        # Feilet er ikke kandidat lenger.
        res = kjor_en_runde(app.tjeneste, pa, graf=graf_503,
                            veksler=_veksler({}))
        assert not [r for r in res["resultater"] if r["kilde"] == str(kid)[:8]]
    finally:
        pa.close()


def test_innhenteren_er_kun_lesende():
    from pathlib import Path
    kilde = (Path(__file__).resolve().parents[1] / "plan" / "epost.py"
             ).read_text(encoding="utf-8")
    for forbudt in ("sendMail", "Mail.Send", "Mail.ReadWrite", "smtplib",
                    "/messages/send", "PATCH", "DELETE"):
        assert forbudt not in kilde, forbudt
    assert "ON CONFLICT (tenant, kilde_id, leverandor_melding_id) DO NOTHING" \
        in kilde
    assert 'outlook.body-content-type="text"' in kilde
