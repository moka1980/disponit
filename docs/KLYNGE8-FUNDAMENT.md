# Klynge 8: prognosene

**M-15** likviditets- og kostnadsagent, **M-33** prediksjons- og
scenarioagent, **M-36** bedriftsoptimalisator.

Klynge 7 var fem moduler som møter en MYNDIGHET. Denne er tre som
møter noe vanskeligere: **framtiden**. De tre er samlet fordi de deler
én dom, og fordi den dommen er lettere å felle én gang enn tre.

## Den delte dommen

> **EN GAL PROGNOSE SER NØYAKTIG UT SOM EN RIKTIG PROGNOSE — HELT TIL
> HORISONTEN ER PASSERT, OG DA HAR ALLE SLUTTET Å SE.**

Dette er klyngens svar på klynge 7s «en foreldet regel ser nøyaktig ut
som en riktig regel», og det er den samme feilformen ett hakk verre.
En foreldet regel kan i det minste SLÅS OPP: den har en gyldighetsdato,
og noen kan lese den. En prognose har ingenting å slå opp mot før
tiden har gått — og i det øyeblikket den kunne etterprøves, er den
uinteressant, fordi nå vet vi jo hva som skjedde.

Derfor er ikke klyngens vanskeligste problem å LAGE prognoser. Det er
å sørge for at de blir MÅLT.

Spesifikasjonen sier det selv, i hver av de tre vaktsetningene:

| Modul | Vakt (fra katalogen) |
|---|---|
| M-15 | «Forslag merkes som prognose; oppsigelser og betalinger utføres bare via egne policykontrollerte moduler.» |
| M-33 | «Prognoser er ikke fakta; ingen personalavgjørelse eller automatisk handling uten separat policy.» |
| M-36 | «Kan aldri utvide egen fullmakt; korrelasjon presenteres ikke som årsak; porteføljestopp tilgjengelig.» |

Tre setninger, samme frykt: at et tall med desimaler blir lest som et
faktum fordi det står i et system.

## Fire dommer v1 hviler på

Alle fire håndheves i datamodellen, ikke som regler noen må huske.

### 1. EN PROGNOSE UTEN HORISONT KAN IKKE OPPSTÅ

`horisont` og `gjelder_til` er `NOT NULL`. En prognose uten et
tidspunkt den kan etterprøves mot, er ikke en prognose — det er en
mening med tall i.

Samme form som M-50s `journalperson.slettefrist` (124) og M-53s
`hmsavvik.oppbevaring_til` (127): **det farlige gjøres umulig, ikke
oppdaget.** Og av samme grunn som der — oppdagelsen kommer for sent.
En prognose uten horisont oppdages den dagen noen spør «stemte den?»,
og da finnes svaret ikke lenger.

### 2. HVER PROGNOSE BÆRER MODELLEN SIN, SNAPSHOTET

`modellnavn`, `modellversjon` og parametrene som ble brukt står PÅ
raden — ikke som en fremmednøkkel til en modell som kan endres.

Klynge 7 felte den samme dommen om regelverk, og begrunnelsen er
ordrett den samme: en modell som kunne endres i ettertid ville gjort
hvert snapshot til en påstand om noe som ikke lenger står noe sted.
Forskjellen er at en modell endres OFTERE enn et regelverk, og som
regel av oss selv.

### 3. INTERVALL, ALDRI BARE PUNKT

`nedre` og `ovre` er `NOT NULL` ved siden av `punkt`. Et punktestimat
uten usikkerhet er ikke en presis prognose — det er en upresis
prognose som har mistet informasjonen om hvor upresis den er.

DETTE ER EN KOSTNAD VI TAR MED VILJE. Det er lettere å bygge en modul
som svarer «kontantbeholdningen om 13 uker: 2 340 000». Den ser bedre
ut på en skjerm, og den er verdiløs. Intervallet er det eneste som
gjør en prognose mulig å ta en beslutning på: en likviditetsbane som
med 80 % sannsynlighet ligger mellom 200 000 og 4 millioner sier at du
ikke vet, og «du vet ikke» er et brukbart svar.

### 4. INGEN AV DE TRE UTFØRER NOE

Ingen kolonne betyr «sendt», «sagt opp», «betalt» eller «iverksatt».
M-15 finner et kostnadstiltak og STOPPER der; oppsigelsen av et
abonnement går gjennom M-41s policykontrollerte vei, av et menneske.
M-33 lager en bemanningsprognose og stopper der. M-36 rangerer tiltak
og stopper der.

Dette er v1-dommen fra hver forrige klynge, og den er skarpere her
fordi FRISTELSEN er større: en optimalisator som rangerer tiltak er
ett steg fra en optimalisator som tar dem, og det steget ser ut som en
forbedring.

## Funnet ingen kan lukke

Hver modul får minst ett, og de tre deler det viktigste:

**`prognose_uten_maaling`** — horisonten er passert, og ingen har
sammenlignet prognosen med det som faktisk skjedde.

Det kan ikke lukkes av et menneske. Det lukkes av at MÅLINGEN
registreres. En knapp som fjernet det, ville fjernet det eneste
signalet om at modulen har sluttet å lære — og en prognosemodul som
ikke måles er en modul som blir gradvis dårligere uten at noen
oppdager det, mens den beholder autoriteten sin.

De andre:

| Funn | Modul | Hvorfor det ikke kan klikkes bort |
|---|---|---|
| `slaar_ikke_naiv_baseline` | M-33 | En modell som ikke slår «samme som forrige uke» bærer autoritet den ikke har fortjent. |
| `prognose_mot_utdatert_grunnlag` | M-15 | Banksaldoen er fra i går, prognosen fra i dag. Alderen på inngangsdataene er en del av prognosen. |
| `korrelasjon_presentert_som_aarsak` | M-36 | Vaktsetningens egen ordlyd. Se under. |
| `tiltak_uten_reversibilitet` | M-15, M-36 | Et tiltak ingen har vurdert reversibiliteten av, er et tiltak ingen kan angre. |

## Hvor klyngen IKKE er én ting: M-36

**M-36 hører hjemme i klyngen fordi den prognostiserer, men den er
farligere enn de to andre, og det skal stå skrevet.**

M-15 og M-33 lager prognoser om ETT domene hver. M-36 leser resultatet
av ALLE moduler og rangerer tiltak på tvers av dem. Tre ting følger:

1. **Den er den eneste modulen i katalogen som har en oppfatning om de
   andre modulene.** Katalogens egen `dep` for M-36 er «Modul 1–35».
2. **Vaktsetningen sier «kan aldri utvide egen fullmakt», og det er
   ikke en selvfølge — det er en advarsel.** En optimalisator som
   finner at den beste forbedringen er «gi M-36 lov til å gjøre X», er
   ikke ødelagt. Den gjør nøyaktig det den ble bedt om. Derfor må
   fullmaktsutvidelse være UREPRESENTERBAR, ikke frarådet.
3. **«Korrelasjon presenteres ikke som årsak» er et krav til
   DATAMODELLEN, ikke til teksten på skjermen.** Et tiltaksforslag
   bærer `grunnlagstype` med et lukket sett: `korrelasjon`,
   `eksperiment`, `regel`. En rangering som blander dem uten å si
   hvilken som er hvilken, er den påstanden vakten forbyr.

**Konsekvensen for rekkefølgen:** M-36 bygges SIST, og grensesnittet
mot KPI-laget avklares før koden — se neste avsnitt.

## Grensesnittet som må avklares FØR koden

Klynge 7 lærte dette av M-53: fundamentet forutsatte at M-30 SLETTER,
og M-30 sletter ingenting. Antakelsen var skrevet ned i et
fundamentdokument og feil, og den ble oppdaget først da noen leste
099. Derfor står avklaringen her, målt mot koden.

### M-36 leser en «KPI-katalog» som ikke finnes

Katalogen sier at M-36s input er «KPI-katalog, modulresultater,
strategi, budsjett, risiko og eksperimenthistorikk», og at
integrasjonen er «alle moduler via hendelses- og KPI-lag».

**Det laget finnes ikke.** Jeg har lest M-16 (086 og
`platform/core/api/lesing.py`): `GET /v1/nokkeltall` teller
PLATTFORMAKTIVITET — `m16_beslutninger`, `m16_frekvens`,
`m16_aktiveringer`, `m16_oppdrag`, `m16_unntak_aktivitet`, `m16_tick`
— over et vindu. Tillatt, stoppet, unntak, totalt. Det er et
driftsdashbord for policymotoren, ikke en katalog over
virksomhetens nøkkeltall.

Det finnes heller ikke noe «hendelseslag» på tvers av moduler. Hver
modul har sitt eget funnregister (`*funn`), og de deler FORM, ikke
tabell.

**Avklaringen:** M-36 v1 leser ikke en KPI-katalog, fordi det ikke
finnes en å lese. Den leser de ÅPNE FUNNENE fra modulregistrene — det
er det eneste tverrgående, standardiserte signalet som faktisk
eksisterer i huset i dag, og det er et ærlig et: et åpent funn er noe
en modul har målt og et menneske ikke har lukket.

Det betyr at M-36 v1 er SMALERE enn katalogteksten. Det står her, og
det skal stå i modulens eget filhode: **en modul som later som den
leser noe den ikke har, er verre enn en som sier hva den mangler.**

### M-33 og datakvaliteten (M-3, 092)

M-33s katalog-`dep` er «Datakvalitet, historikk, evaluering og
KPI-katalog». M-3 finnes (092). Avklaringen som må gjøres i M-33s egen
runde: en prognose regnet på data M-3 har flagget som mangelfulle, må
BÆRE det flagget — ikke nektes. Å nekte ville gjort modulen ubrukelig
i nettopp den situasjonen den er nyttigst.

### M-33 OG HISTORIKKEN — TO AV FIRE `dep` FINNES IKKE

**SKREVET 5/9, under byggingen av 130.** Fundamentet skrev at M-33s
`dep` er «datakvalitet, historikk, evaluering og KPI-katalog». To av
de fire finnes ikke som tabeller. **Tredje gang i denne klyngen at et
fundament tildelte data det ikke hadde lest** — etter M-36s
KPI-katalog og M-15s lønnssats.

**HVA SOM FAKTISK FINNES, verifisert mot basen:**

* `timeregistrering` (M-39, 113) — FAKTISK ARBEIDET TID. Minutter, per
  person, per dato, med `kilde` i et lukket sett.
* `arbeidsplan` (M-39, 113) — PLANLAGT kapasitet, versjonert per taker.
* `kvalitetsfunn` og `kvalitetsprofil` (M-3, 092) — datakvaliteten.

**HISTORIKKEN ER `timeregistrering` OG IKKE `arbeidsplan`, og det er
en dom:** en prognose regnet på PLANEN prognoserer planen. Den ville
vært perfekt og verdiløs — modellen ville spådd nøyaktig det noen alt
hadde skrevet ned, og «slår modellen basislinjen?» ville handlet om
hvor stabil planleggeren er, ikke om hvor godt vi forstår arbeidet.
HISTORIKKEN MÅ VÆRE NOE INGEN BESTEMTE PÅ FORHÅND.

**LEGG MERKE TIL AT DET SAMME FUNNET SOM SVEKKET M-15, BÆRER M-33.**
M-39 måler timer og ikke kroner. For M-15 betydde det at
lønnskostnaden måtte registreres av et menneske. For M-33 er timer
nøyaktig det modulen skal prognosere — samme fravær, motsatt
konsekvens.

### `slaar_ikke_naiv_baseline` KREVER AT MODELLEN KAN TAPE

**Klyngen gjorde funnet til et av dem ingen kan lukke. Det er bare
ekte hvis modellen faktisk KAN tape for basislinjen.** En v1 som
«prognoserte» ved å kopiere forrige uke ville hatt null avvik mot
basislinjen for alltid, invarianten ville vært grønn i all evighet, og
den ville ikke målt noe som helst.

Derfor er v1-modellen et GLIDENDE SNITT over `grunnlag_uker` hele
uker, og basislinjen er «samme som forrige uke». De to er
FORSKJELLIGE tall, og på en tenant med stigende eller sesongpreget
bemanning taper snittet regelmessig. **Det er meningen.**

Det følger to ting av dette som står i 130:

1. `grunnlag_uker` har minstverdi **2**, ikke 1. Med én observert uke
   ER snittet forrige uke — modellen er sin egen basislinje og kan
   ikke tape.
2. **Likhet teller som tap** (`>=`, ikke `>`). En modell som er
   nøyaktig like god som å kopiere forrige uke har ikke tilført noe,
   og den koster tillit fordi den ser ut som analyse.

### «REN» OG «UKJENT» DATAKVALITET ER IKKE SAMME TILSTAND

`prognose_uten_datakvalitetsflagg` kunne vært løst med en boolsk
`data_ok`. Det ville vært galt, og galt i den farlige retningen:
`kvalitetsfunn` er tom for en tenant både når M-3 har kjørt og ikke
funnet noe, OG når M-3 aldri har sett på tenanten. En boolsk kolonne
ville gjort det andre til det første, og prognosen ville båret et
kvalitetsstempel ingen hadde utstedt.

Derfor er `datakvalitet` et lukket sett på **tre**: `ren`, `flagget`,
`ukjent`. Samme form som `lukket_av`-dommen i 125 — den stille
standardverdien er den farlige, og den skal være urepresenterbar.

**Og prognosen nektes aldri på flagget data.** Å nekte ville gjort
modulen ubrukelig i nettopp den situasjonen den er nyttigst — når noe
er galt og noen må planlegge likevel.

### FUNNET RIGGEN GA, SOM LESING IKKE VILLE GITT

**En sveip uten tenantkontekst ser NULL RADER, ikke alle.** Første
utkast av `m33_sveip_prognose` spurte på tvers av tenanter i ett svep.
Tabellene har `FORCE ROW LEVEL SECURITY`, så spørringen ville passert
ingen policy — sveipen ville rapportert null funn hver natt, med grønn
exit-kode.

**Det er den farligste feilformen en sveip kan ha: den ser ut som en
vellykket kjøring.** Samme familie som klyngens egen dom — en gal
prognose ser ut som en riktig prognose; her ser en blind sveip ut som
en ren base. Sveipen går derfor ÉN TENANT OM GANGEN med konteksten
satt, og `m33_sveip_tenantliste` er den snevre kryss-tenant-policyen
som lar løkka hente tenantlisten før konteksten finnes.

### M-15 har inngangsdataene sine — MEN IKKE LØNNEN

**RETTET 5/9, under byggingen av 128.** Dette avsnittet listet
opprinnelig lønnsgrunnlaget (M-39, 113) blant M-15s inngangsdata.
**Det stemte ikke.**

**M-39 måler timer, ikke kroner.** `arbeidsplan.planlagt_minutter_dag`
er minutter; `lonnstaker` har navn og ekstern referanse. Det finnes
INGEN sats noe sted i huset — verifisert mot katalogen: ingen kolonne
heter `timelonn`, `sats`, `maanedslonn` eller noe i den familien
utenfor moms, toll og støtteordninger.

Dette er den SAMME feilformen dette dokumentet selv fanget for M-36
(«leser en KPI-katalog som ikke finnes»), og andre gang i klyngen at
en antakelse ikke overlevde møtet med skjemaet. Lærdommen står:

> **ET FUNDAMENT KAN TILDELE NUMRE OG ROLLER UTEN Å LESE KODEN. DET
> KAN IKKE TILDELE DATA.**

**Konsekvensen, og den gjør modulen bedre:** forpliktelser huset ikke
kan PRISE, registreres av et menneske i `likviditetspost`. Lønn,
husleie, skattetrekk. Da vet vi ALLTID hvem som satte tallet, og
prognosen kan aldri hvile på en utledning ingen har sett.

Det som FAKTISK finnes, verifisert mot basen: `bankkonto`/`bankpost`
(M-13, 101) og `fordring` (M-23, 104). `abonnementsperiode` (M-41,
111) har heller ingen beløpskolonne — `betalingshendelse.forventet_ore`
er det nærmeste, og v1 bruker den ikke: en forventet betaling er ikke
det samme som en forpliktelse noen har bekreftet.

## Tildelte migrasjonsnumre

| Nr | Modul | Fil | Bygges |
|----|-------|-----|--------|
| 128 | M-15 | `128_m15_likviditet.sql` | 1. **Landet 5/9.** |
| 129 | — | `129_m15_ukevindu.sql` | rettelse, se under |
| ~~129~~ **130** | M-33 | `130_m33_prognose.sql` | 2. **Landet 5/9.** |
| ~~130~~ **131** | M-36 | `131_m36_optimalisator.sql` | 3. |

**M-33 OG M-36 FLYTTET ETT HAKK, og grunnen skal stå her — et tildelt
migrasjonsnummer som stille bytter plass er nøyaktig den slags
opplysning ingen finner igjen:** CodeRabbit fant på #391, etter merge,
at penger med forfall NØYAKTIG I DAG falt mellom to uker i M-15s
bane — uke 1 hadde eksklusiv nedre grense, og det finnes ingen
tidligere uke å falle i. Migrasjoner er forward-only, så rettelsen tok
129 (`129_m15_ukevindu.sql`).

**Dette er tredje gang i denne kjeden at en etterfunnet feil skyver et
modulnummer** (125/126 gjorde det samme for klynge 7). Mønsteret er
verdt å se: nummeret er ikke en plan, det er en kø — og en systemisk
feil i det som ALT er merget går foran en modul som ennå ikke finnes.

**Rekkefølgen er begrunnet.** M-15 først fordi den er den mest
KONKRETE: en kontantbane er et tall man kan ta feil av på en målbar
måte, og inngangsdataene finnes allerede. Den etablerer klyngens form
— horisont, modellsnapshot, intervall, måling mot faktisk utfall — på
det tilfellet der «tok vi feil» er lettest å avgjøre.

M-33 deretter, fordi den generaliserer formen til flere måltyper og
legger til baseline-kravet.

M-36 sist, av grunnen over.

## Roller og sveip

| Modul | Eier | Sveip | Klokkeslett (UTC) |
|---|---|---|---|
| M-15 | `disponit_likviditet_eier` | `disponit_likviditetssveip` | 08:35 |
| M-33 | `disponit_prognose_eier` | `disponit_prognosesveip` | 11:35 |
| M-36 | `disponit_optimalisator_eier` | `disponit_optimalisatorsveip` | 11:50 |

**08:35 var det ledige hullet** — det ble tomt da sveipestatus flyttet
for M-48, og klynge 7 lot det stå fordi de fem skulle leses samlet.
M-15 tar det: den er økonomiklyngens, og hører hjemme sammen med
avstemmings- og betalingssveipene tidlig på morgenen, ikke etter
klynge 7.

M-33 og M-36 legger seg BAK klynge 7, og da må
**`disponit-sveipestatus.timer` flyttes fra 11:20 til 12:05** — den
skal lese flåtens tilstand ETTER at flåten har kjørt, og
`test_timeren_gaar_etter_hele_stigen` gjør det til en måling og ikke
en huskeregel.

**GJORT 5/9, I M-33s PR (130).** Se avsnittet under for hvorfor den ventet — og `test_timeren_gaar_etter_hele_stigen` i `test_m33_prognose.py` måler den nå, ved å SAMMENLIGNE klokkeslettene i stedet for å pinne et literal.

**FLYTTINGEN BLE IKKE GJORT I FUNDAMENTET, OG DET VAR RIKTIG.** Dette fundamentet
tildeler klokkeslettene; det installerer ingen timer. Flytter man
sveipestatus nå, står den i halvannen time og leser en flåte som ikke
har fått nye medlemmer ennå — og `test_m54_ehf.py` pinner 11:20 mot
dagens stige, med rette. **Flyttingen hører til M-33s PR**, som er den
første som faktisk legger en timer bak 11:20. Et fundament som endrer
drift for noe som ennå ikke finnes, er et fundament som gjetter.

Fem eiere og fem sveipere av samme grunn som før: en delt sveiperolle
måtte hatt EXECUTE på alle kryss-tenant-definerne, og en feil i én
sveip ville båret de andres fullmakt.

## Stigen begynner å bli lang

**28 nattlige sveip er i drift, og med disse tre blir det 31.**
Stigen går fra 08:20 til 12:05 — nesten fire timer der én treg sveip
skyver dem etter seg.

Det er ikke et problem i dag, og det skal måles før det blir det:
ingen enkelt sveip har brukt mer enn sitt vindu, og hver har
`TimeoutStartSec=10min`. Men grensen er synlig herfra, og den riktige
responsen når den nås er ikke å presse dem tettere — det er å la
sveipene kjøre PARALLELT der de ikke deler rolle. Det står som en
egen sak, ikke som en del av denne klyngen.
