-- 219 — M-35: KONTINUITETSDØRENE BLIR HENDELSER I EVIDENSKJEDEN.
--
-- ADR-002 D1 (19.11.2026): en tenantdør som endrer tilstand skriver én
-- hendelse i `revisjonslogg` med aktør og egen `input_hash` — «ingen
-- fritak». M-35s tidslinje er modulens EGEN append-only evidens for en
-- hendelse, men den sier ingenting om tjenestekartet eller kontaktene, og
-- den er ikke husets kjede. M-35 er klasse C (tenantregister uten sveip).
--
-- EVIDENSEN ER TRIGGERE, IKKE KALLSTEDER (211–214-formen). Hver dør ender
-- i en INSERT eller UPDATE; et gjenspill er et stille ja FØR skrivingen og
-- skriver derfor ingen hendelse.
--
-- AKTØREN er dørens egen der raden bærer den (`oppdatert_av`,
-- `bekreftet_av`, postens `aktor`). Kontaktraden bærer ingen
-- opprettende aktør, så den leses fra `disponit.aktor` (satt av API-et fra
-- autentiseringen); mangler den, skrives `ukjent`, og fasitens
-- `uten_aktor` fanger det.
--
-- HENDELSEN SELV logges gjennom tidslinjen: døra som åpner en hendelse
-- skriver alltid en `opprettet`-post, og lukkedøra en `lukket`-post, så
-- én trigger på posten dekker hele livsløpet uten dobbeltføring.
--
-- FUNKSJONENE EIES AV CLAIMEREN, som har INSERT på `revisjonslogg` fra
-- før (041). Tabellene er migratorens, så triggerne lages etter RESET.

SET LOCAL ROLE disponit_m37_claimer;

CREATE OR REPLACE FUNCTION m35_evidens_skriv(
    p_tenant TEXT, p_aktor TEXT, p_handling TEXT, p_ressurs UUID,
    p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm35_kontinuitet', 'handling', p_handling,
        'ressurs_id', p_ressurs::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, coalesce(nullif(p_aktor, ''), 'ukjent'),
            'm35_kontinuitet',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:kontinuitet', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m35_evidens_skriv(TEXT, TEXT, TEXT, UUID, JSONB)
    FROM PUBLIC;

CREATE OR REPLACE FUNCTION m35_tjeneste_evidens()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.m35_evidens_skriv(
        NEW.tenant, NEW.oppdatert_av,
        CASE TG_OP WHEN 'INSERT' THEN 'tjeneste.opprettet'
                   ELSE 'tjeneste.oppdatert' END,
        NEW.tjeneste_id,
        jsonb_build_object('referent', NEW.referent_type || ':'
                                       || NEW.referent_id,
                           'kritikalitet', NEW.kritikalitet,
                           'rto_maal_s', NEW.rto_maal_s,
                           'rpo_maal_s', NEW.rpo_maal_s,
                           'playbook_ref', NEW.playbook_ref,
                           'kontaktrolle', NEW.kontaktrolle,
                           'oppdatert_ts', NEW.oppdatert_ts));
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION m35_kontakt_evidens()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM public.m35_evidens_skriv(
            NEW.tenant, current_setting('disponit.aktor', true),
            'kontakt.opprettet', NEW.kontakt_id,
            jsonb_build_object('rolle', NEW.rolle,
                               'prioritet', NEW.prioritet,
                               'bruker_id', NEW.bruker_id));
    ELSIF NEW.bekreftet_ts IS DISTINCT FROM OLD.bekreftet_ts THEN
        PERFORM public.m35_evidens_skriv(
            NEW.tenant, NEW.bekreftet_av, 'kontakt.bekreftet',
            NEW.kontakt_id,
            jsonb_build_object('bekreftet_ts', NEW.bekreftet_ts));
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION m35_post_evidens()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    -- Teksten logges ikke (den kan bære PII i en post); bare at posten
    -- finnes, hvilken type den har og hvem som skrev den.
    PERFORM public.m35_evidens_skriv(
        NEW.tenant, NEW.aktor, 'hendelse.' || NEW.posttype,
        NEW.hendelse_id,
        jsonb_build_object('post_id', NEW.post_id::text,
                           'posttype', NEW.posttype));
    RETURN NEW;
END $$;

RESET ROLE;

DROP TRIGGER IF EXISTS m35_tjeneste_evidens ON kontinuitet_tjeneste;
CREATE TRIGGER m35_tjeneste_evidens
    AFTER INSERT OR UPDATE ON kontinuitet_tjeneste
    FOR EACH ROW EXECUTE FUNCTION m35_tjeneste_evidens();

DROP TRIGGER IF EXISTS m35_kontakt_evidens ON beredskapskontakt;
CREATE TRIGGER m35_kontakt_evidens
    AFTER INSERT OR UPDATE OF bekreftet_ts ON beredskapskontakt
    FOR EACH ROW EXECUTE FUNCTION m35_kontakt_evidens();

DROP TRIGGER IF EXISTS m35_post_evidens ON kontinuitetshendelse_post;
CREATE TRIGGER m35_post_evidens
    AFTER INSERT ON kontinuitetshendelse_post
    FOR EACH ROW EXECUTE FUNCTION m35_post_evidens();
