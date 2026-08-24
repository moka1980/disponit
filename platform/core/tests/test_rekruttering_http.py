"""M-57s leseflate + signeringsvei over HTTP — utførelsesarmens første ben.

Kjøres ende-til-ende gjennom `klient`: ekte browserøkt, ekte runtime-
rolle, ekte pool — og signeringen går gjennom DEN EKTE 056-kjeden
(opprett → signer), så «svaret sa 201» og «signaturen står i basen» kan
skilles (test_varsel_http-formen).
"""
import hashlib
import secrets
import uuid

import pytest

from .test_api import DSN, MIGRATOR_DSN, app, klient, miljo  # noqa: F401

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
TEN = "t-rhttp-" + secrets.token_hex(3)


def _migrator():
    from db.pg import koble, sett_kontekst
    m = koble(MIGRATOR_DSN)
    sett_kontekst(m, TEN, "sys", "r0")
    return m


def _bruker(navn: str, roller) -> str:
    m = _migrator()
    try:
        bid = m.execute(
            "INSERT INTO brukeridentitet (issuer, sub) VALUES (%s,%s)"
            " ON CONFLICT (issuer,sub) DO UPDATE SET sub=EXCLUDED.sub"
            " RETURNING bruker_id",
            ("https://idp.example", f"{TEN}-{navn}")).fetchone()[0]
        m.execute(
            "INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
            " VALUES (%s,%s,%s)"
            " ON CONFLICT (tenant,bruker_id) DO UPDATE SET"
            " roller=EXCLUDED.roller, aktiv=true", (TEN, bid, list(roller)))
        m.commit()
        return bid
    finally:
        m.close()


def _browsersesjon(bid: str):
    from api import sesjon as sesjonmodul
    cookie, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    m = _migrator()
    try:
        ver = m.execute(
            "SELECT authz_version FROM brukermedlemskap WHERE tenant=%s"
            " AND bruker_id=%s", (TEN, bid)).fetchone()[0]
        m.execute(
            "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
            " authz_snapshot, csrf_hash, opprettet, siste_bruk, utloper,"
            " tilbakekalt) VALUES (%s,%s,%s,%s,%s, now(), now(),"
            " now()+interval '12 hour', false)",
            (sesjonmodul._hash(cookie), TEN, bid, ver,
             sesjonmodul._hash(csrf)))
        m.commit()
        return cookie, csrf
    finally:
        m.close()


def _get(klient, cookie, sti):
    from api import sesjon as sesjonmodul
    return klient.get(sti, cookies={sesjonmodul.C_SESJON: cookie})


def _post(klient, cookie, csrf, sti, kropp, idem=None):
    from api import sesjon as sesjonmodul
    return klient.post(sti, json=kropp,
                       cookies={sesjonmodul.C_SESJON: cookie},
                       headers={"X-Disponit-CSRF": csrf,
                                "Idempotency-Key":
                                    idem or secrets.token_urlsafe(24)})


def _seed_prosess():
    """Hele den ekte kjeden i miniatyr: loggpost → utfort
    evalueringsoppdrag → prosess → to kandidatartefakter → innstilt
    liste (056-veien). -> (prosess_id, liste_id, innhold_hash)."""
    from db import kryptering
    m = _migrator()
    try:
        logg = m.execute(
            "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
            " policy_id, beslutning, begrunnelse, idempotency_key)"
            " VALUES (%s,'test','api_token','ih','p@1.0.0/x.y','TILLAT',"
            " '[]',%s) RETURNING id", (TEN, secrets.token_hex(8))
        ).fetchone()[0]
        key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(m, TEN)
        ct, nonce = kryptering.krypter(dek, {"demo": True}, TEN, key_id)
        oid = m.execute(
            "INSERT INTO oppdrag (opprinnelse, tenant,"
            " beslutning_loggpost_id, oppdragstype, handling, eiermodul,"
            " payload_kryptert, key_id, nonce, utforelsesfrist,"
            " evidensfrist, koblingsstatus)"
            " VALUES ('beslutning',%s,%s,'rekruttering.evaluering',"
            " 'rekruttering.evaluering.bunt','m57_ats',%s,%s,%s,"
            " now()+interval '4 hour', now()+interval '1 day','KOBLET')"
            " RETURNING id", (TEN, logg, ct, key_id, nonce)).fetchone()[0]
        # Fødselsporten (057): prosessen fødes MENS kjøringen står på —
        # én lovlig tilstand (plukket). Utfort settes ETTER lagrene, før
        # listen (promoteringsvakten krever fullført evaluering).
        m.execute("UPDATE oppdrag SET status='plukket' WHERE tenant=%s"
                  " AND id=%s", (TEN, oid))
        m.commit()

        from db.pg import koble
        rt = koble(DSN)
        try:
            from db.pg import sett_kontekst
            sett_kontekst(rt, TEN, "test", "r1")
            pid = rt.execute(
                "SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                (TEN, oid)).fetchone()[0]
            for n, (poeng_ok, funn) in enumerate((
                    (True, []),
                    (False, [{"kategori": "krav_ikke_dokumentert",
                              "kilde": {"start": 0, "slutt": 4,
                                        "sitat": "Uten"}}]))):
                kid = uuid.uuid4()
                rt.execute(
                    "INSERT INTO kandidat_evalueringsartefakt (tenant,"
                    " prosess_id, kandidat_id, artefakt, innhold_sha256)"
                    " VALUES (%s,%s,%s,%s,%s)",
                    (TEN, pid, kid,
                     __import__("json").dumps({
                         "oppfylt": {"drift": poeng_ok, "sky": True},
                         "vekter": {"drift": 3, "sky": 2},
                         "funn": funn,
                         "intervjusporsmal": ["Fortell om drift."]}),
                     hashlib.sha256(str(kid).encode()).hexdigest()))
            rt.commit()
            from db.pg import sett_kontekst as _sk
            _sk(m, TEN, "sys", "r3")   # konteksten døde i forrige commit
            n = m.execute("UPDATE oppdrag SET status='utfort'"
                          " WHERE tenant=%s AND id=%s", (TEN, oid)).rowcount
            assert n == 1, "utfort-overgangen traff ikke raden"
            m.commit()
            # Konteksten er transaksjonslokal og døde i commiten over.
            sett_kontekst(rt, TEN, "test", "r2")
            innhold_hash = hashlib.sha256(b"demoliste-v1").hexdigest()
            lid = rt.execute(
                "SELECT opprett_utsendingsliste(%s,%s,NULL,%s,"
                " 'invitasjon','invitasjon-v1',%s,2)",
                (TEN, uuid.uuid4(), oid, innhold_hash)).fetchone()[0]
            rt.commit()
            return str(pid), str(lid), innhold_hash
        finally:
            rt.close()
    finally:
        m.close()


def _ny_versjon(liste_id: str):
    """En NY versjon i samme utkast_serie: serien redigeres videre, og
    `liste_id` blir spissens forelder. -> (barn_liste_id, barn_hash)."""
    from db.pg import koble, sett_kontekst
    rt = koble(DSN)
    try:
        sett_kontekst(rt, TEN, "test", "r-versjon")
        serie, oid = rt.execute(
            "SELECT utkast_serie, oppdrag_id FROM utsendingsliste"
            " WHERE tenant=%s AND liste_id=%s",
            (TEN, liste_id)).fetchone()
        barn_hash = hashlib.sha256(
            f"demoliste-v2:{liste_id}".encode()).hexdigest()
        barn = rt.execute(
            "SELECT opprett_utsendingsliste(%s,%s,%s,%s,"
            " 'invitasjon','invitasjon-v1',%s,3)",
            (TEN, serie, liste_id, oid, barn_hash)).fetchone()[0]
        rt.commit()
        return str(barn), barn_hash
    finally:
        rt.close()


@pg
def test_prosesslisten_baerer_flatens_kontrakt(klient):
    pid, lid, ih = _seed_prosess()
    bid = _bruker("leser", ["leser"])
    cookie, _ = _browsersesjon(bid)
    r = _get(klient, cookie, "/v1/rekruttering/prosesser")
    assert r.status_code == 200, r.text
    pr = [p for p in r.json()["prosesser"] if p["prosess_id"] == pid]
    assert len(pr) == 1
    p = pr[0]
    assert p["vekter"] == {"drift": 3, "sky": 2}
    assert p["vekter_kilde"] == "evalueringsartefakt"
    assert p["blinding_av"] is False
    assert {k["status"] for k in p["kandidater"]} == \
        {"anbefalt", "innstilt_avslag"}
    assert all(set(k) >= {"kandidat_id", "oppfylt", "funn",
                          "intervjusporsmal"} for k in p["kandidater"])
    liste = [l for l in p["lister"] if l["liste_id"] == lid]
    assert liste and liste[0]["innhold_hash"] == ih \
        and liste[0]["antall"] == 2 and liste[0]["signert"] is False


def _artefakt(prosess_id: str, oppfylt: dict, funn=()) -> str:
    """En ekstra kandidatartefakt i en eksisterende prosess, på den
    KANONISKE formen: ingen `status`-nøkkel — den finnes ikke i
    `evaluering.evaluer_kandidat`s returverdi. -> kandidat_id."""
    import json as _json
    from db.pg import koble, sett_kontekst
    rt = koble(DSN)
    try:
        sett_kontekst(rt, TEN, "test", "r-art")
        kid = uuid.uuid4()
        rt.execute(
            "INSERT INTO kandidat_evalueringsartefakt (tenant, prosess_id,"
            " kandidat_id, artefakt, innhold_sha256) VALUES (%s,%s,%s,%s,%s)",
            (TEN, prosess_id, kid,
             _json.dumps({"oppfylt": oppfylt, "vekter": {"drift": 3,
                                                         "sky": 2},
                          "funn": list(funn), "intervjusporsmal": []}),
             hashlib.sha256(str(kid).encode()).hexdigest()))
        rt.commit()
        return str(kid)
    finally:
        rt.close()


@pg
def test_anbefalingen_krever_oppfylte_krav_ikke_bare_tomme_funn(klient):
    """Codex P1: trafikklyset er reservens verk for ENHVER kanonisk
    artefakt (evalueringen returnerer ingen `status`), og funn og
    kravoppfyllelse er uavhengige felt. En komplett evaluering med tom
    `funn` og bare `false` i `oppfylt` — kandidaten oppfyller ikke ETT krav
    — fikk «Anbefalt» utelukkende fordi ingen risiko var notert.

    MUTASJONEN SOM DREPER DENNE: sett betingelsen tilbake til
    `"vurderes" if funn else "anbefalt"`.
    """
    pid, _lid, _ih = _seed_prosess()
    null_krav = _artefakt(pid, {"drift": False, "sky": False})
    delvis = _artefakt(pid, {"drift": True, "sky": False})
    alle = _artefakt(pid, {"drift": True, "sky": True})
    bid = _bruker("lys-leser", ["leser"])
    cookie, _ = _browsersesjon(bid)
    r = _get(klient, cookie, "/v1/rekruttering/prosesser")
    assert r.status_code == 200, r.text
    p = [x for x in r.json()["prosesser"] if x["prosess_id"] == pid][0]
    lys = {k["kandidat_id"]: k["status"] for k in p["kandidater"]}
    assert lys[null_krav] == "vurderes", \
        "null oppfylte krav og tomme funn ble til grønt lys"
    assert lys[delvis] == "vurderes", \
        "delvis oppfyllelse er ikke en anbefaling"
    # …og porten stenger ikke for den kandidaten kravene FAKTISK bærer.
    assert lys[alle] == "anbefalt"


@pg
def test_signering_gaar_gjennom_056_kjeden(klient):
    _pid, lid, ih = _seed_prosess()
    bid = _bruker("sjef", ["admin"])
    cookie, csrf = _browsersesjon(bid)
    r = _post(klient, cookie, csrf,
              f"/v1/rekruttering/lister/{lid}/signer",
              {"innhold_hash": ih})
    assert r.status_code == 201, r.text
    assert r.json()["innhold_hash"] == ih
    # …og sannheten står i BASEN, med øktens bruker som signatar.
    m = _migrator()
    try:
        rad = m.execute(
            "SELECT signatar FROM utsendingssignatur WHERE tenant=%s"
            " AND liste_id=%s", (TEN, lid)).fetchone()
        assert rad and rad[0] == bid
        m.rollback()
    finally:
        m.close()
    # Serien er signert: en NY signatur (annen nøkkel) er konflikt.
    r2 = _post(klient, cookie, csrf,
               f"/v1/rekruttering/lister/{lid}/signer",
               {"innhold_hash": ih})
    assert r2.status_code == 409 and r2.json()["feil"] == \
        "serien_alt_signert"


@pg
def test_signering_replay_samme_idempotency_key(klient):
    """SP-2-kontrakten flaten LOVER brukeren, målt over HTTP (Cursor P2).

    `ui.rekruttering.usikkert_utfall` ber brukeren prøve igjen etter et
    tvetydig svar med løftet «et nytt forsøk gjentar den SAMME
    operasjonen og lager ingen ny» — og `api.js` sender da samme
    `Idempotency-Key`. `signer_utsendingsliste` (056) no-op-er på
    identisk nøkkel + liste + signatar, men INGEN test viste at HTTP-
    laget bærer løftet helt fram: at retryet blir 201 og ikke 409
    `serien_alt_signert`, som er det svaret en ANNEN nøkkel får.
    """
    _pid, lid, ih = _seed_prosess()
    bid = _bruker("sjef-replay", ["admin"])
    cookie, csrf = _browsersesjon(bid)
    nokkel = secrets.token_urlsafe(24)
    svar = [_post(klient, cookie, csrf,
                  f"/v1/rekruttering/lister/{lid}/signer",
                  {"innhold_hash": ih}, idem=nokkel) for _ in range(2)]
    assert [r.status_code for r in svar] == [201, 201], \
        [r.text for r in svar]
    assert svar[1].json()["innhold_hash"] == ih
    # ÉN signatur i basen, med uendret signatar — replayet la ingen rad.
    m = _migrator()
    try:
        rader = m.execute(
            "SELECT signatar, operasjonsnokkel FROM utsendingssignatur"
            " WHERE tenant=%s AND liste_id=%s", (TEN, lid)).fetchall()
        assert len(rader) == 1, rader
        assert rader[0][0] == bid and rader[0][1] == nokkel
        m.rollback()
    finally:
        m.close()


@pg
def test_replay_overlever_at_serien_ble_redigert_videre(klient):
    """Codex P1 (runde 3): spissporten er en TILSTANDSPORT — «kan denne
    raden signeres nå?» — og for et replay er det spørsmålet alt besvart.
    Committer signaturen mens svaret går tapt, og serien redigeres videre
    før klienten prøver igjen med SAMME nøkkel, svarte porten
    `liste_utdatert` (409) på en operasjon som var ferdig. Flaten leser
    409 som definitivt avslag, og brukeren har da ingen måte å vite om den
    irreversible autorisasjonen gikk igjennom — stikk i strid med løftet
    i `ui.rekruttering.usikkert_utfall`.

    MUTASJONEN SOM DREPER DENNE: fjern `not replay and` foran
    spissporten.
    """
    _pid, rot, rot_hash = _seed_prosess()
    bid = _bruker("sjef-replay-spiss", ["admin"])
    cookie, csrf = _browsersesjon(bid)
    nokkel = secrets.token_urlsafe(24)
    forste = _post(klient, cookie, csrf,
                   f"/v1/rekruttering/lister/{rot}/signer",
                   {"innhold_hash": rot_hash}, idem=nokkel)
    assert forste.status_code == 201, forste.text
    # Svaret gikk tapt for klienten; serien redigeres videre i mellomtiden.
    _barn, _barn_hash = _ny_versjon(rot)
    igjen = _post(klient, cookie, csrf,
                  f"/v1/rekruttering/lister/{rot}/signer",
                  {"innhold_hash": rot_hash}, idem=nokkel)
    assert igjen.status_code == 201, igjen.text
    assert igjen.json()["innhold_hash"] == rot_hash
    # …og replayet la ingen ny rad: 056 no-op-er, porten bare slapp fram.
    m = _migrator()
    try:
        assert m.execute(
            "SELECT count(*) FROM utsendingssignatur WHERE tenant=%s"
            " AND liste_id=%s", (TEN, rot)).fetchone()[0] == 1
        m.rollback()
    finally:
        m.close()
    # Porten står for alle ANDRE: en fersk nøkkel på den utdaterte raden
    # er fortsatt 409 — replayet er et unntak for den ferdige operasjonen,
    # ikke en åpning av spissporten.
    fersk = _post(klient, cookie, csrf,
                  f"/v1/rekruttering/lister/{rot}/signer",
                  {"innhold_hash": rot_hash})
    assert fersk.status_code == 409, fersk.text
    assert fersk.json()["feil"] == "liste_utdatert"


@pg
def test_signering_krever_hashen_dialogen_viste(klient):
    _pid, lid, _ih = _seed_prosess()
    bid = _bruker("sjef2", ["admin"])
    cookie, csrf = _browsersesjon(bid)
    r = _post(klient, cookie, csrf,
              f"/v1/rekruttering/lister/{lid}/signer",
              {"innhold_hash": "f" * 64})
    assert r.status_code == 409 and r.json()["feil"] == "innhold_endret"
    r2 = _post(klient, cookie, csrf,
               f"/v1/rekruttering/lister/{uuid.uuid4()}/signer",
               {"innhold_hash": "f" * 64})
    assert r2.status_code == 404


@pg
def test_signering_av_utdatert_listeversjon_avvises(klient):
    """Cursor P1: `_lister` viser bare serie-spissen, men et `liste_id`
    overlever redigeringen (åpen dialog, parallell editor, direkte kall).
    Hashen fanger det ikke — den GAMLE radens hash er uendret — og serien
    har nøyaktig én signatur-slot: rot-signaturen ville låst feil innhold
    OG gjort det nye utkastet usignerbart for alltid."""
    _pid, rot, rot_hash = _seed_prosess()
    barn, barn_hash = _ny_versjon(rot)
    bid = _bruker("sjef-serie", ["admin"])
    cookie, csrf = _browsersesjon(bid)
    r = _post(klient, cookie, csrf,
              f"/v1/rekruttering/lister/{rot}/signer",
              {"innhold_hash": rot_hash})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "liste_utdatert"
    # …og avvisningen SKREV INGENTING: signatur-sloten står ubrukt.
    m = _migrator()
    try:
        assert m.execute(
            "SELECT count(*) FROM utsendingssignatur WHERE tenant=%s"
            " AND liste_id IN (%s,%s)", (TEN, rot, barn)).fetchone()[0] == 0
        m.rollback()
    finally:
        m.close()
    # Spissen selv signeres som før.
    r2 = _post(klient, cookie, csrf,
               f"/v1/rekruttering/lister/{barn}/signer",
               {"innhold_hash": barn_hash})
    assert r2.status_code == 201, r2.text
    m = _migrator()
    try:
        rad = m.execute(
            "SELECT signatar FROM utsendingssignatur WHERE tenant=%s"
            " AND liste_id=%s", (TEN, barn)).fetchone()
        assert rad and rad[0] == bid
        m.rollback()
    finally:
        m.close()


@pg
def test_signering_avviser_medlemskap_som_faller_bort_underveis(klient):
    """Cursor P2: `signer_endepunkt` mapper `InsufficientPrivilege` fra
    056s medlemskapsport til 403 `signatar_uten_medlemskap`, men HTTP-laget
    hadde ingen negativ test på den armen — bare scope-gatingen. DB-porten
    er dekket i `test_m57_utsending`; det som ikke var dekket, er at
    PRODUKSJONSVEIEN oversetter dommen i stedet for å la den escape som en
    500.

    Deaktivering FØR forespørselen når aldri hit: `sesjon.py` krever
    `aktiv` medlemskap og svarer 401. Armen kan bare nås der Cursor peker
    — mellom autentiseringen og signeringen — så kappløpet er testens
    eneste vei inn, og det gjøres deterministisk med en lås i stedet for
    med tid: deaktiveringen står UCOMMITTET, så enhver ren leser (også
    autentiseringen) ser fortsatt et aktivt medlemskap, mens
    `laas_godkjenner`s `FOR UPDATE` stiller seg i kø bak den. Vi commiter
    først når forespørselen beviselig står i køen.

    MUTASJONEN SOM DREPER DENNE: fjern `except
    psycopg.errors.InsufficientPrivilege`-armen i `signer_endepunkt` —
    dommen blir da en 500.
    """
    import threading

    _pid, lid, ih = _seed_prosess()
    bid = _bruker("sjef-faller-bort", ["admin"])
    cookie, csrf = _browsersesjon(bid)
    svar: dict = {}
    rev = _migrator()
    try:
        rev.execute("UPDATE brukermedlemskap SET aktiv=false"
                    " WHERE tenant=%s AND bruker_id=%s", (TEN, bid))

        def post():
            svar["r"] = _post(klient, cookie, csrf,
                              f"/v1/rekruttering/lister/{lid}/signer",
                              {"innhold_hash": ih})

        tp = threading.Thread(target=post)
        tp.start()
        tp.join(timeout=2)
        assert tp.is_alive(), (
            "forespørselen stoppet FØR medlemskapslåsen — da måler testen"
            " noe annet enn armen den er skrevet for")
        rev.commit()                  # deaktiveringen lander, låsen slippes
        tp.join(timeout=10)
        assert not tp.is_alive(), "forespørselen kom aldri ut av låskøen"
    finally:
        rev.rollback()
        rev.close()
    r = svar["r"]
    assert r.status_code == 403, r.text
    assert r.json()["feil"] == "signatar_uten_medlemskap"
    # …og avvisningen SKREV INGENTING: signatur-sloten står ubrukt.
    m = _migrator()
    try:
        assert m.execute(
            "SELECT count(*) FROM utsendingssignatur WHERE tenant=%s"
            " AND liste_id=%s", (TEN, lid)).fetchone()[0] == 0
        m.rollback()
    finally:
        m.close()


@pg
def test_signaturstatusen_folger_serien_ikke_raden(klient):
    """Codex P2: signatur-sloten er `en_signert_versjon_per_serie` — UNIK
    på (tenant, utkast_serie), altså én per SERIE. `opprett_utsendingsliste`
    hindrer ikke et barn etter at forelderen ble signert, og barnet er da
    spissen `_lister` returnerer. Med et eksakt liste-treff meldte raden
    `signert: false`: flaten viste en handlingsklar knapp på en versjon som
    ALDRI kan signeres.

    MUTASJONEN SOM DREPER DENNE: sett joinen i `_lister` tilbake til
    `s.liste_id = l.liste_id`.
    """
    _pid, rot, rot_hash = _seed_prosess()
    sjef = _bruker("sjef-serie2", ["admin"])
    cookie, csrf = _browsersesjon(sjef)
    r = _post(klient, cookie, csrf,
              f"/v1/rekruttering/lister/{rot}/signer",
              {"innhold_hash": rot_hash})
    assert r.status_code == 201, r.text
    # Serien redigeres videre ETTER signaturen — 056 tillater det.
    barn, barn_hash = _ny_versjon(rot)
    leser = _bruker("serie-leser", ["leser"])
    lc, _ = _browsersesjon(leser)
    rg = _get(klient, lc, "/v1/rekruttering/prosesser")
    assert rg.status_code == 200, rg.text
    spisser = [l for p in rg.json()["prosesser"] for l in p["lister"]]
    spiss = [l for l in spisser if l["liste_id"] == barn]
    assert spiss, "barnet er spissen, men kom ikke ut av leseflaten"
    assert spiss[0]["signert"] is True, \
        "spissen meldte usignert på en serie hvis signatur-slot er brukt"
    assert not [l for l in spisser if l["liste_id"] == rot], \
        "forelderen har et barn og skal ikke være spiss"
    # …og påstanden bak statusen holder: barnet KAN ikke signeres.
    r2 = _post(klient, cookie, csrf,
               f"/v1/rekruttering/lister/{barn}/signer",
               {"innhold_hash": barn_hash})
    assert r2.status_code == 409, r2.text
    assert r2.json()["feil"] == "serien_alt_signert"


@pg
def test_scopene_gater_som_flaten_lover(klient):
    _pid, lid, ih = _seed_prosess()
    leser = _bruker("bare-leser", ["leser"])
    cookie, csrf = _browsersesjon(leser)
    r = _post(klient, cookie, csrf,
              f"/v1/rekruttering/lister/{lid}/signer",
              {"innhold_hash": ih})
    assert r.status_code in (401, 403), r.text
    # …og blinding-avskruing er en KODET avvisning til #159 (aldri
    # stille suksess) — også for den som HAR mutasjonsscopet.
    sjef = _bruker("sjef3", ["admin"])
    c2, cs2 = _browsersesjon(sjef)
    r2 = _post(klient, c2, cs2,
               "/v1/rekruttering/prosesser/x/blinding",
               {"av": True, "begrunnelse": "test"})
    assert r2.status_code == 409 and r2.json()["feil"] == \
        "blinding_avskruing_krever_159"


@pg
def test_rls_skiller_tenantene_ogsaa_her(klient):
    _seed_prosess()
    fremmed = "t-annen-" + secrets.token_hex(3)
    from db.pg import koble, sett_kontekst
    m = koble(MIGRATOR_DSN)
    try:
        sett_kontekst(m, fremmed, "sys", "r0")
        bid = m.execute(
            "INSERT INTO brukeridentitet (issuer, sub) VALUES (%s,%s)"
            " ON CONFLICT (issuer,sub) DO UPDATE SET sub=EXCLUDED.sub"
            " RETURNING bruker_id",
            ("https://idp.example", f"{fremmed}-x")).fetchone()[0]
        m.execute(
            "INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
            " VALUES (%s,%s,ARRAY['leser'])"
            " ON CONFLICT (tenant,bruker_id) DO UPDATE SET aktiv=true",
            (fremmed, bid))
        from api import sesjon as sesjonmodul
        cookie = secrets.token_urlsafe(24)
        ver = m.execute(
            "SELECT authz_version FROM brukermedlemskap WHERE tenant=%s"
            " AND bruker_id=%s", (fremmed, bid)).fetchone()[0]
        m.execute(
            "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
            " authz_snapshot, csrf_hash, opprettet, siste_bruk, utloper,"
            " tilbakekalt) VALUES (%s,%s,%s,%s,%s, now(), now(),"
            " now()+interval '1 hour', false)",
            (sesjonmodul._hash(cookie), fremmed, bid, ver,
             sesjonmodul._hash("x")))
        m.commit()
    finally:
        m.close()
    r = _get(klient, cookie, "/v1/rekruttering/prosesser")
    assert r.status_code == 200
    assert r.json()["prosesser"] == []
