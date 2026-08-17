-- ============================================================
-- 035 — Modul-onboarding: engangshemmelighet → langlivet modultoken
--        (implementeringsklarsignal 2026-08-17, konsolidert v1–v4)
--
-- Problemet: `_oppdrag_claim` sender release/miljø/epoch som NULL, med
-- vilje og fail-closed — en registrert oppdragstype er ikke claimbar
-- over HTTP i det hele tatt. Identiteten må komme fra modulens token,
-- bundet ved onboarding. `claim_neste_oppdrag` (015) håndhever allerede
-- alt når den får ekte verdier; det som mangler er KILDEN.
--
-- Det bærende skillet: TOKENET AUTENTISERER, REGISTERET AUTORISERER.
-- Tokenet svarer på ett spørsmål — hvilken deployment er dette?
-- `(modul_id, miljo, release_id)`. Alt annet (livsløp, status, epoch,
-- oppdrags-/artefakttyper) slås opp ved HVER bruk, via releasens
-- kontrakt. Scopes lagres aldri.
--
-- To frister: tokenets egen (`utloper`, v1: 30 døgn, selvrotasjon) og
-- familiens (`familie_utloper`, v1: 365 døgn, ABSOLUTT). Ærlig om hva
-- det gir: et stjålet token kan fornye seg selv inntil familiehorisonten,
-- epoch-økning eller tilbakekalling — grensen fjerner ikke tyveriets
-- verdi, den setter et tak på den.
--
-- LAGRINGEN håndhever kontrakten, ikke funksjonene: kompositt-FK binder
-- tokenet til familiens frist og deployment, CHECK kapper tokenets
-- levetid mot fristen, immutabilitetstriggere låser identiteten og gjør
-- tilbakekalling MONOTON. Rekkevidde, sagt rett ut: kontrakten holder
-- for alle roller i systemet, inkludert funksjonseierne og
-- `disponit_modules_admin`. En superbruker kan deaktivere triggere; det
-- dekkes av driftstilgangen, ikke av skjemaet.
-- ============================================================

-- ------------------------------------------------------------
-- 1. Tabellene
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS modul_onboarding (
    onboarding_id    UUID PRIMARY KEY,
    modul_id         TEXT NOT NULL,
    miljo            TEXT NOT NULL,
    release_id       TEXT NOT NULL,
    hemmelighet_hash TEXT NOT NULL CHECK (hemmelighet_hash ~ '^[0-9a-f]{64}$'),
    familie_utloper  TIMESTAMPTZ NOT NULL,   -- ABSOLUTT horisont (§5)
    -- Hemmeligheten bærer sin egen epoch (Codex P1): en ubrukt hemmelighet
    -- utstedt FØR et nødstopp overlevde stoppet — `noddeaktiver_modul` og
    -- `reaktiver_modul` tilbakekaller tokener, ikke hemmeligheter. Etter
    -- reaktiveringen, mens de 60 minuttene ennå løp, kunne den mynte et
    -- helt gjeldende token og gå rundt kravet om NY onboarding. Innløsningen
    -- krever nå likhet med modulens epoch, lest under modullåsen, akkurat
    -- som tokenet gjør ved hver claim. DEFAULT 0 er epochen en modul som
    -- aldri er nødstoppet har — den gjelder rader satt inn utenom
    -- funksjonen (tester, manuelle rader), aldri utstedelsen selv.
    utstedt_epoch    BIGINT NOT NULL DEFAULT 0,
    utstedt_av       TEXT NOT NULL,
    utstedt_ts       TIMESTAMPTZ NOT NULL DEFAULT now(),
    utloper          TIMESTAMPTZ NOT NULL,   -- hemmelighetens TTL (60 min)
    innlost_ts       TIMESTAMPTZ,            -- NULL = ubrukt
    FOREIGN KEY (modul_id, miljo, release_id)
        REFERENCES moduldeployment (modul_id, miljo, release_id),
    -- Kompositt-identiteten modultoken-FK-en binder seg til: et token kan
    -- aldri peke på en familie med en ANNEN frist eller deployment enn
    -- den det selv bærer (portene 37, 39–41).
    CONSTRAINT onboarding_familie_identitet
        UNIQUE (onboarding_id, familie_utloper, modul_id, miljo, release_id)
);

-- Ett ubrukt onboarding per deployment: to samtidige utstedelser kan ikke
-- begge stå og vente, og en glemt hemmelighet erstattes (utløpt+ubrukt
-- ryddes av utstedelsen) i stedet for å blokkere for alltid.
CREATE UNIQUE INDEX IF NOT EXISTS ett_ubrukt_onboarding
    ON modul_onboarding (modul_id, miljo, release_id)
    WHERE innlost_ts IS NULL;

CREATE TABLE IF NOT EXISTS modultoken (
    token_id         UUID PRIMARY KEY,
    token_mac        TEXT NOT NULL UNIQUE     -- pepper-MAC, som api_tokener
                     CHECK (token_mac ~ '^[0-9a-f]{64}$'),
    onboarding_id    UUID NOT NULL,
    familie_utloper  TIMESTAMPTZ NOT NULL,    -- DENORMALISERT kopi, ikke autoritet
    modul_id         TEXT NOT NULL,
    miljo            TEXT NOT NULL,
    release_id       TEXT NOT NULL,
    utstedt_epoch    BIGINT NOT NULL,
    -- Én-rotasjon-garantien I LAGRINGEN, ikke bare i rotasjonsfunksjonens
    -- radlås: to samtidige rotasjoner kan aldri begge committe en
    -- etterfølger (portene 21, 30). Unikheten står som en indeks under
    -- tabellen — se `ett_forsok_per_rotasjon`.
    forgjenger       UUID REFERENCES modultoken (token_id),
    -- Rotasjonens idempotensnøkkel (Codex P1): deploymentens egen id for
    -- FORSØKET, ikke for tokenet. Den gjør et gjentatt forsøk etter et tapt
    -- svar gjenkjennelig, som er det eneste som skiller det fra en ekte
    -- konflikt. NULL på familiens første token (det er ingen rotasjon) og
    -- på klienter som ikke sender nøkkelen — de får konflikt som før.
    rotasjon_id      UUID,
    -- Forsøksnummeret INNENFOR rotasjonen (Codex P1). Et gjentatt forsøk
    -- etter et tapt svar mynter et SØSKEN, det river ikke ned det forrige:
    -- serveren kan ikke vite om den første hemmeligheten kom frem, og en
    -- deployment som har lagret den ville blitt låst ute i det øyeblikket
    -- den ble tilbakekalt. Alle søsknene tilhører samme `rotasjon_id`, og
    -- alle er levende — deploymenten bruker den den faktisk fikk. Taket
    -- står i lagringen: fem forsøk, så er dette ikke lenger en tapt pakke.
    rotasjon_forsok  SMALLINT NOT NULL DEFAULT 1
                     CHECK (rotasjon_forsok BETWEEN 1 AND 5),
    opprettet        TIMESTAMPTZ NOT NULL DEFAULT now(),
    utloper          TIMESTAMPTZ NOT NULL,    -- v1: 30 døgn
    tilbakekalt_ts   TIMESTAMPTZ,
    tilbakekalt_grunn TEXT,
    CONSTRAINT token_innenfor_familie CHECK (utloper <= familie_utloper),
    -- ON UPDATE RESTRICT: familiens frist kan ikke flyttes «gjennom»
    -- tokenene; ON DELETE RESTRICT: en familie med tokener kan ikke
    -- slettes under dem (port 38).
    CONSTRAINT modultoken_familie
        FOREIGN KEY (onboarding_id, familie_utloper, modul_id, miljo, release_id)
        REFERENCES modul_onboarding
            (onboarding_id, familie_utloper, modul_id, miljo, release_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS modultoken_levende
    ON modultoken (modul_id, miljo, release_id)
    WHERE tilbakekalt_ts IS NULL;

-- ÉN ROTASJON PER TOKEN — og ett forsøk per nummer i den (Codex P1).
--
-- Garantien som betyr noe er at forgjengeren har nøyaktig ÉN rotasjon:
-- to ekte samtidige rotasjoner vil begge sette inn sitt forsøk nummer 1,
-- og lagringen lar bare den ene komme inn (portene 21, 30) — akkurat som
-- før. Det ET GJENTATT FORSØK etter et tapt svar får, er neste nummer i
-- SAMME rotasjon; at hvert nummer er unikt gir taket i CHECK-en et reelt
-- gulv å stå på. Vakten `modultoken_soesken_vakt` under holder søsknene
-- til én og samme `rotasjon_id`.
--
-- Den forrige formen (unik på `forgjenger`, med `erstattet_etter_tapt_svar`
-- som fribillett) er borte: den løste det tapte svaret ved å TILBAKEKALLE
-- etterfølgeren fra forrige forsøk og utstede en ny hemmelighet. Men
-- serveren kan ikke vite om det første svaret kom frem — var det bare
-- forsinket, satt deploymenten igjen med et token som nettopp ble drept,
-- og den kastet forgjengeren sin. Retten til å prøve på nytt skal ikke
-- kunne drepe en credential som kan være levert.
DROP INDEX IF EXISTS en_etterfolger_per_token;
CREATE UNIQUE INDEX IF NOT EXISTS ett_forsok_per_rotasjon
    ON modultoken (forgjenger, rotasjon_forsok);

-- Append-only revisjonsspor: utstedt, innløst, rotert, tilbakekalt,
-- avvist bruk. Speiler modulregister_hendelse i form.
CREATE TABLE IF NOT EXISTS modultoken_hendelse (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    onboarding_id UUID,
    token_id      UUID,
    modul_id      TEXT NOT NULL,
    miljo         TEXT NOT NULL,
    release_id    TEXT NOT NULL,
    hendelse      TEXT NOT NULL CHECK (hendelse IN
                      ('utstedt', 'innlost', 'rotert', 'tilbakekalt',
                       'avvist_bruk')),
    aktor         TEXT NOT NULL,
    detalj        JSONB NOT NULL DEFAULT '{}'::jsonb
                  CHECK (jsonb_typeof(detalj) = 'object'),
    ts            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 2. Immutabilitet — håndhevet i lagringen, ikke i funksjonene
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION avvis_endring()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION '%: % er ikke tillatt (immutabel lagringskontrakt, 035)',
        TG_TABLE_NAME, TG_OP USING ERRCODE = 'check_violation';
END $$;

-- Familiehorisonten kan aldri flyttes — heller ikke før innløsning, og
-- heller ikke av funksjonseieren (port 35). Innløsningen (innlost_ts
-- NULL → satt) og opprydding av utløpte ubrukte rader er de eneste
-- lovlige mutasjonene.
DROP TRIGGER IF EXISTS onboarding_familiefrist_immutable ON modul_onboarding;
CREATE TRIGGER onboarding_familiefrist_immutable
    BEFORE UPDATE ON modul_onboarding
    FOR EACH ROW WHEN (
           NEW.familie_utloper IS DISTINCT FROM OLD.familie_utloper
        OR NEW.onboarding_id    IS DISTINCT FROM OLD.onboarding_id
        OR NEW.modul_id         IS DISTINCT FROM OLD.modul_id
        OR NEW.miljo            IS DISTINCT FROM OLD.miljo
        OR NEW.release_id       IS DISTINCT FROM OLD.release_id
        OR NEW.hemmelighet_hash IS DISTINCT FROM OLD.hemmelighet_hash
        OR NEW.utstedt_epoch    IS DISTINCT FROM OLD.utstedt_epoch
        OR NEW.utloper          IS DISTINCT FROM OLD.utloper
        OR NEW.utstedt_ts       IS DISTINCT FROM OLD.utstedt_ts
        OR NEW.utstedt_av       IS DISTINCT FROM OLD.utstedt_av
        -- innløsning er monoton: NULL → satt, aldri tilbake, aldri endret
        OR (OLD.innlost_ts IS NOT NULL
            AND NEW.innlost_ts IS DISTINCT FROM OLD.innlost_ts))
    EXECUTE FUNCTION avvis_endring();

-- En ubrukt-og-utløpt hemmelighet kan ryddes (utstedelsen erstatter den);
-- en INNLØST familierad kan aldri slettes så lenge tokener finnes (FK
-- RESTRICT, port 38) — og heller ikke etterpå: den ER familiens anker.
DROP TRIGGER IF EXISTS onboarding_slettevern ON modul_onboarding;
CREATE TRIGGER onboarding_slettevern
    BEFORE DELETE ON modul_onboarding
    FOR EACH ROW WHEN (OLD.innlost_ts IS NOT NULL)
    EXECUTE FUNCTION avvis_endring();

-- Tokenets identitet er immutabel; tilbakekalling er eneste lovlige
-- mutasjon, og den er MONOTON MOT DØDEN: NULL → satt, og deretter kun
-- FREMSKYNDET (ny frist strengt tidligere enn den gamle), aldri utsatt,
-- aldri nullet. Retningen — ikke uforanderligheten — er invarianten:
-- rotasjonens 15-minutters nåde er et FREMTIDIG tilbakekalt_ts, og en
-- eksplisitt tilbakekalling må kunne kappe den nåden umiddelbart. En
-- grunn kan bare endres sammen med en fremskynding, så et dødt token
-- aldri får omskrevet historikken sin. Hele regelen står i WHEN-leddet
-- — ikke i prosatekst.
--
-- OG EN GRUNN ALENE ER ALDRI EN TILBAKEKALLING (Codex P2): på et LEVENDE
-- token (tilbakekalt_ts NULL) var en ren grunn-endring usynlig for regelen
-- over, siden den bare så på OLD.tilbakekalt_ts. Da kunne eieren eller
-- `disponit_modul_eier` skrive en tilbakekallingsgrunn på et token som
-- fortsatt virker — sporet ville lyve om tokenets tilstand. Grunnen følger
-- døden, ikke omvendt: den kan bare settes eller endres i samme UPDATE som
-- flytter tilbakekalt_ts (NULL → satt, eller fremskyndet).
DROP TRIGGER IF EXISTS modultoken_identitet_immutable ON modultoken;
CREATE TRIGGER modultoken_identitet_immutable
    BEFORE UPDATE ON modultoken
    FOR EACH ROW WHEN (
           NEW.token_id        IS DISTINCT FROM OLD.token_id
        OR NEW.token_mac       IS DISTINCT FROM OLD.token_mac
        OR NEW.onboarding_id   IS DISTINCT FROM OLD.onboarding_id
        OR NEW.familie_utloper IS DISTINCT FROM OLD.familie_utloper
        OR NEW.modul_id        IS DISTINCT FROM OLD.modul_id
        OR NEW.miljo           IS DISTINCT FROM OLD.miljo
        OR NEW.release_id      IS DISTINCT FROM OLD.release_id
        OR NEW.utstedt_epoch   IS DISTINCT FROM OLD.utstedt_epoch
        OR NEW.forgjenger      IS DISTINCT FROM OLD.forgjenger
        OR NEW.rotasjon_id     IS DISTINCT FROM OLD.rotasjon_id
        OR NEW.rotasjon_forsok IS DISTINCT FROM OLD.rotasjon_forsok
        OR NEW.utloper         IS DISTINCT FROM OLD.utloper
        OR NEW.opprettet       IS DISTINCT FROM OLD.opprettet
        OR (OLD.tilbakekalt_ts IS NOT NULL
            AND (NEW.tilbakekalt_ts IS NULL
              OR NEW.tilbakekalt_ts > OLD.tilbakekalt_ts
              OR (NEW.tilbakekalt_ts = OLD.tilbakekalt_ts
                  AND NEW.tilbakekalt_grunn
                      IS DISTINCT FROM OLD.tilbakekalt_grunn)))
        -- Levende token: grunnen kan ikke røres uten at raden faktisk dør.
        OR (NEW.tilbakekalt_ts IS NULL
            AND NEW.tilbakekalt_grunn IS DISTINCT FROM OLD.tilbakekalt_grunn))
    EXECUTE FUNCTION avvis_endring();

-- SØSKEN HØRER TIL SAMME ROTASJON (Codex P1). Indeksen over sier at hvert
-- forsøksnummer er unikt; denne sier hva et forsøk > 1 ER: neste forsøk i
-- den rotasjonen forsøk 1 startet, med samme nøkkel. Uten den kunne to
-- ulike rotasjoner dele forgjengeren bare de valgte hvert sitt nummer —
-- altså nettopp familiegreningen én-etterfølger-regelen finnes for å
-- stoppe. Sjekken leser en COMMITTET rad (forsøk 1); en samtidig, ennå
-- ucommittet forsøk-1-rad er usynlig her og gir avvisning — fail-closed.
CREATE OR REPLACE FUNCTION modultoken_soesken_vakt()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.rotasjon_forsok = 1 THEN
        RETURN NEW;
    END IF;
    IF NEW.rotasjon_id IS NULL OR NEW.forgjenger IS NULL THEN
        RAISE EXCEPTION 'modultoken: gjentatt forsok krever forgjenger og'
            ' rotasjonsnokkel' USING ERRCODE = 'check_violation';
    END IF;
    PERFORM 1 FROM public.modultoken t
     WHERE t.forgjenger = NEW.forgjenger AND t.rotasjon_forsok = 1
       AND t.rotasjon_id IS NOT DISTINCT FROM NEW.rotasjon_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'modultoken: forsok % horer ikke til rotasjonen som'
            ' startet pa dette tokenet', NEW.rotasjon_forsok
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS modultoken_soesken ON modultoken;
CREATE TRIGGER modultoken_soesken BEFORE INSERT ON modultoken
    FOR EACH ROW EXECUTE FUNCTION modultoken_soesken_vakt();

-- Tokener slettes aldri — de tilbakekalles. Kjeden (forgjenger) er
-- revisjonsspor.
DROP TRIGGER IF EXISTS modultoken_slettevern ON modultoken;
CREATE TRIGGER modultoken_slettevern
    BEFORE DELETE ON modultoken
    FOR EACH ROW EXECUTE FUNCTION avvis_endring();

DROP TRIGGER IF EXISTS modultoken_hendelse_append_only ON modultoken_hendelse;
CREATE TRIGGER modultoken_hendelse_append_only
    BEFORE UPDATE OR DELETE ON modultoken_hendelse
    FOR EACH ROW EXECUTE FUNCTION avvis_endring();

-- TRUNCATE fyrer INGEN rad-trigger (Codex P2): vaktene over ser den ikke,
-- og tabelleieren kunne tømt hele det annonserte append-only sporet — og
-- tokenkjeden og familieankrene med det — uten motstand. Statement-vakt på
-- alle tre tabellene, samme mønster som 014/016 bruker på sine append-only
-- tabeller. Den gjelder også eieren og migratoren; en TRUNCATE her er
-- alltid en feil, aldri en driftsoppgave.
DROP TRIGGER IF EXISTS onboarding_ingen_truncate ON modul_onboarding;
CREATE TRIGGER onboarding_ingen_truncate BEFORE TRUNCATE ON modul_onboarding
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();
DROP TRIGGER IF EXISTS modultoken_ingen_truncate ON modultoken;
CREATE TRIGGER modultoken_ingen_truncate BEFORE TRUNCATE ON modultoken
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();
DROP TRIGGER IF EXISTS modultoken_hendelse_ingen_truncate
    ON modultoken_hendelse;
CREATE TRIGGER modultoken_hendelse_ingen_truncate
    BEFORE TRUNCATE ON modultoken_hendelse
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- ------------------------------------------------------------
-- 3. Herdede funksjoner (SECURITY DEFINER, eier disponit_modul_eier,
--    search_path=pg_catalog). Scopet gir retten til å FORSØKE, ikke
--    retten til å bestemme — vilkårene under er maskinverifiserte.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_modul_eier;

-- Utstedelse (fase 1): mennesket med `modules:onboard` får en
-- engangshemmelighet. Vilkår: deploymentraden finnes og er `claiming`,
-- modulen er `staging_verifisert` eller `aktiv`, og minst én
-- oppdragstype er registrert under RELEASENS kontrakt — et token uten
-- claimbart arbeid er bare en hemmelighet på avveie som venter.
-- Horisonten og TTL-en kommer fra SERVERKONFIGURASJON via kalleren
-- (aldri fra requesten — HTTP-laget sender dem ikke videre).
CREATE OR REPLACE FUNCTION utsted_onboarding_hemmelighet(
    p_modul_id TEXT, p_miljo TEXT, p_release_id TEXT,
    p_onboarding_id UUID, p_hemmelighet_hash TEXT,
    p_familie_dager INT, p_ttl_minutter INT, p_aktor TEXT)
RETURNS TABLE (onboarding_id UUID, utloper TIMESTAMPTZ,
               familie_utloper TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_kv INT; v_kh TEXT; v_utloper TIMESTAMPTZ;
        v_familie TIMESTAMPTZ; v_epoch BIGINT;
BEGIN
    -- MODULLÅSEN FØRST, så deployment-låsen — samme rekkefølge som
    -- innløsning og rotasjon (modullås → resten), ellers er dette en
    -- vranglås i stedet for en serialisering. Modullåsen er ny her (Codex
    -- P1): epochen som STEMPLES i hemmeligheten må leses i samme
    -- serialisering som epoch-endringene, ellers kan et nødstopp legge seg
    -- mellom lesningen og INSERT-en og hemmeligheten fødes med en epoch som
    -- alt er foreldet.
    PERFORM pg_advisory_xact_lock(hashtextextended('modul:' || p_modul_id, 0));
    -- Serialiser mot innløsning/re-utstedelse for samme deployment.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'modulonboarding:' || p_modul_id || ':' || p_miljo || ':'
        || p_release_id, 0));
    SELECT d.kontraktversjon, d.kontrakt_hash INTO v_kv, v_kh
      FROM public.moduldeployment d
     WHERE d.modul_id = p_modul_id AND d.miljo = p_miljo
       AND d.release_id = p_release_id AND d.livslop = 'claiming';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'onboarding: deployment %/%/% finnes ikke eller er'
            ' ikke claiming', p_modul_id, p_miljo, p_release_id
            USING ERRCODE = 'no_data_found';
    END IF;
    SELECT h.status, h.module_epoch INTO v_status, v_epoch
      FROM public.modulhode h WHERE h.modul_id = p_modul_id;
    IF v_status IS NULL
       OR v_status NOT IN ('staging_verifisert', 'aktiv') THEN
        RAISE EXCEPTION 'onboarding: modul % er % (krever staging_verifisert'
            ' eller aktiv)', p_modul_id, coalesce(v_status, 'ukjent')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.oppdragstype_register reg
                    WHERE reg.eiermodul = p_modul_id
                      AND reg.kontraktversjon = v_kv
                      AND reg.kontrakt_hash = v_kh) THEN
        RAISE EXCEPTION 'onboarding: ingen registrert oppdragstype under'
            ' releasens kontrakt (%/%)', v_kv, v_kh
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_familie_dager IS NULL OR p_familie_dager < 1
       OR p_ttl_minutter IS NULL OR p_ttl_minutter < 1 THEN
        RAISE EXCEPTION 'onboarding: ugyldig horisont/TTL'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- En glemt (utløpt, ubrukt) hemmelighet skal ikke blokkere for alltid:
    -- den har aldri produsert et token (FK-en peker bare på innløste
    -- familier), så den kan trygt erstattes. En UBRUKT som fortsatt lever
    -- står — unik-indeksen avviser da denne utstedelsen, med vilje.
    --
    -- Med ett tillegg: en ubrukt hemmelighet fra en TIDLIGERE EPOCH kan
    -- ikke lenger innløses (sjekken i `innlos_onboarding`), så den er ikke
    -- en hemmelighet i drift — den er søppel etter et nødstopp. Den skal
    -- ikke stå igjen og se levende ut for et menneske som leser tabellen,
    -- og den skal aldri kunne blokkere en ny utstedelse via
    -- `ett_ubrukt_onboarding`.
    DELETE FROM public.modul_onboarding o
     WHERE o.modul_id = p_modul_id AND o.miljo = p_miljo
       AND o.release_id = p_release_id AND o.innlost_ts IS NULL
       AND (o.utloper < now() OR o.utstedt_epoch IS DISTINCT FROM v_epoch);
    v_utloper := now() + make_interval(mins => p_ttl_minutter);
    v_familie := now() + make_interval(days => p_familie_dager);
    INSERT INTO public.modul_onboarding
        (onboarding_id, modul_id, miljo, release_id, hemmelighet_hash,
         familie_utloper, utstedt_epoch, utstedt_av, utloper)
        VALUES (p_onboarding_id, p_modul_id, p_miljo, p_release_id,
                p_hemmelighet_hash, v_familie, v_epoch, p_aktor, v_utloper);
    INSERT INTO public.modultoken_hendelse
        (onboarding_id, modul_id, miljo, release_id, hendelse, aktor)
        VALUES (p_onboarding_id, p_modul_id, p_miljo, p_release_id,
                'utstedt', p_aktor);
    RETURN QUERY SELECT p_onboarding_id, v_utloper, v_familie;
END $$;

-- Innløsning (fase 2): deploymenten bytter hemmeligheten i sitt token.
-- ENGANGS OG ATOMISK — hemmeligheten merkes brukt i samme transaksjon
-- som tokenet opprettes; radlåsen serialiserer to samtidige innløsninger
-- (portene 4–5). IDENTITET FRA RADEN, ALDRI FRA REQUESTEN: kalleren
-- oppgir bare onboarding_id + hash; modul/miljø/release leses her.
--
-- EN AVVISNING RAISER IKKE (Codex P2). Den skriver `avvist_bruk` i det
-- append-only sporet og RETURNERER `avvist` satt. Årsaken er mekanisk:
-- en INSERT etterfulgt av RAISE i samme transaksjon rulles tilbake av
-- nettopp den exceptionen, og HTTP-laget ruller uansett tilbake etterpå
-- — det annonserte revisjonssporet fikk altså aldri se et eneste
-- mislykket innløsningsforsøk, som er den ene hendelsen det finnes for.
-- Kalleren committer og svarer 403; grunnen står KUN i sporet, aldri i
-- svaret (feil hemmelighet, brukt hemmelighet og utløpt hemmelighet er
-- fortsatt samme svar utad — intet orakel for gjettverk).
--
-- Formen endret seg, så den gamle signaturen må vekk før REPLACE.
DROP FUNCTION IF EXISTS innlos_onboarding(UUID, TEXT, UUID, TEXT, INT, TEXT);
CREATE OR REPLACE FUNCTION innlos_onboarding(
    p_onboarding_id UUID, p_hemmelighet_hash TEXT,
    p_token_id UUID, p_token_mac TEXT, p_token_dager INT, p_aktor TEXT)
RETURNS TABLE (token_id UUID, modul_id TEXT, miljo TEXT, release_id TEXT,
               utstedt_epoch BIGINT, utloper TIMESTAMPTZ,
               familie_utloper TIMESTAMPTZ, avvist TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE o RECORD; v_epoch BIGINT; v_utloper TIMESTAMPTZ; v_grunn TEXT;
        v_modul TEXT; v_status TEXT;
BEGIN
    -- MODULLÅSEN FØRST (Codex P1). Mynting og epoch-endring må serialisere:
    -- uten låsen kan et token fødes inne i nødstoppets allerede tatte
    -- snapshot, og overleve en «terminering» som aldri så det. Låsen tas før
    -- radlåsen — samme rekkefølge som `noddeaktiver_modul`/`reaktiver_modul`
    -- (modullås → radlåser), ellers er dette en vranglås i stedet for en
    -- serialisering. `modul_id` er uforanderlig (identitetstriggeren), så
    -- det ulåste oppslaget som gir NØKKELEN kan ikke bli feil.
    SELECT ob.modul_id INTO v_modul FROM public.modul_onboarding ob
     WHERE ob.onboarding_id = p_onboarding_id;
    IF NOT FOUND THEN
        -- UKJENT id raiser fortsatt: det finnes ingen rad å tilskrive
        -- hendelsen (modul/miljø/release er NOT NULL), og et gjettet
        -- id-ledd skal ikke kunne fylle en append-only tabell.
        RAISE EXCEPTION 'innlosning: ukjent onboarding'
            USING ERRCODE = 'no_data_found';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('modul:' || v_modul, 0));
    SELECT * INTO o FROM public.modul_onboarding ob
     WHERE ob.onboarding_id = p_onboarding_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'innlosning: ukjent onboarding'
            USING ERRCODE = 'no_data_found';
    END IF;
    -- Hemmeligheten sammenlignes som pepper-MAC (kalleren har regnet den) —
    -- klartekst finnes aldri her. Feil hemmelighet, brukt hemmelighet og
    -- utløpt hemmelighet er SAMME feil utad (ingen orakel for gjettverk);
    -- grunnene skilles bare i sporet, som er der de er til nytte.
    IF o.hemmelighet_hash IS DISTINCT FROM p_hemmelighet_hash
       OR o.innlost_ts IS NOT NULL THEN
        v_grunn := 'innlosning_avvist';
    ELSIF o.utloper < now() THEN
        v_grunn := 'innlosning_utlopt';
    END IF;
    -- Modulens tilstand LESES UNDER MODULLÅSEN (Codex P1). For rotasjonen
    -- er låsen nok — et nødstopp har da alt tilbakekalt forgjengeren, og
    -- rotasjonen faller på det. Her finnes det ingen forgjenger å
    -- tilbakekalle: en innløsning som starter ETTER stoppet ville født et
    -- helt nytt, levende token for en modul som nettopp ble terminert.
    -- Vilkåret er utstedelsens eget (`staging_verifisert`/`aktiv`), så en
    -- nødstoppet — og en reaktivert, ennå ikke re-verifisert — modul ikke
    -- får nye tokener. Hemmeligheten er URØRT: den kan innløses når modulen
    -- er tilbake, innenfor sin egen TTL.
    IF v_grunn IS NULL THEN
        SELECT h.status, h.module_epoch INTO v_status, v_epoch
          FROM public.modulhode h WHERE h.modul_id = o.modul_id;
        IF v_status IS NULL
           OR v_status NOT IN ('staging_verifisert', 'aktiv') THEN
            v_grunn := 'innlosning_modul_stengt';
        -- HEMMELIGHETEN BÆRER SIN EGEN EPOCH (Codex P1). Statussjekken over
        -- fanger et PÅGÅENDE nødstopp, men ikke et OVERSTÅTT: er modulen
        -- nødstoppet og siden reaktivert og re-verifisert, står statusen
        -- igjen på `aktiv`/`staging_verifisert`, mens epochen har steget to
        -- ganger. En hemmelighet utstedt før stoppet — ubrukt, og fortsatt
        -- innenfor sine 60 minutter — ville da myntet et fullt gjeldende
        -- token og gått rundt kravet om ny onboarding etter reaktivering.
        -- Epochen leses her, under modullåsen, og må være DEN SAMME som ved
        -- utstedelsen. Avvisningen er én av de vanlige: samme svar utad,
        -- egen grunn i sporet. Hemmeligheten røres ikke — den kan bare
        -- aldri mer bli et token, for epochen kommer aldri tilbake.
        ELSIF o.utstedt_epoch IS DISTINCT FROM v_epoch THEN
            v_grunn := 'innlosning_epoch_endret';
        END IF;
    END IF;
    IF v_grunn IS NOT NULL THEN
        INSERT INTO public.modultoken_hendelse
            (onboarding_id, modul_id, miljo, release_id, hendelse, aktor,
             detalj)
            VALUES (o.onboarding_id, o.modul_id, o.miljo, o.release_id,
                    'avvist_bruk', p_aktor,
                    jsonb_build_object('grunn', v_grunn));
        -- Ingen RAISE: transaksjonen skal COMMITTE, ellers forsvinner
        -- hendelsen sammen med avvisningen. Intet token er opprettet, og
        -- hemmeligheten er urørt — en utløpt/feil hemmelighet blir ikke
        -- «brukt» av at noen prøvde.
        RETURN QUERY SELECT NULL::UUID, NULL::TEXT, NULL::TEXT, NULL::TEXT,
                            NULL::BIGINT, NULL::TIMESTAMPTZ,
                            NULL::TIMESTAMPTZ, v_grunn;
        RETURN;
    END IF;
    -- Epoch fryses i tokenet NÅ — verdien er lest over, under modullåsen,
    -- så den kan ikke ha blitt bumpet mellom lesningen og innsettingen.
    -- Claim-veien krever likhet med gjeldende epoch ved hver bruk; rotasjon
    -- ARVER denne verdien og plukker aldri opp en ny (port 23).
    v_utloper := LEAST(now() + make_interval(days => p_token_dager),
                       o.familie_utloper);
    UPDATE public.modul_onboarding SET innlost_ts = now()
     WHERE modul_onboarding.onboarding_id = p_onboarding_id;
    INSERT INTO public.modultoken
        (token_id, token_mac, onboarding_id, familie_utloper, modul_id,
         miljo, release_id, utstedt_epoch, forgjenger, utloper)
        VALUES (p_token_id, p_token_mac, o.onboarding_id, o.familie_utloper,
                o.modul_id, o.miljo, o.release_id, v_epoch, NULL, v_utloper);
    INSERT INTO public.modultoken_hendelse
        (onboarding_id, token_id, modul_id, miljo, release_id, hendelse,
         aktor)
        VALUES (o.onboarding_id, p_token_id, o.modul_id, o.miljo,
                o.release_id, 'innlost', p_aktor);
    RETURN QUERY SELECT p_token_id, o.modul_id, o.miljo, o.release_id,
                        v_epoch, v_utloper, o.familie_utloper, NULL::TEXT;
END $$;

-- Verifisering ved bruk (claim/rotasjon): rent lesende oppslag på MAC.
-- Runtime har ingen SELECT på modultoken — dette er den ENESTE veien inn.
-- Gyldighet = ikke tilbakekalt-og-forbi (rotasjonens 15-minutters nåde
-- ligger i et FREMTIDIG tilbakekalt_ts) og ikke utløpt.
CREATE OR REPLACE FUNCTION verifiser_modultoken(p_token_mac TEXT)
RETURNS TABLE (token_id UUID, modul_id TEXT, miljo TEXT, release_id TEXT,
               utstedt_epoch BIGINT, familie_utloper TIMESTAMPTZ)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT t.token_id, t.modul_id, t.miljo, t.release_id, t.utstedt_epoch,
           t.familie_utloper
      FROM public.modultoken t
     WHERE t.token_mac = p_token_mac
       AND (t.tilbakekalt_ts IS NULL OR t.tilbakekalt_ts > now())
       AND t.utloper > now();
$$;

-- Selvrotasjon: modulens token bytter seg selv, fritt innenfor
-- familiehorisonten, uten menneske. Radlås på forgjengeren + den partielle
-- unikheten på `forgjenger` = én etterfølger (portene 20–21, 27–30).
-- Forgjengeren tilbakekalles med 15 minutters nåde (in-flight-requests) —
-- nåden er kodet som et FREMTIDIG tilbakekalt_ts, så identitetstriggeren
-- gjelder uendret. Epoch ARVES fra forgjengeren, aldri fra modulhodet.
--
-- GJENTATT FORSØK ER GJENOPPRETTELIG (Codex P1). Hemmeligheten mynter
-- serveren og viser den ÉN gang; går 201-svaret tapt, holder INGEN
-- etterfølgeren, mens den likevel okkuperer forgjengerens eneste
-- etterfølgerplass. Deploymenten prøvde da igjen med sitt fortsatt gyldige
-- token, fikk 409, og var ute av drift så snart nåden løp ut — en tapt
-- pakke krevde et menneske og en ny onboarding. Derfor bærer forsøket en
-- idempotensnøkkel: kommer det samme `rotasjon_id` en gang til, mynter
-- rotasjonen NESTE FORSØK i samme rotasjon. Uten nøkkel — eller med en
-- ANNEN nøkkel — er dette fortsatt en konflikt: det er nettopp forskjellen
-- på «samme forsøk om igjen» og «to rotasjoner».
--
-- OG DET GJENTATTE FORSØKET RIVER IKKE NED DET FORRIGE (Codex P1, runde 3).
-- Første utgave tilbakekalte etterfølgeren fra forrige forsøk og kalte den
-- ulevert. Men serveren VET ikke at svaret gikk tapt — den vet bare at det
-- kom en request til. Var det første svaret levert, eller bare forsinket,
-- satt deploymenten igjen med et token som nettopp ble drept, kastet
-- forgjengeren og var ute av drift umiddelbart; to samtidige automatiske
-- retries kunne gjøre det samme mot hverandre. Derfor lever ALLE forsøkene
-- i en rotasjon: deploymenten bruker den hemmeligheten den faktisk fikk,
-- uansett hvilket forsøk som nådde frem. Det er ingen familiegrening —
-- alle søsknene tilhører samme `rotasjon_id`, og veien hit krever et
-- LEVENDE forgjenger-token, så vinduet er nådevinduet. Taket er fem
-- forsøk (CHECK i lagringen); over det er dette ikke lenger en tapt pakke.
--
-- KONVERGENS: neste rotasjon FRA et av søsknene beviser hvilket
-- deploymenten faktisk holder. De andre tilbakekalles da umiddelbart —
-- de er beviselig ikke i bruk, og de skal ikke ligge og leve i 30 dager.
--
-- Formen endret seg (ny parameter), så den gamle signaturen må vekk før
-- REPLACE — ellers står to overlaster igjen og 5-argumentskallet blir
-- tvetydig.
DROP FUNCTION IF EXISTS roter_modultoken(UUID, UUID, TEXT, INT, TEXT);
CREATE OR REPLACE FUNCTION roter_modultoken(
    p_forgjenger UUID, p_ny_token_id UUID, p_ny_mac TEXT,
    p_token_dager INT, p_aktor TEXT, p_rotasjon_id UUID DEFAULT NULL)
RETURNS TABLE (token_id UUID, utloper TIMESTAMPTZ,
               familie_utloper TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE g RECORD; v_utloper TIMESTAMPTZ; v_modul TEXT; e RECORD; s RECORD;
        v_forsok SMALLINT := 1;
BEGIN
    -- MODULLÅSEN FØRST (Codex P1) — se `innlos_onboarding`. Uten den kunne
    -- rotasjonen legge inn en etterfølger som nødstoppets alt startede
    -- UPDATE aldri så: stoppet tilbakekalte forgjengeren, etterfølgeren
    -- overlevde, og den kan rotere videre og bruke utestående
    -- oppdragsfullmakter. Med låsen skjer rotasjonen enten HELT før stoppet
    -- (som da ser og dreper etterfølgeren) eller HELT etter det — og da er
    -- forgjengeren tilbakekalt, som avvises rett under. `modul_id` er
    -- uforanderlig, så nøkkeloppslaget kan tas uten radlås.
    SELECT t.modul_id INTO v_modul FROM public.modultoken t
     WHERE t.token_id = p_forgjenger;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'rotasjon: ukjent token'
            USING ERRCODE = 'no_data_found';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('modul:' || v_modul, 0));
    SELECT * INTO g FROM public.modultoken t
     WHERE t.token_id = p_forgjenger FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'rotasjon: ukjent token'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF g.tilbakekalt_ts IS NOT NULL AND g.tilbakekalt_ts <= now() THEN
        RAISE EXCEPTION 'rotasjon: tokenet er tilbakekalt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF g.utloper <= now() THEN
        RAISE EXCEPTION 'rotasjon: tokenet er utlopt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Fristen leses og skrives i SAMME transaksjon under radlåsen; etter
    -- horisonten kreves ny onboarding med `modules:onboard` (port 29).
    IF g.familie_utloper <= now() THEN
        RAISE EXCEPTION 'rotasjon: familiehorisonten er passert — ny'
            ' onboarding kreves' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- KONVERGENS FØRST: at forgjengeren selv brukes til å rotere, beviser
    -- hvilket av sine egne søsken deploymenten faktisk holder. De andre
    -- kappes her, umiddelbart — de ble aldri tatt i bruk, og skal ikke
    -- ligge og leve ut sine 30 dager ved siden av kjeden.
    IF g.forgjenger IS NOT NULL THEN
        FOR s IN
            SELECT * FROM public.modultoken t
             WHERE t.forgjenger = g.forgjenger
               AND t.token_id <> g.token_id
               AND t.tilbakekalt_ts IS NULL
             FOR UPDATE
        LOOP
            UPDATE public.modultoken
               SET tilbakekalt_ts = now(),
                   tilbakekalt_grunn = 'soesken_ikke_valgt'
             WHERE public.modultoken.token_id = s.token_id;
            INSERT INTO public.modultoken_hendelse
                (onboarding_id, token_id, modul_id, miljo, release_id,
                 hendelse, aktor, detalj)
                VALUES (s.onboarding_id, s.token_id, s.modul_id, s.miljo,
                        s.release_id, 'tilbakekalt', p_aktor,
                        jsonb_build_object('grunn', 'soesken_ikke_valgt',
                                           'valgt', g.token_id::text));
        END LOOP;
    END IF;
    -- Har forgjengeren alt en etterfølger, er dette enten SAMME forsøk om
    -- igjen (svaret gikk tapt) eller en ekte konflikt. Bare nøkkelen kan
    -- skille dem, og bare den første er gjenopprettelig. Forsøk 1 er raden
    -- som eier rotasjonen; låsen på den serialiserer de gjentatte
    -- forsøkene mot hverandre.
    SELECT * INTO e FROM public.modultoken t
     WHERE t.forgjenger = p_forgjenger AND t.rotasjon_forsok = 1
     FOR UPDATE;
    IF FOUND THEN
        IF p_rotasjon_id IS NULL
           OR e.rotasjon_id IS DISTINCT FROM p_rotasjon_id THEN
            -- Samme feil som unikheten ville gitt, bare tatt her hvor
            -- grunnen kan sies: HTTP-laget svarer 409 på begge.
            RAISE EXCEPTION 'rotasjon: forgjengeren har alt en etterfolger'
                USING ERRCODE = 'unique_violation';
        END IF;
        -- Samme forsøk om igjen: neste nummer i rotasjonen. Ingenting
        -- tilbakekalles — hemmeligheten fra et tidligere forsøk KAN være
        -- levert, og da er den deploymentens eneste credential.
        --
        -- Vinduet er ikke ubegrenset, og det er verdt å si hvorfor: veien hit
        -- krever et LEVENDE forgjenger-token, og forgjengeren ble
        -- tilbakekalt med 15 minutters nåde av det første forsøket. Etter
        -- nåden faller kallet på tilbakekallingssjekken over, lenge før
        -- denne grenen. Gjenopprettelsen lever altså nøyaktig like lenge som
        -- forgjengeren selv — og hvert eneste forsøk står i sporet under.
        SELECT max(t.rotasjon_forsok) + 1 INTO v_forsok
          FROM public.modultoken t WHERE t.forgjenger = p_forgjenger;
        IF v_forsok > 5 THEN
            RAISE EXCEPTION 'rotasjon: for mange gjentatte forsok pa samme'
                ' rotasjon' USING ERRCODE = 'unique_violation';
        END IF;
    END IF;
    v_utloper := LEAST(now() + make_interval(days => p_token_dager),
                       g.familie_utloper);
    INSERT INTO public.modultoken
        (token_id, token_mac, onboarding_id, familie_utloper, modul_id,
         miljo, release_id, utstedt_epoch, forgjenger, utloper, rotasjon_id,
         rotasjon_forsok)
        VALUES (p_ny_token_id, p_ny_mac, g.onboarding_id, g.familie_utloper,
                g.modul_id, g.miljo, g.release_id, g.utstedt_epoch,
                p_forgjenger, v_utloper, p_rotasjon_id, v_forsok);
    -- Nåden: forgjengeren dør om 15 minutter uansett hva som skjer videre.
    -- Var den alt i nådevindu (grace fra en tidligere hendelse), står den
    -- fristen — triggeren gjør et satt tilbakekalt_ts uforanderlig.
    IF g.tilbakekalt_ts IS NULL THEN
        UPDATE public.modultoken
           SET tilbakekalt_ts = now() + interval '15 minutes',
               tilbakekalt_grunn = 'rotert'
         WHERE public.modultoken.token_id = p_forgjenger;
    END IF;
    INSERT INTO public.modultoken_hendelse
        (onboarding_id, token_id, modul_id, miljo, release_id, hendelse,
         aktor, detalj)
        VALUES (g.onboarding_id, p_ny_token_id, g.modul_id, g.miljo,
                g.release_id, 'rotert', p_aktor,
                jsonb_build_object('forgjenger', p_forgjenger::text,
                                   'rotasjon_id', p_rotasjon_id::text,
                                   'forsok', v_forsok));
    RETURN QUERY SELECT p_ny_token_id, v_utloper, g.familie_utloper;
END $$;

-- Eksplisitt tilbakekalling (`modules:onboard`): umiddelbar, auditert
-- grunn. Idempotent overfor et allerede dødt token. Et token i
-- rotasjonsnåde (fremtidig tilbakekalt_ts) er IKKE dødt ennå — nåden
-- finnes for in-flight-requests, ikke for kompromitterte tokener, så
-- her kappes den til now(). Triggeren tillater nettopp den retningen.
CREATE OR REPLACE FUNCTION tilbakekall_modultoken(
    p_token_id UUID, p_grunn TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE t RECORD;
BEGIN
    IF p_grunn IS NULL OR length(btrim(p_grunn)) = 0 THEN
        RAISE EXCEPTION 'tilbakekalling: grunn er obligatorisk'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO t FROM public.modultoken mt
     WHERE mt.token_id = p_token_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'tilbakekalling: ukjent token'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF t.tilbakekalt_ts IS NOT NULL AND t.tilbakekalt_ts <= now() THEN
        RETURN;                                   -- alt dødt: idempotent
    END IF;
    -- Enten NULL (levende) eller fremtidig (nåde) — begge kappes til now().
    UPDATE public.modultoken SET tilbakekalt_ts = now(),
                                 tilbakekalt_grunn = p_grunn
     WHERE public.modultoken.token_id = p_token_id;
    INSERT INTO public.modultoken_hendelse
        (onboarding_id, token_id, modul_id, miljo, release_id, hendelse,
         aktor, detalj)
        VALUES (t.onboarding_id, p_token_id, t.modul_id, t.miljo,
                t.release_id, 'tilbakekalt', p_aktor,
                jsonb_build_object('grunn', p_grunn));
END $$;

-- ------------------------------------------------------------
-- 4. Epoch-økning terminerer familien: `noddeaktiver_modul` og
--    `reaktiver_modul` tilbakekaller alle LEVENDE tokener for modulen i
--    SAMME transaksjon som epoch-bumpen. Kroppene under er 014 sine
--    GJELDENDE kropper (kopiert, ikke husket), diff-endret med
--    tilbakekallingsblokken — merket «035:».
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION noddeaktiver_modul(
    p_modul_id    TEXT,
    p_begrunnelse TEXT,
    p_aktor       TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_epoch BIGINT; v_d RECORD;
BEGIN
    IF p_begrunnelse IS NULL OR length(btrim(p_begrunnelse)) = 0 THEN
        RAISE EXCEPTION 'noddeaktiver_modul: begrunnelse er obligatorisk'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Modul-lås: serialiserer med bytt_release/sett_modulstatus/reaktiver, så
    -- nødstoppets postbetingelse «alle claiming drained» er stabil (Codex P1).
    PERFORM pg_advisory_xact_lock(hashtextextended('modul:' || p_modul_id, 0));
    SELECT status, module_epoch INTO v_status, v_epoch FROM public.modulhode
     WHERE modul_id = p_modul_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'noddeaktiver_modul: ukjent modul %', p_modul_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_status = 'nodeaktivert' THEN
        RETURN;                                   -- idempotent
    END IF;
    UPDATE public.modulhode
       SET status = 'nodeaktivert', modulrevisjon = modulrevisjon + 1,
           module_epoch = module_epoch + 1, status_ts = now()
     WHERE modul_id = p_modul_id;
    -- Fjern fencet arbeidsgrunnlag: enhver claiming draines (emergency stop),
    -- så reaktivering ikke kan gjenbruke en gammel deployment. Hver tvangs-
    -- drenert deployment revideres (Codex P2 — ellers står de som claiming i
    -- hendelsesstrømmen). Miljøet er med (Codex P2): nødstoppet treffer ALLE
    -- miljøer, og samme release i staging og produksjon gir ellers to
    -- identiske hendelser.
    FOR v_d IN
        WITH drenert AS (
            UPDATE public.moduldeployment SET livslop = 'draining'
             WHERE modul_id = p_modul_id AND livslop = 'claiming'
            RETURNING release_id, miljo, kontraktversjon, kontrakt_hash)
        SELECT * FROM drenert
    LOOP
        INSERT INTO public.modulregister_hendelse
            (modul_id, hendelse, fra_livslop, til_livslop, release_id, miljo,
             kontraktversjon, kontrakt_hash, module_epoch, aktor, begrunnelse)
            VALUES (p_modul_id, 'drenet_ved_nodstopp', 'claiming', 'draining',
                    v_d.release_id, v_d.miljo, v_d.kontraktversjon,
                    v_d.kontrakt_hash, v_epoch + 1, p_aktor, p_begrunnelse);
    END LOOP;
    -- 035: epoch-økningen terminerer tokenfamilien I SAMME TRANSAKSJON.
    -- Umiddelbart (ingen nåde): et nødstopp ER unntakstilstanden nåden
    -- finnes for å unngå. Også tokener i rotasjonsnåde (fremtidig
    -- tilbakekalt_ts) kappes til now() — triggeren tillater fremskynding.
    INSERT INTO public.modultoken_hendelse
        (onboarding_id, token_id, modul_id, miljo, release_id, hendelse,
         aktor, detalj)
        SELECT t.onboarding_id, t.token_id, t.modul_id, t.miljo,
               t.release_id, 'tilbakekalt', p_aktor,
               jsonb_build_object('grunn', 'epoch_okning_nodstopp')
          FROM public.modultoken t
         WHERE t.modul_id = p_modul_id
           AND (t.tilbakekalt_ts IS NULL OR t.tilbakekalt_ts > now());
    UPDATE public.modultoken SET tilbakekalt_ts = now(),
                                 tilbakekalt_grunn = 'epoch_okning_nodstopp'
     WHERE modul_id = p_modul_id
       AND (tilbakekalt_ts IS NULL OR tilbakekalt_ts > now());
    INSERT INTO public.modulregister_hendelse
        (modul_id, hendelse, fra_status, til_status, module_epoch, aktor,
         begrunnelse)
        VALUES (p_modul_id, 'noddeaktivering', v_status, 'nodeaktivert',
                v_epoch + 1, p_aktor, p_begrunnelse);
END $$;

CREATE OR REPLACE FUNCTION reaktiver_modul(
    p_modul_id       TEXT,
    p_forventet_epoch BIGINT,
    p_aktor          TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_epoch BIGINT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('modul:' || p_modul_id, 0));
    SELECT status, module_epoch INTO v_status, v_epoch FROM public.modulhode
     WHERE modul_id = p_modul_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'reaktiver_modul: ukjent modul %', p_modul_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_status <> 'nodeaktivert' THEN
        RAISE EXCEPTION 'reaktiver_modul: modul % er % (kun nodeaktivert kan '
            'reaktiveres)', p_modul_id, v_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_forventet_epoch IS DISTINCT FROM v_epoch THEN
        RAISE EXCEPTION 'reaktiver_modul: epoch-avvik (forventet %, er %)',
            p_forventet_epoch, v_epoch USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.modulhode
       SET status = 'staging_verifisert', modulrevisjon = modulrevisjon + 1,
           module_epoch = module_epoch + 1, status_ts = now()
     WHERE modul_id = p_modul_id;
    -- 035: også reaktiveringen bumper epoch — samme terminering. Rotasjon
    -- plukker aldri opp ny epoch (arver forgjengerens), så reaktivering
    -- KREVER ny onboarding (port 23).
    INSERT INTO public.modultoken_hendelse
        (onboarding_id, token_id, modul_id, miljo, release_id, hendelse,
         aktor, detalj)
        SELECT t.onboarding_id, t.token_id, t.modul_id, t.miljo,
               t.release_id, 'tilbakekalt', p_aktor,
               jsonb_build_object('grunn', 'epoch_okning_reaktivering')
          FROM public.modultoken t
         WHERE t.modul_id = p_modul_id
           AND (t.tilbakekalt_ts IS NULL OR t.tilbakekalt_ts > now());
    UPDATE public.modultoken SET tilbakekalt_ts = now(),
                              tilbakekalt_grunn = 'epoch_okning_reaktivering'
     WHERE modul_id = p_modul_id
       AND (tilbakekalt_ts IS NULL OR tilbakekalt_ts > now());
    INSERT INTO public.modulregister_hendelse
        (modul_id, hendelse, fra_status, til_status, module_epoch, aktor)
        VALUES (p_modul_id, 'reaktivering', 'nodeaktivert', 'staging_verifisert',
                v_epoch + 1, p_aktor);
END $$;

-- ------------------------------------------------------------
-- 5. `registrer_artefakttype` — herdet med GLOBAL lås +
--    prefiks-overlappssjekk, samme mønster som `registrer_oppdragstype`
--    (klarsignalet §4/§8). Kroppen er 016 sin GJELDENDE, diff-endret:
--    global lås i tillegg til identitetslåsen, og overlappssjekken —
--    merket «035:». Funksjonen EIES av domene-eieren (016) — CREATE OR
--    REPLACE må kjøre som samme eier, derfor rollebyttet her.
-- ------------------------------------------------------------
RESET ROLE;
SET LOCAL ROLE disponit_domene_eier;
CREATE OR REPLACE FUNCTION registrer_artefakttype(
    p_artefakttype TEXT, p_eiermodul TEXT, p_kontraktversjon INT,
    p_kontrakt_hash TEXT, p_skjema_hash TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_konflikt TEXT;
BEGIN
    -- 035: navneformen er lukket — `<domene>.<underdomene>.<artefakt>`,
    -- kun [a-z0-9_.], globalt unik, MINST tre ledd (dypere hierarki er
    -- lov — det er nettopp da prefiks-overlappen under har arbeid å
    -- gjøre). Ingen versjon og intet modulnavn i navnet:
    -- (kontraktversjon, kontrakt_hash) versjonerer raden og eiermodul er
    -- egen kolonne.
    IF p_artefakttype !~ '^[a-z0-9_]+(\.[a-z0-9_]+){2,}$' THEN
        RAISE EXCEPTION 'artefakttype % har ugyldig navneform'
            ' (<domene>.<underdomene>.<artefakt>, [a-z0-9_.])',
            p_artefakttype USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- 035: GLOBAL lås (som `modulregister:oppdragstype`) — uten den er
    -- prefiks-overlappen under en avgjørelse tatt på et snapshot: to
    -- samtidige registreringer av `a.b.c` og `a.b.c_x` kan begge passere
    -- hver sin sjekk og committe. Identitetslåsen (016) beholdes for den
    -- idempotente no-op-veien.
    PERFORM pg_advisory_xact_lock(
        hashtextextended('modulregister:artefakttype', 0));
    -- Codex P2: serialiser på artefakttype-identiteten (samme mønster som
    -- modulregisteret). Uten låsen kan to samtidige registreringer av samme
    -- immutable tuppel begge passere eksistenssjekken under; én vinner, den andre
    -- får PK-brudd i stedet for den dokumenterte idempotente no-op-en.
    PERFORM pg_advisory_xact_lock(
        hashtextextended('artefakttype:' || p_artefakttype, 0));
    -- Sammenlign HELE den immutable tuppelen — en re-registrering med samme
    -- skjema_hash men ANNEN eier/kontrakt ble ellers rapportert som en vellykket
    -- no-op selv om bindingen ikke ble anvendt.
    SELECT eiermodul, kontraktversjon, kontrakt_hash, skjema_hash INTO r
      FROM public.artefakttype_register WHERE artefakttype = p_artefakttype;
    IF FOUND THEN
        IF (r.eiermodul, r.kontraktversjon, r.kontrakt_hash, r.skjema_hash)
           IS DISTINCT FROM
           (p_eiermodul, p_kontraktversjon, p_kontrakt_hash, p_skjema_hash) THEN
            RAISE EXCEPTION 'artefakttype % er immutable', p_artefakttype
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN;
    END IF;
    -- 035: prefiks-overlappssjekk under den globale låsen (speiler
    -- `registrer_oppdragstype`) — `a.b.c` og `a.b.c.d` skal ikke kunne
    -- sameksistere; punktumgrensen hindrer at `a.b.cd` regnes som overlapp.
    SELECT artefakttype INTO v_konflikt FROM public.artefakttype_register
     WHERE starts_with(p_artefakttype, artefakttype || '.')
        OR starts_with(artefakttype, p_artefakttype || '.')
     LIMIT 1;
    IF v_konflikt IS NOT NULL THEN
        RAISE EXCEPTION 'artefakttype % overlapper eksisterende %',
            p_artefakttype, v_konflikt USING ERRCODE = 'unique_violation';
    END IF;
    INSERT INTO public.artefakttype_register
        (artefakttype, eiermodul, kontraktversjon, kontrakt_hash, skjema_hash)
        VALUES (p_artefakttype, p_eiermodul, p_kontraktversjon, p_kontrakt_hash,
                p_skjema_hash);   -- FK → modulkontrakt
END $$;

REVOKE ALL ON FUNCTION registrer_artefakttype(TEXT, TEXT, INT, TEXT, TEXT, TEXT) FROM PUBLIC;
-- 016 ga domains_admin denne (artefaktregisteret bor i domenelaget);
-- den ACL-en BESTÅR — 035 legger modulforvaltningen til, ikke i stedet.
GRANT EXECUTE ON FUNCTION registrer_artefakttype(TEXT, TEXT, INT, TEXT, TEXT, TEXT) TO disponit_domains_admin;
GRANT EXECUTE ON FUNCTION registrer_artefakttype(TEXT, TEXT, INT, TEXT, TEXT, TEXT) TO disponit_modules_admin;
RESET ROLE;

-- ------------------------------------------------------------
-- 5b. Artefaktkapabiliteten bindes til HELE deploymenten (Codex P1)
--
-- 017 binder kapabiliteten til modul · release · kontrakt · epoch, og
-- innløsningen sammenligner bare `modul_id` — som for et modultoken er
-- modulens id, ikke deploymentens. Med 035 er det ikke lenger nok: en
-- modul kan ha flere LEVENDE deployments samtidig (staging og produksjon,
-- eller to releaser under hver sin kontraktversjon), hver med sitt eget
-- modultoken. Opplastingsendepunktet slipper alle modultokener forbi
-- scope-porten (retten ER kapabilitetens), så en staging-arbeider som får
-- en jti utstedt til produksjonsdeploymenten — delt eller feilrutet
-- arbeidsutdeling — kunne levere rapporten, og API-et ville ført evidensen
-- på den releasen kapabiliteten bar. Da attesterer sporet en deployment
-- som ikke autentiserte requesten. Miljøet er det ene leddet tabellen
-- manglet; releasen står der alt.
--
-- Regelen: kapabiliteten stempler miljøet den ble utstedt i, og
-- innløsningen krever HELE den autentiserte deploymenten. NULL betyr
-- «ingen autentisert deployment» (legacy api-token) og matcher kun rader
-- som selv er miljøløse — fail-closed begge veier.
-- ------------------------------------------------------------
ALTER TABLE artefaktkapabilitet ADD COLUMN IF NOT EXISTS miljo TEXT;

-- Bindingsfelt = uforanderlig, som de øvrige i 017s statusmaskin. Egen
-- trigger i stedet for en kopi av hele statusmaskinen hit: mindre å drifte,
-- og den kan ikke komme i utakt med 017 ved en senere endring der.
CREATE OR REPLACE FUNCTION artefaktkapabilitet_miljo_frosset()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.miljo IS DISTINCT FROM OLD.miljo THEN
        RAISE EXCEPTION 'artefaktkapabilitet: miljo er uforanderlig';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS artefaktkapabilitet_miljo ON artefaktkapabilitet;
CREATE TRIGGER artefaktkapabilitet_miljo BEFORE UPDATE ON artefaktkapabilitet
    FOR EACH ROW EXECUTE FUNCTION artefaktkapabilitet_miljo_frosset();

SET LOCAL ROLE disponit_domene_eier;

-- Formen endret seg (nytt haleargument), så de gamle signaturene må vekk
-- før REPLACE — ellers står to overlaster igjen og de gamle kallene blir
-- tvetydige. Nykommeren har DEFAULT NULL nettopp så de kallene fortsatt
-- treffer, med «ingen autentisert deployment».
DROP FUNCTION IF EXISTS utsted_artefaktkapabilitet(TEXT, BIGINT, TEXT, TEXT,
    INT, TEXT, BIGINT, TEXT, TEXT, INT);
CREATE OR REPLACE FUNCTION utsted_artefaktkapabilitet(
    p_tenant TEXT, p_oppdrag_id BIGINT, p_modul_id TEXT, p_release_id TEXT,
    p_kontraktversjon INT, p_kontrakt_hash TEXT, p_module_epoch BIGINT,
    p_artefakttype TEXT, p_jti TEXT, p_levetid_s INT DEFAULT 900,
    p_miljo TEXT DEFAULT NULL)
RETURNS TABLE (jti TEXT, utloper TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_lev INT := least(greatest(coalesce(p_levetid_s, 900), 60), 3600);
        v_utloper TIMESTAMPTZ := now() + (v_lev || ' seconds')::INTERVAL;
BEGIN
    IF p_jti IS NULL OR p_jti !~ '^[0-9a-f]{32,}$' THEN
        RAISE EXCEPTION 'utsted_artefaktkapabilitet: ugyldig jti-format';
    END IF;
    PERFORM 1 FROM public.oppdrag o
     WHERE o.tenant = p_tenant AND o.id = p_oppdrag_id AND o.status = 'plukket'
       AND o.modul_id IS NOT DISTINCT FROM p_modul_id
       AND o.kontraktversjon IS NOT DISTINCT FROM p_kontraktversjon
       AND o.kontrakt_hash IS NOT DISTINCT FROM p_kontrakt_hash
       AND o.module_epoch IS NOT DISTINCT FROM p_module_epoch;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'utsted_artefaktkapabilitet: oppdrag %/% er ikke plukket '
            'med matchende kontraktbinding', p_tenant, p_oppdrag_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Codex: artefakttypen MÅ være registrert til NETTOPP denne modulen+kontrakten
    -- — ellers kunne en kapabilitet for modul A navngi en type registrert til
    -- modul B, og opplastingen lykkes med feil type/skjema.
    PERFORM 1 FROM public.artefakttype_register atr
     WHERE atr.artefakttype = p_artefakttype AND atr.eiermodul = p_modul_id
       AND atr.kontraktversjon = p_kontraktversjon
       AND atr.kontrakt_hash = p_kontrakt_hash;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'utsted_artefaktkapabilitet: artefakttype % er ikke '
            'registrert for modulen/kontrakten', p_artefakttype
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Codex P2: release_id VERIFISERES også. Oppdraget stempler kun modul/
    -- kontrakt/epoch ved claim — IKKE release — så uten denne porten kunne en
    -- gyldig claimet jobb tilskrives en vilkårlig/ikke-eksisterende release, som
    -- promoteringen senere leser tilbake fra artefaktet. Krev at (modul, release,
    -- kontrakt) er en REGISTRERT release (samme kontrakt som kapabiliteten).
    PERFORM 1 FROM public.modulrelease mr
     WHERE mr.modul_id = p_modul_id AND mr.release_id = p_release_id
       AND mr.kontraktversjon = p_kontraktversjon
       AND mr.kontrakt_hash = p_kontrakt_hash;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'utsted_artefaktkapabilitet: release % er ikke registrert '
            'for modulen/kontrakten', p_release_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- 035: miljøet MÅ være en faktisk deployment av nettopp denne releasen —
    -- ellers kunne en kapabilitet stemples med et miljø modulen ikke er
    -- deployet i, og innløsningsporten under ville sluppet feil arbeider inn.
    IF p_miljo IS NOT NULL THEN
        PERFORM 1 FROM public.moduldeployment d
         WHERE d.modul_id = p_modul_id AND d.release_id = p_release_id
           AND d.miljo = p_miljo;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'utsted_artefaktkapabilitet: %/% er ikke deployet '
                'i miljo %', p_modul_id, p_release_id, p_miljo
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
    END IF;
    INSERT INTO public.artefaktkapabilitet (jti, tenant, oppdrag_id, modul_id,
        release_id, kontraktversjon, kontrakt_hash, module_epoch, artefakttype,
        miljo, utloper)
        VALUES (p_jti, p_tenant, p_oppdrag_id, p_modul_id, p_release_id,
                p_kontraktversjon, p_kontrakt_hash, p_module_epoch, p_artefakttype,
                p_miljo, v_utloper);
    RETURN QUERY SELECT p_jti, v_utloper;
END $$;

-- Innløs (idempotent, brenner IKKE): 017s kropp, med deploymentporten.
-- `p_miljo`/`p_release_id` er den AUTENTISERTE deploymenten, ikke noe
-- kalleren kan velge fritt for seg selv: HTTP-laget leser dem av
-- modultokenet. Er de NULL (legacy api-token), matcher innløsningen kun
-- kapabiliteter som selv er miljøløse — et modultokens kapabilitet kan
-- altså ikke hentes ut av en miljøløs credential heller.
DROP FUNCTION IF EXISTS innlos_artefaktkapabilitet(TEXT, TEXT);
CREATE OR REPLACE FUNCTION innlos_artefaktkapabilitet(
    p_jti TEXT, p_modul_id TEXT, p_miljo TEXT DEFAULT NULL,
    p_release_id TEXT DEFAULT NULL)
RETURNS TABLE (tenant TEXT, oppdrag_id BIGINT, release_id TEXT,
               kontraktversjon INT, kontrakt_hash TEXT, module_epoch BIGINT,
               artefakttype TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    RETURN QUERY
    SELECT k.tenant, k.oppdrag_id, k.release_id, k.kontraktversjon,
           k.kontrakt_hash, k.module_epoch, k.artefakttype
      FROM public.artefaktkapabilitet k
     WHERE k.jti = p_jti AND k.modul_id = p_modul_id
       AND k.miljo IS NOT DISTINCT FROM p_miljo
       -- Miljøet skiller de to verdenene; er det satt, er kalleren en
       -- autentisert deployment og DA må releasen stemme også (to releaser
       -- av samme modul kan være claiming i samme miljø under hver sin
       -- kontraktversjon).
       AND (p_miljo IS NULL
            OR k.release_id IS NOT DISTINCT FROM p_release_id)
       AND k.status <> 'feilet'
       AND (k.status = 'brukt' OR k.utloper > now());
END $$;

REVOKE ALL ON FUNCTION utsted_artefaktkapabilitet(TEXT, BIGINT, TEXT, TEXT, INT, TEXT, BIGINT, TEXT, TEXT, INT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION innlos_artefaktkapabilitet(TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION utsted_artefaktkapabilitet(TEXT, BIGINT, TEXT, TEXT, INT, TEXT, BIGINT, TEXT, TEXT, INT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION innlos_artefaktkapabilitet(TEXT, TEXT, TEXT, TEXT) TO disponit;
RESET ROLE;
-- SECURITY DEFINER kjører som domene_eier: den nye miljøporten i
-- `utsted_artefaktkapabilitet` leser deploymentregisteret (som 017 måtte
-- gi den `modulrelease` for release-porten). Kjøres som migrator (eier).
GRANT SELECT ON moduldeployment TO disponit_domene_eier;
SET LOCAL ROLE disponit_modul_eier;
REVOKE ALL ON FUNCTION utsted_onboarding_hemmelighet(TEXT, TEXT, TEXT, UUID, TEXT, INT, INT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION innlos_onboarding(UUID, TEXT, UUID, TEXT, INT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION verifiser_modultoken(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION roter_modultoken(UUID, UUID, TEXT, INT, TEXT, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION tilbakekall_modultoken(UUID, TEXT, TEXT) FROM PUBLIC;
-- Runtime (API-et) er den eneste nettverksveien inn; utstedelse og
-- tilbakekalling er i tillegg scope-gatet (`modules:onboard`) i HTTP-laget.
GRANT EXECUTE ON FUNCTION utsted_onboarding_hemmelighet(TEXT, TEXT, TEXT, UUID, TEXT, INT, INT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION innlos_onboarding(UUID, TEXT, UUID, TEXT, INT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION verifiser_modultoken(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION roter_modultoken(UUID, UUID, TEXT, INT, TEXT, UUID) TO disponit;
GRANT EXECUTE ON FUNCTION tilbakekall_modultoken(UUID, TEXT, TEXT) TO disponit;

RESET ROLE;

-- Eieren (modul_eier) må kunne SKRIVE de nye tabellene når funksjonene
-- kjører (SECURITY DEFINER kjører som eier). Grantene bor HER, sammen med
-- funksjonene (PR-013-lærdom). Kjøres som migrator (eier av tabellene).
GRANT SELECT, INSERT, UPDATE, DELETE ON modul_onboarding TO disponit_modul_eier;
GRANT SELECT, INSERT, UPDATE ON modultoken TO disponit_modul_eier;
GRANT SELECT, INSERT ON modultoken_hendelse TO disponit_modul_eier;
-- Runtime og modulroller har INGEN direkte skriving (klarsignalet §3) —
-- og heller ingen lesing: `verifiser_modultoken` er lese-veien.
REVOKE ALL ON modul_onboarding, modultoken, modultoken_hendelse FROM PUBLIC;
REVOKE ALL ON modul_onboarding, modultoken, modultoken_hendelse FROM disponit;

-- ------------------------------------------------------------
-- 6. Varsling: familiehorisonten skal varsles (30/7/1 døgn), ikke
--    oppdages. Varseltabellens lukkede CHECK-er utvides additivt.
-- ------------------------------------------------------------
DO $$
DECLARE c TEXT;
BEGIN
    SELECT conname INTO c FROM pg_constraint
     WHERE conrelid = 'varsel'::regclass
       AND pg_get_constraintdef(oid) LIKE '%attestering_venter%';
    IF c IS NOT NULL THEN
        EXECUTE format('ALTER TABLE public.varsel DROP CONSTRAINT %I', c);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'varsel'::regclass
                      AND conname = 'varsel_art_chk') THEN
        ALTER TABLE public.varsel ADD CONSTRAINT varsel_art_chk
            CHECK (art IN ('attestering_venter', 'validering_venter',
                           'runde_apnet', 'tokenfamilie_utloper'));
    END IF;
    SELECT conname INTO c FROM pg_constraint
     WHERE conrelid = 'varsel'::regclass
       AND pg_get_constraintdef(oid) LIKE '%policyutkast%'
       AND pg_get_constraintdef(oid) LIKE '%ressurs_type%';
    IF c IS NOT NULL THEN
        EXECUTE format('ALTER TABLE public.varsel DROP CONSTRAINT %I', c);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'varsel'::regclass
                      AND conname = 'varsel_ressurs_type_chk') THEN
        ALTER TABLE public.varsel ADD CONSTRAINT varsel_ressurs_type_chk
            CHECK (ressurs_type IN ('policyutkast', 'modultoken'));
    END IF;
END $$;

-- ------------------------------------------------------------
-- 7. Seed: testartefakttypen (klarsignalet §8). `audit.wcag.rapport`
--    registreres i 014c, ikke her. Prefikset `test.` er reservert og
--    utledes ALDRI for et produksjonsmiljø (håndhevet i API-ets
--    utledning + deploy-port). Modulen er en TESTKONTRAKT og settes
--    aldri `aktiv` i produksjon.
-- ------------------------------------------------------------
INSERT INTO modulhode (modul_id, status)
    VALUES ('m_test_onboarding', 'installert')
    ON CONFLICT (modul_id) DO NOTHING;
-- Testkontrakten: sideeffektfri og direkte reversibel — den finnes bare
-- for at onboarding-selvtesten skal ha en gyldig kontraktbinding.
-- Hashene er sha256 over JCS-kanonisk form av selvtest-skjemaet
-- ({kjoring_id, tidspunkt, resultat[ok|feil]}, additionalProperties:
-- false, beregnet med policy_validator.jcs) — payload og kvittering ER
-- samme skjema i selvtesten.
INSERT INTO modulkontrakt (modul_id, kontraktversjon, kontrakt_hash,
                           payload_schema_hash, kvittering_schema_hash,
                           sideeffektklasse, reversibilitet)
    SELECT 'm_test_onboarding', 1,
           'e30ef85662f0967117cf3d0dc2e28b9efd3da50b501429be79bd8e5cea5fc40e',
           'e30ef85662f0967117cf3d0dc2e28b9efd3da50b501429be79bd8e5cea5fc40e',
           'e30ef85662f0967117cf3d0dc2e28b9efd3da50b501429be79bd8e5cea5fc40e',
           'sideeffektfri', 'direkte'
    WHERE NOT EXISTS (SELECT 1 FROM modulkontrakt
                       WHERE modul_id = 'm_test_onboarding'
                         AND kontraktversjon = 1);
INSERT INTO artefakttype_register
    (artefakttype, eiermodul, kontraktversjon, kontrakt_hash, skjema_hash)
    SELECT 'test.onboarding.kvittering', 'm_test_onboarding', 1,
           'e30ef85662f0967117cf3d0dc2e28b9efd3da50b501429be79bd8e5cea5fc40e',
           -- sha256 over JCS-kanonisk form av skjemaet
           -- {kjoring_id, tidspunkt, resultat[ok|feil]},
           -- additionalProperties: false (beregnet med policy_validator.jcs)
           'e30ef85662f0967117cf3d0dc2e28b9efd3da50b501429be79bd8e5cea5fc40e'
    WHERE NOT EXISTS (SELECT 1 FROM artefakttype_register
                       WHERE artefakttype = 'test.onboarding.kvittering');

-- ------------------------------------------------------------
-- 8. Familiehorisont-varslene (30/7/1 døgn, klarsignalet §5): en modul
--    som ikke er rullet på et år stopper og krever et menneske — det
--    skal VARSLES, ikke oppdages. Varslene skrives av senderens
--    pre-pass via denne funksjonen (varselsender-rollen eier ingenting
--    og har ellers ingen vei inn i modultoken); mottakerne er de aktive
--    `admin`-medlemmene i plattformtenanten (kallerens serverkonfig).
--    Unikhetsnøkkelen (bruker · art · ressurs · hendelse=terskel) gjør
--    sveipen idempotent — hver terskel varsles én gang per familie.
--
--    KANALVALGET GJELDER OGSÅ HER (Codex P1). `varsel.epost_status` har
--    DEFAULT 'koet', så en insert som utelater kolonnen køer e-post til
--    ALLE — også dem som har valgt `kun_portal`. Denne funksjonen gjør
--    derfor nøyaktig det `varsel.opprett` gjør i Python: tar mottakerens
--    kanalvalg-lås (advisory-klasse 615774026, andre halvdel hashen av
--    tenant + bruker — samme nøkkel som `varsel.KANALVALGNOKKEL`, ellers
--    serialiserer de to veiene ikke mot hverandre i det hele tatt), LESER
--    valget under den låsen, og setter `ikke_aktuelt` for portal-bare
--    mottakere. Uten låsen kunne en avmelding som skjer akkurat nå ha
--    ryddet køen FØR raden vår ble satt inn, og e-posten gått ut likevel.
--    Mottakerne låses i STIGENDE bruker_id, samme retning som
--    `varsel.mottakere_for_runde` sorterer — motsatt retning ville vært
--    en vranglås mot en aktiveringsrunde som åpnes samtidig.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_modul_eier;

CREATE OR REPLACE FUNCTION varsle_tokenfamilie_utlop(p_tenant TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_n INT := 0; f RECORD; b RECORD; t INT; v_kanal TEXT; v_rader INT;
BEGIN
    -- varsel/brukermedlemskap står under FORCE RLS med tenant-GUC-en som
    -- predikat — funksjonen setter den LOKALT for sin egen transaksjon.
    PERFORM set_config('disponit.tenant', p_tenant, true);
    PERFORM set_config('disponit.aktor', 'tokenfamilievarsel', true);
    FOREACH t IN ARRAY ARRAY[30, 7, 1] LOOP
        FOR f IN
            SELECT o.onboarding_id, o.modul_id, o.miljo, o.release_id,
                   o.familie_utloper
              FROM public.modul_onboarding o
             WHERE o.innlost_ts IS NOT NULL
               AND o.familie_utloper > now()
               AND o.familie_utloper <= now() + make_interval(days => t)
               -- bare familier med et LEVENDE token: en familie der alt
               -- er tilbakekalt/utløpt har ingenting å miste ved fristen.
               AND EXISTS (SELECT 1 FROM public.modultoken mt
                            WHERE mt.onboarding_id = o.onboarding_id
                              AND (mt.tilbakekalt_ts IS NULL
                                   OR mt.tilbakekalt_ts > now())
                              AND mt.utloper > now())
        LOOP
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
                -- Fraværende rad er IKKE «av»: standarden er e-post +
                -- portal, samme regel som `varsel._kanal`.
                INSERT INTO public.varsel (tenant, bruker_id, art,
                                           ressurs_type, ressurs_id, hendelse,
                                           tekstnokkel, parametre,
                                           epost_status)
                VALUES (p_tenant, b.bruker_id, 'tokenfamilie_utloper',
                        'modultoken', f.onboarding_id::text, t::text,
                        'varsel.tokenfamilie_utloper',
                        jsonb_build_object('modul_id', f.modul_id,
                                           'miljo', f.miljo,
                                           'release_id', f.release_id,
                                           'familie_utloper',
                                           f.familie_utloper,
                                           'dager', t),
                        CASE WHEN COALESCE(v_kanal, 'epost_og_portal')
                                  = 'kun_portal'
                             THEN 'ikke_aktuelt' ELSE 'koet' END)
                     ON CONFLICT DO NOTHING;
                -- ROW_COUNT og ikke RETURNING: eieren har INSERT på
                -- `varsel`, ikke SELECT, og RETURNING ville krevd
                -- lesetilgang på kolonnen. En skrivefunksjon skal ikke
                -- måtte kunne LESE hele varseltabellen for å telle sine
                -- egne innsettinger. ON CONFLICT DO NOTHING gir 0 når
                -- raden alt fantes, så telleren er FAKTISK opprettede
                -- varsler — ikke antall familier sveipen så på.
                GET DIAGNOSTICS v_rader = ROW_COUNT;
                v_n := v_n + v_rader;
            END LOOP;
        END LOOP;
    END LOOP;
    RETURN v_n;
END $$;

-- KUN SENDERROLLEN (Codex P1). Funksjonen er kryss-tenant: den tar tenanten
-- som parameter, setter DENS RLS-kontekst, leser dens aktive administratorer
-- og køer varsler til dem. Et grant til web-runtime ville gitt en
-- kompromittert forespørselsvei nøyaktig det vinduet `disponit_varselsender`
-- finnes for å nekte den — samme grunn som `migrer.py` bevisst holder
-- `varsel_klaim_epost`/`varsel_rekoe` unna `disponit`. REVOKE-en står fordi
-- en tidligere versjon av denne migrasjonen GA grantet: en rettighet som
-- bare slutter å bli gitt, er ikke trukket tilbake.
REVOKE ALL ON FUNCTION varsle_tokenfamilie_utlop(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION varsle_tokenfamilie_utlop(TEXT) FROM disponit;
GRANT EXECUTE ON FUNCTION varsle_tokenfamilie_utlop(TEXT)
    TO disponit_varselsender;

RESET ROLE;
-- Eieren trenger lese modul_onboarding/modultoken (har det, §-grantene
-- over) og skrive varsel + lese medlemskap — begge under FORCE RLS, som
-- funksjonen tilfredsstiller via tenant-GUC-en.
GRANT SELECT ON brukermedlemskap TO disponit_modul_eier;
GRANT INSERT ON varsel TO disponit_modul_eier;
-- ... og lese kanalvalget, ellers kunne funksjonen ikke respektert det.
GRANT SELECT ON varselvalg TO disponit_modul_eier;
