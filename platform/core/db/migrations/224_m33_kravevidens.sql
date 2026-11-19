-- 224 — M-33: KRAVHENDELSEN BÆRER GRENSENE SOM BLE SATT (215–223-formen).
--
-- Målt 19.11.2026: `m33_sett_krav` skrev `sett_krav` med bare
-- {versjon, endret}. Loggen kunne si AT grensene ble satt, ikke HVA
-- horisonten, grunnlaget, målefristen og domsgrunnlaget ble satt til — og
-- første krav i to tenanter delte `input_hash`. Ingenting annet i døra
-- endres.

SET LOCAL ROLE disponit_prognose_eier;

CREATE OR REPLACE FUNCTION m33_sett_krav(p_tenant TEXT, p_horisont INT,
                              p_grunnlag INT, p_maalefrist INT,
                              p_domsgrunnlag INT, p_aktor TEXT,
                              p_nokkel TEXT)
RETURNS TABLE (horisont_uker INT, grunnlag_uker INT,
               maalefrist_dogn INT, domsgrunnlag_uker INT,
               versjon INT, endret BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_rad public.prognosekrav%ROWTYPE;
    v_endret BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm33_sett_krav');
    IF p_nokkel IS NULL OR btrim(p_nokkel) = '' THEN
        RAISE EXCEPTION 'm33_sett_krav: idempotensnøkkel mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- LÅS FØRST, LES ETTERPÅ (klynge 6s lærdom): en lesing før
    -- `FOR UPDATE` bruker transaksjonens snapshot, og to samtidige
    -- kall ville begge sett den gamle raden.
    SELECT * INTO v_rad FROM public.prognosekrav
     WHERE tenant = p_tenant FOR UPDATE;

    IF FOUND AND v_rad.siste_nokkel IS NOT DISTINCT FROM p_nokkel THEN
        RETURN QUERY SELECT v_rad.horisont_uker, v_rad.grunnlag_uker,
                            v_rad.maalefrist_dogn,
                            v_rad.domsgrunnlag_uker,
                            v_rad.versjon, false;
        RETURN;
    END IF;

    v_endret := NOT FOUND
        OR v_rad.horisont_uker IS DISTINCT FROM p_horisont
        OR v_rad.grunnlag_uker IS DISTINCT FROM p_grunnlag
        OR v_rad.maalefrist_dogn IS DISTINCT FROM p_maalefrist
        OR v_rad.domsgrunnlag_uker IS DISTINCT FROM p_domsgrunnlag;

    INSERT INTO public.prognosekrav
        (tenant, horisont_uker, grunnlag_uker, maalefrist_dogn,
         domsgrunnlag_uker, versjon, siste_nokkel, satt_av)
    VALUES (p_tenant, p_horisont, p_grunnlag, p_maalefrist,
            p_domsgrunnlag, 1, p_nokkel, p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        horisont_uker = EXCLUDED.horisont_uker,
        grunnlag_uker = EXCLUDED.grunnlag_uker,
        maalefrist_dogn = EXCLUDED.maalefrist_dogn,
        domsgrunnlag_uker = EXCLUDED.domsgrunnlag_uker,
        -- VERSJONEN ØKER BARE NÅR EN GRENSE FAKTISK ENDRET SEG. En
        -- versjon som økte for hvert gjenspill ville gjort
        -- funnhistorikken uleselig (M-51s lærdom 119).
        versjon = public.prognosekrav.versjon
                  + CASE WHEN v_endret THEN 1 ELSE 0 END,
        siste_nokkel = EXCLUDED.siste_nokkel,
        satt_ts = now(), satt_av = EXCLUDED.satt_av
    RETURNING * INTO v_rad;

    PERFORM public.m33_evidens(p_tenant, NULL, 'sett_krav', p_aktor,
        jsonb_build_object('versjon', v_rad.versjon,
                           'endret', v_endret,
                           'horisont_uker', v_rad.horisont_uker,
                           'grunnlag_uker', v_rad.grunnlag_uker,
                           'maalefrist_dogn', v_rad.maalefrist_dogn,
                           'domsgrunnlag_uker', v_rad.domsgrunnlag_uker));
    RETURN QUERY SELECT v_rad.horisont_uker, v_rad.grunnlag_uker,
                        v_rad.maalefrist_dogn,
                        v_rad.domsgrunnlag_uker, v_rad.versjon,
                        v_endret;
END $$;

RESET ROLE;
