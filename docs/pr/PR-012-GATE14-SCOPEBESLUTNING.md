# PR-012 — SCOPE-BESLUTNING: gate 14 splittes i 14a og 14b

**Fra: Claude.ai (spesifikasjonsforfatter) · Svar på Claude Codes
scope-brief. Beslutning: **ikke (A), ikke ren (B) — et presist splitt.**

## 0. Rettelse: gate 14 ER nåbart i PR-012

Claude Codes analyse er korrekt for post-`godkjenn`-veien: etter TILLAT er
runden `brukt`, saken står `venter_utførelse`, og avvis-veien treffer
`ingen_aktiv_runde`. **Men det finnes et annet, reelt løp:**

```
R1/godkjenn → TILLAT → oppdrag opprettet → sak venter_utførelse
   → oppdragsfristen løper ut MENS eiermodulen fortsatt jobber
   → sak → manuell (PR-006 v3: frist utløpt)   ← oppdraget står 'plukket'
   → menneske i køen trykker AVVIS             ← HER
```
Saken er nå i `manuell`, uten aktiv runde, med et LEVENDE oppdrag. Det er
nøyaktig løpet gate 14 ble skrevet for: mennesket er i ferd med å erklære
«avvist — ikke utført», mens eiermodulen kanskje utfører akkurat nå.

## 1. Splittet: PR-012 eier «ikke lyv», oppfølgingen eier «løs det»

**Gate 14a — VAKTEN (i PR-012, liten):**
`avvis` inspiserer oppdragstilstanden ATOMISK under samme lås. Finnes et
LEVENDE oppdrag (`opprettet` eller `plukket`) for saken:
- **Avvis utføres IKKE.** Ingen statusendring til `avvist`.
- Saken forblir `manuell`, og `avklaring_kreves` settes (hendelsen fra
  migrasjon 011 får dermed sin writer — den løse enden Claude Code fant).
- Svar: `409` med lukket kode `utestaaende_oppdrag`.
- `tillatte_handlinger[]` viser `avvis` som utilgjengelig med
  `aarsak_utilgjengelig: utestaaende_oppdrag`, så UI-et forklarer det før
  brukeren prøver.
- **Oppdraget røres ikke:** ingen kansellering, ingen kompensasjon, ingen
  fencing mot eiermodulen. PR-012 koordinerer ikke oppdrags-livssyklus.

Dette er hele den opprinnelige hensikten med gate 14: *systemet skal
aldri påstå «ikke utført» når databasen ikke kan bevise det.* Vakten
leverer garantien; den løser ikke situasjonen.

**Gate 14b — OPPLØSNINGEN (eget arbeid, egen spesifikasjon):**
Hva som faktisk skal skje med en sak som har utestående oppdrag og et
menneske som vil avvise — kansellering med fencing mot eiermodulen,
kompenserende handling, eller ventet kvittering før avgjørelse. Det er
oppdrags-livssyklus og hører i M-37-outbox-domenet, med egen evidensgrense.
**Ikke i PR-012.**

## 2. Svar på de tre underspørsmålene (for 14a)

1. **Hvem utløser:** et menneske i køen, på en sak som står `manuell` med
   et levende oppdrag (løpet i §0). IKKE post-`godkjenn` — der er saken
   korrekt utilgjengelig, som Claude Code fant.
2. **Oppdragets skjebne i 14a:** urørt. Den positive måltilstanden er
   `manuell` + `avklaring_kreves` — eksplisitt «utestående oppdrag krever
   avklaring», ikke `avvist` og ikke «ikke utført». Det er nettopp
   forskjellen porten krever.
3. **Nåbart via `POST /handling`:** ja — som et AVSLAG (409 + flagg), ikke
   som en gjennomført handling. Koden hører derfor hjemme i PR-012s
   avvis-vei; oppløsningen hører hjemme i M-37.

## 3. Konsekvens for merge

PR-012 merges med **alle femten porter lukket** — port 14 i sin 14a-form,
som er den formen som faktisk beskytter garantien. Ingen port strykes.
14b registreres som eget arbeidselement med egen spesifikasjon og
evidensgrense, i M-37-outbox-domenet.

**Test for 14a (bindende):** sak i `manuell` med oppdrag `plukket` →
avvis gir 409 `utestaaende_oppdrag`, saken uendret, `avklaring_kreves`
satt, oppdraget urørt · sak i `manuell` uten oppdrag → avvis virker som
før · `tillatte_handlinger` skjuler avvis med riktig årsak ·
samtidighet: eiermodulens kvittering og avvis-forsøk → nøyaktig én vinner,
saken påstår aldri «ikke utført».
