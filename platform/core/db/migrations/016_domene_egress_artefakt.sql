-- ============================================================
-- 016 — Domenekontroll · egress · artefakt (PR-014b): plattforminfrastruktur
-- alle senere eiermoduler arver. INGEN WCAG-spesifikk logikk.
--
-- NUMMERERING: klarsignalet skrev «migrasjon 014», men 014 (modulregister) OG
-- 015 (claim-binding) er alt tatt av 014a (PR #29, i review). Å skrive dette til
-- 014 ville korrumpert den migrasjonen. Denne er derfor 016 — additiv, rører
-- ingen kolonne/constraint på tidligere tabeller.
--
-- SKJEMA-TILPASNINGER mot den FAKTISKE 014a-kjeden (klarsignalets DDL var mot et
-- idealisert skjema):
--   * `oppdrag` har PK `id BIGINT` (ikke `oppdrag_id UUID`). `artefakt.oppdrag_id`
--     er derfor BIGINT med kompositt-FK (tenant, oppdrag_id) → oppdrag(tenant, id).
--   * `dek_ref` er `tenant_nokler.key_id` (kompositt-FK (tenant, dek_ref)).
--   * `v_domeneautorisasjon` er `security_invoker=true` (RLS gjelder egress), og
--     egress får KOLONNE-SELECT på nøyaktig visningens kolonner — aldri
--     `challenge_token_hash`. RLS+FORCE er det som faktisk innkapsler egress.
-- ============================================================

-- ------------------------------------------------------------
-- 1. domenekontroll — bevis på DNS-sonekontroll på et tidspunkt (ikke eierskap).
--    status/autorisasjonsgenerasjon endres KUN via §2-funksjonene.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS domenekontroll (
    tenant                        TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    hostname                      TEXT NOT NULL,   -- IDNA2008 A-label, lowercase, uten avsluttende punktum
    status                        TEXT NOT NULL DEFAULT 'ventende'
        CHECK (status IN ('ventende','verifisert','avklaring_kreves','utlopt','tilbakekalt')),
    wildcard                      BOOLEAN NOT NULL DEFAULT false,
    autorisasjonsgenerasjon       BIGINT NOT NULL DEFAULT 0,   -- monoton (§3 B1)
    challenge_token_hash          TEXT,            -- sha256; klartekst vises ÉN gang, lagres aldri
    challenge_utstedt             TIMESTAMPTZ,
    challenge_utloper             TIMESTAMPTZ,     -- 7 døgn
    verifisert_ts                 TIMESTAMPTZ,
    siste_vellykkede_revalidering TIMESTAMPTZ,
    utloper                       TIMESTAMPTZ,     -- verifisert_ts + 90 døgn
    opprettet                     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, hostname)
);
-- Kun ÉN verifisert eier per hostname på tvers av tenanter (§3 B2/B4).
CREATE UNIQUE INDEX IF NOT EXISTS en_verifisert_per_hostname
    ON domenekontroll (hostname) WHERE status = 'verifisert';

-- ------------------------------------------------------------
-- 2. domenekontroll_hendelse — append-only revisjon av alle overganger + grunn.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS domenekontroll_hendelse (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant                  TEXT NOT NULL,
    hostname                TEXT NOT NULL,
    hendelse                TEXT NOT NULL,
    fra_status              TEXT,
    til_status              TEXT,
    autorisasjonsgenerasjon BIGINT,
    grunn                   TEXT,
    aktor                   TEXT NOT NULL,
    detalj                  JSONB,
    ts                      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS domenekontroll_hendelse_host
    ON domenekontroll_hendelse (tenant, hostname, id);

-- ------------------------------------------------------------
-- 3. hostname_binding — global serialiseringsautoritet (§3 B2). INGEN RLS,
--    INGEN runtime-SELECT. Taper nummer to observerer COMMITTET tilstand her,
--    ikke en unique violation.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hostname_binding (
    hostname   TEXT PRIMARY KEY,
    tenant     TEXT NOT NULL,
    bundet_ts  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 4. artefakttype_register — global, bundet til modulkontrakten (014a).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artefakttype_register (
    artefakttype     TEXT PRIMARY KEY CHECK (length(btrim(artefakttype)) > 0),
    eiermodul        TEXT NOT NULL,
    kontraktversjon  INT  NOT NULL,
    kontrakt_hash    TEXT NOT NULL,
    skjema_hash      TEXT NOT NULL,
    opprettet        TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (eiermodul, kontraktversjon, kontrakt_hash)
        REFERENCES modulkontrakt (modul_id, kontraktversjon, kontrakt_hash)
);

-- ------------------------------------------------------------
-- 5. artefakt — tenant-scopet, RLS+FORCE. `tilstand` endres KUN via §2.
--    `klartekst_sha256` er SERVERBEREGNET (JCS). `ciphertext` nulles ved forkastet.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artefakt (
    artefakt_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant           TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    oppdrag_id       BIGINT NOT NULL,
    artefakttype     TEXT NOT NULL REFERENCES artefakttype_register (artefakttype),
    modul_id         TEXT NOT NULL,
    release_id       TEXT NOT NULL,
    kontraktversjon  INT  NOT NULL,
    kontrakt_hash    TEXT NOT NULL,
    module_epoch     BIGINT NOT NULL,
    tilstand         TEXT NOT NULL DEFAULT 'staged'
        CHECK (tilstand IN ('staged','promotert','forkastet')),
    storrelse_bytes  INT NOT NULL
        CHECK (storrelse_bytes > 0 AND storrelse_bytes <= 1048576),   -- 1 MiB (v1)
    klartekst_sha256 TEXT NOT NULL,
    ciphertext       BYTEA,                 -- nullbar: nulles ved 'forkastet'
    dek_ref          TEXT NOT NULL,
    kapabilitet_jti  TEXT NOT NULL UNIQUE,
    opprettet        TIMESTAMPTZ NOT NULL DEFAULT now(),
    promotert_ts     TIMESTAMPTZ,
    CONSTRAINT artefakt_oppdrag_fk
        FOREIGN KEY (tenant, oppdrag_id) REFERENCES oppdrag (tenant, id),
    CONSTRAINT artefakt_dek_fk
        FOREIGN KEY (tenant, dek_ref) REFERENCES tenant_nokler (tenant, key_id),
    CONSTRAINT artefakt_kontrakt_fk
        FOREIGN KEY (modul_id, kontraktversjon, kontrakt_hash)
        REFERENCES modulkontrakt (modul_id, kontraktversjon, kontrakt_hash)
);
-- Nøyaktig ÉN promotert artefakt per (oppdrag, artefakttype).
CREATE UNIQUE INDEX IF NOT EXISTS ett_promotert_per_oppdrag
    ON artefakt (oppdrag_id, artefakttype) WHERE tilstand = 'promotert';
CREATE INDEX IF NOT EXISTS artefakt_staged_opprydding
    ON artefakt (opprettet) WHERE tilstand = 'staged';

-- ------------------------------------------------------------
-- 6. v_domeneautorisasjon — eneste flate egress-proxyen ser (§3 B1).
--    security_invoker=true: RLS gjelder egress (uten det ville visningen kjørt
--    med eierens rettigheter og omgått RLS → proxyen sett alle tenanters rader).
--    `sett_kontekst` MÅ kjøres først også fra proxyen (invariant 7).
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW v_domeneautorisasjon WITH (security_invoker = true) AS
    SELECT tenant, hostname, autorisasjonsgenerasjon,
           (status = 'verifisert' AND now() < utloper
            AND siste_vellykkede_revalidering > now() - interval '72 hours')
           AS gyldig
      FROM domenekontroll;

-- ============================================================
-- Integritetstriggere
-- ============================================================

-- 7a. Append-only: domenekontroll_hendelse + artefakttype_register.
CREATE OR REPLACE FUNCTION domene_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% er append-only (immutable): % er forbudt',
        TG_TABLE_NAME, TG_OP;
END $$;
DROP TRIGGER IF EXISTS hendelse_append_only ON domenekontroll_hendelse;
CREATE TRIGGER hendelse_append_only BEFORE UPDATE OR DELETE ON domenekontroll_hendelse
    FOR EACH ROW EXECUTE FUNCTION domene_append_only();
DROP TRIGGER IF EXISTS artefakttype_immutable ON artefakttype_register;
CREATE TRIGGER artefakttype_immutable BEFORE UPDATE OR DELETE ON artefakttype_register
    FOR EACH ROW EXECUTE FUNCTION domene_append_only();

-- 7b. domenekontroll kolonnelås: identitet frosset, autorisasjonsgenerasjon
--     monoton, status-statemaskin. Selve fullmakten (resolver-enighet, PSL,
--     takeover) håndheves i §2-funksjonene; dette er sikkerhetsnettet mot en
--     ulovlig direkte overgang.
CREATE OR REPLACE FUNCTION domenekontroll_statemaskin()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.hostname IS DISTINCT FROM OLD.hostname THEN
        RAISE EXCEPTION 'domenekontroll: (tenant, hostname) er uforanderlig';
    END IF;
    IF NEW.autorisasjonsgenerasjon < OLD.autorisasjonsgenerasjon THEN
        RAISE EXCEPTION 'domenekontroll: autorisasjonsgenerasjon er monoton (% -> %)',
            OLD.autorisasjonsgenerasjon, NEW.autorisasjonsgenerasjon;
    END IF;
    IF NOT (
        (OLD.status = 'ventende'         AND NEW.status IN ('ventende','verifisert','avklaring_kreves','utlopt','tilbakekalt')) OR
        (OLD.status = 'verifisert'       AND NEW.status IN ('verifisert','avklaring_kreves','utlopt','tilbakekalt')) OR
        (OLD.status = 'avklaring_kreves' AND NEW.status IN ('avklaring_kreves','verifisert','tilbakekalt','utlopt')) OR
        (OLD.status = 'utlopt'           AND NEW.status IN ('utlopt','ventende','verifisert','tilbakekalt')) OR
        (OLD.status = 'tilbakekalt'      AND NEW.status IN ('tilbakekalt','ventende','verifisert')) OR
        (OLD.status = NEW.status)
    ) THEN
        RAISE EXCEPTION 'domenekontroll: ulovlig statusovergang % -> %',
            OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS domenekontroll_laas ON domenekontroll;
CREATE TRIGGER domenekontroll_laas BEFORE UPDATE ON domenekontroll
    FOR EACH ROW EXECUTE FUNCTION domenekontroll_statemaskin();

-- 7c. artefakt kolonnelås: identitet/binding frosset; tilstand fremover-only
--     staged→{promotert,forkastet} (begge terminale); ciphertext kan KUN nulles
--     (ved forkastet), aldri endres til noe annet.
CREATE OR REPLACE FUNCTION artefakt_statemaskin()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.artefakt_id  IS DISTINCT FROM OLD.artefakt_id
       OR NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.oppdrag_id IS DISTINCT FROM OLD.oppdrag_id
       OR NEW.artefakttype IS DISTINCT FROM OLD.artefakttype
       OR NEW.modul_id IS DISTINCT FROM OLD.modul_id
       OR NEW.release_id IS DISTINCT FROM OLD.release_id
       OR NEW.kontraktversjon IS DISTINCT FROM OLD.kontraktversjon
       OR NEW.kontrakt_hash IS DISTINCT FROM OLD.kontrakt_hash
       OR NEW.module_epoch IS DISTINCT FROM OLD.module_epoch
       OR NEW.klartekst_sha256 IS DISTINCT FROM OLD.klartekst_sha256
       OR NEW.kapabilitet_jti IS DISTINCT FROM OLD.kapabilitet_jti
       OR NEW.storrelse_bytes IS DISTINCT FROM OLD.storrelse_bytes THEN
        RAISE EXCEPTION 'artefakt: identitet/binding/hash er frosset';
    END IF;
    IF NOT (
        (OLD.tilstand = 'staged' AND NEW.tilstand IN ('staged','promotert','forkastet')) OR
        (OLD.tilstand = NEW.tilstand)
    ) THEN
        RAISE EXCEPTION 'artefakt: ulovlig tilstandsovergang % -> %',
            OLD.tilstand, NEW.tilstand;
    END IF;
    IF OLD.tilstand IN ('promotert','forkastet') AND NEW.tilstand <> OLD.tilstand THEN
        RAISE EXCEPTION 'artefakt: % er terminal', OLD.tilstand;
    END IF;
    -- ciphertext kan bare bli NULL (forkastet), aldri endres til annet innhold.
    IF NEW.ciphertext IS DISTINCT FROM OLD.ciphertext AND NEW.ciphertext IS NOT NULL THEN
        RAISE EXCEPTION 'artefakt: ciphertext kan kun nulles, aldri endres';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS artefakt_laas ON artefakt;
CREATE TRIGGER artefakt_laas BEFORE UPDATE ON artefakt
    FOR EACH ROW EXECUTE FUNCTION artefakt_statemaskin();

-- 7d. Ingen sletting av domenekontroll/artefakt/hostname_binding (evidens/binding).
CREATE OR REPLACE FUNCTION domene_ingen_sletting()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '%: sletting/truncate er forbudt (evidens/binding)', TG_TABLE_NAME;
END $$;
DROP TRIGGER IF EXISTS domenekontroll_ingen_delete ON domenekontroll;
CREATE TRIGGER domenekontroll_ingen_delete BEFORE DELETE ON domenekontroll
    FOR EACH ROW EXECUTE FUNCTION domene_ingen_sletting();
DROP TRIGGER IF EXISTS hostname_binding_ingen_delete ON hostname_binding;
CREATE TRIGGER hostname_binding_ingen_delete BEFORE DELETE ON hostname_binding
    FOR EACH ROW EXECUTE FUNCTION domene_ingen_sletting();

-- ============================================================
-- RLS + FORCE (tenant-isolasjon) på domenekontroll, domenekontroll_hendelse,
-- artefakt. hostname_binding og artefakttype_register er GLOBALE (ingen RLS).
-- ============================================================
ALTER TABLE domenekontroll           ENABLE ROW LEVEL SECURITY;
ALTER TABLE domenekontroll           FORCE  ROW LEVEL SECURITY;
ALTER TABLE domenekontroll_hendelse  ENABLE ROW LEVEL SECURITY;
ALTER TABLE domenekontroll_hendelse  FORCE  ROW LEVEL SECURITY;
ALTER TABLE artefakt                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE artefakt                 FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolasjon ON domenekontroll;
CREATE POLICY tenant_isolasjon ON domenekontroll
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
DROP POLICY IF EXISTS tenant_isolasjon ON domenekontroll_hendelse;
CREATE POLICY tenant_isolasjon ON domenekontroll_hendelse
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
DROP POLICY IF EXISTS tenant_isolasjon ON artefakt;
CREATE POLICY tenant_isolasjon ON artefakt
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- ============================================================
-- GRANT-modell (default-deny). Funksjonene i §2 (neste seksjon) er den ENESTE
-- skriveveien; her gis lesetilgangene.
-- ============================================================
-- Runtime: SELECT på domenekontroll, artefakt, artefakttype_register (INGEN
-- skriv, INGEN SELECT på hostname_binding) gis i migrer.py RETTIGHETER — der
-- runtime-grantsettet er autoritativt og overlever REVOKE-ALL-syklusen (samme
-- mønster som 014a-registeret; en løs GRANT her ville blitt vasket bort).

-- Egress: KUN visningen. security_invoker krever SELECT på visningens
-- basiskolonner — gitt som KOLONNE-grant (aldri challenge_token_hash), og RLS+
-- FORCE innkapsler egress til gjeldende tenant-kontekst.
GRANT SELECT ON v_domeneautorisasjon TO disponit_egress;
GRANT SELECT (tenant, hostname, autorisasjonsgenerasjon, status, utloper,
              siste_vellykkede_revalidering)
    ON domenekontroll TO disponit_egress;
