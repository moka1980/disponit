# Klynge 3 — «kundens livsløp og pengene»

Fem moduler bygges parallelt: **M-13** bankavstemming, **M-17**
kundeservice, **M-18** kunde-onboarding, **M-23** kundefordringer,
**M-24** leverandør og innkjøp. Denne fila er kontrakten mellom
sporene, og den følger `KLYNGE-FUNDAMENT.md` og
`KLYNGE2-FUNDAMENT.md` — formen har holdt to ganger.

## Den bærende dommen

Alle fem er **registre og målere**. For hver av dem er den farlige
handlingen nettopp den katalogen lover:

| Modul | Katalogen lover | v1 gjør |
|---|---|---|
| M-13 | bokfører automatisk ved full match | **avstemmer og viser** de uavstemte |
| M-17 | løser repeterende henvendelser automatisk | **klassifiserer** og lager **utkast** |
| M-18 | 0 min per ny kunde, alt maskinelt | **registrerer løpet** og måler det |
| M-23 | foreslår nedbetalingsplan til kunden | **registrerer fordringen** og alderen |
| M-24 | betaler leverandøren innen policygrenser | **måler** avtalen mot leveransen |

## Hva som er ANNERLEDES fra klynge 1 og 2

Dette må stå, ellers ser register-først ut som vane:

**Tre av de fem rører penger** (M-13, M-23, M-24) og **to av dem rører
en kunde direkte** (M-17, M-23). Skaden ved en feilaktig utført
handling er kvalitativt annen enn i de to første klyngene. En
tilgangsrad som ble skrevet feil, kan slettes. En postering i et
regnskap, en purring til feil kunde, eller en utgående betaling kan
det ikke — de har forlatt systemet i det øyeblikket de skjedde.

Register-først er derfor ikke bare den vante formen her. Den er den
eneste forsvarlige, og den er dessuten den raskeste veien til
utførelsen: en autobokføring uten en målt treffrate er et veddemål, og
en purregrense ingen har målt normalvariasjonen bak er et tall noen
gjettet.

## Tildelte migrasjonsnumre

| Nr | Modul | Fil |
|----|-------|-----|
| 101 | M-13 | `101_m13_avstemmingsregister.sql` |
| 102 | M-17 | `102_m17_henvendelsesregister.sql` |
| 103 | M-18 | `103_m18_onboardingregister.sql` |
| 104 | M-23 | `104_m23_fordringsregister.sql` |
| 105 | M-24 | `105_m24_leverandorregister.sql` |

Kjøreren krever **ikke** ubrutt sekvens (`db/kjorer.py` itererer
`sorted(glob(...))` og hopper over det som alt står i registeret). Det
er KOLLISJONEN i `migrasjons-fasit.json` som koster, ikke hullet — så
et spor kan bruke sitt tildelte nummer med én gang, uten å vente på at
et lavere nummer merges.

## Grensene mot hverandre og mot søsknene, sagt eksplisitt

* **M-13** eier BANKPOSTER — det som har skjedd på konto
* **M-23** eier FORDRINGER — det kunden skylder

  En innbetaling er begge deler, sett fra hver sin side. v1 kobler dem
  **ikke** automatisk: en ubetalt fordring med en umatchet innbetaling
  i samme størrelsesorden er et FUNN å se på, aldri en lukking.

* **M-18** eier LØPET — hva som skal skje for en ny kunde
* **M-12** eier TILGANGENE — hvem som har hva

  Et onboardingsteg kan NEVNE en tilgang; det oppretter den ikke, og
  det **speiler** den ikke. To registre som begge påstår å vite hvem
  som har hva, kan aldri holdes i takt.

* **M-17** eier HENVENDELSENE — det noen spurte om
* **M-9** eier BEGREPENE — det vi vet

  Et svarutkast SITERER M-9. Når begrepet endres, er utkastet
  foreldet — og det er en egenskap ved utkastet, ikke ved begrepet.

* **M-17** bruker **M-37s** unntakskø. Ingen ny kø. En andre kø ved
  siden av den er nøyaktig det M-37 ble bygget for å hindre.

* **M-24** måler leverandørens forpliktelse MOT OSS. **M-21** eier
  våre plikter mot omverdenen. Et SLA-brudd er et funn om DEM.

* **M-24** oppdager kostnadsøkningen, **M-26** foreslår ny pris.
  Katalogen deler marginbeskyttelsen eksplisitt, og v1 holder seg på
  sin side av snittet: den beregner ikke ny pris i det hele tatt.

## Beløp regnes i heltall

Alle tre pengemodulene bærer invarianten `belop_i_flyttall`. Et
flyttall i en avstemming eller en aldersfordeling er en feil som viser
seg først når summene ikke går opp — og da er den ikke lenger til å
finne ut av. Minste enhet (øre), `BIGINT`, ingen unntak.

## Én sveiperolle per modul — og hvorfor

Fem eiere og fem sveipere. Det er ti roller og fem nattlige timere for
én klynge, og det er en **sikkerhetsdom**, ikke en forglemmelse: en
delt sveiperolle måtte hatt EXECUTE på alle fem kryss-tenant-defienerne,
og en feil i én sveip ville da båret de fire andres fullmakt.

Prisen er operasjonell (fem timere i stedet for én). Gevinsten er at
hver sveips autoritet står i nøyaktig én definer, revidérbar på ett
sted. Med klynge 3 er plattformen oppe i et tosifret antall nattlige
sveip — det er verdt en egen runde om planlegging og observerbarhet,
men det er en drifts­sak og ikke en grunn til å slå rollene sammen.

## Hva fundamentet eier

* fem manifester + `MODULSTATUS`/`MODULER` i **samme commit**
* `KRAVGRENSER` for `m13-v1`…`m24-v1` — registrert **før** byggingen (§0)
* `locales/{nb,en}.json`: `site.modul.m{13,17,18,23,24}.*`
* ti DB-roller i `oppsett-postgresql.sh` og `ci.yml`, i én omgang
* de tildelte migrasjonsnumrene over

## Hva hvert byggespor eier

Egne filer, og **egne linjer** i de delte. Erfaringen fra klynge 2 er
konkret: `losflett.py` løser tekstkonflikter additivt, men en naiv
fletting av to spor som la hver sin halelinje i samme liste gir en
syntaksfeil, ikke to lister. Sporene skal derfor:

* legge sin oppføring i delte lister **uten** å endre naboens linje
* aldri gjøre en assert avhengig av å stå SIST i en delt liste
  (M-9-porten ble rød av M-34 av nøyaktig den grunnen)
* kjøre `sjekk-fletteskade.py` etter rebase, før commit
