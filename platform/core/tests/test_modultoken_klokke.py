"""#205: modultoken-predikatet dømmer med VEGGKLOKKEN (migrasjon 062).

Porten tar delt modul-lås FØR tidsmålingen. Venter den bak en eksklusiv
holder (nødstopp/rotasjon — hendelsene porten finnes for) og tokenet
utløper i køen, skal dommen være `token_ugyldig` — transaksjonsfrossen
`now()` var starttiden og sa `ok`.

MUTASJONEN SOM DREPER DENNE: sett `now()` tilbake i ett av de to
tidsleddene i 062.
"""
import secrets
import threading
import time
import uuid

import pytest

from .test_api import DSN, MIGRATOR_DSN, miljo  # noqa: F401
from .test_modul_onboarding_http import _kjede

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _rigg(m, ttl_sekunder: float, tilbakekall_om: float = None):
    """modulhode → onboarding → token med kort utløp. Direkte INSERT som
    tabelleieren (modul_eier): produsentveien (HTTP-innløsning) kan ikke
    utstede et token som utløper om et halvt sekund, og det er nøyaktig
    fristen porten skal måles mot."""
    mid, rel = _kjede(m)          # hode→kontrakt→release→deployment
    oid, tid = uuid.uuid4(), uuid.uuid4()
    m.execute("SET LOCAL ROLE disponit_modul_eier")
    m.execute(
        "INSERT INTO modul_onboarding (onboarding_id,modul_id,miljo,"
        " release_id,hemmelighet_hash,familie_utloper,utloper,"
        " utstedt_av)"
        " VALUES (%s,%s,'staging',%s,%s,"
        " clock_timestamp()+interval '1 day',"
        " clock_timestamp()+interval '1 hour','test')"
        " RETURNING familie_utloper",
        (oid, mid, rel, "0" * 64))
    familie = m.execute(
        "SELECT familie_utloper FROM modul_onboarding"
        " WHERE onboarding_id=%s", (oid,)).fetchone()[0]
    m.execute(
        "INSERT INTO modultoken (token_id,token_mac,onboarding_id,"
        " familie_utloper,modul_id,miljo,release_id,utstedt_epoch,"
        " utloper)"
        " VALUES (%s,%s,%s,%s,"
        " %s,'staging',%s,0,"
        " clock_timestamp() + (%s || ' seconds')::interval)",
        (tid, secrets.token_hex(32), oid, familie, mid, rel,
         str(ttl_sekunder)))
    if tilbakekall_om is not None:
        # Rotasjonsnåde-formen: tilbakekalt_ts i NÆR fremtid — den andre
        # tidsgrenen porten måler (CodeRabbit minor).
        m.execute(
            "UPDATE modultoken SET tilbakekalt_ts ="
            " clock_timestamp() + (%s || ' seconds')::interval"
            " WHERE token_id=%s", (str(tilbakekall_om), tid))
    m.execute("RESET ROLE")
    m.commit()
    return mid, rel, tid


def _dom(conn, mid, rel, tid):
    return conn.execute(
        "SELECT modultoken_fortsatt_autorisert(%s,%s,'staging',%s,0)",
        (tid, mid, rel)).fetchone()[0]


@pg
def test_token_som_utloper_i_laasekoen_avvises(miljo):
    from db.pg import koble
    m = koble(MIGRATOR_DSN)
    try:
        mid, rel, tid = _rigg(m, 0.4)
        # Ferskt: porten sier ok (positiv kontroll — uten den målte
        # negativen bare at riggen er ødelagt).
        m.execute("SET LOCAL ROLE disponit_modul_eier")
        assert _dom(m, mid, rel, tid) == "ok"
        m.execute("RESET ROLE"); m.rollback()

        # Eksklusiv holder tar modul-låsen …
        laas = koble(MIGRATOR_DSN)
        laas.execute("SELECT pg_advisory_xact_lock(hashtextextended("
                     "'modul:' || %s, 0))", (mid,))
        svar = {}

        def kaller():
            c = koble(MIGRATOR_DSN)
            try:
                c.execute("SET LOCAL ROLE disponit_modul_eier")
                svar["dom"] = _dom(c, mid, rel, tid)
                c.rollback()
            finally:
                c.close()

        t = threading.Thread(target=kaller)
        t.start()
        # … kallet BEKREFTES ventende på den delte låsen (pg_locks —
        # deterministisk, ikke en blind sleep; CodeRabbit minor), og
        # slippes først når fristen målt av VEGGKLOKKEN er passert …
        _vent_paa_koe(m)
        time.sleep(0.6)          # fristen (0.4s) passerer i køen
        laas.rollback()          # slipp låsen
        t.join(10)
        laas.close()
        # … og dommen felles med klokka slik den står NÅ.
        assert svar.get("dom") == "token_ugyldig", svar
    finally:
        m.close()


def _vent_paa_koe(m, n=200):
    """Poller pg_locks til NOEN venter ugrantet på en advisory-lås."""
    for _ in range(n):
        rad = m.execute(
            "SELECT 1 FROM pg_locks"
            " WHERE locktype='advisory' AND NOT granted").fetchone()
        m.rollback()
        if rad:
            return
        time.sleep(0.02)
    raise AssertionError("kallet stilte seg aldri i låsekøen")


@pg
def test_tilbakekalling_i_laasekoen_avvises(miljo):
    """Den ANDRE tidsgrenen (CodeRabbit minor): lang utloper, men
    tilbakekalt_ts i nær fremtid — nåden løper ut mens kallet står i
    køen, og veggklokken skal se det."""
    from db.pg import koble
    m = koble(MIGRATOR_DSN)
    try:
        mid, rel, tid = _rigg(m, 3600.0, tilbakekall_om=0.4)
        laas = koble(MIGRATOR_DSN)
        laas.execute("SELECT pg_advisory_xact_lock(hashtextextended("
                     "'modul:' || %s, 0))", (mid,))
        svar = {}

        def kaller():
            c = koble(MIGRATOR_DSN)
            try:
                c.execute("SET LOCAL ROLE disponit_modul_eier")
                svar["dom"] = _dom(c, mid, rel, tid)
                c.rollback()
            finally:
                c.close()

        t = threading.Thread(target=kaller)
        t.start()
        _vent_paa_koe(m)
        time.sleep(0.6)
        laas.rollback()
        t.join(10)
        laas.close()
        assert svar.get("dom") == "token_ugyldig", svar
    finally:
        m.close()
