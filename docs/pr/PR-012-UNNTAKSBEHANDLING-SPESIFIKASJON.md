# PR-012 SPESIFIKASJON — Unntaksbehandling v1 (til ChatGPT-porten)

**Draft: Claude.ai · Første HANDLING i Disponit — mennesket griper inn i
M-37-køen. Bygger på eksisterende maskineri (statusmaskin, fencing,
outbox, attestasjoner, `exceptions:manage`-scopet som har vært reservert
siden PR-005b). Redesigner ingenting av det.**

## 0. Det bærende prinsippet: mennesket gir en ATTESTASJON, ikke en beslutning

Den farlige designfellen: en «godkjenn»-knapp som flipper status ville
omgått hele policymotoren — en betaling stoppet på beløpsgrense ville
blitt utført forbi grensen med ett klikk. **Det skjer ikke.**

Menneskelig godkjenning er en **signert attestasjon** som mates inn i en
NY beslutning gjennom motoren — nøyaktig samme mønster som R1s fase 2
(PR-007). Policyen bestemmer om, og under hvilke grenser, en menneskelig
attestasjon er tilstrekkelig. Konsekvensen:
- Godkjenner et menneske noe policyen ikke tillater et menneske å
  godkjenne → motoren stopper det igjen. Mennesket kan ikke overstyre
  policyen, kun oppfylle et vilkår policyen har definert.
- Alle sideeffekter går gjennom outbox og krever eiermodul-kvittering.
- Alt havner i revisjonsloggen med hvem, når og hvorfor.

**Mennesket får ikke nye fullmakter — det får en rolle policyen allerede
har definert plass til.**

## 1. Policy-utvidelse: hva et menneske kan attestere

Policy-skjemaet får (bakoverkompatibelt, valgfritt) per handling:
```yaml
menneskelig_overstyring:
  tillatt_for_kategorier: [manglende_data, teknisk_feil]   # lukket liste
  krever_rolle: okonomiansvarlig                            # eksisterende rolle
  belop_maks: "50000.00"                                    # eget tak, kan avvike
  krever_fire_oyne: true                                    # to ulike godkjennere
  begrunnelse_pakrevd: true
```
- Mangler feltet → **ingen menneskelig godkjenning er mulig** for den
  handlingen (deny-by-default, som alt annet).
- `belop_maks` her er et EGET tak — det arver ikke automatisk handlingens
  auto-grense, og kan være både høyere og lavere. Eksplisitt, aldri utledet.
- Kategorier utenfor listen kan aldri godkjennes menneskelig, kun avvises.

## 2. Tre handlinger — og hva de faktisk gjør

| Handling | Hva skjer | Sideeffekt |
|---|---|---|
| **Avvis** | Saken → `avvist` (terminal). Ingen forretningshandling utføres noensinne | Ingen |
| **Eskaler** | Saken → `manuell` med `eskalert_til`-rolle + begrunnelse. Ingen beslutning | Ingen |
| **Godkjenn** | Menneskelig attestasjon signeres → NY beslutning gjennom API+motor → TILLAT+sideeffekt gir oppdrag og `venter_utførelse`; kun eiermodul-kvittering gir `løst` | Kun via outbox |

**Avvis** er den trygge handlingen og skal alltid være tilgjengelig for
den som har scopet. **Godkjenn** er kun tilgjengelig når policyen sier
det (§1) og alle vilkår er oppfylt.

## 3. `tillatte_handlinger[]` er server-returnert (aldri klientutledet)

`GET /v1/unntak/{id}` utvides med:
```
tillatte_handlinger: [{handling: "avvis"|"eskaler"|"godkjenn",
                       aarsak_utilgjengelig?: <kode>}]
saksversjon: <int>          # optimistisk lås
```
- Serveren utleder listen fra: sakens status, sakstype, kategori,
  policyens `menneskelig_overstyring`, brukerens rolle/scopes, og
  fire-øyne-status.
- **UI viser KUN det serveren returnerer** (TilgangsVakt-komponenten fra
  UI-spec §7). Klienten utleder aldri fra status eller rolle.
- Utilgjengelig handling kan vises grået med `aarsak_utilgjengelig`-kode
  (oversatt via locale) — så brukeren forstår hvorfor, uten at UI gjetter.

## 4. Statusmaskin-utvidelse (migrasjon 010)

`manuell` har vært terminal. Nå åpnes ÉN kontrollert vei ut — den
«separate, eksplisitte og auditerte administrative gjenåpningen» som ble
deklarert i PR-007:
```
manuell → under_behandling   (KUN via godkjenn-handlingen, fenced,
                              med menneskelig attestasjon registrert)
manuell → avvist             (avvis-handlingen, terminal)
manuell → manuell            (eskaler: samme status, ny eskalert_til + historikk)
```
- Overgangene håndheves av trigger som alle andre; `løst` og `avvist`
  forblir absolutt terminale.
- Gjenåpning krever gyldig menneskelig attestasjon i SAMME transaksjon —
  ingen naken statusendring er mulig.
- Alt skriver `unntak_historikk` med aktør fra sesjonskontekst.

## 5. Menneskelig attestasjon (ny tabell, migrasjon 010)

```sql
CREATE TABLE menneskelig_attestasjon (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant TEXT NOT NULL, unntak_id BIGINT NOT NULL,
  bruker_id TEXT NOT NULL, rolle TEXT NOT NULL,
  handling TEXT NOT NULL CHECK (handling IN ('avvis','eskaler','godkjenn')),
  begrunnelse_kryptert BYTEA, key_id TEXT, nonce BYTEA,  -- tenant-DEK
  saksversjon INT NOT NULL,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant, unntak_id) REFERENCES unntak (tenant, id)
);
```
- **Append-only** (trigger), RLS+FORCE.
- Begrunnelse krypteres med tenant-DEK (kan inneholde forretnings- eller
  persondata) — samme envelope som unntakspayload.
- Attestasjonen bæres inn i den nye beslutningen som et vilkårsbevis
  (`vilkaar: menneskelig_godkjenning`, verifikator = sesjonens
  bruker_id + rolle, verifisert av serveren — ikke klientpåstand).

## 6. Fire øyne (når policyen krever det)

`krever_fire_oyne: true` → godkjenning krever **to ulike `bruker_id`**
med påkrevd rolle:
- Første godkjenning registreres, saken forblir `manuell`,
  `tillatte_handlinger` for FØRSTE godkjenner viser ikke lenger «godkjenn».
- Andre godkjenning (annen bruker) utløser den nye beslutningen.
- Samme bruker kan ALDRI være begge (DB-constraint på
  `(tenant, unntak_id, bruker_id, handling='godkjenn')` UNIQUE).
- Første godkjenning har utløp (24 t) — deretter må begge gjøres på nytt.

## 7. Idempotens, saksversjon og konflikt

`POST /v1/unntak/{id}/handling`:
```
Headers: X-Disponit-CSRF, Idempotency-Key
Body: {handling, saksversjon, begrunnelse?, eskalert_til?}
```
- **`saksversjon` må matche** sakens nåværende versjon → ellers `409`
  med lukket kode `saksversjon_utdatert`; UI laster saken på nytt og
  viser hva som endret seg. **Aldri blind retry.**
- `Idempotency-Key` gjenbruker eksisterende idempotensmekanikk (PR-005a):
  samme nøkkel + samme input → lagret respons; annen input → `409`.
- Hele operasjonen (attestasjon + statusendring + historikk + eventuell
  ny beslutning) i ÉN transaksjon, med fencing mot sakens
  `claim_generation` slik at en M-37-arbeider ikke samtidig endrer saken.
- Scope: `exceptions:manage` (finnes reservert). `exceptions:read` alene
  gir ingen handlinger.

## 8. Fire samtidighetsspørsmål besvart

| Kontroll | Alle veier inn? | Under samtidighet? | Riktig vs velformet? | Lukket format? |
|---|---|---|---|---|
| Handling tillatt | Kun `/handling`-ruten; server utleder listen | To brukere samtidig: saksversjon → én vinner, andre får 409 | Sjekker policy + rolle + kategori, ikke bare scope | Lukket handlingsenum |
| Godkjenn = ny beslutning | Går gjennom API+motor som alle beslutninger | Fenced mot claim_generation | Motoren evaluerer på nytt mot aktiv policy | Ingen direkte statusflipp finnes |
| Fire øyne | DB-UNIQUE, ikke bare app-logikk | To samtidige andre-godkjenninger → én vinner | Krever ulik bruker_id, ikke ulik sesjon | Utløp definert |
| Gjenåpning fra manuell | Kun med attestasjon i samme tx | Trigger håndhever overgangen | `løst`/`avvist` forblir absolutt terminale | Statusenum utvidet, ikke åpnet |

## 9. UI (bygger `HandlingDialog` — nå med protokoll bak)

Unntaksdetaljen får handlingsknapper KUN fra `tillatte_handlinger[]`.
`HandlingDialog` (planlagt i UI-spec §7, bevisst ikke bygget før nå):
beskriver konsekvensen i klartekst, krever begrunnelse der policyen sier
det, sender `saksversjon` + `Idempotency-Key` + CSRF, håndterer `409`
med ny lasting og forklaring. Ved fire-øyne vises status «venter på
andre godkjenner». Alle fem skjermtilstander som ellers.

## 10. Evidenskrav (`behandling-m37-v1`, defineres FØR arbeidet)

Injiser 12 saker over 4 kategorier: avvis-vei terminal · godkjenn-vei gir
NY beslutning som motoren evaluerer (og som STOPPER når policyen ikke
tillater menneskelig godkjenning — negativ avgrensning) · sideeffekt →
`venter_utførelse` → kvittering → `løst` · fire-øyne krever to brukere ·
saksversjonskonflikt gir 409 uten sideeffekt · samtidig
M-37-arbeider + menneskelig handling → nøyaktig én vinner · ingen
klartekst-begrunnelse i logg eller DB-dump · alle handlinger i
revisjonsloggen med aktør.

## Spørsmål til ChatGPT

1. Er «menneskelig godkjenning som attestasjon inn i ny beslutning»
   riktig grense — eller ser du et tilfelle der et menneske MÅ kunne
   avslutte en sak som policyen ikke kan re-evaluere?
2. `manuell → under_behandling` er den første veien ut av en terminal-
   lignende tilstand vi åpner. Er trigger + påkrevd attestasjon i samme
   transaksjon tilstrekkelig, eller bør gjenåpning ha egen status
   (`gjenapnet`) for å være synlig i historikken?
3. Fire-øyne med 24 t utløp på første godkjenning: rimelig, eller bør
   ufullstendig fire-øyne ha en egen synlig tilstand i køen?
