# PR-007 SPESIFIKASJON v5 — DELTA (fler-vilkårs-form → GO)

**Draft: Claude.ai · Modell (b) + v1–v4 står. Løser fler-vilkårs-hullet
Claude Code målte. Én form valgt (A), avgrenset ærlig.**

## 0. Beslutning: form A, og hvorfor ikke B eller C

Funnet: v1 §0 sa «det manglende vilkåret» (entall); v3 klassifiserte per
Grunn-kode uten å definere hva som skjer når FLERE påkrevde vilkår mangler
i samme sak. Firekjeden stopper fordi fase 2 sender én ny beslutning, men
bare ETT vilkår er blitt attestert — motoren finner fortsatt de andre
umulige.

- **Form B** (én generasjon per vilkår, flere parallelle) avvises: den
  konvergerer mot en haug `manuell`-saker, ikke mot `løst`, og bryter
  «én aktiv reparasjon per sak»-invarianten fra PR-006. Claude Codes
  vurdering deles.
- **Form C** (lagre originalens attestasjoner) avvises: jeg avviste
  original-lagring allerede i v1 §0, og gjør det igjen — det reverserer
  dataminimeringen for et problem A løser uten.
- **Form A** valgt: fase 1 verifiserer ALLE påkrevde vilkår for saken som
  ETT sett, i én generasjon. Fase 2 bygger den nye hendelsen med hele
  settet av verifiserte attestasjoner. Konvergerer mot `løst`, bevarer
  én-aktiv-reparasjon, ingen ny datalagring.

## 1. Vilkårssettet er saksbundet, ikke enkeltvilkår

Klassifisereren bestemmer ved første behandling det KOMPLETTE settet av
påkrevde vilkår for sakens handling (fra policyens handling-definisjon —
finnes allerede) og deler i to:

- **innhentbare** (mangler attestasjon / utløpt attestasjon, ressurs-
  bundet, autoritativt verifiserbare) → fase 1 skal dekke ALLE disse.
- **ikke-innhentbare** (manglende forretningsverdi, f.eks. `belop`) →
  saken går `manuell` UMIDDELBART, uten fase 1. En sak der ett vilkår er
  prinsipielt ikke-innhentbart er ikke reparerbar via R1, og skal ikke
  starte en verifikasjonsrunde som uansett ender manuelt.

Bare saker der HELE det manglende settet er innhentbart går til fase 1.
Dette er fail-closed og unngår B-konvergensen mot manuelle saker.

## 2. Generasjonsnøkkel: `vilkaar` → fast sett-sentinel

`verifikasjonsgenerasjon`- og `verifikasjonsbevis`-nøklene bruker i dag
`(tenant, unntak_id, vilkaar, generation, ...)`. Form A trenger én
generasjon som dekker settet, ikke per vilkår. To muligheter — jeg velger
den som bevarer skjemaet fra v3/v4:

- `vilkaar`-kolonnen settes til den faste sentinelen `'*sett*'` for
  R1-generasjoner (ett verifikasjonsløp per sak+generasjon, ikke per
  vilkår). Delindeksen `en_aktiv_generasjon_per_sak_vilkaar` gir dermed
  automatisk «én aktiv R1-generasjon per sak» — nøyaktig
  én-aktiv-reparasjon-invarianten, uten skjemaendring.
- **Ett bevis per vilkår i settet** lagres fortsatt separat i
  `verifikasjonsbevis`, men nå med det FAKTISKE vilkårsnavnet i en egen
  kolonne `bevis_vilkaar`, mens nøkkelkolonnen `vilkaar='*sett*'` binder
  dem til samme generasjon. Kompositt-nøkkelen blir
  `(tenant, unntak_id, vilkaar='*sett*', generation, id)` — v4s FK står.
  `bevis_vilkaar` er UNIQUE innen `(tenant, unntak_id, generation)` så
  samme vilkår ikke kan dobbeltbevises i én generasjon.

## 3. Fase 1 for et sett: ett verifikasjonsoppdrag, flere krav

Verifikasjonsoppdraget (`vilkaar`-feltet fra v1 pkt. 4) utvides til
`vilkaar_sett: [..]` — den lukkede feltlisten får en array, ikke et
skalarfelt (additionalProperties: false bevart). Verifikatormodulen
attesterer hvert innhentbart vilkår og returnerer ÉN
`verifikasjonskvittering_v1` som bærer et SETT av signerte attestasjoner
(kvitteringsskjemaet: `attestasjoner: [..]`, hver med sitt vilkår,
ressurs_id, jti, signatur — lukket).

**Delvis resultat er ikke positivt.** `registrer_verifikasjonsbevis`
setter generasjon `positiv` KUN når HELE settet er attestert gyldig og
ikke-utløpt. Mangler ett → generasjon `negativ` (→ retry-klar eller
manuell per v4 pkt. 1). Ingen «noen vilkår løst»-halvtilstand.

## 4. Fase 2 bygger med hele settet

Fase 2s nye hendelse = minimert payload + ALLE verifiserte attestasjoner
fra generasjonens bevis (join på `(tenant, unntak_id, '*sett*',
generation)`). Motoren evaluerer med komplett attestasjonssett → ingen
gjenværende `attestasjon_mangler`. `fase2_id` binder til generasjonen
(ikke enkeltbevis): `SHA-256(tenant ‖ unntak_id ‖ 'beslutning' ‖
target_action ‖ generation)` — stabil når settet er komplett.

## 5. Retry-budsjett: per RUNDE, ikke per vilkår (Claude Codes spørsmål)

`verification_generation` teller RUNDER over hele settet.
`maks_auto_forsok_snapshot` begrenser antall sett-runder (v4 pkt. 5
uendret: første generation=1, ny når `generation <
maks_auto_forsok_snapshot`). Et vilkår som gjentatte ganger ikke kan
attesteres bruker altså budsjett på vegne av hele settet — riktig, fordi
saken ikke kan løses uten det, og vi vil ikke bruke N×vilkår forsøk på en
sak som ikke konvergerer. Etter budsjett → `manuell` med historikk som
angir hvilke vilkår som gjensto uattestert (metadata, ikke verdier).

## 6. De to avvikene Claude Code fant

Briefens to avvik fra firekjeden tas inn (uten å ha selve teksten her,
dekkes de av formen): (a) fase-overgangen må sjekke komplett sett før
`verifikasjon_klar`, ikke første bevis — dekket av pkt. 3
«delvis er ikke positivt». (b) klassifisereren må kjøre på HELE settet
ved første behandling, ikke oppdage vilkår ett om gangen — dekket av
pkt. 1. Hvis briefens målte avvik peker på noe utover dette, flagg det
mot denne formen før implementering.

## Bindende tester (i tillegg til v4s ti porter)

Sak med to manglende innhentbare vilkår → ett fase-1-oppdrag, begge
attestert → `positiv` → fase 2 → `løst`. Sak med to manglende, ett
ikke-innhentbart → `manuell` umiddelbart, ingen fase 1. Sak med to
manglende innhentbare, verifikator attesterer bare ett → `negativ` →
retry/manuell, aldri delvis positiv. Samme vilkår kan ikke dobbeltbevises
i én generasjon (UNIQUE). Retry-budsjett teller runder: sett-sak med
maks=2 gir maks 2 verifikasjonsoppdrag totalt, ikke 2 per vilkår.
