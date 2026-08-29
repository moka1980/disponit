# PR-014a SPESIFIKASJON v5 — DELTA (fire deploymentkontrakter → GO)

**Draft: Claude.ai · Register- og kontraktmodellen står. Fire lukket —
én skjemafeil og patchens livsløp.**

## 1. `moduldeployment` får kontraktkolonnene indeksen trenger

v4s indeks refererte `kontrakt_hash`, en kolonne v3 aldri la på tabellen.
Rettet — deployment bærer kontraktidentiteten direkte, håndhevet med
kompositt-FK (ingen join i indekslogikk):
```sql
CREATE TABLE moduldeployment (
  modul_id TEXT NOT NULL,
  release_id TEXT NOT NULL,
  kontraktversjon INT NOT NULL,
  kontrakt_hash TEXT NOT NULL,
  miljo TEXT NOT NULL CHECK (miljo IN ('staging','produksjon')),
  livslop TEXT NOT NULL
    CHECK (livslop IN ('claiming','draining','retired')),        -- §4
  fra_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (modul_id, miljo, release_id),
  -- release og deployment MÅ enes om kontrakten:
  FOREIGN KEY (modul_id, release_id, kontraktversjon, kontrakt_hash)
    REFERENCES modulrelease (modul_id, release_id, kontraktversjon, kontrakt_hash)
);
CREATE UNIQUE INDEX en_claiming_per_kontrakt ON moduldeployment
  (modul_id, miljo, kontraktversjon, kontrakt_hash) WHERE livslop = 'claiming';
```
`modulrelease` får tilsvarende `UNIQUE (modul_id, release_id,
kontraktversjon, kontrakt_hash)` som FK-mål.

## 2. Claim matcher HELE kontraktidentiteten

v4 filtrerte kun på hash. Rettet:
```
claim krever eksakt samsvar: modul_id + kontraktversjon + kontrakt_hash
```
- **Oppdraget lagrer BEGGE** (`kontraktversjon`, `kontrakt_hash`) ved
  opprettelse.
- **Samme hash under annen versjonsidentitet kan ikke kryssclaimes** uten
  en eksplisitt migrasjonsregel (finnes ikke i v1 — fail-closed).

## 3. Kontrakt vs. release: hvem refererer hva

Min retensjonsregel gjorde sikkerhetspatching umulig — policyen refererer
med hensikt samme kontrakt, så gammel deployment kunne aldri fjernes.
Rettet, fire klare referanseeiere:

| Hvem | Refererer |
|---|---|
| **Policy** | kontrakten (`kontraktversjon` + `kontrakt_hash`) |
| **Uclaimede oppdrag** | kontrakten |
| **Claim og kvittering** | den KONKRETE releasen (`release_id`) |
| **Revisjonslogg** | releasen + `artifact_digest` |

- **En deployment kan erstattes så snart en annen godkjent deployment
  leverer SAMME kontrakt.** Policyen merker ingenting.
- **Releasen beholdes som revisjonsevidens** så lenge claims,
  kvitteringer eller logger refererer den — men den blokkerer ikke
  utrulling av en patch.

## 4. `claiming → draining → retired` gir patchen et trygt vindu

Én aktiv release per kontrakt ga ikke plass til pågående claims. Rettet:
```
ny release           → claiming
gammel release       → draining     (ingen NYE claims; pågående claims og
                                     kvitteringer kan fullføre)
ingen aktive claims  → retired
```
- Delindeksen (§1) håndhever **én `claiming` per (modul, miljø, kontrakt)** —
  `draining` og `retired` kan være mange.
- Overgang `draining → retired` skjer når teller av aktive claims for
  releasen er null (kontrollert under lås, idempotent).
- **Nøddeaktivering overstyrer livsløpet** med epoch og karantene (§v3 5)
  — den venter ikke på draining.
- **Reaktivering etter nød** må knytte NY evidens og GJELDENDE epoch til
  hver deployment som igjen skal få `claiming`. Ingen deployment
  gjenoppstår automatisk.
- **Policyaktivering er fortsatt unødvendig** når `kontraktversjon` og
  `kontrakt_hash` er uendret — patchen ruller ut uten fire øyne, som den
  skal.

## Tester (tillegg)
Deployment med kontrakt som avviker fra releasen → FK avviser · to
`claiming` for samme (modul, miljø, kontrakt) → avvist av delindeks ·
claim med riktig hash men feil versjon → avvist · patchbytte: ny release
`claiming`, gammel `draining`, pågående claim fullfører, deretter
`retired` · policy urørt gjennom hele patchbyttet (ingen ny aktivering) ·
release med utestående kvittering kan ikke slettes, men blokkerer ikke
`claiming` av ny · nøddeaktivering under `draining` → epoch overstyrer,
ingen fullføring · reaktivering uten ny evidens per deployment → avvist.
