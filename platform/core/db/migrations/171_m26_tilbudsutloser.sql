-- 171 — M-26 (ARC B tilbud, PR 3): tilbudsutløseren — kandidater,
-- bokføring av utfallet, lesedør. 162/166-formen.
--
-- Registeret bestiller når et menneske har godkjent; policyen avgjør.
-- Én bestilling per tilbud: utfallet bokføres i `tilbudsbestilling` FØR
-- neste runde kan se tilbudet igjen. Ble det brudd (en pris under
-- rabattgrensen, en erstattet klausul, summen over taket), er saken i
-- unntakskøen et menneskes — et rettet tilbud er et NYTT tilbud.
CREATE TABLE tilbudsbestilling (
    tenant      TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    tilbud_id   UUID NOT NULL,
    nokkel      TEXT NOT NULL CHECK (length(nokkel) BETWEEN 8 AND 200),
    utfall      TEXT NOT NULL CHECK (
                    utfall IN ('tillat', 'brudd', 'stopp')
                    OR utfall LIKE 'feil:%'),
    oppdrag_id  BIGINT,
    unntak_id   BIGINT,
    request_id  TEXT NOT NULL CHECK (request_id ~ '[^[:space:]]'),
    detalj      JSONB NOT NULL DEFAULT '{}'::jsonb,
    bestilt_ts  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT tilbudsbestilling_pk PRIMARY KEY (tenant, tilbud_id),
    CONSTRAINT tilbudsbestilling_tilbud_fk FOREIGN KEY (tenant, tilbud_id)
        REFERENCES tilbud (tenant, tilbud_id),
    CONSTRAINT tilbudsbestilling_tillat_har_oppdrag
        CHECK (utfall <> 'tillat' OR oppdrag_id IS NOT NULL)
);
ALTER TABLE tilbudsbestilling ENABLE ROW LEVEL SECURITY;
ALTER TABLE tilbudsbestilling FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON tilbudsbestilling
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
GRANT SELECT, INSERT ON tilbudsbestilling TO disponit_prisbok_eier;

-- Kryss-tenant-døra under må se HVILKE tenanter som har godkjente tilbud
-- FØR den setter kontekst per tenant (102/106-formen): en SELECT-policy
-- for eierrollen som gjelder bare når INGEN tenantkontekst står.
CREATE POLICY m26_tilbud_tenantliste ON tilbud
    FOR SELECT TO disponit_prisbok_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

SET LOCAL ROLE disponit_prisbok_eier;

CREATE FUNCTION m26_tilbudskandidater(p_grense INT DEFAULT 50)
RETURNS TABLE(tenant TEXT, tilbud_id UUID)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_grense INT;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm26_tilbudskandidater: døra er KRYSS-TENANT og'
            ' kalles uten tenantkontekst'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_grense := greatest(least(coalesce(p_grense, 50), 500), 1);
    SELECT array_agg(DISTINCT t.tenant ORDER BY t.tenant) INTO v_tenanter
      FROM public.tilbud t WHERE t.status = 'godkjent';
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        RETURN QUERY
        SELECT t.tenant, t.tilbud_id
          FROM public.tilbud t
         WHERE t.tenant = v_t
           AND t.status = 'godkjent'
           AND t.gyldig_til >= current_date
           AND EXISTS (SELECT 1 FROM public.tilbudslinje l
                        WHERE l.tenant = t.tenant AND l.tilbud_id = t.tilbud_id)
           AND NOT EXISTS (
                SELECT 1 FROM public.tilbudsbestilling b
                 WHERE b.tenant = t.tenant AND b.tilbud_id = t.tilbud_id)
         ORDER BY t.avgjort_ts, t.tilbud_id
         LIMIT v_grense;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
END $$;
REVOKE ALL ON FUNCTION m26_tilbudskandidater(INT) FROM PUBLIC;

CREATE FUNCTION m26_bokfor_tilbudsbestilling(
    p_tenant TEXT, p_tilbud_id UUID, p_nokkel TEXT, p_utfall TEXT,
    p_oppdrag_id BIGINT, p_unntak_id BIGINT, p_rid TEXT, p_detalj JSONB)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_n INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_bokfor_tilbudsbestilling');
    INSERT INTO public.tilbudsbestilling
        (tenant, tilbud_id, nokkel, utfall, oppdrag_id, unntak_id,
         request_id, detalj)
    VALUES (p_tenant, p_tilbud_id, p_nokkel, p_utfall, p_oppdrag_id,
            p_unntak_id, p_rid, coalesce(p_detalj, '{}'::jsonb))
        ON CONFLICT ON CONSTRAINT tilbudsbestilling_pk DO NOTHING;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    IF v_n = 0 THEN
        RETURN false;
    END IF;
    PERFORM public.m26_evidens(
        p_tenant, p_tilbud_id, 'tilbud.bestilt', 'agent:tilbud',
        jsonb_build_object('utfall', p_utfall, 'oppdrag_id', p_oppdrag_id,
                           'unntak_id', p_unntak_id, 'nokkel', p_nokkel,
                           'request_id', p_rid));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m26_bokfor_tilbudsbestilling(TEXT, UUID, TEXT, TEXT,
    BIGINT, BIGINT, TEXT, JSONB) FROM PUBLIC;

CREATE FUNCTION m26_tilbudsbestillingen(p_tenant TEXT, p_tilbud_id UUID)
RETURNS TABLE(utfall TEXT, oppdrag_id BIGINT, unntak_id BIGINT,
              request_id TEXT, bestilt_ts TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_tilbudsbestillingen');
    RETURN QUERY
    SELECT b.utfall, b.oppdrag_id, b.unntak_id, b.request_id, b.bestilt_ts
      FROM public.tilbudsbestilling b
     WHERE b.tenant = p_tenant AND b.tilbud_id = p_tilbud_id;
END $$;
REVOKE ALL ON FUNCTION m26_tilbudsbestillingen(TEXT, UUID) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
               WHERE rolname = 'disponit_plan_arbeider') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_tilbudskandidater(INT)'
            ' TO disponit_plan_arbeider';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_bokfor_tilbudsbestilling('
            'TEXT, UUID, TEXT, TEXT, BIGINT, BIGINT, TEXT, JSONB)'
            ' TO disponit_plan_arbeider';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_tilbudsbestillingen(TEXT,'
            ' UUID) TO disponit';
    END IF;
END $$;

RESET ROLE;
