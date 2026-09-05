#!/usr/bin/env bash
# ============================================================
# Disponit opp.sh — automatisert utrulling (PR-009 v2 §4 + v3 §3 + V1).
#
# Én kommando: fra «ingenting kjører» eller «forrige versjon kjører» til
# «API (socket-aktivert) og M-37 kjører, overvåkes og restartes». Kjøres
# som root, UTEN TTY-krav (v5 §3) — utsteder ALDRI tokens; det gjør
# bootstrap-token.sh, interaktivt.
#
# FORWARD-ONLY (V1): migrasjonene har ingen nedvei, og 009 dropper
# `aktiv` — forrige applikasjonsversjon KAN IKKE startes mot nytt schema.
# Sekvensen er derfor vedlikeholdsvindu: stopp tjenester → migrér →
# aktiver ny release → start → verifiser. Rapporten til slutt sier
# eksplisitt hva som IKKE kan rulles tilbake, i stedet for å love en
# rollback som ikke finnes.
# ============================================================
set -euo pipefail

ROT=/opt/disponit
MILJOFIL=/etc/disponit/staging.env
LAAS=/var/lock/disponit-deploy.lock

# --- Deploylås: fail-fast, aldri kø (v2 §4) --------------------------------
exec 9>"$LAAS"
if ! flock -n 9; then
  echo "AVBRUTT: en annen utrulling holder $LAAS" >&2
  exit 1
fi

SHA=$(git -C "$ROT" rev-parse HEAD)
KILDE="$ROT/releases/$SHA"
AKTIV="$ROT/aktiv"
FORRIGE=$(readlink -f "$AKTIV" 2>/dev/null || true)

echo "== opp.sh: utrulling av $SHA =="

# --- 1. Release-katalogen STAGES (inert — endrer ingen levende sti) --------
mkdir -p "$ROT/releases"
if [ ! -d "$KILDE" ]; then
  git -C "$ROT" archive --format=tar "$SHA" | \
    ( mkdir -p "$KILDE" && tar -x -C "$KILDE" )
fi

# --- 2. PREFLIGHT — SIDEEFFEKTFRI, FØR FØRSTE AKTIVE MUTASJON (P1 rd. 2) ---
# Verifiseringen skjer i en temporær falsk rot (verify --root) med
# KANDIDATENS units og skript: /usr/local/lib røres ikke, ingen bruker
# opprettes, ingen credential skrives, ingenting stoppes. Feiler gaten,
# er systemet BEVISELIG uendret — en gammel timer kan aldri ha sett
# kandidatkode.
. "$KILDE/deploy/staging/lib-opp.sh"
UNITS="disponit-api.socket disponit-api.service disponit-m37.service
disponit-helse.service disponit-helse.timer
disponit-rydd-pending.service disponit-rydd-pending.timer
disponit-backup.service disponit-backup.timer
disponit-domenerevalidering.service disponit-domenerevalidering.timer
disponit-artefaktrydding.service disponit-artefaktrydding.timer
disponit-evidensreaper.service disponit-evidensreaper.timer
disponit-plan.service disponit-plan.timer
disponit-wcag-audit.service
disponit-domeneverifisering.service disponit-domeneverifisering.timer
disponit-varselsender.service disponit-varselsender.timer
disponit-m57-utsending.service disponit-m57-utsending.timer
disponit-backupstatus.service disponit-backupstatus.timer
disponit-selvtest.service disponit-selvtest.timer
disponit-kvalitetsprofil.service disponit-kvalitetsprofil.timer
disponit-lagermaaling.service disponit-lagermaaling.timer
disponit-begrepssveip.service disponit-begrepssveip.timer
disponit-tilgangssveip.service disponit-tilgangssveip.timer
disponit-personvernsveip.service disponit-personvernsveip.timer
disponit-compliancesveip.service disponit-compliancesveip.timer
disponit-avstemmingssveip.service disponit-avstemmingssveip.timer
disponit-henvendelsessveip.service disponit-henvendelsessveip.timer
disponit-onboardingsveip.service disponit-onboardingsveip.timer
disponit-fordringssveip.service disponit-fordringssveip.timer
disponit-leverandorsveip.service disponit-leverandorsveip.timer
disponit-fakturasveip.service disponit-fakturasveip.timer
disponit-prosjektsveip.service disponit-prosjektsveip.timer
disponit-prisboksveip.service disponit-prisboksveip.timer
disponit-lagersveip.service disponit-lagersveip.timer
disponit-kontovaktsveip.service disponit-kontovaktsveip.timer
disponit-betalingssveip.service disponit-betalingssveip.timer
disponit-adressesveip.service disponit-adressesveip.timer
disponit-lonnssveip.service disponit-lonnssveip.timer
disponit-kampanjesveip.service disponit-kampanjesveip.timer
disponit-motpartssveip.service disponit-motpartssveip.timer
disponit-sanksjonssveip.service disponit-sanksjonssveip.timer
disponit-anbudssveip.service disponit-anbudssveip.timer
disponit-tilskuddssveip.service disponit-tilskuddssveip.timer
disponit-merkevaresveip.service disponit-merkevaresveip.timer
disponit-ehfsveip.service disponit-ehfsveip.timer
disponit-tollkodesveip.service disponit-tollkodesveip.timer
disponit-myndighetssveip.service disponit-myndighetssveip.timer
disponit-postjournalsveip.service disponit-postjournalsveip.timer
disponit-hmssveip.service disponit-hmssveip.timer
disponit-likviditetssveip.service disponit-likviditetssveip.timer
disponit-prognosesveip.service disponit-prognosesveip.timer
disponit-optimalisatorsveip.service disponit-optimalisatorsveip.timer
disponit-motesveip.service disponit-motesveip.timer
disponit-innholdssveip.service disponit-innholdssveip.timer
disponit-telefonisveip.service disponit-telefonisveip.timer
disponit-sveipestatus.service disponit-sveipestatus.timer"
# Deploy-portene kjøres OGSÅ her, som preflight — FØR noe stoppes (18/8:
# porten som bare kjørte etter migrasjonene fant rødt da gamle release
# alt var ubootbar, og deployen etterlot tjenesten NEDE). Rød port her =
# avbrutt med alt urørt. Skjema porten ikke kjenner ennå (ny migrasjon)
# er stille i preflight og håndheves i hovedkjøringen i steg 6b.
# Miljøfila leses i SUBSHELL, som miljøgaten i steg 4: den skal ikke
# lekke inn i resten av preflighten.
if ! (set -a; . "$MILJOFIL"; set +a; cd "$KILDE" && \
   DATABASE_URL="$DATABASE_URL" DISPONIT_REPO="$KILDE" \
   "$ROT/.venv/bin/python" deploy/staging/deployport-modultyper.py --preflight); then
  echo "AVBRUTT: deploy-port rød i preflight — systemet er urørt; forrige"
  echo "release kjører som før. Rett registeret/typen først."
  exit 1
fi

if ! preflight_units "$KILDE" "$ROT/.venv" $UNITS; then
  echo "AVBRUTT: preflight feilet — systemet er urørt; forrige release"
  echo "kjører som før."
  exit 1
fi
# PR-015: revalideringsarbeideren importerer dnspython LAT (enhetstestene
# injiserer egne oppslag og skal slippe DNS-avhengigheten). Baksiden er at
# unit-preflighten passerer selv om pakken mangler i venv-en, og at feilen
# først viser seg ved første timeraktivering — som en RuntimeError i
# `_txt_oppslag`, uten at ett eneste domene blir revalidert. Importen prøves
# derfor her, lesende, sammen med resten av gaten.
if ! "$ROT/.venv/bin/python" -c 'import dns.resolver' 2>/dev/null; then
  echo "AVBRUTT: dnspython mangler i $ROT/.venv — disponit-domenerevalidering"
  echo "ville startet og feilet ved første TXT-oppslag. Kjør"
  echo "deploy/staging/oppsett-postgresql.sh på nytt (den installerer den),"
  echo "og kjør så opp.sh igjen."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# Cursor P2-3 (#178, runde 4): `psql` er nå en FEILSONE-avhengighet.
# `rollbackmaal_kompatibelt` (lib-opp.sh) leser basens anvendte versjoner med
# `psql`, og #172 gjorde `selvrevers()` avhengig av nettopp den dommen. Er
# klienten ikke installert, blir dommen «umålt» — og porten er fail-closed
# med vilje, så INGEN enhet startes igjen. En manglende pakke ville dermed
# gjort en migrasjonsfeil om til en full nedetid, oppdaget inne i vinduet.
# Sjekken er lesende og hører derfor her: mangler klienten, avbrytes
# utrullingen mens systemet beviselig er urørt.
if ! command -v psql >/dev/null 2>&1; then
  echo "AVBRUTT: psql finnes ikke på verten. rollbackmaal_kompatibelt bruker"
  echo "den både i statusrapporten (steg 9) og i selv-reverseringens"
  echo "rullbakk-gate — uten den er gaten umålt, og umålt er ikke"
  echo "kompatibelt: en feil i vinduet ville latt HVER enhet bli stående"
  echo "stoppet. Installer postgresql-client og kjør opp.sh igjen."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# Cursor P2 (#178, runde 7): `timeout` er SAMME KLASSE avhengighet som `psql`.
# E1 (runde 5) ga `rollbackmaal_kompatibelt` et tosidig tak —
# `PGCONNECT_TIMEOUT` for oppkoblingen og `timeout 10` for en spørring som
# blokkerer på lås — og gjorde dermed coreutils' `timeout` til en del av
# feilsonen. Mangler den, feiler kommandoen, `bv` blir tom, dommen blir
# «umålt», og fail-closed betyr at HVER enhet blir stående stoppet. `psql`
# ble preflightet nettopp for å unngå at en manglende pakke gjør en
# migrasjonsfeil om til full nedetid; taket som ble lagt oppå den kan ikke
# stå ugatet ved siden av.
if ! command -v timeout >/dev/null 2>&1; then
  echo "AVBRUTT: timeout (coreutils) finnes ikke på verten."
  echo "rollbackmaal_kompatibelt leser basen med 'timeout 10 psql' — uten"
  echo "timeout feiler kommandoen, dommen blir umålt, og umålt er ikke"
  echo "kompatibelt: en feil i vinduet ville latt HVER enhet bli stående"
  echo "stoppet. Installer coreutils og kjør opp.sh igjen."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# PR-015: driftstimerne over kaller funksjoner som migrasjon 019 kun granter
# til `disponit_domains_admin` og `disponit_domener`. En EKSISTERENDE
# installasjon har ingen DISPONIT_DOMAINS_URL før `oppsett-postgresql.sh` er
# kjørt på nytt; en stille fallback til runtime-DSN-en (`disponit`) ville
# startet begge timerne rett i `permission denied` og latt revalidering og
# rydding stå ute av drift uten at utrullingen sa fra. Gaten hører derfor
# hjemme her, FØR første mutasjon: feiler den, er systemet beviselig urørt.
# Lesingen skjer i en subshell, så miljøfilen ikke lekker inn i preflighten.
# Samme port for varselsenderens DSN (Codex P1 på #68): min første utgave
# kontrollerte den nede ved `skriv_cred` — MIDT i den muterende fasen, etter
# at tjenester var stoppet og credentials skrevet. En «preflight» som feiler
# etter første mutasjon er ingen preflight; den etterlater et halvt utrullet
# system med beskjed om at ingenting skulle vært rørt. Porten hører hjemme
# HER, der DOMAINS-porten allerede står, av nøyaktig samme grunn.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_VARSEL_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_VARSEL_URL mangler i $MILJOFIL."
  echo "Varselsenderen (disponit-varselsender.timer) trenger sin egen"
  echo "DB-rolle (disponit_varselsender) — uten DSN-en ville den fått"
  echo "API-ets, som ikke har EXECUTE på senderfunksjonene. Kjør"
  echo "deploy/staging/oppsett-postgresql.sh (idempotent) først; den"
  echo "oppretter rollen og skriver DSN-en. Kjør så opp.sh igjen."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# 048 (#108): plan-arbeiderens DSN — samme kontrakt som varselsenderens.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_PLAN_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_PLAN_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_plan_arbeider og skriver DSN-en til miljøfila."
  exit 1
fi

# 090/091: driftstatusens og selvtestens DSN-er — samme kontrakt og samme
# plassering som varselsenderens over. Hver av de to rollene har NØYAKTIG
# én rettighet (EXECUTE på sin skrivedør); en stille fallback til
# runtime-DSN-en ville startet begge jobbene rett i `permission denied`,
# fordi migrer.py REVOKEr nettopp de dørene fra den konfigurerte
# runtime-rollen. Porten står her, FØR første mutasjon: feiler den, er
# systemet beviselig urørt.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_DRIFTSTATUS_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_DRIFTSTATUS_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_driftstatus og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_SELVTEST_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_SELVTEST_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_selvtest og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# 093 (M-4): retensjonsmålingens DSN — samme kontrakt og samme grunn.
# `disponit_lagermaaler` har NULL tabellrettigheter og EXECUTE på NØYAKTIG
# én funksjon; en stille fallback til runtime-DSN-en ville startet jobben
# rett i «permission denied», fordi migrasjonen aldri granter måledøren
# til runtime.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_LAGERMAALER_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_LAGERMAALER_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_lagermaaler og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi

# 092 (M-3): profileringsjobbens DSN — samme kontrakt og samme
# plassering som driftsstatus/selvtest over. Rollen har NØYAKTIG én
# rettighet (EXECUTE på `m3_profiler`), og `migrer.py` REVOKEr den samme
# funksjonen fra runtime-rollen: en stille fallback til runtime-DSN-en
# ville startet jobben rett i `permission denied`. Porten står her, FØR
# første mutasjon — feiler den, er systemet beviselig urørt.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_KVALITETSMAALER_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_KVALITETSMAALER_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_kvalitetsmaaler og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi

# 095 (M-9): begrepssveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. Uten DSN-en ville uniten startet, lest ingenting og gitt
# exit 2 hver natt — og en utløpssveip som ikke kjører er en ordliste
# som eldes stille. Porten står her, FØR første mutasjon: feiler den,
# er systemet beviselig urørt.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_KUNNSKAPSSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_KUNNSKAPSSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_kunnskapssveip og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi

# 097 (M-12): gjennomgangssveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. Uten DSN-en ville uniten startet, lest ingenting og gitt
# exit 2 hver natt — og en gjennomgangssveip som ikke kjører er et
# tilgangsregister som eldes stille, altså nøyaktig tilstanden modulen
# finnes for å gjøre synlig. Porten står her, FØR første mutasjon:
# feiler den, er systemet beviselig urørt.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_TILGANGSSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_TILGANGSSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_tilgangssveip og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi

# 099 (M-30): fristsveipen har sin EGEN rolle med nøyaktig én EXECUTE.
# Uten DSN-en ville uniten startet, lest ingenting og gitt exit 2 hver
# natt — og en fristsveip som ikke kjører er et forespørselsregister der
# en oversittet innsynsfrist aldri blir et funn. Det er et lovbrudd
# ingen får vite om. Porten står her, FØR første mutasjon: feiler den,
# er systemet beviselig urørt.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_PERSONVERNSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_PERSONVERNSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_personvernsveip og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# 100 (M-34): etterprøvingssveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. Uten DSN-en ville uniten startet, lest ingenting og gitt
# exit 2 hver natt — og en etterprøvingssveip som ikke kjører er et
# kontrollregister som eldes stille, altså nøyaktig tilstanden modulen
# finnes for å gjøre synlig. Porten står her, FØR første mutasjon:
# feiler den, er systemet beviselig urørt.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_COMPLIANCESVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_COMPLIANCESVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_compliancesveip og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# 101 (M-13): avstemmingssveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. Uten DSN-en ville uniten startet, lest ingenting og gitt
# exit 2 hver natt — og en avstemmingssveip som ikke kjører er et
# register som eldes stille, altså nøyaktig tilstanden modulen finnes
# for å gjøre synlig. Porten står her, FØR første mutasjon: feiler den,
# er systemet beviselig urørt.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_AVSTEMMINGSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_AVSTEMMINGSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_avstemmingssveip og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# 102 (M-17): henvendelsessveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. Uten DSN-en ville uniten startet, lest ingenting og gitt
# exit 2 hver natt — og en henvendelsessveip som ikke kjører er en
# kundeservicekø som eldes stille. Porten står FØR første mutasjon.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_HENVENDELSESVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_HENVENDELSESVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_henvendelsessveip og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# 103 (M-18): onboardingsveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. En onboardingsveip som ikke kjører er en ny kunde som blir
# liggende uten at noen ser det. Porten står FØR første mutasjon.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_ONBOARDINGSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_ONBOARDINGSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_onboardingsveip og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# 104 (M-23): fordringssveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. En fordringssveip som ikke kjører er utestående som eldes
# uten at noen ser det — og for penger er tiden selve skaden.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_FORDRINGSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_FORDRINGSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_fordringssveip og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# 105 (M-24): leverandørsveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. En leverandørsveip som ikke kjører er en avtale som glir ut,
# en pris som stiger og et SLA ingen måler — alle tre koster penger i
# det stille.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_LEVERANDORSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_LEVERANDORSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_leverandorsveip og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# 106 (M-14): fakturasveipen har sin EGEN rolle med nøyaktig én EXECUTE.
# En fakturasveip som ikke kjører er en faktura som forfaller mens den
# venter — den dyreste raden i registeret.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_FAKTURASVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_FAKTURASVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_fakturasveip og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# 107 (M-25): prosjektsveipen har sin EGEN rolle med nøyaktig én EXECUTE.
# En stille prosjektsveip er et budsjett som sprekker uten at noen ser
# det.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_PROSJEKTSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_PROSJEKTSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_prosjektsveip og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# 111 (M-41): betalingssveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. En stille betalingssveip er penger som verken er kommet
# eller etterlyst.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_BETALINGSSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_BETALINGSSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_betalingssveip og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi

# 112 (M-19): adressesveipen har sin EGEN rolle med nøyaktig én EXECUTE.
# En stille adressesveip er leveranser ingen har sett på.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_ADRESSESVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_ADRESSESVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_adressesveip og skriver DSN-en til miljøfila."
  exit 1
fi

# 119 (M-51): tilskuddssveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. En stille tilskuddssveip er søknadsfrister ingen har sett.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_TILSKUDDSSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_TILSKUDDSSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_tilskuddssveip og skriver DSN-en til miljøfila."
  exit 1
fi

# 120 (M-55): merkevaresveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. En stille merkevaresveip er forvekslinger ingen har sett på.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_MERKEVARESVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_MERKEVARESVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_merkevaresveip og skriver DSN-en til miljøfila."
  exit 1
fi

# 121 (M-54): EHF-sveipen har sin EGEN rolle med nøyaktig én EXECUTE.
# En stille EHF-sveip er en standard som er gått ut uten at noen har
# sett det — og en foreldet regel ser ut som en riktig regel.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_EHFSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_EHFSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_ehfsveip og skriver DSN-en til miljøfila."
  exit 1
fi

# 124 (M-50): postjournalsveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. En stille postjournalsveip er navngitte privatpersoner
# oppbevart etter vår egen slettefrist, uten at noen har sett det.
if ! ( set -a; . "$MILJOFIL"; set +a; \
       [ -n "${DISPONIT_POSTJOURNALSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_POSTJOURNALSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_postjournalsveip og skriver DSN-en til miljøfila."
  exit 1
fi

# 127 (M-53): HMS-sveipen har sin EGEN rolle med nøyaktig én EXECUTE.
# OG HER ER STILLHETEN SELVE SKADEN, som i M-47: et avvik ingen har
# gjort noe med er nøyaktig det modulen ble bygget for å fange. En
# stille HMS-sveip er et menneske som meldte fra, og ingen som svarte.
if ! ( set -a; . "$MILJOFIL"; set +a; \
       [ -n "${DISPONIT_HMSSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_HMSSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_hmssveip og skriver DSN-en til miljøfila."
  exit 1
fi

# 128 (M-15): likviditetssveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. En stille likviditetssveip er en kontantbane som går under
# null uten at noen får vite det — og en prognose som står umålt, som
# er nøyaktig det klyngen finnes for å hindre.
if ! ( set -a; . "$MILJOFIL"; set +a; \
       [ -n "${DISPONIT_LIKVIDITETSSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_LIKVIDITETSSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_likviditetssveip og skriver DSN-en til miljøfila."
  exit 1
fi

# 130 (M-33): prognosesveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. En stille prognosesveip er en modell som taper for «samme
# som forrige uke» uten at noen får vite det — og som fortsetter å bli
# lest som analyse. Deployen sto død i to uker etter M-47 nettopp
# fordi tre slike DSN-er manglet i miljøfila; forhåndssjekken ER den
# lærdommen.
if ! ( set -a; . "$MILJOFIL"; set +a; \
       [ -n "${DISPONIT_PROGNOSESVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_PROGNOSESVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_prognosesveip og skriver DSN-en til miljøfila."
  exit 1
fi

# 132 (M-36): optimalisatorsveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. En stille optimalisatorsveip er en rangering ingen har målt
# effekten av — og som fortsetter å bli lest som en anbefaling.
if ! ( set -a; . "$MILJOFIL"; set +a; \
       [ -n "${DISPONIT_OPTIMALISATORSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_OPTIMALISATORSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_optimalisatorsveip og skriver DSN-en til miljøfila."
  exit 1
fi

# 133 (M-7): møtesveipen har sin EGEN rolle med nøyaktig én EXECUTE. En
# stille møtesveip er et møte ingen skrev referat fra, og en aksjon
# ingen gjorde.
if ! ( set -a; . "$MILJOFIL"; set +a; \
       [ -n "${DISPONIT_MOTESVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_MOTESVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_motesveip og skriver DSN-en til miljøfila."
  exit 1
fi

# 134 (M-20): innholdssveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. En stille innholdssveip er en udokumentert påstand som blir
# stående på forsiden.
if ! ( set -a; . "$MILJOFIL"; set +a; \
       [ -n "${DISPONIT_INNHOLDSSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_INNHOLDSSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_innholdssveip og skriver DSN-en til miljøfila."
  exit 1
fi

# 135 (M-43): telefonisveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. En stille telefonisveip er en eskalering ingen tok, mens den
# andre parten fikk beskjed om at noen skulle ta over.
if ! ( set -a; . "$MILJOFIL"; set +a; \
       [ -n "${DISPONIT_TELEFONISVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_TELEFONISVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_telefonisveip og skriver DSN-en til miljøfila."
  exit 1
fi

# 123 (M-47): myndighetssveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. OG HER ER STILLHETEN SELVE SKADEN: en frist som går uten
# innsending er nøyaktig det modulen ble bygget for å hindre. En stille
# M-47 er verre enn ingen M-47 — derfor er preflighten hard, ikke en
# advarsel.
if ! ( set -a; . "$MILJOFIL"; set +a; \
       [ -n "${DISPONIT_MYNDIGHETSSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_MYNDIGHETSSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_myndighetssveip og skriver DSN-en til miljøfila."
  exit 1
fi

# 122 (M-52): tollkodesveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. En stille tollkodesveip er et regelverk som er avviklet
# uten at noen har sett det — og en kode mot en avviklet nomenklatur
# er et velformet og galt svar.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_TOLLKODESVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_TOLLKODESVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_tollkodesveip og skriver DSN-en til miljøfila."
  exit 1
fi

# 118 (M-46): anbudssveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. En stille anbudssveip er frister ingen har sett — og en
# frist som passerer er den ene feilen som ikke kan rettes dagen etter.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_ANBUDSSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_ANBUDSSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_anbudssveip og skriver DSN-en til miljøfila."
  exit 1
fi

# 117 (M-49): sanksjonssveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. En stille sanksjonssveip er uavklarte treff ingen har sett
# på — og et treff ingen har sett på er ikke et vern, det er en
# udokumentert risiko.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_SANKSJONSSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_SANKSJONSSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_sanksjonssveip og skriver DSN-en til miljøfila."
  exit 1
fi

# 116 (M-48): motpartssveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. En stille motpartssveip er motparter ingen har vurdert — og
# forlatte reservasjoner ingen rydder, som er den ene raden sveipen
# endrer utenfor funntabellen.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_MOTPARTSSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_MOTPARTSSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_motpartssveip og skriver DSN-en til miljøfila."
  exit 1
fi

# 113 (M-39): lønnssveipen har sin EGEN rolle med nøyaktig én EXECUTE.
# En stille lønnssveip er overtid ingen har sett.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_LONNSSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_LONNSSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_lonnssveip og skriver DSN-en til miljøfila."
  exit 1
fi

# 114 (M-44): kampanjesveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. En stille kampanjesveip er markedsføring ingen har sett på.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_KAMPANJESVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_KAMPANJESVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_kampanjesveip og skriver DSN-en til miljøfila."
  exit 1
fi
# 110 (M-42): kontovaktsveipen har sin EGEN rolle med nøyaktig én
# EXECUTE. En stille kontovaktsveip er en kontoendring ingen ser.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_KONTOVAKTSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_KONTOVAKTSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_kontovaktsveip og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# 109 (M-27): lagersveipen har sin EGEN rolle med nøyaktig én EXECUTE.
# En stille lagersveip er en vare som går tom uten at noen merker det.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_LAGERSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_LAGERSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_lagersveip og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# 108 (M-26): prisboksveipen har sin EGEN rolle med nøyaktig én EXECUTE.
# En stille prisboksveip er en pris som går ut uten at noen merker det.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_PRISBOKSVEIP_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_PRISBOKSVEIP_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_prisboksveip og skriver DSN-en til miljøfila."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi

if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_DOMAINS_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_DOMAINS_URL mangler i $MILJOFIL."
  echo "Driftstimerne (disponit-domenerevalidering, disponit-artefaktrydding)"
  echo "ville da fått runtime-DSN-en, som migrasjon 019 ikke granter"
  echo "revaliderings- eller ryddefunksjonene til — begge timerne ville"
  echo "startet i 'permission denied'. Kjør deploy/staging/oppsett-postgresql.sh"
  echo "på nytt (den oppretter rollen disponit_domener og skriver DSN-en),"
  echo "og kjør så opp.sh igjen."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# 039 (Codex P2): ARBEIDERPORTEN — grantet og legitimasjonen må bygge på
# SAMME invariant. Migrasjonen nøkler EXECUTE på
# `ventende_overtakelseskonflikter` til om rollen `disponit_arbeider` finnes i
# basen; `skriv_cred m37` under velger legitimasjonen på om
# DISPONIT_ARBEIDER_URL er satt. De to kan avvike, og avviket er STILLE:
# finnes rollen uten variabelen, kjører m37 som `disponit` mot en funksjon
# runtime nettopp mistet, og hver domeneovertakelse blir stående i
# `avklaring_kreves` uten sak. Fallbacken i `skriv_cred`-linjen er derfor bare
# gyldig når rollen HELLER IKKE finnes — og det er nøyaktig det denne porten
# krever. Lesende (`pg_roles`), i subshell, FØR første mutasjon.
if ! ARBEIDERROLLE=$( set -a; . "$MILJOFIL"; set +a
      "$ROT/.venv/bin/python" -c '
import os, psycopg
with psycopg.connect(os.environ["DATABASE_URL"]) as c:
    print("ja" if c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s",
                            ("disponit_arbeider",)).fetchone() else "nei")
' 2>&1 ); then
  echo "AVBRUTT: kunne ikke lese rollekatalogen (pg_roles) i runtime-basen."
  echo "$ARBEIDERROLLE" | tail -3
  echo "Porten avgjør om m37-unitten skal ha den dedikerte arbeiderrollen"
  echo "eller runtime-DSN-en, og den gjetter ikke."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
ARBEIDER_DSN_SATT=$( set -a; . "$MILJOFIL"; set +a
  if [ -n "${DISPONIT_ARBEIDER_URL:-}" ]; then echo ja; else echo nei; fi )
if ! vurder_arbeiderskille "$ARBEIDER_DSN_SATT" "$ARBEIDERROLLE"; then
  echo "AVBRUTT: M-37s rolleskille er halvferdig — basen og $MILJOFIL sier"
  echo "ikke det samme (rollen disponit_arbeider finnes: $ARBEIDERROLLE,"
  echo "DISPONIT_ARBEIDER_URL satt: $ARBEIDER_DSN_SATT)."
  if [ "$ARBEIDERROLLE" = ja ]; then
    echo "Rollen finnes, så migrasjon 039 har GITT den EXECUTE på"
    echo "ventende_overtakelseskonflikter og REVOKET den fra disponit. Uten"
    echo "DSN-en ville m37-unitten koblet seg opp som disponit og feilet på"
    echo "hver eneste konfliktdrenering — mens hver domeneovertakelse ble"
    echo "stående uten M-37-sak."
  else
    echo "DSN-en peker på en rolle som ikke finnes i basen: m37-unitten"
    echo "ville ikke kunne autentisere i det hele tatt."
  fi
  echo "Kjør deploy/staging/oppsett-postgresql.sh (idempotent) — den"
  echo "oppretter rollen OG skriver DSN-en, slik at de to følges ad — og"
  echo "kjør så opp.sh igjen."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# 041 (Codex P1): ADJUDIKATORPORTEN. Migrasjon 041 KREVER klyngerollen
# `disponit_domains_adjudicator` (§0) — den bærer RLS-policyen og SELECT-en
# adjudikatorkøen leser `unntak` gjennom. Rollen opprettes i
# `oppsett-postgresql.sh`, og denne løypa kjører IKKE det skriptet: en
# eksisterende installasjon som ikke har fått oppsettet på nytt, har ikke
# rollen. Migrasjonen stopper da av seg selv — men den kjører i steg 6, ETTER
# at tjenestene er stoppet, og etterlater et vedlikeholdsvindu som må ryddes
# for hånd. Porten hører derfor hjemme her, lesende, i subshell, FØR første
# mutasjon — samme form og samme grunn som arbeiderporten over.
#
# MEDLEMSKAPET MÅLES OGSÅ (Codex P1, runde 7): at rollen FINNES er ikke det
# samme som at den kan BRUKES. Begge API-veiene gjør `SET LOCAL ROLE
# disponit_domains_adjudicator`, og det krever at runtime-rollen `disponit`
# er medlem MED SET. Oppsettet oppretter rollen og gir medlemskapet i to
# separate psql-kall, så et avbrudd mellom dem etterlater en base der rollen
# finnes og medlemskapet ikke gjør det — og der ville en port på `pg_roles`
# alene sagt `ja`. Migrasjonens egen fallback svelger `insufficient_privilege`
# på det grantet med vilje (klyngeobjekt), så 041 kunne blitt registrert mens
# køen og attestasjonsveien feilet på hver eneste adjudikasjon.
if ! ADJUDIKATORPORT=$( set -a; . "$MILJOFIL"; set +a
      "$ROT/.venv/bin/python" -c '
import os, psycopg
with psycopg.connect(os.environ["DATABASE_URL"]) as c:
    rolle = c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s",
                      ("disponit_domains_adjudicator",)).fetchone()
    medlem = c.execute(
        "SELECT m.set_option FROM pg_auth_members m"
        "  JOIN pg_roles r ON r.oid = m.roleid"
        "  JOIN pg_roles b ON b.oid = m.member"
        " WHERE r.rolname = %s AND b.rolname = %s",
        ("disponit_domains_adjudicator", "disponit")).fetchone()
    print(("ja" if rolle else "nei") + ":"
          + ("ja" if medlem and medlem[0] else "nei"))
' 2>&1 ); then
  echo "AVBRUTT: kunne ikke lese rolle- og medlemskapskatalogen i"
  echo "runtime-basen (pg_roles / pg_auth_members)."
  echo "$ADJUDIKATORPORT" | tail -3
  echo "Porten avgjør om migrasjon 041 i det hele tatt kan kjøre, og den"
  echo "gjetter ikke."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
if [ "$ADJUDIKATORPORT" != ja:ja ]; then
  echo "AVBRUTT: adjudikatorrollen er ikke utrullet (rolle:medlemskap ="
  echo "$ADJUDIKATORPORT)."
  echo "Migrasjon 041 gir rollen disponit_domains_adjudicator RLS-policyen"
  echo "og SELECT på unntak som adjudikatorkøen leser gjennom, og BEGGE"
  echo "API-veiene gjør SET LOCAL ROLE til den — som krever at runtime-"
  echo "rollen disponit er medlem MED SET. Mangler noe av dette, ville 041"
  echo "blitt registrert som kjørt mens hver overtakelsessak sto"
  echo "uavgjørbar, uten en eneste rød indikator."
  echo "Roller og medlemskap er KLYNGEobjekter og settes i"
  echo "deploy/staging/oppsett-postgresql.sh (idempotent), aldri i en"
  echo "migrasjon — migratorrollen har verken eller skal ha CREATEROLE."
  echo "Kjør oppsett-postgresql.sh først, og kjør så opp.sh igjen."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# PR-015 (Codex P1): RESOLVERPORTEN kjøres FØR første mutasjon, ikke først ved
# timeraktivering. `skriv_cred domener DISPONIT_RESOLVERE` skrev tidligere
# hva som helst — også tom streng — og utrullingen rapporterte suksess fordi
# den bare måler API/M-37-readiness; `systemctl enable --now` på en .timer
# starter ikke oneshot-tjenesten synkront, så en ugyldig resolverkonfigurasjon
# var USYNLIG i rapporten. Hver aktivering ville da avsluttet med
# «oppstart_nektet» uten å røre databasen, og etter 72 timer uten fersk
# revalidering ville freshness-regelen ugyldiggjort ALLE domeneautorisasjoner.
# Samme parser og samme diversitetsgate som arbeideren bruker (§2.4: minst to
# resolvere hos ULIKE operatører og ULIKE nett) — ikke en kopi av regelen her,
# som kunne divergert. Ingen DNS-oppslag utføres; `resolvere()` bygger bare
# transporten og måler diversiteten. PYTHONPATH speiler unit-filene: `drift`
# fra platform/, `db` fra platform/core.
if ! RESOLVERFEIL=$( set -a; . "$MILJOFIL"; set +a
      cd "$KILDE/platform" && PYTHONPATH="$KILDE/platform/core" \
        "$ROT/.venv/bin/python" -c 'import drift.kjor_revalidering as k; k.resolvere()' 2>&1 ); then
  echo "AVBRUTT: DISPONIT_RESOLVERE er ugyldig i $MILJOFIL."
  echo "$RESOLVERFEIL" | tail -3
  echo "Formatet er navn@operator/nett=adresse, komma-separert, med minst to"
  echo "resolvere hos ULIKE operatører og ULIKE nett (§2.4). Uten dette ville"
  echo "disponit-domenerevalidering blitt aktivert, nektet oppstart ved hver"
  echo "kjøring uten å røre databasen, og utrullingen ville rapportert suksess"
  echo "— helt til 72-timersregelen ugyldiggjorde alle domeneautorisasjoner."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# PR-045 (Codex P1): MILJØPORTEN. Denne løypa installerer STAGING — den heter
# det, den leser /etc/disponit/staging.env, og docs/DEPLOY.md utpeker maskinen
# som staging-serveren. `DISPONIT_MILJO=produksjon` her ville derfor vært en
# påstand om maskinen som ikke stemmer, og den påstanden er ikke uskyldig i
# noen retning: registeret ville sluttet å la `utkast` binde beslutninger
# (altså sluttet å teste det staging er til for), og forsiden ville lovet
# «Tilgjengelig» til en besøkende på en maskin uten kundedata. Gaten står FØR
# første mutasjon, så en slik miljøfil stopper utrullingen i stedet for å bli
# skrevet inn i en credential. Uspesifisert er greit — da gjelder `staging`.
if ! ( set -a; . "$MILJOFIL"; set +a
       [ "${DISPONIT_MILJO:-staging}" = "staging" ] ); then
  echo "AVBRUTT: DISPONIT_MILJO i $MILJOFIL er ikke 'staging'."
  echo "opp.sh er staging-løypa: den ruller ut på maskinen docs/DEPLOY.md"
  echo "utpeker som staging-serveren, mens produksjon er en EGEN VPS med"
  echo "kunder og ekte data. En annen verdi her ville fått policyregisteret"
  echo "til å slutte å godta 'utkast' (staging tester da ikke lenger det den"
  echo "er til for) og forsiden til å love moduler 'Tilgjengelig' på en"
  echo "maskin uten kundedata. Fjern linjen, eller sett den til 'staging'."
  echo "Systemet er urørt; forrige release kjører som før."
  exit 1
fi
# CHECKSUM-PORTEN: kjørt historikk er byte-identisk (#172).
# 23/8: en kommentarlinje i en KJØRT migrasjon ble oppdaget av kjøreren i
# steg 6 — ETTER at tjenestene var stoppet. Prod sto nede på en
# dokumentasjonsendring. (CI-porten i test_migrasjonsfasit feller klassen
# før merge; denne er deploy-sidens halvdel — siste skanse, målt mot
# BASENS egne rader, per base.)
#
# Codex P1 (runde 1): porten sto først som «steg 4z», etter steg 3–4. Den
# var da FØR vinduet, men ETTER at `useradd` hadde opprettet identiteter og
# `skriv_cred` hadde overskrevet /etc/disponit/*/ med KANDIDATENS verdier —
# mens avbruddsmeldingen sa at forrige release kjører som før. De kjørende
# prosessene beholder riktignok sine innlastede credentials, men neste
# timeraktivering eller restart av forrige release ville lastet kandidatens
# konfigurasjon mens `aktiv` fortsatt pekte på den gamle koden: roterte
# nøkler eller ny konfigurasjon kunne dermed felle nettopp den releasen
# meldingen lovet var uberørt. En port som først kan feile etter første
# mutasjon er ingen preflight — samme dom som varselsender-DSN-en fikk på
# #68. Den hører HER, sammen med resten av gaten, og lesingen skjer i
# SUBSHELL som de andre, så miljøfila ikke lekker inn i preflighten.
#
# Codex P2 (runde 7): DSN-ene porten MÅLER er dermed lest i en subshell og
# kastet, mens steg 4 leser fila på nytt og steg 6 migrerer DEN verdien. Blir
# miljøfila byttet mellom de to lesingene, målte porten historikken til én
# base og kjøreren migrerer en annen — og et checksum-avvik i den andre
# oppdages først i steg 6, inne i vinduet. Verdien porten faktisk målte
# beholdes derfor HER, i navngitte variabler, slik at re-gaten i steg 4 kan
# sammenligne den med det som skal brukes. Lesingen står ute av løkka: da er
# det ETT snapshot begge basene måles mot, og et filbytte midt i løkka kan
# ikke gi runtime én fil-versjon og test en annen.
PREFLIGHT_DISPONIT_MIGRATOR_URL=$( set -a; . "$MILJOFIL"; set +a
    printf '%s' "${DISPONIT_MIGRATOR_URL:-}" )
PREFLIGHT_DISPONIT_TEST_MIGRATOR_DSN=$( set -a; . "$MILJOFIL"; set +a
    printf '%s' "${DISPONIT_TEST_MIGRATOR_DSN:-}" )
for base in runtime test; do
  if ! CHECKSUMPREFLIGHT=$( set -a; . "$MILJOFIL"; set +a
      # Snapshotet, ikke den nettopp sourcede verdien: porten skal måle
      # NØYAKTIG den DSN-en steg 4 senere gates mot. `PREFLIGHT_*` finnes
      # ikke i miljøfila, så en `. "$MILJOFIL"` kan ikke overskrive dem.
      case $base in
        runtime) url=$PREFLIGHT_DISPONIT_MIGRATOR_URL ;;
        test)    url=$PREFLIGHT_DISPONIT_TEST_MIGRATOR_DSN ;;
      esac
      cd "$KILDE" && DISPONIT_MIGRATOR_URL="$url" \
        "$ROT/.venv/bin/python" - 2>&1 <<'PYPRE'
import hashlib, importlib.util, os, sys
from pathlib import Path
import psycopg
kat = Path("platform/core/db/migrations")
# Samme glob som kjorer.py: en `*.sql` uten tresifret prefiks ville felt
# `int(f.name[:3])` her, mens kjøreren aldri så fila. Porten skal måle
# NØYAKTIG det settet kjøreren kjører.
#
# Codex P2 (runde 3): et dict-oppslag er ETT navn per versjon, men treet er
# ikke det. To filer med samme tresifrede prefiks lot den siste overskrive
# den første her — stille — mens kjøreren (`kjorer.py`: `sorted(glob(...))`)
# kjører BEGGE. Porten ville da godkjent treet etter å ha målt én av dem, og
# verre: `api.app.forventede_migrasjoner()` beholder begge tallene, så
# `krev_migrasjonstilstand` sammenligner en liste MED duplikat mot basens
# `versjon`-kolonne, som er PRIMARY KEY og derfor unik. `faktisk !=
# forventet` kunne aldri blitt usann igjen — API-et permanent bootnektet,
# oppdaget i steg 6/8, etter at tjenestene var stoppet. Duplikatet felles
# derfor HER, før første mutasjon, og det måles mens kartet bygges.
filer = {}
duplikater = []
for f in sorted(kat.glob("[0-9][0-9][0-9]_*.sql")):
    versjon = int(f.name[:3])
    if versjon in filer:
        duplikater.append(f"{versjon:03d}: {filer[versjon].name} og {f.name}")
    filer[versjon] = f
if duplikater:
    print("to migrasjonsfiler deler versjonsnummer —\n"
          + "\n".join(duplikater)
          + "\nkjøreren kjører begge, og forventede_migrasjoner() ville"
            " talt versjonen to ganger mot en unik versjon-kolonne:"
            " API-et kunne ikke bootet igjen")
    sys.exit(1)
# HERDINGEN SPØRRES, DEN SPEILES IKKE (#181, eiervalg A fra #178s K2).
#
# Denne blokken var en håndskrevet gjengivelse av akseptkriteriene til
# `migrasjon-bootstrap.herd_historikk`, og de to løkkene hadde forskjellig
# definisjonsmengde: porten løkket over BASENS rader, herdingen over
# `REVIEWEDE_CHECKSUMS` med UBETINGET filmåling. Hver reviewrunde fant et
# nytt sted de sa forskjellige ting (R1 manglende kolonne, R2 NULL på ukjent
# versjon, R3 NULL på legacy uten filmåling), og grenen kunne ikke
# konvergere ved lapping: en FERDIG herdet base med en endret 001-fil gikk
# grønt her og rødt i herdingen — inne i vedlikeholdsvinduet.
#
# `kan_herdes()` ER herdingen, kjørt uten skriving. Det er forskjellen på et
# predikat og en simulator (K4/SP-13): den kan ikke drifte fra originalen,
# fordi den ikke har en egen kropp å drifte i.
_spek = importlib.util.spec_from_file_location(
    "migrasjon_bootstrap", "deploy/staging/migrasjon-bootstrap.py")
_boot = importlib.util.module_from_spec(_spek)
_spek.loader.exec_module(_boot)
with psycopg.connect(os.environ["DISPONIT_MIGRATOR_URL"]) as c:
    herdeavvik = _boot.kan_herdes(c)
    if herdeavvik:
        print("herdingen ville feilet i vedlikeholdsvinduet:\n  "
              + "\n  ".join(herdeavvik))
        sys.exit(1)
    # SYNLIGHET, IKKE ET KRITERIUM: står basen halvveis i herdingen, skal
    # operatøren se det i deploy-loggen i stedet for at raden hoppes over
    # stille. Dette er en OBSERVASJON av bastilstanden — hvilke rader som
    # mangler checksum — ikke en gjengivelse av herdingens akseptkriterier.
    # Kunne noen av dem ikke fylles, hadde `kan_herdes()` over alt felt
    # kjøringen, så observasjonen kan ikke bli en port som drifter.
    try:
        uherdet = [v for (v,) in c.execute(
            "SELECT versjon FROM migrasjoner WHERE checksum IS NULL"
            " ORDER BY versjon").fetchall()]
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        c.rollback()
        uherdet = []
    if uherdet:
        print("uten checksum (herdingen fyller dem): "
              + ", ".join(f"{v:03d}" for v in uherdet))

    # KJØRERENS egen sannhet er en ANNEN måling enn herdingens, og den
    # hører fortsatt her: `kjorer.py` sammenligner hver kjørt migrasjons
    # FIL mot RADENS checksum — 056-hendelsen 23/8. Herdingen ser bare de
    # to reviewede filene, så dette speiler ingenting; det måler et krav
    # herdingen ikke stiller.
    try:
        rader = c.execute(
            "SELECT versjon, checksum FROM migrasjoner"
            " WHERE checksum IS NOT NULL").fetchall()
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        c.rollback()
        print("fersk eller uherdet base — ingen radchecksums å måle mot")
        sys.exit(0)
avvik = []
for versjon, checksum in rader:
    fil = filer.get(versjon)
    if fil is None:
        avvik.append(f"{versjon:03d}: kjørt i basen, borte fra treet")
    elif hashlib.sha256(fil.read_bytes()).hexdigest() != checksum:
        avvik.append(f"{fil.name}: endret etter kjøring (checksum-avvik)")
if avvik:
    print("\n".join(avvik)); sys.exit(1)
print(f"{len(rader)} kjørte migrasjoner byte-identiske;"
      " herdingen kan fullføre")
PYPRE
      ); then
    echo "AVBRUTT ($base): checksum-preflighten er rød —"
    echo "$CHECKSUMPREFLIGHT"
    echo "Systemet er urørt: ingen tjeneste er stoppet, ingen identitet"
    echo "opprettet og ingen credential skrevet; forrige release kjører som"
    echo "før. Er avviket en endret migrasjon, rettes historikk FREMOVER"
    echo "(056-hendelsen 23/8) — aldri ved å redigere den."
    exit 1
  fi
  echo "checksum-preflight ($base): $CHECKSUMPREFLIGHT"
done

# ============================================================
# HERFRA MUTERES SYSTEMET — gaten over er passert.
# ============================================================

# --- 3. Unix-identiteter (idempotent; v2 §3 + PR-009b V1) ------------------
getent group disponit-proxy >/dev/null || groupadd --system disponit-proxy
# PR-015: driftstimerne (revalidering + rydding) får sin EGEN Unix-bruker,
# samme skille som API/M-37/helse — et kompromittert timerkjøring skal ikke
# arve noen annen tjenestes fullmakter, og omvendt.
# PR-014c (Codex P1): disponit-wcag hører HIT, ikke i sjekklisterunden.
# `disponit-wcag-audit.service` har `User=disponit-wcag`, og uten brukeren
# feiler uniten på oppstart uansett hvor riktig konfigurasjonen er skrevet
# — identiteten er utrullingens ansvar, som for alle de andre tjenestene.
for b in disponit-api disponit-m37 disponit-helse disponit-domener \
         disponit-plan \
         disponit-wcag; do
  getent passwd "$b" >/dev/null || \
    useradd --system --no-create-home --shell /usr/sbin/nologin "$b"
done

# SUBORDINATE ID-ER FOR DEN ROOTLESSE ARBEIDEREN (PR-014c, Codex P1).
# `disponit-wcag-audit.service` kjører motoren med rootless podman, og et
# rootless user-namespace bygges av områdene i /etc/subuid og /etc/subgid.
# `useradd --system` deler ikke ut slike områder — systembrukere får dem
# ikke automatisk — og uten dem feiler `podman run` med «cannot find
# UID/GID in /etc/subuid» på HVER eneste kontroll, uansett hvor riktig
# uniten og konfigurasjonen er skrevet. Identiteten er utrullingens
# ansvar, og det er også dette den består av.
SUBID_ANTALL=65536
tildel_subid() {                        # $1=fil  $2=usermod-flagg
  local fil="$1" flagg="$2" start=524288
  [ -f "$fil" ] || : > "$fil"
  if grep -q '^disponit-wcag:' "$fil"; then return 0; fi
  # Første område fra 524288 som ikke overlapper noe som alt står der.
  while awk -F: -v s="$start" -v n="$SUBID_ANTALL" \
        '$2+0 < s+n && $2+0 + $3+0 > s { treff=1 } END { exit !treff }' \
        "$fil"; do
    start=$((start + SUBID_ANTALL))
  done
  usermod "$flagg" "$start-$((start + SUBID_ANTALL - 1))" disponit-wcag
}
tildel_subid /etc/subuid --add-subuids
tildel_subid /etc/subgid --add-subgids
# ... og et HJEM, fordi rootless podman legger BILDELAGERET der
# ($HOME/.local/share/containers/storage). Brukeren ble opprettet med
# `--no-create-home` som de andre tjenestene, og uten en skrivbar
# hjemmekatalog kan podman verken laste inn motorimaget eller finne det
# igjen etterpå. systemd setter $HOME fra kontodatabasen for `User=`, så
# uniten trenger ingen egen Environment-linje.
WCAG_HJEM=/var/lib/disponit-wcag
install -d -m 700 -o disponit-wcag -g disponit-wcag "$WCAG_HJEM"
usermod -d "$WCAG_HJEM" disponit-wcag
for f in /etc/subuid /etc/subgid; do
  grep -q '^disponit-wcag:' "$f" || {
    echo "AVBRUTT: fikk ikke tildelt subordinate ID-er i $f for" \
         "disponit-wcag — rootless podman kan ikke bygge namespacet." >&2
    exit 1
  }
done
# Hjelperne selv er shadow-utils' setuid-binærer (pakken `uidmap` på
# Debian/Ubuntu). Mangler de, er områdene over riktige men ubrukelige.
# Dette stopper IKKE utrullingen — resten av plattformen er uavhengig av
# wcag-motoren — men det skal være synlig i loggen, og fase 9 i
# staging-sjekklisten måler det samme før den enabler uniten.
for h in newuidmap newgidmap; do
  command -v "$h" >/dev/null || \
    echo "MERK: $h mangler (pakken uidmap) — rootless podman, og dermed" \
         "wcag-motoren, vil ikke starte." >&2
done
# nginx-brukeren meldes inn i disponit-proxy av PR-009b — ALDRI her, og
# aldri M-37: gruppen ER tillitsgrensen. Helsesjekkeren er medlem som
# TILSYNSKLIENT (/live over socketen) — en bevisst, synlig utvidelse,
# dokumentert i DEPLOY.md og flagget i PR-beskrivelsen.
usermod -aG disponit-proxy disponit-helse

# --- 4. Credentials per tjeneste (v3 §5): root-eide filer ------------------
# Kilden er staging.env (lib-miljofil eier livssyklusen); her MATERIALISERES
# de per unit, slik at LoadCredential gir hver prosess kun sine egne.
set -a; . "$MILJOFIL"; set +a
# PR-045 (Codex P2): MILJØPORTEN PÅ NYTT — nå på verdien som faktisk SKRIVES.
# Gaten før første mutasjon leser miljøfila i en SUBSHELL (bevisst: fila skal
# ikke lekke inn i preflighten) og kaster verdien når subshellen dør. Lesingen
# rett over er en NY lesing av samme fil, og det er DEN verdien
# `skriv_cred api DISPONIT_MILJO` under materialiserer. Byttes eller
# redigeres fila mellom de to lesingene — en konfigurasjonsstyring som ruller
# `produksjon` ut mens utrullingen står på — godkjente den første gaten en
# verdi som aldri ble skrevet, og staging-API-et startet i produksjonsmodus
# med gaten passert. Sjekken står derfor på SAMME shell-variabel som skrives,
# og linjene mellom her og skrivingen leser ikke miljøfila igjen; da finnes
# det ikke noe vindu mellom godkjenningen og verdien.
#
# Den første gaten blir ikke overflødig av dette: den er den som lar en
# feilkonfigurert miljøfil stoppe utrullingen mens systemet BEVISELIG er
# urørt. Denne er den som garanterer at det godkjente og det skrevne er det
# samme. Begge trengs, og de måler ikke det samme.
if [ "${DISPONIT_MILJO:-staging}" != "staging" ]; then
  echo "AVBRUTT: DISPONIT_MILJO i $MILJOFIL er ikke 'staging'."
  echo "Verdien endret seg mellom miljøporten og materialiseringen av"
  echo "credentials — miljøfila er byttet eller redigert mens utrullingen"
  echo "kjørte. Verdien er IKKE skrevet: ingen credential er materialisert,"
  echo "ingen unit er aktivert, og ingen tjeneste er startet på nytt."
  echo "Unix-identitetene i steg 3 er opprettet (idempotent) — kjør opp.sh"
  echo "på nytt når $MILJOFIL står stille og sier 'staging'."
  exit 1
fi
# Cursor P2-2 (#178, runde 4): SAMME RE-GATE PÅ MIGRASJONS-DSN-ENE.
# `DISPONIT_VARSEL_URL` og `DISPONIT_PLAN_URL` re-gates fordi preflighten
# leste dem i en subshell og materialiseringen leser fila på nytt — men de
# to migrasjons-DSN-ene ble lest på nøyaktig samme måte (checksum-porten,
# i subshell) og BRUKT fra den autoritative lesingen over, uten en tilsvarende
# gate. Er én av dem tom etter et filbytte, oppdages det først i steg 6 —
# INNE i vedlikeholdsvinduet, etter tjenestestoppen — som en psycopg-feil
# som går til `selvrevers()` i stedet for til en urørt avbrutt deploy.
#
# Begge måles, ikke bare runtime: de leses fra samme linje og brukes i
# samme løkke, og å lukke halve klassen inviterer bare en runde til.
# `set -u` stopper en FRAVÆRENDE variabel av seg selv; det er den TOMME
# denne gaten finnes for.
#
# Codex P2 (runde 7): TOMHET ER IKKE NOK. Gaten fanget verdien som forsvant,
# men ikke verdien som ble en ANNEN: en ny, ikke-tom DSN slapp gjennom, og
# steg 6 migrerte da en base ingen port hadde sett historikken til. Gaten
# måler derfor IDENTITET mot verdien checksum-porten faktisk målte —
# tomhetssjekken står igjen foran, fordi den har en egen jobb: er BEGGE tomme,
# er de identiske, men `psycopg.connect("")` faller tilbake på libpq-defaultene
# og kan ha målt en helt annen base enn den tomme strengen ser ut som.
for dsn_navn in DISPONIT_MIGRATOR_URL DISPONIT_TEST_MIGRATOR_DSN; do
  if [ -z "${!dsn_navn:-}" ]; then     # indirekte oppslag, ikke eval
    echo "AVBRUTT: $dsn_navn forsvant fra $MILJOFIL mellom"
    echo "checksum-preflighten og den autoritative lesingen — fila er byttet"
    echo "eller redigert mens utrullingen kjørte. Ingen credential er"
    echo "materialisert, ingen tjeneste er stoppet, og ingen migrasjon er"
    echo "kjørt. Kjør opp.sh på nytt når $MILJOFIL står stille."
    exit 1
  fi
  maalt_navn="PREFLIGHT_$dsn_navn"
  if [ "${!dsn_navn}" != "${!maalt_navn:-}" ]; then
    echo "AVBRUTT: $dsn_navn i $MILJOFIL peker et annet sted enn den basen"
    echo "checksum-preflighten målte — fila er byttet eller redigert mens"
    echo "utrullingen kjørte. Steg 6 ville da migrert en historikk ingen port"
    echo "har lest, og et checksum-avvik ville vist seg først INNE i"
    echo "vedlikeholdsvinduet, etter tjenestestoppen (23/8-klassen). Ingen"
    echo "credential er materialisert, ingen tjeneste er stoppet, og ingen"
    echo "migrasjon er kjørt. Kjør opp.sh på nytt når $MILJOFIL står stille."
    exit 1
  fi
done
# SNAPSHOT AV CREDENTIALENE FØR DE OVERSKRIVES (Codex P1, runde 4).
#
# Steg 4 materialiserer KANDIDATENS verdier i /etc/disponit/*, og
# `selvrevers()` starter FORRIGE release fra nøyaktig de samme filene.
# `LoadCredential` leser fila på nytt ved HVER aktivering, så uten et
# snapshot booter reverseringen gammel binær på ny konfigurasjon — og
# meldingen «symlinken er urørt» er sann om symlinken og usann om
# tilstanden prosessen faktisk starter i.
#
# Skarpeste tilfellet er `DISPONIT_SEMANTIKK_MILJO`: den regnes ut nedenfor
# med KANDIDATENS kode (`$KILDE`) og måles ved oppstart av FORRIGE releases
# egen `verifiser_oppstartsmiljo()`. Endret signaturformen seg mellom de to
# releasene, nekter forrige release å starte — på en verdi denne
# utrullingen skrev. Samme klasse: en nøkkel rotert i miljøfila siden
# forrige deploy, eller en DSN som peker et nytt sted.
#
# Snapshotet er GENERISK — hver underkatalog av /etc/disponit, ikke en
# liste over dagens credential-kataloger — så neste `skriv_cred`-katalog er
# dekket uten at noen husker det. Navnet har ledende punktum med vilje:
# `*/` matcher ikke skjulte navn, så kopien kan ikke kopiere seg selv.
CRED_FORVINDU=/etc/disponit/.forvindu
rm -rf "$CRED_FORVINDU"
install -d -m 700 "$CRED_FORVINDU"
for kat in /etc/disponit/*/; do
  [ -d "$kat" ] || continue        # fersk vert: ingen kataloger å bevare
  cp -a "$kat" "$CRED_FORVINDU/"
done
# Hver katalog `skriv_cred` skriver i MÅ opprettes FØR den skrives i —
# `skriv_cred` er en `printf >`-omdirigering, og uten katalogen feiler den i
# den MUTERENDE fasen, etter at preflighten er passert. På en vert som har
# rullet ut før, ligger katalogen igjen fra forrige gang og hullet er usynlig;
# det er den ferske verten som treffer det. `test_pr009` måler koblingen mot
# kilden, så den neste credential-katalogen er dekket uten at noen husker det.
install -d -m 700 /etc/disponit/api /etc/disponit/m37 /etc/disponit/plan
skriv_cred() {  # katalog navn verdi
  printf '%s' "$3" > "/etc/disponit/$1/$2"
  chmod 600 "/etc/disponit/$1/$2"
}
skriv_cred api DATABASE_URL          "$DATABASE_URL"
# Senderen leser køen som runtime-rollen. Katalogen må finnes FØR `skriv_cred`
# skriver i den — uten `install -d` feilet omdirigeringen, og den feilen ville
# først vist seg som en sender uten DB-URL.
install -d -m 700 /etc/disponit/varsel
# SENDERENS EGEN DSN — aldri API-ets. Preflighten beviste at variabelen
# fantes FØR første mutasjon, men lesingen over er en NY lesing av samme fil
# (Codex P2, samme funn som DISPONIT_MILJO-porten rett over): byttes fila
# mellom de to lesingene, godkjente preflighten en verdi som aldri skrives —
# og en tom verdi her ville materialisert en tom credential som senderen
# først oppdager ved neste timerkjøring. Sjekken står derfor på SAMME
# shell-variabel som skrives, uten ny lesing av fila imellom.
if [ -z "${DISPONIT_VARSEL_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_VARSEL_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env} mellom"
  echo "preflighten og materialiseringen — fila er byttet eller redigert"
  echo "mens utrullingen kjørte. Ingen varsel-credential er skrevet."
  echo "Kjør opp.sh på nytt når ${MILJOFIL:-/etc/disponit/staging.env} står stille."
  exit 1
fi
skriv_cred varsel DISPONIT_DATABASE_URL "$DISPONIT_VARSEL_URL"
# 090/091: hver jobb sin EGEN katalog og sin EGEN DSN — aldri API-ets, og
# aldri hverandres. Katalogene må finnes FØR `skriv_cred` skriver i dem
# (samme felle som varsel-katalogen over). Sjekken står på SAMME
# shell-variabel som skrives, uten ny lesing av miljøfila imellom:
# preflighten beviste at variabelen fantes, men byttes fila i mellomtiden,
# godkjente den en verdi som aldri skrives.
install -d -m 700 /etc/disponit/driftstatus /etc/disponit/selvtest /etc/disponit/kvalitet
install -d -m 700 /etc/disponit/driftstatus /etc/disponit/selvtest /etc/disponit/lagermaaler
if [ -z "${DISPONIT_LAGERMAALER_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_LAGERMAALER_URL forsvant fra"
  echo "${MILJOFIL:-/etc/disponit/staging.env} mellom preflighten og"
  echo "materialiseringen — fila er byttet eller redigert mens utrullingen"
  echo "kjørte. Ingen lagermaaler-credential er skrevet."
  exit 1
fi
if [ -z "${DISPONIT_DRIFTSTATUS_URL:-}" ] || [ -z "${DISPONIT_SELVTEST_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_DRIFTSTATUS_URL eller DISPONIT_SELVTEST_URL"
  echo "forsvant fra ${MILJOFIL:-/etc/disponit/staging.env} mellom preflighten"
  echo "og materialiseringen — fila er byttet eller redigert mens"
  echo "utrullingen kjørte. Ingen driftstatus-credential er skrevet."
  exit 1
fi
skriv_cred driftstatus DISPONIT_DRIFTSTATUS_URL "$DISPONIT_DRIFTSTATUS_URL"
skriv_cred selvtest DISPONIT_SELVTEST_URL "$DISPONIT_SELVTEST_URL"
# 092 (M-3): egen katalog og egen DSN, som de to over. Sjekken står på
# SAMME shell-variabel som skrives, uten ny lesing av miljøfila imellom.
if [ -z "${DISPONIT_KVALITETSMAALER_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_KVALITETSMAALER_URL forsvant fra"
  echo "${MILJOFIL:-/etc/disponit/staging.env} mellom preflighten og"
  echo "materialiseringen — fila er byttet eller redigert mens"
  echo "utrullingen kjørte. Ingen kvalitets-credential er skrevet."
  exit 1
fi
skriv_cred kvalitet DISPONIT_KVALITETSMAALER_URL "$DISPONIT_KVALITETSMAALER_URL"
skriv_cred lagermaaler DISPONIT_LAGERMAALER_URL "$DISPONIT_LAGERMAALER_URL"
# 095 (M-9): begrepssveipens egen katalog og egen DSN — aldri API-ets,
# og aldri en av de andre jobbenes. Sjekken står på SAMME shell-variabel
# som skrives, uten ny lesing av miljøfila imellom (samme felle som
# over): preflighten beviste at variabelen fantes, men byttes fila i
# mellomtiden, godkjente den en verdi som aldri skrives.
install -d -m 700 /etc/disponit/kunnskapssveip
if [ -z "${DISPONIT_KUNNSKAPSSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_KUNNSKAPSSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "mellom preflighten og materialiseringen — fila er byttet eller"
  echo "redigert mens utrullingen kjørte. Ingen begrepssveip-credential"
  echo "er skrevet."
  exit 1
fi
skriv_cred kunnskapssveip DISPONIT_KUNNSKAPSSVEIP_URL "$DISPONIT_KUNNSKAPSSVEIP_URL"
# 097 (M-12): gjennomgangssveipens egen katalog og egen DSN — aldri
# API-ets, og aldri en av de andre jobbenes. Sjekken står på SAMME
# shell-variabel som skrives, uten ny lesing av miljøfila imellom
# (samme felle som over): preflighten beviste at variabelen fantes, men
# byttes fila i mellomtiden, godkjente den en verdi som aldri skrives.
install -d -m 700 /etc/disponit/tilgangssveip
if [ -z "${DISPONIT_TILGANGSSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_TILGANGSSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "mellom preflighten og materialiseringen — fila er byttet eller"
  echo "redigert mens utrullingen kjørte. Ingen tilgangssveip-credential"
  echo "er skrevet."
  exit 1
fi
skriv_cred tilgangssveip DISPONIT_TILGANGSSVEIP_URL "$DISPONIT_TILGANGSSVEIP_URL"
# 099 (M-30): fristsveipens egen katalog og egen DSN — aldri API-ets, og
# aldri en av de andre jobbenes. Sjekken står på SAMME shell-variabel som
# skrives, uten ny lesing av miljøfila imellom (samme felle som over):
# preflighten beviste at variabelen fantes, men byttes fila i mellomtiden,
# godkjente den en verdi som aldri skrives.
install -d -m 700 /etc/disponit/personvernsveip
if [ -z "${DISPONIT_PERSONVERNSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_PERSONVERNSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "mellom preflighten og materialiseringen — fila er byttet eller"
  echo "redigert mens utrullingen kjørte. Ingen personvernsveip-credential"
  echo "er skrevet."
  exit 1
fi
skriv_cred personvernsveip DISPONIT_PERSONVERNSVEIP_URL "$DISPONIT_PERSONVERNSVEIP_URL"
# 100 (M-34): etterprøvingssveipens egen katalog og egen DSN — aldri
# API-ets, og aldri en av de andre jobbenes. Sjekken står på SAMME
# shell-variabel som skrives, uten ny lesing av miljøfila imellom (samme
# felle som over).
install -d -m 700 /etc/disponit/compliancesveip
if [ -z "${DISPONIT_COMPLIANCESVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_COMPLIANCESVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "mellom preflighten og materialiseringen — fila er byttet eller"
  echo "redigert mens utrullingen kjørte. Ingen compliancesveip-credential"
  echo "er skrevet."
  exit 1
fi
skriv_cred compliancesveip DISPONIT_COMPLIANCESVEIP_URL "$DISPONIT_COMPLIANCESVEIP_URL"
# 101 (M-13): avstemmingssveipens egen katalog og egen DSN — aldri
# API-ets, og aldri en av de andre jobbenes. Sjekken står på SAMME
# shell-variabel som skrives, uten ny lesing av miljøfila imellom (samme
# felle som over).
install -d -m 700 /etc/disponit/avstemmingssveip
if [ -z "${DISPONIT_AVSTEMMINGSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_AVSTEMMINGSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "mellom preflighten og materialiseringen — fila er byttet eller"
  echo "redigert mens utrullingen kjørte. Ingen avstemmingssveip-credential"
  echo "er skrevet."
  exit 1
fi
skriv_cred avstemmingssveip DISPONIT_AVSTEMMINGSVEIP_URL "$DISPONIT_AVSTEMMINGSVEIP_URL"
# 102 (M-17): henvendelsessveipens egen katalog og egen DSN. Sjekken står
# på SAMME shell-variabel som skrives, uten ny lesing av miljøfila
# imellom (samme felle som over).
install -d -m 700 /etc/disponit/henvendelsessveip
if [ -z "${DISPONIT_HENVENDELSESVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_HENVENDELSESVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "mellom preflighten og materialiseringen — fila er byttet eller"
  echo "redigert mens utrullingen kjørte. Ingen henvendelsessveip-credential"
  echo "er skrevet."
  exit 1
fi
skriv_cred henvendelsessveip DISPONIT_HENVENDELSESVEIP_URL "$DISPONIT_HENVENDELSESVEIP_URL"
# 103 (M-18): onboardingsveipens egen katalog og egen DSN. Sjekken står
# på SAMME shell-variabel som skrives (samme felle som over).
install -d -m 700 /etc/disponit/onboardingsveip
if [ -z "${DISPONIT_ONBOARDINGSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_ONBOARDINGSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "mellom preflighten og materialiseringen — fila er byttet eller"
  echo "redigert mens utrullingen kjørte. Ingen onboardingsveip-credential"
  echo "er skrevet."
  exit 1
fi
skriv_cred onboardingsveip DISPONIT_ONBOARDINGSVEIP_URL "$DISPONIT_ONBOARDINGSVEIP_URL"
# 104 (M-23): fordringssveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/fordringssveip
if [ -z "${DISPONIT_FORDRINGSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_FORDRINGSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "mellom preflighten og materialiseringen — fila er byttet eller"
  echo "redigert mens utrullingen kjørte. Ingen fordringssveip-credential"
  echo "er skrevet."
  exit 1
fi
skriv_cred fordringssveip DISPONIT_FORDRINGSVEIP_URL "$DISPONIT_FORDRINGSVEIP_URL"
# 105 (M-24): leverandørsveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/leverandorsveip
if [ -z "${DISPONIT_LEVERANDORSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_LEVERANDORSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "mellom preflighten og materialiseringen — fila er byttet eller"
  echo "redigert mens utrullingen kjørte. Ingen leverandorsveip-credential"
  echo "er skrevet."
  exit 1
fi
skriv_cred leverandorsveip DISPONIT_LEVERANDORSVEIP_URL "$DISPONIT_LEVERANDORSVEIP_URL"
# 106 (M-14): fakturasveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/fakturasveip
if [ -z "${DISPONIT_FAKTURASVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_FAKTURASVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "mellom preflighten og materialiseringen — fila er byttet eller"
  echo "redigert mens utrullingen kjørte. Ingen fakturasveip-credential"
  echo "er skrevet."
  exit 1
fi
skriv_cred fakturasveip DISPONIT_FAKTURASVEIP_URL "$DISPONIT_FAKTURASVEIP_URL"
# 107 (M-25): prosjektsveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/prosjektsveip
if [ -z "${DISPONIT_PROSJEKTSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_PROSJEKTSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "mellom preflighten og materialiseringen — fila er byttet eller"
  echo "redigert mens utrullingen kjørte. Ingen prosjektsveip-credential"
  echo "er skrevet."
  exit 1
fi
skriv_cred prosjektsveip DISPONIT_PROSJEKTSVEIP_URL "$DISPONIT_PROSJEKTSVEIP_URL"
# 108 (M-26): prisboksveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/prisboksveip
if [ -z "${DISPONIT_PRISBOKSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_PRISBOKSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "mellom preflighten og materialiseringen — fila er byttet eller"
  echo "redigert mens utrullingen kjørte. Ingen prisboksveip-credential"
  echo "er skrevet."
  exit 1
fi
skriv_cred prisboksveip DISPONIT_PRISBOKSVEIP_URL "$DISPONIT_PRISBOKSVEIP_URL"
# 109 (M-27): lagersveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/lagersveip
if [ -z "${DISPONIT_LAGERSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_LAGERSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "mellom preflighten og materialiseringen — fila er byttet eller"
  echo "redigert mens utrullingen kjørte. Ingen lagersveip-credential"
  echo "er skrevet."
  exit 1
fi
skriv_cred lagersveip DISPONIT_LAGERSVEIP_URL "$DISPONIT_LAGERSVEIP_URL"
# 110 (M-42): kontovaktsveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/kontovaktsveip
if [ -z "${DISPONIT_KONTOVAKTSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_KONTOVAKTSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "mellom preflighten og materialiseringen — fila er byttet eller"
  echo "redigert mens utrullingen kjørte. Ingen kontovaktsveip-credential"
  echo "er skrevet."
  exit 1
fi
skriv_cred kontovaktsveip DISPONIT_KONTOVAKTSVEIP_URL "$DISPONIT_KONTOVAKTSVEIP_URL"
# 111 (M-41): betalingssveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/betalingssveip
if [ -z "${DISPONIT_BETALINGSSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_BETALINGSSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "mellom preflighten og materialiseringen — fila er byttet eller"
  echo "redigert mens utrullingen kjørte. Ingen betalingssveip-credential"
  echo "er skrevet."
  exit 1
fi
skriv_cred betalingssveip DISPONIT_BETALINGSSVEIP_URL "$DISPONIT_BETALINGSSVEIP_URL"

# 112 (M-19): adressesveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/adressesveip
if [ -z "${DISPONIT_ADRESSESVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_ADRESSESVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen adressesveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred adressesveip DISPONIT_ADRESSESVEIP_URL "$DISPONIT_ADRESSESVEIP_URL"

# 119 (M-51): tilskuddssveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/tilskuddssveip
if [ -z "${DISPONIT_TILSKUDDSSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_TILSKUDDSSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen tilskuddssveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred tilskuddssveip DISPONIT_TILSKUDDSSVEIP_URL "$DISPONIT_TILSKUDDSSVEIP_URL"

# 120 (M-55): merkevaresveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/merkevaresveip
if [ -z "${DISPONIT_MERKEVARESVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_MERKEVARESVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen merkevaresveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred merkevaresveip DISPONIT_MERKEVARESVEIP_URL "$DISPONIT_MERKEVARESVEIP_URL"

# 121 (M-54): EHF-sveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/ehfsveip
if [ -z "${DISPONIT_EHFSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_EHFSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen ehfsveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred ehfsveip DISPONIT_EHFSVEIP_URL "$DISPONIT_EHFSVEIP_URL"

# 124 (M-50): postjournalsveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/postjournalsveip
if [ -z "${DISPONIT_POSTJOURNALSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_POSTJOURNALSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen postjournalsveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred postjournalsveip DISPONIT_POSTJOURNALSVEIP_URL \
    "$DISPONIT_POSTJOURNALSVEIP_URL"

# 127 (M-53): HMS-sveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/hmssveip
if [ -z "${DISPONIT_HMSSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_HMSSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen hmssveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred hmssveip DISPONIT_HMSSVEIP_URL "$DISPONIT_HMSSVEIP_URL"

# 128 (M-15): likviditetssveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/likviditetssveip
if [ -z "${DISPONIT_LIKVIDITETSSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_LIKVIDITETSSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen likviditetssveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred likviditetssveip DISPONIT_LIKVIDITETSSVEIP_URL \
    "$DISPONIT_LIKVIDITETSSVEIP_URL"

# 130 (M-33): prognosesveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/prognosesveip
if [ -z "${DISPONIT_PROGNOSESVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_PROGNOSESVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen prognosesveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred prognosesveip DISPONIT_PROGNOSESVEIP_URL \
    "$DISPONIT_PROGNOSESVEIP_URL"

# 132 (M-36): optimalisatorsveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/optimalisatorsveip
if [ -z "${DISPONIT_OPTIMALISATORSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_OPTIMALISATORSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen optimalisatorsveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred optimalisatorsveip DISPONIT_OPTIMALISATORSVEIP_URL \
    "$DISPONIT_OPTIMALISATORSVEIP_URL"

# 133 (M-7): møtesveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/motesveip
if [ -z "${DISPONIT_MOTESVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_MOTESVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen motesveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred motesveip DISPONIT_MOTESVEIP_URL "$DISPONIT_MOTESVEIP_URL"

# 134 (M-20): innholdssveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/innholdssveip
if [ -z "${DISPONIT_INNHOLDSSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_INNHOLDSSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen innholdssveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred innholdssveip DISPONIT_INNHOLDSSVEIP_URL \
    "$DISPONIT_INNHOLDSSVEIP_URL"

# 135 (M-43): telefonisveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/telefonisveip
if [ -z "${DISPONIT_TELEFONISVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_TELEFONISVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen telefonisveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred telefonisveip DISPONIT_TELEFONISVEIP_URL \
    "$DISPONIT_TELEFONISVEIP_URL"

# 123 (M-47): myndighetssveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/myndighetssveip
if [ -z "${DISPONIT_MYNDIGHETSSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_MYNDIGHETSSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen myndighetssveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred myndighetssveip DISPONIT_MYNDIGHETSSVEIP_URL \
    "$DISPONIT_MYNDIGHETSSVEIP_URL"

# 122 (M-52): tollkodesveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/tollkodesveip
if [ -z "${DISPONIT_TOLLKODESVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_TOLLKODESVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen tollkodesveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred tollkodesveip DISPONIT_TOLLKODESVEIP_URL "$DISPONIT_TOLLKODESVEIP_URL"

# 118 (M-46): anbudssveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/anbudssveip
if [ -z "${DISPONIT_ANBUDSSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_ANBUDSSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen anbudssveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred anbudssveip DISPONIT_ANBUDSSVEIP_URL "$DISPONIT_ANBUDSSVEIP_URL"

# 117 (M-49): sanksjonssveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/sanksjonssveip
if [ -z "${DISPONIT_SANKSJONSSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_SANKSJONSSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen sanksjonssveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred sanksjonssveip DISPONIT_SANKSJONSSVEIP_URL "$DISPONIT_SANKSJONSSVEIP_URL"

# 116 (M-48): motpartssveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/motpartssveip
if [ -z "${DISPONIT_MOTPARTSSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_MOTPARTSSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen motpartssveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred motpartssveip DISPONIT_MOTPARTSSVEIP_URL "$DISPONIT_MOTPARTSSVEIP_URL"

# 113 (M-39): lønnssveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/lonnssveip
if [ -z "${DISPONIT_LONNSSVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_LONNSSVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen lønnssveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred lonnssveip DISPONIT_LONNSSVEIP_URL "$DISPONIT_LONNSSVEIP_URL"

# 114 (M-44): kampanjesveipens egen katalog og egen DSN.
install -d -m 700 /etc/disponit/kampanjesveip
if [ -z "${DISPONIT_KAMPANJESVEIP_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_KAMPANJESVEIP_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env}"
  echo "etter forhåndssjekken. Enten ble den fjernet, eller så ble fila"
  echo "redigert mens utrullingen kjørte. Ingen kampanjesveip-credential"
  echo "skrives på et tomt DSN."
  exit 1
fi
skriv_cred kampanjesveip DISPONIT_KAMPANJESVEIP_URL "$DISPONIT_KAMPANJESVEIP_URL"
skriv_cred api DISPONIT_KEK          "$DISPONIT_KEK"
skriv_cred api DISPONIT_TOKEN_PEPPER "$DISPONIT_TOKEN_PEPPER"
skriv_cred api DISPONIT_ATT_NOKLER   "$DISPONIT_ATT_NOKLER"
skriv_cred api DISPONIT_MAC_NOKLER   "$DISPONIT_MAC_NOKLER"   # PR-012 (boot-perre)
# 044: plan-materialisereren kjører beslutningsveien i prosess (samme
# tillitsnivå som API-et) — den trenger nøyaktig API-ets nøkkelsett, og
# runtime-DSN-en (ikke claimerens): all DB-autoritet ligger i de
# claimer-eide funksjonene den EXECUTEr.
# 048 (#108): plan-arbeiderens EGEN DSN — aldri runtime-DSN-en. Delte
# den DSN med API-et, ville claim-EXECUTE måttet gis til `disponit`, og
# da hadde et kompromittert web-API hatt planvindus-mutexen (nøyaktig
# begrunnelsen i disponit-varselsender.service, som er malen her).
if [ -z "${DISPONIT_PLAN_URL:-}" ]; then
  echo "AVBRUTT: DISPONIT_PLAN_URL forsvant fra ${MILJOFIL:-/etc/disponit/staging.env} mellom"
  echo "preflight og credential-skrivingen."
  exit 1
fi
skriv_cred plan DISPONIT_DATABASE_URL "$DISPONIT_PLAN_URL"
skriv_cred plan DISPONIT_KEK          "$DISPONIT_KEK"
skriv_cred plan DISPONIT_TOKEN_PEPPER "$DISPONIT_TOKEN_PEPPER"
skriv_cred plan DISPONIT_ATT_NOKLER   "$DISPONIT_ATT_NOKLER"
skriv_cred plan DISPONIT_MAC_NOKLER   "$DISPONIT_MAC_NOKLER"
skriv_cred plan DISPONIT_MILJO        "${DISPONIT_MILJO:-staging}"
# PR-013 (V8/port 13): fest miljøsignaturen (tzdata) RELEASEN bygges med, målt
# med releasens EGEN kode. Bytter vertens tzdata etterpå, avviker boot-sjekken
# og prosessen nekter start — motoren tolker aldri tidsvinduer annerledes enn
# klassifikatoren beviste, uoppdaget.
#
# BEGGE enhetene får den (Codex P1): plan-arbeideren konstruerer `Tjeneste` og
# kjører NØYAKTIG samme motor som API-et, og `verifiser_oppstartsmiljo()`
# hopper over tzdata-sammenligningen når variabelen MANGLER. Sto den bare hos
# API-et, ville en tzdata-drift på verten gitt akkurat det utfallet porten
# finnes for å hindre: API-et nekter å starte, mens den planlagte veien —
# den som beslutter uten et menneske til stede — fortsetter i stillhet på
# en semantikk ingen har verifisert. Signaturen måles ÉN gang, så de to
# enhetene aldri kan få hver sin.
SEMANTIKK_MILJO="$(PYTHONPATH="$KILDE/platform/core" \
    "$ROT/.venv/bin/python" -c 'from policy_validator import semantikk; print(semantikk.miljosignatur())')"
skriv_cred api  DISPONIT_SEMANTIKK_MILJO "$SEMANTIKK_MILJO"
skriv_cred plan DISPONIT_SEMANTIKK_MILJO "$SEMANTIKK_MILJO"
# PR-011b: UI-deploy-config (provider-valg + IdP-origins). Ikke hemmeligheter,
# men hydreres til os.environ via samme LoadCredential-vei. Tomme = default.
skriv_cred api DISPONIT_UI_PROVIDER    "${DISPONIT_UI_PROVIDER:-}"
skriv_cred api DISPONIT_UI_IDP_ORIGINS "${DISPONIT_UI_IDP_ORIGINS:-}"

# M-6 (088/PR-B): M365-kildekoblingen. Fem verdier, og TOMME DEFAULTS med
# vilje — `epost_kilde.lag_konfig()` returnerer None når client_id eller
# hemmeligheten mangler, og /start svarer da 503 «ikke konfigurert» i
# stedet for å starte en halv OAuth-flyt. Fravær er altså en gyldig,
# ærlig tilstand her, ulikt DSN-ene over som deployporten krever.
#
# Derfor står det heller ingen preflight-port på dem: en port ville
# stanset hver eneste deploy til noen registrerte en Azure-app, og
# resten av plattformen har ingenting med M-6 å gjøre.
#
# TENANTSEGMENTET er en URL-PATH-komponent hos Microsoft
# (login.microsoftonline.com/<tenant>/oauth2/v2.0/...), validert mot et
# lukket tegnsett i koden. `organizations` utelukker personlige kontoer,
# `common` tillater begge, `consumers` bare personlige — og en katalog-
# GUID låser til én organisasjon. Defaulten her er TOM, ikke en gjetning:
# koden har sin egen default, og to steder som gjetter forskjellig er en
# feil som først viser seg i en avvist innlogging.
skriv_cred api DISPONIT_M365_CLIENT_ID     "${DISPONIT_M365_CLIENT_ID:-}"
skriv_cred api DISPONIT_M365_CLIENT_SECRET "${DISPONIT_M365_CLIENT_SECRET:-}"
skriv_cred api DISPONIT_M365_REDIRECT_URI  "${DISPONIT_M365_REDIRECT_URI:-}"
skriv_cred api DISPONIT_M365_TENANT        "${DISPONIT_M365_TENANT:-}"
skriv_cred api DISPONIT_M365_ALLOWLIST     "${DISPONIT_M365_ALLOWLIST:-}"
# PR-045 (Codex P1): MILJØET prosessen kjører i. `platform/core/miljo` er den
# ene tolkningen av variabelen, og TO ting leser den: hvilke policystatuser som
# får binde en beslutning (`api.policyregister.tillatte_statuser`), og om
# forsiden kan love en modul til en kunde (`/ui/oppsett.json`). Uten denne
# linjen nådde verdien ALDRI prosessen — `lag_app()` hydrerer kun de
# credentialene unitten laster, så begge leddene tok fallbacken uansett hva
# som sto i miljøfila. Da var `DISPONIT_MILJO` i praksis en variabel bare
# testene hadde.
#
# Verdien er `staging` og kan ikke være noe annet HER: dette er
# staging-løypa, og `docs/DEPLOY.md` reserverer produksjon for en egen VPS.
# Påstanden hviler på gaten rett etter den autoritative lesingen, ikke på
# den før første mutasjon: det er bare den første som er lest fra SAMME
# variabel som denne linjen skriver. En produksjonsutrulling får sin egen
# løype som skriver sin egen verdi.
#
# Verdien hydreres RÅ i prosessen (`db.hemmeligheter.EKSAKTE`), så
# `platform/core/miljo` ser nøyaktig det som står her — ingen stripping
# mellom fila og sammenligningen.
skriv_cred api DISPONIT_MILJO          "${DISPONIT_MILJO:-staging}"
# Arbeideren får sin EGEN DB-rolle (v2 §3) når DISPONIT_ARBEIDER_URL er
# satt av oppsett-postgresql.sh; ellers deler den runtime-DSN-en og det
# rapporteres som avvik nederst.
skriv_cred m37 DATABASE_URL "${DISPONIT_ARBEIDER_URL:-$DATABASE_URL}"
skriv_cred m37 DISPONIT_KEK "$DISPONIT_KEK"
install -d -m 700 /etc/disponit/tokenadmin
skriv_cred tokenadmin DISPONIT_TOKEN_ADMIN_URL "$DISPONIT_TOKEN_ADMIN_URL"
skriv_cred tokenadmin DISPONIT_TOKEN_PEPPER    "$DISPONIT_TOKEN_PEPPER"
# PR-015: driftstimerne — egen rolle (disponit_domener, migrasjon 019),
# ALDRI disponit_domains_admin (den bærer direkte EXECUTE på
# avgjor_domeneovertakelse og ville omgått fire øyne, jf. F16-notatet i
# migrasjonen). DISPONIT_RESOLVERE er ikke en hemmelighet — server-
# adressene for DNS-oppslag — men går gjennom samme LoadCredential-vei
# som resten (v3 §5); tom streng gir en tydelig oppstart-nektet-feil i
# stedet for stille å hoppe over diversitetskravet.
install -d -m 700 /etc/disponit/domener
# INGEN fallback til $DATABASE_URL: den DSN-en har ingen av grantene 019
# gir, så en fallback ville bare gjort en manglende rolle til to timere som
# feiler i drift. Gaten i §2 har alt avbrutt utrullingen hvis den mangler.
skriv_cred domener DISPONIT_DOMAINS_URL "$DISPONIT_DOMAINS_URL"
skriv_cred domener DISPONIT_RESOLVERE   "${DISPONIT_RESOLVERE:-}"

# Selv-reversering (#172): i feilsonen mellom «tjenester stoppet» og
# release-byttet peker symlinken FORTSATT på forrige release — å starte
# unitene igjen ER å boote den. Men bare når basen står stille også: rakk
# runtime-migrasjonen å flytte skjemaet før feilen, finnes det ingen
# gjenoppretting å love, og reverseringen sier det i stedet for å starte
# gamle arbeidere mot et nytt skjema (rullbakk-gaten først i funksjonen).
#
# Codex P1 (runde 1): en reversering som starter et UTVALG av det vinduet
# stoppet, er ingen reversering. Steg 5 slår av elleve enheter; første
# utgave startet fire av dem og målte dommen på API-et alene — altså kunne
# skriptet skrive «SELVREVERSERT: forrige release kjører igjen» mens
# varselsenderen, plan-materialisereren, evidensreaperen (038) og
# domeneverifiseringen (039) sto stille på ubestemt tid. Køene ville bare
# vokst, og ingenting hadde sagt fra. Listen under SPEILER derfor steg 5,
# den er ikke et utvalg av den; `test_selvrevers_speiler_vedlikeholdsvinduet`
# måler de to mot hverandre så et nytt stopp uten et nytt start blir rødt.
#
# Formen er steg 8s, ikke steg 5s: TIMERNE startes, ikke oneshot-tjenestene
# bak dem — å starte en oneshot direkte er å kjøre jobben nå, mens timerens
# jobb er å velge når. `enable` gjøres aldri her: reverseringen skal
# gjenopprette, ikke innføre.
SELVREVERS_ENHETER="disponit-api.socket disponit-api.service
disponit-m37.service disponit-helse.timer disponit-varselsender.timer
disponit-domenerevalidering.timer disponit-artefaktrydding.timer
disponit-evidensreaper.timer disponit-plan.timer
disponit-rydd-pending.timer disponit-backup.timer
disponit-domeneverifisering.timer disponit-wcag-audit.service
disponit-m57-utsending.timer
disponit-backupstatus.timer disponit-selvtest.timer
disponit-kvalitetsprofil.timer
disponit-lagermaaling.timer
disponit-begrepssveip.timer
disponit-tilgangssveip.timer
disponit-personvernsveip.timer
disponit-compliancesveip.timer
disponit-avstemmingssveip.timer
disponit-henvendelsessveip.timer
disponit-onboardingsveip.timer
disponit-fordringssveip.timer
disponit-leverandorsveip.timer
disponit-fakturasveip.timer
disponit-prosjektsveip.timer
disponit-prisboksveip.timer
disponit-lagersveip.timer
disponit-kontovaktsveip.timer
disponit-betalingssveip.timer
disponit-adressesveip.timer
disponit-lonnssveip.timer
disponit-kampanjesveip.timer
disponit-motpartssveip.timer
disponit-sanksjonssveip.timer
disponit-anbudssveip.timer
disponit-tilskuddssveip.timer
disponit-merkevaresveip.timer
disponit-ehfsveip.timer
disponit-tollkodesveip.timer
disponit-myndighetssveip.timer
disponit-postjournalsveip.timer
disponit-hmssveip.timer
disponit-likviditetssveip.timer
disponit-prognosesveip.timer
disponit-optimalisatorsveip.timer
disponit-motesveip.timer
disponit-innholdssveip.timer
disponit-telefonisveip.timer
disponit-sveipestatus.timer"

# Codex P2 (runde 2): vilkåret var `is-enabled`, og det måler UNIT-FILA,
# ikke driften — `systemctl --help` skiller dem eksplisitt. En timer eller
# wcag-arbeider en operatør bevisst hadde stoppet (uten å disable den) ville
# derfor blitt AKTIVERT av en mislykket deploy, mens en enhet som kjørte
# uten å være enablet ble stående nede. Begge er endringer utrullingen ikke
# har mandat til: reverseringen skal gjenopprette tilstanden fra FØR vinduet,
# og den tilstanden finnes bare ett sted — i driften, målt før steg 5 river
# den. Settet snapshottes derfor der, og både startlista og dommen leser
# NØYAKTIG det samme snapshotet.
AKTIVE_FOR_VINDUET=""
selvrevers() {
  echo "AVBRUTT: $1 — forsøker selv-reversering (forrige release,"
  echo "symlinken er urørt: $FORRIGE)"
  # Codex P1 (runde 4): CREDENTIALENE TILBAKE FØR GAMMEL KODE BOOTER PÅ DEM.
  # De tre forrige rundene på denne funksjonen målte hvilke ENHETER som
  # startes; dette er den tilstanden de startes MOT. Steg 4 skrev
  # kandidatens verdier over forrige releases, `LoadCredential` leser fila
  # på nytt ved hver aktivering, og `DISPONIT_SEMANTIKK_MILJO` er regnet ut
  # med kandidatens kode mens forrige releases boot-port måler den mot sin
  # egen. Uten denne tilbakestillingen er «forrige release kjører igjen» et
  # utsagn om binæren, ikke om konfigurasjonen den kjører på.
  #
  # Cursor P1 (runde 7): TILBAKESTILLINGEN SKJER FØRST — FØR RULLBAKK-GATEN.
  # Den sto etter gaten, altså bare på stien som faktisk STARTER enheter.
  # NEKTET-grenen `exit 1`-er før den, og etterlot kandidatens credentials
  # som den levende konfigurasjonen på en vert der `aktiv` peker på forrige
  # release og alt er stoppet. Verre enn øyeblikket: snapshotet lever bare
  # til neste kjøring, og steg 4 gjør `rm -rf "$CRED_FORVINDU"` og
  # snapshotter så DEN FORURENSEDE tilstanden som «før vinduet». Da er
  # forrige releases konfigurasjon borte for godt, og en operatør som
  # starter enhetene manuelt mot den «urørte» symlinken booter gammel binær
  # på ny konfig — nøyaktig klassen runde 4 lukket, gjennom den ene stien
  # runde 4 ikke dekket. Gjenopprettingen hører derfor før hver `exit` i
  # funksjonen: den er billig, idempotent, og starter ingenting.
  #
  # Tilbakestillingen SKRIVER OVER, den rydder ikke: en credential
  # kandidaten la til og forrige release ikke kjenner, blir liggende. Det
  # er med vilje — forrige releases units laster bare de filene deres egen
  # `LoadCredential` navngir, så en ekstra fil er inert, mens et `rm -rf`
  # her ville lagt et destruktivt steg inn i selve feilhåndteringen.
  #
  # Snapshotet ($CRED_FORVINDU) røres ikke av kopieringen, så rullbakk-gaten
  # under leser fortsatt forrige releases `api/DATABASE_URL` derfra.
  GJENOPPRETTET=""
  for kat in "$CRED_FORVINDU"/*/; do
    [ -d "$kat" ] || continue
    cp -a "$kat" /etc/disponit/
    GJENOPPRETTET="$GJENOPPRETTET $(basename "$kat")"
  done
  echo "credentials tilbakestilt til før vinduet:" \
       "${GJENOPPRETTET:- ingen — /etc/disponit var tomt før steg 4}"
  # Codex P1 (runde 2): symlinken er urørt, men BASEN er ikke nødvendigvis
  # det. Steg 6 migrerer runtime-basen FØR testbasen, og steg 6b kjører
  # etter begge: går runtime grønt og testbasen eller deploy-porten rødt,
  # bærer runtime-basen alt kandidatens forward-only-sett mens `aktiv`
  # peker på forrige release. Å starte enhetene da er ikke å gjenopprette
  # — det er å sette GAMLE arbeidere på et NYTT skjema. API-et nekter selv
  # (`krev_migrasjonstilstand`, eksakt samsvar), men M-37 og timerne har
  # ingen slik bootport: de ville kjørt videre mot et skjema dette skriptet
  # nettopp erklærte inkompatibelt, og feilgrenen stopper dem aldri igjen.
  #
  # Dommen felles derfor FØR første `systemctl start`, og den MÅLES:
  # `rollbackmaal_kompatibelt` (lib-opp.sh, samme kall steg 9 bruker)
  # leser forrige releases migrasjonsversjoner mot de FAKTISK anvendte i
  # runtime-basen — bootportens egen fasit, ikke en slutning fra hva denne
  # kjøringen rakk å migrere (#127-lærdommen). Er den rød, er «umålt» ikke
  # «kompatibelt»: ingenting startes.
  #
  # Codex P2 (runde 6): OG DEN MÅLES PÅ BASEN SOM FAKTISK BOOTES.
  # F13 (runde 4) gjorde at forrige release starter på den TILBAKESTILTE
  # `DATABASE_URL` fra snapshotet, ikke på kandidatens env. Dommen leste
  # likevel `DISPONIT_MIGRATOR_URL` — kandidatens migrator-DSN. Peker de to
  # på samme base (normaltilfellet), er de samme lesing gjennom to roller
  # og dommen er uendret. Flyttet DENNE utrullingen basen — ny vert, ny
  # base, et DSN-bytte i miljøfila — måler gaten kandidatens base mens
  # kjøreren starter mot forrige releases: nekter reverseringen på et
  # migrasjonssett ingen av de gjenopprettede enhetene noensinne vil se,
  # og lar hver enhet stå stoppet selv om forrige release og forrige base
  # kjørte sammen sekundet før vinduet.
  #
  # Kilden er `api/DATABASE_URL` fra snapshotet med vilje: det er NØYAKTIG
  # fila `disponit-api.service` sin `LoadCredential` leser, og
  # `krev_migrasjonstilstand` (eksakt samsvar) feller sin dom gjennom den.
  # Gaten predikerer bootportens svar, så den må lese bootportens base.
  # Mangler fila — fersk vert uten `/etc/disponit/api` før vinduet — er
  # kandidatens migrator-DSN fortsatt det nærmeste vi har, og det er
  # dagens oppførsel. Er DSN-en ubrukelig, feller `rollbackmaal_kompatibelt`
  # «umålt er ikke kompatibelt», og det er riktig: en DSN forrige release
  # ikke kan koble seg opp med, kan den heller ikke starte på.
  ROLLBACKDOM=""
  ROLLBACKBASE="${DISPONIT_MIGRATOR_URL:-}"
  if [ -s "$CRED_FORVINDU/api/DATABASE_URL" ]; then
    ROLLBACKBASE=$(cat "$CRED_FORVINDU/api/DATABASE_URL")
  fi
  if [ -z "$FORRIGE" ]; then
    ROLLBACKDOM="ingen forrige release å boote (aktiv-symlinken fantes ikke)"
  else
    ROLLBACKDOM=$(rollbackmaal_kompatibelt "$FORRIGE" "$ROLLBACKBASE") || \
      ROLLBACKDOM="${ROLLBACKDOM:-rullbakkmålet lot seg ikke måle}"
  fi
  if [ -n "$ROLLBACKDOM" ]; then
    echo "SELV-REVERSERING NEKTET: $ROLLBACKDOM."
    echo "Basen har flyttet seg forbi forrige release, og da kan forrige"
    echo "kode ikke bootes mot dette skjemaet (bootportens dom). Enhetene"
    echo "blir STÅENDE STOPPET, med vilje — en gammel arbeider mot et nytt"
    echo "skjema er verre enn en stoppet arbeider, og M-37 og timerne har"
    echo "ingen bootport som ville nektet for dem."
    echo "STÅENDE STOPPET (var i drift før vinduet):" \
         "${AKTIVE_FOR_VINDUET:- ingen — ingenting kjørte før vinduet}"
    echo "Credentialene er likevel tilbakestilt til før vinduet, så en"
    echo "manuell start mot den urørte symlinken booter forrige release på"
    echo "forrige releases konfigurasjon — men SKJEMAET er fortsatt fremme."
    echo "Rettingen er FREMOVER og den er NÅ: fullfør deployen mot et"
    echo "skjema begge basene deler, eller rull frem til et sett forrige"
    echo "release bærer. Deployen er avbrutt."
    exit 1
  fi
  # Snapshotet, i den rekkefølgen unitene avhenger av hverandre: socket og
  # API først (`disponit-wcag-audit.service` har `After=disponit-api.service`),
  # så resten. Rekkefølgen ligger i SELVREVERS_ENHETER, og snapshotet er
  # bygget i den rekkefølgen.
  for enhet in $AKTIVE_FOR_VINDUET; do
    systemctl start "$enhet" 2>/dev/null || true
  done
  sleep 2
  # Dommen måles på NØYAKTIG det settet som ble startet — samme variabel,
  # ikke en ny liste som kan drifte fra den. `2>/dev/null || true` over
  # sluker startfeil med vilje (en enhet som ikke finnes på verten skal ikke
  # felle reverseringen), og nettopp derfor kan ikke suksess utledes av
  # exit-koden: den må MÅLES.
  #
  # TERSKELEN ER HETEROGEN MED VILJE (#182, eiervalg A + driftsvedtak
  # 24/8): API-et er SELVREVERSERT først når `/ready` svarer over
  # socketen — målt med NØYAKTIG steg 8s egen kropp (`vent_paa_ready`,
  # lib-opp.sh), så de to dommene aldri kan drifte. `is-active` var feil
  # svar for API-et (`Type=simple`: «active» er prosessen, ikke svaret),
  # men står for M-37 og timerne til de har et eget klarhetssignal — de
  # har ingen `/ready`, og heartbeaten som kunne blitt ett er en senere
  # maskin, ikke en utvidelse av #182.
  NEDE=""
  for enhet in $AKTIVE_FOR_VINDUET; do
    systemctl is-active --quiet "$enhet" || NEDE="$NEDE $enhet"
  done
  case " $AKTIVE_FOR_VINDUET " in *" disponit-api.service "*)
    case " $NEDE " in *" disponit-api.service "*) : ;; *)
      vent_paa_ready || NEDE="$NEDE disponit-api.service(/ready)" ;;
    esac ;;
  esac
  if [ -z "$NEDE" ]; then
    echo "SELVREVERSERT: forrige release kjører igjen — hver enhet som var i"
    echo "drift før vinduet er aktiv igjen:$AKTIVE_FOR_VINDUET."
    echo "Feilen rettes FREMOVER; deployen er avbrutt."
  else
    echo "SELV-REVERSERING FEILET. Fortsatt nede:$NEDE"
    echo "Er API-et blant dem, kan forrige kode ikke starte mot basens"
    echo "migrasjonssett (bootportens dom). Uansett hvilke enheter det"
    echo "gjelder: de er STOPPET av dette vinduet, og manuell"
    echo "fremoverrettet retting kreves NÅ."
  fi
  exit 1
}

# --- 5. VEDLIKEHOLDSVINDU: stopp tjenester OG helsetimer (V1) --------------
# Timeren stoppes også: den skal verken telle feil mot stoppede tjenester
# eller utløse en restart midt i migrasjonsvinduet.
#
# PR-015 (Codex P2): driftstimerne MÅ med. Er de først aktivert av en tidligere
# utrulling, kan de ellers fyre midt i dette vinduet — mens credentials skrives
# om, forward-only-migrasjoner kjører og `aktiv`-symlenken byttes — og en
# revaliderings- eller ryddekjøring ville da kjørt halvt gammel, halvt ny kode
# mot et skjema i bevegelse. Både TIMERNE og de aktive oneshot-TJENESTENE
# stoppes: å stoppe timeren alene avbryter ikke en kjøring som alt er i gang.
# `systemctl stop` på en oneshot venter til prosessen er ute, så vinduet åpnes
# først når begge arbeiderne faktisk er stille.
#
# Cursor P2-4/P2-5 (#178, runde 4): `disponit-rydd-pending` og
# `disponit-backup` sto i `UNITS`, ble `enable --now`-et i steg 8 — og ble
# ALDRI stoppet her. De to kjørte altså tvers gjennom vedlikeholdsvinduet,
# mot nøyaktig det skjemaet i bevegelse avsnittet over er skrevet om:
# ryddejobben gjør DELETE mot `api_tokener`, og `pg_dump` midt i et
# forward-only-sett gir en dump som er halvt gammelt og halvt nytt skjema —
# en backup som ikke kan restores er verre enn ingen backup, fordi den ser
# ut som en.
#
# SNAPSHOT FØRST (Codex P2, runde 2): hvilke enheter som var I DRIFT finnes
# bare å lese HER — ett sekund senere har vinduet revet tilstanden, og da er
# `is-enabled` det eneste som er igjen å gjette ut fra. Gjetningen er feil i
# begge retninger: en enhet en operatør bevisst stoppet er fortsatt enablet,
# og en enhet som kjørte trenger ikke være det. Snapshotet er reverseringens
# eneste sannhet om hva «tilbake» betyr, og det skrives til loggen så
# operatøren ser det samme settet skriptet vil gjenopprette.
for enhet in $SELVREVERS_ENHETER; do
  if systemctl is-active --quiet "$enhet"; then
    AKTIVE_FOR_VINDUET="$AKTIVE_FOR_VINDUET $enhet"
  fi
done
echo "vedlikeholdsvindu: i drift før stopp —" \
     "${AKTIVE_FOR_VINDUET:- ingen}"
# Cursor P2 (#178, runde 7): OGSÅ `disponit-helse.service`. Å stoppe timeren
# hindrer NESTE aktivering, ikke den som alt løper — samme lærdom som
# rydd-pending og backup fikk i runde 4, og helsesjekken er den farligste av
# dem: en pågående kjøring med teller ≥ MAKS_FEIL kaller
# `disponit-restart-helper` på API-et og M-37, altså restarter nøyaktig de
# tjenestene vinduet nettopp stoppet — midt i migrasjonen, mot et skjema i
# bevegelse. Den hører derfor i den SAMME `systemctl stop` som timeren sin,
# og IKKE i `SELVREVERS_ENHETER`: oneshoten er timerens å starte (steg 8s
# form), og reverseringen skal gjenopprette timeplanen, ikke kjøre jobben nå.
systemctl stop disponit-helse.timer disponit-helse.service \
    disponit-m37.service \
    disponit-api.service disponit-api.socket 2>/dev/null || true
systemctl stop disponit-m57-utsending.timer disponit-m57-utsending.service 2>/dev/null || true
# 090/091: driftstatusens lesejobb og selvtesten stoppes i samme vindu som
# de andre timerne. Begge er idempotente over sin egen tilstand — en
# rapport som ikke ble registrert nå, registreres neste halvtime, og en
# selvtestrunde som ikke ble kjørt måler tilstanden på nytt neste time —
# så vinduet koster ingenting utover en manglende måling i det.
systemctl stop disponit-backupstatus.timer disponit-backupstatus.service \
    disponit-selvtest.timer disponit-selvtest.service 2>/dev/null || true
# 092 (M-3): profileringen stoppes i samme vindu. Den er idempotent over
# sin egen tilstand — en profil som ikke ble målt nå, måles på nytt neste
# døgn — så vinduet koster ingenting utover den ene manglende målingen.
systemctl stop disponit-kvalitetsprofil.timer \
    disponit-kvalitetsprofil.service 2>/dev/null || true
# 093 (M-4): retensjonsmålingen stoppes i samme vindu. Den er idempotent
# over sin egen tilstand — en måling som ikke ble ferdig står som
# `avbrutt = true` og gjøres ferdig av neste kjøring, så vinduet koster
# ingenting utover et døgn uten et ferskt bilde.
systemctl stop disponit-lagermaaling.timer \
    disponit-lagermaaling.service 2>/dev/null || true
# 095 (M-9): begrepssveipen stoppes i samme vindu. Den er idempotent over
# sin egen tilstand — et funn som ikke ble reist i natt, reises i morgen,
# og et som alt står åpent får bare et nyere `sist_sett_sveip` — så
# vinduet koster ingenting utover én manglende sveip i det.
systemctl stop disponit-begrepssveip.timer disponit-begrepssveip.service \
    2>/dev/null || true
# 097 (M-12): gjennomgangssveipen stoppes i samme vindu. Den er
# idempotent over sin egen tilstand — et funn som ikke ble reist i natt,
# reises i morgen, og et som alt står åpent får bare et nyere
# `sist_sett_sveip` — så vinduet koster ingenting utover én manglende
# sveip i det.
systemctl stop disponit-tilgangssveip.timer disponit-tilgangssveip.service \
    2>/dev/null || true
# 099 (M-30): fristsveipen stoppes i samme vindu. Den er idempotent over
# sin egen tilstand — et funn som ikke ble reist i natt, reises i morgen,
# og et som alt står åpent får bare et nyere `sist_sett_sveip` — så
# vinduet koster ingenting utover én manglende sveip i det.
systemctl stop disponit-personvernsveip.timer \
    disponit-personvernsveip.service 2>/dev/null || true
# 100 (M-34): etterprøvingssveipen stoppes i samme vindu og av samme
# grunn — den er idempotent over sin egen tilstand: et funn som ikke ble
# reist i natt, reises i morgen, og et som alt står åpent får bare et
# nyere `sist_sett_sveip`.
systemctl stop disponit-compliancesveip.timer \
    disponit-compliancesveip.service 2>/dev/null || true
# 101 (M-13): avstemmingssveipen stoppes i samme vindu og av samme grunn
# — den er idempotent over sin egen tilstand: et funn som ikke ble reist
# i natt, reises i morgen, og et som alt står åpent får bare et nyere
# `sist_sett_sveip`.
systemctl stop disponit-avstemmingssveip.timer \
    disponit-avstemmingssveip.service 2>/dev/null || true
# 102 (M-17): henvendelsessveipen stoppes i samme vindu og av samme grunn
# — funnene er idempotente over sin egen tilstand.
systemctl stop disponit-henvendelsessveip.timer \
    disponit-henvendelsessveip.service 2>/dev/null || true
# 103 (M-18): onboardingsveipen stoppes i samme vindu — funnene er
# idempotente over sin egen tilstand.
systemctl stop disponit-onboardingsveip.timer \
    disponit-onboardingsveip.service 2>/dev/null || true
# 104 (M-23): fordringssveipen stoppes i samme vindu — funnene er
# idempotente over sin egen tilstand.
systemctl stop disponit-fordringssveip.timer \
    disponit-fordringssveip.service 2>/dev/null || true
# 105 (M-24): leverandørsveipen stoppes i samme vindu — funnene er
# idempotente over sin egen tilstand.
systemctl stop disponit-leverandorsveip.timer \
    disponit-leverandorsveip.service 2>/dev/null || true
# 106 (M-14): fakturasveipen stoppes i samme vindu — funnene er
# idempotente over sin egen tilstand.
systemctl stop disponit-fakturasveip.timer \
    disponit-fakturasveip.service 2>/dev/null || true
# 107 (M-25): prosjektsveipen stoppes i samme vindu — funnene er
# idempotente over sin egen tilstand.
systemctl stop disponit-prosjektsveip.timer \
    disponit-prosjektsveip.service 2>/dev/null || true
# 108 (M-26): prisboksveipen stoppes i samme vindu — funnene er
# idempotente over sin egen tilstand.
systemctl stop disponit-prisboksveip.timer \
    disponit-prisboksveip.service 2>/dev/null || true
# 109 (M-27): lagersveipen stoppes i samme vindu — funnene er
# idempotente over sin egen tilstand.
systemctl stop disponit-lagersveip.timer \
    disponit-lagersveip.service 2>/dev/null || true
# 110 (M-42): kontovaktsveipen stoppes i samme vindu — funnene er
# idempotente over sin egen tilstand.
systemctl stop disponit-kontovaktsveip.timer \
    disponit-kontovaktsveip.service 2>/dev/null || true
# 111 (M-41): betalingssveipen stoppes i samme vindu — funnene er
# idempotente over sin egen tilstand.
systemctl stop disponit-betalingssveip.timer \
    disponit-betalingssveip.service 2>/dev/null || true
# 112 (M-19): adressesveipen stoppes i samme vindu — funnene er
# idempotente, så neste døgn tar kjøringen igjen.
systemctl stop disponit-adressesveip.timer \
    disponit-adressesveip.service 2>/dev/null || true
# 113 (M-39): lønnssveipen stoppes i samme vindu — funnene er
# idempotente, så neste døgn tar kjøringen igjen.
systemctl stop disponit-lonnssveip.timer \
    disponit-lonnssveip.service 2>/dev/null || true
# 114 (M-44): kampanjesveipen stoppes i samme vindu — funnene er
# idempotente, så neste døgn tar kjøringen igjen.
systemctl stop disponit-kampanjesveip.timer \
    disponit-kampanjesveip.service 2>/dev/null || true
# 119 (M-51): tilskuddssveipen stoppes i samme vindu — funnene er
# idempotente. Ingen frist går tapt: den står på ordningsraden.
systemctl stop disponit-tilskuddssveip.timer \
    disponit-tilskuddssveip.service 2>/dev/null || true
# 120 (M-55): merkevaresveipen stoppes i samme vindu — varslene er
# idempotente. Ingen bevis går tapt: bevaringskopien står i basen, og
# sveipen tar ikke kopier.
systemctl stop disponit-merkevaresveip.timer \
    disponit-merkevaresveip.service 2>/dev/null || true
# 121 (M-54): EHF-sveipen stoppes i samme vindu — funnene er
# idempotente. Ingen dom går tapt: valideringene står i basen.
systemctl stop disponit-ehfsveip.timer \
    disponit-ehfsveip.service 2>/dev/null || true
# 122 (M-52): tollkodesveipen stoppes i samme vindu — funnene er
# idempotente. Ingen kode går tapt: forslagene står i basen.
systemctl stop disponit-tollkodesveip.timer \
    disponit-tollkodesveip.service 2>/dev/null || true
systemctl stop disponit-myndighetssveip.timer \
    disponit-myndighetssveip.service 2>/dev/null || true
systemctl stop disponit-postjournalsveip.timer \
    disponit-postjournalsveip.service 2>/dev/null || true
systemctl stop disponit-hmssveip.timer \
    disponit-hmssveip.service 2>/dev/null || true
systemctl stop disponit-likviditetssveip.timer \
    disponit-likviditetssveip.service 2>/dev/null || true
# 130 (M-33): prognosesveipen stoppes i samme vindu. Funnene er
# idempotente, så neste døgn tar kjøringen igjen — og ingen frist går
# tapt: målefristen står i `prognosekrav`, ikke i sveipen.
systemctl stop disponit-prognosesveip.timer \
    disponit-prognosesveip.service 2>/dev/null || true
# 132 (M-36): optimalisatorsveipen stoppes i samme vindu. Funnene er
# idempotente, og målefristen står i `optimaliseringskrav`.
systemctl stop disponit-optimalisatorsveip.timer \
    disponit-optimalisatorsveip.service 2>/dev/null || true
# 133 (M-7): møtesveipen stoppes i samme vindu. Funnene er idempotente,
# og referatfristen står i `motekrav`, ikke i sveipen.
systemctl stop disponit-motesveip.timer \
    disponit-motesveip.service 2>/dev/null || true
# 134 (M-20): innholdssveipen stoppes i samme vindu. Funnene er
# idempotente, og kildevinduet står i `innholdskrav`, ikke i sveipen.
systemctl stop disponit-innholdssveip.timer \
    disponit-innholdssveip.service 2>/dev/null || true
# 135 (M-43): telefonisveipen stoppes i samme vindu. Funnene er
# idempotente, og fristene står i `telefonikrav`, ikke i sveipen.
systemctl stop disponit-telefonisveip.timer \
    disponit-telefonisveip.service 2>/dev/null || true
# 118 (M-46): anbudssveipen stoppes i samme vindu — funnene er
# idempotente, så neste døgn tar kjøringen igjen. Ingen frist går tapt:
# fristen står på anbudsraden, ikke i sveipen.
systemctl stop disponit-anbudssveip.timer \
    disponit-anbudssveip.service 2>/dev/null || true
# 117 (M-49): sanksjonssveipen stoppes i samme vindu — funnene er
# idempotente, så neste døgn tar kjøringen igjen. Ingen avklaring går
# tapt: sveipen avklarer ingenting.
systemctl stop disponit-sanksjonssveip.timer \
    disponit-sanksjonssveip.service 2>/dev/null || true
# 116 (M-48): motpartssveipen stoppes i samme vindu — funnene er
# idempotente. Blir en RESERVASJON stående fordi kjøringen ble stoppet
# mellom reservasjon og fullføring, er det riktig utfall og ikke et
# tap: neste kjøring finner den som `oppslag_uten_svar` og setter den
# til `forlatt` etter seks timer.
systemctl stop disponit-motpartssveip.timer \
    disponit-motpartssveip.service 2>/dev/null || true
# 115: sveipestatusen stoppes i samme vindu. Den fører flåtens
# tilstand på nytt ved neste kjøring; ingenting går tapt.
systemctl stop disponit-sveipestatus.timer \
    disponit-sveipestatus.service 2>/dev/null || true
systemctl stop disponit-varselsender.timer disponit-varselsender.service \
    2>/dev/null || true
systemctl stop disponit-domenerevalidering.timer \
    disponit-artefaktrydding.timer disponit-evidensreaper.timer \
    disponit-plan.service disponit-plan.timer \
    disponit-domenerevalidering.service \
    disponit-artefaktrydding.service \
    disponit-evidensreaper.service \
    disponit-wcag-audit.service \
    disponit-domeneverifisering.timer \
    disponit-domeneverifisering.service 2>/dev/null || true
systemctl stop disponit-rydd-pending.timer disponit-rydd-pending.service \
    disponit-backup.timer disponit-backup.service 2>/dev/null || true

# --- 6. Migrasjoner (begge baser) — FØR ny release aktiveres ---------------
# P1 runde 1: hver base melder sitt til rapporten. Første utgave lot siste
# iterasjon (TESTbasen) overskrive resultatet — en forward-only-migrasjon
# kjørt kun i runtime-basen ville blitt rapportert rollback-kompatibel.
RAPPORT_KATALOG=$(mktemp -d)
trap 'rm -rf "$RAPPORT_KATALOG"' EXIT
for par in "runtime:$DISPONIT_MIGRATOR_URL" "test:$DISPONIT_TEST_MIGRATOR_DSN"; do
  base=${par%%:*}; url=${par#*:}
  (cd "$KILDE" && DISPONIT_MIGRATOR_URL="$url" \
     "$ROT/.venv/bin/python" deploy/staging/migrer.py disponit) \
     | tee "$RAPPORT_KATALOG/$base" || {
    selvrevers "migrasjon ($base) feilet"
  }
done

# --- 6b. Deploy-portene fra PR-014c §5 — register vs kodefestet type -------
# ETTER migrasjonene (skjemaet er ferskt), FØR release-byttet: en
# registerrad uten kodefestet OPPDRAGSTYPER-type, en ekstern_lesing-
# kontrakt uten målautorisasjonsflagg på typen, eller en artefakttype hvis
# skjema_hash ikke finnes i `artefaktskjema` (014c port 4), stopper
# deployen mens forrige release fortsatt er intakt. Runtime-basen er
# sannheten som betjener kunder; testbasen bærer syntetiske typerader per
# konstruksjon og måles ikke her — og det er nettopp derfor skjemaporten
# bor HER og ikke som en migrasjon: en migrasjon treffer begge basene, og
# de syntetiske hashene i testbasen har ingen skjemaer å registrere.
(cd "$KILDE" && DATABASE_URL="$DATABASE_URL" DISPONIT_REPO="$KILDE" \
   "$ROT/.venv/bin/python" deploy/staging/deployport-modultyper.py) || {
  selvrevers "deploy-port (014c §5) rød"
}

# #162: inndata-lageret — krypterte bunter på FS, eid av api-brukeren.
# Opprettes FØR release-byttet så en fersk vert aldri ENOENT-er i
# opplastingsveien; 0700 hele veien, payloaden er tenant-DEK-kryptert.
#
# EGEN rot, ikke /var/lib/disponit/inndata (Codex P1): den katalogen er
# `disponit-artefaktrydding.service` sin StateDirectory, eid av
# `disponit-domener` med 0750. Denne linjen ville på en fersk vert
# opprettet også FORELDEREN som `disponit-api:0700` (GNU install -d gir
# hver komponent den oppretter de oppgitte attributtene) og tatt katalogen
# fra ryddeuniten — og på en vert der ryddeuniten alt hadde kjørt, ville
# API-et ikke kunnet TRAVERSERE ned til barnet. `StateDirectory=` i
# api-uniten eier nå denne katalogen; linjen her står igjen som
# førstegangsopprettelsen, med nøyaktig samme eier og modus som systemd
# setter, så de to aldri drar den fram og tilbake.
install -d -m 700 -o disponit-api -g disponit-api /var/lib/disponit-inndata

# --- 7. Atomisk release-bytte + units --------------------------------------
ln -sfn "$KILDE" "$AKTIV"
# Feilsonen er passert: fra og med linjen over er symlinken byttet, og
# `selvrevers()` kalles ikke lenger (`test_hver_feil_i_vinduet_kaller_selvrevers`
# avgrenser sonen til nøyaktig dette intervallet). Credential-snapshotet har
# ingen leser igjen, og det er en KOPI av hemmelighetene i /etc/disponit —
# den skal ikke bli liggende som stabil tilstand på verten. Feiler deployen
# før dette punktet, blir kopien liggende til neste kjøring rydder den; da
# er den fortsatt 700/root, og fortsatt de samme hemmelighetene som allerede
# ligger ved siden av.
rm -rf "$CRED_FORVINDU"
for u in $UNITS; do
  install -m 644 "$KILDE/deploy/staging/$u" "/etc/systemd/system/$u"
done
# Hjelperskriptene installeres HER — i mutasjonsfasen, etter gaten og
# mens tjenestene og helsetimeren er stoppet. Preflighten verifiserte
# nøyaktig disse filene i den falske roten; ingen gammel timer kan ha
# kjørt kandidatkode før dette punktet.
install -m 755 "$KILDE/deploy/staging/helse-sjekk.sh" \
    /usr/local/lib/disponit-helse-sjekk
install -m 755 "$KILDE/deploy/staging/restart-helper.sh" \
    /usr/local/lib/disponit-restart-helper
install -m 440 "$KILDE/deploy/staging/sudoers-disponit-helse" \
    /etc/sudoers.d/disponit-helse
visudo -cf /etc/sudoers.d/disponit-helse >/dev/null
# journald: IKKE noe globalt tak. Klarsignalet forutsatte dedikert vert,
# men DEPLOY.md sier eksplisitt at Cloud Server S deler maskin med et
# annet produkt — og v2 §7s egen regel for delt vert er per-unit
# LogRateLimit (satt i unit-filene). Global SystemMaxUse ville skrevet om
# naboproduktets loggretensjon. Flagget som avvik i PR-beskrivelsen.
systemctl daemon-reload

# --- 8. Start + readiness over SOCKETEN (PR-009b §0: ingen TCP) ------------
systemctl enable --now disponit-api.socket
systemctl enable --now disponit-api.service disponit-m37.service
systemctl enable --now disponit-helse.timer disponit-rydd-pending.timer \
    disponit-backup.timer
# PR-015: revalidering (timeplan i .timer) + rydding (hvert 15. min). Begge
# er Type=oneshot bak en .timer — enable --now på TIMEREN, ikke tjenesten.
systemctl enable --now disponit-domenerevalidering.timer \
    disponit-artefaktrydding.timer
# 038 §5: evidensfrist-reaperen — samme form (oneshot bak timer, kjøres
# som disponit-domener; hele regelen ligger i reap_evidensfrister i basen).
systemctl enable --now disponit-evidensreaper.timer
# 044: plan-materialisereren — hvert 5. minutt.
systemctl enable --now disponit-plan.timer
# m_wcag_audit-arbeideren INSTALLERES men enables IKKE her: den skal
# først i drift når modulen er AKSEPTERT (manifest-sjekklisten grønn,
# status aktiv). Var den alt enablet av aksept-runden, startes den igjen.
if systemctl is-enabled disponit-wcag-audit.service >/dev/null 2>&1; then
  systemctl start disponit-wcag-audit.service
fi
# 039: selvbetjent domeneverifisering — hvert 5. minutt.
systemctl enable --now disponit-domeneverifisering.timer
# Varselsenderen: samme form, og den MÅ startes igjen her. Steg 5 stopper
# timeren i vedlikeholdsvinduet — uten denne linjen var utrullingen det som
# slo senderen av, permanent, og køen ville bare vokst. Timeren, ikke
# tjenesten: oneshot-en er timerens å starte.
systemctl enable --now disponit-varselsender.timer
# 081: M-57-utsendingen — samme form og samme credential som
# varselsenderen; uten smtp.env rører den ingenting.
systemctl enable --now disponit-m57-utsending.timer
# 090 (M-10): backupens verifisering inn i basen, hvert 30. minutt.
# Uten credentialen står jobben med exit 2 og rører ingenting — men
# preflighten over gater DSN-en, så det skal ikke kunne skje.
systemctl enable --now disponit-backupstatus.timer
# 091 (M-11): selvtestrunden, hver time. Samme form.
systemctl enable --now disponit-selvtest.timer
# 092 (M-3): datakvalitetsprofileringen, daglig. Samme form; uten
# credentialen står jobben med exit 2 og rører ingenting — men
# preflighten over gater DSN-en, så det skal ikke kunne skje.
systemctl enable --now disponit-kvalitetsprofil.timer
# 093 (M-4): retensjonsmålingen, daglig 03:17. Samme form.
systemctl enable --now disponit-lagermaaling.timer
# 095 (M-9): begrepssveipen, én gang i døgnet med spredning. Uten
# credentialen står jobben med exit 2 og rører ingenting — men
# preflighten over gater DSN-en, så det skal ikke kunne skje.
systemctl enable --now disponit-begrepssveip.timer
# 097 (M-12): gjennomgangssveipen, én gang i døgnet med spredning.
# Samme form; uten credentialen står jobben med exit 2 og rører
# ingenting — men preflighten over gater DSN-en, så det skal ikke kunne
# skje.
systemctl enable --now disponit-tilgangssveip.timer
# 099 (M-30): fristsveipen, én gang i døgnet med spredning. Samme form;
# uten credentialen står jobben med exit 2 og rører ingenting — men
# preflighten over gater DSN-en, så det skal ikke kunne skje.
systemctl enable --now disponit-personvernsveip.timer
# 100 (M-34): etterprøvingssveipen, én gang i døgnet med spredning.
# Samme form; uten credentialen står jobben med exit 2 og rører
# ingenting — men preflighten over gater DSN-en, så det skal ikke kunne
# skje.
systemctl enable --now disponit-compliancesveip.timer
# 101 (M-13): avstemmingssveipen, én gang i døgnet med spredning. Samme
# form; uten credentialen står jobben med exit 2 og rører ingenting —
# men preflighten over gater DSN-en, så det skal ikke kunne skje.
systemctl enable --now disponit-avstemmingssveip.timer
# 102 (M-17): henvendelsessveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-henvendelsessveip.timer
# 103 (M-18): onboardingsveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-onboardingsveip.timer
# 104 (M-23): fordringssveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-fordringssveip.timer
# 105 (M-24): leverandørsveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-leverandorsveip.timer
# 106 (M-14): fakturasveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-fakturasveip.timer
# 107 (M-25): prosjektsveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-prosjektsveip.timer
# 108 (M-26): prisboksveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-prisboksveip.timer
# 109 (M-27): lagersveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-lagersveip.timer
# 110 (M-42): kontovaktsveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-kontovaktsveip.timer
# 111 (M-41): betalingssveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-betalingssveip.timer
# 112 (M-19): adressesveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-adressesveip.timer
# 113 (M-39): lønnssveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-lonnssveip.timer
# 114 (M-44): kampanjesveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-kampanjesveip.timer
# 116 (M-48): motpartssveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-motpartssveip.timer
# 117 (M-49): sanksjonssveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-sanksjonssveip.timer
# 118 (M-46): anbudssveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-anbudssveip.timer
# 119 (M-51): tilskuddssveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-tilskuddssveip.timer
# 120 (M-55): merkevaresveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-merkevaresveip.timer
# 121 (M-54): EHF-sveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-ehfsveip.timer
# 122 (M-52): tollkodesveipen, én gang i døgnet med spredning.
systemctl enable --now disponit-tollkodesveip.timer
systemctl enable --now disponit-myndighetssveip.timer
systemctl enable --now disponit-postjournalsveip.timer
systemctl enable --now disponit-hmssveip.timer
systemctl enable --now disponit-likviditetssveip.timer
# 130 (M-33): prognosesveipen, 11:35 — den FØRSTE bak 11:20, og
# grunnen til at sveipestatusen under flyttet til 12:05.
systemctl enable --now disponit-prognosesveip.timer
# 132 (M-36): optimalisatorsveipen, 11:50 — SISTE trinn i stigen.
systemctl enable --now disponit-optimalisatorsveip.timer
# 133 (M-7): møtesveipen, 12:35 — klynge 9s første trinn.
systemctl enable --now disponit-motesveip.timer
# 134 (M-20): innholdssveipen, 12:50 — klynge 9s andre trinn.
systemctl enable --now disponit-innholdssveip.timer
# 135 (M-43): telefonisveipen, 13:05 — klynge 9s tredje trinn.
systemctl enable --now disponit-telefonisveip.timer
# 115: sveipestatusen, ETTER hele stigen (12:35 fra og med 133, og
# flyttet helt til 13:35 fordi klynge 9s øvrige slot alt er tildelt).
# Rekkefølgen er poenget: observatøren leser flåtens tilstand etter at
# flåten har kjørt.
systemctl enable --now disponit-sveipestatus.timer

# Klarhetsløkka bor i `vent_paa_ready` (lib-opp.sh, #182) — samme kropp
# som selvrevers() dømmer API-et med.
KLAR=nei
if vent_paa_ready; then KLAR=ja; fi

API=$(systemctl is-active disponit-api.service || true)
M37=$(systemctl is-active disponit-m37.service || true)

# --- 9. TREDELT STATUSRAPPORT (v3 §3) — ærlig, aldri en lovet rollback -----
echo
echo "== statusrapport =="
echo "(a) schema, per base:"
vurder_migrasjoner runtime "$RAPPORT_KATALOG/runtime" \
                   test    "$RAPPORT_KATALOG/test"
echo "(b) kandidat:  api=$API m37=$M37 /ready=$KLAR (release $SHA)"
# Issue #127: dommen felles mot BOOTPORTENS fasit (forrige releases
# migrasjonssett vs basens anvendte), aldri bare mot hva DENNE
# kjøringen migrerte — oppsett-postgresql.sh kan ha migrert i samme
# deploy, og da er «ingen nye her» et utsagn om kjøringen, ikke om
# rullbakken. NYE_MIGRASJONER beholdes som konservativt tilleggssignal.
ROLLBACK_DOM=""
if [ -n "$FORRIGE" ] && [ "$FORRIGE" != "$(readlink -f "$AKTIV")" ]; then
  ROLLBACK_DOM=$(rollbackmaal_kompatibelt "$FORRIGE" "$DISPONIT_MIGRATOR_URL") || true
fi
if [ -z "$FORRIGE" ] || [ "$FORRIGE" = "$(readlink -f "$AKTIV")" ]; then
  echo "(c) rollback:  ingen forrige release å vurdere — rullbakk er"
  echo "               UMÅLT, ikke lovet."
elif [ -z "$NYE_MIGRASJONER" ] && [ -z "$ROLLBACK_DOM" ]; then
  echo "(c) rollback:  forrige kode ($FORRIGE)"
  echo "               bærer NØYAKTIG basens migrasjonssett (målt mot"
  echo "               bootportens fasit) og kan bootes."
else
  echo "(c) rollback:  FORBUDT. ${NYE_MIGRASJONER:+Nye migrasjoner [$NYE_MIGRASJONER]. }${ROLLBACK_DOM}"
  echo "               Forrige kode kan IKKE startes mot dette skjemaet;"
  echo "               feil rettes FREMOVER. (Dommen er målt, ikke"
  echo "               utledet av hva denne kjøringen gjorde — #127.)"
fi
if [ -z "${DISPONIT_ARBEIDER_URL:-}" ]; then
  echo "AVVIK: DISPONIT_ARBEIDER_URL er ikke satt — arbeideren deler"
  echo "       runtime-DSN. Kjør oppsett-postgresql.sh for rolleskillet."
fi

[ "$API" = active ] && [ "$M37" = active ] && [ "$KLAR" = ja ]
