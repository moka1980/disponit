-- 167 — M-14 (ARC B bokføring, PR 4): kvitteringen når registeret —
-- fakturaen er BOKFØRT, bilaget står i M-13.
--
-- v1 (106) hadde ingen status som het `bokfort`, fordi det ikke fantes
-- noen hovedbok. Det finnes fortsatt ingen: det som finnes er husets
-- BILAGSREGISTER (101, M-13) — det avstemmingen matcher bankposter mot.
-- Å bokføre en inngående faktura i v1-koblingen ER å registrere den som
-- et `ut`-bilag der, med fakturaens tall, og merke fakturaen som
-- bokført med bilagets identitet. Så kan M-13 avstemme betalingen mot
-- nettopp det bilaget.
--
-- Veien inn er ÉN: `m14_faktura_bokfort`, kalt av kvitteringskroken
-- (API-ets tillit) når eiermodulens signerte `utfort`-kvittering kommer.
-- `m14_avgjor_faktura` (106) kjenner den ikke: «bokført» er aldri en
-- dom et menneske setter i skjemaet — det er det som skjedde.
--
-- BILAGET ER EN AVSKRIFT: døra nekter et bilag som avviker fra
-- fakturaens brutto, motpart eller utstedelsesdato. Modulen kunne
-- signere hva som helst; registeret bokfører bare sitt eget tall.

ALTER TABLE inngaaende_faktura
    DROP CONSTRAINT IF EXISTS faktura_status_lukket;
ALTER TABLE inngaaende_faktura
    ADD CONSTRAINT faktura_status_lukket
    CHECK (status IN ('mottatt', 'kontrollert', 'avvist', 'bokfort'));
ALTER TABLE inngaaende_faktura
    ADD COLUMN bilag_id UUID,
    ADD COLUMN bilagsnummer TEXT
        CHECK (bilagsnummer IS NULL OR bilagsnummer ~ '[^[:space:]]'),
    ADD COLUMN bokfort_ts TIMESTAMPTZ,
    ADD COLUMN bokfort_oppdrag_id BIGINT;
ALTER TABLE inngaaende_faktura
    ADD CONSTRAINT faktura_bokfort_helhet CHECK (
        (status = 'bokfort') = (bilag_id IS NOT NULL
                                AND bilagsnummer IS NOT NULL
                                AND bokfort_ts IS NOT NULL
                                AND bokfort_oppdrag_id IS NOT NULL));

-- Vakten (106) med to nye setninger: `kontrollert` OG `mottatt` kan bli
-- `bokfort` — og et bokført bilag er frosset som resten av raden.
CREATE OR REPLACE FUNCTION m14_faktura_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'inngaaende_faktura: TRUNCATE avvist — en tømt'
            ' fakturatabell gjør hver dublettsjekk verdiløs: den vet'
            ' ikke lenger hva vi har sett før'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'inngaaende_faktura: DELETE avvist — en faktura'
            ' avvises med begrunnelse. En slettet faktura er en dublett'
            ' vi ikke lenger kan oppdage'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.faktura_id IS DISTINCT FROM OLD.faktura_id
       OR NEW.leverandor_ref IS DISTINCT FROM OLD.leverandor_ref
       OR NEW.fakturanummer IS DISTINCT FROM OLD.fakturanummer
       OR NEW.netto_ore IS DISTINCT FROM OLD.netto_ore
       OR NEW.mva_ore IS DISTINCT FROM OLD.mva_ore
       OR NEW.brutto_ore IS DISTINCT FROM OLD.brutto_ore
       OR NEW.sats_kode IS DISTINCT FROM OLD.sats_kode
       OR NEW.valuta IS DISTINCT FROM OLD.valuta
       OR NEW.utstedt IS DISTINCT FROM OLD.utstedt
       OR NEW.forfall IS DISTINCT FROM OLD.forfall
       OR NEW.mottatt IS DISTINCT FROM OLD.mottatt
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'inngaaende_faktura: fakturaens innhold er'
            ' frosset — det er det leverandøren krever, ikke vårt tall'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status = 'bokfort'
       AND (NEW.bilag_id IS DISTINCT FROM OLD.bilag_id
            OR NEW.bilagsnummer IS DISTINCT FROM OLD.bilagsnummer
            OR NEW.bokfort_ts IS DISTINCT FROM OLD.bokfort_ts
            OR NEW.bokfort_oppdrag_id IS DISTINCT FROM OLD.bokfort_oppdrag_id)
    THEN
        RAISE EXCEPTION 'inngaaende_faktura: bilaget er bokført og frosset'
            ' — en rettelse er et motbilag, ikke en redigering'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status <> 'mottatt' AND NEW.status <> OLD.status
       AND NOT (OLD.status = 'kontrollert' AND NEW.status = 'bokfort') THEN
        RAISE EXCEPTION 'inngaaende_faktura: fakturaen er alt % — en'
            ' avgjørelse gjøres om ved en NY kontroll, ikke ved å bytte'
            ' status tilbake', OLD.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status <> 'mottatt' AND OLD.status = 'mottatt' THEN
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL OR NEW.avgjort_av IS DISTINCT FROM v_aktor THEN
            RAISE EXCEPTION 'inngaaende_faktura: avgjort_av (%) er ikke'
                ' aktøren som avgjør (%)',
                coalesce(NEW.avgjort_av, '<null>'),
                coalesce(v_aktor, '<ingen>')
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;

-- Fakturaregisterets eier må få kalle bilagsregisterets dør — det er
-- den ENE veien et bilag fødes av en faktura. Grantet gis av dørens
-- eier (101 skapte den under disponit_avstemming_eier), som 106 gjorde
-- for leverandørregisterets lesing.
SET LOCAL ROLE disponit_avstemming_eier;
GRANT EXECUTE ON FUNCTION m13_registrer_bilag(TEXT, UUID, TEXT, TEXT, BIGINT,
                                              TEXT, DATE, DATE, TEXT)
    TO disponit_faktura_eier;
RESET ROLE;

SET LOCAL ROLE disponit_faktura_eier;

CREATE FUNCTION m14_faktura_bokfort(
    p_tenant TEXT, p_faktura_id UUID, p_oppdrag_id BIGINT,
    p_bilagsnummer TEXT, p_retning TEXT, p_belop_ore BIGINT,
    p_motpart TEXT, p_utstedt DATE, p_forfall DATE,
    p_bokfort_ts TIMESTAMPTZ, p_malversjon TEXT, p_aktor TEXT)
RETURNS TABLE(bokfort BOOLEAN, bilag_registrert BOOLEAN, bilag_id UUID)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_f RECORD; v_bilag UUID; v_reg BOOLEAN;
        v_ts TIMESTAMPTZ := coalesce(p_bokfort_ts, now());
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm14_faktura_bokfort');
    IF p_oppdrag_id IS NULL THEN
        RAISE EXCEPTION 'm14_faktura_bokfort: oppdraget må være satt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT f.status, f.brutto_ore, f.leverandor_ref, f.fakturanummer,
           f.utstedt, f.forfall, f.bilag_id AS gammelt_bilag
      INTO v_f
      FROM public.inngaaende_faktura f
     WHERE f.tenant = p_tenant AND f.faktura_id = p_faktura_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm14_faktura_bokfort: fakturaen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_f.status = 'bokfort' THEN
        bokfort := false; bilag_registrert := false;
        bilag_id := v_f.gammelt_bilag;
        RETURN NEXT; RETURN;                       -- gjenspill: stille ja
    END IF;
    IF v_f.status = 'avvist' THEN
        RAISE EXCEPTION 'm14_faktura_bokfort: en avvist faktura bokføres'
            ' ikke' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_retning IS DISTINCT FROM 'ut'
       OR p_belop_ore IS DISTINCT FROM v_f.brutto_ore
       OR btrim(coalesce(p_motpart, '')) IS DISTINCT FROM v_f.leverandor_ref
       OR p_utstedt IS DISTINCT FROM v_f.utstedt
       OR p_forfall IS DISTINCT FROM v_f.forfall THEN
        RAISE EXCEPTION 'm14_faktura_bokfort: bilaget avviker fra fakturaen'
            ' (retning/beløp/motpart/datoer) — registeret bokfører bare'
            ' sitt eget tall' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Bilagets identitet er fakturaens: samme faktura gir samme bilag_id,
    -- så et gjenspill av kvitteringen aldri kan føde et bilag nummer to.
    v_bilag := md5('m14|bilag|' || p_tenant || '|' || p_faktura_id::text)::uuid;
    v_reg := public.m13_registrer_bilag(
        p_tenant, v_bilag, p_bilagsnummer, 'ut', p_belop_ore,
        v_f.leverandor_ref, p_utstedt, p_forfall, p_aktor);
    UPDATE public.inngaaende_faktura
       SET status = 'bokfort',
           bilag_id = v_bilag,
           bilagsnummer = btrim(p_bilagsnummer),
           bokfort_ts = v_ts,
           bokfort_oppdrag_id = p_oppdrag_id,
           avgjort_ts = coalesce(avgjort_ts, now()),
           avgjort_av = coalesce(avgjort_av, p_aktor),
           avgjort_begrunnelse = coalesce(
               avgjort_begrunnelse,
               'bokført som bilag ' || btrim(p_bilagsnummer)
               || ' (oppdrag ' || p_oppdrag_id::text || ')')
     WHERE tenant = p_tenant AND faktura_id = p_faktura_id;
    PERFORM public.m14_evidens(
        p_tenant, p_faktura_id, 'faktura.bokfort', p_aktor,
        jsonb_build_object('oppdrag_id', p_oppdrag_id,
                           'bilag_id', v_bilag::text,
                           'bilagsnummer', btrim(p_bilagsnummer),
                           'belop_ore', p_belop_ore,
                           'bokfort_ts', v_ts, 'malversjon', p_malversjon,
                           'bilag_registrert', v_reg));
    bokfort := true; bilag_registrert := v_reg; bilag_id := v_bilag;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m14_faktura_bokfort(TEXT, UUID, BIGINT, TEXT, TEXT,
    BIGINT, TEXT, DATE, DATE, TIMESTAMPTZ, TEXT, TEXT) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m14_faktura_bokfort(TEXT, UUID,'
            ' BIGINT, TEXT, TEXT, BIGINT, TEXT, DATE, DATE, TIMESTAMPTZ,'
            ' TEXT, TEXT) TO disponit';
    END IF;
END $$;

RESET ROLE;
