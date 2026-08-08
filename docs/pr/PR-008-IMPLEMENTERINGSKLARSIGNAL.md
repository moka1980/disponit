# PR-008 — IMPLEMENTERINGSKLARSIGNAL (GO, lese-API for M-1)

**Til Claude Code · Implementér mot v1–v6 fra gjeldende main. Branch:
`pr-008-lese-api`. GO + fem vilkår i PR-beskrivelsen. Sesjonsutstedelse
er EGEN senere PR — browserintegrasjon blokkert til den er godkjent.**

## De fem implementeringsvilkårene (bindende merge-krav)

### V1. Alle oppdragsopprettelser i samme PR
HVER eksisterende skrivevei som oppretter `oppdrag` (PR-006 claim/
kvittering, PR-007 fase 2) oppdateres til å levere korrekt
`beslutning_loggpost_id` i SAMME PR. Ingen overgangsperiode der default
`KOBLET` møter manglende FK. Beslutningsloggpost + oppdrag i samme
transaksjon der protokollen krever det.

### V2. Backfill fail-hard ved tvetydighet
INGEN `MIN`/`MAX`/`LIMIT 1`/«første match». Nøyaktig én gyldig kandidat →
`KOBLET`; null/flere → `LEGACY_UKJENT`. Skulle to oppdrag likevel koble til
samme beslutning → UNIQUE-opprettelsen STOPPER migrasjonen med diagnostikk;
migrasjonen velger ALDRI ett automatisk.

### V3. Triggerfunksjonene herdet
Schema-kvalifiser alle tabeller/funksjoner · lås `search_path` ·
trigger-eier ≠ runtime-rollen · runtime kan ikke deaktivere triggere eller
endre triggerfunksjoner · INGEN `session_replication_role`- eller
custom-setting-bypass. (Samme herding som `verifiser_token` fra PR-005b.)

### V4. Legacy-fravær vises ærlig
En beslutning uten entydig koblet oppdrag får ALDRI et konstruert
utførelsesresultat. «Utførelsesdata ikke tilgjengelig»-variant returneres
KUN når beslutningsraden SELV beviser at utførelse var relevant (outbox-
handling); ellers ingen antatt oppdragsstatus.

### V5. DTO-validering mekanisk fullført
`innholds_hash` = 64 hex-tegn · IANA-tidssone + ISO-4217 validert mot
registre · alle arraygrenser og unikhetsnøkler testet ved grense−1/grense/
grense+1 · decimal alltid kanonisk streng med to desimaler.

## De ni Codex-mergeportene (hver MÅ ha en test som dreper sin vakt)

1. Migrasjon 008 kjører på DB med entydige, tvetydige OG ukjente legacy-rader
2. Feil midt i migrasjonen → ingen kolonner/constraints/triggere igjen (full rollback)
3. Alle oppdragsopprettende kodeveier leverer beslutnings-FK
4. Runtime kan verken opprette `LEGACY_UKJENT` eller endre koblingen senere
5. To oppdrag for samme beslutning avvises (partiell UNIQUE)
6. Identisk replay oppretter ingen evidensflagg
7. Sen konflikt → både sen- og konfliktflagg, oppdragsresultat uendret
8. Alle seks nye leseendepunkter har tenant-, scope-, cursor- og feiltester
9. Browserintegrasjon blokkert til sesjons-PR godkjent (ingen ekte token i browser)

## Implementeringsomfang (v1–v6 samlet)

- **Migrasjon 008** (kjøreren eier tx, reviewet checksum): den TRINNVISE
  rekkefølgen fra v6 §1 (kolonner nullable → backfill via
  repair_operation_id/idempotens → verifiser ingen NULL → FK → CHECK
  NOT VALID+VALIDATE → NOT NULL+default → partiell UNIQUE → triggere sist);
  `koblingsstatus`-vakt (rekkefølge, ikke flagg); uforanderlighet FK+status.
- **`platform/core/api/`:** seks lese-endepunkter (`/v1/oversikt`,
  `/v1/beslutninger`, `/v1/beslutninger/{id}`, `/v1/unntak/{id}`,
  `/v1/unntak/{id}/historikk`, `/v1/policy/aktiv`) + eksisterende
  `GET /v1/unntak`. Tre-akse-respons (resultat/evidensstatus/sikkerhet),
  avledet evidensflagg-matrise (v5 §2), ærlig keyset uten snapshotløfte
  (v4 §3), lukkede policy-DTO-er med v6-grenser. `bruker`-rolle med lese-
  scopes, default-deny mot muterende ruter.
- **Oppdatering av oppdrag-skriveveier** (V1): PR-006/007-funksjonene
  leverer `beslutning_loggpost_id`.
- Feilmodell gjenbruker eksisterende feilveitabell; kryss-tenant → 404.

## Sannhetssjekk før implementering (Claude Code bekrefter mot main)
- At `repair_operation_id` finnes på BÅDE oppdrag og fase-2-loggpost slik
  backfillen krever (v5 §1). Hvis ikke entydig tilgjengelig → flagg før
  migrasjon, ikke gjett.
- At `beslutning(loggpost) ← unntak ← oppdrag` + ny direkte-FK dekker alle
  faktiske oppdragskilder.

## Etter merge → staging
Bootstrap → migrasjon 008 → full suite → de seks endepunktene mot
staging med to tenanter (kryss-tenant-404, scope-nekt, cursor). Dette er
backend-fundamentet UI-komponentene bygges mot — men frontend kobles
først når sesjons-PR-en OG lag-1-drift står.

## Invarianter urørt
`sett_kontekst` først på alle nye ruter · RLS+FORCE · to-transaksjonsmodell
· kjøreren eier migrasjonstransaksjonen · én skrivevei til revisjonsloggen
· ingen ny forretningslogikk (rent lese-API + FK-migrasjon).
