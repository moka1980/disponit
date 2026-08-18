"""039 — selvbetjent domeneverifisering.

Snittet som måles: API-et kan bare UTSTEDE (aldri bekrefte — det kjente
tokenet), arbeiderfunksjonene beviser mot DB-holdt hash, og
statusoverganger eies fortsatt av `verifiser_domenekontroll`.
Alle tester konstruerer egen tilstand.
"""
import hashlib
import secrets

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, TENANT, migrator, miljo  # noqa: F401
from .test_api import dekker
from .test_m37 import _sett_kontekst
from .test_outbox_bestilling import (_adminsesjon, _rt, app,  # noqa: F401
                                     klient)

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _som_eier(migrator_, sql, args):
    migrator_.execute("SET LOCAL ROLE disponit_domene_eier")
    rad = migrator_.execute(sql, args).fetchone()
    migrator_.execute("RESET ROLE")
    return rad


def _utsted(migrator_, hostname, token=None):
    token = token or secrets.token_hex(32)
    h = hashlib.sha256(token.encode()).hexdigest()
    _sett_kontekst(migrator_, TENANT)
    _som_eier(migrator_, "SELECT utsted_challenge(%s,%s,false,%s,'test')",
              (TENANT, hostname, h))
    migrator_.commit()
    return token


@pg
def test_bekreft_krever_beviset_i_txt(migrator):
    """DB-en holder beviset: feil TXT → exception (ingen påstand om
    suksess); riktig TXT → verifisert; nytt kall → idempotent 'verifisert'
    (dobbeltplukk er et JA, aldri en dobbel overgang)."""
    vert = f"kunde{secrets.token_hex(3)}.example"
    token = _utsted(migrator, vert)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _som_eier(migrator, "SELECT bekreft_domenechallenge(%s,%s,'t',%s)",
                  (TENANT, vert, ["feil-verdi", "v=spf1 -all"]))
    migrator.rollback()
    _sett_kontekst(migrator, TENANT)
    svar = _som_eier(migrator,
                     "SELECT bekreft_domenechallenge(%s,%s,'t',%s)",
                     (TENANT, vert, ["v=spf1 -all", token]))[0]
    migrator.commit()
    assert svar == "verifisert"
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT status, siste_vellykkede_revalidering IS NOT NULL"
        " FROM domenekontroll WHERE tenant=%s AND hostname=%s",
        (TENANT, vert)).fetchone()
    svar2 = _som_eier(migrator,
                      "SELECT bekreft_domenechallenge(%s,%s,'t',%s)",
                      (TENANT, vert, ["hva-som-helst"]))[0]
    migrator.rollback()
    assert rad == ("verifisert", True), rad
    assert svar2 == "verifisert"


@pg
def test_utlopt_challenge_beviser_ingenting(migrator):
    vert = f"gammel{secrets.token_hex(3)}.example"
    token = _utsted(migrator, vert)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE domenekontroll SET"
                     " challenge_utloper=now()-interval '1 hour'"
                     " WHERE tenant=%s AND hostname=%s", (TENANT, vert))
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _som_eier(migrator, "SELECT bekreft_domenechallenge(%s,%s,'t',%s)",
                  (TENANT, vert, [token]))
    migrator.rollback()


@pg
def test_bekreft_overstyrer_aldri_en_avklaring(migrator):
    """En rad utenfor `ventende` (her: tilbakekalt) flyttes ALDRI av et
    DNS-bevis — bare M-37-avgjørelsen kan det. Svaret er status, ikke en
    overgang."""
    vert = f"laast{secrets.token_hex(3)}.example"
    token = _utsted(migrator, vert)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE domenekontroll SET status='tilbakekalt'"
                     " WHERE tenant=%s AND hostname=%s", (TENANT, vert))
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    svar = _som_eier(migrator,
                     "SELECT bekreft_domenechallenge(%s,%s,'t',%s)",
                     (TENANT, vert, [token]))[0]
    status = migrator.execute(
        "SELECT status FROM domenekontroll WHERE tenant=%s AND hostname=%s",
        (TENANT, vert)).fetchone()[0]
    migrator.rollback()
    assert svar == "tilbakekalt"
    assert status == "tilbakekalt"


@pg
def test_ventende_plukket_er_ferskt_og_lukket(migrator):
    v1 = f"fersk{secrets.token_hex(3)}.example"
    v2 = f"utgatt{secrets.token_hex(3)}.example"
    _utsted(migrator, v1)
    _utsted(migrator, v2)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE domenekontroll SET"
                     " challenge_utloper=now()-interval '1 hour'"
                     " WHERE tenant=%s AND hostname=%s", (TENANT, v2))
    migrator.commit()
    rader = [r[1] for r in _alle_ventende(migrator)]
    assert v1 in rader
    assert v2 not in rader, "utløpt challenge skal aldri plukkes"


def _alle_ventende(migrator_):
    migrator_.execute("SET LOCAL ROLE disponit_domene_eier")
    rader = migrator_.execute(
        "SELECT tenant, hostname FROM ventende_domenechallenges(500)"
    ).fetchall()
    migrator_.execute("RESET ROLE")
    migrator_.rollback()
    return rader


@pg
def test_runtime_kan_utstede_men_aldri_bekrefte(migrator):
    """Sikkerhetssnittet: API-et (runtime) genererte tokenet og skal
    derfor ALDRI kunne bekrefte det — ellers var DNS-beviset valgfritt."""
    vert = f"snitt{secrets.token_hex(3)}.example"
    token = secrets.token_hex(32)
    h = hashlib.sha256(token.encode()).hexdigest()
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT utsted_challenge(%s,%s,false,%s,'rt')",
                   (TENANT, vert, h))
        rt.commit()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT bekreft_domenechallenge(%s,%s,'rt',%s)",
                       (TENANT, vert, [token]))
        rt.rollback()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT * FROM ventende_domenechallenges(10)")
        rt.rollback()
    finally:
        rt.close()


@pg
def test_http_utsted_og_liste(migrator, klient):
    """POST /v1/domener → 201 med TXT-verdien (vist ÉN gang; kun hashen i
    basen); GET /v1/domener viser raden; lukket kropp og scope."""
    from api import sesjon as sesjonmodul
    cookie, csrf = _adminsesjon()
    vert = f"selv{secrets.token_hex(3)}.example"
    r = klient.post("/v1/domener", json={"hostname": vert.upper()},
                    headers={"X-Disponit-CSRF": csrf},
                    cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 201, r.text
    svar = r.json()
    assert svar["txt_navn"] == vert and len(svar["txt_verdi"]) == 64
    _sett_kontekst(migrator, TENANT)
    h = migrator.execute(
        "SELECT challenge_token_hash, status FROM domenekontroll"
        " WHERE tenant=%s AND hostname=%s", (TENANT, vert)).fetchone()
    migrator.rollback()
    assert h[1] == "ventende"
    assert h[0] == hashlib.sha256(svar["txt_verdi"].encode()).hexdigest()
    assert svar["txt_verdi"] not in h[0], "klartekst lagres aldri"

    lr = klient.get("/v1/domener",
                    cookies={sesjonmodul.C_SESJON: cookie})
    assert lr.status_code == 200, lr.text
    mine = {d["hostname"]: d["status"] for d in lr.json()["domener"]}
    assert mine.get(vert) == "ventende"
    for d in lr.json()["domener"]:
        assert "challenge_token_hash" not in d

    fr = klient.post("/v1/domener", json={"hostname": vert, "x": 1},
                     headers={"X-Disponit-CSRF": csrf},
                     cookies={sesjonmodul.C_SESJON: cookie})
    assert fr.status_code == 400
    ck2, cs2 = _adminsesjon(roller="leser")
    sr = klient.post("/v1/domener", json={"hostname": vert},
                     headers={"X-Disponit-CSRF": cs2},
                     cookies={sesjonmodul.C_SESJON: ck2})
    assert sr.status_code == 403


@pg
@dekker("domene_challenge_avvist")
def test_http_ukanonisk_hostname_avvises_av_basen(migrator, klient):
    """018 §0-vakten er strengere enn API-regexen (IDNA2008): et navn som
    slipper gjennom klientformen, men ikke er kanonisk (all-numerisk),
    blir 409 domene_challenge_avvist — aldri en rå DB-feil."""
    from api import sesjon as sesjonmodul
    cookie, csrf = _adminsesjon()
    r = klient.post("/v1/domener", json={"hostname": "127.0.0.1"},
                    headers={"X-Disponit-CSRF": csrf},
                    cookies={sesjonmodul.C_SESJON: cookie})
    assert (r.status_code, r.json()["feil"]) == (
        409, "domene_challenge_avvist"), r.text


@pg
def test_verifiseringspasset_ende_til_ende(migrator, klient):
    """Arbeiderpasset med en fake-resolver: challenge utstedt over HTTP,
    TXT «i sonen», pass → verifisert — og bestillingsveiens
    hostname-port åpner seg (integrasjonen selvbetjeningen finnes for)."""
    import sys
    from api import sesjon as sesjonmodul
    from drift import domenerevalidering as dr

    cookie, csrf = _adminsesjon()
    vert = f"e2e{secrets.token_hex(3)}.example"
    r = klient.post("/v1/domener", json={"hostname": vert},
                    headers={"X-Disponit-CSRF": csrf},
                    cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 201, r.text
    token = r.json()["txt_verdi"]

    def fake_enig(resolvere, hostname):
        return frozenset({token, "v=spf1 -all"}) if hostname == vert else None

    ekte = dr.enig_svar
    dr.enig_svar = fake_enig
    try:
        # SET ROLE (sesjon), ikke SET LOCAL: passet committer/ruller
        # tilbake underveis, og en transaksjonslokal rolle ville falt av
        # midt i løkka.
        migrator.execute("SET ROLE disponit_domene_eier")
        migrator.commit()
        res = dr.kjor_ventende(migrator, resolvere=[],
                               aktor="test-pass", grense=500)
    finally:
        dr.enig_svar = ekte
        migrator.execute("RESET ROLE")
        migrator.commit()
    assert res["verifisert"] >= 1, res
    _sett_kontekst(migrator, TENANT)
    status = migrator.execute(
        "SELECT status FROM domenekontroll WHERE tenant=%s AND hostname=%s",
        (TENANT, vert)).fetchone()[0]
    migrator.rollback()
    assert status == "verifisert"
