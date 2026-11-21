"""Porten for 226: måltallene for køen.

  1. Volum og svartid gjelder vinduet, og svartiden er MEDIANEN av
     henvendelsene som ble lukket som besvart.
  2. Uten besvarte er medianen `None` — ikke 0.
  3. Tallene er tenantbundet, og køen bærer dem i `maaltall`.

MUTASJONEN SOM DREPER DENNE: bytt medianen med snittet (ett uteliggende
svar flytter da tallet), eller tell `ikke_aktuell` som besvart.
"""
import secrets
import uuid

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_m17_kundeservice import _nokkel, _ta_imot, _utkast
from .test_m37 import _sett_kontekst


def _tall(c, tenant, dogn=30):
    _sett_kontekst(c, tenant)
    r = c.execute("SELECT * FROM m17_maaltall(%s,%s)",
                  (tenant, dogn)).fetchone()
    c.rollback()
    return dict(zip(("dogn", "mottatt", "besvart", "ikke_aktuelle",
                     "median", "apne", "eldste"), r))


def _besvar(c, m, tenant, key_id, dek, *, minutter):
    """En henvendelse mottatt for `minutter` siden, lukket som besvart nå."""
    hid = _ta_imot(c, tenant, key_id, dek)
    uid = uuid.uuid4()
    _utkast(c, tenant, hid, key_id, dek, uid=uid)
    _sett_kontekst(c, tenant)
    c.execute("SELECT m17_avgjor_utkast(%s,%s,'brukt_manuelt','u-test')",
              (tenant, uid))
    c.execute("SELECT m17_lukk(%s,%s,'besvart','u-test')", (tenant, hid))
    c.commit()
    # Mottakstidspunktet settes av migratoren: døra tar bare imot «nå minus
    # hele døgn», og svartid måles i minutter.
    _sett_kontekst(m, tenant)
    m.execute("ALTER TABLE henvendelse DISABLE TRIGGER USER")
    m.execute("UPDATE henvendelse SET mottatt = lukket_ts"
              " - make_interval(mins => %s) WHERE tenant=%s"
              " AND henvendelse_id=%s", (minutter, tenant, hid))
    m.execute("ALTER TABLE henvendelse ENABLE TRIGGER USER")
    m.commit()
    return hid


@pg
def test_svartiden_er_medianen_av_de_besvarte(migrator, miljo, klient, token):
    from db.pg import koble
    tenant = "t-226-" + secrets.token_hex(4)
    c = koble(DSN)
    try:
        key_id, dek = _nokkel(c, tenant)
        tomt = _tall(c, tenant)
        assert tomt["mottatt"] == 0 and tomt["besvart"] == 0
        assert tomt["median"] is None, "0 minutter er en påstand"
        assert tomt["apne"] == 0 and tomt["eldste"] is None

        for minutter in (20, 60, 600):          # median 60, snitt 226
            _besvar(c, migrator, tenant, key_id, dek, minutter=minutter)
        # Én lukket som ikke aktuell (teller ikke som besvart), og én åpen
        # som er tre døgn gammel.
        hid = _ta_imot(c, tenant, key_id, dek)
        _sett_kontekst(c, tenant)
        c.execute("SELECT m17_lukk(%s,%s,'ikke_aktuell','u-test')",
                  (tenant, hid))
        c.commit()
        _ta_imot(c, tenant, key_id, dek, dager_siden=3)

        t = _tall(c, tenant)
        assert t["dogn"] == 30
        assert t["mottatt"] == 5 and t["besvart"] == 3
        assert t["ikke_aktuelle"] == 1
        assert t["median"] == 60, t
        assert t["apne"] == 1 and t["eldste"] == 3
        # Vinduet avgrenser: for ett døgn siden er den tre døgn gamle
        # ikke «mottatt i perioden», men den er fortsatt åpen.
        kort = _tall(c, tenant, 1)
        assert kort["mottatt"] == 4 and kort["apne"] == 1
        # En annen tenant ser ingenting av dette.
        annen = _tall(c, "t-226-annen-" + secrets.token_hex(3))
        assert annen["mottatt"] == 0 and annen["median"] is None
    finally:
        c.close()


@pg
def test_koen_baerer_maaltallene(migrator, miljo, klient, token):
    tok, _ = token(rolle="leser", scopes=("decisions:read",))
    r = klient.get("/v1/kundeservice",
                   headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    mt = r.json()["maaltall"]
    assert set(mt) == {"dogn", "mottatt", "besvart", "ikke_aktuelle",
                       "median_svartid_min", "apne", "eldste_apne_dogn"}
    assert mt["dogn"] == 30 and isinstance(mt["mottatt"], int)
