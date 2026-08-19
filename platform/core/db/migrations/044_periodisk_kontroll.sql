-- ============================================================
-- 044 — PERIODISK KONTROLL (klarsignal 2026-08-19, konsolidert)
--
-- Nummeret er verifisert mot main ved branch-push (043 var siste);
-- klarsignalet navngir med vilje ikke noe nummer.
--
-- Det bærende: PLANEN BESTILLER, MOTOREN BESLUTTER. Planen er en
-- produsent inn i /v1/bestilling-veien — aldri en snarvei rundt den.
-- Planen eier rytme, parametre, egen tilstand og en deterministisk
-- idempotensnøkkel per vindu. Den eier IKKE frist (oppdragskontrakten),
-- ikke retten til å utføre (policyen, per tick), ikke domeneautorisasjon
-- (domenekontroll) og ikke kvote (motorens teller — som ALDRI frigis:
-- reservasjonen er beslutningens evidens, og en angrevei ville betydd at
-- revisjonssporet kan endres av det som skjedde etterpå).
--
-- Eierskap: planfunksjonene er CLAIMER-EIDE (disponit_m37_claimer) — de
-- er arbeidsdispatcherens, som resten av M-37-flaten, og claimeren
-- bærer CURRENT_USER-policyen som gjør kryss-tenant-plukket mulig under
-- FORCE RLS (041-lærdommen: tabelleierens definer-lesing filtreres også).
-- Runtime har INGEN tabellrettigheter her — kun EXECUTE (port 7).
-- ============================================================

-- ------------------------------------------------------------
-- 1. Tabellene — MIGRATOR-EIDE (unntak-modellen): migrers sekvens-
--    grants og eierskapsdesignet forblir enkle; claimeren får GRANTS +
--    dispatcher-policy, aldri eierskap. (Målt: claimer-eide tabeller ga
--    «permission denied for sequence» i migrers ALL SEQUENCES-grant.)
-- ------------------------------------------------------------
CREATE TABLE bestillingsplan (
  plan_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant TEXT NOT NULL,
  bestillingstype TEXT NOT NULL,
  parametre JSONB NOT NULL,
  rytme TEXT NOT NULL CHECK (rytme IN ('daglig','ukentlig','manedlig')),
  ukedag SMALLINT, manedsdag SMALLINT,
  time_lokal SMALLINT NOT NULL CHECK (time_lokal BETWEEN 0 AND 23),
  tidssone TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'utkast'
    CHECK (status IN ('utkast','aktiv','pauset','stanset')),
  pause_aarsak TEXT CHECK (pause_aarsak IN
    ('menneskelig_avvis','policy_stopper','modul_utilgjengelig',
     'gjentatt_uten_resultat')),
  opprettet_av TEXT NOT NULL,
  aktivert_av TEXT,
  opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- NULL-sikret med eksplisitt IS NOT NULL: `NULL BETWEEN 1 AND 7` er
  -- NULL, og en CHECK som evaluerer til NULL SLIPPER RADEN GJENNOM — en
  -- ukentlig plan uten ukedag ville bestått uten leddet (funnet av
  -- port 2/4-testen, ikke av gjennomlesning).
  CONSTRAINT plan_rytme_komplett CHECK (
       (rytme = 'daglig'    AND ukedag IS NULL AND manedsdag IS NULL)
    OR (rytme = 'ukentlig'  AND ukedag IS NOT NULL
        AND ukedag BETWEEN 1 AND 7 AND manedsdag IS NULL)
    OR (rytme = 'manedlig'  AND ukedag IS NULL
        AND manedsdag IS NOT NULL AND manedsdag BETWEEN 1 AND 28)),
  -- Pausegrunnen impliserer status RELASJONELT (samme presisering som
  -- kansellert_aarsak i 043): en grunn på en plan som ikke er pauset er
  -- en løgn skjemaet selv nekter å bære — og omvendt: en pause uten
  -- grunn er en pause ingen kan gjenoppta informert.
  CONSTRAINT plan_pause_aarsak_krever_status CHECK (
    (pause_aarsak IS NULL) = (status <> 'pauset')));

-- Autoritativt aktiveringsintervall: en plan kan pauses og gjenopptas,
-- så aktivering er ikke ett tidspunkt. Kvalifiseringen i §4 leser
-- PERIODENE, aldri planstatusen alene.
CREATE TABLE bestillingsplan_aktiv_periode (
  plan_id UUID NOT NULL REFERENCES bestillingsplan (plan_id),
  tenant TEXT NOT NULL,
  fra_ts TIMESTAMPTZ NOT NULL,
  til_ts TIMESTAMPTZ,
  aarsak_slutt TEXT,
  PRIMARY KEY (plan_id, fra_ts),
  CONSTRAINT periode_gyldig CHECK (til_ts IS NULL OR til_ts > fra_ts));
CREATE UNIQUE INDEX en_apen_periode_per_plan
  ON bestillingsplan_aktiv_periode (plan_id) WHERE til_ts IS NULL;

-- MUTEX: eneste autoritet for retten til å FORSØKE (materialisering) og
-- til å TERMINALISERE. Overlapp er umulig per PK.
CREATE TABLE bestillingsplan_vindu (
  plan_id UUID NOT NULL REFERENCES bestillingsplan (plan_id),
  tenant TEXT NOT NULL,
  vindu_start TIMESTAMPTZ NOT NULL,
  vindu_slutt TIMESTAMPTZ NOT NULL,
  tilstand TEXT NOT NULL DEFAULT 'ledig'
    CHECK (tilstand IN ('ledig','aktivt','terminal')),
  claim_id UUID,
  lease_utloper TIMESTAMPTZ,
  terminalisert_ts TIMESTAMPTZ,
  PRIMARY KEY (plan_id, vindu_start),
  CONSTRAINT vindu_gyldig CHECK (vindu_slutt > vindu_start),
  CONSTRAINT vindu_tilstand_komplett CHECK (
       (tilstand = 'ledig'    AND claim_id IS NULL
                              AND lease_utloper IS NULL
                              AND terminalisert_ts IS NULL)
    OR (tilstand = 'aktivt'   AND claim_id IS NOT NULL
                              AND lease_utloper IS NOT NULL
                              AND terminalisert_ts IS NULL)
    OR (tilstand = 'terminal' AND terminalisert_ts IS NOT NULL)));

-- EVIDENS: append-only, kun terminale utfall. INGEN RAD = VENTENDE —
-- kapasitetstak kan forsinke et vindu, aldri konsumere det. FK-en mot
-- vinduet gjør «tick kan bare skrives sammen med terminal overgang»
-- håndhevet av TO mekanismer (funksjonen OG lagringen), ikke én.
CREATE TABLE bestillingsplan_tick (
  plan_id UUID NOT NULL,
  tenant TEXT NOT NULL,
  vindu_start TIMESTAMPTZ NOT NULL,
  idempotensnokkel TEXT NOT NULL
    CHECK (char_length(idempotensnokkel) BETWEEN 8 AND 200),
  -- `avvist_av_menneske` står IKKE her (Codex P2): en kansellering skjer
  -- ETTER at ticket er skrevet, og evidensen er append-only — å innføre
  -- en verdi ingen skriver kan produsere ville vært et løfte skjemaet
  -- ikke kan holde. Diskriminatoren avledes i lesingen i stedet, av
  -- `hent_plan_tick`, uten å røre revisjonssporet.
  utfall TEXT NOT NULL CHECK (utfall IN
    ('tillat','stopp','brudd','hoppet_over')),
  oppdrag_id BIGINT,
  detalj JSONB,
  registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (plan_id, vindu_start),
  FOREIGN KEY (plan_id, vindu_start)
    REFERENCES bestillingsplan_vindu (plan_id, vindu_start));

-- Append-only overgangsspor for planen selv (opprettet, aktivert,
-- pauset m/ grunn, gjenopptatt, stanset, varslet, aggregert nedetid).
CREATE TABLE bestillingsplan_hendelse (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  plan_id UUID NOT NULL REFERENCES bestillingsplan (plan_id),
  tenant TEXT NOT NULL,
  hendelse TEXT NOT NULL CHECK (hendelse IN
    ('opprettet','aktivert','pauset','gjenopptatt','stanset',
     'varslet','nedetid_aggregert','sikkerhetsavvik')),
  aktor TEXT NOT NULL,
  request_id TEXT,
  detalj JSONB,
  ts TIMESTAMPTZ NOT NULL DEFAULT now());

-- ------------------------------------------------------------
-- 2. RLS + FORCE på alt; policyer: tenant-GUC ELLER claimeren selv
--    (speiler unntak-tabellens m37_dispatcher-policy — claimeren er
--    dispatcheren, og materialiseringen plukker på tvers av tenanter).
-- ------------------------------------------------------------
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['bestillingsplan','bestillingsplan_aktiv_periode',
                           'bestillingsplan_vindu','bestillingsplan_tick',
                           'bestillingsplan_hendelse'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY %I_tenant ON %I USING '
      || '(tenant = current_setting(''disponit.tenant'', true))', t, t);
    EXECUTE format(
      'CREATE POLICY %I_dispatcher ON %I USING '
      || '(CURRENT_USER = ''disponit_m37_claimer'')', t, t);
  END LOOP;
END $$;

-- ------------------------------------------------------------
-- 3. Immutabilitet
-- ------------------------------------------------------------
-- «Terminal» gjelder HELE raden, ikke bare kolonnen
-- (konsolideringspresiseringen): kunne `terminalisert_ts` flyttes eller
-- `claim_id` omskrives, ville evidensen om hvem som terminaliserte når
-- kunne endres i ettertid.
CREATE TRIGGER vindu_terminal_er_endelig
  BEFORE UPDATE ON bestillingsplan_vindu
  FOR EACH ROW WHEN (OLD.tilstand = 'terminal' AND (
        NEW.tilstand         IS DISTINCT FROM OLD.tilstand
     OR NEW.terminalisert_ts IS DISTINCT FROM OLD.terminalisert_ts
     OR NEW.claim_id         IS DISTINCT FROM OLD.claim_id
     OR NEW.lease_utloper    IS DISTINCT FROM OLD.lease_utloper
     OR NEW.vindu_slutt      IS DISTINCT FROM OLD.vindu_slutt
     OR NEW.vindu_start      IS DISTINCT FROM OLD.vindu_start
     OR NEW.plan_id          IS DISTINCT FROM OLD.plan_id
     OR NEW.tenant           IS DISTINCT FROM OLD.tenant))
  EXECUTE FUNCTION avvis_endring();

-- Tick kan bare finnes for et TERMINALT vindu (port 52): FK-en beviser
-- at raden finnes, denne beviser tilstanden — funksjonen, FK-en og
-- triggeren er tre uavhengige mekanismer for samme invariant. AFTER,
-- fordi terminaliser_planvindu setter tilstanden i samme transaksjon
-- rett før ticket skrives.
CREATE OR REPLACE FUNCTION tick_krever_terminalt_vindu()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.bestillingsplan_vindu w
                    WHERE w.plan_id = NEW.plan_id
                      AND w.vindu_start = NEW.vindu_start
                      AND w.tilstand = 'terminal') THEN
        RAISE EXCEPTION 'tick uten terminal vindustilstand';
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER tick_terminalt_vindu
  AFTER INSERT ON bestillingsplan_tick
  FOR EACH ROW EXECUTE FUNCTION tick_krever_terminalt_vindu();

-- Den åpne perioderaden kan lukkes ÉN gang: til_ts NULL → verdi, aldri
-- tilbake, og en lukket rad er i sin helhet endelig.
CREATE OR REPLACE FUNCTION plan_periode_laas()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.til_ts IS NOT NULL THEN
        RAISE EXCEPTION 'bestillingsplan_aktiv_periode: en lukket periode er endelig';
    END IF;
    IF NEW.plan_id IS DISTINCT FROM OLD.plan_id
       OR NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.fra_ts IS DISTINCT FROM OLD.fra_ts THEN
        RAISE EXCEPTION 'bestillingsplan_aktiv_periode: identiteten er uforanderlig';
    END IF;
    IF NEW.til_ts IS NULL THEN
        RAISE EXCEPTION 'bestillingsplan_aktiv_periode: eneste lovlige endring er å lukke';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER periode_lukkes_en_gang
  BEFORE UPDATE ON bestillingsplan_aktiv_periode
  FOR EACH ROW EXECUTE FUNCTION plan_periode_laas();

-- Tick og hendelse tåler ingen UPDATE/DELETE; ingenting her slettes.
CREATE OR REPLACE FUNCTION plan_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '%: % er ikke tillatt (append-only, 044)',
        TG_TABLE_NAME, TG_OP USING ERRCODE = 'check_violation';
END $$;
CREATE TRIGGER tick_ingen_update BEFORE UPDATE OR DELETE
  ON bestillingsplan_tick FOR EACH ROW EXECUTE FUNCTION plan_append_only();
CREATE TRIGGER hendelse_ingen_update BEFORE UPDATE OR DELETE
  ON bestillingsplan_hendelse FOR EACH ROW
  EXECUTE FUNCTION plan_append_only();
CREATE TRIGGER periode_ingen_delete BEFORE DELETE
  ON bestillingsplan_aktiv_periode FOR EACH ROW
  EXECUTE FUNCTION plan_append_only();
CREATE TRIGGER vindu_ingen_delete BEFORE DELETE
  ON bestillingsplan_vindu FOR EACH ROW EXECUTE FUNCTION plan_append_only();
CREATE TRIGGER plan_ingen_delete BEFORE DELETE
  ON bestillingsplan FOR EACH ROW EXECUTE FUNCTION plan_append_only();

-- Claimeren (funksjonseieren under) får radrettighetene tabellene
-- krever — dispatcher-policyen i §2 gir den synet, grantene gir verbene.
GRANT SELECT, INSERT, UPDATE ON bestillingsplan,
    bestillingsplan_aktiv_periode, bestillingsplan_vindu,
    bestillingsplan_tick, bestillingsplan_hendelse
    TO disponit_m37_claimer;
GRANT USAGE, SELECT ON SEQUENCE bestillingsplan_hendelse_id_seq
    TO disponit_m37_claimer;

-- ------------------------------------------------------------
-- 4. Planens livsløp — herdede funksjoner, aldri direkte DML
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_m37_claimer;

-- PORTEN FØRST (Codex P1): hver tenant-skopet definer under binder
-- `p_tenant` til kallerens FAKTISKE tenantkontekst med `krev_tenantkontekst`
-- (038 §4) — den samme GUC-en `sett_kontekst` setter og all vanlig RLS
-- måles mot.
--
-- Uten porten var `p_tenant` kallerens frie ord. En kompromittert
-- `disponit`-credential — eller én SQL-injeksjon i en fremtidig kodevei —
-- kunne kalle `hent_planer('offer')` og få HELE en annen tenants planflate
-- ut, fordi definer-funksjonen kjører som claimeren og FORCE RLS måles mot
-- eierens dispatcher-policy, ikke mot innloggingens tenant. Mutasjonene var
-- verre: `opprett_plan`/`aktiver_plan` kunne LAGE og starte en stående
-- bestilling hos en annen tenant, på hennes kvote.
--
-- Fail-closed: uten kontekst (NULL/tom) finnes ingen tenant å være lik, og
-- kallet avvises. Kryss-tenant-autoriteten finnes fortsatt — men bare
-- innelukket i sveipefunksjonene som IKKE tar `p_tenant` i det hele tatt
-- (`forfalte_planvinduer`, `utlopte_planvinduer`, kandidatfunksjonene):
-- de plukker per definisjon på tvers, og har sitt eget grant.
--
-- Porten eies av claimeren selv (038, gjenopprettet av eierskapsmodellen),
-- og definerne her kjører som nettopp den rollen — intet ekstra grant.

CREATE OR REPLACE FUNCTION opprett_plan(
    p_tenant TEXT, p_bestillingstype TEXT, p_parametre JSONB,
    p_rytme TEXT, p_ukedag SMALLINT, p_manedsdag SMALLINT,
    p_time_lokal SMALLINT, p_tidssone TEXT,
    p_aktor TEXT, p_request_id TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'opprett_plan');
    -- Tidssonen valideres mot serverens egen katalog — en plan med en
    -- sone PostgreSQL ikke kjenner ville feilet først ved materialisering.
    IF NOT EXISTS (SELECT 1 FROM pg_timezone_names
                    WHERE name = p_tidssone) THEN
        RAISE EXCEPTION 'opprett_plan: ukjent tidssone %', p_tidssone
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.bestillingsplan
        (tenant, bestillingstype, parametre, rytme, ukedag, manedsdag,
         time_lokal, tidssone, opprettet_av)
    VALUES (p_tenant, p_bestillingstype, p_parametre, p_rytme, p_ukedag,
            p_manedsdag, p_time_lokal, p_tidssone, p_aktor)
    RETURNING plan_id INTO v_id;
    INSERT INTO public.bestillingsplan_hendelse
        (plan_id, tenant, hendelse, aktor, request_id, detalj)
    VALUES (v_id, p_tenant, 'opprettet', p_aktor, p_request_id,
            jsonb_build_object('rytme', p_rytme));
    RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION aktiver_plan(
    p_tenant TEXT, p_plan UUID, p_aktor TEXT, p_request_id TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'aktiver_plan');
    SELECT status INTO v_status FROM public.bestillingsplan
     WHERE tenant = p_tenant AND plan_id = p_plan FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'aktiver_plan: ukjent plan'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_status <> 'utkast' THEN
        RAISE EXCEPTION 'aktiver_plan: planen er % (kun utkast aktiveres '
            'her; gjenopptak har sin egen vei)', v_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.bestillingsplan
       SET status = 'aktiv', aktivert_av = p_aktor
     WHERE tenant = p_tenant AND plan_id = p_plan;
    INSERT INTO public.bestillingsplan_aktiv_periode
        (plan_id, tenant, fra_ts) VALUES (p_plan, p_tenant, now());
    INSERT INTO public.bestillingsplan_hendelse
        (plan_id, tenant, hendelse, aktor, request_id)
    VALUES (p_plan, p_tenant, 'aktivert', p_aktor, p_request_id);
END $$;

-- Pausen er alltid auditert med grunn og varslet til den som aktiverte
-- planen. Varselet er ikke evidens; hendelsen er — feiler varslingen,
-- står pausen (samme kontrakt som varsle_overtakelse, 041 port 41).
CREATE OR REPLACE FUNCTION pause_plan(
    p_tenant TEXT, p_plan UUID, p_aarsak TEXT, p_aktor TEXT,
    p_request_id TEXT, p_detalj JSONB DEFAULT NULL)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_aktivert_av TEXT; v_bruker TEXT; v_hendelse BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'pause_plan');
    SELECT status, aktivert_av INTO v_status, v_aktivert_av
      FROM public.bestillingsplan
     WHERE tenant = p_tenant AND plan_id = p_plan FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'pause_plan: ukjent plan'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_status <> 'aktiv' THEN
        RETURN false;    -- idempotent: alt pauset/stanset — ingen ny pause
    END IF;
    UPDATE public.bestillingsplan
       SET status = 'pauset', pause_aarsak = p_aarsak
     WHERE tenant = p_tenant AND plan_id = p_plan;
    UPDATE public.bestillingsplan_aktiv_periode
       SET til_ts = now(), aarsak_slutt = p_aarsak
     WHERE plan_id = p_plan AND til_ts IS NULL;
    INSERT INTO public.bestillingsplan_hendelse
        (plan_id, tenant, hendelse, aktor, request_id, detalj)
    VALUES (p_plan, p_tenant, 'pauset', p_aktor, p_request_id,
            jsonb_build_object('aarsak', p_aarsak) || coalesce(p_detalj,
                                                              '{}'::jsonb))
    RETURNING id INTO v_hendelse;
    -- Varsle den som aktiverte planen (in-app; e-postkøen tar resten).
    -- `aktivert_av` er en AKTØRSTRENG ('bruker:<bid>'); varselets
    -- bruker_id er FK mot brukeridentitet, så id-en løses opp — en rå
    -- aktørstreng ville brutt FK-en og blitt stille slukt av vernet
    -- under (funnet av port 17-testen: pause.uten_varsel).
    BEGIN
        v_bruker := CASE WHEN v_aktivert_av LIKE 'bruker:%'
                         THEN substring(v_aktivert_av FROM 8) END;
        IF v_bruker IS NOT NULL THEN
            -- `hendelse` er FOREKOMSTEN, ikke arten (Codex P2; samme
            -- sluk som 041 §15 og som bruddvarselet under). Med literalen
            -- 'pauset' var nøkkelen (tenant, bruker, 'plan_pauset',
            -- 'plan', plan_id, 'pauset') KONSTANT per plan: pause nummer
            -- to — etter et gjenopptak, og kanskje med en helt annen
            -- grunn — traff `varsel_en_per_hendelse` og ble slukt av
            -- vernet under. Overgangen sto, men eieren fikk verken
            -- varselet eller `varslet`-sporet, og verst for den som hadde
            -- lest det gamle varselet og altså ikke så noe nytt.
            -- Pause-hendelsens id er global og monoton, og den ER pausen.
            INSERT INTO public.varsel (tenant, bruker_id, art, ressurs_type,
                ressurs_id, hendelse, tekstnokkel, parametre)
            VALUES (p_tenant, v_bruker, 'plan_pauset', 'plan',
                    p_plan::text, 'pauset:' || v_hendelse,
                    'varsel.plan_pauset',
                    jsonb_build_object('aarsak', p_aarsak));
            INSERT INTO public.bestillingsplan_hendelse
                (plan_id, tenant, hendelse, aktor, request_id, detalj)
            VALUES (p_plan, p_tenant, 'varslet', p_aktor, p_request_id,
                    jsonb_build_object('bruker', v_bruker));
        END IF;
    EXCEPTION WHEN OTHERS THEN
        -- Varselet er ikke evidens; pausen står (port 41). WARNING, ikke
        -- stillhet: en varselvei som ryker skal SES i driftsloggen.
        RAISE WARNING 'pause_plan: varsel feilet for %: %', p_plan, SQLERRM;
    END;
    RETURN true;
END $$;

CREATE OR REPLACE FUNCTION gjenoppta_plan(
    p_tenant TEXT, p_plan UUID, p_aktor TEXT, p_request_id TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'gjenoppta_plan');
    SELECT status INTO v_status FROM public.bestillingsplan
     WHERE tenant = p_tenant AND plan_id = p_plan FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'gjenoppta_plan: ukjent plan'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_status <> 'pauset' THEN
        RAISE EXCEPTION 'gjenoppta_plan: planen er % (kun pauset kan '
            'gjenopptas)', v_status USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.bestillingsplan
       SET status = 'aktiv', pause_aarsak = NULL
     WHERE tenant = p_tenant AND plan_id = p_plan;
    INSERT INTO public.bestillingsplan_aktiv_periode
        (plan_id, tenant, fra_ts) VALUES (p_plan, p_tenant, now());
    -- Gjenopptakelsen nullstiller tellerne ved sin blotte eksistens:
    -- telleverkene (`brudd`/`uten resultat` på rad) leses ALLTID kun
    -- innenfor gjeldende åpne periode.
    INSERT INTO public.bestillingsplan_hendelse
        (plan_id, tenant, hendelse, aktor, request_id)
    VALUES (p_plan, p_tenant, 'gjenopptatt', p_aktor, p_request_id);
END $$;

CREATE OR REPLACE FUNCTION stans_plan(
    p_tenant TEXT, p_plan UUID, p_aktor TEXT, p_request_id TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'stans_plan');
    SELECT status INTO v_status FROM public.bestillingsplan
     WHERE tenant = p_tenant AND plan_id = p_plan FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'stans_plan: ukjent plan'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_status = 'stanset' THEN
        RETURN;                                   -- idempotent
    END IF;
    UPDATE public.bestillingsplan
       SET status = 'stanset', pause_aarsak = NULL
     WHERE tenant = p_tenant AND plan_id = p_plan;
    UPDATE public.bestillingsplan_aktiv_periode
       SET til_ts = now(), aarsak_slutt = 'stanset'
     WHERE plan_id = p_plan AND til_ts IS NULL;
    INSERT INTO public.bestillingsplan_hendelse
        (plan_id, tenant, hendelse, aktor, request_id)
    VALUES (p_plan, p_tenant, 'stanset', p_aktor, p_request_id);
END $$;

-- ------------------------------------------------------------
-- 5. Vindusprotokollen (§4): claim, terminalisering, plukk
-- ------------------------------------------------------------

-- Forfallet er vindu_start pluss et minutt AVLEDET av plan_id — samme
-- spredningsgrep som revalideringen (019 §3.3), av samme grunn: hundre
-- planer klokka 08 skal ikke treffe motoren samtidig. `mod()`, ikke `%`.
CREATE OR REPLACE FUNCTION plan_forfallsminutt(p_plan UUID)
RETURNS INT LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
    SELECT mod(get_byte(sha256(convert_to(p_plan::text,'UTF8')),0)::INT * 256
             + get_byte(sha256(convert_to(p_plan::text,'UTF8')),1)::INT, 60)
$$;

-- Plukket: forfalte, kvalifiserte vinduer — eldste forfall først, hardt
-- tak. Kvalifisert = FORFALLET (ikke vindu_start) ligger i en aktiv
-- periode: en plan aktivert midt i vinduet, men før forfall, skal kjøre;
-- en plan aktivert ETTER forfall får INGEN rad (port 32/38).
-- Vinduer materialiseres her (INSERT ... ON CONFLICT DO NOTHING) idet de
-- plukkes — det finnes ingen rad før noen har noe å gjøre med den.
CREATE OR REPLACE FUNCTION forfalte_planvinduer(p_maks INT)
RETURNS TABLE(plan_id UUID, tenant TEXT, vindu_start TIMESTAMPTZ,
              vindu_slutt TIMESTAMPTZ, forfall TIMESTAMPTZ,
              bestillingstype TEXT, parametre JSONB)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
#variable_conflict use_column
-- (RETURNS TABLE-navnene kolliderer ellers med ON CONFLICT-kolonnene)
BEGIN
    RETURN QUERY
    WITH aktive AS (
        SELECT p.plan_id, p.tenant, p.bestillingstype, p.parametre,
               p.rytme, p.ukedag, p.manedsdag, p.time_lokal, p.tidssone
          FROM public.bestillingsplan p
         WHERE p.status = 'aktiv'
    ), kandidat AS (
        SELECT a.*,
               -- Siste rytmetreff-tidspunkt i planens egen sone, som
               -- TIMESTAMPTZ. date_trunc i lokal tid, så time settes.
               (date_trunc('day', now() AT TIME ZONE a.tidssone)
                + make_interval(hours => a.time_lokal))
                 AT TIME ZONE a.tidssone AS dagens
          FROM aktive a
    ), vindu AS (
        SELECT k.plan_id, k.tenant, k.bestillingstype, k.parametre,
               v.start AS vindu_start,
               v.start + public.plan_forfallsminutt(k.plan_id)
                       * interval '1 minute' AS forfall,
               v.start + public.plan_forfallsminutt(k.plan_id)
                       * interval '1 minute'
                       + CASE k.rytme WHEN 'daglig'   THEN interval '2 hours'
                                      WHEN 'ukentlig' THEN interval '6 hours'
                                      ELSE interval '24 hours'
                         END AS vindu_slutt
          FROM kandidat k
          CROSS JOIN LATERAL (
              SELECT CASE
                  WHEN k.rytme = 'daglig' THEN
                      CASE WHEN k.dagens <= now() THEN k.dagens
                           ELSE k.dagens - interval '1 day' END
                  WHEN k.rytme = 'ukentlig' THEN (
                      SELECT d FROM (
                          SELECT (date_trunc('day',
                                    (now() AT TIME ZONE k.tidssone)
                                    - make_interval(days => o))
                                  + make_interval(hours => k.time_lokal))
                                   AT TIME ZONE k.tidssone AS d,
                                 extract(isodow FROM
                                    (now() AT TIME ZONE k.tidssone)
                                    - make_interval(days => o))::int AS dow
                            FROM generate_series(0, 7) o) x
                       WHERE x.dow = k.ukedag AND x.d <= now()
                       ORDER BY x.d DESC LIMIT 1)
                  ELSE (
                      SELECT d FROM (
                          SELECT (date_trunc('day',
                                    (now() AT TIME ZONE k.tidssone)
                                    - make_interval(days => o))
                                  + make_interval(hours => k.time_lokal))
                                   AT TIME ZONE k.tidssone AS d,
                                 extract(day FROM
                                    (now() AT TIME ZONE k.tidssone)
                                    - make_interval(days => o))::int AS dom
                            FROM generate_series(0, 31) o) x
                       WHERE x.dom = k.manedsdag AND x.d <= now()
                       ORDER BY x.d DESC LIMIT 1)
              END AS start) v
         WHERE v.start IS NOT NULL
    ), kvalifisert AS (
        SELECT w.* FROM vindu w
         WHERE w.forfall <= now()
           AND now() < w.vindu_slutt
           -- Kvalifiseringsregelen (§4): FORFALLET i en aktiv periode.
           AND EXISTS (SELECT 1 FROM public.bestillingsplan_aktiv_periode pr
                        WHERE pr.plan_id = w.plan_id
                          AND pr.fra_ts <= w.forfall
                          AND (pr.til_ts IS NULL OR pr.til_ts > w.forfall))
           -- Alt terminalisert/tick-et vindu plukkes aldri om igjen.
           AND NOT EXISTS (SELECT 1 FROM public.bestillingsplan_vindu bv
                            WHERE bv.plan_id = w.plan_id
                              AND bv.vindu_start = w.vindu_start
                              AND bv.tilstand = 'terminal')
         ORDER BY w.forfall
         LIMIT greatest(p_maks, 0)
    ), materialisert AS (
        INSERT INTO public.bestillingsplan_vindu
            (plan_id, tenant, vindu_start, vindu_slutt)
        SELECT q.plan_id, q.tenant, q.vindu_start, q.vindu_slutt
          FROM kvalifisert q
        ON CONFLICT (plan_id, vindu_start) DO NOTHING
        RETURNING public.bestillingsplan_vindu.plan_id
    )
    SELECT q.plan_id, q.tenant, q.vindu_start, q.vindu_slutt, q.forfall,
           q.bestillingstype, q.parametre
      FROM kvalifisert q;
END $$;

-- Claim: mutexovergangen. Leasen beskytter forsøket (2 × HTTP-timeout);
-- låsen holdes ALDRI over HTTP-kallet.
CREATE OR REPLACE FUNCTION claim_planvindu(
    p_tenant TEXT, p_plan UUID, p_vindu TIMESTAMPTZ, p_lease_s INT)
RETURNS TABLE(utfall TEXT, claim_id UUID)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v RECORD; v_claim UUID; v_status TEXT; v_forfall TIMESTAMPTZ;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'claim_planvindu');
    SELECT * INTO v FROM public.bestillingsplan_vindu w
     WHERE w.plan_id = p_plan AND w.vindu_start = p_vindu FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'ukjent'::text, NULL::uuid; RETURN;
    END IF;
    IF v.tilstand = 'terminal' THEN
        -- AVBRYT — ingen POST. Terminal er absorberende.
        RETURN QUERY SELECT 'terminal'::text, NULL::uuid; RETURN;
    END IF;
    -- UTLØPET SJEKKES HER, IKKE BARE I PLUKKET (Codex P1). Plukket
    -- returnerer en BATCH som arbeides ned sekvensielt, og hver
    -- bestilling er et HTTP-kall: en rad som var innenfor vinduet da
    -- batchen ble valgt, kan være minutter utenfor når turen kommer til
    -- den. Uten dette leddet ble et misset vindu til en INNHENTING —
    -- stikk i strid med §5s aldri-ta-igjen. Kontrollen må skje atomisk
    -- med selve claimet: alt annet er et tidsvindu mellom sjekk og bruk.
    -- Vinduet står `ledig` og klassifisereren feller `hoppet_over`.
    IF now() >= v.vindu_slutt THEN
        RETURN QUERY SELECT 'utlopt'::text, NULL::uuid; RETURN;
    END IF;
    IF v.tilstand = 'aktivt' AND v.lease_utloper > now() THEN
        RETURN QUERY SELECT 'aktivt'::text, NULL::uuid; RETURN;
    END IF;
    -- PLANENS TILSTAND REVALIDERES HER OGSÅ (Codex P1). Plukket
    -- kvalifiserte batchen i sin egen, committede transaksjon; radene
    -- arbeides ned sekvensielt med et HTTP-kall hver. Pauser eller stanser
    -- en administrator planen i mellomtiden, var det bare vindusraden som
    -- sto imot — og en STANSET plan kunne fortsatt konsumere en kvoteplass
    -- og starte en ekstern skanning. En stans er en menneskelig ordre om at
    -- planen ikke skal bestille mer; da må den gjelde fra det øyeblikket
    -- den committes, ikke fra neste sveip.
    --
    -- Planraden låses FOR SHARE, ikke bare leses: `pause_plan`,
    -- `stans_plan` og `gjenoppta_plan` tar alle FOR UPDATE på nettopp den
    -- raden først. Låsen er det som gjør revalideringen ATOMISK med
    -- claimet — uten den ville en pause som committer mellom lesningen og
    -- UPDATE-en under sluppet forbi. Låserekkefølgen er vindu → plan
    -- overalt her; ingen vei går motsatt vei.
    --
    -- Regelen er plukkets egen, ikke en ny: FORFALLET skal ligge i en
    -- aktiv periode. Et gjenopptak ETTER forfallet åpner en ny periode som
    -- ikke dekker dette vinduet — planen kjører igjen, men tar ikke igjen
    -- det den var pauset gjennom (§5).
    SELECT b.status INTO v_status FROM public.bestillingsplan b
     WHERE b.plan_id = p_plan AND b.tenant = p_tenant FOR SHARE;
    v_forfall := p_vindu + public.plan_forfallsminutt(p_plan)
                           * interval '1 minute';
    IF v_status IS DISTINCT FROM 'aktiv'
       OR NOT EXISTS (SELECT 1 FROM public.bestillingsplan_aktiv_periode pr
                       WHERE pr.plan_id = p_plan
                         AND pr.fra_ts <= v_forfall
                         AND (pr.til_ts IS NULL OR pr.til_ts > v_forfall))
    THEN
        -- Vinduet står `ledig`; klassifisereren feller `hoppet_over` når
        -- det utløper. Ingen tick her — intet forsøk ble gjort.
        RETURN QUERY SELECT 'ikke_aktiv'::text, NULL::uuid; RETURN;
    END IF;
    v_claim := gen_random_uuid();
    UPDATE public.bestillingsplan_vindu w
       SET tilstand = 'aktivt', claim_id = v_claim,
           lease_utloper = now() + make_interval(secs =>
               least(greatest(p_lease_s, 30), 600))
     WHERE w.plan_id = p_plan AND w.vindu_start = p_vindu;
    RETURN QUERY SELECT 'claimet'::text, v_claim;
END $$;

-- Frigivelse: motstykket til claimet for et FORBIGÅENDE avbrudd (Codex
-- P1). Et driftsuhell i bestillingsveien er ingen dom over planen, og
-- vinduet skal derfor stå åpent — men å bare la leasen løpe ut ville
-- kostet inntil to minutter av et vindu som kanskje har sekunder igjen.
-- Fencing som i terminaliseringen: kun claimets eier kan frigi, og
-- terminal er absorberende (en frigivelse etter terminalisering ville
-- gjenåpnet et vindu evidensen alt har lukket).
CREATE OR REPLACE FUNCTION frigi_planvindu(
    p_tenant TEXT, p_plan UUID, p_vindu TIMESTAMPTZ, p_claim UUID)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'frigi_planvindu');
    SELECT * INTO v FROM public.bestillingsplan_vindu w
     WHERE w.plan_id = p_plan AND w.vindu_start = p_vindu FOR UPDATE;
    IF NOT FOUND THEN
        RETURN 'ukjent';
    END IF;
    IF v.tilstand = 'terminal' THEN
        RETURN 'terminal';
    END IF;
    IF v.claim_id IS DISTINCT FROM p_claim THEN
        RETURN 'ikke_ditt';
    END IF;
    UPDATE public.bestillingsplan_vindu w
       SET tilstand = 'ledig', claim_id = NULL, lease_utloper = NULL
     WHERE w.plan_id = p_plan AND w.vindu_start = p_vindu;
    RETURN 'frigitt';
END $$;

-- Terminalisering + tick i ÉN transaksjon — eneste tick-skriver.
-- `hoppet_over` krever utløpt vindu OG intet idempotenstreff: finnes en
-- rad i bestilling_idempotens på vinduets nøkkel, BLE det bestilt, og
-- utfallet skal hentes derfra — aldri hoppet_over (§5). Kontrollen bor
-- her fordi CHECK ikke kan lese now() eller andre tabeller.
CREATE OR REPLACE FUNCTION terminaliser_planvindu(
    p_tenant TEXT, p_plan UUID, p_vindu TIMESTAMPTZ, p_claim UUID,
    p_nokkel TEXT, p_utfall TEXT, p_oppdrag BIGINT, p_detalj JSONB)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v RECORD; v_eksisterende RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'terminaliser_planvindu');
    SELECT * INTO v FROM public.bestillingsplan_vindu w
     WHERE w.plan_id = p_plan AND w.vindu_start = p_vindu FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'terminaliser_planvindu: ukjent vindu'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v.tilstand = 'terminal' THEN
        -- Konflikt på tick er ikke suksess: eksisterende rad MÅ bære
        -- samme terminale utfall — avvik er en sikkerhetssak hos
        -- kalleren. Mutexen skal gjøre avviket umulig; kontrollen står
        -- fordi den beviser det.
        SELECT t.utfall INTO v_eksisterende
          FROM public.bestillingsplan_tick t
         WHERE t.plan_id = p_plan AND t.vindu_start = p_vindu;
        IF v_eksisterende.utfall IS DISTINCT FROM p_utfall THEN
            -- Avviket fører SIN EGEN sikkerhetshendelse her, atomisk med
            -- oppdagelsen: kalleren har ingen tabellrettigheter (port 7),
            -- og en hendelse kalleren kunne glemme var ingen hendelse.
            INSERT INTO public.bestillingsplan_hendelse
                (plan_id, tenant, hendelse, aktor, request_id, detalj)
            VALUES (p_plan, p_tenant, 'sikkerhetsavvik',
                    'terminaliser_planvindu', NULL,
                    jsonb_build_object('ventet', p_utfall,
                        'fant', coalesce(v_eksisterende.utfall,
                                         '<uten tick>')));
            RETURN 'avvik:' || coalesce(v_eksisterende.utfall, '<uten tick>');
        END IF;
        RETURN 'idempotent';
    END IF;
    IF p_utfall = 'hoppet_over' THEN
        IF now() < v.vindu_slutt THEN
            RAISE EXCEPTION 'terminaliser_planvindu: hoppet_over før '
                'vindu_slutt' USING ERRCODE = 'invalid_parameter_value';
        END IF;
        IF v.tilstand = 'aktivt' AND v.lease_utloper > now() THEN
            RAISE EXCEPTION 'terminaliser_planvindu: et levende forsøk '
                'eier vinduet' USING ERRCODE = 'invalid_parameter_value';
        END IF;
        IF EXISTS (SELECT 1 FROM public.bestilling_idempotens bi
                    WHERE bi.tenant = p_tenant
                      AND bi.idempotensnokkel = p_nokkel) THEN
            RAISE EXCEPTION 'terminaliser_planvindu: det finnes en '
                'bestilling på vinduets nøkkel — utfallet skal hentes '
                'derfra, aldri hoppet_over'
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
    ELSIF v.tilstand = 'aktivt' AND v.claim_id IS DISTINCT FROM p_claim THEN
        -- Materialiserings-utfall krever claimet — fencing på forsøket.
        RAISE EXCEPTION 'terminaliser_planvindu: claimet er ikke ditt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.bestillingsplan_vindu w
       SET tilstand = 'terminal', terminalisert_ts = now()
     WHERE w.plan_id = p_plan AND w.vindu_start = p_vindu;
    INSERT INTO public.bestillingsplan_tick
        (plan_id, tenant, vindu_start, idempotensnokkel, utfall,
         oppdrag_id, detalj)
    VALUES (p_plan, p_tenant, p_vindu, p_nokkel, p_utfall, p_oppdrag,
            p_detalj);
    RETURN 'terminalisert';
END $$;

-- Aggregert nedetidshendelse (§5): lengre nedetid gir ÉN rad, ikke tusen.
-- `til` er ikke pynt: neste kandidatsøk leser den som «dekket hit», og
-- det er nettopp den lesingen som gjør at ett avbrudd gir ÉN hendelse og
-- ikke én per sveip. `avkortet` sier om avbruddet er eldre enn
-- enumereringstaket — en avkorting skal SES, aldri antas.
CREATE OR REPLACE FUNCTION plan_nedetid_aggregert(
    p_tenant TEXT, p_plan UUID, p_fra TIMESTAMPTZ, p_til TIMESTAMPTZ,
    p_antall INT, p_aktor TEXT, p_request_id TEXT,
    p_avkortet BOOLEAN DEFAULT false)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'plan_nedetid_aggregert');
    INSERT INTO public.bestillingsplan_hendelse
        (plan_id, tenant, hendelse, aktor, request_id, detalj)
    VALUES (p_plan, p_tenant, 'nedetid_aggregert', p_aktor, p_request_id,
            jsonb_build_object('fra', p_fra, 'til', p_til,
                               'vinduer', p_antall,
                               'avkortet', coalesce(p_avkortet, false)));
END $$;

-- Lesefunksjoner for API-et (runtime har ingen bordtilgang).
--
-- plpgsql, ikke sql: porten skal AVVISE, ikke returnere tomt. Et ekstra
-- WHERE-ledd mot GUC-en ville gjort et kryss-tenant-forsøk til en tom
-- liste — som ser ut som «ingen planer», ikke som et avvist kall — og
-- lesingene her er nettopp de som lekker mest hvis porten mangler.
CREATE OR REPLACE FUNCTION hent_planer(p_tenant TEXT)
RETURNS SETOF public.bestillingsplan
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'hent_planer');
    RETURN QUERY
    SELECT * FROM public.bestillingsplan p
     WHERE p.tenant = p_tenant ORDER BY p.opprettet DESC;
END $$;

-- Historikken viser den SENERE kanselleringen uten å røre evidensen
-- (Codex P2). Ticket er immutabelt og forblir `tillat` — det ER hva
-- motoren svarte da vinduet ble terminalisert. Men et oppdrag som siden
-- ble avvist av et menneske sto fortsatt som «Bestilt» i flaten, mens
-- pausesveipen oppdaget nøyaktig den kanselleringen og UI-et hadde en
-- ferdig etikett for den. Diskriminatoren AVLEDES derfor i lesingen:
-- `utfall` er revisjonssporet, `vist_utfall` er hva som gjelder nå.
CREATE OR REPLACE FUNCTION hent_plan_tick(
    p_tenant TEXT, p_plan UUID, p_grense INT)
RETURNS TABLE(plan_id UUID, tenant TEXT, vindu_start TIMESTAMPTZ,
              idempotensnokkel TEXT, utfall TEXT, oppdrag_id BIGINT,
              detalj JSONB, registrert TIMESTAMPTZ, vist_utfall TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
#variable_conflict use_column
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'hent_plan_tick');
    RETURN QUERY
    SELECT t.plan_id, t.tenant, t.vindu_start, t.idempotensnokkel,
           t.utfall, t.oppdrag_id, t.detalj, t.registrert,
           CASE WHEN t.utfall = 'tillat' AND o.status = 'kansellert'
                     AND o.kansellert_aarsak = 'menneskelig_avvis'
                THEN 'avvist_av_menneske' ELSE t.utfall END
      FROM public.bestillingsplan_tick t
      LEFT JOIN public.oppdrag o
        ON o.tenant = t.tenant AND o.id = t.oppdrag_id
     WHERE t.tenant = p_tenant AND t.plan_id = p_plan
     ORDER BY t.vindu_start DESC
     LIMIT greatest(least(coalesce(p_grense, 50), 200), 1);
END $$;

CREATE OR REPLACE FUNCTION hent_plan_hendelser(
    p_tenant TEXT, p_plan UUID, p_grense INT)
RETURNS SETOF public.bestillingsplan_hendelse
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'hent_plan_hendelser');
    RETURN QUERY
    SELECT h.* FROM public.bestillingsplan_hendelse h
     WHERE h.tenant = p_tenant AND h.plan_id = p_plan
     ORDER BY h.id DESC
     LIMIT greatest(least(coalesce(p_grense, 50), 200), 1);
END $$;

-- Materialiserer-plukkets tilstandslesing for pausereglene: aktive
-- planers siste oppdrag (via tick) som er kansellert av menneske, og
-- tellinger innenfor gjeldende åpne periode.
-- Dempingen er hendelsessporet, ikke tiden (Codex P1): predikatet så
-- ETHVERT historisk `tillat`-tick med et menneskelig kansellert oppdrag,
-- for alltid. Etter at en slik avvisning hadde pauset planen, kunne en
-- administrator gjenoppta — og neste sveip fant det samme immutable
-- oppdraget og pauset umiddelbart igjen. `gjenoppta_plan` var da
-- virkningsløs nettopp for den grunnen den oftest brukes mot.
--
-- Hver avvisning skal pause ÉN gang: kandidaten faller bort idet en
-- `pauset`-hendelse med grunn `menneskelig_avvis` bærer nettopp dette
-- oppdraget. Samme idempotensgrep som `planer_med_gjentatt_brudd`, og
-- ikke en periodegrense: en kansellering som kommer ETTER et gjenopptak
-- gjelder fortsatt det oppdraget planen bestilte, og skal fortsatt tas.
CREATE OR REPLACE FUNCTION planer_med_menneskelig_avvis()
RETURNS TABLE(plan_id UUID, tenant TEXT, oppdrag_id BIGINT)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT DISTINCT t.plan_id, t.tenant, t.oppdrag_id
      FROM public.bestillingsplan_tick t
      JOIN public.bestillingsplan p
        ON p.plan_id = t.plan_id AND p.status = 'aktiv'
      JOIN public.oppdrag o
        ON o.tenant = t.tenant AND o.id = t.oppdrag_id
     WHERE t.utfall = 'tillat'
       AND o.status = 'kansellert'
       AND o.kansellert_aarsak = 'menneskelig_avvis'
       AND NOT EXISTS (
           SELECT 1 FROM public.bestillingsplan_hendelse h
            WHERE h.plan_id = t.plan_id
              AND h.hendelse = 'pauset'
              AND h.detalj->>'aarsak' = 'menneskelig_avvis'
              AND h.detalj->>'oppdrag_id' = t.oppdrag_id::text)
$$;


-- Utløpte vinduer som ALDRI fikk et forsøk (nedetid): enumerer
-- rytmetreffene i tilbakeblikket, materialiser radene, og la
-- klassifisereren felle `hoppet_over`-dommen. Aldri før planens
-- tidligste fra_ts, aldri lenger tilbake enn p_dager (port 35: eldre
-- nedetid aggregeres i stedet).
CREATE OR REPLACE FUNCTION utlopte_planvinduer(p_dager INT, p_maks INT)
RETURNS TABLE(plan_id UUID, tenant TEXT, vindu_start TIMESTAMPTZ,
              vindu_slutt TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
#variable_conflict use_column
-- (RETURNS TABLE-navnene kolliderer ellers med ON CONFLICT-kolonnene)
BEGIN
    RETURN QUERY
    WITH planer AS (
        SELECT p.plan_id, p.tenant, p.rytme, p.ukedag, p.manedsdag,
               p.time_lokal, p.tidssone,
               (SELECT min(ap.fra_ts)
                  FROM public.bestillingsplan_aktiv_periode ap
                 WHERE ap.plan_id = p.plan_id) AS forste_fra
          FROM public.bestillingsplan p
         WHERE EXISTS (SELECT 1 FROM public.bestillingsplan_aktiv_periode a
                        WHERE a.plan_id = p.plan_id)
    ), treff AS (
        SELECT pl.plan_id, pl.tenant,
               (date_trunc('day', (now() AT TIME ZONE pl.tidssone)
                                  - make_interval(days => d.o))
                + make_interval(hours => pl.time_lokal))
                 AT TIME ZONE pl.tidssone AS start,
               pl.plan_id AS pid
          FROM planer pl
          CROSS JOIN LATERAL generate_series(0,
               least(greatest(p_dager, 0), 31)) AS d(o)
         WHERE pl.forste_fra IS NOT NULL
           AND (   (pl.rytme = 'daglig')
                OR (pl.rytme = 'ukentlig' AND extract(isodow FROM
                       (now() AT TIME ZONE pl.tidssone)
                       - make_interval(days => d.o))::int = pl.ukedag)
                OR (pl.rytme = 'manedlig' AND extract(day FROM
                       (now() AT TIME ZONE pl.tidssone)
                       - make_interval(days => d.o))::int = pl.manedsdag))
    ), vindu AS (
        SELECT t.plan_id, t.tenant, t.start AS v_start,
               t.start + public.plan_forfallsminutt(t.plan_id)
                       * interval '1 minute'
                       + CASE p.rytme WHEN 'daglig'   THEN interval '2 hours'
                                      WHEN 'ukentlig' THEN interval '6 hours'
                                      ELSE interval '24 hours' END AS v_slutt,
               t.start + public.plan_forfallsminutt(t.plan_id)
                       * interval '1 minute' AS forfall
          FROM treff t
          JOIN public.bestillingsplan p ON p.plan_id = t.plan_id
    ), kvalifisert AS (
        SELECT w.plan_id, w.tenant, w.v_start, w.v_slutt FROM vindu w
         WHERE now() >= w.v_slutt
           AND EXISTS (SELECT 1 FROM public.bestillingsplan_aktiv_periode pr
                        WHERE pr.plan_id = w.plan_id
                          AND pr.fra_ts <= w.forfall
                          AND (pr.til_ts IS NULL OR pr.til_ts > w.forfall))
           AND NOT EXISTS (SELECT 1 FROM public.bestillingsplan_vindu bv
                            WHERE bv.plan_id = w.plan_id
                              AND bv.vindu_start = w.v_start)
         ORDER BY w.v_slutt
         LIMIT greatest(p_maks, 0)
    ), materialisert AS (
        INSERT INTO public.bestillingsplan_vindu
            (plan_id, tenant, vindu_start, vindu_slutt)
        SELECT q.plan_id, q.tenant, q.v_start, q.v_slutt FROM kvalifisert q
        ON CONFLICT (plan_id, vindu_start) DO NOTHING
        RETURNING public.bestillingsplan_vindu.plan_id
    )
    SELECT q.plan_id, q.tenant, q.v_start, q.v_slutt FROM kvalifisert q;
END $$;
REVOKE ALL ON FUNCTION utlopte_planvinduer(INT, INT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION utlopte_planvinduer(INT, INT) TO disponit;


-- Pausesveipets tilbakeblikk: tre `tillat`-tick på rad i GJELDENDE åpne
-- periode uten promotert artefakt. Definer-funksjon fordi kalleren
-- (runtime) ikke har bordtilgang, og fordi artefakt-lesingen hører til
-- claimeren her — samme snitt som resten av M-37-flaten.
-- «Tre på rad» må måles på de tre SISTE tickene, ikke på de tre siste
-- VELLYKKEDE (Codex P2). Filteret `utfall = 'tillat'` sto FØR `LIMIT 3`,
-- så rekkefølgen `tillat, brudd, tillat, tillat` ble lest som tre
-- sammenhengende `tillat` og pauset planen — enda `brudd`-et imellom
-- nettopp BRYTER stripen. Samme vindusform som
-- `planer_med_gjentatt_brudd`: ta de tre siste, og krev at alle tre
-- holder. Uten resultat = ingen promotert artefakt på tickets oppdrag.
CREATE OR REPLACE FUNCTION planer_gjentatt_uten_resultat()
RETURNS TABLE(plan_id UUID, tenant TEXT)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    WITH siste AS (
        SELECT t.plan_id, t.tenant, t.utfall,
               EXISTS (SELECT 1 FROM public.artefakt a
                        WHERE a.tenant = t.tenant
                          AND a.oppdrag_id = t.oppdrag_id
                          AND a.tilstand = 'promotert') AS har_resultat,
               row_number() OVER (PARTITION BY t.plan_id
                                  ORDER BY t.vindu_start DESC) AS rn
          FROM public.bestillingsplan_tick t
          JOIN public.bestillingsplan p
            ON p.plan_id = t.plan_id AND p.status = 'aktiv'
         WHERE t.registrert >= (SELECT max(ap.fra_ts)
                  FROM public.bestillingsplan_aktiv_periode ap
                 WHERE ap.plan_id = t.plan_id)
    )
    SELECT s.plan_id, s.tenant FROM siste s
     WHERE s.rn <= 3
     GROUP BY s.plan_id, s.tenant
    HAVING count(*) = 3
       AND bool_and(s.utfall = 'tillat')
       AND bool_and(NOT s.har_resultat)
$$;
REVOKE ALL ON FUNCTION planer_gjentatt_uten_resultat() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION planer_gjentatt_uten_resultat() TO disponit;

-- §7: `brudd` pauser ALDRI — kvoten er ikke brukt og vinduet åpner igjen
-- — men TRE brudd på rad skal varsles. Kandidaten: de tre SISTE tickene
-- i gjeldende åpne periode er alle `brudd`. Dempingen er hendelsen selv:
-- ett `varslet`-spor (grunn=gjentatt_brudd) etter stripens første tick
-- demper gjentak til stripen brytes av et annet utfall.
CREATE OR REPLACE FUNCTION planer_med_gjentatt_brudd()
RETURNS TABLE(plan_id UUID, tenant TEXT)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    WITH siste AS (
        SELECT t.plan_id, t.tenant, t.utfall, t.registrert,
               row_number() OVER (PARTITION BY t.plan_id
                                  ORDER BY t.vindu_start DESC) AS rn
          FROM public.bestillingsplan_tick t
          JOIN public.bestillingsplan p
            ON p.plan_id = t.plan_id AND p.status = 'aktiv'
         WHERE t.registrert >= (SELECT max(ap.fra_ts)
                  FROM public.bestillingsplan_aktiv_periode ap
                 WHERE ap.plan_id = t.plan_id)
    ), striper AS (
        SELECT s.plan_id, s.tenant, min(s.registrert) AS stripe_fra
          FROM siste s WHERE s.rn <= 3
         GROUP BY s.plan_id, s.tenant
        HAVING count(*) = 3 AND bool_and(s.utfall = 'brudd')
    )
    SELECT st.plan_id, st.tenant FROM striper st
     WHERE NOT EXISTS (
        SELECT 1 FROM public.bestillingsplan_hendelse h
         WHERE h.plan_id = st.plan_id AND h.hendelse = 'varslet'
           AND h.detalj->>'grunn' = 'gjentatt_brudd'
           AND h.ts >= st.stripe_fra)
$$;

-- Selve varselet, per plan, med kallerens tenantkontekst satt (samme
-- disiplin som pause_plan): varsel + dempings-hendelse i ÉN transaksjon,
-- idempotent via kandidatfunksjonen over. Returnerer om det ble varslet.
CREATE OR REPLACE FUNCTION varsle_plan_brudd(
    p_tenant TEXT, p_plan UUID, p_aktor TEXT, p_request_id TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_aktivert_av TEXT; v_bruker TEXT; v_hendelse BIGINT;
        v_varslet BOOLEAN := false;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'varsle_plan_brudd');
    IF NOT EXISTS (SELECT 1 FROM public.planer_med_gjentatt_brudd() k
                    WHERE k.plan_id = p_plan AND k.tenant = p_tenant) THEN
        RETURN false;
    END IF;
    SELECT b.aktivert_av INTO v_aktivert_av FROM public.bestillingsplan b
     WHERE b.plan_id = p_plan AND b.tenant = p_tenant FOR UPDATE;
    -- Aktørstreng → bruker-id, som i pause_plan (FK mot brukeridentitet).
    v_bruker := CASE WHEN v_aktivert_av LIKE 'bruker:%'
                     THEN substring(v_aktivert_av FROM 8) END;
    -- Dempings-hendelsen skrives UANSETT, og FØRST: uten den ville en
    -- plan uten varslingsmottaker blitt kandidat i hvert sveip for
    -- alltid — og skrev vi den etter varselet, ville en feilet
    -- varselinnsetting rullet den bort igjen. Id-en er dessuten
    -- FOREKOMSTEN varselnøkkelen trenger under.
    INSERT INTO public.bestillingsplan_hendelse
        (plan_id, tenant, hendelse, aktor, request_id, detalj)
    VALUES (p_plan, p_tenant, 'varslet', p_aktor, p_request_id,
            jsonb_build_object('grunn', 'gjentatt_brudd',
                               'bruker', v_bruker))
    RETURNING id INTO v_hendelse;
    IF v_bruker IS NOT NULL THEN
        BEGIN
            -- `hendelse` er FOREKOMSTEN, ikke arten (026s egen begrunnelse
            -- for kolonnen, og 041 §15-lærdommen). Med den konstante
            -- literalen 'varslet' var nøkkelen (tenant, bruker,
            -- 'plan_gjentatt_brudd', 'plan', plan_id, 'varslet') den
            -- SAMME for hver eneste bruddstripe på planen: stripe nummer
            -- to — korrekt gjenåpnet av et mellomliggende utfall — traff
            -- `varsel_en_per_hendelse`, og siden dette ikke var fanget,
            -- aborterte HELE sveiptransaksjonen. Dempings-hendelsen ble
            -- rullet bort med den, så neste sveip feilet likt, for alltid.
            -- Hendelses-id-en er global og monoton, og den ER stripen.
            INSERT INTO public.varsel (tenant, bruker_id, art, ressurs_type,
                ressurs_id, hendelse, tekstnokkel, parametre)
            VALUES (p_tenant, v_bruker, 'plan_gjentatt_brudd', 'plan',
                    p_plan::text, 'gjentatt_brudd:' || v_hendelse,
                    'varsel.plan_gjentatt_brudd',
                    jsonb_build_object('antall', 3));
            v_varslet := true;
        EXCEPTION WHEN OTHERS THEN
            -- Varselet er ikke evidens; dempingen står (samme kontrakt som
            -- pause_plan og 041 port 41). WARNING, ikke stillhet: en
            -- varselvei som ryker skal SES i driftsloggen.
            RAISE WARNING 'varsle_plan_brudd: varsel feilet for %: %',
                p_plan, SQLERRM;
        END;
    END IF;
    RETURN v_varslet;
END $$;

-- Klassifisererens leseveier (port 7 gjelder også den: runtime har
-- ingen bordtilgang, så utvalget går gjennom claimerens definere).
CREATE OR REPLACE FUNCTION planvinduer_til_klassifisering(
    p_dager INT, p_grense INT)
RETURNS TABLE(plan_id UUID, tenant TEXT, vindu_start TIMESTAMPTZ,
              tilstand TEXT, lease_utloper TIMESTAMPTZ)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT w.plan_id, w.tenant, w.vindu_start, w.tilstand, w.lease_utloper
      FROM public.bestillingsplan_vindu w
     WHERE w.tilstand <> 'terminal'
       AND now() >= w.vindu_slutt
       AND w.vindu_start >= now() - make_interval(days =>
                                                  greatest(p_dager, 0))
     ORDER BY w.vindu_slutt
     LIMIT greatest(p_grense, 0)
$$;

-- Nedetidsaggregatet leses fra RYTMEN, ikke bare fra radene (Codex P2).
--
-- En vindusrad fødes først når noen har noe å gjøre med den, og
-- `utlopte_planvinduer` materialiserer bare tilbakeblikket (30 døgn). Et
-- 60-døgns avbrudd etterlot derfor INGEN rad for døgn 60→30: aggregatet,
-- som bare grupperte eksisterende rader, meldte enten ingenting eller kun
-- grenseraden. Løftet i §5 er ÉN hendelse som forteller SANT om
-- avbruddet; da må den telle forekomstene planen skulle hatt.
--
-- Søket går over HELE planens levetid innenfor taket, ikke fra siste
-- vindusrad: klassifisereren materialiserer selv tilbakeblikkets 30 døgn
-- rett før dette kallet, så «nyeste rad før tilbakeblikket» ligger alltid
-- like ved grensen og ville skjult hullet bak seg (målt i CI: et
-- 90-døgns avbrudd ble til ÉN savnet forekomst). Anti-joinen mot
-- vindusradene er det som avgjør hva som mangler — en frisk plan har en
-- rad for hver forekomst og faller ut med tomt resultat.
--
-- Nedre grense er det seneste av planens tidligste `fra_ts`, `til` fra
-- forrige aggregerte nedetidshendelse, og takets grense. Leddet om
-- forrige hendelse er DEMPINGEN: uten den ville en plan uten rader fått
-- en ny hendelse i HVERT sveip — nettopp tusen hendelser i stedet for én.
-- Etter første aggregering er søkeintervallet derfor kort igjen.
--
-- Kostnaden er bevisst: inntil `p_maks_dogn` genererte rader per plan,
-- hver med ett primærnøkkeloppslag. Det er en batchjobb hvert femte
-- minutt over PLANER (kunderader), ikke over hendelser, og taket er
-- parameteren en operatør kan senke.
--
-- `vinduer` er fortsatt BARE radene som finnes: kun de kan termineres
-- (FK-en krever en vindusrad). `fra`, `til` og `antall` dekker hele
-- avbruddet, materialisert eller ei.
--
-- Avkortingen er eksplisitt, aldri stille: er avbruddet eldre enn
-- `p_maks_dogn`, sier `avkortet` det, og hendelsen bærer flagget videre.
-- Et ubundet generate_series per plan er en spørring ingen tar igjen.
CREATE OR REPLACE FUNCTION plan_nedetid_kandidater(
    p_dager INT, p_maks_dogn INT)
RETURNS TABLE(plan_id UUID, tenant TEXT, fra TIMESTAMPTZ, til TIMESTAMPTZ,
              antall BIGINT, vinduer TIMESTAMPTZ[], avkortet BOOLEAN)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    WITH g AS (
        SELECT now() - make_interval(days => greatest(p_dager, 0))
                   AS eldre_enn,
               least(greatest(p_maks_dogn, 1), 3650) AS tak
    ), eksisterende AS (
        SELECT w.plan_id, w.tenant, min(w.vindu_start) AS fra,
               max(w.vindu_start) AS til, count(*) AS antall,
               array_agg(w.vindu_start ORDER BY w.vindu_start) AS vinduer
          FROM public.bestillingsplan_vindu w, g
         WHERE w.tilstand <> 'terminal' AND w.vindu_start < g.eldre_enn
         GROUP BY w.plan_id, w.tenant
    ), planer AS (
        SELECT p.plan_id, p.tenant, p.rytme, p.ukedag, p.manedsdag,
               p.time_lokal, p.tidssone, g.eldre_enn, g.tak,
               greatest(
                   (SELECT min(ap.fra_ts)
                      FROM public.bestillingsplan_aktiv_periode ap
                     WHERE ap.plan_id = p.plan_id),
                   coalesce((SELECT max((h.detalj->>'til')::timestamptz)
                               FROM public.bestillingsplan_hendelse h
                              WHERE h.plan_id = p.plan_id
                                AND h.hendelse = 'nedetid_aggregert'),
                            '-infinity'::timestamptz)) AS dekket_til
          FROM public.bestillingsplan p, g
         WHERE p.status <> 'stanset'
           AND EXISTS (SELECT 1 FROM public.bestillingsplan_aktiv_periode a
                        WHERE a.plan_id = p.plan_id)
    ), soek AS (
        SELECT pl.*,
               greatest(pl.dekket_til,
                        now() - make_interval(days => pl.tak)) AS fra_soek,
               pl.dekket_til < now() - make_interval(days => pl.tak)
                   AS avkortet
          FROM planer pl
    ), treff AS (
        SELECT s.plan_id, s.tenant, s.avkortet, s.fra_soek, s.eldre_enn,
               (date_trunc('day', (s.eldre_enn AT TIME ZONE s.tidssone)
                                  - make_interval(days => d.o))
                + make_interval(hours => s.time_lokal))
                 AT TIME ZONE s.tidssone AS start
          FROM soek s
          CROSS JOIN LATERAL generate_series(0, least(greatest(
                  ceil(extract(epoch FROM (s.eldre_enn - s.fra_soek))
                       / 86400)::int, 0) + 1, s.tak)) AS d(o)
         WHERE (   s.rytme = 'daglig'
                OR (s.rytme = 'ukentlig' AND extract(isodow FROM
                       (s.eldre_enn AT TIME ZONE s.tidssone)
                       - make_interval(days => d.o))::int = s.ukedag)
                OR (s.rytme = 'manedlig' AND extract(day FROM
                       (s.eldre_enn AT TIME ZONE s.tidssone)
                       - make_interval(days => d.o))::int = s.manedsdag))
    ), savnet AS (
        SELECT t.plan_id, t.tenant, t.avkortet, t.start
          FROM treff t
         WHERE t.start > t.fra_soek AND t.start < t.eldre_enn
           -- Samme kvalifiseringsregel som plukket: FORFALLET i en aktiv
           -- periode. Et vindu planen aldri var aktiv for er ikke nedetid.
           AND EXISTS (SELECT 1 FROM public.bestillingsplan_aktiv_periode pr
                        WHERE pr.plan_id = t.plan_id
                          AND pr.fra_ts <= t.start
                               + public.plan_forfallsminutt(t.plan_id)
                                 * interval '1 minute'
                          AND (pr.til_ts IS NULL OR pr.til_ts > t.start
                               + public.plan_forfallsminutt(t.plan_id)
                                 * interval '1 minute'))
           AND NOT EXISTS (SELECT 1 FROM public.bestillingsplan_vindu bv
                            WHERE bv.plan_id = t.plan_id
                              AND bv.vindu_start = t.start)
    ), manglende AS (
        SELECT sv.plan_id, sv.tenant, min(sv.start) AS fra,
               max(sv.start) AS til, count(*) AS antall,
               bool_or(sv.avkortet) AS avkortet
          FROM savnet sv GROUP BY sv.plan_id, sv.tenant
    )
    SELECT coalesce(e.plan_id, m.plan_id), coalesce(e.tenant, m.tenant),
           least(e.fra, m.fra), greatest(e.til, m.til),
           coalesce(e.antall, 0) + coalesce(m.antall, 0),
           coalesce(e.vinduer, ARRAY[]::TIMESTAMPTZ[]),
           coalesce(m.avkortet, false)
      FROM eksisterende e
      FULL JOIN manglende m ON m.plan_id = e.plan_id
$$;

REVOKE ALL ON FUNCTION planvinduer_til_klassifisering(INT, INT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION planvinduer_til_klassifisering(INT, INT)
    TO disponit;
REVOKE ALL ON FUNCTION plan_nedetid_kandidater(INT, INT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION plan_nedetid_kandidater(INT, INT) TO disponit;

-- Deploy-portens lesevei (port 1): runtime har ingen bordtilgang (port
-- 7), så tellingen per bestillingstype går gjennom claimerens definer —
-- samme mønster som resten av plukket.
CREATE OR REPLACE FUNCTION plan_bestillingstyper()
RETURNS TABLE(bestillingstype TEXT, antall BIGINT)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT p.bestillingstype, count(*) FROM public.bestillingsplan p
     WHERE p.status <> 'stanset' GROUP BY p.bestillingstype
$$;

REVOKE ALL ON FUNCTION plan_bestillingstyper() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION plan_bestillingstyper() TO disponit;

REVOKE ALL ON FUNCTION planer_med_gjentatt_brudd() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION planer_med_gjentatt_brudd() TO disponit;
REVOKE ALL ON FUNCTION varsle_plan_brudd(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION varsle_plan_brudd(TEXT, UUID, TEXT, TEXT)
    TO disponit;

REVOKE ALL ON FUNCTION opprett_plan(TEXT, TEXT, JSONB, TEXT, SMALLINT,
    SMALLINT, SMALLINT, TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION aktiver_plan(TEXT, UUID, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION pause_plan(TEXT, UUID, TEXT, TEXT, TEXT, JSONB)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION gjenoppta_plan(TEXT, UUID, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION stans_plan(TEXT, UUID, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION plan_forfallsminutt(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION forfalte_planvinduer(INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION claim_planvindu(TEXT, UUID, TIMESTAMPTZ, INT)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION frigi_planvindu(TEXT, UUID, TIMESTAMPTZ, UUID)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION terminaliser_planvindu(TEXT, UUID, TIMESTAMPTZ,
    UUID, TEXT, TEXT, BIGINT, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION plan_nedetid_aggregert(TEXT, UUID, TIMESTAMPTZ,
    TIMESTAMPTZ, INT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
REVOKE ALL ON FUNCTION hent_planer(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION hent_plan_tick(TEXT, UUID, INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION hent_plan_hendelser(TEXT, UUID, INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION planer_med_menneskelig_avvis() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION opprett_plan(TEXT, TEXT, JSONB, TEXT, SMALLINT,
    SMALLINT, SMALLINT, TEXT, TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION aktiver_plan(TEXT, UUID, TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION pause_plan(TEXT, UUID, TEXT, TEXT, TEXT, JSONB)
    TO disponit;
GRANT EXECUTE ON FUNCTION gjenoppta_plan(TEXT, UUID, TEXT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION stans_plan(TEXT, UUID, TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION plan_forfallsminutt(UUID) TO disponit;
GRANT EXECUTE ON FUNCTION forfalte_planvinduer(INT) TO disponit;
GRANT EXECUTE ON FUNCTION claim_planvindu(TEXT, UUID, TIMESTAMPTZ, INT)
    TO disponit;
GRANT EXECUTE ON FUNCTION frigi_planvindu(TEXT, UUID, TIMESTAMPTZ, UUID)
    TO disponit;
GRANT EXECUTE ON FUNCTION terminaliser_planvindu(TEXT, UUID, TIMESTAMPTZ,
    UUID, TEXT, TEXT, BIGINT, JSONB) TO disponit;
GRANT EXECUTE ON FUNCTION plan_nedetid_aggregert(TEXT, UUID, TIMESTAMPTZ,
    TIMESTAMPTZ, INT, TEXT, TEXT, BOOLEAN) TO disponit;
GRANT EXECUTE ON FUNCTION hent_planer(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION hent_plan_tick(TEXT, UUID, INT) TO disponit;
GRANT EXECUTE ON FUNCTION hent_plan_hendelser(TEXT, UUID, INT)
    TO disponit;
GRANT EXECUTE ON FUNCTION planer_med_menneskelig_avvis() TO disponit;

RESET ROLE;

-- Varselenumet utvides med plan-arten — splice av GJELDENDE definisjon,
-- samme grep som 041 §15.
DO $$
DECLARE def TEXT; c TEXT;
BEGIN
  SELECT conname, pg_get_constraintdef(oid) INTO c, def FROM pg_constraint
   WHERE conrelid = 'varsel'::regclass
     AND pg_get_constraintdef(oid) LIKE '%attestering_venter%';
  IF def IS NULL THEN
    RAISE EXCEPTION '044: fant ikke art-CHECKen på varsel';
  END IF;
  IF def NOT LIKE '%plan_pauset%' THEN
    def := replace(def, '''domeneovertakelse''::text',
                   '''domeneovertakelse''::text, ''plan_pauset''::text,'
                   || ' ''plan_gjentatt_brudd''::text');
    EXECUTE format('ALTER TABLE varsel DROP CONSTRAINT %I', c);
    EXECUTE format('ALTER TABLE varsel ADD CONSTRAINT %I %s', c, def);
  END IF;
  -- ... og ressurstypen.
  SELECT conname, pg_get_constraintdef(oid) INTO c, def FROM pg_constraint
   WHERE conrelid = 'varsel'::regclass
     AND pg_get_constraintdef(oid) LIKE '%ressurs_type%';
  IF def IS NOT NULL AND def NOT LIKE '%''plan''%' THEN
    def := replace(def, '''domene''::text',
                   '''domene''::text, ''plan''::text');
    EXECUTE format('ALTER TABLE varsel DROP CONSTRAINT %I', c);
    EXECUTE format('ALTER TABLE varsel ADD CONSTRAINT %I %s', c, def);
  END IF;
END $$;

-- Rollemønsteret i basen (043 §6b) speiler ROLLE_TIL_SCOPES eksakt —
-- planscopene føyes til admin her, ellers er port 26 rød.
INSERT INTO rolle_scope (rolle, scope) VALUES
    ('admin', 'plan:opprett'),
    ('admin', 'plan:aktiver'),
    ('admin', 'plan:gjenoppta')
ON CONFLICT DO NOTHING;

-- Claimeren trenger INSERT på varsel for pausevarselet (varsle_overtakelse-
-- presedensen ga domene_eier det samme i 041 §13).
GRANT INSERT ON varsel TO disponit_m37_claimer;
GRANT SELECT ON oppdrag TO disponit_m37_claimer;
-- «hoppet_over krever intet idempotenstreff» (§5) leses av
-- terminaliser_planvindu — fasittabellen er immutabel inkludert DELETE,
-- så lesetilgangen gir claimeren ingen omskrivingsmakt.
GRANT SELECT ON bestilling_idempotens TO disponit_m37_claimer;
-- Artefaktlesingen for gjentatt_uten_resultat: artefakt er
-- migrator-eid (016), så granten gis direkte — et SET ROLE-vindu her
-- ville vært en no-op-grant fra en ikke-eier.
GRANT SELECT ON artefakt TO disponit_m37_claimer;
