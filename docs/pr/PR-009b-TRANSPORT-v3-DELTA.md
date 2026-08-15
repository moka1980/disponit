# PR-009b SPESIFIKASJON v3 — DELTA (fire kontrakter → GO)

**Draft: Claude.ai · v1+v2 står der de ikke motsies. ACME-sekvens,
logg-redaksjon, HTTP-normalisering, CSP-splitt, HSTS-plan og ekstern
verifikasjon er godkjent og uendret.**

## 1. Unix-socket er tillitsgrensen — ikke loopback

Loopback beviser hvor forbindelsen kom fra, ikke hvem som åpnet den:
enhver lokal prosess — inkludert en kompromittert M-37-arbeider — kan
koble til `127.0.0.1:8099` og sende falske proxy-headere. Rettet:

- **API-et binder en Unix-socket**, ikke en TCP-port:
  `/run/disponit/api.sock`, eier `disponit-api`, gruppe `disponit-proxy`,
  modus `0660`.
- **Kun nginx-brukeren er i `disponit-proxy`.** M-37-brukeren og alle
  andre lokale prosesser mangler filsystemrettighet til å koble til.
- nginx: `proxy_pass http://unix:/run/disponit/api.sock;`
- TCP-porten 8099 fjernes helt (også fra boot-sjekken; loopback-TCP var
  et midlertidig oppsett, ikke en kontrakt).
- Systemd: `RuntimeDirectory=disponit`, `RuntimeDirectoryMode=0750`, og
  socketen opprettes av API-prosessen med riktig gruppe (eller via
  `.socket`-unit med `SocketGroup=disponit-proxy`).
- **Klientsendt `X-Disponit-Host` fjernes eksplisitt** før nginx setter
  sin egen verdi — samme behandling som `X-Forwarded-*`.

Tillitsgrensen er dermed filsystemrettigheter, ikke en headerpåstand.

## 2. Sesjons- og OIDC-ruter avviser uverifisert transport

v2 lot et uverifisert kall potensielt få en cookie uten `Secure`. Skal
aldri skje:
- `/v1/sesjon`, `/v1/oidc/*` **AVVISER** enhver request som ikke kom
  gjennom den betrodde transportkanalen (Unix-socket + hardkodet
  `X-Forwarded-Proto: https` fra nginx). Svar: `421`/`400`, aldri
  behandling.
- **Sesjonscookien har ALLTID `Secure`** — det er en konstant, ikke en
  betinget verdi. `__Host-`-prefikset krever det uansett maskinelt.
- **Ingen fallback** til mindre sikker cookie under noen omstendighet.
- Direkte utviklingskjøring uten proxy: egen eksplisitt utviklingsmodus
  (aldri på staging/produksjon), som nekter å starte hvis
  `DISPONIT_MILJO != utvikling`.

## 3. SNI og Host er to lag med hver sin respons

Ukjent SNI kan ikke gi pålitelig 421 — klienten stopper på sertifikatfeil
før den ser HTTP. Delt kontrakt:

| Tilfelle | Respons |
|---|---|
| Ukjent SNI | **TLS-handshake avvises** (`ssl_reject_handshake on` i default-server). Ingen HTTP, ingen upstream |
| Kjent SNI, ukjent/avvikende Host | **HTTP 421**, ingen upstream |
| SNI = Host = godkjent | Proxy med kanonisk host (v2 §1) |

## 4. Eksakte tall og strenger

**Rate-grenser (transportvern; PR-010 eier misbruksgrensene):**
| Sone | Grense | Burst | Merknad |
|---|---|---|---|
| Generell | **600 r/m per IP** | 100 | Høy nok til et bedriftsnett bak NAT; ren flood-brems |
| `/v1/oidc/start` | **120 r/m per IP** | 30 | |
| `/v1/oidc/callback` | **120 r/m per IP** | 30 | |
`limit_req_status 429`, generisk respons. PR-010s per-identitet/per-
transaksjon-grenser (v5 §4) er den autoritative misbruksbeskyttelsen.

**TLS 1.2 — faktisk OpenSSL-streng:**
```
ssl_protocols TLSv1.2 TLSv1.3;
ssl_prefer_server_ciphers off;
ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305;
```
**TLS 1.3 — eksplisitt policy:**
```
ssl_conf_command Ciphersuites TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256;
```
Krever OpenSSL ≥ 1.1.1 (verifiseres i `opp-transport.sh`; eldre versjon →
deploy stopper). Kurver: `ssl_ecdh_curve X25519:prime256v1;`

## Akseptansekriterier (tillegg/revidert)
M-37-brukeren kan IKKE koble til API-socketen (negativ test som annen
lokal bruker) · 8099 finnes ikke · klientsendt `X-Disponit-Host` når
aldri appen · `/v1/oidc/*` uten betrodd transport → avvist, ingen cookie ·
ukjent SNI → handshake avvist (ingen HTTP-respons observert) · kjent SNI +
feil Host → 421 · `ssl_protocols` og cipher-strenger nøyaktig som over
(konfig-diff-test) · OpenSSL-versjon verifisert ved deploy · generell
rate-grense slipper gjennom et realistisk NAT-nett (simulert 200 klienter).
