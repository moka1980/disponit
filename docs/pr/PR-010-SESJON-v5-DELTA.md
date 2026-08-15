# PR-010 SPESIFIKASJON v5 — DELTA (SSRF, credential, autoritet, redaksjon)

**Draft: Claude.ai · OIDC-first står. v1–v4 gjelder der de ikke motsies.
Syv P1 — hvorav SSRF og callback-lekkasje er sikkerhetshull v4 ikke dekket.**

## 1. `POST /v1/oidc/start` (Origin holder ikke på GET-navigasjon)

Browsere sender ikke alltid `Origin` på vanlig GET-navigasjon — v4s krav
ville brutt legitim innlogging. Rettet:
```
POST /v1/oidc/start
Content-Type: application/json
```
- Workspace utledes fra HOST (server-side), aldri fra body.
- `provider_id` valideres server-side mot `tenant_oidc_provider`.
- Origin/Host + **Fetch Metadata** (`Sec-Fetch-Site`, `Sec-Fetch-Mode`)
  kontrolleres — tilgjengelig på POST.
- Respons: **`303`** til IdP.
- Krever verken eksisterende sesjon eller CSRF-token.
- Ukjent workspace ELLER provider → samme generiske feil.
Callback forblir `GET` og beskyttes av state + bindingcookie + nonce + PKCE.

## 2. SSRF-herding av alle provider-kall

`discovery_url`, `token_endpoint`, `jwks_uri` gir server-side nettverkskall
— kompromittert providerkonfig kunne nådd localhost eller
metadata-endepunkter. Krav:
- **Kun HTTPS** i produksjon.
- Ingen userinfo-del, ingen fragment, ingen vilkårlig redirect.
- **DNS/IP-validering FØR og ETTER redirect:** blokker loopback,
  link-local (169.254/fe80), metadata-endepunkter, private nett — med
  mindre eksplisitt staging-allowlist.
- Redirect-mål REVALIDERES mot samme policy (ingen TOCTOU).
- Maks responsstørrelse (f.eks. 256 KiB) + korte connect/read-timeouts (5 s).
- **Discoveryens `issuer` må være EKSAKT forventet issuer** fra
  `oidc_provider.issuer`.
- Staging test-IdP får eksplisitt LOKAL allowlist (én vert), aldri et
  generelt «tillat private IP-er».

## 3. Lukket credential-referanse

`client_secret_ref` valideres mot lukket format `^[a-z0-9_-]{1,64}$` —
ingen `/`, `..`, kontrolltegn eller sti. Referansen slås opp i en
**oppstartsvalidert allowlist** (PR-009 `LoadCredential`-navn). Manglende
credential → provideren er utilgjengelig FAIL-CLOSED (ikke stille feil).
Ny providercredential krever kontrollert deploy/restart (systemd
credentials leses ved oppstart) — dokumentert som driftskonsekvens.

## 4. Roller er autoritet, scopes er avledet

`roller[]` og `scopes[]` som to lagrede felt ville drevet fra hverandre.
Rettet — reviewens anbefaling:
- **`brukermedlemskap.roller[]` er ENESTE autoritet.**
- Scopes UTLEDES server-side fra et lukket rollemønster
  (`ROLLE_TIL_SCOPES`, konstant i kode, CI-validert mot scope-listen).
- `scopes`-kolonnen fjernes.
- **DB-trigger øker `authz_version` ATOMISK** ved endring av `aktiv`,
  `roller` eller andre sikkerhetsrelevante medlemskapsfelt. Runtime kan
  ikke endre fullmakter uten versjonsøkning (negativ test).

## 5. Lukket profil-DTO

`profil JSONB` var en åpen IdP-dump. Rettet:
```
profil { visningsnavn: str≤128,
         epost: str≤254 | null,
         epost_verifisert: bool | null }   additionalProperties: false
```
- **Hele ID-token/UserInfo-responsen lagres ALDRI.**
- Claims er ubetrodd profilinformasjon — aldri autoritet (§ v3 §2).
- Ukjent felt fra IdP forkastes ved mapping, ikke lagret.

## 6. Callback-redaksjon (code/state ut av logger og historikk)

Callback-URL-en bærer `code` og `state`:
- **nginx og app logger ALDRI querystring for callbackruten** (egen
  log_format for `/v1/oidc/callback`).
- Ingen `code`, `state`, `nonce` eller PKCE-verifier i feilmeldinger,
  logger eller telemetry.
- Etter terminal callback: **umiddelbar redirect til ren, relativ URL**
  (fjerner parametrene fra browserhistorikken).
- `Referrer-Policy: no-referrer` på callbackresponsen.
- Callbackfeil → generisk side som ALDRI gjengir URL-parametere.
- Authorization code behandles som hemmelighet selv om den er engangs.

## 7. Global nødbrems: konkrete tall (ellers ut)

Beholdes med målbare terskler (ikke en port vi ikke kan teste):
| Nøkkel | Vindu | Maks | Cooldown | Reset |
|---|---|---|---|---|
| Per tenant, alle loginfaser | 15 min | 200 | 30 min | Admin-kommando |
Overskredet → `429 rate_grense_login` + `Retry-After`. Tenantbundet, så én
tenant kan ikke stenge andre (v3 §5). Admin-reset er en eksplisitt CLI-
kommando, logget i revisjonsloggen.

## 8. Bindende OIDC-presiseringer (vedtatt)
- Login-transaksjonstabellen nås KUN via herdede funksjoner; **callbacken
  kjenner tenant fra TRANSAKSJONEN**, aldri fra query.
- `NY → KONSUMERT` committes FØR nettverkskall (v4 §3).
- `FULLFØRT` + sesjonsopprettelse committes SAMMEN.
- Feiler sesjonsopprettelsen → transaksjonen → `FEILET`, kan ikke gjenbrukes.
- Maks fem sesjoner serialisert med lås på `(tenant, bruker_id)`.
- **Samtidige `/oidc/start` i samme browser:** ny start UGYLDIGGJØR
  tidligere ufullført browserbinding (én binding om gangen — enklest og
  tettest; en forlatt fane kan ikke fullføre senere).

## Akseptansekriterier (tillegg)
`POST /start` med feil Fetch Metadata avvist · discovery mot loopback/
metadata-IP avvist (og etter redirect) · issuer-mismatch avvist ·
credential-ref med `..` avvist · manglende credential → provider
utilgjengelig, ikke stille feil · rolleendring → `authz_version`++ via
trigger · scopes finnes ikke som kolonne · profil med ukjent claim →
forkastet · callback-querystring ikke i noen logg · callbackrespons har
`no-referrer` og redirect til ren URL · nødbrems utløser 429 med
Retry-After, admin-reset logget.
