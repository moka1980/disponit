-- 166 — M-14 (ARC B bokføring, PR 2): bokføringsutløseren — kandidater,
-- bokføring av utfallet, lesedør.
--
-- Registeret bestiller når kontrollene er rene; policyen avgjør. Én
-- bestilling per faktura: utfallet bokføres i `bokforingsbestilling`
-- FØR neste runde kan se fakturaen igjen. Ble det brudd (et mva-avvik,
-- en ukjent leverandør), er saken i unntakskøen et menneskes — utløseren
-- prøver ikke igjen. Ligger fakturaen over BEGGE policyens grenser,
-- bokføres `menneske_kreves`: den vises i flaten som «over policyens
-- tak», og et menneske avgjør den som før (kontrollert/avvist).
--
-- Kandidatdøra er KRYSS-TENANT (162-formen): den nekter tenantkontekst
-- og setter den selv per tenant. Beløpsgrensene står IKKE her — de er
-- policyens, og planrunden leser dem fra tenantens aktive policy.
CREATE TABLE bokforingsbestilling (
    tenant      TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    faktura_id  UUID NOT NULL,
    nokkel      TEXT NOT NULL CHECK (length(nokkel) BETWEEN 8 AND 200),
    handling    TEXT NOT NULL CHECK (handling IN (
                    'faktura.bokfor', 'faktura.bokfor_stor', 'ingen')),
    utfall      TEXT NOT NULL CHECK (
                    utfall IN ('tillat', 'brudd', 'stopp', 'menneske_kreves')
                    OR utfall LIKE 'feil:%'),
    oppdrag_id  BIGINT,
    unntak_id   BIGINT,
    request_id  TEXT NOT NULL CHECK (request_id ~ '[^[:space:]]'),
    detalj      JSONB NOT NULL DEFAULT '{}'::jsonb,
    bestilt_ts  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT bokforingsbestilling_pk PRIMARY KEY (tenant, faktura_id),
    CONSTRAINT bokforingsbestilling_faktura_fk
        FOREIGN KEY (tenant, faktura_id)
        REFERENCES inngaaende_faktura (tenant, faktura_id),
    CONSTRAINT bokforingsbestilling_tillat_har_oppdrag
        CHECK (utfall <> 'tillat' OR oppdrag_id IS NOT NULL),
    CONSTRAINT bokforingsbestilling_menneske_uten_handling
        CHECK ((utfall = 'menneske_kreves') = (handling = 'ingen'))
);
ALTER TABLE bokforingsbestilling ENABLE ROW LEVEL SECURITY;
ALTER TABLE bokforingsbestilling FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON bokforingsbestilling
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
GRANT SELECT, INSERT ON bokforingsbestilling TO disponit_faktura_eier;

SET LOCAL ROLE disponit_faktura_eier;

CREATE FUNCTION m14_bokforingskandidater(p_grense INT DEFAULT 50)
RETURNS TABLE(tenant TEXT, faktura_id UUID, brutto_ore BIGINT,
              valuta TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_grense INT;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm14_bokforingskandidater: døra er KRYSS-TENANT og'
            ' kalles uten tenantkontekst'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_grense := greatest(least(coalesce(p_grense, 50), 500), 1);
    SELECT array_agg(DISTINCT f.tenant ORDER BY f.tenant) INTO v_tenanter
      FROM public.inngaaende_faktura f
     WHERE f.status IN ('mottatt', 'kontrollert');
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        RETURN QUERY
        SELECT f.tenant, f.faktura_id, f.brutto_ore, f.valuta
          FROM public.inngaaende_faktura f
         WHERE f.tenant = v_t
           AND f.status IN ('mottatt', 'kontrollert')
           -- De tre kontrollene 106 kjørte ved registreringen: rene.
           AND EXISTS (SELECT 1 FROM public.fakturakontroll k
                        WHERE k.tenant = f.tenant
                          AND k.faktura_id = f.faktura_id
                          AND k.kontrolltype = 'dublett' AND k.utfall = 'ok')
           AND EXISTS (SELECT 1 FROM public.fakturakontroll k
                        WHERE k.tenant = f.tenant
                          AND k.faktura_id = f.faktura_id
                          AND k.kontrolltype = 'mva' AND k.utfall = 'ok')
           AND EXISTS (SELECT 1 FROM public.fakturakontroll k
                        WHERE k.tenant = f.tenant
                          AND k.faktura_id = f.faktura_id
                          AND k.kontrolltype = 'leverandor'
                          AND k.utfall = 'ok')
           -- Over tenantens egen beløpsgrense: 106 krever en manuell
           -- kontroll der, og det gjør utløseren også.
           AND (COALESCE((SELECT f.brutto_ore <= t.belopsgrense_ore
                            FROM public.fakturaterskel t
                           WHERE t.tenant = f.tenant), false)
                OR EXISTS (SELECT 1 FROM public.fakturakontroll k
                            WHERE k.tenant = f.tenant
                              AND k.faktura_id = f.faktura_id
                              AND k.kontrolltype = 'manuell'
                              AND k.utfall = 'ok'))
           AND NOT EXISTS (
                SELECT 1 FROM public.bokforingsbestilling b
                 WHERE b.tenant = f.tenant AND b.faktura_id = f.faktura_id)
         ORDER BY f.mottatt, f.faktura_id
         LIMIT v_grense;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
END $$;
REVOKE ALL ON FUNCTION m14_bokforingskandidater(INT) FROM PUBLIC;

CREATE FUNCTION m14_bokfor_bokforingsbestilling(
    p_tenant TEXT, p_faktura_id UUID, p_nokkel TEXT, p_handling TEXT,
    p_utfall TEXT, p_oppdrag_id BIGINT, p_unntak_id BIGINT, p_rid TEXT,
    p_detalj JSONB)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_n INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm14_bokfor_bokforingsbestilling');
    INSERT INTO public.bokforingsbestilling
        (tenant, faktura_id, nokkel, handling, utfall, oppdrag_id,
         unntak_id, request_id, detalj)
    VALUES (p_tenant, p_faktura_id, p_nokkel, p_handling, p_utfall,
            p_oppdrag_id, p_unntak_id, p_rid, coalesce(p_detalj, '{}'::jsonb))
        ON CONFLICT ON CONSTRAINT bokforingsbestilling_pk DO NOTHING;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    IF v_n = 0 THEN
        RETURN false;
    END IF;
    PERFORM public.m14_evidens(
        p_tenant, p_faktura_id, 'bokforing.bestilt', 'agent:faktura',
        jsonb_build_object('handling', p_handling, 'utfall', p_utfall,
                           'oppdrag_id', p_oppdrag_id,
                           'unntak_id', p_unntak_id, 'nokkel', p_nokkel,
                           'request_id', p_rid));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m14_bokfor_bokforingsbestilling(TEXT, UUID, TEXT,
    TEXT, TEXT, BIGINT, BIGINT, TEXT, JSONB) FROM PUBLIC;

CREATE FUNCTION m14_bokforingsbestillingen(p_tenant TEXT, p_faktura_id UUID)
RETURNS TABLE(handling TEXT, utfall TEXT, oppdrag_id BIGINT,
              unntak_id BIGINT, request_id TEXT, bestilt_ts TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm14_bokforingsbestillingen');
    RETURN QUERY
    SELECT b.handling, b.utfall, b.oppdrag_id, b.unntak_id, b.request_id,
           b.bestilt_ts
      FROM public.bokforingsbestilling b
     WHERE b.tenant = p_tenant AND b.faktura_id = p_faktura_id;
END $$;
REVOKE ALL ON FUNCTION m14_bokforingsbestillingen(TEXT, UUID) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
               WHERE rolname = 'disponit_plan_arbeider') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m14_bokforingskandidater(INT)'
            ' TO disponit_plan_arbeider';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m14_bokfor_bokforingsbestilling('
            'TEXT, UUID, TEXT, TEXT, TEXT, BIGINT, BIGINT, TEXT, JSONB)'
            ' TO disponit_plan_arbeider';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m14_bokforingsbestillingen(TEXT,'
            ' UUID) TO disponit';
    END IF;
END $$;

RESET ROLE;
