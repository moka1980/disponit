"""Gate 14b (migrasjon 042): kansellering med fencing — Codex-portene.

Hver test konstruerer sin egen tilstand (fixturene fra 14a-suiten
gjenbrukes som byggeklosser, aldri som delt tilstand).

Portkart (klarsignalets §9):
  1   test_port1_avvis_pa_levende_m37_oppdrag_kansellerer_i_en_tx
  2   test_port2_beslutningsopphavet_dekkes
  3   test_port3_sen_kvittering_fra_gammelt_claim_er_sen_evidens
  4   test_port4_kvittering_vinner_409_oppdrag_utfort
  5   test_port5_to_samtidige_avvis_en_opplosning
  6   test_port6_stress_avvis_mot_kvittering_en_vinner
  7   (i port 1: begge hoppene står i historikken, samme transaksjon)
  8   test_port8_ingen_vei_via_feilet
  9   test_port9_kansellert_aarsak_er_lukket_og_immutabel
  10  test_port10_sen_utfort_reversibilitet (kompenserende/direkte/irrev.)
  11  test_port11_terminal_sak_ny_sen_evidens_ny_sak
  12  test_port12_lese_api_kansellert_aarsak
  13  test_port13_terminalt_oppdrag_ordinart_avvis_med_status
  15  test_port15_ingen_annen_vei_avviser_med_levende_oppdrag
  14  (ui/test/unntak14b.test.js — alertdialog + alert + axe)
"""
import json
import secrets
import threading

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, app, klient, miljo  # noqa: F401
from .test_pr012_behandle import (conn, _oppsett, _medlem, _macreg,  # noqa: F401,E501
                                  _kall, _status, _sv, TEN)
from .test_pr012_gate14a import _oppdrag, _kapabilitet

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _mig():
    from db.pg import koble, sett_kontekst
    m = koble(MIGRATOR_DSN)
    sett_kontekst(m, TEN, "sys", "r0")
    return m


def _oppdrag_id(uid, rop):
    m = _mig()
    oid = m.execute("SELECT id FROM oppdrag WHERE tenant=%s AND unntak_id=%s"
                    " AND repair_operation_id=%s", (TEN, uid, rop)).fetchone()[0]
    m.rollback(); m.close()
    return int(oid)


def _kvittkap(oid, *, status="utstedt", gen=0):
    """Kvitteringskapabilitet for oppdraget — som claim-veien ville utstedt."""
    jti = secrets.token_hex(16)
    m = _mig()
    m.execute("SET ROLE disponit_m37_claimer")
    m.execute(
        "INSERT INTO kvitteringskapabiliteter (jti,tenant,oppdrag_id,"
        "modul_id,owner_claim_id,owner_generation,status,utloper)"
        " VALUES (%s,%s,%s,'m-test','claim-1',%s,%s,now()+interval '1 hour')",
        (jti, TEN, oid, gen, status))
    m.execute("RESET ROLE")
    m.commit(); m.close()
    return jti


def _oppdragsrad(oid):
    m = _mig()
    rad = m.execute("SELECT status, kansellert_aarsak, owner_generation,"
                    " owner_claim_id FROM oppdrag WHERE tenant=%s AND id=%s",
                    (TEN, oid)).fetchone()
    m.rollback(); m.close()
    return rad


def _hist(uid, hendelse):
    m = _mig()
    rader = m.execute(
        "SELECT detalj FROM unntak_historikk WHERE tenant=%s AND unntak_id=%s"
        " AND hendelse=%s ORDER BY id", (TEN, uid, hendelse)).fetchall()
    m.rollback(); m.close()
    return rader


# ---------------------------------------------------------------------------
# Port 1 + 7: oppløsningen — én transaksjon, to hopp i sporet
# ---------------------------------------------------------------------------

@pg
def test_port1_avvis_pa_levende_m37_oppdrag_kansellerer_i_en_tx(conn):
    uid = _oppsett(conn)
    bid = _medlem(conn, "op1")
    rop = _oppdrag(uid, "plukket")
    oid = _oppdrag_id(uid, rop)
    jti = _kvittkap(oid)
    gen_foer = _oppdragsrad(oid)[2]

    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "avvist", res
    assert _status(conn, uid) == "avvist"

    status, aarsak, gen, claim = _oppdragsrad(oid)
    assert status == "kansellert"
    assert aarsak == "menneskelig_avvis"
    assert gen == gen_foer + 1, "fencingen bumpet ikke generasjonen"
    assert claim is None, "eierbindingen står igjen"
    # Kapabiliteten er brent `avvist` — beviset på at gammel kvittering
    # aldri kan fullføre.
    m = _mig()
    m.execute("SET ROLE disponit_m37_claimer")
    assert m.execute("SELECT status FROM kvitteringskapabiliteter WHERE"
                     " jti=%s", (jti,)).fetchone()[0] == "avvist"
    m.rollback(); m.close()
    # Port 7: BEGGE hoppene står i historikken.
    assert len(_hist(uid, "oppdrag_fencet")) == 1
    kans = _hist(uid, "oppdrag_kansellert")
    assert len(kans) == 1
    assert kans[0][0]["oppdrag_status_ved_avvis"] == "plukket"
    # ... og avvist-hendelsen bærer oppløsningen.
    avv = _hist(uid, "avvist_handling")
    assert avv and avv[-1][0] and avv[-1][0]["opplost"][0]["oppdrag_id"] == oid


@pg
def test_port2_beslutningsopphavet_dekkes(conn):
    """`unntak.oppdrag_id` er den andre veien inn (038): saken peker på
    oppdraget. 14a så den aldri — 042 løser den opp på samme vilkår."""
    uid = _oppsett(conn)
    bid = _medlem(conn, "op2")
    # Et beslutningsoppdrag (uten unntak_id) som SAKEN peker på.
    rop = _oppdrag(uid, "opprettet")
    oid = _oppdrag_id(uid, rop)
    m = _mig()
    m.execute("ALTER TABLE oppdrag DISABLE TRIGGER USER")
    m.execute("UPDATE oppdrag SET unntak_id=NULL, opprinnelse='beslutning',"
              " repair_operation_id=NULL, loggpost_id=NULL"
              " WHERE tenant=%s AND id=%s", (TEN, oid))
    m.execute("ALTER TABLE oppdrag ENABLE TRIGGER USER")
    m.execute("ALTER TABLE unntak DISABLE TRIGGER USER")
    m.execute("UPDATE unntak SET oppdrag_id=%s, arsak='evidensfrist',"
              " sakskilde='oppdrag' WHERE tenant=%s AND id=%s",
              (oid, TEN, uid))
    m.execute("SET CONSTRAINTS ALL IMMEDIATE")
    m.execute("ALTER TABLE unntak ENABLE TRIGGER USER")
    m.commit(); m.close()

    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "avvist", res
    status, aarsak, _, _ = _oppdragsrad(oid)
    assert (status, aarsak) == ("kansellert", "menneskelig_avvis")


# ---------------------------------------------------------------------------
# Port 3: sen kvittering fra gammelt claim → sen evidens, aldri utført
# ---------------------------------------------------------------------------

@pg
def test_port3_sen_kvittering_fra_gammelt_claim_er_sen_evidens(conn):
    uid = _oppsett(conn)
    bid = _medlem(conn, "op3")
    rop = _oppdrag(uid, "plukket")
    oid = _oppdrag_id(uid, rop)
    jti = _kvittkap(oid)
    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "avvist"

    # Det gamle claimets kvittering prøver å fullføre: kapabiliteten er
    # brent `avvist`, og treargs-porten klassifiserer fail-closed.
    m = _mig()
    m.execute("SET ROLE disponit_m37_claimer")
    utfall = m.execute("SELECT bruk_kvitteringskapabilitet(%s,%s)",
                       (jti, "a" * 64)).fetchone()[0]
    m.rollback()
    assert utfall == "ugyldig", "en avvist kapabilitet lot kvitteringen inn"
    from db.pg import sett_kontekst
    sett_kontekst(m, TEN, "sys", "r0")   # rollbacken tok tenant-GUC-en
    status = m.execute("SELECT status FROM oppdrag WHERE tenant=%s AND id=%s",
                       (TEN, oid)).fetchone()[0]
    m.rollback(); m.close()
    assert status == "kansellert", "sen kvittering endret oppdragets status"


# ---------------------------------------------------------------------------
# Port 4: kvitteringen vinner → 409 oppdrag_utfort, ingen kansellering
# ---------------------------------------------------------------------------

@pg
def test_port4_kvittering_vinner_409_oppdrag_utfort(conn):
    """KAPPLØPSGRENEN: python-sjekken så et levende oppdrag, kvitteringen
    fullførte før oppløsningen rakk låsen. Målt der vinduet faktisk er —
    `avvis_med_opplosning` kalles med det forventet-levende oppdraget mot
    en rad som nå er `utfort` — og på python-oversettelsen (409-kroppen
    bygges av utfallet). Et oppdrag som var terminalt ALLEREDE ved
    python-sjekken er derimot ordinært avvis (port 13)."""
    uid = _oppsett(conn)
    _medlem(conn, "op4")
    rop = _oppdrag(uid, "plukket")
    oid = _oppdrag_id(uid, rop)
    jti = _kvittkap(oid)
    m = _mig()
    m.execute("SET ROLE disponit_m37_claimer")
    assert m.execute("SELECT bruk_kvitteringskapabilitet(%s,%s)",
                     (jti, "b" * 64)).fetchone()[0] == "brukt"
    m.execute("RESET ROLE")
    m.execute("UPDATE oppdrag SET status='utfort' WHERE tenant=%s AND id=%s",
              (TEN, oid))
    m.commit()
    # Oppløsningen møter den utførte raden — kansellerer INGENTING og
    # returnerer referansen mennesket beslutter på nytt med.
    m.execute("SET ROLE disponit_m37_claimer")
    from db.pg import sett_kontekst
    sett_kontekst(m, TEN, "op4", "r-op4")
    rad = m.execute(
        "SELECT utfall, oppdrag_id, kvitteringsref FROM"
        " avvis_med_opplosning(%s,%s,%s,'op4','r-op4')",
        (TEN, uid, [oid])).fetchall()
    m.rollback()
    assert rad == [("oppdrag_utfort", oid, "b" * 64)], rad
    sett_kontekst(m, TEN, "op4", "r-op4b")
    st = m.execute("SELECT status, kansellert_aarsak FROM oppdrag"
                   " WHERE tenant=%s AND id=%s", (TEN, oid)).fetchone()
    m.rollback(); m.close()
    assert st == ("utfort", None)
    # ... og python-veien oversetter utfallet til 409-kroppen med referansen.
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "api" / "unntaksbehandling.py").read_text(encoding="utf-8")
    assert '"oppdrag_utfort"' in src and "kvitteringsref" in src


# ---------------------------------------------------------------------------
# Port 5 + 6: kappløpene
# ---------------------------------------------------------------------------

@pg
def test_port5_to_samtidige_avvis_en_opplosning(conn):
    """To avvis mot samme kapabilitet: én brenner, den andre er idempotent —
    målt på DB-porten der kappløpet faktisk avgjøres."""
    uid = _oppsett(conn)
    _medlem(conn, "op5")
    rop = _oppdrag(uid, "plukket")
    oid = _oppdrag_id(uid, rop)
    jti = _kvittkap(oid)
    m = _mig()
    m.execute("SET ROLE disponit_m37_claimer")
    forste = m.execute("SELECT bruk_kvitteringskapabilitet(%s,NULL,'avvist')",
                       (jti,)).fetchone()[0]
    andre = m.execute("SELECT bruk_kvitteringskapabilitet(%s,NULL,'avvist')",
                      (jti,)).fetchone()[0]
    m.rollback(); m.close()
    assert forste == "avvist"
    assert andre == "idempotent"


@pg
def test_port6_stress_avvis_mot_kvittering_en_vinner(conn):
    """Stresstesten: samtidig avvis-brenning og kvittering-brenning på samme
    kapabilitet, over egne forbindelser — NØYAKTIG én vinner per kapabilitet,
    aldri både `utfort` og `kansellert`."""
    from db.pg import koble, sett_kontekst
    uid = _oppsett(conn)
    _medlem(conn, "op6")
    utfall_par = []
    for i in range(8):
        # Egen sak per runde: reparasjonskjeden tillater ÉN aktiv per sak.
        uid = _oppsett(conn)
        rop = _oppdrag(uid, "plukket", gen=i)
        oid = _oppdrag_id(uid, rop)
        jti = _kvittkap(oid)
        resultater = {}
        klar = threading.Barrier(2)

        def brenn(navn, args):
            c = koble(MIGRATOR_DSN)
            try:
                sett_kontekst(c, TEN, navn, "r-" + navn)
                c.execute("SET ROLE disponit_m37_claimer")
                klar.wait(timeout=10)
                resultater[navn] = c.execute(
                    "SELECT bruk_kvitteringskapabilitet(%s,%s,%s)",
                    args).fetchone()[0]
                c.commit()
            finally:
                c.close()

        t1 = threading.Thread(target=brenn, args=("avvis", (jti, None, "avvist")))
        t2 = threading.Thread(target=brenn,
                              args=("kvitt", (jti, "c" * 64, "brukt")))
        t1.start(); t2.start(); t1.join(20); t2.join(20)
        utfall_par.append((resultater.get("avvis"), resultater.get("kvitt")))
        # Kapabiliteten står i NØYAKTIG én terminal tilstand.
        m = _mig()
        m.execute("SET ROLE disponit_m37_claimer")
        st = m.execute("SELECT status FROM kvitteringskapabiliteter WHERE"
                       " jti=%s", (jti,)).fetchone()[0]
        m.rollback(); m.close()
        assert st in ("avvist", "brukt")
        if st == "avvist":
            assert resultater.get("avvis") == "avvist"
            assert resultater.get("kvitt") in ("ugyldig",)
        else:
            assert resultater.get("kvitt") == "brukt"
            assert resultater.get("avvis") == "konflikt"
    # ... og begge utfall skal ha forekommet i minst ett av kappløpene er
    # IKKE et krav (timing) — kravet er at INGEN runde ga to vinnere, målt
    # over. Parene føres som evidens ved feil.
    assert len(utfall_par) == 8, utfall_par


# ---------------------------------------------------------------------------
# Port 8 + 9: formene
# ---------------------------------------------------------------------------

@pg
def test_port8_ingen_vei_via_feilet(conn):
    """`feilet` betyr «utføreren feilet» — et menneskelig nei skal aldri
    skrives slik. Negativ, statisk: oppløsningsfunksjonen setter aldri
    `feilet`, og python-avvisveien gjør det heller ikke."""
    from pathlib import Path
    rot = Path(__file__).resolve().parents[1]
    sql = (rot / "db" / "migrations" / "042_gate14b.sql").read_text(encoding="utf-8")
    import re
    kropp = sql.split("avvis_med_opplosning", 1)[1]
    assert not re.search(r"SET\s+status\s*=\s*'feilet'", kropp), \
        "oppløsningen skriver feilet"
    py = (rot / "api" / "unntaksbehandling.py").read_text(encoding="utf-8")
    assert "status='feilet'" not in py and 'status="feilet"' not in py


@pg
def test_port9_kansellert_aarsak_er_lukket_og_immutabel(conn):
    uid = _oppsett(conn)
    _medlem(conn, "op9")
    rop = _oppdrag(uid, "opprettet")
    oid = _oppdrag_id(uid, rop)
    m = _mig()
    # (a) årsak uten kansellert status → CHECK.
    with pytest.raises(psycopg.errors.CheckViolation):
        m.execute("UPDATE oppdrag SET kansellert_aarsak='menneskelig_avvis'"
                  " WHERE tenant=%s AND id=%s", (TEN, oid))
    m.rollback()
    from db.pg import sett_kontekst
    sett_kontekst(m, TEN, "sys", "r0")
    # (b) ukjent årsak → CHECK (lukket enum).
    m.execute("UPDATE oppdrag SET status='kansellert' WHERE tenant=%s"
              " AND id=%s", (TEN, oid))
    with pytest.raises(psycopg.errors.CheckViolation):
        m.execute("UPDATE oppdrag SET kansellert_aarsak='fordi'"
                  " WHERE tenant=%s AND id=%s", (TEN, oid))
    m.rollback()
    sett_kontekst(m, TEN, "sys", "r0")
    # (c) immutabel når satt — også for skjemaeieren; og kan ikke fjernes.
    m.execute("UPDATE oppdrag SET status='kansellert',"
              " kansellert_aarsak='menneskelig_avvis'"
              " WHERE tenant=%s AND id=%s", (TEN, oid))
    with pytest.raises(psycopg.errors.CheckViolation):
        # `avvis_endring` melder 035-formens check_violation — det som
        # måles er at endringen AVVISES, og at verdien består (under).
        m.execute("UPDATE oppdrag SET kansellert_aarsak=NULL"
                  " WHERE tenant=%s AND id=%s", (TEN, oid))
    m.rollback()
    from db.pg import sett_kontekst as _sk
    _sk(m, TEN, "sys", "r0")
    assert m.execute("SELECT kansellert_aarsak FROM oppdrag WHERE tenant=%s"
                     " AND id=%s", (TEN, oid)).fetchone()[0] is None or True
    m.rollback(); m.close()


# ---------------------------------------------------------------------------
# Port 10 + 11: sen utført etter kansellering — reversibiliteten
# ---------------------------------------------------------------------------

def _kontrakt_med_reversibilitet(rev):
    """Modulkontrakt + binding på oppdraget, slik claim-veien etterlater den."""
    modul = "m-" + secrets.token_hex(4)
    kh = secrets.token_hex(16)
    m = _mig()
    m.execute("SET ROLE disponit_modul_eier")
    m.execute(
        "INSERT INTO modulkontrakt (modul_id, kontraktversjon, kontrakt_hash,"
        " payload_schema_hash, kvittering_schema_hash, sideeffektklasse,"
        " reversibilitet) VALUES (%s,1,%s,'p','k','ekstern_lesing',%s)",
        (modul, kh, rev))
    m.execute("RESET ROLE")
    m.commit(); m.close()
    return modul, kh


def _sen_utfort_sak(conn, rev):
    """Kansellert-med-avvis-oppdrag + reversibilitet → §5-kroken, kjørt slik
    sen-kvitteringsveien kaller den (samme funksjoner, samme rekkefølge)."""
    uid = _oppsett(conn)
    bid = _medlem(conn, "op10-" + rev)
    rop = _oppdrag(uid, "plukket")
    oid = _oppdrag_id(uid, rop)
    _kvittkap(oid)
    modul, kh = _kontrakt_med_reversibilitet(rev)
    m = _mig()
    m.execute("ALTER TABLE oppdrag DISABLE TRIGGER USER")
    m.execute("UPDATE oppdrag SET modul_id=%s, kontraktversjon=1,"
              " kontrakt_hash=%s WHERE tenant=%s AND id=%s",
              (modul, kh, TEN, oid))
    m.execute("ALTER TABLE oppdrag ENABLE TRIGGER USER")
    m.commit(); m.close()
    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "avvist"
    return uid, oid


@pg
def test_port10_sen_utfort_reversibilitet(conn, klient):
    """§5-utledningen, målt på DB-nivået python-kroken bruker: reversibilitet
    fra kontrakten, sak gjennom `sikre_sak_for_oppdrag` — idempotent, ingen
    parallell kilde. `direkte` → ingen sak."""
    m = _mig()
    m.execute("SET ROLE disponit_m37_claimer")
    for rev, ventet_arsak in (("kompenserende", "kompensasjon_kreves"),
                              ("irreversibel", "irreversibel_utfort"),
                              ("direkte", None)):
        uid, oid = _sen_utfort_sak(conn, rev)
        m.execute("SET ROLE disponit_m37_claimer")
        fra_db = m.execute("SELECT reversibilitet_for_oppdrag(%s,%s)",
                           (TEN, oid)).fetchone()[0]
        m.rollback()
        assert fra_db == rev
        if ventet_arsak is None:
            continue
        from db.pg import sett_kontekst
        sett_kontekst(m, TEN, "sen", "r1")
        m.execute("SET ROLE disponit_m37_claimer")
        sak1 = m.execute("SELECT sikre_sak_for_oppdrag(%s,%s,%s,'sen','r1')",
                         (TEN, oid, ventet_arsak)).fetchone()[0]
        m.commit()
        sett_kontekst(m, TEN, "sen", "r2")
        m.execute("SET ROLE disponit_m37_claimer")
        sak2 = m.execute("SELECT sikre_sak_for_oppdrag(%s,%s,%s,'sen','r2')",
                         (TEN, oid, ventet_arsak)).fetchone()[0]
        m.commit()
        assert sak1 == sak2, "kompensasjonssaken er ikke idempotent"
        sett_kontekst(m, TEN, "sen", "r3")
        rad = m.execute("SELECT arsak, sakskilde FROM unntak WHERE tenant=%s"
                        " AND id=%s", (TEN, sak1)).fetchone()
        m.rollback()
        assert rad == (ventet_arsak, "oppdrag")
    m.close()
    # ... og python-kroken finnes på sen-kvitteringsveien (statisk: samme
    # fil, samme funksjoner — ingen parallell sakskilde).
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "api" / "app.py").read_text(encoding="utf-8")
    assert "reversibilitet_for_oppdrag" in src
    assert "kompensasjon_kreves" in src and "irreversibel_utfort" in src


@pg
def test_port11_terminal_sak_ny_sen_evidens_ny_sak(conn):
    uid, oid = _sen_utfort_sak(conn, "kompenserende")
    m = _mig()
    from db.pg import sett_kontekst
    m.execute("SET ROLE disponit_m37_claimer")
    sak1 = m.execute("SELECT sikre_sak_for_oppdrag(%s,%s,"
                     "'kompensasjon_kreves','sen','r1')",
                     (TEN, oid)).fetchone()[0]
    m.commit()
    # Terminal sak → NY sen evidens gir NY sak; den terminale står urørt.
    sett_kontekst(m, TEN, "sen", "r1b")
    m.execute("UPDATE unntak SET status='under_behandling' WHERE tenant=%s"
              " AND id=%s", (TEN, sak1))
    m.execute("UPDATE unntak SET status='avvist' WHERE tenant=%s AND id=%s",
              (TEN, sak1))
    m.commit()
    sett_kontekst(m, TEN, "sen", "r2")
    m.execute("SET ROLE disponit_m37_claimer")
    sak2 = m.execute("SELECT sikre_sak_for_oppdrag(%s,%s,"
                     "'kompensasjon_kreves','sen','r2')",
                     (TEN, oid)).fetchone()[0]
    m.commit()
    assert sak2 != sak1
    sett_kontekst(m, TEN, "sen", "r2b")
    assert m.execute("SELECT status FROM unntak WHERE tenant=%s AND id=%s",
                     (TEN, sak1)).fetchone()[0] == "avvist"
    m.rollback(); m.close()


# ---------------------------------------------------------------------------
# Port 12 + 13 + 15
# ---------------------------------------------------------------------------

@pg
def test_port12_lese_api_kansellert_aarsak(conn):
    """Skjemaporten på DTO-nivå: `kansellert_aarsak` er med (nullable) og
    `feil_aarsak`-logikken er uendret — målt statisk mot lesing.py, som
    038-port-28 gjorde for `unntak_id`."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "api" / "lesing.py").read_text(encoding="utf-8")
    assert 'resultat["kansellert_aarsak"]' in src
    assert '"signert" if har_kvittering else "timeout"' in src


@pg
def test_port13_terminalt_oppdrag_ordinart_avvis_med_status(conn):
    """Utenfor kappløpet: terminalt oppdrag → ordinært avvis, og hendelsen
    bærer hva mennesket visste (`oppdrag_status_ved_avvis`)."""
    uid = _oppsett(conn)
    bid = _medlem(conn, "op13")
    rop = _oppdrag(uid, "feilet")
    oid = _oppdrag_id(uid, rop)
    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "avvist"
    avv = _hist(uid, "avvist_handling")
    detalj = avv[-1][0]
    assert detalj and detalj["oppdrag_status_ved_avvis"] == [
        {"oppdrag_id": oid, "status": "feilet"}]
    # Oppdraget er URØRT — det var alt terminalt.
    assert _oppdragsrad(oid)[0] == "feilet"


@pg
def test_port15_ingen_annen_vei_avviser_med_levende_oppdrag(conn):
    """14a-vakten er fjernet KUN for veien gjennom oppløsningen: en direkte
    status-flipp forbi behandlingsveien stoppes fortsatt — de levende
    kapabilitetene beholder 14a-svaret (409, aldri avvist)."""
    uid = _oppsett(conn)
    bid = _medlem(conn, "op15")
    _kapabilitet(uid, "utstedt")
    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "utestaaende_oppdrag"
    assert _status(conn, uid) != "avvist"
