-- =====================================================================
-- 150 — M-23: PURRINGEN ER SENDT — REGISTERET BOKFØRER OG FLYTTER TRINNET
-- =====================================================================
--
-- ARC B, PR 5. Til nå har kjeden vært: funn → bestilling → oppdrag →
-- sending → signert kvittering. Det som manglet var det siste leddet
-- tilbake til REGISTERET: at fordringen faktisk står på trinnet som ble
-- purret, og at hendelsen står i fordringens egen historikk — ikke bare
-- i oppdragets kvittering.
--
-- Døra kalles av API-ets kvitteringsvei (runtime) når en `purring.send`-
-- kvittering er `utfort`. Den er IDEMPOTENT på oppdraget (hendelse_id
-- utledes av oppdrag_id), og den er ÆRLIG om rekkefølgen: er trinnet i
-- kvitteringen ikke fordringens neste, ble det likevel sendt — hendelsen
-- føres, evidensen sier «utenfor rekkefølge», og trinnet står. Å nekte
-- kvitteringen ville skjult en e-post som alt er ute.
-- ---------------------------------------------------------------------

-- Hendelsesarten `purring`: trinnet som ble purret, ingen beløp.
ALTER TABLE fordringshendelse DROP CONSTRAINT IF EXISTS
    fordringshendelse_art_check;
ALTER TABLE fordringshendelse ADD CONSTRAINT fordringshendelse_art_check
    CHECK (art IN ('betaling', 'trinn', 'ettergitt', 'purring'));
ALTER TABLE fordringshendelse DROP CONSTRAINT IF EXISTS
    fordringshendelse_felt_per_art;
ALTER TABLE fordringshendelse ADD CONSTRAINT fordringshendelse_felt_per_art
    CHECK (
        (art = 'betaling' AND belop_ore IS NOT NULL AND trinn IS NULL)
        OR (art = 'trinn' AND belop_ore IS NULL AND trinn IS NOT NULL)
        OR (art = 'purring' AND belop_ore IS NULL AND trinn IS NOT NULL)
        OR (art = 'ettergitt' AND belop_ore IS NULL AND trinn IS NULL
            AND begrunnelse IS NOT NULL AND begrunnelse ~ '[^[:space:]]'));

SET LOCAL ROLE disponit_fordring_eier;

CREATE FUNCTION m23_purring_sendt(
    p_tenant TEXT, p_fordring_id UUID, p_trinn INT, p_oppdrag_id BIGINT,
    p_sendt_ts TIMESTAMPTZ, p_malversjon TEXT, p_mottaker_maske TEXT,
    p_aktor TEXT)
RETURNS TABLE(bokfort BOOLEAN, flyttet BOOLEAN, trinn_naa INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_f RECORD; v_hid UUID; v_rader INT; v_versjon INT;
        v_finnes BOOLEAN; v_flyttet BOOLEAN := false;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_purring_sendt');
    IF p_trinn IS NULL OR p_trinn < 1 OR p_oppdrag_id IS NULL THEN
        RAISE EXCEPTION 'm23_purring_sendt: trinn og oppdrag må være satt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT * INTO v_f FROM public.fordring f
     WHERE f.tenant = p_tenant AND f.fordring_id = p_fordring_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm23_purring_sendt: fordringen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    -- Hendelses-id-en er OPPDRAGETS: samme kvittering to ganger er én
    -- hendelse (deterministisk UUID over tenant + oppdrag_id; md5 er
    -- kjerne, uuid-ossp er det ikke).
    v_hid := md5('disponit:purring:' || p_tenant || ':'
                 || p_oppdrag_id::text)::uuid;
    INSERT INTO public.fordringshendelse
        (tenant, hendelse_id, fordring_id, art, trinn, begrunnelse,
         inntruffet, opprettet_av)
    VALUES (p_tenant, v_hid, p_fordring_id, 'purring', p_trinn,
            'oppdrag ' || p_oppdrag_id::text || ' (' ||
            coalesce(p_malversjon, '?') || ')',
            coalesce(p_sendt_ts, now())::date, p_aktor)
        ON CONFLICT (tenant, hendelse_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        bokfort := false; flyttet := false; trinn_naa := v_f.trinn;
        RETURN NEXT; RETURN;                       -- gjenspill: stille ja
    END IF;
    -- Trinnet flyttes BARE når det er fordringens neste og planen har
    -- det. Ellers står trinnet, og evidensen sier hvorfor.
    SELECT true INTO v_finnes FROM public.purretrinn t
     WHERE t.tenant = p_tenant AND t.trinn_nr = p_trinn;
    IF v_f.status = 'apen' AND p_trinn = v_f.trinn + 1
       AND v_finnes IS TRUE THEN
        SELECT p.versjon INTO v_versjon FROM public.purreplan p
         WHERE p.tenant = p_tenant;
        UPDATE public.fordring
           SET trinn = p_trinn, purreplan_versjon = v_versjon
         WHERE tenant = p_tenant AND fordring_id = p_fordring_id;
        v_flyttet := true;
    END IF;
    PERFORM public.m23_evidens(
        p_tenant, p_fordring_id,
        CASE WHEN v_flyttet THEN 'purring.sendt'
             ELSE 'purring.sendt_utenfor_rekkefolge' END,
        p_aktor,
        jsonb_build_object('trinn', p_trinn, 'oppdrag_id', p_oppdrag_id,
                           'sendt_ts', p_sendt_ts, 'malversjon', p_malversjon,
                           'mottaker_maske', p_mottaker_maske,
                           'fordring_trinn_for', v_f.trinn,
                           'status', v_f.status));
    bokfort := true; flyttet := v_flyttet;
    trinn_naa := CASE WHEN v_flyttet THEN p_trinn ELSE v_f.trinn END;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m23_purring_sendt(TEXT, UUID, INT, BIGINT, TIMESTAMPTZ,
    TEXT, TEXT, TEXT) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_purring_sendt(TEXT, UUID, INT,'
            ' BIGINT, TIMESTAMPTZ, TEXT, TEXT, TEXT) TO disponit';
    END IF;
END $$;
RESET ROLE;
