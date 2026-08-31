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


def _fremmed_okt(navn: str, roller):
    """En EKTE browserøkt i en annen tenant enn `TEN` — `_bruker` og
    `_browsersesjon` er bundet til den. -> (tenant, cookie, csrf).

    Økten er komplett, ikke halv: den bærer et gyldig CSRF-token og de
    rollene kalleren ber om, slik at en avvisning fra en muterende rute
    er TENANTGRENSEN og ikke en manglende forutsetning lenger framme."""
    from api import sesjon as sesjonmodul
    from db.pg import koble, sett_kontekst
    fremmed = "t-annen-" + secrets.token_hex(3)
    cookie, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    m = koble(MIGRATOR_DSN)
    try:
        sett_kontekst(m, fremmed, "sys", "r0")
        bid = m.execute(
            "INSERT INTO brukeridentitet (issuer, sub) VALUES (%s,%s)"
            " ON CONFLICT (issuer,sub) DO UPDATE SET sub=EXCLUDED.sub"
            " RETURNING bruker_id",
            ("https://idp.example", f"{fremmed}-{navn}")).fetchone()[0]
        m.execute(
            "INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
            " VALUES (%s,%s,%s)"
            " ON CONFLICT (tenant,bruker_id) DO UPDATE SET"
            " roller=EXCLUDED.roller, aktiv=true",
            (fremmed, bid, list(roller)))
        ver = m.execute(
            "SELECT authz_version FROM brukermedlemskap WHERE tenant=%s"
            " AND bruker_id=%s", (fremmed, bid)).fetchone()[0]
        m.execute(
            "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
            " authz_snapshot, csrf_hash, opprettet, siste_bruk, utloper,"
            " tilbakekalt) VALUES (%s,%s,%s,%s,%s, now(), now(),"
            " now()+interval '1 hour', false)",
            (sesjonmodul._hash(cookie), fremmed, bid, ver,
             sesjonmodul._hash(csrf)))
        m.commit()
        return fremmed, cookie, csrf
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


def _seed_prosess(vekter=None):
    """Hele den ekte kjeden i miniatyr: loggpost → utfort
    evalueringsoppdrag → prosess → to kandidatartefakter → innstilt
    liste (056-veien). -> (prosess_id, liste_id, innhold_hash).

    `vekter` skriver vektsettet BEGGE artefaktene deklarerer. Det er den
    eneste veien til å måle vektporten: valget er prosessvidt og krever
    at de lesbare settene er ENIGE, så et enkelt giftig artefakt ved
    siden av et friskt bare hoppes over — mens et lesbart sett som sier
    noe ANNET feller hele vektingen til reserven. Standard er det
    kanoniske settet."""
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
            kids = []
            for n, (poeng_ok, funn) in enumerate((
                    (True, []),
                    (False, [{"kategori": "krav_ikke_dokumentert",
                              "kilde": {"start": 0, "slutt": 4,
                                        "sitat": "Uten"}}]))):
                kid = uuid.uuid4()
                kids.append(kid)
                rt.execute("SELECT opprett_kandidat(%s,%s,%s)",
                           (TEN, pid, kid))
                rt.execute(
                    "INSERT INTO kandidat_evalueringsartefakt (tenant,"
                    " prosess_id, kandidat_id, artefakt, innhold_sha256)"
                    " VALUES (%s,%s,%s,%s,%s)",
                    (TEN, pid, kid,
                     __import__("json").dumps({
                         "oppfylt": {"drift": poeng_ok, "sky": True},
                         "vekter": ({"drift": 3, "sky": 2}
                                    if vekter is None else vekter),
                         "funn": funn,
                         "intervjusporsmal": ["ARTEFAKTKOPI."]}),
                     hashlib.sha256(str(kid).encode()).hexdigest()))
                # 057-LAGERET EIER SPØRSMÅLENE, IKKE ARTEFAKTKOPIEN
                # (Codex P2, runde 5). Artefaktet over bærer med vilje en
                # ANNEN liste enn lageret: leser flaten kopien, sier
                # svaret «ARTEFAKTKOPI.» og testen faller. Og bare den
                # FØRSTE kandidaten får en rad — lagrene fylles
                # inkrementelt mens kjøringen står på, og den andre skal
                # fortsatt vises, med tom liste.
                if n == 0:
                    rt.execute(
                        "INSERT INTO kandidat_intervjusporsmal (tenant,"
                        " prosess_id, kandidat_id, sporsmal,"
                        " innhold_sha256) VALUES (%s,%s,%s,%s,%s)",
                        (TEN, pid, kid,
                         __import__("json").dumps(["Fortell om drift."]),
                         hashlib.sha256(str(kid).encode()).hexdigest()))
            rt.commit()
            from db.pg import sett_kontekst as _sk
            _sk(m, TEN, "sys", "r3")   # konteksten døde i forrige commit
            n = m.execute("UPDATE oppdrag SET status='utfort'"
                          " WHERE tenant=%s AND id=%s", (TEN, oid)).rowcount
            assert n == 1, "utfort-overgangen traff ikke raden"
            m.commit()
            # Konteksten er transaksjonslokal og døde i commiten over.
            # Manifestformen (080): døren tar MEDLEMMENE og utleder
            # hashen — riggen mottar den i stedet for å finne på en.
            sett_kontekst(rt, TEN, "test", "r2")
            lid, innhold_hash = rt.execute(
                "SELECT ut_liste_id, ut_innhold_hash FROM"
                " opprett_utsendingsliste(%s,%s,NULL,%s,"
                " 'invitasjon','invitasjon-v1',%s::uuid[])",
                (TEN, uuid.uuid4(), oid, kids)).fetchone()
            rt.commit()
            return str(pid), str(lid), innhold_hash
        finally:
            rt.close()
    finally:
        m.close()


def _reap(prosess_id: str):
    """Timerens sletting, den EKTE veien (057): lukk prosessen med en
    lukketid som alt ligger forbi slettefristen, og la
    `reap_kandidatdata` tømme de seks lagrene og merke ankeret
    `slettet_ts`. Ingen håndsatt kolonne — radvakten ville uansett avvist
    et merke satt mens payload sto igjen.

    HVER FUNKSJON KALLES AV ROLLEN SOM EIER DEN, og ingen av dem er
    migrator. 057 REVOKEr begge fra PUBLIC, og `lukk_rekruttering-
    sprosess` grantes bare til runtime-rollen (`migrer.py`, M37_RETTIG-
    HETER_API) — migratorkallet ga `InsufficientPrivilege` og gjorde
    denne testen rød. Reaperen er kryss-tenant og hører til timerrollen
    der den finnes: koblingsvalget deles med `_reaperkobling`, den samme
    057-testene og evidensreaperen bruker, i stedet for å anta
    lokaloppsettet. Og lukkingen krever tenantkontekst
    (`krev_tenantkontekst`), som runtime-koblingen har og migrator ikke."""
    from db.pg import koble, sett_kontekst
    from .test_outbox_bestilling import _reaperkobling
    rt = koble(DSN)
    rp = None
    try:
        sett_kontekst(rt, TEN, "test", "r-lukk")
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '91 days')", (TEN, prosess_id))
        rt.commit()
        rp, _timerrolle = _reaperkobling()
        reapet = rp.execute("SELECT * FROM reap_kandidatdata(50)").fetchall()
        rp.commit()
        assert prosess_id in [str(r[1]) for r in reapet], reapet
    finally:
        rt.close()
        if rp is not None and rp is not rt:
            rp.close()


def _forbi_frist(prosess_id: str):
    """Kundens frist er ute, men reaperen har IKKE rukket batchen sin:
    samme lukking som `_reap` (`now() - 91 dager` mot seedens 90 døgn),
    uten `reap_kandidatdata`. `slettet_ts` står altså NULL og kandidat-
    og mottakerlagrene er urørte.

    Det er nøyaktig vinduet en batchet reaper etterlater — og det ENESTE
    tilstandsbildet der «måler reaperens merke» og «måler kundens frist»
    gir ulike svar. En port som bare leser merket går grønn på alt annet.
    Kallerne bekrefter selv at merket fortsatt er NULL (`_merket`), så en
    reaper som en dag skulle kjøre synkront ikke gjør testene vakuøse."""
    from db.pg import koble, sett_kontekst
    rt = koble(DSN)
    try:
        sett_kontekst(rt, TEN, "test", "r-frist")
        rt.execute("SELECT lukk_rekrutteringsprosess(%s,%s,"
                   " now() - interval '91 days')", (TEN, prosess_id))
        rt.commit()
    finally:
        rt.close()


def _merket(prosess_id: str) -> bool:
    """Har reaperen satt `slettet_ts` på ankeret?"""
    m = _migrator()
    try:
        rad = m.execute(
            "SELECT slettet_ts IS NOT NULL FROM rekrutteringsprosess"
            " WHERE tenant=%s AND prosess_id=%s",
            (TEN, prosess_id)).fetchone()[0]
        m.rollback()
        return rad
    finally:
        m.close()


@pg
def test_fristen_stenger_ogsaa_prosessflaten(klient):
    """Cursor P1 (#220): rapportveien og evalueringslisten håndhever
    `now() < coalesce(lukket_ts, opprettet) + slettefrist_dogn`, men
    `GET /v1/rekruttering/prosesser` filtrerte bare `slettet_ts IS NULL`
    — altså på når reaperens BATCH rakk å kjøre.

    I vinduet mellom fristen og batchen sa listen `slettet: true` /
    `rapport_klar: false`, mens DENNE flaten fortsatt serverte funn,
    sitater og intervjuspørsmål for hver kandidat. Claim-fødselen fyller
    nettopp denne flaten for hver evaluering, så vinduet er ikke en
    kuriositet.

    MUTASJONEN SOM DREPER DENNE: fjern fristleddet fra prosess-SELECT-en
    i `prosesser_endepunkt` (tilbake til `slettet_ts IS NULL` alene)."""
    pid, _lid, _ih = _seed_prosess()
    bid = _bruker("leser-frist", ["leser"])
    cookie, _ = _browsersesjon(bid)

    # POSITIV KONTROLL FØRST: med levende frist ER prosessen der, med
    # kandidatene sine. Uten dette leddet ville en fraværstest gått
    # grønn på en flate som aldri viste noe.
    r = _get(klient, cookie, f"/v1/rekruttering/prosesser?prosess_id={pid}")
    assert r.status_code == 200, r.text
    fore = [p for p in r.json()["prosesser"] if p["prosess_id"] == pid]
    assert len(fore) == 1, "levende prosess skal stå i velgeren"
    assert fore[0]["kandidater"], "…og bære kandidatene sine"

    _forbi_frist(pid)
    assert not _merket(pid), (
        "positiv kontroll: reaperen skal IKKE ha kjørt — det er nettopp"
        " merket-vs-fristen denne testen skiller")

    # INDEKSEN, ikke raden: denne testen måler at prosessen FORSVINNER fra
    # velgeren når fristen er ute. Ber vi om den ved id, er svaret 404 —
    # sant, men det er en annen påstand. Under (#183) står den også.
    r2 = _get(klient, cookie, "/v1/rekruttering/prosesser")
    assert r2.status_code == 200, r2.text
    etter = [p for p in r2.json()["prosesser"] if p["prosess_id"] == pid]
    assert etter == [], (
        "utløpt frist skal stenge prosessflaten FØR reaperen rekker det —"
        " ellers serverer den kandidatdata rapportflaten alt kaller"
        " slettet")
    # Og den DIREKTE veien inn er stengt med samme svar som rapportveien:
    # identisk 404, uansett om prosessen aldri fantes eller nettopp falt
    # ut. En klient som holder på en id fra før fristen skal ikke kunne
    # hente kandidatene med den (#183).
    r3 = _get(klient, cookie, f"/v1/rekruttering/prosesser?prosess_id={pid}")
    assert r3.status_code == 404, (
        "en utløpt prosess kunne hentes direkte på id — fristen stenger"
        f" velgeren, men ikke døra: {r3.text}")


@pg
def test_signering_avvises_naar_fristen_er_ute_foer_reaperen(klient):
    """Cursor P2 (#220): `kandidatdata_slettet` målte bare `slettet_ts`,
    og det merket settes av `reap_kandidatdata` i BATCHER. Foran den
    irreversible handlingen betydde det 201 på en utsendelse hvis
    mottakerdata er forbi kundens frist — seriens ene signatur-slot
    brent, mens rapportflaten alt behandlet oppdraget som slettet.

    MUTASJONEN SOM DREPER DENNE: snevre `reapet`-leddet i signeringens
    oppslag tilbake til `p.slettet_ts IS NOT NULL`."""
    # POSITIV KONTROLL, på sin EGEN serie: signering er irreversibel og
    # serien har nøyaktig én signatur-slot, så 201-armen kan ikke deles
    # med 409-armen.
    frisk_pid, frisk_rot, frisk_hash = _seed_prosess()
    sjef = _bruker("sjef-frist", ["admin"])
    cookie, csrf = _browsersesjon(sjef)
    ok = _post(klient, cookie, csrf,
               f"/v1/rekruttering/lister/{frisk_rot}/signer",
               {"innhold_hash": frisk_hash})
    assert ok.status_code == 201, ok.text
    assert not _merket(frisk_pid)

    pid, rot, rot_hash = _seed_prosess()
    _forbi_frist(pid)
    assert not _merket(pid), (
        "positiv kontroll: reaperen skal IKKE ha kjørt — porten måles på"
        " fristen alene")
    r = _post(klient, cookie, csrf,
              f"/v1/rekruttering/lister/{rot}/signer",
              {"innhold_hash": rot_hash})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "kandidatdata_slettet"
    # …og avvisningen SKREV INGENTING: signatur-sloten står ubrukt.
    m = _migrator()
    try:
        assert m.execute(
            "SELECT count(*) FROM utsendingssignatur WHERE tenant=%s"
            " AND liste_id=%s", (TEN, rot)).fetchone()[0] == 0
        m.rollback()
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
        # Revisjonen er et ANNET medlemsutvalg (identisk innhold ville
        # gitt identisk utledet hash, og serien er unik på innholdet).
        medlem = rt.execute(
            "SELECT kandidat_id FROM utsendingsliste_medlem"
            " WHERE tenant=%s AND liste_id=%s ORDER BY kandidat_id"
            " LIMIT 1", (TEN, liste_id)).fetchone()[0]
        barn, barn_hash = rt.execute(
            "SELECT ut_liste_id, ut_innhold_hash FROM"
            " opprett_utsendingsliste(%s,%s,%s,%s,"
            " 'invitasjon','invitasjon-v1',%s::uuid[])",
            (TEN, serie, liste_id, oid, [medlem])).fetchone()
        rt.commit()
        return str(barn), barn_hash
    finally:
        rt.close()


@pg
def test_prosesslisten_baerer_flatens_kontrakt(klient):
    pid, lid, ih = _seed_prosess()
    bid = _bruker("leser", ["leser"])
    cookie, _ = _browsersesjon(bid)
    r = _get(klient, cookie, f"/v1/rekruttering/prosesser?prosess_id={pid}")
    assert r.status_code == 200, r.text
    pr = [p for p in r.json()["prosesser"] if p["prosess_id"] == pid]
    assert len(pr) == 1
    p = pr[0]
    assert p["vekter"] == {"drift": 3, "sky": 2}
    assert p["vekter_kilde"] == "evalueringsartefakt"
    assert p["evaluering_status"] == "utfort"
    assert p["blinding_av"] is False
    # STARTTIDSPUNKTET FØLGER MED (Codex P2, runde 4): prosessvelgeren
    # hadde bare `prosess_id` å sette på hver oppføring, så brukeren måtte
    # velge mellom rå UUID-er før hun kunne signere en irreversibel
    # utsendelse. Stillingens tittel finnes ikke å hente ennå (#162), men
    # dette gjør — og det skiller prosessene fra hverandre.
    #
    # MUTASJONEN SOM DREPER DENNE: dropp `opprettet` fra svaret.
    from datetime import datetime
    assert datetime.fromisoformat(p["opprettet"]).tzinfo is not None, \
        "et tidspunkt uten sone kan ikke formateres i leserens sone"
    # …og serveren kaller det ALDRI et navn: flaten bruker `navn` når #162
    # en dag gir tittelen, og et tidsstempel er ikke den tittelen.
    assert "navn" not in p
    assert {k["status"] for k in p["kandidater"]} == \
        {"anbefalt", "innstilt_avslag"}
    assert all(set(k) >= {"kandidat_id", "oppfylt", "funn"}
               for k in p["kandidater"])
    # …og INGEN intervjuspørsmål i utvelgelsessvaret (eiers
    # produktbeslutning 27/8, #224/#225): de hører til innkallingen av
    # de beste. Lageret (kandidat_intervjusporsmal) består som
    # shortlist-arcens kilde — men feltet forlater aldri serveren her,
    # verken fra lageret eller fra artefaktkopien.
    assert all("intervjusporsmal" not in k for k in p["kandidater"]), \
        "intervjuspørsmål skal ikke serveres i prosessflaten"
    liste = [l for l in p["lister"] if l["liste_id"] == lid]
    assert liste and liste[0]["innhold_hash"] == ih \
        and liste[0]["antall"] == 2 and liste[0]["signert"] is False


def _prosess_under_kjoring() -> str:
    """En prosess på et oppdrag som fortsatt står `plukket` — nøyaktig den
    tilstanden 057s fødselsport KREVER, og der artefaktene skrives
    inkrementelt. -> prosess_id."""
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
        m.execute("UPDATE oppdrag SET status='plukket' WHERE tenant=%s"
                  " AND id=%s", (TEN, oid))
        m.commit()
    finally:
        m.close()
    from db.pg import koble, sett_kontekst
    rt = koble(DSN)
    try:
        sett_kontekst(rt, TEN, "test", "r-kjorer")
        pid = rt.execute("SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                         (TEN, oid)).fetchone()[0]
        rt.commit()
        return str(pid)
    finally:
        rt.close()


def _terminer(prosess_id: str, status: str) -> str:
    """Løpet ENDER uten at et eneste kandidatlager ble skrevet — den
    skipede controllerens signatur. Bare `utfort`/`feilet` er lovlige fra
    `plukket` (056s statusvakt); `kansellert` nås kun fra `opprettet`, og
    da er prosessen aldri født. -> prosess_id (uendret).

    Oppslaget av oppdraget tas på RUNTIME-koblingen, ikke migrator:
    ankeret leses av leseveiens egen rolle under tenantkontekst
    (`_reap`-formen), mens selve statusskiftet hører til migrator som i
    `_seed_prosess`."""
    from db.pg import koble, sett_kontekst
    rt = koble(DSN)
    try:
        sett_kontekst(rt, TEN, "test", "r-term")
        oid = rt.execute(
            "SELECT oppdrag_id FROM rekrutteringsprosess"
            " WHERE tenant=%s AND prosess_id=%s",
            (TEN, prosess_id)).fetchone()[0]
    finally:
        rt.close()
    m = _migrator()
    try:
        n = m.execute("UPDATE oppdrag SET status=%s WHERE tenant=%s"
                      " AND id=%s", (status, TEN, oid)).rowcount
        assert n == 1, "terminalovergangen traff ikke oppdraget"
        m.commit()
        return prosess_id
    finally:
        m.close()


@pg
def test_terminal_tom_retensjonsanker_holdes_ute_av_prosessvelgeren(klient):
    """Codex P2/#220: claimen føder et retensjonsanker for HVER
    evaluering, men den skipede controlleren skriver ingen kandidatlagre.
    Et terminalt løp med tom prosess har derfor ingenting velgeren kan
    vise — og ville fortrengt en ekte prosess som standardvalg i 30–365
    døgn, siden ankeret lever ut slettefristen. Grensen går ved
    TERMINAL: en pågående tom prosess vises fortsatt (evalueringens
    tilstand-doktrine), for der kommer kandidatene ennå.

    MUTASJONENE SOM DREPER DENNE: fjern `OR EXISTS (...)`-armen (den
    ekte, ferdige prosessen forsvinner), eller hele tilleggspredikatet
    (de tomme ankrene er tilbake i velgeren).
    """
    ekte, _lid, _ih = _seed_prosess()
    tom_utfort = _terminer(_prosess_under_kjoring(), "utfort")
    tom_feilet = _terminer(_prosess_under_kjoring(), "feilet")
    kjorer = _prosess_under_kjoring()
    bid = _bruker("velger-leser", ["leser"])
    cookie, _ = _browsersesjon(bid)
    r = _get(klient, cookie, "/v1/rekruttering/prosesser")
    assert r.status_code == 200, r.text
    ider = {p["prosess_id"] for p in r.json()["prosesser"]}
    assert tom_utfort not in ider, \
        "et terminalt tomt retensjonsanker fortrenger ekte prosesser"
    assert tom_feilet not in ider, \
        "et feilet tomt retensjonsanker fortrenger ekte prosesser"
    assert ekte in ider, "den ferdige prosessen med kandidater falt ut"
    assert kjorer in ider, "en pågående tom prosess skal fortsatt vises"


@pg
def test_evalueringens_tilstand_folger_med_leseflaten(klient):
    """Codex P2: prosessen fødes MENS kjøringen står på (`plukket` — 057s
    fødselsport), og kandidatartefaktene skrives inkrementelt etterpå.
    Uten oppdragets status kunne flaten ikke skille en delvis
    kandidatliste fra en ferdig rangering, og en `feilet`/`kansellert`
    kjøring — der resten aldri kommer — så nøyaktig like ferdig ut.

    MUTASJONEN SOM DREPER DENNE: fjern `o.status` fra spørringen i
    `prosesser_endepunkt`.
    """
    ferdig, _lid, _ih = _seed_prosess()
    kjorer = _prosess_under_kjoring()
    bid = _bruker("tilstand-leser", ["leser"])
    cookie, _ = _browsersesjon(bid)
    r = _get(klient, cookie, "/v1/rekruttering/prosesser")
    assert r.status_code == 200, r.text
    status = {p["prosess_id"]: p["evaluering_status"]
              for p in r.json()["prosesser"]}
    assert status[kjorer] == "plukket", \
        "en evaluering midt i løpet ble meldt som en ferdig rangering"
    assert status[ferdig] == "utfort"
    # …og prosessen FORSVINNER ikke: å filtrere den bort ville vært sin
    # egen løgn — «ingen aktiv rekrutteringsprosess» — og oppdraget er
    # eneste vei inn til å se at noe kjører.
    assert kjorer in status


def _artefakt(prosess_id: str, oppfylt: dict, funn=(), ekstra=None,
              raa=None) -> str:
    """En ekstra kandidatartefakt i en eksisterende prosess, på den
    KANONISKE formen: ingen `status`-nøkkel — den finnes ikke i
    `evaluering.evaluer_kandidat`s returverdi. -> kandidat_id.

    `ekstra` skriver felter den kanoniske formen IKKE har — den eneste
    veien til å måle at leseflaten avviser dem (runtime har INSERT på
    lageret, så en produsent kan faktisk skrive dem). `raa` skriver
    artefaktet i sin helhet, også når det ikke er et objekt: 057 har
    ingen formsjekk på kolonnen, så `[]` og `3` er lovlige INSERTer."""
    import json as _json
    from db.pg import koble, sett_kontekst
    rt = koble(DSN)
    try:
        sett_kontekst(rt, TEN, "test", "r-art")
        kid = uuid.uuid4()
        rt.execute("SELECT opprett_kandidat(%s,%s,%s)",
                   (TEN, prosess_id, kid))
        innhold = (raa if raa is not None else
                   {"oppfylt": oppfylt, "vekter": {"drift": 3, "sky": 2},
                    "funn": list(funn), "intervjusporsmal": [],
                    **(ekstra or {})})
        rt.execute(
            "INSERT INTO kandidat_evalueringsartefakt (tenant, prosess_id,"
            " kandidat_id, artefakt, innhold_sha256) VALUES (%s,%s,%s,%s,%s)",
            (TEN, prosess_id, kid, _json.dumps(innhold),
             hashlib.sha256(str(kid).encode()).hexdigest()))
        rt.commit()
        return str(kid)
    finally:
        rt.close()


def _sporsmalslager(prosess_id: str, kandidat_id: str, verdi):
    """En rad i 057s eget spørsmålslager, med `verdi` skrevet RÅTT.
    Kolonnen er `jsonb NOT NULL` og ingenting mer, så en skalar, et objekt
    eller en blandet liste er lovlige INSERTer for runtime."""
    import json as _json
    from db.pg import koble, sett_kontekst
    rt = koble(DSN)
    try:
        sett_kontekst(rt, TEN, "test", "r-spm")
        rt.execute("SELECT opprett_kandidat(%s,%s,%s)",
                   (TEN, prosess_id, kandidat_id))
        rt.execute(
            "INSERT INTO kandidat_intervjusporsmal (tenant, prosess_id,"
            " kandidat_id, sporsmal, innhold_sha256) VALUES (%s,%s,%s,%s,%s)",
            (TEN, prosess_id, kandidat_id, _json.dumps(verdi),
             hashlib.sha256(str(kandidat_id).encode()).hexdigest()))
        rt.commit()
    finally:
        rt.close()


@pg
def test_giftig_sporsmalstype_tar_ikke_ned_detaljpanelet(klient):
    """Codex P2 (runde 8): `kandidat_intervjusporsmal.sporsmal` er `jsonb
    NOT NULL` i 057 og ingenting mer — `3`, `"hei"` og `{"a": 1}` er alle
    lovlige INSERTer for runtime. Verdien ble sendt RÅ ut, og flaten gjør
    `(kandidat.intervjusporsmal || []).map(...)`: en ikke-array er sann,
    så `||` verner ikke, og `.map` finnes ikke på den. Én slik rad tok
    HELE detaljpanelet ned med en `TypeError`.

    Elementene måles også: en liste med et objekt i ville rendret
    `[object Object]` som et intervjuspørsmål.

    Til forskjell fra `funn` bæres det IKKE i `lesbart` — spørsmålene er
    ren visning og inngår ikke i trafikklyset, så et tomt spørsmålsfelt
    gjør ingen kandidat grønnere enn hun er. Den positive kontrollen
    under måler nettopp det: den giftige kandidaten har perfekt `oppfylt`
    og ingen funn, og er fortsatt `anbefalt`.

    Etter #224 hentes spørsmålene ikke lenger i det hele tatt, så porten
    er ikke lenger en typesjekk, men et fravær: giftige lagerrader kan
    ikke nå flaten fordi feltet ikke forlater serveren.

    MUTASJONEN SOM DREPER DENNE: la `_kandidater` emittere spørsmålene
    igjen — enten fra artefaktkopien (drop `- 'intervjusporsmal'` og legg
    `art.get("intervjusporsmal")` på kandidaten) eller fra lageret (legg
    JOIN-en tilbake og send `sporsmal` rått ut).
    """
    pid, _lid, _ih = _seed_prosess()
    perfekt = {"drift": True, "sky": True}
    skalar = _artefakt(pid, perfekt)
    _sporsmalslager(pid, skalar, 3)
    objekt = _artefakt(pid, perfekt)
    _sporsmalslager(pid, objekt, {"a": 1})
    blandet = _artefakt(pid, perfekt)
    _sporsmalslager(pid, blandet, ["Fortell om drift.", {"a": 1}])
    ekte = _artefakt(pid, perfekt)
    _sporsmalslager(pid, ekte, ["Fortell om drift."])
    bid = _bruker("lys-leser-spm", ["leser"])
    cookie, _ = _browsersesjon(bid)
    r = _get(klient, cookie, f"/v1/rekruttering/prosesser?prosess_id={pid}")
    assert r.status_code == 200, r.text
    p = [x for x in r.json()["prosesser"] if x["prosess_id"] == pid][0]
    # Etter eiers produktbeslutning (#224) forlater spørsmålene aldri
    # serveren — giftklassen kan dermed ikke nå flaten i det hele tatt.
    # Porten består i sin sterkeste form: 200 tross giftige lagerrader,
    # feltet fraværende for ALLE, og trafikklyset urørt.
    assert all("intervjusporsmal" not in k for k in p["kandidater"])
    lys = {k["kandidat_id"]: k["status"] for k in p["kandidater"]}
    assert all(lys[kid] == "anbefalt"
               for kid in (skalar, objekt, blandet, ekte)), lys


def test_utvelgelsen_rorer_ikke_sporsmalslageret():
    """Cursor P2 (#224): et felt som ikke serveres, skal heller ikke
    HENTES. HTTP-porten over måler svaret og ville bestått også med
    JOIN-en stående — den ser ikke arbeidet i basen. Denne måler
    spørringen selv: `_kandidater` løper over HVER ureapet prosess med
    inntil 5000 kandidater hver (#183), så en JOIN mot et lager ingen
    leser er unødvendig arbeid i basen og unødvendig nyttelast over
    forbindelsen.

    Subtraksjonen består: artefaktet kan fortsatt bære en KOPI av
    spørsmålene, og den skal ut av svaret.
    """
    import inspect

    from api import rekruttering as rk
    kilde = inspect.getsource(rk._kandidater)
    sql = kilde[kilde.index("rader = conn.execute"):kilde.index(".fetchall")]
    assert "kandidat_intervjusporsmal" not in sql \
        and "i.sporsmal" not in sql, \
        "utvelgelsen skal ikke hente spørsmålslageret den ikke serverer"
    assert "- 'intervjusporsmal'" in sql, \
        "artefaktkopien skal fortsatt subtraheres ut av svaret"


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
    r = _get(klient, cookie, f"/v1/rekruttering/prosesser?prosess_id={pid}")
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
def test_trafikklyset_er_fail_closed_paa_alle_tre_leddene(klient):
    """Cursor P1 (10:01): utledningen er porten, og den tar bare imot det
    skriveveien selv ville godtatt.

    Tre ledd, tre ULIKE veier til et falskt grønt lys — alle på artefakter
    runtime FAKTISK kan skrive (den har INSERT på lageret):

    1. en skrevet `status: "anbefalt"` gikk foran hele dømmingen,
    2. `"false"` er en SANN streng: kandidaten «oppfylte» kravet. Begge
       skriveportene (`evaluer_kandidat` og `ranger`) avviser
       ikke-boolske verdier med `ikke_boolsk_oppfyllelse`; leseveien tok
       imot dem,
    3. bare de kravene som STO der ble målt: `{"drift": true}` mot en
       profil som krever drift OG sky ble «Anbefalt» fordi det ikke fantes
       et `sky`-oppslag å feile på. `ranger` krever det EKSAKTE kravsettet.

    MUTASJONENE SOM DREPER DENNE, én per ledd: legg `art.get("status") or`
    tilbake foran utledningen; bytt `v is True` mot `v`; fjern
    `set(oppfylt) == set(vekter)`.
    """
    pid, _lid, _ih = _seed_prosess()
    pastand = _artefakt(pid, {"drift": False, "sky": False},
                        ekstra={"status": "anbefalt"})
    strenger = _artefakt(pid, {"drift": "false", "sky": "false"})
    halvt_maalt = _artefakt(pid, {"drift": True})
    alle = _artefakt(pid, {"drift": True, "sky": True})
    bid = _bruker("lys-leser-2", ["leser"])
    cookie, _ = _browsersesjon(bid)
    r = _get(klient, cookie, f"/v1/rekruttering/prosesser?prosess_id={pid}")
    assert r.status_code == 200, r.text
    p = [x for x in r.json()["prosesser"] if x["prosess_id"] == pid][0]
    lys = {k["kandidat_id"]: k["status"] for k in p["kandidater"]}
    assert lys[pastand] == "vurderes", \
        "en skrevet status gikk forbi dømmingen"
    assert lys[strenger] == "vurderes", \
        "«false» som streng ble lest som oppfylt"
    assert lys[halvt_maalt] == "vurderes", \
        "et umålt krav i profilen ble til en anbefaling"
    # Positiv kontroll: porten stenger ikke for den ekte anbefalingen.
    assert lys[alle] == "anbefalt"


@pg
def test_giftig_artefakttype_tar_ikke_ned_prosesslisten(klient):
    """Cursor P1 (10:29): FEIL TYPE er ikke en halv sannhet — og den var
    ikke en 500 heller.

    `x or {}` verner mot NULL og tomt, aldri mot type: `funn` som objekt
    ga `AttributeError` på `f.get`, `oppfylt` som liste eller tall ga
    `AttributeError`/`TypeError` på `.values()`, og et artefakt som ikke
    er et objekt i det hele tatt fikk basen til å feile på `jsonb - text`
    (`cannot delete from scalar`) FØR Python. Alle tre er lovlige
    INSERTer for runtime — 057 har ingen formsjekk på kolonnen. Og fordi
    lesningen løper over HVER prosess, tok ett giftig artefakt ned hele
    tenantens prosessliste, signeringsflaten inkludert.

    Målt på BEGGE ledd: at svaret er 200 (ingen 500), og at de øvrige
    kandidatene fortsatt står i det — «kom seg gjennom» er ikke det samme
    som «svarte det samme». Den giftige kandidaten er `vurderes`, aldri
    grønt lys på data ingen kunne lese.

    OG DET ANDRE LEDDET ER DET SOM KOSTER: å NORMALISERE et ulesbart
    `funn` til `[]` gjør kandidaten GRØNNERE — «ingen funn» er halve
    anbefalingen. `funn_objekt` under har perfekt `oppfylt` og et
    uleselig `funn`; verner man bare mot krasjet, byttes 500-en mot et
    falskt grønt lys foran en irreversibel utsendelse. Derfor bærer
    lesningen `lesbart`, og en rad som ikke var lesbar kan ikke bevise
    en anbefaling.

    MUTASJONENE SOM DREPER DENNE, én per ledd: bytt `isinstance(f, dict)
    and ...` mot `f.get(...)`; bytt de to `isinstance`-portene i `lest`
    mot `or []` / `or {}`; fjern `jsonb_typeof(...) = 'object'`-CASEen;
    fjern `lesbart and` fra anbefalingen.
    """
    pid, _lid, _ih = _seed_prosess()
    # Perfekt `oppfylt`, uleselig `funn`: den ENESTE forskjellen fra en
    # ekte anbefaling er at funnene ikke kunne leses.
    funn_objekt = _artefakt(
        pid, {"drift": True, "sky": True},
        ekstra={"funn": {"kategori": "krav_ikke_dokumentert"}})
    oppfylt_liste = _artefakt(pid, {}, ekstra={"oppfylt": ["drift", "sky"]})
    oppfylt_tall = _artefakt(pid, {}, ekstra={"oppfylt": 1, "funn": "nei"})
    skalar = _artefakt(pid, {}, raa=3)
    array = _artefakt(pid, {}, raa=["ikke et objekt"])
    alle = _artefakt(pid, {"drift": True, "sky": True})
    bid = _bruker("lys-leser-3", ["leser"])
    cookie, _ = _browsersesjon(bid)
    r = _get(klient, cookie, f"/v1/rekruttering/prosesser?prosess_id={pid}")
    assert r.status_code == 200, r.text
    p = [x for x in r.json()["prosesser"] if x["prosess_id"] == pid][0]
    lys = {k["kandidat_id"]: k["status"] for k in p["kandidater"]}
    for kid in (funn_objekt, oppfylt_liste, oppfylt_tall, skalar, array):
        assert lys[kid] == "vurderes", \
            f"{kid} fikk en dom av data ingen kunne lese"
    # Positiv kontroll: de andre kandidatene overlevde nabomaten, og den
    # ekte anbefalingen er fortsatt en anbefaling.
    assert lys[alle] == "anbefalt"
    # …og vektene kom fra artefaktet, ikke fra reserven: et giftig
    # artefakt tidlig i rekkefølgen skal ikke gjøre huset til kilde.
    assert p["vekter_kilde"] == "evalueringsartefakt"


def test_vektporten_er_skriveveiens_egen():
    """Codex P2 (runde 9): `isinstance(v, dict)` spurte om FORMEN og
    ikke om verdiene.

    `evaluering.ranger` avviser et tomt vektsett og enhver verdi som
    ikke er et ikke-negativt heltall (`ugyldige_vekter`) — men den
    porten står i en funksjon runtime kan gå utenom med en rå INSERT i
    `kandidat_evalueringsartefakt`, som 057 ikke formsjekker. Leseveien
    målte derfor ikke det skriveveien måler.

    Predikatet er rent og trenger ingen base — de fem avvisningene og
    den ene godkjenningen ses her, HTTP-testen under viser at det
    faktisk er koblet inn. Drepende mutasjon: bytt kroppen mot
    `isinstance(v, dict)`.
    """
    from api.rekruttering import _vekter_lesbare
    assert _vekter_lesbare({"drift": 3, "sky": 0})
    assert not _vekter_lesbare({}), "tomt sett vekter ingenting"
    assert not _vekter_lesbare({"drift": None}), "null er ingen vekt"
    assert not _vekter_lesbare({"drift": -3}), "negativ vekt"
    assert not _vekter_lesbare({"drift": True}), "bool er `int` i Python"
    assert not _vekter_lesbare({"drift": "3"}), "streng blir NaN i flaten"
    assert not _vekter_lesbare({"drift": 1.5}), "ranger krever heltall"
    assert not _vekter_lesbare(["drift"]), "ikke engang et objekt"
    # Codex P2 (runde 10): et heltall over `Number.MAX_SAFE_INTEGER` er
    # avrundet ALT i flatens `JSON.parse` — den viste vekten er ikke
    # lenger den rangerte. Stort nok blir det `Infinity`, og skyverens
    # tak (utledet av de samme verdiene) står igjen på 10 mens poengene
    # er uendelige. Grensen er inklusiv: det siste eksakte tallet er en
    # lovlig vekt.
    assert _vekter_lesbare({"drift": 2 ** 53 - 1}), "siste eksakte tall"
    assert not _vekter_lesbare({"drift": 2 ** 53}), "avrundes i flaten"
    assert not _vekter_lesbare({"drift": 10 ** 400}), "blir `Infinity`"


@pg
def test_giftige_vekter_faller_til_reserven_ikke_til_flaten(klient):
    """Codex P2 (runde 9): en vekting ingen kunne stå inne for, ble
    likevel prosessens.

    `{"drift": null}` er en lovlig INSERT i det uformsjekkede lageret, og
    den gamle porten tok imot den fordi den var en `dict`. To ting fulgte
    av det: trafikklyset måler `set(oppfylt) == set(vekter)` — bare
    NØKLENE — så «Anbefalt» ble bevist mot tall som ikke fantes, og
    flaten regner `Number(verdi)` på de samme verdiene og får `0` av
    `null` (og `NaN` av en streng), så skyveren står ett sted mens tallet
    ved siden av sier noe annet. Alt dette på flaten der den irreversible
    signeringen skjer.

    Er vektene ikke lesbare, er de INGEN opplysning: reserven (`3` per
    krav) er svaret, og `vekter_kilde` sier «standard» — merknaden flaten
    allerede viser. `sky` skiller de to kildene alene (2 fra artefaktet,
    3 fra reserven).

    Vektsettet skrives på BEGGE artefaktene: valget er prosessvidt og
    krever at de LESBARE settene er enige, så et giftig artefakt ved
    siden av et friskt skal nettopp bare hoppes over.

    Drepende mutasjon: bytt `_vekter_lesbare(...)` tilbake mot
    `isinstance(art.get("vekter"), dict)` — da er kilden artefaktet og
    `vekter["drift"]` er `None`.
    """
    pid, _lid, _ih = _seed_prosess(vekter={"drift": None, "sky": 2})
    bid = _bruker("vekt-leser", ["leser"])
    cookie, _ = _browsersesjon(bid)
    r = _get(klient, cookie, f"/v1/rekruttering/prosesser?prosess_id={pid}")
    assert r.status_code == 200, r.text
    p = [x for x in r.json()["prosesser"] if x["prosess_id"] == pid][0]
    assert p["vekter_kilde"] == "standard", \
        "en uleselig vekting ble utgitt for stillingsprofilens egen"
    assert p["vekter"] == {"drift": 3, "sky": 3}, p["vekter"]
    # Positiv kontroll: reserven er en ÆRLIG vekting, ikke en straff —
    # kandidaten som oppfyller alt er fortsatt anbefalt.
    lys = {k["kandidat_id"]: k["status"] for k in p["kandidater"]}
    assert "anbefalt" in lys.values(), lys


@pg
def test_motstridende_vekter_er_ingen_vekting(klient):
    """Cursor P2 (runde 10): første lesbare vektsett vant, avgjort av en
    UUID.

    Vekten er STILLINGENS, ikke kandidatens, så to lesbare artefakter som
    deklarerer ULIKE sett er ikke et valg mellom to kilder — det er
    beviset på at ingen av dem kan tas for prosessens. Runtime har INSERT
    på det uformsjekkede lageret, og valget sto på `ORDER BY
    a.kandidat_id`: et smalt, gyldig sett på lav UUID ble prosessens
    vekting. Trafikklyset måler `set(oppfylt) == set(vekter)`, så nettopp
    et SMALERE sett gjør «Anbefalt» lettere å oppnå — foran den
    irreversible signeringen.

    Seeden skriver `{drift: 3, sky: 2}` på begge sine artefakter; det
    tredje deklarerer `{drift: 3}` og oppfyller bare `drift`.

    Drepende mutasjon: sett betingelsen tilbake til «første lesbare sett
    vinner». Da er `vekter_kilde` «evalueringsartefakt» uansett hvilken
    av de to settene UUID-rekkefølgen plukket — dommen her er derfor
    uavhengig av rekkefølgen, slik den må være.
    """
    pid, _lid, _ih = _seed_prosess()
    smal = _artefakt(pid, {"drift": True}, ekstra={"vekter": {"drift": 3}})
    bid = _bruker("vekt-uenig", ["leser"])
    cookie, _ = _browsersesjon(bid)
    r = _get(klient, cookie, f"/v1/rekruttering/prosesser?prosess_id={pid}")
    assert r.status_code == 200, r.text
    p = [x for x in r.json()["prosesser"] if x["prosess_id"] == pid][0]
    assert p["vekter_kilde"] == "standard", \
        "et vektsett ingen av artefaktene var enige om ble prosessens"
    assert p["vekter"] == {"drift": 3, "sky": 3}, p["vekter"]
    # …og kandidaten det smale settet ville gjort grønn, er det ikke:
    # hun er ikke målt mot `sky` i det hele tatt.
    lys = {k["kandidat_id"]: k["status"] for k in p["kandidater"]}
    assert lys[smal] == "vurderes", \
        "«Anbefalt» ble bevist mot et kravsett som ikke er profilens"
    # Positiv kontroll: ENIGE lesbare sett er fortsatt artefaktets, så
    # dommen over feller uenighet — ikke det å ha flere artefakter.
    pid2, _l2, _h2 = _seed_prosess()
    _artefakt(pid2, {"drift": True, "sky": True})
    r2 = _get(klient, cookie, f"/v1/rekruttering/prosesser?prosess_id={pid2}")
    assert r2.status_code == 200, r2.text
    p2 = [x for x in r2.json()["prosesser"] if x["prosess_id"] == pid2][0]
    assert p2["vekter_kilde"] == "evalueringsartefakt", p2["vekter_kilde"]
    assert p2["vekter"] == {"drift": 3, "sky": 2}, p2["vekter"]


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
def test_replay_venter_pa_originalen_i_flukt_i_stedet_for_a_avvise(klient):
    """Codex P1 (runde 8): runde 3 avlyste spissporten for et replay — men
    bare for et replay VI KUNNE SE. Originalen som har SATT INN signaturen
    sin og ennå ikke committet, er usynlig for `_fullfort_replay` (READ
    COMMITTED), så retryen leste `replay = False` på en operasjon som var
    i ferd med å lykkes. Var serien redigert videre i mellomtiden, svarte
    spissporten `liste_utdatert` (409) rett FORAN låsen — et definitivt
    avslag på en irreversibel autorisasjon som sekunder senere står i
    basen.

    Formen er medlemskaps- og fullmaktstestenes: originalens transaksjon
    står UCOMMITTET, og `signer_utsendingsliste` har selv tatt
    medlemskapslåsen gjennom `laas_godkjenner` (056 §7b). Låsen som
    holdes er altså nøyaktig den retryen må gjennom — og et replay er per
    definisjon samme signatar, så de to møtes på samme rad.

    MUTASJONEN SOM DREPER DENNE: flytt `laas_godkjenner`-blokka med
    etteroppslaget tilbake ned under portene i `signer_endepunkt`.
    """
    import threading

    from db.pg import koble, sett_kontekst

    _pid, rot, rot_hash = _seed_prosess()
    bid = _bruker("sjef-replay-flukt", ["admin"])
    cookie, csrf = _browsersesjon(bid)
    nokkel = secrets.token_urlsafe(24)
    svar: dict = {}
    orig = koble(DSN)
    try:
        sett_kontekst(orig, TEN, "test", "r-original")
        orig.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                     (TEN, rot, bid, nokkel))
        # Serien redigeres videre MENS originalen står: uten fiksen er det
        # spissporten som svarer retryen, foran låsen.
        _barn, _barn_hash = _ny_versjon(rot)

        def post():
            svar["r"] = _post(klient, cookie, csrf,
                              f"/v1/rekruttering/lister/{rot}/signer",
                              {"innhold_hash": rot_hash}, idem=nokkel)

        tp = threading.Thread(target=post)
        tp.start()
        tp.join(timeout=2)
        assert tp.is_alive(), (
            "retryen svarte FØR medlemskapslåsen — da dømte en"
            " tilstandsport en operasjon som var i ferd med å lykkes")
        orig.commit()                # originalen lander, låsen slippes
        tp.join(timeout=10)
        assert not tp.is_alive(), "retryen kom aldri ut av låskøen"
    finally:
        orig.close()
    r = svar["r"]
    assert r.status_code == 201, r.text
    assert r.json()["innhold_hash"] == rot_hash
    # …og replayet la ingen ny rad: originalens signatur er den ene, og
    # seriens ene slot er brukt nøyaktig én gang.
    m = _migrator()
    try:
        rader = m.execute(
            "SELECT signatar, operasjonsnokkel FROM utsendingssignatur"
            " WHERE tenant=%s AND liste_id=%s", (TEN, rot)).fetchall()
        assert len(rader) == 1, rader
        assert rader[0][0] == bid and rader[0][1] == nokkel
        m.rollback()
    finally:
        m.close()


@pg
def test_signering_avvises_nar_kandidatdata_er_reapet(klient):
    """Codex P2 (runde 4): `reap_kandidatdata` (057) tømmer kandidat- og
    mottakerdataene og merker prosessen `slettet_ts` når slettefristen
    løper ut. Listeraden i 056 overlever — den er append-only — og
    oppslaget i signeringen spurte bare etter tenant og liste_id. En flate
    eller en bekreftelsesdialog som sto åpen over fristen kunne derfor
    autorisere en utsendelse hvis mottakere ikke lenger finnes, og brenne
    seriens ENE signatur-slot på den.

    MUTASJONEN SOM DREPER DENNE: fjern `kandidatdata_slettet`-porten (og
    dermed `reapet`-leddet i oppslaget).
    """
    pid, rot, rot_hash = _seed_prosess()
    bid = _bruker("sjef-reapet", ["admin"])
    cookie, csrf = _browsersesjon(bid)
    _reap(pid)
    r = _post(klient, cookie, csrf,
              f"/v1/rekruttering/lister/{rot}/signer",
              {"innhold_hash": rot_hash})
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "kandidatdata_slettet"
    # …og avvisningen SKREV INGENTING: signatur-sloten står ubrukt.
    m = _migrator()
    try:
        assert m.execute(
            "SELECT count(*) FROM utsendingssignatur WHERE tenant=%s"
            " AND liste_id=%s", (TEN, rot)).fetchone()[0] == 0
        m.rollback()
    finally:
        m.close()


@pg
def test_replay_overlever_at_kandidatdata_ble_reapet(klient):
    """Cursor P2 (08:51): speilet av testen over — reap-porten har det
    SAMME `not replay`-unntaket som spissporten, og bare spissporten
    hadde en test på det.

    Unntaket er riktig og nødvendig: signerer eieren, og 201-et går tapt
    på veien hjem, ber `ui.rekruttering.usikkert_utfall` henne prøve igjen
    med løftet om at forsøket gjentar den SAMME operasjonen — og `api.js`
    sender samme nøkkel. Løper slettefristen ut i mellomtiden, ville en
    reap-port uten unntaket svart 409 `kandidatdata_slettet` på en
    signatur som STÅR, og flaten leser 409 som et definitivt avslag.
    Uten en test kan unntaket forsvinne i en omskriving av portstabelen
    (fire runder til nå) uten at CI ser det.

    MUTASJONEN SOM DREPER DENNE: fjern `not replay and` fra
    `kandidatdata_slettet`-porten, eller flytt `_fullfort_replay` ned
    under den.
    """
    pid, rot, rot_hash = _seed_prosess()
    bid = _bruker("sjef-reap-replay", ["admin"])
    cookie, csrf = _browsersesjon(bid)
    nokkel = secrets.token_urlsafe(24)
    forste = _post(klient, cookie, csrf,
                   f"/v1/rekruttering/lister/{rot}/signer",
                   {"innhold_hash": rot_hash}, idem=nokkel)
    assert forste.status_code == 201, forste.text
    _reap(pid)
    replay = _post(klient, cookie, csrf,
                   f"/v1/rekruttering/lister/{rot}/signer",
                   {"innhold_hash": rot_hash}, idem=nokkel)
    assert replay.status_code == 201, replay.text
    # …og replayet la ingen ny rad: det er den samme signaturen.
    m = _migrator()
    try:
        rader = m.execute(
            "SELECT operasjonsnokkel FROM utsendingssignatur"
            " WHERE tenant=%s AND liste_id=%s", (TEN, rot)).fetchall()
        assert rader == [(nokkel,)], rader
        m.rollback()
    finally:
        m.close()
    # …og porten står: en FERSK nøkkel etter reaping er fortsatt 409, og
    # det er `kandidatdata_slettet` — ikke `serien_alt_signert`, som ville
    # betydd at reap-porten aldri ble målt i det hele tatt.
    fersk = _post(klient, cookie, csrf,
                  f"/v1/rekruttering/lister/{rot}/signer",
                  {"innhold_hash": rot_hash})
    assert fersk.status_code == 409, fersk.text
    assert fersk.json()["feil"] == "kandidatdata_slettet"


@pg
def test_replay_med_annen_hash_er_konflikt(klient):
    """Codex P2 (runde 4): forbigangen for et fullført replay hører til
    spissporten ALENE. Spissporten spør om radens tilstand, og det
    spørsmålet er avlyst av at signaturen står; hashen spør om KROPPENS
    påstand om hva signataren leste, og den påstanden er kallerens. Med
    forbigangen på begge fikk samme nøkkel + liste + signatar med en ANNEN
    `innhold_hash` 201: endepunktet bekreftet innhold kalleren aldri
    signerte, og en gjenbrukt nøkkel med endret inndata slapp unna
    konfliktregelen.

    MUTASJONEN SOM DREPER DENNE: sett `not replay and` tilbake foran
    hash-porten.
    """
    _pid, rot, rot_hash = _seed_prosess()
    bid = _bruker("sjef-replay-hash", ["admin"])
    cookie, csrf = _browsersesjon(bid)
    nokkel = secrets.token_urlsafe(24)
    forste = _post(klient, cookie, csrf,
                   f"/v1/rekruttering/lister/{rot}/signer",
                   {"innhold_hash": rot_hash}, idem=nokkel)
    assert forste.status_code == 201, forste.text
    # SAMME nøkkel, liste og signatar — men kroppen påstår et annet innhold.
    r = _post(klient, cookie, csrf,
              f"/v1/rekruttering/lister/{rot}/signer",
              {"innhold_hash": "f" * 64}, idem=nokkel)
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "innhold_endret"
    # …og det EKTE replayet — samme hash — går fortsatt igjennom.
    igjen = _post(klient, cookie, csrf,
                  f"/v1/rekruttering/lister/{rot}/signer",
                  {"innhold_hash": rot_hash}, idem=nokkel)
    assert igjen.status_code == 201, igjen.text


@pg
def test_gjenbrukt_idempotensnokkel_er_409_ikke_500(klient):
    """Codex P2: gjenbrukes nøkkelen på en ANNEN liste, reiser
    `signer_utsendingsliste` `invalid_parameter_value` (056 §7b) — ikke
    `unique_violation`. Uoversatt escaper den `_med_conn`, som bare kjenner
    `_Avbrudd`/`Aktiveringsfeil`, og klienten fikk en 500 der plattformens
    kanoniske svar er 409 `idempotenskonflikt`.

    MUTASJONEN SOM DREPER DENNE: fjern
    `except psycopg.errors.InvalidParameterValue`-armen.
    """
    _pid, rot, rot_hash = _seed_prosess()
    bid = _bruker("sjef-nokkel", ["admin"])
    cookie, csrf = _browsersesjon(bid)
    nokkel = secrets.token_urlsafe(24)
    forste = _post(klient, cookie, csrf,
                   f"/v1/rekruttering/lister/{rot}/signer",
                   {"innhold_hash": rot_hash}, idem=nokkel)
    assert forste.status_code == 201, forste.text
    # SAMME nøkkel, ANNEN liste: ikke et replay — en konflikt.
    barn, barn_hash = _ny_versjon(rot)
    r = _post(klient, cookie, csrf,
              f"/v1/rekruttering/lister/{barn}/signer",
              {"innhold_hash": barn_hash}, idem=nokkel)
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "idempotenskonflikt"


@pg
def test_samtidig_nokkelkollisjon_er_konflikt_ikke_signert_serie(klient):
    """Codex P2 (runde 6): den SAMTIDIGE halvdelen av testen over.

    Sekvensielt ser `signer_utsendingsliste` den forrige nøkkelraden og
    reiser `invalid_parameter_value`. Er kallene samtidige og signatarene
    ULIKE, låser `laas_godkjenner` hver sin medlemskapsrad — de
    serialiserer ikke hverandre — så begge passerer nøkkel-oppslaget mens
    den andres rad er ucommittet. Taperen blokkerer på unik-indeksen og
    får `unique_violation` på `(tenant, operasjonsnokkel)`.

    Endepunktet dømte alle 23505 som `serien_alt_signert`. Taperens serie
    er URØRT: svaret sa «denne utsendelsen er alt autorisert» om en
    signatur som aldri ble skrevet, og en klient som tror på det slutter å
    prøve på en utsendelse ingen har godkjent.

    MUTASJONEN SOM DREPER DENNE: fjern `_NOKKELBRUDD`-grenen i
    `UniqueViolation`-armen — da blir svaret `serien_alt_signert` igjen.
    """
    import threading

    from db.pg import koble, sett_kontekst

    # To ULIKE serier: bare nøkkelen kan kollidere. Var listene i samme
    # serie, ville PK-en/serie-indeksen brutt først, og testen målt en
    # annen dom enn den den er skrevet for.
    _pa, liste_a, _hash_a = _seed_prosess()
    _pb, liste_b, hash_b = _seed_prosess()
    sign_a = _bruker("sjef-kapp-a", ["admin"])
    sign_b = _bruker("sjef-kapp-b", ["admin"])
    cookie, csrf = _browsersesjon(sign_b)
    nokkel = secrets.token_urlsafe(24)
    svar: dict = {}

    def signer_b():
        svar["r"] = _post(klient, cookie, csrf,
                          f"/v1/rekruttering/lister/{liste_b}/signer",
                          {"innhold_hash": hash_b}, idem=nokkel)

    a = koble(DSN)
    try:
        sett_kontekst(a, TEN, "test", "r-kapp")
        a.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                  (TEN, liste_a, sign_a, nokkel))          # ikke committet
        t = threading.Thread(target=signer_b)
        t.start()
        t.join(timeout=3)
        assert t.is_alive(), \
            "B skulle blokkere på As ucommittede nøkkelrad — uten den" \
            " blokkeringen måler testen ikke kappløpet"
        a.commit()                       # nå brister unik-kravet for B
        t.join(timeout=20)
        assert not t.is_alive(), "B kom aldri tilbake"
    finally:
        a.close()
    m = _migrator()
    try:
        # NAVNET ROUTINGEN LESER, MÅLT MOT KATALOGEN — og målt FØR dommen,
        # så et navneavvik peker på seg selv i stedet for å se ut som en
        # manglende gren.
        from api import rekruttering as rekrutteringsmodul
        navn = [k[0] for k in m.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid ="
            " 'public.utsendingssignatur'::regclass").fetchall()]
        assert rekrutteringsmodul._NOKKELBRUDD in navn, navn
        # …og Bs serie står fortsatt USIGNERT: dommen «alt signert» ville
        # ikke bare vært feil navn på en riktig tilstand.
        assert m.execute(
            "SELECT count(*) FROM utsendingssignatur WHERE tenant=%s"
            " AND liste_id=%s", (TEN, liste_b)).fetchone()[0] == 0
        m.rollback()
    finally:
        m.close()
    r = svar["r"]
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "idempotenskonflikt", r.text


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
def test_signering_avviser_fullmakt_som_trekkes_underveis(klient):
    """Codex P1 (runde 3): `_browserkontekst` måler scopet mot sesjonens
    `authz_snapshot` ved inngangen; 056s port LÅSER medlemskapet, men
    leser med vilje ikke `roller` («rolle- og scope-nivået hører til
    flatens egen autorisasjon (CP3)», §7b). Ingen av de to målte altså
    rollen PÅ skrivetidspunktet. Fratas administratoren
    `bestilling:opprett` i mellomtiden, står medlemskapet fortsatt aktiv,
    og en tilbakekalt fullmakt kunne skrive en irreversibel autorisasjon
    inn i en append-only tabell.

    Samme deterministiske form som medlemskapstesten over: nedgraderingen
    står UCOMMITTET, så autentiseringen ser den gamle rollen (og en
    uendret `authz_version`, altså en gyldig sesjon), mens
    `laas_godkjenner`s `FOR UPDATE` køer bak den.

    MUTASJONEN SOM DREPER DENNE: fjern `_SIGNERINGSSCOPE`-sjekken etter
    `laas_godkjenner` i `signer_endepunkt`.
    """
    import threading

    _pid, lid, ih = _seed_prosess()
    bid = _bruker("sjef-fullmakt", ["admin"])
    cookie, csrf = _browsersesjon(bid)
    svar: dict = {}
    rev = _migrator()
    try:
        # Medlemskapet blir AKTIVT — bare fullmakten forsvinner. `leser`
        # bærer `decisions:read`, ikke `bestilling:opprett`.
        rev.execute("UPDATE brukermedlemskap SET roller=ARRAY['leser']"
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
        rev.commit()                 # nedgraderingen lander, låsen slippes
        tp.join(timeout=10)
        assert not tp.is_alive(), "forespørselen kom aldri ut av låskøen"
    finally:
        rev.rollback()
        rev.close()
    r = svar["r"]
    assert r.status_code == 403, r.text
    assert r.json()["feil"] == "signatar_uten_fullmakt"
    # …og avvisningen SKREV INGENTING: signatur-sloten står ubrukt, så
    # serien kan fortsatt signeres av en som HAR fullmakten.
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
    rg = _get(klient, lc, f"/v1/rekruttering/prosesser?prosess_id={_pid}")
    assert rg.status_code == 200, rg.text
    # Bare den VALGTE prosessen bærer data (#183) — de andre er indeks.
    # `p["lister"]` over alle ville vært en KeyError så snart tenanten har
    # mer enn én prosess, altså en test som består på ordningstilfeldighet.
    svar = rg.json()
    spisser = [l for p in svar["prosesser"]
               if p["prosess_id"] == svar["valgt_prosess_id"]
               for l in p["lister"]]
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
    _fremmed, cookie, _csrf = _fremmed_okt("x", ["leser"])
    r = _get(klient, cookie, "/v1/rekruttering/prosesser")
    assert r.status_code == 200
    assert r.json()["prosesser"] == []


@pg
def test_signering_paa_tvers_av_tenanter_avvises(klient):
    """Cursor P2 (10:29): tenantgrensen måles også på den IRREVERSIBLE
    veien, ikke bare på lesningen.

    GET har `test_rls_skiller_tenantene_ogsaa_her`. POST hadde 404 for en
    tilfeldig UUID — men aldri for en liste som FINNES, hos noen andre,
    med den korrekte innholdshashen. Det er den eneste formen som skiller
    «id-en fantes ikke» fra «id-en fantes, og tenantgrensen holdt»: en
    regresjon i tenant-leddet eller i gjenopprettingen av RLS-konteksten
    ville ellers vært usynlig på nettopp den veien som brenner
    `en_signert_versjon_per_serie` — én signatur per serie, aldri
    tilbake.

    Målt på tre ledd: statusen (404), koden (`liste_ukjent` — grensen
    lekker ikke at raden finnes), og at signatur-sloten står UBRUKT
    etterpå. Avvist er ikke det samme som skrev ingenting.

    MUTASJONEN SOM DREPER DENNE: fjern `AND l.tenant=%s` fra oppslaget OG
    kjør det utenfor tenantkonteksten (f.eks. uten
    `_gjenopprett_kontekst`). De to beltene bærer denne grensen sammen, og
    testen måler utfallet kalleren møter — ikke ett av dem.
    """
    _pid, lid, ih = _seed_prosess()
    _fremmed, cookie, csrf = _fremmed_okt("signerer", ["admin"])
    sti = f"/v1/rekruttering/lister/{lid}/signer"
    r = _post(klient, cookie, csrf, sti, {"innhold_hash": ih})
    assert r.status_code == 404, r.text
    assert r.json()["feil"] == "liste_ukjent"
    m = _migrator()
    try:
        n = m.execute("SELECT count(*) FROM utsendingssignatur"
                      " WHERE tenant=%s AND liste_id=%s",
                      (TEN, lid)).fetchone()[0]
        assert n == 0, "en fremmed tenant brente signatur-sloten"
        m.rollback()
    finally:
        m.close()
    # Positiv kontroll: listen ER signerbar, med den korrekte hashen —
    # 404-en var tenantgrensen, ikke en ødelagt fixture.
    egen = _bruker("sjef-tenantgrense", ["admin"])
    c2, cs2 = _browsersesjon(egen)
    ok = _post(klient, c2, cs2, sti, {"innhold_hash": ih})
    assert ok.status_code == 201, ok.text


@pg
def test_signering_avviser_manglende_csrf_og_idempotensnokkel(klient):
    """Cursor P2 (10:01): de to portene inngangen til den irreversible
    handlingen faktisk har, målt PÅ den ruten.

    `signer_endepunkt` går gjennom `_browserkontekst` (sesjon + CSRF) og
    `_krev_idem`, men HTTP-suiten dekket scope, medlemskap, fullmakt,
    hash, spiss, reaping, replay og kappløp — aldri at CSRF-porten og
    SP-2-nøkkelporten avviser HER. En regresjon i rutevalget (f.eks.
    bytte til `_autentiser` uten CSRF, eller et `_krev_idem` som ryker ut
    når rekkefølgen på portene endres — noe denne stabelen har gjort i
    fire runder) ville vært usynlig.

    Målt med status OG feilkode OG at signatur-sloten står ubrukt:
    «avvist» er ikke det samme som «skrev ingenting», og på en append-only
    tabell med én slot per serie er det andre det som teller. Den ekte
    signeringen til slutt er den positive kontrollen — uten den kunne
    begge avvisningene kommet av en ødelagt fixture.
    """
    from api import sesjon as sesjonmodul
    _pid, lid, ih = _seed_prosess()
    bid = _bruker("sjef-porter", ["admin"])
    cookie, csrf = _browsersesjon(bid)
    sti = f"/v1/rekruttering/lister/{lid}/signer"

    uten_csrf = klient.post(
        sti, json={"innhold_hash": ih},
        cookies={sesjonmodul.C_SESJON: cookie},
        headers={"Idempotency-Key": secrets.token_urlsafe(24)})
    assert uten_csrf.status_code == 403, uten_csrf.text
    assert uten_csrf.json()["feil"] == "csrf_ugyldig"
    # …og en TILSTEDEVÆRENDE, men feil token er samme dom: porten måler
    # verdien, ikke om hodet finnes.
    feil_csrf = klient.post(
        sti, json={"innhold_hash": ih},
        cookies={sesjonmodul.C_SESJON: cookie},
        headers={"X-Disponit-CSRF": secrets.token_urlsafe(24),
                 "Idempotency-Key": secrets.token_urlsafe(24)})
    assert feil_csrf.status_code == 403, feil_csrf.text
    assert feil_csrf.json()["feil"] == "csrf_ugyldig"

    uten_idem = klient.post(
        sti, json={"innhold_hash": ih},
        cookies={sesjonmodul.C_SESJON: cookie},
        headers={"X-Disponit-CSRF": csrf})
    assert uten_idem.status_code == 400, uten_idem.text
    assert uten_idem.json()["feil"] == "idempotensnokkel_mangler"
    # …og en nøkkel som bare er mellomrom er ingen nøkkel.
    blank_idem = klient.post(
        sti, json={"innhold_hash": ih},
        cookies={sesjonmodul.C_SESJON: cookie},
        headers={"X-Disponit-CSRF": csrf, "Idempotency-Key": "   "})
    assert blank_idem.status_code == 400, blank_idem.text
    assert blank_idem.json()["feil"] == "idempotensnokkel_mangler"

    m = _migrator()
    try:
        n = m.execute("SELECT count(*) FROM utsendingssignatur"
                      " WHERE tenant=%s AND liste_id=%s",
                      (TEN, lid)).fetchone()[0]
        assert n == 0, "en avvist forespørsel skrev en signatur"
        m.rollback()
    finally:
        m.close()
    # Positiv kontroll: den samme økten, med BEGGE hodene, signerer.
    ok = _post(klient, cookie, csrf, sti, {"innhold_hash": ih})
    assert ok.status_code == 201, ok.text


#: En oppdrag-id som ikke finnes. Rapportruta slår opp på den, så den er
#: målestokken for at modulporten ligger FØR oppslaget: 503 med flagget,
#: 404 uten.
_UKJENT_OID = 987654321


@pg
def test_deaktivert_m57_gir_definert_503_paa_alle_tre_rutene(miljo,
                                                             monkeypatch):
    """Rollback-kontrakten gjelder ALLE M-57s ruter (Codex P1).

    `DISPONIT_INAKTIVE_MODULER` er veien en modul rulles tilbake på i
    drift, og bare M-1s beslutningsvei leste den. Å deaktivere `m57_ats`
    stanset derfor ikke rekrutteringsflaten — signeringen inkludert, som
    er den irreversible handlingen en rollback finnes for å stoppe.

    Målt i BEGGE retninger: med flagget svarer alle sju rutene 503
    `modul_inaktiv` OG signatur-sloten står ubrukt; uten flagget signerer
    den samme økten 201 og de to leserutene svarer sitt eget (200/404).
    Uten den andre halvdelen ville testen bestått på et endepunkt som var
    permanent nede.

    Avvisningen ligger FØR autentiseringen, og økten her er en ekte
    admin-økt med gyldig CSRF: 503-en er da modulporten, ikke en 401 som
    tilfeldigvis kom først.

    STATUSEN MÅLES MOT `feil.FEIL`, ikke mot tallet 503 skrevet her.
    Modulen bruker ellers `policyadmin_http._feil`, som har sin EGEN
    lokale statustabell og faller til 409 for koder den ikke kjenner —
    første versjon av porten svarte derfor `modul_inaktiv` med 409, en
    «konflikt, prøv noe annet» om en modul som er slått av. Leses tallet
    fra kontrakten, peker et framtidig avvik på seg selv i stedet for å
    bestå mot en kopi.
    """
    from starlette.testclient import TestClient
    from api import feil as feiltabell
    from api.app import lag_app
    forventet = feiltabell.FEIL["modul_inaktiv"].http
    assert forventet == 503, "rollback-kontrakten er 503"
    _pid, lid, ih = _seed_prosess()
    bid = _bruker("sjef-rollback", ["admin"])
    cookie, csrf = _browsersesjon(bid)

    monkeypatch.setenv("DISPONIT_INAKTIVE_MODULER", "m57_ats")
    av = lag_app(DSN)
    try:
        with TestClient(av) as c:
            svar = [
                _get(c, cookie, "/v1/rekruttering/prosesser"),
                _post(c, cookie, csrf,
                      f"/v1/rekruttering/lister/{lid}/signer",
                      {"innhold_hash": ih}),
                _post(c, cookie, csrf,
                      f"/v1/rekruttering/prosesser/{_pid}/blinding",
                      {"av": True, "begrunnelse": "test"}),
                # #189-rutene hører til samme modul og samme rollback-
                # kontrakt (Cursor P2-5 på #206).
                _get(c, cookie, "/v1/rekruttering/stillingsprofiler"),
                _post(c, cookie, csrf,
                      "/v1/rekruttering/stillingsprofiler",
                      {"navn": "R",
                       "krav": [{"kravnavn": "K", "vekt": 1}]}),
                # M-57s EGEN rapportflate (#220) hører til samme modul og
                # samme rollback-kontrakt (Codex P1). Rutene bor i
                # `lesing.py`, ikke i `rekruttering.py`, og gikk derfor
                # utenom porten: en rollback stanset resten av flaten mens
                # historikken og de DEKRYPTERTE rapportene sto åpne.
                _get(c, cookie, "/v1/rekruttering/evalueringer"),
                _get(c, cookie, f"/v1/rekruttering/rapport/{_UKJENT_OID}"),
            ]
            assert [r.status_code for r in svar] == [forventet] * 7, \
                [r.text for r in svar]
            assert {r.json()["feil"] for r in svar} == {"modul_inaktiv"}
    finally:
        av.tjeneste.pool.lukk()
    m = _migrator()
    try:
        n = m.execute("SELECT count(*) FROM utsendingssignatur"
                      " WHERE tenant=%s AND liste_id=%s",
                      (TEN, lid)).fetchone()[0]
        assert n == 0, "en deaktivert modul skrev en signatur"
        # …og #189-ruten måles med SAMME negative bevis (Cursor P2-3):
        # 503 alene sier bare hva klienten SÅ, ikke at basen står urørt.
        p = m.execute("SELECT count(*) FROM stillingsprofil"
                      " WHERE tenant=%s AND navn='R'",
                      (TEN,)).fetchone()[0]
        assert p == 0, "en deaktivert modul skrev en stillingsprofil"
        m.rollback()
    finally:
        m.close()

    monkeypatch.delenv("DISPONIT_INAKTIVE_MODULER")
    paa = lag_app(DSN)
    try:
        with TestClient(paa) as c:
            r = _post(c, cookie, csrf,
                      f"/v1/rekruttering/lister/{lid}/signer",
                      {"innhold_hash": ih})
            assert r.status_code == 201, r.text
            # ...og de to leserutene svarer sitt EGNE svar uten flagget:
            # listen 200, den ukjente rapporten 404 `ikke_funnet`. Uten
            # denne halvdelen ville 503-armen over bestått på to ruter som
            # var permanent nede — og 404-en er dessuten beviset på at
            # porten ligger FØR oppslaget: med flagget svarte NØYAKTIG
            # samme id 503, altså uten å ha rørt basen.
            liste = _get(c, cookie, "/v1/rekruttering/evalueringer")
            assert liste.status_code == 200, liste.text
            ukjent = _get(c, cookie,
                          f"/v1/rekruttering/rapport/{_UKJENT_OID}")
            assert ukjent.status_code == 404, ukjent.text
            assert ukjent.json()["feil"] == "ikke_funnet"
    finally:
        paa.tjeneste.pool.lukk()


@pg
def test_svaret_vokser_ikke_med_antall_prosesser(klient):
    """#183 (Codex P2 fra #176): flaten lastet ALT.

    `GET /v1/rekruttering/prosesser` kalte `_kandidater` og `_lister` for
    HVER ureapet prosess. Katalogens løfte er 5000 søknader per bestilling,
    og prosessraden lever til slettefristen — inntil 365 døgn. Én GET kunne
    dermed skanne og serialisere titusener av funn- og spørsmålspayloader,
    holde en pool-forbindelse hele veien, og i verste fall ta knekken på
    worker-minnet. Det er ikke en spesialkonstruert forespørsel; det er
    flaten som åpnes.

    Målingen issuet ber om: to prosesser med kandidater, og svaret uten
    `prosess_id` skal ikke bære den andres kandidater.

    MUTASJONEN SOM DREPER DENNE: kall `_kandidater` for hver rad igjen.
    """
    gammel, _r1, _h1 = _seed_prosess()
    ny, _r2, _h2 = _seed_prosess()
    bruker = _bruker("les-183", ["leser"])
    cookie, _ = _browsersesjon(bruker)

    # EIERS FUNN 30/8: et FERDIG løp auto-vises aldri — begge seedene
    # her er utfort, så standardvalget skal ikke være noen av dem
    # (aktiv-ellers-ingen; delt base kan bære en fremmed aktiv).
    r0 = _get(klient, cookie, "/v1/rekruttering/prosesser")
    assert r0.status_code == 200, r0.text
    assert r0.json()["valgt_prosess_id"] not in (str(gammel), str(ny)), \
        "et utfort løp valgte seg selv — gamle rapporter stabler seg"

    # #183-invarianten måles på det EKSPLISITTE valget: ÉN bærer data.
    r = _get(klient, cookie,
             f"/v1/rekruttering/prosesser?prosess_id={ny}")
    assert r.status_code == 200, r.text
    svar = r.json()
    etter = {p["prosess_id"]: p for p in svar["prosesser"]}
    assert str(gammel) in etter and str(ny) in etter, \
        "begge prosessene skal stå i indeksen — velgeren trenger dem"
    med_data = [pid for pid, p in etter.items() if "kandidater" in p]
    assert med_data == [str(ny)] == [svar["valgt_prosess_id"]], \
        f"flere enn den valgte bar kandidater: {med_data}"
    assert not etter[str(gammel)].get("kandidater"), \
        "den andre prosessens kandidater ble serialisert likevel — det er" \
        " nettopp veksten #183 finnes for"

    # Indeksen bærer det velgeren trenger, og ikke mer (+ oppdraget,
    # som 071-slettingen i dypdykket går gjennom).
    lett = etter[str(gammel)]
    assert set(lett) == {"prosess_id", "oppdrag_id", "opprettet",
                         "evaluering_status", "kandidat_antall"}, \
        f"indeksraden bærer felter velgeren ikke bruker: {sorted(lett)}"


@pg
def test_indeksraden_teller_kandidatene_sine(klient):
    """Velgeretiketten skal ikke lyve om antallet (Cursor P2, #183).

    Etiketten er «Startet <dato> · kandidater: N», og N ble lest av
    `kandidater`-listen — som etter #183 bare finnes på den VALGTE raden.
    Hver andre prosess i nedtrekket sa derfor «kandidater: 0» om en
    prosess som kan bære tusenvis. Det er ikke en manglende opplysning;
    det er en gal en, på den ene kontrollen som skiller prosessene fra
    hverandre foran en irreversibel signering.

    Tallet er en SKALAR per rad — ikke payloaden tilbake — og må måle
    NØYAKTIG det `_kandidater` selv ville levert.

    MUTASJONEN SOM DREPER DENNE: la `kandidat_antall` falle bort fra
    indeksraden, eller tell uten `slettet_ts IS NULL`.
    """
    gammel, _r1, _h1 = _seed_prosess()
    _artefakt(gammel, {"drift": True, "sky": True})
    ny, _r2, _h2 = _seed_prosess()
    cookie, _ = _browsersesjon(_bruker("les-183d", ["leser"]))

    # Aktiv-ellers-ingen (eiers funn 30/8): begge seedene er utfort,
    # så etiketten måles på et EKSPLISITT valg.
    r = _get(klient, cookie,
             f"/v1/rekruttering/prosesser?prosess_id={ny}")
    assert r.status_code == 200, r.text
    svar = r.json()
    etter = {p["prosess_id"]: p for p in svar["prosesser"]}
    assert svar["valgt_prosess_id"] == str(ny)

    indeks = etter[str(gammel)]
    assert "kandidater" not in indeks, \
        "indeksraden bar kandidatene likevel — veksten er tilbake"
    assert indeks["kandidat_antall"] > 0, \
        "indeksraden telte 0 kandidater på en prosess som har dem —" \
        " nøyaktig løgnen velgeretiketten sto for"

    # ... og tallet er det SAMME som den valgte radens egen liste gir, så
    # etiketten og tabellen ikke kan si ulike ting om samme prosess.
    rg = _get(klient, cookie,
              f"/v1/rekruttering/prosesser?prosess_id={gammel}")
    assert rg.status_code == 200, rg.text
    valgt = [p for p in rg.json()["prosesser"]
             if p["prosess_id"] == str(gammel)][0]
    assert valgt["kandidat_antall"] == len(valgt["kandidater"]), \
        "indeksens tall og den valgte radens liste er uenige"
    assert indeks["kandidat_antall"] == len(valgt["kandidater"]), \
        "indeksraden og den spisse raden teller ulikt"


@pg
def test_den_valgte_radens_antall_kan_ikke_motsi_sin_egen_liste(
        klient, monkeypatch):
    """Tallet og listen er to setninger — de skal likevel være ett svar.

    Codex P2 (#183): indekstellingen og `_kandidater` er TO setninger på
    en forbindelse som står på psycopg-standarden READ COMMITTED
    (`koble`: `autocommit=False`, intet isolasjonsnivå satt), så hver av
    dem tar sitt eget øyeblikksbilde. En `plukket` prosess får
    artefaktene sine skrevet etter hvert, og ett som committes mellom de
    to setningene står i `kandidater` uten å være med i
    `kandidat_antall`. Da sier velgerens etikett og tabellen under den
    ULIKE ting om samme prosess, i samme svar, på flaten der signeringen
    skjer — nøyaktig løgnen indekstallet ble innført for å fjerne.

    VINDUET GJØRES DETERMINISTISK, IKKE SIMULERT: `_kandidater` byttes ut
    med en som skriver ETT artefakt fra en ANNEN, committende forbindelse
    (`_artefakt`s egen form) før den kaller den ekte. Det er nøyaktig
    interleavingen, ikke en modell av den — skrivingen skjer i det ekte
    vinduet, mellom endepunktets to setninger.

    PREMISSET MÅLES FØRST: står nykommeren i den valgte radens liste, ER
    vinduet åpent på denne forbindelsen. Gjør den ikke det, feiler testen
    på premisset i stedet for å bli grønn på feil grunnlag.

    MUTASJONEN SOM DREPER DENNE: la den valgte raden beholde
    indeksspørringens `antall` i stedet for `len(kandidater)`.
    """
    from api import rekruttering as rekrutteringsmodul

    prosess, _r, _h = _seed_prosess()
    cookie, _ = _browsersesjon(_bruker("les-183f", ["leser"]))

    ekte = rekrutteringsmodul._kandidater
    skrevet = {}

    def _skriver_i_vinduet(conn, tenant, prosess_id):
        if not skrevet:
            skrevet["kid"] = _artefakt(str(prosess_id),
                                       {"drift": True, "sky": False})
        return ekte(conn, tenant, prosess_id)

    monkeypatch.setattr(rekrutteringsmodul, "_kandidater", _skriver_i_vinduet)

    r = _get(klient, cookie,
             f"/v1/rekruttering/prosesser?prosess_id={prosess}")
    assert r.status_code == 200, r.text
    valgt = [p for p in r.json()["prosesser"]
             if p["prosess_id"] == str(prosess)][0]
    assert skrevet, "vinduet ble aldri åpnet — testen måler ikke det den tror"
    assert skrevet["kid"] in {k["kandidat_id"] for k in valgt["kandidater"]}, \
        "artefaktet som ble committet i vinduet kom ikke med i listen —" \
        " forbindelsen leser ikke READ COMMITTED, og premisset holder ikke"
    assert valgt["kandidat_antall"] == len(valgt["kandidater"]), \
        "den valgte radens etikett og dens egen tabell er uenige om" \
        " antallet — velgeren motsier innholdet under seg"


@pg
def test_den_valgte_prosessen_hentes_paa_id(klient):
    """Velgeren skal kunne be om en ANNEN prosess enn den nyeste.

    Uten dette var flaten låst til `prosesser[0]` igjen — den feilen Codex
    alt felte i #176 — bare med et lettere svar.
    """
    gammel, _r1, _h1 = _seed_prosess()
    _ny, _r2, _h2 = _seed_prosess()
    cookie, _ = _browsersesjon(_bruker("les-183b", ["leser"]))

    r = _get(klient, cookie,
             f"/v1/rekruttering/prosesser?prosess_id={gammel}")
    assert r.status_code == 200, r.text
    svar = r.json()
    assert svar["valgt_prosess_id"] == str(gammel)
    valgt = [p for p in svar["prosesser"]
             if p["prosess_id"] == str(gammel)][0]
    assert valgt["kandidater"], "den etterspurte prosessen bar ingen data"


@pg
def test_ukjent_prosess_id_er_ikke_funnet_ikke_den_nyeste(klient):
    """En ukjent id skal ikke bli til «ta den nyeste».

    Å servere en annen prosess' kandidater under den id-en klienten ba om,
    er en løgn flaten ikke kan oppdage — og prosessen KAN være borte helt
    lovlig: fristen løp ut mellom to klikk. Samme doktrine som rapportveien:
    identisk 404, uansett om den aldri fantes eller nettopp falt ut.

    MUTASJONEN SOM DREPER DENNE: fall tilbake til nyeste når id-en ikke
    finnes.
    """
    _seed_prosess()
    cookie, _ = _browsersesjon(_bruker("les-183c", ["leser"]))
    r = _get(klient, cookie,
             "/v1/rekruttering/prosesser"
             "?prosess_id=00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404, \
        f"en ukjent prosess-id ga data i stedet for 404: {r.text}"


def test_kandidatkortet_avmaskerer_og_fristen_stenger(klient):
    """Kandidatkortet (eiers bestilling 30/8): koblingen tilbake fra
    kandidatnummeret — avmaskerte felter + originaldokumentenes navn —
    bak flatens lesescope, med rapportrutens fristgrense, og med
    skrivedørens uuid5-form for buntens egne id-er.

    MUTASJONEN SOM DREPER DENNE: fjern fristleddet i
    `kandidatkort_endepunkt`, eller slutt å filtrere ikke-strenger fra
    `felter`."""
    from api.app import _KANDIDAT_NS

    pid, _lid, _ih = _seed_prosess()
    m = _migrator()
    try:
        from db.pg import sett_kontekst
        sett_kontekst(m, TEN, "test", "r-kort")
        oid = m.execute(
            "SELECT oppdrag_id FROM rekrutteringsprosess"
            " WHERE tenant=%s AND prosess_id=%s", (TEN, pid)).fetchone()[0]
        # Buntens egen id → lagerets uuid5 (skrivedørens form, ordrett).
        kid = uuid.uuid5(_KANDIDAT_NS, f"{TEN}\x1f{pid}\x1fkandidat-77")
        m.execute(
            "INSERT INTO kandidat (tenant, prosess_id, kandidat_id)"
            " VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (TEN, pid, kid))
        m.execute(
            "INSERT INTO kandidat_avmaskering (tenant, prosess_id,"
            " kandidat_id, felter, innhold_sha256) VALUES (%s,%s,%s,"
            # Ett ikke-streng-felt med vilje: 057 har ingen formsjekk,
            # og kortet skal aldri servere det som ikke er et kart av
            # strenger.
            " '{\"[NAVN-1]\": \"Kari Nordmann\","
            "   \"[KONTAKT-1]\": \"kari@eksempel.no\","
            "   \"[ALDER-1]\": 42}', 'h')", (TEN, pid, kid))
        did = uuid.uuid4()
        m.execute(
            "INSERT INTO kandidat_originaldokument (tenant, prosess_id,"
            " kandidat_id, dokument_id, filnavn, innholdstype, dokument,"
            " storrelse_bytes, innhold_sha256) VALUES"
            " (%s,%s,%s,%s,'cv.pdf','text/html',%s,4,%s)",
            (TEN, pid, kid, did, b"%PDF", "c" * 64))
        m.commit()
    finally:
        m.close()

    bid = _bruker("leser-kort", ["leser"])
    cookie, _ = _browsersesjon(bid)
    # Begge id-formene svarer: buntens egen og lagerets uuid.
    for form in ("kandidat-77", str(kid)):
        r = _get(klient, cookie, f"/v1/rekruttering/kandidatkort/{oid}/{form}")
        assert r.status_code == 200, r.text
        k = r.json()
        assert k["felter"] == {"[NAVN-1]": "Kari Nordmann",
                               "[KONTAKT-1]": "kari@eksempel.no"}, \
            "ikke-streng-feltet skulle vært filtrert, strengene servert"
        assert k["dokumenter"] == [{"dokument_id": str(did),
                                    "filnavn": "cv.pdf"}]
    # En kandidat uten lagrede rader er «finnes ikke» — aldri et tomt kort.
    assert _get(klient, cookie,
                f"/v1/rekruttering/kandidatkort/{oid}/kandidat-99"
                ).status_code == 404
    # DOKUMENTET BAK KORTET VISES — I SANDKASSE (eiers funn 31/8):
    # HTML serveres inline med `Content-Security-Policy: sandbox` (unik
    # origin, ingen skript, ingen sesjon) + nosniff; lagret skript i en
    # søknad kan aldri kjøre med appens fullmakter. Ukjent innholdstype
    # beholder nattens form: octet-stream + attachment — rendres aldri.
    rd = _get(klient, cookie,
              f"/v1/rekruttering/kandidatdokument/{oid}/{did}")
    assert rd.status_code == 200, rd.text
    assert rd.content == b"%PDF"
    assert rd.headers["content-type"].startswith("text/html")
    assert rd.headers["content-disposition"] == 'inline; filename="cv.pdf"'
    assert rd.headers["content-security-policy"] == "sandbox", \
        "HTML uten CSP-sandkasse er lagret XSS med sesjonskaken"
    assert rd.headers["x-content-type-options"] == "nosniff"
    # Ukjent innholdstype rendres aldri: attachment + octet-stream.
    m2 = _migrator()
    try:
        from db.pg import sett_kontekst as _ktx2
        _ktx2(m2, TEN, "test", "r-kort-dok")
        did2 = uuid.uuid4()
        m2.execute(
            "INSERT INTO kandidat_originaldokument (tenant, prosess_id,"
            " kandidat_id, dokument_id, filnavn, innholdstype, dokument,"
            " storrelse_bytes, innhold_sha256) VALUES"
            " (%s,%s,%s,%s,'ukjent.bin','application/x-ukjent',%s,3,%s)",
            (TEN, pid, kid, did2, b"abc", "d" * 64))
        m2.commit()
    finally:
        m2.close()
    ru = _get(klient, cookie,
              f"/v1/rekruttering/kandidatdokument/{oid}/{did2}")
    assert ru.headers["content-type"].startswith("application/octet-stream")
    assert ru.headers["content-disposition"].startswith("attachment;")
    # Ukjent dokument er «finnes ikke».
    assert _get(klient, cookie,
                f"/v1/rekruttering/kandidatdokument/{oid}/{uuid.uuid4()}"
                ).status_code == 404
    # Fristen stenger kortet FØR reaperens batch (samme grense som
    # rapporten) — persondata skal aldri leve på reaperens etterslep.
    _forbi_frist(pid)
    assert not _merket(pid)
    assert _get(klient, cookie,
                f"/v1/rekruttering/kandidatkort/{oid}/kandidat-77"
                ).status_code == 404
    assert _get(klient, cookie,
                f"/v1/rekruttering/kandidatdokument/{oid}/{did}"
                ).status_code == 404, "fristen skal stenge dokumentet også"


def test_profilsletting_er_enveis_skjuling(klient):
    """Slett for stillingsprofiler (eiers bestilling 30/8, 074): raden
    forlater listen (og dermed Ny bestilling), versjonene består i
    basen (rapportene refererer dem), merket er enveis, og idempotent
    re-sletting er et stille ja.

    MUTASJONEN SOM DREPER DENNE: fjern `skjult_ts IS NULL`-leddet i
    liste-spørringen, eller enveis-armen i 074-vakten."""
    m = _migrator()
    try:
        from db.pg import sett_kontekst
        sett_kontekst(m, TEN, "test", "r-profil")
        puid = uuid.uuid4()
        m.execute(
            "INSERT INTO stillingsprofil (tenant, profil_id, versjon,"
            " navn, opprettet_av, operasjonsnokkel, innhold_hash)"
            " VALUES (%s,%s,1,'Sletteprofil','test',%s,'h')",
            (TEN, puid, secrets.token_hex(8)))
        m.execute(
            "INSERT INTO stillingsprofil_krav (tenant, profil_id,"
            " versjon, rekkefolge, kravnavn, vekt)"
            " VALUES (%s,%s,1,1,'drift',5)", (TEN, puid))
        m.commit()
    finally:
        m.close()

    sjef = _bruker("profil-sjef", ["admin"])
    cookie, csrf = _browsersesjon(sjef)
    for_sletting = _get(klient, cookie, "/v1/rekruttering/stillingsprofiler")
    assert any(p["profil_id"] == str(puid)
               for p in for_sletting.json()["profiler"]), \
        "positiv kontroll: profilen skal stå i listen før slettingen"
    r = _post(klient, cookie, csrf,
              f"/v1/rekruttering/stillingsprofil/{puid}/slett", {})
    assert r.status_code == 200, r.text
    assert r.json()["slettet"] is True
    etter = _get(klient, cookie, "/v1/rekruttering/stillingsprofiler")
    assert all(p["profil_id"] != str(puid)
               for p in etter.json()["profiler"]), \
        "profilen skal forlate listen"
    # Idempotent: en alt slettet profil er et stille ja.
    assert _post(klient, cookie, csrf,
                 f"/v1/rekruttering/stillingsprofil/{puid}/slett",
                 {}).status_code == 200
    # Ukjent profil er «finnes ikke».
    assert _post(klient, cookie, csrf,
                 f"/v1/rekruttering/stillingsprofil/{uuid.uuid4()}/slett",
                 {}).status_code == 404

    m = _migrator()
    try:
        from db.pg import sett_kontekst
        sett_kontekst(m, TEN, "test", "r-profil2")
        # Radene består (rapportenes vekt-referanse lever videre) …
        krav = m.execute(
            "SELECT count(*) FROM stillingsprofil_krav"
            " WHERE tenant=%s AND profil_id=%s", (TEN, puid)).fetchone()[0]
        assert krav == 1, "kravradene skal bestå etter sletting"
        # … og merket er ENVEIS: heller ikke migrator får nullet det.
        import psycopg as psy
        with pytest.raises(psy.errors.InsufficientPrivilege):
            m.execute(
                "UPDATE stillingsprofil SET skjult_ts = NULL"
                " WHERE tenant=%s AND profil_id=%s", (TEN, puid))
        m.rollback()
        # Append-only for alt annet står ubrutt (kun skjuling kan skrives).
        with pytest.raises(psy.errors.InsufficientPrivilege):
            _sett = sett_kontekst(m, TEN, "test", "r-profil3")
            m.execute(
                "UPDATE stillingsprofil SET navn='Omdøpt'"
                " WHERE tenant=%s AND profil_id=%s", (TEN, puid))
        m.rollback()
    finally:
        m.close()

    # SKJULINGEN ER PROFILENS (CodeRabbit): runtime har ingen rå UPDATE
    # i det hele tatt — en enkeltversjons-skjuling kan ikke uttrykkes.
    # Døren er den ENESTE veien, og den tar hele profilen.
    from db.pg import koble, sett_kontekst as _ktx
    rt = koble(DSN)
    try:
        _ktx(rt, TEN, "test", "r-profil4")
        import psycopg as psy
        with pytest.raises(psy.errors.InsufficientPrivilege):
            rt.execute(
                "UPDATE stillingsprofil SET skjult_ts = now()"
                " WHERE tenant=%s AND profil_id=%s AND versjon=1",
                (TEN, puid))
        rt.rollback()
    finally:
        rt.close()


@pg
def test_160_utsendingstekstene_er_kundeeide_og_versjonerte(klient):
    """#160 (klarsignalet §6): kundens tone bor i et eget, versjonert
    lager — forfattet gjennom døren, lest av resolveren, aldri skrevet
    av en modul eller en modell. Speiler stillingsprofil-kontrakten:
    idempotent lagring, redigering = ny versjon, slett = enveis
    skjuling, append-only for alt annet."""
    sjef = _bruker("tekst-sjef", ["admin"])
    cookie, csrf = _browsersesjon(sjef)
    r = _post(klient, cookie, csrf, "/v1/rekruttering/utsendingstekster",
              {"navn": "Standardhilsen", "tekst": "Med vennlig hilsen oss"},
              idem="tekst-" + secrets.token_hex(6))
    assert r.status_code == 201, r.text
    tid = r.json()["tekst_id"]
    assert r.json()["versjon"] == 1
    # Redigering er en NY versjon.
    r2 = _post(klient, cookie, csrf, "/v1/rekruttering/utsendingstekster",
               {"tekst_id": tid, "navn": "Standardhilsen",
                "tekst": "Hilsen oss – v2"},
               idem="tekst-" + secrets.token_hex(6))
    assert r2.status_code == 201, r2.text
    assert r2.json()["versjon"] == 2
    rl = _get(klient, cookie, "/v1/rekruttering/utsendingstekster")
    rad = next(t for t in rl.json()["tekster"] if t["tekst_id"] == tid)
    assert rad["versjon"] == 2 and rad["tekst"] == "Hilsen oss – v2"
    # Resolveren bærer tonen som TYPE — og eksakt versjon slås opp.
    m = _migrator()
    try:
        from db.pg import sett_kontekst
        from api.rekruttering import hent_firmatekst
        sett_kontekst(m, TEN, "test", "r-tone")
        tone = hent_firmatekst(m, TEN, tid)
        assert tone.versjon == 2 and tone.tekst == "Hilsen oss – v2"
        v1 = hent_firmatekst(m, TEN, tid, versjon=1)
        assert v1.tekst == "Med vennlig hilsen oss"
        # … og resultatet flettes bare gjennom typen (#160-porten i
        # maler-suiten tar strengveien; her måles hele kjeden).
        from modules.m57_ats import maler
        ut = maler.flett("invitasjon",
                         {"stilling": "Utvikler", "kandidatnavn": "K",
                          "tidsvalg_lenke": "https://x/t"},
                         firmatekst=tone)
        assert "Hilsen oss – v2" in ut["tekst"]
        assert ut["firmatekst_ref"] == tid and ut["firmatekst_versjon"] == 2
        # Append-only: selv migrator får ikke redigere en versjon.
        import psycopg as psy
        with pytest.raises(psy.errors.InsufficientPrivilege):
            m.execute("UPDATE utsendingstekst SET tekst='hacket'"
                      " WHERE tenant=%s AND tekst_id=%s", (TEN, tid))
        m.rollback()
    finally:
        m.close()
    # Slett = skjuling: raden forlater listen; versjonene består for
    # sendte utsendelsers referanser.
    assert _post(klient, cookie, csrf,
                 f"/v1/rekruttering/utsendingstekst/{tid}/slett",
                 {}).status_code == 200
    etter = _get(klient, cookie, "/v1/rekruttering/utsendingstekster")
    assert all(t["tekst_id"] != tid for t in etter.json()["tekster"])
    m = _migrator()
    try:
        from db.pg import sett_kontekst
        from api.rekruttering import hent_firmatekst
        sett_kontekst(m, TEN, "test", "r-tone2")
        assert hent_firmatekst(m, TEN, tid) is None, \
            "siste-versjon-oppslaget skal respektere skjulingen"
        assert hent_firmatekst(m, TEN, tid, versjon=2).tekst \
            == "Hilsen oss – v2", "eksakte referanser lever videre"
    finally:
        m.close()


@pg
def test_165_listen_innstilles_fra_rapporten_med_manifest(klient):
    """#149/080: POST /v1/rekruttering/lister — flaten sender buntens
    kandidat-id-er, endepunktet mapper til lagerets uuid5-form, døren
    skriver manifestet og UTLEDER innhold_hash. Idempotensnøkkelen eier
    serien: samme nøkkel + samme medlemskap er et gjenspill (200, samme
    liste), annet medlemskap på samme nøkkel er en konflikt (409)."""
    import uuid as uuidmod

    from api.app import _KANDIDAT_NS
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
        m.execute("UPDATE oppdrag SET status='plukket' WHERE tenant=%s"
                  " AND id=%s", (TEN, oid))
        m.commit()
        from db.pg import koble, sett_kontekst
        rt = koble(DSN)
        try:
            sett_kontekst(rt, TEN, "test", "r165")
            pid = rt.execute(
                "SELECT opprett_rekrutteringsprosess(%s,%s,90)",
                (TEN, oid)).fetchone()[0]
            # Kandidatene fødes under LAGERETS uuid5-nøkkel — nøyaktig
            # den formen endepunktet utleder av buntens id.
            for bunt_kid in ("kandidat-01", "kandidat-02"):
                rt.execute(
                    "SELECT opprett_kandidat(%s,%s,%s)",
                    (TEN, pid, uuidmod.uuid5(
                        _KANDIDAT_NS, f"{TEN}\x1f{pid}\x1f{bunt_kid}")))
            rt.commit()
        finally:
            rt.close()
        sett_kontekst(m, TEN, "test", "r165c")
        m.execute("UPDATE oppdrag SET status='utfort' WHERE tenant=%s"
                  " AND id=%s", (TEN, oid))
        m.commit()
    finally:
        m.close()
    sjef = _bruker("innstiller", ["admin"])
    cookie, csrf = _browsersesjon(sjef)
    nokkel = "liste-" + secrets.token_hex(6)
    r = _post(klient, cookie, csrf, "/v1/rekruttering/lister",
              {"oppdrag_id": oid, "listetype": "invitasjon",
               "kandidater": ["kandidat-01", "kandidat-02"]}, idem=nokkel)
    assert r.status_code == 201, r.text
    svar = r.json()
    assert svar["antall"] == 2 and svar["listetype"] == "invitasjon"
    assert len(svar["innhold_hash"]) == 64
    m = _migrator()
    try:
        from db.pg import sett_kontekst
        sett_kontekst(m, TEN, "test", "r165b")
        medlemmer = {str(r2[0]) for r2 in m.execute(
            "SELECT kandidat_id FROM utsendingsliste_medlem"
            " WHERE tenant=%s AND liste_id=%s",
            (TEN, svar["liste_id"])).fetchall()}
        forventet = {str(uuidmod.uuid5(_KANDIDAT_NS,
                                       f"{TEN}\x1f{pid}\x1f{k}"))
                     for k in ("kandidat-01", "kandidat-02")}
        assert medlemmer == forventet, "manifestet matcher ikke utvalget"
    finally:
        m.close()
    # Gjenspill: samme nøkkel + samme medlemskap → samme liste, 200.
    r2 = _post(klient, cookie, csrf, "/v1/rekruttering/lister",
               {"oppdrag_id": oid, "listetype": "invitasjon",
                "kandidater": ["kandidat-02", "kandidat-01"]}, idem=nokkel)
    assert r2.status_code == 200, r2.text
    assert r2.json()["liste_id"] == svar["liste_id"]
    # Annet medlemskap på samme nøkkel er en KONFLIKT, aldri en ny liste.
    r3 = _post(klient, cookie, csrf, "/v1/rekruttering/lister",
               {"oppdrag_id": oid, "listetype": "invitasjon",
                "kandidater": ["kandidat-01"]}, idem=nokkel)
    assert r3.status_code == 409, r3.text
