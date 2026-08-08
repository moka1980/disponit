#!/usr/bin/env bash
# ============================================================
# Disponit helsesjekk (PR-009 v2 §1 + v3 §1–2 + v4 §3).
# Kjøres av disponit-helse.timer hvert 60. sekund, som en UPRIVILEGERT
# kontrollprosess. Restart skjer KUN via den privilegerte helperen med
# lukket unit-allowlist (sudoers-regelen peker på nøyaktig én kommando).
#
# To ULIKE kontroller:
#   API : GET /live over Unix-socketen — svarer event-loopen?
#         (ALDRI /ready: en DB-feil skal ikke gi restartstorm.)
#   M-37: heartbeat-filen skrevet av HOVEDLØKKEN. Foreldet = hengt.
#         `db_utilgjengelig` i fersk heartbeat = IKKE hengt — workeren
#         løkker og venter; en restart løser ingenting da.
#
# Tellerens kontrakt (v3 §2 + v4 §3): flock rundt sjekk+oppdatering;
# 3 påfølgende feil → restartforsøk; telleren nullstilles KUN når
# prosessen har NY oppstartstid OG består sjekken igjen — en restart som
# ikke hjalp fortsetter å telle mot unitens StartLimit.
# ============================================================
set -euo pipefail

TILSTAND=/run/disponit-health
HEARTBEAT=/run/disponit-m37/heartbeat
SOCKET=/run/disponit/api.sock
MAKS_FEIL=3
MAKS_HEARTBEAT_ALDER=90          # 3 × forventet syklustid (v3 §1)

install -d -m 755 "$TILSTAND"

sjekk_api() {
  curl -fsS --max-time 5 --unix-socket "$SOCKET" \
    http://disponit/live >/dev/null 2>&1
}

sjekk_m37() {
  [ -f "$HEARTBEAT" ] || return 1
  python3 - "$HEARTBEAT" "$MAKS_HEARTBEAT_ALDER" <<'PY'
import json, sys, time
try:
    hb = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
alder = time.time() - float(hb.get("ts", 0))
# db_utilgjengelig i FERSK heartbeat er en frisk, ventende worker.
sys.exit(0 if alder <= float(sys.argv[2]) else 1)
PY
}

oppstart_ts() {  # unit -> MONOTONIC oppstartsstempel ('' hvis nede)
  systemctl show -p ExecMainStartTimestampMonotonic --value "$1" 2>/dev/null
}

haandter() {  # unit sjekkfunksjon
  local unit=$1 sjekk=$2
  local fil="$TILSTAND/${unit}.count" pidfil="$TILSTAND/${unit}.start"
  exec 8>"$TILSTAND/${unit}.lock"
  flock 8                                  # aldri kappløp mellom kjøringer
  local teller; teller=$(cat "$fil" 2>/dev/null || echo 0)
  local start_for; start_for=$(cat "$pidfil" 2>/dev/null || echo "")
  local start_naa; start_naa=$(oppstart_ts "$unit")

  if "$sjekk"; then
    # Nullstilling krever RESULTAT: bestått sjekk, og — hvis en restart
    # er utført — ny oppstartstid (v4 §3). En bestått sjekk fra samme
    # prosess som feilet er også et resultat: prosessen kom seg.
    echo 0 > "$fil"
    echo "$start_naa" > "$pidfil"
    return 0
  fi

  teller=$((teller + 1))
  echo "$teller" > "$fil"
  logger -t disponit-helse "$unit: helsesjekk feilet ($teller/$MAKS_FEIL)"
  if [ "$teller" -ge "$MAKS_FEIL" ]; then
    if [ -n "$start_naa" ] && [ "$start_naa" = "$start_for" ]; then
      : # samme prosess som sist — restart den
    fi
    logger -t disponit-helse "$unit: ber om restart via helper"
    sudo /usr/local/lib/disponit-restart-helper "$unit" || \
      logger -t disponit-helse "$unit: helper avviste eller feilet"
    # Telleren står — den nullstilles først når NY prosess består sjekken.
    echo "$start_naa" > "$pidfil"
  fi
  return 1
}

rc=0
haandter disponit-api.service sjekk_api || rc=1
haandter disponit-m37.service sjekk_m37 || rc=1
exit $rc
