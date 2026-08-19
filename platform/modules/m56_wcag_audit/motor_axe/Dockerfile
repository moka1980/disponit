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
#
# PINNEN HAR INGEN DEFAULT (Codex P2). Kommentaren over sto her mens
# FROM-linja brukte den MUTABLE taggen `v1.49.1-noble` — altså en påstand
# om pinning som fila selv motsa. Da registryet retargeter taggen, gir en
# ny bygging av samme `wcag-r1` andre browser- og systembiter, mens
# release-registreringen er immutabel og forutsetter reproduserbar
# artefaktidentitet: to ulike motorer under ett og samme
# `container_image_digest`.
#
# ARG-en er derfor UTEN standardverdi. Uten `--build-arg` feiler bygget,
# og det er poenget: et upinnet bygg skal ikke være mulig ved et uhell.
# `bygg.sh` leser verdien fra `basis-digest.txt` og verifiserer formen.
ARG PLAYWRIGHT_BASIS
FROM ${PLAYWRIGHT_BASIS}

# PLAYWRIGHTS PYTHON-PAKKE INSTALLERES HER (Codex P1). Basisimaget bærer
# nettleserne og systemavhengighetene deres — ikke nødvendigvis
# `playwright`-pakken selv, og `kjor.py` gjør `from playwright.sync_api
# import sync_playwright` som aller første ting i browserdelen. Manglet
# pakken, feilet HVER kjøring av motoren, og feilen ville dukket opp første
# gang en kunde bestilte en kontroll — ikke i bygget.
#
# Versjonen er BROWSER-IMAGETS EGEN: `bygg.sh` avleder den fra `BASISTAGG`
# (`…:v1.49.1-noble` → `1.49.1`) og sender den inn, så pakken og
# nettleserne ikke kan skille lag. Står den skrevet her også, er det to
# steder som må endres i takt — og det ene blir glemt.
#
# Importen kjøres i samme lag: er pakken likevel ikke brukbar, skal BYGGET
# feile, ikke den første kontrollen.
ARG PLAYWRIGHT_PAKKE
RUN test -n "${PLAYWRIGHT_PAKKE}" \
    && python3 -m pip install --no-cache-dir --break-system-packages \
        "playwright==${PLAYWRIGHT_PAKKE}" \
    && python3 -c "from playwright.sync_api import sync_playwright"

# Axe-kilden bakes inn og sha256-verifiseres av kjor.py ved HVER kjøring
# (AXE_SHA256-pinnen i koden) — containeren gjør ingen nettoppslag for
# verktøyet sitt, bare mot målet.
#
# FILA MÅ VÆRE LESBAR FOR `pwuser` (Codex P1). En `ADD` fra en URL lager
# fila root-eid med modus 0600 (Dockerfile-referansen sier det rett ut), og
# imaget bytter til `pwuser` under. `_axe_kilde()` kunne dermed ikke lese
# `AXE_STI`, og motoren døde før Playwright i det hele tatt ble startet —
# hver eneste kjøring, straks imaget lot seg bygge.
#
# Modusen settes eksplisitt for BEGGE filene, ikke bare den nedlastede:
# `COPY` arver modusen fra byggekonteksten, så en `kjor.py` som en dag
# committes uten leserett for alle ville gitt nøyaktig samme feil. Lesbart
# og ikke skrivbart (0444) — motoren skal ikke kunne endre sitt eget
# regelverk.
WORKDIR /motor
COPY kjor.py /motor/kjor.py
ADD https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.3/axe.min.js /motor/axe.min.js
RUN chmod 0444 /motor/axe.min.js && chmod 0444 /motor/kjor.py
ENV AXE_STI=/motor/axe.min.js

# Kjøres som playwrights ubetrodde bruker, aldri root.
USER pwuser
ENTRYPOINT ["python3", "/motor/kjor.py"]
