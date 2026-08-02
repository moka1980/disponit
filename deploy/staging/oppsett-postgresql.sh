#!/usr/bin/env bash
# ============================================================
# Disponit staging: PostgreSQL-oppsett på Cloud Server S (Ubuntu)
# Kjøres som root av Claude Code. Idempotent.
# ============================================================
set -euo pipefail

DB=disponit
BRUKER=disponit              # RUNTIME — kun DML, eier ingenting
MIGRATOR=disponit_migrator   # eier skjemaet, kjører migrasjoner
AUTH=disponit_authenticator  # eier api_tokener; runtime naar den aldri direkte
MILJOFIL=/etc/disponit/staging.env

# Rolleskillet er Codex' P1 fra PR-004-reviewen: eide runtime-rollen
# tabellene, kunne den skru av eller slette append-only-triggerne selv.
# En vakt du kan fjerne er ingen vakt.

apt-get update -q
apt-get install -y -q postgresql postgresql-contrib
systemctl enable --now postgresql

# Roller + database (idempotent)
# Roller er KLYNGEobjekter og opprettes her, med superbrukeren — aldri i en
# migrasjon. Draften til PR-005 gjorde det siste, og det feiler i vaart
# oppsett fordi migratorrollen verken har eller skal ha CREATEROLE.
for r in "$BRUKER" "$MIGRATOR"; do
  sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$r'" \
    | grep -q 1 || sudo -u postgres psql -c \
    "CREATE ROLE $r LOGIN PASSWORD '$(openssl rand -hex 24)'"
done
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$AUTH'" \
  | grep -q 1 || sudo -u postgres psql -qc "CREATE ROLE $AUTH NOLOGIN"
# Migrator maa vaere MEDLEM av authenticator for aa kunne sette eierskap
# (OWNER TO) paa api_tokener i migrasjon 003.
sudo -u postgres psql -qc "GRANT $AUTH TO $MIGRATOR"

sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='$DB'" \
  | grep -q 1 || sudo -u postgres createdb -O $MIGRATOR $DB

# Test-database for staging-kjøringer
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB}_test'" \
  | grep -q 1 || sudo -u postgres createdb -O $MIGRATOR ${DB}_test

# ------------------------------------------------------------
# Miljøfil med hemmeligheter — 0600, aldri i repo (RUTINER/ADR-001).
# Tilstandsmaskinen ligger i lib-miljofil.sh og testes i CI; her kalles den
# bare. Alle fem feilene som er funnet i dette oppsettet satt i den logikken,
# og manuell staging-prøve er ingen port.
# ------------------------------------------------------------
mkdir -p /etc/disponit && chmod 700 /etc/disponit
touch "$MILJOFIL" && chmod 600 "$MILJOFIL"

. "$(dirname "$0")/lib-miljofil.sh"

RUNTIME_DSN=("DATABASE_URL=$DB" "DISPONIT_TEST_DSN=${DB}_test")
MIGRATOR_DSN=("DISPONIT_MIGRATOR_URL=$DB" "DISPONIT_TEST_MIGRATOR_DSN=${DB}_test")

sikre_rolle_dsn "$BRUKER"   "${RUNTIME_DSN[@]}"
sikre_rolle_dsn "$MIGRATOR" "${MIGRATOR_DSN[@]}"
sikre_attestasjonsnokler

# Sannhetsprøve FØR noen DSN tas i bruk.
#
# Codex' P1: denne sto tidligere bare til slutt. Var forrige kjøring avbrutt
# mellom passordrotasjon og filskriving, pekte migrator-DSN-en på et passord
# rollen ikke lenger hadde — migrasjonen feilet, `set -e` avsluttet skriptet,
# og reparasjonen ble aldri nådd. Reparasjonen var altså utilgjengelig
# nøyaktig i den tilstanden den fantes for. Den må kjøre før første bruk.
verifiser_og_reparer "$BRUKER"   "${RUNTIME_DSN[@]}"
verifiser_og_reparer "$MIGRATOR" "${MIGRATOR_DSN[@]}"

# ------------------------------------------------------------
# Migrasjoner kjøres av MIGRATOR-rollen — verken av postgres eller av
# runtime.
#
#   postgres  => tabellene eies av superbrukeren, og ingen andre kan
#                migrere skjemaet ("must be owner of table revisjonslogg").
#   runtime   => runtime kan skru av eller slette append-only-triggerne
#                sine egne. Codex' P1 i PR-004-reviewen.
#
# Runtime får kun DML: SELECT og INSERT. Ingen UPDATE, ingen DELETE, ingen
# TRUNCATE, ingen eierskap — append-only er da ikke bare en trigger, men
# også et fravær av rettigheter.
# ------------------------------------------------------------
cd /opt/disponit || cd "$(dirname "$0")/../.."
set -a; . "$MILJOFIL"; set +a

for base in $DB ${DB}_test; do
  # Flytt eierskap til migrator (reparerer tidligere installasjoner der
  # objektene ble eid av postgres eller av runtime-rollen).
  # Codex' P1: forrige versjon skrev «ALTER FUNCTION public.navn()» uten
  # argumenttyper. Det er feil for enhver funksjon med parametre, og det
  # ville i tillegg forsøkt å ta eierskap over funksjoner som tilhører en
  # EXTENSION — pgcrypto er allerede tilgjengelig her og trengs til
  # attestasjonssignaturene. Å endre eier på extension-objekter er både
  # unødvendig og skadelig.
  #
  # Nå: `oid::regprocedure` gir full signatur med argumenttyper, kun
  # vanlige funksjoner (prokind='f'), og alt som henger på en extension
  # (pg_depend.deptype='e') er utelatt — for tabeller også.
  sudo -u postgres psql -qtAd "$base" -c \
    "SELECT format('ALTER TABLE %s OWNER TO %I;', c.oid::regclass, '$MIGRATOR')
       FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind IN ('r','p')
        AND pg_get_userbyid(c.relowner) <> '$MIGRATOR'
        AND NOT EXISTS (SELECT 1 FROM pg_depend d
                         WHERE d.classid='pg_class'::regclass
                           AND d.objid=c.oid
                           AND d.refclassid='pg_extension'::regclass
                           AND d.deptype='e')
     UNION ALL
     SELECT format('ALTER FUNCTION %s OWNER TO %I;', p.oid::regprocedure, '$MIGRATOR')
       FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='public' AND p.prokind='f'
        AND pg_get_userbyid(p.proowner) <> '$MIGRATOR'
        AND NOT EXISTS (SELECT 1 FROM pg_depend d
                         WHERE d.classid='pg_proc'::regclass
                           AND d.objid=p.oid
                           AND d.refclassid='pg_extension'::regclass
                           AND d.deptype='e')" \
    | sudo -u postgres psql -q -v ON_ERROR_STOP=1 -d "$base" -f -

  # Selvhelbredelse: den forrige versjonen av dette skriptet rakk å ta
  # eierskap over extension-funksjoner UTEN argumenter (på staging traff den
  # pgcrypto sine `fips_mode()` og `gen_random_uuid()`). Gi dem tilbake til
  # extensionens egen eier, ellers henger skaden igjen på enhver maskin der
  # den gamle versjonen har kjørt.
  sudo -u postgres psql -qtAd "$base" -c \
    "SELECT format('ALTER FUNCTION %s OWNER TO %I;', p.oid::regprocedure,
                   pg_get_userbyid(e.extowner))
       FROM pg_proc p
       JOIN pg_namespace n ON n.oid=p.pronamespace
       JOIN pg_depend d ON d.classid='pg_proc'::regclass
                       AND d.objid=p.oid
                       AND d.refclassid='pg_extension'::regclass
                       AND d.deptype='e'
       JOIN pg_extension e ON e.oid=d.refobjid
      WHERE n.nspname='public'
        AND pg_get_userbyid(p.proowner) <> pg_get_userbyid(e.extowner)" \
    | sudo -u postgres psql -q -v ON_ERROR_STOP=1 -d "$base" -f -
  sudo -u postgres psql -q -d "$base" -c "ALTER SCHEMA public OWNER TO $MIGRATOR"
done

psql "$DISPONIT_MIGRATOR_URL"      -q -v ON_ERROR_STOP=1 \
     -f platform/core/db/migrations/001_init.sql \
     -f platform/core/db/migrations/002_roller_og_tenant_isolasjon.sql
psql "$DISPONIT_TEST_MIGRATOR_DSN" -q -v ON_ERROR_STOP=1 \
     -f platform/core/db/migrations/001_init.sql \
     -f platform/core/db/migrations/002_roller_og_tenant_isolasjon.sql

# Rettigheter til runtime: kun det den trenger, aldri mer.
for base in $DB ${DB}_test; do
  # PG15+: public-skjemaet gir ikke lenger CREATE til alle. Authenticator
  # trenger det for aa kunne eie api_tokener.
  sudo -u postgres psql -q -d "$base" -c "GRANT USAGE, CREATE ON SCHEMA public TO $AUTH"
  sudo -u postgres psql -q -d "$base" <<GRANTS
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM $BRUKER;
GRANT USAGE ON SCHEMA public TO $BRUKER;
GRANT SELECT, INSERT ON revisjonslogg, frekvens_hendelser TO $BRUKER;
GRANT SELECT ON migrasjoner TO $BRUKER;
-- PR-005: runtime faar noeyaktig det den trenger, ikke mer.
-- unntak_historikk er INSERT-only: historikken skal aldri kunne endres.
-- policyer er lesetilgang: policyer endres av en egen vei, ikke av API-et.
GRANT SELECT, INSERT ON unntak_historikk, attestasjon_jti TO $BRUKER;
GRANT SELECT, INSERT, UPDATE ON unntak, idempotens TO $BRUKER;
GRANT SELECT, INSERT, UPDATE ON tenant_nokler TO $BRUKER;
GRANT SELECT ON policyer TO $BRUKER;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO $BRUKER;
GRANTS
done

# Sluttkontroll: alt skal fortsatt virke etter migrasjoner og rettigheter.
verifiser_og_reparer "$BRUKER"   "${RUNTIME_DSN[@]}"
verifiser_og_reparer "$MIGRATOR" "${MIGRATOR_DSN[@]}"

echo "OK. Kilde miljøet med: set -a; . $MILJOFIL; set +a"
echo "Verifiser: python3 -m pytest platform/core/tests -q  (94 forventet)"
