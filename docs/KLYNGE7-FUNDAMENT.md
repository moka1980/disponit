# Klynge 7 — «de fem der regelen er myndighetens»

**M-47** myndighetsrapporteringsagent, **M-50** postjournal- og
innsynsvakt, **M-52** toll- og HS-kodeagent, **M-53** HMS- og
avviksmottak, **M-54** EHF- og Peppol-avviksretter.

Dette dokumentet fester dommen, migrasjonsnumrene, rollene og grensene
FØR koden (§0-regelen), som klynge 2–6 gjorde.

## Hvorfor akkurat disse fem

**Fordi de er de som er igjen.** v8 tok inn elleve nye moduler; M-56
ble bygget for seg, klynge 6 tok M-46, M-48, M-49, M-51 og M-55, og
disse fem står tilbake. Det er aritmetikk, ikke slektskap.

Det er verdt å si rett ut, fordi den nærliggende feilen er å finne på
et fellesskap som ikke finnes. Klynge 6 HADDE en delt dom —
spesifikasjonens egne vakter sa det samme fem ganger, «sender aldri
inn tilbud», «setter aldri kredittgrensen selv», «sender aldri inn
søknad» — og dommen bar hele klyngen. Å lete etter noe tilsvarende her
og late som man fant det, ville gitt fem moduler bygget på en
begrunnelse som ikke holder for noen av dem.

## Det de LIKEVEL deler, og som er ekte

**Alle fem står overfor en myndighet.**

| Modul | Myndigheten |
|---|---|
| M-47 | den som krever innsendingen |
| M-50 | den som fører postjournalen |
| M-52 | tollmyndigheten som eier HS-nomenklaturen |
| M-53 | Arbeidstilsynet, som krever avviksregisteret |
| M-54 | EU/Peppol, som eier EHF-standarden |

Og det har én konsekvens som gjelder alle fem:

> **REGELEN ER IKKE VÅR, DEN ENDRES, OG DEN ENDRES UTEN Å SI FRA.**

HS-nomenklaturen revideres. EHF-formatet får nye versjoner.
Innsendingsfrister flyttes. Postjournalens format er kommunens.
Arbeidstilsynets krav til hva et avvik må inneholde er lov, ikke
konfigurasjon.

**En foreldet regel ser nøyaktig ut som en riktig regel.** Det er
forskjellen fra en feil: en feil gir et avvik noen ser. En foreldet
HS-kode gir et svar som er velformet, selvsikkert og galt — og
tollboten kommer et halvår senere.

Derfor er klyngens delte invariant:

> **HVER AVGJØRELSE BÆRER HVILKEN VERSJON AV REGELEN DEN BLE TATT
> UNDER, OG HVOR DEN VERSJONEN KOM FRA.**

M-51 (119) lærte halvparten av dette med `regelverksversjon` på
ordningsraden. M-55 (120) lærte den andre halvparten med
`algoritmeversjon` og `kravversjon` på hver vurdering. Klynge 7 gjør
det til den bærende invarianten for alle fem, og legger til det de to
ikke trengte: **regelen skal kunne bli GAMMEL, og modulen skal si fra
når den er det.**

En regel uten utløpsdato er en regel ingen ser aldrende.

## Den andre delte dommen: modulen KLARGJØR, mennesket SIGNERER

Fire av de fem lager et artefakt som er ment å FORLATE HUSET: en
innsending, en tolldeklarasjon, en rettet faktura. Spesifikasjonen sier
det selv for M-54: «Retting klargjøres maskinelt, utsending signeres av
menneske.»

Det er ikke klynge 6s dom om igjen. Der ble den farlige handlingen
holdt tilbake HELT, fordi modulene fant noe og ikke skulle handle på
det. Her ER artefaktet produktet — en innsending som aldri klargjøres
er verdiløs — og grensen går ved signaturen.

**MEN v1 GÅR IKKE DIT.** Register-først gjelder som før:

> v1 klargjør artefaktet og STOPPER. Det finnes ingen utboks, ingen
> signaturmaskineri og ingen sendevei. Signaturen hører til v2, og
> forutsetningen for v2 er MÅLT: hvor ofte klargjøringen er feil, og
> hvor ofte et menneske faktisk fanget det.

Grunnen er den samme som i klynge 6, men strammere her: en signatur som
et menneske setter på noe det ikke har lest, er verre enn ingen
signatur — den flytter ansvaret uten å flytte kontrollen. Vi vet ikke
ennå hvor ofte klargjøringen tar feil. Til vi vet det, kan vi ikke
utforme signaturen slik at den betyr noe.

## Hvor klyngen IKKE er én ting: M-53

**M-53 hører egentlig ikke hjemme her, og det skal stå skrevet.**

De fire andre produserer noe som skal ut. M-53 TAR IMOT — et
avviksmottak er en innboks, ikke en utboks. Og risikoen ligger et helt
annet sted: dette er den eneste modulen i katalogen som mottar data OM
en ansatt FRA en ansatt.

Det betyr tre ting ingen av de fire andre har:

1. **Helseopplysninger.** En skademelding inneholder særlige kategorier
   etter GDPR art. 9. Ikke som en mulighet — som normaltilfellet.
2. **Varslervern.** Et avvik kan være et varsel etter arbeidsmiljøloven
   kap. 2 A. Den som varsler har rett til vern mot gjengjeldelse, og
   det vernet er verdiløst hvis mottakeren lekker identitet.
3. **Oppbevaringsplikt SOM KOLLIDERER MED SLETTEPLIKT.** Arbeidstilsynet
   krever at avvik bevares. GDPR krever at personopplysninger slettes.
   M-30 eier sletteretten; M-53 vil ha rader M-30 ikke får røre.

Punkt 3 er det farligste, fordi det er en KONFLIKT MELLOM TO MODULER
VI ALT HAR BYGGET, og den oppdages først den dagen noen ber om
sletting.

**Konsekvensen for rekkefølgen:** M-53 bygges SIST i klyngen, og
grensesnittet mot M-30 avklares før koden. Migrasjonsnummeret er
tildelt nå, men rekkefølgen i tabellen under er ikke vilkårlig.

## Dommene v1 hviler på

Felles for alle fem, håndhevet i datamodellen og ikke som regler noen
må huske:

1. **HISTORIKKEN OVERSKRIVES ALDRI.** Append-only med radvakt. M-42s
   dom (110), gjentatt i 112–120.

2. **HVER AVGJØRELSE BÆRER REGELVERSJONEN SIN.** NOT NULL, snapshotet
   på raden — ikke en fremmednøkkel til en rad som kan endres.

3. **REGELEN KAN BLI GAMMEL, OG SVEIPEN SIER FRA.** Hver regelrad har
   en gyldighet, og et funn som er regnet under en utløpt regel er et
   sveipefunn — ikke et stille galt svar.

4. **INGEN KOLONNE BETYR «SENDT».** Ingen utboks, ingen mottaker, ingen
   signatur i v1.

5. **TERSKLENE ER TENANTENS.** Samme dom som M-55s
   `forvekslingsterskel_hardkodet`.

## Tildelte migrasjonsnumre

| Nr | Modul | Fil | Bygges |
|----|-------|-----|--------|
| 121 | M-54 | `121_m54_ehf_avvik.sql` | 1. |
| 122 | M-52 | `122_m52_tollkode.sql` | 2. |
| 123 | M-47 | `123_m47_myndighetsrapport.sql` | 3. |
| 124 | M-50 | `124_m50_postjournal.sql` | 4. |
| 125 | M-53 | `125_m53_hms_avvik.sql` | 5. |

**Rekkefølgen er begrunnet.** M-54 først fordi den er den enkleste og
den mest mekaniske: EHF-validering er XML mot et skjema, og «hva er
riktig» er ikke en vurdering. Den etablerer klyngens form —
regelversjon på hver avgjørelse, utløpsvakt på regelen — på det
tilfellet der formen er lettest å se om er riktig.

M-53 sist, av grunnen over.

## Roller og sveip

| Modul | Eier | Sveip | Klokkeslett (UTC) |
|---|---|---|---|
| M-54 | `disponit_ehf_eier` | `disponit_ehfsveip` | 10:05 |
| M-52 | `disponit_tollkode_eier` | `disponit_tollkodesveip` | 10:20 |
| M-47 | `disponit_myndighet_eier` | `disponit_myndighetssveip` | 10:35 |
| M-50 | `disponit_postjournal_eier` | `disponit_postjournalsveip` | 10:50 |
| M-53 | `disponit_hms_eier` | `disponit_hmssveip` | 11:05 |

Fem eiere og fem sveipere, av samme grunn som før: en delt sveiperolle
måtte hatt EXECUTE på alle kryss-tenant-definerne, og en feil i én
sveip ville båret de andres fullmakt.

**`disponit-sveipestatus.timer` flyttes til 11:20.** Den skal lese
flåtens tilstand ETTER at flåten har kjørt, og
`test_timeren_gaar_etter_hele_stigen` gjør det til en måling, ikke en
huskeregel. Klokkeslettet 08:35 står fortsatt ledig — det ble tomt da
sveipestatus flyttet for M-48 — men klyngen holdes SAMLET i stigen, så
de fem kan leses som fem i `systemctl list-timers`.

**Med klynge 7 er plattformen oppe i ÅTTE OG TJUE nattlige sveip**
(03:15 → 11:20).

## Arbeidsdelingen, uendret fra klynge 6

**FUNDAMENT-FØRST.** Denne PR-en eier: migrasjonsnumrene, manifestene,
`KRAVGRENSER`, eierrollene og sveiperollene i BÅDE
`oppsett-postgresql.sh` og `ci.yml`, MODULSTATUS/MODULOVERSIKT og
locales for modulnavnene.

**Modul-PR-ene eier:** sin egen migrasjon, sitt API, sin flate, sin
sveip, sine porter, sin DSN og sin `opp.sh`-preflight.

Grunnen er målt i klynge 5 og 6: to modul-PR-er som begge rører
`ci.yml` og `oppsett-postgresql.sh` gir en fletting der en tapt linje
ser ut som ingenting — og en manglende rolle oppdages først når
migrasjonen kjøres på en fersk base.
