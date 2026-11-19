-- 218 — M-36: KRAVHENDELSEN BÆRER GRENSENE SOM BLE SATT (215–217-formen).
--
-- Målt 19.11.2026: `m36_sett_krav` skrev `sett_krav` med bare
-- {versjon, endret}. Loggen kunne si AT grensene ble satt, ikke HVA de
-- ble satt til — og første krav i to tenanter delte `input_hash`.
-- Detaljen bærer nå de tre grensene kunden selv valgte. Ingenting annet
-- i døra endres.

SET LOCAL ROLE disponit_optimalisator_eier;

CREATE OR REPLACE FUNCTION m36_sett_krav(p_tenant TEXT, p_horisont INT,
                              p_maalefrist INT, p_maks INT,
                              p_aktor TEXT, p_nokkel TEXT)
RETURNS TABLE (horisont_uker INT, maalefrist_dogn INT,
               maks_i_rangering INT, versjon INT, endret BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_rad public.optimaliseringskrav%ROWTYPE;
    v_endret BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm36_sett_krav');
    IF p_nokkel IS NULL OR btrim(p_nokkel) = '' THEN
        RAISE EXCEPTION 'm36_sett_krav: idempotensnøkkel mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- LÅS FØRST, LES ETTERPÅ (klynge 6s lærdom).
    SELECT * INTO v_rad FROM public.optimaliseringskrav
     WHERE tenant = p_tenant FOR UPDATE;
    IF FOUND AND v_rad.siste_nokkel IS NOT DISTINCT FROM p_nokkel THEN
        RETURN QUERY SELECT v_rad.horisont_uker, v_rad.maalefrist_dogn,
                            v_rad.maks_i_rangering, v_rad.versjon,
                            false;
        RETURN;
    END IF;
    v_endret := NOT FOUND
        OR v_rad.horisont_uker IS DISTINCT FROM p_horisont
        OR v_rad.maalefrist_dogn IS DISTINCT FROM p_maalefrist
        OR v_rad.maks_i_rangering IS DISTINCT FROM p_maks;
    INSERT INTO public.optimaliseringskrav
        (tenant, horisont_uker, maalefrist_dogn, maks_i_rangering,
         versjon, siste_nokkel, satt_av)
    VALUES (p_tenant, p_horisont, p_maalefrist, p_maks, 1, p_nokkel,
            p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        horisont_uker = EXCLUDED.horisont_uker,
        maalefrist_dogn = EXCLUDED.maalefrist_dogn,
        maks_i_rangering = EXCLUDED.maks_i_rangering,
        -- VERSJONEN ØKER BARE VED EN EKTE ENDRING (119s lærdom).
        versjon = public.optimaliseringskrav.versjon
                  + CASE WHEN v_endret THEN 1 ELSE 0 END,
        siste_nokkel = EXCLUDED.siste_nokkel,
        satt_ts = now(), satt_av = EXCLUDED.satt_av
    RETURNING * INTO v_rad;
    PERFORM public.m36_evidens(p_tenant, NULL, 'sett_krav', p_aktor,
        jsonb_build_object('versjon', v_rad.versjon,
                           'endret', v_endret,
                           'horisont_uker', v_rad.horisont_uker,
                           'maalefrist_dogn', v_rad.maalefrist_dogn,
                           'maks_i_rangering', v_rad.maks_i_rangering));
    RETURN QUERY SELECT v_rad.horisont_uker, v_rad.maalefrist_dogn,
                        v_rad.maks_i_rangering, v_rad.versjon,
                        v_endret;
END $$;
RESET ROLE;
