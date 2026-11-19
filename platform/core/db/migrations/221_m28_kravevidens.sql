-- 221 — M-28: KRAVHENDELSEN BÆRER GRENSENE SOM BLE SATT (215–218-formen).
--
-- Målt 19.11.2026: `m28_sett_krav` skrev `sett_krav` med kravversjon og
-- avsenderland. Loggen kunne si AT grensene ble satt, ikke HVA vekt- og
-- fristgrensene ble satt til — og første krav i to tenanter med samme
-- avsenderland delte `input_hash`. Detaljen bærer nå alle fire. Ingenting
-- annet i døra endres.

SET LOCAL ROLE disponit_transport_eier;

CREATE OR REPLACE FUNCTION m28_sett_krav(p_tenant TEXT, p_avsenderland TEXT,
                              p_maks_kolli_gram BIGINT,
                              p_manuell_over_gram BIGINT,
                              p_forslagsfrist_dogn INT, p_av TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm28_sett_krav');
    -- AVSENDERLANDET MÅ HA EN LANDPAKKE, av samme grunn som M-32s
    -- selgerland: uten leste regler for landet vi sender FRA, er
    -- ingenting av det som følger etterprøvbart.
    IF NOT EXISTS (SELECT 1 FROM public.landpakke l
                    WHERE l.landkode = p_avsenderland) THEN
        RAISE EXCEPTION 'm28: ingen landpakke for avsenderlandet %.'
                        ' En landpakke felles i en migrasjon, ikke her',
            p_avsenderland;
    END IF;
    SELECT coalesce(max(kravversjon), 0) + 1 INTO v_versjon
      FROM public.transportkrav WHERE tenant = p_tenant;
    INSERT INTO public.transportkrav
        (tenant, kravversjon, avsenderland, maks_kolli_gram,
         manuell_kontroll_over_gram, forslagsfrist_dogn, satt_av)
    VALUES (p_tenant, v_versjon, p_avsenderland, p_maks_kolli_gram,
            p_manuell_over_gram, p_forslagsfrist_dogn, p_av);
    PERFORM public.m28_evidens(p_tenant, NULL, 'sett_krav', p_av,
        jsonb_build_object('kravversjon', v_versjon,
                           'avsenderland', p_avsenderland,
                           'maks_kolli_gram', p_maks_kolli_gram,
                           'manuell_kontroll_over_gram', p_manuell_over_gram,
                           'forslagsfrist_dogn', p_forslagsfrist_dogn));
    RETURN v_versjon;
END $$;

RESET ROLE;
