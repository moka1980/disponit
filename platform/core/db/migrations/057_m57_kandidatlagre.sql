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

-- Radvakten (§5 + port 20): fødselen skjer på et levende, m57-eid
-- evalueringsoppdrag og alltid ÅPEN, fristen er immutabel, lukking skjer
-- én gang og aldri frem i tid, reap-merket settes én gang. Alt annet
-- avvises — også for eieren; en vakt som bare gjelder de rettighetsløse
-- er ingen vakt (append-only-husformen fra 011/053/056).
CREATE OR REPLACE FUNCTION rekrutteringsprosess_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    -- FØDSELEN måles her, ikke bare i funksjonen (Cursor P2). Vakten var
    -- BEFORE UPDATE OR DELETE, og runtime ble derfor fratatt tabell-INSERT
    -- i forrige runde — men CLAIMEREN må ha INSERT: den er definer for
    -- `opprett_rekrutteringsprosess`. Direkte DML som claimer gikk dermed
    -- utenom hele fødselsporten (oppdragstype, eiermodul, levende status,
    -- åpen fødsel). En vakt som bare gjelder de rettighetsløse er ingen
    -- vakt — samme lærdom som resten av denne funksjonen bygger på.
    --
    -- Porten er den SAMME som funksjonens, med vilje duplisert: funksjonen
    -- eier den låste lesningen og det lesbare utfallet
    -- (`invalid_parameter_value`), vakten er backstoppen som gjelder
    -- ENHVER rolle, også eieren, og svarer i vaktens egen kode.
    IF TG_OP = 'INSERT' THEN
        IF NEW.lukket_ts IS NOT NULL OR NEW.slettet_ts IS NOT NULL THEN
            RAISE EXCEPTION 'rekrutteringsprosess: en prosess fødes ÅPEN —'
                ' lukking og reap-merke er egne, målte overganger'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- `opprettet` er den ANDRE enden av fristen (Cursor P2): reaperens
        -- maks-levetid-arm regner fra `coalesce(lukket_ts, opprettet)`, og
        -- kolonnen er immutabel etter fødselen. En fødsel med `opprettet`
        -- frem i tid ville derfor skjøvet utløpet for en forlatt prosess
        -- stille — nøyaktig den forlengelsen port 20 finnes for å nekte,
        -- bare gjennom den andre kolonnen. Bakover er lovlig: det KORTER
        -- levetiden, og det er retningen §5 tillater.
        IF NEW.opprettet > pg_catalog.now() THEN
            RAISE EXCEPTION 'rekrutteringsprosess: opprettet kan ikke stå'
                ' frem i tid — det ville forlenget maks levetid'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.oppdrag o
             WHERE o.tenant = NEW.tenant AND o.id = NEW.oppdrag_id
               AND o.oppdragstype = 'rekruttering.evaluering'
               AND o.eiermodul = 'm57_ats'
               AND o.status NOT IN ('feilet', 'kansellert')) THEN
            RAISE EXCEPTION 'rekrutteringsprosess: oppdrag % hos % er ikke'
                ' et LEVENDE rekruttering.evaluering-oppdrag eid av'
                ' m57_ats — fødselen går gjennom'
                ' opprett_rekrutteringsprosess', NEW.oppdrag_id, NEW.tenant
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
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
    IF NEW.slettet_ts IS DISTINCT FROM OLD.slettet_ts THEN
        IF OLD.slettet_ts IS NOT NULL THEN
            RAISE EXCEPTION 'rekrutteringsprosess: slettet_ts er alt satt'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- REAP-MERKET ER EN KONKLUSJON, IKKE EN PÅSTAND (Cursor P1).
        -- Reaperen velger bare prosesser med `slettet_ts IS NULL`, så et
        -- merke satt UTEN at lagrene er tømt utelukker prosessen fra
        -- reaping for alltid — payloaden blir stående, og evidensen sier
        -- at den er slettet. Det er den verst tenkelige formen: §5s løfte
        -- brutt og målingen selv gjort blind.
        --
        -- Merket måles derfor mot lagrene, ikke mot den som setter det:
        -- ingen levende payload igjen på prosessen. Reaperen tømmer alle
        -- seks FØR den merker ankeret, i samme transaksjon, så den lovlige
        -- veien er uendret. En rad uten payload har `slettet_ts` satt
        -- (CHECK-en binder de to begge veier), så predikatet er det samme
        -- spørsmålet lagervakten stiller per rad.
        IF EXISTS (SELECT 1 FROM public.kandidat_originaldokument k
                    WHERE k.tenant = NEW.tenant
                      AND k.prosess_id = NEW.prosess_id
                      AND k.slettet_ts IS NULL)
           OR EXISTS (SELECT 1 FROM public.kandidat_parsettekst k
                       WHERE k.tenant = NEW.tenant
                         AND k.prosess_id = NEW.prosess_id
                         AND k.slettet_ts IS NULL)
           OR EXISTS (SELECT 1 FROM public.kandidat_evalueringsartefakt k
                       WHERE k.tenant = NEW.tenant
                         AND k.prosess_id = NEW.prosess_id
                         AND k.slettet_ts IS NULL)
           OR EXISTS (SELECT 1 FROM public.kandidat_intervjusporsmal k
                       WHERE k.tenant = NEW.tenant
                         AND k.prosess_id = NEW.prosess_id
                         AND k.slettet_ts IS NULL)
           OR EXISTS (SELECT 1 FROM public.kandidat_utsendingsdata k
                       WHERE k.tenant = NEW.tenant
                         AND k.prosess_id = NEW.prosess_id
                         AND k.slettet_ts IS NULL)
           OR EXISTS (SELECT 1 FROM public.kandidat_avmaskering k
                       WHERE k.tenant = NEW.tenant
                         AND k.prosess_id = NEW.prosess_id
                         AND k.slettet_ts IS NULL) THEN
            RAISE EXCEPTION 'rekrutteringsprosess: % hos % kan ikke merkes'
                ' reapet mens et av de seks lagrene fortsatt bærer payload'
                ' — merket ville utelukket prosessen fra reaperen for'
                ' alltid (klarsignalet §5)', NEW.prosess_id, NEW.tenant
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS rekrutteringsprosess_vakt ON rekrutteringsprosess;
CREATE TRIGGER rekrutteringsprosess_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON rekrutteringsprosess
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
-- (minimal revisjonsevidens, §5) — og UTLEDES derfor av lagervakten ved
-- INSERT, den mottas ikke fra kalleren: det som overlever payloaden kan
-- ikke være en påstand om den.

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
-- SECURITY DEFINER er INSERT-portens forutsetning, ikke en utvidelse:
-- vakten må LÅSE prosessraden (under), og en radlåsende SELECT krever
-- UPDATE-rettighet på tabellen. Runtime har med vilje bare SELECT på
-- `rekrutteringsprosess` (forrige rundes P1), så en vakt som kjører som
-- kalleren kunne ikke ta låsen. Definer er migrator, som eier tabellene;
-- vakten skriver ingenting og kan bare slippe raden gjennom eller
-- avvise den, og FORCE RLS gjelder også for eieren — tenant-policyen
-- står derfor uendret.
CREATE OR REPLACE FUNCTION m57_kandidatlager_vakt()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE nj jsonb; oj jsonb; kol TEXT; v_slettet TIMESTAMPTZ;
        v_payload jsonb;
BEGIN
    -- Port 18, INSERT-siden (Codex P1): en reapet prosess tar ikke imot
    -- ny payload. FK-en krever bare at prosessen FINNES, og reaperen
    -- utelukker for alltid en prosess som alt har `slettet_ts` — så en
    -- forsinket eller retriet skriver kunne gjenoppstå persondata på en
    -- reapet prosess, uten noen vei til å slette dem igjen.
    --
    -- Lesningen må LÅSE (Codex P1): en ULÅST `EXISTS` måler snapshotet
    -- fra før reaperen committet. Rekkefølgen var da: vakten ser en
    -- levende prosess → FK-sjekken blokkerer på reaperens `FOR UPDATE`
    -- → reaperen committer → FK-en er fortsatt oppfylt, for raden BLIR
    -- stående (den er revisjonsevidens) → payloaden committes under en
    -- prosess som alt er merket slettet, og som reaperen aldri ser igjen.
    -- `FOR SHARE` konflikter med reaperens `FOR UPDATE`, så vakten venter
    -- på samme sted FK-en ville ventet — og leser radens NYE versjon.
    IF TG_OP = 'INSERT' THEN
        SELECT p.slettet_ts INTO v_slettet
          FROM public.rekrutteringsprosess p
         WHERE p.tenant = NEW.tenant AND p.prosess_id = NEW.prosess_id
         FOR SHARE;
        IF v_slettet IS NOT NULL THEN
            RAISE EXCEPTION '%: prosessen er reapet — payload skrives'
                ' ikke tilbake til en slettet prosess (klarsignalet §5)',
                TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- `innhold_sha256` UTLEDES her, den mottas ikke (Codex P2).
        -- Hashen er den ENESTE evidensen som består etter reaping:
        -- payloaden blir NULL, og raden står igjen som revisjonsspor.
        -- Verken en CHECK eller denne vakten målte den mot innholdet, så
        -- en skriver som satte tom, feil eller fremmed streng kunne
        -- korrumpere det sporet PERMANENT — det finnes ingen vei til å
        -- rette det, siden reap-overgangen er den eneste lovlige UPDATE
        -- og resten av raden er immutabel. Samme valg som
        -- `storrelse_bytes = octet_length(dokument)` alt gjør i denne
        -- fila: grensen måles på de LAGREDE bytene, ikke på påstanden om
        -- dem. Payloadkolonnene er trigger-argumentene, så hvert lager
        -- får sin egen kanoniske form uten at vakten kjenner tabellene.
        nj := to_jsonb(NEW);
        v_payload := '{}'::jsonb;
        FOREACH kol IN ARRAY TG_ARGV LOOP
            v_payload := v_payload || jsonb_build_object(kol, nj->kol);
        END LOOP;
        NEW.innhold_sha256 :=
            encode(sha256(convert_to(v_payload::text, 'UTF8')), 'hex');
        -- Finnes ingen (synlig) prosess, avviser FK-en og RLS-en raden
        -- som før; vakten later ikke som den er den porten.
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

-- «ALDRI ETT LAGER ALENE» (port 19, §5) — målt ved COMMIT.
--
-- Lagervakten over ser ÉN RAD. Den kan derfor si at reap-overgangen er
-- lovlig i formen, men ikke at de seks lagrene reapes SAMMEN: det er en
-- påstand om seks tabeller samtidig, og tilstanden er dessuten lovlig
-- MENS reaperen står midt i sin egen transaksjon. Atomisiteten var
-- derfor bare dokumentert i `reap_kandidatdata`, ikke håndhevet — og
-- `disponit_m37_claimer` har UPDATE på alle seks (den må, den er definer
-- for reaperen). Direkte DML som claimer kunne dermed etterlate en varig
-- halvtom prosess: ett lager reapet, resten levende, ankeret uten merke.
-- Ankervakten fanger den motsatte retningen (merke uten tømte lagre);
-- dette er den siste.
--
-- En UTSATT constraint-trigger er porten som kan stille spørsmålet på
-- riktig tidspunkt: den kjører ved COMMIT, når reaperens seks UPDATE-er
-- er ferdige, og den gjelder ENHVER rolle — også claimeren og eieren.
-- Predikatet er tilstanden, ikke hvem som skrev den: for én prosess kan
-- lagrene ikke bære både levende og reapet payload når transaksjonen er
-- over. `EXISTS` på hver arm gjør prisen to indeksoppslag per arm, ikke
-- en telling over hele prosessen.
--
-- INVOKER, ikke definer, og det er et valg: porten skal se NØYAKTIG de
-- radene skriveren selv ser. Som definer ville den lest med migrators
-- øyne, og siden FORCE RLS gjelder også eieren, ville reaperens
-- kryss-tenant-transaksjon gjort den blind for alle prosesser unntatt
-- den siste — en vakt som feiler åpent. Som invoker leser claimeren
-- gjennom sin egen `m57_reaper`-policy og ser hele transaksjonen sin,
-- mens enhver tenant-bundet skriver måles innenfor sin egen kontekst.
-- Rollene som kan UPDATE-e disse tabellene i det hele tatt (claimeren og
-- eieren) har begge SELECT på dem.
CREATE OR REPLACE FUNCTION m57_lagrene_reapes_samlet()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.kandidat_originaldokument k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_parsettekst k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_evalueringsartefakt k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_intervjusporsmal k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_utsendingsdata k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_avmaskering k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NULL)
       AND EXISTS (
        SELECT 1 FROM public.kandidat_originaldokument k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NOT NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_parsettekst k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NOT NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_evalueringsartefakt k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NOT NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_intervjusporsmal k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NOT NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_utsendingsdata k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NOT NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_avmaskering k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NOT NULL) THEN
        RAISE EXCEPTION 'kandidatlagrene: prosess % hos % bærer både'
            ' levende og reapet payload ved COMMIT — de seks lagrene'
            ' reapes SAMLET, aldri ett alene (klarsignalet §5, port 19)',
            NEW.prosess_id, NEW.tenant
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NULL;
END $$;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'kandidat_originaldokument', 'kandidat_parsettekst',
        'kandidat_evalueringsartefakt', 'kandidat_intervjusporsmal',
        'kandidat_utsendingsdata', 'kandidat_avmaskering'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I',
                       t || '_reapes_samlet', t);
        -- Bare UPDATE: en INSERT kan ikke lage blandingen. Lagervakten
        -- avviser INSERT på en reapet prosess, og en prosess der lagrene
        -- er reapet uten at ankeret er merket, er nettopp det denne
        -- porten forbyr — så det finnes ingen tilstand å skrive inn i.
        EXECUTE format(
            'CREATE CONSTRAINT TRIGGER %I AFTER UPDATE ON %I'
            ' DEFERRABLE INITIALLY DEFERRED'
            ' FOR EACH ROW EXECUTE FUNCTION m57_lagrene_reapes_samlet()',
            t || '_reapes_samlet', t);
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
    -- SNAPSHOTKRAVET (Cursor P2) — samme klasse og SAMME ratifiserte form
    -- som 056s `frigi_utsendelse`, `signer_utsendingsliste` og
    -- `opprett_frigivelsesoppdrag`; dette er ikke et nytt formforsøk, men
    -- den samme porten på det gjenstående stedet.
    --
    -- Idempotensløftet («samme oppdrag ⇒ samme prosess-id») er utledet av
    -- en LESNING: `ON CONFLICT DO NOTHING` svelger taperens unik-brudd
    -- uten feil, og gjenlesningen rett etter må se VINNERENS rad. Under
    -- REPEATABLE READ eller SERIALIZABLE står transaksjonens snapshot
    -- fast fra første setning, så gjenlesningen er blind for en samtidig
    -- committet prosess: `v_id` blir NULL, og et helt legitimt retry får
    -- «kunne hverken opprettes eller leses» der kontrakten lover den
    -- samme id-en tilbake. READ COMMITTED er det eneste nivået der hver
    -- setning ser ferske data. `read uncommitted` er med fordi PostgreSQL
    -- BEHANDLER det som READ COMMITTED (nivået finnes bare som synonym).
    IF current_setting('transaction_isolation')
       NOT IN ('read committed', 'read uncommitted') THEN
        RAISE EXCEPTION 'opprett_rekrutteringsprosess: krever READ'
            ' COMMITTED (fikk %) — idempotensløftet er utledet av en'
            ' LESNING etter konflikt, og et fastholdt snapshot gjør den'
            ' blind for en samtidig committet prosess',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    -- MISLYKKET TERMINALSTATUS FØDER INGEN PROSESS (Cursor P2).
    -- Fristen løper fra LUKKINGEN (§5), og lukkingen er noe kjøringen
    -- gjør når den er ferdig. Et `feilet`- eller `kansellert`-oppdrag er
    -- alt over: kjøringen som skulle lukket prosessen kommer aldri, så
    -- persondataene ville blitt liggende til reaperens MAKS LEVETID fra
    -- fødselen i stedet for fristen fra faktisk avslutning — svakere enn
    -- §5 lover, og for data som aldri skulle vært skrevet.
    --
    -- Porten er NEGATIV, ikke `= 'utfort'` som promoteringsvakten (§7d).
    -- Vaktens spørsmål er «kan denne listen promoteres», og da ER
    -- kjøringen ferdig. Dette ankeret fødes MENS kjøringen står på —
    -- modulen claimer oppdraget (`plukket`) og trenger et sted å legge
    -- parset tekst og artefakter der og da. Et `utfort`-krav her ville
    -- betydd at modulen måtte lagre alt den evaluerer ETTER at den var
    -- ferdig å evaluere, altså snudd livsløpet.
    --
    -- Fødselsporten dekker ikke oppdrag som feiler ETTER at prosessen er
    -- født; der er reaperens maks-levetid-arm grensen. Den ekte roten —
    -- å lukke prosessen i SAMME transaksjon som terminalovergangen —
    -- hører til utføreren, som ikke finnes ennå (K1, se PR-tråden).
    -- EIERMODULEN er en del av fødselsporten (Cursor P2). `claim_neste_-
    -- oppdrag` plukker på `oppdrag.eiermodul`, så et oppdrag med riktig
    -- TYPE men feil eier kan aldri claimes av `m57_ats`. Fødtes prosessen
    -- likevel, ville persondataene ligget til reaperens maks levetid på et
    -- oppdrag ingen modul kommer for å lukke — svakere enn §5, for data
    -- som aldri skulle vært skrevet. Kontrakten binder paret ved
    -- opprettelsen (`_eiermodul_for`), så et avvikende par er DML utenom
    -- kontrakten, og det er nettopp da porten har arbeid å gjøre.
    -- LÅST lesning, ikke et ulåst `EXISTS` (Cursor P2) — samme klasse og
    -- samme form som INSERT-vakten mot reaperen. En ulåst sjekk er en
    -- påstand om FORTIDEN: under READ COMMITTED kunne oppdraget gå til
    -- `feilet`/`kansellert` mellom sjekken og INSERT-en, og prosessen ble
    -- født på et oppdrag som alt var terminalt — nøyaktig den tilstanden
    -- porten finnes for å nekte. `FOR SHARE` holder raden mens fødselen
    -- fullføres, og PostgreSQL re-evaluerer predikatet etter låsen, så en
    -- rad som ble terminal under ventingen faller ut av treffet i stedet
    -- for å bli lest fra et gammelt snapshot.
    PERFORM 1 FROM public.oppdrag o
        WHERE o.tenant = p_tenant AND o.id = p_oppdrag_id
          AND o.oppdragstype = 'rekruttering.evaluering'
          AND o.eiermodul = 'm57_ats'
          AND o.status NOT IN ('feilet', 'kansellert')
        FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'rekrutteringsprosess: oppdrag % hos % er ikke et'
            ' LEVENDE rekruttering.evaluering-oppdrag eid av m57_ats — en'
            ' prosess fødes ikke på et oppdrag som er feilet eller'
            ' kansellert (klarsignalet §5: fristen løper fra lukkingen, og'
            ' den kommer aldri), og heller ikke på et oppdrag modulen'
            ' aldri kan claime', p_oppdrag_id, p_tenant
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT prosess_id, slettefrist_dogn INTO v_id, v_frist
      FROM public.rekrutteringsprosess
     WHERE tenant = p_tenant AND oppdrag_id = p_oppdrag_id;
    IF v_id IS NULL THEN
        -- Kappløpet mellom SELECT-en over og INSERT-en her er ekte
        -- (Codex P2): to samtidige retries av samme bestilling rakk
        -- begge forbi lesingen, og taperen fikk en rå `unique_violation`
        -- i stedet for den idempotente returen funksjonen lover.
        -- ON CONFLICT DO NOTHING venter på vinneren i stedet, og
        -- taperen leser vinnerens rad rett etter.
        v_id := gen_random_uuid();
        INSERT INTO public.rekrutteringsprosess
            (tenant, prosess_id, oppdrag_id, slettefrist_dogn)
        VALUES (p_tenant, v_id, p_oppdrag_id, p_frist_dogn)
        ON CONFLICT ON CONSTRAINT prosess_en_per_oppdrag DO NOTHING
        RETURNING prosess_id INTO v_id;
        IF v_id IS NOT NULL THEN
            RETURN v_id;
        END IF;
        SELECT prosess_id, slettefrist_dogn INTO v_id, v_frist
          FROM public.rekrutteringsprosess
         WHERE tenant = p_tenant AND oppdrag_id = p_oppdrag_id;
        IF v_id IS NULL THEN
            RAISE EXCEPTION 'rekrutteringsprosess: oppdrag % hos % kunne'
                ' hverken opprettes eller leses', p_oppdrag_id, p_tenant
                USING ERRCODE = 'unique_violation';
        END IF;
    END IF;
    -- Materialiteten står uendret: SAMME frist er idempotent, en ANNEN
    -- frist er en konflikt — også for kappløpstaperen. «Opprett på nytt»
    -- er ikke en vei rundt §5.
    IF v_frist IS DISTINCT FROM p_frist_dogn THEN
        RAISE EXCEPTION 'rekrutteringsprosess: oppdrag % har alt en'
            ' prosess med frist % døgn — fristen kan ikke endres ved'
            ' å «opprette på nytt» (klarsignalet §5)',
            p_oppdrag_id, v_frist
            USING ERRCODE = 'unique_violation';
    END IF;
    RETURN v_id;
END $$;

-- Lukking starter fristen. Aldri frem i tid (radvakten håndhever det
-- også ved direkte DML); idempotent på identisk tidspunkt.
--
-- Standarden er NULL og ikke `now()` (Codex P2): med `now()` som default
-- fikk hver RETRY et nytt tidspunkt, så den vanligste feilformen som
-- finnes — kallet committet, men svaret gikk tapt — traff
-- «alt lukket ved X, lukkingen flyttes ikke» og fikk `unique_violation`
-- for en operasjon som hadde lykkes. NULL betyr «jeg har ikke noe
-- tidspunkt å insistere på»: er prosessen alt lukket, er retryen
-- idempotent og RØRER IKKE det lagrede tidspunktet; er den åpen, lukkes
-- den nå. Et EKSPLISITT tidspunkt er fortsatt materielt, og et annet
-- eksplisitt tidspunkt er fortsatt en konflikt.
CREATE OR REPLACE FUNCTION lukk_rekrutteringsprosess(
    p_tenant TEXT, p_prosess_id UUID,
    p_lukket_ts TIMESTAMPTZ DEFAULT NULL)
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
        IF p_lukket_ts IS NOT NULL AND v_lukket IS DISTINCT FROM p_lukket_ts
        THEN
            RAISE EXCEPTION 'rekrutteringsprosess: % er alt lukket ved % —'
                ' lukkingen flyttes ikke', p_prosess_id, v_lukket
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN;
    END IF;
    UPDATE public.rekrutteringsprosess
       SET lukket_ts = coalesce(p_lukket_ts, pg_catalog.now())
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
        -- Den FORLATTE prosessen (Codex P1): fristen løper fra lukkingen
        -- (§5), men en kjøring som krasjer eller kanselleres før
        -- `lukk_rekrutteringsprosess` etterlot en prosess som ALDRI ble
        -- lukket — og et predikat på `lukket_ts IS NOT NULL` utelukket
        -- den for alltid. Originaldokumentene og alt avledet ble stående
        -- i det uendelige, uansett hvor ferdig oppdraget var.
        -- Maks levetid er derfor den samme fristen målt fra FØDSELEN:
        -- ingen ny konstant, og strengere enn den lukkede veien (en
        -- prosess som lukkes, får alltid hele fristen fra lukkingen).
        -- Utførelsesfristen er 240 min, så en prosess som står åpen
        -- forbi hele slettefristen er forlatt, ikke i arbeid.
        SELECT p.tenant AS t, p.prosess_id AS pid
          FROM public.rekrutteringsprosess p
         WHERE p.slettet_ts IS NULL
           AND v_naa > coalesce(p.lukket_ts, p.opprettet)
                       + p.slettefrist_dogn * interval '1 day'
         ORDER BY coalesce(p.lukket_ts, p.opprettet)
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
        -- En forlatt prosess lukkes ved FØDSELEN i samme setning som den
        -- reapes: `prosess_reapet_krever_lukket` skal fortsatt holde, og
        -- radvakten godtar nettopp denne retningen (lukking bakover
        -- korter fristen, den forlenger den aldri).
        UPDATE public.rekrutteringsprosess p2
           SET lukket_ts = coalesce(p2.lukket_ts, p2.opprettet),
               slettet_ts = v_naa
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
-- Runtime-grantene står IKKE her (Cursor P2). `disponit` er lokal-/test-
-- navnet på runtime-rollen; `migrer.py` tar navnet som argument og er
-- eneste rettighetskilde. 056 lukket samme feilklasse: på en installasjon
-- uten rollen `disponit` ruller migrasjonen tilbake, og FINNES navnet som
-- en urelatert eller utrangert innlogging, får DEN varig EXECUTE på
-- prosessfødselen og på lukkingen — kjørerens nullstilling gjelder den
-- KONFIGURERTE rollen, ikke alle roller. Grantene ligger på `{rolle}`-form
-- i `migrer.py` (M37_RETTIGHETER_API), speilet av
-- `test_057_rettighetene_er_parameterisert_pa_rollenavnet`.
-- Reaperen er kryss-tenant (038-læren): i et oppsett MED egen timerrolle
-- hører den hjemme der, og web-API-rollen skal ikke ha den. Lokalt/test
-- ER runtime hele plattformen. 038-blokken ORDRETT (Codex P1): et grant
-- som bare slutter å bli gitt er ikke trukket tilbake — finnes
-- timerrollen, REVOKES runtime, ellers ville en kompromittert API-prosess
-- kunne trigge retensjonsarbeid på tvers av alle tenanter.
-- BEGGE armene er vaktet på at rollen `disponit` FINNES (Codex P1).
-- Denne migrasjonens egen rettighetsseksjon sier at runtime-rollen kan
-- hete noe annet — navnet er et argument til `migrer.py` — og PostgreSQL
-- behandler `REVOKE ... FROM <ukjent rolle>` som en FEIL, ikke en no-op.
-- På en installasjon som har timerrollen, men et eget runtime-rollenavn,
-- rullet derfor hele 057 tilbake på en linje som skulle vært virkningsløs.
-- Finnes ikke lokalnavnet, er det heller ingenting å trekke tilbake fra
-- det: reaperen er kryss-tenant og hører til timerrollen, og en
-- parameterisert runtime-rolle skal aldri få den (se testen over).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_domener') THEN
        GRANT EXECUTE ON FUNCTION reap_kandidatdata(INT)
            TO disponit_domener;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
            REVOKE EXECUTE ON FUNCTION reap_kandidatdata(INT) FROM disponit;
        END IF;
    ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        GRANT EXECUTE ON FUNCTION reap_kandidatdata(INT) TO disponit;
    END IF;
END $$;

RESET ROLE;

-- Vaktene og tabellene er migrators egne.
REVOKE ALL ON FUNCTION rekrutteringsprosess_vakt() FROM PUBLIC;
REVOKE ALL ON FUNCTION m57_kandidatlager_vakt() FROM PUBLIC;
REVOKE ALL ON FUNCTION m57_lagrene_reapes_samlet() FROM PUBLIC;

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
-- `opprett_rekrutteringsprosess`: runtime kunne skrevet en prosess på et
-- oppdrag som ikke er en `rekruttering.evaluering`, eller satt `lukket_ts`
-- frem i tid og dermed skjøvet hele slettefristen ut i det blå. Fødselen
-- går gjennom funksjonen, som eier den låste lesningen og det lesbare
-- utfallet.
--
-- Rettigheten er likevel ikke hele porten (Cursor P2): CLAIMEREN må ha
-- INSERT — den er definer for funksjonen — så en rettighetsgrense alene
-- ville sluppet direkte DML som claimer rett forbi fødselsporten. Vakten
-- er derfor BEFORE INSERT OR UPDATE OR DELETE og måler den samme
-- trippelen for enhver rolle, også eieren.
--
-- Tabellgrantene bor av samme grunn som funksjonsgrantene over i
-- `migrer.py` på `{rolle}`-form — `GRANT SELECT ON rekrutteringsprosess`
-- og `GRANT SELECT, INSERT` på de seks lagrene, med ankerets INSERT
-- fortsatt utelatt. Speilet av
-- `test_057_rettighetene_er_parameterisert_pa_rollenavnet`.
