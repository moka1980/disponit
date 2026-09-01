-- 093: M-4 «Data- og filforvalter» v1 — HUSETS RETENSJONSREGNSKAP.
--
-- V1-DOMMEN (manifestets hodekommentar, bokstavelig): modulen SLETTER
-- INGENTING utenfor sine egne målerader. Katalogteksten beskriver en
-- filforvalter over kundens skylagre med karantene og angrefrist; ingen
-- av de tre forutsetningene den selv oppgir finnes (connector-rammeverk,
-- OCR, tilgangskart), og en ny slettevei ved siden av de seks reaperne
-- som alt kjører ville vært den farligste koden i huset.
--
-- Det modulen GJØR: den navngir hvert lager, skriver ned hvilken frist
-- og hvilken reaper som gjelder, måler beholdning og alder — og gjør et
-- lager UTEN SKREVET DOM til et FUNN. Sletting fortsetter å skje
-- nøyaktig der den skjer i dag.
--
-- REGELEN SOM BÆRER MODULEN: et lager som ikke kunne måles rapporteres
-- som FUNN, ALDRI som null. En rapport som sier «0 ureapede rader» fordi
-- grantet manglet, er ikke en grønn måling — den er en måling som ikke
-- kjørte.
--
-- FORMENE ER HUSETS EGNE:
--   * registeret er GLOBALT og tenantløst (M-31s plattformregisterform),
--     seedet her og endret bare i migrasjon — dommene felles i git,
--     ikke gjennom en dør;
--   * `retensjonsbeholdning` er tenant-tabell med RLS ENABLE + FORCE og
--     `tenant_isolasjon` på 088-formen;
--   * funnene er et LUKKET SETT (M-6s klassifisering_utenfor_lukket_sett)
--     med ETT åpent funn per (lager, funntype) — funnlisten er ikke en
--     logg som vokser med kadensen;
--   * målejobben er kryss-tenant på 038/057-formen: UTVALGET ER
--     PREDIKATET, ingen tenantparameter, ingen BYPASSRLS — en eksplisitt
--     policy per målt tabell;
--   * KOLONNEGRANT, aldri tabellgrant: at måleren aldri leser persondata
--     skal være en egenskap ved BASEN, ikke ved disiplinen.
--
-- FORUTSETNING SOM MÅ VÆRE PÅ PLASS FØR DENNE MIGRASJONEN KJØRER:
-- `disponit_migrator` må være MEDLEM av `disponit_lager_eier`
-- (`GRANT disponit_lager_eier TO disponit_migrator WITH INHERIT FALSE`).
-- Klyngefundamentet opprettet rollen og ga den UC på skjemaet, men ikke
-- medlemskapet — uten det kan migrator verken `SET LOCAL ROLE` hit eller
-- eie objektene på eierens vegne. Sjekken under feiler HARDT med den
-- ene linjen som mangler, framfor å la migrasjonen dø på en
-- «permission denied» ingen kan lese fikset ut av.

DO $$
BEGIN
    -- 'MEMBER', ikke 'USAGE': medlemskapet skal være WITH INHERIT FALSE
    -- (005/013/014-formen), altså SET ROLE og ingen arvede rettigheter —
    -- et arvende medlemskap ville gitt migrator eierrollens RLS-policyer.
    IF NOT pg_catalog.pg_has_role(current_user, 'disponit_lager_eier',
                                  'MEMBER') THEN
        RAISE EXCEPTION '093: % er ikke medlem av disponit_lager_eier.'
            ' Kjør som superbruker: GRANT disponit_lager_eier TO %'
            ' WITH INHERIT FALSE', current_user, current_user;
    END IF;
END $$;

-- Kontekstporten eies av claimeren (038) og er REVOKEt fra PUBLIC.
-- M-4s LESEDØRER er definere som må passere den, så eieren trenger
-- EXECUTE — 039/074-formen, gitt av porteieren selv.
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_lager_eier;
RESET ROLE;

-- ============================================================
-- 1. REGISTERET og målelagrene. Eid av disponit_lager_eier: eierskapet
--    ER skrivetilgangen (057-radenes egen begrunnelse, ordrett), og
--    runtime-rollen når dem KUN gjennom lesedørene.
-- ============================================================
SET LOCAL ROLE disponit_lager_eier;

-- ------------------------------------------------------------
-- 1.1 `retensjonslager` — ÉN RAD PER LAGER. Globalt og tenantløst:
--     registeret beskriver PLATTFORMENS lagre, ikke en kundes data.
--
--     VAKTEN ER DET BÆRENDE. CHECKen gjør ulovlige kombinasjoner
--     UREPRESENTERBARE: et lager kan ikke PÅSTÅ at det står under frist
--     uten å navngi reaperen, fristkilden og reap-markøren. Og de to
--     triggerne under gjør påstanden sann mot BASEN — en reaper som
--     ikke finnes i pg_proc er en løgn registeret ikke skal kunne bære.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS retensjonslager (
    lager_id        text PRIMARY KEY,
    relasjon        text NOT NULL,
    klasse          text NOT NULL
                    CHECK (klasse IN ('persondata', 'evidens',
                                      'driftsspor', 'konfigurasjon')),
    -- NULL = globalt lager. Beholdningen bokføres da på tenant ''.
    tenantkolonne   text,
    alderskolonne   text NOT NULL,
    -- NULL = ingen reap-markør → INGEN RADLESING. Uten en kolonne som
    -- sier hva som ER reapet, finnes det ikke noe ureapet å telle, og
    -- da skal måleren la være å røre radene i det hele tatt.
    reapetkolonne   text,
    fristkilde      text,
    frist_dogn      numeric,
    reaper          text,
    dom             text NOT NULL
                    CHECK (dom IN ('under_frist', 'uten_frist_akseptert',
                                   'uten_frist_apen')),
    dom_begrunnelse text NOT NULL CHECK (length(btrim(dom_begrunnelse)) > 0),
    dom_ts          timestamptz NOT NULL DEFAULT now(),
    dom_migrasjon   text NOT NULL,
    CONSTRAINT retensjonslager_dom_vakt CHECK (
        (dom = 'under_frist'
             AND reaper IS NOT NULL
             AND fristkilde IS NOT NULL
             AND reapetkolonne IS NOT NULL)
        OR (dom IN ('uten_frist_akseptert', 'uten_frist_apen')
             AND reaper IS NULL
             AND fristkilde IS NULL
             AND frist_dogn IS NULL))
);

-- ------------------------------------------------------------
-- 1.2 `retensjonsmaaling` — én rad per KJØRING. Append-only.
--     `avbrutt` skiller «målt, ingenting å melde» fra «rakk ikke
--     ferdig». Raden fødes med avbrutt = true og blir bare false når
--     kjøringen faktisk lukker seg: et krasj midt i en måling kan
--     dermed ikke etterlate en rad som SER komplett ut.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS retensjonsmaaling (
    maaling_id       uuid PRIMARY KEY,
    startet_ts       timestamptz NOT NULL DEFAULT now(),
    fullfort_ts      timestamptz,
    antall_lagre     int NOT NULL DEFAULT 0,
    antall_umaalbare int NOT NULL DEFAULT 0,
    antall_funn      int NOT NULL DEFAULT 0,
    avbrutt          boolean NOT NULL DEFAULT true,
    -- Modulens EGEN retensjon: 400 døgn, reapet av
    -- `m4_reap_egne_maalinger`. Registeret fører sitt eget regnskap på
    -- samme form som alle andres — et retensjonsregister uten frist på
    -- seg selv er den første raden som skulle vært et funn.
    reapet_ts        timestamptz
);

CREATE INDEX IF NOT EXISTS retensjonsmaaling_startet
    ON retensjonsmaaling (startet_ts DESC);
CREATE INDEX IF NOT EXISTS retensjonsmaaling_ureapet
    ON retensjonsmaaling (startet_ts) WHERE reapet_ts IS NULL;

-- ------------------------------------------------------------
-- 1.3 `retensjonsstorrelse` — KATALOGTALL, ingen radlesing.
--     `pg_total_relation_size` og `pg_class.reltuples` krever ingen
--     tabellprivilegier og ingen seq scan. `rader_estimat` er ALLTID
--     merket estimat, i kolonnenavnet og på flaten: reltuples er
--     ANALYZE-ens siste gjetning, ikke en telling.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS retensjonsstorrelse (
    maaling_id    uuid NOT NULL REFERENCES retensjonsmaaling (maaling_id),
    lager_id      text NOT NULL REFERENCES retensjonslager (lager_id),
    maalt_ts      timestamptz NOT NULL DEFAULT now(),
    bytes_totalt  bigint,
    rader_estimat bigint,
    reapet_ts     timestamptz,
    PRIMARY KEY (maaling_id, lager_id)
);

-- ------------------------------------------------------------
-- 1.4 `retensjonsbeholdning` — den EKSAKTE tellingen, og bare der en
--     reap-markør er erklært. Tenant-tabell: RLS ENABLE + FORCE +
--     `tenant_isolasjon` på 088-formen. Globale lagre bokføres på
--     tenant '' (NOT NULL DEFAULT ''), slik at PK-en holder.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS retensjonsbeholdning (
    maaling_id        uuid NOT NULL REFERENCES retensjonsmaaling (maaling_id),
    lager_id          text NOT NULL REFERENCES retensjonslager (lager_id),
    tenant            text NOT NULL DEFAULT '',
    maalt_ts          timestamptz NOT NULL DEFAULT now(),
    rader             bigint,
    rader_ureapet     bigint,
    eldste_ureapet_ts timestamptz,
    sist_reapet_ts    timestamptz,
    reapet_ts         timestamptz,
    PRIMARY KEY (maaling_id, lager_id, tenant)
);

-- ------------------------------------------------------------
-- 1.5 `retensjonsfunn` — LUKKET SETT, ETT åpent funn per (lager,
--     funntype, tenant). Et funn som står ved lag oppdateres med
--     `sist_sett_maaling`; forsvinner grunnen, settes `lukket_maaling`.
--     Funnlisten er et bilde av NÅ, ikke en logg som vokser med
--     kadensen — en funnliste som vokser er en funnliste ingen leser.
--
--     INGEN RLS HER, og det er en dom: funnlisten er
--     PLATTFORMDRIFTENS, ikke en tenants. `tenant` sier hvilken
--     tenants rader funnet gjelder (for `reaper_henger`) — det er
--     ikke en eierskapsnøkkel. Listen når bare `platform:admin`,
--     gjennom en definer-dør runtime ikke kan omgå: det finnes
--     ingen SELECT-rettighet på tabellen å falle tilbake på.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS retensjonsfunn (
    funn_id          uuid PRIMARY KEY,
    lager_id         text NOT NULL,
    relasjon         text NOT NULL,
    tenant           text NOT NULL DEFAULT '',
    funntype         text NOT NULL
                     CHECK (funntype IN ('uregistrert', 'umaalbar',
                                         'uten_dom', 'reaper_mangler',
                                         'reaper_henger')),
    oppdaget_maaling uuid NOT NULL REFERENCES retensjonsmaaling (maaling_id),
    oppdaget_ts      timestamptz NOT NULL DEFAULT now(),
    sist_sett_maaling uuid NOT NULL REFERENCES retensjonsmaaling (maaling_id),
    lukket_maaling   uuid REFERENCES retensjonsmaaling (maaling_id),
    detalj           jsonb NOT NULL DEFAULT '{}'::jsonb
                     CHECK (jsonb_typeof(detalj) = 'object')
);

-- ETT åpent funn per (lager, funntype, tenant) — håndhevet, ikke ønsket.
--
-- TENANTEN ER MED I NØKKELEN, og det er en rettelse av den første formen
-- (CodeRabbit, alvorlig): `reaper_henger` er PER TENANT — det er tenant
-- A som har rader reaperen ikke kommer gjennom, ikke «lageret». Med en
-- nøkkel uten tenant ville funn nummer to overskrevet det første, og
-- funnlisten ville sagt at ÉN tenant henger når to gjør det. De
-- aggregerte funntypene (`uregistrert`, `umaalbar`, `uten_dom`,
-- `reaper_mangler`) bruker tenant '' og oppfører seg nøyaktig som før.
--
-- DROP før CREATE: en base som alt har den gamle formen skal få den nye
-- (SP-10 — migrasjonen må være grønn også mot en seedet base), og
-- `IF NOT EXISTS` ville stille latt den gamle stå.
DROP INDEX IF EXISTS retensjonsfunn_ett_apent;
CREATE UNIQUE INDEX retensjonsfunn_ett_apent
    ON retensjonsfunn (lager_id, funntype, tenant)
 WHERE lukket_maaling IS NULL;
CREATE INDEX IF NOT EXISTS retensjonsfunn_apne
    ON retensjonsfunn (funntype) WHERE lukket_maaling IS NULL;

RESET ROLE;

-- ============================================================
-- 2. VAKTENE PÅ REGISTERET. CHECKen over holder FORMEN; disse to
--    holder SANNHETEN mot basen: relasjonen og hver navngitt kolonne
--    må FINNES, og reaperen må ha et treff i pg_proc.
--
--    Vaktene er SECURITY DEFINER og eid av `disponit_lager_eier` fordi
--    de leser `information_schema.columns` og `pg_proc` på vegne av
--    hvem som helst som skriver registeret — og fordi eieren er den
--    eneste som HAR lov å skrive det.
-- ============================================================
SET LOCAL ROLE disponit_lager_eier;

CREATE OR REPLACE FUNCTION m4_lager_finnes_i_basen()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_reg oid; k TEXT;
BEGIN
    -- Kvalifisert til `public` FORDI funksjonen har
    -- `SET search_path = pg_catalog`: et ukvalifisert navn ville
    -- aldri slått opp, og hver eneste seedet rad ville sett ut som
    -- en relasjon som ikke finnes.
    v_reg := pg_catalog.to_regclass(pg_catalog.format('public.%I',
                                                      NEW.relasjon));
    IF v_reg IS NULL THEN
        RAISE EXCEPTION 'retensjonslager: relasjonen % finnes ikke —'
            ' et register som kan navngi et lager som ikke er der, gir'
            ' stille null ved neste måling', NEW.relasjon;
    END IF;
    FOREACH k IN ARRAY ARRAY[NEW.tenantkolonne, NEW.alderskolonne,
                             NEW.reapetkolonne] LOOP
        CONTINUE WHEN k IS NULL;
        IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_attribute a
             WHERE a.attrelid = v_reg AND a.attname = k
               AND a.attnum > 0 AND NOT a.attisdropped) THEN
            RAISE EXCEPTION 'retensjonslager: kolonnen %.% finnes ikke',
                NEW.relasjon, k;
        END IF;
    END LOOP;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION m4_reaper_finnes_i_basen()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.reaper IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc p
         WHERE p.proname = NEW.reaper) THEN
        RAISE EXCEPTION 'retensjonslager: reaperen % finnes ikke i'
            ' pg_proc — en reaper som ikke finnes er en løgn registeret'
            ' ikke skal kunne bære', NEW.reaper;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS retensjonslager_relasjon_finnes ON retensjonslager;
CREATE TRIGGER retensjonslager_relasjon_finnes
    BEFORE INSERT OR UPDATE ON retensjonslager
    FOR EACH ROW EXECUTE FUNCTION m4_lager_finnes_i_basen();

DROP TRIGGER IF EXISTS retensjonslager_reaper_finnes ON retensjonslager;
CREATE TRIGGER retensjonslager_reaper_finnes
    BEFORE INSERT OR UPDATE ON retensjonslager
    FOR EACH ROW EXECUTE FUNCTION m4_reaper_finnes_i_basen();

-- ------------------------------------------------------------
-- 2.1 APPEND-ONLY-VAKTENE på de tre målelagrene (079/088-formen:
--     vakten nekter DELETE og snevrer UPDATE til de to lovlige
--     overgangene — lukkingen av en kjøring, og reap-markeringen).
--     En måling som kunne redigeres i ettertid er ikke evidens.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION m4_maaling_vakt()
RETURNS TRIGGER LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'retensjonsmaaling er append-only —'
            ' en måling slettes aldri, den reapes';
    END IF;
    -- Lovlig overgang 1: LUKKINGEN. En åpen kjøring får skrevet sitt
    -- resultat én gang. Identiteten og starttidspunktet er låst.
    IF OLD.fullfort_ts IS NULL AND NEW.fullfort_ts IS NOT NULL
       AND NEW.maaling_id = OLD.maaling_id
       AND NEW.startet_ts = OLD.startet_ts
       AND NEW.reapet_ts IS NOT DISTINCT FROM OLD.reapet_ts THEN
        RETURN NEW;
    END IF;
    -- Lovlig overgang 2: REAP-MARKERINGEN, og bare den.
    IF OLD.reapet_ts IS NULL AND NEW.reapet_ts IS NOT NULL
       AND NEW.maaling_id = OLD.maaling_id
       AND NEW.startet_ts = OLD.startet_ts
       AND NEW.fullfort_ts IS NOT DISTINCT FROM OLD.fullfort_ts
       AND NEW.antall_lagre = OLD.antall_lagre
       AND NEW.antall_umaalbare = OLD.antall_umaalbare
       AND NEW.antall_funn = OLD.antall_funn
       AND NEW.avbrutt = OLD.avbrutt THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'retensjonsmaaling er append-only — en registrert'
        ' måling endres ikke (kun lukking og reap-markering er lovlige'
        ' overganger)';
END $$;

CREATE OR REPLACE FUNCTION m4_aggregat_vakt()
RETURNS TRIGGER LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '%: aggregatraden slettes aldri, den reapes',
            TG_TABLE_NAME;
    END IF;
    IF OLD.reapet_ts IS NULL AND NEW.reapet_ts IS NOT NULL THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION '%: aggregatraden er append-only — kun'
        ' reap-markering er en lovlig overgang', TG_TABLE_NAME;
END $$;

DROP TRIGGER IF EXISTS retensjonsmaaling_vakt ON retensjonsmaaling;
CREATE TRIGGER retensjonsmaaling_vakt
    BEFORE UPDATE OR DELETE ON retensjonsmaaling
    FOR EACH ROW EXECUTE FUNCTION m4_maaling_vakt();

DROP TRIGGER IF EXISTS retensjonsstorrelse_vakt ON retensjonsstorrelse;
CREATE TRIGGER retensjonsstorrelse_vakt
    BEFORE UPDATE OR DELETE ON retensjonsstorrelse
    FOR EACH ROW EXECUTE FUNCTION m4_aggregat_vakt();

DROP TRIGGER IF EXISTS retensjonsbeholdning_vakt ON retensjonsbeholdning;
CREATE TRIGGER retensjonsbeholdning_vakt
    BEFORE UPDATE OR DELETE ON retensjonsbeholdning
    FOR EACH ROW EXECUTE FUNCTION m4_aggregat_vakt();

RESET ROLE;

-- ============================================================
-- 3. FUNKSJONENE. Alle eid av `disponit_lager_eier`.
--
--    `m4_mal_lagre` er kryss-tenant på 038/057-formen: UTVALGET ER
--    PREDIKATET. Den tar ingen tenantparameter — kryss-tenant-autoriteten
--    er innelukket i funksjonen, ikke delegert til kalleren, og
--    `krev_tenantkontekst` gjelder derfor ikke her (samme unntak som
--    `reap_epostdata` og `reap_kandidatdata`). LESEDØRENE i §4 har
--    tenantparameter og kaller porten FØRST, som de skal.
--
--    ALL DYNAMISK SQL bygges med `format(%I)` fra registerets VERIFISERTE
--    identifikatorer — kolonnene triggerne i §2 har slått opp i
--    `pg_attribute`. Aldri fra et kallargument: `m4_mal_lagre` har to
--    argumenter, en INT-grense og et BOOLEAN-flagg, og ingen av dem når
--    noen gang en identifikator.
-- ============================================================
SET LOCAL ROLE disponit_lager_eier;

-- ------------------------------------------------------------
-- 3.1 Funnbokføringen. ETT åpent funn per (lager, funntype): et funn som
--     står ved lag oppdateres med `sist_sett_maaling` i stedet for å
--     føde en ny rad. En funnliste som vokser med kadensen er en
--     funnliste ingen leser.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION m4_registrer_funn(
    p_maaling UUID, p_lager TEXT, p_relasjon TEXT, p_tenant TEXT,
    p_funntype TEXT, p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    -- Oppslaget matcher på HELE nøkkelen, tenanten inkludert: to
    -- tenanter som henger på samme lager er to funn, ikke ett som
    -- overskriver det andre. `detalj` oppdateres MED — et åpent funn
    -- skal beskrive SISTE observasjon. `oppdaget_ts` og
    -- `oppdaget_maaling` står derimot urørt: de er funnets ALDER, og den
    -- er hele grunnen til at et gammelt funn er verre enn et nytt.
    UPDATE public.retensjonsfunn f
       SET sist_sett_maaling = p_maaling,
           detalj = coalesce(p_detalj, '{}'::jsonb)
     WHERE f.lager_id = p_lager AND f.funntype = p_funntype
       AND f.tenant = coalesce(p_tenant, '')
       AND f.lukket_maaling IS NULL;
    IF NOT FOUND THEN
        INSERT INTO public.retensjonsfunn (
            funn_id, lager_id, relasjon, tenant, funntype,
            oppdaget_maaling, sist_sett_maaling, detalj)
        VALUES (gen_random_uuid(), p_lager, p_relasjon,
                coalesce(p_tenant, ''), p_funntype, p_maaling, p_maaling,
                coalesce(p_detalj, '{}'::jsonb));
    END IF;
END $$;

-- ------------------------------------------------------------
-- 3.1b Registerets KOLONNENAVN, for tabelleieren. Kolonnegrantene i §6.3
--      må deles ut av den som EIER de målte tabellene (migrator), men
--      registeret eies av `disponit_lager_eier` — og migrator skal ikke
--      ha lesetilgang til en tabell den ikke trenger. Døren her gir
--      NAVN, aldri en rad fra et målt lager, og gjør at grantlisten er
--      UTLEDET av registeret i stedet for skrevet for hånd ved siden av
--      det.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION m4_registerkolonner()
RETURNS TABLE (relasjon TEXT, tenantkolonne TEXT, alderskolonne TEXT,
               reapetkolonne TEXT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT l.relasjon, l.tenantkolonne, l.alderskolonne, l.reapetkolonne
      FROM public.retensjonslager l
     WHERE l.reapetkolonne IS NOT NULL
     ORDER BY l.lager_id
$$;
REVOKE ALL ON FUNCTION m4_registerkolonner() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION m4_registerkolonner() TO disponit_migrator;

-- ------------------------------------------------------------
-- 3.2 Modulens ENESTE sletting: sine egne aggregatrader, etter 400 døgn.
--     Reapen er SOFT på husets dominerende form (082/088): markøren
--     settes og tallene nullstilles, raden består. Ingen DELETE, ingen
--     TRUNCATE, og ingen tabell utenfor `retensjons*` røres.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION m4_reap_egne_maalinger(p_grense INT DEFAULT 50)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_naa TIMESTAMPTZ; v_n INT := 0; v_frist NUMERIC;
BEGIN
    v_naa := pg_catalog.now();
    SELECT l.frist_dogn INTO v_frist FROM public.retensjonslager l
     WHERE l.lager_id = 'retensjonsmaaling';
    IF v_frist IS NULL THEN
        -- Registeret er kilden til sin egen frist. Står raden ikke der,
        -- reaper vi ikke på en antatt frist — vi lar være.
        RETURN 0;
    END IF;
    FOR r IN
        SELECT m.maaling_id AS mid FROM public.retensjonsmaaling m
         WHERE m.reapet_ts IS NULL
           AND v_naa > m.startet_ts + v_frist * interval '1 day'
         ORDER BY m.startet_ts
         LIMIT greatest(least(coalesce(p_grense, 50), 500), 0)
         FOR UPDATE OF m SKIP LOCKED
    LOOP
        UPDATE public.retensjonsstorrelse s
           SET bytes_totalt = NULL, rader_estimat = NULL,
               reapet_ts = v_naa
         WHERE s.maaling_id = r.mid AND s.reapet_ts IS NULL;
        UPDATE public.retensjonsbeholdning b
           SET rader = NULL, rader_ureapet = NULL,
               eldste_ureapet_ts = NULL, sist_reapet_ts = NULL,
               reapet_ts = v_naa
         WHERE b.maaling_id = r.mid AND b.reapet_ts IS NULL;
        UPDATE public.retensjonsmaaling m2
           SET reapet_ts = v_naa
         WHERE m2.maaling_id = r.mid;
        v_n := v_n + 1;
    END LOOP;
    RETURN v_n;
END $$;

-- ------------------------------------------------------------
-- 3.3 MÅLINGEN. Ett kall = ett skritt, slik at KALLEREN kan sette
--     `statement_timeout` per lager og committe hvert skritt for seg.
--     Det er ikke et stilvalg: `statement_timeout` gjelder den ytterste
--     setningen, så en per-lager-grense kan ikke bo inne i en plpgsql-
--     løkke. Timeout-veien er nettopp den som skal gi FUNN, og da må
--     den kunne treffe ett lager om gangen.
--
--     `p_umaalbar = true` bokfører NESTE umålte lager som `umaalbar` og
--     går videre. Kalleren trenger derfor ikke å vite HVILKET lager som
--     nettopp feilet — den vet bare at det feilet, og funksjonen velger
--     med SAMME rekkefølge som målingen selv. Regelen som bærer modulen
--     bor her: et lager som ikke kunne måles blir et FUNN, aldri en null.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION m4_mal_lagre(p_grense INT DEFAULT 50,
                                        p_umaalbar BOOLEAN DEFAULT false)
RETURNS TABLE (maaling_id UUID, lager_id TEXT, utfall TEXT,
               ferdig BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_m UUID; v_grense INT; r RECORD; b RECORD; c RECORD;
        v_reg oid; v_sql TEXT; v_tenantuttrykk TEXT; v_n INT := 0;
BEGIN
    v_grense := greatest(least(coalesce(p_grense, 50), 50), 0);

    -- Åpen kjøring, ellers ny. `avbrutt` fødes TRUE: en kjøring som
    -- aldri lukker seg forblir ærlig avbrutt.
    SELECT m.maaling_id INTO v_m FROM public.retensjonsmaaling m
     WHERE m.fullfort_ts IS NULL ORDER BY m.startet_ts DESC LIMIT 1;
    IF v_m IS NULL THEN
        v_m := gen_random_uuid();
        INSERT INTO public.retensjonsmaaling (maaling_id) VALUES (v_m);
    END IF;

    IF p_umaalbar THEN
        SELECT * INTO r FROM public.retensjonslager l
         WHERE NOT EXISTS (SELECT 1 FROM public.retensjonsstorrelse s
                            WHERE s.maaling_id = v_m
                              AND s.lager_id = l.lager_id)
           AND NOT EXISTS (SELECT 1 FROM public.retensjonsfunn f
                            WHERE f.lager_id = l.lager_id
                              AND f.funntype = 'umaalbar'
                              AND f.sist_sett_maaling = v_m
                              AND f.lukket_maaling IS NULL)
         ORDER BY (SELECT max(s2.maalt_ts)
                     FROM public.retensjonsstorrelse s2
                    WHERE s2.lager_id = l.lager_id) NULLS FIRST,
                  l.lager_id
         LIMIT 1;
        IF NOT FOUND THEN
            RETURN QUERY SELECT v_m, NULL::text, 'ingen_igjen'::text, false;
            RETURN;
        END IF;
        PERFORM public.m4_registrer_funn(
            v_m, r.lager_id, r.relasjon, '', 'umaalbar',
            jsonb_build_object('grunn', 'maaling_avbrutt_av_kaller'));
        RETURN QUERY SELECT v_m, r.lager_id, 'umaalbar'::text, false;
        RETURN;
    END IF;

    FOR r IN
        SELECT * FROM public.retensjonslager l
         WHERE NOT EXISTS (SELECT 1 FROM public.retensjonsstorrelse s
                            WHERE s.maaling_id = v_m
                              AND s.lager_id = l.lager_id)
           AND NOT EXISTS (SELECT 1 FROM public.retensjonsfunn f
                            WHERE f.lager_id = l.lager_id
                              AND f.funntype = 'umaalbar'
                              AND f.sist_sett_maaling = v_m
                              AND f.lukket_maaling IS NULL)
         ORDER BY (SELECT max(s2.maalt_ts)
                     FROM public.retensjonsstorrelse s2
                    WHERE s2.lager_id = l.lager_id) NULLS FIRST,
                  l.lager_id
         LIMIT v_grense
    LOOP
        v_n := v_n + 1;
        v_reg := pg_catalog.to_regclass(
            pg_catalog.format('public.%I', r.relasjon));
        IF v_reg IS NULL THEN
            -- Relasjonen er borte SIDEN registeret ble skrevet. Det er
            -- et funn, ikke en null — og ikke et krasj.
            PERFORM public.m4_registrer_funn(
                v_m, r.lager_id, r.relasjon, '', 'umaalbar',
                jsonb_build_object('grunn', 'relasjon_finnes_ikke'));
            RETURN QUERY SELECT v_m, r.lager_id, 'umaalbar'::text, false;
            CONTINUE;
        END IF;

        -- Reaperen kan ha forsvunnet i en senere migrasjon. Registeret
        -- lyver da uten å vite det — så det skal STÅ.
        IF r.reaper IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_proc p
                 WHERE p.proname = r.reaper) THEN
            PERFORM public.m4_registrer_funn(
                v_m, r.lager_id, r.relasjon, '', 'reaper_mangler',
                jsonb_build_object('reaper', r.reaper));
        END IF;

        -- En ÅPEN dom er selve grunnen modulen finnes: lageret er kjent,
        -- men ingen har skrevet hva som gjelder.
        IF r.dom = 'uten_frist_apen' THEN
            PERFORM public.m4_registrer_funn(
                v_m, r.lager_id, r.relasjon, '', 'uten_dom',
                jsonb_build_object('klasse', r.klasse));
        END IF;

        -- KATALOGTALL. Ingen radlesing, ingen tabellprivilegier.
        -- `rader_estimat` er `reltuples` — ANALYZE-ens siste gjetning,
        -- og den heter estimat i kolonnenavnet fordi den ER det.
        INSERT INTO public.retensjonsstorrelse (
            maaling_id, lager_id, bytes_totalt, rader_estimat)
        SELECT v_m, r.lager_id,
               pg_catalog.pg_total_relation_size(v_reg),
               nullif(c2.reltuples, -1)::bigint
          FROM pg_catalog.pg_class c2 WHERE c2.oid = v_reg;

        -- BEHOLDNINGEN måles KUN der en reap-markør er erklært. Uten en
        -- kolonne som sier hva som ER reapet, finnes det ikke noe
        -- ureapet å telle — og da skal radene ikke røres i det hele
        -- tatt.
        IF r.reapetkolonne IS NULL THEN
            RETURN QUERY SELECT v_m, r.lager_id, 'malt'::text, false;
            CONTINUE;
        END IF;
        v_tenantuttrykk := CASE WHEN r.tenantkolonne IS NULL
                                THEN '''''::text'
                                ELSE format('%I', r.tenantkolonne) END;
        v_sql := format(
            'SELECT %s AS t, count(*)::bigint AS rader,'
            ' count(*) FILTER (WHERE %I IS NULL)::bigint AS ureapet,'
            ' min(%I) FILTER (WHERE %I IS NULL) AS eldste,'
            ' max(%I) AS sist'
            '  FROM %s GROUP BY 1',
            v_tenantuttrykk, r.reapetkolonne, r.alderskolonne,
            r.reapetkolonne, r.reapetkolonne, v_reg::regclass::text);
        FOR b IN EXECUTE v_sql LOOP
            INSERT INTO public.retensjonsbeholdning (
                maaling_id, lager_id, tenant, rader, rader_ureapet,
                eldste_ureapet_ts, sist_reapet_ts)
            VALUES (v_m, r.lager_id, coalesce(b.t, ''), b.rader,
                    b.ureapet, b.eldste, b.sist)
                -- ON CONSTRAINT, ikke kolonnelisten: OUT-parametrene
                -- til denne funksjonen heter det samme som kolonnene,
                -- og en ukvalifisert konfliktliste er tvetydig.
                ON CONFLICT ON CONSTRAINT retensjonsbeholdning_pkey
                   DO NOTHING;
            -- REAPEREN HENGER: en rad som er eldre enn fristen pluss en
            -- ukes slakk, og fortsatt ureapet, betyr at reaperen ikke
            -- kommer gjennom. Det er den ene tilstanden et
            -- retensjonsregnskap finnes for å oppdage.
            IF r.dom = 'under_frist' AND r.frist_dogn IS NOT NULL
               AND b.eldste IS NOT NULL
               AND b.eldste < pg_catalog.now()
                   - (r.frist_dogn + 7) * interval '1 day' THEN
                PERFORM public.m4_registrer_funn(
                    v_m, r.lager_id, r.relasjon, coalesce(b.t, ''),
                    'reaper_henger',
                    jsonb_build_object('reaper', r.reaper,
                                       'frist_dogn', r.frist_dogn,
                                       'eldste_ureapet_ts', b.eldste));
            END IF;
        END LOOP;
        RETURN QUERY SELECT v_m, r.lager_id, 'malt'::text, false;
    END LOOP;

    IF v_n > 0 THEN
        RETURN;                     -- flere lagre igjen, kjøringen står åpen
    END IF;

    -- ------------------------------------------------------------
    -- INGEN LAGRE IGJEN → siste skritt: katalogsveipen, lukking av funn
    -- som ikke lenger står, og lukkingen av selve kjøringen.
    -- ------------------------------------------------------------
    FOR c IN
        SELECT cl.relname::text AS navn
          FROM pg_catalog.pg_class cl
          JOIN pg_catalog.pg_namespace ns ON ns.oid = cl.relnamespace
         WHERE ns.nspname = 'public' AND cl.relkind IN ('r', 'p')
           AND NOT EXISTS (
               SELECT 1 FROM public.retensjonslager l
                WHERE pg_catalog.to_regclass(
                          pg_catalog.format('public.%I', l.relasjon))
                      = cl.oid)
         ORDER BY cl.relname
    LOOP
        -- UREGISTRERT: et lager i katalogen uten en skrevet dom. Ikke en
        -- feil i modulen — modulens hele poeng.
        PERFORM public.m4_registrer_funn(
            v_m, 'uregistrert:' || c.navn, c.navn, '', 'uregistrert',
            jsonb_build_object('relasjon', c.navn));
    END LOOP;

    -- Funn som ikke ble sett i denne kjøringen har ikke lenger en grunn.
    UPDATE public.retensjonsfunn f
       SET lukket_maaling = v_m
     WHERE f.lukket_maaling IS NULL AND f.sist_sett_maaling <> v_m;

    UPDATE public.retensjonsmaaling m
       SET fullfort_ts = pg_catalog.now(),
           avbrutt = false,
           antall_lagre = (SELECT count(*) FROM public.retensjonsstorrelse s
                            WHERE s.maaling_id = v_m),
           antall_umaalbare = (SELECT count(*) FROM public.retensjonsfunn f2
                                WHERE f2.sist_sett_maaling = v_m
                                  AND f2.funntype = 'umaalbar'
                                  AND f2.lukket_maaling IS NULL),
           antall_funn = (SELECT count(*) FROM public.retensjonsfunn f3
                           WHERE f3.lukket_maaling IS NULL)
     WHERE m.maaling_id = v_m;

    -- Modulens egen retensjon kjøres av modulens egen kjøring: rollen
    -- har EXECUTE på NØYAKTIG én funksjon, og en reaper ingen kaller er
    -- en frist ingen holder.
    PERFORM public.m4_reap_egne_maalinger(50);

    RETURN QUERY SELECT v_m, NULL::text, 'ferdig'::text, true;
END $$;

-- ============================================================
-- 4. LESEDØRENE. Runtime når registeret og målingene KUN gjennom disse
--    — ingen tabellrettigheter, 090/091-formen. Hver av dem kaller
--    `krev_tenantkontekst` FØRST (SP-1/051-leseformen).
--
--    SNITTET er delt i to av en grunn som er konkret: `platform:admin`
--    står ikke i `LESESCOPES` (app.py), og en browserøkt mot et slikt
--    scope avvises — en rute deklarert `platform:admin` ville gitt 403
--    for hver innlogging. Ruten er derfor `security:read`, og
--    kontrollplanet avgjøres INNE i endepunktet (`/v1/utrulling`-
--    presedensen). Dørene speiler det: `m4_retensjonsbilde` er
--    øktens eget, `m4_retensjonskatalog` og `m4_retensjonsfunn` er
--    kontrollplanets.
-- ============================================================

CREATE OR REPLACE FUNCTION m4_siste_maaling(p_tenant TEXT)
RETURNS TABLE (maaling_id UUID, startet_ts TIMESTAMPTZ,
               fullfort_ts TIMESTAMPTZ, avbrutt BOOLEAN,
               antall_lagre INT, antall_umaalbare INT, antall_funn INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm4_siste_maaling');
    RETURN QUERY
    SELECT m.maaling_id, m.startet_ts, m.fullfort_ts, m.avbrutt,
           m.antall_lagre, m.antall_umaalbare, m.antall_funn
      FROM public.retensjonsmaaling m
     ORDER BY m.startet_ts DESC
     LIMIT 1;
END $$;

-- Registeret + ØKTENS EGEN beholdning. Ingen bytes, ingen andre
-- tenanters rader — den delen er kontrollplanets.
CREATE OR REPLACE FUNCTION m4_retensjonsbilde(p_tenant TEXT)
RETURNS TABLE (lager_id TEXT, relasjon TEXT, klasse TEXT,
               tenantkolonne TEXT, alderskolonne TEXT,
               reapetkolonne TEXT, fristkilde TEXT, frist_dogn NUMERIC,
               reaper TEXT, dom TEXT, dom_begrunnelse TEXT,
               dom_migrasjon TEXT, rader BIGINT, rader_ureapet BIGINT,
               eldste_ureapet_ts TIMESTAMPTZ, sist_reapet_ts TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_m UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm4_retensjonsbilde');
    SELECT m.maaling_id INTO v_m FROM public.retensjonsmaaling m
     ORDER BY m.startet_ts DESC LIMIT 1;
    RETURN QUERY
    SELECT l.lager_id, l.relasjon, l.klasse, l.tenantkolonne,
           l.alderskolonne, l.reapetkolonne, l.fristkilde, l.frist_dogn,
           l.reaper, l.dom, l.dom_begrunnelse, l.dom_migrasjon,
           b.rader, b.rader_ureapet, b.eldste_ureapet_ts,
           b.sist_reapet_ts
      FROM public.retensjonslager l
      LEFT JOIN public.retensjonsbeholdning b
             ON b.maaling_id = v_m AND b.lager_id = l.lager_id
            AND b.tenant = CASE WHEN l.tenantkolonne IS NULL
                                THEN '' ELSE p_tenant END
     ORDER BY l.lager_id;
END $$;

-- KONTROLLPLANET: katalogtallene og alle tenanters beholdning.
CREATE OR REPLACE FUNCTION m4_retensjonskatalog(p_tenant TEXT)
RETURNS TABLE (lager_id TEXT, bytes_totalt BIGINT, rader_estimat BIGINT,
               tenant TEXT, rader BIGINT, rader_ureapet BIGINT,
               eldste_ureapet_ts TIMESTAMPTZ, sist_reapet_ts TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_m UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm4_retensjonskatalog');
    SELECT m.maaling_id INTO v_m FROM public.retensjonsmaaling m
     ORDER BY m.startet_ts DESC LIMIT 1;
    RETURN QUERY
    SELECT s.lager_id, s.bytes_totalt, s.rader_estimat,
           b.tenant, b.rader, b.rader_ureapet, b.eldste_ureapet_ts,
           b.sist_reapet_ts
      FROM public.retensjonsstorrelse s
      LEFT JOIN public.retensjonsbeholdning b
             ON b.maaling_id = s.maaling_id AND b.lager_id = s.lager_id
     WHERE s.maaling_id = v_m
     ORDER BY s.lager_id, b.tenant;
END $$;

-- KONTROLLPLANET: hele funnlisten, åpne funn først.
CREATE OR REPLACE FUNCTION m4_retensjonsfunn(p_tenant TEXT)
RETURNS TABLE (funn_id UUID, lager_id TEXT, relasjon TEXT, tenant TEXT,
               funntype TEXT, oppdaget_ts TIMESTAMPTZ,
               oppdaget_maaling UUID, sist_sett_maaling UUID,
               detalj JSONB)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm4_retensjonsfunn');
    RETURN QUERY
    SELECT f.funn_id, f.lager_id, f.relasjon, f.tenant, f.funntype,
           f.oppdaget_ts, f.oppdaget_maaling, f.sist_sett_maaling,
           f.detalj
      FROM public.retensjonsfunn f
     WHERE f.lukket_maaling IS NULL
     ORDER BY f.funntype, f.lager_id;
END $$;

RESET ROLE;

-- ============================================================
-- 5. SEEDINGEN — de lagrene som FAKTISK står under frist i dag, lest ut
--    av reaperkroppene slik de er nå: `rydd_staged_artefakter` (016→019),
--    `reap_evidensfrister` (038→043→056→067), `reap_kandidatdata`
--    (057→067→069→073→075→082, gjeldende kropp i 082),
--    `makuler_artefakter_for_prosess` (067→072), `reap_epostdata` (088)
--    og `slett_ubrukt_policy` (032).
--
--    ALT ANNET I KATALOGEN oppstår som `uregistrert`-funn ved første
--    kjøring. Det er ikke en mangel ved seedingen — det ER modulen:
--    et lager uten skrevet dom skal være synlig, ikke stille.
--
--    Hver rad er VERIFISERT mot den faktiske kolonnen. Triggerne i §2
--    feller en rad som ikke er det, og det er meningen.
--
--    TO ÆRLIGE PRESISERINGER som står i begrunnelsene, ikke bare her:
--      * `reap_evidensfrister` er IKKE en sletter. Den lukker
--        rekrutteringsprosessen slik at 082 måler fra lukkingen i stedet
--        for å falle til forlatt-fallbacken. Den gir derfor INGEN rad —
--        en rad for den ville vært et falskt lager.
--      * `frist_dogn` er STANDARDVERDIEN der fristen står i en radkolonne
--        (`slettefrist_dogn`, default 90, CHECK 30–365). `fristkilde`
--        navngir kolonnen, så ingen leser tallet som en fast frist.
-- ============================================================
SET LOCAL ROLE disponit_lager_eier;

INSERT INTO retensjonslager (
    lager_id, relasjon, klasse, tenantkolonne, alderskolonne,
    reapetkolonne, fristkilde, frist_dogn, reaper, dom, dom_begrunnelse,
    dom_migrasjon)
VALUES
    -- ---- 072: artefaktmakuleringen. `artefakt` har INGEN `slettet_ts`
    -- — markøren heter `makulert_ts` (067). Merk at 016/019s
    -- staged-rydding og 072s gren B lar markøren stå NULL og bruker
    -- `tilstand='forkastet'` som eneste spor: beholdningstallet under
    -- teller derfor de MAKULERTE, og en forkastet staged-rad står som
    -- ureapet til den ryddes. Det er den ærlige lesningen — ikke en
    -- penere.
    ('artefakt', 'artefakt', 'evidens', 'tenant', 'opprettet',
     'makulert_ts', 'rekrutteringsprosess.slettefrist_dogn (072) og'
     ' 24 t staged-rydding (016/019)', 90,
     'makuler_artefakter_for_prosess', 'under_frist',
     'Artefakter makuleres når prosessen reapes (072), og staged'
     ' artefakter uten kvittering ryddes etter 24 t (016/019).'
     ' Payloadfrie artefakttyper makuleres aldri — 072s eget unntak.',
     '093'),
    -- ---- 082: kandidatfamilien. Fristen står i
    -- `rekrutteringsprosess.slettefrist_dogn`; barnelagrene ARVER den og
    -- måles på sin egen `opprettet` fordi det er den kolonnen de HAR.
    ('rekrutteringsprosess', 'rekrutteringsprosess', 'persondata',
     'tenant', 'opprettet', 'slettet_ts',
     'rekrutteringsprosess.slettefrist_dogn', 90, 'reap_kandidatdata',
     'under_frist',
     'Reapes av 082 når now() > coalesce(lukket_ts, opprettet) +'
     ' slettefrist_dogn. Registeret måler fra `opprettet` fordi det er'
     ' den kolonnen som ALLTID finnes — «eldste ureapet» blir dermed et'
     ' konservativt tall, aldri et for gunstig.', '093'),
    ('kandidat', 'kandidat', 'persondata', 'tenant', 'opprettet',
     'slettet_ts', 'rekrutteringsprosess.slettefrist_dogn', 90,
     'reap_kandidatdata', 'under_frist',
     'Kandidatankeret (075) reapes i samme transaksjon som prosessen'
     ' — 082 tømmer hele familien samlet.', '093'),
    ('kandidat_originaldokument', 'kandidat_originaldokument',
     'persondata', 'tenant', 'opprettet', 'slettet_ts',
     'rekrutteringsprosess.slettefrist_dogn', 90, 'reap_kandidatdata',
     'under_frist',
     'Payload nullstilles av 082 sammen med resten av familien.', '093'),
    ('kandidat_parsettekst', 'kandidat_parsettekst', 'persondata',
     'tenant', 'opprettet', 'slettet_ts',
     'rekrutteringsprosess.slettefrist_dogn', 90, 'reap_kandidatdata',
     'under_frist',
     'Payload nullstilles av 082 sammen med resten av familien.', '093'),
    ('kandidat_evalueringsartefakt', 'kandidat_evalueringsartefakt',
     'persondata', 'tenant', 'opprettet', 'slettet_ts',
     'rekrutteringsprosess.slettefrist_dogn', 90, 'reap_kandidatdata',
     'under_frist',
     'Payload nullstilles av 082 sammen med resten av familien.', '093'),
    ('kandidat_intervjusporsmal', 'kandidat_intervjusporsmal',
     'persondata', 'tenant', 'opprettet', 'slettet_ts',
     'rekrutteringsprosess.slettefrist_dogn', 90, 'reap_kandidatdata',
     'under_frist',
     'Payload nullstilles av 082 sammen med resten av familien.', '093'),
    ('kandidat_utsendingsdata', 'kandidat_utsendingsdata', 'persondata',
     'tenant', 'opprettet', 'slettet_ts',
     'rekrutteringsprosess.slettefrist_dogn', 90, 'reap_kandidatdata',
     'under_frist',
     'Payload nullstilles av 082 sammen med resten av familien.', '093'),
    ('kandidat_avmaskering', 'kandidat_avmaskering', 'persondata',
     'tenant', 'opprettet', 'slettet_ts',
     'rekrutteringsprosess.slettefrist_dogn', 90, 'reap_kandidatdata',
     'under_frist',
     'Avmaskeringssporet nullstilles av 082 sammen med familien.',
     '093'),
    ('m8_slotvalg', 'm8_slotvalg', 'persondata', 'tenant', 'opprettet',
     'slettet_ts', 'rekrutteringsprosess.slettefrist_dogn', 90,
     'reap_kandidatdata', 'under_frist',
     'M-8s tidsvalg reapes med kandidatfamilien (082) — valget er'
     ' kandidatens data, ikke en driftslogg.', '093'),
    -- ---- 088: e-postfamilien. Fristen står i
    -- `epost_melding.slettefrist_dogn`; barnelagrene arver den.
    ('epost_melding', 'epost_melding', 'persondata', 'tenant',
     'mottatt_ts', 'slettet_ts', 'epost_melding.slettefrist_dogn', 90,
     'reap_epostdata', 'under_frist',
     'Reapes av 088 når now() > mottatt_ts + slettefrist_dogn. Alle'
     ' payloadlagrene tømmes i SAMME transaksjon som meldingsmerket —'
     ' den utsatte samlet-porten gjør delvis reap urepresenterbart.',
     '093'),
    ('epost_vedlegg', 'epost_vedlegg', 'persondata', 'tenant',
     'opprettet', 'slettet_ts', 'epost_melding.slettefrist_dogn', 90,
     'reap_epostdata', 'under_frist',
     'Payload nullstilles av 088 sammen med meldingen.', '093'),
    ('epost_klassifisering', 'epost_klassifisering', 'persondata',
     'tenant', 'opprettet', 'slettet_ts',
     'epost_melding.slettefrist_dogn', 90, 'reap_epostdata',
     'under_frist',
     'Payload nullstilles av 088 sammen med meldingen.', '093'),
    ('epost_utkast', 'epost_utkast', 'persondata', 'tenant', 'opprettet',
     'slettet_ts', 'epost_melding.slettefrist_dogn', 90,
     'reap_epostdata', 'under_frist',
     'Payload nullstilles av 088 sammen med meldingen.', '093'),
    -- ---- 032: den eneste FYSISKE slettingen i huset — og den eneste
    -- uten en tidsdimensjon i det hele tatt. Dommen er derfor ikke
    -- «under frist», men «bevisst uten frist».
    ('policyer', 'policyer', 'konfigurasjon', 'tenant', 'opprettet',
     NULL, NULL, NULL, NULL, 'uten_frist_akseptert',
     'Policyversjoner slettes av `slett_ubrukt_policy` (032) på'
     ' OPERATØRENS initiativ, med optimistisk lås på versjon og'
     ' innholdshash, og bare når versjonen er ubrukt. Det er ingen'
     ' tidsfrist og skal ikke være det: en policyversjon som har vært i'
     ' kraft er evidens for beslutningene tatt under den.', '093'),
    -- ---- M-4s EGNE tre målelagre. Registeret fører sitt eget regnskap
    -- på samme form som alle andres — 400 døgn, reapet av modulens
    -- ENESTE slettevei. Et retensjonsregister uten frist på seg selv
    -- ville vært den første raden som skulle stått som funn.
    ('retensjonsmaaling', 'retensjonsmaaling', 'driftsspor', NULL,
     'startet_ts', 'reapet_ts', 'M-4 v1: 400 døgn (093)', 400,
     'm4_reap_egne_maalinger', 'under_frist',
     'Målingens hode beholdes i 400 døgn — ett år pluss margin, så en'
     ' årlig gjennomgang alltid har fjorårets bilde. Reapes av'
     ' m4_reap_egne_maalinger, som er modulens eneste skriving utenfor'
     ' innsetting.', '093'),
    ('retensjonsstorrelse', 'retensjonsstorrelse', 'driftsspor', NULL,
     'maalt_ts', 'reapet_ts', 'M-4 v1: 400 døgn (093)', 400,
     'm4_reap_egne_maalinger', 'under_frist',
     'Katalogtallene reapes med målingen de hører til.', '093'),
    ('retensjonsbeholdning', 'retensjonsbeholdning', 'driftsspor',
     'tenant', 'maalt_ts', 'reapet_ts', 'M-4 v1: 400 døgn (093)', 400,
     'm4_reap_egne_maalinger', 'under_frist',
     'Beholdningstallene reapes med målingen de hører til.', '093')
ON CONFLICT (lager_id) DO NOTHING;

RESET ROLE;

-- ============================================================
-- 6. RETTIGHETENE. Dette er hele sikkerhetsargumentet for modulen, og
--    det er en egenskap ved BASEN — ikke ved disiplinen i koden.
--
--    * `disponit_lagermaaler` (LOGIN) har NULL tabellrettigheter i hele
--      basen og EXECUTE på NØYAKTIG én funksjon.
--    * `disponit_lager_eier` (NOLOGIN) eier retensjons*-tabellene og har
--      KOLONNEGRANT — `SELECT (tenant, alderskolonne, reapetkolonne)` —
--      på hvert målt lager. Payloadkolonnene er UGRANTEDE. En måler som
--      ikke KAN lese `ciphertext` trenger ingen regel om å la være.
--    * Kryss-tenant er en EKSPLISITT POLICY per målt tabell
--      (`CURRENT_USER = 'disponit_lager_eier'`), aldri BYPASSRLS —
--      005/057/088s valg, ordrett.
--
--    GRANTENE OG POLICYENE UTLEDES AV REGISTERET SELV. Det er ikke en
--    bekvemmelighet: en håndskrevet liste kan komme ut av synk med
--    registeret, og da måler porten kildeteksten i stedet for basen.
-- ============================================================

-- 6.1 Tenant-isolasjon på beholdningen — 088-formen, ordrett.
--     Målerens egen policy må stå ved siden av: FORCE ROW LEVEL SECURITY
--     gjelder OGSÅ EIEREN, så uten den kunne definerveiene ikke skrive
--     eller lese sine egne aggregater.
SET LOCAL ROLE disponit_lager_eier;
ALTER TABLE retensjonsbeholdning ENABLE ROW LEVEL SECURITY;
ALTER TABLE retensjonsbeholdning FORCE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                    WHERE schemaname = 'public'
                      AND tablename = 'retensjonsbeholdning'
                      AND policyname = 'tenant_isolasjon') THEN
        EXECUTE 'CREATE POLICY tenant_isolasjon ON retensjonsbeholdning
                    USING      (tenant = current_setting(''disponit.tenant'', true))
                    WITH CHECK (tenant = current_setting(''disponit.tenant'', true))';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                    WHERE schemaname = 'public'
                      AND tablename = 'retensjonsbeholdning'
                      AND policyname = 'm4_maaler') THEN
        EXECUTE 'CREATE POLICY m4_maaler ON retensjonsbeholdning
                    TO disponit_lager_eier
                    USING      (CURRENT_USER = ''disponit_lager_eier'')
                    WITH CHECK (CURRENT_USER = ''disponit_lager_eier'')';
    END IF;
END $$;

-- 6.2 Målerrollens ENESTE rettighet i hele basen.
REVOKE ALL ON FUNCTION m4_mal_lagre(INT, BOOLEAN) FROM PUBLIC;
REVOKE ALL ON FUNCTION m4_reap_egne_maalinger(INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION m4_registrer_funn(UUID, TEXT, TEXT, TEXT, TEXT, JSONB)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION m4_siste_maaling(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION m4_retensjonsbilde(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION m4_retensjonskatalog(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION m4_retensjonsfunn(TEXT) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_lagermaaler') THEN
        -- EXECUTE på en SECURITY DEFINER-funksjon i public krever USAGE
        -- på skjemaet, så den ene linjen står — men ikke én tabell.
        GRANT USAGE ON SCHEMA public TO disponit_lagermaaler;
        GRANT EXECUTE ON FUNCTION m4_mal_lagre(INT, BOOLEAN)
            TO disponit_lagermaaler;
        -- OG INGENTING MER. `m4_reap_egne_maalinger` kalles av
        -- `m4_mal_lagre` når kjøringen lukker seg: en reaper med sin
        -- egen inngang er en slettevei til, og modulen skal ha
        -- nøyaktig én.
    END IF;
END $$;
RESET ROLE;

-- 6.3 KOLONNEGRANTENE og kryss-tenant-policyene på de MÅLTE lagrene.
--     Gis av tabelleierne (migrator), utledet av registeret.
DO $$
DECLARE r RECORD; v_kol TEXT; v_reg oid;
BEGIN
    FOR r IN
        SELECT k.relasjon, k.tenantkolonne, k.alderskolonne,
               k.reapetkolonne
          FROM m4_registerkolonner() k
    LOOP
        v_reg := to_regclass(format('public.%I', r.relasjon));
        CONTINUE WHEN v_reg IS NULL;
        -- Eierens egne tabeller trenger verken grant eller policy:
        -- eierskapet ER tilgangen, og m4_maaler-policyen står alt på
        -- beholdningen (5.1).
        CONTINUE WHEN (SELECT c.relowner FROM pg_class c
                        WHERE c.oid = v_reg)
                      = (SELECT oid FROM pg_roles
                          WHERE rolname = 'disponit_lager_eier');
        -- KOLONNEGRANT, aldri tabellgrant. Nøyaktig de tre kolonnene
        -- registeret navngir — og ingen payloadkolonne kan snike seg
        -- inn, fordi listen ikke er skrevet for hånd.
        v_kol := concat_ws(', ',
            quote_ident(r.alderskolonne),
            quote_ident(r.reapetkolonne),
            CASE WHEN r.tenantkolonne IS NULL THEN NULL
                 ELSE quote_ident(r.tenantkolonne) END);
        EXECUTE format('GRANT SELECT (%s) ON %s TO disponit_lager_eier',
                       v_kol, v_reg::regclass::text);
        -- KRYSS-TENANT SOM EKSPLISITT POLICY, aldri BYPASSRLS.
        IF NOT EXISTS (SELECT 1 FROM pg_policies p
                        WHERE p.schemaname = 'public'
                          AND p.tablename = r.relasjon
                          AND p.policyname = 'm4_maaler') THEN
            EXECUTE format(
                'CREATE POLICY m4_maaler ON %s TO disponit_lager_eier'
                ' USING (CURRENT_USER = ''disponit_lager_eier'')',
                v_reg::regclass::text);
        END IF;
    END LOOP;
END $$;

-- ------------------------------------------------------------
-- 6.4 Kommentarer som følger objektene inn i basen. En kolonne som
--     heter `rader_estimat` skal også SI at den er et estimat der en
--     DBA møter den.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_lager_eier;
COMMENT ON TABLE retensjonslager IS
    'M-4 v1 (093): husets retensjonsregnskap. Én rad per lager, seedet i'
    ' migrasjon og endret KUN i migrasjon — dommene er sporet i git,'
    ' ikke i revisjonsloggen. Vakten gjør «under frist uten reaper»'
    ' urepresenterbart, og triggerne gjør påstanden sann mot basen.';
COMMENT ON COLUMN retensjonsstorrelse.rader_estimat IS
    'pg_class.reltuples — ANALYZE-ens siste gjetning, ALDRI en telling.'
    ' Skal presenteres som estimat i tekst, aldri antydet med farge.';
COMMENT ON COLUMN retensjonsmaaling.avbrutt IS
    'Fødes true og blir false først når kjøringen lukker seg. En kjøring'
    ' som krasjet står dermed som avbrutt, aldri som komplett med null.';
RESET ROLE;
