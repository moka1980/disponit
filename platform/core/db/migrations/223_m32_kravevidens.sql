-- 223 — M-32: KRAVHENDELSEN BÆRER GRENSENE SOM BLE SATT (215–222-formen).
--
-- Målt 19.11.2026: `m32_sett_krav` skrev `sett_krav` med kravversjon og
-- selgerland. Loggen kunne si AT grensene ble satt, ikke HVA
-- kontrollgrensen og fristen ble satt til — og første krav i to tenanter
-- med samme selgerland delte `input_hash`. Grensen er det eneste som
-- lukker modulens ene funn, så endringen av den skal kunne leses.
-- Ingenting annet i døra endres.

SET LOCAL ROLE disponit_skatt_eier;

CREATE OR REPLACE FUNCTION m32_sett_krav(p_tenant TEXT, p_selgerland TEXT,
                              p_manuell_over_ore BIGINT,
                              p_kontrollfrist_dogn INT, p_av TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm32_sett_krav');
    -- SELGERLANDET MÅ HA EN PAKKE. Uten den kan ingen si hva som er
    -- innenlands, og «usikker jurisdiksjon» begynner allerede her.
    IF NOT EXISTS (SELECT 1 FROM public.landpakke l
                    WHERE l.landkode = p_selgerland) THEN
        RAISE EXCEPTION 'm32: ingen landpakke for selgerlandet %.'
                        ' En landpakke felles i en migrasjon, ikke her',
            p_selgerland;
    END IF;
    SELECT coalesce(max(kravversjon), 0) + 1 INTO v_versjon
      FROM public.skattekrav WHERE tenant = p_tenant;
    INSERT INTO public.skattekrav
        (tenant, kravversjon, selgerland, manuell_kontroll_over_ore,
         kontrollfrist_dogn, satt_av)
    VALUES (p_tenant, v_versjon, p_selgerland, p_manuell_over_ore,
            p_kontrollfrist_dogn, p_av);
    PERFORM public.m32_evidens(p_tenant, NULL, 'sett_krav', p_av,
        jsonb_build_object('kravversjon', v_versjon,
                           'selgerland', p_selgerland,
                           'manuell_kontroll_over_ore', p_manuell_over_ore,
                           'kontrollfrist_dogn', p_kontrollfrist_dogn));
    RETURN v_versjon;
END $$;

RESET ROLE;
