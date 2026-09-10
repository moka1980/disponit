#!/usr/bin/env bash
# M-26 tilbud (ARC B) — vertsoppsett i ett (M-14-formen, ARC B tilbud PR 6). Kjør som root:
#   sudo bash deploy/staging/m26-oppsett.sh
# INGEN NY HEMMELIGHET: modulen kvitterer som `v_prisbok`, og den nøkkelen
# står alt i DISPONIT_ATT_NOKLER (API + plan) siden M-1. Skriptet leser den
# fra API-ets materialiserte nøkkelfil (root) og legger den i modulens
# 0640-fil — aldri via argument eller samtale. SMTP som for M-17: modulen
# sender tilbudet over husets SMTP (varsel/smtp.env).
# Idempotent: kan kjøres på nytt; steg som alt er gjort hoppes over.
set -euo pipefail
AKTIV=/opt/disponit/aktiv; PY=/opt/disponit/.venv/bin/python; ENVFIL=/etc/disponit/staging.env
MODUL=m26_prisbok; REL=m26-r1; MILJO=staging; AKTOR=m26-utrulling
[ "$(id -u)" = 0 ] || { echo "kjør med sudo"; exit 1; }
cd "$AKTIV"

echo "== 1/5 v_prisbok i DISPONIT_ATT_NOKLER (finnes siden M-1)"
for fil in /etc/disponit/api/DISPONIT_ATT_NOKLER /etc/disponit/plan/DISPONIT_ATT_NOKLER; do
  python3 -c "import json,sys; d=json.load(open('$fil')); k=d.get('v_prisbok') or {}; sys.exit(0 if k else 1)" \
    || { echo "   $fil mangler v_prisbok — kjør opp.sh først"; exit 1; }
  echo "   $fil har v_prisbok"
done
NOKKEL_ID=$(python3 -c "import json; d=json.load(open('/etc/disponit/api/DISPONIT_ATT_NOKLER')); print(sorted(d['v_prisbok'])[0])")

echo "== 2/5 konto, konfig, kvitteringsnøkkel, unit"
id disponit-m26 >/dev/null 2>&1 || useradd --system --home /nonexistent --shell /usr/sbin/nologin disponit-m26
install -d -m 0750 -o root -g disponit-m26 /etc/disponit/m26
SMTP=$(grep -E '^DISPONIT_SMTP_' /etc/disponit/varsel/smtp.env || true)
[ -n "$SMTP" ] || { echo "   ingen DISPONIT_SMTP_* i /etc/disponit/varsel/smtp.env — modulen kan ikke sende"; exit 1; }
{ printf '%s\n' "$SMTP"
  echo "DISPONIT_API_URL=https://disponit.com"
  echo "DISPONIT_KVITTERINGSNOKKEL=/etc/disponit/m26/kvitteringsnokkel.json"
} > /etc/disponit/m26/konfig
# Nøkkelen kopieres fil-til-fil i python (aldri som argument, aldri i ps).
umask 077
python3 - "$NOKKEL_ID" <<'PY' > /etc/disponit/m26/kvitteringsnokkel.json
import json, sys
d = json.load(open("/etc/disponit/api/DISPONIT_ATT_NOKLER"))
nid = sys.argv[1]
print(json.dumps({"verifikator": "v_prisbok", "nokkel_id": nid,
                  "hemmelighet": d["v_prisbok"][nid]}))
PY
umask 022
chown root:disponit-m26 /etc/disponit/m26/konfig /etc/disponit/m26/kvitteringsnokkel.json
chmod 0640 /etc/disponit/m26/konfig /etc/disponit/m26/kvitteringsnokkel.json
cp deploy/staging/disponit-m26.service /etc/systemd/system/disponit-m26.service
systemctl daemon-reload
# Tjenesten leser kvitteringsnøkkelen selv, og /etc/disponit er lukket for andre:
# gjennomgang (--x) for gruppen, som for m57.
setfacl -m g:disponit-m26:--x /etc/disponit
echo "   kvitteringsnøkkel: v_prisbok/$NOKKEL_ID; smtp-variabler i konfig: $(grep -c '^DISPONIT_SMTP_' /etc/disponit/m26/konfig)"

echo "== 3/5 registrering av modulkjeden"
KONTRAKT=$(sha256sum platform/core/oppdragskontrakt.py | cut -c1-64)
KVITT=$(sha256sum platform/modules/m26_prisbok/controller.py | cut -c1-64)
PAYLOAD=$(PYTHONPATH=platform/core "$PY" -c "import json,hashlib,oppdragskontrakt as o; print(hashlib.sha256(json.dumps(sorted(o.OPPDRAGSTYPER['tilbud.generer'].felter)).encode()).hexdigest())")
DIGEST=$(tar -C platform/modules -cf - m26_prisbok | sha256sum | cut -c1-64)
echo "$KONTRAKT" > /etc/disponit/m26/kontrakt-hash
set -a; . "$ENVFIL"; set +a
if sudo -u postgres psql -d disponit -Atc "SELECT 1 FROM modulrelease WHERE modul_id='$MODUL' AND release_id='$REL'" | grep -q 1; then
  echo "   release $REL finnes alt"
else
  "$PY" deploy/staging/registrer-m26-prisbok.py "$REL" "$KONTRAKT" "$DIGEST" "$PAYLOAD" "$KVITT" | sed 's/^/   /'
fi

echo "== 4/5 status og deployment"
STATUS=$(sudo -u postgres psql -d disponit -Atc "SELECT status FROM modulhode WHERE modul_id='$MODUL'")
echo "   status nå: $STATUS"
if [ "$STATUS" = installert ]; then
  sudo -u postgres psql -d disponit -v ON_ERROR_STOP=1 -q -c "SET ROLE disponit_modules_admin; SELECT sett_modulstatus('$MODUL','staging_verifisert',NULL,'$AKTOR');"
fi
sudo -u postgres psql -d disponit -v ON_ERROR_STOP=1 -q -c "SET ROLE disponit_modules_admin; SELECT bytt_release('$MODUL','$MILJO','$REL',1,'$KONTRAKT','$AKTOR');"
STATUS=$(sudo -u postgres psql -d disponit -Atc "SELECT status FROM modulhode WHERE modul_id='$MODUL'")
if [ "$STATUS" != aktiv ]; then
  sudo -u postgres psql -d disponit -v ON_ERROR_STOP=1 -q -c "SET ROLE disponit_modules_admin; SELECT sett_modulstatus('$MODUL','aktiv','$REL','$AKTOR');"
fi
sudo -u postgres psql -d disponit -Atc "SELECT 'deployment: '||modul_id||' '||miljo||' '||release_id||' '||livslop FROM moduldeployment WHERE modul_id='$MODUL'" | sed 's/^/   /'
sudo -u postgres psql -d disponit -Atc "SELECT 'modulhode: '||status FROM modulhode WHERE modul_id='$MODUL'" | sed 's/^/   /'

echo "== 5/5 onboarding → modultoken → start"
if [ -s /etc/disponit/m26/DISPONIT_MODULTOKEN ]; then
  echo "   modultoken finnes alt"
else
  # `|| true`: under set -e ville et ikke-null-svar drept skriptet FØR
  # diagnosen under (CodeRabbit på ARC B kundeservice PR 6).
  UT=$("$PY" deploy/staging/token-cli.py opprett --tenant disponit --rolle drift --scope modules:onboard --bootstrap 2>&1 || true)
  DRIFT=$(printf '%s\n' "$UT" | grep -oE '^\s*tk_[A-Za-z0-9_-]+\.[^ ]+' | tr -d ' ' | head -1)
  DRIFT_ID=${DRIFT%%.*}
  [ -n "$DRIFT" ] || { echo "   fikk ikke drift-token:"; printf '%s\n' "$UT" | grep -v '\.' | sed 's/^/   /'; exit 1; }
  # Tokenet og hemmeligheten går ALDRI som argument (de ville stått i
  # `ps` for alle på verten): headeren leses fra en prosess-substitusjon,
  # kroppen fra stdin.
  SVAR=$(printf '{"modul_id":"%s","miljo":"%s","release_id":"%s"}' "$MODUL" "$MILJO" "$REL" \
    | curl -s -X POST https://disponit.com/v1/modul/onboarding -H @<(printf 'authorization: Bearer %s' "$DRIFT") -H 'content-type: application/json' -d @-)
  HEM=$(printf '%s' "$SVAR" | python3 -c "import json,sys; print(json.load(sys.stdin).get('hemmelighet',''))" 2>/dev/null || true)
  [ -n "$HEM" ] || { echo "   onboarding avvist: $SVAR"; "$PY" deploy/staging/token-cli.py deaktiver "$DRIFT_ID" >/dev/null 2>&1 || true; exit 1; }
  SVAR2=$(printf '{"hemmelighet":"%s"}' "$HEM" \
    | curl -s -X POST https://disponit.com/v1/modul/onboarding/innlos -H 'content-type: application/json' -d @-)
  TOK=$(printf '%s' "$SVAR2" | python3 -c "import json,sys; print(json.load(sys.stdin).get('token',''))" 2>/dev/null || true)
  [ -n "$TOK" ] || { echo "   innløsning avvist: $SVAR2"; "$PY" deploy/staging/token-cli.py deaktiver "$DRIFT_ID" >/dev/null 2>&1 || true; exit 1; }
  printf '%s' "$TOK" > /etc/disponit/m26/DISPONIT_MODULTOKEN
  chown root:disponit-m26 /etc/disponit/m26/DISPONIT_MODULTOKEN; chmod 0640 /etc/disponit/m26/DISPONIT_MODULTOKEN
  echo "   modultoken skrevet (${TOK:0:8}…)"
  "$PY" deploy/staging/token-cli.py deaktiver "$DRIFT_ID" >/dev/null 2>&1 && echo "   drift-token $DRIFT_ID tilbakekalt (engangsbruk)"
fi
systemctl enable --now disponit-m26 >/dev/null 2>&1; systemctl restart disponit-m26
sleep 6
journalctl -u disponit-m26 -n 5 --no-pager | sed 's/^/   /'
echo "== ferdig"
