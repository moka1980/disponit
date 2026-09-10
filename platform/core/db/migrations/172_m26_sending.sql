-- 172 — M-26 (ARC B tilbud, PR 4): claim-veiens dør og tenantens
-- avsenderprofil for tilbud. 163-formen.
--
-- `m26_for_sending` gir claim-veien ALT eiermodulen trenger for å sende:
-- kundens adresse (kryptert — dekrypteres i API-ets tillit, aldri her),
-- navnet, linjene med tallene registeret satte, klausulTEKSTEN tilbudet
-- siterte (versjonen som ble bundet, ikke dagens), innledningen og
-- avsenderprofilen. Og tilstanden spurt en gang til: status og gyldighet.
CREATE TABLE tilbudsavsender (
    tenant         TEXT PRIMARY KEY CHECK (length(btrim(tenant)) > 0),
    avsender_navn  TEXT NOT NULL CHECK (avsender_navn ~ '[^[:space:]]'),
    svar_til       TEXT CHECK (svar_til IS NULL OR svar_til ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
    signatur       TEXT CHECK (signatur IS NULL OR signatur ~ '[^[:space:]]'),
    oppdatert      TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av   TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]')
);
ALTER TABLE tilbudsavsender ENABLE ROW LEVEL SECURITY;
ALTER TABLE tilbudsavsender FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON tilbudsavsender
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
GRANT SELECT, INSERT, UPDATE ON tilbudsavsender TO disponit_prisbok_eier;

SET LOCAL ROLE disponit_prisbok_eier;

CREATE FUNCTION m26_sett_avsenderprofil(
    p_tenant TEXT, p_navn TEXT, p_svar_til TEXT, p_signatur TEXT,
    p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_ny BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_sett_avsenderprofil');
    INSERT INTO public.tilbudsavsender
        (tenant, avsender_navn, svar_til, signatur, oppdatert_av)
    VALUES (p_tenant, btrim(p_navn), nullif(btrim(coalesce(p_svar_til, '')), ''),
            nullif(btrim(coalesce(p_signatur, '')), ''), p_aktor)
        ON CONFLICT (tenant) DO UPDATE
           SET avsender_navn = EXCLUDED.avsender_navn,
               svar_til = EXCLUDED.svar_til, signatur = EXCLUDED.signatur,
               oppdatert = now(), oppdatert_av = EXCLUDED.oppdatert_av
        RETURNING (xmax = 0) INTO v_ny;
    PERFORM public.m26_evidens(
        p_tenant, '00000000-0000-0000-0000-000000000000'::uuid,
        'tilbudsavsender.satt', p_aktor,
        jsonb_build_object('avsender_navn', btrim(p_navn),
                           'har_svar_til', p_svar_til IS NOT NULL));
    RETURN v_ny;
END $$;
REVOKE ALL ON FUNCTION m26_sett_avsenderprofil(TEXT, TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC;

CREATE FUNCTION m26_avsenderprofilen(p_tenant TEXT)
RETURNS TABLE(avsender_navn TEXT, svar_til TEXT, signatur TEXT,
              oppdatert TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_avsenderprofilen');
    RETURN QUERY
    SELECT a.avsender_navn, a.svar_til, a.signatur, a.oppdatert
      FROM public.tilbudsavsender a WHERE a.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m26_avsenderprofilen(TEXT) FROM PUBLIC;

CREATE FUNCTION m26_for_sending(p_tenant TEXT, p_tilbud_id UUID)
RETURNS TABLE(status TEXT, gyldig_til DATE, kunde_navn TEXT,
              kunde_maske TEXT, kunde_kryptert BYTEA, nonce_kunde BYTEA,
              kunde_key_id TEXT, sum_ore BIGINT, valuta TEXT,
              tilbudsdato DATE, innledning TEXT, linjer JSONB,
              klausuler JSONB, profil_navn TEXT, profil_svar_til TEXT,
              profil_signatur TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_for_sending');
    RETURN QUERY
    SELECT t.status, t.gyldig_til, t.kunde_navn, t.kunde_maske,
           t.kunde_kryptert, t.nonce_kunde, t.kunde_key_id, t.sum_ore,
           t.valuta, t.tilbudsdato, t.innledning, d.linjer, d.klausuler,
           a.avsender_navn, a.svar_til, a.signatur
      FROM public.tilbud t
      LEFT JOIN LATERAL public.m26_tilbudet(p_tenant, t.tilbud_id) d ON true
      LEFT JOIN public.tilbudsavsender a ON a.tenant = t.tenant
     WHERE t.tenant = p_tenant AND t.tilbud_id = p_tilbud_id;
END $$;
REVOKE ALL ON FUNCTION m26_for_sending(TEXT, UUID) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_sett_avsenderprofil(TEXT,'
            ' TEXT, TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_avsenderprofilen(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_for_sending(TEXT, UUID)'
            ' TO disponit';
    END IF;
END $$;

RESET ROLE;
