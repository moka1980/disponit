# ADR-007: Klasse H — plattformport

**Status:** vedtatt 2026-11-19 (Claude Code, med eiers fullmakt «du kan
bestemme arkitektavklaringen»).
**Gjelder:** M-31 (agentkvalitet og modellstyring).
**Bygger på:** ADR-002 (D0–D5).

## Problemet

M-31 er golden-sett-porten på releasebyttene. Modulen består av:

- **tre dører** som bare plattformadministratoren når (registrer sett, sett
  krav, registrer kjøring);
- **en port inne i `bytt_release`**;
- **en egen append-only hendelsesstrøm** (`modellstyring_hendelse`).

Registeret er globalt, uten tenant og uten RLS. Manifestet sier selv at
det trengs en avklaring av hva feilinjisering og rollback betyr for en
portmodul. Manifestet er også utdatert på ett punkt: det sier at
golden-settet ikke finnes. Målt 19.11.2026 har driftsbasen to
settversjoner registrert og én bestått evaluering.

## Dommen

**H0. Klasse H er «plattformport».** Én produsent
(`deploy/staging/m31-lest.py`) måler i den isolerte basen på 55432, fordi
riggen skriver plattformregistre. Den bruker testsuitens egne hjelpere, og
de står i bevisroten sammen med golden-settene.

**H1. Syntetisk datasett.** To ting måles:

- **Datasettets identitet.** Den kanoniske hashen regnes av repoets filer
  og må være lik hashen driftsbasen bærer. Hashene er pinnet i
  `M31_DRIFTSHASHER`. «Samme datasett lokalt og på staging» er dermed en
  likhet mellom to målte tall.
- **Kjent dørsekvens.** Settet registreres gjennom den ekte CLI-en.
  Sekvensen gir kjente portutfall: bytte uten krav går, bytte uten bestått
  kjøring nektes, en bestått kjøring bærer byttet, og et strammere krav gjør
  den gamle dommen verdiløs.

**H2. Revisjonslogg.** Modulens egen hendelsesstrøm er kanalen som eier
endringen (ADR-002 D1). Hver dørovergang står som nøyaktig én hendelse med
aktør, og runtime-rollen har ingen skriverett på registeret.

**H3. Feilinjisering.** Porten feiler synkront og lukket mot kalleren. Fem
brudd måles:

- bytte uten bestått kjøring;
- kjøring mot ukjent sett;
- delvis kjøring;
- brudd på settets immutabilitet;
- hash-avvik, som skal stoppe **før første modellkall**.

Hvert brudd skal avvises typet og uten spor. En portmodul har ingen
unntakskø. Punktets hensikt oppfylles av at byttet ikke skjer og at
kalleren får feilen.

**H4. Rollback.** Kjernen rulles. Forgjengeren og kandidaten må regne samme
hash og samme score av samme sett, og porten må svare likt etterpå. At
porten nekter før byttet, er modulens egen rollback-semantikk. Den måles i
H1 og H3.

**H5. Ytelse.** En ekte evaluering kjøres mot vertens modell. Grensen er
p95 per eksempel ≤ 60 s og ingen modellfeil. Driftsbasens tre tidligere
kjøringer målte 17–76 s. Kjøringen registreres i den isolerte basen, slik
at målingen ikke legger en rad i driftsregisteret.

**Tillegg etter første måling (19.11.2026).** Den første evalueringen traff
en kald modell. Lastingen tok over to minutter, og det første eksemplet ble
en modellfeil. De 23 andre besto. Punktet måler evalueringens ytelse, ikke
modellasting. Produsenten laster derfor modellen med ett eksplisitt kall
først, og fører tiden i artefaktet (`oppvarming_ms`). Funnet står: **en
evaluering mot en kald modell kan felle første eksempel.** Runbooken for
modellbytte bør varme modellen før `kjor-m31-evaluering.py`.
