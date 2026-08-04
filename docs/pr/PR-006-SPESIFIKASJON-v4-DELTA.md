# PR-006 SPESIFIKASJON v4 — DELTA (seks presiseringer → GO)

**Draft: Claude.ai · Reviewens anbefalte løsninger vedtatt direkte.
Alt godkjent i v1–v3 står uendret.**

## 1. Arbeidskapabilitet: reserver/bruk-livssyklus

Tilstandsmaskin `utstedt → reservert → brukt | feilet`, håndhevet i
kapabilitetstabellen (status-kolonne + CHECK + overgangs-guard i
SECURITY DEFINER-funksjonene):

1. Pre-auth kaller `reserver_kapabilitet(jti, request_id)` — atomisk
   `utstedt→reservert`, binder request_id, returnerer tenantkontekst.
   (`repair_operation_id` er allerede bundet ved utstedelse — se under.)
2. Forretningstransaksjonen REVALIDERER mot unntaksraden før bruk:
   claim_id, claim_generation, status='under_behandling', lease gyldig,
   repair_operation_id og tillatt_handling matcher — aldri kun
   utstedelsesverdiene.
3. Kapabiliteten settes `brukt` I SAMME COMMIT som den auditerte
   beslutningen (loggpost + idempotens ferdig).
4. Gjenopptak: SAMME request_id + samme Idempotency-Key
   (= repair_operation_id) kan gjenoppta en `reservert` kapabilitet;
   enhver annen request avvises.
5. Timeout på `reservert` (5 min) frigjør til `feilet` KUN hvis verken
   ferdig idempotensrespons eller auditert beslutning med samme
   repair_operation_id finnes — sjekket i frigjøringsfunksjonen.

**Parameterherding:** `utsted_arbeidskapabilitet` tar KUN
(claim_id, claim_generation). tillatt_handling og repair_operation_id
UTLEDES server-side fra den registrerte reparasjonsklassifiseringen på
saken (handler-deklarasjon + sakens handling/kategori) — arbeideren kan
aldri sende ønsket handling som parameter. Negativ test: kall med
egendefinert handling er umulig per signatur.

## 2. To frister: utførelse og evidens

Oppdrag får `utforelsesfrist` (default 24 t — siste tidspunkt resultat
kan endre status automatisk) og `evidensfrist` (default 30 dager —
siste tidspunkt signert kvittering mottas som sen evidens).
Kvitteringskapabiliteten utløper ved EVIDENSFRISTEN. Etter
utførelsesfristen: kvittering verifiseres og lagres, merkes
`sen_kvittering` i historikk, oppdrag/sak lukkes ALDRI automatisk,
motstridende resultat → sikkerhet. Etter evidensfristen: avvises
(administrativ import er utenfor PR-006-scope, deklarert).

## 3. Owner-fencing ved kvittering

Oppdrag får egne `owner_claim_id` + `owner_generation` (fencing for
eiermodulen, symmetrisk med sakens). Ingest-regler:
- Automatisk avslutning (før utførelsesfrist) krever at kvitteringen
  bærer GJELDENDE owner_claim_id + generation — mistet owner-lease =
  ingen ordinær terminalstatus fra gammel utfører.
- Gyldig signert kvittering fra utdatert generation lagres som
  sen/motstridende evidens; den vinner ALDRI over ny generation.
- To utførere, ulike generations, ulike resultater → sikkerhetssak.

## 4. API-side dekryptering + lukket payloadskjema per oppdragstype

DEK/KEK forlater aldri API-/kryptolaget. Claim-endepunktet: autentiser
modultoken → claim → dekrypter INTERNT → dataminimér mot
**oppdragstypens lukkede feltskjema** (`m37/oppdragsskjema.py`:
oppdragstype → eksplisitt felt-whitelist, additionalProperties-prinsipp;
handlingsprefiks gir aldri feltbredde alene) → returnér nødvendig
plaintext over godkjent transport (loopback/TLS-porten fra 005b).
Respons og plaintext logges aldri (canary-test). Eiermodulen ser aldri
nøkler eller ciphertext.

## 5. Backfill: historisk policyoppslag med hashvalidering

Rekkefølge per rad: (1) tenant/policy_id/versjon/hash fra
revisjonsloggposten → (2) slå opp NØYAKTIG historisk rad i `policyer`
(tenant, versjon, innholds_hash) → (3) re-hash lagret innhold og valider
mot loggens hash → (4) hent `maks_auto_forsok` derfra → (5) manglende
rad, hashavvik eller ugyldig policy → legacy-verdier + `manuell`.
**Ny retention-regel (tas inn i spesifikasjonen):** policyversjoner
referert av revisjonslogg eller ikke-terminale saker kan aldri slettes
— håndhevet med FK-sjekk i eventuell fremtidig opprydding, dokumentert
i policyregisterets kontrakt.

## 6. Taksonomipredikat (typefeil rettet)

```text
behandlingsbar ⇔ kategori ∈ policy.unntak.kategorier
              ∧ kategori ∈ handler.kategorier
              ∧ grunnkode ∈ handler.grunnkoder
```
Feiler ett vilkår → `manuell` eller sikkerhet per den lukkede
routingtabellen. Policyens liste sammenlignes kun med kategorier;
grunnkoder valideres kun mot handler-deklarasjonen.
