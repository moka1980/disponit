# ADR-002: Sjekklisten for moduler uten egen beslutningsvei

**Status:** vedtatt 2026-11-19 (Claude Code, med eiers fullmakt «du kan
bestemme arkitektavklaringen»).
**Gjelder:** M-03, M-04, M-05, M-08, M-09, M-12, M-16, og enhver senere
modul av samme form.

## Problemet

Sjekklisten (`staging_sjekkliste`) er lukket og lik for alle moduler.
Den ble formet av moduler med en egen kjørevei: en dør som skriver til
evidenskjeden, en sveip som feiler inn i unntakskøen, en release som
kan rulles tilbake. Sju manifester sier at det trengs en arkitektdom
før noen kan avgjøre hva punktene betyr for en modul som bare leser,
bare måler eller bare registrerer. Til nå har de stått på `nei`, slik
at ingen skulle låne en tolkning.

Det har en kostnad. M-30, M-50, M-51 og M-53 er ferdig målt, men kan
ikke settes i drift, fordi en aktiv modul ikke kan avhenge av en
inaktiv (M-04 og M-16).

## Kartlegging (19.11.2026, målt i koden, ikke i manifestene)

Fire av manifestene sier «ingen kode finnes». Det stemmer ikke lenger:

| modul | det som faktisk finnes | skriver evidens? | klasse |
|---|---|---|---|
| M-12 tilgang | tenantdører, `tilgangsfunn`, sveip og timer | **ja** (`m12_evidens`) | A |
| M-09 kunnskap | tenantdører, `begrepsfunn`, sveip og timer | nei | A |
| M-04 dataforvalter | globalt register seedet i migrasjon, kryss-tenant måler (`disponit-lagermaaling`) | nei, har ingen dør | B |
| M-03 datakvalitet | regler seedet i migrasjon, kryss-tenant profilering (`disponit-kvalitetsprofil`) | nei, har ingen dør | B |
| M-05 dokumentmal | tenantdører (opprett, publiser, trekk tilbake), ingen funn | nei | C |
| M-08 kalender | tenantdører (slot, token, valg), ingen funn | nei | C |
| M-16 nøkkeltall | rent leseendepunkt i kjernen | nei, skriver ingenting | D |

## Dommen

**D0. Punktene beholder navn og hensikt; bare målemetoden følger
klassen.** Klassen avgjøres av hva koden faktisk gjør, aldri av hva som
er lettest å måle. En modul som får en ny skrivevei, skifter klasse, og
punktene må måles på nytt.

**D1. Revisjonslogg korrekt betyr at enhver tilstandsendring er
sporbar til en aktør, i kanalen som eier endringen.**

- *Tenantdører (klasse A og C):* hver dør som endrer tenanttilstand
  skriver én hendelse i `revisjonslogg` med aktør og egen `input_hash`
  (M-12-formen: `m<NN>_evidens`). **Ingen fritak.** At et register er
  append-only i sitt eget lager er ikke en revisjonslogg: det viser
  *hva* som står der, ikke *hvem som satte det dit*. M-09, M-05 og M-08
  får derfor evidensskriving i dørene sine før de måles.
- *Plattformregister og måler (klasse B):* plattformdommer (hvilke
  lagre og regler som finnes, og med hvilken frist) endres bare i
  migrasjon og er sporet i git. Det er plattformens revisjonslogg for
  plattformdommer. Punktet måles som **målt fravær**: (a) katalogbevis
  for at ingen rolle utenom migratoren har skriverett på registeret,
  (b) at en full målekjøring gir null nye rader i `revisjonslogg` og
  null endrede rader utenfor modulens egne måletabeller.
- *Lesemodul (klasse D):* målt fravær alene. En full kjøring av
  endepunktet over et kjent datasett gir null endringer i noen tabell
  (`pg_stat_xact_*`) og null nye rader i `revisjonslogg`. I tillegg
  kreves katalogbevis for at runtime-rollen ikke har skriverett
  gjennom modulens funksjoner.

«Målt fravær» er en måling, ikke et fritak. Et artefakt som sier
`nye_revisjonsrader: 0` uten at kjøringen beviselig nådde
modulkoden (positiv kontroll: svaret inneholder fasitens tall), er ugyldig.

**D2. Feilinjisering til unntakskø betyr at en feil aldri blir stille
eller blir lest som et gyldig null.**

- *Klasse A:* den eksisterende generiske formen.
- *Klasse B:* et lager eller en regel som ikke kan måles, blir et funn
  (`umaalbar` eller tilsvarende), **aldri** null. En jobb som dør, gir
  alarm etter den eksisterende formen (to sammenhengende feil).
  Injeksjonen er en ekte driftsfeil: tilbaketrukket grant eller
  `statement_timeout`, aldri en bryter rigget inne i modulen.
- *Klasse C:* injeksjonen er et dørkall som bryter en vakt
  (materialitetskonflikt, ugyldig tilstandsovergang). Kallet skal
  avvises med typet feil, uten delvis skriving og uten evidensrad.
- *Klasse D:* injisert feil i lesingen (tilbaketrukket grant eller
  `statement_timeout` på en av de underliggende spørringene). Svaret
  skal bli en typet feil, **aldri delvise tall**. En lesemodul har ingen
  unntakskø. Punktets hensikt («feilen når et sted der den blir sett»)
  oppfylles av feilsvaret og den strukturerte loggen.

**D3. Rollback testet betyr at modulens oppførsel er målt under en
rulling, ikke lånt fra en annen moduls drill.**

Alle sju moduler bor i kjernen og har ingen egen release. Rollback måles
derfor ved å rulle **kjernen** til en forgjenger med en annen
kjernedigest, og tilbake. Modulens egen oppførsel måles før, under og
etter: samme fasitsvar, ingen tapte rader, og jobben går rent etter at
kjernen er rullet fram igjen. Kjernedrillens `bestatt` alene er lånt
(m37-lærdommen). Artefaktet må inneholde modulens egne målinger.

**D4. Ytelse og syntetisk datasett** følger den eksisterende formen
(grense pinnet i `KRAVGRENSER` før målingen, fasitsett med kjent svar).
For klasse C er fasiten en kjent sekvens av dørkall med kjent
sluttilstand, re-regnet av radene. For klasse D er det et kjent
beslutningsdatasett med kjente nøkkeltall.

**D5. Rekkefølgen er etter verdi.** Den bestemmes av hvor mange andre
moduler som venter:

1. M-12 (klasse A, generisk, allerede evidensskrivende)
2. M-04 (klasse B; åpner for M-30, deretter M-50 og M-53)
3. M-16 (klasse D; åpner for M-51)
4. M-09 (klasse A etter evidensfiksen)
5. M-03 (klasse B)
6. M-05 og M-08 (klasse C etter evidensfiksen)

## Det dommen ikke gjør

- Den flipper ingenting. Hvert punkt står `nei` til artefaktet er målt
  på verten og bundet i manifestet.
- Den endrer ikke avhengighetsregelen. M-30 står fortsatt med M-04 som
  avhengighet, fordi 093-registeret er M-04s kode og M-30 leser det.
- Den svekker ingen eksisterende grense. Ingen `min_evidens` senkes for
  å slippe en modul gjennom. Klasse B og D får egne grenser med
  `maks_nye_revisjonsrader: 0` **og** et positivt kontrollkrav.
