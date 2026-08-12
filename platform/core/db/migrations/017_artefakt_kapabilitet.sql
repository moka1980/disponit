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
-- API-veien når oppdraget claimes (runtime), innløses/forbrukes av opplastings-
-- endepunktet (runtime). All skriving går via de tre herdede funksjonene.
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

-- Forbruk (atomisk brenn): setter status='brukt' + artefakt_id. Idempotent på
-- samme artefakt_id; motstridende artefakt → 'konflikt'.
CREATE OR REPLACE FUNCTION bruk_artefaktkapabilitet(p_jti TEXT, p_artefakt_id UUID)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_aid UUID;
BEGIN
    SELECT status, artefakt_id INTO v_status, v_aid FROM public.artefaktkapabilitet
     WHERE jti = p_jti FOR UPDATE;
    IF NOT FOUND THEN RETURN 'ugyldig'; END IF;
    IF v_status = 'brukt' THEN
        RETURN CASE WHEN v_aid = p_artefakt_id THEN 'idempotent' ELSE 'konflikt' END;
    END IF;
    IF v_status = 'feilet' THEN RETURN 'ugyldig'; END IF;
    UPDATE public.artefaktkapabilitet
       SET status = 'brukt', artefakt_id = p_artefakt_id, brukt_ts = now()
     WHERE jti = p_jti;
    RETURN 'brukt';
END $$;

REVOKE ALL ON FUNCTION utsted_artefaktkapabilitet(TEXT, BIGINT, TEXT, TEXT, INT, TEXT, BIGINT, TEXT, TEXT, INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION innlos_artefaktkapabilitet(TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION bruk_artefaktkapabilitet(TEXT, UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION utsted_artefaktkapabilitet(TEXT, BIGINT, TEXT, TEXT, INT, TEXT, BIGINT, TEXT, TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION innlos_artefaktkapabilitet(TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION bruk_artefaktkapabilitet(TEXT, UUID) TO disponit;
RESET ROLE;

-- domene_eier (SECURITY DEFINER-kjøreren) må kunne skrive tabellen + LESE oppdrag.
GRANT SELECT, INSERT, UPDATE ON artefaktkapabilitet TO disponit_domene_eier;
