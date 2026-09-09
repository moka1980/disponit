-- =====================================================================
-- 153 — M-44: MOTTAKEREN HAR EN ADRESSE, KAMPANJEN HAR ET INNHOLD
-- =====================================================================
--
-- ARC B, kampanje (PR 1 av 6). v1 (114) kastet adressen etter maske og
-- hash — riktig så lenge ingenting skulle nå kunden. Skal plattformen
-- en dag levere kampanjen innenfor policyen, må registeret vite HVOR
-- den skal: adressen ligger nå KRYPTERT på mottakeren (tenantens DEK,
-- AAD `m44:kontakt`), som 146 gjorde for fordringen. Flaten viser
-- fortsatt bare masken; basen ser aldri klartekst.
--
-- Og kampanjen har et INNHOLD: emne og tekst, tenantens egne ord
-- (ingen persondata — mottakerens navn flettes inn ved levering, aldri
-- lagret i teksten). En kampanje uten innhold kan planlegges som før,
-- men kan aldri leveres: fraværet er synlig i flaten.
--
-- Ingenting her leverer noe. Dørene er skrivedører for registeret.
-- ---------------------------------------------------------------------

ALTER TABLE kampanjemottaker
    ADD COLUMN IF NOT EXISTS kontakt_kryptert BYTEA,
    ADD COLUMN IF NOT EXISTS kontakt_nonce BYTEA,
    ADD COLUMN IF NOT EXISTS kontakt_key_id TEXT,
    ADD COLUMN IF NOT EXISTS kontakt_satt_ts TIMESTAMPTZ;
ALTER TABLE kampanjemottaker DROP CONSTRAINT IF EXISTS
    kampanjemottaker_kontakt_helhet;
ALTER TABLE kampanjemottaker ADD CONSTRAINT kampanjemottaker_kontakt_helhet
    CHECK ((kontakt_kryptert IS NULL AND kontakt_nonce IS NULL
            AND kontakt_key_id IS NULL AND kontakt_satt_ts IS NULL)
           OR (kontakt_kryptert IS NOT NULL AND kontakt_nonce IS NOT NULL
               AND kontakt_key_id IS NOT NULL AND kontakt_satt_ts IS NOT NULL));

ALTER TABLE kampanje
    ADD COLUMN IF NOT EXISTS emne TEXT,
    ADD COLUMN IF NOT EXISTS tekst TEXT;
ALTER TABLE kampanje DROP CONSTRAINT IF EXISTS kampanje_innhold_helhet;
ALTER TABLE kampanje ADD CONSTRAINT kampanje_innhold_helhet CHECK (
    (emne IS NULL AND tekst IS NULL)
    OR (emne IS NOT NULL AND tekst IS NOT NULL
        AND length(btrim(emne)) BETWEEN 1 AND 200
        AND length(btrim(tekst)) BETWEEN 1 AND 4000));

SET LOCAL ROLE disponit_kampanje_eier;

-- Adressen, kryptert. Bare på en aktiv mottaker; kan rettes.
CREATE FUNCTION m44_sett_kontakt(
    p_tenant TEXT, p_mottaker_id UUID, p_kryptert BYTEA, p_nonce BYTEA,
    p_key_id TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_aktiv BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_sett_kontakt');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_kryptert IS NULL OR p_nonce IS NULL OR p_key_id IS NULL
       OR length(p_kryptert) < 17 OR length(p_nonce) <> 12 THEN
        RAISE EXCEPTION 'm44_sett_kontakt: chiffertekst, nonce og key_id'
            ' må være satt' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT m.aktiv INTO v_aktiv FROM public.kampanjemottaker m
     WHERE m.tenant = p_tenant AND m.mottaker_id = p_mottaker_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm44_sett_kontakt: mottakeren finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF NOT v_aktiv THEN
        RAISE EXCEPTION 'm44_sett_kontakt: mottakeren er deaktivert —'
            ' en adresse på en deaktivert mottaker er en adresse ingen'
            ' skal bruke' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.kampanjemottaker
       SET kontakt_kryptert = p_kryptert, kontakt_nonce = p_nonce,
           kontakt_key_id = p_key_id, kontakt_satt_ts = now()
     WHERE tenant = p_tenant AND mottaker_id = p_mottaker_id;
    PERFORM public.m44_evidens(p_tenant, p_mottaker_id,
        'kontakt_satt', p_aktor, jsonb_build_object('key_id', p_key_id));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m44_sett_kontakt(TEXT, UUID, BYTEA, BYTEA, TEXT, TEXT)
    FROM PUBLIC;

-- Registrering MED kryptert adresse i samme transaksjon (overlast av
-- 114-døra: maske og hash som før, chifferteksten i tillegg).
CREATE FUNCTION m44_registrer_mottaker(
    p_tenant TEXT, p_mottaker_id UUID, p_ekstern_ref TEXT, p_navn TEXT,
    p_kontakt TEXT, p_kryptert BYTEA, p_nonce BYTEA, p_key_id TEXT,
    p_aktor TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_maske TEXT;
BEGIN
    v_maske := public.m44_registrer_mottaker(
        p_tenant, p_mottaker_id, p_ekstern_ref, p_navn, p_kontakt, p_aktor);
    PERFORM public.m44_sett_kontakt(p_tenant, p_mottaker_id, p_kryptert,
                                    p_nonce, p_key_id, p_aktor);
    RETURN v_maske;
END $$;
REVOKE ALL ON FUNCTION m44_registrer_mottaker(TEXT, UUID, TEXT, TEXT, TEXT,
    BYTEA, BYTEA, TEXT, TEXT) FROM PUBLIC;

-- Innholdet: emne og tekst. Bare på en registrert (ikke avlyst)
-- kampanje; kan rettes fram til den er levert (PR 5 låser).
CREATE FUNCTION m44_sett_innhold(
    p_tenant TEXT, p_kampanje_id UUID, p_emne TEXT, p_tekst TEXT,
    p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_sett_innhold');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_emne IS NULL OR length(btrim(p_emne)) NOT BETWEEN 1 AND 200
       OR p_tekst IS NULL OR length(btrim(p_tekst)) NOT BETWEEN 1 AND 4000
    THEN
        RAISE EXCEPTION 'm44_sett_innhold: emne (1–200 tegn) og tekst'
            ' (1–4000 tegn) må begge være satt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT k.status INTO v_status FROM public.kampanje k
     WHERE k.tenant = p_tenant AND k.kampanje_id = p_kampanje_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm44_sett_innhold: kampanjen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_status = 'avlyst' THEN
        RAISE EXCEPTION 'm44_sett_innhold: kampanjen er avlyst'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.kampanje
       SET emne = btrim(p_emne), tekst = btrim(p_tekst)
     WHERE tenant = p_tenant AND kampanje_id = p_kampanje_id;
    PERFORM public.m44_evidens(p_tenant, NULL, 'kampanje_innhold_satt',
        p_aktor, jsonb_build_object('kampanje_id', p_kampanje_id,
                                    'emne_lengde', length(btrim(p_emne)),
                                    'tekst_lengde', length(btrim(p_tekst))));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m44_sett_innhold(TEXT, UUID, TEXT, TEXT, TEXT)
    FROM PUBLIC;

CREATE FUNCTION m44_registrer_kampanje(
    p_tenant TEXT, p_kampanje_id UUID, p_ekstern_ref TEXT,
    p_navn TEXT, p_formal TEXT, p_avmeldingslenke TEXT,
    p_planlagt_sendt DATE, p_emne TEXT, p_tekst TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.m44_registrer_kampanje(
        p_tenant, p_kampanje_id, p_ekstern_ref, p_navn, p_formal,
        p_avmeldingslenke, p_planlagt_sendt, p_aktor);
    PERFORM public.m44_sett_innhold(p_tenant, p_kampanje_id, p_emne,
                                    p_tekst, p_aktor);
END $$;
REVOKE ALL ON FUNCTION m44_registrer_kampanje(TEXT, UUID, TEXT, TEXT, TEXT,
    TEXT, DATE, TEXT, TEXT, TEXT) FROM PUBLIC;

-- Lesedørene sier om adressen og innholdet finnes — aldri hva de er.
DROP FUNCTION m44_mottakerne(TEXT, INT);
CREATE FUNCTION m44_mottakerne(p_tenant TEXT, p_grense INT)
RETURNS TABLE (mottaker_id UUID, ekstern_ref TEXT, navn TEXT,
               kontakt_maske TEXT, aktiv BOOLEAN, tilstand TEXT,
               kanal TEXT, siste_samtykke DATE, i_planer BIGINT,
               apne_funn TEXT[], har_kontakt BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_mottakerne');
    RETURN QUERY
    WITH siste AS (
        SELECT DISTINCT ON (s.mottaker_id)
               s.mottaker_id, s.tilstand, s.kanal, s.inntruffet
          FROM public.samtykkehendelse s
         WHERE s.tenant = p_tenant
         ORDER BY s.mottaker_id, s.inntruffet DESC, s.registrert DESC)
    SELECT m.mottaker_id, m.ekstern_ref, m.navn, m.kontakt_maske,
           m.aktiv, s.tilstand, s.kanal, s.inntruffet,
           (SELECT count(*) FROM public.kampanjeplan pl
             WHERE pl.tenant = p_tenant
               AND pl.mottaker_id = m.mottaker_id),
           (SELECT coalesce(array_agg(f.funntype ORDER BY f.funntype),
                            ARRAY[]::TEXT[])
              FROM public.kampanjefunn f
             WHERE f.tenant = p_tenant
               AND f.mottaker_id = m.mottaker_id AND f.apen),
           (m.kontakt_kryptert IS NOT NULL)
      FROM public.kampanjemottaker m
      LEFT JOIN siste s ON s.mottaker_id = m.mottaker_id
     WHERE m.tenant = p_tenant
     ORDER BY m.aktiv DESC, m.ekstern_ref
     LIMIT greatest(p_grense, 1);
END $$;
REVOKE ALL ON FUNCTION m44_mottakerne(TEXT, INT) FROM PUBLIC;

DROP FUNCTION m44_kampanjene(TEXT, INT);
CREATE FUNCTION m44_kampanjene(p_tenant TEXT, p_grense INT)
RETURNS TABLE (kampanje_id UUID, ekstern_ref TEXT, navn TEXT,
               formal TEXT, avmeldingslenke TEXT,
               planlagt_sendt DATE, status TEXT, mottakere BIGINT,
               opprettet TIMESTAMPTZ, opprettet_av TEXT,
               har_innhold BOOLEAN, emne TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_kampanjene');
    RETURN QUERY
    SELECT k.kampanje_id, k.ekstern_ref, k.navn, k.formal,
           k.avmeldingslenke, k.planlagt_sendt, k.status,
           (SELECT count(*) FROM public.kampanjeplan pl
             WHERE pl.tenant = p_tenant
               AND pl.kampanje_id = k.kampanje_id),
           k.opprettet, k.opprettet_av,
           (k.tekst IS NOT NULL), k.emne
      FROM public.kampanje k
     WHERE k.tenant = p_tenant
     ORDER BY k.planlagt_sendt DESC, k.opprettet DESC
     LIMIT greatest(p_grense, 1);
END $$;
REVOKE ALL ON FUNCTION m44_kampanjene(TEXT, INT) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_mottakerne(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_kampanjene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_sett_kontakt(TEXT, UUID,'
            ' BYTEA, BYTEA, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_registrer_mottaker(TEXT,'
            ' UUID, TEXT, TEXT, TEXT, BYTEA, BYTEA, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_sett_innhold(TEXT, UUID,'
            ' TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_registrer_kampanje(TEXT,'
            ' UUID, TEXT, TEXT, TEXT, TEXT, DATE, TEXT, TEXT, TEXT)'
            ' TO disponit';
    END IF;
END $$;
RESET ROLE;
