#!/usr/bin/env bash
# Fornyer arbeidermodulenes tokener før de utløper.
#
# BAKGRUNNEN (funnet 20.11.2026). Et modultoken lever i 30 døgn, og
# `/v1/modul/token/roter` har stått der siden 035 — men ingenting kalte den.
# Tokenene fra 17.9 utløp 17.10, og alle sju arbeiderne feilet deretter i
# HVER runde uten at noe varslet: tjenestene sto «active», helsesjekken så
# dem ikke, og køen var tom, så ingen kunde merket det.
#
# Skriptet kjøres av `disponit-modultoken.timer` to ganger i døgnet, som
# root. For hver `/etc/disponit/<katalog>/DISPONIT_MODULTOKEN`:
#   - mer enn MIN_DOGN igjen  -> røres ikke
#   - mindre igjen            -> roteres med tokenet selv, fila byttes,
#                                arbeideren startes på nytt
#   - alt utløpt              -> kan ikke rotere seg selv. Skriptet sier det
#                                og avslutter med feil, så uniten står i
#                                `systemctl --failed` til noen har kjørt
#                                `modul-til-produksjon.sh` for modulen.
# Det gamle tokenet er gyldig i et nådevindu etter rotasjonen (serveren
# svarer med fristen), så arbeideren mister ingen runde på byttet.
set -uo pipefail
MIN_DOGN="${MIN_DOGN:-10}"
API="${DISPONIT_API_URL:-https://disponit.com}"
[ "$(id -u)" = 0 ] || { echo "kjør som root"; exit 2; }
sql() { runuser -u postgres -- psql -d disponit -X -At -F'|' -v ON_ERROR_STOP=1 -c "$1"; }

feil=0
for FIL in /etc/disponit/*/DISPONIT_MODULTOKEN; do
  [ -s "$FIL" ] || continue
  KAT=$(basename "$(dirname "$FIL")")
  # `KUN=<katalog>` begrenser kjøringen til én modul (prøving for hånd).
  [ -z "${KUN:-}" ] || [ "$KUN" = "$KAT" ] || continue
  UNIT="disponit-$KAT"; [ "$KAT" = wcag ] && UNIT=disponit-wcag-audit
  # Token-id-en er den offentlige halvdelen (`mtk_<id>.<hemmelighet>`);
  # hemmeligheten forlater aldri fila.
  TID=$(python3 -c "import sys; print(open(sys.argv[1]).read().strip()[4:].partition('.')[0])" "$FIL")
  RAD=$(sql "SELECT modul_id, miljo,
                    floor(extract(epoch FROM utloper - now()) / 86400)::int,
                    (tilbakekalt_ts IS NOT NULL AND tilbakekalt_ts <= now())
               FROM modultoken WHERE token_id::text = '$TID'" 2>/dev/null || true)
  if [ -z "$RAD" ]; then
    echo "FEIL $KAT: tokenet i $FIL finnes ikke i registeret"; feil=1; continue
  fi
  IFS='|' read -r MODUL MILJO DOGN TILBAKEKALT <<<"$RAD"
  if [ "$TILBAKEKALT" = t ] || [ "$DOGN" -lt 0 ]; then
    echo "FEIL $KAT ($MODUL, $MILJO): tokenet er utløpt eller tilbakekalt og kan ikke rotere seg selv. Kjør deploy/staging/modul-til-produksjon.sh $MODUL $KAT $UNIT"
    feil=1; continue
  fi
  if [ "$DOGN" -gt "$MIN_DOGN" ]; then
    echo "ok   $KAT ($MODUL, $MILJO): $DOGN døgn igjen"; continue
  fi

  TMP=$(mktemp -d); chmod 700 "$TMP"
  printf '{"rotasjon_id":"%s"}' "$(cat /proc/sys/kernel/random/uuid)" > "$TMP/kropp"
  KODE=$(curl -s -o "$TMP/svar" -w '%{http_code}' -X POST "$API/v1/modul/token/roter" \
           -H @<(printf 'authorization: Bearer %s' "$(cat "$FIL")") \
           -H 'content-type: application/json' --data-binary "@$TMP/kropp" || echo 000)
  NY=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('token',''))" "$TMP/svar" 2>/dev/null || true)
  if [ "$KODE" != 201 ] || [ -z "$NY" ]; then
    echo "FEIL $KAT ($MODUL): rotasjonen svarte $KODE"; rm -rf "$TMP"; feil=1; continue
  fi
  cp -p "$FIL" "$FIL.for-$(date +%s)"
  # `>` beholder eier og modus på den eksisterende fila.
  printf '%s' "$NY" > "$FIL"
  rm -rf "$TMP"
  # Bare de to siste forgjengerne beholdes.
  ls -t "$FIL".for-* 2>/dev/null | tail -n +3 | xargs -r rm -f
  if systemctl restart "$UNIT"; then
    echo "NY   $KAT ($MODUL, $MILJO): rotert med $DOGN døgn igjen, $UNIT startet på nytt"
  else
    echo "FEIL $KAT ($MODUL): tokenet er rotert, men $UNIT startet ikke"; feil=1
  fi
done
exit "$feil"
