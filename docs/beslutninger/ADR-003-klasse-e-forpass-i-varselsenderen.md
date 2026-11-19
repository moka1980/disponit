# ADR-003: Klasse E — forpass i varselsenderen

**Status:** vedtatt 2026-11-19 (Claude Code, med eiers fullmakt «du kan
bestemme arkitektavklaringen»).
**Gjelder:** M-21 (fristvarsler) og M-22 (utløpsvarsler). Formen er åpen
for M-10 og M-11 når de måles, men de er plattformskopet og hører til en
egen dom (se «Det dommen ikke gjør»).
**Bygger på:** ADR-002 (D0–D5 gjelder uendret; denne ADR-en legger til én
klasse).

## Problemet

ADR-002 kartla sju moduler og fant fire former (A–D). M-21 og M-22 passer
ingen av dem:

- de har **tenantdører som skriver evidens** (`m21_evidens`,
  `m22_evidens`) — som klasse A og C;
- men de har **ingen funn** som åpnes og lukkes, og derfor ingen
  `min_sveip2_lukkede` å måle — så den generiske sveiplesten (klasse A)
  kan ikke brukes;
- og de har **ingen egen kjørevei**: arbeidet gjøres av et FORPASS som
  `platform/drift/varselsender.py` kjører før den ordinære sendingen.
  Det forpasset skriver et append-only anker per (subjekt, varslingspunkt,
  forekomst) i samme transaksjon som varselet det svarer til.

## Dommen

**E0. Klasse E er «tenantregister med forpass i varselsenderen».** Alle
fire punktene måles i den isolerte basen på 55432, bygget fra commiten som
måles, av én produsent (`deploy/staging/forpass-lest.py --modul X`).
Forpasset kjøres gjennom den **ekte senderen** (`varselsender.kjor`) som
`disponit_varselsender`, med en oppsamlende `send` i stedet for SMTP. Ingen
e-post forlater verten, og ingen rigg skriver i driftsbasen. Årsaken er at
riggens varsler ellers ville gått til senderen i drift.

**E1. Syntetisk datasett og revisjonslogg (fasit).** Et kjent subjektsett i
to tenanter, med kjent antall ankre per subjekt etter første kjøring. Tallet
regnes på nytt fra ankrene. Settet har en negativ kontroll: minst to
subjekter som ikke skal varsles (for langt fram, lukket eller avsluttet).
Hvert anker peker på ett varsel til subjektets eier, og varselet har
e-poststatus etter eierens kanalvalg. Mellom kjøringene kvitteres eller
fornyes ett subjekt. Andre kjøring skal ikke køe noe nytt. Evidensen telles
per handling, med aktør og egen `input_hash`, som i klasse C.

**E2. Feilinjisering.** Invarianten er modulens egen
(`forpass_stanset_ordinaer_sending`). Den måles med to ekte driftsfeil,
aldri med en bryter rigget inne i modulen:

1. tilbaketrukket `EXECUTE` på forpasset for senderens rolle;
2. et låst register, der senderen har `lock_timeout`.

Under feilen skal fire ting gjelde:

- den ordinære sendingen går som før;
- feilen telles separat (`forpass_feil = 1`, og bare den);
- forpasset skriver ingen ankre;
- **inngangspunktet avslutter med kode ≠ 0.**

Det siste punktet er nytt. Før sto en feilet sveip bare som et tall i en
journallinje. Nå gjør `kjor_varselsender` uniten `failed` (kode 2) når et
forpass feilet, og det er den tilstanden driften faktisk ser etter. Etter
reparasjonen skal forpasset gå rent og nå fasitens ankre.

**E3. Rollback.** Kjernen rulles som i ADR-002 D3. Forgjengerens sender og
kandidatens sender kjøres hver på sitt like sett, og må gi like ankre per
subjekt, lik fasiten. Kandidaten kjøres deretter over forgjengerens sett og
skal ikke køe noe nytt. Det betyr at det ikke kommer dobbeltvarsler over en
rulling.

**E4. Ytelse.** Forpasset måles alene med et fullt tak (100 forfalne
subjekter) per kjøring, fem ganger. Grensen er pinnet før målingen:
`maks_sekunder = 5.0`. Forpasset deler senderens fristvindu (240 s) med den
ordinære sendingen, og et fullt tak skal bruke en brøkdel av det.

**E5. Suiten** måles som for alle andre (`registrer_suitegrense`).

## Det dommen ikke gjør

- Den flipper ingenting. Hvert punkt står `nei` til artefaktet er målt på
  verten og bundet i manifestet.
- M-10 og M-11 er også forpass i senderen, men de er plattformskopet: de
  har ingen tenant og ingen dør som skriver evidens, og de varsler
  plattformens egne administratorer. De måles i en egen runde. Den
  sannsynlige formen er klasse B (plattformregister og måler) med E2 som
  feilinjisering.
