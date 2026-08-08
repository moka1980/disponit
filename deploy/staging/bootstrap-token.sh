#!/usr/bin/env bash
# ============================================================
# bootstrap-token.sh <tenant-id>  (PR-009 v3 §4 + v4 §1 + v5 §3)
#
# EKSPLISITT INTERAKTIV førstegangsutlevering: agent-token
# (decision:write) og bruker-token (lese-scopes, PR-008) — hver for seg,
# med separat resultat («agent ok, bruker feilet — kjør roter-token»).
# Selve seremonien (TTY før generering → PENDING → lokal verifisering →
# visning → operatørbekreftelse → aktivering) bor i token-cli og er
# testet der; dette skriptet er operatørens inngangsdør og NEKTER uten
# TTY (Codex-port 7) — automatisert deploy (opp.sh) rører aldri tokens.
# ============================================================
set -euo pipefail

TENANT="${1:?bruk: bootstrap-token.sh <tenant-id>}"

if ! [ -t 1 ]; then
  echo "AVBRUTT: bootstrap-token.sh krever et terminalvindu — hemmeligheten" >&2
  echo "vises én gang og skal bekreftes av et menneske (Codex-port 7)." >&2
  exit 1
fi

set -a; . /etc/disponit/staging.env; set +a
CLI=(/opt/disponit/.venv/bin/python
     /opt/disponit/aktiv/deploy/staging/token-cli.py)

STATUS_AGENT=feilet
STATUS_BRUKER=feilet

echo "== agent-token for $TENANT (decision:write) =="
if "${CLI[@]}" opprett --tenant "$TENANT" --rolle agent \
     --scope decision:write; then
  STATUS_AGENT=ok
fi

echo
echo "== bruker-token for $TENANT (lese-scopes, PR-008) =="
if "${CLI[@]}" opprett --tenant "$TENANT" --rolle bruker \
     --scope decisions:read --scope exceptions:read --scope policy:read; then
  STATUS_BRUKER=ok
fi

echo
echo "== resultat: agent=$STATUS_AGENT bruker=$STATUS_BRUKER =="
if [ "$STATUS_AGENT" != ok ] || [ "$STATUS_BRUKER" != ok ]; then
  echo "Delvis suksess er IKKE suksess: kjør roter-token.sh eller dette"
  echo "skriptet på nytt for delen som feilet — PENDING-rester ryddes av"
  echo "timeren innen TTL."
  exit 1
fi
