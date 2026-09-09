-- =====================================================================
-- 162 — M-17: SVARUTLØSEREN — REGISTERET BESTILLER NÅR ET MENNESKE HAR
--       GODKJENT
-- =====================================================================
--
-- ARC B kundeservice, PR 3 (148/155-formen). Utløseren plukker hvert
-- godkjente utkast (160) på en åpen henvendelse som ikke står i
-- unntakskøen, har adresse (160) og en kanal plattformen kan svare i —
-- og bestiller `kundeservice.svar.send` (161) gjennom NØYAKTIG samme
-- bestillingsvei som et menneske. Policyen avgjør (godkjenning, DLP,
-- løfter, kjent mottaker); utløseren har ingen egen autoritet.
--
-- ÉN BESTILLING PER UTKAST. `svarbestilling` er utløserens hukommelse.
-- Ble det brudd (et fødselsnummer i teksten, et løfte om rabatt), er
-- saken et menneskes — utløseren prøver ikke igjen på samme utkast; et
-- rettet utkast er en NY rad (102) og en ny kandidat når det godkjennes.
-- Append-only ved grant, evidens gjennom `m17_evidens` — uten tekst.
--
-- Kjører i PLANARBEIDERENS prosess (048): samme tillitsnivå som purrings-
-- og kampanjeutløseren, ingen ny rolle, ingen ny credential.
-- ---------------------------------------------------------------------

CREATE TABLE svarbestilling (
    tenant         TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    henvendelse_id UUID NOT NULL,
    utkast_id      UUID NOT NULL,
    nokkel         TEXT NOT NULL CHECK (length(nokkel) BETWEEN 8 AND 200),
    utfall         TEXT NOT NULL CHECK (
                       utfall IN ('tillat', 'brudd', 'stopp')
                       OR utfall LIKE 'feil:%'),
    oppdrag_id     BIGINT,
    unntak_id      BIGINT,
    request_id     TEXT NOT NULL CHECK (request_id ~ '[^[:space:]]'),
    detalj         JSONB NOT NULL DEFAULT '{}'::jsonb,
    bestilt_ts     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT svarbestilling_pk PRIMARY KEY (tenant, utkast_id),
    CONSTRAINT svarbestilling_utkast_fk
        FOREIGN KEY (tenant, utkast_id)
        REFERENCES svarutkast (tenant, utkast_id),
    CONSTRAINT svarbestilling_henvendelse_fk
        FOREIGN KEY (tenant, henvendelse_id)
        REFERENCES henvendelse (tenant, henvendelse_id),
    CONSTRAINT svarbestilling_tillat_har_oppdrag
        CHECK (utfall <> 'tillat' OR oppdrag_id IS NOT NULL)
);
ALTER TABLE svarbestilling ENABLE ROW LEVEL SECURITY;
ALTER TABLE svarbestilling FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON svarbestilling
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
GRANT SELECT, INSERT ON svarbestilling TO disponit_kundeservice_eier;

SET LOCAL ROLE disponit_kundeservice_eier;

-- Kandidatdøra lister tenantene gjennom sveipens egen kryss-tenant-
-- policy på `henvendelse` (102 `m17_sveip_tenantliste`).
CREATE FUNCTION m17_svarkandidater(p_grense INT DEFAULT 50)
RETURNS TABLE(tenant TEXT, henvendelse_id UUID, utkast_id UUID)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_grense INT;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm17_svarkandidater: døra er KRYSS-TENANT og'
            ' kalles uten tenantkontekst'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_grense := greatest(least(coalesce(p_grense, 50), 500), 1);
    SELECT array_agg(DISTINCT h.tenant ORDER BY h.tenant) INTO v_tenanter
      FROM public.henvendelse h WHERE h.lukket_ts IS NULL;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        RETURN QUERY
        SELECT h.tenant, h.henvendelse_id, u.utkast_id
          FROM public.svarutkast u
          JOIN public.henvendelse h
            ON h.tenant = u.tenant AND h.henvendelse_id = u.henvendelse_id
         WHERE u.tenant = v_t
           AND u.status = 'godkjent'
           AND h.lukket_ts IS NULL
           AND h.unntak_id IS NULL
           AND h.kanal = 'epost'
           AND h.avsender_kryptert IS NOT NULL
           AND NOT EXISTS (
                SELECT 1 FROM public.svarbestilling b
                 WHERE b.tenant = u.tenant AND b.utkast_id = u.utkast_id)
         ORDER BY u.opprettet, u.utkast_id
         LIMIT v_grense;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
END $$;
REVOKE ALL ON FUNCTION m17_svarkandidater(INT) FROM PUBLIC;

CREATE FUNCTION m17_bokfor_svarbestilling(
    p_tenant TEXT, p_henvendelse_id UUID, p_utkast_id UUID, p_nokkel TEXT,
    p_utfall TEXT, p_oppdrag_id BIGINT, p_unntak_id BIGINT, p_rid TEXT,
    p_detalj JSONB)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_n INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_bokfor_svarbestilling');
    INSERT INTO public.svarbestilling
        (tenant, henvendelse_id, utkast_id, nokkel, utfall, oppdrag_id,
         unntak_id, request_id, detalj)
    VALUES (p_tenant, p_henvendelse_id, p_utkast_id, p_nokkel, p_utfall,
            p_oppdrag_id, p_unntak_id, p_rid, coalesce(p_detalj, '{}'::jsonb))
        ON CONFLICT ON CONSTRAINT svarbestilling_pk DO NOTHING;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    IF v_n = 0 THEN
        RETURN false;
    END IF;
    PERFORM public.m17_evidens(
        p_tenant, p_henvendelse_id, 'svar.bestilt', 'agent:kundeservice',
        jsonb_build_object('utkast_id', p_utkast_id::text,
                           'utfall', p_utfall, 'oppdrag_id', p_oppdrag_id,
                           'unntak_id', p_unntak_id, 'nokkel', p_nokkel,
                           'request_id', p_rid));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m17_bokfor_svarbestilling(TEXT, UUID, UUID, TEXT,
    TEXT, BIGINT, BIGINT, TEXT, JSONB) FROM PUBLIC;

CREATE FUNCTION m17_svarbestillingene(p_tenant TEXT, p_henvendelse_id UUID)
RETURNS TABLE(utkast_id UUID, utfall TEXT, oppdrag_id BIGINT,
              unntak_id BIGINT, request_id TEXT, bestilt_ts TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_svarbestillingene');
    RETURN QUERY
    SELECT b.utkast_id, b.utfall, b.oppdrag_id, b.unntak_id, b.request_id,
           b.bestilt_ts
      FROM public.svarbestilling b
     WHERE b.tenant = p_tenant AND b.henvendelse_id = p_henvendelse_id
     ORDER BY b.bestilt_ts, b.utkast_id;
END $$;
REVOKE ALL ON FUNCTION m17_svarbestillingene(TEXT, UUID) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
               WHERE rolname = 'disponit_plan_arbeider') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_svarkandidater(INT)'
            ' TO disponit_plan_arbeider';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_bokfor_svarbestilling('
            'TEXT, UUID, UUID, TEXT, TEXT, BIGINT, BIGINT, TEXT, JSONB)'
            ' TO disponit_plan_arbeider';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_svarbestillingene(TEXT,'
            ' UUID) TO disponit';
    END IF;
END $$;
RESET ROLE;
