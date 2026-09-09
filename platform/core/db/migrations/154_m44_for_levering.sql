-- =====================================================================
-- 154 — M-44: DØRA BESTILLINGSVEIEN SPØR FØR EN KAMPANJE LEVERES
-- =====================================================================
--
-- ARC B kampanje, PR 2: `kampanje.send` blir en bestillingstype — ÉN
-- mottaker i ÉN kampanje på sendedagen. Bestillingsveien må vite FØR
-- beslutningen brenner kvote: finnes kampanjen og er den ikke avlyst,
-- står mottakeren i planen, er mottakeren aktiv og har en adresse, har
-- kampanjen et innhold — og hva registeret VET om samtykket på
-- sendedatoen (siste hendelse t.o.m. den dagen, målt mot tenantens
-- gyldighetsvindu). Samtykket er POLICYENS vilkår (`samtykke_gyldig`,
-- `v_samtykke`): døra svarer med tilstanden, bestillingsveien attesterer
-- sant om den — og et trukket samtykke blir en sak, ikke en stille 409.
--
-- Runtime har ingen tabellrettigheter (SP-7): en lesende dør, én rad,
-- eid av kampanje_eier som resten av 114. Planarbeideren får EXECUTE
-- fordi bestillingsveien kjøres også av utløseren (PR 3).
-- ---------------------------------------------------------------------
SET LOCAL ROLE disponit_kampanje_eier;

CREATE FUNCTION m44_for_levering(p_tenant TEXT, p_kampanje_id UUID,
                                 p_mottaker_id UUID)
RETURNS TABLE(kampanje_status TEXT, planlagt_sendt DATE, har_innhold BOOLEAN,
              avmeldingslenke TEXT, emne TEXT, i_plan BOOLEAN,
              mottaker_aktiv BOOLEAN, har_kontakt BOOLEAN,
              mottaker_navn TEXT, samtykke_tilstand TEXT,
              samtykke_dato DATE, samtykke_gyldig_dogn INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_for_levering');
    RETURN QUERY
    SELECT k.status, k.planlagt_sendt, (k.tekst IS NOT NULL),
           k.avmeldingslenke, k.emne,
           EXISTS (SELECT 1 FROM public.kampanjeplan pl
                    WHERE pl.tenant = p_tenant
                      AND pl.kampanje_id = p_kampanje_id
                      AND pl.mottaker_id = p_mottaker_id),
           m.aktiv, (m.kontakt_kryptert IS NOT NULL), m.navn,
           s.tilstand, s.inntruffet,
           (SELECT g.samtykke_gyldig_dogn FROM public.kampanjegrense g
             WHERE g.tenant = p_tenant)
      FROM public.kampanje k
      LEFT JOIN public.kampanjemottaker m
        ON m.tenant = k.tenant AND m.mottaker_id = p_mottaker_id
      LEFT JOIN LATERAL (
            SELECT s2.tilstand, s2.inntruffet
              FROM public.samtykkehendelse s2
             WHERE s2.tenant = p_tenant AND s2.mottaker_id = p_mottaker_id
               AND s2.inntruffet <= greatest(k.planlagt_sendt, current_date)
             ORDER BY s2.inntruffet DESC, s2.registrert DESC
             LIMIT 1) s ON true
     WHERE k.tenant = p_tenant AND k.kampanje_id = p_kampanje_id;
END $$;
REVOKE ALL ON FUNCTION m44_for_levering(TEXT, UUID, UUID) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_for_levering(TEXT, UUID,'
            ' UUID) TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
               WHERE rolname = 'disponit_plan_arbeider') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_for_levering(TEXT, UUID,'
            ' UUID) TO disponit_plan_arbeider';
    END IF;
END $$;
RESET ROLE;
