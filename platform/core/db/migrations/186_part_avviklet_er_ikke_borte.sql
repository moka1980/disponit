-- 186 — En avviklet kunde FINNES. Den er bare avviklet.
--
-- 183s vakt slo sammen to ulike ting i én feilkode:
--
--     PERFORM 1 FROM part WHERE ... AND aktiv;
--     IF NOT FOUND THEN RAISE ... ERRCODE = 'foreign_key_violation';
--
-- «Finnes ikke» og «er avviklet» er ikke det samme, og API-laget kan
-- ikke skille dem når basen ikke gjør det: begge ble 404 «ikke_funnet».
-- For brukeren var det en LØGN — kunden står i lista hun nettopp så på,
-- merket avviklet, og systemet sier den ikke finnes.
--
-- FUNNET VED Å KJØRE, i HTTP-porten som skulle måle at avviklingen
-- stenger for nye kontaktpunkter. Porten ventet 409 og fikk 404, og det
-- var porten som hadde rett.
--
-- KODEN ER HUSETS EGEN: `integrity_constraint_violation` (23000), som
-- M-6s dører bruker for nøyaktig samme slags nei (180). Og valget er
-- ikke kosmetisk — i psycopg er `CheckViolation`, `ForeignKeyViolation`
-- og `IntegrityConstraintViolation` SØSKEN under `IntegrityError`, ikke
-- foreldre og barn. En `check_violation` her ville gått rett forbi
-- API-lagets `except IntegrityConstraintViolation` og blitt en 500 på en
-- tilstand brukeren selv skapte. Målt ved å kjøre, ikke antatt.
--
-- Kroppen er ellers 183s, ordrett — bare vakten er delt i to.
SET LOCAL ROLE disponit_m37_claimer;

CREATE OR REPLACE FUNCTION part_sett_kontakt(p_tenant TEXT, p_part_id UUID,
                                  p_kanal TEXT, p_maske TEXT,
                                  p_kryptert BYTEA, p_nonce BYTEA,
                                  p_key_id TEXT, p_pseudonym TEXT,
                                  p_primar BOOLEAN, p_merkelapp TEXT,
                                  p_aktor TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID; v_aktiv BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'part_sett_kontakt');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'part_sett_kontakt: et kontaktpunkt har en kilde'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT aktiv INTO v_aktiv FROM public.part
     WHERE tenant = p_tenant AND part_id = p_part_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'part_sett_kontakt: parten finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF NOT v_aktiv THEN
        RAISE EXCEPTION 'part_sett_kontakt: parten er avviklet — et'
            ' kontaktpunkt hører til et levende kundeforhold'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    -- Samme atomiske form som over: adressen er enten der, eller den
    -- skrives — aldri «sjekk, så skriv» med et vindu imellom.
    -- Konfliktmålet må nevne indeksens predikat, fordi unikheten bare
    -- gjelder LEVENDE rader (en slettet adresse skal kunne legges inn
    -- på nytt).
    INSERT INTO public.partkontakt
        (tenant, kontakt_id, part_id, kanal, verdi_maske,
         verdi_kryptert, verdi_nonce, verdi_key_id, verdi_pseudonym,
         merkelapp, opprettet_av)
    VALUES (p_tenant, public.gen_random_uuid(), p_part_id, p_kanal,
            p_maske, p_kryptert, p_nonce, p_key_id, p_pseudonym,
            p_merkelapp, p_aktor)
    ON CONFLICT (tenant, part_id, kanal, verdi_pseudonym)
        WHERE slettet_ts IS NULL DO NOTHING
    RETURNING kontakt_id INTO v_id;
    IF v_id IS NULL THEN
        SELECT kontakt_id INTO v_id FROM public.partkontakt
         WHERE tenant = p_tenant AND part_id = p_part_id
           AND kanal = p_kanal AND verdi_pseudonym = p_pseudonym
           AND slettet_ts IS NULL;
    END IF;
    IF coalesce(p_primar, false) THEN
        -- ÉN PRIMÆR PER KANAL: den forrige trer til side i samme
        -- transaksjon, ellers feller delindeksen skrivingen.
        UPDATE public.partkontakt SET primar = false
         WHERE tenant = p_tenant AND part_id = p_part_id
           AND kanal = p_kanal AND primar AND slettet_ts IS NULL
           AND kontakt_id <> v_id;
        UPDATE public.partkontakt SET primar = true
         WHERE tenant = p_tenant AND kontakt_id = v_id;
    END IF;
    RETURN v_id;
END $$;
REVOKE ALL ON FUNCTION part_sett_kontakt(TEXT, UUID, TEXT, TEXT, BYTEA,
    BYTEA, TEXT, TEXT, BOOLEAN, TEXT, TEXT) FROM PUBLIC;

-- `CREATE OR REPLACE` beholder grantene 183 ga; linjen står her som
-- en påminnelse om at det er en EGENSKAP ved REPLACE, ikke flaks.

RESET ROLE;
