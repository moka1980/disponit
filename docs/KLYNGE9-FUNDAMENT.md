# Klynge 9-fundamentet: ytringene (M-7, M-20, M-43, M-45)

**M-7** møteoperasjonsagent, **M-20** nettside- og innholdsagent,
**M-43** tale- og telefoniagent, **M-45** bærekrafts- og ESG-agent.

Klynge 7 var fem moduler som møter en MYNDIGHET. Klynge 8 var tre som
møter FRAMTIDEN. Denne er fire som møter noe tredje: **et publikum.**

## Den delte dommen

> **EN YTRING AVGITT I HUSETS NAVN KAN IKKE TAS TILBAKE — OG DEN SOM
> LESER DEN VET IKKE AT EN MASKIN SKREV DEN.**

Klynge 7s feilform kunne SLÅS OPP: en foreldet regel finnes et sted,
og noen kan lese den på nytt. Klynge 8s kunne MÅLES: en gal prognose
møter horisonten sin, og da vet vi.

Denne kan ingen av delene. En publisert produktpåstand, en uttalelse i
telefonen, et tall i en bærekraftsrapport, en beslutning i et referat
— de er lest av noen, og det som er lest kan ikke uleses. En rollback
fjerner siden; den fjerner ikke at noen handlet på den.

**Tre av de fire vaktsetningene sier det samme, uavhengig av
hverandre:**

| Modul | Vaktsetningens kjerne |
|-------|----------------------|
| M-20 | «Ingen udokumenterte produktpåstander» |
| M-45 | «Ingen påstand uten datagrunnlag (anti-grønnvasking)» |
| M-43 | «Økonomiske løfter og bindende avtaler krever eksplisitt policy» |

M-7s «lav sikkerhet merkes som ubekreftet» er samme form snudd: der de
tre forbyr en udokumentert påstand, krever M-7 at usikkerheten i det
som er skrevet ned er SYNLIG. Et referat som utelater at maskinen var
usikker, er en påstand om at den ikke var det.

## Funnet ingen kan lukke

| Funn | Modul | Hvorfor |
|------|-------|---------|
| `paastand_uten_kilde` | M-20, M-45 | En påstand uten kilde kan ikke etterprøves, og den som skal svare for den finner ikke hva den hviler på. |
| `opptak_uten_hjemmel` | M-7, M-43 | Et opptak tatt uten grunnlag kan ikke gjøres ugjort, og det er ulovlig i det øyeblikket det starter. |
| `agenten_skjulte_at_den_er_automatisert` | M-43 | Den som tror hun snakker med et menneske, svarer annerledes. |
| `estimat_ikke_merket` | M-45 | Et estimat lest som en måling er grønnvasking, uansett hva som var ment. |

## De fire avklaringene — MÅLT MOT BASEN, IKKE ANTATT

Klynge 8 lærte det tre ganger: **ET FUNDAMENT KAN TILDELE NUMRE OG
ROLLER UTEN Å LESE KODEN. DET KAN IKKE TILDELE DATA.** M-36 skulle lese
en KPI-katalog som ikke fantes, M-15 en lønnssats som ikke fantes, og
M-33 en «historikk» der to av fire kilder ikke fantes.

Derfor er disse fire slått opp i basen før første linje av dette
dokumentet.

### 1. SAMTYKKEREGISTERET FINNES — MEN DET ER MARKEDSFØRINGENS

`samtykkehendelse` (M-44, migrasjon 114) finnes, og et fundament som
stoppet ved navnet ville tildelt den til M-7 og M-43.

**Kolonnene sier noe annet:** `mottaker_id`, `kanal`, `formal`,
`tilstand`. Det er samtykke til å bli KONTAKTET, i en kanal, for et
formål. Den svarer på «har vi lov til å sende dette», ikke på **«har vi
lov til å ta opp denne samtalen»**.

De to er ikke samme spørsmål, og i norsk rett er de ikke engang samme
mekanisme: markedsføringssamtykke er samtykke, mens opptak av en samtale
handler om HJEMMEL og VARSLING — den andre parten skal vite det, og det
finnes grunnlag som ikke er samtykke.

**Avklaringen:** M-7 og M-43 deler ÉN opptakshjemmel, og den er ny.
Registeret bærer hjemmelsgrunnlag, varslingstidspunkt og hvem som ble
varslet. To modeller for samme hjemmel ville gitt to svar på «hadde vi
lov», og det er ett for mange.

**Og den bygges i M-7s runde**, ikke i M-43s: M-7 er fase 1 og kommer
først. M-43 arver den.

### 2. UTKASTFORMEN FINNES FIRE GANGER — OG M-1s ER NØYAKTIG M-20s

Huset har `epost_utkast` (M-6), `anbudsutkast` (M-46), `svarutkast`
(M-17) og `policyutkast` (M-1). Den siste har kolonnene M-20s
vaktsetning ber om, ord for ord:

    basert_pa_versjon, basert_pa_hash, rollback_av_versjon,
    innholds_hash, status, aktivert_revisjon

«Publisering krever policy, forhåndsvisning og automatisk rollback» ER
`policyutkast` + `policyaktivering`, bygget for policyer.

**Avklaringen:** M-20 arver formen og finner ikke opp en femte. Det som
er NYTT for M-20 er kildekravet — en policy hviler på en beslutning, en
produktpåstand må hvile på et DOKUMENT noen kan slå opp.

### 3. M-45 HAR INGEN INNGANGSDATA. INGEN.

Katalogen sier at M-45 «henter energi-, transport-, innkjøps- og
avfallsdata fra kildesystemer». **Ingen av dem finnes.** Verifisert:
ingen tabell i basen har en kolonne som heter `mengde`, `kwh`, `liter`,
`km`, `volum` eller `vekt` i en betydning M-45 kan bruke — det eneste
treffet er `stillingsprofil_krav.vekt`, som er M-57s vekting av
stillingskrav.

Dette er **fjerde gang** i to klynger at et fundament tildelte data det
ikke hadde lest.

**Konsekvensen, og den gjør modulen bedre — akkurat som for M-15:**
mengden registreres av et MENNESKE, med kilde og faktorversjon.
`likviditetspost` finnes fordi huset ikke kan prise lønn; M-45s
tilsvarende register finnes fordi huset ikke måler energi. Da vet vi
alltid hvem som oppga tallet, og rapporten kan aldri hvile på en
utledning ingen har sett.

**Og faktorversjonen er ikke pynt.** Utslippsfaktorer endres mellom
rapportår. Et tall regnet med fjorårets faktor og lest som årets er
feil på nøyaktig den måten CSRD skal hindre. `standardversjon_laast_per_periode`
er derfor en invariant og ikke en anbefaling.

### 4. M-7 HAR EN NABO, IKKE ET FUNDAMENT

M-8 (migrasjon 082) har `m8_slot`, `m8_slotvalg` og
`m8_tidsvalgtoken` — **tidsbooking mot eksterne**, altså «finn et
tidspunkt som passer». Det er ikke møter med agenda, referat og
aksjoner.

**Avklaringen:** M-7 lager sitt eget register, og grensen mot M-8 skal
stå i modulens filhode: M-8 finner tidspunktet, M-7 eier det som skjer
i møtet. En modul som utvidet M-8s slotregister til å bære referater
ville gjort tidsbooking til møteledelse i stillhet.

## Tildelte migrasjonsnumre

| Nr | Modul | Fil | Bygges |
|----|-------|-----|--------|
| 133 | M-7 | `133_m7_moteoperasjon.sql` | 1. |
| 134 | M-20 | `134_m20_innhold.sql` | 2. |
| 135 | M-43 | `135_m43_telefoni.sql` | 3. |
| 136 | M-45 | `136_m45_esg.sql` | 4. |

**Rekkefølgen er begrunnet.** M-7 først fordi den er **den siste
fase-1-modulen i hele katalogen** — katalogens egen rekkefølge sier den
er overmoden — og fordi den bygger opptakshjemmelen M-43 arver. M-20
nest fordi den arver en form som alt finnes og derfor etablerer
kildekravet billigst. M-43 tredje fordi den trenger begge deler. M-45
sist fordi den har mest å registrere og minst å arve.

**ET TILDELT NUMMER ER EN KØ, IKKE EN PLAN.** Fire ganger i klynge
7-8-kjeden skjøv en etterfunnet feil et modulnummer (125/126, 129,
131). Skjer det igjen, flyttes de som står bak, og det skrives HER.

## Roller og sveipeklokkeslett

| Modul | Eier | Sveip | Klokke |
|-------|------|-------|--------|
| M-7 | `disponit_mote_eier` | `disponit_motesveip` | 12:35 |
| M-20 | `disponit_innhold_eier` | `disponit_innholdssveip` | 12:50 |
| M-43 | `disponit_telefoni_eier` | `disponit_telefonisveip` | 13:05 |
| M-45 | `disponit_esg_eier` | `disponit_esgsveip` | 13:20 |

**KLOKKESLETTENE TILDELES HER, MEN INGEN TIMER INSTALLERES HER.**

Og regnestykket må gjøres av hver enkelt PR, ikke av dette dokumentet.
132 lærte det: `RandomizedDelaySec` etablerer ingen rekkefølge. Det som
må gå opp er **START + SPREDNING + `TimeoutStartSec`** for hver
overvåket sveip, målt mot statussveipens TIDLIGSTE start.

Med 15 minutters trinn og 10 minutters timeout betyr det at hver av de
fire må ha `RandomizedDelaySec` på høyst **5 minutter**, og at
`disponit-sveipestatus.timer` må flyttes til **13:35** av M-45s PR —
den siste bak den. `test_sveipen_rekker_aa_bli_ferdig_for_statussveipen_starter`
måler det for hele stigen, så en PR som glemmer det faller.

**STIGEN ER NÅ ET PROBLEM, IKKE BARE EN OBSERVASJON.** Med disse fire
går den fra 08:20 til 13:35 — over fem timer der én treg sveip skyver
alle etter seg. Klynge 8 skrev at «den riktige responsen når grensen
nås er å la sveipene kjøre PARALLELT der de ikke deler rolle». Grensen
er nådd. Det står som en egen sak, og den bør tas FØR klynge 10.

## Fire eiere og fire sveipere

Samme grunn som før: en delt sveiperolle måtte hatt EXECUTE på alle
kryss-tenant-definerne, og en feil i én sveip ville båret de andres
fullmakt.

## Hva klyngen IKKE deler

M-45 skiller seg på ett punkt, og det skal stå: **den er den eneste av
de fire som rapporterer til en MYNDIGHET** (CSRD/ESRS). Det gjør den
til en klynge 7-modul i forkledning — regelen er ikke vår, og den
endres uten å si fra. `standardversjon_laast_per_periode` er derfor
klynge 7s dom («en foreldet regel ser nøyaktig ut som en riktig
regel») anvendt på rapporteringsstandarden selv.
