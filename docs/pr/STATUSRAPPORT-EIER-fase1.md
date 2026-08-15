# STATUSRAPPORT TIL EIER — Disponit, fase 1 og veien til «kunden ser M-1»

**Fra: Claude.ai (arkitekt) · 2026-08-05 · Skrevet nå fordi hele kartet
endelig er tegnbart: tillitsankeret er bygget, og UI/API-sporet er
ferdig spesifisert. Dette er der vi står og hva som gjenstår.**

---

## 1. Hva som er FERDIG og bevist

**Tillitsankeret (fase 1) er komplett kode på main**, hver del bevist med
maskinell port, ikke påstand:

| Modul | Hva | Status |
|---|---|---|
| M-1 policymotor | Deterministisk evaluering, deny-by-default, fail-closed | `aktiv`, 6/6 sjekkliste, p95 82 ms |
| M-2 revisjonslogg | Append-only håndhevet av DB, tenant-isolert | Bevist i m01-kjeden |
| M-37 unntaksmotor | Behandling, R1 tofase-reparasjon, outbox, null egne fullmakter | Feilinjisering p95 65 ms, 24/24 terminale |
| Tilstandslag | PostgreSQL, RLS+FORCE, envelope-kryptering, crypto-shredding | 384 tester, 0 hopp |

M-1 er satt `aktiv` + `driftstilstand: ikke_i_drift` — ærlig milepæl:
modulen er ferdig og godkjent, men kjører ingensteds ennå.

## 2. Hva som er SPESIFISERT og klar for bygging

To PR-er er gjennom porten, GO gitt, klare for Claude Code:
- **PR-008 lese-API** — de seks endepunktene UI trenger. GO i dag.
- **M-1 UI** — komponentbibliotek + fire flater (Oversikt/Policy/
  Beslutninger/Unntak), bygget mot `design/tokens.css`. GO.

Prototypen du så er retningen — bekreftet av porten som produktretning.

## 3. Den ærlige avstanden til «en kunde klikker seg gjennom M-1»

Fire ting står mellom nå og det, ingen av dem store, i rekkefølge:

1. **PR-008 lese-API implementeres** (Claude Code, GO gitt).
2. **Lag 1 — staging-drift:** API-et som kjørende tjeneste (systemd-unit
   finnes ikke ennå), migrasjoner kjørt, en tenant + token opprettet. Til
   nå har staging kjørt tester, ikke en levende tjeneste.
3. **Sesjons-PR:** browserinnlogging (utstedelse, levetid, cookie, CSRF) —
   hard avhengighet før en nettleser kan snakke med API-et. Egen liten PR.
4. **M-1 UI implementeres** mot lese-API-et + sesjonen.

Da — og først da — logger du inn som en tenant, ser policyen som
håndheves, sender en beslutning, ser den i loggen, åpner et unntak og ser
reparasjonskjeden. Det er «M-1 fungerer» sett fra kunden.

**Estimat i arbeidsmengde, ikke kalendertid:** punkt 1, 2 og 3 er hver
små-til-middels og godt spesifisert. Punkt 4 er det største, men
komponentbiblioteket bygges én gang og arves av alle senere moduler — så
kostnaden er en investering, ikke en M-1-utgift.

## 4. To lærdommer som bør bli fast rutine

**A. Samtidighet og ulykkelige veier må være FØRSTE designsjekk, ikke en
review-oppdagelse.** PR-007 tok åtte spesifikasjonsrunder; nesten hvert
funn var samme familie: «sekvensielt riktig ≠ samtidig riktig», eller «en
rettelse i forrige runde åpnet neste hull». PR-008 tok seks. Dette er ikke
svak spesifikasjon — det er at M-37 og lese-API-et krysser tenant, fase,
prosess og variabelt tilstandsrom samtidig, så kombinatorikken er iboende
høy. Men vi kan flytte funnene FRAM: for hver kontrakt still de fire
spørsmålene FØR draften sendes — (1) alle veier inn? (2) under samtidighet?
(3) riktig vs. velformet? (4) lukket format? Claude Code formulerte disse;
de fortjener plass i RUTINER, ikke bare i hver enkelt brief.

**B. Tester må konstruere sin egen tilstand, aldri anta et utgangspunkt
som kan endres.** Tre ganger nå har en test råtnet fordi den antok en
tilstand som senere endret seg: den hardkodede 114-en, no-op-en da m01 ble
`aktiv`, og en tidlig fixture. Negative tester skal bygge tilstanden de
måler fra bunnen. Dette bør inn i RUTINER som testkrav.

## 5. Hva jeg anbefaler som NESTE strategiske steg

Ikke start M-38 ennå. Fullfør heller «M-1 på ekte» (punkt 1–4 over) FØRST,
av én grunn: det gir oss den første ende-til-ende-sannheten — en modul som
faktisk kjører, med et menneske som ser den virke. Alt vi har bygget er
bevist i tester; ingenting er bevist i bruk. Den erfaringen vil forme M-38
og alle senere moduler mer enn nok en motor bygget i blinde.

Når M-1 kjører på staging med deg som ser den: DA er riktig tidspunkt for
M-38 (kapasitet/kø/modellruting) og for å vurdere «kunde null» — plattformen
som driver sin egen bedrift.

---

**NÅ:** Gi `PR-008-IMPLEMENTERINGSKLARSIGNAL.md` til Claude Code — branch
`pr-008-lese-api` — **Claude Code** — `platform/core/api/`,
`platform/core/db/migrations/008_*.sql`

**NESTE:** Etter PR-008: lag-1-drift (API-unit + staging-tjeneste) og
sesjons-PR parallelt, så M-1 UI — **Claude.ai drafter lag-1 + sesjon,
så Claude Code** — `deploy/staging/`, `docs/pr/`

**DERETTER:** M-1 kjører på staging, du ser den virke ende-til-ende — så
drafter jeg M-38-spesifikasjon og «kunde null»-vurdering — **Claude.ai** —
`docs/`
