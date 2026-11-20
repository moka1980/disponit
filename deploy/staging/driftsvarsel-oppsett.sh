#!/usr/bin/env bash
# Installerer driftsvarselet. Kjør som root:
#   sudo bash deploy/staging/driftsvarsel-oppsett.sh [mottaker@eksempel.no]
# Mottakeren skrives til /etc/disponit/driftsvarsel/konfig første gang (eller
# når den oppgis på nytt). Uten argument beholdes den som står der.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "kjør med sudo"; exit 1; }
KAT=/etc/disponit/driftsvarsel
cd /opt/disponit/aktiv
install -d -m 750 -o root -g disponit-helse "$KAT"
if [ -n "${1:-}" ]; then
  case "$1" in *@*.*) ;; *) echo "«$1» ser ikke ut som en e-postadresse"; exit 1;; esac
  printf 'DISPONIT_DRIFTSVARSEL_TIL=%s\n' "$1" > "$KAT/konfig"
fi
[ -s "$KAT/konfig" ] || { echo "ingen mottaker: oppgi en e-postadresse"; exit 1; }
chown root:disponit-helse "$KAT/konfig"; chmod 640 "$KAT/konfig"
[ -s /etc/disponit/varsel/smtp.env ] || { echo "mangler /etc/disponit/varsel/smtp.env"; exit 1; }
install -m 644 deploy/staging/disponit-driftsvarsel.service /etc/systemd/system/
install -m 644 deploy/staging/disponit-driftsvarsel.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now disponit-driftsvarsel.timer
systemctl list-timers disponit-driftsvarsel.timer --no-pager | sed -n 1,2p
