-- =====================================================================
-- 163 — M-17: DET CLAIM-VEIEN GIR SVARMODULEN, OG TENANTENS AVSENDER
-- =====================================================================
--
-- ARC B kundeservice, PR 4 (149/156-formen). Payloaden bærer referanser
-- (161); modulen har verken KEK eller base. Claim-veien i API-et
-- dekrypterer adressen, henvendelsens emne og utkastets tekst gjennom
-- `m17_for_sending` og gir dem som `utforelse`.
--
-- TILSTANDEN SPØRRES EN GANG TIL: mellom bestilling og claim kan
-- henvendelsen ha blitt lukket eller sendt til unntakskøen, og utkastet
-- kan ha fått en annen dom. Døra svarer med tilstanden NÅ, og claim-
-- veien gjør det til en `hindring` modulen kvitterer `feilet` på.
--
-- AVSENDERPROFILEN: svaret går fra husets SMTP; navnet, svar-til og
-- signaturen er tenantens. Egen tabell (156-formen).
-- ---------------------------------------------------------------------

CREATE TABLE kundeserviceavsender (
    tenant        TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    avsender_navn TEXT NOT NULL
        CHECK (length(btrim(avsender_navn)) BETWEEN 1 AND 120),
    svar_til      TEXT
        CHECK (svar_til IS NULL
               OR (length(svar_til) <= 254
                   AND svar_til ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$')),
    signatur      TEXT CHECK (signatur IS NULL OR length(signatur) <= 500),
    oppdatert     TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av  TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT kundeserviceavsender_pk PRIMARY KEY (tenant)
);
ALTER TABLE kundeserviceavsender ENABLE ROW LEVEL SECURITY;
ALTER TABLE kundeserviceavsender FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON kundeserviceavsender
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
GRANT SELECT, INSERT, UPDATE ON kundeserviceavsender
    TO disponit_kundeservice_eier;

SET LOCAL ROLE disponit_kundeservice_eier;

CREATE FUNCTION m17_sett_avsenderprofil(p_tenant TEXT, p_navn TEXT,
                                        p_svar_til TEXT, p_signatur TEXT,
                                        p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_sett_avsenderprofil');
    IF p_navn IS NULL OR length(btrim(p_navn)) NOT BETWEEN 1 AND 120 THEN
        RAISE EXCEPTION 'm17_sett_avsenderprofil: avsendernavnet må være'
            ' 1–120 tegn' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_svar_til IS NOT NULL AND (length(p_svar_til) > 254
        OR p_svar_til !~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$')
    THEN
        RAISE EXCEPTION 'm17_sett_avsenderprofil: svar-til er ikke en'
            ' e-postadresse' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_signatur IS NOT NULL AND length(p_signatur) > 500 THEN
        RAISE EXCEPTION 'm17_sett_avsenderprofil: signaturen er for lang'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_aktor IS NULL OR p_aktor !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm17_sett_avsenderprofil: aktør mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.kundeserviceavsender
        (tenant, avsender_navn, svar_til, signatur, oppdatert_av)
    VALUES (p_tenant, btrim(p_navn), p_svar_til, p_signatur, p_aktor)
    ON CONFLICT (tenant) DO UPDATE
       SET avsender_navn = EXCLUDED.avsender_navn,
           svar_til = EXCLUDED.svar_til, signatur = EXCLUDED.signatur,
           oppdatert = now(), oppdatert_av = EXCLUDED.oppdatert_av;
    PERFORM public.m17_evidens(
        p_tenant, NULL, 'avsenderprofil.satt', p_aktor,
        jsonb_build_object('avsender_navn', btrim(p_navn),
                           'svar_til_satt', p_svar_til IS NOT NULL,
                           'signatur_satt', p_signatur IS NOT NULL));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m17_sett_avsenderprofil(TEXT, TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC;

CREATE FUNCTION m17_avsenderprofilen(p_tenant TEXT)
RETURNS TABLE(avsender_navn TEXT, svar_til TEXT, signatur TEXT,
              oppdatert TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_avsenderprofilen');
    RETURN QUERY
    SELECT a.avsender_navn, a.svar_til, a.signatur, a.oppdatert
      FROM public.kundeserviceavsender a WHERE a.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m17_avsenderprofilen(TEXT) FROM PUBLIC;

-- Alt claim-veien trenger, i ett kall. Klartekst lages aldri i basen.
CREATE FUNCTION m17_for_sending(p_tenant TEXT, p_henvendelse_id UUID,
                                p_utkast_id UUID)
RETURNS TABLE(lukket BOOLEAN, i_unntakskoe BOOLEAN, kanal TEXT,
              ekstern_ref TEXT,
              avsender_maske TEXT, avsender_kryptert BYTEA,
              nonce_avsender BYTEA, avsender_key_id TEXT,
              emne_kryptert BYTEA, nonce_emne BYTEA, key_id TEXT,
              utkast_henvendelse_id UUID, utkast_status TEXT,
              utkast_kryptert BYTEA, utkast_nonce BYTEA,
              utkast_key_id TEXT, profil_navn TEXT, profil_svar_til TEXT,
              profil_signatur TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_for_sending');
    RETURN QUERY
    SELECT (h.lukket_ts IS NOT NULL), (h.unntak_id IS NOT NULL), h.kanal,
           h.ekstern_ref,
           h.avsender_maske, h.avsender_kryptert, h.nonce_avsender,
           h.avsender_key_id, h.emne_kryptert, h.nonce_emne, h.key_id,
           u.henvendelse_id, u.status, u.tekst_kryptert, u.nonce, u.key_id,
           a.avsender_navn, a.svar_til, a.signatur
      FROM public.henvendelse h
      LEFT JOIN public.svarutkast u
        ON u.tenant = h.tenant AND u.utkast_id = p_utkast_id
      LEFT JOIN public.kundeserviceavsender a ON a.tenant = h.tenant
     WHERE h.tenant = p_tenant AND h.henvendelse_id = p_henvendelse_id;
END $$;
REVOKE ALL ON FUNCTION m17_for_sending(TEXT, UUID, UUID) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_sett_avsenderprofil(TEXT,'
            ' TEXT, TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_avsenderprofilen(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_for_sending(TEXT, UUID,'
            ' UUID) TO disponit';
    END IF;
END $$;
RESET ROLE;
