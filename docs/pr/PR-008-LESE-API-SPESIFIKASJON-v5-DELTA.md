# PR-008 SPESIFIKASJON v5 — DELTA (to tettinger + DTO-semantikk → GO)

**Draft: Claude.ai · v1–v4 står der de ikke motsies. Reviewens
koblingsstatus, avledede evidensflagg og DTO-invarianter vedtatt direkte.**

## 1. Koblingsstatus løser nullable-motsigelsen

v4 krevde både legacy `NULL` OG global `NOT NULL` — umulig samtidig.
Rettet med eksplisitt status:
```sql
ALTER TABLE oppdrag
  ADD COLUMN beslutning_loggpost_id BIGINT,
  ADD COLUMN koblingsstatus TEXT NOT NULL DEFAULT 'KOBLET'
    CHECK (koblingsstatus IN ('KOBLET','LEGACY_UKJENT')),
  ADD CONSTRAINT oppdrag_kobling_konsistent CHECK (
    (koblingsstatus='KOBLET'        AND beslutning_loggpost_id IS NOT NULL) OR
    (koblingsstatus='LEGACY_UKJENT' AND beslutning_loggpost_id IS NULL)),
  ADD CONSTRAINT oppdrag_beslutning_fk
    FOREIGN KEY (tenant, beslutning_loggpost_id)
    REFERENCES revisjonslogg (tenant, id);

CREATE UNIQUE INDEX oppdrag_en_per_beslutning
  ON oppdrag (tenant, beslutning_loggpost_id)
  WHERE beslutning_loggpost_id IS NOT NULL;   -- partiell, tåler NULL
```
DB-invarianter (trigger + CHECK):
- `KOBLET` ⇒ ikke-null FK; `LEGACY_UKJENT` ⇒ null FK (CHECK over).
- **`LEGACY_UKJENT` kan KUN settes av migrasjonen** på eksisterende rader —
  runtime-innsetting med `LEGACY_UKJENT` avvises av trigger (sjekker at
  transaksjonen er migrasjonskonteksten; ellers `KOBLET` påkrevd).
- Alle nye runtime-rader er `KOBLET` med ikke-null FK.
- **Koblingsstatus + FK uforanderlige etter innsetting** (kolonnelås-trigger).
- Detaljresponsen: `LEGACY_UKJENT`-oppdrag vises som «utførelsesdata ikke
  tilgjengelig», aldri koblet til en antatt beslutning.

Backfill via `repair_operation_id` krever ALLE fire:
samme tenant · nøyaktig ÉN matchende beslutningsloggpost · riktig
logghendelsestype (fase-2-beslutning) · riktig reparasjonsidentitet.
Null ELLER flere enn én kandidat → `LEGACY_UKJENT`. ALDRI første/siste rad.

## 2. Evidensflagg AVLEDET fra append-only evidens (ikke kartesisk)

v4s `{false|true}` på alle outbox var et kartesisk produkt, ikke en
matrise. Rettet — feltene avledes DIREKTE fra evidensradene, og
invarianter forbyr de umulige kombinasjonene:
```
evidensstatus =
  GYLDIG         hvis autoritativ kvittering akseptert innen frist
  MANGLER        hvis outbox krevde kvittering, men ingen akseptert i tide
  IKKE_RELEVANT  hvis oppdraget aldri krevde ordinær kvittering
sen_evidens      = EXISTS(gyldig kvittering klassifisert SEN)
konflikt_evidens = EXISTS(motstridende kvittering/sikkerhetshendelse)
```
Låste invarianter (servermodellen avviser alt annet):
- Ikke-outbox → `IKKE_RELEVANT`, begge flagg `false`.
- `outbox_utfort` → `GYLDIG`.
- **`outbox_utfort + sen_evidens=true` KREVER `konflikt_evidens=true`** —
  fordi identisk sen replay er idempotent no-op (setter INGEN flagg), så en
  sen kvittering som faktisk er registrert på et allerede utført oppdrag
  må være en AVVIKENDE kvittering = konflikt. Kombinasjonen
  `outbox_utfort + sen=true + konflikt=false` har ingen legitim DB-vei og
  avvises.
- `konflikt_evidens=true` ⇒ sikkerhetssak finnes (server-invariant fra v4).
- `sen_evidens` alene endrer ALDRI oppdragsstatus til utført.
- `outbox_kansellert` kan ha sen og/eller konflikt uten gjenåpning.
- **Identisk replay setter VERKEN sen- eller konfliktflagg** (idempotent).
- Flere evidensrader → server aggregerer etter dokumentert presedens
  (konflikt > sen), avledet fra append-only-radene, ikke fritt satt.

Dette gjør matrisen faktisk total: hver (art, evidensstatus, sen, konflikt)
er enten avledbar fra en reell evidenskonfigurasjon eller avvist.

## 3. Semantisk policy-DTO-validering (utover formen)

Formen var lukket (v4); semantikken låses nå i backendmodellen:
- `roller`, `handlinger`, `vilkaar`, `betrodd_for`, `ukedager` — UNIKE
  elementer (ingen duplikater).
- `belop_maks` — lukket decimal-regex `^\d{1,13}\.\d{2}$`, POSITIV, maks
  presisjon/skala definert; **`valuta` PÅKREVD når `belop_maks` finnes,
  ellers null.**
- `maks`, `vindu_antall` — øvre grenser (f.eks. ≤ 100000) i tillegg til ≥1.
- `fra`, `til` — reelle klokkeslett (00:00–23:59), validert.
- `skjemaversjon` — støttet KONSTANT/enum (f.eks. `1`), ikke vilkårlig int.
- **Serveren returnerer ALDRI `{}` for `grenser`** — normaliseres til
  `null` (tomt objekt og null betyr det samme, v4; her håndhevet på ut-veien).

## Bindende tester (reviewens, vedtatt)
Legacy-rad uten kobling overlever migrasjonen som `LEGACY_UKJENT` · runtime
kan IKKE opprette `LEGACY_UKJENT` (trigger) · to loggposter med samme
reparasjonsidentitet → ingen automatisk backfill (`LEGACY_UKJENT`) ·
identisk sen replay setter INGEN evidensflagg · sen motstridende kvittering
setter BEGGE flagg · `outbox_utfort + sen=true + konflikt=false` avvist
(ingen legitim DB-vei) · konflikt uten sikkerhetssak avvist (invariant) ·
tomme/dupliserte policyfelt avvist/normalisert · `grenser={}` normaliseres
til null på ut-veien · `belop_maks` uten `valuta` avvist.

## Ingen åpne punkter
Koblingsstatus løser nullable-migrasjonen · evidensflagg avledet +
totalvalidert · DTO semantisk låst. Klar for GO.
