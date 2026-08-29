# PR-014a — IMPLEMENTERINGSKLARSIGNAL (GO, modulregister)

**Til Claude Code · Konsolidert v1–v5 + tre nye vilkår. Branch:
`pr-014a-modulregister`. Første av tre: 014a → 014b → 014c.
Forutsetning: `m37_unntak`-aksept lukket.**

**Dette er plattforminfrastruktur alle senere eiermoduler arver.**

---

## 1. Samlet DDL (migrasjon 013) — autoritativ, erstatter deltaene

```sql
-- Kontrakten: hva modulen LOVER (immutable)
CREATE TABLE modulkontrakt (
  modul_id TEXT NOT NULL, kontraktversjon INT NOT NULL,
  kontrakt_hash TEXT NOT NULL,
  payload_schema_hash TEXT NOT NULL,
  kvittering_schema_hash TEXT NOT NULL,
  sideeffektklasse TEXT NOT NULL
    CHECK (sideeffektklasse IN ('sideeffektfri','krever_outbox')),
  reversibilitet TEXT NOT NULL
    CHECK (reversibilitet IN ('direkte','kompenserende','irreversibel')),
  PRIMARY KEY (modul_id, kontraktversjon),
  UNIQUE (modul_id, kontraktversjon, kontrakt_hash));

-- Modulens tilstand
CREATE TABLE modulhode (
  modul_id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN
    ('installert','staging_verifisert','aktiv','nodeaktivert')),
  modulrevisjon BIGINT NOT NULL DEFAULT 0,
  module_epoch  BIGINT NOT NULL DEFAULT 0,
  status_ts TIMESTAMPTZ NOT NULL DEFAULT now());

-- Releaser: immutable, flere samtidig
CREATE TABLE modulrelease (
  modul_id TEXT NOT NULL, release_id TEXT NOT NULL,
  kontraktversjon INT NOT NULL, kontrakt_hash TEXT NOT NULL,
  manifest_hash TEXT NOT NULL, artifact_digest TEXT NOT NULL,
  opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (modul_id, release_id),
  UNIQUE (modul_id, release_id, kontraktversjon, kontrakt_hash),
  FOREIGN KEY (modul_id, kontraktversjon, kontrakt_hash)
    REFERENCES modulkontrakt (modul_id, kontraktversjon, kontrakt_hash));

-- Hva som faktisk kjører
CREATE TABLE moduldeployment (
  modul_id TEXT NOT NULL, release_id TEXT NOT NULL,
  kontraktversjon INT NOT NULL, kontrakt_hash TEXT NOT NULL,
  miljo TEXT NOT NULL CHECK (miljo IN ('staging','produksjon')),
  livslop TEXT NOT NULL CHECK (livslop IN ('claiming','draining','retired')),
  fra_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (modul_id, miljo, release_id),
  FOREIGN KEY (modul_id, release_id, kontraktversjon, kontrakt_hash)
    REFERENCES modulrelease (modul_id, release_id, kontraktversjon, kontrakt_hash));
CREATE UNIQUE INDEX en_claiming_per_kontrakt ON moduldeployment
  (modul_id, miljo, kontraktversjon, kontrakt_hash) WHERE livslop = 'claiming';

-- Globalt unike oppdragstyper
CREATE TABLE oppdragstype_register (
  oppdragstype TEXT PRIMARY KEY,
  eiermodul TEXT NOT NULL,
  kontraktversjon INT NOT NULL, kontrakt_hash TEXT NOT NULL,
  FOREIGN KEY (eiermodul, kontraktversjon, kontrakt_hash)
    REFERENCES modulkontrakt (modul_id, kontraktversjon, kontrakt_hash));

CREATE TABLE modulregister_hendelse (...);   -- append-only, alle overganger
```
Immutabilitet håndheves med triggere: `modulkontrakt` og `modulrelease`
tåler ingen UPDATE; `modulhode.status`/`module_epoch` og
`moduldeployment.livslop` endres KUN via overgangsfunksjonene (§2).

## 2. Herdede overgangsfunksjoner (SECURITY DEFINER, NOLOGIN-eier, `search_path=pg_catalog`)

| Funksjon | Håndhever |
|---|---|
| `registrer_oppdragstype()` | Global namespace-advisory-lås + prefiks-overlappssjekk. Eneste vei inn |
| `sett_modulstatus()` | Lovlig foregående status · **maskinverifisert evidens** (åpner artefakt, verifiserer sha256, regner tall mot KRAVGRENSER) · riktig release · kontrakttester · `aktiv` krever ≥1 `claiming`-deployment |
| `noddeaktiver_modul()` | `modules:emergency`. Status → `nodeaktivert`, `module_epoch++`, auditert m/ begrunnelse. **Overstyrer livsløp** — venter ikke på draining |
| `reaktiver_modul()` | Krever NY evidens per deployment + gjeldende epoch. Ingen deployment gjenoppstår automatisk. `module_epoch++` igjen |
| **`bytt_release()`** | **V1: atomisk releasebytte** — ny `claiming` + gammel `draining` i ÉN transaksjon under kontraktlås |
| `pensjoner_release()` | `draining → retired` når aktive claims = 0 (under lås, idempotent) |

## 3. De tre nye bindende vilkårene

### V1. Atomisk releasebytte
Ny release settes `claiming` OG gammel settes `draining` i **én herdet
transaksjon under kontraktlås**. **Direkte statusoppdatering er forbudt**
(runtime har ingen UPDATE på `moduldeployment`).

### V2. Utløpt claim fra `draining` release
Når et claim fra en `draining` release utløper, kan **ny `claiming`
release reclaime oppdraget** med ny releasebinding og nytt fencing-token.
**Gammel kvittering avvises da som stale evidens** (fencing-generasjonen
har flyttet seg — samme mekanikk som PR-006 owner-fencing).

### V3. Kvittering etter `retired`
`retired` betyr **«kan aldri claime»**, ikke «historiske kvitteringer er
ugyldige». En kvittering fra et eksisterende, **ikke-reclaimet** claim
må fortsatt kunne mottas **innen evidensfristen**.
**Release og deployment kan derfor ikke slettes** mens slike bindinger
finnes.

## 4. GRANT-modell (default-deny)

| Rolle | Rettighet |
|---|---|
| `disponit_runtime` | **SELECT** på alle registertabeller. **EXECUTE** på claim-/lesefunksjoner. **INGEN** INSERT/UPDATE/DELETE på `modulkontrakt`, `modulrelease`, `moduldeployment`, `oppdragstype_register`, `modulhode` |
| `disponit_modules_admin` | EXECUTE på overgangsfunksjonene (§2) — ikke direkte DML |
| Funksjonseiere | NOLOGIN, eier tabellene |
Negativ GRANT-test per tabell: runtime får `permission denied` på direkte
skriving.

## 5. Bindinger utad (uendret fra deltaene)

- **Policy binder** `modul_id · oppdragstype · kontraktversjon ·
  kontrakt_hash` (IKKE manifest, IKKE `modulrevisjon`).
  `modulbinding_hash` over kun refererte moduler; `module_epoch` bindes
  separat.
- **Claim krever eksakt** `modul_id + kontraktversjon + kontrakt_hash`,
  aktiv `claiming`-deployment og matchende epoch — kontrollert **under
  oppdragslåsen**.
- **`artifact_digest` fra serverkontekst**, aldri fra modulens payload.
  Revisjonstekst: «artifact-digest for den serverautoriserte
  deployment-identiteten som utførte oppdraget» — ikke bevis på at ingen
  annen kode kjørte.
- **Epoch gjennom hele kjeden:** oppdrag · owner-claim · artefakt-
  kapabilitet · kvitteringskapabilitet · resultatkvittering.
- **Karantene kun ved epoch-avvik**; signatur-/tenant-/oppdrags-/
  release-/bindingsavvik → sikkerhetsrouting.
- **Aktiveringsporten:** advarsel ved utkastvalidering, hard feil ved
  runde-åpning, revalidert under aktiveringslåsen.
- **Testkapabilitet:** egen issuer + nøkkel som ikke finnes i
  produksjonsartefaktet; omgår KUN modulstatus.
- **Global låserekkefølge:** modulhoder (sortert på `modul_id`) →
  `policy_hode` → policy/utkast/aktivering → `modulrelease`/
  oppdragstypebindinger.

## 6. Codex-porter

1. Release/deployment med avvikende kontrakt → FK avviser
2. To `claiming` for samme (modul, miljø, kontrakt) → avvist
3. **To samtidige patchbytter → nøyaktig én release ender `claiming`**
4. Claim med riktig hash men feil kontraktversjon → avvist
5. Workload for kontrakt A kan ikke claime kontrakt B
6. Patchbytte uten ny policyaktivering (kontrakt uendret)
7. Utløpt claim fra `draining` → reclaimet av `claiming`; gammel kvittering stale
8. Kvittering fra ikke-reclaimet claim mot `retired` release → mottas innen evidensfrist
9. Release/deployment kan ikke slettes med utestående bindinger
10. Modul som sender eget `artifact_digest` → kvittering avvist
11. Epoch-avvik i hvert av de fem leddene → ingen fullføring
12. Gammel epoch + gyldig signatur → karantene + avklaring; ugyldig signatur → sikkerhet
13. `aktiv` uten `claiming`-deployment → overgang avvist
14. `sett_modulstatus` uten godkjent evidensartefakt → avvist
15. Reaktivering uten ny evidens per deployment → avvist; epoch økt
16. Blokkert oppdrag uten sak → ny M-37-sak fra loggposten, idempotent; terminal sak aldri gjenbrukt
17. Runtime kan ikke skrive noen av de fem registertabellene
18. To samtidige overlappende prefiksregistreringer → én vinner
19. Produksjonsartefakt mangler testnøkkel OG test-issuer-konfig (deploy-port)
20. Deadlock-test: samtidig policyaktivering, releasepromotering og deaktivering

## 7. Evidensgrense `modulregister-v1`
Defineres i KRAVGRENSER FØR arbeidet: alle 20 portene dekket, pluss
manifest på disk ≠ register → deploy stopper.
