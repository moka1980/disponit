-- =====================================================================
-- M-53 HMS- OG AVVIKSMOTTAK (v1) — ET FELT SOM KAN FYLLES BLIR FYLT.
-- =====================================================================
--
-- V1-DOMMEN: MODULEN VARSLER INGEN MYNDIGHET OG LUKKER INGEN AVVIK.
--
-- KLYNGE 7s FEMTE OG SISTE, og den hører egentlig ikke hjemme i den.
-- `docs/KLYNGE7-FUNDAMENT.md` skrev det ned før noen av de fem var
-- bygget: de fire andre PRODUSERER noe som skal ut. Et avviksmottak
-- TAR IMOT — en innboks, ikke en utboks. Og risikoen ligger et helt
-- annet sted: dette er den eneste modulen i katalogen som mottar data
-- OM en ansatt FRA en ansatt.
--
-- DERFOR ER DEN BYGGET SIST, og grensesnittet mot M-30 er avklart FØR
-- denne filen ble skrevet. Avklaringen står i
-- `docs/M53-M30-GRENSESNITTET.md` og oppsummeres i §0 under.
--
-- ---------------------------------------------------------------------
-- DET SKARPESTE FUNNET I HELE MODULEN: HUSETS EGEN STANDARDKOLONNE ER
-- LEKKASJEN.
--
-- Hver tabell i dette huset har `opprettet_av TEXT NOT NULL`. Den er
-- riktig overalt ellers — 099 la til og med `lukket_av` med overlegg,
-- fordi «en statusovergang som ikke bærer navnet sitt er en overgang
-- en jobb kunne ha gjort».
--
-- På et ANONYMT avvik er den samme kolonnen selve bruddet. Et anonymt
-- avvik som bærer aktøren i `opprettet_av` er ikke anonymt; det er et
-- avvik der navnet står i en annen kolonne enn den man ser på.
--
-- Og verre: `revisjonslogg` er append-only, håndhevet av trigger siden
-- 001. ET NAVN SOM LEKKER INN I EVIDENSKJEDEN KAN ALDRI FJERNES IGJEN
-- — den samme garantien som gjør beviskjeden troverdig, gjør lekkasjen
-- permanent. M-30 så det for sitt eget register og skrev det inn i
-- `m30_evidens`: `subjekt_ref` står ALDRI i evidensraden. Her gjelder
-- det VARSLEREN, og et varslervern som lekker identitet er verdiløst.
--
-- ANONYMT AVVIK ER DERFOR EN FØRSTEKLASSES TILSTAND, IKKE ET TOMT
-- NAVNEFELT. Melderen er en egen RAD SOM IKKE FINNES, ikke en NULL i
-- en kolonne. Et felt som KAN fylles blir fylt.
--
-- OG TIDSSTEMPLET ER OGSÅ IDENTITET. `now()` på mikrosekundet, i en
-- bedrift med tolv ansatte og en vaktliste, peker på én person.
-- Anonyme avvik bærer DATO, ikke tidspunkt — og kolonnen har derfor
-- ingen `DEFAULT now()` å gli på.
--
-- DEN ÆRLIGE GRENSEN: fritekst kan ikke sikres. «Jeg sa fra til
-- formannen på tirsdag» identifiserer melderen uansett hva skjemaet
-- gjør. Flaten sier det til den som skriver, FØR det skrives. Det er
-- så langt en database kan komme, og vi later ikke som den kommer
-- lenger.
--
-- PRISEN VI BETALER, SKREVET NED: plattformen kan heller ikke spore
-- et anonymt avvik. Ikke «vil ikke» — KAN IKKE. En anonym kanal som
-- ikke kan spores kan også misbrukes, og det aksepteres, fordi
-- alternativet er et varslervern vi holder helt til noen med nok
-- myndighet spør.
--
-- ---------------------------------------------------------------------
-- OPPBEVARINGSPLIKT MOT SLETTEPLIKT — TO FUNN SOM PEKER MOT HVERANDRE.
--
-- Arbeidstilsynet krever at avvik BEVARES. GDPR krever at
-- personopplysninger SLETTES. Begge retninger biter, og modulen måler
-- begge:
--
--   * `oppbevaring_utlopt` — vi holder en identifiserbar HMS-opplysning
--     lenger enn vår egen hjemmel rekker. LUKKES IKKE AV ET MENNESKE;
--     den lukkes av at raden ANONYMISERES.
--
--   * `for_tidlig_anonymisert` — en rad forsvant FØR fristen, uten en
--     M-30-sak å vise til. Det er invarianten
--     `sletting_uten_m30_avklaring`, og den er det motsatte bruddet.
--
-- HVER RAD BÆRER SIN EGEN HJEMMEL, og det er ikke pynt: et ordinært
-- avvik, en personskade med helseopplysninger etter GDPR art. 9 og et
-- varsel etter arbeidsmiljøloven kap. 2 A har ULIK hjemmel og ULIK
-- lengde. M-4s `retensjonslager.frist_dogn` er ETT tall for HELE
-- lageret; registreres de under ett tall er tallet feil for minst to
-- av tre. `oppbevaring_hjemmel` og `oppbevaring_til` er derfor
-- NOT NULL på raden — M-50s `journalperson.slettefrist`-form (124):
-- det farlige gjøres UMULIG, ikke oppdaget.
--
-- ---------------------------------------------------------------------
-- KLYNGENS DELTE DOM: EN FORELDET REGEL SER NØYAKTIG UT SOM EN RIKTIG
-- REGEL. Hvert avvik bærer regelversjonen sin, snapshotet, og et avvik
-- regnet under et utløpt regelverk er et sveipefunn — ikke et stille
-- galt svar.
--
-- GRENSEN MOT M-21: `plikt` er M-21s bord. Her heter tabellene
-- `hmsavvik`, `hmstiltak`, `hmsmelder` — kollisjonen ER grensen
-- (123s lærdom).
-- =====================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_hms_eier') THEN
        RAISE EXCEPTION 'rollen disponit_hms_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_hms_eier;
GRANT INSERT ON revisjonslogg TO disponit_hms_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_hms_eier;
RESET ROLE;

-- TABELLENE EIES AV MIGRATOREN, FUNKSJONENE AV MODULROLLEN (122–124s
-- form). RLS slås på av en `ALTER TABLE`, og bare eieren kan gjøre det:
-- lager modulrollen tabellene, kan den også ta radvakten AV igjen.

-- ---------------------------------------------------------------------
-- TENANTENS EGNE GRENSER.
--
-- HVOR LENGE VI KAN OPPBEVARE ER TENANTENS BESLUTNING, ikke vår. Et
-- bygg- og anleggsforetak og et regnskapskontor har ikke samme
-- risikobilde, og et tak vi satte for dem ville vært en fullmakt
-- modulen ga seg selv over kundens etterlevelse (samme dom som M-55s
-- `forvekslingsterskel_hardkodet`).
-- ---------------------------------------------------------------------
CREATE TABLE hmskrav (
    tenant TEXT PRIMARY KEY CHECK (length(btrim(tenant)) > 0),
    -- TAKET på hva en oppbevaringshjemmel kan kreve. Døra nekter et
    -- avvik hvis regelverkets frist er lengre enn dette.
    oppbevaring_maks_dogn INT NOT NULL DEFAULT 3650
        CHECK (oppbevaring_maks_dogn BETWEEN 30 AND 21900),
    -- Hvor lenge før oppbevaringsfristen vi sier fra.
    oppbevaringsvarsel_dogn INT NOT NULL DEFAULT 60
        CHECK (oppbevaringsvarsel_dogn BETWEEN 1 AND 365),
    -- HVOR LENGE ET AVVIK KAN STÅ UBEHANDLET. Sveipens hovedjobb:
    -- et avvik ingen har gjort noe med er modulens egen grunn til å
    -- finnes.
    tiltaksfrist_dogn INT NOT NULL DEFAULT 14
        CHECK (tiltaksfrist_dogn BETWEEN 1 AND 365),
    -- Hvor lenge før et regelverk utløper vi sier fra.
    regelvarsel_dogn INT NOT NULL DEFAULT 60
        CHECK (regelvarsel_dogn BETWEEN 1 AND 730),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon > 0),
    -- IDEMPOTENSNØKKELEN LEVER PÅ RADEN (M-51s lærdom 119, gjentatt i
    -- 123 og 124). Hvert funn bærer `kravversjon`: en versjon som økte
    -- uten at en grense endret seg gjør funnhistorikken uleselig.
    siste_nokkel TEXT,
    satt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    satt_av TEXT NOT NULL CHECK (length(btrim(satt_av)) > 0)
);

-- ---------------------------------------------------------------------
-- REGELVERKET — MED GYLDIGHET, FORDI DET BLIR GAMMELT.
--
-- Én rad per (tenant, avvikstype, versjon). Raden sier hvor lenge et
-- avvik av denne typen SKAL bevares, og med hvilken hjemmel.
--
-- IDENTITETEN ER FROSSET etter innsetting; bare `gyldig_til` kan settes
-- senere. Det er 121s dom, gjentatt i 122–124: en regel som kunne
-- endres i ettertid, ville gjort hvert snapshot til en påstand om noe
-- som ikke lenger står noe sted.
-- ---------------------------------------------------------------------
CREATE TABLE hmsregelverk (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    regel_id UUID NOT NULL,
    PRIMARY KEY (tenant, regel_id),
    -- LUKKET SETT. En «type» utenfor settet er ikke en ny kategori —
    -- det er en feilregistrering, og et lukket sett er den eneste
    -- formen som kan si det (099s begrunnelse for GDPR-rettighetene,
    -- ordrett).
    --
    -- `varsel` står med i settet MED VILJE, selv om et varsel etter
    -- arbeidsmiljøloven kap. 2 A ikke er et HMS-avvik i snever
    -- forstand: den som melder velger ikke kategori etter jussen, og
    -- en kanal som ikke tar imot det, sender varsleren et annet sted.
    avvikstype TEXT NOT NULL
        CHECK (avvikstype IN ('naerulykke', 'personskade', 'sykdom',
                              'materiell', 'psykososialt', 'varsel')),
    versjon TEXT NOT NULL CHECK (versjon ~ '[^[:space:]]'),
    -- HJEMMELEN, SKREVET UT. Ikke en enum: hjemmelen er en henvisning
    -- til lov og forskrift, og den skal kunne leses av den som skal
    -- etterprøve svaret. Ikke-tom med vilje — en oppbevaringsplikt
    -- uten hjemmel er en vane.
    hjemmel TEXT NOT NULL CHECK (hjemmel ~ '[^[:space:]]'),
    oppbevaring_dogn INT NOT NULL CHECK (oppbevaring_dogn BETWEEN 1
                                                          AND 21900),
    -- SÆRLIGE KATEGORIER ETTER GDPR ART. 9 er ikke en mulighet her,
    -- det er normaltilfellet for `personskade` og `sykdom`. Flagget
    -- står på REGELEN og ikke på raden, fordi det er en egenskap ved
    -- KATEGORIEN og ikke ved den enkelte hendelsen — og fordi et flagg
    -- den som melder kunne huke av, ville blitt huket av feil.
    helseopplysninger BOOLEAN NOT NULL DEFAULT false,
    gyldig_fra DATE NOT NULL,
    gyldig_til DATE,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (length(btrim(opprettet_av)) > 0),
    CONSTRAINT hmsregelverk_gyldighet CHECK (
        gyldig_til IS NULL OR gyldig_til >= gyldig_fra),
    CONSTRAINT hmsregelverk_versjon_unik UNIQUE (tenant, avvikstype,
                                                 versjon)
);

-- Døra slår opp «regelen som gjelder i dag for denne typen». Delindeks
-- på de gjeldende: avviklede versjoner er historikk, og oppslaget skal
-- aldri betale for dem.
CREATE INDEX hmsregelverk_gjeldende
    ON hmsregelverk (tenant, avvikstype, gyldig_fra DESC)
    WHERE gyldig_til IS NULL;

-- ---------------------------------------------------------------------
-- AVVIKET.
--
-- HER LIGGER MODULENS TO VANSKELIGSTE KOLONNER, og de er begge NULLBARE
-- I EN TABELL DER HUSFORMEN SIER NOT NULL. Avviket fra formen er
-- BEGRUNNET, ikke uteglemt:
--
--   `meldt_av` — aktøren. NULL på anonyme avvik, og det er hele
--   varslervernet. En CHECK gjør de to tilstandene gjensidig
--   utelukkende, slik at «anonymt avvik med aktør» ikke er en rad noen
--   må huske å ikke skrive — den er UREPRESENTERBAR.
--
--   `meldt_ts` — tidspunktet. NULL på anonyme avvik, av samme grunn:
--   `now()` på mikrosekundet, i en bedrift med tolv ansatte og en
--   vaktliste, peker på én person. KOLONNEN HAR INGEN `DEFAULT now()`,
--   og det er med vilje — en default her ville fylt seg selv i det
--   stille og gjort vernet til pynt. `meldt_dato` er NOT NULL for
--   begge: en dato er nok til å måle en tiltaksfrist, og for lite til
--   å peke ut en person.
--
-- APPEND-ONLY PÅ INNHOLDET (`avvik_overskrevet`): beskrivelsen,
-- typen, hjemmelen og regelversjonen fryses ved innsetting. Bare
-- STATUSFELTENE og anonymiseringssporet kan endres, og radvakten i §2
-- sier hvilke. M-42s dom (110), gjentatt i 112–124.
-- ---------------------------------------------------------------------
CREATE TABLE hmsavvik (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    avvik_id UUID NOT NULL,
    PRIMARY KEY (tenant, avvik_id),
    avvikstype TEXT NOT NULL
        CHECK (avvikstype IN ('naerulykke', 'personskade', 'sykdom',
                              'materiell', 'psykososialt', 'varsel')),
    -- MELDERFORMEN ER EN TILSTAND, IKKE ET TOMT FELT.
    melderform TEXT NOT NULL CHECK (melderform IN ('navngitt', 'anonym')),
    -- Hendelsen selv. Fritekst, og den kan ikke sikres — se filhodet.
    beskrivelse TEXT NOT NULL CHECK (beskrivelse ~ '[^[:space:]]'),
    -- HVOR. Ikke-tom: et avvik uten sted er et avvik ingen kan lukke.
    sted TEXT NOT NULL CHECK (sted ~ '[^[:space:]]'),
    hendelsesdato DATE NOT NULL,
    meldt_dato DATE NOT NULL,
    meldt_ts TIMESTAMPTZ,
    meldt_av TEXT,
    -- REGELVERKET, SNAPSHOTET. Ikke en fremmednøkkel til en rad som
    -- kan endres — verdiene selv, frosset i det avviket ble tatt imot.
    -- Klyngens dom 2, håndhevet av NOT NULL og ikke av en regel noen
    -- må huske.
    regel_id UUID NOT NULL,
    regelversjon TEXT NOT NULL CHECK (regelversjon ~ '[^[:space:]]'),
    oppbevaring_hjemmel TEXT NOT NULL
        CHECK (oppbevaring_hjemmel ~ '[^[:space:]]'),
    oppbevaring_dogn INT NOT NULL CHECK (oppbevaring_dogn > 0),
    -- DATOEN VI SELV HAR BUNDET OSS TIL. NOT NULL: en HMS-opplysning
    -- uten oppbevaringsplan skal ikke kunne OPPSTÅ.
    oppbevaring_til DATE NOT NULL,
    helseopplysninger BOOLEAN NOT NULL,
    kravversjon INT NOT NULL CHECK (kravversjon > 0),
    -- STATUS. `apen` → `behandlet` skjer ved at ET MENNESKE registrerer
    -- et tiltak som lukker. Det finnes INGEN vei fra sveipen hit
    -- (`modulen_lukket_avvik_selv`), og ingen kolonne som betyr
    -- «varslet myndighet» (`modulen_varslet_myndighet`).
    status TEXT NOT NULL DEFAULT 'apen'
        CHECK (status IN ('apen', 'behandlet')),
    behandlet_ts TIMESTAMPTZ,
    behandlet_av TEXT,
    -- ANONYMISERINGSSPORET. Raden SLETTES ALDRI: at vi HAR hatt et
    -- avvik er nøyaktig det Arbeidstilsynet etterprøver, og sletting
    -- ville fjernet beviset på at vi hadde det (M-50s dom, 124).
    anonymisert_ts TIMESTAMPTZ,
    anonymisert_av TEXT,
    -- M-30-HENVISNINGEN. Se `docs/M53-M30-GRENSESNITTET.md`:
    -- anonymisering FØR oppbevaringsfristen krever en sak å vise til.
    -- Etter fristen gjør reaperen det uten. Kolonnen er en HENVISNING
    -- og ingen fremmednøkkel — samme retning på autoriteten som 099s
    -- egen vakt mot `retensjonslager`.
    m30_sak_ref TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- VARSLERVERNET SOM SKJEMA. Begge halvdeler, fordi bare den ene
    -- ville latt den andre tilstanden være representerbar:
    --   * anonym  → ingen aktør, intet tidspunkt
    --   * navngitt → begge, og begge ikke-tomme
    CONSTRAINT hmsavvik_anonym_er_sporlos CHECK (
        melderform <> 'anonym'
        OR (meldt_av IS NULL AND meldt_ts IS NULL)),
    -- …OG «NAVNGITT» BETYR «BÆRER ET NAVN INNTIL DET FJERNES».
    --
    -- Første utgave av denne CHECKen manglet `anonymisert_ts`-leddet,
    -- og de to reglene motsa da hverandre: denne krevde at et
    -- navngitt avvik HAR en aktør, og `hmsavvik_anonymisert_er_sporlost`
    -- krevde at et anonymisert avvik IKKE har en. Et navngitt avvik
    -- kunne dermed ALDRI anonymiseres.
    --
    -- Det var ikke en skjønnhetsfeil. `oppbevaring_utlopt` er funnet
    -- ingen kan lukke, og det lukkes av ÉN handling: anonymisering.
    -- Med den blokkert ville funnet stått åpent for alltid på
    -- nøyaktig de radene som betyr mest — de navngitte — og modulen
    -- ville hatt et varsel den selv gjorde umulig å svare på.
    --
    -- Fanget av en riggkjøring, ikke av lesing.
    CONSTRAINT hmsavvik_navngitt_er_navngitt CHECK (
        melderform <> 'navngitt'
        OR anonymisert_ts IS NOT NULL
        OR (meldt_av IS NOT NULL AND length(btrim(meldt_av)) > 0
            AND meldt_ts IS NOT NULL)),
    -- Et avvik kan ikke være meldt før det skjedde.
    CONSTRAINT hmsavvik_meldt_etter_hendelse CHECK (
        meldt_dato >= hendelsesdato),
    -- Fristen regnes fra meldingen, og døra regner den — men CHECKen
    -- står her uansett, fordi den gjelder ENHVER skrivevei, også
    -- direkte DML som eier (099s form).
    CONSTRAINT hmsavvik_oppbevaring_er_regnet CHECK (
        oppbevaring_til = meldt_dato + oppbevaring_dogn),
    -- LUKKEREGELEN, og den er TOTAL: et behandlet avvik bærer et
    -- menneske og et tidspunkt, et åpent bærer ingen av delene. Et
    -- felt som bare betyr noe i én status skal være NULL i de andre
    -- (099s `personvernsak_apen_er_ubesvart`, ordrett begrunnelse).
    CONSTRAINT hmsavvik_behandlet_krever_menneske CHECK (
        status <> 'behandlet'
        OR (behandlet_ts IS NOT NULL AND behandlet_av IS NOT NULL
            AND length(btrim(behandlet_av)) > 0)),
    CONSTRAINT hmsavvik_apen_er_ubehandlet CHECK (
        status <> 'apen'
        OR (behandlet_ts IS NULL AND behandlet_av IS NULL)),
    -- Anonymiseringen er hel eller ikke skjedd.
    CONSTRAINT hmsavvik_anonymisering_er_hel CHECK (
        (anonymisert_ts IS NULL AND anonymisert_av IS NULL)
        OR (anonymisert_ts IS NOT NULL AND anonymisert_av IS NOT NULL
            AND length(btrim(anonymisert_av)) > 0)),
    -- ET ANONYMISERT AVVIK KAN IKKE BÆRE EN AKTØR. Uten denne ville
    -- anonymiseringen tømt melderraden og latt navnet stå igjen i
    -- kolonnen ved siden av — nøyaktig den feilen filhodet handler om.
    CONSTRAINT hmsavvik_anonymisert_er_sporlost CHECK (
        anonymisert_ts IS NULL
        OR (meldt_av IS NULL AND meldt_ts IS NULL))
);

-- Sveipen spør «åpne avvik, eldst først» og «oppbevaringsfrist
-- nærmer seg». To delindekser, begge på det sveipen faktisk spør om.
CREATE INDEX hmsavvik_apne
    ON hmsavvik (tenant, meldt_dato)
    WHERE status = 'apen';
CREATE INDEX hmsavvik_oppbevaring
    ON hmsavvik (tenant, oppbevaring_til)
    WHERE anonymisert_ts IS NULL;

-- ---------------------------------------------------------------------
-- MELDEREN — EN RAD SOM IKKE FINNES.
--
-- DETTE ER MODULENS BÆRENDE VALG. Alternativet — en `melder_navn`-
-- kolonne på `hmsavvik` som står tom for anonyme — ville vært den
-- samme feilen som filhodet advarer mot: ET FELT SOM KAN FYLLES BLIR
-- FYLT. En import, en migrering, en velmenende integrasjon, og
-- anonymiteten er borte uten at noe feilet.
--
-- Her er anonymiteten fraværet av en rad. Vakten i §2 nekter en
-- melderrad mot et avvik med `melderform = 'anonym'`, og da finnes det
-- ingen kolonne å fylle.
--
-- SLETTEFRIST NOT NULL, av M-50s grunn (124): melderens navn er en
-- personopplysning, og en personopplysning uten sletteplan skal ikke
-- kunne oppstå.
-- ---------------------------------------------------------------------
CREATE TABLE hmsmelder (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    avvik_id UUID NOT NULL,
    PRIMARY KEY (tenant, avvik_id),
    CONSTRAINT hmsmelder_avvik_fk FOREIGN KEY (tenant, avvik_id)
        REFERENCES hmsavvik (tenant, avvik_id),
    -- NAVNET. Nullbart BARE fordi anonymiseringen TØMMER det — ved
    -- registrering krever døra at det står. En plassholderstreng
    -- («(anonymisert)») ville vært 124s CodeRabbit-funn om igjen: en
    -- rad som SER anonymisert ut uten å være det, og som et søk på
    -- navn fortsatt treffer.
    navn TEXT CHECK (navn IS NULL OR navn ~ '[^[:space:]]'),
    rolle TEXT CHECK (rolle IS NULL OR rolle ~ '[^[:space:]]'),
    -- ANONYMISERT ER FRAVÆR AV NAVN, ikke en verdi som betyr fravær.
    CONSTRAINT hmsmelder_anonym_er_navnlos CHECK (
        (anonymisert_ts IS NULL AND navn IS NOT NULL)
        OR (anonymisert_ts IS NOT NULL AND navn IS NULL
            AND rolle IS NULL)),
    slettefrist DATE NOT NULL,
    anonymisert_ts TIMESTAMPTZ,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX hmsmelder_slettefrist
    ON hmsmelder (tenant, slettefrist)
    WHERE anonymisert_ts IS NULL;

-- ---------------------------------------------------------------------
-- TILTAKET — HANDLINGEN, IKKE MENINGEN OM DEN.
--
-- Append-only. `lukker` er den ENE veien fra `apen` til `behandlet`,
-- og den bærer alltid et navngitt menneske: et avvik som ble lukket
-- uten at noen skrev navnet sitt, er et avvik en jobb kunne ha lukket.
-- ---------------------------------------------------------------------
CREATE TABLE hmstiltak (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    avvik_id UUID NOT NULL,
    tiltak_id UUID NOT NULL,
    PRIMARY KEY (tenant, tiltak_id),
    CONSTRAINT hmstiltak_avvik_fk FOREIGN KEY (tenant, avvik_id)
        REFERENCES hmsavvik (tenant, avvik_id),
    beskrivelse TEXT NOT NULL CHECK (beskrivelse ~ '[^[:space:]]'),
    lukker BOOLEAN NOT NULL DEFAULT false,
    utfort_dato DATE NOT NULL,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (length(btrim(opprettet_av)) > 0)
);

CREATE INDEX hmstiltak_avvik ON hmstiltak (tenant, avvik_id, utfort_dato);

-- ---------------------------------------------------------------------
-- FUNNENE. LUKKET SETT, ETT ÅPENT PER (nøkkel, funntype).
--
-- Formen er `journalfunn` sin (124), og begrunnelsen med den: en
-- funnliste som vokser med kadensen er en funnliste folk lærer seg å
-- overse, og da forsvinner de viktige med dem.
--
-- TO FUNN INGEN KAN LUKKE, og de peker mot HVER SIN retning:
--
--   `oppbevaring_utlopt` — vi holder en identifiserbar HMS-opplysning
--   lenger enn vår egen hjemmel rekker. Lukkes av at raden
--   ANONYMISERES, ikke av at noen klikker.
--
--   `for_tidlig_anonymisert` — en rad forsvant FØR fristen uten en
--   M-30-sak å vise til. Det motsatte bruddet: Arbeidstilsynet krever
--   at avviket bevares, og en tidlig sletting er ikke en ryddig
--   sletting, den er et bortkommet bevis.
--
--   `avvik_mot_utlopt_regelverk` — klyngens delte funn. En foreldet
--   regel ser nøyaktig ut som en riktig regel.
--
-- INGEN ETTERFØLGER-UNNTAK PÅ AVVIKSNIVÅET (123s lærdom, funnet av min
-- egen port der): et avvik regnet under versjon 1 er fortsatt regnet
-- under versjon 1 etter at versjon 2 er registrert. Forsvant funnet i
-- det øyeblikket noen la inn en ny regel, ville funnet forsvunnet
-- fordi problemet ble STØRRE.
-- ---------------------------------------------------------------------
CREATE TABLE hmsfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    funn_id UUID NOT NULL,
    PRIMARY KEY (tenant, funn_id),
    funntype TEXT NOT NULL CHECK (funntype IN (
        'ingen_krav',
        -- `ingen_regelverk` STO HER OG ER FJERNET. Døra nekter et
        -- avvik uten en gjeldende regel, så tilstanden kan ikke
        -- oppstå — blir regelen avviklet SENERE, er det
        -- `avvik_mot_utlopt_regelverk`. En verdi i et lukket sett som
        -- ingen kode kan produsere er et løfte ingenting holder, og
        -- den neste som leser lista bruker tid på å finne ut hvorfor
        -- den aldri dukker opp.
        'regelverk_utlopt',
        'regelverk_utloper_snart',
        'avvik_mot_utlopt_regelverk',
        'avvik_ubehandlet',
        'oppbevaring_naermer_seg',
        'oppbevaring_utlopt',
        'for_tidlig_anonymisert')),
    regel_id UUID,
    avvik_id UUID,
    CONSTRAINT hmsfunn_nivaa CHECK (
        CASE funntype
          WHEN 'ingen_krav' THEN
            regel_id IS NOT NULL OR avvik_id IS NOT NULL
          WHEN 'regelverk_utlopt' THEN regel_id IS NOT NULL
          WHEN 'regelverk_utloper_snart' THEN regel_id IS NOT NULL
          ELSE avvik_id IS NOT NULL
        END),
    CONSTRAINT hmsfunn_en_noekkel CHECK (
        num_nonnulls(regel_id, avvik_id) = 1),
    -- DØGN, MED FORTEGN BÅRET AV FUNNTYPEN: `naermer_seg` teller ned,
    -- `utlopt` teller opp. En frist som gikk i går og en som gikk for
    -- fem år siden er to ulike brudd (124s formulering, som holder).
    over_grense INT,
    detalj TEXT CHECK (detalj IS NULL OR detalj ~ '[^[:space:]]'),
    kravversjon INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT,
    lukkenotat TEXT,
    CONSTRAINT hmsfunn_lukking CHECK (
        (apen AND lukket_ts IS NULL AND lukket_av IS NULL
             AND lukkenotat IS NULL)
        OR (NOT apen AND lukket_ts IS NOT NULL)),
    -- FRA FØDSELEN, ikke som en ettermontering (125). En lukket rad
    -- uten navn gjorde `apen OR lukket_av = '...'` til NULL og felte
    -- hele sveipetransaksjonen på 124 — samme NULL-form som
    -- `cardinality(NULL)` i 122.
    CONSTRAINT hmsfunn_lukket_har_navn CHECK (
        apen OR (lukket_av IS NOT NULL
                 AND lukket_av ~ '[^[:space:]]'))
);
CREATE UNIQUE INDEX hmsfunn_regel_unik
    ON hmsfunn (tenant, regel_id, funntype) WHERE regel_id IS NOT NULL;
CREATE UNIQUE INDEX hmsfunn_avvik_unik
    ON hmsfunn (tenant, avvik_id, funntype) WHERE avvik_id IS NOT NULL;
CREATE INDEX hmsfunn_apne_idx ON hmsfunn (tenant, apen, funntype);

-- =====================================================================
-- HERFRA EIES DØRENE AV HMS-EIEREN.
-- =====================================================================
SET LOCAL ROLE disponit_hms_eier;

-- FUNNENE INGEN KAN LUKKE, SOM EN FUNKSJON OG IKKE EN HUSKEREGEL.
-- Lista står her, én gang, og både lukkedøra og lesedøra leser den —
-- flaten slipper å kopiere regelen (124s form).
--
-- DE TO HJELPEFUNKSJONENE ER FLYTTET INN UNDER MODULROLLEN, og det er
-- en RETTELSE av 124s form og ikke en kopi av den: der står
-- `m50_funn_er_sveipens` og `m50_kilde_gyldig` som migrators i basen,
-- mens `deploy/staging/eierskap-reparasjon.sql` deklarerer dem som
-- postjournaleierens. Reparasjonen flytter dem altså hver gang den
-- kjører, og paritetsporten ser det ikke fordi den måler FORMEN på
-- designtabellen, ikke eierskapet i basen. Her lages de av den rollen
-- designet sier eier dem, én gang.
CREATE FUNCTION m53_funn_er_sveipens(p_funntype TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT p_funntype IN ('avvik_mot_utlopt_regelverk',
                          'oppbevaring_utlopt',
                          'for_tidlig_anonymisert')
$$;
REVOKE ALL ON FUNCTION m53_funn_er_sveipens(TEXT) FROM PUBLIC;

-- STABLE, IKKE IMMUTABLE (125s lærdom, innebygd fra fødselen).
-- Funksjonen leser `current_date`, og planleggeren har LOV til å folde
-- en IMMUTABLE funksjon til en konstant og gjenbruke den i en bufret
-- plan. Jeg skrev det feil i både 123 og 124.
CREATE FUNCTION m53_regel_gyldig(p_fra DATE, p_til DATE)
RETURNS BOOLEAN LANGUAGE sql STABLE
SET search_path = pg_catalog AS $$
    SELECT p_fra <= current_date
       AND (p_til IS NULL OR p_til >= current_date)
$$;
REVOKE ALL ON FUNCTION m53_regel_gyldig(DATE, DATE) FROM PUBLIC;

-- EVIDENSKJEDEN — OG DEN SKARPESTE REGELEN I HELE FILEN.
--
-- `p_aktor` ER NULLBAR, og for et anonymt avvik ER den NULL.
-- `revisjonslogg.aktor` har vært nullbar siden 001, så formen finnes
-- allerede; det som er nytt er at fraværet her er en PLIKT og ikke en
-- mulighet.
--
-- `revisjonslogg` er append-only, håndhevet av trigger siden 001. Et
-- navn som lekker inn her kan ALDRI fjernes igjen — den samme
-- garantien som gjør beviskjeden troverdig, gjør lekkasjen permanent.
-- M-30 skrev det inn i `m30_evidens` for sitt eget subjekt; her
-- gjelder det varsleren.
--
-- OG BESKRIVELSEN GÅR ALDRI INN I HASHEN. `input_hash` er sha256 over
-- den KANONISKE BESKRIVELSEN AV HANDLINGEN, ikke over innholdet i
-- avviket. En hash er enveis, men den lar hvem som helst BEKREFTE en
-- gjetning — og «var det Kari som meldte om formannen?» er nøyaktig
-- den gjetningen vernet skal gjøre ubesvarbar.
CREATE FUNCTION m53_evidens(p_tenant TEXT, p_avvik_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm53_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm53_hms', 'handling', p_handling,
        'avvik_id', p_avvik_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm53_hms',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:hms_avvik', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m53_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- =====================================================================
-- DØRENE.
-- =====================================================================

CREATE FUNCTION m53_sett_krav(p_tenant TEXT, p_maks_dogn INT,
                              p_oppbevaringsvarsel INT,
                              p_tiltaksfrist INT, p_regelvarsel INT,
                              p_aktor TEXT, p_nokkel TEXT)
RETURNS TABLE (oppbevaring_maks_dogn INT, oppbevaringsvarsel_dogn INT,
               tiltaksfrist_dogn INT, regelvarsel_dogn INT,
               versjon INT, endret BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_rad public.hmskrav%ROWTYPE;
    v_endret BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm53_sett_krav');
    IF p_nokkel IS NULL OR btrim(p_nokkel) = '' THEN
        RAISE EXCEPTION 'm53_sett_krav: idempotensnøkkel mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- LÅS FØRST, LES ETTERPÅ (klynge 6s lærdom, gjentatt i 123/124):
    -- en lesing før `FOR UPDATE` bruker transaksjonens snapshot, og to
    -- samtidige kall ville begge sett den gamle raden.
    SELECT * INTO v_rad FROM public.hmskrav
     WHERE tenant = p_tenant FOR UPDATE;

    -- IDEMPOTENSNØKKELEN LEVER PÅ RADEN (M-51s lærdom 119, og M-47s
    -- gjentakelse av den i 123 — der døra IGNORERTE nøkkelen og
    -- versjonen økte for hvert gjenspill, mens hvert funn bar
    -- `kravversjon`).
    IF FOUND AND v_rad.siste_nokkel IS NOT DISTINCT FROM p_nokkel THEN
        RETURN QUERY SELECT v_rad.oppbevaring_maks_dogn,
                            v_rad.oppbevaringsvarsel_dogn,
                            v_rad.tiltaksfrist_dogn,
                            v_rad.regelvarsel_dogn,
                            v_rad.versjon, false;
        RETURN;
    END IF;

    v_endret := NOT FOUND
        OR v_rad.oppbevaring_maks_dogn IS DISTINCT FROM p_maks_dogn
        OR v_rad.oppbevaringsvarsel_dogn IS DISTINCT FROM
           p_oppbevaringsvarsel
        OR v_rad.tiltaksfrist_dogn IS DISTINCT FROM p_tiltaksfrist
        OR v_rad.regelvarsel_dogn IS DISTINCT FROM p_regelvarsel;

    INSERT INTO public.hmskrav
        (tenant, oppbevaring_maks_dogn, oppbevaringsvarsel_dogn,
         tiltaksfrist_dogn, regelvarsel_dogn, versjon, siste_nokkel,
         satt_av)
    VALUES (p_tenant, p_maks_dogn, p_oppbevaringsvarsel,
            p_tiltaksfrist, p_regelvarsel, 1, p_nokkel, p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        oppbevaring_maks_dogn = EXCLUDED.oppbevaring_maks_dogn,
        oppbevaringsvarsel_dogn = EXCLUDED.oppbevaringsvarsel_dogn,
        tiltaksfrist_dogn = EXCLUDED.tiltaksfrist_dogn,
        regelvarsel_dogn = EXCLUDED.regelvarsel_dogn,
        -- VERSJONEN ØKER BARE NÅR EN GRENSE FAKTISK ENDRET SEG.
        versjon = public.hmskrav.versjon + CASE WHEN v_endret
                                                THEN 1 ELSE 0 END,
        siste_nokkel = EXCLUDED.siste_nokkel,
        satt_ts = now(), satt_av = EXCLUDED.satt_av
    RETURNING * INTO v_rad;

    PERFORM public.m53_evidens(p_tenant, NULL, 'sett_krav', p_aktor,
        jsonb_build_object('versjon', v_rad.versjon,
                           'endret', v_endret));
    RETURN QUERY SELECT v_rad.oppbevaring_maks_dogn,
                        v_rad.oppbevaringsvarsel_dogn,
                        v_rad.tiltaksfrist_dogn,
                        v_rad.regelvarsel_dogn, v_rad.versjon,
                        v_endret;
END $$;
REVOKE ALL ON FUNCTION m53_sett_krav(TEXT, INT, INT, INT, INT, TEXT,
                                     TEXT) FROM PUBLIC;

-- REGELVERKSDØRA. En avviklet versjon KAN registreres: arkivet skal
-- kunne svare på hvilken regel som gjaldt den gangen. Skillet går ved
-- AVVIKET — `m53_meld_avvik` nekter mot en regel som ikke gjelder i
-- dag (124s form, og 121s dom).
CREATE FUNCTION m53_registrer_regel(
    p_tenant TEXT, p_regel_id UUID, p_avvikstype TEXT, p_versjon TEXT,
    p_hjemmel TEXT, p_oppbevaring_dogn INT, p_helse BOOLEAN,
    p_gyldig_fra DATE, p_gyldig_til DATE, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_gml public.hmsregelverk%ROWTYPE;
    v_ny BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm53_registrer_regel');
    SELECT * INTO v_gml FROM public.hmsregelverk
     WHERE tenant = p_tenant AND regel_id = p_regel_id FOR UPDATE;

    -- SP-2-MATERIALITET (m35/096-formen, gjentatt i 121-124):
    -- kalleren utleder id-en av sin Idempotency-Key. Samme id med
    -- SAMME innhold er et stille ja; samme id med ANNET innhold er en
    -- materiell konflikt, ikke en oppdatering.
    IF FOUND THEN
        IF v_gml.avvikstype IS DISTINCT FROM p_avvikstype
           OR v_gml.versjon IS DISTINCT FROM p_versjon
           OR v_gml.hjemmel IS DISTINCT FROM p_hjemmel
           OR v_gml.oppbevaring_dogn IS DISTINCT FROM p_oppbevaring_dogn
           OR v_gml.helseopplysninger IS DISTINCT FROM p_helse
           OR v_gml.gyldig_fra IS DISTINCT FROM p_gyldig_fra THEN
            RAISE EXCEPTION 'm53_registrer_regel: regel % finnes med'
                ' annet innhold', p_regel_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        IF p_gyldig_til IS NOT NULL
           AND v_gml.gyldig_til IS DISTINCT FROM p_gyldig_til THEN
            UPDATE public.hmsregelverk SET gyldig_til = p_gyldig_til
             WHERE tenant = p_tenant AND regel_id = p_regel_id;
            PERFORM public.m53_evidens(p_tenant, NULL,
                'regel_avviklet', p_aktor,
                jsonb_build_object('regel_id', p_regel_id::text,
                                   'gyldig_til', p_gyldig_til));
        END IF;
        RETURN false;
    END IF;

    INSERT INTO public.hmsregelverk
        (tenant, regel_id, avvikstype, versjon, hjemmel,
         oppbevaring_dogn, helseopplysninger, gyldig_fra, gyldig_til,
         opprettet_av)
    VALUES (p_tenant, p_regel_id, p_avvikstype, p_versjon, p_hjemmel,
            p_oppbevaring_dogn, p_helse, p_gyldig_fra, p_gyldig_til,
            p_aktor);
    v_ny := true;
    PERFORM public.m53_evidens(p_tenant, NULL, 'regel_registrert',
        p_aktor, jsonb_build_object('regel_id', p_regel_id::text,
                                    'avvikstype', p_avvikstype,
                                    'versjon', p_versjon));
    RETURN v_ny;
END $$;
REVOKE ALL ON FUNCTION m53_registrer_regel(TEXT, UUID, TEXT, TEXT,
    TEXT, INT, BOOLEAN, DATE, DATE, TEXT) FROM PUBLIC;

-- MATERIALITETSSJEKKEN, ETT STED.
--
-- `m53_meld_avvik` bruker den to ganger: på den vanlige
-- gjenspillveien, og i `unique_violation`-grenen når to samtidige
-- kall kappløp om samme id. To kopier av den samme sammenligningen
-- ville før eller siden gått fra hverandre, og da ville den ene veien
-- godtatt noe den andre nektet.
CREATE FUNCTION m53_krev_samme_avvik(
    p_rad public.hmsavvik, p_avvikstype TEXT, p_melderform TEXT,
    p_beskrivelse TEXT, p_sted TEXT, p_hendelsesdato DATE)
RETURNS VOID LANGUAGE plpgsql IMMUTABLE
SET search_path = pg_catalog AS $$
BEGIN
    IF p_rad.avvikstype IS DISTINCT FROM p_avvikstype
       OR p_rad.melderform IS DISTINCT FROM p_melderform
       OR p_rad.beskrivelse IS DISTINCT FROM btrim(p_beskrivelse)
       OR p_rad.sted IS DISTINCT FROM btrim(p_sted)
       OR p_rad.hendelsesdato IS DISTINCT FROM p_hendelsesdato THEN
        RAISE EXCEPTION 'm53_meld_avvik: avvik % finnes med annet'
            ' innhold', p_rad.avvik_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
END $$;
REVOKE ALL ON FUNCTION m53_krev_samme_avvik(public.hmsavvik, TEXT,
    TEXT, TEXT, TEXT, DATE) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- MOTTAKSDØRA — MODULENS TYNGSTE, OG DEN ENESTE SOM KAN LEKKE EN
-- VARSLER.
--
-- `p_melderform` STYRER ALT. Er den 'anonym', tar døra ikke imot en
-- aktør i det hele tatt: parameteren finnes, men den IGNORERES ikke —
-- den NEKTES. Forskjellen er hele poenget. En dør som stille kastet
-- aktøren, ville sett riktig ut i alle tester og feilet den dagen noen
-- endret rekkefølgen på argumentene.
--
-- AVVIKET OG MELDEREN SKRIVES I SAMME SETNING, av M-50s grunn (124):
-- var melderen et eget kall etterpå, ville et navngitt avvik uten
-- slettefrist eksistert i vinduet mellom de to.
-- ---------------------------------------------------------------------
CREATE FUNCTION m53_meld_avvik(
    p_tenant TEXT, p_avvik_id UUID, p_avvikstype TEXT,
    p_melderform TEXT, p_beskrivelse TEXT, p_sted TEXT,
    p_hendelsesdato DATE, p_melder_navn TEXT, p_melder_rolle TEXT,
    p_aktor TEXT)
RETURNS TABLE (oppbevaring_til DATE, oppbevaring_hjemmel TEXT,
               regelversjon TEXT, helseopplysninger BOOLEAN,
               kravversjon INT, melder_lagret BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_krav public.hmskrav%ROWTYPE;
    v_regel public.hmsregelverk%ROWTYPE;
    v_gml public.hmsavvik%ROWTYPE;
    v_til DATE;
    v_dato DATE := current_date;
    v_ts TIMESTAMPTZ;
    v_aktor TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm53_meld_avvik');

    IF p_melderform NOT IN ('navngitt', 'anonym') THEN
        RAISE EXCEPTION 'm53_meld_avvik: melderform må være navngitt'
            ' eller anonym (%)', p_melderform
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- VARSLERVERNET, FØRSTE NEKT. Et anonymt avvik med et navn eller
    -- en aktør er ikke et anonymt avvik, og døra skal SI DET i stedet
    -- for å kaste opplysningen i stillhet.
    IF p_melderform = 'anonym' THEN
        IF p_melder_navn IS NOT NULL OR p_melder_rolle IS NOT NULL THEN
            RAISE EXCEPTION 'm53_meld_avvik: et anonymt avvik kan ikke'
                ' bære et melderavn. Anonymitet er en TILSTAND, ikke'
                ' et tomt felt'
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        IF p_aktor IS NOT NULL THEN
            RAISE EXCEPTION 'm53_meld_avvik: et anonymt avvik kan ikke'
                ' bære en aktør. Kallet skal ikke sende den — heller'
                ' ikke for at loggen skal se komplett ut'
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        v_aktor := NULL;
        v_ts := NULL;
    ELSE
        IF p_melder_navn IS NULL OR btrim(p_melder_navn) = '' THEN
            RAISE EXCEPTION 'm53_meld_avvik: et navngitt avvik krever'
                ' et navn. Velg anonym i stedet'
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
            RAISE EXCEPTION 'm53_meld_avvik: et navngitt avvik krever'
                ' en aktør' USING ERRCODE = 'invalid_parameter_value';
        END IF;
        v_aktor := btrim(p_aktor);
        v_ts := now();
    END IF;

    -- ET AVVIK KAN IKKE HA SKJEDD I MORGEN. CHECKen i §1 stenger for
    -- det uansett, men en constraint-feil forteller den som melder at
    -- noe heter `hmsavvik_meldt_etter_hendelse` — og den som står med
    -- en skade og et skjema skal få vite hvilket felt som er galt.
    IF p_hendelsesdato > v_dato THEN
        RAISE EXCEPTION 'm53_meld_avvik: hendelsesdatoen (%) ligger'
            ' fram i tid', p_hendelsesdato
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- TENANTENS GRENSER MÅ FINNES. Uten dem er det ingen maksimal
    -- oppbevaringstid å måle hjemmelen mot (124s første nekt).
    SELECT * INTO v_krav FROM public.hmskrav WHERE tenant = p_tenant;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm53_meld_avvik: tenantens oppbevaringsgrenser'
            ' er ikke satt' USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- REGELEN SOM GJELDER I DAG. En avviklet versjon nektes: et avvik
    -- regnet under en regel som er lagt om, er et avvik med feil
    -- oppbevaringsfrist — og fristen er det eneste som holder raden.
    SELECT * INTO v_regel FROM public.hmsregelverk r
     WHERE r.tenant = p_tenant AND r.avvikstype = p_avvikstype
       AND public.m53_regel_gyldig(r.gyldig_fra, r.gyldig_til)
     ORDER BY r.gyldig_fra DESC LIMIT 1;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm53_meld_avvik: ingen gjeldende regel for %.'
            ' Et avvik uten oppbevaringshjemmel skal ikke kunne'
            ' oppstå', p_avvikstype
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- TAKET ER TENANTENS. En hjemmel på ti år i et register med ett
    -- års tak er ikke en plan; det er en omgåelse av planen (124s
    -- tredje nekt, ordrett).
    IF v_regel.oppbevaring_dogn > v_krav.oppbevaring_maks_dogn THEN
        RAISE EXCEPTION 'm53_meld_avvik: regelen krever % døgn, taket'
            ' er %', v_regel.oppbevaring_dogn,
            v_krav.oppbevaring_maks_dogn
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    v_til := v_dato + v_regel.oppbevaring_dogn;

    -- SP-2-MATERIALITET (m35/096-formen, som de fire andre i klyngen).
    --
    -- Kalleren utleder `p_avvik_id` av sin Idempotency-Key. Uten dette
    -- ville et gjenspill — en nettverksfeil, et dobbelttrykk, en
    -- retry i et mellomledd — truffet primærnøkkelen og gitt en
    -- feilmelding til et menneske som nettopp meldte en personskade.
    -- ET SKJEMA SOM FEILER PÅ ANDRE FORSØK ER ET SKJEMA FOLK SLUTTER
    -- Å BRUKE, og en HMS-melding som ikke ble sendt er hele skaden.
    --
    -- SAMME ID MED ANNET INNHOLD ER DERIMOT EN MATERIELL KONFLIKT, og
    -- den skal si fra: to ulike hendelser under samme nøkkel betyr at
    -- kalleren gjenbruker nøkler, og den nest siste ville forsvunnet.
    SELECT * INTO v_gml FROM public.hmsavvik
     WHERE tenant = p_tenant AND avvik_id = p_avvik_id FOR UPDATE;
    IF FOUND THEN
        PERFORM public.m53_krev_samme_avvik(
            v_gml, p_avvikstype, p_melderform, p_beskrivelse, p_sted,
            p_hendelsesdato);
        -- STILLE JA. Fristen og hjemmelen kommer fra RADEN, ikke fra
        -- en ny beregning: et gjenspill en uke senere skal ikke gi et
        -- annet svar enn det første kallet fikk.
        RETURN QUERY SELECT
            v_gml.oppbevaring_til, v_gml.oppbevaring_hjemmel,
            v_gml.regelversjon, v_gml.helseopplysninger,
            v_gml.kravversjon,
            EXISTS (SELECT 1 FROM public.hmsmelder m
                     WHERE m.tenant = p_tenant
                       AND m.avvik_id = p_avvik_id);
        RETURN;
    END IF;

    -- KAPPLØPET `FOR UPDATE` IKKE KAN FANGE (CodeRabbit).
    --
    -- En lås på en rad som IKKE FINNES tar ingen lås. To samtidige
    -- kall med samme Idempotency-Key ser derfor begge `NOT FOUND`,
    -- én INSERT vinner primærnøkkelen, og den andre ville fått en
    -- `unique_violation` — altså nøyaktig den klientfeilen
    -- gjenspillgrenen finnes for å hindre, i det ene tilfellet der
    -- den er mest sannsynlig: dobbelttrykket.
    --
    -- TAPEREN LESER RADEN VINNEREN SKREV og svarer som et gjenspill.
    -- `ON CONFLICT DO NOTHING` ville ikke duget: da mistet vi den
    -- materielle sjekken, og to ULIKE hendelser under samme nøkkel
    -- ville blitt til én i stillhet.
    BEGIN
        INSERT INTO public.hmsavvik
            (tenant, avvik_id, avvikstype, melderform, beskrivelse,
             sted, hendelsesdato, meldt_dato, meldt_ts, meldt_av,
             regel_id, regelversjon, oppbevaring_hjemmel,
             oppbevaring_dogn, oppbevaring_til, helseopplysninger,
             kravversjon)
        VALUES (p_tenant, p_avvik_id, p_avvikstype, p_melderform,
                btrim(p_beskrivelse), btrim(p_sted), p_hendelsesdato,
                v_dato, v_ts, v_aktor, v_regel.regel_id,
                v_regel.versjon, v_regel.hjemmel,
                v_regel.oppbevaring_dogn, v_til,
                v_regel.helseopplysninger, v_krav.versjon);
    EXCEPTION WHEN unique_violation THEN
        SELECT * INTO v_gml FROM public.hmsavvik
         WHERE tenant = p_tenant AND avvik_id = p_avvik_id FOR UPDATE;
        PERFORM public.m53_krev_samme_avvik(
            v_gml, p_avvikstype, p_melderform, p_beskrivelse, p_sted,
            p_hendelsesdato);
        RETURN QUERY SELECT
            v_gml.oppbevaring_til, v_gml.oppbevaring_hjemmel,
            v_gml.regelversjon, v_gml.helseopplysninger,
            v_gml.kravversjon,
            EXISTS (SELECT 1 FROM public.hmsmelder m
                     WHERE m.tenant = p_tenant
                       AND m.avvik_id = p_avvik_id);
        RETURN;
    END;

    -- SAMME SETNING, IKKE SAMME TRANSAKSJON. Melderraden skrives her,
    -- før noen kan lese avviket.
    IF p_melderform = 'navngitt' THEN
        INSERT INTO public.hmsmelder
            (tenant, avvik_id, navn, rolle, slettefrist)
        VALUES (p_tenant, p_avvik_id, btrim(p_melder_navn),
                nullif(btrim(coalesce(p_melder_rolle, '')), ''), v_til);
    END IF;

    -- EVIDENSKJEDEN BÆRER IKKE VARSLEREN. `v_aktor` er NULL for
    -- anonyme, og `revisjonslogg` er append-only siden 001: et navn
    -- som lekker inn her kan aldri fjernes igjen.
    PERFORM public.m53_evidens(p_tenant, p_avvik_id, 'avvik_meldt',
        v_aktor, jsonb_build_object(
            'avvikstype', p_avvikstype, 'melderform', p_melderform,
            'regelversjon', v_regel.versjon,
            'oppbevaring_til', v_til));

    RETURN QUERY SELECT v_til, v_regel.hjemmel, v_regel.versjon,
                        v_regel.helseopplysninger, v_krav.versjon,
                        (p_melderform = 'navngitt');
END $$;
REVOKE ALL ON FUNCTION m53_meld_avvik(TEXT, UUID, TEXT, TEXT, TEXT,
    TEXT, DATE, TEXT, TEXT, TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- TILTAKET, OG DEN ENESTE VEIEN TIL `behandlet`.
--
-- V1-DOMMEN `modulen_lukket_avvik_selv` betyr ikke at avvik aldri
-- lukkes — den betyr at INGEN AUTOMATIKK gjør det. Her lukkes de av at
-- et navngitt menneske registrerer et tiltak med `lukker = true`.
-- Sveipen har ingen vei hit, og det finnes ingen dør som setter
-- statusen uten et tiltak å vise til.
-- ---------------------------------------------------------------------
CREATE FUNCTION m53_registrer_tiltak(
    p_tenant TEXT, p_avvik_id UUID, p_tiltak_id UUID,
    p_beskrivelse TEXT, p_lukker BOOLEAN, p_utfort_dato DATE,
    p_aktor TEXT)
RETURNS TABLE (status TEXT, antall_tiltak BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_avvik public.hmsavvik%ROWTYPE;
    v_tiltak public.hmstiltak%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm53_registrer_tiltak');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm53_registrer_tiltak: et tiltak bærer navnet'
            ' til den som gjorde det'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- LÅS FØRST, LES ETTERPÅ. To saksbehandlere som lukker samtidig
    -- ville begge sett `apen` i sitt eget snapshot.
    SELECT * INTO v_avvik FROM public.hmsavvik
     WHERE tenant = p_tenant AND avvik_id = p_avvik_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm53_registrer_tiltak: ukjent avvik %',
            p_avvik_id USING ERRCODE = 'no_data_found';
    END IF;

    -- ET ANONYMISERT AVVIK TAR IKKE IMOT NYE TILTAK. Raden er et spor
    -- av en behandling som er avsluttet; et tiltak registrert etterpå
    -- ville vært saksbehandling på noe som ikke lenger finnes.
    IF v_avvik.anonymisert_ts IS NOT NULL THEN
        RAISE EXCEPTION 'm53_registrer_tiltak: avviket er anonymisert'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- SP-2-MATERIALITET, SAMME FORM SOM DE TO ANDRE DØRENE.
    --
    -- `ON CONFLICT DO NOTHING` sto her, og det er verre enn det ser
    -- ut (CodeRabbit): et gjenspill med ANNET innhold ville gjort
    -- ingenting og svart OK. Den som registrerte «stillaset er
    -- sikret» ville fått bekreftelse på et tiltak som aldri ble
    -- skrevet — og `hmstiltak` er append-only, så det finnes ingen
    -- vei til å rette det etterpå.
    SELECT * INTO v_tiltak FROM public.hmstiltak
     WHERE tenant = p_tenant AND tiltak_id = p_tiltak_id;
    IF FOUND THEN
        IF v_tiltak.avvik_id IS DISTINCT FROM p_avvik_id
           OR v_tiltak.beskrivelse IS DISTINCT FROM btrim(p_beskrivelse)
           OR v_tiltak.lukker IS DISTINCT FROM coalesce(p_lukker, false)
           OR v_tiltak.utfort_dato IS DISTINCT FROM p_utfort_dato THEN
            RAISE EXCEPTION 'm53_registrer_tiltak: tiltak % finnes med'
                ' annet innhold', p_tiltak_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
    ELSE
        INSERT INTO public.hmstiltak
            (tenant, avvik_id, tiltak_id, beskrivelse, lukker,
             utfort_dato, opprettet_av)
        VALUES (p_tenant, p_avvik_id, p_tiltak_id,
                btrim(p_beskrivelse), coalesce(p_lukker, false),
                p_utfort_dato, btrim(p_aktor));
    END IF;

    IF coalesce(p_lukker, false) AND v_avvik.status = 'apen' THEN
        UPDATE public.hmsavvik
           SET status = 'behandlet', behandlet_ts = now(),
               behandlet_av = btrim(p_aktor)
         WHERE tenant = p_tenant AND avvik_id = p_avvik_id;
    END IF;

    PERFORM public.m53_evidens(p_tenant, p_avvik_id,
        CASE WHEN coalesce(p_lukker, false) THEN 'avvik_behandlet'
             ELSE 'tiltak_registrert' END,
        btrim(p_aktor),
        jsonb_build_object('tiltak_id', p_tiltak_id::text,
                           'lukker', coalesce(p_lukker, false)));

    RETURN QUERY
    SELECT a.status, (SELECT count(*) FROM public.hmstiltak t
                       WHERE t.tenant = p_tenant
                         AND t.avvik_id = p_avvik_id)
      FROM public.hmsavvik a
     WHERE a.tenant = p_tenant AND a.avvik_id = p_avvik_id;
END $$;
REVOKE ALL ON FUNCTION m53_registrer_tiltak(TEXT, UUID, UUID, TEXT,
    BOOLEAN, DATE, TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- ANONYMISERINGEN — OG M-30-GRENSEN, GJORT MÅLBAR.
--
-- `docs/M53-M30-GRENSESNITTET.md` avklarte dette FØR koden: M-30
-- SLETTER ingenting. Den registrerer at noen har bedt om sletting og
-- måler fristen; utførelsen gjøres av den som eier lageret. Her.
--
-- ETTER oppbevaringsfristen er anonymisering vår egen plikt, og den
-- gjøres uten en sak å vise til.
--
-- FØR fristen er den et BRUDD på oppbevaringsplikten — med mindre en
-- M-30-sak gir grunnlaget. Da kreves henvisningen, og den skrives på
-- raden. Det er invarianten `sletting_uten_m30_avklaring`, og den er
-- den ENESTE måten en tidlig anonymisering kan skje uten å bli et
-- funn.
--
-- SLETTING FINNES IKKE. Raden blir et spor av en behandling, ikke en
-- person. At vi HAR hatt avviket er nøyaktig det Arbeidstilsynet
-- etterprøver; sletting ville fjernet beviset på at vi hadde det.
-- ---------------------------------------------------------------------
CREATE FUNCTION m53_anonymiser(
    p_tenant TEXT, p_avvik_id UUID, p_m30_sak_ref TEXT, p_aktor TEXT)
RETURNS TABLE (anonymisert BOOLEAN, for_tidlig BOOLEAN,
               oppbevaring_til DATE)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_avvik public.hmsavvik%ROWTYPE;
    v_tidlig BOOLEAN;
    v_ref TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm53_anonymiser');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm53_anonymiser: en anonymisering bærer navnet'
            ' til den som gjorde den'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT * INTO v_avvik FROM public.hmsavvik
     WHERE tenant = p_tenant AND avvik_id = p_avvik_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm53_anonymiser: ukjent avvik %', p_avvik_id
            USING ERRCODE = 'no_data_found';
    END IF;

    -- IDEMPOTENT — OG SVARET ER `true` (CodeRabbit).
    --
    -- Første utgave svarte `false` her, og API-et sender den første
    -- kolonnen videre som feltet `anonymisert`. Et andre kall mot en
    -- alt anonymisert rad svarte altså «anonymisert: false» om en rad
    -- som ER anonymisert — det motsatte av sannheten, til den som
    -- nettopp ba om det.
    --
    -- `false` ville betydd «ny handling utført», og det er et ANNET
    -- spørsmål enn det kalleren stiller. Kalleren spør om raden er
    -- anonymisert. Den er det.
    IF v_avvik.anonymisert_ts IS NOT NULL THEN
        RETURN QUERY SELECT true, false, v_avvik.oppbevaring_til;
        RETURN;
    END IF;

    v_tidlig := v_avvik.oppbevaring_til > current_date;
    v_ref := nullif(btrim(coalesce(p_m30_sak_ref, '')), '');

    IF v_tidlig AND v_ref IS NULL THEN
        RAISE EXCEPTION 'm53_anonymiser: oppbevaringsfristen løper til'
            ' % (% døgn igjen). En tidlig anonymisering krever en'
            ' M-30-sak å vise til — Arbeidstilsynet krever at avviket'
            ' bevares, og en sletting uten hjemmel er et bortkommet'
            ' bevis', v_avvik.oppbevaring_til,
            (v_avvik.oppbevaring_til - current_date)
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    UPDATE public.hmsavvik
       SET anonymisert_ts = now(), anonymisert_av = btrim(p_aktor),
           m30_sak_ref = v_ref, meldt_av = NULL, meldt_ts = NULL
     WHERE tenant = p_tenant AND avvik_id = p_avvik_id;

    -- MELDERRADEN TØMMES, DEN SLETTES IKKE — av samme grunn.
    UPDATE public.hmsmelder
       SET navn = NULL, rolle = NULL, anonymisert_ts = now()
     WHERE tenant = p_tenant AND avvik_id = p_avvik_id
       AND anonymisert_ts IS NULL;

    PERFORM public.m53_evidens(p_tenant, p_avvik_id, 'avvik_anonymisert',
        btrim(p_aktor),
        jsonb_build_object('for_tidlig', v_tidlig,
                           'm30_sak_ref', v_ref,
                           'oppbevaring_til', v_avvik.oppbevaring_til));

    RETURN QUERY SELECT true, v_tidlig, v_avvik.oppbevaring_til;
END $$;
REVOKE ALL ON FUNCTION m53_anonymiser(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

-- ---------------------------------------------------------------------
-- LUKKEDØRA FOR FUNN.
--
-- Aktøren er obligatorisk fra første linje (125s lærdom, som kom av at
-- 124 slapp en NULL gjennom og felte hele sveipetransaksjonen).
-- ---------------------------------------------------------------------
CREATE FUNCTION m53_lukk_funn(p_tenant TEXT, p_funn_id UUID,
                              p_notat TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_type TEXT;
    v_apen BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm53_lukk_funn');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm53_lukk_funn: en lukking bærer navnet til'
            ' den som lukket'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF length(btrim(coalesce(p_notat, ''))) < 4 THEN
        RAISE EXCEPTION 'm53_lukk_funn: et funn lukkes med en'
            ' begrunnelse, ikke med et klikk'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT funntype, apen INTO v_type, v_apen FROM public.hmsfunn
     WHERE tenant = p_tenant AND funn_id = p_funn_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm53_lukk_funn: ukjent funn %', p_funn_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_apen THEN
        RETURN;  -- idempotent
    END IF;

    -- TRE FUNN LUKKES IKKE HER, og lista bor i en funksjon slik at
    -- flaten leser den samme regelen som døra håndhever.
    --
    -- `oppbevaring_utlopt` lukkes av at raden ANONYMISERES.
    -- `for_tidlig_anonymisert` kan ikke lukkes av noen: det er
    -- registreringen av at et bevis ER borte, og en knapp som fjernet
    -- den ville fjernet nettopp det tilsynet spør etter.
    -- `avvik_mot_utlopt_regelverk` er klyngens delte funn.
    IF public.m53_funn_er_sveipens(v_type) THEN
        RAISE EXCEPTION 'm53_lukk_funn: % lukkes ikke av et menneske.'
            ' Det lukkes av at tilstanden er borte', v_type
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    UPDATE public.hmsfunn
       SET apen = false, lukket_ts = now(), lukket_av = btrim(p_aktor),
           lukkenotat = btrim(p_notat)
     WHERE tenant = p_tenant AND funn_id = p_funn_id;

    PERFORM public.m53_evidens(p_tenant, NULL, 'funn_lukket',
        btrim(p_aktor), jsonb_build_object('funn_id', p_funn_id::text,
                                           'funntype', v_type));
END $$;
REVOKE ALL ON FUNCTION m53_lukk_funn(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

-- =====================================================================
-- SVEIPEN.
--
-- TENANTLISTA ER BEGGE REGISTRENE (122s CodeRabbit-funn, gjentatt i
-- 123 og 124): en tenant som har avvik men intet regelverk — eller
-- omvendt — skal ikke hoppes over. Han er nettopp den som har
-- konfigurert halvveis.
--
-- MATERIALISERT FØR LØKKA (klynge 6s lærdom om den late markøren):
-- `set_config` inne i løkka ville flyttet tenantkonteksten under en
-- markør som ennå ikke var lest ferdig.
-- =====================================================================
CREATE FUNCTION m53_sveip_hms(p_maks_tenanter INT)
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
    v_n3 BIGINT;
BEGIN
    IF p_maks_tenanter IS NULL OR p_maks_tenanter < 1 THEN
        RAISE EXCEPTION 'm53_sveip_hms: maks_tenanter må være minst 1'
            ' (%)', p_maks_tenanter
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.tenant', '', true);

    SELECT array_agg(DISTINCT t ORDER BY t) INTO v_tenanter
      FROM (SELECT r.tenant AS t FROM public.hmsregelverk r
            UNION
            SELECT a.tenant FROM public.hmsavvik a) s;
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

        -- REGELNIVÅET.
        WITH krav AS (
            SELECT k.regelvarsel_dogn, k.versjon
              FROM public.hmskrav k WHERE k.tenant = v_t),
        kand AS (
            -- INGEN CROSS JOIN krav (121s funn): funnet handler om at
            -- kravet MANGLER, og et CROSS JOIN mot en tom rad ville
            -- gitt null rader — altså ingen funn om at det mangler.
            SELECT r.regel_id, 'ingen_krav'::text AS funntype,
                   NULL::int AS over_grense,
                   'oppbevaringsgrensene er tenantens og er ikke'
                   || ' satt'::text AS detalj,
                   NULL::int AS kravversjon
              FROM public.hmsregelverk r
             WHERE r.tenant = v_t AND NOT EXISTS (SELECT 1 FROM krav)
            UNION ALL
            SELECT r.regel_id, 'regelverk_utlopt',
                   (current_date - r.gyldig_til),
                   r.avvikstype || ' ' || r.versjon, NULL
              FROM public.hmsregelverk r
             WHERE r.tenant = v_t AND r.gyldig_til IS NOT NULL
               AND r.gyldig_til < current_date
               -- ETTERFØLGER-UNNTAKET GJELDER PÅ REGELNIVÅET: er en ny
               -- versjon i kraft for samme avvikstype, er den gamle
               -- historikk og ikke et hull.
               AND NOT EXISTS (
                   SELECT 1 FROM public.hmsregelverk r2
                    WHERE r2.tenant = v_t
                      AND r2.avvikstype = r.avvikstype
                      AND public.m53_regel_gyldig(r2.gyldig_fra,
                                                  r2.gyldig_til))
            UNION ALL
            SELECT r.regel_id, 'regelverk_utloper_snart',
                   (r.gyldig_til - current_date),
                   r.avvikstype || ' ' || r.versjon, kr.versjon
              FROM public.hmsregelverk r CROSS JOIN krav kr
             WHERE r.tenant = v_t AND r.gyldig_til IS NOT NULL
               AND r.gyldig_til >= current_date
               AND r.gyldig_til <= current_date
                   + make_interval(days => kr.regelvarsel_dogn)
        ),
        skrevet AS (
            INSERT INTO public.hmsfunn
                (tenant, funn_id, regel_id, funntype, over_grense,
                 detalj, kravversjon)
            SELECT v_t, gen_random_uuid(), k.regel_id, k.funntype,
                   k.over_grense, k.detalj, k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, regel_id, funntype)
                WHERE regel_id IS NOT NULL
            -- ET MENNESKES LUKKING SKAL STÅ. Formen er 124s, og
            -- 125/126s vakt gjør den sann uansett hva som står her —
            -- men den står her likevel, fordi en leser skal se
            -- regelen der handlingen er.
            DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                detalj = EXCLUDED.detalj,
                kravversjon = EXCLUDED.kravversjon,
                sist_sett_sveip = now(),
                apen = (public.hmsfunn.apen
                        OR public.hmsfunn.lukket_av = 'm53_sveip'),
                lukket_ts = CASE
                    WHEN public.hmsfunn.apen
                      OR public.hmsfunn.lukket_av = 'm53_sveip'
                    THEN NULL ELSE public.hmsfunn.lukket_ts END,
                lukket_av = CASE
                    WHEN public.hmsfunn.apen
                      OR public.hmsfunn.lukket_av = 'm53_sveip'
                    THEN NULL ELSE public.hmsfunn.lukket_av END,
                lukkenotat = CASE
                    WHEN public.hmsfunn.apen
                      OR public.hmsfunn.lukket_av = 'm53_sveip'
                    THEN NULL ELSE public.hmsfunn.lukkenotat END
            RETURNING (xmax = 0) AS var_ny),
        lukket AS (
            -- LUKKINGEN LESER SAMME `kand` SOM SKRIVINGEN.
            --
            -- Første utgave hadde et eget `gjeldende`-CTE som gjentok
            -- predikatene, og lukket bare to av funntypene: et
            -- «utløper snart» ble stående åpent for alltid etter at
            -- datoen passerte, ved siden av «utløpt» (CodeRabbit).
            -- En funnliste som vokser er en funnliste folk lærer seg
            -- å overse, og da forsvinner de viktige med dem.
            --
            -- Å GJENTA PREDIKATENE VAR SELVE FEILEN. Nå kan de to
            -- ikke gå fra hverandre: det som ikke lenger er en
            -- kandidat, er per definisjon lukket.
            --
            -- RADENE ER DISJUNKTE fra `skrevet`s: den rører dem som
            -- ER i `kand`, denne dem som IKKE er det. To
            -- datamodifiserende CTE-er som traff samme rad i én
            -- setning ville vært udefinert.
            UPDATE public.hmsfunn f
               SET apen = false, lukket_ts = now(),
                   lukket_av = 'm53_sveip',
                   lukkenotat = 'tilstanden er ikke lenger til stede'
             WHERE f.tenant = v_t AND f.apen
               AND f.regel_id IS NOT NULL
               AND f.funntype IN ('ingen_krav', 'regelverk_utlopt',
                                  'regelverk_utloper_snart')
               AND NOT EXISTS (
                   SELECT 1 FROM kand k
                    WHERE k.regel_id = f.regel_id
                      AND k.funntype = f.funntype)
            RETURNING 1)
        SELECT (SELECT count(*) FILTER (WHERE var_ny) FROM skrevet),
               (SELECT count(*) FILTER (WHERE NOT var_ny) FROM skrevet),
               (SELECT count(*) FROM lukket)
          INTO v_n, v_n2, v_n3;
        -- `INTO` SETTER, den akkumulerer ikke (112s retting).
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- AVVIKSNIVÅET. Fem funntyper, og tre av dem kan ingen lukke.
        --
        -- INGEN ETTERFØLGER-UNNTAK HER (123s lærdom, funnet av min
        -- egen port der): et avvik regnet under versjon 1 er fortsatt
        -- regnet under versjon 1 etter at versjon 2 er registrert.
        -- Forsvant funnet når noen la inn en ny regel, ville det
        -- forsvunnet fordi problemet ble STØRRE.
        WITH krav AS (
            SELECT k.tiltaksfrist_dogn, k.oppbevaringsvarsel_dogn,
                   k.versjon FROM public.hmskrav k WHERE k.tenant = v_t),
        kand AS (
            SELECT a.avvik_id, 'ingen_krav'::text AS funntype,
                   NULL::int AS over_grense,
                   'oppbevaringsgrensene er tenantens og er ikke'
                   || ' satt'::text AS detalj,
                   NULL::int AS kravversjon
              FROM public.hmsavvik a
             WHERE a.tenant = v_t AND NOT EXISTS (SELECT 1 FROM krav)
            UNION ALL
            -- MODULENS EGEN GRUNN TIL Å FINNES: et avvik ingen har
            -- gjort noe med. Et HMS-mottak som tok imot og ikke sa
            -- fra, ville vært en postkasse.
            SELECT a.avvik_id, 'avvik_ubehandlet',
                   (current_date - a.meldt_dato - kr.tiltaksfrist_dogn),
                   a.avvikstype || ' meldt ' || a.meldt_dato::text,
                   kr.versjon
              FROM public.hmsavvik a CROSS JOIN krav kr
             WHERE a.tenant = v_t AND a.status = 'apen'
               AND a.anonymisert_ts IS NULL
               AND a.meldt_dato + kr.tiltaksfrist_dogn < current_date
            UNION ALL
            -- KLYNGENS DELTE FUNN. En foreldet regel ser nøyaktig ut
            -- som en riktig regel.
            SELECT a.avvik_id, 'avvik_mot_utlopt_regelverk',
                   (current_date - r.gyldig_til),
                   a.avvikstype || ' ' || a.regelversjon, NULL
              FROM public.hmsavvik a
              JOIN public.hmsregelverk r
                ON r.tenant = a.tenant AND r.regel_id = a.regel_id
             WHERE a.tenant = v_t AND a.anonymisert_ts IS NULL
               AND r.gyldig_til IS NOT NULL
               AND r.gyldig_til < current_date
            UNION ALL
            SELECT a.avvik_id, 'oppbevaring_naermer_seg',
                   (a.oppbevaring_til - current_date),
                   a.oppbevaring_hjemmel, kr.versjon
              FROM public.hmsavvik a CROSS JOIN krav kr
             WHERE a.tenant = v_t AND a.anonymisert_ts IS NULL
               AND a.oppbevaring_til >= current_date
               AND a.oppbevaring_til <= current_date
                   + make_interval(days => kr.oppbevaringsvarsel_dogn)
            UNION ALL
            -- FUNNET INGEN KAN LUKKE, RETNING ÉN: vi holder en
            -- identifiserbar HMS-opplysning lenger enn vår egen
            -- hjemmel rekker. Lukkes av at raden ANONYMISERES.
            SELECT a.avvik_id, 'oppbevaring_utlopt',
                   (current_date - a.oppbevaring_til),
                   a.oppbevaring_hjemmel, NULL
              FROM public.hmsavvik a
             WHERE a.tenant = v_t AND a.anonymisert_ts IS NULL
               AND a.oppbevaring_til < current_date
            UNION ALL
            -- FUNNET INGEN KAN LUKKE, RETNING TO — og den er den
            -- vanskeligste å skrive ned:
            --
            -- Raden ble anonymisert FØR fristen. Døra tillater det
            -- BARE med en M-30-sak, så dette er som regel den LOVLIGE
            -- veien: noen krevde sletting, og art. 17 ga dem rett.
            --
            -- DET ER FORTSATT ET HULL. Arbeidstilsynet spør ikke om
            -- hvorfor beviset er borte; det spør om det er der. At vi
            -- gjorde det riktige og likevel mangler dokumentasjonen,
            -- er nøyaktig den opplysningen et tilsyn skal få lese —
            -- og en knapp som fjernet den ville fjernet det eneste
            -- sporet av at avviket noen gang fantes.
            SELECT a.avvik_id, 'for_tidlig_anonymisert',
                   (a.oppbevaring_til - a.anonymisert_ts::date),
                   coalesce('M-30-sak ' || a.m30_sak_ref,
                            'uten M-30-henvisning'), NULL
              FROM public.hmsavvik a
             WHERE a.tenant = v_t AND a.anonymisert_ts IS NOT NULL
               AND a.oppbevaring_til > a.anonymisert_ts::date
        ),
        skrevet AS (
            INSERT INTO public.hmsfunn
                (tenant, funn_id, avvik_id, funntype, over_grense,
                 detalj, kravversjon)
            SELECT v_t, gen_random_uuid(), k.avvik_id, k.funntype,
                   k.over_grense, k.detalj, k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, avvik_id, funntype)
                WHERE avvik_id IS NOT NULL
            DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                detalj = EXCLUDED.detalj,
                kravversjon = EXCLUDED.kravversjon,
                sist_sett_sveip = now(),
                apen = (public.hmsfunn.apen
                        OR public.hmsfunn.lukket_av = 'm53_sveip'),
                lukket_ts = CASE
                    WHEN public.hmsfunn.apen
                      OR public.hmsfunn.lukket_av = 'm53_sveip'
                    THEN NULL ELSE public.hmsfunn.lukket_ts END,
                lukket_av = CASE
                    WHEN public.hmsfunn.apen
                      OR public.hmsfunn.lukket_av = 'm53_sveip'
                    THEN NULL ELSE public.hmsfunn.lukket_av END,
                lukkenotat = CASE
                    WHEN public.hmsfunn.apen
                      OR public.hmsfunn.lukket_av = 'm53_sveip'
                    THEN NULL ELSE public.hmsfunn.lukkenotat END
            RETURNING (xmax = 0) AS var_ny),
        lukket AS (
            -- SAMME GREP SOM PÅ REGELNIVÅET, og her betyr det mest:
            -- `oppbevaring_naermer_seg` skal lukkes i det øyeblikket
            -- fristen PASSERER, for da er det `oppbevaring_utlopt`
            -- som gjelder. Et lukket «nærmer seg» skjuler ikke et
            -- «passert» — de er to ulike rader.
            --
            -- `avvik_mot_utlopt_regelverk` og `for_tidlig_anonymisert`
            -- står IKKE i lista, og det er ikke en forglemmelse: de
            -- er etiketter, ikke oppgaver. Se avsnittet under sveipen.
            UPDATE public.hmsfunn f
               SET apen = false, lukket_ts = now(),
                   lukket_av = 'm53_sveip',
                   lukkenotat = 'tilstanden er ikke lenger til stede'
             WHERE f.tenant = v_t AND f.apen
               AND f.avvik_id IS NOT NULL
               AND f.funntype IN ('ingen_krav', 'avvik_ubehandlet',
                                  'oppbevaring_naermer_seg',
                                  'oppbevaring_utlopt')
               AND NOT EXISTS (
                   SELECT 1 FROM kand k
                    WHERE k.avvik_id = f.avvik_id
                      AND k.funntype = f.funntype)
            RETURNING 1)
        SELECT (SELECT count(*) FILTER (WHERE var_ny) FROM skrevet),
               (SELECT count(*) FILTER (WHERE NOT var_ny) FROM skrevet),
               (SELECT count(*) FROM lukket)
          INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- LUKKINGEN: funn som ikke lenger gjelder. Sveipen lukker BARE
        -- sine egne og de menneskene ikke har rørt — 125/126s vakt sørger
        -- for resten.
    END LOOP;

    PERFORM set_config('disponit.tenant', '', true);
    RETURN QUERY SELECT v_antall, v_nye, v_oppdaterte, v_lukket;
END $$;
REVOKE ALL ON FUNCTION m53_sveip_hms(INT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- ET ORD OM `avvik_mot_utlopt_regelverk`, SOM ALDRI LUKKES.
--
-- De to andre sveipefunnene lukkes av at tilstanden er borte: avviket
-- blir behandlet, raden blir anonymisert. Dette gjør det ikke, og det
-- er ikke en forglemmelse.
--
-- Avviket bærer regelversjonen SNAPSHOTET, og snapshotet er frosset.
-- Blir regelen avviklet, er setningen «denne oppbevaringsfristen ble
-- regnet under en regel som ikke lenger gjelder» sann for alltid.
-- Funnet er ikke en oppgave noen kan gjøre ferdig — det er en ETIKETT,
-- og den skal henge ved raden helt til raden er anonymisert.
--
-- PRISEN ER ÆRLIG: avvikler en tenant et regelverk, får hvert avvik
-- som ble regnet under det ett funn. Det er den riktige mengden. Ett
-- funn per avvik, ikke ett per natt — og den som ser hundre av dem har
-- fått vite nøyaktig det han skulle: hundre avvik hviler på en regel
-- ingen står inne for lenger.
-- ---------------------------------------------------------------------

-- =====================================================================
-- LESEDØRENE.
--
-- INGEN AV DEM RETURNERER `meldt_av` FOR ET ANONYMT AVVIK, og det er
-- ikke fordi kolonnen er NULL — det er fordi den ALDRI ble skrevet.
-- Forskjellen er den samme som mellom å låse skuffen og å ikke ha
-- papiret.
-- =====================================================================

CREATE FUNCTION m53_bildet(p_tenant TEXT)
RETURNS TABLE (avvik BIGINT, apne BIGINT, ubehandlet_over_frist BIGINT,
               anonyme BIGINT, med_helseopplysninger BIGINT,
               levende BIGINT, oppbevaring_passert BIGINT,
               oppbevaring_naer BIGINT, regler BIGINT,
               gyldige_regler BIGINT, apne_funn BIGINT,
               har_krav BOOLEAN, oppbevaring_maks_dogn INT,
               oppbevaringsvarsel_dogn INT, tiltaksfrist_dogn INT,
               regelvarsel_dogn INT, kravversjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm53_bildet');
    RETURN QUERY
    WITH k AS (SELECT * FROM public.hmskrav WHERE tenant = p_tenant),
    levende AS (
        SELECT a.oppbevaring_til FROM public.hmsavvik a
         WHERE a.tenant = p_tenant AND a.anonymisert_ts IS NULL)
    SELECT (SELECT count(*) FROM public.hmsavvik a
             WHERE a.tenant = p_tenant),
           (SELECT count(*) FROM public.hmsavvik a
             WHERE a.tenant = p_tenant AND a.status = 'apen'),
           (SELECT count(*) FROM public.hmsavvik a CROSS JOIN k
             WHERE a.tenant = p_tenant AND a.status = 'apen'
               AND a.anonymisert_ts IS NULL
               AND a.meldt_dato + k.tiltaksfrist_dogn < current_date),
           (SELECT count(*) FROM public.hmsavvik a
             WHERE a.tenant = p_tenant AND a.melderform = 'anonym'),
           (SELECT count(*) FROM public.hmsavvik a
             WHERE a.tenant = p_tenant AND a.helseopplysninger),
           (SELECT count(*) FROM levende),
           (SELECT count(*) FROM levende l
             WHERE l.oppbevaring_til < current_date),
           (SELECT count(*) FROM levende l CROSS JOIN k
             WHERE l.oppbevaring_til >= current_date
               AND l.oppbevaring_til <= current_date
                   + make_interval(days => k.oppbevaringsvarsel_dogn)),
           (SELECT count(*) FROM public.hmsregelverk r
             WHERE r.tenant = p_tenant),
           (SELECT count(*) FROM public.hmsregelverk r
             WHERE r.tenant = p_tenant
               AND public.m53_regel_gyldig(r.gyldig_fra, r.gyldig_til)),
           (SELECT count(*) FROM public.hmsfunn f
             WHERE f.tenant = p_tenant AND f.apen),
           (SELECT count(*) > 0 FROM k),
           -- ALLE FIRE GRENSENE, ikke én av dem (123s funn: et skjema
           -- som viser mindre enn det lagrer er en felle — flaten
           -- forhåndsutfyller fra dette bildet).
           (SELECT k.oppbevaring_maks_dogn FROM k),
           (SELECT k.oppbevaringsvarsel_dogn FROM k),
           (SELECT k.tiltaksfrist_dogn FROM k),
           (SELECT k.regelvarsel_dogn FROM k),
           (SELECT k.versjon FROM k);
END $$;
REVOKE ALL ON FUNCTION m53_bildet(TEXT) FROM PUBLIC;

-- AVVIKSLISTA. `melder_navn` er NULL for anonyme OG for anonymiserte,
-- og `melderform` sier hvilket av de to det er. Uten den kolonnen
-- ville flaten ikke kunnet skille «meldte anonymt» fra «navnet er
-- slettet», og det er to helt ulike opplysninger.
CREATE FUNCTION m53_avvikene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (avvik_id UUID, avvikstype TEXT, melderform TEXT,
               beskrivelse TEXT, sted TEXT, hendelsesdato DATE,
               meldt_dato DATE, status TEXT, behandlet_av TEXT,
               regelversjon TEXT, oppbevaring_hjemmel TEXT,
               oppbevaring_til DATE, dogn_til_oppbevaring INT,
               helseopplysninger BOOLEAN, melder_navn TEXT,
               anonymisert BOOLEAN, m30_sak_ref TEXT,
               antall_tiltak BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm53_avvikene');
    RETURN QUERY
    SELECT a.avvik_id, a.avvikstype, a.melderform, a.beskrivelse,
           a.sted, a.hendelsesdato, a.meldt_dato, a.status,
           a.behandlet_av, a.regelversjon, a.oppbevaring_hjemmel,
           a.oppbevaring_til,
           (a.oppbevaring_til - current_date)::int,
           a.helseopplysninger, m.navn,
           (a.anonymisert_ts IS NOT NULL), a.m30_sak_ref,
           (SELECT count(*) FROM public.hmstiltak t
             WHERE t.tenant = p_tenant AND t.avvik_id = a.avvik_id)
      FROM public.hmsavvik a
      LEFT JOIN public.hmsmelder m
        ON m.tenant = a.tenant AND m.avvik_id = a.avvik_id
     WHERE a.tenant = p_tenant
     ORDER BY a.meldt_dato DESC, a.avvik_id
     LIMIT p_maks;
END $$;
REVOKE ALL ON FUNCTION m53_avvikene(TEXT, INT) FROM PUBLIC;

-- OPPBEVARINGSGRUNNLAGET — SETNINGEN SAKSBEHANDLEREN LIMER INN.
--
-- Se `docs/M53-M30-GRENSESNITTET.md`. `personvernsak.status` er
-- ('apen','besvart','avvist') og `personvernsak_lager` har INGEN
-- status per lager: en slettesak som dekker fem lagre får ETT svar.
-- GDPR art. 17 nr. 3 bokstav b gjør det DELTE svaret til det riktige,
-- og den setningen har M-30 ikke plass til.
--
-- VI UTVIDER IKKE M-30. Migrasjonene er forward-only, og å utvide et
-- lukket sett i en merget modul for en modul som ennå ikke fantes,
-- ville vært å endre den fungerende for den ubygde. I stedet gjør vi
-- avslaget SITERBART: hjemmelen, datoen og regelversjonen, ferdig
-- formulert, i det øyeblikket svaret skrives.
CREATE FUNCTION m53_oppbevaringsgrunnlag(p_tenant TEXT, p_avvik_id UUID)
RETURNS TABLE (hjemmel TEXT, oppbevaring_til DATE, regelversjon TEXT,
               helseopplysninger BOOLEAN, kan_anonymiseres_naa BOOLEAN,
               dogn_igjen INT, alt_anonymisert BOOLEAN, setning TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_a public.hmsavvik%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm53_oppbevaringsgrunnlag');
    SELECT * INTO v_a FROM public.hmsavvik
     WHERE tenant = p_tenant AND avvik_id = p_avvik_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm53_oppbevaringsgrunnlag: ukjent avvik %',
            p_avvik_id USING ERRCODE = 'no_data_found';
    END IF;
    RETURN QUERY SELECT
        v_a.oppbevaring_hjemmel, v_a.oppbevaring_til, v_a.regelversjon,
        v_a.helseopplysninger,
        (v_a.oppbevaring_til <= current_date),
        (v_a.oppbevaring_til - current_date)::int,
        (v_a.anonymisert_ts IS NOT NULL),
        CASE WHEN v_a.anonymisert_ts IS NOT NULL THEN
            'Opplysningen er allerede anonymisert.'
        WHEN v_a.oppbevaring_til <= current_date THEN
            'Oppbevaringsfristen løp ut ' || v_a.oppbevaring_til::text
            || '. Opplysningen kan anonymiseres.'
        ELSE
            'Opplysningen er omfattet av oppbevaringsplikt etter '
            || v_a.oppbevaring_hjemmel || ' (regelversjon '
            || v_a.regelversjon || ') og kan ikke slettes før '
            || v_a.oppbevaring_til::text || '. Jf. personvern-'
            || 'forordningen art. 17 nr. 3 bokstav b.'
        END;
END $$;
REVOKE ALL ON FUNCTION m53_oppbevaringsgrunnlag(TEXT, UUID)
    FROM PUBLIC;

-- FUNNENE. `kan_lukkes` følger med hver rad, slik at flaten leser den
-- samme regelen som døra håndhever i stedet for å kopiere den (124s
-- form).
CREATE FUNCTION m53_funnene(p_tenant TEXT, p_apne BOOLEAN)
RETURNS TABLE (funn_id UUID, funntype TEXT, regel_id UUID,
               avvik_id UUID, over_grense INT, detalj TEXT,
               kravversjon INT, forst_sett TIMESTAMPTZ,
               sist_sett_sveip TIMESTAMPTZ, apen BOOLEAN,
               lukket_ts TIMESTAMPTZ, lukket_av TEXT,
               lukkenotat TEXT, kan_lukkes BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm53_funnene');
    RETURN QUERY
    SELECT f.funn_id, f.funntype, f.regel_id, f.avvik_id,
           f.over_grense, f.detalj, f.kravversjon, f.forst_sett,
           f.sist_sett_sveip, f.apen, f.lukket_ts, f.lukket_av,
           f.lukkenotat,
           NOT public.m53_funn_er_sveipens(f.funntype)
      FROM public.hmsfunn f
     WHERE f.tenant = p_tenant
       AND (p_apne IS NULL OR f.apen = p_apne)
     ORDER BY f.apen DESC, f.forst_sett DESC;
END $$;
REVOKE ALL ON FUNCTION m53_funnene(TEXT, BOOLEAN) FROM PUBLIC;

CREATE FUNCTION m53_regelverket(p_tenant TEXT)
RETURNS TABLE (regel_id UUID, avvikstype TEXT, versjon TEXT,
               hjemmel TEXT, oppbevaring_dogn INT,
               helseopplysninger BOOLEAN, gyldig_fra DATE,
               gyldig_til DATE, gyldig_naa BOOLEAN,
               dogn_til_utlop INT, antall_avvik BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm53_regelverket');
    RETURN QUERY
    SELECT r.regel_id, r.avvikstype, r.versjon, r.hjemmel,
           r.oppbevaring_dogn, r.helseopplysninger, r.gyldig_fra,
           r.gyldig_til,
           public.m53_regel_gyldig(r.gyldig_fra, r.gyldig_til),
           CASE WHEN r.gyldig_til IS NULL THEN NULL
                ELSE (r.gyldig_til - current_date)::int END,
           (SELECT count(*) FROM public.hmsavvik a
             WHERE a.tenant = p_tenant AND a.regel_id = r.regel_id)
      FROM public.hmsregelverk r
     WHERE r.tenant = p_tenant
     ORDER BY r.avvikstype, r.gyldig_fra DESC;
END $$;
REVOKE ALL ON FUNCTION m53_regelverket(TEXT) FROM PUBLIC;

CREATE FUNCTION m53_tiltakene(p_tenant TEXT, p_avvik_id UUID)
RETURNS TABLE (tiltak_id UUID, beskrivelse TEXT, lukker BOOLEAN,
               utfort_dato DATE, opprettet TIMESTAMPTZ,
               opprettet_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm53_tiltakene');
    RETURN QUERY
    SELECT t.tiltak_id, t.beskrivelse, t.lukker, t.utfort_dato,
           t.opprettet, t.opprettet_av
      FROM public.hmstiltak t
     WHERE t.tenant = p_tenant AND t.avvik_id = p_avvik_id
     ORDER BY t.utfort_dato, t.opprettet;
END $$;
REVOKE ALL ON FUNCTION m53_tiltakene(TEXT, UUID) FROM PUBLIC;

-- =====================================================================
-- RETTIGHETENE, RADVAKTENE OG FRYSINGEN.
-- =====================================================================

RESET ROLE;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['hmskrav', 'hmsregelverk', 'hmsavvik',
                             'hmsmelder', 'hmstiltak', 'hmsfunn']
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
                       ' disponit_hms_eier', t);
    END LOOP;
END $$;

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER (111s form, 112-124):
-- bare FOR SELECT, bare til eieren, bare uten tenantkontekst — og på
-- BEGGE registrene sveipens tenantliste leser (122s lærdom).
CREATE POLICY m53_sveip_tenantliste ON hmsregelverk
    FOR SELECT TO disponit_hms_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);
CREATE POLICY m53_sveip_tenantliste_avvik ON hmsavvik
    FOR SELECT TO disponit_hms_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- HISTORIKKTABELLENE FÅR IKKE UPDATE.
--
-- `hmstiltak` er HELT lukket: et tiltak kan bare OPPSTÅ. Hva som
-- FAKTISK ble gjort, og når, er det Arbeidstilsynet etterprøver — og
-- et tiltak som lot seg redigere i ettertid er ikke et tiltak, det er
-- en forklaring man finner på når noen spør (124s formulering om
-- formålet, som gjelder ordrett her).
REVOKE UPDATE ON public.hmstiltak FROM disponit_hms_eier;

-- `hmsregelverk` FÅR BARE ENDRE `gyldig_til` (121s dom, 122-124s form).
REVOKE UPDATE ON public.hmsregelverk FROM disponit_hms_eier;
GRANT UPDATE (gyldig_til) ON public.hmsregelverk TO disponit_hms_eier;

-- `hmsavvik` FÅR BARE BEHANDLES OG ANONYMISERES.
--
-- Beskrivelsen, typen, hjemmelen, regelversjonen og
-- oppbevaringsfristen er frosset. FRISTEN SÆRLIG: kunne den flyttes,
-- ville «oppbevart etter egen frist» vært et funn man kunne fjerne ved
-- å utsette fristen — et gjerde som forsvant når man dyttet på det.
--
-- `meldt_av` og `meldt_ts` står i listen FORDI anonymiseringen tømmer
-- dem. Ingen annen skrivevei setter dem: de skrives ved fødselen eller
-- aldri.
REVOKE UPDATE ON public.hmsavvik FROM disponit_hms_eier;
GRANT UPDATE (status, behandlet_ts, behandlet_av, anonymisert_ts,
              anonymisert_av, m30_sak_ref, meldt_av, meldt_ts)
    ON public.hmsavvik TO disponit_hms_eier;

-- `hmsmelder` FÅR BARE ANONYMISERES.
REVOKE UPDATE ON public.hmsmelder FROM disponit_hms_eier;
GRANT UPDATE (navn, rolle, anonymisert_ts) ON public.hmsmelder
    TO disponit_hms_eier;

-- INGEN AV TABELLENE FÅR SLETTES. `DELETE` står ikke i noen GRANT over
-- — listen er `SELECT, INSERT, UPDATE`. Det står her fordi et fravær
-- er lettere å overse enn en setning, og porten leser begge.
--
-- FOR `hmsavvik` ER DET EN DOM: at vi HAR hatt avviket er nøyaktig det
-- Arbeidstilsynet etterprøver. Anonymisering fjerner opplysningen;
-- sletting ville fjernet beviset på at vi hadde den.

-- RADVAKTENE. Triggerne settes av MIGRATOREN, som eier tabellene:
-- `CREATE TRIGGER` krever eierskap, og en modulrolle som kunne sette
-- dem kunne også ta dem av igjen.
CREATE FUNCTION m53_regel_frosset()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.regel_id IS DISTINCT FROM OLD.regel_id
       OR NEW.avvikstype IS DISTINCT FROM OLD.avvikstype
       OR NEW.versjon IS DISTINCT FROM OLD.versjon
       OR NEW.hjemmel IS DISTINCT FROM OLD.hjemmel
       OR NEW.oppbevaring_dogn IS DISTINCT FROM OLD.oppbevaring_dogn
       OR NEW.helseopplysninger IS DISTINCT FROM OLD.helseopplysninger
       OR NEW.gyldig_fra IS DISTINCT FROM OLD.gyldig_fra
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av THEN
        RAISE EXCEPTION 'hmsregelverk: identiteten er frosset — bare'
            ' gyldig_til kan settes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER hmsregelverk_frosset BEFORE UPDATE ON hmsregelverk
    FOR EACH ROW EXECUTE FUNCTION m53_regel_frosset();

-- AVVIKET: bare status og anonymisering er lovlige endringer, og
-- anonymiseringen går ÉN VEI.
CREATE FUNCTION m53_avvik_frosset()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.avvik_id IS DISTINCT FROM OLD.avvik_id
       OR NEW.avvikstype IS DISTINCT FROM OLD.avvikstype
       OR NEW.melderform IS DISTINCT FROM OLD.melderform
       OR NEW.beskrivelse IS DISTINCT FROM OLD.beskrivelse
       OR NEW.sted IS DISTINCT FROM OLD.sted
       OR NEW.hendelsesdato IS DISTINCT FROM OLD.hendelsesdato
       OR NEW.meldt_dato IS DISTINCT FROM OLD.meldt_dato
       OR NEW.regel_id IS DISTINCT FROM OLD.regel_id
       OR NEW.regelversjon IS DISTINCT FROM OLD.regelversjon
       OR NEW.oppbevaring_hjemmel IS DISTINCT FROM
          OLD.oppbevaring_hjemmel
       OR NEW.oppbevaring_dogn IS DISTINCT FROM OLD.oppbevaring_dogn
       OR NEW.oppbevaring_til IS DISTINCT FROM OLD.oppbevaring_til
       OR NEW.helseopplysninger IS DISTINCT FROM OLD.helseopplysninger
       OR NEW.kravversjon IS DISTINCT FROM OLD.kravversjon
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'hmsavvik: raden er frosset — bare behandling'
            ' og anonymisering er lovlige endringer'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.anonymisert_ts IS NOT NULL
       AND NEW.anonymisert_ts IS DISTINCT FROM OLD.anonymisert_ts THEN
        RAISE EXCEPTION 'hmsavvik: en anonymisert rad kan ikke gjøres'
            ' om igjen' USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- AKTØREN KAN BARE FORSVINNE, ALDRI KOMME TILBAKE. Uten denne
    -- kunne en anonymisert rad få navnet sitt igjen — og da var
    -- anonymiseringen aldri ekte (124s form).
    IF NEW.meldt_av IS NOT NULL AND OLD.meldt_av IS NULL THEN
        RAISE EXCEPTION 'hmsavvik: en melder kan ikke settes tilbake'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- OG ET BEHANDLET AVVIK KAN IKKE ÅPNES IGJEN. Et nytt tiltak på
    -- en gammel sak er et nytt avvik, ikke en omgjøring: statusen er
    -- historikk, og historikken overskrives aldri.
    IF OLD.status = 'behandlet' AND NEW.status = 'apen' THEN
        RAISE EXCEPTION 'hmsavvik: et behandlet avvik kan ikke åpnes'
            ' igjen — meld et nytt'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER hmsavvik_frosset BEFORE UPDATE ON hmsavvik
    FOR EACH ROW EXECUTE FUNCTION m53_avvik_frosset();

-- MELDERRADEN: bare veien FRA navn TIL anonymisert er lovlig.
CREATE FUNCTION m53_melder_frosset()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.avvik_id IS DISTINCT FROM OLD.avvik_id
       OR NEW.slettefrist IS DISTINCT FROM OLD.slettefrist
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'hmsmelder: raden er frosset — bare'
            ' anonymisering er en lovlig endring'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.navn IS NOT NULL AND OLD.navn IS NULL THEN
        RAISE EXCEPTION 'hmsmelder: et navn kan ikke settes tilbake'
            ' etter anonymisering'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER hmsmelder_frosset BEFORE UPDATE ON hmsmelder
    FOR EACH ROW EXECUTE FUNCTION m53_melder_frosset();

-- MELDERRADEN MOT ET ANONYMT AVVIK ER UMULIG.
--
-- DETTE ER MODULENS BÆRENDE VAKT. Uten den ville anonymiteten hvilt
-- på at hver skrivevei husket det; med den finnes det ingen kolonne å
-- fylle.
CREATE FUNCTION m53_melder_krever_navngitt()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.hmsavvik a
                    WHERE a.tenant = NEW.tenant
                      AND a.avvik_id = NEW.avvik_id
                      AND a.melderform = 'navngitt') THEN
        RAISE EXCEPTION 'hmsmelder: avviket er anonymt. Anonymitet er'
            ' fraværet av en rad, ikke et tomt felt'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m53_melder_krever_navngitt() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION m53_melder_krever_navngitt()
    TO disponit_migrator;

CREATE TRIGGER hmsmelder_krever_navngitt BEFORE INSERT ON hmsmelder
    FOR EACH ROW EXECUTE FUNCTION m53_melder_krever_navngitt();

-- TILTAKET ER APPEND-ONLY, HÅNDHEVET (M-42s dom, 110).
CREATE FUNCTION m53_tiltak_append_only()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'hmstiltak er append-only: % er forbudt — hva som'
        ' faktisk ble gjort er det et tilsyn etterprøver', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TRIGGER hmstiltak_append_only
    BEFORE UPDATE OR DELETE ON hmstiltak
    FOR EACH ROW EXECUTE FUNCTION m53_tiltak_append_only();
CREATE TRIGGER hmstiltak_ingen_truncate
    BEFORE TRUNCATE ON hmstiltak
    FOR EACH STATEMENT EXECUTE FUNCTION m53_tiltak_append_only();

-- 125/126s VAKT GJELDER OGSÅ HER. Nummer ti kopierte sveipen fra nummer
-- ni; det er nøyaktig det vakten ble skrevet for.
CREATE TRIGGER hmsfunn_lukkevern BEFORE UPDATE ON hmsfunn
    FOR EACH ROW EXECUTE FUNCTION sveipefunn_lukkevern('m53_sveip');

-- =====================================================================
-- EXECUTE — HVEM SOM FÅR ÅPNE HVILKEN DØR.
-- =====================================================================
SET LOCAL ROLE disponit_hms_eier;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m53_bildet(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m53_avvikene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m53_funnene(TEXT, BOOLEAN)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m53_regelverket(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m53_tiltakene(TEXT, UUID)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m53_oppbevaringsgrunnlag(TEXT, UUID) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m53_sett_krav(TEXT, INT, INT, INT, INT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m53_registrer_regel(TEXT,'
            ' UUID, TEXT, TEXT, TEXT, INT, BOOLEAN, DATE, DATE, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m53_meld_avvik(TEXT, UUID,'
            ' TEXT, TEXT, TEXT, TEXT, DATE, TEXT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m53_registrer_tiltak(TEXT,'
            ' UUID, UUID, TEXT, BOOLEAN, DATE, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m53_anonymiser(TEXT, UUID,'
            ' TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m53_lukk_funn(TEXT, UUID,'
            ' TEXT, TEXT) TO disponit';
    END IF;
END $$;

-- SVEIPEROLLEN NÅR ÉN FUNKSJON, OG BARE DEN (111s form, 112-124).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_hmssveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m53_sveip_hms(INT)'
            ' TO disponit_hmssveip';
    END IF;
END $$;

RESET ROLE;

COMMENT ON TABLE hmsavvik IS
    'M-53 avviksmottak (127). Anonymt avvik er en TILSTAND og '
    'ikke et tomt navnefelt: meldt_av og meldt_ts er NULL, og '
    'hmsmelder-raden finnes ikke. Se docs/M53-M30-GRENSESNITTET.md.';
COMMENT ON TABLE hmsmelder IS
    'Melderen. Raden finnes BARE for navngitte avvik — vakten '
    'hmsmelder_krever_navngitt nekter resten. Anonymisering tømmer '
    'navnet; raden slettes aldri.';
