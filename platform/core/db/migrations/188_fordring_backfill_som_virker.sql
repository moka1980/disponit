-- 188 — Backfillen i 187 var en STILLE NULLOPERASJON.
--
-- MÅLT I PROD ETTER DEPLOY: seks fordringer, null koblet, null parter
-- laget. Migrasjonen gikk grønt og gjorde ingenting.
--
-- ÅRSAKEN: løkka begynte med `SELECT DISTINCT tenant FROM fordring`, og
-- den kjørte som MIGRATOR med TOM tenantkontekst. `fordring` har FORCE
-- RLS, og `tenant_isolasjon` måler mot `disponit.tenant` — tom kontekst
-- betyr INGEN rader, ikke alle. Løkka fikk null tenanter og gikk
-- hjem. Fail-closed, som det skal være — men stille, og en backfill som
-- ikke fant noe ser nøyaktig ut som en som ikke hadde noe å finne.
--
-- (Den lærdommen står alt i huset, om backup-kopiering. Den gjelder her
-- også, og jeg gikk rett i den.)
--
-- LØSNINGEN ER HUSETS EGEN: `fordring` har `m23_sveip_tenantliste`, en
-- policy som åpner tabellen for `disponit_fordring_eier` NÅR KONTEKSTEN
-- ER TOM — laget nettopp for kryss-tenant-jobber. Lesingen skjer som den
-- rollen; skrivingen skjer per tenant med konteksten satt, fordi `part`
-- ikke har noen slik policy og ikke skal ha det.
--
-- PORTEN SOM IKKE FANTES: 187s test het «backfillen slår aldri sammen»,
-- men den målte registreringsdøra — ikke backfillen. Navnet lovet mer
-- enn testen gjorde. 188 har en port som kjører backfillens EGEN logikk
-- mot rader som alt finnes.

-- 1. LESINGEN: hvilke (tenant, kunde_ref) mangler en part?
CREATE TEMP TABLE _backfill_par ON COMMIT DROP AS
SELECT DISTINCT tenant, kunde_ref FROM public.fordring WHERE false;
-- Temp-tabellen er MIGRATORS; lesingen under skjer som fordringseieren,
-- og uten dette grantet er det «permission denied for table
-- _backfill_par» — ikke på den følsomme tabellen, men på arbeidsbenken.
-- Tabellen dør ved commit (`ON COMMIT DROP`), så grantet dør med den.
GRANT INSERT ON _backfill_par TO disponit_fordring_eier;

DO $$
BEGIN
    PERFORM set_config('disponit.tenant', '', true);
    SET LOCAL ROLE disponit_fordring_eier;
    INSERT INTO _backfill_par
    SELECT DISTINCT f.tenant, f.kunde_ref
      FROM public.fordring f
     WHERE f.part_id IS NULL;
    RESET ROLE;
    RAISE NOTICE 'backfill 188: % par å vurdere',
                 (SELECT count(*) FROM _backfill_par);
END $$;

-- 2. SKRIVINGEN: per tenant, med konteksten satt. Én part per referanse
--    — ALDRI en sammenslåing, av grunnen 187 skrev ned: referansene i
--    drift identifiserer ikke samme kunde på tvers, og en gjetning ville
--    slått sammen to virkelige kunder.
DO $$
DECLARE t TEXT; r RECORD; v_id UUID; v_rader INT;
        v_ny INT := 0; v_koblet INT := 0;
BEGIN
    FOR t IN SELECT DISTINCT tenant FROM _backfill_par LOOP
        PERFORM set_config('disponit.tenant', t, true);
        FOR r IN SELECT kunde_ref FROM _backfill_par WHERE tenant = t LOOP
            SELECT part_id INTO v_id FROM public.part
             WHERE tenant = t AND part_ref = r.kunde_ref;
            IF v_id IS NULL THEN
                v_id := public.gen_random_uuid();
                INSERT INTO public.part
                    (tenant, part_id, part_ref, navn, opprettet_av)
                VALUES (t, v_id, r.kunde_ref, r.kunde_ref, 'migrasjon-188');
                v_ny := v_ny + 1;
            END IF;
            UPDATE public.fordring SET part_id = v_id
             WHERE tenant = t AND kunde_ref = r.kunde_ref AND part_id IS NULL;
            GET DIAGNOSTICS v_rader = ROW_COUNT;
            v_koblet := v_koblet + v_rader;
        END LOOP;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
    RAISE NOTICE 'backfill 188: % nye parter, % fordringer koblet',
                 v_ny, v_koblet;
END $$;
