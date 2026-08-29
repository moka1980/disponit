# PR-010 — IMPLEMENTERINGSKLARSIGNAL (GO, OIDC-sesjon)

**Til Claude Code · Implementér mot v1–v6 fra main ETTER PR-009b.
Branch: `pr-010-oidc-sesjon`. GO + seks vilkår i PR-beskrivelsen.
Rekkefølge: PR-009 drift → PR-009b transport → PR-010 → M-1 UI.**

## De seks implementeringsvilkårene (bindende merge-krav)

### V1. Kun globalt routbare adresser (allowlist-prinsipp, ikke blocklist)
Produksjonsregelen er **positiv**: alle resolvede adresser MÅ være globalt
routbare. Det avviser automatisk CGNAT (100.64/10), dokumentasjonsnett,
benchmarking-nett og andre spesialområder — ikke bare de opplistede.
Eneste unntak: den eksakte staging-allowlisten
`(scheme, host, port, IP/CIDR)` for test-IdP.

### V2. Null redirects i v1
`redirects = 0` for discovery, JWKS OG tokenveksling. Providerkonfig peker
DIREKTE på korrekt HTTPS-endepunkt. Formuleringen «maks 2 der discovery
krever det» fra v6 §1 tas IKKE med — hvis redirects senere trengs, er det
egen kontrakt med revalidering og repinning per hopp.

### V3. Cache utløper fail-closed
Validert metadata: TTL 1 time · refresh før/ved utløp · **feiler refresh
etter utløpt TTL → provideren er utilgjengelig** · gammel metadata brukes
ALDRI på ubestemt tid · én providers feil isolert fra andre.

### V4. Alle bibliotekets nettverkskall gjennom den pinnede klienten
Gjelder discovery, JWKS førstehenting, JWKS-refresh ved ukjent `kid`,
tokenveksling og eventuell senere UserInfo. **Bibliotekets innebygde
klient/DNS deaktiveres eller erstattes** — velg et bibliotek som faktisk
støtter injisert transport (dette er et utvelgelseskriterium, ikke en
etterpå-tilpasning).

### V5. Original host bevares gjennom hele forbindelsen
TCP → validert IP · TLS-SNI og sertifikatverifikasjon → original hostname ·
HTTP `Host` → original hostname · **connection-poolens nøkkel inkluderer
(hostname, pinned IP)** · forbindelse gjenbrukes ALDRI for annen
provider/host.

### V6. Én eier av state/nonce/PKCE
Biblioteket kan generere og validere verdiene, men **Disponits
login-transaksjon (DB-statusmaskinen) er den autoritative engangs- og
browserbindingen**. Biblioteket skal IKKE opprette en parallell
cookie-/sesjonsbasert state-mekanisme som kan avvike fra DB-en.

## De tolv Codex-portene
1. Alle A/AAAA-resultater globalt routbare eller eksakt staging-allowlistet
2. CNAME-/DNS-kjede har lukket maksimum og ender i full IP-validering
3. Ingen OIDC-request følger redirect
4. Ingen ny DNS-oppløsning mellom validering og sockettilkobling
5. TLS-SNI, sertifikat og `Host` validert mot original hostname
6. Utløpt metadata brukes ikke etter mislykket refresh
7. JWKS-refresh bruker samme pinnede transport og er ratebegrenset
8. Bibliotekets standardtransport kan ikke nå nettverket
9. Manipulert state, nonce, PKCE, issuer, audience og signatur avvises
10. Callback i annen browser uten bindingcookie avvist FØR tokenveksling
11. Ingen code/state/querystring eller providerhemmelighet i logg eller tracing
12. PR-010-e2e nekter å kjøre før PR-009b har verifisert HTTPS

## Implementeringsomfang (v1–v6)
Migrasjon 009: `oidc_provider` (kun issuer + discovery_url + client_id +
credential-ref + algoritmer + aktiv), `tenant_oidc_provider`,
`brukeridentitet` UNIQUE(issuer,sub), `brukermedlemskap` med roller som
eneste autoritet + `authz_version`-trigger, `oidc_logintransaksjon`
(NY→KONSUMERT→FULLFØRT|FEILET), `brukersesjon` · fire ruter
(`POST /v1/oidc/start` 303, `GET /v1/oidc/callback`, `GET /v1/sesjon`,
`DELETE /v1/sesjon`) · tre cookies (`__Host-disponit_sesjon` HttpOnly,
`__Host-disponit_csrf` JS-lesbar, `__Host-disponit_oidc` binding) ·
pinnet SSRF-transport · rategrenser med konkrete tall · callback-redaksjon
i alle logglag · scopes utledet fra roller.
