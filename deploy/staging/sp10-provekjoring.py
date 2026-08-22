#!/usr/bin/env python3
"""SP-10: backfill-migrasjoner prøvekjøres mot en BEBODD base.

«Kjørbar DDL fra tom base» er halvparten av porten. CI på tom base kunne
per konstruksjon ikke se prod-stoppet i 047: masse-UPDATE køet utsatte
DEFERRABLE-triggerhendelser, og ALTER-klasse-setninger nekter å passere
dem. Skriptet bygger derfor en egen engangsbase 0..N−1 med den herdede
kjøreren, SEEDER nøyaktig radformene backfillen i N skal bære over
(inkludert utsatte FK-hendelser i samme transaksjonsklasse som stoppet
047), kjører N — og MÅLER utfallet i stedet for å anta det.

Seedene er per migrasjon: en backfill uten seed her har ikke bestått
SP-10, og CI-steget peker på nummeret sitt eksplisitt så en ny
backfill-migrasjon må registrere seg selv.

BRUK:  DISPONIT_SP10_ADMIN_URL=...    (superbruker, mot basen `postgres`)
       DISPONIT_SP10_MIGRATOR_URL=... (migrator, mot engangsbasen)
       python3 deploy/staging/sp10-provekjoring.py <migrasjonsnummer>

Engangsbasen MÅ hete `disponit_sp10`, eventuelt `disponit_sp10_<suffiks>`
for parallelle kjøringer: skriptet dropper og gjenskaper målbasen sin med
`DROP DATABASE ... WITH (FORCE)` gjennom superbrukertilkoblingen, og et
fritt basenavn i DSN-en ville gjort en skrivefeil til et datatap.
"""
import os
import re
import sys
from pathlib import Path

ROT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROT / "platform/core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import psycopg  # noqa: E402

# Speiler CI-steget «Databaseroller»: skjemaet må eies av migrator og
# eierrollene må kunne CREATE (funksjonseierskap i migrasjonene).
SKJEMA_ROLLER = ("disponit_authenticator", "disponit_m37_claimer",
                 "disponit_policy_eier", "disponit_modul_eier",
                 "disponit_domene_eier")

TEN = "t-sp10"


def _seed_048(conn):
    """Bebodd 047-tilstand for 048-backfillen — hver arm får en rad:

    - et AKTIVT planvindu (claimet før 048 → skal attesteres 'disponit'),
      et ledig og et terminalt (skal stå urørt / forbli NULL);
    - en BUNDET styrt hendelse (policyer bærer 'styrt' + operasjon, med
      den utsatte FK-en `policyer_aktivert_av_hendelse_fk` I KØ — 047-
      stoppets klasse) → skal få kilde 'styrt' og rundebundet kvorumskrav;
    - en FORELDRELØS hendelse (versjonsraden slettet) → 'historisk';
    - et GJENBRUKT versjonsnummer (Codex P1): den foreldreløse hendelsen
      og den gjeldende deler tenant+policy_id+versjon, fordi
      `slett_ubrukt_policy` frigir nummeret og det er aktivert på nytt.
      Den gamle skal bli 'historisk', den nye 'styrt' — en versjonsbundet
      backfill stemplet begge 'styrt'.
    """
    from db.pg import sett_kontekst
    sett_kontekst(conn, TEN, "sp10:seed", "r-sp10")
    pid = conn.execute(
        "INSERT INTO bestillingsplan (tenant, bestillingstype, parametre,"
        " rytme, time_lokal, tidssone, opprettet_av, status) VALUES"
        " (%s,'kontroll.wcag.nettsted','{}','daglig',8,'Europe/Oslo',"
        "'sp10:seed','aktiv') RETURNING plan_id", (TEN,)).fetchone()[0]
    conn.execute(
        "INSERT INTO bestillingsplan_vindu (plan_id, tenant, vindu_start,"
        " vindu_slutt) VALUES (%s,%s, now()-interval '2 hours',"
        " now()-interval '1 hour')", (pid, TEN))
    conn.execute(
        "INSERT INTO bestillingsplan_vindu (plan_id, tenant, vindu_start,"
        " vindu_slutt, tilstand, claim_id, lease_utloper) VALUES"
        " (%s,%s, now()-interval '26 hours', now()-interval '25 hours',"
        " 'aktivt', gen_random_uuid(), now()+interval '1 hour')", (pid, TEN))
    conn.execute(
        "INSERT INTO bestillingsplan_vindu (plan_id, tenant, vindu_start,"
        " vindu_slutt, tilstand, terminalisert_ts) VALUES"
        " (%s,%s, now()-interval '50 hours', now()-interval '49 hours',"
        " 'terminal', now()-interval '49 hours')", (pid, TEN))

    def _hendelse(ppid, versjon, ihash, *, runde=1):
        opid = f"sp10-{ppid}-{runde}"
        # ON CONFLICT: utkastet deles av rundene på samme policy — det er
        # nettopp slik en runde 2 på samme utkast ser ut i produksjon.
        conn.execute(
            "INSERT INTO policyutkast (tenant,utkast_id,policy_id,innhold,"
            "innholds_hash,status,opprettet_av) VALUES (%s,%s,%s,"
            "'{}'::jsonb,%s,'aktivert','sp10:seed')"
            " ON CONFLICT DO NOTHING",
            (TEN, "u-" + ppid, ppid, ihash))
        conn.execute(
            "INSERT INTO aktiveringsrunde (tenant,utkast_id,runde,status,"
            "diff_hash,utkast_innholds_hash,base_policy_hash,risikoklasse,"
            "klassifisering_hash,klassifikatorversjon,policyskjema_versjon,"
            "motor_semantikkversjon,deny_all_hash,deny_all_versjon,"
            "pakrevd_antall_godkjennere,utloper,decision_operation_id,"
            "aktivert_som_versjon) VALUES (%s,%s,%s,'brukt','d-'||%s,%s,"
            "'b','UTVIDER','k','1','0.2','1','dh','1',2,"
            "now()+interval '1 hour',%s,%s)",
            (TEN, "u-" + ppid, runde, ppid, ihash, opid, versjon))
        for bruker in ("uavh", "uavh2"):
            conn.execute(
                "INSERT INTO aktiveringsattestasjon (tenant,utkast_id,"
                "runde,bruker_id,rolle,authz_version,er_forfatter,"
                "diff_hash,klassifisering_hash,risikoklasse,"
                "konvoluttversjon,konvolutt_hash,mac,mac_key_id,jti,"
                "utloper) VALUES (%s,%s,%s,%s,'okonomi',1,false,'d-'||%s,"
                "'k','UTVIDER',1,'h','m','mk1',%s,now()+interval '1 hour')",
                (TEN, "u-" + ppid, runde, bruker, ppid,
                 f"sp10-jti-{ppid}-{bruker}-r{runde}-000000"))
        conn.execute(
            "INSERT INTO policyaktivering (tenant,policy_id,utkast_id,"
            "runde,decision_operation_id,versjon,innholds_hash,diff_hash,"
            "attestant_a,attestant_b) VALUES (%s,%s,%s,%s,%s,%s,%s,"
            "'d-'||%s,'uavh','uavh2')",
            (TEN, ppid, "u-" + ppid, runde, opid, versjon, ihash, ppid))
        return opid

    # Bundet: versjonsraden finnes, bærer 'styrt' og operasjonen —
    # `policyer_aktivert_av_hendelse_fk` er DEFERRABLE og står nå I KØ.
    opid = _hendelse("p-sp10-bundet", "1.0.0", "ih-bundet")
    conn.execute(
        "INSERT INTO policyer (tenant,policy_id,versjon,innholds_hash,"
        "status,innhold,aktiv,aktiveringskilde,aktivert_av_operasjon)"
        " VALUES (%s,'p-sp10-bundet','1.0.0','ih-bundet','produksjon',"
        "'{}'::jsonb,false,'styrt',%s)", (TEN, opid))
    # Foreldreløs: hendelsen står igjen, versjonsraden finnes ikke.
    _hendelse("p-sp10-tapt", "2.0.0", "ih-tapt")
    # GJENBRUKT VERSJONSNUMMER (Codex P1). Runde 1 aktiverte 1.0.0,
    # versjonsraden ble siden slettet (`slett_ubrukt_policy`) og nummeret
    # frigitt; runde 2 aktiverte NØYAKTIG samme (policy_id, versjon) på
    # nytt. De to hendelsene er derfor umulige å skille på
    # tenant+policy_id+versjon — bare operasjonen skiller dem.
    #
    # Rundene commites hver for seg fordi det er slik de oppsto:
    # `hendelse_en_per_levende_versjon` er en DEFERRED constraint-trigger
    # som fyrer for HVER rad innsatt i transaksjonen, og med begge
    # hendelsene i samme tx ville runde 1 sett runde 2s levende
    # versjonsrad og (med rette) nektet.
    _hendelse("p-sp10-gjenbruk", "1.0.0", "ih-gjenbruk", runde=1)
    conn.commit()
    sett_kontekst(conn, TEN, "sp10:seed", "r-sp10")
    opid_ny = _hendelse("p-sp10-gjenbruk", "1.0.0", "ih-gjenbruk", runde=2)
    conn.execute(
        "INSERT INTO policyer (tenant,policy_id,versjon,innholds_hash,"
        "status,innhold,aktiv,aktiveringskilde,aktivert_av_operasjon)"
        " VALUES (%s,'p-sp10-gjenbruk','1.0.0','ih-gjenbruk','produksjon',"
        "'{}'::jsonb,true,'styrt',%s)", (TEN, opid_ny))
    conn.commit()


def _mal_048(conn) -> list[str]:
    from db.pg import sett_kontekst
    sett_kontekst(conn, TEN, "sp10:fasit", "r-sp10-2")
    feil = []
    rader = conn.execute(
        "SELECT tilstand, claimet_av FROM bestillingsplan_vindu"
        " WHERE tenant = %s", (TEN,)).fetchall()
    fasit_vindu = {("ledig", None), ("aktivt", "disponit"),
                   ("terminal", None)}
    if set(rader) != fasit_vindu or len(rader) != 3:
        feil.append(f"vinduer etter 048: {sorted(rader)!r},"
                    f" ventet {sorted(fasit_vindu)!r}")
    # `decision_operation_id` er med i målingen fordi den er det ENESTE
    # som skiller de to gjenbruks-hendelsene: de deler policy_id og
    # versjon, og det er nettopp der en versjonsbundet backfill bommet.
    rader = conn.execute(
        "SELECT decision_operation_id, policy_id, aktiveringskilde,"
        "       pakrevd_antall"
        "  FROM policyaktivering WHERE tenant = %s", (TEN,)).fetchall()
    fasit_hend = {("sp10-p-sp10-bundet-1", "p-sp10-bundet", "styrt", 2),
                  ("sp10-p-sp10-tapt-1", "p-sp10-tapt", "historisk", 2),
                  # Foreldreløs, samme versjonsnummer som den under.
                  ("sp10-p-sp10-gjenbruk-1", "p-sp10-gjenbruk",
                   "historisk", 2),
                  ("sp10-p-sp10-gjenbruk-2", "p-sp10-gjenbruk", "styrt", 2)}
    if set(rader) != fasit_hend or len(rader) != 4:
        feil.append(f"hendelser etter 048: {sorted(rader)!r},"
                    f" ventet {sorted(fasit_hend)!r}")
    conn.rollback()
    return feil


def _seed_049(conn):
    """Bebodd 048-tilstand for 049: promoterte artefakter på TO releaser
    (24 rader i prod, r1 + r5) — `artefakt_release_fk` og den
    refererbare nøkkelen må validere mot dem, og det er nøyaktig
    klassen tom base ikke kan se."""
    from db.pg import sett_kontekst
    sett_kontekst(conn, TEN, "sp10:seed", "r-sp10-49")
    conn.execute(
        "INSERT INTO modulhode (modul_id, status) VALUES ('m_sp10','aktiv')")
    conn.execute(
        "INSERT INTO modulkontrakt (modul_id, kontraktversjon,"
        " kontrakt_hash, payload_schema_hash, kvittering_schema_hash,"
        " sideeffektklasse, reversibilitet) VALUES"
        " ('m_sp10',1,'kh-sp10','ph','qh','ekstern_lesing','direkte')")
    for rel in ("sp10-r1", "sp10-r2"):
        conn.execute(
            "INSERT INTO modulrelease (modul_id, release_id,"
            " kontraktversjon, kontrakt_hash, manifest_hash,"
            " artifact_digest) VALUES ('m_sp10',%s,1,'kh-sp10','mh',"
            "'digest-sp10')", (rel,))
    conn.execute(
        "INSERT INTO moduldeployment (modul_id, release_id,"
        " kontraktversjon, kontrakt_hash, miljo, livslop) VALUES"
        " ('m_sp10','sp10-r1',1,'kh-sp10','staging','draining')")
    conn.execute(
        "INSERT INTO moduldeployment (modul_id, release_id,"
        " kontraktversjon, kontrakt_hash, miljo, livslop) VALUES"
        " ('m_sp10','sp10-r2',1,'kh-sp10','staging','claiming')")
    conn.execute(
        "INSERT INTO artefaktskjema (skjema_hash, kanonisk) VALUES"
        " ('44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a','{}') ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO artefakttype_register (artefakttype, eiermodul,"
        " kontraktversjon, kontrakt_hash, skjema_hash) VALUES"
        " ('sp10.rapport','m_sp10',1,'kh-sp10','44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a')")
    conn.execute(
        "INSERT INTO tenant_nokler (tenant, key_id, wrapped_dek) VALUES"
        " (%s,'k-sp10','\\x00'::bytea) ON CONFLICT DO NOTHING", (TEN,))
    for i, rel in enumerate(("sp10-r1", "sp10-r2")):
        blid = conn.execute(
            "INSERT INTO revisjonslogg (tenant, input_hash, policy_id,"
            " beslutning, begrunnelse, idempotency_key, kilde) VALUES"
            " (%s,'h','p','TILLAT','[]'::jsonb,%s,'arbeidskapabilitet')"
            " RETURNING id", (TEN, f"sp10-idem-{i}")).fetchone()[0]
        oid = conn.execute(
            "INSERT INTO oppdrag (opprinnelse, tenant, oppdragstype,"
            " handling, eiermodul, status, payload_kryptert, key_id,"
            " nonce, utforelsesfrist, evidensfrist, koblingsstatus,"
            " beslutning_loggpost_id) VALUES"
            " ('beslutning',%s,'kontroll.wcag.nettsted',"
            "'kontroll.wcag.nettsted','m_sp10','utfort',%s,'k-sp10',%s,"
            " now()+interval '1 hour', now()+interval '2 hours',"
            "'KOBLET', %s)"
            " RETURNING id",
            (TEN, b"\x00" * 24, b"\x00" * 12, blid)).fetchone()[0]
        conn.execute(
            "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype,"
            " modul_id, release_id, kontraktversjon, kontrakt_hash,"
            " module_epoch, tilstand, storrelse_bytes, klartekst_sha256,"
            " ciphertext, nonce, dek_ref, kapabilitet_jti, promotert_ts)"
            " VALUES (%s,%s,'sp10.rapport','m_sp10',%s,1,"
            "'kh-sp10',0,'promotert',64,%s,%s,%s,'k-sp10',%s, now())",
            (TEN, oid, rel, "ab" * 32, b"\x01" * 40, b"\x02" * 12,
             f"sp10-jti-{i}-000000000000"))
    conn.commit()


def _mal_049(conn) -> list[str]:
    feil = []
    for navn in ("artefakt_release_fk", "artefakt_refererbar"):
        n = conn.execute(
            "SELECT count(*) FROM pg_constraint WHERE conname = %s"
            "   AND conrelid = 'artefakt'::regclass", (navn,)).fetchone()[0]
        if n != 1:
            feil.append(f"constrainten {navn} mangler etter 049")
    n = conn.execute(
        "SELECT count(*) FROM akseptkrav_punkt"
        " WHERE krav_id = 'wcag-kontroll-v1'").fetchone()[0]
    if n < 20:
        feil.append(f"kravpunktregisteret har {n} punkter, ventet >= 20")
    conn.rollback()
    return feil


#: Migrasjonsnummer -> (seed før N, måling etter N). En backfill-migrasjon
#: uten oppføring her kan ikke bestå port 17.


def _seed_056(conn):
    """Bebodd 055-tilstand for 056-swappen (klarsignalets port 33): ett
    oppdrag per EKSISTERENDE opprinnelse, i produksjonsform, som skal
    bæres uendret gjennom constraint-swappen. Uten seedet måler
    prøvekjøringen bare at swappen godtar en tom tabell — 047-stoppets
    klasse."""
    import os
    import secrets
    # Engangsbasen har ingen driftshemmeligheter: payloaden seedet
    # krypterer finnes bare for at radene skal ha produksjonsFORM, og
    # nøkkelen er testens egen (samme mønster som pytest-fixturens KEK).
    os.environ.setdefault("DISPONIT_KEK", "ab" * 32)
    from db import kryptering
    from db.pg import sett_kontekst
    sett_kontekst(conn, TEN, "sp10:seed", "r-sp10-056")
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, TEN)
    ct, nonce = kryptering.krypter(dek, {"sp10": "056"}, TEN, key_id)
    # Beslutningsveien: TILLAT-loggpost + oppdrag som API-veien lager det.
    logg = conn.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'sp10','api_token','ih','p@1.0.0/x.y','TILLAT','[]',%s)"
        " RETURNING id", (TEN, "sp10-056-b")).fetchone()[0]
    conn.execute(
        "INSERT INTO oppdrag (opprinnelse, tenant, beslutning_loggpost_id,"
        " oppdragstype, handling, eiermodul, payload_kryptert, key_id,"
        " nonce, utforelsesfrist, evidensfrist, koblingsstatus)"
        " VALUES ('beslutning',%s,%s,'kontroll.wcag.nettsted',"
        "'kontroll.wcag.nettsted','m_wcag_audit',%s,%s,%s,"
        " now()+interval '1 hour', now()+interval '1 day','KOBLET')",
        (TEN, logg, ct, key_id, nonce))
    # M-37-veien: unntakssak + reparasjonsoperasjon + fase-2-loggpost +
    # oppdrag med hele trioen — formen `_lag_sak`/`_lag_oppdrag` bruker.
    ulogg = conn.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, policy_content_hash)"
        " VALUES (%s,'sp10','test','ih','p@1.0.0/purring.send','UNNTAK',"
        "'[]',%s) RETURNING id", (TEN, "1" * 64)).fetchone()[0]
    sak = conn.execute(
        "INSERT INTO unntak (tenant, loggpost_id, handling, kategori,"
        " sakstype, prioritet, payload_kryptert, key_id, nonce,"
        " maks_auto_forsok_snapshot, policy_versjon, policy_content_hash,"
        " sakskilde)"
        " VALUES (%s,%s,'purring.send','manglende_data','normal','normal',"
        " %s,%s,%s,3,'1.0.0',%s,'policybrudd') RETURNING id",
        (TEN, ulogg, ct, key_id, nonce, "1" * 64)).fetchone()[0]
    rid = secrets.token_hex(32)          # 64 hex-tegn — formen 006 krever
    conn.execute(
        "INSERT INTO reparasjonsoperasjoner (tenant, unntak_id,"
        " repair_operation_id, repair_generation, handler_id,"
        " handler_versjon, maalhandling, input_hash, kategori)"
        " VALUES (%s,%s,%s,0,'r1_reinnsending','1','purring.send',%s,"
        "'manglende_data')", (TEN, sak, rid, secrets.token_hex(32)))
    # KOBLET krever fase-2-beslutningsloggposten også for m37-veien
    # (oppdrag_kobling_konsistent) — produksjonsformen fra _lag_oppdrag.
    fase2 = conn.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'sp10','arbeidskapabilitet','ih2','p@1.0.0/x.y',"
        "'TILLAT','[]',%s) RETURNING id", (TEN, rid)).fetchone()[0]
    conn.execute(
        "INSERT INTO oppdrag (opprinnelse, tenant, unntak_id, loggpost_id,"
        " repair_operation_id, beslutning_loggpost_id, oppdragstype,"
        " handling, eiermodul,"
        " payload_kryptert, key_id, nonce, utforelsesfrist, evidensfrist,"
        " koblingsstatus)"
        " VALUES ('m37_reparasjon',%s,%s,%s,%s,%s,'reinnsending',"
        "'purring.send','eiermodul:reinnsending',%s,%s,%s,"
        " now()+interval '1 hour', now()+interval '30 days','KOBLET')",
        (TEN, sak, ulogg, rid, fase2, ct, key_id, nonce))
    conn.commit()


def _mal_056(conn) -> list[str]:
    """Etter 056: begge seedede opprinnelser står uendret; totalformen
    avviser hybrider; kjeden liste→signatur→frigivelse→oppdrag er
    representerbar — og usignert frigivelse er det IKKE."""
    import psycopg
    from db.pg import sett_kontekst
    sett_kontekst(conn, TEN, "sp10:fasit", "r-sp10-056-2")
    feil = []
    rader = conn.execute(
        "SELECT opprinnelse, beslutning_loggpost_id IS NOT NULL,"
        "       unntak_id IS NOT NULL, frigivelse_id"
        "  FROM oppdrag WHERE tenant = %s ORDER BY opprinnelse",
        (TEN,)).fetchall()
    fasit = [("beslutning", True, False, None),
             ("m37_reparasjon", True, True, None)]
    if rader != fasit:
        feil.append(f"seedede oppdrag etter 056: {rader!r}, ventet {fasit!r}")
    # Totalformen: den GAMLE beslutningsarmen tok aldri stilling til
    # frigivelse_id — nå avvises hybriden av CHECK-en, før noen FK rekker
    # å mene noe.
    try:
        conn.execute(
            "INSERT INTO oppdrag (opprinnelse, tenant,"
            " beslutning_loggpost_id, frigivelse_id, oppdragstype,"
            " handling, eiermodul, payload_kryptert, key_id, nonce,"
            " utforelsesfrist, evidensfrist, koblingsstatus)"
            " SELECT 'beslutning', tenant, beslutning_loggpost_id,"
            " gen_random_uuid(), oppdragstype, handling, eiermodul,"
            " payload_kryptert, key_id, nonce, utforelsesfrist,"
            " evidensfrist, koblingsstatus FROM oppdrag"
            " WHERE tenant = %s AND opprinnelse = 'beslutning'", (TEN,))
        feil.append("hybrid (beslutning + frigivelse_id) ble AKSEPTERT")
        conn.rollback()
    except psycopg.errors.CheckViolation:
        conn.rollback()
    # Kjeden er representerbar — og rekkefølgen er tvungen. Direkte DML
    # (eierens rett), for funksjonsveien måles av pytest-portene.
    sett_kontekst(conn, TEN, "sp10:fasit", "r-sp10-056-3")
    # Kjeden STARTER i en fullført `rekruttering.evaluering` — etter
    # Cursor P2 (runde 7 på #140) er det en SKJEMApåstand
    # (`utsendingsliste_promotering`), ikke bare en funksjonspåstand, så
    # den seedede WCAG-raden kan ikke lenger bære listen. Den seedede
    # raden står urørt (fasit over måler nettopp det); evalueringen
    # lages her, av samme autorisasjon og samme payload.
    oid = conn.execute(
        "INSERT INTO oppdrag (opprinnelse, tenant,"
        " beslutning_loggpost_id, oppdragstype, handling, eiermodul,"
        " payload_kryptert, key_id, nonce, utforelsesfrist,"
        " evidensfrist, koblingsstatus)"
        " SELECT 'beslutning', tenant, beslutning_loggpost_id,"
        " 'rekruttering.evaluering','rekruttering.evaluering', eiermodul,"
        " payload_kryptert, key_id, nonce, utforelsesfrist, evidensfrist,"
        " 'KOBLET' FROM oppdrag WHERE tenant=%s AND"
        " opprinnelse='beslutning'"
        " AND oppdragstype='kontroll.wcag.nettsted' RETURNING id",
        (TEN,)).fetchone()[0]
    for steg in ("plukket", "utfort"):        # statusmaskinens lovlige vei
        conn.execute("UPDATE oppdrag SET status=%s WHERE tenant=%s"
                     " AND id=%s", (steg, TEN, oid))
    bid = conn.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://sp10.local','sp10-056') RETURNING bruker_id"
        ).fetchone()[0]
    liste = conn.execute(
        "INSERT INTO utsendingsliste (tenant, liste_id, utkast_serie,"
        " oppdrag_id, listetype, malversjon, innhold_hash, antall)"
        " VALUES (%s, gen_random_uuid(), gen_random_uuid(), %s,"
        " 'invitasjon','m@1','h1',3) RETURNING liste_id, utkast_serie,"
        " innhold_hash", (TEN, oid)).fetchone()
    # Grunnlaget committes FØR den negative proben: rollbacken etter det
    # FORVENTEDE nei-et skal kaste probens rad, aldri grunnlaget.
    conn.commit()
    sett_kontekst(conn, TEN, "sp10:fasit", "r-sp10-056-3b")
    # Usignert frigivelse er ikke representerbar (port 6):
    try:
        conn.execute(
            "INSERT INTO utsendingsfrigivelse (tenant, frigivelse_id,"
            " liste_id, innhold_hash, utkast_serie, mottaker_ref)"
            " VALUES (%s, gen_random_uuid(), %s, %s, %s, 'm1')",
            (TEN, liste[0], liste[2], liste[1]))
        feil.append("frigivelse UTEN signatur ble akseptert")
        conn.rollback()
        return feil
    except psycopg.errors.ForeignKeyViolation:
        conn.rollback()
    sett_kontekst(conn, TEN, "sp10:fasit", "r-sp10-056-4")
    conn.execute(
        "INSERT INTO utsendingssignatur (tenant, liste_id, utkast_serie,"
        " innhold_hash, signatar, operasjonsnokkel)"
        " VALUES (%s,%s,%s,%s,%s,'sp10-056-sig')",
        (TEN, liste[0], liste[1], liste[2], bid))
    frig = conn.execute(
        "INSERT INTO utsendingsfrigivelse (tenant, frigivelse_id,"
        " liste_id, innhold_hash, utkast_serie, mottaker_ref)"
        " VALUES (%s, gen_random_uuid(), %s, %s, %s, 'm1')"
        " RETURNING frigivelse_id", (TEN, liste[0], liste[2],
                                     liste[1])).fetchone()[0]
    conn.execute(
        "INSERT INTO oppdrag (opprinnelse, tenant, frigivelse_id,"
        " oppdragstype, handling, eiermodul, payload_kryptert, key_id,"
        " nonce, utforelsesfrist, evidensfrist, koblingsstatus)"
        " SELECT 'frigivelse', tenant, %s, 'rekruttering.utsending',"
        " 'rekruttering.utsending', 'm57_ats', payload_kryptert, key_id,"
        " nonce, now()+interval '4 hours', now()+interval '1 day',"
        " 'KOBLET' FROM oppdrag WHERE tenant=%s AND id=%s",
        (frig, TEN, oid))
    conn.commit()
    # GUC-en er transaksjonslokal — kontekst settes på nytt for tellingen,
    # ellers teller RLS et tomt vindu og kaller det null rader.
    sett_kontekst(conn, TEN, "sp10:fasit", "r-sp10-056-5")
    n = conn.execute("SELECT count(*) FROM oppdrag WHERE tenant=%s AND"
                     " opprinnelse='frigivelse'", (TEN,)).fetchone()[0]
    if n != 1:
        feil.append(f"frigivelsesoppdraget: {n} rader, ventet 1")
    return feil


SEEDS = {48: (_seed_048, _mal_048), 49: (_seed_049, _mal_049),
         56: (_seed_056, _mal_056)}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    admin = os.environ.get("DISPONIT_SP10_ADMIN_URL")
    mig_url = os.environ.get("DISPONIT_SP10_MIGRATOR_URL")
    if not admin or not mig_url or len(argv) != 1 or not argv[0].isdigit():
        print("AVBRUTT: DISPONIT_SP10_ADMIN_URL, DISPONIT_SP10_MIGRATOR_URL"
              " og <migrasjonsnummer> kreves — se docstringen.")
        return 2
    n = int(argv[0])
    if n not in SEEDS:
        print(f"AVBRUTT: migrasjon {n:03d} har ingen SP-10-seed. En"
              " backfill-migrasjon registrerer seed+måling i SEEDS; en"
              " migrasjon uten masse-skriving skal ikke pekes på her.")
        return 2
    seed, mal = SEEDS[n]
    base = psycopg.conninfo.conninfo_to_dict(mig_url).get("dbname") or ""
    # ENGANGSBASENAVNET ER EN PORT, IKKE EN FORMALITET (Codex P1).
    # Neste setning i skriptet er `DROP DATABASE ... WITH (FORCE)`, og
    # den kjøres gjennom en SUPERBRUKER-tilkobling. Den gamle sjekken
    # spurte bare om navnet var alfanumerisk — altså sa den ja til
    # `disponit` og `disponit_test`. Én slurvefeil i DSN-en når SP-10
    # kjøres for hånd (kommandoen står i docstringen over) og staging-
    # eller produksjonsbasen var borte FØR noe hadde fastslått at målet
    # var en engangsbase.
    #
    # Navnerommet er derfor dedikert og lukket: nøyaktig `disponit_sp10`,
    # eventuelt med et `_<suffiks>` for parallelle kjøringer. Alt annet
    # avvises, og de operasjonelle navnene avvises for seg med sin egen
    # melding — en «ugyldig basenavn»-linje forklarer ikke hvor nær det
    # var.
    OPERASJONELLE = {"disponit", "disponit_test", "postgres", "template0",
                     "template1"}
    if base in OPERASJONELLE:
        print(f"AVBRUTT: {base!r} er en OPERASJONELL base. SP-10 dropper"
              " og gjenskaper målbasen sin med DROP DATABASE ... WITH"
              " (FORCE) — den kjøres aldri mot noe annet enn"
              " engangsbasen `disponit_sp10[_<suffiks>]`. Ingenting er"
              " droppet.")
        return 2
    if not re.fullmatch(r"disponit_sp10(_[a-z0-9]+)*", base):
        print(f"AVBRUTT: {base!r} er ikke et SP-10-engangsbasenavn."
              " DISPONIT_SP10_MIGRATOR_URL må peke på `disponit_sp10`,"
              " eventuelt `disponit_sp10_<suffiks>` for parallelle"
              " kjøringer. Ingenting er droppet.")
        return 2

    ac = psycopg.connect(admin, autocommit=True)
    try:
        ac.execute(f'DROP DATABASE IF EXISTS "{base}" WITH (FORCE)')
        ac.execute(f'CREATE DATABASE "{base}" OWNER disponit_migrator')
    finally:
        ac.close()
    adm2 = psycopg.conninfo.conninfo_to_dict(admin) | {"dbname": base}
    ac = psycopg.connect(psycopg.conninfo.make_conninfo(**adm2),
                         autocommit=True)
    try:
        ac.execute("ALTER SCHEMA public OWNER TO disponit_migrator")
        for r in SKJEMA_ROLLER:
            ac.execute(f"GRANT USAGE, CREATE ON SCHEMA public TO {r}")
    finally:
        ac.close()

    # Nøyaktig deployens vei: den herdede kjøreren, i migrer.mains etapper
    # (legacy → herding → m37-backfill), stoppet på N−1.
    import migrer as migrer_mod
    from db import m37_backfill
    from db.kjorer import LAAS, LEGACY_MAKS, migrer
    from db.pg import koble
    bootstrap = migrer_mod.last_bootstrap()
    conn = koble(mig_url)
    try:
        conn.execute("SELECT pg_advisory_lock(%s)", (LAAS,))
        conn.commit()
        migrer(conn, til_og_med=LEGACY_MAKS)
        bootstrap.herd_historikk(conn)
        migrer(conn, til_og_med=m37_backfill.KJOR_ETTER_MIGRASJON)
        m37_backfill.backfill(conn)
        kjort = migrer(conn, til_og_med=n - 1)
        print(f"bebodd base: 1..{max(kjort)} bygget")

        seed(conn)
        print(f"seedet bebodd tilstand for {n:03d}")

        resten = migrer(conn)
        if n not in resten:
            print(f"AVBRUTT: {n:03d} ble ikke kjørt (kjørte: {resten})")
            return 1
        feil = mal(conn)
        for f in feil:
            print(f"RØD: {f}")
        if feil:
            return 1
        print(f"SP-10 GRØNN: {n:03d} kjørte mot bebodd base og målingen"
              " holdt")
    finally:
        try:
            conn.rollback()  # en feilet transaksjon må vekk før opplåsing
            conn.execute("SELECT pg_advisory_unlock(%s)", (LAAS,))
            conn.commit()
        finally:
            conn.close()
    ac = psycopg.connect(admin, autocommit=True)
    try:
        ac.execute(f'DROP DATABASE "{base}" WITH (FORCE)')
    finally:
        ac.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
