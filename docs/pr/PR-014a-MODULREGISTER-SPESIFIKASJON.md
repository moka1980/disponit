# PR-014a SPESIFIKASJON — Modulregister, kontraktbinding og aktiveringsport

**Draft: Claude.ai · Første av tre. Plattforminfrastruktur ALLE senere
eiermoduler arver — derfor egen port. Ingen WCAG-spesifikk logikk her.**

Rekkefølge: **014a (dette)** → 014b (domene, sandkasse, egress, artefakt)
→ 014c (automatisk WCAG-kontroll).
Forutsetning: `m37_unntak` modulaksept lukket.

## 1. Autoritativt, versjonert modulregister (migrasjon 013)

Python-state kan ikke låses på tvers av prosessbilder. Registeret blir
DB-autoritativt:
```sql
modulregister(
  modul_id TEXT PRIMARY KEY,
  manifest_hash TEXT NOT NULL,
  kontraktversjon INT NOT NULL,          -- payload-/kvitteringsskjema
  oppdragstyper TEXT[] NOT NULL,
  status TEXT NOT NULL
    CHECK (status IN ('installert','staging_verifisert','aktiv','nodeaktivert')),
  status_ts TIMESTAMPTZ NOT NULL DEFAULT now()
);
modulregister_hendelse(...)               -- append-only, alle overganger
CREATE TABLE registerhode(              -- monoton versjon for hele registeret
  id BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),
  registerversjon BIGINT NOT NULL DEFAULT 0
);
```
- **`registerversjon`** økes ved hver registerendring, under lås.
- Filbaserte manifester leses ved deploy og **synkroniseres inn** med
  `manifest_hash`; avvik mellom disk og register → deploy stopper
  (samme mønster som semantikkmanifestet i PR-013 V8).

## 2. Tre tilstander bryter bootstrap-sirkelen

`installert → staging_verifisert → aktiv`, pluss `nodeaktivert` (§4).
- **Produksjonspolicy krever `aktiv`.**
- **Staging-selen kan teste en `installert` modul** — men KUN via
  testkapabilitet (§5).
- Overganger er auditerte, append-only, og krever
  `modules:manage`-scope.

## 3. Kontraktbinding: modul-ID er ikke nok

En aktiv modul kunne oppgraderes til nytt payload- eller
kvitteringsskjema uten at policyen endret seg. Policyens handling
refererer derfor:
```yaml
modul: m_wcag_audit
oppdragstype: audit.revider
kontraktversjon: 1
manifest_hash: "<sha256>"        # bundet ved aktivering
```
- **Runtime verifiserer samme binding FØR oppdragsopprettelse** — avvik →
  UNNTAK, aldri utførelse mot feil kontrakt.
- **Moduloppgradering som bryter kontrakten krever ny policyaktivering.**
  Bakoverkompatibel oppgradering (samme `kontraktversjon`) krever det ikke,
  men `manifest_hash` endres → policyen må rebindes med mindre den
  eksplisitt binder kun `kontraktversjon` (velges per handling, default:
  bind begge).

## 4. Aktiveringsporten (utvider PR-013 lag 3)

- **Utkastvalidering:** modul-/oppdragstype som ikke finnes gir
  **ADVARSEL** (forfatteren ser det mens hun skriver). Valideringen
  forblir deterministisk; registerkontrollen rapporteres som **separat
  miljøkontroll**, ikke som del av det deterministiske resultatet.
- **Åpning av aktiveringsrunde:** HARD FEIL hvis modul mangler, ikke er
  `aktiv`, eller kontraktbindingen avviker.
- **Ved aktivering:** kontrolleres PÅ NYTT under låsen
  (PR-013 V4-rekalkulering). **Aktiveringsrunden binder
  `registerversjon`**; endret versjon → runde kansellert.

## 5. Deaktivering: normal vs. nød — ingen uspesifisert fullmakt

| Type | Regel |
|---|---|
| **Normal deaktivering** | **BLOKKERT** mens aktive policyer refererer modulen. Forfatteren må først aktivere en policy uten handlingen |
| **Nøddeaktivering** | **Alltid mulig**, krever `modules:emergency`-scope, auditert med begrunnelse. Nye handlinger → **UNNTAK umiddelbart**. **Ingen utførelse etter nøddeaktivering** — pågående claim kan ikke kvitteres inn som gyldig |

Nøddeaktivering setter status `nodeaktivert` (egen tilstand, ikke
tilbake til `installert`), slik at den er synlig i revisjonen.

## 6. Testkapabilitet — egen issuer, egen nøkkel, ikke et miljøflagg

Staging-selen tester `installert` moduler med en
**testkapabilitet** som:
- har **separat issuer og audience**,
- signeres med en **nøkkel som ikke finnes i produksjonsartefaktet**
  (verifiseres ved deploy: produksjonsbygg uten testnøkkel),
- **omgår KUN modulstatus** — aldri domeneeierskap, egress, policy eller
  noen annen kontroll,
- er bundet til en egen tenant-klasse (`test`), aldri en kundetenant.
**Et miljøflagg er ikke tilstrekkelig og brukes ikke.**

## 7. Fire samtidighetsspørsmål

| Kontroll | Alle veier inn? | Samtidighet? | Riktig vs velformet? | Lukket format? |
|---|---|---|---|---|
| Modulstatus | DB-autoritativt, alle prosesser | `registerversjon` bundet i runde, revalidert under lås | Krever `aktiv` OG matchende kontrakt | CHECK-enum |
| Kontraktbinding | Runtime før oppdragsopprettelse + aktivering | Manifest endret mellom → avvik fanges | Hash, ikke navn | Eksplisitt felt i policy |
| Nøddeaktivering | Kun `modules:emergency` | Pågående claim kan ikke kvitteres etterpå | Auditert m/ begrunnelse | Egen status |
| Testkapabilitet | Egen issuer/nøkkel | — | Omgår kun status | Miljøbundet nøkkel, ikke flagg |

## 8. Evidensgrense `modulregister-v1` (defineres FØR arbeidet)
Modul uten `aktiv` kan ikke aktiveres i policy · advarsel ved
utkastvalidering, hard feil ved runde-åpning · `registerversjon` endret
mellom åpning og aktivering → runde kansellert · kontraktversjon-avvik →
UNNTAK før oppdragsopprettelse · normal deaktivering blokkert med aktiv
referanse · nøddeaktivering → nye handlinger UNNTAK, pågående kan ikke
kvitteres · testkapabilitet virker på `installert` i test-tenant, avvises
i produksjonstenant · produksjonsartefakt inneholder ikke testnøkkelen
(deploy-port) · manifest på disk ≠ register → deploy stopper.

## Spørsmål til ChatGPT
1. **Default for kontraktbinding:** jeg har valgt at policyen binder BÅDE
   `kontraktversjon` og `manifest_hash` som default, med mulighet for kun
   `kontraktversjon` per handling. Er det riktig, eller bør
   `manifest_hash` alltid bindes (og dermed enhver moduloppgradering
   kreve ny policyaktivering)?
2. **Nøddeaktivering og pågående oppdrag:** jeg sier «ingen utførelse
   etter nøddeaktivering, pågående claim kan ikke kvitteres inn som
   gyldig». Men eiermodulen kan allerede ha utført sideeffekten. Skal en
   slik kvittering avvises (og saken gå til avklaring, som gate 14a), eller
   lagres som sen evidens uten å avslutte oppdraget?
3. **`registerversjon` som global teller** vs. per modul: global gir
   enklere binding, men betyr at enhver registerendring kansellerer alle
   åpne aktiveringsrunder. Er det for aggressivt?
