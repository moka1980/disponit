-- =====================================================================
-- M-47 MYNDIGHETSRAPPORTERINGSAGENTEN (v1) — FRISTEN ER PRODUKTET.
-- =====================================================================
--
-- V1-DOMMEN: MODULEN SENDER INGEN INNSENDING.
--
-- En innsending til en myndighet er BINDENDE og kan ikke kalles
-- tilbake. Feil tall i en pålagt rapport er ikke en feil man retter —
-- det er en korrigert innsending med sin egen historikk, og i noen
-- tilfeller et avvik myndigheten ser. Derfor finnes det ingen
-- mottaker her, ingen utboks, ingen signatur og ingen «sendt»-kolonne.
--
-- MEN HER ER FRAVÆRET IKKE NOK, OG DET SKILLER M-47 FRA KLYNGE 6.
--
-- For de fem i klynge 6 var skaden å HANDLE: å sende inn et tilbud, å
-- sette en kredittgrense, å avgi en tollkode. Avholdenhet var hele
-- svaret, og en modul som ikke gjorde noe kunne ikke gjøre skade.
--
-- HER ER SKADEN OGSÅ Å LA VÆRE. En frist som går uten innsending er
-- nøyaktig det modulen ble bygget for å hindre. En modul som legger et
-- utkast klart og lar fristen passere i stillhet, har forårsaket
-- skaden den skulle avverge — og den har gjort det verre enn om den
-- ikke fantes, fordi noen stolte på at den så etter.
--
--   EN STILLE M-47 ER VERRE ENN INGEN M-47.
--
-- Det er ikke en talemåte. Det er grunnen til at TO av funnene her
-- ikke kan lukkes av et menneske, og at sveipens egen feiltelling er
-- en invariant og ikke en bekvemmelighet.
--
-- DEN FEILEN ER MÅLT I DETTE HUSET, IKKE TENKT UT: plattformens
-- auto-utrulling til staging feilet hver eneste natt fra 4. september,
-- i fem kjøringer, på samme manglende DSN. Den returnerte feilkode.
-- Ingen så det. Serveren sto med kode fra flere moduler tilbake mens
-- arbeidet gikk videre, og det ble oppdaget først da eier spurte om
-- noe helt annet. Det er `sveipefeil_uten_stoy`, i vår egen drift.
--
-- KLYNGENS DELTE DOM (docs/KLYNGE7-FUNDAMENT.md): regelen er ikke vår,
-- den endres, og den endres uten å si fra. Innsendingsfrister flyttes,
-- skjemaer får nye versjoner, hjemler erstattes. EN FORELDET REGEL SER
-- NØYAKTIG UT SOM EN RIKTIG REGEL — derfor bærer hver plikt hjemmelen
-- og regelversjonen sin, snapshotet, og sveipen ser etter plikter som
-- hviler på et regelverk som siden er avviklet.
--
-- GRENSEN MOT M-34: M-34 eier SERTIFISERING — etterlevelse av
-- standarder man VELGER. M-47 eier PÅLAGTE innsendinger med frist. Den
-- ene kan man la være; den andre kan man ikke.
--
-- GRENSEN MOT M-21: M-21 eier avtalefrister, altså våre egne
-- kontrakter. M-47s frister er lovpålagte, og forskjellen er hvem som
-- sanksjonerer.
-- =====================================================================

-- ROLLEN MÅ FINNES FØR VI BYGGER. En migrasjon som skaper objekter
-- uten en eier å gi dem til, gir dem til migratoren — og da har
-- kjøretidsrollen arvet fullmakter ingen bestemte.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_myndighet_eier') THEN
        RAISE EXCEPTION 'rollen disponit_myndighet_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_myndighet_eier;
GRANT INSERT ON revisjonslogg TO disponit_myndighet_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_myndighet_eier;
RESET ROLE;

-- TABELLENE EIES AV MIGRATOREN, FUNKSJONENE AV MODULROLLEN (122s form).
--
-- Det er ikke en detalj: RLS-en slås på av en `ALTER TABLE`, og bare
-- eieren kan gjøre det. Lager modulrollen tabellene, må RLS-blokken
-- kjøre SOM den rollen — og da har den også fullmakt til å ta radvakten
-- AV igjen. Migratoren eier strukturen; modulrollen eier dørene.

-- ---------------------------------------------------------------------
-- TENANTENS EGNE FRISTER.
--
-- VARSELFRISTEN ER TENANTENS, IKKE VÅR. En bedrift med regnskapsfører
-- og fjorten dagers internfrist trenger et annet varsel enn en som
-- gjør det selv kvelden før. En konstant her ville vært en fullmakt
-- modulen ga seg selv over kundens forsinkelsesgebyr.
-- ---------------------------------------------------------------------
CREATE TABLE myndighetskrav (
    tenant TEXT PRIMARY KEY CHECK (length(btrim(tenant)) > 0),
    -- Hvor mange døgn før fristen varselet skal reises.
    varselfrist_dogn INT NOT NULL DEFAULT 14
        CHECK (varselfrist_dogn BETWEEN 1 AND 365),
    -- Når et varsel som ikke er fulgt opp blir en eskalering.
    eskaleringsfrist_dogn INT NOT NULL DEFAULT 3
        CHECK (eskaleringsfrist_dogn BETWEEN 1 AND 90),
    -- Hvor lenge før utløp et regelverk varsles.
    regelvarsel_dogn INT NOT NULL DEFAULT 60
        CHECK (regelvarsel_dogn BETWEEN 1 AND 730),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon > 0),
    -- IDEMPOTENSNØKKELEN LEVER PÅ RADEN (M-51s lærdom, 119). Uten den
    -- kan døra ikke skille en replay fra en endring, og hvert funn
    -- bærer `kravversjon`: en versjon som økte uten at en terskel
    -- endret seg gjør funnhistorikken uleselig.
    siste_nokkel TEXT,
    satt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    satt_av TEXT NOT NULL CHECK (length(btrim(satt_av)) > 0)
);

-- ---------------------------------------------------------------------
-- REGELVERKET — MYNDIGHETENS, IKKE VÅRT.
--
-- Identiteten er FROSSET. Bare `gyldig_til` kan settes senere, fordi
-- en myndighet som kunngjør at et skjema avvikles er nettopp den
-- endringen modulen skal følge med på (M-54s dom, 121).
-- ---------------------------------------------------------------------
CREATE TABLE regelverk (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    regelverk_id UUID NOT NULL,
    PRIMARY KEY (tenant, regelverk_id),
    -- Hvem som eier regelen. Ikke et fritekstfelt: den som leser skal
    -- kunne se myndigheten uten å tolke en streng.
    myndighet TEXT NOT NULL CHECK (myndighet IN (
        'skatteetaten', 'altinn', 'brreg', 'ssb', 'nav',
        'arbeidstilsynet', 'annen')),
    -- Myndighetens eget navn på regelverket, og versjonen.
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    versjon TEXT NOT NULL CHECK (versjon ~ '[^[:space:]]'),
    -- HJEMMELEN. En plikt uten hjemmel er en påstand om at noen må
    -- gjøre noe, uten å si hvem som har bestemt det.
    hjemmel TEXT NOT NULL CHECK (hjemmel ~ '[^[:space:]]'),
    gyldig_fra DATE NOT NULL,
    gyldig_til DATE,
    CONSTRAINT regelverk_vindu CHECK (
        gyldig_til IS NULL OR gyldig_til >= gyldig_fra),
    innhold_sha256 TEXT NOT NULL CHECK (innhold_sha256 ~ '^[0-9a-f]{64}$'),
    kilde_url TEXT,
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (length(btrim(registrert_av)) > 0),
    CONSTRAINT regelverk_unik UNIQUE (tenant, myndighet, navn, versjon)
);

CREATE INDEX regelverk_gyldig_idx ON regelverk (tenant, gyldig_til);

-- ---------------------------------------------------------------------
-- PLIKTTYPEN — hva slags innsending det er.
--
-- Typen bærer HJEMMELEN og hvor ofte plikten inntreffer. Den er frosset
-- etter registrering: en rapportplikttype som kunne endres i ettertid ville
-- gjort «hvilken hjemmel gjaldt da» til et spørsmål uten svar.
-- ---------------------------------------------------------------------
CREATE TABLE rapportplikttype (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    rapportplikttype_id UUID NOT NULL,
    PRIMARY KEY (tenant, rapportplikttype_id),
    nokkel TEXT NOT NULL CHECK (nokkel ~ '^[a-z][a-z0-9_]{2,63}$'),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    -- FREKVENSEN ER EN OPPLYSNING, IKKE EN BEREGNING. Modulen regner
    -- ikke ut neste frist av den: fristene er myndighetens, de flyttes,
    -- og en utregnet frist ville sett like sikker ut som en avlest.
    frekvens TEXT NOT NULL CHECK (frekvens IN (
        'maanedlig', 'to_maanedlig', 'kvartalsvis', 'halvaarlig',
        'aarlig', 'ved_hendelse')),
    beskrivelse TEXT CHECK (beskrivelse IS NULL
        OR beskrivelse ~ '[^[:space:]]'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (length(btrim(registrert_av)) > 0),
    CONSTRAINT plikttype_unik UNIQUE (tenant, nokkel)
);

-- ---------------------------------------------------------------------
-- PLIKTEN — én konkret innsending med én konkret frist.
--
-- HELE RADEN ER FROSSET. Perioden, fristen, hjemmelen og
-- regelversjonen er det som skal kunne leses år senere: «hva var vi
-- pålagt, av hvem, med hvilken frist, etter hvilken regel».
--
-- SNAPSHOTET STÅR VED SIDEN AV FREMMEDNØKKELEN, ikke i stedet for den.
-- Nøkkelen binder til raden; snapshotet binder til TEKSTEN, og det er
-- snapshotet som svarer uten et oppslag den dagen regelverket er
-- endret under oss.
-- ---------------------------------------------------------------------
-- NAVNET ER `rapportplikt`, IKKE `plikt`: M-21 (096) eier `plikt`, og
-- det er ikke bare en kollisjon — det er GRENSEN mellom modulene. M-21s
-- plikter er avtalefrister, altså våre egne kontrakter. M-47s er
-- lovpålagte innsendinger. Forskjellen er hvem som sanksjonerer, og to
-- tabeller med samme navn ville skjult nettopp den forskjellen.
CREATE TABLE rapportplikt (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    rapportplikt_id UUID NOT NULL,
    PRIMARY KEY (tenant, rapportplikt_id),
    rapportplikttype_id UUID NOT NULL,
    regelverk_id UUID NOT NULL,
    CONSTRAINT rapportplikt_type_fk
        FOREIGN KEY (tenant, rapportplikttype_id)
        REFERENCES rapportplikttype (tenant, rapportplikttype_id),
    CONSTRAINT rapportplikt_regelverk_fk FOREIGN KEY (tenant, regelverk_id)
        REFERENCES regelverk (tenant, regelverk_id),
    -- SNAPSHOTET: hva regelen HET da plikten ble registrert.
    myndighet_ved_registrering TEXT NOT NULL,
    regelnavn_ved_registrering TEXT NOT NULL,
    regelversjon_ved_registrering TEXT NOT NULL,
    hjemmel_ved_registrering TEXT NOT NULL
        CHECK (hjemmel_ved_registrering ~ '[^[:space:]]'),
    -- PERIODEN plikten gjelder for, og FRISTEN.
    periode_fra DATE NOT NULL,
    periode_til DATE NOT NULL,
    CONSTRAINT rapportplikt_periode CHECK (periode_til >= periode_fra),
    frist DATE NOT NULL,
    CONSTRAINT rapportplikt_frist_etter_periode CHECK (frist >= periode_til),
    kravversjon INT NOT NULL CHECK (kravversjon > 0),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (length(btrim(registrert_av)) > 0),
    -- SAMME PLIKT FOR SAMME PERIODE ER ÉN PLIKT. To rader ville gitt to
    -- frister for det samme, og den ene av dem ville vært usann.
    CONSTRAINT rapportplikt_unik UNIQUE (
        tenant, rapportplikttype_id, periode_fra, periode_til)
);
CREATE INDEX rapportplikt_frist_idx ON rapportplikt (tenant, frist);
CREATE INDEX rapportplikt_regelverk_idx ON rapportplikt (tenant, regelverk_id);

-- ---------------------------------------------------------------------
-- BEVISET — AT ET MENNESKE SENDTE INN.
--
-- MODULEN SENDER IKKE. Dette er ikke en «sendt»-kolonne med et annet
-- navn: raden registreres AV et menneske, ETTER at mennesket har sendt
-- inn et annet sted, og den bærer kvitteringsreferansen myndigheten ga
-- DEM. Vi har ingen kanal til myndigheten og påstår ikke å ha det.
--
-- Skillet er hele modulens dom, og det er derfor kolonnen heter
-- `innsendt_av_person` og ikke `innsendt`: den som leser skal ikke
-- kunne tro at systemet gjorde det.
-- ---------------------------------------------------------------------
CREATE TABLE rapportbevis (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    bevis_id UUID NOT NULL,
    PRIMARY KEY (tenant, bevis_id),
    rapportplikt_id UUID NOT NULL,
    CONSTRAINT rapportbevis_plikt_fk
        FOREIGN KEY (tenant, rapportplikt_id)
        REFERENCES rapportplikt (tenant, rapportplikt_id),
    innsendt_dato DATE NOT NULL,
    -- KVITTERINGEN ER MYNDIGHETENS. Uten den er beviset en påstand;
    -- med den er det noe man kan slå opp hos den som mottok.
    kvittering_ref TEXT NOT NULL CHECK (kvittering_ref ~ '[^[:space:]]'),
    innsendt_av_person TEXT NOT NULL
        CHECK (length(btrim(innsendt_av_person)) > 0),
    notat TEXT CHECK (notat IS NULL OR notat ~ '[^[:space:]]'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (length(btrim(registrert_av)) > 0),
    -- ÉN PLIKT, ETT BEVIS. En korrigert innsending er en NY plikt med
    -- sin egen frist — ikke et nytt bevis på den gamle.
    CONSTRAINT rapportbevis_unik UNIQUE (tenant, rapportplikt_id)
);

-- ---------------------------------------------------------------------
-- FUNNENE — NATTENS MÅLING.
--
-- TO AV DEM KAN IKKE LUKKES AV ET MENNESKE, og det er ikke symmetri
-- med klynge 6. Det er modulens dom:
--
--   `plikt_mot_utlopt_regelverk` er KLYNGENS funn. Plikten ble
--   registrert mot et regelverk som var gyldig da, og som siden er
--   avviklet. Den ser velformet ut. Den forsvinner når plikten
--   registreres på nytt mot gjeldende regelverk — en HANDLING.
--
--   `frist_passert_uten_bevis` er MODULENS EGET, og det skarpeste her.
--   En frist som har gått uten at noen har sendt inn er ikke en mening
--   man kan være uenig i. Å lukke den for hånd ville vært å skru av
--   det ene varselet som sier at noe faktisk har gått galt — og
--   forsinkelsesgebyret kommer uansett. Den lukkes av at et BEVIS
--   registreres, altså av at noen faktisk sendte inn.
--
-- `frist_naermer_seg` KAN lukkes: «jeg har sett den, jeg gjør den på
-- fredag» er en legitim menneskelig beslutning om noe som ennå ikke
-- har gått galt. Det er skillet mellom en påminnelse og et avvik.
-- ---------------------------------------------------------------------
CREATE TABLE myndighetsfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    funn_id UUID NOT NULL,
    PRIMARY KEY (tenant, funn_id),
    funntype TEXT NOT NULL
        CONSTRAINT myndighetsfunn_type CHECK (funntype IN (
            'ingen_krav',
            'regelverk_utlopt',
            'regelverk_utloper_snart',
            'plikt_mot_utlopt_regelverk',
            'frist_naermer_seg',
            'frist_passert_uten_bevis')),
    regelverk_id UUID,
    rapportplikt_id UUID,
    CONSTRAINT myndighetsfunn_nivaa CHECK (
        CASE funntype
          WHEN 'ingen_krav' THEN
            regelverk_id IS NOT NULL OR rapportplikt_id IS NOT NULL
          WHEN 'regelverk_utlopt' THEN regelverk_id IS NOT NULL
          WHEN 'regelverk_utloper_snart' THEN regelverk_id IS NOT NULL
          ELSE rapportplikt_id IS NOT NULL
        END),
    CONSTRAINT myndighetsfunn_en_noekkel CHECK (
        num_nonnulls(regelverk_id, rapportplikt_id) = 1),
    -- DØGN. Positivt tall for «over grensen», og fortegnet bæres av
    -- funntypen: `frist_naermer_seg` teller ned, `frist_passert` teller
    -- opp. Tallet er poenget — en frist som gikk i går og en som gikk
    -- for et halvår siden krever ikke samme hastverk.
    over_grense INT,
    detalj TEXT CHECK (detalj IS NULL OR detalj ~ '[^[:space:]]'),
    kravversjon INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT,
    lukkenotat TEXT,
    CONSTRAINT myndighetsfunn_lukking CHECK (
        (apen AND lukket_ts IS NULL AND lukket_av IS NULL
             AND lukkenotat IS NULL)
        OR (NOT apen AND lukket_ts IS NOT NULL))
);
-- ÉN RAD PER TILSTAND, ikke én per natt. Sveipen er idempotent, og
-- delvise unike indekser er formen som gjør den det (117–122).
CREATE UNIQUE INDEX myndighetsfunn_regelverk_unik
    ON myndighetsfunn (tenant, regelverk_id, funntype)
    WHERE regelverk_id IS NOT NULL;
CREATE UNIQUE INDEX myndighetsfunn_plikt_unik
    ON myndighetsfunn (tenant, rapportplikt_id, funntype)
    WHERE rapportplikt_id IS NOT NULL;
CREATE INDEX myndighetsfunn_apne_idx
    ON myndighetsfunn (tenant, apen, funntype);

-- ---------------------------------------------------------------------
-- FUNNENE INGEN KAN LUKKE, SOM EN TABELL OG IKKE EN HUSKEREGEL.
--
-- Lista står her, én gang, og både døra og porten leser den. En liste
-- som fantes to steder ville før eller siden vært to lister.
-- ---------------------------------------------------------------------
CREATE FUNCTION m47_funn_er_sveipens(p_funntype TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT p_funntype IN ('plikt_mot_utlopt_regelverk',
                          'frist_passert_uten_bevis')
$$;

-- Gjelder regelverket i dag? Står som funksjon fordi BÅDE døra og
-- sveipen spør, og to kopier av samme vindusregel er to regler.
CREATE FUNCTION m47_regelverk_gyldig(p_fra DATE, p_til DATE)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT p_fra <= current_date
       AND (p_til IS NULL OR p_til >= current_date)
$$;

-- HERFRA EIES DØRENE AV MYNDIGHETSEIEREN.
SET LOCAL ROLE disponit_myndighet_eier;

CREATE FUNCTION m47_evidens(p_tenant TEXT, p_plikt_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm47_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm47_myndighetsrapport',
        'handling', p_handling,
        'rapportplikt_id', p_plikt_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm47_myndighetsrapport',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:myndighetsrapport', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m47_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- =====================================================================
-- DØRENE.
-- =====================================================================

-- TENANTENS FRISTER. Idempotensnøkkelen ligger INNE i døra (M-51s
-- lærdom, 119): en gjentatt POST skal ikke bumpe versjonen, for hvert
-- funn bærer `kravversjon`, og en versjon som hoppet av en replay
-- ville gjort funnhistorikken uleselig.
CREATE FUNCTION m47_sett_krav(p_tenant TEXT, p_varselfrist INT,
                              p_eskalering INT, p_regelvarsel INT,
                              p_aktor TEXT, p_nokkel TEXT)
RETURNS TABLE (varselfrist_dogn INT, eskaleringsfrist_dogn INT,
               regelvarsel_dogn INT, versjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_ny INT;
    v_sett INT;
    v_nokkel TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm47_sett_krav');
    IF p_nokkel IS NULL OR btrim(p_nokkel) = '' THEN
        RAISE EXCEPTION 'm47_sett_krav: idempotensnøkkel mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- LÅS FØRST, LES ETTERPÅ (klynge 6s lærdom, fem ganger). En
    -- lesning før låsen er fra transaksjonens snapshot, og et
    -- samtidig kall som committer mens vi venter er usynlig der.
    PERFORM 1 FROM public.myndighetskrav
     WHERE tenant = p_tenant FOR UPDATE;

    -- NØKKELEN LESES UNDER LÅSEN, OG DEN BETYR NOE (CodeRabbit).
    --
    -- M-51 (119) hadde nøyaktig denne feilen: nøkkelen ble tatt imot og
    -- kastet, så en REPLAYET POST bumpet `versjon`. Her er det verre
    -- enn en teller som hopper — HVERT FUNN BÆRER `kravversjon`, og en
    -- versjon som økte uten at en terskel endret seg gjør
    -- funnhistorikken uleselig: «hvilken terskel gjaldt da dette
    -- funnet ble reist» får to svar.
    --
    -- EN REPLAY ER IKKE EN ENDRING. Samme nøkkel → samme rad tilbake,
    -- urørt.
    SELECT k.versjon, k.siste_nokkel INTO v_sett, v_nokkel
      FROM public.myndighetskrav k WHERE k.tenant = p_tenant;
    IF v_nokkel IS NOT NULL AND v_nokkel = p_nokkel THEN
        RETURN QUERY SELECT k.varselfrist_dogn,
                            k.eskaleringsfrist_dogn,
                            k.regelvarsel_dogn, k.versjon
                       FROM public.myndighetskrav k
                      WHERE k.tenant = p_tenant;
        RETURN;
    END IF;

    INSERT INTO public.myndighetskrav
        (tenant, varselfrist_dogn, eskaleringsfrist_dogn,
         regelvarsel_dogn, versjon, satt_av, siste_nokkel)
    VALUES (p_tenant, p_varselfrist, p_eskalering, p_regelvarsel,
            1, p_aktor, p_nokkel)
    ON CONFLICT (tenant) DO UPDATE SET
        varselfrist_dogn = EXCLUDED.varselfrist_dogn,
        eskaleringsfrist_dogn = EXCLUDED.eskaleringsfrist_dogn,
        regelvarsel_dogn = EXCLUDED.regelvarsel_dogn,
        versjon = public.myndighetskrav.versjon + 1,
        satt_ts = now(), satt_av = EXCLUDED.satt_av,
        siste_nokkel = EXCLUDED.siste_nokkel
    RETURNING public.myndighetskrav.versjon INTO v_ny;

    PERFORM public.m47_evidens(p_tenant, NULL, 'krav_satt', p_aktor,
        jsonb_build_object('varselfrist_dogn', p_varselfrist,
                           'eskaleringsfrist_dogn', p_eskalering,
                           'regelvarsel_dogn', p_regelvarsel,
                           'versjon', v_ny, 'nokkel', p_nokkel));

    RETURN QUERY SELECT k.varselfrist_dogn, k.eskaleringsfrist_dogn,
                        k.regelvarsel_dogn, k.versjon
                   FROM public.myndighetskrav k
                  WHERE k.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m47_sett_krav(TEXT, INT, INT, INT, TEXT, TEXT)
    FROM PUBLIC;

-- REGELVERKET REGISTRERES — OGSÅ ET SOM ALT ER AVVIKLET.
--
-- 121s LÆRDOM, OG DEN VAR MIN EGEN FEIL DER: første utkast nektet å
-- registrere et utløpt regelsett. Det var galt. Modulen finnes for å
-- svare på «hva sa regelen DA», og en plikt fra 2019 må kunne forstås
-- mot hjemmelen som gjaldt i 2019. REGISTRERING ER ARKIVERING.
--
-- Skillet ligger i DOMMEN, ikke i arkivet: `m47_registrer_plikt`
-- nekter mot et regelverk som ikke gjelder i dag.
CREATE FUNCTION m47_registrer_regelverk(
    p_tenant TEXT, p_regelverk_id UUID, p_myndighet TEXT,
    p_navn TEXT, p_versjon TEXT, p_hjemmel TEXT, p_gyldig_fra DATE,
    p_gyldig_til DATE, p_sha TEXT, p_url TEXT, p_aktor TEXT)
RETURNS TABLE (regelverk_id UUID, gyldig_naa BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
        'm47_registrer_regelverk');
    INSERT INTO public.regelverk
        (tenant, regelverk_id, myndighet, navn, versjon, hjemmel,
         gyldig_fra, gyldig_til, innhold_sha256, kilde_url,
         registrert_av)
    VALUES (p_tenant, p_regelverk_id, p_myndighet, btrim(p_navn),
            btrim(p_versjon), btrim(p_hjemmel), p_gyldig_fra,
            p_gyldig_til, lower(btrim(p_sha)), p_url, p_aktor);

    PERFORM public.m47_evidens(p_tenant, NULL, 'regelverk_registrert',
        p_aktor, jsonb_build_object(
            'regelverk_id', p_regelverk_id::text,
            'myndighet', p_myndighet, 'navn', p_navn,
            'versjon', p_versjon, 'hjemmel', p_hjemmel));

    RETURN QUERY
    SELECT r.regelverk_id,
           public.m47_regelverk_gyldig(r.gyldig_fra, r.gyldig_til)
      FROM public.regelverk r
     WHERE r.tenant = p_tenant AND r.regelverk_id = p_regelverk_id;
END $$;
REVOKE ALL ON FUNCTION m47_registrer_regelverk(
    TEXT, UUID, TEXT, TEXT, TEXT, TEXT, DATE, DATE, TEXT, TEXT, TEXT)
    FROM PUBLIC;

-- AVVIKLINGSDATOEN KAN SETTES — og BARE den.
--
-- Identiteten er frosset av en kolonnegrant lenger nede. Grunnen står
-- her: en myndighet som kunngjør at et skjema avvikles er nettopp den
-- endringen modulen skal følge med på. Å fryse ALT ville gjort modulen
-- blind for det ene den er bygget for å se.
CREATE FUNCTION m47_sett_gyldig_til(p_tenant TEXT, p_regelverk_id UUID,
                                    p_gyldig_til DATE, p_aktor TEXT)
RETURNS TABLE (regelverk_id UUID, gyldig_til DATE, gyldig_naa BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_fra DATE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
        'm47_sett_gyldig_til');
    SELECT r.gyldig_fra INTO v_fra FROM public.regelverk r
     WHERE r.tenant = p_tenant AND r.regelverk_id = p_regelverk_id
       FOR UPDATE;
    IF v_fra IS NULL THEN
        RAISE EXCEPTION 'm47_sett_gyldig_til: ukjent regelverk %',
            p_regelverk_id USING ERRCODE = 'no_data_found';
    END IF;
    IF p_gyldig_til IS NOT NULL AND p_gyldig_til < v_fra THEN
        RAISE EXCEPTION 'm47_sett_gyldig_til: % er før regelverkets'
            ' startdato %', p_gyldig_til, v_fra
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.regelverk r SET gyldig_til = p_gyldig_til
     WHERE r.tenant = p_tenant AND r.regelverk_id = p_regelverk_id;

    PERFORM public.m47_evidens(p_tenant, NULL, 'regelverk_avvikles',
        p_aktor, jsonb_build_object(
            'regelverk_id', p_regelverk_id::text,
            'gyldig_til', p_gyldig_til));

    RETURN QUERY
    SELECT r.regelverk_id, r.gyldig_til,
           public.m47_regelverk_gyldig(r.gyldig_fra, r.gyldig_til)
      FROM public.regelverk r
     WHERE r.tenant = p_tenant AND r.regelverk_id = p_regelverk_id;
END $$;
REVOKE ALL ON FUNCTION m47_sett_gyldig_til(TEXT, UUID, DATE, TEXT)
    FROM PUBLIC;

CREATE FUNCTION m47_registrer_plikttype(
    p_tenant TEXT, p_plikttype_id UUID, p_nokkel TEXT, p_navn TEXT,
    p_frekvens TEXT, p_beskrivelse TEXT, p_aktor TEXT)
RETURNS TABLE (rapportplikttype_id UUID, nokkel TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
        'm47_registrer_plikttype');
    INSERT INTO public.rapportplikttype
        (tenant, rapportplikttype_id, nokkel, navn, frekvens, beskrivelse,
         registrert_av)
    VALUES (p_tenant, p_plikttype_id, lower(btrim(p_nokkel)),
            btrim(p_navn), p_frekvens,
            nullif(btrim(coalesce(p_beskrivelse, '')), ''), p_aktor);

    PERFORM public.m47_evidens(p_tenant, NULL, 'plikttype_registrert',
        p_aktor, jsonb_build_object(
            'rapportplikttype_id', p_plikttype_id::text, 'nokkel', p_nokkel,
            'frekvens', p_frekvens));

    RETURN QUERY SELECT t.rapportplikttype_id, t.nokkel
                   FROM public.rapportplikttype t
                  WHERE t.tenant = p_tenant
                    AND t.rapportplikttype_id = p_plikttype_id;
END $$;
REVOKE ALL ON FUNCTION m47_registrer_plikttype(
    TEXT, UUID, TEXT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- PLIKTEN REGISTRERES — MODULENS SKARPESTE DØR.
--
-- TRE NEKT, og hvert av dem finnes fordi det motsatte ville sett
-- riktig ut:
--
--   1. INGEN PLIKT UTEN KRAV. Uten tenantens varselfrist finnes det
--      ingen frist å varsle på, og plikten ville ligget i registeret
--      og sett overvåket ut mens ingenting så etter den. Det er
--      `frist_uten_varsel`, og det er hele skaden modulen finnes for.
--
--   2. INGEN PLIKT MOT ET AVVIKLET REGELVERK. Regelverket kan
--      REGISTRERES avviklet (arkivet), men en NY plikt mot det ville
--      vært en plikt bygget på en hjemmel som ikke gjelder — velformet
--      og gal, som er klyngens hele problem.
--
--   3. HJEMMELEN OG VERSJONEN SNAPSHOTES. Ikke som en bekvemmelighet:
--      fremmednøkkelen peker på en rad som kan få `gyldig_til` satt i
--      morgen, og snapshotet er det som svarer «hva sto det DA» uten
--      et oppslag.
-- ---------------------------------------------------------------------
CREATE FUNCTION m47_registrer_plikt(
    p_tenant TEXT, p_plikt_id UUID, p_plikttype_id UUID,
    p_regelverk_id UUID, p_periode_fra DATE, p_periode_til DATE,
    p_frist DATE, p_aktor TEXT)
RETURNS TABLE (rapportplikt_id UUID, frist DATE, dogn_til_frist INT,
               myndighet TEXT, regelnavn TEXT, regelversjon TEXT,
               hjemmel TEXT, kravversjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_kravversjon INT;
    v_myndighet TEXT;
    v_navn TEXT;
    v_ver TEXT;
    v_hjemmel TEXT;
    v_fra DATE;
    v_til DATE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
        'm47_registrer_plikt');

    -- 1: INGEN PLIKT UTEN KRAV.
    SELECT k.versjon INTO v_kravversjon FROM public.myndighetskrav k
     WHERE k.tenant = p_tenant;
    IF v_kravversjon IS NULL THEN
        RAISE EXCEPTION 'm47_registrer_plikt: tenanten har ingen'
            ' varselfrist. En plikt uten frist å varsle på ligger i'
            ' registeret og SER overvåket ut mens ingenting ser etter'
            ' den — og en frist ingen har sett er hele skaden'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT r.myndighet, r.navn, r.versjon, r.hjemmel, r.gyldig_fra,
           r.gyldig_til
      INTO v_myndighet, v_navn, v_ver, v_hjemmel, v_fra, v_til
      FROM public.regelverk r
     WHERE r.tenant = p_tenant AND r.regelverk_id = p_regelverk_id;
    IF v_myndighet IS NULL THEN
        RAISE EXCEPTION 'm47_registrer_plikt: ukjent regelverk %',
            p_regelverk_id USING ERRCODE = 'no_data_found';
    END IF;

    -- 2: INGEN NY PLIKT MOT ET AVVIKLET REGELVERK.
    IF NOT public.m47_regelverk_gyldig(v_fra, v_til) THEN
        RAISE EXCEPTION 'm47_registrer_plikt: regelverket % % gjelder'
            ' ikke i dag (% – %). Arkivet tar imot det; en NY plikt'
            ' mot det ville hvilt på en hjemmel som ikke gjelder',
            v_navn, v_ver, v_fra, coalesce(v_til::text, 'åpen')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- 3: SNAPSHOTET SKRIVES SAMMEN MED RADEN.
    INSERT INTO public.rapportplikt
        (tenant, rapportplikt_id, rapportplikttype_id, regelverk_id,
         myndighet_ved_registrering, regelnavn_ved_registrering,
         regelversjon_ved_registrering, hjemmel_ved_registrering,
         periode_fra, periode_til, frist, kravversjon, registrert_av)
    VALUES (p_tenant, p_plikt_id, p_plikttype_id, p_regelverk_id,
            v_myndighet, v_navn, v_ver, v_hjemmel,
            p_periode_fra, p_periode_til, p_frist, v_kravversjon,
            p_aktor);

    PERFORM public.m47_evidens(p_tenant, p_plikt_id,
        'plikt_registrert', p_aktor, jsonb_build_object(
            'rapportplikttype_id', p_plikttype_id::text,
            'regelverk_id', p_regelverk_id::text,
            'hjemmel', v_hjemmel, 'regelversjon', v_ver,
            'frist', p_frist, 'kravversjon', v_kravversjon));

    RETURN QUERY
    SELECT p.rapportplikt_id, p.frist, (p.frist - current_date)::int,
           p.myndighet_ved_registrering,
           p.regelnavn_ved_registrering,
           p.regelversjon_ved_registrering,
           p.hjemmel_ved_registrering, p.kravversjon
      FROM public.rapportplikt p
     WHERE p.tenant = p_tenant AND p.rapportplikt_id = p_plikt_id;
END $$;
REVOKE ALL ON FUNCTION m47_registrer_plikt(
    TEXT, UUID, UUID, UUID, DATE, DATE, DATE, TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- BEVISET — AT ET MENNESKE SENDTE INN.
--
-- DETTE ER IKKE «SEND»-DØRA MED ET ANNET NAVN. Den registrerer at noen
-- HAR sendt inn, et annet sted, og bærer kvitteringsreferansen
-- myndigheten ga DEM. Vi har ingen kanal til myndigheten.
--
-- DØRA NEKTER EN FRAMTIDSDATO. Et bevis datert i morgen er ikke et
-- bevis — det er en plan, og en plan lukker ikke et fristfunn.
-- ---------------------------------------------------------------------
CREATE FUNCTION m47_registrer_bevis(
    p_tenant TEXT, p_bevis_id UUID, p_plikt_id UUID,
    p_innsendt_dato DATE, p_kvittering TEXT, p_person TEXT,
    p_notat TEXT, p_aktor TEXT)
RETURNS TABLE (bevis_id UUID, rapportplikt_id UUID, innsendt_dato DATE,
               frist DATE, dogn_etter_frist INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_frist DATE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
        'm47_registrer_bevis');

    SELECT p.frist INTO v_frist FROM public.rapportplikt p
     WHERE p.tenant = p_tenant AND p.rapportplikt_id = p_plikt_id;
    IF v_frist IS NULL THEN
        RAISE EXCEPTION 'm47_registrer_bevis: ukjent plikt %',
            p_plikt_id USING ERRCODE = 'no_data_found';
    END IF;

    IF p_innsendt_dato > current_date THEN
        RAISE EXCEPTION 'm47_registrer_bevis: % er i framtiden. Et'
            ' bevis datert i morgen er ikke et bevis — det er en plan,'
            ' og en plan lukker ikke et fristfunn',
            p_innsendt_dato USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO public.rapportbevis
        (tenant, bevis_id, rapportplikt_id, innsendt_dato, kvittering_ref,
         innsendt_av_person, notat, registrert_av)
    VALUES (p_tenant, p_bevis_id, p_plikt_id, p_innsendt_dato,
            btrim(p_kvittering), btrim(p_person),
            nullif(btrim(coalesce(p_notat, '')), ''), p_aktor);

    -- FORSINKELSEN STÅR I EVIDENSKJEDEN. Et bevis registrert etter
    -- fristen er fortsatt et bevis — men at det kom for sent er en
    -- opplysning noen skal kunne finne igjen.
    PERFORM public.m47_evidens(p_tenant, p_plikt_id,
        'bevis_registrert', p_aktor, jsonb_build_object(
            'bevis_id', p_bevis_id::text,
            'innsendt_dato', p_innsendt_dato, 'frist', v_frist,
            'dogn_etter_frist', (p_innsendt_dato - v_frist),
            'kvittering_ref', btrim(p_kvittering),
            'innsendt_av_person', btrim(p_person)));

    RETURN QUERY
    SELECT b.bevis_id, b.rapportplikt_id, b.innsendt_dato, v_frist,
           (b.innsendt_dato - v_frist)::int
      FROM public.rapportbevis b
     WHERE b.tenant = p_tenant AND b.bevis_id = p_bevis_id;
END $$;
REVOKE ALL ON FUNCTION m47_registrer_bevis(
    TEXT, UUID, UUID, DATE, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- Å LUKKE ET FUNN — OG DE TO SOM IKKE KAN LUKKES.
--
-- `frist_naermer_seg` KAN lukkes: «jeg har sett den, jeg gjør den på
-- fredag» er en legitim beslutning om noe som ennå ikke har gått galt.
--
-- `plikt_mot_utlopt_regelverk` og `frist_passert_uten_bevis` KAN IKKE.
-- Den første forsvinner når plikten registreres på nytt mot gjeldende
-- regelverk. Den andre forsvinner når et BEVIS registreres. Begge er
-- HANDLINGER — og et menneske som kunne klikket dem bort ville skrudd
-- av det ene varselet som sier at noe faktisk har gått galt.
-- Forsinkelsesgebyret kommer uansett.
-- ---------------------------------------------------------------------
CREATE FUNCTION m47_lukk_funn(p_tenant TEXT, p_funn_id UUID,
                              p_notat TEXT, p_aktor TEXT)
RETURNS TABLE (funn_id UUID, apen BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_type TEXT;
    v_plikt UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm47_lukk_funn');
    IF p_notat IS NULL OR length(btrim(p_notat)) < 4 THEN
        RAISE EXCEPTION 'm47_lukk_funn: lukkingen krever et notat'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT f.funntype, f.rapportplikt_id INTO v_type, v_plikt
      FROM public.myndighetsfunn f
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id
       FOR UPDATE;
    IF v_type IS NULL THEN
        RAISE EXCEPTION 'm47_lukk_funn: ukjent funn %', p_funn_id
            USING ERRCODE = 'no_data_found';
    END IF;

    IF public.m47_funn_er_sveipens(v_type) THEN
        RAISE EXCEPTION 'm47_lukk_funn: % kan ikke lukkes for hånd.'
            ' Det forsvinner når tilstanden er borte — plikten'
            ' registrert på nytt mot gjeldende regelverk, eller et'
            ' bevis på at noen faktisk sendte inn. Det er en handling,'
            ' ikke en mening', v_type
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.myndighetsfunn f
       SET apen = false, lukket_ts = now(), lukket_av = p_aktor,
           lukkenotat = btrim(p_notat)
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;

    PERFORM public.m47_evidens(p_tenant, v_plikt, 'funn_lukket',
        p_aktor, jsonb_build_object('funn_id', p_funn_id::text,
                                    'funntype', v_type,
                                    'notat', btrim(p_notat)));

    RETURN QUERY SELECT f.funn_id, f.apen FROM public.myndighetsfunn f
                  WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;
END $$;
REVOKE ALL ON FUNCTION m47_lukk_funn(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

-- =====================================================================
-- SVEIPEN — MODULENS PRODUKT.
--
-- For de andre modulene er sveipen et andre gjerde. HER ER DEN
-- PRODUKTET. En plikt som ligger i registeret uten at noen ser på den
-- er ikke overvåket; den er arkivert. Det er sveipen som gjør den til
-- en frist noen vet om.
--
-- DERFOR ER TENANTLISTA BEGGE REGISTRENE (122s CodeRabbit-funn, lært
-- der og anvendt her uten å måtte finne det på nytt): en tenant som
-- har plikter men ingen regelverk, eller omvendt, skal ikke hoppes
-- over. Han er nettopp den som har konfigurert halvveis.
-- =====================================================================
CREATE FUNCTION m47_sveip_myndighetsplikt(p_maks_tenanter INT)
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
        RAISE EXCEPTION 'm47_sveip_myndighetsplikt: maks_tenanter må'
            ' være minst 1 (%)', p_maks_tenanter
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.tenant', '', true);

    -- MATERIALISERT FØR LØKKA (klynge 6s lærdom om den late markøren):
    -- `set_config` inne i løkka ville ellers endret radvakten under
    -- markøren som fortsatt leser fra den.
    SELECT array_agg(DISTINCT t ORDER BY t) INTO v_tenanter
      FROM (SELECT r.tenant AS t FROM public.regelverk r
            UNION
            SELECT p.tenant FROM public.rapportplikt p) s;
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

        -- REGELVERKSNIVÅET.
        WITH krav AS (
            SELECT k.regelvarsel_dogn, k.versjon
              FROM public.myndighetskrav k WHERE k.tenant = v_t),
        kand AS (
            -- INGEN CROSS JOIN krav (121s funn): funnet handler om at
            -- kravet MANGLER, så en krysskobling mot en tom `krav`
            -- ville gjort det usynlig for nettopp den tenanten det
            -- gjelder.
            SELECT r.regelverk_id, 'ingen_krav'::text AS funntype,
                   NULL::int AS over_grense,
                   'varselfristen er tenantens og er ikke satt'::text
                       AS detalj,
                   NULL::int AS kravversjon
              FROM public.regelverk r
             WHERE r.tenant = v_t AND NOT EXISTS (SELECT 1 FROM krav)

            UNION ALL
            -- ET AVVIKLET REGELVERK UTEN GYLDIG ETTERFØLGER. Ikke
            -- ETHVERT avviklet: et arkivert 2019-regelverk ved siden
            -- av et gyldig 2026 er historikk, og et funn på det ville
            -- vært støy hver natt for alltid (121s lærdom).
            SELECT r.regelverk_id, 'regelverk_utlopt',
                   (current_date - r.gyldig_til),
                   r.navn || ' ' || r.versjon, NULL
              FROM public.regelverk r
             WHERE r.tenant = v_t AND r.gyldig_til IS NOT NULL
               AND r.gyldig_til < current_date
               AND NOT EXISTS (
                   SELECT 1 FROM public.regelverk r2
                    WHERE r2.tenant = v_t
                      AND r2.myndighet = r.myndighet
                      AND r2.navn = r.navn
                      AND public.m47_regelverk_gyldig(r2.gyldig_fra,
                                                      r2.gyldig_til))

            UNION ALL
            SELECT r.regelverk_id, 'regelverk_utloper_snart',
                   (r.gyldig_til - current_date),
                   r.navn || ' ' || r.versjon, k.versjon
              FROM public.regelverk r CROSS JOIN krav k
             WHERE r.tenant = v_t AND r.gyldig_til IS NOT NULL
               AND r.gyldig_til >= current_date
               AND r.gyldig_til <= current_date
                   + make_interval(days => k.regelvarsel_dogn)
        ),
        skrevet AS (
            INSERT INTO public.myndighetsfunn
                (tenant, funn_id, regelverk_id, funntype, over_grense,
                 detalj, kravversjon)
            SELECT v_t, gen_random_uuid(), k.regelverk_id, k.funntype,
                   k.over_grense, k.detalj, k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, regelverk_id, funntype)
                WHERE regelverk_id IS NOT NULL
            DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                detalj = EXCLUDED.detalj,
                kravversjon = EXCLUDED.kravversjon,
                sist_sett_sveip = now(), apen = true,
                lukket_ts = NULL, lukket_av = NULL, lukkenotat = NULL
            RETURNING (xmax = 0) AS var_ny)
        SELECT count(*) FILTER (WHERE var_ny),
               count(*) FILTER (WHERE NOT var_ny)
          INTO v_n, v_n2
          FROM skrevet;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);

        -- PLIKTNIVÅET. KLYNGENS FUNN OG MODULENS EGET.
        WITH krav AS (
            SELECT k.varselfrist_dogn, k.versjon
              FROM public.myndighetskrav k WHERE k.tenant = v_t),
        ubevist AS (
            -- PLIKTER INGEN HAR SENDT INN ENNÅ. Bevist plikt er
            -- ferdig plikt; den skal ikke måles mot noen frist mer.
            SELECT p.rapportplikt_id, p.frist, p.regelverk_id,
                   p.regelnavn_ved_registrering AS navn,
                   p.regelversjon_ved_registrering AS ver
              FROM public.rapportplikt p
             WHERE p.tenant = v_t
               AND NOT EXISTS (SELECT 1 FROM public.rapportbevis b
                                WHERE b.tenant = v_t
                                  AND b.rapportplikt_id = p.rapportplikt_id)),
        kand AS (
            -- KLYNGENS FUNN: plikten hviler på et regelverk som siden
            -- er avviklet. Den ser velformet ut, og det er poenget.
            -- INGEN ETTERFØLGER-UNNTAK HER, og det er forskjellen
            -- fra `regelverk_utlopt` over.
            --
            -- På REGELVERKSNIVÅET er unntaket riktig: et arkivert
            -- 2019-sett ved siden av et gyldig 2026-sett er historikk,
            -- og et funn på det ville vært støy hver natt for alltid.
            --
            -- PÅ PLIKTNIVÅET ER DET GALT, og det er en feil jeg selv
            -- skrev og porten fant: med unntaket forsvant funnet i det
            -- øyeblikket noen registrerte en NY regelversjon — mens
            -- den gamle plikten fortsatt hvilte på den avviklede
            -- hjemmelen. Funnet ville altså blitt lukket av at
            -- problemet ble STØRRE, ikke løst.
            --
            -- Tilstanden er «en ubevist plikt hviler på et regelverk
            -- som ikke gjelder». Den er borte når plikten er bevist,
            -- eller når regelverket ikke lenger er avviklet. Ikke før.
            SELECT u.rapportplikt_id,
                   'plikt_mot_utlopt_regelverk'::text AS funntype,
                   (current_date - r.gyldig_til) AS over_grense,
                   (u.navn || ' ' || u.ver)::text AS detalj,
                   (SELECT versjon FROM krav) AS kravversjon
              FROM ubevist u
              JOIN public.regelverk r
                ON r.tenant = v_t AND r.regelverk_id = u.regelverk_id
             WHERE r.gyldig_til IS NOT NULL
               AND r.gyldig_til < current_date


            UNION ALL
            -- MODULENS EGET FUNN, OG DET SKARPESTE: fristen har gått,
            -- og ingen har sendt inn. Ikke en påminnelse — et avvik.
            SELECT u.rapportplikt_id, 'frist_passert_uten_bevis',
                   (current_date - u.frist),
                   (u.navn || ' ' || u.ver)::text,
                   (SELECT versjon FROM krav)
              FROM ubevist u
             WHERE u.frist < current_date

            UNION ALL
            -- PÅMINNELSEN. Denne KAN lukkes: den handler om noe som
            -- ennå ikke har gått galt.
            SELECT u.rapportplikt_id, 'frist_naermer_seg',
                   (u.frist - current_date),
                   (u.navn || ' ' || u.ver)::text, k.versjon
              FROM ubevist u CROSS JOIN krav k
             WHERE u.frist >= current_date
               AND u.frist <= current_date
                   + make_interval(days => k.varselfrist_dogn)

            UNION ALL
            -- OG PLIKTEN UTEN KRAV I DET HELE TATT. Uten dette ville
            -- en tenant med plikter og ingen varselfrist fått
            -- INGENTING — han har ingen regelverksrad å henge
            -- `ingen_krav` på hvis han ikke har registrert noe der
            -- (122s lærdom, som gikk dypere enn CodeRabbit meldte).
            SELECT u.rapportplikt_id, 'ingen_krav', NULL,
                   'varselfristen er tenantens og er ikke satt'::text,
                   NULL
              FROM ubevist u
             WHERE NOT EXISTS (SELECT 1 FROM krav)
               AND NOT EXISTS (SELECT 1 FROM public.regelverk r
                                WHERE r.tenant = v_t)
        ),
        skrevet AS (
            INSERT INTO public.myndighetsfunn
                (tenant, funn_id, rapportplikt_id, funntype, over_grense,
                 detalj, kravversjon)
            SELECT v_t, gen_random_uuid(), k.rapportplikt_id, k.funntype,
                   k.over_grense, k.detalj, k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, rapportplikt_id, funntype)
                WHERE rapportplikt_id IS NOT NULL
            DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                detalj = EXCLUDED.detalj,
                kravversjon = EXCLUDED.kravversjon,
                sist_sett_sveip = now(), apen = true,
                lukket_ts = NULL, lukket_av = NULL, lukkenotat = NULL
            RETURNING (xmax = 0) AS var_ny)
        SELECT count(*) FILTER (WHERE var_ny),
               count(*) FILTER (WHERE NOT var_ny)
          INTO v_n, v_n2
          FROM skrevet;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
    END LOOP;

    -- LUKKINGEN I EGEN RUNDE (117–123s form). Et funn som ikke lenger
    -- er sant skal ikke bli stående — men det lukkes av at TILSTANDEN
    -- er borte, ikke av at noen trykket.
    --
    -- DE TO SVEIPENS EGNE LUKKES OGSÅ HER, og bare her:
    -- `frist_passert_uten_bevis` når et bevis er registrert, og
    -- `plikt_mot_utlopt_regelverk` når plikten er registrert på nytt
    -- mot et gyldig regelverk. Døra nekter fremdeles begge.
    FOREACH v_t IN ARRAY v_tenanter
    LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        WITH krav AS (
            SELECT k.varselfrist_dogn, k.regelvarsel_dogn
              FROM public.myndighetskrav k WHERE k.tenant = v_t),
        ubevist AS (
            SELECT p.rapportplikt_id, p.frist, p.regelverk_id
              FROM public.rapportplikt p
             WHERE p.tenant = v_t
               AND NOT EXISTS (SELECT 1 FROM public.rapportbevis b
                                WHERE b.tenant = v_t
                                  AND b.rapportplikt_id = p.rapportplikt_id)),
        fortsatt AS (
            SELECT r.regelverk_id, NULL::uuid AS rapportplikt_id,
                   'ingen_krav'::text AS funntype
              FROM public.regelverk r
             WHERE r.tenant = v_t AND NOT EXISTS (SELECT 1 FROM krav)
            UNION ALL
            SELECT r.regelverk_id, NULL, 'regelverk_utlopt'
              FROM public.regelverk r
             WHERE r.tenant = v_t AND r.gyldig_til IS NOT NULL
               AND r.gyldig_til < current_date
               AND NOT EXISTS (
                   SELECT 1 FROM public.regelverk r2
                    WHERE r2.tenant = v_t
                      AND r2.myndighet = r.myndighet
                      AND r2.navn = r.navn
                      AND public.m47_regelverk_gyldig(r2.gyldig_fra,
                                                      r2.gyldig_til))
            UNION ALL
            SELECT r.regelverk_id, NULL, 'regelverk_utloper_snart'
              FROM public.regelverk r CROSS JOIN krav k
             WHERE r.tenant = v_t AND r.gyldig_til IS NOT NULL
               AND r.gyldig_til >= current_date
               AND r.gyldig_til <= current_date
                   + make_interval(days => k.regelvarsel_dogn)
            UNION ALL
            -- SPEILER `kand` over: ingen etterfølger-unntak på
            -- pliktnivået. Sto det her, ville lukkingen fjernet
            -- nøyaktig det kandidatlista nettopp sluttet å slippe unna.
            SELECT NULL, u.rapportplikt_id, 'plikt_mot_utlopt_regelverk'
              FROM ubevist u
              JOIN public.regelverk r
                ON r.tenant = v_t AND r.regelverk_id = u.regelverk_id
             WHERE r.gyldig_til IS NOT NULL
               AND r.gyldig_til < current_date

            UNION ALL
            SELECT NULL, u.rapportplikt_id, 'frist_passert_uten_bevis'
              FROM ubevist u WHERE u.frist < current_date
            UNION ALL
            SELECT NULL, u.rapportplikt_id, 'frist_naermer_seg'
              FROM ubevist u CROSS JOIN krav k
             WHERE u.frist >= current_date
               AND u.frist <= current_date
                   + make_interval(days => k.varselfrist_dogn)
            UNION ALL
            SELECT NULL, u.rapportplikt_id, 'ingen_krav'
              FROM ubevist u
             WHERE NOT EXISTS (SELECT 1 FROM krav)
               AND NOT EXISTS (SELECT 1 FROM public.regelverk r
                                WHERE r.tenant = v_t)
        ),
        lukket AS (
            UPDATE public.myndighetsfunn f
               SET apen = false, lukket_ts = now(),
                   lukket_av = 'm47_sveip',
                   lukkenotat = 'tilstanden er borte'
             WHERE f.tenant = v_t AND f.apen
               AND NOT EXISTS (
                   SELECT 1 FROM fortsatt s
                    WHERE s.funntype = f.funntype
                      AND s.regelverk_id IS NOT DISTINCT FROM
                          f.regelverk_id
                      AND s.rapportplikt_id
                          IS NOT DISTINCT FROM f.rapportplikt_id)
            RETURNING 1)
        SELECT count(*) INTO v_n FROM lukket;
        v_lukket := v_lukket + coalesce(v_n, 0);
    END LOOP;

    PERFORM set_config('disponit.tenant', '', true);
    RETURN QUERY SELECT v_antall, v_nye, v_oppdaterte, v_lukket;
END $$;
REVOKE ALL ON FUNCTION m47_sveip_myndighetsplikt(INT) FROM PUBLIC;

-- =====================================================================
-- LESEDØRENE.
-- =====================================================================

-- BILDET. Sammendraget står med `frist_passert` FØRST og alene, fordi
-- det er det ene tallet modulen finnes for: plikter som har gått uten
-- at noen sendte inn.
CREATE FUNCTION m47_bildet(p_tenant TEXT, p_maks INT)
RETURNS TABLE (plikter BIGINT, beviste BIGINT, ubeviste BIGINT,
               frist_passert BIGINT, frist_naer BIGINT,
               regelverk BIGINT, gyldige BIGINT, utlopte BIGINT,
               apne_funn BIGINT, har_krav BOOLEAN,
               -- ALLE TRE TERSKLENE, ikke bare varselfristen
               -- (CodeRabbit). Skjemaet som setter dem forhåndsfyller
               -- seg fra dette svaret: med bare den ene sto de to
               -- andre feltene TOMME, og en tenant som lagret skjemaet
               -- ville sendt 0 inn i felt med minimum 1. Et skjema som
               -- viser mindre enn det lagrer er en felle.
               varselfrist_dogn INT, eskaleringsfrist_dogn INT,
               regelvarsel_dogn INT, kravversjon INT, vist BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm47_bildet');
    RETURN QUERY
    WITH ubevist AS (
        SELECT p.rapportplikt_id, p.frist FROM public.rapportplikt p
         WHERE p.tenant = p_tenant
           AND NOT EXISTS (SELECT 1 FROM public.rapportbevis b
                            WHERE b.tenant = p_tenant
                              AND b.rapportplikt_id = p.rapportplikt_id)),
    k AS (SELECT * FROM public.myndighetskrav
           WHERE tenant = p_tenant)
    SELECT (SELECT count(*) FROM public.rapportplikt p
             WHERE p.tenant = p_tenant),
           (SELECT count(*) FROM public.rapportbevis b
             WHERE b.tenant = p_tenant),
           (SELECT count(*) FROM ubevist),
           (SELECT count(*) FROM ubevist u
             WHERE u.frist < current_date),
           (SELECT count(*) FROM ubevist u CROSS JOIN k
             WHERE u.frist >= current_date
               AND u.frist <= current_date
                   + make_interval(days => k.varselfrist_dogn)),
           (SELECT count(*) FROM public.regelverk r
             WHERE r.tenant = p_tenant),
           (SELECT count(*) FROM public.regelverk r
             WHERE r.tenant = p_tenant
               AND public.m47_regelverk_gyldig(r.gyldig_fra,
                                               r.gyldig_til)),
           (SELECT count(*) FROM public.regelverk r
             WHERE r.tenant = p_tenant AND r.gyldig_til IS NOT NULL
               AND r.gyldig_til < current_date),
           (SELECT count(*) FROM public.myndighetsfunn f
             WHERE f.tenant = p_tenant AND f.apen),
           (SELECT count(*) > 0 FROM k),
           (SELECT k.varselfrist_dogn FROM k),
           (SELECT k.eskaleringsfrist_dogn FROM k),
           (SELECT k.regelvarsel_dogn FROM k),
           (SELECT k.versjon FROM k),
           least((SELECT count(*) FROM public.rapportplikt p
                   WHERE p.tenant = p_tenant), p_maks);
END $$;
REVOKE ALL ON FUNCTION m47_bildet(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m47_regelverkene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (regelverk_id UUID, myndighet TEXT, navn TEXT,
               versjon TEXT, hjemmel TEXT, gyldig_fra DATE,
               gyldig_til DATE, gyldig_naa BOOLEAN,
               dogn_til_utlop INT, innhold_sha256 TEXT,
               kilde_url TEXT, antall_plikter BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm47_regelverkene');
    RETURN QUERY
    SELECT r.regelverk_id, r.myndighet, r.navn, r.versjon, r.hjemmel,
           r.gyldig_fra, r.gyldig_til,
           public.m47_regelverk_gyldig(r.gyldig_fra, r.gyldig_til),
           CASE WHEN r.gyldig_til IS NULL THEN NULL
                ELSE (r.gyldig_til - current_date)::int END,
           r.innhold_sha256, r.kilde_url,
           (SELECT count(*) FROM public.rapportplikt p
             WHERE p.tenant = p_tenant
               AND p.regelverk_id = r.regelverk_id)
      FROM public.regelverk r
     WHERE r.tenant = p_tenant
     ORDER BY r.myndighet, r.navn, r.gyldig_fra DESC
     LIMIT p_maks;
END $$;
REVOKE ALL ON FUNCTION m47_regelverkene(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m47_plikttypene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (rapportplikttype_id UUID, nokkel TEXT, navn TEXT,
               frekvens TEXT, beskrivelse TEXT, antall_plikter BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm47_plikttypene');
    RETURN QUERY
    SELECT t.rapportplikttype_id, t.nokkel, t.navn, t.frekvens,
           t.beskrivelse,
           (SELECT count(*) FROM public.rapportplikt p
             WHERE p.tenant = p_tenant
               AND p.rapportplikttype_id = t.rapportplikttype_id)
      FROM public.rapportplikttype t
     WHERE t.tenant = p_tenant
     ORDER BY t.navn
     LIMIT p_maks;
END $$;
REVOKE ALL ON FUNCTION m47_plikttypene(TEXT, INT) FROM PUBLIC;

-- PLIKTENE. HVER RAD BÆRER HJEMMELEN, REGELVERSJONEN OG OM REGELEN
-- GJELDER I DAG — aldri fristen alene. En frist uten hjemmel er en
-- påstand om at noen må gjøre noe, uten å si hvem som har bestemt det.
CREATE FUNCTION m47_pliktene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (rapportplikt_id UUID, rapportplikttype_id UUID, typenavn TEXT,
               typenokkel TEXT, periode_fra DATE, periode_til DATE,
               frist DATE, dogn_til_frist INT, myndighet TEXT,
               regelnavn TEXT, regelversjon TEXT, hjemmel TEXT,
               regelverk_gyldig_naa BOOLEAN, bevis_id UUID,
               innsendt_dato DATE, kvittering_ref TEXT,
               innsendt_av_person TEXT, dogn_etter_frist INT,
               kravversjon INT, registrert TIMESTAMPTZ,
               registrert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm47_pliktene');
    RETURN QUERY
    SELECT p.rapportplikt_id, p.rapportplikttype_id, t.navn, t.nokkel,
           p.periode_fra, p.periode_til, p.frist,
           (p.frist - current_date)::int,
           p.myndighet_ved_registrering,
           p.regelnavn_ved_registrering,
           p.regelversjon_ved_registrering,
           p.hjemmel_ved_registrering,
           public.m47_regelverk_gyldig(r.gyldig_fra, r.gyldig_til),
           b.bevis_id, b.innsendt_dato, b.kvittering_ref,
           b.innsendt_av_person,
           CASE WHEN b.innsendt_dato IS NULL THEN NULL
                ELSE (b.innsendt_dato - p.frist)::int END,
           p.kravversjon, p.registrert, p.registrert_av
      FROM public.rapportplikt p
      JOIN public.rapportplikttype t
        ON t.tenant = p.tenant
       AND t.rapportplikttype_id = p.rapportplikttype_id
      JOIN public.regelverk r
        ON r.tenant = p.tenant AND r.regelverk_id = p.regelverk_id
      LEFT JOIN public.rapportbevis b
        ON b.tenant = p.tenant AND b.rapportplikt_id = p.rapportplikt_id
     WHERE p.tenant = p_tenant
     -- DEN NÆRMESTE FRISTEN FØRST, og de passerte aller først: en
     -- liste sortert på registreringstidspunkt ville begravd avviket.
     ORDER BY (b.bevis_id IS NOT NULL), p.frist, p.rapportplikt_id
     LIMIT p_maks;
END $$;
REVOKE ALL ON FUNCTION m47_pliktene(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m47_funnene(p_tenant TEXT, p_bare_apne BOOLEAN)
RETURNS TABLE (funn_id UUID, funntype TEXT, regelverk_id UUID,
               rapportplikt_id UUID, myndighet TEXT, regelnavn TEXT,
               regelversjon TEXT, typenavn TEXT, frist DATE,
               over_grense INT, detalj TEXT, kravversjon INT,
               kan_lukkes BOOLEAN, forst_sett TIMESTAMPTZ,
               sist_sett_sveip TIMESTAMPTZ, apen BOOLEAN,
               lukket_ts TIMESTAMPTZ, lukket_av TEXT,
               lukkenotat TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm47_funnene');
    RETURN QUERY
    SELECT f.funn_id, f.funntype, f.regelverk_id, f.rapportplikt_id,
           coalesce(r.myndighet, p.myndighet_ved_registrering),
           coalesce(r.navn, p.regelnavn_ved_registrering),
           coalesce(r.versjon, p.regelversjon_ved_registrering),
           t.navn, p.frist, f.over_grense, f.detalj, f.kravversjon,
           -- FLATEN SKAL VITE HVA DEN KAN TILBY. En lukkeknapp som
           -- alltid feiler er verre enn en valgmulighet som ikke
           -- finnes — og regelen bor ÉTT sted (`m47_funn_er_sveipens`),
           -- ikke som en kopi i klienten.
           NOT public.m47_funn_er_sveipens(f.funntype),
           f.forst_sett, f.sist_sett_sveip, f.apen, f.lukket_ts,
           f.lukket_av, f.lukkenotat
      FROM public.myndighetsfunn f
      LEFT JOIN public.regelverk r
        ON r.tenant = f.tenant AND r.regelverk_id = f.regelverk_id
      LEFT JOIN public.rapportplikt p
        ON p.tenant = f.tenant AND p.rapportplikt_id = f.rapportplikt_id
      LEFT JOIN public.rapportplikttype t
        ON t.tenant = p.tenant
       AND t.rapportplikttype_id = p.rapportplikttype_id
     WHERE f.tenant = p_tenant
       AND (NOT p_bare_apne OR f.apen)
     -- DET SOM HAR GÅTT GALT FØRST. `frist_passert_uten_bevis` er
     -- modulens skarpeste funn, og en liste sortert alfabetisk ville
     -- lagt det under «frist_naermer_seg».
     ORDER BY (f.funntype = 'frist_passert_uten_bevis') DESC,
              f.over_grense DESC NULLS LAST, f.forst_sett;
END $$;
REVOKE ALL ON FUNCTION m47_funnene(TEXT, BOOLEAN) FROM PUBLIC;

-- =====================================================================
-- RETTIGHETENE, RADVAKTENE OG FRYSINGEN.
-- =====================================================================

RESET ROLE;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['myndighetskrav', 'regelverk',
                             'rapportplikttype', 'rapportplikt',
                             'rapportbevis', 'myndighetsfunn']
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
                       ' disponit_myndighet_eier', t);
    END LOOP;
END $$;

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER (111s form, 112–122):
-- bare FOR SELECT, bare til eieren, bare når ingen tenantkontekst står
-- — og på BEGGE registrene sveipens tenantliste leser, fordi en tenant
-- kan ha plikter før han har registrert et regelverk (122s lærdom).
CREATE POLICY m47_sveip_tenantliste ON regelverk
    FOR SELECT TO disponit_myndighet_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);
CREATE POLICY m47_sveip_tenantliste_plikt ON rapportplikt
    FOR SELECT TO disponit_myndighet_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- HISTORIKKTABELLENE FÅR IKKE UPDATE, HELLER IKKE FOR EIEREN.
--
-- `rapportplikttype`, `plikt` og `rapportbevis` ER HELT LUKKET. En hjemmel, en
-- frist og en kvittering kan bare OPPSTÅ, aldri endres: det er
-- nettopp «hva var vi pålagt, med hvilken frist, etter hvilken regel»
-- som skal kunne leses år senere.
--
-- Det er ikke en radvakt som kan gjøre en feil — det er en rettighet
-- som ikke finnes.
REVOKE UPDATE ON public.rapportplikttype FROM disponit_myndighet_eier;
REVOKE UPDATE ON public.rapportplikt FROM disponit_myndighet_eier;
REVOKE UPDATE ON public.rapportbevis FROM disponit_myndighet_eier;

-- `regelverk` FÅR BARE ENDRE `gyldig_til` (121s dom, 122s form): en
-- myndighet som kunngjør at et skjema avvikles er nettopp den
-- endringen modulen skal følge med på. Å fryse ALT ville gjort modulen
-- blind for det ene den er bygget for å se.
REVOKE UPDATE ON public.regelverk FROM disponit_myndighet_eier;
GRANT UPDATE (gyldig_til) ON public.regelverk
    TO disponit_myndighet_eier;

-- INGEN AV TABELLENE FÅR SLETTES. Sletting står ikke i noen GRANT over
-- — den listen er `SELECT, INSERT, UPDATE`. Det står her fordi et
-- fravær er lettere å overse enn en setning, og porten leser begge.

-- ---------------------------------------------------------------------
-- RADVAKTEN PÅ REGELVERKET.
--
-- Kolonnegranten hindrer at andre kolonner SKRIVES. Denne hindrer at
-- `gyldig_til` skrives av noen som endrer identiteten i samme setning
-- — belte og seler, fordi en kolonnegrant er en rettighet og en
-- radvakt er en påstand om hva raden ER.
-- ---------------------------------------------------------------------
-- Triggeren settes av MIGRATOREN, som eier tabellen: `CREATE TRIGGER`
-- krever eierskap, og en modulrolle som kunne sette den kunne også ta
-- den av igjen.
CREATE FUNCTION m47_regelverk_frosset()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.regelverk_id IS DISTINCT FROM OLD.regelverk_id
       OR NEW.myndighet IS DISTINCT FROM OLD.myndighet
       OR NEW.navn IS DISTINCT FROM OLD.navn
       OR NEW.versjon IS DISTINCT FROM OLD.versjon
       OR NEW.hjemmel IS DISTINCT FROM OLD.hjemmel
       OR NEW.gyldig_fra IS DISTINCT FROM OLD.gyldig_fra
       OR NEW.innhold_sha256 IS DISTINCT FROM OLD.innhold_sha256
       OR NEW.registrert IS DISTINCT FROM OLD.registrert
       OR NEW.registrert_av IS DISTINCT FROM OLD.registrert_av THEN
        RAISE EXCEPTION 'regelverk: identiteten er frosset — bare'
            ' gyldig_til kan settes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER regelverk_frosset
    BEFORE UPDATE ON regelverk
    FOR EACH ROW EXECUTE FUNCTION m47_regelverk_frosset();

-- =====================================================================
-- EXECUTE — HVEM SOM FÅR ÅPNE HVILKEN DØR.
--
-- KJØRETIDSROLLEN HAR NULL TABELLRETTIGHETER (SP-7). Alt går gjennom
-- dørene, og sveipen er IKKE kjøretidsrollens: en runtime som kunne
-- kjørt sveipen kunne skrevet funn i alle tenanters navn.
-- =====================================================================
SET LOCAL ROLE disponit_myndighet_eier;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m47_bildet(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m47_regelverkene(TEXT, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m47_plikttypene(TEXT, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m47_pliktene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m47_funnene(TEXT, BOOLEAN)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m47_sett_krav(TEXT, INT, INT, INT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m47_registrer_regelverk('
            'TEXT, UUID, TEXT, TEXT, TEXT, TEXT, DATE, DATE, TEXT,'
            ' TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m47_sett_gyldig_til(TEXT, UUID, DATE, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m47_registrer_plikttype('
            'TEXT, UUID, TEXT, TEXT, TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m47_registrer_plikt('
            'TEXT, UUID, UUID, UUID, DATE, DATE, DATE, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m47_registrer_bevis('
            'TEXT, UUID, UUID, DATE, TEXT, TEXT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m47_lukk_funn(TEXT, UUID, TEXT, TEXT) TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_myndighetssveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m47_sveip_myndighetsplikt(INT)'
            ' TO disponit_myndighetssveip';
    END IF;
END $$;

-- SVEIPEN ER IKKE KJØRETIDSROLLENS.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'REVOKE EXECUTE ON FUNCTION'
            ' m47_sveip_myndighetsplikt(INT) FROM disponit';
    END IF;
END $$;

RESET ROLE;
