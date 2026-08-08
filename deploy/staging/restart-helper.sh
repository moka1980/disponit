#!/usr/bin/env bash
# ============================================================
# Privilegert restart-helper (PR-009 v3 §2): den ENESTE veien
# kontrollprosessen har til systemctl, og den kan restarte NØYAKTIG to
# units. Allowlisten står i koden — helperen validerer selv, uavhengig av
# sudoers-regelen, så en romsligere sudoers-linje alene aldri er nok
# (to lag, samme regel: en vakt som bare finnes i konfig er ingen vakt).
# ============================================================
set -euo pipefail

UNIT="${1:-}"
case "$UNIT" in
  disponit-api.service|disponit-m37.service) ;;
  *)
    logger -t disponit-restart-helper "AVVIST: '$UNIT' er utenfor allowlisten"
    echo "AVVIST: '$UNIT' er ikke en tillatt unit" >&2
    exit 1
    ;;
esac

logger -t disponit-restart-helper "restart av $UNIT (helsetimer)"
exec systemctl restart "$UNIT"
