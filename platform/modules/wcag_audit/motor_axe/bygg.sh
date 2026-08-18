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
docker build -t disponit-wcag-motor "$her"
docker inspect --format='{{index .RepoDigests 0}}' disponit-wcag-motor \
  2>/dev/null || docker images --digests disponit-wcag-motor | tail -1
# Lokalt (uten push) finnes ingen RepoDigest — bruk Image-ID-digesten:
docker inspect --format='sha256-id: {{.Id}}' disponit-wcag-motor
