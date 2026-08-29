# PR-014a SPESIFIKASJON v4 — DELTA (seks kontrakter → GO)

**Draft: Claude.ai · Seks lukket. Den viktigste: parallelle kontrakter
under migrering.**

## 1. Kompositt-FK binder hele kontraktidentiteten

PK `(modul_id, kontraktversjon)` hindret ikke at release-raden lagret en
ANNEN `kontrakt_hash`. Rettet:
```sql
ALTER TABLE modulkontrakt
  ADD CONSTRAINT modulkontrakt_identitet
  UNIQUE (modul_id, kontraktversjon, kontrakt_hash);

-- modulrelease OG oppdragstype_register:
FOREIGN KEY (modul_id, kontraktversjon, kontrakt_hash)
  REFERENCES modulkontrakt (modul_id, kontraktversjon, kontrakt_hash)
```
Hele identiteten bindes, ikke bare nøkkelen.

## 2. Parallelle kontraktdeployments — sirkelen lukket for godt

Én aktiv deployment per modul gjenskapte oppgraderingssirkelen: gamle
policyer og oppdrag trenger gammel kontrakt, mens ny policy ikke kan
aktiveres før ny kontrakt er aktiv. Rettet:
```sql
CREATE UNIQUE INDEX en_aktiv_per_kontrakt ON moduldeployment
  (modul_id, miljo, kontrakt_hash) WHERE aktiv;
```
- **Én aktiv deployment per (modul, miljø, kontrakt_hash)** — flere
  kontrakter kan kjøre samtidig under migrering.
- **Runtime ruter oppdraget til en release som implementerer oppdragets
  EKSAKTE kontrakt** (fra oppdragets lagrede `kontrakt_hash`).
- **Gammel deployment fjernes først når ingen policy og ingen utestående
  oppdrag refererer kontrakten** (samme retention-prinsipp som releases og
  policyversjoner).

## 3. Claim filtreres på workloadens kontrakt

En release-token skal ikke kunne claime alle modulens oppdrag.
Serverkonteksten binder: `modul_id · miljo · release_id ·
kontraktversjon · kontrakt_hash · artifact_digest · module_epoch`.
- **Claim returnerer KUN oppdrag med samme modul OG samme kontrakt_hash.**
- **Aktiv deployment og epoch kontrolleres under oppdragslåsen** — ikke
  bare ved tokenutstedelse.
- En workload for gammel kontrakt kan dermed tømme sin egen kø under
  migrering, men aldri røre ny-kontrakt-oppdrag.

## 4. Én herdet overgangsfunksjon håndhever statuskonsistens

`modulhode.status='aktiv'` kunne eksistert uten aktiv deployment, eller
en deployment være aktiv mens modulen var nøddeaktivert. Rettet — én
funksjon håndhever atomisk:
- **`aktiv` krever minst én godkjent, aktiv deployment.**
- **Nøddeaktivering gjør ALLE deployments ikke-claimbare** gjennom
  status + epoch (deploymentradene røres ikke — §v3 6, lat deteksjon).
- **Reaktivering navngir release OG kontrakt** som den nye evidensen
  gjelder for.
- **Runtime-rollene er fratatt direkte INSERT/UPDATE/DELETE på
  `moduldeployment`, `modulkontrakt` og `oppdragstype_register`** —
  negativ GRANT-test.

## 5. Ærlig formulering: digest er deploymentevidens

Et serverbundet token hindrer modulen i å velge digest i payloaden, men
et kompromittert workload kan bruke sin egen credential etter
kodeinjeksjon. Revisjonen formulerer derfor:
> **artifact-digest for den serverautoriserte deployment-identiteten som
> utførte oppdraget**

**Ikke** «kryptografisk bevis på at ingen annen kode kjørte». Samme
disiplin som «automatisk WCAG-kontroll» og «ferskhet»-kontrakten i
PR-010: vi lover nøyaktig det evidensen bærer.
**014b må beskytte credentialen** gjennom kort levetid, releasebinding og
controller/browser-separasjon.

## 6. Sak-gjenbruk krever eksplisitt gjenbrukbarhet

Et unntak funnet via beslutningsloggposten kan være **terminalt** eller
gjelde en **annen feilfamilie**. Claim-feilen kobles KUN til en sak som
positivt er:
- ikke-terminal (`ny`, `manuell`, `venter_*`), OG
- av familien `modulstatus` (eksplisitt felt, ikke utledet).

Ellers **opprettes en ny, idempotent M-37-sak** med lineage til oppdrag
og beslutningsloggpost. **Terminale saker endres ALDRI.**
(Positiv tillatelsesliste, som gate 14a §1 — ikke «den saken som tilfeldigvis
finnes».)

## 7. Namespace-låsen er ikke tilgjengelig for runtime

Advisory-låsen for oppdragstyperegistrering nås **kun gjennom den herdede
registreringsfunksjonen** (SECURITY DEFINER, NOLOGIN-eier). Runtime har
ingen direkte skriverett på `oppdragstype_register` (§4) og kan derfor
ikke omgå låsen ved å skrive direkte.

## Tester (tillegg)
Release med kontrakthash som ikke matcher kontraktraden → FK avviser ·
to kontrakter aktive samtidig under migrering, begge køer tømmes ·
workload for kontrakt A kan ikke claime oppdrag med kontrakt B ·
deployment fjernet mens oppdrag refererer kontrakten → avvist · `aktiv`
uten aktiv deployment → overgang avvist · nøddeaktivering → alle
deployments ikke-claimbare · runtime kan ikke skrive de tre tabellene ·
claim-feil mot terminal sak → ny sak opprettet, terminal urørt ·
claim-feil mot ikke-terminal modulstatussak → gjenbrukt, idempotent ·
revisjonstekst formulerer digest som deploymentevidens.
