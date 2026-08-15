# PR-014a SPESIFIKASJON v2 — DELTA (åtte kontrakter → GO)

**Draft: Claude.ai · Registerretningen står. Den bærende rettelsen:
kontrakt og release er to ulike identiteter.**

## 1. Tre identiteter — kontrakt bindes, release ikke

`manifest_hash` blandet hva modulen LOVER med hvilken binær som KJØRTE.
Da ville enhver sikkerhetsoppdatering krevd ny policyaktivering — et
system der man kvier seg for å patche. Rettet:

| Identitet | Hva den dekker | Hvem binder den |
|---|---|---|
| `kontraktversjon` | Grov kompatibilitetsgenerasjon | **Policyen** |
| `kontrakt_hash` | Payload-, kvitterings- og semantikkontrakten | **Policyen** |
| `artifact_digest` | Eksakt container/binær som utførte | **Kvittering + revisjonslogg** |

- **Policyen binder ALLTID `kontraktversjon` + `kontrakt_hash`** — aldri
  manifesthash, aldri artifact-digest.
- **Kontraktsendring krever ny policyaktivering.** Bakoverkompatibel
  kodeoppdatering beholder kontrakthashen, får nytt `artifact_digest`, og
  krever INGEN policyendring.
- `artifact_digest` registreres ved utførelse, så revisjonen alltid viser
  nøyaktig hvilken binær som gjorde jobben.

## 2. Immutable releases (erstatter én muterbar rad)

```sql
modulhode(modul_id PK, status ['installert'|'staging_verifisert'|'aktiv'|'nodeaktivert'],
          modulrevisjon BIGINT NOT NULL DEFAULT 0,   -- monoton, per modul
          module_epoch BIGINT NOT NULL DEFAULT 0)     -- §5

modulrelease(modul_id, release_id, kontraktversjon INT, kontrakt_hash TEXT,
             manifest_hash TEXT, artifact_digest TEXT,
             opprettet TIMESTAMPTZ, PRIMARY KEY (modul_id, release_id))
             -- IMMUTABLE: ingen UPDATE (trigger), append-only
```
- **Flere releases kan eksistere samtidig** — utrullingen er ikke lenger
  sirkulær.
- **En release fjernes først når ingen policy og ingen utestående oppdrag
  refererer den** (FK-sjekk, samme retention-prinsipp som policyversjoner).

## 3. Oppdragstyper normaliseres til rader med global unikhet

Tekst-array var ubeskyttet. Rettet:
```sql
oppdragstype_register(
  oppdragstype TEXT PRIMARY KEY,          -- globalt unik
  eiermodul TEXT NOT NULL,
  kontrakt_hash TEXT NOT NULL,
  payload_schema_hash TEXT NOT NULL,
  kvittering_schema_hash TEXT NOT NULL)
```
- **DB avviser at to moduler eier samme oppdragstype.**
- **Reserverte prefiks kan ikke overlappe** — håndhevet av
  eksklusjonsconstraint/trigger (`audit.` og `audit.wcag.` kan ikke eies
  av ulike moduler).

## 4. `modules:manage` alene kan ikke gjøre en modul aktiv

Overgang til `staging_verifisert` og `aktiv` krever **maskinverifisert
evidens**, ikke bare et menneskelig scope. Den herdede
overgangsfunksjonen kontrollerer:
- riktig release/`artifact_digest`,
- **godkjent evidensartefakt** (samme port som `manifestskjema`-
  KRAVGRENSER: åpner filen, verifiserer sha256, regner tallene på nytt),
- kontrakttester bestått for `kontrakt_hash`,
- påkrevde sikkerhetsporter,
- lovlig foregående status.
Ett vilkår som mangler → overgangen avvises. Scopet gir *retten til å
forsøke*, ikke retten til å bestemme.

## 5. Nøddeaktivering: epoch, ikke løfte om «ingen utførelse»

En claimet eiermodul kan ha utført sideeffekten allerede. Rettet —
nøddeaktivering gjør ATOMISK:
1. status → `nodeaktivert`,
2. **`module_epoch` økes** (monoton),
3. nye beslutninger og claims stoppes,
4. **alle gamle claim-/kvitteringskapabiliteter ugyldiggjøres som
   automatisk fullføringsbevis.**

**Kvittering fra gammel epoch:** tas imot som **append-only, karantenesatt
evidens** — aldri forkastet, aldri automatisk `løst`. Saken går til
`avklaring_kreves` (samme mønster som gate 14a: systemet påstår ikke noe
databasen ikke kan bevise).
Nøddeaktiveringstransaksjonen **skanner ikke alle oppdrag** (§6).

## 6. Uclaimede oppdrag: kontroll ved claim, ikke ved deaktivering

Et oppdrag kan være opprettet før nøddeaktivering, men ikke claimet.
**Ved claim kontrolleres epoch og modulstatus under oppdragslåsen.**
Avvik:
- **ingen payload og ingen kapabilitet utleveres**,
- oppdraget merkes `blokkert_av_modulstatus`,
- tilknyttet sak går fail-closed til UNNTAK/manuell avklaring.
Lat deteksjon, som PR-013 §8 — deteksjonen er autoriteten, ikke en
ivrig skanning.

## 7. Registerbinding PER REFERERT MODUL

Global `registerversjon` gjorde at oppdatering av en irrelevant modul
kansellerte alle åpne policyaktiveringer — en tilgjengelighetsvektor.
Rettet: aktiveringsrunden binder en **kanonisk hash over de konkrete
modulkontraktene policyen refererer**:
```
modulbinding_hash = SHA-256(JCS(sortert liste av
  (modul_id, oppdragstype, kontraktversjon, kontrakt_hash, modulrevisjon)))
```
**Bare endring i en REFERERT binding kansellerer runden.**

## 8. Global låserekkefølge (mot policyadministrasjonen)

Fastsatt, gjelder alle veier:
1. **berørte modulhoder, sortert på `modul_id`**
2. `policy_hode`
3. policy-/utkast-/aktiveringsrader
4. `modulrelease` og oppdragstypebindinger

Deadlock-test med **samtidig policyaktivering, releasepromotering og
deaktivering**.

## Svar på v1-spørsmålene (reviewens, vedtatt)
1. Policyen binder `kontraktversjon` + `kontrakt_hash`. Artifact-digest
   registreres ved utførelse.
2. Kvittering etter nøddeaktivering bevares som karantenesatt evidens;
   saken går til avklaring.
3. Per modul, med rundehash over kun refererte moduler.

Staging-kapabiliteten står (v1 §6). **Produksjonsartefaktet må mangle
BÅDE testnøkkel og test-issuer-konfigurasjon** — verifiseres ved deploy.

## Tester (tillegg)
Sikkerhetsoppdatering m/ samme `kontrakt_hash` krever IKKE ny
policyaktivering · kontraktsendring krever det · to moduler kan ikke eie
samme oppdragstype · overlappende prefiks avvist · `modules:manage` uten
godkjent evidensartefakt → overgang avvist · nøddeaktivering: ny claim
avvist, kvittering fra gammel epoch lagres karantenesatt og gir
avklaring · uclaimet oppdrag ved epoch-avvik → ingen payload utlevert,
sak til UNNTAK · irrelevant modulendring kansellerer IKKE åpen runde ·
referert modulendring gjør det · deadlock-test grønn · release kan ikke
slettes mens policy eller oppdrag refererer den.
