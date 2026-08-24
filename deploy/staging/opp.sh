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
# 048 (#108): plan-arbeiderens DSN — samme kontrakt som varselsenderens.
if ! ( set -a; . "$MILJOFIL"; set +a; [ -n "${DISPONIT_PLAN_URL:-}" ] ); then
  echo "AVBRUTT: DISPONIT_PLAN_URL mangler i $MILJOFIL."
  echo "Kjør deploy/staging/oppsett-postgresql.sh først — den oppretter"
  echo "rollen disponit_plan_arbeider og skriver DSN-en til miljøfila."
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
for base in runtime test; do
  if ! CHECKSUMPREFLIGHT=$( set -a; . "$MILJOFIL"; set +a
      case $base in
        runtime) url=$DISPONIT_MIGRATOR_URL ;;
        test)    url=$DISPONIT_TEST_MIGRATOR_DSN ;;
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
# Hvilke NULL-rader herdingen FAKTISK kan fylle, lest fra herdingens egen
# kilde. Tallene {1, 2} står ALDRI hardkodet her: legger noen en versjon til
# eller fjerner en fra REVIEWEDE_CHECKSUMS, skal porten flytte seg med
# herdingen, ikke motsi den. (Samme importvei som migrer.last_bootstrap —
# filnavnet har bindestrek og kan ikke importeres vanlig.)
_spek = importlib.util.spec_from_file_location(
    "migrasjon_bootstrap", "deploy/staging/migrasjon-bootstrap.py")
_boot = importlib.util.module_from_spec(_spek)
_spek.loader.exec_module(_boot)
herdbare = set(_boot.REVIEWEDE_CHECKSUMS)
with psycopg.connect(os.environ["DISPONIT_MIGRATOR_URL"]) as c:
    try:
        rader = c.execute(
            "SELECT versjon, checksum FROM migrasjoner").fetchall()
    except psycopg.errors.UndefinedTable:
        print("fersk base — ingen historikk å verne"); sys.exit(0)
    except psycopg.errors.UndefinedColumn:
        c.rollback()
        # PR-004-æraens base: `migrasjoner` ble laget av 001_init.sql UTEN
        # checksum-kolonne. Da finnes det ingen registrerte checksums å
        # måle mot, og kjøreren er bygget for nettopp denne oppgraderingen:
        # kjorer.py legger til kolonnen (ADD COLUMN IF NOT EXISTS) og
        # migrasjon-bootstrap.herd_historikk backfiller de REVIEWEDE
        # checksummene før 003. Et avbrudd her ville stoppet den eneste
        # veien ut av tilstanden porten klager på.
        #
        # Men bare så langt herdingen rekker: kolonnen legges til NULL for
        # HVER registrert versjon, og `herd_historikk` fyller kun de
        # reviewede. Er det registrert en versjon herdingen ikke kjenner,
        # er dette ikke en oppgradering migrer.py kan fullføre — samme dom
        # som NULL-grenen under, og den hører her av samme grunn.
        ukjente = sorted(v for (v,) in c.execute(
            "SELECT versjon FROM migrasjoner").fetchall()
            if v not in herdbare)
        if ukjente:
            print("ingen checksum-kolonne, og registrerte versjoner ("
                  + ", ".join(f"{v:03d}" for v in ukjente)
                  + ") som herdingen ikke kan fylle — migrer.py ville"
                  " feilet i vedlikeholdsvinduet")
            sys.exit(1)
        print("historikken er ikke herdet ennå (ingen checksum-kolonne)"
              " — migrer.py legger til kolonnen og backfiller")
        sys.exit(0)
avvik = []
uherdet = []
for versjon, checksum in rader:
    fil = filer.get(versjon)
    if fil is None:
        avvik.append(f"{versjon:03d}: kjørt i basen, borte fra treet")
    elif checksum is None and versjon in herdbare:
        # Kjørt, men ikke herdet — og herdingen KAN fylle nettopp denne:
        # `herd_historikk` backfiller den fra REVIEWEDE_CHECKSUMS før 003.
        # Da er raden ikke et avvik, men den telles og RAPPORTERES i stedet
        # for å hoppes over stille, så en base som står halvveis i herdingen
        # er synlig i deploy-loggen.
        uherdet.append(versjon)
    elif checksum is None:
        # Codex P2 (runde 2): og HER tok runde 1 feil. Begrunnelsen den gang
        # målte `kjorer.py`, som bare sammenligner når raden HAR en checksum
        # — sant, men det er ikke kjøreren som feller denne tilstanden.
        # `migrer.py` kaller `herd_historikk` UBETINGET, og den backfiller
        # kun REVIEWEDE_CHECKSUMS (001/002) før den kaster `HerdingFeilet`
        # på enhver NULL som står igjen. En kjørt versjon uten checksum som
        # herdingen ikke kjenner, er altså ikke en tilstand migrer.py kan
        # løse: den feller steg 6 GARANTERT — etter at tjenestene er
        # stoppet. Det er nøyaktig 056-klassen denne porten finnes for, og
        # den hører derfor blant avvikene, målt før første mutasjon.
        avvik.append(
            f"{versjon:03d}: kjørt uten checksum, og herdingen kan ikke fylle"
            f" den (reviewede: "
            + ", ".join(f"{v:03d}" for v in sorted(herdbare))
            + ") — migrer.py ville feilet i vedlikeholdsvinduet")
    elif hashlib.sha256(fil.read_bytes()).hexdigest() != checksum:
        avvik.append(f"{fil.name}: endret etter kjøring (checksum-avvik)")
if avvik:
    print("\n".join(avvik)); sys.exit(1)
melding = f"{len(rader) - len(uherdet)} kjørte migrasjoner byte-identiske"
if uherdet:
    melding += (f"; {len(uherdet)} uten checksum ("
                + ", ".join(f"{v:03d}" for v in sorted(uherdet))
                + ") — migrer.py herder dem")
print(melding)
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
disponit-domeneverifisering.timer disponit-wcag-audit.service"

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
  ROLLBACKDOM=""
  if [ -z "$FORRIGE" ]; then
    ROLLBACKDOM="ingen forrige release å boote (aktiv-symlinken fantes ikke)"
  else
    ROLLBACKDOM=$(rollbackmaal_kompatibelt "$FORRIGE" \
        "${DISPONIT_MIGRATOR_URL:-}") || \
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
    echo "Rettingen er FREMOVER og den er NÅ: fullfør deployen mot et"
    echo "skjema begge basene deler, eller rull frem til et sett forrige"
    echo "release bærer. Deployen er avbrutt."
    exit 1
  fi
  # Codex P1 (runde 4): CREDENTIALENE TILBAKE FØR GAMMEL KODE BOOTER PÅ DEM.
  # De tre forrige rundene på denne funksjonen målte hvilke ENHETER som
  # startes; dette er den tilstanden de startes MOT. Steg 4 skrev
  # kandidatens verdier over forrige releases, `LoadCredential` leser fila
  # på nytt ved hver aktivering, og `DISPONIT_SEMANTIKK_MILJO` er regnet ut
  # med kandidatens kode mens forrige releases boot-port måler den mot sin
  # egen. Uten denne tilbakestillingen er «forrige release kjører igjen» et
  # utsagn om binæren, ikke om konfigurasjonen den kjører på.
  #
  # Tilbakestillingen SKRIVER OVER, den rydder ikke: en credential
  # kandidaten la til og forrige release ikke kjenner, blir liggende. Det
  # er med vilje — forrige releases units laster bare de filene deres egen
  # `LoadCredential` navngir, så en ekstra fil er inert, mens et `rm -rf`
  # her ville lagt et destruktivt steg inn i selve feilhåndteringen.
  GJENOPPRETTET=""
  for kat in "$CRED_FORVINDU"/*/; do
    [ -d "$kat" ] || continue
    cp -a "$kat" /etc/disponit/
    GJENOPPRETTET="$GJENOPPRETTET $(basename "$kat")"
  done
  echo "credentials tilbakestilt til før vinduet:" \
       "${GJENOPPRETTET:- ingen — /etc/disponit var tomt før steg 4}"
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
  NEDE=""
  for enhet in $AKTIVE_FOR_VINDUET; do
    systemctl is-active --quiet "$enhet" || NEDE="$NEDE $enhet"
  done
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
systemctl stop disponit-helse.timer disponit-m37.service \
    disponit-api.service disponit-api.socket 2>/dev/null || true
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
