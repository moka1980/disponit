"""PR-014a CP5: claim ↔ modulkontrakt-binding under oppdragslåsen.

Et oppdrag hvis oppdragstype er REGISTRERT kan bare claimes når eiermodulen er
aktiv med en claiming-deployment for den AUTORISERTE kontrakten (port 4/5).
Uregistrerte oppdragstyper claimes som før (legacy — ingen M-37-flyt endres).
Reclaim bumper owner-generasjonen (port 7/V2); en retired release gjør ikke
et åpent claim ugyldig (port 8/V3); release/deployment kan aldri slettes
(port 9). Hver invariant har en mutasjon som MÅ feile.
"""
import secrets

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, TENANT, migrator, miljo  # noqa: F401
from .test_m37 import _lag_sak, _sett_kontekst
from .test_pr014a_modulregister import (_admin, _reg_kontrakt, _reg_release)

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _tok():
    return secrets.token_hex(4)


def _lag_oppdrag_type(conn, tenant, sak_id, loggpost_id, *, oppdragstype,
                      eiermodul, handling="purring.send"):
    """Som test_m37._lag_oppdrag, men med parametrisert oppdragstype/eiermodul.

    (Hjelperen der pinner oppdragstype='reinnsending'; CP5 trenger egne,
    UNIKE typer så en registrering ikke smitter over på legacy-oppdrag.)
    """
    from db import kryptering
    rid = secrets.token_hex(32)
    _sett_kontekst(conn, tenant)
    conn.execute(
        "INSERT INTO reparasjonsoperasjoner (tenant, unntak_id,"
        " repair_operation_id, repair_generation, handler_id, handler_versjon,"
        " maalhandling, input_hash, kategori)"
        " VALUES (%s,%s,%s,0,'r1_reinnsending','1',%s,%s,'manglende_data')"
        " ON CONFLICT DO NOTHING",
        (tenant, sak_id, rid, handling, secrets.token_hex(32)))
    beslutning = conn.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'test','arbeidskapabilitet','ih2','p@1.0.0/x.y',"
        " 'TILLAT','[]',%s) RETURNING id", (tenant, rid)).fetchone()[0]
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
    ct, nonce = kryptering.krypter(
        dek, {"handling": handling, "ressurs_id": "fak-1"}, tenant, key_id)
    opp = conn.execute(
        "INSERT INTO oppdrag (tenant, unntak_id, loggpost_id,"
        " repair_operation_id, oppdragstype, handling, eiermodul,"
        " payload_kryptert, key_id, nonce, utforelsesfrist, evidensfrist,"
        " beslutning_loggpost_id, koblingsstatus)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        " now()+interval '1 hour', now()+interval '30 days',"
        " %s,'KOBLET') RETURNING id",
        (tenant, sak_id, loggpost_id, rid, oppdragstype, handling, eiermodul,
         ct, key_id, nonce, beslutning)).fetchone()[0]
    conn.commit()
    return int(opp), rid


def _claim(modul_id, prefiks, cid, lease=300, release=None, miljo=None,
           epoch=None):
    """Kaller claim_neste_oppdrag som RUNTIME (den har EXECUTE, ingen andre).

    release/miljo/epoch er kallerens deployment-identitet (Codex P1): en
    registrert oppdragstype claimes bare når NETTOPP den deploymenten er
    claiming. Legacy/uregistrert lar dem være NULL.
    """
    from db.pg import koble
    c = koble(DSN)
    try:
        c.execute("SELECT set_config('disponit.aktor','m37',true),"
                  "       set_config('disponit.request_id','r',true)")
        rad = c.execute(
            "SELECT id, owner_generation FROM claim_neste_oppdrag("
            "%s,%s,%s,%s,%s,%s,%s)",
            (modul_id, prefiks, cid, lease, release, miljo, epoch)).fetchone()
        c.commit()
        return rad
    finally:
        c.close()


def _binding(conn, opp):
    _sett_kontekst(conn, TENANT)
    rad = conn.execute("SELECT modul_id, kontraktversjon, kontrakt_hash,"
                       " module_epoch, status, owner_generation FROM oppdrag"
                       " WHERE tenant=%s AND id=%s", (TENANT, opp)).fetchone()
    conn.rollback()
    return rad


def _aktiver_modul(a, modul_id, kh, *, rel="r1", ver=1):
    """Registrer kontrakt+release, installer, aktiver m/ claiming-deployment."""
    _reg_kontrakt(a, modul_id, kh, ver)
    _reg_release(a, modul_id, rel, kh, ver)
    a.execute("SELECT installer_modul(%s,'sys')", (modul_id,))
    a.execute("SELECT sett_modulstatus(%s,'staging_verifisert',NULL,'sys')",
              (modul_id,))
    a.execute("SELECT bytt_release(%s,'staging',%s,%s,%s,'sys')",
              (modul_id, rel, ver, kh))
    a.execute("SELECT sett_modulstatus(%s,'aktiv',%s,'sys')", (modul_id, rel))
    a.commit()


@pg
def test_port5_registrert_oppdragstype_krever_aktiv_claiming_kontrakt(migrator):
    # Port 5: en registrert oppdragstype er ikke claimbar uten en aktiv modul
    # med claiming-deployment for den autoriserte kontrakten; når den finnes,
    # stemples kontrakt+epoch på oppdraget.
    t = _tok(); modul = "cp5mod-" + t; ot = "cp5-" + t; kh = "k-" + t
    a = _admin()
    try:
        _reg_kontrakt(a, modul, kh)
        a.execute("SELECT registrer_oppdragstype(%s,%s,1,%s,'sys')",
                  (ot, modul, kh)); a.commit()
    finally:
        a.close()
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag_type(migrator, TENANT, sak, logg,
                               oppdragstype=ot, eiermodul=modul)
    # Ingen aktiv modul ennå → registrert type er IKKE claimbar.
    assert _claim(modul, ["purring."], secrets.token_hex(16)) is None, \
        "registrert oppdragstype ble claimet uten aktiv claiming-kontrakt"
    # Aktiver modulen med en claiming-deployment for (v1, kh).
    a = _admin()
    try:
        # kontrakten er alt registrert over; installer+release+aktiver.
        _reg_release(a, modul, "r1", kh)
        a.execute("SELECT installer_modul(%s,'sys')", (modul,))
        a.execute("SELECT sett_modulstatus(%s,'staging_verifisert',NULL,'sys')",
                  (modul,))
        a.execute("SELECT bytt_release(%s,'staging','r1',1,%s,'sys')", (modul, kh))
        a.execute("SELECT sett_modulstatus(%s,'aktiv','r1','sys')", (modul,))
        a.commit()
    finally:
        a.close()
    got = _claim(modul, ["purring."], secrets.token_hex(16),
                 release="r1", miljo="staging", epoch=0)
    assert got is not None and got[0] == opp, "aktiv kontrakt ga ikke claim"
    b = _binding(migrator, opp)
    assert b[0] == modul and b[1] == 1 and b[2] == kh, "kontrakt ikke stemplet"
    assert b[3] == 0, "module_epoch ikke stemplet"
    # Codex P1: samme modul, men FEIL deployment-identitet (annen release) →
    # ikke claimbar (drenert/retired r1-prosess kan ikke claime via en annen).
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE oppdrag SET status='opprettet', owner_claim_id=NULL,"
                     " owner_lease_utloper=NULL WHERE tenant=%s AND id=%s",
                     (TENANT, opp)); migrator.commit()
    assert _claim(modul, ["purring."], secrets.token_hex(16),
                  release="feil-release", miljo="staging", epoch=0) is None, \
        "claimet med feil deployment-identitet"


@pg
def test_port4_feil_kontraktversjon_er_ikke_claimbar(migrator):
    # Port 4: rett modul, men den eneste claiming-deploymenten er for en ANNEN
    # kontraktversjon enn oppdragstypen autoriserer → ikke claimbar.
    t = _tok(); modul = "cp5mod-" + t; ot = "cp5-" + t
    khA = "kA-" + t; khB = "kB-" + t
    a = _admin()
    try:
        _reg_kontrakt(a, modul, khA, ver=1)          # autorisert: v1/khA
        _reg_kontrakt(a, modul, khB, ver=2)          # finnes, men ikke autorisert
        a.execute("SELECT registrer_oppdragstype(%s,%s,1,%s,'sys')",
                  (ot, modul, khA)); a.commit()
        # aktiv modul, men claiming-deployment er for v2/khB (feil versjon).
        _reg_release(a, modul, "r2", khB, ver=2)
        a.execute("SELECT installer_modul(%s,'sys')", (modul,))
        a.execute("SELECT sett_modulstatus(%s,'staging_verifisert',NULL,'sys')",
                  (modul,))
        a.execute("SELECT bytt_release(%s,'staging','r2',2,%s,'sys')", (modul, khB))
        a.execute("SELECT sett_modulstatus(%s,'aktiv','r2','sys')", (modul,))
        a.commit()
    finally:
        a.close()
    sak, logg = _lag_sak(migrator, TENANT)
    _lag_oppdrag_type(migrator, TENANT, sak, logg, oppdragstype=ot, eiermodul=modul)
    assert _claim(modul, ["purring."], secrets.token_hex(16)) is None, \
        "claimet en autorisert v1-kontrakt mot en claiming v2-deployment"


@pg
def test_port7_reclaim_bumper_generasjon_og_bevarer_binding(migrator):
    # Port 7 / V2: utløpt lease → reclaim med ØKT owner_generation (gammel
    # kvittering blir stale); kontraktbindingen er write-once og bevares.
    t = _tok(); modul = "cp5mod-" + t; ot = "cp5-" + t; kh = "k-" + t
    a = _admin()
    try:
        _reg_kontrakt(a, modul, kh)
        a.execute("SELECT registrer_oppdragstype(%s,%s,1,%s,'sys')",
                  (ot, modul, kh)); a.commit()
    finally:
        a.close()
    a = _admin()
    try:
        _reg_release(a, modul, "r1", kh)
        a.execute("SELECT installer_modul(%s,'sys')", (modul,))
        a.execute("SELECT sett_modulstatus(%s,'staging_verifisert',NULL,'sys')",
                  (modul,))
        a.execute("SELECT bytt_release(%s,'staging','r1',1,%s,'sys')", (modul, kh))
        a.execute("SELECT sett_modulstatus(%s,'aktiv','r1','sys')", (modul,))
        a.commit()
    finally:
        a.close()
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag_type(migrator, TENANT, sak, logg,
                               oppdragstype=ot, eiermodul=modul)
    first = _claim(modul, ["purring."], secrets.token_hex(16),
                   release="r1", miljo="staging", epoch=0)
    assert first is not None and first[1] == 1
    b1 = _binding(migrator, opp)
    # tving leasen utløpt.
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE oppdrag SET owner_lease_utloper=now()-interval '1 s'"
                     " WHERE tenant=%s AND id=%s", (TENANT, opp))
    migrator.commit()
    second = _claim(modul, ["purring."], secrets.token_hex(16),
                    release="r1", miljo="staging", epoch=0)
    assert second is not None and second[0] == opp and second[1] == 2, \
        "reclaim økte ikke owner_generation"
    b2 = _binding(migrator, opp)
    assert (b2[0], b2[1], b2[2]) == (b1[0], b1[1], b1[2]), \
        "kontraktbindingen endret seg ved reclaim (skal være write-once)"


@pg
def test_port8_retired_release_ugyldiggjor_ikke_apent_claim(migrator):
    # Port 8 / V3: en retired release gjør IKKE et åpent (ikke-reclaimet) claim
    # ugyldig — oppdraget kan fortsatt avsluttes innen evidensfrist.
    t = _tok(); modul = "cp5mod-" + t; ot = "cp5-" + t; kh = "k-" + t
    a = _admin()
    try:
        _reg_kontrakt(a, modul, kh)
        a.execute("SELECT registrer_oppdragstype(%s,%s,1,%s,'sys')",
                  (ot, modul, kh)); a.commit()
        _reg_release(a, modul, "r1", kh)
        a.execute("SELECT installer_modul(%s,'sys')", (modul,))
        a.execute("SELECT sett_modulstatus(%s,'staging_verifisert',NULL,'sys')",
                  (modul,))
        a.execute("SELECT bytt_release(%s,'staging','r1',1,%s,'sys')", (modul, kh))
        a.execute("SELECT sett_modulstatus(%s,'aktiv','r1','sys')", (modul,))
        a.commit()
    finally:
        a.close()
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag_type(migrator, TENANT, sak, logg,
                               oppdragstype=ot, eiermodul=modul)
    got = _claim(modul, ["purring."], secrets.token_hex(16),
                 release="r1", miljo="staging", epoch=0)
    assert got is not None and got[0] == opp
    # retire r1: bytt til r2 (r1→draining), pensjoner r1.
    a = _admin()
    try:
        _reg_release(a, modul, "r2", kh)
        a.execute("SELECT bytt_release(%s,'staging','r2',1,%s,'sys')", (modul, kh))
        a.execute("SELECT pensjoner_release(%s,'staging','r1','sys')", (modul,))
        a.commit()
    finally:
        a.close()
    # det åpne claimet kan fortsatt avsluttes (status→utfort innen evidensfrist).
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE oppdrag SET status='utfort', resultathash=%s"
                     " WHERE tenant=%s AND id=%s",
                     ("rh-" + t, TENANT, opp))
    migrator.commit()
    assert _binding(migrator, opp)[4] == "utfort", \
        "retired release blokkerte avslutning av et åpent claim (V3-brudd)"


@pg
def test_port9_release_og_deployment_kan_ikke_slettes(migrator):
    # Port 9: verken release eller deployment kan slettes mens bindinger finnes.
    t = _tok(); modul = "cp5mod-" + t; kh = "k-" + t
    a = _admin()
    try:
        _aktiver_modul(a, modul, kh)
    finally:
        a.close()
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("DELETE FROM modulrelease WHERE modul_id=%s", (modul,))
    migrator.rollback()
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("DELETE FROM moduldeployment WHERE modul_id=%s", (modul,))
    migrator.rollback()


@pg
def test_legacy_uregistrert_oppdragstype_claimes_som_for(migrator):
    # Den betingede bindingen rører IKKE legacy: en UREGISTRERT oppdragstype
    # claimes uendret, og kontraktfeltene forblir NULL.
    t = _tok(); modul = "eiermodul:legacy-" + t; ot = "legacy-" + t
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag_type(migrator, TENANT, sak, logg,
                               oppdragstype=ot, eiermodul=modul)
    got = _claim(modul, ["purring."], secrets.token_hex(16))
    assert got is not None and got[0] == opp, "legacy-oppdrag ble ikke claimet"
    b = _binding(migrator, opp)
    assert b[0] is None and b[1] is None and b[2] is None and b[3] is None, \
        "uregistrert oppdragstype fikk kontraktbinding"


@pg
def test_runtime_kan_ikke_sette_oppdrag_binding_direkte(migrator):
    # Codex P1/P2: bare den herdede claim-funksjonen (disponit_m37_claimer) kan
    # sette/endre kontraktbinding+epoch — en direkte UPDATE fra en annen rolle
    # avvises (ellers kunne runtime forfalske bindingen eller nulle epoch).
    sak, logg = _lag_sak(migrator, TENANT)
    opp, _ = _lag_oppdrag_type(migrator, TENANT, sak, logg,
                               oppdragstype="legacy-" + _tok(),
                               eiermodul="eiermodul:x")
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE oppdrag SET modul_id='forfalsk' WHERE tenant=%s"
                         " AND id=%s", (TENANT, opp))
    migrator.rollback()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE oppdrag SET module_epoch=5 WHERE tenant=%s AND"
                         " id=%s", (TENANT, opp))
    migrator.rollback()


@pg
def test_oppdrag_binding_kan_ikke_settes_ved_insert(migrator):
    # Codex P1: runtime har direkte INSERT — BEFORE INSERT-vakten hindrer at en
    # rad opprettes med forfalsket binding (vakten fyrer FØR FK/RLS-sjekkene).
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute(
            "INSERT INTO oppdrag (tenant,unntak_id,loggpost_id,repair_operation_id,"
            "oppdragstype,handling,eiermodul,payload_kryptert,key_id,nonce,"
            "utforelsesfrist,evidensfrist,modul_id) VALUES (%s,1,1,'x',"
            "'reinnsending','purring.send','em','\\x00','k','\\x00',now(),"
            "now()+interval '1 day','forfalsk')", (TENANT,))
    migrator.rollback()


def _claim_apen(modul_id, prefiks, cid, *, release, miljo, epoch,
                lock_timeout="2s"):
    """Som `_claim`, men lar transaksjonen stå ÅPEN — låsene beholdes.

    Kalleren må rulle tilbake og lukke. `lock_timeout` gjør en blokkering
    målbar: venter kallet på modul-låsen, feiler det i stedet for å henge.
    """
    from db.pg import koble
    c = koble(DSN)
    try:
        c.execute("SELECT set_config('disponit.aktor','m37',true),"
                  "       set_config('disponit.request_id','r',true)")
        # set_config(...,true) = SET LOCAL; SET tar ikke parametre.
        c.execute("SELECT set_config('lock_timeout', %s, true)", (lock_timeout,))
        rad = c.execute(
            "SELECT id FROM claim_neste_oppdrag(%s,%s,%s,%s,%s,%s,%s)",
            (modul_id, prefiks, cid, 300, release, miljo, epoch)).fetchone()
        return c, rad
    except BaseException:
        c.close()
        raise


@pg
def test_claim_deler_modullaasen_men_gjerder_fortsatt_nodstopp(migrator):
    # Codex P2: modul-låsen tas DELT av claims. En eksklusiv lås serialiserte
    # HVERT claim for samme modul — og API-et slipper den først ved commit,
    # etter dekryptering, minimering og kapabilitetsutstedelse — så modulens
    # pollere køet bak hele request-transaksjonen selv når SKIP LOCKED alt
    # hadde gitt dem hver sin rad. Gjerdet mot nødstopp (eksklusiv lås) står.
    t = _tok(); modul = "cp5mod-" + t; ot = "cp5-" + t; kh = "k-" + t
    a = _admin()
    try:
        _reg_kontrakt(a, modul, kh)
        a.execute("SELECT registrer_oppdragstype(%s,%s,1,%s,'sys')",
                  (ot, modul, kh)); a.commit()
        _aktiver_modul(a, modul, kh)
    finally:
        a.close()
    for _ in range(2):
        sak, logg = _lag_sak(migrator, TENANT)
        _lag_oppdrag_type(migrator, TENANT, sak, logg, oppdragstype=ot,
                          eiermodul=modul)

    c1, r1 = _claim_apen(modul, ["purring."], secrets.token_hex(16),
                         release="r1", miljo="staging", epoch=0)
    try:
        assert r1 is not None, "første claim feilet"
        # Samtidig claim for SAMME modul skal ikke vente på den delte låsen.
        c2, r2 = _claim_apen(modul, ["purring."], secrets.token_hex(16),
                             release="r1", miljo="staging", epoch=0)
        try:
            assert r2 is not None and r2[0] != r1[0], \
                "samtidig claim for samme modul ble blokkert/tomt"
        finally:
            c2.rollback(); c2.close()
        # ... men nødstoppet, som tar låsen EKSKLUSIVT, må fortsatt vente på
        # at det åpne claimet committer (her: til lock_timeout slår inn).
        n = _admin()
        try:
            n.execute("SET LOCAL lock_timeout = '2s'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                n.execute("SELECT noddeaktiver_modul(%s,'test','sys')", (modul,))
            n.rollback()
        finally:
            n.close()
    finally:
        c1.rollback(); c1.close()


@pg
def test_claim_deler_oppdragstype_gjerdet_med_registrering(migrator):
    # Codex P1: «uregistrert» er en avgjørelse tatt på et snapshot. Uten et delt
    # gjerde kunne `registrer_oppdragstype` committe ETTER at claimet leste
    # registeret, men FØR claimet committet — og oppdraget ble claimet UBUNDET,
    # forbi aktiv-modul-/deployment-portene registreringen nettopp innførte.
    # Claimet tar derfor SAMME globale nøkkel som registreringen, men DELT.
    t = _tok(); modul = "cp5mod-" + t; ot = "cp5-" + t; kh = "k-" + t
    a = _admin()
    try:
        _reg_kontrakt(a, modul, kh)
        a.execute("SELECT registrer_oppdragstype(%s,%s,1,%s,'sys')",
                  (ot, modul, kh)); a.commit()
        _aktiver_modul(a, modul, kh)
    finally:
        a.close()
    sak, logg = _lag_sak(migrator, TENANT)
    _lag_oppdrag_type(migrator, TENANT, sak, logg, oppdragstype=ot,
                      eiermodul=modul)

    # En ÅPEN registrering holder nøkkelen EKSKLUSIVT → claimet må vente
    # (målbart som lock_timeout i _claim_apen i stedet for en hengende test).
    reg = _admin()
    try:
        reg.execute("SELECT registrer_oppdragstype(%s,%s,1,%s,'sys')",
                    ("cp5annen-" + t, modul, kh))          # ikke committet
        with pytest.raises(psycopg.errors.LockNotAvailable):
            _claim_apen(modul, ["purring."], secrets.token_hex(16),
                        release="r1", miljo="staging", epoch=0)
        reg.rollback()
    finally:
        reg.close()

    # Uten en pågående registrering går claimet gjennom som før — det delte
    # gjerdet sperrer bare mot samtidig registrering, ikke mot claims.
    assert _claim(modul, ["purring."], secrets.token_hex(16),
                  release="r1", miljo="staging", epoch=0) is not None, \
        "gjerdet blokkerte et claim uten samtidig registrering"
