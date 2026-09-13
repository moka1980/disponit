-- 199: PLATTFORMEIEREN KAN SE OG ADMINISTRERE ALLE FIRMAER.
--
-- MÅLT FØR DETTE: ingen kan det. `firma` har FORCE RLS med
-- `tenant_isolasjon`, og HVER dør i 190 kaller `krev_tenantkontekst`. Et
-- firma kan rette sitt eget navn og ikke noe mer; eieren av plattformen har
-- ingen vei inn i det hele tatt uten å gå direkte på basen som migrator.
--
-- Eiers krav, ordrett: «Jeg er allerede admin/root/eier som eliassi@gmail.com
-- og skal også ha mulighet til å opprette nye firmaer, redigere og slette.»
--
-- TRE LAG, OG DE GJØR HVER SIN JOBB
-- ---------------------------------
--  1. ROLLEN `disponit_plattform_eier` eier dørene. Den gir dem SYN —
--     policyen under lar den se alle rader i `firma`. Ikke BYPASSRLS: målt
--     at en policy med TO-klausul holder, og den er strengt snevrere (et
--     BYPASSRLS opphever RLS på HVER tabell i basen).
--  2. TABELLEN `plattformeier` er FULLMAKTEN. Hver dør slår opp kalleren
--     der FØRST, og nekter ellers. Rollen alene autoriserer ingenting.
--  3. DØRENE i 190 gjør fortsatt arbeidet. `plattform_firma_*` setter
--     tenantkonteksten og KALLER dem — den lovlige-overganger-tabellen,
--     angrefristen og `ON CONFLICT DO NOTHING`-dommen skrives ikke om her.
--     En kopi av en statusmaskin er en statusmaskin som kommer til å drifte
--     fra originalen.
--
-- TABELLEN STARTER TOM, OG DET ER MENINGEN
-- Systemet kommer med NULL plattformeiere. Ingen er eier før noen skrives
-- inn på verten. Et fabrikkoppsett med en innebygget superbruker er en
-- bakdør uansett hvor godt den er ment.
--
-- HVORFOR IKKE RLS PÅ `plattformeier`
-- Huset har ingen policyløs RLS-tabell, og jeg fant ingen presedens for å
-- lage den første her. Naboene som gjør samme jobb — `oidc_provider`,
-- `brukeridentitet`, `bruker_tenant` — er alle kryss-tenant og vernes av
-- GRANTS alene (målt). Denne speiler dem: runtime får INGENTING på
-- tabellen, bare EXECUTE på dørene.
-- ============================================================

CREATE TABLE IF NOT EXISTS plattformeier (
    -- `brukeridentitet.bruker_id`. Ingen FK med vilje: en eier skal kunne
    -- skrives inn FØR hun har logget inn første gang, ellers er oppstarten
    -- en høne-og-egg-situasjon på verten.
    bruker_id    TEXT PRIMARY KEY CHECK (bruker_id ~ '[^[:space:]]'),
    notat        TEXT,
    opprettet    TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]')
);

REVOKE ALL ON TABLE plattformeier FROM PUBLIC;
GRANT SELECT ON TABLE plattformeier TO disponit_plattform_eier;

-- SYNET. Uten denne ser dørene null rader, fordi FORCE RLS gjelder alle som
-- ikke er unntatt — også en SECURITY DEFINER-dør. Formen er `firma_sveiper`
-- (190), som gir `disponit_m37_claimer` samme kryss-tenant-syn for
-- prøveutløpssveipen.
CREATE POLICY plattform_eier_ser_alle ON firma
    TO disponit_plattform_eier
    USING (true) WITH CHECK (true);
GRANT SELECT ON TABLE firma TO disponit_plattform_eier;

-- Dørene i 190 er SECURITY DEFINER eid av migrator; rollen må kunne KALLE
-- dem for å gjenbruke dem.
GRANT EXECUTE ON FUNCTION firma_registrer(TEXT, TEXT, TEXT, INT, TEXT)
    TO disponit_plattform_eier;
GRANT EXECUTE ON FUNCTION firma_oppdater(TEXT, TEXT, TEXT, TEXT)
    TO disponit_plattform_eier;
GRANT EXECUTE ON FUNCTION firma_sett_status(TEXT, TEXT, TEXT)
    TO disponit_plattform_eier;

-- ============================================================
-- DØRENE. Eid av `disponit_plattform_eier`.
--
-- REVOKE OG GRANT SKJER MENS VI FORTSATT ER EIEREN — 184s og 190s
-- rekkefølge. Den implisitte `EXECUTE TO PUBLIC` en ny funksjon får, er gitt
-- av eieren; trekker migrator den tilbake etter `RESET ROLE`, svarer
-- Postgres «no privileges could be revoked» (en WARNING, ikke en feil) og
-- funksjonen står igjen med `=X` i ACL-en. Det ble målt i 190, og porten
-- under måler ACL-en direkte her.
-- ============================================================
SET LOCAL ROLE disponit_plattform_eier;

-- FULLMAKTSSJEKKEN, ÉN GANG, brukt av alle de andre.
--
-- DEN SVARER BARE OM KALLEREN SELV (CodeRabbit, major). Første form tok en
-- vilkårlig id og sa ja eller nei — altså et orakel runtime kunne ramse opp
-- plattformeierne med, én gjetning om gangen. Det er nøyaktig det
-- `plattform_krev_eier`s ÉNE feilkode finnes for å hindre, og da kan ikke
-- denne døra stå åpen ved siden av.
--
-- FALSE, ikke exception: spørsmålet flaten stiller er «er JEG eier?», og
-- svaret på det er nei — ikke en feil.
CREATE OR REPLACE FUNCTION plattform_er_eier(p_bruker_id TEXT)
RETURNS BOOLEAN LANGUAGE sql SECURITY DEFINER STABLE
SET search_path = pg_catalog AS $$
    SELECT nullif(current_setting('disponit.aktor', true), '')
           IS NOT DISTINCT FROM p_bruker_id
       AND EXISTS (SELECT 1 FROM public.plattformeier
                    WHERE bruker_id = p_bruker_id)
$$;

CREATE OR REPLACE FUNCTION plattform_krev_eier(p_bruker_id TEXT, p_dor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    -- PARAMETERET ALENE ER INGEN IDENTITET (CodeRabbit, critical).
    -- Første form slo bare opp `p_bruker_id` i tabellen. Da var enhver
    -- kaller som kunne nå døra, plattformeier — hun trengte bare å sende
    -- EN EKTE eiers bruker-id. Fullmakten lå i det kalleren skrev, ikke i
    -- hvem hun var.
    --
    -- Dette er 038s form, speilet: `krev_tenantkontekst` binder `p_tenant`
    -- til `current_setting('disponit.tenant')` nettopp fordi «definer-veiene
    -- binder tenanten til KONTEKSTEN, aldri til parameteret alene». Her er
    -- aktøren det samme.
    --
    -- FORMEN ER DEN RÅ BRUKER-ID-EN, ikke `bruker:<id>`. Jeg skrev prefikset
    -- først, fordi `invitasjon.py` bruker den formen i sitt eget kall — men
    -- `_browserkontekst` (policyadmin_http) gjør `bid = token_id.split(
    -- 'sesjon:', 1)[-1]` og sender NØYAKTIG den strengen til `sett_kontekst`.
    -- Med prefikset ville døra avvist hvert eneste ekte kall, mens porten min
    -- sto grønn fordi den satte konteksten slik jeg TRODDE API-et gjorde.
    -- Porten kaller nå `db.pg.sett_kontekst` selv, så formen kan ikke skli
    -- fra hverandre igjen.
    IF p_bruker_id IS NULL OR btrim(p_bruker_id) = ''
       OR nullif(current_setting('disponit.aktor', true), '')
          IS DISTINCT FROM p_bruker_id THEN
        RAISE EXCEPTION '%: p_bruker_id er ikke kallerens aktørkontekst', p_dor
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT public.plattform_er_eier(p_bruker_id) THEN
        -- ÉN KODE FOR BÅDE «ukjent» OG «ikke eier». Å skille dem ville latt
        -- noen kartlegge hvem som er plattformeier ved å prøve seg fram.
        RAISE EXCEPTION '%: ikke plattformeier', p_dor
            USING ERRCODE = 'insufficient_privilege';
    END IF;
END $$;

-- LESNINGEN: alle firmaer, på tvers av tenanter.
CREATE OR REPLACE FUNCTION plattform_firmaliste(p_bruker_id TEXT)
RETURNS TABLE (tenant TEXT, navn TEXT, orgnummer TEXT, status TEXT,
               prove_utloper DATE, stengt_ts TIMESTAMPTZ,
               opprettet TIMESTAMPTZ, endret TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.plattform_krev_eier(p_bruker_id, 'plattform_firmaliste');
    RETURN QUERY
        SELECT f.tenant, f.navn, f.orgnummer, f.status, f.prove_utloper,
               f.stengt_ts, f.opprettet, f.endret
          FROM public.firma f
         ORDER BY f.tenant;
END $$;

-- SKRIVINGENE. Hver setter konteksten og KALLER 190s dør — logikken der
-- skrives ikke om.
CREATE OR REPLACE FUNCTION plattform_firma_opprett(
        p_bruker_id TEXT, p_tenant TEXT, p_navn TEXT,
        p_orgnummer TEXT, p_prove_dogn INT, p_aktor TEXT)
RETURNS DATE LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_frist DATE; v_for TEXT;
BEGIN
    PERFORM public.plattform_krev_eier(p_bruker_id,
                                       'plattform_firma_opprett');
    -- KONTEKSTEN LEGGES TILBAKE (CodeRabbit, major). `set_config(...,true)`
    -- er transaksjonslokal, ikke kall-lokal: uten dette sto kalleren igjen
    -- i MÅLFIRMAETS tenant resten av transaksjonen, og neste spørring i
    -- samme transaksjon leste feil tenants rader. Den settes bare fordi
    -- 190s dør krever den.
    v_for := current_setting('disponit.tenant', true);
    PERFORM set_config('disponit.tenant', p_tenant, true);
    BEGIN
        v_frist := public.firma_registrer(p_tenant, p_navn, p_orgnummer,
                                          p_prove_dogn, p_aktor);
    EXCEPTION WHEN OTHERS THEN
        PERFORM set_config('disponit.tenant', coalesce(v_for, ''), true);
        RAISE;
    END;
    PERFORM set_config('disponit.tenant', coalesce(v_for, ''), true);
    RETURN v_frist;
END $$;

CREATE OR REPLACE FUNCTION plattform_firma_oppdater(
        p_bruker_id TEXT, p_tenant TEXT, p_navn TEXT,
        p_orgnummer TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_for TEXT;
BEGIN
    PERFORM public.plattform_krev_eier(p_bruker_id,
                                       'plattform_firma_oppdater');
    v_for := current_setting('disponit.tenant', true);   -- se opprett-døra
    PERFORM set_config('disponit.tenant', p_tenant, true);
    BEGIN
        PERFORM public.firma_oppdater(p_tenant, p_navn, p_orgnummer, p_aktor);
    EXCEPTION WHEN OTHERS THEN
        PERFORM set_config('disponit.tenant', coalesce(v_for, ''), true);
        RAISE;
    END;
    PERFORM set_config('disponit.tenant', coalesce(v_for, ''), true);
END $$;

CREATE OR REPLACE FUNCTION plattform_firma_status(
        p_bruker_id TEXT, p_tenant TEXT, p_status TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_for TEXT;
BEGIN
    PERFORM public.plattform_krev_eier(p_bruker_id,
                                       'plattform_firma_status');
    v_for := current_setting('disponit.tenant', true);   -- se opprett-døra
    PERFORM set_config('disponit.tenant', p_tenant, true);
    BEGIN
        -- «Slette» er `stengt`, ikke DELETE. 190s angrefrist og reaper eier
        -- destruksjonen; en DELETE her ville omgått begge.
        PERFORM public.firma_sett_status(p_tenant, p_status, p_aktor);
    EXCEPTION WHEN OTHERS THEN
        PERFORM set_config('disponit.tenant', coalesce(v_for, ''), true);
        RAISE;
    END;
    PERFORM set_config('disponit.tenant', coalesce(v_for, ''), true);
END $$;

REVOKE ALL ON FUNCTION plattform_er_eier(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION plattform_krev_eier(TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION plattform_firmaliste(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION plattform_firma_opprett(TEXT, TEXT, TEXT, TEXT, INT,
                                               TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION plattform_firma_oppdater(TEXT, TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION plattform_firma_status(TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC;

-- Runtime får kalle dørene, og INGENTING annet. `plattform_krev_eier` er
-- bevisst IKKE grantet: den er et internt ledd, ikke en dør utenfra.
GRANT EXECUTE ON FUNCTION plattform_er_eier(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION plattform_firmaliste(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION plattform_firma_opprett(TEXT, TEXT, TEXT, TEXT,
                                                  INT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION plattform_firma_oppdater(TEXT, TEXT, TEXT, TEXT,
                                                   TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION plattform_firma_status(TEXT, TEXT, TEXT, TEXT)
    TO disponit;

RESET ROLE;
