-- 216 — M-43: KRAVHENDELSEN BÆRER GRENSENE SOM BLE SATT (215-formen).
--
-- Målt i M-43-fasiten 19.11.2026: `m43_sett_krav` skrev hendelsen
-- `sett_krav` med bare {versjon}. Loggen kunne si AT kravene ble satt,
-- ikke HVA de ble satt til — og første krav i to tenanter delte
-- `input_hash`. Detaljen bærer nå de fire grensene kunden selv valgte.
-- Ingenting annet i døra endres.

SET LOCAL ROLE disponit_telefoni_eier;

CREATE OR REPLACE FUNCTION m43_sett_krav(p_tenant TEXT, p_terskel INT,
                              p_identfrist_sek INT,
                              p_eskaleringsfrist INT,
                              p_samtaletak_timer INT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm43_sett_krav');
    SELECT coalesce(max(kravversjon), 0) + 1 INTO v_versjon
      FROM public.telefonikrav WHERE tenant = p_tenant;
    INSERT INTO public.telefonikrav
        (tenant, kravversjon, sikkerhetsterskel_bp,
         identifikasjonsfrist_sek, eskaleringsfrist_dogn,
         samtaletak_timer, satt_av)
    VALUES (p_tenant, v_versjon, p_terskel, p_identfrist_sek,
            p_eskaleringsfrist, p_samtaletak_timer, p_aktor);
    PERFORM public.m43_evidens(p_tenant, NULL, 'sett_krav', p_aktor,
        jsonb_build_object('versjon', v_versjon,
                           'sikkerhetsterskel_bp', p_terskel,
                           'identifikasjonsfrist_sek', p_identfrist_sek,
                           'eskaleringsfrist_dogn', p_eskaleringsfrist,
                           'samtaletak_timer', p_samtaletak_timer));
    RETURN v_versjon;
END $$;

RESET ROLE;
