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

# Setter en nøkkel: fjerner en eventuell gammel linje først, så verdien
# ikke finnes i to utgaver.
sett_nokkel() {
  local n="$1" v="$2" tmp
  tmp=$(mktemp) && chmod 600 "$tmp"
  grep -v "^$n=" "$MILJOFIL" > "$tmp" || true
  printf "%s='%s'\n" "$n" "$v" >> "$tmp"
  mv "$tmp" "$MILJOFIL" && chmod 600 "$MILJOFIL"
}

# En rolles DSN-er hører sammen: de deler ETT passord i databasen.
#
# Codex' P1: forrige versjon roterte passordet så snart ÉN av dem manglet,
# men skrev bare den manglende linjen. Søskenlinja beholdt da det gamle
# passordet og sluttet å virke. Verst av alt gjaldt det nøyaktig den
# rotasjonsprosedyren jeg selv hadde skrevet i DEPLOY.md — «slett den ene
# linjen og kjør skriptet» — så dokumentasjonen ledet rett i fella.
#
# Nå: mangler én, skrives ALLE rollens DSN-er på nytt med det nye passordet.
# Mangler ingen, røres ingenting og passordet roteres ikke.
sikre_rolle_dsn() {
  local rolle="$1"; shift          # deretter: NØKKEL=dbnavn NØKKEL=dbnavn ...
  local mangler=0 par n
  for par in "$@"; do
    har_nokkel "${par%%=*}" || mangler=1
  done
  [ "$mangler" -eq 0 ] && return 0

  local pw; pw=$(openssl rand -hex 24)
  sudo -u postgres psql -qc "ALTER ROLE $rolle PASSWORD '$pw'"
  for par in "$@"; do
    n="${par%%=*}"
    sett_nokkel "$n" "host=127.0.0.1 dbname=${par#*=} user=$rolle password=$pw"
  done
  echo "  roterte passord for $rolle og skrev ${#@} DSN-er samlet"
}

sikre_rolle_dsn "$BRUKER"   "DATABASE_URL=$DB"          "DISPONIT_TEST_DSN=${DB}_test"
sikre_rolle_dsn "$MIGRATOR" "DISPONIT_MIGRATOR_URL=$DB" "DISPONIT_TEST_MIGRATOR_DSN=${DB}_test"

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
