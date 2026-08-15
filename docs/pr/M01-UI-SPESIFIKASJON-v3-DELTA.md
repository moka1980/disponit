# M-1 UI-SPESIFIKASJON v3 — DELTA (to detaljkontrakter → GO)

**Draft: Claude.ai · v1 + v2 står. Reviewens anbefalte kontrakter vedtatt
direkte. Lukker inkonsistensen: v2 spesifiserte lister, men skjermene
åpner detaljer.**

## 1. Unntaksdetalj: eget endepunkt (backend-avhengighet nr. 4)

Listeendepunktet forblir lett metadata; historikk hører ikke i hver
listerad. Nytt:
```
GET /v1/unntak/{id}
  scope: exceptions:read
  tenant: utledes fra sesjonen (aldri fra klient)
  200: {
    id, ts, handling,
    kategori   [enum: manglende_data|over_grense|regelkonflikt|
                      teknisk_feil|ukjent|svindelmistanke|hms_avvik],
    sakstype   [enum: normal|sikkerhet|drift],
    status     [enum: ny|under_behandling|løst|avvist|manuell|
                      venter_verifikasjon|verifikasjon_klar|
                      verifikasjon_retry_klar|venter_utførelse],
    prioritet  [enum: normal|hoy],
    begrunnelse[kodeliste, display-safe],
    historikk  [cursor-paginert hvis stor:
               {hendelse, fra_status, til_status, ts}]
  }
  404: ukjent ID OG annen tenants ID (ingen lekkasje av eksistens)
```
- ALDRI payload (kryptert/dekryptert), attestasjoner, tokens eller nøkler.
- Enum-verdiene over speiler faktiske maskinkoder fra migrasjon 003/007 —
  ikke UI-oppfunne. Frontend viser dem via `KodeForklaring` med fallback.
- Historikk cursor-pagineres (kan vokse med retry-generasjoner).

Backend-avhengigheten er dermed FIRE endepunkter: `GET /v1/unntak`
(liste), `GET /v1/unntak/{id}` (detalj), `GET /v1/beslutninger`,
`GET /v1/oversikt`, `GET /v1/policy/aktiv`. (Rettelse: fem totalt —
v2 sa tre nye, detaljene gjør det til fire nye + eksisterende liste.)

## 2. Beslutningsdetalj: separate nullbare strukturer, ikke sammenslått

v2s `utfrt_status?` var utilstrekkelig og navnemessig uklar. Rettet —
UI konstruerer ALDRI status ved å kombinere felter; serveren leverer dem
adskilt:
```
GET /v1/beslutninger/{id}
  scope: decisions:read
  200: {
    policybeslutning [enum: TILLAT|STOPP|UNNTAK],
    policy_versjon, policy_hash, beslutning_ts,
    utførelse:  { status [enum: VENTER|UTFØRT|FEILET|IKKE_RELEVANT],
                  oppdrag_id? } | null,
    kvittering: { status [enum: MANGLER|GYLDIG|SEN|KONFLIKT|
                          IKKE_RELEVANT] } | null,
    unntak:     { id, kategori, status } | null,
    sikkerhet:  { sak_finnes: bool }
  }
  404: ukjent/annen tenant
```
- Listeendepunktet (`GET /v1/beslutninger`) returnerer REDUSERT rad
  (ts, handling, policybeslutning, begrunnelse-koder); detaljpanelet
  henter full struktur via `{id}`.
- **`TILLAT` ≠ utført:** utførelse-strukturen er separat og kan være
  VENTER selv når policybeslutning er TILLAT — presist det skillet v2
  pkt. 5 krevde, nå i responsskjemaet, ikke bare i visningen.
- Eksakte enum-navn låses mot backendens faktiske koder i backend-PR-en;
  navnene over er kontraktforslaget som verifiseres der.

## 3. Fire bindende presiseringer (vedtatt)

1. **Backend-PR er hard port.** Frontend kan bruke kontrakt-genererte
   mocks, men kan ikke integreres eller erklæres ferdig før endepunktene
   OG negative tenanttester (kryss-tenant → 404) er merget.
2. **Display-safe fra server.** Serveren returnerer display-safe
   begrunnelseskoder; frontend mottar ALDRI sensitiv begrunnelsestekst og
   skal ikke maskere selv. `SensitiveData`-komponenten håndterer «ikke
   tilgjengelig»-tilstand, ikke redaksjon av mottatt sensitivt innhold.
3. **`HandlingDialog` bygges ikke nå.** Beholdes i designsystemets plan,
   implementeres først med den fremtidige behandlingsprotokollen — en
   ubrukt sikkerhetskritisk komponent bygges ikke på spekulasjon.
4. **Breakpoint og sr-only:** `--bp-narrow` kan ikke stå i vanlig
   `@media`. Løsning: én dokumentert breakpoint-konstant generert fra
   samme tokenkilde (build-time custom-media eller PostCSS), ikke en
   CSS-variabel i media-query. `sr-only` er en utility i felles base-CSS,
   ikke et designtoken — flyttes ut av tokens.css.

## Status: alle seks P1 lukket
Read-only unntak ✓ · eksakt API-grense ✓ (fem endepunkter, to detalj) ·
tenant/session ✓ · XSS/dataminimering ✓ · beslutning≠utførelse ✓
(separat responsskjema) · tilgjengelig interaksjon ✓. Ingen åpne punkter.
