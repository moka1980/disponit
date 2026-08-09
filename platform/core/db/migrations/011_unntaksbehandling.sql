-- ============================================================
-- 011 — Unntaksbehandling (PR-012): mennesket avgir en MAC-signert
-- attestasjon som mates som en NY beslutning gjennom motoren. Aldri en
-- statusflipp. Denne migrasjonen legger SKJEMAET: intensjonsfelt på
-- unntak, tre menneskeflyt-statuser, og tre tabeller
-- (menneskelig_attestasjon, godkjenningsrunde, godkjenningsutfall) med
-- append-only-/bindings-/kolonnelås-triggere.
--
-- MAC-nøkkelregisteret bor i APP-STATE (systemd credential), ALDRI i DB
-- (v3 §1) — ingen tabell her for det.
-- Ingen BEGIN/COMMIT: kjøreren (db/kjorer.py) eier transaksjonen.
-- ============================================================

-- ------------------------------------------------------------
-- 1. Intensjonsfelt på unntak (v2 §1, v3 §7, v4 §3, v6 §3)
--    Formell utvidelse av minimeringskontrakten: minimert payload mangler
--    `belop`, så motoren kan ikke re-evaluere en over_grense-sak. Lagres
--    kryptert (tenant-DEK), lukket feltliste, KUN når handlingen kan bli
--    menneskelig godkjennbar. Alle feltene er uforanderlige etter innsetting.
-- ------------------------------------------------------------
ALTER TABLE unntak
    ADD COLUMN IF NOT EXISTS handlingsintensjon_kryptert BYTEA,
    ADD COLUMN IF NOT EXISTS hi_key_id             TEXT,
    ADD COLUMN IF NOT EXISTS hi_nonce              BYTEA,
    ADD COLUMN IF NOT EXISTS hi_integritet_hash    TEXT,   -- SHA-256 over CIPHERTEXT
    ADD COLUMN IF NOT EXISTS hi_skjemaversjon      INT,
    -- intensjon_policy_hash: policyhashen FROSSET ved intensjonsopprettelse.
    -- AES-GCM-AAD ved kryptering OG all senere dekryptering (v6 §3) — dermed
    -- virker dekryptering uansett hvor mange ganger aktiv policy endres.
    ADD COLUMN IF NOT EXISTS intensjon_policy_hash TEXT,
    -- Snapshot satt av beslutningsveien (en CHECK kan ikke lese policyraden,
    -- v4 §3). Uforanderlig.
    ADD COLUMN IF NOT EXISTS intensjon_pakrevd     BOOLEAN NOT NULL DEFAULT false,
    -- Optimistisk lås (v1 §7): POST /handling må bære sakens nåværende
    -- versjon, ellers 409 `saksversjon_utdatert`. Monotont voksende; bumpes
    -- av triggeren på HVER statusendring, og av kalleren for endringer uten
    -- statusskifte (eskaler, v2 §7). Aldri redusert.
    ADD COLUMN IF NOT EXISTS saksversjon           INT NOT NULL DEFAULT 0;

-- Tenantkonsistent FK til nøkkelregisteret (v3 §7). NULL hi_key_id → ikke
-- håndhevet (saker uten intensjon).
ALTER TABLE unntak DROP CONSTRAINT IF EXISTS unntak_hi_key_fk;
ALTER TABLE unntak ADD CONSTRAINT unntak_hi_key_fk
    FOREIGN KEY (tenant, hi_key_id) REFERENCES tenant_nokler (tenant, key_id);

-- Konsistens: er intensjon påkrevd, MÅ hele envelopen finnes (v4 §3).
ALTER TABLE unntak DROP CONSTRAINT IF EXISTS unntak_intensjon_komplett;
ALTER TABLE unntak ADD CONSTRAINT unntak_intensjon_komplett CHECK (
    NOT intensjon_pakrevd OR (
        handlingsintensjon_kryptert IS NOT NULL AND hi_key_id IS NOT NULL
        AND hi_nonce IS NOT NULL AND hi_integritet_hash IS NOT NULL
        AND hi_skjemaversjon IS NOT NULL AND intensjon_policy_hash IS NOT NULL));

-- ------------------------------------------------------------
-- 2. Statusmaskin: tre menneskeflyt-statuser (v2 §4)
--    IKKE `under_behandling` (M-37-arbeiderens claim-tilstand) — gjenbruk
--    ville gjort saken claimbar av feil prosess. `claim_neste_sak` (007)
--    filtrerer på ny/verifikasjon_klar/verifikasjon_retry_klar, så de nye
--    statusene er utenfor arbeiderens sett per konstruksjon.
-- ------------------------------------------------------------
ALTER TABLE unntak DROP CONSTRAINT IF EXISTS unntak_status_check;
ALTER TABLE unntak ADD CONSTRAINT unntak_status_check CHECK (
    status IN ('ny','under_behandling','løst','avvist','manuell',
               'venter_utførelse','venter_verifikasjon','verifikasjon_klar',
               'verifikasjon_retry_klar',
               'venter_godkjenning','venter_andre_godkjenner','godkjenning_klar'));

-- Historikk-hendelser for menneskeflyten.
-- Hele 007-settet (uendret) + PR-012-hendelsene. Å utelate ett av 007s
-- verdier ville brutt m37-arbeideren — MÅLT av hele suiten, ikke gjettet.
ALTER TABLE unntak_historikk DROP CONSTRAINT IF EXISTS unntak_historikk_hendelse_check;
ALTER TABLE unntak_historikk ADD CONSTRAINT unntak_historikk_hendelse_check CHECK (
    hendelse IN (
        'opprettet','statusendring','claim','claim_utlopt','dek_destruert',
        'claim_fornyet','klassifisert','repair_generation_ny',
        'generation_blokkert_aktiv_utforelse','kapabilitet_utstedt',
        'kapabilitet_brukt','oppdrag_opprettet','oppdrag_kansellert',
        'kvittering','sen_kvittering','motstridende_kvittering',
        'policy_endret_siden_opprettelse','legacy_uten_snapshot',
        'dek_utilgjengelig','verifikator_utilgjengelig','frist_utlopt',
        'verifikasjon_bestilt','verifikasjon_positiv','verifikasjon_negativ',
        'verifikasjon_utlopt','verifikasjon_konflikt','verifikasjon_retry',
        'sikkerhetsfrysing',
        -- PR-012 (unntaksbehandling):
        'godkjenning_startet','attestasjon_registrert','runde_apnet',
        'runde_utlopt','runde_kansellert','godkjent','avvist_handling',
        'eskalert','godkjenning_stoppet_av_policy',
        'policy_endret_under_godkjenning','sen_kvittering_etter_avvis',
        'avklaring_kreves'));

-- ------------------------------------------------------------
-- 3. `unntak_kolonnelaas` utvidet (CREATE OR REPLACE fra 007-body)
--    Beholder ALLE 007-overganger. Legger til: de seks intensjon-kolonnene
--    + intensjon_pakrevd i kolonnelåsen; den KONTROLLERTE gjenåpningen
--    `manuell → venter_godkjenning` (v1 §4) som 007 eksplisitt utsatte; og
--    menneskeflyt-overgangene. `løst`/`avvist` forblir absolutt terminale;
--    `manuell` er ikke lenger terminal, men eneste utvei er den whitelistede.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION unntak_kolonnelaas()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.loggpost_id IS DISTINCT FROM OLD.loggpost_id
       OR NEW.handling IS DISTINCT FROM OLD.handling
       OR NEW.kategori IS DISTINCT FROM OLD.kategori
       OR NEW.sakstype IS DISTINCT FROM OLD.sakstype
       OR NEW.prioritet IS DISTINCT FROM OLD.prioritet
       OR NEW.payload_kryptert IS DISTINCT FROM OLD.payload_kryptert
       OR NEW.key_id IS DISTINCT FROM OLD.key_id
       OR NEW.alg IS DISTINCT FROM OLD.alg
       OR NEW.nonce IS DISTINCT FROM OLD.nonce
       OR NEW.ts IS DISTINCT FROM OLD.ts
       -- PR-012: intensjons-envelopen er uforanderlig etter opprettelse.
       OR NEW.handlingsintensjon_kryptert IS DISTINCT FROM OLD.handlingsintensjon_kryptert
       OR NEW.hi_key_id IS DISTINCT FROM OLD.hi_key_id
       OR NEW.hi_nonce IS DISTINCT FROM OLD.hi_nonce
       OR NEW.hi_integritet_hash IS DISTINCT FROM OLD.hi_integritet_hash
       OR NEW.hi_skjemaversjon IS DISTINCT FROM OLD.hi_skjemaversjon
       OR NEW.intensjon_policy_hash IS DISTINCT FROM OLD.intensjon_policy_hash
       OR NEW.intensjon_pakrevd IS DISTINCT FROM OLD.intensjon_pakrevd THEN
        RAISE EXCEPTION 'unntak: kun status/status_ts/forsok/claim-felter kan endres';
    END IF;

    IF OLD.maks_auto_forsok_snapshot IS NOT NULL
       AND NEW.maks_auto_forsok_snapshot IS DISTINCT FROM OLD.maks_auto_forsok_snapshot THEN
        RAISE EXCEPTION 'unntak: maks_auto_forsok_snapshot er uforanderlig etter at den er satt';
    END IF;
    IF OLD.policy_versjon IS NOT NULL
       AND NEW.policy_versjon IS DISTINCT FROM OLD.policy_versjon THEN
        RAISE EXCEPTION 'unntak: policy_versjon er uforanderlig etter at den er satt';
    END IF;
    IF OLD.policy_content_hash IS NOT NULL
       AND NEW.policy_content_hash IS DISTINCT FROM OLD.policy_content_hash THEN
        RAISE EXCEPTION 'unntak: policy_content_hash er uforanderlig etter at den er satt';
    END IF;

    IF NEW.claim_generation < OLD.claim_generation THEN
        RAISE EXCEPTION 'unntak: claim_generation kan aldri reduseres (% -> %)',
            OLD.claim_generation, NEW.claim_generation;
    END IF;
    IF NEW.verification_generation < OLD.verification_generation THEN
        RAISE EXCEPTION 'unntak: verification_generation kan aldri reduseres (% -> %)',
            OLD.verification_generation, NEW.verification_generation;
    END IF;

    IF OLD.krav_sett IS NOT NULL
       AND NEW.krav_sett IS DISTINCT FROM OLD.krav_sett THEN
        RAISE EXCEPTION 'unntak: krav_sett er frosset og kan ikke endres';
    END IF;

    IF NOT (
        (OLD.status = 'ny'               AND NEW.status IN ('under_behandling','manuell')) OR
        (OLD.status = 'under_behandling' AND NEW.status IN
             ('løst','avvist','manuell','venter_utførelse',
              'venter_verifikasjon','verifikasjon_retry_klar')) OR
        (OLD.status = 'under_behandling' AND NEW.status = 'ny'
             AND OLD.claim_utloper IS NOT NULL AND OLD.claim_utloper < now()) OR
        (OLD.status = 'venter_utførelse' AND NEW.status IN ('løst','manuell')) OR
        (OLD.status = 'venter_verifikasjon' AND NEW.status IN
             ('verifikasjon_klar','verifikasjon_retry_klar','manuell')) OR
        (OLD.status IN ('verifikasjon_klar','verifikasjon_retry_klar')
             AND NEW.status IN ('under_behandling','manuell')) OR
        -- PR-012: den kontrollerte gjenåpningen (v1 §4). Selve overgangen er
        -- lovlig her; rundekravet håndheves i egen IF UNDER (ellers ville
        -- EXISTS-subspørringen blitt evaluert av OR-en også på m37-claimens
        -- ny→under_behandling — der triggeren kjører inne i en SECURITY
        -- DEFINER-funksjon med begrenset search_path og treffer «relation
        -- does not exist». Funnet ved å kjøre HELE suiten.)
        (OLD.status = 'manuell' AND NEW.status = 'venter_godkjenning') OR
        (OLD.status = 'venter_godkjenning' AND NEW.status IN
             ('venter_andre_godkjenner','godkjenning_klar','manuell','avvist')) OR
        (OLD.status = 'venter_andre_godkjenner' AND NEW.status IN
             ('godkjenning_klar','manuell','avvist')) OR
        (OLD.status = 'godkjenning_klar' AND NEW.status IN
             ('venter_utførelse','løst','manuell','avvist')) OR
        (OLD.status = NEW.status)  -- forsok/claim-oppdatering uten statusskifte
    ) THEN
        RAISE EXCEPTION 'unntak: ulovlig statusovergang % -> %', OLD.status, NEW.status;
    END IF;

    -- Gjenåpningen krever at en `apen` godkjenningsrunde ALLEREDE finnes for
    -- saken — ingen naken statusflipp. Evalueres KUN for denne overgangen
    -- (nestet IF), og tabellen er schema-kvalifisert så den løser uansett
    -- callerens search_path.
    IF OLD.status = 'manuell' AND NEW.status = 'venter_godkjenning' THEN
        IF NOT EXISTS (SELECT 1 FROM public.godkjenningsrunde r
                       WHERE r.tenant = NEW.tenant AND r.unntak_id = NEW.id
                         AND r.status = 'apen') THEN
            RAISE EXCEPTION
                'unntak: gjenåpning til venter_godkjenning krever en apen godkjenningsrunde';
        END IF;
    END IF;

    -- `løst`/`avvist` forblir ABSOLUTT terminale. `manuell` er ikke lenger i
    -- settet: PR-012 åpner nettopp den ene auditerte veien ut (whitelistet +
    -- rundekravet over), som 007-kommentaren utsatte til «en egen auditert
    -- prosedyre».
    IF OLD.status IN ('løst','avvist') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'unntak: % er terminal og kan ikke forlates', OLD.status;
    END IF;

    -- Optimistisk lås: aldri redusert. Kalleren kan bumpe den for endringer
    -- uten statusskifte (eskaler); statusendringer bumper automatisk under.
    IF NEW.saksversjon < OLD.saksversjon THEN
        RAISE EXCEPTION 'unntak: saksversjon kan aldri reduseres (% -> %)',
            OLD.saksversjon, NEW.saksversjon;
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status THEN
        NEW.status_ts := now();
        NEW.saksversjon := OLD.saksversjon + 1;   -- hver statusendring = ny versjon
    END IF;
    RETURN NEW;
END $$;

-- ------------------------------------------------------------
-- 4. menneskelig_attestasjon — MAC-signert konvolutt, append-only (v3 §1)
--    `operatorhandling` (godkjenn|avvis|eskaler) ≠ `target_action`
--    (f.eks. faktura.bokfor). MAC-nøkkelen ligger i app-state; `mac_key_id`
--    er kun en referanse (ingen FK). Begrunnelse kryptert med tenant-DEK.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS menneskelig_attestasjon (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant         TEXT NOT NULL,
    unntak_id      BIGINT NOT NULL,
    runde          INT  NOT NULL,
    operatorhandling TEXT NOT NULL
        CHECK (operatorhandling IN ('godkjenn','avvis','eskaler')),
    target_action  TEXT,                         -- NULL for avvis/eskaler
    bundet_grunnkode TEXT,                        -- konvoluttfelt (v8 §2); NULL for avvis/eskaler
    bruker_id      TEXT NOT NULL,
    rolle          TEXT NOT NULL,
    authz_version  INT  NOT NULL,
    konvoluttversjon INT NOT NULL,               -- 2 = disponit_human_approval_v2 (m/ bundet_grunnkode)
    konvolutt_hash TEXT NOT NULL,                -- SHA-256 over kanonisk JCS-konvolutt
    mac            TEXT NOT NULL,
    mac_key_id     TEXT NOT NULL,                -- referanse til app-state-register
    jti            TEXT NOT NULL CHECK (length(jti) >= 22),
    utloper        TIMESTAMPTZ NOT NULL,
    begrunnelse_kryptert BYTEA, key_id TEXT, nonce BYTEA,   -- tenant-DEK
    saksversjon    INT  NOT NULL,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant, jti),                                   -- engangsbruk (v3 §2)
    UNIQUE (tenant, unntak_id, runde, bruker_id),           -- fire øyne (v3 §1)
    FOREIGN KEY (tenant, unntak_id) REFERENCES unntak (tenant, id),
    FOREIGN KEY (tenant, key_id) REFERENCES tenant_nokler (tenant, key_id)
);
CREATE OR REPLACE FUNCTION menneskelig_attestasjon_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'menneskelig_attestasjon er append-only: % er forbudt', TG_OP;
END $$;
DROP TRIGGER IF EXISTS attestasjon_ingen_endring ON menneskelig_attestasjon;
CREATE TRIGGER attestasjon_ingen_endring BEFORE UPDATE OR DELETE ON menneskelig_attestasjon
    FOR EACH ROW EXECUTE FUNCTION menneskelig_attestasjon_append_only();
DROP TRIGGER IF EXISTS attestasjon_ingen_truncate ON menneskelig_attestasjon;
CREATE TRIGGER attestasjon_ingen_truncate BEFORE TRUNCATE ON menneskelig_attestasjon
    FOR EACH STATEMENT EXECUTE FUNCTION menneskelig_attestasjon_append_only();

-- ------------------------------------------------------------
-- 5. godkjenningsrunde — fire øyne uten permanent frysing (v2 §5, v3 §3, v6 §2)
--    `godkjennings_policy_hash` = aktiv policy frosset PER RUNDE (avgjør om ny
--    runde er lovlig). `decision_operation_id` settes ved klar→brukt og er
--    kolonnelåst — bindingskjeden godkjenningsutfall→runde→sak hviler på den.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS godkjenningsrunde (
    tenant       TEXT NOT NULL,
    unntak_id    BIGINT NOT NULL,
    runde        INT  NOT NULL,
    status       TEXT NOT NULL DEFAULT 'apen'
        CHECK (status IN ('apen','klar','brukt','utlopt','kansellert')),
    godkjennings_policy_hash TEXT NOT NULL,
    policy_versjon TEXT NOT NULL,
    -- Den ENE grunnkoden runden (og dermed godkjenningen) binder seg til.
    -- Server-utledet fra sakens begrunnelseskjede ved åpning (v8 §2), aldri
    -- klientvalgt; uforanderlig etterpå. Motoren kan løfte KUN denne.
    bundet_grunnkode TEXT NOT NULL CHECK (bundet_grunnkode ~ '^[a-z0-9_]+$'),
    decision_operation_id TEXT,                  -- settes ved klar→brukt
    apnet        TIMESTAMPTZ NOT NULL DEFAULT now(),
    utloper      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant, unntak_id, runde),
    FOREIGN KEY (tenant, unntak_id) REFERENCES unntak (tenant, id)
);
-- Maks ÉN aktiv runde per sak (v3 §3 — dekker BÅDE apen og klar).
CREATE UNIQUE INDEX IF NOT EXISTS en_aktiv_runde ON godkjenningsrunde
    (tenant, unntak_id) WHERE status IN ('apen','klar');
-- decision_operation_id globalt unik per tenant (idempotens, v5 §3).
CREATE UNIQUE INDEX IF NOT EXISTS runde_operasjon_unik ON godkjenningsrunde
    (tenant, decision_operation_id) WHERE decision_operation_id IS NOT NULL;

CREATE OR REPLACE FUNCTION godkjenningsrunde_kolonnelaas()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.unntak_id IS DISTINCT FROM OLD.unntak_id
       OR NEW.runde IS DISTINCT FROM OLD.runde
       OR NEW.apnet IS DISTINCT FROM OLD.apnet
       OR NEW.godkjennings_policy_hash IS DISTINCT FROM OLD.godkjennings_policy_hash
       OR NEW.policy_versjon IS DISTINCT FROM OLD.policy_versjon
       OR NEW.bundet_grunnkode IS DISTINCT FROM OLD.bundet_grunnkode THEN
        RAISE EXCEPTION 'godkjenningsrunde: identitets-/policyfelt er uforanderlige';
    END IF;
    -- decision_operation_id settes ÉN gang og kan aldri endres etterpå.
    IF OLD.decision_operation_id IS NOT NULL
       AND NEW.decision_operation_id IS DISTINCT FROM OLD.decision_operation_id THEN
        RAISE EXCEPTION 'godkjenningsrunde: decision_operation_id er uforanderlig';
    END IF;
    IF NOT (
        (OLD.status = 'apen' AND NEW.status IN ('klar','utlopt','kansellert')) OR
        (OLD.status = 'klar' AND NEW.status IN ('brukt','utlopt','kansellert')) OR
        (OLD.status = NEW.status)
    ) THEN
        RAISE EXCEPTION 'godkjenningsrunde: ulovlig overgang % -> %', OLD.status, NEW.status;
    END IF;
    IF OLD.status IN ('brukt','utlopt','kansellert') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'godkjenningsrunde: % er terminal', OLD.status;
    END IF;
    IF NEW.status = 'brukt' AND NEW.decision_operation_id IS NULL THEN
        RAISE EXCEPTION 'godkjenningsrunde: brukt krever decision_operation_id';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS runde_kolonnelaas ON godkjenningsrunde;
CREATE TRIGGER runde_kolonnelaas BEFORE UPDATE ON godkjenningsrunde
    FOR EACH ROW EXECUTE FUNCTION godkjenningsrunde_kolonnelaas();
-- Ingen sletting (auditerbarhet).
CREATE OR REPLACE FUNCTION godkjenningsrunde_ingen_sletting()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'godkjenningsrunde: % er forbudt', TG_OP;
END $$;
DROP TRIGGER IF EXISTS runde_ingen_sletting ON godkjenningsrunde;
CREATE TRIGGER runde_ingen_sletting BEFORE DELETE ON godkjenningsrunde
    FOR EACH ROW EXECUTE FUNCTION godkjenningsrunde_ingen_sletting();
DROP TRIGGER IF EXISTS runde_ingen_truncate ON godkjenningsrunde;
CREATE TRIGGER runde_ingen_truncate BEFORE TRUNCATE ON godkjenningsrunde
    FOR EACH STATEMENT EXECUTE FUNCTION godkjenningsrunde_ingen_sletting();

-- ------------------------------------------------------------
-- 6. godkjenningsutfall — terminalt utfall + bindingstrigger (v5 §3, v6 §2)
--    Samme (sak, intensjonshash, godkjennings_policy_hash) kan ALDRI
--    godkjennes to ganger. Bindingstriggeren beviser TILHØRIGHET: FK-ene
--    beviser bare eksistens.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS godkjenningsutfall (
    tenant       TEXT NOT NULL,
    unntak_id    BIGINT NOT NULL,
    hi_integritet_hash TEXT NOT NULL,
    policy_hash  TEXT NOT NULL,                  -- = godkjennings_policy_hash
    decision_operation_id TEXT NOT NULL,
    motorutfall  TEXT NOT NULL
        CHECK (motorutfall IN ('TILLAT_SIDEEFFEKTFRI','TILLAT_OUTBOX',
                               'STOPP','TIL_UNNTAK')),
    beslutning_loggpost_id BIGINT NOT NULL,
    ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, unntak_id, hi_integritet_hash, policy_hash),
    UNIQUE (tenant, decision_operation_id),
    FOREIGN KEY (tenant, unntak_id) REFERENCES unntak (tenant, id),
    FOREIGN KEY (tenant, beslutning_loggpost_id)
        REFERENCES revisjonslogg (tenant, id)
);
-- Bindingstrigger (v6 §2): DB-håndhevet tilhørighetskjede
-- godkjenningsutfall → decision_operation_id → BRUKT runde (samme sak) →
-- loggpost som bærer samme operasjons-id + tenant. En loggpost fra riktig
-- operasjon men FEIL sak avvises her — det FK-ene ikke fanger.
CREATE OR REPLACE FUNCTION godkjenningsutfall_binding()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM godkjenningsrunde r
         WHERE r.tenant = NEW.tenant AND r.unntak_id = NEW.unntak_id
           AND r.status = 'brukt'
           AND r.decision_operation_id = NEW.decision_operation_id) THEN
        RAISE EXCEPTION
            'godkjenningsutfall: ingen brukt runde med operasjon % for sak %',
            NEW.decision_operation_id, NEW.unntak_id;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM revisjonslogg l
         WHERE l.tenant = NEW.tenant AND l.id = NEW.beslutning_loggpost_id
           AND l.idempotency_key = NEW.decision_operation_id) THEN
        RAISE EXCEPTION
            'godkjenningsutfall: loggpost % tilhører ikke operasjon %',
            NEW.beslutning_loggpost_id, NEW.decision_operation_id;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS utfall_binding ON godkjenningsutfall;
CREATE TRIGGER utfall_binding BEFORE INSERT ON godkjenningsutfall
    FOR EACH ROW EXECUTE FUNCTION godkjenningsutfall_binding();
-- Append-only.
CREATE OR REPLACE FUNCTION godkjenningsutfall_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'godkjenningsutfall er append-only: % er forbudt', TG_OP;
END $$;
DROP TRIGGER IF EXISTS utfall_ingen_endring ON godkjenningsutfall;
CREATE TRIGGER utfall_ingen_endring BEFORE UPDATE OR DELETE ON godkjenningsutfall
    FOR EACH ROW EXECUTE FUNCTION godkjenningsutfall_append_only();
DROP TRIGGER IF EXISTS utfall_ingen_truncate ON godkjenningsutfall;
CREATE TRIGGER utfall_ingen_truncate BEFORE TRUNCATE ON godkjenningsutfall
    FOR EACH STATEMENT EXECUTE FUNCTION godkjenningsutfall_append_only();

-- ------------------------------------------------------------
-- 7. RLS + FORCE + tenant-isolasjon på de tre nye tabellene (mønster fra 003)
-- ------------------------------------------------------------
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['menneskelig_attestasjon','godkjenningsrunde',
                             'godkjenningsutfall']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolasjon ON %I', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolasjon ON %I
                USING      (tenant = current_setting(''disponit.tenant'', true))
                WITH CHECK (tenant = current_setting(''disponit.tenant'', true))', t);
    END LOOP;
END $$;
