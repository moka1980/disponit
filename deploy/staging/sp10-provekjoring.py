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
                 "disponit_domene_eier",
                 # Klyngen «orden i eget hus» (092-096). Prøvekjøringen
                 # etterligner deployens vei; mangler en eierrolle CREATE
                 # på public her, stopper SP-10-porten på en migrasjon som
                 # ville gått fint i produksjon — altså en falsk rød.
                 "disponit_kvalitet_eier", "disponit_lager_eier",
                 "disponit_mal_eier", "disponit_kunnskap_eier",
                 "disponit_plikt_eier",
                 # Klynge 2 (097-100), samme grunn.
                 "disponit_tilgang_eier", "disponit_lisens_eier",
                 "disponit_personvern_eier", "disponit_compliance_eier")

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
    # EGEN beslutningsloggpost: `oppdrag_en_per_beslutning` (008) gir
    # loggposten nøyaktig ETT oppdrag, så evalueringen kan ikke arve
    # seedets — den er sin egen beslutning, slik API-veien lager den.
    ev_logg = conn.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'sp10','api_token','ih','p@1.0.0/x.y','TILLAT','[]',"
        "%s) RETURNING id", (TEN, "sp10-056-ev")).fetchone()[0]
    oid = conn.execute(
        "INSERT INTO oppdrag (opprinnelse, tenant,"
        " beslutning_loggpost_id, oppdragstype, handling, eiermodul,"
        " payload_kryptert, key_id, nonce, utforelsesfrist,"
        " evidensfrist, koblingsstatus)"
        " SELECT 'beslutning', tenant, %s,"
        " 'rekruttering.evaluering','rekruttering.evaluering', eiermodul,"
        " payload_kryptert, key_id, nonce, utforelsesfrist, evidensfrist,"
        " 'KOBLET' FROM oppdrag WHERE tenant=%s AND"
        " opprinnelse='beslutning'"
        " AND oppdragstype='kontroll.wcag.nettsted' RETURNING id",
        (ev_logg, TEN)).fetchone()[0]
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
            " VALUES (%s, gen_random_uuid(), %s, %s, %s, 'psn-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')",
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
        " VALUES (%s, gen_random_uuid(), %s, %s, %s, 'psn-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')"
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


#: En tenant-ID med `/` i seg. 058 lot den reservere fritt (`lager_sti`
#: sto NULL mens raden var `reservert`, så `inndata_lagersti_navnerom`
#: sov), og `init-tenant.sh` tar tenant-argumentet uten stikomponent-
#: sjekk. Den er nøyaktig raden 059s backfill ville felt hele
#: migrasjonen på — Codex P1 på #196.
UTRYGG_TEN = "t/sp10"


def _seed_059(conn):
    """Bebodd 058-tilstand for 059 (B-maskinen): backfillen av
    fødselsstien PLUSS constraint-swappen, med hver arm bebodd.

    Seks rader, én per ting 059 kan ødelegge:

    - `reservert` UTEN sti, i en lovlig tenant — 058s eneste form for en
      levende reservasjon, og den ene backfillen faktisk skal bære over.
    - `reservert` UTEN sti i en tenant hvis ID ikke er en lovlig
      stikomponent. Å gi den `<tenant>/<uuid>.bin` bryter
      `inndata_lagersti_navnerom` og RULLER HELE 059 TILBAKE — for alle
      tenanter, inne i vedlikeholdsvinduet. Tom-base-CI kan per
      konstruksjon ikke se den.
    - `reservert` UTEN sti hvis fødselssti alt er OPPTATT av en `lastet`
      rad. 058 lot kalleren velge filnavnet, så aliaset kan bygges med
      vilje; backfillen ville felt `inndata_lagersti_unik` og dermed
      hele 059. Paret er to rader: reservasjonen og squatteren.
    - `lastet` og `bundet` i produksjonsform, som skal stå ORDRETT
      gjennom swappen av `inndata_tilstand_totalt`.

    `oppdrag` er dessuten BEBODD her, og det er 059s andre masse-
    skriving: `ADD COLUMN fodt_xid ... DEFAULT pg_current_xact_id()` er
    et volatilt default, altså en full tabellomskriving. På tom base
    måler CI at setningen parser."""
    import os
    import secrets
    import uuid
    # Engangsbasen har ingen driftshemmeligheter (samme mønster som
    # _seed_056): nøkkelen finnes bare for at radene skal ha
    # produksjonsFORM.
    os.environ.setdefault("DISPONIT_KEK", "ab" * 32)
    from db import kryptering
    from db.pg import sett_kontekst
    sett_kontekst(conn, TEN, "sp10:seed", "r-sp10-059")
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, TEN)
    ct, nonce = kryptering.krypter(dek, {"sp10": "059"}, TEN, key_id)

    # (1) Levende reservasjon, 058-form: stien er NULL og skal fødes av
    #     backfillen som NØYAKTIG <tenant>/<inndata_id>.bin.
    conn.execute(
        "INSERT INTO inndata_artefakt (tenant, eiermodul, formaal,"
        " innholdstype, maks_bytes, reservasjon_jti, idempotensnokkel,"
        " utloper) VALUES (%s,'m57_ats','soknadsbunt','application/zip',"
        " 1024,%s,'sp10-059-reservert', now() + interval '1 hour')",
        (TEN, secrets.token_hex(32)))

    # (2) Samme form, men i en tenant som ikke kan bære en sti.
    sett_kontekst(conn, UTRYGG_TEN, "sp10:seed", "r-sp10-059-utrygg")
    conn.execute(
        "INSERT INTO inndata_artefakt (tenant, eiermodul, formaal,"
        " innholdstype, maks_bytes, reservasjon_jti, idempotensnokkel,"
        " utloper) VALUES (%s,'m57_ats','soknadsbunt','application/zip',"
        " 1024,%s,'sp10-059-utrygg', now() + interval '1 hour')",
        (UTRYGG_TEN, secrets.token_hex(32)))
    sett_kontekst(conn, TEN, "sp10:seed", "r-sp10-059-2")

    # (3) Lastet, full produksjonsform (måling + krypto + sti).
    lid = uuid.uuid4()
    conn.execute(
        "INSERT INTO inndata_artefakt (tenant, inndata_id, eiermodul,"
        " formaal, innholdstype, maks_bytes, faktiske_bytes,"
        " innhold_sha256, key_id, nonce, lager_sti, status,"
        " reservasjon_jti, idempotensnokkel, utloper, lastet_ts)"
        " VALUES (%s,%s,'m57_ats','soknadsbunt','application/zip',1024,10,"
        " %s,%s,%s,%s,'lastet',%s,'sp10-059-lastet',"
        " now() + interval '1 hour', now())",
        (TEN, lid, "a" * 64, key_id, nonce, f"{TEN}/{lid}.bin",
         secrets.token_hex(32)))

    # (4) Bundet: en lastet som HAR fått oppdraget sitt. Oppdraget er
    #     beslutningsveiens form, ordrett fra _seed_056 — `bind_inndata`
    #     kjøres ikke her, seedet skal bære 058-TILSTANDEN, ikke dørens
    #     vei til den.
    logg = conn.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'sp10','api_token','ih','p@1.0.0/x.y','TILLAT','[]',%s)"
        " RETURNING id", (TEN, "sp10-059-b")).fetchone()[0]
    oid = conn.execute(
        "INSERT INTO oppdrag (opprinnelse, tenant, beslutning_loggpost_id,"
        " oppdragstype, handling, eiermodul, payload_kryptert, key_id,"
        " nonce, utforelsesfrist, evidensfrist, koblingsstatus)"
        " VALUES ('beslutning',%s,%s,'kontroll.wcag.nettsted',"
        "'kontroll.wcag.nettsted','m_wcag_audit',%s,%s,%s,"
        " now()+interval '1 hour', now()+interval '1 day','KOBLET')"
        " RETURNING id", (TEN, logg, ct, key_id, nonce)).fetchone()[0]
    bid = uuid.uuid4()
    conn.execute(
        "INSERT INTO inndata_artefakt (tenant, inndata_id, eiermodul,"
        " formaal, innholdstype, maks_bytes, faktiske_bytes,"
        " innhold_sha256, key_id, nonce, lager_sti, status, oppdrag_id,"
        " reservasjon_jti, idempotensnokkel, utloper, lastet_ts,"
        " bundet_ts)"
        " VALUES (%s,%s,'m57_ats','soknadsbunt','application/zip',1024,10,"
        " %s,%s,%s,%s,'bundet',%s,%s,'sp10-059-bundet',"
        " now() + interval '1 hour', now(), now())",
        (TEN, bid, "b" * 64, key_id, nonce, f"{TEN}/{bid}.bin", oid,
         secrets.token_hex(32)))
    # (5) STIEN ER ALT OPPTATT (Codex P1, runde 2 på #196). Under 058 tok
    #     `registrer_inndata_lastet` filnavnet fra kalleren (`p_sti`,
    #     058:440-441), og runtime har SELECT på tabellen — en kaller
    #     kunne lese en synlig reservasjons id og laste opp SIN bunt på
    #     nøyaktig reservasjonens fremtidige fødselssti. Backfillen ville
    #     delt ut den samme strengen og felt `inndata_lagersti_unik`,
    #     altså hele 059. Reservasjonen skal vike, aliaset skal stå.
    #     Formen er uoppnåelig på en base som alt står på 059 (6-arg-
    #     døren utleder stien), så bare SP-10 kan bebo den.
    aid = uuid.uuid4()
    conn.execute(
        "INSERT INTO inndata_artefakt (tenant, inndata_id, eiermodul,"
        " formaal, innholdstype, maks_bytes, reservasjon_jti,"
        " idempotensnokkel, utloper)"
        " VALUES (%s,%s,'m57_ats','soknadsbunt','application/zip',1024,%s,"
        " 'sp10-059-alias-res', now() + interval '1 hour')",
        (TEN, aid, secrets.token_hex(32)))
    sid = uuid.uuid4()
    conn.execute(
        "INSERT INTO inndata_artefakt (tenant, inndata_id, eiermodul,"
        " formaal, innholdstype, maks_bytes, faktiske_bytes,"
        " innhold_sha256, key_id, nonce, lager_sti, status,"
        " reservasjon_jti, idempotensnokkel, utloper, lastet_ts)"
        " VALUES (%s,%s,'m57_ats','soknadsbunt','application/zip',1024,10,"
        " %s,%s,%s,%s,'lastet',%s,'sp10-059-alias-sti',"
        " now() + interval '1 hour', now())",
        (TEN, sid, "c" * 64, key_id, nonce, f"{TEN}/{aid}.bin",
         secrets.token_hex(32)))
    conn.commit()


def _mal_059(conn) -> list[str]:
    """Etter 059: fødselsstien er delt ut til de radene som KAN bære en,
    de som ikke kan er terminert i stedet for å felle migrasjonen, de
    ferdige radene står ordrett, og begge vaktene er tilbake."""
    import psycopg
    from db.pg import sett_kontekst
    sett_kontekst(conn, TEN, "sp10:fasit", "r-sp10-059-f")
    feil = []

    rader = dict(conn.execute(
        "SELECT idempotensnokkel, (status, lager_sti)::text"
        "  FROM inndata_artefakt WHERE tenant = %s", (TEN,)).fetchall())
    fodt = conn.execute(
        "SELECT status, lager_sti = tenant || '/' || inndata_id::text"
        " || '.bin' FROM inndata_artefakt"
        " WHERE tenant = %s AND idempotensnokkel = 'sp10-059-reservert'",
        (TEN,)).fetchone()
    if fodt != ("reservert", True):
        feil.append("den levende reservasjonen fikk ikke dørens egen"
                    f" fødselssti: {fodt}")
    for nokkel, ventet in (("sp10-059-lastet", "lastet"),
                           ("sp10-059-bundet", "bundet")):
        if nokkel not in rader:
            feil.append(f"{nokkel}: raden er borte etter 059")
        elif not rader[nokkel].startswith(f"({ventet},"):
            feil.append(f"{nokkel}: {rader[nokkel]}, ventet {ventet}")

    # Sti-aliaset: reservasjonen vek, aliaset står ORDRETT. Måles på
    # stien og ikke bare på statusen — hadde backfillen tatt aliaset i
    # stedet, ville en `lastet` bunt mistet filen sin.
    alias = conn.execute(
        "SELECT status, lager_sti FROM inndata_artefakt"
        " WHERE tenant = %s AND idempotensnokkel = 'sp10-059-alias-res'",
        (TEN,)).fetchone()
    if alias != ("forkastet", None):
        feil.append("reservasjonen hvis fødselssti alt var opptatt skulle"
                    f" stått forkastet uten sti: {alias}")
    squatter = conn.execute(
        "SELECT status, lager_sti = %s || '/' || (SELECT inndata_id::text"
        "   FROM inndata_artefakt WHERE tenant = %s"
        "    AND idempotensnokkel = 'sp10-059-alias-res') || '.bin'"
        "  FROM inndata_artefakt WHERE tenant = %s"
        "   AND idempotensnokkel = 'sp10-059-alias-sti'",
        (TEN, TEN, TEN)).fetchone()
    if squatter != ("lastet", True):
        feil.append("raden som eide stien skulle stått urørt som lastet"
                    f" på nøyaktig den stien: {squatter}")

    # Den ulovlige tenanten: terminert, uten sti — og fortsatt der, så
    # ingen rad ble slettet i det stille.
    sett_kontekst(conn, UTRYGG_TEN, "sp10:fasit", "r-sp10-059-u")
    utrygg = conn.execute(
        "SELECT status, lager_sti FROM inndata_artefakt"
        " WHERE tenant = %s AND idempotensnokkel = 'sp10-059-utrygg'",
        (UTRYGG_TEN,)).fetchone()
    if utrygg != ("forkastet", None):
        feil.append("reservasjonen i tenanten med ulovlig stikomponent"
                    f" skulle stått forkastet uten sti: {utrygg}")

    # CHECKen er AKTIV, ikke bare tilstede: en reservasjon uten sti kan
    # ikke lenger skrives. Savepoint, ellers tar den feilede setningen
    # resten av målingen med seg.
    sett_kontekst(conn, TEN, "sp10:fasit", "r-sp10-059-c")
    conn.execute("SAVEPOINT p")
    try:
        conn.execute(
            "INSERT INTO inndata_artefakt (tenant, eiermodul, formaal,"
            " innholdstype, maks_bytes, reservasjon_jti,"
            " idempotensnokkel, utloper)"
            " VALUES (%s,'m57_ats','soknadsbunt','application/zip',1024,"
            " %s,'sp10-059-etterpaa', now() + interval '1 hour')",
            (TEN, "c" * 32))
        feil.append("reservert UTEN lager_sti gikk gjennom etter 059 —"
                    " inndata_tilstand_totalt er ikke aktiv")
        conn.execute("ROLLBACK TO SAVEPOINT p")
    except psycopg.errors.CheckViolation:
        conn.execute("ROLLBACK TO SAVEPOINT p")

    # Vaktene backfillen måtte slippe: begge tilbake i samme transaksjon.
    vakt, force = conn.execute(
        "SELECT (SELECT tgenabled FROM pg_trigger"
        "         WHERE tgrelid = 'inndata_artefakt'::regclass"
        "           AND tgname = 'inndata_artefakt_vakt'),"
        "       (SELECT relforcerowsecurity FROM pg_class"
        "         WHERE oid = 'inndata_artefakt'::regclass)").fetchone()
    if vakt != "O":
        feil.append(f"inndata_artefakt_vakt står tgenabled={vakt!r},"
                    " ikke 'O' — backfillen slo den ikke på igjen")
    if not force:
        feil.append("FORCE ROW LEVEL SECURITY er ikke slått på igjen")

    # X1s fødselsattest på en BEBODD oppdragstabell: kolonnen er skrevet
    # for hver eksisterende rad (volatilt default = full omskriving), og
    # attesten er uforanderlig etterpå.
    n, arvet_naa = conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE fodt_xid ="
        " pg_catalog.pg_current_xact_id() AND fodt_oppstart ="
        " pg_catalog.pg_postmaster_start_time())"
        " FROM oppdrag WHERE tenant = %s", (TEN,)).fetchone()
    if n != 1:
        feil.append(f"seedet oppdrag: {n} rader, ventet 1")
    if arvet_naa:
        feil.append(f"{arvet_naa} arvede oppdrag bærer DENNE"
                    " transaksjonens attest — X1 ville sluppet dem inn")
    # Begge leddene er uforanderlige. `fodt_oppstart` er clusterens
    # inkarnasjon (Codex P1, runde 2): uten den ville en `fodt_xid` fra en
    # gjenopprettet dump vært gyldig i den nye clusteren.
    for kolonne, verdi in (
            ("fodt_xid", "pg_catalog.pg_current_xact_id()"),
            ("fodt_oppstart", "pg_catalog.pg_postmaster_start_time()"
                              " - interval '1 day'")):
        conn.execute("SAVEPOINT q")
        try:
            conn.execute(f"UPDATE oppdrag SET {kolonne} = {verdi}"
                         " WHERE tenant = %s", (TEN,))
            feil.append(f"{kolonne} lot seg skrive om — fødselsattesten"
                        " er ingen attest")
            conn.execute("ROLLBACK TO SAVEPOINT q")
        except psycopg.errors.RaiseException:
            conn.execute("ROLLBACK TO SAVEPOINT q")
    conn.rollback()
    return feil


def _seed_067(conn):
    """Bebodd 066-tilstand for 067s engangs-makulering (Cursor P1/P2 på
    #252): den ENE formen migrasjonen møter ved oppgradering og som ingen
    tom-base-kjøring kan vise — en prosess som ALT er reapet av reaperen
    slik den sto FØR 067, med den promoterte rapportens payload i live.

    Etter `slettet_ts` er satt plukkes prosessen aldri igjen (reaperens
    predikat er `slettet_ts IS NULL`), så uten engangssteget beholder
    nettopp disse radene ciphertext for alltid. Formen er uoppnåelig på
    en base som alt står på 067: vakten avviser da merket så lenge
    rapporten bærer payload. Bare SP-10 kan bebo den.

    To armer, én per utfall:

    - REAPET prosess + promotert rapport med payload → skal makuleres av
      067 (payload nullet, merke satt, tilstand + hash i behold).
    - LEVENDE prosess + promotert rapport med payload → skal stå ORDRETT
      urørt; backfillen er ikke en tabellsveip.
    """
    import os
    import secrets
    # Engangsbasen har ingen driftshemmeligheter (samme mønster som
    # _seed_056): nøkkelen finnes bare for at radene skal ha
    # produksjonsFORM.
    os.environ.setdefault("DISPONIT_KEK", "ab" * 32)
    from db import kryptering
    from db.pg import sett_kontekst
    sett_kontekst(conn, TEN, "sp10:seed", "r-sp10-067")
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, TEN)

    # Rapporttypen i registerets ekte form: artefakt-FK-en går via
    # `artefakttype_register` → `modulkontrakt`. Fødes bare hvis den ikke
    # alt finnes (035-formen), og bindingen LESES etterpå — en hardkodet
    # hash ville felt FK-en den dagen en migrasjon seeder kontrakten
    # først, altså gjort seedet skjørt mot sin egen fremtid.
    conn.execute(
        "INSERT INTO modulkontrakt (modul_id, kontraktversjon,"
        " kontrakt_hash, payload_schema_hash, kvittering_schema_hash,"
        " sideeffektklasse, reversibilitet)"
        " SELECT 'm57_ats',1,'kh-sp10-067','p','k','krever_outbox',"
        "'kompenserende'"
        " WHERE NOT EXISTS (SELECT 1 FROM modulkontrakt"
        "   WHERE modul_id='m57_ats' AND kontraktversjon=1)")
    conn.execute(
        "INSERT INTO artefakttype_register (artefakttype, eiermodul,"
        " kontraktversjon, kontrakt_hash, skjema_hash)"
        " SELECT 'rekruttering.evaluering.rapport', mk.modul_id,"
        "        mk.kontraktversjon, mk.kontrakt_hash, 's'"
        "   FROM modulkontrakt mk"
        "  WHERE mk.modul_id='m57_ats' AND mk.kontraktversjon=1"
        "    AND NOT EXISTS (SELECT 1 FROM artefakttype_register"
        "      WHERE artefakttype='rekruttering.evaluering.rapport')")
    modul, ver, kh = conn.execute(
        "SELECT eiermodul, kontraktversjon, kontrakt_hash"
        "  FROM artefakttype_register"
        " WHERE artefakttype='rekruttering.evaluering.rapport'").fetchone()
    # `artefakt_release_fk` (049) binder (modul_id, release_id) til
    # modulregisteret — samme rigg som _seed_049 bygger for sin modul.
    conn.execute(
        "INSERT INTO modulhode (modul_id, status) SELECT %s,'aktiv'"
        " WHERE NOT EXISTS (SELECT 1 FROM modulhode WHERE modul_id=%s)",
        (modul, modul))
    conn.execute(
        "INSERT INTO modulrelease (modul_id, release_id, kontraktversjon,"
        " kontrakt_hash, manifest_hash, artifact_digest)"
        " SELECT %s,'r-sp10',%s,%s,'mh','digest-sp10'"
        " WHERE NOT EXISTS (SELECT 1 FROM modulrelease"
        "   WHERE modul_id=%s AND release_id='r-sp10')",
        (modul, ver, kh, modul))

    def _arm(merke: str, reapet: bool):
        # Beslutningsveien, ordrett fra _seed_056 — men et CLAIMET
        # `rekruttering.evaluering`-oppdrag, den ENESTE tilstanden et
        # retensjonsanker fødes på (057s vakt måler nettopp den).
        logg = conn.execute(
            "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
            " policy_id, beslutning, begrunnelse, idempotency_key)"
            " VALUES (%s,'sp10','api_token','ih','p@1.0.0/x.y','TILLAT',"
            "'[]',%s) RETURNING id", (TEN, "sp10-067-" + merke)).fetchone()[0]
        ct, nonce = kryptering.krypter(dek, {"sp10": "067", "arm": merke},
                                       TEN, key_id)
        oid = conn.execute(
            "INSERT INTO oppdrag (opprinnelse, tenant,"
            " beslutning_loggpost_id, oppdragstype, handling, eiermodul,"
            " payload_kryptert, key_id, nonce, utforelsesfrist,"
            " evidensfrist, koblingsstatus)"
            " VALUES ('beslutning',%s,%s,'rekruttering.evaluering',"
            "'rekruttering.evaluering','m57_ats',%s,%s,%s,"
            " now()+interval '1 hour', now()+interval '1 day','KOBLET')"
            " RETURNING id", (TEN, logg, ct, key_id, nonce)).fetchone()[0]
        conn.execute("UPDATE oppdrag SET status='plukket'"
                     " WHERE tenant=%s AND id=%s", (TEN, oid))
        conn.execute(
            "INSERT INTO rekrutteringsprosess (tenant, prosess_id,"
            " oppdrag_id, slettefrist_dogn)"
            " VALUES (%s, gen_random_uuid(), %s, 30)", (TEN, oid))
        # Rapporten: promotert, med STRUKTURELT dekrypterbar payload —
        # det er den som overlevde fristen, og som backfillen måles på.
        conn.execute(
            "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype,"
            " modul_id, release_id, kontraktversjon, kontrakt_hash,"
            " module_epoch, tilstand, storrelse_bytes, klartekst_sha256,"
            " ciphertext, nonce, dek_ref, kapabilitet_jti, promotert_ts)"
            " VALUES (%s,%s,'rekruttering.evaluering.rapport',%s,"
            "'r-sp10',%s,%s,1,'promotert',10,%s,%s,%s,%s,%s, now())",
            (TEN, oid, modul, ver, kh, "h-" + merke, ct, nonce, key_id,
             "jti-sp10-067-" + secrets.token_hex(8)))
        if reapet:
            # Reaperens merke slik den SATTE det før 067: de seks lagrene
            # er tomme, så 057-vakten slipper det gjennom — og rapporten
            # blir stående.
            conn.execute(
                "UPDATE rekrutteringsprosess"
                "   SET lukket_ts = now() - interval '31 days',"
                "       slettet_ts = now() - interval '1 day'"
                " WHERE tenant=%s AND oppdrag_id=%s", (TEN, oid))
        return oid

    _arm("reapet", True)
    _arm("levende", False)
    conn.commit()


def _mal_067(conn) -> list[str]:
    """Etter 067: den reapede armens rapport er TØMT og merket, med
    tilstand og hash i behold; den levende armens rapport står ordrett
    urørt.

    NEGATIVEN LIGGER I SEEDET: fjernes engangs-løkken fra 067, står den
    reapede armens ciphertext fortsatt der — og målingen her er rød. En
    tom-base-`migrer` er grønn i begge tilfeller."""
    from db.pg import sett_kontekst
    sett_kontekst(conn, TEN, "sp10:fasit", "r-sp10-067-2")
    feil = []
    rader = conn.execute(
        "SELECT p.slettet_ts IS NOT NULL, a.tilstand,"
        "       a.ciphertext IS NULL, a.nonce IS NULL,"
        "       a.makulert_ts IS NOT NULL, a.klartekst_sha256"
        "  FROM rekrutteringsprosess p JOIN artefakt a"
        "    ON a.tenant = p.tenant AND a.oppdrag_id = p.oppdrag_id"
        " WHERE p.tenant = %s ORDER BY p.slettet_ts IS NOT NULL",
        (TEN,)).fetchall()
    fasit = [(False, "promotert", False, False, False, "h-levende"),
             (True, "promotert", True, True, True, "h-reapet")]
    if rader != fasit:
        feil.append(f"067 mot bebodd base: {rader!r}, ventet {fasit!r}")
    conn.rollback()
    return feil


SEEDS = {48: (_seed_048, _mal_048), 49: (_seed_049, _mal_049),
         56: (_seed_056, _mal_056), 59: (_seed_059, _mal_059),
         67: (_seed_067, _mal_067)}


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
