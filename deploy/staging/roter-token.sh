#!/usr/bin/env bash
# ============================================================
# roter-token.sh <token_id>  (PR-009 v2 §5)
#
# Eksplisitt rotasjon ved mistet/kompromittert hemmelighet. Rekkefølgen
# (korreksjon 2 + PR-009): NY opprettes som PENDING og aktiveres etter
# levert hemmelighet — GAMMEL tilbakekalles først da. Interaktiv, som
# bootstrap.
# ============================================================
set -euo pipefail

TOKEN_ID="${1:?bruk: roter-token.sh <token_id>}"

if ! [ -t 1 ]; then
  echo "AVBRUTT: roter-token.sh krever et terminalvindu." >&2
  exit 1
fi

set -a; . /etc/disponit/staging.env; set +a
exec /opt/disponit/.venv/bin/python \
  /opt/disponit/aktiv/deploy/staging/token-cli.py roter "$TOKEN_ID"
