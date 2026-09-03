-- 114: M-44 kampanjeregister v1 — REGISTERET, IKKE UTSENDINGEN.
-- Fem tenant-skopede tabeller, fjorten dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- M-44 ER EN ANNEN FIGUR ENN DE TRE ANDRE I KLYNGE 5. De er manglende
-- VERIFIKATORER — betrodde parter som skal attestere et vilkår. M-44 er
-- den manglende AKTØREN: netthandelsmalen fører modulen som `modul:` på
-- en `auto`-handling, ikke i `verifikatorer`.
--
--     - id: kampanje.send
--       modul: M-44
--       modus: auto
--       dataklasser_tillatt: [offentlig, persondata]
--       grenser:
--         frekvens: {maks: 2, periode_antall: 1, periode_enhet: uker,
--                    grupperingsnokkel: mottaker_id}
--       vilkaar: [{navn: samtykke_gyldig,   verifikator: v_samtykke},
--                 {navn: avmeldingslenke,   verifikator: v_samtykke},
--                 {navn: priser_fra_prisbok, verifikator: v_prisbok}]
--       reversering: {type: kompenserende,
--                     handling: kampanje.send_korreksjon}
--
-- VILKÅRENE HAR VERIFIKATORER SOM FINNES. `v_samtykke` er M-30,
-- `v_prisbok` er M-26 fra klynge 4. Det er HANDLINGEN SELV som mangler
-- en modul — og det er derfor denne modulen er en annen slags mangel.
--
-- v1 SENDER INGENTING.
--
-- DET GJØR TILBAKEHOLDELSEN STERKERE, IKKE SVAKERE. For de tre andre
-- kunne man sagt at modulen bare mangler én evne. Her finnes modulen
-- FOR å sende, og v1 sender null. Det er hele dens grunn til å
-- eksistere som er holdt tilbake.
--
-- OG SE PÅ REVERSERINGEN MALEN FORESLÅR: `kompenserende`, med
-- `kampanje.send_korreksjon`. Botemiddelet for en feilsendt e-post er
-- Å SENDE EN TIL. Det er ikke en reversering — det er en andre e-post
-- til noen som ikke ville ha den første. En utsending er irreversibel
-- på den måten som betyr noe: en e-post kan ikke kalles tilbake, og en
-- for mye er en klage, en avmelding eller et tilsynsspørsmål.
--
-- v1 GJØR ÉN TING: registrerer kampanjen, mottakerne og samtykkets
-- tilstand — og MÅLER frekvenstaket malen alt har satt.
--
-- DOMMENE v1 HVILER PÅ, håndhevet i DATAMODELLEN:
--
--   1. SAMTYKKET ER APPEND-ONLY, og det er ikke en teknisk preferanse.
--      «Hadde vi lov til å sende dette DEN DAGEN» er hele spørsmålet et
--      tilsyn stiller, og et samtykke som kunne oppdateres på stedet
--      ville slettet svaret i samme øyeblikk som spørsmålet ble
--      aktuelt. Den gjeldende tilstanden ER den siste hendelsen.
--
--   2. HVERT SAMTYKKE HAR EN KILDE OG EN KANAL. Hvor det kom fra —
--      avkryssingen i kassa, en preferanseside, en importert liste —
--      avgjør om det er et samtykke i det hele tatt. Et samtykke uten
--      opphav er en påstand, og `samtykke_gyldig` ville hvilt på den.
--
--   3. INGEN KAMPANJE UTEN AVMELDINGSLENKE. `avmeldingslenke` er et
--      eget vilkår i malen, og her er det en NOT NULL-kolonne med en
--      formsjekk. En kampanje uten den kan ikke registreres i det hele
--      tatt — ikke fordi v1 sender, men fordi en kampanje som ikke
--      KUNNE vært sendt lovlig heller ikke skal kunne stå i registeret
--      som om den var klar.
--
--   4. FREKVENSTAKET ER TENANTENS. Malen foreslår to per uke per
--      mottaker; tallet ligger i basen og settes gjennom en dør. En
--      konstant i koden ville vært nøyaktig den fullmakten invarianten
--      `frekvensgrense_hardkodet` forbyr — og malens forslag er et
--      FORSLAG, ikke en grense noen tenant har vedtatt.
--
--   5. ET BRUDD PÅ TAKET ER ET FUNN. Ikke et flagg, ikke en stille
--      utelatelse: en rad noen må se på. Registeret måler mot taket og
--      skriver funnet; det stopper ingen utsending, fordi det ikke
--      finnes noen utsending å stoppe.
--
-- GRENSEN MOT M-6: M-6 eier E-POSTEN som kanal. M-44 eier KAMPANJEN og
-- SAMTYKKET. v1 kobler dem ikke — koblingen ER nettopp utsendingen vi
-- ikke gjør, og en fremmednøkkel mellom registrene ville antydet at
-- den fantes.
--
-- GRENSEN MOT M-30: M-30 eier personvernbehandlingene og er
-- `v_samtykke` i malen. M-44 eier samtykket til MARKEDSFØRING
-- spesifikt, per mottaker og per kampanjekanal. v1 kobler dem ikke;
-- registeret her er grunnlaget M-30 en dag kan attestere PÅ.
--
-- SVEIPEN HAR SIN EGEN ROLLE OG SIN EGEN TIMER (095/100-113):
-- `disponit_kampanjesveip` har nøyaktig ÉN rettighet — EXECUTE på
-- `m44_sveip_kampanjer` — og INGEN tabellrettigheter. Sveipen SENDER
-- INGENTING; den skriver FUNN.
--
-- FORMENE ER HUSETS: tabellene eies av migrator, dørene av NOLOGIN-rollen
-- `disponit_kampanje_eier`, tenant TEXT + RLS ENABLE+FORCE +
-- `tenant_isolasjon`, SP-1 i hver tenantbundet definer, ingen BYPASSRLS.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_kampanje_eier') THEN
        RAISE EXCEPTION 'rollen disponit_kampanje_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_kampanje_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `kampanjegrense` — ÉN per tenant. DOM 4: TAKET ER TENANTENS.
CREATE TABLE kampanjegrense (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- Malen FORESLÅR 2 per uke per mottaker. Standarden er derfor 2 —
    -- men den står her, i tenantens egen rad, ikke i koden.
    maks_per_periode INT NOT NULL DEFAULT 2
        CHECK (maks_per_periode BETWEEN 0 AND 1000),
    -- Perioden i DØGN. Malen sier «1 uke»; 7 er det tallet, uttrykt i
    -- den enheten alt annet i registeret måles i.
    periode_dogn INT NOT NULL DEFAULT 7
        CHECK (periode_dogn BETWEEN 1 AND 3650),
    -- Hvor lenge et samtykke regnes som gyldig uten å ha blitt
    -- bekreftet på nytt. Et samtykke fra 2019 er ikke et samtykke i
    -- dag, og «vi har aldri hørt noe» er ikke en bekreftelse.
    samtykke_gyldig_dogn INT NOT NULL DEFAULT 730
        CHECK (samtykke_gyldig_dogn BETWEEN 1 AND 3650),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT kampanjegrense_pk PRIMARY KEY (tenant)
);

-- `kampanjemottaker` — den vi VILLE sendt til.
CREATE TABLE kampanjemottaker (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    mottaker_id UUID NOT NULL,
    -- Tenantens egen referanse (kunde- eller abonnentnummer). FRI TEKST
    -- og ingen fremmednøkkel: samtykkehistorikken skal kunne stå alene.
    ekstern_ref TEXT NOT NULL CHECK (ekstern_ref ~ '[^[:space:]]'),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    -- KONTAKTPUNKTET LAGRES ALDRI I KLARTEKST (M-42/M-41s form, 110/111).
    -- Maske og SALTET hash holder: registeret trenger å kunne SKILLE to
    -- mottakere og se at samme adresse går igjen, ikke å kjenne den. En
    -- e-postadresse er persondata, og et kampanjeregister er ikke stedet
    -- den skal bo.
    kontakt_maske TEXT NOT NULL CHECK (kontakt_maske ~ '[^[:space:]]'),
    kontakt_hash TEXT NOT NULL CHECK (kontakt_hash ~ '^[0-9a-f]{64}$'),
    -- MOTTAKERENS EGET SALT. Uten det ville to like adresser hos to
    -- tenanter fått samme hash, og registeret blitt et oppslagsverk
    -- over hvem som er kunde hvor.
    hash_salt TEXT NOT NULL
        DEFAULT (gen_random_uuid()::text || gen_random_uuid()::text)
        CHECK (length(hash_salt) >= 32),
    aktiv BOOLEAN NOT NULL DEFAULT true,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT kampanjemottaker_pk PRIMARY KEY (tenant, mottaker_id),
    CONSTRAINT kampanjemottaker_ref_unik UNIQUE (tenant, ekstern_ref)
);
CREATE INDEX kampanjemottaker_aktive
    ON kampanjemottaker (tenant) WHERE aktiv;

-- `samtykkehendelse` — DOM 1 OG 2. HOVEDBOKEN FOR SAMTYKKE.
--
-- Den gjeldende samtykketilstanden er den SISTE raden her. Det finnes
-- ingen `samtykke BOOLEAN`-kolonne noe sted i skjemaet, og det er hele
-- poenget: «hadde vi lov DEN DAGEN» skal kunne besvares i ettertid.
CREATE TABLE samtykkehendelse (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    hendelse_id UUID NOT NULL,
    mottaker_id UUID NOT NULL,
    -- Lukket sett. `trukket` er den viktigste: en avmelding er en
    -- hendelse man REGISTRERER, ikke en rad man sletter.
    tilstand TEXT NOT NULL
        CONSTRAINT samtykkehendelse_tilstand_lukket
        CHECK (tilstand IN ('gitt', 'bekreftet', 'trukket',
                            'utlopt_markert')),
    -- DOM 2: HVOR SAMTYKKET KOM FRA. Avkryssingen i kassa og en
    -- importert liste er ikke samme grunnlag — og en importert liste
    -- er ofte ikke et samtykke i det hele tatt.
    kanal TEXT NOT NULL
        CONSTRAINT samtykkehendelse_kanal_lukket CHECK (kanal IN (
            'kasse', 'preferanseside', 'skjema', 'import', 'manuell',
            'avmeldingslenke')),
    kilde_ref TEXT NOT NULL CHECK (kilde_ref ~ '[^[:space:]]'),
    -- Hva mottakeren faktisk fikk se da hen samtykket. Uten den er
    -- «samtykke til hva» ubesvart.
    formal TEXT NOT NULL CHECK (formal ~ '[^[:space:]]'),
    inntruffet DATE NOT NULL,
    notat TEXT NOT NULL CHECK (notat ~ '[^[:space:]]'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT samtykkehendelse_pk PRIMARY KEY (tenant, hendelse_id),
    CONSTRAINT samtykkehendelse_mottaker_fk
        FOREIGN KEY (tenant, mottaker_id)
        REFERENCES kampanjemottaker (tenant, mottaker_id),
    -- SAMME KILDEHENDELSE REGISTRERES ÉN GANG. En preferanseside som
    -- postes to ganger er ikke to samtykker.
    CONSTRAINT samtykkehendelse_kilde_unik
        UNIQUE (tenant, mottaker_id, kanal, kilde_ref)
);
CREATE INDEX samtykkehendelse_oppslag
    ON samtykkehendelse (tenant, mottaker_id, inntruffet DESC,
                         registrert DESC);

-- `kampanje` — DOM 3. KAMPANJEN, MED SIN AVMELDINGSLENKE.
--
-- `planlagt_sendt` er en DATO, ikke en kø: registeret vet når
-- kampanjen VAR ment å gå, og det er nettopp den datoen frekvenstaket
-- måles på. Ingenting i skjemaet sender noe når den datoen passerer.
CREATE TABLE kampanje (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kampanje_id UUID NOT NULL,
    ekstern_ref TEXT NOT NULL CHECK (ekstern_ref ~ '[^[:space:]]'),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    formal TEXT NOT NULL CHECK (formal ~ '[^[:space:]]'),
    -- DOM 3: AVMELDINGSLENKEN ER PÅKREVD, med en formsjekk. `https://`
    -- og ikke `http://`: en avmeldingslenke over ukryptert forbindelse
    -- lekker at mottakeren fikk kampanjen.
    avmeldingslenke TEXT NOT NULL
        CONSTRAINT kampanje_avmelding_form
        CHECK (avmeldingslenke ~ '^https://[^[:space:]]+$'),
    planlagt_sendt DATE NOT NULL,
    -- INGEN `sendt`-KOLONNE, og det er ikke en forglemmelse: v1 sender
    -- ingenting, og en kolonne som kunne settes ville vært et sted å
    -- late som noe var gjort.
    status TEXT NOT NULL DEFAULT 'registrert'
        CONSTRAINT kampanje_status_lukket
        CHECK (status IN ('registrert', 'avlyst')),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT kampanje_pk PRIMARY KEY (tenant, kampanje_id),
    CONSTRAINT kampanje_ref_unik UNIQUE (tenant, ekstern_ref)
);
CREATE INDEX kampanje_planlagt
    ON kampanje (tenant, planlagt_sendt DESC);

-- `kampanjeplan` — HVEM KAMPANJEN VAR MENT FOR.
--
-- Det er DENNE tabellen frekvenstaket måles på: hvor mange kampanjer
-- en gitt mottaker var satt opp til å få innenfor tenantens periode.
CREATE TABLE kampanjeplan (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kampanje_id UUID NOT NULL,
    mottaker_id UUID NOT NULL,
    lagt_til TIMESTAMPTZ NOT NULL DEFAULT now(),
    lagt_til_av TEXT NOT NULL CHECK (lagt_til_av ~ '[^[:space:]]'),
    CONSTRAINT kampanjeplan_pk PRIMARY KEY (tenant, kampanje_id,
                                            mottaker_id),
    CONSTRAINT kampanjeplan_kampanje_fk
        FOREIGN KEY (tenant, kampanje_id)
        REFERENCES kampanje (tenant, kampanje_id),
    CONSTRAINT kampanjeplan_mottaker_fk
        FOREIGN KEY (tenant, mottaker_id)
        REFERENCES kampanjemottaker (tenant, mottaker_id)
);
CREATE INDEX kampanjeplan_mottaker
    ON kampanjeplan (tenant, mottaker_id);

-- `kampanjefunn` — funnene. Nøklet på mottakeren og typen.
CREATE TABLE kampanjefunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    mottaker_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CONSTRAINT kampanjefunn_type_lukket CHECK (funntype IN (
            'over_frekvensgrense', 'uten_samtykke', 'samtykke_trukket',
            'samtykke_utlopt', 'ingen_grense')),
    -- Hvor mange OVER taket, eller hvor mange døgn over
    -- gyldighetsvinduet. 0 for de som ikke måles i tall.
    over_grense INT,
    -- Hvor mange kampanjer mottakeren var satt opp til i perioden.
    antall_i_periode INT,
    siste_dato DATE,
    grenseversjon INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT kampanjefunn_pk
        PRIMARY KEY (tenant, mottaker_id, funntype),
    CONSTRAINT kampanjefunn_mottaker_fk
        FOREIGN KEY (tenant, mottaker_id)
        REFERENCES kampanjemottaker (tenant, mottaker_id),
    CONSTRAINT kampanjefunn_lukking_helhet CHECK (
        (apen AND lukket_ts IS NULL)
     OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE INDEX kampanjefunn_apne
    ON kampanjefunn (tenant, funntype) WHERE apen;


-- ------------------------------------------------------------
-- 2. Vaktene.
-- ------------------------------------------------------------

CREATE FUNCTION m44_grense_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'kampanjegrense: TRUNCATE avvist — et tømt'
            ' frekvenstak er ingen grense i det hele tatt'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'kampanjegrense: DELETE avvist — et tak endres'
            ' ved å settes, ikke ved å forsvinne'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.versjon <= OLD.versjon THEN
        RAISE EXCEPTION 'kampanjegrense: versjonen må øke (% -> %)',
            OLD.versjon, NEW.versjon
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m44_grense_vakt() FROM PUBLIC;
CREATE TRIGGER m44_grense_vakt
    BEFORE UPDATE OR DELETE ON kampanjegrense
    FOR EACH ROW EXECUTE FUNCTION m44_grense_vakt();
CREATE TRIGGER m44_grense_ingen_truncate
    BEFORE TRUNCATE ON kampanjegrense
    EXECUTE FUNCTION m44_grense_vakt();


CREATE FUNCTION m44_mottaker_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'kampanjemottaker: TRUNCATE avvist —'
            ' mottakerne bærer samtykkehistorikken'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'kampanjemottaker: DELETE avvist — en mottaker'
            ' deaktiveres, hen slettes ikke. Samtykkehistorikken er'
            ' svaret på om vi hadde lov'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.mottaker_id <> OLD.mottaker_id
       OR NEW.ekstern_ref <> OLD.ekstern_ref
       OR NEW.hash_salt <> OLD.hash_salt
       OR NEW.opprettet <> OLD.opprettet THEN
        RAISE EXCEPTION 'kampanjemottaker: identiteten er FROSSET —'
            ' mottaker_id, ekstern_ref, hash_salt og opprettet kan'
            ' ikke endres' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m44_mottaker_vakt() FROM PUBLIC;
CREATE TRIGGER m44_mottaker_vakt
    BEFORE UPDATE OR DELETE ON kampanjemottaker
    FOR EACH ROW EXECUTE FUNCTION m44_mottaker_vakt();
CREATE TRIGGER m44_mottaker_ingen_truncate
    BEFORE TRUNCATE ON kampanjemottaker
    EXECUTE FUNCTION m44_mottaker_vakt();


-- DOM 1: SAMTYKKET OVERSKRIVES ALDRI.
--
-- Modulens skarpeste vakt. «Hadde vi lov til å sende dette DEN DAGEN»
-- er hele spørsmålet et tilsyn stiller, og et samtykke som kunne
-- oppdateres på stedet ville slettet svaret i samme øyeblikk som
-- spørsmålet ble aktuelt.
CREATE FUNCTION m44_samtykke_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'samtykkehendelse: TRUNCATE avvist — en tømt'
            ' samtykkehistorikk er markedsføring ingen kan forsvare'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'samtykkehendelse: DELETE avvist — en'
            ' avmelding er en HENDELSE man registrerer, ikke en rad'
            ' man sletter' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'samtykkehendelse: raden er FROSSET — «hadde'
            ' vi lov den dagen» må kunne besvares i ettertid'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m44_samtykke_vakt() FROM PUBLIC;
CREATE TRIGGER m44_samtykke_vakt
    BEFORE UPDATE OR DELETE ON samtykkehendelse
    FOR EACH ROW EXECUTE FUNCTION m44_samtykke_vakt();
CREATE TRIGGER m44_samtykke_ingen_truncate
    BEFORE TRUNCATE ON samtykkehendelse
    EXECUTE FUNCTION m44_samtykke_vakt();


-- DOM 3: KAMPANJEN BÆRER SIN AVMELDINGSLENKE, og den kan ikke fjernes
-- i ettertid.
CREATE FUNCTION m44_kampanje_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'kampanje: TRUNCATE avvist — kampanjene er det'
            ' frekvenstaket måles på'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'kampanje: DELETE avvist — en kampanje avlyses,'
            ' den slettes ikke'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- KUN STATUS KAN ENDRES, og bare til `avlyst`. Alt annet er
    -- frosset: en kampanje som kunne endre avmeldingslenke eller
    -- formål i ettertid ville gjort registeret verdiløst som bevis.
    IF NEW.kampanje_id <> OLD.kampanje_id
       OR NEW.ekstern_ref <> OLD.ekstern_ref
       OR NEW.formal <> OLD.formal
       OR NEW.avmeldingslenke <> OLD.avmeldingslenke
       OR NEW.planlagt_sendt <> OLD.planlagt_sendt
       OR NEW.opprettet <> OLD.opprettet THEN
        RAISE EXCEPTION 'kampanje: raden er FROSSET — bare status kan'
            ' endres, og bare til avlyst'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status = 'avlyst' THEN
        RAISE EXCEPTION 'kampanje: en avlyst kampanje gjenåpnes ikke'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m44_kampanje_vakt() FROM PUBLIC;
CREATE TRIGGER m44_kampanje_vakt
    BEFORE UPDATE OR DELETE ON kampanje
    FOR EACH ROW EXECUTE FUNCTION m44_kampanje_vakt();
CREATE TRIGGER m44_kampanje_ingen_truncate
    BEFORE TRUNCATE ON kampanje
    EXECUTE FUNCTION m44_kampanje_vakt();


-- PLANEN ER APPEND-ONLY. Hvem en kampanje VAR ment for kan ikke
-- omskrives i ettertid — det er selve grunnlaget frekvenstaket måles
-- på, og en mottaker som kunne fjernes fra en plan ville forsvunnet
-- fra tellingen også.
CREATE FUNCTION m44_plan_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'kampanjeplan: TRUNCATE avvist — planen ER'
            ' grunnlaget frekvenstaket måles på'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'kampanjeplan: DELETE avvist — en mottaker som'
            ' kunne fjernes fra en plan ville forsvunnet fra tellingen'
            ' også. Avlys kampanjen i stedet'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RAISE EXCEPTION 'kampanjeplan: raden er FROSSET'
        USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE ALL ON FUNCTION m44_plan_vakt() FROM PUBLIC;
CREATE TRIGGER m44_plan_vakt
    BEFORE UPDATE OR DELETE ON kampanjeplan
    FOR EACH ROW EXECUTE FUNCTION m44_plan_vakt();
CREATE TRIGGER m44_plan_ingen_truncate
    BEFORE TRUNCATE ON kampanjeplan
    EXECUTE FUNCTION m44_plan_vakt();


CREATE FUNCTION m44_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'kampanjefunn: TRUNCATE avvist — et tømt'
            ' funnregister ser ut som en lydig utsendingsliste'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'kampanjefunn: DELETE avvist — et funn lukkes,'
            ' det slettes ikke'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.forst_sett <> OLD.forst_sett THEN
        RAISE EXCEPTION 'kampanjefunn: forst_sett er FROSSET — hvor'
            ' lenge et funn har stått er halve alvoret'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m44_funn_vakt() FROM PUBLIC;
CREATE TRIGGER m44_funn_vakt
    BEFORE UPDATE OR DELETE ON kampanjefunn
    FOR EACH ROW EXECUTE FUNCTION m44_funn_vakt();
CREATE TRIGGER m44_funn_ingen_truncate
    BEFORE TRUNCATE ON kampanjefunn
    EXECUTE FUNCTION m44_funn_vakt();


-- ------------------------------------------------------------
-- 3. Dørene. SECURITY DEFINER, eid av `disponit_kampanje_eier`, SP-1.
--
--    DET FINNES INGEN DØR SOM SENDER. Ikke `m44_send_kampanje`, ikke
--    `m44_kjor_utsending`, ikke noe som ligner. Modulen finnes for å
--    sende, og v1 sender null — det er hele dens grunn til å eksistere
--    som er holdt tilbake.
-- ------------------------------------------------------------

-- Eieren trenger å kunne kalle SP-1-vakten og å skrive evidens.
GRANT INSERT ON revisjonslogg TO disponit_kampanje_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_kampanje_eier;
RESET ROLE;

SET LOCAL ROLE disponit_kampanje_eier;

-- EVIDENSEN. Modulen skriver i revisjonsloggen som BEVIS på hva den
-- selv gjorde — aldri som en dom om et vilkår.
--
-- DETALJEN BÆRER ALDRI KONTAKTPUNKTET. Revisjonsloggen er bredere
-- lesbar enn kampanjeregisteret, og en e-postadresse som lekker dit har
-- lekket ut av modulen sin.
CREATE FUNCTION m44_evidens(p_tenant TEXT, p_mottaker_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm44_kampanje', 'handling', p_handling,
        'mottaker_id', p_mottaker_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm44_kampanje',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:kampanje', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m44_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;


-- NORMALISERINGEN AV ET KONTAKTPUNKT, ETT STED (110/111s form).
-- Små bokstaver og uten ytterkanter — nok til at samme adresse skrevet
-- på to måter gir samme hash, og ikke mer. Den gjetter ikke.
CREATE FUNCTION m44_normaliser(p_tekst TEXT)
RETURNS TEXT LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT lower(btrim(coalesce(p_tekst, '')));
$$;
REVOKE ALL ON FUNCTION m44_normaliser(TEXT) FROM PUBLIC;


-- FREKVENSTAKET. DOM 4.
CREATE FUNCTION m44_sett_grense(
    p_tenant TEXT, p_maks_per_periode INT, p_periode_dogn INT,
    p_samtykke_gyldig_dogn INT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_sett_grense');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.kampanjegrense
        (tenant, maks_per_periode, periode_dogn, samtykke_gyldig_dogn,
         oppdatert_av)
    VALUES (p_tenant, p_maks_per_periode, p_periode_dogn,
            p_samtykke_gyldig_dogn, p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        maks_per_periode = EXCLUDED.maks_per_periode,
        periode_dogn = EXCLUDED.periode_dogn,
        samtykke_gyldig_dogn = EXCLUDED.samtykke_gyldig_dogn,
        versjon = public.kampanjegrense.versjon + 1,
        oppdatert = now(),
        oppdatert_av = EXCLUDED.oppdatert_av
    RETURNING versjon INTO v_versjon;
    PERFORM public.m44_evidens(p_tenant, NULL, 'kampanjegrense_satt',
        p_aktor, jsonb_build_object('versjon', v_versjon,
                                    'maks', p_maks_per_periode));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION m44_sett_grense(TEXT, INT, INT, INT, TEXT)
    FROM PUBLIC;


-- MOTTAKEREN. Kontaktpunktet går inn ÉN gang, blir maske og saltet
-- hash, og kastes.
CREATE FUNCTION m44_registrer_mottaker(
    p_tenant TEXT, p_mottaker_id UUID, p_ekstern_ref TEXT, p_navn TEXT,
    p_kontakt TEXT, p_aktor TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_norm TEXT;
    v_salt TEXT;
    v_maske TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm44_registrer_mottaker');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    v_norm := public.m44_normaliser(p_kontakt);
    IF length(v_norm) < 3 THEN
        RAISE EXCEPTION 'm44_registrer_mottaker: kontaktpunktet er for'
            ' kort til å kunne maskeres meningsfullt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- MASKEN VISER NOK TIL Å KJENNE IGJEN, ikke nok til å bruke: første
    -- tegn, stjerner, og alt fra snabel-a-en. `k****@example.com` er en
    -- adresse et menneske kjenner igjen og ingen kan sende til.
    v_maske := CASE
        WHEN position('@' IN v_norm) > 1 THEN
            left(v_norm, 1) || '****'
            || substring(v_norm FROM position('@' IN v_norm))
        ELSE left(v_norm, 1) || '****' || right(v_norm, 2)
    END;

    INSERT INTO public.kampanjemottaker
        (tenant, mottaker_id, ekstern_ref, navn, kontakt_maske,
         kontakt_hash, opprettet_av)
    VALUES (p_tenant, p_mottaker_id, btrim(p_ekstern_ref),
            btrim(p_navn), v_maske, repeat('0', 64), p_aktor);

    -- HASHEN REGNES MED RADENS EGET SALT, som ble laget av DEFAULT-en
    -- over. Derfor to steg: saltet finnes ikke før raden gjør det.
    SELECT hash_salt INTO v_salt FROM public.kampanjemottaker
     WHERE tenant = p_tenant AND mottaker_id = p_mottaker_id;
    UPDATE public.kampanjemottaker
       SET kontakt_hash = encode(
           sha256(convert_to(v_salt || v_norm, 'UTF8')), 'hex')
     WHERE tenant = p_tenant AND mottaker_id = p_mottaker_id;

    PERFORM public.m44_evidens(p_tenant, p_mottaker_id,
        'kampanjemottaker_opprettet', p_aktor, '{}'::jsonb);
    RETURN v_maske;
END $$;
REVOKE ALL ON FUNCTION m44_registrer_mottaker(
    TEXT, UUID, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;


CREATE FUNCTION m44_sett_mottakeraktiv(
    p_tenant TEXT, p_mottaker_id UUID, p_aktiv BOOLEAN, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_naa BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm44_sett_mottakeraktiv');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT aktiv INTO v_naa FROM public.kampanjemottaker
     WHERE tenant = p_tenant AND mottaker_id = p_mottaker_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm44_sett_mottakeraktiv: ukjent mottaker %',
            p_mottaker_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_naa = p_aktiv THEN
        RETURN false;
    END IF;
    UPDATE public.kampanjemottaker SET aktiv = p_aktiv
     WHERE tenant = p_tenant AND mottaker_id = p_mottaker_id;
    IF NOT p_aktiv THEN
        UPDATE public.kampanjefunn
           SET apen = false, lukket_ts = now()
         WHERE tenant = p_tenant AND mottaker_id = p_mottaker_id
           AND apen;
    END IF;
    PERFORM public.m44_evidens(p_tenant, p_mottaker_id,
        'kampanjemottaker_aktiv_satt', p_aktor,
        jsonb_build_object('aktiv', p_aktiv));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m44_sett_mottakeraktiv(
    TEXT, UUID, BOOLEAN, TEXT) FROM PUBLIC;


-- SAMTYKKEDØREN. DOM 1 OG 2.
CREATE FUNCTION m44_registrer_samtykke(
    p_tenant TEXT, p_hendelse_id UUID, p_mottaker_id UUID,
    p_tilstand TEXT, p_kanal TEXT, p_kilde_ref TEXT, p_formal TEXT,
    p_inntruffet DATE, p_notat TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_aktiv BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm44_registrer_samtykke');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    -- SAMME DOM SOM 111-113: en hendelse kan ikke inntreffe i framtida.
    -- Sveipen måler mot `current_date`, så en framtidsdatert rad ville
    -- vært den siste for DØREN og usynlig for SVEIPEN.
    IF p_inntruffet IS NULL OR p_inntruffet > current_date THEN
        RAISE EXCEPTION 'm44_registrer_samtykke: et samtykke kan ikke'
            ' inntreffe i framtida (%)', p_inntruffet
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT aktiv INTO v_aktiv FROM public.kampanjemottaker
     WHERE tenant = p_tenant AND mottaker_id = p_mottaker_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm44_registrer_samtykke: ukjent mottaker %',
            p_mottaker_id USING ERRCODE = 'no_data_found';
    END IF;
    -- EN AVMELDING TAS ALLTID IMOT, også fra en deaktivert mottaker.
    -- Å nekte den ville vært å nekte noen å trekke samtykket sitt.
    IF NOT v_aktiv AND p_tilstand <> 'trukket' THEN
        RAISE EXCEPTION 'm44_registrer_samtykke: mottakeren % er'
            ' deaktivert — bare en avmelding tas imot',
            p_mottaker_id USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO public.samtykkehendelse
        (tenant, hendelse_id, mottaker_id, tilstand, kanal, kilde_ref,
         formal, inntruffet, notat, registrert_av)
    VALUES (p_tenant, p_hendelse_id, p_mottaker_id, p_tilstand,
            p_kanal, btrim(p_kilde_ref), btrim(p_formal),
            p_inntruffet, btrim(p_notat), p_aktor);

    PERFORM public.m44_evidens(p_tenant, p_mottaker_id,
        'samtykke_registrert', p_aktor,
        jsonb_build_object('tilstand', p_tilstand, 'kanal', p_kanal));
END $$;
REVOKE ALL ON FUNCTION m44_registrer_samtykke(
    TEXT, UUID, UUID, TEXT, TEXT, TEXT, TEXT, DATE, TEXT, TEXT)
    FROM PUBLIC;


-- KAMPANJEDØREN. DOM 3: AVMELDINGSLENKEN ER PÅKREVD.
CREATE FUNCTION m44_registrer_kampanje(
    p_tenant TEXT, p_kampanje_id UUID, p_ekstern_ref TEXT,
    p_navn TEXT, p_formal TEXT, p_avmeldingslenke TEXT,
    p_planlagt_sendt DATE, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm44_registrer_kampanje');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_avmeldingslenke IS NULL
       OR p_avmeldingslenke !~ '^https://[^[:space:]]+$' THEN
        RAISE EXCEPTION 'm44_registrer_kampanje: avmeldingslenken må'
            ' være en https-URL — en kampanje uten den kunne ikke vært'
            ' sendt lovlig, og skal derfor ikke stå i registeret som'
            ' om den var klar'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.kampanje
        (tenant, kampanje_id, ekstern_ref, navn, formal,
         avmeldingslenke, planlagt_sendt, opprettet_av)
    VALUES (p_tenant, p_kampanje_id, btrim(p_ekstern_ref),
            btrim(p_navn), btrim(p_formal), btrim(p_avmeldingslenke),
            p_planlagt_sendt, p_aktor);
    PERFORM public.m44_evidens(p_tenant, NULL, 'kampanje_registrert',
        p_aktor, jsonb_build_object('kampanje_id', p_kampanje_id));
END $$;
REVOKE ALL ON FUNCTION m44_registrer_kampanje(
    TEXT, UUID, TEXT, TEXT, TEXT, TEXT, DATE, TEXT) FROM PUBLIC;


CREATE FUNCTION m44_avlys_kampanje(
    p_tenant TEXT, p_kampanje_id UUID, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_avlys_kampanje');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT status INTO v_status FROM public.kampanje
     WHERE tenant = p_tenant AND kampanje_id = p_kampanje_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm44_avlys_kampanje: ukjent kampanje %',
            p_kampanje_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_status = 'avlyst' THEN
        RETURN false;
    END IF;
    UPDATE public.kampanje SET status = 'avlyst'
     WHERE tenant = p_tenant AND kampanje_id = p_kampanje_id;
    PERFORM public.m44_evidens(p_tenant, NULL, 'kampanje_avlyst',
        p_aktor, jsonb_build_object('kampanje_id', p_kampanje_id));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m44_avlys_kampanje(TEXT, UUID, TEXT)
    FROM PUBLIC;


-- MOTTAKEREN LEGGES I PLANEN. Det er DETTE frekvenstaket måles på.
--
-- DØREN SENDER INGENTING. Den skriver ned at mottakeren VAR MENT å få
-- kampanjen — og svarer med hvor mange kampanjer hen da står oppført
-- til innenfor tenantens periode, så den som planlegger får vite det
-- MED ÉN GANG og ikke først når sveipen har gått.
CREATE FUNCTION m44_legg_i_plan(
    p_tenant TEXT, p_kampanje_id UUID, p_mottaker_id UUID, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_aktiv BOOLEAN;
    v_status TEXT;
    v_dato DATE;
    v_periode INT;
    v_antall INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_legg_i_plan');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    SELECT aktiv INTO v_aktiv FROM public.kampanjemottaker
     WHERE tenant = p_tenant AND mottaker_id = p_mottaker_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm44_legg_i_plan: ukjent mottaker %',
            p_mottaker_id USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_aktiv THEN
        RAISE EXCEPTION 'm44_legg_i_plan: mottakeren % er deaktivert',
            p_mottaker_id USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- LÅSER KAMPANJERADEN (CodeRabbit). Uten låsen kunne en samtidig
    -- `m44_avlys_kampanje` committet mellom denne lesningen og
    -- innsettingen under — og en mottaker ville blitt stående i planen
    -- til en AVLYST kampanje. `kampanjeplan` er append-only, så raden
    -- kunne ikke fjernes igjen.
    --
    -- LÅSREKKEFØLGEN ER MOTTAKER FØR KAMPANJE, og ingen annen dør
    -- låser dem motsatt vei: `m44_avlys_kampanje` tar bare kampanjen,
    -- de tre andre bare mottakeren. Ingen vranglås.
    SELECT status, planlagt_sendt INTO v_status, v_dato
      FROM public.kampanje
     WHERE tenant = p_tenant AND kampanje_id = p_kampanje_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm44_legg_i_plan: ukjent kampanje %',
            p_kampanje_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_status = 'avlyst' THEN
        RAISE EXCEPTION 'm44_legg_i_plan: kampanjen % er avlyst',
            p_kampanje_id USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO public.kampanjeplan
        (tenant, kampanje_id, mottaker_id, lagt_til_av)
    VALUES (p_tenant, p_kampanje_id, p_mottaker_id, p_aktor);

    SELECT coalesce(g.periode_dogn, 7) INTO v_periode
      FROM public.kampanjegrense g WHERE g.tenant = p_tenant;
    v_periode := coalesce(v_periode, 7);
    SELECT count(*) INTO v_antall
      FROM public.kampanjeplan pl
      JOIN public.kampanje k ON k.tenant = pl.tenant
       AND k.kampanje_id = pl.kampanje_id
     WHERE pl.tenant = p_tenant AND pl.mottaker_id = p_mottaker_id
       AND k.status <> 'avlyst'
       AND k.planlagt_sendt > v_dato - v_periode
       AND k.planlagt_sendt <= v_dato;

    PERFORM public.m44_evidens(p_tenant, p_mottaker_id,
        'lagt_i_kampanjeplan', p_aktor,
        jsonb_build_object('kampanje_id', p_kampanje_id,
                           'i_periode', v_antall));
    RETURN v_antall;
END $$;
REVOKE ALL ON FUNCTION m44_legg_i_plan(TEXT, UUID, UUID, TEXT)
    FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Lesedørene.
-- ------------------------------------------------------------

CREATE FUNCTION m44_grensene(p_tenant TEXT)
RETURNS TABLE (maks_per_periode INT, periode_dogn INT,
               samtykke_gyldig_dogn INT, versjon INT,
               oppdatert TIMESTAMPTZ, oppdatert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_grensene');
    RETURN QUERY
    SELECT g.maks_per_periode, g.periode_dogn, g.samtykke_gyldig_dogn,
           g.versjon, g.oppdatert, g.oppdatert_av
      FROM public.kampanjegrense g WHERE g.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m44_grensene(TEXT) FROM PUBLIC;


-- SAMTYKKET SLIK DET STO EN GITT DAG. DOM 1s hele poeng.
--
-- «Hadde vi lov til å sende dette DEN DAGEN» besvares her, og bare
-- her: den siste hendelsen med `inntruffet <= p_dag`.
CREATE FUNCTION m44_samtykke_paa_dato(
    p_tenant TEXT, p_mottaker_id UUID, p_dag DATE)
RETURNS TABLE (hendelse_id UUID, tilstand TEXT, kanal TEXT,
               kilde_ref TEXT, formal TEXT, inntruffet DATE,
               registrert TIMESTAMPTZ, registrert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm44_samtykke_paa_dato');
    RETURN QUERY
    SELECT s.hendelse_id, s.tilstand, s.kanal, s.kilde_ref, s.formal,
           s.inntruffet, s.registrert, s.registrert_av
      FROM public.samtykkehendelse s
     WHERE s.tenant = p_tenant AND s.mottaker_id = p_mottaker_id
       AND s.inntruffet <= p_dag
     ORDER BY s.inntruffet DESC, s.registrert DESC
     LIMIT 1;
END $$;
REVOKE ALL ON FUNCTION m44_samtykke_paa_dato(TEXT, UUID, DATE)
    FROM PUBLIC;


CREATE FUNCTION m44_samtykkehistorikken(
    p_tenant TEXT, p_mottaker_id UUID, p_grense INT)
RETURNS TABLE (hendelse_id UUID, tilstand TEXT, kanal TEXT,
               kilde_ref TEXT, formal TEXT, inntruffet DATE,
               notat TEXT, registrert TIMESTAMPTZ, registrert_av TEXT,
               endret BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm44_samtykkehistorikken');
    RETURN QUERY
    WITH rader AS (
        SELECT s.*,
               lag(s.tilstand) OVER (ORDER BY s.inntruffet,
                                              s.registrert) AS forrige
          FROM public.samtykkehendelse s
         WHERE s.tenant = p_tenant AND s.mottaker_id = p_mottaker_id)
    SELECT r.hendelse_id, r.tilstand, r.kanal, r.kilde_ref, r.formal,
           r.inntruffet, r.notat, r.registrert, r.registrert_av,
           r.forrige IS NOT NULL AND r.forrige IS DISTINCT FROM r.tilstand
      FROM rader r
     ORDER BY r.inntruffet DESC, r.registrert DESC
     LIMIT greatest(p_grense, 1);
END $$;
REVOKE ALL ON FUNCTION m44_samtykkehistorikken(TEXT, UUID, INT)
    FROM PUBLIC;


CREATE FUNCTION m44_kampanjene(p_tenant TEXT, p_grense INT)
RETURNS TABLE (kampanje_id UUID, ekstern_ref TEXT, navn TEXT,
               formal TEXT, avmeldingslenke TEXT,
               planlagt_sendt DATE, status TEXT, mottakere BIGINT,
               opprettet TIMESTAMPTZ, opprettet_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_kampanjene');
    RETURN QUERY
    SELECT k.kampanje_id, k.ekstern_ref, k.navn, k.formal,
           k.avmeldingslenke, k.planlagt_sendt, k.status,
           (SELECT count(*) FROM public.kampanjeplan pl
             WHERE pl.tenant = p_tenant
               AND pl.kampanje_id = k.kampanje_id),
           k.opprettet, k.opprettet_av
      FROM public.kampanje k
     WHERE k.tenant = p_tenant
     ORDER BY k.planlagt_sendt DESC, k.opprettet DESC
     LIMIT greatest(p_grense, 1);
END $$;
REVOKE ALL ON FUNCTION m44_kampanjene(TEXT, INT) FROM PUBLIC;


-- FUNNKANDIDATENE. Delt av sveipen og av testene (110-113s form).
--
-- Fem funntyper:
--   over_frekvensgrense — flere kampanjer i perioden enn taket
--   uten_samtykke       — ingen samtykkehendelse i det hele tatt
--   samtykke_trukket    — siste hendelse er en avmelding
--   samtykke_utlopt     — samtykket er eldre enn gyldighetsvinduet
--   ingen_grense        — tenanten har ikke satt taket sitt
--
-- DE TRE SAMTYKKEFUNNENE GJELDER BARE MOTTAKERE SOM STÅR I EN PLAN.
-- En mottaker ingen har tenkt å sende til er ikke et problem, og et
-- funnregister som listet hver kunde uten nyhetsbrevsamtykke ville
-- vært ubrukelig fra første natt.
CREATE FUNCTION m44_funnkandidater(p_tenant TEXT, p_dag DATE)
RETURNS TABLE (mottaker_id UUID, funntype TEXT, over_grense INT,
               antall_i_periode INT, siste_dato DATE,
               grenseversjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_funnkandidater');
    RETURN QUERY
    WITH grense AS (
        SELECT g.maks_per_periode, g.periode_dogn,
               g.samtykke_gyldig_dogn, g.versjon
          FROM public.kampanjegrense g WHERE g.tenant = p_tenant),
    aktive AS (
        SELECT m.mottaker_id FROM public.kampanjemottaker m
         WHERE m.tenant = p_tenant AND m.aktiv),
    -- PLANLAGTE KAMPANJER PER MOTTAKER, avlyste holdt utenfor: en
    -- avlyst kampanje går ikke, og skal ikke telle mot taket.
    planlagt AS (
        SELECT pl.mottaker_id, k.planlagt_sendt
          FROM public.kampanjeplan pl
          JOIN public.kampanje k ON k.tenant = pl.tenant
           AND k.kampanje_id = pl.kampanje_id
          JOIN aktive a ON a.mottaker_id = pl.mottaker_id
         WHERE pl.tenant = p_tenant AND k.status <> 'avlyst'
           AND k.planlagt_sendt <= p_dag),
    -- FREKVENSEN MÅLES I ET GLIDENDE VINDU per kampanjedato: for hver
    -- planlagt kampanje telles hvor mange andre som traff samme
    -- mottaker i perioden fram til den. Et fast kalendervindu ville
    -- sluppet gjennom to på søndag og to på mandag.
    frekvens AS (
        SELECT p.mottaker_id, p.planlagt_sendt,
               (SELECT count(*) FROM planlagt p2
                 WHERE p2.mottaker_id = p.mottaker_id
                   AND p2.planlagt_sendt
                       > p.planlagt_sendt - g.periode_dogn
                   AND p2.planlagt_sendt <= p.planlagt_sendt) AS n
          FROM planlagt p CROSS JOIN grense g),
    siste_samtykke AS (
        SELECT DISTINCT ON (s.mottaker_id)
               s.mottaker_id, s.tilstand, s.inntruffet
          FROM public.samtykkehendelse s
         WHERE s.tenant = p_tenant AND s.inntruffet <= p_dag
         ORDER BY s.mottaker_id, s.inntruffet DESC, s.registrert DESC),
    -- SISTE BEKREFTELSE, for gyldighetsvinduet. `gitt` og `bekreftet`
    -- teller; `trukket` og `utlopt_markert` gjør det ikke.
    siste_ja AS (
        SELECT DISTINCT ON (s.mottaker_id)
               s.mottaker_id, s.inntruffet
          FROM public.samtykkehendelse s
         WHERE s.tenant = p_tenant AND s.inntruffet <= p_dag
           AND s.tilstand IN ('gitt', 'bekreftet')
         ORDER BY s.mottaker_id, s.inntruffet DESC, s.registrert DESC),
    -- MOTTAKERE NOEN FAKTISK HAR TENKT Å SENDE TIL.
    i_plan AS (
        SELECT DISTINCT p.mottaker_id, max(p.planlagt_sendt) AS siste
          FROM planlagt p GROUP BY p.mottaker_id)
    SELECT s.mottaker_id, s.funntype, max(s.over_grense)::INT,
           max(s.antall_i_periode)::INT, max(s.siste_dato),
           max(s.grenseversjon)::INT
      FROM (
        -- Ingen grense satt: DET er funnet, og de andre måles ikke.
        SELECT a.mottaker_id, 'ingen_grense'::TEXT, 0, 0,
               NULL::DATE, NULL::INT
          FROM aktive a
         WHERE NOT EXISTS (SELECT 1 FROM grense)
        UNION ALL
        -- DOM 5: over taket.
        SELECT f.mottaker_id, 'over_frekvensgrense'::TEXT,
               (max(f.n) - g.maks_per_periode)::INT,
               max(f.n)::INT, max(f.planlagt_sendt), g.versjon
          FROM frekvens f CROSS JOIN grense g
         WHERE f.n > g.maks_per_periode
         GROUP BY f.mottaker_id, g.maks_per_periode, g.versjon
        UNION ALL
        -- Planlagt, men uten et eneste samtykke.
        SELECT ip.mottaker_id, 'uten_samtykke'::TEXT, 0, 0,
               ip.siste, g.versjon
          FROM i_plan ip CROSS JOIN grense g
         WHERE NOT EXISTS (SELECT 1 FROM siste_samtykke ss
                            WHERE ss.mottaker_id = ip.mottaker_id)
        UNION ALL
        -- Planlagt, men samtykket er trukket.
        SELECT ip.mottaker_id, 'samtykke_trukket'::TEXT, 0, 0,
               ip.siste, g.versjon
          FROM i_plan ip CROSS JOIN grense g
          JOIN siste_samtykke ss ON ss.mottaker_id = ip.mottaker_id
         WHERE ss.tilstand IN ('trukket', 'utlopt_markert')
        UNION ALL
        -- Planlagt, samtykket står, men det er for gammelt.
        SELECT ip.mottaker_id, 'samtykke_utlopt'::TEXT,
               ((p_dag - sj.inntruffet)
                - g.samtykke_gyldig_dogn)::INT, 0,
               ip.siste, g.versjon
          FROM i_plan ip CROSS JOIN grense g
          JOIN siste_samtykke ss ON ss.mottaker_id = ip.mottaker_id
          JOIN siste_ja sj ON sj.mottaker_id = ip.mottaker_id
         WHERE ss.tilstand IN ('gitt', 'bekreftet')
           AND (p_dag - sj.inntruffet) > g.samtykke_gyldig_dogn
    ) s (mottaker_id, funntype, over_grense, antall_i_periode,
         siste_dato, grenseversjon)
     GROUP BY s.mottaker_id, s.funntype;
END $$;
REVOKE ALL ON FUNCTION m44_funnkandidater(TEXT, DATE) FROM PUBLIC;


-- OVERSIKTEN. Sammendraget flaten åpner på.
CREATE FUNCTION m44_kampanjestatus(p_tenant TEXT)
RETURNS TABLE (mottakere BIGINT, aktive BIGINT, med_samtykke BIGINT,
               kampanjer BIGINT, planlagte BIGINT, apne_funn BIGINT,
               apne_over_tak BIGINT, har_grense BOOLEAN,
               grenseversjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_kampanjestatus');
    RETURN QUERY
    SELECT (SELECT count(*) FROM public.kampanjemottaker m
             WHERE m.tenant = p_tenant),
           (SELECT count(*) FROM public.kampanjemottaker m
             WHERE m.tenant = p_tenant AND m.aktiv),
           (SELECT count(DISTINCT s.mottaker_id)
              FROM public.samtykkehendelse s
             WHERE s.tenant = p_tenant
               AND s.tilstand IN ('gitt', 'bekreftet')),
           (SELECT count(*) FROM public.kampanje k
             WHERE k.tenant = p_tenant),
           (SELECT count(*) FROM public.kampanje k
             WHERE k.tenant = p_tenant AND k.status = 'registrert'),
           (SELECT count(*) FROM public.kampanjefunn f
             WHERE f.tenant = p_tenant AND f.apen),
           (SELECT count(*) FROM public.kampanjefunn f
             WHERE f.tenant = p_tenant AND f.apen
               AND f.funntype = 'over_frekvensgrense'),
           EXISTS (SELECT 1 FROM public.kampanjegrense g
                    WHERE g.tenant = p_tenant),
           (SELECT g.versjon FROM public.kampanjegrense g
             WHERE g.tenant = p_tenant);
END $$;
REVOKE ALL ON FUNCTION m44_kampanjestatus(TEXT) FROM PUBLIC;


CREATE FUNCTION m44_mottakerne(p_tenant TEXT, p_grense INT)
RETURNS TABLE (mottaker_id UUID, ekstern_ref TEXT, navn TEXT,
               kontakt_maske TEXT, aktiv BOOLEAN, tilstand TEXT,
               kanal TEXT, siste_samtykke DATE, i_planer BIGINT,
               apne_funn TEXT[])
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_mottakerne');
    RETURN QUERY
    WITH siste AS (
        SELECT DISTINCT ON (s.mottaker_id)
               s.mottaker_id, s.tilstand, s.kanal, s.inntruffet
          FROM public.samtykkehendelse s
         WHERE s.tenant = p_tenant
         ORDER BY s.mottaker_id, s.inntruffet DESC, s.registrert DESC)
    SELECT m.mottaker_id, m.ekstern_ref, m.navn, m.kontakt_maske,
           m.aktiv, s.tilstand, s.kanal, s.inntruffet,
           (SELECT count(*) FROM public.kampanjeplan pl
             WHERE pl.tenant = p_tenant
               AND pl.mottaker_id = m.mottaker_id),
           (SELECT coalesce(array_agg(f.funntype ORDER BY f.funntype),
                            ARRAY[]::TEXT[])
              FROM public.kampanjefunn f
             WHERE f.tenant = p_tenant
               AND f.mottaker_id = m.mottaker_id AND f.apen)
      FROM public.kampanjemottaker m
      LEFT JOIN siste s ON s.mottaker_id = m.mottaker_id
     WHERE m.tenant = p_tenant
     ORDER BY m.aktiv DESC, m.ekstern_ref
     LIMIT greatest(p_grense, 1);
END $$;
REVOKE ALL ON FUNCTION m44_mottakerne(TEXT, INT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 5. SVEIPEN. Kryss-tenant, egen rolle, INGEN tabellrettigheter.
-- ------------------------------------------------------------
--
-- SVEIPEN SENDER INGENTING. Den leser registeret, regner ut hvem som
-- er over tenantens eget tak eller mangler et gyldig samtykke, og
-- skriver FUNN. Det er hele mandatet.
CREATE FUNCTION m44_sveip_kampanjer(p_grense INT DEFAULT 500)
RETURNS TABLE (tenanter INT, nye_funn INT, oppdaterte_funn INT,
               lukkede_funn INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_liste TEXT[];
    v_t TEXT;
    v_dag DATE := current_date;
    v_tenanter INT := 0;
    v_nye INT := 0;
    v_oppdaterte INT := 0;
    v_lukket INT := 0;
    v_n INT;
    v_m INT;
BEGIN
    IF coalesce(current_setting('disponit.tenant', true), '') <> '' THEN
        RAISE EXCEPTION 'm44_sveip_kampanjer: KRYSS-TENANT — kall den'
            ' uten tenantkontekst'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- MATERIALISERT FØR LØKKEN (111-113s form). En `FOR ... IN SELECT`
    -- er en LAT markør: `set_config` inne i løkka ville endret nettopp
    -- den RLS-konteksten markøren fortsatt hentet rader gjennom.
    SELECT array_agg(DISTINCT m.tenant ORDER BY m.tenant)
      INTO v_liste FROM public.kampanjemottaker m WHERE m.aktiv;
    v_liste := (SELECT array_agg(x ORDER BY x) FROM unnest(
        coalesce(v_liste, ARRAY[]::TEXT[])) WITH ORDINALITY AS u(x, i)
        WHERE u.i <= greatest(p_grense, 1));

    FOREACH v_t IN ARRAY coalesce(v_liste, ARRAY[]::TEXT[])
    LOOP
        v_tenanter := v_tenanter + 1;
        PERFORM set_config('disponit.tenant', v_t, true);

        WITH kand AS (
            SELECT * FROM public.m44_funnkandidater(v_t, v_dag)),
        skrevet AS (
            INSERT INTO public.kampanjefunn
                (tenant, mottaker_id, funntype, over_grense,
                 antall_i_periode, siste_dato, grenseversjon)
            SELECT v_t, k.mottaker_id, k.funntype,
                   greatest(k.over_grense, 0), k.antall_i_periode,
                   k.siste_dato, k.grenseversjon
              FROM kand k
            ON CONFLICT (tenant, mottaker_id, funntype) DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                antall_i_periode = EXCLUDED.antall_i_periode,
                siste_dato = EXCLUDED.siste_dato,
                grenseversjon = EXCLUDED.grenseversjon,
                sist_sett_sveip = now(),
                apen = true,
                lukket_ts = NULL
            RETURNING (xmax = 0) AS var_ny)
        -- BEGGE AKKUMULERES over tenantene (112s CodeRabbit-lærdom).
        SELECT count(*) FILTER (WHERE var_ny),
               count(*) FILTER (WHERE NOT var_ny)
          INTO v_n, v_m FROM skrevet;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_m, 0);

        WITH kand AS (
            SELECT * FROM public.m44_funnkandidater(v_t, v_dag)),
        lukket AS (
            UPDATE public.kampanjefunn f
               SET apen = false, lukket_ts = now()
             WHERE f.tenant = v_t AND f.apen
               AND NOT EXISTS (
                   SELECT 1 FROM kand k
                    WHERE k.mottaker_id = f.mottaker_id
                      AND k.funntype = f.funntype)
            RETURNING 1)
        SELECT count(*) INTO v_n FROM lukket;
        v_lukket := v_lukket + coalesce(v_n, 0);

        PERFORM set_config('disponit.tenant', '', true);
    END LOOP;

    RETURN QUERY SELECT v_tenanter, v_nye, v_oppdaterte, v_lukket;
END $$;
REVOKE ALL ON FUNCTION m44_sveip_kampanjer(INT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 6. RLS. ENABLE + FORCE på alle fem, `tenant_isolasjon` på hver.
-- ------------------------------------------------------------

RESET ROLE;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['kampanjegrense', 'kampanjemottaker',
                             'samtykkehendelse', 'kampanje',
                             'kampanjeplan', 'kampanjefunn']
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL'
                       ' SECURITY', t);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL'
                       ' SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolasjon ON public.%I'
                       ' USING (tenant = current_setting('
                       '''disponit.tenant'', true))'
                       ' WITH CHECK (tenant = current_setting('
                       '''disponit.tenant'', true))', t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO'
                       ' disponit_kampanje_eier', t);
    END LOOP;
END $$;

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — tre gjerder
-- (111-113s form): bare på MOTTAKERTABELLEN, bare FOR SELECT, bare til
-- eieren, og bare når ingen tenantkontekst står.
CREATE POLICY m44_sveip_tenantliste ON kampanjemottaker
    FOR SELECT TO disponit_kampanje_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- SAMTYKKET OG PLANEN FÅR IKKE UPDATE, heller ikke for eieren. Vaktene
-- over stanser den som likevel skulle prøve; dette gjerdet stanser
-- forsøket før det når vakten. To gjerder, som i 110-113.
REVOKE UPDATE ON public.samtykkehendelse FROM disponit_kampanje_eier;
REVOKE UPDATE ON public.kampanjeplan FROM disponit_kampanje_eier;


-- ------------------------------------------------------------
-- 7. Rettighetene. SP-7: kjøretidsrollen får INGEN tabellrettigheter.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_kampanje_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_kampanjestatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_mottakerne(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_kampanjene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m44_samtykkehistorikken(TEXT, UUID, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m44_samtykke_paa_dato(TEXT, UUID, DATE) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_grensene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_funnkandidater(TEXT,'
            ' DATE) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m44_sett_grense(TEXT, INT, INT, INT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m44_registrer_mottaker(TEXT, UUID, TEXT, TEXT, TEXT,'
            ' TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m44_registrer_samtykke(TEXT, UUID, UUID, TEXT, TEXT,'
            ' TEXT, TEXT, DATE, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m44_registrer_kampanje(TEXT, UUID, TEXT, TEXT, TEXT,'
            ' TEXT, DATE, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m44_avlys_kampanje(TEXT, UUID, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m44_legg_i_plan(TEXT, UUID, UUID, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m44_sett_mottakeraktiv(TEXT, UUID, BOOLEAN, TEXT)'
            ' TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_kampanjesveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_sveip_kampanjer(INT)'
            ' TO disponit_kampanjesveip';
    END IF;
END $$;

REVOKE EXECUTE ON FUNCTION m44_sveip_kampanjer(INT) FROM disponit;

RESET ROLE;
