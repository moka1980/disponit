# GATE 14a — v2 (tre mekaniske kontrakter → lukket)

**Fra: Claude.ai · Splittet 14a/14b står. Tre kontrakter innarbeidet.**

## 1. Positiv tillatelsesliste, ikke denylist

`opprettet|plukket` var en denylist som ville sluppet gjennom `feilet`,
`utfort`, en utestående kapabilitet, eller en ukjent fremtidig status.
Samme prinsipp som SSRF-vernet i PR-010: **kravet er positivt bevis.**

Avvis tillates KUN når databasen positivt beviser én av:
- **(a)** Intet oppdrag OG ingen utestående arbeidskapabilitet finnes for
  saken, ELLER
- **(b)** Oppdraget ble definitivt `kansellert` FØR claim/utførelse, med
  fencing-evidens (owner_claim_id/generation urørt, ingen kvittering).

**Alle andre tilstander — inkludert `feilet`, `utfort`, ukjente og
fremtidige statuser — gir `avklaring_kreves`.** Garantien overlever
dermed at nye oppdragsstatuser innføres senere: en status vi ikke kjenner
kan aldri gi grønt lys.

## 2. Commit før 409 — flagget må overleve svaret

14a skal både PERSISTERE `avklaring_kreves` og RETURNERE 409. Kaster
handleren en HTTP-feil inne i transaksjonen, rulles flagget tilbake.
Bindende rekkefølge:
1. Lås (`unntak → oppdrag → kapabilitet`) og kontroller mot §1.
2. Sett `avklaring_kreves`, **øk `saksversjon`**, skriv historikk.
3. **COMMIT.**
4. Returner den lagrede 409-responsen (`utestaaende_oppdrag`).

- **Idempotens:** gjentatt identisk forsøk (samme `Idempotency-Key`) gir
  samme lagrede 409 og produserer INGEN nye historikkrader.
- Presisering: «saken uendret» betyr at STATUS fortsatt er `manuell` —
  raden og `saksversjon` endres. UI må laste på nytt (409-håndteringen
  fra v2 §7 gjelder: ny lasting, aldri blind retry).

## 3. Låserekkefølge og bindende utfallsmatrise mot kvitteringsløpet

Avvis låser i etablert rekkefølge `unntak → oppdrag → kapabilitet`.
Kvitteringsingest MÅ bruke samme rekkefølge, eller dokumenteres som
ikke-deadlockbar mot den (verifiseres i deadlock-testen).

| Vinner | Utfall |
|---|---|
| **Kvittering** | Saken følger kvitteringsprotokollen (PR-007/PR-006). Avvis REAUTORISERES etter lås (jf. V7) og avvises — saken er ikke lenger i avvisbar tilstand |
| **14a-vakten** | Status forblir `manuell`, `avklaring_kreves` committes. Senere kvittering behandles etter EKSISTERENDE evidensregler (sen/konflikt → append-only evidens + eventuell sikkerhetssak) |

**I ingen rekkefølge kan saken bli `avvist` mens utførelse er mulig eller
bekreftet.** Det er invarianten hele gate 14 finnes for.

## Bindende tester (14a, endelig)
Sak uten oppdrag/kapabilitet → avvis virker · sak med oppdrag
`kansellert` før claim (fencing-evidens) → avvis virker · sak med
oppdrag i ENHVER annen tilstand (`opprettet`, `plukket`, `feilet`,
`utfort`) → 409 + `avklaring_kreves` committet · **ukjent/ny
oppdragsstatus → 409** (fremtidssikring, testes med syntetisk status) ·
utestående kapabilitet uten oppdrag → 409 · gjentatt forsøk → samme 409,
ingen ny historikkrad · `saksversjon` økt ved flagging · kappløp
kvittering vs. avvis → begge utfall per matrisen, aldri `avvist` ·
deadlock-test: blandet avvis/kvitteringsingest/timeout uten deadlock.
