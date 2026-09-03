-- 112: M-19 adressevalidering v1 — REGISTERET, IKKE OPPSLAGET.
-- Fem tenant-skopede tabeller, fjorten dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- NUMMERET ER EN RETTING. Netthandelsmalen skrev «M-11
-- adressevalidering», men M-11 ER PLATTFORMENS SELVTEST (091). Malen
-- er rettet til M-19 i klyngefundamentet, og
-- `test_hvert_modulnummer_en_mal_navngir_finnes_i_katalogen` gjør en
-- gjentakelse rød. Rollekommentarene i `oppsett-postgresql.sh` bar
-- fortsatt det gamle nummeret; de er rettet i samme PR som denne fila.
--
-- V1-AVGRENSNINGEN, ORDRETT FRA POLICYEN VI SENDER UT: netthandelsmalen
-- navngir denne modulen som verifikatoren `v_adresse`, betrodd for
-- vilkåret `adresse_validert`. Og M-25s auto-handling venter på det:
--
--     - id: ordre.bekreft_og_fakturer
--       modus: auto
--       vilkaar: [{navn: betaling_autorisert, verifikator: v_betaling},
--                 {navn: adresse_validert,    verifikator: v_adresse}]
--
-- v1 SLÅR INGENTING OPP EKSTERNT OG ATTESTERER INGENTING.
--
-- DOMMEN, OG DEN ER ANNERLEDES ENN SØSKENMODULENES: for M-41 var
-- faren at modulen skulle GJØRE noe farlig. Her er faren at den skal
-- SPØRRE noen. Et oppslag mot et adresseregister er en utgående kanal
-- med personopplysninger i — vi ville sendt kundens navn og adresse ut
-- av huset, til en tredjepart vi ikke har databehandleravtale med, for
-- å få tilbake et ja eller nei vi så ville kalt «validert».
--
-- Og «validert» av et oppslag er uansett ikke det vilkåret lover. At en
-- adresse FINNES i et register sier ikke at pakken kommer fram til den
-- som skal ha den. Det er to forskjellige påstander, og bare den ene av
-- dem er `adresse_validert`.
--
-- SÅ v1 REGISTRERER TO TING: adressen SLIK DEN BLE OPPGITT, og hvordan
-- et MENNESKE kontrollerte den. Den dagen noen skal attestere, finnes
-- det en målt historikk å bygge fullmakten på — og et grunnlag for å
-- svare på om oppslag i det hele tatt er verdt personopplysningene.
--
-- DOMMENE v1 HVILER PÅ, håndhevet i DATAMODELLEN:
--
--   1. HISTORIKKEN OVERSKRIVES ALDRI. Det finnes ingen kolonne som
--      holder «gjeldende adresse». Den gjeldende adressen ER den siste
--      versjonen, og hver versjon er FROSSET. M-42s dom (110).
--
--   2. NORMALISERINGEN ERSTATTER ALDRI ORIGINALEN. Begge står på
--      raden, i hver sin kolonne, begge frosset. Dette er modulens
--      egen dom, og den er skarpere enn den ser ut:
--
--      Blander man dem, kan ingen etterpå se om en feillevering skyldtes
--      det kunden skrev eller det vi gjorde med det. «Storgt. 5» og
--      «Storgata 5» er samme adresse for et menneske og to forskjellige
--      strenger for en maskin — og hvis vi lagret bare vår egen
--      utgave, har vi slettet spørsmålet før noen rakk å stille det.
--
--      Normaliseringen her er dessuten BEVISST NØYTRAL: den slår sammen
--      mellomrom og endrer bokstavstørrelse. Den gjetter ikke på
--      forkortelser, den slår ikke opp postnummer, og den retter ikke
--      stavefeil. En normalisering som GJETTER er et oppslag i
--      forkledning.
--
--   3. HVER KONTROLL HAR EN KILDE OG EN METODE. `metode` er et lukket
--      sett og `kilde_ref` er NOT NULL: hvem kontrollerte, og hvordan.
--      «Validert» uten hvem og hvordan er ikke en måling — det er
--      nøyaktig den påstanden `adresse_validert` ville hvilt på.
--
--   4. VALIDERINGSKRAVET ER TENANTENS. Hvor gammel en kontroll kan
--      være, og hvilke metoder som teller som tilstrekkelige, ligger i
--      basen og settes gjennom en dør. En nettbutikk som sender
--      digitale varer og en som sender kjøleskap har ikke samme krav.
--
--   5. EN ADRESSE KAN VÆRE UKONTROLLERBAR, og det er et SVAR. En
--      kontroll som ikke lot seg gjennomføre registreres som nettopp
--      det — ikke som et fravær av kontroll, og ikke som et avslag.
--
-- GRENSEN MOT M-30: M-30 eier personvernbehandlingene og sletteplikten.
-- M-19 eier adressen som LEVERINGSFAKTUM. v1 kobler dem ikke, men
-- registeret er bygget så koblingen kan komme: adressene er nøklet på
-- subjektet, og subjektet kan deaktiveres uten at historikken forsvinner.
--
-- SVEIPEN HAR SIN EGEN ROLLE OG SIN EGEN TIMER (095/100-111):
-- `disponit_adressesveip` har nøyaktig ÉN rettighet — EXECUTE på
-- `m19_sveip_adresser` — og INGEN tabellrettigheter. Sveipen
-- KONTROLLERER INGENTING og SLÅR INGENTING OPP; den skriver FUNN.
--
-- FORMENE ER HUSETS: tabellene eies av migrator, dørene av NOLOGIN-rollen
-- `disponit_adresse_eier`, tenant TEXT + RLS ENABLE+FORCE +
-- `tenant_isolasjon`, SP-1 i hver tenantbundet definer, ingen BYPASSRLS.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_adresse_eier') THEN
        RAISE EXCEPTION 'rollen disponit_adresse_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_adresse_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `adressekrav` — ÉN per tenant. DOM 4: KRAVET ER TENANTENS.
CREATE TABLE adressekrav (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- Hvor lenge en adresse kan stå UKONTROLLERT før det er et funn.
    -- En adresse ingen har sett på er ikke en feil, men den er heller
    -- ikke et grunnlag å attestere på.
    ukontrollert_dogn INT NOT NULL DEFAULT 14
        CHECK (ukontrollert_dogn BETWEEN 0 AND 3650),
    -- Hvor lenge en GODKJENT kontroll regnes som gyldig. En kontroll
    -- fra i fjor sier ingenting om hvor kunden bor i dag.
    kontroll_gyldig_dogn INT NOT NULL DEFAULT 365
        CHECK (kontroll_gyldig_dogn BETWEEN 0 AND 3650),
    -- METODENE SOM TELLER, som et sett tenanten velger fra det lukkede
    -- settet under. En tenant som sender kjøleskap kan kreve
    -- `bekreftet_av_kunde`; en som sender e-bøker kan nøye seg med
    -- `visuell`. TOM LISTE ER FORBUDT: et krav uten metoder ville gjort
    -- hver kontroll utilstrekkelig og hver adresse til et funn, som
    -- ser ut som en streng policy og er en konfigurasjonsfeil.
    godkjente_metoder TEXT[] NOT NULL
        DEFAULT ARRAY['bekreftet_av_kunde', 'dokumentert']
        CHECK (cardinality(godkjente_metoder) > 0),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT adressekrav_pk PRIMARY KEY (tenant)
);

-- `adressesubjekt` — den vi registrerer adresser for.
CREATE TABLE adressesubjekt (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    subjekt_id UUID NOT NULL,
    -- Tenantens egen referanse (kunde-, ordre- eller mottakernummer).
    -- FRI TEKST og ingen fremmednøkkel: adressehistorikken skal kunne
    -- stå alene.
    ekstern_ref TEXT NOT NULL CHECK (ekstern_ref ~ '[^[:space:]]'),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    aktiv BOOLEAN NOT NULL DEFAULT true,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT adressesubjekt_pk PRIMARY KEY (tenant, subjekt_id),
    CONSTRAINT adressesubjekt_ref_unik UNIQUE (tenant, ekstern_ref)
);
CREATE INDEX adressesubjekt_aktive
    ON adressesubjekt (tenant) WHERE aktiv;

-- `adresseversjon` — DOM 1 OG 2. HOVEDBOKEN FOR ADRESSER.
--
-- Den gjeldende adressen er den SISTE raden her. Det finnes ingen annen
-- adresse noe sted i skjemaet, og det er hele poenget.
--
-- ORIGINALEN OG NORMALISERINGEN STÅR SIDE OM SIDE, begge frosset. Ingen
-- av dem kan skrives over den andre, fordi begge er svar på hvert sitt
-- spørsmål: «hva skrev kunden» og «hva sammenlignet vi på».
CREATE TABLE adresseversjon (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    versjon_id UUID NOT NULL,
    subjekt_id UUID NOT NULL,
    -- SLIK DEN BLE OPPGITT. Aldri rørt, aldri rettet, aldri erstattet.
    linje1_original TEXT NOT NULL CHECK (linje1_original ~ '[^[:space:]]'),
    linje2_original TEXT,
    postnr_original TEXT NOT NULL CHECK (postnr_original ~ '[^[:space:]]'),
    poststed_original TEXT NOT NULL
        CHECK (poststed_original ~ '[^[:space:]]'),
    -- ISO 3166-1 alfa-2, store bokstaver. Ikke en validering av at
    -- landet finnes — bare av at feltet har formen til en landkode.
    land TEXT NOT NULL CHECK (land ~ '^[A-Z]{2}$'),
    -- HVA VI SAMMENLIGNER PÅ. Regnet av `m19_normaliser`, som slår
    -- sammen mellomrom og senker bokstavstørrelse — og gjør INGENTING
    -- annet. Se dommen i toppen.
    linje1_normalisert TEXT NOT NULL,
    postnr_normalisert TEXT NOT NULL,
    poststed_normalisert TEXT NOT NULL,
    -- Hvor adressen kom fra. Et lukket sett: en adresse tenanten
    -- skrev inn og en adressen kunden selv oppga er ikke samme
    -- grunnlag.
    kilde TEXT NOT NULL
        CONSTRAINT adresseversjon_kilde_lukket CHECK (kilde IN (
            'oppgitt_av_kunde', 'ordre', 'manuell', 'import')),
    kilde_ref TEXT NOT NULL CHECK (kilde_ref ~ '[^[:space:]]'),
    gjelder_fra DATE NOT NULL,
    notat TEXT NOT NULL CHECK (notat ~ '[^[:space:]]'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT adresseversjon_pk PRIMARY KEY (tenant, versjon_id),
    CONSTRAINT adresseversjon_subjekt_fk
        FOREIGN KEY (tenant, subjekt_id)
        REFERENCES adressesubjekt (tenant, subjekt_id),
    -- SAMME KILDEHENDELSE REGISTRERES ÉN GANG. En import som kjøres to
    -- ganger er ikke to adresseendringer.
    CONSTRAINT adresseversjon_kilde_unik
        UNIQUE (tenant, subjekt_id, kilde, kilde_ref)
);
CREATE INDEX adresseversjon_oppslag
    ON adresseversjon (tenant, subjekt_id, gjelder_fra DESC,
                       registrert DESC);

-- `adressekontroll` — DOM 3 OG 5. HVEM KONTROLLERTE, OG HVORDAN.
--
-- Nøklet på VERSJONEN, ikke på subjektet: en kontroll gjelder den
-- adressen som sto da den ble gjort. Endrer kunden adresse, er den
-- gamle kontrollen fortsatt sann om den gamle adressen — og sier
-- ingenting om den nye. Det er nettopp den forskjellen
-- `adresse_validert` ville stått og falt på.
CREATE TABLE adressekontroll (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kontroll_id UUID NOT NULL,
    versjon_id UUID NOT NULL,
    -- HVORDAN. Lukket sett, og INGEN AV DEM ER ET OPPSLAG:
    --   visuell            — et menneske leste adressen og fant den rimelig
    --   bekreftet_av_kunde — kunden bekreftet den selv
    --   dokumentert        — mot et dokument kunden framviste
    --   levering_bekreftet — en pakke kom faktisk fram
    -- Den siste er den sterkeste og den eneste som er et FAKTUM
    -- framfor en vurdering.
    metode TEXT NOT NULL
        CONSTRAINT adressekontroll_metode_lukket CHECK (metode IN (
            'visuell', 'bekreftet_av_kunde', 'dokumentert',
            'levering_bekreftet')),
    -- UTFALLET, og «ukontrollerbar» er DOM 5: et svar, ikke et fravær.
    utfall TEXT NOT NULL
        CONSTRAINT adressekontroll_utfall_lukket CHECK (utfall IN (
            'godkjent', 'avvist', 'ukontrollerbar')),
    -- HVEM. Ikke `registrert_av` (som er den tekniske aktøren), men
    -- den som faktisk gjorde vurderingen.
    kontrollor TEXT NOT NULL CHECK (kontrollor ~ '[^[:space:]]'),
    kilde_ref TEXT NOT NULL CHECK (kilde_ref ~ '[^[:space:]]'),
    -- En avvist eller ukontrollerbar kontroll UTEN begrunnelse er ingen
    -- vurdering. Vakten under krever den.
    begrunnelse TEXT,
    kontrollert DATE NOT NULL,
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT adressekontroll_pk PRIMARY KEY (tenant, kontroll_id),
    CONSTRAINT adressekontroll_versjon_fk
        FOREIGN KEY (tenant, versjon_id)
        REFERENCES adresseversjon (tenant, versjon_id),
    CONSTRAINT adressekontroll_kilde_unik
        UNIQUE (tenant, versjon_id, metode, kilde_ref)
);
CREATE INDEX adressekontroll_oppslag
    ON adressekontroll (tenant, versjon_id, kontrollert DESC,
                        registrert DESC);

-- `adressefunn` — funnene. Nøklet på subjektet og typen (111s form).
CREATE TABLE adressefunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    subjekt_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CONSTRAINT adressefunn_type_lukket CHECK (funntype IN (
            'ukontrollert_adresse', 'kontroll_utlopt', 'avvist_adresse',
            'utilstrekkelig_metode', 'ingen_krav')),
    -- DØGN for de to tidsfunnene, 0 for de tre andre.
    over_grense INT,
    -- Den siste kontrollen, når funnet handler om den. Metoden er med
    -- fordi `utilstrekkelig_metode` ellers ville vært et funn uten det
    -- ene faktumet som forklarer det.
    siste_metode TEXT,
    siste_utfall TEXT,
    kravversjon INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT adressefunn_pk PRIMARY KEY (tenant, subjekt_id,
                                           funntype),
    CONSTRAINT adressefunn_subjekt_fk FOREIGN KEY (tenant, subjekt_id)
        REFERENCES adressesubjekt (tenant, subjekt_id),
    CONSTRAINT adressefunn_lukking_helhet CHECK (
        (apen AND lukket_ts IS NULL)
     OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE INDEX adressefunn_apne
    ON adressefunn (tenant, funntype) WHERE apen;


-- ------------------------------------------------------------
-- 2. Vaktene.
-- ------------------------------------------------------------

CREATE FUNCTION m19_krav_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'adressekrav: TRUNCATE avvist — et tømt'
            ' valideringskrav gjør hver adresse ukontrollerbar uten at'
            ' noen har bestemt det'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'adressekrav: DELETE avvist — et krav slås av'
            ' ved å settes, ikke ved å forsvinne'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.versjon <= OLD.versjon THEN
        RAISE EXCEPTION 'adressekrav: versjonen må øke (% -> %)',
            OLD.versjon, NEW.versjon
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m19_krav_vakt() FROM PUBLIC;
CREATE TRIGGER m19_krav_vakt
    BEFORE UPDATE OR DELETE ON adressekrav
    FOR EACH ROW EXECUTE FUNCTION m19_krav_vakt();
CREATE TRIGGER m19_krav_ingen_truncate
    BEFORE TRUNCATE ON adressekrav
    EXECUTE FUNCTION m19_krav_vakt();


CREATE FUNCTION m19_subjekt_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'adressesubjekt: TRUNCATE avvist — subjektene'
            ' bærer adressehistorikken'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'adressesubjekt: DELETE avvist — et subjekt'
            ' deaktiveres, det slettes ikke. Historikken skal overleve'
            ' at kundeforholdet tar slutt'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.subjekt_id <> OLD.subjekt_id
       OR NEW.ekstern_ref <> OLD.ekstern_ref
       OR NEW.opprettet <> OLD.opprettet THEN
        RAISE EXCEPTION 'adressesubjekt: identiteten er FROSSET —'
            ' subjekt_id, ekstern_ref og opprettet kan ikke endres'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m19_subjekt_vakt() FROM PUBLIC;
CREATE TRIGGER m19_subjekt_vakt
    BEFORE UPDATE OR DELETE ON adressesubjekt
    FOR EACH ROW EXECUTE FUNCTION m19_subjekt_vakt();
CREATE TRIGGER m19_subjekt_ingen_truncate
    BEFORE TRUNCATE ON adressesubjekt
    EXECUTE FUNCTION m19_subjekt_vakt();


-- DOM 1 OG 2: HISTORIKKEN OVERSKRIVES ALDRI, OG NORMALISERINGEN
-- ERSTATTER ALDRI ORIGINALEN.
--
-- Modulens skarpeste vakt, og den holder BEGGE dommene med samme grep:
-- hele raden er frosset. Kunne originalen endres, var den ikke lenger
-- «slik den ble oppgitt». Kunne normaliseringen endres alene, ville de
-- to kolonnene sluttet å være to svar på samme adresse.
CREATE FUNCTION m19_versjon_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'adresseversjon: TRUNCATE avvist — en tømt'
            ' adressehistorikk er leveranser ingen kan forklare'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'adresseversjon: DELETE avvist — en feilført'
            ' adresse rettes med en NY versjon. Historikken er hele'
            ' beviset' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'adresseversjon: raden er FROSSET — originalen'
            ' skal alltid kunne leses som den ble oppgitt, og'
            ' normaliseringen som det vi faktisk sammenlignet på'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m19_versjon_vakt() FROM PUBLIC;
CREATE TRIGGER m19_versjon_vakt
    BEFORE UPDATE OR DELETE ON adresseversjon
    FOR EACH ROW EXECUTE FUNCTION m19_versjon_vakt();
CREATE TRIGGER m19_versjon_ingen_truncate
    BEFORE TRUNCATE ON adresseversjon
    EXECUTE FUNCTION m19_versjon_vakt();


-- DOM 3: HVER KONTROLL HAR EN KILDE OG EN METODE — og et utfall som
-- ikke er «godkjent» har en BEGRUNNELSE.
CREATE FUNCTION m19_kontroll_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'adressekontroll: TRUNCATE avvist — uten'
            ' kontrollene finnes det ingen måling å attestere på'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'adressekontroll: DELETE avvist — en feilført'
            ' kontroll rettes med en NY kontroll'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'adressekontroll: raden er FROSSET — en'
            ' vurdering som kunne endres i ettertid er ingen vurdering'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Et AVSLAG eller en UKONTROLLERBAR adresse uten begrunnelse er en
    -- påstand. `godkjent` trenger ingen: da er metoden og kontrolløren
    -- hele svaret.
    IF NEW.utfall <> 'godkjent'
       AND (NEW.begrunnelse IS NULL
            OR NEW.begrunnelse !~ '[^[:space:]]') THEN
        RAISE EXCEPTION 'adressekontroll: utfallet «%» krever en'
            ' begrunnelse — uten den er avslaget en påstand', NEW.utfall
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m19_kontroll_vakt() FROM PUBLIC;
CREATE TRIGGER m19_kontroll_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON adressekontroll
    FOR EACH ROW EXECUTE FUNCTION m19_kontroll_vakt();
CREATE TRIGGER m19_kontroll_ingen_truncate
    BEFORE TRUNCATE ON adressekontroll
    EXECUTE FUNCTION m19_kontroll_vakt();


CREATE FUNCTION m19_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'adressefunn: TRUNCATE avvist — et tømt'
            ' funnregister ser ut som en ren adressebok'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'adressefunn: DELETE avvist — et funn lukkes,'
            ' det slettes ikke'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.forst_sett <> OLD.forst_sett THEN
        RAISE EXCEPTION 'adressefunn: forst_sett er FROSSET — hvor'
            ' lenge et funn har stått er halve alvoret'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m19_funn_vakt() FROM PUBLIC;
CREATE TRIGGER m19_funn_vakt
    BEFORE UPDATE OR DELETE ON adressefunn
    FOR EACH ROW EXECUTE FUNCTION m19_funn_vakt();
CREATE TRIGGER m19_funn_ingen_truncate
    BEFORE TRUNCATE ON adressefunn
    EXECUTE FUNCTION m19_funn_vakt();


-- ------------------------------------------------------------
-- 3. Dørene. SECURITY DEFINER, eid av `disponit_adresse_eier`, SP-1.
-- ------------------------------------------------------------

-- Eieren trenger å kunne kalle SP-1-vakten og å skrive evidens.
-- `krev_tenantkontekst` eies av `disponit_m37_claimer` (111s form).
GRANT INSERT ON revisjonslogg TO disponit_adresse_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_adresse_eier;
RESET ROLE;

SET LOCAL ROLE disponit_adresse_eier;

-- EVIDENSEN. Modulen skriver i revisjonsloggen som BEVIS på hva den
-- selv gjorde — aldri som en dom om et vilkår. Beslutningen er alltid
-- `TILLAT` på en registrering modulen utførte; den attesterer ingenting.
CREATE FUNCTION m19_evidens(p_tenant TEXT, p_subjekt_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm19_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm19_adresse', 'handling', p_handling,
        'subjekt_id', p_subjekt_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm19_adresse',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:adresse', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m19_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- NORMALISERINGEN. Det farligste stedet i hele modulen, fordi den ser
-- så uskyldig ut.
--
-- Den gjør NØYAKTIG TO TING: slår sammen mellomrom og senker
-- bokstavstørrelse. Den gjetter ikke på forkortelser («Storgt.» blir
-- ikke «Storgata»), den slår ikke opp postnummer mot poststed, og den
-- retter ikke stavefeil.
--
-- HVER av de tingene ville vært en påstand om hva kunden MENTE, lagret
-- ved siden av det kunden SKREV, og etterpå umulig å skille fra
-- hverandre. En normalisering som gjetter er et oppslag i forkledning —
-- den bare slår opp i en tabell vi skrev selv.
--
-- IMMUTABLE, fordi normaliseringen av en gitt streng må gi samme svar
-- i dag og om fem år. Var den det ikke, kunne to versjoner av samme
-- adresse blitt ulike uten at noen rørte dem.
CREATE FUNCTION m19_normaliser(p_tekst TEXT)
RETURNS TEXT LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT lower(btrim(regexp_replace(coalesce(p_tekst, ''),
                                      '[[:space:]]+', ' ', 'g')));
$$;
REVOKE ALL ON FUNCTION m19_normaliser(TEXT) FROM PUBLIC;


-- KRAVET. DOM 4.
CREATE FUNCTION m19_sett_krav(
    p_tenant TEXT, p_ukontrollert_dogn INT, p_kontroll_gyldig_dogn INT,
    p_godkjente_metoder TEXT[], p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_versjon INT;
    v_ugyldig TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm19_sett_krav');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_godkjente_metoder IS NULL
       OR cardinality(p_godkjente_metoder) = 0 THEN
        RAISE EXCEPTION 'm19_sett_krav: minst én metode må godkjennes'
            ' — et krav uten metoder gjør hver adresse til et funn og'
            ' ser ut som en streng policy'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- METODENE MÅ VÆRE FRA DET LUKKEDE SETTET. Uten denne sjekken
    -- kunne en tenant skrevet «oppslag» i lista, og v1-dommen ville
    -- vært omgått gjennom en konfigurasjonsverdi.
    SELECT m INTO v_ugyldig
      FROM unnest(p_godkjente_metoder) AS m
     WHERE m NOT IN ('visuell', 'bekreftet_av_kunde', 'dokumentert',
                     'levering_bekreftet')
     LIMIT 1;
    IF v_ugyldig IS NOT NULL THEN
        RAISE EXCEPTION 'm19_sett_krav: «%» er ingen kjent'
            ' kontrollmetode', v_ugyldig
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO public.adressekrav
        (tenant, ukontrollert_dogn, kontroll_gyldig_dogn,
         godkjente_metoder, oppdatert_av)
    VALUES (p_tenant, p_ukontrollert_dogn, p_kontroll_gyldig_dogn,
            p_godkjente_metoder, p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        ukontrollert_dogn = EXCLUDED.ukontrollert_dogn,
        kontroll_gyldig_dogn = EXCLUDED.kontroll_gyldig_dogn,
        godkjente_metoder = EXCLUDED.godkjente_metoder,
        versjon = public.adressekrav.versjon + 1,
        oppdatert = now(),
        oppdatert_av = EXCLUDED.oppdatert_av
    RETURNING versjon INTO v_versjon;

    -- Kravet gjelder HELE tenanten, ikke ett subjekt: NULL er det
    -- ærlige svaret på «hvilket subjekt», ikke et manglende felt.
    PERFORM public.m19_evidens(p_tenant, NULL, 'adressekrav_satt',
        p_aktor, jsonb_build_object('versjon', v_versjon,
                                    'metoder', p_godkjente_metoder));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION m19_sett_krav(TEXT, INT, INT, TEXT[], TEXT) FROM PUBLIC;


CREATE FUNCTION m19_registrer_subjekt(
    p_tenant TEXT, p_subjekt_id UUID, p_ekstern_ref TEXT, p_navn TEXT,
    p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm19_registrer_subjekt');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.adressesubjekt
        (tenant, subjekt_id, ekstern_ref, navn, opprettet_av)
    VALUES (p_tenant, p_subjekt_id, btrim(p_ekstern_ref),
            btrim(p_navn), p_aktor);
    PERFORM public.m19_evidens(p_tenant, p_subjekt_id,
        'adressesubjekt_opprettet', p_aktor, '{}'::jsonb);
END $$;
REVOKE ALL ON FUNCTION m19_registrer_subjekt(
    TEXT, UUID, TEXT, TEXT, TEXT)
    FROM PUBLIC;


CREATE FUNCTION m19_sett_subjektaktiv(
    p_tenant TEXT, p_subjekt_id UUID, p_aktiv BOOLEAN, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_naa BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm19_sett_subjektaktiv');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT aktiv INTO v_naa FROM public.adressesubjekt
     WHERE tenant = p_tenant AND subjekt_id = p_subjekt_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm19_sett_subjektaktiv: ukjent subjekt %',
            p_subjekt_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_naa = p_aktiv THEN
        RETURN false;
    END IF;
    UPDATE public.adressesubjekt SET aktiv = p_aktiv
     WHERE tenant = p_tenant AND subjekt_id = p_subjekt_id;
    -- Et deaktivert subjekt har ingen åpne funn: ingen skal se på en
    -- adresse ingen lenger sender noe til. HISTORIKKEN BLIR STÅENDE.
    IF NOT p_aktiv THEN
        UPDATE public.adressefunn
           SET apen = false, lukket_ts = now()
         WHERE tenant = p_tenant AND subjekt_id = p_subjekt_id AND apen;
    END IF;
    PERFORM public.m19_evidens(p_tenant, p_subjekt_id,
        'adressesubjekt_aktiv_satt', p_aktor,
        jsonb_build_object('aktiv', p_aktiv));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m19_sett_subjektaktiv(
    TEXT, UUID, BOOLEAN, TEXT)
    FROM PUBLIC;


-- ADRESSEDØREN. DOM 1 OG 2.
--
-- Normaliseringen REGNES HER, av `m19_normaliser`, og skrives ned ved
-- siden av originalen. Kalleren får ikke sende inn en normalisert form:
-- da kunne den vært hva som helst, og kolonnen ville sluttet å bety
-- «det vi faktisk sammenlignet på».
CREATE FUNCTION m19_registrer_adresse(
    p_tenant TEXT, p_versjon_id UUID, p_subjekt_id UUID,
    p_linje1 TEXT, p_linje2 TEXT, p_postnr TEXT, p_poststed TEXT,
    p_land TEXT, p_kilde TEXT, p_kilde_ref TEXT, p_gjelder_fra DATE,
    p_notat TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_aktiv BOOLEAN;
    v_forrige TEXT;
    v_ny TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm19_registrer_adresse');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    -- SAMME DOM SOM 111: en adresse kan ikke gjelde fra framtida.
    -- Sveipen måler mot `current_date`, så en framtidsdatert versjon
    -- ville vært den siste for DØREN og usynlig for SVEIPEN — og
    -- funnet ville pekt på en adresse som ikke lenger var den
    -- gjeldende.
    IF p_gjelder_fra IS NULL OR p_gjelder_fra > current_date THEN
        RAISE EXCEPTION 'm19_registrer_adresse: en adresse kan ikke'
            ' gjelde fra framtida (%)', p_gjelder_fra
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT aktiv INTO v_aktiv FROM public.adressesubjekt
     WHERE tenant = p_tenant AND subjekt_id = p_subjekt_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm19_registrer_adresse: ukjent subjekt %',
            p_subjekt_id USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_aktiv THEN
        RAISE EXCEPTION 'm19_registrer_adresse: subjektet % er'
            ' deaktivert', p_subjekt_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    v_ny := public.m19_normaliser(p_linje1) || '|'
         || public.m19_normaliser(p_postnr) || '|'
         || public.m19_normaliser(p_poststed);
    SELECT linje1_normalisert || '|' || postnr_normalisert || '|'
        || poststed_normalisert INTO v_forrige
      FROM public.adresseversjon
     WHERE tenant = p_tenant AND subjekt_id = p_subjekt_id
     ORDER BY gjelder_fra DESC, registrert DESC
     LIMIT 1;

    INSERT INTO public.adresseversjon
        (tenant, versjon_id, subjekt_id, linje1_original,
         linje2_original, postnr_original, poststed_original, land,
         linje1_normalisert, postnr_normalisert, poststed_normalisert,
         kilde, kilde_ref, gjelder_fra, notat, registrert_av)
    VALUES (p_tenant, p_versjon_id, p_subjekt_id, btrim(p_linje1),
            nullif(btrim(coalesce(p_linje2, '')), ''), btrim(p_postnr),
            btrim(p_poststed), upper(btrim(p_land)),
            public.m19_normaliser(p_linje1),
            public.m19_normaliser(p_postnr),
            public.m19_normaliser(p_poststed),
            p_kilde, btrim(p_kilde_ref), p_gjelder_fra, btrim(p_notat),
            p_aktor);

    -- DETALJEN BÆRER ALDRI ADRESSEN SELV. Revisjonsloggen er
    -- bredere lesbar enn adresseregisteret, og en adresse som lekker
    -- dit har lekket ut av modulen sin.
    PERFORM public.m19_evidens(p_tenant, p_subjekt_id,
        'adresse_registrert', p_aktor,
        jsonb_build_object('kilde', p_kilde,
                           'endret', v_forrige IS DISTINCT FROM v_ny));
    -- SANT NÅR ADRESSEN FAKTISK ER EN ANNEN. Det er dette svaret som
    -- gjør at en gammel kontroll ikke stille kan gjelde en ny adresse:
    -- kontrollene er nøklet på VERSJONEN, ikke på subjektet.
    RETURN v_forrige IS DISTINCT FROM v_ny;
END $$;
REVOKE ALL ON FUNCTION m19_registrer_adresse(
    TEXT, UUID, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, DATE,
    TEXT, TEXT) FROM PUBLIC;


-- KONTROLLDØREN. DOM 3 OG 5.
CREATE FUNCTION m19_registrer_kontroll(
    p_tenant TEXT, p_kontroll_id UUID, p_versjon_id UUID,
    p_metode TEXT, p_utfall TEXT, p_kontrollor TEXT, p_kilde_ref TEXT,
    p_begrunnelse TEXT, p_kontrollert DATE, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_subjekt UUID;
    v_aktiv BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm19_registrer_kontroll');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    IF p_kontrollert IS NULL OR p_kontrollert > current_date THEN
        RAISE EXCEPTION 'm19_registrer_kontroll: en kontroll kan ikke'
            ' være gjort i framtida (%)', p_kontrollert
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- VERSJONEN LESES UTEN LÅS, SUBJEKTET LÅSES.
    --
    -- `SELECT ... FOR UPDATE` krever UPDATE-RETTEN, og `adresseversjon`
    -- har den ikke — den er frosset, og eieren fikk UPDATE revokert i
    -- seksjon 6. En lås på versjonsraden ville derfor feilet med
    -- «permission denied» (M-42s lærdom, 110). Raden trenger heller
    -- ingen lås: den kan ikke endres av noen. Det som KAN endre seg
    -- under oss er om subjektet fortsatt er aktivt, så det er den
    -- raden vi låser — og den serialiserer samtidige kontroller på
    -- samme subjekt.
    SELECT subjekt_id INTO v_subjekt FROM public.adresseversjon
     WHERE tenant = p_tenant AND versjon_id = p_versjon_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm19_registrer_kontroll: ukjent adresseversjon'
            ' %', p_versjon_id USING ERRCODE = 'no_data_found';
    END IF;
    SELECT aktiv INTO v_aktiv FROM public.adressesubjekt
     WHERE tenant = p_tenant AND subjekt_id = v_subjekt
       FOR UPDATE;
    IF NOT v_aktiv THEN
        RAISE EXCEPTION 'm19_registrer_kontroll: subjektet % er'
            ' deaktivert — en kontroll av en adresse ingen sender noe'
            ' til er ingen måling', v_subjekt
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO public.adressekontroll
        (tenant, kontroll_id, versjon_id, metode, utfall, kontrollor,
         kilde_ref, begrunnelse, kontrollert, registrert_av)
    VALUES (p_tenant, p_kontroll_id, p_versjon_id, p_metode, p_utfall,
            btrim(p_kontrollor), btrim(p_kilde_ref),
            nullif(btrim(coalesce(p_begrunnelse, '')), ''),
            p_kontrollert, p_aktor);

    PERFORM public.m19_evidens(p_tenant, v_subjekt,
        'adressekontroll_registrert', p_aktor,
        jsonb_build_object('versjon_id', p_versjon_id,
                           'metode', p_metode,
                           'utfall', p_utfall));
END $$;
REVOKE ALL ON FUNCTION m19_registrer_kontroll(
    TEXT, UUID, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, DATE, TEXT)
    FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Lesedørene.
-- ------------------------------------------------------------

CREATE FUNCTION m19_kravene(p_tenant TEXT)
RETURNS TABLE (ukontrollert_dogn INT, kontroll_gyldig_dogn INT,
               godkjente_metoder TEXT[], versjon INT,
               oppdatert TIMESTAMPTZ, oppdatert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm19_kravene');
    RETURN QUERY
    SELECT k.ukontrollert_dogn, k.kontroll_gyldig_dogn,
           k.godkjente_metoder, k.versjon, k.oppdatert, k.oppdatert_av
      FROM public.adressekrav k WHERE k.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m19_kravene(TEXT) FROM PUBLIC;


-- DEN GJELDENDE ADRESSEN ER DEN SISTE VERSJONEN. Ingen kolonne holder
-- den; dette oppslaget ER svaret.
--
-- `p_dag` gjør spørsmålet historisk besvarbart: «hvilken adresse gjaldt
-- den dagen pakken gikk». Uten den ville en feillevering bare kunnet
-- måles mot adressen som gjelder NÅ.
CREATE FUNCTION m19_gjeldende_adresse(
    p_tenant TEXT, p_subjekt_id UUID, p_dag DATE)
RETURNS TABLE (versjon_id UUID, linje1_original TEXT,
               linje2_original TEXT, postnr_original TEXT,
               poststed_original TEXT, land TEXT,
               linje1_normalisert TEXT, kilde TEXT, kilde_ref TEXT,
               gjelder_fra DATE, notat TEXT, registrert TIMESTAMPTZ,
               registrert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm19_gjeldende_adresse');
    RETURN QUERY
    SELECT v.versjon_id, v.linje1_original, v.linje2_original,
           v.postnr_original, v.poststed_original, v.land,
           v.linje1_normalisert, v.kilde, v.kilde_ref, v.gjelder_fra,
           v.notat, v.registrert, v.registrert_av
      FROM public.adresseversjon v
     WHERE v.tenant = p_tenant AND v.subjekt_id = p_subjekt_id
       AND v.gjelder_fra <= p_dag
     ORDER BY v.gjelder_fra DESC, v.registrert DESC
     LIMIT 1;
END $$;
REVOKE ALL ON FUNCTION m19_gjeldende_adresse(TEXT, UUID, DATE) FROM PUBLIC;


-- HISTORIKKEN. Nyeste øverst, med `endret` på hver linje.
--
-- `endret` sammenligner NORMALISERT form mot den forrige: to skrivemåter
-- av samme adresse er ikke et adresseskifte, og en flate som viste dem
-- som to skifter ville gjort hver retting av en skrivefeil til en
-- hendelse noen måtte se på.
CREATE FUNCTION m19_adressehistorikken(
    p_tenant TEXT, p_subjekt_id UUID, p_grense INT)
RETURNS TABLE (versjon_id UUID, linje1_original TEXT,
               linje2_original TEXT, postnr_original TEXT,
               poststed_original TEXT, land TEXT, kilde TEXT,
               kilde_ref TEXT, gjelder_fra DATE, notat TEXT,
               registrert TIMESTAMPTZ, registrert_av TEXT,
               endret BOOLEAN, kontroller BIGINT, siste_utfall TEXT,
               siste_metode TEXT, siste_kontrollert DATE)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm19_adressehistorikken');
    RETURN QUERY
    WITH rader AS (
        SELECT v.*,
               lag(v.linje1_normalisert || '|' || v.postnr_normalisert
                   || '|' || v.poststed_normalisert)
                 OVER (ORDER BY v.gjelder_fra, v.registrert) AS forrige,
               v.linje1_normalisert || '|' || v.postnr_normalisert
                 || '|' || v.poststed_normalisert AS naa
          FROM public.adresseversjon v
         WHERE v.tenant = p_tenant AND v.subjekt_id = p_subjekt_id),
    siste AS (
        SELECT DISTINCT ON (k.versjon_id)
               k.versjon_id, k.utfall, k.metode, k.kontrollert
          FROM public.adressekontroll k
         WHERE k.tenant = p_tenant
         ORDER BY k.versjon_id, k.kontrollert DESC, k.registrert DESC)
    SELECT r.versjon_id, r.linje1_original, r.linje2_original,
           r.postnr_original, r.poststed_original, r.land, r.kilde,
           r.kilde_ref, r.gjelder_fra, r.notat, r.registrert,
           r.registrert_av,
           r.forrige IS NOT NULL AND r.forrige IS DISTINCT FROM r.naa,
           (SELECT count(*) FROM public.adressekontroll k2
             WHERE k2.tenant = p_tenant
               AND k2.versjon_id = r.versjon_id),
           s.utfall, s.metode, s.kontrollert
      FROM rader r
      LEFT JOIN siste s ON s.versjon_id = r.versjon_id
     ORDER BY r.gjelder_fra DESC, r.registrert DESC
     LIMIT greatest(p_grense, 1);
END $$;
REVOKE ALL ON FUNCTION m19_adressehistorikken(TEXT, UUID, INT) FROM PUBLIC;


CREATE FUNCTION m19_kontrollene(
    p_tenant TEXT, p_versjon_id UUID, p_grense INT)
RETURNS TABLE (kontroll_id UUID, metode TEXT, utfall TEXT,
               kontrollor TEXT, kilde_ref TEXT, begrunnelse TEXT,
               kontrollert DATE, registrert TIMESTAMPTZ,
               registrert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm19_kontrollene');
    RETURN QUERY
    SELECT k.kontroll_id, k.metode, k.utfall, k.kontrollor,
           k.kilde_ref, k.begrunnelse, k.kontrollert, k.registrert,
           k.registrert_av
      FROM public.adressekontroll k
     WHERE k.tenant = p_tenant AND k.versjon_id = p_versjon_id
     ORDER BY k.kontrollert DESC, k.registrert DESC
     LIMIT greatest(p_grense, 1);
END $$;
REVOKE ALL ON FUNCTION m19_kontrollene(TEXT, UUID, INT) FROM PUBLIC;


-- FUNNKANDIDATENE. Delt av sveipen og av testene, så porten måler den
-- SAMME regnestykket som natta gjør (110/111s form).
--
-- Fem funntyper, og skillet mellom dem er hele modulens verdi:
--   ukontrollert_adresse  — ingen har sett på den, og fristen er ute
--   kontroll_utlopt       — den ble godkjent, men for lenge siden
--   avvist_adresse        — noen så på den og sa nei
--   utilstrekkelig_metode — noen så på den, men ikke godt nok for
--                           tenantens eget krav
--   ingen_krav            — tenanten har ikke sagt hva som kreves
--
-- «Ukontrollerbar» gir `ukontrollert_adresse`: DOM 5 sier at det er et
-- svar, men det er ikke et grunnlag. Å telle det som en kontroll ville
-- gjort «vi klarte ikke å sjekke» til «vi har sjekket».
CREATE FUNCTION m19_funnkandidater(p_tenant TEXT, p_dag DATE)
RETURNS TABLE (subjekt_id UUID, funntype TEXT, over_grense INT,
               siste_metode TEXT, siste_utfall TEXT, kravversjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm19_funnkandidater');
    RETURN QUERY
    WITH krav AS (
        SELECT k.ukontrollert_dogn, k.kontroll_gyldig_dogn,
               k.godkjente_metoder, k.versjon
          FROM public.adressekrav k WHERE k.tenant = p_tenant),
    gjeldende AS (
        SELECT DISTINCT ON (v.subjekt_id)
               v.subjekt_id, v.versjon_id, v.gjelder_fra
          FROM public.adresseversjon v
          JOIN public.adressesubjekt s ON s.tenant = v.tenant
           AND s.subjekt_id = v.subjekt_id AND s.aktiv
         WHERE v.tenant = p_tenant AND v.gjelder_fra <= p_dag
         ORDER BY v.subjekt_id, v.gjelder_fra DESC, v.registrert DESC),
    siste AS (
        SELECT DISTINCT ON (k.versjon_id)
               k.versjon_id, k.metode, k.utfall, k.kontrollert
          FROM public.adressekontroll k
         WHERE k.tenant = p_tenant AND k.kontrollert <= p_dag
         ORDER BY k.versjon_id, k.kontrollert DESC, k.registrert DESC),
    -- DEN SISTE GODKJENTE med en metode tenanten faktisk godtar.
    -- Egen CTE fordi «sist kontrollert» og «sist godkjent godt nok»
    -- er to forskjellige spørsmål: en avvist kontroll i går skal ikke
    -- kunne skjule at den godkjente er fra i fjor.
    godkjent AS (
        SELECT DISTINCT ON (k.versjon_id)
               k.versjon_id, k.kontrollert
          FROM public.adressekontroll k, krav
         WHERE k.tenant = p_tenant AND k.kontrollert <= p_dag
           AND k.utfall = 'godkjent'
           AND k.metode = ANY (krav.godkjente_metoder)
         ORDER BY k.versjon_id, k.kontrollert DESC, k.registrert DESC)
    SELECT * FROM (
        -- Ingen krav satt: DET er funnet, og de andre måles ikke.
        SELECT g.subjekt_id, 'ingen_krav'::TEXT, 0,
               NULL::TEXT, NULL::TEXT, NULL::INT
          FROM gjeldende g
         WHERE NOT EXISTS (SELECT 1 FROM krav)
        UNION ALL
        -- Aldri kontrollert (eller bare ukontrollerbart), og fristen er
        -- ute.
        SELECT g.subjekt_id, 'ukontrollert_adresse'::TEXT,
               ((p_dag - g.gjelder_fra) - krav.ukontrollert_dogn)::INT,
               s.metode, s.utfall, krav.versjon
          FROM gjeldende g CROSS JOIN krav
          LEFT JOIN siste s ON s.versjon_id = g.versjon_id
         WHERE (s.utfall IS NULL OR s.utfall = 'ukontrollerbar')
           AND (p_dag - g.gjelder_fra) > krav.ukontrollert_dogn
           -- …MEN EN SENERE «UKONTROLLERBAR» OPPHEVER IKKE EN GYLDIG
           -- GODKJENNING. Uten dette gjerdet ville en adresse noen
           -- FAKTISK har godkjent stått som aldri kontrollert bare
           -- fordi et senere forsøk ikke lot seg gjennomføre — og
           -- `kontroll_utlopt` ville sagt det motsatte om samme rad
           -- (CodeRabbit).
           AND NOT EXISTS (SELECT 1 FROM godkjent gk
                            WHERE gk.versjon_id = g.versjon_id)
        UNION ALL
        -- Noen så på den og sa nei.
        SELECT g.subjekt_id, 'avvist_adresse'::TEXT, 0,
               s.metode, s.utfall, krav.versjon
          FROM gjeldende g CROSS JOIN krav
          JOIN siste s ON s.versjon_id = g.versjon_id
         WHERE s.utfall = 'avvist'
        UNION ALL
        -- Godkjent, men med en metode tenantens eget krav ikke godtar.
        SELECT g.subjekt_id, 'utilstrekkelig_metode'::TEXT, 0,
               s.metode, s.utfall, krav.versjon
          FROM gjeldende g CROSS JOIN krav
          JOIN siste s ON s.versjon_id = g.versjon_id
         WHERE s.utfall = 'godkjent'
           AND NOT (s.metode = ANY (krav.godkjente_metoder))
           AND NOT EXISTS (SELECT 1 FROM godkjent gk
                            WHERE gk.versjon_id = g.versjon_id)
        UNION ALL
        -- Godkjent godt nok, men for lenge siden.
        SELECT g.subjekt_id, 'kontroll_utlopt'::TEXT,
               ((p_dag - gk.kontrollert)
                - krav.kontroll_gyldig_dogn)::INT,
               s.metode, s.utfall, krav.versjon
          FROM gjeldende g CROSS JOIN krav
          JOIN godkjent gk ON gk.versjon_id = g.versjon_id
          LEFT JOIN siste s ON s.versjon_id = g.versjon_id
         WHERE (p_dag - gk.kontrollert) > krav.kontroll_gyldig_dogn
    ) f (subjekt_id, funntype, over_grense, siste_metode, siste_utfall,
         kravversjon);
END $$;
REVOKE ALL ON FUNCTION m19_funnkandidater(TEXT, DATE) FROM PUBLIC;


-- OVERSIKTEN. Sammendraget flaten åpner på.
CREATE FUNCTION m19_adressestatus(p_tenant TEXT)
RETURNS TABLE (subjekter BIGINT, aktive BIGINT, med_adresse BIGINT,
               kontrollerte BIGINT, apne_funn BIGINT,
               apne_avvist BIGINT, har_krav BOOLEAN, kravversjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm19_adressestatus');
    RETURN QUERY
    SELECT (SELECT count(*) FROM public.adressesubjekt s
             WHERE s.tenant = p_tenant),
           (SELECT count(*) FROM public.adressesubjekt s
             WHERE s.tenant = p_tenant AND s.aktiv),
           (SELECT count(DISTINCT v.subjekt_id)
              FROM public.adresseversjon v WHERE v.tenant = p_tenant),
           (SELECT count(DISTINCT k.versjon_id)
              FROM public.adressekontroll k
             WHERE k.tenant = p_tenant AND k.utfall = 'godkjent'),
           (SELECT count(*) FROM public.adressefunn f
             WHERE f.tenant = p_tenant AND f.apen),
           (SELECT count(*) FROM public.adressefunn f
             WHERE f.tenant = p_tenant AND f.apen
               AND f.funntype = 'avvist_adresse'),
           EXISTS (SELECT 1 FROM public.adressekrav k
                    WHERE k.tenant = p_tenant),
           (SELECT k.versjon FROM public.adressekrav k
             WHERE k.tenant = p_tenant);
END $$;
REVOKE ALL ON FUNCTION m19_adressestatus(TEXT) FROM PUBLIC;


CREATE FUNCTION m19_subjektene(p_tenant TEXT, p_grense INT)
RETURNS TABLE (subjekt_id UUID, ekstern_ref TEXT, navn TEXT,
               aktiv BOOLEAN, versjon_id UUID, linje1_original TEXT,
               postnr_original TEXT, poststed_original TEXT, land TEXT,
               gjelder_fra DATE, kilde TEXT, siste_metode TEXT,
               siste_utfall TEXT, siste_kontrollert DATE,
               versjoner BIGINT, apne_funn TEXT[])
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm19_subjektene');
    RETURN QUERY
    WITH gjeldende AS (
        SELECT DISTINCT ON (v.subjekt_id) v.*
          FROM public.adresseversjon v
         WHERE v.tenant = p_tenant AND v.gjelder_fra <= current_date
         ORDER BY v.subjekt_id, v.gjelder_fra DESC, v.registrert DESC),
    siste AS (
        SELECT DISTINCT ON (k.versjon_id)
               k.versjon_id, k.metode, k.utfall, k.kontrollert
          FROM public.adressekontroll k
         WHERE k.tenant = p_tenant
         ORDER BY k.versjon_id, k.kontrollert DESC, k.registrert DESC)
    SELECT s.subjekt_id, s.ekstern_ref, s.navn, s.aktiv,
           g.versjon_id, g.linje1_original, g.postnr_original,
           g.poststed_original, g.land, g.gjelder_fra, g.kilde,
           k.metode, k.utfall, k.kontrollert,
           (SELECT count(*) FROM public.adresseversjon v2
             WHERE v2.tenant = p_tenant
               AND v2.subjekt_id = s.subjekt_id),
           (SELECT coalesce(array_agg(f.funntype ORDER BY f.funntype),
                            ARRAY[]::TEXT[])
              FROM public.adressefunn f
             WHERE f.tenant = p_tenant AND f.subjekt_id = s.subjekt_id
               AND f.apen)
      FROM public.adressesubjekt s
      LEFT JOIN gjeldende g ON g.subjekt_id = s.subjekt_id
      LEFT JOIN siste k ON k.versjon_id = g.versjon_id
     ORDER BY s.aktiv DESC, s.ekstern_ref
     LIMIT greatest(p_grense, 1);
END $$;
REVOKE ALL ON FUNCTION m19_subjektene(TEXT, INT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 5. SVEIPEN. Kryss-tenant, egen rolle, INGEN tabellrettigheter.
-- ------------------------------------------------------------
--
-- SVEIPEN KONTROLLERER INGENTING OG SLÅR INGENTING OPP. Den leser
-- registeret, regner ut hvem som er over tenantens egne grenser, og
-- skriver FUNN. Det er hele mandatet.
CREATE FUNCTION m19_sveip_adresser(p_grense INT DEFAULT 500)
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
    -- SP-1s speilbilde: sveipen er KRYSS-TENANT og skal derfor kjøre
    -- UTEN tenantkontekst. Står det en kontekst, er kallet enten en
    -- feil eller et forsøk på å bruke sveiperollen som en snarvei
    -- rundt RLS.
    IF coalesce(current_setting('disponit.tenant', true), '') <> '' THEN
        RAISE EXCEPTION 'm19_sveip_adresser: KRYSS-TENANT — kall den'
            ' uten tenantkontekst'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- MATERIALISERT FØR LØKKEN (111s form). En `FOR ... IN SELECT` er
    -- en LAT markør: `set_config` inne i løkka ville endret nettopp den
    -- RLS-konteksten markøren fortsatt hentet rader gjennom, og sveipen
    -- ville stille sett bare den første tenanten.
    SELECT array_agg(DISTINCT s.tenant ORDER BY s.tenant)
      INTO v_liste FROM public.adressesubjekt s WHERE s.aktiv;
    v_liste := (SELECT array_agg(x ORDER BY x) FROM unnest(
        coalesce(v_liste, ARRAY[]::TEXT[])) WITH ORDINALITY AS u(x, i)
        WHERE u.i <= greatest(p_grense, 1));

    FOREACH v_t IN ARRAY coalesce(v_liste, ARRAY[]::TEXT[])
    LOOP
        v_tenanter := v_tenanter + 1;
        PERFORM set_config('disponit.tenant', v_t, true);

        WITH kand AS (
            SELECT * FROM public.m19_funnkandidater(v_t, v_dag)),
        skrevet AS (
            INSERT INTO public.adressefunn
                (tenant, subjekt_id, funntype, over_grense,
                 siste_metode, siste_utfall, kravversjon)
            SELECT v_t, k.subjekt_id, k.funntype,
                   greatest(k.over_grense, 0), k.siste_metode,
                   k.siste_utfall, k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, subjekt_id, funntype) DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                siste_metode = EXCLUDED.siste_metode,
                siste_utfall = EXCLUDED.siste_utfall,
                kravversjon = EXCLUDED.kravversjon,
                sist_sett_sveip = now(),
                apen = true,
                lukket_ts = NULL
            RETURNING (xmax = 0) AS var_ny)
        -- NYE OG OPPFRISKEDE TELLES HVER FOR SEG. «Fem funn» og «fem
        -- funn som alt sto der i går» er ikke samme natt, og en linje
        -- som slo dem sammen ville skjult begge.
        --
        -- BEGGE AKKUMULERES over tenantene. `INTO v_oppdaterte` ville
        -- SATT summen på nytt for hver tenant, så linjen bare hadde
        -- rapportert den siste (CodeRabbit).
        SELECT count(*) FILTER (WHERE var_ny),
               count(*) FILTER (WHERE NOT var_ny)
          INTO v_n, v_m FROM skrevet;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_m, 0);

        -- LUKKER DE SOM IKKE LENGER ER KANDIDATER. Radene blir
        -- stående: at et funn HAR stått er også en måling.
        WITH kand AS (
            SELECT * FROM public.m19_funnkandidater(v_t, v_dag)),
        lukket AS (
            UPDATE public.adressefunn f
               SET apen = false, lukket_ts = now()
             WHERE f.tenant = v_t AND f.apen
               AND NOT EXISTS (
                   SELECT 1 FROM kand k
                    WHERE k.subjekt_id = f.subjekt_id
                      AND k.funntype = f.funntype)
            RETURNING 1)
        SELECT count(*) INTO v_n FROM lukket;
        v_lukket := v_lukket + coalesce(v_n, 0);

        PERFORM set_config('disponit.tenant', '', true);
    END LOOP;

    RETURN QUERY SELECT v_tenanter, v_nye, v_oppdaterte, v_lukket;
END $$;
REVOKE ALL ON FUNCTION m19_sveip_adresser(INT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 6. RLS. ENABLE + FORCE på alle fem, `tenant_isolasjon` på hver.
-- ------------------------------------------------------------

RESET ROLE;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['adressekrav', 'adressesubjekt',
                             'adresseversjon', 'adressekontroll',
                             'adressefunn']
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
                       ' disponit_adresse_eier', t);
    END LOOP;
END $$;

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — tre gjerder (111s
-- form): bare på SUBJEKTTABELLEN, bare FOR SELECT, bare til eieren, og
-- bare når ingen tenantkontekst står. Sveipen trenger nøyaktig ett
-- kryss-tenant-svar: HVILKE tenanter finnes. Alt annet den leser, leser
-- den inne i én tenants kontekst, gjennom `tenant_isolasjon`.
CREATE POLICY m19_sveip_tenantliste ON adressesubjekt
    FOR SELECT TO disponit_adresse_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- HISTORIKKTABELLENE FÅR IKKE UPDATE, HELLER IKKE FOR EIEREN. Vaktene
-- over stanser den som likevel skulle prøve; dette gjerdet stanser
-- forsøket før det når vakten. To gjerder, av samme grunn som i 110/111.
REVOKE UPDATE ON public.adresseversjon FROM disponit_adresse_eier;
REVOKE UPDATE ON public.adressekontroll FROM disponit_adresse_eier;


-- ------------------------------------------------------------
-- 7. Rettighetene. SP-7: kjøretidsrollen får INGEN tabellrettigheter.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_adresse_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m19_adressestatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m19_subjektene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m19_adressehistorikken(TEXT, UUID, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m19_gjeldende_adresse(TEXT, UUID, DATE) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m19_kontrollene(TEXT, UUID, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m19_kravene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m19_sett_krav(TEXT, INT, INT, TEXT[], TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m19_registrer_subjekt(TEXT, UUID, TEXT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m19_registrer_adresse(TEXT, UUID, UUID, TEXT, TEXT,'
            ' TEXT, TEXT, TEXT, TEXT, TEXT, DATE, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m19_registrer_kontroll(TEXT, UUID, UUID, TEXT, TEXT,'
            ' TEXT, TEXT, TEXT, DATE, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m19_sett_subjektaktiv(TEXT, UUID, BOOLEAN, TEXT)'
            ' TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_adressesveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m19_sveip_adresser(INT)'
            ' TO disponit_adressesveip';
    END IF;
END $$;

-- SVEIPEN ER IKKE KJØRETIDSROLLENS. `m19_funnkandidater` er derimot
-- delt: flaten skal kunne vise hvorfor et funn står, uten å vente på
-- natta.
REVOKE EXECUTE ON FUNCTION m19_sveip_adresser(INT) FROM disponit;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m19_funnkandidater(TEXT, DATE) TO disponit';
    END IF;
END $$;

RESET ROLE;
