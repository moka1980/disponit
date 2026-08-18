-- ============================================================
-- 038 — Outbox-generalisering + bestillingsveien (014c v4-klarsignalet)
--
-- Outboxen har vært M-37-forankret på skjemanivå siden 005: hvert
-- oppdrag ER en reparasjon av et unntak (NOT NULL + FK på
-- unntak_id/loggpost_id/repair_operation_id). 014c §9 trenger oppdrag
-- født av en TILLAT-BESLUTNING — uten unntak, uten reparasjon. Å fylle
-- trioen med liksom-rader ville løyet i revisjonssporet; i stedet:
--
--   * `opprinnelse` er et EKSPLISITT felt (aldri utledet av
--     NULL-mønster), immutabelt, med en CHECK som dekker begge
--     kombinasjonene uttømmende. En M-37-vei kan ikke lage et
--     beslutningsoppdrag og omvendt.
--   * Saken peker på oppdraget, aldri omvendt: `oppdrag.unntak_id`
--     betyr «født som reparasjon av», ikke «har en sak».
--     `sikre_sak_for_oppdrag` er den ENE veien til en sak for et
--     beslutningsoppdrag — idempotent fordi UNIK-indeksen
--     `en_apen_sak_per_oppdrag_arsak` gjør den det, ikke fordi
--     funksjonen er skrevet forsiktig. Terminale saker gjenbrukes aldri.
--   * `bestilling_idempotens` binder HELE den normaliserte intensjonen
--     (hash) til nøkkelen og er immutabel også mot DELETE — kunne raden
--     slettes, kunne en nøkkel gjenbrukes med ny intensjon (samme
--     mønster som artefaktskjema i 036).
--   * Begge opphavsveiene går gjennom hver sin HERDEDE funksjon som
--     setter `opprinnelse` selv; verdien kommer aldri fra en request,
--     og verken runtime eller arbeideren har direkte INSERT på
--     `oppdrag` lenger (grantene strammes i migrer.py, som eier
--     runtime-rettighetene).
-- ============================================================

-- ------------------------------------------------------------
-- 1. Opphav på oppdraget
-- ------------------------------------------------------------
ALTER TABLE oppdrag
    ADD COLUMN IF NOT EXISTS opprinnelse TEXT NOT NULL
        DEFAULT 'm37_reparasjon'
        CHECK (opprinnelse IN ('m37_reparasjon', 'beslutning'));
-- DEFAULT kun for å fylle eksisterende rader (alle ER reparasjoner).
-- Fjernes umiddelbart: nye rader må velge bevisst (port 6).
ALTER TABLE oppdrag ALTER COLUMN opprinnelse DROP DEFAULT;

ALTER TABLE oppdrag
    ALTER COLUMN unntak_id           DROP NOT NULL,
    ALTER COLUMN loggpost_id         DROP NOT NULL,
    ALTER COLUMN repair_operation_id DROP NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'oppdrag'::regclass
                      AND conname = 'oppdrag_opprinnelse_komplett') THEN
        ALTER TABLE oppdrag ADD CONSTRAINT oppdrag_opprinnelse_komplett CHECK (
            (opprinnelse = 'm37_reparasjon'
               AND unntak_id IS NOT NULL
               AND loggpost_id IS NOT NULL
               AND repair_operation_id IS NOT NULL)
            OR
            (opprinnelse = 'beslutning'
               AND unntak_id IS NULL
               AND loggpost_id IS NULL
               AND repair_operation_id IS NULL
               AND beslutning_loggpost_id IS NOT NULL));
    END IF;
END $$;

DROP TRIGGER IF EXISTS oppdrag_opprinnelse_immutable ON oppdrag;
CREATE TRIGGER oppdrag_opprinnelse_immutable
    BEFORE UPDATE ON oppdrag
    FOR EACH ROW WHEN (NEW.opprinnelse IS DISTINCT FROM OLD.opprinnelse)
    EXECUTE FUNCTION avvis_endring();

-- ------------------------------------------------------------
-- 2. Saker for oppdrag: unntaket opprettes når noe FAKTISK er et unntak.
--    Kolonnene er nye — saken peker på oppdraget. `terminal` er en
--    LAGRET, generert kolonne: hvilke statuser som er endelige er en
--    egenskap ved lagringen (indeksen under leser den), ikke ved hver
--    enkelt kaller. `manuell` og godkjennings-/verifikasjonsstatusene er
--    LEVENDE parkeringer, ikke endelige.
-- ------------------------------------------------------------
ALTER TABLE unntak
    ADD COLUMN IF NOT EXISTS oppdrag_id BIGINT,
    ADD COLUMN IF NOT EXISTS arsak TEXT
        CHECK (arsak IN ('evidensfrist', 'sikkerhet'));
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_attribute
                    WHERE attrelid = 'unntak'::regclass
                      AND attname = 'terminal') THEN
        ALTER TABLE unntak ADD COLUMN terminal BOOLEAN
            GENERATED ALWAYS AS (status IN ('løst', 'avvist')) STORED;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'unntak'::regclass
                      AND conname = 'unntak_oppdrag_fk') THEN
        ALTER TABLE unntak ADD CONSTRAINT unntak_oppdrag_fk
            FOREIGN KEY (tenant, oppdrag_id)
            REFERENCES oppdrag (tenant, id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'unntak'::regclass
                      AND conname = 'unntak_oppdragssak_komplett') THEN
        -- En oppdragssak har alltid en årsak, og en årsak alltid et
        -- oppdrag — halvferdige koblinger finnes ikke.
        ALTER TABLE unntak ADD CONSTRAINT unntak_oppdragssak_komplett
            CHECK ((oppdrag_id IS NULL) = (arsak IS NULL));
    END IF;
END $$;

-- Én IKKE-TERMINAL sak per (oppdrag, årsak): indeksen ER idempotensen i
-- `sikre_sak_for_oppdrag`, og den er også det som håndhever «terminal
-- gjenbrukes aldri» — en terminal sak forlater indeksen, og neste
-- hendelse får lov til å opprette en ny.
CREATE UNIQUE INDEX IF NOT EXISTS en_apen_sak_per_oppdrag_arsak
    ON unntak (tenant, oppdrag_id, arsak)
    WHERE NOT terminal AND oppdrag_id IS NOT NULL;

-- BINDINGEN ER UFORANDERLIG (Codex P2).
--
-- Runtime har fortsatt direkte UPDATE på `unntak` (statusmaskinen og
-- claim-feltene går den veien), og de to nye kolonnene sto utenfor
-- ENHVER lås. Både den parvise CHECKen og FK-en er tilfredsstilt av et
-- HVILKET SOM HELST gyldig par, så en eksisterende sak kunne få
-- oppdraget og årsaken sin skrevet om i ettertid — og all
-- append-only-historikken saken bærer ville da tilhøre et annet oppdrag
-- enn det den ble født av. Det er ikke et tapt felt, det er et
-- revisjonsspor som peker feil.
--
-- Egen trigger, ikke en utvidelse av `unntak_kolonnelaas`: den er
-- omdefinert i 003/005/007/011, og å gjenskrive den gjeldende kroppen her
-- er nøyaktig den «skriv lista på nytt fra hukommelsen»-feilen §2 over
-- unngår for hendelses-CHECKen. Samme form som
-- `oppdrag_opprinnelse_immutable` i §1 og som 035-familien.
DROP TRIGGER IF EXISTS unntak_oppdragsbinding_immutable ON unntak;
CREATE TRIGGER unntak_oppdragsbinding_immutable
    BEFORE UPDATE ON unntak
    FOR EACH ROW WHEN (
           NEW.oppdrag_id IS DISTINCT FROM OLD.oppdrag_id
        OR NEW.arsak      IS DISTINCT FROM OLD.arsak)
    EXECUTE FUNCTION avvis_endring();

-- Historikk-enumet utvides additivt med sakskoblingens hendelse. Listen
-- HARDKODES IKKE (den har vokst i 011/016/017 og vokser videre): den
-- GJELDENDE definisjonen leses fra katalogen og 'sak_for_oppdrag' skjøtes
-- inn — å gjenskrive lista fra hukommelsen er nøyaktig feilen som mistet
-- rettelser tre ganger i 028.
DO $$
DECLARE c TEXT; def TEXT;
BEGIN
    SELECT conname, pg_get_constraintdef(oid) INTO c, def
      FROM pg_constraint
     WHERE conrelid = 'unntak_historikk'::regclass
       AND pg_get_constraintdef(oid) LIKE '%claim_fornyet%';
    IF c IS NOT NULL AND def NOT LIKE '%sak_for_oppdrag%' THEN
        EXECUTE format('ALTER TABLE unntak_historikk DROP CONSTRAINT %I', c);
        -- def har formen «CHECK ((hendelse = ANY (ARRAY[...])))» eller
        -- «CHECK (hendelse IN (...))» — begge slutter på «])))» hhv. «)))»;
        -- vi skjøter inn det nye elementet før den siste lukkingen.
        IF def LIKE '%ARRAY[%' THEN
            -- Formen er «… 'avklaring_kreves'::text])))» — det nye
            -- elementet skjøtes inn før den avsluttende «])))».
            def := regexp_replace(def, '\]\)\)\)$',
                                  ', ''sak_for_oppdrag''::text])))');
        ELSE
            def := regexp_replace(def, '\)\)$',
                                  ', ''sak_for_oppdrag''))');
        END IF;
        IF def NOT LIKE '%sak_for_oppdrag%' THEN
            RAISE EXCEPTION 'unntak_historikk: kunne ikke utvide'
                ' hendelses-CHECKen — uventet definisjonsform: %', def;
        END IF;
        EXECUTE 'ALTER TABLE unntak_historikk ADD CONSTRAINT ' || quote_ident(c)
             || ' ' || def;
    END IF;
END $$;

-- ------------------------------------------------------------
-- 3. Idempotens for bestillinger. Immutabel OGSÅ mot DELETE: kunne raden
--    slettes, kunne nøkkelen gjenbrukes med ny intensjon. `oppdrag_id`
--    er BIGINT (klarsignalet skrev UUID; `oppdrag.id` er BIGINT IDENTITY
--    siden 005 — radens FAKTISKE nøkkeltype vinner).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bestilling_idempotens (
    tenant           TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    idempotensnokkel TEXT NOT NULL CHECK (length(idempotensnokkel)
                                          BETWEEN 8 AND 200),
    intensjonshash   TEXT NOT NULL CHECK (intensjonshash ~ '^[0-9a-f]{64}$'),
    oppdrag_id       BIGINT,          -- NULL når beslutningen ikke ga oppdrag
    beslutning       TEXT NOT NULL CHECK (beslutning IN
                                          ('tillat', 'stopp', 'brudd')),
    -- HELE det opprinnelige svaret, uten `request_id` (Codex P2).
    --
    -- Første utgave gjenskapte bare `beslutning` + `oppdrag_id` ved
    -- gjenspill, mens førstesvaret også bærer `begrunnelse` for
    -- STOPP/BRUDD og `unntak_id` for BRUDD. Nettopp i gjenspillets
    -- primærscenario — svaret gikk tapt, klienten prøver igjen — kunne
    -- flaten dermed hverken annonsere STOPP-grunnen eller si hvilken
    -- unntakssak som ble opprettet. Et gjenspill som svarer noe ANNET
    -- enn originalen er ikke idempotent, det er bare stille.
    --
    -- Lagret og ikke rekonstruert, samme mønster som kjernens egen
    -- `idempotens.respons` (som replayer byte-identisk): en
    -- rekonstruksjon ville vært en ANDRE implementasjon av svarformen,
    -- fri til å gli fra den første.
    svarkropp        JSONB NOT NULL DEFAULT '{}'::jsonb
                     CHECK (jsonb_typeof(svarkropp) = 'object'),
    opprettet        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, idempotensnokkel),
    FOREIGN KEY (tenant, oppdrag_id) REFERENCES oppdrag (tenant, id)
);
-- For en base som alt har kjørt en tidligere 038: kolonnen legges til.
-- DEFAULT-en er kun for den overgangen — nye rader skal BÆRE svaret, og
-- raden er uansett immutabel, så en tom kropp kan ikke fylles i ettertid.
ALTER TABLE bestilling_idempotens
    ADD COLUMN IF NOT EXISTS svarkropp JSONB NOT NULL DEFAULT '{}'::jsonb;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'bestilling_idempotens'::regclass
                      AND conname = 'bestilling_idempotens_svarkropp_objekt')
    THEN
        ALTER TABLE bestilling_idempotens
            ADD CONSTRAINT bestilling_idempotens_svarkropp_objekt
            CHECK (jsonb_typeof(svarkropp) = 'object');
    END IF;
END $$;

DROP TRIGGER IF EXISTS bestilling_idempotens_immutable
    ON bestilling_idempotens;
CREATE TRIGGER bestilling_idempotens_immutable
    BEFORE UPDATE OR DELETE ON bestilling_idempotens
    FOR EACH ROW EXECUTE FUNCTION avvis_endring();

DO $$
BEGIN
    EXECUTE 'ALTER TABLE bestilling_idempotens ENABLE ROW LEVEL SECURITY';
    EXECUTE 'ALTER TABLE bestilling_idempotens FORCE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS tenant_isolasjon ON bestilling_idempotens';
    EXECUTE
        'CREATE POLICY tenant_isolasjon ON bestilling_idempotens
            USING      (tenant = current_setting(''disponit.tenant'', true))
            WITH CHECK (tenant = current_setting(''disponit.tenant'', true))';
END $$;

-- ------------------------------------------------------------
-- 4. De herdede opphavsfunksjonene (eier: disponit_m37_claimer — samme
--    eier som resten av outbox-maskineriet). Runtime og arbeideren mister
--    direkte INSERT på `oppdrag` (migrer.py); disse to er de ENESTE
--    skriveveiene, og hver setter `opprinnelse` selv.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_m37_claimer;

-- TENANTPORTEN FOR DEFINER-VEIENE (Codex P1).
--
-- Funksjonene under er SECURITY DEFINER og kjører altså som
-- `disponit_m37_claimer`. Eierens `m37_dispatcher`-policy (005/007) er
-- PERMISSIV og slipper gjennom hver eneste rad så lenge
-- `current_user = 'disponit_m37_claimer'` — den er nettopp DERFOR
-- formulert slik: inne i en definer-funksjon SKAL maskineriet se på tvers
-- av tenanter. Følgen er at RLS ikke lenger sier noe om `p_tenant`:
-- parameteret er kallerens ord alene.
--
-- For funksjoner web-API-rollen kan kalle er det en tenantrømning. Med
-- EXECUTE på `sikre_sak_for_oppdrag` kunne en kompromittert runtime gjette
-- et oppdragsnummer hos en ANNEN tenant og få opprettet — og deretter lest
-- ut — en sak for det, tvers gjennom isolasjonsmodellen repoet er bygget
-- rundt (en kompromittert runtime skal se ÉN tenant om gangen).
--
-- Porten binder derfor `p_tenant` til den tenantkonteksten kalleren
-- FAKTISK står i, den samme GUC-en `sett_kontekst` setter og all vanlig
-- RLS måles mot. Fail-closed: uten kontekst (NULL/tom) er det ingen
-- tenant å være lik, og kallet avvises. Kryss-tenant-autoritet finnes
-- fortsatt, men bare der den er innelukket i én funksjon med sitt eget
-- grant — reaperen under, som binder konteksten til RADENS tenant før
-- hvert saks-kall.
CREATE OR REPLACE FUNCTION krev_tenantkontekst(p_tenant TEXT,
                                               p_funksjon TEXT)
RETURNS VOID LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF p_tenant IS NULL OR p_tenant IS DISTINCT FROM
       nullif(current_setting('disponit.tenant', true), '') THEN
        RAISE EXCEPTION '%: p_tenant (%) er ikke kallerens tenantkontekst'
            ' — definer-veiene binder tenanten til konteksten, aldri til'
            ' parameteret alene', p_funksjon, coalesce(p_tenant, '<null>')
            USING ERRCODE = 'insufficient_privilege';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION opprett_reparasjonsoppdrag(
    p_tenant TEXT, p_unntak_id BIGINT, p_loggpost_id BIGINT,
    p_repair_operation_id TEXT, p_oppdragstype TEXT, p_handling TEXT,
    p_eiermodul TEXT, p_payload BYTEA, p_key_id TEXT, p_nonce BYTEA,
    p_utforelsesfrist TIMESTAMPTZ, p_evidensfrist TIMESTAMPTZ,
    p_beslutning_loggpost_id BIGINT, p_koblingsstatus TEXT)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id BIGINT;
BEGIN
    PERFORM krev_tenantkontekst(p_tenant, 'opprett_reparasjonsoppdrag');
    INSERT INTO public.oppdrag (tenant, unntak_id, loggpost_id,
        repair_operation_id, oppdragstype, handling, eiermodul,
        payload_kryptert, key_id, nonce, utforelsesfrist, evidensfrist,
        beslutning_loggpost_id, koblingsstatus, opprinnelse)
    VALUES (p_tenant, p_unntak_id, p_loggpost_id, p_repair_operation_id,
            p_oppdragstype, p_handling, p_eiermodul, p_payload, p_key_id,
            p_nonce, p_utforelsesfrist, p_evidensfrist,
            p_beslutning_loggpost_id, p_koblingsstatus, 'm37_reparasjon')
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION opprett_beslutningsoppdrag(
    p_tenant TEXT, p_beslutning_loggpost_id BIGINT, p_oppdragstype TEXT,
    p_handling TEXT, p_eiermodul TEXT, p_payload BYTEA, p_key_id TEXT,
    p_nonce BYTEA, p_utforelsesfrist TIMESTAMPTZ,
    p_evidensfrist TIMESTAMPTZ)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id BIGINT;
BEGIN
    PERFORM krev_tenantkontekst(p_tenant, 'opprett_beslutningsoppdrag');
    -- Trioen er NULL per konstruksjon (CHECK-en i §1 håndhever det
    -- uansett) og koblingen er KOBLET fra fødselen: beslutningen ER
    -- loggposten oppdraget peker på.
    INSERT INTO public.oppdrag (tenant, oppdragstype, handling, eiermodul,
        payload_kryptert, key_id, nonce, utforelsesfrist, evidensfrist,
        beslutning_loggpost_id, koblingsstatus, opprinnelse)
    VALUES (p_tenant, p_oppdragstype, p_handling, p_eiermodul, p_payload,
            p_key_id, p_nonce, p_utforelsesfrist, p_evidensfrist,
            p_beslutning_loggpost_id, 'KOBLET', 'beslutning')
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;

-- Sak for et oppdrag — generaliseringen av «unntaket opprettes når noe
-- faktisk ER et unntak». Idempotensen bæres av indeksen (§2): to
-- samtidige kall serialiseres av unik-bruddet, og taperen leser vinnerens
-- rad. Saken arver oppdragets krypterte payload (samme tenant-DEK, samme
-- innhold — saken HANDLER om oppdraget) og fester lineage til
-- beslutningsloggposten for beslutningsoppdrag / loggposten for
-- reparasjoner.
CREATE OR REPLACE FUNCTION sikre_sak_for_oppdrag(
    p_tenant TEXT, p_oppdrag_id BIGINT, p_arsak TEXT, p_aktor TEXT,
    p_request_id TEXT)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE o RECORD; v_id BIGINT; v_logg BIGINT; v_policy TEXT; v_policy_hash TEXT;
BEGIN
    -- Tenantporten FØRST — før GUC-ene under settes og før noe leses.
    -- Dette er den API-kallbare formen, og uten porten var `p_tenant`
    -- kallerens frie valg (se `krev_tenantkontekst`).
    PERFORM krev_tenantkontekst(p_tenant, 'sikre_sak_for_oppdrag');
    -- Historikktriggeren på unntak krever aktør + request-id i GUC-ene.
    -- Funksjonen FÅR dem eksplisitt — den setter dem selv (LOCAL), så
    -- reaper-/kvitteringsveiene ikke er avhengige av at kalleren husket
    -- nøyaktig hvilken kontekstvariant den satte.
    PERFORM set_config('disponit.aktor', p_aktor, true);
    PERFORM set_config('disponit.request_id', p_request_id, true);
    SELECT u.id INTO v_id FROM public.unntak u
     WHERE u.tenant = p_tenant AND u.oppdrag_id = p_oppdrag_id
       AND u.arsak = p_arsak AND NOT u.terminal;
    IF FOUND THEN
        RETURN v_id;                              -- idempotent (port 25)
    END IF;
    SELECT * INTO o FROM public.oppdrag k
     WHERE k.tenant = p_tenant AND k.id = p_oppdrag_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'sikre_sak_for_oppdrag: ukjent oppdrag %',
            p_oppdrag_id USING ERRCODE = 'no_data_found';
    END IF;
    v_logg := coalesce(o.beslutning_loggpost_id, o.loggpost_id);
    -- Policysnapshotet (011) arver saken fra BESLUTNINGSLOGGPOSTEN — det
    -- er den policyen som autoriserte oppdraget. `maks_auto_forsok` er 0:
    -- en oppdragssak finnes for MENNESKER (evidensfrist/sikkerhet), aldri
    -- for auto-reparasjon.
    SELECT r.policy_id, r.policy_content_hash INTO v_policy, v_policy_hash
      FROM public.revisjonslogg r
     WHERE r.tenant = p_tenant AND r.id = v_logg;
    BEGIN
        INSERT INTO public.unntak (tenant, loggpost_id, handling, kategori,
            sakstype, prioritet, payload_kryptert, key_id, nonce,
            maks_auto_forsok_snapshot, policy_versjon, policy_content_hash,
            oppdrag_id, arsak)
        VALUES (p_tenant, v_logg, o.handling, 'teknisk_feil',
                CASE p_arsak WHEN 'sikkerhet' THEN 'sikkerhet'
                             ELSE 'normal' END,
                CASE p_arsak WHEN 'sikkerhet' THEN 'hoy' ELSE 'normal' END,
                o.payload_kryptert, o.key_id, o.nonce,
                0, coalesce(v_policy, 'ukjent'),
                coalesce(v_policy_hash, ''),
                p_oppdrag_id, p_arsak)
        RETURNING id INTO v_id;
    EXCEPTION WHEN unique_violation THEN
        -- Kappløpstaperen: vinnerens ikke-terminale sak er svaret, og den
        -- er FERDIG — vinneren har alt skrevet koblingshendelsen sin.
        --
        -- Codex P2: retur HERFRA, ikke gjennom innsettingsveien under.
        -- Sakskoblingen er én HENDELSE, ikke en tilstand: raden er
        -- idempotent fordi indeksen gjør den det, men historikken er
        -- append-only og teller. Falt taperen ut i den felles halen,
        -- fikk ETT oppdrag TO `sak_for_oppdrag`-rader for den samme
        -- koblingen — og det skjer i praksis, med samtidige sene
        -- kvitteringer eller sikkerhetskonflikter fra hver sin
        -- claim-generasjon. Å telle hendelser i sporet er nettopp det
        -- sporet er til for.
        SELECT u.id INTO v_id FROM public.unntak u
         WHERE u.tenant = p_tenant AND u.oppdrag_id = p_oppdrag_id
           AND u.arsak = p_arsak AND NOT u.terminal;
        RETURN v_id;
    END;
    -- Kun på INNSETTINGSVEIEN: koblingen skjedde nettopp, her.
    INSERT INTO public.unntak_historikk (tenant, unntak_id, hendelse,
                                         aktor, request_id, detalj)
    VALUES (p_tenant, v_id, 'sak_for_oppdrag', p_aktor, p_request_id,
            jsonb_build_object('oppdrag_id', p_oppdrag_id,
                               'arsak', p_arsak));
    RETURN v_id;
END $$;

-- Reaperen (038 §5, port 23): evidensfrist løpt ut på et BESLUTNINGS-
-- oppdrag → idempotent evidensfrist-sak + oppdraget lukkes. M-37-veien
-- røres IKKE (regresjonsport 8): dens oppdrag hører til en sak som alt
-- finnes, med sin egen fase-2-oppfølging. Hele autoriteten ligger her i
-- basen — timeren utenfor har bare EXECUTE på nøyaktig denne funksjonen,
-- samme snitt som `revalideringskandidater` (019).
CREATE OR REPLACE FUNCTION reap_evidensfrister(p_grense INT DEFAULT 200)
RETURNS TABLE (tenant TEXT, oppdrag_id BIGINT, unntak_id BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_sak BIGINT; v_rid TEXT; v_kontekst TEXT;
BEGIN
    -- Ingen advisory-lås: `FOR UPDATE SKIP LOCKED` gjør overlappende
    -- kjøringer trygge (de deler radene, ingen tas to ganger), og et tomt
    -- svar betyr dermed alltid «ingen kandidater» — aldri «noen andre
    -- holdt låsen». En lås her hadde gjort de to utfallene umulige å
    -- skille for kalleren.
    v_rid := 'reap-' || replace(gen_random_uuid()::text, '-', '');
    -- Reaperen ER kryss-tenant-autoriteten, og den eneste: den har ingen
    -- `p_tenant` en kaller kan velge, og hele utvalget ligger i predikatet
    -- under. Kallerens egen kontekst tas vare på og legges tilbake til
    -- slutt, så en kjøring aldri etterlater seg en fremmed tenant i
    -- transaksjonen den ble kalt fra.
    v_kontekst := current_setting('disponit.tenant', true);
    FOR r IN
        SELECT o.tenant AS t, o.id AS oid FROM public.oppdrag o
         WHERE o.opprinnelse = 'beslutning'
           AND o.status IN ('opprettet', 'plukket')
           AND now() > o.evidensfrist
         ORDER BY o.evidensfrist
         LIMIT p_grense
         FOR UPDATE OF o SKIP LOCKED
    LOOP
        -- Tenantporten i `sikre_sak_for_oppdrag` gjelder også her: den
        -- kryss-tenant-autoriteten som finnes, brukes ÉN RAD OM GANGEN,
        -- bundet til nøyaktig den radens tenant. Da er saksveien den
        -- samme for reaperen som for enhver annen kaller — porten er ikke
        -- noe reaperen slipper unna, bare noe den oppfyller per kandidat.
        PERFORM set_config('disponit.tenant', r.t, true);
        v_sak := public.sikre_sak_for_oppdrag(
            r.t, r.oid, 'evidensfrist', 'evidensreaper', v_rid);
        -- «ufullført» finnes ikke i statusmaskinen (005). `feilet` UTEN
        -- kvittering ER plattformens ufullført: lese-API-et viser
        -- nøyaktig den kombinasjonen som `feil_aarsak: timeout`.
        UPDATE public.oppdrag o SET status = 'feilet'
         WHERE o.tenant = r.t AND o.id = r.oid
           AND o.status IN ('opprettet', 'plukket');
        tenant := r.t; oppdrag_id := r.oid; unntak_id := v_sak;
        RETURN NEXT;
    END LOOP;
    PERFORM set_config('disponit.tenant', coalesce(v_kontekst, ''), true);
END $$;

REVOKE ALL ON FUNCTION opprett_reparasjonsoppdrag(TEXT, BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT, BYTEA, TEXT, BYTEA, TIMESTAMPTZ, TIMESTAMPTZ, BIGINT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION reap_evidensfrister(INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION opprett_beslutningsoppdrag(TEXT, BIGINT, TEXT, TEXT, TEXT, BYTEA, TEXT, BYTEA, TIMESTAMPTZ, TIMESTAMPTZ) FROM PUBLIC;
REVOKE ALL ON FUNCTION sikre_sak_for_oppdrag(TEXT, BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
-- Porten kalles bare INNENFRA definer-funksjonene, altså som eieren.
-- Ingen ekstern rolle trenger den.
REVOKE ALL ON FUNCTION krev_tenantkontekst(TEXT, TEXT) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_arbeider') THEN
        GRANT EXECUTE ON FUNCTION opprett_reparasjonsoppdrag(TEXT, BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT, BYTEA, TEXT, BYTEA, TIMESTAMPTZ, TIMESTAMPTZ, BIGINT, TEXT) TO disponit_arbeider;
        GRANT EXECUTE ON FUNCTION sikre_sak_for_oppdrag(TEXT, BIGINT, TEXT, TEXT, TEXT) TO disponit_arbeider;
    END IF;
END $$;
-- Lokalt/test kjører arbeiderveien som runtime — samme funksjon, samme port.
GRANT EXECUTE ON FUNCTION opprett_reparasjonsoppdrag(TEXT, BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT, BYTEA, TEXT, BYTEA, TIMESTAMPTZ, TIMESTAMPTZ, BIGINT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION opprett_beslutningsoppdrag(TEXT, BIGINT, TEXT, TEXT, TEXT, BYTEA, TEXT, BYTEA, TIMESTAMPTZ, TIMESTAMPTZ) TO disponit;
GRANT EXECUTE ON FUNCTION sikre_sak_for_oppdrag(TEXT, BIGINT, TEXT, TEXT, TEXT) TO disponit;
-- Reaperen kjøres av drift-timer-rollen (019-presedensen); lokalt/test
-- som runtime, samme funksjon, samme porter.
--
-- KRYSS-TENANT-AUTORITETEN ER INNELUKKET (Codex P1). De tre funksjonene
-- over binder `p_tenant` til kallerens kontekst og er derfor ufarlige å
-- dele; reaperen gjør ikke det — den ER definisjonen av en jobb som
-- spenner over alle tenanter. Da skal den heller ikke ligge i hendene på
-- web-API-rollen i et oppsett som HAR en egen timerrolle: en kompromittert
-- runtime skal ikke kunne skyve andre tenanters beslutningsoppdrag til
-- `feilet` på kommando. Grantet til `disponit` er derfor det som gjelder
-- NÅR timerrollen ikke finnes — altså lokalt og i test, der runtime ER
-- hele plattformen — og faller bort i det driftsoppsettet kommer på plass.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_domener') THEN
        GRANT EXECUTE ON FUNCTION reap_evidensfrister(INT) TO disponit_domener;
        REVOKE EXECUTE ON FUNCTION reap_evidensfrister(INT) FROM disponit;
    ELSE
        GRANT EXECUTE ON FUNCTION reap_evidensfrister(INT) TO disponit;
    END IF;
END $$;

RESET ROLE;

-- Eieren (m37_claimer) skriver oppdrag/unntak/historikk når funksjonene
-- kjører — den har det meste fra 005/007; oppdrag-INSERT og de nye
-- kolonnene dekkes av eksisterende `GRANT ... ON oppdrag` til claimer?
-- Nei — claimer har SELECT/UPDATE via claim-funksjonene sine. Grantene
-- settes eksplisitt her (kjøres som migrator, tabelleier):
GRANT SELECT, INSERT ON oppdrag TO disponit_m37_claimer;
GRANT SELECT, INSERT ON unntak TO disponit_m37_claimer;
GRANT SELECT, INSERT ON unntak_historikk TO disponit_m37_claimer;
GRANT SELECT, INSERT ON bestilling_idempotens TO disponit;

-- ------------------------------------------------------------
-- 5. Koblingsvakta (008) lærer beslutningsopphavet. Kroppen under er 008
--    sin GJELDENDE, diff-endret i ETT vilkår (merket «038:»): for et
--    beslutningsoppdrag ER loggposten selve beslutningen — den semantiske
--    porten er «en TILLAT-beslutning hos samme tenant», ikke M-37-trippelen
--    (repair_operation_id finnes ikke på denne opphavsveien).
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION oppdrag_koblingsvakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.koblingsstatus = 'LEGACY_UKJENT' THEN
            RAISE EXCEPTION
                'oppdrag: LEGACY_UKJENT kan kun settes av migrasjon 008 — '
                'runtime skal levere beslutning_loggpost_id (KOBLET) eller '
                'et verifikasjonsoppdrag (VERIFIKASJON)';
        END IF;
        IF NEW.koblingsstatus NOT IN ('KOBLET', 'VERIFIKASJON') THEN
            RAISE EXCEPTION 'oppdrag: ukjent koblingsstatus %',
                NEW.koblingsstatus;
        END IF;
        -- 038: beslutningsopphavet. Loggposten er beslutningen selv;
        -- kravet er at den ER en TILLAT-beslutning hos samme tenant —
        -- under kallerens RLS, fail-closed som resten.
        IF NEW.opprinnelse = 'beslutning' THEN
            IF NEW.koblingsstatus <> 'KOBLET' THEN
                RAISE EXCEPTION 'oppdrag: et beslutningsoppdrag er alltid '
                    'KOBLET — det ER koblingen';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.revisjonslogg r
                 WHERE r.tenant = NEW.tenant
                   AND r.id = NEW.beslutning_loggpost_id
                   AND r.beslutning = 'TILLAT') THEN
                RAISE EXCEPTION
                    'oppdrag: beslutning_loggpost_id % er ikke en '
                    'TILLAT-beslutning hos tenanten',
                    NEW.beslutning_loggpost_id;
            END IF;
            RETURN NEW;
        END IF;
        -- Codex P1 (review-runde 1): FK-en beviser at LOGGPOSTEN FINNES,
        -- ikke at den er RIKTIG beslutning. Uten dette kunne en KOBLET rad
        -- peke på en vilkårlig revisjonsrad hos samme tenant — og
        -- lese-API-et ville vist en fremmed beslutnings «utførelse» med
        -- full FK-integritet. Porten er SEMANTISK og ligger i databasen:
        -- loggposten må være nøyaktig fase-2-TILLAT-beslutningen for
        -- DETTE oppdragets reparasjonsidentitet — samme tre predikater som
        -- backfillen og arbeiderens oppslag, håndhevet der raden fødes.
        -- Kjøres under kallerens RLS: en innsetting uten tenantkontekst
        -- ser ingen loggpost og avvises — fail-closed, ikke en bypass.
        -- NULL-FK-en overlates til CHECK-en `oppdrag_kobling_konsistent`
        -- (BEFORE-triggere kjører FØR CHECK; uten IS NOT NULL her ville
        -- den navngitte constrainten aldri fått rapportere sitt eget brudd).
        IF NEW.koblingsstatus = 'KOBLET'
           AND NEW.beslutning_loggpost_id IS NOT NULL
           AND NOT EXISTS (
            SELECT 1 FROM public.revisjonslogg r
             WHERE r.tenant = NEW.tenant
               AND r.id = NEW.beslutning_loggpost_id
               AND r.idempotency_key = NEW.repair_operation_id
               AND r.kilde = 'arbeidskapabilitet'
               AND r.beslutning = 'TILLAT') THEN
            RAISE EXCEPTION
                'oppdrag: beslutning_loggpost_id % er ikke fase-2-TILLAT-'
                'beslutningen for repair_operation_id % (semantisk kobling '
                'kreves: idempotency_key + kilde + beslutning)',
                NEW.beslutning_loggpost_id, NEW.repair_operation_id;
        END IF;
        RETURN NEW;
    END IF;
    -- UPDATE: koblingen er uforanderlig etter innsetting (v5 pkt. 1).
    IF NEW.koblingsstatus IS DISTINCT FROM OLD.koblingsstatus
       OR NEW.beslutning_loggpost_id IS DISTINCT FROM OLD.beslutning_loggpost_id THEN
        RAISE EXCEPTION
            'oppdrag: koblingsstatus og beslutning_loggpost_id er '
            'uforanderlige etter innsetting';
    END IF;
    RETURN NEW;
END $$;
