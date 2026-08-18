# ============================================================
# lib-opp.sh — opp.sh sine PORTER som testbare funksjoner
# (PR-009 review-runde 1, to P1).
#
# Samme mønster som lib-miljofil.sh: logikken CI kan måle bor i en lib,
# opp.sh kaller den. Begge P1-ene satt i inline-bash ingen test så.
# ============================================================

# P1-1 (runde 1) + P1 (runde 2): unit-preflight som FEILER og som er
# HELT SIDEEFFEKTFRI.
#
# Runde 1 slukte feil med `|| true`. Runde 1-fiksen installerte
# hjelperskriptene i /usr/local/lib FØR verifiseringen — og gjorde dermed
# preflighten selv til en mutasjon: en gammel, aktiv helse-timer kunne
# kjøre KANDIDATENS skript mot GAMMEL release, og et avvist deploy
# etterlot systemet delvis endret. Runde 2: verifiseringen skjer mot en
# TEMPORÆR FALSK ROT (`systemd-analyze verify --root`, systemd ≥ 252) der
# kandidatens units, kandidatens hjelperskript og en pekepinn til venv-en
# ligger — ingenting utenfor temp-katalogen røres, og roten fjernes
# uansett utfall. Målt på staging (systemd 259): gyldig unit → 0, ødelagt
# ExecStart → 1.
preflight_units() {  # <kilde-katalog> <venv-sti> <unit...>
  local kilde=$1 venv=$2; shift 2
  local rot u rc=0
  # venv-en sjekkes LESENDE her (stubben i den falske roten beviser bare
  # unit-formen, ikke at tolken finnes på ekte).
  if [ ! -x "$venv/bin/python" ]; then
    echo "PREFLIGHT FEILET: $venv/bin/python finnes ikke eller er ikke kjørbar" >&2
    return 1
  fi
  rot=$(mktemp -d) || return 1
  # Falsk rot: systemd-skjelettet (targets) som symlink, kandidatens
  # hjelperskript der unitene forventer dem, venv-en som symlink slik at
  # ExecStart-stiene løses — alt lesende mot kilden, aldri mot systemet.
  mkdir -p "$rot/etc/systemd/system" "$rot/usr/local/lib" \
           "$rot/opt/disponit" "$rot/usr/lib/systemd"
  # ABSOLUTTE symlinker inn i en falsk rot løses PÅ NYTT inne i roten og
  # blir selv-løkker («too many levels») — målt lokalt. Derfor: systemd-
  # skjelettet KOPIERES (interne alias-lenker er relative og forblir
  # inne i treet), og ExecStart-målene utenfor kandidaten STUBBES som
  # tomme kjørbare filer — preflighten beviser unit-form + at kandidatens
  # egne skript finnes og er kjørbare; at venv-en finnes på ekte sjekkes
  # av opp.sh som egen, lesende forhåndssjekk.
  cp -a /usr/lib/systemd/system "$rot/usr/lib/systemd/system"
  # Vertens systemd-tre kan inneholde DØDE alias-symlinker (staging har
  # dracut-*-lenker uten mål). Kjørende systemd ignorerer dem; verify i
  # den falske roten nekter å laste grafen og feiler VÅRE units på
  # naboens lik. De beskjæres fra KOPIEN — aldri fra verten.
  find "$rot/usr/lib/systemd/system" -xtype l -delete
  stub() { install -D -m 755 /dev/null "$rot$1"; }
  stub /opt/disponit/.venv/bin/python
  stub /usr/bin/install
  # Kandidatens deploy-tre KOPIERES (absolutt symlink ville løkket, som
  # over) — backup-unitens ExecStart peker på aktiv/deploy/staging/.
  mkdir -p "$rot/opt/disponit/aktiv/deploy"
  cp -a "$kilde/deploy/staging" "$rot/opt/disponit/aktiv/deploy/staging"
  if [ -f "$kilde/deploy/staging/helse-sjekk.sh" ]; then
    install -m 755 "$kilde/deploy/staging/helse-sjekk.sh" \
        "$rot/usr/local/lib/disponit-helse-sjekk"
  fi
  if [ -f "$kilde/deploy/staging/restart-helper.sh" ]; then
    install -m 755 "$kilde/deploy/staging/restart-helper.sh" \
        "$rot/usr/local/lib/disponit-restart-helper"
  fi
  # ALLE kandidat-units kopieres inn FØR noen verifiseres: en .socket
  # verifiseres mot sin parede .service, og grafen må se hele settet —
  # funnet av gaten selv på staging (socket felt fordi tjenesten manglet
  # i roten).
  for u in "$@"; do
    if [ ! -f "$kilde/deploy/staging/$u" ]; then
      echo "PREFLIGHT FEILET: $kilde/deploy/staging/$u finnes ikke" >&2
      rc=1
      continue
    fi
    cp "$kilde/deploy/staging/$u" "$rot/etc/systemd/system/$u"
  done
  [ $rc -eq 0 ] || { rm -rf "$rot"; return $rc; }
  for u in "$@"; do
    if ! systemd-analyze verify --root="$rot" \
         "$rot/etc/systemd/system/$u"; then
      echo "PREFLIGHT FEILET: $u" >&2
      rc=1
    fi
  done
  rm -rf "$rot"
  return $rc
}

# 039 (Codex P2): M-37s rolleskille avgjøres to steder, og de MÅ bygge på
# samme fakta. Migrasjonen nøkler EXECUTE på `ventende_overtakelseskonflikter`
# til om rollen `disponit_arbeider` FINNES i basen (finnes den, er den eneste
# mottaker og runtime er REVOKET); `opp.sh` valgte m37-unittens legitimasjon
# på noe annet — om DISPONIT_ARBEIDER_URL er SATT i miljøfilen. Er rollen der
# uten variabelen (en halvferdig rolleutrulling), kobler arbeideren seg opp
# som `disponit` mot nettopp funksjonen runtime mistet, og HVER overtakelse
# blir stående uten sak. Motsatt vei — variabelen satt, rollen borte — kan
# arbeideren ikke autentisere i det hele tatt.
#
# Verdikten er derfor ENIGHET, ikke en av de to påstandene alene, og den er
# en ren funksjon så matrisen kan måles uten en base. Ukjente verdier er et
# avvik: porten er fail-closed.
vurder_arbeiderskille() {  # <dsn_satt ja|nei> <rolle_finnes ja|nei>
  case "$1:$2" in
    ja:ja|nei:nei) return 0 ;;
    *) return 1 ;;
  esac
}

# PR-009b review-runde 1: den eksterne helseproben må kreve EKSAKT forventet
# status (normalt 200). Første utgave avviste bare `000` (ingen tilkobling),
# så 421/500/502/503 — nettopp de transport- og upstream-feilene proben
# finnes for — ble godkjent som «transport oppe». Verdikten er skilt ut som
# en ren funksjon slik at matrisen kan måles uten en server.
vurder_helsekode() {  # <kode> <forventet> -> 0 kun ved EKSAKT match
  [ -n "$1" ] && [ "$1" = "$2" ]
}

# P1-2: rollback-dommen felles over UNIONEN av basene. Første utgave lot
# siste iterasjon (TESTbasen) overskrive rapporten — en forward-only-
# migrasjon kjørt KUN i runtime-basen ville dermed blitt rapportert som
# rollback-kompatibel. Nå: hver base melder sitt, og dommen er
# konservativ — én ny migrasjon i ÉN base er nok til FORBUDT.
#
# Input: par av «basenavn utskriftsfil» (migrer.py-utskrift per base).
# Skriver per-base-linjer til stdout og setter den globale variabelen
# NYE_MIGRASJONER til unionen (tom = ingen nye noe sted).
vurder_migrasjoner() {
  NYE_MIGRASJONER=""
  while [ "$#" -ge 2 ]; do
    local base=$1 fil=$2; shift 2
    local kjort
    kjort=$(sed -n 's/^migrasjoner kjørt: //p' "$fil" | tail -1)
    echo "    $base: ${kjort:-ukjent}"
    case "$kjort" in
      ""|*ingen*) : ;;
      *) NYE_MIGRASJONER="${NYE_MIGRASJONER:+$NYE_MIGRASJONER; }$base: $kjort" ;;
    esac
  done
}
