# M-1 UI — IMPLEMENTERINGSKLARSIGNAL (GO)

**Til Claude Code · Implementér mot main `218d322`, UI-spesifikasjon
v1 + v2/v3/v4-deltaene + konsolidert grunnlag. Branch: `pr-011-m01-ui`.
GO + åtte vilkår i PR-beskrivelsen. Dette er siste leveranse før
innloggingstesten.**

**Korreksjon:** **syv** leseendepunkter — ett eksisterende
(`GET /v1/unntak`) + seks fra PR-008 (`/v1/unntak/{id}`,
`/v1/unntak/{id}/historikk`, `/v1/beslutninger`, `/v1/beslutninger/{id}`,
`/v1/oversikt`, `/v1/policy/aktiv`).

## De åtte bindende vilkårene

### V1. Backend-kontrakten er autoritativ
Alle syv endepunkter brukes med sine FAKTISKE DTO-er. Ingen tidsbasert
kobling, ingen lokal statusutledning, ingen ukjente ekstrafelter. Ukjent
`resultat.art` → kontrollert `Feiltilstand`.

### V2. Sesjon og OIDC følger browserkontrakten
- **OIDC-start som TOPPNIVÅ-NAVIGASJON** — ordinært same-origin
  `<form method="post">`, ALDRI `fetch()` som prøver å følge redirecten
  til IdP gjennom CORS.
- Ingen bearer-token eksponeres til browseren.
- Alle API-kall bruker same-origin credentials.
- Logout og senere mutasjoner sender CSRF-header.
- **`401` → innloggingsflate; `403` → manglende-tilgang-tilstand.**
  Slås ALDRI sammen.

### V3. Tenant bestemmes kun av sesjonen
Workspace vises fra `GET /v1/sesjon`. INGEN lokal tenantvelger,
URL-parameter eller browserlagret tenant påvirker forespørslene.

### V4. UI-CSP lukkes her — som HTTP-header, ikke bare `<meta>`
```
default-src 'none'; script-src 'self'; style-src 'self';
connect-src 'self'; img-src 'self'; font-src 'self'; object-src 'none';
base-uri 'none'; frame-ancestors 'none'; form-action 'self';
manifest-src 'self'; upgrade-insecure-requests;
```
Ingen inline script, inline event handlers eller `eval`. Utvidelser krever
eksplisitt begrunnelse + negativ test. (PR-009b holdt CSP åpen nettopp
for denne leveransen.)

### V5. Beslutning, utførelse og evidens forblir adskilt
UI presenterer mottatte akser, konstruerer ALDRI ny forretningstilstand.
`TILLAT` = tillatt, ikke utført. `UNNTAK` ≠ policy-stopp.
Sikkerhetsseksjon vises KUN når feltet faktisk finnes.

### V6. Dataminimering og XSS på hele browserflaten
Ingen payload, attestasjon, token, sesjonsreferanse eller CSRF-verdi i
DOM, URL, web storage, klientlogger eller analytics. API- og locale-tekst
escapes. Ingen ufiltrert HTML-rendering.

### V7. Tilgjengelighet er ferdighetskrav
Alle fire flater: fem tilstander · full tastaturflyt · korrekt
dialogfokus · tabell-`caption` · fungerende sortering · live-regioner ·
200 % zoom · axe-core uten alvorlige/kritiske funn.

### V8. Mock er ikke ferdig
Mock kun under komponentbygging. Leveransen er IKKE komplett før de fire
flatene OG OIDC-flyten kjører mot ekte staging-endepunkter.

## De fjorten Codex-portene
1. Bygg, typekontroll, lint, enhetstester grønne
2. Kontraktstest mot alle syv leseendepunkter
3. OIDC-start bevist som toppnivå-navigasjon
4. Ingen bearer-token/sensitiv verdi i browserlager, URL eller DOM
5. CSP-header verifisert; inline script og handlers blokkert
6. Axe-core på alle fire flater × alle fem tilstander
7. Tastaturtest: åpne/lukke detaljpanel, fokusfelle, fokusretur
8. Kryss-tenant: ingen data eller eksistenslekkasje
9. Ukjent `resultat.art` → `Feiltilstand`, ikke krasj eller gjetting
10. Manglende `security:read` → fraværende sikkerhetsseksjon
11. `TILLAT` fremstilles aldri som «utført»
12. E2E på staging: logg inn → policy → beslutning → åpne unntak →
    begrunnelse + historikk
13. Mobilbredde og 200 % zoom uten tap av innhold eller handling
14. Ingen `HandlingDialog` eller unntaksbehandling bygget

## Omfang
`design/tokens.css` (manglende UI-tokens; `sr-only` til base-CSS, ikke
token) · komponentbibliotek: elleve fra v1 + åtte fra v2 §7
(`HandlingDialog` IKKE bygget) · fire flater med fem tilstander via
`SideStatus` · `locales/` UI-nøkler · CSP-header i UI-serveringen ·
axe-core i CI fra første komponent.

## Etter merge
E2E på staging, deretter **innloggingstesten på disponit.com**. Da har
hele kjeden vært gjennom: policy håndhevet av M-1, beslutning logget av
M-2, unntak behandlet av M-37, servert gjennom lese-API-et, bak OIDC og
TLS — sett fra en flate Eier klikker i.
