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
    (opp_id, jti, claim_id)."""
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
    return opp, jti, cid


def _kvitter(jti, resultathash="a" * 64):
    """Eiermodulens EKTE kvittering: bruk_kvitteringskapabilitet. -> utfall."""
    from db.pg import koble, sett_kontekst
    r = koble(DSN)
    sett_kontekst(r, TEN, "sys", "r0")
    try:
        return r.execute("SELECT bruk_kvitteringskapabilitet(%s,%s)",
                         (jti, resultathash)).fetchone()[0]
    finally:
        r.commit()
        r.close()


def _kap_status(jti):
    # kvitteringskapabiliteter er m37_claimer-eid (off-limits for runtime);
    # migrator er medlem og kan SET ROLE for lesningen.
    from db.pg import koble, sett_kontekst
    m = koble(MIGRATOR_DSN)
    sett_kontekst(m, TEN, "sys", "r0")
    try:
        m.execute("SET ROLE disponit_m37_claimer")
        rad = m.execute("SELECT status, resultathash FROM kvitteringskapabiliteter"
                        " WHERE tenant=%s AND jti=%s", (TEN, jti)).fetchone()
        m.execute("RESET ROLE")
        return rad
    finally:
        m.rollback()
        m.close()


@pg
def test_port_kvittering_vs_avvis_konsistent(conn):
    """Scope-beslutningen §3 (samtidighetsport): eiermodulens EKTE kvittering og
    et menneskelig avvis gir ALDRI et motsigende utfall. En kvittering markerer
    kvitteringskapabiliteten `brukt` — den kanselleerer ALDRI oppdraget — så
    avvis ser fortsatt et levende oppdrag og flagger avklaring (409), aldri
    `avvist`; kvitteringen bevares. Vi kjører begge deterministiske rekkefølger,
    en samtidig kjøring, og til slutt at avvis vurderer den nye TERMINALE
    tilstanden korrekt når oppdraget faktisk kanselleres.

    Mutasjon som erstatter `bruk_kvitteringskapabilitet` med en direkte
    status-UPDATE dør på `_kap_status`-sjekken (brukt + resultathash); mutasjon
    som lar avvis lykkes med et levende oppdrag dør på `!= 'avvist'`."""
    import secrets
    # --- Rekkefølge A: kvittering FØRST, så avvis -----------------------
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    _opp, jti, _cid = _claimet_oppdrag_med_kvittering(uid)
    assert _kvitter(jti) == "brukt"                      # eiermodul vinner sitt løp
    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "utestaaende_oppdrag"        # avvis flagger, vinner IKKE
    assert _status(conn, uid) != "avvist"
    assert _kap_status(jti)[0] == "brukt"                # kvitteringen BEVART
    assert _kap_status(jti)[1] == "a" * 64               # resultathashen bevart

    # --- Rekkefølge B: avvis FØRST, så kvittering -----------------------
    uid2 = _oppsett(conn)
    _opp2, jti2, _c2 = _claimet_oppdrag_med_kvittering(uid2)
    res2 = _kall(conn, uid2, "avvis", bid, _macreg())
    assert res2["utfall"] == "utestaaende_oppdrag"
    assert _status(conn, uid2) != "avvist"
    assert _kvitter(jti2) == "brukt"                     # kvitteringen går fortsatt gjennom
    assert _kap_status(jti2)[0] == "brukt"

    # --- Samtidig: avvis-tråd + kvittering-tråd, join m/ timeout --------
    uid3 = _oppsett(conn)
    _opp3, jti3, _c3 = _claimet_oppdrag_med_kvittering(uid3)
    sv3 = _saksversjon(conn, uid3)
    ut = {}
    laas = threading.Lock()

    def avvis_traad():
        from db.pg import koble
        c = koble(DSN)
        try:
            r = _kall(c, uid3, "avvis", bid, _macreg(), saksversjon=sv3,
                      idem=f"kap-race-{uid3}")
            with laas:
                ut["avvis"] = r.get("utfall")
        finally:
            c.close()

    def kvitter_traad():
        with laas:
            ut["kvitter"] = _kvitter(jti3)

    tr = [threading.Thread(target=avvis_traad),
          threading.Thread(target=kvitter_traad)]
    for t in tr:
        t.start()
    for t in tr:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in tr), "en tråd henger — kan ikke bevise"
    # Uansett interleaving: avvis flagget avklaring (aldri avvist), kvittering brukt.
    assert ut["avvis"] == "utestaaende_oppdrag"
    assert ut["kvitter"] == "brukt"
    assert _status(conn, uid3) != "avvist"
    assert _kap_status(jti3)[0] == "brukt"
    # Utfallet er ALLTID (b): en kvittering kan bare gjøre oppdraget mer utført,
    # aldri `kansellert` — så avvis er korrekt blokkert i hver interleaving.
    # At avvis lykkes NÅR oppdraget faktisk er terminalt trygt (kansellert /
    # utfort vurdert korrekt) er dekket av port 2 og port 4.


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
