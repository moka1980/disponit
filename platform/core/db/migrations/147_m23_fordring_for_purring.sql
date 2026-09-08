-- =====================================================================
-- 147 — M-23: DØRA BESTILLINGSVEIEN SPØR FØR EN PURRING BESTILLES
-- =====================================================================
--
-- ARC B, PR 2: `purring.send` er nå en bestillingstype. Bestillingsveien
-- må vite tre ting om fordringen FØR beslutningen brenner kvote: er den
-- åpen, hvor mange dager over forfall står den, og hvilket trinn i
-- purreplanen er det neste — og hva heter handlingen der. Runtime har
-- ingen tabellrettigheter (SP-7), så det er en dør: lesende, én rad,
-- eid av fordring_eier som resten av 104.
--
-- Svaret er OGSÅ grunnlaget for attestasjonene bestillingsveien signerer
-- som `v_fordring` (`forfall_passert_dager` er MÅLT her, ikke oppgitt av
-- bestilleren). Trinnet er dørens «neste», aldri bestillerens valg.
-- ---------------------------------------------------------------------
SET LOCAL ROLE disponit_fordring_eier;

CREATE FUNCTION m23_fordring_for_purring(p_tenant TEXT, p_fordring_id UUID)
RETURNS TABLE(status TEXT, fakturanummer TEXT, rest_ore BIGINT,
              dogn_over_forfall INT, trinn INT,
              neste_trinn INT, neste_navn TEXT, neste_handling TEXT,
              neste_dogn_etter_forfall INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_fordring_for_purring');
    RETURN QUERY
    SELECT f.status, f.fakturanummer, f.belop_ore - f.betalt_ore,
           (current_date - f.forfall)::int, f.trinn,
           t.trinn_nr, t.navn, t.handling, t.dogn_etter_forfall
      FROM public.fordring f
      LEFT JOIN public.purretrinn t
        ON t.tenant = f.tenant AND t.trinn_nr = f.trinn + 1
     WHERE f.tenant = p_tenant AND f.fordring_id = p_fordring_id;
END $$;
REVOKE ALL ON FUNCTION m23_fordring_for_purring(TEXT, UUID) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_fordring_for_purring(TEXT,'
            ' UUID) TO disponit';
    END IF;
END $$;
RESET ROLE;
