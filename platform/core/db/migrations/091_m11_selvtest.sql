-- 091: M-11 selvtestrunden — kjøringer og prober som TABELLER, tre
-- herdede dører og de to varselartene (rød probe / uteblitt runde).
--
-- PLATTFORMSKOP (dommen natt til 1/9): selvtesten måler PLATTFORMEN,
-- ikke en tenant — ingen tenant-kolonne, ingen RLS, ingen direkte
-- runtime-lesing (SP-7). Lesingen går gjennom `selvtest_status`
-- (051-leseformen, krev_tenantkontekst først).
--
-- STATUSSETTET ER LUKKET OG TREDELT: `gronn`, `rod`,
-- `ikke_konfigurert`. Det tredje er ikke et mildere rødt — det er
-- «denne proben måler noe som ikke er satt opp her», og det varsles
-- ALDRI (dommen). En delvis konfigurasjon er derimot `rod`: fem
-- SMTP-navn der tre finnes er en feil, ikke et fravær.
--
-- Formene er 089/051/035 sine (se 090 for begrunnelsene — samme eier,
-- samme vakter, samme sveipform):
--   * dørene eies av `disponit_m37_claimer`;
--   * skrivedøren `registrer_selvtest` er idempotent på `kjoring_id`
--     og gis KUN til den nye timerrollen `disponit_selvtest`;
--   * varsel per RØD probe køes I SAMME TRANSAKSJON som kjøringen
--     skrives — en rød probe uten varsel i køen er urepresenterbar;
--   * `varsle_selvtest_uteblitt` (3 t-terskelen) hører senderen til:
--     selvtesten kan ikke varsle om sin egen død, så den sveipen bor i
--     varselsenderens pre-pass — selvtesten må overleve pasienten, og
--     varsleren må overleve selvtesten.

CREATE TABLE IF NOT EXISTS selvtest_kjoring (
    kjoring_id  uuid PRIMARY KEY,
    ts          timestamptz NOT NULL DEFAULT now(),
    samlet      text NOT NULL
                CHECK (samlet IN ('gronn', 'rod', 'ikke_konfigurert'))
);

CREATE TABLE IF NOT EXISTS selvtest_probe (
    kjoring_id  uuid NOT NULL REFERENCES selvtest_kjoring (kjoring_id),
    probe       text NOT NULL CHECK (length(btrim(probe)) > 0),
    status      text NOT NULL
                CHECK (status IN ('gronn', 'rod', 'ikke_konfigurert')),
    -- Målingen er et LUKKET objekt av tall/flagg proben selv velger ut —
    -- aldri rå kommandoutdata, aldri miljøverdier. Kanariporten i
    -- test_m11_selvtest måler at en hemmelighet i miljøet ikke finnes
    -- her; CHECK-en holder formen, testen holder innholdet.
    maalt       jsonb NOT NULL DEFAULT '{}'::jsonb
                CHECK (jsonb_typeof(maalt) = 'object'),
    PRIMARY KEY (kjoring_id, probe)
);

-- Statusspørringen leser «nyeste kjøringer først», sveipen bare max(ts).
CREATE INDEX IF NOT EXISTS selvtest_kjoring_ts
    ON selvtest_kjoring (ts DESC);

GRANT SELECT, INSERT ON selvtest_kjoring, selvtest_probe
    TO disponit_m37_claimer;

SET LOCAL ROLE disponit_m37_claimer;

-- ------------------------------------------------------------
-- 1. Skrivedøren: hele runden i ett kall, idempotent på kjoring_id.
--    `p_prober` er {probe: {status, maalt}}; samlet-dommen regnes HER,
--    ikke hos kalleren — en kaller som kunne påstå «gronn» over en rød
--    probe ville vært m31s `kjoring_bestatt_pastatt_av_kaller` på nytt.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION registrer_selvtest(
    p_kjoring_id UUID, p_prober JSONB, p_tenant TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_samlet TEXT; v_navn TEXT; v_probe JSONB;
        b RECORD; v_kanal TEXT; v_dag TEXT; v_n INT := 0;
BEGIN
    IF p_prober IS NULL OR jsonb_typeof(p_prober) <> 'object'
       OR p_prober = '{}'::jsonb THEN
        RAISE EXCEPTION 'registrer_selvtest: p_prober må være et'
            ' ikke-tomt objekt';
    END IF;
    -- Samlet-dommen: én rød probe gjør runden rød; en runde der INGEN
    -- probe fant noe konfigurert er `ikke_konfigurert`; ellers grønn.
    -- Ukjente statusverdier stoppes av probe-CHECKen ved innsettingen
    -- under — dommen her trenger bare de tre lovlige.
    SELECT CASE
             WHEN bool_or(p.value->>'status' = 'rod') THEN 'rod'
             WHEN bool_and(p.value->>'status' = 'ikke_konfigurert')
                  THEN 'ikke_konfigurert'
             ELSE 'gronn'
           END
      INTO v_samlet
      FROM jsonb_each(p_prober) p;
    INSERT INTO public.selvtest_kjoring (kjoring_id, samlet)
    VALUES (p_kjoring_id, v_samlet)
        ON CONFLICT (kjoring_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        -- Runden er alt registrert: en retry skal verken duplisere
        -- prober eller køe varslene en gang til.
        RETURN 0;
    END IF;
    -- RLS-konteksten settes LOKALT for varselveien (035-formen).
    PERFORM set_config('disponit.tenant', p_tenant, true);
    PERFORM set_config('disponit.aktor', 'selvtest', true);
    v_dag := to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD');
    FOR v_navn, v_probe IN
        SELECT p.key, p.value FROM jsonb_each(p_prober) p ORDER BY p.key
    LOOP
        IF jsonb_typeof(v_probe) <> 'object' THEN
            RAISE EXCEPTION 'registrer_selvtest: proben % er ikke et'
                ' objekt', v_navn;
        END IF;
        INSERT INTO public.selvtest_probe (kjoring_id, probe, status, maalt)
        VALUES (p_kjoring_id, v_navn, v_probe->>'status',
                COALESCE(v_probe->'maalt', '{}'::jsonb));
        -- RØD PROBE → VARSEL I SAMME TRANSAKSJON. `ikke_konfigurert`
        -- varsles ALDRI (dommen) — det er et ærlig fravær, ikke en
        -- feil. Nøkkelen (bruker · art · probenavn · dagens dato) gir
        -- ett varsel per probe per døgn, ikke ett per timerkjøring.
        IF v_probe->>'status' = 'rod' THEN
            FOR b IN
                SELECT bm.bruker_id FROM public.brukermedlemskap bm
                 WHERE bm.tenant = p_tenant AND bm.aktiv
                   AND 'admin' = ANY (bm.roller)
                 ORDER BY bm.bruker_id
            LOOP
                PERFORM pg_advisory_xact_lock(
                    615774026,
                    hashtext(p_tenant || E'\x1f' || b.bruker_id));
                SELECT vv.kanal INTO v_kanal FROM public.varselvalg vv
                 WHERE vv.tenant = p_tenant
                   AND vv.bruker_id = b.bruker_id;
                INSERT INTO public.varsel (tenant, bruker_id, art,
                    ressurs_type, ressurs_id, hendelse, tekstnokkel,
                    parametre, epost_status)
                VALUES (p_tenant, b.bruker_id, 'selvtest_rodt', 'selvtest',
                        v_navn, v_dag, 'varsel.selvtest_rodt',
                        jsonb_build_object('probe', v_navn),
                        CASE WHEN COALESCE(v_kanal, 'epost_og_portal')
                                  = 'kun_portal'
                             THEN 'ikke_aktuelt' ELSE 'koet' END)
                     ON CONFLICT DO NOTHING;
                GET DIAGNOSTICS v_rader = ROW_COUNT;
                v_n := v_n + v_rader;
            END LOOP;
        END IF;
    END LOOP;
    RETURN 1;
END $$;

-- ------------------------------------------------------------
-- 2. Lesedøren: siste N kjøringer med probene sine, nyeste først.
--    Flate rader (kjøring × probe) — grupperingen er presentasjon og
--    bor i API-laget, ikke i SQL (oversikt-lærdommen).
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION selvtest_status(p_tenant TEXT, p_grense INT)
RETURNS TABLE(kjoring_id UUID, ts TIMESTAMPTZ, samlet TEXT,
              alder_s BIGINT, probe TEXT, status TEXT, maalt JSONB)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'selvtest_status');
    RETURN QUERY
    WITH siste AS (
        SELECT k.kjoring_id, k.ts, k.samlet,
               EXTRACT(EPOCH FROM (now() - k.ts))::bigint AS alder_s
          FROM public.selvtest_kjoring k
         ORDER BY k.ts DESC
         LIMIT greatest(least(coalesce(p_grense, 20), 100), 1)
    )
    SELECT s.kjoring_id, s.ts, s.samlet, s.alder_s,
           p.probe, p.status, p.maalt
      FROM siste s
      JOIN public.selvtest_probe p ON p.kjoring_id = s.kjoring_id
     ORDER BY s.ts DESC, p.probe;
END $$;

-- ------------------------------------------------------------
-- 3. Sveipen: uteblitt runde. Kadens 1 t, terskel 3 t (3× kadens,
--    samme forhold som helse-sjekkens heartbeat-grense) — og FRAVÆR ER
--    FEIL: en base uten en eneste kjøring varsles på samme terskel.
--    035-formen, se 090 for begrunnelsene.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION varsle_selvtest_uteblitt(p_tenant TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_n INT := 0; v_siste TIMESTAMPTZ; b RECORD; v_kanal TEXT;
        v_rader INT; v_dag TEXT;
BEGIN
    PERFORM set_config('disponit.tenant', p_tenant, true);
    PERFORM set_config('disponit.aktor', 'selvtestvarsel', true);
    SELECT max(k.ts) INTO v_siste FROM public.selvtest_kjoring k;
    IF v_siste IS NOT NULL AND v_siste > now() - interval '3 hours' THEN
        RETURN 0;
    END IF;
    v_dag := to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD');
    FOR b IN
        SELECT bm.bruker_id FROM public.brukermedlemskap bm
         WHERE bm.tenant = p_tenant AND bm.aktiv
           AND 'admin' = ANY (bm.roller)
         ORDER BY bm.bruker_id
    LOOP
        PERFORM pg_advisory_xact_lock(
            615774026, hashtext(p_tenant || E'\x1f' || b.bruker_id));
        SELECT vv.kanal INTO v_kanal FROM public.varselvalg vv
         WHERE vv.tenant = p_tenant AND vv.bruker_id = b.bruker_id;
        INSERT INTO public.varsel (tenant, bruker_id, art, ressurs_type,
                                   ressurs_id, hendelse, tekstnokkel,
                                   parametre, epost_status)
        VALUES (p_tenant, b.bruker_id, 'selvtest_uteblitt', 'selvtest',
                'plattform', v_dag, 'varsel.selvtest_uteblitt',
                jsonb_build_object('siste_ts', v_siste,
                                   'terskel_timer', 3),
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
-- 4. Rettighetene — samme form og samme begrunnelser som 090 §4.
-- ------------------------------------------------------------
REVOKE ALL ON FUNCTION registrer_selvtest(UUID, JSONB, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION selvtest_status(TEXT, INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION varsle_selvtest_uteblitt(TEXT) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_selvtest') THEN
        GRANT EXECUTE ON FUNCTION registrer_selvtest(UUID, JSONB, TEXT)
            TO disponit_selvtest;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_varselsender') THEN
        GRANT EXECUTE ON FUNCTION varsle_selvtest_uteblitt(TEXT)
            TO disponit_varselsender;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        REVOKE ALL ON FUNCTION registrer_selvtest(UUID, JSONB, TEXT)
            FROM disponit;
        REVOKE ALL ON FUNCTION varsle_selvtest_uteblitt(TEXT)
            FROM disponit;
    END IF;
END $$;

RESET ROLE;

-- ------------------------------------------------------------
-- 5. Varselenumet: de to nye artene + ressurstypen (044-splicen; 091
--    kjører etter 090, så 090s art er et gyldig anker).
-- ------------------------------------------------------------
DO $$
DECLARE def TEXT; c TEXT;
BEGIN
  SELECT conname, pg_get_constraintdef(oid) INTO c, def FROM pg_constraint
   WHERE conrelid = 'varsel'::regclass
     AND pg_get_constraintdef(oid) LIKE '%attestering_venter%';
  IF def IS NULL THEN
    RAISE EXCEPTION '091: fant ikke art-CHECKen på varsel';
  END IF;
  IF def NOT LIKE '%selvtest_rodt%' THEN
    def := replace(def, '''backupverifisering_uteblitt''::text',
                   '''backupverifisering_uteblitt''::text,'
                   || ' ''selvtest_rodt''::text, ''selvtest_uteblitt''::text');
    IF def NOT LIKE '%selvtest_rodt%' THEN
      RAISE EXCEPTION '091: kunne ikke utvide % — uventet'
          ' definisjonsform: %', c, def;
    END IF;
    EXECUTE format('ALTER TABLE varsel DROP CONSTRAINT %I', c);
    EXECUTE format('ALTER TABLE varsel ADD CONSTRAINT %I %s', c, def);
  END IF;
  SELECT conname, pg_get_constraintdef(oid) INTO c, def FROM pg_constraint
   WHERE conrelid = 'varsel'::regclass
     AND pg_get_constraintdef(oid) LIKE '%ressurs_type%';
  IF def IS NULL THEN
    RAISE EXCEPTION '091: fant ikke ressurs_type-CHECKen på varsel';
  END IF;
  IF def NOT LIKE '%''selvtest''%' THEN
    def := replace(def, '''backupverifisering''::text',
                   '''backupverifisering''::text, ''selvtest''::text');
    IF def NOT LIKE '%selvtest%' THEN
      RAISE EXCEPTION '091: kunne ikke utvide % — uventet'
          ' definisjonsform: %', c, def;
    END IF;
    EXECUTE format('ALTER TABLE varsel DROP CONSTRAINT %I', c);
    EXECUTE format('ALTER TABLE varsel ADD CONSTRAINT %I %s', c, def);
  END IF;
END $$;
