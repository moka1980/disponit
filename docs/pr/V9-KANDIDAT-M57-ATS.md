# M-57 REKRUTTERINGSAGENT (ATS) — MODULKANDIDAT TIL PORTEN

**Draft: Claude.ai · Full sti: `docs/pr/V9-KANDIDAT-M57-ATS.md`.
Grunnlag: eiers ATS-kravspesifikasjon 2026-08-20 + vurderingen i
`V9-KANDIDAT-ATS-VURDERING.md` (§3-kontraktene innarbeidet).
Dette er en **produktbeslutning** (som v8-runden) — full spesifikasjon
skrives først ved prioritering. Ingen bygging nå.**

---

## 1. Katalogoppføringen, i v8-skjemaet

```json
{"n":57,"name":"Rekrutteringsagent (ATS)","area":"Samarbeid og HR",
 "p":3,"level":"Krevende","v9":"ny","status":"planlagt",
 "kl":"dokumentbehandling","rev":"radgivende_pluss_signert_utsendelse",
 "goal":"Leser og rangerer opptil 5000 søknader mot stillingens krav, forbereder intervjuspørsmål og innstiller svar — mennesket beslutter hvem som går videre og godkjenner all utsendelse.",
 "flow":["Mottar bestilling gjennom beslutningsveien med stillingsprofil og søknadsbunt (ZIP)",
         "Pakker ut og trekker tekst fra PDF, Word og HTML i isolert container",
         "Blinder som standard: navn, kjønn, alder, adresse, bilde og kontaktfelt maskeres før evaluering",
         "Grovsorterer mot absolutte minimumskrav, deretter detaljevaluering av resten",
         "Lagrer rangering, funn og skreddersydde intervjuspørsmål som artefakt per kandidat",
         "Innstiller intervjuinvitasjoner og malbaserte avslag — sendes først etter menneskelig signatur per liste"],
 "input":"Stillingsprofil med vektede krav, søknadsbunt (ZIP med PDF/DOCX/HTML).",
 "output":"Rangert kandidatliste med begrunnede funn per kandidat, intervjuforberedelse, og innstilte utsendelser som venter på signatur.",
 "int":"Dokumentparsing i container, artefaktlager, e-postutsendelse via M-54-mønsteret, kalender-API (lesing av ledige tider).",
 "guard":"Ingen kandidat avslås eller kontaktes uten menneskelig signatur — automatisk utfiltrering innstiller, aldri beslutter (GDPR art. 22; EU AI Act høyrisiko, vedlegg III). Blindet evaluering (personalia skjult) er standard på — de maskerte feltene er navngitt i flyten; modulen påstår ikke anonymisering ut over dem. Avslag er malbaserte med flettefelt fra sporbare funn — aldri frigenerert prosa i masseutsendelse. Rangering vises som rangering med synlige vekter, aldri som målt sannhet. Søkerdata slettes etter fastsatt frist når prosessen lukkes.",
 "kpi":"Andel bunter ferdig rangert innen frist; andel innstillinger signert uendret; klager på avslagsinnhold (mål: null); slettefrister overholdt.",
 "accept":"Ingen utsendelse uten signaturhendelse; evaluering kjørt blindet med mindre eier eksplisitt har skrudd det av (auditert); risikofunn uten kildereferanse i søknadsteksten avvises; biasmåling dokumentert per modellversjon.",
 "dep":"M-1, M-2, M-37, M-54 (signert utsendelse), artefaktlager, outbox."}
```

**Tellinger ved opptak:** 56 → 57 moduler; fase 3: 19 → 20. Nytt
`rev`-nivå og `kl`-verdi (`dokumentbehandling`) må inn i
katalogforklaringen — modulen leser opplastede dokumenter, ikke eksterne
domener, så den er *ikke* `ekstern_lesing` i M-56-forstand.

## 2. Hva som er endret fra eiers dokument, og hvorfor

| Eiers form | Kandidatens form | Grunn |
|---|---|---|
| «Rød filtrert ut automatisk» + bulk-avslag | Utfiltrering **innstiller**; mennesket signerer listen | GDPR art. 22 / AI Act høyrisiko — og det er salgsargumentet, ikke en kostnad |
| AI-genererte avslag i fri tekst | **Malbaserte** avslag, flettefelt fra sporbare funn | Én hallusinert begrunnelse til én av 4000 er én for mye |
| Blind-modus som bryter | Blindet evaluering **standard på**; avskruing auditeres | En rettferdighetsgaranti man kan glemme er ingen garanti |
| Celery/Redis/Pinecone/S3/MongoDB | Bestillingsvei, outbox, artefaktlager, container | Modul i plattformen, ikke sidesystem — kontrollen *er* beslutningsveien |
| «match_score 87 %» | Rangering med synlige vekter | Systemet påstår aldri noe databasen ikke kan bevise |
| `job_hopping` / «avdekke illojal» | Risikofunn krever kildereferanse i søknadsteksten; intensjonsfeltet omformulert til nøytral intervjuforberedelse | Diskriminerings- og dokumentasjonsrisiko |
| Kalenderintegrasjon skriver | Kalender **leses**; invitasjonen sendes signert med bookinglenke | Minste nødvendige fullmakt |

## 2b. Portens produktbeslutninger (V9-runden — innarbeidet)

- **Byggerekkefølge: M-16 før M-57.** Katalogopptak og byggeprioritet er
  skilt: M-57 får nummeret nå, men M-16 (KPI-dashboards) bygges først —
  den har nå produksjonsdata å vise og langt mindre ny tillitsflate.
  M-57 står som neste store arbeidsmodulkandidat etterpå, fortsatt
  fase 3 med intern rekkefølge.
- **Avslagsmaler: plattformkuraterte rammer med begrenset
  kundetilpasning.** Plattformen eier struktur, tillatte flettefelt,
  begrunnelsestyper og forbudet mot modellgenerert fritekst i
  masseavslag; kunden eier tone, firmatekst, kontaktinfo og
  forhåndsgodkjente tekstblokker. Signaturen står uansett.
- **Retensjon: kundevalgt innenfor plattformdefinert spenn, streng
  standard.** Produktinvarianten er at lukket prosess starter en
  eksplisitt slettefrist som ikke kan være ubegrenset eller glemmes;
  lovlige hold er senere eksplisitte tilstander, aldri en generell
  «ikke slett»-bryter. Spennet tallfestes i spesifikasjonen, ikke i
  katalogen.
- **`radgivende_pluss_signert_utsendelse` er beskrivende, ikke en ferdig
  sikkerhetsklasse:** spesifikasjonen avgjør senere hvilke mekaniske
  klasser det komponerer (rådgivende analyse + signert outbox-handling).

## 3. Parkert (egne beslutninger senere, ikke del av kandidaten)

1. **Desktop-agenten** (lokal mappeovervåking med API-nøkkel) — egen
   angrepsflate; vurderes først når ZIP-veien har bevist verdi.
2. **Annonse-URL-uttrekk fra tredjepartssider** (FINN o.l.) — egen
   `ekstern_lesing`-vurdering siden målet ikke er kundens domene.
   **Følgelig ute av `input` også (V9-1):** v1-input er stillingsprofil
   pluss dokumentbunt, og URL-import er en senere utvidelse etter egen
   egress-/målautorisasjonsvurdering.
3. **Automatisk booking-forhandling** (agenten velger tidspunkter selv)
   — kandidaten leser kalender og innstiller; mer autonomi er en senere,
   separat utvidelse.

## 4. Spørsmål til porten — **besvart i V9-runden, innarbeidet i §2b**

1. **Fase og rekkefølge:** kandidaten står som fase 3. Er ATS riktig
   *neste arbeidsmodul* etter M-56, eller skal den bak M-16
   (KPI-dashboards) som nå har datakilde? ATS har klarest betalings-
   vilje; M-16 har lavest risiko.
2. **Avslagsmalene:** skal malene være plattformens (kuraterte, like for
   alle kunder) eller kundens egne (fleksibilitet, men kunden kan skrive
   dårlige maler)? Jeg heller mot plattformens med kundefelt.
3. **Slettefristen:** fast plattformgrense (f.eks. 6 måneder etter
   lukket prosess) eller kundevalgt innenfor et spenn? Jeg heller mot
   spenn med streng standard.

---

```
NÅ:    Kandidaten gjennom ChatGPT-porten som produktbeslutning (V9-1),
       med de tre spørsmålene — ChatGPT (Eier relayer)
       — docs/pr/V9-KANDIDAT-M57-ATS.md
NESTE: Ved GO: M-57 inn i katalogen (v9-innhold, tellinger 57/fase 3=20,
       norskport etter); full spesifikasjon med SP-1…SP-12, evidensgrense
       og WCAG-kontrakt først når modulen prioriteres som byggearbeid
       — Claude.ai / Claude Code
```
