# PR-010 SPESIFIKASJON v2 — DELTA (OIDC-first browseridentitet)

**Draft: Claude.ai · Passordinnlogging FORKASTET til fordel for OIDC —
arkitektbeslutning etter reviewens anbefaling, Eier kan overprøve.
Sesjonsmodellen fra v1 består der den ikke motsies.**

## 0. Beslutning: OIDC-first, ingen lokal passordhåndtering

Passord ville dratt med seg registrering, e-postbekreftelse, glemt-passord
med reset-token, MFA, kompromittert-passord-kontroll, låsing, recovery,
Argon2-parameteroppgradering og support ved mistet tilgang — et helt
delsystem vi ville kastet ved første bedriftskunde som krever SSO.
Disponit er **relying party**, aldri passordholder.
- Staging: kontrollert lokal test-IdP (f.eks. en container-IdP), ikke
  ekte kundeidentiteter.
- Produksjon: Entra ID, Google Workspace eller kundens egen IdP.
- Validering LUKKET: `state`, `nonce`, PKCE (S256), `issuer`, `audience`,
  `redirect_uri` mot allowlist. Avvik → generisk avvisning.
- **Ingen tenant opprettes automatisk fra en e-postdomene-påstand.**
  Tenantmedlemskap er eksplisitt data (§5), aldri utledet fra IdP-claims.

## 1. Delt, atomisk login-state (ikke i minne)

Rate-/brute-force-state i PostgreSQL (delt, overlever restart og flere
workers):
- Per kontoidentitet (subject fra IdP), per kilde-IP/prefiks, og en
  global nødbrems.
- Utløp + maksimum backoff; vellykket login nullstiller KUN riktig teller.
- Generisk respons og tilnærmet lik tidsbruk uansett om identiteten er
  kjent (ingen enumerering).
Gjelder callback-endepunktet — «loopback beskytter» holder ikke når
nginx (PR-009b) videresender ekstern trafikk.

## 2. Cookie: levetid uten selvmotsigelse

v1s `Max-Age`=inaktivitetstak motsa glidende fornyelse. Rettet — variant A
(enklest):
- **`Max-Age` = absolutt tak (12 t)**; serveren håndhever
  30-minutters inaktivitet.
- Serveravvist utløp → `401` + `Set-Cookie` som SLETTER cookien.
- Navn: **`__Host-disponit_sesjon`** — prefikset krever maskinelt
  `Secure`, `Path=/`, ingen `Domain`.
- Uendret: HttpOnly, SameSite=Lax (bekreftet riktig for dyplenker,
  kombinert med Origin-kontroll), 256-bit CSPRNG, kun referanse.

## 3. Autorisasjonsversjon — scopes kan ikke være 12 t gamle

Sesjonsraden lagrer snapshot; en deaktivert bruker beholdt ellers
fullmakter. Rettet:
- `brukermedlemskap` bærer `authz_version INT`; sesjonsraden lagrer
  versjonen den ble opprettet med.
- **Hvert kall sammenligner sesjonens versjon mot aktiv versjon** —
  avvik → `401 sesjon_ugyldig`, ny innlogging.
- Rolleendring, deaktivering eller fjernet medlemskap ØKER versjonen og
  ugyldiggjør alle eksisterende sesjoner for brukeren umiddelbart.
- Scopes leses fra medlemskapet ved hvert kall (ikke fra snapshot) når
  versjonen matcher — snapshot er kun cache-nøkkel, ikke autoritet.

## 4. Logout: ærlig kontrakt

Ikke lov «umiddelbar kansellering av in-flight requests» uten mekanisme:
- Logout stopper ALLE NYE kall (tilbakekalt leses i pre-auth).
- Allerede autoriserte LESEkall kan fullføre — dokumentert, ikke skjult.
- Fremtidige sensitive mutasjoner REVALIDERER sesjon + `authz_version` i
  SAMME transaksjon som mutasjonen (låst der), så en mutasjon aldri
  committer på en tilbakekalt sesjon.

## 5. Tenantbinding ved login (aldri fra request-body)

- Workspace-slug eller host mappes SERVER-SIDE til tenant.
- Autentisert identitet må ha AKTIVT medlemskap i den tenanten.
- Sesjonen bindes til nøyaktig den tenanten.
- Ukjent workspace OG manglende medlemskap → SAMME generiske avvisning
  (ingen eksistenslekkasje).
- Tenantbytte = ny tenantbundet sesjon (aldri bytte i eksisterende).

## 6. CSRF + Origin komplettert

- Modellen er synchronizer-token (ikke double-submit) — navngitt korrekt.
- **Login/callback krever godkjent `Origin`/host** (login-CSRF-vern) og
  JSON content-type der relevant.
- Alle fremtidige unsafe-metoder krever BÅDE godkjent Origin OG
  CSRF-header.
- `GET /v1/sesjon` returnerer et ROTERT CSRF-token (så UI får nytt etter
  page reload). Rotasjon er atomisk; gammelt token avvises umiddelbart
  (ingen overgangsvindu — enklere og tettere).
- CORS lukket: ingen `*`, aldri `*` med credentials.

## 7. Sesjonslagerets livssyklus

- **Hashvalg navngitt:** SHA-256 av cookieverdien er tilstrekkelig fordi
  verdien er 256-bit CSPRNG (ingen laventropi å brute-force) — til
  forskjell fra `api_tokener.secret_mac` som bruker HMAC med pepper fordi
  den beskytter mot DB-dump-basert forfalskning av strukturerte tokens.
  Bevisst forskjell, dokumentert.
- Opprydding: timer sletter utløpte/tilbakekalte sesjoner (>30 d).
- Login/logout-evidens i revisjonsloggen med egen retention (ikke slettet
  med sesjonsraden).
- **Maks aktive sesjoner per bruker = 5**; ny sesjon over taket
  tilbakekaller den eldste ATOMISK i samme transaksjon.
- **Sesjons-ID roteres ved vellykket login og ved privilegieendring**
  (fixation-vern).

## 8. Principal-type per request (reviewens svar 3, vedtatt)

Hvert request har NØYAKTIG én principal-type:
- Bearer `bruker`-token → maskin/integrasjonstest.
- Sesjonscookie → browser.
- **Sendes BEGGE (cookie + Authorization) → requesten AVVISES** (400,
  lukket kode). Ingen automatisk fallback mellom mekanismene.
- Muterende maskinruter: eksplisitte Bearer-scopes.
- Fremtidige browsermutasjoner: sesjon + Origin + CSRF.

## 9. Akseptansekriterier (revidert)
OIDC-flyt med PKCE mot test-IdP gir sesjon · manipulert `state`/`nonce`/
`redirect_uri` avvist · ingen tenant opprettet fra e-postdomene ·
`authz_version` økes → alle sesjoner for brukeren ugyldige ved neste kall ·
cookie har `__Host-`-prefiks og korrekte flagg · inaktivitet 30 min og
absolutt 12 t begge håndhevet · logout: nye kall 401, pågående lesekall
fullfører (dokumentert) · sjette sesjon tilbakekaller eldste · cookie +
Bearer samtidig → 400 · login-rate-state overlever prosessrestart ·
kryss-tenant: sesjon A ser aldri B · ingen sesjonsverdi i DB (kun hash),
DOM, URL, browserlager eller logg.
