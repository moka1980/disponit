-- 168 — M-14 (ARC B bokføring, PR 5): flatens lesedør for bokføringen.
--
-- Flaten viser per faktura hva plattformens arm gjorde: utløserens
-- bestilling (handling, utfall, oppdrag eller sak, eller «over
-- policyens tak»), og — er den bokført — bilagets nummer og tidspunkt.
-- ÉN dør for de VISTE fakturaene, ikke ett kall per rad: listen er
-- opptil 200 fakturaer, og 200 dørkall for å tegne en tabell er en flate
-- som straffer den som har mange fakturaer. Døra tar id-ene lista viser
-- (CodeRabbit): en tenantvid liste med et tak ville stille utelatt
-- bokføringen på fakturaer nederst i en stor tenant.
SET LOCAL ROLE disponit_faktura_eier;

CREATE FUNCTION m14_bokforingsbildet(p_tenant TEXT, p_faktura_ids UUID[])
RETURNS TABLE(faktura_id UUID, bilagsnummer TEXT, bokfort_ts TIMESTAMPTZ,
              bokfort_oppdrag_id BIGINT, handling TEXT, utfall TEXT,
              oppdrag_id BIGINT, unntak_id BIGINT, bestilt_ts TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm14_bokforingsbildet');
    RETURN QUERY
    SELECT f.faktura_id, f.bilagsnummer, f.bokfort_ts, f.bokfort_oppdrag_id,
           b.handling, b.utfall, b.oppdrag_id, b.unntak_id, b.bestilt_ts
      FROM public.inngaaende_faktura f
      LEFT JOIN public.bokforingsbestilling b
        ON b.tenant = f.tenant AND b.faktura_id = f.faktura_id
     WHERE f.tenant = p_tenant
       AND f.faktura_id = ANY(coalesce(p_faktura_ids, ARRAY[]::UUID[]))
       AND (f.bilag_id IS NOT NULL OR b.faktura_id IS NOT NULL);
END $$;
REVOKE ALL ON FUNCTION m14_bokforingsbildet(TEXT, UUID[]) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m14_bokforingsbildet(TEXT, UUID[])'
            ' TO disponit';
    END IF;
END $$;

RESET ROLE;
