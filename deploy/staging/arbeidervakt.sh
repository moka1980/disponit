#!/usr/bin/env bash
# Arbeidervakten: gjør en arbeider som feiler i HVER runde synlig.
#
# BAKGRUNNEN (funnet 20.11.2026). Modultokenene utløp 17.10, og alle sju
# arbeiderne logget `runde_feilet` hvert tiende sekund i en måned. Ingenting
# så det: tjenestene sto «active», helsesjekken måler bare API-et og M-37,
# og `systemctl --failed` var tom. `roter-modultokener.sh` fjerner den ene
# ÅRSAKEN; denne vakten måler VIRKNINGEN, uansett årsak.
#
# Kjøres av `disponit-arbeidervakt.timer` hvert femte minutt, som root
# (journalen). For hver `/etc/disponit/<katalog>/DISPONIT_MODULTOKEN`:
#   - tjenesten er ikke «active»                 -> FEIL
#   - minst TERSKEL `runde_feilet` siste VINDU   -> FEIL
# En runde tar ca. ti sekunder, så vinduet rommer rundt seksti. En
# utrulling starter API-et på nytt og gir én eller to feil — langt under
# terskelen. En arbeider som feiler i hver runde, når den på under fire
# minutter.
#
# Vakten RETTER ingenting. Den avslutter med feil, så uniten står i
# `systemctl --failed` til årsaken er borte og neste kjøring er grønn.
set -uo pipefail
KATALOG="${ARBEIDERVAKT_KATALOG:-/etc/disponit}"
VINDU_MIN="${VINDU_MIN:-10}"
TERSKEL="${TERSKEL:-20}"
[ "$(id -u)" = 0 ] || [ -n "${ARBEIDERVAKT_TEST:-}" ] || { echo "kjør som root"; exit 2; }

feil=0; sett=0
for FIL in "$KATALOG"/*/DISPONIT_MODULTOKEN; do
  [ -s "$FIL" ] || continue
  KAT=$(basename "$(dirname "$FIL")")
  UNIT="disponit-$KAT"; [ "$KAT" = wcag ] && UNIT=disponit-wcag-audit
  sett=$((sett + 1))
  TILSTAND=$(systemctl is-active "$UNIT" 2>/dev/null || true)
  if [ "$TILSTAND" != active ]; then
    echo "FEIL $UNIT: tjenesten er «${TILSTAND:-ukjent}», ikke «active»"
    feil=1; continue
  fi
  # En journal vi ikke får lest er en BLIND vakt, ikke en frisk arbeider.
  if ! LOGG=$(journalctl -u "$UNIT" --since "-${VINDU_MIN} min" --no-pager -o cat 2>/dev/null); then
    echo "FEIL $UNIT: journalen kunne ikke leses"; feil=1; continue
  fi
  ANTALL=$(printf '%s\n' "$LOGG" | grep -c '"hendelse": "runde_feilet"' || true)
  if [ "$ANTALL" -ge "$TERSKEL" ]; then
    echo "FEIL $UNIT: $ANTALL feilede runder siste $VINDU_MIN min (terskel $TERSKEL). Se: journalctl -u $UNIT -n 20; utløpt token rettes med deploy/staging/modul-til-produksjon.sh"
    feil=1
  else
    echo "ok   $UNIT: $ANTALL feilede runder siste $VINDU_MIN min"
  fi
done
# Ingen arbeidere funnet er også et svar — men ikke «alt er bra».
[ "$sett" -gt 0 ] || { echo "FEIL: fant ingen arbeidere under $KATALOG"; exit 1; }
exit "$feil"
