#!/usr/bin/env bash
# ADR-009 steg 2 og 3: gir ÉN arbeidermodul en claiming-deployment og et
# modultoken i miljøet `produksjon`. Kjør som root:
#   sudo bash deploy/staging/modul-til-produksjon.sh <modul_id> <katalog> <unit>
#   f.eks. … m14_fakturakontroll m14 disponit-m14
#
# Staging-deploymenten og det gamle tokenet RØRES IKKE: tokenfila kopieres
# til `DISPONIT_MODULTOKEN.staging-<tid>` før den nye skrives, så steget
# angres ved å kopiere den tilbake og starte arbeideren på nytt.
# Idempotent: en deployment eller et token som finnes, hoppes over.
set -euo pipefail
MODUL="${1:?modul_id}"; KAT="/etc/disponit/${2:?katalog}"; UNIT="${3:?unit}"
AKTOR=til-produksjon; PY=/opt/disponit/.venv/bin/python
API=https://disponit.com
[ "$(id -u)" = 0 ] || { echo "kjør med sudo"; exit 1; }
[ -s "$KAT/DISPONIT_MODULTOKEN" ] || { echo "fant ikke $KAT/DISPONIT_MODULTOKEN"; exit 1; }
cd /opt/disponit/aktiv
sql() { sudo -u postgres psql -d disponit -X -At -F'|' -v ON_ERROR_STOP=1 -c "$1"; }
felt() { python3 -c "import json,sys; print(json.load(sys.stdin).get('$1',''))" 2>/dev/null || true; }

echo "== 1/3 deployment i produksjon"
IFS='|' read -r SREL KV KH < <(sql "SELECT release_id, kontraktversjon, kontrakt_hash
  FROM moduldeployment WHERE modul_id='$MODUL' AND miljo='staging' AND livslop='claiming'")
[ -n "${SREL:-}" ] || { echo "   ingen claiming staging-deployment for $MODUL"; exit 1; }
REL=$(sql "SELECT release_id FROM moduldeployment WHERE modul_id='$MODUL'
  AND miljo='produksjon' AND livslop='claiming' AND kontraktversjon=$KV AND kontrakt_hash='$KH'")
if [ -n "$REL" ]; then
  echo "   finnes alt: $REL"
else
  REL=$SREL
  sql "SET ROLE disponit_modules_admin; SELECT bytt_release('$MODUL','produksjon','$REL',$KV,'$KH','$AKTOR');" >/dev/null
  echo "   registrert: $REL (samme release og kontrakt som staging)"
fi
sql "SELECT '   '||miljo||' '||release_id||' '||livslop FROM moduldeployment
      WHERE modul_id='$MODUL' AND livslop='claiming' ORDER BY miljo"

echo "== 2/3 modultoken for produksjon"
HAR=$(sql "SELECT count(*) FROM modultoken WHERE modul_id='$MODUL' AND miljo='produksjon'
  AND release_id='$REL' AND tilbakekalt_ts IS NULL AND utloper > now()")
if [ "$HAR" != 0 ]; then
  echo "   gyldig produksjonstoken finnes alt — tokenfila røres ikke"
else
  set -a; . /etc/disponit/staging.env; set +a
  UT=$("$PY" deploy/staging/token-cli.py opprett --tenant disponit --rolle drift --scope modules:onboard --bootstrap 2>&1 || true)
  DRIFT=$(printf '%s\n' "$UT" | grep -oE '^\s*tk_[A-Za-z0-9_-]+\.[^ ]+' | tr -d ' ' | head -1)
  DRIFT_ID=${DRIFT%%.*}
  [ -n "$DRIFT" ] || { echo "   fikk ikke drift-token:"; printf '%s\n' "$UT" | grep -v '\.' | sed 's/^/   /'; exit 1; }
  rydd() { "$PY" deploy/staging/token-cli.py deaktiver "$DRIFT_ID" >/dev/null 2>&1 || true; }
  # Token og hemmelighet går aldri som argument: header fra en
  # prosess-substitusjon, kropp fra en fil bare root kan lese.
  TMP=$(mktemp -d); chmod 700 "$TMP"; trap 'rm -rf "$TMP"' EXIT
  printf '{"modul_id":"%s","miljo":"produksjon","release_id":"%s"}' "$MODUL" "$REL" > "$TMP/be"
  curl -s -X POST "$API/v1/modul/onboarding" -H @<(printf 'authorization: Bearer %s' "$DRIFT") \
       -H 'content-type: application/json' --data-binary "@$TMP/be" -o "$TMP/svar1"
  HEM=$(felt hemmelighet < "$TMP/svar1")
  [ -n "$HEM" ] || { echo "   onboarding avvist: $(cut -c1-200 "$TMP/svar1")"; rydd; exit 1; }
  printf '{"hemmelighet":"%s"}' "$HEM" > "$TMP/inn"
  curl -s -X POST "$API/v1/modul/onboarding/innlos" -H 'content-type: application/json' \
       --data-binary "@$TMP/inn" -o "$TMP/svar2"
  TOK=$(felt token < "$TMP/svar2")
  [ -n "$TOK" ] || { echo "   innløsning avvist"; rydd; exit 1; }
  BAK="$KAT/DISPONIT_MODULTOKEN.staging-$(date +%Y%m%dT%H%M%S)"
  cp -p "$KAT/DISPONIT_MODULTOKEN" "$BAK"
  # `>` beholder eier og modus på den eksisterende fila.
  printf '%s' "$TOK" > "$KAT/DISPONIT_MODULTOKEN"
  rydd
  echo "   nytt token skrevet; det gamle står i $BAK"

  echo "== 3/3 start arbeideren på nytt"
  systemctl restart "$UNIT"
  sleep 8
fi
systemctl is-active "$UNIT" | sed 's/^/   tjenesten: /'
journalctl -u "$UNIT" -n 4 --no-pager -o cat | cut -c1-160 | sed 's/^/   /'
echo "== ferdig: $MODUL"
