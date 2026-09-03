-- 113: M-39 lønnsgrunnlag v1 — GRUNNLAGET, IKKE LØNNSKJØRINGEN.
-- Fem tenant-skopede tabeller, fjorten dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- V1-AVGRENSNINGEN, ORDRETT FRA POLICYEN VI SENDER UT:
-- håndverk/bygg-malen navngir denne modulen som verifikatoren `v_lonn`,
-- betrodd for TRE vilkår — og bruker ALLE TRE til å slippe en handling
-- gjennom automatisk:
--
--     - id: timeliste.samle_og_valider
--       modul: M-39
--       modus: auto
--       dataklasser_tillatt: [persondata, finansiell]
--       vilkaar: [{navn: timer_mot_arbeidsplan, verifikator: v_lonn},
--                 {navn: prosjektkode_gyldig,   verifikator: v_lonn},
--                 {navn: overtid_flagget,       verifikator: v_lonn}]
--
-- v1 UTBETALER INGENTING OG PRODUSERER INGEN LØNNSFIL.
--
-- DOMMEN, OG DEN ER TODELT.
--
--   FØRSTE HALVDEL: en utbetaling er penger ut døra. Samme dom som
--   M-41s (111), og den trenger ingen ny begrunnelse.
--
--   ANDRE HALVDEL ER DEN SOM ER SÆREGEN HER, og den er skarpere enn
--   den ser ut: en LØNNSFIL er ikke en betaling — det er en fil. Den
--   ser harmløs ut, den kan «bare genereres», og den er nettopp derfor
--   farligere enn en enkelt utbetaling: den rammer ALLE på én gang, og
--   den rammer noen som har regnet med beløpet. En feil i en faktura
--   oppdages av en kunde som klager. En feil i en lønnsfil oppdages av
--   noen som ikke fikk husleia.
--
--   Å ta den fullmakten før noen har målt hvor ofte timegrunnlaget vårt
--   stemmer med arbeidsplanen, er å la modulen definere sin egen
--   troverdighet på ANDRES INNTEKT.
--
-- v1 GJØR ÉN TING: samler timegrunnlaget og MÅLER det mot
-- arbeidsplanen.
--
-- DOMMENE v1 HVILER PÅ, håndhevet i DATAMODELLEN:
--
--   1. TIMER ER HELE MINUTTER, `INT`. M-25s dom (107), og den gjelder
--      tyngre her: «7,5 time» som flyttall er 7.499999999999999 på
--      veien tilbake. En lønnskjøring som driver noen øre per rad
--      driver systematisk, i samme retning, hver måned, for alle.
--
--   2. LØNNSGRUNNLAGET OVERSKRIVES ALDRI. Hver timeregistrering er
--      FROSSET. En feilført time rettes med en NY rad, ikke ved å
--      endre den gamle — ellers sletter man sporet av at noe ble
--      rettet, og det er nettopp det sporet en lønnstvist står på.
--
--   3. EN TIME UTEN EN PLAN Å MÅLE MOT ER IKKE MÅLT. `timer_mot_
--      arbeidsplan` er et vilkår om en SAMMENLIGNING; uten en plan
--      finnes det ingen sammenligning, og et «ja» ville vært en
--      attestasjon om noe ingen gjorde. Derfor er «ingen plan» et
--      FUNN, ikke en stille null.
--
--   4. OVERTID ER ET FUNN, IKKE ET FLAGG. Det finnes ingen
--      `overtid BOOLEAN`-kolonne noen kan sette og gå videre fra.
--      Overtid UTLEDES av timene mot tenantens egen normaltid, og
--      havner i funnregisteret der noen må se på den. Et flagg modulen
--      setter selv er nøyaktig den attestasjonen `overtid_flagget`
--      ville hvilt på.
--
--   5. GRENSENE ER TENANTENS. Normaltid per dag og uke, avviksgrensen
--      og fristen for en plan ligger i basen, satt gjennom en dør. En
--      bedrift med 37,5-timers uke og en med rotasjonsturnus har ikke
--      samme normaltid, og en konstant i koden ville vært nøyaktig den
--      fullmakten `timegrense_hardkodet` forbyr.
--
-- GRENSEN MOT M-25: M-25 eier PROSJEKTETS forbruk — timen som kostnad
-- på et prosjekt. M-39 eier TIMEN SOM LØNNSGRUNNLAG — den samme timen
-- sett fra den ansattes side. De to registrene skal ikke speile
-- hverandre; de svarer på forskjellige spørsmål, og en felles tabell
-- ville tvunget det ene svaret til å være det andre. v1 kobler dem
-- ikke: `prosjektkode_gyldig` måles mot ARBEIDSPLANENE VÅRE EGNE, ikke
-- mot M-25s prosjektregister. Det er en ærligere måling, ikke en
-- svakere: den svarer på «jobbet hen på noe hen var satt opp på», som
-- er det spørsmålet en timeliste faktisk reiser.
--
-- SVEIPEN HAR SIN EGEN ROLLE OG SIN EGEN TIMER (095/100-112):
-- `disponit_lonnssveip` har nøyaktig ÉN rettighet — EXECUTE på
-- `m39_sveip_lonnsgrunnlag` — og INGEN tabellrettigheter. Sveipen
-- UTBETALER INGENTING og PRODUSERER INGEN FIL; den skriver FUNN.
--
-- FORMENE ER HUSETS: tabellene eies av migrator, dørene av NOLOGIN-rollen
-- `disponit_lonn_eier`, tenant TEXT + RLS ENABLE+FORCE +
-- `tenant_isolasjon`, SP-1 i hver tenantbundet definer, ingen BYPASSRLS.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_lonn_eier') THEN
        RAISE EXCEPTION 'rollen disponit_lonn_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_lonn_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `lonnsterskel` — ÉN per tenant. DOM 5: GRENSENE ER TENANTENS.
--
-- ALT I MINUTTER (dom 1). Ingen av feltene er timer, og ingen av dem
-- er flyttall.
CREATE TABLE lonnsterskel (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- Normal arbeidsdag. 450 minutter er 7,5 time — men det er
    -- STANDARDEN, ikke regelen: en bedrift med rotasjonsturnus setter
    -- sin egen.
    normaltid_minutter_dag INT NOT NULL DEFAULT 450
        CHECK (normaltid_minutter_dag BETWEEN 0 AND 1440),
    -- Normal arbeidsuke. 2250 minutter er 37,5 time.
    normaltid_minutter_uke INT NOT NULL DEFAULT 2250
        CHECK (normaltid_minutter_uke BETWEEN 0 AND 10080),
    -- Hvor mye en dags førte timer kan avvike fra planen før det er et
    -- funn. I MINUTTER, ikke prosent: fem minutter er fem minutter
    -- enten dagen er på seks timer eller på tolv.
    avvik_minutter INT NOT NULL DEFAULT 0
        CHECK (avvik_minutter BETWEEN 0 AND 1440),
    -- Hvor lenge en time kan stå uten en arbeidsplan å måles mot før
    -- det er et funn. DOM 3.
    uten_plan_dogn INT NOT NULL DEFAULT 7
        CHECK (uten_plan_dogn BETWEEN 0 AND 3650),
    -- HVOR LANGT TILBAKE FUNNENE VURDERES.
    --
    -- Dette er ikke en bekvemmelighet, det er en NØDVENDIGHET, og
    -- skillet er hvorvidt funnet har et BOTEMIDDEL:
    --
    --   `time_uten_arbeidsplan` KAN rettes — noen fører en plan, og
    --   dagen blir målt. Det funnet skal stå til noen gjør det, og
    --   vinduet gjelder derfor ikke det.
    --
    --   `overtid`, `avvik_mot_plan` og `ukjent_prosjektkode` kan IKKE
    --   rettes. Timeregistreringene er frosset; overtid som har
    --   skjedd, har skjedd. Uten et vindu ville de tre funnene aldri
    --   kunne lukkes — og innen et år ville hver aktiv ansatt hatt
    --   alle tre permanent åpne. Et funnregister som alltid sier ja
    --   sier ingenting.
    --
    -- VINDUET ER TENANTENS, som alle andre grenser her: hvor lenge en
    -- overtidsdag er verdt å se på er en forretningsbeslutning.
    vurderingsvindu_dogn INT NOT NULL DEFAULT 60
        CHECK (vurderingsvindu_dogn BETWEEN 1 AND 3650),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT lonnsterskel_pk PRIMARY KEY (tenant)
);

-- `lonnstaker` — den vi fører timer for.
CREATE TABLE lonnstaker (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    taker_id UUID NOT NULL,
    -- Tenantens egen referanse (ansattnummer). FRI TEKST og ingen
    -- fremmednøkkel: lønnshistorikken skal kunne stå alene.
    ekstern_ref TEXT NOT NULL CHECK (ekstern_ref ~ '[^[:space:]]'),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    aktiv BOOLEAN NOT NULL DEFAULT true,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT lonnstaker_pk PRIMARY KEY (tenant, taker_id),
    CONSTRAINT lonnstaker_ref_unik UNIQUE (tenant, ekstern_ref)
);
CREATE INDEX lonnstaker_aktive ON lonnstaker (tenant) WHERE aktiv;

-- `arbeidsplan` — DOM 3. DET TIMENE MÅLES MOT.
--
-- Versjonert, datert, uten overlapp — samme form som prisen i 108 og
-- abonnementsperioden i 111. «Hvilken plan gjaldt den dagen» skal ha
-- nøyaktig ett svar, for uten det er `timer_mot_arbeidsplan` ikke et
-- spørsmål man kan svare på i det hele tatt.
CREATE TABLE arbeidsplan (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    plan_id UUID NOT NULL,
    taker_id UUID NOT NULL,
    versjon INT NOT NULL CHECK (versjon >= 1),
    -- Planlagte minutter per arbeidsdag. MINUTTER (dom 1).
    planlagt_minutter_dag INT NOT NULL
        CHECK (planlagt_minutter_dag BETWEEN 0 AND 1440),
    -- Prosjektkoden planen gjelder. Det er DENNE `prosjektkode_gyldig`
    -- måles mot — ikke M-25s prosjektregister. Se grensen i toppen.
    prosjektkode TEXT NOT NULL CHECK (prosjektkode ~ '[^[:space:]]'),
    gyldig_fra DATE NOT NULL,
    gyldig_til DATE,
    -- En plan uten begrunnelse er ingen beslutning: den avgjør hva
    -- noens timer måles mot.
    begrunnelse TEXT NOT NULL CHECK (begrunnelse ~ '[^[:space:]]'),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT arbeidsplan_pk PRIMARY KEY (tenant, plan_id),
    CONSTRAINT arbeidsplan_taker_fk FOREIGN KEY (tenant, taker_id)
        REFERENCES lonnstaker (tenant, taker_id),
    CONSTRAINT arbeidsplan_versjon_unik
        UNIQUE (tenant, taker_id, versjon),
    CONSTRAINT arbeidsplan_periode_sunn
        CHECK (gyldig_til IS NULL OR gyldig_til > gyldig_fra)
);
CREATE INDEX arbeidsplan_oppslag
    ON arbeidsplan (tenant, taker_id, gyldig_fra DESC);

-- `timeregistrering` — DOM 1, 2 OG 4. HOVEDBOKEN FOR TIMER.
--
-- DET FINNES INGEN `overtid`-KOLONNE HER, og det er ikke en
-- forglemmelse (dom 4). Overtid UTLEDES av timene mot tenantens egen
-- normaltid, og havner i funnregisteret. Et flagg modulen satte selv
-- ville vært nøyaktig den attestasjonen `overtid_flagget` skal hvile
-- på — og den ville stått der som et faktum ingen hadde sett på.
CREATE TABLE timeregistrering (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    time_id UUID NOT NULL,
    taker_id UUID NOT NULL,
    dato DATE NOT NULL,
    -- MINUTTER, `INT`, aldri timer og aldri flyttall (dom 1).
    minutter INT NOT NULL CHECK (minutter BETWEEN 0 AND 1440),
    prosjektkode TEXT NOT NULL CHECK (prosjektkode ~ '[^[:space:]]'),
    -- Hvor timen kom fra. Et lukket sett: en time den ansatte førte
    -- selv og en leder korrigerte inn er ikke samme grunnlag.
    kilde TEXT NOT NULL
        CONSTRAINT timeregistrering_kilde_lukket CHECK (kilde IN (
            'fort_av_ansatt', 'fort_av_leder', 'import', 'korreksjon')),
    kilde_ref TEXT NOT NULL CHECK (kilde_ref ~ '[^[:space:]]'),
    notat TEXT NOT NULL CHECK (notat ~ '[^[:space:]]'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT timeregistrering_pk PRIMARY KEY (tenant, time_id),
    CONSTRAINT timeregistrering_taker_fk FOREIGN KEY (tenant, taker_id)
        REFERENCES lonnstaker (tenant, taker_id),
    -- SAMME KILDEHENDELSE REGISTRERES ÉN GANG. En timelisteimport som
    -- kjøres to ganger er ikke to arbeidsdager.
    CONSTRAINT timeregistrering_kilde_unik
        UNIQUE (tenant, taker_id, kilde, kilde_ref)
);
CREATE INDEX timeregistrering_oppslag
    ON timeregistrering (tenant, taker_id, dato DESC, registrert DESC);

-- `lonnsfunn` — funnene. Nøklet på takeren og typen (111/112s form).
CREATE TABLE lonnsfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    taker_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CONSTRAINT lonnsfunn_type_lukket CHECK (funntype IN (
            'time_uten_arbeidsplan', 'avvik_mot_plan', 'overtid',
            'ukjent_prosjektkode', 'ingen_terskel')),
    -- MINUTTER for `avvik_mot_plan` og `overtid`, DØGN for
    -- `time_uten_arbeidsplan`, 0 for de to andre.
    over_grense INT,
    -- Hvor mange dager funnet gjelder. Ett avvik og tjue er ikke samme
    -- sak, og et funn som bare bar det siste ville sagt for lite.
    antall_dager INT,
    -- Den seneste dagen funnet gjelder — inngangen til historikken.
    siste_dato DATE,
    terskelversjon INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT lonnsfunn_pk PRIMARY KEY (tenant, taker_id, funntype),
    CONSTRAINT lonnsfunn_taker_fk FOREIGN KEY (tenant, taker_id)
        REFERENCES lonnstaker (tenant, taker_id),
    CONSTRAINT lonnsfunn_lukking_helhet CHECK (
        (apen AND lukket_ts IS NULL)
     OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE INDEX lonnsfunn_apne ON lonnsfunn (tenant, funntype) WHERE apen;


-- ------------------------------------------------------------
-- 2. Vaktene.
-- ------------------------------------------------------------

CREATE FUNCTION m39_terskel_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'lonnsterskel: TRUNCATE avvist — en tømt'
            ' normaltid gjør hver arbeidsdag til overtid uten at noen'
            ' har bestemt det'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'lonnsterskel: DELETE avvist — en grense endres'
            ' ved å settes, ikke ved å forsvinne'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.versjon <= OLD.versjon THEN
        RAISE EXCEPTION 'lonnsterskel: versjonen må øke (% -> %)',
            OLD.versjon, NEW.versjon
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m39_terskel_vakt() FROM PUBLIC;
CREATE TRIGGER m39_terskel_vakt
    BEFORE UPDATE OR DELETE ON lonnsterskel
    FOR EACH ROW EXECUTE FUNCTION m39_terskel_vakt();
CREATE TRIGGER m39_terskel_ingen_truncate
    BEFORE TRUNCATE ON lonnsterskel
    EXECUTE FUNCTION m39_terskel_vakt();


CREATE FUNCTION m39_taker_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'lonnstaker: TRUNCATE avvist — takerne bærer'
            ' lønnshistorikken'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'lonnstaker: DELETE avvist — en lønnstaker'
            ' deaktiveres, hen slettes ikke. Timegrunnlaget skal'
            ' overleve at arbeidsforholdet tar slutt'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.taker_id <> OLD.taker_id
       OR NEW.ekstern_ref <> OLD.ekstern_ref
       OR NEW.opprettet <> OLD.opprettet THEN
        RAISE EXCEPTION 'lonnstaker: identiteten er FROSSET — taker_id,'
            ' ekstern_ref og opprettet kan ikke endres'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m39_taker_vakt() FROM PUBLIC;
CREATE TRIGGER m39_taker_vakt
    BEFORE UPDATE OR DELETE ON lonnstaker
    FOR EACH ROW EXECUTE FUNCTION m39_taker_vakt();
CREATE TRIGGER m39_taker_ingen_truncate
    BEFORE TRUNCATE ON lonnstaker
    EXECUTE FUNCTION m39_taker_vakt();


-- DOM 2: LØNNSGRUNNLAGET OVERSKRIVES ALDRI.
--
-- Modulens skarpeste vakt. En feilført time rettes med en NY rad —
-- `kilde = 'korreksjon'` finnes nettopp for det. Kunne den gamle raden
-- endres, ville sporet av at noe ble rettet forsvunnet, og det sporet
-- er det en lønnstvist står på.
CREATE FUNCTION m39_time_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'timeregistrering: TRUNCATE avvist — et tømt'
            ' timegrunnlag er arbeid ingen kan bevise at ble gjort'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'timeregistrering: DELETE avvist — en feilført'
            ' time rettes med en NY rad (kilde = korreksjon).'
            ' Historikken er hele beviset'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'timeregistrering: raden er FROSSET — en time'
            ' som kunne endres i ettertid ville slettet sporet av at'
            ' noe ble rettet'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m39_time_vakt() FROM PUBLIC;
CREATE TRIGGER m39_time_vakt
    BEFORE UPDATE OR DELETE ON timeregistrering
    FOR EACH ROW EXECUTE FUNCTION m39_time_vakt();
CREATE TRIGGER m39_time_ingen_truncate
    BEFORE TRUNCATE ON timeregistrering
    EXECUTE FUNCTION m39_time_vakt();


-- DOM 3: PLANEN ERSTATTES, DEN ENDRES ALDRI — og periodene overlapper
-- ikke. «Hvilken plan gjaldt den dagen» skal ha nøyaktig ett svar.
CREATE FUNCTION m39_plan_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_kolliderer INT;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'arbeidsplan: TRUNCATE avvist — uten planene'
            ' finnes det ingenting å måle timene mot'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'arbeidsplan: DELETE avvist — en plan avløses'
            ' av en ny, den forsvinner ikke'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        -- KUN `gyldig_til` KAN SETTES, og bare fra NULL. Det er slik
        -- en plan avløses; alt annet på raden er frosset.
        IF NEW.plan_id <> OLD.plan_id
           OR NEW.taker_id <> OLD.taker_id
           OR NEW.versjon <> OLD.versjon
           OR NEW.planlagt_minutter_dag <> OLD.planlagt_minutter_dag
           OR NEW.prosjektkode <> OLD.prosjektkode
           OR NEW.gyldig_fra <> OLD.gyldig_fra
           OR NEW.begrunnelse <> OLD.begrunnelse
           OR NEW.opprettet <> OLD.opprettet THEN
            RAISE EXCEPTION 'arbeidsplan: raden er FROSSET — bare'
                ' gyldig_til kan settes når planen avløses'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF OLD.gyldig_til IS NOT NULL THEN
            RAISE EXCEPTION 'arbeidsplan: gyldig_til er alt satt —'
                ' en avsluttet plan gjenåpnes ikke'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    -- INSERT: ingen overlapp med en plan som alt står.
    SELECT count(*) INTO v_kolliderer FROM public.arbeidsplan p
     WHERE p.tenant = NEW.tenant AND p.taker_id = NEW.taker_id
       AND p.plan_id <> NEW.plan_id
       AND daterange(p.gyldig_fra, p.gyldig_til, '[)')
           && daterange(NEW.gyldig_fra, NEW.gyldig_til, '[)');
    IF v_kolliderer > 0 THEN
        RAISE EXCEPTION 'arbeidsplan: perioden overlapper en plan som'
            ' alt står — «hvilken plan gjaldt den dagen» skal ha'
            ' nøyaktig ett svar'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m39_plan_vakt() FROM PUBLIC;
CREATE TRIGGER m39_plan_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON arbeidsplan
    FOR EACH ROW EXECUTE FUNCTION m39_plan_vakt();
CREATE TRIGGER m39_plan_ingen_truncate
    BEFORE TRUNCATE ON arbeidsplan
    EXECUTE FUNCTION m39_plan_vakt();


CREATE FUNCTION m39_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'lonnsfunn: TRUNCATE avvist — et tømt'
            ' funnregister ser ut som en ryddig timeliste'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'lonnsfunn: DELETE avvist — et funn lukkes,'
            ' det slettes ikke'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.forst_sett <> OLD.forst_sett THEN
        RAISE EXCEPTION 'lonnsfunn: forst_sett er FROSSET — hvor lenge'
            ' et funn har stått er halve alvoret'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m39_funn_vakt() FROM PUBLIC;
CREATE TRIGGER m39_funn_vakt
    BEFORE UPDATE OR DELETE ON lonnsfunn
    FOR EACH ROW EXECUTE FUNCTION m39_funn_vakt();
CREATE TRIGGER m39_funn_ingen_truncate
    BEFORE TRUNCATE ON lonnsfunn
    EXECUTE FUNCTION m39_funn_vakt();


-- ------------------------------------------------------------
-- 3. Dørene. SECURITY DEFINER, eid av `disponit_lonn_eier`, SP-1.
--
--    DET FINNES INGEN DØR SOM UTBETALER, OG INGEN SOM PRODUSERER EN
--    LØNNSFIL. `korreksjon` er en KILDE man registrerer en time fra —
--    aldri en handling denne modulen utfører mot noens konto.
-- ------------------------------------------------------------

-- Eieren trenger å kunne kalle SP-1-vakten og å skrive evidens.
-- `krev_tenantkontekst` eies av `disponit_m37_claimer` (111/112s form).
GRANT INSERT ON revisjonslogg TO disponit_lonn_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_lonn_eier;
RESET ROLE;

SET LOCAL ROLE disponit_lonn_eier;

-- EVIDENSEN. Modulen skriver i revisjonsloggen som BEVIS på hva den
-- selv gjorde — aldri som en dom om et vilkår. Beslutningen er alltid
-- `TILLAT` på en registrering modulen utførte; den attesterer ingenting.
--
-- DETALJEN BÆRER ALDRI TIMETALLET. Revisjonsloggen er bredere lesbar
-- enn lønnsregisteret, og hvor mye en navngitt person jobbet er
-- persondata som ikke skal lekke dit.
CREATE FUNCTION m39_evidens(p_tenant TEXT, p_taker_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm39_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm39_lonn', 'handling', p_handling,
        'taker_id', p_taker_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm39_lonn',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:lonn', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m39_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;


-- GRENSENE. DOM 5.
CREATE FUNCTION m39_sett_terskler(
    p_tenant TEXT, p_normaltid_dag INT, p_normaltid_uke INT,
    p_avvik_minutter INT, p_uten_plan_dogn INT,
    p_vurderingsvindu_dogn INT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm39_sett_terskler');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    -- EN UKE KAN IKKE VÆRE KORTERE ENN EN DAG. Uten denne ville hver
    -- eneste arbeidsdag blitt ukesovertid, og funnlisten hadde vært
    -- ubrukelig fra første natt.
    IF p_normaltid_uke < p_normaltid_dag THEN
        RAISE EXCEPTION 'm39_sett_terskler: normaltid per uke (%) kan'
            ' ikke være kortere enn per dag (%)',
            p_normaltid_uke, p_normaltid_dag
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.lonnsterskel
        (tenant, normaltid_minutter_dag, normaltid_minutter_uke,
         avvik_minutter, uten_plan_dogn, vurderingsvindu_dogn,
         oppdatert_av)
    VALUES (p_tenant, p_normaltid_dag, p_normaltid_uke,
            p_avvik_minutter, p_uten_plan_dogn,
            p_vurderingsvindu_dogn, p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        normaltid_minutter_dag = EXCLUDED.normaltid_minutter_dag,
        normaltid_minutter_uke = EXCLUDED.normaltid_minutter_uke,
        avvik_minutter = EXCLUDED.avvik_minutter,
        uten_plan_dogn = EXCLUDED.uten_plan_dogn,
        vurderingsvindu_dogn = EXCLUDED.vurderingsvindu_dogn,
        versjon = public.lonnsterskel.versjon + 1,
        oppdatert = now(),
        oppdatert_av = EXCLUDED.oppdatert_av
    RETURNING versjon INTO v_versjon;
    PERFORM public.m39_evidens(p_tenant, NULL, 'lonnsterskler_satt',
        p_aktor, jsonb_build_object('versjon', v_versjon));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION m39_sett_terskler(
    TEXT, INT, INT, INT, INT, INT, TEXT) FROM PUBLIC;


CREATE FUNCTION m39_registrer_taker(
    p_tenant TEXT, p_taker_id UUID, p_ekstern_ref TEXT, p_navn TEXT,
    p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm39_registrer_taker');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.lonnstaker
        (tenant, taker_id, ekstern_ref, navn, opprettet_av)
    VALUES (p_tenant, p_taker_id, btrim(p_ekstern_ref), btrim(p_navn),
            p_aktor);
    PERFORM public.m39_evidens(p_tenant, p_taker_id,
        'lonnstaker_opprettet', p_aktor, '{}'::jsonb);
END $$;
REVOKE ALL ON FUNCTION m39_registrer_taker(TEXT, UUID, TEXT, TEXT, TEXT)
    FROM PUBLIC;


CREATE FUNCTION m39_sett_takeraktiv(
    p_tenant TEXT, p_taker_id UUID, p_aktiv BOOLEAN, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_naa BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm39_sett_takeraktiv');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT aktiv INTO v_naa FROM public.lonnstaker
     WHERE tenant = p_tenant AND taker_id = p_taker_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm39_sett_takeraktiv: ukjent lønnstaker %',
            p_taker_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_naa = p_aktiv THEN
        RETURN false;
    END IF;
    UPDATE public.lonnstaker SET aktiv = p_aktiv
     WHERE tenant = p_tenant AND taker_id = p_taker_id;
    -- En deaktivert taker har ingen åpne funn: ingen skal se på timene
    -- til noen som har sluttet. TIMEGRUNNLAGET BLIR STÅENDE.
    IF NOT p_aktiv THEN
        UPDATE public.lonnsfunn SET apen = false, lukket_ts = now()
         WHERE tenant = p_tenant AND taker_id = p_taker_id AND apen;
    END IF;
    PERFORM public.m39_evidens(p_tenant, p_taker_id,
        'lonnstaker_aktiv_satt', p_aktor,
        jsonb_build_object('aktiv', p_aktiv));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m39_sett_takeraktiv(TEXT, UUID, BOOLEAN, TEXT)
    FROM PUBLIC;


-- ARBEIDSPLANEN. DOM 3.
--
-- En ny plan AVLØSER den forrige i samme transaksjon: den forrige får
-- `gyldig_til`, den nye får `gyldig_fra`. Uten det ville to planer
-- gjeldt samme dag, og «hvilken plan gjaldt den dagen» hatt to svar.
CREATE FUNCTION m39_sett_arbeidsplan(
    p_tenant TEXT, p_plan_id UUID, p_taker_id UUID,
    p_planlagt_minutter INT, p_prosjektkode TEXT, p_gyldig_fra DATE,
    p_begrunnelse TEXT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_aktiv BOOLEAN;
    v_siste DATE;
    v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm39_sett_arbeidsplan');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_begrunnelse IS NULL OR p_begrunnelse !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm39_sett_arbeidsplan: en plan uten begrunnelse'
            ' er ingen beslutning — den avgjør hva noens timer måles'
            ' mot' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT aktiv INTO v_aktiv FROM public.lonnstaker
     WHERE tenant = p_tenant AND taker_id = p_taker_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm39_sett_arbeidsplan: ukjent lønnstaker %',
            p_taker_id USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_aktiv THEN
        RAISE EXCEPTION 'm39_sett_arbeidsplan: lønnstakeren % er'
            ' deaktivert', p_taker_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT max(p.gyldig_fra), coalesce(max(p.versjon), 0) + 1
      INTO v_siste, v_versjon
      FROM public.arbeidsplan p
     WHERE p.tenant = p_tenant AND p.taker_id = p_taker_id;
    -- EN PLAN SKRIVES IKKE BAKOVER. Timer som alt er målt mot den
    -- gamle planen ville fått et nytt fasitsvar i ettertid.
    IF v_siste IS NOT NULL AND p_gyldig_fra <= v_siste THEN
        RAISE EXCEPTION 'm39_sett_arbeidsplan: planen skrives ikke'
            ' bakover (% <= %)', p_gyldig_fra, v_siste
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.arbeidsplan SET gyldig_til = p_gyldig_fra
     WHERE tenant = p_tenant AND taker_id = p_taker_id
       AND gyldig_til IS NULL;

    INSERT INTO public.arbeidsplan
        (tenant, plan_id, taker_id, versjon, planlagt_minutter_dag,
         prosjektkode, gyldig_fra, begrunnelse, opprettet_av)
    VALUES (p_tenant, p_plan_id, p_taker_id, v_versjon,
            p_planlagt_minutter, btrim(p_prosjektkode), p_gyldig_fra,
            btrim(p_begrunnelse), p_aktor);

    PERFORM public.m39_evidens(p_tenant, p_taker_id,
        'arbeidsplan_satt', p_aktor,
        jsonb_build_object('versjon', v_versjon,
                           'prosjektkode', btrim(p_prosjektkode)));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION m39_sett_arbeidsplan(
    TEXT, UUID, UUID, INT, TEXT, DATE, TEXT, TEXT) FROM PUBLIC;


-- TIMEDØREN. DOM 1, 2 OG 4.
--
-- MINUTTER GÅR INN. Det finnes ingen vei inn for et timetall med
-- desimaler, og det er hele dom 1: konverteringen skjer i klienten,
-- én gang, og basen ser aldri et flyttall.
--
-- DEN SETTER INGEN OVERTIDSFLAGG. Om dagen var overtid utledes av
-- sveipen mot tenantens normaltid, og blir et funn noen må se på.
CREATE FUNCTION m39_registrer_timer(
    p_tenant TEXT, p_time_id UUID, p_taker_id UUID, p_dato DATE,
    p_minutter INT, p_prosjektkode TEXT, p_kilde TEXT,
    p_kilde_ref TEXT, p_notat TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_aktiv BOOLEAN;
    v_har_plan BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm39_registrer_timer');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    -- SAMME DOM SOM 111 OG 112: en time kan ikke være ført i framtida.
    -- Sveipen måler mot `current_date`, så en framtidsdatert rad ville
    -- vært usynlig for den — og timene ville stått uten funn til datoen
    -- passerte.
    IF p_dato IS NULL OR p_dato > current_date THEN
        RAISE EXCEPTION 'm39_registrer_timer: en time kan ikke være'
            ' arbeidet i framtida (%)', p_dato
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT aktiv INTO v_aktiv FROM public.lonnstaker
     WHERE tenant = p_tenant AND taker_id = p_taker_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm39_registrer_timer: ukjent lønnstaker %',
            p_taker_id USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_aktiv THEN
        RAISE EXCEPTION 'm39_registrer_timer: lønnstakeren % er'
            ' deaktivert', p_taker_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO public.timeregistrering
        (tenant, time_id, taker_id, dato, minutter, prosjektkode,
         kilde, kilde_ref, notat, registrert_av)
    VALUES (p_tenant, p_time_id, p_taker_id, p_dato, p_minutter,
            btrim(p_prosjektkode), p_kilde, btrim(p_kilde_ref),
            btrim(p_notat), p_aktor);

    SELECT EXISTS (
        SELECT 1 FROM public.arbeidsplan p
         WHERE p.tenant = p_tenant AND p.taker_id = p_taker_id
           AND p.gyldig_fra <= p_dato
           AND (p.gyldig_til IS NULL OR p.gyldig_til > p_dato))
      INTO v_har_plan;

    PERFORM public.m39_evidens(p_tenant, p_taker_id,
        'timer_registrert', p_aktor,
        jsonb_build_object('kilde', p_kilde, 'har_plan', v_har_plan));
    -- SANT NÅR TIMEN HAR EN PLAN Å MÅLES MOT. DOM 3, som et SVAR til
    -- kalleren og ikke bare som et funn neste natt: den som fører en
    -- time skal få vite med én gang at den ikke måles mot noe.
    RETURN v_har_plan;
END $$;
REVOKE ALL ON FUNCTION m39_registrer_timer(
    TEXT, UUID, UUID, DATE, INT, TEXT, TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Lesedørene.
-- ------------------------------------------------------------

CREATE FUNCTION m39_tersklene(p_tenant TEXT)
RETURNS TABLE (normaltid_minutter_dag INT, normaltid_minutter_uke INT,
               avvik_minutter INT, uten_plan_dogn INT,
               vurderingsvindu_dogn INT, versjon INT,
               oppdatert TIMESTAMPTZ, oppdatert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm39_tersklene');
    RETURN QUERY
    SELECT t.normaltid_minutter_dag, t.normaltid_minutter_uke,
           t.avvik_minutter, t.uten_plan_dogn, t.vurderingsvindu_dogn,
           t.versjon, t.oppdatert, t.oppdatert_av
      FROM public.lonnsterskel t WHERE t.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m39_tersklene(TEXT) FROM PUBLIC;


-- PLANEN SOM GJALDT DEN DAGEN. Nøyaktig ett svar, alltid.
CREATE FUNCTION m39_plan_paa_dato(
    p_tenant TEXT, p_taker_id UUID, p_dag DATE)
RETURNS TABLE (plan_id UUID, versjon INT, planlagt_minutter_dag INT,
               prosjektkode TEXT, gyldig_fra DATE, gyldig_til DATE,
               begrunnelse TEXT, opprettet_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm39_plan_paa_dato');
    RETURN QUERY
    SELECT p.plan_id, p.versjon, p.planlagt_minutter_dag,
           p.prosjektkode, p.gyldig_fra, p.gyldig_til, p.begrunnelse,
           p.opprettet_av
      FROM public.arbeidsplan p
     WHERE p.tenant = p_tenant AND p.taker_id = p_taker_id
       AND p.gyldig_fra <= p_dag
       AND (p.gyldig_til IS NULL OR p.gyldig_til > p_dag);
END $$;
REVOKE ALL ON FUNCTION m39_plan_paa_dato(TEXT, UUID, DATE) FROM PUBLIC;


CREATE FUNCTION m39_planene(p_tenant TEXT, p_taker_id UUID, p_grense INT)
RETURNS TABLE (plan_id UUID, versjon INT, planlagt_minutter_dag INT,
               prosjektkode TEXT, gyldig_fra DATE, gyldig_til DATE,
               begrunnelse TEXT, opprettet TIMESTAMPTZ,
               opprettet_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm39_planene');
    RETURN QUERY
    SELECT p.plan_id, p.versjon, p.planlagt_minutter_dag,
           p.prosjektkode, p.gyldig_fra, p.gyldig_til, p.begrunnelse,
           p.opprettet, p.opprettet_av
      FROM public.arbeidsplan p
     WHERE p.tenant = p_tenant AND p.taker_id = p_taker_id
     ORDER BY p.gyldig_fra DESC
     LIMIT greatest(p_grense, 1);
END $$;
REVOKE ALL ON FUNCTION m39_planene(TEXT, UUID, INT) FROM PUBLIC;


-- TIMEGRUNNLAGET PER DAG, MED PLANEN VED SIDEN AV.
--
-- DETTE ER MODULENS EGENTLIGE SVAR. `timer_mot_arbeidsplan` er et
-- spørsmål om en SAMMENLIGNING, og her står begge tallene på samme
-- linje: hva som ble ført, hva som var planlagt, og differansen —
-- i MINUTTER, som et heltall, aldri som en prosent.
--
-- `planlagt_minutter` er NULL når ingen plan gjaldt den dagen. Det er
-- ikke det samme som null minutter, og en flate som viste dem likt
-- ville gjort «ingen plan» om til «planlagt fri».
CREATE FUNCTION m39_dagene(p_tenant TEXT, p_taker_id UUID, p_grense INT)
RETURNS TABLE (dato DATE, minutter BIGINT, planlagt_minutter INT,
               avvik_minutter BIGINT, prosjektkoder TEXT[],
               plan_prosjektkode TEXT, poster BIGINT,
               ukjent_prosjektkode BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm39_dagene');
    RETURN QUERY
    WITH dager AS (
        SELECT tr.dato, sum(tr.minutter) AS sum_min,
               count(*) AS n,
               array_agg(DISTINCT tr.prosjektkode
                         ORDER BY tr.prosjektkode) AS koder
          FROM public.timeregistrering tr
         WHERE tr.tenant = p_tenant AND tr.taker_id = p_taker_id
         GROUP BY tr.dato)
    SELECT d.dato, d.sum_min, p.planlagt_minutter_dag,
           CASE WHEN p.planlagt_minutter_dag IS NULL THEN NULL
                ELSE d.sum_min - p.planlagt_minutter_dag END,
           d.koder, p.prosjektkode, d.n,
           -- SANT når en av dagens koder ikke er planens. Målt mot
           -- ARBEIDSPLANEN VÅR EGEN, ikke mot M-25s prosjektregister
           -- — se grensen i toppen av fila.
           p.prosjektkode IS NOT NULL
             AND EXISTS (SELECT 1 FROM unnest(d.koder) k
                          WHERE k <> p.prosjektkode)
      FROM dager d
      LEFT JOIN public.arbeidsplan p
             ON p.tenant = p_tenant AND p.taker_id = p_taker_id
            AND p.gyldig_fra <= d.dato
            AND (p.gyldig_til IS NULL OR p.gyldig_til > d.dato)
     ORDER BY d.dato DESC
     LIMIT greatest(p_grense, 1);
END $$;
REVOKE ALL ON FUNCTION m39_dagene(TEXT, UUID, INT) FROM PUBLIC;


CREATE FUNCTION m39_timehistorikken(
    p_tenant TEXT, p_taker_id UUID, p_grense INT)
RETURNS TABLE (time_id UUID, dato DATE, minutter INT,
               prosjektkode TEXT, kilde TEXT, kilde_ref TEXT,
               notat TEXT, registrert TIMESTAMPTZ, registrert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm39_timehistorikken');
    RETURN QUERY
    SELECT tr.time_id, tr.dato, tr.minutter, tr.prosjektkode, tr.kilde,
           tr.kilde_ref, tr.notat, tr.registrert, tr.registrert_av
      FROM public.timeregistrering tr
     WHERE tr.tenant = p_tenant AND tr.taker_id = p_taker_id
     ORDER BY tr.dato DESC, tr.registrert DESC
     LIMIT greatest(p_grense, 1);
END $$;
REVOKE ALL ON FUNCTION m39_timehistorikken(TEXT, UUID, INT)
    FROM PUBLIC;


-- FUNNKANDIDATENE. Delt av sveipen og av testene, så porten måler den
-- SAMME regnestykket som natta gjør (110-112s form).
--
-- Fem funntyper:
--   time_uten_arbeidsplan — timer ført uten en plan å måles mot, og
--                           fristen er ute (DOM 3)
--   avvik_mot_plan        — ført tid avviker fra planen utover
--                           tenantens grense
--   overtid               — over normaltid, dag eller uke (DOM 4)
--   ukjent_prosjektkode   — ført på en annen kode enn planens
--   ingen_terskel         — tenanten har ikke satt grensene sine
--
-- ALT REGNES I MINUTTER, som heltall. Ingen divisjon, ingen prosent
-- modulen fant på selv.
CREATE FUNCTION m39_funnkandidater(p_tenant TEXT, p_dag DATE)
RETURNS TABLE (taker_id UUID, funntype TEXT, over_grense INT,
               antall_dager INT, siste_dato DATE, terskelversjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm39_funnkandidater');
    RETURN QUERY
    WITH terskel AS (
        SELECT t.normaltid_minutter_dag, t.normaltid_minutter_uke,
               t.avvik_minutter, t.uten_plan_dogn,
               t.vurderingsvindu_dogn, t.versjon
          FROM public.lonnsterskel t WHERE t.tenant = p_tenant),
    aktive AS (
        SELECT s.taker_id FROM public.lonnstaker s
         WHERE s.tenant = p_tenant AND s.aktiv),
    -- DAGENE, med planen ved siden av. Samme regnestykke som
    -- `m39_dagene`, men for ALLE takere på én gang.
    dager AS (
        SELECT tr.taker_id, tr.dato, sum(tr.minutter) AS sum_min,
               array_agg(DISTINCT tr.prosjektkode) AS koder
          FROM public.timeregistrering tr
          JOIN aktive a ON a.taker_id = tr.taker_id
         WHERE tr.tenant = p_tenant AND tr.dato <= p_dag
         GROUP BY tr.taker_id, tr.dato),
    med_plan AS (
        SELECT d.*, p.planlagt_minutter_dag, p.prosjektkode
          FROM dager d
          LEFT JOIN public.arbeidsplan p
                 ON p.tenant = p_tenant AND p.taker_id = d.taker_id
                AND p.gyldig_fra <= d.dato
                AND (p.gyldig_til IS NULL OR p.gyldig_til > d.dato)),
    -- UKENE, for ukesovertiden. `date_trunc('week', ...)` er mandag i
    -- Postgres, som er den uka en arbeidsuke faktisk er.
    uker AS (
        SELECT d.taker_id,
               date_trunc('week', d.dato)::date AS uke,
               sum(d.sum_min) AS sum_min,
               max(d.dato) AS siste
          FROM dager d GROUP BY 1, 2)
    -- DAGSOVERTID OG UKESOVERTID ER SAMME FUNNTYPE, og funnraden er
    -- nøklet på (tenant, taker_id, funntype). To rader herfra ville
    -- kollidert i sveipens `ON CONFLICT` («cannot affect row a second
    -- time») og tatt hele natta med seg. Derfor slås armene sammen her:
    -- den STØRSTE overskridelsen, alle dagene, og den seneste datoen.
    SELECT s.taker_id, s.funntype, max(s.over_grense)::INT,
           sum(s.antall_dager)::INT, max(s.siste_dato),
           max(s.terskelversjon)::INT
      FROM (
        -- Ingen grenser satt: DET er funnet, og de andre måles ikke.
        SELECT a.taker_id, 'ingen_terskel'::TEXT, 0, 0,
               NULL::DATE, NULL::INT
          FROM aktive a
         WHERE NOT EXISTS (SELECT 1 FROM terskel)
        UNION ALL
        -- DOM 3: timer uten en plan å måles mot, og fristen er ute.
        SELECT m.taker_id, 'time_uten_arbeidsplan'::TEXT,
               ((p_dag - max(m.dato)) - t.uten_plan_dogn)::INT,
               count(*)::INT, max(m.dato), t.versjon
          FROM med_plan m CROSS JOIN terskel t
         WHERE m.planlagt_minutter_dag IS NULL
           AND (p_dag - m.dato) > t.uten_plan_dogn
         GROUP BY m.taker_id, t.uten_plan_dogn, t.versjon
        UNION ALL
        -- Ført tid avviker fra planen utover tenantens grense.
        -- `abs()` fordi BEGGE retninger er avvik: mindre enn planlagt
        -- er like mye et spørsmål som mer.
        SELECT m.taker_id, 'avvik_mot_plan'::TEXT,
               max(abs(m.sum_min - m.planlagt_minutter_dag)
                   - t.avvik_minutter)::INT,
               count(*)::INT, max(m.dato), t.versjon
          FROM med_plan m CROSS JOIN terskel t
         WHERE m.planlagt_minutter_dag IS NOT NULL
           AND abs(m.sum_min - m.planlagt_minutter_dag)
               > t.avvik_minutter
           -- INNENFOR VINDUET. Se `vurderingsvindu_dogn`: et avvik kan
           -- ikke rettes, så uten dette ville funnet aldri lukkes.
           AND (p_dag - m.dato) <= t.vurderingsvindu_dogn
         GROUP BY m.taker_id, t.versjon
        UNION ALL
        -- DOM 4: OVERTID ER ET FUNN. Dagsovertid.
        SELECT m.taker_id, 'overtid'::TEXT,
               max(m.sum_min - t.normaltid_minutter_dag)::INT,
               count(*)::INT, max(m.dato), t.versjon
          FROM med_plan m CROSS JOIN terskel t
         WHERE m.sum_min > t.normaltid_minutter_dag
           AND (p_dag - m.dato) <= t.vurderingsvindu_dogn
         GROUP BY m.taker_id, t.versjon
        UNION ALL
        -- …og ukesovertid, for den som jobber normale dager men mange
        -- av dem. En modul som bare så på dagen ville sluppet gjennom
        -- sju sekstimersdager på rad.
        SELECT u.taker_id, 'overtid'::TEXT,
               max(u.sum_min - t.normaltid_minutter_uke)::INT,
               count(*)::INT, max(u.siste), t.versjon
          FROM uker u CROSS JOIN terskel t
         WHERE u.sum_min > t.normaltid_minutter_uke
           AND (p_dag - u.siste) <= t.vurderingsvindu_dogn
         GROUP BY u.taker_id, t.versjon
        UNION ALL
        -- Ført på en annen kode enn planens.
        SELECT m.taker_id, 'ukjent_prosjektkode'::TEXT, 0,
               count(*)::INT, max(m.dato), t.versjon
          FROM med_plan m CROSS JOIN terskel t
         WHERE m.prosjektkode IS NOT NULL
           AND EXISTS (SELECT 1 FROM unnest(m.koder) k
                        WHERE k <> m.prosjektkode)
           AND (p_dag - m.dato) <= t.vurderingsvindu_dogn
         GROUP BY m.taker_id, t.versjon
    ) s (taker_id, funntype, over_grense, antall_dager, siste_dato,
         terskelversjon)
     GROUP BY s.taker_id, s.funntype;
END $$;
REVOKE ALL ON FUNCTION m39_funnkandidater(TEXT, DATE) FROM PUBLIC;


-- OVERSIKTEN. Sammendraget flaten åpner på.
CREATE FUNCTION m39_lonnsstatus(p_tenant TEXT)
RETURNS TABLE (takere BIGINT, aktive BIGINT, med_timer BIGINT,
               med_plan BIGINT, apne_funn BIGINT, apne_overtid BIGINT,
               har_terskel BOOLEAN, terskelversjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm39_lonnsstatus');
    RETURN QUERY
    SELECT (SELECT count(*) FROM public.lonnstaker s
             WHERE s.tenant = p_tenant),
           (SELECT count(*) FROM public.lonnstaker s
             WHERE s.tenant = p_tenant AND s.aktiv),
           (SELECT count(DISTINCT tr.taker_id)
              FROM public.timeregistrering tr
             WHERE tr.tenant = p_tenant),
           (SELECT count(DISTINCT p.taker_id)
              FROM public.arbeidsplan p WHERE p.tenant = p_tenant),
           (SELECT count(*) FROM public.lonnsfunn f
             WHERE f.tenant = p_tenant AND f.apen),
           (SELECT count(*) FROM public.lonnsfunn f
             WHERE f.tenant = p_tenant AND f.apen
               AND f.funntype = 'overtid'),
           EXISTS (SELECT 1 FROM public.lonnsterskel t
                    WHERE t.tenant = p_tenant),
           (SELECT t.versjon FROM public.lonnsterskel t
             WHERE t.tenant = p_tenant);
END $$;
REVOKE ALL ON FUNCTION m39_lonnsstatus(TEXT) FROM PUBLIC;


CREATE FUNCTION m39_takerne(p_tenant TEXT, p_grense INT)
RETURNS TABLE (taker_id UUID, ekstern_ref TEXT, navn TEXT,
               aktiv BOOLEAN, plan_id UUID, planlagt_minutter_dag INT,
               plan_prosjektkode TEXT, plan_fra DATE,
               sum_minutter BIGINT, dager BIGINT, siste_dato DATE,
               apne_funn TEXT[])
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm39_takerne');
    RETURN QUERY
    WITH gjeldende AS (
        SELECT p.* FROM public.arbeidsplan p
         WHERE p.tenant = p_tenant AND p.gyldig_fra <= current_date
           AND (p.gyldig_til IS NULL OR p.gyldig_til > current_date)),
    sumtid AS (
        SELECT tr.taker_id, sum(tr.minutter) AS sum_min,
               count(DISTINCT tr.dato) AS n, max(tr.dato) AS siste
          FROM public.timeregistrering tr
         WHERE tr.tenant = p_tenant GROUP BY tr.taker_id)
    SELECT s.taker_id, s.ekstern_ref, s.navn, s.aktiv,
           g.plan_id, g.planlagt_minutter_dag, g.prosjektkode,
           g.gyldig_fra,
           coalesce(st.sum_min, 0), coalesce(st.n, 0), st.siste,
           (SELECT coalesce(array_agg(f.funntype ORDER BY f.funntype),
                            ARRAY[]::TEXT[])
              FROM public.lonnsfunn f
             WHERE f.tenant = p_tenant AND f.taker_id = s.taker_id
               AND f.apen)
      FROM public.lonnstaker s
      LEFT JOIN gjeldende g ON g.taker_id = s.taker_id
      LEFT JOIN sumtid st ON st.taker_id = s.taker_id
     WHERE s.tenant = p_tenant
     ORDER BY s.aktiv DESC, s.ekstern_ref
     LIMIT greatest(p_grense, 1);
END $$;
REVOKE ALL ON FUNCTION m39_takerne(TEXT, INT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 5. SVEIPEN. Kryss-tenant, egen rolle, INGEN tabellrettigheter.
-- ------------------------------------------------------------
--
-- SVEIPEN UTBETALER INGENTING OG PRODUSERER INGEN LØNNSFIL. Den leser
-- registeret, regner ut hvem som er over tenantens egne grenser, og
-- skriver FUNN. Det er hele mandatet.
CREATE FUNCTION m39_sveip_lonnsgrunnlag(p_grense INT DEFAULT 500)
RETURNS TABLE (tenanter INT, nye_funn INT, oppdaterte_funn INT,
               lukkede_funn INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_liste TEXT[];
    v_t TEXT;
    v_dag DATE := current_date;
    v_tenanter INT := 0;
    v_nye INT := 0;
    v_oppdaterte INT := 0;
    v_lukket INT := 0;
    v_n INT;
    v_m INT;
BEGIN
    -- SP-1s speilbilde: sveipen er KRYSS-TENANT og skal derfor kjøre
    -- UTEN tenantkontekst. Står det en kontekst, er kallet enten en
    -- feil eller et forsøk på å bruke sveiperollen som en snarvei
    -- rundt RLS.
    IF coalesce(current_setting('disponit.tenant', true), '') <> '' THEN
        RAISE EXCEPTION 'm39_sveip_lonnsgrunnlag: KRYSS-TENANT — kall'
            ' den uten tenantkontekst'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- MATERIALISERT FØR LØKKEN (111/112s form). En `FOR ... IN SELECT`
    -- er en LAT markør: `set_config` inne i løkka ville endret nettopp
    -- den RLS-konteksten markøren fortsatt hentet rader gjennom, og
    -- sveipen ville stille sett bare den første tenanten.
    SELECT array_agg(DISTINCT s.tenant ORDER BY s.tenant)
      INTO v_liste FROM public.lonnstaker s WHERE s.aktiv;
    v_liste := (SELECT array_agg(x ORDER BY x) FROM unnest(
        coalesce(v_liste, ARRAY[]::TEXT[])) WITH ORDINALITY AS u(x, i)
        WHERE u.i <= greatest(p_grense, 1));

    FOREACH v_t IN ARRAY coalesce(v_liste, ARRAY[]::TEXT[])
    LOOP
        v_tenanter := v_tenanter + 1;
        PERFORM set_config('disponit.tenant', v_t, true);

        WITH kand AS (
            SELECT * FROM public.m39_funnkandidater(v_t, v_dag)),
        skrevet AS (
            INSERT INTO public.lonnsfunn
                (tenant, taker_id, funntype, over_grense,
                 antall_dager, siste_dato, terskelversjon)
            SELECT v_t, k.taker_id, k.funntype,
                   greatest(k.over_grense, 0), k.antall_dager,
                   k.siste_dato, k.terskelversjon
              FROM kand k
            ON CONFLICT (tenant, taker_id, funntype) DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                antall_dager = EXCLUDED.antall_dager,
                siste_dato = EXCLUDED.siste_dato,
                terskelversjon = EXCLUDED.terskelversjon,
                sist_sett_sveip = now(),
                apen = true,
                lukket_ts = NULL
            RETURNING (xmax = 0) AS var_ny)
        -- NYE OG OPPFRISKEDE TELLES HVER FOR SEG, og BEGGE akkumuleres
        -- over tenantene (112s CodeRabbit-lærdom: `INTO v_oppdaterte`
        -- ville satt summen på nytt for hver tenant).
        SELECT count(*) FILTER (WHERE var_ny),
               count(*) FILTER (WHERE NOT var_ny)
          INTO v_n, v_m FROM skrevet;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_m, 0);

        -- LUKKER DE SOM IKKE LENGER ER KANDIDATER. Radene blir
        -- stående: at et funn HAR stått er også en måling.
        WITH kand AS (
            SELECT * FROM public.m39_funnkandidater(v_t, v_dag)),
        lukket AS (
            UPDATE public.lonnsfunn f
               SET apen = false, lukket_ts = now()
             WHERE f.tenant = v_t AND f.apen
               AND NOT EXISTS (
                   SELECT 1 FROM kand k
                    WHERE k.taker_id = f.taker_id
                      AND k.funntype = f.funntype)
            RETURNING 1)
        SELECT count(*) INTO v_n FROM lukket;
        v_lukket := v_lukket + coalesce(v_n, 0);

        PERFORM set_config('disponit.tenant', '', true);
    END LOOP;

    RETURN QUERY SELECT v_tenanter, v_nye, v_oppdaterte, v_lukket;
END $$;
REVOKE ALL ON FUNCTION m39_sveip_lonnsgrunnlag(INT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 6. RLS. ENABLE + FORCE på alle fem, `tenant_isolasjon` på hver.
-- ------------------------------------------------------------

RESET ROLE;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['lonnsterskel', 'lonnstaker',
                             'arbeidsplan', 'timeregistrering',
                             'lonnsfunn']
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL'
                       ' SECURITY', t);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL'
                       ' SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolasjon ON public.%I'
                       ' USING (tenant = current_setting('
                       '''disponit.tenant'', true))'
                       ' WITH CHECK (tenant = current_setting('
                       '''disponit.tenant'', true))', t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO'
                       ' disponit_lonn_eier', t);
    END LOOP;
END $$;

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — tre gjerder
-- (111/112s form): bare på TAKERTABELLEN, bare FOR SELECT, bare til
-- eieren, og bare når ingen tenantkontekst står. Sveipen trenger
-- nøyaktig ett kryss-tenant-svar: HVILKE tenanter finnes.
CREATE POLICY m39_sveip_tenantliste ON lonnstaker
    FOR SELECT TO disponit_lonn_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- TIMEGRUNNLAGET FÅR IKKE UPDATE, HELLER IKKE FOR EIEREN. Vakten over
-- stanser den som likevel skulle prøve; dette gjerdet stanser forsøket
-- før det når vakten. To gjerder, av samme grunn som i 110-112.
REVOKE UPDATE ON public.timeregistrering FROM disponit_lonn_eier;


-- ------------------------------------------------------------
-- 7. Rettighetene. SP-7: kjøretidsrollen får INGEN tabellrettigheter.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_lonn_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m39_lonnsstatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m39_takerne(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m39_timehistorikken(TEXT, UUID, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m39_dagene(TEXT, UUID, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m39_planene(TEXT, UUID, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m39_plan_paa_dato(TEXT, UUID, DATE) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m39_tersklene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m39_funnkandidater(TEXT,'
            ' DATE) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m39_sett_terskler(TEXT, INT, INT, INT, INT, INT,'
            ' TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m39_registrer_taker(TEXT, UUID, TEXT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m39_sett_arbeidsplan(TEXT, UUID, UUID, INT, TEXT, DATE,'
            ' TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m39_registrer_timer(TEXT, UUID, UUID, DATE, INT, TEXT,'
            ' TEXT, TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m39_sett_takeraktiv(TEXT, UUID, BOOLEAN, TEXT)'
            ' TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_lonnssveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m39_sveip_lonnsgrunnlag(INT) TO disponit_lonnssveip';
    END IF;
END $$;

-- SVEIPEN ER IKKE KJØRETIDSROLLENS. `m39_funnkandidater` er derimot
-- delt: flaten skal kunne vise hvorfor et funn står, uten å vente på
-- natta, og kandidatdøren er tenantbundet (SP-1).
REVOKE EXECUTE ON FUNCTION m39_sveip_lonnsgrunnlag(INT) FROM disponit;

RESET ROLE;
