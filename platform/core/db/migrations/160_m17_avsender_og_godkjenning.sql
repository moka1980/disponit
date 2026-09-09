-- =====================================================================
-- 160 — M-17: AVSENDEREN HAR EN ADRESSE, UTKASTET KAN GODKJENNES
-- =====================================================================
--
-- ARC B kundeservice, PR 1 (M-23/M-44-formen). 102 lagrer avsenderen
-- som HASH — nok til å kjenne igjen den samme kunden, aldri nok til å
-- svare. Skal plattformen sende svaret innenfor policyen, må adressen
-- finnes: kryptert med tenantens DEK (AAD `m17:avsender`), med en maske
-- flaten kan vise. Klartekst lever bare i forespørselen og i claim-
-- svaret til eiermodulen (PR 4) — aldri i basen, aldri i loggene.
--
-- UTKASTET FÅR EN TREDJE DOM: `godkjent`. `brukt_manuelt` består (et
-- menneske sendte selv); `godkjent` er et menneskes ja til at
-- PLATTFORMEN sender — bestillingen (PR 2) og utløseren (PR 3) bygger
-- på den, og policyen avgjør. Statusen `sendt` kommer med bokføringen
-- (PR 5); til da er et godkjent utkast avgjort som 102 sier: det går
-- ikke tilbake til foreslått.
-- ---------------------------------------------------------------------

ALTER TABLE henvendelse
    ADD COLUMN IF NOT EXISTS avsender_kryptert BYTEA,
    ADD COLUMN IF NOT EXISTS nonce_avsender BYTEA,
    ADD COLUMN IF NOT EXISTS avsender_key_id TEXT,
    ADD COLUMN IF NOT EXISTS avsender_maske TEXT,
    ADD COLUMN IF NOT EXISTS avsender_satt_ts TIMESTAMPTZ;
ALTER TABLE henvendelse DROP CONSTRAINT IF EXISTS henvendelse_avsender_helhet;
ALTER TABLE henvendelse ADD CONSTRAINT henvendelse_avsender_helhet CHECK (
    (avsender_kryptert IS NULL AND nonce_avsender IS NULL
     AND avsender_key_id IS NULL AND avsender_maske IS NULL
     AND avsender_satt_ts IS NULL)
    OR (avsender_kryptert IS NOT NULL AND octet_length(nonce_avsender) = 12
        AND avsender_key_id IS NOT NULL
        AND avsender_maske ~ '[^[:space:]]' AND avsender_satt_ts IS NOT NULL));
ALTER TABLE henvendelse DROP CONSTRAINT IF EXISTS henvendelse_avsender_dek_fk;
ALTER TABLE henvendelse ADD CONSTRAINT henvendelse_avsender_dek_fk
    FOREIGN KEY (tenant, avsender_key_id)
    REFERENCES tenant_nokler (tenant, key_id);

ALTER TABLE svarutkast DROP CONSTRAINT IF EXISTS svarutkast_status_check;
ALTER TABLE svarutkast ADD CONSTRAINT svarutkast_status_check
    CHECK (status IN ('foreslatt', 'forkastet', 'brukt_manuelt',
                      'godkjent'));

SET LOCAL ROLE disponit_kundeservice_eier;

-- Adressen på en eksisterende henvendelse (og rettet, så lenge den er
-- åpen). Masken regnes i API-et av den normaliserte adressen — samme
-- form som M-44s (`k****@domene`).
CREATE FUNCTION m17_sett_avsender(
    p_tenant TEXT, p_henvendelse_id UUID, p_maske TEXT, p_kryptert BYTEA,
    p_nonce BYTEA, p_key_id TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_lukket TIMESTAMPTZ;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_sett_avsender');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_kryptert IS NULL OR p_nonce IS NULL OR p_key_id IS NULL
       OR length(p_kryptert) < 17 OR length(p_nonce) <> 12
       OR p_maske IS NULL OR p_maske !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm17_sett_avsender: maske, chiffertekst, nonce og'
            ' key_id må være satt' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT h.lukket_ts INTO v_lukket FROM public.henvendelse h
     WHERE h.tenant = p_tenant AND h.henvendelse_id = p_henvendelse_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm17_sett_avsender: henvendelsen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_lukket IS NOT NULL THEN
        RAISE EXCEPTION 'm17_sett_avsender: henvendelsen er lukket — en'
            ' adresse på en lukket henvendelse er en adresse ingen skal'
            ' bruke' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.henvendelse
       SET avsender_kryptert = p_kryptert, nonce_avsender = p_nonce,
           avsender_key_id = p_key_id, avsender_maske = btrim(p_maske),
           avsender_satt_ts = now()
     WHERE tenant = p_tenant AND henvendelse_id = p_henvendelse_id;
    PERFORM public.m17_evidens(p_tenant, p_henvendelse_id,
        'avsender.satt', p_aktor, jsonb_build_object('key_id', p_key_id));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m17_sett_avsender(TEXT, UUID, TEXT, BYTEA, BYTEA,
    TEXT, TEXT) FROM PUBLIC;

-- Inntaket med adresse i samme kall: 102-døra tar imot (hash, innhold,
-- inntaksidempotens), og adressen legges på når henvendelsen ER ny.
-- Et gjentatt inntak (samme kanal + referanse) rører ikke adressen.
CREATE FUNCTION m17_ta_imot(
    p_tenant TEXT, p_henvendelse_id UUID, p_kanal TEXT,
    p_ekstern_ref TEXT, p_mottatt TIMESTAMPTZ, p_avsender_hash TEXT,
    p_emne_kryptert BYTEA, p_nonce_emne BYTEA,
    p_kropp_kryptert BYTEA, p_nonce_kropp BYTEA, p_key_id TEXT,
    p_aktor TEXT, p_avsender_maske TEXT, p_avsender_kryptert BYTEA,
    p_nonce_avsender BYTEA)
RETURNS TABLE(ny BOOLEAN, lagret_henvendelse_id UUID)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_ny BOOLEAN; v_hid UUID;
BEGIN
    SELECT t.ny, t.lagret_henvendelse_id INTO v_ny, v_hid
      FROM public.m17_ta_imot(p_tenant, p_henvendelse_id, p_kanal,
                              p_ekstern_ref, p_mottatt, p_avsender_hash,
                              p_emne_kryptert, p_nonce_emne,
                              p_kropp_kryptert, p_nonce_kropp, p_key_id,
                              p_aktor) t;
    IF v_ny AND p_avsender_kryptert IS NOT NULL THEN
        PERFORM public.m17_sett_avsender(p_tenant, v_hid, p_avsender_maske,
                                         p_avsender_kryptert,
                                         p_nonce_avsender, p_key_id,
                                         p_aktor);
    END IF;
    ny := v_ny; lagret_henvendelse_id := v_hid;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m17_ta_imot(TEXT, UUID, TEXT, TEXT, TIMESTAMPTZ, TEXT,
    BYTEA, BYTEA, BYTEA, BYTEA, TEXT, TEXT, TEXT, BYTEA, BYTEA) FROM PUBLIC;

-- Den tredje dommen. 102s tekst «modulen SENDER ingenting» står i 102;
-- her sies det som nå er sant: et menneske godkjenner, plattformen
-- sender innenfor policyen (PR 2–5).
CREATE OR REPLACE FUNCTION m17_avgjor_utkast(
    p_tenant TEXT, p_utkast_id UUID, p_status TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_hid UUID; v_gammel TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_avgjor_utkast');
    IF p_status IS NULL
       OR p_status NOT IN ('forkastet', 'brukt_manuelt', 'godkjent') THEN
        RAISE EXCEPTION 'm17_avgjor_utkast: status må være forkastet,'
            ' brukt_manuelt eller godkjent — «sendt» er bokføringens'
            ' ord, aldri et menneskes'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT henvendelse_id, status INTO v_hid, v_gammel
      FROM public.svarutkast
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id;
    IF v_hid IS NULL THEN
        RAISE EXCEPTION 'm17_avgjor_utkast: utkastet finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_gammel = p_status THEN
        RETURN false;
    END IF;
    UPDATE public.svarutkast SET status = p_status
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id
       AND status = 'foreslatt';
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RAISE EXCEPTION 'm17_avgjor_utkast: utkastet er alt avgjort som'
            ' %, og en avgjørelse går ikke om igjen', v_gammel
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM public.m17_evidens(
        p_tenant, v_hid, 'utkast.' || p_status, p_aktor,
        jsonb_build_object('utkast_id', p_utkast_id::text));
    RETURN true;
END $$;

-- Køen bærer om adressen finnes, og masken — aldri adressen.
DROP FUNCTION m17_koen(TEXT, INT);
CREATE FUNCTION m17_koen(p_tenant TEXT, p_grense INT)
RETURNS TABLE(henvendelse_id UUID, kanal TEXT, ekstern_ref TEXT,
              mottatt TIMESTAMPTZ, avsender_hash TEXT, alder_dogn INT,
              prioritet TEXT, tema TEXT, handlingstype TEXT,
              klassifisert_av TEXT, i_unntakskoe BOOLEAN,
              antall_utkast INT, brukt_utkast BOOLEAN,
              apne_funn TEXT[], har_avsender BOOLEAN,
              avsender_maske TEXT, godkjent_utkast BOOLEAN)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_koen');
    RETURN QUERY
    SELECT h.henvendelse_id, h.kanal, h.ekstern_ref, h.mottatt,
           h.avsender_hash,
           (current_date - h.mottatt::date)::int,
           k.prioritet, k.tema, k.handlingstype, k.kilde,
           h.unntak_id IS NOT NULL,
           (SELECT count(*)::int FROM public.svarutkast u
             WHERE u.tenant = h.tenant
               AND u.henvendelse_id = h.henvendelse_id),
           EXISTS (SELECT 1 FROM public.svarutkast u
                    WHERE u.tenant = h.tenant
                      AND u.henvendelse_id = h.henvendelse_id
                      AND u.status = 'brukt_manuelt'),
           coalesce((SELECT array_agg(f.funntype ORDER BY f.funntype)
                       FROM public.henvendelsesfunn f
                      WHERE f.tenant = h.tenant
                        AND f.henvendelse_id = h.henvendelse_id
                        AND f.apen), ARRAY[]::TEXT[]),
           (h.avsender_kryptert IS NOT NULL), h.avsender_maske,
           EXISTS (SELECT 1 FROM public.svarutkast u
                    WHERE u.tenant = h.tenant
                      AND u.henvendelse_id = h.henvendelse_id
                      AND u.status = 'godkjent')
      FROM public.henvendelse h
      LEFT JOIN public.klassifisering k
        ON k.tenant = h.tenant AND k.henvendelse_id = h.henvendelse_id
     WHERE h.tenant = p_tenant AND h.lukket_ts IS NULL
     ORDER BY h.mottatt, h.henvendelse_id
     LIMIT greatest(least(coalesce(p_grense, 100), 1000), 1);
END $$;
REVOKE ALL ON FUNCTION m17_koen(TEXT, INT) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_sett_avsender(TEXT, UUID,'
            ' TEXT, BYTEA, BYTEA, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_ta_imot(TEXT, UUID, TEXT,'
            ' TEXT, TIMESTAMPTZ, TEXT, BYTEA, BYTEA, BYTEA, BYTEA, TEXT,'
            ' TEXT, TEXT, BYTEA, BYTEA) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_koen(TEXT, INT) TO disponit';
    END IF;
END $$;
RESET ROLE;
