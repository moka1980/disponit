# PR-007 SCOPE v1 — R1 dekker enkelt-verifikator-sett (til ChatGPT-porten)

**Draft: Claude.ai · Arkitektbeslutning (Eier godkjent): innsnevre
R1-scope for v1 for å lande en implementerbar spesifikasjon nå.
Erstatter v7 pkt. 4 og HELE v8. v1–v6 + v7 pkt. 1–3 står uendret.**

## Beslutningen

R1-tofaseprotokollen håndterer i v1 KUN saker der hele det manglende,
innhentbare vilkårssettet dekkes av ÉN verifikatormodul. Saker som
krever FLERE ulike verifikatorer for å komplettere settet går til
`manuell` — fail-closed, ikke feil.

**Hva som faller bort:** v7 pkt. 4 (per-vilkår-verifikator med
del-akkumulering) og v8 (sub-generasjonsmodellen `verifikasjonsdel`).
Hele fler-part-akkumuleringen utgår fra v1. Én kvittering bærer hele
settet (v5), signert av én verifikator, verifisert atomisk (v6) — ingen
delbevis, ingen `verifikasjonsdel`-tabell, ingen hengende delsett.

**Hva som består uendret:** modell (b), form A (sett som enhet, v5),
frosset `krav_sett` (v6 pkt. 1), atomisk sett-ingest (v6 pkt. 2),
`krav_sett_hash`-binding (v6 pkt. 3), motor-som-autoritativ ferskhet
(v7 pkt. 1), `permanent`-flagg for ikke-innhentbar (v7 pkt. 2), monoton
avslutning (v7 pkt. 3), og hele v1–v4-maskineriet.

## Grensen — presist definert (fail-closed)

Klassifisereren utleder ved første behandling, fra sakens frosne
`krav_sett` og policyens `verifikatorer.betrodd_for` (finnes fra PR-002),
settet av verifikatorer som kreves for å attestere hele det innhentbare
settet:

- **Nøyaktig én verifikator dekker alle innhentbare vilkår** → R1 fase 1,
  som spesifisert i v1–v7. Verifikatoren utpekes i verifikasjonsoppdraget.
- **To eller flere verifikatorer kreves** → saken → `manuell` UMIDDELBART,
  historikk `krever_flere_verifikatorer`, ingen fase 1. Metadata logger
  hvilke verifikatorer settet ville krevd (for fremtidig prioritering av
  fler-verifikator-PR-en).
- **Ikke-innhentbart vilkår i settet** → `manuell` (v5 pkt. 1, uendret),
  vurderes før verifikator-tellingen.

Grensen er ren og maskinell: antall distinkte verifikatorer i
`{betrodd_for[vilkår] : vilkår ∈ krav_sett.innhentbare}`. Er det > 1 →
manuell. Ingen gjetting, ingen delvis behandling.

## Verifikatorbinding i den enkle formen (v7 pkt. 4 erstattet)

Selv med én verifikator består per-vilkår-tillitssjekken fra v7 pkt. 4s
sikkerhetskjerne: ingest verifiserer at den ene signerende verifikatoren
er i `betrodd_for` for HVERT vilkår i settet. Er den ikke betrodd for
ett vilkår → dette er per definisjon en fler-verifikator-sak → skulle
vært rutet til `manuell` ved klassifisering. Havner en slik kvittering
likevel i ingest (policy endret etter klassifisering, e.l.) → avvist,
sikkerhetslogg, saken → `manuell`. Dobbelt fail-closed.

## Fremtidig PR (deklarert, ikke planlagt nå)

«R1 fler-verifikator-sett» spesifiseres som egen PR NÅR: (a) M-1 er i
produksjon, og (b) måledata viser hvor ofte fler-verifikator-saker
faktisk oppstår. v8s sub-generasjonsmodell (`verifikasjonsdel`, monoton
hovedgenerasjon, timeout på generasjonsnivå) er det arkitektoniske
utgangspunktet — arbeidet er ikke tapt, kun utsatt til det er
etterspørselsdata å dimensjonere det mot. Dette følger prosjektets
kjerneprinsipp: bygg for det virkeligheten viser behov for, ikke for det
vi antar.

## Konsekvens for evidensporten

Feilinjiserings-artefaktet blir enklere og NÅS RASKERE: injiser
`manglende_data`/`attestasjon_mangler` der ett eller flere vilkår deles
av samme verifikator → én signert kvittering → fase 2 → `løst`.
`lost_andel av reparerbare = 1.0` oppnås. Negativ avgrensning som må
bevises: en konstruert fler-verifikator-sak → `manuell` umiddelbart,
ingen fase 1 (beviser grensen).

## Spørsmål til ChatGPT

1. Er verifikator-tellingen (`> 1 distinkt verifikator → manuell`) en
   ren, komplett grense — eller finnes en degenerert sakstype (f.eks.
   ett vilkår med FLERE mulige betrodde verifikatorer) der «antall
   distinkte» er tvetydig og trenger en tie-break-regel?
2. Er det trygt at grensen evalueres på frosset `krav_sett` +
   klassifiseringstidspunktets policy, gitt at ingest re-sjekker
   betrodd-relasjonen som dobbelt fail-closed?
