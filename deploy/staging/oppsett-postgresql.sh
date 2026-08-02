#!/usr/bin/env bash
# ============================================================
# Disponit staging: PostgreSQL-oppsett på Cloud Server S (Ubuntu)
# Kjøres som root av Claude Code. Idempotent.
# ============================================================
set -euo pipefail

DB=disponit
BRUKER=disponit              # RUNTIME — kun DML, eier ingenting
MIGRATOR=disponit_migrator   # eier skjemaet, kjører migrasjoner
MILJOFIL=/etc/disponit/staging.env

# Rolleskillet er Codex' P1 fra PR-004-reviewen: eide runtime-rollen
# tabellene, kunne den skru av eller slette append-only-triggerne selv.
# En vakt du kan fjerne er ingen vakt.

apt-get update -q
apt-get install -y -q postgresql postgresql-contrib
systemctl enable --now postgresql

# Roller + database (idempotent)
for r in "$BRUKER" "$MIGRATOR"; do
  sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$r'" \
    | grep -q 1 || sudo -u postgres psql -c \
    "CREATE ROLE $r LOGIN PASSWORD '$(openssl rand -hex 24)'"
done
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='$DB'" \
  | grep -q 1 || sudo -u postgres createdb -O $MIGRATOR $DB

# Miljøfil med hemmeligheter — 0600, aldri i repo (RUTINER/ADR-001)
mkdir -p /etc/disponit && chmod 700 /etc/disponit
if [ ! -f "$MILJOFIL" ]; then
  PASSORD=$(openssl rand -hex 24)
  MIGPASSORD=$(openssl rand -hex 24)
  sudo -u postgres psql -c "ALTER ROLE $BRUKER PASSWORD '$PASSORD'"
  sudo -u postgres psql -c "ALTER ROLE $MIGRATOR PASSWORD '$MIGPASSORD'"
  # Verdiene MAA vaere i anfoerselstegn: DSN-ene inneholder mellomrom, og
  # `set -a; . fila` tolker da bare foerste ord som verdi. Uten dette blir
  # DISPONIT_TEST_DSN til "host=127.0.0.1" og passordet forsvinner —
  # psycopg feiler med "no password supplied". Funnet ved faktisk kjoering
  # paa Cloud Server S, ikke ved lesing.
  cat > "$MILJOFIL" << MILJO
DATABASE_URL='host=127.0.0.1 dbname=$DB user=$BRUKER password=$PASSORD'
DISPONIT_TEST_DSN='host=127.0.0.1 dbname=${DB}_test user=$BRUKER password=$PASSORD'
DISPONIT_MIGRATOR_URL='host=127.0.0.1 dbname=$DB user=$MIGRATOR password=$MIGPASSORD'
DISPONIT_TEST_MIGRATOR_DSN='host=127.0.0.1 dbname=${DB}_test user=$MIGRATOR password=$MIGPASSORD'
DISPONIT_ATT_NOKLER='{"v_regnskap":{"k1":"$(openssl rand -hex 32)"},"v_register":{"k1":"$(openssl rand -hex 32)"},"v_bank":{"k1":"$(openssl rand -hex 32)"},"v_svindel":{"k1":"$(openssl rand -hex 32)"},"v_fordring":{"k1":"$(openssl rand -hex 32)"},"v_dlp":{"k1":"$(openssl rand -hex 32)"},"v_prisbok":{"k1":"$(openssl rand -hex 32)"}}'
MILJO
  chmod 600 "$MILJOFIL"
fi

# Test-database for staging-kjøringer
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB}_test'" \
  | grep -q 1 || sudo -u postgres createdb -O $MIGRATOR ${DB}_test

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
  sudo -u postgres psql -qtAd "$base" -c \
    "SELECT 'ALTER TABLE public.'||quote_ident(tablename)||' OWNER TO $MIGRATOR;'
       FROM pg_tables WHERE schemaname='public' AND tableowner <> '$MIGRATOR'
     UNION ALL
     SELECT 'ALTER FUNCTION public.'||quote_ident(p.proname)||'() OWNER TO $MIGRATOR;'
       FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='public' AND pg_get_userbyid(p.proowner) <> '$MIGRATOR'" \
    | sudo -u postgres psql -q -d "$base" -f -
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
  sudo -u postgres psql -q -d "$base" <<GRANTS
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM $BRUKER;
GRANT USAGE ON SCHEMA public TO $BRUKER;
GRANT SELECT, INSERT ON revisjonslogg, frekvens_hendelser TO $BRUKER;
GRANT SELECT ON migrasjoner TO $BRUKER;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO $BRUKER;
GRANTS
done

echo "OK. Kilde miljøet med: set -a; . $MILJOFIL; set +a"
echo "Verifiser: python3 -m pytest platform/core/tests -q  (74 forventet)"
