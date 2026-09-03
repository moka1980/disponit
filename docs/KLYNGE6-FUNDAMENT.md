# Klynge 6 — «de fem som finner noe, og ikke handler på det»

**M-46** anbuds- og konkurransevakt, **M-48** foretaks- og kredittvakt,
**M-49** sanksjons- og hvitvaskingsvakt, **M-51** tilskudds- og
støtteagent, **M-55** merkevare- og IP-overvåker.

Alle fem er `ekstern_lesing` i spesifikasjonen (v9), og alle fem er
ubygde. Dette dokumentet fester dommen, migrasjonsnumrene, rollene og
grensene FØR koden (§0-regelen).

## Hva som er annerledes fra klynge 5

De fire foregående klyngene ble drevet av **bransjemalene**: en mal
navnga en modul som verifikator eller aktør, og gapet var noe vi alt
hadde sendt ut til kunder. `VENTENDE` er nå tom, og porten som telte
gapet holder det lukket.

**Ingen bransjemal navngir disse fem.** Driveren er eierens egen
prioritering i spesifikasjonen, ikke et løfte vi har gitt. Det gjør
tilbakeholdelsen mindre presserende og friheten større — og det er en
grunn til å være ekstra tydelig på hva v1 faktisk er.

## Dommen: M-19s begrunnelse gjelder IKKE her

Den nærliggende feilen ville vært å gjenbruke M-19s dom mekanisk. M-19
(112) slår ingenting opp, og begrunnelsen var todelt:

1. et oppslag mot et adresseregister er en **utgående kanal med
   personopplysninger i** — kundens navn og adresse ut av huset, til en
   tredjepart uten databehandleravtale, og
2. svaret ville uansett vært **feil vare**: at en adresse finnes i et
   register sier ikke at pakken kommer fram.

**Ingen av de to holder for alle fem her.** Sanksjonslister lastes ned
og matches lokalt — man forteller ingen hvem man sjekker.
Doffin og TED er offentlige kunngjøringsstrømmer. Ordningskataloger er
offentlige. For M-55 sender man sine EGNE merkevarenavn, ikke kundens
data. Bare M-48 har M-19s form: et oppslag mot et kredittregister
sender motpartens organisasjonsnummer ut og er i seg selv en
behandling av persondata — spesifikasjonens egen vakt sier det.

**Å kopiere dommen ville vært å bygge fem tynne moduler av en grunn som
ikke gjelder.**

## Dommen som gjelder: den unødvendige forespørselen er skaden

Plattformen har alt en doktrine for denne klassen, i
`oppdragskontrakt.py`:

> «`ekstern_lesing` er klassen der den unødvendige forespørselen ER
> skaden.»

Og den har maskineriet: `ekstern_lesing`-oppdragstyper med
målautorisasjon (`krever_malautorisasjon`), egress- og robots-vakt
(014b), frekvenshåndheving i aktiveringsporten, og utførelsesfrist på
oppdragsraden. `m_wcag_audit` leser eksternt i dag, innenfor det.

**v1 GJØR LIKEVEL INGEN UTGÅENDE FORESPØRSEL**, og grunnen er ikke at
forespørselen er farlig i seg selv:

* Å koble fem integrasjoner — Doffin, TED, Brreg, kredittleverandør,
  OFAC/EU/FN, søke-API-er — inn i oppdragskontrakten er fem separate
  arbeider med fem sett hemmeligheter, og hver av dem har sin egen
  målautorisasjon å få på plass.
* Doktrinen sier at den unødvendige forespørselen er skaden. **Vi kan
  ikke ennå si hvilke forespørsler som er nødvendige**, fordi vi ikke
  har målt hva vi ville spurt om. Registeret er den målingen.
* Register-først, som i alle klyngene før: bygg målingen, så har
  fullmakten grunn å stå på.

Det er en **scope-beslutning med en frist**, ikke en dom om at
forespørselen er gal. Den dagen en integrasjon kobles på, er porten
`modulen_hentet_eksternt` det som må endres bevisst — og
oppdragskontrakten er stedet det skal skje, ikke en `httpx`-import i
en modulfil.

## Den delte dommen: fem som finner, og ingen som handler

Spesifikasjonens egne vakter sier det samme fem ganger:

| Modul | Vakten sier |
|---|---|
| M-46 | «Sender aldri inn tilbud» |
| M-48 | «Setter aldri kredittgrensen selv» |
| M-49 | «Treff blokkerer fail-closed og løses kun av menneske» |
| M-51 | «Sender aldri inn søknad» |
| M-55 | «Sender aldri juridiske krav eller klager» |

Fire av fem holder tilbake en **utgående handling**. M-49 er unntaket,
og det er det interessante.

### M-49 er klyngens vanskeligste

Den er den eneste der spesifikasjonen vil at modulen SKAL handle:
stoppe handel ved treff, fail-closed. Og samtidig: «navnelikhet er
aldri automatisk avfeid».

De to sammen betyr at treffene blir MANGE — navnelikhet mot
sanksjonslister gir store mengder kandidater — og at ingen av dem kan
lukkes maskinelt. **En modul som blokkerte automatisk på det
grunnlaget ville stanset lovlig handel fra første natt**, uten at noen
hadde målt hvor ofte den tar feil.

v1 blokkerer derfor ikke. Den registrerer at kontrollen ble gjort, mot
HVILKEN listeversjon, med hvilket matchgrunnlag — og gjør et uavklart
treff til et FUNN. Fail-closed-blokkeringen er fullmakten som holdes
tilbake til noen har målt falsk-positiv-raten på vår egen
motpartsportefølje.

Det er samme figur som M-41s refusjon: den farligste raden i
spesifikasjonen, gatet på en modul som aldri har eksistert.

## Dommene v1 hviler på

Samme form som klynge 3, 4 og 5:

* **HISTORIKKEN OVERSKRIVES ALDRI.** Motpartsprofil, sanksjonskontroll,
  anbudstreff, kvalifiseringsvurdering og merkevarefunn er alle
  append-only. Den gjeldende verdien ER den siste raden.
* **HVER PÅSTAND HAR EN KILDE OG EN VERSJON.** Et sanksjonstreff uten
  listeversjon er ubrukelig i ettertid; en kredittvurdering uten
  hvilken policy som gjaldt, likeså. Alle fem grensene bærer en
  `*_uten_kilde`- eller `*_uten_versjon`-invariant.
* **BELØP I ØRE, HELTALL.** `BIGINT`, ingen unntak (101s form).
* **GRENSENE ER TENANTENS.** Kredittpolicy, matchterskel, søkeprofil og
  kontrollfrekvens ligger i basen, satt gjennom en dør.
* **INGEN AV DE FEM GJØR EN UTGÅENDE FORESPØRSEL, OG INGEN HANDLER.**

## Tildelte migrasjonsnumre

| Nr | Modul | Fil |
|----|-------|-----|
| 116 | M-48 | `116_m48_motpartsregister.sql` |
| 117 | M-49 | `117_m49_sanksjonskontroll.sql` |
| 118 | M-46 | `118_m46_anbudsregister.sql` |
| 119 | M-51 | `119_m51_tilskuddsregister.sql` |
| 120 | M-55 | `120_m55_merkevarefunn.sql` |

**Rekkefølgen er ikke vilkårlig.** M-49 avhenger av M-48 i
spesifikasjonen (`dep: M-1, M-2, M-37, M-48`): sanksjonskontrollen
sjekker motparten OG dens reelle rettighetshavere, og motparten er
M-48s register. 116 før 117 er den avhengigheten, uttrykt i
nummerrekkefølgen. De tre siste er uavhengige av hverandre og av de to
første.

## Roller og sveip

| Modul | Eier | Sveip | Klokkeslett (UTC) |
|---|---|---|---|
| M-48 | `disponit_motpart_eier` | `disponit_motpartssveip` | 08:50 |
| M-49 | `disponit_sanksjon_eier` | `disponit_sanksjonssveip` | 09:05 |
| M-46 | `disponit_anbud_eier` | `disponit_anbudssveip` | 09:20 |
| M-51 | `disponit_tilskudd_eier` | `disponit_tilskuddssveip` | 09:35 |
| M-55 | `disponit_merkevare_eier` | `disponit_merkevaresveip` | 09:50 |

Fem eiere og fem sveipere, av samme grunn som før: en delt sveiperolle
måtte hatt EXECUTE på alle kryss-tenant-definerne, og en feil i én
sveip ville båret de andres fullmakt.

**Med klynge 6 er plattformen oppe i TRE OG TJUE nattlige sveip**
(03:15 → 09:50). Stigen holdes av `test_deploy_timerplan` (#369): ingen
to kalendertimere kan dele klokkeslett, og hver må være UTC-festet.
Og `disponit-sveipestatus.timer` (115) må flyttes bakerst — den skal
lese flåtens tilstand ETTER at flåten har kjørt, og porten
`test_timeren_gaar_etter_hele_stigen` gjør det til en måling, ikke en
huskeregel.

## Arbeidsdelingen, uendret fra 3/9

* **Fundamentet** (denne PR-en) eier: migrasjonsnumrene, manifestene,
  `KRAVGRENSER`, rollene i `oppsett-postgresql.sh`, og
  rollemedlemskapene BÅDE der og i `ci.yml`.

  Medlemskapene er her og ikke i modul-PR-ene fordi `SET ROLE` krever
  MEDLEMSKAP, ikke at rollen finnes — og det var nettopp den
  forskjellen som tok staging ned 3/9. Migrasjonene 116–120 kan ikke
  kjøre uten dem.

  **Test-DSN-ene i `ci.yml` er derimot IKKE her.** En test-DSN for en
  sveiperolle hvis sveipefunksjon ikke finnes ennå er død
  konfigurasjon, og den hører sammen med `oppsett-postgresql.sh`-blokken
  og `opp.sh`-preflighten — som #360 måler mot hverandre.
* **Modul-PR-ene** eier: migrasjonen, API-et, flaten, sveipen, DSN-ene
  og `opp.sh`-preflighten.

#360 og #361 måler at de to halvdelene ikke driver fra hverandre.
