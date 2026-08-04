-- ============================================================
-- Disponit migrasjon 005 — M-37 behandlingsmotor.
-- Spesifisert i PR-006 v1 + v2/v3/v4-delta + de tre GO-vilkårene.
--
-- HVORFOR 005 OG IKKE 004, som spesifikasjonen ber om:
-- 004 er OPPTATT. `004_token_bruk_og_konstanttid.sql` ble merget med
-- PR-005b og er registrert med checksum i `migrasjoner`. Kjøreren
-- (db/kjorer.py) avviser enhver endring av en historisk fil, og to filer
-- med samme versjonsnummer ville dessuten gitt kjøreren to kandidater for
-- samme rad. Spesifikasjonen er skrevet mot et øyeblikksbilde der 004 ennå
-- ikke fantes. Historikken er immutable — rettelser og påbygg kommer alltid
-- som NESTE versjon. Filnavnet er derfor det eneste som avviker fra
-- klarsignalet; innholdet er det klarsignalet beskriver.
--
-- INGEN BEGIN/COMMIT: fra versjon 3 eier migrasjonskjøreren transaksjonen.
-- Kjøres av MIGRATOR-rollen.
--
-- MERK om NOT NULL: de tre snapshot-kolonnene legges NULLABLE her.
-- Backfillen må RE-HASHE lagret policyinnhold og sammenligne mot
-- revisjonsloggen (GO-vilkår V2), og den kanoniske hashen er definert i
-- Python (`api.policyregister.innholds_hash` — sorterte nøkler, ensure_ascii
-- av, ingen mellomrom). Å gjenskape den i PL/pgSQL ville vært en ANDRE
-- implementasjon av en sikkerhetskritisk regel, altså nøyaktig
-- duplikatformen som ga P1 nr. 4 i PR-002. Backfillen kjøres derfor av
-- `db.m37_backfill` fra `deploy/staging/migrer.py`, og migrasjon 006 setter
-- NOT NULL. Uteblir backfillen, feiler 006 — porten er rekkefølgen, ikke
-- en instruks.
-- ============================================================

-- ------------------------------------------------------------
-- 0. Roller: IKKE opprettet her (samme skille som 003 og 004).
--
-- Roller er klyngeobjekter og opprettes av
-- `deploy/staging/oppsett-postgresql.sh` med superbrukeren. Migratorrollen
-- har ikke CREATEROLE og skal ikke ha det: en migrasjon som kan lage sine
-- egne roller kan også lage seg selv rettigheter.
--
-- `disponit_m37_claimer` eier claim-, kapabilitets- og oppdragsfunksjonene
-- samt `arbeidskapabiliteter`. Den er NOLOGIN: ingen kan koble til som den,
-- og SECURITY DEFINER er derfor den ENESTE veien til de rettighetene.
-- ------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_m37_claimer') THEN
        RAISE EXCEPTION
            'rollen disponit_m37_claimer mangler — kjør deploy/staging/oppsett-postgresql.sh først (roller opprettes der, ikke i migrasjoner)';
    END IF;
    IF NOT pg_has_role(current_user, 'disponit_m37_claimer', 'MEMBER') THEN
        RAISE EXCEPTION
            'migratorrollen % er ikke medlem av disponit_m37_claimer — kreves for OWNER TO. Settes i oppsett-skriptet.', current_user;
    END IF;
END $$;

-- ------------------------------------------------------------
-- 1. Unntak: nye terminaltilstander og policysnapshot
--
-- `manuell` er en REELL terminaltilstand (v2 pkt. 5), ikke «ny som ingen
-- plukker». Forskjellen er målbar: en sak som står `ny` med oppbrukte
-- forsøk er umulig å skille fra en sak ingen har rukket å ta, og
-- feilinjiseringsporten krever at terminal_andel = 1.0 kan telles.
--
-- `venter_utførelse` er saken som venter på en eiermodul sin signerte
-- kvittering. Den er IKKE terminal — evidensporten teller den ikke med.
-- ------------------------------------------------------------
ALTER TABLE unntak DROP CONSTRAINT IF EXISTS unntak_status_check;
ALTER TABLE unntak ADD CONSTRAINT unntak_status_check CHECK (
    status IN ('ny','under_behandling','løst','avvist','manuell','venter_utførelse'));

ALTER TABLE unntak
    -- Ekte fencing-token (v3 pkt. 6): id-likhet alene er ikke fencing.
    -- To arbeidere kan i prinsippet trekke samme claim_id-streng; en
    -- monotont voksende generasjon per sak kan de ikke.
    ADD COLUMN IF NOT EXISTS claim_generation          INT NOT NULL DEFAULT 0,
    -- Policykonteksten saken ble FØDT under (v2 pkt. 6). Uten snapshot
    -- endrer en policyredigering retrysemantikken til saker som allerede
    -- ligger i køen — altså i ettertid.
    ADD COLUMN IF NOT EXISTS maks_auto_forsok_snapshot INT,
    ADD COLUMN IF NOT EXISTS policy_versjon            TEXT,
    ADD COLUMN IF NOT EXISTS policy_content_hash       TEXT;

ALTER TABLE unntak DROP CONSTRAINT IF EXISTS unntak_snapshot_omraade;
ALTER TABLE unntak ADD CONSTRAINT unntak_snapshot_omraade CHECK (
    maks_auto_forsok_snapshot IS NULL
    OR (maks_auto_forsok_snapshot >= 0 AND maks_auto_forsok_snapshot <= 10));

-- Delvis indeks for claim-veien. `status='ny'` er det eneste claim-bare
-- utvalget, og en delvis indeks holder den liten selv når køen har mange
-- terminale saker.
CREATE INDEX IF NOT EXISTS unntak_claimbar
    ON unntak (sakstype, prioritet, ts, id) WHERE status = 'ny';
CREATE INDEX IF NOT EXISTS unntak_claim_oppslag
    ON unntak (claim_id) WHERE claim_id IS NOT NULL;

-- ------------------------------------------------------------
-- 2. Statusmaskin og kolonnelås — erstatter 003-versjonen
--
-- Overgangene (v2 pkt. 5):
--   ny               -> under_behandling | manuell
--   under_behandling -> løst | avvist | manuell | venter_utførelse
--   under_behandling -> ny   (KUN ved utløpt lease)
--   venter_utførelse -> løst | manuell
--
-- `manuell` direkte fra `ny` er R3-veien: klassene som ikke har noen
-- automatisk reparasjon skal ikke brenne tre claims på å oppdage det.
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
       OR NEW.ts IS DISTINCT FROM OLD.ts THEN
        RAISE EXCEPTION 'unntak: kun status/status_ts/forsok/claim-felter kan endres';
    END IF;

    -- Snapshotfeltene er saksidentitet, ikke tilstand. Kunne de endres,
    -- ville «policyen som gjaldt da saken oppsto» vært noe man kan skrive
    -- om i ettertid — og da beviser den ingenting. Unntaket er
    -- backfill-veien, som fyller dem fra NULL nøyaktig én gang.
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

    -- Generasjonen er monoton. Kunne den settes ned, ville et gammelt
    -- fencing-token blitt gyldig igjen — som er hele det angrepet
    -- fencing finnes for.
    IF NEW.claim_generation < OLD.claim_generation THEN
        RAISE EXCEPTION 'unntak: claim_generation kan aldri reduseres (% -> %)',
            OLD.claim_generation, NEW.claim_generation;
    END IF;

    IF NOT (
        (OLD.status = 'ny'               AND NEW.status IN ('under_behandling','manuell')) OR
        (OLD.status = 'under_behandling' AND NEW.status IN ('løst','avvist','manuell','venter_utførelse')) OR
        (OLD.status = 'under_behandling' AND NEW.status = 'ny'
             AND OLD.claim_utloper IS NOT NULL AND OLD.claim_utloper < now()) OR
        (OLD.status = 'venter_utførelse' AND NEW.status IN ('løst','manuell')) OR
        (OLD.status = NEW.status)  -- forsok/claim-oppdatering uten statusskifte
    ) THEN
        RAISE EXCEPTION 'unntak: ulovlig statusovergang % -> %', OLD.status, NEW.status;
    END IF;

    -- Terminaltilstandene er terminale. Uten dette kunne `løst` gå tilbake
    -- til `løst` og videre — CHECK-en over stopper bare selve paret, ikke
    -- at en avsluttet sak gjenåpnes gjennom en tilsynelatende lovlig kjede.
    IF OLD.status IN ('løst','avvist','manuell') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'unntak: % er terminal og kan ikke forlates', OLD.status;
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status THEN
        NEW.status_ts := now();
    END IF;
    RETURN NEW;
END $$;

-- ------------------------------------------------------------
-- 3. Historikk: flere hendelsestyper og generasjon
-- ------------------------------------------------------------
ALTER TABLE unntak_historikk
    ADD COLUMN IF NOT EXISTS claim_generation INT,
    ADD COLUMN IF NOT EXISTS detalj           JSONB;

ALTER TABLE unntak_historikk DROP CONSTRAINT IF EXISTS unntak_historikk_hendelse_check;
ALTER TABLE unntak_historikk ADD CONSTRAINT unntak_historikk_hendelse_check CHECK (
    hendelse IN (
        -- fra 003
        'opprettet','statusendring','claim','claim_utlopt','dek_destruert',
        -- PR-006
        'claim_fornyet','klassifisert','repair_generation_ny',
        'generation_blokkert_aktiv_utforelse','kapabilitet_utstedt',
        'kapabilitet_brukt','oppdrag_opprettet','oppdrag_kansellert',
        'kvittering','sen_kvittering','motstridende_kvittering',
        'policy_endret_siden_opprettelse','legacy_uten_snapshot',
        'dek_utilgjengelig','verifikator_utilgjengelig','frist_utlopt'));

CREATE OR REPLACE FUNCTION unntak_skriv_historikk()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $$
DECLARE
    v_aktor TEXT := pg_catalog.current_setting('disponit.aktor', true);
BEGIN
    IF v_aktor IS NULL OR length(btrim(v_aktor)) = 0 THEN
        RAISE EXCEPTION 'disponit.aktor er ikke satt — historikk uten aktør er forbudt';
    END IF;
    IF TG_OP = 'INSERT' THEN
        INSERT INTO public.unntak_historikk (tenant, unntak_id, hendelse,
                                      fra_status, til_status, aktor,
                                      request_id, claim_id, claim_generation)
        VALUES (NEW.tenant, NEW.id, 'opprettet', NULL, NEW.status, v_aktor,
                pg_catalog.current_setting('disponit.request_id', true),
                NEW.claim_id, NEW.claim_generation);
    ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO public.unntak_historikk (tenant, unntak_id, hendelse,
                                      fra_status, til_status, aktor,
                                      request_id, claim_id, claim_generation)
        VALUES (NEW.tenant, NEW.id,
                CASE WHEN NEW.status = 'under_behandling' THEN 'claim'
                     WHEN OLD.status = 'under_behandling' AND NEW.status = 'ny'
                          THEN 'claim_utlopt'
                     ELSE 'statusendring' END,
                OLD.status, NEW.status, v_aktor,
                pg_catalog.current_setting('disponit.request_id', true),
                NEW.claim_id, NEW.claim_generation);
    END IF;
    RETURN NEW;
END $$;

-- ------------------------------------------------------------
-- 4. Reparasjonsoperasjoner — stabil identitet, generasjonshistorikk
--
-- `repair_operation_id` = SHA-256(tenant ‖ unntak_id ‖ handler_id@versjon ‖
-- maalhandling ‖ kanonisk_input_hash) (v2 pkt. 4). `forsok` og `claim_id`
-- inngår ALDRI: de er transportdetaljer, og en idempotensnøkkel som endrer
-- seg per forsøk er ingen idempotensnøkkel — hvert retry ville skapt en ny
-- forretningshandling.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reparasjonsoperasjoner (
    tenant              TEXT NOT NULL,
    unntak_id           BIGINT NOT NULL,
    repair_operation_id TEXT NOT NULL CHECK (repair_operation_id ~ '^[0-9a-f]{64}$'),
    repair_generation   INT  NOT NULL CHECK (repair_generation >= 0),
    handler_id          TEXT NOT NULL,
    handler_versjon     TEXT NOT NULL,
    maalhandling        TEXT NOT NULL,
    input_hash          TEXT NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    kategori            TEXT NOT NULL,
    grunnkode           TEXT,
    status              TEXT NOT NULL DEFAULT 'aktiv'
                        CHECK (status IN ('aktiv','superseded')),
    opprettet           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, repair_operation_id),
    CONSTRAINT reparasjon_generasjon_unik UNIQUE (tenant, unntak_id, repair_generation),
    FOREIGN KEY (tenant, unntak_id) REFERENCES unntak (tenant, id)
);
-- Én AKTIV reparasjon per sak. Andre forsvarslinje mot at to generasjoner
-- kjører samtidig (v3 pkt. 4) — fencing er den første, men fencing er kode,
-- og dette er databasen.
CREATE UNIQUE INDEX IF NOT EXISTS en_aktiv_reparasjon_per_sak
    ON reparasjonsoperasjoner (tenant, unntak_id) WHERE status = 'aktiv';

CREATE OR REPLACE FUNCTION reparasjon_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'reparasjonsoperasjoner: % er forbudt', TG_OP;
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.unntak_id IS DISTINCT FROM OLD.unntak_id
       OR NEW.repair_operation_id IS DISTINCT FROM OLD.repair_operation_id
       OR NEW.repair_generation IS DISTINCT FROM OLD.repair_generation
       OR NEW.handler_id IS DISTINCT FROM OLD.handler_id
       OR NEW.handler_versjon IS DISTINCT FROM OLD.handler_versjon
       OR NEW.maalhandling IS DISTINCT FROM OLD.maalhandling
       OR NEW.input_hash IS DISTINCT FROM OLD.input_hash
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'reparasjonsoperasjoner: kun status kan endres';
    END IF;
    IF OLD.status = 'superseded' AND NEW.status <> 'superseded' THEN
        RAISE EXCEPTION 'reparasjonsoperasjoner: superseded er terminal';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS reparasjon_vakt ON reparasjonsoperasjoner;
CREATE TRIGGER reparasjon_vakt BEFORE UPDATE OR DELETE ON reparasjonsoperasjoner
    FOR EACH ROW EXECUTE FUNCTION reparasjon_append_only();
DROP TRIGGER IF EXISTS reparasjon_ingen_truncate ON reparasjonsoperasjoner;
CREATE TRIGGER reparasjon_ingen_truncate BEFORE TRUNCATE ON reparasjonsoperasjoner
    FOR EACH STATEMENT EXECUTE FUNCTION reparasjon_append_only();

-- ------------------------------------------------------------
-- 5. Oppdrag — outbox-protokollen (v2 pkt. 2, v3 pkt. 2, v4 pkt. 2-3)
--
-- M-37 erklærer ALDRI en forretningshandling utført. Den ber om en
-- policystyrt beslutning, og hvis den blir TILLAT legges et OPPDRAG ut.
-- Saken lukkes først når en eiermodul har levert en signert,
-- ressursbundet kvittering. Det er forskjellen på «vi har bedt om det» og
-- «det er gjort», og uten den forskjellen ville M-37 hatt fullmakter den
-- ikke skal ha.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS oppdrag (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant              TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    unntak_id           BIGINT NOT NULL,
    loggpost_id         BIGINT NOT NULL,
    repair_operation_id TEXT NOT NULL,
    oppdragstype        TEXT NOT NULL,
    handling            TEXT NOT NULL,
    eiermodul           TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'opprettet'
                        CHECK (status IN ('opprettet','plukket','utfort','feilet','kansellert')),
    status_ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Payload er kryptert med tenantens DEK, som alt annet saksinnhold.
    -- Eiermodulen får klartekst fra API-laget etter dataminimering — den
    -- ser aldri ciphertext og aldri en nøkkel (v4 pkt. 4).
    payload_kryptert    BYTEA NOT NULL,
    key_id              TEXT NOT NULL,
    alg                 TEXT NOT NULL DEFAULT 'AES-256-GCM',
    nonce               BYTEA NOT NULL,
    -- Owner-fencing (v4 pkt. 3), symmetrisk med sakens claim-fencing.
    owner_claim_id      TEXT,
    owner_generation    INT NOT NULL DEFAULT 0,
    owner_lease_utloper TIMESTAMPTZ,
    -- To frister (v4 pkt. 2): utførelse kan endre status automatisk,
    -- evidens kan bare LAGRES. En frist er ikke to ting.
    utforelsesfrist     TIMESTAMPTZ NOT NULL,
    evidensfrist        TIMESTAMPTZ NOT NULL,
    kvittering          JSONB,
    kvittering_signatur TEXT,
    resultathash        TEXT,
    opprettet           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT oppdrag_tenant_id_unik UNIQUE (tenant, id),
    -- Andre forsvarslinje mot dublett-oppdrag hvis fencing skulle glippe
    -- (v2 pkt. 3). Unikheten er per tenant, ikke global.
    CONSTRAINT oppdrag_repair_unik UNIQUE (tenant, repair_operation_id),
    CONSTRAINT oppdrag_frister CHECK (evidensfrist >= utforelsesfrist),
    FOREIGN KEY (tenant, unntak_id)   REFERENCES unntak (tenant, id),
    FOREIGN KEY (tenant, loggpost_id) REFERENCES revisjonslogg (tenant, id),
    FOREIGN KEY (tenant, key_id)      REFERENCES tenant_nokler (tenant, key_id),
    FOREIGN KEY (tenant, repair_operation_id)
        REFERENCES reparasjonsoperasjoner (tenant, repair_operation_id)
);
CREATE INDEX IF NOT EXISTS oppdrag_ko
    ON oppdrag (eiermodul, status, opprettet, id) WHERE status = 'opprettet';
CREATE INDEX IF NOT EXISTS oppdrag_sak ON oppdrag (tenant, unntak_id);

CREATE OR REPLACE FUNCTION oppdrag_kolonnelaas()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.unntak_id IS DISTINCT FROM OLD.unntak_id
       OR NEW.loggpost_id IS DISTINCT FROM OLD.loggpost_id
       OR NEW.repair_operation_id IS DISTINCT FROM OLD.repair_operation_id
       OR NEW.oppdragstype IS DISTINCT FROM OLD.oppdragstype
       OR NEW.handling IS DISTINCT FROM OLD.handling
       OR NEW.eiermodul IS DISTINCT FROM OLD.eiermodul
       OR NEW.payload_kryptert IS DISTINCT FROM OLD.payload_kryptert
       OR NEW.key_id IS DISTINCT FROM OLD.key_id
       OR NEW.alg IS DISTINCT FROM OLD.alg
       OR NEW.nonce IS DISTINCT FROM OLD.nonce
       OR NEW.utforelsesfrist IS DISTINCT FROM OLD.utforelsesfrist
       OR NEW.evidensfrist IS DISTINCT FROM OLD.evidensfrist
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'oppdrag: kun status-, owner- og kvitteringsfelter kan endres';
    END IF;
    IF NEW.owner_generation < OLD.owner_generation THEN
        RAISE EXCEPTION 'oppdrag: owner_generation kan aldri reduseres';
    END IF;
    -- Kvitteringen er evidens. Var den overskrivbar, ville «to utførere med
    -- ulike resultater» blitt til «det siste svaret vant» i stedet for en
    -- sikkerhetssak (v3 pkt. 3).
    IF OLD.kvittering IS NOT NULL
       AND (NEW.kvittering IS DISTINCT FROM OLD.kvittering
            OR NEW.kvittering_signatur IS DISTINCT FROM OLD.kvittering_signatur
            OR NEW.resultathash IS DISTINCT FROM OLD.resultathash) THEN
        RAISE EXCEPTION 'oppdrag: kvitteringen er uforanderlig når den først er lagret';
    END IF;
    IF NOT (
        (OLD.status = 'opprettet' AND NEW.status IN ('plukket','kansellert','feilet')) OR
        (OLD.status = 'plukket'   AND NEW.status IN ('utfort','feilet','opprettet')) OR
        (OLD.status = NEW.status)
    ) THEN
        RAISE EXCEPTION 'oppdrag: ulovlig statusovergang % -> %', OLD.status, NEW.status;
    END IF;
    IF OLD.status IN ('utfort','feilet','kansellert') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'oppdrag: % er terminal', OLD.status;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        NEW.status_ts := now();
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS oppdrag_laas ON oppdrag;
CREATE TRIGGER oppdrag_laas BEFORE UPDATE ON oppdrag
    FOR EACH ROW EXECUTE FUNCTION oppdrag_kolonnelaas();

CREATE OR REPLACE FUNCTION oppdrag_ingen_sletting()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'oppdrag er append+status: % er forbudt', TG_OP;
END $$;
DROP TRIGGER IF EXISTS oppdrag_ingen_delete ON oppdrag;
CREATE TRIGGER oppdrag_ingen_delete BEFORE DELETE ON oppdrag
    FOR EACH ROW EXECUTE FUNCTION oppdrag_ingen_sletting();
DROP TRIGGER IF EXISTS oppdrag_ingen_truncate ON oppdrag;
CREATE TRIGGER oppdrag_ingen_truncate BEFORE TRUNCATE ON oppdrag
    FOR EACH STATEMENT EXECUTE FUNCTION oppdrag_ingen_sletting();

-- ============================================================
-- FRA HER OG UT SEKSJON 9 KJØRER VI SOM `disponit_m37_claimer`.
--
-- Førsteutkastet opprettet alt som migrator og flyttet eierskapet med
-- `ALTER ... OWNER TO` til slutt. Det virket ÉN gang: ved neste kjøring
-- eide migrator ingenting lenger, og `DROP TRIGGER IF EXISTS ... ON
-- arbeidskapabiliteter` feilet med «must be owner of table». Migrasjonen
-- var altså ikke idempotent — i strid med kontrakten, og oppdaget først
-- da 80 tester veltet fordi fixturene kjører `migrer()` på nytt.
--
-- Å opprette objektene SOM riktig eier fjerner hele problemet: ingen
-- eierskifter, og hver eneste kjøring gjør nøyaktig det samme.
-- `SET LOCAL` gjelder til transaksjonen avsluttes, og kjøreren eier
-- transaksjonen — derfor står det en eksplisitt `RESET ROLE` før neste
-- seksjon, ellers ville kjøreren forsøkt å skrive sin egen
-- `migrasjoner`-rad som en rolle uten rettigheter på den tabellen.
-- ============================================================
SET LOCAL ROLE disponit_m37_claimer;

-- ------------------------------------------------------------
-- 6. Arbeidskapabiliteter (v3 pkt. 1 + v4 pkt. 1 + GO-vilkår V1)
--
-- Pre-auth-paradokset: arbeideren trenger en identitet API-et kan
-- autentisere, men et globalt M-37-token ville vært en fullmakt på tvers av
-- alle tenanter — altså nøyaktig det null-fullmaktsprinsippet forbyr.
--
-- Løsningen er symmetrisk med resten av plattformen: en DB-backet,
-- ENGANGS, fencing-bundet kapabilitet. Kompromitteres arbeideren, får
-- angriperen maks ÉN handling på ÉN sak i ETT lease-vindu. Ingen
-- nøkkeldistribusjon, ingen stateless token som kan spilles av på nytt.
--
-- Tabellen er RLS-fri MED VILJE og eies av en NOLOGIN-rolle: den nås kun
-- gjennom SECURITY DEFINER-funksjonene under. En RLS-policy her ville vært
-- teater — funksjonene kjører uansett som eieren.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS arbeidskapabiliteter (
    jti                 TEXT PRIMARY KEY CHECK (jti ~ '^[0-9a-f]{32,}$'),
    tenant              TEXT NOT NULL,
    unntak_id           BIGINT NOT NULL,
    claim_id            TEXT NOT NULL,
    claim_generation    INT  NOT NULL,
    repair_operation_id TEXT NOT NULL,
    tillatt_handling    TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'utstedt'
                        CHECK (status IN ('utstedt','reservert','brukt','feilet')),
    request_id          TEXT,
    utstedt             TIMESTAMPTZ NOT NULL DEFAULT now(),
    reservert_ts        TIMESTAMPTZ,
    reservasjon_utloper TIMESTAMPTZ,
    utloper             TIMESTAMPTZ NOT NULL,
    brukt_ts            TIMESTAMPTZ,
    -- GO-vilkår V1, den delen som KAN stå som CHECK: en reservasjon kan
    -- aldri leve lenger enn kapabiliteten selv. Resten av invarianten
    -- (kapabilitet_utloper <= claim_utloper) krever oppslag i `unntak` og
    -- håndheves av triggeren rett under — en CHECK kan ikke lese andre rader.
    CONSTRAINT kapabilitet_reservasjon_innenfor CHECK (
        reservasjon_utloper IS NULL OR reservasjon_utloper <= utloper),
    CONSTRAINT kapabilitet_reservert_har_frist CHECK (
        status <> 'reservert'
        OR (reservasjon_utloper IS NOT NULL AND request_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS kapabilitet_sak
    ON arbeidskapabiliteter (tenant, unntak_id);
CREATE INDEX IF NOT EXISTS kapabilitet_reservert
    ON arbeidskapabiliteter (reservasjon_utloper) WHERE status = 'reservert';

-- GO-vilkår V1, andre halvdel: reservasjon_utloper <= utloper <= claim_utloper.
--
-- Dette er en TRIGGER og ikke bare en clamp i utstedelsesfunksjonen, fordi
-- en clamp i én funksjon er en egenskap ved den funksjonen. Legger noen til
-- en vei nummer to inn i tabellen, gjelder clampen ikke lenger — og
-- «porten dekket bare den ene veien jeg tenkte på» er funnfamilien som har
-- kostet oss fem runder på PR #8 og fire på PR-005a.
CREATE OR REPLACE FUNCTION kapabilitet_innenfor_claim()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $$
DECLARE
    v_claim_utloper TIMESTAMPTZ;
    v_generation    INT;
BEGIN
    SELECT u.claim_utloper, u.claim_generation
      INTO v_claim_utloper, v_generation
      FROM public.unntak u
     WHERE u.tenant = NEW.tenant AND u.id = NEW.unntak_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'arbeidskapabilitet viser til ukjent sak %/%',
            NEW.tenant, NEW.unntak_id;
    END IF;
    IF v_claim_utloper IS NULL OR NEW.utloper > v_claim_utloper THEN
        RAISE EXCEPTION
            'arbeidskapabilitet: utloper (%) overstiger claim_utloper (%) — GO-vilkår V1',
            NEW.utloper, v_claim_utloper;
    END IF;
    IF NEW.claim_generation <> v_generation THEN
        RAISE EXCEPTION
            'arbeidskapabilitet: claim_generation % != sakens % — utdatert fencing',
            NEW.claim_generation, v_generation;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS kapabilitet_tidsgrense ON arbeidskapabiliteter;
CREATE TRIGGER kapabilitet_tidsgrense BEFORE INSERT ON arbeidskapabiliteter
    FOR EACH ROW EXECUTE FUNCTION kapabilitet_innenfor_claim();

-- Statusmaskin: utstedt -> reservert -> brukt | feilet.
CREATE OR REPLACE FUNCTION kapabilitet_statusmaskin()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.jti IS DISTINCT FROM OLD.jti
       OR NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.unntak_id IS DISTINCT FROM OLD.unntak_id
       OR NEW.claim_id IS DISTINCT FROM OLD.claim_id
       OR NEW.claim_generation IS DISTINCT FROM OLD.claim_generation
       OR NEW.repair_operation_id IS DISTINCT FROM OLD.repair_operation_id
       OR NEW.tillatt_handling IS DISTINCT FROM OLD.tillatt_handling
       OR NEW.utloper IS DISTINCT FROM OLD.utloper
       OR NEW.utstedt IS DISTINCT FROM OLD.utstedt THEN
        RAISE EXCEPTION 'arbeidskapabiliteter: identitets- og bindingsfelter er uforanderlige';
    END IF;
    IF NOT (
        (OLD.status = 'utstedt'   AND NEW.status IN ('reservert','feilet')) OR
        (OLD.status = 'reservert' AND NEW.status IN ('brukt','feilet','reservert')) OR
        (OLD.status = NEW.status)
    ) THEN
        RAISE EXCEPTION 'arbeidskapabiliteter: ulovlig overgang % -> %',
            OLD.status, NEW.status;
    END IF;
    IF OLD.status IN ('brukt','feilet') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'arbeidskapabiliteter: % er terminal', OLD.status;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS kapabilitet_overgang ON arbeidskapabiliteter;
CREATE TRIGGER kapabilitet_overgang BEFORE UPDATE ON arbeidskapabiliteter
    FOR EACH ROW EXECUTE FUNCTION kapabilitet_statusmaskin();

REVOKE ALL ON arbeidskapabiliteter FROM PUBLIC;

-- ------------------------------------------------------------
-- 7. SECURITY DEFINER: claim av sak
--
-- Herdingen er den samme som `verifiser_token` fikk i 003/004:
-- NOLOGIN-eier, `search_path = pg_catalog`, kun skjemakvalifiserte
-- objekter, ingen dynamisk SQL, REVOKE ALL FROM PUBLIC.
--
-- MERK om kvalifiseringen: `LEAST`, `GREATEST` og `COALESCE` er SQL-
-- NØKKELORD, ikke katalogfunksjoner. `pg_catalog.least(...)` finnes ikke
-- og feiler med «function does not exist» — funnet ved å kjøre, ikke ved å
-- lese. De er parserkonstruksjoner og kan uansett ikke kapres via
-- search_path, så ukvalifisert er både nødvendig og trygt. Ekte funksjoner
-- (`now`, `count`, `unnest`, `array_length`, `current_setting`,
-- `set_config`) er fortsatt kvalifisert — de KAN skygges.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION claim_neste_sak(p_claim_id TEXT, p_lease_s INT DEFAULT 120)
RETURNS TABLE (tenant TEXT, id BIGINT, handling TEXT, kategori TEXT,
               loggpost_id BIGINT, claim_generation INT, claim_utloper TIMESTAMPTZ,
               forsok INT, maks_auto_forsok_snapshot INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_lease INT;
BEGIN
    -- Parameterherding (v3 pkt. 6). En claim_id kalleren velger fritt er
    -- ikke et fencing-token: kortere eller ikke-tilfeldige verdier kan
    -- gjettes, og da kan en annen prosess skrive med en annens token.
    IF p_claim_id IS NULL OR p_claim_id !~ '^[0-9a-f]{32,}$' THEN
        RAISE EXCEPTION 'claim_neste_sak: ugyldig claim_id-format';
    END IF;
    v_lease := least(greatest(
        coalesce(p_lease_s, 120), 30), 600);

    RETURN QUERY
    UPDATE public.unntak u
       SET status = 'under_behandling',
           claim_id = p_claim_id,
           claim_generation = u.claim_generation + 1,
           claim_utloper = pg_catalog.now() + (v_lease || ' seconds')::INTERVAL,
           forsok = u.forsok + 1
     WHERE (u.tenant, u.id) = (
        SELECT k.tenant, k.id
          FROM public.unntak k
         WHERE k.sakstype = 'normal'
           AND k.status = 'ny'
           -- Effektiv grense = LEAST(snapshot, plattformtak 3). Systemet
           -- kan STRAMME INN globalt, aldri løsne en kundes grense.
           AND k.forsok < least(
                 coalesce(k.maks_auto_forsok_snapshot, 0), 3)
           -- Anti-dominans (v3 pkt. 6): en tenant med fem saker under
           -- behandling får ikke ta den sjette før noe frigjøres. Full
           -- per-tenant fairness er deklarert M-38-scope.
           AND (SELECT pg_catalog.count(*) FROM public.unntak b
                 WHERE b.tenant = k.tenant
                   AND b.status = 'under_behandling'
                   AND b.claim_utloper > pg_catalog.now()) < 5
         -- Deterministisk rekkefølge. MERK: spesifikasjonen skriver
         -- `ORDER BY prioritet DESC`, men kolonnen er TEKST med verdiene
         -- 'hoy' og 'normal', og 'hoy' < 'normal' — DESC ville altså
         -- sortert NORMAL FØRST og gjort høyprioriterte saker til de
         -- siste i køen. Rangeringen er derfor eksplisitt.
         ORDER BY (CASE k.prioritet WHEN 'hoy' THEN 0 ELSE 1 END), k.ts, k.id
           FOR UPDATE SKIP LOCKED
         LIMIT 1)
    RETURNING u.tenant, u.id, u.handling, u.kategori, u.loggpost_id,
              u.claim_generation, u.claim_utloper, u.forsok,
              u.maks_auto_forsok_snapshot;
    -- Payload returneres ALDRI herfra. Arbeideren henter og dekrypterer
    -- den i sin egen tenantbundne transaksjon, der RLS gjelder.
END $$;
REVOKE ALL ON FUNCTION claim_neste_sak(TEXT, INT) FROM PUBLIC;

-- Gjenopptak etter krasj: saker med utløpt lease tilbake til køen.
--
-- MÅ være SECURITY DEFINER, og det var ikke åpenbart før testen kjørte.
-- Første utkast gjorde denne UPDATE-en rett fra arbeideren, og den traff
-- ALLTID null rader: `unntak` har row level security med FORCE, og
-- opprydningen kan per definisjon ikke sette `disponit.tenant` på forhånd
-- — den vet ikke hvilke tenanter som har hengende saker. Resultatet var en
-- gjenopptaksvei som så ut til å kjøre og aldri gjorde noe.
--
-- Statusmaskinen tillater `under_behandling -> ny` KUN når leasen faktisk
-- er utløpt, så funksjonen kan ikke rive en sak fra en arbeider som lever
-- — heller ikke ved en programmeringsfeil her.
CREATE OR REPLACE FUNCTION frigi_utlopte_claims()
RETURNS INT
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_treff INT;
BEGIN
    UPDATE public.unntak u
       SET status = 'ny', claim_id = NULL
     WHERE u.status = 'under_behandling'
       AND u.claim_utloper < pg_catalog.now();
    GET DIAGNOSTICS v_treff = ROW_COUNT;
    RETURN v_treff;
END $$;
REVOKE ALL ON FUNCTION frigi_utlopte_claims() FROM PUBLIC;

-- Fornyelse ved 50 % av leasen — samme claim_id OG generasjon i WHERE.
CREATE OR REPLACE FUNCTION forny_claim(p_tenant TEXT, p_unntak_id BIGINT,
                                       p_claim_id TEXT, p_generation INT,
                                       p_lease_s INT DEFAULT 120)
RETURNS TIMESTAMPTZ
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_ny    TIMESTAMPTZ;
    v_lease INT := least(greatest(
        coalesce(p_lease_s, 120), 30), 600);
BEGIN
    UPDATE public.unntak u
       SET claim_utloper = pg_catalog.now() + (v_lease || ' seconds')::INTERVAL
     WHERE u.tenant = p_tenant AND u.id = p_unntak_id
       AND u.claim_id = p_claim_id
       AND u.claim_generation = p_generation
       AND u.status = 'under_behandling'
       AND u.claim_utloper > pg_catalog.now()
    RETURNING u.claim_utloper INTO v_ny;
    RETURN v_ny;   -- NULL == leasen er tapt; kalleren SKAL avbryte
END $$;
REVOKE ALL ON FUNCTION forny_claim(TEXT, BIGINT, TEXT, INT, INT) FROM PUBLIC;

-- ------------------------------------------------------------
-- 8. SECURITY DEFINER: arbeidskapabilitetens livssyklus
--
-- Parameterherdingen fra v4 pkt. 1 er det viktigste her:
-- `utsted_arbeidskapabilitet` tar KUN (claim_id, claim_generation).
-- `tillatt_handling` og `repair_operation_id` UTLEDES fra den registrerte
-- reparasjonsklassifiseringen. Arbeideren kan altså ikke be om en handling
-- — den kan bare be om å få utføre den handlingen klassifiseringen allerede
-- har bundet den til. Den negative testen er triviell fordi signaturen gjør
-- angrepet umulig å uttrykke.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION utsted_arbeidskapabilitet(
        p_claim_id TEXT, p_claim_generation INT, p_jti TEXT,
        p_levetid_s INT DEFAULT 60)
RETURNS TABLE (jti TEXT, tenant TEXT, unntak_id BIGINT,
               tillatt_handling TEXT, repair_operation_id TEXT,
               utloper TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    r RECORD;
    v_utloper TIMESTAMPTZ;
BEGIN
    IF p_jti IS NULL OR p_jti !~ '^[0-9a-f]{32,}$' THEN
        RAISE EXCEPTION 'utsted_arbeidskapabilitet: ugyldig jti-format';
    END IF;
    IF p_claim_id IS NULL OR p_claim_id !~ '^[0-9a-f]{32,}$' THEN
        RAISE EXCEPTION 'utsted_arbeidskapabilitet: ugyldig claim_id-format';
    END IF;

    -- Fencing-WHERE i sin fulle form: claim_id OG generasjon OG status OG
    -- levende lease. Uten alle fire er dette bare et oppslag.
    SELECT u.tenant, u.id, u.claim_utloper, o.repair_operation_id, o.maalhandling
      INTO r
      FROM public.unntak u
      JOIN public.reparasjonsoperasjoner o
        ON o.tenant = u.tenant AND o.unntak_id = u.id AND o.status = 'aktiv'
     WHERE u.claim_id = p_claim_id
       AND u.claim_generation = p_claim_generation
       AND u.status = 'under_behandling'
       AND u.claim_utloper > pg_catalog.now();
    IF NOT FOUND THEN
        RETURN;   -- tapt lease eller ingen klassifisering: ingen kapabilitet
    END IF;

    -- GO-vilkår V1: kapabiliteten kan ALDRI leve lenger enn claimen.
    -- Clampen her og triggeren på tabellen sier det samme; det er med
    -- vilje. Clampen gir en riktig verdi, triggeren gjør en gal verdi
    -- umulig — også for en fremtidig andre vei inn.
    v_utloper := least(
        pg_catalog.now() + (least(greatest(
            coalesce(p_levetid_s, 60), 5), 300) || ' seconds')::INTERVAL,
        r.claim_utloper);

    INSERT INTO public.arbeidskapabiliteter
        (jti, tenant, unntak_id, claim_id, claim_generation,
         repair_operation_id, tillatt_handling, utloper)
    VALUES (p_jti, r.tenant, r.id, p_claim_id, p_claim_generation,
            r.repair_operation_id, r.maalhandling, v_utloper);

    jti := p_jti;
    tenant := r.tenant;
    unntak_id := r.id;
    tillatt_handling := r.maalhandling;
    repair_operation_id := r.repair_operation_id;
    utloper := v_utloper;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION utsted_arbeidskapabilitet(TEXT, INT, TEXT, INT) FROM PUBLIC;

-- Pre-auth-veien: utstedt -> reservert, atomisk, bundet til request_id.
--
-- Gjenopptak (v4 pkt. 1 punkt 4): SAMME request_id kan gjenoppta en
-- allerede reservert kapabilitet. Enhver ANNEN request avvises — det er
-- porten som hindrer at en parallell forespørsel overtar en reservasjon
-- midt i en pågående transaksjon.
CREATE OR REPLACE FUNCTION reserver_kapabilitet(p_jti TEXT, p_request_id TEXT,
                                                p_reservasjon_s INT DEFAULT 300)
RETURNS TABLE (tenant TEXT, unntak_id BIGINT, tillatt_handling TEXT,
               repair_operation_id TEXT, claim_id TEXT, claim_generation INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_frist TIMESTAMPTZ;
BEGIN
    IF p_jti IS NULL OR p_request_id IS NULL OR length(btrim(p_request_id)) = 0 THEN
        RETURN;
    END IF;
    -- GO-vilkår V1 igjen: reservasjonsfristen klippes til kapabilitetens
    -- egen utløpstid. 5-minutters-timeouten fra v4 gjelder FRIGJØRING til
    -- `feilet` og kan aldri forlenge noe.
    RETURN QUERY
    UPDATE public.arbeidskapabiliteter k
       SET status = 'reservert',
           request_id = p_request_id,
           reservert_ts = pg_catalog.now(),
           reservasjon_utloper = least(
               pg_catalog.now() + (least(greatest(
                   coalesce(p_reservasjon_s, 300), 30), 300)
                   || ' seconds')::INTERVAL,
               k.utloper)
     WHERE k.jti = p_jti
       AND k.utloper > pg_catalog.now()
       AND (k.status = 'utstedt'
            -- gjenopptak: kun samme forespørsel
            OR (k.status = 'reservert' AND k.request_id = p_request_id))
    RETURNING k.tenant, k.unntak_id, k.tillatt_handling,
              k.repair_operation_id, k.claim_id, k.claim_generation;
END $$;
REVOKE ALL ON FUNCTION reserver_kapabilitet(TEXT, TEXT, INT) FROM PUBLIC;

-- Forbrukes i SAMME commit som den auditerte beslutningen (v4 pkt. 1
-- punkt 3). Kalles derfor fra INNSIDEN av `kjerne.behandle()`s transaksjon,
-- ikke fra pre-auth: en kapabilitet som brennes før loggposten er
-- committet, er brent uten evidens for hva den ble brukt til.
CREATE OR REPLACE FUNCTION bruk_kapabilitet(p_jti TEXT, p_request_id TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_treff INT;
BEGIN
    UPDATE public.arbeidskapabiliteter k
       SET status = 'brukt', brukt_ts = pg_catalog.now()
     WHERE k.jti = p_jti
       AND k.status = 'reservert'
       AND k.request_id = p_request_id
       AND k.utloper > pg_catalog.now();
    GET DIAGNOSTICS v_treff = ROW_COUNT;
    RETURN v_treff = 1;
END $$;
REVOKE ALL ON FUNCTION bruk_kapabilitet(TEXT, TEXT) FROM PUBLIC;

-- Frigjøring av hengende reservasjoner (v4 pkt. 1 punkt 5).
--
-- Den viktige delen er BETINGELSEN, ikke tidsgrensen: en reservasjon
-- frigjøres KUN hvis det verken finnes en ferdig idempotensrespons eller en
-- auditert beslutning med samme repair_operation_id. Uten den sjekken ville
-- en treg, men vellykket transaksjon fått kapabiliteten sin revet vekk, og
-- gjenopptaket ville laget en NY beslutning på en handling som alt var
-- utført.
CREATE OR REPLACE FUNCTION frigi_hengende_kapabiliteter()
RETURNS INT
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_treff INT;
BEGIN
    UPDATE public.arbeidskapabiliteter k
       SET status = 'feilet'
     WHERE k.status = 'reservert'
       AND k.reservasjon_utloper < pg_catalog.now()
       AND NOT EXISTS (
            SELECT 1 FROM public.idempotens i
             WHERE i.tenant = k.tenant
               AND i.nokkel = k.repair_operation_id
               AND i.status = 'ferdig')
       AND NOT EXISTS (
            SELECT 1 FROM public.revisjonslogg r
             WHERE r.tenant = k.tenant
               AND r.idempotency_key = k.repair_operation_id);
    GET DIAGNOSTICS v_treff = ROW_COUNT;
    RETURN v_treff;
END $$;
REVOKE ALL ON FUNCTION frigi_hengende_kapabiliteter() FROM PUBLIC;

-- ------------------------------------------------------------
-- 9. SECURITY DEFINER: oppdragsclaim for eiermoduler (v3 pkt. 2)
--
-- Eiermodulen ser KUN oppdrag som er bundet til den ved opprettelsen.
-- Ubundne oppdrag finnes ikke — `eiermodul` er NOT NULL — og et oppdrag
-- for en annen modul kan ikke nås, uansett hva kalleren sender inn.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION claim_neste_oppdrag(p_modul_id TEXT, p_prefiks TEXT[],
                                               p_claim_id TEXT,
                                               p_lease_s INT DEFAULT 300)
RETURNS TABLE (id BIGINT, tenant TEXT, unntak_id BIGINT, oppdragstype TEXT,
               handling TEXT, repair_operation_id TEXT,
               payload_kryptert BYTEA, key_id TEXT, nonce BYTEA,
               owner_generation INT, utforelsesfrist TIMESTAMPTZ,
               evidensfrist TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_lease INT := least(greatest(
        coalesce(p_lease_s, 300), 30), 3600);
BEGIN
    IF p_claim_id IS NULL OR p_claim_id !~ '^[0-9a-f]{32,}$' THEN
        RAISE EXCEPTION 'claim_neste_oppdrag: ugyldig claim_id-format';
    END IF;
    IF p_modul_id IS NULL OR length(btrim(p_modul_id)) = 0 THEN
        RAISE EXCEPTION 'claim_neste_oppdrag: modul_id mangler';
    END IF;

    RETURN QUERY
    UPDATE public.oppdrag o
       SET status = 'plukket',
           owner_claim_id = p_claim_id,
           owner_generation = o.owner_generation + 1,
           owner_lease_utloper = pg_catalog.now() + (v_lease || ' seconds')::INTERVAL
     WHERE o.id = (
        SELECT k.id FROM public.oppdrag k
         WHERE (
                 k.status = 'opprettet'
                 -- RECLAIM AV UTLØPT EIER-LEASE (Codex P1, runde 1).
                 --
                 -- Uten denne grenen var et `plukket` oppdrag PERMANENT
                 -- uclaimbart: ingenting førte det tilbake til `opprettet`,
                 -- selv om statusmaskinen tillot overgangen. Et krasj i
                 -- eiermodulen mellom claim-commit og kvittering parkerte
                 -- dermed saken for alltid, og owner-fencingen hadde ingen
                 -- reell gjenopptaksvei — den kunne bare nekte den gamle
                 -- eieren, aldri slippe til en ny.
                 --
                 -- Reclaim skjer i SAMME atomiske setning som en vanlig
                 -- claim, med ny owner_claim_id og ØKT owner_generation.
                 -- Det er nettopp generasjonsøkningen som gjør den gamle
                 -- eierens sene kvittering til evidens i stedet for til en
                 -- avslutning.
                 OR (k.status = 'plukket'
                     AND k.owner_lease_utloper IS NOT NULL
                     AND k.owner_lease_utloper < pg_catalog.now())
               )
           AND k.eiermodul = p_modul_id
           -- Prefikslisten er modulens scope. Er den tom eller NULL,
           -- treffer ingenting — fail-closed, ikke «alle».
           AND p_prefiks IS NOT NULL
           AND pg_catalog.array_length(p_prefiks, 1) > 0
           AND EXISTS (SELECT 1 FROM pg_catalog.unnest(p_prefiks) AS pre
                        WHERE k.handling LIKE pre || '%')
           AND k.utforelsesfrist > pg_catalog.now()
         ORDER BY k.opprettet, k.id
           FOR UPDATE SKIP LOCKED
         LIMIT 1)
    RETURNING o.id, o.tenant, o.unntak_id, o.oppdragstype, o.handling,
              o.repair_operation_id, o.payload_kryptert, o.key_id, o.nonce,
              o.owner_generation, o.utforelsesfrist, o.evidensfrist;
END $$;
REVOKE ALL ON FUNCTION claim_neste_oppdrag(TEXT, TEXT[], TEXT, INT) FROM PUBLIC;

-- ------------------------------------------------------------
-- 9b. Kvitteringskapabilitet (v3-delta pkt. 2, v4-delta pkt. 2)
--
-- Codex P1 runde 1: første leveranse hoppet over denne og lot
-- modultokenet være hele adgangsbilletten til kvitteringsporten. Et
-- langlivet token som kan kvittere for HVILKET SOM HELST oppdrag modulen
-- noensinne har hatt, er ikke den per-oppdrag-bindingen spesifikasjonen
-- krever — og en merknad i en PR-beskrivelse kan ikke oppheve en kontrakt.
--
-- Symmetrisk med arbeidskapabiliteten: DB-backet, serverbundet identitet,
-- ingen nøkkeldistribusjon. Forskjellen er levetiden — den følger
-- EVIDENSFRISTEN (v4 pkt. 2), ikke eier-leasen, fordi en kvittering som
-- kommer etter at leasen gikk ut fortsatt er gyldig EVIDENS. Den binder
-- til oppdraget, ikke til claimen.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kvitteringskapabiliteter (
    jti              TEXT PRIMARY KEY CHECK (jti ~ '^[0-9a-f]{32,}$'),
    tenant           TEXT NOT NULL,
    oppdrag_id       BIGINT NOT NULL,
    modul_id         TEXT NOT NULL,
    owner_claim_id   TEXT NOT NULL,
    owner_generation INT  NOT NULL,
    status           TEXT NOT NULL DEFAULT 'utstedt'
                     CHECK (status IN ('utstedt','brukt','feilet')),
    resultathash     TEXT,
    utstedt          TIMESTAMPTZ NOT NULL DEFAULT now(),
    utloper          TIMESTAMPTZ NOT NULL,
    brukt_ts         TIMESTAMPTZ,
    -- En brukt kapabilitet MÅ bære resultatet den ble brukt til. Uten det
    -- kan ikke en re-post skilles fra et motstridende resultat, og
    -- «identisk kvittering er idempotent» blir umulig å håndheve.
    CONSTRAINT kvitteringskapabilitet_brukt_har_hash CHECK (
        status <> 'brukt' OR resultathash IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS kvitteringskapabilitet_oppdrag
    ON kvitteringskapabiliteter (tenant, oppdrag_id);

CREATE OR REPLACE FUNCTION kvitteringskapabilitet_statusmaskin()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.jti IS DISTINCT FROM OLD.jti
       OR NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.oppdrag_id IS DISTINCT FROM OLD.oppdrag_id
       OR NEW.modul_id IS DISTINCT FROM OLD.modul_id
       OR NEW.owner_claim_id IS DISTINCT FROM OLD.owner_claim_id
       OR NEW.owner_generation IS DISTINCT FROM OLD.owner_generation
       OR NEW.utloper IS DISTINCT FROM OLD.utloper
       OR NEW.utstedt IS DISTINCT FROM OLD.utstedt THEN
        RAISE EXCEPTION 'kvitteringskapabiliteter: bindingsfelter er uforanderlige';
    END IF;
    IF OLD.resultathash IS NOT NULL
       AND NEW.resultathash IS DISTINCT FROM OLD.resultathash THEN
        RAISE EXCEPTION 'kvitteringskapabiliteter: resultatet er uforanderlig';
    END IF;
    IF OLD.status = 'feilet' AND NEW.status <> 'feilet' THEN
        RAISE EXCEPTION 'kvitteringskapabiliteter: feilet er terminal';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS kvitteringskapabilitet_overgang ON kvitteringskapabiliteter;
CREATE TRIGGER kvitteringskapabilitet_overgang BEFORE UPDATE
    ON kvitteringskapabiliteter
    FOR EACH ROW EXECUTE FUNCTION kvitteringskapabilitet_statusmaskin();

REVOKE ALL ON kvitteringskapabiliteter FROM PUBLIC;

-- Utstedes av claim-veien. Parameterherdet på samme måte som
-- arbeidskapabiliteten: tenant, modul og frist UTLEDES fra oppdragsraden,
-- og kalleren kan derfor ikke be om en kapabilitet for et oppdrag den ikke
-- nettopp har claimet.
CREATE OR REPLACE FUNCTION utsted_kvitteringskapabilitet(
        p_oppdrag_id BIGINT, p_owner_claim_id TEXT, p_owner_generation INT,
        p_jti TEXT)
RETURNS TABLE (jti TEXT, utloper TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE r RECORD;
BEGIN
    IF p_jti IS NULL OR p_jti !~ '^[0-9a-f]{32,}$' THEN
        RAISE EXCEPTION 'utsted_kvitteringskapabilitet: ugyldig jti-format';
    END IF;
    SELECT o.tenant, o.eiermodul, o.evidensfrist INTO r
      FROM public.oppdrag o
     WHERE o.id = p_oppdrag_id
       AND o.owner_claim_id = p_owner_claim_id
       AND o.owner_generation = p_owner_generation
       AND o.status = 'plukket';
    IF NOT FOUND THEN
        RETURN;   -- ikke vår claim: ingen kapabilitet
    END IF;
    INSERT INTO public.kvitteringskapabiliteter
        (jti, tenant, oppdrag_id, modul_id, owner_claim_id, owner_generation,
         utloper)
    VALUES (p_jti, r.tenant, p_oppdrag_id, r.eiermodul, p_owner_claim_id,
            p_owner_generation, r.evidensfrist);
    jti := p_jti;
    utloper := r.evidensfrist;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION utsted_kvitteringskapabilitet(BIGINT, TEXT, INT, TEXT)
    FROM PUBLIC;

-- Innløses i pre-auth på kvitteringsveien. BRENNER IKKE — en kvittering er
-- idempotent, og en modul som mistet svaret sitt skal kunne re-poste.
-- `modul_id` sammenlignes med den innloggede modulens identitet, så en
-- annen modul aldri kan innløse en kapabilitet den har fått tak i.
CREATE OR REPLACE FUNCTION innlos_kvitteringskapabilitet(p_jti TEXT,
                                                         p_modul_id TEXT)
RETURNS TABLE (tenant TEXT, oppdrag_id BIGINT, owner_claim_id TEXT,
               owner_generation INT, status TEXT, resultathash TEXT)
LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT k.tenant, k.oppdrag_id, k.owner_claim_id, k.owner_generation,
           k.status, k.resultathash
      FROM public.kvitteringskapabiliteter k
     WHERE k.jti = p_jti
       AND k.modul_id = p_modul_id
       AND k.status <> 'feilet'
       AND k.utloper > pg_catalog.now()
$$;
REVOKE ALL ON FUNCTION innlos_kvitteringskapabilitet(TEXT, TEXT) FROM PUBLIC;

-- Forbrukes i SAMME commit som statusskiftet på oppdraget og saken.
--
-- Funksjonen returnerer UTFALLET, ikke en boolean, og det er Codex' P1 fra
-- runde 3: to transaksjoner kan begge lese kapabiliteten som
-- `utstedt/resultathash=NULL`, begge passere hashkontrollene i app-laget,
-- og så kappes om denne UPDATE-en. Vinneren committer; TAPEREN blokkerte
-- på radlåsen og fikk `false`.
--
-- Med en boolean hadde kalleren ingenting å klassifisere på, og svarte
-- `kapabilitet_ugyldig`. Da ble to identiske samtidige kvitteringer til
-- «202 + 401» i stedet for «202 + idempotent», og to MOTSTRIDENDE
-- samtidige kvitteringer forsvant som et generisk auth-avvik i stedet for
-- å bli en sikkerhetssak. Kontrakten «identisk => idempotent, to hasher =>
-- sikkerhetssak» gjaldt altså bare når postene kom etter hverandre.
--
-- Klassifiseringen hører hjemme HER og ikke i app-laget: taperens UPDATE
-- blokkerer til vinneren committer, og først da finnes svaret. Et oppslag
-- fra kalleren etterpå ville vært en andre lesing i et nytt vindu — altså
-- et nytt kappløp for å avgjøre utfallet av det første.
--
-- Under READ COMMITTED får hver setning sin egen snapshot: SELECT-en under
-- kjører ETTER at UPDATE-en slapp låsen, og ser derfor vinnerens
-- committede rad.
DROP FUNCTION IF EXISTS bruk_kvitteringskapabilitet(TEXT, TEXT);
CREATE FUNCTION bruk_kvitteringskapabilitet(p_jti TEXT, p_resultathash TEXT)
RETURNS TEXT
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_treff  INT;
    v_status TEXT;
    v_hash   TEXT;
BEGIN
    UPDATE public.kvitteringskapabiliteter k
       SET status = 'brukt', resultathash = p_resultathash,
           brukt_ts = pg_catalog.now()
     WHERE k.jti = p_jti
       AND k.status = 'utstedt'
       AND k.utloper > pg_catalog.now();
    GET DIAGNOSTICS v_treff = ROW_COUNT;
    IF v_treff = 1 THEN
        RETURN 'brukt';
    END IF;

    -- Vi tapte kappløpet, eller kapabiliteten var alt brukt/utløpt.
    SELECT k.status, k.resultathash INTO v_status, v_hash
      FROM public.kvitteringskapabiliteter k
     WHERE k.jti = p_jti;
    IF NOT FOUND THEN
        RETURN 'ugyldig';
    END IF;
    IF v_status = 'brukt' THEN
        -- `IS NOT DISTINCT FROM` og ikke `=`: en NULL-hash ville gjort
        -- sammenligningen NULL, og en NULL i en IF er usann — altså ville
        -- en uventet tilstand blitt klassifisert som konflikt i stedet for
        -- som ugyldig. CHECK-en garanterer riktig nok at `brukt` har hash,
        -- men en vakt som stoler på en annen vakt er én endring unna å
        -- være feil.
        IF v_hash IS NOT DISTINCT FROM p_resultathash THEN
            RETURN 'idempotent';
        END IF;
        RETURN 'konflikt';
    END IF;
    RETURN 'ugyldig';   -- feilet, utløpt eller ukjent tilstand: fail-closed
END $$;
REVOKE ALL ON FUNCTION bruk_kvitteringskapabilitet(TEXT, TEXT) FROM PUBLIC;

-- Tilbake til migrator: `policyer` eies av skjemaeieren, og bare eieren
-- kan legge en trigger på den.
RESET ROLE;

-- ------------------------------------------------------------
-- 10. GO-vilkår V3: policyretention håndhevet i DATABASEN
--
-- «Referert policyversjon kan ikke slettes» sto i v4 som en dokumentert
-- intensjon. En regel som bare står i et dokument er ikke håndhevet — det
-- er den samme lærdommen som ga oss feilveitabellen som DATA og
-- `Transaksjonsvakt` som kjøretidsegenskap.
--
-- Triggeren fyrer for ENHVER rolle som gjør DELETE, inkludert migrator og
-- skjemaeieren: PostgreSQL hopper bare over triggere for den som eksplisitt
-- slår dem av (ALTER TABLE ... DISABLE TRIGGER, kun eier) eller setter
-- session_replication_role='replica' (kun superbruker). Den første veien
-- lukkes av event-triggeren i oppsett-postgresql.sh; den andre krever en
-- superbruker vi ikke gir til noen tjeneste.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION policy_retention_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $$
DECLARE
    v_ref TEXT;
BEGIN
    -- Bevisst arkivering går gjennom `arkiver_policyversjon`, som setter
    -- dette flagget for sin egen transaksjon. Alt annet er en DELETE som
    -- ikke har tenkt på referansene.
    IF pg_catalog.current_setting('disponit.policy_arkivering', true) = 'paagaar' THEN
        RETURN OLD;
    END IF;

    SELECT 'revisjonslogg' INTO v_ref
      FROM public.revisjonslogg r
     WHERE r.tenant = OLD.tenant AND r.policy_content_hash = OLD.innholds_hash
     LIMIT 1;
    IF v_ref IS NULL THEN
        SELECT 'ikke-terminalt unntak' INTO v_ref
          FROM public.unntak u
         WHERE u.tenant = OLD.tenant
           AND u.policy_content_hash = OLD.innholds_hash
           AND u.status NOT IN ('løst','avvist','manuell')
         LIMIT 1;
    END IF;
    IF v_ref IS NULL THEN
        SELECT 'oppdrag' INTO v_ref
          FROM public.oppdrag o
          JOIN unntak u ON u.tenant = o.tenant AND u.id = o.unntak_id
         WHERE o.tenant = OLD.tenant
           AND u.policy_content_hash = OLD.innholds_hash
         LIMIT 1;
    END IF;
    IF v_ref IS NULL THEN
        SELECT 'reparasjonsoperasjon' INTO v_ref
          FROM public.reparasjonsoperasjoner p
          JOIN unntak u ON u.tenant = p.tenant AND u.id = p.unntak_id
         WHERE p.tenant = OLD.tenant
           AND u.policy_content_hash = OLD.innholds_hash
         LIMIT 1;
    END IF;

    IF v_ref IS NOT NULL THEN
        RAISE EXCEPTION
            'policyversjon %/%/% er referert av % og kan ikke slettes (GO-vilkår V3)',
            OLD.tenant, OLD.policy_id, OLD.versjon, v_ref;
    END IF;
    RETURN OLD;
END $$;
DROP TRIGGER IF EXISTS policy_retention ON policyer;
CREATE TRIGGER policy_retention BEFORE DELETE ON policyer
    FOR EACH ROW EXECUTE FUNCTION policy_retention_vakt();

CREATE OR REPLACE FUNCTION policyer_ingen_truncate()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- TRUNCATE omgår rad-triggere fullstendig. Uten denne ville hele
    -- retention-vakten vært én setning unna å være virkningsløs.
    RAISE EXCEPTION 'policyer: TRUNCATE er forbudt — retention håndheves per rad';
END $$;
DROP TRIGGER IF EXISTS policyer_ingen_truncate ON policyer;
CREATE TRIGGER policyer_ingen_truncate BEFORE TRUNCATE ON policyer
    FOR EACH STATEMENT EXECUTE FUNCTION policyer_ingen_truncate();

-- Den ENESTE sanksjonerte veien forbi den generelle DELETE-sperren.
--
-- Merk at den ikke er en omgåelse av REGELEN: den kjører nøyaktig de samme
-- referansesjekkene. Det den omgår er den blanke sperren, slik at ekte
-- arkivering av en UREFERERT policyversjon er mulig uten å måtte slå av en
-- vakt. En escape hatch som kan slette en referert versjon ville vært den
-- samme feilen som «en advarsel med exit 0».
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION arkiver_policyversjon(p_tenant TEXT, p_policy_id TEXT,
                                                 p_versjon TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_hash   TEXT;
    v_aktiv  BOOLEAN;
    v_treff  INT;
BEGIN
    SELECT p.innholds_hash, p.aktiv INTO v_hash, v_aktiv
      FROM public.policyer p
     WHERE p.tenant = p_tenant AND p.policy_id = p_policy_id
       AND p.versjon = p_versjon;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    IF v_aktiv THEN
        RAISE EXCEPTION 'arkiver_policyversjon: den AKTIVE versjonen kan ikke arkiveres';
    END IF;
    IF EXISTS (SELECT 1 FROM public.revisjonslogg r
                WHERE r.tenant = p_tenant AND r.policy_content_hash = v_hash)
       OR EXISTS (SELECT 1 FROM public.unntak u
                   WHERE u.tenant = p_tenant AND u.policy_content_hash = v_hash
                     AND u.status NOT IN ('løst','avvist','manuell')) THEN
        RAISE EXCEPTION
            'arkiver_policyversjon: %/%/% er referert — arkivering avvist (GO-vilkår V3)',
            p_tenant, p_policy_id, p_versjon;
    END IF;
    PERFORM pg_catalog.set_config('disponit.policy_arkivering', 'paagaar', true);
    DELETE FROM public.policyer p
     WHERE p.tenant = p_tenant AND p.policy_id = p_policy_id
       AND p.versjon = p_versjon;
    GET DIAGNOSTICS v_treff = ROW_COUNT;
    PERFORM pg_catalog.set_config('disponit.policy_arkivering', '', true);
    RETURN v_treff = 1;
END $$;
REVOKE ALL ON FUNCTION arkiver_policyversjon(TEXT, TEXT, TEXT) FROM PUBLIC;

-- ------------------------------------------------------------
-- 10b. Backfill-hjelper: hvilke tenanter mangler policysnapshot?
--
-- Backfillen kjører som MIGRATOR, og row level security med FORCE gjelder
-- også tabelleieren (migrasjon 002, med vilje). Uten `disponit.tenant` satt
-- ser migrator NULL rader — altså kan den ikke engang finne ut hvilke
-- tenanter som finnes.
--
-- Alternativet var å slå av FORCE under backfillen. Det er nøyaktig
-- «vakten som slås av av sitt eget oppsett», og den formen har allerede
-- kostet oss et P1 i PR-005a. Denne funksjonen returnerer KUN tenantnavn,
-- aldri saksdata, og backfillen setter deretter kontekst per tenant som
-- alle andre veier inn gjør.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION tenanter_uten_policysnapshot()
RETURNS TABLE (tenant TEXT, antall BIGINT)
LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT u.tenant, pg_catalog.count(*)
      FROM public.unntak u
     WHERE u.maks_auto_forsok_snapshot IS NULL
        OR u.policy_versjon IS NULL
        OR u.policy_content_hash IS NULL
     GROUP BY u.tenant
     ORDER BY u.tenant
$$;
REVOKE ALL ON FUNCTION tenanter_uten_policysnapshot() FROM PUBLIC;

-- Backfillen kjøres av MIGRATOR, som etter `WITH INHERIT FALSE` ikke arver
-- eierrollens rettigheter. Uten denne ene GRANT-en kan den som skal kjøre
-- backfillen ikke engang finne ut hvilke tenanter som mangler snapshot.
--
-- `SESSION_USER` og ikke `CURRENT_USER`: vi står inne i `SET LOCAL ROLE`,
-- så CURRENT_USER er eierrollen — og en GRANT til seg selv er en STILLE
-- NO-OP. SESSION_USER er fortsatt den som koblet til, altså migrator.
-- Det er nettopp den forskjellen som gjorde det første forsøket grønt og
-- virkningsløst på samme tid.
GRANT EXECUTE ON FUNCTION tenanter_uten_policysnapshot() TO SESSION_USER;

-- Skjemaeieren får rydde kapabilitetstabellene.
--
-- Det svekker ingenting: kapabilitetsmodellen beskytter mot RUNTIME, som
-- verken har tabelltilgang eller kan SET ROLE hit. Migrator eier skjemaet
-- og kunne uansett droppe tabellene — å nekte den DELETE var derfor ikke
-- en kontroll, bare en ulempe.
--
-- Ulempen var reell: testsuiten kunne ikke rydde radene, de hopet seg opp
-- på tvers av kjøringer, og `frigi_hengende_kapabiliteter()` er global.
-- Resultatet var tester som feilet på ULIKT sted mellom kjøringer. En
-- suite som ikke er hermetisk, måler tilfeldigheter.
-- SELECT trengs også: en `DELETE ... WHERE tenant=…` må lese kolonnen.
GRANT SELECT, DELETE ON public.arbeidskapabiliteter TO SESSION_USER;
GRANT SELECT, DELETE ON public.kvitteringskapabiliteter TO SESSION_USER;

-- Tilbake til migrator for resten av filen. Uten denne linjen ville
-- kjøreren forsøkt å skrive sin egen `migrasjoner`-rad som eierrollen,
-- som ikke har rettigheter på den tabellen.
RESET ROLE;

-- ------------------------------------------------------------
-- 11. RLS + FORCE på de nye tenant-tabellene (mønster fra 002/003)
--
-- `arbeidskapabiliteter` står bevisst IKKE i listen: den eies av
-- NOLOGIN-rollen og nås kun via SECURITY DEFINER, der funksjonene kjører
-- som eieren og RLS derfor ikke ville bitt uansett. En policy der ville
-- sett ut som en kontroll uten å være en.
-- ------------------------------------------------------------
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['oppdrag','reparasjonsoperasjoner']
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

-- ------------------------------------------------------------
-- 12. Hva `disponit_m37_claimer` faktisk trenger — og hvorfor
--
-- Funnet ved å KJØRE, ikke ved å lese: SECURITY DEFINER kjører som EIEREN,
-- og eieren her er en fersk NOLOGIN-rolle uten en eneste rettighet. Første
-- kjøring ga «permission denied for table unntak» inne i funksjonen.
-- Rettighetene er altså ikke pynt — uten dem finnes ikke claim-veien.
--
-- Det andre, og viktigere: `unntak` har row level security med FORCE, og
-- eieren av FUNKSJONEN er ikke eieren av TABELLEN, så policyen gjelder.
-- Claim-veien kan per definisjon ikke sette `disponit.tenant` på forhånd —
-- den finner tenanten ved å claime. Uten en policy ville funksjonen sett
-- null rader og køen vært evig tom, stille.
--
-- Valget står mellom BYPASSRLS på rollen og en EKSPLISITT policy her.
-- BYPASSRLS ville gitt rollen fritak på ALLE tabeller, for alltid, usynlig
-- i skjemaet. Policyene under står i migrasjonen, gjelder navngitte
-- tabeller, og kan leses av den som lurer på hvem som ser hva. Rekkevidden
-- er innelukket av at rollen er NOLOGIN: ingen kan koble til som den, og
-- de eneste veiene inn er funksjonene over — som ikke engang KAN uttrykke
-- en forespørsel om en annen tenants sak, fordi de ikke tar tenant som
-- parameter.
-- ------------------------------------------------------------
GRANT SELECT, UPDATE ON unntak    TO disponit_m37_claimer;
GRANT INSERT           ON unntak_historikk TO disponit_m37_claimer;
GRANT SELECT           ON reparasjonsoperasjoner TO disponit_m37_claimer;
GRANT SELECT, UPDATE ON oppdrag   TO disponit_m37_claimer;
GRANT SELECT           ON idempotens, revisjonslogg TO disponit_m37_claimer;
GRANT SELECT, DELETE ON policyer  TO disponit_m37_claimer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO disponit_m37_claimer;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['unntak','unntak_historikk','oppdrag',
                             'reparasjonsoperasjoner','idempotens',
                             'revisjonslogg','policyer']
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS m37_dispatcher ON %I', t);
        -- Permissiv policy, OR-et sammen med tenant_isolasjon.
        --
        -- `TO disponit_m37_claimer` ALENE er IKKE nok, og det er den
        -- viktigste linjen i denne migrasjonen: PostgreSQL matcher
        -- TO-klausulen med `pg_has_role(..., 'USAGE')`, altså ARVET
        -- medlemskap. Migrator MÅ være medlem av rollen for å kunne sette
        -- eierskap (OWNER TO), og med vanlig GRANT arvet den dermed denne
        -- policyen — på revisjonslogg, unntak og alt annet i listen.
        --
        -- Resultatet var at skjemaeieren igjen så alle tenanter. Det er
        -- NØYAKTIG Codex' P1 nr. 2 fra PR-004, gjeninnført bakveien av en
        -- GRANT som så ut som en ren formalitet. Fanget av den eksisterende
        -- testen `test_ogsaa_skjemaeieren_er_underlagt_tenant_isolasjonen`.
        --
        -- Predikatet under navngir betingelsen i stedet for å stole på
        -- rollemedlemskapets finmekanikk: policyen gjelder når koden
        -- FAKTISK kjører som dispatcher-rollen — det vil si inne i en av
        -- SECURITY DEFINER-funksjonene. Oppsettet gir i tillegg
        -- medlemskapet `WITH INHERIT FALSE`, så de to lagene stopper
        -- henholdsvis arv og alt annet enn ekte dispatcher-kontekst.
        EXECUTE format(
            'CREATE POLICY m37_dispatcher ON %I TO disponit_m37_claimer
                USING      (current_user = ''disponit_m37_claimer'')
                WITH CHECK (current_user = ''disponit_m37_claimer'')', t);
    END LOOP;
END $$;

-- Rettighetene til runtime settes IKKE her.
--
-- 003 har et betinget `IF EXISTS (... 'disponit_runtime')`-blokk som aldri
-- har gjort noe: runtime-rollen heter `disponit`, ikke `disponit_runtime`.
-- Den stille no-op-en er beholdt i historikken (immutable), men skal ikke
-- kopieres videre. Rettighetene settes av `deploy/staging/migrer.py`, som
-- er den ENESTE veien inn for migrasjoner og kjenner det faktiske
-- rollenavnet.
