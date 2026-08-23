# SPESIFIKASJON — M-57 Rekrutteringsagent (ATS), v1

**Draft: Claude.ai · Full sti: `docs/pr/PR-M57-SPESIFIKASJON.md`.
Grunnlag: katalogoppføringen (v9, portet) + startgrunnlaget 2026-08-21
mot main `fad844d`. Stående porter SP-1…SP-13; K1–K5 i rundene.
Migrasjon: neste ledige mot main. Ingen bygging før porten har gitt GO.**

---

## 0. Stående krav
Alt UI WCAG 2.1 AA fra første commit; axe-port i samme PR (§7).
Evidensgrense og akseptkrav defineres FØR bygging; UMAALTE-regelen står.

## 1. Det bærende: evalueringen er rådgivende, utsendelsen er en beslutning

Katalogen sier det allerede — «kontraktklassen gjelder utsendelsen, ikke
rangeringen» — og hele modulen faller ut av den setningen:

| Fase | Karakter | Mekanisme |
|---|---|---|
| Evaluering | **Rådgivende.** Skriver artefakter, rører ingenting utad | Ordinær bestilling → oppdrag → artefakt per kandidat |
| Utsendelse | **Irreversibel handling** | Skjer kun fordi et menneske signerte; outbox-rader kan ikke finnes uten signaturhendelsen |

Det gir svaret på startgrunnlagets funn 3 og 7 samtidig: **én
oppdragstype** (`rekruttering.evaluering`), og utsendelsen er *ikke* et
nytt oppdrag med egen type — den er en signaturbunden frigivelse av det
allerede produserte. 053-entydighetsporten holdes uendret, uten
portrevisjon.

## 2. Signaturgaten (startgrunnlagets funn 3) — hendelsesbundet, ikke ny livsløpstilstand

Mekanismen finnes ikke i dag. Formen som velges er
**`policyaktivering`-mønsteret**, som har bevist seg gjennom E1-serien:
en immutabel hendelse som outbox-radene er relasjonelt bundet til.

```sql
CREATE TABLE utsendingsliste (
  tenant TEXT NOT NULL, liste_id UUID NOT NULL,
  oppdrag_id UUID NOT NULL,                 -- evalueringsoppdraget
  listetype TEXT NOT NULL CHECK (listetype IN ('invitasjon','avslag')),
  malversjon TEXT NOT NULL,                 -- FK mot malregisteret (§4)
  innhold_hash TEXT NOT NULL,               -- SHA-256 (JCS) av HELE listen
  antall INT NOT NULL CHECK (antall > 0),
  opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, liste_id),
  UNIQUE (tenant, liste_id, innhold_hash),  -- refererbar (E1d-form)
  FOREIGN KEY (tenant, oppdrag_id) REFERENCES oppdrag (tenant, oppdrag_id));

CREATE TABLE utsendingssignatur (
  tenant TEXT NOT NULL, liste_id UUID NOT NULL,
  innhold_hash TEXT NOT NULL,               -- det som FAKTISK ble signert
  signatar TEXT NOT NULL,                   -- FK mot brukeridentitet (SP-§3)
  signert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  operasjonsnokkel TEXT NOT NULL,           -- SP-2, replay-sikker
  PRIMARY KEY (tenant, liste_id),
  FOREIGN KEY (tenant, liste_id, innhold_hash)
    REFERENCES utsendingsliste (tenant, liste_id, innhold_hash));
-- append-only; INSERT kun til signaturfunksjonens eier
```

**Hva formen beviser:**

- **Signaturen gjelder nøyaktig det innholdet som ble vist.** `innhold_hash`
  står i FK-en: endres listen etter signering, finnes ingen matchende rad
  — signaturen faller, den «følger ikke med». Dette er hele poenget, og
  det er E1e-lærdommen anvendt: signaturen refererer innholdet, kopierer
  det ikke.
- **Outbox-rader kan ikke finnes uten signatur:** utsendingsfunksjonen
  er eneste vei til outbox for denne modulen, og den krever
  signaturraden i samme transaksjon (FK). Statisk AST-port: modulen har
  ingen egen INSERT mot outbox.
- **Ingen ny livsløpstilstand i `oppdrag`.** Evalueringsoppdraget blir
  `utfort` når artefaktene er promotert. Utsendelsen er en senere,
  separat hendelse — beslutning ≠ utførelse, som ellers.

**Én signatar, ikke fire øyne** — konsistent med at dette er kundens egen
kommunikasjon til egne søkere, ikke en policyendring. *(Spørsmål 1.)*

**Delvis signatur finnes ikke:** listen signeres hel eller ikke. Vil
brukeren fjerne kandidater, redigeres listen, `innhold_hash` endres, og
den må signeres på nytt. Alternativet — signatur på delmengder — ville
gjort «hva ble faktisk godkjent» til en rekonstruksjon.

## 3. M-6/M-8 (funn 4): **(a) — plattformens outbox direkte, M-8 helt ut av v1**

`registry.valider` avviser aktiv modul med ikke-aktive avhengigheter, og
M-6/M-8 finnes ikke. v1 avhenger derfor **ikke** av dem:

- **E-post går gjennom plattformens eksisterende signerte
  utsendingsvei.** Den finnes, den er portet, og den er transporten
  M-6 senere ville pakket inn uansett.
- **Kalender ut:** invitasjonen bærer en lenke til tidsvalg i stedet for
  å foreslå tider. Fjerner M-8-avhengigheten, fjerner en
  skrive-integrasjon mot kundens kalender, og koster lite for søkeren.
- Katalogoppføringens integrasjonsfelt oppdateres tilsvarende i samme PR
  som modulen bygges — **katalogen skal ikke love M-6/M-8 mens v1 ikke
  bruker dem.**

## 4. Malregisteret (portens V9-beslutning, nå mekanisk)

Plattformkuraterte rammer, begrenset kundetilpasning:

```sql
CREATE TABLE utsendingsmal (
  malversjon TEXT PRIMARY KEY,      -- plattformeid, immutabel
  listetype TEXT NOT NULL, struktur JSONB NOT NULL,
  tillatte_flettefelt TEXT[] NOT NULL);
```
- Plattformen eier struktur, tillatte flettefelt og begrunnelsestyper.
- Kunden eier tone, firmatekst, kontaktinfo og forhåndsgodkjente
  tekstblokker — lagret per tenant, validert mot malens felt.
- **Ingen modellgenerert fritekst i utsendelser.** Statisk port: veien
  fra modellutdata til utsendingsinnhold finnes ikke; flettefeltene
  fylles fra **strukturerte funn med kildereferanse** (§5).

## 5. Evaluering, blinding og funn

- **Blinding før evaluering, målt:** maskeringen skjer i containeren før
  modellen ser teksten. Port: evalueringsinput inneholder ingen av de
  maskerte feltene (målt på faktisk input, ikke på kode), og
  av-maskering-tabellen er utilgjengelig for modellsteget. Avskrudd
  blinding krever eksplisitt valg og skriver auditrad.
- **Rangering, ikke score-som-sannhet:** vektene er synlige og
  kundejusterbare; utdata er rangering med vektene som fulgte den.
  Ingen prosent presentert som målt egenskap. *(M-16s §1-disiplin
  gjelder også her.)*
- **Risikofunn krever kildereferanse:** hvert funn bærer sitat-/
  posisjonsreferanse i søknadsteksten. Funn uten referanse **avvises av
  skjemaet**, ikke av en advarsel. Kategorier som beskriver personen
  framfor teksten (lojalitet, stabilitet som karaktertrekk) er ikke i
  det lukkede kategorisettet.
- **Modellen ligger i container-image** (funn 10): digest = modellversjon,
  pinnet som m56s motor. Biasmåling bindes til digesten og er et
  akseptkrav per modellversjon — bytte av image uten ny biasmåling
  blokkerer aksept.

## 6. Søkerdata-TTL (funn 9) — ny mekanisme, minimal form

- Bestillingen bærer `slettefrist_dager` innenfor et plattformspenn med
  streng standard (spennet tallfestes i klarsignalet; katalogen fastsetter
  det ikke).
- **Sletting fjerner payload, ikke sporet:** artefaktraden består med
  `slettet_ts` og innholdshash; **klartekst og kryptert payload
  slettes**. Da kan revisjonen fortsatt bevise *at* noe fantes og hva det
  hashet til, uten å bevare persondata. Terminale tilstander endres
  aldri; dette er ikke en tilstandsendring, men en payload-tømming med
  eget spor.
- Reaper kjører som eksisterende reapere; frist er absolutt og kan ikke
  forlenges av modulen. Lovlige hold er **eksplisitte tilstander**, ikke
  en «ikke slett»-bryter — v1 har ingen hold; trengs de, er de egen arc.
- **Slettefristen løper fra prosessen lukkes**, ikke fra opplasting.

## 7. Flaten (WCAG-kontrakt)

- Kandidatliste i ekte `<table>` med `<caption>`, `<th scope>`,
  sorterbare kolonner via knapper med `aria-sort`.
- **Trafikklys aldri kun farge:** kategorien står som tekst i raden;
  fargen er tillegg. Kontrast fra `design/tokens.css`.
- Vektskyveknapper er `<input type="range">` med `<label>`, synlig
  tallverdi og tastaturstøtte; endring annonseres i `aria-live="polite"`
  med ny rekkefølge oppsummert.
- Blindingsbryteren er `<input type="checkbox">` med tydelig ledetekst;
  avskruing åpner `role="alertdialog"` som sier at valget auditeres.
- Sidepanelet er en ekte dialog med fokusfangst og retur.
- **Signaturdialogen viser antall, listetype og innholdshashens
  kortform**, og sier hva som skjer: «Dette sender N e-poster. Kan ikke
  angres.» Utfall i `role="alert"`.
- All tekst via `locales/nb.json`; **axe-port i samme PR**; manuell
  tastaturgjennomgang dokumentert.

## 8. Skala og frister (funn 5)

- **Hard øvre grense per bestilling** (5000). Over grensen avvises
  bestillingen ved validering med tydelig melding — ikke stille
  avkorting.
- Parsing kjøres i porsjoner inne i oppdraget med fremdrift skrevet som
  evidens; **fristvalget utvides** med et lengre alternativ i
  `oppdragskontrakt.UTFORELSESFRIST_VALG`, og leasen dekker fristen som
  i 037. Klarsignalet tallfester begge etter en målt prøvekjøring.
- **Delvis resultat er ikke suksess:** avbrutt kjøring gir ingen
  promotert kandidatliste; oppdraget feiler rent (SP-3) og kan
  gjenopptas som ny bestilling.

## 9. Angrepsflate: ubetrodd arkiv (funn 6)

Skarpere enn m56s HTML-lesing, og portene skrives fra dag én:
zip-bombe (komprimeringsforhold og utpakket totalstørrelse med hard
grense), path traversal (`../`, absolutte stier, symlenker → avvist),
filantall, nøstede arkiver (ikke tillatt), filtypesjekk på **innhold**
ikke navn, og makro-/aktivt innhold i DOCX ignoreres. Containeren er
credential-fri og nettverksløs under parsing (port 24-formen).

## 10. Codex-porter (skisse — nummereres i klarsignalet)

**Signaturgaten.** Outbox-rad uten signaturrad → FK-avvist · Liste
endret etter signering → signaturen matcher ikke, utsendelse avvist ·
Modulen har ingen egen outbox-INSERT (statisk AST) · Replay: to
signaturkall → én signatur, én utsendelse (SP-2) · Delvis signatur
finnes ikke (ingen delmengde-API).

**Innhold.** Ingen vei fra modellutdata til utsendingstekst (statisk) ·
Flettefelt utenfor malens `tillatte_flettefelt` → avvist · Funn uten
kildereferanse → skjemaavvist · Kategori utenfor lukket sett → avvist.

**Blinding og modell.** Evalueringsinput fri for maskerte felt (målt på
input) · Avskrudd blinding uten auditrad → avvist · Imagebytte uten ny
biasmåling → aksept blokkert.

**TTL.** Frist passert → payload slettet, sporrad består med hash og
`slettet_ts` · Modulen kan ikke forlenge frist (statisk) · Sletting
etterlater ingen klartekst (målt).

**Arkiv.** Zip-bombe, path traversal, symlenke, nøstet arkiv, feil
innholdstype → alle avvist, hver med egen test · Container uten
credentials og uten nett under parsing.

**Skala og flate.** 5001 søknader → avvist ved validering, ikke avkortet
· Avbrutt kjøring → ingen promotert liste, rent feilutfall · axe null
`alvorligeBrudd` · Trafikklys og vektendring tilgjengelig uten farge/mus.

---

## Spørsmål til porten

1. **Én signatar for utsendelse.** Kundens egen kommunikasjon til egne
   søkere, ikke en policyendring — men et bulk-avslag til tusenvis er
   irreversibelt og omdømmebærende. Er én signatar riktig, eller bør
   lister over en viss størrelse kreve to?
2. **Sletting som payload-tømming.** Sporraden består med hash og
   `slettet_ts` så revisjonen kan bevise at noe fantes. Er det riktig
   balanse mot sletteplikten, eller skal raden bort i sin helhet?
3. **Kalender ut av v1.** Invitasjonen bærer lenke til tidsvalg i stedet
   for foreslåtte tider. Svekker det produktet nok til å rettferdiggjøre
   M-8-avhengigheten, eller er lenken god nok til første kunde?

```
NÅ:    Spesifikasjonen gjennom porten (de fire faste portspørsmålene +
       de tre over) — ChatGPT (Eier relayer)
       — docs/pr/PR-M57-SPESIFIKASJON.md
NESTE: Ved GO: implementeringsklarsignal med tallfestede grenser
       (slettespenn, fristvalg, arkivgrenser) og evidensgrense
       `m57-v1` — Claude.ai → Claude Code
       — docs/pr/PR-M57-IMPLEMENTERINGSKLARSIGNAL.md
```
