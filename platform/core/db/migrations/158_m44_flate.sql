-- =====================================================================
-- 158 — M-44: DET FLATEN SER — LEVERANSEN PER KAMPANJE
-- =====================================================================
--
-- ARC B kampanje, PR 6. To lesedører for administrasjonsflaten:
-- status per kampanje (hvor mange bestilt, tillatt, brudd, feil,
-- levert) i ETT kall for listen, og linjene per kampanje (mottakerens
-- referanse, navn og MASKE — aldri adressen) for detaljpanelet.
-- ---------------------------------------------------------------------
SET LOCAL ROLE disponit_kampanje_eier;

CREATE FUNCTION m44_leveransestatus(p_tenant TEXT)
RETURNS TABLE(kampanje_id UUID, bestilt INT, tillat INT, brudd INT,
              feil INT, levert INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_leveransestatus');
    RETURN QUERY
    SELECT k.kampanje_id,
           (SELECT count(*)::int FROM public.kampanjebestilling b
             WHERE b.tenant = k.tenant AND b.kampanje_id = k.kampanje_id),
           (SELECT count(*)::int FROM public.kampanjebestilling b
             WHERE b.tenant = k.tenant AND b.kampanje_id = k.kampanje_id
               AND b.utfall = 'tillat'),
           (SELECT count(*)::int FROM public.kampanjebestilling b
             WHERE b.tenant = k.tenant AND b.kampanje_id = k.kampanje_id
               AND b.utfall IN ('brudd', 'stopp')),
           (SELECT count(*)::int FROM public.kampanjebestilling b
             WHERE b.tenant = k.tenant AND b.kampanje_id = k.kampanje_id
               AND b.utfall LIKE 'feil:%'),
           (SELECT count(*)::int FROM public.kampanjelevering l
             WHERE l.tenant = k.tenant AND l.kampanje_id = k.kampanje_id)
      FROM public.kampanje k
     WHERE k.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m44_leveransestatus(TEXT) FROM PUBLIC;

CREATE FUNCTION m44_kampanjeleveringene(p_tenant TEXT, p_kampanje_id UUID)
RETURNS TABLE(mottaker_id UUID, ekstern_ref TEXT, navn TEXT,
              kontakt_maske TEXT, utfall TEXT, oppdrag_id BIGINT,
              unntak_id BIGINT, bestilt_ts TIMESTAMPTZ,
              levert_ts TIMESTAMPTZ, malversjon TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_kampanjeleveringene');
    RETURN QUERY
    SELECT b.mottaker_id, m.ekstern_ref, m.navn, m.kontakt_maske,
           b.utfall, b.oppdrag_id, b.unntak_id, b.bestilt_ts,
           l.levert_ts, l.malversjon
      FROM public.kampanjebestilling b
      JOIN public.kampanjemottaker m
        ON m.tenant = b.tenant AND m.mottaker_id = b.mottaker_id
      LEFT JOIN public.kampanjelevering l
        ON l.tenant = b.tenant AND l.kampanje_id = b.kampanje_id
       AND l.mottaker_id = b.mottaker_id
     WHERE b.tenant = p_tenant AND b.kampanje_id = p_kampanje_id
     ORDER BY b.bestilt_ts, b.mottaker_id;
END $$;
REVOKE ALL ON FUNCTION m44_kampanjeleveringene(TEXT, UUID) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_leveransestatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_kampanjeleveringene(TEXT,'
            ' UUID) TO disponit';
    END IF;
END $$;
RESET ROLE;
