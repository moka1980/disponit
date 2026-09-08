"""Porten mot #429: to dører som slapp gjennom noe som så riktig ut.

M-15: lønn, husleie, skatt, avgift, abonnement og lån med POSITIVT beløp
ble forventede innbetalinger, og prognosen viste et selskap som tjente
på lønnen sin (Fjordlys 8/9). 144 nekter i døra; `annet` kan fortsatt
være begge deler.

M-45: en sammenstilling over en periode uten målinger ga rapport
versjon 1 med sum 0 — et nullutslipp ingen hadde målt. 145 nekter.

Gjennom dørene mot ekte base.
MUTASJONEN SOM DREPER DENNE: legg 128/136-versjonene tilbake.
"""
import uuid

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, pg, migrator, miljo  # noqa: F401
from .test_m15_likviditet import _post as _m15_post, _tenantnavn as _m15_tenant
from .test_m45_esg import _krav, _periode, _tenantnavn as _m45_tenant


def _rt():
    from db.pg import koble
    return koble(DSN)


@pg
def test_forpliktelse_med_positivt_belop_nektes_annet_gaar(miljo, migrator):
    c = _rt()
    try:
        t = _m15_tenant("fortegn")
        for typ in ("lonn", "husleie", "skatt", "avgift", "abonnement", "laan"):
            with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
                _m15_post(c, t, posttype=typ, beskrivelse=f"{typ} feil vei",
                          belop=4_800_000)
            c.rollback()
            assert "er en utbetaling" in str(ei.value), str(ei.value)
        # …negativt går, og «annet» går begge veier.
        _m15_post(c, t, posttype="lonn", beskrivelse="Lønn", belop=-4_800_000)
        _m15_post(c, t, posttype="annet", beskrivelse="Tilskudd", belop=500_000)
        _m15_post(c, t, posttype="annet", beskrivelse="Gebyr", belop=-20_000)
    finally:
        c.close()


@pg
def test_sammenstilling_uten_maalinger_nektes(miljo, migrator):
    c = _rt()
    try:
        t = _m45_tenant("tom")
        _krav(c, t)
        pid = _periode(c, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
            c.execute("SELECT * FROM m45_sammenstill(%s,%s,%s,%s)",
                      (t, uuid.uuid4(), pid, "u-test")).fetchone()
        c.rollback()
        assert "ingen gjeldende målinger" in str(ei.value), str(ei.value)
    finally:
        c.close()
