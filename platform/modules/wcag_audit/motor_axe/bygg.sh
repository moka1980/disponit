#!/usr/bin/env bash
# Bygg browser-containeren og skriv image-digesten — verdien controlleren
# attesterer som `miljo.container_image_digest`. Wrapper-kommandoen for
# DISPONIT_WCAG_MOTOR blir da:
#
#   podman run --rm -i --network host --cap-drop ALL \
#       --security-opt no-new-privileges disponit-wcag-motor@<digest>
#
# ROOTLESS podman, ikke docker (Codex P1): arbeideren som starter denne
# kommandoen er nettvendt, og `docker`-gruppemedlemskapet den ellers
# trengte er root på verten. Se disponit-wcag-audit.service. Bygget her
# kjøres av ops, ikke av arbeideren, og bruker docker som før.
#
# (`--network host` fordi testnettstedet på staging er loopback-bundet;
# motoren har uansett ingen credentials å misbruke — egressvakten ligger
# i kjor.py, som SELV slår opp målet og avviser hele forespørselen om
# navnet peker på en ikke-offentlig adresse. Uten den kontrollen ville
# `--network host` gjort tenantens egen DNS til en vei inn til vertens
# loopback-tjenester og skymetadata.)
#
# STAGING-FIXTUREN reiser i KOMMANDOEN, aldri i miljøet: Kommandomotor
# allowlister motormiljøet, så `MOTOR_TLS_USIKKER`/`MOTOR_VERTSKART`
# settes som `docker run -e ...` (eller `env VAR=... python kjor.py`
# lokalt) i sjekklisterundens motorkommando — driftens unit-filer kjenner
# dem ikke.
set -euo pipefail
her="$(cd "$(dirname "$0")" && pwd)"

# TAGGEN STÅR ÉTT STED. Den er ikke pinnen — den er navnet vi HENTER pinnen
# fra, én gang, bevisst. Sto den i tre kommentarer og i feilmeldingen, kunne
# `pin`-kommandoen under hentet digesten for en annen tagg enn den repoet
# tror den pinner.
BASISTAGG="mcr.microsoft.com/playwright/python:v1.49.1-noble"
PINNEFIL="$her/basis-digest.txt"

les_pinne() { sed 's/#.*//' "$PINNEFIL" | tr -d '[:space:]'; }
er_pinne() {
  printf '%s' "$1" | grep -Eq '^[^@[:space:]]+@sha256:[0-9a-f]{64}$'
}

# `bygg.sh pin` FYLLER PLASSHOLDEREN (Codex P1). Den forrige runden gjorde
# pinnen til en HÅNDHEVET betingelse, men lot verdien stå som en
# plassholder — og siden `bygg.sh` med rette avviser den, kunne en FERSK
# utrulling ikke bygge motoren i det hele tatt (fase 1 i sjekklisten). En
# håndhevet pinne uten verdi er en stengt dør, ikke en låst dør.
#
# Verdien kan bare hentes med nett og en registry-klient, så den kan ikke
# ligge i repoet fra vår side. Det den KAN være, er én verifiserbar
# kommando i stedet for en manuell klipp-og-lim fra et dokumentasjonsavsnitt:
#
#   bash bygg.sh pin      # pull + les RepoDigest + skriv PINNEFIL
#   git add -p && git commit   # verdien inn i EGEN commit, som før
#
# Kommandoen bygger INGENTING og committer ingenting: den skriver bare
# fila, og formkontrollen under er den samme porten et bygg møter. At
# verdien blir stående i historikken er fortsatt et menneskes handling.
if [ "${1:-}" = "pin" ]; then
  docker pull "$BASISTAGG"
  ny="$(docker inspect --format='{{index .RepoDigests 0}}' "$BASISTAGG")"
  if ! er_pinne "$ny"; then
    echo "docker ga ingen RepoDigest for $BASISTAGG (fikk: '$ny')." >&2
    echo "Imaget må være HENTET fra registryet — et lokalt bygget image" >&2
    echo "har ingen registry-digest å pinne mot." >&2
    exit 1
  fi
  # Kommentarhodet beholdes; bare verdilinja byttes.
  tmp="$(mktemp)"
  sed '/^[^#]/d' "$PINNEFIL" > "$tmp"
  printf '%s\n' "$ny" >> "$tmp"
  mv "$tmp" "$PINNEFIL"
  echo "pinnet: $ny"
  echo "Verdien er skrevet til basis-digest.txt. Commit den for seg."
  exit 0
fi

# BASISPINNEN LESES OG VERIFISERES FØR BYGGET (Codex P2). Dockerfilens
# ARG har ingen standardverdi, så et bygg uten denne verdien feiler
# uansett — sjekken her finnes for å feile med et svar i stedet for en
# docker-feilmelding, og for å avvise en halvferdig fil (plassholderen)
# før den blir til et image noen tror er pinnet.
basis="$(les_pinne)"
if ! er_pinne "$basis"
then
  cat >&2 <<FEIL
basis-digest.txt har ingen gyldig pinne (leste: '${basis}').

Hent og skriv den med:

  bash $0 pin

Den henter $BASISTAGG, leser registry-digesten og skriver den inn.
Verdien skal så committes FOR SEG — den er et bevisst valg, ikke et
byggeartefakt. Vil du gjøre det for hånd, er det de samme to stegene:

  docker pull $BASISTAGG
  docker inspect --format='{{index .RepoDigests 0}}' $BASISTAGG

Et upinnet bygg er IKKE et alternativ: release-registreringen er
immutabel og forutsetter at samme wcag-rN gir samme browserbiter.
FEIL
  exit 1
fi
echo "basis: $basis"
docker build --build-arg PLAYWRIGHT_BASIS="$basis" \
  -t disponit-wcag-motor "$her"
docker inspect --format='{{index .RepoDigests 0}}' disponit-wcag-motor \
  2>/dev/null || docker images --digests disponit-wcag-motor | tail -1
# Lokalt (uten push) finnes ingen RepoDigest — bruk Image-ID-digesten:
docker inspect --format='sha256-id: {{.Id}}' disponit-wcag-motor
