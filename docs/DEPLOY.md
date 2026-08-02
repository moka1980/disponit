# DEPLOY — miljøer, servere og skaleringsvei

## Server: one.com Cloud Server (VPS) — Eiers eksisterende oppsett

Vi har allerede en **one.com Cloud Server S** med Ubuntu: 2 vCPU, 4 GB RAM,
100 GB NVMe, full root-tilgang, Docker-støtte, ubegrenset trafikk og
databehandleravtale tilgjengelig (GDPR). Månedlig avtale — oppgradering
til større plan tar minutter og krever ingen omskriving.

**Beslutning (Claude.ai):** Cloud Server S er staging-serveren vår nå.
Skillet mellom miljøer beholdes uansett leverandør: staging og produksjon
deler aldri database, nøkler eller nettverk.

### Hva Cloud Server S (4 GB) dekker — og ikke

| Kjører fint på S | Krever oppgradering |
|---|---|
| FastAPI-backend + jobbkø | Ollama med Mistral 7B / Llama 3.1 → **Cloud Server L (16 GB)** |
| PostgreSQL + objektlagring lokalt | Flere samtidige LLM-forespørsler → XL/XXL |
| Policymotor, register, revisjonslogg (hele fase 1-kjernen er deterministisk) | GPU-inferens (fase 3-skala) → ekstern GPU-leverandør; M-38 holder inferensplanet adskilt så byttet er enkelt |
| Små kvantiserte modeller (~3B) for røyk-testing | |

Oppgraderingsutløser: **første modul som trenger 7B-modell i staging-test
→ bestill Cloud Server L.** Ikke før.

## Miljøer

| Miljø | Hvor | Formål | Data |
|---|---|---|---|
| **Lokalt** | Utviklers maskin / Claude Code | Kode + enhetstester | Syntetisk |
| **Staging** | one.com Cloud Server S (Ubuntu) — oppgraderes S → L → XL etter behov | «Ekte test på server»: hver modul må bestå sjekklisten sin her 100 % før neste modul startes | Syntetisk + sandkasser (Stripe test-mode, bank-sandbox). ALDRI kundedata |
| **Produksjon** | Egen VPS (settes opp når fase 1 nærmer seg pilot — kan også være one.com, men alltid separat maskin) | Kunder + kunde null | Ekte, kryptert, tenant-isolert |

Produksjon oppdateres kun via utrullingsløypen i v7.2 (CI → staging →
evaluering → kanari → gradvis → automatisk rollback). Ingen SSH-endringer
rett i produksjon — aldri.

## Skaleringsvei (bygget inn, aktivert etter behov)

Prinsippet som gjør skalering til et maskinvalg, ikke en omskriving:
**ingen tilstand i API-prosessen** — all tilstand bor i PostgreSQL /
objektlagring / kø. Håndheves fra PR-004 (API-skjelettet).

| Nivå | Samtidige brukere | Hva som må til |
|---|---|---|
| 1 | 0–1 000 | Cloud Server S/L: API + DB + kø på én maskin. CDN foran statiske filer. |
| 2 | 1 000–10 000 | Oppgrader til L/XL, eller skill API og PostgreSQL på to VPS-er. Jobbkø-workers. Backup til objektlagring. |
| 3 | 10 000–100 000 | Load balancer + flere stateless API-noder, PostgreSQL-replika, dedikerte GPU-inferensnoder eksternt (M-38 styrer kø/ruting), regional datalagring. |
| 4 | 100 000–1 000 000 | Multi-region, sharding per tenant-gruppe, autoskalering av workers, katastrofegjenoppretting (M-35). |

## Staging-databasen — faktisk oppsett (PR-004)

Satt opp av `deploy/staging/oppsett-postgresql.sh` (idempotent) og verifisert
på Cloud Server S 2026-08-02.

| | |
|---|---|
| Versjon | PostgreSQL **18.4** (skriptet installerer distroens `postgresql`) |
| Lytter på | `127.0.0.1:5432` **kun loopback** — ingen 5432 utad |
| Tuning | `shared_buffers=192MB`, `max_connections=40`, `work_mem=4MB`, `effective_cache_size=512MB` |
| Roller | **Tre, med vilje.** `disponit_migrator` eier skjemaet og kjører migrasjonene. `disponit` er runtime — eier ingenting, kan verken slette eller deaktivere append-only-triggerne eller RLS-policyene. `disponit_authenticator` (NOLOGIN, PR-005) eier `api_tokener`; runtime når den aldri direkte, kun via `verifiser_token` som er SECURITY DEFINER med låst `search_path`. Superbrukeren `postgres` brukes bare til å opprette roller. |
| `DISPONIT_KEK` | KEK for envelope-kryptering av unntaks-payload (PR-005). Ligger i miljøfila. **Roteres aldri av oppsettskriptet:** mistes den, er alle krypterte payloads uleselige for alltid, og rotasjon krever rewrapping av samtlige DEK-er først. |
| Tenant-isolasjon | Row level security med `FORCE` på begge tabeller. Policyen sammenligner radens tenant med sesjonsvariabelen `disponit.tenant`, som `db.pg.sett_tenant()` setter per transaksjon. Er den ikke satt: null rader synlige, ingen rader skrivbare. |
| Databaser | `disponit` (staging) og `disponit_test` (testkjøringer) |
| Hemmeligheter | `/etc/disponit/staging.env`, `chmod 600`, katalog `chmod 700`. DSN-er + attestasjonsnøkler. **Aldri i repoet, aldri i chat.** |
| Repo på serveren | `/opt/disponit`, med venv i `/opt/disponit/.venv` |
| Sikret originalkonfig | `/etc/postgresql/18/main/postgresql.conf.bak.20260801` |

**Kjør staging-porten:**

```bash
cd /opt/disponit && git pull
sudo bash deploy/staging/oppsett-postgresql.sh
sudo bash -c 'set -a; . /etc/disponit/staging.env; set +a; \
  cd /opt/disponit && ./.venv/bin/python -m pytest platform/core/tests -q'
```

> 🔑 **Gjenopprett aldri en gammel `staging.env`.** Fila er kilden til
> sannhet for hemmelighetene, og skriptet roterer et passord i det en nøkkel
> mangler. Legger du tilbake en eldre kopi, peker DSN-en på et passord rollen
> ikke lenger har, og alt feiler med `password authentication failed`.
> Skal en hemmelighet fornyes: **slett én av rollens DSN-linjer** og kjør
> skriptet. Da roteres rollens passord og **alle** dens DSN-er skrives på
> nytt samlet — søskenlinja blir aldri stående igjen med det gamle passordet.
> Mangler ingen nøkler, roteres ingenting: fila er da bit for bit uendret.
>
> Rollenes DSN-par: `DATABASE_URL` + `DISPONIT_TEST_DSN` (runtime) og
> `DISPONIT_MIGRATOR_URL` + `DISPONIT_TEST_MIGRATOR_DSN` (migrator).

> ✅ **Tilstandsmaskinen for hemmelighetene er nå testet i CI.** Den ligger i
> `deploy/staging/lib-miljofil.sh` og dekkes av
> `platform/core/tests/test_deploy_miljofil.py` (11 tester): ny installasjon,
> oppgradering, rotasjon per DSN, ingen rotasjon uten grunn, avbrutt
> rotasjon, midlertidig fil i målkatalogen, ingen dupliserte nøkler.
> Codex' krav etter at fem feil på rad ble funnet her: manuell staging-prøve
> er ikke en port.

Sju feil ble funnet nettopp fordi skriptet ble kjørt på ekte server eller
testet — alle er rettet:

1. **Miljøfila var ugyldig shell.** DSN-ene inneholder mellomrom, og uten
   anførselstegn tolker `set -a; . fila` bare første ord som verdi.
   `DISPONIT_TEST_DSN` ble `host=127.0.0.1`, passordet forsvant, og psycopg
   feilet med «no password supplied».
2. **Migrasjonene kjørte som `postgres`.** Da eies tabellene av
   superbrukeren, og applikasjonen kan ikke migrere sitt eget skjema:
   «must be owner of table revisjonslogg».
3. **Miljøfila ble bare skrevet når den ikke fantes.** En oppgradering fikk
   dermed aldri migrator-nøklene, og eneste vei videre var å slette fila og
   rotere alt for hånd. Fila skrives nå per nøkkel.
4. **Rotasjon brakk søskenlinja.** Manglet én av en rolles to DSN-er, ble
   passordet rotert mens bare den manglende linja ble skrevet — den andre
   sto igjen med gammelt passord. Prosedyren over ledet altså rett i fella.
   Alle rollens DSN-er skrives nå samlet.
5. **Avbrudd mellom passordrotasjon og filskriving kunne ikke oppdages**,
   fordi maskinen bare så etter nøkkelnavn. Skriptet prøver nå å koble til
   med hver DSN og reparerer rollen når den ikke virker.
6. **`mktemp` i `/tmp` etterfulgt av `mv` til `/etc`** krysser
   filsystemgrenser, og da er `mv` ikke atomisk. Temp-fila lages nå ved
   siden av målet.
7. **Eierskapsreparasjonen tok eierskap over `pgcrypto`-funksjoner.** Den
   skrev `ALTER FUNCTION public.navn()` uten argumenttyper, som traff
   funksjoner uten parametre — `fips_mode()` og `gen_random_uuid()` ble
   faktisk flyttet til migrator-rollen på denne serveren. Skriptet bruker nå
   full signatur, hopper over alt som tilhører en extension, og gir tilbake
   det den gamle versjonen tok.

> ⚠️ **Cloud Server S er ikke en dedikert maskin.** Den kjører også et annet
> produkt (WCAGvakt) med egen produksjonstjeneste, teststed og tre bots.
> Eier har godkjent samlokaliseringen **midlertidig**, fordi det andre
> produktet ennå ikke har kunder. Derfor er PostgreSQL tunet konservativt.
> Den dagen naboproduktet tar imot sin første kunde, bryter dette
> miljøprinsippet lenger oppe i dokumentet, og disponit-staging må flytte
> til egen maskin.

## Modulens staging-sjekkliste (mal — kopieres inn i hvert manifest)

- [ ] Alle enhetstester og negative policytester grønne på staging
- [ ] Kjørt mot syntetisk datasett med målt resultat likt lokalt
- [ ] Sandkasse-integrasjoner svarer (der modulen har noen)
- [ ] Revisjonslogg (M-2-format) skrives korrekt og er lesbar
- [ ] Feilinjisering: minst én fremprovosert feil havner riktig i unntakskø
- [ ] Ytelse: definert last kjørt uten feil (grense settes per modul)
- [ ] Rollback testet: modulen kan deaktiveres via registeret uten at annet påvirkes
