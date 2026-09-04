-- 122: M-52 toll- og HS-kodeagent v1 — FORSLAGET, IKKE DEKLARASJONEN.
-- Åtte tenant-skopede tabeller, sytten dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- V1-DOMMEN: MODULEN DEKLARERER INGENTING.
--
-- EN HS-KODE ER EN RETTSLIG PÅSTAND OM HVA EN VARE ER. Spesifikasjonen
-- sier det rett ut — «feil HS-kode gir bot, ikke bare forsinkelse» — og
-- boten treffer KUNDEN, ikke oss. En deklarasjon er dessuten bindende:
-- den kan rettes, men rettingen er en egen sak med sin egen historikk,
-- og i noen tilfeller et avvik tollmyndigheten ser.
--
-- DERFOR FORESLÅR v1, OG FORSLAGET ER IKKE EN DEKLARASJON. Det finnes
-- ingen kolonne for «deklarert», ingen mottaker og ingen utboks.
--
-- OG HER ER MODULENS EGEN, SKARPESTE DOM:
--
--   ET FORSLAG UTEN GRUNNLAG ER VERRE ENN INGEN FORSLAG.
--
-- Grunnen er ikke at et svakt forslag er ubrukelig. Den er at et
-- forslag PRODUSERER FALSK TRYGGHET: en kode som står der ser like
-- ferdig ut som en noen har tenkt på, og den som stempler den har
-- flyttet ansvaret uten å ha flyttet kontrollen. Et tomt felt spør;
-- en kode uten grunnlag svarer.
--
-- DERFOR: `tollforslag` HAR INGEN KODE UTEN MINST ÉN
-- `forslagsgrunn`-RAD, håndhevet av `m52_avgi_forslag`, og hver grunn
-- peker på NOE ETTERPRØVBART — en regeltekst i nomenklaturen, en
-- tidligere klassifisering med sin dato, eller en bindende
-- forhåndsuttalelse. Invarianten `forslag_uten_grunnlag` er dermed
-- ikke en regel noen må huske; den er formen på dørene.
--
-- KLYNGE 7s DELTE DOM: REGELEN ER MYNDIGHETENS.
--
-- HS-nomenklaturen revideres — hvert femte år av WCO, og oftere
-- nasjonalt. En kode som var riktig i 2022 kan være avviklet i dag, og
-- et forslag mot en avviklet versjon er ikke et gammelt forslag: det
-- er et VELFORMET OG GALT svar. Derfor bærer hvert forslag
-- `nomenklaturversjon`, snapshotet, og døra nekter mot et utløpt sett
-- (`forslag_mot_utlopt_nomenklatur`).
--
-- SIKKERHETSTERSKELEN ER TENANTENS (`sikkerhetsterskel_hardkodet`).
-- Hvor sikker en klassifisering må være før den i det hele tatt vises
-- som et forslag, er en RISIKOVURDERING: en importør med tusen
-- kolliposter i uka og en med tre har ikke samme toleranse for å ta
-- feil. En konstant her ville vært en fullmakt modulen ga seg selv
-- over kundens bøter.
--
-- DOMMENE v1 HVILER PÅ, HÅNDHEVET I DATAMODELLEN:
--
--   1. HISTORIKKEN OVERSKRIVES ALDRI. Nomenklaturer, varer, forslag og
--      grunner er append-only med radvakt (M-42s dom, 110, gjentatt i
--      112–121).
--
--   2. HVERT FORSLAG BÆRER NOMENKLATURVERSJONEN SIN, snapshotet.
--
--   3. HVERT FORSLAG HAR MINST ÉN GRUNN, og hver grunn peker på noe
--      etterprøvbart.
--
--   4. EN NY VURDERING ER ET NYTT FORSLAG. Ny nomenklaturversjon eller
--      ny terskel gir en ny rad ved siden av den gamle — aldri i
--      stedet for. Samme dom som M-55s vurderinger (120) og M-54s
--      valideringer (121).
--
--   5. INGEN KOLONNE BETYR «DEKLARERT». Forslaget kan merkes KLART TIL
--      DEKLARERING — en tilstand hos oss, av samme slag som M-46s
--      «klar til gjennomgang» (118) og M-54s «klar til signering»
--      (121).
--
-- GRENSEN MOT M-28: M-28 eier TRANSPORTEN — hvor varen er og når den
-- kommer. M-52 eier FORTOLLINGEN — hva varen ER, rettslig. En vare kan
-- være framme og feilklassifisert, og de to feilene har ulik mottaker,
-- ulik frist og ulik sanksjon.
--
-- GRENSEN MOT M-14 (106): M-14 kontrollerer FAKTURAENS innhold mot
-- bestilling og mottak. M-52 klassifiserer VAREN. Fakturaen kan være
-- riktig og koden gal.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_tollkode_eier') THEN
        RAISE EXCEPTION 'rollen disponit_tollkode_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_tollkode_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `tollkrav` — ÉN per tenant. Tenantens egne terskler.
--
-- INVARIANTEN `sikkerhetsterskel_hardkodet` BOR HER. Hvor sikker en
-- klassifisering må være før den vises som et forslag, er en
-- RISIKOVURDERING: en importør med tusen kolliposter i uka og en med
-- tre har ikke samme toleranse for å ta feil.
CREATE TABLE tollkrav (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- SIKKERHETSTERSKELEN, i hele prosent. Et forslag under denne
    -- avgis ikke — det blir et FUNN, så noen ser at varen ikke lot
    -- seg klassifisere med den sikkerheten tenanten krever.
    sikkerhetsterskel INT NOT NULL DEFAULT 70
        CHECK (sikkerhetsterskel BETWEEN 1 AND 100),
    -- Hvor mange døgn før nomenklaturen utløper vi melder fra.
    utlopsvarsel_dogn INT NOT NULL DEFAULT 60
        CHECK (utlopsvarsel_dogn BETWEEN 1 AND 730),
    -- Hvor lenge en vare kan stå uten forslag før sveipen melder den.
    -- En vare ingen har klassifisert er en vare som fortolles på
    -- gjetning den dagen den skal ut.
    forslagsfrist_dogn INT NOT NULL DEFAULT 14
        CHECK (forslagsfrist_dogn BETWEEN 1 AND 365),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    -- IDEMPOTENSNØKKELEN SOM SATTE DENNE VERSJONEN (119s lærdom).
    siste_nokkel TEXT NOT NULL
        CHECK (siste_nokkel ~ '[^[:space:]]'),
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT tollkrav_pk PRIMARY KEY (tenant)
);

-- `nomenklatur` — MYNDIGHETENS REGEL, MED SIN GYLDIGHET.
--
-- Klyngens bærende tabell, samme form som M-54s `ehfregelsett` (121).
-- Registreres av et menneske som har lest nomenklaturen, med versjon,
-- innholdssum og et GYLDIGHETSVINDU.
--
-- ET ALT UTLØPT SETT KAN REGISTRERES, og det er med vilje: en
-- klassifisering fra 2022 må kunne forstås mot nomenklaturen som
-- gjaldt DA. Å forby arkivet er å forby spørsmålet — 121s lærdom,
-- skrevet ned der.
--
-- IDENTITETEN ER FROSSET; bare `gyldig_til` kan settes senere, fordi
-- et tollvesen som kunngjør en avviklingsdato i juni er nettopp den
-- endringen modulen skal følge med på.
CREATE TABLE nomenklatur (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    nomenklatur_id UUID NOT NULL,
    -- HVILKET REGELVERK. Lukket liste: de tre lagene i en norsk
    -- tolltariff er ikke samme regel, og et forslag må kunne skilles
    -- på hvilket det hviler på.
    system TEXT NOT NULL
        CONSTRAINT nomenklatur_system_lukket CHECK (system IN (
            'hs',          -- WCOs seksifrede grunnstamme
            'kn',          -- EUs kombinerte nomenklatur (åtte siffer)
            'tolltariff')),-- den norske tariffen (ti siffer)
    -- REGELVERKETS EGEN VERSJONSBETEGNELSE («HS 2022»). Fri tekst:
    -- versjoneringen er myndighetens, ikke vår.
    versjon TEXT NOT NULL CHECK (versjon ~ '[^[:space:]]'),
    gyldig_fra DATE NOT NULL,
    gyldig_til DATE,
    CONSTRAINT nomenklatur_vindu_er_ekte CHECK (
        gyldig_til IS NULL OR gyldig_til >= gyldig_fra),
    innhold_sha256 TEXT NOT NULL
        CHECK (innhold_sha256 ~ '^[0-9a-f]{64}$'),
    kilde_url TEXT
        CONSTRAINT nomenklatur_url_er_web CHECK (
            kilde_url IS NULL
            OR (kilde_url ~ '^https?://[^[:space:]]+$'
                AND length(kilde_url) <= 2000)),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT nomenklatur_pk PRIMARY KEY (tenant, nomenklatur_id),
    CONSTRAINT nomenklatur_unik UNIQUE (tenant, system, versjon)
);

CREATE INDEX nomenklatur_gyldige_idx ON nomenklatur
    (tenant, system, gyldig_fra DESC);

-- `varenummer` — ÉN POSISJON I EN NOMENKLATUR.
--
-- Koden ALENE er ikke nok: teksten er det en klassifisering faktisk
-- argumenteres mot, og satsen er det som gjør feilen dyr. Begge står
-- her, frosset sammen med nomenklaturen de hører til.
CREATE TABLE varenummer (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    varenummer_id UUID NOT NULL,
    nomenklatur_id UUID NOT NULL,
    -- KODEN. Sifre og punktum — formen er nomenklaturens, og lengden
    -- varierer mellom systemene (6/8/10), så den er ikke låst her.
    kode TEXT NOT NULL
        CONSTRAINT varenummer_kode_er_siffer CHECK (
            kode ~ '^[0-9]{4}[0-9.]*$' AND length(kode) <= 20),
    -- POSISJONSTEKSTEN, ORDRETT. Det er DENNE en klassifisering
    -- argumenteres mot — ikke koden, som bare er en adresse.
    tekst TEXT NOT NULL
        CHECK (tekst ~ '[^[:space:]]' AND length(tekst) <= 4000),
    -- TOLLSATSEN I BASISPUNKTER (hundredels prosent), HELTALL.
    -- 106s dom: en sats i flyttall ville gjort «hva koster feilen» til
    -- et spørsmål med to svar. NULL betyr «ikke registrert her», ikke
    -- «null toll» — og skillet er hele forskjellen mellom en vare som
    -- er tollfri og en vi ikke vet satsen på.
    tollsats_bp INT CHECK (tollsats_bp IS NULL
                           OR tollsats_bp BETWEEN 0 AND 1000000),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT varenummer_pk PRIMARY KEY (tenant, varenummer_id),
    CONSTRAINT varenummer_nomenklatur_fk FOREIGN KEY
        (tenant, nomenklatur_id)
        REFERENCES nomenklatur (tenant, nomenklatur_id),
    CONSTRAINT varenummer_unik UNIQUE (tenant, nomenklatur_id, kode)
);

CREATE INDEX varenummer_oppslag_idx ON varenummer
    (tenant, nomenklatur_id, kode);

-- `tollvare` — VÅR EGEN VARE, SLIK TOLLMYNDIGHETEN SER DEN.
--
-- NAVNET ER `tollvare` OG IKKE `vare`, og det er ikke bare for å unngå
-- kollisjonen med M-27s lagerregister (109). Det er FORDI de to er
-- ulike ting: M-27s `vare` er artikkelen på lageret — den har antall,
-- plassering og beholdning. Denne raden er varen som et FORTOLLINGS-
-- OBJEKT, og bærer nettopp det HS-nomenklaturen klassifiserer på.
--
-- De to deler ingen fremmednøkkel, og det er med vilje: en vare vi
-- ikke har på lager skal kunne klassifiseres, og en lagervare uten
-- import trenger ingen kode.
--
-- FROSSET. Beskrivelsen et forslag ble avgitt mot er en del av
-- forslaget, og en beskrivelse som kunne redigeres i ettertid ville
-- gjort hvert eldre forslag uetterprøvbart.
--
-- MATERIALE OG BRUK ER EGNE FELTER, ikke fritekst i beskrivelsen: de
-- to er nettopp det HS-nomenklaturen klassifiserer på — en skrue av
-- stål og en av plast havner i ulike kapitler, og en del til bil og
-- en til møbel likeså. Å gjemme dem i en setning ville gjort
-- grunnlaget uleselig for den som skal etterprøve.
CREATE TABLE tollvare (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    vare_id UUID NOT NULL,
    -- VÅR EGEN REFERANSE: varenummer i lageret, artikkelnummer.
    ekstern_ref TEXT NOT NULL CHECK (ekstern_ref ~ '[^[:space:]]'),
    beskrivelse TEXT NOT NULL
        CHECK (beskrivelse ~ '[^[:space:]]'
               AND length(beskrivelse) <= 4000),
    -- DET NOMENKLATUREN FAKTISK KLASSIFISERER PÅ.
    materiale TEXT
        CHECK (materiale IS NULL OR materiale ~ '[^[:space:]]'),
    bruk TEXT CHECK (bruk IS NULL OR bruk ~ '[^[:space:]]'),
    -- OPPRINNELSESLAND, ISO 3166-1 alpha-2. Det avgjør preferansetoll
    -- og er derfor en del av «hva koster feilen».
    opprinnelsesland TEXT
        CHECK (opprinnelsesland IS NULL
               OR opprinnelsesland ~ '^[A-Z]{2}$'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT tollvare_pk PRIMARY KEY (tenant, vare_id),
    CONSTRAINT tollvare_unik UNIQUE (tenant, ekstern_ref)
);

-- `tollforslag` — FORSLAGET, ALDRI DEKLARASJONEN.
--
-- `nomenklatur_id` ER NOT NULL MED FREMMEDNØKKEL, og versjonen er
-- SNAPSHOTET ved siden av: invarianten
-- `forslag_uten_nomenklaturversjon` er formen på tabellen.
--
-- `over_terskel` ER GENERERT. Dommen kan ikke være uenig med sine egne
-- tall: `sikkerhet >= terskel_brukt`, regnet av basen.
--
-- DET FINNES INGEN «DEKLARERT»-KOLONNE. Forslaget kan merkes KLART TIL
-- DEKLARERING — en tilstand hos oss.
--
-- APPEND-ONLY. Et forslag som kunne endres i ettertid ville gjort
-- «hva foreslo vi, og hvorfor» til et spørsmål uten svar den dagen
-- tollmyndigheten spør. `forslag_overskrevet` er den invarianten.
CREATE TABLE tollforslag (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    forslag_id UUID NOT NULL,
    vare_id UUID NOT NULL,
    nomenklatur_id UUID NOT NULL,
    varenummer_id UUID NOT NULL,
    -- SNAPSHOTENE. Systemet, versjonen og koden slik de var.
    system_ved_forslag TEXT NOT NULL
        CHECK (system_ved_forslag ~ '[^[:space:]]'),
    versjon_ved_forslag TEXT NOT NULL
        CHECK (versjon_ved_forslag ~ '[^[:space:]]'),
    kode_ved_forslag TEXT NOT NULL
        CHECK (kode_ved_forslag ~ '[^[:space:]]'),
    -- …OG BESKRIVELSEN DEN BLE AVGITT MOT. Uten den kan ingen etterpå
    -- se HVA som ble klassifisert — bare hva det ble klassifisert som.
    beskrivelse_ved_forslag TEXT NOT NULL
        CHECK (beskrivelse_ved_forslag ~ '[^[:space:]]'),
    -- SIKKERHETEN, i hele prosent. Den er MENNESKETS vurdering, ikke
    -- modulens: v1 regner ingen sannsynlighet. Å la modulen anslå sin
    -- egen sikkerhet ville vært å la den definere sin troverdighet.
    sikkerhet INT NOT NULL CHECK (sikkerhet BETWEEN 0 AND 100),
    terskel_brukt INT NOT NULL
        CHECK (terskel_brukt BETWEEN 1 AND 100),
    kravversjon INT NOT NULL CHECK (kravversjon >= 1),
    over_terskel BOOLEAN NOT NULL
        GENERATED ALWAYS AS (sikkerhet >= terskel_brukt) STORED,
    klar_til_deklarering BOOLEAN NOT NULL DEFAULT false,
    klar_ts TIMESTAMPTZ,
    klar_av TEXT CHECK (klar_av IS NULL OR klar_av ~ '[^[:space:]]'),
    CONSTRAINT tollforslag_klar_er_hel CHECK (
        klar_til_deklarering = (klar_ts IS NOT NULL)
        AND klar_til_deklarering = (klar_av IS NOT NULL)),
    avgitt TIMESTAMPTZ NOT NULL DEFAULT now(),
    avgitt_av TEXT NOT NULL CHECK (avgitt_av ~ '[^[:space:]]'),
    CONSTRAINT tollforslag_pk PRIMARY KEY (tenant, forslag_id),
    CONSTRAINT tollforslag_vare_fk FOREIGN KEY (tenant, vare_id)
        REFERENCES tollvare (tenant, vare_id),
    CONSTRAINT tollforslag_nomenklatur_fk FOREIGN KEY
        (tenant, nomenklatur_id)
        REFERENCES nomenklatur (tenant, nomenklatur_id),
    CONSTRAINT tollforslag_varenummer_fk FOREIGN KEY
        (tenant, varenummer_id)
        REFERENCES varenummer (tenant, varenummer_id),
    -- SAMME VARE MOT SAMME NOMENKLATUR ER ÉTT FORSLAG. Et NYTT
    -- regelverk gir en ny rad ved siden av den gamle, aldri i stedet.
    CONSTRAINT tollforslag_unik UNIQUE (
        tenant, vare_id, nomenklatur_id)
);

CREATE INDEX tollforslag_vare_idx ON tollforslag
    (tenant, vare_id, avgitt DESC);

-- `forslagsgrunn` — HVA FORSLAGET HVILER PÅ.
--
-- MODULENS SKARPESTE TABELL. `m52_avgi_forslag` NEKTER uten minst én
-- rad her, og hver rad peker på NOE ETTERPRØVBART.
--
-- ET FORSLAG UTEN GRUNNLAG ER VERRE ENN INGEN FORSLAG, fordi det
-- produserer falsk trygghet: en kode som står der ser like ferdig ut
-- som en noen har tenkt på. Et tomt felt SPØR; en kode uten grunnlag
-- SVARER.
--
-- FROSSET, som forslaget den hører til.
CREATE TABLE forslagsgrunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    grunn_id UUID NOT NULL,
    forslag_id UUID NOT NULL,
    -- HVA SLAGS GRUNN. Lukket liste, og rekkefølgen er rettskildenes:
    -- en bindende forhåndsuttalelse veier tyngre enn en egen tidligere
    -- klassifisering, som veier tyngre enn en tekstlikhet.
    art TEXT NOT NULL
        CONSTRAINT forslagsgrunn_art_lukket CHECK (art IN (
            'bindende_forhandsuttalelse',
            'tidligere_klassifisering',
            'nomenklaturtekst',
            'alminnelig_fortolkningsregel',
            'faglig_vurdering')),
    -- HENVISNINGEN. Saksnummer, varenummer, regelnummer — det som gjør
    -- grunnen mulig å slå opp. PÅKREVD: en grunn ingen kan slå opp er
    -- en påstand, ikke en grunn.
    henvisning TEXT NOT NULL
        CHECK (henvisning ~ '[^[:space:]]'
               AND length(henvisning) <= 500),
    -- …OG HVA DEN SIER, ordrett nok til å leses uten oppslaget.
    utdrag TEXT NOT NULL
        CHECK (length(btrim(utdrag)) >= 4 AND length(utdrag) <= 4000),
    -- DATOEN GRUNNEN GJELDER FRA. En tidligere klassifisering fra 2019
    -- er ikke like tung som en fra i fjor, og en forhåndsuttalelse har
    -- en gyldighetstid.
    grunn_dato DATE,
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT forslagsgrunn_pk PRIMARY KEY (tenant, grunn_id),
    CONSTRAINT forslagsgrunn_forslag_fk FOREIGN KEY
        (tenant, forslag_id)
        REFERENCES tollforslag (tenant, forslag_id),
    CONSTRAINT forslagsgrunn_unik UNIQUE (
        tenant, forslag_id, art, henvisning)
);

CREATE INDEX forslagsgrunn_forslag_idx ON forslagsgrunn
    (tenant, forslag_id);

-- `tollfunn` — NATTENS FUNN.
--
-- `forslag_uten_grunnlag` KAN IKKE OPPSTÅ i basen — døra nekter — så
-- funntypen finnes ikke. Det som FINNES er dens motstykke:
-- `vare_uten_forslag`, altså en vare ingen har klassifisert. Den er
-- den ærlige tilstanden når grunnlaget mangler, og den er et funn
-- nettopp fordi et forslag ikke ble avgitt.
--
-- `forslag_mot_utlopt_nomenklatur` KAN IKKE LUKKES AV ET MENNESKE.
-- Det funnet forsvinner når varen klassifiseres på nytt mot en gyldig
-- nomenklatur — og det er en HANDLING, ikke en mening. Samme figur
-- som M-49s bekreftede treff (117), M-46s udekkede absolutte krav
-- (118), M-51s takfunn (119), M-55s uhenviste forveksling (120) og
-- M-54s dom under utløpt regelsett (121).
CREATE TABLE tollfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    funn_id UUID NOT NULL,
    -- NØKKELEN. Nøyaktig ett av de tre er satt.
    nomenklatur_id UUID,
    vare_id UUID,
    forslag_id UUID,
    funntype TEXT NOT NULL
        CONSTRAINT tollfunn_type_lukket CHECK (funntype IN (
            'nomenklatur_utlopt',
            'nomenklatur_utloper_snart',
            'forslag_mot_utlopt_nomenklatur',
            'vare_uten_forslag',
            'forslag_under_terskel',
            'forslag_ikke_klart',
            'ingen_krav')),
    CONSTRAINT tollfunn_nivaa_folger_type CHECK (
        CASE funntype
          WHEN 'nomenklatur_utlopt' THEN nomenklatur_id IS NOT NULL
          WHEN 'nomenklatur_utloper_snart'
            THEN nomenklatur_id IS NOT NULL
          -- `ingen_krav` HENGER PÅ DET TENANTEN FAKTISK HAR.
          -- Vanligvis en nomenklatur. Men en tenant som har varer og
          -- ingen nomenklatur har heller ingen nomenklaturrad å henge
          -- funnet på — og han er nettopp den som trenger det: varer
          -- på vei ut, og ingenting å klassifisere dem mot.
          WHEN 'ingen_krav'
            THEN nomenklatur_id IS NOT NULL OR vare_id IS NOT NULL
          WHEN 'vare_uten_forslag' THEN vare_id IS NOT NULL
          ELSE forslag_id IS NOT NULL
        END),
    CONSTRAINT tollfunn_en_noekkel CHECK (
        num_nonnulls(nomenklatur_id, vare_id, forslag_id) = 1),
    over_grense INT,
    detalj TEXT CHECK (detalj IS NULL OR detalj ~ '[^[:space:]]'),
    -- SIKKERHETEN PÅ FUNNET. «Under terskel» uten å si hvor mye er en
    -- beskjed man ikke kan handle på (119s lærdom).
    sikkerhet INT CHECK (sikkerhet IS NULL
                         OR sikkerhet BETWEEN 0 AND 100),
    terskel_brukt INT CHECK (terskel_brukt IS NULL
                             OR terskel_brukt BETWEEN 1 AND 100),
    kravversjon INT CHECK (kravversjon IS NULL OR kravversjon >= 1),
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT CHECK (lukket_av IS NULL
                          OR lukket_av ~ '[^[:space:]]'),
    lukkenotat TEXT
        CHECK (lukkenotat IS NULL
               OR (length(btrim(lukkenotat)) >= 4
                   AND length(lukkenotat) <= 4000)),
    CONSTRAINT tollfunn_lukking_er_hel CHECK (
        num_nulls(lukket_ts, lukket_av, lukkenotat) IN (0, 3)),
    CONSTRAINT tollfunn_apen_er_ulukket CHECK (
        apen = (lukket_ts IS NULL)),
    CONSTRAINT tollfunn_pk PRIMARY KEY (tenant, funn_id),
    CONSTRAINT tollfunn_nomenklatur_fk FOREIGN KEY
        (tenant, nomenklatur_id)
        REFERENCES nomenklatur (tenant, nomenklatur_id),
    CONSTRAINT tollfunn_vare_fk FOREIGN KEY (tenant, vare_id)
        REFERENCES tollvare (tenant, vare_id),
    CONSTRAINT tollfunn_forslag_fk FOREIGN KEY (tenant, forslag_id)
        REFERENCES tollforslag (tenant, forslag_id)
);

-- TRE DELVISE UNIKHETSINDEKSER, én per nøkkeltype (121s form): en
-- sammensatt primærnøkkel med NULL i seg kunne ikke rommet dem, fordi
-- NULL aldri er lik NULL.
CREATE UNIQUE INDEX tollfunn_nomenklatur_unik ON tollfunn
    (tenant, nomenklatur_id, funntype)
    WHERE nomenklatur_id IS NOT NULL;
CREATE UNIQUE INDEX tollfunn_vare_unik ON tollfunn
    (tenant, vare_id, funntype) WHERE vare_id IS NOT NULL;
CREATE UNIQUE INDEX tollfunn_forslag_unik ON tollfunn
    (tenant, forslag_id, funntype) WHERE forslag_id IS NOT NULL;
CREATE INDEX tollfunn_apne_idx ON tollfunn (tenant, funntype)
    WHERE apen;


-- ------------------------------------------------------------
-- 2. Evidenskjeden og dørene. Eieren eier dem, og eierskapet ER
--    fullmakten.
-- ------------------------------------------------------------

-- `krev_tenantkontekst` eies av `disponit_m37_claimer` (111s form), så
-- EXECUTE må gis AV den rollen (116s lærdom).
GRANT INSERT ON revisjonslogg TO disponit_tollkode_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_tollkode_eier;
RESET ROLE;

-- HERFRA OG TIL SEKSJON 6 EIES ALT SOM LAGES AV TOLLKODEEIEREN.
SET LOCAL ROLE disponit_tollkode_eier;

CREATE FUNCTION m52_evidens(p_tenant TEXT, p_vare_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm52_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm52_tollkode', 'handling', p_handling,
        'vare_id', p_vare_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm52_tollkode',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:tollkode', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m52_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;


-- ER NOMENKLATUREN GYLDIG I DAG? Samme form og samme grunn som M-54s
-- `m54_regelsett_gyldig` (121): `gyldig_til IS NULL` betyr «gjelder
-- fortsatt», ikke «gjelder for alltid». STABLE og ikke IMMUTABLE —
-- den leser dagens dato.
CREATE FUNCTION m52_nomenklatur_gyldig(p_fra DATE, p_til DATE)
RETURNS BOOLEAN LANGUAGE sql STABLE
SET search_path = pg_catalog AS $$
    SELECT p_fra <= current_date
       AND (p_til IS NULL OR p_til >= current_date)
$$;
GRANT EXECUTE ON FUNCTION m52_nomenklatur_gyldig(DATE, DATE)
    TO PUBLIC;


-- ------------------------------------------------------------
-- 3. Skrivedørene.
-- ------------------------------------------------------------

-- TERSKLENE (119s form: nøkkelen står inne i døra fordi raden er en
-- singleton per tenant og ikke har en id å utlede fra den).
--
-- SIKKERHETSTERSKELEN ER TENANTENS. En importør med tusen kolliposter
-- i uka og en med tre har ikke samme toleranse for å ta feil, og en
-- konstant her ville vært en fullmakt modulen ga seg selv over kundens
-- bøter.
CREATE FUNCTION m52_sett_krav(
    p_tenant TEXT, p_sikkerhetsterskel INT, p_utlopsvarsel_dogn INT,
    p_forslagsfrist_dogn INT, p_aktor TEXT, p_nokkel TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT; v_nokkel TEXT; v_likt BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm52_sett_krav');
    IF p_nokkel IS NULL OR btrim(p_nokkel) = '' THEN
        RAISE EXCEPTION 'm52_sett_krav: idempotensnøkkel mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.aktor', p_aktor, true);

    -- LÅS FØRST, LES NØKKELEN ETTERPÅ (119–121s form).
    PERFORM 1 FROM public.tollkrav
     WHERE tenant = p_tenant FOR UPDATE;
    SELECT k.versjon, k.siste_nokkel,
           (k.sikkerhetsterskel = p_sikkerhetsterskel
            AND k.utlopsvarsel_dogn = p_utlopsvarsel_dogn
            AND k.forslagsfrist_dogn = p_forslagsfrist_dogn)
      INTO v_versjon, v_nokkel, v_likt
      FROM public.tollkrav k WHERE k.tenant = p_tenant;

    IF v_nokkel IS NOT NULL AND v_nokkel = p_nokkel THEN
        IF v_likt THEN
            RETURN v_versjon;
        END IF;
        RAISE EXCEPTION 'm52_sett_krav: nøkkelen % er alt brukt på'
            ' andre verdier', p_nokkel
            USING ERRCODE = 'unique_violation';
    END IF;

    INSERT INTO public.tollkrav
        (tenant, sikkerhetsterskel, utlopsvarsel_dogn,
         forslagsfrist_dogn, siste_nokkel, oppdatert_av)
    VALUES (p_tenant, p_sikkerhetsterskel, p_utlopsvarsel_dogn,
            p_forslagsfrist_dogn, btrim(p_nokkel), p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        sikkerhetsterskel = EXCLUDED.sikkerhetsterskel,
        utlopsvarsel_dogn = EXCLUDED.utlopsvarsel_dogn,
        forslagsfrist_dogn = EXCLUDED.forslagsfrist_dogn,
        siste_nokkel = EXCLUDED.siste_nokkel,
        versjon = public.tollkrav.versjon + 1,
        oppdatert = now(),
        oppdatert_av = EXCLUDED.oppdatert_av
    RETURNING versjon INTO v_versjon;

    PERFORM public.m52_evidens(p_tenant, NULL, 'tollkrav_satt',
        p_aktor, jsonb_build_object(
            'versjon', v_versjon,
            'sikkerhetsterskel', p_sikkerhetsterskel));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION
    m52_sett_krav(TEXT, INT, INT, INT, TEXT, TEXT) FROM PUBLIC;


-- NOMENKLATUREN. Registreres av et menneske som har lest den.
--
-- ET ALT UTLØPT SETT KAN REGISTRERES, og det er 121s lærdom tatt med
-- hit: en klassifisering fra 2022 må kunne forstås mot nomenklaturen
-- som gjaldt DA. Å forby arkivet er å forby spørsmålet. Skillet går
-- ved FORSLAGET — `m52_avgi_forslag` nekter mot et utløpt sett.
CREATE FUNCTION m52_registrer_nomenklatur(
    p_tenant TEXT, p_nomenklatur_id UUID, p_system TEXT,
    p_versjon TEXT, p_gyldig_fra DATE, p_gyldig_til DATE,
    p_innhold_sha256 TEXT, p_kilde_url TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm52_registrer_nomenklatur');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.nomenklatur
        (tenant, nomenklatur_id, system, versjon, gyldig_fra,
         gyldig_til, innhold_sha256, kilde_url, registrert_av)
    VALUES (p_tenant, p_nomenklatur_id, p_system, btrim(p_versjon),
            p_gyldig_fra, p_gyldig_til,
            lower(btrim(p_innhold_sha256)),
            nullif(btrim(coalesce(p_kilde_url, '')), ''), p_aktor);
    PERFORM public.m52_evidens(p_tenant, NULL,
        'nomenklatur_registrert', p_aktor, jsonb_build_object(
            'nomenklatur_id', p_nomenklatur_id::text,
            'system', p_system, 'versjon', btrim(p_versjon),
            'gyldig_til', p_gyldig_til));
END $$;
REVOKE ALL ON FUNCTION m52_registrer_nomenklatur(
    TEXT, UUID, TEXT, TEXT, DATE, DATE, TEXT, TEXT, TEXT)
    FROM PUBLIC;


-- AVVIKLINGSDATOEN KAN SETTES ETTERPÅ, OG BARE DEN (121s dom).
--
-- Et tollvesen som kunngjør i juni at HS 2022 avvikles 31. desember,
-- er nettopp den endringen modulen skal følge med på. Alt annet ved
-- nomenklaturen er IDENTITETEN — system, versjon, `gyldig_fra`,
-- innholdssummen — og den er det som gjør et gammelt forslag
-- etterprøvbart.
CREATE FUNCTION m52_sett_gyldig_til(
    p_tenant TEXT, p_nomenklatur_id UUID, p_gyldig_til DATE,
    p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_fra DATE; v_for DATE; v_sys TEXT; v_ver TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm52_sett_gyldig_til');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT n.gyldig_fra, n.gyldig_til, n.system, n.versjon
      INTO v_fra, v_for, v_sys, v_ver
      FROM public.nomenklatur n
     WHERE n.tenant = p_tenant
       AND n.nomenklatur_id = p_nomenklatur_id;
    IF v_fra IS NULL THEN
        RAISE EXCEPTION 'm52_sett_gyldig_til: ukjent nomenklatur %',
            p_nomenklatur_id USING ERRCODE = 'no_data_found';
    END IF;
    IF p_gyldig_til IS NOT NULL AND p_gyldig_til < v_fra THEN
        RAISE EXCEPTION 'm52_sett_gyldig_til: % er før settets'
            ' startdato %', p_gyldig_til, v_fra
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.nomenklatur SET gyldig_til = p_gyldig_til
     WHERE tenant = p_tenant
       AND nomenklatur_id = p_nomenklatur_id;
    PERFORM public.m52_evidens(p_tenant, NULL,
        'nomenklatur_gyldig_til_satt', p_aktor, jsonb_build_object(
            'nomenklatur_id', p_nomenklatur_id::text,
            'system', v_sys, 'versjon', v_ver,
            'fra', v_for, 'til', p_gyldig_til));
END $$;
REVOKE ALL ON FUNCTION
    m52_sett_gyldig_til(TEXT, UUID, DATE, TEXT) FROM PUBLIC;


-- ÉN POSISJON I NOMENKLATUREN.
CREATE FUNCTION m52_registrer_varenummer(
    p_tenant TEXT, p_varenummer_id UUID, p_nomenklatur_id UUID,
    p_kode TEXT, p_tekst TEXT, p_tollsats_bp INT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm52_registrer_varenummer');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.varenummer
        (tenant, varenummer_id, nomenklatur_id, kode, tekst,
         tollsats_bp, registrert_av)
    VALUES (p_tenant, p_varenummer_id, p_nomenklatur_id,
            btrim(p_kode), btrim(p_tekst), p_tollsats_bp, p_aktor);
    PERFORM public.m52_evidens(p_tenant, NULL,
        'varenummer_registrert', p_aktor, jsonb_build_object(
            'nomenklatur_id', p_nomenklatur_id::text,
            'kode', btrim(p_kode)));
END $$;
REVOKE ALL ON FUNCTION m52_registrer_varenummer(
    TEXT, UUID, UUID, TEXT, TEXT, INT, TEXT) FROM PUBLIC;


-- VÅR EGEN VARE.
CREATE FUNCTION m52_registrer_vare(
    p_tenant TEXT, p_vare_id UUID, p_ekstern_ref TEXT,
    p_beskrivelse TEXT, p_materiale TEXT, p_bruk TEXT,
    p_opprinnelsesland TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm52_registrer_vare');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.tollvare
        (tenant, vare_id, ekstern_ref, beskrivelse, materiale, bruk,
         opprinnelsesland, registrert_av)
    VALUES (p_tenant, p_vare_id, btrim(p_ekstern_ref),
            btrim(p_beskrivelse),
            nullif(btrim(coalesce(p_materiale, '')), ''),
            nullif(btrim(coalesce(p_bruk, '')), ''),
            nullif(btrim(upper(coalesce(p_opprinnelsesland, ''))), ''),
            p_aktor);
    PERFORM public.m52_evidens(p_tenant, p_vare_id,
        'vare_registrert', p_aktor, jsonb_build_object(
            'ekstern_ref', btrim(p_ekstern_ref),
            'har_materiale', p_materiale IS NOT NULL,
            'har_bruk', p_bruk IS NOT NULL));
END $$;
REVOKE ALL ON FUNCTION m52_registrer_vare(
    TEXT, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;


-- FORSLAGET, MED GRUNNENE SINE. MODULENS KJERNE.
--
-- GRUNNENE KOMMER INN I SAMME KALL SOM FORSLAGET, og det er ikke
-- bekvemmelighet — det er invarianten. Hadde grunnene vært et eget
-- kall etterpå, ville et forslag uten grunnlag EKSISTERT i vinduet
-- mellom de to, og en flate som leste i det vinduet ville vist en kode
-- ingen kunne etterprøve. `forslag_uten_grunnlag` er derfor formen på
-- DØRA, ikke bare på tabellen.
--
-- ET FORSLAG UTEN GRUNNLAG ER VERRE ENN INGEN FORSLAG: en kode som
-- står der ser like ferdig ut som en noen har tenkt på, og den som
-- stempler den har flyttet ansvaret uten å ha flyttet kontrollen. Et
-- tomt felt SPØR; en kode uten grunnlag SVARER.
--
-- DØRA NEKTER MOT EN UTLØPT NOMENKLATUR. En kode som var riktig i 2022
-- kan være avviklet i dag, og et forslag mot en avviklet versjon er
-- ikke et gammelt forslag — det er et velformet og galt svar.
--
-- SIKKERHETEN ER MENNESKETS TALL, IKKE MODULENS. v1 regner ingen
-- sannsynlighet: å la modulen anslå sin egen sikkerhet ville vært å la
-- den definere sin egen troverdighet. Under tenantens terskel avgis
-- forslaget IKKE — det blir et funn, så noen ser at varen ikke lot seg
-- klassifisere med den sikkerheten tenanten krever.
CREATE FUNCTION m52_avgi_forslag(
    p_tenant TEXT, p_forslag_id UUID, p_vare_id UUID,
    p_varenummer_id UUID, p_sikkerhet INT, p_grunn_arter TEXT[],
    p_grunn_henvisninger TEXT[], p_grunn_utdrag TEXT[],
    p_grunn_datoer DATE[], p_aktor TEXT)
RETURNS TABLE (sikkerhet INT, terskel_brukt INT,
               over_terskel BOOLEAN, antall_grunner INT,
               system TEXT, versjon TEXT, kode TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_terskel INT; v_kravversjon INT;
    v_nomenklatur_id UUID; v_sys TEXT; v_ver TEXT; v_kode TEXT;
    v_fra DATE; v_til DATE; v_beskrivelse TEXT;
    v_n INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm52_avgi_forslag');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    -- TERSKELEN ER TENANTENS, OG DØRA NEKTER UTEN DEN. Et vennlig
    -- standardtall ville vært nettopp den hardkodede terskelen
    -- `sikkerhetsterskel_hardkodet` forbyr.
    SELECT k.sikkerhetsterskel, k.versjon
      INTO v_terskel, v_kravversjon
      FROM public.tollkrav k WHERE k.tenant = p_tenant;
    IF v_terskel IS NULL THEN
        RAISE EXCEPTION 'm52_avgi_forslag: tenanten har ingen'
            ' sikkerhetsterskel. Hvor sikker en klassifisering må'
            ' være før den vises som et forslag er en risikovurdering,'
            ' ikke en konstant — sett den med m52_sett_krav først'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- MINST ÉN GRUNN, OG FIRE LISTER AV SAMME LENGDE. Ulik lengde
    -- ville stilltiende kappet den korteste, og en grunn som forsvant
    -- i kappingen ville gjort forslaget svakere enn det ser ut.
    IF p_grunn_arter IS NULL OR cardinality(p_grunn_arter) = 0 THEN
        RAISE EXCEPTION 'm52_avgi_forslag: forslaget har ingen grunn.'
            ' En HS-kode er en rettslig påstand om hva varen er, og et'
            ' forslag uten grunnlag produserer falsk trygghet — et'
            ' tomt felt spør, en kode uten grunnlag svarer'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- …OG INGEN AV DEM ER NULL. `cardinality(NULL)` ER NULL, så
    -- sammenligningen under ble NULL — altså ikke SANN — og vakten slo
    -- ikke til. Med `p_grunn_arter` fylt gikk forslaget gjennom, mens
    -- `unnest` over en NULL-liste ga NULL RADER: et forslag som
    -- beskriver seg selv som begrunnet, uten en eneste grunn i basen.
    -- Det er presis den falske tryggheten modulen finnes for å hindre,
    -- og den kom inn gjennom modulens egen vakt (CodeRabbit).
    IF p_grunn_henvisninger IS NULL OR p_grunn_utdrag IS NULL
       OR p_grunn_datoer IS NULL THEN
        RAISE EXCEPTION 'm52_avgi_forslag: en av grunnlistene er NULL'
            ' (henvisninger %, utdrag %, datoer %). Fire lister av'
            ' samme lengde er invarianten',
            (p_grunn_henvisninger IS NULL),
            (p_grunn_utdrag IS NULL), (p_grunn_datoer IS NULL)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF cardinality(p_grunn_henvisninger) <> cardinality(p_grunn_arter)
       OR cardinality(p_grunn_utdrag) <> cardinality(p_grunn_arter)
       OR cardinality(p_grunn_datoer) <> cardinality(p_grunn_arter)
    THEN
        RAISE EXCEPTION 'm52_avgi_forslag: grunnlistene har ulik'
            ' lengde (%, %, %, %)', cardinality(p_grunn_arter),
            cardinality(p_grunn_henvisninger),
            cardinality(p_grunn_utdrag), cardinality(p_grunn_datoer)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT v.nomenklatur_id, v.kode, n.system, n.versjon,
           n.gyldig_fra, n.gyldig_til
      INTO v_nomenklatur_id, v_kode, v_sys, v_ver, v_fra, v_til
      FROM public.varenummer v
      JOIN public.nomenklatur n
        ON n.tenant = v.tenant
       AND n.nomenklatur_id = v.nomenklatur_id
     WHERE v.tenant = p_tenant AND v.varenummer_id = p_varenummer_id;
    IF v_nomenklatur_id IS NULL THEN
        RAISE EXCEPTION 'm52_avgi_forslag: ukjent varenummer %',
            p_varenummer_id USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT public.m52_nomenklatur_gyldig(v_fra, v_til) THEN
        RAISE EXCEPTION 'm52_avgi_forslag: nomenklaturen % % er ikke'
            ' gyldig i dag (% til %). En kode som var riktig da, kan'
            ' være avviklet nå — og et forslag mot en avviklet versjon'
            ' er et velformet og galt svar',
            v_sys, v_ver, v_fra, coalesce(v_til::text, '—')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT v.beskrivelse INTO v_beskrivelse
      FROM public.tollvare v
     WHERE v.tenant = p_tenant AND v.vare_id = p_vare_id;
    IF v_beskrivelse IS NULL THEN
        RAISE EXCEPTION 'm52_avgi_forslag: ukjent vare %', p_vare_id
            USING ERRCODE = 'no_data_found';
    END IF;

    -- UNDER TERSKELEN AVGIS INGENTING. Forslaget ville sett like
    -- ferdig ut som ett over, og sikkerheten står bare på raden — ikke
    -- i øyet til den som leser den i en liste.
    IF p_sikkerhet < v_terskel THEN
        RAISE EXCEPTION 'm52_avgi_forslag: sikkerheten % prosent er'
            ' under tenantens terskel på % prosent. Et forslag under'
            ' terskelen avgis ikke — varen blir stående uten, så noen'
            ' ser at den ikke lot seg klassifisere',
            p_sikkerhet, v_terskel
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- FORSLAGET OG GRUNNENE I ÉN SETNING, og rekkefølgen er tvunget:
    -- `forslagsgrunn` peker på `tollforslag`. Krysskoblingen til slutt
    -- er ikke pynt — uten den ville de data-modifiserende CTE-ene ikke
    -- blitt kjørt (121s målte lærdom).
    WITH f AS (
        INSERT INTO public.tollforslag
            (tenant, forslag_id, vare_id, nomenklatur_id,
             varenummer_id, system_ved_forslag, versjon_ved_forslag,
             kode_ved_forslag, beskrivelse_ved_forslag, sikkerhet,
             terskel_brukt, kravversjon, avgitt_av)
        VALUES (p_tenant, p_forslag_id, p_vare_id, v_nomenklatur_id,
                p_varenummer_id, v_sys, v_ver, v_kode, v_beskrivelse,
                p_sikkerhet, v_terskel, v_kravversjon, p_aktor)
        RETURNING 1),
    g AS (
        INSERT INTO public.forslagsgrunn
            (tenant, grunn_id, forslag_id, art, henvisning, utdrag,
             grunn_dato, registrert_av)
        SELECT p_tenant, gen_random_uuid(), p_forslag_id, a.art,
               btrim(h.henvisning), btrim(u.utdrag), d.dato, p_aktor
          FROM unnest(p_grunn_arter) WITH ORDINALITY AS a(art, i)
          JOIN unnest(p_grunn_henvisninger) WITH ORDINALITY
               AS h(henvisning, i) ON h.i = a.i
          JOIN unnest(p_grunn_utdrag) WITH ORDINALITY AS u(utdrag, i)
            ON u.i = a.i
          JOIN unnest(p_grunn_datoer) WITH ORDINALITY AS d(dato, i)
            ON d.i = a.i
        RETURNING 1)
    SELECT gg.n INTO v_n
      FROM (SELECT count(*)::int AS n FROM g) gg
      CROSS JOIN (SELECT count(*) FROM f) ff(n);

    -- ANDRE GJERDE: antallet SKREVNE grunner måles mot det lovede.
    -- Vaktene over er argumenter om hva som ikke KAN skje; dette er en
    -- måling av hva som FAKTISK skjedde. Slår den til, rulles hele
    -- setningen tilbake og forslaget finnes ikke — som er hele
    -- poenget med at forslaget og grunnene deler én setning.
    IF v_n IS DISTINCT FROM cardinality(p_grunn_arter) THEN
        RAISE EXCEPTION 'm52_avgi_forslag: % grunner ble lovet, %'
            ' ble skrevet', cardinality(p_grunn_arter), v_n
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    PERFORM public.m52_evidens(p_tenant, p_vare_id,
        'tollforslag_avgitt', p_aktor, jsonb_build_object(
            'forslag_id', p_forslag_id::text,
            'system', v_sys, 'versjon', v_ver, 'kode', v_kode,
            'sikkerhet', p_sikkerhet, 'terskel_brukt', v_terskel,
            'antall_grunner', v_n));

    RETURN QUERY
    SELECT t.sikkerhet, t.terskel_brukt, t.over_terskel, v_n,
           t.system_ved_forslag, t.versjon_ved_forslag,
           t.kode_ved_forslag
      FROM public.tollforslag t
     WHERE t.tenant = p_tenant AND t.forslag_id = p_forslag_id;
END $$;
REVOKE ALL ON FUNCTION m52_avgi_forslag(
    TEXT, UUID, UUID, UUID, INT, TEXT[], TEXT[], TEXT[], DATE[], TEXT)
    FROM PUBLIC;


-- «KLAR TIL DEKLARERING» — EN TILSTAND HOS OSS.
--
-- Ikke en deklarasjon, ikke en signatur og ikke en utsending. Samme
-- figur som M-46s «klar til gjennomgang» (118), M-51s ferdigstilte
-- estimat (119) og M-54s «klar til signering» (121).
--
-- DØRA NEKTER PÅ ET FORSLAG UNDER TERSKEL. Det kan riktignok ikke
-- oppstå gjennom `m52_avgi_forslag` — men terskelen kan HEVES etterpå,
-- og da står forslaget der med en sikkerhet tenanten ikke lenger
-- godtar. Å merke det klart ville vært å be et menneske deklarere på
-- et grunnlag hen selv har forkastet.
CREATE FUNCTION m52_merk_klart(
    p_tenant TEXT, p_forslag_id UUID, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_klart BOOLEAN; v_vare_id UUID; v_sikkerhet INT;
    v_terskel_naa INT; v_gyldig BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm52_merk_klart');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    -- LÅS FORSLAGET, LES KLARMERKET ETTERPÅ. Lesningen over en lås er
    -- fra transaksjonens snapshot; et parallelt `m52_merk_klart` som
    -- committer mens vi venter ville vært usynlig (klynge 6s lærdom,
    -- skrevet feil fem ganger der).
    PERFORM 1 FROM public.tollforslag
     WHERE tenant = p_tenant AND forslag_id = p_forslag_id
     FOR UPDATE;
    SELECT t.klar_til_deklarering, t.vare_id, t.sikkerhet
      INTO v_klart, v_vare_id, v_sikkerhet
      FROM public.tollforslag t
     WHERE t.tenant = p_tenant AND t.forslag_id = p_forslag_id;
    IF v_vare_id IS NULL THEN
        RAISE EXCEPTION 'm52_merk_klart: ukjent forslag %',
            p_forslag_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_klart THEN
        RAISE EXCEPTION 'm52_merk_klart: forslaget % er alt merket'
            ' klart', p_forslag_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- TERSKELEN SLIK DEN ER NÅ, ikke slik den var da forslaget kom.
    SELECT k.sikkerhetsterskel INTO v_terskel_naa
      FROM public.tollkrav k WHERE k.tenant = p_tenant;
    IF v_terskel_naa IS NOT NULL AND v_sikkerhet < v_terskel_naa THEN
        RAISE EXCEPTION 'm52_merk_klart: forslaget har % prosent'
            ' sikkerhet, og tenantens terskel er nå % prosent. Å'
            ' merke det klart ville vært å be et menneske deklarere'
            ' på et grunnlag tenanten selv har forkastet',
            v_sikkerhet, v_terskel_naa
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- …OG NOMENKLATUREN MÅ FORTSATT GJELDE. En kode som er avviklet
    -- siden forslaget ble avgitt, er ikke klar til noe.
    SELECT public.m52_nomenklatur_gyldig(n.gyldig_fra, n.gyldig_til)
      INTO v_gyldig
      FROM public.tollforslag t
      JOIN public.nomenklatur n
        ON n.tenant = t.tenant
       AND n.nomenklatur_id = t.nomenklatur_id
     WHERE t.tenant = p_tenant AND t.forslag_id = p_forslag_id;
    IF NOT v_gyldig THEN
        RAISE EXCEPTION 'm52_merk_klart: nomenklaturen forslaget'
            ' hviler på er ikke gyldig i dag. Klassifiser varen på'
            ' nytt mot en gjeldende versjon'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.tollforslag
       SET klar_til_deklarering = true, klar_ts = now(),
           klar_av = p_aktor
     WHERE tenant = p_tenant AND forslag_id = p_forslag_id;

    PERFORM public.m52_evidens(p_tenant, v_vare_id,
        'tollforslag_klart', p_aktor, jsonb_build_object(
            'forslag_id', p_forslag_id::text,
            'sikkerhet', v_sikkerhet,
            'terskel_naa', v_terskel_naa));
    RETURN v_sikkerhet;
END $$;
REVOKE ALL ON FUNCTION m52_merk_klart(TEXT, UUID, TEXT) FROM PUBLIC;


-- FUNNET LUKKES, MEN IKKE `forslag_mot_utlopt_nomenklatur`.
--
-- Det funnet forsvinner når varen klassifiseres på nytt mot en gyldig
-- nomenklatur — og det er en HANDLING, ikke en mening.
CREATE FUNCTION m52_lukk_funn(
    p_tenant TEXT, p_funn_id UUID, p_notat TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_type TEXT; v_apen BOOLEAN; v_vare_id UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm52_lukk_funn');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    PERFORM 1 FROM public.tollfunn
     WHERE tenant = p_tenant AND funn_id = p_funn_id FOR UPDATE;
    SELECT f.funntype, f.apen, f.vare_id
      INTO v_type, v_apen, v_vare_id
      FROM public.tollfunn f
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;
    IF v_type IS NULL THEN
        RAISE EXCEPTION 'm52_lukk_funn: ukjent funn %', p_funn_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_apen THEN
        RAISE EXCEPTION 'm52_lukk_funn: funnet % er alt lukket',
            p_funn_id USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_type = 'forslag_mot_utlopt_nomenklatur' THEN
        RAISE EXCEPTION 'm52_lukk_funn: % kan ikke lukkes. Forslaget'
            ' hviler på en nomenklatur som siden er avviklet, og'
            ' funnet forsvinner når varen klassifiseres på nytt mot'
            ' en gyldig versjon — det er en handling, ikke en mening',
            v_type USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.tollfunn
       SET apen = false, lukket_ts = now(), lukket_av = p_aktor,
           lukkenotat = btrim(p_notat)
     WHERE tenant = p_tenant AND funn_id = p_funn_id;

    PERFORM public.m52_evidens(p_tenant, v_vare_id,
        'tollfunn_lukket', p_aktor,
        jsonb_build_object('funntype', v_type));
END $$;
REVOKE ALL ON FUNCTION m52_lukk_funn(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Lesedørene.
-- ------------------------------------------------------------

CREATE FUNCTION m52_kravene(p_tenant TEXT)
RETURNS TABLE (sikkerhetsterskel INT, utlopsvarsel_dogn INT,
               forslagsfrist_dogn INT, versjon INT,
               oppdatert TIMESTAMPTZ, oppdatert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm52_kravene');
    RETURN QUERY
    SELECT k.sikkerhetsterskel, k.utlopsvarsel_dogn,
           k.forslagsfrist_dogn, k.versjon, k.oppdatert,
           k.oppdatert_av
      FROM public.tollkrav k WHERE k.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m52_kravene(TEXT) FROM PUBLIC;


-- NOMENKLATURENE, MED GYLDIGHETEN REGNET I BASEN. To lesere skal ikke
-- kunne komme til hver sin konklusjon om hvorvidt regelverket vi
-- klassifiserer mot fortsatt gjelder.
CREATE FUNCTION m52_nomenklaturene(p_tenant TEXT, p_grense INT)
RETURNS TABLE (nomenklatur_id UUID, system TEXT, versjon TEXT,
               gyldig_fra DATE, gyldig_til DATE, gyldig_naa BOOLEAN,
               dogn_til_utlop INT, innhold_sha256 TEXT,
               kilde_url TEXT, registrert TIMESTAMPTZ,
               registrert_av TEXT, antall_varenummer BIGINT,
               antall_forslag BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm52_nomenklaturene');
    IF p_grense IS NULL OR p_grense < 1 OR p_grense > 500 THEN
        RAISE EXCEPTION 'm52_nomenklaturene: grensen må være'
            ' 1..500 (%)', p_grense
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    SELECT n.nomenklatur_id, n.system, n.versjon, n.gyldig_fra,
           n.gyldig_til,
           public.m52_nomenklatur_gyldig(n.gyldig_fra, n.gyldig_til),
           CASE WHEN n.gyldig_til IS NULL THEN NULL
                ELSE (n.gyldig_til - current_date) END,
           n.innhold_sha256, n.kilde_url, n.registrert,
           n.registrert_av,
           (SELECT count(*) FROM public.varenummer v
             WHERE v.tenant = n.tenant
               AND v.nomenklatur_id = n.nomenklatur_id),
           (SELECT count(*) FROM public.tollforslag t
             WHERE t.tenant = n.tenant
               AND t.nomenklatur_id = n.nomenklatur_id)
      FROM public.nomenklatur n
     WHERE n.tenant = p_tenant
     ORDER BY n.system, n.gyldig_fra DESC
     LIMIT p_grense;
END $$;
REVOKE ALL ON FUNCTION m52_nomenklaturene(TEXT, INT) FROM PUBLIC;


CREATE FUNCTION m52_varenumrene(p_tenant TEXT, p_nomenklatur_id UUID,
                                p_grense INT)
RETURNS TABLE (varenummer_id UUID, kode TEXT, tekst TEXT,
               tollsats_bp INT, registrert TIMESTAMPTZ,
               registrert_av TEXT, brukt_i_forslag BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm52_varenumrene');
    IF p_grense IS NULL OR p_grense < 1 OR p_grense > 2000 THEN
        RAISE EXCEPTION 'm52_varenumrene: grensen må være 1..2000 (%)',
            p_grense USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    SELECT v.varenummer_id, v.kode, v.tekst, v.tollsats_bp,
           v.registrert, v.registrert_av,
           (SELECT count(*) FROM public.tollforslag t
             WHERE t.tenant = v.tenant
               AND t.varenummer_id = v.varenummer_id)
      FROM public.varenummer v
     WHERE v.tenant = p_tenant
       AND v.nomenklatur_id = p_nomenklatur_id
     ORDER BY v.kode
     LIMIT p_grense;
END $$;
REVOKE ALL ON FUNCTION m52_varenumrene(TEXT, UUID, INT) FROM PUBLIC;


-- VARENE, MED NYESTE FORSLAG PÅ SAMME RAD.
--
-- NOMENKLATURVERSJONEN OG ANTALL GRUNNER STÅR HER, ikke bak et ekstra
-- oppslag: en kode uten versjonen den hviler på, og uten hvor mange
-- grunner den har, er nettopp det modulen finnes for å unngå.
CREATE FUNCTION m52_varene(p_tenant TEXT, p_grense INT)
RETURNS TABLE (vare_id UUID, ekstern_ref TEXT, beskrivelse TEXT,
               materiale TEXT, bruk TEXT, opprinnelsesland TEXT,
               registrert TIMESTAMPTZ, registrert_av TEXT,
               forslag_id UUID, system TEXT, versjon TEXT,
               kode TEXT, tollsats_bp INT, sikkerhet INT,
               terskel_brukt INT, over_terskel BOOLEAN,
               antall_grunner BIGINT, klar_til_deklarering BOOLEAN,
               klar_ts TIMESTAMPTZ, klar_av TEXT,
               avgitt TIMESTAMPTZ, nomenklatur_gyldig_naa BOOLEAN,
               antall_forslag BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm52_varene');
    IF p_grense IS NULL OR p_grense < 1 OR p_grense > 500 THEN
        RAISE EXCEPTION 'm52_varene: grensen må være 1..500 (%)',
            p_grense USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    SELECT v.vare_id, v.ekstern_ref, v.beskrivelse, v.materiale,
           v.bruk, v.opprinnelsesland, v.registrert, v.registrert_av,
           s.forslag_id, s.system_ved_forslag, s.versjon_ved_forslag,
           s.kode_ved_forslag, s.tollsats_bp, s.sikkerhet,
           s.terskel_brukt, s.over_terskel, s.antall_grunner,
           s.klar_til_deklarering, s.klar_ts, s.klar_av, s.avgitt,
           s.gyldig_naa, coalesce(a.antall, 0)
      FROM public.tollvare v
      LEFT JOIN LATERAL (
           -- NYESTE FORSLAG. Forslagene er append-only og versjonerte:
           -- en vare kan ha flere, og en telling som tok alle ville
           -- sagt at det finnes flere klassifiseringer enn det gjør
           -- (119s målte lærdom).
           SELECT tt.forslag_id, tt.system_ved_forslag,
                  tt.versjon_ved_forslag, tt.kode_ved_forslag,
                  vn.tollsats_bp, tt.sikkerhet, tt.terskel_brukt,
                  tt.over_terskel, tt.klar_til_deklarering,
                  tt.klar_ts, tt.klar_av, tt.avgitt,
                  public.m52_nomenklatur_gyldig(nn.gyldig_fra,
                                                nn.gyldig_til)
                      AS gyldig_naa,
                  (SELECT count(*) FROM public.forslagsgrunn gg
                    WHERE gg.tenant = tt.tenant
                      AND gg.forslag_id = tt.forslag_id)
                      AS antall_grunner
             FROM public.tollforslag tt
             JOIN public.nomenklatur nn
               ON nn.tenant = tt.tenant
              AND nn.nomenklatur_id = tt.nomenklatur_id
             JOIN public.varenummer vn
               ON vn.tenant = tt.tenant
              AND vn.varenummer_id = tt.varenummer_id
            WHERE tt.tenant = v.tenant AND tt.vare_id = v.vare_id
            ORDER BY tt.avgitt DESC, tt.forslag_id DESC
            LIMIT 1) s ON true
      LEFT JOIN LATERAL (
           SELECT count(*) AS antall FROM public.tollforslag t2
            WHERE t2.tenant = v.tenant AND t2.vare_id = v.vare_id)
           a ON true
     WHERE v.tenant = p_tenant
     ORDER BY (s.forslag_id IS NULL) DESC,
              coalesce(s.over_terskel, false), v.ekstern_ref
     LIMIT p_grense;
END $$;
REVOKE ALL ON FUNCTION m52_varene(TEXT, INT) FROM PUBLIC;


-- GRUNNENE BAK ETT FORSLAG. Rekkefølgen er RETTSKILDENES: en bindende
-- forhåndsuttalelse veier tyngre enn en egen tidligere klassifisering,
-- som veier tyngre enn en tekstlikhet. Den som leser skal se det
-- tyngste først.
CREATE FUNCTION m52_grunnene(p_tenant TEXT, p_forslag_id UUID)
RETURNS TABLE (grunn_id UUID, art TEXT, henvisning TEXT,
               utdrag TEXT, grunn_dato DATE,
               registrert TIMESTAMPTZ, registrert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm52_grunnene');
    RETURN QUERY
    SELECT g.grunn_id, g.art, g.henvisning, g.utdrag, g.grunn_dato,
           g.registrert, g.registrert_av
      FROM public.forslagsgrunn g
     WHERE g.tenant = p_tenant AND g.forslag_id = p_forslag_id
     ORDER BY array_position(
                  ARRAY['bindende_forhandsuttalelse',
                        'tidligere_klassifisering',
                        'alminnelig_fortolkningsregel',
                        'nomenklaturtekst', 'faglig_vurdering'],
                  g.art),
              g.grunn_dato DESC NULLS LAST, g.henvisning;
END $$;
REVOKE ALL ON FUNCTION m52_grunnene(TEXT, UUID) FROM PUBLIC;


-- ALLE FORSLAGENE PÅ ÉN VARE, i rekkefølge. En ny nomenklaturversjon
-- gir en ny rad, og HELE rekken skal kunne leses: det er der «hva var
-- riktig kode den gangen» faktisk står.
CREATE FUNCTION m52_forslagene(p_tenant TEXT, p_vare_id UUID)
RETURNS TABLE (forslag_id UUID, system TEXT, versjon TEXT,
               kode TEXT, sikkerhet INT, terskel_brukt INT,
               over_terskel BOOLEAN, antall_grunner BIGINT,
               nomenklatur_gyldig_naa BOOLEAN,
               klar_til_deklarering BOOLEAN, avgitt TIMESTAMPTZ,
               avgitt_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm52_forslagene');
    RETURN QUERY
    SELECT t.forslag_id, t.system_ved_forslag, t.versjon_ved_forslag,
           t.kode_ved_forslag, t.sikkerhet, t.terskel_brukt,
           t.over_terskel,
           (SELECT count(*) FROM public.forslagsgrunn g
             WHERE g.tenant = t.tenant
               AND g.forslag_id = t.forslag_id),
           public.m52_nomenklatur_gyldig(n.gyldig_fra, n.gyldig_til),
           t.klar_til_deklarering, t.avgitt, t.avgitt_av
      FROM public.tollforslag t
      JOIN public.nomenklatur n
        ON n.tenant = t.tenant
       AND n.nomenklatur_id = t.nomenklatur_id
     WHERE t.tenant = p_tenant AND t.vare_id = p_vare_id
     ORDER BY t.avgitt DESC, t.forslag_id DESC;
END $$;
REVOKE ALL ON FUNCTION m52_forslagene(TEXT, UUID) FROM PUBLIC;


CREATE FUNCTION m52_funnene(p_tenant TEXT, p_bare_apne BOOLEAN)
RETURNS TABLE (funn_id UUID, funntype TEXT, nomenklatur_id UUID,
               vare_id UUID, forslag_id UUID, system TEXT,
               nomenklaturversjon TEXT, ekstern_ref TEXT,
               over_grense INT, detalj TEXT, sikkerhet INT,
               terskel_brukt INT, kravversjon INT,
               forst_sett TIMESTAMPTZ, sist_sett_sveip TIMESTAMPTZ,
               apen BOOLEAN, lukket_ts TIMESTAMPTZ, lukket_av TEXT,
               lukkenotat TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm52_funnene');
    RETURN QUERY
    SELECT f.funn_id, f.funntype, f.nomenklatur_id, f.vare_id,
           f.forslag_id, coalesce(n.system, fn.system),
           coalesce(n.versjon, fn.versjon),
           coalesce(v.ekstern_ref, fv.ekstern_ref), f.over_grense,
           f.detalj, f.sikkerhet, f.terskel_brukt, f.kravversjon,
           f.forst_sett, f.sist_sett_sveip, f.apen, f.lukket_ts,
           f.lukket_av, f.lukkenotat
      FROM public.tollfunn f
      LEFT JOIN public.nomenklatur n
        ON n.tenant = f.tenant AND n.nomenklatur_id = f.nomenklatur_id
      LEFT JOIN public.tollvare v
        ON v.tenant = f.tenant AND v.vare_id = f.vare_id
      LEFT JOIN public.tollforslag t
        ON t.tenant = f.tenant AND t.forslag_id = f.forslag_id
      LEFT JOIN public.nomenklatur fn
        ON fn.tenant = t.tenant
       AND fn.nomenklatur_id = t.nomenklatur_id
      LEFT JOIN public.tollvare fv
        ON fv.tenant = t.tenant AND fv.vare_id = t.vare_id
     WHERE f.tenant = p_tenant
       AND (NOT coalesce(p_bare_apne, true) OR f.apen)
     ORDER BY f.apen DESC, f.funntype, f.forst_sett;
END $$;
REVOKE ALL ON FUNCTION m52_funnene(TEXT, BOOLEAN) FROM PUBLIC;


-- SAMMENDRAGET. Tallene flaten åpner med.
CREATE FUNCTION m52_tollstatus(p_tenant TEXT)
RETURNS TABLE (nomenklaturer BIGINT, gyldige BIGINT, utlopte BIGINT,
               varenummer BIGINT, varer BIGINT, klassifiserte BIGINT,
               uklassifiserte BIGINT, over_terskel BIGINT,
               klare BIGINT, forslag_under_utlopt BIGINT,
               apne_funn BIGINT, har_krav BOOLEAN, terskel INT,
               kravversjon INT, vist BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm52_tollstatus');
    RETURN QUERY
    WITH nyeste AS (
        -- NYESTE FORSLAG PER VARE, ÉN GANG (119s lærdom).
        SELECT v.vare_id, s.forslag_id, s.over_terskel,
               s.klar_til_deklarering, s.gyldig_naa
          FROM public.tollvare v
          LEFT JOIN LATERAL (
               SELECT tt.forslag_id, tt.over_terskel,
                      tt.klar_til_deklarering,
                      public.m52_nomenklatur_gyldig(nn.gyldig_fra,
                                                    nn.gyldig_til)
                          AS gyldig_naa
                 FROM public.tollforslag tt
                 JOIN public.nomenklatur nn
                   ON nn.tenant = tt.tenant
                  AND nn.nomenklatur_id = tt.nomenklatur_id
                WHERE tt.tenant = v.tenant AND tt.vare_id = v.vare_id
                ORDER BY tt.avgitt DESC, tt.forslag_id DESC
                LIMIT 1) s ON true
         WHERE v.tenant = p_tenant)
    SELECT (SELECT count(*) FROM public.nomenklatur n
             WHERE n.tenant = p_tenant),
           (SELECT count(*) FROM public.nomenklatur n
             WHERE n.tenant = p_tenant
               AND public.m52_nomenklatur_gyldig(n.gyldig_fra,
                                                 n.gyldig_til)),
           (SELECT count(*) FROM public.nomenklatur n
             WHERE n.tenant = p_tenant
               AND NOT public.m52_nomenklatur_gyldig(n.gyldig_fra,
                                                     n.gyldig_til)),
           (SELECT count(*) FROM public.varenummer v
             WHERE v.tenant = p_tenant),
           (SELECT count(*) FROM nyeste),
           (SELECT count(*) FROM nyeste n
             WHERE n.forslag_id IS NOT NULL),
           -- EN VARE INGEN HAR KLASSIFISERT FORTOLLES PÅ GJETNING
           -- den dagen den skal ut.
           (SELECT count(*) FROM nyeste n
             WHERE n.forslag_id IS NULL),
           (SELECT count(*) FROM nyeste n WHERE n.over_terskel),
           (SELECT count(*) FROM nyeste n
             WHERE n.klar_til_deklarering),
           -- DET ENE TALLET KLYNGEN FINNES FOR: koder som hviler på
           -- et regelverk som siden er avviklet.
           (SELECT count(*) FROM nyeste n
             WHERE n.forslag_id IS NOT NULL AND NOT n.gyldig_naa),
           (SELECT count(*) FROM public.tollfunn f
             WHERE f.tenant = p_tenant AND f.apen),
           EXISTS (SELECT 1 FROM public.tollkrav k
                    WHERE k.tenant = p_tenant),
           (SELECT k.sikkerhetsterskel FROM public.tollkrav k
             WHERE k.tenant = p_tenant),
           (SELECT k.versjon FROM public.tollkrav k
             WHERE k.tenant = p_tenant),
           (SELECT least(count(*), 200) FROM public.tollvare v
             WHERE v.tenant = p_tenant);
END $$;
REVOKE ALL ON FUNCTION m52_tollstatus(TEXT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 5. Sveipen. Kryss-tenant, egen LOGIN-rolle, egen timer.
-- ------------------------------------------------------------

-- NATTENS ENESTE JOBB: SE ETTER REGELVERK SOM ER AVVIKLET, OG KODER
-- SOM HVILER PÅ DEM.
--
-- SVEIPEN KLASSIFISERER IKKE. En automatisk klassifisering ville avgitt
-- rettslige påstander om hva varer ER, om natten, uten at noen ba om
-- det — og boten treffer kunden. `m52_avgi_forslag` kalles av et
-- menneske gjennom flaten, aldri herfra.
--
-- OG DEN MERKER INGENTING KLART. «Klar til deklarering» er en tilstand
-- et menneske setter.
--
-- SVEIPEN HENTER HELLER INGEN NOMENKLATUR. Tolltariffen er
-- myndighetens, og en modul som lastet ned den nyeste versjonen selv
-- ville tatt ansvaret for at NØYAKTIG den er den gjeldende.
--
-- TENANTLISTA MATERIALISERES FØR LØKKA (112s lærdom, 116–121).
CREATE FUNCTION m52_sveip_tollkode(p_maks_tenanter INT)
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
        RAISE EXCEPTION 'm52_sveip_tollkode: maks_tenanter må være'
            ' minst 1 (%)', p_maks_tenanter
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.tenant', '', true);
    -- TENANTLISTA ER BEGGE REGISTRENE, IKKE BARE NOMENKLATURENE
    -- (CodeRabbit). En tenant som har registrert varer, men ennå ingen
    -- nomenklatur, er nettopp den som trenger `vare_uten_forslag` og
    -- `ingen_krav` mest — og med nomenklaturtabellen alene ville
    -- sveipen hoppet rett over ham, hver natt, uten et ord.
    SELECT array_agg(DISTINCT t ORDER BY t) INTO v_tenanter
      FROM (SELECT n.tenant AS t FROM public.nomenklatur n
            UNION
            SELECT v.tenant FROM public.tollvare v) s;
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

        -- NOMENKLATURNIVÅ.
        WITH krav AS (
            SELECT k.utlopsvarsel_dogn, k.versjon
              FROM public.tollkrav k WHERE k.tenant = v_t),
        kand AS (
            SELECT n.nomenklatur_id, 'ingen_krav'::text AS funntype,
                   NULL::int AS over_grense,
                   'sikkerhetsterskelen er tenantens og er ikke'
                   || ' satt'::text AS detalj,
                   NULL::int AS kravversjon
              FROM public.nomenklatur n
             WHERE n.tenant = v_t AND NOT EXISTS (SELECT 1 FROM krav)

            UNION ALL
            -- ET AVVIKLET REGELVERK UTEN EN GYLDIG ETTERFØLGER.
            --
            -- IKKE ETHVERT AVVIKLET SETT (121s lærdom): et arkivert
            -- HS 2017 ved siden av et gyldig HS 2022 er historikk, og
            -- et funn på det ville vært støy hver natt for alltid.
            --
            -- INGEN `CROSS JOIN krav` (121s CodeRabbit-funn): funnet
            -- avhenger ikke av en terskel, og en krysskobling mot en
            -- TOM `krav` ville gjort det usynlig for nettopp den
            -- tenanten som ikke har konfigurert noe.
            SELECT n.nomenklatur_id, 'nomenklatur_utlopt',
                   (current_date - n.gyldig_til),
                   n.system || ' ' || n.versjon,
                   (SELECT versjon FROM krav)
              FROM public.nomenklatur n
             WHERE n.tenant = v_t AND n.gyldig_til IS NOT NULL
               AND n.gyldig_til < current_date
               AND NOT EXISTS (
                   SELECT 1 FROM public.nomenklatur n2
                    WHERE n2.tenant = v_t AND n2.system = n.system
                      AND public.m52_nomenklatur_gyldig(
                              n2.gyldig_fra, n2.gyldig_til))

            UNION ALL
            SELECT n.nomenklatur_id, 'nomenklatur_utloper_snart',
                   (n.gyldig_til - current_date),
                   n.system || ' ' || n.versjon, k.versjon
              FROM public.nomenklatur n CROSS JOIN krav k
             WHERE n.tenant = v_t AND n.gyldig_til IS NOT NULL
               AND n.gyldig_til >= current_date
               AND n.gyldig_til <= current_date
                   + make_interval(days => k.utlopsvarsel_dogn)
        ),
        skrevet AS (
            INSERT INTO public.tollfunn
                (tenant, funn_id, nomenklatur_id, funntype,
                 over_grense, detalj, kravversjon)
            SELECT v_t, gen_random_uuid(), k.nomenklatur_id,
                   k.funntype, k.over_grense, k.detalj, k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, nomenklatur_id, funntype)
                WHERE nomenklatur_id IS NOT NULL
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
        -- `INTO` SETTER variabelen; akkumuleringen står her (112s
        -- retting, gjentatt i 116–121).
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);

        -- VARENIVÅ: en vare ingen har klassifisert.
        --
        -- `CROSS JOIN krav` ER RIKTIG HER, men bare fordi det andre
        -- leddet finnes: uten en frist er «for lenge siden» ikke en
        -- målbar påstand, og en innebygd frist ville vært en fullmakt
        -- modulen ga seg selv. Tenanten UTEN krav får i stedet
        -- `ingen_krav` — og etter CodeRabbits funn får han det også
        -- når han ikke har en eneste nomenklatur å henge det på.
        WITH krav AS (
            SELECT k.forslagsfrist_dogn, k.versjon
              FROM public.tollkrav k WHERE k.tenant = v_t),
        kand AS (
            SELECT v.vare_id, 'vare_uten_forslag'::text AS funntype,
                   (current_date - v.registrert::date) AS over_grense,
                   NULL::text AS detalj,
                   k.versjon AS kravversjon
              FROM public.tollvare v CROSS JOIN krav k
             WHERE v.tenant = v_t
               AND NOT EXISTS (SELECT 1 FROM public.tollforslag t
                                WHERE t.tenant = v_t
                                  AND t.vare_id = v.vare_id)
               AND v.registrert < now()
                   - make_interval(days => k.forslagsfrist_dogn)

            UNION ALL
            -- VARER, MEN INGENTING Å MÅLE DEM MOT. Ett funn, festet
            -- til den ELDSTE varen: nøkkelen må finnes (hvert funn har
            -- én), og den eldste er den samme natt etter natt, så
            -- sveipen forblir idempotent.
            SELECT v.vare_id, 'ingen_krav',
                   (current_date - v.registrert::date),
                   'verken sikkerhetsterskel eller nomenklatur er'
                   || ' registrert'::text,
                   NULL::int
              FROM public.tollvare v
             WHERE v.tenant = v_t
               AND NOT EXISTS (SELECT 1 FROM krav)
               AND NOT EXISTS (SELECT 1 FROM public.nomenklatur n
                                WHERE n.tenant = v_t)
               AND v.registrert = (SELECT min(v2.registrert)
                                     FROM public.tollvare v2
                                    WHERE v2.tenant = v_t)
        ),
        skrevet AS (
            INSERT INTO public.tollfunn
                (tenant, funn_id, vare_id, funntype, over_grense,
                 detalj, kravversjon)
            SELECT v_t, gen_random_uuid(), k.vare_id,
                   k.funntype, k.over_grense, k.detalj, k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, vare_id, funntype)
                WHERE vare_id IS NOT NULL
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

        -- FORSLAGSNIVÅ. KLYNGENS EGET FUNN.
        WITH krav AS (
            SELECT k.sikkerhetsterskel, k.versjon
              FROM public.tollkrav k WHERE k.tenant = v_t),
        nyeste AS (
            SELECT DISTINCT ON (t.vare_id)
                   t.forslag_id, t.vare_id, t.nomenklatur_id,
                   t.sikkerhet, t.klar_til_deklarering
              FROM public.tollforslag t
             WHERE t.tenant = v_t
             ORDER BY t.vare_id, t.avgitt DESC, t.forslag_id DESC),
        kand AS (
            -- KODEN HVILER PÅ ET REGELVERK SOM SIDEN ER AVVIKLET.
            -- Kan ikke lukkes av et menneske: den forsvinner når
            -- varen klassifiseres på nytt mot en gyldig versjon.
            SELECT n.forslag_id,
                   'forslag_mot_utlopt_nomenklatur'::text AS funntype,
                   (current_date - nn.gyldig_til) AS over_grense,
                   nn.system || ' ' || nn.versjon AS detalj,
                   n.sikkerhet, NULL::int AS terskel,
                   (SELECT versjon FROM krav) AS kravversjon
              FROM nyeste n
              JOIN public.nomenklatur nn
                ON nn.tenant = v_t
               AND nn.nomenklatur_id = n.nomenklatur_id
             WHERE NOT public.m52_nomenklatur_gyldig(nn.gyldig_fra,
                                                     nn.gyldig_til)

            UNION ALL
            -- TERSKELEN ER HEVET SIDEN FORSLAGET BLE AVGITT. Koden
            -- står da med en sikkerhet tenanten ikke lenger godtar —
            -- og `m52_merk_klart` nekter, så funnet er beskjeden om
            -- hvorfor.
            SELECT n.forslag_id, 'forslag_under_terskel',
                   (k.sikkerhetsterskel - n.sikkerhet), NULL::text,
                   n.sikkerhet, k.sikkerhetsterskel, k.versjon
              FROM nyeste n CROSS JOIN krav k
             WHERE n.sikkerhet < k.sikkerhetsterskel

            UNION ALL
            -- ET FORSLAG OVER TERSKEL SOM INGEN HAR MERKET KLART.
            SELECT n.forslag_id, 'forslag_ikke_klart', NULL::int,
                   NULL::text, n.sikkerhet, k.sikkerhetsterskel,
                   k.versjon
              FROM nyeste n CROSS JOIN krav k
             WHERE n.sikkerhet >= k.sikkerhetsterskel
               AND NOT n.klar_til_deklarering
        ),
        skrevet AS (
            INSERT INTO public.tollfunn
                (tenant, funn_id, forslag_id, funntype, over_grense,
                 detalj, sikkerhet, terskel_brukt, kravversjon)
            SELECT v_t, gen_random_uuid(), k.forslag_id, k.funntype,
                   k.over_grense, k.detalj, k.sikkerhet, k.terskel,
                   k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, forslag_id, funntype)
                WHERE forslag_id IS NOT NULL
            DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                detalj = EXCLUDED.detalj,
                sikkerhet = EXCLUDED.sikkerhet,
                terskel_brukt = EXCLUDED.terskel_brukt,
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

        PERFORM set_config('disponit.tenant', '', true);
    END LOOP;

    -- LUKKINGEN I EGEN RUNDE (117–121s form). Et funn som ikke lenger
    -- er sant skal ikke bli stående — men det lukkes av at TILSTANDEN
    -- er borte, ikke av at noen trykket.
    --
    -- `forslag_mot_utlopt_nomenklatur` LUKKES OGSÅ HER, og bare her:
    -- når varen er klassifisert på nytt mot en gyldig nomenklatur, er
    -- tilstanden borte. Døra `m52_lukk_funn` nekter fremdeles.
    FOREACH v_t IN ARRAY v_tenanter
    LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        WITH krav AS (
            SELECT k.sikkerhetsterskel, k.utlopsvarsel_dogn,
                   k.forslagsfrist_dogn
              FROM public.tollkrav k WHERE k.tenant = v_t),
        nyeste AS (
            SELECT DISTINCT ON (t.vare_id)
                   t.forslag_id, t.vare_id, t.nomenklatur_id,
                   t.sikkerhet, t.klar_til_deklarering
              FROM public.tollforslag t
             WHERE t.tenant = v_t
             ORDER BY t.vare_id, t.avgitt DESC, t.forslag_id DESC),
        fortsatt AS (
            SELECT n.nomenklatur_id, NULL::uuid AS vare_id,
                   NULL::uuid AS forslag_id,
                   'ingen_krav'::text AS funntype
              FROM public.nomenklatur n
             WHERE n.tenant = v_t AND NOT EXISTS (SELECT 1 FROM krav)
            UNION ALL
            SELECT n.nomenklatur_id, NULL, NULL,
                   'nomenklatur_utlopt'
              FROM public.nomenklatur n
             WHERE n.tenant = v_t AND n.gyldig_til IS NOT NULL
               AND n.gyldig_til < current_date
               AND NOT EXISTS (
                   SELECT 1 FROM public.nomenklatur n2
                    WHERE n2.tenant = v_t AND n2.system = n.system
                      AND public.m52_nomenklatur_gyldig(
                              n2.gyldig_fra, n2.gyldig_til))
            UNION ALL
            SELECT n.nomenklatur_id, NULL, NULL,
                   'nomenklatur_utloper_snart'
              FROM public.nomenklatur n CROSS JOIN krav k
             WHERE n.tenant = v_t AND n.gyldig_til IS NOT NULL
               AND n.gyldig_til >= current_date
               AND n.gyldig_til <= current_date
                   + make_interval(days => k.utlopsvarsel_dogn)
            UNION ALL
            SELECT NULL, v.vare_id, NULL, 'vare_uten_forslag'
              FROM public.tollvare v CROSS JOIN krav k
             WHERE v.tenant = v_t
               AND NOT EXISTS (SELECT 1 FROM public.tollforslag t
                                WHERE t.tenant = v_t
                                  AND t.vare_id = v.vare_id)
               AND v.registrert < now()
                   - make_interval(days => k.forslagsfrist_dogn)
            UNION ALL
            -- …og det varefestede `ingen_krav` lukkes av seg selv den
            -- dagen tenanten registrerer et krav ELLER en nomenklatur.
            SELECT NULL, v.vare_id, NULL, 'ingen_krav'
              FROM public.tollvare v
             WHERE v.tenant = v_t
               AND NOT EXISTS (SELECT 1 FROM krav)
               AND NOT EXISTS (SELECT 1 FROM public.nomenklatur n
                                WHERE n.tenant = v_t)
               AND v.registrert = (SELECT min(v2.registrert)
                                     FROM public.tollvare v2
                                    WHERE v2.tenant = v_t)
            UNION ALL
            SELECT NULL, NULL, n.forslag_id,
                   'forslag_mot_utlopt_nomenklatur'
              FROM nyeste n
              JOIN public.nomenklatur nn
                ON nn.tenant = v_t
               AND nn.nomenklatur_id = n.nomenklatur_id
             WHERE NOT public.m52_nomenklatur_gyldig(nn.gyldig_fra,
                                                     nn.gyldig_til)
            UNION ALL
            SELECT NULL, NULL, n.forslag_id, 'forslag_under_terskel'
              FROM nyeste n CROSS JOIN krav k
             WHERE n.sikkerhet < k.sikkerhetsterskel
            UNION ALL
            SELECT NULL, NULL, n.forslag_id, 'forslag_ikke_klart'
              FROM nyeste n CROSS JOIN krav k
             WHERE n.sikkerhet >= k.sikkerhetsterskel
               AND NOT n.klar_til_deklarering
        )
        UPDATE public.tollfunn f
           SET apen = false, lukket_ts = now(),
               lukket_av = 'm52_sveip_tollkode',
               lukkenotat = 'tilstanden er ikke lenger til stede'
         WHERE f.tenant = v_t AND f.apen
           AND NOT EXISTS (
               SELECT 1 FROM fortsatt s
                WHERE s.funntype = f.funntype
                  AND s.nomenklatur_id IS NOT DISTINCT FROM
                      f.nomenklatur_id
                  AND s.vare_id IS NOT DISTINCT FROM f.vare_id
                  AND s.forslag_id IS NOT DISTINCT FROM f.forslag_id);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        v_lukket := v_lukket + coalesce(v_n, 0);

        PERFORM set_config('disponit.tenant', '', true);
    END LOOP;

    RETURN QUERY SELECT v_antall, v_nye, v_oppdaterte, v_lukket;
END $$;
REVOKE ALL ON FUNCTION m52_sveip_tollkode(INT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 6. RLS, radvakter og rettigheter.
-- ------------------------------------------------------------

RESET ROLE;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['tollkrav', 'nomenklatur', 'varenummer',
                             'tollvare', 'tollforslag', 'forslagsgrunn',
                             'tollfunn']
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
                       ' disponit_tollkode_eier', t);
    END LOOP;
END $$;

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER (111s form, 112–121):
-- bare på NOMENKLATURTABELLEN, bare FOR SELECT, bare til eieren, og
-- bare når ingen tenantkontekst står.
CREATE POLICY m52_sveip_tenantliste ON nomenklatur
    FOR SELECT TO disponit_tollkode_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- …OG PÅ VAREREGISTERET, av samme grunn og med samme snevre form:
-- tenantlista er begge registrene, fordi en tenant kan ha varer før han
-- har en nomenklatur.
CREATE POLICY m52_sveip_tenantliste_vare ON tollvare
    FOR SELECT TO disponit_tollkode_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- HISTORIKKTABELLENE FÅR IKKE UPDATE, HELLER IKKE FOR EIEREN.
--
-- `varenummer`, `tollvare` og `forslagsgrunn` ER HELT LUKKET: en
-- posisjonstekst, en varebeskrivelse og en grunn kan bare oppstå,
-- aldri endres. Det er ikke en radvakt som kan gjøre en feil — det er
-- en rettighet som ikke finnes.
REVOKE UPDATE ON public.varenummer FROM disponit_tollkode_eier;
REVOKE UPDATE ON public.tollvare FROM disponit_tollkode_eier;
REVOKE UPDATE ON public.forslagsgrunn FROM disponit_tollkode_eier;

-- `nomenklatur` FÅR BARE ENDRE `gyldig_til` (121s dom): et tollvesen
-- som kunngjør en avviklingsdato er nettopp den endringen modulen
-- skal følge med på. Identiteten — system, versjon, `gyldig_fra`,
-- innholdssummen — er frosset av kolonnegranten.
REVOKE UPDATE ON public.nomenklatur FROM disponit_tollkode_eier;
GRANT UPDATE (gyldig_til) ON public.nomenklatur
    TO disponit_tollkode_eier;

-- `tollforslag` BEHOLDER UPDATE — klarmerket må kunne settes.
-- Åpningen lukkes fra den andre siden, av radvakten under.
CREATE FUNCTION m52_nomenklatur_frosset()
RETURNS TRIGGER LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.nomenklatur_id IS DISTINCT FROM OLD.nomenklatur_id
       OR NEW.system IS DISTINCT FROM OLD.system
       OR NEW.versjon IS DISTINCT FROM OLD.versjon
       OR NEW.gyldig_fra IS DISTINCT FROM OLD.gyldig_fra
       OR NEW.innhold_sha256 IS DISTINCT FROM OLD.innhold_sha256
       OR NEW.kilde_url IS DISTINCT FROM OLD.kilde_url
       OR NEW.registrert IS DISTINCT FROM OLD.registrert
       OR NEW.registrert_av IS DISTINCT FROM OLD.registrert_av THEN
        RAISE EXCEPTION 'nomenklatur: identiteten er frosset — bare'
            ' gyldig_til kan settes. Identiteten er det som gjør et'
            ' gammelt forslag etterprøvbart'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER nomenklatur_frosset
    BEFORE UPDATE ON nomenklatur
    FOR EACH ROW EXECUTE FUNCTION m52_nomenklatur_frosset();

CREATE FUNCTION m52_forslag_frosset()
RETURNS TRIGGER LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF OLD.klar_til_deklarering THEN
        RAISE EXCEPTION 'tollforslag: forslaget % er merket klart og'
            ' er frosset — en ny vurdering hører til et nytt forslag',
            OLD.forslag_id USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- FORSLAGETS EGEN DEL ER FROSSET. `forslag_overskrevet` er denne
    -- listen: en kode, en sikkerhet eller en snapshotet versjon som
    -- kunne endres i ettertid, ville gjort «hva foreslo vi, og
    -- hvorfor» til et spørsmål uten svar den dagen tollmyndigheten
    -- spør.
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.forslag_id IS DISTINCT FROM OLD.forslag_id
       OR NEW.vare_id IS DISTINCT FROM OLD.vare_id
       OR NEW.nomenklatur_id IS DISTINCT FROM OLD.nomenklatur_id
       OR NEW.varenummer_id IS DISTINCT FROM OLD.varenummer_id
       OR NEW.system_ved_forslag
          IS DISTINCT FROM OLD.system_ved_forslag
       OR NEW.versjon_ved_forslag
          IS DISTINCT FROM OLD.versjon_ved_forslag
       OR NEW.kode_ved_forslag IS DISTINCT FROM OLD.kode_ved_forslag
       OR NEW.beskrivelse_ved_forslag
          IS DISTINCT FROM OLD.beskrivelse_ved_forslag
       OR NEW.sikkerhet IS DISTINCT FROM OLD.sikkerhet
       OR NEW.terskel_brukt IS DISTINCT FROM OLD.terskel_brukt
       OR NEW.kravversjon IS DISTINCT FROM OLD.kravversjon
       OR NEW.avgitt IS DISTINCT FROM OLD.avgitt
       OR NEW.avgitt_av IS DISTINCT FROM OLD.avgitt_av THEN
        RAISE EXCEPTION 'tollforslag: forslagets egne felter er'
            ' frosset — bare klarmerket kan settes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER tollforslag_frosset
    BEFORE UPDATE ON tollforslag
    FOR EACH ROW EXECUTE FUNCTION m52_forslag_frosset();

-- SLETTING ER ALDRI LOVLIG.
REVOKE DELETE ON public.tollkrav FROM disponit_tollkode_eier;
REVOKE DELETE ON public.nomenklatur FROM disponit_tollkode_eier;
REVOKE DELETE ON public.varenummer FROM disponit_tollkode_eier;
REVOKE DELETE ON public.tollvare FROM disponit_tollkode_eier;
REVOKE DELETE ON public.tollforslag FROM disponit_tollkode_eier;
REVOKE DELETE ON public.forslagsgrunn FROM disponit_tollkode_eier;
REVOKE DELETE ON public.tollfunn FROM disponit_tollkode_eier;

-- KJØRETIDSROLLEN FÅR DØRENE, ALDRI TABELLENE.
--
-- GRANTENE GIS AV EIEREN (116s lærdom), og `REVOKE ... FROM <rolle som
-- ikke finnes>` er en FEIL i PostgreSQL, ikke en no-op (målt i 117).
SET LOCAL ROLE disponit_tollkode_eier;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m52_tollstatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m52_kravene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m52_nomenklaturene(TEXT, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m52_varenumrene(TEXT, UUID, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m52_varene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m52_grunnene(TEXT, UUID)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m52_forslagene(TEXT, UUID)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m52_funnene(TEXT, BOOLEAN)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m52_sett_krav(TEXT, INT, INT, INT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m52_registrer_nomenklatur(TEXT, UUID, TEXT, TEXT, DATE,'
            ' DATE, TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m52_sett_gyldig_til(TEXT, UUID, DATE, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m52_registrer_varenummer(TEXT, UUID, UUID, TEXT, TEXT,'
            ' INT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m52_registrer_vare(TEXT, UUID, TEXT, TEXT, TEXT, TEXT,'
            ' TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m52_avgi_forslag(TEXT, UUID, UUID, UUID, INT, TEXT[],'
            ' TEXT[], TEXT[], DATE[], TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m52_merk_klart(TEXT, UUID, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m52_lukk_funn(TEXT, UUID, TEXT, TEXT) TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_tollkodesveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m52_sveip_tollkode(INT) TO disponit_tollkodesveip';
    END IF;
END $$;

-- SVEIPEN ER IKKE KJØRETIDSROLLENS.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'REVOKE EXECUTE ON FUNCTION m52_sveip_tollkode(INT)'
            ' FROM disponit';
    END IF;
END $$;

RESET ROLE;
