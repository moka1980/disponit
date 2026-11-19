-- 213 — M-5: HVER MALENDRING BLIR EN HENDELSE I EVIDENSKJEDEN.
--
-- ADR-002 D1 (19.11.2026): en dør som endrer tenanttilstand skriver én
-- hendelse i `revisjonslogg` med aktør og egen `input_hash`. M-5 er
-- klasse C (tenantregister uten sveip) og har fem slike dører over HTTP
-- (opprett familie, opprett versjon, publiser, trekk tilbake, forkast).
-- Ingen av dem skrev dit: at registeret er append-only og fryser en
-- publisert versjon, viser HVA som står i det — ikke HVEM som satte det
-- dit.
--
-- EVIDENSEN ER TRIGGERE, IKKE KALLSTEDER (211/212-formen). Hver dør ender
-- i en INSERT eller en statusovergang på `malfamilie`/`malversjon`, og
-- den neste døren vil også gjøre det. Et gjenspill er et stille ja som
-- returnerer FØR skrivingen — og skriver derfor ingen hendelse.
--
-- AKTØREN ER RADENS EGEN: `opprettet_av` ved fødselen, og overgangens
-- `publisert_av` / `tilbaketrukket_av` / `forkastet_av` ved en
-- statusendring. CHECKene på tabellen nekter tomme aktører, og
-- `malversjon_status_total` garanterer at riktig kolonne er satt.
--
-- PUBLISERINGEN PINNER INNHOLDET. Hendelsen `malversjon.publisert` bærer
-- en sha256 over komponentene og feltene i rekkefølge — nøyaktig det
-- som ble frosset. Når versjonen fødes finnes komponentene ennå ikke
-- (døra setter dem inn etter versjonsraden), men ved publiseringen er
-- de der og kan aldri endres igjen (`m5_innhold_vakt`).
--
-- TO EIERE, TO VINDUER (027-fellen, ordrett fra 211/212): tabellene er
-- MIGRATORENS (094 lager dem før sitt `SET LOCAL ROLE`), så triggerne
-- lages av migratoren — men FUNKSJONENE eies av registerets eier, som
-- får INSERT på `revisjonslogg` her (097-formen).

GRANT INSERT ON revisjonslogg TO disponit_mal_eier;

SET LOCAL ROLE disponit_mal_eier;

CREATE OR REPLACE FUNCTION m5_evidens_skriv(
    p_tenant TEXT, p_aktor TEXT, p_handling TEXT, p_ressurs UUID,
    p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm05_dokumentmal', 'handling', p_handling,
        'ressurs_id', p_ressurs::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm05_dokumentmal',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:malregister', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m5_evidens_skriv(TEXT, TEXT, TEXT, UUID, JSONB)
    FROM PUBLIC;

CREATE OR REPLACE FUNCTION m5_familie_evidens()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.m5_evidens_skriv(
        NEW.tenant, NEW.opprettet_av, 'malfamilie.opprettet',
        NEW.familie_id, '{}'::jsonb);
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION m5_versjon_evidens()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT; v_innhold TEXT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM public.m5_evidens_skriv(
            NEW.tenant, NEW.opprettet_av, 'malversjon.opprettet',
            NEW.versjon_id,
            jsonb_build_object('familie_id', NEW.familie_id::text,
                               'versjonsnr', NEW.versjonsnr));
        RETURN NEW;
    END IF;
    -- UPDATE: bare en ekte statusovergang er en hendelse.
    IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
        RETURN NEW;
    END IF;
    v_aktor := CASE NEW.status
                   WHEN 'publisert' THEN NEW.publisert_av
                   WHEN 'tilbaketrukket' THEN NEW.tilbaketrukket_av
                   WHEN 'forkastet' THEN NEW.forkastet_av END;
    IF NEW.status = 'publisert' THEN
        SELECT encode(sha256(convert_to(coalesce(string_agg(
                   jsonb_build_object(
                       'r', k.rekkefolge, 't', k.komponenttype,
                       'i', k.innhold, 'f', k.feltnokkel,
                       'l', k.laast)::text, E'\n'
                   ORDER BY k.rekkefolge), ''), 'UTF8')), 'hex')
          INTO v_innhold
          FROM public.malkomponent k
         WHERE k.tenant = NEW.tenant AND k.versjon_id = NEW.versjon_id;
    END IF;
    PERFORM public.m5_evidens_skriv(
        NEW.tenant, v_aktor, 'malversjon.' || NEW.status, NEW.versjon_id,
        jsonb_build_object('fra', OLD.status,
                           'versjonsnr', NEW.versjonsnr,
                           'innhold_sha256', v_innhold));
    RETURN NEW;
END $$;

RESET ROLE;

DROP TRIGGER IF EXISTS malfamilie_evidens ON malfamilie;
CREATE TRIGGER malfamilie_evidens
    AFTER INSERT ON malfamilie
    FOR EACH ROW EXECUTE FUNCTION m5_familie_evidens();

DROP TRIGGER IF EXISTS malversjon_evidens ON malversjon;
CREATE TRIGGER malversjon_evidens
    AFTER INSERT OR UPDATE OF status ON malversjon
    FOR EACH ROW EXECUTE FUNCTION m5_versjon_evidens();
