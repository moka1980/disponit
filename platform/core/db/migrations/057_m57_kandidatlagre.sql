-- 057: M-57s kandidatlagre + kandidatdatagrensen (TTL) — klarsignalet §5.
--
-- Seks lagre bærer kandidatens payload: originaldokument, parset
-- mellomtekst, evalueringsartefakt, intervjuspørsmål, utsendingsdata og
-- av-maskeringstabellen. Ved utløp slettes PAYLOAD i alle seks — i samme
-- transaksjon, aldri ett lager alene — og det som består er rad-ID,
-- tidsstempler, `slettet_ts` og innholdshash. Spesifikasjonen påstår
-- IKKE at hashen er anonym: den sier at payload er slettet og at minimal
-- revisjonsevidens består.
--
-- Fristen er kundevalgt 30–365 døgn (standard 90) og løper fra prosessen
-- LUKKES. Modulen kan ikke forlenge den (§5): `slettefrist_dogn` er
-- immutabel etter INSERT (radvakten under), `lukket_ts` kan bare settes
-- én gang og aldri frem i tid — å tidlegge lukkingen KORTER fristen, å
-- utsette den ville FORLENGET den, og bare den første retningen finnes.
--
-- Eierskap: tabellene og vaktene eies av migrator; prosessfunksjonene
-- eies av `disponit_m37_claimer` — de kaller `krev_tenantkontekst`, som
-- claimeren eier og PUBLIC mistet i 038, og eierens egne kall er den
-- eneste veien inn. Derfor kjører §5–6 under SET LOCAL ROLE, og ALLE
-- rettighetsendringer på de funksjonene står INNE i samme blokk
-- (PUBLIC-EXECUTE-klassen fra #140: REVOKE/GRANT fra en ikke-eier på
-- claimer-eide funksjoner er stille virkningsløse). Tabellrettighetene
-- til runtime SPEILES i migrer.py (RETTIGHETER); kjøreren nullstiller
-- migrator-eide tabellgrants ved hvert deploy, så migrasjonens egne
-- grants er lokal/test-veien, ikke driftssannheten.

-- ------------------------------------------------------------
-- 1. Ankeret: rekrutteringsprosessen. Én per evalueringsoppdrag.
CREATE TABLE rekrutteringsprosess (
    tenant TEXT NOT NULL,
    prosess_id UUID NOT NULL,
    oppdrag_id BIGINT NOT NULL,
    slettefrist_dogn INT NOT NULL DEFAULT 90
        CONSTRAINT prosess_frist_i_spennet
        CHECK (slettefrist_dogn BETWEEN 30 AND 365),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    lukket_ts TIMESTAMPTZ,
    slettet_ts TIMESTAMPTZ,
    CONSTRAINT rekrutteringsprosess_pk PRIMARY KEY (tenant, prosess_id),
    CONSTRAINT prosess_en_per_oppdrag UNIQUE (tenant, oppdrag_id),
    CONSTRAINT prosess_oppdrag_fk FOREIGN KEY (tenant, oppdrag_id)
        REFERENCES oppdrag (tenant, id),
    -- Reaping forutsetter lukking: en prosess som aldri lukket kan ikke
    -- ha fått fristen til å løpe ut.
    CONSTRAINT prosess_reapet_krever_lukket
        CHECK (slettet_ts IS NULL OR lukket_ts IS NOT NULL)
);

-- Radvakten (§5 + port 20): fristen er immutabel, lukking skjer én gang
-- og aldri frem i tid, reap-merket settes én gang. Alt annet avvises —
-- også for eieren; en vakt som bare gjelder de rettighetsløse er ingen
-- vakt (append-only-husformen fra 011/053/056).
CREATE OR REPLACE FUNCTION rekrutteringsprosess_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'rekrutteringsprosess: % avvist — raden består,'
            ' bare payloaden i lagrene reapes', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.prosess_id IS DISTINCT FROM OLD.prosess_id
       OR NEW.oppdrag_id IS DISTINCT FROM OLD.oppdrag_id
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'rekrutteringsprosess: identitetskolonnene er'
            ' immutable' USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Port 20, selve kjernen: INGEN overgang endrer fristen. Ikke
    -- modulen, ikke runtime, ikke eieren — «modulen kan ikke forlenge
    -- frist; ingen hold i v1» (§5).
    IF NEW.slettefrist_dogn IS DISTINCT FROM OLD.slettefrist_dogn THEN
        RAISE EXCEPTION 'rekrutteringsprosess: slettefristen er satt ved'
            ' fødselen og kan ikke endres (klarsignalet §5)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.lukket_ts IS DISTINCT FROM OLD.lukket_ts THEN
        IF OLD.lukket_ts IS NOT NULL THEN
            RAISE EXCEPTION 'rekrutteringsprosess: lukket_ts er alt satt'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- Fristen løper fra lukkingen. En lukking frem i tid ville
        -- skjøvet utløpet — altså forlenget fristen. Bakover korter den
        -- bare, og den retningen er lovlig (og testbar).
        IF NEW.lukket_ts > pg_catalog.now() THEN
            RAISE EXCEPTION 'rekrutteringsprosess: lukket_ts kan ikke stå'
                ' frem i tid — det ville forlenget slettefristen'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    IF NEW.slettet_ts IS DISTINCT FROM OLD.slettet_ts
       AND OLD.slettet_ts IS NOT NULL THEN
        RAISE EXCEPTION 'rekrutteringsprosess: slettet_ts er alt satt'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS rekrutteringsprosess_vakt ON rekrutteringsprosess;
CREATE TRIGGER rekrutteringsprosess_vakt
    BEFORE UPDATE OR DELETE ON rekrutteringsprosess
    FOR EACH ROW EXECUTE FUNCTION rekrutteringsprosess_vakt();
DROP TRIGGER IF EXISTS rekrutteringsprosess_ingen_truncate
    ON rekrutteringsprosess;
CREATE TRIGGER rekrutteringsprosess_ingen_truncate
    BEFORE TRUNCATE ON rekrutteringsprosess
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- ------------------------------------------------------------
-- 2. De seks lagrene. Felles form: payloadkolonnene er nullable, og
-- CHECK-en binder dem til `slettet_ts` BEGGE veier — en levende rad HAR
-- payload, en reapet rad HAR IKKE. `innhold_sha256` består etter reaping
-- (minimal revisjonsevidens, §5).

CREATE TABLE kandidat_originaldokument (
    tenant TEXT NOT NULL,
    prosess_id UUID NOT NULL,
    kandidat_id UUID NOT NULL,
    dokument_id UUID NOT NULL,
    filnavn TEXT,
    innholdstype TEXT,
    dokument BYTEA,
    -- §4: enkeltfilgrensen står også i basen, ikke bare i parseren.
    storrelse_bytes BIGINT NOT NULL,
    innhold_sha256 TEXT NOT NULL,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    slettet_ts TIMESTAMPTZ,
    CONSTRAINT kandidat_originaldokument_pk
        PRIMARY KEY (tenant, prosess_id, kandidat_id, dokument_id),
    CONSTRAINT originaldokument_prosess_fk FOREIGN KEY (tenant, prosess_id)
        REFERENCES rekrutteringsprosess (tenant, prosess_id),
    -- Grensen måles på de LAGREDE bytene, ikke på påstanden om dem
    -- (Codex P2). `storrelse_bytes` kommer fra parseren, og en skriver som
    -- satte 1 kunne lagre et vilkårlig stort dokument — da var «25 MB i
    -- basen» bare parserens tall en gang til. Metadatakolonnen beholdes,
    -- men er bundet til målingen: spriker de, finnes ikke raden.
    CONSTRAINT dokument_enkeltfilgrense
        CHECK (storrelse_bytes >= 0
               AND storrelse_bytes <= 25 * 1024 * 1024
               AND (dokument IS NULL
                    OR (octet_length(dokument) <= 25 * 1024 * 1024
                        AND storrelse_bytes = octet_length(dokument)))),
    -- Filnavnet er persondata så godt som noe (fornavn.etternavn-cv.pdf)
    -- og reapes med innholdet.
    CONSTRAINT originaldokument_payload_folger_slettet
        CHECK ((slettet_ts IS NULL AND dokument IS NOT NULL
                AND filnavn IS NOT NULL AND innholdstype IS NOT NULL)
            OR (slettet_ts IS NOT NULL AND dokument IS NULL
                AND filnavn IS NULL AND innholdstype IS NULL))
);

CREATE TABLE kandidat_parsettekst (
    tenant TEXT NOT NULL,
    prosess_id UUID NOT NULL,
    kandidat_id UUID NOT NULL,
    dokument_id UUID NOT NULL,
    tekst TEXT,
    innhold_sha256 TEXT NOT NULL,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    slettet_ts TIMESTAMPTZ,
    CONSTRAINT kandidat_parsettekst_pk
        PRIMARY KEY (tenant, prosess_id, kandidat_id, dokument_id),
    CONSTRAINT parsettekst_dokument_fk
        FOREIGN KEY (tenant, prosess_id, kandidat_id, dokument_id)
        REFERENCES kandidat_originaldokument
            (tenant, prosess_id, kandidat_id, dokument_id),
    CONSTRAINT parsettekst_payload_folger_slettet
        CHECK ((slettet_ts IS NULL AND tekst IS NOT NULL)
            OR (slettet_ts IS NOT NULL AND tekst IS NULL))
);

CREATE TABLE kandidat_evalueringsartefakt (
    tenant TEXT NOT NULL,
    prosess_id UUID NOT NULL,
    kandidat_id UUID NOT NULL,
    artefakt JSONB,                -- funn, sitater, rangering, begrunnelser
    innhold_sha256 TEXT NOT NULL,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    slettet_ts TIMESTAMPTZ,
    CONSTRAINT kandidat_evalueringsartefakt_pk
        PRIMARY KEY (tenant, prosess_id, kandidat_id),
    CONSTRAINT evalueringsartefakt_prosess_fk
        FOREIGN KEY (tenant, prosess_id)
        REFERENCES rekrutteringsprosess (tenant, prosess_id),
    CONSTRAINT evalueringsartefakt_payload_folger_slettet
        CHECK ((slettet_ts IS NULL AND artefakt IS NOT NULL)
            OR (slettet_ts IS NOT NULL AND artefakt IS NULL))
);

CREATE TABLE kandidat_intervjusporsmal (
    tenant TEXT NOT NULL,
    prosess_id UUID NOT NULL,
    kandidat_id UUID NOT NULL,
    sporsmal JSONB,
    innhold_sha256 TEXT NOT NULL,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    slettet_ts TIMESTAMPTZ,
    CONSTRAINT kandidat_intervjusporsmal_pk
        PRIMARY KEY (tenant, prosess_id, kandidat_id),
    CONSTRAINT intervjusporsmal_prosess_fk
        FOREIGN KEY (tenant, prosess_id)
        REFERENCES rekrutteringsprosess (tenant, prosess_id),
    CONSTRAINT intervjusporsmal_payload_folger_slettet
        CHECK ((slettet_ts IS NULL AND sporsmal IS NOT NULL)
            OR (slettet_ts IS NOT NULL AND sporsmal IS NULL))
);

CREATE TABLE kandidat_utsendingsdata (
    tenant TEXT NOT NULL,
    prosess_id UUID NOT NULL,
    kandidat_id UUID NOT NULL,
    mottaker_ref TEXT,
    flettefelt JSONB,
    innhold_sha256 TEXT NOT NULL,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    slettet_ts TIMESTAMPTZ,
    CONSTRAINT kandidat_utsendingsdata_pk
        PRIMARY KEY (tenant, prosess_id, kandidat_id),
    CONSTRAINT utsendingsdata_prosess_fk
        FOREIGN KEY (tenant, prosess_id)
        REFERENCES rekrutteringsprosess (tenant, prosess_id),
    CONSTRAINT utsendingsdata_payload_folger_slettet
        CHECK ((slettet_ts IS NULL AND mottaker_ref IS NOT NULL
                AND flettefelt IS NOT NULL)
            OR (slettet_ts IS NOT NULL AND mottaker_ref IS NULL
                AND flettefelt IS NULL))
);

CREATE TABLE kandidat_avmaskering (
    tenant TEXT NOT NULL,
    prosess_id UUID NOT NULL,
    kandidat_id UUID NOT NULL,
    felter JSONB,                  -- maskeringstoken -> klartekst
    innhold_sha256 TEXT NOT NULL,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    slettet_ts TIMESTAMPTZ,
    CONSTRAINT kandidat_avmaskering_pk
        PRIMARY KEY (tenant, prosess_id, kandidat_id),
    CONSTRAINT avmaskering_prosess_fk
        FOREIGN KEY (tenant, prosess_id)
        REFERENCES rekrutteringsprosess (tenant, prosess_id),
    CONSTRAINT avmaskering_payload_folger_slettet
        CHECK ((slettet_ts IS NULL AND felter IS NOT NULL)
            OR (slettet_ts IS NOT NULL AND felter IS NULL))
);

-- ------------------------------------------------------------
-- 3. Lagervakten: eneste lovlige UPDATE er reap-overgangen — payload til
-- NULL, `slettet_ts` fra NULL til satt, alt annet uendret. DELETE og
-- TRUNCATE avvises. INSERT slippes gjennom, men bare på en LEVENDE
-- prosess. Én generisk vakt; payloadkolonnene står som
-- trigger-argumenter, så hvert lager navngir sine og resten måles som
-- «uendret» via radens jsonb.
CREATE OR REPLACE FUNCTION m57_kandidatlager_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE nj jsonb; oj jsonb; kol TEXT;
BEGIN
    -- Port 18, INSERT-siden (Codex P1): en reapet prosess tar ikke imot
    -- ny payload. FK-en krever bare at prosessen FINNES, og reaperen
    -- utelukker for alltid en prosess som alt har `slettet_ts` — så en
    -- forsinket eller retriet skriver kunne gjenoppstå persondata på en
    -- reapet prosess, uten noen vei til å slette dem igjen.
    IF TG_OP = 'INSERT' THEN
        IF EXISTS (SELECT 1 FROM public.rekrutteringsprosess p
                    WHERE p.tenant = NEW.tenant
                      AND p.prosess_id = NEW.prosess_id
                      AND p.slettet_ts IS NOT NULL) THEN
            RAISE EXCEPTION '%: prosessen er reapet — payload skrives'
                ' ikke tilbake til en slettet prosess (klarsignalet §5)',
                TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION '%: % avvist — kandidatrader reapes (payload til'
            ' NULL), de slettes aldri som rader', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.slettet_ts IS NOT NULL THEN
        RAISE EXCEPTION '%: raden er alt reapet og immutabel',
            TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
    END IF;
    nj := to_jsonb(NEW); oj := to_jsonb(OLD);
    IF nj->>'slettet_ts' IS NULL THEN
        RAISE EXCEPTION '%: eneste lovlige UPDATE er reap-overgangen'
            ' (slettet_ts settes, payload til NULL)', TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    FOREACH kol IN ARRAY TG_ARGV LOOP
        IF (nj->kol) IS DISTINCT FROM 'null'::jsonb THEN
            RAISE EXCEPTION '%: reaping krever at payloadkolonnen % blir'
                ' NULL', TG_TABLE_NAME, kol
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        nj := nj - kol; oj := oj - kol;
    END LOOP;
    nj := nj - 'slettet_ts'; oj := oj - 'slettet_ts';
    IF nj IS DISTINCT FROM oj THEN
        RAISE EXCEPTION '%: bare payload og slettet_ts endres ved reaping'
            ' — resten av raden er revisjonsevidens', TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

DO $$
DECLARE par RECORD;
BEGIN
    FOR par IN SELECT * FROM (VALUES
        ('kandidat_originaldokument',
         ARRAY['dokument', 'filnavn', 'innholdstype']),
        ('kandidat_parsettekst', ARRAY['tekst']),
        ('kandidat_evalueringsartefakt', ARRAY['artefakt']),
        ('kandidat_intervjusporsmal', ARRAY['sporsmal']),
        ('kandidat_utsendingsdata', ARRAY['mottaker_ref', 'flettefelt']),
        ('kandidat_avmaskering', ARRAY['felter'])
    ) AS v(tab, payload) LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS %I ON %I',
            par.tab || '_vakt', par.tab);
        EXECUTE format(
            'CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE ON %I'
            ' FOR EACH ROW EXECUTE FUNCTION m57_kandidatlager_vakt(%s)',
            par.tab || '_vakt', par.tab,
            (SELECT string_agg(quote_literal(k), ', ')
               FROM unnest(par.payload) AS k));
        EXECUTE format(
            'DROP TRIGGER IF EXISTS %I ON %I',
            par.tab || '_ingen_truncate', par.tab);
        EXECUTE format(
            'CREATE TRIGGER %I BEFORE TRUNCATE ON %I'
            ' FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring()',
            par.tab || '_ingen_truncate', par.tab);
    END LOOP;
END $$;

-- ------------------------------------------------------------
-- 4. Tenant-isolasjon — samme form som 038/056.
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'rekrutteringsprosess', 'kandidat_originaldokument',
        'kandidat_parsettekst', 'kandidat_evalueringsartefakt',
        'kandidat_intervjusporsmal', 'kandidat_utsendingsdata',
        'kandidat_avmaskering'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolasjon ON %I
                USING      (tenant = current_setting(''disponit.tenant'', true))
                WITH CHECK (tenant = current_setting(''disponit.tenant'', true))',
            t);
    END LOOP;
END $$;

-- Reaperen er kryss-tenant og eies av claimeren — og 005s valg gjelder
-- ordrett her: en EKSPLISITT policy for akkurat den rollen, aldri
-- BYPASSRLS (som ville gitt rollen fritak på ALLE tabeller, for alltid,
-- usynlig herfra). Uten den ser reaperens utvalgs-SELECT ingenting:
-- tenant-policyen krever en kontekst, og reaperens definisjon ER at den
-- ikke har noen å arve. Skrivingene forblir tenant-bundet i
-- funksjonsportene (`krev_tenantkontekst` + per-rad-kontekst i reap).
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'rekrutteringsprosess', 'kandidat_originaldokument',
        'kandidat_parsettekst', 'kandidat_evalueringsartefakt',
        'kandidat_intervjusporsmal', 'kandidat_utsendingsdata',
        'kandidat_avmaskering'] LOOP
        EXECUTE format(
            'CREATE POLICY m57_reaper ON %I TO disponit_m37_claimer
                USING (CURRENT_USER = ''disponit_m37_claimer'')
                WITH CHECK (CURRENT_USER = ''disponit_m37_claimer'')', t);
    END LOOP;
END $$;

-- ------------------------------------------------------------
-- 5. Prosessfunksjonene. SECURITY DEFINER, tenant bundet til konteksten,
-- eid av claimeren (se hodet). Eieren av tabellene (migrator) gir
-- claimeren radrettighetene funksjonene trenger — UPDATE er her, men
-- lagervaktene snevrer den til reap-overgangen, også for claimeren.
GRANT SELECT, INSERT, UPDATE ON rekrutteringsprosess,
    kandidat_originaldokument, kandidat_parsettekst,
    kandidat_evalueringsartefakt, kandidat_intervjusporsmal,
    kandidat_utsendingsdata, kandidat_avmaskering
    TO disponit_m37_claimer;

SET LOCAL ROLE disponit_m37_claimer;

-- Én prosess per evalueringsoppdrag; idempotent på identisk frist,
-- materiell konflikt på ulik (056s materialitetsform).
CREATE OR REPLACE FUNCTION opprett_rekrutteringsprosess(
    p_tenant TEXT, p_oppdrag_id BIGINT, p_frist_dogn INT DEFAULT 90)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID; v_frist INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'opprett_rekrutteringsprosess');
    IF NOT EXISTS (
        SELECT 1 FROM public.oppdrag o
         WHERE o.tenant = p_tenant AND o.id = p_oppdrag_id
           AND o.oppdragstype = 'rekruttering.evaluering') THEN
        RAISE EXCEPTION 'rekrutteringsprosess: oppdrag % er ikke en'
            ' rekruttering.evaluering hos %', p_oppdrag_id, p_tenant
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT prosess_id, slettefrist_dogn INTO v_id, v_frist
      FROM public.rekrutteringsprosess
     WHERE tenant = p_tenant AND oppdrag_id = p_oppdrag_id;
    IF v_id IS NOT NULL THEN
        IF v_frist IS DISTINCT FROM p_frist_dogn THEN
            RAISE EXCEPTION 'rekrutteringsprosess: oppdrag % har alt en'
                ' prosess med frist % døgn — fristen kan ikke endres ved'
                ' å «opprette på nytt» (klarsignalet §5)',
                p_oppdrag_id, v_frist
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN v_id;
    END IF;
    v_id := gen_random_uuid();
    INSERT INTO public.rekrutteringsprosess
        (tenant, prosess_id, oppdrag_id, slettefrist_dogn)
    VALUES (p_tenant, v_id, p_oppdrag_id, p_frist_dogn);
    RETURN v_id;
END $$;

-- Lukking starter fristen. Aldri frem i tid (radvakten håndhever det
-- også ved direkte DML); idempotent på identisk tidspunkt.
CREATE OR REPLACE FUNCTION lukk_rekrutteringsprosess(
    p_tenant TEXT, p_prosess_id UUID,
    p_lukket_ts TIMESTAMPTZ DEFAULT now())
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_lukket TIMESTAMPTZ;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'lukk_rekrutteringsprosess');
    SELECT lukket_ts INTO v_lukket FROM public.rekrutteringsprosess
     WHERE tenant = p_tenant AND prosess_id = p_prosess_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'rekrutteringsprosess: % finnes ikke hos %',
            p_prosess_id, p_tenant
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_lukket IS NOT NULL THEN
        IF v_lukket IS DISTINCT FROM p_lukket_ts THEN
            RAISE EXCEPTION 'rekrutteringsprosess: % er alt lukket ved % —'
                ' lukkingen flyttes ikke', p_prosess_id, v_lukket
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN;
    END IF;
    UPDATE public.rekrutteringsprosess
       SET lukket_ts = p_lukket_ts
     WHERE tenant = p_tenant AND prosess_id = p_prosess_id;
END $$;

-- ------------------------------------------------------------
-- 6. Reaperen (§5 + portene 18–19). 038-formen: kryss-tenant-autoriteten
-- er innelukket — intet tenantparameter, utvalget ER predikatet, én rad
-- om gangen med RADENS tenant i konteksten, SKIP LOCKED gjør
-- overlappende kjøringer trygge. Alle seks lagre tømmes i SAMME
-- iterasjon og samme transaksjon som prosessmerket: det finnes ingen vei
-- gjennom denne funksjonen der ett lager reapes alene.
CREATE OR REPLACE FUNCTION reap_kandidatdata(p_grense INT DEFAULT 50)
RETURNS TABLE (tenant TEXT, prosess_id UUID)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_kontekst TEXT; v_naa TIMESTAMPTZ;
BEGIN
    v_kontekst := current_setting('disponit.tenant', true);
    v_naa := pg_catalog.now();
    FOR r IN
        SELECT p.tenant AS t, p.prosess_id AS pid
          FROM public.rekrutteringsprosess p
         WHERE p.lukket_ts IS NOT NULL
           AND p.slettet_ts IS NULL
           AND v_naa > p.lukket_ts
                       + p.slettefrist_dogn * interval '1 day'
         ORDER BY p.lukket_ts
         LIMIT p_grense
         FOR UPDATE OF p SKIP LOCKED
    LOOP
        PERFORM set_config('disponit.tenant', r.t, true);
        UPDATE public.kandidat_originaldokument k
           SET dokument = NULL, filnavn = NULL, innholdstype = NULL,
               slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        UPDATE public.kandidat_parsettekst k
           SET tekst = NULL, slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        UPDATE public.kandidat_evalueringsartefakt k
           SET artefakt = NULL, slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        UPDATE public.kandidat_intervjusporsmal k
           SET sporsmal = NULL, slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        UPDATE public.kandidat_utsendingsdata k
           SET mottaker_ref = NULL, flettefelt = NULL, slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        UPDATE public.kandidat_avmaskering k
           SET felter = NULL, slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        UPDATE public.rekrutteringsprosess p2
           SET slettet_ts = v_naa
         WHERE p2.tenant = r.t AND p2.prosess_id = r.pid;
        tenant := r.t; prosess_id := r.pid;
        RETURN NEXT;
    END LOOP;
    PERFORM set_config('disponit.tenant', coalesce(v_kontekst, ''), true);
END $$;

-- ------------------------------------------------------------
-- 7. Rettigheter. Funksjonsblokka kjører fortsatt SOM CLAIMEREN — det
-- er eierens egne REVOKE/GRANT som gjelder (#140-læren); et RESET før
-- disse linjene hadde gjort dem stille virkningsløse.
REVOKE ALL ON FUNCTION opprett_rekrutteringsprosess(TEXT, BIGINT, INT)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION lukk_rekrutteringsprosess(TEXT, UUID, TIMESTAMPTZ)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION reap_kandidatdata(INT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION opprett_rekrutteringsprosess(TEXT, BIGINT, INT)
    TO disponit;
GRANT EXECUTE ON FUNCTION lukk_rekrutteringsprosess(TEXT, UUID,
    TIMESTAMPTZ) TO disponit;
-- Reaperen er kryss-tenant (038-læren): i et oppsett MED egen timerrolle
-- hører den hjemme der, og web-API-rollen skal ikke ha den. Lokalt/test
-- ER runtime hele plattformen. 038-blokken ORDRETT (Codex P1): et grant
-- som bare slutter å bli gitt er ikke trukket tilbake — finnes
-- timerrollen, REVOKES runtime, ellers ville en kompromittert API-prosess
-- kunne trigge retensjonsarbeid på tvers av alle tenanter.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_domener') THEN
        GRANT EXECUTE ON FUNCTION reap_kandidatdata(INT)
            TO disponit_domener;
        REVOKE EXECUTE ON FUNCTION reap_kandidatdata(INT) FROM disponit;
    ELSE
        GRANT EXECUTE ON FUNCTION reap_kandidatdata(INT) TO disponit;
    END IF;
END $$;

RESET ROLE;

-- Vaktene og tabellene er migrators egne.
REVOKE ALL ON FUNCTION rekrutteringsprosess_vakt() FROM PUBLIC;
REVOKE ALL ON FUNCTION m57_kandidatlager_vakt() FROM PUBLIC;

REVOKE ALL ON rekrutteringsprosess, kandidat_originaldokument,
    kandidat_parsettekst, kandidat_evalueringsartefakt,
    kandidat_intervjusporsmal, kandidat_utsendingsdata,
    kandidat_avmaskering FROM PUBLIC;
-- Runtime skriver lagrene gjennom API-veien (RLS-gated INSERT + SELECT).
-- INGEN UPDATE: den eneste lovlige mutasjonen er reap-overgangen, og den
-- bor i reaperen. INGEN DELETE noensinne.
--
-- ANKERET er unntaket (Codex P1): `rekrutteringsprosess` får KUN SELECT.
-- Et tabell-INSERT der ville vært en vei UTENOM
-- `opprett_rekrutteringsprosess` — vakten er BEFORE UPDATE OR DELETE og
-- ser ingen INSERT, så runtime kunne skrevet en prosess på et oppdrag som
-- ikke er en `rekruttering.evaluering`, eller satt `lukket_ts` frem i tid
-- og dermed skjøvet hele slettefristen ut i det blå. Fødselen går gjennom
-- funksjonen, som eier begge portene.
GRANT SELECT ON rekrutteringsprosess TO disponit;
GRANT SELECT, INSERT ON kandidat_originaldokument,
    kandidat_parsettekst, kandidat_evalueringsartefakt,
    kandidat_intervjusporsmal, kandidat_utsendingsdata,
    kandidat_avmaskering TO disponit;
