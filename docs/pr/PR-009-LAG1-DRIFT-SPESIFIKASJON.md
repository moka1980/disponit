# PR-009 SPESIFIKASJON — Lag 1: staging-drift (til ChatGPT-porten)

**Draft: Claude.ai · Første gang Disponit KJØRER som tjeneste, ikke bare
som testsuite. Rent driftsoppsett — ingen ny forretningslogikk, ingen
migrasjon. Bygger på eksisterende `deploy/staging/`-skript, boot-sjekkene
i app.py og `disponit-m37.service` som ligger i repoet men aldri er
installert.**

## 0. Hva dette leverer

Én kommando (`deploy/staging/opp.sh`) som tar staging fra «ingenting
kjører» til «API og M-37-arbeider kjører, overvåkes og restartes».
Ikke produksjon: loopback + brannmur, syntetiske tenanter.

## 1. To systemd-units

**`disponit-api.service`** (finnes IKKE i repoet — leveres her):
- `ExecStart`: uvicorn mot `platform/core/api/app.py`, bundet til
  `127.0.0.1:8099` (loopback — TLS-porten fra 005b krever det).
- `EnvironmentFile=/etc/disponit/staging.env` (0600, eksisterende fil:
  DATABASE_URL, DISPONIT_ATT_NOKLER, DISPONIT_KEK, token-pepper).
- `User=disponit` (ikke root), `Restart=on-failure`, `RestartSec=5`.
- Herding: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`,
  `ProtectHome`, `ReadWritePaths=` kun det som trengs.
- `Type=notify` hvis mulig, ellers `Type=simple` + readiness-polling i
  opp.sh (se §3).

**`disponit-m37.service`** (finnes i repoet, aldri installert):
- Samme herding og miljøfil. Egen prosess — aldri i API-prosessen
  (PR-006-invarianten, nå håndhevet av at det er to units).
- `Restart=always` (arbeideren skal alltid tilbake; claim-leasen tåler
  krasj og re-claim per PR-006).

**Rekkefølge:** m37 `After=disponit-api.service` er IKKE nødvendig
(arbeideren går DB-direkte), men `After=postgresql.service` er det for
begge. Ingen `Requires` mellom API og arbeider — de skal kunne restartes
uavhengig.

## 2. Boot-sjekkene er porten (eksisterende, nå operasjonalisert)

Begge prosesser NEKTER oppstart hvis: nøkkelregister ugyldig, KEK mangler,
DB unådd, migrasjonsversjon ≠ forventet, eller bind-adresse ≠ loopback
uten TLS-flagg. systemd `Restart=on-failure` vil da forsøke igjen og
til slutt gi opp — **ønsket oppførsel: en feilkonfigurert tjeneste skal
IKKE kjøre halvveis.** `opp.sh` sjekker `systemctl is-active` og feiler
hardt hvis en unit ikke er `active` innen 30 s.

## 3. `/live` og `/ready` operasjonalisert

- `/live` → systemd-watchdog/restart-beslutning (prosessen lever).
- `/ready` → `opp.sh` poller denne før den erklærer oppsettet vellykket;
  den er loopback-only (PR-005b), så ingen ekstern eksponering.
- Readiness krever: DB, migrasjonsversjon match, nøkkelregister,
  policyregister nåbart.

## 4. Tenant og token (engangs, idempotent)

`deploy/staging/init-tenant.sh <tenant-id>`:
1. Opprett tenant-DEK via eksisterende kryptolag (idempotent — `ON CONFLICT`).
2. Last en bransjemal-policy inn i `policyer` som `aktiv` for tenanten
   (validert mot skjema v0.2 ved innsetting — eksisterende vei).
3. Utsted tokens via `token-cli.py`: ett `agent`-token (decision:write)
   og ett `bruker`-token (lese-scopes, PR-008) — vises ÉN gang på TTY,
   aldri lagret.
Kjøres for `demo-a` og `demo-b` (to tenanter — kryss-tenant-testene i
PR-008 trenger begge).

## 5. Logg, rotasjon, backup

- Logg til journald (strukturert, JSON-linjer). **Ingen payload, tokens,
  nøkler eller dekryptert innhold** — canary-test i CI dekker dette alt.
- `journald` rotasjon: `SystemMaxUse=500M`.
- Backup: eksisterende `backup.sh` legges i systemd-timer (daglig), med
  gjenopprettingsverifisering som allerede er rutine.

## 6. Nginx/TLS — deklarert, IKKE i denne PR-en

Så lenge API-et er loopback-only trengs ingen nginx-blokk. Ekstern
tilgang (subdomene + TLS) er EGEN PR sammen med sesjons-PR-en, fordi de
to henger sammen: en browser kan først snakke med API-et når både TLS og
sesjon finnes. `DISPONIT_TLS_AKTIV` forblir usatt her.

## 7. Fire samtidighetsspørsmål besvart

| Kontroll | Alle veier inn? | Under samtidighet? | Riktig vs velformet? | Lukket format? |
|---|---|---|---|---|
| Boot-sjekker | Begge units, samme kodevei | N/A (oppstart) | Sjekker faktisk DB/migrasjon, ikke bare at env-var finnes | Migrasjonsversjon er eksakt match, ikke ≥ |
| opp.sh idempotens | Eneste oppsettvei | To samtidige kjøringer: systemd serialiserer; init-tenant er `ON CONFLICT` | Poller `/ready`, ikke bare `is-active` | Feiler hardt ved ukjent tilstand |
| Token-utstedelse | Kun token-cli | Unik `token_id` (CSPRNG) | Verifiserer at token faktisk virker mot `/v1/...` etterpå | Scopes fra lukket liste |
| Arbeider-restart | systemd | Lease/fencing fra PR-006 håndterer krasj midt i claim | Re-claim krever gyldig generation | Statusmaskin uendret |

## 8. Akseptansekriterier (målbart)

Etter `opp.sh` på fersk staging: begge units `active` · `/ready` 200 ·
to tenanter med policy og tokens · en beslutning sendt med `agent`-token
gir loggpost · `bruker`-token når lese-endepunktene og NEKTES på
`POST /v1/beslutning` · arbeider plukker en injisert sak · `systemctl
restart` på begge → fortsatt friskt, null tapte loggposter · ingen
hemmelighet i journald (grep-test).

## Spørsmål til ChatGPT
1. Bør `disponit-api` være `Type=notify` (krever sd_notify i app.py) eller
   holder `Type=simple` + readiness-polling i opp.sh?
2. Arbeideren har `Restart=always` — bør den ha `StartLimitBurst` slik at
   en permanent feilkonfigurasjon ikke gir uendelig restartløkke, eller er
   fail-closed-oppstart nok?
3. Er det riktig å utsette nginx/TLS til sesjons-PR-en, eller bør TLS
   settes opp nå slik at sesjonsarbeidet kan testes mot ekte transport?
