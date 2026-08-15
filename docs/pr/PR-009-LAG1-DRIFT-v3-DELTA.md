# PR-009 SPESIFIKASJON v3 — DELTA (heartbeat, ærlig deploy, tokenlevering)

**Draft: Claude.ai · v1+v2 står der de ikke motsies. Tre P1 + presiseringer.**

## 1. M-37-heartbeat: workeren har ikke HTTP

`/live` tilhører API-prosessen; timeren kunne ikke se en hengt worker.
Rettet — to ulike kontroller:

**API:** lokal `GET /live` (event loop svarer).

**M-37:** atomisk heartbeat-fil `/run/disponit-m37/heartbeat`:
- Skrives av workerens **hovedløkke** (etter hver claim-syklus, også når
  køen er tom) — ALDRI av en separat tråd som kan leve mens arbeidet henger.
- Atomisk: skriv til `.tmp` + `rename()`.
- Innhold: `{ts, syklus_nr, siste_status}` — ingen saksdata.
- Hengt = `now - ts > 3 × forventet syklustid` (default 90 s).
- **Manglende DB alene ⇒ IKKE hengt:** workeren skriver heartbeat med
  `siste_status: db_utilgjengelig` og fortsetter å løkke. Da restartes den
  ikke for noe en restart ikke løser (`/ready` viser DB-problemet i stedet).

## 2. Helsetellerens state (konkret, ikke «tre feil»)

- Teller per unit under `/run/disponit-health/<unit>.count`.
- `flock` rundt hele sjekk+teller-oppdateringen (ingen kappløp mellom
  timer-kjøringer).
- Suksess nullstiller telleren; restart nullstiller den.
- Restart utføres av en **privilegert helper med lukket allowlist** —
  kun `disponit-api.service` og `disponit-m37.service`. Kontrollprosessen
  får ALDRI generell `systemctl`-fullmakt (sudoers-regel begrenset til
  helperen, som selv validerer unitnavn mot allowlisten).

## 3. Ærlig deploykontrakt — ingen falsk rollback

v2 lovte «forrige versjon bevart»; etter committet migrasjon er ikke
forrige kode nødvendigvis kjørbar, og unitfil-rollback reverserer ikke DB.
Rettet:
- **Versjonert release-katalog** `/opt/disponit/releases/<sha>/`; aktiv
  release er et symlink som byttes ATOMISK. Unitfil og aktiv release peker
  alltid på samme versjon.
- **Migrasjoner må være bakoverkompatible med forrige applikasjonsversjon**
  hvis automatisk kode-rollback skal støttes. Er den ikke det, deklareres
  det i migrasjonen, og deployen stopper FAIL-CLOSED ved feil — krever
  fremoverrettet retting.
- **ALDRI automatisk nedmigrering.** Ingen `down`-migrasjoner i dette
  prosjektet.
- `opp.sh` rapporterer SEPARAT: (a) schema oppgradert ja/nei, (b) kandidat
  startet/feilet, (c) forrige kode kompatibel med nytt schema ja/nei.
  Operatøren ser hva som faktisk kan rulles tilbake, ikke en påstand.

## 4. Tokenlevering: PENDING → testet → vist → aktiv

v2s flyt kunne committe et aktivt token ingen kjenner hemmeligheten til
(test eller TTY-visning feiler etter commit, og idempotens hindrer nytt).
Rettet:
1. Tokenmetadata opprettes som `status='PENDING'` (kan ikke autentisere).
2. Test kjøres mot et lese-endepunkt med hemmeligheten i minnet.
3. **Visning krever faktisk TTY** (`isatty`) og skjer FØR kommandoen
   rapporterer suksess.
4. Først etter vellykket visning settes `status='AKTIV'`.
5. Feil i steg 2 eller 3 → pending-token slettes/tilbakekalles; ingen
   foreldreløs aktiv token.
6. Avbrudd ETTER aktivering men før bekreftet visning → kommandoen
   rapporterer TYDELIG rotasjonskrav (ikke stille suksess).
7. Agent- og brukertoken behandles SEPARAT; delvis suksess rapporteres
   eksplisitt («agent ok, bruker feilet — kjør roter-token for bruker»).

`api_tokener` får `status TEXT CHECK IN ('PENDING','AKTIV','TILBAKEKALT')`;
`verifiser_token` godtar KUN `AKTIV` (migrasjon i denne PR-en).

## 5. Presiseringer
- **`LoadCredential` velges for alle hemmeligheter** (ikke
  `EnvironmentFile` for de samme) — appen leser fra
  `$CREDENTIALS_DIRECTORY/<navn>`. Én mekanisme, dokumentert i DEPLOY.md.
  `EnvironmentFile` brukes kun for ikke-hemmelig konfigurasjon.
- Health-restart testes UTEN at kontrollprosessen har generell
  `systemctl`-fullmakt (negativ test: helper avviser unit utenfor allowlist).
- `Restart=always` + start-limit testes også ved **exit code 0** (en worker
  som avslutter rent skal fortsatt restartes, men ikke i uendelig løkke).
- **PR-009b er hard, maskinell staging-avhengighet for PR-010-e2e** —
  `opp.sh` for PR-010 nekter å kjøre uten verifisert HTTPS.

## Akseptansekriterier (tillegg)
Kunstig hengt worker (hovedløkke blokkert) → heartbeat foreldes →
restart etter 3 sykluser · DB nede → worker restartes IKKE (heartbeat
friskt m/ db_utilgjengelig) · helper avviser unit utenfor allowlist ·
PENDING-token kan ikke autentisere · avbrutt visning → ingen aktiv token
uten kjent hemmelighet · deploy rapporterer de tre separate statusene.
