-- ============================================================
-- Disponit migrasjon 010 — OIDC-brukersesjon (PR-010 v1–v6).
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen. Kjøres av MIGRATOR.
--
-- Disponit er RELYING PARTY: identiteten er (issuer, sub), aldri e-post
-- (v3 §2). Roller er eneste autoritet, scopes AVLEDES i kode (v5 §4) —
-- derfor ingen scopes-kolonne. Ingen JIT-medlemskap (v3 §2): medlemskap
-- må finnes på forhånd.
-- ============================================================

-- ------------------------------------------------------------
-- 1. Provider: KUN discovery som metadatakilde (v6 §2)
--    Endepunkter (authorization/token/jwks) hentes fra discovery — aldri
--    lagret her, så manuelle og discovery-endepunkter aldri blandes.
--    `client_secret_ref` er en credential-referanse, ALDRI hemmeligheten
--    (v4 §2 / v5 §3), i lukket format håndhevet av CHECK.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS oidc_provider (
    provider_id         TEXT PRIMARY KEY CHECK (provider_id ~ '^[a-z0-9_-]{1,64}$'),
    issuer              TEXT NOT NULL UNIQUE,
    discovery_url       TEXT NOT NULL,
    client_id           TEXT NOT NULL,
    client_secret_ref   TEXT NOT NULL CHECK (client_secret_ref ~ '^[a-z0-9_-]{1,64}$'),
    tillatte_algoritmer TEXT[] NOT NULL CHECK (
        array_length(tillatte_algoritmer, 1) >= 1
        AND NOT ('none' = ANY(tillatte_algoritmer))),   -- aldri 'none'
    aktiv               BOOLEAN NOT NULL DEFAULT false,
    opprettet           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 2. Tenant ↔ provider: eksakte redirect-URI-er, ingen mønster (v4 §2)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenant_oidc_provider (
    tenant        TEXT NOT NULL,
    provider_id   TEXT NOT NULL REFERENCES oidc_provider (provider_id),
    redirect_uris TEXT[] NOT NULL CHECK (array_length(redirect_uris, 1) >= 1),
    PRIMARY KEY (tenant, provider_id)
);

-- ------------------------------------------------------------
-- 3. Brukeridentitet: (issuer, sub) er identiteten. Profil er en LUKKET
--    DTO uten autoritetsverdi (v5 §5) — hele ID-tokenet lagres aldri.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS brukeridentitet (
    -- gen_random_uuid() er kjerne (pg_catalog, PG13+) — ingen pgcrypto-
    -- avhengighet i search_path (den varierer mellom kjøreveier).
    bruker_id  TEXT PRIMARY KEY DEFAULT ('bid_' || replace(gen_random_uuid()::text, '-', '')),
    issuer     TEXT NOT NULL,
    sub        TEXT NOT NULL,
    profil     JSONB NOT NULL DEFAULT '{}'::jsonb,
    opprettet  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (issuer, sub)
);

-- ------------------------------------------------------------
-- 4. Medlemskap: roller er ENESTE autoritet (v5 §4). `authz_version` økes
--    ATOMISK av trigger ved endring av aktiv/roller — runtime kan ikke
--    endre fullmakter uten versjonsøkning (negativ test).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS brukermedlemskap (
    tenant        TEXT NOT NULL,
    bruker_id     TEXT NOT NULL REFERENCES brukeridentitet (bruker_id),
    aktiv         BOOLEAN NOT NULL DEFAULT true,
    roller        TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    authz_version INT NOT NULL DEFAULT 1,
    id            BIGINT GENERATED ALWAYS AS IDENTITY,
    PRIMARY KEY (tenant, bruker_id)
);

CREATE OR REPLACE FUNCTION brukermedlemskap_authz_bump()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    -- Enhver sikkerhetsrelevant endring bumper versjonen. Uendret rad
    -- (bare et no-op UPDATE) rører den ikke.
    IF NEW.aktiv IS DISTINCT FROM OLD.aktiv
       OR NEW.roller IS DISTINCT FROM OLD.roller THEN
        NEW.authz_version := OLD.authz_version + 1;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS brukermedlemskap_authz ON brukermedlemskap;
CREATE TRIGGER brukermedlemskap_authz BEFORE UPDATE ON brukermedlemskap
    FOR EACH ROW EXECUTE FUNCTION brukermedlemskap_authz_bump();

-- ------------------------------------------------------------
-- 5. Login-transaksjon: kortlivet, engangs (v3 §1 + v4 §3).
--    Statusmaskin NY → KONSUMERT → FULLFØRT|FEILET. KUN hasher lagres.
--    pkce_verifier krypteres i ro (v3 §1) — her lagres ciphertext.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS oidc_logintransaksjon (
    state_hash       TEXT PRIMARY KEY CHECK (state_hash ~ '^[0-9a-f]{64}$'),
    binding_hash     TEXT NOT NULL CHECK (binding_hash ~ '^[0-9a-f]{64}$'),
    nonce            TEXT NOT NULL,
    pkce_kryptert    BYTEA NOT NULL,
    pkce_nonce       BYTEA NOT NULL,
    pkce_key_id      TEXT NOT NULL,
    provider_id      TEXT NOT NULL REFERENCES oidc_provider (provider_id),
    tenant_kandidat  TEXT NOT NULL,
    retursti         TEXT NOT NULL CHECK (retursti ~ '^/'),   -- RELATIV
    status           TEXT NOT NULL DEFAULT 'NY'
                     CHECK (status IN ('NY','KONSUMERT','FULLFØRT','FEILET')),
    opprettet        TIMESTAMPTZ NOT NULL DEFAULT now(),
    utloper          TIMESTAMPTZ NOT NULL
);

CREATE OR REPLACE FUNCTION oidc_logintransaksjon_overgang()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NOT (
        (OLD.status = 'NY'        AND NEW.status IN ('KONSUMERT','FEILET')) OR
        (OLD.status = 'KONSUMERT' AND NEW.status IN ('FULLFØRT','FEILET')) OR
        (OLD.status = NEW.status)
    ) THEN
        RAISE EXCEPTION 'oidc_logintransaksjon: ulovlig overgang % -> %',
            OLD.status, NEW.status;
    END IF;
    IF OLD.status IN ('FULLFØRT','FEILET') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'oidc_logintransaksjon: % er terminal', OLD.status;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS oidc_logintransaksjon_laas ON oidc_logintransaksjon;
CREATE TRIGGER oidc_logintransaksjon_laas BEFORE UPDATE ON oidc_logintransaksjon
    FOR EACH ROW EXECUTE FUNCTION oidc_logintransaksjon_overgang();

-- ------------------------------------------------------------
-- 6. Brukersesjon: kun hasher lagres (v1 §1 + v2 §7). Uforanderlig unntatt
--    siste_bruk + tilbakekalt (kolonnelås). `authz_snapshot` = versjonen
--    sesjonen ble opprettet med; hvert kall sammenligner mot aktiv (v2 §3).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS brukersesjon (
    sesjon_id_hash  TEXT PRIMARY KEY CHECK (sesjon_id_hash ~ '^[0-9a-f]{64}$'),
    tenant          TEXT NOT NULL,
    bruker_id       TEXT NOT NULL,
    authz_snapshot  INT NOT NULL,
    csrf_hash       TEXT NOT NULL CHECK (csrf_hash ~ '^[0-9a-f]{64}$'),
    opprettet       TIMESTAMPTZ NOT NULL DEFAULT now(),
    siste_bruk      TIMESTAMPTZ NOT NULL DEFAULT now(),
    utloper         TIMESTAMPTZ NOT NULL,                    -- absolutt tak
    tilbakekalt     BOOLEAN NOT NULL DEFAULT false,
    id              BIGINT GENERATED ALWAYS AS IDENTITY
);
CREATE INDEX IF NOT EXISTS brukersesjon_bruker
    ON brukersesjon (tenant, bruker_id, opprettet, id)
    WHERE NOT tilbakekalt;

CREATE OR REPLACE FUNCTION brukersesjon_kolonnelaas()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.sesjon_id_hash IS DISTINCT FROM OLD.sesjon_id_hash
       OR NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.bruker_id IS DISTINCT FROM OLD.bruker_id
       OR NEW.authz_snapshot IS DISTINCT FROM OLD.authz_snapshot
       OR NEW.csrf_hash IS DISTINCT FROM OLD.csrf_hash
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.utloper IS DISTINCT FROM OLD.utloper THEN
        RAISE EXCEPTION 'brukersesjon: kun siste_bruk og tilbakekalt kan endres';
    END IF;
    -- Tilbakekalling er enveis.
    IF OLD.tilbakekalt AND NOT NEW.tilbakekalt THEN
        RAISE EXCEPTION 'brukersesjon: en tilbakekalt sesjon kan ikke gjenopplives';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS brukersesjon_laas ON brukersesjon;
CREATE TRIGGER brukersesjon_laas BEFORE UPDATE ON brukersesjon
    FOR EACH ROW EXECUTE FUNCTION brukersesjon_kolonnelaas();

-- ------------------------------------------------------------
-- 7. Login-rate-state: delt, atomisk, overlever restart (v3 §5 + v4 §4).
--    Én rad per (fase, nøkkel); increment + grensekontroll i én
--    ON CONFLICT DO UPDATE ... RETURNING under radlåsen.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS oidc_rate (
    fase         TEXT NOT NULL,
    nokkel       TEXT NOT NULL,
    teller       INT NOT NULL DEFAULT 0,
    vindu_start  TIMESTAMPTZ NOT NULL DEFAULT now(),
    sperret_til  TIMESTAMPTZ,
    PRIMARY KEY (fase, nokkel)
);

-- ------------------------------------------------------------
-- 8. RLS + FORCE på de TENANT-SKANNEDE tabellene (mønster fra 003).
--    `brukersesjon` og `oidc_logintransaksjon` er BEVISST UTENFOR RLS:
--    begge slås opp på en ugjettbar HASH FØR tenanten er kjent (sesjonen/
--    transaksjonen BÆRER tenanten), så en RLS-policy som krever
--    tenantkontekst ville skjult raden vi nettopp skal lese. Samme
--    chicken-egg som `arbeidskapabiliteter` (005) — løsningen er identisk:
--    REVOKE FROM PUBLIC, oppslag via herdet SECURITY DEFINER, og
--    tenantbindingen håndheves nedstrøms (funksjonen returnerer tenanten,
--    appen setter konteksten, og alle unntaks-/beslutningslesninger er
--    RLS-beskyttet av DEN tenanten).
--    Provider/identitet er delt/global og har egne avgrensede grants.
-- ------------------------------------------------------------
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['tenant_oidc_provider','brukermedlemskap']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolasjon ON %I', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolasjon ON %I
                USING      (tenant = current_setting(''disponit.tenant'', true))
                WITH CHECK (tenant = current_setting(''disponit.tenant'', true))', t);
    END LOOP;
END $$;

-- ------------------------------------------------------------
-- 9. Herdet sesjonsoppslag (v1 §1): SECURITY DEFINER, eid av
--    authenticator, returnerer (tenant, bruker_id, authz_snapshot) —
--    ALDRI hashene. Setter tenantkontekst internt så RLS gjelder, og
--    oppdaterer siste_bruk maks 1×/min (ikke-blokkerende, v2 §2).
--    Én kodevei for BÅDE cookie- og Bearer-oppslag (v1 §7).
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION slaa_opp_sesjon(p_hash TEXT)
RETURNS TABLE (tenant TEXT, bruker_id TEXT, authz_snapshot INT,
               csrf_hash TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE r RECORD;
BEGIN
    SELECT s.tenant, s.bruker_id, s.authz_snapshot, s.csrf_hash,
           s.utloper, s.siste_bruk, s.tilbakekalt
      INTO r
      FROM public.brukersesjon s
     WHERE s.sesjon_id_hash = p_hash;
    IF NOT FOUND OR r.tilbakekalt
       OR r.utloper <= pg_catalog.now()
       OR r.siste_bruk < pg_catalog.now() - INTERVAL '30 minutes' THEN
        RETURN;   -- utløpt/inaktiv/tilbakekalt → ingen rad (401 i app-laget)
    END IF;
    UPDATE public.brukersesjon
       SET siste_bruk = pg_catalog.now()
     WHERE sesjon_id_hash = p_hash
       AND siste_bruk < pg_catalog.now() - INTERVAL '1 minute';
    tenant := r.tenant; bruker_id := r.bruker_id;
    authz_snapshot := r.authz_snapshot; csrf_hash := r.csrf_hash;
    RETURN NEXT;
END $$;
ALTER FUNCTION slaa_opp_sesjon(TEXT) OWNER TO disponit_authenticator;
REVOKE ALL ON FUNCTION slaa_opp_sesjon(TEXT) FROM PUBLIC;
-- Definer-funksjonen kjører som authenticator; den trenger derfor lese-
-- og siste_bruk-oppdateringsrett på den RLS-frie sesjonstabellen.
-- `brukersesjon` når PUBLIC aldri direkte.
REVOKE ALL ON brukersesjon FROM PUBLIC;
GRANT SELECT, UPDATE ON brukersesjon TO disponit_authenticator;

-- ------------------------------------------------------------
-- 10. Runtime-rettigheter (betinget — rollen fra deploy-oppsettet).
--     `disponit` (API-runtime) trenger DML på sesjons-/OIDC-tabellene og
--     EXECUTE på oppslaget. Provider-tabellen: KUN SELECT (aldri skrive
--     provider fra runtime).
-- ------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        GRANT SELECT ON oidc_provider, tenant_oidc_provider TO disponit;
        GRANT SELECT, INSERT, UPDATE ON brukeridentitet TO disponit;
        GRANT SELECT ON brukermedlemskap TO disponit;
        GRANT SELECT, INSERT, UPDATE ON oidc_logintransaksjon TO disponit;
        GRANT SELECT, INSERT, UPDATE ON brukersesjon TO disponit;
        GRANT SELECT, INSERT, UPDATE, DELETE ON oidc_rate TO disponit;
        GRANT EXECUTE ON FUNCTION slaa_opp_sesjon(TEXT) TO disponit;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO disponit;
    END IF;
END $$;
