# Klynge 10-fundamentet: handlingene (M-28, M-29, M-32, M-40)

**M-28** logistikk- og transportagent, **M-29** sikkerhets- og
hendelsesagent, **M-32** global lokaliserings- og skatteagent,
**M-40** HR- og medarbeideragent.

Klynge 7 var fem moduler som møter en MYNDIGHET. Klynge 8 var tre som
møter FRAMTIDEN. Klynge 9 var fire som møter et PUBLIKUM. Denne er de
fire som møter VERDEN SELV.

## Den delte dommen

> **EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN ANGRES IKKE AV EN
> ROLLBACK.**

Klynge 9s ytring kunne ikke tas tilbake fordi noen hadde LEST den.
Denne klyngens feilform trenger ingen leser: pakken er hentet, kontoen
er stengt, skatten er innberettet, kontrakten er signert. Databasen
kan rulles tilbake til sekundet før — og bilen kjører fortsatt.

**Alle fire vaktsetningene holder tilbake nøyaktig den fullmakten
modulen ser ut til å trenge:**

| Modul | Vaktsetningens kjerne | Fullmakten som holdes tilbake |
|-------|----------------------|-------------------------------|
| M-28 | «ingen ulovlig ruteoptimalisering»; «samme kolli bestilles aldri to ganger» | Å BESTILLE transporten |
| M-29 | «kill-switch, tofaktor for utvidet inngrep, forhåndsdefinerte playbooks; **ingen fri kommandokjøring**» | Å ISOLERE kontoen og ROTERE hemmeligheten |
| M-32 | «landlansering er deaktivert uten komplett og testet landpakke; usikker jurisdiksjon **stopper transaksjonen**» | Å INNBERETTE skatten |
| M-40 | «ingen automatiske beslutninger med rettsvirkning for enkeltpersoner» | Å AVGJØRE noe om et menneske |

Det er ikke fire tilfeldig like formuleringer. Det er den samme
setningen fire ganger: *modulen er bygget for å handle, og v1 handler
ikke.*

## Funnet ingen kan lukke

| Funn | Modul | Hvorfor det aldri kan reises |
|------|-------|------------------------------|
| `kolli_bestilt_to_ganger` | M-28 | Datamodellen gir én frigivelse per kolli. Funnet står i settet fordi det NAVNGIR skaden; at det er umulig er beviset. |
| `inngrep_uten_playbook` | M-29 | v1 har ingen inngrepsvei. Et inngrep uten playbook krever et inngrep. |
| `fri_kommando_kjort` | M-29 | Det finnes ingen dør som tar en kommandostreng. |
| `transaksjon_uten_jurisdiksjon` | M-32 | Skatt kan ikke beregnes uten at jurisdiksjonen er slått fast; kolonnen er NOT NULL. |
| `beslutning_med_rettsvirkning` | M-40 | v1 har ingen beslutningsdør. |
| `puls_identifiserte_en_person` | M-40 | Svarene bærer ingen personnøkkel — det er en egenskap ved BASEN, ikke ved disiplinen. |

Formen er klynge 9s, og den har nå gjentatt seg i åtte moduler: **skriv
funnet inn i det lukkede settet, og skriv en port som måler at
datamodellen utelukker det.** Et sett som ikke navnga dem ville ikke
sagt noe; et sett som navnga dem og kunne fylles ville sagt at vernet
er en sveip.

## De fem avklaringene — MÅLT MOT BASEN, IKKE ANTATT

Klynge 8 lærte det tre ganger, klynge 9 to ganger til: **ET FUNDAMENT
KAN TILDELE NUMRE OG ROLLER UTEN Å LESE KODEN. DET KAN IKKE TILDELE
DATA.**

Basen ble migrert til 136 og spurt før første linje av dette
dokumentet. 332 tabeller. Her er hva den svarte.

### 1. M-28 HAR INGEN ORDRE, INGEN KOLLI OG INGEN TRANSPORTPRIS

Katalogen sier inndata er «ordre, lagerlokasjon, adresse, kolli, SLA og
transportpriser». **Tre av seks finnes ikke.**

| Inndata | Finnes? | Hva basen faktisk har |
|---|---|---|
| Ordre | **NEI** | `bestillingsplan` er en RYTME (`rytme`, `ukedag`, `manedsdag`, `time_lokal`) for gjentakende bestillinger — ikke en ordre med varelinjer og en leveringsadresse. `bestillingspunkt` (M-27) er et bestillingsPUNKT: en terskel. |
| Lagerlokasjon | delvis | `lagerbevegelse` (M-27, 109) har `vare_id` og `endring`, men **ingen lokasjon**. Huset teller beholdning; det vet ikke hvor den står. |
| Adresse | **JA** | `adressesubjekt`/`adresseversjon`/`adressekontroll` (M-19). Validering med versjonshistorikk. |
| Kolli | **NEI** | Målt: ingen kolonne i noen av de 332 tabellene heter `vekt`, `volum`, `dimensjon`, `hoyde`, `bredde`, `lengde`, `fareklasse` eller `kolli` i en brukbar betydning. Eneste treff er `stillingsprofil_krav.vekt` — M-57s vekting av stillingskrav. |
| SLA | **JA** | `leveranseavtale.sla_type` + `leveranse` (M-24). |
| Transportpriser | **NEI** | Ingen prisliste, ingen transportør, ingen carrier-connector. |

**Konsekvensen, og den er den samme som M-15s og M-45s:** kolliet
registreres av et MENNESKE, med mål, vekt og fareklasse. Modulen
PLANLEGGER mot registrerte kolli og registrerte adresser, og
`transportforslag` er et forslag med begrunnelse — ikke en booking.

Og det gjør modulen ærligere, ikke fattigere: vaktsetningen sier
«farlig gods, toll og persondata følger land- og transportørregler».
En modul som utledet fareklassen av en produktbeskrivelse ville
PÅSTÅTT noe om farlig gods. Nå oppgir et menneske den, og vi vet alltid
hvem.

### 2. M-29 SKAL ROTERE HEMMELIGHETER SOM ALLEREDE LIGGER I BASEN

Dette er klyngens farligste avklaring, og den går motsatt vei av de
andre: **inndataene mangler, men FULLMAKTSMÅLENE finnes.**

Mangler: SIEM, IdP, EDR, sårbarhetsskanner, aktivaregister. Ingen av
dem finnes, og ingen av dem kan finnes uten en utgående integrasjon
huset ikke har.

Finnes: `api_tokener` (`secret_mac`, `status`, `utloper`),
`modultoken`, `tenant_pseudonymnokkel`, `brukersesjon`,
`brukeridentitet`. **Det er nøyaktig de radene en «roter secrets og
isoler konto, token eller workload»-modul ville skrevet i.**

En modul som fikk `UPDATE` på `api_tokener` ville kunne stenge huset
ute av seg selv, og den ville gjort det raskere enn noe menneske rakk
å lese logglinjen. `revisjonshendelse` (M-2) er den ENESTE
applikasjonsloggen som finnes — og en modul som både leser
revisjonsloggen og kan handle på den, er en modul som kan handle på sin
egen forrige handling.

**Avklaringen: M-29 v1 får LESERETT og ingen skriverett utenfor sine
egne funnrader.** Den korrelerer, scorer med forklarbare regler, og
gjør et inngrep uten playbook til et FUNN. Isolering og rotasjon
fortsetter å skje nøyaktig der de skjer i dag — for øyeblikket ved at
et menneske gjør det.

Vaktsetningens «ingen fri kommandokjøring» er ikke en policy her. Den
er en egenskap ved dørene: **ingen dør i 138 tar en kommandostreng.**

### 3. M-32 HAR ÉN SKATTESATS, OG DEN ER TENANTENS EGEN

`mvasats` finnes — bygget av M-14 (106, fakturakontroll) — med
kolonnene `tenant, sats_kode, promille, gyldig_fra, gyldig_til`.

Et fundament som stoppet ved navnet ville tildelt den til M-32 og
kalt landpakken bygget. **`tenant`-kolonnen sier noe annet:** dette er
satsen KUNDEN har registrert for seg selv, i sitt eget land. Det er
ikke en landpakke — det er en enkelt bedrifts oppfatning av sin egen
mva.

Dette er samme feltype som klynge 9s samtykkeregister, tredje gang nå:
riktig navn, feil spørsmål.

**Avklaringen:** M-32 bygger et GLOBALT, tenantløst landregister
(M-31s plattformregisterform, som M-4s `retensjonslager`): dommene
felles i git, ikke gjennom en dør. `mvasats` blir stående som
tenantens egen og røres ikke.

**Og «landlansering er deaktivert uten komplett landpakke» er ikke en
sjekk — det er en tilstand i registeret.** Et land uten komplett pakke
har ingen rad, og en transaksjon mot et land uten rad får ingen sats.
Den stopper, som vaktsetningen sier den skal.

### 4. M-40s ANSATTREGISTER FINNES — OG DET ER M-39s

`lonnstaker` (M-39) har `tenant, taker_id, ekstern_ref, navn, aktiv`.
**Det er husets eneste register over mennesker som jobber i bedriften.**

Dette er den motsatte feilen av de tre over: her ville et fundament som
leste katalogen og ikke basen bygget et ANDRE ansattregister. To
registre over de samme menneskene gir to svar på «jobber hun her», og
det er ett for mange — nøyaktig argumentet som ga M-7 og M-43 én delt
opptakshjemmel.

**Avklaringen: M-40 arver `lonnstaker` og bygger ikke et nytt.** Det
har en konsekvens som er verdt å skrive ned: M-40 blir da avhengig av
en modul som selv er `bygges`. Det er akseptabelt fordi avhengigheten
er en TABELL med en stabil nøkkel, ikke en oppførsel.

**OG ÉN TIL, MÅLT OG IKKE ANTATT:** `lonnstaker` eies av
`disponit_migrator`, ikke av `disponit_lonn_eier` — 113 lager den uten
`SET LOCAL ROLE`. Leseretten til M-40 må derfor gis AV MIGRATOR, ikke
av lønnseieren. Det er den samme fella 133 gikk i mot
`krev_tenantkontekst`, der granten måtte komme fra
`disponit_m37_claimer` og ikke fra migrator: **en REVOKE eller GRANT
fra en som ikke eier objektet er en FEIL, ikke et stille null-tiltak.**

**`onboardinglop` (M-18) er IKKE medarbeideronboarding.** Kolonnen
heter `kunde_ref`. M-18 er kundens innføring i produktet; M-40s er den
ansattes første uke. Fjerde gang samme felle i to klynger — og den ble
igjen avverget av å lese kolonnene og ikke navnet.

**Malene finnes, og de er låst.** `malfamilie`/`malversjon`/`malfelt`/
`malkomponent` (M-5) er nøyaktig det vaktsetningens «juridiske
klausuler er låste» og akseptansekravets «kontrakter kan alltid spores
til malversjon og kildefelt» ber om. M-40 arver, og finner ikke opp en
femte utkastform — samme dom som M-20 fikk.

### 5. PULSSVARENE ER KLYNGENS EGENTLIGE VANSKELIGHET

«Anonymiserte pulsmålinger aggregert på gruppenivå» med «minste
gruppestørrelse» er det eneste kravet i klyngen som **ikke kan
oppfylles av å holde tilbake en fullmakt.** Modulen må faktisk lagre
svarene, og de må faktisk være uidentifiserbare.

Og anonymitet er ikke en egenskap ved én rad. Den er en egenskap ved
SETTET: fire svar fra en gruppe på fire er anonyme hver for seg og
fullt identifiserende til sammen, og en gruppe som krymper fra åtte til
tre gjør gårsdagens anonyme svar identifiserbare i dag.

**Derfor er k-anonymiteten en INVARIANT i basen og ikke en sjekk i en
dør:** et svar bærer ingen `taker_id`, aggregatet nekter å svare under
terskelen, og terskelen er lagret sammen med målingen — ikke lest fra
en konstant ved lesetidspunktet. En terskel som kan endres i ettertid
er ingen terskel.

## Det som IKKE er en avklaring: M-30

`docs/M53-M30-GRENSESNITTET.md` avklarte det allerede, og svaret
gjelder her: **M-30 SLETTER IKKE.** Den er et register over
forespørsler. Sletting skjer der lageret eies, styrt av M-4s
`retensjonslager` (093).

M-40 skal derfor ikke ha et eget grensesnittdokument mot M-30. Den skal
gjøre det M-53 gjorde: registrere hvert av sine lagre i
`retensjonslager` med `dom`, `reaper`, `reapetkolonne` og `fristkilde`.
Pulssvarene får `anonym_ved_fodsel` som dom — det er den ene av husets
domsformer som betyr «det finnes ingen persondata å slette her», og den
må kunne BEVISES av kolonnene og ikke påstås av dommen.

> Klyngerekkefølgen sa at M-40 trengte et eget grensesnittdokument slik
> M-53 gjorde. Etter å ha lest 093 og 099 er det ikke riktig: M-53
> trengte det fordi den er et lager med OPPBEVARINGSPLIKT som møter en
> slettesak. M-40s pulssvar er det motsatte — et lager uten persondata
> i det hele tatt. **M-40 bygges likevel SIST**, av en annen grunn: den
> er den eneste som rører enkeltmennesker som er ansatt hos kunden.

## Tildelte migrasjonsnumre

| Nr | Modul | Fil | Bygges |
|----|-------|-----|--------|
| 137 | M-29 | `137_m29_hendelse.sql` | 1. |
| 138 | M-32 | `138_m32_skatt.sql` | 2. |
| 139 | M-28 | `139_m28_transport.sql` | 3. |
| 140 | M-40 | `140_m40_medarbeider.sql` | 4. |

**Rekkefølgen er begrunnet, og den er ikke katalogens fase.**

**M-29 først** fordi den er den eneste der fullmaktsmålene ALLEREDE
LIGGER I BASEN. De andre tre må vente på data som ikke finnes; M-29
kunne skrevet i `api_tokener` i morgen. Tilbakeholdelsen er billigst å
etablere når den er skarpest, og den setter formen for de tre andre.

**M-32 nest** fordi den bygger det globale landregisteret, og fordi den
er den minste: ett register, én invariant («ingen sats uten komplett
pakke»), null utgående handling.

**M-28 tredje** fordi den trenger et menneskeregistrert kolli, og fordi
den bør arve M-32s landregister: «farlig gods og toll følger LANDregler»
er nøyaktig det registeret M-32 lager. En M-28 bygget først ville laget
sitt eget landbegrep.

**M-40 sist** — den rører mennesker.

**ET TILDELT NUMMER ER EN KØ, IKKE EN PLAN.** Fem ganger i klynge
7-8-9-kjeden skjøv en etterfunnet feil et modulnummer (125/126, 129,
131). Skjer det igjen, flyttes de som står bak, og det skrives HER.

## Roller og sveipeklokkeslett

| Modul | Eier | Sveip | Klokke |
|-------|------|-------|--------|
| M-29 | `disponit_hendelse_eier` | `disponit_hendelsessveip` | 06:55 |
| M-32 | `disponit_skatt_eier` | `disponit_skattesveip` | 07:00 |
| M-28 | `disponit_transport_eier` | `disponit_transportsveip` | 07:05 |
| M-40 | `disponit_medarbeider_eier` | `disponit_medarbeidersveip` | 07:10 |

**KLOKKESLETTENE TILDELES HER, MEN INGEN TIMER INSTALLERES HER.**

Og de er ikke gjettet. Stigen ble strammet 5/9 (trinn 5, spredning 4,
`AccuracySec=1us`), og `test_stigen_har_plass_til_klynge_ti` regnet
plassen ut PÅ FORHÅND: siste trinn 06:50 + fire trinn = 07:10, pluss
spredning 4 og `TimeoutStartSec` 10 = **07:24**, og statussveipen står
07:30.

Det er forskjellen på en grense som er valgt og en som blir oppdaget av
den som bygger den fjerde modulen. **Hver PR må likevel gjøre
regnestykket selv** — `test_sveipen_rekker_aa_bli_ferdig_for_statussveipen_starter`
måler det for hele stigen, og en PR som glemmer det faller.

## Fire eiere og fire sveipere

Samme grunn som før: en delt sveiperolle måtte hatt EXECUTE på alle
kryss-tenant-definerne, og en feil i én sveip ville båret de andres
fullmakt.

## En rettelse M-29s migrasjon skal ta med seg

`m36_funnregister` (132) har raden

    ('retensjonsfunn', 'm29_retensjon', 'funntype', 'lukket_maaling_null', …)

**`retensjonsfunn` er M-4s** — bygget i 093 (`m4_retensjonsregister`).
`m29` i modulnavnet er feil, og med denne klyngen blir det en KOLLISJON:
M-29 er sikkerhets- og hendelsesagenten, ikke retensjon.

Kolonnen har ingen referanseintegritet og har ikke ødelagt noe. Men
merkelappen er det eneste stedet et funnregister sier hvem det tilhører,
og en sveip som grupperte på den ville tilskrevet M-4s funn til M-29.

**Rettes i 137** med en `UPDATE` — ikke i dette dokumentet, som ikke
eier migrasjoner.

## Klynge 10 LUKKER KATALOGEN

Dette ble ikke planlagt, det ble oppdaget: da de fire manifestene var
skrevet, hadde **alle 57 modulene i katalogen et manifest.** Nummer
1 til 57, uten hull.

Spesifikasjonen sier «katalogen fryses på 57». Fra og med denne
klyngen er det ikke lenger en påstand om en plan — det er en tilstand
i repoet.

Det tok en port med seg. `plattformdata.test.js` hadde siden 5/9 brukt
**M-28 som stedfortreder for «en ekte modul som ennå ikke er
registrert»**, med kommentaren «M-28 hører til klynge 10 og flyttes DA
— ikke før». Den stedfortrederen finnes ikke lenger, og det finnes
ingen å erstatte den med.

Testen låner derfor ikke lenger en ekte modul, og en NY port måler det
som faktisk gjelder nå: **57 id-er, ingen hull, hver med en rad i
`MODULSTATUS`.** Porten måler HULLET og ikke antallet — en katalog kan
telle 57 og likevel mangle M-32 hvis noen la til en M-58, og det er
nøyaktig den feilen et frossent tall skjuler.

## Hva klyngen IKKE deler

M-40 skiller seg på ett punkt, og det skal stå: **den er den eneste av
de fire der den skadelidte er en PRIVATPERSON som ikke er kunde.** M-28
skader en forsendelse, M-29 skader driften, M-32 skader et regnskap.
M-40 skader en ansatt hos kunden — en som aldri har sett produktet,
aldri samtykket til noe, og ikke kan klage til noen.

AI Act-grensen i vaktsetningen er derfor ikke en compliance-linje som
er lagt til. Den er hele grunnen til at modulen ser slik ut.
