# Browser-containeren for motoren (014b §6 / 014c §2): axe-core i headless
# Chromium, uten credentials. Controlleren starter den med payloaden på
# stdin og leser resultatet på stdout — containeren har INGEN nettverks-
# eller DB-hemmeligheter, og image-digesten (sha256) er verdien
# controlleren attesterer som `miljo.container_image_digest`.
#
# Basen er Playwrights offisielle image, PINNET på digest — «:latest» er
# et løfte om at regelverket kan bytte seg selv. Digesten oppdateres
# BEVISST, i egen commit, aldri av en byggejobb. (Se oppdater-kommandoen i
# bygg.sh.)
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

# Axe-kilden bakes inn og sha256-verifiseres av kjor.py ved HVER kjøring
# (AXE_SHA256-pinnen i koden) — containeren gjør ingen nettoppslag for
# verktøyet sitt, bare mot målet.
WORKDIR /motor
COPY kjor.py /motor/kjor.py
ADD https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.3/axe.min.js /motor/axe.min.js
ENV AXE_STI=/motor/axe.min.js

# Kjøres som playwrights ubetrodde bruker, aldri root.
USER pwuser
ENTRYPOINT ["python3", "/motor/kjor.py"]
