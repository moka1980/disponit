# Modulkontrakt — m_wcag_audit, kontraktversjon 1

Dette er DOKUMENTET bak `modulkontrakt.kontrakt_hash`: sha256 over denne
filens bytes registreres immutabelt av `registrer_kontrakt()` ved deploy
(PR-014a). Endres kontrakten, bumpes kontraktversjonen — raden endres aldri.

## Identitet
- **modul_id**: `m_wcag_audit` (katalognummer tildeles ved aksept;
  mappen i repoet er `platform/modules/wcag_audit/`)
- **oppdragstype**: `kontroll.wcag.nettsted` (registrert type, eier
  `kontroll.wcag.`-prefikset i claim-veien)
- **artefakttype**: `kontroll.wcag.rapport` (rapportskjemaet er
  innholdsadressert i `artefaktskjema`; hash regnes av
  `rapportskjema.skjema_hash()`)

## Klassifisering
- **sideeffektklasse**: `ekstern_lesing` — modulen leser på nettet, mot
  positivt autoriserte mål (målautorisasjonsvilkår + frekvensgrense
  håndheves ved policyaktivering; verifisert hostname ved bestilling).
- **reversibilitet**: `direkte` — modulen er stateless utover outboxen;
  ingen sideeffekt å reversere hos målet (kun lesing).

## Grensen mot motoren (PR-014c §2)
Modulen er KUNDE av kontrollmotoren (axe-core i headless Chromium i
browser-containeren, uten credentials). Alt motoren produserer er
ubetrodd inndata: controlleren skjemavaliderer før innsending, og
plattformen validerer ved opplasting OG promotering. Motorens
miljø er allowlistet; payloaden når den på stdin.

## Payload (det modulen SER — port 5)
Skjema: `payload-skjema.json` i denne mappen.
**payload_schema_hash**: `44b2bd8a91d21d94a99c5961621809fa3cb778cf995d7333b1185a583ca9cc66`

## Kvittering (PR-006, signert)
Skjema: `kvittering-skjema.json` i denne mappen.
**kvittering_schema_hash**: `95449df4742a4964342dc9b6c2d42ff9c210370d35f38c20c01d90c483c8c4aa`

Kvitteringen attesterer: *denne releasen kjørte dette regelsettet mot
disse sidene på dette tidspunktet, og produserte artefaktet med denne
hashen* — aldri «nettstedet oppfyller WCAG».

## Frister
- enkeltside: 30 min · nettsted: 60 min (deklarert i
  `oppdragskontrakt.UTFORELSESFRIST_VALG`, skrevet på oppdragsraden ved
  opprettelsen; motoren får claimets faktiske restvindu minus
  avslutningsmarginen).
