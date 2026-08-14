-- ============================================================
-- 017 — Artefakt-opplastingskapabilitet (PR-014b CP5, §7).
--
-- Controlleren laster opp en lukket rapport til POST /v1/artefakt med en EGEN
-- kapabilitet: eget audience, eget scope (`artifacts:upload`), kort levetid,
-- bundet til tenant · oppdrag_id · modul_id · release_id · kontraktversjon ·
-- kontrakt_hash · module_epoch · artefakttype. Kryssbruk mot kvitterings-
-- kapabiliteten (005) avvises STRUKTURELT: egen tabell, egne funksjoner — en jti
-- utstedt her kan ikke innløses av `innlos_kvitteringskapabilitet` og omvendt.
--
-- Eid av `disponit_domene_eier` (som artefakt-funksjonene i 016). Utstedes av
-- API-veien når oppdraget claimes (runtime), innløses (lesende) av opplastings-
-- endepunktet og FORBRUKES kun av `lagre_artefakt_staged` — i samme transaksjon
-- som artefaktraden skrives. All skriving går via de herdede funksjonene.
-- ============================================================

CREATE TABLE IF NOT EXISTS artefaktkapabilitet (
    jti              TEXT PRIMARY KEY CHECK (jti ~ '^[0-9a-f]{32,}$'),
    tenant           TEXT NOT NULL,
    oppdrag_id       BIGINT NOT NULL,
    modul_id         TEXT NOT NULL,
    release_id       TEXT NOT NULL,
    kontraktversjon  INT  NOT NULL,
    kontrakt_hash    TEXT NOT NULL,
    module_epoch     BIGINT NOT NULL,
    artefakttype     TEXT NOT NULL,
    scope            TEXT NOT NULL DEFAULT 'artifacts:upload'
                     CHECK (scope = 'artifacts:upload'),
    status           TEXT NOT NULL DEFAULT 'utstedt'
                     CHECK (status IN ('utstedt','brukt','feilet')),
    artefakt_id      UUID,               -- settes ved forbruk (bruk_...)
    utstedt          TIMESTAMPTZ NOT NULL DEFAULT now(),
    utloper          TIMESTAMPTZ NOT NULL,
    brukt_ts         TIMESTAMPTZ,
    -- En brukt kapabilitet MÅ bære artefaktet den ble brukt til (som
    -- kvitteringskapabiliteten bærer resultathash) — ellers kan ikke en re-post
    -- skilles fra motstridende bruk.
    CONSTRAINT artefaktkapabilitet_brukt_har_artefakt CHECK (
        status <> 'brukt' OR artefakt_id IS NOT NULL),
    CONSTRAINT artefaktkapabilitet_oppdrag_fk
        FOREIGN KEY (tenant, oppdrag_id) REFERENCES oppdrag (tenant, id)
);
CREATE INDEX IF NOT EXISTS artefaktkapabilitet_oppdrag
    ON artefaktkapabilitet (tenant, oppdrag_id);

-- Bindingsfelter uforanderlige; status fremover (utstedt→brukt/feilet); feilet
-- terminal; artefakt_id write-once.
CREATE OR REPLACE FUNCTION artefaktkapabilitet_statusmaskin()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.jti IS DISTINCT FROM OLD.jti
       OR NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.oppdrag_id IS DISTINCT FROM OLD.oppdrag_id
       OR NEW.modul_id IS DISTINCT FROM OLD.modul_id
       OR NEW.release_id IS DISTINCT FROM OLD.release_id
       OR NEW.kontraktversjon IS DISTINCT FROM OLD.kontraktversjon
       OR NEW.kontrakt_hash IS DISTINCT FROM OLD.kontrakt_hash
       OR NEW.module_epoch IS DISTINCT FROM OLD.module_epoch
       OR NEW.artefakttype IS DISTINCT FROM OLD.artefakttype
       OR NEW.scope IS DISTINCT FROM OLD.scope
       OR NEW.utloper IS DISTINCT FROM OLD.utloper
       OR NEW.utstedt IS DISTINCT FROM OLD.utstedt THEN
        RAISE EXCEPTION 'artefaktkapabilitet: bindingsfelter er uforanderlige';
    END IF;
    IF OLD.artefakt_id IS NOT NULL AND NEW.artefakt_id IS DISTINCT FROM OLD.artefakt_id THEN
        RAISE EXCEPTION 'artefaktkapabilitet: artefakt_id er uforanderlig når satt';
    END IF;
    IF OLD.status = 'feilet' AND NEW.status <> 'feilet' THEN
        RAISE EXCEPTION 'artefaktkapabilitet: feilet er terminal';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS artefaktkapabilitet_overgang ON artefaktkapabilitet;
CREATE TRIGGER artefaktkapabilitet_overgang BEFORE UPDATE ON artefaktkapabilitet
    FOR EACH ROW EXECUTE FUNCTION artefaktkapabilitet_statusmaskin();
DROP TRIGGER IF EXISTS artefaktkapabilitet_ingen_delete ON artefaktkapabilitet;
CREATE TRIGGER artefaktkapabilitet_ingen_delete BEFORE DELETE OR TRUNCATE ON artefaktkapabilitet
    FOR EACH STATEMENT EXECUTE FUNCTION domene_append_only();

REVOKE ALL ON artefaktkapabilitet FROM PUBLIC;

-- ============================================================
-- Herdede funksjoner (SECURITY DEFINER, eid av disponit_domene_eier).
-- ============================================================
SET LOCAL ROLE disponit_domene_eier;

-- Utsted: bindingen VERIFISERES mot det claimede oppdraget (tenant + at
-- oppdraget faktisk er plukket + at kontrakt/epoch stemplet på oppdraget matcher
-- det kalleren ber om) — kalleren kan ikke be om en kapabilitet for et oppdrag
-- den ikke nettopp claimet, eller for en annen kontrakt enn den bundne.
CREATE OR REPLACE FUNCTION utsted_artefaktkapabilitet(
    p_tenant TEXT, p_oppdrag_id BIGINT, p_modul_id TEXT, p_release_id TEXT,
    p_kontraktversjon INT, p_kontrakt_hash TEXT, p_module_epoch BIGINT,
    p_artefakttype TEXT, p_jti TEXT, p_levetid_s INT DEFAULT 900)
RETURNS TABLE (jti TEXT, utloper TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_lev INT := least(greatest(coalesce(p_levetid_s, 900), 60), 3600);
        v_utloper TIMESTAMPTZ := now() + (v_lev || ' seconds')::INTERVAL;
BEGIN
    IF p_jti IS NULL OR p_jti !~ '^[0-9a-f]{32,}$' THEN
        RAISE EXCEPTION 'utsted_artefaktkapabilitet: ugyldig jti-format';
    END IF;
    PERFORM 1 FROM public.oppdrag o
     WHERE o.tenant = p_tenant AND o.id = p_oppdrag_id AND o.status = 'plukket'
       AND o.modul_id IS NOT DISTINCT FROM p_modul_id
       AND o.kontraktversjon IS NOT DISTINCT FROM p_kontraktversjon
       AND o.kontrakt_hash IS NOT DISTINCT FROM p_kontrakt_hash
       AND o.module_epoch IS NOT DISTINCT FROM p_module_epoch;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'utsted_artefaktkapabilitet: oppdrag %/% er ikke plukket '
            'med matchende kontraktbinding', p_tenant, p_oppdrag_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Codex: artefakttypen MÅ være registrert til NETTOPP denne modulen+kontrakten
    -- — ellers kunne en kapabilitet for modul A navngi en type registrert til
    -- modul B, og opplastingen lykkes med feil type/skjema.
    PERFORM 1 FROM public.artefakttype_register atr
     WHERE atr.artefakttype = p_artefakttype AND atr.eiermodul = p_modul_id
       AND atr.kontraktversjon = p_kontraktversjon
       AND atr.kontrakt_hash = p_kontrakt_hash;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'utsted_artefaktkapabilitet: artefakttype % er ikke '
            'registrert for modulen/kontrakten', p_artefakttype
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.artefaktkapabilitet (jti, tenant, oppdrag_id, modul_id,
        release_id, kontraktversjon, kontrakt_hash, module_epoch, artefakttype,
        utloper)
        VALUES (p_jti, p_tenant, p_oppdrag_id, p_modul_id, p_release_id,
                p_kontraktversjon, p_kontrakt_hash, p_module_epoch, p_artefakttype,
                v_utloper);
    RETURN QUERY SELECT p_jti, v_utloper;
END $$;

-- Innløs (idempotent, brenner IKKE): returnerer bindingen for en gyldig, ubrukt,
-- ikke-utløpt kapabilitet — men KUN til den holdende modulen (p_modul_id).
CREATE OR REPLACE FUNCTION innlos_artefaktkapabilitet(p_jti TEXT, p_modul_id TEXT)
RETURNS TABLE (tenant TEXT, oppdrag_id BIGINT, release_id TEXT,
               kontraktversjon INT, kontrakt_hash TEXT, module_epoch BIGINT,
               artefakttype TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    RETURN QUERY
    SELECT k.tenant, k.oppdrag_id, k.release_id, k.kontraktversjon,
           k.kontrakt_hash, k.module_epoch, k.artefakttype
      FROM public.artefaktkapabilitet k
     WHERE k.jti = p_jti AND k.modul_id = p_modul_id
       AND k.status <> 'feilet' AND k.utloper > now();
END $$;

-- Codex: den FRITTSTÅENDE brenneren er FJERNET. Etter at `lagre_artefakt_staged`
-- ble den atomiske forbruksveien (016:502), var `bruk_artefaktkapabilitet(jti,
-- uuid)` en foreldet sidedør som runtime fortsatt hadde EXECUTE på: den tok bare
-- en jti og en vilkårlig UUID, verifiserte verken holdende modul eller at
-- artefaktet fantes, og kunne dermed markere en LEVENDE kapabilitet `brukt` med
-- et oppdiktet artefakt-id — hvorpå staged-write leste den statusen som
-- autoritativ og den legitime opplastingen aldri kom inn. Forbruk skjer nå KUN
-- der artefaktraden faktisk skrives, i samme transaksjon. DROP-en tar også
-- databaser der den foreldede funksjonen alt er installert.
DROP FUNCTION IF EXISTS bruk_artefaktkapabilitet(TEXT, UUID);

-- Codex (016:502): lagre_artefakt_staged VALIDERER OG FORBRUKER kapabiliteten
-- ATOMISK (redefinert her hvor artefaktkapabilitet finnes). Signaturen er
-- uendret, men bindingen MÅ nå matche en gyldig, ubrukt, ikke-utløpt kapabilitet
-- holdt av modulen — en runtime-tilkobling kan ikke lenger kalle funksjonen
-- direkte med oppdiktet jti/binding og omgå eierskap/utløp/status.
CREATE OR REPLACE FUNCTION lagre_artefakt_staged(
    p_tenant TEXT, p_oppdrag_id BIGINT, p_artefakttype TEXT, p_modul_id TEXT,
    p_release_id TEXT, p_kontraktversjon INT, p_kontrakt_hash TEXT,
    p_module_epoch BIGINT, p_storrelse INT, p_klartekst_sha256 TEXT,
    p_ciphertext BYTEA, p_nonce BYTEA, p_dek_ref TEXT, p_kapabilitet_jti TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE k RECORD; v_id UUID; v_hash TEXT;
BEGIN
    SET LOCAL synchronous_commit = on;
    SELECT tenant, oppdrag_id, release_id, kontraktversjon, kontrakt_hash,
           module_epoch, artefakttype, status, artefakt_id, utloper
      INTO k FROM public.artefaktkapabilitet
     WHERE jti = p_kapabilitet_jti AND modul_id = p_modul_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'artefaktkapabilitet %/% ukjent', p_kapabilitet_jti,
            p_modul_id USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF k.status = 'brukt' THEN
        SELECT klartekst_sha256 INTO v_hash FROM public.artefakt
         WHERE artefakt_id = k.artefakt_id;
        IF v_hash IS DISTINCT FROM p_klartekst_sha256 THEN
            RAISE EXCEPTION 'artefaktkapabilitet % gjenbrukt for ANNET dokument',
                p_kapabilitet_jti USING ERRCODE = 'unique_violation';
        END IF;
        RETURN k.artefakt_id;                       -- idempotent
    END IF;
    IF k.status = 'feilet' OR now() > k.utloper THEN
        RAISE EXCEPTION 'artefaktkapabilitet % utløpt/ugyldig', p_kapabilitet_jti
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF (k.tenant, k.oppdrag_id, k.release_id, k.kontraktversjon, k.kontrakt_hash,
        k.module_epoch, k.artefakttype)
       IS DISTINCT FROM
       (p_tenant, p_oppdrag_id, p_release_id, p_kontraktversjon, p_kontrakt_hash,
        p_module_epoch, p_artefakttype) THEN
        RAISE EXCEPTION 'lagre_artefakt_staged: binding matcher ikke kapabiliteten'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.artefakt (tenant, oppdrag_id, artefakttype, modul_id,
        release_id, kontraktversjon, kontrakt_hash, module_epoch, tilstand,
        storrelse_bytes, klartekst_sha256, ciphertext, nonce, dek_ref,
        kapabilitet_jti)
        VALUES (k.tenant, k.oppdrag_id, k.artefakttype, p_modul_id, k.release_id,
                k.kontraktversjon, k.kontrakt_hash, k.module_epoch, 'staged',
                p_storrelse, p_klartekst_sha256, p_ciphertext, p_nonce, p_dek_ref,
                p_kapabilitet_jti)
        RETURNING artefakt_id INTO v_id;
    UPDATE public.artefaktkapabilitet
       SET status = 'brukt', artefakt_id = v_id, brukt_ts = now()
     WHERE jti = p_kapabilitet_jti;
    RETURN v_id;
END $$;

REVOKE ALL ON FUNCTION utsted_artefaktkapabilitet(TEXT, BIGINT, TEXT, TEXT, INT, TEXT, BIGINT, TEXT, TEXT, INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION innlos_artefaktkapabilitet(TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION lagre_artefakt_staged(TEXT, BIGINT, TEXT, TEXT, TEXT, INT, TEXT, BIGINT, INT, TEXT, BYTEA, BYTEA, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION utsted_artefaktkapabilitet(TEXT, BIGINT, TEXT, TEXT, INT, TEXT, BIGINT, TEXT, TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION innlos_artefaktkapabilitet(TEXT, TEXT) TO disponit;
-- Runtime har ETT forbrukspunkt: den atomiske staged-writen (redefinert over).
GRANT EXECUTE ON FUNCTION lagre_artefakt_staged(TEXT, BIGINT, TEXT, TEXT, TEXT, INT, TEXT, BIGINT, INT, TEXT, BYTEA, BYTEA, TEXT, TEXT) TO disponit;
RESET ROLE;

-- domene_eier (SECURITY DEFINER-kjøreren) må kunne skrive tabellen + LESE oppdrag.
GRANT SELECT, INSERT, UPDATE ON artefaktkapabilitet TO disponit_domene_eier;

-- §7 pkt. 8: en kvittering hvis artefakt ikke lar seg verifisere (epoch-drift
-- eller bindingsavvik) karantenesettes som sikkerhetssak. Utvid hendelses-
-- taksonomien additivt (alle 011-verdier beholdt + den nye).
ALTER TABLE unntak_historikk DROP CONSTRAINT unntak_historikk_hendelse_check;
ALTER TABLE unntak_historikk ADD CONSTRAINT unntak_historikk_hendelse_check CHECK (
    hendelse IN (
        'opprettet','statusendring','claim','claim_utlopt','dek_destruert',
        'claim_fornyet','klassifisert','repair_generation_ny',
        'generation_blokkert_aktiv_utforelse','kapabilitet_utstedt',
        'kapabilitet_brukt','oppdrag_opprettet','oppdrag_kansellert',
        'kvittering','sen_kvittering','motstridende_kvittering',
        'policy_endret_siden_opprettelse','legacy_uten_snapshot',
        'dek_utilgjengelig','verifikator_utilgjengelig','frist_utlopt',
        'verifikasjon_bestilt','verifikasjon_positiv','verifikasjon_negativ',
        'verifikasjon_utlopt','verifikasjon_konflikt','verifikasjon_retry',
        'sikkerhetsfrysing',
        'godkjenning_startet','attestasjon_registrert','runde_apnet',
        'runde_utlopt','runde_kansellert','godkjent','avvist_handling',
        'eskalert','godkjenning_stoppet_av_policy',
        'policy_endret_under_godkjenning','sen_kvittering_etter_avvis',
        'avklaring_kreves',
        -- PR-014b §7:
        'artefakt_ikke_verifisert'));
