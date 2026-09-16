-- 205 — DOMENEREGLER TAR UNDERDOMENER.
--
-- MÅLT PÅ VERTEN 15/9, første planrunde etter 204: 4 av 18 klassifisert.
-- Blant de 14 som sto igjen: «emailnotifications.microsoft.com» — med en
-- regel for «microsoft.com» satt. 204 matchet domenet EKSAKT, og
-- leverandørers varsler kommer nettopp fra underdomener. En regel som
-- må gjentas per underdomene er en liste ingen holder oppdatert.
--
-- Suffikset krever punktumet foran («%.microsoft.com»): «notmicrosoft.com»
-- er et annet domene, og skal ikke treffes. Signatur og eier er
-- uendret, så eierskapsdesignet står.

SET LOCAL ROLE disponit_kundeservice_eier;

CREATE OR REPLACE FUNCTION m17_klassifiser_etter_regel(p_grense INT DEFAULT 500)
RETURNS TABLE(tenanter INT, klassifisert INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_n INT; v_grense INT; v_h RECORD;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm17_klassifiser_etter_regel: kryss-tenant-døra'
            ' nekter tenantkontekst' USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_grense := greatest(least(coalesce(p_grense, 500), 5000), 1);
    tenanter := 0; klassifisert := 0;
    -- Bare tenanter som HAR regler — resten koster ingenting.
    SELECT array_agg(DISTINCT r.tenant ORDER BY r.tenant) INTO v_tenanter
      FROM public.kundeserviceregel r;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        PERFORM set_config('disponit.aktor', 'agent:stilleregel', true);
        tenanter := tenanter + 1;
        v_n := 0;
        FOR v_h IN
            SELECT h.henvendelse_id, r.handlingstype, r.prioritet, r.tema
              FROM public.henvendelse h
              JOIN public.kundeserviceregel r
                ON r.tenant = h.tenant
               AND ((r.art = 'domene'
                     AND h.avsender_maske IS NOT NULL
                     -- DOMENET ELLER ET UNDERDOMENE AV DET (205). En
                     -- regel for «microsoft.com» skal ta
                     -- «emailnotifications.microsoft.com» — det var
                     -- nøyaktig den som sto igjen uklassifisert.
                     -- Suffikset krever punktumet foran: «notmicrosoft.com»
                     -- er et annet domene.
                     AND (lower(split_part(h.avsender_maske, '@', 2))
                              = r.monster
                          OR lower(split_part(h.avsender_maske, '@', 2))
                              LIKE '%.' || r.monster))
                    OR (r.art = 'adresse'
                        AND h.avsender_hash = r.monster))
             WHERE h.tenant = v_t
               -- ÅPEN = ikke lukket, og ikke i unntakskøen: det et
               -- menneske har sendt til M-37 er alt under behandling,
               -- og en regel skal ikke dømme over hodet på den køen.
               AND h.lukket_ts IS NULL
               AND h.unntak_id IS NULL
               AND NOT EXISTS (SELECT 1 FROM public.klassifisering k
                                WHERE k.tenant = h.tenant
                                  AND k.henvendelse_id = h.henvendelse_id)
             ORDER BY h.mottatt, h.henvendelse_id
             LIMIT v_grense
        LOOP
            INSERT INTO public.klassifisering
                (tenant, henvendelse_id, prioritet, tema, handlingstype,
                 kilde, modell_digest, opprettet_av)
            VALUES (v_t, v_h.henvendelse_id, v_h.prioritet, v_h.tema,
                    v_h.handlingstype, 'regel', NULL, 'agent:stilleregel')
            ON CONFLICT (tenant, henvendelse_id) DO NOTHING;
            IF FOUND THEN
                v_n := v_n + 1;
                PERFORM public.m17_evidens(
                    v_t, v_h.henvendelse_id, 'henvendelse.klassifisert',
                    'agent:stilleregel',
                    jsonb_build_object('prioritet', v_h.prioritet,
                                       'tema', v_h.tema,
                                       'handlingstype', v_h.handlingstype,
                                       'kilde', 'regel'));
            END IF;
        END LOOP;
        klassifisert := klassifisert + v_n;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
    PERFORM set_config('disponit.aktor', '', true);
    RETURN NEXT;
END $$;

RESET ROLE;
