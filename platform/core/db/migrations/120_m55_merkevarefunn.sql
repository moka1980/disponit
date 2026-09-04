-- 120: M-55 merkevare- og IP-overvåker v1 — BEVISET, IKKE KRAVET.
-- Seks tenant-skopede tabeller, sytten dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- V1-DOMMEN, ORDRETT FRA VAKTEN: «Sender aldri juridiske krav eller
-- klager. Modulen dokumenterer og rapporterer; enhver reaksjon
-- besluttes av menneske.»
--
-- ET KRAV SENDT PÅ ET AUTOMATISK FUNN ER EN ANKLAGE MOT EN NAVNGITT
-- PART, og en feilaktig anklage er ikke reversibel ved å trekke den.
-- Spesifikasjonen parkerte «automatisk varselbrev ved IP-brudd» med
-- nettopp den begrunnelsen. Klyngens fire andre moduler holder også
-- tilbake en utgående handling; her er den utgående handlingen den
-- eneste med en navngitt motpart på den andre siden.
--
-- DERFOR GJØR v1 ÉN TING: DOKUMENTERER FUNNET SLIK AT DET HOLDER SOM
-- BEVIS. Alt annet er menneskets.
--
-- OG DET ER DEN SETNINGEN SOM FORMER DATAMODELLEN.
--
-- Et merkevarefunn er en påstand om at NOEN ANDRE bruker noe som
-- ligner vårt. Påstanden er verdiløs uten tre ting: hvor det sto,
-- når det sto der, og hva som faktisk sto der. De to første er
-- tekst. Den tredje er en KOPI — og uten den er funnet ikke bare
-- svakt, det er VERRE ENN INGEN FUNN, fordi noen handler på det.
--
-- En nettside som er endret eller borte den dagen saken tas opp, er
-- ingen sak. Det er hele grunnen til at bevaringskopien finnes.
--
-- DERFOR: `merkevarefunn.kopi_id` ER `NOT NULL` MED FREMMEDNØKKEL TIL
-- `bevaringskopi`. Invarianten `funn_uten_bevaringskopi` er dermed
-- ikke en regel noen må huske — det er formen på tabellen. Et funn
-- uten bevaringskopi KAN IKKE UTTRYKKES.
--
-- FORVEKSLINGSVURDERINGEN ER DETERMINISTISK, OG TERSKELEN ER
-- TENANTENS.
--
-- To halvdeler av samme dom, og begge er invarianter:
--
--   `forvekslingsvurdering_uten_grunnlag` — vurderingen bærer
--   `grunnlag TEXT[]` med minst ett element, OG de to tekstene den
--   sammenlignet, snapshotet på raden. Uten dem kan ingen etterpå
--   regne etter, og en vurdering ingen kan regne etter er en
--   mening — ikke et bevis.
--
--   `forvekslingsterskel_hardkodet` — hvor likt noe må være før det
--   er forveksling er en forretnings- og juridisk vurdering. Et
--   varemerke i en nisje tåler mindre likhet enn et generisk ord.
--   Terskelen står i `merkevarekrav`, settes gjennom en dør, og
--   SNAPSHOTES på hver vurdering: `terskel_brukt`. Endrer tenanten
--   terskelen i morgen, står gårsdagens vurdering fremdeles med den
--   terskelen den faktisk ble gjort under.
--
-- LIKHETEN REGNES I BASEN, av `m55_likhet` — IMMUTABLE, ren
-- redigeringsavstand over normaliserte strenger, ingen ordbok og
-- ingen gjetning. Samme inn gir samme ut, i dag og om tre år, lokalt
-- og på staging. Manifestets sjekklistepunkt krever nettopp det:
-- vurderingen er deterministisk eller ingenting.
--
-- v1 GJØR INGEN UTGÅENDE FORESPØRSEL. M-48 fikk klyngens ene unntak
-- (eierbeslutning 3/9), og M-19s begrunnelse gjelder ikke her:
-- modulen ville sendt VÅRE EGNE merkevarenavn til et søke-API, ikke
-- kundedata. Grunnen er en annen og enklere — et overvåkingsoppslag
-- mot tredjeparts annonseplattformer og domeneregistre hører hjemme i
-- oppdragskontraktens `ekstern_lesing` med målautorisasjon, ikke i en
-- modulfil. Bevaringskopien REGISTRERES av den som tok den.
--
-- DOMMENE v1 HVILER PÅ, HÅNDHEVET I DATAMODELLEN:
--
--   1. BEVISET OVERSKRIVES ALDRI. Bevaringskopier, funn og
--      vurderinger er append-only, håndhevet av radvakter. M-42s dom
--      (110), gjentatt i 112–119. `merkevarefunn_overskrevet` er den
--      invarianten her — og den er skarpere enn i de andre modulene,
--      fordi et endret bevis ikke er et svakere bevis: det er et
--      bevis som ikke lenger beviser noe.
--
--   2. HVERT FUNN HAR EN BEVARINGSKOPI. NOT NULL fremmednøkkel.
--
--   3. HVER VURDERING HAR ET GRUNNLAG OG SINE EGNE INNDATA.
--
--   4. EN NY VURDERING ER EN NY RAD. Endres algoritmen eller
--      terskelen, oppstår en ny vurdering ved siden av den gamle —
--      aldri i stedet for den.
--
--   5. INGEN KOLONNE BETYR «SENDT». Det finnes ingen krav, ingen
--      klage, ingen mottaker og ingen utboks. Et funn kan HENVISES
--      TIL M-37s unntakskø, og der beslutter et menneske.
--
-- GRENSEN MOT M-37: et funn hører i unntakskøen når et menneske skal
-- se på det. M-55 eier DOKUMENTASJONEN av funnet; M-37 eier køen.
-- `henvist_unntak_id` er hele koblingen, og den peker ut av modulen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_merkevare_eier') THEN
        RAISE EXCEPTION 'rollen disponit_merkevare_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_merkevare_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `merkevarekrav` — ÉN per tenant. Tenantens egne terskler.
--
-- INVARIANTEN `forvekslingsterskel_hardkodet` BOR HER. Hvor likt noe
-- må være før det er forveksling er en forretnings- og juridisk
-- vurdering: et varemerke i en nisje tåler langt mindre likhet enn et
-- generisk ord gjør, og en konstant i koden ville tatt den
-- vurderingen fra den som eier den.
CREATE TABLE merkevarekrav (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- TERSKELEN, i hele prosent likhet. `>=` er forveksling.
    forvekslingsterskel INT NOT NULL DEFAULT 80
        CHECK (forvekslingsterskel BETWEEN 1 AND 100),
    -- Hvor mange døgn et åpent funn kan stå uten at noen har sett på
    -- det før sveipen melder det. Et funn ingen ser på er en sak som
    -- blir eldre uten å bli bedre — og bevis foreldes.
    funnfrist_dogn INT NOT NULL DEFAULT 14
        CHECK (funnfrist_dogn BETWEEN 1 AND 365),
    -- Hvor mange døgn en forveksling over terskel kan stå UHENVIST.
    -- Kortere enn `funnfrist_dogn` med vilje: det er nettopp disse
    -- funnene noen skal se på.
    henvisningsfrist_dogn INT NOT NULL DEFAULT 3
        CHECK (henvisningsfrist_dogn BETWEEN 1 AND 365),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    -- IDEMPOTENSNØKKELEN SOM SATTE DENNE VERSJONEN. Samme dom som i
    -- 119: raden er en singleton og har ingen id å utlede fra
    -- nøkkelen, så uten den her ville et gjenspill bumpet versjonen —
    -- og hver vurdering bærer `kravversjon`.
    siste_nokkel TEXT NOT NULL
        CHECK (siste_nokkel ~ '[^[:space:]]'),
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT merkevarekrav_pk PRIMARY KEY (tenant)
);

-- `merkevare` — VÅRE EGNE merker. Ikke andres.
--
-- FROSSET bortsett fra `aktiv`: navnet et funn ble vurdert mot er en
-- del av vurderingen, og et navn som kunne redigeres i ettertid ville
-- gjort hver eldre vurdering uetterprøvbar.
CREATE TABLE merkevare (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    merkevare_id UUID NOT NULL,
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    -- NORMALISERT FORM, REGNET I BASEN AV `m55_normaliser` (117s
    -- form). Lagret ved siden av det oppgitte navnet, ikke i stedet
    -- for det: uten begge kan ingen etterpå se om en vurdering traff
    -- på navnet eller på normaliseringen av det.
    navn_normalisert TEXT NOT NULL
        CHECK (navn_normalisert ~ '[^[:space:]]'),
    -- HVA SLAGS MERKE. Lukket liste: et domenenavn og en logo er
    -- ikke samme sak juridisk, og et funn må kunne skilles på det.
    art TEXT NOT NULL
        CONSTRAINT merkevare_art_lukket CHECK (art IN (
            'varemerke', 'domenenavn', 'firmanavn', 'produktnavn',
            'logo', 'slagord')),
    -- REGISTRERINGEN, hvis den finnes. Et registrert varemerke og et
    -- innarbeidet kjennetegn har ikke samme vern, og den forskjellen
    -- skal stå på raden — ikke i hodet til den som leser den.
    registernummer TEXT
        CHECK (registernummer IS NULL
               OR registernummer ~ '[^[:space:]]'),
    registerfoerer TEXT
        CHECK (registerfoerer IS NULL
               OR registerfoerer ~ '[^[:space:]]'),
    CONSTRAINT merkevare_register_henger_sammen CHECK (
        (registernummer IS NULL) = (registerfoerer IS NULL)),
    -- Klassene merket er registrert i (Nice-klasser). FRI TEKST i en
    -- tabell: klassifiseringen er registerførerens, ikke vår.
    vareklasser TEXT[] NOT NULL DEFAULT '{}',
    gjelder_fra DATE NOT NULL,
    aktiv BOOLEAN NOT NULL DEFAULT true,
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT merkevare_pk PRIMARY KEY (tenant, merkevare_id),
    CONSTRAINT merkevare_navn_unik UNIQUE (tenant, art, navn)
);

-- `bevaringskopi` — HELE POENGET MED MODULEN.
--
-- En nettside som er endret eller borte den dagen saken tas opp, er
-- ingen sak. Kopien er det som gjør et funn til et bevis.
--
-- HELT FROSSET. Ingen kolonne her kan endres etter innsetting, og
-- radvakten håndhever det. Et bevis som kunne redigeres beviser
-- ingenting — det er ikke et svakere bevis, det er et annet.
--
-- v1 HENTER DEN IKKE SELV. Kopien REGISTRERES av den som tok den, med
-- innholdssum og størrelse. Modulen gjør ingen utgående forespørsel
-- (`modulen_hentet_eksternt`); et overvåkingsoppslag mot tredjeparts
-- annonseplattformer hører hjemme i oppdragskontraktens
-- `ekstern_lesing` med målautorisasjon.
CREATE TABLE bevaringskopi (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kopi_id UUID NOT NULL,
    -- HVOR DET STO. Skjemaet er begrenset: en `file:`- eller
    -- `javascript:`-URL i et bevis er ikke en kilde, det er en feil
    -- som har fått stå.
    kilde_url TEXT NOT NULL
        CONSTRAINT bevaringskopi_url_er_web CHECK (
            kilde_url ~ '^https?://[^[:space:]]+$'
            AND length(kilde_url) <= 2000),
    -- NÅR DET STO DER. Den som tok kopien oppgir tidspunktet; det er
    -- ikke det samme som når raden ble skrevet, og forskjellen kan
    -- ha betydning.
    hentet_ts TIMESTAMPTZ NOT NULL,
    -- HVA SOM STO DER. Innholdssummen binder raden til bytene.
    innhold_sha256 TEXT NOT NULL
        CHECK (innhold_sha256 ~ '^[0-9a-f]{64}$'),
    innhold_bytes BIGINT NOT NULL CHECK (innhold_bytes > 0),
    medietype TEXT NOT NULL CHECK (medietype ~ '^[a-z]+/[a-z0-9.+-]+$'),
    -- HVOR BYTENE LIGGER. Artefaktlageret eier dem; denne raden eier
    -- påstanden om hva de er.
    lagringsnokkel TEXT NOT NULL
        CHECK (lagringsnokkel ~ '[^[:space:]]'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT bevaringskopi_pk PRIMARY KEY (tenant, kopi_id),
    -- SAMME BYTES FRA SAMME URL PÅ SAMME TIDSPUNKT ER ÉN KOPI.
    CONSTRAINT bevaringskopi_unik UNIQUE (
        tenant, kilde_url, hentet_ts, innhold_sha256),
    -- EN KOPI FRA FRAMTIDA ER EN FEIL, ikke et bevis.
    CONSTRAINT bevaringskopi_ikke_fra_framtida CHECK (
        hentet_ts <= registrert + INTERVAL '1 hour')
);

-- `merkevarefunn` — PÅSTANDEN, MED BEVISET FESTET.
--
-- INVARIANTEN `funn_uten_bevaringskopi` ER FORMEN PÅ DENNE TABELLEN:
-- `kopi_id` er NOT NULL med fremmednøkkel. Et funn uten bevaringskopi
-- kan ikke uttrykkes — det er ikke en regel noen må huske.
--
-- APPEND-ONLY. Radvakten fryser alt bortsett fra henvisningen og
-- lukkingen, og de to er tillegg, ikke redigeringer. Invarianten
-- heter `merkevarefunn_overskrevet`, og den er skarpere her enn i de
-- fire andre modulene i klyngen: et endret bevis er ikke et svakere
-- bevis, det er et bevis som ikke lenger beviser noe.
--
-- INGEN KOLONNE BETYR «SENDT». Det finnes ingen mottaker, ingen
-- kravtekst og ingen utboks. `henvist_unntak_id` peker inn i M-37s
-- unntakskø, og DER beslutter et menneske.
CREATE TABLE merkevarefunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    funn_id UUID NOT NULL,
    merkevare_id UUID NOT NULL,
    -- BEVISET. NOT NULL, med fremmednøkkel.
    kopi_id UUID NOT NULL,
    -- HVA SOM FAKTISK STO DER. Ordrett, slik det ble observert.
    observert_navn TEXT NOT NULL
        CHECK (observert_navn ~ '[^[:space:]]'
               AND length(observert_navn) <= 500),
    observert_normalisert TEXT NOT NULL
        CHECK (observert_normalisert ~ '[^[:space:]]'),
    -- HVOR PÅ SIDEN, OG I HVILKEN BRUK. Lukket liste: et domenenavn
    -- og en annonsetekst er ikke samme sak, og forskjellen avgjør
    -- hva slags reaksjon som i det hele tatt er mulig.
    bruksform TEXT NOT NULL
        CONSTRAINT merkevarefunn_bruksform_lukket CHECK (bruksform IN (
            'domenenavn', 'annonsetekst', 'produktnavn', 'firmanavn',
            'sosial_konto', 'markedsplassoppforing', 'annet')),
    -- KONTEKSTEN, i den observerendes egne ord. Påkrevd: «dette
    -- ligner» uten å si hvor det sto, er ikke dokumentasjon.
    kontekst TEXT NOT NULL
        CHECK (kontekst ~ '[^[:space:]]' AND length(kontekst) <= 4000),
    -- HVEM SOM STÅR BAK, hvis det er kjent. NULL er et ærlig svar og
    -- er ikke det samme som «ingen» — derfor ingen tom streng.
    motpart TEXT CHECK (motpart IS NULL OR motpart ~ '[^[:space:]]'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (registrert_av ~ '[^[:space:]]'),
    -- HENVISNINGEN TIL M-37. Hele koblingen ut av modulen.
    henvist_unntak_id UUID,
    henvist_ts TIMESTAMPTZ,
    henvist_av TEXT CHECK (henvist_av IS NULL
                           OR henvist_av ~ '[^[:space:]]'),
    CONSTRAINT merkevarefunn_henvisning_er_hel CHECK (
        num_nulls(henvist_unntak_id, henvist_ts, henvist_av)
            IN (0, 3)),
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT CHECK (lukket_av IS NULL
                          OR lukket_av ~ '[^[:space:]]'),
    -- LUKKING KREVER EN BEGRUNNELSE. Samme dom som M-49s avklaring
    -- (117): et funn som forsvinner uten at noen skrev hvorfor, er en
    -- beslutning ingen kan etterprøve.
    lukkebegrunnelse TEXT
        CHECK (lukkebegrunnelse IS NULL
               OR (length(btrim(lukkebegrunnelse)) >= 4
                   AND length(lukkebegrunnelse) <= 4000)),
    CONSTRAINT merkevarefunn_lukking_er_hel CHECK (
        num_nulls(lukket_ts, lukket_av, lukkebegrunnelse) IN (0, 3)),
    CONSTRAINT merkevarefunn_pk PRIMARY KEY (tenant, funn_id),
    CONSTRAINT merkevarefunn_merke_fk FOREIGN KEY
        (tenant, merkevare_id)
        REFERENCES merkevare (tenant, merkevare_id),
    CONSTRAINT merkevarefunn_kopi_fk FOREIGN KEY (tenant, kopi_id)
        REFERENCES bevaringskopi (tenant, kopi_id),
    -- SAMME OBSERVASJON PÅ SAMME KOPI ER ÉTT FUNN.
    CONSTRAINT merkevarefunn_unik UNIQUE (
        tenant, merkevare_id, kopi_id, observert_navn)
);

CREATE INDEX merkevarefunn_apne_idx ON merkevarefunn
    (tenant, merkevare_id) WHERE lukket_ts IS NULL;

-- `forvekslingsvurdering` — TALLET, MED ALT SOM SKAL TIL FOR Å REGNE
-- DET ETTER.
--
-- INVARIANTEN `forvekslingsvurdering_uten_grunnlag` ER TO TING PÅ
-- DENNE RADEN, og begge er nødvendige:
--
--   `grunnlag TEXT[]` med minst ett element — HVA likheten hviler på.
--   `merkenavn_ved_vurdering` og `observert_ved_vurdering` — de to
--   tekstene som faktisk ble sammenlignet, snapshotet.
--
-- Uten dem er tallet en mening. Med dem kan hvem som helst regne
-- etter med `m55_likhet` og få nøyaktig samme svar — i dag, om tre år,
-- lokalt og på staging.
--
-- `over_terskel` ER GENERERT. Dommen kan ikke være uenig med sine
-- egne tall: `likhet >= terskel_brukt`, regnet av basen, ikke skrevet
-- av noen.
--
-- EN NY VURDERING ER EN NY RAD. Endres algoritmen eller terskelen,
-- oppstår en ny vurdering VED SIDEN AV den gamle — aldri i stedet
-- for. Det er dom 4, og unikhetsnøkkelen håndhever den.
CREATE TABLE forvekslingsvurdering (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    vurdering_id UUID NOT NULL,
    funn_id UUID NOT NULL,
    -- INNDATAENE, SNAPSHOTET. Merkenavnet kan ikke endres (`merkevare`
    -- er frosset), men det kan DEAKTIVERES og et nytt merke med samme
    -- navn kan aldri oppstå — så snapshotet er belte og seler for det
    -- ene spørsmålet som betyr noe: hva ble sammenlignet.
    merkenavn_ved_vurdering TEXT NOT NULL
        CHECK (merkenavn_ved_vurdering ~ '[^[:space:]]'),
    observert_ved_vurdering TEXT NOT NULL
        CHECK (observert_ved_vurdering ~ '[^[:space:]]'),
    -- TALLET, i hele prosent.
    likhet INT NOT NULL CHECK (likhet BETWEEN 0 AND 100),
    -- TERSKELEN SLIK DEN VAR DA. Endrer tenanten den i morgen, står
    -- denne vurderingen fremdeles med den terskelen den ble gjort
    -- under — `forvekslingsterskel_hardkodet`, sett bakover i tid.
    terskel_brukt INT NOT NULL
        CHECK (terskel_brukt BETWEEN 1 AND 100),
    kravversjon INT NOT NULL CHECK (kravversjon >= 1),
    -- DOMMEN, REGNET. Ikke skrevet.
    over_terskel BOOLEAN NOT NULL
        GENERATED ALWAYS AS (likhet >= terskel_brukt) STORED,
    -- HVA LIKHETEN HVILER PÅ. Minst ett element, alltid.
    grunnlag TEXT[] NOT NULL
        CONSTRAINT forvekslingsvurdering_grunnlag_finnes CHECK (
            cardinality(grunnlag) >= 1)
        CONSTRAINT forvekslingsvurdering_grunnlag_lukket CHECK (
            grunnlag <@ ARRAY['identisk_normalisert', 'redigeringsavstand',
                              'delstreng', 'ordoverlapp',
                              'samme_bruksform_som_merket']),
    -- ALGORITMEVERSJONEN. En ny algoritme gir en NY rad, aldri en
    -- endret. Uten den kunne to tall fra to tidspunkter se ut som
    -- samme måling.
    algoritmeversjon TEXT NOT NULL
        CHECK (algoritmeversjon ~ '^[a-z0-9.-]+$'),
    vurdert TIMESTAMPTZ NOT NULL DEFAULT now(),
    vurdert_av TEXT NOT NULL CHECK (vurdert_av ~ '[^[:space:]]'),
    CONSTRAINT forvekslingsvurdering_pk PRIMARY KEY
        (tenant, vurdering_id),
    CONSTRAINT forvekslingsvurdering_funn_fk FOREIGN KEY
        (tenant, funn_id)
        REFERENCES merkevarefunn (tenant, funn_id),
    -- SAMME FUNN, SAMME ALGORITME, SAMME KRAVVERSJON → SAMME SVAR.
    -- Å regne det to ganger er ikke to vurderinger.
    CONSTRAINT forvekslingsvurdering_unik UNIQUE (
        tenant, funn_id, algoritmeversjon, kravversjon)
);

CREATE INDEX forvekslingsvurdering_funn_idx ON forvekslingsvurdering
    (tenant, funn_id, vurdert DESC);

-- `merkevarevarsel` — NATTENS FUNN OM DAGENS FUNN.
--
-- Sveipens egne observasjoner, ikke IP-funnene selv. Formen er den
-- samme som i 112–119: (tenant, nøkkel, varseltype) er unik, `apen`
-- er tilstanden, og et lukket varsel kan gjenåpnes av neste sveip
-- hvis tilstanden består.
--
-- `forveksling_ikke_henvist` KAN IKKE LUKKES. Samme figur som M-49s
-- bekreftede treff (117), M-46s udekkede absolutte krav (118) og
-- M-51s takfunn (119) — og her er grunnen den skarpeste: en
-- forveksling over tenantens EGEN terskel som ingen har sett på, er
-- nøyaktig det modulen finnes for å vise. Kunne den lukkes uten
-- henvisning, ville modulens eneste utgang vært viskbar.
CREATE TABLE merkevarevarsel (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    varsel_id UUID NOT NULL,
    -- ALLTID FESTET TIL ET MERKE, som 119s funn alltid er festet til
    -- en ordning. Et varsel uten adresse er en beskjed ingen eier.
    merkevare_id UUID NOT NULL,
    -- FUNNIVÅ NÅR VARSELET GJELDER ETT FUNN, NULL når det gjelder
    -- merket. To varsler av samme type på samme merke er ÉN sak; to
    -- uhenviste forvekslinger er TO.
    funn_id UUID,
    varseltype TEXT NOT NULL
        CONSTRAINT merkevarevarsel_type_lukket CHECK (varseltype IN (
            'funn_uten_vurdering', 'forveksling_ikke_henvist',
            'vurdering_med_utdatert_terskel', 'funn_eldre_enn_frist',
            'merkevare_uten_funn', 'ingen_terskler')),
    -- FUNNIVÅ OG MERKENIVÅ ER IKKE VALGFRITT PER TYPE. Uten dette
    -- kunne den samme tilstanden meldes på to nivåer og telles to
    -- ganger.
    CONSTRAINT merkevarevarsel_nivaa_folger_type CHECK (
        (varseltype IN ('merkevare_uten_funn', 'ingen_terskler'))
            = (funn_id IS NULL)),
    -- HVOR MYE OVER. `over_grense` er døgn eller prosentpoeng,
    -- avhengig av type — og `detalj` sier hvilket.
    over_grense INT,
    detalj TEXT CHECK (detalj IS NULL OR detalj ~ '[^[:space:]]'),
    -- LIKHETEN PÅ VARSELET. «Over terskel» uten å si hvor mye er en
    -- beskjed man ikke kan handle på (119s lærdom).
    likhet INT CHECK (likhet IS NULL OR likhet BETWEEN 0 AND 100),
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
    CONSTRAINT merkevarevarsel_lukking_er_hel CHECK (
        num_nulls(lukket_ts, lukket_av, lukkenotat) IN (0, 3)),
    CONSTRAINT merkevarevarsel_apen_er_ulukket CHECK (
        apen = (lukket_ts IS NULL)),
    CONSTRAINT merkevarevarsel_pk PRIMARY KEY (tenant, varsel_id),
    CONSTRAINT merkevarevarsel_merke_fk FOREIGN KEY
        (tenant, merkevare_id)
        REFERENCES merkevare (tenant, merkevare_id),
    CONSTRAINT merkevarevarsel_funn_fk FOREIGN KEY (tenant, funn_id)
        REFERENCES merkevarefunn (tenant, funn_id)
);

-- TO DELVISE UNIKHETSINDEKSER, ikke én sammensatt nøkkel: en
-- primærnøkkel med `funn_id` i seg kunne ikke rommet merkenivået,
-- fordi NULL aldri er lik NULL. Sveipen treffer nøyaktig én av dem
-- per opsjon, og `ON CONFLICT` peker på indeksens eget predikat.
CREATE UNIQUE INDEX merkevarevarsel_merkenivaa_unik
    ON merkevarevarsel (tenant, merkevare_id, varseltype)
 WHERE funn_id IS NULL;
CREATE UNIQUE INDEX merkevarevarsel_funnivaa_unik
    ON merkevarevarsel (tenant, funn_id, varseltype)
 WHERE funn_id IS NOT NULL;
CREATE INDEX merkevarevarsel_apne_idx ON merkevarevarsel
    (tenant, varseltype) WHERE apen;


-- ------------------------------------------------------------
-- 2. Dørene. Eieren eier dem, og eierskapet ER fullmakten.
-- ------------------------------------------------------------

-- `krev_tenantkontekst` eies av `disponit_m37_claimer` (111s form), så
-- EXECUTE må gis AV den rollen (116s lærdom: en GRANT fra migratoren
-- er et stille ikke-oppdrag med en advarsel).
GRANT INSERT ON revisjonslogg TO disponit_merkevare_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_merkevare_eier;
RESET ROLE;

-- HERFRA OG TIL SEKSJON 6 EIES ALT SOM LAGES AV MERKEVAREEIEREN.
-- Eierskapet ER fullmakten en SECURITY DEFINER-funksjon kjører med;
-- `SET LOCAL ROLE` må derfor stå FØR den første av dem, ikke etter.
SET LOCAL ROLE disponit_merkevare_eier;

CREATE FUNCTION m55_evidens(p_tenant TEXT, p_merkevare_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm55_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm55_merkevare', 'handling', p_handling,
        'merkevare_id', p_merkevare_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm55_merkevare',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:merkevare', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m55_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- HVA VI SAMMENLIGNER PÅ, OG INGENTING MER (117s form).
--
-- Senker bokstavstørrelse og slår sammen mellomrom. Den
-- translittererer ikke, fjerner ikke suffikser, og gjetter ikke på
-- stavemåter.
--
-- DET ER EN BEVISST BEGRENSNING. En normalisering som GJETTER er en
-- match i forkledning: gjør den «Nordvik AS» om til «nordvik», har
-- den tatt en beslutning ingen kan etterprøve — og her ville den
-- beslutningen stått mellom en navngitt part og en anklage. Slike
-- omforminger hører hjemme som EGNE grunnlagselementer med sitt eget
-- navn, ikke skjult inne i sammenligningsgrunnlaget.
--
-- IMMUTABLE: samme inn gir samme ut, alltid. Det er halvparten av
-- hvorfor vurderingen er deterministisk.
CREATE FUNCTION m55_normaliser(p_tekst TEXT)
RETURNS TEXT LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT lower(btrim(regexp_replace(coalesce(p_tekst, ''),
                                      '\s+', ' ', 'g')))
$$;
GRANT EXECUTE ON FUNCTION m55_normaliser(TEXT) TO PUBLIC;


-- LIKHETEN, REGNET I BASEN.
--
-- REN REDIGERINGSAVSTAND (Levenshtein), skrevet ut her i stedet for
-- lånt fra `fuzzystrmatch`. Grunnen er ikke smak: utvidelsen er ikke
-- garantert installert i hver base modulen skal kjøre i, og en
-- vurdering som gir 83 lokalt og feiler på staging er ikke
-- deterministisk — den er tilfeldig. Manifestets sjekklistepunkt
-- krever nøyaktig samme svar begge steder.
--
-- SVARET ER PROSENT LIKHET: `100 * (1 - avstand / lengste)`, avrundet
-- nedover. Nedover, ikke nærmeste: en avrunding som løfter 79,6 til
-- 80 ville dyttet et funn over en terskel tenanten har satt, og
-- terskelen er ikke omtrentlig.
--
-- LENGDEN ER BEGRENSET til 200 tegn per side. Algoritmen er O(n·m),
-- og et bevis er ikke bedre av at en enkelt rad kan bruke et sekund
-- på seg selv. Lengre tekst kappes — og at den kappes står i
-- grunnlaget, aldri skjult.
--
-- IMMUTABLE, og det er den andre halvparten av determinismen.
CREATE FUNCTION m55_likhet(p_a TEXT, p_b TEXT)
RETURNS INT LANGUAGE plpgsql IMMUTABLE
SET search_path = pg_catalog AS $$
DECLARE
    a TEXT := left(public.m55_normaliser(p_a), 200);
    b TEXT := left(public.m55_normaliser(p_b), 200);
    la INT; lb INT;
    forrige INT[]; naa INT[];
    i INT; j INT; kost INT;
BEGIN
    la := length(a); lb := length(b);
    -- TO TOMME STRENGER ER IKKE 100 % LIKE. De er ingenting, og
    -- ingenting kan ikke forveksles med noe. En modul som svarte 100
    -- her ville laget en forveksling ut av to manglende felter.
    IF la = 0 OR lb = 0 THEN
        RETURN 0;
    END IF;
    IF a = b THEN
        RETURN 100;
    END IF;
    -- Radvis Levenshtein: to rader om gangen, ikke hele matrisen.
    forrige := ARRAY(SELECT generate_series(0, lb));
    FOR i IN 1..la LOOP
        naa := ARRAY[i];
        FOR j IN 1..lb LOOP
            kost := CASE WHEN substr(a, i, 1) = substr(b, j, 1)
                         THEN 0 ELSE 1 END;
            naa := naa || least(
                naa[j] + 1,                 -- innsetting
                forrige[j + 1] + 1,         -- sletting
                forrige[j] + kost);         -- bytte
        END LOOP;
        forrige := naa;
    END LOOP;
    RETURN (100 * (greatest(la, lb) - forrige[lb + 1]))
           / greatest(la, lb);
END $$;
GRANT EXECUTE ON FUNCTION m55_likhet(TEXT, TEXT) TO PUBLIC;


-- GRUNNLAGET: HVA LIKHETEN HVILER PÅ.
--
-- Ikke et tall til, men en LISTE over hvilke observasjoner som er
-- sanne om paret. `forvekslingsvurdering_uten_grunnlag` er porten:
-- et tall alene er en mening, et tall med grunnlag er et argument.
--
-- Elementene er faktapåstander, ikke vekter. Ingen av dem endrer
-- likheten — de forklarer den, og de kan sjekkes av hvem som helst
-- med de to tekstene i hånd.
CREATE FUNCTION m55_grunnlag(p_merke TEXT, p_observert TEXT,
                             p_samme_bruksform BOOLEAN)
RETURNS TEXT[] LANGUAGE plpgsql IMMUTABLE
SET search_path = pg_catalog AS $$
DECLARE
    a TEXT := public.m55_normaliser(p_merke);
    b TEXT := public.m55_normaliser(p_observert);
    ut TEXT[] := '{}';
BEGIN
    IF a <> '' AND a = b THEN
        ut := ut || 'identisk_normalisert'::text;
    END IF;
    IF a <> '' AND b <> '' AND a <> b
       AND (position(a IN b) > 0 OR position(b IN a) > 0) THEN
        ut := ut || 'delstreng'::text;
    END IF;
    IF a <> '' AND b <> '' AND EXISTS (
        SELECT 1 FROM unnest(string_to_array(a, ' ')) o
         WHERE length(o) >= 3
           AND o = ANY (string_to_array(b, ' '))) THEN
        ut := ut || 'ordoverlapp'::text;
    END IF;
    IF p_samme_bruksform THEN
        ut := ut || 'samme_bruksform_som_merket'::text;
    END IF;
    -- `::text` PÅ HVERT ELEMENT ER IKKE PYNT: uten den leser
    -- PostgreSQL `array || 'ord'` som array-mot-array og feiler på
    -- «malformed array literal» (målt). Casten velger
    -- array-mot-element.
    --
    -- REDIGERINGSAVSTAND STÅR ALLTID. Den er den ene som ALLTID er
    -- sann om paret — tallet kom fra den — og uten den kunne
    -- grunnlaget bli tomt, som CHECK-en forbyr. At den står alene
    -- betyr da noe presist: likheten er ren avstand, ingenting annet.
    ut := ut || 'redigeringsavstand'::text;
    RETURN ut;
END $$;
GRANT EXECUTE ON FUNCTION m55_grunnlag(TEXT, TEXT, BOOLEAN) TO PUBLIC;


-- ALGORITMEVERSJONEN, SOM EN DØR — ikke som en streng spredt utover.
--
-- Hver vurdering bærer den, og unikhetsnøkkelen bruker den: endres
-- `m55_likhet` eller `m55_grunnlag`, bumpes denne, og gamle
-- vurderinger blir stående ved siden av de nye i stedet for å bli
-- borte. Uten den kunne to tall fra to tidspunkter se ut som samme
-- måling.
CREATE FUNCTION m55_algoritmeversjon()
RETURNS TEXT LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$ SELECT 'lev-1'::text $$;
GRANT EXECUTE ON FUNCTION m55_algoritmeversjon() TO PUBLIC;


-- ------------------------------------------------------------
-- 3. Skrivedørene.
-- ------------------------------------------------------------

-- TERSKLENE. Se 119s `m51_sett_krav` for hvorfor nøkkelen står inne i
-- døra: raden er en singleton per tenant og har ingen id å utlede fra
-- nøkkelen, og hver vurdering bærer `kravversjon`.
CREATE FUNCTION m55_sett_krav(
    p_tenant TEXT, p_forvekslingsterskel INT, p_funnfrist_dogn INT,
    p_henvisningsfrist_dogn INT, p_aktor TEXT, p_nokkel TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT; v_nokkel TEXT; v_likt BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm55_sett_krav');
    IF p_nokkel IS NULL OR btrim(p_nokkel) = '' THEN
        RAISE EXCEPTION 'm55_sett_krav: idempotensnøkkel mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.aktor', p_aktor, true);

    -- LÅS FØRST, LES NØKKELEN ETTERPÅ (119s form). Lesningen over en
    -- lås er fra transaksjonens snapshot; et parallelt kall som
    -- committer mens vi venter ville vært usynlig, og to gjenspill
    -- ville da begge bumpet versjonen.
    PERFORM 1 FROM public.merkevarekrav
     WHERE tenant = p_tenant FOR UPDATE;
    SELECT k.versjon, k.siste_nokkel,
           (k.forvekslingsterskel = p_forvekslingsterskel
            AND k.funnfrist_dogn = p_funnfrist_dogn
            AND k.henvisningsfrist_dogn = p_henvisningsfrist_dogn)
      INTO v_versjon, v_nokkel, v_likt
      FROM public.merkevarekrav k WHERE k.tenant = p_tenant;

    IF v_nokkel IS NOT NULL AND v_nokkel = p_nokkel THEN
        IF v_likt THEN
            RETURN v_versjon;
        END IF;
        RAISE EXCEPTION 'm55_sett_krav: nøkkelen % er alt brukt på'
            ' andre verdier', p_nokkel
            USING ERRCODE = 'unique_violation';
    END IF;

    INSERT INTO public.merkevarekrav
        (tenant, forvekslingsterskel, funnfrist_dogn,
         henvisningsfrist_dogn, siste_nokkel, oppdatert_av)
    VALUES (p_tenant, p_forvekslingsterskel, p_funnfrist_dogn,
            p_henvisningsfrist_dogn, btrim(p_nokkel), p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        forvekslingsterskel = EXCLUDED.forvekslingsterskel,
        funnfrist_dogn = EXCLUDED.funnfrist_dogn,
        henvisningsfrist_dogn = EXCLUDED.henvisningsfrist_dogn,
        siste_nokkel = EXCLUDED.siste_nokkel,
        versjon = public.merkevarekrav.versjon + 1,
        oppdatert = now(),
        oppdatert_av = EXCLUDED.oppdatert_av
    RETURNING versjon INTO v_versjon;

    PERFORM public.m55_evidens(p_tenant, NULL, 'merkevarekrav_satt',
        p_aktor, jsonb_build_object(
            'versjon', v_versjon,
            'forvekslingsterskel', p_forvekslingsterskel));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION
    m55_sett_krav(TEXT, INT, INT, INT, TEXT, TEXT) FROM PUBLIC;


-- VÅRT EGET MERKE. Normaliseringen regnes HER, av `m55_normaliser`, og
-- lagres ved siden av det oppgitte navnet — aldri i stedet for det.
CREATE FUNCTION m55_registrer_merkevare(
    p_tenant TEXT, p_merkevare_id UUID, p_navn TEXT, p_art TEXT,
    p_registernummer TEXT, p_registerfoerer TEXT,
    p_vareklasser TEXT[], p_gjelder_fra DATE, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm55_registrer_merkevare');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF public.m55_normaliser(p_navn) = '' THEN
        RAISE EXCEPTION 'm55_registrer_merkevare: navnet er tomt etter'
            ' normalisering — det finnes ingenting å sammenligne mot'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.merkevare
        (tenant, merkevare_id, navn, navn_normalisert, art,
         registernummer, registerfoerer, vareklasser, gjelder_fra,
         registrert_av)
    VALUES (p_tenant, p_merkevare_id, btrim(p_navn),
            public.m55_normaliser(p_navn), p_art,
            nullif(btrim(coalesce(p_registernummer, '')), ''),
            nullif(btrim(coalesce(p_registerfoerer, '')), ''),
            coalesce(p_vareklasser, '{}'), p_gjelder_fra, p_aktor);
    PERFORM public.m55_evidens(p_tenant, p_merkevare_id,
        'merkevare_registrert', p_aktor, jsonb_build_object(
            'art', p_art, 'registrert', p_registernummer IS NOT NULL));
END $$;
REVOKE ALL ON FUNCTION m55_registrer_merkevare(
    TEXT, UUID, TEXT, TEXT, TEXT, TEXT, TEXT[], DATE, TEXT)
    FROM PUBLIC;


-- AKTIVFLAGGET, og INGENTING ANNET. Merket selv er frosset: navnet en
-- vurdering ble gjort mot kan ikke redigeres i ettertid.
CREATE FUNCTION m55_sett_merkevare_aktiv(
    p_tenant TEXT, p_merkevare_id UUID, p_aktiv BOOLEAN, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm55_sett_merkevare_aktiv');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    UPDATE public.merkevare SET aktiv = p_aktiv
     WHERE tenant = p_tenant AND merkevare_id = p_merkevare_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm55_sett_merkevare_aktiv: ukjent merke %',
            p_merkevare_id USING ERRCODE = 'no_data_found';
    END IF;
    PERFORM public.m55_evidens(p_tenant, p_merkevare_id,
        'merkevare_aktiv_satt', p_aktor,
        jsonb_build_object('aktiv', p_aktiv));
END $$;
REVOKE ALL ON FUNCTION
    m55_sett_merkevare_aktiv(TEXT, UUID, BOOLEAN, TEXT) FROM PUBLIC;


-- BEVARINGSKOPIEN. Registreres av den som TOK den — modulen henter
-- ikke (`modulen_hentet_eksternt`).
--
-- Innholdssummen og størrelsen binder raden til bytene i
-- artefaktlageret. Uten dem kunne raden peke på hva som helst, og en
-- bevaringskopi som ikke kan bindes til sitt eget innhold er ikke en
-- bevaringskopi.
CREATE FUNCTION m55_registrer_bevaringskopi(
    p_tenant TEXT, p_kopi_id UUID, p_kilde_url TEXT,
    p_hentet_ts TIMESTAMPTZ, p_innhold_sha256 TEXT,
    p_innhold_bytes BIGINT, p_medietype TEXT, p_lagringsnokkel TEXT,
    p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm55_registrer_bevaringskopi');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_hentet_ts > now() + INTERVAL '1 hour' THEN
        RAISE EXCEPTION 'm55_registrer_bevaringskopi: kopien er'
            ' hentet i framtida (%) — det er en feil, ikke et bevis',
            p_hentet_ts USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.bevaringskopi
        (tenant, kopi_id, kilde_url, hentet_ts, innhold_sha256,
         innhold_bytes, medietype, lagringsnokkel, registrert_av)
    VALUES (p_tenant, p_kopi_id, btrim(p_kilde_url), p_hentet_ts,
            lower(btrim(p_innhold_sha256)), p_innhold_bytes,
            lower(btrim(p_medietype)), btrim(p_lagringsnokkel),
            p_aktor);
    PERFORM public.m55_evidens(p_tenant, NULL,
        'bevaringskopi_registrert', p_aktor, jsonb_build_object(
            'kopi_id', p_kopi_id::text,
            'innhold_sha256', lower(btrim(p_innhold_sha256)),
            'innhold_bytes', p_innhold_bytes));
END $$;
REVOKE ALL ON FUNCTION m55_registrer_bevaringskopi(
    TEXT, UUID, TEXT, TIMESTAMPTZ, TEXT, BIGINT, TEXT, TEXT, TEXT)
    FROM PUBLIC;


-- FUNNET. Kopien er PÅKREVD, og fremmednøkkelen gjør det umulig å
-- omgå: `funn_uten_bevaringskopi` er formen på tabellen.
CREATE FUNCTION m55_registrer_funn(
    p_tenant TEXT, p_funn_id UUID, p_merkevare_id UUID,
    p_kopi_id UUID, p_observert_navn TEXT, p_bruksform TEXT,
    p_kontekst TEXT, p_motpart TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm55_registrer_funn');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF public.m55_normaliser(p_observert_navn) = '' THEN
        RAISE EXCEPTION 'm55_registrer_funn: det observerte navnet er'
            ' tomt etter normalisering — det finnes ingenting å'
            ' sammenligne' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.merkevarefunn
        (tenant, funn_id, merkevare_id, kopi_id, observert_navn,
         observert_normalisert, bruksform, kontekst, motpart,
         registrert_av)
    VALUES (p_tenant, p_funn_id, p_merkevare_id, p_kopi_id,
            btrim(p_observert_navn),
            public.m55_normaliser(p_observert_navn), p_bruksform,
            btrim(p_kontekst),
            nullif(btrim(coalesce(p_motpart, '')), ''), p_aktor);
    PERFORM public.m55_evidens(p_tenant, p_merkevare_id,
        'merkevarefunn_registrert', p_aktor, jsonb_build_object(
            'funn_id', p_funn_id::text,
            'kopi_id', p_kopi_id::text, 'bruksform', p_bruksform));
END $$;
REVOKE ALL ON FUNCTION m55_registrer_funn(
    TEXT, UUID, UUID, UUID, TEXT, TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC;


-- VURDERINGEN. MODULENS KJERNE, OG DEN ENESTE DØRA SOM REGNER NOE.
--
-- TERSKELEN ER TENANTENS, OG DØRA NEKTER UTEN DEN. Ikke fordi et
-- standardtall ville vært vanskelig å velge, men fordi et valgt
-- standardtall ER en hardkodet terskel — `forvekslingsterskel_hardkodet`
-- ville vært brutt av nettopp det vennlige valget. Hvor likt noe må
-- være før det er forveksling er en juridisk vurdering, og modulen
-- eier den ikke.
--
-- SVARET ER DETERMINISTISK. `m55_likhet` og `m55_grunnlag` er
-- IMMUTABLE, og alle inndataene lagres på raden: de to tekstene,
-- terskelen, kravversjonen og algoritmeversjonen. Hvem som helst kan
-- regne etter og få samme tall — lokalt og på staging, i dag og om
-- tre år.
--
-- EN NY VURDERING ER EN NY RAD (dom 4). Unikhetsnøkkelen (funn,
-- algoritme, kravversjon) gjør at samme regnestykke ikke kan bli to
-- vurderinger, og at et NYTT regnestykke ikke kan overskrive det
-- gamle.
CREATE FUNCTION m55_vurder_funn(
    p_tenant TEXT, p_funn_id UUID, p_vurdering_id UUID, p_aktor TEXT)
RETURNS TABLE (likhet INT, terskel_brukt INT, over_terskel BOOLEAN,
               grunnlag TEXT[], kravversjon INT,
               algoritmeversjon TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_terskel INT; v_kravversjon INT;
    v_merke TEXT; v_art TEXT;
    v_observert TEXT; v_bruksform TEXT;
    v_merkevare_id UUID; v_lukket TIMESTAMPTZ;
    v_likhet INT; v_grunnlag TEXT[]; v_algoritme TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm55_vurder_funn');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    SELECT k.forvekslingsterskel, k.versjon
      INTO v_terskel, v_kravversjon
      FROM public.merkevarekrav k WHERE k.tenant = p_tenant;
    IF v_terskel IS NULL THEN
        RAISE EXCEPTION 'm55_vurder_funn: tenanten har ingen'
            ' forvekslingsterskel. Hvor likt noe må være før det er'
            ' forveksling er en juridisk vurdering, ikke en konstant'
            ' — sett den med m55_sett_krav først'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- LÅS FUNNET, LES TILSTANDEN ETTERPÅ. Lesningen over en lås er
    -- fra transaksjonens snapshot: et `m55_lukk_funn` som committer
    -- mens vi venter ville vært usynlig, og vurderingen ville landet
    -- på et funn som alt er lukket og gjennomgått.
    PERFORM 1 FROM public.merkevarefunn
     WHERE tenant = p_tenant AND funn_id = p_funn_id FOR UPDATE;
    SELECT f.merkevare_id, f.observert_navn, f.bruksform, f.lukket_ts
      INTO v_merkevare_id, v_observert, v_bruksform, v_lukket
      FROM public.merkevarefunn f
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;
    IF v_merkevare_id IS NULL THEN
        RAISE EXCEPTION 'm55_vurder_funn: ukjent funn %', p_funn_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_lukket IS NOT NULL THEN
        RAISE EXCEPTION 'm55_vurder_funn: funnet % er lukket — en ny'
            ' vurdering hører til et nytt funn, ikke til et som alt'
            ' er gjennomgått', p_funn_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT m.navn, m.art INTO v_merke, v_art
      FROM public.merkevare m
     WHERE m.tenant = p_tenant AND m.merkevare_id = v_merkevare_id;

    v_likhet := public.m55_likhet(v_merke, v_observert);
    v_grunnlag := public.m55_grunnlag(v_merke, v_observert,
                                      v_art = v_bruksform);
    v_algoritme := public.m55_algoritmeversjon();

    INSERT INTO public.forvekslingsvurdering
        (tenant, vurdering_id, funn_id, merkenavn_ved_vurdering,
         observert_ved_vurdering, likhet, terskel_brukt, kravversjon,
         grunnlag, algoritmeversjon, vurdert_av)
    VALUES (p_tenant, p_vurdering_id, p_funn_id, v_merke, v_observert,
            v_likhet, v_terskel, v_kravversjon, v_grunnlag,
            v_algoritme, p_aktor);

    PERFORM public.m55_evidens(p_tenant, v_merkevare_id,
        'forvekslingsvurdering_gjort', p_aktor, jsonb_build_object(
            'funn_id', p_funn_id::text, 'likhet', v_likhet,
            'terskel_brukt', v_terskel,
            'algoritmeversjon', v_algoritme));

    RETURN QUERY
    SELECT v.likhet, v.terskel_brukt, v.over_terskel, v.grunnlag,
           v.kravversjon, v.algoritmeversjon
      FROM public.forvekslingsvurdering v
     WHERE v.tenant = p_tenant AND v.vurdering_id = p_vurdering_id;
END $$;
REVOKE ALL ON FUNCTION m55_vurder_funn(TEXT, UUID, UUID, TEXT)
    FROM PUBLIC;


-- HENVISNINGEN TIL M-37. HELE UTGANGEN AV MODULEN.
--
-- Det finnes ingen mottaker her, ingen kravtekst og ingen utboks.
-- Funnet får en peker inn i unntakskøen, og DER beslutter et menneske
-- hva som eventuelt skal skje. `modulen_sendte_krav` er ikke en regel
-- vi håndhever — det er en handling som ikke finnes.
--
-- HENVISNINGEN ER ET TILLEGG, IKKE EN REDIGERING. Den kan settes én
-- gang; radvakten nekter å endre den etterpå.
CREATE FUNCTION m55_henvis_funn(
    p_tenant TEXT, p_funn_id UUID, p_unntak_id UUID, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_alt UUID; v_lukket TIMESTAMPTZ; v_merkevare_id UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm55_henvis_funn');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    -- LÅS, SÅ LES (samme dom som over).
    PERFORM 1 FROM public.merkevarefunn
     WHERE tenant = p_tenant AND funn_id = p_funn_id FOR UPDATE;
    SELECT f.henvist_unntak_id, f.lukket_ts, f.merkevare_id
      INTO v_alt, v_lukket, v_merkevare_id
      FROM public.merkevarefunn f
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;
    IF v_merkevare_id IS NULL THEN
        RAISE EXCEPTION 'm55_henvis_funn: ukjent funn %', p_funn_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_alt IS NOT NULL THEN
        RAISE EXCEPTION 'm55_henvis_funn: funnet % er alt henvist'
            ' (unntak %) — en ny henvisning ville skjult den første',
            p_funn_id, v_alt USING ERRCODE = 'unique_violation';
    END IF;
    IF v_lukket IS NOT NULL THEN
        RAISE EXCEPTION 'm55_henvis_funn: funnet % er lukket',
            p_funn_id USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.merkevarefunn
       SET henvist_unntak_id = p_unntak_id, henvist_ts = now(),
           henvist_av = p_aktor
     WHERE tenant = p_tenant AND funn_id = p_funn_id;

    PERFORM public.m55_evidens(p_tenant, v_merkevare_id,
        'merkevarefunn_henvist', p_aktor, jsonb_build_object(
            'funn_id', p_funn_id::text,
            'unntak_id', p_unntak_id::text));
END $$;
REVOKE ALL ON FUNCTION m55_henvis_funn(TEXT, UUID, UUID, TEXT)
    FROM PUBLIC;


-- LUKKINGEN, MED BEGRUNNELSE — OG MED ÉN DØR SOM IKKE ÅPNER.
--
-- ET FUNN VURDERT OVER TENANTENS EGEN TERSKEL KAN IKKE LUKKES UTEN AT
-- DET FØRST ER HENVIST. Samme figur som M-49s bekreftede treff (117),
-- M-46s udekkede absolutte krav (118) og M-51s takfunn (119), og her
-- er grunnen den skarpeste av dem: modulen har nøyaktig én utgang, og
-- kunne den lukkes forbi, ville modulens eneste virkning vært
-- viskbar. Terskelen er tenantens egen — dette er ikke vår mening om
-- hva som er alvorlig.
--
-- ET UVURDERT FUNN KAN LUKKES. «Vi så på det, det var ingenting» er
-- et lovlig svar; det som ikke er lovlig er å lukke noe modulen alt
-- har målt over terskelen uten å sende det videre.
CREATE FUNCTION m55_lukk_funn(
    p_tenant TEXT, p_funn_id UUID, p_begrunnelse TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_lukket TIMESTAMPTZ; v_henvist UUID; v_merkevare_id UUID;
    v_over BOOLEAN; v_likhet INT; v_terskel INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm55_lukk_funn');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    -- LÅS, SÅ LES. Et `m55_vurder_funn` eller `m55_henvis_funn` som
    -- committer mens vi venter på låsen er ellers usynlig — og da
    -- ville et funn som NETTOPP ble målt over terskelen blitt lukket
    -- uten henvisning, som er nøyaktig det denne døra finnes for.
    PERFORM 1 FROM public.merkevarefunn
     WHERE tenant = p_tenant AND funn_id = p_funn_id FOR UPDATE;
    SELECT f.lukket_ts, f.henvist_unntak_id, f.merkevare_id
      INTO v_lukket, v_henvist, v_merkevare_id
      FROM public.merkevarefunn f
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;
    IF v_merkevare_id IS NULL THEN
        RAISE EXCEPTION 'm55_lukk_funn: ukjent funn %', p_funn_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_lukket IS NOT NULL THEN
        RAISE EXCEPTION 'm55_lukk_funn: funnet % er alt lukket',
            p_funn_id USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- NYESTE VURDERING, ikke en vilkårlig av dem.
    SELECT v.over_terskel, v.likhet, v.terskel_brukt
      INTO v_over, v_likhet, v_terskel
      FROM public.forvekslingsvurdering v
     WHERE v.tenant = p_tenant AND v.funn_id = p_funn_id
     ORDER BY v.vurdert DESC, v.vurdering_id DESC
     LIMIT 1;

    IF coalesce(v_over, false) AND v_henvist IS NULL THEN
        -- «prosent» skrevet ut, ikke «%%»: i RAISE er `%` en
        -- plassholder, og `%%%` leses som literal-prosent FULGT AV
        -- plassholder — altså i motsatt rekkefølge av det man mente.
        RAISE EXCEPTION 'm55_lukk_funn: funnet % er vurdert til %'
            ' prosent likhet mot en terskel på % prosent, og er ikke'
            ' henvist. Et funn over tenantens egen terskel lukkes'
            ' ikke her — det henvises til unntakskøen, og et menneske'
            ' beslutter',
            p_funn_id, v_likhet, v_terskel
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.merkevarefunn
       SET lukket_ts = now(), lukket_av = p_aktor,
           lukkebegrunnelse = btrim(p_begrunnelse)
     WHERE tenant = p_tenant AND funn_id = p_funn_id;

    PERFORM public.m55_evidens(p_tenant, v_merkevare_id,
        'merkevarefunn_lukket', p_aktor, jsonb_build_object(
            'funn_id', p_funn_id::text, 'likhet', v_likhet,
            'var_henvist', v_henvist IS NOT NULL));
END $$;
REVOKE ALL ON FUNCTION m55_lukk_funn(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;


-- VARSELET LUKKES, MEN IKKE `forveksling_ikke_henvist`.
CREATE FUNCTION m55_lukk_varsel(
    p_tenant TEXT, p_varsel_id UUID, p_notat TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_type TEXT; v_apen BOOLEAN; v_merkevare_id UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm55_lukk_varsel');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    PERFORM 1 FROM public.merkevarevarsel
     WHERE tenant = p_tenant AND varsel_id = p_varsel_id FOR UPDATE;
    SELECT w.varseltype, w.apen, w.merkevare_id
      INTO v_type, v_apen, v_merkevare_id
      FROM public.merkevarevarsel w
     WHERE w.tenant = p_tenant AND w.varsel_id = p_varsel_id;
    IF v_type IS NULL THEN
        RAISE EXCEPTION 'm55_lukk_varsel: ukjent varsel %',
            p_varsel_id USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_apen THEN
        RAISE EXCEPTION 'm55_lukk_varsel: varselet % er alt lukket',
            p_varsel_id USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_type = 'forveksling_ikke_henvist' THEN
        RAISE EXCEPTION 'm55_lukk_varsel: % kan ikke lukkes. En'
            ' forveksling over tenantens egen terskel som ingen har'
            ' sett på er nøyaktig det modulen finnes for å vise —'
            ' varselet forsvinner når funnet henvises, ikke før',
            v_type USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.merkevarevarsel
       SET apen = false, lukket_ts = now(), lukket_av = p_aktor,
           lukkenotat = btrim(p_notat)
     WHERE tenant = p_tenant AND varsel_id = p_varsel_id;

    PERFORM public.m55_evidens(p_tenant, v_merkevare_id,
        'merkevarevarsel_lukket', p_aktor,
        jsonb_build_object('varseltype', v_type));
END $$;
REVOKE ALL ON FUNCTION m55_lukk_varsel(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Lesedørene.
-- ------------------------------------------------------------

CREATE FUNCTION m55_kravene(p_tenant TEXT)
RETURNS TABLE (forvekslingsterskel INT, funnfrist_dogn INT,
               henvisningsfrist_dogn INT, versjon INT,
               oppdatert TIMESTAMPTZ, oppdatert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm55_kravene');
    RETURN QUERY
    SELECT k.forvekslingsterskel, k.funnfrist_dogn,
           k.henvisningsfrist_dogn, k.versjon, k.oppdatert,
           k.oppdatert_av
      FROM public.merkevarekrav k WHERE k.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m55_kravene(TEXT) FROM PUBLIC;


-- MERKENE, MED TELLINGENE SINE. Åpne funn og uhenviste forvekslinger
-- står per merke: «tre funn» uten å si hvor mange av dem som venter
-- på et menneske er en beskjed man ikke kan handle på.
CREATE FUNCTION m55_merkene(p_tenant TEXT, p_grense INT)
RETURNS TABLE (merkevare_id UUID, navn TEXT, art TEXT,
               registernummer TEXT, registerfoerer TEXT,
               vareklasser TEXT[], gjelder_fra DATE, aktiv BOOLEAN,
               registrert TIMESTAMPTZ, antall_funn BIGINT,
               apne_funn BIGINT, uvurderte BIGINT,
               over_terskel BIGINT, uhenviste BIGINT,
               hoyeste_likhet INT, apne_varsler BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm55_merkene');
    IF p_grense IS NULL OR p_grense < 1 OR p_grense > 500 THEN
        RAISE EXCEPTION 'm55_merkene: grensen må være 1..500 (%)',
            p_grense USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    SELECT m.merkevare_id, m.navn, m.art, m.registernummer,
           m.registerfoerer, m.vareklasser, m.gjelder_fra, m.aktiv,
           m.registrert, coalesce(t.antall, 0), coalesce(t.apne, 0),
           coalesce(t.uvurderte, 0), coalesce(t.over, 0),
           coalesce(t.uhenviste, 0), t.hoyeste,
           coalesce(w.antall, 0)
      FROM public.merkevare m
      LEFT JOIN LATERAL (
           SELECT count(*) AS antall,
                  count(*) FILTER (WHERE f.lukket_ts IS NULL) AS apne,
                  count(*) FILTER (
                      WHERE f.lukket_ts IS NULL
                        AND s.likhet IS NULL) AS uvurderte,
                  count(*) FILTER (
                      WHERE f.lukket_ts IS NULL
                        AND s.over_terskel) AS over,
                  -- UHENVISTE OVER TERSKEL. Det ene tallet som sier
                  -- hvor mange saker som venter på et menneske.
                  count(*) FILTER (
                      WHERE f.lukket_ts IS NULL AND s.over_terskel
                        AND f.henvist_unntak_id IS NULL) AS uhenviste,
                  max(s.likhet) FILTER (
                      WHERE f.lukket_ts IS NULL) AS hoyeste
             FROM public.merkevarefunn f
             LEFT JOIN LATERAL (
                  SELECT vv.likhet, vv.over_terskel
                    FROM public.forvekslingsvurdering vv
                   WHERE vv.tenant = f.tenant
                     AND vv.funn_id = f.funn_id
                   ORDER BY vv.vurdert DESC, vv.vurdering_id DESC
                   LIMIT 1) s ON true
            WHERE f.tenant = m.tenant
              AND f.merkevare_id = m.merkevare_id) t ON true
      LEFT JOIN LATERAL (
           SELECT count(*) AS antall FROM public.merkevarevarsel w2
            WHERE w2.tenant = m.tenant
              AND w2.merkevare_id = m.merkevare_id
              AND w2.apen) w ON true
     WHERE m.tenant = p_tenant
     ORDER BY coalesce(t.uhenviste, 0) DESC, m.aktiv DESC, m.navn
     LIMIT p_grense;
END $$;
REVOKE ALL ON FUNCTION m55_merkene(TEXT, INT) FROM PUBLIC;


-- FUNNENE, MED BEVISET OG NYESTE VURDERING PÅ SAMME RAD.
--
-- Bevaringskopiens URL, tidspunkt og innholdssum står HER, ikke bak
-- et ekstra oppslag: et funn uten sitt bevis synlig er nettopp det
-- modulen finnes for å unngå.
CREATE FUNCTION m55_funnene(p_tenant TEXT, p_merkevare_id UUID,
                            p_grense INT)
RETURNS TABLE (funn_id UUID, merkevare_id UUID, merkenavn TEXT,
               observert_navn TEXT, bruksform TEXT, kontekst TEXT,
               motpart TEXT, registrert TIMESTAMPTZ,
               registrert_av TEXT, kopi_id UUID, kilde_url TEXT,
               hentet_ts TIMESTAMPTZ, innhold_sha256 TEXT,
               innhold_bytes BIGINT, medietype TEXT,
               likhet INT, terskel_brukt INT, over_terskel BOOLEAN,
               grunnlag TEXT[], algoritmeversjon TEXT,
               kravversjon INT, vurdert TIMESTAMPTZ,
               antall_vurderinger BIGINT, henvist_unntak_id UUID,
               henvist_ts TIMESTAMPTZ, henvist_av TEXT,
               lukket_ts TIMESTAMPTZ, lukket_av TEXT,
               lukkebegrunnelse TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm55_funnene');
    IF p_grense IS NULL OR p_grense < 1 OR p_grense > 500 THEN
        RAISE EXCEPTION 'm55_funnene: grensen må være 1..500 (%)',
            p_grense USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    SELECT f.funn_id, f.merkevare_id, m.navn, f.observert_navn,
           f.bruksform, f.kontekst, f.motpart, f.registrert,
           f.registrert_av, f.kopi_id, b.kilde_url, b.hentet_ts,
           b.innhold_sha256, b.innhold_bytes, b.medietype,
           s.likhet, s.terskel_brukt, s.over_terskel, s.grunnlag,
           s.algoritmeversjon, s.kravversjon, s.vurdert,
           coalesce(a.antall, 0), f.henvist_unntak_id, f.henvist_ts,
           f.henvist_av, f.lukket_ts, f.lukket_av, f.lukkebegrunnelse
      FROM public.merkevarefunn f
      JOIN public.merkevare m
        ON m.tenant = f.tenant AND m.merkevare_id = f.merkevare_id
      JOIN public.bevaringskopi b
        ON b.tenant = f.tenant AND b.kopi_id = f.kopi_id
      LEFT JOIN LATERAL (
           SELECT vv.likhet, vv.terskel_brukt, vv.over_terskel,
                  vv.grunnlag, vv.algoritmeversjon, vv.kravversjon,
                  vv.vurdert
             FROM public.forvekslingsvurdering vv
            WHERE vv.tenant = f.tenant AND vv.funn_id = f.funn_id
            ORDER BY vv.vurdert DESC, vv.vurdering_id DESC
            LIMIT 1) s ON true
      LEFT JOIN LATERAL (
           SELECT count(*) AS antall
             FROM public.forvekslingsvurdering v2
            WHERE v2.tenant = f.tenant
              AND v2.funn_id = f.funn_id) a ON true
     WHERE f.tenant = p_tenant
       AND (p_merkevare_id IS NULL
            OR f.merkevare_id = p_merkevare_id)
     ORDER BY (f.lukket_ts IS NULL) DESC,
              coalesce(s.over_terskel, false) DESC,
              coalesce(s.likhet, -1) DESC, f.registrert DESC
     LIMIT p_grense;
END $$;
REVOKE ALL ON FUNCTION m55_funnene(TEXT, UUID, INT) FROM PUBLIC;


-- ALLE VURDERINGENE AV ETT FUNN, i rekkefølge. En ny algoritme eller
-- en ny terskel gir en ny rad, og HELE rekken skal kunne leses: det
-- er der «hva mente vi da» faktisk står.
CREATE FUNCTION m55_vurderingene(p_tenant TEXT, p_funn_id UUID)
RETURNS TABLE (vurdering_id UUID, likhet INT, terskel_brukt INT,
               over_terskel BOOLEAN, grunnlag TEXT[],
               algoritmeversjon TEXT, kravversjon INT,
               merkenavn_ved_vurdering TEXT,
               observert_ved_vurdering TEXT, vurdert TIMESTAMPTZ,
               vurdert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm55_vurderingene');
    RETURN QUERY
    SELECT v.vurdering_id, v.likhet, v.terskel_brukt, v.over_terskel,
           v.grunnlag, v.algoritmeversjon, v.kravversjon,
           v.merkenavn_ved_vurdering, v.observert_ved_vurdering,
           v.vurdert, v.vurdert_av
      FROM public.forvekslingsvurdering v
     WHERE v.tenant = p_tenant AND v.funn_id = p_funn_id
     ORDER BY v.vurdert DESC, v.vurdering_id DESC;
END $$;
REVOKE ALL ON FUNCTION m55_vurderingene(TEXT, UUID) FROM PUBLIC;


-- BEVARINGSKOPIENE. `brukt_i_funn` er med fordi en kopi ingen har
-- festet til et funn er bevis vi betaler for å lagre uten å bruke.
CREATE FUNCTION m55_bevaringskopiene(p_tenant TEXT, p_grense INT)
RETURNS TABLE (kopi_id UUID, kilde_url TEXT, hentet_ts TIMESTAMPTZ,
               innhold_sha256 TEXT, innhold_bytes BIGINT,
               medietype TEXT, lagringsnokkel TEXT,
               registrert TIMESTAMPTZ, registrert_av TEXT,
               brukt_i_funn BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm55_bevaringskopiene');
    IF p_grense IS NULL OR p_grense < 1 OR p_grense > 500 THEN
        RAISE EXCEPTION 'm55_bevaringskopiene: grensen må være'
            ' 1..500 (%)', p_grense
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    SELECT b.kopi_id, b.kilde_url, b.hentet_ts, b.innhold_sha256,
           b.innhold_bytes, b.medietype, b.lagringsnokkel,
           b.registrert, b.registrert_av, coalesce(f.antall, 0)
      FROM public.bevaringskopi b
      LEFT JOIN LATERAL (
           SELECT count(*) AS antall FROM public.merkevarefunn ff
            WHERE ff.tenant = b.tenant
              AND ff.kopi_id = b.kopi_id) f ON true
     WHERE b.tenant = p_tenant
     ORDER BY b.hentet_ts DESC
     LIMIT p_grense;
END $$;
REVOKE ALL ON FUNCTION m55_bevaringskopiene(TEXT, INT) FROM PUBLIC;


CREATE FUNCTION m55_varslene(p_tenant TEXT, p_bare_apne BOOLEAN)
RETURNS TABLE (varsel_id UUID, merkevare_id UUID, merkenavn TEXT,
               funn_id UUID, observert_navn TEXT, varseltype TEXT,
               over_grense INT, detalj TEXT, likhet INT,
               terskel_brukt INT, kravversjon INT,
               forst_sett TIMESTAMPTZ, sist_sett_sveip TIMESTAMPTZ,
               apen BOOLEAN, lukket_ts TIMESTAMPTZ, lukket_av TEXT,
               lukkenotat TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm55_varslene');
    RETURN QUERY
    SELECT w.varsel_id, w.merkevare_id, m.navn, w.funn_id,
           f.observert_navn, w.varseltype, w.over_grense, w.detalj,
           w.likhet, w.terskel_brukt, w.kravversjon, w.forst_sett,
           w.sist_sett_sveip, w.apen, w.lukket_ts, w.lukket_av,
           w.lukkenotat
      FROM public.merkevarevarsel w
      JOIN public.merkevare m
        ON m.tenant = w.tenant AND m.merkevare_id = w.merkevare_id
      LEFT JOIN public.merkevarefunn f
        ON f.tenant = w.tenant AND f.funn_id = w.funn_id
     WHERE w.tenant = p_tenant
       AND (NOT coalesce(p_bare_apne, true) OR w.apen)
     ORDER BY w.apen DESC, w.likhet DESC NULLS LAST, w.forst_sett;
END $$;
REVOKE ALL ON FUNCTION m55_varslene(TEXT, BOOLEAN) FROM PUBLIC;


-- SAMMENDRAGET. Tallene flaten åpner med.
CREATE FUNCTION m55_merkevarestatus(p_tenant TEXT)
RETURNS TABLE (merker BIGINT, aktive BIGINT, funn BIGINT,
               apne_funn BIGINT, uvurderte BIGINT,
               over_terskel BIGINT, uhenviste BIGINT,
               henviste BIGINT, bevaringskopier BIGINT,
               ubrukte_kopier BIGINT, apne_varsler BIGINT,
               har_krav BOOLEAN, terskel INT, kravversjon INT,
               vist BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm55_merkevarestatus');
    RETURN QUERY
    WITH nyeste AS (
        -- NYESTE VURDERING PER FUNN, ÉN GANG. Vurderingene er
        -- versjonerte og append-only: et funn kan ha flere, og en
        -- telling som tok alle ville sagt at det finnes flere saker
        -- enn det gjør (119s lærdom, målt).
        SELECT f.funn_id, f.lukket_ts, f.henvist_unntak_id,
               s.likhet, s.over_terskel
          FROM public.merkevarefunn f
          LEFT JOIN LATERAL (
               SELECT vv.likhet, vv.over_terskel
                 FROM public.forvekslingsvurdering vv
                WHERE vv.tenant = f.tenant AND vv.funn_id = f.funn_id
                ORDER BY vv.vurdert DESC, vv.vurdering_id DESC
                LIMIT 1) s ON true
         WHERE f.tenant = p_tenant)
    SELECT (SELECT count(*) FROM public.merkevare m
             WHERE m.tenant = p_tenant),
           (SELECT count(*) FROM public.merkevare m
             WHERE m.tenant = p_tenant AND m.aktiv),
           (SELECT count(*) FROM nyeste),
           (SELECT count(*) FROM nyeste n
             WHERE n.lukket_ts IS NULL),
           (SELECT count(*) FROM nyeste n
             WHERE n.lukket_ts IS NULL AND n.likhet IS NULL),
           (SELECT count(*) FROM nyeste n
             WHERE n.lukket_ts IS NULL AND n.over_terskel),
           -- DET TALLET SOM BETYR NOE: hvor mange forvekslinger som
           -- venter på et menneske.
           (SELECT count(*) FROM nyeste n
             WHERE n.lukket_ts IS NULL AND n.over_terskel
               AND n.henvist_unntak_id IS NULL),
           (SELECT count(*) FROM nyeste n
             WHERE n.henvist_unntak_id IS NOT NULL),
           (SELECT count(*) FROM public.bevaringskopi b
             WHERE b.tenant = p_tenant),
           (SELECT count(*) FROM public.bevaringskopi b
             WHERE b.tenant = p_tenant
               AND NOT EXISTS (SELECT 1 FROM public.merkevarefunn ff
                                WHERE ff.tenant = b.tenant
                                  AND ff.kopi_id = b.kopi_id)),
           (SELECT count(*) FROM public.merkevarevarsel w
             WHERE w.tenant = p_tenant AND w.apen),
           EXISTS (SELECT 1 FROM public.merkevarekrav k
                    WHERE k.tenant = p_tenant),
           (SELECT k.forvekslingsterskel FROM public.merkevarekrav k
             WHERE k.tenant = p_tenant),
           (SELECT k.versjon FROM public.merkevarekrav k
             WHERE k.tenant = p_tenant),
           (SELECT least(count(*), 200) FROM public.merkevare m
             WHERE m.tenant = p_tenant);
END $$;
REVOKE ALL ON FUNCTION m55_merkevarestatus(TEXT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 5. Sveipen. Kryss-tenant, egen LOGIN-rolle, egen timer.
-- ------------------------------------------------------------

-- NATTENS ENESTE JOBB: SE ETTER DET INGEN SÅ PÅ.
--
-- SVEIPEN VURDERER IKKE, OG HENVISER IKKE. Den leser hva som står, og
-- melder tilstander et menneske må ta stilling til. En sveip som
-- vurderte selv ville gjort `m55_vurder_funn` til noe som skjer om
-- natten uten at noen ba om det — og en vurdering er inngangen til en
-- anklage mot en navngitt part.
--
-- TENANTLISTA MATERIALISERES FØR LØKKA (112s lærdom, 116–119): en
-- `FOR t IN SELECT`-markør er lat, og `set_config` inne i løkken
-- endrer RLS-konteksten markøren fremdeles leser gjennom.
CREATE FUNCTION m55_sveip_merkevare(p_maks_tenanter INT)
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
        RAISE EXCEPTION 'm55_sveip_merkevare: maks_tenanter må være'
            ' minst 1 (%)', p_maks_tenanter
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.tenant', '', true);
    SELECT array_agg(DISTINCT m.tenant ORDER BY m.tenant)
      INTO v_tenanter FROM public.merkevare m;
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

        -- ------------------------------------------------------
        -- MERKENIVÅ: `ingen_terskler` og `merkevare_uten_funn`.
        -- ------------------------------------------------------
        WITH krav AS (
            SELECT k.forvekslingsterskel, k.versjon
              FROM public.merkevarekrav k WHERE k.tenant = v_t),
        kand AS (
            -- UTEN TERSKEL BLIR INGENTING VURDERT. Varselet står på
            -- hvert AKTIVT merke, fordi det er merket som ikke blir
            -- overvåket — ikke tenanten i sin alminnelighet.
            SELECT m.merkevare_id, 'ingen_terskler'::text AS varseltype,
                   NULL::int AS over_grense,
                   'forvekslingsterskelen er tenantens og er ikke'
                   || ' satt'::text AS detalj,
                   NULL::int AS likhet, NULL::int AS terskel,
                   NULL::int AS kravversjon
              FROM public.merkevare m
             WHERE m.tenant = v_t AND m.aktiv
               AND NOT EXISTS (SELECT 1 FROM krav)

            UNION ALL
            -- ET AKTIVT MERKE INGEN HAR REGISTRERT ETT ENESTE FUNN
            -- PÅ. Ikke nødvendigvis galt — men modulen overvåker
            -- ikke selv, og et merke uten funn er et merke ingen har
            -- sett etter. Det skal stå på skjermen, ikke antas.
            SELECT m.merkevare_id, 'merkevare_uten_funn', NULL::int,
                   NULL::text, NULL::int, k.forvekslingsterskel,
                   k.versjon
              FROM public.merkevare m CROSS JOIN krav k
             WHERE m.tenant = v_t AND m.aktiv
               AND NOT EXISTS (SELECT 1 FROM public.merkevarefunn f
                                WHERE f.tenant = v_t
                                  AND f.merkevare_id = m.merkevare_id)
        ),
        skrevet AS (
            INSERT INTO public.merkevarevarsel
                (tenant, varsel_id, merkevare_id, funn_id, varseltype,
                 over_grense, detalj, likhet, terskel_brukt,
                 kravversjon)
            SELECT v_t, gen_random_uuid(), k.merkevare_id, NULL,
                   k.varseltype, k.over_grense, k.detalj, k.likhet,
                   k.terskel, k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, merkevare_id, varseltype)
                WHERE funn_id IS NULL
            DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                detalj = EXCLUDED.detalj,
                likhet = EXCLUDED.likhet,
                terskel_brukt = EXCLUDED.terskel_brukt,
                kravversjon = EXCLUDED.kravversjon,
                sist_sett_sveip = now(),
                apen = true, lukket_ts = NULL, lukket_av = NULL,
                lukkenotat = NULL
            RETURNING (xmax = 0) AS var_ny)
        SELECT count(*) FILTER (WHERE var_ny),
               count(*) FILTER (WHERE NOT var_ny)
          INTO v_n, v_n2
          FROM skrevet;
        -- `INTO` SETTER variabelen; akkumuleringen står her (112s
        -- retting, gjentatt i 116–119).
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);

        -- ------------------------------------------------------
        -- FUNNIVÅ.
        -- ------------------------------------------------------
        WITH krav AS (
            SELECT k.forvekslingsterskel, k.funnfrist_dogn,
                   k.henvisningsfrist_dogn, k.versjon
              FROM public.merkevarekrav k WHERE k.tenant = v_t),
        siste AS (
            -- NYESTE VURDERING PER FUNN. Vurderingene er append-only
            -- og versjonerte; en telling som tok alle ville sagt at
            -- det finnes flere saker enn det gjør.
            SELECT f.funn_id, f.merkevare_id, f.registrert,
                   f.henvist_unntak_id, s.likhet, s.terskel_brukt,
                   s.over_terskel, s.kravversjon AS vurdert_kravversjon
              FROM public.merkevarefunn f
              LEFT JOIN LATERAL (
                   SELECT vv.likhet, vv.terskel_brukt,
                          vv.over_terskel, vv.kravversjon
                     FROM public.forvekslingsvurdering vv
                    WHERE vv.tenant = f.tenant
                      AND vv.funn_id = f.funn_id
                    ORDER BY vv.vurdert DESC, vv.vurdering_id DESC
                    LIMIT 1) s ON true
             WHERE f.tenant = v_t AND f.lukket_ts IS NULL),
        kand AS (
            -- ET FUNN INGEN HAR VURDERT. Bevis vi har tatt vare på
            -- uten å ta stilling til.
            SELECT s.merkevare_id, s.funn_id,
                   'funn_uten_vurdering'::text AS varseltype,
                   (current_date - s.registrert::date) AS over_grense,
                   NULL::text AS detalj, NULL::int AS likhet,
                   k.forvekslingsterskel AS terskel, k.versjon
              FROM siste s CROSS JOIN krav k
             WHERE s.likhet IS NULL

            UNION ALL
            -- FORVEKSLING OVER TENANTENS EGEN TERSKEL, UHENVIST.
            -- MODULENS VIKTIGSTE VARSEL, og det ene som ikke kan
            -- lukkes: `m55_lukk_varsel` nekter, og varselet
            -- forsvinner når funnet HENVISES.
            SELECT s.merkevare_id, s.funn_id,
                   'forveksling_ikke_henvist',
                   (current_date - s.registrert::date),
                   NULL::text, s.likhet, s.terskel_brukt, k.versjon
              FROM siste s CROSS JOIN krav k
             WHERE s.over_terskel
               AND s.henvist_unntak_id IS NULL

            UNION ALL
            -- VURDERT UNDER EN ANNEN TERSKEL ENN DEN SOM GJELDER NÅ.
            -- Dommen ble tatt under en annen regel, og ingen har
            -- regnet den om. Varselet er en OPPFORDRING til å vurdere
            -- på nytt — sveipen gjør det ikke selv.
            SELECT s.merkevare_id, s.funn_id,
                   'vurdering_med_utdatert_terskel', NULL::int,
                   NULL::text, s.likhet, k.forvekslingsterskel,
                   k.versjon
              FROM siste s CROSS JOIN krav k
             WHERE s.likhet IS NOT NULL
               AND s.terskel_brukt <> k.forvekslingsterskel

            UNION ALL
            -- ET ÅPENT FUNN SOM HAR BLITT GAMMELT. Bevis foreldes, og
            -- en sak som blir eldre uten å bli bedre er en sak som
            -- forsvinner av seg selv.
            SELECT s.merkevare_id, s.funn_id, 'funn_eldre_enn_frist',
                   (current_date - s.registrert::date), NULL::text,
                   s.likhet, k.forvekslingsterskel, k.versjon
              FROM siste s CROSS JOIN krav k
             WHERE s.registrert
                   < now() - make_interval(days => k.funnfrist_dogn)
        ),
        skrevet AS (
            INSERT INTO public.merkevarevarsel
                (tenant, varsel_id, merkevare_id, funn_id, varseltype,
                 over_grense, detalj, likhet, terskel_brukt,
                 kravversjon)
            SELECT v_t, gen_random_uuid(), k.merkevare_id, k.funn_id,
                   k.varseltype, k.over_grense, k.detalj, k.likhet,
                   k.terskel, k.versjon
              FROM kand k
            ON CONFLICT (tenant, funn_id, varseltype)
                WHERE funn_id IS NOT NULL
            DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                detalj = EXCLUDED.detalj,
                likhet = EXCLUDED.likhet,
                terskel_brukt = EXCLUDED.terskel_brukt,
                kravversjon = EXCLUDED.kravversjon,
                sist_sett_sveip = now(),
                apen = true, lukket_ts = NULL, lukket_av = NULL,
                lukkenotat = NULL
            RETURNING (xmax = 0) AS var_ny)
        SELECT count(*) FILTER (WHERE var_ny),
               count(*) FILTER (WHERE NOT var_ny)
          INTO v_n, v_n2
          FROM skrevet;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);

        PERFORM set_config('disponit.tenant', '', true);
    END LOOP;

    -- LUKKINGEN I EGEN RUNDE (117–119s form). Et varsel som ikke
    -- lenger er sant, skal ikke bli stående — men det lukkes av at
    -- TILSTANDEN er borte, ikke av at noen trykket.
    --
    -- `forveksling_ikke_henvist` LUKKES OGSÅ HER, og bare her: når
    -- funnet er henvist eller lukket, er tilstanden borte. Døra
    -- `m55_lukk_varsel` nekter fremdeles — forskjellen er hele
    -- poenget. Sveipen lukker det som ER løst; et menneske kan ikke
    -- lukke det som ikke er det.
    FOREACH v_t IN ARRAY v_tenanter
    LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        WITH krav AS (
            SELECT k.forvekslingsterskel, k.funnfrist_dogn,
                   k.versjon
              FROM public.merkevarekrav k WHERE k.tenant = v_t),
        siste AS (
            SELECT f.funn_id, f.merkevare_id, f.registrert,
                   f.henvist_unntak_id, s.likhet, s.terskel_brukt,
                   s.over_terskel
              FROM public.merkevarefunn f
              LEFT JOIN LATERAL (
                   SELECT vv.likhet, vv.terskel_brukt,
                          vv.over_terskel
                     FROM public.forvekslingsvurdering vv
                    WHERE vv.tenant = f.tenant
                      AND vv.funn_id = f.funn_id
                    ORDER BY vv.vurdert DESC, vv.vurdering_id DESC
                    LIMIT 1) s ON true
             WHERE f.tenant = v_t AND f.lukket_ts IS NULL),
        fortsatt AS (
            SELECT s.merkevare_id, s.funn_id,
                   'funn_uten_vurdering'::text AS varseltype
              FROM siste s WHERE s.likhet IS NULL
            UNION ALL
            SELECT s.merkevare_id, s.funn_id,
                   'forveksling_ikke_henvist'
              FROM siste s
             WHERE s.over_terskel AND s.henvist_unntak_id IS NULL
            UNION ALL
            SELECT s.merkevare_id, s.funn_id,
                   'vurdering_med_utdatert_terskel'
              FROM siste s CROSS JOIN krav k
             WHERE s.likhet IS NOT NULL
               AND s.terskel_brukt <> k.forvekslingsterskel
            UNION ALL
            SELECT s.merkevare_id, s.funn_id, 'funn_eldre_enn_frist'
              FROM siste s CROSS JOIN krav k
             WHERE s.registrert
                   < now() - make_interval(days => k.funnfrist_dogn)
            UNION ALL
            SELECT m.merkevare_id, NULL::uuid, 'ingen_terskler'
              FROM public.merkevare m
             WHERE m.tenant = v_t AND m.aktiv
               AND NOT EXISTS (SELECT 1 FROM krav)
            UNION ALL
            SELECT m.merkevare_id, NULL::uuid, 'merkevare_uten_funn'
              FROM public.merkevare m
             WHERE m.tenant = v_t AND m.aktiv
               AND NOT EXISTS (SELECT 1 FROM public.merkevarefunn f
                                WHERE f.tenant = v_t
                                  AND f.merkevare_id = m.merkevare_id)
        )
        UPDATE public.merkevarevarsel w
           SET apen = false, lukket_ts = now(),
               lukket_av = 'm55_sveip_merkevare',
               lukkenotat = 'tilstanden er ikke lenger til stede'
         WHERE w.tenant = v_t AND w.apen
           AND NOT EXISTS (
               SELECT 1 FROM fortsatt f
                WHERE f.varseltype = w.varseltype
                  AND f.merkevare_id = w.merkevare_id
                  AND f.funn_id IS NOT DISTINCT FROM w.funn_id);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        v_lukket := v_lukket + coalesce(v_n, 0);

        PERFORM set_config('disponit.tenant', '', true);
    END LOOP;

    RETURN QUERY SELECT v_antall, v_nye, v_oppdaterte, v_lukket;
END $$;
REVOKE ALL ON FUNCTION m55_sveip_merkevare(INT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 6. RLS, radvakter og rettigheter.
-- ------------------------------------------------------------

RESET ROLE;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['merkevarekrav', 'merkevare',
                             'bevaringskopi', 'merkevarefunn',
                             'forvekslingsvurdering',
                             'merkevarevarsel']
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
                       ' disponit_merkevare_eier', t);
    END LOOP;
END $$;

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER (111s form, 112–119):
-- bare på MERKETABELLEN, bare FOR SELECT, bare til eieren, og bare når
-- ingen tenantkontekst står. Sveipen trenger tenantlista og
-- ingenting mer; alt annet leser den inne i hver tenants kontekst.
CREATE POLICY m55_sveip_tenantliste ON merkevare
    FOR SELECT TO disponit_merkevare_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- HISTORIKKTABELLENE FÅR IKKE UPDATE, HELLER IKKE FOR EIEREN.
--
-- `bevaringskopi` OG `forvekslingsvurdering` ER HELT LUKKET: et bevis
-- og en vurdering kan bare oppstå, aldri endres. Det er ikke en
-- radvakt som kan gjøre en feil — det er en rettighet som ikke finnes.
REVOKE UPDATE ON public.bevaringskopi FROM disponit_merkevare_eier;
REVOKE UPDATE ON public.forvekslingsvurdering
    FROM disponit_merkevare_eier;

-- `merkevare` FÅR BARE ENDRE `aktiv`. Navnet en vurdering ble gjort
-- mot kan ikke redigeres i ettertid; kolonnegranten gjør det til en
-- rettighet i stedet for en regel en vakt må huske (119s form).
REVOKE UPDATE ON public.merkevare FROM disponit_merkevare_eier;
GRANT UPDATE (aktiv) ON public.merkevare TO disponit_merkevare_eier;

-- `merkevarefunn` BEHOLDER UPDATE — henvisningen og lukkingen må
-- kunne settes. Åpningen lukkes fra den andre siden, av radvakten
-- under: alt annet enn de to er frosset, og de to kan bare gå fra
-- tomt til satt.
CREATE FUNCTION m55_funn_frosset()
RETURNS TRIGGER LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    -- BEVISDELEN AV RADEN ER FROSSET. `merkevarefunn_overskrevet` er
    -- denne listen: et endret bevis er ikke et svakere bevis, det er
    -- et bevis som ikke lenger beviser noe.
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.funn_id IS DISTINCT FROM OLD.funn_id
       OR NEW.merkevare_id IS DISTINCT FROM OLD.merkevare_id
       OR NEW.kopi_id IS DISTINCT FROM OLD.kopi_id
       OR NEW.observert_navn IS DISTINCT FROM OLD.observert_navn
       OR NEW.observert_normalisert
          IS DISTINCT FROM OLD.observert_normalisert
       OR NEW.bruksform IS DISTINCT FROM OLD.bruksform
       OR NEW.kontekst IS DISTINCT FROM OLD.kontekst
       OR NEW.motpart IS DISTINCT FROM OLD.motpart
       OR NEW.registrert IS DISTINCT FROM OLD.registrert
       OR NEW.registrert_av IS DISTINCT FROM OLD.registrert_av THEN
        RAISE EXCEPTION 'merkevarefunn: funnets bevisdel er frosset —'
            ' bare henvisning og lukking kan settes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- HENVISNINGEN KAN SETTES ÉN GANG. En ny henvisning ville skjult
    -- den første, og hvem som sendte hva til unntakskøen når er hele
    -- sporet ut av modulen.
    IF OLD.henvist_unntak_id IS NOT NULL
       AND (NEW.henvist_unntak_id IS DISTINCT FROM OLD.henvist_unntak_id
            OR NEW.henvist_ts IS DISTINCT FROM OLD.henvist_ts
            OR NEW.henvist_av IS DISTINCT FROM OLD.henvist_av) THEN
        RAISE EXCEPTION 'merkevarefunn: henvisningen er satt og kan'
            ' ikke endres' USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- OG LUKKINGEN ÉN GANG.
    IF OLD.lukket_ts IS NOT NULL THEN
        RAISE EXCEPTION 'merkevarefunn: funnet er lukket og er'
            ' frosset' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER merkevarefunn_frosset
    BEFORE UPDATE ON merkevarefunn
    FOR EACH ROW EXECUTE FUNCTION m55_funn_frosset();

-- SLETTING ER ALDRI LOVLIG. Et bevis som kan slettes er ikke et bevis.
REVOKE DELETE ON public.merkevarekrav FROM disponit_merkevare_eier;
REVOKE DELETE ON public.merkevare FROM disponit_merkevare_eier;
REVOKE DELETE ON public.bevaringskopi FROM disponit_merkevare_eier;
REVOKE DELETE ON public.merkevarefunn FROM disponit_merkevare_eier;
REVOKE DELETE ON public.forvekslingsvurdering
    FROM disponit_merkevare_eier;
REVOKE DELETE ON public.merkevarevarsel FROM disponit_merkevare_eier;

-- KJØRETIDSROLLEN FÅR DØRENE, ALDRI TABELLENE.
--
-- GRANTENE GIS AV EIEREN. Dørene eies av `disponit_merkevare_eier`,
-- og en GRANT fra migratoren på en funksjon den ikke eier er ikke en
-- no-op — den er en feil (116s lærdom, i den mildere formen).
--
-- Vaktet i tillegg: `REVOKE ... FROM <rolle som ikke finnes>` er en
-- FEIL i PostgreSQL, ikke en no-op (målt i 117), og `disponit` er
-- valgfri i en frisk base.
SET LOCAL ROLE disponit_merkevare_eier;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m55_merkevarestatus(TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m55_kravene(TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m55_merkene(TEXT, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m55_funnene(TEXT, UUID, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m55_vurderingene(TEXT, UUID) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m55_bevaringskopiene(TEXT, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m55_varslene(TEXT, BOOLEAN) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m55_sett_krav(TEXT, INT, INT, INT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m55_registrer_merkevare(TEXT, UUID, TEXT, TEXT, TEXT,'
            ' TEXT, TEXT[], DATE, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m55_sett_merkevare_aktiv(TEXT, UUID, BOOLEAN, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m55_registrer_bevaringskopi(TEXT, UUID, TEXT,'
            ' TIMESTAMPTZ, TEXT, BIGINT, TEXT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m55_registrer_funn(TEXT, UUID, UUID, UUID, TEXT, TEXT,'
            ' TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m55_vurder_funn(TEXT, UUID, UUID, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m55_henvis_funn(TEXT, UUID, UUID, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m55_lukk_funn(TEXT, UUID, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m55_lukk_varsel(TEXT, UUID, TEXT, TEXT) TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_merkevaresveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m55_sveip_merkevare(INT)'
            ' TO disponit_merkevaresveip';
    END IF;
END $$;

-- SVEIPEN ER IKKE KJØRETIDSROLLENS.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'REVOKE EXECUTE ON FUNCTION m55_sveip_merkevare(INT)'
            ' FROM disponit';
    END IF;
END $$;

RESET ROLE;
