-- ============================================================
-- 016 — Domenekontroll · egress · artefakt (PR-014b): plattforminfrastruktur
-- alle senere eiermoduler arver. INGEN WCAG-spesifikk logikk.
--
-- NUMMERERING: klarsignalet skrev «migrasjon 014», men 014 (modulregister) OG
-- 015 (claim-binding) er alt tatt av 014a (PR #29, i review). Å skrive dette til
-- 014 ville korrumpert den migrasjonen. Denne er derfor 016 — additiv, rører
-- ingen kolonne/constraint på tidligere tabeller.
--
-- SKJEMA-TILPASNINGER mot den FAKTISKE 014a-kjeden (klarsignalets DDL var mot et
-- idealisert skjema):
--   * `oppdrag` har PK `id BIGINT` (ikke `oppdrag_id UUID`). `artefakt.oppdrag_id`
--     er derfor BIGINT med kompositt-FK (tenant, oppdrag_id) → oppdrag(tenant, id).
--   * `dek_ref` er `tenant_nokler.key_id` (kompositt-FK (tenant, dek_ref)).
--   * `v_domeneautorisasjon` er `security_invoker=true` (RLS gjelder egress), og
--     egress får KOLONNE-SELECT på nøyaktig visningens kolonner — aldri
--     `challenge_token_hash`. RLS+FORCE er det som faktisk innkapsler egress.
-- ============================================================

-- ------------------------------------------------------------
-- 1. domenekontroll — bevis på DNS-sonekontroll på et tidspunkt (ikke eierskap).
--    status/autorisasjonsgenerasjon endres KUN via §2-funksjonene.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS domenekontroll (
    tenant                        TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    hostname                      TEXT NOT NULL,   -- IDNA2008 A-label, lowercase, uten avsluttende punktum
    status                        TEXT NOT NULL DEFAULT 'ventende'
        CHECK (status IN ('ventende','verifisert','avklaring_kreves','utlopt','tilbakekalt')),
    wildcard                      BOOLEAN NOT NULL DEFAULT false,
    autorisasjonsgenerasjon       BIGINT NOT NULL DEFAULT 0,   -- monoton (§3 B1)
    challenge_token_hash          TEXT,            -- sha256; klartekst vises ÉN gang, lagres aldri
    challenge_utstedt             TIMESTAMPTZ,
    challenge_utloper             TIMESTAMPTZ,     -- 7 døgn
    verifisert_ts                 TIMESTAMPTZ,
    siste_vellykkede_revalidering TIMESTAMPTZ,
    utloper                       TIMESTAMPTZ,     -- verifisert_ts + 90 døgn
    opprettet                     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, hostname)
);
-- Kun ÉN verifisert eier per hostname på tvers av tenanter (§3 B2/B4).
CREATE UNIQUE INDEX IF NOT EXISTS en_verifisert_per_hostname
    ON domenekontroll (hostname) WHERE status = 'verifisert';

-- ------------------------------------------------------------
-- 2. domenekontroll_hendelse — append-only revisjon av alle overganger + grunn.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS domenekontroll_hendelse (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant                  TEXT NOT NULL,
    hostname                TEXT NOT NULL,
    hendelse                TEXT NOT NULL,
    fra_status              TEXT,
    til_status              TEXT,
    autorisasjonsgenerasjon BIGINT,
    grunn                   TEXT,
    aktor                   TEXT NOT NULL,
    detalj                  JSONB,
    ts                      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS domenekontroll_hendelse_host
    ON domenekontroll_hendelse (tenant, hostname, id);

-- ------------------------------------------------------------
-- 3. hostname_binding — global serialiseringsautoritet (§3 B2). INGEN RLS,
--    INGEN runtime-SELECT. Taper nummer to observerer COMMITTET tilstand her,
--    ikke en unique violation.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hostname_binding (
    hostname   TEXT PRIMARY KEY,
    tenant     TEXT NOT NULL,
    bundet_ts  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 4. artefakttype_register — global, bundet til modulkontrakten (014a).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artefakttype_register (
    artefakttype     TEXT PRIMARY KEY CHECK (length(btrim(artefakttype)) > 0),
    eiermodul        TEXT NOT NULL,
    kontraktversjon  INT  NOT NULL,
    kontrakt_hash    TEXT NOT NULL,
    skjema_hash      TEXT NOT NULL,
    opprettet        TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (eiermodul, kontraktversjon, kontrakt_hash)
        REFERENCES modulkontrakt (modul_id, kontraktversjon, kontrakt_hash)
);

-- ------------------------------------------------------------
-- 5. artefakt — tenant-scopet, RLS+FORCE. `tilstand` endres KUN via §2.
--    `klartekst_sha256` er SERVERBEREGNET (JCS). `ciphertext` nulles ved forkastet.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artefakt (
    artefakt_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant           TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    oppdrag_id       BIGINT NOT NULL,
    artefakttype     TEXT NOT NULL REFERENCES artefakttype_register (artefakttype),
    modul_id         TEXT NOT NULL,
    release_id       TEXT NOT NULL,
    kontraktversjon  INT  NOT NULL,
    kontrakt_hash    TEXT NOT NULL,
    module_epoch     BIGINT NOT NULL,
    tilstand         TEXT NOT NULL DEFAULT 'staged'
        CHECK (tilstand IN ('staged','promotert','forkastet','karantene','bevart')),
    storrelse_bytes  INT NOT NULL
        CHECK (storrelse_bytes > 0 AND storrelse_bytes <= 1048576),   -- 1 MiB (v1)
    klartekst_sha256 TEXT NOT NULL,
    ciphertext       BYTEA,                 -- nullbar: nulles ved 'forkastet'
    nonce            BYTEA,                 -- AES-GCM-nonce (lagres separat, som oppdrag); nulles ved 'forkastet'
    dek_ref          TEXT NOT NULL,
    kapabilitet_jti  TEXT NOT NULL UNIQUE,
    opprettet        TIMESTAMPTZ NOT NULL DEFAULT now(),
    promotert_ts     TIMESTAMPTZ,
    CONSTRAINT artefakt_oppdrag_fk
        FOREIGN KEY (tenant, oppdrag_id) REFERENCES oppdrag (tenant, id),
    CONSTRAINT artefakt_dek_fk
        FOREIGN KEY (tenant, dek_ref) REFERENCES tenant_nokler (tenant, key_id),
    CONSTRAINT artefakt_kontrakt_fk
        FOREIGN KEY (modul_id, kontraktversjon, kontrakt_hash)
        REFERENCES modulkontrakt (modul_id, kontraktversjon, kontrakt_hash)
);
-- Nøyaktig ÉN promotert artefakt per (oppdrag, artefakttype).
CREATE UNIQUE INDEX IF NOT EXISTS ett_promotert_per_oppdrag
    ON artefakt (oppdrag_id, artefakttype) WHERE tilstand = 'promotert';
CREATE INDEX IF NOT EXISTS artefakt_staged_opprydding
    ON artefakt (opprettet) WHERE tilstand = 'staged';

-- ------------------------------------------------------------
-- 6. v_domeneautorisasjon — eneste flate egress-proxyen ser (§3 B1).
--    security_invoker=true: RLS gjelder egress (uten det ville visningen kjørt
--    med eierens rettigheter og omgått RLS → proxyen sett alle tenanters rader).
--    `sett_kontekst` MÅ kjøres først også fra proxyen (invariant 7).
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW v_domeneautorisasjon WITH (security_invoker = true) AS
    SELECT tenant, hostname, autorisasjonsgenerasjon,
           (status = 'verifisert' AND now() < utloper
            AND siste_vellykkede_revalidering > now() - interval '72 hours')
           AS gyldig,
           -- Codex: scope-biten MÅ eksponeres. Uten den kan egress ikke skille en
           -- eksakt-host-verifisering fra en wildcard, og måtte enten avvise ALL
           -- subdomenebruk eller risikere å behandle en eksakt verifisering som
           -- autorisasjon for subdomener.
           wildcard AS wildcard_scope
      FROM domenekontroll;

-- ============================================================
-- Integritetstriggere
-- ============================================================

-- 7a. Append-only: domenekontroll_hendelse + artefakttype_register.
CREATE OR REPLACE FUNCTION domene_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% er append-only (immutable): % er forbudt',
        TG_TABLE_NAME, TG_OP;
END $$;
DROP TRIGGER IF EXISTS hendelse_append_only ON domenekontroll_hendelse;
CREATE TRIGGER hendelse_append_only BEFORE UPDATE OR DELETE ON domenekontroll_hendelse
    FOR EACH ROW EXECUTE FUNCTION domene_append_only();
DROP TRIGGER IF EXISTS artefakttype_immutable ON artefakttype_register;
CREATE TRIGGER artefakttype_immutable BEFORE UPDATE OR DELETE ON artefakttype_register
    FOR EACH ROW EXECUTE FUNCTION domene_append_only();
-- Codex P2: TRUNCATE omgår rad-triggere i PostgreSQL. Uten en statement-nivå
-- TRUNCATE-vakt kunne skjemaeieren tømt disse append-only-tabellene tross
-- immutabilitets-invarianten. (domene_append_only rapporterer TG_OP='TRUNCATE'.)
DROP TRIGGER IF EXISTS hendelse_ingen_truncate ON domenekontroll_hendelse;
CREATE TRIGGER hendelse_ingen_truncate BEFORE TRUNCATE ON domenekontroll_hendelse
    FOR EACH STATEMENT EXECUTE FUNCTION domene_append_only();
DROP TRIGGER IF EXISTS artefakttype_ingen_truncate ON artefakttype_register;
CREATE TRIGGER artefakttype_ingen_truncate BEFORE TRUNCATE ON artefakttype_register
    FOR EACH STATEMENT EXECUTE FUNCTION domene_append_only();

-- 7b. domenekontroll kolonnelås: identitet frosset, autorisasjonsgenerasjon
--     monoton, status-statemaskin. Selve fullmakten (resolver-enighet, PSL,
--     takeover) håndheves i §2-funksjonene; dette er sikkerhetsnettet mot en
--     ulovlig direkte overgang.
CREATE OR REPLACE FUNCTION domenekontroll_statemaskin()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.hostname IS DISTINCT FROM OLD.hostname THEN
        RAISE EXCEPTION 'domenekontroll: (tenant, hostname) er uforanderlig';
    END IF;
    IF NEW.autorisasjonsgenerasjon < OLD.autorisasjonsgenerasjon THEN
        RAISE EXCEPTION 'domenekontroll: autorisasjonsgenerasjon er monoton (% -> %)',
            OLD.autorisasjonsgenerasjon, NEW.autorisasjonsgenerasjon;
    END IF;
    IF NOT (
        (OLD.status = 'ventende'         AND NEW.status IN ('ventende','verifisert','avklaring_kreves','utlopt','tilbakekalt')) OR
        (OLD.status = 'verifisert'       AND NEW.status IN ('verifisert','avklaring_kreves','utlopt','tilbakekalt')) OR
        (OLD.status = 'avklaring_kreves' AND NEW.status IN ('avklaring_kreves','verifisert','tilbakekalt','utlopt')) OR
        -- `avklaring_kreves` må kunne nås fra ENHVER tilstand som allerede kan
        -- nå `verifisert`. En tenant hvis forrige overtakelse ble avvist står
        -- `tilbakekalt` (og en utløpt står `utlopt`); tar den senere DNS-
        -- kontrollen fra en aktiv eier igjen, skal overtakelsen ende i ny
        -- avklaring — uten disse to falt B4-grenen på en ulovlig overgang og
        -- HELE verifiseringen feilet. Avklaring gir ingen autorisasjon
        -- (`v_domeneautorisasjon.gyldig` krever `verifisert`), så dette er en
        -- strengt svakere overgang enn den allerede tillatte til `verifisert`.
        (OLD.status = 'utlopt'           AND NEW.status IN ('utlopt','ventende','verifisert','tilbakekalt','avklaring_kreves')) OR
        (OLD.status = 'tilbakekalt'      AND NEW.status IN ('tilbakekalt','ventende','verifisert','avklaring_kreves')) OR
        (OLD.status = NEW.status)
    ) THEN
        RAISE EXCEPTION 'domenekontroll: ulovlig statusovergang % -> %',
            OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS domenekontroll_laas ON domenekontroll;
CREATE TRIGGER domenekontroll_laas BEFORE UPDATE ON domenekontroll
    FOR EACH ROW EXECUTE FUNCTION domenekontroll_statemaskin();

-- 7c. artefakt kolonnelås: identitet/binding frosset; tilstand fremover-only
--     staged→{promotert,forkastet} (begge terminale); ciphertext kan KUN nulles
--     (ved forkastet), aldri endres til noe annet.
CREATE OR REPLACE FUNCTION artefakt_statemaskin()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.artefakt_id  IS DISTINCT FROM OLD.artefakt_id
       OR NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.oppdrag_id IS DISTINCT FROM OLD.oppdrag_id
       OR NEW.artefakttype IS DISTINCT FROM OLD.artefakttype
       OR NEW.modul_id IS DISTINCT FROM OLD.modul_id
       OR NEW.release_id IS DISTINCT FROM OLD.release_id
       OR NEW.kontraktversjon IS DISTINCT FROM OLD.kontraktversjon
       OR NEW.kontrakt_hash IS DISTINCT FROM OLD.kontrakt_hash
       OR NEW.module_epoch IS DISTINCT FROM OLD.module_epoch
       OR NEW.klartekst_sha256 IS DISTINCT FROM OLD.klartekst_sha256
       OR NEW.kapabilitet_jti IS DISTINCT FROM OLD.kapabilitet_jti
       -- Codex: dek_ref MÅ fryses. Krypteringen binder AES-GCM-AAD til
       -- tenant|key_id (db/kryptering.py); en repointing til en annen nøkkel
       -- ville gjort artefaktet UDEKRYPTERBART mens ciphertext/hash/tilstand står
       -- urørt — bevaret evidens stille korrumpert.
       OR NEW.dek_ref IS DISTINCT FROM OLD.dek_ref
       OR NEW.storrelse_bytes IS DISTINCT FROM OLD.storrelse_bytes THEN
        RAISE EXCEPTION 'artefakt: identitet/binding/hash er frosset';
    END IF;
    IF NOT (
        (OLD.tilstand = 'staged'
         AND NEW.tilstand IN ('staged','promotert','forkastet','karantene','bevart')) OR
        (OLD.tilstand = NEW.tilstand)
    ) THEN
        RAISE EXCEPTION 'artefakt: ulovlig tilstandsovergang % -> %',
            OLD.tilstand, NEW.tilstand;
    END IF;
    -- Codex: 'karantene' og 'bevart' er RETAINED (ryddes aldri) — 'karantene' er
    -- artefaktet til en kvittering som ikke lot seg verifisere (epoch/binding),
    -- 'bevart' er artefaktet til en godtatt SEN kvittering (evidens som aldri
    -- kan promoteres). Begge terminale.
    IF OLD.tilstand IN ('promotert','forkastet','karantene','bevart')
       AND NEW.tilstand <> OLD.tilstand THEN
        RAISE EXCEPTION 'artefakt: % er terminal', OLD.tilstand;
    END IF;
    -- Codex: ciphertext/nonce er KOBLET til forkastelsen. Uten koblingen kunne en
    -- feilaktig privilegert UPDATE nulle nyttelasten til et staged/promotert/
    -- retained artefakt mens tilstand + hash fortsatt påsto at evidensen fantes.
    -- Eneste lovlige mutasjon: SAMME update setter staged → forkastet og nuller
    -- BEGGE feltene.
    IF NEW.ciphertext IS DISTINCT FROM OLD.ciphertext
       OR NEW.nonce IS DISTINCT FROM OLD.nonce THEN
        IF NOT (OLD.tilstand = 'staged' AND NEW.tilstand = 'forkastet'
                AND NEW.ciphertext IS NULL AND NEW.nonce IS NULL) THEN
            RAISE EXCEPTION 'artefakt: ciphertext/nonce kan kun nulles i '
                'overgangen staged -> forkastet';
        END IF;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS artefakt_laas ON artefakt;
CREATE TRIGGER artefakt_laas BEFORE UPDATE ON artefakt
    FOR EACH ROW EXECUTE FUNCTION artefakt_statemaskin();

-- 7d. Ingen sletting av domenekontroll/artefakt/hostname_binding (evidens/binding).
CREATE OR REPLACE FUNCTION domene_ingen_sletting()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '%: sletting/truncate er forbudt (evidens/binding)', TG_TABLE_NAME;
END $$;
-- Codex: DELETE- OG TRUNCATE-vakt på ALLE tre evidens-/bindingstabellene
-- (artefakt manglet vakt helt; ingen hadde TRUNCATE-vakt → skjemaeieren kunne
-- tømt bevisene). En statement-nivå TRUNCATE-vakt per tabell + rad-nivå DELETE.
DROP TRIGGER IF EXISTS domenekontroll_ingen_delete ON domenekontroll;
CREATE TRIGGER domenekontroll_ingen_delete BEFORE DELETE ON domenekontroll
    FOR EACH ROW EXECUTE FUNCTION domene_ingen_sletting();
DROP TRIGGER IF EXISTS domenekontroll_ingen_truncate ON domenekontroll;
CREATE TRIGGER domenekontroll_ingen_truncate BEFORE TRUNCATE ON domenekontroll
    FOR EACH STATEMENT EXECUTE FUNCTION domene_ingen_sletting();
DROP TRIGGER IF EXISTS hostname_binding_ingen_delete ON hostname_binding;
CREATE TRIGGER hostname_binding_ingen_delete BEFORE DELETE ON hostname_binding
    FOR EACH ROW EXECUTE FUNCTION domene_ingen_sletting();
DROP TRIGGER IF EXISTS hostname_binding_ingen_truncate ON hostname_binding;
CREATE TRIGGER hostname_binding_ingen_truncate BEFORE TRUNCATE ON hostname_binding
    FOR EACH STATEMENT EXECUTE FUNCTION domene_ingen_sletting();
DROP TRIGGER IF EXISTS artefakt_ingen_delete ON artefakt;
CREATE TRIGGER artefakt_ingen_delete BEFORE DELETE ON artefakt
    FOR EACH ROW EXECUTE FUNCTION domene_ingen_sletting();
DROP TRIGGER IF EXISTS artefakt_ingen_truncate ON artefakt;
CREATE TRIGGER artefakt_ingen_truncate BEFORE TRUNCATE ON artefakt
    FOR EACH STATEMENT EXECUTE FUNCTION domene_ingen_sletting();

-- ============================================================
-- RLS + FORCE (tenant-isolasjon) på domenekontroll, domenekontroll_hendelse,
-- artefakt. hostname_binding og artefakttype_register er GLOBALE (ingen RLS).
-- ============================================================
ALTER TABLE domenekontroll           ENABLE ROW LEVEL SECURITY;
ALTER TABLE domenekontroll           FORCE  ROW LEVEL SECURITY;
ALTER TABLE domenekontroll_hendelse  ENABLE ROW LEVEL SECURITY;
ALTER TABLE domenekontroll_hendelse  FORCE  ROW LEVEL SECURITY;
ALTER TABLE artefakt                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE artefakt                 FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolasjon ON domenekontroll;
CREATE POLICY tenant_isolasjon ON domenekontroll
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
DROP POLICY IF EXISTS tenant_isolasjon ON domenekontroll_hendelse;
CREATE POLICY tenant_isolasjon ON domenekontroll_hendelse
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
DROP POLICY IF EXISTS tenant_isolasjon ON artefakt;
CREATE POLICY tenant_isolasjon ON artefakt
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- ============================================================
-- GRANT-modell (default-deny). Funksjonene i §2 (neste seksjon) er den ENESTE
-- skriveveien; her gis lesetilgangene.
-- ============================================================
-- Runtime: SELECT på domenekontroll, artefakt, artefakttype_register (INGEN
-- skriv, INGEN SELECT på hostname_binding) gis i migrer.py RETTIGHETER — der
-- runtime-grantsettet er autoritativt og overlever REVOKE-ALL-syklusen (samme
-- mønster som 014a-registeret; en løs GRANT her ville blitt vasket bort).

-- Egress: KUN visningen. security_invoker krever SELECT på visningens
-- basiskolonner — gitt som KOLONNE-grant (aldri challenge_token_hash), og RLS+
-- FORCE innkapsler egress til gjeldende tenant-kontekst.
GRANT SELECT ON v_domeneautorisasjon TO disponit_egress;
GRANT SELECT (tenant, hostname, autorisasjonsgenerasjon, status, utloper,
              siste_vellykkede_revalidering, wildcard)
    ON domenekontroll TO disponit_egress;

-- ============================================================
-- §2 Herdede funksjoner (SECURITY DEFINER, eid av `disponit_domene_eier`,
-- search_path=pg_catalog). domene_eier har BYPASSRLS fordi B4-takeover er
-- iboende KRYSS-TENANT (revoker A, setter B avklaring). DNS-fakta (≥2 resolvere
-- enige, PSL) verifiseres av Python-kalleren FØR kallet — som `sett_modulstatus`
-- i 014a; her håndheves det DB-en eier: hostname-advisory-lås, atomisk
-- state-maskin, generasjon++, hostname_binding-autoriteten, revisjon.
-- ============================================================
SET LOCAL ROLE disponit_domene_eier;

-- Utsted challenge. Kalleren genererer ≥128-bit token, lagrer ALDRI klartekst —
-- her lagres kun sha256-hashen + 7-døgnsvindu. Reutstedelse er gratis (idempotent
-- oppdatering av hash/vindu; rører ikke en allerede verifisert status).
CREATE OR REPLACE FUNCTION utsted_challenge(
    p_tenant     TEXT,
    p_hostname   TEXT,
    p_wildcard   BOOLEAN,
    p_token_hash TEXT,
    p_aktor      TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    INSERT INTO public.domenekontroll (tenant, hostname, status, wildcard,
        challenge_token_hash, challenge_utstedt, challenge_utloper)
        VALUES (p_tenant, p_hostname, 'ventende', p_wildcard, p_token_hash,
                now(), now() + interval '7 days')
    ON CONFLICT (tenant, hostname) DO UPDATE
        SET challenge_token_hash = p_token_hash, challenge_utstedt = now(),
            challenge_utloper = now() + interval '7 days',
            -- Codex: en re-utstedelse skal IKKE kunne utvide scope. Mens raden er
            -- verifisert ELLER avventer M-37-avklaring beholdes gammel wildcard —
            -- ellers kunne B reutstede en wildcard-challenge mens den står i
            -- avklaring_kreves, og avgjor_domeneovertakelse godkjenne den utvidede
            -- scopen UTEN at den nye wildcard-challengen noen gang ble verifisert.
            -- En scope-oppgradering krever en ny, verifisert wildcard-challenge.
            wildcard = CASE WHEN public.domenekontroll.status
                                 IN ('verifisert','avklaring_kreves')
                            THEN public.domenekontroll.wildcard
                            ELSE p_wildcard END;   -- status uendret
    INSERT INTO public.domenekontroll_hendelse
        (tenant, hostname, hendelse, aktor) VALUES
        (p_tenant, p_hostname, 'challenge_utstedt', p_aktor);
END $$;

-- Verifiser (DNS er alt bekreftet av kalleren). Advisory-lås på hostname FØRST;
-- hostname_binding (global) er autoriteten. B4-takeover: holder en ANNEN tenant
-- aktiv verifisering, revokes den (grunn overtatt_dns_kontroll) og DENNE settes
-- avklaring_kreves — funksjonen returnerer 'konflikt:<a-tenant>' så Python lager
-- den idempotente M-37-saken. Ellers: verifisert + 90-døgnsvindu. generasjon++.
-- Står KALLEREN selv i avklaring, returneres 'avklaring_kreves' uten å røre noe.
CREATE OR REPLACE FUNCTION verifiser_domenekontroll(
    p_tenant   TEXT,
    p_hostname TEXT,
    p_wildcard BOOLEAN,
    p_aktor    TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_eier TEXT; v_status_a TEXT; v_utloper_a TIMESTAMPTZ; v_status_b TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('domene:' || p_hostname, 0));
    -- Codex: `avklaring_kreves` er TERMINAL FOR DENNE VEIEN. Etter at B har
    -- overtatt en aktiv verifisering står B i avklaring med bindingen på seg —
    -- et RETRY av samme verifisering ville da hoppet over overtakelsesgrenen
    -- (eieren er jo B selv) og falt ned i upserten nedenfor, som satte B rett
    -- til `verifisert` og dermed omgikk hele M-37-avgjørelsen. Kun
    -- `avgjor_domeneovertakelse` kan løfte B ut av avklaring.
    SELECT status INTO v_status_b FROM public.domenekontroll
     WHERE tenant = p_tenant AND hostname = p_hostname FOR UPDATE;
    IF v_status_b = 'avklaring_kreves' THEN
        INSERT INTO public.domenekontroll_hendelse
            (tenant, hostname, hendelse, fra_status, til_status, grunn, aktor)
            VALUES (p_tenant, p_hostname, 'verifisering_blokkert',
                    'avklaring_kreves', 'avklaring_kreves',
                    'avventer_overtakelsesavgjorelse', p_aktor);
        RETURN 'avklaring_kreves';
    END IF;
    SELECT tenant INTO v_eier FROM public.hostname_binding
     WHERE hostname = p_hostname;
    IF v_eier IS NOT NULL AND v_eier IS DISTINCT FROM p_tenant THEN
        SELECT status, utloper INTO v_status_a, v_utloper_a
          FROM public.domenekontroll
         WHERE tenant = v_eier AND hostname = p_hostname;    -- BYPASSRLS
        IF v_status_a = 'verifisert' AND now() < v_utloper_a THEN
            -- B4 rad 1: overtakelse fjerner A, men gir den ikke bort til B.
            UPDATE public.domenekontroll
               SET status = 'tilbakekalt',
                   autorisasjonsgenerasjon = autorisasjonsgenerasjon + 1
             WHERE tenant = v_eier AND hostname = p_hostname;
            INSERT INTO public.domenekontroll_hendelse
                (tenant, hostname, hendelse, fra_status, til_status,
                 grunn, aktor) VALUES
                (v_eier, p_hostname, 'overtatt', 'verifisert', 'tilbakekalt',
                 'overtatt_dns_kontroll', p_aktor);
            INSERT INTO public.domenekontroll (tenant, hostname, status, wildcard,
                autorisasjonsgenerasjon)
                VALUES (p_tenant, p_hostname, 'avklaring_kreves', p_wildcard, 1)
            ON CONFLICT (tenant, hostname) DO UPDATE
                SET status = 'avklaring_kreves',
                    -- Codex P2: bær den NETTOPP verifiserte wildcard-scopen inn i
                    -- avklaringsraden. Ellers kunne en tenant med en gammel
                    -- wildcard-rad fullføre en eksakt-host-overtakelse og etter
                    -- M-37-godkjenning bli verifisert med den gamle scopen.
                    wildcard = p_wildcard,
                    autorisasjonsgenerasjon = public.domenekontroll.autorisasjonsgenerasjon + 1;
            INSERT INTO public.domenekontroll_hendelse
                (tenant, hostname, hendelse, til_status, grunn, aktor) VALUES
                (p_tenant, p_hostname, 'avklaring_kreves', 'avklaring_kreves',
                 'overtatt_dns_kontroll', p_aktor);
            INSERT INTO public.hostname_binding (hostname, tenant)
                VALUES (p_hostname, p_tenant)
            ON CONFLICT (hostname) DO UPDATE SET tenant = p_tenant, bundet_ts = now();
            RETURN 'konflikt:' || v_eier;
        ELSIF v_status_a = 'avklaring_kreves' THEN
            -- Codex: hostnavnet er under AKTIV M-37-avklaring (bindingseieren
            -- v_eier avventer avgjørelse). En TREDJE tenant som verifiserer er en
            -- ny konfliktpart — den går OGSÅ i avklaring_kreves, ALDRI direkte
            -- verifisert. Uten denne grenen falt et ANNET tenant-forsøk gjennom
            -- til direkte-verifisering, så en DNS-kontrollør kunne omgå M-37 ved å
            -- forsøke overtakelsen to ganger under ulike tenanter. Kun
            -- avgjor_domeneovertakelse løfter noen ut av avklaring.
            INSERT INTO public.domenekontroll (tenant, hostname, status, wildcard,
                autorisasjonsgenerasjon)
                VALUES (p_tenant, p_hostname, 'avklaring_kreves', p_wildcard, 1)
            ON CONFLICT (tenant, hostname) DO UPDATE
                SET status = 'avklaring_kreves', wildcard = p_wildcard,
                    autorisasjonsgenerasjon = public.domenekontroll.autorisasjonsgenerasjon + 1;
            INSERT INTO public.domenekontroll_hendelse
                (tenant, hostname, hendelse, til_status, grunn, aktor) VALUES
                (p_tenant, p_hostname, 'avklaring_kreves', 'avklaring_kreves',
                 'samtidig_overtakelseskonflikt', p_aktor);
            INSERT INTO public.hostname_binding (hostname, tenant)
                VALUES (p_hostname, p_tenant)
            ON CONFLICT (hostname) DO UPDATE SET tenant = p_tenant, bundet_ts = now();
            RETURN 'konflikt:' || v_eier;
        ELSIF v_status_a = 'verifisert' THEN
            -- Codex: A er verifisert men UTLØPT. Delindeksen en_verifisert_per_
            -- hostname predikerer kun på status, så A-raden blokkerer B med unique
            -- violation. Sett A → utlopt (+gen++) FØR B verifiseres, ellers nås
            -- den dokumenterte direkte-overføringen (B4 rad 2) aldri ved naturlig
            -- utløp.
            UPDATE public.domenekontroll
               SET status = 'utlopt',
                   autorisasjonsgenerasjon = autorisasjonsgenerasjon + 1
             WHERE tenant = v_eier AND hostname = p_hostname;
            INSERT INTO public.domenekontroll_hendelse
                (tenant, hostname, hendelse, fra_status, til_status, grunn, aktor)
                VALUES (v_eier, p_hostname, 'utlopt', 'verifisert', 'utlopt',
                        'utlopt_ved_overforing', p_aktor);
        END IF;
        -- A er utlopt/tilbakekalt → B kan verifiseres direkte (B4 rad 2).
    END IF;
    INSERT INTO public.domenekontroll (tenant, hostname, status, wildcard,
        autorisasjonsgenerasjon, verifisert_ts, siste_vellykkede_revalidering,
        utloper)
        VALUES (p_tenant, p_hostname, 'verifisert', p_wildcard, 1, now(), now(),
                now() + interval '90 days')
    ON CONFLICT (tenant, hostname) DO UPDATE
        SET status = 'verifisert', wildcard = p_wildcard,
            autorisasjonsgenerasjon = public.domenekontroll.autorisasjonsgenerasjon + 1,
            verifisert_ts = now(), siste_vellykkede_revalidering = now(),
            utloper = now() + interval '90 days';
    INSERT INTO public.domenekontroll_hendelse
        (tenant, hostname, hendelse, til_status, aktor) VALUES
        (p_tenant, p_hostname, 'verifisert', 'verifisert', p_aktor);
    INSERT INTO public.hostname_binding (hostname, tenant)
        VALUES (p_hostname, p_tenant)
    ON CONFLICT (hostname) DO UPDATE SET tenant = p_tenant, bundet_ts = now();
    RETURN 'verifisert';
END $$;

-- Daglig revalidering. Setter KUN siste_vellykkede_revalidering; endrer aldri
-- status alene (ferskheten i v_domeneautorisasjon gjør jobben).
CREATE OR REPLACE FUNCTION revalider_domenekontroll(
    p_tenant TEXT, p_hostname TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    UPDATE public.domenekontroll SET siste_vellykkede_revalidering = now()
     WHERE tenant = p_tenant AND hostname = p_hostname AND status = 'verifisert';
    -- Codex: KUN registrer revisjonshendelsen når NØYAKTIG én verifisert rad
    -- faktisk ble oppdatert. Racet en planlagt revalidering med en tilbakekalling/
    -- overtakelse (eller ble kalt for et ukjent/ikke-verifisert hostnavn), traff
    -- UPDATE-en null rader — men append-only-loggen påsto likevel en vellykket
    -- revalidering ETTER at autorisasjonen var trukket, uten noen tilsvarende
    -- endring i siste_vellykkede_revalidering.
    IF NOT FOUND THEN
        RAISE EXCEPTION 'revalider_domenekontroll: %/% er ikke verifisert '
            '(ingen revalidering registrert)', p_tenant, p_hostname
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.domenekontroll_hendelse
        (tenant, hostname, hendelse, aktor) VALUES
        (p_tenant, p_hostname, 'revalidert', p_aktor);
END $$;

-- Umiddelbar tilbakekalling, auditert grunn, generasjon++.
CREATE OR REPLACE FUNCTION tilbakekall_domenekontroll(
    p_tenant TEXT, p_hostname TEXT, p_grunn TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_status TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('domene:' || p_hostname, 0));
    SELECT status INTO v_status FROM public.domenekontroll
     WHERE tenant = p_tenant AND hostname = p_hostname FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'tilbakekall: ukjent domenekontroll %/%', p_tenant, p_hostname
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_status = 'tilbakekalt' THEN RETURN; END IF;   -- idempotent
    UPDATE public.domenekontroll
       SET status = 'tilbakekalt',
           autorisasjonsgenerasjon = autorisasjonsgenerasjon + 1
     WHERE tenant = p_tenant AND hostname = p_hostname;
    INSERT INTO public.domenekontroll_hendelse
        (tenant, hostname, hendelse, fra_status, til_status, grunn, aktor) VALUES
        (p_tenant, p_hostname, 'tilbakekalt', v_status, 'tilbakekalt', p_grunn, p_aktor);
END $$;

-- Avgjør en overtakelse (fra en avgjort M-37-attestasjon). godkjent → B
-- verifisert m/ nytt 90-døgnsvindu; avvist → B tilbakekalt. Ingen knapp skriver
-- status direkte. Avgjørelsen er GJERDET av overtakelsesgenerasjonen saken ble
-- opprettet for (`p_forventet_generasjon`).
--
-- Den gamle 4-argumentsformen droppes: en overload uten gjerde ville vært en
-- åpen sidedør inn til nøyaktig den overgangen gjerdet skal beskytte.
DROP FUNCTION IF EXISTS avgjor_domeneovertakelse(TEXT, TEXT, BOOLEAN, TEXT);
CREATE OR REPLACE FUNCTION avgjor_domeneovertakelse(
    p_tenant TEXT, p_hostname TEXT, p_forventet_generasjon BIGINT,
    p_godkjent BOOLEAN, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_gen BIGINT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('domene:' || p_hostname, 0));
    SELECT status, autorisasjonsgenerasjon INTO v_status, v_gen
      FROM public.domenekontroll
     WHERE tenant = p_tenant AND hostname = p_hostname FOR UPDATE;
    IF v_status IS DISTINCT FROM 'avklaring_kreves' THEN
        RAISE EXCEPTION 'avgjor_domeneovertakelse: %/% er % (krever avklaring_kreves)',
            p_tenant, p_hostname, v_status USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Codex: M-37-saken er nøklet på overtakelsesgenerasjonen, men avgjørelsen
    -- sjekket bare at raden STO i avklaring. Ble en gammel sak liggende mens en
    -- tidligere overtakelse ble avvist og en NY overtakelse satte samme
    -- tenant/hostname tilbake i avklaring, autoriserte den foreldede
    -- godkjenningen den nye generasjonen. Generasjonen leses under RADLÅSEN og
    -- må stemme nøyaktig.
    IF v_gen IS DISTINCT FROM p_forventet_generasjon THEN
        RAISE EXCEPTION 'avgjor_domeneovertakelse: foreldet sak for %/% '
            '(generasjon % <> %)', p_tenant, p_hostname, p_forventet_generasjon,
            v_gen USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_godkjent THEN
        UPDATE public.domenekontroll
           SET status = 'verifisert',
               autorisasjonsgenerasjon = autorisasjonsgenerasjon + 1,
               verifisert_ts = now(), siste_vellykkede_revalidering = now(),
               utloper = now() + interval '90 days'
         WHERE tenant = p_tenant AND hostname = p_hostname;
        INSERT INTO public.hostname_binding (hostname, tenant)
            VALUES (p_hostname, p_tenant)
        ON CONFLICT (hostname) DO UPDATE SET tenant = p_tenant, bundet_ts = now();
        INSERT INTO public.domenekontroll_hendelse
            (tenant, hostname, hendelse, fra_status, til_status, aktor) VALUES
            (p_tenant, p_hostname, 'avklaring_godkjent', 'avklaring_kreves',
             'verifisert', p_aktor);
    ELSE
        UPDATE public.domenekontroll
           SET status = 'tilbakekalt',
               autorisasjonsgenerasjon = autorisasjonsgenerasjon + 1
         WHERE tenant = p_tenant AND hostname = p_hostname;
        INSERT INTO public.domenekontroll_hendelse
            (tenant, hostname, hendelse, fra_status, til_status, aktor) VALUES
            (p_tenant, p_hostname, 'avklaring_avvist', 'avklaring_kreves',
             'tilbakekalt', p_aktor);
    END IF;
END $$;

-- Registrer en artefakttype (FK mot modulkontrakt). skjema_hash er immutable:
-- samme innhold = no-op, avvik = avvist.
CREATE OR REPLACE FUNCTION registrer_artefakttype(
    p_artefakttype TEXT, p_eiermodul TEXT, p_kontraktversjon INT,
    p_kontrakt_hash TEXT, p_skjema_hash TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE r RECORD;
BEGIN
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
    INSERT INTO public.artefakttype_register
        (artefakttype, eiermodul, kontraktversjon, kontrakt_hash, skjema_hash)
        VALUES (p_artefakttype, p_eiermodul, p_kontraktversjon, p_kontrakt_hash,
                p_skjema_hash);   -- FK → modulkontrakt
END $$;

-- Lagre staged artefakt. Kalleren har JCS-kanonisert + sha256-et + kryptert med
-- tenant-DEK (server-side). Idempotent på (kapabilitet_jti, klartekst_sha256):
-- samme kanoniske dokument → samme artefakt_id; samme jti + ANNEN hash →
-- motstridende evidens (sikkerhetssak). synchronous_commit=on (varig før svar).
CREATE OR REPLACE FUNCTION lagre_artefakt_staged(
    p_tenant TEXT, p_oppdrag_id BIGINT, p_artefakttype TEXT, p_modul_id TEXT,
    p_release_id TEXT, p_kontraktversjon INT, p_kontrakt_hash TEXT,
    p_module_epoch BIGINT, p_storrelse INT, p_klartekst_sha256 TEXT,
    p_ciphertext BYTEA, p_nonce BYTEA, p_dek_ref TEXT, p_kapabilitet_jti TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_id UUID; v_hash TEXT;
BEGIN
    SET LOCAL synchronous_commit = on;
    SELECT artefakt_id, klartekst_sha256 INTO v_id, v_hash FROM public.artefakt
     WHERE kapabilitet_jti = p_kapabilitet_jti;
    IF FOUND THEN
        IF v_hash IS DISTINCT FROM p_klartekst_sha256 THEN
            RAISE EXCEPTION 'artefakt-kapabilitet % gjenbrukt for ANNET dokument',
                p_kapabilitet_jti USING ERRCODE = 'unique_violation';
        END IF;
        RETURN v_id;   -- idempotent
    END IF;
    INSERT INTO public.artefakt (tenant, oppdrag_id, artefakttype, modul_id,
        release_id, kontraktversjon, kontrakt_hash, module_epoch, tilstand,
        storrelse_bytes, klartekst_sha256, ciphertext, nonce, dek_ref,
        kapabilitet_jti)
        VALUES (p_tenant, p_oppdrag_id, p_artefakttype, p_modul_id, p_release_id,
                p_kontraktversjon, p_kontrakt_hash, p_module_epoch, 'staged',
                p_storrelse, p_klartekst_sha256, p_ciphertext, p_nonce, p_dek_ref,
                p_kapabilitet_jti)
        RETURNING artefakt_id INTO v_id;
    RETURN v_id;
END $$;

-- Promoter i SAMME transaksjon som statusovergangen. Verifiserer tilstand ·
-- tenant · oppdrag · release · epoch · hash. Epoch-avvik → RAISE (Python
-- karantenesetter kvitteringen; artefaktet bevares av opprydding).
CREATE OR REPLACE FUNCTION promoter_artefakt(
    p_artefakt_id UUID, p_tenant TEXT, p_oppdrag_id BIGINT, p_release_id TEXT,
    p_module_epoch BIGINT, p_klartekst_sha256 TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE r RECORD;
BEGIN
    SELECT tenant, oppdrag_id, release_id, module_epoch, klartekst_sha256, tilstand
      INTO r FROM public.artefakt WHERE artefakt_id = p_artefakt_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'promoter_artefakt: ukjent artefakt %', p_artefakt_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF r.tilstand = 'promotert' THEN RETURN; END IF;   -- idempotent
    IF r.tenant IS DISTINCT FROM p_tenant
       OR r.oppdrag_id IS DISTINCT FROM p_oppdrag_id
       OR r.release_id IS DISTINCT FROM p_release_id
       OR r.klartekst_sha256 IS DISTINCT FROM p_klartekst_sha256 THEN
        RAISE EXCEPTION 'promoter_artefakt: binding/hash-avvik'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF r.module_epoch IS DISTINCT FROM p_module_epoch THEN
        RAISE EXCEPTION 'promoter_artefakt: epoch-avvik (% <> %)',
            r.module_epoch, p_module_epoch USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF r.tilstand <> 'staged' THEN
        RAISE EXCEPTION 'promoter_artefakt: % kan ikke promoteres', r.tilstand
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.artefakt SET tilstand = 'promotert', promotert_ts = now()
     WHERE artefakt_id = p_artefakt_id;   -- ett_promotert_per_oppdrag vokter
END $$;

-- Rydd staged > 24 t (positiv regel). Refererende-kvittering-vernet kobles i
-- artefakt-API-checkpointet; her forkastes staged > 24 t og ciphertext nulles.
-- Idempotent (forkastet er terminal).
CREATE OR REPLACE FUNCTION rydd_staged_artefakter()
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_antall INT;
BEGIN
    WITH forkastet AS (
        UPDATE public.artefakt SET tilstand = 'forkastet', ciphertext = NULL, nonce = NULL
         WHERE tilstand = 'staged' AND opprettet < now() - interval '24 hours'
        RETURNING 1)
    SELECT count(*) INTO v_antall FROM forkastet;
    RETURN v_antall;
END $$;

-- Codex §7 pkt. 8: et artefakt hvis kvittering ikke lot seg verifisere
-- (epoch/binding) karantenesettes → RETAINED (rydd rører kun 'staged'). Kun for
-- et staged artefakt bundet til (tenant, oppdrag); idempotent hvis alt karantene.
CREATE OR REPLACE FUNCTION karantenesett_artefakt(
    p_artefakt_id UUID, p_tenant TEXT, p_oppdrag_id BIGINT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    UPDATE public.artefakt SET tilstand = 'karantene'
     WHERE artefakt_id = p_artefakt_id AND tenant = p_tenant
       AND oppdrag_id = p_oppdrag_id AND tilstand = 'staged';
END $$;

-- Codex: en GODTATT SEN kvittering (etter utførelsesfrist / fra en foreldet
-- eier) lagres som evidens uten å avslutte noe — men artefaktet den peker på
-- ble aldri promotert, så oppryddingen nullet ciphertexten etter 24 t og
-- ødela nettopp den evidensen kvitteringen ble godtatt for. `bevart` er
-- RETAINED og terminalt: artefaktet kan aldri promoteres senere, og ryddes
-- aldri. Samme form som karantenesett: no-op for et fremmed eller alt
-- terminalt artefakt (det hører til sitt eget oppdrag / sin egen avgjørelse).
CREATE OR REPLACE FUNCTION bevar_artefakt(
    p_artefakt_id UUID, p_tenant TEXT, p_oppdrag_id BIGINT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    UPDATE public.artefakt SET tilstand = 'bevart'
     WHERE artefakt_id = p_artefakt_id AND tenant = p_tenant
       AND oppdrag_id = p_oppdrag_id AND tilstand = 'staged';
END $$;

REVOKE ALL ON FUNCTION utsted_challenge(TEXT, TEXT, BOOLEAN, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION verifiser_domenekontroll(TEXT, TEXT, BOOLEAN, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION revalider_domenekontroll(TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION tilbakekall_domenekontroll(TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION avgjor_domeneovertakelse(TEXT, TEXT, BIGINT, BOOLEAN, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION registrer_artefakttype(TEXT, TEXT, INT, TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION lagre_artefakt_staged(TEXT, BIGINT, TEXT, TEXT, TEXT, INT, TEXT, BIGINT, INT, TEXT, BYTEA, BYTEA, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION promoter_artefakt(UUID, TEXT, BIGINT, TEXT, BIGINT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION rydd_staged_artefakter() FROM PUBLIC;
-- Domenefunksjonene er admin-/ops-handlinger; artefakt-lagring/-promotering er
-- runtime (API-et). rydd + typeregistrering er admin.
GRANT EXECUTE ON FUNCTION utsted_challenge(TEXT, TEXT, BOOLEAN, TEXT, TEXT) TO disponit_domains_admin;
GRANT EXECUTE ON FUNCTION verifiser_domenekontroll(TEXT, TEXT, BOOLEAN, TEXT) TO disponit_domains_admin;
GRANT EXECUTE ON FUNCTION revalider_domenekontroll(TEXT, TEXT, TEXT) TO disponit_domains_admin;
GRANT EXECUTE ON FUNCTION tilbakekall_domenekontroll(TEXT, TEXT, TEXT, TEXT) TO disponit_domains_admin;
GRANT EXECUTE ON FUNCTION avgjor_domeneovertakelse(TEXT, TEXT, BIGINT, BOOLEAN, TEXT) TO disponit_domains_admin;
GRANT EXECUTE ON FUNCTION registrer_artefakttype(TEXT, TEXT, INT, TEXT, TEXT, TEXT) TO disponit_domains_admin;
GRANT EXECUTE ON FUNCTION rydd_staged_artefakter() TO disponit_domains_admin;
GRANT EXECUTE ON FUNCTION lagre_artefakt_staged(TEXT, BIGINT, TEXT, TEXT, TEXT, INT, TEXT, BIGINT, INT, TEXT, BYTEA, BYTEA, TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION promoter_artefakt(UUID, TEXT, BIGINT, TEXT, BIGINT, TEXT, TEXT) TO disponit;
REVOKE ALL ON FUNCTION karantenesett_artefakt(UUID, TEXT, BIGINT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION karantenesett_artefakt(UUID, TEXT, BIGINT) TO disponit;
REVOKE ALL ON FUNCTION bevar_artefakt(UUID, TEXT, BIGINT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bevar_artefakt(UUID, TEXT, BIGINT) TO disponit;
RESET ROLE;

-- domene_eier (SECURITY DEFINER-kjøreren) må kunne skrive tabellene + LESE
-- oppdrag (promoter verifiserer epoch/oppdrag; BYPASSRLS dekker RLS). Grantene
-- bor HER sammen med funksjonene (014a-lærdom).
GRANT SELECT, INSERT, UPDATE ON domenekontroll          TO disponit_domene_eier;
GRANT SELECT, INSERT         ON domenekontroll_hendelse  TO disponit_domene_eier;
GRANT SELECT, INSERT, UPDATE ON hostname_binding         TO disponit_domene_eier;
GRANT SELECT, INSERT         ON artefakttype_register     TO disponit_domene_eier;
GRANT SELECT, INSERT, UPDATE ON artefakt                 TO disponit_domene_eier;
GRANT SELECT                 ON oppdrag                   TO disponit_domene_eier;
-- Sekvensen bak `domenekontroll_hendelse.id`. En IDENTITY-kolonne henter
-- verdien via NextValueExpr, som IKKE ACL-sjekker sekvensen (derfor er suiten
-- grønn i dag) — men INSERT-veien til hendelsesloggen er den ene tingen som
-- ikke får feile: mister den nextval, ruller HELE domeneoperasjonen tilbake.
-- Granten koster ingenting og holder også om kolonnen en dag blir en vanlig
-- sekvenskolonne (der USAGE er et absolutt krav).
DO $$
DECLARE v_seq TEXT := pg_get_serial_sequence('public.domenekontroll_hendelse', 'id');
BEGIN
    IF v_seq IS NOT NULL THEN
        EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO disponit_domene_eier',
                       v_seq);
    END IF;
END $$;
