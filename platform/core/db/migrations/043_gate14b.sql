-- ============================================================
-- 043 — GATE 14b: kansellering med fencing (klarsignal 2026-08-18)
--
-- Klarsignalet sier «migrasjon 041»; det nummeret var foreldet i det
-- arbeidet startet (overtakelsessaken tok det) — samme lærdom som sist:
-- NÅ-blokkas numre er skrevet før forrige PR landet.
--
-- 14a står i produksjon: avvis på sak med levende oppdrag → 409
-- `utestaaende_oppdrag`. 14b er hva som skjer I STEDET FOR 409:
-- kansellering med fencing — nei-et tar effekt i det databasen kan
-- BEVISE at ingen kvittering fra det gamle claimet noen gang kan
-- fullføre oppdraget. Statusmaskinen KOMPONERES, ikke utvides:
-- `plukket → opprettet → kansellert` i én transaksjon, to autoritative
-- revisjonshendelser for ett klikk.
-- ============================================================

-- ------------------------------------------------------------
-- 1. `oppdrag.kansellert_aarsak` — lukket, statusimplisert, immutabel
-- ------------------------------------------------------------
ALTER TABLE oppdrag ADD COLUMN IF NOT EXISTS kansellert_aarsak TEXT;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'oppdrag_kansellert_aarsak_gyldig') THEN
    ALTER TABLE oppdrag ADD CONSTRAINT oppdrag_kansellert_aarsak_gyldig
      CHECK (kansellert_aarsak IS NULL
             OR kansellert_aarsak IN ('menneskelig_avvis'));
  END IF;
  -- Konsolideringspresiseringen: årsak impliserer status, RELASJONELT —
  -- en årsak på en rad som ikke er kansellert er en løgn skjemaet selv
  -- skal nekte å bære.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'kansellert_aarsak_krever_status') THEN
    ALTER TABLE oppdrag ADD CONSTRAINT kansellert_aarsak_krever_status
      CHECK (kansellert_aarsak IS NULL OR status = 'kansellert');
  END IF;
END $$;

-- Immutabel når satt, og kan aldri fjernes — `avvis_endring` er samme
-- vakt som oppdragsbindingen på unntak bruker.
DROP TRIGGER IF EXISTS oppdrag_kansellert_aarsak_immutable ON oppdrag;
CREATE TRIGGER oppdrag_kansellert_aarsak_immutable
  BEFORE UPDATE ON oppdrag
  FOR EACH ROW WHEN (OLD.kansellert_aarsak IS NOT NULL
                     AND NEW.kansellert_aarsak
                         IS DISTINCT FROM OLD.kansellert_aarsak)
  EXECUTE FUNCTION avvis_endring();

-- ------------------------------------------------------------
-- 2. `unntak.arsak` utvides: kompensasjon og irreversibel utført
--    (038s partial UNIQUE (tenant, oppdrag_id, arsak) WHERE NOT terminal
--    dekker de nye verdiene uten endring.)
-- ------------------------------------------------------------
ALTER TABLE unntak DROP CONSTRAINT IF EXISTS unntak_arsak_check;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'unntak_arsak_gyldig') THEN
    ALTER TABLE unntak ADD CONSTRAINT unntak_arsak_gyldig
      CHECK (arsak IN ('evidensfrist', 'sikkerhet',
                       'kompensasjon_kreves', 'irreversibel_utfort'));
  END IF;
END $$;

-- ------------------------------------------------------------
-- 3. Kvitteringskapabiliteten får utfallet `avvist`
-- ------------------------------------------------------------
-- Tabellen er CLAIMER-EID (005) — kolonne-/constraint-ALTER må skje i
-- eierens vindu, ellers «must be owner» (målt på fersk rebuild).
SET LOCAL ROLE disponit_m37_claimer;
ALTER TABLE kvitteringskapabiliteter
  DROP CONSTRAINT IF EXISTS kvitteringskapabiliteter_status_check;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'kvitteringskapabilitet_status_gyldig') THEN
    ALTER TABLE kvitteringskapabiliteter
      ADD CONSTRAINT kvitteringskapabilitet_status_gyldig
      CHECK (status IN ('utstedt', 'brukt', 'feilet', 'avvist'));
  END IF;
END $$;
RESET ROLE;

-- Statusmaskinen: `avvist` er terminal, på linje med `feilet`.
-- EIERSKAPSNORMALISERING FØRST (lærdommen fra 041/#102): designeieren er
-- claimer, men en eldre base kan bære funksjonen migrator-eid —
-- reparasjonen kjører ETTER migrasjonene i oppsett. Guarded ALTER gjør
-- rollevinduet riktig i begge eierskapsverdener.
DO $$ BEGIN
  IF (SELECT pg_get_userbyid(proowner) FROM pg_proc
       WHERE oid = to_regprocedure(
         'public.kvitteringskapabilitet_statusmaskin()')) = current_user THEN
    EXECUTE 'ALTER FUNCTION public.kvitteringskapabilitet_statusmaskin()'
         || ' OWNER TO disponit_m37_claimer';
  END IF;
END $$;
SET LOCAL ROLE disponit_m37_claimer;
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
    -- 043: menneskets nei er like terminalt som modulens feil. En brent
    -- `avvist` som kunne flippes tilbake ville gjenåpnet nøyaktig den
    -- fullføringsveien fencingen finnes for å bevise død.
    IF OLD.status = 'avvist' AND NEW.status <> 'avvist' THEN
        RAISE EXCEPTION 'kvitteringskapabiliteter: avvist er terminal';
    END IF;
    IF OLD.status = 'brukt' AND NEW.status <> 'brukt' THEN
        RAISE EXCEPTION 'kvitteringskapabiliteter: brukt er terminal';
    END IF;
    RETURN NEW;
END $$;
RESET ROLE;

-- Treargs-utgaven: oppløsningen brenner kapabiliteten med `avvist` —
-- SAMME atomiske kappløpssemantikk som kvitteringsveien, for det ER
-- samme kappløp: hvem som brenner først, vinner. Toargs-utgaven står
-- uendret (alle eksisterende kallere er kvitteringer).
DO $$ BEGIN
  IF to_regprocedure('public.bruk_kvitteringskapabilitet(text,text)')
       IS NOT NULL
     AND (SELECT pg_get_userbyid(proowner) FROM pg_proc
           WHERE oid = to_regprocedure(
             'public.bruk_kvitteringskapabilitet(text,text)')) = current_user
  THEN
    EXECUTE 'ALTER FUNCTION public.bruk_kvitteringskapabilitet(text,text)'
         || ' OWNER TO disponit_m37_claimer';
  END IF;
END $$;
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION bruk_kvitteringskapabilitet(
    p_jti TEXT, p_resultathash TEXT, p_utfall TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_treff  INT;
    v_status TEXT;
    v_hash   TEXT;
BEGIN
    IF p_utfall NOT IN ('brukt', 'avvist') THEN
        RAISE EXCEPTION 'bruk_kvitteringskapabilitet: ukjent utfall %',
            p_utfall USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_utfall = 'brukt' AND p_resultathash IS NULL THEN
        RAISE EXCEPTION 'bruk_kvitteringskapabilitet: brukt krever hash'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.kvitteringskapabiliteter k
       SET status = p_utfall,
           -- `avvist` bærer INGEN hash: det finnes intet resultat å
           -- attestere — det er hele poenget.
           resultathash = CASE WHEN p_utfall = 'brukt'
                               THEN p_resultathash END,
           brukt_ts = pg_catalog.now()
     WHERE k.jti = p_jti
       AND k.status = 'utstedt'
       AND k.utloper > pg_catalog.now();
    GET DIAGNOSTICS v_treff = ROW_COUNT;
    IF v_treff = 1 THEN
        RETURN p_utfall;
    END IF;

    -- Kappløpet tapt, eller kapabiliteten var alt terminal/utløpt.
    SELECT k.status, k.resultathash INTO v_status, v_hash
      FROM public.kvitteringskapabiliteter k
     WHERE k.jti = p_jti;
    IF NOT FOUND THEN
        RETURN 'ugyldig';
    END IF;
    IF v_status = 'brukt' THEN
        IF p_utfall = 'avvist' THEN
            -- Kvitteringen vant: kan systemet bevise at handlingen ble
            -- utført, skal det ikke skrive en terminal «avvist» som om
            -- nei-et rakk fram. Taperen får sannheten, ikke stillhet.
            RETURN 'konflikt';
        END IF;
        IF v_hash IS NOT DISTINCT FROM p_resultathash THEN
            RETURN 'idempotent';
        END IF;
        RETURN 'konflikt';
    END IF;
    IF v_status = 'avvist' THEN
        -- To samtidige avvis: én oppløsning, resten idempotente (port 5).
        -- En KVITTERING som treffer en avvist kapabilitet er derimot fra
        -- et fencet claim: fail-closed `ugyldig`, og modulens retry ender
        -- i sen-evidens-stien via generasjonsgjerdet.
        IF p_utfall = 'avvist' THEN
            RETURN 'idempotent';
        END IF;
    END IF;
    RETURN 'ugyldig';
END $$;
REVOKE ALL ON FUNCTION bruk_kvitteringskapabilitet(TEXT, TEXT, TEXT)
  FROM PUBLIC;
-- Granten gis AV EIEREN, i vinduet — migrator har ingen grant-option her.
GRANT EXECUTE ON FUNCTION bruk_kvitteringskapabilitet(TEXT, TEXT, TEXT)
  TO disponit;
RESET ROLE;

-- ------------------------------------------------------------
-- 4. `sak_utestaaende` dekker BEGGE opphav (klarsignal §2)
--    038 gjorde `unntak_id` til OPPHAV, ikke generell sakstilknytning:
--    et beslutningsoppdrag peker den andre veien (`unntak.oppdrag_id`).
--    14a så bare reparasjonsveien — et levende beslutningsoppdrag kunne
--    avvises rett forbi vakten.
-- ------------------------------------------------------------
DO $$ BEGIN
  IF (SELECT pg_get_userbyid(proowner) FROM pg_proc
       WHERE oid = to_regprocedure('public.sak_utestaaende(text,bigint)'))
     = current_user THEN
    EXECUTE 'ALTER FUNCTION public.sak_utestaaende(text,bigint)'
         || ' OWNER TO disponit_m37_claimer';
  END IF;
END $$;
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION sak_utestaaende(p_tenant TEXT, p_unntak_id BIGINT)
RETURNS TABLE(kilde TEXT, ref TEXT, status TEXT)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT DISTINCT * FROM (
        SELECT 'oppdrag'::text, o.id::text, o.status
          FROM public.oppdrag o
         WHERE o.tenant = p_tenant AND o.unntak_id = p_unntak_id
           AND o.status <> 'kansellert'
        UNION ALL
        -- Beslutningsopphavet: saken peker på oppdraget, ikke omvendt.
        SELECT 'oppdrag'::text, o.id::text, o.status
          FROM public.oppdrag o
          JOIN public.unntak u ON u.tenant = o.tenant AND u.oppdrag_id = o.id
         WHERE u.tenant = p_tenant AND u.id = p_unntak_id
           AND o.status <> 'kansellert'
        UNION ALL
        SELECT 'kapabilitet'::text, k.jti, k.status
          FROM public.arbeidskapabiliteter k
         WHERE k.tenant = p_tenant AND k.unntak_id = p_unntak_id
           AND k.status NOT IN ('brukt', 'feilet')
    ) s
    ORDER BY 1, 2;
$$;
RESET ROLE;

-- ------------------------------------------------------------
-- 5. Historikk-hendelsen for fencinghoppet
-- ------------------------------------------------------------
DO $$
DECLARE def TEXT;
BEGIN
  IF NOT EXISTS (
      SELECT 1 FROM pg_constraint
       WHERE conname = 'unntak_historikk_hendelse_check'
         AND pg_get_constraintdef(oid) LIKE '%oppdrag_fencet%') THEN
    SELECT pg_get_constraintdef(oid) INTO def FROM pg_constraint
     WHERE conname = 'unntak_historikk_hendelse_check';
    IF def IS NULL THEN
      RAISE EXCEPTION '042: fant ikke hendelse-CHECKen på unntak_historikk';
    END IF;
    -- Splice: utvid arrayen med den nye hendelsen — samme grep som 041
    -- brukte for varselenumene: den GJELDENDE definisjonen er kilden,
    -- aldri en avskrift av den.
    def := replace(def, '''oppdrag_kansellert''::text',
                   '''oppdrag_kansellert''::text, ''oppdrag_fencet''::text');
    EXECUTE 'ALTER TABLE unntak_historikk DROP CONSTRAINT'
         || ' unntak_historikk_hendelse_check';
    EXECUTE 'ALTER TABLE unntak_historikk ADD CONSTRAINT'
         || ' unntak_historikk_hendelse_check ' || def;
  END IF;
END $$;

-- ------------------------------------------------------------
-- 6. Reversibiliteten for et oppdrag — lesejobb for sen-utført-veien
--    (§5): utledes av MODULKONTRAKTEN oppdraget ble claimet under,
--    aldri av gjetning. NULL når oppdraget aldri ble modulbundet — da
--    finnes heller ingen motor som kan ha utført noe.
-- ------------------------------------------------------------
-- Kontrakttabellen er migrator-eid (014 registrerer i eierens vindu kun
-- funksjonene): lesegranten gis rett frem, av migrator selv.
GRANT SELECT ON modulkontrakt TO disponit_m37_claimer;
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION reversibilitet_for_oppdrag(
    p_tenant TEXT, p_oppdrag_id BIGINT)
RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT k.reversibilitet
      FROM public.oppdrag o
      JOIN public.modulkontrakt k
        ON k.modul_id = o.modul_id
       AND k.kontraktversjon = o.kontraktversjon
       AND k.kontrakt_hash = o.kontrakt_hash
     WHERE o.tenant = p_tenant AND o.id = p_oppdrag_id
$$;
REVOKE ALL ON FUNCTION reversibilitet_for_oppdrag(TEXT, BIGINT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION reversibilitet_for_oppdrag(TEXT, BIGINT)
  TO disponit;
RESET ROLE;

-- ------------------------------------------------------------
-- 7. `avvis_med_opplosning` — nei-et og beviset i ÉN transaksjon
-- ------------------------------------------------------------
-- Claimer-eid: én skrivevei til oppdrag/kapabiliteter, som resten av
-- M-37-flaten. Kalleren (avvis-veien i unntaksbehandlingen) holder alt
-- saks-låsen; her tas OPPDRAGSLÅSEN — samme låseorden som claim-veiene
-- (sak → oppdrag → kapabilitet), så ingen ny vranglåsflate.
--
-- KONTRAKT: `p_forventet` er oppdragene kalleren så som levende under
-- sakslåsen. Funksjonen re-evaluerer under oppdragslåsen:
--   * fortsatt levende → kapabiliteten brennes `avvist`, claimet fences
--     (`plukket → opprettet`, owner_generation++, eierbindingen fjernes),
--     og oppdraget kanselleres med `menneskelig_avvis`. To hendelser i
--     historikken: fencingen ER en autoritativ tilstandsendring, ikke støy.
--   * rukket å bli `utfort` (kvitteringen vant kappløpet) → utfallet
--     `oppdrag_utfort` med kvitteringsreferansen. Kalleren ruller HELE
--     transaksjonen tilbake: kan systemet bevise at handlingen ble
--     utført, skal det ikke skrive en terminal «avvist» som om nei-et
--     rakk fram — mennesket beslutter på nytt med fakta.
--   * rukket å bli `feilet`/`kansellert` → ingenting å løse opp; føres
--     med status ved avvis, som evidens for hva mennesket visste.
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION avvis_med_opplosning(
    p_tenant TEXT, p_unntak_id BIGINT, p_forventet BIGINT[],
    p_aktor TEXT, p_request_id TEXT)
RETURNS TABLE(utfall TEXT, oppdrag_id BIGINT,
              oppdrag_status_ved_avvis TEXT, kvitteringsref TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    r RECORD; v_jti TEXT; v_brenning TEXT; v_status TEXT; v_hash TEXT;
BEGIN
    IF p_forventet IS NULL OR array_length(p_forventet, 1) IS NULL THEN
        RAISE EXCEPTION 'avvis_med_opplosning: ingen oppdrag å løse opp'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    FOR r IN
        SELECT o.id, o.status FROM public.oppdrag o
         WHERE o.tenant = p_tenant AND o.id = ANY(p_forventet)
         ORDER BY o.id
           FOR UPDATE
    LOOP
        IF r.status = 'utfort' THEN
            -- Kvitteringen vant før vi rakk å låse.
            SELECT k.resultathash INTO v_hash
              FROM public.kvitteringskapabiliteter k
             WHERE k.tenant = p_tenant AND k.oppdrag_id = r.id
               AND k.status = 'brukt'
             ORDER BY k.brukt_ts DESC LIMIT 1;
            RETURN QUERY SELECT 'oppdrag_utfort'::text, r.id,
                                r.status, v_hash;
            CONTINUE;
        END IF;
        IF r.status NOT IN ('opprettet', 'plukket') THEN
            RETURN QUERY SELECT 'alt_terminal'::text, r.id, r.status,
                                NULL::text;
            CONTINUE;
        END IF;

        -- Kappløpet avgjøres av kapabiliteten — porten som alt finnes.
        SELECT k.jti INTO v_jti
          FROM public.kvitteringskapabiliteter k
         WHERE k.tenant = p_tenant AND k.oppdrag_id = r.id
           AND k.status = 'utstedt'
         ORDER BY k.utstedt DESC LIMIT 1
           FOR UPDATE;
        IF v_jti IS NOT NULL THEN
            v_brenning := public.bruk_kvitteringskapabilitet(
                v_jti, NULL, 'avvist');
            IF v_brenning = 'konflikt' THEN
                -- Kvitteringen brant først: oppdraget er (i ferd med å
                -- bli) utført. Referansen er vinnerens hash.
                SELECT k.resultathash INTO v_hash
                  FROM public.kvitteringskapabiliteter k
                 WHERE k.jti = v_jti;
                RETURN QUERY SELECT 'oppdrag_utfort'::text, r.id,
                                    r.status, v_hash;
                CONTINUE;
            END IF;
        END IF;

        IF r.status = 'plukket' THEN
            -- Hopp 1 — FENCINGEN: en reell hendelse med eget spor.
            -- Generasjonsbumpen er beviset på at gammel utførelses-
            -- autoritet er død; sen kvittering møter generasjonsgjerdet
            -- og ender som sen evidens, aldri som fullføring.
            UPDATE public.oppdrag o
               SET status = 'opprettet',
                   owner_claim_id = NULL,
                   owner_generation = o.owner_generation + 1,
                   owner_lease_utloper = NULL
             WHERE o.tenant = p_tenant AND o.id = r.id;
            INSERT INTO public.unntak_historikk (tenant, unntak_id,
                hendelse, aktor, request_id, detalj)
            VALUES (p_tenant, p_unntak_id, 'oppdrag_fencet', p_aktor,
                    p_request_id, jsonb_build_object(
                        'oppdrag_id', r.id,
                        'fra_status', 'plukket',
                        'kapabilitet_brent', v_jti IS NOT NULL));
        END IF;

        -- Hopp 2 — menneskets beslutning. Veien via `feilet` er avvist:
        -- et menneskelig nei er ikke en modulfeil, og revisjonssporet
        -- skal ikke lyve om det.
        UPDATE public.oppdrag o
           SET status = 'kansellert',
               kansellert_aarsak = 'menneskelig_avvis'
         WHERE o.tenant = p_tenant AND o.id = r.id;
        INSERT INTO public.unntak_historikk (tenant, unntak_id, hendelse,
            aktor, request_id, detalj)
        VALUES (p_tenant, p_unntak_id, 'oppdrag_kansellert', p_aktor,
                p_request_id, jsonb_build_object(
                    'oppdrag_id', r.id,
                    'kansellert_aarsak', 'menneskelig_avvis',
                    'oppdrag_status_ved_avvis', r.status));
        RETURN QUERY SELECT 'kansellert'::text, r.id, r.status, NULL::text;
    END LOOP;
END $$;
REVOKE ALL ON FUNCTION avvis_med_opplosning(TEXT, BIGINT, BIGINT[], TEXT,
    TEXT) FROM PUBLIC;
-- Kalles av avvis-veien i unntaksbehandlingen (runtime, scope-gatet
-- `exceptions:handle` i app-laget — samme scopeport som resten av veien).
GRANT EXECUTE ON FUNCTION avvis_med_opplosning(TEXT, BIGINT, BIGINT[],
    TEXT, TEXT) TO disponit;
RESET ROLE;
