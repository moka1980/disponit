-- 212 — M-9: HVER ORDLISTEENDRING BLIR EN HENDELSE I EVIDENSKJEDEN.
--
-- ADR-002 D1 (19.11.2026): en dør som endrer tenanttilstand skriver én
-- hendelse i `revisjonslogg` med aktør og egen `input_hash`. Ingen
-- fritak: at `begrep` er append-only (gamle versjoner består) viser
-- HVA som står i ordlisten, ikke HVEM som satte det dit — og det er
-- det revisjonsloggen finnes for. M-9 var det eneste registeret i
-- klasse A som ikke skrev dit.
--
-- EVIDENSEN ER EN TRIGGER, IKKE ET KALLSTED (211-formen). Begge dørene
-- (`m9_registrer_begrep`, `m9_ny_begrepsversjon`) ender i en INSERT på
-- `begrep`, og den neste døren som kommer vil også gjøre det. Et kall
-- kan glemmes i den døren; en trigger kan ikke. Et gjenspill er et
-- stille ja som returnerer FØR innsettingen — og skriver derfor ingen
-- hendelse, slik det skal være: to like hendelser i evidenskjeden ser
-- ut som to registreringer som aldri skjedde.
--
-- AKTØREN ER RADENS `opprettet_av` — døra setter den fra `p_aktor`, og
-- CHECKen på kolonnen nekter en tom aktør. `input_hash` er kanonisk
-- JSON over modul, handling, begrep_id og versjonsnr, så to tenanter
-- med samme term aldri deler hash.
--
-- TO EIERE, TO VINDUER (027-fellen, ordrett fra 211): `begrep` er
-- MIGRATORENS (095 lager den før sitt `SET LOCAL ROLE`), så triggeren
-- må lages av migratoren — men FUNKSJONEN eies av registerets eier,
-- som får INSERT på `revisjonslogg` her (097-formen for M-12).

GRANT INSERT ON revisjonslogg TO disponit_kunnskap_eier;

SET LOCAL ROLE disponit_kunnskap_eier;

CREATE OR REPLACE FUNCTION m9_begrep_evidens()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_handling TEXT; v_kanon TEXT;
BEGIN
    v_handling := CASE WHEN NEW.versjonsnr = 1 THEN 'begrep.registrert'
                       ELSE 'begrep.ny_versjon' END;
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm09_kunnskap', 'handling', v_handling,
        'begrep_id', NEW.begrep_id::text,
        'versjonsnr', NEW.versjonsnr)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (NEW.tenant, NEW.opprettet_av, 'm09_kunnskap',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:begrepsregister', 'TILLAT',
            jsonb_build_array(v_handling), v_handling);
    RETURN NEW;
END $$;

RESET ROLE;

DROP TRIGGER IF EXISTS begrep_evidens ON begrep;
CREATE TRIGGER begrep_evidens
    AFTER INSERT ON begrep
    FOR EACH ROW EXECUTE FUNCTION m9_begrep_evidens();
