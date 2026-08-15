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
TOKENADMIN=disponit_token_admin  # administrerer tokens, eier ingenting
M37=disponit_m37_claimer     # PR-006: eier arbeidskapabiliteter + claim-funksjonene
POLICYEIER=disponit_policy_eier  # PR-013: eier den herdede aktiver_policy-funksjonen
MODULEIER=disponit_modul_eier    # PR-014a: eier modulregisterets overgangsfunksjoner
MODULESADMIN=disponit_modules_admin  # PR-014a: EXECUTE på overgangsfunksjonene
EGRESS=disponit_egress           # PR-014b: egress-proxyens rolle, SELECT kun paa visningen
DOMENEEIER=disponit_domene_eier  # PR-014b: eier domene/artefakt-funksjonene (BYPASSRLS: takeover er kryss-tenant)
DOMAINSADMIN=disponit_domains_admin  # PR-014b: EXECUTE paa domenefunksjonene
# PR-015 (Codex P1): EGEN, minst-privilegert rolle for driftstimerne
# (revalidering + rydding). $DOMAINSADMIN baerer OGSAA direkte EXECUTE paa
# avgjor_domeneovertakelse (016) — en holder av DEN credentialen kunne kalt
# adjudikasjonen med p_godkjent=true og ÉN aktoer, og dermed omgaatt fire
# oeyne helt. Timerne faar derfor sin egen LOGIN-rolle, granted KUN
# revalideringsfunksjonene og rydd_staged_artefakter(int) i migrasjon 019 —
# aldri avgjor_domeneovertakelse, aldri verifiser_domenekontroll.
DOMENER=disponit_domener
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
# PR-009 v2 §3: arbeideren har EGEN DB-rolle — et kompromittert API har
# ikke arbeiderens fullmakter, og omvendt. Rollen får runtime-grantsettet
# MINUS verifiser_token (API-autentisering er ikke arbeiderens jobb);
# skillet settes i migrer.py.
ARBEIDER=disponit_arbeider
for r in "$BRUKER" "$MIGRATOR" "$TOKENADMIN" "$ARBEIDER" "$EGRESS" "$DOMENER"; do
  sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$r'" \
    | grep -q 1 || sudo -u postgres psql -c \
    "CREATE ROLE $r LOGIN PASSWORD '$(openssl rand -hex 24)'"
done
for r in "$AUTH" "$M37" "$POLICYEIER" "$MODULEIER" "$MODULESADMIN" "$DOMAINSADMIN"; do
  sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$r'" \
    | grep -q 1 || sudo -u postgres psql -qc "CREATE ROLE $r NOLOGIN"
done
# domene_eier eier de kryss-tenant takeover-funksjonene → BYPASSRLS (den maa se
# andre tenanters domenekontroll-rader via hostname_binding-autoriteten).
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$DOMENEEIER'" \
  | grep -q 1 || sudo -u postgres psql -qc "CREATE ROLE $DOMENEEIER NOLOGIN BYPASSRLS"
# Migrator maa vaere MEDLEM av begge for aa kunne sette eierskap (OWNER TO)
# paa api_tokener (003) og paa arbeidskapabiliteter + M-37-funksjonene (005).
sudo -u postgres psql -qc "GRANT $AUTH TO $MIGRATOR"
# WITH INHERIT FALSE er ikke pynt. Migrator maa vaere MEDLEM for aa kunne
# gjoere OWNER TO, men et vanlig medlemskap gir ogsaa ARVEDE rettigheter —
# og RLS-policyer med TO-klausul matcher paa arvet medlemskap. Uten dette
# arvet migrator M-37-dispatcherens policy paa revisjonslogg og unntak, og
# saa igjen alle tenanters rader. Det er Codex' P1 nr. 2 fra PR-004 paa
# nytt, gjeninnfoert av en GRANT som saa ut som en formalitet.
# (PostgreSQL 16+. SET ROLE er fortsatt mulig for migrator — det er en
# eksplisitt, sporbar handling, ikke stille arv.)
sudo -u postgres psql -qc "GRANT $M37 TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $POLICYEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $MODULEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $MODULESADMIN TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $DOMENEEIER TO $MIGRATOR WITH INHERIT FALSE"
sudo -u postgres psql -qc "GRANT $DOMAINSADMIN TO $MIGRATOR WITH INHERIT FALSE"

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
TOKENADMIN_DSN=("DISPONIT_TOKEN_ADMIN_URL=$DB"
                "DISPONIT_TEST_TOKEN_ADMIN_DSN=${DB}_test")
ARBEIDER_DSN=("DISPONIT_ARBEIDER_URL=$DB"
              "DISPONIT_TEST_ARBEIDER_DSN=${DB}_test")
# PR-014b (Codex P2): egress-proxyen har egen LOGIN-rolle med tilfeldig passord,
# men uten en DSN i miljøfilen kunne den ikke autentisere på en fersk install
# (manuell passord-reset kreves ellers). Samme state-machine som de andre rollene.
EGRESS_DSN=("DISPONIT_EGRESS_URL=$DB" "DISPONIT_TEST_EGRESS_DSN=${DB}_test")
# PR-015: driftstimerne (revalidering + rydding) leser DISPONIT_DOMAINS_URL —
# samme state-machine som de andre rollene, ellers kan ikke $DOMENER
# autentisere paa en fersk install.
DOMENER_DSN=("DISPONIT_DOMAINS_URL=$DB" "DISPONIT_TEST_DOMAINS_DSN=${DB}_test")

sikre_rolle_dsn "$BRUKER"     "${RUNTIME_DSN[@]}"
sikre_rolle_dsn "$MIGRATOR"   "${MIGRATOR_DSN[@]}"
sikre_rolle_dsn "$TOKENADMIN" "${TOKENADMIN_DSN[@]}"
sikre_rolle_dsn "$ARBEIDER"   "${ARBEIDER_DSN[@]}"
sikre_rolle_dsn "$EGRESS"     "${EGRESS_DSN[@]}"
sikre_rolle_dsn "$DOMENER"    "${DOMENER_DSN[@]}"
sikre_attestasjonsnokler
sikre_mac_nokler          # PR-012: MAC-register (oppstartsperre for API-et)
# KEK og token-pepper (PR-005b). KEK manglet helt etter PR-005a: krypteringen
# ble innfoert, men ingen deploy-vei satte noekkelen — API-et nekter aa starte
# uten den, og det er slik feilen skal oppdages, ikke i foerste unntaksrad.
sikre_hex_hemmelighet DISPONIT_KEK 32
sikre_hex_hemmelighet DISPONIT_TOKEN_PEPPER 32

# Sannhetsprøve FØR noen DSN tas i bruk.
#
# Codex' P1: denne sto tidligere bare til slutt. Var forrige kjøring avbrutt
# mellom passordrotasjon og filskriving, pekte migrator-DSN-en på et passord
# rollen ikke lenger hadde — migrasjonen feilet, `set -e` avsluttet skriptet,
# og reparasjonen ble aldri nådd. Reparasjonen var altså utilgjengelig
# nøyaktig i den tilstanden den fantes for. Den må kjøre før første bruk.
verifiser_og_reparer "$BRUKER"     "${RUNTIME_DSN[@]}"
verifiser_og_reparer "$MIGRATOR"   "${MIGRATOR_DSN[@]}"
verifiser_og_reparer "$TOKENADMIN" "${TOKENADMIN_DSN[@]}"
verifiser_og_reparer "$ARBEIDER"   "${ARBEIDER_DSN[@]}"
verifiser_og_reparer "$EGRESS"     "${EGRESS_DSN[@]}"
verifiser_og_reparer "$DOMENER"    "${DOMENER_DSN[@]}"

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
  # PR-008-staging fant NESTE hull i den gamle reparasjonen: den flyttet
  # OGSÅ objektene migrasjonene BEVISST eier med andre roller, og på en
  # allerede migrert base flatet en re-kjøring rollemodellen ut. Første
  # fiks allowlistet ROLLENE — Codex' P1: da bevares også FEILPLASSERTE
  # ordinære objekter hos de privilegerte rollene, og manglende
  # designobjekter (staging manglet fem claimer-funksjoner) synes aldri.
  #
  # Reparasjonen er nå OBJEKTSPESIFIKK og bor i eierskap-reparasjon.sql:
  # designtabellen der lister nøyaktig hva authenticator og m37_claimer
  # skal eie (speilet fra 003/004/005/007), alt annet går til migrator,
  # og en sluttkontroll feiler hardt hvis noe står utenfor modellen.
  # Testene i platform/core/tests/test_eierskap.py kjører NØYAKTIG samme
  # fil begge veier (flatet designobjekt -> designeier, feilplassert
  # objekt hos privilegert rolle -> migrator).
  sudo -u postgres psql -q -v ON_ERROR_STOP=1 -d "$base" \
    -f "$(dirname "$0")/eierskap-reparasjon.sql"

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
  # FERSK-SERVER-FUNN (disponit.com-maskinen): PostgreSQL ≥ 15 gir ingen
  # CREATE på public til andre enn skjemaeieren. 003/005/007 oppretter
  # objekter UNDER SET ROLE authenticator/m37_claimer — de rollene må ha
  # CREATE, ellers dør første migrasjonskjøring med «permission denied for
  # schema public». Gamle staging hadde grantene fra en manuell æra;
  # førstegangsveien hadde aldri satt dem selv.
  sudo -u postgres psql -q -d "$base" -c \
    "GRANT USAGE, CREATE ON SCHEMA public TO $AUTH, $M37, $POLICYEIER, $MODULEIER, $DOMENEEIER"
done

# ------------------------------------------------------------
# Migrasjoner OG rettigheter kjøres av den herdede kjøreren, ikke av psql.
#
# Codex' P1: forrige versjon kjørte filene med `psql -f`. Det omgår
# advisory-låsen, transaksjonen kjøreren eier fra versjon 3, checksum-
# registreringen og avvisningen av endret historikk. En herdet kjører som
# omgås av sitt eget oppsettskript er ikke en kjører, den er en anbefaling.
#
# Rettighetene settes av samme skript, ETTER migrasjonene: en GRANT på en
# tabell som ikke finnes ennå er stille virkningsløs (det var feil nr. 6).
# ------------------------------------------------------------
VENV="/opt/disponit/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
fi
# PR-010: Authlib eier OIDC-protokollmekanikken (authorization code + PKCE,
# JWS/ID-token-validering, JWKS-rotasjon) — Disponit skriver ingen egen
# JWT-parser (v6 §3). joserfc er Authlibs JOSE-backend. Pinnes med hash i
# et lockfil som egen driftsoppgave; her installeres de i venv-en.
"$VENV/bin/pip" install -q "psycopg[binary]" cryptography pyyaml jsonschema pytest \
  starlette uvicorn httpx "authlib>=1.6,<2" joserfc

for _dsn in "$DISPONIT_MIGRATOR_URL" "$DISPONIT_TEST_MIGRATOR_DSN"; do
  DISPONIT_MIGRATOR_URL="$_dsn" "$VENV/bin/python" \
    "$(dirname "$0")/migrer.py" "$BRUKER"
done

# ------------------------------------------------------------
# GO-vilkaar V3, siste lag: migrator kan ikke DEAKTIVERE retention-vakten.
#
# BEFORE DELETE-triggeren i migrasjon 005 binder ENHVER rolle som gjoer
# DELETE — ogsaa migrator og skjemaeieren. Det finnes noeyaktig to veier
# forbi en radtrigger i PostgreSQL:
#
#   1. ALTER TABLE ... DISABLE TRIGGER   (kun eier — altsaa migrator)
#   2. session_replication_role='replica' (kun superbruker)
#
# Vei 2 krever en superbruker ingen tjeneste har. Vei 1 lukkes her, med en
# EVENT TRIGGER: den kan bare opprettes av superbrukeren, og migrator kan
# derfor verken fjerne den eller endre den. Den paastaar ingenting om
# syntaks — den sjekker TILSTANDEN etter enhver ALTER TABLE, saa den kan
# ikke omgaas ved aa formulere kommandoen annerledes.
#
# Residual, sagt rett ut: en bar `DROP TRIGGER policy_retention` fanges
# ikke her (da finnes ikke triggeren aa sjekke, og migrasjon 005 dropper og
# gjenoppretter den selv paa hver kjoering). Den veien fanges av
# sluttkontrollen i migrer.py, som nekter aa fullfoere et deploy uten en
# aktiv retention-vakt.
# ------------------------------------------------------------
for base in $DB ${DB}_test; do
  sudo -u postgres psql -q -v ON_ERROR_STOP=1 -d "$base" <<'SQL'
CREATE OR REPLACE FUNCTION disponit_vern_policy_retention()
RETURNS event_trigger LANGUAGE plpgsql AS $$
DECLARE v_status "char";
BEGIN
    IF to_regclass('public.policyer') IS NULL THEN
        RETURN;                      -- tabellen finnes ikke ennaa
    END IF;
    SELECT t.tgenabled INTO v_status
      FROM pg_trigger t
     WHERE t.tgrelid = 'public.policyer'::regclass
       AND t.tgname = 'policy_retention';
    IF FOUND AND v_status = 'D' THEN
        RAISE EXCEPTION
            'policy_retention er deaktivert — GO-vilkaar V3 forbyr det, ogsaa for migratorrollen. Bruk arkiver_policyversjon().';
    END IF;
END $$;
DROP EVENT TRIGGER IF EXISTS disponit_policy_retention_vern;
CREATE EVENT TRIGGER disponit_policy_retention_vern
    ON ddl_command_end WHEN TAG IN ('ALTER TABLE')
    EXECUTE FUNCTION disponit_vern_policy_retention();
SQL
done

# Sluttkontroll: alt skal fortsatt virke etter migrasjoner og rettigheter.
verifiser_og_reparer "$BRUKER"     "${RUNTIME_DSN[@]}"
verifiser_og_reparer "$MIGRATOR"   "${MIGRATOR_DSN[@]}"
verifiser_og_reparer "$TOKENADMIN" "${TOKENADMIN_DSN[@]}"
verifiser_og_reparer "$EGRESS"     "${EGRESS_DSN[@]}"
verifiser_og_reparer "$DOMENER"    "${DOMENER_DSN[@]}"

echo "OK. Kilde miljøet med: set -a; . $MILJOFIL; set +a"
echo "Verifiser: python3 -m pytest platform/core/tests -q"
