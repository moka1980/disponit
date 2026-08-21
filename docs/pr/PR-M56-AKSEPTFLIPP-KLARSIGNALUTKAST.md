# M56-AKSEPTFLIPP — KLARSIGNALUTKAST v2 (A1–A3 innarbeidet · §0 lukket)

**Draft: Claude.ai · Full sti:
`docs/pr/PR-M56-AKSEPTFLIPP-KLARSIGNALUTKAST.md`.
Migrasjon (om nødvendig): neste ledige mot main, verifisert ved
branch-push. Stående porter SP-1…SP-12. Grunnlag: debrief #108
2026-08-20, prod `0ebe10f`, migrasjoner 1→48.**

---

## 0. Ratifisering: R47-1-avviket — **ratifisert, og skissen min var feil**

Avviket ratifiseres fullt ut, og det fortjener en ærlig obduksjon: min
CHECK `styrt ⇒ attestant_b IS NOT NULL` **motsa ratifiseringen den red
sammen med.** 047-ratifiseringen godtok nullbar `attestant_b` nettopp
fordi INNSNEVRER/NØYTRAL har kvorum 1 — og så skrev jeg et vilkår som
krevde to attestanter for alle styrte hendelser. UTVIDER-formen der
forfatteren er én av to (krav 2, nøyaktig én kvalifiserende attestasjon)
gjorde den usann i enda en retning. **SP-10-prøvekjøringen mot bebodde
former fanget det** — en tom-base-test hadde vært grønn, for skissen var
konsistent med seg selv, bare ikke med virkeligheten.

Formen som landet er riktigere enn skissen på tre målbare måter:

1. **`pakrevd_antall` er FK-bundet til rundens krav** — hendelsen kan
   ikke lyve om hvor mange som krevdes. Kravet er bevist, ikke antatt.
   (Skissen antok et universelt kvorum som ikke finnes.)
2. **`hendelse_kvorum_gate()` teller attestasjonsradene** — totalt ≥
   kravet, minst én ikke-forfatter. Ingen CHECK på raden alene *kan*
   telle rader i en annen tabell; gate-trigger er riktig verktøy.
3. **Varigheten holder via SP-9s andre form:** etableringskontroll
   (gaten ved INSERT) pluss immutabilitet (attestasjonene er
   append-only, FK-nøkkelen bærer kvalifikasjonen). Kvorumet målt ved
   INSERT forblir sant.

Sløyfas fire P1-er ratifiseres også uten forbehold — særlig nr. 2, som
er **SP-12 anvendt mot min egen backfill** (versjonstrippelen er
gjenbrukbar; operasjonen er ikke), og nr. 1, som utvider
«vakter hører hjemme der faren oppstår» til rettighetsflytt: revoke og cred-bytte
er ett vedlikeholdsvindu, ikke to kommandoer. At SP-10-skriptet selv
fikk en DROP DATABASE-navneromslås er den typen funn som rettferdiggjør
hele reviewregimet.

Arkivpresiseringen noteres: «4 historisk + 1 styrt» fra 047 var
`policyer`-rader; hendelsestabellen hadde alltid bare pilotens ene.

## 1. Akseptflippen — hva den er, og hva den ikke er

`m_wcag_audit` kjører i drift, men deploymenten står i
staging-/pilotaksept. Flippen er den formelle overgangen til akseptert
deployment i modulregisteret — og den skal være **en herdet funksjon med
bevisbårne forutsetninger**, ikke en statuskolonne noen setter.

Flippen er ikke en relansering: modulen endres ikke, arbeideren endres
ikke, policyen endres ikke. Det eneste som endres er registerets påstand
om modulen — og derfor er portene bevisporter, ikke funksjonsporter.

## 2. De to bindende portene

### 2.1 Rollback-drill-porten

Akseptflippen krever at **tilbakerulling er demonstrert, ikke antatt**:

- Drillen utføres mot staging/prøvemiljø: rull moduldeployment tilbake
  til forrige release, verifiser at (a) arbeideren stopper å plukke nye
  oppdrag for den tilbakerullede releasen (epoch-/releasegjerdet fra
  040), (b) løpende oppdrag fullfører eller feiler rent uten falske
  verdikter (SP-3), og (c) rullen fram igjen gjenoppretter plukking.
- **Drillen etterlater en evidensrad** (hvilken release → hvilken,
  tidspunkt, utfall per kontrollpunkt), og det er *den raden*
  akseptfunksjonen krever — ikke en avkrysning.
- Drillen må være **fersk**: utført mot samme release som aksepteres.
- **A1 — identitet, ikke bare bytes:** digest beviser hvilke modulbytes
  som ble drillet; den beviser ikke hvilken *deploymentinstans*. Samme
  bytes kan opptre i en senere deployment med annen kontrakthash, epoch
  eller registertilstand — og drillen tester nettopp deploymentadferd
  (claim-stopp, fencing, tilbakeføring), ikke binærens innhold.
  Evidensraden bærer derfor **både** digest **og** en gjenbrukssikker
  identitet for den drillede deploymenten/releasen (FK mot den
  autoritative raden, eller deploymenthistorikken om den finnes — SP-12
  anvendt på flippen). Digest alene skal aldri stille treffe «samme nok»
  deployment.

### 2.2 Evidensbindingsporten

Akseptfunksjonen krever, relasjonelt bundet (ikke navngitt i fritekst):

1. Rollback-drill-evidensen (2.1) for nøyaktig denne releasen.
2. E2E-beviset fra driftskjøringen (artefakt promotert mot fasit —
   finnes fra 19/8-kjøringen). **A2 — lineage, ikke bare identitet:**
   artefakt-ID + hash beviser hvilket artefakt det er, ikke at *den
   releasen som aksepteres* produserte det. Bindingen er den
   relasjonelle kjeden **aksepthendelse → E2E-bevis → artefakt/oppdrag →
   eksakt release/deployment** — gjenbruk artefaktlagerets lineage om
   den finnes, ellers databasehåndhevet binding i akseptevidensen.
   Negativ port: gyldig fasit-artefakt produsert av en *annen* release,
   med ellers gyldig hash → aksept avvises.
3. Grønn evidensgrense `wcag-modul-v1` på akseptertidspunktet.
   **A3 — grønt må etterlate et varig bevis:** er grensen bare en
   beregning over dagens tilstand, finnes ingen historisk rad å
   FK-referere, og historikken kan aldri bevise *hva som var grønt da*.
   Gren: finnes immutable evidensrader per portresultat → hendelsen
   refererer dem; finnes bare current-state → akseptoperasjonen skriver
   én immutabel **aksept-evidensobservasjon** som binder de konkrete
   portresultatene og kildene deres på tidspunktet. Aldri bare
   `wcag_modul_v1 = true` — det er en kopi av konklusjonen, ikke en
   referanse til beviset (SP-§3). Negativ port: grønt bevis for annen
   release, eller ufullstendig evidenssett → aksept avvises.

**Formen følger `policyaktivering`-mønsteret:** akseptflippen skriver én
immutabel aksepthendelse som FK-refererer bevisene, og registerstatusen
er CHECK-bundet til at hendelsen finnes (samme klasse som
`runde_terminal_krever_hendelse`). Ingen hendelse → ingen akseptert
status. SP-1/SP-2 gjelder funksjonen; SP-5 gjelder enhver ny CHECK.

## 3. Innhold som tas inn ved flippet — ferdig skrevet, betinget

Fra `docs/INNHOLD-wcag-katalog-og-frontside.md`, holdt tilbake til nå:

- **Planlinjen inn i M-56-flyten:** «Mottar bestilling gjennom
  beslutningsveien, **eller fra en aktiv plan**» — scheduleren er levert
  (044→048), så linjen er sann. Den legges til **i samme PR som
  flippet**, ikke før.
- **Statusetiketten hentes fra registeret:** kortets «i drift»-merke
  leses fra modulstatus/deployment når flippen gjør den påstanden
  offisiell — ikke hardkodet i katalogen (som varslet i
  innholdsnotatet).

## 4. Lesejobb før klarsignalet fryses — fire faktaspørsmål, ren lesing

Jeg binder ikke DDL mot register-skjemaet uhørt; det har kostet før:

1. Hva heter deploymentstatusene i `moduldeployment` i dag, og finnes
   det allerede en akseptfunksjon/overgang (eller bare kolonnen)?
2. Finnes det en hendelses-/historikktabell for moduldeployment, og
   **hvilken gjenbrukssikker identitet kan rollback-drillen bindes til**
   (release-ID, deploymentgenerasjon, tilsvarende) — SP-12-spørsmålet?
3. E2E-artefaktets **hele lineage** tilbake til release/deployment —
   ikke bare tabell, ID og hash: hvilke ledd finnes i dag mellom
   artefakt, oppdrag og release, og hvilke mangler?
4. `wcag-modul-v1`: hvilke **immutable evidensobjekter** finnes per
   portresultat, og der grensen bare beregnes dynamisk — hva må en
   aksept-evidensobservasjon snapshotte for å bære beviset varig?

Forhåndsbestemte grener: finnes akseptfunksjon → den utvides med
bevis-FK-ene; finnes ikke → den opprettes etter
`policyaktivering`-mønsteret. Finnes deployment-historikk → drillen
skriver dit; ellers egen smal drilltabell.

## 5. Codex-porter (skisse — nummereres endelig i klarsignalet)

1 Akseptflipp uten drill-evidens → avvist, navngitt constraint · 2 Drill
mot annen release/deployment enn den som aksepteres → avvist (identitet
+ digest, A1) · 2b Fasit-artefakt fra annen release, gyldig hash →
aksept avvist (A2) · 2c Grønt bevis for annen release eller ufullstendig
evidenssett → aksept avvist (A3) ·
3 Akseptert status uten aksepthendelse → CHECK-avvist · 4 Aksepthendelse
immutabel · 5 Drill: tilbakerullet release plukker ikke nye oppdrag;
fram igjen gjenoppretter · 6 Løpende oppdrag under drill → rent utfall,
aldri falskt verdikt (SP-3) · 7 Planlinjen i katalogen finnes først i
flipp-PR-en (innholdsdiff) · 8 Statusetiketten leses fra registeret,
ikke hardkodet (statisk) · 9 SP-10 begge kjøringer om migrasjon trengs ·
10 Replay-nøkkel på akseptoperasjonen (SP-2).

---

```
NÅ:    Ratifiseringen (§0) relayes som lukket; lesejobben i §4 (fire
       spørsmål, ren lesing) — ChatGPT (Eier relayer) / Claude Code
       — docs/pr/PR-M56-AKSEPTFLIPP-KLARSIGNALUTKAST.md
NESTE: Klarsignalet fryses fra lesesvaret (grenene er forhåndsbestemt);
       Claude Code implementerer flippen med innholdsendringene fra §3 i
       samme PR — Claude.ai / Claude Code
       — docs/pr/PR-M56-AKSEPTFLIPP-IMPLEMENTERINGSKLARSIGNAL.md
```
