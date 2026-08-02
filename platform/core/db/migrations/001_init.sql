-- ============================================================
-- Disponit migrasjon 001 — tilstandslaget for tillitsankeret
-- ADR-001: revisjonslogg (M-2) og frekvensteller i PostgreSQL.
-- Idempotent: trygg å kjøre flere ganger.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- M-2 Revisjonslogg. Append-only HÅNDHEVET AV DATABASEN:
-- UPDATE/DELETE avvises av trigger uansett hvem som spør.
-- tenant er del av indeksen fra første migrasjon (ADR-001 krav 4).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS revisjonslogg (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    tenant          TEXT,
    aktor           TEXT,
    kilde           TEXT,
    input_hash      TEXT NOT NULL,
    policy_id       TEXT NOT NULL,
    bransjemal      TEXT,
    mal_status      TEXT,
    schema_version  TEXT,
    beslutning      TEXT NOT NULL
                    CHECK (beslutning IN ('TILLAT', 'STOPP', 'UNNTAK')),
    unntak_kategori TEXT,
    effekt          TEXT,
    begrunnelse     JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS revisjonslogg_tenant_ts
    ON revisjonslogg (tenant, ts);
CREATE INDEX IF NOT EXISTS revisjonslogg_input_hash
    ON revisjonslogg (input_hash);

CREATE OR REPLACE FUNCTION revisjonslogg_er_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'revisjonslogg er append-only: % er forbudt (M-2/ADR-001)', TG_OP;
END $$;

DROP TRIGGER IF EXISTS revisjonslogg_ingen_endring ON revisjonslogg;
CREATE TRIGGER revisjonslogg_ingen_endring
    BEFORE UPDATE OR DELETE ON revisjonslogg
    FOR EACH ROW EXECUTE FUNCTION revisjonslogg_er_append_only();

DROP TRIGGER IF EXISTS revisjonslogg_ingen_truncate ON revisjonslogg;
CREATE TRIGGER revisjonslogg_ingen_truncate
    BEFORE TRUNCATE ON revisjonslogg
    FOR EACH STATEMENT EXECUTE FUNCTION revisjonslogg_er_append_only();

-- ------------------------------------------------------------
-- Frekvenshendelser. reserver() teller og skriver i SAMME
-- transaksjon under pg_advisory_xact_lock på nøkkelen — to
-- samtidige forespørsler kan aldri begge få siste plass.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS frekvens_hendelser (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant      TEXT NOT NULL,
    handling    TEXT NOT NULL,
    nokkel_felt TEXT NOT NULL,
    gruppe      TEXT NOT NULL,
    tidspunkt   TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS frekvens_oppslagsindeks
    ON frekvens_hendelser (tenant, handling, nokkel_felt, gruppe, tidspunkt);

-- Migrasjonsbokføring
CREATE TABLE IF NOT EXISTS migrasjoner (
    versjon  INT PRIMARY KEY,
    kjort_ts TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO migrasjoner (versjon) VALUES (1) ON CONFLICT DO NOTHING;

COMMIT;
