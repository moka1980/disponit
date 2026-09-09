-- =====================================================================
-- 148 — M-23: PURRINGSUTLØSEREN — REGISTERET BESTILLER NÅR TRINNET
--       FORFALLER
-- =====================================================================
--
-- ARC B, PR 3. Sveipen (104) FINNER `trinn_forfalt` og flytter ingenting.
-- Utløseren er det som mangler mellom funnet og bestillingen: den
-- plukker fordringer der neste trinn er forfalt, som er åpne, og som
-- HAR en mottaker (146) — og bestiller `purring.send` gjennom NØYAKTIG
-- samme bestillingsvei som et menneske (plan-materialisererens grep, 044
-- §4). Policyen avgjør; utløseren har ingen egen autoritet.
--
-- ÉN BESTILLING PER FORDRING OG TRINN. `purringsbestilling` er
-- utløserens hukommelse: en rad per (fordring, trinn) med utfallet.
-- Ble det brudd (unntakskø), er saken et menneskes — utløseren prøver
-- ikke igjen på samme trinn. Radene er append-only ved grant
-- (fordringshendelse-mønsteret), og hver rad speiles i revisjonsloggen
-- gjennom `m23_evidens`.
--
-- Utløseren kjører i PLANARBEIDERENS prosess (`disponit_plan_arbeider`,
-- 048): samme tillitsnivå som materialisereren, ingen ny rolle, ingen ny
-- credential. Derfor får den rollen EXECUTE på de to dørene her OG på
-- 147-døra bestillingsveien spør gjennom.
-- ---------------------------------------------------------------------

CREATE TABLE purringsbestilling (
    tenant         TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    fordring_id    UUID NOT NULL,
    trinn          INT  NOT NULL CHECK (trinn >= 1),
    handling_trinn TEXT NOT NULL CHECK (handling_trinn ~ '[^[:space:]]'),
    nokkel         TEXT NOT NULL CHECK (length(nokkel) BETWEEN 8 AND 200),
    -- Bestillingsveiens dom, eller den terminale feilkoden (`feil:<kode>`)
    -- når veien svarte nei FØR beslutningen (ukjent, ikke klar …).
    utfall         TEXT NOT NULL CHECK (
                       utfall IN ('tillat', 'brudd', 'stopp')
                       OR utfall LIKE 'feil:%'),
    oppdrag_id     BIGINT,
    unntak_id      BIGINT,
    request_id     TEXT NOT NULL CHECK (request_id ~ '[^[:space:]]'),
    detalj         JSONB NOT NULL DEFAULT '{}'::jsonb,
    bestilt_ts     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT purringsbestilling_pk
        PRIMARY KEY (tenant, fordring_id, trinn),
    CONSTRAINT purringsbestilling_fordring_fk
        FOREIGN KEY (tenant, fordring_id)
        REFERENCES fordring (tenant, fordring_id),
    -- Et tillat uten oppdrag er ikke et tillat.
    CONSTRAINT purringsbestilling_tillat_har_oppdrag
        CHECK (utfall <> 'tillat' OR oppdrag_id IS NOT NULL)
);
ALTER TABLE purringsbestilling ENABLE ROW LEVEL SECURITY;
ALTER TABLE purringsbestilling FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON purringsbestilling
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
-- Append-only ved grant: verken UPDATE eller DELETE, som fordringshendelse.
GRANT SELECT, INSERT ON purringsbestilling TO disponit_fordring_eier;

-- Kandidatdøra leser funn og trinn PER TENANT etter at den har listet
-- tenantene gjennom sveipens egen kryss-tenant-policy på `fordring`
-- (104 §2b) — samme tre gjerder: bare eieren, bare SELECT, bare uten
-- tenantkontekst.
SET LOCAL ROLE disponit_fordring_eier;

CREATE FUNCTION m23_purringskandidater(p_grense INT DEFAULT 50)
RETURNS TABLE(tenant TEXT, fordring_id UUID, trinn INT,
              handling_trinn TEXT, dogn_over_forfall INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_grense INT; v_dag DATE;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm23_purringskandidater: døra er KRYSS-TENANT og'
            ' kalles uten tenantkontekst — en kaller som har satt en'
            ' kontekst ber om noe annet enn det denne funksjonen gjør'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Taket er PER TENANT: kapasitet forsinker, konsumerer aldri (044).
    v_grense := greatest(least(coalesce(p_grense, 50), 500), 1);
    v_dag := current_date;
    SELECT array_agg(DISTINCT f.tenant ORDER BY f.tenant) INTO v_tenanter
      FROM public.fordring f;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        RETURN QUERY
        SELECT f.tenant, f.fordring_id, t.trinn_nr, t.handling,
               (v_dag - f.forfall)::int
          FROM public.fordringsfunn ff
          JOIN public.fordring f
            ON f.tenant = ff.tenant AND f.fordring_id = ff.fordring_id
          JOIN public.purretrinn t
            ON t.tenant = f.tenant AND t.trinn_nr = f.trinn + 1
         WHERE ff.tenant = v_t AND ff.apen AND ff.funntype = 'trinn_forfalt'
           AND f.status = 'apen'
           -- 146: uten adresse er det ingenting å sende til. Funnet står
           -- (et menneske ser det), bestillingen lages ikke.
           AND f.mottaker_hash IS NOT NULL
           AND v_dag - f.forfall >= t.dogn_etter_forfall
           AND NOT EXISTS (
                SELECT 1 FROM public.purringsbestilling pb
                 WHERE pb.tenant = f.tenant
                   AND pb.fordring_id = f.fordring_id
                   AND pb.trinn = t.trinn_nr)
         ORDER BY ff.forst_sett, f.fordring_id
         LIMIT v_grense;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
END $$;
REVOKE ALL ON FUNCTION m23_purringskandidater(INT) FROM PUBLIC;

CREATE FUNCTION m23_bokfor_purringsbestilling(
    p_tenant TEXT, p_fordring_id UUID, p_trinn INT, p_handling_trinn TEXT,
    p_nokkel TEXT, p_utfall TEXT, p_oppdrag_id BIGINT, p_unntak_id BIGINT,
    p_rid TEXT, p_detalj JSONB)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_n INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm23_bokfor_purringsbestilling');
    INSERT INTO public.purringsbestilling
        (tenant, fordring_id, trinn, handling_trinn, nokkel, utfall,
         oppdrag_id, unntak_id, request_id, detalj)
    VALUES (p_tenant, p_fordring_id, p_trinn, p_handling_trinn, p_nokkel,
            p_utfall, p_oppdrag_id, p_unntak_id, p_rid,
            coalesce(p_detalj, '{}'::jsonb))
    ON CONFLICT ON CONSTRAINT purringsbestilling_pk DO NOTHING;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    IF v_n = 0 THEN
        RETURN false;          -- alt bokført: gjenspill, ingen ny evidens
    END IF;
    PERFORM public.m23_evidens(
        p_tenant, p_fordring_id, 'purring_bestilt', 'agent:purring',
        jsonb_build_object('trinn', p_trinn,
                           'handling_trinn', p_handling_trinn,
                           'utfall', p_utfall, 'oppdrag_id', p_oppdrag_id,
                           'unntak_id', p_unntak_id, 'nokkel', p_nokkel,
                           'request_id', p_rid));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m23_bokfor_purringsbestilling(TEXT, UUID, INT, TEXT,
    TEXT, TEXT, BIGINT, BIGINT, TEXT, JSONB) FROM PUBLIC;

-- Lesedøra for flaten og bevisene: hva utløseren har gjort med fordringen.
CREATE FUNCTION m23_purringsbestillingene(p_tenant TEXT, p_fordring_id UUID)
RETURNS TABLE(trinn INT, handling_trinn TEXT, utfall TEXT,
              oppdrag_id BIGINT, unntak_id BIGINT, request_id TEXT,
              bestilt_ts TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_purringsbestillingene');
    RETURN QUERY
    SELECT pb.trinn, pb.handling_trinn, pb.utfall, pb.oppdrag_id,
           pb.unntak_id, pb.request_id, pb.bestilt_ts
      FROM public.purringsbestilling pb
     WHERE pb.tenant = p_tenant AND pb.fordring_id = p_fordring_id
     ORDER BY pb.trinn;
END $$;
REVOKE ALL ON FUNCTION m23_purringsbestillingene(TEXT, UUID) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
               WHERE rolname = 'disponit_plan_arbeider') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_purringskandidater(INT)'
            ' TO disponit_plan_arbeider';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_bokfor_purringsbestilling('
            'TEXT, UUID, INT, TEXT, TEXT, TEXT, BIGINT, BIGINT, TEXT, JSONB)'
            ' TO disponit_plan_arbeider';
        -- Bestillingsveiens målport (147) spør gjennom denne — og
        -- bestillingsveien kjøres nå også av planarbeideren.
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_fordring_for_purring(TEXT,'
            ' UUID) TO disponit_plan_arbeider';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_purringsbestillingene(TEXT,'
            ' UUID) TO disponit';
    END IF;
END $$;
RESET ROLE;
