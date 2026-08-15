#!/usr/bin/env bash
# ============================================================
# Tilstandsmaskinen for /etc/disponit/staging.env.
#
# Skilt ut fra oppsett-postgresql.sh fordi ALLE fem feilene som er funnet i
# deploy-oppsettet så langt satt her — i håndteringen av hemmeligheter, ikke
# i pakkeinstallasjonen. Manuell staging-prøve fant dem, men manuell prøve
# er ingen port. Denne fila kan kjøres uten root, uten apt og uten
# PostgreSQL, og testes i CI (platform/core/tests/test_deploy_miljofil.py).
#
# To hooks gjør den testbar. Standardimplementasjonene snakker med
# databasen; testene erstatter dem med etterlignere:
#   rotere_passord <rolle> <passord>   — setter rollens passord
#   dsn_virker     <dsn>               — kan vi faktisk koble til?
#
# Krever: MILJOFIL, DB, BRUKER, MIGRATOR
# ============================================================

: "${MILJOFIL:?MILJOFIL må være satt}"

rotere_passord() {
  sudo -u postgres psql -qc "ALTER ROLE $1 PASSWORD '$2'"
}

dsn_virker() {
  psql "$1" -qtAc 'SELECT 1' >/dev/null 2>&1
}

har_nokkel() { grep -q "^$1=" "$MILJOFIL" 2>/dev/null; }

les_nokkel() {
  sed -n "s/^$1='\(.*\)'\$/\1/p" "$MILJOFIL" 2>/dev/null | tail -1
}

# Skriver en nøkkel. Den midlertidige fila lages i SAMME katalog som målet:
# `mv` er kun atomisk innenfor ett filsystem, og mktemp i /tmp gir ingen slik
# garanti når målet ligger under /etc (Codex' P1). Rettighetene settes før
# innholdet flyttes på plass, så fila aldri er lesbar for andre.
sett_nokkel() {
  local n="$1" v="$2" katalog tmp
  katalog=$(dirname "$MILJOFIL")
  tmp=$(mktemp "$katalog/.staging.env.XXXXXX") || return 1
  chmod 600 "$tmp"
  if [ -f "$MILJOFIL" ]; then
    grep -v "^$n=" "$MILJOFIL" >> "$tmp" || true
  fi
  printf "%s='%s'\n" "$n" "$v" >> "$tmp"
  mv -f "$tmp" "$MILJOFIL"
  chmod 600 "$MILJOFIL"
}

# En rolles DSN-er hører sammen: de deler ETT passord i databasen. Roteres
# passordet, må ALLE rollens linjer skrives på nytt — ellers står søskenlinja
# igjen med det gamle og slutter å virke.
#
# Rekkefølgen er bevisst: passordet settes i databasen FØRST, deretter
# skrives fila. Blir vi avbrutt imellom, har rollen et nytt passord mens fila
# har et gammelt — og det er nettopp derfor `verifiser_og_reparer` finnes:
# neste kjøring stoler ikke på at nøkkelnavnene finnes, den prøver å koble
# til. En tilstandsmaskin som bare sjekker at et navn er til stede, kan ikke
# oppdage at verdien er feil.
sikre_rolle_dsn() {
  local rolle="$1" tving="${TVING_ROTASJON:-0}"; shift
  local mangler=0 par n pw
  for par in "$@"; do
    har_nokkel "${par%%=*}" || mangler=1
  done
  # NB: skrevet som if, ikke som «A && B && return». Under `set -e` avslutter
  # en slik kjede hele skriptet naar den er usann — funnet av CI-testen i
  # foerste kjoering, og det ville rammet produksjonsskriptet likt.
  if [ "$mangler" -eq 0 ] && [ "$tving" -eq 0 ]; then
    return 0
  fi

  pw=$(openssl rand -hex 24)
  rotere_passord "$rolle" "$pw" || return 1
  for par in "$@"; do
    n="${par%%=*}"
    sett_nokkel "$n" "host=127.0.0.1 dbname=${par#*=} user=$rolle password=$pw"
  done
  echo "  roterte passord for $rolle og skrev $# DSN-er samlet"
}

# Sannhetsprøven: virker DSN-ene som står i fila? Gjør de ikke det, er rolle
# og fil ute av takt (f.eks. etter et avbrudd midt i rotasjonen), og rollens
# nøkler skrives på nytt med et ferskt passord.
verifiser_og_reparer() {
  local rolle="$1"; shift
  local par ok=1
  for par in "$@"; do
    dsn_virker "$(les_nokkel "${par%%=*}")" || ok=0
  done
  if [ "$ok" -eq 0 ]; then
    echo "  DSN for $rolle virker ikke — rolle og miljøfil er ute av takt, reparerer"
    TVING_ROTASJON=1 sikre_rolle_dsn "$rolle" "$@"
    for par in "$@"; do
      dsn_virker "$(les_nokkel "${par%%=*}")" || {
        echo "  KLARTE IKKE å reparere ${par%%=*}" >&2; return 1; }
    done
  fi
  return 0
}

# Hvilket miljø verten ER. Ikke en hemmelighet, men den hører hjemme her
# fordi den er vertens egenskap, ikke releasens: samme kode ruller ut til
# staging og produksjon, og det er verten som avgjør hvilke policystatuser
# som får binde en ekte handling (`api/policyregister.tillatte_statuser`).
#
# Uten en verdi tar den funksjonen staging-standarden — `utkast` og
# `validert_pilot` binder da ekte beslutninger. Derfor SKRIVES nøkkelen på
# en fersk install, med den trygge verdien: en vert er staging til noen
# bevisst sier noe annet. Oppgradering til produksjon er en redigering av
# miljøfila (`DISPONIT_MILJO='produksjon'`) — og den overskrives aldri
# herfra, like lite som KEK-en roteres bak ryggen på noen.
sikre_miljo() {
  if har_nokkel DISPONIT_MILJO; then
    return 0
  fi
  sett_nokkel DISPONIT_MILJO staging
  echo "  satte DISPONIT_MILJO=staging (endre i $MILJOFIL for produksjon)"
}

# Én hemmelighet, generert én gang og aldri rotert automatisk.
#
# Rotasjon MÅ være en bevisst handling for disse to: bytter man DISPONIT_KEK,
# blir hver eneste lagrede unntaks-payload uleselig (DEK-ene er pakket med
# den), og bytter man DISPONIT_TOKEN_PEPPER, slutter alle utstedte tokens å
# virke samtidig. Derfor er det ingen TVING_ROTASJON her — i motsetning til
# DSN-ene, der rotasjon er billig og reparerende.
sikre_hex_hemmelighet() {
  local navn="$1" bytes="${2:-32}"
  if har_nokkel "$navn"; then
    return 0
  fi
  sett_nokkel "$navn" "$(openssl rand -hex "$bytes")"
  echo "  genererte $navn ($bytes byte)"
}

sikre_attestasjonsnokler() {
  if har_nokkel DISPONIT_ATT_NOKLER; then
    return 0
  fi
  local nokler='{' v
  for v in v_regnskap v_register v_bank v_svindel v_fordring v_dlp v_prisbok; do
    nokler="$nokler\"$v\":{\"k1\":\"$(openssl rand -hex 32)\"},"
  done
  sett_nokkel DISPONIT_ATT_NOKLER "${nokler%,}}"
}

# PR-012: MAC-registeret for menneskelige godkjenningskonvolutter. Registeret
# er en OPPSTARTSPERRE (`last_mac_register` i Tjeneste.__init__) — uten det
# nekter API-et å starte, akkurat som KEK. NØYAKTIG én 'signerer', og
# hemmeligheten må være >= 32 tegn (openssl rand -hex 32 = 64 hex-tegn).
sikre_mac_nokler() {
  if har_nokkel DISPONIT_MAC_NOKLER; then
    return 0
  fi
  sett_nokkel DISPONIT_MAC_NOKLER \
    "{\"mk1\":{\"rolle\":\"signerer\",\"hemmelighet\":\"$(openssl rand -hex 32)\"}}"
  echo "  genererte DISPONIT_MAC_NOKLER (1 signerer)"
}
