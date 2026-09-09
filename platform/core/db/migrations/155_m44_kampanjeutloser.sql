-- =====================================================================
-- 155 — M-44: KAMPANJEUTLØSEREN — REGISTERET BESTILLER PÅ SENDEDAGEN
-- =====================================================================
--
-- ARC B kampanje, PR 3. Sveipen (114) FINNER funn og flytter ingenting.
-- Utløseren er leddet mellom planen og bestillingen: på sendedagen
-- plukker den hver (kampanje, mottaker) i planen der kampanjen står
-- `registrert` med innhold (153) og mottakeren er aktiv med adresse
-- (153) — og bestiller `kampanje.send` (154) gjennom NØYAKTIG samme
-- bestillingsvei som et menneske (044 §4-grepet, som M-23 i 148).
-- Policyen avgjør; utløseren har ingen egen autoritet.
--
-- SAMTYKKET ER IKKE KANDIDATDØRAS DOM — MED ETT UNNTAK. Bestillingsveien
-- attesterer samtykket (`v_samtykke`), og et samtykke som er TRUKKET
-- eller UTLØPT etter planleggingen blir et brudd → en sak i unntakskøen,
-- aldri en stille levering. Men en mottaker som ALDRI har hatt en
-- samtykkehendelse er ikke kandidat: der er det ingenting å attestere,
-- sveipens `uten_samtykke`-funn står alt for et menneske, og en bestilling
-- ville bare fabrikkert én sak per importert rad.
--
-- ÉN BESTILLING PER KAMPANJE OG MOTTAKER. `kampanjebestilling` er
-- utløserens hukommelse: en rad per (kampanje, mottaker) med utfallet.
-- Ble det brudd, er saken et menneskes — utløseren prøver ikke igjen.
-- Radene er append-only ved grant, og hver rad speiles i
-- revisjonsloggen gjennom `m44_evidens` — UTEN kontaktpunktet.
--
-- Utløseren kjører i PLANARBEIDERENS prosess (`disponit_plan_arbeider`,
-- 048): samme tillitsnivå som materialisereren og purringsutløseren,
-- ingen ny rolle, ingen ny credential.
-- ---------------------------------------------------------------------

CREATE TABLE kampanjebestilling (
    tenant       TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kampanje_id  UUID NOT NULL,
    mottaker_id  UUID NOT NULL,
    nokkel       TEXT NOT NULL CHECK (length(nokkel) BETWEEN 8 AND 200),
    -- Bestillingsveiens dom, eller den terminale feilkoden (`feil:<kode>`)
    -- når veien svarte nei FØR beslutningen.
    utfall       TEXT NOT NULL CHECK (
                     utfall IN ('tillat', 'brudd', 'stopp')
                     OR utfall LIKE 'feil:%'),
    oppdrag_id   BIGINT,
    unntak_id    BIGINT,
    request_id   TEXT NOT NULL CHECK (request_id ~ '[^[:space:]]'),
    detalj       JSONB NOT NULL DEFAULT '{}'::jsonb,
    bestilt_ts   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT kampanjebestilling_pk
        PRIMARY KEY (tenant, kampanje_id, mottaker_id),
    CONSTRAINT kampanjebestilling_plan_fk
        FOREIGN KEY (tenant, kampanje_id, mottaker_id)
        REFERENCES kampanjeplan (tenant, kampanje_id, mottaker_id),
    -- Et tillat uten oppdrag er ikke et tillat.
    CONSTRAINT kampanjebestilling_tillat_har_oppdrag
        CHECK (utfall <> 'tillat' OR oppdrag_id IS NOT NULL)
);
ALTER TABLE kampanjebestilling ENABLE ROW LEVEL SECURITY;
ALTER TABLE kampanjebestilling FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON kampanjebestilling
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
-- Append-only ved grant: verken UPDATE eller DELETE.
GRANT SELECT, INSERT ON kampanjebestilling TO disponit_kampanje_eier;
-- Bordet skal aldri tømmes (114 §6-mønsteret: samme vakt som planen).
CREATE TRIGGER m44_kampanjebestilling_ingen_truncate
    BEFORE TRUNCATE ON kampanjebestilling
    EXECUTE FUNCTION m44_plan_vakt();

-- Kandidatdøra lister tenantene gjennom sveipens egen kryss-tenant-
-- policy på `kampanjemottaker` (114 `m44_sveip_tenantliste`) — samme
-- tre gjerder: bare eieren, bare SELECT, bare uten tenantkontekst.
SET LOCAL ROLE disponit_kampanje_eier;

CREATE FUNCTION m44_kampanjekandidater(p_grense INT DEFAULT 50)
RETURNS TABLE(tenant TEXT, kampanje_id UUID, mottaker_id UUID,
              planlagt_sendt DATE)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_grense INT; v_dag DATE;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm44_kampanjekandidater: døra er KRYSS-TENANT og'
            ' kalles uten tenantkontekst — en kaller som har satt en'
            ' kontekst ber om noe annet enn det denne funksjonen gjør'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Taket er PER TENANT: kapasitet forsinker, konsumerer aldri (044).
    v_grense := greatest(least(coalesce(p_grense, 50), 500), 1);
    v_dag := current_date;
    -- MATERIALISERT FØR LØKKEN (114s form): `set_config` inne i en lat
    -- markør ville endret RLS-konteksten markøren fortsatt leste gjennom.
    SELECT array_agg(DISTINCT m.tenant ORDER BY m.tenant) INTO v_tenanter
      FROM public.kampanjemottaker m WHERE m.aktiv;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        RETURN QUERY
        SELECT pl.tenant, pl.kampanje_id, pl.mottaker_id, k.planlagt_sendt
          FROM public.kampanjeplan pl
          JOIN public.kampanje k
            ON k.tenant = pl.tenant AND k.kampanje_id = pl.kampanje_id
          JOIN public.kampanjemottaker m
            ON m.tenant = pl.tenant AND m.mottaker_id = pl.mottaker_id
         WHERE pl.tenant = v_t
           AND k.status = 'registrert'
           AND k.planlagt_sendt <= v_dag
           -- 153: uten innhold er det ingenting å levere; uten adresse
           -- ingen å levere til. Bestillingsveien ville sagt 409 —
           -- kandidatdøra sier det FØR, uten en runde i køen.
           AND k.tekst IS NOT NULL
           AND m.aktiv
           AND m.kontakt_kryptert IS NOT NULL
           -- Aldri samtykket = ingenting å attestere (se toppen).
           AND EXISTS (SELECT 1 FROM public.samtykkehendelse s
                        WHERE s.tenant = pl.tenant
                          AND s.mottaker_id = pl.mottaker_id)
           AND NOT EXISTS (
                SELECT 1 FROM public.kampanjebestilling kb
                 WHERE kb.tenant = pl.tenant
                   AND kb.kampanje_id = pl.kampanje_id
                   AND kb.mottaker_id = pl.mottaker_id)
         ORDER BY k.planlagt_sendt, k.kampanje_id, pl.lagt_til, pl.mottaker_id
         LIMIT v_grense;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
END $$;
REVOKE ALL ON FUNCTION m44_kampanjekandidater(INT) FROM PUBLIC;

CREATE FUNCTION m44_bokfor_kampanjebestilling(
    p_tenant TEXT, p_kampanje_id UUID, p_mottaker_id UUID, p_nokkel TEXT,
    p_utfall TEXT, p_oppdrag_id BIGINT, p_unntak_id BIGINT, p_rid TEXT,
    p_detalj JSONB)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_n INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm44_bokfor_kampanjebestilling');
    INSERT INTO public.kampanjebestilling
        (tenant, kampanje_id, mottaker_id, nokkel, utfall, oppdrag_id,
         unntak_id, request_id, detalj)
    VALUES (p_tenant, p_kampanje_id, p_mottaker_id, p_nokkel, p_utfall,
            p_oppdrag_id, p_unntak_id, p_rid,
            coalesce(p_detalj, '{}'::jsonb))
    ON CONFLICT ON CONSTRAINT kampanjebestilling_pk DO NOTHING;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    IF v_n = 0 THEN
        RETURN false;          -- alt bokført: gjenspill, ingen ny evidens
    END IF;
    PERFORM public.m44_evidens(
        p_tenant, p_mottaker_id, 'kampanje_bestilt', 'agent:kampanje',
        jsonb_build_object('kampanje_id', p_kampanje_id::text,
                           'utfall', p_utfall, 'oppdrag_id', p_oppdrag_id,
                           'unntak_id', p_unntak_id, 'nokkel', p_nokkel,
                           'request_id', p_rid));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m44_bokfor_kampanjebestilling(TEXT, UUID, UUID, TEXT,
    TEXT, BIGINT, BIGINT, TEXT, JSONB) FROM PUBLIC;

-- Lesedøra for flaten og bevisene: hva utløseren har gjort med kampanjen.
CREATE FUNCTION m44_kampanjebestillingene(p_tenant TEXT, p_kampanje_id UUID)
RETURNS TABLE(mottaker_id UUID, utfall TEXT, oppdrag_id BIGINT,
              unntak_id BIGINT, request_id TEXT, bestilt_ts TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_kampanjebestillingene');
    RETURN QUERY
    SELECT kb.mottaker_id, kb.utfall, kb.oppdrag_id, kb.unntak_id,
           kb.request_id, kb.bestilt_ts
      FROM public.kampanjebestilling kb
     WHERE kb.tenant = p_tenant AND kb.kampanje_id = p_kampanje_id
     ORDER BY kb.bestilt_ts, kb.mottaker_id;
END $$;
REVOKE ALL ON FUNCTION m44_kampanjebestillingene(TEXT, UUID) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
               WHERE rolname = 'disponit_plan_arbeider') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_kampanjekandidater(INT)'
            ' TO disponit_plan_arbeider';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_bokfor_kampanjebestilling('
            'TEXT, UUID, UUID, TEXT, TEXT, BIGINT, BIGINT, TEXT, JSONB)'
            ' TO disponit_plan_arbeider';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_kampanjebestillingene(TEXT,'
            ' UUID) TO disponit';
    END IF;
END $$;
RESET ROLE;
