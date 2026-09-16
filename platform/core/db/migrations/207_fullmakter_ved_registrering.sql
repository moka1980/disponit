-- 207 — GRUNNLEGGEREN FORVALTER FIRMAETS POLICY.
--
-- MÅLT I PROD 16/9: wcagvakt (selvregistrert 14/9) har bransjemalen v0.2.0
-- som aktiv policy. Den bærer ikke `kundeservice.svar.send` — handlingen
-- ligger i `policies/utvidelser/kundeservice-svar.yaml` og limes inn per
-- tenant gjennom den styrte veien. Den veien krever `policy:write` og
-- `policy:activate`, og den som registrerer fikk bare `admin`, som har
-- ingen av dem. Kunden trykket «Godkjenn svaret», planrunden bestilte
-- ingenting (policyen nevner ikke handlingen), og ingen sa fra.
--
-- Eiers ord (192): «man kan lett registrere en bedrift og SETTE POLICY og
-- resten skal skje automatisk.» Den som registrerer ER firmaet. Hun får
-- `policyforvalter` sammen med `admin` — samme rollepar oppsettsveien gir
-- eierens egne brukere. V6 (fire øyne) står urørt: en UTVIDELSE av
-- fullmaktene krever fortsatt to attestasjoner og én fra en annen enn
-- forfatteren — det er derfor fullmaktene velges VED REGISTRERINGEN
-- (api/firmaregistrering.py: `fullmakter`), som del av bootstrap-policyen
-- ingen har attestert, og ikke lures inn bakveien etterpå.
--
-- BACKFILL: de som alt har registrert seg får rollen nå. Bare tenanter
-- med en bootstrap-policy — det er de selvregistrerte, og ingen andre.

CREATE OR REPLACE FUNCTION firma_selvregistrer(p_tenant TEXT, p_navn TEXT,
                                               p_orgnummer TEXT,
                                               p_bruker_id TEXT,
                                               p_prove_dogn INT DEFAULT 30)
RETURNS DATE LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kontekst TEXT; v_frist DATE; v_antall INT; v_annen TEXT;
BEGIN
    v_kontekst := coalesce(current_setting('disponit.tenant', true), '');
    IF v_kontekst <> '_registrering' THEN
        RAISE EXCEPTION 'firma_selvregistrer: kalles kun fra '
                        'registreringskonteksten (sto i %)',
                        coalesce(nullif(v_kontekst, ''), '(ingen)')
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF p_bruker_id IS NULL OR btrim(p_bruker_id) = '' THEN
        RAISE EXCEPTION 'firma_selvregistrer: registranten mangler identitet'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.brukeridentitet b
                    WHERE b.bruker_id = p_bruker_id) THEN
        RAISE EXCEPTION 'firma_selvregistrer: ukjent registrant'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    PERFORM pg_advisory_xact_lock(
        hashtextextended('firma_selvregistrer:' || p_bruker_id, 0));
    v_antall := 0;
    FOR v_annen IN
        SELECT bt.tenant FROM public.bruker_tenant bt
         WHERE bt.bruker_id = p_bruker_id AND bt.tenant <> '_registrering'
    LOOP
        PERFORM set_config('disponit.tenant', v_annen, true);
        IF EXISTS (SELECT 1 FROM public.firma f
                    WHERE f.tenant = v_annen AND f.status <> 'stengt') THEN
            v_antall := v_antall + 1;
        END IF;
    END LOOP;
    PERFORM set_config('disponit.tenant', v_kontekst, true);
    IF v_antall >= public.firma_registreringstak() THEN
        RAISE EXCEPTION 'firma_selvregistrer: taket på % firmaer er nådd',
                        public.firma_registreringstak()
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    PERFORM set_config('disponit.tenant', p_tenant, true);
    v_frist := public.firma_registrer(p_tenant, p_navn, p_orgnummer,
                                      p_prove_dogn, 'bruker:' || p_bruker_id);
    -- DEN SOM REGISTRERER ER FIRMAET: admin OG policyforvalter (207).
    INSERT INTO public.brukermedlemskap (tenant, bruker_id, roller, aktiv)
         VALUES (p_tenant, p_bruker_id, ARRAY['admin', 'policyforvalter'],
                 true);
    PERFORM set_config('disponit.tenant', '_registrering', true);
    DELETE FROM public.brukermedlemskap
     WHERE tenant = '_registrering' AND bruker_id = p_bruker_id;
    PERFORM set_config('disponit.tenant', v_kontekst, true);
    RETURN v_frist;
END $$;
REVOKE ALL ON FUNCTION firma_selvregistrer(TEXT, TEXT, TEXT, TEXT, INT)
    FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        GRANT EXECUTE ON FUNCTION
            firma_selvregistrer(TEXT, TEXT, TEXT, TEXT, INT) TO disponit;
    END IF;
END $$;

-- ============================================================
-- BACKFILL i RLS-vinduet (189-formen): migrator har ingen tenantkontekst,
-- og både `brukermedlemskap` og `policyer` har FORCE RLS. Vinduet åpnes for
-- EIEREN, lukkes etter kontrollsteget, alt i samme transaksjon.
-- ============================================================
ALTER TABLE brukermedlemskap NO FORCE ROW LEVEL SECURITY;
ALTER TABLE policyer NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE v_kandidater INT; v_oppdatert INT;
BEGIN
    SELECT count(*) INTO v_kandidater
      FROM brukermedlemskap m
     WHERE m.aktiv AND 'admin' = ANY(m.roller)
       AND NOT ('policyforvalter' = ANY(m.roller))
       AND EXISTS (SELECT 1 FROM policyer p
                    WHERE p.tenant = m.tenant
                      AND p.aktiveringskilde = 'bootstrap');
    UPDATE brukermedlemskap m
       SET roller = array_append(m.roller, 'policyforvalter')
     WHERE m.aktiv AND 'admin' = ANY(m.roller)
       AND NOT ('policyforvalter' = ANY(m.roller))
       AND EXISTS (SELECT 1 FROM policyer p
                    WHERE p.tenant = m.tenant
                      AND p.aktiveringskilde = 'bootstrap');
    GET DIAGNOSTICS v_oppdatert = ROW_COUNT;
    -- KONTROLLSTEGET: det som skulle oppdateres ble oppdatert, og ingen
    -- andre. En backfill som ikke fant noe ser ut som en som ikke hadde noe
    -- å finne, så tallene sies høyt.
    IF v_oppdatert <> v_kandidater THEN
        RAISE EXCEPTION '207: % kandidater, % oppdatert', v_kandidater,
                        v_oppdatert;
    END IF;
    RAISE NOTICE '207: % selvregistrerte admin-medlemskap fikk policyforvalter',
                 v_oppdatert;
END $$;
ALTER TABLE policyer FORCE ROW LEVEL SECURITY;
ALTER TABLE brukermedlemskap FORCE ROW LEVEL SECURITY;
