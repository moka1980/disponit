-- ============================================================
-- 040 — OVERTAKELSESSAKENS KALLER (Arbeid B, klarsignal 2026-08-18)
--
-- `verifiser_domenekontroll()` (016) har gjort B4-overtakelsen i basen og
-- returnert `konflikt:<tapt-tenant>` — men sak-skaperen i Python fikk
-- aldri en produksjonskaller. Med selvbetjent verifisering (039) kan en
-- kunde utløse konflikten og bli stående i `avklaring_kreves` for
-- alltid. Regelen herfra: EN TILSTAND SOM KREVER MENNESKELIG AVGJØRELSE
-- SKAPES SAMMEN MED SAKEN SOM GJØR AVGJØRELSEN MULIG, I ÉN TRANSAKSJON
-- (§2) — håndhevet maskinelt av constraint-triggeren i §3.7.
--
-- DOKUMENTERTE AVVIK fra klarsignalets DDL (samme disiplin som 038s
-- UUID→BIGINT — radens faktiske form vinner, intensjonen beholdes):
--   * `revisjonslogg` HAR INGEN payload_kryptert/key_id/nonce-kolonner
--     (verifisert mot levende base). §3.4s «DROP NOT NULL på trioen» på
--     den tabellen bortfaller; payload_type/referansepayload legges til
--     med konsistens-CHECK i to-tilstandsformen uten trio-grener.
--   * `er_gyldig_hostname_a_label` finnes alt som `er_kanonisk_hostname`
--     (016, IMMUTABLE, brukt av §0-gjerdet) — gren 1 i §5.2: GJENBRUK,
--     ingen ny definisjon. Kontrakten er identisk pluss et strengere
--     ledd (avviser numerisk TLD) — strengere er lov, løsere er det ikke.
--   * `hendelse_a`/`hendelse_b`-semantikk per konfliktgren (klarsignalet
--     binder bare formen): hendelse_a = den TAPENDE/BLOKKERENDE partens
--     hendelse i konflikten, hendelse_b = utfordrerens. I grener der
--     motparten ikke får noen ny hendelse (tredje tenant, reapplikasjon)
--     pekes hendelse_a på motpartens SISTE hendelse for hostnamet — de
--     to radene som faktisk utgjør konflikten, aldri fabrikkerte.
-- ============================================================

-- ------------------------------------------------------------
-- 1. `sakskilde` med eksplisitt backfill — ingen DEFAULT noe sted
-- ------------------------------------------------------------
ALTER TABLE unntak ADD COLUMN IF NOT EXISTS sakskilde TEXT;

UPDATE unntak SET sakskilde = 'oppdrag'
 WHERE sakskilde IS NULL AND oppdrag_id IS NOT NULL;
UPDATE unntak SET sakskilde = 'policybrudd'
 WHERE sakskilde IS NULL AND oppdrag_id IS NULL;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM unntak WHERE sakskilde IS NULL) THEN
    RAISE EXCEPTION 'backfill ufullstendig: % rader uten sakskilde',
      (SELECT count(*) FROM unntak WHERE sakskilde IS NULL);
  END IF;
END $$;

ALTER TABLE unntak ALTER COLUMN sakskilde SET NOT NULL;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'unntak_sakskilde_verdi') THEN
    ALTER TABLE unntak ADD CONSTRAINT unntak_sakskilde_verdi
      CHECK (sakskilde IN ('policybrudd','oppdrag','domeneovertakelse'));
  END IF;
END $$;

CREATE OR REPLACE FUNCTION unntak_sakskilde_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.sakskilde IS DISTINCT FROM OLD.sakskilde THEN
        RAISE EXCEPTION 'unntak.sakskilde er immutable';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS unntak_sakskilde_laas ON unntak;
CREATE TRIGGER unntak_sakskilde_laas BEFORE UPDATE ON unntak
    FOR EACH ROW EXECUTE FUNCTION unntak_sakskilde_immutable();

-- ------------------------------------------------------------
-- 2. Saksfelt, lineage og uttømmende CHECK
-- ------------------------------------------------------------
ALTER TABLE unntak
  ADD COLUMN IF NOT EXISTS hostname_ref TEXT,
  ADD COLUMN IF NOT EXISTS utfordrer_tenant TEXT,
  ADD COLUMN IF NOT EXISTS tapt_tenant TEXT,
  ADD COLUMN IF NOT EXISTS autorisasjonsgenerasjon BIGINT,
  ADD COLUMN IF NOT EXISTS saksrevisjon BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS hendelse_a BIGINT,
  ADD COLUMN IF NOT EXISTS hendelse_b BIGINT;

ALTER TABLE unntak DROP CONSTRAINT IF EXISTS unntak_oppdragssak_komplett;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'unntak_sakskilde_komplett') THEN
    -- Validert mot HELE tabellen (ikke NOT VALID): migrasjonen er selv
    -- beviset på at backfillen var riktig.
    ALTER TABLE unntak ADD CONSTRAINT unntak_sakskilde_komplett CHECK (
      (sakskilde = 'policybrudd'
         AND oppdrag_id IS NULL AND arsak IS NULL AND hostname_ref IS NULL
         AND utfordrer_tenant IS NULL AND tapt_tenant IS NULL
         AND autorisasjonsgenerasjon IS NULL
         AND hendelse_a IS NULL AND hendelse_b IS NULL)
      OR (sakskilde = 'oppdrag'
         AND oppdrag_id IS NOT NULL AND arsak IS NOT NULL
         AND hostname_ref IS NULL
         AND utfordrer_tenant IS NULL AND tapt_tenant IS NULL
         AND autorisasjonsgenerasjon IS NULL
         AND hendelse_a IS NULL AND hendelse_b IS NULL)
      OR (sakskilde = 'domeneovertakelse'
         AND oppdrag_id IS NULL AND arsak IS NULL
         AND hostname_ref            IS NOT NULL
         AND utfordrer_tenant        IS NOT NULL
         AND tapt_tenant             IS NOT NULL
         AND autorisasjonsgenerasjon IS NOT NULL
         AND hendelse_a IS NOT NULL AND hendelse_b IS NOT NULL));
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS en_apen_overtakelsessak_per_hostname
  ON unntak (hostname_ref)
  WHERE sakskilde = 'domeneovertakelse' AND NOT terminal;

-- ------------------------------------------------------------
-- 3. Lineage bundet til riktig hendelse OG riktig hostname
--    (kolonnenavnene `id`/`hostname` verifisert mot levende base)
-- ------------------------------------------------------------
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'domenekontroll_hendelse_identitet') THEN
    ALTER TABLE domenekontroll_hendelse
      ADD CONSTRAINT domenekontroll_hendelse_identitet UNIQUE (id, hostname);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'unntak_hendelse_a_hostname') THEN
    ALTER TABLE unntak
      ADD CONSTRAINT unntak_hendelse_a_hostname
        FOREIGN KEY (hendelse_a, hostname_ref)
        REFERENCES domenekontroll_hendelse (id, hostname),
      ADD CONSTRAINT unntak_hendelse_b_hostname
        FOREIGN KEY (hendelse_b, hostname_ref)
        REFERENCES domenekontroll_hendelse (id, hostname),
      ADD CONSTRAINT unntak_hendelser_ulike
        CHECK (hendelse_a IS NULL OR hendelse_a <> hendelse_b);
  END IF;
END $$;

-- ------------------------------------------------------------
-- 4. Referansepayload — lukket, versjonert, semantisk validert
-- ------------------------------------------------------------
ALTER TABLE unntak
  ADD COLUMN IF NOT EXISTS payload_type TEXT NOT NULL DEFAULT 'kryptert'
    CHECK (payload_type IN ('kryptert','referanse')),
  ADD COLUMN IF NOT EXISTS referansepayload JSONB;
-- DOKUMENTERT AVVIK fra §3.4s «DROP DEFAULT»: defaulten 'kryptert' BLIR
-- STÅENDE. Ingen port måler defaultløshet for payload_type (i motsetning
-- til sakskilde/port 12), 'kryptert' ER den eneste riktige verdien for
-- enhver eksisterende skriver, og referanse-tilstanden kan uansett ikke
-- nås ved uteglemmelse: konsistens-CHECKen krever referansepayload OG
-- forbyr ciphertext samtidig. En defaultløs kolonne hadde tvunget
-- æra-bevisste endringer inn i hver eneste rebuild-fixture uten å stenge
-- noen faktisk feilvei.
ALTER TABLE unntak
  ALTER COLUMN payload_kryptert DROP NOT NULL,
  ALTER COLUMN key_id           DROP NOT NULL,
  ALTER COLUMN nonce            DROP NOT NULL;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'unntak_payload_konsistent') THEN
    ALTER TABLE unntak ADD CONSTRAINT unntak_payload_konsistent CHECK (
      (payload_type = 'kryptert'
         AND payload_kryptert IS NOT NULL AND key_id IS NOT NULL
         AND nonce IS NOT NULL AND referansepayload IS NULL)
      OR
      (payload_type = 'referanse'
         AND payload_kryptert IS NULL AND key_id IS NULL
         AND nonce IS NULL AND referansepayload IS NOT NULL));
  END IF;
END $$;

-- Snapshot-trioen: NULL for domeneovertakelse — det fantes ingen
-- policybeslutning å snapshotte; 'ukjent' ville påstått at det gjorde det.
ALTER TABLE unntak
  ALTER COLUMN maks_auto_forsok_snapshot DROP NOT NULL,
  ALTER COLUMN policy_versjon            DROP NOT NULL,
  ALTER COLUMN policy_content_hash       DROP NOT NULL;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'unntak_snapshot_komplett') THEN
    ALTER TABLE unntak ADD CONSTRAINT unntak_snapshot_komplett CHECK (
      (sakskilde = 'domeneovertakelse'
         AND maks_auto_forsok_snapshot IS NULL
         AND policy_versjon IS NULL AND policy_content_hash IS NULL)
      OR (sakskilde <> 'domeneovertakelse'
         AND maks_auto_forsok_snapshot IS NOT NULL
         AND policy_versjon IS NOT NULL
         AND policy_content_hash IS NOT NULL));
  END IF;
END $$;

-- revisjonslogg: DOKUMENTERT AVVIK — tabellen har ingen kryptert-trio,
-- så konsistensformen er to-tilstands uten trio-grener. `kryptert`
-- betyr her «payloaden ligger der den alltid har ligget: utenfor
-- loggen»; `referanse` bærer den lukkede klartekst-referansen selv.
ALTER TABLE revisjonslogg
  ADD COLUMN IF NOT EXISTS payload_type TEXT NOT NULL DEFAULT 'kryptert'
    CHECK (payload_type IN ('kryptert','referanse')),
  ADD COLUMN IF NOT EXISTS referansepayload JSONB;
-- Samme dokumenterte avvik som på unntak: defaulten står.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'revisjonslogg_payload_konsistent') THEN
    ALTER TABLE revisjonslogg
      ADD CONSTRAINT revisjonslogg_payload_konsistent CHECK (
        (payload_type = 'kryptert'  AND referansepayload IS NULL)
        OR (payload_type = 'referanse' AND referansepayload IS NOT NULL));
  END IF;
END $$;

-- Formkontrakten: lukket nøkkelsett (LIKHET, ikke delmengde), versjonert,
-- semantiske domener validert på TEKSTFORM — ingen cast som kan kaste,
-- og COALESCE(..., false) ytterst: alltid TRUE/FALSE, aldri NULL.
CREATE OR REPLACE FUNCTION er_gyldig_referansepayload(p JSONB)
RETURNS boolean LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, public AS $$
  SELECT COALESCE(
       jsonb_typeof(p) = 'object'
   AND p->>'v' = '1'
   AND p->>'familie' = 'domeneovertakelse'
   AND (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p) k)
       = ARRAY['autorisasjonsgenerasjon','familie','hendelse_a','hendelse_b',
               'hostname','tapt_tenant','utfordrer_tenant','v']
   AND jsonb_typeof(p->'autorisasjonsgenerasjon') = 'number'
   AND jsonb_typeof(p->'hendelse_a') = 'number'
   AND jsonb_typeof(p->'hendelse_b') = 'number'
   AND jsonb_typeof(p->'hostname')          = 'string'
   AND jsonb_typeof(p->'tapt_tenant')       = 'string'
   AND jsonb_typeof(p->'utfordrer_tenant')  = 'string'
   -- length <= 19 er det som faktisk holder tallet innenfor BIGINT:
   -- mønsteret alene godtar vilkårlig lange sifferstrenger.
   AND (p->>'autorisasjonsgenerasjon') ~ '^(0|[1-9][0-9]*)$'
   AND length(p->>'autorisasjonsgenerasjon') <= 19
   AND (p->>'hendelse_a') ~ '^[1-9][0-9]*$' AND length(p->>'hendelse_a') <= 19
   AND (p->>'hendelse_b') ~ '^[1-9][0-9]*$' AND length(p->>'hendelse_b') <= 19
   AND p->>'hendelse_a' <> p->>'hendelse_b'
   AND p->>'tapt_tenant' <> p->>'utfordrer_tenant'
   AND length(p->>'tapt_tenant') > 0
   AND length(p->>'utfordrer_tenant') > 0
   -- §5.2 gren 1: den EKSISTERENDE kanoniske kontrakten (016) gjenbrukes.
   AND public.er_kanonisk_hostname(p->>'hostname')
  , false)
$$;

-- Speilingen — total funksjon, cast går bigint→text (kanonisk, kaster aldri).
CREATE OR REPLACE FUNCTION referansepayload_speiler(
  p JSONB, p_hostname TEXT, p_generasjon BIGINT,
  p_utfordrer TEXT, p_tapt TEXT, p_a BIGINT, p_b BIGINT
) RETURNS boolean LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, public AS $$
  SELECT COALESCE(
       public.er_gyldig_referansepayload(p)
   AND p_hostname  IS NOT NULL AND p_generasjon IS NOT NULL
   AND p_utfordrer IS NOT NULL AND p_tapt       IS NOT NULL
   AND p_a         IS NOT NULL AND p_b          IS NOT NULL
   AND p->>'hostname'                = p_hostname
   AND p->>'autorisasjonsgenerasjon' = p_generasjon::text
   AND p->>'utfordrer_tenant'        = p_utfordrer
   AND p->>'tapt_tenant'             = p_tapt
   AND p->>'hendelse_a'              = p_a::text
   AND p->>'hendelse_b'              = p_b::text
  , false)
$$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'unntak_referansepayload_speiler') THEN
    ALTER TABLE unntak ADD CONSTRAINT unntak_referansepayload_speiler CHECK (
      payload_type <> 'referanse'
      OR public.referansepayload_speiler(referansepayload, hostname_ref,
           autorisasjonsgenerasjon, utfordrer_tenant, tapt_tenant,
           hendelse_a, hendelse_b));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'revisjonslogg_referansepayload_lukket') THEN
    ALTER TABLE revisjonslogg
      ADD CONSTRAINT revisjonslogg_referansepayload_lukket
      CHECK (payload_type <> 'referanse'
             OR public.er_gyldig_referansepayload(referansepayload));
  END IF;
END $$;

-- ------------------------------------------------------------
-- 5. Sak ↔ loggpost-binding (kryssbord → DEFERRED constraint trigger)
-- ------------------------------------------------------------
-- SECURITY DEFINER av samme grunn som domenekontroll_krev_sak under:
-- vakten leser loggposten uansett hvilken rolle som skrev saken.
CREATE OR REPLACE FUNCTION unntak_lineage_matcher_loggpost()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
  IF NEW.sakskilde = 'domeneovertakelse' THEN
    IF (SELECT referansepayload FROM public.revisjonslogg
         WHERE tenant = NEW.tenant AND id = NEW.loggpost_id)
       IS DISTINCT FROM NEW.referansepayload
    THEN RAISE EXCEPTION 'sakens lineage avviker fra loggpostens';
    END IF;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS unntak_lineage_speiler_loggpost ON unntak;
CREATE CONSTRAINT TRIGGER unntak_lineage_speiler_loggpost
  AFTER INSERT OR UPDATE ON unntak
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION unntak_lineage_matcher_loggpost();

-- ------------------------------------------------------------
-- 6. Revisjonsbindingen (B1a): skifte ⇒ nøyaktig +1; +1 kun ved skifte
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION overtakelsessak_revisjon_folger_skifte()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.sakskilde = 'domeneovertakelse' AND NOT OLD.terminal THEN
    IF (NEW.utfordrer_tenant        IS DISTINCT FROM OLD.utfordrer_tenant)
    OR (NEW.autorisasjonsgenerasjon IS DISTINCT FROM OLD.autorisasjonsgenerasjon)
    THEN
      IF NEW.saksrevisjon <> OLD.saksrevisjon + 1 THEN
        RAISE EXCEPTION 'utfordrer/generasjon endret uten saksrevisjon+1 (% -> %)',
          OLD.saksrevisjon, NEW.saksrevisjon;
      END IF;
    ELSIF NEW.saksrevisjon IS DISTINCT FROM OLD.saksrevisjon THEN
      RAISE EXCEPTION 'saksrevisjon endret uten utfordrer-/generasjonsskifte';
    END IF;
    IF NEW.hostname_ref IS DISTINCT FROM OLD.hostname_ref THEN
      RAISE EXCEPTION 'saksidentitet kan ikke endres';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant THEN
      RAISE EXCEPTION 'saken flyttes aldri mellom tenants';
    END IF;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS unntak_overtakelse_revisjonsbinding ON unntak;
CREATE TRIGGER unntak_overtakelse_revisjonsbinding
  BEFORE UPDATE ON unntak FOR EACH ROW
  EXECUTE FUNCTION overtakelsessak_revisjon_folger_skifte();

-- ------------------------------------------------------------
-- 7. `avklaring_kreves` uten gjeldende sak er ulovlig (invariant 10)
-- ------------------------------------------------------------
-- SECURITY DEFINER (migrator-eid): vakten må kunne LESE saken uansett
-- hvem som skrev domenekontroll-raden (domene_eier har ingen — og skal
-- ikke ha noen — SELECT på unntak). En vakt som bare virker for noen
-- skrivere er ingen vakt.
CREATE OR REPLACE FUNCTION domenekontroll_krev_sak()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
  IF NEW.status = 'avklaring_kreves' AND NOT EXISTS (
       SELECT 1 FROM public.unntak
        WHERE hostname_ref = NEW.hostname
          AND sakskilde = 'domeneovertakelse' AND NOT terminal
          AND utfordrer_tenant = NEW.tenant
          AND autorisasjonsgenerasjon = NEW.autorisasjonsgenerasjon)
  THEN RAISE EXCEPTION 'avklaring_kreves uten gjeldende sak';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS domenekontroll_avklaring_krever_sak ON domenekontroll;
CREATE CONSTRAINT TRIGGER domenekontroll_avklaring_krever_sak
  AFTER INSERT OR UPDATE OF status ON domenekontroll
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION domenekontroll_krev_sak();

-- ------------------------------------------------------------
-- 8. Reservert plattformtenant: aldri en kundetenant
-- ------------------------------------------------------------
-- Saken eies av `__plattform_domener`. Prefikset `__` reserveres for
-- plattformen på de tre veiene en KUNDE-tenant materialiserer seg:
-- medlemskap (browserøkter), API-tokener og policyer. RLS-synligheten
-- for kunder er uansett tenant-GUC-bundet; dette gjør det umulig å
-- OPPRETTE en kundeflate i det reserverte navnerommet (port 35).
CREATE OR REPLACE FUNCTION krev_ikke_reservert_tenant()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.tenant LIKE E'\\_\\_%' THEN
    RAISE EXCEPTION 'tenantprefikset __ er reservert for plattformen (%)',
      NEW.tenant;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS brukermedlemskap_ikke_reservert ON brukermedlemskap;
CREATE TRIGGER brukermedlemskap_ikke_reservert
  BEFORE INSERT ON brukermedlemskap
  FOR EACH ROW EXECUTE FUNCTION krev_ikke_reservert_tenant();
DROP TRIGGER IF EXISTS api_tokener_ikke_reservert ON api_tokener;
CREATE TRIGGER api_tokener_ikke_reservert
  BEFORE INSERT ON api_tokener
  FOR EACH ROW EXECUTE FUNCTION krev_ikke_reservert_tenant();
DROP TRIGGER IF EXISTS policyer_ikke_reservert ON policyer;
CREATE TRIGGER policyer_ikke_reservert
  BEFORE INSERT ON policyer
  FOR EACH ROW EXECUTE FUNCTION krev_ikke_reservert_tenant();

-- ------------------------------------------------------------
-- 9. Adjudikator-synligheten (RLS + minst mulig grant)
-- ------------------------------------------------------------
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles
              WHERE rolname = 'disponit_domains_adjudicator') THEN
    IF NOT EXISTS (SELECT 1 FROM pg_policy
                    WHERE polname = 'domeneovertakelse_adjudikator'
                      AND polrelid = 'unntak'::regclass) THEN
      CREATE POLICY domeneovertakelse_adjudikator ON unntak
        FOR SELECT USING (
          sakskilde = 'domeneovertakelse'
          AND CURRENT_USER = 'disponit_domains_adjudicator');
    END IF;
    GRANT SELECT ON unntak TO disponit_domains_adjudicator;
    -- Køen trenger hendelsene sakene peker på (lesing, samme snitt).
    GRANT SELECT ON domenekontroll_hendelse TO disponit_domains_adjudicator;
  END IF;
END $$;

-- ------------------------------------------------------------
-- 10. sikre_overtakelsessak() — claimer-eid, alt i én transaksjon
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_m37_claimer;

CREATE OR REPLACE FUNCTION sikre_overtakelsessak(
    p_hostname TEXT, p_generasjon BIGINT, p_tapt TEXT, p_utfordrer TEXT,
    p_hendelse_a BIGINT, p_hendelse_b BIGINT, p_aktor TEXT,
    p_request_id TEXT)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_payload JSONB; v_logg BIGINT; v_sak BIGINT;
        c_tenant CONSTANT TEXT := '__plattform_domener';
BEGIN
    -- Kalles alltid under hostname-låsen i verifiser_domenekontroll —
    -- én sak per hostname er dermed også serialisert her.
    PERFORM set_config('disponit.tenant', c_tenant, true);
    PERFORM set_config('disponit.aktor', p_aktor, true);
    PERFORM set_config('disponit.request_id', p_request_id, true);

    v_payload := jsonb_build_object(
        'v', '1', 'familie', 'domeneovertakelse',
        'hostname', p_hostname,
        'autorisasjonsgenerasjon', p_generasjon,
        'tapt_tenant', p_tapt, 'utfordrer_tenant', p_utfordrer,
        'hendelse_a', p_hendelse_a, 'hendelse_b', p_hendelse_b);

    -- Overgangen får sin egen loggpost (invariant 6): at en autorisasjon
    -- flytter seg mellom kunder SKAL stå i revisjonsloggen. UNNTAK er
    -- riktig beslutningsverdi — hendelsen føder en sak.
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling, request_id, payload_type, referansepayload)
    VALUES (c_tenant, p_aktor, 'domenekontroll',
            encode(sha256(convert_to(v_payload::text, 'UTF8')), 'hex'),
            'plattform:domenekontroll', 'UNNTAK',
            '["domeneovertakelse"]'::jsonb, 'domene.overtakelse',
            p_request_id, 'referanse', v_payload)
    RETURNING id INTO v_logg;

    SELECT id INTO v_sak FROM public.unntak
     WHERE hostname_ref = p_hostname
       AND sakskilde = 'domeneovertakelse' AND NOT terminal
       FOR UPDATE;
    IF FOUND THEN
        -- Åpen sak: A→B→C eller ny generasjon — samme sak, revisjon+1,
        -- ny loggpost/lineage. Triggeren i §6 håndhever +1-regelen.
        UPDATE public.unntak
           SET utfordrer_tenant = p_utfordrer,
               tapt_tenant = p_tapt,
               autorisasjonsgenerasjon = p_generasjon,
               hendelse_a = p_hendelse_a, hendelse_b = p_hendelse_b,
               saksrevisjon = saksrevisjon + 1,
               loggpost_id = v_logg,
               referansepayload = v_payload
         WHERE id = v_sak;
        INSERT INTO public.unntak_historikk (tenant, unntak_id, hendelse,
            aktor, request_id, detalj)
        VALUES (c_tenant, v_sak, 'overtakelsesskifte', p_aktor, p_request_id,
                jsonb_build_object('familie', 'domeneovertakelse',
                                   'utfordrer_skiftet_til_avtrykk',
                                   left(encode(sha256(convert_to(
                                       p_utfordrer, 'UTF8')), 'hex'), 16),
                                   'generasjon', p_generasjon));
        RETURN v_sak;
    END IF;

    INSERT INTO public.unntak (tenant, loggpost_id, handling, kategori,
        sakstype, prioritet, sakskilde, hostname_ref, utfordrer_tenant,
        tapt_tenant, autorisasjonsgenerasjon, saksrevisjon,
        hendelse_a, hendelse_b, payload_type, referansepayload,
        maks_auto_forsok_snapshot, policy_versjon, policy_content_hash)
    VALUES (c_tenant, v_logg, 'domene.overtakelse', 'domeneovertakelse',
            'sikkerhet', 'hoy', 'domeneovertakelse', p_hostname,
            p_utfordrer, p_tapt, p_generasjon, 0,
            p_hendelse_a, p_hendelse_b, 'referanse', v_payload,
            NULL, NULL, NULL)
    RETURNING id INTO v_sak;
    RETURN v_sak;
END $$;

REVOKE ALL ON FUNCTION sikre_overtakelsessak(TEXT, BIGINT, TEXT, TEXT,
    BIGINT, BIGINT, TEXT, TEXT) FROM PUBLIC;

RESET ROLE;
-- Loggposten er sikre_overtakelsessak sin å skrive (invariant 6-veien
-- for domenehendelser): claimer trenger INSERT — granten gis av
-- tabelleieren (migrator), utenfor SET ROLE-blokken.
GRANT INSERT ON revisjonslogg TO disponit_m37_claimer;
SET LOCAL ROLE disponit_m37_claimer;
-- Den ENESTE kalleren er verifiser_domenekontroll (domene_eier-eid).
GRANT EXECUTE ON FUNCTION sikre_overtakelsessak(TEXT, BIGINT, TEXT, TEXT,
    BIGINT, BIGINT, TEXT, TEXT) TO disponit_domene_eier;

RESET ROLE;

-- ------------------------------------------------------------
-- 11. Kolonnelåsen: den ENE lovlige loggpost-flyttingen (skiftet)
--     — KOPI av gjeldende kropp (dumpet fra basen), diff-endret og
--     merket 040. Alt annet uendret.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION unntak_kolonnelaas()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       -- 040: SKIFTET på en åpen overtakelsessak flytter loggposten
       -- (ny lineage, ny loggpost per revisjon) — den ENE lovlige
       -- endringen, og revisjonsbindings-triggeren håndhever at den
       -- bare skjer sammen med saksrevisjon+1.
       OR (NEW.loggpost_id IS DISTINCT FROM OLD.loggpost_id
           AND OLD.sakskilde <> 'domeneovertakelse')
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
END $function$

;

-- ------------------------------------------------------------
-- 12. unntak_historikk-hendelsen 'overtakelsesskifte' — CHECK-en
--     utvides DYNAMISK fra katalogdefinisjonen (038-formen; aldri en
--     hardkodet liste som mister 016/017-tillegg).
-- ------------------------------------------------------------
DO $$
DECLARE c TEXT; def TEXT;
BEGIN
    SELECT conname, pg_get_constraintdef(oid) INTO c, def
      FROM pg_constraint
     WHERE conrelid = 'unntak_historikk'::regclass
       AND pg_get_constraintdef(oid) LIKE '%claim_fornyet%';
    IF c IS NOT NULL AND def NOT LIKE '%overtakelsesskifte%' THEN
        EXECUTE format('ALTER TABLE unntak_historikk DROP CONSTRAINT %I', c);
        IF def LIKE '%ARRAY[%' THEN
            def := regexp_replace(def, '\]\)\)\)$',
                                  ', ''overtakelsesskifte''::text])))');
        ELSE
            def := regexp_replace(def, '\)\)$',
                                  ', ''overtakelsesskifte''))');
        END IF;
        IF def NOT LIKE '%overtakelsesskifte%' THEN
            RAISE EXCEPTION 'unntak_historikk: kunne ikke utvide'
                ' hendelses-CHECKen — uventet definisjonsform: %', def;
        END IF;
        EXECUTE 'ALTER TABLE unntak_historikk ADD CONSTRAINT '
             || quote_ident(c) || ' ' || def;
    END IF;
END $$;

-- ------------------------------------------------------------
-- 13. Varsling (§6): A og B varsles, ALDRI med motpartens identitet;
--     varselet er ikke evidens — feiler det, står saken (EXCEPTION-
--     grense rundt hele funksjonen, port 41).
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_domene_eier;

GRANT SELECT ON brukermedlemskap, varselvalg TO disponit_domene_eier;
GRANT INSERT ON varsel TO disponit_domene_eier;

CREATE OR REPLACE FUNCTION varsle_overtakelse(
    p_hostname TEXT, p_tapt TEXT, p_utfordrer TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE b RECORD; v_kanal TEXT;
BEGIN
    -- 035-mønsteret: alle brukere i tenanten, kanalvalg respektert.
    -- To løkker — mottakerens EGEN tekstnøkkel, aldri kryssidentitet.
    FOR b IN SELECT bm.tenant, bm.bruker_id,
                    CASE WHEN bm.tenant = p_tapt
                         THEN 'varsel.domene_tilbakekalt'
                         ELSE 'varsel.domene_avklaring' END AS nokkel
               FROM public.brukermedlemskap bm
              WHERE bm.tenant IN (p_tapt, p_utfordrer) AND bm.aktiv
    LOOP
        SELECT vv.kanal INTO v_kanal FROM public.varselvalg vv
         WHERE vv.tenant = b.tenant AND vv.bruker_id = b.bruker_id;
        INSERT INTO public.varsel (tenant, bruker_id, art, ressurs_type,
                                   ressurs_id, hendelse, tekstnokkel,
                                   parametre, epost_status)
        VALUES (b.tenant, b.bruker_id, 'domeneovertakelse', 'domene',
                p_hostname, b.nokkel, b.nokkel,
                jsonb_build_object('hostname', p_hostname),
                CASE WHEN COALESCE(v_kanal, 'epost_og_portal')
                          = 'kun_portal'
                     THEN 'ikke_aktuelt' ELSE 'koet' END)
        ON CONFLICT DO NOTHING;
    END LOOP;
EXCEPTION WHEN OTHERS THEN
    -- Port 41: varselet er ikke evidens. Saken og overgangen står.
    RAISE WARNING 'varsle_overtakelse feilet for %: %', p_hostname, SQLERRM;
END $$;
REVOKE ALL ON FUNCTION varsle_overtakelse(TEXT, TEXT, TEXT) FROM PUBLIC;

-- ------------------------------------------------------------
-- 14. verifiser_domenekontroll — KOPI av gjeldende kropp (dumpet fra
--     basen), diff-endret på de tre konfliktstedene: sak + varsel i
--     SAMME transaksjon, hendelses-ID-ene fanget der de skrives.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION verifiser_domenekontroll(p_tenant text, p_hostname text, p_wildcard boolean, p_aktor text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE v_eier TEXT; v_status_a TEXT; v_utloper_a TIMESTAMPTZ; v_status_b TEXT;
        v_motpart TEXT;
        -- 040: sak-i-samme-transaksjon (§2/§4)
        v_gen BIGINT; v_h_a BIGINT; v_h_b BIGINT; v_rid TEXT;
BEGIN
    v_rid := 'domene-' || replace(gen_random_uuid()::text, '-', '');
    p_hostname := public.krev_kanonisk_hostname(p_hostname);   -- §0-gjerdet
    PERFORM pg_advisory_xact_lock(hashtextextended('domene:' || p_hostname, 0));
    -- Codex: `avklaring_kreves` er TERMINAL FOR DENNE VEIEN. Etter at B har
    -- overtatt en aktiv verifisering står B i avklaring med bindingen på seg —
    -- et RETRY av samme verifisering ville da hoppet over overtakelsesgrenen
    -- (eieren er jo B selv) og falt ned i upserten nedenfor, som satte B rett
    -- til `verifisert` og dermed omgikk hele M-37-avgjørelsen. Kun
    -- `avgjor_domeneovertakelse` kan løfte B ut av avklaring.
    SELECT status, konflikt_motpart INTO v_status_b, v_motpart
      FROM public.domenekontroll
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
    -- Codex: en kandidat som ble AVVIST av M-37 står `tilbakekalt` MED bindingen
    -- fortsatt på seg. En re-verifisering ser da seg selv som bindingseier, hopper
    -- over ALLE fremmed-eier-grenene under, og ville upsertet seg rett til
    -- `verifisert` — omgått avvisningen uten en ny godkjenning. Tving den tilbake
    -- gjennom avklaring (ny M-37-sak); kun avgjor_domeneovertakelse kan verifisere.
    --
    -- Codex (denne runden): grenen returnerte `avklaring_kreves` — SAMME verdi som
    -- et retry av en alt pågående sak. `opprett_overtakelsessak` lages KUN fra
    -- `konflikt:<tapt-tenant>`, så reapplikasjonen fikk verken konfliktsignalet
    -- eller motparten: kandidaten ble stående `avklaring_kreves` uten noen fersk
    -- sak som kunne nå `avgjor_domeneovertakelse` — permanent limbo. Generasjonen
    -- økes (idempotensnøkkelen er hostname+generasjon → NY sak), og konflikten
    -- returneres med motparten saken skal navngi.
    --
    -- Gjerdet er `konflikt_motpart IS NOT NULL`, ikke `tilbakekalt` alene: kun en
    -- rad som HAR stått i en M-37-konflikt bærer en motpart. En tenant som ble
    -- tilbakekalt av en operatør (aldri i avklaring) har ingen motpart, ingen sak
    -- å gjenåpne og ingen avgjørelse å omgå — den følger den dokumenterte veien
    -- «tilbakekalt eier kan verifisere på nytt» (B4 rad 2) i stedet for å bli
    -- låst inne i en avklaring ingen kan avslutte.
    IF v_eier IS NOT DISTINCT FROM p_tenant AND v_status_b = 'tilbakekalt'
       AND v_motpart IS NOT NULL THEN
        UPDATE public.domenekontroll
           SET status = 'avklaring_kreves', wildcard = p_wildcard,
               autorisasjonsgenerasjon = autorisasjonsgenerasjon + 1
         WHERE tenant = p_tenant AND hostname = p_hostname
        RETURNING autorisasjonsgenerasjon INTO v_gen;
        INSERT INTO public.domenekontroll_hendelse
            (tenant, hostname, hendelse, fra_status, til_status, grunn, aktor)
            VALUES (p_tenant, p_hostname, 'avklaring_kreves', 'tilbakekalt',
                    'avklaring_kreves', 'reapplication_etter_avvisning', p_aktor)
        RETURNING id INTO v_h_b;
        -- 040 (§2): motparten fikk ingen ny hendelse i denne grenen —
        -- hendelse_a peker på dens SISTE, den andre halvdelen av
        -- konflikten som faktisk står i historikken.
        SELECT max(h.id) INTO v_h_a FROM public.domenekontroll_hendelse h
         WHERE h.hostname = p_hostname AND h.tenant = v_motpart;
        PERFORM public.sikre_overtakelsessak(p_hostname, v_gen, v_motpart,
            p_tenant, v_h_a, v_h_b, p_aktor, v_rid);
        PERFORM public.varsle_overtakelse(p_hostname, v_motpart, p_tenant);
        RETURN 'konflikt:' || v_motpart;
    END IF;
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
                 'overtatt_dns_kontroll', p_aktor)
            RETURNING id INTO v_h_a;    -- 040: A-siden av konflikten
            INSERT INTO public.domenekontroll (tenant, hostname, status, wildcard,
                autorisasjonsgenerasjon, konflikt_motpart)
                VALUES (p_tenant, p_hostname, 'avklaring_kreves', p_wildcard, 1,
                        v_eier)
            ON CONFLICT (tenant, hostname) DO UPDATE
                SET status = 'avklaring_kreves',
                    -- Codex P2: bær den NETTOPP verifiserte wildcard-scopen inn i
                    -- avklaringsraden. Ellers kunne en tenant med en gammel
                    -- wildcard-rad fullføre en eksakt-host-overtakelse og etter
                    -- M-37-godkjenning bli verifisert med den gamle scopen.
                    wildcard = p_wildcard,
                    -- Motparten saken navngir (= `konflikt:<tapt-tenant>` under).
                    konflikt_motpart = v_eier,
                    autorisasjonsgenerasjon = public.domenekontroll.autorisasjonsgenerasjon + 1
            RETURNING domenekontroll.autorisasjonsgenerasjon INTO v_gen;
            INSERT INTO public.domenekontroll_hendelse
                (tenant, hostname, hendelse, til_status, grunn, aktor) VALUES
                (p_tenant, p_hostname, 'avklaring_kreves', 'avklaring_kreves',
                 'overtatt_dns_kontroll', p_aktor)
            RETURNING id INTO v_h_b;    -- 040: B-siden av konflikten
            INSERT INTO public.hostname_binding (hostname, tenant)
                VALUES (p_hostname, p_tenant)
            ON CONFLICT (hostname) DO UPDATE SET tenant = p_tenant, bundet_ts = now();
            PERFORM public.sikre_overtakelsessak(p_hostname, v_gen, v_eier,
                p_tenant, v_h_a, v_h_b, p_aktor, v_rid);
            PERFORM public.varsle_overtakelse(p_hostname, v_eier, p_tenant);
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
                autorisasjonsgenerasjon, konflikt_motpart)
                VALUES (p_tenant, p_hostname, 'avklaring_kreves', p_wildcard, 1,
                        v_eier)
            ON CONFLICT (tenant, hostname) DO UPDATE
                SET status = 'avklaring_kreves', wildcard = p_wildcard,
                    konflikt_motpart = v_eier,
                    autorisasjonsgenerasjon = public.domenekontroll.autorisasjonsgenerasjon + 1
            RETURNING domenekontroll.autorisasjonsgenerasjon INTO v_gen;
            INSERT INTO public.domenekontroll_hendelse
                (tenant, hostname, hendelse, til_status, grunn, aktor) VALUES
                (p_tenant, p_hostname, 'avklaring_kreves', 'avklaring_kreves',
                 'samtidig_overtakelseskonflikt', p_aktor)
            RETURNING id INTO v_h_b;
            INSERT INTO public.hostname_binding (hostname, tenant)
                VALUES (p_hostname, p_tenant)
            ON CONFLICT (hostname) DO UPDATE SET tenant = p_tenant, bundet_ts = now();
            -- 040: motpartens siste hendelse er A-siden her (ingen ny).
            SELECT max(h.id) INTO v_h_a FROM public.domenekontroll_hendelse h
             WHERE h.hostname = p_hostname AND h.tenant = v_eier;
            PERFORM public.sikre_overtakelsessak(p_hostname, v_gen, v_eier,
                p_tenant, v_h_a, v_h_b, p_aktor, v_rid);
            PERFORM public.varsle_overtakelse(p_hostname, v_eier, p_tenant);
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
            -- Autorisasjonen er i havn: konflikten raden bar er over, og
            -- markøren skal ikke sende en senere, ordinær tilbakekalling
            -- inn i en avklaring det ikke finnes noen motpart for.
            konflikt_motpart = NULL,
            verifisert_ts = now(), siste_vellykkede_revalidering = now(),
            utloper = now() + interval '90 days';
    INSERT INTO public.domenekontroll_hendelse
        (tenant, hostname, hendelse, til_status, aktor) VALUES
        (p_tenant, p_hostname, 'verifisert', 'verifisert', p_aktor);
    INSERT INTO public.hostname_binding (hostname, tenant)
        VALUES (p_hostname, p_tenant)
    ON CONFLICT (hostname) DO UPDATE SET tenant = p_tenant, bundet_ts = now();
    RETURN 'verifisert';
END $function$;

RESET ROLE;

-- ------------------------------------------------------------
-- 15. Varselenumene: `art` og `ressurs_type` er lukkede CHECK-er (029) —
--     utvides DYNAMISK fra katalogdefinisjonen, 038-formen.
-- ------------------------------------------------------------
DO $$
DECLARE r RECORD; def TEXT; ny TEXT;
BEGIN
    FOR r IN SELECT conname, pg_get_constraintdef(oid) AS def
               FROM pg_constraint
              WHERE conrelid = 'varsel'::regclass
                AND conname IN ('varsel_art_chk', 'varsel_ressurs_type_chk')
    LOOP
        ny := CASE r.conname WHEN 'varsel_art_chk' THEN 'domeneovertakelse'
                             ELSE 'domene' END;
        IF r.def NOT LIKE '%' || ny || '%' THEN
            EXECUTE format('ALTER TABLE varsel DROP CONSTRAINT %I', r.conname);
            def := regexp_replace(r.def, '\]\)\)\)$',
                                  format(', %L::text])))', ny));
            IF def NOT LIKE '%' || ny || '%' THEN
                RAISE EXCEPTION 'varsel: kunne ikke utvide %s — uventet'
                    ' definisjonsform: %', r.conname, r.def;
            END IF;
            EXECUTE 'ALTER TABLE varsel ADD CONSTRAINT '
                 || quote_ident(r.conname) || ' ' || def;
        END IF;
    END LOOP;
END $$;

-- ------------------------------------------------------------
-- 16. sikre_sak_for_oppdrag (038) — KOPI av gjeldende kropp, diff-endret:
--     payload_type/sakskilde settes eksplisitt (NOT NULL uten default).
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_m37_claimer;

CREATE OR REPLACE FUNCTION sikre_sak_for_oppdrag(p_tenant text, p_oppdrag_id bigint, p_arsak text, p_aktor text, p_request_id text)
 RETURNS bigint
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE o RECORD; v_id BIGINT; v_logg BIGINT; v_policy TEXT; v_policy_hash TEXT;
        v_forsok INT := 0;
BEGIN
    -- Tenantporten FØRST — før GUC-ene under settes og før noe leses.
    -- Dette er den API-kallbare formen, og uten porten var `p_tenant`
    -- kallerens frie valg (se `krev_tenantkontekst`).
    PERFORM public.krev_tenantkontekst(p_tenant, 'sikre_sak_for_oppdrag');
    -- Historikktriggeren på unntak krever aktør + request-id i GUC-ene.
    -- Funksjonen FÅR dem eksplisitt — den setter dem selv (LOCAL), så
    -- reaper-/kvitteringsveiene ikke er avhengige av at kalleren husket
    -- nøyaktig hvilken kontekstvariant den satte.
    PERFORM set_config('disponit.aktor', p_aktor, true);
    PERFORM set_config('disponit.request_id', p_request_id, true);
    -- OPPDRAGSRADEN LÅSES FØRST, også på gjenbruksveien. Låsrekkefølgen
    -- er oppdrag → unntak overalt: reaperen (§5) holder alt `FOR UPDATE`
    -- på oppdraget når den kaller hit, og kvitteringsveien likeså. Ble
    -- unntaket låst først her, hadde to veier tatt de samme to låsene i
    -- hver sin rekkefølge — altså vranglås.
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
    -- «TERMINAL GJENBRUKES ALDRI» ER EN LÅS, IKKE ET BLIKK (Codex P2).
    --
    -- Uten `FOR UPDATE` leste gjenbruksveien saken i sitt eget snapshot:
    -- en saksbehandler som akkurat da satte `løst`/`avvist` uten å ha
    -- committet var usynlig, og hendelsen ble hengt på en sak som et
    -- øyeblikk senere var endelig — stikk i strid med regelen indeksen
    -- håndhever for INNSETTING. Med låsen venter vi på den transaksjonen,
    -- og READ COMMITTED revaluerer `NOT terminal` mot den nye versjonen:
    -- ble saken terminal, er raden ikke lenger et treff, og vi faller
    -- gjennom til å opprette en ny åpen sak. Det er nettopp utfallet
    -- regelen ber om.
    --
    -- Løkken er kappløpets andre halvdel: taper vi unik-bruddet, finnes
    -- vinnerens rad, og neste runde LÅSER den og leser den (eller ser at
    -- den alt er terminal og prøver innsettingen på nytt). Et tak på
    -- forsøkene, så et patologisk ping-pong mellom opprettelse og løsning
    -- blir en feil vi ser og ikke en evig løkke.
    LOOP
        v_forsok := v_forsok + 1;
        SELECT u.id INTO v_id FROM public.unntak u
         WHERE u.tenant = p_tenant AND u.oppdrag_id = p_oppdrag_id
           AND u.arsak = p_arsak AND NOT u.terminal
           FOR UPDATE;
        IF FOUND THEN
            RETURN v_id;                          -- idempotent (port 25)
        END IF;
        BEGIN
            INSERT INTO public.unntak (tenant, loggpost_id, handling, kategori,
                sakstype, prioritet, payload_kryptert, key_id, nonce,
                maks_auto_forsok_snapshot, policy_versjon, policy_content_hash,
                oppdrag_id, arsak,
                -- 040: payload_type er NOT NULL uten default; oppdragssaker
                -- arver alltid kryptert payload; sakskilde eksplisitt.
                payload_type, sakskilde)
            VALUES (p_tenant, v_logg, o.handling, 'teknisk_feil',
                    CASE p_arsak WHEN 'sikkerhet' THEN 'sikkerhet'
                                 ELSE 'normal' END,
                    CASE p_arsak WHEN 'sikkerhet' THEN 'hoy' ELSE 'normal' END,
                    o.payload_kryptert, o.key_id, o.nonce,
                    0, coalesce(v_policy, 'ukjent'),
                    coalesce(v_policy_hash, ''),
                    p_oppdrag_id, p_arsak,
                    'kryptert', 'oppdrag')
            RETURNING id INTO v_id;
            EXIT;                                 -- innsettingsveien
        EXCEPTION WHEN unique_violation THEN
            -- Kappløpstaperen. Retur skjer i NESTE runde, gjennom
            -- gjenbruksveien over — ikke gjennom innsettingsveiens hale.
            -- Sakskoblingen er én HENDELSE, ikke en tilstand: raden er
            -- idempotent fordi indeksen gjør den det, men historikken er
            -- append-only og teller. Falt taperen ut i den felles halen,
            -- fikk ETT oppdrag TO `sak_for_oppdrag`-rader for den samme
            -- koblingen — og det skjer i praksis, med samtidige sene
            -- kvitteringer eller sikkerhetskonflikter fra hver sin
            -- claim-generasjon. Å telle hendelser i sporet er nettopp det
            -- sporet er til for.
            IF v_forsok >= 5 THEN
                RAISE;
            END IF;
        END;
    END LOOP;
    -- Kun på INNSETTINGSVEIEN: koblingen skjedde nettopp, her.
    INSERT INTO public.unntak_historikk (tenant, unntak_id, hendelse,
                                         aktor, request_id, detalj)
    VALUES (p_tenant, v_id, 'sak_for_oppdrag', p_aktor, p_request_id,
            jsonb_build_object('oppdrag_id', p_oppdrag_id,
                               'arsak', p_arsak));
    RETURN v_id;
END $function$;

RESET ROLE;

-- ------------------------------------------------------------
-- 17. Adjudikatorveien for API-et: runtime får SET ROLE (aldri arv) til
--     adjudikatorrollen — samme medlemskapsform som migrator↔claimer.
--     Endepunktet er scope-gatet (domains:adjudicate) og bytter rolle
--     KUN for de to lesningene; policyen i §9 avgrenser radene.
-- ------------------------------------------------------------
-- Medlemskapet er et KLYNGEOBJEKT og eies av oppsett-postgresql.sh
-- (superbruker) — som rolleopprettelsen selv. Forsøket her er for
-- baser der migrator HAR admin (lokal utvikling); en migrator uten
-- privilegiet skal ikke velte migrasjonen (rebuild-testene kjører som
-- migrator), for medlemskapet overlever uansett rebuilds i klyngen.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles
              WHERE rolname = 'disponit_domains_adjudicator') THEN
    BEGIN
      GRANT disponit_domains_adjudicator TO disponit
          WITH INHERIT FALSE, SET TRUE;
    EXCEPTION WHEN insufficient_privilege THEN
      RAISE NOTICE 'adjudikator-medlemskap settes av oppsett (klyngeobjekt)';
    END;
  END IF;
END $$;

-- ------------------------------------------------------------
-- 18. claim_neste_sak — KOPI av gjeldende kropp (dumpet fra basen),
--     diff-endret: kandidatvalget inn i en MATERIALIZED CTE.
--
--     NØDVENDIGGJORT AV §9, FUNNET VED Å KJØRE: da adjudikator-policyen
--     kom på `unntak`, byttet planleggeren form på claimens
--     `UPDATE ... FROM (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1)` —
--     subspørringen ble INNER side i en nested loop og reskannet per
--     ytre rad. FOR UPDATE re-sjekker radens SISTE versjon, så hver
--     rescan så forrige rad som alt claimet, hoppet videre — og ÉN
--     claim tømte hele køen (målt: 8/8 saker, samme claim_id, én
--     kall). En LIMIT i en subspørring begrenser per EVALUERING;
--     MATERIALIZED er garantien for nøyaktig én evaluering.
--
--     Samme mønster finnes i varselsender-klaimet (027) — uendret av
--     denne migrasjonen og med uendret policyflate, så det tas som
--     eget arbeid, ikke som blindpassasjer her.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION public.claim_neste_sak(p_claim_id text, p_lease_s integer DEFAULT 120)
 RETURNS TABLE(tenant text, id bigint, handling text, kategori text, loggpost_id bigint, claim_generation integer, claim_utloper timestamp with time zone, forsok integer, maks_auto_forsok_snapshot integer, fase text, verification_generation integer)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE
    v_lease INT;
BEGIN
    IF p_claim_id IS NULL OR p_claim_id !~ '^[0-9a-f]{32,}$' THEN
        RAISE EXCEPTION 'claim_neste_sak: ugyldig claim_id-format';
    END IF;
    v_lease := least(greatest(coalesce(p_lease_s, 120), 30), 600);

    -- 040 (§18): kandidaten velges i en MATERIALIZED CTE, IKKE i en
    -- FROM-subspørring. MÅLT på rebuilt base: adjudikator-policyen (§9)
    -- endret planformen slik at subspørringen ble INNER side i en nested
    -- loop og RESKANNET per ytre rad — og fordi FOR UPDATE re-sjekker
    -- radens SISTE versjon, så hver rescan forrige rad som alt claimet
    -- ('under_behandling'), hoppet videre, og ÉN claim tømte hele køen
    -- (8/8 rader, alle med samme claim_id). LIMIT 1 i en subspørring er
    -- bare en grense per EVALUERING; MATERIALIZED er garantien for at
    -- evalueringen skjer nøyaktig én gang. Feilen var latent siden 005 —
    -- policy-endringen gjorde den målbar, den skapte den ikke.
    RETURN QUERY
    WITH kandidat AS MATERIALIZED (
      SELECT k.tenant, k.id, k.status
        FROM public.unntak k
       WHERE k.sakstype = 'normal'
         AND k.status IN ('ny','verifikasjon_klar','verifikasjon_retry_klar')
         AND (k.status <> 'ny'
              OR k.forsok < least(coalesce(k.maks_auto_forsok_snapshot, 0), 3))
         AND (SELECT pg_catalog.count(*) FROM public.unntak b
               WHERE b.tenant = k.tenant
                 AND b.status = 'under_behandling'
                 AND b.claim_utloper > pg_catalog.now()) < 5
       -- Klar-tilstandene går FØRST: en sak som har ventet på en
       -- verifikator har allerede brukt tid, og å la den stå bak ferske
       -- saker ville gjort tofaseveien systematisk tregere enn enfase.
       ORDER BY (CASE WHEN k.status <> 'ny' THEN 0 ELSE 1 END),
                (CASE k.prioritet WHEN 'hoy' THEN 0 ELSE 1 END), k.ts, k.id
         FOR UPDATE SKIP LOCKED
       LIMIT 1
    )
    UPDATE public.unntak u
       SET status = 'under_behandling',
           claim_id = p_claim_id,
           claim_generation = u.claim_generation + 1,
           claim_utloper = pg_catalog.now() + (v_lease || ' seconds')::INTERVAL,
           -- Forsøkstelleren teller BEHANDLINGSforsøk. En fase-2-claim er
           -- ikke et nytt forsøk på saken — den er andre halvdel av det
           -- samme. Uten dette skillet ville en tofasesak brukt opp
           -- budsjettet sitt dobbelt så fort som en enfasesak.
           forsok = u.forsok + CASE WHEN u.status = 'ny' THEN 1 ELSE 0 END
      FROM kandidat k
     WHERE u.tenant = k.tenant AND u.id = k.id
    RETURNING u.tenant, u.id, u.handling, u.kategori, u.loggpost_id,
              u.claim_generation, u.claim_utloper, u.forsok,
              u.maks_auto_forsok_snapshot,
              -- FASEN FØLGER STATUSEN, ikke generasjonstelleren.
              --
              -- MÅLT: med `verification_generation = 0 => ny, ellers fase2`
              -- rapporterte en sak i `verifikasjon_retry_klar` fase2, og
              -- arbeideren lette etter et positivt bevis som per definisjon
              -- ikke fantes — retryen ga `manuell: intet_positivt_bevis`.
              -- Retry-veien kunne dermed ALDRI åpne en ny generasjon, selv
              -- om både statusmaskinen og kommentarene sa at den skulle.
              --
              -- Statusen er den autoritative fasen: `verifikasjon_klar`
              -- betyr «et bevis foreligger», `verifikasjon_retry_klar`
              -- betyr «forrige runde slo feil, kjør en ny». Telleren sier
              -- bare hvor mange runder som har vært.
              --
              -- `k` er raden slik den var FØR UPDATE-en; `u` ville gitt
              -- `under_behandling` for alle tre.
              CASE k.status WHEN 'verifikasjon_klar' THEN 'fase2'
                            WHEN 'verifikasjon_retry_klar' THEN 'retry'
                            ELSE 'ny' END,
              u.verification_generation;
END $function$
;
RESET ROLE;
