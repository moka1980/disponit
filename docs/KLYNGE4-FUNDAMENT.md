# Klynge 4 — «det bransjemalene alt har lovet»

Fem moduler bygges parallelt: **M-14** fakturakontroll, **M-25**
prosjekt- og kontraktstatus, **M-26** prisbok, **M-27** lager og
logistikk, **M-42** kontoverifikasjon og transaksjonsvakt. Denne fila
er kontrakten mellom sporene, og den følger `KLYNGE-FUNDAMENT.md`,
`KLYNGE2-FUNDAMENT.md` og `KLYNGE3-FUNDAMENT.md` — formen har holdt
tre ganger.

## Hvorfor akkurat disse fem

De valgte seg selv, og kriteriet er målbart. Vi sender ut tre
bransjemaler (`policies/bransjemal-{handverk-bygg,netthandel,
tjenestebedrift}.yaml`). Hver av dem navngir **verifikatorer** — de
betrodde partene som kan attestere at et vilkår holder før en
`modus: auto`-handling får lov å skje. Disse fem er navngitt der, og
finnes ikke:

| Modul | Referanser | Maler | Betrodd for |
|---|---:|---|---|
| M-26 | 6 | alle tre | `priser_fra_prisbok`, `laste_klausuler_uendret`, `standard_forbehold_inkludert` |
| M-42 | 4 | 2 | `konto_verifisert`, `konto_verifisert_uavhengig`, `svindelsjekk_bestatt` |
| M-27 | 4 | 2 | `lager_reservert`, `retur_registrert`, `prognose_konfidens` |
| M-25 | 3 | 2 | `milepael_dokumentert`, `kontraktsfestet_betalingsplan`, `prosjektbudsjett_ok`, `arbeid_dokumentert`, `befaring_dokumentert` |
| M-14 | 3 | 1 | `dublettsjekk`, `mva_validert`, `faktura_godkjent` |

De øvrige manglende (M-41 3 ref., M-39 2, M-11 1, M-44 1) står igjen
til klynge 5. Rekkefølgen er den målte, ikke en preferanse.

## Den bærende dommen

**Motoren feiler LUKKET, og det er derfor dette ikke er en åpen
sikkerhetsfeil.** Det er KJØRT, ikke lest:
`platform/core/tests/test_bransjemal_lovnader.py` evaluerer en policy
med et vilkår ingen kan attestere.

De to veiene er forskjellige, og forskjellen betyr noe:

* **Attestasjonen mangler** → `attestasjon_mangler`, og utfallet følger
  handlingens `ved_brudd`. For alle de berørte handlingene er det
  `unntakskø`: saken havner foran et menneske. Handlingen skjer ikke
  automatisk, men den forsvinner heller ikke.
* **Attestasjonen kommer fra en verifikator som ikke er betrodd for
  nettopp det vilkåret** → `verifikator_ikke_betrodd` med
  `tving_stopp`, altså **STOPP** — og `ved_brudd` får ikke overstyre.
  En forfalsket attestasjon skal ikke havne i en kø noen kan godkjenne
  seg forbi.

Og en handling motoren ikke kjenner er UNNTAK med `ukjent_handling`:
deny by default, ikke deny by omission.

**Men det betyr at hver `modus: auto`-handling som er gatet på en av
disse fem, ALDRI HAR SKJEDD.** `faktura.bokfor`, `tilbud.generer`,
`lager.bestill_pafyll`, `materiell.bestill`,
`ordre.bekreft_og_fakturer` står i en policy vi sender ut, merket
`auto`, og har aldri fyrt én gang. Det står ingen steder i policyfila.
Klynge 4 er registrene de fem verifikatorene til slutt må hvile på.

**INGEN AV DE FEM TAR ATTESTASJONSFULLMAKTEN I v1.** Det er klyngens
strengeste enkeltdom, og den er ny: de tre foregående klyngene holdt
igjen på å UTFØRE en handling. Her holder vi igjen på å AUTORISERE en.
En attestasjon er nettopp det som slipper en automatisk handling med
penger i andre enden gjennom — og å ta den fullmakten før målingen
under den finnes, er å la modulen definere sin egen troverdighet.

| Modul | Policyen lover | v1 gjør |
|---|---|---|
| M-14 | attesterer `faktura_godkjent`, `faktura.bokfor` går auto | **kontrollerer** fakturaen og **måler treffraten** |
| M-25 | attesterer `milepael_dokumentert`, auto-fakturerer ordren | **registrerer** kontrakten og **måler** framdrift mot budsjett |
| M-26 | attesterer `priser_fra_prisbok`, genererer tilbud auto | **er boka**: tenantens egne priser, versjonert |
| M-27 | attesterer `lager_reservert`, bestiller påfyll auto | **teller beholdningen** og gjør bestillingspunktet til et funn |
| M-42 | attesterer `konto_verifisert`, `svindelsjekk_bestatt` | **registrerer kontohistorikken** og gjør en endring synlig |

## Hva som er ANNERLEDES fra klynge 3

Dette må stå, ellers ser register-først ut som vane:

Klynge 3 holdt igjen på **utførelsen** — ikke send purringen, ikke
betal leverandøren. Klynge 4 holder igjen på **fullmakten**. Det er en
hardere linje, og den koster: fem moduler bygges uten å levere det
policyen faktisk ber om, og gapet mot `auto` lukkes ikke av denne
klyngen. Det er med vilje. En attestasjonsmyndighet uten en målt
treffrate bak seg er et veddemål der innsatsen er en utgående
betaling.

**M-42 er den skarpeste.** Den er navngitt som svindelvakten foran
utgående betalinger, og det farligste den kunne gjort er ikke å slippe
noe gjennom — det er å **stoppe** noe. En vakt som blokkerer feil er
sin egen skade, og en vakt ingen har målt vet ikke hvor ofte den tar
feil. v1 registrerer hvilken konto som ble oppgitt, av hvem, når, og
hvordan et menneske verifiserte den — og gjør en KONTOENDRING PÅ EN
LEVERANDØR VI BETALER til et funn. Det er det høyeste signalet som
finnes i den svindelklassen, og ingen kan handle på det hvis det ikke
er skrevet ned.

## Tildelte migrasjonsnumre

| Nr | Modul | Fil |
|----|-------|-----|
| 106 | M-14 | `106_m14_fakturakontroll.sql` |
| 107 | M-25 | `107_m25_prosjektregister.sql` |
| 108 | M-26 | `108_m26_prisbok.sql` |
| 109 | M-27 | `109_m27_lagerregister.sql` |
| 110 | M-42 | `110_m42_kontoregister.sql` |

Kjøreren krever **ikke** ubrutt sekvens (`db/kjorer.py` itererer
`sorted(glob(...))` og hopper over det som alt står i registeret). Det
er KOLLISJONEN i `migrasjons-fasit.json` som koster, ikke hullet — så
et spor kan bruke sitt tildelte nummer med én gang.

## Grensene mot hverandre og mot søsknene, sagt eksplisitt

* **M-14** eier den INNGÅENDE fakturaen — det noen krever av oss.
  **M-23** eier FORDRINGENE — det vi krever av noen. **M-13** eier
  BANKPOSTENE — det som har skjedd på konto.

  De tre er ikke tre syn på samme rad. En inngående faktura blir en
  bankpost når den betales, og v1 kobler dem **ikke** automatisk: en
  betalt bankpost uten en kontrollert faktura er et funn å se på.

* **M-14** kontrollerer, **M-13** avstemmer. Dublettsjekken i M-14 ser
  på inngående fakturaer mot hverandre; den ser aldri i hovedboken,
  fordi det ikke finnes noen.

* **M-26** eier PRISEN VI TAR. **M-24** eier PRISEN VI BETALER.
  Katalogen deler marginbeskyttelsen eksplisitt — M-24 oppdager
  kostnadsøkningen, M-26 er boka en ny pris til slutt må skrives inn i.
  **Ingen av dem beregner en ny pris**, og v1-M-26 setter ingen: hver
  pris i boka er skrevet av et menneske gjennom en dør.

* **M-25** eier PROSJEKTET og kontraktens betalingsplan. **M-21** eier
  FRISTENE våre mot omverdenen. En milepæl er ikke en plikt mot
  omverdenen; den er et punkt i en kontrakt, og de to registrene skal
  ikke speile hverandre.

* **M-27** eier BEHOLDNINGEN. **M-24** eier AVTALEN med leverandøren
  vi ville bestilt fra. Et bestillingspunkt som er passert er et funn
  i M-27; hvilken avtale påfyllet ville gått på, er M-24s rad.

* **M-42** eier KONTONUMMERET og hvordan det ble verifisert. **M-24**
  eier LEVERANDØREN. Et kontonummer henger på leverandøren, men
  historikken — hvem oppga hvilken konto når — er M-42s, fordi det er
  historikken og ikke gjeldende verdi som avslører svindelen.

* **Alle fem** bruker **M-37s** unntakskø. Ingen ny kø.

## Beløp regnes i heltall

Fire av de fem bærer invarianten `belop_i_flyttall` (M-42 er unntaket:
den teller ingen penger, den teller kontonumre og hvem som verifiserte
dem). Minste enhet (øre), `BIGINT`, ingen unntak. En prisbok med et
flyttall gir et tilbud som er noen øre feil, hver gang, for alltid.

## Én sveiperolle per modul — og hvorfor

Fem eiere og fem sveipere, som i klynge 3, og av samme grunn: en delt
sveiperolle måtte hatt EXECUTE på alle fem kryss-tenant-defienerne, og
en feil i én sveip ville da båret de fire andres fullmakt.

Med klynge 4 er plattformen oppe i **fjorten** nattlige sveip. Det
tallet er nå stort nok til at det er en egen driftssak: klokkeslettene
er tildelt manuelt og ligger tett (03:15 → 07:20), og en felles
planlegger med observerbarhet er verdt en runde. Det er fortsatt ikke
en grunn til å slå rollene sammen — det ville byttet en driftssak mot
en sikkerhetssvekkelse.

Tildelte klokkeslett: M-14 06:20, M-25 06:35, M-26 06:50, M-27 07:05,
M-42 07:20 (UTC), alle med `RandomizedDelaySec=30min`.

## Hva fundamentet eier

* fem manifester + `MODULSTATUS`/`MODULER` i **samme commit**
* `KRAVGRENSER` for `m14-v1`…`m42-v1` — registrert **før** byggingen (§0)
* `locales/{nb,en}.json`: `site.modul.m{14,25,26,27,42}.*`
* ti DB-roller i `oppsett-postgresql.sh` og `ci.yml`, i én omgang —
  og M-27s eier heter `disponit_beholdning_eier`, ikke
  `disponit_lager_eier`: det navnet er M-4s (093/099), og to moduler
  som deler eierrolle er nøyaktig den fullmaktsdelingen «én rolle per
  modul» finnes for å hindre
* de tildelte migrasjonsnumrene og klokkeslettene over
* **porten som teller gapet**: `test_bransjemal_lovnader.py` måler hvor
  mange verifikatorer i utsendte bransjemaler som ikke har en modul, og
  den listen skal krympe. Uten den er «M-44 mangler også» noe man må
  huske.

## Hva hvert byggespor eier

Egne filer, og **egne linjer** i de delte:

* legg oppføringen i delte lister **uten** å endre naboens linje
* aldri gjør en assert avhengig av å stå SIST i en delt liste
* kjør `sjekk-fletteskade.py` etter rebase, før commit
* **den nye sveipen skal stå i `test_sveipekontrakten.py`** — porten
  krever at listen der er komplett, og den blir rød på en sveip som
  ikke er ført opp. Det er med vilje: den gale commit-rekkefølgen
  spredte seg til seks filer nettopp fordi ingen port krevde at nye
  sveip ble målt.

## Status — klyngen er bygget

Alle fem registrene står i `main`:

| Modul | Migrasjon | PR | Sveip (UTC) |
|---|---|---|---|
| M-14 | `106_m14_fakturakontroll.sql` | #355 | 06:20 |
| M-25 | `107_m25_prosjektregister.sql` | #356 | 06:35 |
| M-26 | `108_m26_prisbok.sql` | #357 | 06:50 |
| M-27 | `109_m27_lagerregister.sql` | #358 | 07:05 |
| M-42 | `110_m42_kontoregister.sql` | #359 | 07:20 |

**OG DOMMEN STÅR: ingen av dem attesterer.** Gapet mot `modus: auto` er
ikke lukket av denne klyngen, og det er med vilje. Det som er endret er
at gapet nå har et MÅLEGRUNNLAG under seg: fem registre som skriver ned
hva som faktisk skjedde, slik at en treffrate kan regnes før noen får
fullmakten.

`test_bransjemal_lovnader.test_klynge4_er_bygget_og_ingen_av_dem_attesterer`
binder begge halvdelene: migrasjonene finnes, og ingen av dem har en
attesteringsdør.

### Det som står igjen

* **Attestasjonsfullmakten** for alle fem — krever en målt treffrate,
  og den finnes ikke før registrene har stått en stund.
* **`VENTENDE` i porten**: M-11 (1 ref.), M-39 (2), M-41 (3), M-44 (1).
  M-39 og M-41 er tildelt klynge 5.
* **Fjorten nattlige sveip** er nå en egen driftssak. Klokkeslettene er
  tildelt manuelt og ligger tett (03:15 → 07:20). En felles planlegger
  med observerbarhet er verdt en runde — men det er fortsatt ikke en
  grunn til å slå sveiperollene sammen.
* **To felles opprydninger i sveipefilene**: `_skriv_feiltelling`-formen
  og rad-kontrakten (109/110 har den nye formen, de sju eldre ikke).
