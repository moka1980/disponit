-- =====================================================================
-- 164 — M-17: KVITTERINGEN NÅR REGISTERET — SVARET ER SENDT
-- =====================================================================
--
-- ARC B kundeservice, PR 5 (150/157-formen). Eiermodulen (163) kvitterer
-- signert og ressursbundet til (henvendelse, utkast). Kvitteringen er
-- evidensen — men registeret må også VITE det: utkastet får statusen
-- `sendt`, og henvendelsen lukkes som «besvart» med kvitteringens aktør.
--
-- `sendt` ER BOKFØRINGENS ORD, ALDRI EN DOM: `m17_avgjor_utkast` (160)
-- nekter den fortsatt, og vakten slipper bare godkjent → sendt — veien
-- går gjennom `m17_svar_sendt`, og bare den. «Besvart» krever fra nå et
-- utkast merket `brukt_manuelt` (et menneske sendte selv) ELLER `sendt`
-- (plattformen sendte, med signert kvittering) — begge er spor noen kan
-- etterprøve; 102s dom står.
-- ---------------------------------------------------------------------

ALTER TABLE svarutkast DROP CONSTRAINT IF EXISTS svarutkast_status_check;
ALTER TABLE svarutkast ADD CONSTRAINT svarutkast_status_check
    CHECK (status IN ('foreslatt', 'forkastet', 'brukt_manuelt',
                      'godkjent', 'sendt'));

CREATE OR REPLACE FUNCTION m17_utkast_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'svarutkast: DELETE avvist — at et utkast fantes'
            ' er også historikk'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.utkast_id IS DISTINCT FROM OLD.utkast_id
       OR NEW.henvendelse_id IS DISTINCT FROM OLD.henvendelse_id
       OR NEW.tekst_kryptert IS DISTINCT FROM OLD.tekst_kryptert
       OR NEW.nonce IS DISTINCT FROM OLD.nonce
       OR NEW.key_id IS DISTINCT FROM OLD.key_id
       OR NEW.kunnskapsref IS DISTINCT FROM OLD.kunnskapsref
       OR NEW.kilde IS DISTINCT FROM OLD.kilde
       OR NEW.modell_digest IS DISTINCT FROM OLD.modell_digest
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'svarutkast: teksten er append-only — et utkast'
            ' som endres under føttene på den som leser det, er et'
            ' utkast ingen kan stå for å ha sendt. Regenerering er en NY'
            ' rad' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status
       AND NOT ((OLD.status = 'foreslatt'
                 AND NEW.status IN ('forkastet', 'brukt_manuelt', 'godkjent'))
                OR (OLD.status = 'godkjent' AND NEW.status = 'sendt')) THEN
        RAISE EXCEPTION 'svarutkast: status går bare fra foreslatt, og fra'
            ' godkjent til sendt (kvitteringens vei, 164) — et forkastet,'
            ' brukt eller sendt utkast er avgjort'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;


CREATE OR REPLACE FUNCTION m17_henvendelse_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'henvendelse: DELETE avvist — en tapt henvendelse'
            ' er verre enn en uklassifisert, og den er usynlig'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.henvendelse_id IS DISTINCT FROM OLD.henvendelse_id
       OR NEW.kanal IS DISTINCT FROM OLD.kanal
       OR NEW.ekstern_ref IS DISTINCT FROM OLD.ekstern_ref
       OR NEW.mottatt IS DISTINCT FROM OLD.mottatt
       OR NEW.avsender_hash IS DISTINCT FROM OLD.avsender_hash
       OR NEW.emne_kryptert IS DISTINCT FROM OLD.emne_kryptert
       OR NEW.kropp_kryptert IS DISTINCT FROM OLD.kropp_kryptert
       OR NEW.nonce_emne IS DISTINCT FROM OLD.nonce_emne
       OR NEW.nonce_kropp IS DISTINCT FROM OLD.nonce_kropp
       OR NEW.key_id IS DISTINCT FROM OLD.key_id
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'henvendelse: innholdet er append-only — det noen'
            ' skrev til oss endrer seg ikke fordi vi redigerer en rad'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- KØKOBLINGEN GÅR ÉN VEI. En henvendelse som kunne løsrives fra sin
    -- unntakssak ville gjort køen til et sted saker forsvinner fra.
    IF OLD.unntak_id IS NOT NULL
       AND NEW.unntak_id IS DISTINCT FROM OLD.unntak_id THEN
        RAISE EXCEPTION 'henvendelse: køkoblingen er satt og løsrives'
            ' ikke — M-37 eier saken derfra'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- …og lukkingen likeså.
    IF OLD.lukket_ts IS NOT NULL
       AND (NEW.lukket_ts IS DISTINCT FROM OLD.lukket_ts
            OR NEW.lukket_av IS DISTINCT FROM OLD.lukket_av
            OR NEW.lukket_utfall IS DISTINCT FROM OLD.lukket_utfall) THEN
        RAISE EXCEPTION 'henvendelse: en lukket henvendelse gjenåpnes'
            ' ikke — en ny sak er en ny henvendelse'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.lukket_ts IS NOT NULL AND OLD.lukket_ts IS NULL THEN
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL OR NEW.lukket_av IS DISTINCT FROM v_aktor THEN
            RAISE EXCEPTION 'henvendelse: lukket_av (%) er ikke aktøren'
                ' som lukker (%) — tiden besvarer ingen henvendelse',
                coalesce(NEW.lukket_av, '<null>'),
                coalesce(v_aktor, '<ingen>')
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- «BESVART» KREVER AT NOEN FAKTISK SKREV NOE. Et utkast merket
        -- `brukt_manuelt` er sporet etter at et menneske sendte et svar;
        -- uten det er «besvart» en påstand ingen kan etterprøve.
        -- `ikke_aktuell` har ikke kravet — den sier nettopp at det ikke
        -- skulle svares.
        IF NEW.lukket_utfall = 'besvart'
           AND NOT EXISTS (SELECT 1 FROM public.svarutkast u
                            WHERE u.tenant = NEW.tenant
                              AND u.henvendelse_id = NEW.henvendelse_id
                              AND u.status IN ('brukt_manuelt', 'sendt')) THEN
            RAISE EXCEPTION 'henvendelse: «besvart» krever et utkast'
                ' merket brukt_manuelt eller sendt — ellers er det en'
                ' påstand ingen kan etterprøve'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;


SET LOCAL ROLE disponit_kundeservice_eier;

CREATE OR REPLACE FUNCTION m17_lukk(
    p_tenant TEXT, p_henvendelse_id UUID, p_utfall TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_lukket TIMESTAMPTZ;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_lukk');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_utfall IS NULL OR p_utfall NOT IN ('besvart', 'ikke_aktuell') THEN
        RAISE EXCEPTION 'm17_lukk: utfallet må være besvart eller'
            ' ikke_aktuell' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT lukket_ts INTO v_lukket FROM public.henvendelse
     WHERE tenant = p_tenant AND henvendelse_id = p_henvendelse_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm17_lukk: henvendelsen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_lukket IS NOT NULL THEN
        RETURN false;                              -- stille ja
    END IF;
    IF p_utfall = 'besvart'
       AND NOT EXISTS (SELECT 1 FROM public.svarutkast u
                        WHERE u.tenant = p_tenant
                          AND u.henvendelse_id = p_henvendelse_id
                          AND u.status IN ('brukt_manuelt', 'sendt')) THEN
        RAISE EXCEPTION 'm17_lukk: «besvart» krever et utkast merket'
            ' brukt_manuelt eller sendt — uten det er det en påstand ingen'
            ' kan etterprøve' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.henvendelse
       SET lukket_ts = now(), lukket_av = p_aktor, lukket_utfall = p_utfall
     WHERE tenant = p_tenant AND henvendelse_id = p_henvendelse_id
       AND lukket_ts IS NULL;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;
    END IF;
    PERFORM public.m17_evidens(
        p_tenant, p_henvendelse_id, 'henvendelse.lukket', p_aktor,
        jsonb_build_object('utfall', p_utfall));
    RETURN true;
END $$;


CREATE FUNCTION m17_svar_sendt(
    p_tenant TEXT, p_henvendelse_id UUID, p_utkast_id UUID,
    p_oppdrag_id BIGINT, p_sendt_ts TIMESTAMPTZ, p_malversjon TEXT,
    p_mottaker_maske TEXT, p_aktor TEXT)
RETURNS TABLE(bokfort BOOLEAN, lukket BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_hid UUID; v_rader INT; v_lukket BOOLEAN := false;
        v_ts TIMESTAMPTZ := coalesce(p_sendt_ts, now());
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_svar_sendt');
    IF p_oppdrag_id IS NULL THEN
        RAISE EXCEPTION 'm17_svar_sendt: oppdraget må være satt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT u.status, u.henvendelse_id INTO v_status, v_hid
      FROM public.svarutkast u
     WHERE u.tenant = p_tenant AND u.utkast_id = p_utkast_id
       FOR UPDATE;
    IF v_hid IS NULL OR v_hid <> p_henvendelse_id THEN
        RAISE EXCEPTION 'm17_svar_sendt: utkastet er ikke henvendelsens'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_status = 'sendt' THEN
        bokfort := false; lukket := false;
        RETURN NEXT; RETURN;                       -- gjenspill: stille ja
    END IF;
    IF v_status <> 'godkjent' THEN
        RAISE EXCEPTION 'm17_svar_sendt: bare et godkjent utkast kan bli'
            ' sendt — dette er %', v_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.svarutkast SET status = 'sendt'
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id;
    -- Lukkingen skjer NÅ (basens klokke); sendetidspunktet er
    -- kvitteringens og står i evidensen.
    UPDATE public.henvendelse
       SET lukket_ts = now(), lukket_av = p_aktor, lukket_utfall = 'besvart'
     WHERE tenant = p_tenant AND henvendelse_id = p_henvendelse_id
       AND lukket_ts IS NULL;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    v_lukket := v_rader = 1;
    PERFORM public.m17_evidens(
        p_tenant, p_henvendelse_id, 'svar.sendt', p_aktor,
        jsonb_build_object('utkast_id', p_utkast_id::text,
                           'oppdrag_id', p_oppdrag_id, 'sendt_ts', v_ts,
                           'malversjon', p_malversjon,
                           'mottaker_maske', p_mottaker_maske,
                           'lukket', v_lukket));
    bokfort := true; lukket := v_lukket;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m17_svar_sendt(TEXT, UUID, UUID, BIGINT, TIMESTAMPTZ,
    TEXT, TEXT, TEXT) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_svar_sendt(TEXT, UUID, UUID,'
            ' BIGINT, TIMESTAMPTZ, TEXT, TEXT, TEXT) TO disponit';
    END IF;
END $$;
RESET ROLE;
