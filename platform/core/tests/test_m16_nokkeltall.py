"""M-16 nøkkeltall — dataportene fra klarsignalet (1–10) + fase 2.

Definer-settet bor i 084 (fase 2, som overtok for 051): de statiske
portene måler DEN fila. Grensen: tall er tellinger over rader som
finnes, radvise varigheter er eneste differanseform, og suminvarianten
per partisjon holder fordi gruppene og totalen kommer fra SAMME skann
(GROUPING SETS — ett snapshot), også under samtidig skriving. Fase 2
legger avledede tall VED SIDEN AV de rå tellingene de er regnet fra
(andeler, lukketid-snitt): divisjonen bor i API-laget, aldri i
definerne og aldri i flaten. Alle tester konstruerer egen tilstand.
Ingen delt fixture.
"""
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, TENANT, app, dekker, klient, \
    migrator, miljo, token  # noqa: F401
from .test_pr013_policyadmin import (TEN as POLTEN, _attest, _c, _runde,
                                     _validert_utkast)
from .test_m37 import _sett_kontekst

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

ROT = Path(__file__).resolve().parents[3]
M084 = ROT / "platform/core/db/migrations/084_m16_fase2.sql"
M086 = ROT / "platform/core/db/migrations/086_m16_indekser.sql"
FLATE = ROT / "platform/core/ui/static/js/flater/nokkeltall.js"


def _rt():
    from db.pg import koble
    return koble(DSN)


def _logg(m, tenant, ts=None, beslutning="TILLAT"):
    m.execute(
        "INSERT INTO revisjonslogg (tenant, input_hash, policy_id,"
        " beslutning, begrunnelse, idempotency_key, kilde, ts) VALUES"
        " (%s,'h','p',%s,'[]'::jsonb,%s,'arbeidskapabilitet',"
        " coalesce(%s, now()))",
        (tenant, beslutning, secrets.token_hex(8), ts))


def _sak(m, tenant, *, ts=None, status="manuell", status_ts=None,
         kategori="over_grense", sakstype="normal"):
    # `manuell` er ÅPEN men ikke claimbar: `status='ny'` er det eneste
    # claim-bare (005), og en telle-test skal aldri etterlate saker
    # m37-kappløpstesten kan plukke opp på tvers av tenants.
    m.execute("INSERT INTO tenant_nokler (tenant, key_id, wrapped_dek)"
              " VALUES (%s,'k1','\\x00'::bytea) ON CONFLICT DO NOTHING",
              (tenant,))
    ct, key_id, nonce = b"\x00", "k1", b"\x00"
    lid = m.execute(
        "INSERT INTO revisjonslogg (tenant, input_hash, policy_id,"
        " beslutning, begrunnelse, idempotency_key, kilde) VALUES"
        " (%s,'h','p','UNNTAK','[]'::jsonb,%s,'arbeidskapabilitet')"
        " RETURNING id", (tenant, secrets.token_hex(8))).fetchone()[0]
    return m.execute(
        "INSERT INTO unntak (tenant, loggpost_id, handling, kategori,"
        " sakstype, prioritet, status, payload_kryptert, key_id, nonce,"
        " sakskilde, maks_auto_forsok_snapshot, policy_versjon,"
        " policy_content_hash, ts, status_ts) VALUES (%s,%s,'utbetaling',"
        "%s,%s,'hoy',%s,%s,%s,%s,'policybrudd',3,'1.0.0',%s,"
        " coalesce(%s, now()), coalesce(%s, now()))"
        " RETURNING id",
        (tenant, lid, kategori, sakstype, status, ct, key_id, nonce,
         "c" * 64, ts, status_ts)).fetchone()[0]


#: Sakstypesettet et token MED `security:read` ser (app.SAKSTYPER).
ALLE_SAKSTYPER = ["normal", "sikkerhet", "drift"]
TERM = ["løst", "avvist"]


def _kall(conn, tenant, fn, fra, til, *ekstra):
    _sett_kontekst(conn, tenant)
    args = (tenant, fra, til, *ekstra)
    rader = conn.execute(
        f"SELECT er_total, nokkel, antall FROM"
        f" {fn}({','.join(['%s'] * len(args))})",
        args).fetchall()
    conn.rollback()
    return _del(rader)


def _del(rader):
    """(er_total, nokkel, antall) → (total, deler).

    Skillet er RADENS egenskap, ikke en reservert nøkkelverdi: ingen
    kategoristreng kan derfor forveksles med aggregatet.
    """
    total = next((a for er_total, _, a in rader if er_total), 0)
    return total, {k: a for er_total, k, a in rader if not er_total}


# ---------------------------------------------------------------------------

@pg
def test_suminvariant_under_samtidig_skriving(migrator):
    """Port 1: gruppene og totalen kommer fra samme skann — mens en
    annen forbindelse skriver, skal HVERT svar summere eksakt."""
    ten = "t-m16-" + secrets.token_hex(3)
    _sett_kontekst(migrator, ten)
    for _ in range(5):
        _logg(migrator, ten)
    migrator.commit()
    fra = datetime.now(timezone.utc) - timedelta(hours=1)
    til = datetime.now(timezone.utc) + timedelta(hours=1)

    stopp = threading.Event()
    feil: list = []

    def skriver():
        from db.pg import koble
        c = koble(MIGRATOR_DSN)
        try:
            while not stopp.is_set():
                _sett_kontekst(c, ten)
                _logg(c, ten, beslutning="UNNTAK")
                c.commit()
        except Exception as e:      # pragma: no cover
            feil.append(e)
        finally:
            c.close()

    tr = threading.Thread(target=skriver)
    tr.start()
    try:
        rt = _rt()
        try:
            for _ in range(25):
                total, deler = _kall(rt, ten, "m16_beslutninger", fra, til)
                assert sum(deler.values()) == total, (total, deler)
                assert total >= 5
        finally:
            rt.close()
    finally:
        stopp.set()
        tr.join()
    assert not feil


@pg
def test_ukjent_verdi_telles_synlig_i_totalen(migrator):
    """Port 1, ukjent-halvdelen: den EKTE NULL-bæreren i dag er
    `pakrevd_antall` på en historisk aktiveringshendelse — den blir
    «ukjent» i kvorumskrav-partisjonen, i egen gruppe OG i totalen,
    aldri stille utenfor."""
    c = _c()
    try:
        uid = "u-m16-" + secrets.token_hex(4)
        pid = "p-m16-" + secrets.token_hex(3)
        opid = "op-" + secrets.token_hex(4)
        c.execute(
            "INSERT INTO policyutkast (tenant, utkast_id, policy_id,"
            " innhold, innholds_hash, status, opprettet_av) VALUES"
            " (%s,%s,%s,'{}'::jsonb,'ih','aktivert','forf')",
            (POLTEN, uid, pid))
        c.execute(
            "INSERT INTO aktiveringsrunde (tenant, utkast_id, runde,"
            " status, diff_hash, utkast_innholds_hash, base_policy_hash,"
            " risikoklasse, klassifisering_hash, klassifikatorversjon,"
            " policyskjema_versjon, motor_semantikkversjon, deny_all_hash,"
            " deny_all_versjon, pakrevd_antall_godkjennere, utloper,"
            " decision_operation_id, aktivert_som_versjon) VALUES"
            " (%s,%s,1,'brukt','d','ih','b','UTVIDER','k','1','0.2','1',"
            "'dh','1',2, now()+interval '1 hour',%s,'1.0.0')",
            (POLTEN, uid, opid))
        c.execute(
            "INSERT INTO aktiveringsattestasjon (tenant, utkast_id,"
            " runde, bruker_id, rolle, authz_version, er_forfatter,"
            " diff_hash, klassifisering_hash, risikoklasse,"
            " konvoluttversjon, konvolutt_hash, mac, mac_key_id, jti,"
            " utloper) VALUES (%s,%s,1,'uavh','okonomi',1,false,'d','k',"
            "'UTVIDER',1,'h','m','mk1',%s, now()+interval '1 hour')",
            (POLTEN, uid, secrets.token_hex(16)))
        c.execute(
            "INSERT INTO policyaktivering (tenant, policy_id, utkast_id,"
            " runde, decision_operation_id, versjon, innholds_hash,"
            " diff_hash, aktiveringskilde, attestant_a, pakrevd_antall)"
            " VALUES (%s,%s,%s,1,%s,'1.0.0','ih','d','historisk','uavh',"
            " NULL)", (POLTEN, pid, uid, opid))
        c.commit()
    finally:
        c.close()
    fra = datetime.now(timezone.utc) - timedelta(hours=1)
    til = datetime.now(timezone.utc) + timedelta(hours=1)
    rt = _rt()
    try:
        _sett_kontekst(rt, POLTEN)
        rader = rt.execute(
            "SELECT er_total, nokkel, antall FROM"
            " m16_aktiveringer(%s,%s,%s) WHERE partisjon='kvorumskrav'",
            (POLTEN, fra, til)).fetchall()
        rt.rollback()
    finally:
        rt.close()
    total, deler = _del(rader)
    assert deler.get("ukjent", 0) >= 1
    assert sum(deler.values()) == total


@pg
def test_tenantbinding_per_definer(migrator):
    """Port 2: SP-1 i hver definer — feil tenant i parameteret avvises,
    og riktig kontekst ser kun egne rader."""
    a, b = ("t-m16a-" + secrets.token_hex(3),
            "t-m16b-" + secrets.token_hex(3))
    _sett_kontekst(migrator, a)
    _logg(migrator, a)
    migrator.commit()
    _sett_kontekst(migrator, b)
    _logg(migrator, b)
    _logg(migrator, b)
    migrator.commit()
    fra = datetime.now(timezone.utc) - timedelta(hours=1)
    til = datetime.now(timezone.utc) + timedelta(hours=1)
    rt = _rt()
    try:
        total_a, _ = _kall(rt, a, "m16_beslutninger", fra, til)
        total_b, _ = _kall(rt, b, "m16_beslutninger", fra, til)
        assert (total_a, total_b) == (1, 2)
        for fn, arg in (
                ("m16_beslutninger(%s,%s,%s)", (b, fra, til)),
                ("m16_aktiveringer(%s,%s,%s)", (b, fra, til)),
                ("m16_oppdrag(%s,%s,%s)", (b, fra, til)),
                ("m16_unntak_aktivitet(%s,%s,%s,%s)",
                 (b, fra, til, ALLE_SAKSTYPER)),
                ("m16_unntak_apne(%s,%s,%s)",
                 (b, TERM, ALLE_SAKSTYPER)),
                ("m16_unntak_lukkede(%s,%s,%s,%s,%s,10)",
                 (b, fra, til, TERM, ALLE_SAKSTYPER)),
                ("m16_tick(%s,%s,%s)", (b, fra, til)),
                ("m16_tick_alltid(%s)", (b,)),
                ("m16_frekvens(%s,%s,%s)", (b, fra, til))):
            _sett_kontekst(rt, a)      # kontekst a, parameter b
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rt.execute(f"SELECT * FROM {fn}", arg)
            rt.rollback()
    finally:
        rt.close()


@pg
def test_grensehendelse_tilhorer_neste_vindu(migrator):
    """Port 5: en hendelse nøyaktig på `til` telles i neste vindu — og
    aldri i begge (halvåpent intervall)."""
    ten = "t-m16-" + secrets.token_hex(3)
    grense = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
    _sett_kontekst(migrator, ten)
    _logg(migrator, ten, ts=grense)
    migrator.commit()
    rt = _rt()
    try:
        for1, _ = _kall(rt, ten, "m16_beslutninger",
                        grense - timedelta(hours=1), grense)
        etter, _ = _kall(rt, ten, "m16_beslutninger",
                         grense, grense + timedelta(hours=1))
    finally:
        rt.close()
    assert (for1, etter) == (0, 1)


@pg
def test_aggregatmerket_kolliderer_ikke_med_en_ekte_kategori(migrator):
    """Merket for totalraden er en egenskap ved RADEN (`er_total`), ikke
    en reservert nøkkelverdi.

    `unntak.kategori` er en fri TEXT-kolonne uten skranke, så `__total__`
    — strengen merket besto av før — er en fullt lovlig kategori. Sto den
    i samme kolonne som kategoriene, var en ekte gruppe ikke til å skille
    fra aggregatet: API-laget leste begge som total, suminvarianten
    sprakk, og `/v1/nokkeltall` svarte `intern_feil` på data som var helt
    i orden. Med bare den ene kategorien i settet ble kortet i stedet
    tegnet som om det ikke hadde en eneste rad.

    Testen bruker nettopp den strengen, fordi det er den ETABLERTE
    kollisjonen; poenget er at INGEN kategoristreng har en særstilling.
    """
    ten = "t-m16-" + secrets.token_hex(3)
    naa = datetime.now(timezone.utc)
    _sett_kontekst(migrator, ten)
    _sak(migrator, ten, ts=naa, kategori="__total__")
    _sak(migrator, ten, ts=naa, kategori="__total__")
    _sak(migrator, ten, ts=naa, kategori="over_grense")
    migrator.commit()
    fra, til = naa - timedelta(hours=1), naa + timedelta(hours=1)
    rt = _rt()
    try:
        total, deler = _kall(rt, ten, "m16_unntak_aktivitet", fra, til,
                             ALLE_SAKSTYPER)
    finally:
        rt.close()
    assert total == 3
    assert deler == {"__total__": 2, "over_grense": 1}
    # Suminvarianten er hele poenget: API-laget skal kunne stole på den
    # uten å vite hvilke strenger kategorikolonnen inneholder.
    assert sum(deler.values()) == total
    from api.lesing import _partisjon
    assert _partisjon([(True, None, 3), (False, "__total__", 2),
                       (False, "over_grense", 1)]) == {
        "total": 3, "deler": {"__total__": 2, "over_grense": 1},
        "andeler": {"__total__": 0.6667, "over_grense": 0.3333}}


@pg
def test_tidsanker_per_kort(migrator):
    """Port 7/7b/7c/7d: hvert kort teller på SITT anker, tilstand blandes
    aldri med aktivitet, og tick følger `vindu_start`."""
    ten = "t-m16-" + secrets.token_hex(3)
    naa = datetime.now(timezone.utc)
    gammel = naa - timedelta(days=10)
    _sett_kontekst(migrator, ten)
    # 7b: sak opprettet FØR vinduet, fortsatt åpen.
    _sak(migrator, ten, ts=gammel, status="manuell", status_ts=gammel)
    # lukket I vinduet, opprettet før: aktivitet på ts-aksen teller den
    # ikke; lukket-listen (status_ts-aksen) gjør.
    _sak(migrator, ten, ts=gammel, status="løst", status_ts=naa)
    migrator.commit()
    fra, til = naa - timedelta(hours=1), naa + timedelta(hours=1)
    rt = _rt()
    try:
        akt_total, _ = _kall(rt, ten, "m16_unntak_aktivitet", fra, til,
                             ALLE_SAKSTYPER)
        assert akt_total == 0, "aktivitet talte en sak opprettet før vinduet"
        _sett_kontekst(rt, ten)
        lukkede = rt.execute(
            "SELECT id FROM m16_unntak_lukkede(%s,%s,%s,%s,%s,10)",
            (ten, fra, til, TERM, ALLE_SAKSTYPER)).fetchall()
        apne = rt.execute("SELECT m16_unntak_apne(%s,%s,%s)",
                          (ten, TERM, ALLE_SAKSTYPER)).fetchone()[0]
        rt.rollback()
        assert len(lukkede) == 1
        assert apne == 1                       # 7b: alltid «åpne nå»
        # 7c: «åpne nå» er upåvirket av ethvert vindu (ingen vindusarg).
        _sett_kontekst(rt, ten)
        apne2 = rt.execute("SELECT m16_unntak_apne(%s,%s,%s)",
                           (ten, TERM, ALLE_SAKSTYPER)).fetchone()[0]
        rt.rollback()
        assert apne2 == apne
    finally:
        rt.close()
    # 7d: tick med registrert og vindu_start i ULIKE vinduer → tilhører
    # vindu_start-vinduet. (Fixture for dagen data kommer: prod har 0.)
    from plan.klassifiser import _nokkel  # noqa: F401  (importbar sti)
    _sett_kontekst(migrator, ten)   # SET LOCAL — borte etter commit over
    pid = migrator.execute(
        "INSERT INTO bestillingsplan (tenant, bestillingstype, parametre,"
        " rytme, time_lokal, tidssone, opprettet_av, status) VALUES"
        " (%s,'kontroll.wcag.nettsted','{}','daglig',8,'Europe/Oslo',"
        "'test','aktiv') RETURNING plan_id", (ten,)).fetchone()[0]
    vs = gammel
    _sett_kontekst(migrator, ten)
    migrator.execute(
        "INSERT INTO bestillingsplan_vindu (plan_id, tenant, vindu_start,"
        " vindu_slutt, tilstand, terminalisert_ts) VALUES (%s,%s,%s,%s,"
        "'terminal', now())", (pid, ten, vs, vs + timedelta(hours=1)))
    migrator.execute(
        "INSERT INTO bestillingsplan_tick (plan_id, tenant, vindu_start,"
        " idempotensnokkel, utfall) VALUES (%s,%s,%s,%s,'tillat')",
        (pid, ten, vs, "n-" + secrets.token_hex(8)))
    migrator.commit()
    rt = _rt()
    try:
        naa_total, _ = _kall(rt, ten, "m16_tick", fra, til)
        gml_total, gml_deler = _kall(
            rt, ten, "m16_tick", vs - timedelta(hours=1),
            vs + timedelta(hours=2))
    finally:
        rt.close()
    assert naa_total == 0, "tick fulgte registrert, ikke vindu_start"
    assert gml_total == 1 and gml_deler == {"tillat": 1}


@pg
def test_api_endepunktet_er_generaliseringen(migrator, klient, token):
    """Port 11s API-halvdel: /v1/nokkeltall svarer med partisjoner der
    suminvarianten holder, «åpne nå» adskilt, og 24t/7d/30d som eneste
    vinduer."""
    tok, _ = token(scopes=("decisions:read", "exceptions:read"))
    r = klient.get("/v1/nokkeltall",
                   headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    d = r.json()
    for kort in ("beslutninger", "frekvens", "oppdrag",
                 "unntak_aktivitet", "tick"):
        assert sum(d[kort]["deler"].values()) == d[kort]["total"], kort
    for partisjon in d["aktiveringer"].values():
        assert sum(partisjon["deler"].values()) == partisjon["total"]
    assert isinstance(d["apne_naa"], int)
    # Fase 2: skalaren er borte — frekvens er partisjonen over. All-tid-
    # tellingen og lukketiden står som egne felt, alltid til stede.
    assert "frekvensreservasjoner" not in d
    assert isinstance(d["tick_alltid_totalt"], int)
    assert set(d["unntak_lukketid"]) == {"sum_s", "antall",
                                         "gjennomsnitt_s"}
    assert d["unntak_lukketid"]["antall"] == d["unntak_lukkede_totalt"]
    assert d["tidssone"] == "UTC"
    r2 = klient.get("/v1/nokkeltall?vindu=aldri",
                    headers={"authorization": f"Bearer {tok}"})
    assert r2.status_code == 400
    r3 = klient.get("/v1/nokkeltall?vindu=30d",
                    headers={"authorization": f"Bearer {tok}"})
    assert r3.status_code == 200
    # Fritt intervall finnes ikke i v1: et kall som ber om `fra`/`til`
    # avvises, det får ALDRI et urelatert 24-timerssvar med status 200.
    for spm in ("?fra=2026-08-01T00:00:00Z&til=2026-08-02T00:00:00Z",
                "?fra=2026-08-01T00:00:00Z",
                "?vindu=7d&til=2026-08-02T00:00:00Z"):
        rf = klient.get("/v1/nokkeltall" + spm,
                        headers={"authorization": f"Bearer {tok}"})
        assert rf.status_code == 400, spm


@pg
def test_vernede_sakstyper_teller_ikke_uten_security_read(
        migrator, klient, token):
    """Sikkerhets- og driftskøene er egne køer med eget scope, og vernet
    gjelder EKSISTENSEN: uten `security:read` skal nøkkeltallene verken
    telle dem, liste dem eller la dem påvirke «åpne nå».

    Nøkkeltallene leser `unntak` via egne definere, altså utenom
    unntakslistens sakstypeport — uten `p_sakstyper` var endepunktet en
    sidevei rundt `security:read` for ethvert `decisions:read`.
    """
    naa = datetime.now(timezone.utc)
    _sett_kontekst(migrator, TENANT)
    # Én åpen + én lukket av HVER sakstype, alle innenfor 24t-vinduet.
    for st in ("normal", "sikkerhet", "drift"):
        _sak(migrator, TENANT, sakstype=st, kategori="k_" + st)
        _sak(migrator, TENANT, sakstype=st, kategori="k_" + st,
             status="løst", status_ts=naa - timedelta(minutes=5))
    migrator.commit()

    def _hent(*scopes):
        tok, _ = token(scopes=scopes)
        r = klient.get("/v1/nokkeltall",
                       headers={"authorization": f"Bearer {tok}"})
        assert r.status_code == 200, r.text
        return r.json()

    smal = _hent("decisions:read")
    bred = _hent("decisions:read", "security:read")

    # Kategoriene fra de vernede køene finnes ikke engang som nøkkel.
    assert set(smal["unntak_aktivitet"]["deler"]) \
        & {"k_sikkerhet", "k_drift"} == set()
    assert {"k_sikkerhet", "k_drift"} <= set(bred["unntak_aktivitet"]["deler"])
    # Suminvarianten holder på BEGGE sider av porten — et filtrert svar
    # er et helt svar om et mindre sett, aldri en total med hull i.
    for d in (smal, bred):
        assert sum(d["unntak_aktivitet"]["deler"].values()) \
            == d["unntak_aktivitet"]["total"]
    assert bred["unntak_aktivitet"]["total"] \
        == smal["unntak_aktivitet"]["total"] + 4
    # Tilstandsaksen og radlisten er vernet på samme måte.
    assert bred["apne_naa"] == smal["apne_naa"] + 2
    assert {r["sakstype"] for r in smal["unntak_lukkede"]} <= {"normal"}
    assert {"sikkerhet", "drift"} \
        <= {r["sakstype"] for r in bred["unntak_lukkede"]}
    assert bred["unntak_lukkede_totalt"] \
        == smal["unntak_lukkede_totalt"] + 2


@pg
def test_lukkede_trunkeres_aldri_stille(migrator):
    """Radgrensen på lukkede-listen er et VISNINGSTAK, ikke en telling:
    `antall_totalt` er hele settet i vinduet, fra samme skann som radene,
    så et avkuttet utsnitt aldri kan leses som «alle lukkede saker»."""
    ten = "t-m16-" + secrets.token_hex(3)
    naa = datetime.now(timezone.utc)
    _sett_kontekst(migrator, ten)
    for i in range(5):
        _sak(migrator, ten, ts=naa - timedelta(hours=2),
             status="løst", status_ts=naa - timedelta(minutes=i + 1))
    migrator.commit()
    fra, til = naa - timedelta(hours=1), naa + timedelta(hours=1)
    rt = _rt()
    try:
        _sett_kontekst(rt, ten)
        rader = rt.execute(
            "SELECT id, antall_totalt FROM"
            " m16_unntak_lukkede(%s,%s,%s,%s,%s,2)",
            (ten, fra, til, TERM, ALLE_SAKSTYPER)).fetchall()
        rt.rollback()
    finally:
        rt.close()
    assert len(rader) == 2, "grensen kuttet ikke radlisten"
    # Grensen kuttet radene — men ALDRI tellingen.
    assert {r[1] for r in rader} == {5}


@pg
def test_andelene_kan_leses_tilbake_til_telling_og_total(migrator, klient,
                                                         token):
    """Fase 2, grensen §1: hver andel i svaret er EKSAKT round(del/total,
    4) av tall som står VED SIDEN AV i samme svar — for hver partisjon,
    generisk, aldri et kuratert utvalg. Nevner 0 gir null, aldri 0."""
    tok, _ = token(scopes=("decisions:read",))
    r = klient.get("/v1/nokkeltall",
                   headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    d = r.json()
    partisjoner = [d[k] for k in ("beslutninger", "frekvens", "oppdrag",
                                  "unntak_aktivitet", "tick")]
    partisjoner += list(d["aktiveringer"].values())
    for partisjon in partisjoner:
        assert set(partisjon["andeler"]) == set(partisjon["deler"])
        for nokkel, verdi in partisjon["andeler"].items():
            if partisjon["total"] == 0:
                assert verdi is None
            else:
                assert verdi == round(
                    partisjon["deler"][nokkel] / partisjon["total"], 4),                     (nokkel, partisjon)
    # Nevner 0 → null er kontrakten også når en del skulle finnes med
    # antall 0: en andel av ingenting er ikke definert, aldri tallet 0.
    from api.lesing import _partisjon
    assert _partisjon([(True, None, 0), (False, "x", 0)]) == {
        "total": 0, "deler": {"x": 0}, "andeler": {"x": None}}


@pg
def test_lukketidsnittet_er_vinduets_ikke_utsnittets(migrator, klient,
                                                     token):
    """Fase 2: `unntak_lukketid` er regnet over HELE vinduet — samme
    skann som radlisten, men upåvirket av visningstaket. Fixturen gir de
    50 nyeste sakene et HELT annet snitt enn vinduet: korte saker sist,
    fem enorme først. Var snittet regnet av radene som vises, ville det
    vært størrelsesordener mindre enn radene som ikke vises."""
    ten = "t-m16-" + secrets.token_hex(3)
    naa = datetime.now(timezone.utc)
    _sett_kontekst(migrator, ten)
    for i in range(50):        # de 50 nyeste: 60 s hver
        _sak(migrator, ten, ts=naa - timedelta(minutes=i + 1, seconds=60),
             status="løst", status_ts=naa - timedelta(minutes=i + 1))
    for i in range(5):         # fem eldre i vinduet: 1 000 000 s hver
        _sak(migrator, ten,
             ts=naa - timedelta(hours=20, minutes=i, seconds=1_000_000),
             status="løst", status_ts=naa - timedelta(hours=20, minutes=i))
    migrator.commit()
    tok, _ = token(tenant=ten, scopes=("decisions:read",))
    r = klient.get("/v1/nokkeltall",
                   headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["unntak_lukkede"]) == 50          # visningstaket
    assert d["unntak_lukkede_totalt"] == 55
    lukketid = d["unntak_lukketid"]
    assert lukketid == {"sum_s": 50 * 60 + 5 * 1_000_000, "antall": 55,
                        "gjennomsnitt_s": round((50 * 60 + 5 * 1_000_000)
                                                / 55)}
    # De 50 viste radene alene har snitt 60 — vinduets er ~91 000.
    # Verdien er beviselig ikke utsnittets.
    assert lukketid["gjennomsnitt_s"] > max(
        r_["varighet_s"] for r_ in d["unntak_lukkede"])


@pg
def test_tick_alltid_star_utenfor_ethvert_vindu(migrator):
    """Fase 2, tilstandsaksen for planer: `m16_tick_alltid` teller uten
    vindu (den HAR ingen vindusparametre) — en tick langt utenfor
    24t-vinduet telles der, mens vinduskortet viser 0."""
    ten = "t-m16-" + secrets.token_hex(3)
    naa = datetime.now(timezone.utc)
    gammel = naa - timedelta(days=10)
    _sett_kontekst(migrator, ten)
    pid = migrator.execute(
        "INSERT INTO bestillingsplan (tenant, bestillingstype, parametre,"
        " rytme, time_lokal, tidssone, opprettet_av, status) VALUES"
        " (%s,'kontroll.wcag.nettsted','{}','daglig',8,'Europe/Oslo',"
        "'test','aktiv') RETURNING plan_id", (ten,)).fetchone()[0]
    _sett_kontekst(migrator, ten)
    migrator.execute(
        "INSERT INTO bestillingsplan_vindu (plan_id, tenant, vindu_start,"
        " vindu_slutt, tilstand, terminalisert_ts) VALUES (%s,%s,%s,%s,"
        "'terminal', now())", (pid, ten, gammel,
                              gammel + timedelta(hours=1)))
    migrator.execute(
        "INSERT INTO bestillingsplan_tick (plan_id, tenant, vindu_start,"
        " idempotensnokkel, utfall) VALUES (%s,%s,%s,%s,'tillat')",
        (pid, ten, gammel, "n-" + secrets.token_hex(8)))
    migrator.commit()
    fra = naa - timedelta(hours=24)
    rt = _rt()
    try:
        vindu_total, _ = _kall(rt, ten, "m16_tick", fra, naa)
        _sett_kontekst(rt, ten)
        alltid = rt.execute("SELECT m16_tick_alltid(%s)",
                            (ten,)).fetchone()[0]
        rt.rollback()
    finally:
        rt.close()
    assert vindu_total == 0, "ticken ligger utenfor vinduet"
    assert alltid == 1


@pg
def test_frekvenspartisjonen_baerer_suminvarianten(migrator):
    """Fase 2: m16_frekvens er samme GROUPING SETS-form som de andre
    kortene — deler per handling og total fra SAMME skann, halvåpent
    vindu på `tidspunkt`. (Ukjent-porten fra port 1 gjelder formen;
    `handling` er NOT NULL i kilden, så NULL-veien kan ikke rigges her.)
    """
    ten = "t-m16-" + secrets.token_hex(3)
    naa = datetime.now(timezone.utc)
    _sett_kontekst(migrator, ten)
    for handling, n in (("utbetaling", 2), ("fakturering", 1)):
        for _ in range(n):
            migrator.execute(
                "INSERT INTO frekvens_hendelser (tenant, handling,"
                " nokkel_felt, gruppe, tidspunkt) VALUES"
                " (%s,%s,'belop','g1',%s)", (ten, handling, naa))
    # Utenfor vinduet: skal ikke telles.
    migrator.execute(
        "INSERT INTO frekvens_hendelser (tenant, handling, nokkel_felt,"
        " gruppe, tidspunkt) VALUES (%s,'utbetaling','belop','g1',%s)",
        (ten, naa - timedelta(days=2)))
    migrator.commit()
    fra, til = naa - timedelta(hours=1), naa + timedelta(hours=1)
    rt = _rt()
    try:
        total, deler = _kall(rt, ten, "m16_frekvens", fra, til)
    finally:
        rt.close()
    assert total == 3
    assert deler == {"utbetaling": 2, "fakturering": 1}
    assert sum(deler.values()) == total


# ---------------------------------------------------------------------------
# Statiske porter (3, 4, 6, 8, 10)
# ---------------------------------------------------------------------------

@pg
def test_ingen_divisjon_i_definerne():
    """Port 10: ingen andel, snitt eller median i DEFINERNE — tegnet
    `/` finnes ikke i 084 utenfor kommentarer; differanseformene er
    radvis varighet og summen av den (samme skann). Andeler og snitt
    regnes i API-laget, av teller og nevner som begge står i svaret."""
    kode = [l for l in M084.read_text(encoding="utf-8").splitlines()
            if not l.strip().startswith("--")]
    for l in kode:
        assert "/" not in l, f"divisjonstegn i definerfila: {l!r}"
    tekst = "\n".join(kode)
    for ord_ in ("avg(", "percentile", "stddev", "corr("):
        assert ord_ not in tekst.lower()


@pg
def test_definerne_er_lesing_uten_payload():
    """Port 4: ingen dekrypteringsvei og ingen payloadkolonner i
    nøkkeltallsveien — kun metadata."""
    sql = M084.read_text(encoding="utf-8").lower()
    for forbudt in ("payload_kryptert", "ciphertext", "dekrypter",
                    "key_id", "nonce", "handlingsintensjon"):
        assert forbudt not in sql, f"{forbudt} i definerfila"
    import inspect

    from api import lesing
    kilde = inspect.getsource(lesing.nokkeltall)
    for forbudt in ("dekrypter", "kryptering"):
        assert forbudt not in kilde


@pg
def test_flaten_leser_aldri_tabeller_og_tegner_aldri_kurver():
    """Port 3 + 8 + 13s statiske halvdel: flaten kaller KUN
    /v1/nokkeltall (+ lenke til beslutningslisten), har ingen SVG/canvas
    og ingen glatting/interpolasjon."""
    js = FLATE.read_text(encoding="utf-8")
    kode = "\n".join(l for l in js.splitlines()
                     if not l.strip().startswith("//"))
    api_kall = set(re.findall(r'hentJson\("([^"]+)"', kode))
    assert api_kall == {"/v1/nokkeltall"}, api_kall
    for forbudt in ("<svg", "canvas", "interpol", "trend", "prognose",
                    "moving", "smooth"):
        assert forbudt not in kode.lower(), forbudt
    assert "SELECT" not in kode


@pg
def test_eneste_divisjon_i_flaten_er_soylens_presentasjonsskala():
    """Fase 2-dommen: divisjonen bor i API-laget. Den eneste `/` i
    nokkeltall.js utenfor kommentarer og strengliteraler er
    presentasjonsskalaen i soyle() — bredden relativ til partisjonens
    største verdi, med tallet som tekst ved siden av. Alle avledede
    tall (andeler, snitt) kommer ferdige i svaret; flaten deler aldri
    to av svarets tall på hverandre. Selv varighetsnedbrytingen skrives
    uten `/` (gjentatt subtraksjon), så porten leses uten unntaksliste.
    """
    js = FLATE.read_text(encoding="utf-8")
    assert "/*" not in js, "blokk-kommentar gjør /-porten uleselig"
    uten_strenger = re.sub(r"`[^`]*`|\"[^\"]*\"|'[^']*'", '""', js)
    i_soyle = False
    for linje in uten_strenger.splitlines():
        kode = linje.split("//")[0]
        if linje.startswith("function soyle("):
            i_soyle = True
        if not i_soyle:
            assert "/" not in kode, \
                f"divisjonstegn i flaten utenfor soyle(): {linje!r}"
        if i_soyle and linje == "}":
            i_soyle = False


@pg
def test_delt_vindushjelp_ingen_egen_aritmetikk():
    """Port 6: ETT sted regner vinduer (NOKKELTALL_VINDUER +
    _nokkeltall_vindu); definerne mottar paret og bærer ingen egen
    tidsaritmetikk."""
    from api import lesing
    assert set(lesing.NOKKELTALL_VINDUER) == {"24t", "7d", "30d"}
    kode = [l for l in M084.read_text(encoding="utf-8").splitlines()
            if not l.strip().startswith("--")]
    tekst = "\n".join(kode).lower()
    for forbudt in ("now()", "interval", "date_trunc", "current_"):
        assert forbudt not in tekst, f"egen tidsaritmetikk: {forbudt}"
    import inspect
    kilde = inspect.getsource(lesing.nokkeltall)
    assert "timedelta" not in kilde, \
        "endepunktet regner vinduer selv i stedet for hjelpefunksjonen"


@pg
def test_terminalsettet_kommer_fra_app_laget():
    """Oversikt-lærdommen: statusmaskinen kopieres aldri inn i SQL —
    definerne tar terminalsettet som parameter fra app.py-konstanten."""
    sql = M084.read_text(encoding="utf-8")
    assert "løst" not in sql and "avvist" not in sql, \
        "terminalstatuser hardkodet i definerfila"
    from api.app import TERMINALE_UNNTAKSSTATUSER
    assert set(TERMINALE_UNNTAKSSTATUSER) == {"løst", "avvist"}


#: Definerne 084 lager — navn, ikke signatur: signaturen er nettopp det
#: fila ikke skal ha en andre utgave av. Fase 2: skalaren
#: m16_frekvensreservasjoner er ERSTATTET av m16_frekvens, og
#: m16_tick_alltid er ny — ni definere, verken flere eller færre.
M16_DEFINERE = ["m16_beslutninger", "m16_frekvens",
                "m16_aktiveringer", "m16_oppdrag", "m16_unntak_aktivitet",
                "m16_unntak_lukkede", "m16_unntak_apne", "m16_tick",
                "m16_tick_alltid"]


@pg
def test_rettighetene_folger_signaturene_som_faktisk_finnes(migrator):
    """SP-7 etter at fase 2 byttet ut og la til definere: hver definer
    finnes i NØYAKTIG én utgave, runtime har EXECUTE på den, og PUBLIC
    har den ikke. En rettighetssetning som gjentok argumentlista pekte
    på en overlast DROP-en alt hadde fjernet — PostgreSQL slår opp på
    eksakt argumentliste, så migrasjonen stoppet og rullet tilbake."""
    rader = migrator.execute(
        "SELECT p.proname, p.oid::regprocedure::text,"
        "       has_function_privilege('disponit', p.oid, 'EXECUTE'),"
        "       has_function_privilege('public', p.oid, 'EXECUTE')"
        "  FROM pg_catalog.pg_proc p"
        "  JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname = 'public' AND p.proname = ANY(%s)",
        (M16_DEFINERE,)).fetchall()
    migrator.rollback()
    navn = [r[0] for r in rader]
    assert sorted(navn) == sorted(M16_DEFINERE), \
        f"definer mangler eller finnes i flere utgaver: {sorted(navn)}"
    for proname, signatur, kjorer_har, alle_har in rader:
        assert kjorer_har, f"runtime mangler EXECUTE på {signatur}"
        assert not alle_har, f"PUBLIC har fortsatt EXECUTE på {signatur}"


@pg
def test_ingen_setning_gjentar_argumentlistene():
    """Roten under funnet over: signaturen skal stå ETT sted i 084 —
    i CREATE. En REVOKE/GRANT/DROP med egen argumentliste er en andre
    utgave som driver fra definisjonen i stillhet, og det er nettopp
    slik drift som brøt migrasjonen.

    DROP var unntaket til denne regelen, og det holdt akkurat til
    returtypene endret seg: da måtte ENDA en håndskrevet signaturliste
    holdes i takt med CREATE-ene. Både ryddingen og rettighetene spør
    nå katalogen, så unntaket finnes ikke lenger."""
    kode = [l for l in M084.read_text(encoding="utf-8").splitlines()
            if not l.strip().startswith("--")]
    for linje in kode:
        if re.search(r"\b(REVOKE|GRANT|DROP)\b.*\bFUNCTION\b", linje):
            assert "||" in linje, \
                f"setning med håndskrevet signatur: {linje!r}"


# ---------------------------------------------------------------------------
# PR-B (086): indeksene for de to uindekserte tidsankrene
# ---------------------------------------------------------------------------

@pg
def test_086_indeksene_finnes_og_definerne_star(migrator):
    """PR-B: hvert av de to skannene har sin (tenant, tidsanker)-indeks
    — og 086 rører INGEN definer: fila lager indekser og ikke noe annet,
    og katalogsettet av m16-definere er fortsatt nøyaktig 084s ni
    (M16_DEFINERE). Bytene i 084 vokter fasitporten allerede."""
    idx = dict(migrator.execute(
        "SELECT indexname, indexdef FROM pg_indexes"
        " WHERE schemaname = 'public'"
        "   AND indexname IN ('oppdrag_status_ts', 'tick_vindu')"
    ).fetchall())
    migrator.rollback()
    assert set(idx) == {"oppdrag_status_ts", "tick_vindu"}, idx
    assert "(tenant, status_ts)" in idx["oppdrag_status_ts"]
    assert "(tenant, vindu_start) INCLUDE (utfall)" in idx["tick_vindu"]

    kode = "\n".join(l for l in M086.read_text(encoding="utf-8").splitlines()
                     if not l.strip().startswith("--"))
    assert re.search(r"\bFUNCTION\b", kode, re.IGNORECASE) is None, \
        "086 skal aldri røre en definer"
    for setning in kode.split(";"):
        if setning.strip():
            assert setning.strip().startswith("CREATE INDEX"), setning

    navn = [r[0] for r in migrator.execute(
        "SELECT p.proname FROM pg_catalog.pg_proc p"
        " JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname = 'public' AND p.proname LIKE 'm16\\_%'"
    ).fetchall()]
    migrator.rollback()
    assert sorted(navn) == sorted(M16_DEFINERE), navn
