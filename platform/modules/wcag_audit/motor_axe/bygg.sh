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

# BASISPINNEN LESES OG VERIFISERES FØR BYGGET (Codex P2). Dockerfilens
# ARG har ingen standardverdi, så et bygg uten denne verdien feiler
# uansett — sjekken her finnes for å feile med et svar i stedet for en
# docker-feilmelding, og for å avvise en halvferdig fil (plassholderen)
# før den blir til et image noen tror er pinnet.
basis="$(sed 's/#.*//' "$her/basis-digest.txt" | tr -d '[:space:]')"
if ! printf '%s' "$basis" | grep -Eq '^[^@[:space:]]+@sha256:[0-9a-f]{64}$'
then
  cat >&2 <<FEIL
basis-digest.txt har ingen gyldig pinne (leste: '${basis}').

Hent og verifiser digesten, og skriv den inn i egen commit:

  docker pull mcr.microsoft.com/playwright/python:v1.49.1-noble
  docker inspect --format='{{index .RepoDigests 0}}' \\
      mcr.microsoft.com/playwright/python:v1.49.1-noble

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
