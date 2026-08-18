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
disponit-domeneverifisering.service disponit-domeneverifisering.timer
disponit-varselsender.service disponit-varselsender.timer"
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

# ============================================================
# HERFRA MUTERES SYSTEMET — gaten over er passert.
# ============================================================

# --- 3. Unix-identiteter (idempotent; v2 §3 + PR-009b V1) ------------------
getent group disponit-proxy >/dev/null || groupadd --system disponit-proxy
# PR-015: driftstimerne (revalidering + rydding) får sin EGEN Unix-bruker,
# samme skille som API/M-37/helse — et kompromittert timerkjøring skal ikke
# arve noen annen tjenestes fullmakter, og omvendt.
for b in disponit-api disponit-m37 disponit-helse disponit-domener; do
  getent passwd "$b" >/dev/null || \
    useradd --system --no-create-home --shell /usr/sbin/nologin "$b"
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
# Hver katalog `skriv_cred` skriver i MÅ opprettes FØR den skrives i —
# `skriv_cred` er en `printf >`-omdirigering, og uten katalogen feiler den i
# den MUTERENDE fasen, etter at preflighten er passert. På en vert som har
# rullet ut før, ligger katalogen igjen fra forrige gang og hullet er usynlig;
# det er den ferske verten som treffer det. `test_pr009` måler koblingen mot
# kilden, så den neste credential-katalogen er dekket uten at noen husker det.
install -d -m 700 /etc/disponit/api /etc/disponit/m37
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
skriv_cred api DISPONIT_KEK          "$DISPONIT_KEK"
skriv_cred api DISPONIT_TOKEN_PEPPER "$DISPONIT_TOKEN_PEPPER"
skriv_cred api DISPONIT_ATT_NOKLER   "$DISPONIT_ATT_NOKLER"
skriv_cred api DISPONIT_MAC_NOKLER   "$DISPONIT_MAC_NOKLER"   # PR-012 (boot-perre)
# PR-013 (V8/port 13): fest miljøsignaturen (tzdata) RELEASEN bygges med, målt
# med releasens EGEN kode. Bytter vertens tzdata etterpå, avviker boot-sjekken
# og prosessen nekter start — motoren tolker aldri tidsvinduer annerledes enn
# klassifikatoren beviste, uoppdaget.
skriv_cred api DISPONIT_SEMANTIKK_MILJO "$(PYTHONPATH="$KILDE/platform/core" \
    "$ROT/.venv/bin/python" -c 'from policy_validator import semantikk; print(semantikk.miljosignatur())')"
# PR-011b: UI-deploy-config (provider-valg + IdP-origins). Ikke hemmeligheter,
# men hydreres til os.environ via samme LoadCredential-vei. Tomme = default.
skriv_cred api DISPONIT_UI_PROVIDER    "${DISPONIT_UI_PROVIDER:-}"
skriv_cred api DISPONIT_UI_IDP_ORIGINS "${DISPONIT_UI_IDP_ORIGINS:-}"
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
systemctl stop disponit-helse.timer disponit-m37.service \
    disponit-api.service disponit-api.socket 2>/dev/null || true
systemctl stop disponit-varselsender.timer disponit-varselsender.service \
    2>/dev/null || true
systemctl stop disponit-domenerevalidering.timer \
    disponit-artefaktrydding.timer disponit-evidensreaper.timer \
    disponit-domenerevalidering.service \
    disponit-artefaktrydding.service \
    disponit-evidensreaper.service \
    disponit-domeneverifisering.timer \
    disponit-domeneverifisering.service 2>/dev/null || true

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
    echo "AVBRUTT: migrasjon ($base) feilet — tjenestene er STOPPET og"
    echo "forrige release står urørt på $FORRIGE. Fremoverrettet retting."
    exit 1
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
  echo "AVBRUTT: deploy-port (014c §5) rød — tjenestene er STOPPET og"
  echo "forrige release står urørt på $FORRIGE. Rett registeret/typen først."
  exit 1
}

# --- 7. Atomisk release-bytte + units --------------------------------------
ln -sfn "$KILDE" "$AKTIV"
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
# 039: selvbetjent domeneverifisering — hvert 5. minutt.
systemctl enable --now disponit-domeneverifisering.timer
# Varselsenderen: samme form, og den MÅ startes igjen her. Steg 5 stopper
# timeren i vedlikeholdsvinduet — uten denne linjen var utrullingen det som
# slo senderen av, permanent, og køen ville bare vokst. Timeren, ikke
# tjenesten: oneshot-en er timerens å starte.
systemctl enable --now disponit-varselsender.timer

KLAR=nei
for _ in $(seq 1 30); do
  if curl -fsS --unix-socket /run/disponit/api.sock \
       http://disponit/ready >/dev/null 2>&1; then KLAR=ja; break; fi
  sleep 1
done

API=$(systemctl is-active disponit-api.service || true)
M37=$(systemctl is-active disponit-m37.service || true)

# --- 9. TREDELT STATUSRAPPORT (v3 §3) — ærlig, aldri en lovet rollback -----
echo
echo "== statusrapport =="
echo "(a) schema, per base:"
vurder_migrasjoner runtime "$RAPPORT_KATALOG/runtime" \
                   test    "$RAPPORT_KATALOG/test"
echo "(b) kandidat:  api=$API m37=$M37 /ready=$KLAR (release $SHA)"
if [ -z "$NYE_MIGRASJONER" ]; then
  echo "(c) rollback:  ingen nye migrasjoner i NOEN base — forrige kode"
  echo "               ($FORRIGE) er fortsatt kompatibel med skjemaet."
else
  echo "(c) rollback:  FORBUDT. Nye migrasjoner [$NYE_MIGRASJONER] er"
  echo "               forward-only; forrige kode kan IKKE startes mot"
  echo "               dette skjemaet. Dommen felles over UNIONEN av"
  echo "               basene — én base er nok. Feil rettes FREMOVER."
fi
if [ -z "${DISPONIT_ARBEIDER_URL:-}" ]; then
  echo "AVVIK: DISPONIT_ARBEIDER_URL er ikke satt — arbeideren deler"
  echo "       runtime-DSN. Kjør oppsett-postgresql.sh for rolleskillet."
fi

[ "$API" = active ] && [ "$M37" = active ] && [ "$KLAR" = ja ]
