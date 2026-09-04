-- 119: M-51 tilskudds- og støtteordningsvakt v1 — ESTIMATET, IKKE
-- SØKNADEN.
-- Sju tenant-skopede tabeller, seksten dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- V1-DOMMEN, ORDRETT FRA VAKTEN: «Sender aldri inn søknad. Estimat
-- presenteres som ESTIMAT MED FORUTSETNINGER, aldri som lovnad.»
--
-- DEN ANDRE SETNINGEN ER DEN SOM FORMER DATAMODELLEN, og den er
-- skarpere enn den ser ut.
--
-- Et tilskuddsestimat er et TALL EN BEDRIFT PLANLEGGER ETTER. Sier vi
-- «dere kan få 400 000», og bedriften ansetter på det grunnlaget, er
-- avstanden mellom estimat og lovnad ikke akademisk — den er
-- lønnsutbetalinger. Og et estimat uten forutsetninger ER en lovnad,
-- fordi ingenting på raden sier hva det hviler på.
--
-- DERFOR: `tilskuddsestimat` HAR INGEN KOLONNE FOR ET BELØP ALENE.
-- Hvert estimat er summen av `estimatpost`-rader, og HVER post peker
-- på en `kildepost` gjennom en NOT NULL fremmednøkkel — en linje i
-- regnskapet, en lønnsart, en timeføring. Og hvert estimat har minst
-- én `estimatforutsetning`, håndhevet av `m51_ferdigstill_estimat`.
--
-- Hadde vi hatt en `belop_ore BIGINT`-kolonne på estimatet, ville
-- invariantene `belop_uten_kildepost` og `estimat_uten_forutsetninger`
-- vært regler noen måtte huske. Nå er de formen på tabellene: et
-- beløp uten kilde kan ikke uttrykkes, og et ferdigstilt estimat uten
-- forutsetninger kan ikke oppstå.
--
-- v1 SENDER INGEN SØKNAD, og det finnes ingen kolonne for «sendt».
-- Estimatet kan merkes KLART TIL GJENNOMGANG — en tilstand hos oss,
-- ikke en handling utad. Samme figur som M-46s utkast (118), og av
-- samme grunn: en innsendt søknad er bindende, og en søknadsfrist er
-- like ubevegelig som en anbudsfrist.
--
-- v1 HENTER INGENTING FRA ORDNINGENE. M-48 fikk klyngens ene unntak
-- (eierbeslutning 3/9) fordi et organisasjonsnummer er offentlige
-- foretaksdata og oppslaget er nødvendig. Her er det annerledes:
-- ordningenes regelverk er dokumenter som endres, og en modul som
-- hentet dem automatisk ville tatt ansvaret for at NØYAKTIG den
-- versjonen er den gjeldende. Ordningen registreres av et menneske,
-- med regelverksversjon og innholdssum.
--
-- DOMMENE v1 HVILER PÅ, HÅNDHEVET I DATAMODELLEN:
--
--   1. HISTORIKKEN OVERSKRIVES ALDRI. Ordninger, kildeposter,
--      estimater, poster og forutsetninger er append-only. M-42s dom
--      (110), gjentatt i 112–118.
--
--   2. HVERT BELØP PEKER PÅ EN KILDEPOST. NOT NULL fremmednøkkel,
--      ingen fritekstbeløp ved siden av.
--
--   3. ET FERDIGSTILT ESTIMAT HAR MINST ÉN FORUTSETNING. Døra nekter
--      ellers — et estimat uten forutsetninger er en lovnad.
--
--   4. BELØP I ØRE, HELTALL. BIGINT, ingen unntak (101s form).
--      `belop_i_flyttall` er den mest banale av invariantene og den
--      dyreste å bryte: et estimat regnet i flyttall gir en bedrift
--      et tall som ikke stemmer med regnskapet de søker på grunnlag
--      av.
--
--   5. ORDNINGENS KRAV ER IKKE MODULENS. Frister, satser og
--      maksbeløp ligger i basen, registrert per ordning og per
--      versjon.
--
-- GRENSEN MOT M-39: M-39 eier LØNNSGRUNNLAGET — hvor mye en navngitt
-- ansatt har jobbet. M-51 eier hva av det som kan telle med i en
-- tilskuddssøknad. v1 kobler dem ikke; kildeposten registreres for
-- seg, med sin egen referanse til der tallet kom fra.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_tilskudd_eier') THEN
        RAISE EXCEPTION 'rollen disponit_tilskudd_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_tilskudd_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `tilskuddskrav` — ÉN per tenant. Tenantens egne terskler for NÅR
-- noe blir et funn. Ordningens EGNE krav står på ordningsraden.
CREATE TABLE tilskuddskrav (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- Hvor mange døgn før søknadsfristen et estimat uten ferdigstilt
    -- utkast blir et funn.
    frist_varsel_dogn INT NOT NULL DEFAULT 21
        CHECK (frist_varsel_dogn BETWEEN 1 AND 365),
    -- Hvor lenge en kildepost regnes som fersk nok til å telle med.
    -- Et regnskapstall fra i fjor er ikke grunnlag for årets søknad.
    kildepost_gyldig_dogn INT NOT NULL DEFAULT 400
        CHECK (kildepost_gyldig_dogn BETWEEN 1 AND 3650),
    -- ESTIMATETS SPENN, i hele prosent. Et estimat er et intervall,
    -- ikke et punkt: `+/- 20 %` sier noe ærlig om usikkerheten som
    -- ett tall aldri kan si. Tenantens valg, fordi hvor forsiktig man
    -- vil være er en forretningsbeslutning.
    usikkerhet_prosent INT NOT NULL DEFAULT 20
        CHECK (usikkerhet_prosent BETWEEN 0 AND 100),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    -- IDEMPOTENSNØKKELEN SOM SATTE DENNE VERSJONEN.
    --
    -- Raden er en singleton per tenant, og har derfor ingen id å
    -- utlede fra nøkkelen slik de andre skrivedørene gjør. Uten
    -- nøkkelen HER ville et gjenspilt kall — en klient som gjentar
    -- etter en tidsavbrutt forbindelse — bumpet `versjon` en gang
    -- til. Og versjonen er ikke pynt: hvert funn bærer
    -- `kravversjon`, så et fantomtall gjør «hvilke terskler gjaldt
    -- da» til et spørsmål ingen kan svare på.
    siste_nokkel TEXT NOT NULL
        CHECK (siste_nokkel ~ '[^[:space:]]'),
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT tilskuddskrav_pk PRIMARY KEY (tenant)
);

-- `stotteordning` — ordningen selv, med SIN regelverksversjon.
--
-- DOM 5: ORDNINGENS KRAV ER IKKE MODULENS. Frist, sats og maksbeløp
-- står HER, per ordning og per versjon — ikke som konstanter i koden.
--
-- FROSSET. Et regelverk som endres er en NY rad: «hvilke regler gjaldt
-- da vi regnet» er hele spørsmålet når et avslag skal forstås.
--
-- v1 HENTER INGEN ORDNING SELV. Registreres av et menneske som har
-- lest regelverket, med versjon og innholdssum.
CREATE TABLE stotteordning (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    ordning_id UUID NOT NULL,
    -- Ordningens egen kode hos forvalter (Innovasjon Norge,
    -- Skattefunn, kommunale ordninger). FRI TEKST: kodingen er
    -- forvalterens, ikke vår.
    ordningskode TEXT NOT NULL CHECK (ordningskode ~ '[^[:space:]]'),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    forvalter TEXT NOT NULL CHECK (forvalter ~ '[^[:space:]]'),
    -- REGELVERKSVERSJONEN. Uten den kan ingen etterpå si hvilke regler
    -- estimatet ble regnet mot.
    regelverksversjon TEXT NOT NULL
        CHECK (regelverksversjon ~ '[^[:space:]]'),
    regelverk_sha256 TEXT NOT NULL
        CHECK (regelverk_sha256 ~ '^[0-9a-f]{64}$'),
    -- ORDNINGENS EGNE TALL, i øre (dom 4). NULL når ordningen ikke
    -- har et tak — og NULL er ærligere enn et oppdiktet stort tall.
    maks_belop_ore BIGINT
        CHECK (maks_belop_ore IS NULL OR maks_belop_ore >= 0),
    -- Støttesats i hele prosent, når ordningen har en. NULL ellers.
    sats_prosent INT
        CHECK (sats_prosent IS NULL
               OR sats_prosent BETWEEN 0 AND 100),
    soknadsfrist TIMESTAMPTZ NOT NULL,
    aktiv BOOLEAN NOT NULL DEFAULT true,
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT stotteordning_pk PRIMARY KEY (tenant, ordning_id),
    -- SAMME ORDNING I SAMME REGELVERKSVERSJON REGISTRERES ÉN GANG.
    CONSTRAINT stotteordning_versjon_unik
        UNIQUE (tenant, ordningskode, regelverksversjon)
);
CREATE INDEX stotteordning_aktive_frist
    ON stotteordning (tenant, soknadsfrist) WHERE aktiv;

-- `kildepost` — DOM 2. DER TALLENE KOMMER FRA.
--
-- Et estimat er summen av poster, og hver post peker hit. Kildeposten
-- er en linje i regnskapet, en lønnsart eller en timeføring — med sin
-- egen referanse til systemet den ble hentet fra.
--
-- FROSSET, med beløp i ØRE.
CREATE TABLE kildepost (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kildepost_id UUID NOT NULL,
    -- Hvilket system tallet kom fra, som et lukket sett. Et tall uten
    -- opphav er et tall ingen kan etterprøve.
    system TEXT NOT NULL
        CONSTRAINT kildepost_system_lukket CHECK (system IN (
            'regnskap', 'lonn', 'timeforing', 'faktura', 'manuell')),
    -- Systemets EGEN referanse: bilagsnummer, lønnsart, prosjektkode.
    ekstern_ref TEXT NOT NULL CHECK (ekstern_ref ~ '[^[:space:]]'),
    beskrivelse TEXT NOT NULL
        CHECK (length(btrim(beskrivelse)) >= 4),
    -- DOM 4: ØRE, HELTALL. Aldri desimaltall.
    belop_ore BIGINT NOT NULL,
    -- Perioden tallet gjelder. En tilskuddssøknad gjelder alltid et
    -- avgrenset tidsrom, og et beløp uten periode kan telles i to
    -- søknader.
    periode_fra DATE NOT NULL,
    periode_til DATE NOT NULL,
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT kildepost_pk PRIMARY KEY (tenant, kildepost_id),
    CONSTRAINT kildepost_periode_stemmer CHECK (
        periode_til >= periode_fra),
    -- SAMME LINJE REGISTRERES ÉN GANG. To rader for samme bilag ville
    -- gjort dobbelttelling til en skrivefeil i stedet for en umulighet.
    CONSTRAINT kildepost_ref_unik
        UNIQUE (tenant, system, ekstern_ref)
);
CREATE INDEX kildepost_periode
    ON kildepost (tenant, periode_fra, periode_til);

-- `tilskuddsestimat` — DOM 3. ESTIMATET, OG LEGG MERKE TIL HVA SOM
-- IKKE ER HER.
--
-- DET FINNES INGEN `belop_ore`-KOLONNE PÅ ESTIMATET.
--
-- Summen er summen av `estimatpost`-radene, og hver av dem peker på en
-- kildepost. Hadde estimatet båret et tall alene, ville
-- `belop_uten_kildepost` vært en regel noen måtte huske ved hver ny
-- skrivevei — og et estimat med et fritt tall ser like ferdig ut som
-- ett bygget av kilder.
--
-- OG DET FINNES INGEN KOLONNE FOR «SENDT». `klar_til_gjennomgang` er
-- en tilstand HOS OSS. Fraværet ER porten `modulen_sendte_soknad`.
--
-- USIKKERHETEN STÅR PÅ RADEN, ikke bare i visningen. Et estimat er et
-- INTERVALL: `+/- 20 %` sier noe ærlig som ett tall aldri kan si, og
-- den som planlegger etter estimatet skal se spennet der tallet står.
CREATE TABLE tilskuddsestimat (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    estimat_id UUID NOT NULL,
    ordning_id UUID NOT NULL,
    versjon INT NOT NULL CHECK (versjon >= 1),
    -- Perioden estimatet gjelder. Må ligge innenfor kildepostenes
    -- perioder — håndhevet i `m51_legg_til_post`.
    periode_fra DATE NOT NULL,
    periode_til DATE NOT NULL,
    -- Usikkerheten som gjaldt da estimatet ble regnet, kopiert fra
    -- `tilskuddskrav` av døra. Endrer tenanten terskelen i morgen,
    -- står gårsdagens estimat med sitt eget spenn.
    usikkerhet_prosent INT NOT NULL
        CHECK (usikkerhet_prosent BETWEEN 0 AND 100),
    kravversjon INT NOT NULL CHECK (kravversjon >= 1),
    klar_til_gjennomgang BOOLEAN NOT NULL DEFAULT false,
    klar_ts TIMESTAMPTZ,
    klar_av TEXT CHECK (klar_av IS NULL OR klar_av ~ '[^[:space:]]'),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT tilskuddsestimat_pk PRIMARY KEY (tenant, estimat_id),
    CONSTRAINT tilskuddsestimat_ordning_fk
        FOREIGN KEY (tenant, ordning_id)
        REFERENCES stotteordning (tenant, ordning_id),
    CONSTRAINT tilskuddsestimat_versjon_unik
        UNIQUE (tenant, ordning_id, versjon),
    CONSTRAINT tilskuddsestimat_periode_stemmer CHECK (
        periode_til >= periode_fra),
    CONSTRAINT tilskuddsestimat_klar_helhet CHECK (
        (NOT klar_til_gjennomgang AND klar_ts IS NULL
         AND klar_av IS NULL)
     OR (klar_til_gjennomgang AND klar_ts IS NOT NULL
         AND klar_av IS NOT NULL))
);
CREATE INDEX tilskuddsestimat_ordning
    ON tilskuddsestimat (tenant, ordning_id, versjon DESC);

-- `estimatpost` — DOM 2. HVERT BELØP PEKER PÅ EN KILDEPOST.
--
-- `kildepost_id` er en NOT NULL fremmednøkkel, og det finnes INGEN
-- fritekstbeløp ved siden av. Beløpet på raden er den ANDELEN av
-- kildeposten som teller med — ordninger dekker sjelden alt — og
-- andelen kan aldri overstige kildepostens eget beløp, håndhevet i
-- døra.
--
-- FROSSET.
CREATE TABLE estimatpost (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    post_id UUID NOT NULL,
    estimat_id UUID NOT NULL,
    kildepost_id UUID NOT NULL,
    -- DEN ANDELEN SOM TELLER MED, i øre (dom 4). Ikke en prosent: en
    -- prosent av et beløp er en utregning noen må gjøre om igjen, og
    -- avrundingen ville flyttet på seg.
    andel_ore BIGINT NOT NULL CHECK (andel_ore >= 0),
    -- HVORFOR akkurat denne andelen. PÅKREVD: en andel uten
    -- begrunnelse er et tall noen må gjette bak.
    begrunnelse TEXT NOT NULL
        CHECK (length(btrim(begrunnelse)) >= 4),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT estimatpost_pk PRIMARY KEY (tenant, post_id),
    CONSTRAINT estimatpost_estimat_fk
        FOREIGN KEY (tenant, estimat_id)
        REFERENCES tilskuddsestimat (tenant, estimat_id),
    CONSTRAINT estimatpost_kilde_fk
        FOREIGN KEY (tenant, kildepost_id)
        REFERENCES kildepost (tenant, kildepost_id),
    -- ÉN POST PER KILDEPOST PER ESTIMAT. To poster på samme kildepost
    -- er dobbelttelling, og det er nettopp den feilen som gjør en
    -- tilskuddssøknad til en tilbakebetalingssak.
    CONSTRAINT estimatpost_kilde_unik
        UNIQUE (tenant, estimat_id, kildepost_id)
);
CREATE INDEX estimatpost_estimat
    ON estimatpost (tenant, estimat_id);

-- `estimatforutsetning` — DOM 3. DET SOM GJØR ESTIMATET TIL ET
-- ESTIMAT.
--
-- «Estimat presenteres som estimat MED FORUTSETNINGER, aldri som
-- lovnad.» Et estimat uten forutsetninger ER en lovnad: ingenting på
-- raden sier hva det hviler på, og den som planlegger etter det har
-- ingen måte å se når grunnlaget svikter.
--
-- `m51_ferdigstill_estimat` NEKTER så lenge det ikke finnes minst én.
--
-- FROSSET.
CREATE TABLE estimatforutsetning (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    forutsetning_id UUID NOT NULL,
    estimat_id UUID NOT NULL,
    -- Lukket sett, fordi klassen avgjør hva som må sjekkes på nytt
    -- når noe endrer seg.
    art TEXT NOT NULL
        CONSTRAINT estimatforutsetning_art_lukket CHECK (art IN (
            'regelverk',        -- ordningen tolkes slik
            'regnskapstall',    -- tallene er ikke endelige
            'bemanning',        -- forutsetter et antall ansatte
            'aktivitet',        -- forutsetter at prosjektet gjennomføres
            'annet')),
    tekst TEXT NOT NULL CHECK (length(btrim(tekst)) >= 8),
    -- OM FORUTSETNINGEN BRISTER, HVA SKJER DA? PÅKREVD: en
    -- forutsetning uten konsekvens er en ansvarsfraskrivelse, ikke en
    -- opplysning. «Faller bort helt» og «reduseres med ca. 30 %» er
    -- to helt forskjellige beskjeder til den som planlegger.
    konsekvens TEXT NOT NULL
        CHECK (length(btrim(konsekvens)) >= 8),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT estimatforutsetning_pk
        PRIMARY KEY (tenant, forutsetning_id),
    CONSTRAINT estimatforutsetning_estimat_fk
        FOREIGN KEY (tenant, estimat_id)
        REFERENCES tilskuddsestimat (tenant, estimat_id)
);
CREATE INDEX estimatforutsetning_estimat
    ON estimatforutsetning (tenant, estimat_id);

-- `tilskuddsfunn` — 112s gjenbruksform, nøklet på (tenant, ordning,
-- funntype).
CREATE TABLE tilskuddsfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    ordning_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CONSTRAINT tilskuddsfunn_type_lukket CHECK (funntype IN (
            'frist_naermer_seg',       -- innen frist_varsel_dogn
            'frist_passert',           -- fristen gikk uten klart estimat
            'estimat_uten_poster',     -- estimat uten et eneste beløp
            'estimat_over_ordningstak', -- sum > ordningens maksbeløp
            'utdatert_kildepost',      -- post eldre enn gyldighetsvinduet
            'ingen_estimat',           -- aktiv ordning uten estimat
            'ingen_krav')),            -- tenanten har ingen terskler
    over_grense INT,
    detalj TEXT,
    -- SUMMEN PÅ FUNNTIDSPUNKTET, i øre. For `estimat_over_ordningstak`
    -- er det selve poenget: «over taket» uten å si hvor mye er en
    -- beskjed man ikke kan handle på.
    sum_ore BIGINT,
    kravversjon INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT tilskuddsfunn_pk
        PRIMARY KEY (tenant, ordning_id, funntype),
    CONSTRAINT tilskuddsfunn_ordning_fk FOREIGN KEY (tenant, ordning_id)
        REFERENCES stotteordning (tenant, ordning_id),
    CONSTRAINT tilskuddsfunn_lukking_helhet CHECK (
        (apen AND lukket_ts IS NULL)
     OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE INDEX tilskuddsfunn_apne
    ON tilskuddsfunn (tenant, funntype) WHERE apen;


-- ------------------------------------------------------------
-- 2. Evidenskjeden. SECURITY DEFINER, eid av
--    `disponit_tilskudd_eier`, SP-1.
-- ------------------------------------------------------------

-- `krev_tenantkontekst` eies av `disponit_m37_claimer` (111s form), så
-- EXECUTE må gis AV den rollen (116s lærdom).
GRANT INSERT ON revisjonslogg TO disponit_tilskudd_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_tilskudd_eier;
RESET ROLE;

-- HERFRA OG TIL SEKSJON 6 EIES ALT SOM LAGES AV TILSKUDDSEIEREN.
SET LOCAL ROLE disponit_tilskudd_eier;

CREATE FUNCTION m51_evidens(p_tenant TEXT, p_ordning_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm51_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm51_tilskudd', 'handling', p_handling,
        'ordning_id', p_ordning_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm51_tilskudd',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:tilskudd', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m51_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;


-- ------------------------------------------------------------
-- 3. Skrivedørene.
-- ------------------------------------------------------------

-- TERSKLENE, MED IDEMPOTENSNØKKELEN SOM DEL AV DØRA.
--
-- De andre skrivedørene her utleder rad-id-en fra nøkkelen, og er
-- idempotente fordi en gjentatt id ikke kan settes inn to ganger.
-- `tilskuddskrav` er en SINGLETON per tenant og har ingen slik id —
-- så uten det som står under, ville en gjenspilt POST økt `versjon`
-- en gang til uten at noe var endret.
--
-- TO UTFALL PÅ SAMME NØKKEL, OG DE ER IKKE DET SAMME:
--
--   samme nøkkel, samme verdier  → GJENSPILL. Returner versjonen som
--                                  alt står der. Ingen bump, ingen
--                                  ny evidens.
--   samme nøkkel, ANDRE verdier  → KONFLIKT. Nøkkelen er klientens
--                                  løfte om at dette er den SAMME
--                                  operasjonen; er verdiene andre,
--                                  er løftet brutt, og å velge én av
--                                  dem i stillhet er verre enn å si
--                                  fra. `unique_violation` blir
--                                  `idempotenskonflikt` i API-laget.
CREATE FUNCTION m51_sett_krav(
    p_tenant TEXT, p_frist_varsel_dogn INT,
    p_kildepost_gyldig_dogn INT, p_usikkerhet_prosent INT,
    p_aktor TEXT, p_nokkel TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT; v_nokkel TEXT; v_likt BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm51_sett_krav');
    IF p_nokkel IS NULL OR btrim(p_nokkel) = '' THEN
        RAISE EXCEPTION 'm51_sett_krav: idempotensnøkkel mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.aktor', p_aktor, true);

    -- LÅS RADEN FØRST, LES NØKKELEN ETTERPÅ. Samme dom som i de tre
    -- andre dørene her: lesningen over en lås er fra transaksjonens
    -- snapshot, og et parallelt `m51_sett_krav` som committer mens vi
    -- venter ville vært usynlig — to gjenspill ville da begge bumpet.
    PERFORM 1 FROM public.tilskuddskrav
     WHERE tenant = p_tenant FOR UPDATE;
    SELECT k.versjon, k.siste_nokkel,
           (k.frist_varsel_dogn = p_frist_varsel_dogn
            AND k.kildepost_gyldig_dogn = p_kildepost_gyldig_dogn
            AND k.usikkerhet_prosent = p_usikkerhet_prosent)
      INTO v_versjon, v_nokkel, v_likt
      FROM public.tilskuddskrav k WHERE k.tenant = p_tenant;

    IF v_nokkel IS NOT NULL AND v_nokkel = p_nokkel THEN
        IF v_likt THEN
            RETURN v_versjon;
        END IF;
        RAISE EXCEPTION 'm51_sett_krav: nøkkelen % er alt brukt på'
            ' andre verdier', p_nokkel
            USING ERRCODE = 'unique_violation';
    END IF;

    INSERT INTO public.tilskuddskrav
        (tenant, frist_varsel_dogn, kildepost_gyldig_dogn,
         usikkerhet_prosent, siste_nokkel, oppdatert_av)
    VALUES (p_tenant, p_frist_varsel_dogn, p_kildepost_gyldig_dogn,
            p_usikkerhet_prosent, btrim(p_nokkel), p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        frist_varsel_dogn = EXCLUDED.frist_varsel_dogn,
        kildepost_gyldig_dogn = EXCLUDED.kildepost_gyldig_dogn,
        usikkerhet_prosent = EXCLUDED.usikkerhet_prosent,
        siste_nokkel = EXCLUDED.siste_nokkel,
        versjon = public.tilskuddskrav.versjon + 1,
        oppdatert = now(),
        oppdatert_av = EXCLUDED.oppdatert_av
    RETURNING versjon INTO v_versjon;

    PERFORM public.m51_evidens(p_tenant, NULL, 'tilskuddskrav_satt',
        p_aktor, jsonb_build_object(
            'versjon', v_versjon,
            'usikkerhet_prosent', p_usikkerhet_prosent));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION
    m51_sett_krav(TEXT, INT, INT, INT, TEXT, TEXT) FROM PUBLIC;


-- DOM 5: ORDNINGENS KRAV ER IKKE MODULENS.
--
-- v1 HENTER INGEN ORDNING SELV. Et menneske har lest regelverket og
-- oppgir versjon og innholdssum — porten `modulen_hentet_eksternt`.
-- Grunnen er at et regelverk som endres gjør gårsdagens estimat feil
-- uten at noe i systemet vet det; en modul som hentet automatisk ville
-- tatt ansvaret for at NØYAKTIG den versjonen er den gjeldende.
CREATE FUNCTION m51_registrer_ordning(
    p_tenant TEXT, p_ordning_id UUID, p_ordningskode TEXT,
    p_navn TEXT, p_forvalter TEXT, p_regelverksversjon TEXT,
    p_regelverk_sha256 TEXT, p_maks_belop_ore BIGINT,
    p_sats_prosent INT, p_soknadsfrist TIMESTAMPTZ, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm51_registrer_ordning');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    INSERT INTO public.stotteordning
        (tenant, ordning_id, ordningskode, navn, forvalter,
         regelverksversjon, regelverk_sha256, maks_belop_ore,
         sats_prosent, soknadsfrist, registrert_av)
    VALUES (p_tenant, p_ordning_id, btrim(p_ordningskode),
            btrim(p_navn), btrim(p_forvalter),
            btrim(p_regelverksversjon),
            lower(btrim(p_regelverk_sha256)), p_maks_belop_ore,
            p_sats_prosent, p_soknadsfrist, p_aktor);

    PERFORM public.m51_evidens(p_tenant, p_ordning_id,
        'stotteordning_registrert', p_aktor,
        jsonb_build_object('ordningskode', btrim(p_ordningskode),
                           'regelverksversjon',
                           btrim(p_regelverksversjon),
                           'soknadsfrist', p_soknadsfrist));
END $$;
REVOKE ALL ON FUNCTION m51_registrer_ordning(
    TEXT, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, BIGINT, INT,
    TIMESTAMPTZ, TEXT) FROM PUBLIC;


CREATE FUNCTION m51_registrer_kildepost(
    p_tenant TEXT, p_kildepost_id UUID, p_system TEXT,
    p_ekstern_ref TEXT, p_beskrivelse TEXT, p_belop_ore BIGINT,
    p_periode_fra DATE, p_periode_til DATE, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm51_registrer_kildepost');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    IF p_periode_til > current_date THEN
        RAISE EXCEPTION 'm51_registrer_kildepost: en kildepost kan'
            ' ikke gjelde en periode som ikke er over (%). Et tall fra'
            ' framtida er et anslag, ikke en kilde', p_periode_til
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO public.kildepost
        (tenant, kildepost_id, system, ekstern_ref, beskrivelse,
         belop_ore, periode_fra, periode_til, registrert_av)
    VALUES (p_tenant, p_kildepost_id, p_system, btrim(p_ekstern_ref),
            btrim(p_beskrivelse), p_belop_ore, p_periode_fra,
            p_periode_til, p_aktor);

    -- Kildeposten hører tenanten til, ikke én ordning.
    PERFORM public.m51_evidens(p_tenant, NULL,
        'kildepost_registrert', p_aktor,
        jsonb_build_object('kildepost_id', p_kildepost_id,
                           'system', p_system,
                           'belop_ore', p_belop_ore));
END $$;
REVOKE ALL ON FUNCTION m51_registrer_kildepost(
    TEXT, UUID, TEXT, TEXT, TEXT, BIGINT, DATE, DATE, TEXT)
    FROM PUBLIC;


-- Versjonen REGNES her (118s form): en kaller som fikk oppgi den
-- kunne gjenbrukt et nummer og skrevet over historikken.
--
-- USIKKERHETEN OG KRAVVERSJONEN KOPIERES INN. Endrer tenanten
-- terskelen i morgen, står gårsdagens estimat med sitt eget spenn —
-- og den som planla etter det kan se hva som gjaldt da.
CREATE FUNCTION m51_opprett_estimat(
    p_tenant TEXT, p_estimat_id UUID, p_ordning_id UUID,
    p_periode_fra DATE, p_periode_til DATE, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_aktiv BOOLEAN;
    v_versjon INT;
    v_usikkerhet INT;
    v_kravversjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm51_opprett_estimat');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    IF p_periode_til < p_periode_fra THEN
        RAISE EXCEPTION 'm51_opprett_estimat: perioden slutter før den'
            ' begynner (% til %)', p_periode_fra, p_periode_til
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT o.aktiv INTO v_aktiv FROM public.stotteordning o
     WHERE o.tenant = p_tenant AND o.ordning_id = p_ordning_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm51_opprett_estimat: ukjent ordning %',
            p_ordning_id USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_aktiv THEN
        RAISE EXCEPTION 'm51_opprett_estimat: ordningen % er'
            ' deaktivert', p_ordning_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT k.usikkerhet_prosent, k.versjon
      INTO v_usikkerhet, v_kravversjon
      FROM public.tilskuddskrav k WHERE k.tenant = p_tenant;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm51_opprett_estimat: tenanten % har ingen'
            ' tilskuddskrav — usikkerheten er tenantens, og et estimat'
            ' uten spenn er et punktanslag som ser ut som en lovnad',
            p_tenant USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT coalesce(max(e.versjon), 0) + 1 INTO v_versjon
      FROM public.tilskuddsestimat e
     WHERE e.tenant = p_tenant AND e.ordning_id = p_ordning_id;

    INSERT INTO public.tilskuddsestimat
        (tenant, estimat_id, ordning_id, versjon, periode_fra,
         periode_til, usikkerhet_prosent, kravversjon, opprettet_av)
    VALUES (p_tenant, p_estimat_id, p_ordning_id, v_versjon,
            p_periode_fra, p_periode_til, v_usikkerhet, v_kravversjon,
            p_aktor);

    PERFORM public.m51_evidens(p_tenant, p_ordning_id,
        'tilskuddsestimat_opprettet', p_aktor,
        jsonb_build_object('estimat_id', p_estimat_id,
                           'versjon', v_versjon,
                           'usikkerhet_prosent', v_usikkerhet));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION m51_opprett_estimat(
    TEXT, UUID, UUID, DATE, DATE, TEXT) FROM PUBLIC;


-- DOM 2. HVERT BELØP PEKER PÅ EN KILDEPOST.
--
-- Fremmednøkkelen gjør et beløp uten kilde umulig; denne døra legger
-- til de tre tingene basen ikke kan uttrykke i en CHECK på tvers av
-- tabeller:
--
--   * ANDELEN KAN IKKE OVERSTIGE KILDEPOSTEN. Å telle med mer enn det
--     som faktisk står i regnskapet er ikke et estimat, det er en
--     feil — og det er den feilen som gjør en tilskuddssak til en
--     tilbakebetalingssak.
--
--   * KILDEPOSTENS PERIODE MÅ OVERLAPPE ESTIMATETS. Et beløp fra en
--     annen periode kan telles i to søknader.
--
--   * KILDEPOSTEN MÅ VÆRE FERSK NOK. Et regnskapstall fra i fjor er
--     ikke grunnlag for årets søknad; vinduet er tenantens.
CREATE FUNCTION m51_legg_til_post(
    p_tenant TEXT, p_post_id UUID, p_estimat_id UUID,
    p_kildepost_id UUID, p_andel_ore BIGINT, p_begrunnelse TEXT,
    p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_ordning UUID;
    v_klar BOOLEAN;
    v_e_fra DATE;
    v_e_til DATE;
    v_belop BIGINT;
    v_k_fra DATE;
    v_k_til DATE;
    v_k_reg TIMESTAMPTZ;
    v_gyldig_dogn INT;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm51_legg_til_post');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    SELECT e.ordning_id, e.klar_til_gjennomgang, e.periode_fra,
           e.periode_til
      INTO v_ordning, v_klar, v_e_fra, v_e_til
      FROM public.tilskuddsestimat e
     WHERE e.tenant = p_tenant AND e.estimat_id = p_estimat_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm51_legg_til_post: ukjent estimat %',
            p_estimat_id USING ERRCODE = 'no_data_found';
    END IF;
    -- Ordningen låses: den ene muterbare foreldreraden (M-42s
    -- lærdom, 110).
    PERFORM 1 FROM public.stotteordning
     WHERE tenant = p_tenant AND ordning_id = v_ordning FOR UPDATE;
    -- OG KLARMERKET LESES PÅ NYTT UNDER LÅSEN (CodeRabbit, 119).
    -- Lesningen over er fra transaksjonens snapshot. En samtidig
    -- ferdigstilling som committer mens vi venter på låsen, er ellers
    -- usynlig her — og raden ville landet i noe som alt er
    -- gjennomgått. Radvakten fanger den ikke: den utløses på UPDATE av
    -- foreldretabellen, ikke på INSERT i barnetabellen.
    --
    -- SAMME FEIL SOM I `m48_fullfor_oppslag` (116) og
    -- `m46_registrer_punkt` (118). Den ble rettet
    -- der og skrevet på nytt her; mønsteret står nå i alle fire.
    SELECT e.klar_til_gjennomgang INTO v_klar
      FROM public.tilskuddsestimat e
     WHERE e.tenant = p_tenant AND e.estimat_id = p_estimat_id;
    IF v_klar THEN
        RAISE EXCEPTION 'm51_legg_til_post: estimatet % er merket'
            ' klart — en ny post hører til et nytt estimat',
            p_estimat_id USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT kp.belop_ore, kp.periode_fra, kp.periode_til, kp.registrert
      INTO v_belop, v_k_fra, v_k_til, v_k_reg
      FROM public.kildepost kp
     WHERE kp.tenant = p_tenant AND kp.kildepost_id = p_kildepost_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm51_legg_til_post: ukjent kildepost %',
            p_kildepost_id USING ERRCODE = 'no_data_found';
    END IF;

    IF p_andel_ore > v_belop THEN
        RAISE EXCEPTION 'm51_legg_til_post: andelen (% øre) er større'
            ' enn kildeposten (% øre). Å telle med mer enn det som'
            ' står i regnskapet er ikke et estimat — det er feilen som'
            ' gjør en tilskuddssak til en tilbakebetalingssak',
            p_andel_ore, v_belop
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF v_k_til < v_e_fra OR v_k_fra > v_e_til THEN
        RAISE EXCEPTION 'm51_legg_til_post: kildeposten gjelder % til'
            ' %, utenfor estimatets periode % til %. Et beløp fra en'
            ' annen periode kan telles i to søknader',
            v_k_fra, v_k_til, v_e_fra, v_e_til
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT k.kildepost_gyldig_dogn INTO v_gyldig_dogn
      FROM public.tilskuddskrav k WHERE k.tenant = p_tenant;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm51_legg_til_post: tenanten % har ingen'
            ' tilskuddskrav', p_tenant
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_k_reg < now() - make_interval(days => v_gyldig_dogn) THEN
        RAISE EXCEPTION 'm51_legg_til_post: kildeposten % er eldre enn'
            ' tenantens gyldighetsvindu på % døgn — et regnskapstall'
            ' fra i fjor er ikke grunnlag for årets søknad',
            p_kildepost_id, v_gyldig_dogn
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO public.estimatpost
        (tenant, post_id, estimat_id, kildepost_id, andel_ore,
         begrunnelse, registrert_av)
    VALUES (p_tenant, p_post_id, p_estimat_id, p_kildepost_id,
            p_andel_ore, btrim(p_begrunnelse), p_aktor);

    PERFORM public.m51_evidens(p_tenant, v_ordning,
        'estimatpost_lagt_til', p_aktor,
        jsonb_build_object('post_id', p_post_id,
                           'kildepost_id', p_kildepost_id,
                           'andel_ore', p_andel_ore));
END $$;
REVOKE ALL ON FUNCTION m51_legg_til_post(
    TEXT, UUID, UUID, UUID, BIGINT, TEXT, TEXT) FROM PUBLIC;


CREATE FUNCTION m51_legg_til_forutsetning(
    p_tenant TEXT, p_forutsetning_id UUID, p_estimat_id UUID,
    p_art TEXT, p_tekst TEXT, p_konsekvens TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_ordning UUID;
    v_klar BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm51_legg_til_forutsetning');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    SELECT e.ordning_id, e.klar_til_gjennomgang INTO v_ordning, v_klar
      FROM public.tilskuddsestimat e
     WHERE e.tenant = p_tenant AND e.estimat_id = p_estimat_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm51_legg_til_forutsetning: ukjent estimat %',
            p_estimat_id USING ERRCODE = 'no_data_found';
    END IF;
    PERFORM 1 FROM public.stotteordning
     WHERE tenant = p_tenant AND ordning_id = v_ordning FOR UPDATE;
    -- KLARMERKET LESES PÅ NYTT UNDER LÅSEN (samme dom som over).
    SELECT e.klar_til_gjennomgang INTO v_klar
      FROM public.tilskuddsestimat e
     WHERE e.tenant = p_tenant AND e.estimat_id = p_estimat_id;
    IF v_klar THEN
        RAISE EXCEPTION 'm51_legg_til_forutsetning: estimatet % er'
            ' merket klart — en ny forutsetning hører til et nytt'
            ' estimat', p_estimat_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO public.estimatforutsetning
        (tenant, forutsetning_id, estimat_id, art, tekst, konsekvens,
         registrert_av)
    VALUES (p_tenant, p_forutsetning_id, p_estimat_id, p_art,
            btrim(p_tekst), btrim(p_konsekvens), p_aktor);

    PERFORM public.m51_evidens(p_tenant, v_ordning,
        'estimatforutsetning_lagt_til', p_aktor,
        jsonb_build_object('forutsetning_id', p_forutsetning_id,
                           'art', p_art));
END $$;
REVOKE ALL ON FUNCTION m51_legg_til_forutsetning(
    TEXT, UUID, UUID, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;


-- DOM 3. MODULENS VIKTIGSTE DØR.
--
-- «Estimat presenteres som estimat MED FORUTSETNINGER, aldri som
-- lovnad.» Her får den setningen tenner: et estimat kan ikke merkes
-- klart uten minst én forutsetning, og heller ikke uten en eneste
-- post.
--
-- ET ESTIMAT UTEN FORUTSETNINGER ER EN LOVNAD. Ingenting på raden sier
-- hva tallet hviler på, og den som planlegger etter det har ingen måte
-- å se når grunnlaget svikter. Det er forskjellen mellom «dere kan få
-- 400 000 hvis prosjektet gjennomføres som beskrevet og regnskapet
-- står» og «dere får 400 000».
--
-- ET ESTIMAT UTEN POSTER er verre: et tall uten noe bak. Summen ville
-- vært null, og null ser ut som et svar.
--
-- SVARET BÆRER SUMMEN OG SPENNET. Den som merker klart får se hva
-- estimatet faktisk sier — nedre og øvre grense i øre — i stedet for
-- bare et «ok».
CREATE FUNCTION m51_ferdigstill_estimat(
    p_tenant TEXT, p_estimat_id UUID, p_aktor TEXT)
RETURNS TABLE (sum_ore BIGINT, nedre_ore BIGINT, ovre_ore BIGINT,
               antall_poster INT, antall_forutsetninger INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_ordning UUID;
    v_klar BOOLEAN;
    v_usikkerhet INT;
    v_poster INT;
    v_forutsetninger INT;
    v_sum BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm51_ferdigstill_estimat');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    SELECT e.ordning_id, e.klar_til_gjennomgang, e.usikkerhet_prosent
      INTO v_ordning, v_klar, v_usikkerhet
      FROM public.tilskuddsestimat e
     WHERE e.tenant = p_tenant AND e.estimat_id = p_estimat_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm51_ferdigstill_estimat: ukjent estimat %',
            p_estimat_id USING ERRCODE = 'no_data_found';
    END IF;
    PERFORM 1 FROM public.stotteordning
     WHERE tenant = p_tenant AND ordning_id = v_ordning FOR UPDATE;
    -- KLARMERKET LESES PÅ NYTT UNDER LÅSEN (samme dom som over): to
    -- samtidige ferdigstillinger skal gi ÉN, ikke to evidenslinjer.
    SELECT e.klar_til_gjennomgang INTO v_klar
      FROM public.tilskuddsestimat e
     WHERE e.tenant = p_tenant AND e.estimat_id = p_estimat_id;

    SELECT count(*), coalesce(sum(p.andel_ore), 0)::bigint
      INTO v_poster, v_sum
      FROM public.estimatpost p
     WHERE p.tenant = p_tenant AND p.estimat_id = p_estimat_id;
    SELECT count(*) INTO v_forutsetninger
      FROM public.estimatforutsetning f
     WHERE f.tenant = p_tenant AND f.estimat_id = p_estimat_id;

    IF v_klar THEN
        -- Idempotent: returner tallene som gjelder.
        RETURN QUERY SELECT v_sum,
            v_sum - (v_sum * v_usikkerhet) / 100,
            v_sum + (v_sum * v_usikkerhet) / 100,
            v_poster, v_forutsetninger;
        RETURN;
    END IF;

    IF v_poster = 0 THEN
        RAISE EXCEPTION 'm51_ferdigstill_estimat: estimatet har ingen'
            ' poster. Et tall uten noe bak ville vært null, og null'
            ' ser ut som et svar'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_forutsetninger = 0 THEN
        RAISE EXCEPTION 'm51_ferdigstill_estimat: estimatet har ingen'
            ' forutsetninger. Et estimat uten forutsetninger ER en'
            ' lovnad — ingenting sier hva tallet hviler på, og den som'
            ' planlegger etter det kan ikke se når grunnlaget svikter'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.tilskuddsestimat
       SET klar_til_gjennomgang = true, klar_ts = now(),
           klar_av = p_aktor
     WHERE tenant = p_tenant AND estimat_id = p_estimat_id;

    PERFORM public.m51_evidens(p_tenant, v_ordning,
        'estimat_ferdigstilt', p_aktor,
        jsonb_build_object('estimat_id', p_estimat_id,
                           'sum_ore', v_sum,
                           'antall_poster', v_poster,
                           'antall_forutsetninger', v_forutsetninger));
    -- SPENNET REGNES I HELTALL. `(sum * prosent) / 100` på BIGINT er
    -- heltallsdivisjon hele veien — ingen flyttall noe sted, som
    -- invarianten `belop_i_flyttall` krever.
    RETURN QUERY SELECT v_sum,
        v_sum - (v_sum * v_usikkerhet) / 100,
        v_sum + (v_sum * v_usikkerhet) / 100,
        v_poster, v_forutsetninger;
END $$;
REVOKE ALL ON FUNCTION m51_ferdigstill_estimat(TEXT, UUID, TEXT)
    FROM PUBLIC;


CREATE FUNCTION m51_sett_ordningaktiv(
    p_tenant TEXT, p_ordning_id UUID, p_aktiv BOOLEAN, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_var BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm51_sett_ordningaktiv');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    SELECT aktiv INTO v_var FROM public.stotteordning
     WHERE tenant = p_tenant AND ordning_id = p_ordning_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm51_sett_ordningaktiv: ukjent ordning %',
            p_ordning_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_var = p_aktiv THEN
        RETURN;  -- idempotent
    END IF;

    UPDATE public.stotteordning SET aktiv = p_aktiv
     WHERE tenant = p_tenant AND ordning_id = p_ordning_id;

    -- HISTORIKKEN BLIR STÅENDE: estimater, poster og forutsetninger er
    -- frosset. «Vi søker ikke på denne» er ikke «ordningen fantes
    -- aldri».
    PERFORM public.m51_evidens(p_tenant, p_ordning_id,
        CASE WHEN p_aktiv THEN 'ordning_aktivert'
             ELSE 'ordning_deaktivert' END, p_aktor, '{}'::jsonb);
END $$;
REVOKE ALL ON FUNCTION m51_sett_ordningaktiv(
    TEXT, UUID, BOOLEAN, TEXT) FROM PUBLIC;


CREATE FUNCTION m51_lukk_funn(
    p_tenant TEXT, p_ordning_id UUID, p_funntype TEXT, p_notat TEXT,
    p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_apen BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm51_lukk_funn');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    IF length(btrim(coalesce(p_notat, ''))) < 4 THEN
        RAISE EXCEPTION 'm51_lukk_funn: et funn lukkes med en'
            ' begrunnelse, ikke med et klikk'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- ET ESTIMAT OVER ORDNINGENS TAK LUKKES IKKE HER, av samme grunn
    -- som M-46s udekkede absolutte krav (118) og M-49s bekreftede
    -- treff (117): et estimat som overstiger taket vil bli avkortet
    -- eller avslått, og en knapp som gjorde den observasjonen borte
    -- ville sett ut som saksbehandling. Funnet lukkes når summen
    -- FAKTISK kommer under taket, eller når ordningen deaktiveres.
    IF p_funntype = 'estimat_over_ordningstak' THEN
        RAISE EXCEPTION 'm51_lukk_funn: et estimat over ordningens tak'
            ' kan ikke lukkes bort. Det lukkes når summen kommer under'
            ' taket, eller når ordningen deaktiveres'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT apen INTO v_apen FROM public.tilskuddsfunn
     WHERE tenant = p_tenant AND ordning_id = p_ordning_id
       AND funntype = p_funntype FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm51_lukk_funn: ukjent funn %/%', p_ordning_id,
            p_funntype USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_apen THEN
        RETURN;  -- idempotent
    END IF;

    UPDATE public.tilskuddsfunn
       SET apen = false, lukket_ts = now()
     WHERE tenant = p_tenant AND ordning_id = p_ordning_id
       AND funntype = p_funntype;

    PERFORM public.m51_evidens(p_tenant, p_ordning_id, 'funn_lukket',
        p_aktor, jsonb_build_object('funntype', p_funntype,
                                    'notat', btrim(p_notat)));
END $$;
REVOKE ALL ON FUNCTION m51_lukk_funn(TEXT, UUID, TEXT, TEXT, TEXT)
    FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Lesedørene.
-- ------------------------------------------------------------

CREATE FUNCTION m51_kravene(p_tenant TEXT)
RETURNS TABLE (frist_varsel_dogn INT, kildepost_gyldig_dogn INT,
               usikkerhet_prosent INT, versjon INT,
               oppdatert TIMESTAMPTZ, oppdatert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm51_kravene');
    RETURN QUERY
    SELECT k.frist_varsel_dogn, k.kildepost_gyldig_dogn,
           k.usikkerhet_prosent, k.versjon, k.oppdatert,
           k.oppdatert_av
      FROM public.tilskuddskrav k WHERE k.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m51_kravene(TEXT) FROM PUBLIC;


-- ORDNINGENE MED SITT SISTE ESTIMAT OG SUMMEN AV DET.
--
-- SUMMEN REGNES HER, ikke i flaten. To lesere skal ikke kunne komme
-- til hver sin konklusjon om hva et estimat sier — og spennet regnes
-- i HELTALL, som `belop_i_flyttall` krever.
CREATE FUNCTION m51_ordningene(p_tenant TEXT, p_grense INT)
RETURNS TABLE (ordning_id UUID, ordningskode TEXT, navn TEXT,
               forvalter TEXT, regelverksversjon TEXT,
               maks_belop_ore BIGINT, sats_prosent INT,
               soknadsfrist TIMESTAMPTZ, aktiv BOOLEAN,
               dogn_til_frist INT, siste_estimat INT,
               estimat_id UUID, klar BOOLEAN, sum_ore BIGINT,
               nedre_ore BIGINT, ovre_ore BIGINT,
               antall_poster BIGINT, antall_forutsetninger BIGINT,
               apne_funn BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm51_ordningene');
    IF p_grense IS NULL OR p_grense < 1 OR p_grense > 500 THEN
        RAISE EXCEPTION 'm51_ordningene: grensen må være 1..500 (%)',
            p_grense USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    SELECT o.ordning_id, o.ordningskode, o.navn, o.forvalter,
           o.regelverksversjon, o.maks_belop_ore, o.sats_prosent,
           o.soknadsfrist, o.aktiv,
           (o.soknadsfrist::date - current_date),
           e.versjon, e.estimat_id, e.klar, e.sum_ore, e.nedre,
           e.ovre, coalesce(e.poster, 0),
           coalesce(e.forutsetninger, 0), coalesce(f.antall, 0)
      FROM public.stotteordning o
      LEFT JOIN LATERAL (
           SELECT ee.versjon, ee.estimat_id,
                  ee.klar_til_gjennomgang AS klar,
                  s.sum_ore,
                  -- SPENNET I HELTALL. `sum()` paa BIGINT gir NUMERIC
                  -- i PostgreSQL, saa `s.sum_ore` er alt castet til
                  -- bigint over; her er all aritmetikk heltall
                  -- (`belop_i_flyttall`).
                  s.sum_ore - (s.sum_ore * ee.usikkerhet_prosent) / 100
                      AS nedre,
                  s.sum_ore + (s.sum_ore * ee.usikkerhet_prosent) / 100
                      AS ovre,
                  s.poster,
                  (SELECT count(*) FROM public.estimatforutsetning ff
                    WHERE ff.tenant = ee.tenant
                      AND ff.estimat_id = ee.estimat_id)
                      AS forutsetninger
             FROM public.tilskuddsestimat ee
             CROSS JOIN LATERAL (
                  SELECT coalesce(sum(pp.andel_ore), 0)::bigint AS sum_ore,
                         count(*) AS poster
                    FROM public.estimatpost pp
                   WHERE pp.tenant = ee.tenant
                     AND pp.estimat_id = ee.estimat_id) s
            WHERE ee.tenant = o.tenant AND ee.ordning_id = o.ordning_id
            ORDER BY ee.versjon DESC
            LIMIT 1) e ON true
      LEFT JOIN LATERAL (
           SELECT count(*) AS antall FROM public.tilskuddsfunn tf
            WHERE tf.tenant = o.tenant
              AND tf.ordning_id = o.ordning_id AND tf.apen) f ON true
     WHERE o.tenant = p_tenant
     ORDER BY o.aktiv DESC, o.soknadsfrist ASC
     LIMIT p_grense;
END $$;
REVOKE ALL ON FUNCTION m51_ordningene(TEXT, INT) FROM PUBLIC;


-- POSTENE MED SIN KILDE. Hver rad viser HVOR tallet kom fra — system,
-- referanse og kildepostens eget beløp — så andelen kan etterprøves
-- uten å slå opp noe annet sted.
CREATE FUNCTION m51_postene(p_tenant TEXT, p_estimat_id UUID)
RETURNS TABLE (post_id UUID, kildepost_id UUID, system TEXT,
               ekstern_ref TEXT, beskrivelse TEXT,
               kilde_belop_ore BIGINT, andel_ore BIGINT,
               begrunnelse TEXT, periode_fra DATE, periode_til DATE,
               registrert TIMESTAMPTZ, registrert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm51_postene');
    RETURN QUERY
    SELECT p.post_id, p.kildepost_id, kp.system, kp.ekstern_ref,
           kp.beskrivelse, kp.belop_ore, p.andel_ore, p.begrunnelse,
           kp.periode_fra, kp.periode_til, p.registrert,
           p.registrert_av
      FROM public.estimatpost p
      JOIN public.kildepost kp
        ON kp.tenant = p.tenant AND kp.kildepost_id = p.kildepost_id
     WHERE p.tenant = p_tenant AND p.estimat_id = p_estimat_id
     ORDER BY kp.periode_fra, kp.ekstern_ref;
END $$;
REVOKE ALL ON FUNCTION m51_postene(TEXT, UUID) FROM PUBLIC;


CREATE FUNCTION m51_forutsetningene(p_tenant TEXT, p_estimat_id UUID)
RETURNS TABLE (forutsetning_id UUID, art TEXT, tekst TEXT,
               konsekvens TEXT, registrert TIMESTAMPTZ,
               registrert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm51_forutsetningene');
    RETURN QUERY
    SELECT f.forutsetning_id, f.art, f.tekst, f.konsekvens,
           f.registrert, f.registrert_av
      FROM public.estimatforutsetning f
     WHERE f.tenant = p_tenant AND f.estimat_id = p_estimat_id
     ORDER BY f.art, f.registrert;
END $$;
REVOKE ALL ON FUNCTION m51_forutsetningene(TEXT, UUID) FROM PUBLIC;


CREATE FUNCTION m51_estimatene(p_tenant TEXT, p_ordning_id UUID)
RETURNS TABLE (estimat_id UUID, versjon INT, periode_fra DATE,
               periode_til DATE, usikkerhet_prosent INT,
               kravversjon INT, klar_til_gjennomgang BOOLEAN,
               klar_ts TIMESTAMPTZ, klar_av TEXT,
               opprettet TIMESTAMPTZ, opprettet_av TEXT,
               sum_ore BIGINT, antall_poster BIGINT,
               antall_forutsetninger BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm51_estimatene');
    RETURN QUERY
    SELECT e.estimat_id, e.versjon, e.periode_fra, e.periode_til,
           e.usikkerhet_prosent, e.kravversjon,
           e.klar_til_gjennomgang, e.klar_ts, e.klar_av, e.opprettet,
           e.opprettet_av,
           (SELECT coalesce(sum(p.andel_ore), 0)::bigint
              FROM public.estimatpost p
             WHERE p.tenant = e.tenant
               AND p.estimat_id = e.estimat_id),
           (SELECT count(*) FROM public.estimatpost p
             WHERE p.tenant = e.tenant
               AND p.estimat_id = e.estimat_id),
           (SELECT count(*) FROM public.estimatforutsetning f
             WHERE f.tenant = e.tenant
               AND f.estimat_id = e.estimat_id)
      FROM public.tilskuddsestimat e
     WHERE e.tenant = p_tenant AND e.ordning_id = p_ordning_id
     ORDER BY e.versjon DESC;
END $$;
REVOKE ALL ON FUNCTION m51_estimatene(TEXT, UUID) FROM PUBLIC;


CREATE FUNCTION m51_kildepostene(p_tenant TEXT, p_grense INT)
RETURNS TABLE (kildepost_id UUID, system TEXT, ekstern_ref TEXT,
               beskrivelse TEXT, belop_ore BIGINT, periode_fra DATE,
               periode_til DATE, registrert TIMESTAMPTZ,
               registrert_av TEXT, fersk BOOLEAN, brukt_i_poster BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_dogn INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm51_kildepostene');
    IF p_grense IS NULL OR p_grense < 1 OR p_grense > 500 THEN
        RAISE EXCEPTION 'm51_kildepostene: grensen må være 1..500 (%)',
            p_grense USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Ferskheten regnes HER, mot tenantens eget vindu, så flaten og
    -- døra aldri kan mene forskjellige ting om hva som er for gammelt.
    SELECT k.kildepost_gyldig_dogn INTO v_dogn
      FROM public.tilskuddskrav k WHERE k.tenant = p_tenant;
    RETURN QUERY
    SELECT kp.kildepost_id, kp.system, kp.ekstern_ref, kp.beskrivelse,
           kp.belop_ore, kp.periode_fra, kp.periode_til, kp.registrert,
           kp.registrert_av,
           CASE WHEN v_dogn IS NULL THEN NULL
                ELSE kp.registrert
                     >= now() - make_interval(days => v_dogn) END,
           (SELECT count(*) FROM public.estimatpost p
             WHERE p.tenant = kp.tenant
               AND p.kildepost_id = kp.kildepost_id)
      FROM public.kildepost kp
     WHERE kp.tenant = p_tenant
     ORDER BY kp.periode_fra DESC, kp.registrert DESC
     LIMIT p_grense;
END $$;
REVOKE ALL ON FUNCTION m51_kildepostene(TEXT, INT) FROM PUBLIC;


CREATE FUNCTION m51_tilskuddsstatus(p_tenant TEXT)
RETURNS TABLE (ordninger BIGINT, aktive BIGINT, med_estimat BIGINT,
               klare BIGINT, sum_klare_ore BIGINT,
               naermeste_frist TIMESTAMPTZ, apne_funn BIGINT,
               kildeposter BIGINT, utdaterte_kildeposter BIGINT,
               har_krav BOOLEAN, kravversjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_dogn INT;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm51_tilskuddsstatus');
    SELECT k.kildepost_gyldig_dogn INTO v_dogn
      FROM public.tilskuddskrav k WHERE k.tenant = p_tenant;
    RETURN QUERY
    SELECT (SELECT count(*) FROM public.stotteordning o
             WHERE o.tenant = p_tenant),
           (SELECT count(*) FROM public.stotteordning o
             WHERE o.tenant = p_tenant AND o.aktiv),
           (SELECT count(DISTINCT e.ordning_id)
              FROM public.tilskuddsestimat e
             WHERE e.tenant = p_tenant),
           -- KLARE, MEN ÉN PER ORDNING. Estimatene er versjonerte
           -- og append-only, og et nytt estimat kan lages mens det
           -- forrige står som klart: en ordning kan derfor ha BÅDE v1
           -- og v2 med `klar_til_gjennomgang`. Talte vi begge, ville
           -- sammendraget vist en sum ingen ordning kan få — og det
           -- er nettopp dette tallet en bedrift planlegger etter.
           -- REGELEN ER DEN SAMME SOM `m51_ordningene` BRUKER:
           -- nyeste versjon per ordning, og bare den.
           (SELECT count(*) FROM (
                SELECT DISTINCT ON (e.ordning_id)
                       e.klar_til_gjennomgang AS klar
                  FROM public.tilskuddsestimat e
                 WHERE e.tenant = p_tenant
                 ORDER BY e.ordning_id, e.versjon DESC) n
             WHERE n.klar),
           -- SUMMEN AV DE KLARE ESTIMATENE, samme utvalg.
           (SELECT coalesce(sum(
                -- HELTALL HELE VEIEN: den indre `sum()` castes for
                -- seg, ellers legger den ytre sammen numeric-verdier
                -- og `belop_i_flyttall` hviler paa den siste casten
                -- alene.
                (SELECT coalesce(sum(p.andel_ore), 0)::bigint
                   FROM public.estimatpost p
                  WHERE p.tenant = p_tenant
                    AND p.estimat_id = n.estimat_id)), 0)::bigint
              FROM (SELECT DISTINCT ON (e.ordning_id)
                           e.estimat_id,
                           e.klar_til_gjennomgang AS klar
                      FROM public.tilskuddsestimat e
                     WHERE e.tenant = p_tenant
                     ORDER BY e.ordning_id, e.versjon DESC) n
             WHERE n.klar),
           (SELECT min(o.soknadsfrist) FROM public.stotteordning o
             WHERE o.tenant = p_tenant AND o.aktiv
               AND o.soknadsfrist >= now()),
           (SELECT count(*) FROM public.tilskuddsfunn f
             WHERE f.tenant = p_tenant AND f.apen),
           (SELECT count(*) FROM public.kildepost kp
             WHERE kp.tenant = p_tenant),
           (SELECT count(*) FROM public.kildepost kp
             WHERE kp.tenant = p_tenant AND v_dogn IS NOT NULL
               AND kp.registrert
                   < now() - make_interval(days => v_dogn)),
           EXISTS (SELECT 1 FROM public.tilskuddskrav k
                    WHERE k.tenant = p_tenant),
           (SELECT k.versjon FROM public.tilskuddskrav k
             WHERE k.tenant = p_tenant);
END $$;
REVOKE ALL ON FUNCTION m51_tilskuddsstatus(TEXT) FROM PUBLIC;


CREATE FUNCTION m51_funnene(p_tenant TEXT, p_bare_apne BOOLEAN)
RETURNS TABLE (ordning_id UUID, ordningskode TEXT, navn TEXT,
               soknadsfrist TIMESTAMPTZ, funntype TEXT,
               over_grense INT, detalj TEXT, sum_ore BIGINT,
               kravversjon INT, forst_sett TIMESTAMPTZ,
               sist_sett_sveip TIMESTAMPTZ, apen BOOLEAN,
               lukket_ts TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm51_funnene');
    RETURN QUERY
    SELECT f.ordning_id, o.ordningskode, o.navn, o.soknadsfrist,
           f.funntype, f.over_grense, f.detalj, f.sum_ore,
           f.kravversjon, f.forst_sett, f.sist_sett_sveip, f.apen,
           f.lukket_ts
      FROM public.tilskuddsfunn f
      JOIN public.stotteordning o
        ON o.tenant = f.tenant AND o.ordning_id = f.ordning_id
     WHERE f.tenant = p_tenant
       AND (NOT coalesce(p_bare_apne, true) OR f.apen)
     ORDER BY f.apen DESC, o.soknadsfrist ASC;
END $$;
REVOKE ALL ON FUNCTION m51_funnene(TEXT, BOOLEAN) FROM PUBLIC;


-- ------------------------------------------------------------
-- 5. Sveipen.
-- ------------------------------------------------------------

-- TENANTLISTA MATERIALISERES FØR LØKKA (112s lærdom, 116–118).
--
-- SVEIPEN REGNER INGEN ESTIMATER. Den ser hver ordning, hver
-- kildepost og hvert tak — og en «hjelpsom» automatikk som fylte inn
-- poster for å nå taket ville vært et estimat ingen har tatt stilling
-- til. Et estimat uten poster blir et FUNN.
CREATE FUNCTION m51_sveip_tilskudd(p_maks_tenanter INT)
RETURNS TABLE (tenanter INT, nye BIGINT, oppdaterte BIGINT,
               lukkede BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_tenanter TEXT[];
    v_t TEXT;
    v_antall INT := 0;
    v_nye BIGINT := 0;
    v_oppdaterte BIGINT := 0;
    v_lukket BIGINT := 0;
    v_n BIGINT;
    v_n2 BIGINT;
BEGIN
    IF p_maks_tenanter IS NULL OR p_maks_tenanter < 1 THEN
        RAISE EXCEPTION 'm51_sveip_tilskudd: maks_tenanter må være'
            ' minst 1 (%)', p_maks_tenanter
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.tenant', '', true);
    SELECT array_agg(DISTINCT o.tenant ORDER BY o.tenant)
      INTO v_tenanter FROM public.stotteordning o;
    IF v_tenanter IS NULL THEN
        RETURN QUERY SELECT 0, 0::bigint, 0::bigint, 0::bigint;
        RETURN;
    END IF;
    IF cardinality(v_tenanter) > p_maks_tenanter THEN
        v_tenanter := v_tenanter[1:p_maks_tenanter];
    END IF;

    FOREACH v_t IN ARRAY v_tenanter
    LOOP
        v_antall := v_antall + 1;
        PERFORM set_config('disponit.tenant', v_t, true);

        WITH krav AS (
            SELECT k.frist_varsel_dogn, k.kildepost_gyldig_dogn,
                   k.versjon
              FROM public.tilskuddskrav k WHERE k.tenant = v_t),
        siste AS (
            SELECT o.ordning_id, o.soknadsfrist, o.maks_belop_ore,
                   e.estimat_id, e.klar_til_gjennomgang AS klar,
                   coalesce(s.sum_ore, 0) AS sum_ore,
                   coalesce(s.poster, 0) AS poster
              FROM public.stotteordning o
              LEFT JOIN LATERAL (
                   SELECT ee.estimat_id, ee.klar_til_gjennomgang
                     FROM public.tilskuddsestimat ee
                    WHERE ee.tenant = o.tenant
                      AND ee.ordning_id = o.ordning_id
                    ORDER BY ee.versjon DESC
                    LIMIT 1) e ON true
              LEFT JOIN LATERAL (
                   SELECT coalesce(sum(pp.andel_ore), 0)::bigint AS sum_ore,
                          count(*) AS poster
                     FROM public.estimatpost pp
                    WHERE pp.tenant = o.tenant
                      AND pp.estimat_id = e.estimat_id) s ON true
             WHERE o.tenant = v_t AND o.aktiv),
        utdatert AS (
            -- POSTER SOM PEKER PÅ EN KILDEPOST SOM ER BLITT FOR
            -- GAMMEL. Kilden var fersk da posten ble lagt til;
            -- vinduet har passert siden. Estimatet hviler da på et
            -- tall som ikke lenger er grunnlag.
            SELECT s.ordning_id, min(kp.ekstern_ref) AS ref
              FROM siste s
              JOIN public.estimatpost p
                ON p.tenant = v_t AND p.estimat_id = s.estimat_id
              JOIN public.kildepost kp
                ON kp.tenant = v_t AND kp.kildepost_id = p.kildepost_id
              CROSS JOIN krav k
             WHERE kp.registrert
                   < now() - make_interval(days => k.kildepost_gyldig_dogn)
             GROUP BY s.ordning_id),
        kand AS (
            SELECT s.ordning_id, 'ingen_krav'::text AS funntype,
                   NULL::int AS over_grense, NULL::text AS detalj,
                   NULL::bigint AS sum_ore, NULL::int AS kravversjon
              FROM siste s WHERE NOT EXISTS (SELECT 1 FROM krav)

            UNION ALL
            SELECT s.ordning_id, 'frist_naermer_seg',
                   (s.soknadsfrist::date - current_date),
                   to_char(s.soknadsfrist, 'YYYY-MM-DD'), s.sum_ore,
                   k.versjon
              FROM siste s CROSS JOIN krav k
             WHERE s.soknadsfrist >= now()
               AND s.soknadsfrist <= now()
                   + make_interval(days => k.frist_varsel_dogn)
               AND NOT coalesce(s.klar, false)

            UNION ALL
            SELECT s.ordning_id, 'frist_passert',
                   (s.soknadsfrist::date - current_date),
                   to_char(s.soknadsfrist, 'YYYY-MM-DD'), s.sum_ore,
                   k.versjon
              FROM siste s CROSS JOIN krav k
             WHERE s.soknadsfrist < now()
               AND NOT coalesce(s.klar, false)

            UNION ALL
            SELECT s.ordning_id, 'ingen_estimat', NULL::int,
                   NULL::text, NULL::bigint, k.versjon
              FROM siste s CROSS JOIN krav k
             WHERE s.estimat_id IS NULL

            UNION ALL
            SELECT s.ordning_id, 'estimat_uten_poster', NULL::int,
                   NULL::text, 0::bigint, k.versjon
              FROM siste s CROSS JOIN krav k
             WHERE s.estimat_id IS NOT NULL AND s.poster = 0

            UNION ALL
            -- OVER ORDNINGENS TAK. `sum_ore` STÅR PÅ FUNNET: «over
            -- taket» uten å si hvor mye er en beskjed man ikke kan
            -- handle på.
            SELECT s.ordning_id, 'estimat_over_ordningstak',
                   NULL::int, NULL::text, s.sum_ore, k.versjon
              FROM siste s CROSS JOIN krav k
             WHERE s.maks_belop_ore IS NOT NULL
               AND s.sum_ore > s.maks_belop_ore

            UNION ALL
            SELECT u.ordning_id, 'utdatert_kildepost', NULL::int,
                   u.ref, NULL::bigint, k.versjon
              FROM utdatert u CROSS JOIN krav k
        ),
        skrevet AS (
            INSERT INTO public.tilskuddsfunn
                (tenant, ordning_id, funntype, over_grense, detalj,
                 sum_ore, kravversjon)
            SELECT v_t, k.ordning_id, k.funntype, k.over_grense,
                   k.detalj, k.sum_ore, k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, ordning_id, funntype) DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                detalj = EXCLUDED.detalj,
                sum_ore = EXCLUDED.sum_ore,
                kravversjon = EXCLUDED.kravversjon,
                sist_sett_sveip = now(),
                apen = true,
                lukket_ts = NULL
            RETURNING (xmax = 0) AS var_ny)
        SELECT count(*) FILTER (WHERE var_ny),
               count(*) FILTER (WHERE NOT var_ny)
          INTO v_n, v_n2
          FROM skrevet;
        v_nye := v_nye + coalesce(v_n, 0);
        -- `INTO` SETTER variabelen; akkumuleringen står her (112s
        -- retting, gjentatt i 116–118).
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);

        PERFORM set_config('disponit.tenant', '', true);
    END LOOP;

    -- LUKKINGEN I EGEN RUNDE (117/118s form).
    FOREACH v_t IN ARRAY v_tenanter
    LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        WITH krav AS (
            SELECT k.frist_varsel_dogn, k.kildepost_gyldig_dogn
              FROM public.tilskuddskrav k WHERE k.tenant = v_t),
        siste AS (
            SELECT o.ordning_id, o.soknadsfrist, o.maks_belop_ore,
                   e.estimat_id, e.klar_til_gjennomgang AS klar,
                   coalesce(s.sum_ore, 0) AS sum_ore,
                   coalesce(s.poster, 0) AS poster
              FROM public.stotteordning o
              LEFT JOIN LATERAL (
                   SELECT ee.estimat_id, ee.klar_til_gjennomgang
                     FROM public.tilskuddsestimat ee
                    WHERE ee.tenant = o.tenant
                      AND ee.ordning_id = o.ordning_id
                    ORDER BY ee.versjon DESC
                    LIMIT 1) e ON true
              LEFT JOIN LATERAL (
                   SELECT coalesce(sum(pp.andel_ore), 0)::bigint AS sum_ore,
                          count(*) AS poster
                     FROM public.estimatpost pp
                    WHERE pp.tenant = o.tenant
                      AND pp.estimat_id = e.estimat_id) s ON true
             WHERE o.tenant = v_t AND o.aktiv),
        utdatert AS (
            SELECT DISTINCT s.ordning_id
              FROM siste s
              JOIN public.estimatpost p
                ON p.tenant = v_t AND p.estimat_id = s.estimat_id
              JOIN public.kildepost kp
                ON kp.tenant = v_t AND kp.kildepost_id = p.kildepost_id
              CROSS JOIN krav k
             WHERE kp.registrert
                   < now() - make_interval(days => k.kildepost_gyldig_dogn)),
        kand AS (
            SELECT s.ordning_id, 'ingen_krav'::text AS funntype
              FROM siste s WHERE NOT EXISTS (SELECT 1 FROM krav)
            UNION ALL
            SELECT s.ordning_id, 'frist_naermer_seg'
              FROM siste s CROSS JOIN krav k
             WHERE s.soknadsfrist >= now()
               AND s.soknadsfrist <= now()
                   + make_interval(days => k.frist_varsel_dogn)
               AND NOT coalesce(s.klar, false)
            UNION ALL
            SELECT s.ordning_id, 'frist_passert'
              FROM siste s CROSS JOIN krav k
             WHERE s.soknadsfrist < now()
               AND NOT coalesce(s.klar, false)
            UNION ALL
            SELECT s.ordning_id, 'ingen_estimat'
              FROM siste s CROSS JOIN krav k
             WHERE s.estimat_id IS NULL
            UNION ALL
            SELECT s.ordning_id, 'estimat_uten_poster'
              FROM siste s CROSS JOIN krav k
             WHERE s.estimat_id IS NOT NULL AND s.poster = 0
            UNION ALL
            SELECT s.ordning_id, 'estimat_over_ordningstak'
              FROM siste s CROSS JOIN krav k
             WHERE s.maks_belop_ore IS NOT NULL
               AND s.sum_ore > s.maks_belop_ore
            UNION ALL
            SELECT u.ordning_id, 'utdatert_kildepost' FROM utdatert u
        ),
        lukket AS (
            UPDATE public.tilskuddsfunn f
               SET apen = false, lukket_ts = now()
             WHERE f.tenant = v_t AND f.apen
               AND NOT EXISTS (
                   SELECT 1 FROM kand k
                    WHERE k.ordning_id = f.ordning_id
                      AND k.funntype = f.funntype)
            RETURNING 1)
        SELECT count(*) INTO v_n FROM lukket;
        v_lukket := v_lukket + coalesce(v_n, 0);
        PERFORM set_config('disponit.tenant', '', true);
    END LOOP;

    RETURN QUERY SELECT v_antall, v_nye, v_oppdaterte, v_lukket;
END $$;
REVOKE ALL ON FUNCTION m51_sveip_tilskudd(INT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 6. RLS. ENABLE + FORCE på alle sju.
-- ------------------------------------------------------------

RESET ROLE;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['tilskuddskrav', 'stotteordning',
                             'kildepost', 'tilskuddsestimat',
                             'estimatpost', 'estimatforutsetning',
                             'tilskuddsfunn']
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
                       ' disponit_tilskudd_eier', t);
    END LOOP;
END $$;

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER (111s form, 112–118):
-- bare på ORDNINGSTABELLEN, bare FOR SELECT, bare til eieren, og bare
-- når ingen tenantkontekst står.
CREATE POLICY m51_sveip_tenantliste ON stotteordning
    FOR SELECT TO disponit_tilskudd_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- HISTORIKKTABELLENE FÅR IKKE UPDATE, HELLER IKKE FOR EIEREN.
--
-- `tilskuddsestimat` ER IKKE MED: `m51_ferdigstill_estimat` må kunne
-- sette klarmerket. Åpningen lukkes fra den andre siden av radvakten
-- under.
REVOKE UPDATE ON public.stotteordning FROM disponit_tilskudd_eier;
REVOKE UPDATE ON public.kildepost FROM disponit_tilskudd_eier;
REVOKE UPDATE ON public.estimatpost FROM disponit_tilskudd_eier;
REVOKE UPDATE ON public.estimatforutsetning
    FROM disponit_tilskudd_eier;

-- …men ordningens `aktiv` må kunne settes. Egen, snever GRANT på
-- KOLONNEN: eieren kan endre aktivflagget og ingenting annet, så
-- regelverksversjonen og fristen er frosset uten at en radvakt må
-- gjette hva som er lov.
GRANT UPDATE (aktiv) ON public.stotteordning
    TO disponit_tilskudd_eier;

CREATE FUNCTION m51_estimat_frosset()
RETURNS TRIGGER LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF OLD.klar_til_gjennomgang THEN
        RAISE EXCEPTION 'tilskuddsestimat: estimat % er merket klart'
            ' og er frosset — en ny post hører til et nytt estimat',
            OLD.estimat_id USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.estimat_id IS DISTINCT FROM OLD.estimat_id
       OR NEW.ordning_id IS DISTINCT FROM OLD.ordning_id
       OR NEW.versjon IS DISTINCT FROM OLD.versjon
       OR NEW.periode_fra IS DISTINCT FROM OLD.periode_fra
       OR NEW.periode_til IS DISTINCT FROM OLD.periode_til
       OR NEW.usikkerhet_prosent
          IS DISTINCT FROM OLD.usikkerhet_prosent
       OR NEW.kravversjon IS DISTINCT FROM OLD.kravversjon
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av THEN
        RAISE EXCEPTION 'tilskuddsestimat: estimatets egne felter er'
            ' frosset — bare klarmerket kan settes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER tilskuddsestimat_frosset
    BEFORE UPDATE ON tilskuddsestimat
    FOR EACH ROW EXECUTE FUNCTION m51_estimat_frosset();

-- SLETTING ER ALDRI LOVLIG.
REVOKE DELETE ON public.tilskuddskrav FROM disponit_tilskudd_eier;
REVOKE DELETE ON public.stotteordning FROM disponit_tilskudd_eier;
REVOKE DELETE ON public.kildepost FROM disponit_tilskudd_eier;
REVOKE DELETE ON public.tilskuddsestimat FROM disponit_tilskudd_eier;
REVOKE DELETE ON public.estimatpost FROM disponit_tilskudd_eier;
REVOKE DELETE ON public.estimatforutsetning
    FROM disponit_tilskudd_eier;
REVOKE DELETE ON public.tilskuddsfunn FROM disponit_tilskudd_eier;


-- ------------------------------------------------------------
-- 7. Rettighetene. SP-7: kjøretidsrollen får INGEN tabellrettigheter.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_tilskudd_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m51_tilskuddsstatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m51_kravene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m51_ordningene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m51_estimatene(TEXT, UUID) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m51_postene(TEXT, UUID)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m51_forutsetningene(TEXT, UUID) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m51_kildepostene(TEXT, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m51_funnene(TEXT, BOOLEAN) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m51_sett_krav(TEXT, INT, INT, INT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m51_registrer_ordning(TEXT, UUID, TEXT, TEXT, TEXT,'
            ' TEXT, TEXT, BIGINT, INT, TIMESTAMPTZ, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m51_registrer_kildepost(TEXT, UUID, TEXT, TEXT, TEXT,'
            ' BIGINT, DATE, DATE, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m51_opprett_estimat(TEXT, UUID, UUID, DATE, DATE, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m51_legg_til_post(TEXT, UUID, UUID, UUID, BIGINT, TEXT,'
            ' TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m51_legg_til_forutsetning(TEXT, UUID, UUID, TEXT, TEXT,'
            ' TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m51_ferdigstill_estimat(TEXT, UUID, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m51_sett_ordningaktiv(TEXT, UUID, BOOLEAN, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m51_lukk_funn(TEXT, UUID, TEXT, TEXT, TEXT)'
            ' TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_tilskuddssveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m51_sveip_tilskudd(INT)'
            ' TO disponit_tilskuddssveip';
    END IF;
END $$;

-- SVEIPEN ER IKKE KJØRETIDSROLLENS. Vaktet, som i 117/118: `REVOKE
-- ... FROM <rolle som ikke finnes>` er en FEIL i PostgreSQL, ikke en
-- no-op, og GRANT-blokken over behandler `disponit` som valgfri.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'REVOKE EXECUTE ON FUNCTION m51_sveip_tilskudd(INT)'
            ' FROM disponit';
    END IF;
END $$;

RESET ROLE;
