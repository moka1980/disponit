# PR-010 SPESIFIKASJON v6 — DELTA (pinnet transport, discovery, bibliotek)

**Draft: Claude.ai · OIDC-first står. v1–v5 gjelder der de ikke motsies.
Tre P1 + presiseringer. Dette er siste kjente åpne punkter.**

## 1. IP-pinnet transport — DNS-rebinding lukket

v5 validerte IP og lot så klienten slå opp navnet på nytt — beskytter
ingenting. For HVERT outbound provider-kall:
1. **Normaliser hostname**; avvis alternative IP-notasjoner (desimal,
   oktal, heksadesimal, IPv4-mapped IPv6, etterfølgende punktum).
2. **Resolve ALLE A/AAAA-adresser.**
3. **Avvis HELE requesten hvis ÉN kandidat er forbudt** (ikke velg en
   lovlig blant flere — en hostname som resolver til både offentlig og
   privat IP er avvist).
4. **Pin den validerte IP-en til forbindelsen** (custom connector/resolver).
5. **Behold original hostname for TLS-SNI og sertifikatvalidering** —
   sertifikatet valideres mot navnet, ikke mot IP-en.
6. HTTP-klienten gjør ALDRI en ukontrollert ny DNS-oppløsning.
7. **Revalider og repin ved HVERT redirect.**
8. Blokkerte områder (IPv4 og IPv6): loopback, private, link-local,
   multicast, unspecified, metadata (169.254.169.254, fd00:ec2::254).

**Eksakte konstanter (ikke «f.eks.»):**
| Parameter | Verdi |
|---|---|
| Maks metadata-/JWKS-/tokenrespons | 256 KiB |
| Connect timeout | 5 s |
| Read timeout | 5 s |
| Redirects | 0 som standard; maks 2 der discovery krever det |

**Staging-unntak** er en eksakt `(scheme, host, port, IP/CIDR)`-allowlist
for test-IdP-en — aldri «tillat private IP-er».

## 2. Discovery er eneste metadatakilde

v5 lagret både discovery-URL OG endepunkter — uklart hva som vinner.
Rettet:
- **Konfigurasjon lagrer KUN:** forventet `issuer`, `discovery_url`,
  `client_id`, `client_secret_ref`, tillatte algoritmer, aktiv-status.
- Authorization-, token- og JWKS-endepunkter **hentes fra discovery**.
- `issuer` i discovery-dokumentet må matche forventet issuer EKSAKT.
- ALLE returnerte endepunkter valideres mot egresspolicyen (§1) før bruk.
- Validert metadata caches med begrenset TTL (1 t); ved refresh brukes ny
  metadata FØRST etter komplett validering (gammel beholdes til da).
- Manuelt lagrede og discovery-returnerte endepunkter blandes ALDRI i
  samme flyt.
- Statisk provider uten discovery: **egen eksplisitt providertype**
  (`type: statisk`) med egne felter — ikke i v1 med mindre test-IdP-en
  krever det, og da tydelig merket.

## 3. Standard OIDC-klient — ingen hjemmelaget protokollkode

Vedlikeholdt, standardkonform bibliotek eier:
authorization code + PKCE · state/nonce-mekanikk · ID-token/JWS-validering ·
claim- og tidsvalidering · JWKS-cache og -rotasjon · feilklassifisering.

**Disponit-kode eier fortsatt:** browserbindingen (v4 §1),
tenant/provider-allowlist, SSRF-sikker transport (§1 — biblioteket får
vår pinnede HTTP-klient injisert), medlemskapsbindingen, sesjons-
opprettelsen, rategrensene.

Krav: biblioteket **pinnes** (eksakt versjon + hash i lockfil),
sikkerhetsoppdateres, og testes med negative testvektorer (`alg: none`,
feil `aud`, utløpt `exp`, manipulert signatur). **Ingen hjemmelaget
JWT-parser eller signaturverifikator** — Codex-port: grep etter egen
base64-dekoding av JWT-segmenter gir null treff.

## 4. Presiseringer
- **`/oidc/start`:** godkjent Origin/Host er PRIMÆR kontroll. Fetch
  Metadata er TILLEGG, ikke eneste kontroll; fravær håndteres etter
  eksplisitt fallbackregel (mangler `Sec-Fetch-Site` → krev godkjent
  Origin, ellers avvis).
- **Callback-query redigeres i ALLE lag:** nginx, ASGI-accesslogg,
  applikasjonslogg, tracing og feilrapportering.
- **Ærlig formulering:** ren redirect etter callback REDUSERER eksponering;
  dokumentasjonen lover IKKE at browserhistorikken fysisk aldri inneholder
  callback-URL-en.
- **Én ødelagt provider isoleres:** discovery- eller credentialfeil
  markerer KUN den provideren utilgjengelig — andre tenanters innlogging
  påvirkes ikke (per-provider helsetilstand, ikke global).

## Siste tester (reviewens, vedtatt)
DNS svarer offentlig ved validering og privat ved neste oppslag → INGEN
forbindelse opprettes · hostname som resolver til både offentlig og privat
IP → avvist · redirect fra offentlig til privat/link-local → avvist ·
TLS-sertifikat valideres mot original hostname ved IP-pinning · discovery
issuer-mismatch eller endepunkt utenfor egresspolicy → avvist · ukjent
`kid` kan ikke gi ubegrenset JWKS-fetch (rate-grense) · oversized
discovery/JWKS/tokenrespons avbrytes ved 256 KiB · én ødelagt provider
påvirker ikke andre.
