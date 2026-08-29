# PR-009 SPESIFIKASJON v2 — DELTA (drift som faktisk kan overvåkes)

**Draft: Claude.ai · v1 står der det ikke motsies. Reviewens modeller
vedtatt. Ny leveranse skilt ut: PR-009b transport/TLS.**

## 1. Helse- og restartmodell (eksplisitt, ikke antatt)

`Type=simple` for begge units — IKKE `notify` (prosessen implementerer
ikke `sd_notify`/`WATCHDOG=1`, og å påstå det ville vært en port vi ikke
kan måle).
- `opp.sh` poller `/ready` under utrulling (utrullingsport).
- **`disponit-helse.timer`** (ny): kjører lokal helsesjekk hvert 60. sek.
  mot `/live`. Etter N=3 påfølgende feil → `systemctl restart` av riktig
  unit. Timeren er den eneste restart-på-hengning-mekanismen.
- **`/live`** tester KUN prosessens event loop (svarer den?). Ingen
  DB-avhengighet — en kort DB-feil skal ikke gi restartstorm.
- **`/ready`** tester avhengigheter (DB, migrasjonsversjon, nøkler,
  policyregister). Brukes av utrulling og manuell diagnose, ALDRI som
  restart-trigger.

## 2. Restartbegrensning (begge units)
```ini
Restart=on-failure        # api
Restart=always            # m37
RestartSec=5
StartLimitIntervalSec=300
StartLimitBurst=5
```
Permanent feilkonfigurasjon ender i tydelig `failed`, ikke uendelig støy.
Statusutdata må vise HVILKEN boot-sjekk som feilet (lukket kodeliste),
uten å logge hemmeligheter — boot-sjekkene skriver kodenavn, aldri verdier.

## 3. Tjenesteisolasjon: to identiteter, to rollesett, to hemmelighetssett

| | disponit-api | disponit-m37 |
|---|---|---|
| Unix-bruker | `disponit-api` | `disponit-m37` |
| DB-rolle | `disponit_runtime` (API-rettigheter) | egen arbeiderrolle, kun det arbeideren trenger |
| Hemmeligheter | `/etc/disponit/api.env` | `/etc/disponit/m37.env` |

- Begge filer root-eide, `0600`, lesbare kun via systemd
  `LoadCredential=`/`EnvironmentFile=` for sin egen unit.
- **Et kompromittert API får ikke arbeiderens fullmakter** — API-et har
  ikke arbeiderens DB-rolle og ikke dens credential-fil.
- Foretrekk `LoadCredential=` (systemd credentials) der praktisk; ellers
  separate, root-eide filer. Ingen delt `staging.env` lenger.

## 4. Deploylås og verifisert utrulling

`opp.sh`:
- `flock` på `/var/lock/disponit-deploy.lock`, **fail-fast** hvis en annen
  utrulling kjører (systemd serialiserer unit-jobs, ikke skriptet).
- Rekkefølge: verifiser unitfiler (`systemd-analyze verify`) → installer →
  **bootstrap+migrasjon FULLFØRT** → start units → poll `/ready`.
- Delvis feil → eksplisitt stopp med forrige versjon bevart (units ikke
  byttet før migrasjon er grønn), og tydelig diagnostikk. Ingen halvveis
  tilstand.

## 5. Atomisk, idempotent tenantinit

`init-tenant.sh` kjører hele sekvensen under **tenantbundet advisory lock**:
- DEK, policy og tokenmetadata i én kontrollert arbeidsflyt (ikke fire
  uavhengige `ON CONFLICT`).
- Eksisterende tenant VALIDERES (policy aktiv? DEK finnes? token gyldig?),
  overskrives ALDRI blindt.
- **Ny kjøring utsteder ikke nytt token automatisk.** Mistet hemmelighet →
  eksplisitt `roter-token.sh` som tilbakekaller gammel (rotasjonsrekkefølge
  fra PR-005b: ny opprettes og committes før gammel deaktiveres).
- Token TESTES mot et lese-endepunkt før det vises én gang på TTY —
  vises aldri hvis det ikke virker.

## 6. TLS får en eier: PR-009b (hard avhengighet før PR-010 på staging)

v1 utsatte TLS til sesjons-PR-en, mens PR-010 forutsetter `Secure`-cookie
— ingen eide kontrakten. Rettet: **egen leveranse PR-009b Transport**,
merget FØR PR-010 kan godkjennes på staging:
- nginx reverse proxy foran API-et (API forblir loopback internt).
- TLS-sertifikat + automatisk fornyelse; godkjente hostnavn (lukket liste).
- Proxy-header-allowlist (kun `X-Forwarded-For/Proto/Host` fra egen proxy;
  klientsendte varianter strippes).
- Requestgrenser og timeouts i proxy (i tillegg til app-lagets 256 KiB).
- **HSTS først etter verifisert HTTPS** — ikke i første deploy.
- `DISPONIT_TLS_AKTIV` settes her, av transporten som faktisk leverer den.

## 7. Driftsavklaringer
- `journald SystemMaxUse=500M` settes KUN med eksplisitt erklæring om at
  verten er dedikert til Disponit (den er det på staging — dokumenteres i
  DEPLOY.md). Ellers per-unit `LogRateLimit` i stedet for global endring.
- Backup-timer definerer: kryptering (GPG/age med nøkkel utenfor verten),
  retention (30 dager), `flock` mot samtidig kjøring, og
  **restore til ISOLERT database** som verifiseringssteg (ikke over prod).
- `After=postgresql.service` beholdes for lokal unit, men **boot-sjekken
  er den reelle readiness-porten** — dokumenteres slik, så ingen tror
  ordering alene garanterer noe.

## 8. Akseptansekriterier (revidert)
Som v1 §8, pluss: to Unix-brukere og to credential-filer verifisert ·
API-prosessen kan IKKE lese m37s hemmeligheter (negativ test) · to
samtidige `opp.sh` → én kjører, andre feiler rent · `init-tenant.sh` to
ganger → ingen nytt token, ingen duplikat · helsetimer restarter en
kunstig hengt prosess innen 3 sykluser · unit med feil konfigurasjon ender
`failed` etter 5 forsøk, ikke i løkke.
