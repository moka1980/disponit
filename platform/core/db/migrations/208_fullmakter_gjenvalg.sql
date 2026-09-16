-- 208 — GJENVALG AV FULLMAKTER PÅ EN URØRT BOOTSTRAP-POLICY.
--
-- 207 lot den som registrerer velge fullmaktene der policyen fødes. De
-- som registrerte seg FØR 207 (wcagvakt, 14/9) står på bransjemalen slik
-- den kom — uten `kundeservice.svar.send` — og kan ikke utvide etterpå:
-- V6 krever to attestasjoner, én fra en annen enn forfatteren, og et
-- enkeltpersonfirma har ikke det.
--
-- DØREN HER ER BOOTSTRAP-DØREN ÉN GANG TIL, og bare så lenge det
-- fortsatt er sant at INGEN har attestert noe: tenanten har nøyaktig én
-- policyrad, den er aktiv og bærer `aktiveringskilde = 'bootstrap'`, og
-- det finnes verken utkast eller attestasjoner. Da er raden det samme
-- startpunktet som ved registreringen, og valget er det samme valget —
-- tatt litt senere. Den dagen firmaet har gått den styrte veien én gang,
-- er det den veien som gjelder, og døren nekter (samme dom som
-- `firma_bootstrap_policy`: «bruk den styrte veien»).
--
-- Innholdet byttes i RADEN. 047s vakter fryser `generasjon` og
-- `aktivert_av_operasjon` — begge står; `bootstrap_aktivert_ts` flyttes
-- så det synes at valget ble gjort om.

CREATE OR REPLACE FUNCTION firma_bootstrap_gjenvalg(p_tenant TEXT,
                                                    p_hash TEXT,
                                                    p_innhold JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_rad RECORD; v_n INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'firma_bootstrap_gjenvalg');
    -- SAMME LÅS SOM DEN STYRTE VEIEN (policyadmin: «under policy_hode-
    -- låsen»): et utkast som opprettes samtidig skal enten se det nye
    -- innholdet, eller stenge døren — aldri glippe mellom sjekk og
    -- skriving (CodeRabbit).
    PERFORM 1 FROM public.policy_hode h WHERE h.tenant = p_tenant
       FOR UPDATE;
    SELECT count(*) INTO v_rader FROM public.policyer p
     WHERE p.tenant = p_tenant;
    IF v_rader <> 1 THEN
        RAISE EXCEPTION 'firma_bootstrap_gjenvalg: % har % policyrader — '
                        'bruk den styrte veien', p_tenant, v_rader
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT p.policy_id, p.versjon, p.aktiv, p.aktiveringskilde INTO v_rad
      FROM public.policyer p WHERE p.tenant = p_tenant;
    IF NOT v_rad.aktiv OR v_rad.aktiveringskilde IS DISTINCT FROM 'bootstrap' THEN
        RAISE EXCEPTION 'firma_bootstrap_gjenvalg: % sin policy er ikke en '
                        'aktiv bootstrap-rad — bruk den styrte veien', p_tenant
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM public.policyutkast u WHERE u.tenant = p_tenant)
       OR EXISTS (SELECT 1 FROM public.aktiveringsattestasjon a
                   WHERE a.tenant = p_tenant) THEN
        RAISE EXCEPTION 'firma_bootstrap_gjenvalg: % har gått den styrte '
                        'veien — bruk den', p_tenant
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    -- Identiteten (policy_id, versjon) må være radens: gjenvalget bytter
    -- fullmaktene, ikke malen.
    IF (p_innhold #>> '{meta,policy_id}') IS DISTINCT FROM v_rad.policy_id
       OR (p_innhold #>> '{meta,versjon}') IS DISTINCT FROM v_rad.versjon THEN
        RAISE EXCEPTION 'firma_bootstrap_gjenvalg: innholdet bærer en annen '
                        'policy (%/%) enn raden (%/%)',
                        p_innhold #>> '{meta,policy_id}',
                        p_innhold #>> '{meta,versjon}',
                        v_rad.policy_id, v_rad.versjon
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.policyer
       SET innhold = p_innhold, innholds_hash = p_hash,
           bootstrap_aktivert_ts = now()
     WHERE tenant = p_tenant AND aktiv;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    IF v_n <> 1 THEN
        RAISE EXCEPTION 'firma_bootstrap_gjenvalg: % rader oppdatert for %',
                        v_n, p_tenant
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
END $$;
REVOKE ALL ON FUNCTION firma_bootstrap_gjenvalg(TEXT, TEXT, JSONB) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        GRANT EXECUTE ON FUNCTION
            firma_bootstrap_gjenvalg(TEXT, TEXT, JSONB) TO disponit;
    END IF;
END $$;
