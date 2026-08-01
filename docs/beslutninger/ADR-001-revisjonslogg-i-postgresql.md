# ADR-001: Revisjonsloggen (M-2) flyttes til PostgreSQL

**Status:** Vedtatt av Claude.ai (arkitekt) 2026-08-01, etter anbefaling fra
Claude Code i PR-002-merknad. Gjelder fra PR-004.

## Kontekst

Under PR-002 ble seks P1-feil funnet og fikset. Fire av dem lå i den
filbaserte revisjonsloggen: serialisering, filsystemsemantikk og
syscall-semantikk (bl.a. at `os.write` ikke er write-all). Mønsteret er
tydelig: en append-only JSONL-fil med lås, write-all-løkke og
kappløpshåndtering er en database skrevet selv, én P1 om gangen.
Revisjonsloggen er samtidig tillitsankeret i M-1-kontrakten
(logg-før-utførelse) — den komponenten som minst av alt skal bære
hjemmelagde garantier.

## Beslutning

1. **M-2-revisjonsloggen implementeres i PostgreSQL i PR-004**, i samme
   leveranse som frekvenstelleren, slik at begge får transaksjonsgarantier
   fra samme motor.
2. **Fil-loggen (JSONL) degraderes til utviklingsverktøy.** Den beholdes
   for lokal kjøring og `run_synthetic.py`, merkes eksplisitt
   `KUN UTVIKLING` i koden, og lappes ikke videre: nye feilklasser i
   fil-loggen lukkes ved å henvise hit, ikke ved å fikse filen.
3. **Kontrakten er uendret:** ingen sideeffekt uten sikret loggpost.
   I PostgreSQL betyr «sikret» committet rad — sideeffekten utløses først
   etter commit.

## Bindende krav til PR-004 (fra Claude Codes merknad, vedtatt)

1. **Atomisk reservasjon i databasen, ikke bare byttet lagringssted:**
   frekvensreservasjonen skal være én atomisk SQL-operasjon (f.eks.
   `INSERT … ON CONFLICT`-mønster eller tilsvarende) slik at to samtidige
   forespørsler aldri begge kan reservere siste plass. `MinneTellerLager`
   beholdes kun for tester.
2. **Logg og reservasjon i samme transaksjon** der en beslutning har
   frekvensnøkkel: enten committes både loggpost og reservasjon, eller
   ingen av dem.
3. **Kryptografisk attestasjonsverifikasjon før API-et åpnes:**
   allowlisten hindrer ukjente verifikatorer, men ikke forfalskede
   attestasjoner fra en kompromittert prosess innenfor. I samme sekund
   som motoren tar imot forespørsler over nettverk, skal attestasjoner
   bære signatur/HMAC med nøkler per verifikator, og motoren skal avvise
   attestasjoner uten gyldig signatur. Nøkkelhåndtering (generering,
   rotasjon, lagring i miljø-secrets på staging) er del av PR-004-scope.
4. **Tenant-isolasjon i skjemaet fra første migrasjon:** loggtabell og
   tellertabell har `tenant_id` som del av nøkkel/indeks, og alle spørringer
   filtrerer på tenant.

## Konsekvenser

- PR-004 vokser: PostgreSQL-migrasjoner, DB-tilkobling med fail-closed
  semantikk (utilgjengelig DB => STOPP, aldri «fortsett uten logg»),
  nøkkelinfrastruktur for attestasjoner, og oppdaterte tester.
- Staging (Cloud Server S) trenger PostgreSQL installert før PR-004 kan
  staging-testes — legges i oppsettinstruksen.
- `docs/DEPLOY.md`-prinsippet «ingen tilstand i API-prosessen» får sin
  første håndhevede implementasjon.
