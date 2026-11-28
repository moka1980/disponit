-- 239 — KALENDERLENKEN: firmaets frister i Outlook og Google Kalender.
--
-- Eier 28/11: «bygg punkt 2» — fristene i kundens egen kalender, og egne
-- frister på samme side. Kalenderprogrammer abonnerer på en adresse og
-- kan ikke logge inn, så adressen ER nøkkelen: en lang, tilfeldig lenke
-- firmaet lager selv, kan bytte, og kan slå av.
--
-- BARE HASHEN LAGRES (som sesjoner og invitasjoner): lenken vises én gang,
-- når den lages. En lekket database gir ingen kalendere. Mistet lenke =
-- lag en ny; den gamle slutter å virke i samme øyeblikk (én rad per firma).
--
-- OPPSLAGET ER KRYSS-TENANT PER KONSTRUKSJON: en forespørsel uten økt
-- kommer med hashen og må finne firmaet. Synet er snevert (096/238-formen):
-- en SELECT-policy for dørenes eier som bare gjelder UTEN tenantkontekst,
-- og døra svarer bare med firmaet til NETTOPP den hashen.
-- ============================================================

CREATE TABLE firma_kalenderlenke (
    tenant       TEXT PRIMARY KEY CHECK (length(btrim(tenant)) > 0),
    token_hash   TEXT NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    opprettet    TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]')
);
ALTER TABLE firma_kalenderlenke ENABLE ROW LEVEL SECURITY;
ALTER TABLE firma_kalenderlenke FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON firma_kalenderlenke
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
CREATE POLICY kalenderlenke_oppslag ON firma_kalenderlenke
    FOR SELECT TO disponit_plattform_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

REVOKE ALL ON TABLE firma_kalenderlenke FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE firma_kalenderlenke
    TO disponit_plattform_eier;

SET LOCAL ROLE disponit_plattform_eier;

CREATE FUNCTION kalenderlenke_sett(p_tenant TEXT, p_token_hash TEXT,
                                   p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '')
       IS DISTINCT FROM p_tenant THEN
        RAISE EXCEPTION 'kalenderlenke_sett: p_tenant er ikke kallerens'
                        ' kontekst' USING ERRCODE = 'insufficient_privilege';
    END IF;
    INSERT INTO public.firma_kalenderlenke
        (tenant, token_hash, opprettet, opprettet_av)
    VALUES (p_tenant, p_token_hash, now(), p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        token_hash = EXCLUDED.token_hash, opprettet = EXCLUDED.opprettet,
        opprettet_av = EXCLUDED.opprettet_av;
END $$;

CREATE FUNCTION kalenderlenke_slett(p_tenant TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '')
       IS DISTINCT FROM p_tenant THEN
        RAISE EXCEPTION 'kalenderlenke_slett: p_tenant er ikke kallerens'
                        ' kontekst' USING ERRCODE = 'insufficient_privilege';
    END IF;
    DELETE FROM public.firma_kalenderlenke WHERE tenant = p_tenant;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    RETURN v_rader > 0;
END $$;

CREATE FUNCTION kalenderlenke_status(p_tenant TEXT)
RETURNS TIMESTAMPTZ LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '')
       IS DISTINCT FROM p_tenant THEN
        RAISE EXCEPTION 'kalenderlenke_status: p_tenant er ikke kallerens'
                        ' kontekst' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN (SELECT k.opprettet FROM public.firma_kalenderlenke k
             WHERE k.tenant = p_tenant);
END $$;

-- Hash → firma. Kalles UTEN økt; konteksten tømmes for oppslaget og
-- settes tilbake, så kryss-tenant-synet finnes bare i dette ene vinduet.
CREATE FUNCTION kalenderlenke_tenant(p_token_hash TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kontekst TEXT; v_tenant TEXT;
BEGIN
    v_kontekst := current_setting('disponit.tenant', true);
    PERFORM set_config('disponit.tenant', '', true);
    SELECT k.tenant INTO v_tenant FROM public.firma_kalenderlenke k
     WHERE k.token_hash = p_token_hash;
    PERFORM set_config('disponit.tenant', coalesce(v_kontekst, ''), true);
    RETURN v_tenant;
END $$;

REVOKE ALL ON FUNCTION kalenderlenke_sett(TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION kalenderlenke_slett(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION kalenderlenke_status(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION kalenderlenke_tenant(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kalenderlenke_sett(TEXT, TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION kalenderlenke_slett(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION kalenderlenke_status(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION kalenderlenke_tenant(TEXT) TO disponit;

RESET ROLE;
