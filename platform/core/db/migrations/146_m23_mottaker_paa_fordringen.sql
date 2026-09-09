-- =====================================================================
-- 146 — M-23: FORDRINGEN VET HVEM PURRINGEN SKAL TIL
-- =====================================================================
--
-- Første ledd i purring som selvbetjening (ARC B, eiervedtak 9/9): før
-- systemet kan sende en purring innenfor policy, må fordringen bære en
-- mottaker. 104 hadde kunde_ref og fakturanummer — ingen adresse.
--
-- ADRESSEN LAGRES ALDRI I KLARTEKST. Den krypteres med tenantens DEK i
-- API-laget (M-17s form, `db/kryptering`), og basen holder chiffertekst,
-- nonce og nøkkel-id — pluss en MASKE («k****@domene.no») som er alt
-- flaten og loggen får se. Utføreren (PR 4) er den eneste som dekrypterer,
-- og den gjør det i det øyeblikket den sender.
--
-- MOTTAKEREN KAN RETTES så lenge fordringen er åpen: en feil adresse må
-- kunne rettes før purringen går, og hver retting bærer aktørens navn og
-- et evidensspor. På en avsluttet fordring er den frosset som alt annet.
--
-- Registreringsdøra får en OVERLAST med mottakeren som fire valgfrie
-- felter; 104s dør står urørt (tester og eldre kallere), og den nye
-- kaller den. Lesedøra `m23_fordringene` får masken som siste kolonne —
-- en returtype endres ikke med OR REPLACE, så den droppes og lages på
-- nytt med samme GRANT.
-- ---------------------------------------------------------------------
ALTER TABLE fordring
    ADD COLUMN IF NOT EXISTS mottaker_maske TEXT
        CHECK (mottaker_maske IS NULL OR mottaker_maske ~ '[^[:space:]]'),
    ADD COLUMN IF NOT EXISTS mottaker_kryptert BYTEA,
    ADD COLUMN IF NOT EXISTS mottaker_nonce BYTEA
        CHECK (mottaker_nonce IS NULL OR length(mottaker_nonce) = 12),
    ADD COLUMN IF NOT EXISTS mottaker_key_id TEXT,
    -- LIKHET uten klartekst: sha256 over tenant + normalisert adresse.
    -- Masken kan ikke bære den — «r1@x.no» og «r2@x.no» har samme maske.
    ADD COLUMN IF NOT EXISTS mottaker_hash TEXT
        CHECK (mottaker_hash IS NULL OR mottaker_hash ~ '^[0-9a-f]{64}$'),
    ADD COLUMN IF NOT EXISTS mottaker_satt_ts TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS mottaker_satt_av TEXT;

-- Alt eller ingenting: en maske uten chiffertekst er en adresse ingen kan
-- sende til, og en chiffertekst uten maske er en adresse ingen kan se at
-- finnes.
ALTER TABLE fordring DROP CONSTRAINT IF EXISTS fordring_mottaker_helhet;
ALTER TABLE fordring ADD CONSTRAINT fordring_mottaker_helhet CHECK (
    (mottaker_maske IS NULL AND mottaker_kryptert IS NULL
        AND mottaker_nonce IS NULL AND mottaker_key_id IS NULL
        AND mottaker_hash IS NULL
        AND mottaker_satt_ts IS NULL AND mottaker_satt_av IS NULL)
    OR (mottaker_maske IS NOT NULL AND mottaker_kryptert IS NOT NULL
        AND mottaker_nonce IS NOT NULL AND mottaker_key_id IS NOT NULL
        AND mottaker_hash IS NOT NULL
        AND mottaker_satt_ts IS NOT NULL AND mottaker_satt_av IS NOT NULL
        AND mottaker_satt_av ~ '[^[:space:]]'));

-- Vakten lages som migrator, slik 104 gjorde (den står før eierbyttet der).
CREATE OR REPLACE FUNCTION m23_fordring_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT; v_sum BIGINT;
BEGIN
    -- TRUNCATE HAR SIN EGEN ARM, av samme grunn som i purretrinnvakten.
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'fordring: TRUNCATE avvist — et krav ettergis med'
            ' begrunnelse, det forsvinner aldri i en tabelltømming'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'fordring: DELETE avvist — et krav ettergis med'
            ' begrunnelse, det slettes aldri som rad'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.fordring_id IS DISTINCT FROM OLD.fordring_id
       OR NEW.fakturanummer IS DISTINCT FROM OLD.fakturanummer
       OR NEW.belop_ore IS DISTINCT FROM OLD.belop_ore
       OR NEW.utstedt IS DISTINCT FROM OLD.utstedt
       OR NEW.forfall IS DISTINCT FROM OLD.forfall
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'fordring: identiteten og beløpet er frosset — et'
            ' annet beløp er et annet krav, og en flyttet forfallsdato er'
            ' en ny avtale' USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- MOTTAKEREN (146): kan settes og rettes på en ÅPEN fordring av en
    -- navngitt aktør — en feil adresse må kunne rettes før purringen går.
    -- På en avsluttet fordring er den frosset som alt annet.
    IF NEW.mottaker_maske IS DISTINCT FROM OLD.mottaker_maske
       OR NEW.mottaker_kryptert IS DISTINCT FROM OLD.mottaker_kryptert
       OR NEW.mottaker_nonce IS DISTINCT FROM OLD.mottaker_nonce
       OR NEW.mottaker_key_id IS DISTINCT FROM OLD.mottaker_key_id
       OR NEW.mottaker_hash IS DISTINCT FROM OLD.mottaker_hash THEN
        IF OLD.status <> 'apen' THEN
            RAISE EXCEPTION 'fordring: mottakeren endres ikke på en %'
                ' fordring — den er gjort opp', OLD.status
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL OR NEW.mottaker_satt_av IS DISTINCT FROM v_aktor
           OR NEW.mottaker_satt_ts IS NULL THEN
            RAISE EXCEPTION 'fordring: mottakeren settes av en navngitt'
                ' aktør, og navnet står på raden'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    -- DOM 4, første halvdel: EN AVSLUTTET FORDRING ESKALERER ALDRI.
    IF OLD.status <> 'apen' THEN
        IF NEW.status IS DISTINCT FROM OLD.status THEN
            RAISE EXCEPTION 'fordring: en avsluttet fordring gjenåpnes'
                ' ikke' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.trinn IS DISTINCT FROM OLD.trinn THEN
            RAISE EXCEPTION 'fordring: trinnet flyttes ikke på en % '
                'fordring — den er gjort opp', OLD.status
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    -- DOM 3: ETT HAKK OM GANGEN, OG BARE FRAMOVER. Et hopp fra trinn 1
    -- til trinn 3 er en eskalering ingen besluttet — og for kunden er
    -- forskjellen mellom en påminnelse og et inkassovarsel hele saken.
    IF NEW.trinn IS DISTINCT FROM OLD.trinn THEN
        IF NEW.trinn <> OLD.trinn + 1 THEN
            RAISE EXCEPTION 'fordring: trinnet går fra % til %, og et'
                ' trinn går ETT hakk framover om gangen — et hopp er en'
                ' eskalering ingen besluttet', OLD.trinn, NEW.trinn
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL THEN
            RAISE EXCEPTION 'fordring: en trinnflytting krever en navngitt'
                ' aktør (disponit.aktor) — tiden purrer ingen'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    -- DOM 4, andre halvdel: `betalt_ore` ER summen av hendelsene, ikke
    -- et fritt tall. En vedlikeholdt avledning ingen kontrollerer er en
    -- denormalisering som driver (100s `sist_etterprovd`-form).
    IF NEW.betalt_ore IS DISTINCT FROM OLD.betalt_ore THEN
        SELECT coalesce(sum(h.belop_ore), 0)::bigint INTO v_sum
          FROM public.fordringshendelse h
         WHERE h.tenant = NEW.tenant AND h.fordring_id = NEW.fordring_id
           AND h.art = 'betaling';
        IF NEW.betalt_ore <> v_sum THEN
            RAISE EXCEPTION 'fordring: betalt_ore (%) er ikke summen av'
                ' de registrerte innbetalingene (%) — et betalt-beløp man'
                ' kan skrive fritt måler ingenting', NEW.betalt_ore, v_sum
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    IF NEW.status <> OLD.status AND NEW.status <> 'apen' THEN
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL OR NEW.avsluttet_av IS DISTINCT FROM v_aktor THEN
            RAISE EXCEPTION 'fordring: avsluttet_av (%) er ikke aktøren'
                ' som avslutter (%)',
                coalesce(NEW.avsluttet_av, '<null>'),
                coalesce(v_aktor, '<ingen>')
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;

SET LOCAL ROLE disponit_fordring_eier;

CREATE FUNCTION m23_sett_mottaker(
    p_tenant TEXT, p_fordring_id UUID, p_maske TEXT, p_kryptert BYTEA,
    p_nonce BYTEA, p_key_id TEXT, p_hash TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_f RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_sett_mottaker');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_maske IS NULL OR p_maske !~ '[^[:space:]]'
       OR p_kryptert IS NULL OR p_nonce IS NULL OR p_key_id IS NULL
       OR p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'm23_sett_mottaker: en mottaker bærer maske,'
            ' chiffertekst, nonce, nøkkel og hash — alle fem'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm23_sett_mottaker: mottakeren settes av en'
            ' navngitt aktør' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO v_f FROM public.fordring f
     WHERE f.tenant = p_tenant AND f.fordring_id = p_fordring_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm23_sett_mottaker: fordringen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_f.status <> 'apen' THEN
        RAISE EXCEPTION 'm23_sett_mottaker: fordringen er % — mottakeren'
            ' på en avsluttet fordring endres ikke', v_f.status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- SAMME ADRESSE PÅ NYTT ER ET STILLE JA (SP-2). Likheten måles på
    -- HASHEN, ikke masken: «r1@x.no» og «r2@x.no» har samme maske, og
    -- den andre er en retting som skal lagres (CodeRabbit).
    IF v_f.mottaker_hash IS NOT DISTINCT FROM p_hash THEN
        RETURN false;
    END IF;
    UPDATE public.fordring
       SET mottaker_maske = p_maske, mottaker_kryptert = p_kryptert,
           mottaker_nonce = p_nonce, mottaker_key_id = p_key_id,
           mottaker_hash = p_hash,
           mottaker_satt_ts = now(), mottaker_satt_av = btrim(p_aktor)
     WHERE tenant = p_tenant AND fordring_id = p_fordring_id;
    PERFORM public.m23_evidens(
        p_tenant, p_fordring_id, 'fordring.mottaker_satt', p_aktor,
        jsonb_build_object('maske', p_maske,
                           'rettet', v_f.mottaker_maske IS NOT NULL));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m23_sett_mottaker(TEXT, UUID, TEXT, BYTEA, BYTEA,
                                         TEXT, TEXT, TEXT) FROM PUBLIC;

-- OVERLASTEN: registrer + mottaker i én transaksjon. Uten mottaker er den
-- 104s dør, ord for ord — den kaller den.
CREATE FUNCTION m23_registrer_fordring(
    p_tenant TEXT, p_fordring_id UUID, p_kunde_ref TEXT,
    p_fakturanummer TEXT, p_belop_ore BIGINT, p_utstedt DATE,
    p_forfall DATE, p_aktor TEXT,
    p_mottaker_maske TEXT, p_mottaker_kryptert BYTEA,
    p_mottaker_nonce BYTEA, p_mottaker_key_id TEXT, p_mottaker_hash TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_ny BOOLEAN;
BEGIN
    v_ny := public.m23_registrer_fordring(
        p_tenant, p_fordring_id, p_kunde_ref, p_fakturanummer,
        p_belop_ore, p_utstedt, p_forfall, p_aktor);
    -- BARE NÅR FORDRINGEN ER NY: et gjentatt registreringskall (SP-2)
    -- skal ikke skrive over en mottaker som er rettet i mellomtiden.
    IF v_ny AND p_mottaker_maske IS NOT NULL THEN
        PERFORM public.m23_sett_mottaker(
            p_tenant, p_fordring_id, p_mottaker_maske,
            p_mottaker_kryptert, p_mottaker_nonce, p_mottaker_key_id,
            p_mottaker_hash, p_aktor);
    END IF;
    RETURN v_ny;
END $$;
REVOKE ALL ON FUNCTION m23_registrer_fordring(TEXT, UUID, TEXT, TEXT,
    BIGINT, DATE, DATE, TEXT, TEXT, BYTEA, BYTEA, TEXT, TEXT) FROM PUBLIC;

DROP FUNCTION m23_fordringene(TEXT, INT);
CREATE FUNCTION m23_fordringene(p_tenant TEXT, p_grense INT)
RETURNS TABLE(fordring_id UUID, kunde_ref TEXT, fakturanummer TEXT,
              belop_ore BIGINT, betalt_ore BIGINT, rest_ore BIGINT,
              utstedt DATE, forfall DATE, dogn_over_forfall INT,
              status TEXT, trinn INT, trinn_navn TEXT,
              moden_for_trinn INT, apne_funn TEXT[],
              mottaker_maske TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_fordringene');
    RETURN QUERY
    SELECT f.fordring_id, f.kunde_ref, f.fakturanummer, f.belop_ore,
           f.betalt_ore, f.belop_ore - f.betalt_ore, f.utstedt, f.forfall,
           (current_date - f.forfall)::int,
           f.status, f.trinn,
           (SELECT t.navn FROM public.purretrinn t
             WHERE t.tenant = f.tenant AND t.trinn_nr = f.trinn),
           -- MODEN FOR TRINN: det HØYESTE trinnet fordringens alder har
           -- passert. Er det høyere enn `trinn`, står den og venter på
           -- et menneske — og det er hele opplysningen flaten finnes
           -- for. Regnet i basen, i samme skann som raden.
           (SELECT max(t.trinn_nr) FROM public.purretrinn t
             WHERE t.tenant = f.tenant
               AND current_date - f.forfall >= t.dogn_etter_forfall),
           coalesce((SELECT array_agg(ff.funntype ORDER BY ff.funntype)
                       FROM public.fordringsfunn ff
                      WHERE ff.tenant = f.tenant
                        AND ff.fordring_id = f.fordring_id
                        AND ff.apen), ARRAY[]::TEXT[]),
           f.mottaker_maske
      FROM public.fordring f
     WHERE f.tenant = p_tenant
     -- Åpne først, deretter mest forfalt. `fordring_id` som tiebreaker
     -- (100s bitmap-lærdom): to fordringer med samme forfall skal ikke
     -- bytte plass mellom to kall.
     ORDER BY (f.status <> 'apen'), f.forfall, f.fordring_id
     LIMIT greatest(least(coalesce(p_grense, 100), 1000), 1);
END $$;
REVOKE ALL ON FUNCTION m23_fordringene(TEXT, INT) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_fordringene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_sett_mottaker(TEXT, UUID,'
            ' TEXT, BYTEA, BYTEA, TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_registrer_fordring(TEXT,'
            ' UUID, TEXT, TEXT, BIGINT, DATE, DATE, TEXT, TEXT, BYTEA,'
            ' BYTEA, TEXT, TEXT) TO disponit';
    END IF;
END $$;
RESET ROLE;
