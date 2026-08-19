"""Gate 14b (migrasjon 043): kansellering med fencing — Codex-portene.

Hver test konstruerer sin egen tilstand (fixturene fra 14a-suiten
gjenbrukes som byggeklosser, aldri som delt tilstand).

Portkart (klarsignalets §9):
  1   test_port1_avvis_pa_levende_m37_oppdrag_kansellerer_i_en_tx
  2   test_port2_beslutningsopphavet_dekkes
  3   test_port3_sen_kvittering_fra_gammelt_claim_er_sen_evidens
      (+ INGEST-veien: test_m37.
       test_P1_sen_kvittering_etter_menneskelig_avvis_naar_evidensgrenen)
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
      test_port15b_levende_kapabilitet_blokkerer_selv_med_kansellerbart_oppdrag
  14  (ui/test/unntak14b.test.js — alertdialog + alert + axe)
  16  test_port16_definer_veiene_binder_tenanten_til_konteksten
      test_port16b_artefaktveiene_binder_tenanten_til_konteksten
  17  test_port17_lasorden_gir_avgjort_utfall_ikke_vranglas
      (+ den YTRE sakslåsen og den nye lesningen bak den, på INGEST-veien:
       test_m37.test_P1_kvitteringsveien_laser_saken_for_kapabiliteten og
       test_m37.test_P1_kvitteringen_leser_tilstanden_paa_nytt_etter_sakslasen
       test_m37.test_P1_sakslasen_dekker_beslutningsopphavet
       test_m37.test_P1_sakslaskoen_tar_ikke_kapabilitetens_frist
       test_m37.test_P1_nei_et_foder_ingen_falsk_evidensfristsak
       test_outbox_bestilling.test_reaperen_venter_aldri_paa_sakslasen)
  18  test_port18_rettighetene_er_parameterisert_pa_rollenavnet
  19  (unntaksbehandling: terminale statuser i avvis-revisjonen)
  20  test_port20_saksarsaken_naar_operatoren_over_http
  21  test_port21_opplosningen_binder_malene_til_saken
  22  test_port22_kansellert_aarsak_kan_ikke_etterstemples
  23  test_port23_verifikasjonsoppdrag_blokkerer_avvis
"""
import json
import secrets
import threading
import time

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
    oppdraget. 14a så den aldri — 043 løser den opp på samme vilkår."""
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

    # Det gamle claimets kvittering kan ikke FULLFØRE: toargsformen (som
    # den avsluttende veien bruker) klassifiserer fail-closed.
    m = _mig()
    m.execute("SET ROLE disponit_m37_claimer")
    utfall = m.execute("SELECT bruk_kvitteringskapabilitet(%s,%s)",
                       (jti, "a" * 64)).fetchone()[0]
    m.rollback()
    assert utfall == "ugyldig", "en avvist kapabilitet lot kvitteringen inn"
    from db.pg import sett_kontekst
    sett_kontekst(m, TEN, "sys", "r0")   # rollbacken tok tenant-GUC-en

    # ... men EVIDENSVEIEN må finnes (Codex P1): fencingen hindrer
    # fullføring, ikke erkjennelsen av at modulen rakk å utføre. Retryen
    # bærer samme jti, så uten `sen_evidens` var `ugyldig` svaret for evig.
    m.execute("SET ROLE disponit_m37_claimer")
    forste = m.execute("SELECT bruk_kvitteringskapabilitet(%s,%s,"
                       "'sen_evidens')", (jti, "a" * 64)).fetchone()[0]
    assert forste == "sen_evidens", forste
    # Samme kvittering igjen: idempotent. Et ANNET resultat: konflikt —
    # de samme to reglene som gjelder på den avsluttende veien.
    assert m.execute("SELECT bruk_kvitteringskapabilitet(%s,%s,"
                     "'sen_evidens')", (jti, "a" * 64)).fetchone()[0] \
        == "idempotent"
    assert m.execute("SELECT bruk_kvitteringskapabilitet(%s,%s,"
                     "'sen_evidens')", (jti, "e" * 64)).fetchone()[0] \
        == "konflikt"
    # Statusen er URØRT: `avvist` er fortsatt terminal.
    assert m.execute("SELECT status FROM kvitteringskapabiliteter WHERE"
                     " jti=%s", (jti,)).fetchone()[0] == "avvist"
    m.rollback()
    sett_kontekst(m, TEN, "sys", "r0")
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
    sql = (rot / "db" / "migrations" / "043_gate14b.sql").read_text(encoding="utf-8")
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
    from db.pg import sett_kontekst

    m = _mig()
    # (a) årsak uten kansellert status → avvist. (Skjemaets CHECK sier det
    #     samme; etter runde 7 er det overgangsvakten som rekker først, og
    #     begge melder check_violation.)
    with pytest.raises(psycopg.errors.CheckViolation):
        m.execute("UPDATE oppdrag SET kansellert_aarsak='menneskelig_avvis'"
                  " WHERE tenant=%s AND id=%s", (TEN, oid))
    m.rollback()
    sett_kontekst(m, TEN, "sys", "r0")
    # (b) ukjent årsak → CHECK (lukket enum). Settes i SAMME setning som
    #     overgangen og SOM OPPLØSNINGSVEIEN — det er den eneste formen
    #     vakten slipper gjennom, og enum-CHECKen skal fortsatt være den
    #     som stopper selve verdien her.
    m.execute("SET ROLE disponit_m37_claimer")
    with pytest.raises(psycopg.errors.CheckViolation):
        m.execute("UPDATE oppdrag SET status='kansellert',"
                  " kansellert_aarsak='fordi'"
                  " WHERE tenant=%s AND id=%s", (TEN, oid))
    m.rollback()
    sett_kontekst(m, TEN, "sys", "r0")
    # (c) immutabel når satt — også for oppløsningsveien selv, og den kan
    #     ikke fjernes.
    m.execute("SET ROLE disponit_m37_claimer")
    m.execute("UPDATE oppdrag SET status='kansellert',"
              " kansellert_aarsak='menneskelig_avvis'"
              " WHERE tenant=%s AND id=%s", (TEN, oid))
    assert m.execute("SELECT kansellert_aarsak FROM oppdrag WHERE tenant=%s"
                     " AND id=%s", (TEN, oid)).fetchone()[0] \
        == "menneskelig_avvis", "den lovlige veien ble stengt"
    with pytest.raises(psycopg.errors.CheckViolation):
        m.execute("UPDATE oppdrag SET kansellert_aarsak=NULL"
                  " WHERE tenant=%s AND id=%s", (TEN, oid))
    m.rollback(); m.close()

    # (d) FORFALSKNINGEN (Codex P2, runde 7): runtime har direkte UPDATE og
    #     kolonnelåsen tillater `opprettet -> kansellert`, så én setning
    #     kunne skrevet inn et menneskelig nei ingen har sagt. Autoriteten
    #     ligger i VEIEN: bare `avvis_med_opplosning` (definer, eid av
    #     claimer-rollen) skal kunne sette årsaken.
    uid2 = _oppsett(conn)          # egen sak: én aktiv reparasjon per sak
    oid2 = _oppdrag_id(uid2, _oppdrag(uid2, "opprettet"))
    sett_kontekst(conn, TEN, "sys", "r0")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("UPDATE oppdrag SET status='kansellert',"
                     " kansellert_aarsak='menneskelig_avvis'"
                     " WHERE tenant=%s AND id=%s", (TEN, oid2))
    conn.rollback()
    m2 = _mig()
    assert m2.execute("SELECT status, kansellert_aarsak FROM oppdrag"
                      " WHERE tenant=%s AND id=%s",
                      (TEN, oid2)).fetchone() == ("opprettet", None), \
        "runtime fikk stemplet et menneskelig nei"
    m2.rollback(); m2.close()


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
    from db.pg import sett_kontekst
    m = _mig()
    m.execute("SET ROLE disponit_m37_claimer")
    for rev, ventet_arsak in (("kompenserende", "kompensasjon_kreves"),
                              ("irreversibel", "irreversibel_utfort"),
                              ("direkte", None)):
        uid, oid = _sen_utfort_sak(conn, rev)
        # Tenantkonteksten settes PER runde: rollbacken under tar den
        # (SET LOCAL), og `reversibilitet_for_oppdrag` binder nå `p_tenant`
        # til den — som alle andre runtime-kallbare definer-veier.
        sett_kontekst(m, TEN, "sen", "r0")
        m.execute("SET ROLE disponit_m37_claimer")
        fra_db = m.execute("SELECT reversibilitet_for_oppdrag(%s,%s)",
                           (TEN, oid)).fetchone()[0]
        m.rollback()
        assert fra_db == rev
        if ventet_arsak is None:
            continue
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


@pg
def test_port15b_levende_kapabilitet_blokkerer_selv_med_kansellerbart_oppdrag(
        conn):
    """Codex P2: vakten står på KAPABILITETEN alene.

    Betinget på `not levende_opp` hoppet avvis-veien forbi
    `_flagg_avklaring` så snart det ALTSÅ fantes et kansellerbart oppdrag:
    oppdragene ble kansellert, saken merket `avvist` — og den frittstående
    arbeidskapabiliteten sto igjen BRUKBAR. Da kunne den autoriserte
    handlingen fortsatt utføres etter det menneskelige nei-et, som er
    nøyaktig det 14a finnes for å hindre."""
    uid = _oppsett(conn)
    bid = _medlem(conn, "op15b")
    _kapabilitet(uid, "utstedt")
    rop = _oppdrag(uid, "plukket")
    oid = _oppdrag_id(uid, rop)
    _kvittkap(oid)

    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "utestaaende_oppdrag", res
    assert _status(conn, uid) != "avvist"
    # INGEN delvis oppløsning: oppdraget er urørt.
    status, aarsak, _, _ = _oppdragsrad(oid)
    assert (status, aarsak) == ("plukket", None)
    assert not _hist(uid, "oppdrag_kansellert")
    assert not _hist(uid, "oppdrag_fencet")
    # ... og arbeidskapabiliteten lever fortsatt — den er grunnen til 409-en.
    m = _mig()
    m.execute("SET ROLE disponit_m37_claimer")
    assert m.execute("SELECT status FROM arbeidskapabiliteter WHERE tenant=%s"
                     " AND unntak_id=%s", (TEN, uid)).fetchall() \
        == [("utstedt",)]
    m.rollback(); m.close()


# ---------------------------------------------------------------------------
# Port 16: tenantporten på de nye definer-veiene (Codex P1)
# ---------------------------------------------------------------------------

@pg
def test_port16_definer_veiene_binder_tenanten_til_konteksten(conn):
    """`avvis_med_opplosning`, `reversibilitet_for_oppdrag` og
    `sak_utestaaende` er SECURITY DEFINER og gitt direkte til runtime.
    `p_tenant` skal derfor bindes til kallerens tenantkontekst — ikke godtas
    som parameter. Uten porten kunne en kompromittert runtime kansellere en
    ANNEN tenants levende oppdrag — eller, gjennom oppslaget, lese ut OM den
    tenantens sak har et levende oppdrag og hvilket (Codex P2, runde 7)."""
    uid = _oppsett(conn)
    _medlem(conn, "op16")
    rop = _oppdrag(uid, "plukket")
    oid = _oppdrag_id(uid, rop)
    _kvittkap(oid)
    from db.pg import koble, sett_kontekst
    m = koble(MIGRATOR_DSN)
    # Kontekst på EN ANNEN tenant enn parameteret: fail-closed.
    sett_kontekst(m, "annen-tenant", "op16", "r-op16")
    m.execute("SET ROLE disponit_m37_claimer")
    for sql, args in (
            ("SELECT utfall FROM avvis_med_opplosning(%s,%s,%s,'op16','r16')",
             (TEN, uid, [oid])),
            ("SELECT reversibilitet_for_oppdrag(%s,%s)", (TEN, oid)),
            # Oppslaget selv: 043 §4 gjorde det bredere (beslutningsopphavet),
            # og et orakel som svarer «ja, den saken har oppdrag N i status
            # plukket» er nettopp det tenantporten stenger.
            ("SELECT 1 FROM sak_utestaaende(%s,%s)", (TEN, uid))):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            m.execute(sql, args)
        m.rollback()
        sett_kontekst(m, "annen-tenant", "op16", "r-op16")
        m.execute("SET ROLE disponit_m37_claimer")
    # Uten kontekst i det hele tatt: også fail-closed.
    m.rollback()
    m.execute("SET ROLE disponit_m37_claimer")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        m.execute("SELECT utfall FROM avvis_med_opplosning(%s,%s,%s,'op16',"
                  "'r16')", (TEN, uid, [oid]))
    m.rollback(); m.close()
    # ... og oppdraget står urørt.
    assert _oppdragsrad(oid)[0] == "plukket"


@pg
def test_port16b_artefaktveiene_binder_tenanten_til_konteksten(conn):
    """Samme port på artefakthalvdelen (Codex P2, runde 3).

    `verifiser_artefaktbinding` (043 §8) og tvillingen `bevar_artefakt` —
    de to grenene av det SAMME valget på det samme kallstedet — er SECURITY
    DEFINER eid av `disponit_domene_eier` og gitt direkte til runtime.
    Eierrollen omgår artefakttabellens tenant-isolasjon, så uten porten er
    `p_tenant` kallerens frie valg: en kompromittert runtime-spørring kunne
    oppgi en ANNEN tenants uuid/oppdrag/hash og lese svaret som et orakel
    («finnes artefaktet, og er det staged/bevart?») — og `FOR UPDATE` ga i
    tillegg en kryss-tenant radlås som holdes til kallerens commit.

    Målingen bruker et artefakt som IKKE finnes: porten står FØR
    oppslaget, så kallet skal HEVE, ikke svare `ugyldig`. Uten porten er
    svaret `ugyldig` og testen dør — som den skal.

    MUTASJONEN SOM DREPER DENNE: fjern `krev_tenantkontekst` fra én av de
    to funksjonene."""
    from db.pg import koble
    ukjent = "00000000-0000-0000-0000-0000000016b0"
    kall = (("SELECT verifiser_artefaktbinding(%s::uuid,%s,%s,%s)",
             (ukjent, TEN, 1, "a" * 64)),
            ("SELECT bevar_artefakt(%s::uuid,%s,%s,%s)",
             (ukjent, TEN, 1, "a" * 64)))
    for sql, args in kall:
        # (1) Kontekst på EN ANNEN tenant enn parameteret.
        r = koble(DSN)
        try:
            r.execute("SELECT set_config('disponit.tenant','annen-tenant',"
                      "true)")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                r.execute(sql, args)
        finally:
            r.rollback(); r.close()
        # (2) Uten kontekst i det hele tatt: også fail-closed.
        r = koble(DSN)
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                r.execute(sql, args)
        finally:
            r.rollback(); r.close()


# ---------------------------------------------------------------------------
# Port 17: låseorden mot kvitteringsveien — to tabeller, én rekkefølge
# ---------------------------------------------------------------------------

@pg
def test_port17_lasorden_gir_avgjort_utfall_ikke_vranglas(conn):
    """DETERMINISTISK vranglåsmåling (Codex P1): kvitteringsveien tar
    kapabiliteten FØR oppdraget. Tok oppløsningen dem motsatt, kunne de to
    holde hver sin rad og vente på den andre — PostgreSQL avbryter da én med
    40P01, altså en vranglås i stedet for det avgjorte utfallet.

    Kappløpet konstrueres her, ikke tilfeldiggjøres: kvitteringsveien brenner
    kapabiliteten og HOLDER låsen, oppløsningen startes og skal da blokkere
    på kapabiliteten (ikke ha tatt oppdraget først), kvitteringsveien
    fullfører oppdraget og committer. Med gammel rekkefølge ville
    `UPDATE oppdrag` under ventet på oppløsningens oppdragslås = vranglås."""
    from db.pg import koble, sett_kontekst
    uid = _oppsett(conn)
    _medlem(conn, "op17")
    rop = _oppdrag(uid, "plukket")
    oid = _oppdrag_id(uid, rop)
    jti = _kvittkap(oid)

    # (1) Kvitteringsveiens rekkefølge: kapabiliteten brennes først, låsen
    #     holdes (ingen commit ennå).
    b = koble(MIGRATOR_DSN)
    sett_kontekst(b, TEN, "kvitt17", "r-kvitt17")
    b.execute("SET ROLE disponit_m37_claimer")
    assert b.execute("SELECT bruk_kvitteringskapabilitet(%s,%s)",
                     (jti, "d" * 64)).fetchone()[0] == "brukt"

    # (2) Oppløsningen startes og skal BLOKKERE på kapabiliteten.
    resultat = {}

    def los_opp():
        a = koble(MIGRATOR_DSN)
        try:
            sett_kontekst(a, TEN, "op17", "r-op17")
            a.execute("SET ROLE disponit_m37_claimer")
            resultat["rader"] = a.execute(
                "SELECT utfall, kvitteringsref FROM"
                " avvis_med_opplosning(%s,%s,%s,'op17','r-op17')",
                (TEN, uid, [oid])).fetchall()
            a.commit()
        except Exception as e:      # noqa: BLE001 — evidens ved feil
            resultat["feil"] = e
        finally:
            a.close()

    t = threading.Thread(target=los_opp)
    t.start()
    time.sleep(1.0)     # oppløsningen rekker å nå (og vente på) låsen
    assert "rader" not in resultat and "feil" not in resultat, \
        "oppløsningen gikk forbi kapabilitetslåsen"

    # (3) Kvitteringsveien fullfører oppdraget. Tok oppløsningen oppdraget
    #     FØRST, ville denne setningen vært den andre halvdelen av en
    #     vranglås.
    b.execute("RESET ROLE")
    b.execute("UPDATE oppdrag SET status='utfort' WHERE tenant=%s AND id=%s",
              (TEN, oid))
    b.commit(); b.close()

    t.join(30)
    assert not t.is_alive(), "oppløsningen ble aldri sluppet fri"
    assert "feil" not in resultat, resultat.get("feil")
    assert resultat["rader"] == [("oppdrag_utfort", "d" * 64)], resultat
    # Oppdraget er utført, ikke kansellert: kvitteringen vant kappløpet.
    status, aarsak, _, _ = _oppdragsrad(oid)
    assert (status, aarsak) == ("utfort", None)


# ---------------------------------------------------------------------------
# Port 18: rettighetene følger den KONFIGURERTE rollen (Codex P1)
# ---------------------------------------------------------------------------

def test_port18_rettighetene_er_parameterisert_pa_rollenavnet():
    """`disponit` er lokal-/testnavnet på runtime-rollen; kjøreren tar navnet
    som argument. En literal grant i migrasjonen treffer derfor feil rolle
    (eller feiler hardt) på en installasjon med et annet navn. Statisk, som
    port 8/12: den autoritative granten skal stå i den PARAMETERISERTE
    blokken, og migrasjonens egen skal være betinget av at rollen finnes."""
    import re
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    kjorer = (rot / "deploy" / "staging" / "migrer.py").read_text(encoding="utf-8")
    nye = ("bruk_kvitteringskapabilitet(TEXT, TEXT, TEXT)",
           "reversibilitet_for_oppdrag(TEXT, BIGINT)",
           "avvis_med_opplosning(TEXT, BIGINT, BIGINT[], TEXT, TEXT)")
    for sign in nye:
        assert f"GRANT EXECUTE ON FUNCTION {sign} TO {{rolle}}" in kjorer, sign
    # ... og de står i API-blokken, ikke i den DELTE M37-blokken: den kjøres
    # også for `disponit_arbeider`, og et menneskelig nei er ikke
    # arbeiderens vei.
    delt = kjorer.split('M37_RETTIGHETER = """', 1)[1].split('"""', 1)[0]
    for sign in nye:
        assert sign not in delt, f"{sign} lekker til arbeiderrollen"
    # `verifiser_artefaktbinding` (043 §8) eies av `disponit_domene_eier`, ikke
    # claimeren, så den autoritative granten hører hjemme i den generelle
    # runtime-blokken sammen med resten av artefaktveien — men den skal være
    # parameterisert på nøyaktig samme måte.
    assert ("GRANT EXECUTE ON FUNCTION verifiser_artefaktbinding(UUID, TEXT,"
            " BIGINT, TEXT) TO {rolle}") in kjorer

    sql = (Path(__file__).resolve().parents[1] / "db" / "migrations"
           / "043_gate14b.sql").read_text(encoding="utf-8")
    for treff in re.finditer(r"TO disponit\b\s*;", sql):
        foran = sql[max(0, treff.start() - 600):treff.start()]
        assert "rolname = 'disponit'" in foran, \
            "ubetinget grant til lokalnavnet " + repr(foran[-120:])


# ---------------------------------------------------------------------------
# Port 20: saksgrunnen når faktisk operatøren (Codex P2)
# ---------------------------------------------------------------------------

@pg
def test_port20_saksarsaken_naar_operatoren_over_http(conn, klient,
                                                      monkeypatch):
    """§5 føder en sak for at et MENNESKE skal handle: `kompensasjon_kreves`
    betyr «noen må kompensere manuelt», `irreversibel_utfort` «det som skjedde
    kan ikke gjøres om». Verken listen (`GET /v1/unntak`) eller detaljen
    (`GET /v1/unntak/{id}`) hentet `u.arsak`, så sakene var ikke til å skille
    fra en hvilken som helst arvet sak — saken ble altså født uten å kunne si
    det den ble født for å si.

    Målt ende-til-ende over HTTP med en ekte browserøkt, ikke statisk.

    MUTASJONEN SOM DREPER DENNE: fjern `arsak` fra DTO-en i `lesing.py`
    eller `app.py`."""
    from api import sesjon as sesjonmodul
    from db.pg import sett_kontekst
    from .test_pr012_behandle import POL, POL_HASH
    from .test_pr012_gate14a import _browsersesjon
    monkeypatch.setattr("api.policyregister.hent_aktiv",
                        lambda conn, tenant, pid: (POL, POL_HASH))
    uid = _oppsett(conn)
    bid = _medlem(conn, "les20")
    cookie, _csrf = _browsersesjon(bid)
    rop = _oppdrag(uid, "plukket")
    oid = _oppdrag_id(uid, rop)

    m = _mig()
    sett_kontekst(m, TEN, "sen", "r20")
    m.execute("SET ROLE disponit_m37_claimer")
    sak = m.execute("SELECT sikre_sak_for_oppdrag(%s,%s,"
                    "'kompensasjon_kreves','sen','r20')",
                    (TEN, oid)).fetchone()[0]
    m.commit(); m.close()

    kaker = {sesjonmodul.C_SESJON: cookie}
    d = klient.get(f"/v1/unntak/{sak}", cookies=kaker)
    assert d.status_code == 200, d.text
    assert d.json()["arsak"] == "kompensasjon_kreves", d.json()
    # ... og en ARVET sak bærer null, ikke en gjettet verdi.
    d0 = klient.get(f"/v1/unntak/{uid}", cookies=kaker)
    assert d0.status_code == 200, d0.text
    assert d0.json()["arsak"] is None, d0.json()

    # Listen er der operatøren LETER — grunnen må stå der også.
    liste = klient.get("/v1/unntak", params={"limit": 20}, cookies=kaker)
    assert liste.status_code == 200, liste.text
    per_id = {s["id"]: s for s in liste.json()["saker"]}
    assert per_id[sak]["arsak"] == "kompensasjon_kreves", per_id[sak]
    assert per_id[uid]["arsak"] is None, per_id[uid]


# ---------------------------------------------------------------------------
# Port 21: målene må høre til SAKEN, ikke bare til tenanten (Codex P1)
# ---------------------------------------------------------------------------

@pg
def test_port21_opplosningen_binder_malene_til_saken(conn):
    """`p_forventet` var bare filtrert på tenant (Codex P1, runde 4).

    Tenantporten (port 16) binder HVEM kalleren er, ikke HVA den peker på.
    `avvis_med_opplosning` er SECURITY DEFINER eid av claimeren og gitt
    direkte til runtime, så en kompromittert runtime-spørring kunne oppgi en
    hvilken som helst av sine EGNE saker sammen med id-ene til helt
    urelaterte oppdrag i samme tenant — og få dem fencet og kansellert med
    eierrollens rettigheter, mens `oppdrag_fencet`/`oppdrag_kansellert` ble
    ført på saken angriperen valgte. Skaden er dobbel: levende arbeid dør
    uten et menneskelig nei bak seg, og sporet forteller at en annen sak
    avgjorde det.

    Autoriteten ligger i sakstilknytningen, og den har de samme TO formene
    `sak_utestaaende` bruker for å finne oppdragene: reparasjonsopphavet
    (`oppdrag.unntak_id`) og beslutningsopphavet (`unntak.oppdrag_id`).
    Begge måles her — porten skal stenge fremmede oppdrag ute UTEN å stenge
    den ene lovlige veien som peker motsatt.

    En blandet mengde er ALT-ELLER-INGENTING: et delvis nei er ikke det
    mennesket sa nei til, så hele kallet skal heve og ingen av radene røres.

    MUTASJONEN SOM DREPER DENNE: fjern `v_fremmede`-porten i §7.
    """
    from db.pg import koble, sett_kontekst

    uid_a = _oppsett(conn)
    uid_b = _oppsett(conn)
    oid_a = _oppdrag_id(uid_a, _oppdrag(uid_a, "plukket"))
    oid_b = _oppdrag_id(uid_b, _oppdrag(uid_b, "plukket"))
    _kvittkap(oid_a)
    jti_b = _kvittkap(oid_b)
    foer_b = _oppdragsrad(oid_b)

    m = koble(MIGRATOR_DSN)
    try:
        # (1) Et FREMMED oppdrag alene, og (2) blandet med et lovlig: begge
        #     skal heve, som runtime ville sett det.
        for mal in ([oid_b], [oid_a, oid_b]):
            sett_kontekst(m, TEN, "op21", "r-op21")
            m.execute("SET ROLE disponit_m37_claimer")
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                m.execute("SELECT utfall FROM avvis_med_opplosning(%s,%s,%s,"
                          "'op21','r-op21')", (TEN, uid_a, mal))
            m.rollback()

        # (3) ... og den LOVLIGE mengden går fortsatt gjennom.
        sett_kontekst(m, TEN, "op21", "r-op21")
        m.execute("SET ROLE disponit_m37_claimer")
        res = m.execute(
            "SELECT utfall, oppdrag_id FROM avvis_med_opplosning(%s,%s,%s,"
            "'op21','r-op21')", (TEN, uid_a, [oid_a])).fetchall()
        m.commit()
        assert res == [("kansellert", oid_a)], res
    finally:
        m.close()

    # Det fremmede oppdraget er urørt — verken fencet eller kansellert, og
    # kapabiliteten er ikke brent.
    assert _oppdragsrad(oid_b) == foer_b, (
        f"et fremmed oppdrag ble rørt: {foer_b} → {_oppdragsrad(oid_b)}")
    assert _oppdragsrad(oid_a)[0] == "kansellert"
    mk = _mig()
    mk.execute("SET ROLE disponit_m37_claimer")
    kap_b = mk.execute("SELECT status FROM kvitteringskapabiliteter"
                       " WHERE jti=%s", (jti_b,)).fetchone()[0]
    mk.rollback(); mk.close()
    assert kap_b == "utstedt", f"fremmed kapabilitet ble brent: {kap_b}"
    # ... og sak B har ingen hendelser fra sak A-s oppløsning.
    assert _hist(uid_b, "oppdrag_kansellert") == []
    assert _hist(uid_b, "oppdrag_fencet") == []

    # (4) BESLUTNINGSOPPHAVET: saken peker på oppdraget (`unntak.oppdrag_id`,
    #     038). Det er en gyldig tilknytning, og porten skal slippe den
    #     gjennom — ellers ville den stengt nøyaktig den veien port 2 måler.
    uid_c = _oppsett(conn)
    oid_c = _oppdrag_id(uid_c, _oppdrag(uid_c, "plukket"))
    m = _mig()
    m.execute("ALTER TABLE oppdrag DISABLE TRIGGER USER")
    m.execute("UPDATE oppdrag SET unntak_id=NULL, opprinnelse='beslutning',"
              " repair_operation_id=NULL, loggpost_id=NULL"
              " WHERE tenant=%s AND id=%s", (TEN, oid_c))
    m.execute("ALTER TABLE oppdrag ENABLE TRIGGER USER")
    m.execute("ALTER TABLE unntak DISABLE TRIGGER USER")
    m.execute("UPDATE unntak SET oppdrag_id=%s, arsak='evidensfrist',"
              " sakskilde='oppdrag' WHERE tenant=%s AND id=%s",
              (oid_c, TEN, uid_c))
    m.execute("SET CONSTRAINTS ALL IMMEDIATE")
    m.execute("ALTER TABLE unntak ENABLE TRIGGER USER")
    m.commit()
    try:
        sett_kontekst(m, TEN, "op21", "r-op21")
        m.execute("SET ROLE disponit_m37_claimer")
        res = m.execute(
            "SELECT utfall, oppdrag_id FROM avvis_med_opplosning(%s,%s,%s,"
            "'op21','r-op21')", (TEN, uid_c, [oid_c])).fetchall()
        m.commit()
    finally:
        m.close()
    assert res == [("kansellert", oid_c)], (
        "porten stengte beslutningsopphavet — den andre lovlige"
        f" tilknytningen: {res}")


# ---------------------------------------------------------------------------
# Port 22: årsaken kan ikke ETTERSTEMPLES — den fødes i overgangen
# ---------------------------------------------------------------------------

@pg
def test_port22_kansellert_aarsak_kan_ikke_etterstemples(conn):
    """Codex P2 (runde 5): immutabilitet alene stengte bare OMSKRIVING.

    Vakten fyrte bare når OLD-verdien alt var satt. En rad som lenge har
    stått terminal `kansellert` med NULL årsak — en tidsavbrutt eller
    systemkansellert jobb — kunne derfor senere få `menneskelig_avvis`
    skrevet på seg: kolonnelåsen (005) tillater `OLD.status = NEW.status`,
    CHECKen er fornøyd så lenge statusen ER `kansellert`, og runtime har
    direkte UPDATE på `oppdrag`. Da ser en ordinær gammel kansellering ut
    som resultatet av et menneskelig nei — og det er nøyaktig den raden
    revisjonen (og §5-saken) leser for å skille de to.

    Årsaken er en påstand om en OVERGANG, ikke om en tilstand.

    (Rollegjerdet — at bare oppløsningsveien kan sette verdien i det hele
    tatt — måles i port 9(d), sammen med resten av kolonnekontrakten.)

    MUTASJONEN SOM DREPER DENNE: la vakten slippe gjennom når OLD-verdien
    er NULL, uansett status.
    """
    from db.pg import sett_kontekst

    uid = _oppsett(conn)
    _medlem(conn, "op22")
    oid = _oppdrag_id(uid, _oppdrag(uid, "opprettet"))
    # (d)-oppdraget får sin EGEN sak: `en_aktiv_reparasjon_per_sak` (og
    # `reparasjon_generasjon_unik`) tillater ikke to aktive reparasjoner
    # under samme unntak.
    uid2 = _oppsett(conn)
    oid2 = _oppdrag_id(uid2, _oppdrag(uid2, "opprettet"))
    m = _mig()
    # try/finally: en migrator-tilkobling som lekker med åpen transaksjon
    # holder låser resten av suiten aldri kommer forbi.
    try:
        # (a) en ordinær kansellering UTEN årsak — slik en tidsavbrutt jobb
        #     ender. Lovlig overgang, ingen påstand om et menneskelig nei.
        m.execute("UPDATE oppdrag SET status='kansellert' WHERE tenant=%s"
                  " AND id=%s", (TEN, oid))
        m.commit()

        # (b) ETTERSTEMPLINGEN: statusen røres ikke, bare årsaken settes.
        sett_kontekst(m, TEN, "sys", "r0")
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("UPDATE oppdrag SET kansellert_aarsak="
                      "'menneskelig_avvis' WHERE tenant=%s AND id=%s",
                      (TEN, oid))
        m.rollback()
        sett_kontekst(m, TEN, "sys", "r0")
        assert m.execute("SELECT kansellert_aarsak FROM oppdrag WHERE"
                         " tenant=%s AND id=%s",
                         (TEN, oid)).fetchone()[0] is None, \
            "årsaken ble etterstemplet på en alt terminal kansellering"
        m.rollback()

        # (c) ... og den kan heller ikke FØDES ferdig ved INSERT — et
        #     oppdrag opprettes aldri allerede kansellert.
        sett_kontekst(m, TEN, "sys", "r0")
        lid, key_id = m.execute("SELECT loggpost_id, key_id FROM unntak"
                                " WHERE tenant=%s AND id=%s",
                                (TEN, uid)).fetchone()
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute(
                "INSERT INTO oppdrag (opprinnelse,tenant,unntak_id,"
                "loggpost_id,oppdragstype,handling,eiermodul,status,"
                "kansellert_aarsak,payload_kryptert,key_id,nonce,"
                "utforelsesfrist,evidensfrist)"
                " VALUES ('m37_reparasjon',%s,%s,%s,'reparasjon',"
                "'faktura.bokfor','eier','kansellert','menneskelig_avvis',"
                "%s,%s,%s,now()+interval '1 hour',now()+interval '2 hour')",
                (TEN, uid, lid, b"\x00", key_id, b"\x00" * 12))
        m.rollback()

        # (d) DEN LOVLIGE VEIEN STÅR: årsaken settes i samme setning som
        #     overgangen, og av oppløsningsveiens rolle — nøyaktig slik §7
        #     gjør det (SECURITY DEFINER, eid av claimer).
        sett_kontekst(m, TEN, "sys", "r0")
        m.execute("SET ROLE disponit_m37_claimer")
        m.execute("UPDATE oppdrag SET status='kansellert',"
                  " kansellert_aarsak='menneskelig_avvis'"
                  " WHERE tenant=%s AND id=%s", (TEN, oid2))
        assert m.execute("SELECT kansellert_aarsak FROM oppdrag WHERE"
                         " tenant=%s AND id=%s", (TEN, oid2)).fetchone()[0] \
            == "menneskelig_avvis", "vakten stengte den lovlige overgangen"
        m.rollback()
    finally:
        m.rollback(); m.close()


# ---------------------------------------------------------------------------
# Port 23: verifikasjonsoppdrag har ingen oppløsningsvei — nei-et blokkeres
# ---------------------------------------------------------------------------

@pg
def test_port23_verifikasjonsoppdrag_blokkerer_avvis(conn):
    """Codex P2 (runde 6): oppløsningsløkka var UTYPET.

    Den brente kapabiliteten `avvist` og kansellerte raden uansett
    oppdragstype. For et `verifikasjon`-oppdrag er det en halv oppløsning:
    kvitteringsingesten forgrener seg til `_ingest_verifikasjon` FØR
    sakslåsen og hele sen-evidensveien, og den veien bruker fortsatt den
    ordinære toargsbrenningen. En korrekt signert verifikasjonskvittering
    som kom fram etter nei-et ble derfor rullet tilbake som
    `kapabilitet_ugyldig` i stedet for bevart som fencet evidens — det
    stille tapet §5 finnes for å hindre, i den ene oppdragsfamilien §5 ikke
    dekker.

    Samme regel som for en levende ARBEIDSkapabilitet, av samme grunn: en
    vakt uten utvei er bedre enn en stille avvisning av evidens.

    MUTASJONEN SOM DREPER DENNE: fjern `uloselige` fra vakten i
    `unntaksbehandling`, eller verifikasjonsporten i 043 §7.
    """
    from db.pg import koble, sett_kontekst

    uid = _oppsett(conn)
    bid = _medlem(conn, "op23")
    rop = _oppdrag(uid, "plukket")
    oid = _oppdrag_id(uid, rop)
    _kvittkap(oid)
    m = _mig()
    m.execute("ALTER TABLE oppdrag DISABLE TRIGGER USER")
    # `oppdrag_kobling_konsistent` (008): et verifikasjonsoppdrag bærer
    # koblingsstatus VERIFIKASJON og ingen beslutnings-FK.
    m.execute("UPDATE oppdrag SET oppdragstype='verifikasjon',"
              " koblingsstatus='VERIFIKASJON', beslutning_loggpost_id=NULL"
              " WHERE tenant=%s AND id=%s", (TEN, oid))
    m.execute("ALTER TABLE oppdrag ENABLE TRIGGER USER")
    m.commit(); m.close()

    # (a) HTTP-veien: nei-et blokkeres, ingenting røres.
    res = _kall(conn, uid, "avvis", bid, _macreg())
    assert res["utfall"] == "utestaaende_oppdrag", res
    assert _status(conn, uid) != "avvist"
    status, aarsak, _, _ = _oppdragsrad(oid)
    assert (status, aarsak) == ("plukket", None), (status, aarsak)
    assert not _hist(uid, "oppdrag_kansellert")
    assert not _hist(uid, "oppdrag_fencet")

    # (b) ... og basen håndhever den SAMME regelen, så en direkte kaller
    #     ikke kan omgå API-vakten.
    m = koble(MIGRATOR_DSN)
    try:
        sett_kontekst(m, TEN, "op23", "r-op23")
        m.execute("SET ROLE disponit_m37_claimer")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            m.execute("SELECT utfall FROM avvis_med_opplosning(%s,%s,%s,"
                      "'op23','r-op23')", (TEN, uid, [oid]))
    finally:
        m.rollback(); m.close()

    # Kvitteringskapabiliteten lever fortsatt — den er hele poenget.
    m = _mig()
    m.execute("SET ROLE disponit_m37_claimer")
    assert m.execute("SELECT status FROM kvitteringskapabiliteter"
                     " WHERE tenant=%s AND oppdrag_id=%s",
                     (TEN, oid)).fetchall() == [("utstedt",)]
    m.rollback(); m.close()
