-- 222 — M-29: KRAVHENDELSEN BÆRER GRENSENE SOM BLE SATT (215–221-formen).
--
-- Målt 19.11.2026: `m29_sett_krav` skrev `sett_krav` med bare
-- {kravversjon}. Loggen kunne si AT grensene ble satt, ikke HVA vinduet,
-- alvorsterskelen, fristen og signaltaket ble satt til — og første krav i
-- to tenanter delte `input_hash`. For en sikkerhetsmodul er det nettopp
-- terskelendringen noen vil lete etter i ettertid. Ingenting annet i døra
-- endres.

SET LOCAL ROLE disponit_hendelse_eier;

CREATE OR REPLACE FUNCTION m29_sett_krav(p_tenant TEXT,
                              p_korrelasjonsvindu_min INT,
                              p_alvorsterskel INT,
                              p_apen_hendelse_frist_dogn INT,
                              p_signaltak INT, p_av TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm29_sett_krav');
    SELECT coalesce(max(kravversjon), 0) + 1 INTO v_versjon
      FROM public.hendelseskrav WHERE tenant = p_tenant;
    INSERT INTO public.hendelseskrav
        (tenant, kravversjon, korrelasjonsvindu_min, alvorsterskel,
         apen_hendelse_frist_dogn, signaltak, satt_av)
    VALUES (p_tenant, v_versjon, p_korrelasjonsvindu_min,
            p_alvorsterskel, p_apen_hendelse_frist_dogn, p_signaltak,
            p_av);
    PERFORM public.m29_evidens(p_tenant, NULL, 'sett_krav', p_av,
        jsonb_build_object('kravversjon', v_versjon,
                           'korrelasjonsvindu_min', p_korrelasjonsvindu_min,
                           'alvorsterskel', p_alvorsterskel,
                           'apen_hendelse_frist_dogn',
                           p_apen_hendelse_frist_dogn,
                           'signaltak', p_signaltak));
    RETURN v_versjon;
END $$;

RESET ROLE;
