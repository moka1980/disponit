-- ============================================================
-- 014 — Modulregister (PR-014a CP1): autoritativ, versjonert registerdatamodell
--
-- MERK NUMMERERING: klarsignalet skrev «migrasjon 013», men 013 er ALLEREDE
-- merget+deployet (policyadmin `aktiver_policy`) og 012 er policyadmin. Å skrive
-- registeret til 013 ville korrumpert den migrasjonen. Registeret er derfor 014.
--
-- Python-state kan ikke låses på tvers av prosessbilder → registeret er
-- DB-autoritativt. Denne migrasjonen (CP1) legger DATAMODELLEN + integritets-
-- triggere: seks tabeller, immutabilitet (`modulkontrakt`/`modulrelease` tåler
-- ingen UPDATE/DELETE), statemaskiner for `modulhode.status` og
-- `moduldeployment.livslop`, og delindeksen som håndhever ÉN `claiming`-
-- deployment per (modul, miljø, kontrakt). De herdede overgangsfunksjonene, den
-- kontrolerte GRANT-modellen og eier-rollen kommer i CP2 — som `aktiver_policy`
-- kom etter policyadmin-skjemaet.
--
-- Registertabellene er GLOBALE (ikke tenant-scopet): oppdragstyper er globalt
-- unike, og en modulkontrakt er felles på tvers av kunder. Derfor INGEN RLS.
-- ============================================================

-- ------------------------------------------------------------
-- 1. modulkontrakt — hva modulen LOVER (immutable). En (modul, kontraktversjon)
--    har nøyaktig én hash; hashen binder payload-/kvitteringsskjema +
--    sideeffekt-/reversibilitetsklasse. Kompositt-UNIQUE er FK-mål for release.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS modulkontrakt (
    modul_id                TEXT NOT NULL CHECK (length(btrim(modul_id)) > 0),
    kontraktversjon         INT  NOT NULL CHECK (kontraktversjon >= 1),
    kontrakt_hash           TEXT NOT NULL,
    payload_schema_hash     TEXT NOT NULL,
    kvittering_schema_hash  TEXT NOT NULL,
    sideeffektklasse        TEXT NOT NULL
        CHECK (sideeffektklasse IN ('sideeffektfri', 'krever_outbox')),
    reversibilitet          TEXT NOT NULL
        CHECK (reversibilitet IN ('direkte', 'kompenserende', 'irreversibel')),
    opprettet               TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (modul_id, kontraktversjon),
    UNIQUE (modul_id, kontraktversjon, kontrakt_hash)
);

-- ------------------------------------------------------------
-- 2. modulhode — modulens tilstand. `status` og `module_epoch` endres KUN via
--    overgangsfunksjonene (CP2); statemaskinen + kolonnelåsen her avviser alt
--    annet. `module_epoch` er per-modul fencing-generasjon (nød/reaktivering).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS modulhode (
    modul_id       TEXT PRIMARY KEY CHECK (length(btrim(modul_id)) > 0),
    status         TEXT NOT NULL DEFAULT 'installert'
        CHECK (status IN ('installert', 'staging_verifisert', 'aktiv',
                          'nodeaktivert')),
    modulrevisjon  BIGINT NOT NULL DEFAULT 0,   -- monoton, +1 ved hver overgang
    module_epoch   BIGINT NOT NULL DEFAULT 0,   -- +1 ved nød/reaktivering
    status_ts      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 3. modulrelease — immutable, flere samtidig. En release binder én kontrakt
--    (kompositt-FK) + manifest + artifact_digest. Beholdes som revisjonsevidens
--    så lenge claims/kvitteringer/logger refererer den (v5 §3).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS modulrelease (
    modul_id         TEXT NOT NULL,
    release_id       TEXT NOT NULL,
    kontraktversjon  INT  NOT NULL,
    kontrakt_hash    TEXT NOT NULL,
    manifest_hash    TEXT NOT NULL,
    artifact_digest  TEXT NOT NULL,
    opprettet        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (modul_id, release_id),
    UNIQUE (modul_id, release_id, kontraktversjon, kontrakt_hash),
    FOREIGN KEY (modul_id, kontraktversjon, kontrakt_hash)
        REFERENCES modulkontrakt (modul_id, kontraktversjon, kontrakt_hash)
);

-- ------------------------------------------------------------
-- 4. moduldeployment — hva som faktisk kjører. `livslop` (claiming→draining→
--    retired) endres KUN via funksjonene. Delindeksen håndhever ÉN claiming per
--    (modul, miljø, kontrakt); draining/retired kan være mange. Kompositt-FK
--    (v5 §1) så indekslogikken slipper join.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS moduldeployment (
    modul_id         TEXT NOT NULL,
    release_id       TEXT NOT NULL,
    kontraktversjon  INT  NOT NULL,
    kontrakt_hash    TEXT NOT NULL,
    miljo            TEXT NOT NULL CHECK (miljo IN ('staging', 'produksjon')),
    livslop          TEXT NOT NULL DEFAULT 'claiming'
        CHECK (livslop IN ('claiming', 'draining', 'retired')),
    fra_ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (modul_id, miljo, release_id),
    FOREIGN KEY (modul_id, release_id, kontraktversjon, kontrakt_hash)
        REFERENCES modulrelease (modul_id, release_id, kontraktversjon,
                                 kontrakt_hash)
);
CREATE UNIQUE INDEX IF NOT EXISTS en_claiming_per_kontrakt ON moduldeployment
    (modul_id, miljo, kontraktversjon, kontrakt_hash) WHERE livslop = 'claiming';

-- ------------------------------------------------------------
-- 5. oppdragstype_register — globalt unike oppdragstyper. Én oppdragstype eies
--    av nøyaktig én modulkontrakt (kompositt-FK). Prefiks-overlapp håndheves av
--    `registrer_oppdragstype` (CP2, global advisory-lås).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS oppdragstype_register (
    oppdragstype     TEXT PRIMARY KEY CHECK (length(btrim(oppdragstype)) > 0),
    eiermodul        TEXT NOT NULL,
    kontraktversjon  INT  NOT NULL,
    kontrakt_hash    TEXT NOT NULL,
    opprettet        TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (eiermodul, kontraktversjon, kontrakt_hash)
        REFERENCES modulkontrakt (modul_id, kontraktversjon, kontrakt_hash)
);

-- ------------------------------------------------------------
-- 6. modulregister_hendelse — append-only revisjon av ALLE overganger.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS modulregister_hendelse (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    modul_id         TEXT NOT NULL,
    hendelse         TEXT NOT NULL,
    fra_status       TEXT,
    til_status       TEXT,
    fra_livslop      TEXT,
    til_livslop      TEXT,
    release_id       TEXT,
    kontraktversjon  INT,
    kontrakt_hash    TEXT,
    module_epoch     BIGINT,
    aktor            TEXT NOT NULL,
    begrunnelse      TEXT,
    detalj           JSONB,
    ts               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS modulregister_hendelse_modul
    ON modulregister_hendelse (modul_id, id);

-- ============================================================
-- Integritetstriggere
-- ============================================================

-- 7a. modulkontrakt + modulrelease + oppdragstype_register er IMMUTABLE:
--     kontrakten er hva modulen LOVER, releasen er revisjonsevidens, og en
--     oppdragstype-eier kan ikke stille endres. Ingen UPDATE/DELETE.
CREATE OR REPLACE FUNCTION modulregister_append_only()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% er append-only (immutable): % er forbudt',
        TG_TABLE_NAME, TG_OP;
END $$;

DROP TRIGGER IF EXISTS kontrakt_immutable ON modulkontrakt;
CREATE TRIGGER kontrakt_immutable BEFORE UPDATE OR DELETE ON modulkontrakt
    FOR EACH ROW EXECUTE FUNCTION modulregister_append_only();
DROP TRIGGER IF EXISTS release_immutable ON modulrelease;
CREATE TRIGGER release_immutable BEFORE UPDATE OR DELETE ON modulrelease
    FOR EACH ROW EXECUTE FUNCTION modulregister_append_only();
DROP TRIGGER IF EXISTS oppdragstype_immutable ON oppdragstype_register;
CREATE TRIGGER oppdragstype_immutable BEFORE UPDATE OR DELETE ON oppdragstype_register
    FOR EACH ROW EXECUTE FUNCTION modulregister_append_only();
DROP TRIGGER IF EXISTS hendelse_append_only ON modulregister_hendelse;
CREATE TRIGGER hendelse_append_only BEFORE UPDATE OR DELETE ON modulregister_hendelse
    FOR EACH ROW EXECUTE FUNCTION modulregister_append_only();

-- 7b. modulhode statemaskin + kolonnelås. `modul_id` uforanderlig; status følger
--     installert→staging_verifisert→aktiv (fremover) · enhver → nodeaktivert
--     (nød) · nodeaktivert → staging_verifisert/aktiv (reaktivering, ny evidens
--     verifiseres i funksjonen). `modulrevisjon` og `module_epoch` er monotone.
--     Selve fullmakten (evidens, ≥1 claiming osv.) håndheves i funksjonene;
--     dette er sikkerhetsnettet mot en ulovlig direkte overgang.
CREATE OR REPLACE FUNCTION modulhode_statemaskin()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.modul_id IS DISTINCT FROM OLD.modul_id THEN
        RAISE EXCEPTION 'modulhode: modul_id er uforanderlig';
    END IF;
    IF NOT (
        (OLD.status = 'installert'          AND NEW.status IN ('installert','staging_verifisert','nodeaktivert')) OR
        (OLD.status = 'staging_verifisert'  AND NEW.status IN ('staging_verifisert','aktiv','nodeaktivert')) OR
        (OLD.status = 'aktiv'               AND NEW.status IN ('aktiv','nodeaktivert')) OR
        (OLD.status = 'nodeaktivert'        AND NEW.status IN ('nodeaktivert','staging_verifisert','aktiv')) OR
        (OLD.status = NEW.status)
    ) THEN
        RAISE EXCEPTION 'modulhode: ulovlig statusovergang % -> %',
            OLD.status, NEW.status;
    END IF;
    IF NEW.modulrevisjon < OLD.modulrevisjon THEN
        RAISE EXCEPTION 'modulhode: modulrevisjon er monoton (% -> %)',
            OLD.modulrevisjon, NEW.modulrevisjon;
    END IF;
    IF NEW.module_epoch < OLD.module_epoch THEN
        RAISE EXCEPTION 'modulhode: module_epoch er monoton (% -> %)',
            OLD.module_epoch, NEW.module_epoch;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS hode_statemaskin ON modulhode;
CREATE TRIGGER hode_statemaskin BEFORE UPDATE ON modulhode
    FOR EACH ROW EXECUTE FUNCTION modulhode_statemaskin();

-- 7c. moduldeployment: livslop er fremover-only claiming→draining→retired;
--     kontrakt-/release-identiteten er frosset. `retired` er terminal.
CREATE OR REPLACE FUNCTION moduldeployment_livslop()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.release_id IS DISTINCT FROM OLD.release_id
       OR NEW.kontraktversjon IS DISTINCT FROM OLD.kontraktversjon
       OR NEW.kontrakt_hash IS DISTINCT FROM OLD.kontrakt_hash
       OR NEW.miljo IS DISTINCT FROM OLD.miljo THEN
        RAISE EXCEPTION 'moduldeployment: identitet/kontrakt er frosset';
    END IF;
    IF NOT (
        (OLD.livslop = 'claiming' AND NEW.livslop IN ('claiming','draining')) OR
        (OLD.livslop = 'draining' AND NEW.livslop IN ('draining','retired')) OR
        (OLD.livslop = NEW.livslop)
    ) THEN
        RAISE EXCEPTION 'moduldeployment: ulovlig livsløpsovergang % -> %',
            OLD.livslop, NEW.livslop;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS deployment_livslop ON moduldeployment;
CREATE TRIGGER deployment_livslop BEFORE UPDATE ON moduldeployment
    FOR EACH ROW EXECUTE FUNCTION moduldeployment_livslop();

-- ============================================================
-- CP2: Herdede overgangsfunksjoner (SECURITY DEFINER, eid av NOLOGIN-rollen
-- `disponit_modul_eier`, search_path=pg_catalog). Runtime har KUN SELECT på
-- registertabellene (§4) — ALL skriving går gjennom disse. EXECUTE gis til
-- `disponit_modules_admin` (overgangene er admin-/deploy-handlinger, ikke
-- runtime). Den maskinverifiserte evidensen (åpne artefakt, sha256, KRAVGRENSER)
-- ligger i Python-orkestreringen som kaller `sett_modulstatus` — som
-- `aktiver_policy`; her håndheves det DB-en KAN: statemaskin, ≥1 claiming,
-- global namespace-lås, prefiks-overlapp, og append-only revisjon.
-- ============================================================
SET LOCAL ROLE disponit_modul_eier;

-- Onboarding: opprett modulhodet (status 'installert'). Idempotent.
CREATE OR REPLACE FUNCTION installer_modul(p_modul_id TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    INSERT INTO public.modulhode (modul_id, status)
        VALUES (p_modul_id, 'installert')
        ON CONFLICT (modul_id) DO NOTHING;
    INSERT INTO public.modulregister_hendelse (modul_id, hendelse, til_status, aktor)
        VALUES (p_modul_id, 'installert', 'installert', p_aktor);
END $$;

-- Globalt unike, ikke-overlappende oppdragstyper. Global advisory-lås så to
-- samtidige overlappende registreringer serialiseres (port 18); prefiks-overlapp
-- avvises (ingen type får være prefiks av en annen — kollisjon i dispatch).
CREATE OR REPLACE FUNCTION registrer_oppdragstype(
    p_oppdragstype   TEXT,
    p_eiermodul      TEXT,
    p_kontraktversjon INT,
    p_kontrakt_hash  TEXT,
    p_aktor          TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_konflikt TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended('modulregister:oppdragstype', 0));
    SELECT oppdragstype INTO v_konflikt FROM public.oppdragstype_register
     WHERE starts_with(p_oppdragstype, oppdragstype)
        OR starts_with(oppdragstype, p_oppdragstype)
     LIMIT 1;
    IF v_konflikt IS NOT NULL THEN
        RAISE EXCEPTION 'oppdragstype % overlapper eksisterende %',
            p_oppdragstype, v_konflikt USING ERRCODE = 'unique_violation';
    END IF;
    INSERT INTO public.oppdragstype_register
        (oppdragstype, eiermodul, kontraktversjon, kontrakt_hash)
        VALUES (p_oppdragstype, p_eiermodul, p_kontraktversjon, p_kontrakt_hash);
    INSERT INTO public.modulregister_hendelse
        (modul_id, hendelse, kontraktversjon, kontrakt_hash, aktor, detalj)
        VALUES (p_eiermodul, 'oppdragstype_registrert', p_kontraktversjon,
                p_kontrakt_hash, p_aktor,
                jsonb_build_object('oppdragstype', p_oppdragstype));
END $$;

-- Statusovergang. Statemaskin-triggeren vokter lovlig overgang; her legges den
-- domene-invarianten DB-en eier: `aktiv` KREVER ≥1 claiming-deployment (port 13).
-- Evidensverifiseringen (artefakt/sha256/KRAVGRENSER) gjøres av Python-kalleren
-- FØR dette kallet (port 14 håndheves der).
CREATE OR REPLACE FUNCTION sett_modulstatus(
    p_modul_id   TEXT,
    p_ny_status  TEXT,
    p_release_id TEXT,
    p_aktor      TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_gammel TEXT; v_claiming INT;
BEGIN
    SELECT status INTO v_gammel FROM public.modulhode
     WHERE modul_id = p_modul_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'sett_modulstatus: ukjent modul %', p_modul_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF p_ny_status = 'aktiv' THEN
        SELECT count(*) INTO v_claiming FROM public.moduldeployment
         WHERE modul_id = p_modul_id AND livslop = 'claiming';
        IF v_claiming < 1 THEN
            RAISE EXCEPTION 'modul % kan ikke bli aktiv uten claiming-deployment',
                p_modul_id USING ERRCODE = 'invalid_parameter_value';
        END IF;
    END IF;
    UPDATE public.modulhode
       SET status = p_ny_status, modulrevisjon = modulrevisjon + 1,
           status_ts = now()
     WHERE modul_id = p_modul_id;      -- statemaskin-trigger vokter overgangen
    INSERT INTO public.modulregister_hendelse
        (modul_id, hendelse, fra_status, til_status, release_id, aktor)
        VALUES (p_modul_id, 'statusovergang', v_gammel, p_ny_status,
                p_release_id, p_aktor);
END $$;

REVOKE ALL ON FUNCTION installer_modul(TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION registrer_oppdragstype(TEXT, TEXT, INT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION sett_modulstatus(TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION installer_modul(TEXT, TEXT) TO disponit_modules_admin;
GRANT EXECUTE ON FUNCTION registrer_oppdragstype(TEXT, TEXT, INT, TEXT, TEXT) TO disponit_modules_admin;
GRANT EXECUTE ON FUNCTION sett_modulstatus(TEXT, TEXT, TEXT, TEXT) TO disponit_modules_admin;
RESET ROLE;

-- Eieren (modul_eier) må kunne SKRIVE registertabellene når funksjonene kjører
-- (SECURITY DEFINER kjører som eier). Grantene bor HER, sammen med funksjonene
-- (PR-013-lærdom: løs migrer.py-kode overlever ikke skjema-gjenoppbygging).
-- Kjøres som migrator (eier av tabellene) etter RESET ROLE.
GRANT SELECT, INSERT         ON modulkontrakt          TO disponit_modul_eier;
GRANT SELECT, INSERT, UPDATE ON modulhode              TO disponit_modul_eier;
GRANT SELECT, INSERT         ON modulrelease           TO disponit_modul_eier;
GRANT SELECT, INSERT, UPDATE ON moduldeployment        TO disponit_modul_eier;
GRANT SELECT, INSERT         ON oppdragstype_register   TO disponit_modul_eier;
GRANT SELECT, INSERT         ON modulregister_hendelse  TO disponit_modul_eier;

-- ============================================================
-- CP3: Release-livsløpsfunksjoner (samme herdemodell som CP2). Registeret har
-- ÉN vei inn for skriving — kontrakt/release registreres herdet, releasebytte er
-- ATOMISK under kontraktlås (V1), og pensjonering er en idempotent
-- livsløpsovergang. `registrer_kontrakt`/`registrer_release` er
-- immutabilitets-idempotente: samme identitet + samme innhold = no-op, samme
-- identitet + avvikende innhold = avvist (kontrakten/releasen LOVER én ting).
-- ============================================================
SET LOCAL ROLE disponit_modul_eier;

-- Herdet kontraktregistrering. En (modul, kontraktversjon) har nøyaktig én hash;
-- re-registrering med samme hash er no-op, med avvikende hash avvist (immutable).
CREATE OR REPLACE FUNCTION registrer_kontrakt(
    p_modul_id              TEXT,
    p_kontraktversjon       INT,
    p_kontrakt_hash         TEXT,
    p_payload_schema_hash   TEXT,
    p_kvittering_schema_hash TEXT,
    p_sideeffektklasse      TEXT,
    p_reversibilitet        TEXT,
    p_aktor                 TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_hash TEXT;
BEGIN
    SELECT kontrakt_hash INTO v_hash FROM public.modulkontrakt
     WHERE modul_id = p_modul_id AND kontraktversjon = p_kontraktversjon;
    IF FOUND THEN
        IF v_hash IS DISTINCT FROM p_kontrakt_hash THEN
            RAISE EXCEPTION 'kontrakt (%,%) er immutable: % != %',
                p_modul_id, p_kontraktversjon, v_hash, p_kontrakt_hash
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN;                                   -- idempotent
    END IF;
    INSERT INTO public.modulkontrakt (modul_id, kontraktversjon, kontrakt_hash,
        payload_schema_hash, kvittering_schema_hash, sideeffektklasse,
        reversibilitet)
        VALUES (p_modul_id, p_kontraktversjon, p_kontrakt_hash,
                p_payload_schema_hash, p_kvittering_schema_hash,
                p_sideeffektklasse, p_reversibilitet);
    INSERT INTO public.modulregister_hendelse
        (modul_id, hendelse, kontraktversjon, kontrakt_hash, aktor)
        VALUES (p_modul_id, 'kontrakt_registrert', p_kontraktversjon,
                p_kontrakt_hash, p_aktor);
END $$;

-- Herdet releaseregistrering. FK avviser en release mot en kontrakt som ikke
-- finnes/avviker (port 1). Immutabilitets-idempotent på hele innholdet.
CREATE OR REPLACE FUNCTION registrer_release(
    p_modul_id        TEXT,
    p_release_id      TEXT,
    p_kontraktversjon INT,
    p_kontrakt_hash   TEXT,
    p_manifest_hash   TEXT,
    p_artifact_digest TEXT,
    p_aktor           TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE r RECORD;
BEGIN
    SELECT kontraktversjon, kontrakt_hash, manifest_hash, artifact_digest
      INTO r FROM public.modulrelease
     WHERE modul_id = p_modul_id AND release_id = p_release_id;
    IF FOUND THEN
        IF (r.kontraktversjon, r.kontrakt_hash, r.manifest_hash, r.artifact_digest)
           IS DISTINCT FROM
           (p_kontraktversjon, p_kontrakt_hash, p_manifest_hash, p_artifact_digest) THEN
            RAISE EXCEPTION 'release (%,%) er immutable', p_modul_id, p_release_id
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN;                                   -- idempotent
    END IF;
    INSERT INTO public.modulrelease (modul_id, release_id, kontraktversjon,
        kontrakt_hash, manifest_hash, artifact_digest)
        VALUES (p_modul_id, p_release_id, p_kontraktversjon, p_kontrakt_hash,
                p_manifest_hash, p_artifact_digest);   -- FK: port 1
    INSERT INTO public.modulregister_hendelse
        (modul_id, hendelse, release_id, kontraktversjon, kontrakt_hash, aktor)
        VALUES (p_modul_id, 'release_registrert', p_release_id,
                p_kontraktversjon, p_kontrakt_hash, p_aktor);
END $$;

-- V1: ATOMISK releasebytte. Under kontraktlås (serialiserer samtidige bytter for
-- samme (modul, miljø, kontrakt) → port 3: nøyaktig én ender claiming) settes den
-- gamle claiming-deploymenten `draining` OG den nye `claiming` i ÉN transaksjon.
-- En release som alt er draining/retired kan ALDRI reclaimes (ny release kreves).
CREATE OR REPLACE FUNCTION bytt_release(
    p_modul_id        TEXT,
    p_miljo           TEXT,
    p_ny_release_id   TEXT,
    p_kontraktversjon INT,
    p_kontrakt_hash   TEXT,
    p_aktor           TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_ny_livslop TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'modulregister:bytt:' || p_modul_id || ':' || p_miljo || ':'
        || p_kontraktversjon::text || ':' || p_kontrakt_hash, 0));
    -- Ny release må finnes OG matche kontrakten (kompositt).
    PERFORM 1 FROM public.modulrelease
     WHERE modul_id = p_modul_id AND release_id = p_ny_release_id
       AND kontraktversjon = p_kontraktversjon AND kontrakt_hash = p_kontrakt_hash;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'bytt_release: ukjent/avvikende release %/%',
            p_modul_id, p_ny_release_id USING ERRCODE = 'foreign_key_violation';
    END IF;
    SELECT livslop INTO v_ny_livslop FROM public.moduldeployment
     WHERE modul_id = p_modul_id AND miljo = p_miljo
       AND release_id = p_ny_release_id;
    IF v_ny_livslop = 'claiming' THEN
        RETURN;                                   -- idempotent: alt claiming
    ELSIF v_ny_livslop IN ('draining', 'retired') THEN
        RAISE EXCEPTION 'release %/% er % — kan ikke reclaimes (ny release kreves)',
            p_modul_id, p_ny_release_id, v_ny_livslop
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Gammel claiming for samme kontrakt → draining (partiell indeks frigjøres
    -- før innsettingen av den nye).
    UPDATE public.moduldeployment SET livslop = 'draining'
     WHERE modul_id = p_modul_id AND miljo = p_miljo
       AND kontraktversjon = p_kontraktversjon AND kontrakt_hash = p_kontrakt_hash
       AND livslop = 'claiming';
    INSERT INTO public.moduldeployment (modul_id, release_id, kontraktversjon,
        kontrakt_hash, miljo, livslop)
        VALUES (p_modul_id, p_ny_release_id, p_kontraktversjon, p_kontrakt_hash,
                p_miljo, 'claiming');
    INSERT INTO public.modulregister_hendelse
        (modul_id, hendelse, til_livslop, release_id, kontraktversjon,
         kontrakt_hash, aktor)
        VALUES (p_modul_id, 'releasebytte', 'claiming', p_ny_release_id,
                p_kontraktversjon, p_kontrakt_hash, p_aktor);
END $$;

-- Idempotent pensjonering: draining → retired. `claiming` må draines først
-- (bytt_release), `retired` er no-op. Aktive-claims=0-vilkåret håndheves i CP5
-- når claim-tabellen finnes; her er selve livsløpsovergangen + revisjon.
CREATE OR REPLACE FUNCTION pensjoner_release(
    p_modul_id   TEXT,
    p_miljo      TEXT,
    p_release_id TEXT,
    p_aktor      TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_livslop TEXT;
BEGIN
    SELECT livslop INTO v_livslop FROM public.moduldeployment
     WHERE modul_id = p_modul_id AND miljo = p_miljo
       AND release_id = p_release_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'pensjoner_release: ukjent deployment %/%/%',
            p_modul_id, p_miljo, p_release_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_livslop = 'retired' THEN
        RETURN;                                   -- idempotent
    ELSIF v_livslop = 'claiming' THEN
        RAISE EXCEPTION 'release %/% er claiming — må draines før pensjonering',
            p_modul_id, p_release_id USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.moduldeployment SET livslop = 'retired'
     WHERE modul_id = p_modul_id AND miljo = p_miljo
       AND release_id = p_release_id;
    INSERT INTO public.modulregister_hendelse
        (modul_id, hendelse, fra_livslop, til_livslop, release_id, aktor)
        VALUES (p_modul_id, 'pensjonert', 'draining', 'retired',
                p_release_id, p_aktor);
END $$;

REVOKE ALL ON FUNCTION registrer_kontrakt(TEXT, INT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION registrer_release(TEXT, TEXT, INT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION bytt_release(TEXT, TEXT, TEXT, INT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION pensjoner_release(TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION registrer_kontrakt(TEXT, INT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT) TO disponit_modules_admin;
GRANT EXECUTE ON FUNCTION registrer_release(TEXT, TEXT, INT, TEXT, TEXT, TEXT, TEXT) TO disponit_modules_admin;
GRANT EXECUTE ON FUNCTION bytt_release(TEXT, TEXT, TEXT, INT, TEXT, TEXT) TO disponit_modules_admin;
GRANT EXECUTE ON FUNCTION pensjoner_release(TEXT, TEXT, TEXT, TEXT) TO disponit_modules_admin;
RESET ROLE;
