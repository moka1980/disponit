-- 214 — M-8: SLOTER, TOKENER OG KANDIDATVALG BLIR HENDELSER I EVIDENSKJEDEN.
--
-- ADR-002 D1 (19.11.2026) og M-8s eget manifest: «slot-opprettelse/
-- -deaktivering og kandidatvalg står som hendelser med identitet i
-- revisjonskjeden». Før 214 sto de bare som driftshendelser i API-loggen.
-- M-8 er klasse C (tenantregister uten sveip).
--
-- EVIDENSEN ER TRIGGERE, IKKE KALLSTEDER (211/212/213-formen). Hver dør
-- ender i en INSERT eller en statusovergang; et gjenspill er et stille ja
-- FØR skrivingen og skriver derfor ingen hendelse.
--
-- AKTØREN:
--   * slot og token: `disponit.aktor` fra forespørselen (db/pg.py setter
--     den fra autentiseringen). Mangler den, skrives hendelsen likevel —
--     med aktøren `ukjent` — og fasitens `uten_aktor` fanger det; en
--     slot som ikke kan opprettes fordi loggen mangler et navn ville vært
--     en annen og verre feil.
--   * kandidatvalget: `kandidat:<kandidat_id>`. Kandidaten har ingen
--     innlogging; kapabilitetstokenet ER identiteten, og kandidat-id-en er
--     pseudonym (M-57). Ingen e-post, ingen navn.
--
-- `m8_velg_slot` setter selv tenantkonteksten fra tokenet før
-- innsettingen, så RLS på revisjonsloggen er oppfylt også på den
-- offentlige veien.
--
-- FUNKSJONENE EIES AV CLAIMEREN, som har INSERT på `revisjonslogg` fra
-- før (041). Tabellene er migratorens, så triggerne lages etter RESET.

SET LOCAL ROLE disponit_m37_claimer;

CREATE OR REPLACE FUNCTION m8_evidens_skriv(
    p_tenant TEXT, p_aktor TEXT, p_handling TEXT, p_ressurs UUID,
    p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm08_kalender', 'handling', p_handling,
        'ressurs_id', p_ressurs::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm08_kalender',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:tidsvalg', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m8_evidens_skriv(TEXT, TEXT, TEXT, UUID, JSONB)
    FROM PUBLIC;

CREATE OR REPLACE FUNCTION m8_slot_evidens()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT := coalesce(
    nullif(current_setting('disponit.aktor', true), ''), 'ukjent');
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM public.m8_evidens_skriv(
            NEW.tenant, v_aktor, 'slot.opprettet', NEW.slot_id,
            jsonb_build_object('prosess_id', NEW.prosess_id::text,
                               'start', NEW.start_ts, 'slutt', NEW.slutt_ts,
                               'kapasitet', NEW.kapasitet));
    ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
        PERFORM public.m8_evidens_skriv(
            NEW.tenant, v_aktor, 'slot.' || NEW.status, NEW.slot_id,
            jsonb_build_object('fra', OLD.status));
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION m8_token_evidens()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    -- Token-id-en er en kapabilitet og skrives ALDRI i loggen; ressursen
    -- er kandidaten, og detaljen er listen tokenet hører til.
    PERFORM public.m8_evidens_skriv(
        NEW.tenant,
        coalesce(nullif(current_setting('disponit.aktor', true), ''),
                 'ukjent'),
        'tidsvalgtoken.utstedt', NEW.kandidat_id,
        jsonb_build_object('liste_id', NEW.liste_id::text,
                           'utstedt', NEW.utstedt));
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION m8_valg_evidens()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.m8_evidens_skriv(
        NEW.tenant, 'kandidat:' || NEW.kandidat_id::text, 'slotvalg.valgt',
        NEW.kandidat_id,
        jsonb_build_object('prosess_id', NEW.prosess_id::text,
                           'slot_id', NEW.slot_id::text));
    RETURN NEW;
END $$;

RESET ROLE;

DROP TRIGGER IF EXISTS m8_slot_evidens ON m8_slot;
CREATE TRIGGER m8_slot_evidens
    AFTER INSERT OR UPDATE OF status ON m8_slot
    FOR EACH ROW EXECUTE FUNCTION m8_slot_evidens();

DROP TRIGGER IF EXISTS m8_token_evidens ON m8_tidsvalgtoken;
CREATE TRIGGER m8_token_evidens
    AFTER INSERT ON m8_tidsvalgtoken
    FOR EACH ROW EXECUTE FUNCTION m8_token_evidens();

DROP TRIGGER IF EXISTS m8_valg_evidens ON m8_slotvalg;
CREATE TRIGGER m8_valg_evidens
    AFTER INSERT ON m8_slotvalg
    FOR EACH ROW EXECUTE FUNCTION m8_valg_evidens();
