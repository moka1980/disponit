"""Porten mot #413: en økt som BRUKES lever; en økt som HVILER dør.

`slaa_opp_sesjon` nekter etter 30 minutter uten bruk og bumper
`siste_bruk` ved hvert oppslag — i sin egen transaksjon. Alle kallerne
rullet den transaksjonen tilbake rett etterpå, så bumpen forsvant hver
gang og «inaktiv i 30 minutter» ble «30 minutter siden innlogging» for
hver eneste bruker (Fjordlys-kampanjen 7/9: to økter brukt i over en
time hadde `siste_bruk == opprettet`).

Porten går gjennom HTTP-døra mot ekte base: en økt som sist ble brukt
for 20 minutter siden svarer 200 OG får `siste_bruk` flyttet; en økt
som sist ble brukt for 31 minutter siden svarer 401.

MUTASJONEN SOM DREPER DENNE: bytt `conn.commit()` etter
`slaa_opp_sesjon` tilbake til `conn.rollback()` i `sesjon.autentiser`.
"""
import secrets

from .test_api import (DSN, MIGRATOR_DSN, TENANT, pg,  # noqa: F401
                       app, klient, migrator, miljo)
from .test_m37 import _sett_kontekst


def _okt(migrator, *, siste_bruk_minutter_siden: int):
    from api import sesjon as sesjonmodul
    _sett_kontekst(migrator, TENANT)
    bid = migrator.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://sesjon413.test', %s) RETURNING bruker_id",
        ("s413-" + secrets.token_hex(6),)).fetchone()[0]
    migrator.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller, aktiv)"
        " VALUES (%s,%s,%s,true)", (TENANT, bid, ["leser"]))
    ver = migrator.execute(
        "SELECT authz_version FROM brukermedlemskap WHERE tenant=%s"
        " AND bruker_id=%s", (TENANT, bid)).fetchone()[0]
    cookie, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    migrator.execute(
        "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
        " authz_snapshot, csrf_hash, opprettet, siste_bruk, utloper,"
        " tilbakekalt) VALUES (%s,%s,%s,%s,%s,"
        " now() - interval '2 hours', now() - %s * interval '1 minute',"
        " now() + interval '10 hours', false)",
        (sesjonmodul._hash(cookie), TENANT, bid, ver,
         sesjonmodul._hash(csrf), siste_bruk_minutter_siden))
    migrator.commit()
    return cookie, sesjonmodul._hash(cookie)


def _siste_bruk_alder_sek(migrator, sesjon_hash) -> float:
    migrator.rollback()
    rad = migrator.execute(
        "SELECT extract(epoch FROM (now() - siste_bruk)) FROM brukersesjon"
        " WHERE sesjon_id_hash=%s", (sesjon_hash,)).fetchone()
    migrator.rollback()
    return float(rad[0])


@pg
def test_en_okt_som_brukes_lever_og_far_siste_bruk_flyttet(miljo, migrator,
                                                            klient):
    from api import sesjon as sesjonmodul
    cookie, h = _okt(migrator, siste_bruk_minutter_siden=20)
    assert _siste_bruk_alder_sek(migrator, h) > 19 * 60
    r = klient.get("/v1/sesjon", cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 200, r.text
    # Bumpen OVERLEVDE kallet: økta er nå «nettopp brukt».
    assert _siste_bruk_alder_sek(migrator, h) < 60, (
        "siste_bruk ble ikke flyttet — bumpen i slaa_opp_sesjon rulles"
        " tilbake, og økta dør 30 minutter etter innlogging uansett bruk")


@pg
def test_en_okt_som_hviler_i_31_minutter_er_dod(miljo, migrator, klient):
    from api import sesjon as sesjonmodul
    cookie, _ = _okt(migrator, siste_bruk_minutter_siden=31)
    r = klient.get("/v1/sesjon", cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 401, r.text
