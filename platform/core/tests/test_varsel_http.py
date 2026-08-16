"""Varselendepunktene over HTTP — der tjenestelaget møter en ekte forespørsel.

`test_varsel.py` prøver tjenesten med en forbindelse testen selv har satt opp.
Det er nyttig, men det er ikke slik flaten når den: der kommer forespørselen
gjennom auth, som ruller tilbake, og gjennom poolen, som ruller tilbake igjen
når forbindelsen leveres. Nøyaktig de to rollbackene er det Codex fant hull i,
og ingen av dem finnes i en test som holder forbindelsen selv.

Derfor kjøres disse ende-til-ende gjennom `klient`: ekte browserøkt, ekte
runtime-rolle, ekte pool. Det er det eneste stedet «svaret sa ok» og «det står
i basen etterpå» kan skilles fra hverandre.
"""
import secrets

import pytest

from .test_api import DSN, MIGRATOR_DSN, app, klient, miljo  # noqa: F401

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
TEN = "t-vhttp-" + secrets.token_hex(3)


def _migrator():
    from db.pg import koble, sett_kontekst
    m = koble(MIGRATOR_DSN)
    sett_kontekst(m, TEN, "sys", "r0")
    return m


def _forvalter(navn: str) -> str:
    """Identitet + `policyforvalter`-medlemskap i TEN. -> bruker_id.

    Medlemskapet er OIDC-forvaltet (runtime-rollen kan lese, ikke skrive), så
    det legges inn via migrator-forbindelsen — som i drift.
    """
    m = _migrator()
    try:
        bid = m.execute(
            "INSERT INTO brukeridentitet (issuer, sub) VALUES (%s,%s)"
            " ON CONFLICT (issuer,sub) DO UPDATE SET sub=EXCLUDED.sub"
            " RETURNING bruker_id",
            ("https://idp.example", f"{TEN}-{navn}")).fetchone()[0]
        m.execute(
            "INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
            " VALUES (%s,%s,ARRAY['policyforvalter'])"
            " ON CONFLICT (tenant,bruker_id) DO UPDATE SET"
            " roller=EXCLUDED.roller, aktiv=true", (TEN, bid))
        m.commit()
        return bid
    finally:
        m.close()


def _browsersesjon(bid: str):
    """En EKTE browserøkt for TEN+bid. -> (sesjonscookie, csrf-token)."""
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


def _legg_inn_varsel(bid: str, uid: str) -> int:
    m = _migrator()
    try:
        vid = m.execute(
            "INSERT INTO varsel (tenant, bruker_id, art, ressurs_type,"
            " ressurs_id, hendelse, tekstnokkel)"
            " VALUES (%s,%s,'attestering_venter','policyutkast',%s,'1',"
            " 'varsel.attestering_venter') RETURNING id",
            (TEN, bid, uid)).fetchone()[0]
        m.commit()
        return vid
    finally:
        m.close()


@pg
def test_innboksen_ser_egne_varsler_gjennom_endepunktet(klient):
    """Codex P1: tenantkonteksten må gjenopprettes etter at auth rullet tilbake.

    `sett_kontekst` er `SET LOCAL` — den dør med `conn.rollback()` inne i
    auth-hjelperen. Med FORCE RLS på `varsel` betyr en uunsatt
    `disponit.tenant` at hver rad er usynlig: svaret blir 200 med en TOM
    innboks, som ser ut som «ingenting venter på deg» og ikke som en feil. Det
    er den verste formen en varslingsfeil kan ta.

    MUTASJONEN SOM DREPER DENNE: fjern `_gjenopprett_kontekst`-kallet fra
    `_browserkontekst`/`_leseauth`.
    """
    from api import sesjon as sesjonmodul
    bid = _forvalter("innboks")
    uid = "u-" + secrets.token_hex(6)
    _legg_inn_varsel(bid, uid)
    cookie, csrf = _browsersesjon(bid)

    # INGEN CSRF-header: `hentJson` sender bare `Accept`, og det er slik flaten
    # faktisk spør. Se `test_innboksen_krever_ikke_csrf_...` under.
    r = klient.get("/v1/varsel", cookies={sesjonmodul.C_SESJON: cookie})

    assert r.status_code == 200, r.text
    body = r.json()
    assert [v["ressurs_id"] for v in body["varsler"]] == [uid], (
        "innboksen er tom — RLS ser ingen tenant, og flaten forteller "
        f"brukeren at ingenting venter: {body}")
    assert body["uleste"] == 1
    assert body["kanal"] == "epost_og_portal"


@pg
def test_innboksen_krever_ikke_csrf_men_mutasjonene_gjoer(klient):
    """Codex P2: GET-en er lesende og skal ikke gates på CSRF — POST-ene skal.

    CSRF-vernet finnes for å hindre at et annet nettsted får browseren til å
    UTFØRE noe med brukerens cookie. En liste over hva som venter på deg er ikke
    noe å utføre, og kravet var ikke gratis: `hentJson` sender bevisst bare
    `Accept`, så innboksen svarte 403 på en helt gyldig forespørsel — flaten
    kunne ikke laste den i det hele tatt.

    Begge halvdelene måles her. Uten den andre kunne funnet «fikses» ved å
    fjerne CSRF overalt, og det ville vært en sikkerhetsregresjon, ikke en fiks.

    MUTASJONEN SOM DREPER DENNE: sett `_browserkontekst` tilbake på GET-en (da
    dør første assert), eller bytt POST-ene til `_leseauth` (da dør den andre).
    """
    from api import sesjon as sesjonmodul
    bid = _forvalter("csrf")
    vid = _legg_inn_varsel(bid, "u-" + secrets.token_hex(6))
    cookie, _csrf = _browsersesjon(bid)
    ck = {sesjonmodul.C_SESJON: cookie}

    assert klient.get("/v1/varsel", cookies=ck).status_code == 200, \
        "lesende GET ble gatet på en header flaten ikke sender"

    # …men mutasjonene står fortsatt bak CSRF.
    assert klient.post(
        f"/v1/varsel/{vid}/lest",
        headers={"Idempotency-Key": secrets.token_hex(8)},
        cookies=ck).status_code == 403, "CSRF-vernet falt på en mutasjon"
    assert klient.post(
        "/v1/varselvalg", json={"kanal": "kun_portal"},
        headers={"Idempotency-Key": secrets.token_hex(8)},
        cookies=ck).status_code == 403, "CSRF-vernet falt på en mutasjon"


def _les(sql, *p):
    m = _migrator()
    try:
        return m.execute(sql, p).fetchone()
    finally:
        m.close()


@pg
def test_merk_lest_overlever_forespoerselen(klient):
    """Codex P1: et 200-svar er ikke et løfte om at noe ble lagret.

    `Tilkoblingspool.gi_tilbake` ruller UBETINGET tilbake — med vilje, så
    SET LOCAL og låser ikke følger med inn i neste tenants forespørsel. Uten en
    commit blir svaret altså sendt og skrivingen kastet i samme åndedrag.

    Derfor leses tilstanden her fra en HELT ANNEN forbindelse etter kallet. En
    assert på responsbodyen ville vært grønn også uten commit: den måler bare
    at UPDATE-en traff en rad, ikke at raden fortsatt er endret.

    MUTASJONEN SOM DREPER DENNE: bytt `_ok_lagret` mot `_ok` i endepunktet.
    """
    from api import sesjon as sesjonmodul
    bid = _forvalter("lest")
    vid = _legg_inn_varsel(bid, "u-" + secrets.token_hex(6))
    cookie, csrf = _browsersesjon(bid)

    r = klient.post(f"/v1/varsel/{vid}/lest",
                    headers={"X-Disponit-CSRF": csrf,
                             "Idempotency-Key": secrets.token_hex(8)},
                    cookies={sesjonmodul.C_SESJON: cookie})

    assert r.status_code == 200, r.text
    assert r.json()["lest"] is True
    assert _les("SELECT lest_ts FROM varsel WHERE id=%s", vid)[0] is not None, (
        "svaret sa «lest», men raden er urørt — mutasjonen ble aldri committet")


@pg
def test_varselvalget_overlever_forespoerselen(klient):
    """Samme P1 på den andre mutasjonen, og den verste av de to.

    En bruker som skrur AV e-postvarsler og får «lagret» i retur, men fortsetter
    å få e-post, har ikke bare mistet en innstilling — hun har mistet tilliten
    til at valget betyr noe.
    """
    from api import sesjon as sesjonmodul
    bid = _forvalter("kanal")
    cookie, csrf = _browsersesjon(bid)

    r = klient.post("/v1/varselvalg", json={"kanal": "kun_portal"},
                    headers={"X-Disponit-CSRF": csrf,
                             "Idempotency-Key": secrets.token_hex(8)},
                    cookies={sesjonmodul.C_SESJON: cookie})

    assert r.status_code == 200, r.text
    assert r.json()["kanal"] == "kun_portal"
    rad = _les("SELECT kanal FROM varselvalg WHERE tenant=%s AND bruker_id=%s",
               TEN, bid)
    assert rad is not None and rad[0] == "kun_portal", (
        f"valget ble ikke lagret ({rad}) — svaret lovte noe basen ikke holder")
