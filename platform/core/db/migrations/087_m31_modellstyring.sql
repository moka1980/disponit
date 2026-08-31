-- 086 — M-31 v1: golden-sett-porten på release-byttene
--
-- Dommene (ratifisert 31/8): golden-eksemplene bor på DISK, hash-pinnet
-- (biasmaalinger.json-presedensen) — basen bærer kun HODET (payloadfritt,
-- ingen TTL, aldri persondata). Porten gjelder ALLE miljøer: digesten er
-- modellens miljøuavhengige identitet. Kravbindingen er EKSAKT
-- kravversjon (dom 3): en bestått dom mot svakere terskler bærer aldri
-- et bytte under strengere — en innstramming koster én re-kjøring.
-- Model card er en AVLEDET leseflate (aldri lagret, aldri stale).
--
-- Datamodellen er plattformregisterets form (014, GLOBAL/tenant-løs,
-- ingen RLS): append-only med immutabilitetstriggere, ALL skriving
-- gjennom SECURITY DEFINER-dørene (014a-doktrinen), append-only
-- revisjonsstrøm. `evalueringskrav` er det ene unntaket fra ren
-- append-only: raden har et LIVSLØP (gjeldende → historisk), voktet av
-- en statemaskin-trigger som fryser alt annet enn nettopp den
-- overgangen — samme grep som moduldeployment.livslop.
--
-- Porten: `bytt_release` REPLACEs med 014-kroppen KOPIERT byte for byte
-- (085-formen for claim-funksjonene) og diff-endret KUN med portblokken
-- + de to portvariablene i DECLARE. Samme signatur, samme
-- låserekkefølge (modul-lås → kontraktlås), grants består. Modul uten
-- krav-rad er UBERØRT (opt-in, ingen backfill) — fail-closed FØR byttet
-- ER rollback-semantikken; kill-switchen finnes (noddeaktiver_modul).

-- ------------------------------------------------------------
-- 1. golden_sett — HODET til et golden-sett: identitet + innholdshash.
--    Selve eksemplene bor på disk og reiser aldri inn i basen
--    (payloadfritt; persondata er forbudt i settene per dom 1).
--    Kompositt-UNIQUE med hashen er FK-mål: et krav og en kjøring kan
--    bare referere et sett de også navngir INNHOLDET av.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS golden_sett (
    modul_id         TEXT NOT NULL REFERENCES modulhode (modul_id),
    sett_id          TEXT NOT NULL CHECK (length(btrim(sett_id)) > 0),
    versjon          INT  NOT NULL CHECK (versjon > 0),
    innhold_hash     TEXT NOT NULL CHECK (innhold_hash ~ '^[0-9a-f]{64}$'),
    antall_eksempler INT  NOT NULL CHECK (antall_eksempler > 0),
    beskrivelse      TEXT,
    opprettet        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (modul_id, sett_id, versjon),
    UNIQUE (modul_id, sett_id, versjon, innhold_hash)
);

-- ------------------------------------------------------------
-- 2. evalueringskrav — terskelen porten håndhever. Én rad per
--    (modul, kravversjon); nøyaktig ÉN `gjeldende` per modul (partiell
--    unik indeks). `terskel_maks_p95_ms` er NULL-bar (ingen
--    latenskrav); `terskel_maks_modellfeil` defaulter til 0 —
--    modellfeil er aldri stille godkjent.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evalueringskrav (
    modul_id               TEXT NOT NULL,
    kravversjon            INT  NOT NULL CHECK (kravversjon > 0),
    sett_id                TEXT NOT NULL,
    sett_versjon           INT  NOT NULL,
    sett_hash              TEXT NOT NULL,
    terskel_min_andel      NUMERIC NOT NULL
        CHECK (terskel_min_andel > 0 AND terskel_min_andel <= 1),
    terskel_maks_p95_ms    INT CHECK (terskel_maks_p95_ms > 0),
    terskel_maks_modellfeil INT NOT NULL DEFAULT 0
        CHECK (terskel_maks_modellfeil >= 0),
    status                 TEXT NOT NULL DEFAULT 'gjeldende'
        CHECK (status IN ('gjeldende', 'historisk')),
    opprettet              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (modul_id, kravversjon),
    FOREIGN KEY (modul_id, sett_id, sett_versjon, sett_hash)
        REFERENCES golden_sett (modul_id, sett_id, versjon, innhold_hash)
);
CREATE UNIQUE INDEX IF NOT EXISTS ett_gjeldende_krav_per_modul
    ON evalueringskrav (modul_id) WHERE status = 'gjeldende';

-- ------------------------------------------------------------
-- 3. evalueringskjoring — én målt kjøring av et golden-sett mot én
--    kandidat-digest. `bestatt` settes AV DØREN (signaturen har ingen
--    bestatt-parameter — port 6 er statisk målbar), mot kravet som var
--    gjeldende i registreringsøyeblikket. `kravversjon` er NULL-bar:
--    runbook-seedens målekjøring skjer FØR første krav finnes, og en
--    NULL-rad bærer per konstruksjon aldri et bytte (porten krever
--    eksakt kravversjon). `detalj_hash` pinner per-eksempel-resultatene
--    på disk — payloadfritt her, som settet selv.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evalueringskjoring (
    modul_id          TEXT NOT NULL,
    kjoring_id        UUID NOT NULL,
    artifact_digest   TEXT NOT NULL CHECK (length(btrim(artifact_digest)) > 0),
    kravversjon       INT,
    sett_id           TEXT NOT NULL,
    sett_versjon      INT  NOT NULL,
    sett_hash         TEXT NOT NULL,
    antall_eksempler  INT  NOT NULL CHECK (antall_eksempler > 0),
    antall_bestatt    INT  NOT NULL,
    antall_modellfeil INT  NOT NULL,
    p50_ms            INT  NOT NULL CHECK (p50_ms >= 0),
    p95_ms            INT  NOT NULL CHECK (p95_ms >= 0),
    varighet_s        NUMERIC NOT NULL CHECK (varighet_s >= 0),
    modellnavn        TEXT NOT NULL CHECK (length(btrim(modellnavn)) > 0),
    detalj_hash       TEXT NOT NULL CHECK (detalj_hash ~ '^[0-9a-f]{64}$'),
    startet_ts        TIMESTAMPTZ NOT NULL,
    avsluttet_ts      TIMESTAMPTZ NOT NULL,
    bestatt           BOOLEAN NOT NULL,
    aktor             TEXT NOT NULL,
    registrert        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (modul_id, kjoring_id),
    CHECK (antall_bestatt >= 0 AND antall_bestatt <= antall_eksempler),
    CHECK (antall_modellfeil >= 0 AND antall_modellfeil <= antall_eksempler),
    CHECK (avsluttet_ts >= startet_ts),
    FOREIGN KEY (modul_id, kravversjon)
        REFERENCES evalueringskrav (modul_id, kravversjon),
    FOREIGN KEY (modul_id, sett_id, sett_versjon, sett_hash)
        REFERENCES golden_sett (modul_id, sett_id, versjon, innhold_hash)
);

-- ------------------------------------------------------------
-- 4. modellstyring_hendelse — append-only revisjon av alt dørene gjør
--    (014-formen). Lukket hendelsessett: en ny hendelsestype er en
--    kontraktsendring, ikke en logglinje.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS modellstyring_hendelse (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    modul_id        TEXT NOT NULL,
    hendelse        TEXT NOT NULL CHECK (hendelse IN
        ('sett_registrert', 'krav_satt', 'kjoring_registrert')),
    sett_id         TEXT,
    sett_versjon    INT,
    sett_hash       TEXT,
    kravversjon     INT,
    kjoring_id      UUID,
    artifact_digest TEXT,
    bestatt         BOOLEAN,
    aktor           TEXT NOT NULL,
    detalj          JSONB,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS modellstyring_hendelse_modul
    ON modellstyring_hendelse (modul_id, id);

-- ============================================================
-- Integritetstriggere (014a-doktrinen: append-only håndheves i basen,
-- ikke i god vilje; TRUNCATE har egen statement-vakt fordi den omgår
-- FOR EACH ROW-triggere).
-- ============================================================
CREATE OR REPLACE FUNCTION m31_append_only()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% er append-only (immutable): % er forbudt',
        TG_TABLE_NAME, TG_OP;
END $$;

DROP TRIGGER IF EXISTS golden_sett_immutable ON golden_sett;
CREATE TRIGGER golden_sett_immutable BEFORE UPDATE OR DELETE ON golden_sett
    FOR EACH ROW EXECUTE FUNCTION m31_append_only();
DROP TRIGGER IF EXISTS golden_sett_ingen_truncate ON golden_sett;
CREATE TRIGGER golden_sett_ingen_truncate BEFORE TRUNCATE ON golden_sett
    FOR EACH STATEMENT EXECUTE FUNCTION m31_append_only();

DROP TRIGGER IF EXISTS kjoring_immutable ON evalueringskjoring;
CREATE TRIGGER kjoring_immutable BEFORE UPDATE OR DELETE ON evalueringskjoring
    FOR EACH ROW EXECUTE FUNCTION m31_append_only();
DROP TRIGGER IF EXISTS kjoring_ingen_truncate ON evalueringskjoring;
CREATE TRIGGER kjoring_ingen_truncate BEFORE TRUNCATE ON evalueringskjoring
    FOR EACH STATEMENT EXECUTE FUNCTION m31_append_only();

DROP TRIGGER IF EXISTS m31_hendelse_append_only ON modellstyring_hendelse;
CREATE TRIGGER m31_hendelse_append_only
    BEFORE UPDATE OR DELETE ON modellstyring_hendelse
    FOR EACH ROW EXECUTE FUNCTION m31_append_only();
DROP TRIGGER IF EXISTS m31_hendelse_ingen_truncate ON modellstyring_hendelse;
CREATE TRIGGER m31_hendelse_ingen_truncate
    BEFORE TRUNCATE ON modellstyring_hendelse
    FOR EACH STATEMENT EXECUTE FUNCTION m31_append_only();

-- evalueringskrav: identiteten og tersklene er FROSSET; det ENESTE
-- lovlige er livsløpsflippet gjeldende → historisk (dørens vei). DELETE
-- er forbudt — et historisk krav er revisjonsevidens.
CREATE OR REPLACE FUNCTION m31_krav_statemaskin()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'evalueringskrav er append-only: DELETE er forbudt';
    END IF;
    IF NEW.modul_id IS DISTINCT FROM OLD.modul_id
       OR NEW.kravversjon IS DISTINCT FROM OLD.kravversjon
       OR NEW.sett_id IS DISTINCT FROM OLD.sett_id
       OR NEW.sett_versjon IS DISTINCT FROM OLD.sett_versjon
       OR NEW.sett_hash IS DISTINCT FROM OLD.sett_hash
       OR NEW.terskel_min_andel IS DISTINCT FROM OLD.terskel_min_andel
       OR NEW.terskel_maks_p95_ms IS DISTINCT FROM OLD.terskel_maks_p95_ms
       OR NEW.terskel_maks_modellfeil IS DISTINCT FROM OLD.terskel_maks_modellfeil
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'evalueringskrav: identitet/terskler er frosset';
    END IF;
    IF NOT ((OLD.status = 'gjeldende' AND NEW.status = 'historisk')
            OR OLD.status = NEW.status) THEN
        RAISE EXCEPTION 'evalueringskrav: ulovlig statusovergang % -> %',
            OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS krav_statemaskin ON evalueringskrav;
CREATE TRIGGER krav_statemaskin BEFORE UPDATE OR DELETE ON evalueringskrav
    FOR EACH ROW EXECUTE FUNCTION m31_krav_statemaskin();
DROP TRIGGER IF EXISTS krav_ingen_truncate ON evalueringskrav;
CREATE TRIGGER krav_ingen_truncate BEFORE TRUNCATE ON evalueringskrav
    FOR EACH STATEMENT EXECUTE FUNCTION m31_append_only();

-- Eieren (modul_eier) må kunne SKRIVE tabellene når dørene kjører
-- (SECURITY DEFINER kjører som eier). Grantene bor HER, sammen med
-- funksjonene (014-formen; PR-013-lærdommen om løs migrer.py-kode).
-- Kjøres som migrator (eier av tabellene).
GRANT SELECT, INSERT         ON golden_sett            TO disponit_modul_eier;
GRANT SELECT, INSERT, UPDATE ON evalueringskrav        TO disponit_modul_eier;
GRANT SELECT, INSERT         ON evalueringskjoring     TO disponit_modul_eier;
GRANT SELECT, INSERT         ON modellstyring_hendelse TO disponit_modul_eier;

-- ============================================================
-- Dørene (SECURITY DEFINER, eid av disponit_modul_eier,
-- search_path=pg_catalog, advisory-xact-lås på identiteten, idempotens
-- på HELE tuppelen, hendelserad, REVOKE PUBLIC + GRANT
-- disponit_modules_admin — 014a-doktrinen hele veien).
-- ============================================================
SET LOCAL ROLE disponit_modul_eier;

-- Dør 1: registrer et golden-sett-hode. Immutabilitets-idempotent
-- (014-formen): identisk tuppel er no-op, avvikende innhold avvises —
-- settet LOVER én ting. FK-en til modulhode krever at modulen er
-- installert først.
CREATE OR REPLACE FUNCTION registrer_golden_sett(
    p_modul_id         TEXT,
    p_sett_id          TEXT,
    p_versjon          INT,
    p_innhold_hash     TEXT,
    p_antall_eksempler INT,
    p_beskrivelse      TEXT,
    p_aktor            TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE r RECORD;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'm31:sett:' || p_modul_id || ':' || p_sett_id || ':'
        || p_versjon::text, 0));
    SELECT innhold_hash, antall_eksempler, beskrivelse INTO r
      FROM public.golden_sett
     WHERE modul_id = p_modul_id AND sett_id = p_sett_id
       AND versjon = p_versjon;
    IF FOUND THEN
        IF (r.innhold_hash, r.antall_eksempler, r.beskrivelse)
           IS DISTINCT FROM
           (p_innhold_hash, p_antall_eksempler, p_beskrivelse) THEN
            RAISE EXCEPTION 'golden_sett (%,%,%) er immutable',
                p_modul_id, p_sett_id, p_versjon
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN;                                   -- idempotent
    END IF;
    INSERT INTO public.golden_sett (modul_id, sett_id, versjon,
        innhold_hash, antall_eksempler, beskrivelse)
        VALUES (p_modul_id, p_sett_id, p_versjon, p_innhold_hash,
                p_antall_eksempler, p_beskrivelse);
    INSERT INTO public.modellstyring_hendelse
        (modul_id, hendelse, sett_id, sett_versjon, sett_hash, aktor,
         detalj)
        VALUES (p_modul_id, 'sett_registrert', p_sett_id, p_versjon,
                p_innhold_hash, p_aktor,
                jsonb_build_object('antall_eksempler', p_antall_eksempler));
END $$;

-- Dør 2: sett gjeldende evalueringskrav. Ny `gjeldende` + forrige
-- `historisk` i SAMME transaksjon (den partielle indeksen gjør to
-- gjeldende umulig også utenom døren). Idempotent på hele tuppelen:
-- et kall som gjentar gjeldende krav ordrett er no-op — ingen ny
-- kravversjon, ingen hendelse.
CREATE OR REPLACE FUNCTION sett_evalueringskrav(
    p_modul_id          TEXT,
    p_sett_id           TEXT,
    p_sett_versjon      INT,
    p_sett_hash         TEXT,
    p_min_andel         NUMERIC,
    p_maks_p95_ms       INT,
    p_maks_modellfeil   INT,
    p_aktor             TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_gjeldende RECORD; v_neste INT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'm31:krav:' || p_modul_id, 0));
    SELECT kravversjon, sett_id, sett_versjon, sett_hash,
           terskel_min_andel, terskel_maks_p95_ms, terskel_maks_modellfeil
      INTO v_gjeldende FROM public.evalueringskrav
     WHERE modul_id = p_modul_id AND status = 'gjeldende';
    IF FOUND AND (v_gjeldende.sett_id, v_gjeldende.sett_versjon,
                  v_gjeldende.sett_hash, v_gjeldende.terskel_min_andel,
                  v_gjeldende.terskel_maks_p95_ms,
                  v_gjeldende.terskel_maks_modellfeil)
        IS NOT DISTINCT FROM
        (p_sett_id, p_sett_versjon, p_sett_hash, p_min_andel,
         p_maks_p95_ms, p_maks_modellfeil) THEN
        RETURN;                                   -- idempotent
    END IF;
    SELECT coalesce(max(kravversjon), 0) + 1 INTO v_neste
      FROM public.evalueringskrav WHERE modul_id = p_modul_id;
    UPDATE public.evalueringskrav SET status = 'historisk'
     WHERE modul_id = p_modul_id AND status = 'gjeldende';
    INSERT INTO public.evalueringskrav (modul_id, kravversjon, sett_id,
        sett_versjon, sett_hash, terskel_min_andel, terskel_maks_p95_ms,
        terskel_maks_modellfeil, status)
        VALUES (p_modul_id, v_neste, p_sett_id, p_sett_versjon,
                p_sett_hash, p_min_andel, p_maks_p95_ms,
                p_maks_modellfeil, 'gjeldende');
    INSERT INTO public.modellstyring_hendelse
        (modul_id, hendelse, sett_id, sett_versjon, sett_hash,
         kravversjon, aktor, detalj)
        VALUES (p_modul_id, 'krav_satt', p_sett_id, p_sett_versjon,
                p_sett_hash, v_neste, p_aktor,
                jsonb_build_object('terskel_min_andel', p_min_andel,
                                   'terskel_maks_p95_ms', p_maks_p95_ms,
                                   'terskel_maks_modellfeil',
                                   p_maks_modellfeil));
END $$;

-- Dør 3: registrer en evalueringskjøring. `bestatt` BEREGNES HER, mot
-- gjeldende krav — signaturen har med vilje ingen bestatt-parameter
-- (port 6: kallerens påstand finnes ikke som inngang). En kjøring som
-- ikke dekker HELE settet er uregistrerbar (port 7), og en kjøring mot
-- et annet sett enn gjeldende kravs måler ikke kravet og avvises
-- (port 3). Uten gjeldende krav (runbook-seedens målekjøring)
-- registreres raden med kravversjon NULL og bestatt=false — fail-closed:
-- uten terskel er ingenting bestått, og NULL bærer aldri et bytte.
-- Idempotent på HELE den oppgitte tuppelen; avvik på samme kjoring_id
-- er konflikt.
CREATE OR REPLACE FUNCTION registrer_evalueringskjoring(
    p_modul_id          TEXT,
    p_kjoring_id        UUID,
    p_artifact_digest   TEXT,
    p_sett_id           TEXT,
    p_sett_versjon      INT,
    p_sett_hash         TEXT,
    p_antall_eksempler  INT,
    p_antall_bestatt    INT,
    p_antall_modellfeil INT,
    p_p50_ms            INT,
    p_p95_ms            INT,
    p_varighet_s        NUMERIC,
    p_modellnavn        TEXT,
    p_detalj_hash       TEXT,
    p_startet_ts        TIMESTAMPTZ,
    p_avsluttet_ts      TIMESTAMPTZ,
    p_aktor             TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_sett RECORD; v_krav RECORD;
        v_kravversjon INT; v_bestatt BOOLEAN;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'm31:kjoring:' || p_modul_id || ':' || p_kjoring_id::text, 0));
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'm31:krav:' || p_modul_id, 0));
    SELECT artifact_digest, sett_id, sett_versjon, sett_hash,
           antall_eksempler, antall_bestatt, antall_modellfeil, p50_ms,
           p95_ms, varighet_s, modellnavn, detalj_hash, startet_ts,
           avsluttet_ts, aktor, bestatt
      INTO r FROM public.evalueringskjoring
     WHERE modul_id = p_modul_id AND kjoring_id = p_kjoring_id;
    IF FOUND THEN
        IF (r.artifact_digest, r.sett_id, r.sett_versjon, r.sett_hash,
            r.antall_eksempler, r.antall_bestatt, r.antall_modellfeil,
            r.p50_ms, r.p95_ms, r.varighet_s, r.modellnavn,
            r.detalj_hash, r.startet_ts, r.avsluttet_ts, r.aktor)
           IS DISTINCT FROM
           (p_artifact_digest, p_sett_id, p_sett_versjon, p_sett_hash,
            p_antall_eksempler, p_antall_bestatt, p_antall_modellfeil,
            p_p50_ms, p_p95_ms, p_varighet_s, p_modellnavn,
            p_detalj_hash, p_startet_ts, p_avsluttet_ts, p_aktor) THEN
            RAISE EXCEPTION 'evalueringskjoring (%,%) er immutable',
                p_modul_id, p_kjoring_id USING ERRCODE = 'unique_violation';
        END IF;
        RETURN r.bestatt;                         -- idempotent
    END IF;
    SELECT innhold_hash, antall_eksempler INTO v_sett
      FROM public.golden_sett
     WHERE modul_id = p_modul_id AND sett_id = p_sett_id
       AND versjon = p_sett_versjon;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'registrer_evalueringskjoring: ukjent golden-sett'
            ' (%,%,%)', p_modul_id, p_sett_id, p_sett_versjon
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_sett.innhold_hash IS DISTINCT FROM p_sett_hash THEN
        RAISE EXCEPTION 'registrer_evalueringskjoring: sett-hash avviker'
            ' fra registrert (%,%,%)', p_modul_id, p_sett_id,
            p_sett_versjon USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Port 7: delvis kjøring er uregistrerbar. Et resultat over færre
    -- (eller flere) eksempler enn settets er ikke en måling av settet.
    IF p_antall_eksempler IS DISTINCT FROM v_sett.antall_eksempler THEN
        RAISE EXCEPTION 'registrer_evalueringskjoring: kjøringen dekker'
            ' % av settets % eksempler — delvis kjøring registreres ikke',
            p_antall_eksempler, v_sett.antall_eksempler
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT kravversjon, sett_id, sett_versjon, sett_hash,
           terskel_min_andel, terskel_maks_p95_ms, terskel_maks_modellfeil
      INTO v_krav FROM public.evalueringskrav
     WHERE modul_id = p_modul_id AND status = 'gjeldende';
    IF FOUND THEN
        -- Port 3: en kjøring mot et annet sett enn gjeldende kravs
        -- måler ikke kravet — den registreres ikke.
        IF (v_krav.sett_id, v_krav.sett_versjon, v_krav.sett_hash)
           IS DISTINCT FROM (p_sett_id, p_sett_versjon, p_sett_hash) THEN
            RAISE EXCEPTION 'registrer_evalueringskjoring: kjøringen'
                ' måler sett (%,%) — gjeldende krav % gjelder sett (%,%)',
                p_sett_id, p_sett_versjon, v_krav.kravversjon,
                v_krav.sett_id, v_krav.sett_versjon
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        v_kravversjon := v_krav.kravversjon;
        v_bestatt :=
            (p_antall_bestatt::numeric / p_antall_eksempler)
                >= v_krav.terskel_min_andel
            AND (v_krav.terskel_maks_p95_ms IS NULL
                 OR p_p95_ms <= v_krav.terskel_maks_p95_ms)
            AND p_antall_modellfeil <= v_krav.terskel_maks_modellfeil;
    ELSE
        v_kravversjon := NULL;
        v_bestatt := FALSE;
    END IF;
    INSERT INTO public.evalueringskjoring (modul_id, kjoring_id,
        artifact_digest, kravversjon, sett_id, sett_versjon, sett_hash,
        antall_eksempler, antall_bestatt, antall_modellfeil, p50_ms,
        p95_ms, varighet_s, modellnavn, detalj_hash, startet_ts,
        avsluttet_ts, bestatt, aktor)
        VALUES (p_modul_id, p_kjoring_id, p_artifact_digest,
                v_kravversjon, p_sett_id, p_sett_versjon, p_sett_hash,
                p_antall_eksempler, p_antall_bestatt, p_antall_modellfeil,
                p_p50_ms, p_p95_ms, p_varighet_s, p_modellnavn,
                p_detalj_hash, p_startet_ts, p_avsluttet_ts, v_bestatt,
                p_aktor);
    INSERT INTO public.modellstyring_hendelse
        (modul_id, hendelse, sett_id, sett_versjon, sett_hash,
         kravversjon, kjoring_id, artifact_digest, bestatt, aktor)
        VALUES (p_modul_id, 'kjoring_registrert', p_sett_id,
                p_sett_versjon, p_sett_hash, v_kravversjon, p_kjoring_id,
                p_artifact_digest, v_bestatt, p_aktor);
    RETURN v_bestatt;
END $$;

REVOKE ALL ON FUNCTION registrer_golden_sett(TEXT, TEXT, INT, TEXT, INT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION sett_evalueringskrav(TEXT, TEXT, INT, TEXT, NUMERIC, INT, INT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION registrer_evalueringskjoring(TEXT, UUID, TEXT, TEXT, INT, TEXT, INT, INT, INT, INT, INT, NUMERIC, TEXT, TEXT, TIMESTAMPTZ, TIMESTAMPTZ, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION registrer_golden_sett(TEXT, TEXT, INT, TEXT, INT, TEXT, TEXT) TO disponit_modules_admin;
GRANT EXECUTE ON FUNCTION sett_evalueringskrav(TEXT, TEXT, INT, TEXT, NUMERIC, INT, INT, TEXT) TO disponit_modules_admin;
GRANT EXECUTE ON FUNCTION registrer_evalueringskjoring(TEXT, UUID, TEXT, TEXT, INT, TEXT, INT, INT, INT, INT, INT, NUMERIC, TEXT, TEXT, TIMESTAMPTZ, TIMESTAMPTZ, TEXT) TO disponit_modules_admin;

-- ============================================================
-- Porten i bytt_release. Kroppen under er 014-kroppen KOPIERT BYTE FOR
-- BYTE (sha256 av originalutdraget:
-- ab59cbaa4053f64de5fb6fb3945582a9f75f6a8fdb0bf9856fbc6e37924159fe)
-- og diff-endret NØYAKTIG to steder: (1) DECLARE-linjen får de to
-- portvariablene, (2) portblokken står etter reclaim-kontrollen og før
-- første mutasjon. Samme signatur og samme låserekkefølge (modul-lås →
-- kontraktlås) — CREATE OR REPLACE beholder eier og grants (085-formen).
-- ============================================================
CREATE OR REPLACE FUNCTION bytt_release(
    p_modul_id        TEXT,
    p_miljo           TEXT,
    p_ny_release_id   TEXT,
    p_kontraktversjon INT,
    p_kontrakt_hash   TEXT,
    p_aktor           TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_ny_livslop TEXT; v_gammel_release TEXT; v_status TEXT;
        v_m31_kravversjon INT; v_m31_digest TEXT;
BEGIN
    -- Modul-lås FØRST (samme rekkefølge overalt), så kontraktlåsen. Modul-låsen
    -- serialiserer med noddeaktiver_modul (Codex P1): ellers kunne et bytte legge
    -- inn en claiming ETTER at nødstoppet skannet deploymentene, og etterlate en
    -- nodeaktivert modul med en claiming-deployment.
    PERFORM pg_advisory_xact_lock(hashtextextended('modul:' || p_modul_id, 0));
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'modulregister:bytt:' || p_modul_id || ':' || p_miljo || ':'
        || p_kontraktversjon::text || ':' || p_kontrakt_hash, 0));
    -- En nodeaktivert modul får ikke en frisk claiming (reaktiver_modul først).
    SELECT status INTO v_status FROM public.modulhode WHERE modul_id = p_modul_id;
    IF v_status = 'nodeaktivert' THEN
        RAISE EXCEPTION 'bytt_release: modul % er nodeaktivert', p_modul_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
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
    -- ============================================================
    -- M-31-porten (086): golden-sett-gaten. Har modulen et GJELDENDE
    -- evalueringskrav, bærer bare en BESTÅTT evalueringskjøring for
    -- NØYAKTIG det kravet (dom 3: eksakt kravversjon — en dom mot
    -- svakere terskler bærer aldri et bytte under strengere) og for
    -- KANDIDATENS artifact_digest byttet. Digesten er modellens
    -- miljøuavhengige identitet (dom 2) — derfor intet miljøfilter.
    -- Modul uten krav-rad er uberørt (opt-in, ingen backfill). Porten
    -- står FØR første mutasjon: fail-closed FØR byttet ER
    -- rollback-semantikken.
    SELECT kravversjon INTO v_m31_kravversjon FROM public.evalueringskrav
     WHERE modul_id = p_modul_id AND status = 'gjeldende';
    IF FOUND THEN
        SELECT artifact_digest INTO v_m31_digest FROM public.modulrelease
         WHERE modul_id = p_modul_id AND release_id = p_ny_release_id
           AND kontraktversjon = p_kontraktversjon
           AND kontrakt_hash = p_kontrakt_hash;
        PERFORM 1 FROM public.evalueringskjoring
         WHERE modul_id = p_modul_id AND artifact_digest = v_m31_digest
           AND kravversjon = v_m31_kravversjon AND bestatt;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'bytt_release: m31-porten — ingen bestått '
                'evalueringskjøring for %/% (digest %, kravversjon %)',
                p_modul_id, p_ny_release_id, v_m31_digest,
                v_m31_kravversjon USING ERRCODE = 'invalid_parameter_value';
        END IF;
    END IF;
    -- Gammel claiming for samme kontrakt → draining (partiell indeks frigjøres
    -- før innsettingen av den nye). Fang den gamle releasen så draining-
    -- overgangen kan revideres (Codex P2 — ellers manglet den i hendelsesstrømmen).
    UPDATE public.moduldeployment SET livslop = 'draining'
     WHERE modul_id = p_modul_id AND miljo = p_miljo
       AND kontraktversjon = p_kontraktversjon AND kontrakt_hash = p_kontrakt_hash
       AND livslop = 'claiming'
    RETURNING release_id INTO v_gammel_release;
    IF v_gammel_release IS NOT NULL THEN
        INSERT INTO public.modulregister_hendelse
            (modul_id, hendelse, fra_livslop, til_livslop, release_id, miljo,
             kontraktversjon, kontrakt_hash, aktor)
            VALUES (p_modul_id, 'drainet_ved_bytte', 'claiming', 'draining',
                    v_gammel_release, p_miljo, p_kontraktversjon,
                    p_kontrakt_hash, p_aktor);
    END IF;
    INSERT INTO public.moduldeployment (modul_id, release_id, kontraktversjon,
        kontrakt_hash, miljo, livslop)
        VALUES (p_modul_id, p_ny_release_id, p_kontraktversjon, p_kontrakt_hash,
                p_miljo, 'claiming');
    INSERT INTO public.modulregister_hendelse
        (modul_id, hendelse, til_livslop, release_id, miljo, kontraktversjon,
         kontrakt_hash, aktor)
        VALUES (p_modul_id, 'releasebytte', 'claiming', p_ny_release_id,
                p_miljo, p_kontraktversjon, p_kontrakt_hash, p_aktor);
END $$;

RESET ROLE;
