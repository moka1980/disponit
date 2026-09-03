-- 115: SVEIPESTATUS — flåtens taushet, observert utenfra.
--
-- HVA DENNE LØSER. Plattformen har nå ATTEN nattlige sveip. Hver av
-- dem bærer den samme selvkontrollen: to sammenhengende feilede
-- kjøringer skal utløse alarm, og telleren ligger i en fil fordi hver
-- kjøring er en egen prosess.
--
-- DEN KONTROLLEN HAR ALDRI NÅDD ET MENNESKE. `alarm`-feltet skrives i
-- JSON-linja av alle atten og LESES AV INGEN — et søk gjennom treet
-- finner bare testene. «To feilede kjøringer → alarm» har vært en
-- linje i journalen, ikke en varsling.
--
-- OG DET VERRE TILFELLET ER TAUSHETEN. En sveip som feiler, skriver i
-- det minste noe. En sveip som ALDRI KJØRER — fordi timeren ble
-- deaktivert, enheten feilet ved oppstart, eller DSN-en forsvant fra
-- miljøfila — skriver ingenting i det hele tatt. Den ser nøyaktig ut
-- som en sveip uten funn.
--
-- BEGRUNNELSEN ER HUSETS EGEN, ordrett fra `varselsender.py` (035/090):
--
--     «En taushet kan per definisjon ikke varsle om seg selv — den må
--      observeres utenfra, av en prosess med en annen rolle på en
--      annen kadens.»
--
-- Det er nøyaktig det denne migrasjonen gjør for sveipeflåten, med
-- samme form som `varsle_backupverifisering_uteblitt` (090) og
-- `varsle_selvtest_uteblitt` (091).
--
-- HVORFOR IKKE EN FELLES PLANLEGGER I STEDET. Det nærliggende svaret på
-- atten timere er å slå dem sammen til én jobb. Den jobben måtte hatt
-- alle atten `LoadCredential`-ene og dermed alle atten rollenes
-- fullmakt — og rev ned nøyaktig det oppdelingen finnes for, sagt i
-- `migrer.py`: «en delt sveiperolle måtte hatt EXECUTE på alle
-- kryss-tenant-definerne, og en feil i én sveip ville båret de andres
-- fullmakt.» Atten timere er prisen for at en feil i lønnssveipen ikke
-- kan røre kampanjeregisteret. Det som manglet var ikke planlegging,
-- men OBSERVERBARHET.
--
-- HVORDAN OBSERVASJONEN SKJER, OG HVA DEN KAN SI.
--
--   Hver sveip skriver `/var/lib/disponit/<navn>.json` med
--   `{"feil": n}` etter hver kjøring som ikke ble hoppet over.
--   Lesejobben (`drift.kjor_sveipestatus`) deler Unix-identitet og
--   `StateDirectory` med sveipene, leser filene, og fører ÉN rad per
--   sveip hit.
--
--   `sist_kjort` er FILENS mtime, ikke et felt i den. Det er en ærlig
--   kilde med én navngitt begrensning: en kjøring som fant
--   arbeidernøkkelen opptatt (`hoppet_over`) skriver med vilje IKKE
--   fila — telleren skal stå urørt — og flytter derfor ikke mtime.
--   «Sist kjørt» betyr her «sist fullførte kjøring som ikke ble hoppet
--   over», og det er den definisjonen varselgrensen er satt etter.
--
-- PLATTFORMSKOP MED VILJE (090s form): flåten er hele installasjonens,
-- ikke en tenants. Tabellen har ingen tenant-kolonne og ingen RLS.
-- Varselfunksjonen tar tenanten som parameter og setter DENS
-- RLS-kontekst lokalt, slik 090 og 091 gjør.
--
-- ROLLEN ER `disponit_driftstatus`, GJENBRUKT OG IKKE NY. Den finnes
-- for nøyaktig denne jobbklassen: en drift-observatør som leser
-- filsystemtilstand og fører den inn i basen. Den får én EXECUTE til
-- og ingen tabellrettigheter — og porten måler begge deler. En
-- nittende rolle med egen DSN, egen credential-katalog og egen
-- preflight ville vært maskineri uten en tilsvarende innsnevring.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

CREATE TABLE IF NOT EXISTS sveipestatus (
    -- Sveipens modulnavn, slik `platform/drift/<navn>.py` heter.
    sveip TEXT PRIMARY KEY CHECK (sveip ~ '^[a-z0-9_]+$'),
    -- Tilstandsfilens mtime. Se definisjonen i toppen: «sist fullførte
    -- kjøring som ikke ble hoppet over».
    sist_kjort TIMESTAMPTZ,
    -- `{"feil": n}` fra fila. NULL når fila ikke finnes ennå — og det
    -- er en helt annen tilstand enn 0, som betyr «kjørte, uten feil».
    sammenhengende_feil INT CHECK (sammenhengende_feil >= 0),
    -- Hvor mange timer det maksimalt skal gå mellom to kjøringer før
    -- tausheten er et funn. Lesejobben fører den fra sin egen
    -- rosterliste, så en ny sveip må ta stilling til tallet.
    forventet_timer INT NOT NULL CHECK (forventet_timer BETWEEN 1 AND 168),
    -- Sann når tilstandsfila IKKE fantes. En sveip som aldri har
    -- kjørt, og en som kjørte for lenge siden, er to forskjellige
    -- historier — og den første er den farligste, fordi den ser ut som
    -- ingenting.
    uten_tilstandsfil BOOLEAN NOT NULL DEFAULT false,
    -- Sann når fila FANTES, men ikke lot seg lese som `{"feil": n}`.
    --
    -- UTEN DENNE SER EN KORRUPT TILSTANDSFIL HELT FRISK UT
    -- (CodeRabbit): fila finnes, så `sist_kjort` er fersk og sveipen
    -- er ikke taus — og telleren er NULL, som `coalesce(..., 0)` gjør
    -- til «ingen feil». Begge signalene sier grønt om en sveip vi ikke
    -- vet noe om. Det er nøyaktig den blindsonen modulen finnes for.
    ulesbar BOOLEAN NOT NULL DEFAULT false,
    observert TIMESTAMPTZ NOT NULL DEFAULT now(),
    observert_av TEXT NOT NULL CHECK (observert_av ~ '[^[:space:]]')
);

-- Dørenes eier trenger radene (051/090-formen: kildene claimeren ikke
-- alt leser — her hele tabellen, som er ny). DELETE står IKKE her:
-- flåtens tilstand oppdateres, den ryddes ikke.
GRANT SELECT, INSERT, UPDATE ON sveipestatus TO disponit_m37_claimer;

SET LOCAL ROLE disponit_m37_claimer;


-- Lesejobben fører HELE flåten i én transaksjon, hver kjøring.
-- `ON CONFLICT` fordi raden er per sveip, ikke per observasjon: det er
-- TILSTANDEN som skal kunne leses, ikke en historikk over den.
CREATE OR REPLACE FUNCTION registrer_sveipestatus(
    p_sveip TEXT, p_sist_kjort TIMESTAMPTZ, p_feil INT,
    p_forventet_timer INT, p_uten_fil BOOLEAN, p_ulesbar BOOLEAN,
    p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_ny BOOLEAN;
BEGIN
    INSERT INTO public.sveipestatus
        (sveip, sist_kjort, sammenhengende_feil, forventet_timer,
         uten_tilstandsfil, ulesbar, observert, observert_av)
    VALUES (p_sveip, p_sist_kjort, p_feil, p_forventet_timer,
            p_uten_fil, p_ulesbar, now(), p_aktor)
    ON CONFLICT (sveip) DO UPDATE SET
        sist_kjort = EXCLUDED.sist_kjort,
        sammenhengende_feil = EXCLUDED.sammenhengende_feil,
        forventet_timer = EXCLUDED.forventet_timer,
        uten_tilstandsfil = EXCLUDED.uten_tilstandsfil,
        ulesbar = EXCLUDED.ulesbar,
        observert = now(),
        observert_av = EXCLUDED.observert_av
    RETURNING (xmax = 0) INTO v_ny;
    RETURN v_ny;
END $$;
REVOKE ALL ON FUNCTION registrer_sveipestatus(
    TEXT, TIMESTAMPTZ, INT, INT, BOOLEAN, BOOLEAN, TEXT) FROM PUBLIC;


-- LESEDØREN. Tenantbundet (051-formen), for flaten og for mennesker.
CREATE OR REPLACE FUNCTION sveipeflaaten(p_tenant TEXT)
RETURNS TABLE (sveip TEXT, sist_kjort TIMESTAMPTZ,
               sammenhengende_feil INT, forventet_timer INT,
               uten_tilstandsfil BOOLEAN, ulesbar BOOLEAN,
               timer_siden NUMERIC, taus BOOLEAN, i_alarm BOOLEAN,
               observert TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'sveipeflaaten');
    RETURN QUERY
    SELECT s.sveip, s.sist_kjort, s.sammenhengende_feil,
           s.forventet_timer, s.uten_tilstandsfil, s.ulesbar,
           CASE WHEN s.sist_kjort IS NULL THEN NULL
                ELSE round(
                    EXTRACT(EPOCH FROM (now() - s.sist_kjort))
                    / 3600.0, 1) END,
           -- TAUS: aldri kjørt, eller ikke kjørt innenfor sitt eget
           -- vindu. De to er samme funn for et menneske, og skilles av
           -- `uten_tilstandsfil` for den som vil vite hvilken.
           s.sist_kjort IS NULL
             OR s.sist_kjort < now()
                - make_interval(hours => s.forventet_timer),
           -- I ALARM: to sammenhengende feil, ELLER en teller vi
           -- ikke kan lese. `coalesce(..., 0)` alene ville lest en
           -- ukjent teller som «ingen feil» — det trygge svaret på en
           -- ulesbar fil er at vi ikke vet, ikke at alt er bra.
           coalesce(s.sammenhengende_feil, 0) >= 2 OR s.ulesbar,
           s.observert
      FROM public.sveipestatus s
     ORDER BY s.sveip;
END $$;
REVOKE ALL ON FUNCTION sveipeflaaten(TEXT) FROM PUBLIC;


-- OBSERVATØRENS EGEN TILSTAND, som ett svar. Flaten og mennesket skal
-- kunne se «når så noen sist på flåten» uten å regne det ut selv.
CREATE OR REPLACE FUNCTION sveipeobservasjonen(p_tenant TEXT)
RETURNS TABLE (i_flaaten BIGINT, sist_observert TIMESTAMPTZ,
               uobservert BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'sveipeobservasjonen');
    RETURN QUERY
    SELECT count(*), max(s.observert),
           max(s.observert) IS NULL
             OR max(s.observert) < now() - interval '30 hours'
      FROM public.sveipestatus s;
END $$;
REVOKE ALL ON FUNCTION sveipeobservasjonen(TEXT) FROM PUBLIC;


-- VARSELENUMENE. `art` og `ressurs_type` er lukkede CHECK-er (029),
-- utvidet ADDITIVT i 041 §15-formen (`regexp_replace` på halen) — den
-- formen som tåler at flere moduler utvider den samme CHECK-en i
-- vilkårlig rekkefølge. Ordrett 096/098s blokk.
RESET ROLE;
DO $$
DECLARE r RECORD; def TEXT; ny TEXT;
BEGIN
    FOR r IN SELECT conname, pg_get_constraintdef(oid) AS def
               FROM pg_constraint
              WHERE conrelid = 'varsel'::regclass
                AND conname IN ('varsel_art_chk',
                                'varsel_ressurs_type_chk')
    LOOP
        ny := CASE r.conname WHEN 'varsel_art_chk' THEN 'sveip_uteblitt'
                             ELSE 'sveipestatus' END;
        -- Sammenlikningen gjøres på den KVOTERTE formen: en
        -- delstrengsjekk som blir usann av en nabo er en migrasjon som
        -- kjører to ganger (096s begrunnelse).
        CONTINUE WHEN r.def LIKE '%''' || ny || '''%';
        EXECUTE format('ALTER TABLE varsel DROP CONSTRAINT %I',
                       r.conname);
        def := regexp_replace(r.def, '\]\)\)\)$',
                              format(', %L::text])))', ny));
        IF def NOT LIKE '%''' || ny || '''%' THEN
            RAISE EXCEPTION '115: kunne ikke utvide % — uventet'
                ' definisjonsform: %', r.conname, r.def;
        END IF;
        EXECUTE 'ALTER TABLE varsel ADD CONSTRAINT '
             || quote_ident(r.conname) || ' ' || def;
    END LOOP;
END $$;
SET LOCAL ROLE disponit_m37_claimer;


-- VARSELET. Samme form som `varsle_backupverifisering_uteblitt` (090)
-- og `varsle_selvtest_uteblitt` (091), og av samme grunn.
--
-- ETT VARSEL PER DØGN PER MOTTAKER, ikke ett per sveip: en vert der
-- timerne er slått av ville ellers sendt atten e-poster på én natt, og
-- atten varsler om samme sak er ingen varsling — det er en flom noen
-- lager en filterregel for.
CREATE OR REPLACE FUNCTION varsle_sveip_uteblitt(p_tenant TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_n INT := 0; b RECORD; v_kanal TEXT; v_rader INT; v_dag TEXT;
        v_tause TEXT[]; v_alarm TEXT[]; v_ulesbare TEXT[];
        v_sist_observert TIMESTAMPTZ; v_uobservert BOOLEAN;
BEGIN
    PERFORM set_config('disponit.tenant', p_tenant, true);
    PERFORM set_config('disponit.aktor', 'sveipvarsel', true);

    SELECT coalesce(array_agg(s.sveip ORDER BY s.sveip), ARRAY[]::TEXT[])
      INTO v_tause FROM public.sveipestatus s
     WHERE s.sist_kjort IS NULL
        OR s.sist_kjort < now()
           - make_interval(hours => s.forventet_timer);
    SELECT coalesce(array_agg(s.sveip ORDER BY s.sveip), ARRAY[]::TEXT[])
      INTO v_alarm FROM public.sveipestatus s
     WHERE coalesce(s.sammenhengende_feil, 0) >= 2;
    -- EGET SETT: «vi vet ikke» er ikke det samme som «den feiler», og
    -- et varsel som slo dem sammen ville gjort en korrupt fil til en
    -- feilrapport ingen kan verifisere.
    SELECT coalesce(array_agg(s.sveip ORDER BY s.sveip), ARRAY[]::TEXT[])
      INTO v_ulesbare FROM public.sveipestatus s WHERE s.ulesbar;

    -- HVEM OBSERVERER OBSERVATØREN (CodeRabbit).
    --
    -- Er tabellen TOM, er alle tre settene over tomme — og funksjonen
    -- ville returnert 0. En observatør som aldri har kjørt ville altså
    -- gitt nøyaktig samme svar som en frisk flåte: taushet om taushet,
    -- i den ene modulen som finnes for å bryte den.
    --
    -- Formen er 090s, ordrett: `max(...)` mot et vindu. 30 timer er
    -- samme tall som backupen bruker og av samme grunn — trygt over
    -- ett døgn for en jobb som går 08:35, godt under to.
    SELECT max(s.observert) INTO v_sist_observert
      FROM public.sveipestatus s;
    v_uobservert := v_sist_observert IS NULL
                    OR v_sist_observert < now() - interval '30 hours';

    IF cardinality(v_tause) = 0 AND cardinality(v_alarm) = 0
       AND cardinality(v_ulesbare) = 0 AND NOT v_uobservert THEN
        RETURN 0;
    END IF;

    -- Hendelsen er DØGNET (UTC), som i 090: en tilstand som vedvarer
    -- gir ett nytt varsel per dag — ikke ett per kjøring, og ikke evig
    -- stillhet etter det første.
    v_dag := to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD');
    FOR b IN
        SELECT bm.bruker_id FROM public.brukermedlemskap bm
         WHERE bm.tenant = p_tenant AND bm.aktiv
           AND 'admin' = ANY (bm.roller)
         ORDER BY bm.bruker_id
    LOOP
        -- Kanalvalget leses under SAMME advisory-lås som varsel.opprett
        -- (615774026 = varsel.KANALVALGNOKKEL) — 090s begrunnelse,
        -- uendret: ellers serialiserer en avmelding som skjer akkurat
        -- nå ikke mot denne innsettingen.
        PERFORM pg_advisory_xact_lock(
            615774026, hashtext(p_tenant || E'\x1f' || b.bruker_id));
        SELECT vv.kanal INTO v_kanal FROM public.varselvalg vv
         WHERE vv.tenant = p_tenant AND vv.bruker_id = b.bruker_id;
        INSERT INTO public.varsel (tenant, bruker_id, art, ressurs_type,
                                   ressurs_id, hendelse, tekstnokkel,
                                   parametre, epost_status)
        VALUES (p_tenant, b.bruker_id, 'sveip_uteblitt',
                'sveipestatus', 'plattform', v_dag,
                'varsel.sveip_uteblitt',
                jsonb_build_object(
                    'tause', array_to_string(v_tause, ', '),
                    'antall_tause', cardinality(v_tause),
                    'i_alarm', array_to_string(v_alarm, ', '),
                    'antall_alarm', cardinality(v_alarm),
                    'ulesbare', array_to_string(v_ulesbare, ', '),
                    'antall_ulesbare', cardinality(v_ulesbare),
                    'uobservert', v_uobservert,
                    'sist_observert', v_sist_observert),
                CASE WHEN COALESCE(v_kanal, 'epost_og_portal')
                          = 'kun_portal'
                     THEN 'ikke_aktuelt' ELSE 'koet' END)
             ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS v_rader = ROW_COUNT;
        v_n := v_n + v_rader;
    END LOOP;
    RETURN v_n;
END $$;
REVOKE ALL ON FUNCTION varsle_sveip_uteblitt(TEXT) FROM PUBLIC;


-- ------------------------------------------------------------
-- Rettighetene (046-halens form). Rollene er VALGFRIE og opprettes av
-- oppsett-postgresql.sh, aldri her — derav vaktene. Runtime navngis
-- ikke her i det hele tatt (057-lærdommen): `migrer.py` er autoritativ
-- for den konfigurerte runtimerollens EXECUTE på lesedøren.
-- ------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_driftstatus') THEN
        -- SKRIVEDØREN, OG BARE DEN. Lesejobben fører tilstanden; den
        -- leser den aldri tilbake, og en `sveipeflaaten` den ikke
        -- trenger ville vært en tenantsveip den ikke skal ha (090s
        -- ordlyd, og den gjelder like sterkt her).
        EXECUTE 'GRANT EXECUTE ON FUNCTION registrer_sveipestatus('
            'TEXT, TIMESTAMPTZ, INT, INT, BOOLEAN, BOOLEAN, TEXT)'
            ' TO disponit_driftstatus';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_varselsender') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION varsle_sveip_uteblitt(TEXT)'
            ' TO disponit_varselsender';
    END IF;
END $$;

RESET ROLE;
