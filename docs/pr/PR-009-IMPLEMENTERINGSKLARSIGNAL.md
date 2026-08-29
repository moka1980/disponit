# PR-009 — IMPLEMENTERINGSKLARSIGNAL (GO, lag-1 staging-drift)

**Til Claude Code · Implementér mot v1–v5 fra gjeldende main. Branch:
`pr-009-lag1-drift`. GO + tre vilkår i PR-beskrivelsen. PR-009b transport
(nginx/TLS) er EGEN leveranse etterpå; PR-010 sesjon venter på begge.**

## De tre implementeringsvilkårene (bindende merge-krav)

### V1. Forward-only migrasjon med vedlikeholdsvindu
Å droppe `aktiv` er ikke bakoverkompatibelt. Sekvens:
stopp API + M-37 → kjør statusmigrasjonen → aktiver ny release → start
tjenestene → readiness- og tokentest. **Automatisk rollback til gammel
kode er FORBUDT etter at `aktiv` er droppet** — `opp.sh` rapporterer det
eksplisitt (v3 §3s tredelte statusrapport). Forward-only er valgt bevisst
framfor expand/contract med generert kompatibilitetskolonne.

### V2. PENDING-verifikasjon i CLI-laget — pepper aldri i DB
- CLI leser pepper via systemd credential.
- CLI beregner MAC lokalt, sammenligner konstant-tid.
- Avgrenset DB-funksjon kan returnere tokenmetadata/MAC til
  token-admin-rollen, men **aldri pepper, og pepper er aldri
  funksjonsargument**.
- API-verifikatoren godtar fortsatt kun `AKTIV`.

### V3. Pending-opprydding uavhengig av signalhåndtering
Avbrudd før aktivering etterlater maks ett inaktivt PENDING-token. Timer
rydder PENDING-rader eldre enn TTL (30 min). Signalhåndtering FORSØKER
tilbakekalling, men korrektheten avhenger ALDRI av at cleanup-handleren
rekker å kjøre.

## De syv Codex-portene
1. Ingen tokenkode leser/skriver gammel `aktiv` (grep-port)
2. Gammel release startes ikke mot schema uten `aktiv`
3. Eksisterende aktive/tilbakekalte tokens beholder riktig status
4. PENDING kan ikke autentisere
5. Pepper finnes ikke i DB eller i DB-funksjonsargumenter
6. Automatisert deploy krever ikke TTY
7. Interaktiv bootstrap nekter uten TTY

## Implementeringsomfang (v1–v5 samlet)
- **Units:** `disponit-api.service` (ny) + `disponit-m37.service`
  (installeres), to Unix-brukere, to DB-roller, `LoadCredential` for alle
  hemmeligheter, `Restart`/`StartLimit` per v2 §2, herding (NoNewPrivileges,
  ProtectSystem=strict m.m.).
- **Helse:** `/live` (event loop, ingen DB) + M-37 heartbeat-fil skrevet av
  hovedløkken (atomisk rename); `disponit-helse.timer` med flock-teller
  under `/run/disponit-health/`, restart via privilegert helper med lukket
  unit-allowlist. Teller nullstilles kun ved ny PID + bestått sjekk.
  DB nede ⇒ ikke hengt worker.
- **Deploy:** `opp.sh` med flock, `systemd-analyze verify`, versjonert
  release-katalog + atomisk symlink, migrasjon før start, poll `/ready`,
  tredelt statusrapport. Ingen nedmigrering.
- **Tenant/token:** `init-tenant.sh` under tenantbundet advisory lock
  (DEK + policy + tokenmetadata i én arbeidsflyt, validerer eksisterende,
  utsteder ikke nytt token automatisk); `bootstrap-token.sh` interaktiv
  (TTY → PENDING → lokal verifisering → visning → operatørbekreftelse →
  aktivering → API-test); `roter-token.sh`.
- **Migrasjon:** `status`-kolonne trinnvis (nullable → backfill fra
  `aktiv` → CHECK+NOT NULL → default PENDING → verifikator strammes →
  DROP `aktiv`).
- **Backup:** timer med kryptering, 30 d retention, flock, restore til
  ISOLERT database som verifiseringssteg.
- **journald:** `SystemMaxUse=500M` med eksplisitt erklæring i DEPLOY.md om
  at verten er dedikert.

## Etter merge → staging
`opp.sh` på fersk staging → begge units `active` → `/ready` 200 → to
tenanter → `bootstrap-token.sh` for agent- og brukertoken → beslutning
sendt gir loggpost → arbeider plukker injisert sak → restart-test →
grep-test for hemmeligheter i journald.

**Dette er første gang Disponit kjører som tjeneste og ikke bare som
testsuite.**
