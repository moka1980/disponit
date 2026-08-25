"""#162 PR-2: resolveren — modulens lesevei, autorisert av CLAIMET (B-form).

Hele kjeden over HTTP: kunde reserverer+laster (PR-1-veien, det EKTE
(m57_ats, soknadsbunt)-paret — 058-CHECKen låser eiermodulen, så ingen
syntetisk modul finnes), bunten BINDES til oppdraget i oppdragets
FØDSELSTRANSAKSJON (X1, 059), og modulen henter via sitt EGET oppdrag
(#200 valg B): bindingsraden er den eneste sannheten om hvilken bunt
oppdraget eier — ingen payload-referanse finnes.

Rettens tilstandsside (o.status = 'plukket') settes deterministisk av
migrator i stedet for HTTP-claim: claim-endepunktet plukker «neste» på
tvers av alt basen har liggende, og et kappløp med andre suiters
etterlatte oppdrag ville målt kjørerekkefølgen, ikke resolveren. At
claim ER veien til 'plukket' bevises av claim-suitene; resolverens
predikat er modul-match + plukket med LEVENDE LEASE + bundet, og det er
DET som måles her. Negativene: opprettet 404, feil modul 404, ubundet
404, utløpt/manglende lease 404, browser 401 — samme svar uansett årsak.
"""
import hashlib
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, app, klient, migrator,  # noqa: F401
                       miljo)
from .test_inndata_http import (_opplast, _reserver, _rigg, _zipbytes,
                                inndata_rot)  # noqa: F401
from .test_modul_onboarding_http import _onboard_token

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _m57_deployment(conn):
    """En onboardbar deployment for den EKTE modulen `m57_ats`.

    Hodet og kontrakten er idempotente på tvers av kjøringer
    (`ON CONFLICT DO NOTHING` — ingen UPDATE, så append-only-vaktene er
    urørt); releasen/deploymenten er fersk per kjøring, og
    kontrakt-hashen leses fra basen slik den faktisk står."""
    conn.execute("INSERT INTO modulhode (modul_id,status)"
                 " VALUES ('m57_ats','aktiv') ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,kontrakt_hash,"
        "payload_schema_hash,kvittering_schema_hash,sideeffektklasse,"
        "reversibilitet) VALUES ('m57_ats',1,%s,'p','k','krever_outbox',"
        "'kompenserende') ON CONFLICT DO NOTHING",
        ("k-" + secrets.token_hex(8),))
    khash = conn.execute(
        "SELECT kontrakt_hash FROM modulkontrakt"
        " WHERE modul_id='m57_ats' AND kontraktversjon=1").fetchone()[0]
    # Onboardingen krever en registrert oppdragstype under releasens
    # kontrakt; registeret er append-only, så navnet er ferskt per kall.
    conn.execute(
        "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
        "kontraktversjon,kontrakt_hash) VALUES (%s,'m57_ats',1,%s)",
        (f"rekr.res.{secrets.token_hex(4)}", khash))
    # `en_claiming_per_kontrakt` tillater NØYAKTIG én claiming-deployment
    # per (modul, miljø, versjon, hash) — finnes den, gjenbrukes den.
    rad = conn.execute(
        "SELECT release_id FROM moduldeployment"
        " WHERE modul_id='m57_ats' AND miljo='staging'"
        " AND kontraktversjon=1 AND kontrakt_hash=%s"
        " AND livslop='claiming'", (khash,)).fetchone()
    if rad:
        conn.commit()
        return rad[0]
    rel = f"r57-{secrets.token_hex(6)}"
    conn.execute(
        "INSERT INTO modulrelease (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,manifest_hash,artifact_digest)"
        " VALUES ('m57_ats',%s,1,%s,'mh','ad')", (rel, khash))
    conn.execute(
        "INSERT INTO moduldeployment (modul_id,release_id,kontraktversjon,"
        "kontrakt_hash,miljo,livslop)"
        " VALUES ('m57_ats',%s,1,%s,'staging','claiming')", (rel, khash))
    conn.commit()
    return rel


def _bundet_bunt(klient, migrator, *, bind=True):
    """Reserver+last en bunt over HTTP (ekte par), og fød oppdraget med
    bindingen i SAMME transaksjon (X1, 059) via dørens eier — nøyaktig
    formen bestillingsveien (PR-3) skal ha. -> (kropp, tenant, oid)."""
    tenant, _bid, cookie, csrf = _rigg(klient)
    r = _reserver(klient, cookie, csrf)
    assert r.status_code == 201, r.text
    jti = r.json()["reservasjon_jti"]
    inndata_id = r.json()["inndata_ref"].split(":", 1)[1]
    kropp = _zipbytes()
    r2 = _opplast(klient, cookie, csrf, jti, kropp)
    assert r2.status_code == 201, r2.text

    from db import kryptering
    from db.pg import sett_kontekst
    sett_kontekst(migrator, tenant, "test", "r-res")
    logg = migrator.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'test','api_token','ih','p@1.0.0/x.y','TILLAT','[]',"
        " %s) RETURNING id", (tenant, secrets.token_hex(8))).fetchone()[0]
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator, tenant)
    ct, nonce = kryptering.krypter(dek, {"x": 1}, tenant, key_id)
    # Konsument-typen: bind_inndata krever formålets konsumerende
    # oppdragstype (rekruttering.evaluering for soknadsbunt).
    oid = migrator.execute(
        "INSERT INTO oppdrag (opprinnelse, tenant, beslutning_loggpost_id,"
        " oppdragstype, handling, eiermodul, payload_kryptert, key_id,"
        " nonce, utforelsesfrist, evidensfrist, koblingsstatus)"
        " VALUES ('beslutning',%s,%s,'rekruttering.evaluering',"
        "'rekruttering.evaluering','m57_ats',%s,%s,%s,"
        " now()+interval '4 hour', now()+interval '1 day','KOBLET')"
        " RETURNING id", (tenant, logg, ct, key_id, nonce)).fetchone()[0]
    if bind:
        migrator.execute("SET LOCAL ROLE disponit_domene_eier")
        migrator.execute("SELECT bind_inndata(%s,%s,%s,%s)",
                         (tenant, inndata_id, oid, "m57_ats"))
        migrator.execute("RESET ROLE")
    migrator.commit()
    return kropp, tenant, oid


def _pluk(migrator, tenant, oid):
    """Flipper til plukket og stempler claim-KAPABILITETEN (owner_claim_id
    — kolonnen er ikke claim-vaktens; formatkravet er claim-dørens
    ^[0-9a-f]{32,}$). Returnerer kapabiliteten kalleren må presentere.

    LEASEN SETTES OGSÅ (Codex P1, #202): claim-porten skriver alltid
    `owner_lease_utloper` (015:277, 037:192, 049:288), og resolveren
    krever nå at holdet fortsatt varer. En positiv sti som lot leasen
    stå NULL ville målt en tilstand claim-døren aldri produserer — og
    skjult nøyaktig det leddet den skal bevise."""
    from db.pg import sett_kontekst
    claim = secrets.token_hex(16)
    sett_kontekst(migrator, tenant, "test", "r-pluk")
    migrator.execute("UPDATE oppdrag SET status='plukket',"
                     " owner_claim_id=%s,"
                     " owner_lease_utloper=now()+interval '1 hour'"
                     " WHERE tenant=%s AND id=%s",
                     (claim, tenant, oid))
    migrator.commit()
    return claim


@pg
def test_resolveren_krever_plukket_oppdrag(klient, migrator, miljo,
                                           inndata_rot):
    rel = _m57_deployment(migrator)
    kropp, tenant, oid = _bundet_bunt(klient, migrator)
    mtk, _ = _onboard_token(klient, migrator, "m57_ats", rel)

    # FØR plukk: 404 — bindingen finnes, retten gjør det ikke (en
    # gjettet kapabilitet hjelper ikke: raden har ingen).
    r = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                    json={"owner_claim_id": secrets.token_hex(16)},
                    headers={"authorization": f"Bearer {mtk}"})
    assert r.status_code == 404, r.text

    claim = _pluk(migrator, tenant, oid)
    # Uten kapabiliteten i kroppen: 400 — kravet er del av kontrakten.
    r400 = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}", json={},
                       headers={"authorization": f"Bearer {mtk}"})
    assert r400.status_code == 400, r400.text
    # FEIL kapabilitet (riktig modul, riktig oppdrag): samme 404.
    rfeil = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                        json={"owner_claim_id": secrets.token_hex(16)},
                        headers={"authorization": f"Bearer {mtk}"})
    assert rfeil.status_code == 404, rfeil.text
    r2 = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                     json={"owner_claim_id": claim},
                     headers={"authorization": f"Bearer {mtk}"})
    assert r2.status_code == 200, r2.text
    assert r2.content == kropp
    assert r2.headers["x-innhold-sha256"] == \
        hashlib.sha256(kropp).hexdigest()

    # Browserøkten (ikke modultoken) er ikke en vei inn.
    from api import sesjon as sesjonmodul
    from .test_rekruttering_http import _browsersesjon, _bruker
    cookie, _csrf = _browsersesjon(_bruker("snoker", ["admin"]))
    r3 = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                     json={"owner_claim_id": claim},
                     cookies={sesjonmodul.C_SESJON: cookie})
    assert r3.status_code == 401


@pg
def test_utlopt_lease_er_ikke_lenger_en_rett(klient, migrator, miljo,
                                             inndata_rot):
    """Codex P1: etter `owner_lease_utloper` er raden reclaimbar, men
    `plukket`/`owner_claim_id` står urørt til noen tar den. I det
    vinduet skal den gamle holderens kapabilitet ikke lenger hente
    noe — samme 404 som «ikke claimet», ikke en egen feilklasse."""
    rel = _m57_deployment(migrator)
    kropp, tenant, oid = _bundet_bunt(klient, migrator)
    claim = _pluk(migrator, tenant, oid)
    mtk, _ = _onboard_token(klient, migrator, "m57_ats", rel)

    # Kontroll: med levende lease er dette 200 og byte-likt.
    r_ok = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                       json={"owner_claim_id": claim},
                       headers={"authorization": f"Bearer {mtk}"})
    assert r_ok.status_code == 200, r_ok.text
    assert r_ok.content == kropp

    # Leasen løper ut. Ingenting annet endres — status og kapabilitet
    # er nøyaktig de samme som i 200-svaret over.
    from db.pg import sett_kontekst
    sett_kontekst(migrator, tenant, "test", "r-utlop")
    migrator.execute("UPDATE oppdrag SET"
                     " owner_lease_utloper=now()-interval '1 second'"
                     " WHERE tenant=%s AND id=%s", (tenant, oid))
    migrator.commit()
    r = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                    json={"owner_claim_id": claim},
                    headers={"authorization": f"Bearer {mtk}"})
    assert r.status_code == 404, r.text

    # Og en rad uten lease i det hele tatt: fail-closed, ikke «ingen
    # frist = ingen utløp».
    sett_kontekst(migrator, tenant, "test", "r-utlop")
    migrator.execute("UPDATE oppdrag SET owner_lease_utloper=NULL"
                     " WHERE tenant=%s AND id=%s", (tenant, oid))
    migrator.commit()
    r2 = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                     json={"owner_claim_id": claim},
                     headers={"authorization": f"Bearer {mtk}"})
    assert r2.status_code == 404, r2.text


@pg
def test_feil_modul_og_ubundet_gir_samme_ingenting(klient, migrator,
                                                   miljo, inndata_rot):
    """Feil modul 404 og ubundet 404 — ingen orakel over hva som finnes.
    «Feil modul» måles med en syntetisk deployment: modultokenets
    modul_id er da aldri 'm57_ats', og resolverens eiermodul-match
    feller den uansett hvilken modulstreng den bærer."""
    from .test_modul_onboarding_http import _kjede
    rel = _m57_deployment(migrator)
    kropp, tenant, oid = _bundet_bunt(klient, migrator)
    claim = _pluk(migrator, tenant, oid)

    # Fremmed deployment (syntetisk modul-id) → 404 på samme oppdrag,
    # SELV MED riktig kapabilitet (eiermodul-porten feller først).
    modul2, rel2 = _kjede(migrator,
                          typenavn=f"rekr.x.{secrets.token_hex(4)}")
    mtk2, _ = _onboard_token(klient, migrator, modul2, rel2)
    r = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid}",
                    json={"owner_claim_id": claim},
                    headers={"authorization": f"Bearer {mtk2}"})
    assert r.status_code == 404, r.text

    # Ubundet, plukket oppdrag hos riktig modul → samme 404.
    _kropp2, tenant2, oid2 = _bundet_bunt(klient, migrator, bind=False)
    claim2 = _pluk(migrator, tenant2, oid2)
    mtk, _ = _onboard_token(klient, migrator, "m57_ats", rel)
    r2 = klient.post(f"/v1/inndata/hent-for-oppdrag/{oid2}",
                     json={"owner_claim_id": claim2},
                     headers={"authorization": f"Bearer {mtk}"})
    assert r2.status_code == 404, r2.text
