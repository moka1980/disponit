-- 215 — M-7: KRAVHENDELSEN BÆRER GRENSENE SOM BLE SATT.
--
-- Målt 19.11.2026 (ADR-002, M-7s fasit): `m7_sett_krav` skrev hendelsen
-- `sett_krav` med bare {versjon, endret}. Revisjonsloggen kunne dermed
-- si AT grensene ble endret, men ikke HVA de ble satt til — og den første
-- kravhendelsen i to tenanter fikk samme `input_hash` uansett grenser.
--
-- Nå bærer detaljen de tre grensene. Det er tall kunden selv har valgt,
-- ingen personopplysninger, og nettopp det en revisor spør etter: «hvilken
-- referatfrist gjaldt da dette funnet ble reist?».
--
-- Ingenting annet i døra endres. Funksjonen eies av registerets eier og
-- erstattes som den.

SET LOCAL ROLE disponit_mote_eier;

CREATE OR REPLACE FUNCTION m7_sett_krav(p_tenant TEXT, p_referatfrist INT,
                             p_aksjonsfrist INT, p_terskel INT,
                             p_aktor TEXT, p_nokkel TEXT)
RETURNS TABLE (referatfrist_dogn INT, aksjonsfrist_dogn INT,
               sikkerhetsterskel_bp INT, versjon INT, endret BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_rad public.motekrav%ROWTYPE;
    v_endret BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm7_sett_krav');
    IF p_nokkel IS NULL OR btrim(p_nokkel) = '' THEN
        RAISE EXCEPTION 'm7_sett_krav: idempotensnøkkel mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- LÅS FØRST, LES ETTERPÅ (klynge 6s lærdom).
    SELECT * INTO v_rad FROM public.motekrav
     WHERE tenant = p_tenant FOR UPDATE;
    IF FOUND AND v_rad.siste_nokkel IS NOT DISTINCT FROM p_nokkel THEN
        RETURN QUERY SELECT v_rad.referatfrist_dogn,
                            v_rad.aksjonsfrist_dogn,
                            v_rad.sikkerhetsterskel_bp,
                            v_rad.versjon, false;
        RETURN;
    END IF;
    v_endret := NOT FOUND
        OR v_rad.referatfrist_dogn IS DISTINCT FROM p_referatfrist
        OR v_rad.aksjonsfrist_dogn IS DISTINCT FROM p_aksjonsfrist
        OR v_rad.sikkerhetsterskel_bp IS DISTINCT FROM p_terskel;
    INSERT INTO public.motekrav
        (tenant, referatfrist_dogn, aksjonsfrist_dogn,
         sikkerhetsterskel_bp, versjon, siste_nokkel, satt_av)
    VALUES (p_tenant, p_referatfrist, p_aksjonsfrist, p_terskel, 1,
            p_nokkel, p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        referatfrist_dogn = EXCLUDED.referatfrist_dogn,
        aksjonsfrist_dogn = EXCLUDED.aksjonsfrist_dogn,
        sikkerhetsterskel_bp = EXCLUDED.sikkerhetsterskel_bp,
        -- VERSJONEN ØKER BARE VED EN EKTE ENDRING (119s lærdom).
        versjon = public.motekrav.versjon
                  + CASE WHEN v_endret THEN 1 ELSE 0 END,
        siste_nokkel = EXCLUDED.siste_nokkel,
        satt_ts = now(), satt_av = EXCLUDED.satt_av
    RETURNING * INTO v_rad;
    PERFORM public.m7_evidens(p_tenant, NULL, 'sett_krav', p_aktor,
        jsonb_build_object('versjon', v_rad.versjon,
                           'endret', v_endret,
                           'referatfrist_dogn', v_rad.referatfrist_dogn,
                           'aksjonsfrist_dogn', v_rad.aksjonsfrist_dogn,
                           'sikkerhetsterskel_bp',
                           v_rad.sikkerhetsterskel_bp));
    RETURN QUERY SELECT v_rad.referatfrist_dogn,
                        v_rad.aksjonsfrist_dogn,
                        v_rad.sikkerhetsterskel_bp, v_rad.versjon,
                        v_endret;
END $$;

RESET ROLE;
