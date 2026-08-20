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
"""
import os
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


#: Migrasjonsnummer -> (seed før N, måling etter N). En backfill-migrasjon
#: uten oppføring her kan ikke bestå port 17.
SEEDS = {48: (_seed_048, _mal_048)}


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
    base = psycopg.conninfo.conninfo_to_dict(mig_url)["dbname"]
    if not base.replace("_", "").isalnum():
        print(f"AVBRUTT: ugyldig basenavn {base!r}")
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
        print(f"seedet 047-tilstand for {n:03d}")

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
