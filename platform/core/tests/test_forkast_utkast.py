"""Forkast utkast — flatens eneste «slett».

Eier ba om å kunne slette, ikke bare redigere. Det som KAN slettes er et
utkast: et forslag som ennå ikke binder noen. En policy som har styrt
beslutninger kan ikke fjernes — da ville revisjonssporet pekt på noe som ikke
finnes lenger — så «slett» stopper ved forslaget, og aktive policyer avvikles
i stedet.

Grensene som prøves her er de som gjør forkastingen trygg:
  * bare `utkast` og `validert` (et `godkjent` utkast har fire øyne bak seg);
  * en ÅPEN runde trekkes tilbake (`kansellert`) i samme handling — å nekte
    lot eier stå fast i opptil 24 timer på sitt eget forslag (17/8), og en
    runde på et forkastet utkast kunne uansett aldri aktiveres;
  * terminalt: statusmaskinen slipper ingen vei ut igjen.
"""
import json
import secrets
from datetime import datetime, timezone

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


def _forkast(rt, uid, ver=1, naa=None):
    idem = secrets.token_hex(8)
    return policyadmin.forkast_utkast(
        rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
        forventet_utkastversjon=ver, idempotency_key=idem,
        input_hash=f"{TEN}\x1f{uid}\x1fforkast\x1f{ver}\x1f{idem}",
        naa=naa or datetime.now(timezone.utc))


def _runde(uid, utloper, status="apen"):
    """Legg inn en runde direkte — testene her prøver forkastingens grenser,
    ikke runde-åpningen."""
    m = _mig()
    m.execute(
        "INSERT INTO aktiveringsrunde (tenant,utkast_id,runde,status,"
        "diff_hash,utkast_innholds_hash,base_policy_hash,risikoklasse,"
        "klassifisering_hash,klassifikatorversjon,policyskjema_versjon,"
        "motor_semantikkversjon,deny_all_hash,deny_all_versjon,"
        "pakrevd_antall_godkjennere,utloper)"
        f" VALUES (%s,%s,1,'{status}','dh','ih','bh','UTVIDER','kh','1','0.2',"
        f"'1','dah','1',2,now()+interval '{utloper}')", (TEN, uid))
    m.commit()
    m.close()


def _rundestatus(uid):
    m = _mig()
    rad = m.execute("SELECT status FROM aktiveringsrunde WHERE tenant=%s"
                    " AND utkast_id=%s AND runde=1", (TEN, uid)).fetchone()
    m.close()
    return rad[0] if rad else None


def _detalj(rt, uid, naa=None):
    return policyadmin.hent_utkast_detalj(
        rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
        naa=naa or datetime.now(timezone.utc))


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
def test_aapen_runde_trekkes_tilbake_ved_forkasting():
    """Å forkaste forslaget trekker den åpne runden tilbake (`kansellert`) i
    samme handling — den forrige regelen («runden må avsluttes først») vernet
    ingen: et forkastet utkast kan uansett aldri aktiveres, og eier hadde
    ingen kodesti som kunne avslutte sin egen runde. Han sto fast i opptil
    24 timer (RUNDE_TTL) på et forslag han selv eide (17/8: seks slettinger
    på rad døde mot samme runde). Varselet pensjoneres samtidig — godkjennere
    skal ikke bes attestere et forslag som er trukket.

    Kontroll: fjern `_kanseller_levende_runde`-kallet i `forkast_utkast`, så
    blir denne rød (runden blokkerer igjen).
    """
    uid = "u-" + secrets.token_hex(6)
    _utkast(uid, "validert")
    _runde(uid, "1 hour")
    rt = _rt()
    try:
        res = _forkast(rt, uid)
        rt.commit()
        assert res["utfall"] == "forkastet"
        assert _status(uid) == "forkastet"
        assert _rundestatus(uid) == "kansellert", \
            "runden ble ikke trukket tilbake"
    finally:
        rt.close()


@pg
def test_forfalt_runde_blokkerer_ikke_forkasting_for_alltid():
    """En runde som har passert `utloper` er død, men ingenting satte den til
    `utlopt`: `attester_aktivering` nekter den og ruller tilbake, så raden ble
    liggende `apen`. Da så forkastingen en «åpen» runde for alltid, og
    utkastet kunne ALDRI ryddes bort — flatens eneste «slett» var stengt av en
    runde ingen kunne avslutte (Codex P2).

    Kontroll: fjern `_lukk_forfalt_runde`-kallet i `forkast_utkast`, så blir
    denne rød.
    """
    uid = "u-" + secrets.token_hex(6)
    _utkast(uid, "validert")
    _runde(uid, "-1 hour")
    rt = _rt()
    try:
        res = _forkast(rt, uid)
        rt.commit()
        assert res["utfall"] == "forkastet"
        assert _status(uid) == "forkastet"
        assert _rundestatus(uid) == "utlopt", "runden ble stående åpen"
    finally:
        rt.close()


@pg
def test_forfalt_klar_runde_lukkes_ogsaa():
    """`klar` er like fastlåst som `apen`: statusmaskinen i 012 tillater
    `klar → utlopt`, og en forfalt runde kan uansett ikke aktiveres."""
    uid = "u-" + secrets.token_hex(6)
    _utkast(uid, "validert")
    _runde(uid, "-5 minutes", status="klar")
    rt = _rt()
    try:
        res = _forkast(rt, uid)
        rt.commit()
        assert res["utfall"] == "forkastet"
        assert _rundestatus(uid) == "utlopt"
    finally:
        rt.close()


@pg
def test_detalj_melder_forfalt_runde_som_utlopt():
    """Lesestien må si det samme som skrivestiene mener (Codex P2 på #66).

    Flaten velger handlinger på rundestatusen den får servert: en forfalt
    runde som fortsatt står `apen` i basen skjuler BÅDE «Åpne runde» og
    «Forkast», og tilbyr «Attester» — den ene handlingen som er umulig. Da er
    veiene ut som ble reparert i skrivestiene ikke nåbare for eier i det hele
    tatt. Ingen skrivesti hadde vært innom ennå, så statusen i basen er
    fortsatt `apen` — detaljen må REGNE seg fram til `utlopt`.

    Kontroll: fjern `_runde_status`-kallet i `hent_utkast_detalj`, så blir
    denne rød.
    """
    uid = "u-" + secrets.token_hex(6)
    _utkast(uid, "validert")
    _runde(uid, "-1 hour")
    rt = _rt()
    try:
        det = _detalj(rt, uid)
        assert det["aktiv_runde"]["status"] == "utlopt"
        assert _rundestatus(uid) == "apen", "lesestien skrev til basen"
    finally:
        rt.close()


@pg
def test_detalj_lar_levende_runde_staa():
    """Motstykket: en runde som ikke har passert `utloper` skal fortsatt meldes
    som åpen — flaten må vite at en forkasting eller gjenåpning kommer til å
    trekke en LEVENDE runde tilbake, så dialogen kan si det før klikket."""
    uid = "u-" + secrets.token_hex(6)
    _utkast(uid, "validert")
    _runde(uid, "1 hour")
    rt = _rt()
    try:
        assert _detalj(rt, uid)["aktiv_runde"]["status"] == "apen"
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
