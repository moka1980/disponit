# M-4 Retensjon — tastaturgjennomgang (klarsignalet §8)

## Hvor gjennomgangen faktisk er gjort

**Flaten står i sitekartet fra og med denne endringen.** Oppføringen
`{ nokkel: "retensjon", scope: "security:read" }` står i `BASISRUTER`
(`platform/core/ui/static/js/sitekart.js`) i SAMME deployerbare endring
som ruten `Route("/v1/retensjon", …)` og `RUTESCOPE`-raden i
`api/app.py`. Det er hele grunnen til at rad 1 under kan stå som PORTET
og ikke som UTESTÅENDE: en menyoppføring uten et serverendepunkt bak seg
er en flyt ingen kan gå, og en gjennomgang av den er falsk evidens.

**Menyveien går gjennom TOPPNAVIGASJONEN.** M-4 er en plattforminternflate
— husets eget retensjonsregnskap — og følger #315-presedensen som
`modellstyring` (M-31) og `driftstatus` (M-10/M-11): ingen
modulflate-flipp, ingen `modulflate`-nøkkel, ingen venstremeny. Eiers
arkitekturvedtak 24/8 gjelder ordrett: venstre er modulnavigasjonen,
toppen bærer plattformflatene. Scopet i sitekartet er det SAMME som
API-et bak flaten krever (`security:read`) — er de to ulike, lover menyen
en flate serveren svarer 403 på.

**Kontrollplanet er IKKE en egen rute.** `platform:admin` avgjøres inne i
endepunktet (`/v1/utrulling`-presedensen), fordi scopet ikke står i
`LESESCOPES` og en browserøkt mot et scope utenfor det settet avvises i
`_autentiser`. En rute deklarert `platform:admin` ville gitt 403 for hver
eneste innlogging. For tastaturgjennomgangen betyr det at det finnes ÉN
adresse og én tabindeksrekkefølge — ikke to flater å gå gjennom.

Resten av gjennomgangen er gjort på **flatens egen modul i jsdom-brettet**
(`platform/core/ui/test/retensjon.test.js`), der `visRetensjon` monteres
direkte i et `<main id="hovedinnhold">` med stubbet transport — samme tre
som en økt ville fått, uten ruteren foran. Flytene under er merket ærlig:
**PORTET** betyr at en test i suiten feiler hvis flyten ryker,
**KOMPONENTPORTET** at mekanismen er portet der den bor, og
**UTESTÅENDE** at flyten hører til ruteren og først kan gjennomgås når
ruten er inne.

**Flaten muterer ingenting, og det er en dom — ikke en mangel.** Det
finnes ingen skjemakontroller, ingen bekreftelsesdialog og ingen
farlige handlinger å felle. Registerets dommer felles i migrasjon 093, og
målingen skrives av `disponit-lagermaaling.service` med sin egen rolle.
Den ENESTE interaktive kontrollen på hele flaten er
sorteringsknappene i lagertabellens `<th>`, og en egen test asserterer
at det ikke finnes noen annen knapp.

Sist gjennomgått: M-4 v1 (migrasjon 093), gren `m4-retensjonsregister`.

## Flytene

| # | Flyt | Tastene | Forventet — og observert | Status |
|---|---|---|---|---|
| 1 | Nå flaten | `Tab` til «Retensjon» i **toppnavigasjonen**, `Enter` | Menyoppføringen finnes for en `security:read`-økt, og `#/retensjon` rendrer retensjonsflaten — ikke reserveflaten. Fokus lander i hovedinnholdet; overskriften leses | **PORTET** — `sitekart.test.js`: «byggRuter: hver rute krever scopet API-et bak flaten krever» asserterer både at ruten er i `byggRuter` for en `security:read`-økt og at `tillatteFlater` slipper flaten gjennom (uten begge lander `#/retensjon` på reserveflaten Oversikt), og «Hver rute byggRuter kan gi har en nav-etikett i BEGGE locale-sett» asserterer at `ui.nav.retensjon` finnes i nb og en. Fokusflyttingen selv er ruterens generelle atferd (`ruter.js`, `hoved.focus()` ved hver navigasjon som ikke er første tegning) — delt av alle ruter |
| 2 | Lese **målingens hode** | `Tab` passerer det | Kortet er `<dl>`/`<dt>`/`<dd>` uten fokuserbare elementer, og står FØRST i lesrekkefølgen. Er siste kjøring avbrutt, står det som en SETNING i kortet — ikke som et manglende merke | **PORTET** — «en AVBRUTT kjøring SIER det med tekst» (assertens motpart, «fullført kjøring sier DET, med sin egen setning», feller en flate som bare utelater den avbrutte teksten). Rekkefølgen er portet ved at hodet er første barn i `kpi-kort-liste` |
| 3 | **Sortere** lagertabellen | `Tab` til kolonneknappen (f.eks. «Klasse»), `Enter`/`Mellomrom` | `aria-sort` på det aktuelle `<th>` veksler `none` → `ascending` → `descending`; radene snur; **fokus blir stående på knappen** fordi bare `<tbody>` tegnes på nytt | **PORTET** — «sortering setter aria-sort og beholder tastaturfokus»: testen fokuserer knappen, klikker, og asserterer BÅDE `aria-sort`-verdien og at `document.activeElement` fortsatt er knappen. Mutasjonen «bygg `thead` på nytt ved sortering» rødner den — det er et reelt a11y-tap, ikke en kosmetisk detalj |
| 4 | Lese **et lager** i tabellen | `Tab` passerer radene | Lagernavnet er `<th scope="row">`, kolonnene `<th scope="col">`, og tabellen har `<caption>`. En skjermleser i tallkolonnene kan si hvilket lager tallet gjelder. Dommen står som TEKST med sin begrunnelse under, aldri som et fargemerke | **PORTET** — «register, dom som tekst, tabellsemantikk, axe rent» asserterer `caption`, `th[scope=col]`, `th[scope=row]` på hver rad, og at begge dommene («Under frist», «Bevisst uten frist») samt begrunnelsen står som tekst |
| 5 | Lese et lager som **ikke telles** | `Tab` passerer det | Et lager uten reap-markør står som «ikke talt (ingen reap-markør)» — ALDRI som en tom celle en leser kan lese som null. Modulens bærende regel gjelder også presentasjonen | **PORTET** — samme test asserterer `ui.retensjon.ikke_talt` i tabellteksten |
| 6 | Lese **katalogtallene** (kun `platform:admin`) | `Tab` passerer tabellen | Radestimatet står med ordet «(estimat)» som TEKST ved siden av tallet — aldri kursiv eller farge alene. `reltuples` er ANALYZE-ens siste gjetning, og et tall som ser ut som en telling blir lest som en | **PORTET** — «platform:admin ser funn og estimattall MERKET som tekst». Motparten «security:read ser verken funnliste eller katalogtall» feller en flate som viser dem uten fullmakt |
| 7 | Lese **funnlisten** (kun `platform:admin`) | `Tab` passerer tabellen | Funntypen står som TEKST («Uregistrert lager», «Kunne ikke måles», …), aldri som et trafikklys alene. En tom funnliste sier «Ingen åpne funn» — den ser ikke ut som manglende tilgang | **PORTET** — samme test for funntypen, og «tom funnliste er IKKE det samme som ingen tilgang» for skillet mellom `[]` og `null` |
| 8 | **Ingen mutasjon** å utløse | `Tab` gjennom hele flaten | Den eneste knappen som finnes er sorteringen. Det finnes ingen skjemakontroll, ingen «Slett», ingen bekreftelsesdialog — flaten endrer ingenting | **PORTET** — «flaten muterer ingenting — ingen knapp utenom sortering» går gjennom hver `<button>` i treet og krever `class="sort-knapp"`. Serversiden er portet i `test_m4_retensjon.py::test_endepunktet_krever_scope_og_har_ingen_skrivevei` (POST/PUT/DELETE/PATCH gir 405) |
| 9 | **Tomtilstandene** | — | «Ingen måling er registrert ennå» og «Registeret er tomt» er eksplisitt innhold, ikke en tom side | **PORTET** — «tomtilstand og manglende måling er eksplisitt innhold» |

Ingen av flytene krever mus eller pekerpresisjon; ingen informasjon bæres
av farge alene — dommen, funntypen, estimatmerket og den avbrutte
kjøringen står alle som tekst, og flaten setter ingen fargeklasse på
noen av dem.

Axe-porten kjøres i to av testene over (hovedformen og
`platform:admin`-formen) med `alvorligeBrudd(h, { fragment: true })` og
krever null brudd med alvorsgrad `serious` eller `critical`.

Dokumentet er det porten `ui.tastaturgjennomgang_dokumentert` krever. Det
er verdiløst hvis det påstår mer enn det som er målt: en gjennomgang som
sier «observert» om en knapp som ikke gjorde noe, er falsk evidens. Står
en flyt som UTESTÅENDE her, er det fordi den ikke KAN gjennomgås ennå —
ikke fordi den ble hoppet over.
