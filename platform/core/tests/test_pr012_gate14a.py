"""PR-012 gate 14a: avvis på sak med utestående oppdrag/kapabilitet.

Avvis er KUN trygt når HVER relatert rad positivt er trygg (intet, kansellert
oppdrag, terminal kapabilitet). Én levende rad → `avklaring_kreves` + 409,
ALDRI `avvist`. P3: gjentatt forsøk (ulike nøkler) mot SAMME utestående
tilstand gir samme 409 uten ny versjonsøkning eller historikkrad.
"""
import threading

import pytest

from api.unntaksbehandling import Godkjenningsfeil
from .test_api import DSN, KEK, MIGRATOR_DSN, app, klient, miljo  # noqa: F401
from .test_pr012_behandle import (conn, _oppsett, _medlem, _macreg, _kall,  # noqa: F401,E501
                                  _status, _sv as _saksversjon, TEN)

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _oppdrag(uid, status="opprettet", gen=0, rep_status="aktiv"):
    """Full ekte kjede for ett oppdrag: reparasjonsoperasjon (64-hex id) →
    fase-2-TILLAT-beslutning (koblingsvakten) → KOBLET oppdrag. Returnerer
    repair_operation_id. Kansellering gjøres etterpå via en lovlig overgang."""
    import secrets
    from db.pg import koble, sett_kontekst
    m = koble(MIGRATOR_DSN)
    sett_kontekst(m, TEN, "sys", "r0")
    lid, key_id = m.execute("SELECT loggpost_id, key_id FROM unntak WHERE"
                            " tenant=%s AND id=%s", (TEN, uid)).fetchone()
    rop, ih = secrets.token_hex(32), secrets.token_hex(32)
    m.execute(
        "INSERT INTO reparasjonsoperasjoner (tenant,unntak_id,"
        "repair_operation_id,repair_generation,handler_id,handler_versjon,"
        "maalhandling,input_hash,kategori,status) VALUES (%s,%s,%s,%s,'h','v',"
        "'faktura.bokfor',%s,'over_grense',%s)",
        (TEN, uid, rop, gen, ih, rep_status))
    blid = m.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse,idempotency_key,kilde) VALUES (%s,'h','p','TILLAT',"
        "'[]'::jsonb,%s,'arbeidskapabilitet') RETURNING id",
        (TEN, rop)).fetchone()[0]
    m.execute(
        "INSERT INTO oppdrag (tenant,unntak_id,loggpost_id,repair_operation_id,"
        "oppdragstype,handling,eiermodul,status,payload_kryptert,key_id,nonce,"
        "utforelsesfrist,evidensfrist,koblingsstatus,beslutning_loggpost_id)"
        " VALUES (%s,%s,%s,%s,'reparasjon','faktura.bokfor','eier',%s,%s,%s,%s,"
        "now()+interval '1 hour',now()+interval '2 hour','KOBLET',%s)",
        (TEN, uid, lid, rop, status, b"\x00", key_id, b"\x00" * 12, blid))
    m.commit()
    m.close()
    return rop


def _kansellert_oppdrag(uid, gen=0):
    rop = _oppdrag(uid, "opprettet", gen=gen, rep_status="superseded")
    from db.pg import koble, sett_kontekst
    m = koble(MIGRATOR_DSN)
    sett_kontekst(m, TEN, "sys", "r0")
    m.execute("UPDATE oppdrag SET status='kansellert' WHERE tenant=%s AND"
              " unntak_id=%s AND repair_operation_id=%s", (TEN, uid, rop))
    m.commit()
    m.close()


def _historikk_teller(conn, uid, hendelse="avklaring_kreves"):
    from db.pg import sett_tenant
    sett_tenant(conn, TEN)
    n = conn.execute("SELECT count(*) FROM unntak_historikk WHERE tenant=%s AND"
                     " unntak_id=%s AND hendelse=%s",
                     (TEN, uid, hendelse)).fetchone()[0]
    conn.rollback()
    return n


def test_port8_ingen_oppdrag_kapabilitet_insert_uten_sakslas():
    """Port 8 (P2, statisk): ingen INSERT mot `oppdrag`/`arbeidskapabiliteter`
    skjer utenfor de GJENNOMGÅTTE, saks-låste veiene. Låsen er en `claim`
    (holdt hele transaksjonen), ikke en per-setnings FOR UPDATE — derfor en
    ALLOWLIST: en NY innsettingsvei tvinger en review av om den låser saken
    først. Det er dette som holder P2 sant over tid, også for fremtidige veier.
    """
    import re
    from pathlib import Path
    rot = Path(__file__).resolve().parents[1]     # platform/core
    mons = re.compile(
        r"INSERT\s+INTO\s+(?:public\.)?(oppdrag|arbeidskapabiliteter)\b", re.I)
    # Python: kun m37/arbeider.py — den claimer saken (claim_neste_sak låser)
    # FØR den oppretter oppdraget.
    py_tillatt = {("m37", "arbeider.py")}
    for py in rot.glob("**/*.py"):
        if py.name.startswith("test_") or "tests" in py.parts \
                or "node_modules" in py.parts:
            continue
        if mons.search(py.read_text(encoding="utf-8")):
            assert (py.parent.name, py.name) in py_tillatt, \
                f"oppdrag/kapabilitet-INSERT utenfor saks-låst vei: {py}"
    # SQL: kun de gjennomgåtte migrasjonsfunksjonene (utsted_arbeidskapabilitet),
    # som kalles etter en claim som holder saks­låsen.
    sql_tillatt = {"005_m37_behandling.sql", "007_r1_tofase.sql"}
    for sql in (rot / "db" / "migrations").glob("*.sql"):
        if mons.search(sql.read_text(encoding="utf-8")):
            assert sql.name in sql_tillatt, \
                f"oppdrag/kapabilitet-INSERT i uventet migrasjon: {sql.name}"


@pg
def test_port1_sak_uten_oppdrag_avvis_virker(conn):
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "avvist"
    assert _status(conn, uid) == "avvist"


@pg
def test_port2_kansellert_oppdrag_avvis_virker(conn):
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    _kansellert_oppdrag(uid)
    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "avvist"


@pg
def test_port3_levende_oppdrag_gir_409_og_avklaring(conn):
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    _oppdrag(uid, "opprettet")
    sv0 = _saksversjon(conn, uid)
    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "utestaaende_oppdrag"   # http-koden settes i endepunktet
    # avklaring_kreves committet, saksversjon økt, saken IKKE avvist.
    assert _status(conn, uid) != "avvist"
    assert _saksversjon(conn, uid) == sv0 + 1
    assert _historikk_teller(conn, uid) == 1


def _kapabilitet(uid, status="utstedt"):
    """Én utestående arbeidskapabilitet for saken (via migrator)."""
    import secrets
    from db.pg import koble, sett_kontekst
    m = koble(MIGRATOR_DSN)
    sett_kontekst(m, TEN, "sys", "r0")
    # Kapabilitetens utløp må ligge innenfor sakens claim_utloper (GO-vilkår V1).
    cid = secrets.token_hex(16)
    m.execute("UPDATE unntak SET claim_id=%s, claim_utloper=now()+interval"
              " '2 hour' WHERE tenant=%s AND id=%s", (cid, TEN, uid))
    # arbeidskapabiliteter eies av m37_claimer; migrator er medlem og kan SET
    # ROLE for innsettingen (som i drift går via utsted_arbeidskapabilitet).
    m.execute("SET ROLE disponit_m37_claimer")
    m.execute(
        "INSERT INTO arbeidskapabiliteter (jti,tenant,unntak_id,claim_id,"
        "claim_generation,repair_operation_id,tillatt_handling,status,utloper)"
        " VALUES (%s,%s,%s,%s,0,%s,'faktura.bokfor',%s,now()+interval '1 hour')",
        (secrets.token_hex(16), TEN, uid, cid, secrets.token_hex(32), status))
    m.execute("RESET ROLE")
    m.commit()
    m.close()


@pg
def test_port6_utestaaende_kapabilitet_uten_oppdrag_gir_409(conn):
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    _kapabilitet(uid, "utstedt")           # utestående kapabilitet, ingen oppdrag
    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "utestaaende_oppdrag"


@pg
def test_port4_utfort_oppdrag_default_deny_gir_409(conn):
    """Port 4 (fremtidssikring): whitelisten er default-DENY — KUN `kansellert`
    er trygt. Et `utfort` oppdrag (sideeffekten har alt skjedd) er nettopp det
    farligste å avvise, og gir 409. En ekte syntetisk/ukjent status kan ikke
    settes inn (CHECK-en på oppdrag.status), så egenskapen er STRUKTURELL i
    formen `status <> 'kansellert'`; `utfort` er den sterkeste observerbare
    prøven — en status som *ser* ferdig ut, men ikke er trygg å avvise."""
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    _oppdrag(uid, "utfort")
    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "utestaaende_oppdrag"   # http-koden settes i endepunktet


@pg
def test_port5_baade_kansellert_og_levende_gir_409(conn):
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    _kansellert_oppdrag(uid, gen=0)
    _oppdrag(uid, "plukket", gen=1)   # én levende blant kansellerte
    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "utestaaende_oppdrag"


@pg
def test_port9_gjentatt_ulik_noekkel_samme_409_ingen_ny_versjon_eller_historikk(conn):
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    _oppdrag(uid, "opprettet")
    r1 = _kall(conn, uid, "avvis", bid, _macreg(), idem=f"a-{uid}")
    assert r1["utfall"] == "utestaaende_oppdrag"
    sv1 = _saksversjon(conn, uid)
    # Nytt forsøk, ANNEN nøkkel, SAMME utestående tilstand → samme 409, men
    # ingen ny versjonsøkning og ingen ny historikkrad (P3).
    r2 = _kall(conn, uid, "avvis", bid, _macreg(), saksversjon=sv1,
               idem=f"b-{uid}")
    assert r2["utfall"] == "utestaaende_oppdrag"
    assert _saksversjon(conn, uid) == sv1          # ingen ny versjonsøkning
    assert _historikk_teller(conn, uid) == 1       # ingen ny historikkrad


def _browsersesjon(bid):
    """En EKTE browserøkt for TEN+bid: brukersesjon-rad m/ csrf, snapshot lik
    medlemskapets authz_version. Returnerer (sesjonscookie, csrf-token)."""
    import secrets
    from db.pg import koble, sett_kontekst
    from api import sesjon as sesjonmodul
    cookie, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    m = koble(MIGRATOR_DSN)
    sett_kontekst(m, TEN, "sys", "r0")
    ver = m.execute("SELECT authz_version FROM brukermedlemskap WHERE tenant=%s"
                    " AND bruker_id=%s", (TEN, bid)).fetchone()[0]
    m.execute(
        "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
        " authz_snapshot, csrf_hash, opprettet, siste_bruk, utloper, tilbakekalt)"
        " VALUES (%s,%s,%s,%s,%s, now(), now(), now()+interval '12 hour', false)",
        (sesjonmodul._hash(cookie), TEN, bid, ver, sesjonmodul._hash(csrf)))
    m.commit()
    m.close()
    return cookie, csrf


def _runtime():
    from db.pg import koble
    return koble(DSN)


@pg
def test_port_endepunkt_avklaring_gir_lukket_409_og_committer(klient, miljo,
                                                              monkeypatch):
    """Codex-P1: den committede 409-en må bære det LUKKEDE feilformatet
    `{"feil":"utestaaende_oppdrag","request_id":...}` — ikke den interne
    DTO-en (`utfall`/`http`), ellers ser UI-et bare en generisk 409. Ekte
    ende-til-ende gjennom endepunktet (autentisert browserøkt + CSRF): bevis
    status 409, lukket body UTEN intern lekkasje, OG at flagg/historikk faktisk
    er committet."""
    from api import sesjon as sesjonmodul
    from .test_pr012_behandle import POL, POL_HASH
    # `avvis` når gate 14a (steg 6b) FØR policyinnholdet brukes; stub oppslaget
    # så testen ikke må registrere en full skjemagyldig policy for saken.
    monkeypatch.setattr("api.unntaksbehandling.policyregister.hent_aktiv",
                        lambda conn, tenant, pid: (POL, POL_HASH))
    c = _runtime()
    try:
        uid = _oppsett(c)
    finally:
        c.close()
    bid = _medlem(None, "e2e")               # rolle godkjenner → exceptions:reject
    _oppdrag(uid, "opprettet")               # én levende oppdrag → utrygt å avvise
    c = _runtime()
    try:
        sv = _saksversjon(c, uid)
    finally:
        c.close()
    cookie, csrf = _browsersesjon(bid)

    r = klient.post(
        f"/v1/unntak/{uid}/handling",
        json={"operatorhandling": "avvis", "saksversjon": sv},
        headers={"Idempotency-Key": f"e2e-{uid}", "X-Disponit-CSRF": csrf},
        cookies={sesjonmodul.C_SESJON: cookie})

    assert r.status_code == 409, r.text
    body = r.json()
    assert body["feil"] == "utestaaende_oppdrag"
    assert "request_id" in body
    # INGEN intern DTO-lekkasje på wire:
    assert "utfall" not in body and "http" not in body and "unntak_id" not in body
    # Flagget/historikken ER committet (kan ikke rulles tilbake som vanlig feil):
    c = _runtime()
    try:
        assert _status(c, uid) != "avvist"
        assert _historikk_teller(c, uid) == 1
    finally:
        c.close()


def _claimet_oppdrag_med_kvittering(uid):
    """Plukket, owner-claimet oppdrag + EKTE utstedt kvitteringskapabilitet —
    M-37s faktiske kvitteringsvei, ikke en fake status-UPDATE. Returnerer
    (opp_id, jti, claim_id, repair_operation_id)."""
    import secrets
    from db.pg import koble, sett_kontekst
    m = koble(MIGRATOR_DSN)
    sett_kontekst(m, TEN, "sys", "r0")
    lid, key_id = m.execute("SELECT loggpost_id, key_id FROM unntak WHERE"
                            " tenant=%s AND id=%s", (TEN, uid)).fetchone()
    rop, cid = secrets.token_hex(32), secrets.token_hex(16)
    m.execute(
        "INSERT INTO reparasjonsoperasjoner (tenant,unntak_id,"
        "repair_operation_id,repair_generation,handler_id,handler_versjon,"
        "maalhandling,input_hash,kategori,status) VALUES (%s,%s,%s,0,'h','v',"
        "'faktura.bokfor',%s,'over_grense','aktiv')",
        (TEN, uid, rop, secrets.token_hex(32)))
    blid = m.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse,idempotency_key,kilde) VALUES (%s,'h','p','TILLAT',"
        "'[]'::jsonb,%s,'arbeidskapabilitet') RETURNING id",
        (TEN, rop)).fetchone()[0]
    opp = m.execute(
        "INSERT INTO oppdrag (tenant,unntak_id,loggpost_id,repair_operation_id,"
        "oppdragstype,handling,eiermodul,status,payload_kryptert,key_id,nonce,"
        "utforelsesfrist,evidensfrist,koblingsstatus,beslutning_loggpost_id,"
        "owner_claim_id,owner_generation,owner_lease_utloper) VALUES (%s,%s,%s,"
        "%s,'reparasjon','faktura.bokfor','eier:reinns','plukket',%s,%s,%s,"
        "now()+interval '1 hour',now()+interval '2 hour','KOBLET',%s,%s,0,"
        "now()+interval '1 hour') RETURNING id",
        (TEN, uid, lid, rop, b"\x00", key_id, b"\x00" * 12, blid, cid)
    ).fetchone()[0]
    m.commit()
    m.close()
    # Kvitteringskapabiliteten utstedes gjennom den EKTE funksjonen (runtime-
    # grantet), som i drift — ikke en direkte INSERT.
    r = koble(DSN)
    sett_kontekst(r, TEN, "sys", "r0")
    jti = secrets.token_hex(16)
    kap = r.execute("SELECT jti FROM utsted_kvitteringskapabilitet(%s,%s,0,%s)",
                    (opp, cid, jti)).fetchone()
    r.commit()
    r.close()
    assert kap is not None, "kvitteringskapabiliteten ble ikke utstedt"
    return opp, jti, cid, rop


def _sak_venter_utforelse(conn):
    """Sak i `venter_utførelse` (der en sak med et LEVENDE oppdrag faktisk
    står — jf. M-37) med ekte intensjon + loggpost. ny → under_behandling →
    venter_utførelse, den lovlige veien."""
    import secrets
    import types
    from api.kjerne import _skriv_unntak
    from api.minimering import bygg_handlingsintensjon
    from db.pg import sett_kontekst
    from .test_pr012_behandle import POL_HASH
    sett_kontekst(conn, TEN, "sys", "r0")
    lid = conn.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse) VALUES (%s,'h','test-mg@1.0.0/faktura.bokfor','UNNTAK',"
        "%s::jsonb) RETURNING id",
        (TEN, '[{"kode":"rolle_ok","params":{"rolle":"agent"}},'
              '{"kode":"belop_over_grense"}]')).fetchone()[0]
    snap = types.SimpleNamespace(maks_auto_forsok=3, versjon="1.0.0",
                                 innholds_hash=POL_HASH)
    ev = {"handling": "faktura.bokfor", "belop": "45000.00", "valuta": "NOK",
          "ressurs_id": "fak-1"}
    uid = _skriv_unntak(conn, TEN, lid, "faktura.bokfor", "over_grense",
                        "normal", "normal", {"handling": "faktura.bokfor"},
                        snap, bygg_handlingsintensjon(ev, "agent"))
    conn.execute("UPDATE unntak SET status='under_behandling', claim_id=%s,"
                 " claim_generation=1, claim_utloper=now()+interval '600 s'"
                 " WHERE tenant=%s AND id=%s", (secrets.token_hex(16), TEN, uid))
    conn.execute("UPDATE unntak SET status='venter_utførelse' WHERE tenant=%s"
                 " AND id=%s", (TEN, uid))
    conn.commit()
    return uid


def _signert_kvittering(opp, jti, cid, rop):
    from policy_validator import attestering
    from .test_api import NOKLER
    return attestering.signer(
        {"oppdrag_id": opp, "tenant": TEN, "kvittering_jti": jti,
         "repair_operation_id": rop, "owner_claim_id": cid,
         "owner_generation": 0, "resultat": "utfort", "ressurs_id": "fak-1",
         "verifikator": "v_fordring"}, "k1", NOKLER["v_fordring"]["k1"])


def _full_kvittering(app, kv):
    """Eiermodulens EKTE, signerte kvitteringsflyt — HELE `_ingest_kvittering`,
    ikke bare SQL-primitivet: innløser kapabiliteten, verifiserer signaturen,
    oppdaterer oppdrag (→utfort) OG sak (→løst) i én tx. -> HTTP-status."""
    from api.app import Autentisert, _ingest_kvittering
    from db.pg import koble
    r = koble(DSN)
    auth = Autentisert(TEN, "eier:reinns", set(), "eiermod")
    try:
        resp = _ingest_kvittering(app.tjeneste, r, auth, kv, "r")
        r.commit()
        return resp.status_code
    finally:
        r.close()


def _hist_hendelser(conn, uid):
    from db.pg import sett_tenant
    sett_tenant(conn, TEN)
    rader = conn.execute("SELECT hendelse FROM unntak_historikk WHERE tenant=%s"
                         " AND unntak_id=%s ORDER BY id", (TEN, uid)).fetchall()
    conn.rollback()
    return [r[0] for r in rader]


@pg
def test_port_full_kvittering_vs_avvis(conn, app, miljo):
    """Scope-beslutningen §3: eiermodulens FULLE signerte kvitteringsflyt
    (`_ingest_kvittering` → oppdrag `utfort` + sak `løst`, saksversjon bumpet)
    mot et menneskelig avvis. Bevis fra COMMITTET DB-tilstand at ingen
    rekkefølge — seriell eller samtidig — gir et motsigende utfall, og at
    saks­låsen faktisk serialiserer.

    Mutasjonen «flytt 14a-kontrollen (steg 6b) før saksversjon-/lås-steget»
    DØR på rekkefølge A: der committer den fulle kvitteringen først og bumper
    saksversjonen, så et avvis med operatørens STALE versjon MÅ få
    `saksversjon_utdatert` (steg 4, under låsen) — ikke `utestaaende_oppdrag`
    fra en 6b som kjørte for tidlig."""
    bid = _medlem(conn, "op1")

    # --- Rekkefølge A: full kvittering FØRST (sak→løst, sv bump) ---------
    uid = _sak_venter_utforelse(conn)
    opp, jti, cid, rop = _claimet_oppdrag_med_kvittering(uid)
    sv_stale = _saksversjon(conn, uid)                   # det operatøren SÅ
    assert _full_kvittering(app, _signert_kvittering(opp, jti, cid, rop)) == 200
    assert _status(conn, uid) == "løst"                  # oppdrag utfort, sak løst
    with pytest.raises(Godkjenningsfeil) as ei:          # stale avvis under låsen
        _kall(conn, uid, "avvis", bid, _macreg(), saksversjon=sv_stale)
    conn.rollback()
    assert ei.value.kode == "saksversjon_utdatert"       # dreper 6b-før-lås-mutasjonen
    assert _status(conn, uid) == "løst"                  # ALDRI avvist
    assert "avklaring_kreves" not in _hist_hendelser(conn, uid)

    # --- Rekkefølge B: avvis FØRST (409 avklaring), så full kvittering ---
    uid2 = _sak_venter_utforelse(conn)
    opp2, jti2, cid2, rop2 = _claimet_oppdrag_med_kvittering(uid2)
    res2 = _kall(conn, uid2, "avvis", bid, _macreg())
    assert res2["utfall"] == "utestaaende_oppdrag"       # aldri avvist
    assert _status(conn, uid2) != "avvist"
    assert _full_kvittering(app, _signert_kvittering(opp2, jti2, cid2, rop2)) == 200
    assert _status(conn, uid2) == "løst"                 # kvitteringen bevart+fullført
    h2 = _hist_hendelser(conn, uid2)
    assert "avklaring_kreves" in h2 and "kvittering" in h2

    # --- Samtidig, deterministisk vindu rundt saks­låsen -----------------
    # En port-conn holder `unntak FOR UPDATE`, så BÅDE avvis-tråden (som tar
    # samme lås) og kvittering-tråden (hvis `UPDATE unntak` tar radlåsen) står
    # i kø; når porten slippes, serialiserer PostgreSQL dem. Uansett vinner:
    # saken ender `løst`, aldri `avvist`, uten motsigende historikk.
    from db.pg import koble, sett_kontekst
    uid3 = _sak_venter_utforelse(conn)
    opp3, jti3, cid3, rop3 = _claimet_oppdrag_med_kvittering(uid3)
    sv3 = _saksversjon(conn, uid3)
    port = koble(DSN)
    sett_kontekst(port, TEN, "sys", "r0")
    port.execute("SELECT 1 FROM unntak WHERE tenant=%s AND id=%s FOR UPDATE",
                 (TEN, uid3))                            # HOLDER saks­låsen
    ut = {}
    laas = threading.Lock()
    klar = threading.Barrier(2, timeout=30)

    def avvis_traad():
        c = koble(DSN)
        try:
            klar.wait()
            try:
                r = _kall(c, uid3, "avvis", bid, _macreg(), saksversjon=sv3,
                          idem=f"full-race-{uid3}")
                with laas:
                    ut["avvis"] = r.get("utfall")
            except Godkjenningsfeil as g:
                with laas:
                    ut["avvis"] = f"feil:{g.kode}"
        finally:
            c.close()

    def kvitter_traad():
        klar.wait()
        s = _full_kvittering(app, _signert_kvittering(opp3, jti3, cid3, rop3))
        with laas:
            ut["kvitter"] = s

    tr = [threading.Thread(target=avvis_traad),
          threading.Thread(target=kvitter_traad)]
    for t in tr:
        t.start()
    import time as _t
    _t.sleep(1.0)                                        # la begge blokkere på porten
    port.rollback()                                      # slipp saks­låsen
    port.close()
    for t in tr:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in tr), "en tråd henger — kan ikke bevise"
    assert ut["kvitter"] == 200                          # kvitteringen fullførte
    # Uansett hvem som vant den serialiserte kritiske seksjonen:
    #  - avvis flagget avklaring (så et levende oppdrag) ELLER så den bumpede
    #    versjonen (`saksversjon_utdatert`) — men vant ALDRI avvisningen;
    #  - saken ender `løst`, ALDRI `avvist`, og kvitteringen er bevart.
    # En eventuell `avklaring_kreves` er en korrekt observasjon i det øyeblikket
    # avvis kjørte (oppdraget var da levende), ikke en motsigelse — den endelige
    # tilstanden er `løst`, aldri «ikke utført».
    assert ut["avvis"] in ("utestaaende_oppdrag", "feil:saksversjon_utdatert")
    assert _status(conn, uid3) == "løst"                 # ALDRI avvist; ender løst
    assert "kvittering" in _hist_hendelser(conn, uid3)   # kvitteringen bevart


@pg
def test_port_leseapi_skjuler_avvis_ved_utestaaende(klient, miljo, monkeypatch):
    """Scope-beslutningen §1/§3: lese-API-et må IKKE tilby `avvis` når saken
    har et levende oppdrag — `tillatte_handlinger[]` skjuler den og detalj-DTO-en
    bærer den lukkede `avvis_utilgjengelig: utestaaende_oppdrag`, utledet
    server-side fra SAMME autoritative funksjon som POST-vakten. Mutasjon som
    fjerner sjekken (avvis dukker opp igjen ved utestående) dør her."""
    from api import sesjon as sesjonmodul
    from .test_pr012_behandle import POL, POL_HASH
    monkeypatch.setattr("api.policyregister.hent_aktiv",
                        lambda conn, tenant, pid: (POL, POL_HASH))
    c = _runtime()
    try:
        uid = _oppsett(c)
    finally:
        c.close()
    bid = _medlem(None, "les14a")                 # godkjenner → exceptions:read
    cookie, _csrf = _browsersesjon(bid)

    # Uten oppdrag: avvis tilbys, ingen avvis-årsak.
    r0 = klient.get(f"/v1/unntak/{uid}", cookies={sesjonmodul.C_SESJON: cookie})
    assert r0.status_code == 200, r0.text
    assert "avvis" in r0.json()["tillatte_handlinger"]
    assert "avvis_utilgjengelig" not in r0.json()

    # Med ett levende oppdrag: avvis SKJULT + lukket årsak, eskaler fortsatt lovlig.
    _oppdrag(uid, "opprettet")
    r1 = klient.get(f"/v1/unntak/{uid}", cookies={sesjonmodul.C_SESJON: cookie})
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert "avvis" not in body["tillatte_handlinger"], \
        "lese-API-et inviterer til en avvis serverkontrakten vet er utilgjengelig"
    assert body["avvis_utilgjengelig"] == "utestaaende_oppdrag"
    assert "eskaler" in body["tillatte_handlinger"]
