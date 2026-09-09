-- =====================================================================
-- 156 — M-44: DET CLAIM-VEIEN GIR KAMPANJEMODULEN, OG TENANTENS AVSENDER
-- =====================================================================
--
-- ARC B kampanje, PR 4: eiermodulen `m44_kampanje` leverer. Payloaden
-- bærer referanser (154); modulen har verken KEK eller base. Claim-veien
-- i API-et dekrypterer adressen og henter teksten gjennom
-- `m44_for_sending` og gir dem som `utforelse` — som 149 for M-23.
--
-- SAMTYKKET SPØRRES EN GANG TIL. Beslutningen ble tatt med registerets
-- attestasjon (154); mellom bestilling og claim kan mottakeren ha meldt
-- seg av. Døra svarer med tilstanden PÅ DAGEN, og claim-veien gjør et
-- trukket samtykke til en `hindring` modulen kvitterer `feilet` på —
-- «hadde vi lov til å sende dette den dagen» er hele spørsmålet.
--
-- AVSENDERPROFILEN (149s form): e-posten går fra husets SMTP; navnet og
-- svar-til-adressen er tenantens. Egen tabell — `kampanjegrense` er
-- versjonert og rører seg ikke av en avsenderendring.
-- ---------------------------------------------------------------------

CREATE TABLE kampanjeavsender (
    tenant        TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    avsender_navn TEXT NOT NULL
        CHECK (length(btrim(avsender_navn)) BETWEEN 1 AND 120),
    svar_til      TEXT
        CHECK (svar_til IS NULL
               OR (length(svar_til) <= 254
                   AND svar_til ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$')),
    oppdatert     TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av  TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT kampanjeavsender_pk PRIMARY KEY (tenant)
);
ALTER TABLE kampanjeavsender ENABLE ROW LEVEL SECURITY;
ALTER TABLE kampanjeavsender FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON kampanjeavsender
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
GRANT SELECT, INSERT, UPDATE ON kampanjeavsender TO disponit_kampanje_eier;

SET LOCAL ROLE disponit_kampanje_eier;

CREATE FUNCTION m44_sett_avsender(p_tenant TEXT, p_navn TEXT,
                                  p_svar_til TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_sett_avsender');
    IF p_navn IS NULL OR length(btrim(p_navn)) NOT BETWEEN 1 AND 120 THEN
        RAISE EXCEPTION 'm44_sett_avsender: avsendernavnet må være 1–120'
            ' tegn' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_svar_til IS NOT NULL AND (length(p_svar_til) > 254
        OR p_svar_til !~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$')
    THEN
        RAISE EXCEPTION 'm44_sett_avsender: svar-til er ikke en'
            ' e-postadresse' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_aktor IS NULL OR p_aktor !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm44_sett_avsender: aktør mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.kampanjeavsender (tenant, avsender_navn, svar_til,
                                         oppdatert_av)
    VALUES (p_tenant, btrim(p_navn), p_svar_til, p_aktor)
    ON CONFLICT (tenant) DO UPDATE
       SET avsender_navn = EXCLUDED.avsender_navn,
           svar_til = EXCLUDED.svar_til,
           oppdatert = now(), oppdatert_av = EXCLUDED.oppdatert_av;
    PERFORM public.m44_evidens(
        p_tenant, NULL, 'avsender.satt', p_aktor,
        jsonb_build_object('avsender_navn', btrim(p_navn),
                           'svar_til_satt', p_svar_til IS NOT NULL));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m44_sett_avsender(TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;

CREATE FUNCTION m44_avsenderen(p_tenant TEXT)
RETURNS TABLE(avsender_navn TEXT, svar_til TEXT, oppdatert TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_avsenderen');
    RETURN QUERY
    SELECT a.avsender_navn, a.svar_til, a.oppdatert
      FROM public.kampanjeavsender a WHERE a.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m44_avsenderen(TEXT) FROM PUBLIC;

-- Alt claim-veien trenger, i ett kall: kampanjens tilstand og tekst,
-- planmedlemskap, mottakerens tilstand og KRYPTERTE adresse (klartekst
-- lages aldri i basen), samtykket på dagen, og avsenderprofilen.
CREATE FUNCTION m44_for_sending(p_tenant TEXT, p_kampanje_id UUID,
                                p_mottaker_id UUID)
RETURNS TABLE(kampanje_status TEXT, planlagt_sendt DATE, emne TEXT,
              tekst TEXT, avmeldingslenke TEXT, i_plan BOOLEAN,
              mottaker_aktiv BOOLEAN, mottaker_navn TEXT,
              kontakt_maske TEXT, kontakt_kryptert BYTEA, kontakt_nonce BYTEA,
              kontakt_key_id TEXT, samtykke_tilstand TEXT,
              avsender_navn TEXT, svar_til TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_for_sending');
    RETURN QUERY
    SELECT k.status, k.planlagt_sendt, k.emne, k.tekst, k.avmeldingslenke,
           EXISTS (SELECT 1 FROM public.kampanjeplan pl
                    WHERE pl.tenant = p_tenant
                      AND pl.kampanje_id = p_kampanje_id
                      AND pl.mottaker_id = p_mottaker_id),
           m.aktiv, m.navn, m.kontakt_maske, m.kontakt_kryptert,
           m.kontakt_nonce,
           m.kontakt_key_id, s.tilstand, a.avsender_navn, a.svar_til
      FROM public.kampanje k
      LEFT JOIN public.kampanjemottaker m
        ON m.tenant = k.tenant AND m.mottaker_id = p_mottaker_id
      LEFT JOIN LATERAL (
            SELECT s2.tilstand
              FROM public.samtykkehendelse s2
             WHERE s2.tenant = p_tenant AND s2.mottaker_id = p_mottaker_id
               AND s2.inntruffet <= current_date
             ORDER BY s2.inntruffet DESC, s2.registrert DESC
             LIMIT 1) s ON true
      LEFT JOIN public.kampanjeavsender a ON a.tenant = k.tenant
     WHERE k.tenant = p_tenant AND k.kampanje_id = p_kampanje_id;
END $$;
REVOKE ALL ON FUNCTION m44_for_sending(TEXT, UUID, UUID) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_sett_avsender(TEXT, TEXT,'
            ' TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_avsenderen(TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_for_sending(TEXT, UUID,'
            ' UUID) TO disponit';
    END IF;
END $$;
RESET ROLE;
