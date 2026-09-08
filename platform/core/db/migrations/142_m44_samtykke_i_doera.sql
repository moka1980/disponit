-- =====================================================================
-- 142 — M-44: PLANEN NEKTER DET SVEIPEN BARE VILLE FUNNET
-- =====================================================================
--
-- `m44_legg_i_plan` (114) tok imot enhver aktiv mottaker og lot
-- sveipen (`m44_funnkandidater`) oppdage manglende, trukket eller
-- utløpt samtykke og brudd på frekvenstaket — men først den dagen
-- `planlagt_sendt` var nådd. På disponit.com (Fjordlys-kampanjen 8/9)
-- gikk en mottaker som hadde trukket samtykket rett inn i planen, og
-- en annen sto i tre kampanjer med tak 2 og svaret `i_periode: 3`.
--
-- Registerets doktrine er «samtykke og frekvenstak — ikke en
-- utsendingskø». En plan som bryter dem er ikke et funn å oppdage
-- senere; det er en handling døra skal nekte (#423). Sveipen består som
-- etterkontroll, fordi et samtykke kan trekkes ETTER planleggingen.
--
-- Dommen felles på det vi VET NÅ (siste samtykkehendelse per i dag),
-- målt mot SENDEDATOEN (gyldighet). Taket måles etter innsettingen,
-- og et nei ruller innsettingen tilbake med transaksjonen.
--
-- Som eierrollen: CREATE OR REPLACE beholder GRANT-ene fra 114.
-- ---------------------------------------------------------------------
SET LOCAL ROLE disponit_kampanje_eier;

CREATE OR REPLACE FUNCTION m44_legg_i_plan(
    p_tenant TEXT, p_kampanje_id UUID, p_mottaker_id UUID, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_aktiv BOOLEAN;
    v_status TEXT;
    v_dato DATE;
    v_periode INT;
    v_maks INT;
    v_gyldig INT;
    v_antall INT;
    v_tilstand TEXT;
    v_inntruffet DATE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_legg_i_plan');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT aktiv INTO v_aktiv FROM public.kampanjemottaker
     WHERE tenant = p_tenant AND mottaker_id = p_mottaker_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm44_legg_i_plan: ukjent mottaker %',
            p_mottaker_id USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_aktiv THEN
        RAISE EXCEPTION 'm44_legg_i_plan: mottakeren % er deaktivert',
            p_mottaker_id USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT status, planlagt_sendt INTO v_status, v_dato
      FROM public.kampanje
     WHERE tenant = p_tenant AND kampanje_id = p_kampanje_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm44_legg_i_plan: ukjent kampanje %',
            p_kampanje_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_status = 'avlyst' THEN
        RAISE EXCEPTION 'm44_legg_i_plan: kampanjen % er avlyst',
            p_kampanje_id USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- GRENSENE. Uten en satt grense gjelder 114s standarder (2 per 7
    -- døgn, samtykke gyldig 730 døgn) — de samme sveipen bruker.
    SELECT g.maks_per_periode, g.periode_dogn, g.samtykke_gyldig_dogn
      INTO v_maks, v_periode, v_gyldig
      FROM public.kampanjegrense g WHERE g.tenant = p_tenant;
    v_maks := coalesce(v_maks, 2);
    v_periode := coalesce(v_periode, 7);
    v_gyldig := coalesce(v_gyldig, 730);

    -- SAMTYKKET, SLIK VI KJENNER DET I DAG. Siste hendelse per i dag;
    -- en fremtidsdatert hendelse er ikke et samtykke ennå.
    SELECT s.tilstand, s.inntruffet INTO v_tilstand, v_inntruffet
      FROM public.samtykkehendelse s
     WHERE s.tenant = p_tenant AND s.mottaker_id = p_mottaker_id
       AND s.inntruffet <= current_date
     ORDER BY s.inntruffet DESC, s.registrert DESC
     LIMIT 1;
    IF v_tilstand IS NULL THEN
        RAISE EXCEPTION 'm44_legg_i_plan: mottakeren % har ikke gitt'
            ' samtykke — en mottaker uten samtykke planlegges ikke',
            p_mottaker_id USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_tilstand NOT IN ('gitt', 'bekreftet') THEN
        RAISE EXCEPTION 'm44_legg_i_plan: mottakeren % har samtykke i'
            ' tilstand «%» (siste hendelse %) — et trukket eller utløpt'
            ' samtykke er et nei',
            p_mottaker_id, v_tilstand, v_inntruffet
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_inntruffet + v_gyldig < v_dato THEN
        RAISE EXCEPTION 'm44_legg_i_plan: samtykket fra % er utløpt før'
            ' sendedatoen % (gyldig % døgn) — be om nytt samtykke'
            ' før planlegging',
            v_inntruffet, v_dato, v_gyldig
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO public.kampanjeplan
        (tenant, kampanje_id, mottaker_id, lagt_til_av)
    VALUES (p_tenant, p_kampanje_id, p_mottaker_id, p_aktor);

    SELECT count(*) INTO v_antall
      FROM public.kampanjeplan pl
      JOIN public.kampanje k ON k.tenant = pl.tenant
       AND k.kampanje_id = pl.kampanje_id
     WHERE pl.tenant = p_tenant AND pl.mottaker_id = p_mottaker_id
       AND k.status <> 'avlyst'
       AND k.planlagt_sendt > v_dato - v_periode
       AND k.planlagt_sendt <= v_dato;

    -- FREKVENSTAKET. Telles ETTER innsettingen så tallet er det samme
    -- som sveipen ville sett; et nei ruller innsettingen tilbake.
    IF v_antall > v_maks THEN
        RAISE EXCEPTION 'm44_legg_i_plan: mottakeren % ville stått i %'
            ' kampanjer i perioden på % døgn fram til %, taket er %',
            p_mottaker_id, v_antall, v_periode, v_dato, v_maks
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    PERFORM public.m44_evidens(p_tenant, p_mottaker_id,
        'lagt_i_kampanjeplan', p_aktor,
        jsonb_build_object('kampanje_id', p_kampanje_id,
                           'i_periode', v_antall,
                           'samtykke', v_tilstand,
                           'samtykke_dato', v_inntruffet));
    RETURN v_antall;
END $$;

RESET ROLE;
