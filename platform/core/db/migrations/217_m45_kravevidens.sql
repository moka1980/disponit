-- 217 — M-45: KRAVHENDELSEN BÆRER GRENSENE SOM BLE SATT (215/216-formen).
--
-- Målt 19.11.2026: `m45_sett_krav` skrev `sett_krav` med bare {versjon}.
-- Loggen kunne si AT kravene ble satt, ikke HVA de ble satt til — og
-- første krav i to tenanter delte `input_hash`. Detaljen bærer nå de tre
-- grensene kunden selv valgte. Ingenting annet i døra endres.

SET LOCAL ROLE disponit_esg_eier;

CREATE OR REPLACE FUNCTION m45_sett_krav(p_tenant TEXT, p_estimatterskel INT,
                              p_estimatfrist INT, p_kilde_dogn INT,
                              p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm45_sett_krav');
    SELECT coalesce(max(kravversjon), 0) + 1 INTO v_versjon
      FROM public.esgkrav WHERE tenant = p_tenant;
    INSERT INTO public.esgkrav
        (tenant, kravversjon, estimatterskel_bp, estimatfrist_dogn,
         kilde_gyldig_dogn, satt_av)
    VALUES (p_tenant, v_versjon, p_estimatterskel, p_estimatfrist,
            p_kilde_dogn, p_aktor);
    PERFORM public.m45_evidens(p_tenant, NULL, 'sett_krav', p_aktor,
        jsonb_build_object('versjon', v_versjon,
                           'estimatterskel_bp', p_estimatterskel,
                           'estimatfrist_dogn', p_estimatfrist,
                           'kilde_gyldig_dogn', p_kilde_dogn));
    RETURN v_versjon;
END $$;

RESET ROLE;
