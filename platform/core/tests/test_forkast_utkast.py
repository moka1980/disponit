"""Forkast utkast — flatens eneste «slett».

Eier ba om å kunne slette, ikke bare redigere. Det som KAN slettes er et
utkast: et forslag som ennå ikke binder noen. En policy som har styrt
beslutninger kan ikke fjernes — da ville revisjonssporet pekt på noe som ikke
finnes lenger — så «slett» stopper ved forslaget, og aktive policyer avvikles
i stedet.

Grensene som prøves her er de som gjør forkastingen trygg:
  * bare `utkast` og `validert` (et `godkjent` utkast har fire øyne bak seg);
  * aldri mens en runde er åpen — attestasjoner i omløp skal ikke kunne rives
    bort under godkjennerne;
  * terminalt: statusmaskinen slipper ingen vei ut igjen.
"""
import json
import secrets

import pytest

from api import policyadmin
from api import policyregister as pr

from .test_api import DSN, MIGRATOR_DSN

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
TEN = "t-forkast-" + secrets.token_hex(3)

INNHOLD = {
    "schema_version": "0.2",
    "tidssone": "Europe/Oslo",
    "dataklasser": ["offentlig"],
    "roller": [{"id": "agent"}],
    "verifikatorer": {"v1": {"betrodd_for": ["fire_oyne"],
                             "kan_fastsla_permanent": False}},
    "unntak": {"kategorier": ["over_grense", "manglende_data", "regelkonflikt",
                              "teknisk_feil", "ugyldig_data", "ukjent"],
               "maks_auto_forsok": 0, "eskalering": "unntakskø"},
    "handlinger": [{"id": "faktura.bokfor", "modul": "M-14",
                    "modus": "auto_med_vilkaar", "ved_brudd": "unntakskø",
                    "reversering": {"type": "kompenserende",
                                    "handling": "faktura.krediter"},
                    "dataklasser_tillatt": ["offentlig"],
                    "vilkaar": [{"navn": "fire_oyne", "verifikator": "v1"}],
                    "tillatt_for": ["agent"],
                    "grenser": {"belop_maks": "25000.00", "valuta": ["NOK"]}}],
}


def _mig():
    from db.pg import koble, sett_kontekst
    c = koble(MIGRATOR_DSN)
    sett_kontekst(c, TEN, "sys", "r0")
    return c


def _rt():
    from db.pg import koble
    return koble(DSN)


def _utkast(uid, status="validert", pid=None):
    pid = pid or "p-" + secrets.token_hex(3)
    innhold = {**INNHOLD, "meta": {"policy_id": pid, "versjon": "1.1.0",
                                   "bransjemal": "test", "status": "produksjon"}}
    m = _mig()
    m.execute(
        "INSERT INTO policyutkast (tenant,utkast_id,policy_id,innhold,"
        "innholds_hash,status,opprettet_av) VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s)",
        (TEN, uid, pid, json.dumps(innhold), pr.innholds_hash(innhold), status,
         "forf"))
    m.commit()
    m.close()
    return pid


def _forkast(rt, uid, ver=1):
    idem = secrets.token_hex(8)
    return policyadmin.forkast_utkast(
        rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
        forventet_utkastversjon=ver, idempotency_key=idem,
        input_hash=f"{TEN}\x1f{uid}\x1fforkast\x1f{ver}\x1f{idem}")


def _status(uid):
    m = _mig()
    rad = m.execute("SELECT status FROM policyutkast WHERE tenant=%s"
                    " AND utkast_id=%s", (TEN, uid)).fetchone()
    m.close()
    return rad[0] if rad else None


@pg
def test_validert_utkast_kan_forkastes():
    uid = "u-" + secrets.token_hex(6)
    _utkast(uid, "validert")
    rt = _rt()
    try:
        res = _forkast(rt, uid)
        rt.commit()
        assert res["utfall"] == "forkastet"
        assert _status(uid) == "forkastet"
    finally:
        rt.close()


@pg
def test_forkastet_er_terminalt():
    """Statusmaskinen (012) slipper ingen vei ut. Uten dette ville et forkastet
    forslag kunne vekkes til live igjen og attesteres."""
    uid = "u-" + secrets.token_hex(6)
    _utkast(uid, "validert")
    rt = _rt()
    try:
        _forkast(rt, uid)
        rt.commit()
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _forkast(rt, uid)
        assert "ulovlig_tilstand" in str(e.value.kode)
        assert _status(uid) == "forkastet"
    finally:
        rt.close()


@pg
def test_utkast_med_aapen_runde_kan_ikke_rives_bort():
    """Attestasjoner i omløp. En godkjenner som står midt i en vurdering skal
    ikke oppdage at forslaget er borte — runden avsluttes først.

    Kontroll: fjern `aapen`-sjekken i `forkast_utkast`, så blir denne rød.
    """
    uid = "u-" + secrets.token_hex(6)
    _utkast(uid, "validert")
    m = _mig()
    m.execute(
        "INSERT INTO aktiveringsrunde (tenant,utkast_id,runde,status,"
        "diff_hash,utkast_innholds_hash,base_policy_hash,risikoklasse,"
        "klassifisering_hash,klassifikatorversjon,policyskjema_versjon,"
        "motor_semantikkversjon,deny_all_hash,deny_all_versjon,"
        "pakrevd_antall_godkjennere,utloper)"
        " VALUES (%s,%s,1,'apen','dh','ih','bh','UTVIDER','kh','1','0.2','1',"
        "'dah','1',2,now()+interval '1 hour')", (TEN, uid))
    m.commit()
    m.close()
    rt = _rt()
    try:
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _forkast(rt, uid)
        assert "runde" in str(e.value.kode)
        assert _status(uid) == "validert", "utkastet ble forkastet likevel"
    finally:
        rt.close()


@pg
def test_ukjent_utkast_gir_domenefeil_ikke_stille_suksess():
    rt = _rt()
    try:
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _forkast(rt, "u-finnesikke")
        assert "ukjent" in str(e.value.kode)
    finally:
        rt.close()


@pg
def test_feil_utkastversjon_avvises():
    """Nøkkelen er bundet til versjonen, som ellers i modulen: forkaster du på
    grunnlag av en tilstand du ikke lenger ser, skal det stoppe."""
    uid = "u-" + secrets.token_hex(6)
    _utkast(uid, "validert")
    rt = _rt()
    try:
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _forkast(rt, uid, ver=99)
        assert "utdatert" in str(e.value.kode)
        assert _status(uid) == "validert"
    finally:
        rt.close()
