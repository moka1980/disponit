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

# ------------------------------------------------------------
# Miljøfil med hemmeligheter — 0600, aldri i repo (RUTINER/ADR-001).
#
# Skrives PER NØKKEL, ikke som en alt-eller-ingenting-fil. Codex' P1:
# skriptet skrev bare fila når den ikke fantes, så en oppgradering fra en
# eldre installasjon fikk aldri migrator-DSN-ene — og krevde at et menneske
# slettet fila og dermed roterte alle hemmeligheter for hånd. En
# oppgraderingsvei som krever manuell hemmelighetsendring er ingen
# oppgraderingsvei.
#
# Eksisterende verdier røres ikke: passord roteres aldri som bieffekt av at
# skriptet kjøres på nytt.
# ------------------------------------------------------------
mkdir -p /etc/disponit && chmod 700 /etc/disponit
touch "$MILJOFIL" && chmod 600 "$MILJOFIL"

har_nokkel() { grep -q "^$1=" "$MILJOFIL"; }
legg_til()   { printf "%s='%s'\n" "$1" "$2" >> "$MILJOFIL"; }

# Runtime-rollen
if ! har_nokkel DATABASE_URL || ! har_nokkel DISPONIT_TEST_DSN; then
  PASSORD=$(openssl rand -hex 24)
  sudo -u postgres psql -qc "ALTER ROLE $BRUKER PASSWORD '$PASSORD'"
  har_nokkel DATABASE_URL || \
    legg_til DATABASE_URL "host=127.0.0.1 dbname=$DB user=$BRUKER password=$PASSORD"
  har_nokkel DISPONIT_TEST_DSN || \
    legg_til DISPONIT_TEST_DSN "host=127.0.0.1 dbname=${DB}_test user=$BRUKER password=$PASSORD"
fi

# Migrator-rollen — dette er nøkkelen som manglet ved oppgradering
if ! har_nokkel DISPONIT_MIGRATOR_URL || ! har_nokkel DISPONIT_TEST_MIGRATOR_DSN; then
  MIGPASSORD=$(openssl rand -hex 24)
  sudo -u postgres psql -qc "ALTER ROLE $MIGRATOR PASSWORD '$MIGPASSORD'"
  har_nokkel DISPONIT_MIGRATOR_URL || \
    legg_til DISPONIT_MIGRATOR_URL "host=127.0.0.1 dbname=$DB user=$MIGRATOR password=$MIGPASSORD"
  har_nokkel DISPONIT_TEST_MIGRATOR_DSN || \
    legg_til DISPONIT_TEST_MIGRATOR_DSN "host=127.0.0.1 dbname=${DB}_test user=$MIGRATOR password=$MIGPASSORD"
fi

# Attestasjonsnøkler
if ! har_nokkel DISPONIT_ATT_NOKLER; then
  NOKLER='{'
  for v in v_regnskap v_register v_bank v_svindel v_fordring v_dlp v_prisbok; do
    NOKLER="$NOKLER\"$v\":{\"k1\":\"$(openssl rand -hex 32)\"},"
  done
  legg_til DISPONIT_ATT_NOKLER "${NOKLER%,}}"
fi

chmod 600 "$MILJOFIL"

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
                         WHERE d.objid=c.oid AND d.deptype='e')
     UNION ALL
     SELECT format('ALTER FUNCTION %s OWNER TO %I;', p.oid::regprocedure, '$MIGRATOR')
       FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='public' AND p.prokind='f'
        AND pg_get_userbyid(p.proowner) <> '$MIGRATOR'
        AND NOT EXISTS (SELECT 1 FROM pg_depend d
                         WHERE d.objid=p.oid AND d.deptype='e')" \
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
