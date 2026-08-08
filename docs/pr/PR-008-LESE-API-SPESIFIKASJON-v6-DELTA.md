# PR-008 SPESIFIKASJON v6 — DELTA (migrasjonsrekkefølge + vakt + DTO-tall → GO)

**Draft: Claude.ai · v1–v5 står; evidensflaggmodellen er godkjent.
Reviewens trinnvise rekkefølge, rekkefølge-som-vakt og eksakte grenser
vedtatt direkte. Ren mekanikk, ingen ny semantikk.**

## 1. Trinnvis migrasjon i én runner-eid transaksjon

v5s samlede `ALTER TABLE` aktiverte CHECK før backfill → legacy-rader
(`KOBLET` + NULL) ville brutt CHECK umiddelbart. Rettet — nøyaktig denne
rekkefølgen i migrasjon 008 (kjøreren eier transaksjonen, ingen
BEGIN/COMMIT i fila):
```sql
-- 1. Kolonner nullable, INGEN default, INGEN CHECK
ALTER TABLE oppdrag
  ADD COLUMN beslutning_loggpost_id BIGINT,
  ADD COLUMN koblingsstatus TEXT;

-- 2. Backfill eksisterende rader (entydig repair_operation_id-match, §v5.1)
--    entydig → FK + 'KOBLET'; ellers → NULL + 'LEGACY_UKJENT'
UPDATE oppdrag o SET
  beslutning_loggpost_id = k.loggpost_id, koblingsstatus = 'KOBLET'
  FROM (/* entydig match: samme tenant, nøyaktig én fase-2-loggpost,
           riktig hendelsestype, riktig reparasjonsidentitet */) k
  WHERE o.tenant = k.tenant AND o.id = k.oppdrag_id;
UPDATE oppdrag SET koblingsstatus = 'LEGACY_UKJENT'
  WHERE koblingsstatus IS NULL;

-- 3. Ingen rad har null koblingsstatus (fail-hard hvis brudd)
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM oppdrag WHERE koblingsstatus IS NULL) THEN
    RAISE EXCEPTION 'backfill ufullstendig — avbryter migrasjon';
  END IF;
END $$;

-- 4. Kompositt-FK
ALTER TABLE oppdrag ADD CONSTRAINT oppdrag_beslutning_fk
  FOREIGN KEY (tenant, beslutning_loggpost_id) REFERENCES revisjonslogg (tenant, id);

-- 5. CHECK: NOT VALID først, så VALIDATE (rader er allerede konsistente)
ALTER TABLE oppdrag ADD CONSTRAINT oppdrag_kobling_konsistent CHECK (
  (koblingsstatus='KOBLET'        AND beslutning_loggpost_id IS NOT NULL) OR
  (koblingsstatus='LEGACY_UKJENT' AND beslutning_loggpost_id IS NULL)) NOT VALID;
ALTER TABLE oppdrag VALIDATE CONSTRAINT oppdrag_kobling_konsistent;

-- 6-7. NOT NULL + default for fremtidige inserts
ALTER TABLE oppdrag ALTER COLUMN koblingsstatus SET NOT NULL;
ALTER TABLE oppdrag ALTER COLUMN koblingsstatus SET DEFAULT 'KOBLET';

-- 8. Partiell UNIQUE (tåler NULL)
CREATE UNIQUE INDEX oppdrag_en_per_beslutning
  ON oppdrag (tenant, beslutning_loggpost_id) WHERE beslutning_loggpost_id IS NOT NULL;

-- 9. Runtime-triggere TIL SLUTT (se §2)
```
Feil hvor som helst → hele migrasjonen ruller tilbake (kjøreren eier
transaksjonen). Tabellen er konsistent FØR vaktene aktiveres.

## 2. Rekkefølge er vakten — ingen spoofbar «migrasjonskontekst»

v5s trigger stolte på en custom setting som sa «migrasjon» — spoofbar hvis
runtime kan `SET app.migrasjon`. Fjernet helt. Vakten ER rekkefølgen:
- **Backfill (steg 2) kjører FØR INSERT-triggeren opprettes (steg 9).**
  Legacy-radene er dermed satt før nokon vakt finnes.
- Etter steg 9 avviser INSERT-triggeren ENHVER ny `LEGACY_UKJENT` — også
  fra migrator/vanlig bruk. Ingen bypass eksisterer.
- En fremtidig migrasjon som virkelig trenger unntak må EKSPLISITT
  droppe+gjenopprette vakten i en reviewet migrasjon — ikke en runtime-flagg.
- Runtime har ingen generell INSERT-rett; oppdrag opprettes via den
  eksisterende avgrensede skriveveien (PR-006 claim/kvittering-funksjoner).

Trigger (opprettet steg 9) låser uforanderlighet:
```
INSERT: koblingsstatus='LEGACY_UKJENT' → avvist (kun KOBLET fra runtime)
UPDATE: endring av koblingsstatus, beslutning_loggpost_id → avvist
DELETE: avvist (append-only som øvrige oppdrag)
```
Senere manuell legacy-reparasjon (hvis noen gang) = separat auditert
SECURITY DEFINER-prosedyre med KUN overgangen `LEGACY_UKJENT → KOBLET`,
aldri generell UPDATE. Deklarert, ikke i PR-008.

## 3. Eksakte DTO-konstanter (ikke «f.eks.»)

v5s «f.eks. ≤ 100000» var ikke en kontrakt. Låst nå:

| Felt | Grense |
|---|---|
| `roller[]` | ≤ 50 |
| `handlinger[]` | ≤ 200 |
| `verifikatorer[]` | ≤ 100 |
| `vilkaar[]` per handling | ≤ 50 |
| `betrodd_for[]` per verifikator | ≤ 50 |
| `ukedager[]` | ≤ 7 |
| `FrekvensDTO.maks` | 1 … 100 000 |
| `FrekvensDTO.vindu_antall` | 1 … 10 000 |
| strenglengder (`policy_id`,`versjon`,`navn`,`*_id`,`*_kode`) | ≤ 128 (versjon ≤ 64, innholds_hash = 64) |
| `belop_maks` | regex `^\d{1,13}\.\d{2}$`, positiv (skala 2, presisjon ≤ 15) |

Unikhetsnøkler (duplikat = valideringsfeil):
`roller` etter `id` · `handlinger` etter `navn` · `verifikatorer` etter
`offentlig_id` · `vilkaar` og `betrodd_for` etter eksakt kode · `ukedager`
etter heltallsverdi.

## Bindende migrasjonstester (reviewens, vedtatt)
Migrasjon med både entydige OG ukjente legacy-rader fullfører · alle rader
tilfredsstiller CHECK før VALIDATE · runtime kan ikke forfalske
migrasjonsmodus (ingen slik modus finnes) · ny rad uten beslutnings-FK
avvist · ny rad med `LEGACY_UKJENT` avvist · FK og status uforanderlige
etter innsetting · to koblede oppdrag samme beslutning avvist (partiell
UNIQUE) · feil midt i backfill → hele migrasjonen rullet tilbake.

## Ingen åpne punkter
Migrasjonsrekkefølge korrekt · vakt uspoofbar (rekkefølge, ikke flagg) ·
DTO-grenser eksakte konstanter. Klar for GO.
