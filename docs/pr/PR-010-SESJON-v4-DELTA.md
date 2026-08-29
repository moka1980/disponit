# PR-010 SPESIFIKASJON v4 — DELTA (browserbinding + datamodell + flyt)

**Draft: Claude.ai · OIDC-first står. v1–v3 gjelder der de ikke motsies.
Fem P1, hvorav én er et reelt angrepsscenario.**

## 1. Browserbinding — `state` beviser flyten, ikke browseren

Angrepet reviewen beskrev er reelt: angriper starter flyten, autentiserer
seg, og sender callback-URL med gyldig `code`+`state` til offeret, som da
får angriperens sesjon i sin browser. Rettet:
- `/oidc/start` setter **`__Host-disponit_oidc`**: tilfeldig 256-bit,
  `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`.
- KUN hash lagres på login-transaksjonen.
- **Callback krever at cookiehashen matcher transaksjonen** (konstant-tid)
  — sjekkes FØR authorization code veksles.
- Cookie slettes ved suksess OG ved alle terminale feil.
- Bindingen er SEPARAT fra den endelige sesjonscookien (§ v2/v3).

## 2. Lukkede provider-, identitets- og medlemskapstabeller (migrasjon 009)

```
oidc_provider(provider_id PK, issuer UNIQUE, discovery_url,
  authorization_endpoint, token_endpoint, jwks_uri, client_id,
  client_secret_ref,            -- referanse til systemd credential, ALDRI verdien
  tillatte_algoritmer TEXT[],   -- allowlist, aldri 'none'
  aktiv BOOLEAN NOT NULL DEFAULT false)

tenant_oidc_provider(tenant, provider_id, redirect_uris TEXT[] NOT NULL,
  PRIMARY KEY (tenant, provider_id))     -- eksakte URI-er, ingen mønster

brukeridentitet(bruker_id PK, issuer, sub, profil JSONB,
  UNIQUE (issuer, sub))                  -- (issuer,sub) er identiteten
                                         -- profil = uten autoritetsverdi

brukermedlemskap(tenant, bruker_id, aktiv BOOLEAN NOT NULL,
  roller TEXT[], scopes TEXT[], authz_version INT NOT NULL DEFAULT 1,
  PRIMARY KEY (tenant, bruker_id))
```
- RLS+FORCE der tenant finnes; default-deny (ingen medlemskap = ingen
  tilgang; ingen `aktiv` provider = ingen flyt).
- **`client_secret_ref` peker på systemd credential** (PR-009 §5) — selve
  hemmeligheten finnes ALDRI i DB, API-respons eller logg.
- Providerhemmelighet kan ikke leses via noe endepunkt (negativ test +
  miljødump-test).

## 3. Callback-statusmaskin (ingen DB-lås under nettverkskall)

`NY → KONSUMERT → FULLFØRT | FEILET`:
1. **Atomisk `NY → KONSUMERT`**: validerer `state`, utløp OG browserbinding
   (§1). Commit UMIDDELBART — ingen lås holdes videre.
2. Tokenveksling + ID-tokenvalidering skjer UTEN DB-lås (nettverkskall).
3. Sesjonsopprettelse krever fortsatt status `KONSUMERT` og samme login-ID;
   **`FULLFØRT` settes i SAMME transaksjon som sesjonsopprettelsen**.
4. Callback-replay på `KONSUMERT`/`FULLFØRT`/`FEILET` → avvist.
5. Transient IdP-feil → `FEILET`, krever NY login. Ingen usikker replay.
6. Opprydding: transaksjoner slettes 24 t etter terminal status; retention
   for login-evidens i revisjonsloggen (egen, lengre).
7. Samtidige callbacks for samme login-ID → maks én sesjon (steg 1 er
   atomisk, taperen får `KONSUMERT` og avvises).

## 4. Konkrete, atomiske rategrenser

| Fase | Nøkkel | Vindu | Maks | Backoff-tak | TTL |
|---|---|---|---|---|---|
| `/oidc/start` | IP-prefiks + workspace + provider | 5 min | 20 | — | 1 t |
| `/oidc/callback` ugyldig state/binding | IP-prefiks + provider | 5 min | 10 | 15 min | 1 t |
| `/oidc/callback` tokenvekslingsfeil | IP-prefiks + provider | 5 min | 10 | 15 min | 1 t |
| Mislykket medlemskapsbinding | `(issuer, sub)` | 15 min | 5 | 1 t | 24 t |

- **IP-prefiks: /32 for IPv4, /64 for IPv6** (ikke enkeltadresse på IPv6).
- Alle increments og grensekontroller ATOMISKE (én `INSERT ... ON CONFLICT
  DO UPDATE ... RETURNING` under samme rad-lås).
- **Vellykket callback nullstiller kun tellerne for den nøkkelen**.
- Overskredet → `429` med lukket kode `rate_grense_login` og `Retry-After`
  (sekunder til vinduet åpner).
- Global nødbrems: separate terskler per tenant (v3 §5 uendret).

## 5. CSRF-cookien er TILSIKTET browserlagring (akseptansekriteriet rettet)

v3s kriterium «ingen CSRF-verdi i browserlager» motsa modellen som krever
en JS-lesbar cookie. Rettet tekst:
- CSRF-token finnes KUN i `__Host-disponit_csrf` og requestheaderen.
- IKKE i local/sessionStorage, DOM, URL, analytics eller logger.
- Sesjonscookien forblir `HttpOnly` (ikke JS-lesbar).
- **Begge cookies + bindingcookien slettes ved logout og ved ugyldig
  sesjon.**

## Bindende tester (reviewens, vedtatt)
Gyldig callback åpnet i annen browser uten bindingcookie → avvist FØR
tokenveksling · samme callback kan ikke konsumeres to ganger · DB-lås
holdes ikke under IdP-tokenveksling (målt) · samtidige callbacks samme
login-ID → maks én sesjon · samme `sub` fra annen issuer = annen identitet ·
ukjent/deaktivert provider avvist FØR redirect og før tokenveksling ·
providerhemmelighet ikke i respons, logg eller miljødump · utgåtte
login-transaksjoner og bindingcookies ryddes · rate-grense returnerer
`429` + `Retry-After`, nullstilles kun for riktig nøkkel.
