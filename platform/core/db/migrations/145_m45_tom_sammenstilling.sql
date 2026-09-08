-- =====================================================================
-- 145 — M-45: EN SAMMENSTILLING UTEN MÅLINGER GIR INGEN RAPPORT
-- =====================================================================
--
-- `m45_sammenstill` (136) laget rapport versjon 1 over en periode uten
-- en eneste måling: sum 0, estimatandel 0 (Fjordlys-kampanjen 8/9).
-- En tom rapport ser ut som et nullutslipp, og et nullutslipp er en
-- påstand — den sterkeste modulen kan komme med (#429).
--
-- Døra nekter nå sammenstilling når perioden ikke har gjeldende
-- målinger. Alt annet i funksjonen er 136s.
--
-- Som eierrollen (136): CREATE OR REPLACE beholder GRANT-ene.
-- ---------------------------------------------------------------------
SET LOCAL ROLE disponit_esg_eier;

CREATE OR REPLACE FUNCTION m45_sammenstill(p_tenant TEXT, p_rapport_id UUID,
                                p_periode_id UUID, p_aktor TEXT)
RETURNS TABLE (rapport_id UUID, versjon INT, sum_utslipp_kg NUMERIC,
               estimatandel_bp INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_periode RECORD;
    v_sum NUMERIC;
    v_est NUMERIC;
    v_antall INT;
    v_antall_est INT;
    v_paastander INT;
    v_andel INT;
    v_versjon INT;
    v_hash TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm45_sammenstill');
    SELECT * INTO v_periode FROM public.rapportperiode
     WHERE tenant = p_tenant AND periode_id = p_periode_id;
    IF v_periode IS NULL THEN
        RAISE EXCEPTION 'm45_sammenstill: ukjent rapportperiode %',
            p_periode_id;
    END IF;
    -- EN MÅLING SOM ER ERSTATTET TELLER IKKE. Den står i registeret —
    -- historikken overskrives ikke — men rapporten bærer det siste
    -- tallet.
    SELECT coalesce(sum(m.utslipp_kg), 0),
           coalesce(sum(m.utslipp_kg) FILTER (WHERE m.er_estimat), 0),
           count(*)::INT,
           count(*) FILTER (WHERE m.er_estimat)::INT
      INTO v_sum, v_est, v_antall, v_antall_est
      FROM public.esgmaaling m
     WHERE m.tenant = p_tenant AND m.periode_id = p_periode_id
       AND NOT EXISTS (SELECT 1 FROM public.esgmaaling r
                        WHERE r.tenant = p_tenant
                          AND r.erstatter_maaling_id = m.maaling_id);
    -- EN RAPPORT UTEN MÅLINGER ER IKKE EN RAPPORT. På disponit.com ga
    -- en sammenstilling over en tom periode «versjon 1, sum 0» — et
    -- nullutslipp ingen hadde målt (#429).
    IF v_antall = 0 THEN
        RAISE EXCEPTION 'm45_sammenstill: perioden % har ingen'
            ' gjeldende målinger — en rapport uten målinger ville sett'
            ' ut som et nullutslipp. Registrer målingene først',
            v_periode.merke USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT count(*)::INT INTO v_paastander FROM public.esgpaastand
     WHERE tenant = p_tenant AND periode_id = p_periode_id;
    v_andel := CASE WHEN v_sum = 0 THEN 0
                    ELSE round(v_est * 10000 / v_sum)::INT END;
    SELECT coalesce(max(r.versjon), 0) + 1 INTO v_versjon
      FROM public.esgrapport r
     WHERE r.tenant = p_tenant AND r.periode_id = p_periode_id;
    v_hash := encode(sha256(convert_to(
        jsonb_build_object('periode', p_periode_id,
                           'versjon', v_periode.standardversjon,
                           'sum', v_sum, 'estimat', v_est,
                           'maalinger', v_antall,
                           'paastander', v_paastander)::text,
        'UTF8')), 'hex');
    INSERT INTO public.esgrapport
        (tenant, rapport_id, periode_id, versjon, innholds_hash,
         sum_utslipp_kg, antall_maalinger, antall_estimater,
         estimatandel_bp, antall_paastander, standardversjon,
         sammenstilt_av)
    VALUES (p_tenant, p_rapport_id, p_periode_id, v_versjon, v_hash,
            v_sum, v_antall, v_antall_est, v_andel, v_paastander,
            v_periode.standardversjon, p_aktor);
    PERFORM public.m45_evidens(p_tenant, p_rapport_id, 'sammenstill',
        p_aktor, jsonb_build_object('versjon', v_versjon,
                                    'estimatandel_bp', v_andel));
    RETURN QUERY SELECT p_rapport_id, v_versjon, v_sum, v_andel;
END $$;

RESET ROLE;
