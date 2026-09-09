-- =====================================================================
-- 151 — M-23: LESEDØRA FOR AVSENDERPROFILEN
-- =====================================================================
--
-- ARC B, PR 6 (flaten). 149 ga tenanten en avsenderprofil på
-- purreplan-raden og en skrivedør. Flaten må kunne VISE den — navnet
-- purringene sendes i og adressen kunden svarer til — så eieren ser hva
-- kunden ser, før den første purringen går ut. Én rad, tenantbundet.
-- ---------------------------------------------------------------------
SET LOCAL ROLE disponit_fordring_eier;

CREATE FUNCTION m23_avsenderen(p_tenant TEXT)
RETURNS TABLE(avsender_navn TEXT, svar_til TEXT, oppdatert TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_avsenderen');
    RETURN QUERY
    SELECT p.avsender_navn, p.svar_til, p.oppdatert
      FROM public.purreplan p
     WHERE p.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m23_avsenderen(TEXT) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_avsenderen(TEXT) TO disponit';
    END IF;
END $$;
RESET ROLE;
