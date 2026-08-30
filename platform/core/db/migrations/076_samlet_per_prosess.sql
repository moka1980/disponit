-- 076: samlet-porten på riktig nivå (#163 — K2-dom B, eiers ratifisering
-- i #153 13:47)
--
-- Invarianten er PROSESSVID («aldri levende og reapet payload i samme
-- prosess ved COMMIT»), men kroken var PER RAD: en utsatt constraint-
-- trigger kan ikke være FOR EACH STATEMENT, så en full prosess på 5000
-- kandidater × 7 medlemmer kjørte de samme prosessvide EXISTS-skannene
-- ~35 000 ganger ved COMMIT — kvadratisk i kandidatantallet, på nøyaktig
-- den arbeidsmengden katalogen selger, og nok til å velte reap-
-- transaksjonen på stagingtimeouten. Porten var KORREKT; nivået var
-- feil. (#163: hard forutsetning for utførelsesarmen i 5000-klassen.)
--
-- Eiers B, ordrett:
--   * merkingen degraderes til en billig per-rad-markering i en
--     transaksjonslokal temptabell («denne prosessen ble rørt»),
--   * selve EXISTS-sjekken flyttes til ÉN utsatt constraint-trigger på
--     rekrutteringsprosess-ankeret — én gang per prosess, ikke per rad.
--
-- Mekanikken som binder de to: FØRSTE markering av en prosess i
-- transaksjonen gjør én no-op-UPDATE på ankerraden. Det armerer
-- ankerets utsatte port for nøyaktig den prosessen — også når
-- transaksjonen aldri rører ankeret selv (den forsinkede INSERT-en fra
-- port 19s egen testflora). Senere rader i samme prosess er ett
-- temptabell-oppslag og retur.

-- ------------------------------------------------------------
-- 1. Markøren. Claimer-eid definer som lagervaktene: skriverne er både
--    runtime (INSERT) og reaperen (UPDATE), og no-op-armeringen trenger
--    claimerens UPDATE-rett og kryss-tenant-policy. Verdiene endres
--    ikke, så radvaktens DISTINCT-porter er stille.
SET LOCAL ROLE disponit_m37_claimer;
CREATE FUNCTION m57_marker_beroert_prosess()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_vaar BOOLEAN;
BEGIN
    CREATE TEMP TABLE IF NOT EXISTS m57_beroerte_prosesser (
        tenant TEXT NOT NULL,
        prosess_id UUID NOT NULL,
        PRIMARY KEY (tenant, prosess_id)
    ) ON COMMIT DROP;
    -- MARKØRTABELLEN MÅ VÆRE VÅR (CodeRabbit): TEMP-retten er PUBLICs,
    -- så en kaller kunne pre-lage tabellen — ferdig seedet — og kvele
    -- armeringen for sine egne skriv. Skapes den her (SECURITY DEFINER)
    -- eies den av claimeren; alt annet eierskap er en forfalskning, og
    -- da avvises SKRIVET (fail-closed), aldri bare markeringen.
    SELECT c.relowner = (SELECT r.oid FROM pg_catalog.pg_roles r
                          WHERE r.rolname = 'disponit_m37_claimer')
      INTO v_vaar
      FROM pg_catalog.pg_class c
     WHERE c.relnamespace = pg_catalog.pg_my_temp_schema()
       AND c.relname = 'm57_beroerte_prosesser';
    IF v_vaar IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'kandidatlagrene: markørtabellen er ikke portens'
            ' egen — skrivet avvises (klarsignalet §5, port 19)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    INSERT INTO pg_temp.m57_beroerte_prosesser (tenant, prosess_id)
         VALUES (NEW.tenant, NEW.prosess_id)
    ON CONFLICT DO NOTHING;
    IF NOT FOUND THEN
        RETURN NULL;                 -- alt armert i denne transaksjonen
    END IF;
    UPDATE public.rekrutteringsprosess p
       SET prosess_id = p.prosess_id
     WHERE p.tenant = NEW.tenant AND p.prosess_id = NEW.prosess_id;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION m57_marker_beroert_prosess() FROM PUBLIC;
-- CREATE TRIGGER krever EXECUTE for TABELLEIEREN, og grantet må gis av
-- funksjonens EIER — altså her, inne i claimer-blokka (057-formen).
DO $$
DECLARE v_eier TEXT;
BEGIN
    SELECT pg_catalog.pg_get_userbyid(relowner) INTO v_eier
      FROM pg_catalog.pg_class
     WHERE oid = 'public.kandidat_originaldokument'::regclass;
    EXECUTE format('GRANT EXECUTE ON FUNCTION'
                   ' m57_marker_beroert_prosess() TO %I', v_eier);
END $$;

-- Sjekken selv er uendret i innhold (075-kroppen med de sju medlemmene)
-- — men den leser nå NEW fra ANKERRADEN, og kjører én gang per prosess.
-- Ingen omskriving trengs: funksjonen bruker bare NEW.tenant og
-- NEW.prosess_id, som ankerraden bærer selv.
RESET ROLE;

-- ------------------------------------------------------------
-- 2. Nivåbyttet: de utsatte per-rad-portene på medlemmene byttes med
--    billige markører; ankeret får DEN ENE utsatte porten. Markøren
--    dekker alle SJU medlemmene (075: ankeret kandidat er med — dets
--    merkeovergang er også en del av blandingen porten måler).
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'kandidat', 'kandidat_originaldokument', 'kandidat_parsettekst',
        'kandidat_evalueringsartefakt', 'kandidat_intervjusporsmal',
        'kandidat_utsendingsdata', 'kandidat_avmaskering'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I',
                       t || '_reapes_samlet', t);
        EXECUTE format(
            'CREATE TRIGGER %I AFTER INSERT OR UPDATE ON %I'
            ' FOR EACH ROW EXECUTE FUNCTION m57_marker_beroert_prosess()',
            t || '_beroert', t);
    END LOOP;
END $$;

DROP TRIGGER IF EXISTS rekrutteringsprosess_reapes_samlet
    ON rekrutteringsprosess;
CREATE CONSTRAINT TRIGGER rekrutteringsprosess_reapes_samlet
    AFTER INSERT OR UPDATE ON rekrutteringsprosess
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION m57_lagrene_reapes_samlet();
