# PR-009b SPESIFIKASJON v2 — DELTA (seks transportkontrakter → GO)

**Draft: Claude.ai · v1s arkitektur står (nginx terminerer, API på
loopback, ukjent host når aldri appen). Seks kontrakter strammet.**

## 1. Host kanoniseres server-side — aldri `$http_host` videre

- Tillatt hostname velges FRA NGINX-KONFIGURASJONEN (den matchede
  `server_name`-verdien), ikke fra klientens header.
- API-et mottar `X-Disponit-Host: <kanonisk navn>` — lowercase, uten port,
  uten trailing dot. **PR-010 utleder workspace fra DENNE**, aldri fra
  `Host`.
- SNI og Host må SAMSVARE; avvik → 421, ingen proxying.
- Ukjent SNI, ukjent Host eller mismatch → 421 (se §6), aldri upstream.
- **Port 80-defaulten avviser ukjent Host** — redirect bygges KUN fra den
  kanoniske verdien, aldri fra angriperens header (ingen redirect-injeksjon).

## 2. TLS-bevis kommer fra proxy-peer, ikke fra et flagg

`DISPONIT_TLS_AKTIV` som sikkerhetsbevis var sirkulært — flagget sier
ingenting om den enkelte requesten. Rettet:
- nginx setter `X-Forwarded-Proto: https` HARDKODET (ikke `$scheme`).
- ALLE klientsendte `Forwarded`, `X-Forwarded-*`, `X-Real-IP` fjernes før
  proxying.
- **API-et stoler på proxyinformasjon KUN når peer er loopback**
  (sjekker faktisk klientadresse på socketen); ellers ignoreres headerne
  og requesten behandles som ikke-TLS.
- `Secure`-cookie settes basert på den verifiserte proxy-informasjonen,
  ALDRI på miljøflagget. `DISPONIT_TLS_AKTIV` beholdes kun som
  BOOT-sjekk (får prosessen binde ikke-loopback?) — ikke som per-request-
  autoritet. Rollen presiseres i PR-005b-porten.
- Ekstern TLS verifiseres med en faktisk HTTPS-probe etter deploy (§3.6).

## 3. ACME-tilstandsmaskin (første konfig kan ikke peke på et sertifikat som ikke finnes)

Bindende rekkefølge i `opp-transport.sh`:
1. Installer HTTP-konfig med KUN ACME-path + default-avvisning (421).
2. Hent første sertifikat.
3. Installer HTTPS-konfig.
4. `nginx -t`.
5. Reload.
6. **Ekstern HTTPS-probe** (fra utsiden, ikke localhost) — deploy er ikke
   grønn før den er det.
7. Fornyelse: deploy-hook kjører `nginx -t` FØR reload; varsling ved feil.

ACME `--dry-run` hører hjemme i staging-/timer-verifikasjon, **ikke som
nettverksavhengig CI-port** (v1 hadde den feil plassert).

## 4. HTTP-normalisering og request-smuggling som eksplisitt port

- `proxy_http_version 1.1` (eksplisitt).
- Fjern hop-by-hop-headere som ikke brukes: `Connection`, `Upgrade`,
  `TE`, `Trailer`, `Transfer-Encoding` mot upstream.
- **Avvis tvetydig `Content-Length`/`Transfer-Encoding`** (både til stede,
  eller dobbel `Content-Length`).
- **Ingen websocket-oppgradering** i denne leveransen.
- Negative tester: CL.TE, TE.CL, dobbel Content-Length, ugyldige
  headernavn, obs-fold.

## 5. Callback-redaksjon i ALLE logglag (ikke bare access-logg)

Porten må bevise at callbackens query IKKE finnes i:
nginx access-logg · **nginx error-logg** · API/ASGI-access-logg ·
applikasjonslogg · feilrespons eller redirectmål.
- Egen `log_format` for `/v1/oidc/callback` som **forbyr** `$request`,
  `$request_uri`, `$args`, cookies, `Authorization` og `Referer`.
- nginx error-logg for den ruten settes til nivå som ikke gjengir URI
  (eller ruten logges til egen, redigert fil).
- ASGI-access-logg konfigureres tilsvarende i appen (PR-010 §6 utvides
  til å dekke ASGI-laget eksplisitt).

## 6. Målbare sikkerhetsinnstillinger (ikke «moderne»/«helst»)

| Innstilling | Verdi |
|---|---|
| Protokoller | TLS 1.2 og 1.3 KUN (`ssl_protocols TLSv1.2 TLSv1.3`) |
| TLS 1.2-ciphers | Eksplisitt liste: ECDHE-ECDSA/RSA-AES128/256-GCM-SHA256/384, CHACHA20-POLY1305. Ingen CBC, ingen RSA-key-exchange |
| TLS 1.3 | Standard suiter, dokumentert som bevisst valg |
| `server_tokens` | `off` |
| Ukjent Host/SNI | **421** (standardisert, observerbar — valgt over 444) |
| `limit_req_status` | `429`, generisk respons |
| Eksponerte porter | KUN 80/443; **8099 verifiseres utilgjengelig eksternt** (portskann i akseptansetest) |

**CSP splittes:** API-responser får en streng, egen CSP nå. **UI-ets CSP
defineres i UI-leveransen** — en global CSP skrevet før UI-ressursene er
kjent ville blokkert den godkjente UI-leveransen.

**HSTS:** separat, auditert aktiveringsport etter stabil TLS og bestått
fornyelsestest. Start `max-age=300`, økes gradvis til 31536000.
`includeSubDomains` først når alle underdomener er kartlagt.

## 7. Rate-grenser: separate soner, NAT-vennlig

- Generell sone: 60 r/m per IP, burst 20 — men **ikke som eneste vern for
  et helt bedriftsnett bak NAT**; den er grov flood-brems.
- **Egen sone `/v1/oidc/start`** og **egen `/v1/oidc/callback`**
  (strammere, f.eks. 30 r/m burst 10), som TRANSPORTVERN.
- PR-010 eier fortsatt den autoritative per-transaksjon/per-identitet-
  begrensningen (v5 §4) — nginx-grensen erstatter den ikke.

## Akseptansekriterier (revidert)
Ukjent Host/SNI → 421, ingen upstream-treff (logg-bevist) · API mottar
kanonisk host, ikke rå Host · klientsendt `X-Forwarded-Proto: https` mot
et HTTP-kall gir IKKE `Secure`-cookie (peer-sjekk) · ACME-sekvens fra
tom vert til grønn ekstern HTTPS-probe · smuggling-vektorene avvist ·
callback-query fraværende i alle fem logglag (ekte flyt + grep) ·
`ssl_protocols` nøyaktig TLSv1.2+1.3 · 8099 ikke nåbar utenfra ·
`nginx -t`-feil → ingen reload · fornyelses-hook validerer før reload.
