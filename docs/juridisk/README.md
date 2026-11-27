# Vilkår, personvern og databehandleravtale

**Tekstene bor i databasen, ikke her** (migrasjon 235, eiers ordre 27.11.2026:
«alt må betjenes via dashboard … ikke gjennom koden»).

- Plattformeieren redigerer og publiserer dem i **Plattform → «Vilkår og
  personvern»**.
- Leverandørens opplysninger (`{{navn}}`, `{{orgnummer}}`, `{{adresse}}`,
  `{{kontakt_epost}}`, `{{verneting}}`) fylles inn fra **Plattform → «Om
  Disponit»** (234) når sidene vises.
- Sidene: `/vilkar`, `/personvern`, `/databehandleravtale`.
- Når vilkår og databehandleravtale er publisert, krever registreringen at
  kunden godtar dem. `firma_samtykke` sier hvilken versjon hvert firma godtok,
  av hvem og når.
- En publisert versjon endres aldri; en endring er en ny versjon.

Startutkastene (versjon 1, ikke publisert) kom fra #632. Fakta i dem som er
hentet fra koden: prøveperioden er 30 dager (`PROVE_DOGN`); pause og eksport
etter utløp (#630, #631); e-post slettes etter 90 dager (088); 30 dagers
angrefrist etter oppsigelse (190); kontaktpunkt slettes 90 dager etter
avvikling (184); kryptering per firma; ingen språkmodell (eiers vedtak 27.11).
Serveren står hos one.com/dogado GmbH i Tyskland (EØS), slått opp i RIPE 27.11.
