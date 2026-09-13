-- 197: EN TOM ALGORITMELISTE SLÅR AV ALGORITMEPINNINGEN — STILLE.
--
-- MÅLT 13/9, i denne rekkefølgen:
--
--   1. `INSERT INTO oidc_provider (… tillatte_algoritmer) VALUES (… '{}')`
--      BLE AKSEPTERT. CHECK-en fra 010 er `array_length(…,1) >= 1`. For et
--      tomt array er `array_length` NULL, `NULL >= 1` er NULL, og en CHECK
--      SLIPPER NULL GJENNOM. Samme feilklasse som 194 (`roller`) og 182
--      (`NULL !~ mønster`).
--   2. `jwt.decode(token, nøkkel, algorithms=[])` i joserfc AKSEPTERTE et
--      RS256-token. Med `algorithms=["HS256"]` ble det samme tokenet avvist
--      med `UnsupportedAlgorithmError`. Tom liste betyr altså «ingen
--      begrensning», ikke «ingenting tillatt».
--   3. `alg=none` avvises av biblioteket selv, så dette er IKKE full
--      signaturomgåelse. Men vernet mot algoritmeforvirring er borte, og
--      det vernet er hele grunnen til at kolonnen finnes.
--
-- HVORFOR NÅ: ingen kunde skriver denne tabellen — bare migrator og eier.
-- Men Microsoft-innloggingen skriver provider-rader FOR HÅND på verten, og
-- en tom liste er nøyaktig den tastefeilen CHECK-en finnes for å fange.
--
-- `redirect_uris` i `tenant_oidc_provider` har samme CHECK-form og samme
-- hull: en kobling uten en eneste redirect-URI ville blitt akseptert.
--
-- PROD TÅLER DETTE: målt før skriving — `google` og `e2e` har begge én
-- algoritme, og den ene tenantkoblingen har sine URI-er. Ingen eksisterende
-- rad bryter den strammere CHECK-en.
-- ============================================================

ALTER TABLE oidc_provider
    DROP CONSTRAINT IF EXISTS oidc_provider_tillatte_algoritmer_check;
ALTER TABLE oidc_provider
    ADD CONSTRAINT oidc_provider_algoritmer_ikke_tom
    CHECK (cardinality(tillatte_algoritmer) >= 1
           AND NOT ('none' = ANY(tillatte_algoritmer)));

ALTER TABLE tenant_oidc_provider
    DROP CONSTRAINT IF EXISTS tenant_oidc_provider_redirect_uris_check;
ALTER TABLE tenant_oidc_provider
    ADD CONSTRAINT tenant_oidc_provider_uris_ikke_tom
    CHECK (cardinality(redirect_uris) >= 1);

COMMENT ON COLUMN oidc_provider.tillatte_algoritmer IS
    'Signaturalgoritmer som godtas for denne provideren. ALDRI tom: en tom '
    'liste sendes videre som `algorithms=[]`, og biblioteket leser det som '
    '«ingen begrensning» (målt 13/9). `cardinality`, ikke `array_length` — '
    'sistnevnte gir NULL for tomt array, og en CHECK slipper NULL gjennom.';
