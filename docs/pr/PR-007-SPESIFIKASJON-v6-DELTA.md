# PR-007 SPESIFIKASJON v6 — DELTA (fem sett-integritetsfunn → GO)

**Draft: Claude.ai · Modell (b) + form A + v1–v5 står. Reviewens
anbefalte løsninger vedtatt direkte. Fem punkter, alle om settets
integritet under samtidighet.**

## 1. Settet fryses ved klassifisering — snapshot, ikke oppslag

Vilkårssettet bestemmes ÉN gang ved første klassifisering og LAGRES på
saken; det slås aldri opp på nytt mot aktiv policy under fase 1/2.

Migrasjon 007: `unntak.krav_sett JSONB NOT NULL` (kolonnelåst etter
opprettelse) = det komplette settet påkrevde vilkår for sakens handling,
utledet fra `policy_content_hash`-versjonen saken alt binder (v3 pkt. 6).
Endrer aktiv policy vilkårssettet etterpå, påvirker det ALDRI en
pågående sak. Klassifisering (innhentbar/ikke-innhentbar-splitt, v5
pkt. 1) kjøres på `krav_sett`, ikke på live-policyen. Fase 2s
komplett-sett-sjekk måler mot `krav_sett`. Policy-drift under behandling
logges som historikk `policy_endret_under_reparasjon`, men endrer ikke
settet saken behandles mot.

## 2. Bevisinnsamling er atomisk per generasjon — ett kall, hele settet

v5s «ett bevis per vilkår, samme generasjon» åpnet for delvis
akkumulering over flere kall. Lukkes:

`registrer_verifikasjonsbevis` mottar HELE settet av signerte
attestasjoner i ÉN `verifikasjonskvittering_v1` og skriver alle bevis +
setter generasjonsstatus i ÉN transaksjon under `FOR UPDATE` på
generasjonsraden (v4 vilkår 1-låsen). Enten committes hele settet med
generasjon `positiv`, eller ingenting. Det finnes INGEN mellomtilstand
der noen bevis er lagret og generasjonen fortsatt `aktiv`.

- Kvittering som ikke dekker hele `krav_sett` → generasjon `negativ`,
  ingen bevis lagret (ikke delvis), sikkerhets-/historikkevidens om
  hvilke som manglet (metadata).
- To kvitteringer for samme generasjon → første committer vinner
  (v4 vilkår 1); andre er idempotent (identisk sett-hash) eller konflikt.
- `bevis_vilkaar`-UNIQUE innen generasjonen består som andre forsvarslinje.

Dermed er «bevis for ett vilkår finnes, resten mangler»-tilstanden
strukturelt umulig, ikke bare uønsket.

## 3. Sett-hash binder kvittering og bevis til det frosne settet

For idempotens og anti-manipulasjon: `krav_sett_hash =
SHA-256(kanonisk sortert krav_sett)`. Både verifikasjonsoppdraget,
`verifikasjonskvittering_v1` og generasjonsraden bærer `krav_sett_hash`.
Ingest avviser (sikkerhet) en kvittering hvis:
- kvitteringens `krav_sett_hash` ≠ sakens (kvittering for et annet sett),
- attestasjonssettet i kvitteringen ≠ nøyaktig `krav_sett`s innhentbare
  vilkår (verken færre eller flere — et ekstra, uventet attestert vilkår
  er også avvik).
Resultathash for idempotens (v4) beregnes over
`(krav_sett_hash, sorterte attestasjon-jti-er, attestert_resultat)`.

## 4. Ikke-innhentbar oppdaget under fase 1 → hele settet manuelt

v5 splittet innhentbar/ikke-innhentbar ved klassifisering. Men en
verifikator kan RAPPORTERE at et vilkår antatt innhentbart likevel ikke
kan attesteres autoritativt (kilden mangler dataene). Regel:

Verifikasjonskvittering kan per vilkår melde `attestert | ikke_attesterbar
| negativ`. Inneholder settet ETT `ikke_attesterbar` → generasjon
`negativ` OG saken → `manuell` DIREKTE (ikke retry) med historikk
`vilkaar_ikke_innhentbar`. Retry gir mening kun ved forbigående
`negativ`/utløp, ikke ved prinsipiell u-innhentbarhet — ellers brenner vi
budsjett på en sak som aldri konvergerer. `negativ` (forbigående) →
retry-klar/manuell per budsjett (v4/v5 uendret).

## 5. Fase 2 revaliderer settet mot nåtid før beslutning

Mellom fase 1-positiv og fase 2-claim kan en attestasjon i settet ha
utløpt (hver bærer egen `utloper`). Fase 2 (i den fenced claimen, før
den bygger hendelsen):
- Sjekker at ALLE bevis i generasjonen fortsatt er gyldige (`now() <
  utloper`) og at settet fortsatt matcher `krav_sett_hash`.
- Ett utløpt bevis → saken → `verifikasjon_retry_klar` (ny generasjon,
  hele settet re-verifiseres) eller `manuell` ved budsjettslutt. Fase 2
  bygger ALDRI en hendelse med et delvis utløpt sett.
- Består alt → bygg hendelse med komplett sett, send til motor (v5 pkt. 4).

Motoren er fortsatt siste kontroll: skulle et vilkår mot formodning være
utløpt ved evaluering, gir motoren `attestasjon_utlopt` → UNNTAK, og
saken fanges på nytt. Dobbelt fail-closed.

## Bindende tester (i tillegg til v4/v5)

Policy endres mellom klassifisering og fase 2 → saken behandles mot
frosset `krav_sett`, ikke nytt · kvittering med delvis sett → null bevis
lagret, generasjon negativ · kvittering med ekstra uventet vilkår →
avvist · to kvitteringer samme generasjon → én vinner, sett-hash-idempotens
· ett `ikke_attesterbar` i settet → manuell direkte, ingen retry · ett
bevis utløpt mellom fase 1 og fase 2 → retry/manuell, aldri delvis
hendelse · krav_sett_hash-mismatch → sikkerhet.
