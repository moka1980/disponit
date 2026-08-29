# PR-009b SPESIFIKASJON — Transport: nginx og TLS (til ChatGPT-porten)

**Draft: Claude.ai · Det manglende leddet: PR-009 gir kjørende tjenester
på loopback, PR-010 forutsetter HTTPS og `Secure`-cookies. Denne leverer
transporten mellom dem. Hard avhengighet begge veier — merges etter
PR-009, før PR-010-e2e.**

## 0. Avgrensning
Leverer: reverse proxy, TLS, hostnavn-allowlist, header-hygiene,
requestgrenser, logg-redaksjon. Leverer IKKE: autentisering (PR-010),
nye endepunkter, WAF, CDN.

## 1. Topologi
```
Internett → nginx (443, TLS)  →  127.0.0.1:8099 (disponit-api)
                                  M-37 har ingen inngående port
```
- **API-et forblir loopback-bundet.** nginx er eneste vei inn.
- `DISPONIT_TLS_AKTIV=1` settes av DENNE leveransen — den som faktisk
  leverer TLS eier flagget (PR-005b boot-porten).
- M-37 eksponeres aldri; heartbeat er en fil, ikke et endepunkt.

## 2. TLS
- Sertifikat via ACME (Let's Encrypt) med automatisk fornyelse; fornyelse
  testes med `--dry-run` i CI-/timer-kontekst.
- **Kun TLS 1.2+ (helst 1.3)**, moderne ciphersuiter, ingen komprimering.
- **HSTS settes IKKE i første deploy** — først etter verifisert HTTPS i
  minst én uke (max-age 31536000, includeSubDomains først når alle
  subdomener er HTTPS). Dokumentert som eget aktiveringssteg.
- OCSP stapling på. Privat nøkkel `0600`, eid av root, aldri i repo eller
  backup som ikke er kryptert.

## 3. Hostnavn-allowlist (lukket)
- `server_name` matcher EKSAKT de godkjente vertsnavnene (staging:
  ett navn). **`default_server` returnerer 444/close** for alt annet —
  ingen wildcard, ingen catch-all som proxyer videre.
- Host-header videresendes urørt (PR-010 utleder workspace fra host, så
  en forfalsket Host ville vært en tenant-forveksling).
- HTTP (80) → 301 til HTTPS, unntatt ACME-utfordringssti.

## 4. Header-hygiene (allowlist, ikke blocklist)
- **Klientsendte `X-Forwarded-*` STRIPPES** og settes på nytt av nginx:
  `X-Forwarded-For` (kun klientens IP), `X-Forwarded-Proto`,
  `X-Forwarded-Host`. Appen stoler KUN på disse fra egen proxy.
- Alt annet klient-sendt som ligner infrastrukturheadere fjernes.
- Responsheadere satt av nginx: `Referrer-Policy: no-referrer`,
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  CSP (fra UI-spec §4, uten inline-script).

## 5. Requestgrenser og timeouts
| Parameter | Verdi |
|---|---|
| `client_max_body_size` | 256 KiB (samme som app-lagets grense) |
| `client_body_timeout` / `client_header_timeout` | 10 s |
| `proxy_read_timeout` | 30 s |
| `proxy_connect_timeout` | 5 s |
| `keepalive_timeout` | 65 s |
| `limit_req` (grov flood-brems) | 60 r/m per IP, burst 20 |
nginx' grense er en FØRSTE forsvarslinje; app-lagets 256 KiB-teller
(PR-005b) består uendret som andre linje.

## 6. Logg-redaksjon (kritisk for PR-010)
- **Egen `location = /v1/oidc/callback`** med `access_log` som logger
  `$uri` UTEN `$query_string` (egen `log_format`).
- Ingen `Authorization`-header, cookie-verdier eller
  `X-Disponit-CSRF` i noen loggformat.
- Feilsider er generiske; nginx gjengir aldri URL-parametere.
- Logg-rotasjon og retention (30 d) definert.

## 7. Konfigurasjonsforvaltning
- nginx-konfig ligger i repoet (`deploy/staging/nginx/disponit.conf`),
  installeres av `opp.sh`-familien, **valideres med `nginx -t` FØR reload**
  — feil konfig gir aldri nedetid.
- Reload (ikke restart) ved endring.
- Endringer er versjonert med releasen (PR-009 §3).

## 8. Akseptansekriterier (målbart)
HTTPS svarer på godkjent hostnavn · ukjent Host → 444, ingen proxying ·
HTTP → 301 · TLS 1.2+ og moderne ciphers (testverktøy-rapport i artefakt) ·
klientsendt `X-Forwarded-For` overskrives (negativ test) · body > 256 KiB
avvist i nginx · callback-querystring finnes IKKE i access-logg
(grep-test med ekte flyt) · `nginx -t` feiler → ingen reload, tjenesten
uberørt · sertifikatfornyelse `--dry-run` grønn · API-et er IKKE nåbart
direkte utenfra (portskann fra utsiden viser kun 80/443).

## Spørsmål til ChatGPT
1. Er `444` riktig for ukjent Host, eller foretrekkes 421 Misdirected
   Request av hensyn til feilsøkbarhet?
2. HSTS utsatt til verifisert drift — riktig avveining, eller bør det på
   fra dag én siden staging uansett er internt?
3. Bør nginx' `limit_req` være per IP alene, eller trengs en egen,
   strammere grense på `/v1/oidc/*` allerede her (i tillegg til PR-010s
   applikasjonsgrenser)?
