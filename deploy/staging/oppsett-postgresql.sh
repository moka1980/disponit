#!/usr/bin/env bash
# ============================================================
# Disponit staging: PostgreSQL-oppsett på Cloud Server S (Ubuntu)
# Kjøres som root av Claude Code. Idempotent.
# ============================================================
set -euo pipefail

DB=disponit
BRUKER=disponit
MILJOFIL=/etc/disponit/staging.env

apt-get update -q
apt-get install -y -q postgresql postgresql-contrib
systemctl enable --now postgresql

# Rolle + database (idempotent)
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$BRUKER'" \
  | grep -q 1 || sudo -u postgres psql -c \
  "CREATE ROLE $BRUKER LOGIN PASSWORD '$(openssl rand -hex 24)'"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='$DB'" \
  | grep -q 1 || sudo -u postgres createdb -O $BRUKER $DB

# Miljøfil med hemmeligheter — 0600, aldri i repo (RUTINER/ADR-001)
mkdir -p /etc/disponit && chmod 700 /etc/disponit
if [ ! -f "$MILJOFIL" ]; then
  PASSORD=$(openssl rand -hex 24)
  sudo -u postgres psql -c "ALTER ROLE $BRUKER PASSWORD '$PASSORD'"
  cat > "$MILJOFIL" << MILJO
DATABASE_URL=host=127.0.0.1 dbname=$DB user=$BRUKER password=$PASSORD
DISPONIT_TEST_DSN=host=127.0.0.1 dbname=${DB}_test user=$BRUKER password=$PASSORD
DISPONIT_ATT_NOKLER={"v_regnskap":{"k1":"$(openssl rand -hex 32)"},"v_register":{"k1":"$(openssl rand -hex 32)"},"v_bank":{"k1":"$(openssl rand -hex 32)"},"v_svindel":{"k1":"$(openssl rand -hex 32)"},"v_fordring":{"k1":"$(openssl rand -hex 32)"},"v_dlp":{"k1":"$(openssl rand -hex 32)"},"v_prisbok":{"k1":"$(openssl rand -hex 32)"}}
MILJO
  chmod 600 "$MILJOFIL"
fi

# Test-database for staging-kjøringer
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB}_test'" \
  | grep -q 1 || sudo -u postgres createdb -O $BRUKER ${DB}_test

# Kjør migrasjoner mot begge databaser
cd /opt/disponit || cd "$(dirname "$0")/../.."
for base in $DB ${DB}_test; do
  sudo -u postgres psql -d $base -f platform/core/db/migrations/001_init.sql
done

echo "OK. Kilde miljøet med: set -a; . $MILJOFIL; set +a"
echo "Verifiser: python3 -m pytest platform/core/tests -q  (74 forventet)"
