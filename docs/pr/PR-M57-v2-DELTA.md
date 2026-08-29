# M-57 — v2-DELTA (M57-1 · M57-2 · M57-3 → GO)

**Draft: Claude.ai · Kun de tre funnene. Én oppdragstype, én signatar,
ingen delvis signatur, outbox uten M-6, kalender ute, malbasert
utsendelse, målt blinding, kilderefererte funn, biasmåling per
modellversjon, 5000-grensen og arkivmodellen er urørt.
Migrasjon: neste ledige mot main.**

---

## 1. M57-1 — signert listeversjon er immutabel; redigering lager ny

Porten har rett, og feilen er verdt å navngi: **jeg beskrev en
arbeidsflyt DDL-en min forbød.** FK-en gjorde allerede den signerte
listen uendelig — det er den sterke egenskapen — mens teksten beskrev
redigering på samme `liste_id`. Jeg leste min egen constraint som om den
var svakere enn den var.

Formen som lukker det, uten å svekke noe:

```sql
-- Listen er en VERSJON. Redigering lager en ny rad, aldri en UPDATE.
CREATE TABLE utsendingsliste (
  tenant TEXT NOT NULL, liste_id UUID NOT NULL,     -- versjonens egen identitet
  utkast_serie UUID NOT NULL,                       -- samme arbeid på tvers av versjoner
  forrige_liste_id UUID,                            -- lineage, NULL for første
  oppdrag_id UUID NOT NULL,
  listetype TEXT NOT NULL CHECK (listetype IN ('invitasjon','avslag')),
  malversjon TEXT NOT NULL,
  innhold_hash TEXT NOT NULL,
  antall INT NOT NULL CHECK (antall > 0),
  opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, liste_id),
  UNIQUE (tenant, liste_id, innhold_hash),
  UNIQUE (tenant, utkast_serie, innhold_hash),      -- samme innhold to ganger i serien = samme versjon
  FOREIGN KEY (tenant, forrige_liste_id) REFERENCES utsendingsliste (tenant, liste_id),
  FOREIGN KEY (tenant, oppdrag_id) REFERENCES oppdrag (tenant, oppdrag_id));

CREATE TRIGGER utsendingsliste_immutabel      -- hele raden, ikke bare hashen
  BEFORE UPDATE OR DELETE ON utsendingsliste
  FOR EACH ROW EXECUTE FUNCTION avvis_endring();
```

- **Redigering = ny `liste_id`** med `forrige_liste_id` satt. Gammel
  versjon og dens eventuelle signatur består som evidens — historikken
  viser hva som ble vist, hva som ble endret, og hva som til slutt ble
  signert.
- **Én signatur per versjon** (uendret PK på `utsendingssignatur`), og
  **høyst én signert versjon per serie** — ellers kunne to versjoner av
  samme arbeid begge vært signert:

```sql
CREATE UNIQUE INDEX en_signert_versjon_per_serie
  ON utsendingssignatur (tenant, utkast_serie) ;   -- kolonnen bæres inn i signaturen
```
  Signaturen bærer `utkast_serie` sammen med `liste_id` og
  `innhold_hash`, alle tre i FK-en mot listeversjonen — så serien i
  signaturen kan ikke være en annen enn listens.
- «Ingen delvis signatur» blir nå trivielt sant: signaturen peker på ett
  immutabelt dokument.
- Utsending skjer kun fra **den signerte versjonen** (§2). En senere
  versjon i serien kan ikke sendes uten sin egen signatur, og den kan
  ikke få en fordi serien allerede har en signert versjon — redigering
  etter utsendelse er altså ikke en vei; det er en ny liste.

## 2. M57-2 — utsendelsen bindes relasjonelt, uten å forurense outboxen

Porten har rett igjen: jeg påsto en FK som ikke sto i DDL-en, og lot
funksjonskonvensjon pluss AST-port bære en påstand om lagringen.
**Tredje gang i denne serien** at «håndhevet av lagringen» pekte på noe
annet enn en navngitt constraint.

Og porten har rett i den andre halvdelen også: ATS-spesifikke kolonner
skal ikke på den generelle outboxen. Løsningen er en smal
frigivelsesrad — modulens egen, outboxens referanse:

```sql
CREATE TABLE utsendingsfrigivelse (
  tenant TEXT NOT NULL, frigivelse_id UUID NOT NULL,
  liste_id UUID NOT NULL, innhold_hash TEXT NOT NULL, utkast_serie UUID NOT NULL,
  mottaker_ref TEXT NOT NULL,          -- én rad per utsending i listen
  frigitt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, frigivelse_id),
  -- signaturen MÅ finnes, og gjelde nøyaktig denne versjonen
  FOREIGN KEY (tenant, liste_id, innhold_hash, utkast_serie)
    REFERENCES utsendingssignatur (tenant, liste_id, innhold_hash, utkast_serie),
  UNIQUE (tenant, liste_id, mottaker_ref));    -- én utsending per mottaker per liste
-- append-only; INSERT kun til utsendingsfunksjonens eier
```
- **Outboxen refererer frigivelsen**, ikke omvendt: outbox-radens
  eksisterende referansefelt peker på `frigivelse_id` (FK der skjemaet
  tillater det; ellers bærer outbox-raden frigivelsesreferansen som
  påkrevd kolonne med FK — én generisk kolonne, ikke ATS-semantikk).
- **Kjeden blir:** outbox → frigivelse → signatur → listeversjon →
  innhold. Hvert ledd en navngitt constraint. Ingen gyldig signatur ⇒
  ingen frigivelse ⇒ ingen representerbar ATS-utsendelse — bevisbart med
  **direkte DML**, ikke bare gjennom funksjonen.
- AST-porten beholdes som andre lag, men den bærer ikke lenger
  påstanden alene.

## 3. M57-3 — én kandidatdatagrense for TTL

Riktig og viktig: original-PDF-en er den minst interessante
persondatabæreren. Funn med sitater, rangeringsbegrunnelser,
intervjuspørsmål og flettefelt bærer alle søkerdata.

**Kandidatdatagrensen** defineres eksplisitt og omfatter alle lagre
modulen skriver persondata til:

| Lager | Ved TTL |
|---|---|
| Originaldokument (opplastet fil) | payload slettet |
| Parset tekst / mellomlager | payload slettet |
| Evalueringsartefakt (funn, sitater, rangering, begrunnelser) | payload slettet |
| Intervjuspørsmål | payload slettet |
| Utsendingsliste og frigivelse: `mottaker_ref`, flettefeltverdier | payload/verdier slettet |
| Blindingens av-maskeringstabell | slettet |

**Det som består er minimal, ikke-reversibel evidens:** rad-ID,
tidsstempler, `slettet_ts`, innholdshash, antall og statuser.
Spesifikasjonen **påstår ikke at hashen er anonym** — den sier at
payload er slettet og at minimal revisjonsevidens består. Det er
formuleringen som brukes i produktteksten også.

**Porten er en søkeport, ikke en radtelling:** en kjent
fixture-streng (kandidatens navn, e-post, en unik setning fra søknaden)
plantes i alle seks lagre, reaperen kjøres, og strengen søkes opp
**på tvers av alle M-57-payloadlagre** — inkludert utsendingsrader og
eventuelle indekser/materialiserte former. Null treff, ellers rødt.

## 4. Porter (tillegg)

**M57-1.** UPDATE på signert listeversjon → trigger-avvist · Ny versjon
uten `forrige_liste_id` i eksisterende serie → avvist · To signerte
versjoner i samme serie → unikindeks-avvist · Signatur med `utkast_serie`
≠ listens → FK-avvist · Samtidig rediger/signér: én signert versjon,
den andre redigeringen blir ny usignert versjon.

**M57-2.** Direkte DML: frigivelse uten signatur → FK-avvist ·
outbox-rad uten frigivelsesreferanse → avvist · Frigivelse mot annen
versjon enn den signerte → FK-avvist · To frigivelser for samme mottaker
i samme liste → unikavvist · Outboxen har ingen ATS-spesifikke kolonner
(statisk).

**M57-3.** Fixture-strengen finnes i alle seks lagre før reaping og i
null etter · TTL-reaping av ett lager alene → rødt (grensen er
kollektiv) · Minimal evidens består: rad, hash, `slettet_ts`.

## 5. Evidensgrense — tillegg

`liste.signert_versjon_endret = 0` (sikkerhetsinvariant) ·
`serie.to_signerte_versjoner = 0` (sikkerhetsinvariant) ·
`utsending.uten_signaturkjede = 0` (sikkerhetsinvariant, målt med
direkte DML) · `outbox.ats_spesifikke_kolonner = 0` ·
`ttl.persondata_funnet_etter_reaping = 0` (sikkerhetsinvariant) ·
`ttl.lager_utenfor_kandidatgrensen = 0`.

---

```
NÅ:    v2-deltaet gjennom porten — ChatGPT (Eier relayer)
       — docs/pr/PR-M57-v2-DELTA.md
NESTE: Ved GO: klarsignal med tallfestede grenser (slettespenn,
       fristvalg, arkivgrenser) og evidensgrense `m57-v1`
       — Claude.ai → Claude Code
```
