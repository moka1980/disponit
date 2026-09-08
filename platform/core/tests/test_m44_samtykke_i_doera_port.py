"""Porten mot #423: planen nekter det sveipen bare ville funnet.

`m44_legg_i_plan` (114) tok imot enhver aktiv mottaker; manglende,
trukket eller utløpt samtykke og brudd på frekvenstaket ble funn først
den dagen `planlagt_sendt` var nådd. På disponit.com (Fjordlys 8/9) gikk
en mottaker som hadde trukket samtykket rett inn i planen, og en annen
sto i tre kampanjer med tak 2.

142 dømmer i døra. Fem ting måles gjennom døra mot ekte base:
  1. uten samtykke → nei;  2. trukket → nei;  3. utløpt før sendedato → nei;
  4. over frekvenstaket → nei, og innsettingen er rullet tilbake;
  5. gitt samtykke innenfor taket → ja, og handlingen står i loggen.
Og sveipens etterkontroll består: samtykke trukket ETTER planlegging
gir fortsatt `samtykke_trukket`.

MUTASJONEN SOM DREPER DENNE: legg 114s versjon av `m44_legg_i_plan`
tilbake (ingen samtykkedom, ingen takdom).
"""
import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, pg, migrator, miljo  # noqa: F401
from .test_m37 import _sett_kontekst
from .test_m44_kampanje import (_grense, _kampanje, _mottaker, _plan, _rt,
                                _samtykke, _tenantnavn, _sveip, _sv)


def _nei(c, tenant, kid, mid) -> str:
    with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
        _plan(c, tenant, kid, mid)
    c.rollback()
    return str(ei.value).split("\n")[0]


@pg
def test_uten_trukket_og_utlopt_samtykke_nektes(miljo, migrator):
    c = _rt()
    try:
        t = _tenantnavn("dor")
        _grense(c, t, maks=2, periode=7, gyldig=30)
        kid = _kampanje(c, t, dato="2026-09-20")
        # 1. aldri gitt
        m1, _ = _mottaker(c, t)
        assert "har ikke gitt samtykke" in _nei(c, t, kid, m1)
        # 2. trukket
        m2, _ = _mottaker(c, t)
        _samtykke(c, t, m2, "gitt", "2026-08-01")
        _samtykke(c, t, m2, "trukket", "2026-08-20")
        assert "trukket" in _nei(c, t, kid, m2)
        # 3. gitt, men utløpt før sendedatoen (30 døgn gyldig)
        m3, _ = _mottaker(c, t)
        _samtykke(c, t, m3, "gitt", "2026-07-01")
        assert "utløpt før sendedatoen" in _nei(c, t, kid, m3)
        # …og ingen av dem står i planen.
        _sett_kontekst(migrator, t)
        n = migrator.execute("SELECT count(*) FROM kampanjeplan"
                             " WHERE tenant=%s", (t,)).fetchone()[0]
        migrator.rollback()
        assert n == 0
    finally:
        c.close()


@pg
def test_over_frekvenstaket_nektes_og_innsettingen_rulles_tilbake(miljo,
                                                                   migrator):
    c = _rt()
    try:
        t = _tenantnavn("tak")
        _grense(c, t, maks=2, periode=30, gyldig=730)
        m, _ = _mottaker(c, t)
        _samtykke(c, t, m, "bekreftet", "2026-09-01")
        k1 = _kampanje(c, t, dato="2026-09-15")
        k2 = _kampanje(c, t, dato="2026-09-22")
        k3 = _kampanje(c, t, dato="2026-09-29")
        assert _plan(c, t, k1, m) == 1
        assert _plan(c, t, k2, m) == 2
        assert "taket er 2" in _nei(c, t, k3, m)
        _sett_kontekst(migrator, t)
        n = migrator.execute("SELECT count(*) FROM kampanjeplan"
                             " WHERE tenant=%s AND kampanje_id=%s",
                             (t, k3)).fetchone()[0]
        migrator.rollback()
        assert n == 0, "et nei fra taket etterlot raden i planen"
    finally:
        c.close()


@pg
def test_gitt_samtykke_innenfor_taket_gaar_og_sveipen_ser_trukket_etterpaa(
        miljo, migrator):
    c = _rt()
    try:
        t = _tenantnavn("ja")
        _grense(c, t, maks=2, periode=7, gyldig=730)
        m, _ = _mottaker(c, t)
        _samtykke(c, t, m, "gitt", "2026-09-01")
        kid = _kampanje(c, t, dato="2026-09-08")
        assert _plan(c, t, kid, m) == 1
        # …og handlingen står i revisjonsloggen (evidensen bærer bare
        # hashen av detaljen — det er husets form).
        _sett_kontekst(migrator, t)
        ev = migrator.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
            " AND kilde='m44_kampanje' AND handling='lagt_i_kampanjeplan'",
            (t,)).fetchone()[0]
        migrator.rollback()
        assert ev == 1, ev
        # Trukket ETTER planlegging: døra sa ja da, sveipen sier fra nå.
        _samtykke(c, t, m, "trukket", "2026-09-07")
        v = _sv()
        try:
            _sveip(v)
        finally:
            v.close()
        _sett_kontekst(migrator, t)
        funn = migrator.execute(
            "SELECT funntype FROM kampanjefunn WHERE tenant=%s"
            " AND mottaker_id=%s AND lukket_ts IS NULL", (t, m)).fetchall()
        migrator.rollback()
        assert ("samtykke_trukket",) in funn, funn
    finally:
        c.close()
