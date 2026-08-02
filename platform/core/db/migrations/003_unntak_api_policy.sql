-- ============================================================
-- Disponit migrasjon 003 — unntakskø (M-37-lagring), API-tabeller,
-- policyregister, tenant-DEK-register, jti-replay-vern.
-- Spesifisert i PR-005 v2 + v3-delta + GO-vilkår 1 og 2.
--
-- INGEN BEGIN/COMMIT: fra versjon 3 eier migrasjonskjøreren
-- transaksjonen (v3-delta pkt. 1.6). Kjøreren registrerer også
-- versjon+checksum i migrasjoner-tabellen — filen gjør det ikke selv.
-- Kjøres av MIGRATOR-rollen.
-- ============================================================

-- ------------------------------------------------------------
-- 0. Roller: IKKE opprettet her.
--
-- Draften opprettet `disponit_authenticator` med CREATE ROLE inne i
-- migrasjonen. Det feiler i vårt oppsett: migrasjoner kjøres av
-- MIGRATOR-rollen, som ikke har CREATEROLE — og skal ikke ha det. Roller
-- er klyngeobjekter, ikke skjemaobjekter, og opprettes av
-- `deploy/staging/oppsett-postgresql.sh` der superbrukeren finnes. Samme
-- skille ble innført i PR-004 nettopp for at runtime ikke skal kunne endre
-- sine egne rammer.
--
-- Migrasjonen stopper med en tydelig melding hvis rollen mangler, i stedet
-- for å feile halvveis nede i filen på en OWNER TO.
-- ------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_authenticator') THEN
        RAISE EXCEPTION
            'rollen disponit_authenticator mangler — kjør deploy/staging/oppsett-postgresql.sh først (roller opprettes der, ikke i migrasjoner)';
    END IF;
    IF NOT pg_has_role(current_user, 'disponit_authenticator', 'MEMBER') THEN
        RAISE EXCEPTION
            'migratorrollen % er ikke medlem av disponit_authenticator — kreves for OWNER TO. Settes i oppsett-skriptet.', current_user;
    END IF;
END $$;

-- ------------------------------------------------------------
-- 1. Revisjonslogg: selvstendig evidensidentitet (retro-P1 #3)
-- ------------------------------------------------------------
ALTER TABLE revisjonslogg
    ADD COLUMN IF NOT EXISTS handling             TEXT,
    ADD COLUMN IF NOT EXISTS request_id           TEXT,
    ADD COLUMN IF NOT EXISTS idempotency_key      TEXT,
    ADD COLUMN IF NOT EXISTS policy_content_hash  TEXT,
    ADD COLUMN IF NOT EXISTS attestation_set_hash TEXT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'revisjonslogg_tenant_id_unik') THEN
        ALTER TABLE revisjonslogg
            ADD CONSTRAINT revisjonslogg_tenant_id_unik UNIQUE (tenant, id);
    END IF;
END $$;

-- ------------------------------------------------------------
-- 2. Tenant-DEK-register (v3-delta pkt. 3, GO-vilkår 1)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenant_nokler (
    tenant       TEXT NOT NULL,
    key_id       TEXT NOT NULL,
    wrapped_dek  BYTEA,
    wrap_alg     TEXT NOT NULL DEFAULT 'AES-256-GCM-KEK-v1',
    dek_alg      TEXT NOT NULL DEFAULT 'AES-256-GCM',
    opprettet    TIMESTAMPTZ NOT NULL DEFAULT now(),
    aktiv        BOOLEAN NOT NULL DEFAULT true,
    destruert_ts TIMESTAMPTZ,
    PRIMARY KEY (tenant, key_id),
    -- GO-vilkår 1: en destruert nøkkel kan aldri være aktiv, og en
    -- levende nøkkel kan aldri være merket destruert.
    CONSTRAINT dek_liv_eller_destruert CHECK (
        (wrapped_dek IS NOT NULL AND destruert_ts IS NULL)
        OR
        (wrapped_dek IS NULL AND destruert_ts IS NOT NULL AND aktiv = false)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS en_aktiv_dek_per_tenant
    ON tenant_nokler (tenant) WHERE aktiv;

-- Eneste tillatte UPDATE er destruksjon (crypto-shredding, v3-delta pkt. 2)
-- eller deaktivering (aktiv -> false ved rotasjon).
CREATE OR REPLACE FUNCTION tenant_nokler_kun_destruksjon()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant  IS DISTINCT FROM OLD.tenant
       OR NEW.key_id IS DISTINCT FROM OLD.key_id
       OR NEW.wrap_alg IS DISTINCT FROM OLD.wrap_alg
       OR NEW.dek_alg  IS DISTINCT FROM OLD.dek_alg
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'tenant_nokler: identitetsfelter er uforanderlige';
    END IF;
    IF OLD.wrapped_dek IS NULL THEN
        RAISE EXCEPTION 'tenant_nokler: destruert nøkkel kan ikke endres';
    END IF;
    -- Lovlige overganger: deaktivering, og destruksjon (alt i én UPDATE)
    IF NEW.wrapped_dek IS NULL AND
       (NEW.destruert_ts IS NULL OR NEW.aktiv) THEN
        RAISE EXCEPTION 'tenant_nokler: destruksjon krever destruert_ts og aktiv=false';
    END IF;
    IF NEW.wrapped_dek IS NOT NULL
       AND NEW.wrapped_dek IS DISTINCT FROM OLD.wrapped_dek THEN
        RAISE EXCEPTION 'tenant_nokler: wrapped_dek kan aldri byttes, kun destrueres';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS tenant_nokler_vakt ON tenant_nokler;
CREATE TRIGGER tenant_nokler_vakt BEFORE UPDATE ON tenant_nokler
    FOR EACH ROW EXECUTE FUNCTION tenant_nokler_kun_destruksjon();
CREATE OR REPLACE FUNCTION tenant_nokler_ingen_sletting()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'tenant_nokler: % er forbudt — nøkler destrueres, aldri slettes', TG_OP;
END $$;
DROP TRIGGER IF EXISTS tenant_nokler_ingen_delete ON tenant_nokler;
CREATE TRIGGER tenant_nokler_ingen_delete BEFORE DELETE ON tenant_nokler
    FOR EACH ROW EXECUTE FUNCTION tenant_nokler_ingen_sletting();
DROP TRIGGER IF EXISTS tenant_nokler_ingen_truncate ON tenant_nokler;
CREATE TRIGGER tenant_nokler_ingen_truncate BEFORE TRUNCATE ON tenant_nokler
    FOR EACH STATEMENT EXECUTE FUNCTION tenant_nokler_ingen_sletting();

-- ------------------------------------------------------------
-- 3. Unntak (M-37-lagring) med sakstype og evidenskjede
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS unntak (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    tenant        TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    loggpost_id   BIGINT NOT NULL,
    handling      TEXT NOT NULL,
    kategori      TEXT NOT NULL,
    sakstype      TEXT NOT NULL DEFAULT 'normal'
                  CHECK (sakstype IN ('normal','sikkerhet','drift')),
    prioritet     TEXT NOT NULL DEFAULT 'normal'
                  CHECK (prioritet IN ('normal','hoy')),
    status        TEXT NOT NULL DEFAULT 'ny'
                  CHECK (status IN ('ny','under_behandling','løst','avvist')),
    status_ts     TIMESTAMPTZ NOT NULL DEFAULT now(),
    forsok        INT NOT NULL DEFAULT 0 CHECK (forsok >= 0),
    claim_id      TEXT,
    claim_utloper TIMESTAMPTZ,
    payload_kryptert BYTEA NOT NULL,          -- ct || gcm-tag (siste 16 byte)
    key_id        TEXT NOT NULL,
    alg           TEXT NOT NULL DEFAULT 'AES-256-GCM',
    nonce         BYTEA NOT NULL,
    CONSTRAINT unntak_tenant_id_unik UNIQUE (tenant, id),
    FOREIGN KEY (tenant, loggpost_id) REFERENCES revisjonslogg (tenant, id),
    FOREIGN KEY (tenant, key_id)      REFERENCES tenant_nokler (tenant, key_id)
);
CREATE INDEX IF NOT EXISTS unntak_ko ON unntak (tenant, sakstype, status, prioritet, ts);

-- Kolonnelås + statusmaskin (v2 1.3 + v3-delta pkt. 5: sakstype låst)
CREATE OR REPLACE FUNCTION unntak_kolonnelaas()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.loggpost_id IS DISTINCT FROM OLD.loggpost_id
       OR NEW.handling IS DISTINCT FROM OLD.handling
       OR NEW.kategori IS DISTINCT FROM OLD.kategori
       OR NEW.sakstype IS DISTINCT FROM OLD.sakstype
       OR NEW.prioritet IS DISTINCT FROM OLD.prioritet
       OR NEW.payload_kryptert IS DISTINCT FROM OLD.payload_kryptert
       OR NEW.key_id IS DISTINCT FROM OLD.key_id
       OR NEW.alg IS DISTINCT FROM OLD.alg
       OR NEW.nonce IS DISTINCT FROM OLD.nonce
       OR NEW.ts IS DISTINCT FROM OLD.ts THEN
        RAISE EXCEPTION 'unntak: kun status/status_ts/forsok/claim-felter kan endres';
    END IF;
    IF NOT (
        (OLD.status = 'ny'               AND NEW.status = 'under_behandling') OR
        (OLD.status = 'under_behandling' AND NEW.status IN ('løst','avvist')) OR
        (OLD.status = 'under_behandling' AND NEW.status = 'ny'
             AND OLD.claim_utloper IS NOT NULL AND OLD.claim_utloper < now()) OR
        (OLD.status = NEW.status)  -- forsok/claim-oppdatering uten statusskifte
    ) THEN
        RAISE EXCEPTION 'unntak: ulovlig statusovergang % -> %', OLD.status, NEW.status;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        NEW.status_ts := now();
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS unntak_laas ON unntak;
CREATE TRIGGER unntak_laas BEFORE UPDATE ON unntak
    FOR EACH ROW EXECUTE FUNCTION unntak_kolonnelaas();

CREATE OR REPLACE FUNCTION unntak_ingen_sletting()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'unntak er append+status: % er forbudt', TG_OP;
END $$;
DROP TRIGGER IF EXISTS unntak_ingen_delete ON unntak;
CREATE TRIGGER unntak_ingen_delete BEFORE DELETE ON unntak
    FOR EACH ROW EXECUTE FUNCTION unntak_ingen_sletting();
DROP TRIGGER IF EXISTS unntak_ingen_truncate ON unntak;
CREATE TRIGGER unntak_ingen_truncate BEFORE TRUNCATE ON unntak
    FOR EACH STATEMENT EXECUTE FUNCTION unntak_ingen_sletting();

-- ------------------------------------------------------------
-- 4. Unntakshistorikk — tenantbundet, append-only (v3-delta pkt. 4)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS unntak_historikk (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant     TEXT NOT NULL,
    unntak_id  BIGINT NOT NULL,
    hendelse   TEXT NOT NULL CHECK (hendelse IN
               ('opprettet','statusendring','claim','claim_utlopt','dek_destruert')),
    fra_status TEXT,
    til_status TEXT,
    aktor      TEXT NOT NULL,
    request_id TEXT,
    claim_id   TEXT,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant, unntak_id) REFERENCES unntak (tenant, id)
);
CREATE OR REPLACE FUNCTION unntak_historikk_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'unntak_historikk er append-only: % er forbudt', TG_OP;
END $$;
DROP TRIGGER IF EXISTS historikk_ingen_endring ON unntak_historikk;
CREATE TRIGGER historikk_ingen_endring BEFORE UPDATE OR DELETE ON unntak_historikk
    FOR EACH ROW EXECUTE FUNCTION unntak_historikk_append_only();
DROP TRIGGER IF EXISTS historikk_ingen_truncate ON unntak_historikk;
CREATE TRIGGER historikk_ingen_truncate BEFORE TRUNCATE ON unntak_historikk
    FOR EACH STATEMENT EXECUTE FUNCTION unntak_historikk_append_only();

-- Historikk skrives av trigger på unntak; aktor fra SERVERKONTEKST.
-- Mangler disponit.aktor -> transaksjonen feiler (fail-closed, ingen
-- anonym historikk). request_id kan mangle (interne jobber).
CREATE OR REPLACE FUNCTION unntak_skriv_historikk()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_aktor TEXT := current_setting('disponit.aktor', true);
BEGIN
    IF v_aktor IS NULL OR length(btrim(v_aktor)) = 0 THEN
        RAISE EXCEPTION 'disponit.aktor er ikke satt — historikk uten aktør er forbudt';
    END IF;
    IF TG_OP = 'INSERT' THEN
        INSERT INTO unntak_historikk (tenant, unntak_id, hendelse, fra_status,
                                      til_status, aktor, request_id, claim_id)
        VALUES (NEW.tenant, NEW.id, 'opprettet', NULL, NEW.status, v_aktor,
                current_setting('disponit.request_id', true), NEW.claim_id);
    ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO unntak_historikk (tenant, unntak_id, hendelse, fra_status,
                                      til_status, aktor, request_id, claim_id)
        VALUES (NEW.tenant, NEW.id,
                CASE WHEN NEW.status = 'under_behandling' THEN 'claim'
                     WHEN OLD.status = 'under_behandling' AND NEW.status = 'ny'
                          THEN 'claim_utlopt'
                     ELSE 'statusendring' END,
                OLD.status, NEW.status, v_aktor,
                current_setting('disponit.request_id', true), NEW.claim_id);
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS unntak_historikkforing ON unntak;
CREATE TRIGGER unntak_historikkforing AFTER INSERT OR UPDATE ON unntak
    FOR EACH ROW EXECUTE FUNCTION unntak_skriv_historikk();

-- ------------------------------------------------------------
-- 5. Idempotens (v2 1.4 + v3-delta pkt. 6)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS idempotens (
    tenant     TEXT NOT NULL,
    nokkel     TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'paagaar'
               CHECK (status IN ('paagaar','ferdig')),
    respons    JSONB,
    request_id TEXT,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, nokkel),
    CONSTRAINT ferdig_har_respons CHECK (status <> 'ferdig' OR respons IS NOT NULL)
);

-- ------------------------------------------------------------
-- 6. Policyregister (v2 1.5)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS policyer (
    tenant        TEXT NOT NULL,
    policy_id     TEXT NOT NULL,
    versjon       TEXT NOT NULL,
    innholds_hash TEXT NOT NULL,
    status        TEXT NOT NULL,
    innhold       JSONB NOT NULL,
    aktiv         BOOLEAN NOT NULL DEFAULT false,
    opprettet     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, policy_id, versjon)
);
CREATE UNIQUE INDEX IF NOT EXISTS en_aktiv_per_policy
    ON policyer (tenant, policy_id) WHERE aktiv;

-- ------------------------------------------------------------
-- 7. Attestasjon-jti (replay-vern, v2 Del 2)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS attestasjon_jti (
    tenant    TEXT NOT NULL,
    jti       TEXT NOT NULL CHECK (length(jti) >= 22),  -- >=128 bit b64/hex
    konsumert TIMESTAMPTZ NOT NULL DEFAULT now(),
    utloper   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant, jti)
);

-- ------------------------------------------------------------
-- 8. API-tokens + herdet SECURITY DEFINER (v3-delta pkt. 7, GO-vilkår 2)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_tokener (
    token_id     TEXT PRIMARY KEY,
    tenant       TEXT NOT NULL,
    rolle        TEXT NOT NULL,
    scopes       TEXT[] NOT NULL,
    secret_mac   TEXT NOT NULL CHECK (secret_mac ~ '^[0-9a-f]{64}$'),
    aktiv        BOOLEAN NOT NULL DEFAULT true,
    utloper      TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    opprettet    TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE api_tokener OWNER TO disponit_authenticator;
REVOKE ALL ON api_tokener FROM PUBLIC;

-- Sammenligningen skjer INNE i funksjonen på kandidat-MAC beregnet i
-- API-prosessen med pepper som ALDRI finnes i databasen. En angriper med
-- full DB-lesing kan derfor ikke konstruere gyldige kandidater, og et
-- prefiks-timing-orakel gir ingen vei til pepper. GO-vilkår 2 oppfylles:
-- fast lengde håndheves av CHECK og format-guard FØR sammenligning,
-- secret_mac returneres aldri, ingen dynamisk SQL, search_path=pg_catalog.
-- Skulle Codex kreve applikasjonsside-sammenligning likevel, er byttet
-- avgrenset til denne funksjonens kropp.
CREATE OR REPLACE FUNCTION verifiser_token(p_token_id TEXT, p_kandidat_mac TEXT)
RETURNS TABLE (tenant TEXT, rolle TEXT, scopes TEXT[])
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF p_kandidat_mac IS NULL OR p_kandidat_mac !~ '^[0-9a-f]{64}$' THEN
        RETURN;  -- avvis før sammenligning ved ugyldig format/lengde
    END IF;
    RETURN QUERY
    SELECT t.tenant, t.rolle, t.scopes
      FROM public.api_tokener t
     WHERE t.token_id = p_token_id
       AND t.secret_mac = p_kandidat_mac
       AND t.aktiv
       AND (t.utloper IS NULL OR t.utloper > pg_catalog.now());
END $$;
ALTER FUNCTION verifiser_token(TEXT, TEXT) OWNER TO disponit_authenticator;
REVOKE ALL ON FUNCTION verifiser_token(TEXT, TEXT) FROM PUBLIC;

-- ------------------------------------------------------------
-- 9. RLS + FORCE på alle nye tenant-tabeller (mønster fra 002)
-- ------------------------------------------------------------
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['unntak','unntak_historikk','idempotens',
                             'policyer','tenant_nokler','attestasjon_jti']
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

-- Runtime-rettigheter (rollen opprettes av deploy-oppsettet; betinget grant)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_runtime') THEN
        GRANT SELECT, INSERT, UPDATE ON unntak, idempotens, tenant_nokler TO disponit_runtime;
        GRANT SELECT, INSERT ON attestasjon_jti TO disponit_runtime;
        GRANT SELECT ON policyer, unntak_historikk TO disponit_runtime;
        GRANT EXECUTE ON FUNCTION verifiser_token(TEXT, TEXT) TO disponit_runtime;
    END IF;
END $$;
