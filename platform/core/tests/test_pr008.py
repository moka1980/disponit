"""PR-008: lese-API-et + beslutning→oppdrag-koblingen.

Testplanen er klarsignalets ni Codex-porter + de fem vilkårene + de
bindende testene fra spesifikasjonens v2–v6. Migrasjonstestene bygger sin
egen utgangstilstand fra bunnen (007-skjema med håndsådde legacy-rader) og
KJØRER migrasjon 008 på den — de antar aldri en tilstand suiten tilfeldigvis
etterlot (jf. lærdommen fra PR-007: en test som antar en tilstand blir
stille verdiløs).
"""
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from .test_api import (ANNEN_TENANT, DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       _lag_token, _rydd, app, dekker, klient,   # noqa: F401
                       malpolicy, migrator, miljo, policy, token)  # noqa: F401
from .test_m37 import (_lag_oppdrag, _lag_sak, _policyref,       # noqa: F401
                       _sett_kontekst, FIXTURE_POLICY_ID)
from .test_kjorer_og_kryptering import _nullstill                # noqa: F401

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


# ---------------------------------------------------------------------------
# Hjelpere
# ---------------------------------------------------------------------------

def _lesetoken(migrator, tenant=TENANT,
               scopes=("decisions:read", "exceptions:read", "policy:read")):
    return _lag_token(migrator, tenant, "bruker", list(scopes))


def _hent(klient, sti, tok, **params):
    return klient.get(sti, params=params,
                      headers={"authorization": f"Bearer {tok}"})


def _beslutningslogg(conn, tenant, *, beslutning="TILLAT", kilde="test",
                     idem=None, handling="purring.send",
                     begrunnelse='[{"kode":"innenfor_grense"}]', ts=None):
    _sett_kontekst(conn, tenant)
    kolonner = "tenant, aktor, kilde, input_hash, policy_id, beslutning," \
               " begrunnelse, policy_content_hash, handling, idempotency_key"
    verdier = [tenant, "test", kilde, "ih", _policyref(), beslutning,
               begrunnelse, "c" * 64, handling, idem]
    if ts is not None:
        kolonner += ", ts"
        verdier.append(ts)
    plasser = ",".join(["%s"] * len(verdier))
    rad = conn.execute(
        f"INSERT INTO revisjonslogg ({kolonner}) VALUES ({plasser})"
        " RETURNING id", tuple(verdier)).fetchone()
    conn.commit()
    return int(rad[0])


# ===========================================================================
# Migrasjon 008 — Codex-port 1, 2, 4, 5 + v5/v6-bindende tester
# ===========================================================================

def _gjenopprett_rettigheter(migrator):
    """Rettighetene deploy-skriptet setter ETTER migrasjonene.

    `_nullstill` + `kjorer.migrer` bygger skjemaet, men GRANT-ene bor i
    deploy/staging/migrer.py — uten dette steget står runtimerollen uten
    SELECT og resten av suiten feiler på noe migrasjonstestene rev ned.
    """
    from .test_kjorer_og_kryptering import _migrer_modul
    modul = _migrer_modul()
    # VARSLER-blokken hører med: migrasjonstestene river skjemaet og bygger
    # det på nytt, og en gjenoppbygging som replayer alle grantsettene UNNTATT
    # ett etterlater senderrollen uten EXECUTE — for resten av suiten. Det var
    # nøyaktig slik varselsendertestene røk i full suite men besto alene.
    for sql, rolle in ((modul.RETTIGHETER, "disponit"),
                       (modul.M37_RETTIGHETER, "disponit"),
                       (modul.VARSLER_RETTIGHETER, "disponit_varselsender"),
                       (modul.TOKEN_ADMIN_RETTIGHETER,
                        "disponit_token_admin")):
        migrator.execute(sql.format(rolle=rolle))
        # Commit PER blokk: M37-blokken setter `SET LOCAL ROLE`, og uten
        # commit her ville neste blokk kjørt som feil rolle.
        migrator.commit()


def _kjor_til_007_og_saa_alt(migrator, seed):
    """Bygger 007-tilstand, sår legacy-rader med `seed(conn)`, kjører 008."""
    from db import kjorer
    _nullstill(migrator, med_legacy_uten_checksum=False)
    kjorer.migrer(migrator, til_og_med=7)
    seed(migrator)
    kjort = kjorer.migrer(migrator)
    _gjenopprett_rettigheter(migrator)
    return kjort


def _saa_legacy_oppdrag(conn, tenant, rid, *, oppdragstype="reinnsending",
                        beslutningslogg_antall=1, kilde="arbeidskapabilitet",
                        beslutning="TILLAT"):
    """En 007-æra oppdragsrad + `beslutningslogg_antall` kandidatloggposter."""
    _sett_kontekst(conn, tenant)
    conn.execute("INSERT INTO tenant_nokler (tenant, key_id, wrapped_dek)"
                 " VALUES (%s,'k1','\\x00'::bytea)"
                 " ON CONFLICT DO NOTHING", (tenant,))
    sakslogg = conn.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse) VALUES"
        " (%s,'test','test','ih',%s,'UNNTAK','[]') RETURNING id",
        (tenant, _policyref())).fetchone()[0]
    sak = conn.execute(
        "INSERT INTO unntak (tenant, loggpost_id, handling, kategori,"
        " payload_kryptert, key_id, nonce, maks_auto_forsok_snapshot,"
        " policy_versjon, policy_content_hash)"
        " VALUES (%s,%s,'purring.send','manglende_data',"
        " '\\x00'::bytea,'k1','\\x00'::bytea,3,'1.0.0',%s) RETURNING id",
        (tenant, sakslogg, "c" * 64)).fetchone()[0]
    conn.execute(
        "INSERT INTO reparasjonsoperasjoner (tenant, unntak_id,"
        " repair_operation_id, repair_generation, handler_id,"
        " handler_versjon, maalhandling, input_hash, kategori) VALUES"
        " (%s,%s,%s,0,'r1','1','purring.send',%s,'manglende_data')",
        (tenant, sak, rid, secrets.token_hex(32)))
    kandidater = []
    for _ in range(beslutningslogg_antall):
        kandidater.append(conn.execute(
            "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
            " policy_id, beslutning, begrunnelse, idempotency_key) VALUES"
            " (%s,'test',%s,'ih',%s,%s,'[]',%s) RETURNING id",
            (tenant, kilde, _policyref(), beslutning, rid)).fetchone()[0])
    opp = conn.execute(
        "INSERT INTO oppdrag (tenant, unntak_id, loggpost_id,"
        " repair_operation_id, oppdragstype, handling, eiermodul,"
        " payload_kryptert, key_id, nonce, utforelsesfrist, evidensfrist)"
        " VALUES (%s,%s,%s,%s,%s,'purring.send','eiermodul:reinnsending',"
        " '\\x00'::bytea,'k1','\\x00'::bytea,"
        " now()+interval '1 hour', now()+interval '30 days') RETURNING id",
        (tenant, sak, sakslogg, rid, oppdragstype)).fetchone()[0]
    conn.commit()
    return int(opp), kandidater


@pg
def test_port1_migrasjonen_backfyller_entydig_tvetydig_ukjent_og_verifikasjon(
        migrator):
    """Codex-port 1: alle fire legacy-klassene i SAMME kjøring.

    Entydig match -> KOBLET med riktig FK. To kandidater -> LEGACY_UKJENT
    (aldri første/siste rad). Ingen kandidat -> LEGACY_UKJENT — selv om
    sakens egen `unntak.loggpost_id`-kjede finnes (v4 pkt. 1: kjeden er
    ALDRI tilstrekkelig). Feil logghendelsestype (kilde/beslutning) teller
    ikke som kandidat. Verifikasjonsoppdrag -> VERIFIKASJON, deterministisk
    av typen.
    """
    tilstand = {}

    def seed(conn):
        tilstand["entydig"] = _saa_legacy_oppdrag(
            conn, TENANT, "a" * 64, beslutningslogg_antall=1)
        tilstand["tvetydig"] = _saa_legacy_oppdrag(
            conn, TENANT, "b" * 64, beslutningslogg_antall=2)
        tilstand["ukjent"] = _saa_legacy_oppdrag(
            conn, TENANT, "d" * 64, beslutningslogg_antall=0)
        # Kandidater med FEIL hendelsestype: riktig nøkkel, feil kilde —
        # og riktig kilde, feil beslutning. Ingen av dem skal koble.
        tilstand["feil_kilde"] = _saa_legacy_oppdrag(
            conn, TENANT, "e" * 64, beslutningslogg_antall=1, kilde="test")
        tilstand["feil_beslutning"] = _saa_legacy_oppdrag(
            conn, TENANT, "f" * 64, beslutningslogg_antall=1,
            beslutning="STOPP")
        tilstand["verifikasjon"] = _saa_legacy_oppdrag(
            conn, TENANT, "9" * 64, beslutningslogg_antall=0,
            oppdragstype="verifikasjon")

    kjort = _kjor_til_007_og_saa_alt(migrator, seed)
    assert 8 in kjort

    def status(navn):
        opp_id = tilstand[navn][0]
        return migrator.execute(
            "SELECT koblingsstatus, beslutning_loggpost_id FROM oppdrag"
            " WHERE tenant=%s AND id=%s", (TENANT, opp_id)).fetchone()

    _sett_kontekst(migrator, TENANT)
    assert status("entydig") == ("KOBLET", tilstand["entydig"][1][0])
    assert status("tvetydig") == ("LEGACY_UKJENT", None), \
        "to kandidater skal ALDRI kobles automatisk (vilkår V2)"
    assert status("ukjent") == ("LEGACY_UKJENT", None)
    assert status("feil_kilde") == ("LEGACY_UKJENT", None)
    assert status("feil_beslutning") == ("LEGACY_UKJENT", None)
    assert status("verifikasjon") == ("VERIFIKASJON", None)
    migrator.rollback()


@pg
def test_port2_feil_midt_i_migrasjonen_ruller_alt_tilbake(migrator):
    """Codex-port 2: feiler backfillen, finnes verken kolonner, constraints
    eller triggere etterpå — og 008 er ikke registrert som kjørt.

    Feilen induseres ved å legge kolonnen inn på forhånd med FEIL TYPE:
    `ADD COLUMN IF NOT EXISTS` hopper over den, og backfillens tilordning
    bigint->text feiler hardt midt i steg 2 — etter at koblingsstatus-
    kolonnen alt er lagt til i samme transaksjon.
    """
    from db import kjorer

    def seed(conn):
        _saa_legacy_oppdrag(conn, TENANT, "a" * 64, beslutningslogg_antall=1)
        conn.execute("ALTER TABLE oppdrag ADD COLUMN beslutning_loggpost_id TEXT")
        conn.commit()

    _nullstill(migrator, med_legacy_uten_checksum=False)
    kjorer.migrer(migrator, til_og_med=7)
    seed(migrator)
    with pytest.raises(Exception):
        kjorer.migrer(migrator)
    migrator.rollback()

    kolonner = {r[0] for r in migrator.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_name='oppdrag'").fetchall()}
    assert "koblingsstatus" not in kolonner, \
        "delvis migrasjon: koblingsstatus overlevde en feilet kjøring"
    vakter = migrator.execute(
        "SELECT COUNT(*) FROM pg_trigger WHERE tgname='oppdrag_koblingslaas'"
    ).fetchone()[0]
    assert vakter == 0
    constraints = migrator.execute(
        "SELECT COUNT(*) FROM pg_constraint"
        " WHERE conname IN ('oppdrag_beslutning_fk','oppdrag_kobling_konsistent')"
    ).fetchone()[0]
    assert constraints == 0
    registrert = migrator.execute(
        "SELECT COUNT(*) FROM migrasjoner WHERE versjon=8").fetchone()[0]
    assert registrert == 0
    migrator.rollback()
    # Rydd opp den induserte feilkolonnen og fullfør migrasjonen, slik at
    # databasen står komplett for testene etter denne.
    migrator.execute("ALTER TABLE oppdrag DROP COLUMN beslutning_loggpost_id")
    migrator.commit()
    kjort = kjorer.migrer(migrator)
    assert 8 in kjort
    _gjenopprett_rettigheter(migrator)


@pg
def test_port4_runtime_kan_verken_skape_legacy_eller_endre_koblingen(
        migrator, policy):
    """Codex-port 4 + v5/v6: LEGACY_UKJENT er utilgjengelig fra runtime
    (det finnes ingen migrasjonsmodus å forfalske — vakten er rekkefølgen),
    og FK+status er uforanderlige etter innsetting."""
    sak, loggpost = _lag_sak(migrator, TENANT)
    opp, rid = _lag_oppdrag(migrator, TENANT, sak, loggpost)
    _sett_kontekst(migrator, TENANT)

    with pytest.raises(Exception, match="LEGACY_UKJENT"):
        migrator.execute(
            "INSERT INTO oppdrag (tenant, unntak_id, loggpost_id,"
            " repair_operation_id, oppdragstype, handling, eiermodul,"
            " payload_kryptert, key_id, nonce, utforelsesfrist, evidensfrist,"
            " beslutning_loggpost_id, koblingsstatus)"
            " SELECT tenant, unntak_id, loggpost_id, %s, oppdragstype,"
            " handling, eiermodul, payload_kryptert, key_id, nonce,"
            " utforelsesfrist, evidensfrist, NULL, 'LEGACY_UKJENT'"
            "  FROM oppdrag WHERE tenant=%s AND id=%s",
            (secrets.token_hex(32), TENANT, opp))
    migrator.rollback()

    _sett_kontekst(migrator, TENANT)
    with pytest.raises(Exception, match="uforanderlig"):
        migrator.execute(
            "UPDATE oppdrag SET beslutning_loggpost_id=%s"
            " WHERE tenant=%s AND id=%s", (loggpost, TENANT, opp))
    migrator.rollback()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(Exception, match="uforanderlig"):
        migrator.execute(
            "UPDATE oppdrag SET koblingsstatus='VERIFIKASJON'"
            " WHERE tenant=%s AND id=%s", (TENANT, opp))
    migrator.rollback()


@pg
def test_ny_rad_uten_beslutningsfk_avvises_i_databasen(migrator, policy):
    """v4 bindende test: `KOBLET` uten FK er en CHECK-feil — defaulten kan
    ikke møte en manglende FK i stillhet (vilkår V1s DB-side)."""
    sak, loggpost = _lag_sak(migrator, TENANT)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(Exception, match="oppdrag_kobling_konsistent"):
        migrator.execute(
            "INSERT INTO oppdrag (tenant, unntak_id, loggpost_id,"
            " repair_operation_id, oppdragstype, handling, eiermodul,"
            " payload_kryptert, key_id, nonce, utforelsesfrist,"
            " evidensfrist)"
            " SELECT %s, %s, %s, %s, 'reinnsending', 'purring.send',"
            " 'eiermodul:reinnsending', payload_kryptert, key_id, nonce,"
            " now()+interval '1 hour', now()+interval '30 days'"
            "  FROM unntak WHERE tenant=%s AND id=%s",
            (TENANT, sak, loggpost, secrets.token_hex(32), TENANT, sak))
    migrator.rollback()


@pg
def test_koblingsstatus_er_toveis_bundet_til_oppdragstypen(migrator, policy):
    """AVVIK-vedlegget: VERIFIKASJON kun for verifikasjonsoppdrag, KOBLET
    aldri for dem — begge retninger håndhevet av CHECK-en."""
    sak, loggpost = _lag_sak(migrator, TENANT)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(Exception, match="oppdrag_kobling_konsistent"):
        migrator.execute(
            "INSERT INTO oppdrag (tenant, unntak_id, loggpost_id,"
            " repair_operation_id, oppdragstype, handling, eiermodul,"
            " payload_kryptert, key_id, nonce, utforelsesfrist, evidensfrist,"
            " koblingsstatus)"
            " SELECT %s, %s, %s, %s, 'reinnsending', 'purring.send',"
            " 'eiermodul:reinnsending', payload_kryptert, key_id, nonce,"
            " now()+interval '1 hour', now()+interval '30 days',"
            " 'VERIFIKASJON' FROM unntak WHERE tenant=%s AND id=%s",
            (TENANT, sak, loggpost, secrets.token_hex(32), TENANT, sak))
    migrator.rollback()
    # KOBLET verifikasjonsoppdrag: den semantiske porten passeres med en
    # EKTE fase-2-loggpost, slik at det er CHECK-ens typebinding — ikke
    # semantikkvakten — som beviselig avviser raden.
    sak2, loggpost2 = _lag_sak(migrator, TENANT)
    rid2 = secrets.token_hex(32)
    fase2 = _beslutningslogg(migrator, TENANT, kilde="arbeidskapabilitet",
                             idem=rid2)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(Exception, match="oppdrag_kobling_konsistent"):
        migrator.execute(
            "INSERT INTO oppdrag (tenant, unntak_id, loggpost_id,"
            " repair_operation_id, oppdragstype, handling, eiermodul,"
            " payload_kryptert, key_id, nonce, utforelsesfrist, evidensfrist,"
            " beslutning_loggpost_id, koblingsstatus)"
            " SELECT %s, %s, %s, %s, 'verifikasjon', 'verifiser.x',"
            " 'eiermodul:verifisering', payload_kryptert, key_id, nonce,"
            " now()+interval '1 hour', now()+interval '30 days',"
            " %s, 'KOBLET' FROM unntak WHERE tenant=%s AND id=%s",
            (TENANT, sak2, loggpost2, rid2, fase2, TENANT, sak2))
    migrator.rollback()


@pg
def test_port5_to_oppdrag_for_samme_beslutning_avvises(migrator, policy):
    """Codex-port 5: UNIQUE (tenant, beslutning_loggpost_id) — partiell,
    så LEGACY/VERIFIKASJON (NULL) deltar ikke.

    Etter den semantiske porten (review-runde 1) er indeksen ANDRE
    forsvarslinje: en semantisk gyldig duplikatkobling må bære samme
    `repair_operation_id` som loggposten, og da fyrer `oppdrag_repair_unik`
    først. Indeksens eget lag måles derfor med koblingsvakten midlertidig
    av — det beviser at duplikatvernet står SELV OM triggeren skulle
    falle, i stedet for å anta det.
    """
    sak, loggpost = _lag_sak(migrator, TENANT)
    opp, rid = _lag_oppdrag(migrator, TENANT, sak, loggpost)
    _sett_kontekst(migrator, TENANT)
    fk = migrator.execute(
        "SELECT beslutning_loggpost_id FROM oppdrag WHERE tenant=%s AND id=%s",
        (TENANT, opp)).fetchone()[0]
    migrator.execute("ALTER TABLE oppdrag DISABLE TRIGGER oppdrag_koblingslaas")
    try:
        _sett_kontekst(migrator, TENANT)
        with pytest.raises(Exception, match="oppdrag_en_per_beslutning"):
            migrator.execute(
                "INSERT INTO oppdrag (tenant, unntak_id, loggpost_id,"
                " repair_operation_id, oppdragstype, handling, eiermodul,"
                " payload_kryptert, key_id, nonce, utforelsesfrist,"
                " evidensfrist, beslutning_loggpost_id, koblingsstatus)"
                " SELECT tenant, unntak_id, loggpost_id, %s, oppdragstype,"
                " handling, eiermodul, payload_kryptert, key_id, nonce,"
                " utforelsesfrist, evidensfrist, %s, 'KOBLET'"
                "  FROM oppdrag WHERE tenant=%s AND id=%s",
                (secrets.token_hex(32), fk, TENANT, opp))
    finally:
        migrator.rollback()
        migrator.execute(
            "ALTER TABLE oppdrag ENABLE TRIGGER oppdrag_koblingslaas")
        migrator.commit()


@pg
def test_P1_koblingen_er_semantisk_ikke_bare_en_fk(migrator, policy):
    """Codex P1 review-runde 1: FK-en beviser at loggposten FINNES, ikke at
    den er riktig beslutning. Databaseporten må kreve at den refererte
    raden er nøyaktig fase-2-TILLAT-beslutningen for oppdragets
    `repair_operation_id` — hvert av de tre predikatene måles negativt,
    og den positive kontrollen beviser at porten slipper riktig rad
    gjennom (en vakt ingen gyldig rad passerer er en annen feil)."""

    def _forsok(rid, fk):
        sak, sakslogg = _lag_sak(migrator, TENANT)
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "INSERT INTO reparasjonsoperasjoner (tenant, unntak_id,"
            " repair_operation_id, repair_generation, handler_id,"
            " handler_versjon, maalhandling, input_hash, kategori) VALUES"
            " (%s,%s,%s,0,'r1','1','purring.send',%s,'manglende_data')",
            (TENANT, sak, rid, secrets.token_hex(32)))
        return migrator.execute(
            "INSERT INTO oppdrag (tenant, unntak_id, loggpost_id,"
            " repair_operation_id, oppdragstype, handling, eiermodul,"
            " payload_kryptert, key_id, nonce, utforelsesfrist, evidensfrist,"
            " beslutning_loggpost_id, koblingsstatus)"
            " SELECT %s, %s, %s, %s, 'reinnsending', 'purring.send',"
            " 'eiermodul:reinnsending', payload_kryptert, key_id, nonce,"
            " now()+interval '1 hour', now()+interval '30 days', %s, 'KOBLET'"
            "  FROM unntak WHERE tenant=%s AND id=%s RETURNING id",
            (TENANT, sak, sakslogg, rid, fk, TENANT, sak)).fetchone()

    # Negativ 1 — FEIL idempotency_key: gyldig fase-2-loggpost, men den
    # tilhører en ANNEN reparasjonsidentitet.
    rid = secrets.token_hex(32)
    fremmed = _beslutningslogg(migrator, TENANT, kilde="arbeidskapabilitet",
                               idem=secrets.token_hex(32))
    with pytest.raises(Exception, match="semantisk"):
        _forsok(rid, fremmed)
    migrator.rollback()

    # Negativ 2 — FEIL kilde: riktig nøkkel og TILLAT, men en ordinær
    # API-beslutning, ikke en fase-2-beslutning.
    rid = secrets.token_hex(32)
    feil_kilde = _beslutningslogg(migrator, TENANT, kilde="test", idem=rid)
    with pytest.raises(Exception, match="semantisk"):
        _forsok(rid, feil_kilde)
    migrator.rollback()

    # Negativ 3 — FEIL beslutning: fase-2-loggpost med riktig nøkkel som
    # ble STOPP — en avvist reparasjon skaper aldri et oppdrag.
    rid = secrets.token_hex(32)
    stopp = _beslutningslogg(migrator, TENANT, kilde="arbeidskapabilitet",
                             idem=rid, beslutning="STOPP")
    with pytest.raises(Exception, match="semantisk"):
        _forsok(rid, stopp)
    migrator.rollback()

    # Positiv kontroll — nøyaktig riktig rad: aksepteres.
    rid = secrets.token_hex(32)
    riktig = _beslutningslogg(migrator, TENANT, kilde="arbeidskapabilitet",
                              idem=rid)
    assert _forsok(rid, riktig) is not None
    migrator.rollback()

    # Og RLS-flanken: samme innsetting UTEN tenantkontekst ser ingen
    # loggpost og avvises — porten er fail-closed, ikke omgåelig ved å
    # utelate konteksten.
    rid = secrets.token_hex(32)
    riktig2 = _beslutningslogg(migrator, TENANT, kilde="arbeidskapabilitet",
                               idem=rid)
    sak, sakslogg = _lag_sak(migrator, TENANT)
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "INSERT INTO reparasjonsoperasjoner (tenant, unntak_id,"
        " repair_operation_id, repair_generation, handler_id,"
        " handler_versjon, maalhandling, input_hash, kategori) VALUES"
        " (%s,%s,%s,0,'r1','1','purring.send',%s,'manglende_data')",
        (TENANT, sak, rid, secrets.token_hex(32)))
    ct_rad = migrator.execute(
        "SELECT payload_kryptert, key_id, nonce FROM unntak"
        " WHERE tenant=%s AND id=%s", (TENANT, sak)).fetchone()
    migrator.commit()   # kontekst borte ved commit — neste INSERT er naken
    with pytest.raises(Exception):
        migrator.execute(
            "INSERT INTO oppdrag (tenant, unntak_id, loggpost_id,"
            " repair_operation_id, oppdragstype, handling, eiermodul,"
            " payload_kryptert, key_id, nonce, utforelsesfrist, evidensfrist,"
            " beslutning_loggpost_id, koblingsstatus)"
            " VALUES (%s,%s,%s,%s,'reinnsending','purring.send',"
            " 'eiermodul:reinnsending',%s,%s,%s, now()+interval '1 hour',"
            " now()+interval '30 days', %s, 'KOBLET')",
            (TENANT, sak, sakslogg, rid, ct_rad[0], ct_rad[1], ct_rad[2],
             riktig2))
    migrator.rollback()


# ===========================================================================
# Codex-port 3 — alle oppdragsopprettende kodeveier leverer beslutnings-FK
# ===========================================================================

def test_port3_eneste_insertsted_er_opprett_oppdrag():
    """Statisk: `INSERT INTO oppdrag` finnes i NØYAKTIG én produksjonsfil,
    `m37/arbeider.py` — kodeveiene som skal levere FK-en er dermed telbare,
    og en ny skrivevei kan ikke oppstå uten at denne testen ser den."""
    from .conftest import CORE
    treff = []
    for fil in CORE.rglob("*.py"):
        if "tests" in fil.parts or "__pycache__" in fil.parts:
            continue
        if "INSERT INTO oppdrag" in fil.read_text(encoding="utf-8"):
            treff.append(fil.name)
    assert treff == ["arbeider.py"], f"uventede skriveveier: {treff}"


def test_port3_forretningsoppdrag_uten_fk_er_programmeringsfeil():
    """`_opprett_oppdrag` nekter et forretningsoppdrag uten beslutnings-FK
    FØR databasen i det hele tatt ser raden (vilkår V1)."""
    from m37 import arbeider, reparasjoner

    class _Sak:
        tenant, id, loggpost_id = "t", 1, 2
    plan = reparasjoner.Reparasjonsplan(
        "oppdrag", "x", maalhandling="purring.send",
        oppdragstype="reinnsending", reparasjonsinput={"handling": "purring.send"})
    with pytest.raises(RuntimeError, match="V1"):
        arbeider._opprett_oppdrag(None, _Sak(), plan, "r" * 64, 2)


@pg
def test_port3_beslutningsloggpost_krever_noyaktig_en_kandidat(migrator,
                                                               policy):
    """Arbeiderens oppslag: null kandidater -> hard feil; to -> hard feil;
    én -> nøyaktig den. Samme regel som backfillen, målt på samme nøkkel."""
    from m37 import arbeider

    class _Sak:
        tenant, loggpost_id = TENANT, None
        id = 0

    rid = secrets.token_hex(32)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(RuntimeError, match="fant 0"):
        arbeider._beslutningsloggpost(migrator, _Sak(), rid)
    en = _beslutningslogg(migrator, TENANT, kilde="arbeidskapabilitet",
                          idem=rid)
    _sett_kontekst(migrator, TENANT)
    assert arbeider._beslutningsloggpost(migrator, _Sak(), rid) == en
    _beslutningslogg(migrator, TENANT, kilde="arbeidskapabilitet", idem=rid)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(RuntimeError, match="fant 2"):
        arbeider._beslutningsloggpost(migrator, _Sak(), rid)
    migrator.rollback()


# ===========================================================================
# Endepunktene — Codex-port 8 (tenant, scope, cursor, feil) + detaljaksene
# ===========================================================================

@pg
def test_oversikt_invariant_og_vindu(klient, migrator, policy):
    tok, _ = _lesetoken(migrator)
    _beslutningslogg(migrator, TENANT, beslutning="TILLAT")
    _beslutningslogg(migrator, TENANT, beslutning="STOPP")
    _lag_sak(migrator, TENANT)          # gir en UNNTAK-loggpost
    # Utenfor vinduet: skal ikke telles.
    _beslutningslogg(migrator, TENANT, beslutning="TILLAT",
                     ts=datetime.now(timezone.utc) - timedelta(hours=25))
    # Annen tenant: aldri synlig.
    _beslutningslogg(migrator, ANNEN_TENANT, beslutning="TILLAT")

    r = _hent(klient, "/v1/oversikt", tok)
    assert r.status_code == 200
    k = r.json()
    assert k["tillatt"] + k["stoppet"] + k["unntak"] == k["totalt"]
    assert (k["tillatt"], k["stoppet"], k["unntak"]) == (1, 1, 1)
    assert k["tidssone"] == "UTC"


@pg
def test_beslutningsliste_keyset_filter_og_cursorbinding(klient, migrator,
                                                         policy):
    tok, _ = _lesetoken(migrator)
    ider = [_beslutningslogg(migrator, TENANT, beslutning=b)
            for b in ("TILLAT", "STOPP", "TILLAT", "UNNTAK", "TILLAT")]

    r = _hent(klient, "/v1/beslutninger", tok, limit=2)
    assert r.status_code == 200
    side1 = r.json()
    assert [rad["id"] for rad in side1["rader"]] == [ider[4], ider[3]]
    assert side1["neste_cursor"]

    # En rad satt inn MIDT i pagineringen: ærlig keyset — ingen duplikater,
    # ingen krasj; raden er nyere enn cursoren og dukker derfor ikke opp.
    ny = _beslutningslogg(migrator, TENANT, beslutning="STOPP")
    r2 = _hent(klient, "/v1/beslutninger", tok, limit=2,
               cursor=side1["neste_cursor"])
    side2 = r2.json()
    assert [rad["id"] for rad in side2["rader"]] == [ider[2], ider[1]]
    sett = {rad["id"] for rad in side1["rader"] + side2["rader"]}
    assert len(sett) == 4 and ny not in sett

    # Backdated rad (eldre ts, høyere id) under paginering: kan bli synlig
    # på en senere side — det ÆRLIGE keysetet lover bare «ingen duplikater
    # for uendrede rader», og det måles her.
    _beslutningslogg(migrator, TENANT, beslutning="TILLAT",
                     ts=datetime.now(timezone.utc) - timedelta(minutes=90))
    r3 = _hent(klient, "/v1/beslutninger", tok, limit=50,
               cursor=side2["neste_cursor"])
    assert r3.status_code == 200
    assert len({rad["id"] for rad in r3.json()["rader"]}) \
        == len(r3.json()["rader"])

    # Filteret er del av cursor-bindingen: en cursor laget uten filter er
    # ugyldig med filter — og omvendt.
    r4 = _hent(klient, "/v1/beslutninger", tok, limit=2,
               cursor=side1["neste_cursor"], policybeslutning="TILLAT")
    assert r4.status_code == 400 and r4.json()["feil"] == "cursor_ugyldig"

    r5 = _hent(klient, "/v1/beslutninger", tok, policybeslutning="TILLAT")
    assert {rad["policybeslutning"] for rad in r5.json()["rader"]} == {"TILLAT"}

    # Taket er 100 — over er request_feilformet, aldri stille avkorting.
    assert _hent(klient, "/v1/beslutninger", tok, limit=101).status_code == 400
    assert _hent(klient, "/v1/beslutninger", tok, limit=100).status_code == 200


@pg
def test_cursor_fra_annet_endepunkt_annen_tenant_og_retning_avvises(
        klient, migrator, policy):
    from api import cursor as cursormodul
    tok, _ = _lesetoken(migrator)
    naa = datetime.now(timezone.utc)
    # Peppret må være appens faktiske cursorpepper — ellers faller alt på
    # signaturen, og bindingene måles aldri.
    pepper = klient.app.tjeneste.cursorpepper

    for gal in (dict(tenant=ANNEN_TENANT), dict(endepunkt="unntak_historikk"),
                dict(retning="asc"), dict(filtre={"policybeslutning": "STOPP"})):
        args = dict(tenant=TENANT, endepunkt="beslutninger", retning="desc",
                    filtre={}, ts=naa, rad_id=1)
        args.update(gal)
        c = cursormodul.lag_v2(pepper, **args)
        r = _hent(klient, "/v1/beslutninger", tok, cursor=c)
        assert r.status_code == 400 and r.json()["feil"] == "cursor_ugyldig", gal

    # Manipulert og utløpt:
    gyldig = cursormodul.lag_v2(pepper, tenant=TENANT,
                                endepunkt="beslutninger", retning="desc",
                                filtre={}, ts=naa, rad_id=1)
    tuklet = gyldig[:-4] + ("aaaa" if not gyldig.endswith("aaaa") else "bbbb")
    assert _hent(klient, "/v1/beslutninger", tok,
                 cursor=tuklet).status_code == 400
    gammel = cursormodul.lag_v2(pepper, tenant=TENANT,
                                endepunkt="beslutninger", retning="desc",
                                filtre={}, ts=naa, rad_id=1,
                                naa=naa - timedelta(seconds=cursormodul.LEVETID_S + 60))
    assert _hent(klient, "/v1/beslutninger", tok,
                 cursor=gammel).status_code == 400


@pg
def test_detalj_stopp_unntak_og_sideeffektfri(klient, migrator, policy):
    tok, _ = _lesetoken(migrator)

    stopp = _beslutningslogg(migrator, TENANT, beslutning="STOPP")
    r = _hent(klient, f"/v1/beslutninger/{stopp}", tok)
    k = r.json()
    assert k["resultat"] == {"art": "policy_stoppet"}
    assert (k["evidensstatus"], k["sen_evidens"], k["konflikt_evidens"]) \
        == ("IKKE_RELEVANT", False, False)
    assert "sikkerhet" not in k, "uten security:read skal feltet MANGLE"
    assert k["begrunnelse"] == ["innenfor_grense"]
    assert k["policy_versjon"] == "1.0.0" and k["policy_hash"] == "c" * 64

    sak, loggpost = _lag_sak(migrator, TENANT)
    r = _hent(klient, f"/v1/beslutninger/{loggpost}", tok)
    k = r.json()
    assert k["resultat"]["art"] == "til_unntak"
    assert k["resultat"]["unntak_id"] == sak
    assert k["resultat"]["kategori"] == "manglende_data"
    assert k["evidensstatus"] == "IKKE_RELEVANT"

    tillat = _beslutningslogg(migrator, TENANT, beslutning="TILLAT")
    k = _hent(klient, f"/v1/beslutninger/{tillat}", tok).json()
    assert k["resultat"] == {"art": "sideeffektfri_tillatt"}


@pg
def test_detalj_outbox_artene_utledes_av_fk_bundet_oppdrag(klient, migrator,
                                                           policy):
    """v3 pkt. 1–3: arten kommer fra det FK-bundne oppdraget, aldri fra
    tidsnærhet — og evidensstatusen fra samme rad."""
    tok, _ = _lesetoken(migrator)
    sak, loggpost = _lag_sak(migrator, TENANT)
    opp, rid = _lag_oppdrag(migrator, TENANT, sak, loggpost)
    _sett_kontekst(migrator, TENANT)
    fk = migrator.execute(
        "SELECT beslutning_loggpost_id FROM oppdrag WHERE tenant=%s AND id=%s",
        (TENANT, opp)).fetchone()[0]
    migrator.commit()

    k = _hent(klient, f"/v1/beslutninger/{fk}", tok).json()
    assert k["resultat"] == {"art": "outbox_opprettet", "oppdrag_id": opp}
    assert k["evidensstatus"] == "MANGLER"

    # plukket -> MANGLER
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE oppdrag SET status='plukket'"
                     " WHERE tenant=%s AND id=%s", (TENANT, opp))
    migrator.commit()
    k = _hent(klient, f"/v1/beslutninger/{fk}", tok).json()
    assert k["resultat"]["art"] == "outbox_plukket"

    # feilet UTEN kvittering -> MANGLER + feil_aarsak=timeout
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE oppdrag SET status='feilet'"
                     " WHERE tenant=%s AND id=%s", (TENANT, opp))
    migrator.commit()
    k = _hent(klient, f"/v1/beslutninger/{fk}", tok).json()
    assert k["resultat"]["art"] == "outbox_feilet"
    assert k["resultat"]["feil_aarsak"] == "timeout"
    assert k["evidensstatus"] == "MANGLER"


@pg
def test_detalj_utfort_gyldig_og_kansellert_superseded(klient, migrator,
                                                       policy):
    tok, _ = _lesetoken(migrator)

    # utført MED kvittering -> GYLDIG
    sak, loggpost = _lag_sak(migrator, TENANT)
    opp, rid = _lag_oppdrag(migrator, TENANT, sak, loggpost)
    _sett_kontekst(migrator, TENANT)
    fk = migrator.execute(
        "SELECT beslutning_loggpost_id FROM oppdrag WHERE tenant=%s AND id=%s",
        (TENANT, opp)).fetchone()[0]
    migrator.execute(
        "UPDATE oppdrag SET status='plukket' WHERE tenant=%s AND id=%s",
        (TENANT, opp))
    migrator.execute(
        "UPDATE oppdrag SET status='utfort', kvittering='{}',"
        " resultathash=%s WHERE tenant=%s AND id=%s",
        ("h" * 64, TENANT, opp))
    migrator.commit()
    k = _hent(klient, f"/v1/beslutninger/{fk}", tok).json()
    assert k["resultat"]["art"] == "outbox_utfort"
    assert (k["evidensstatus"], k["sen_evidens"], k["konflikt_evidens"]) \
        == ("GYLDIG", False, False)

    # kansellert + superseded reparasjonsoperasjon
    sak2, loggpost2 = _lag_sak(migrator, TENANT)
    opp2, rid2 = _lag_oppdrag(migrator, TENANT, sak2, loggpost2)
    _sett_kontekst(migrator, TENANT)
    fk2 = migrator.execute(
        "SELECT beslutning_loggpost_id FROM oppdrag WHERE tenant=%s AND id=%s",
        (TENANT, opp2)).fetchone()[0]
    migrator.execute(
        "UPDATE oppdrag SET status='kansellert' WHERE tenant=%s AND id=%s",
        (TENANT, opp2))
    migrator.execute(
        "UPDATE reparasjonsoperasjoner SET status='superseded'"
        " WHERE tenant=%s AND repair_operation_id=%s", (TENANT, rid2))
    migrator.commit()
    k = _hent(klient, f"/v1/beslutninger/{fk2}", tok).json()
    assert k["resultat"]["art"] == "outbox_kansellert"
    assert k["resultat"]["superseded"] is True
    assert k["evidensstatus"] == "IKKE_RELEVANT"


@pg
def test_port6_port7_evidensflagg_avledes_aldri_konstrueres(klient, migrator,
                                                            policy):
    """Codex-port 6+7: flaggene er AVLEDET av kvitteringsportens
    append-only evidensrader — og bare av dem.

    Port 6: et identisk replay skriver ingen evidensrad (idempotensveiene
    returnerer FØR historikkskrivingen — målt i PR-006/007-testene), så et
    utført oppdrag UTEN slike rader skal ha begge flagg false. Port 7: en
    sen, motstridende kvittering har skrevet radene sine, og da settes
    BEGGE flagg — uten at resultatarten rører seg. Dør en mutasjon i
    utledningen (glemt hendelsestype, feil sak, konstruert flagg), dør den
    her.
    """
    tok, _ = _lesetoken(migrator, scopes=("decisions:read", "security:read"))
    sak, loggpost = _lag_sak(migrator, TENANT)
    opp, rid = _lag_oppdrag(migrator, TENANT, sak, loggpost)
    _sett_kontekst(migrator, TENANT)
    fk = migrator.execute(
        "SELECT beslutning_loggpost_id FROM oppdrag WHERE tenant=%s AND id=%s",
        (TENANT, opp)).fetchone()[0]
    migrator.execute(
        "UPDATE oppdrag SET status='plukket' WHERE tenant=%s AND id=%s",
        (TENANT, opp))
    migrator.execute(
        "UPDATE oppdrag SET status='utfort', kvittering='{}',"
        " resultathash=%s WHERE tenant=%s AND id=%s", ("h" * 64, TENANT, opp))
    migrator.commit()

    # PORT 6: ingen evidensrader — ingen flagg. (At replayveiene ikke
    # skriver rader er kvitteringsportens egne testers ansvar; her måles at
    # fraværet av rader ALDRI blir et flagg.)
    k = _hent(klient, f"/v1/beslutninger/{fk}", tok).json()
    assert k["resultat"]["art"] == "outbox_utfort"
    assert (k["sen_evidens"], k["konflikt_evidens"]) == (False, False)
    assert k["sikkerhet"] == {"sak_finnes": False}

    # En sen-rad på et ANNET oppdrag i samme sak skal ikke smitte.
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "INSERT INTO unntak_historikk (tenant, unntak_id, hendelse, aktor,"
        " request_id, detalj) VALUES (%s,%s,'sen_kvittering',"
        " 'kvitteringsport','r',%s)",
        (TENANT, sak, json.dumps({"oppdrag_id": opp + 1_000_000})))
    migrator.commit()
    k = _hent(klient, f"/v1/beslutninger/{fk}", tok).json()
    assert k["sen_evidens"] is False, \
        "sen-evidens for et annet oppdrag skal aldri farge dette"

    # PORT 7: en sen, MOTSTRIDENDE kvittering (evidensrader i historikken)
    # setter begge flagg — resultatet står urørt som utført.
    _sett_kontekst(migrator, TENANT)
    for h, detalj in (("sen_kvittering", {"oppdrag_id": opp}),
                      ("motstridende_kvittering", {"kilde": "oppdrag",
                                                   "ny": "x" * 64})):
        migrator.execute(
            "INSERT INTO unntak_historikk (tenant, unntak_id, hendelse,"
            " aktor, request_id, detalj) VALUES (%s,%s,%s,'kvitteringsport',"
            " 'r',%s)", (TENANT, sak, h, json.dumps(detalj)))
    migrator.commit()
    k = _hent(klient, f"/v1/beslutninger/{fk}", tok).json()
    assert k["resultat"]["art"] == "outbox_utfort", \
        "sen/konflikt-evidens endrer ALDRI resultatarten"
    assert (k["sen_evidens"], k["konflikt_evidens"]) == (True, True)
    # v4-invarianten: konfliktevidens => sikkerhetssak finnes.
    assert k["sikkerhet"] == {"sak_finnes": True}


@pg
def test_detalj_fase2_tillat_uten_oppdrag_er_utforelsesdata_utilgjengelig(
        klient, migrator, policy):
    """Vilkår V4: variantens PREMISS er at beslutningsraden selv beviser
    outbox-relevans (kilde='arbeidskapabilitet'); en ordinær TILLAT uten
    oppdrag er sideeffektfri, aldri «utilgjengelig»."""
    tok, _ = _lesetoken(migrator)
    fase2 = _beslutningslogg(migrator, TENANT, kilde="arbeidskapabilitet",
                             idem=secrets.token_hex(32))
    k = _hent(klient, f"/v1/beslutninger/{fase2}", tok).json()
    assert k["resultat"] == {"art": "utforelsesdata_ikke_tilgjengelig"}
    assert (k["evidensstatus"], k["sen_evidens"], k["konflikt_evidens"]) \
        == ("IKKE_RELEVANT", False, False)


@pg
def test_identisk_404_for_ukjent_og_annen_tenants_id(klient, migrator,
                                                     policy):
    """Codex-port 8/GO-krav: de to 404-ene er BYTE-identiske så nær som
    request-id — ellers er detaljendepunktet et orakel over andres data."""
    tok, _ = _lesetoken(migrator, scopes=("decisions:read",
                                          "exceptions:read"))
    annen = _beslutningslogg(migrator, ANNEN_TENANT, beslutning="STOPP")
    sak_annen, _ = _lag_sak(migrator, ANNEN_TENANT)

    for sti_mal, fremmed_id in (("/v1/beslutninger/{}", annen),
                                ("/v1/unntak/{}", sak_annen),
                                ("/v1/unntak/{}/historikk", sak_annen)):
        ukjent = _hent(klient, sti_mal.format(999_999_999), tok)
        fremmed = _hent(klient, sti_mal.format(fremmed_id), tok)
        assert ukjent.status_code == fremmed.status_code == 404
        a, b = ukjent.json(), fremmed.json()
        a.pop("request_id"), b.pop("request_id")
        assert a == b == {"feil": "ikke_funnet"}


@pg
def test_status_apen_er_hele_statusmaskinen_minus_de_terminale(
        klient, migrator, policy):
    """`?status=apen` må dekke ALT som ikke er ferdigbehandlet — også de fire
    godkjenningsstatusene fra PR-012.

    Dashbordet spurte tidligere om de åtte ferskeste sakene i ALLE statuser og
    silte selv, med en tillatelsesliste som var en utdatert kopi av
    statusmaskinen i 011. To feil i én: saker som ventet på en godkjenner ble
    aldri vist, og silingen skjedde ETTER `LIMIT`, så åtte ferdige saker
    gjemte en uløst sak bak sidegrensen.

    Kontroll: legg en av de ikke-terminale statusene tilbake i en
    tillatelsesliste-form, eller filtrer etter `LIMIT`, så blir denne rød.
    """
    tok, _ = _lesetoken(migrator, scopes=("exceptions:read",))
    ikke_terminale = ("ny", "under_behandling", "manuell", "venter_utførelse",
                      "venter_verifikasjon", "verifikasjon_klar",
                      "verifikasjon_retry_klar", "venter_godkjenning",
                      "venter_andre_godkjenner", "godkjenning_klar")
    apne = {_lag_sak(migrator, TENANT, status=s)[0] for s in ikke_terminale}
    # De terminale sist, altså SOM DE FERSKESTE. Det er hele poenget: siler
    # noen etter `LIMIT`, spiser disse to plassene i sidevinduet.
    for s in ("løst", "avvist"):
        _lag_sak(migrator, TENANT, status=s)

    # Nøyaktig like mange plasser som det finnes åpne saker jeg nettopp lagde.
    # Kom silingen etter grensen, ville svaret hatt åtte rader, ikke ti — og
    # de to eldste åpne sakene mine hadde falt ut bak sidegrensen.
    r = _hent(klient, "/v1/unntak", tok, status="apen",
              limit=len(ikke_terminale))
    assert r.status_code == 200
    saker = r.json()["saker"]
    assert {s["id"] for s in saker} == apne, \
        "åpne saker mangler eller terminale slapp inn"
    assert not {s["status"] for s in saker} & {"løst", "avvist"}

    # Pseudo-statusen utvider ikke det som ellers er lov å spørre om.
    assert _hent(klient, "/v1/unntak", tok,
                 status="åpen").status_code == 400


@pg
def test_unntaksdetalj_og_historikk(klient, migrator, policy):
    tok, _ = _lesetoken(migrator, scopes=("exceptions:read",))
    sak, loggpost = _lag_sak(migrator, TENANT)
    k = _hent(klient, f"/v1/unntak/{sak}", tok).json()
    assert k["id"] == sak and k["sakstype"] == "normal"
    assert k["status"] == "ny" and k["kategori"] == "manglende_data"
    assert "historikk" not in k, "historikken er et EGET endepunkt (v2 pkt. 7)"
    assert "payload" not in json.dumps(k)

    r = _hent(klient, f"/v1/unntak/{sak}/historikk", tok, limit=1)
    side = r.json()
    assert [rad["hendelse"] for rad in side["rader"]] == ["opprettet"]
    if side["neste_cursor"]:
        r2 = _hent(klient, f"/v1/unntak/{sak}/historikk", tok, limit=50,
                   cursor=side["neste_cursor"])
        assert r2.status_code == 200
        forste = {rad["id"] for rad in side["rader"]}
        assert forste.isdisjoint({rad["id"] for rad in r2.json()["rader"]})

    # Historikk-cursoren er bundet til SIN sak: samme endepunktnavn, annet
    # unntak_id-filter -> ugyldig.
    sak2, _ = _lag_sak(migrator, TENANT)
    if side["neste_cursor"]:
        r3 = _hent(klient, f"/v1/unntak/{sak2}/historikk", tok,
                   cursor=side["neste_cursor"])
        assert r3.status_code == 400


@pg
def test_sikkerhetssak_er_404_uten_security_read(klient, migrator, policy):
    """En 403 på en konkret ID bekrefter at sikkerhetssaken finnes — derfor
    404, samme svar som «finnes ikke»."""
    sak, _ = _lag_sak(migrator, TENANT, sakstype="sikkerhet")
    uten, _ = _lesetoken(migrator, scopes=("exceptions:read",))
    med, _ = _lesetoken(migrator, scopes=("exceptions:read", "security:read"))
    assert _hent(klient, f"/v1/unntak/{sak}", uten).status_code == 404
    assert _hent(klient, f"/v1/unntak/{sak}/historikk", uten).status_code == 404
    r = _hent(klient, f"/v1/unntak/{sak}", med)
    assert r.status_code == 200 and r.json()["sakstype"] == "sikkerhet"


@pg
def test_scope_og_bruker_rollens_default_deny(klient, migrator, policy):
    """Codex-port 9-forberedelsen + v2 pkt. 9: bruker-tokenet når aldri en
    muterende rute — SELV med `decision:write` feilutstedt i scopes."""
    kun_lese, _ = _lesetoken(migrator, scopes=("decisions:read",))
    r = _hent(klient, "/v1/policy/aktiv", kun_lese)
    assert r.status_code == 403 and r.json()["feil"] == "scope_mangler"

    feilutstedt, _ = _lag_token(migrator, TENANT, "bruker",
                                ["decision:write", "decisions:read"])
    r = klient.post("/v1/beslutning",
                    headers={"authorization": f"Bearer {feilutstedt}",
                             "idempotency-key": "n-1",
                             "content-type": "application/json"},
                    content=json.dumps({"policy_id": FIXTURE_POLICY_ID,
                                        "event": {}}))
    assert r.status_code == 403 and r.json()["feil"] == "scope_mangler", \
        "rollen skal stoppe det scope-kolonnen slapp gjennom"

    # og claim-veien: bruker-token uten ordre-scopes har tom prefiksliste.
    r = klient.post("/v1/oppdrag/claim",
                    headers={"authorization": f"Bearer {feilutstedt}"})
    assert r.status_code == 403


def test_rutescope_registeret_dekker_alle_ruter():
    """v2 pkt. 9: hver rute deklarert, hver deklarasjon reell — begge veier.

    `lag_app` krever en levende database; rutene leses derfor statisk ut av
    kilden — samme form som port 0/10-testene bruker.
    """
    import re as _re
    from api.app import RUTESCOPE, LESESCOPES
    from api import app as appmodul
    kilde = Path(appmodul.__file__).read_text(encoding="utf-8")
    ruter = set()
    for m in _re.finditer(
            r'Route\("([^"]+)",\s*\w+,\s*(?:\n\s*)?methods=\["(\w+)"\]',
            kilde):
        ruter.add((m.group(2), m.group(1)))
    deklarert = set(RUTESCOPE)
    assert ruter == deklarert, (
        f"udeklarert: {sorted(ruter - deklarert)} / "
        f"død deklarasjon: {sorted(deklarert - ruter)}")
    # None-scope er lovlig KUN for helsesjekkene og OIDC-/sesjonsrutene:
    # de to første etablerer en sesjon (kan ikke kreve en), og /v1/sesjon
    # er sesjonshåndtering (GET hvem / DELETE logout), ikke scope-gatet
    # lese-data. Alt annet UTEN scope ville vært en åpen dør.
    UAUTENTISERT_OK = {"/live", "/ready", "/v1/oidc/start",
                       "/v1/oidc/callback", "/v1/sesjon"}
    for (metode, sti), scope in RUTESCOPE.items():
        if scope is None:
            assert sti in UAUTENTISERT_OK, \
                f"{sti}: uventet uautentisert rute"
        elif sti.startswith("/v1/") and metode == "GET" \
                and "oppdrag" not in sti:
            assert scope in LESESCOPES, f"{sti}: leserute med ikke-lese-scope"


@pg
def test_policy_aktiv_dto_er_lukket_og_redigert(klient, migrator, policy):
    tok, _ = _lesetoken(migrator, scopes=("policy:read",))
    r = _hent(klient, "/v1/policy/aktiv", tok)
    assert r.status_code == 200
    dto = r.json()
    dto.pop("request_id")
    assert set(dto) == {"skjemaversjon", "policy_id", "versjon",
                        "innholds_hash", "roller", "handlinger",
                        "verifikatorer"}
    assert dto["skjemaversjon"] == 1
    assert dto["policy_id"] == FIXTURE_POLICY_ID
    tekst = json.dumps(dto)
    for forbudt in ("beskrivelse\":", "dataklasser", "retention", "meta",
                    "pepper", "secret", "tokenhash", "grupperingsnokkel"):
        assert forbudt not in tekst, f"lekket felt: {forbudt}"

    bokfor = next(h for h in dto["handlinger"] if h["navn"] == "faktura.bokfor")
    assert bokfor["grenser"]["belop_maks"] == "25000.00"
    assert bokfor["grenser"]["valuta"] == ["NOK"]
    assert bokfor["vilkaar"] == ["dublettsjekk", "leverandor_i_register",
                                 "mva_validert"]
    med_vindu = [h for h in dto["handlinger"]
                 if h["grenser"] and h["grenser"]["tidsvindu"]]
    assert med_vindu, "tidsvinduet fra malen skal være strukturert i DTO-en"
    v = med_vindu[0]["grenser"]["tidsvindu"]
    assert v["ukedager"] == [0, 1, 2, 3, 4]
    assert (v["fra"], v["til"]) == ("07:00", "17:00")
    assert v["tidssone"] == "Europe/Oslo"
    med_frekvens = [h for h in dto["handlinger"]
                    if h["grenser"] and h["grenser"]["frekvens"]]
    assert med_frekvens
    f = med_frekvens[0]["grenser"]["frekvens"]
    assert set(f) == {"maks", "vindu_enhet", "vindu_antall"}
    for verifikator in dto["verifikatorer"]:
        assert set(verifikator) == {"offentlig_id", "betrodd_for",
                                    "kan_fastsla_permanent"}
    # Handling uten grenser: eksplisitt null, aldri {}.
    assert all(h["grenser"] is None or isinstance(h["grenser"], dict)
               for h in dto["handlinger"])
    assert not any(h["grenser"] == {} for h in dto["handlinger"])


@pg
def test_policy_aktiv_uten_policy_er_404(klient, migrator):
    tok, _ = _lesetoken(migrator, scopes=("policy:read",))
    r = _hent(klient, "/v1/policy/aktiv", tok)
    assert r.status_code == 404 and r.json()["feil"] == "ikke_funnet"


# ===========================================================================
# DTO-validatoren — vilkår V5: grense-1 / grense / grense+1
# ===========================================================================

def _gyldig_dto(**overstyr):
    dto = {"skjemaversjon": 1, "policy_id": "p", "versjon": "1.0.0",
           "innholds_hash": "a" * 64,
           "roller": [{"id": "agent", "beskrivelse_kode": "rolle.agent"}],
           "handlinger": [{"navn": "faktura.bokfor", "modus": "auto",
                           "grenser": None, "vilkaar": ["dublettsjekk"]}],
           "verifikatorer": [{"offentlig_id": "v1", "betrodd_for": ["x"],
                              "kan_fastsla_permanent": False}]}
    dto.update(overstyr)
    return dto


def _grenser(**overstyr):
    g = {"belop_maks": None, "valuta": None, "tidsvindu": None,
         "frekvens": None}
    g.update(overstyr)
    return g


def test_dto_arraygrenser_ved_grensen():
    from api.lesing import valider_policy_dto

    def rolle(i):
        return {"id": f"r{i}", "beskrivelse_kode": f"rolle.r{i}"}

    def handling(i):
        return {"navn": f"h{i}.x", "modus": "auto", "grenser": None,
                "vilkaar": []}

    def verifikator(i):
        return {"offentlig_id": f"v{i}", "betrodd_for": ["x"],
                "kan_fastsla_permanent": False}

    for felt, lag, grense in (("roller", rolle, 50),
                              ("handlinger", handling, 200),
                              ("verifikatorer", verifikator, 100)):
        for n, ok in ((grense - 1, True), (grense, True), (grense + 1, False)):
            dto = _gyldig_dto(**{felt: [lag(i) for i in range(n)]})
            feil = valider_policy_dto(dto)
            assert (feil == []) is ok, f"{felt}={n}: {feil}"

    for n, ok in ((49, True), (50, True), (51, False)):
        dto = _gyldig_dto(handlinger=[{
            "navn": "h.x", "modus": "auto", "grenser": None,
            "vilkaar": [f"v{i}" for i in range(n)]}])
        assert (valider_policy_dto(dto) == []) is ok, f"vilkaar={n}"
        dto = _gyldig_dto(verifikatorer=[{
            "offentlig_id": "v", "betrodd_for": [f"b{i}" for i in range(n)],
            "kan_fastsla_permanent": True}])
        assert (valider_policy_dto(dto) == []) is ok, f"betrodd_for={n}"


def test_dto_frekvens_og_ukedager_ved_grensen():
    from api.lesing import valider_policy_dto

    def med_frekvens(maks=1, antall=1):
        return _gyldig_dto(handlinger=[{
            "navn": "h.x", "modus": "auto", "vilkaar": [],
            "grenser": _grenser(frekvens={"maks": maks,
                                          "vindu_enhet": "timer",
                                          "vindu_antall": antall})}])

    for maks, ok in ((0, False), (1, True), (99_999, True), (100_000, True),
                     (100_001, False)):
        assert (valider_policy_dto(med_frekvens(maks=maks)) == []) is ok, maks
    for antall, ok in ((0, False), (1, True), (10_000, True), (10_001, False)):
        assert (valider_policy_dto(med_frekvens(antall=antall)) == []) is ok

    def med_dager(dager):
        return _gyldig_dto(handlinger=[{
            "navn": "h.x", "modus": "auto", "vilkaar": [],
            "grenser": _grenser(tidsvindu={"ukedager": dager, "fra": "08:00",
                                           "til": "16:00",
                                           "tidssone": "Europe/Oslo"})}])
    assert valider_policy_dto(med_dager([0, 1, 2, 3, 4, 5])) == []
    assert valider_policy_dto(med_dager([0, 1, 2, 3, 4, 5, 6])) == []
    assert valider_policy_dto(med_dager([0, 1, 2, 3, 4, 5, 6, 6])) != []
    assert valider_policy_dto(med_dager([7])) != []
    assert valider_policy_dto(med_dager([])) != []


def test_dto_strenger_hash_og_belop_ved_grensen():
    from api.lesing import valider_policy_dto

    for lengde, ok in ((127, True), (128, True), (129, False)):
        dto = _gyldig_dto(policy_id="p" * lengde)
        assert (valider_policy_dto(dto) == []) is ok, lengde
    for lengde, ok in ((63, True), (64, True), (65, False)):
        dto = _gyldig_dto(versjon="v" * lengde)
        assert (valider_policy_dto(dto) == []) is ok
    for h, ok in (("a" * 63, False), ("a" * 64, True), ("a" * 65, False),
                  ("G" * 64, False)):
        dto = _gyldig_dto(innholds_hash=h)
        assert (valider_policy_dto(dto) == []) is ok

    def med_belop(belop, valuta=("NOK",)):
        return _gyldig_dto(handlinger=[{
            "navn": "h.x", "modus": "auto", "vilkaar": [],
            "grenser": _grenser(belop_maks=belop,
                                valuta=list(valuta) if valuta else None)}])

    assert valider_policy_dto(med_belop("25000.00")) == []
    assert valider_policy_dto(med_belop("0.01")) == []
    assert valider_policy_dto(med_belop("9999999999999.00")) == []          # 13 sifre
    assert valider_policy_dto(med_belop("99999999999999.00")) != []         # 14 sifre
    assert valider_policy_dto(med_belop("0.00")) != [], "positiv er kravet"
    assert valider_policy_dto(med_belop("1.5")) != [], "to desimaler, alltid"
    assert valider_policy_dto(med_belop("1,50")) != []
    assert valider_policy_dto(med_belop("25000.00", valuta=None)) != [], \
        "valuta er PÅKREVD når belop_maks er satt (v5)"
    assert valider_policy_dto(med_belop("25000.00", ("XKY",))) != [], \
        "tre store bokstaver er ikke det samme som en ISO 4217-kode"
    assert valider_policy_dto(med_belop("25000.00", ("NOK", "NOK"))) != []


def test_dto_ukjent_felt_avvises_paa_hvert_nivaa():
    """v3/v4 bindende test: ekstra felt på ETHVERT nivå er byggefeil."""
    from api.lesing import valider_policy_dto

    assert valider_policy_dto({**_gyldig_dto(), "ekstra": 1}) != []
    assert valider_policy_dto(_gyldig_dto(
        roller=[{"id": "r", "beskrivelse_kode": "rolle.r", "x": 1}])) != []
    assert valider_policy_dto(_gyldig_dto(
        handlinger=[{"navn": "h.x", "modus": "auto", "grenser": None,
                     "vilkaar": [], "x": 1}])) != []
    assert valider_policy_dto(_gyldig_dto(
        handlinger=[{"navn": "h.x", "modus": "auto", "vilkaar": [],
                     "grenser": {**_grenser(), "x": 1}}])) != []
    assert valider_policy_dto(_gyldig_dto(
        handlinger=[{"navn": "h.x", "modus": "auto", "vilkaar": [],
                     "grenser": _grenser(
                         tidsvindu={"ukedager": [0], "fra": "08:00",
                                    "til": "16:00", "tidssone": "Europe/Oslo",
                                    "x": 1})}])) != []
    assert valider_policy_dto(_gyldig_dto(
        handlinger=[{"navn": "h.x", "modus": "auto", "vilkaar": [],
                     "grenser": _grenser(
                         frekvens={"maks": 1, "vindu_enhet": "timer",
                                   "vindu_antall": 1, "x": 1})}])) != []
    assert valider_policy_dto(_gyldig_dto(
        verifikatorer=[{"offentlig_id": "v", "betrodd_for": ["x"],
                        "kan_fastsla_permanent": False, "x": 1}])) != []
    assert valider_policy_dto(_gyldig_dto(
        handlinger=[{"navn": "h.x", "modus": "auto", "vilkaar": [],
                     "grenser": _grenser()}])) != [], \
        "tomt grenser-objekt skal være normalisert til null før validering"


def test_dto_tidssone_valideres_mot_iana():
    from api.lesing import valider_policy_dto
    dto = _gyldig_dto(handlinger=[{
        "navn": "h.x", "modus": "auto", "vilkaar": [],
        "grenser": _grenser(tidsvindu={"ukedager": [0], "fra": "08:00",
                                       "til": "16:00",
                                       "tidssone": "Europe/Ukjentby"})}])
    assert any("IANA" in f for f in valider_policy_dto(dto))


# ===========================================================================
# Matrise-totalitet + tidsvindu-parseren
# ===========================================================================

def test_evidensmatrisen_er_total_og_lukket():
    """Hele (art × evidensstatus × sen × konflikt)-rommet, mot fasit."""
    from api.lesing import _kombinasjon_lovlig

    arter = ("policy_stoppet", "sideeffektfri_tillatt", "til_unntak",
             "utforelsesdata_ikke_tilgjengelig", "outbox_opprettet",
             "outbox_plukket", "outbox_utfort", "outbox_feilet",
             "outbox_kansellert")
    lovlige = set()
    for art in ("policy_stoppet", "sideeffektfri_tillatt", "til_unntak",
                "utforelsesdata_ikke_tilgjengelig"):
        lovlige.add((art, "IKKE_RELEVANT", False, False))
    for art in ("outbox_opprettet", "outbox_plukket"):
        for sen in (False, True):
            for k in (False, True):
                lovlige.add((art, "MANGLER", sen, k))
    for sen, k in ((False, False), (False, True), (True, True)):
        lovlige.add(("outbox_utfort", "GYLDIG", sen, k))
    for ev in ("GYLDIG", "MANGLER"):
        for sen in (False, True):
            for k in (False, True):
                lovlige.add(("outbox_feilet", ev, sen, k))
    for ev in ("IKKE_RELEVANT", "GYLDIG"):
        for sen in (False, True):
            for k in (False, True):
                lovlige.add(("outbox_kansellert", ev, sen, k))

    for art in arter + ("ukjent_art",):
        for ev in ("IKKE_RELEVANT", "MANGLER", "GYLDIG"):
            for sen in (False, True):
                for k in (False, True):
                    forventet = (art, ev, sen, k) in lovlige
                    assert _kombinasjon_lovlig(art, ev, sen, k) is forventet, \
                        (art, ev, sen, k)


def test_tidsvindu_parseren_ruller_over_uken():
    from api.lesing import _tidsvindu_dto
    assert _tidsvindu_dto("man-fre 08:00-16:00", "UTC")["ukedager"] \
        == [0, 1, 2, 3, 4]
    assert _tidsvindu_dto("fre-man 22:00-06:00", "UTC")["ukedager"] \
        == [4, 5, 6, 0]
    assert _tidsvindu_dto("son-son 00:00-23:59", "UTC")["ukedager"] == [6]
    with pytest.raises(ValueError):
        _tidsvindu_dto("man-fre 25:00-16:00", "UTC")
