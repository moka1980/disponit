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
    -- UNIQUE er én-etterfølger-garantien I LAGRINGEN, ikke bare i
    -- rotasjonsfunksjonens radlås: to samtidige rotasjoner kan aldri
    -- begge committe en etterfølger (portene 21, 30).
    forgjenger       UUID UNIQUE REFERENCES modultoken (token_id),
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
        OR NEW.utloper         IS DISTINCT FROM OLD.utloper
        OR NEW.opprettet       IS DISTINCT FROM OLD.opprettet
        OR (OLD.tilbakekalt_ts IS NOT NULL
            AND (NEW.tilbakekalt_ts IS NULL
              OR NEW.tilbakekalt_ts > OLD.tilbakekalt_ts
              OR (NEW.tilbakekalt_ts = OLD.tilbakekalt_ts
                  AND NEW.tilbakekalt_grunn
                      IS DISTINCT FROM OLD.tilbakekalt_grunn))))
    EXECUTE FUNCTION avvis_endring();

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
        v_familie TIMESTAMPTZ;
BEGIN
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
    SELECT h.status INTO v_status FROM public.modulhode h
     WHERE h.modul_id = p_modul_id;
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
    DELETE FROM public.modul_onboarding o
     WHERE o.modul_id = p_modul_id AND o.miljo = p_miljo
       AND o.release_id = p_release_id AND o.innlost_ts IS NULL
       AND o.utloper < now();
    v_utloper := now() + make_interval(mins => p_ttl_minutter);
    v_familie := now() + make_interval(days => p_familie_dager);
    INSERT INTO public.modul_onboarding
        (onboarding_id, modul_id, miljo, release_id, hemmelighet_hash,
         familie_utloper, utstedt_av, utloper)
        VALUES (p_onboarding_id, p_modul_id, p_miljo, p_release_id,
                p_hemmelighet_hash, v_familie, p_aktor, v_utloper);
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
CREATE OR REPLACE FUNCTION innlos_onboarding(
    p_onboarding_id UUID, p_hemmelighet_hash TEXT,
    p_token_id UUID, p_token_mac TEXT, p_token_dager INT, p_aktor TEXT)
RETURNS TABLE (token_id UUID, modul_id TEXT, miljo TEXT, release_id TEXT,
               utstedt_epoch BIGINT, utloper TIMESTAMPTZ,
               familie_utloper TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE o RECORD; v_epoch BIGINT; v_utloper TIMESTAMPTZ;
BEGIN
    SELECT * INTO o FROM public.modul_onboarding ob
     WHERE ob.onboarding_id = p_onboarding_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'innlosning: ukjent onboarding'
            USING ERRCODE = 'no_data_found';
    END IF;
    -- Hemmeligheten sammenlignes som pepper-MAC (kalleren har regnet den) —
    -- klartekst finnes aldri her. Feil hemmelighet og brukt hemmelighet er
    -- SAMME feil utad (ingen orakel for gjettverk).
    IF o.hemmelighet_hash IS DISTINCT FROM p_hemmelighet_hash
       OR o.innlost_ts IS NOT NULL THEN
        INSERT INTO public.modultoken_hendelse
            (onboarding_id, modul_id, miljo, release_id, hendelse, aktor,
             detalj)
            VALUES (o.onboarding_id, o.modul_id, o.miljo, o.release_id,
                    'avvist_bruk', p_aktor,
                    jsonb_build_object('grunn', 'innlosning_avvist'));
        RAISE EXCEPTION 'innlosning: avvist'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF o.utloper < now() THEN
        RAISE EXCEPTION 'innlosning: hemmeligheten er utlopt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Epoch fryses i tokenet NÅ. Claim-veien krever likhet med gjeldende
    -- epoch ved hver bruk; rotasjon ARVER denne verdien og plukker aldri
    -- opp en ny (port 23).
    SELECT h.module_epoch INTO v_epoch FROM public.modulhode h
     WHERE h.modul_id = o.modul_id;
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
                        v_epoch, v_utloper, o.familie_utloper;
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
-- familiehorisonten, uten menneske. Radlås på forgjengeren + UNIQUE
-- (forgjenger) = nøyaktig én etterfølger (portene 20–21, 27–30).
-- Forgjengeren tilbakekalles med 15 minutters nåde (in-flight-requests) —
-- nåden er kodet som et FREMTIDIG tilbakekalt_ts, så identitetstriggeren
-- gjelder uendret. Epoch ARVES fra forgjengeren, aldri fra modulhodet.
CREATE OR REPLACE FUNCTION roter_modultoken(
    p_forgjenger UUID, p_ny_token_id UUID, p_ny_mac TEXT,
    p_token_dager INT, p_aktor TEXT)
RETURNS TABLE (token_id UUID, utloper TIMESTAMPTZ,
               familie_utloper TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE g RECORD; v_utloper TIMESTAMPTZ;
BEGIN
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
    v_utloper := LEAST(now() + make_interval(days => p_token_dager),
                       g.familie_utloper);
    INSERT INTO public.modultoken
        (token_id, token_mac, onboarding_id, familie_utloper, modul_id,
         miljo, release_id, utstedt_epoch, forgjenger, utloper)
        VALUES (p_ny_token_id, p_ny_mac, g.onboarding_id, g.familie_utloper,
                g.modul_id, g.miljo, g.release_id, g.utstedt_epoch,
                p_forgjenger, v_utloper);
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
                jsonb_build_object('forgjenger', p_forgjenger::text));
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
SET LOCAL ROLE disponit_modul_eier;
REVOKE ALL ON FUNCTION utsted_onboarding_hemmelighet(TEXT, TEXT, TEXT, UUID, TEXT, INT, INT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION innlos_onboarding(UUID, TEXT, UUID, TEXT, INT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION verifiser_modultoken(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION roter_modultoken(UUID, UUID, TEXT, INT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION tilbakekall_modultoken(UUID, TEXT, TEXT) FROM PUBLIC;
-- Runtime (API-et) er den eneste nettverksveien inn; utstedelse og
-- tilbakekalling er i tillegg scope-gatet (`modules:onboard`) i HTTP-laget.
GRANT EXECUTE ON FUNCTION utsted_onboarding_hemmelighet(TEXT, TEXT, TEXT, UUID, TEXT, INT, INT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION innlos_onboarding(UUID, TEXT, UUID, TEXT, INT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION verifiser_modultoken(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION roter_modultoken(UUID, UUID, TEXT, INT, TEXT) TO disponit;
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
DECLARE v_n INT := 0; f RECORD; b RECORD; t INT; v_kanal TEXT; v_id BIGINT;
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
                     ON CONFLICT DO NOTHING
                  RETURNING id INTO v_id;
                -- INTO setter v_id til NULL når ON CONFLICT slukte raden,
                -- så telleren er FAKTISK opprettede varsler — ikke antall
                -- familier sveipen så på.
                IF v_id IS NOT NULL THEN
                    v_n := v_n + 1;
                END IF;
            END LOOP;
        END LOOP;
    END LOOP;
    RETURN v_n;
END $$;

REVOKE ALL ON FUNCTION varsle_tokenfamilie_utlop(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION varsle_tokenfamilie_utlop(TEXT)
    TO disponit_varselsender;
GRANT EXECUTE ON FUNCTION varsle_tokenfamilie_utlop(TEXT) TO disponit;

RESET ROLE;
-- Eieren trenger lese modul_onboarding/modultoken (har det, §-grantene
-- over) og skrive varsel + lese medlemskap — begge under FORCE RLS, som
-- funksjonen tilfredsstiller via tenant-GUC-en.
GRANT SELECT ON brukermedlemskap TO disponit_modul_eier;
GRANT INSERT ON varsel TO disponit_modul_eier;
-- ... og lese kanalvalget, ellers kunne funksjonen ikke respektert det.
GRANT SELECT ON varselvalg TO disponit_modul_eier;
