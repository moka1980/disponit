# ADR-010: Fire øyne er firmaets valg

**Status:** vedtatt 2026-11-21 av eier («to godkjennere kan vi ha som
alternativ og ikke som hindring … firmaene kan velge selv»). Gjennomført i
migrasjon 227.
**Endrer:** V6 i PR-013 («minst én attestasjon fra en ikke-forfatter»), som
til nå gjaldt alle firmaer uten unntak.

## Problemet

Kundetypen er bedrifter med 5–30 ansatte som deler en felles innboks. Mange
av dem har ÉN person som bestemmer. Kravet om at en ANNEN enn forfatteren
må godkjenne en policyendring stoppet dem før de kom i gang: fullmakten til
å sende et godkjent svar lot seg ikke gi uten en kollega med rett rolle.
Kravet er riktig for den som vil ha det. Som tvang er det et hinder.

## Vedtaket

`firma.uavhengig_godkjenning` avgjør regelen, per firma:

| Verdi | Regel |
|---|---|
| `true` | Som før: minst én attestasjon fra en som ikke skrev endringen, og to attestasjoner når endringen utvider fullmaktene. |
| `false` | Forfatteren kan attestere og aktivere alene. Én attestasjon kreves uansett risikoklasse. |

Hvem som kan endre regelen:

- **Første valg** gjøres av den som registrerer firmaet, alene. Skjemaet har
  valget avkrysset AV: et lite firma skal ikke stoppes av en kollega det
  ikke har.
- **Strengere** (`false → true`): én person alene.
- **Svakere** (`true → false`): to personer. Den ene foreslår, en ANNEN
  bekrefter innen sju døgn. Å fjerne fire øyne er selv en endring som krever
  fire øyne — ellers var regelen bare en bryter.

Eksisterende firmaer beholder `true` og regnes som å ha valgt. En tenant
uten rad i `firma`, et tomt svar og en lesefeil gir alle `true`: en usikker
lesing gjør aldri kontrollen svakere.

## Fire steder håndhever regelen, og alle leser samme kolonne

1. `policyadmin._vurder` — påkrevd antall (1 når regelen er av).
2. `policyadmin` terskelregning — `_tellende_uavhengige`.
3. `hendelse_kvorum_gate` (trigger på `policyaktivering`).
4. `aktiver_policy` — 048s kropp med én endret kontroll og utvelgelsen av
   attestant; `test_godkjenningsregel_port` holder kroppene mot hverandre.

## Evidensen lyver ikke

`policyaktivering` sa strukturelt at attestanten aldri er forfatteren
(`attestant_er_forfatter` låst til false, og FK-en mot attestasjonen bar
flagget). En aktivering gjort av én person får nå `eneattestert = true` og
`attestant_er_forfatter = true`, uten attestant B. De to flaggene er bundet
sammen av en CHECK. Hvem som gjorde hva står altså i hendelsen selv — en
eneattestert aktivering kan ikke se ut som fire øyne.

## Det som IKKE endres

- Et svar til en kunde går fortsatt aldri ut før et menneske har godkjent
  utkastet. Vedtaket gjelder hvem som kan endre FULLMAKTENE, ikke om en
  handling trenger et menneske.
- Policyer løftes fortsatt aldri med en databaseoppdatering.
- Rollene og scopene er de samme (`policy:activate`).

## Angre

Et firma setter regelen tilbake til «to personer» med ett klikk under
Kundeflate. Koden: `git revert` av PR-en; kolonnen kan stå — med `true`
overalt er oppførselen den gamle.
