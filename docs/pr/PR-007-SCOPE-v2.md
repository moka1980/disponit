# PR-007 SCOPE v2 — skjæringsmengde-grense + aktiv autoritet (til porten)

**Draft: Claude.ai · Retter grenseformelen (union→skjæring), legger til
aktiv autoritetskontroll, og fastsetter de fem arvede sikkerhetskravene
eksplisitt. Erstatter SCOPE v1. v1–v6 + v7 pkt. 1–3 + modell (b) + form A
består.**

## 1. Grensen er SKJÆRINGSMENGDE, ikke antall distinkte verifikatorer

v1s formel («> 1 distinkt verifikator → manuell») var feil: vilkår A
betrodd `{V1,V2}`, vilkår B betrodd `{V1}` gir union {V1,V2} men V1
dekker HELE settet alene → innenfor scope. Rettelse:

```
kandidater = ⋂ betrodd_for(vilkår)  for alle vilkår ∈ krav_sett.innhentbare
```
- `kandidater = ∅` → ingen enkelt verifikator dekker settet → `manuell`,
  historikk `krever_flere_verifikatorer`, ingen fase 1.
- `kandidater ≠ ∅` → R1 kjøres med én verifikator fra `kandidater`.

**Deterministisk valg** når `|kandidater| > 1` (lukket regel, server-side):
(1) eksplisitt `verifikator_prioritet` i policyen hvis satt; (2) ellers
stabil sortering på `verifikator_id`, laveste velges. Valget er rent
deterministisk — samme sak gir samme verifikator hver gang.

**Fryses på generasjonen ved opprettelse** (kolonnelåst):
`valgt_verifikator`, `autoritetsregister_versjon` (versjon/hash av
`betrodd_for`-relasjonen brukt i valget), `krav_sett_hash`. Arbeider og
klient kan ALDRI påvirke valget — det er utledet, lagret og låst før
oppdraget bygges.

## 2. Frosset snapshot beviser forsøk — aktiv autoritet må kontrolleres

Snapshotet er evidensgrunnlag, ikke fullmaktsbevis. En tilbaketrukket
fullmakt må fanges på nåtid. To kontrollpunkter:

**Ved ingest (fase 1-kvittering):**
- `valgt_verifikator` må FORTSATT være aktiv og betrodd for HVERT vilkår
  i settet, sjekket mot AKTIV autoritet — ikke mot snapshotet.
- Tilbakekalt/endret autoritet → kvittering avvist, saken → `manuell`
  (eller sikkerhet ved mismatch mot valgt verifikator), historikk
  `autoritet_tilbakekalt_ved_ingest`.
- Snapshotets autoritetsdata er ALDRI tilstrekkelig alene.

**Før fase 2 (i den fenced claimen, før hendelsen bygges):**
- Aktiv policy lastes og valideres på nytt.
- Er kravsettet blitt STRENGERE eller endret → policyendringsregelen
  (v6 pkt. 1 / PR-006 v4): saken re-klassifiseres; nytt/strengere sett →
  `verifikasjon_retry_klar` (ny generasjon mot nytt sett) eller `manuell`.
  Fase 2 bygger ALDRI mot et utdatert kravsett.
- `fase2_id = SHA-256(tenant ‖ unntak_id ‖ 'beslutning' ‖ target_action ‖
  generation ‖ aktiv_policy_hash ‖ krav_sett_hash)` — bundet til AKTIV
  policyhash, ikke bare generasjonen. En tilbaketrukket fullmakt gir ny
  hash → gammel fase2_id kan aldri gjenbrukes.
- Motoren er siste port (v7 pkt. 1): evaluerer mot aktiv policy uansett.

## 3. Fem arvede kontrakter — eksplisitt fastsatt (ikke bare «består»)

Siden scope sier v1–v7 pkt.1–3 består, fastsettes disse uttrykkelig så
tidligere reviewfunn ikke arves ulukket:

1. **Én ytre konvolutt-signatur.** Hele settkvitteringen har ÉN ytre
   JCS-signert konvolutt (ikke per-attestasjon-signaturer som primær
   integritet). Konvolutten omslutter alle vilkårs-attestasjonene.
2. **`resultathash`** beregnes over hele den kanoniske konvolutten UTEN
   den ytre signaturen (samme mønster som attesterings-MAC fra PR-002:
   signer over innhold, hash over samme innhold, aldri over signaturen).
3. **`krav_sett` har lukket, versjonert elementskjema.** Hvert element:
   `{vilkaar, ressurs_id, innhentbar: bool}` med `additionalProperties:
   false` og `skjemaversjon`. Ukjent felt = valideringsfeil.
4. **«Ferskhet» defineres uttømmende:** gyldig signatur ∧ ikke utløpt
   (`utloper`) ∧ innen policyalder (`maks_attestasjon_alder_s`, v7 pkt. 1)
   ∧ ingen registrert revokasjon. Motoren kan IKKE oppdage endrede
   eksterne fakta uten en konkret mekanisme — derfor er
   revokasjonssjekk (pkt. 2, aktiv autoritet) en EKSPLISITT del av
   ferskhet, ikke en antakelse om at motoren «ser» virkeligheten.
5. **`permanent=true`** gir direkte `manuell` KUN hvis verifikatoren har
   særskilt autoritet til å fastslå permanent u-innhentbarhet
   (`kan_fastsla_permanent: true` i autoritetsregisteret for den
   verifikatoren). `betrodd_for` alene er IKKE nok — en verifikator
   betrodd for å attestere et vilkår er ikke nødvendigvis betrodd for å
   erklære det prinsipielt uinnhentbart. Uten særskilt autoritet
   behandles `permanent` som forbigående `negativ` → retry per budsjett.

## Bindende scope-tester (reviewens liste, vedtatt)

To mulige verifikatorer totalt, én dekker alle vilkår → R1 · ingen enkelt
verifikator dekker settet (`kandidater=∅`) → manuell, ingen oppdrag ·
flere kandidater dekker settet → samme verifikator velges deterministisk
hver kjøring · valgt verifikators autoritet trekkes tilbake før ingest →
kvittering avvist · autoritet trekkes tilbake etter ingest, før fase 2 →
ingen ny beslutning med gammel fullmakt (aktiv-policy-revalidering +
fase2_id-hash) · klient/arbeider kan ikke påvirke verifikatorvalget ·
`permanent=true` fra verifikator UTEN `kan_fastsla_permanent` → behandles
som negativ, ikke direkte manuell · `resultathash` endres ikke av å bytte
ytre signatur (hash over innhold, ikke signatur).

## Spørsmål til ChatGPT

Ingen åpne — de to fra v1 er besvart (skjæringsmengde + aktiv
autoritetskontroll). Bekreftelse av at de fem arvede kravene er korrekt
fastsatt er tilstrekkelig for GO.
