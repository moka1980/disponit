-- =====================================================================
-- 149 — M-23: DET PLATTFORMEN GIR UTFØREREN NÅR PURRINGEN SKAL SENDES
-- =====================================================================
--
-- ARC B, PR 4. Eiermodulen som sender purringen lever i M-57-formen:
-- modultoken, claim over API-et, signert kvittering — og INGEN nøkler,
-- ingen base. Adressen ligger kryptert på fordringen (146) med tenantens
-- DEK, så det er claim-veien i API-et som må lese og dekryptere den,
-- akkurat som den dekrypterer payloaden. Døra `m23_for_sending` er det
-- ene oppslaget claim-veien gjør: fordringens tilstand, tallene
-- e-posten trenger, chiffertekst + nonce + key_id for adressen, og
-- tenantens avsenderprofil.
--
-- AVSENDERPROFILEN (eiervedtaket 9/9, valg 3): e-posten går fra husets
-- SMTP, med TENANTENS navn og svar-til. Den bor på `purreplan`-raden
-- (én per tenant, M-23s egen innstilling) og settes gjennom
-- `m23_sett_avsender`. Uten profil brukes tenant-id-en som navn og
-- ingen svar-til — ærlig, men ikke pent; flaten (PR 6) gjør det synlig.
-- ---------------------------------------------------------------------

ALTER TABLE purreplan
    ADD COLUMN IF NOT EXISTS avsender_navn TEXT,
    ADD COLUMN IF NOT EXISTS svar_til TEXT;
ALTER TABLE purreplan DROP CONSTRAINT IF EXISTS purreplan_avsender_form;
ALTER TABLE purreplan ADD CONSTRAINT purreplan_avsender_form CHECK (
    (avsender_navn IS NULL
     OR (length(btrim(avsender_navn)) BETWEEN 1 AND 120))
    AND (svar_til IS NULL
         OR (length(svar_til) <= 254
             AND svar_til ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$')));

SET LOCAL ROLE disponit_fordring_eier;

CREATE FUNCTION m23_sett_avsender(p_tenant TEXT, p_navn TEXT,
                                  p_svar_til TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_sett_avsender');
    IF p_navn IS NULL OR length(btrim(p_navn)) NOT BETWEEN 1 AND 120 THEN
        RAISE EXCEPTION 'm23_sett_avsender: avsendernavnet må være 1–120'
            ' tegn' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_svar_til IS NOT NULL AND (length(p_svar_til) > 254
        OR p_svar_til !~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$')
    THEN
        RAISE EXCEPTION 'm23_sett_avsender: svar-til er ikke en'
            ' e-postadresse' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm23_sett_avsender: aktør mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Purreplanraden er tenantens M-23-innstilling; finnes den ikke
    -- ennå (ingen trinn satt), fødes den her uten trinn. Versjonen er
    -- TRINNENES og rører seg ikke av en avsenderendring.
    INSERT INTO public.purreplan (tenant, oppdatert_av, avsender_navn,
                                  svar_til)
    VALUES (p_tenant, p_aktor, btrim(p_navn), p_svar_til)
    ON CONFLICT (tenant) DO UPDATE
       SET avsender_navn = EXCLUDED.avsender_navn,
           svar_til = EXCLUDED.svar_til,
           oppdatert = now(), oppdatert_av = EXCLUDED.oppdatert_av;
    PERFORM public.m23_evidens(
        p_tenant, NULL, 'avsender.satt', p_aktor,
        jsonb_build_object('avsender_navn', btrim(p_navn),
                           'svar_til_satt', p_svar_til IS NOT NULL));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m23_sett_avsender(TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;

CREATE FUNCTION m23_for_sending(p_tenant TEXT, p_fordring_id UUID)
RETURNS TABLE(status TEXT, kunde_ref TEXT, fakturanummer TEXT,
              rest_ore BIGINT, forfall DATE, trinn INT,
              neste_trinn INT, handling_trinn TEXT, gebyr_ore BIGINT,
              mottaker_maske TEXT, mottaker_kryptert BYTEA,
              mottaker_nonce BYTEA, mottaker_key_id TEXT,
              avsender_navn TEXT, svar_til TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_for_sending');
    RETURN QUERY
    SELECT f.status, f.kunde_ref, f.fakturanummer,
           f.belop_ore - f.betalt_ore, f.forfall, f.trinn,
           t.trinn_nr, t.handling, t.gebyr_ore,
           f.mottaker_maske, f.mottaker_kryptert, f.mottaker_nonce,
           f.mottaker_key_id, p.avsender_navn, p.svar_til
      FROM public.fordring f
      LEFT JOIN public.purretrinn t
        ON t.tenant = f.tenant AND t.trinn_nr = f.trinn + 1
      LEFT JOIN public.purreplan p ON p.tenant = f.tenant
     WHERE f.tenant = p_tenant AND f.fordring_id = p_fordring_id;
END $$;
REVOKE ALL ON FUNCTION m23_for_sending(TEXT, UUID) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_sett_avsender(TEXT, TEXT,'
            ' TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_for_sending(TEXT, UUID)'
            ' TO disponit';
    END IF;
END $$;
RESET ROLE;
