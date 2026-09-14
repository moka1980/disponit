"""Plattformeierens ruter over HTTP — der dørene i 199 møter en ekte økt.

`test_plattformeier_port.py` prøver dørene med en forbindelse testen selv
setter opp. Det er ikke slik flaten når dem: der kommer forespørselen gjennom
auth, som ruller tilbake, og gjennom poolen, som ruller tilbake igjen.

DET ER IKKE EN AKADEMISK FORSKJELL. Aktørbindingen i `plattform_krev_eier`
var først skrevet mot `'bruker:' || bid`, fordi `invitasjon.py` bruker den
formen. `_browserkontekst` sender den RÅ id-en. Alle portene sto grønne — de
satte konteksten på samme gale måte — og hver eneste ekte forespørsel ville
blitt avvist. En ende-til-ende-test er det eneste stedet den klassen dør.
"""
import secrets

import pytest

from .test_api import DSN, MIGRATOR_DSN, app, klient, miljo  # noqa: F401

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
TEN = "t-phttp-" + secrets.token_hex(3)


def _migrator():
    from db.pg import koble, sett_kontekst
    m = koble(MIGRATOR_DSN)
    sett_kontekst(m, TEN, "sys", "r0")
    return m


def _bruker(navn: str, *, plattformeier: bool) -> str:
    """Identitet + medlemskap i TEN, og eventuelt plattformfullmakt.

    Medlemskapet gir `policyforvalter`, som bærer `policy:read` — scopet
    rutene krever. Fullmakten er en HELT annen ting: raden i `plattformeier`.
    At en bruker kan ha det ene uten det andre er nettopp det som måles her.
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
        if plattformeier:
            m.execute(
                "INSERT INTO plattformeier (bruker_id, opprettet_av)"
                " VALUES (%s,'test') ON CONFLICT (bruker_id) DO NOTHING",
                (bid,))
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


def _rydd(tenanter):
    m = _migrator()
    try:
        for t in tenanter:
            m.execute("SELECT set_config('disponit.tenant',%s,true)", (t,))
            m.execute("DELETE FROM firma WHERE tenant=%s", (t,))
        m.commit()
    finally:
        m.close()


@pytest.fixture(autouse=True)
def _rydd_plattformeiere():
    """Fjerner fullmaktsradene denne FILA la inn.

    Uten dette lakk de ut til andre testfiler — og felte
    `test_tabellen_starter_tom_i_en_fersk_base`, som (den gang) talte rader i
    tabellen. To feil av samme rot: en test som forurenser, og en port som
    maalte basen i stedet for kilden. Begge er rettet.
    """
    yield
    m = _migrator()
    try:
        m.execute("DELETE FROM plattformeier WHERE opprettet_av='test'")
        m.commit()
    finally:
        m.close()


# ---------------------------------------------------------------------------
# Den porten som ville fanget aktørfeilen
# ---------------------------------------------------------------------------

@pg
def test_en_EKTE_okt_slipper_gjennom_aktorbindingen(klient):
    """DEN VIKTIGSTE TESTEN I FILA.

    Dørene binder `p_bruker_id` til `disponit.aktor`. Skrives bindingen med
    feil form, svarer HVER rute `ikke_plattformeier` til en bruker som ER
    plattformeier — i prod, etter grønn CI, fordi alle de andre portene
    setter konteksten selv.

    MUTASJON SOM FELLER: sett `'bruker:' || p_bruker_id` tilbake i
    `plattform_krev_eier`.
    """
    from api import sesjon as sesjonmodul
    bid = _bruker("eier", plattformeier=True)
    cookie, _csrf = _browsersesjon(bid)

    r = klient.get("/v1/plattform/meg",
                   cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 200, r.text
    assert r.json()["eier"] is True, (
        "en ekte plattformeier fikk «nei» gjennom en ekte økt — "
        "aktørbindingen stemmer ikke med det API-et faktisk setter")


@pg
def test_en_vanlig_bruker_er_ikke_plattformeier(klient):
    """POSITIV KONTROLL for testen over.

    Uten denne ville en `plattform_er_eier` som alltid sa `true` sett like
    grønn ut som den riktige.
    """
    from api import sesjon as sesjonmodul
    bid = _bruker("vanlig", plattformeier=False)
    cookie, _csrf = _browsersesjon(bid)
    r = klient.get("/v1/plattform/meg",
                   cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 200, r.text
    assert r.json()["eier"] is False


# ---------------------------------------------------------------------------
# Fullmakten, gjennom hele stacken
# ---------------------------------------------------------------------------

@pg
def test_en_uten_fullmakt_avvises_av_HVER_rute(klient):
    """Hun har scopet (`policyforvalter` bærer `policy:read`) og en gyldig
    økt. Det som mangler er raden i `plattformeier` — og det er nettopp
    poenget: scopet er ikke fullmakten.

    MUTASJON SOM FELLER: fjern `plattform_krev_eier` fra én dør.
    """
    from api import sesjon as sesjonmodul
    bid = _bruker("uten", plattformeier=False)
    cookie, csrf = _browsersesjon(bid)
    c = {sesjonmodul.C_SESJON: cookie}
    h = {"x-disponit-csrf": csrf}

    assert klient.get("/v1/plattform/firmaer", cookies=c).status_code == 403
    assert klient.post("/v1/plattform/firmaer", cookies=c, headers=h,
                       json={"tenant": "x-" + secrets.token_hex(4),
                             "navn": "N"}).status_code == 403
    assert klient.post("/v1/plattform/firmaer/uansett/oppdater", cookies=c,
                       headers=h, json={"navn": "N"}).status_code == 403
    assert klient.post("/v1/plattform/firmaer/uansett/status", cookies=c,
                       headers=h, json={"status": "aktiv"}).status_code == 403


@pg
def test_eieren_oppretter_ser_endrer_og_stenger(klient):
    """Hele eiers krav i én runde: opprette, se, redigere, «slette»."""
    from api import sesjon as sesjonmodul
    bid = _bruker("full", plattformeier=True)
    cookie, csrf = _browsersesjon(bid)
    c = {sesjonmodul.C_SESJON: cookie}
    h = {"x-disponit-csrf": csrf}
    nytt = "p199h-" + secrets.token_hex(4)
    try:
        r = klient.post("/v1/plattform/firmaer", cookies=c, headers=h,
                        json={"tenant": nytt, "navn": "Nytt AS"})
        assert r.status_code == 201, r.text
        assert r.json()["prove_utloper"], "prøvefristen kom ikke tilbake"

        r = klient.get("/v1/plattform/firmaer", cookies=c)
        assert r.status_code == 200, r.text
        rad = next(f for f in r.json()["firmaer"] if f["tenant"] == nytt)
        assert (rad["navn"], rad["status"]) == ("Nytt AS", "prove")

        r = klient.post(f"/v1/plattform/firmaer/{nytt}/oppdater", cookies=c,
                        headers=h, json={"navn": "Endret AS"})
        assert r.status_code == 200, r.text

        r = klient.post(f"/v1/plattform/firmaer/{nytt}/status", cookies=c,
                        headers=h, json={"status": "stengt"})
        assert r.status_code == 200, r.text

        r = klient.get("/v1/plattform/firmaer", cookies=c)
        rad = next(f for f in r.json()["firmaer"] if f["tenant"] == nytt)
        assert (rad["navn"], rad["status"]) == ("Endret AS", "stengt")
        # «Slett» er `stengt`, ikke DELETE — raden skal fortsatt finnes, ellers
        # er angrefristen i 190 en regel uten noe å virke på.
        assert rad["stengt"], "stengt-tidspunktet mangler"
    finally:
        _rydd([nytt])


@pg
def test_synet_gaar_paa_tvers_av_tenanter(klient):
    """Eieren står i ÉN tenant og skal likevel se firmaer i andre.

    MUTASJON SOM FELLER: fjern policyen `plattform_eier_ser_alle`.
    """
    from api import sesjon as sesjonmodul
    bid = _bruker("kryss", plattformeier=True)
    cookie, csrf = _browsersesjon(bid)
    c = {sesjonmodul.C_SESJON: cookie}
    h = {"x-disponit-csrf": csrf}
    a = "p199x-" + secrets.token_hex(4)
    b = "p199y-" + secrets.token_hex(4)
    try:
        for t in (a, b):
            assert klient.post("/v1/plattform/firmaer", cookies=c, headers=h,
                               json={"tenant": t, "navn": t}
                               ).status_code == 201
        sett = {f["tenant"] for f in
                klient.get("/v1/plattform/firmaer", cookies=c).json()["firmaer"]}
        assert {a, b} <= sett, "listen var begrenset til én tenant"
    finally:
        _rydd([a, b])


@pg
def test_en_ULOVLIG_overgang_avvises_med_409(klient):
    """190s statusmaskin eier dommen — ruten kopierer den ikke."""
    from api import sesjon as sesjonmodul
    bid = _bruker("overgang", plattformeier=True)
    cookie, csrf = _browsersesjon(bid)
    c = {sesjonmodul.C_SESJON: cookie}
    h = {"x-disponit-csrf": csrf}
    t = "p199z-" + secrets.token_hex(4)
    try:
        klient.post("/v1/plattform/firmaer", cookies=c, headers=h,
                    json={"tenant": t, "navn": "T"})
        # `prove → prove` står ikke i tabellen over lovlige overganger.
        r = klient.post(f"/v1/plattform/firmaer/{t}/status", cookies=c,
                        headers=h, json={"status": "prove"})
        assert r.status_code == 409, r.text
    finally:
        _rydd([t])


@pg
def test_et_firma_som_alt_finnes_gir_409(klient):
    """190: «registrering er ikke en oppdatering»."""
    from api import sesjon as sesjonmodul
    bid = _bruker("dobbel", plattformeier=True)
    cookie, csrf = _browsersesjon(bid)
    c = {sesjonmodul.C_SESJON: cookie}
    h = {"x-disponit-csrf": csrf}
    t = "p199d-" + secrets.token_hex(4)
    try:
        assert klient.post("/v1/plattform/firmaer", cookies=c, headers=h,
                           json={"tenant": t, "navn": "A"}).status_code == 201
        r = klient.post("/v1/plattform/firmaer", cookies=c, headers=h,
                        json={"tenant": t, "navn": "B"})
        assert r.status_code == 409, r.text
    finally:
        _rydd([t])


@pg
def test_mutasjonene_krever_CSRF(klient):
    """Uten CSRF er en POST noe et annet nettsted kan få browseren til å gjøre.

    MUTASJON SOM FELLER: bytt `_browserkontekst` mot `_leseauth` i en dør.
    """
    from api import sesjon as sesjonmodul
    bid = _bruker("csrf", plattformeier=True)
    cookie, _csrf = _browsersesjon(bid)
    r = klient.post("/v1/plattform/firmaer",
                    cookies={sesjonmodul.C_SESJON: cookie},
                    json={"tenant": "x-" + secrets.token_hex(4), "navn": "N"})
    assert r.status_code == 403, r.text
