# PR-014a SPESIFIKASJON v3 — DELTA (åtte registerkontrakter → GO)

**Draft: Claude.ai · Kontrakt/release-skillet står. Nå gjøres det
håndhevbart.**

## 1. Deploymentbinding — registeret sier hvilken release som kjører

`modulhode` hadde status og `modulrelease` flere immutable rader, men
ingen autoritativ kobling til det som faktisk er deployet:
```sql
moduldeployment(
  modul_id TEXT NOT NULL, release_id TEXT NOT NULL,
  miljo TEXT NOT NULL CHECK (miljo IN ('staging','produksjon')),
  aktiv BOOLEAN NOT NULL,
  fra_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (modul_id, miljo, release_id),
  FOREIGN KEY (modul_id, release_id) REFERENCES modulrelease (modul_id, release_id));
CREATE UNIQUE INDEX en_aktiv_deployment ON moduldeployment (modul_id, miljo)
  WHERE aktiv;   -- flere kan tillates senere (kanari); v1: én
```
**Runtime velger KUN blant aktive deployments.** En vilkårlig historisk
release kan aldri claime oppdrag.

## 2. Kontrakten får egen autoritativ tabell

`kontrakt_hash` var fritekst to steder. Normalisert:
```sql
modulkontrakt(
  modul_id TEXT NOT NULL, kontraktversjon INT NOT NULL,
  kontrakt_hash TEXT NOT NULL,
  payload_schema_hash TEXT NOT NULL,      -- immutable
  kvittering_schema_hash TEXT NOT NULL,   -- immutable
  sideeffektklasse TEXT NOT NULL
    CHECK (sideeffektklasse IN ('sideeffektfri','krever_outbox')),
  reversibilitet TEXT NOT NULL
    CHECK (reversibilitet IN ('direkte','kompenserende','irreversibel')),
  PRIMARY KEY (modul_id, kontraktversjon));
```
- **FK fra `modulrelease` OG `oppdragstype_register` til samme
  kontraktrad.** En release kan ikke erklære en kontrakthash registeret
  ikke kjenner.
- Alle felt immutable etter innsetting (trigger).

## 3. `artifact_digest` kommer fra SERVERKONTEKST, aldri fra modulen

En kompromittert modul kunne skrevet en annen, godkjent binærs digest i
kvitteringen — altså løyet om hva som kjørte. Rettet:
- **Modultoken/workload-identitet bindes server-side til
  `(release_id, artifact_digest)` VED DEPLOY** (i `api_tokener` eller eget
  workload-register).
- **Claim og kvittering henter identiteten fra AUTENTISERT
  SERVERKONTEKST**, aldri fra kvitteringspayloaden.
- Sender modulen likevel et digest-felt → **avvises** (lukket skjema).
- Revisjonsloggen registrerer serverens digest, ikke modulens påstand.

## 4. Epoch bindes gjennom HELE oppdragskjeden

Å øke `module_epoch` er ikke nok — den må følge med. `module_epoch`
lagres og kontrolleres i:
`oppdrag` (ved opprettelse) · owner-claim/fencing · artefaktkapabilitet ·
kvitteringskapabilitet · resultatkvittering og evidens.
**Automatisk fullføring krever EKSAKT samsvar med modulens aktuelle epoch
under samme lås.** Avvik → ingen fullføring (§5).

## 5. Karanteneveien VERIFISERER fortsatt alt annet

«Bevar kvitteringen» kan ikke bety at vilkårlige eller falske
kvitteringer lagres som troverdig evidens. Karanteneinngest verifiserer
FULLT: signatur · tenant · oppdrag · release · kapabilitetsbinding · epoch.
- **Kun EPOCH-avvik** → karantenesatt evidens, sak → `avklaring_kreves`.
- **Signatur-, tenant-, oppdrags-, release- eller bindingsavvik** →
  **sikkerhetsrouting**, ingen evidensrad.
Skillet er bindende: karantene er for *utdatert*, ikke for *ugyldig*.

## 6. Policybindingen er patchvennlig — `modulrevisjon` UT

En kompatibel sikkerhetsrelease øker normalt `modulrevisjon`, som ville
endret `modulbinding_hash` selv med identisk kontrakt — altså igjen straff
for å patche. Rettet:
```
modulbinding_hash = SHA-256(JCS(sortert liste av
  (modul_id, oppdragstype, kontraktversjon, kontrakt_hash)))
```
**`modulrevisjon` inngår IKKE.** `module_epoch` bindes SEPARAT (for
status-/nødendringer), slik at en nøddeaktivering fortsatt fanges.
**Releasepromotering med uendret kontrakt endrer ikke policybindingen.**

## 7. Blokkert oppdrag uten sak — eksplisitt vei

En ordinær TILLAT kan opprette outbox-oppdrag uten unntaksrad, så
«tilknyttet sak» finnes ikke alltid. Claim-funksjonen gjør ATOMISK:
1. blokker payload og kapabilitet (ingen utlevering),
2. registrer modulstatusfeilen på oppdraget
   (`blokkert_av_modulstatus`, epoch + status lagres),
3. finn eventuell eksisterende sak via stabil FK
   (`oppdrag.beslutning_loggpost_id → unntak`, PR-008), ELLER
   **opprett/rut én M-37-sak fra beslutningsloggposten**,
4. **idempotent under flere claim-forsøk** — gjentatt claim gir samme
   resultat og ingen ny sak (UNIQUE på (oppdrag, blokkeringsårsak)).

## 8. Prefikslås og full statusmaskin

**Namespace-samtidighet:** en trigger som SØKER etter overlappende
prefiks kan slippe to samtidige innsettinger under ulike modulhodelåser.
Rettet: **global namespace-lås** (`pg_advisory_xact_lock` på konstant
namespace-nøkkel) rundt all innsetting i `oppdragstype_register`, ELLER
`EXCLUDE`-constraint på prefiksrelasjonen. Valgt: advisory-lås +
verifiserende trigger (enklest, deterministisk).

**Full statusmaskin:**
```
installert → staging_verifisert → aktiv
aktiv | staging_verifisert → nodeaktivert        (nød, epoch++)
nodeaktivert → staging_verifisert                 (reaktivering)
staging_verifisert → aktiv                        (ny evidens kreves)
```
- **Reaktivering krever NY evidens** (nytt godkjent artefakt for den
  releasen som skal kjøre) — aldri gjenbruk av evidensen som gjaldt før
  nøddeaktiveringen.
- **`module_epoch` økes IGJEN ved reaktivering** — gamle kapabiliteter
  kan aldri gjenoppstå.
- Reaktivering krever `modules:emergency` + evidensport (§v2 4).
- **Ingen direkte statusoppdatering** — kun via de herdede
  overgangsfunksjonene (runtime har ingen UPDATE på `modulhode`).

## Tester (tillegg)
Historisk release kan ikke claime · release med ukjent kontrakthash
avvises av FK · modul som sender eget digest → kvittering avvist ·
digest i revisjonslogg = serverens, ikke modulens · epoch-avvik i hvert
av de fem leddene → ingen fullføring · gammel epoch + gyldig signatur →
karantene + avklaring · gammel epoch + UGYLDIG signatur → sikkerhet,
ingen evidensrad · sikkerhetspatch m/ samme kontrakt → policybinding
uendret · nøddeaktivering endrer binding via epoch · blokkert oppdrag
uten sak → M-37-sak opprettet fra loggposten, idempotent · to samtidige
overlappende prefiksregistreringer → én vinner · reaktivering uten ny
evidens avvist · epoch økt ved reaktivering · runtime kan ikke UPDATE
`modulhode`.
