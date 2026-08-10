# PR-012 — GATE 14b: REGISTRERT SOM EGET M-37-OUTBOX-ARBEID

**Status: REGISTRERT (ikke i PR-012). Egen spesifikasjon + evidensgrense
kreves før bygg.** Denne fila lukker «gate 14b gjenstår» ved å registrere
arbeidet formelt, slik scope-beslutningen (claude.ai) foreskrev — den er
ikke en spesifikasjon og ikke en implementering.

## Hvorfor 14b er skilt ut

Scope-beslutningen (`PR-012-GATE14-SCOPEBESLUTNING.md` §1 + §3, fra
spesifikasjonsforfatteren) delte gate 14:

- **14a — VAKTEN (levert i PR-012):** `avvis` på en sak med et LEVENDE
  oppdrag/kapabilitet utfører ALDRI avvisningen; saken forblir `manuell`,
  `avklaring_kreves` settes, svaret er `409 utestaaende_oppdrag`. Garantien:
  *systemet påstår aldri «ikke utført» når databasen ikke kan bevise det.*
  Oppdraget røres ikke — ingen kansellering, ingen fencing, ingen
  kompensasjon.
- **14b — OPPLØSNINGEN (dette arbeidet, IKKE i PR-012):** hva som faktisk
  skal SKJE med en sak som har utestående oppdrag og et menneske som vil
  avvise. Det er oppdrags-livssyklus og hører i M-37-outbox-domenet.

Sitat, scope-beslutningen §3: «14b registreres som eget arbeidselement med
egen spesifikasjon og evidensgrense, i M-37-outbox-domenet. Ikke i PR-012.»

## Løpet 14b må håndtere (fra scope-beslutningen §0)

```
R1/godkjenn → TILLAT → oppdrag opprettet → sak venter_utførelse
   → oppdragsfristen løper ut MENS eiermodulen fortsatt jobber
   → sak → manuell (frist utløpt)            ← oppdraget står 'plukket'
   → menneske i køen trykker AVVIS → 14a: 409 utestaaende_oppdrag (nå)
   → 14b: HVA nå?                             ← dette arbeidet
```

## Åpne spesifikasjonsspørsmål (må avgjøres av spec-forfatter før bygg)

1. **Oppløsningsstrategi:** kansellering med fencing mot eiermodulen · en
   kompenserende handling · eller ventet kvittering før avgjørelse. Disse
   er gjensidig utelukkende og har ulik sikkerhetsprofil.
2. **Fencing-kontrakt:** hvordan hindre at eiermodulen utfører ETTER at
   avvisningen er besluttet (owner-fencing symmetrisk med sakens claim —
   `oppdrag.owner_claim_id`/`owner_generation` finnes alt i skjemaet).
3. **Idempotens/rekkefølge** mot M-37-outboxen: avvis-intensjon vs.
   ankommende kvittering — nøyaktig én vinner, og saken skifter aldri til en
   tilstand som motsier den andre.
4. **Evidensgrense:** hva den deploy-verifiserte artefakten for 14b må måle
   (analogt med `behandling-m37-v1`), og hvilke mutasjoner som må dø.

## Grensesnittet 14b arver fra 14a (allerede på plass)

- `avklaring_kreves`-hendelsen (migrasjon 011) har nå sin writer
  (`_flagg_avklaring`); 14b konsumerer den flaggede tilstanden.
- Den utestående tilstanden er lesbar via `sak_utestaaende(tenant,
  unntak_id)` (SECURITY DEFINER, m37_claimer-eid) — samme kilde 14b vil
  bruke for å velge oppløsning.
- `oppdrag`-fencing-feltene (`owner_claim_id`, `owner_generation`,
  `owner_lease_utloper`) finnes fra migrasjon 005.

## Disposisjon

- **PR-012 merger UTEN 14b**, med gate 14 i sin 14a-form (scope-beslutningen
  §3: «Ingen port strykes»).
- 14b bygges som eget arbeidselement når spesifikasjonen foreligger.
- Sporet i **GitHub-issue #24** slik at det ikke faller mellom stolene ved
  merge.
