-- 058: inndata-artefaktkontrakten (#162) — buntens vei INN.
--
-- Speiler 017s form i motsatt retning: 017 er UTDATA (modulen leverer
-- en rapport med en kapabilitet utstedt VED claim); dette er INNDATA
-- (kunden laster opp en bunt med en reservasjon utstedt FØR claim, og
-- bestillingen BINDER den). Tre tilstander, tre dører:
--
--   reservert --(registrer_inndata_lastet)--> lastet
--   lastet    --(bind_inndata, i bestillingens tx)--> bundet
--
-- * `maks_bytes` er DEKLARERT ved reservasjonen og håndhevet både i
--   strømmen (middleware-tellingen) og her (faktiske_bytes <= maks) —
--   arkivgatens arbeid blir dermed bundet av et tall noen har signert
--   for (#162s hele poeng).
-- * Payloaden bor på FILSYSTEMET (kryptert med tenant-DEK av API-et);
--   raden bærer metadata + kryptoreferansen. En rad uten fil er en
--   død referanse og fanges av resolverens lesing, aldri av tillit.
-- * Eid av `disponit_domene_eier` (017-formen): all skriving går via
--   de herdede funksjonene; runtime har KUN EXECUTE.
--
-- v1-GRENSE (bevisst, dokumentert i kontrakt/KONTRAKT.md): fysisk tak
-- 64 MiB per bunt — engangs-kryptering i minnet. Chunket kryptering for
-- fullskala (opptil ~1 GiB fysisk) er egen maskin med eget issue; taket
-- står i `INNDATA_MAKS_FYSISK` her og i api/inndata.py og MÅLES likt.

CREATE TABLE inndata_artefakt (
    tenant TEXT NOT NULL,
    inndata_id UUID NOT NULL DEFAULT gen_random_uuid(),
    eiermodul TEXT NOT NULL,
    formaal TEXT NOT NULL CHECK (formaal IN ('soknadsbunt')),
    innholdstype TEXT NOT NULL CHECK (innholdstype IN ('application/zip')),
    maks_bytes BIGINT NOT NULL CHECK (maks_bytes > 0
                                      AND maks_bytes <= 64 * 1024 * 1024),
    faktiske_bytes BIGINT,
    innhold_sha256 TEXT,
    key_id TEXT,
    nonce BYTEA,
    lager_sti TEXT,
    status TEXT NOT NULL DEFAULT 'reservert'
        CHECK (status IN ('reservert', 'lastet', 'bundet', 'forkastet')),
    reservasjon_jti TEXT NOT NULL CHECK (reservasjon_jti ~ '^[0-9a-f]{32,}$'),
    oppdrag_id BIGINT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    utloper TIMESTAMPTZ NOT NULL,
    lastet_ts TIMESTAMPTZ,
    bundet_ts TIMESTAMPTZ,
    CONSTRAINT inndata_artefakt_pk PRIMARY KEY (tenant, inndata_id),
    CONSTRAINT inndata_jti_en_gang UNIQUE (tenant, reservasjon_jti),
    CONSTRAINT inndata_oppdrag_fk FOREIGN KEY (tenant, oppdrag_id)
        REFERENCES oppdrag (tenant, id),
    -- Tilstanden bærer feltene sine, totalt (SP-5-formen): en lastet rad
    -- HAR måling+krypto+sti; en bundet rad HAR oppdraget; en reservert
    -- har ingen av delene.
    CONSTRAINT inndata_tilstand_totalt CHECK (
        (status = 'reservert' AND faktiske_bytes IS NULL
         AND innhold_sha256 IS NULL AND lager_sti IS NULL
         AND oppdrag_id IS NULL AND lastet_ts IS NULL
         AND bundet_ts IS NULL)
     OR (status = 'lastet' AND faktiske_bytes IS NOT NULL
         AND innhold_sha256 IS NOT NULL AND lager_sti IS NOT NULL
         AND key_id IS NOT NULL AND nonce IS NOT NULL
         AND oppdrag_id IS NULL AND lastet_ts IS NOT NULL
         AND bundet_ts IS NULL)
     OR (status = 'bundet' AND faktiske_bytes IS NOT NULL
         AND innhold_sha256 IS NOT NULL AND lager_sti IS NOT NULL
         AND oppdrag_id IS NOT NULL AND bundet_ts IS NOT NULL)
     OR (status = 'forkastet')),
    CONSTRAINT inndata_maaling_innenfor CHECK (
        faktiske_bytes IS NULL OR
        (faktiske_bytes > 0 AND faktiske_bytes <= maks_bytes))
);
CREATE INDEX inndata_artefakt_oppdrag
    ON inndata_artefakt (tenant, oppdrag_id) WHERE oppdrag_id IS NOT NULL;
-- Reaperens utvalg (ubundne som løp ut): partial på status+utloper.
CREATE INDEX inndata_artefakt_utlop
    ON inndata_artefakt (utloper) WHERE status IN ('reservert', 'lastet');

-- Statusmaskinen (017-formen): bindingsfelter immutable; overgangene er
-- nøyaktig de tre pilene + forkasting av ureist reservasjon/utløpt
-- lastet; DELETE/TRUNCATE aldri.
CREATE OR REPLACE FUNCTION inndata_artefakt_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'inndata_artefakt: % avvist', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.inndata_id IS DISTINCT FROM OLD.inndata_id
       OR NEW.eiermodul IS DISTINCT FROM OLD.eiermodul
       OR NEW.formaal IS DISTINCT FROM OLD.formaal
       OR NEW.innholdstype IS DISTINCT FROM OLD.innholdstype
       OR NEW.maks_bytes IS DISTINCT FROM OLD.maks_bytes
       OR NEW.reservasjon_jti IS DISTINCT FROM OLD.reservasjon_jti
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.utloper IS DISTINCT FROM OLD.utloper THEN
        RAISE EXCEPTION 'inndata_artefakt: bindingsfeltene er immutable'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT ((OLD.status = 'reservert' AND NEW.status IN ('lastet',
                                                         'forkastet'))
         OR (OLD.status = 'lastet' AND NEW.status IN ('bundet',
                                                      'forkastet'))) THEN
        RAISE EXCEPTION 'inndata_artefakt: overgang % -> % finnes ikke',
            OLD.status, NEW.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Målingene er write-once: satt ved 'lastet', aldri endret siden.
    IF OLD.status <> 'reservert' AND (
           NEW.faktiske_bytes IS DISTINCT FROM OLD.faktiske_bytes
        OR NEW.innhold_sha256 IS DISTINCT FROM OLD.innhold_sha256
        OR NEW.key_id IS DISTINCT FROM OLD.key_id
        OR NEW.nonce IS DISTINCT FROM OLD.nonce
        OR NEW.lager_sti IS DISTINCT FROM OLD.lager_sti
        OR NEW.lastet_ts IS DISTINCT FROM OLD.lastet_ts) THEN
        RAISE EXCEPTION 'inndata_artefakt: målingene er write-once'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS inndata_artefakt_vakt ON inndata_artefakt;
CREATE TRIGGER inndata_artefakt_vakt
    BEFORE UPDATE OR DELETE ON inndata_artefakt
    FOR EACH ROW EXECUTE FUNCTION inndata_artefakt_vakt();
DROP TRIGGER IF EXISTS inndata_artefakt_ingen_truncate ON inndata_artefakt;
CREATE TRIGGER inndata_artefakt_ingen_truncate
    BEFORE TRUNCATE ON inndata_artefakt
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

ALTER TABLE inndata_artefakt ENABLE ROW LEVEL SECURITY;
ALTER TABLE inndata_artefakt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON inndata_artefakt
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- ------------------------------------------------------------
-- Dørene. Eid av domene_eier (017-formen); rettighetene INNE i blokka
-- (PUBLIC-EXECUTE-læren fra #140). Eieren av tabellen (migrator) gir
-- dør-eieren radrettighetene først — vaktene snevrer dem til
-- statusmaskinens overganger, også for denne rollen.
GRANT SELECT, INSERT, UPDATE ON inndata_artefakt TO disponit_domene_eier;

SET LOCAL ROLE disponit_domene_eier;

-- Reservasjonen: utstedes av bestillingsflaten FØR opplasting. Taket er
-- kontraktens — kunden ber aldri om et tall, hun får kontraktens.
CREATE FUNCTION reserver_inndata(
    p_tenant TEXT, p_eiermodul TEXT, p_formaal TEXT, p_maks_bytes BIGINT)
RETURNS TABLE (inndata_id UUID, reservasjon_jti TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID; v_jti TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'reserver_inndata');
    -- Kjerne-PG, ingen pgcrypto (kjørerens egen regel): to UUID-er gir
    -- 64 hex-tegn entropi for engangs-jti-en.
    v_jti := replace(pg_catalog.gen_random_uuid()::text, '-', '')
             || replace(pg_catalog.gen_random_uuid()::text, '-', '');
    INSERT INTO public.inndata_artefakt
        (tenant, eiermodul, formaal, innholdstype, maks_bytes,
         reservasjon_jti, utloper)
    VALUES (p_tenant, p_eiermodul, p_formaal, 'application/zip',
            p_maks_bytes, v_jti, pg_catalog.now() + interval '1 hour')
    RETURNING public.inndata_artefakt.inndata_id INTO v_id;
    inndata_id := v_id; reservasjon_jti := v_jti;
    RETURN NEXT;
END $$;

-- Lastingen: API-et har strømmet, målt, hashet og kryptert — HER møter
-- målingen deklarasjonen, og reservasjonen forbrukes (jti er engangs:
-- raden EIES av jti-en, og overgangen kan bare skje én gang).
CREATE FUNCTION registrer_inndata_lastet(
    p_tenant TEXT, p_jti TEXT, p_faktiske_bytes BIGINT,
    p_sha256 TEXT, p_key_id TEXT, p_nonce BYTEA, p_sti TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'registrer_inndata_lastet');
    SELECT * INTO r FROM public.inndata_artefakt
     WHERE tenant = p_tenant AND reservasjon_jti = p_jti
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'inndata: ukjent reservasjon'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF r.status <> 'reservert' THEN
        RAISE EXCEPTION 'inndata: reservasjonen er alt forbrukt (%)',
            r.status USING ERRCODE = 'unique_violation';
    END IF;
    IF pg_catalog.now() > r.utloper THEN
        RAISE EXCEPTION 'inndata: reservasjonen er utløpt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_faktiske_bytes IS NULL OR p_faktiske_bytes <= 0
       OR p_faktiske_bytes > r.maks_bytes THEN
        RAISE EXCEPTION 'inndata: % byte bryter deklarasjonen (maks %)',
            p_faktiske_bytes, r.maks_bytes
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.inndata_artefakt
       SET status = 'lastet', faktiske_bytes = p_faktiske_bytes,
           innhold_sha256 = p_sha256, key_id = p_key_id, nonce = p_nonce,
           lager_sti = p_sti, lastet_ts = pg_catalog.now()
     WHERE tenant = p_tenant AND inndata_id = r.inndata_id;
    RETURN r.inndata_id;
END $$;

-- Bindingen: kalles i BESTILLINGENS transaksjon. Én bunt, ett oppdrag,
-- én gang — og modulen som skal lese må være den bunten ble reservert
-- for.
CREATE FUNCTION bind_inndata(
    p_tenant TEXT, p_inndata_id UUID, p_oppdrag_id BIGINT,
    p_eiermodul TEXT)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'bind_inndata');
    SELECT * INTO r FROM public.inndata_artefakt
     WHERE tenant = p_tenant AND inndata_id = p_inndata_id
     FOR UPDATE;
    IF NOT FOUND OR r.status <> 'lastet' THEN
        RAISE EXCEPTION 'inndata: % er ikke en lastet bunt',
            p_inndata_id USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF r.eiermodul IS DISTINCT FROM p_eiermodul THEN
        RAISE EXCEPTION 'inndata: bunten er reservert for %, ikke %',
            r.eiermodul, p_eiermodul
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.inndata_artefakt
       SET status = 'bundet', oppdrag_id = p_oppdrag_id,
           bundet_ts = pg_catalog.now()
     WHERE tenant = p_tenant AND inndata_id = p_inndata_id;
END $$;

REVOKE ALL ON FUNCTION reserver_inndata(TEXT, TEXT, TEXT, BIGINT)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION registrer_inndata_lastet(TEXT, TEXT, BIGINT, TEXT,
    TEXT, BYTEA, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION bind_inndata(TEXT, UUID, BIGINT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION reserver_inndata(TEXT, TEXT, TEXT, BIGINT)
    TO disponit;
GRANT EXECUTE ON FUNCTION registrer_inndata_lastet(TEXT, TEXT, BIGINT,
    TEXT, TEXT, BYTEA, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION bind_inndata(TEXT, UUID, BIGINT, TEXT)
    TO disponit;

RESET ROLE;

REVOKE ALL ON inndata_artefakt FROM PUBLIC;
GRANT SELECT ON inndata_artefakt TO disponit;
