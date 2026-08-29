# PR-010 SPESIFIKASJON v3 — DELTA (den faktiske OIDC-transaksjonen)

**Draft: Claude.ai · OIDC-first bekreftet. Passordtabell og
passordendepunkt FJERNET fullstendig fra scope. v1/v2 står der de ikke
motsies. Syv P1.**

## 1. OIDC state-maskin: fire ruter + login-transaksjon

v2 forkastet passord men lot passordets endepunktsform stå. Rettet:

| Rute | Rolle |
|---|---|
| `GET /v1/oidc/start` | Oppretter login-transaksjon, redirect til IdP |
| `GET /v1/oidc/callback` | Konsumerer transaksjonen, veksler kode, oppretter sesjon |
| `GET /v1/sesjon` | Hvem er jeg (roterer IKKE CSRF — se §4) |
| `DELETE /v1/sesjon` | Lokal logout (§7) |

**Login-transaksjon (migrasjon 009), kortlivet og engangs:**
```sql
CREATE TABLE oidc_logintransaksjon (
  state_hash    TEXT PRIMARY KEY,       -- SHA-256 av state
  nonce         TEXT NOT NULL,
  pkce_verifier TEXT NOT NULL,          -- kryptert i ro
  provider_id   TEXT NOT NULL,
  tenant_kandidat TEXT NOT NULL,        -- fra workspace-slug, server-side
  retursti      TEXT NOT NULL,          -- RELATIV, allowlistet
  opprettet     TIMESTAMPTZ NOT NULL DEFAULT now(),
  utloper       TIMESTAMPTZ NOT NULL,   -- 10 min
  brukt         BOOLEAN NOT NULL DEFAULT false
);
```
**Callback konsumerer `state` ATOMISK** (`UPDATE ... SET brukt=true WHERE
state_hash=$1 AND NOT brukt AND utloper > now() RETURNING ...` — null rader
= avvis). Replay feiler selv om authorization code fortsatt ser gyldig ut.

## 2. Identitet er `(issuer, sub)` — aldri e-post

- Brukeridentitet: `(issuer, sub)`. E-post og domene er PROFILINFORMASJON,
  aldri medlemskapsautoritet.
- Providerkonfigurasjon er eksplisitt tenant-/workspacebundet (lukket
  tabell: hvilke providere som gjelder for hvilken tenant).
- Valider LUKKET: discovery/JWKS, tillatte signaturalgoritmer (allowlist,
  ingen `none`), `iss`, `aud`, `azp` der relevant, `exp`, `nbf`, `iat`,
  `nonce`, og authorization code mot token-endepunktet.
- JWKS-rotasjon støttes FAIL-CLOSED (ukjent `kid` → hent JWKS på nytt med
  rate-grense; fortsatt ukjent → avvis).
- **JIT-medlemskap FORBUDT i v1.** Medlemskap må finnes på forhånd; ukjent
  identitet → samme generiske avvisning som manglende medlemskap.

## 3. Callback krever IKKE app-Origin

OIDC-callbacken er navigasjon fra IdP og har ikke Disponits Origin — v2s
Origin-krav ville brutt flyten. Riktig grense:
- `/oidc/start`: same-origin/host-kontroll (den kommer fra vår egen app).
- `/oidc/callback`: **`state` + `nonce` + PKCE + issuer + redirect_uri ER
  CSRF-/bindingsbeskyttelsen** — ingen Origin-krav.
- Vanlige unsafe sesjonskall: Origin + CSRF (uendret).
- Retursti er RELATIV og allowlistet — ingen åpen redirect.

## 4. CSRF: double-submit cookie med serverbinding (racefri)

v2 roterte CSRF på hver `GET /v1/sesjon` → to samtidige kall kunne
ugyldiggjøre hverandres token. Rettet til reviewens modell:
- Egen cookie **`__Host-disponit_csrf`**: `Secure`, `SameSite=Lax`,
  **ikke** HttpOnly (UI må lese den).
- Serveren lagrer hash i sesjonsraden.
- Unsafe request: cookieverdien sendes i `X-Disponit-CSRF`; serveren
  sammenligner **cookie, header OG lagret hash** konstant-tid.
- **Rotasjon KUN ved login og sesjonsrotasjon** — aldri ved GET. Reload og
  parallell lasting tåles.
- Dokumentert: CSRF-token beskytter mot cross-site, IKKE mot XSS. CSP og
  escaping (UI-spec §4) er fortsatt nødvendige.

## 5. Rate-grenser per fase (Disponit kjenner ikke `sub` før IdP har svart)

- `/oidc/start`: per IP/prefiks, workspace og provider.
- `/oidc/callback`: per IP/prefiks, provider, ugyldig state, og
  tokenvekslingsfeil.
- Etter gyldig ID-token: per `(issuer, sub)` for mislykket
  medlemskapsbinding.
- **IdP håndterer passord-/MFA-bruteforce** — ikke vårt ansvar.
- Global nødbrems har SEPARATE terskler per tenant, så én tenant ikke kan
  stenge alle andre.
- All state i PostgreSQL (delt, overlever restart — v2 §1 uendret).

## 6. Sesjonsgrensen serialiseres

To samtidige callbacker kunne begge se fire sesjoner og lage fem og seks.
Rettet:
- Advisory lock på `(tenant, bruker_id)` ELLER `SELECT ... FOR UPDATE` på
  medlemskapsraden.
- Tell aktive → tilbakekall eldste → opprett ny, ALT i én transaksjon.
- Entydig sortering `opprettet, id` (ts alene er ikke entydig).
- **Grensen gjelder per `(tenant, bruker_id)`**, ikke globalt på tvers av
  brukerens medlemskap i flere tenanter.

## 7. Privilegieendring: ingen transparent rotasjon (motsigelse rettet)

v2 sa både «authz_version ugyldiggjør alle sesjoner» og «sesjons-ID roteres
ved privilegieendring» — umulig samtidig. Riktig kontrakt:
- Privilegieendring ØKER `authz_version`.
- Eksisterende sesjoner AVVISES ved neste kall (401).
- Brukeren autentiserer på nytt gjennom OIDC.
- **Den nye innloggingen får ny sesjons-ID** (fixation-vern der det hører
  hjemme).
- Ingen transparent rotasjon av en allerede ugyldig sesjon.

## 8. Logout er LOKAL i v1
`DELETE /v1/sesjon`: tilbakekaller Disponit-sesjonen, sletter begge cookies
(`__Host-disponit_sesjon`, `__Host-disponit_csrf`). **IdP-sesjonen kan
fortsatt eksistere** og gi rask ny innlogging. RP-initiated logout hos IdP
er egen, provideravhengig kontrakt (deklarert, ikke i v1). **UI må ALDRI
love at brukeren er logget ut av Microsoft/Google** — teksten sier «logget
ut av Disponit».

## Akseptansekriterier (revidert)
OIDC-flyt m/ PKCE mot test-IdP gir sesjon · state-replay avvist (atomisk
konsum) · utløpt transaksjon avvist · manipulert nonce/redirect_uri/iss/aud
avvist · `alg: none` avvist · ukjent `kid` → JWKS refetch, så fail-closed ·
ukjent identitet uten forhåndsmedlemskap → generisk avvisning, ingen JIT ·
callback fungerer UTEN app-Origin · to samtidige `GET /v1/sesjon` bryter
ikke CSRF · unsafe uten header/cookie-match avvist · to samtidige callbacks
→ aldri over sesjonsgrensen · `authz_version`++ → alle sesjoner 401, ny
login gir ny sesjons-ID · logout sletter begge cookies, lokal kun ·
cookie + Bearer samtidig → 400 · ingen sesjons-/CSRF-verdi i DB (kun hash),
DOM, URL eller logg.
