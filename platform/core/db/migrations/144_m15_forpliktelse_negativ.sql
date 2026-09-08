-- =====================================================================
-- 144 — M-15: EN FORPLIKTELSE MED POSITIVT BELØP ER EN TASTEFEIL
-- =====================================================================
--
-- 128: «Negativt er UT, positivt er INN — og fortegnet er en del av
-- registreringen, ikke noe modulen gjetter av typen.» Doktrinen holdt
-- ikke mot virkeligheten: på disponit.com (Fjordlys-kampanjen 8/9) ble
-- lønn, husleie, skatt og lån registrert positivt — slik et lønns-
-- eller regnskapssystem leverer dem — og prognosen viste et selskap
-- som tjente på lønnen sin, uten funn og uten advarsel (#429).
--
-- Døra nekter nå et positivt beløp for de seks forpliktelsestypene med
-- sin egen setning. `annet` kan fortsatt være begge deler. Eksisterende
-- rader røres ikke; de er registreringer noen gjorde.
--
-- Som eierrollen (128): CREATE OR REPLACE beholder GRANT-ene.
-- ---------------------------------------------------------------------
SET LOCAL ROLE disponit_likviditet_eier;

CREATE OR REPLACE FUNCTION m15_registrer_post(
    p_tenant TEXT, p_post_id UUID, p_posttype TEXT,
    p_beskrivelse TEXT, p_belop_ore BIGINT, p_forste_forfall DATE,
    p_gjentakelse TEXT, p_gjelder_til DATE, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_gml public.likviditetspost%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm15_registrer_post');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm15_registrer_post: en registrert forpliktelse'
            ' bærer navnet til den som satte tallet'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT * INTO v_gml FROM public.likviditetspost
     WHERE tenant = p_tenant AND post_id = p_post_id FOR UPDATE;
    IF FOUND THEN
        IF v_gml.posttype IS DISTINCT FROM p_posttype
           OR v_gml.beskrivelse IS DISTINCT FROM btrim(p_beskrivelse)
           OR v_gml.belop_ore IS DISTINCT FROM p_belop_ore
           OR v_gml.forste_forfall IS DISTINCT FROM p_forste_forfall
           OR v_gml.gjentakelse IS DISTINCT FROM p_gjentakelse THEN
            RAISE EXCEPTION 'm15_registrer_post: post % finnes med'
                ' annet innhold', p_post_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN false;
    END IF;

    -- FORTEGNET FOR EN FORPLIKTELSE. 128 lot fortegnet være en del av
    -- registreringen; på disponit.com ble lønn, husleie, skatt og lån
    -- registrert positivt, og prognosen viste et selskap som tjente på
    -- lønnen sin — uten funn (#429). Lønn, husleie, skatt, avgift,
    -- abonnement og lån ER utbetalinger; en positiv post av den typen
    -- er en tastefeil, ikke en innbetaling. `annet` beholder begge
    -- fortegn, som 128 sier.
    IF p_posttype IN ('lonn', 'husleie', 'skatt', 'avgift',
                      'abonnement', 'laan') AND p_belop_ore > 0 THEN
        RAISE EXCEPTION 'm15_registrer_post: % er en utbetaling —'
            ' beløpet må være negativt (i øre). Bruk posttypen «annet»'
            ' for en forventet innbetaling', p_posttype
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO public.likviditetspost
        (tenant, post_id, posttype, beskrivelse, belop_ore,
         forste_forfall, gjentakelse, gjelder_til, registrert_av)
    VALUES (p_tenant, p_post_id, p_posttype, btrim(p_beskrivelse),
            p_belop_ore, p_forste_forfall, p_gjentakelse,
            p_gjelder_til, btrim(p_aktor));
    PERFORM public.m15_evidens(p_tenant, p_post_id,
        'post_registrert', btrim(p_aktor),
        jsonb_build_object('posttype', p_posttype,
                           'gjentakelse', p_gjentakelse));
    RETURN true;
END $$;

RESET ROLE;
