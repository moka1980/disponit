#!/usr/bin/env bash
# M-17 kundeservice (ARC B) — vertsoppsett i ett (M-23-formen, ARC B kundeservice PR 6). Kjør som root:
#   sudo bash deploy/staging/m17-oppsett.sh /root/v_kundeservice.hemmelighet
# Hemmeligheten lages med: python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
# og legges i en 0600-fil — aldri som argument (den ville stått i ps og historikk).
# Idempotent: kan kjøres på nytt; steg som alt er gjort hoppes over.
set -euo pipefail
HEMFIL="${1:?bruk: sudo bash deploy/staging/m17-oppsett.sh <0600-fil med v_kundeservice-hemmeligheten>}"
[ -f "$HEMFIL" ] || { echo "finner ikke $HEMFIL"; exit 1; }
HEMMELIGHET=$(tr -d '

 ' < "$HEMFIL")
[ ${#HEMMELIGHET} -ge 32 ] || { echo "hemmeligheten må være minst 32 tegn"; exit 1; }
AKTIV=/opt/disponit/aktiv; PY=/opt/disponit/.venv/bin/python; ENVFIL=/etc/disponit/staging.env
MODUL=m17_kundeservice; REL=m17-r1; MILJO=staging; AKTOR=m17-utrulling
[ "$(id -u)" = 0 ] || { echo "kjør med sudo"; exit 1; }
cd "$AKTIV"

echo "== 1/5 v_kundeservice i DISPONIT_ATT_NOKLER"
cp -n "$ENVFIL" "$ENVFIL.bak-m17" 2>/dev/null || true
# Hemmeligheten går som MILJØVARIABEL, aldri som argument (CodeRabbit på
# ARC B PR 6): et argument står i `ps` for alle på verten.
DISPONIT_HEM="$HEMMELIGHET" python3 - "$ENVFIL" <<'PY'
import json, os, sys
p, hem = sys.argv[1], os.environ["DISPONIT_HEM"]
linjer = open(p, encoding="utf-8").read().split("\n")
for i, l in enumerate(linjer):
    s = l.strip()
    if s.startswith("DISPONIT_ATT_NOKLER=") or s.startswith("export DISPONIT_ATT_NOKLER="):
        pre = "export " if s.startswith("export ") else ""
        v = s.split("=", 1)[1].strip()
        if v[:1] in ("'", '"') and v[-1:] == v[:1]:
            v = v[1:-1]
        d = json.loads(v)
        if d.get("v_kundeservice", {}).get("vk1") == hem:
            print("   alt satt"); break
        d.setdefault("v_kundeservice", {})["vk1"] = hem
        linjer[i] = pre + "DISPONIT_ATT_NOKLER='" + json.dumps(d, separators=(",", ":")) + "'"
        print("   lagt til v_kundeservice/vk1"); break
else:
    sys.exit("   FANT IKKE DISPONIT_ATT_NOKLER i " + p)
open(p, "w", encoding="utf-8").write("\n".join(linjer))
PY
if ! python3 -c "import json,sys; d=json.load(open('/etc/disponit/plan/DISPONIT_ATT_NOKLER')); sys.exit(0 if d.get('v_kundeservice',{}).get('vk1') else 1)" 2>/dev/null; then
  echo "   materialiserer via opp.sh (tar noen minutter) …"
  deploy/staging/opp.sh >/tmp/m17-opp.log 2>&1 || { tail -20 /tmp/m17-opp.log; echo "   opp.sh FEILET — se /tmp/m17-opp.log"; exit 1; }
fi
python3 -c "import json; d=json.load(open('/etc/disponit/plan/DISPONIT_ATT_NOKLER')); print('   plan har v_kundeservice:', 'vk1' in d.get('v_kundeservice',{}))"
python3 -c "import json; d=json.load(open('/etc/disponit/api/DISPONIT_ATT_NOKLER')); print('   api har v_kundeservice:', 'vk1' in d.get('v_kundeservice',{}))"

echo "== 2/5 konto, konfig, kvitteringsnøkkel, unit"
id disponit-m17 >/dev/null 2>&1 || useradd --system --home /nonexistent --shell /usr/sbin/nologin disponit-m17
install -d -m 0750 -o root -g disponit-m17 /etc/disponit/m17
SMTP=$(grep -E '^DISPONIT_SMTP_' /etc/disponit/varsel/smtp.env || true)
[ -n "$SMTP" ] || { echo "   ingen DISPONIT_SMTP_* i /etc/disponit/varsel/smtp.env — modulen kan ikke sende"; exit 1; }
{ printf '%s\n' "$SMTP"
  echo "DISPONIT_API_URL=https://disponit.com"
  echo "DISPONIT_KVITTERINGSNOKKEL=/etc/disponit/m17/kvitteringsnokkel.json"
} > /etc/disponit/m17/konfig
printf '{"verifikator": "v_kundeservice", "nokkel_id": "vk1", "hemmelighet": "%s"}\n' "$HEMMELIGHET" > /etc/disponit/m17/kvitteringsnokkel.json
chown root:disponit-m17 /etc/disponit/m17/konfig /etc/disponit/m17/kvitteringsnokkel.json
chmod 0640 /etc/disponit/m17/konfig /etc/disponit/m17/kvitteringsnokkel.json
cp deploy/staging/disponit-m17.service /etc/systemd/system/disponit-m17.service
systemctl daemon-reload
# Tjenesten leser kvitteringsnøkkelen selv, og /etc/disponit er lukket for andre:
# gjennomgang (--x) for gruppen, som for m57.
setfacl -m g:disponit-m17:--x /etc/disponit
echo "   smtp-variabler i konfig: $(grep -c '^DISPONIT_SMTP_' /etc/disponit/m17/konfig)"

echo "== 3/5 registrering av modulkjeden"
KONTRAKT=$(sha256sum platform/core/oppdragskontrakt.py | cut -c1-64)
KVITT=$(sha256sum platform/modules/m17_kundeservice/controller.py | cut -c1-64)
PAYLOAD=$(PYTHONPATH=platform/core "$PY" -c "import json,hashlib,oppdragskontrakt as o; print(hashlib.sha256(json.dumps(sorted(o.OPPDRAGSTYPER['kundeservice.svar.send'].felter)).encode()).hexdigest())")
DIGEST=$(tar -C platform/modules -cf - m17_kundeservice | sha256sum | cut -c1-64)
echo "$KONTRAKT" > /etc/disponit/m17/kontrakt-hash
set -a; . "$ENVFIL"; set +a
if sudo -u postgres psql -d disponit -Atc "SELECT 1 FROM modulrelease WHERE modul_id='$MODUL' AND release_id='$REL'" | grep -q 1; then
  echo "   release $REL finnes alt"
else
  "$PY" deploy/staging/registrer-m17-kundeservice.py "$REL" "$KONTRAKT" "$DIGEST" "$PAYLOAD" "$KVITT" | sed 's/^/   /'
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
if [ -s /etc/disponit/m17/DISPONIT_MODULTOKEN ]; then
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
  printf '%s' "$TOK" > /etc/disponit/m17/DISPONIT_MODULTOKEN
  chown root:disponit-m17 /etc/disponit/m17/DISPONIT_MODULTOKEN; chmod 0640 /etc/disponit/m17/DISPONIT_MODULTOKEN
  echo "   modultoken skrevet (${TOK:0:8}…)"
  "$PY" deploy/staging/token-cli.py deaktiver "$DRIFT_ID" >/dev/null 2>&1 && echo "   drift-token $DRIFT_ID tilbakekalt (engangsbruk)"
fi
systemctl enable --now disponit-m17 >/dev/null 2>&1; systemctl restart disponit-m17
sleep 6
journalctl -u disponit-m17 -n 5 --no-pager | sed 's/^/   /'
echo "== ferdig"
