#!/usr/bin/env bash
# ============================================================
# Disponit opp.sh — automatisert utrulling (PR-009 v2 §4 + v3 §3 + V1).
#
# Én kommando: fra «ingenting kjører» eller «forrige versjon kjører» til
# «API (socket-aktivert) og M-37 kjører, overvåkes og restartes». Kjøres
# som root, UTEN TTY-krav (v5 §3) — utsteder ALDRI tokens; det gjør
# bootstrap-token.sh, interaktivt.
#
# FORWARD-ONLY (V1): migrasjonene har ingen nedvei, og 009 dropper
# `aktiv` — forrige applikasjonsversjon KAN IKKE startes mot nytt schema.
# Sekvensen er derfor vedlikeholdsvindu: stopp tjenester → migrér →
# aktiver ny release → start → verifiser. Rapporten til slutt sier
# eksplisitt hva som IKKE kan rulles tilbake, i stedet for å love en
# rollback som ikke finnes.
# ============================================================
set -euo pipefail

ROT=/opt/disponit
MILJOFIL=/etc/disponit/staging.env
LAAS=/var/lock/disponit-deploy.lock

# --- Deploylås: fail-fast, aldri kø (v2 §4) --------------------------------
exec 9>"$LAAS"
if ! flock -n 9; then
  echo "AVBRUTT: en annen utrulling holder $LAAS" >&2
  exit 1
fi

SHA=$(git -C "$ROT" rev-parse HEAD)
KILDE="$ROT/releases/$SHA"
AKTIV="$ROT/aktiv"
FORRIGE=$(readlink -f "$AKTIV" 2>/dev/null || true)

echo "== opp.sh: utrulling av $SHA =="

# --- 1. Release-katalogen STAGES (inert — endrer ingen levende sti) --------
mkdir -p "$ROT/releases"
if [ ! -d "$KILDE" ]; then
  git -C "$ROT" archive --format=tar "$SHA" | \
    ( mkdir -p "$KILDE" && tar -x -C "$KILDE" )
fi

# --- 2. PREFLIGHT — SIDEEFFEKTFRI, FØR FØRSTE AKTIVE MUTASJON (P1 rd. 2) ---
# Verifiseringen skjer i en temporær falsk rot (verify --root) med
# KANDIDATENS units og skript: /usr/local/lib røres ikke, ingen bruker
# opprettes, ingen credential skrives, ingenting stoppes. Feiler gaten,
# er systemet BEVISELIG uendret — en gammel timer kan aldri ha sett
# kandidatkode.
. "$KILDE/deploy/staging/lib-opp.sh"
UNITS="disponit-api.socket disponit-api.service disponit-m37.service
disponit-helse.service disponit-helse.timer
disponit-rydd-pending.service disponit-rydd-pending.timer
disponit-backup.service disponit-backup.timer
disponit-domenerevalidering.service disponit-domenerevalidering.timer
disponit-artefaktrydding.service disponit-artefaktrydding.timer"
if ! preflight_units "$KILDE" "$ROT/.venv" $UNITS; then
  echo "AVBRUTT: preflight feilet — systemet er urørt; forrige release"
  echo "kjører som før."
  exit 1
fi
# PR-015: driftstimerne over kaller funksjoner som migrasjon 019 kun granter
# til `disponit_domains_admin` og `disponit_domener`. En EKSISTERENDE
# installasjon har ingen DISPONIT_DOMAINS_URL før `oppsett-postgresql.sh` er
# kjørt på nytt; en stille fallback til runtime-DSN-en (`disponit`) ville
# startet begge timerne rett i `permission denied` og latt revalidering og
# rydding stå ute av drift uten at utrullingen sa fra. Gaten hører derfor
# hjemme her, FØR første mutasjon: feiler den, er systemet beviselig urørt.
# Lesingen skjer i en subshell, så miljøfilen ikke lekker inn i preflighten.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_DOMAINS_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_DOMAINS_URL mangler i $MILJOFIL."
  echo "Driftstimerne (disponit-domenerevalidering, disponit-artefaktrydding)"
  echo "ville da fått runtime-DSN-en, som migrasjon 019 ikke granter"
  echo "revaliderings- eller ryddefunksjonene til — begge timerne ville"
  echo "startet i 'permission denied'. Kjør deploy/staging/oppsett-postgresql.sh"
  echo "på nytt (den oppretter rollen disponit_domener og skriver DSN-en),"
  echo "og kjør så opp.sh igjen."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi

# ============================================================
# HERFRA MUTERES SYSTEMET — gaten over er passert.
# ============================================================

# --- 3. Unix-identiteter (idempotent; v2 §3 + PR-009b V1) ------------------
getent group disponit-proxy >/dev/null || groupadd --system disponit-proxy
# PR-015: driftstimerne (revalidering + rydding) får sin EGEN Unix-bruker,
# samme skille som API/M-37/helse — et kompromittert timerkjøring skal ikke
# arve noen annen tjenestes fullmakter, og omvendt.
for b in disponit-api disponit-m37 disponit-helse disponit-domener; do
  getent passwd "$b" >/dev/null || \
    useradd --system --no-create-home --shell /usr/sbin/nologin "$b"
done
# nginx-brukeren meldes inn i disponit-proxy av PR-009b — ALDRI her, og
# aldri M-37: gruppen ER tillitsgrensen. Helsesjekkeren er medlem som
# TILSYNSKLIENT (/live over socketen) — en bevisst, synlig utvidelse,
# dokumentert i DEPLOY.md og flagget i PR-beskrivelsen.
usermod -aG disponit-proxy disponit-helse

# --- 4. Credentials per tjeneste (v3 §5): root-eide filer ------------------
# Kilden er staging.env (lib-miljofil eier livssyklusen); her MATERIALISERES
# de per unit, slik at LoadCredential gir hver prosess kun sine egne.
set -a; . "$MILJOFIL"; set +a
install -d -m 700 /etc/disponit/api /etc/disponit/m37
skriv_cred() {  # katalog navn verdi
  printf '%s' "$3" > "/etc/disponit/$1/$2"
  chmod 600 "/etc/disponit/$1/$2"
}
skriv_cred api DATABASE_URL          "$DATABASE_URL"
skriv_cred api DISPONIT_KEK          "$DISPONIT_KEK"
skriv_cred api DISPONIT_TOKEN_PEPPER "$DISPONIT_TOKEN_PEPPER"
skriv_cred api DISPONIT_ATT_NOKLER   "$DISPONIT_ATT_NOKLER"
skriv_cred api DISPONIT_MAC_NOKLER   "$DISPONIT_MAC_NOKLER"   # PR-012 (boot-perre)
# PR-013 (V8/port 13): fest miljøsignaturen (tzdata) RELEASEN bygges med, målt
# med releasens EGEN kode. Bytter vertens tzdata etterpå, avviker boot-sjekken
# og prosessen nekter start — motoren tolker aldri tidsvinduer annerledes enn
# klassifikatoren beviste, uoppdaget.
skriv_cred api DISPONIT_SEMANTIKK_MILJO "$(PYTHONPATH="$KILDE/platform/core" \
    "$ROT/.venv/bin/python" -c 'from policy_validator import semantikk; print(semantikk.miljosignatur())')"
# PR-011b: UI-deploy-config (provider-valg + IdP-origins). Ikke hemmeligheter,
# men hydreres til os.environ via samme LoadCredential-vei. Tomme = default.
skriv_cred api DISPONIT_UI_PROVIDER    "${DISPONIT_UI_PROVIDER:-}"
skriv_cred api DISPONIT_UI_IDP_ORIGINS "${DISPONIT_UI_IDP_ORIGINS:-}"
# Arbeideren får sin EGEN DB-rolle (v2 §3) når DISPONIT_ARBEIDER_URL er
# satt av oppsett-postgresql.sh; ellers deler den runtime-DSN-en og det
# rapporteres som avvik nederst.
skriv_cred m37 DATABASE_URL "${DISPONIT_ARBEIDER_URL:-$DATABASE_URL}"
skriv_cred m37 DISPONIT_KEK "$DISPONIT_KEK"
install -d -m 700 /etc/disponit/tokenadmin
skriv_cred tokenadmin DISPONIT_TOKEN_ADMIN_URL "$DISPONIT_TOKEN_ADMIN_URL"
skriv_cred tokenadmin DISPONIT_TOKEN_PEPPER    "$DISPONIT_TOKEN_PEPPER"
# PR-015: driftstimerne — egen rolle (disponit_domener, migrasjon 019),
# ALDRI disponit_domains_admin (den bærer direkte EXECUTE på
# avgjor_domeneovertakelse og ville omgått fire øyne, jf. F16-notatet i
# migrasjonen). DISPONIT_RESOLVERE er ikke en hemmelighet — server-
# adressene for DNS-oppslag — men går gjennom samme LoadCredential-vei
# som resten (v3 §5); tom streng gir en tydelig oppstart-nektet-feil i
# stedet for stille å hoppe over diversitetskravet.
install -d -m 700 /etc/disponit/domener
# INGEN fallback til $DATABASE_URL: den DSN-en har ingen av grantene 019
# gir, så en fallback ville bare gjort en manglende rolle til to timere som
# feiler i drift. Gaten i §2 har alt avbrutt utrullingen hvis den mangler.
skriv_cred domener DISPONIT_DOMAINS_URL "$DISPONIT_DOMAINS_URL"
skriv_cred domener DISPONIT_RESOLVERE   "${DISPONIT_RESOLVERE:-}"

# --- 5. VEDLIKEHOLDSVINDU: stopp tjenester OG helsetimer (V1) --------------
# Timeren stoppes også: den skal verken telle feil mot stoppede tjenester
# eller utløse en restart midt i migrasjonsvinduet.
systemctl stop disponit-helse.timer disponit-m37.service \
    disponit-api.service disponit-api.socket 2>/dev/null || true

# --- 6. Migrasjoner (begge baser) — FØR ny release aktiveres ---------------
# P1 runde 1: hver base melder sitt til rapporten. Første utgave lot siste
# iterasjon (TESTbasen) overskrive resultatet — en forward-only-migrasjon
# kjørt kun i runtime-basen ville blitt rapportert rollback-kompatibel.
RAPPORT_KATALOG=$(mktemp -d)
trap 'rm -rf "$RAPPORT_KATALOG"' EXIT
for par in "runtime:$DISPONIT_MIGRATOR_URL" "test:$DISPONIT_TEST_MIGRATOR_DSN"; do
  base=${par%%:*}; url=${par#*:}
  (cd "$KILDE" && DISPONIT_MIGRATOR_URL="$url" \
     "$ROT/.venv/bin/python" deploy/staging/migrer.py disponit) \
     | tee "$RAPPORT_KATALOG/$base" || {
    echo "AVBRUTT: migrasjon ($base) feilet — tjenestene er STOPPET og"
    echo "forrige release står urørt på $FORRIGE. Fremoverrettet retting."
    exit 1
  }
done

# --- 7. Atomisk release-bytte + units --------------------------------------
ln -sfn "$KILDE" "$AKTIV"
for u in $UNITS; do
  install -m 644 "$KILDE/deploy/staging/$u" "/etc/systemd/system/$u"
done
# Hjelperskriptene installeres HER — i mutasjonsfasen, etter gaten og
# mens tjenestene og helsetimeren er stoppet. Preflighten verifiserte
# nøyaktig disse filene i den falske roten; ingen gammel timer kan ha
# kjørt kandidatkode før dette punktet.
install -m 755 "$KILDE/deploy/staging/helse-sjekk.sh" \
    /usr/local/lib/disponit-helse-sjekk
install -m 755 "$KILDE/deploy/staging/restart-helper.sh" \
    /usr/local/lib/disponit-restart-helper
install -m 440 "$KILDE/deploy/staging/sudoers-disponit-helse" \
    /etc/sudoers.d/disponit-helse
visudo -cf /etc/sudoers.d/disponit-helse >/dev/null
# journald: IKKE noe globalt tak. Klarsignalet forutsatte dedikert vert,
# men DEPLOY.md sier eksplisitt at Cloud Server S deler maskin med et
# annet produkt — og v2 §7s egen regel for delt vert er per-unit
# LogRateLimit (satt i unit-filene). Global SystemMaxUse ville skrevet om
# naboproduktets loggretensjon. Flagget som avvik i PR-beskrivelsen.
systemctl daemon-reload

# --- 8. Start + readiness over SOCKETEN (PR-009b §0: ingen TCP) ------------
systemctl enable --now disponit-api.socket
systemctl enable --now disponit-api.service disponit-m37.service
systemctl enable --now disponit-helse.timer disponit-rydd-pending.timer \
    disponit-backup.timer
# PR-015: revalidering (timeplan i .timer) + rydding (hvert 15. min). Begge
# er Type=oneshot bak en .timer — enable --now på TIMEREN, ikke tjenesten.
systemctl enable --now disponit-domenerevalidering.timer \
    disponit-artefaktrydding.timer

KLAR=nei
for _ in $(seq 1 30); do
  if curl -fsS --unix-socket /run/disponit/api.sock \
       http://disponit/ready >/dev/null 2>&1; then KLAR=ja; break; fi
  sleep 1
done

API=$(systemctl is-active disponit-api.service || true)
M37=$(systemctl is-active disponit-m37.service || true)

# --- 9. TREDELT STATUSRAPPORT (v3 §3) — ærlig, aldri en lovet rollback -----
echo
echo "== statusrapport =="
echo "(a) schema, per base:"
vurder_migrasjoner runtime "$RAPPORT_KATALOG/runtime" \
                   test    "$RAPPORT_KATALOG/test"
echo "(b) kandidat:  api=$API m37=$M37 /ready=$KLAR (release $SHA)"
if [ -z "$NYE_MIGRASJONER" ]; then
  echo "(c) rollback:  ingen nye migrasjoner i NOEN base — forrige kode"
  echo "               ($FORRIGE) er fortsatt kompatibel med skjemaet."
else
  echo "(c) rollback:  FORBUDT. Nye migrasjoner [$NYE_MIGRASJONER] er"
  echo "               forward-only; forrige kode kan IKKE startes mot"
  echo "               dette skjemaet. Dommen felles over UNIONEN av"
  echo "               basene — én base er nok. Feil rettes FREMOVER."
fi
if [ -z "${DISPONIT_ARBEIDER_URL:-}" ]; then
  echo "AVVIK: DISPONIT_ARBEIDER_URL er ikke satt — arbeideren deler"
  echo "       runtime-DSN. Kjør oppsett-postgresql.sh for rolleskillet."
fi

[ "$API" = active ] && [ "$M37" = active ] && [ "$KLAR" = ja ]
