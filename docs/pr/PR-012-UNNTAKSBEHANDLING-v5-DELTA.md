# PR-012 SPESIFIKASJON v5 — DELTA (tre integritetskontrakter → GO)

**Draft: Claude.ai · v1–v4 står. Tre smale punkter.**

## 1. Undertrykking av ny kø-sak er SERVER-UTLEDET, aldri et parameter

`opphav_unntak_id` som kallerfelt ville vært en kø-omgåelse: hvem som
helst kunne merket en ordinær beslutning som «menneskelig godkjenning» og
hindret at unntaket ble opprettet. Rettet:

- **Feltet finnes IKKE i noe API-, arbeider- eller kjerne-kall.** Verken
  klient, M-37-arbeider eller det ordinære beslutnings-API-et kan sette
  det.
- Undertrykkingen aktiveres KUN internt i
  `behandle_unntakshandling(...)`, og kun når motoren i SAMME transaksjon
  har verifisert ALLE fem:
  1. original sak låst (`FOR UPDATE`),
  2. aktiv godkjenningsrunde (`klar`, riktig runde),
  3. gyldige, UBRUKTE attestasjoner (MAC + utløp for begge, v4 §4),
  4. samme `hi_integritet_hash` og `policy_hash` som runden frøs,
  5. reservert `decision_operation_id`.
- Mangler ett vilkår → ordinær unntaksopprettelse, som ellers.
- Codex-port: statisk sjekk på at ingen offentlig signatur eksponerer
  undertrykkingsflagget; kun den låste kodeveien kan sette det.

## 2. Handlingsintensjonen er PERMANENT frosset i v1

v4s «legitim ny intensjonsgenerasjon» motsa v2s uforanderlighet og hadde
ingen generasjonstabell. Fjernet fra v1:
- **Intensjonen er permanent frosset** på unntaksraden (kolonnelåst).
- **Kun ny `policy_hash` kan åpne en ny runde** etter terminalt utfall
  (v4 §2, nå eneste vei).
- Ny intensjonsgenerasjon er en **egen fremtidig protokoll** — ville
  krevd egen rad, lineage og retention. Deklarert, ikke i v1.

## 3. `godkjenningsutfall` med semantisk DB-integritet

FK-er alene beviser ikke at loggposten hører til `decision_operation_id`
og saken. Rettet:
```sql
CREATE TABLE godkjenningsutfall (
  tenant TEXT NOT NULL, unntak_id BIGINT NOT NULL,
  hi_integritet_hash TEXT NOT NULL, policy_hash TEXT NOT NULL,
  decision_operation_id TEXT NOT NULL,
  motorutfall TEXT NOT NULL
    CHECK (motorutfall IN ('TILLAT_SIDEEFFEKTFRI','TILLAT_OUTBOX',
                           'STOPP','TIL_UNNTAK')),
  beslutning_loggpost_id BIGINT NOT NULL,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, unntak_id, hi_integritet_hash, policy_hash),
  UNIQUE (tenant, decision_operation_id),
  FOREIGN KEY (tenant, unntak_id) REFERENCES unntak (tenant, id),
  FOREIGN KEY (tenant, beslutning_loggpost_id)
    REFERENCES revisjonslogg (tenant, id)
);
```
- **Bindingstrigger** (herdet) verifiserer ved INSERT at
  `revisjonslogg`-raden faktisk bærer samme `decision_operation_id`
  (som `idempotency_key`/operasjons-id) og samme tenant — FK-ene alene
  beviser bare eksistens, triggeren beviser TILHØRIGHET.
- **AAD-feltene er serverautoritative:** `target_action` og `policy_hash`
  hentes fra den LÅSTE saksraden og det aktive policyregisteret — ALDRI
  fra ciphertext (som ikke kan leses før dekryptering) og aldri fra
  klienten. Kryptering/dekryptering bruker samme serverkilde begge veier.
- **Utfallsraden opprettes i SAMME COMMIT som beslutningsloggposten** —
  ingen tilstand der beslutningen finnes uten registrert utfall.
- Append-only (UPDATE/DELETE/TRUNCATE avvist), RLS+FORCE.

## Tester (tillegg)
Ordinært beslutnings-API kan ikke sette undertrykkingsflagget (statisk +
runtime) · undertrykking uteblir hvis ett av de fem vilkårene mangler ·
intensjonen kan ikke endres, og ny runde krever ny policyhash ·
`decision_operation_id` kan ikke gjenbrukes (UNIQUE) · loggpost fra annen
operasjon/sak avvist av bindingstrigger · ukjent `motorutfall` avvist av
CHECK · utfallsrad og loggpost committes sammen (krasj mellom → begge
borte) · AAD-felt hentet fra låst sak, ikke fra request.
