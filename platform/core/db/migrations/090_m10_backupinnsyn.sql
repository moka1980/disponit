-- 090: M-10 backupinnsyn — verifiseringshistorikken som TABELL (dommen
-- natt til 1/9: historikk-tabell, ikke fil-per-kall), tre herdede dører
-- og varselarten for uteblitt verifisering.
--
-- PLATTFORMSKOP MED VILJE: backupen er hele basens, ikke en tenants —
-- tabellen har ingen tenant-kolonne og ingen RLS, og LESES aldri
-- direkte av runtime (SP-7). All lesing går gjennom `backup_status`,
-- som krever tenantkontekst (051-leseformen) og bare svarer på
-- SPØRSMÅLET «virker backupen» — radene bærer ingen kundedata.
--
-- Formene er husets egne, med vilje og ordrett der de passer:
--   * dørene er SECURITY DEFINER eid av `disponit_m37_claimer`
--     (051-formen: samme eier som `krev_tenantkontekst` og
--     varselveiens claimer-grants fra 043/044), search_path pg_catalog;
--   * skrivedøren `registrer_backupverifisering` er idempotent på
--     `backup_ts` (ON CONFLICT DO NOTHING) og gis KUN til den nye
--     lesejobbrollen `disponit_driftstatus` — bak pg_roles-vakt, fordi
--     roller er klyngeobjekter en migrasjon aldri kan anta (035);
--   * sveipen `varsle_backupverifisering_uteblitt` er
--     `varsle_tokenfamilie_utlop`-formen fra 035: RLS-GUC settes
--     LOKALT, plattformtenantens aktive admin-medlemmer sveipes i
--     stigende bruker_id, kanalvalget leses under advisory-låsen
--     (615774026 — `varsel.KANALVALGNOKKEL`), og nøkkelen
--     (bruker · art · ressurs · hendelse=dagens dato) gjør sveipen
--     idempotent per døgn. EXECUTE kun `disponit_varselsender`.
--
-- CHECK-grensene på tabellen er FAIL-CLOSED-portene fra planen: en
-- verifisering med færre enn 10 tabeller eller en dump på under 1 KiB
-- er ikke en svak måling — den er et tegn på at restoren målte feil
-- base, og skal avvises, aldri registreres «med forbehold».

CREATE TABLE IF NOT EXISTS backupverifisering (
    -- Backupens eget tidsstempel er identiteten: samme backup verifisert
    -- to ganger er ÉN rad, og lesejobben kan gjenspille siste fil trygt.
    backup_ts           timestamptz PRIMARY KEY,
    verifisert_ts       timestamptz NOT NULL,
    restore_varighet_s  numeric NOT NULL CHECK (restore_varighet_s >= 0),
    tabeller            integer NOT NULL CHECK (tabeller >= 10),
    storrelse_b         bigint NOT NULL CHECK (storrelse_b > 1024),
    registrert          timestamptz NOT NULL DEFAULT now()
);

-- Statusspørringen leser «nyeste først» — og sveipen spør bare etter
-- maks verifisert_ts. Ett indeks dekker begge.
CREATE INDEX IF NOT EXISTS backupverifisering_verifisert
    ON backupverifisering (verifisert_ts DESC);

-- Dørenes eier trenger radene (051-formen: kildene claimeren ikke alt
-- leser — her hele tabellen, som er ny).
GRANT SELECT, INSERT ON backupverifisering TO disponit_m37_claimer;

SET LOCAL ROLE disponit_m37_claimer;

-- ------------------------------------------------------------
-- 1. Skrivedøren: idempotent på backup_ts, teller FAKTISK skrevne rader
--    (ROW_COUNT, ikke RETURNING — 035s begrunnelse: en skrivefunksjon
--    skal ikke måtte kunne lese tabellen for å telle sine egne
--    innsettinger).
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION registrer_backupverifisering(
    p_backup_ts TIMESTAMPTZ, p_verifisert_ts TIMESTAMPTZ,
    p_restore_varighet_s NUMERIC, p_tabeller INT, p_storrelse_b BIGINT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT;
BEGIN
    -- INGEN utfylling av manglende verdier: NOT NULL-kolonnene og
    -- CHECK-ene er porten, og et brudd skal velte kallet — lesejobben
    -- oversetter det til exit 1 uten å ha skrevet noe (fail-closed).
    INSERT INTO public.backupverifisering
        (backup_ts, verifisert_ts, restore_varighet_s, tabeller,
         storrelse_b)
    VALUES (p_backup_ts, p_verifisert_ts, p_restore_varighet_s,
            p_tabeller, p_storrelse_b)
        ON CONFLICT (backup_ts) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    RETURN v_rader;
END $$;

-- ------------------------------------------------------------
-- 2. Lesedøren: siste N + radvis alder (radfakta, aldri analyse —
--    M-16-regelen). Tenantkontekst kreves FØRST (051/SP-1); dataene er
--    plattformens, men RETTEN til å spørre er øktens.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION backup_status(p_tenant TEXT, p_grense INT)
RETURNS TABLE(backup_ts TIMESTAMPTZ, verifisert_ts TIMESTAMPTZ,
              restore_varighet_s NUMERIC, tabeller INT, storrelse_b BIGINT,
              registrert TIMESTAMPTZ, alder_s BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'backup_status');
    RETURN QUERY
    SELECT b.backup_ts, b.verifisert_ts, b.restore_varighet_s, b.tabeller,
           b.storrelse_b, b.registrert,
           -- Alderen regnes i SAMME skann som radene (ett snapshot) —
           -- klienten skal aldri måtte regne «hvor gammel» selv av to
           -- tall fra to tidspunkter.
           EXTRACT(EPOCH FROM (now() - b.verifisert_ts))::bigint
      FROM public.backupverifisering b
     ORDER BY b.verifisert_ts DESC
     LIMIT greatest(least(coalesce(p_grense, 20), 100), 1);
END $$;

-- ------------------------------------------------------------
-- 3. Sveipen: uteblitt verifisering skal VARSLES, ikke oppdages
--    (035 §8-formen, ordrett der den passer). Terskelen er 30 timer —
--    backupen er daglig, så seks timers slark skiller «treg» fra
--    «borte». FRAVÆR ER FEIL i v1 (dommen): en tom tabell varsles på
--    samme terskel som en foreldet — en installasjon uten en eneste
--    verifisering er nøyaktig tilstanden innsynet finnes for.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION varsle_backupverifisering_uteblitt(p_tenant TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_n INT := 0; v_siste TIMESTAMPTZ; b RECORD; v_kanal TEXT;
        v_rader INT; v_dag TEXT;
BEGIN
    -- varsel/brukermedlemskap står under FORCE RLS med tenant-GUC-en som
    -- predikat — funksjonen setter den LOKALT for sin egen transaksjon.
    PERFORM set_config('disponit.tenant', p_tenant, true);
    PERFORM set_config('disponit.aktor', 'backupvarsel', true);
    SELECT max(bv.verifisert_ts) INTO v_siste
      FROM public.backupverifisering bv;
    IF v_siste IS NOT NULL AND v_siste > now() - interval '30 hours' THEN
        RETURN 0;
    END IF;
    -- Hendelsen er DØGNET (UTC): unikhetsnøkkelen varsel_en_per_hendelse
    -- gjør sveipen idempotent innen døgnet, og en tilstand som vedvarer
    -- gir ett nytt varsel per dag — ikke ett per timerkjøring (hvert
    -- 5. minutt), og heller ikke evig stillhet etter det første.
    v_dag := to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD');
    FOR b IN
        SELECT bm.bruker_id FROM public.brukermedlemskap bm
         WHERE bm.tenant = p_tenant AND bm.aktiv
           AND 'admin' = ANY (bm.roller)
         ORDER BY bm.bruker_id
    LOOP
        -- Kanalvalget leses under SAMME advisory-lås som varsel.opprett
        -- (615774026 = varsel.KANALVALGNOKKEL) — ellers serialiserer en
        -- avmelding som skjer akkurat nå ikke mot denne innsettingen.
        PERFORM pg_advisory_xact_lock(
            615774026, hashtext(p_tenant || E'\x1f' || b.bruker_id));
        SELECT vv.kanal INTO v_kanal FROM public.varselvalg vv
         WHERE vv.tenant = p_tenant AND vv.bruker_id = b.bruker_id;
        INSERT INTO public.varsel (tenant, bruker_id, art, ressurs_type,
                                   ressurs_id, hendelse, tekstnokkel,
                                   parametre, epost_status)
        VALUES (p_tenant, b.bruker_id, 'backupverifisering_uteblitt',
                'backupverifisering', 'plattform', v_dag,
                'varsel.backupverifisering_uteblitt',
                jsonb_build_object('siste_verifisert_ts', v_siste,
                                   'terskel_timer', 30),
                CASE WHEN COALESCE(v_kanal, 'epost_og_portal')
                          = 'kun_portal'
                     THEN 'ikke_aktuelt' ELSE 'koet' END)
             ON CONFLICT DO NOTHING;
        GET DIAGNOSTICS v_rader = ROW_COUNT;
        v_n := v_n + v_rader;
    END LOOP;
    RETURN v_n;
END $$;

-- ------------------------------------------------------------
-- 4. Rettighetene — satt av eieren selv (046-halens form). Runtime
--    navngis ikke her i det hele tatt (057-lærdommen): migrer.py er
--    autoritativ for den konfigurerte runtimerollens EXECUTE på
--    `backup_status`. Begge de navngitte rollene er VALGFRIE og
--    opprettes av oppsett-postgresql.sh, aldri her — derav vaktene.
-- ------------------------------------------------------------
REVOKE ALL ON FUNCTION registrer_backupverifisering(
    TIMESTAMPTZ, TIMESTAMPTZ, NUMERIC, INT, BIGINT) FROM PUBLIC;
REVOKE ALL ON FUNCTION backup_status(TEXT, INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION varsle_backupverifisering_uteblitt(TEXT) FROM PUBLIC;
DO $$
BEGIN
    -- Skrivedøren er lesejobbens ALENE: web-runtime skal ikke kunne
    -- dikte en verifisering, og senderen skal ikke kunne skrive en.
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_driftstatus') THEN
        GRANT EXECUTE ON FUNCTION registrer_backupverifisering(
            TIMESTAMPTZ, TIMESTAMPTZ, NUMERIC, INT, BIGINT)
            TO disponit_driftstatus;
    END IF;
    -- Sveipen er senderens pre-pass, som varsle_tokenfamilie_utlop: den
    -- tar tenanten som parameter og setter DENS RLS-kontekst — et grant
    -- til web-runtime ville gitt forespørselsveien nøyaktig det
    -- kryss-tenant-vinduet senderrollen finnes for å nekte den.
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_varselsender') THEN
        GRANT EXECUTE ON FUNCTION varsle_backupverifisering_uteblitt(TEXT)
            TO disponit_varselsender;
    END IF;
    -- En rettighet som bare slutter å bli gitt, er ikke trukket tilbake
    -- (035): standard-runtimenavnet REVOKEs eksplisitt der det finnes.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        REVOKE ALL ON FUNCTION registrer_backupverifisering(
            TIMESTAMPTZ, TIMESTAMPTZ, NUMERIC, INT, BIGINT) FROM disponit;
        REVOKE ALL ON FUNCTION varsle_backupverifisering_uteblitt(TEXT)
            FROM disponit;
    END IF;
END $$;

RESET ROLE;

-- ------------------------------------------------------------
-- 5. Varselenumet utvides med arten og ressurstypen — splice av
--    GJELDENDE definisjon, samme grep som 041 §15 og 044.
-- ------------------------------------------------------------
DO $$
DECLARE def TEXT; c TEXT;
BEGIN
  SELECT conname, pg_get_constraintdef(oid) INTO c, def FROM pg_constraint
   WHERE conrelid = 'varsel'::regclass
     AND pg_get_constraintdef(oid) LIKE '%attestering_venter%';
  IF def IS NULL THEN
    RAISE EXCEPTION '090: fant ikke art-CHECKen på varsel';
  END IF;
  IF def NOT LIKE '%backupverifisering_uteblitt%' THEN
    def := replace(def, '''plan_gjentatt_brudd''::text',
                   '''plan_gjentatt_brudd''::text,'
                   || ' ''backupverifisering_uteblitt''::text');
    IF def NOT LIKE '%backupverifisering_uteblitt%' THEN
      RAISE EXCEPTION '090: kunne ikke utvide % — uventet'
          ' definisjonsform: %', c, def;
    END IF;
    EXECUTE format('ALTER TABLE varsel DROP CONSTRAINT %I', c);
    EXECUTE format('ALTER TABLE varsel ADD CONSTRAINT %I %s', c, def);
  END IF;
  -- ... og ressurstypen.
  SELECT conname, pg_get_constraintdef(oid) INTO c, def FROM pg_constraint
   WHERE conrelid = 'varsel'::regclass
     AND pg_get_constraintdef(oid) LIKE '%ressurs_type%';
  IF def IS NULL THEN
    RAISE EXCEPTION '090: fant ikke ressurs_type-CHECKen på varsel';
  END IF;
  IF def NOT LIKE '%''backupverifisering''%' THEN
    def := replace(def, '''plan''::text',
                   '''plan''::text, ''backupverifisering''::text');
    IF def NOT LIKE '%backupverifisering%' THEN
      RAISE EXCEPTION '090: kunne ikke utvide % — uventet'
          ' definisjonsform: %', c, def;
    END IF;
    EXECUTE format('ALTER TABLE varsel DROP CONSTRAINT %I', c);
    EXECUTE format('ALTER TABLE varsel ADD CONSTRAINT %I %s', c, def);
  END IF;
END $$;
