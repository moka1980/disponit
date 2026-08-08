#!/usr/bin/env bash
# certbot deploy-hook (PR-009b v2 §3 pkt. 7): kjøres ETTER en vellykket
# fornyelse. Validerer FØR reload — en fornyelse skal aldri kunne ta ned
# tjenesten med en konfig nginx ikke godtar.
set -euo pipefail

if nginx -t; then
  systemctl reload nginx
  logger -t disponit-transport "sertifikat fornyet, nginx reloadet"
else
  logger -t disponit-transport \
    "ADVARSEL: fornyelse skjedde men nginx -t FEILET — IKKE reloadet"
  exit 1
fi
