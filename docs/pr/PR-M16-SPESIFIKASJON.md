# SPESIFIKASJON — M-16 Nøkkeltall (KPI-flaten), v1

**Draft: Claude.ai · Full sti: `docs/pr/PR-M16-SPESIFIKASJON.md`.
Grunnlag: lesesvar 2026-08-21 mot main `5f2e233` og prod. Stående porter
SP-1…SP-13. Migrasjon: **ingen** — v1 er ren lesing; ett unntak er
flagget i §6 og bestilles ikke her.**

---

## 1. Grensen som definerer modulen

**M-16 viser hva Disponit faktisk gjorde. Den analyserer ikke.**

«Nøkkeltall regnet fra faktiske beslutninger» betyr, konkret og
testbart:

- Hvert tall er en telling over rader som finnes, eller en **radvis**
  differanse (én saks varighet). **Ingen andeler, gjennomsnitt eller
  medianer i v1** — divisjon er et tolkningslag (M16-2). Ingen glatting,
  ingen interpolasjon, ingen trendlinje, ingen prognose.
- **Ett datapunkt vises som ett punkt** — aldri som en «trend».
  Prod har i dag 1 aktivering; den skal se ut som 1.
- **Tomme vinduer vises tomme**, som eksplisitt null — ikke skjult,
  ikke utjevnet. Datamengdene er små og skjeve (én tenant-familie,
  testtenanter), og §1-grensen er det som hindrer pynting.
- Tall som krever et tolkningslag mellom basen og skjermen hører ikke
  hjemme i v1. Diffen mellom «vise det som skjedde» og «analysere det
  som skjedde» er denne paragrafen.

## 2. Kildene og kortene

Fire kort i v1, alle fra eksisterende tabeller og leseveier:

| Kort | Kilde | Tall |
|---|---|---|
| **Beslutninger** | `revisjonslogg` (192 TILLAT / 12 UNNTAK / 0 STOPP i prod) | Antall per beslutning per vindu; totalen |
| **Policyaktiveringer** | `policyaktivering` (1 rad) | Aktiveringer per vindu; per kilde; per kvorumklasse; antall med 1 / med 2 attestanter (rå tellinger — ingen andel, M16-2) |
| **Oppdrag** | `oppdrag` (29 utført / 25 feilet / 1 opprettet) | Utfall per vindu (`status_ts`) |
| **Unntakskøen** | `unntak` — **kun metadatafeltene** | **To akser, aldri blandet (M16-4):** *aktivitet i vinduet* — opprettet i `[fra, til)` etter `ts`, lukket i `[fra, til)` etter `status_ts`, per kategori; og *nåtilstand* — åpne saker **nå**, egen rå teller uttrykkelig upåvirket av vindusvelgeren. Hver lukket saks faktiske varighet `status_ts − ts` som radfakta (M16-2) |

`frekvens_hendelser` (42 rader) tas som femte tall *inne i*
beslutningskortet (reservasjoner per vindu) — ikke eget kort i v1.

**Tick-kortet (planer) er med, ærlig tomt:** prod har 0 rader, og kortet
viser «ingen planer kjørte i det valgte vinduet» som tekst — ikke en tom
graf som ser ødelagt ut. Setningen er skopet til VINDUET: kortet teller
aktivitet i `[fra, til)`, og 0 der sier ingenting om tiden før. En
påstand om at ingen planer noen gang har kjørt ville krevd en egen
all-tid-telling, og den finnes ikke i v1. Lesing skjer uten ny indeks;
volumet er null (§6).

## 3. Én vindusdefinisjon, én suminvariant

- **Ett vindusbegrep for alle kort** (lesesvarets flagg 3): UTC i basen,
  **halvåpne intervaller** `[fra, til)`, tidssone kun som presentasjon —
  presedensen fra 24-timers-sammendraget. Én delt hjelpefunksjon; ingen
  kort regner sitt eget vindu. Tidsstemplene mappes eksplisitt per kort:
  beslutninger på `ts`, aktiveringer på `aktivert_ts`, oppdrag på
  `status_ts`, saksaktivitet på `ts` (opprettet) og `status_ts`
  (lukket).
- **Aktivitet i vindu ≠ tilstand ved tidspunkt (M16-4).** «Opprettet i
  vinduet» og «lukket i vinduet» er hendelser; «åpne saker» er
  tilstand. v1 viser tilstanden kun som **«åpne nå»** — en egen rå
  teller utenfor vindusvelgeren. «Åpne ved `til`» tilbys ikke: dagens
  tabell bærer ikke hendelseshistorikken som skulle bevist historisk
  tilstand, og å utlede den fra dagens status ville vært nettopp et
  tolkningslag. En sak opprettet før vinduet og fortsatt åpen telles
  aldri som aktivitet i vinduet, men inngår alltid i «åpne nå».
- **Tick-kortets tidsanker er forfallet, ikke skrivetidspunktet
  (M16-4/SP-6):** når planer får data, tilhører et tick vinduet der
  **`vindu_start`** (planens forfallsidentitet) ligger — aldri
  `registrert`, som bare sier når raden tilfeldigvis ble skrevet.
  Periodetilhørighet måles på riktig tidsanker; det står nå i
  spesifikasjonen så Claude Code aldri velger det under bygg.
- **Suminvarianten gjelder per partisjon, ikke per kort** (M16-1): en
  partisjon er en gruppe **gjensidig utelukkende** kategorier —
  beslutning per utfall, aktiveringer per kilde, aktiveringer per
  kvorumklasse, oppdrag per status, saker per kategori. Hver partisjon
  telles fra **samme skann** (`COUNT(*) FILTER (WHERE …)`,
  lesing.py-mønsteret) og summerer til sin egen total. Et kort kan bære
  flere partisjoner; de summeres aldri på tvers. Målinger som ikke er
  delmengder (radvise varigheter) har sin egen navngitte kontrakt (§2)
  og inngår ikke i noen suminvariant.
- **Beslutningsdomenet er repoets, ikke mitt** (M16-3): den lukkede
  mengden hentes fra `revisjonslogg`-enumen slik 24h-sammendraget alt
  bruker den — lesesvaret viser TILLAT/STOPP/UNNTAK som faktiske
  verdier i prod, og det er *den* mengden som brukes i kortet,
  partisjonen, port 1, fixtures og UI-etiketter. Jeg antar ingen verdi:
  klarsignalet skriver mengden ordrett fra enum-CHECK-en, og **en ukjent
  beslutningsverdi skal telles synlig som «ukjent», aldri stille falle
  utenfor totalen** (fail-closed for tellinger).

## 4. API — generalisering av det som finnes

`GET /v1/nokkeltall?vindu=24t|7d|30d` bak samme scope som lesingen ellers
(`decisions:read`), implementert som **generaliseringen av
24h-sammendraget**: samme filtertelling over valgbart vindu. Mønsteret
er allerede Codex-herdet; v1 gjenbruker det framfor å finne opp et
nytt.

- **Vinduet velges med `vindu`, ikke med et fritt intervall.** Den ENE
  parameteren er `vindu` ∈ {`24t`, `7d`, `30d`} (utelatt = `24t`); en
  ukjent verdi er `400 request_feilformet`. Fritt `fra`/`til` er
  bevisst ikke implementert i v1 (§3: forhåndsdefinerte vinduer), og et
  kall som likevel sender dem **avvises med 400** framfor å få et
  urelatert 24-timerssvar med status 200. Et eksplisitt spørsmål skal
  ikke kunne besvares stille med noe annet.
- Alle spørringer via definere med `tenant = p_tenant` og
  `krev_tenantkontekst` (SP-1); flaten leser aldri tabellene direkte
  (SP-7).
- **v1 er tenant-skopet.** Plattformbred aggregering på tvers av
  tenanter er utenfor v1 — det er en annen tillitsflate.
- `unntak`-payloaden er kryptert og **røres ikke**: kun
  metadatakolonnene leses, og porten tester statisk at ingen
  dekrypteringsvei kalles fra nøkkeltallsveien.
- Keyset-cursor gjenbrukes der lister vises (beslutningslisten finnes
  alt som `/v1/beslutninger`; kortet lenker dit i stedet for å bygge en
  ny liste).
- **Radgrensen er aldri stille.** Lukkede-listen er radfakta med et
  visningstak (`unntak_lukkede_grense`, 50 rader), og svaret bærer
  ALLTID hele tellingen i vinduet ved siden av
  (`unntak_lukkede_totalt`, fra samme skann som radene — `count(*)
  OVER ()` før `LIMIT`). Er settet større enn taket, sier flaten det i
  klartekst og lenker til unntakslisten; et utsnitt presenteres aldri
  som «alle saker lukket i vinduet».

## 5. Flaten — første graf-flate, WCAG-kontrakt fra første commit

Lesesvarets svar 5 er styrende: dette er første grafarbeid i UI-et, og
den ærligste formen for dagens datamengder er **tellere + søylerader per
vindu, ikke kurver**.

- **Tabellen er tilgangsformen; søylene er progressiv forsterkning.**
  Hvert kort er en ekte `<table>` (`<caption>`, `<th scope>`) med
  tallene i tekst; søyleraden er HTML/CSS-bredder ved siden av
  tallet — ikke SVG-kurver, ikke canvas. Skjermlesere får tabellen;
  ingen informasjon finnes bare i søylen.
- **Aldri farge alene:** beslutningstype står som tekst i hver rad;
  søylefargene har tekstetikett og oppfyller kontrast mot bakgrunn fra
  `design/tokens.css`.
- Tomt vindu: raden vises med 0 og teksten «ingen» — ikke utelatt.
  Tick-kortets tomtilstand er en setning, ikke en tom tabell.
- Vindusvelgeren er `<select>` med `<label>` (forhåndsdefinerte vinduer:
  24 t, 7 d, 30 d — fritt intervall kan komme senere); valgt vindu og
  tidssone står i klartekst ved tallene.
- KPI-tellernes eksisterende mønster (`t()`-nøkler, `plattformTelling`)
  gjenbrukes; all tekst via `locales/nb.json`; **axe-port i samme PR**;
  manuell tastaturgjennomgang dokumentert.

## 6. Det v1 eksplisitt ikke gjør (flagget, ikke smuglet)

1. **Ingen nye indekser.** Aggregeringsindeksene som mangler (`oppdrag
   (tenant, status_ts)`, tick per tenant/utfall) er skriveveisendringer
   utenfor rammen. Volumene (55 oppdrag, 0 ticks) bærer lesing uten.
   **Terskelen for å bestille dem** skrives i spesifikasjonen: når en
   nøkkeltallsspørring målt i prod passerer 100 ms, kommer indeksen som
   egen liten migrasjon med SP-10-kjøring — ikke før, og aldri i en
   flate-PR.
2. **Ingen plattformbred visning**, ingen eksport, ingen
   sammenligning mellom tenanter.
3. **Ingen avledede påstander:** ingen «suksessrate», ingen andel,
   ingen gjennomsnitt/median, ingen vektede skårer (M16-2). Rå tellinger
   med navngitte definisjoner, og radfakta der én rad bærer verdien.

## 7. Codex-porter

**Data (1–7).** 1 Suminvariant **per partisjon**: hver gjensidig
utelukkende gruppe telles med total fra samme skann (test under
samtidig skriving); ukjent enumverdi telles som «ukjent» i totalen,
aldri utenfor · 2 Tenant-binding: én tenant ser
aldri en annens tall (SP-1-port per definer) · 3 Flaten har ingen
direkte SELECT mot kildetabellene (statisk, SP-7) · 4 Ingen
dekrypteringsvei kalles fra nøkkeltallsveien (statisk) · 5 Halvåpne
vinduer: hendelse nøyaktig på `til` telles i neste vindu, ikke begge ·
6 Alle kort bruker den delte vindushjelpen (statisk: ingen egen
vindusaritmetikk i kortspørringer) · 7 Tidsstempel→vindu-mappingen er
eksplisitt per kilde (test per kort med kjent fixture) · 7b **Sak
opprettet før vinduet, fortsatt åpen:** telles ikke som opprettet eller
lukket i vinduet, men inngår i «åpne nå» (M16-4) · 7c «Åpne nå» endres
ikke av vindusvelgeren (ende til ende) · 7d Tick med `registrert` i ett
vindu og `vindu_start` i et annet → tilhører `vindu_start`-vinduet
(fixture, klar for når data finnes).

**Ærlighet (8–11).** 8 Ett datapunkt → ett punkt; ingen interpolasjons-
eller glattingskode i flaten (statisk) · 9 Tomt vindu → rad med 0 og
«ingen»; tick-kortet uten data → tomtilstandssetning · 10 Ingen
divisjon i definerne — ingen andel, gjennomsnitt eller median (statisk);
radvise varigheter er eneste differanseform ·
11 Suminvarianter vist i UI-et stemmer med API-svaret (ende til ende).

**Flate (12–15).** 12 axe null `alvorligeBrudd` · 13 Tabell er
tilgangsform: alle tall finnes som tekst; søyle uten tekstlig verdi
finnes ikke (statisk + manuell) · 14 Beslutningstype aldri kun ved
farge; kontrast fra tokens · 15 Ingen hardkodet visningstekst; tastatur-
gjennomgang dokumentert.

**Alle tester konstruerer egen tilstand.** Ingen delt fixture.

## 8. Evidensgrense `m16-v1` (defineres FØR arbeidet)

**Sikkerhetsinvarianter:** `nokkeltall.kryss_tenant_lesing = 0` ·
`nokkeltall.dekryptering_kalt = 0` ·
`nokkeltall.direkte_tabellesing = 0`.

**Øvrige:** `partisjon.suminvariant_brutt = 0` · `partisjon.ukjent_verdi_utenfor_total = 0` ·
`kort.egen_vindusaritmetikk = 0` · `vindu.dobbelttelling_pa_grense = 0` · `sak.tilstand_blandet_med_aktivitet = 0` · `tick.vindu_etter_registrert = 0` ·
`flate.glatting_finnes = 0` · `flate.avledet_skaar = 0` · `definer.divisjon_finnes = 0` ·
`flate.tall_kun_i_soyle = 0` · `flate.tomt_vindu_skjult = 0` ·
`ui.axe_alvorlige_brudd = 0` · `ui.tastaturgjennomgang_dokumentert = ja` ·
`ytelse.sporring_over_100ms_uten_indeksflagg = 0`.

Et punkt uten definert, målbar grense regnes som `nei`.

---

## Spørsmål til porten — **besvart i første runde**

1. **Tick-kortet: beholdes** — «ingen planer kjørte i det valgte
   vinduet» er ærlig observasjon som viser at planfunksjonen finnes uten
   å fabrikere aktivitet, og uten å påstå et fravær utenfor vinduet.
2. **Testtenantmerking: utsatt** til en faktisk plattformvisning
   spesifiseres — v1 er tenant-skopet, og en skrivevei nå ville brutt
   den bevisst lille read-only-arcen.

```
NÅ:    Spesifikasjonen gjennom porten (de fire faste portspørsmålene +
       de to over) — ChatGPT (Eier relayer)
       — docs/pr/PR-M16-SPESIFIKASJON.md
NESTE: Ved GO: implementeringsklarsignal (ingen migrasjon; definere,
       API-generalisering, kortflate med axe i samme PR) — Claude.ai →
       Claude Code — docs/pr/PR-M16-IMPLEMENTERINGSKLARSIGNAL.md
```
