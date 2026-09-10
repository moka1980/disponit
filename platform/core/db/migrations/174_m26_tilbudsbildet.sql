-- 174 — M-26 (ARC B tilbud, PR 6): plattformens arm i ord, per tilbud.
-- 168-formen: én dør for de viste tilbudene — utløserens bestilling
-- (171) og sendingen (173) — lagt på lista og detaljen som TEKST. Flaten
-- utløser ingenting; den viser det som skjedde. Aldri adressen.
SET LOCAL ROLE disponit_prisbok_eier;

CREATE FUNCTION m26_tilbudsbildet(p_tenant TEXT, p_tilbud_ids UUID[])
RETURNS TABLE(tilbud_id UUID, utfall TEXT, oppdrag_id BIGINT,
              unntak_id BIGINT, bestilt_ts TIMESTAMPTZ,
              sendt_ts TIMESTAMPTZ, sendt_oppdrag_id BIGINT,
              sendt_malversjon TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_tilbudsbildet');
    RETURN QUERY
    SELECT t.tilbud_id, b.utfall, b.oppdrag_id, b.unntak_id, b.bestilt_ts,
           t.sendt_ts, t.sendt_oppdrag_id, t.sendt_malversjon
      FROM public.tilbud t
      LEFT JOIN public.tilbudsbestilling b
        ON b.tenant = t.tenant AND b.tilbud_id = t.tilbud_id
     WHERE t.tenant = p_tenant
       AND t.tilbud_id = ANY(coalesce(p_tilbud_ids, ARRAY[]::UUID[]))
       AND (b.tilbud_id IS NOT NULL OR t.sendt_ts IS NOT NULL);
END $$;
REVOKE ALL ON FUNCTION m26_tilbudsbildet(TEXT, UUID[]) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_tilbudsbildet(TEXT, UUID[])'
            ' TO disponit';
    END IF;
END $$;

RESET ROLE;
