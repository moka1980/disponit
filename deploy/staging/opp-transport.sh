#!/usr/bin/env bash
# ============================================================
# opp-transport.sh — nginx + TLS/ACME foran API-socketen (PR-009b).
#
# ACME-TILSTANDSMASKIN (v2 §3): en konfig kan ikke peke på et sertifikat
# som ikke finnes. Rekkefølgen er bindende:
#   1. HTTP-konfig ALENE (ACME-path + 421-default) → nginx -t → reload
#   2. hent første sertifikat (certbot webroot)
#   3. legg til HTTPS-konfig → nginx -t → reload
#   4. ekstern HTTPS-probe (fra utsiden) — deploy er ikke grønn før den er
#
# Idempotent: har verten alt et sertifikat, hoppes steg 2 (ingen unødig
# Let's Encrypt-kall — de har ukesrate). `--dry-run` på fornyelsen er
# staging-/timerverifikasjon, ALDRI en nettverksavhengig CI-port.
#
# Kjøres som root. HOST fra $DISPONIT_HOST (default disponit.com).
# ============================================================
set -euo pipefail

HOST="${DISPONIT_HOST:-disponit.com}"
ACME_EPOST="${DISPONIT_ACME_EPOST:-eliassi@gmail.com}"
ROT=/opt/disponit
# Filene tas fra DENNE scriptets eget tre — ikke fra `aktiv`-symlinken,
# som kan peke på en eldre release uten transport-malene. Fornyelses-hooken
# refereres med absolutt sti under samme tre.
HER=$(cd "$(dirname "$0")" && pwd)
KILDE=$(cd "$HER/../.." && pwd)
MAL="$HER/nginx"
LAAS=/var/lock/disponit-transport.lock

exec 9>"$LAAS"
flock -n 9 || { echo "AVBRUTT: en annen transport-utrulling kjører" >&2; exit 1; }

echo "== opp-transport.sh: $HOST =="

# --- 0. Pakker + ACME-webroot ---------------------------------------------
apt-get install -y -q nginx certbot >/dev/null

# Tillitsgrensen (PR-009b §0 / V1): nginx-brukeren inn i disponit-proxy —
# ellers kan den ikke koble til /run/disponit/api.sock. ACL-porten (V2)
# måler BEGGE veier: nginx fullfører en hel request; M-37 får EACCES.
# nginx-brukeren: www-data på Debian/Ubuntu, ellers nginx. Utledes fra
# hvilken bruker som faktisk finnes — ikke gjettet.
if getent passwd www-data >/dev/null; then
  NGINX_BRUKER=www-data
elif getent passwd nginx >/dev/null; then
  NGINX_BRUKER=nginx
else
  echo "AVBRUTT: fant verken www-data eller nginx som bruker" >&2
  exit 1
fi
getent group disponit-proxy >/dev/null || groupadd --system disponit-proxy
usermod -aG disponit-proxy "$NGINX_BRUKER"

install -d -o "$NGINX_BRUKER" -g "$NGINX_BRUKER" -m 0755 /var/www/acme

# Fornyelses-hooken må være kjørbar — certbot validerer det FØR utstedelse.
# En fersk git-checkout kan mangle x-biten avhengig av core.filemode.
chmod +x "$KILDE/deploy/staging/nginx-fornyelse-hook.sh"

# --- Rate-soner + log_format i http-kontekst (conf.d, FØR site) -----------
install -m 644 "$MAL/rate-soner.conf" /etc/nginx/conf.d/disponit-rate.conf

rendr() {  # <template> <mål>
  sed "s/\${DISPONIT_HOST}/$HOST/g" "$1" > "$2"
}

# --- 1. HTTP-konfig ALENE → validér → reload ------------------------------
install -d /etc/nginx/sites-available /etc/nginx/sites-enabled
rendr "$MAL/disponit-http.conf.template" /etc/nginx/sites-available/disponit.conf
ln -sfn /etc/nginx/sites-available/disponit.conf \
    /etc/nginx/sites-enabled/disponit.conf
# Distroens default-site kan eie port 80s default_server — fjern den.
rm -f /etc/nginx/sites-enabled/default
if ! nginx -t; then
  echo "AVBRUTT: HTTP-konfig validerte ikke — ingen reload." >&2
  exit 1
fi
systemctl enable --now nginx
systemctl reload nginx

# --- 2. Første sertifikat (idempotent) ------------------------------------
if [ ! -d "/etc/letsencrypt/live/$HOST" ]; then
  echo "-- henter sertifikat for $HOST via webroot --"
  certbot certonly --webroot -w /var/www/acme -d "$HOST" \
    --non-interactive --agree-tos --email "$ACME_EPOST" \
    --deploy-hook "$KILDE/deploy/staging/nginx-fornyelse-hook.sh"
else
  echo "-- sertifikat finnes alt for $HOST — hopper over utstedelse --"
fi

# --- 3. HTTPS-konfig i tillegg → validér → reload -------------------------
{
  rendr "$MAL/disponit-http.conf.template" /dev/stdout
  echo
  rendr "$MAL/disponit-https.conf.template" /dev/stdout
} > /etc/nginx/sites-available/disponit.conf
if ! nginx -t; then
  echo "AVBRUTT: HTTPS-konfig validerte ikke — beholder forrige (HTTP)." >&2
  # Gjenopprett ren HTTP-konfig så tjenesten ikke står med en halv config.
  rendr "$MAL/disponit-http.conf.template" \
      /etc/nginx/sites-available/disponit.conf
  nginx -t && systemctl reload nginx
  exit 1
fi
systemctl reload nginx

# --- 4. TLS-flagget: settes av den som FAKTISK leverer TLS (v1 §1) --------
# Merk: API-et lytter på Unix-socket (strengere enn loopback), så flagget
# er ikke lenger last-bærende for binding — men PR-005b-porten og
# eventuelle fremtidige TCP-veier leser det, så den som leverer TLS eier
# det. Skrives til api-credential-katalogen dersom den finnes.
if [ -d /etc/disponit/api ]; then
  printf '1' > /etc/disponit/api/DISPONIT_TLS_AKTIV
  chmod 600 /etc/disponit/api/DISPONIT_TLS_AKTIV
fi

# --- 5. EKSTERN HTTPS-probe (v2 §3.6) — fra utsiden, ikke localhost --------
# Løser navnet mot den offentlige IP-en og krever 2xx/3xx/4xx (et svar,
# ikke en tilkoblingsfeil). En probe mot localhost ville ikke bevist at
# den eksterne veien virker.
echo "-- ekstern HTTPS-probe --"
KODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
       "https://$HOST/live" || echo 000)
if [ "$KODE" = 000 ]; then
  echo "AVBRUTT: ekstern HTTPS-probe fikk ingen respons ($KODE)." >&2
  exit 1
fi
echo "== transport oppe: https://$HOST/live → $KODE =="
