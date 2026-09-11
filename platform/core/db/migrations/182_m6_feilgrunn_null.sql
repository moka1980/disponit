-- 182 — M-6: NULL er heller ingen kode (CodeRabbit, svararmen).
--
-- 181s vakt leste `IF p_grunn !~ '^[a-z][a-z0-9_]{0,63}$'`, og i SQL er
-- `NULL !~ mønster` verken sant eller usant — den er NULL. En IF på NULL
-- kjører ikke grenen sin, så et NULL-kall gled RETT FORBI vakten og
-- skrev `feilgrunn = NULL` på et utkast merket `feilet`.
--
-- HVA DET HADDE KOSTET: flaten viser feilgrunnen bare når den finnes
-- (`u.status === "feilet" && u.feilgrunn`), så raden hadde stått som
-- feilet UTEN å si hvorfor — den eneste tilstanden der brukeren trenger
-- et ord mest. Ingen kaller sender NULL i dag (`plan/epost.py` bygger
-- alltid en streng), så dette er ikke en levende feil. Men vakten er
-- selve stedet der det skal være umulig, og en vakt som er tett «så
-- lenge kallerne oppfører seg» er ingen vakt.
--
-- Funksjonen er claimer-eid, så den erstattes under samme rolle som
-- skapte den. Bare vakten endres; resten av kroppen står ordrett.
SET LOCAL ROLE disponit_m37_claimer;

CREATE OR REPLACE FUNCTION m6_svar_feilet(p_tenant TEXT, p_utkast_id UUID,
                                          p_grunn TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_melding UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm6_svar_feilet');
    IF p_grunn IS NULL OR p_grunn !~ '^[a-z][a-z0-9_]{0,63}$' THEN
        RAISE EXCEPTION 'm6_svar_feilet: grunnen er en KODE, ikke en'
            ' leverandørtekst' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT u.status, u.melding_id INTO v_status, v_melding
      FROM public.epost_utkast u
     WHERE u.tenant = p_tenant AND u.utkast_id = p_utkast_id FOR UPDATE;
    IF NOT FOUND OR v_status <> 'sendes' THEN
        RETURN false;
    END IF;
    UPDATE public.epost_utkast
       SET status = 'feilet', feilgrunn = p_grunn
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id;
    PERFORM public.m6_evidens(p_tenant, v_melding, 'epost.svar_feilet',
                              p_aktor,
                              jsonb_build_object('utkast_id', p_utkast_id,
                                                 'grunn', p_grunn));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m6_svar_feilet(TEXT, UUID, TEXT, TEXT) FROM PUBLIC;

-- EXECUTE-grantet til planarbeideren bor i `migrer.py` (PLAN_RETTIGHETER)
-- og settes der ved hver kjøring — `CREATE OR REPLACE` beholder det
-- uansett, men regelen står her fordi den er lett å glemme.

RESET ROLE;
