-- 121: M-54 EHF- og Peppol-avviksretter v1 — FORMEN, IKKE INNHOLDET.
-- Sju tenant-skopede tabeller, sytten dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- V1-DOMMEN, ORDRETT FRA VAKTEN: «Retting klargjøres maskinelt,
-- utsending signeres av menneske.»
--
-- v1 SENDER INGEN RETTET FAKTURA, og det finnes ingen kolonne for
-- «sendt», ingen mottaker og ingen utboks. En faktura sendt to ganger
-- er et DOBBELT BETALINGSKRAV — og en rettet faktura som gikk ut uten
-- at noen så på rettingen, er nettopp det.
--
-- KLYNGE 7s DELTE DOM, OG HER ER DEN LETTEST Å SE OM ER RIKTIG:
--
--   REGELEN ER MYNDIGHETENS. Den endres, og den endres uten å si fra.
--
-- EHF er den norske innrettingen av PEPPOL BIS Billing 3.0, og begge
-- får nye versjoner. Et avvik funnet mot en gammel regelsettversjon er
-- ikke et avvik — det er en FORELDET DOM SOM SER VELFORMET UT. Det er
-- forskjellen fra en feil: en feil gir et avvik noen ser, mens en
-- foreldet regel gir et svar som er velformet, selvsikkert og galt.
--
-- DERFOR: `ehfvalidering.regelsett_id` ER NOT NULL MED FREMMEDNØKKEL,
-- OG VERSJONEN ER SNAPSHOTET PÅ RADEN. Invarianten
-- `validering_uten_skjemaversjon` er formen på tabellen — en validering
-- uten regelsettet den ble gjort under KAN IKKE UTTRYKKES.
--
-- OG REGELSETTET KAN BLI GAMMELT. `ehfregelsett` har `gyldig_fra` og
-- `gyldig_til`; døra NEKTER å validere mot et utløpt sett, og sveipen
-- melder hver validering som ER gjort under et sett som siden har
-- utløpt. `validering_mot_utlopt_skjema` er de to sammen.
--
-- HVOR REGLENE EVALUERES, OG HVORFOR DET ER I BASEN:
--
-- Den nærliggende løsningen er å validere XML-en i Python og skrive
-- svaret hit. Da ville basen bare vært et arkiv for en dom den ikke
-- kan etterprøve — og en ny regelsettversjon ville krevd at hele
-- dokumentet ble hentet og parset på nytt for å svare på «hva ville
-- den nye regelen sagt om det vi alt har mottatt».
--
-- I STEDET PARSES DOKUMENTET ÉN GANG TIL RADER (`ehffelt`), og
-- reglene evalueres MOT DE RADENE. Da er parsingen en registrert
-- kjensgjerning, evalueringen er deterministisk og re-kjørbar, og en
-- ny regelsettversjon gir en NY validering ved siden av den gamle —
-- mot nøyaktig de samme feltene. Samme figur som M-55s `m55_likhet`
-- (120): det som skal kunne etterprøves, regnes i basen.
--
-- v1s REGELSPRÅK ER LITE MED VILJE. Fire krav — `finnes`,
-- `ikke_tom`, `i_kodeliste`, `lik_sum` — og ingen fri XPath. Et
-- generelt uttrykksspråk ville gjort hver regel til kode ingen kan
-- lese uten å kjøre den, og en regel man må kjøre for å forstå er
-- ikke et regelsett; det er et program med et regelsetts navn.
--
-- EN REGEL SOM NEVNER ET FELT VI IKKE HAR TRUKKET UT, ER SELV ET
-- FUNN. Den er ikke stille grønn. Det er den samme dommen som over,
-- sett fra den andre siden: et manglende grunnlag skal si fra.
--
-- DOMMENE v1 HVILER PÅ, HÅNDHEVET I DATAMODELLEN:
--
--   1. HISTORIKKEN OVERSKRIVES ALDRI. Regelsett, dokumenter, felter,
--      valideringer og avvik er append-only med radvakt. M-42s dom
--      (110), gjentatt i 112–120.
--
--   2. HVER VALIDERING BÆRER REGELSETTET SITT, snapshotet — ikke bare
--      som fremmednøkkel til en rad som kunne endres.
--
--   3. HVER RETTING PEKER PÅ ET AVVIK. NOT NULL fremmednøkkel:
--      `retting_uten_avviksreferanse` er formen på tabellen. En
--      retting uten et avvik å rette er en endring av kundens faktura
--      uten en grunn noen kan peke på.
--
--   4. BELØP ER HELTALL I ØRE. BIGINT, uten unntak (101s og 106s
--      form). Summekontrollen er hele poenget med `lik_sum`, og en
--      sum regnet i flyttall ville gjort «stemmer fakturaen» til et
--      spørsmål med to svar.
--
--   5. INGEN KOLONNE BETYR «SENDT». Ingen mottaker, ingen utboks,
--      ingen signatur.
--
-- GRENSEN MOT M-14 (106): M-14 kontrollerer fakturaens INNHOLD mot
-- bestilling, avtale og mottak — og eier mva-satsene, som er DATERTE
-- og tenantens. M-54 kontrollerer dens FORM mot en teknisk standard.
-- En faktura kan være formriktig og innholdsmessig gal, og omvendt.
-- De to deler ingen tabell, og det er med vilje: en formfeil og en
-- innholdsfeil har ikke samme mottaker og ikke samme frist.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_ehf_eier') THEN
        RAISE EXCEPTION 'rollen disponit_ehf_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_ehf_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `ehfkrav` — ÉN per tenant. Tenantens egne terskler.
CREATE TABLE ehfkrav (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- Hvor mange døgn før et regelsett utløper vi begynner å melde
    -- fra. Et regelsett som utløper i morgen og som ingen har byttet
    -- ut, er en modul som slutter å virke over natta.
    utlopsvarsel_dogn INT NOT NULL DEFAULT 30
        CHECK (utlopsvarsel_dogn BETWEEN 1 AND 365),
    -- Hvor lenge et dokument med åpne avvik kan stå urettet før
    -- sveipen melder det.
    avviksfrist_dogn INT NOT NULL DEFAULT 7
        CHECK (avviksfrist_dogn BETWEEN 1 AND 365),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    -- IDEMPOTENSNØKKELEN SOM SATTE DENNE VERSJONEN (119s lærdom):
    -- raden er en singleton per tenant og har ingen id å utlede fra
    -- nøkkelen, så uten den ville et gjenspill bumpet versjonen.
    siste_nokkel TEXT NOT NULL
        CHECK (siste_nokkel ~ '[^[:space:]]'),
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT ehfkrav_pk PRIMARY KEY (tenant)
);

-- `ehfregelsett` — MYNDIGHETENS REGEL, MED SIN GYLDIGHET.
--
-- KLYNGENS BÆRENDE TABELL. Regelsettet registreres av et menneske som
-- har lest standarden, med versjon, innholdssum og et
-- GYLDIGHETSVINDU. Vinduet er det som gjør at en foreldet regel kan
-- oppdages i det hele tatt: uten `gyldig_til` ville en regel fra 2019
-- sett like gyldig ut som en fra i dag.
--
-- HELT FROSSET. Et regelsett som kunne endres i ettertid, ville gjort
-- hver eldre validering uetterprøvbar — og valideringen er hele
-- produktet.
CREATE TABLE ehfregelsett (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    regelsett_id UUID NOT NULL,
    -- HVILKEN STANDARD. Lukket liste: de tre lagene i en EHF-faktura
    -- er ikke samme regel, og et avvik må kunne skilles på hvilket.
    standard TEXT NOT NULL
        CONSTRAINT ehfregelsett_standard_lukket CHECK (standard IN (
            'ubl',          -- syntaksen (OASIS UBL 2.1)
            'peppol_bis',   -- forretningsreglene (PEPPOL BIS 3.0)
            'ehf')),        -- den norske innrettingen
    -- STANDARDENS EGEN VERSJONSBETEGNELSE. Fri tekst: versjoneringen
    -- er myndighetens, ikke vår.
    versjon TEXT NOT NULL CHECK (versjon ~ '[^[:space:]]'),
    -- GYLDIGHETSVINDUET. `gyldig_til` NULL betyr «gjelder fortsatt» —
    -- ikke «gjelder for alltid», og sveipen skiller på det.
    gyldig_fra DATE NOT NULL,
    gyldig_til DATE,
    CONSTRAINT ehfregelsett_vindu_er_ekte CHECK (
        gyldig_til IS NULL OR gyldig_til >= gyldig_fra),
    -- INNHOLDSSUMMEN over regeldokumentet slik det ble lest. Uten den
    -- kan ingen etterpå si HVILKEN tekst versjonsnummeret pekte på.
    innhold_sha256 TEXT NOT NULL
        CHECK (innhold_sha256 ~ '^[0-9a-f]{64}$'),
    kilde_url TEXT
        CONSTRAINT ehfregelsett_url_er_web CHECK (
            kilde_url IS NULL
            OR (kilde_url ~ '^https?://[^[:space:]]+$'
                AND length(kilde_url) <= 2000)),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT ehfregelsett_pk PRIMARY KEY (tenant, regelsett_id),
    CONSTRAINT ehfregelsett_unik UNIQUE (tenant, standard, versjon)
);

CREATE INDEX ehfregelsett_gyldige_idx ON ehfregelsett
    (tenant, standard, gyldig_fra DESC);

-- `ehfregel` — ÉN REGEL I ET SETT.
--
-- v1s REGELSPRÅK ER LITE MED VILJE: fire krav og ingen fri XPath. Et
-- generelt uttrykksspråk ville gjort hver regel til kode ingen kan
-- lese uten å kjøre den — og en regel man må kjøre for å forstå er
-- ikke et regelsett, det er et program med et regelsetts navn.
--
-- FROSSET, som settet den hører til.
CREATE TABLE ehfregel (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    regel_id UUID NOT NULL,
    regelsett_id UUID NOT NULL,
    -- STANDARDENS EGEN REGELKODE («BR-CO-10», «EHF-001»). Fri tekst:
    -- kodingen er myndighetens.
    kode TEXT NOT NULL CHECK (kode ~ '[^[:space:]]'),
    -- FELTET REGELEN GJELDER, som en punktsti («Invoice/ID»). Den
    -- matcher `ehffelt.sti` bokstavelig — ingen mønstre, ingen
    -- jokertegn.
    sti TEXT NOT NULL CHECK (sti ~ '^[A-Za-z0-9_./-]+$'),
    -- HVA KRAVET ER. Lukket liste, og hvert alternativ er
    -- etterprøvbart ved å lese regelen alene.
    krav TEXT NOT NULL
        CONSTRAINT ehfregel_krav_lukket CHECK (krav IN (
            'finnes',        -- feltet må være trukket ut
            'ikke_tom',      -- …og verdien må ikke være tom
            'i_kodeliste',   -- …og verdien må stå i `kodeverdi`
            'lik_sum')),     -- …og verdien må være summen av `sum_sti`
    -- FOR `i_kodeliste`: de lovlige verdiene. For de andre: tom.
    kodeverdi TEXT[] NOT NULL DEFAULT '{}',
    -- FOR `lik_sum`: stien til linjene som skal summeres.
    sum_sti TEXT
        CHECK (sum_sti IS NULL OR sum_sti ~ '^[A-Za-z0-9_./-]+$'),
    -- KRAVET OG PARAMETEREN HENGER SAMMEN. Uten dette kunne en
    -- `lik_sum`-regel stå uten noe å summere, og den ville vært
    -- stille grønn — den verste tilstanden en regel kan ha.
    CONSTRAINT ehfregel_parameter_folger_krav CHECK (
        (krav = 'i_kodeliste') = (cardinality(kodeverdi) > 0)
        AND (krav = 'lik_sum') = (sum_sti IS NOT NULL)),
    -- ALVORLIGHET. En advarsel stopper ingenting; en feil gjør
    -- dokumentet ugyldig. Skillet er standardens, ikke vårt.
    alvorlighet TEXT NOT NULL
        CONSTRAINT ehfregel_alvorlighet_lukket CHECK (
            alvorlighet IN ('feil', 'advarsel')),
    beskrivelse TEXT NOT NULL
        CHECK (beskrivelse ~ '[^[:space:]]'
               AND length(beskrivelse) <= 4000),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT ehfregel_pk PRIMARY KEY (tenant, regel_id),
    CONSTRAINT ehfregel_sett_fk FOREIGN KEY (tenant, regelsett_id)
        REFERENCES ehfregelsett (tenant, regelsett_id),
    CONSTRAINT ehfregel_unik UNIQUE (tenant, regelsett_id, kode)
);

CREATE INDEX ehfregel_sett_idx ON ehfregel (tenant, regelsett_id);

-- `ehfdokument` — FAKTURAEN, SLIK DEN KOM ELLER SKAL GÅ.
--
-- RETNINGEN ER IKKE PYNT. En INNGÅENDE faktura med formfeil er en vi
-- kan avvise; en UTGÅENDE er en vi må rette FØR den sendes. De to har
-- ikke samme mottaker og ikke samme frist, og en modul som ikke
-- skilte dem ville gitt samme beskjed i to helt ulike situasjoner.
--
-- FROSSET. Dokumentet er det valideringen uttaler seg om.
CREATE TABLE ehfdokument (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    dokument_id UUID NOT NULL,
    retning TEXT NOT NULL
        CONSTRAINT ehfdokument_retning_lukket CHECK (
            retning IN ('inngaaende', 'utgaaende')),
    -- FAKTURANUMMERET slik det står i dokumentet.
    ekstern_ref TEXT NOT NULL CHECK (ekstern_ref ~ '[^[:space:]]'),
    -- MOTPARTEN, som TEKST og ikke som fremmednøkkel til M-48: en
    -- faktura fra en leverandør vi ikke har registrert skal likevel
    -- kunne formkontrolleres. Formen er uavhengig av hvem vi kjenner.
    motpart TEXT NOT NULL CHECK (motpart ~ '[^[:space:]]'),
    fakturadato DATE NOT NULL,
    -- INNHOLDET, BUNDET TIL BYTENE. Samme figur som M-55s
    -- bevaringskopi (120): uten summen kan raden peke på hva som
    -- helst, og en validering av «hva som helst» er ingen validering.
    innhold_sha256 TEXT NOT NULL
        CHECK (innhold_sha256 ~ '^[0-9a-f]{64}$'),
    innhold_bytes BIGINT NOT NULL CHECK (innhold_bytes > 0),
    lagringsnokkel TEXT NOT NULL
        CHECK (lagringsnokkel ~ '[^[:space:]]'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT ehfdokument_pk PRIMARY KEY (tenant, dokument_id),
    -- SAMME FAKTURA FRA SAMME MOTPART I SAMME RETNING ER ÉN.
    CONSTRAINT ehfdokument_unik UNIQUE (
        tenant, retning, motpart, ekstern_ref)
);

CREATE INDEX ehfdokument_dato_idx ON ehfdokument
    (tenant, fakturadato DESC);

-- `ehffelt` — DOKUMENTET, PARSET ÉN GANG TIL RADER.
--
-- DETTE ER GRUNNEN TIL AT MODULEN KAN SVARE PÅ «HVA VILLE DEN NYE
-- REGELEN SAGT OM DET VI ALT HAR MOTTATT». Parsingen er en registrert
-- kjensgjerning; evalueringen skjer mot disse radene, og en ny
-- regelsettversjon gir en ny validering mot NØYAKTIG de samme
-- feltene. Uten dette måtte hele dokumentet hentes og parses på nytt,
-- og de to svarene ville ikke vært sammenlignbare.
--
-- BELØP LIGGER BÅDE SOM TEKST OG SOM ØRE. Teksten er det som faktisk
-- sto i XML-en — den skal ikke gå tapt. `verdi_ore` er det
-- summekontrollen regner på, i HELTALL: en sum i flyttall ville gjort
-- «stemmer fakturaen» til et spørsmål med to svar (106s dom).
CREATE TABLE ehffelt (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    felt_id UUID NOT NULL,
    dokument_id UUID NOT NULL,
    sti TEXT NOT NULL CHECK (sti ~ '^[A-Za-z0-9_./-]+$'),
    -- LØPENUMMER for gjentatte felter (fakturalinjer). 0 for felter
    -- som bare finnes én gang.
    forekomst INT NOT NULL DEFAULT 0 CHECK (forekomst >= 0),
    -- VERDIEN ORDRETT. Kan være tom streng: «feltet fantes, men var
    -- tomt» og «feltet fantes ikke» er to forskjellige avvik, og en
    -- NULL her ville visket ut forskjellen.
    verdi TEXT NOT NULL,
    -- BELØPET I ØRE, når feltet er et beløp. NULL ellers — og NULL
    -- betyr «ikke et beløp», ikke «null kroner».
    verdi_ore BIGINT,
    CONSTRAINT ehffelt_pk PRIMARY KEY (tenant, felt_id),
    CONSTRAINT ehffelt_dokument_fk FOREIGN KEY (tenant, dokument_id)
        REFERENCES ehfdokument (tenant, dokument_id),
    CONSTRAINT ehffelt_unik UNIQUE (tenant, dokument_id, sti,
                                    forekomst)
);

CREATE INDEX ehffelt_oppslag_idx ON ehffelt
    (tenant, dokument_id, sti);

-- `ehfvalidering` — DOMMEN, MED REGELSETTET SITT.
--
-- `regelsett_id` ER NOT NULL MED FREMMEDNØKKEL, og versjonen er
-- SNAPSHOTET ved siden av. Invarianten `validering_uten_skjemaversjon`
-- er dermed formen på tabellen: en validering uten regelsettet den
-- ble gjort under kan ikke uttrykkes.
--
-- Hvorfor BÅDE fremmednøkkel og snapshot: fremmednøkkelen binder til
-- raden, snapshotet binder til TEKSTEN. Regelsettraden er riktignok
-- frosset — men snapshotet gjør at svaret på «hvilken versjon» kan
-- leses uten et oppslag, og det er nettopp det spørsmålet som stilles
-- når noen etterprøver et avvik år senere.
--
-- `gyldig` ER GENERERT. Dommen kan ikke være uenig med sine egne
-- tall: null feil betyr gyldig, og ingen kan skrive noe annet.
CREATE TABLE ehfvalidering (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    validering_id UUID NOT NULL,
    dokument_id UUID NOT NULL,
    regelsett_id UUID NOT NULL,
    -- SNAPSHOTENE. Standarden og versjonen slik de var.
    standard_ved_validering TEXT NOT NULL
        CHECK (standard_ved_validering ~ '[^[:space:]]'),
    versjon_ved_validering TEXT NOT NULL
        CHECK (versjon_ved_validering ~ '[^[:space:]]'),
    antall_regler INT NOT NULL CHECK (antall_regler >= 0),
    antall_feil INT NOT NULL CHECK (antall_feil >= 0),
    antall_advarsler INT NOT NULL CHECK (antall_advarsler >= 0),
    -- REGLER SOM NEVNTE ET FELT VI IKKE HAR TRUKKET UT. De er ikke
    -- stille grønne: et manglende grunnlag skal si fra, og tallet står
    -- på dommen så den som leser den ser hvor mye den IKKE dekket.
    antall_uten_grunnlag INT NOT NULL
        CHECK (antall_uten_grunnlag >= 0),
    gyldig BOOLEAN NOT NULL
        GENERATED ALWAYS AS (antall_feil = 0) STORED,
    validert TIMESTAMPTZ NOT NULL DEFAULT now(),
    validert_av TEXT NOT NULL CHECK (validert_av ~ '[^[:space:]]'),
    CONSTRAINT ehfvalidering_pk PRIMARY KEY (tenant, validering_id),
    CONSTRAINT ehfvalidering_dokument_fk FOREIGN KEY
        (tenant, dokument_id)
        REFERENCES ehfdokument (tenant, dokument_id),
    CONSTRAINT ehfvalidering_sett_fk FOREIGN KEY
        (tenant, regelsett_id)
        REFERENCES ehfregelsett (tenant, regelsett_id),
    -- SAMME DOKUMENT MOT SAMME REGELSETT ER ÉN VALIDERING. Å regne
    -- det to ganger er ikke to dommer — men et NYTT regelsett gir en
    -- ny rad ved siden av den gamle, aldri i stedet for.
    CONSTRAINT ehfvalidering_unik UNIQUE (
        tenant, dokument_id, regelsett_id)
);

CREATE INDEX ehfvalidering_dokument_idx ON ehfvalidering
    (tenant, dokument_id, validert DESC);

-- `ehfavvik` — ETT BRUDD, MED REGELEN SOM BLE BRUTT.
--
-- FROSSET. Et avvik som kunne redigeres, ville gjort rettingen under
-- det til en endring uten en grunn noen kan peke på.
CREATE TABLE ehfavvik (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    avvik_id UUID NOT NULL,
    validering_id UUID NOT NULL,
    regel_id UUID NOT NULL,
    -- REGELKODEN OG ALVORLIGHETEN SNAPSHOTES. Regelraden er frosset,
    -- men avviket skal kunne leses alene — det er den raden et
    -- menneske faktisk ser på.
    regelkode TEXT NOT NULL CHECK (regelkode ~ '[^[:space:]]'),
    alvorlighet TEXT NOT NULL
        CONSTRAINT ehfavvik_alvorlighet_lukket CHECK (
            alvorlighet IN ('feil', 'advarsel', 'uten_grunnlag')),
    sti TEXT NOT NULL CHECK (sti ~ '^[A-Za-z0-9_./-]+$'),
    -- HVA SOM STO DER, OG HVA SOM VAR VENTET. NULL på `funnet_verdi`
    -- betyr at feltet IKKE FANTES — ikke at det var tomt.
    funnet_verdi TEXT,
    forventet TEXT,
    beskrivelse TEXT NOT NULL
        CHECK (beskrivelse ~ '[^[:space:]]'
               AND length(beskrivelse) <= 4000),
    CONSTRAINT ehfavvik_pk PRIMARY KEY (tenant, avvik_id),
    CONSTRAINT ehfavvik_validering_fk FOREIGN KEY
        (tenant, validering_id)
        REFERENCES ehfvalidering (tenant, validering_id),
    CONSTRAINT ehfavvik_regel_fk FOREIGN KEY (tenant, regel_id)
        REFERENCES ehfregel (tenant, regel_id),
    CONSTRAINT ehfavvik_unik UNIQUE (
        tenant, validering_id, regel_id, sti)
);

CREATE INDEX ehfavvik_validering_idx ON ehfavvik
    (tenant, validering_id, alvorlighet);

-- `ehfretting` — DEN KLARGJORTE RETTINGEN.
--
-- `avvik_id` ER NOT NULL MED FREMMEDNØKKEL. Invarianten
-- `retting_uten_avviksreferanse` er formen på tabellen: en retting
-- uten et avvik å rette er en endring av en faktura uten en grunn
-- noen kan peke på — og en faktura er et betalingskrav.
--
-- DET FINNES INGEN «SENDT»-KOLONNE, INGEN MOTTAKER OG INGEN SIGNATUR.
-- Rettingen kan merkes KLAR TIL SIGNERING — en tilstand HOS OSS, av
-- samme slag som M-46s «klar til gjennomgang» (118) og M-51s
-- ferdigstilte estimat (119). Signaturen selv hører til v2, og
-- forutsetningen for v2 er MÅLT: hvor ofte klargjøringen er feil.
CREATE TABLE ehfretting (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    retting_id UUID NOT NULL,
    avvik_id UUID NOT NULL,
    -- HVA SOM SKAL ENDRES, FRA HVA, TIL HVA. Alle tre påkrevd:
    -- «rett feltet» uten fra-verdien er en endring ingen kan
    -- kontrollere i ettertid.
    felt_sti TEXT NOT NULL CHECK (felt_sti ~ '^[A-Za-z0-9_./-]+$'),
    -- NULL på `fra_verdi` betyr at feltet SKAL LEGGES TIL. Tom streng
    -- betyr at det fantes og var tomt.
    fra_verdi TEXT,
    til_verdi TEXT NOT NULL,
    begrunnelse TEXT NOT NULL
        CHECK (length(btrim(begrunnelse)) >= 4
               AND length(begrunnelse) <= 4000),
    klar_til_signering BOOLEAN NOT NULL DEFAULT false,
    klar_ts TIMESTAMPTZ,
    klar_av TEXT CHECK (klar_av IS NULL
                        OR klar_av ~ '[^[:space:]]'),
    CONSTRAINT ehfretting_klar_er_hel CHECK (
        klar_til_signering = (klar_ts IS NOT NULL)
        AND klar_til_signering = (klar_av IS NOT NULL)),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT ehfretting_pk PRIMARY KEY (tenant, retting_id),
    CONSTRAINT ehfretting_avvik_fk FOREIGN KEY (tenant, avvik_id)
        REFERENCES ehfavvik (tenant, avvik_id),
    -- ÉN RETTING PER AVVIK OG FELT. To rettinger av samme felt for
    -- samme avvik ville vært to svar på samme spørsmål.
    CONSTRAINT ehfretting_unik UNIQUE (tenant, avvik_id, felt_sti)
);

CREATE INDEX ehfretting_avvik_idx ON ehfretting (tenant, avvik_id);

-- `ehffunn` — NATTENS FUNN.
--
-- `regelsett_utlopt` OG `validering_mot_utlopt_regelsett` ER
-- KLYNGENS EGNE. Den første sier at regelen vi bruker er gått ut;
-- den andre at en dom vi alt har felt, ble felt under en regel som
-- siden har gått ut. De er ikke det samme, og bare den første kan
-- lukkes av et menneske: den andre forsvinner når dokumentet
-- valideres på nytt mot et gyldig sett — og det er en handling, ikke
-- en mening.
CREATE TABLE ehffunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    funn_id UUID NOT NULL,
    -- NØKKELEN. Ett av de tre er satt, aldri to.
    regelsett_id UUID,
    dokument_id UUID,
    validering_id UUID,
    funntype TEXT NOT NULL
        CONSTRAINT ehffunn_type_lukket CHECK (funntype IN (
            'regelsett_utlopt',
            'regelsett_utloper_snart',
            'validering_mot_utlopt_regelsett',
            'dokument_uten_validering',
            'avvik_uten_retting',
            'retting_ikke_klar',
            'ingen_krav')),
    over_grense INT,
    detalj TEXT CHECK (detalj IS NULL OR detalj ~ '[^[:space:]]'),
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
    CONSTRAINT ehffunn_lukking_er_hel CHECK (
        num_nulls(lukket_ts, lukket_av, lukkenotat) IN (0, 3)),
    CONSTRAINT ehffunn_apen_er_ulukket CHECK (
        apen = (lukket_ts IS NULL)),
    -- NØYAKTIG ÉN NØKKEL. Uten dette kunne samme tilstand meldes på
    -- to nivåer og telles to ganger.
    CONSTRAINT ehffunn_en_noekkel CHECK (
        num_nonnulls(regelsett_id, dokument_id, validering_id) = 1),
    CONSTRAINT ehffunn_pk PRIMARY KEY (tenant, funn_id),
    CONSTRAINT ehffunn_sett_fk FOREIGN KEY (tenant, regelsett_id)
        REFERENCES ehfregelsett (tenant, regelsett_id),
    CONSTRAINT ehffunn_dokument_fk FOREIGN KEY (tenant, dokument_id)
        REFERENCES ehfdokument (tenant, dokument_id),
    CONSTRAINT ehffunn_validering_fk FOREIGN KEY
        (tenant, validering_id)
        REFERENCES ehfvalidering (tenant, validering_id)
);

-- TRE DELVISE UNIKHETSINDEKSER, én per nøkkeltype: en sammensatt
-- primærnøkkel med NULL i seg kunne ikke rommet dem, fordi NULL aldri
-- er lik NULL. Sveipen treffer nøyaktig én per opsjon.
CREATE UNIQUE INDEX ehffunn_regelsett_unik ON ehffunn
    (tenant, regelsett_id, funntype) WHERE regelsett_id IS NOT NULL;
CREATE UNIQUE INDEX ehffunn_dokument_unik ON ehffunn
    (tenant, dokument_id, funntype) WHERE dokument_id IS NOT NULL;
CREATE UNIQUE INDEX ehffunn_validering_unik ON ehffunn
    (tenant, validering_id, funntype)
    WHERE validering_id IS NOT NULL;
CREATE INDEX ehffunn_apne_idx ON ehffunn (tenant, funntype)
    WHERE apen;


-- ------------------------------------------------------------
-- 2. Evidenskjeden og dørene. Eieren eier dem, og eierskapet ER
--    fullmakten.
-- ------------------------------------------------------------

-- `krev_tenantkontekst` eies av `disponit_m37_claimer` (111s form), så
-- EXECUTE må gis AV den rollen (116s lærdom: en GRANT fra migratoren
-- er et stille ikke-oppdrag med en advarsel).
GRANT INSERT ON revisjonslogg TO disponit_ehf_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_ehf_eier;
RESET ROLE;

-- HERFRA OG TIL SEKSJON 6 EIES ALT SOM LAGES AV EHF-EIEREN.
SET LOCAL ROLE disponit_ehf_eier;

CREATE FUNCTION m54_evidens(p_tenant TEXT, p_dokument_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm54_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm54_ehf', 'handling', p_handling,
        'dokument_id', p_dokument_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm54_ehf',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:ehf', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m54_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;


-- ER REGELSETTET GYLDIG I DAG? KLYNGENS EGEN SPØRRING.
--
-- `gyldig_til IS NULL` betyr «gjelder fortsatt», ikke «gjelder for
-- alltid» — og forskjellen er hele grunnen til at kolonnen finnes.
-- IMMUTABLE kan den ikke være: den leser dagens dato. STABLE er
-- riktig, og det er nok: innenfor én setning gir den samme svar.
CREATE FUNCTION m54_regelsett_gyldig(p_fra DATE, p_til DATE)
RETURNS BOOLEAN LANGUAGE sql STABLE
SET search_path = pg_catalog AS $$
    SELECT p_fra <= current_date
       AND (p_til IS NULL OR p_til >= current_date)
$$;
GRANT EXECUTE ON FUNCTION m54_regelsett_gyldig(DATE, DATE) TO PUBLIC;


-- ------------------------------------------------------------
-- 3. Skrivedørene.
-- ------------------------------------------------------------

-- TERSKLENE (119s form: nøkkelen står inne i døra fordi raden er en
-- singleton per tenant og ikke har en id å utlede fra den).
CREATE FUNCTION m54_sett_krav(
    p_tenant TEXT, p_utlopsvarsel_dogn INT, p_avviksfrist_dogn INT,
    p_aktor TEXT, p_nokkel TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT; v_nokkel TEXT; v_likt BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm54_sett_krav');
    IF p_nokkel IS NULL OR btrim(p_nokkel) = '' THEN
        RAISE EXCEPTION 'm54_sett_krav: idempotensnøkkel mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.aktor', p_aktor, true);

    -- LÅS FØRST, LES NØKKELEN ETTERPÅ (119/120s form).
    PERFORM 1 FROM public.ehfkrav
     WHERE tenant = p_tenant FOR UPDATE;
    SELECT k.versjon, k.siste_nokkel,
           (k.utlopsvarsel_dogn = p_utlopsvarsel_dogn
            AND k.avviksfrist_dogn = p_avviksfrist_dogn)
      INTO v_versjon, v_nokkel, v_likt
      FROM public.ehfkrav k WHERE k.tenant = p_tenant;

    IF v_nokkel IS NOT NULL AND v_nokkel = p_nokkel THEN
        IF v_likt THEN
            RETURN v_versjon;
        END IF;
        RAISE EXCEPTION 'm54_sett_krav: nøkkelen % er alt brukt på'
            ' andre verdier', p_nokkel
            USING ERRCODE = 'unique_violation';
    END IF;

    INSERT INTO public.ehfkrav
        (tenant, utlopsvarsel_dogn, avviksfrist_dogn, siste_nokkel,
         oppdatert_av)
    VALUES (p_tenant, p_utlopsvarsel_dogn, p_avviksfrist_dogn,
            btrim(p_nokkel), p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        utlopsvarsel_dogn = EXCLUDED.utlopsvarsel_dogn,
        avviksfrist_dogn = EXCLUDED.avviksfrist_dogn,
        siste_nokkel = EXCLUDED.siste_nokkel,
        versjon = public.ehfkrav.versjon + 1,
        oppdatert = now(),
        oppdatert_av = EXCLUDED.oppdatert_av
    RETURNING versjon INTO v_versjon;

    PERFORM public.m54_evidens(p_tenant, NULL, 'ehfkrav_satt',
        p_aktor, jsonb_build_object('versjon', v_versjon));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION m54_sett_krav(TEXT, INT, INT, TEXT, TEXT)
    FROM PUBLIC;


-- REGELSETTET. Registreres av et menneske som har lest standarden.
--
-- ET UTLØPT SETT KAN REGISTRERES, OG DET ER MED VILJE.
--
-- Den nærliggende regelen — «nekt et vindu som alt er utløpt» — ble
-- skrevet her først, og den var GAL. Modulen finnes nettopp for å
-- kunne svare på «hva sa standarden den gangen»: en tenant som tar i
-- bruk modulen i 2026 må kunne skrive inn EHF 2.0 for å forstå en
-- faktura fra 2022. Å forby arkivet er å forby spørsmålet.
--
-- SKILLET GÅR VED DOMMEN, IKKE VED ARKIVET: `m54_valider_dokument`
-- NEKTER mot et utløpt sett. Man får skrive ned historien; man får
-- ikke dømme etter den.
CREATE FUNCTION m54_registrer_regelsett(
    p_tenant TEXT, p_regelsett_id UUID, p_standard TEXT,
    p_versjon TEXT, p_gyldig_fra DATE, p_gyldig_til DATE,
    p_innhold_sha256 TEXT, p_kilde_url TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm54_registrer_regelsett');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.ehfregelsett
        (tenant, regelsett_id, standard, versjon, gyldig_fra,
         gyldig_til, innhold_sha256, kilde_url, registrert_av)
    VALUES (p_tenant, p_regelsett_id, p_standard, btrim(p_versjon),
            p_gyldig_fra, p_gyldig_til,
            lower(btrim(p_innhold_sha256)),
            nullif(btrim(coalesce(p_kilde_url, '')), ''), p_aktor);
    PERFORM public.m54_evidens(p_tenant, NULL,
        'ehfregelsett_registrert', p_aktor, jsonb_build_object(
            'regelsett_id', p_regelsett_id::text,
            'standard', p_standard, 'versjon', btrim(p_versjon),
            'gyldig_til', p_gyldig_til));
END $$;
REVOKE ALL ON FUNCTION m54_registrer_regelsett(
    TEXT, UUID, TEXT, TEXT, DATE, DATE, TEXT, TEXT, TEXT)
    FROM PUBLIC;



-- UTLØPSDATOEN KAN SETTES ETTERPÅ, OG BARE DEN.
--
-- DETTE ER KLYNGENS EGEN DOM, OG DEN BLE OPPDAGET VED Å PRØVE Å TESTE
-- DEN MOTSATTE: settet var først HELT frosset, og da kunne modulen
-- ikke skrive ned at myndigheten hadde kunngjort en sluttdato.
--
-- Et standardorgan varsler i juni at EHF 3.0 trekkes 31. desember.
-- Raden vår sier `gyldig_til = NULL`. Uten denne døra måtte vi
-- registrert et NYTT sett med samme standard og versjon — som
-- unikhetsnøkkelen forbyr — eller latt som vi ikke visste det.
--
-- Å NEKTE Å SKRIVE NED ENDRINGEN ER Å NEKTE DOKTRINEN: regelen er
-- myndighetens, og den endres. Modulen må kunne følge med.
--
-- ALT ANNET ER FROSSET. Standard, versjon, `gyldig_fra` og
-- innholdssummen er settets IDENTITET — det er den som gjør en gammel
-- validering etterprøvbar — og radvakten i seksjon 6 håndhever det.
-- En kolonnegrant på `gyldig_til` alene gjør resten til en rettighet
-- som ikke finnes, i stedet for en regel en vakt må huske (119s form).
CREATE FUNCTION m54_sett_gyldig_til(
    p_tenant TEXT, p_regelsett_id UUID, p_gyldig_til DATE,
    p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_fra DATE; v_for DATE; v_std TEXT; v_ver TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm54_sett_gyldig_til');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT r.gyldig_fra, r.gyldig_til, r.standard, r.versjon
      INTO v_fra, v_for, v_std, v_ver
      FROM public.ehfregelsett r
     WHERE r.tenant = p_tenant AND r.regelsett_id = p_regelsett_id;
    IF v_fra IS NULL THEN
        RAISE EXCEPTION 'm54_sett_gyldig_til: ukjent regelsett %',
            p_regelsett_id USING ERRCODE = 'no_data_found';
    END IF;
    IF p_gyldig_til IS NOT NULL AND p_gyldig_til < v_fra THEN
        RAISE EXCEPTION 'm54_sett_gyldig_til: % er før settets'
            ' startdato %', p_gyldig_til, v_fra
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.ehfregelsett SET gyldig_til = p_gyldig_til
     WHERE tenant = p_tenant AND regelsett_id = p_regelsett_id;
    PERFORM public.m54_evidens(p_tenant, NULL,
        'ehfregelsett_gyldig_til_satt', p_aktor, jsonb_build_object(
            'regelsett_id', p_regelsett_id::text,
            'standard', v_std, 'versjon', v_ver,
            'fra', v_for, 'til', p_gyldig_til));
END $$;
REVOKE ALL ON FUNCTION
    m54_sett_gyldig_til(TEXT, UUID, DATE, TEXT) FROM PUBLIC;

-- ÉN REGEL I ET SETT.
CREATE FUNCTION m54_registrer_regel(
    p_tenant TEXT, p_regel_id UUID, p_regelsett_id UUID, p_kode TEXT,
    p_sti TEXT, p_krav TEXT, p_kodeverdi TEXT[], p_sum_sti TEXT,
    p_alvorlighet TEXT, p_beskrivelse TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm54_registrer_regel');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.ehfregel
        (tenant, regel_id, regelsett_id, kode, sti, krav, kodeverdi,
         sum_sti, alvorlighet, beskrivelse, registrert_av)
    VALUES (p_tenant, p_regel_id, p_regelsett_id, btrim(p_kode),
            btrim(p_sti), p_krav, coalesce(p_kodeverdi, '{}'),
            nullif(btrim(coalesce(p_sum_sti, '')), ''),
            p_alvorlighet, btrim(p_beskrivelse), p_aktor);
    PERFORM public.m54_evidens(p_tenant, NULL, 'ehfregel_registrert',
        p_aktor, jsonb_build_object(
            'regelsett_id', p_regelsett_id::text, 'kode', btrim(p_kode),
            'krav', p_krav));
END $$;
REVOKE ALL ON FUNCTION m54_registrer_regel(
    TEXT, UUID, UUID, TEXT, TEXT, TEXT, TEXT[], TEXT, TEXT, TEXT,
    TEXT) FROM PUBLIC;


-- DOKUMENTET.
CREATE FUNCTION m54_registrer_dokument(
    p_tenant TEXT, p_dokument_id UUID, p_retning TEXT,
    p_ekstern_ref TEXT, p_motpart TEXT, p_fakturadato DATE,
    p_innhold_sha256 TEXT, p_innhold_bytes BIGINT,
    p_lagringsnokkel TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm54_registrer_dokument');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.ehfdokument
        (tenant, dokument_id, retning, ekstern_ref, motpart,
         fakturadato, innhold_sha256, innhold_bytes, lagringsnokkel,
         registrert_av)
    VALUES (p_tenant, p_dokument_id, p_retning, btrim(p_ekstern_ref),
            btrim(p_motpart), p_fakturadato,
            lower(btrim(p_innhold_sha256)), p_innhold_bytes,
            btrim(p_lagringsnokkel), p_aktor);
    PERFORM public.m54_evidens(p_tenant, p_dokument_id,
        'ehfdokument_registrert', p_aktor, jsonb_build_object(
            'retning', p_retning,
            'innhold_sha256', lower(btrim(p_innhold_sha256))));
END $$;
REVOKE ALL ON FUNCTION m54_registrer_dokument(
    TEXT, UUID, TEXT, TEXT, TEXT, DATE, TEXT, BIGINT, TEXT, TEXT)
    FROM PUBLIC;


-- FELTENE, PARSET ÉN GANG. Hele settet i ETT kall: en delvis parsing
-- ville gitt en validering som så komplett ut mot et halvt dokument.
CREATE FUNCTION m54_registrer_felter(
    p_tenant TEXT, p_dokument_id UUID, p_stier TEXT[],
    p_forekomster INT[], p_verdier TEXT[], p_ore BIGINT[],
    p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_antall INT;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm54_registrer_felter');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_stier IS NULL OR cardinality(p_stier) = 0 THEN
        RAISE EXCEPTION 'm54_registrer_felter: ingen felter — et'
            ' dokument uten et eneste felt er ikke parset, det er'
            ' bare registrert'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- FIRE LISTER MED SAMME LENGDE. Ulik lengde ville stilltiende
    -- kappet den korteste, og et felt som forsvant i kappingen ville
    -- blitt `uten_grunnlag` uten at noen skrev det.
    IF cardinality(p_forekomster) <> cardinality(p_stier)
       OR cardinality(p_verdier) <> cardinality(p_stier)
       OR cardinality(p_ore) <> cardinality(p_stier) THEN
        RAISE EXCEPTION 'm54_registrer_felter: listene har ulik'
            ' lengde (%, %, %, %)', cardinality(p_stier),
            cardinality(p_forekomster), cardinality(p_verdier),
            cardinality(p_ore)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.ehffelt
        (tenant, felt_id, dokument_id, sti, forekomst, verdi,
         verdi_ore)
    SELECT p_tenant, gen_random_uuid(), p_dokument_id,
           s.sti, f.forekomst, v.verdi, o.ore
      FROM unnest(p_stier) WITH ORDINALITY AS s(sti, i)
      JOIN unnest(p_forekomster) WITH ORDINALITY AS f(forekomst, i)
        ON f.i = s.i
      JOIN unnest(p_verdier) WITH ORDINALITY AS v(verdi, i)
        ON v.i = s.i
      JOIN unnest(p_ore) WITH ORDINALITY AS o(ore, i) ON o.i = s.i;
    GET DIAGNOSTICS v_antall = ROW_COUNT;
    PERFORM public.m54_evidens(p_tenant, p_dokument_id,
        'ehffelt_registrert', p_aktor,
        jsonb_build_object('antall', v_antall));
    RETURN v_antall;
END $$;
REVOKE ALL ON FUNCTION m54_registrer_felter(
    TEXT, UUID, TEXT[], INT[], TEXT[], BIGINT[], TEXT) FROM PUBLIC;


-- VALIDERINGEN. MODULENS KJERNE, OG DEN ENESTE DØRA SOM DØMMER.
--
-- DØRA NEKTER MOT ET UTLØPT REGELSETT. Ikke fordi et gammelt svar er
-- ubrukelig, men fordi det ville sett ut som et gyldig svar: en
-- foreldet regel gir en dom som er velformet, selvsikkert og gal.
-- `validering_mot_utlopt_skjema` er denne nekten pluss sveipens
-- melding om dommer som ALT er felt under et sett som siden har
-- utløpt.
--
-- EVALUERINGEN SKJER MOT `ehffelt`, ikke mot XML. Det er derfor et
-- NYTT regelsett kan felle en ny dom over NØYAKTIG de samme feltene,
-- og de to dommene kan sammenlignes. Hadde vi validert XML på nytt,
-- ville forskjellen mellom to svar kunnet komme fra parsingen.
--
-- TRE UTFALL PER REGEL, OG DET TREDJE ER POENGET:
--
--   `feil`/`advarsel`  — regelen er brutt, med standardens egen
--                        alvorlighet.
--   ingenting          — regelen holder.
--   `uten_grunnlag`    — regelen nevner et felt vi ikke har trukket
--                        ut. DEN ER IKKE STILLE GRØNN. Et manglende
--                        grunnlag skal si fra, og tallet står på
--                        dommen så den som leser den ser hvor mye
--                        den IKKE dekket.
--
-- `finnes` ER UNNTAKET: der ER fraværet selve avviket, og et
-- manglende felt gir `feil`, ikke `uten_grunnlag`.
CREATE FUNCTION m54_valider_dokument(
    p_tenant TEXT, p_dokument_id UUID, p_regelsett_id UUID,
    p_validering_id UUID, p_aktor TEXT)
RETURNS TABLE (antall_regler INT, antall_feil INT,
               antall_advarsler INT, antall_uten_grunnlag INT,
               gyldig BOOLEAN, standard TEXT, versjon TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_standard TEXT; v_versjon TEXT;
    v_fra DATE; v_til DATE;
    v_regler INT := 0; v_feil INT := 0; v_advarsler INT := 0;
    v_utenfor INT := 0;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm54_valider_dokument');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    SELECT r.standard, r.versjon, r.gyldig_fra, r.gyldig_til
      INTO v_standard, v_versjon, v_fra, v_til
      FROM public.ehfregelsett r
     WHERE r.tenant = p_tenant AND r.regelsett_id = p_regelsett_id;
    IF v_standard IS NULL THEN
        RAISE EXCEPTION 'm54_valider_dokument: ukjent regelsett %',
            p_regelsett_id USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT public.m54_regelsett_gyldig(v_fra, v_til) THEN
        RAISE EXCEPTION 'm54_valider_dokument: regelsettet % %'
            ' er ikke gyldig i dag (% til %). En dom felt under en'
            ' foreldet regel ser velformet ut og er gal — registrer'
            ' den gjeldende versjonen først',
            v_standard, v_versjon, v_fra, coalesce(v_til::text, '—')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM public.ehfdokument d
                    WHERE d.tenant = p_tenant
                      AND d.dokument_id = p_dokument_id) THEN
        RAISE EXCEPTION 'm54_valider_dokument: ukjent dokument %',
            p_dokument_id USING ERRCODE = 'no_data_found';
    END IF;
    -- ET REGELSETT UTEN REGLER DØMMER INGENTING (CodeRabbit).
    --
    -- Uten dette ville en validering mot et tomt sett gitt «0 regler,
    -- 0 feil, GYLDIG» — et dokument erklært i orden som ingen har sett
    -- på. Det er den verste formen for feil modulen kan gjøre, fordi
    -- svaret er velformet og selvsikkert.
    IF NOT EXISTS (SELECT 1 FROM public.ehfregel g
                    WHERE g.tenant = p_tenant
                      AND g.regelsett_id = p_regelsett_id) THEN
        RAISE EXCEPTION 'm54_valider_dokument: regelsettet % % har'
            ' ingen regler. En validering mot det ville sagt «null'
            ' feil» om et dokument ingen har sett på',
            v_standard, v_versjon
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- ET DOKUMENT UTEN FELTER ER IKKE PARSET. En validering mot null
    -- felter ville gitt «alt uten grunnlag» og sett ut som en kjøring.
    IF NOT EXISTS (SELECT 1 FROM public.ehffelt f
                    WHERE f.tenant = p_tenant
                      AND f.dokument_id = p_dokument_id) THEN
        RAISE EXCEPTION 'm54_valider_dokument: dokumentet % har ingen'
            ' registrerte felter — det er ikke parset, og en'
            ' validering mot ingenting er ingen validering',
            p_dokument_id USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- SELVE DOMMEN. ÉN gjennomgang av reglene, og rekkefølgen er
    -- tvunget: `ehfavvik` peker på `ehfvalidering`, så valideringen må
    -- settes inn FØRST. Data-modifiserende CTE-er kjøres bare når noe
    -- refererer dem — derfor krysskoblingen til slutt, som ikke er
    -- pynt: uten den ville avvikene stilltiende ikke blitt skrevet.
    WITH regel AS (
        SELECT g.regel_id, g.kode, g.sti, g.krav, g.kodeverdi,
               g.sum_sti, g.alvorlighet, g.beskrivelse
          FROM public.ehfregel g
         WHERE g.tenant = p_tenant
           AND g.regelsett_id = p_regelsett_id),
    grunnlag AS (
        SELECT r.*,
               -- FELTET REGELEN GJELDER. `forekomst = 0` er
               -- dokumentnivået; gjentatte felter summeres for seg.
               (SELECT f.verdi FROM public.ehffelt f
                 WHERE f.tenant = p_tenant
                   AND f.dokument_id = p_dokument_id
                   AND f.sti = r.sti AND f.forekomst = 0) AS verdi,
               -- TO ULIKE SPØRSMÅL, OG DE MÅ HOLDES FRA HVERANDRE
               -- (CodeRabbit).
               --
               -- `finnes` er FOREKOMST-AGNOSTISK: et felt som bare
               -- står på linjenivå (forekomst 1, 2, …) FINNES.
               -- `har_verdi` spør om det finnes på DOKUMENTNIVÅ
               -- (forekomst 0), som er det de andre kravene dømmer på.
               --
               -- Uten skillet ville en `ikke_tom`-regel på
               -- `Invoice/Line/Amount` sett `finnes = true` og
               -- `verdi = NULL`, lest det som «feltet er tomt», og
               -- meldt en FEIL som ikke finnes.
               EXISTS (SELECT 1 FROM public.ehffelt f
                        WHERE f.tenant = p_tenant
                          AND f.dokument_id = p_dokument_id
                          AND f.sti = r.sti) AS finnes,
               EXISTS (SELECT 1 FROM public.ehffelt f
                        WHERE f.tenant = p_tenant
                          AND f.dokument_id = p_dokument_id
                          AND f.sti = r.sti
                          AND f.forekomst = 0) AS har_verdi,
               (SELECT f.verdi_ore FROM public.ehffelt f
                 WHERE f.tenant = p_tenant
                   AND f.dokument_id = p_dokument_id
                   AND f.sti = r.sti AND f.forekomst = 0) AS ore,
               -- SUMMEN AV LINJENE, I HELTALL (106s dom). NULL når
               -- regelen ikke er en summeregel.
               (SELECT sum(f.verdi_ore)::bigint
                  FROM public.ehffelt f
                 WHERE r.sum_sti IS NOT NULL
                   AND f.tenant = p_tenant
                   AND f.dokument_id = p_dokument_id
                   AND f.sti = r.sum_sti) AS linjesum
          FROM regel r),
    dom AS (
        SELECT g.*,
               CASE
                 -- `finnes`: FRAVÆRET ER AVVIKET.
                 WHEN g.krav = 'finnes' AND NOT g.finnes
                   THEN g.alvorlighet
                 WHEN g.krav = 'finnes' THEN NULL
                 -- DE ANDRE KREVER ET GRUNNLAG PÅ DOKUMENTNIVÅ.
                 WHEN NOT g.har_verdi THEN 'uten_grunnlag'
                 WHEN g.krav = 'ikke_tom'
                      AND btrim(coalesce(g.verdi, '')) = ''
                   THEN g.alvorlighet
                 WHEN g.krav = 'i_kodeliste'
                      AND NOT (g.verdi = ANY (g.kodeverdi))
                   THEN g.alvorlighet
                 WHEN g.krav = 'lik_sum'
                      AND (g.ore IS NULL OR g.linjesum IS NULL)
                   THEN 'uten_grunnlag'
                 WHEN g.krav = 'lik_sum' AND g.ore <> g.linjesum
                   THEN g.alvorlighet
                 ELSE NULL
               END AS utfall
          FROM grunnlag g),
    telling AS (
        SELECT count(*)::int AS regler,
               count(*) FILTER (WHERE utfall = 'feil')::int AS feil,
               count(*) FILTER (WHERE utfall = 'advarsel')::int
                   AS advarsler,
               count(*) FILTER (WHERE utfall = 'uten_grunnlag')::int
                   AS utenfor
          FROM dom),
    val AS (
        INSERT INTO public.ehfvalidering
            (tenant, validering_id, dokument_id, regelsett_id,
             standard_ved_validering, versjon_ved_validering,
             antall_regler, antall_feil, antall_advarsler,
             antall_uten_grunnlag, validert_av)
        SELECT p_tenant, p_validering_id, p_dokument_id,
               p_regelsett_id, v_standard, v_versjon, t.regler,
               t.feil, t.advarsler, t.utenfor, p_aktor
          FROM telling t
        RETURNING 1),
    skrevet AS (
        INSERT INTO public.ehfavvik
            (tenant, avvik_id, validering_id, regel_id, regelkode,
             alvorlighet, sti, funnet_verdi, forventet, beskrivelse)
        SELECT p_tenant, gen_random_uuid(), p_validering_id,
               d.regel_id, d.kode, d.utfall, d.sti,
               -- NULL BETYR AT FELTET IKKE FANTES — ikke at det var
               -- tomt. Forskjellen er det første et menneske spør om.
               CASE WHEN d.har_verdi
                      THEN coalesce(d.verdi, '') END,
               CASE d.krav
                 WHEN 'i_kodeliste'
                   THEN array_to_string(d.kodeverdi, ', ')
                 WHEN 'lik_sum' THEN d.linjesum::text
                 WHEN 'finnes' THEN 'feltet må finnes'
                 ELSE 'feltet må ikke være tomt' END,
               d.beskrivelse
          FROM dom d
         WHERE d.utfall IS NOT NULL
        RETURNING 1)
    SELECT t.regler, t.feil, t.advarsler, t.utenfor
      INTO v_regler, v_feil, v_advarsler, v_utenfor
      FROM telling t
      CROSS JOIN (SELECT count(*) FROM val) v(n)
      CROSS JOIN (SELECT count(*) FROM skrevet) w(n);

    PERFORM public.m54_evidens(p_tenant, p_dokument_id,
        'ehfvalidering_gjort', p_aktor, jsonb_build_object(
            'validering_id', p_validering_id::text,
            'standard', v_standard, 'versjon', v_versjon,
            'antall_feil', coalesce(v_feil, 0),
            'antall_uten_grunnlag', coalesce(v_utenfor, 0)));

    RETURN QUERY
    SELECT v.antall_regler, v.antall_feil, v.antall_advarsler,
           v.antall_uten_grunnlag, v.gyldig,
           v.standard_ved_validering, v.versjon_ved_validering
      FROM public.ehfvalidering v
     WHERE v.tenant = p_tenant
       AND v.validering_id = p_validering_id;
END $$;
REVOKE ALL ON FUNCTION
    m54_valider_dokument(TEXT, UUID, UUID, UUID, TEXT) FROM PUBLIC;


-- RETTINGEN. `avvik_id` ER PÅKREVD, og fremmednøkkelen gjør det
-- umulig å omgå: `retting_uten_avviksreferanse` er formen på tabellen.
--
-- DØRA NEKTER Å RETTE ET `uten_grunnlag`-AVVIK. Et avvik der vi ikke
-- kunne dømme, er ikke et avvik vi vet hvordan skal rettes — og en
-- retting på det grunnlaget ville endret en faktura fordi vi manglet
-- data, ikke fordi noe var galt.
CREATE FUNCTION m54_registrer_retting(
    p_tenant TEXT, p_retting_id UUID, p_avvik_id UUID,
    p_felt_sti TEXT, p_fra_verdi TEXT, p_til_verdi TEXT,
    p_begrunnelse TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_alvorlighet TEXT; v_dokument_id UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm54_registrer_retting');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT a.alvorlighet, v.dokument_id
      INTO v_alvorlighet, v_dokument_id
      FROM public.ehfavvik a
      JOIN public.ehfvalidering v
        ON v.tenant = a.tenant AND v.validering_id = a.validering_id
     WHERE a.tenant = p_tenant AND a.avvik_id = p_avvik_id;
    IF v_alvorlighet IS NULL THEN
        RAISE EXCEPTION 'm54_registrer_retting: ukjent avvik %',
            p_avvik_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_alvorlighet = 'uten_grunnlag' THEN
        RAISE EXCEPTION 'm54_registrer_retting: avviket % er'
            ' «uten_grunnlag» — regelen nevnte et felt vi ikke har'
            ' trukket ut. En retting her ville endret fakturaen fordi'
            ' vi manglet data, ikke fordi noe var galt', p_avvik_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.ehfretting
        (tenant, retting_id, avvik_id, felt_sti, fra_verdi,
         til_verdi, begrunnelse, registrert_av)
    VALUES (p_tenant, p_retting_id, p_avvik_id, btrim(p_felt_sti),
            p_fra_verdi, p_til_verdi, btrim(p_begrunnelse), p_aktor);
    PERFORM public.m54_evidens(p_tenant, v_dokument_id,
        'ehfretting_registrert', p_aktor, jsonb_build_object(
            'retting_id', p_retting_id::text,
            'avvik_id', p_avvik_id::text,
            'felt_sti', btrim(p_felt_sti)));
END $$;
REVOKE ALL ON FUNCTION m54_registrer_retting(
    TEXT, UUID, UUID, TEXT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;


-- «KLAR TIL SIGNERING» — EN TILSTAND HOS OSS, IKKE EN HANDLING UTAD.
--
-- Samme figur som M-46s «klar til gjennomgang» (118) og M-51s
-- ferdigstilte estimat (119). Det finnes ingen signatur her og ingen
-- utsending: signaturen hører til v2, og forutsetningen for v2 er
-- MÅLT — hvor ofte klargjøringen er feil.
--
-- DØRA NEKTER SÅ LENGE DOKUMENTET HAR ET ÅPENT `feil`-AVVIK UTEN
-- RETTING. Å merke en faktura klar mens en formfeil står urettet, er
-- å be et menneske signere på at noe er i orden som ikke er det.
CREATE FUNCTION m54_merk_klar(
    p_tenant TEXT, p_retting_id UUID, p_aktor TEXT)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_avvik_id UUID; v_validering_id UUID; v_dokument_id UUID;
    v_klar BOOLEAN; v_udekket BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm54_merk_klar');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    -- LÅS RETTINGEN, LES KLARMERKET ETTERPÅ. Lesningen over en lås er
    -- fra transaksjonens snapshot; et parallelt `m54_merk_klar` som
    -- committer mens vi venter ville vært usynlig (116–120s lærdom,
    -- skrevet feil fem ganger i klynge 6).
    PERFORM 1 FROM public.ehfretting
     WHERE tenant = p_tenant AND retting_id = p_retting_id
     FOR UPDATE;
    SELECT r.avvik_id, r.klar_til_signering
      INTO v_avvik_id, v_klar
      FROM public.ehfretting r
     WHERE r.tenant = p_tenant AND r.retting_id = p_retting_id;
    IF v_avvik_id IS NULL THEN
        RAISE EXCEPTION 'm54_merk_klar: ukjent retting %',
            p_retting_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_klar THEN
        RAISE EXCEPTION 'm54_merk_klar: rettingen % er alt merket'
            ' klar', p_retting_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT a.validering_id, v.dokument_id
      INTO v_validering_id, v_dokument_id
      FROM public.ehfavvik a
      JOIN public.ehfvalidering v
        ON v.tenant = a.tenant AND v.validering_id = a.validering_id
     WHERE a.tenant = p_tenant AND a.avvik_id = v_avvik_id;

    -- FEIL UTEN RETTING, I SAMME VALIDERING. Rettingen vi nå merker
    -- klar teller som dekket.
    SELECT count(*) INTO v_udekket
      FROM public.ehfavvik a
     WHERE a.tenant = p_tenant
       AND a.validering_id = v_validering_id
       AND a.alvorlighet = 'feil'
       AND a.avvik_id <> v_avvik_id
       AND NOT EXISTS (SELECT 1 FROM public.ehfretting r2
                        WHERE r2.tenant = a.tenant
                          AND r2.avvik_id = a.avvik_id);
    IF v_udekket > 0 THEN
        RAISE EXCEPTION 'm54_merk_klar: dokumentet har % formfeil'
            ' uten retting. Å merke klar mens en formfeil står'
            ' urettet, er å be et menneske signere på at noe er i'
            ' orden som ikke er det', v_udekket
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.ehfretting
       SET klar_til_signering = true, klar_ts = now(),
           klar_av = p_aktor
     WHERE tenant = p_tenant AND retting_id = p_retting_id;

    PERFORM public.m54_evidens(p_tenant, v_dokument_id,
        'ehfretting_klar', p_aktor, jsonb_build_object(
            'retting_id', p_retting_id::text,
            'udekkede_feil', v_udekket));
    RETURN v_udekket;
END $$;
REVOKE ALL ON FUNCTION m54_merk_klar(TEXT, UUID, TEXT) FROM PUBLIC;


-- FUNNET LUKKES, MEN IKKE `validering_mot_utlopt_regelsett`.
--
-- Det funnet forsvinner når dokumentet valideres på nytt mot et
-- gyldig sett — og det er en HANDLING, ikke en mening. Samme figur
-- som M-49s bekreftede treff (117), M-46s udekkede absolutte krav
-- (118), M-51s takfunn (119) og M-55s uhenviste forveksling (120).
CREATE FUNCTION m54_lukk_funn(
    p_tenant TEXT, p_funn_id UUID, p_notat TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_type TEXT; v_apen BOOLEAN; v_dokument_id UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm54_lukk_funn');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    PERFORM 1 FROM public.ehffunn
     WHERE tenant = p_tenant AND funn_id = p_funn_id FOR UPDATE;
    SELECT f.funntype, f.apen, f.dokument_id
      INTO v_type, v_apen, v_dokument_id
      FROM public.ehffunn f
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;
    IF v_type IS NULL THEN
        RAISE EXCEPTION 'm54_lukk_funn: ukjent funn %', p_funn_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_apen THEN
        RAISE EXCEPTION 'm54_lukk_funn: funnet % er alt lukket',
            p_funn_id USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_type = 'validering_mot_utlopt_regelsett' THEN
        RAISE EXCEPTION 'm54_lukk_funn: % kan ikke lukkes. Dommen ble'
            ' felt under en regel som siden har gått ut, og funnet'
            ' forsvinner når dokumentet valideres på nytt mot et'
            ' gyldig sett — det er en handling, ikke en mening',
            v_type USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.ehffunn
       SET apen = false, lukket_ts = now(), lukket_av = p_aktor,
           lukkenotat = btrim(p_notat)
     WHERE tenant = p_tenant AND funn_id = p_funn_id;

    PERFORM public.m54_evidens(p_tenant, v_dokument_id,
        'ehffunn_lukket', p_aktor,
        jsonb_build_object('funntype', v_type));
END $$;
REVOKE ALL ON FUNCTION m54_lukk_funn(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Lesedørene.
-- ------------------------------------------------------------

CREATE FUNCTION m54_kravene(p_tenant TEXT)
RETURNS TABLE (utlopsvarsel_dogn INT, avviksfrist_dogn INT,
               versjon INT, oppdatert TIMESTAMPTZ,
               oppdatert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm54_kravene');
    RETURN QUERY
    SELECT k.utlopsvarsel_dogn, k.avviksfrist_dogn, k.versjon,
           k.oppdatert, k.oppdatert_av
      FROM public.ehfkrav k WHERE k.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m54_kravene(TEXT) FROM PUBLIC;


-- REGELSETTENE, MED GYLDIGHETEN SIN REGNET I BASEN.
--
-- `gyldig_naa` og `dogn_til_utlop` regnes HER og ikke i flaten: to
-- lesere skal ikke kunne komme til hver sin konklusjon om hvorvidt
-- regelen vi bruker fortsatt gjelder.
CREATE FUNCTION m54_regelsettene(p_tenant TEXT, p_grense INT)
RETURNS TABLE (regelsett_id UUID, standard TEXT, versjon TEXT,
               gyldig_fra DATE, gyldig_til DATE, gyldig_naa BOOLEAN,
               dogn_til_utlop INT, innhold_sha256 TEXT,
               kilde_url TEXT, registrert TIMESTAMPTZ,
               registrert_av TEXT, antall_regler BIGINT,
               antall_valideringer BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm54_regelsettene');
    IF p_grense IS NULL OR p_grense < 1 OR p_grense > 500 THEN
        RAISE EXCEPTION 'm54_regelsettene: grensen må være 1..500 (%)',
            p_grense USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    SELECT r.regelsett_id, r.standard, r.versjon, r.gyldig_fra,
           r.gyldig_til,
           public.m54_regelsett_gyldig(r.gyldig_fra, r.gyldig_til),
           CASE WHEN r.gyldig_til IS NULL THEN NULL
                ELSE (r.gyldig_til - current_date) END,
           r.innhold_sha256, r.kilde_url, r.registrert,
           r.registrert_av,
           (SELECT count(*) FROM public.ehfregel g
             WHERE g.tenant = r.tenant
               AND g.regelsett_id = r.regelsett_id),
           (SELECT count(*) FROM public.ehfvalidering v
             WHERE v.tenant = r.tenant
               AND v.regelsett_id = r.regelsett_id)
      FROM public.ehfregelsett r
     WHERE r.tenant = p_tenant
     ORDER BY r.standard, r.gyldig_fra DESC
     LIMIT p_grense;
END $$;
REVOKE ALL ON FUNCTION m54_regelsettene(TEXT, INT) FROM PUBLIC;


CREATE FUNCTION m54_reglene(p_tenant TEXT, p_regelsett_id UUID)
RETURNS TABLE (regel_id UUID, kode TEXT, sti TEXT, krav TEXT,
               kodeverdi TEXT[], sum_sti TEXT, alvorlighet TEXT,
               beskrivelse TEXT, registrert TIMESTAMPTZ,
               registrert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm54_reglene');
    RETURN QUERY
    SELECT g.regel_id, g.kode, g.sti, g.krav, g.kodeverdi, g.sum_sti,
           g.alvorlighet, g.beskrivelse, g.registrert, g.registrert_av
      FROM public.ehfregel g
     WHERE g.tenant = p_tenant AND g.regelsett_id = p_regelsett_id
     ORDER BY g.kode;
END $$;
REVOKE ALL ON FUNCTION m54_reglene(TEXT, UUID) FROM PUBLIC;


-- DOKUMENTENE, MED NYESTE VALIDERING PÅ SAMME RAD.
--
-- REGELSETTVERSJONEN STÅR HER, ikke bak et ekstra oppslag: en dom
-- uten versjonen den ble felt under er nettopp det klyngen finnes for
-- å unngå.
CREATE FUNCTION m54_dokumentene(p_tenant TEXT, p_grense INT)
RETURNS TABLE (dokument_id UUID, retning TEXT, ekstern_ref TEXT,
               motpart TEXT, fakturadato DATE, innhold_sha256 TEXT,
               innhold_bytes BIGINT, registrert TIMESTAMPTZ,
               registrert_av TEXT, antall_felt BIGINT,
               validering_id UUID, standard TEXT, versjon TEXT,
               antall_regler INT, antall_feil INT,
               antall_advarsler INT, antall_uten_grunnlag INT,
               gyldig BOOLEAN, validert TIMESTAMPTZ,
               regelsett_gyldig_naa BOOLEAN, antall_rettinger BIGINT,
               klare_rettinger BIGINT, antall_valideringer BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm54_dokumentene');
    IF p_grense IS NULL OR p_grense < 1 OR p_grense > 500 THEN
        RAISE EXCEPTION 'm54_dokumentene: grensen må være 1..500 (%)',
            p_grense USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    SELECT d.dokument_id, d.retning, d.ekstern_ref, d.motpart,
           d.fakturadato, d.innhold_sha256, d.innhold_bytes,
           d.registrert, d.registrert_av,
           (SELECT count(*) FROM public.ehffelt f
             WHERE f.tenant = d.tenant
               AND f.dokument_id = d.dokument_id),
           s.validering_id, s.standard_ved_validering,
           s.versjon_ved_validering, s.antall_regler, s.antall_feil,
           s.antall_advarsler, s.antall_uten_grunnlag, s.gyldig,
           s.validert, s.gyldig_naa,
           coalesce(t.antall, 0), coalesce(t.klare, 0),
           coalesce(a.antall, 0)
      FROM public.ehfdokument d
      LEFT JOIN LATERAL (
           -- NYESTE VALIDERING. Valideringene er append-only og
           -- versjonerte: et dokument kan ha flere, og en telling som
           -- tok alle ville sagt at det finnes flere dommer enn det
           -- gjør (119s målte lærdom).
           SELECT vv.validering_id, vv.standard_ved_validering,
                  vv.versjon_ved_validering, vv.antall_regler,
                  vv.antall_feil, vv.antall_advarsler,
                  vv.antall_uten_grunnlag, vv.gyldig, vv.validert,
                  public.m54_regelsett_gyldig(rr.gyldig_fra,
                                              rr.gyldig_til)
                      AS gyldig_naa
             FROM public.ehfvalidering vv
             JOIN public.ehfregelsett rr
               ON rr.tenant = vv.tenant
              AND rr.regelsett_id = vv.regelsett_id
            WHERE vv.tenant = d.tenant
              AND vv.dokument_id = d.dokument_id
            ORDER BY vv.validert DESC, vv.validering_id DESC
            LIMIT 1) s ON true
      LEFT JOIN LATERAL (
           SELECT count(*) AS antall,
                  count(*) FILTER (WHERE r.klar_til_signering)
                      AS klare
             FROM public.ehfretting r
             JOIN public.ehfavvik av
               ON av.tenant = r.tenant AND av.avvik_id = r.avvik_id
            WHERE r.tenant = d.tenant
              AND av.validering_id = s.validering_id) t ON true
      LEFT JOIN LATERAL (
           SELECT count(*) AS antall FROM public.ehfvalidering v2
            WHERE v2.tenant = d.tenant
              AND v2.dokument_id = d.dokument_id) a ON true
     WHERE d.tenant = p_tenant
     ORDER BY coalesce(s.antall_feil, 0) DESC,
              d.fakturadato DESC, d.ekstern_ref
     LIMIT p_grense;
END $$;
REVOKE ALL ON FUNCTION m54_dokumentene(TEXT, INT) FROM PUBLIC;


-- AVVIKENE I ÉN VALIDERING, MED RETTINGEN SIN.
CREATE FUNCTION m54_avvikene(p_tenant TEXT, p_validering_id UUID)
RETURNS TABLE (avvik_id UUID, regelkode TEXT, alvorlighet TEXT,
               sti TEXT, funnet_verdi TEXT, forventet TEXT,
               beskrivelse TEXT, retting_id UUID, felt_sti TEXT,
               fra_verdi TEXT, til_verdi TEXT,
               retting_begrunnelse TEXT, klar_til_signering BOOLEAN,
               klar_ts TIMESTAMPTZ, klar_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm54_avvikene');
    RETURN QUERY
    SELECT a.avvik_id, a.regelkode, a.alvorlighet, a.sti,
           a.funnet_verdi, a.forventet, a.beskrivelse,
           r.retting_id, r.felt_sti, r.fra_verdi, r.til_verdi,
           r.begrunnelse, coalesce(r.klar_til_signering, false),
           r.klar_ts, r.klar_av
      FROM public.ehfavvik a
      LEFT JOIN public.ehfretting r
        ON r.tenant = a.tenant AND r.avvik_id = a.avvik_id
     WHERE a.tenant = p_tenant AND a.validering_id = p_validering_id
     ORDER BY (a.alvorlighet = 'feil') DESC,
              (a.alvorlighet = 'uten_grunnlag') DESC, a.regelkode;
END $$;
REVOKE ALL ON FUNCTION m54_avvikene(TEXT, UUID) FROM PUBLIC;


-- ALLE VALIDERINGENE AV ETT DOKUMENT, i rekkefølge. En ny
-- regelsettversjon gir en ny rad, og HELE rekken skal kunne leses:
-- det er der «hva sa standarden den gangen» faktisk står.
CREATE FUNCTION m54_valideringene(p_tenant TEXT, p_dokument_id UUID)
RETURNS TABLE (validering_id UUID, regelsett_id UUID,
               standard TEXT, versjon TEXT, antall_regler INT,
               antall_feil INT, antall_advarsler INT,
               antall_uten_grunnlag INT, gyldig BOOLEAN,
               regelsett_gyldig_naa BOOLEAN, validert TIMESTAMPTZ,
               validert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm54_valideringene');
    RETURN QUERY
    SELECT v.validering_id, v.regelsett_id,
           v.standard_ved_validering, v.versjon_ved_validering,
           v.antall_regler, v.antall_feil, v.antall_advarsler,
           v.antall_uten_grunnlag, v.gyldig,
           public.m54_regelsett_gyldig(r.gyldig_fra, r.gyldig_til),
           v.validert, v.validert_av
      FROM public.ehfvalidering v
      JOIN public.ehfregelsett r
        ON r.tenant = v.tenant AND r.regelsett_id = v.regelsett_id
     WHERE v.tenant = p_tenant AND v.dokument_id = p_dokument_id
     ORDER BY v.validert DESC, v.validering_id DESC;
END $$;
REVOKE ALL ON FUNCTION m54_valideringene(TEXT, UUID) FROM PUBLIC;


CREATE FUNCTION m54_funnene(p_tenant TEXT, p_bare_apne BOOLEAN)
RETURNS TABLE (funn_id UUID, funntype TEXT, regelsett_id UUID,
               dokument_id UUID, validering_id UUID,
               standard TEXT, regelsettversjon TEXT,
               ekstern_ref TEXT, over_grense INT, detalj TEXT,
               kravversjon INT, forst_sett TIMESTAMPTZ,
               sist_sett_sveip TIMESTAMPTZ, apen BOOLEAN,
               lukket_ts TIMESTAMPTZ, lukket_av TEXT,
               lukkenotat TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm54_funnene');
    RETURN QUERY
    SELECT f.funn_id, f.funntype, f.regelsett_id, f.dokument_id,
           f.validering_id, coalesce(r.standard, vr.standard),
           coalesce(r.versjon, vr.versjon), d.ekstern_ref,
           f.over_grense, f.detalj, f.kravversjon, f.forst_sett,
           f.sist_sett_sveip, f.apen, f.lukket_ts, f.lukket_av,
           f.lukkenotat
      FROM public.ehffunn f
      LEFT JOIN public.ehfregelsett r
        ON r.tenant = f.tenant AND r.regelsett_id = f.regelsett_id
      LEFT JOIN public.ehfvalidering v
        ON v.tenant = f.tenant AND v.validering_id = f.validering_id
      LEFT JOIN public.ehfregelsett vr
        ON vr.tenant = v.tenant AND vr.regelsett_id = v.regelsett_id
      LEFT JOIN public.ehfdokument d
        ON d.tenant = f.tenant
       AND d.dokument_id = coalesce(f.dokument_id, v.dokument_id)
     WHERE f.tenant = p_tenant
       AND (NOT coalesce(p_bare_apne, true) OR f.apen)
     ORDER BY f.apen DESC, f.funntype, f.forst_sett;
END $$;
REVOKE ALL ON FUNCTION m54_funnene(TEXT, BOOLEAN) FROM PUBLIC;


-- SAMMENDRAGET. Tallene flaten åpner med.
CREATE FUNCTION m54_ehfstatus(p_tenant TEXT)
RETURNS TABLE (regelsett BIGINT, gyldige_regelsett BIGINT,
               utlopte_regelsett BIGINT, dokumenter BIGINT,
               validerte BIGINT, med_feil BIGINT,
               uten_grunnlag BIGINT, uvaliderte BIGINT,
               dommer_under_utlopt BIGINT, rettinger BIGINT,
               klare_rettinger BIGINT, apne_funn BIGINT,
               har_krav BOOLEAN, kravversjon INT, vist BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm54_ehfstatus');
    RETURN QUERY
    WITH nyeste AS (
        -- NYESTE VALIDERING PER DOKUMENT, ÉN GANG (119s lærdom).
        SELECT d.dokument_id, s.validering_id, s.antall_feil,
               s.antall_uten_grunnlag, s.gyldig_naa
          FROM public.ehfdokument d
          LEFT JOIN LATERAL (
               SELECT vv.validering_id, vv.antall_feil,
                      vv.antall_uten_grunnlag,
                      public.m54_regelsett_gyldig(rr.gyldig_fra,
                                                  rr.gyldig_til)
                          AS gyldig_naa
                 FROM public.ehfvalidering vv
                 JOIN public.ehfregelsett rr
                   ON rr.tenant = vv.tenant
                  AND rr.regelsett_id = vv.regelsett_id
                WHERE vv.tenant = d.tenant
                  AND vv.dokument_id = d.dokument_id
                ORDER BY vv.validert DESC, vv.validering_id DESC
                LIMIT 1) s ON true
         WHERE d.tenant = p_tenant)
    SELECT (SELECT count(*) FROM public.ehfregelsett r
             WHERE r.tenant = p_tenant),
           (SELECT count(*) FROM public.ehfregelsett r
             WHERE r.tenant = p_tenant
               AND public.m54_regelsett_gyldig(r.gyldig_fra,
                                               r.gyldig_til)),
           (SELECT count(*) FROM public.ehfregelsett r
             WHERE r.tenant = p_tenant
               AND NOT public.m54_regelsett_gyldig(r.gyldig_fra,
                                                   r.gyldig_til)),
           (SELECT count(*) FROM nyeste),
           (SELECT count(*) FROM nyeste n
             WHERE n.validering_id IS NOT NULL),
           (SELECT count(*) FROM nyeste n WHERE n.antall_feil > 0),
           (SELECT count(*) FROM nyeste n
             WHERE n.antall_uten_grunnlag > 0),
           (SELECT count(*) FROM nyeste n
             WHERE n.validering_id IS NULL),
           -- DOMMER FELT UNDER EN REGEL SOM SIDEN HAR GÅTT UT. Det
           -- ene tallet klyngen finnes for.
           (SELECT count(*) FROM nyeste n
             WHERE n.validering_id IS NOT NULL
               AND NOT n.gyldig_naa),
           (SELECT count(*) FROM public.ehfretting r
             WHERE r.tenant = p_tenant),
           (SELECT count(*) FROM public.ehfretting r
             WHERE r.tenant = p_tenant AND r.klar_til_signering),
           (SELECT count(*) FROM public.ehffunn f
             WHERE f.tenant = p_tenant AND f.apen),
           EXISTS (SELECT 1 FROM public.ehfkrav k
                    WHERE k.tenant = p_tenant),
           (SELECT k.versjon FROM public.ehfkrav k
             WHERE k.tenant = p_tenant),
           (SELECT least(count(*), 200) FROM public.ehfdokument d
             WHERE d.tenant = p_tenant);
END $$;
REVOKE ALL ON FUNCTION m54_ehfstatus(TEXT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 5. Sveipen. Kryss-tenant, egen LOGIN-rolle, egen timer.
-- ------------------------------------------------------------

-- NATTENS ENESTE JOBB: SE ETTER REGLER SOM ER GÅTT UT, OG DOMMER SOM
-- BLE FELT UNDER DEM.
--
-- SVEIPEN VALIDERER IKKE OG RETTER IKKE. En automatisk revalidering
-- ville felt nye dommer om natten uten at noen ba om det — og en dom
-- er inngangen til en retting av en kundes faktura.
--
-- SVEIPEN HENTER HELLER INGEN REGELSETT. Standarden er myndighetens,
-- og en modul som lastet ned den nyeste versjonen selv ville tatt
-- ansvaret for at NØYAKTIG den er den gjeldende. Regelsettet
-- registreres av et menneske som har lest den.
--
-- TENANTLISTA MATERIALISERES FØR LØKKA (112s lærdom, 116–120).
CREATE FUNCTION m54_sveip_ehf(p_maks_tenanter INT)
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
        RAISE EXCEPTION 'm54_sveip_ehf: maks_tenanter må være minst'
            ' 1 (%)', p_maks_tenanter
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.tenant', '', true);
    SELECT array_agg(DISTINCT r.tenant ORDER BY r.tenant)
      INTO v_tenanter FROM public.ehfregelsett r;
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

        -- REGELSETTNIVÅ.
        WITH krav AS (
            SELECT k.utlopsvarsel_dogn, k.versjon
              FROM public.ehfkrav k WHERE k.tenant = v_t),
        kand AS (
            -- INGEN TERSKLER. Festet per regelsett, som 119s
            -- `ingen_krav` festes per ordning: et funn uten adresse
            -- er en beskjed ingen eier.
            SELECT r.regelsett_id, 'ingen_krav'::text AS funntype,
                   NULL::int AS over_grense,
                   'utløpsvarselet er tenantens og er ikke satt'::text
                       AS detalj,
                   NULL::int AS kravversjon
              FROM public.ehfregelsett r
             WHERE r.tenant = v_t AND NOT EXISTS (SELECT 1 FROM krav)

            UNION ALL
            -- ET UTLØPT SETT UTEN EN GYLDIG ETTERFØLGER.
            --
            -- IKKE ETHVERT UTLØPT SETT: et arkivert EHF 2.0 ved siden
            -- av et gyldig 3.0 er historikk, ikke et problem, og et
            -- funn på det ville vært støy hver natt for alltid. Det
            -- som ER et problem, er å stå uten noe gyldig å validere
            -- MED — da slutter modulen å virke, stille.
            --
            -- INGEN `CROSS JOIN krav` HER (CodeRabbit). Dette funnet
            -- avhenger ikke av en terskel, og en krysskobling mot en
            -- TOM `krav` ville gitt null rader — altså: en tenant som
            -- ikke har satt terskler ville ALDRI fått vite at
            -- regelsettet er gått ut. Kravversjonen hentes som skalar
            -- og er NULL når den mangler, som er et ærlig svar.
            SELECT r.regelsett_id, 'regelsett_utlopt',
                   (current_date - r.gyldig_til),
                   r.standard || ' ' || r.versjon,
                   (SELECT versjon FROM krav)
              FROM public.ehfregelsett r
             WHERE r.tenant = v_t AND r.gyldig_til IS NOT NULL
               AND r.gyldig_til < current_date
               AND NOT EXISTS (
                   SELECT 1 FROM public.ehfregelsett r2
                    WHERE r2.tenant = v_t
                      AND r2.standard = r.standard
                      AND public.m54_regelsett_gyldig(r2.gyldig_fra,
                                                      r2.gyldig_til))

            UNION ALL
            -- …ELLER GÅR UT SNART. Et regelsett som utløper i morgen
            -- og som ingen har byttet ut, er en modul som slutter å
            -- virke over natta.
            SELECT r.regelsett_id, 'regelsett_utloper_snart',
                   (r.gyldig_til - current_date),
                   r.standard || ' ' || r.versjon, k.versjon
              FROM public.ehfregelsett r CROSS JOIN krav k
             WHERE r.tenant = v_t AND r.gyldig_til IS NOT NULL
               AND r.gyldig_til >= current_date
               AND r.gyldig_til <= current_date
                   + make_interval(days => k.utlopsvarsel_dogn)
        ),
        skrevet AS (
            INSERT INTO public.ehffunn
                (tenant, funn_id, regelsett_id, funntype,
                 over_grense, detalj, kravversjon)
            SELECT v_t, gen_random_uuid(), k.regelsett_id, k.funntype,
                   k.over_grense, k.detalj, k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, regelsett_id, funntype)
                WHERE regelsett_id IS NOT NULL
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
        -- retting, gjentatt i 116–120).
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);

        -- DOKUMENTNIVÅ.
        WITH krav AS (
            SELECT k.avviksfrist_dogn, k.versjon
              FROM public.ehfkrav k WHERE k.tenant = v_t),
        kand AS (
            SELECT d.dokument_id,
                   'dokument_uten_validering'::text AS funntype,
                   (current_date - d.registrert::date) AS over_grense,
                   NULL::text AS detalj, k.versjon AS kravversjon
              FROM public.ehfdokument d CROSS JOIN krav k
             WHERE d.tenant = v_t
               AND NOT EXISTS (SELECT 1 FROM public.ehfvalidering v
                                WHERE v.tenant = v_t
                                  AND v.dokument_id = d.dokument_id)
               AND d.registrert < now()
                   - make_interval(days => k.avviksfrist_dogn)
        ),
        skrevet AS (
            INSERT INTO public.ehffunn
                (tenant, funn_id, dokument_id, funntype, over_grense,
                 detalj, kravversjon)
            SELECT v_t, gen_random_uuid(), k.dokument_id, k.funntype,
                   k.over_grense, k.detalj, k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, dokument_id, funntype)
                WHERE dokument_id IS NOT NULL
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

        -- VALIDERINGSNIVÅ. KLYNGENS EGET FUNN.
        WITH krav AS (
            SELECT k.versjon FROM public.ehfkrav k
             WHERE k.tenant = v_t),
        nyeste AS (
            SELECT DISTINCT ON (v.dokument_id)
                   v.validering_id, v.dokument_id, v.regelsett_id,
                   v.antall_feil
              FROM public.ehfvalidering v
             WHERE v.tenant = v_t
             ORDER BY v.dokument_id, v.validert DESC,
                      v.validering_id DESC),
        kand AS (
            -- DOMMEN BLE FELT UNDER EN REGEL SOM SIDEN HAR GÅTT UT.
            -- Den kan ikke lukkes av et menneske: den forsvinner når
            -- dokumentet valideres på nytt mot et gyldig sett.
            -- INGEN `CROSS JOIN krav` PÅ DE TRE UNDER (CodeRabbit):
            -- ingen av dem avhenger av en terskel, og en krysskobling
            -- mot en tom `krav` ville gjort dem usynlige for nettopp
            -- den tenanten som ikke har satt noe.
            SELECT n.validering_id,
                   'validering_mot_utlopt_regelsett'::text AS funntype,
                   (current_date - r.gyldig_til) AS over_grense,
                   r.standard || ' ' || r.versjon AS detalj,
                   (SELECT versjon FROM krav) AS kravversjon
              FROM nyeste n
              JOIN public.ehfregelsett r
                ON r.tenant = v_t AND r.regelsett_id = n.regelsett_id
             WHERE NOT public.m54_regelsett_gyldig(r.gyldig_fra,
                                                   r.gyldig_til)

            UNION ALL
            -- ET FORMFEIL-AVVIK INGEN HAR KLARGJORT EN RETTING FOR.
            SELECT n.validering_id, 'avvik_uten_retting',
                   n.antall_feil, NULL::text, (SELECT versjon FROM krav)
              FROM nyeste n
             WHERE EXISTS (
                 SELECT 1 FROM public.ehfavvik a
                  WHERE a.tenant = v_t
                    AND a.validering_id = n.validering_id
                    AND a.alvorlighet = 'feil'
                    AND NOT EXISTS (
                        SELECT 1 FROM public.ehfretting r2
                         WHERE r2.tenant = v_t
                           AND r2.avvik_id = a.avvik_id))

            UNION ALL
            -- EN RETTING SOM ER KLARGJORT MEN IKKE MERKET KLAR.
            SELECT n.validering_id, 'retting_ikke_klar', NULL::int,
                   NULL::text, (SELECT versjon FROM krav)
              FROM nyeste n
             WHERE EXISTS (
                 SELECT 1 FROM public.ehfretting r3
                  JOIN public.ehfavvik a3
                    ON a3.tenant = r3.tenant
                   AND a3.avvik_id = r3.avvik_id
                  WHERE r3.tenant = v_t
                    AND a3.validering_id = n.validering_id
                    AND NOT r3.klar_til_signering)
        ),
        skrevet AS (
            INSERT INTO public.ehffunn
                (tenant, funn_id, validering_id, funntype,
                 over_grense, detalj, kravversjon)
            SELECT v_t, gen_random_uuid(), k.validering_id,
                   k.funntype, k.over_grense, k.detalj, k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, validering_id, funntype)
                WHERE validering_id IS NOT NULL
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

        PERFORM set_config('disponit.tenant', '', true);
    END LOOP;

    -- LUKKINGEN I EGEN RUNDE (117–120s form). Et funn som ikke lenger
    -- er sant skal ikke bli stående — men det lukkes av at TILSTANDEN
    -- er borte, ikke av at noen trykket.
    --
    -- `validering_mot_utlopt_regelsett` LUKKES OGSÅ HER, og bare her:
    -- når dokumentet er validert på nytt mot et gyldig sett, er
    -- tilstanden borte. Døra `m54_lukk_funn` nekter fremdeles.
    FOREACH v_t IN ARRAY v_tenanter
    LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        WITH krav AS (
            SELECT k.utlopsvarsel_dogn, k.avviksfrist_dogn
              FROM public.ehfkrav k WHERE k.tenant = v_t),
        nyeste AS (
            SELECT DISTINCT ON (v.dokument_id)
                   v.validering_id, v.dokument_id, v.regelsett_id
              FROM public.ehfvalidering v
             WHERE v.tenant = v_t
             ORDER BY v.dokument_id, v.validert DESC,
                      v.validering_id DESC),
        fortsatt AS (
            SELECT r.regelsett_id, NULL::uuid AS dokument_id,
                   NULL::uuid AS validering_id,
                   'ingen_krav'::text AS funntype
              FROM public.ehfregelsett r
             WHERE r.tenant = v_t AND NOT EXISTS (SELECT 1 FROM krav)
            UNION ALL
            SELECT r.regelsett_id, NULL, NULL, 'regelsett_utlopt'
              FROM public.ehfregelsett r
             WHERE r.tenant = v_t AND r.gyldig_til IS NOT NULL
               AND r.gyldig_til < current_date
               AND NOT EXISTS (
                   SELECT 1 FROM public.ehfregelsett r2
                    WHERE r2.tenant = v_t
                      AND r2.standard = r.standard
                      AND public.m54_regelsett_gyldig(r2.gyldig_fra,
                                                      r2.gyldig_til))
            UNION ALL
            SELECT r.regelsett_id, NULL, NULL,
                   'regelsett_utloper_snart'
              FROM public.ehfregelsett r CROSS JOIN krav k
             WHERE r.tenant = v_t AND r.gyldig_til IS NOT NULL
               AND r.gyldig_til >= current_date
               AND r.gyldig_til <= current_date
                   + make_interval(days => k.utlopsvarsel_dogn)
            UNION ALL
            SELECT NULL, d.dokument_id, NULL,
                   'dokument_uten_validering'
              FROM public.ehfdokument d CROSS JOIN krav k
             WHERE d.tenant = v_t
               AND NOT EXISTS (SELECT 1 FROM public.ehfvalidering v
                                WHERE v.tenant = v_t
                                  AND v.dokument_id = d.dokument_id)
               AND d.registrert < now()
                   - make_interval(days => k.avviksfrist_dogn)
            UNION ALL
            SELECT NULL, NULL, n.validering_id,
                   'validering_mot_utlopt_regelsett'
              FROM nyeste n
              JOIN public.ehfregelsett r
                ON r.tenant = v_t AND r.regelsett_id = n.regelsett_id
             WHERE NOT public.m54_regelsett_gyldig(r.gyldig_fra,
                                                   r.gyldig_til)
            UNION ALL
            SELECT NULL, NULL, n.validering_id, 'avvik_uten_retting'
              FROM nyeste n
             WHERE EXISTS (
                 SELECT 1 FROM public.ehfavvik a
                  WHERE a.tenant = v_t
                    AND a.validering_id = n.validering_id
                    AND a.alvorlighet = 'feil'
                    AND NOT EXISTS (
                        SELECT 1 FROM public.ehfretting r2
                         WHERE r2.tenant = v_t
                           AND r2.avvik_id = a.avvik_id))
            UNION ALL
            SELECT NULL, NULL, n.validering_id, 'retting_ikke_klar'
              FROM nyeste n
             WHERE EXISTS (
                 SELECT 1 FROM public.ehfretting r3
                  JOIN public.ehfavvik a3
                    ON a3.tenant = r3.tenant
                   AND a3.avvik_id = r3.avvik_id
                  WHERE r3.tenant = v_t
                    AND a3.validering_id = n.validering_id
                    AND NOT r3.klar_til_signering)
        )
        UPDATE public.ehffunn f
           SET apen = false, lukket_ts = now(),
               lukket_av = 'm54_sveip_ehf',
               lukkenotat = 'tilstanden er ikke lenger til stede'
         WHERE f.tenant = v_t AND f.apen
           AND NOT EXISTS (
               SELECT 1 FROM fortsatt s
                WHERE s.funntype = f.funntype
                  AND s.regelsett_id IS NOT DISTINCT FROM
                      f.regelsett_id
                  AND s.dokument_id IS NOT DISTINCT FROM f.dokument_id
                  AND s.validering_id IS NOT DISTINCT FROM
                      f.validering_id);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        v_lukket := v_lukket + coalesce(v_n, 0);

        PERFORM set_config('disponit.tenant', '', true);
    END LOOP;

    RETURN QUERY SELECT v_antall, v_nye, v_oppdaterte, v_lukket;
END $$;
REVOKE ALL ON FUNCTION m54_sveip_ehf(INT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 6. RLS, radvakter og rettigheter.
-- ------------------------------------------------------------

RESET ROLE;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['ehfkrav', 'ehfregelsett', 'ehfregel',
                             'ehfdokument', 'ehffelt',
                             'ehfvalidering', 'ehfavvik',
                             'ehfretting', 'ehffunn']
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
                       ' disponit_ehf_eier', t);
    END LOOP;
END $$;

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER (111s form, 112–120):
-- bare på REGELSETTABELLEN, bare FOR SELECT, bare til eieren, og bare
-- når ingen tenantkontekst står.
CREATE POLICY m54_sveip_tenantliste ON ehfregelsett
    FOR SELECT TO disponit_ehf_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- HISTORIKKTABELLENE FÅR IKKE UPDATE, HELLER IKKE FOR EIEREN.
--
-- `ehfregelsett`, `ehfregel`, `ehffelt`, `ehfvalidering` og `ehfavvik`
-- ER HELT LUKKET: en regel, en parsing og en dom kan bare oppstå,
-- aldri endres. Det er ikke en radvakt som kan gjøre en feil — det er
-- en rettighet som ikke finnes.
REVOKE UPDATE ON public.ehfregelsett FROM disponit_ehf_eier;
REVOKE UPDATE ON public.ehfregel FROM disponit_ehf_eier;

-- …MEN `gyldig_til` MÅ KUNNE SETTES. Se `m54_sett_gyldig_til`: et
-- standardorgan som kunngjør en sluttdato i juni er nettopp den
-- endringen klyngen finnes for å følge med på, og et helt frosset sett
-- ville tvunget oss til å late som vi ikke visste det.
--
-- EGEN, SNEVER GRANT PÅ KOLONNEN (119s form): settets IDENTITET —
-- standard, versjon, `gyldig_fra`, innholdssummen — er frosset uten at
-- en radvakt må gjette hva som er lov.
GRANT UPDATE (gyldig_til) ON public.ehfregelsett
    TO disponit_ehf_eier;
REVOKE UPDATE ON public.ehfdokument FROM disponit_ehf_eier;
REVOKE UPDATE ON public.ehffelt FROM disponit_ehf_eier;
REVOKE UPDATE ON public.ehfvalidering FROM disponit_ehf_eier;
REVOKE UPDATE ON public.ehfavvik FROM disponit_ehf_eier;

-- `ehfretting` BEHOLDER UPDATE — klarmerket må kunne settes.
-- Åpningen lukkes fra den andre siden, av radvakten under.
CREATE FUNCTION m54_retting_frosset()
RETURNS TRIGGER LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF OLD.klar_til_signering THEN
        RAISE EXCEPTION 'ehfretting: rettingen % er merket klar og er'
            ' frosset — en ny retting hører til et nytt avvik',
            OLD.retting_id USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.retting_id IS DISTINCT FROM OLD.retting_id
       OR NEW.avvik_id IS DISTINCT FROM OLD.avvik_id
       OR NEW.felt_sti IS DISTINCT FROM OLD.felt_sti
       OR NEW.fra_verdi IS DISTINCT FROM OLD.fra_verdi
       OR NEW.til_verdi IS DISTINCT FROM OLD.til_verdi
       OR NEW.begrunnelse IS DISTINCT FROM OLD.begrunnelse
       OR NEW.registrert IS DISTINCT FROM OLD.registrert
       OR NEW.registrert_av IS DISTINCT FROM OLD.registrert_av THEN
        RAISE EXCEPTION 'ehfretting: rettingens egne felter er'
            ' frosset — bare klarmerket kan settes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION m54_regelsett_frosset()
RETURNS TRIGGER LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    -- IDENTITETEN ER FROSSET. Kolonnegranten over gjør at bare
    -- `gyldig_til` KAN endres; denne vakten sier det én gang til, med
    -- en lesbar beskjed — og fanger den dagen noen gir eieren en
    -- videre grant uten å lese hvorfor den var snever.
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.regelsett_id IS DISTINCT FROM OLD.regelsett_id
       OR NEW.standard IS DISTINCT FROM OLD.standard
       OR NEW.versjon IS DISTINCT FROM OLD.versjon
       OR NEW.gyldig_fra IS DISTINCT FROM OLD.gyldig_fra
       OR NEW.innhold_sha256 IS DISTINCT FROM OLD.innhold_sha256
       OR NEW.kilde_url IS DISTINCT FROM OLD.kilde_url
       OR NEW.registrert IS DISTINCT FROM OLD.registrert
       OR NEW.registrert_av IS DISTINCT FROM OLD.registrert_av THEN
        RAISE EXCEPTION 'ehfregelsett: settets identitet er frosset —'
            ' bare gyldig_til kan settes. Identiteten er det som gjør'
            ' en gammel validering etterprøvbar'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER ehfregelsett_frosset
    BEFORE UPDATE ON ehfregelsett
    FOR EACH ROW EXECUTE FUNCTION m54_regelsett_frosset();

CREATE TRIGGER ehfretting_frosset
    BEFORE UPDATE ON ehfretting
    FOR EACH ROW EXECUTE FUNCTION m54_retting_frosset();

-- SLETTING ER ALDRI LOVLIG.
REVOKE DELETE ON public.ehfkrav FROM disponit_ehf_eier;
REVOKE DELETE ON public.ehfregelsett FROM disponit_ehf_eier;
REVOKE DELETE ON public.ehfregel FROM disponit_ehf_eier;
REVOKE DELETE ON public.ehfdokument FROM disponit_ehf_eier;
REVOKE DELETE ON public.ehffelt FROM disponit_ehf_eier;
REVOKE DELETE ON public.ehfvalidering FROM disponit_ehf_eier;
REVOKE DELETE ON public.ehfavvik FROM disponit_ehf_eier;
REVOKE DELETE ON public.ehfretting FROM disponit_ehf_eier;
REVOKE DELETE ON public.ehffunn FROM disponit_ehf_eier;

-- KJØRETIDSROLLEN FÅR DØRENE, ALDRI TABELLENE.
--
-- GRANTENE GIS AV EIEREN (116s lærdom i den mildere formen: en GRANT
-- fra migratoren på en funksjon den ikke eier er en feil, ikke en
-- no-op). Vaktet i tillegg: `REVOKE ... FROM <rolle som ikke finnes>`
-- er en FEIL i PostgreSQL (målt i 117).
SET LOCAL ROLE disponit_ehf_eier;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m54_ehfstatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m54_kravene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m54_regelsettene(TEXT, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m54_reglene(TEXT, UUID)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m54_dokumentene(TEXT, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m54_avvikene(TEXT, UUID)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m54_valideringene(TEXT, UUID) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m54_funnene(TEXT, BOOLEAN) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m54_sett_krav(TEXT, INT, INT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m54_registrer_regelsett(TEXT, UUID, TEXT, TEXT, DATE,'
            ' DATE, TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m54_registrer_regel(TEXT, UUID, UUID, TEXT, TEXT, TEXT,'
            ' TEXT[], TEXT, TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m54_sett_gyldig_til(TEXT, UUID, DATE, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m54_registrer_dokument(TEXT, UUID, TEXT, TEXT, TEXT,'
            ' DATE, TEXT, BIGINT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m54_registrer_felter(TEXT, UUID, TEXT[], INT[], TEXT[],'
            ' BIGINT[], TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m54_valider_dokument(TEXT, UUID, UUID, UUID, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m54_registrer_retting(TEXT, UUID, UUID, TEXT, TEXT,'
            ' TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m54_merk_klar(TEXT, UUID, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m54_lukk_funn(TEXT, UUID, TEXT, TEXT) TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_ehfsveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m54_sveip_ehf(INT)'
            ' TO disponit_ehfsveip';
    END IF;
END $$;

-- SVEIPEN ER IKKE KJØRETIDSROLLENS.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'REVOKE EXECUTE ON FUNCTION m54_sveip_ehf(INT)'
            ' FROM disponit';
    END IF;
END $$;

RESET ROLE;
