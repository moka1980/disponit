"""Porten for pausen: firmaets status har en virkning (eiers vedtak 27/11).

MÅLT FØR: `utlopt` (prøveutløpssveipen) og `stengt` (plattformeieren) var
ord i en kolonne. Agentene bestilte, e-posten ble hentet og svar gikk ut
som før.

  1. Et pauset firma bestiller INGENTING — sperren står før resten av
     bestillingsveien, og et aktivt firma går forbi den som før.
  2. `prove`, `aktiv` og et firma UTEN rad går; `utlopt` og `stengt` står.
  3. Banneret: ingen nedtelling før de siste sju dagene, så nedtellingen,
     så «står stille».
  4. E-posten (232): de fire kandidatdørene — Microsoft og IMAP, inn og
     ut — tar ikke med et firma som står stille.

MUTASJONER SOM DREPER DENNE: fjern sperren i `utfor_bestilling` (1),
legg `prove` i PAUSET (2), dropp NEDTELLING_DOGN-grensen (3).
"""
import secrets

from .test_api import (DSN, MIGRATOR_DSN, app, dekker, klient,  # noqa: F401
                       migrator, miljo, pg, token)
from .test_m37 import _sett_kontekst


def _t():
    return "t-pause-" + secrets.token_hex(3)


def _firma(m, t, *, dogn=30, status=None):
    _sett_kontekst(m, t)
    m.execute("SELECT firma_registrer(%s,%s,NULL,%s,'kari')",
              (t, "Pauset AS", dogn))
    if status:
        # `stengt` krever tidspunkt og aktør (firma_status_helhet).
        m.execute("UPDATE firma SET status=%s, endret=now(), endret_av='test',"
                  " stengt_ts = CASE WHEN %s='stengt' THEN now() END,"
                  " stengt_av = CASE WHEN %s='stengt' THEN 'test' END"
                  " WHERE tenant=%s", (status, status, status, t))
    m.commit()


def _kropp():
    return {"bestillingstype": "kontroll.wcag.nettsted",
            "hostname": "uverifisert.example", "kravsett": "wcag21_aa",
            "omfang": "enkeltside"}


@pg
@dekker("firma_pauset")
def test_pauset_firma_bestiller_ingenting_aktivt_gaar_forbi(migrator, app):
    from api.bestilling import utfor_bestilling
    from db.pg import koble
    pauset, aktivt = _t(), _t()
    _firma(migrator, pauset, status="utlopt")
    _firma(migrator, aktivt)
    c = koble(DSN)
    try:
        res = utfor_bestilling(app.tjeneste, c, pauset, "plan:p1", _kropp(),
                               "n-" + secrets.token_hex(6), "r1")
        assert res == ("feil", "firma_pauset"), res
        # Et aktivt firma passerer sperren og stopper der det alltid har
        # stoppet: domenet er ikke verifisert.
        res = utfor_bestilling(app.tjeneste, c, aktivt, "plan:p1", _kropp(),
                               "n-" + secrets.token_hex(6), "r2")
        assert res == ("feil", "bestilling_hostname_uverifisert"), res
    finally:
        c.close()


@pg
def test_hvilke_statuser_som_pauser(migrator, app):
    from api import firmastatus
    tenanter = {s: _t() for s in ("prove", "aktiv", "utlopt", "stengt")}
    for s, t in tenanter.items():
        _firma(migrator, t, status=None if s == "prove" else s)
    uten_rad = _t()
    stille = firmastatus.pausede(app.tjeneste,
                                 list(tenanter.values()) + [uten_rad])
    assert stille == {tenanter["utlopt"], tenanter["stengt"]}


@pg
def test_banneret_teller_ned_de_siste_sju_dagene(migrator):
    from api import firmastatus
    from db.pg import koble, sett_kontekst
    t30, t5, tut = _t(), _t(), _t()
    _firma(migrator, t30, dogn=30)
    _firma(migrator, t5, dogn=5)
    _firma(migrator, tut, status="utlopt")
    c = koble(DSN)
    try:
        def s(t):
            sett_kontekst(c, t, "test", "r")
            ut = firmastatus.status_for(c, t)
            c.rollback()
            return ut
        a = s(t30)
        assert a["status"] == "prove" and a["vis"] is False
        assert a["dogn_igjen"] in (29, 30, 31)
        b = s(t5)
        assert b["vis"] is True and b["pauset"] is False
        assert b["dogn_igjen"] in (4, 5, 6)
        u = s(tut)
        assert u["vis"] is True and u["pauset"] is True
        # Et firma uten rad (eierens egne tenanter) får ingen rad.
        assert s(_t())["vis"] is False
    finally:
        c.close()


@pg
def test_statusruten(klient, token, migrator):
    tok, _ = token(scopes=("decisions:read",))
    r = klient.get("/v1/firma/status",
                   headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    assert set(r.json()) >= {"status", "prove_utloper", "dogn_igjen",
                             "pauset", "vis", "kontakt_epost"}
    tok2, _ = token(scopes=("bestilling:opprett",))
    assert klient.get("/v1/firma/status", headers={
        "authorization": f"Bearer {tok2}"}).status_code == 403


def _kilde(m, t, lev):
    from db import kryptering
    _sett_kontekst(m, t)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(m, t)
    ct, nonce = kryptering.krypter(dek, {"x": 1}, t, key_id)
    kid = m.execute(
        "INSERT INTO epost_kilde (tenant, leverandor, postboks,"
        " auth_kryptert, nonce, key_id, scope) VALUES"
        " (%s,%s,%s,%s,%s,%s,'imap smtp') RETURNING kilde_id",
        (t, lev, f"post-{secrets.token_hex(3)}@x.example", ct, nonce,
         key_id)).fetchone()[0]
    m.commit()
    return kid


@pg
def test_epostdorene_hopper_over_firma_som_staar_stille(migrator):
    """232: ingen henting for et firma som står stille — begge
    leverandørene. (Utsendingsdørene har samme ledd; porten under leser
    migrasjonen for dem, siden et utkast i kø krever en hel melding.)"""
    stille, aktivt = _t(), _t()
    _firma(migrator, stille, status="utlopt")
    _firma(migrator, aktivt)
    for t in (stille, aktivt):
        _kilde(migrator, t, "imap")
        _kilde(migrator, t, "m365")
    migrator.execute("RESET ALL")
    migrator.execute("SET LOCAL ROLE disponit_plan_arbeider")
    sett = set()
    for dor in ("m6_imap_hentekandidater", "m6_hentekandidater"):
        sett |= {(dor, r[0]) for r in migrator.execute(
            f"SELECT tenant FROM {dor}(200)").fetchall()}
    migrator.rollback()
    for dor in ("m6_imap_hentekandidater", "m6_hentekandidater"):
        assert (dor, stille) not in sett, dor
        assert (dor, aktivt) in sett or len(
            [x for x in sett if x[0] == dor]) >= 200, dor


def test_utsendingsdorene_har_samme_ledd():
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[1] / "db/migrations"
           / "232_firma_pause_stopper_epost.sql").read_text(encoding="utf-8")
    kode = "\n".join(l for l in sql.splitlines()
                     if not l.lstrip().startswith("--"))
    assert kode.count("f.status IN ('utlopt', 'stengt')") == 4
    for dor in ("m6_hentekandidater", "m6_sendekandidater",
                "m6_imap_hentekandidater", "m6_imap_sendekandidater"):
        assert f"CREATE OR REPLACE FUNCTION {dor}(" in kode, dor
