# PR-009 SPESIFIKASJON v4 — DELTA (tokenrekkefølge + statusmigrasjon)

**Draft: Claude.ai · v1–v3 står; heartbeat-, deploy- og restartmodellene
er godkjent. To P1 + én presisering.**

## 1. Tokenrekkefølge uten selvmotsigelse

v3 testet et `PENDING`-token mot API-et samtidig som `verifiser_token`
kun godtar `AKTIV` — steg 2 kunne aldri lykkes. Rettet til reviewens
sekvens:

1. **Bekreft TTY FØR token genereres** (`isatty` — ingen hemmelighet
   produseres hvis den ikke kan leveres).
2. Opprett `PENDING`.
3. **Lokal verifisering** via avgrenset CLI-/DB-funksjon: hemmeligheten
   matcher lagret MAC og forventet metadata. Dette er IKKE
   API-autentisering — `PENDING` er aldri en API-principal.
4. Vis tokenet på TTY.
5. Operatøren BEKREFTER at hemmeligheten er lagret.
6. Aktiver atomisk (`PENDING → AKTIV`).
7. Test mot API-et som `AKTIV`.
8. Feiler API-testen → tilbakekall tokenet og instruer operatøren om å
   forkaste hemmeligheten.

Krasjkonsekvenser (alle rene):
- Før visning → pending-token ryddes (ingen ukjent aktiv hemmelighet).
- Etter visning, før aktivering → tokenet virker ikke; eksplisitt
  retry/rotasjon, operatøren vet det.
- Etter aktivering → operatøren har allerede hemmeligheten.
**Ingen spesialvei tillater `PENDING` som API-principal** (negativ test).

## 2. Trinnvis statusmigrasjon (samlet DEFAULT ville drept alle tokens)

En samlet `ADD COLUMN status ... DEFAULT 'PENDING'` ville satt alle
eksisterende tokens til PENDING = umiddelbar utestenging. Rekkefølge
(samme mønster som PR-008s koblingsstatus):
```sql
-- 1. nullable, ingen default, ingen CHECK
ALTER TABLE api_tokener ADD COLUMN status TEXT;
-- 2. backfill fra eksisterende sannhet
UPDATE api_tokener SET status = CASE WHEN aktiv THEN 'AKTIV'
                                     ELSE 'TILBAKEKALT' END;
-- 3. valider: ingen NULL igjen (fail-hard ellers)
-- 4. CHECK + NOT NULL
ALTER TABLE api_tokener
  ADD CONSTRAINT api_tokener_status_ck
    CHECK (status IN ('PENDING','AKTIV','TILBAKEKALT')) NOT VALID;
ALTER TABLE api_tokener VALIDATE CONSTRAINT api_tokener_status_ck;
ALTER TABLE api_tokener ALTER COLUMN status SET NOT NULL;
-- 5. default for FREMTIDIGE rader
ALTER TABLE api_tokener ALTER COLUMN status SET DEFAULT 'PENDING';
-- 6. verifikatoren strammes til slutt
```
`verifiser_token` oppdateres til `AND status='AKTIV'` som SISTE steg —
etter at backfillen har gjort alle gyldige tokens AKTIVE. Test:
eksisterende token fortsetter å virke gjennom hele migrasjonen.

## 3. Presisering: helsetelleren nullstilles av RESULTAT, ikke kommando

Telleren nullstilles IKKE fordi en restartkommando ble sendt. Den
nullstilles først når målprosessen har **ny PID/oppstartstid OG igjen
består helsesjekken**. En restart som ikke hjelper skal fortsette å telle
mot start-limit, ikke skjules av en nullstilling.

## Akseptansekriterier (tillegg)
`PENDING`-token avvist av `verifiser_token` (negativ test) · lokal
verifisering fungerer på `PENDING` uten API · TTY-sjekk før generering ·
migrasjon: eksisterende aktivt token virker før, under og etter · alle
tokens har status etter migrasjon, ingen NULL · helsetelleren nullstilles
kun ved ny PID + bestått sjekk.
