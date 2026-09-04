-- 118: M-46 anbuds- og konkurransevakt v1 — TREFFENE OG UTKASTET,
-- IKKE INNSENDINGEN.
-- Sju tenant-skopede tabeller, seksten dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- V1-DOMMEN: MODULEN SENDER INGEN TILBUD.
--
-- Spesifikasjonens vakt sier det rett ut, og grunnen er skarpere enn
-- de andre fire i klyngen: et innsendt tilbud er BINDENDE, og fristen
-- gjør det irreversibelt på den måten som betyr noe — man kan ikke
-- trekke det og sende et bedre etterpå. De andre modulenes farligste
-- handlinger kan i det minste rettes opp dagen etter.
--
-- DEN ANDRE DOMMEN ER DEN SOM FORMER DATAMODELLEN, og den kommer
-- ordrett fra vakten: «utkast markerer hvert faktapunkt med kilde;
-- udekkede krav blir unntak, ALDRI UTFYLT GJETNING».
--
-- DET ER IKKE EN SJEKK. DET ER FRAVÆRET AV EN KOLONNE.
--
-- `utkastpunkt` har ingen fritekstkolonne som kan bære en påstand.
-- Hvert punkt PEKER på et `kildedokument` gjennom en NOT NULL
-- fremmednøkkel, og teksten som står der er et SITAT med en
-- sidereferanse. Det finnes altså ingen måte å skrive «vi har ISO
-- 9001» på uten å peke på dokumentet som viser det.
--
-- Hadde vi hatt en `pastand TEXT`-kolonne ved siden av, ville
-- invarianten `utkastpunkt_uten_kilde` vært en regel noen måtte huske
-- å håndheve. Nå er den formen på tabellen.
--
-- OG DET UDEKKEDE KRAVET BLIR ET FUNN, ikke en tom rad. Et
-- `kvalifikasjonskrav` uten et `utkastpunkt` som dekker det er
-- `udekket_krav` — sveipen finner det, og utkastet kan ikke merkes
-- ferdig så lenge det står. Alternativet — en rad med tom tekst — ville
-- sett ut som et besvart krav i enhver telling.
--
-- v1 HENTER INGENTING FRA DOFFIN ELLER TED. M-48 fikk klyngens ene
-- unntak fra «ingen utgående forespørsel» (eierbeslutning 3/9), og
-- grunnen der var at et organisasjonsnummer er offentlige foretaksdata
-- og at oppslaget er nødvendig. Her er det annerledes: anbudsportalene
-- er ikke ett oppslag, de er et ABONNEMENT — en søkeprofil som kjører
-- kontinuerlig og henter alt som matcher. Doktrinen om den unødvendige
-- forespørselen gjelder med full tyngde, og vi vet ennå ikke hvilke
-- søk som er nødvendige. Registeret er den målingen.
--
-- DOMMENE v1 HVILER PÅ, HÅNDHEVET I DATAMODELLEN:
--
--   1. HISTORIKKEN OVERSKRIVES ALDRI. Anbud, krav, kildedokumenter,
--      utkast og punkter er append-only. M-42s dom (110), gjentatt i
--      112–117.
--
--   2. HVERT FAKTAPUNKT PEKER PÅ ET KILDEDOKUMENT. NOT NULL
--      fremmednøkkel, ingen fritekst ved siden av.
--
--   3. ET UDEKKET KRAV ER ET FUNN, ALDRI EN UTFYLT GJETNING.
--
--   4. INGEN INNSENDING. Det finnes ingen kolonne for «sendt», ingen
--      dør som sender, og ingen utgående kanal i koden. Utkastet kan
--      merkes KLART TIL GJENNOMGANG — som er en tilstand hos oss, ikke
--      en handling utad.
--
--   5. SØKEPROFILEN ER TENANTENS. NACE, geografi og verdigrenser er
--      forretningsvalg, ikke konstanter.
--
--   6. BELØP I ØRE, HELTALL. BIGINT, ingen unntak (101s form).
--
-- GRENSEN MOT M-5: M-5 eier dokumentmalene. M-46 eier hvilke
-- DOKUMENTER som kan brukes som kilde i et anbudssvar, og at hvert
-- punkt peker på ett av dem. v1 kobler dem ikke.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_anbud_eier') THEN
        RAISE EXCEPTION 'rollen disponit_anbud_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_anbud_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `anbudsprofil` — ÉN per tenant. DOM 5: SØKEPROFILEN ER TENANTENS.
CREATE TABLE anbudsprofil (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- NACE-KODENE tenanten konkurrerer innenfor. TOM LISTE ER
    -- FORBUDT: en profil uten næringskoder ville gjort hvert anbud
    -- irrelevant og dermed skjult alle — det ser ut som en presis
    -- profil og er en konfigurasjonsfeil (112s dom).
    nace_koder TEXT[] NOT NULL CHECK (cardinality(nace_koder) > 0),
    -- Geografi som fri tekst i et array: fylker, kommuner, land. Ikke
    -- et lukket sett — inndelingen er kundens, ikke vår.
    geografi TEXT[] NOT NULL CHECK (cardinality(geografi) > 0),
    -- VERDIGRENSENE, i øre (dom 6). Et anbud under gulvet er ikke verdt
    -- tiden; et over taket er utenfor kapasiteten. Begge er
    -- forretningsvalg.
    min_verdi_ore BIGINT NOT NULL DEFAULT 0
        CHECK (min_verdi_ore >= 0),
    maks_verdi_ore BIGINT NOT NULL DEFAULT 100000000000
        CHECK (maks_verdi_ore >= 0),
    -- Hvor mange døgn før fristen et anbud uten ferdig utkast blir et
    -- funn. DETTE ER MODULENS MEST BETENTE TALL: en frist som passerer
    -- er den ene feilen som ikke kan rettes dagen etter.
    frist_varsel_dogn INT NOT NULL DEFAULT 14
        CHECK (frist_varsel_dogn BETWEEN 1 AND 365),
    -- Hvor lenge et kildedokument regnes som gyldig etter at det er
    -- registrert, når dokumentet ikke selv oppgir en utløpsdato.
    kilde_gyldig_dogn INT NOT NULL DEFAULT 365
        CHECK (kilde_gyldig_dogn BETWEEN 1 AND 3650),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT anbudsprofil_pk PRIMARY KEY (tenant),
    -- Et tak under gulvet ville gjort hvert anbud usynlig.
    CONSTRAINT anbudsprofil_grenser_stemmer CHECK (
        maks_verdi_ore >= min_verdi_ore)
);

-- `anbud` — konkurransen selv. Identitetsraden, og den ENESTE
-- muterbare tabellen i modulen (`aktiv`).
--
-- DEN ER OGSÅ LÅSERADEN (M-42s lærdom, 110): `SELECT ... FOR UPDATE`
-- krever UPDATE-rett, og de frosne tabellene har den ikke.
--
-- REGISTRERES MANUELT. v1 henter ingenting fra Doffin eller TED — se
-- dommen i toppen. Referansen står som fri tekst med formkrav, ikke
-- som et validert Doffin-id: en validering ville vært en påstand om et
-- system vi ikke snakker med.
CREATE TABLE anbud (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    anbud_id UUID NOT NULL,
    -- Portalens egen referanse, slik den ble lest av et menneske.
    ekstern_ref TEXT NOT NULL CHECK (ekstern_ref ~ '[^[:space:]]'),
    kilde TEXT NOT NULL
        CONSTRAINT anbud_kilde_lukket CHECK (kilde IN (
            'doffin', 'ted', 'direkte', 'annen')),
    tittel TEXT NOT NULL CHECK (tittel ~ '[^[:space:]]'),
    oppdragsgiver TEXT NOT NULL CHECK (oppdragsgiver ~ '[^[:space:]]'),
    nace_kode TEXT NOT NULL CHECK (nace_kode ~ '^[0-9]{2}(\.[0-9]{1,3})?$'),
    geografi TEXT NOT NULL CHECK (geografi ~ '[^[:space:]]'),
    -- Anslått verdi i ØRE. NULL når portalen ikke oppgir den — og NULL
    -- er et ærlig svar, ikke 0. Et anbud uten oppgitt verdi er ikke et
    -- gratisanbud.
    verdi_ore BIGINT CHECK (verdi_ore IS NULL OR verdi_ore >= 0),
    -- FRISTEN. Modulens viktigste kolonne: den ene datoen som ikke kan
    -- flyttes, og den ene feilen som ikke kan rettes dagen etter.
    frist TIMESTAMPTZ NOT NULL,
    aktiv BOOLEAN NOT NULL DEFAULT true,
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT anbud_pk PRIMARY KEY (tenant, anbud_id),
    CONSTRAINT anbud_ref_unik UNIQUE (tenant, kilde, ekstern_ref)
);
CREATE INDEX anbud_aktive_frist
    ON anbud (tenant, frist) WHERE aktiv;

-- `kvalifikasjonskrav` — det anbudet KREVER av oss.
--
-- FROSSET. Kravene er lest ut av konkurransegrunnlaget av et menneske,
-- og en rad som kunne endres i ettertid ville gjort «hva sto det
-- egentlig i grunnlaget» til et åpent spørsmål — nettopp spørsmålet en
-- klage på tildelingen handler om.
CREATE TABLE kvalifikasjonskrav (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    krav_id UUID NOT NULL,
    anbud_id UUID NOT NULL,
    -- Grunnlagets egen nummerering, slik den står. Fri tekst: hvert
    -- oppdragsgiver nummererer på sin måte.
    kravnummer TEXT NOT NULL CHECK (kravnummer ~ '[^[:space:]]'),
    kravtekst TEXT NOT NULL CHECK (length(btrim(kravtekst)) >= 4),
    kravtype TEXT NOT NULL
        CONSTRAINT kvalifikasjonskrav_type_lukket CHECK (kravtype IN (
            'kvalifikasjon', 'dokumentasjon', 'erfaring',
            'sertifisering', 'okonomi', 'annet')),
    -- ET ABSOLUTT KRAV STENGER KONKURRANSEN om det ikke er dekket. Et
    -- vektet kan gi trekk. Forskjellen avgjør om et udekket krav er en
    -- ulempe eller en avvisning, og den må stå på raden.
    absolutt BOOLEAN NOT NULL DEFAULT true,
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT kvalifikasjonskrav_pk PRIMARY KEY (tenant, krav_id),
    CONSTRAINT kvalifikasjonskrav_anbud_fk
        FOREIGN KEY (tenant, anbud_id)
        REFERENCES anbud (tenant, anbud_id),
    CONSTRAINT kvalifikasjonskrav_nummer_unikt
        UNIQUE (tenant, anbud_id, kravnummer)
);
CREATE INDEX kvalifikasjonskrav_anbud
    ON kvalifikasjonskrav (tenant, anbud_id, absolutt DESC);

-- `kildedokument` — DOM 2. DE GODKJENTE KILDENE.
--
-- Et utkastpunkt kan bare peke hit. Det er hele mekanismen bak
-- «utkast markerer hvert faktapunkt med kilde».
--
-- FROSSET, med innholdssum. Uten summen kan ingen etterpå vise at det
-- var NØYAKTIG denne versjonen av sertifikatet som ble sitert — og et
-- anbudssvar er et dokument man blir holdt til.
CREATE TABLE kildedokument (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kilde_id UUID NOT NULL,
    tittel TEXT NOT NULL CHECK (tittel ~ '[^[:space:]]'),
    dokumenttype TEXT NOT NULL
        CONSTRAINT kildedokument_type_lukket CHECK (dokumenttype IN (
            'sertifikat', 'attest', 'regnskap', 'referanse',
            'policy', 'cv', 'annet')),
    -- Dokumentets EGEN utløpsdato når det har en (sertifikater har
    -- det). NULL når det ikke har, og da regnes gyldigheten fra
    -- `kilde_gyldig_dogn` i profilen — tenantens tall, ikke vårt.
    gyldig_til DATE,
    innhold_sha256 TEXT NOT NULL
        CHECK (innhold_sha256 ~ '^[0-9a-f]{64}$'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT kildedokument_pk PRIMARY KEY (tenant, kilde_id),
    -- SAMME DOKUMENT REGISTRERES ÉN GANG. To rader med samme sum er
    -- to navn på det samme, og et punkt som pekte på «feil» av dem
    -- ville vært umulig å skille fra ett som pekte på «riktig».
    CONSTRAINT kildedokument_sum_unik UNIQUE (tenant, innhold_sha256)
);
CREATE INDEX kildedokument_gyldige
    ON kildedokument (tenant, gyldig_til);

-- `anbudsutkast` — svaret under arbeid.
--
-- INGEN KOLONNE FOR «SENDT». Det finnes `klar_til_gjennomgang`, som er
-- en tilstand HOS OSS: et menneske sier at utkastet er ferdig fra
-- modulens side. Hva som skjer videre — om noen laster det ned,
-- redigerer det og sender det inn i portalen — er utenfor modulen, og
-- det skal det være. Fraværet av «sendt» ER porten
-- `modulen_sendte_tilbud`.
CREATE TABLE anbudsutkast (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    utkast_id UUID NOT NULL,
    anbud_id UUID NOT NULL,
    -- Utkastversjonen. Et nytt utkast er en NY RAD, ikke en endring:
    -- «hva sto i utkastet da noen godkjente det» må kunne besvares.
    versjon INT NOT NULL CHECK (versjon >= 1),
    -- KLAR TIL GJENNOMGANG, ikke «sendt». Settes av `m46_merk_klart`,
    -- som NEKTER så lenge et absolutt krav står udekket — se dom 3.
    klar_til_gjennomgang BOOLEAN NOT NULL DEFAULT false,
    klar_ts TIMESTAMPTZ,
    klar_av TEXT CHECK (klar_av IS NULL OR klar_av ~ '[^[:space:]]'),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT anbudsutkast_pk PRIMARY KEY (tenant, utkast_id),
    CONSTRAINT anbudsutkast_anbud_fk
        FOREIGN KEY (tenant, anbud_id)
        REFERENCES anbud (tenant, anbud_id),
    CONSTRAINT anbudsutkast_versjon_unik
        UNIQUE (tenant, anbud_id, versjon),
    -- Klar, tidspunkt og aktør følges ad: et «klart» utkast uten hvem
    -- og når er en rad som later som noen tok stilling.
    CONSTRAINT anbudsutkast_klar_helhet CHECK (
        (NOT klar_til_gjennomgang AND klar_ts IS NULL
         AND klar_av IS NULL)
     OR (klar_til_gjennomgang AND klar_ts IS NOT NULL
         AND klar_av IS NOT NULL))
);
CREATE INDEX anbudsutkast_anbud
    ON anbudsutkast (tenant, anbud_id, versjon DESC);

-- `utkastpunkt` — DOM 2 OG 3. TABELLEN HELE DOMMEN HVILER PÅ.
--
-- LES KOLONNENE, OG LEGG MERKE TIL HVA SOM IKKE ER HER:
--
--   Det finnes INGEN `pastand TEXT`. Ingen fritekstkolonne der noen
--   kan skrive «vi har ISO 9001» uten å peke på dokumentet som viser
--   det. Teksten som står på raden er et SITAT, og sitatet hører til
--   `kilde_id` — en NOT NULL fremmednøkkel til `kildedokument`.
--
-- Hadde vi hatt en påstandskolonne ved siden av, ville invarianten
-- `utkastpunkt_uten_kilde` vært en regel noen måtte huske å håndheve
-- ved hver ny skrivevei. Nå er den FORMEN PÅ TABELLEN: et punkt uten
-- kilde kan ikke uttrykkes.
--
-- OG ET UDEKKET KRAV BLIR IKKE EN TOM RAD HER. Alternativet — en rad
-- med `sitat = ''` og kilde satt til et plassholderdokument — ville
-- sett ut som et besvart krav i enhver telling. Et udekket krav har
-- INGEN rad, og sveipen finner det som `udekket_krav`.
--
-- FROSSET. Et utkastpunkt er en påstand vi blir holdt til; en rad som
-- kunne rettes ville gjort «hva sto det da noen godkjente det» til et
-- åpent spørsmål.
CREATE TABLE utkastpunkt (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    punkt_id UUID NOT NULL,
    utkast_id UUID NOT NULL,
    -- HVILKET KRAV PUNKTET SVARER PÅ. NOT NULL: et punkt som ikke
    -- svarer på noe er tekst uten adressat, og det ville ikke telt som
    -- dekning av noe krav uansett.
    krav_id UUID NOT NULL,
    -- HVILKET DOKUMENT PÅSTANDEN HVILER PÅ. NOT NULL. Dette er dommen.
    kilde_id UUID NOT NULL,
    -- SITATET, med sidereferanse. Ikke en omskrivning: en modul som
    -- formulerte om ville lagt til en påstand ingen kan spore.
    sitat TEXT NOT NULL CHECK (length(btrim(sitat)) >= 4),
    sidereferanse TEXT NOT NULL CHECK (sidereferanse ~ '[^[:space:]]'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT utkastpunkt_pk PRIMARY KEY (tenant, punkt_id),
    CONSTRAINT utkastpunkt_utkast_fk
        FOREIGN KEY (tenant, utkast_id)
        REFERENCES anbudsutkast (tenant, utkast_id),
    CONSTRAINT utkastpunkt_krav_fk
        FOREIGN KEY (tenant, krav_id)
        REFERENCES kvalifikasjonskrav (tenant, krav_id),
    CONSTRAINT utkastpunkt_kilde_fk
        FOREIGN KEY (tenant, kilde_id)
        REFERENCES kildedokument (tenant, kilde_id),
    -- ÉN DEKNING PER KRAV PER UTKAST. To punkter på samme krav er ikke
    -- dobbelt så godt dekket; det er to svar der leseren må gjette
    -- hvilket som gjelder.
    CONSTRAINT utkastpunkt_krav_unikt UNIQUE (tenant, utkast_id, krav_id)
);
CREATE INDEX utkastpunkt_utkast
    ON utkastpunkt (tenant, utkast_id);
CREATE INDEX utkastpunkt_kilde
    ON utkastpunkt (tenant, kilde_id);

-- `anbudsfunn` — 112s gjenbruksform, nøklet på (tenant, anbud,
-- funntype). Funnene er per ANBUD og ikke per krav: «dette anbudet
-- har udekkede krav» er handlingen noen skal ta, og en liste med
-- ett funn per krav ville druknet fristvarselet.
CREATE TABLE anbudsfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    anbud_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CONSTRAINT anbudsfunn_type_lukket CHECK (funntype IN (
            'frist_naermer_seg',      -- innen frist_varsel_dogn
            'frist_passert',          -- fristen gikk, uten klart utkast
            'udekket_absolutt_krav',  -- absolutt krav uten punkt
            'udekket_krav',           -- vektet krav uten punkt
            'utlopt_kilde',           -- punkt peker på utløpt dokument
            'ingen_krav_registrert',  -- anbud uten kravpunkter
            'utenfor_profil',         -- NACE/verdi utenfor søkeprofilen
            'ingen_profil')),         -- tenanten har ingen søkeprofil
    -- DØGN til (positivt) eller etter (negativt) fristen for
    -- fristfunnene; ANTALL udekkede krav for kravfunnene.
    over_grense INT,
    -- Det ene faktumet som forklarer funnet (112s dom): fristen for
    -- fristfunnene, kravnummeret for det første udekkede kravet.
    detalj TEXT,
    profilversjon INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT anbudsfunn_pk PRIMARY KEY (tenant, anbud_id, funntype),
    CONSTRAINT anbudsfunn_anbud_fk FOREIGN KEY (tenant, anbud_id)
        REFERENCES anbud (tenant, anbud_id),
    CONSTRAINT anbudsfunn_lukking_helhet CHECK (
        (apen AND lukket_ts IS NULL)
     OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE INDEX anbudsfunn_apne
    ON anbudsfunn (tenant, funntype) WHERE apen;


-- ------------------------------------------------------------
-- 2. Evidenskjeden. SECURITY DEFINER, eid av `disponit_anbud_eier`,
--    SP-1.
-- ------------------------------------------------------------

-- `krev_tenantkontekst` eies av `disponit_m37_claimer` (111s form), så
-- EXECUTE må gis AV den rollen — en GRANT fra migratoren er en no-op
-- med en advarsel (116s lærdom).
GRANT INSERT ON revisjonslogg TO disponit_anbud_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_anbud_eier;
RESET ROLE;

-- HERFRA OG TIL SEKSJON 6 EIES ALT SOM LAGES AV ANBUDSEIEREN. Dørene
-- er SECURITY DEFINER, så eierskapet ER fullmakten de kjører med.
SET LOCAL ROLE disponit_anbud_eier;

CREATE FUNCTION m46_evidens(p_tenant TEXT, p_anbud_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm46_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm46_anbud', 'handling', p_handling,
        'anbud_id', p_anbud_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm46_anbud',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:anbud', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m46_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;


-- ------------------------------------------------------------
-- 3. Skrivedørene.
-- ------------------------------------------------------------

-- DOM 5: SØKEPROFILEN ER TENANTENS.
CREATE FUNCTION m46_sett_profil(
    p_tenant TEXT, p_nace_koder TEXT[], p_geografi TEXT[],
    p_min_verdi_ore BIGINT, p_maks_verdi_ore BIGINT,
    p_frist_varsel_dogn INT, p_kilde_gyldig_dogn INT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm46_sett_profil');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    INSERT INTO public.anbudsprofil
        (tenant, nace_koder, geografi, min_verdi_ore, maks_verdi_ore,
         frist_varsel_dogn, kilde_gyldig_dogn, oppdatert_av)
    VALUES (p_tenant, p_nace_koder, p_geografi, p_min_verdi_ore,
            p_maks_verdi_ore, p_frist_varsel_dogn,
            p_kilde_gyldig_dogn, p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        nace_koder = EXCLUDED.nace_koder,
        geografi = EXCLUDED.geografi,
        min_verdi_ore = EXCLUDED.min_verdi_ore,
        maks_verdi_ore = EXCLUDED.maks_verdi_ore,
        frist_varsel_dogn = EXCLUDED.frist_varsel_dogn,
        kilde_gyldig_dogn = EXCLUDED.kilde_gyldig_dogn,
        versjon = public.anbudsprofil.versjon + 1,
        oppdatert = now(),
        oppdatert_av = EXCLUDED.oppdatert_av
    RETURNING versjon INTO v_versjon;

    PERFORM public.m46_evidens(p_tenant, NULL, 'anbudsprofil_satt',
        p_aktor, jsonb_build_object('versjon', v_versjon,
                                    'nace_koder', p_nace_koder));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION m46_sett_profil(
    TEXT, TEXT[], TEXT[], BIGINT, BIGINT, INT, INT, TEXT) FROM PUBLIC;


CREATE FUNCTION m46_registrer_anbud(
    p_tenant TEXT, p_anbud_id UUID, p_ekstern_ref TEXT, p_kilde TEXT,
    p_tittel TEXT, p_oppdragsgiver TEXT, p_nace_kode TEXT,
    p_geografi TEXT, p_verdi_ore BIGINT, p_frist TIMESTAMPTZ,
    p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm46_registrer_anbud');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    -- ET ANBUD MED FRIST I FORTIDA kan registreres — det skjer når
    -- noen fører inn en konkurranse de oppdaget for seint, og det er
    -- en observasjon verdt å ha. Sveipen finner den som
    -- `frist_passert` med en gang.
    INSERT INTO public.anbud
        (tenant, anbud_id, ekstern_ref, kilde, tittel, oppdragsgiver,
         nace_kode, geografi, verdi_ore, frist, registrert_av)
    VALUES (p_tenant, p_anbud_id, btrim(p_ekstern_ref), p_kilde,
            btrim(p_tittel), btrim(p_oppdragsgiver),
            btrim(p_nace_kode), btrim(p_geografi), p_verdi_ore,
            p_frist, p_aktor);

    PERFORM public.m46_evidens(p_tenant, p_anbud_id,
        'anbud_registrert', p_aktor,
        jsonb_build_object('kilde', p_kilde,
                           'ekstern_ref', btrim(p_ekstern_ref),
                           'frist', p_frist));
END $$;
REVOKE ALL ON FUNCTION m46_registrer_anbud(
    TEXT, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BIGINT,
    TIMESTAMPTZ, TEXT) FROM PUBLIC;


CREATE FUNCTION m46_registrer_krav(
    p_tenant TEXT, p_krav_id UUID, p_anbud_id UUID,
    p_kravnummer TEXT, p_kravtekst TEXT, p_kravtype TEXT,
    p_absolutt BOOLEAN, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_aktiv BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm46_registrer_krav');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    SELECT a.aktiv INTO v_aktiv FROM public.anbud a
     WHERE a.tenant = p_tenant AND a.anbud_id = p_anbud_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm46_registrer_krav: ukjent anbud %',
            p_anbud_id USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_aktiv THEN
        RAISE EXCEPTION 'm46_registrer_krav: anbudet % er deaktivert',
            p_anbud_id USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO public.kvalifikasjonskrav
        (tenant, krav_id, anbud_id, kravnummer, kravtekst, kravtype,
         absolutt, registrert_av)
    VALUES (p_tenant, p_krav_id, p_anbud_id, btrim(p_kravnummer),
            btrim(p_kravtekst), p_kravtype, p_absolutt, p_aktor);

    PERFORM public.m46_evidens(p_tenant, p_anbud_id,
        'kvalifikasjonskrav_registrert', p_aktor,
        jsonb_build_object('krav_id', p_krav_id,
                           'kravnummer', btrim(p_kravnummer),
                           'absolutt', p_absolutt));
END $$;
REVOKE ALL ON FUNCTION m46_registrer_krav(
    TEXT, UUID, UUID, TEXT, TEXT, TEXT, BOOLEAN, TEXT) FROM PUBLIC;


CREATE FUNCTION m46_registrer_kilde(
    p_tenant TEXT, p_kilde_id UUID, p_tittel TEXT,
    p_dokumenttype TEXT, p_gyldig_til DATE, p_innhold_sha256 TEXT,
    p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm46_registrer_kilde');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    INSERT INTO public.kildedokument
        (tenant, kilde_id, tittel, dokumenttype, gyldig_til,
         innhold_sha256, registrert_av)
    VALUES (p_tenant, p_kilde_id, btrim(p_tittel), p_dokumenttype,
            p_gyldig_til, lower(btrim(p_innhold_sha256)), p_aktor);

    -- Kilden hører hele tenanten til, ikke ett anbud: NULL er det
    -- ærlige svaret på «hvilket anbud».
    PERFORM public.m46_evidens(p_tenant, NULL,
        'kildedokument_registrert', p_aktor,
        jsonb_build_object('kilde_id', p_kilde_id,
                           'dokumenttype', p_dokumenttype));
END $$;
REVOKE ALL ON FUNCTION m46_registrer_kilde(
    TEXT, UUID, TEXT, TEXT, DATE, TEXT, TEXT) FROM PUBLIC;


-- VERSJONEN REGNES HER, den sendes ikke inn. En kaller som fikk oppgi
-- den kunne gjenbrukt et nummer og skrevet over historikken —
-- «hva sto i utkast 2 da noen godkjente det» må kunne besvares.
CREATE FUNCTION m46_opprett_utkast(
    p_tenant TEXT, p_utkast_id UUID, p_anbud_id UUID, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_aktiv BOOLEAN;
    v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm46_opprett_utkast');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    SELECT a.aktiv INTO v_aktiv FROM public.anbud a
     WHERE a.tenant = p_tenant AND a.anbud_id = p_anbud_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm46_opprett_utkast: ukjent anbud %',
            p_anbud_id USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_aktiv THEN
        RAISE EXCEPTION 'm46_opprett_utkast: anbudet % er deaktivert',
            p_anbud_id USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT coalesce(max(u.versjon), 0) + 1 INTO v_versjon
      FROM public.anbudsutkast u
     WHERE u.tenant = p_tenant AND u.anbud_id = p_anbud_id;

    INSERT INTO public.anbudsutkast
        (tenant, utkast_id, anbud_id, versjon, opprettet_av)
    VALUES (p_tenant, p_utkast_id, p_anbud_id, v_versjon, p_aktor);

    PERFORM public.m46_evidens(p_tenant, p_anbud_id,
        'anbudsutkast_opprettet', p_aktor,
        jsonb_build_object('utkast_id', p_utkast_id,
                           'versjon', v_versjon));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION m46_opprett_utkast(TEXT, UUID, UUID, TEXT)
    FROM PUBLIC;


-- DOM 2. HVERT FAKTAPUNKT PEKER PÅ ET KILDEDOKUMENT.
--
-- Fremmednøkkelen gjør det umulig å skrive et punkt uten kilde;
-- denne døra legger til de to tingene basen ikke kan uttrykke i en
-- CHECK på tvers av tabeller:
--
--   * KRAVET MÅ HØRE TIL SAMME ANBUD SOM UTKASTET. Uten den sjekken
--     kunne et punkt «dekket» et krav fra en helt annen konkurranse,
--     og tellingen av udekkede krav ville sett riktig ut mens
--     utkastet var tomt der det gjaldt.
--
--   * KILDEN MÅ VÆRE GYLDIG NÅ. Et sertifikat som gikk ut i fjor er
--     ikke dokumentasjon; et punkt som pekte på det ville vært en
--     påstand med en kilde som ikke bærer den.
CREATE FUNCTION m46_registrer_punkt(
    p_tenant TEXT, p_punkt_id UUID, p_utkast_id UUID, p_krav_id UUID,
    p_kilde_id UUID, p_sitat TEXT, p_sidereferanse TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_anbud UUID;
    v_klar BOOLEAN;
    v_kravanbud UUID;
    v_gyldig_til DATE;
    v_registrert TIMESTAMPTZ;
    v_kilde_dogn INT;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm46_registrer_punkt');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    SELECT u.anbud_id, u.klar_til_gjennomgang INTO v_anbud, v_klar
      FROM public.anbudsutkast u
     WHERE u.tenant = p_tenant AND u.utkast_id = p_utkast_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm46_registrer_punkt: ukjent utkast %',
            p_utkast_id USING ERRCODE = 'no_data_found';
    END IF;
    -- Anbudet låses, ikke utkastet: utkastet er frosset bortsett fra
    -- klarmerkingen, og anbudet er den muterbare foreldreraden
    -- (M-42s lærdom, 110).
    PERFORM 1 FROM public.anbud
     WHERE tenant = p_tenant AND anbud_id = v_anbud FOR UPDATE;
    -- OG KLARMERKET LESES PÅ NYTT UNDER LÅSEN (CodeRabbit, 118).
    -- Lesningen over er fra transaksjonens snapshot. En samtidig
    -- ferdigstilling som committer mens vi venter på låsen, er ellers
    -- usynlig her — og raden ville landet i noe som alt er
    -- gjennomgått. Radvakten fanger den ikke: den utløses på UPDATE av
    -- foreldretabellen, ikke på INSERT i barnetabellen.
    --
    -- SAMME FEIL SOM I `m48_fullfor_oppslag` (116). Den ble rettet
    -- der og skrevet på nytt her; mønsteret står nå i alle fire.
    SELECT u.klar_til_gjennomgang INTO v_klar
      FROM public.anbudsutkast u
     WHERE u.tenant = p_tenant AND u.utkast_id = p_utkast_id;
    IF v_klar THEN
        RAISE EXCEPTION 'm46_registrer_punkt: utkastet % er merket'
            ' klart — et nytt punkt hører til et nytt utkast, ikke til'
            ' et som alt er gjennomgått', p_utkast_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT k.anbud_id INTO v_kravanbud
      FROM public.kvalifikasjonskrav k
     WHERE k.tenant = p_tenant AND k.krav_id = p_krav_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm46_registrer_punkt: ukjent krav %',
            p_krav_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_kravanbud <> v_anbud THEN
        RAISE EXCEPTION 'm46_registrer_punkt: kravet % hører til'
            ' anbud %, ikke %. Et punkt som «dekket» et krav fra en'
            ' annen konkurranse ville gjort tellingen av udekkede krav'
            ' riktig og utkastet tomt der det gjaldt',
            p_krav_id, v_kravanbud, v_anbud
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT d.gyldig_til, d.registrert INTO v_gyldig_til, v_registrert
      FROM public.kildedokument d
     WHERE d.tenant = p_tenant AND d.kilde_id = p_kilde_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm46_registrer_punkt: ukjent kildedokument %',
            p_kilde_id USING ERRCODE = 'no_data_found';
    END IF;
    SELECT pr.kilde_gyldig_dogn INTO v_kilde_dogn
      FROM public.anbudsprofil pr WHERE pr.tenant = p_tenant;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm46_registrer_punkt: tenanten % har ingen'
            ' anbudsprofil — gyldighetsvinduet for kilder er'
            ' tenantens, og et punkt uten det er et punkt ingen kan'
            ' etterprøve', p_tenant
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Dokumentets EGEN utløpsdato vinner når den finnes; ellers
    -- tenantens vindu regnet fra registreringen.
    IF (v_gyldig_til IS NOT NULL AND v_gyldig_til < current_date)
       OR (v_gyldig_til IS NULL
           AND v_registrert
               < now() - make_interval(days => v_kilde_dogn)) THEN
        RAISE EXCEPTION 'm46_registrer_punkt: kildedokumentet % er'
            ' ikke gyldig lenger — et utløpt sertifikat er ikke'
            ' dokumentasjon', p_kilde_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO public.utkastpunkt
        (tenant, punkt_id, utkast_id, krav_id, kilde_id, sitat,
         sidereferanse, registrert_av)
    VALUES (p_tenant, p_punkt_id, p_utkast_id, p_krav_id, p_kilde_id,
            btrim(p_sitat), btrim(p_sidereferanse), p_aktor);

    PERFORM public.m46_evidens(p_tenant, v_anbud,
        'utkastpunkt_registrert', p_aktor,
        jsonb_build_object('punkt_id', p_punkt_id,
                           'krav_id', p_krav_id,
                           'kilde_id', p_kilde_id));
END $$;
REVOKE ALL ON FUNCTION m46_registrer_punkt(
    TEXT, UUID, UUID, UUID, UUID, TEXT, TEXT, TEXT) FROM PUBLIC;


-- DOM 3 OG 4. MODULENS VIKTIGSTE DØR.
--
-- «KLAR TIL GJENNOMGANG» ER IKKE «SENDT», og det er ikke ordkløveri:
-- det er en tilstand HOS OSS. Et menneske sier at utkastet er ferdig
-- fra modulens side. Hva som skjer videre — om noen laster det ned,
-- redigerer det og sender det inn i portalen — er utenfor modulen.
--
-- OG DØRA NEKTER SÅ LENGE ET ABSOLUTT KRAV STÅR UDEKKET. Det er her
-- «udekkede krav blir unntak, aldri utfylt gjetning» får tenner: uten
-- denne vakten kunne noen merket et utkast klart med hull i, og
-- hullet ville bare vært et funn i en liste ingen leser før fristen.
--
-- ET VEKTET KRAV STOPPER IKKE. Forskjellen er hele grunnen til at
-- `absolutt` står på kravraden: et absolutt krav som mangler
-- dokumentasjon fører til AVVISNING av tilbudet, mens et vektet gir
-- trekk. Å behandle dem likt ville enten blokkert utkast som er
-- lovlig ufullstendige, eller sluppet gjennom utkast som blir avvist.
CREATE FUNCTION m46_merk_klart(
    p_tenant TEXT, p_utkast_id UUID, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_anbud UUID;
    v_klar BOOLEAN;
    v_udekket INT;
    v_forste TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm46_merk_klart');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    SELECT u.anbud_id, u.klar_til_gjennomgang INTO v_anbud, v_klar
      FROM public.anbudsutkast u
     WHERE u.tenant = p_tenant AND u.utkast_id = p_utkast_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm46_merk_klart: ukjent utkast %', p_utkast_id
            USING ERRCODE = 'no_data_found';
    END IF;
    PERFORM 1 FROM public.anbud
     WHERE tenant = p_tenant AND anbud_id = v_anbud FOR UPDATE;
    -- KLARMERKET LESES PÅ NYTT UNDER LÅSEN, samme grunn som over: to
    -- samtidige klarmerkinger skal gi ÉN, ikke to evidenslinjer.
    SELECT u.klar_til_gjennomgang INTO v_klar
      FROM public.anbudsutkast u
     WHERE u.tenant = p_tenant AND u.utkast_id = p_utkast_id;
    IF v_klar THEN
        RETURN 0;  -- idempotent
    END IF;

    -- DE ABSOLUTTE KRAVENE UTEN ET PUNKT SOM DEKKER DEM.
    SELECT count(*), min(k.kravnummer) INTO v_udekket, v_forste
      FROM public.kvalifikasjonskrav k
     WHERE k.tenant = p_tenant AND k.anbud_id = v_anbud
       AND k.absolutt
       AND NOT EXISTS (SELECT 1 FROM public.utkastpunkt p
                        WHERE p.tenant = k.tenant
                          AND p.utkast_id = p_utkast_id
                          AND p.krav_id = k.krav_id);
    IF v_udekket > 0 THEN
        RAISE EXCEPTION 'm46_merk_klart: % absolutte krav står'
            ' udekket (første: %). Et absolutt krav uten'
            ' dokumentasjon fører til AVVISNING av tilbudet — det'
            ' skal ikke kunne merkes klart, og det skal ikke fylles'
            ' inn med en gjetning', v_udekket, v_forste
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.anbudsutkast
       SET klar_til_gjennomgang = true, klar_ts = now(),
           klar_av = p_aktor
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id;

    -- HVOR MANGE VEKTEDE KRAV SOM FORTSATT STÅR UDEKKET returneres, så
    -- den som merker klart får vite hva som mangler i stedet for å tro
    -- at alt er dekket.
    SELECT count(*) INTO v_udekket
      FROM public.kvalifikasjonskrav k
     WHERE k.tenant = p_tenant AND k.anbud_id = v_anbud
       AND NOT k.absolutt
       AND NOT EXISTS (SELECT 1 FROM public.utkastpunkt p
                        WHERE p.tenant = k.tenant
                          AND p.utkast_id = p_utkast_id
                          AND p.krav_id = k.krav_id);

    PERFORM public.m46_evidens(p_tenant, v_anbud, 'utkast_merket_klart',
        p_aktor, jsonb_build_object('utkast_id', p_utkast_id,
                                    'udekkede_vektede', v_udekket));
    RETURN v_udekket;
END $$;
REVOKE ALL ON FUNCTION m46_merk_klart(TEXT, UUID, TEXT) FROM PUBLIC;


CREATE FUNCTION m46_sett_anbudaktiv(
    p_tenant TEXT, p_anbud_id UUID, p_aktiv BOOLEAN, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_var BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm46_sett_anbudaktiv');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    SELECT aktiv INTO v_var FROM public.anbud
     WHERE tenant = p_tenant AND anbud_id = p_anbud_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm46_sett_anbudaktiv: ukjent anbud %',
            p_anbud_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_var = p_aktiv THEN
        RETURN;  -- idempotent
    END IF;

    UPDATE public.anbud SET aktiv = p_aktiv
     WHERE tenant = p_tenant AND anbud_id = p_anbud_id;

    -- HISTORIKKEN BLIR STÅENDE. «Vi går ikke for denne» er ikke
    -- «konkurransen fantes aldri»: krav, kilder, utkast og punkter er
    -- frosset og røres ikke.
    PERFORM public.m46_evidens(p_tenant, p_anbud_id,
        CASE WHEN p_aktiv THEN 'anbud_aktivert'
             ELSE 'anbud_deaktivert' END, p_aktor, '{}'::jsonb);
END $$;
REVOKE ALL ON FUNCTION m46_sett_anbudaktiv(TEXT, UUID, BOOLEAN, TEXT)
    FROM PUBLIC;


CREATE FUNCTION m46_lukk_funn(
    p_tenant TEXT, p_anbud_id UUID, p_funntype TEXT, p_notat TEXT,
    p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_apen BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm46_lukk_funn');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    IF length(btrim(coalesce(p_notat, ''))) < 4 THEN
        RAISE EXCEPTION 'm46_lukk_funn: et funn lukkes med en'
            ' begrunnelse, ikke med et klikk'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- ET UDEKKET ABSOLUTT KRAV LUKKES IKKE HER, av samme grunn som
    -- M-49s bekreftede treff (117): et absolutt krav uten
    -- dokumentasjon fører til avvisning av tilbudet, og en knapp som
    -- gjorde den observasjonen borte ville sett ut som saksbehandling.
    -- Funnet lukkes når kravet FAKTISK dekkes, eller når anbudet
    -- deaktiveres fordi vi ikke går for det.
    IF p_funntype = 'udekket_absolutt_krav' THEN
        RAISE EXCEPTION 'm46_lukk_funn: et udekket ABSOLUTT krav kan'
            ' ikke lukkes bort. Det lukkes når kravet dekkes av et'
            ' punkt med kilde, eller når anbudet deaktiveres'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT apen INTO v_apen FROM public.anbudsfunn
     WHERE tenant = p_tenant AND anbud_id = p_anbud_id
       AND funntype = p_funntype FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm46_lukk_funn: ukjent funn %/%', p_anbud_id,
            p_funntype USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_apen THEN
        RETURN;  -- idempotent
    END IF;

    UPDATE public.anbudsfunn
       SET apen = false, lukket_ts = now()
     WHERE tenant = p_tenant AND anbud_id = p_anbud_id
       AND funntype = p_funntype;

    PERFORM public.m46_evidens(p_tenant, p_anbud_id, 'funn_lukket',
        p_aktor, jsonb_build_object('funntype', p_funntype,
                                    'notat', btrim(p_notat)));
END $$;
REVOKE ALL ON FUNCTION m46_lukk_funn(TEXT, UUID, TEXT, TEXT, TEXT)
    FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Lesedørene.
-- ------------------------------------------------------------

CREATE FUNCTION m46_profilen(p_tenant TEXT)
RETURNS TABLE (nace_koder TEXT[], geografi TEXT[],
               min_verdi_ore BIGINT, maks_verdi_ore BIGINT,
               frist_varsel_dogn INT, kilde_gyldig_dogn INT,
               versjon INT, oppdatert TIMESTAMPTZ, oppdatert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm46_profilen');
    RETURN QUERY
    SELECT p.nace_koder, p.geografi, p.min_verdi_ore,
           p.maks_verdi_ore, p.frist_varsel_dogn, p.kilde_gyldig_dogn,
           p.versjon, p.oppdatert, p.oppdatert_av
      FROM public.anbudsprofil p WHERE p.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m46_profilen(TEXT) FROM PUBLIC;


CREATE FUNCTION m46_anbudene(p_tenant TEXT, p_grense INT)
RETURNS TABLE (anbud_id UUID, ekstern_ref TEXT, kilde TEXT,
               tittel TEXT, oppdragsgiver TEXT, nace_kode TEXT,
               geografi TEXT, verdi_ore BIGINT, frist TIMESTAMPTZ,
               aktiv BOOLEAN, dogn_til_frist INT, antall_krav BIGINT,
               absolutte_krav BIGINT, udekkede_absolutte BIGINT,
               siste_utkast INT, klar BOOLEAN, apne_funn BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm46_anbudene');
    IF p_grense IS NULL OR p_grense < 1 OR p_grense > 500 THEN
        RAISE EXCEPTION 'm46_anbudene: grensen må være 1..500 (%)',
            p_grense USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    SELECT a.anbud_id, a.ekstern_ref, a.kilde, a.tittel,
           a.oppdragsgiver, a.nace_kode, a.geografi, a.verdi_ore,
           a.frist, a.aktiv,
           (a.frist::date - current_date),
           coalesce(k.alle, 0), coalesce(k.absolutte, 0),
           -- UDEKKEDE ABSOLUTTE KRAV MOT SISTE UTKAST. Regnes her og
           -- ikke i flaten, så to lesere ikke kan komme til hver sin
           -- konklusjon om hvorvidt et anbud er klart.
           coalesce(u.udekkede, coalesce(k.absolutte, 0)),
           u.versjon, coalesce(u.klar, false), coalesce(f.antall, 0)
      FROM public.anbud a
      LEFT JOIN LATERAL (
           SELECT count(*) AS alle,
                  count(*) FILTER (WHERE kk.absolutt) AS absolutte
             FROM public.kvalifikasjonskrav kk
            WHERE kk.tenant = a.tenant
              AND kk.anbud_id = a.anbud_id) k ON true
      LEFT JOIN LATERAL (
           SELECT uu.versjon, uu.klar_til_gjennomgang AS klar,
                  (SELECT count(*) FROM public.kvalifikasjonskrav kk
                    WHERE kk.tenant = a.tenant
                      AND kk.anbud_id = a.anbud_id
                      AND kk.absolutt
                      AND NOT EXISTS (
                          SELECT 1 FROM public.utkastpunkt pp
                           WHERE pp.tenant = uu.tenant
                             AND pp.utkast_id = uu.utkast_id
                             AND pp.krav_id = kk.krav_id))
                  AS udekkede
             FROM public.anbudsutkast uu
            WHERE uu.tenant = a.tenant AND uu.anbud_id = a.anbud_id
            ORDER BY uu.versjon DESC
            LIMIT 1) u ON true
      LEFT JOIN LATERAL (
           SELECT count(*) AS antall FROM public.anbudsfunn af
            WHERE af.tenant = a.tenant AND af.anbud_id = a.anbud_id
              AND af.apen) f ON true
     WHERE a.tenant = p_tenant
     ORDER BY a.aktiv DESC, a.frist ASC
     LIMIT p_grense;
END $$;
REVOKE ALL ON FUNCTION m46_anbudene(TEXT, INT) FROM PUBLIC;


-- KRAVENE MED SIN DEKNING. Et udekket krav står i SAMME liste som et
-- dekket, med `punkt_id` NULL — det utelates ikke. En flate som bare
-- viste de dekkede ville skjult nettopp det som må gjøres.
CREATE FUNCTION m46_kravene(p_tenant TEXT, p_anbud_id UUID,
                            p_utkast_id UUID)
RETURNS TABLE (krav_id UUID, kravnummer TEXT, kravtekst TEXT,
               kravtype TEXT, absolutt BOOLEAN, punkt_id UUID,
               sitat TEXT, sidereferanse TEXT, kilde_id UUID,
               kildetittel TEXT, kilde_gyldig_til DATE)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm46_kravene');
    RETURN QUERY
    SELECT k.krav_id, k.kravnummer, k.kravtekst, k.kravtype,
           k.absolutt, p.punkt_id, p.sitat, p.sidereferanse,
           p.kilde_id, d.tittel, d.gyldig_til
      FROM public.kvalifikasjonskrav k
      LEFT JOIN public.utkastpunkt p
        ON p.tenant = k.tenant AND p.krav_id = k.krav_id
       AND p.utkast_id = p_utkast_id
      LEFT JOIN public.kildedokument d
        ON d.tenant = p.tenant AND d.kilde_id = p.kilde_id
     WHERE k.tenant = p_tenant AND k.anbud_id = p_anbud_id
     ORDER BY k.absolutt DESC, k.kravnummer;
END $$;
REVOKE ALL ON FUNCTION m46_kravene(TEXT, UUID, UUID) FROM PUBLIC;


CREATE FUNCTION m46_utkastene(p_tenant TEXT, p_anbud_id UUID)
RETURNS TABLE (utkast_id UUID, versjon INT,
               klar_til_gjennomgang BOOLEAN, klar_ts TIMESTAMPTZ,
               klar_av TEXT, opprettet TIMESTAMPTZ,
               opprettet_av TEXT, antall_punkter BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm46_utkastene');
    RETURN QUERY
    SELECT u.utkast_id, u.versjon, u.klar_til_gjennomgang, u.klar_ts,
           u.klar_av, u.opprettet, u.opprettet_av,
           (SELECT count(*) FROM public.utkastpunkt p
             WHERE p.tenant = u.tenant AND p.utkast_id = u.utkast_id)
      FROM public.anbudsutkast u
     WHERE u.tenant = p_tenant AND u.anbud_id = p_anbud_id
     ORDER BY u.versjon DESC;
END $$;
REVOKE ALL ON FUNCTION m46_utkastene(TEXT, UUID) FROM PUBLIC;


CREATE FUNCTION m46_kildene(p_tenant TEXT)
RETURNS TABLE (kilde_id UUID, tittel TEXT, dokumenttype TEXT,
               gyldig_til DATE, innhold_sha256 TEXT,
               registrert TIMESTAMPTZ, registrert_av TEXT,
               gyldig_naa BOOLEAN, brukt_i_punkter BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_dogn INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm46_kildene');
    -- Gyldigheten regnes HER, mot tenantens eget vindu, så flaten og
    -- døra aldri kan mene forskjellige ting om hva som er utløpt.
    SELECT p.kilde_gyldig_dogn INTO v_dogn
      FROM public.anbudsprofil p WHERE p.tenant = p_tenant;
    RETURN QUERY
    SELECT d.kilde_id, d.tittel, d.dokumenttype, d.gyldig_til,
           d.innhold_sha256, d.registrert, d.registrert_av,
           CASE
               WHEN d.gyldig_til IS NOT NULL
                   THEN d.gyldig_til >= current_date
               WHEN v_dogn IS NULL THEN NULL
               ELSE d.registrert
                    >= now() - make_interval(days => v_dogn)
           END,
           (SELECT count(*) FROM public.utkastpunkt p
             WHERE p.tenant = d.tenant AND p.kilde_id = d.kilde_id)
      FROM public.kildedokument d
     WHERE d.tenant = p_tenant
     ORDER BY d.registrert DESC;
END $$;
REVOKE ALL ON FUNCTION m46_kildene(TEXT) FROM PUBLIC;


CREATE FUNCTION m46_anbudsstatus(p_tenant TEXT)
RETURNS TABLE (anbud BIGINT, aktive BIGINT, med_utkast BIGINT,
               klare BIGINT, udekkede_absolutte BIGINT,
               naermeste_frist TIMESTAMPTZ, apne_funn BIGINT,
               kilder BIGINT, utlopte_kilder BIGINT,
               har_profil BOOLEAN, profilversjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_dogn INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm46_anbudsstatus');
    SELECT p.kilde_gyldig_dogn INTO v_dogn
      FROM public.anbudsprofil p WHERE p.tenant = p_tenant;
    RETURN QUERY
    SELECT (SELECT count(*) FROM public.anbud a
             WHERE a.tenant = p_tenant),
           (SELECT count(*) FROM public.anbud a
             WHERE a.tenant = p_tenant AND a.aktiv),
           (SELECT count(DISTINCT u.anbud_id)
              FROM public.anbudsutkast u WHERE u.tenant = p_tenant),
           (SELECT count(*) FROM public.anbudsutkast u
             WHERE u.tenant = p_tenant AND u.klar_til_gjennomgang),
           -- UDEKKEDE ABSOLUTTE KRAV PÅ AKTIVE ANBUD. Modulens
           -- viktigste tall: det er disse som gjør et tilbud avvist.
           (SELECT count(*) FROM public.kvalifikasjonskrav k
              JOIN public.anbud a
                ON a.tenant = k.tenant AND a.anbud_id = k.anbud_id
             WHERE k.tenant = p_tenant AND k.absolutt AND a.aktiv
               AND NOT EXISTS (
                   SELECT 1 FROM public.utkastpunkt p
                     JOIN public.anbudsutkast u
                       ON u.tenant = p.tenant
                      AND u.utkast_id = p.utkast_id
                    WHERE p.tenant = k.tenant
                      AND p.krav_id = k.krav_id
                      AND u.anbud_id = k.anbud_id)),
           (SELECT min(a.frist) FROM public.anbud a
             WHERE a.tenant = p_tenant AND a.aktiv
               AND a.frist >= now()),
           (SELECT count(*) FROM public.anbudsfunn f
             WHERE f.tenant = p_tenant AND f.apen),
           (SELECT count(*) FROM public.kildedokument d
             WHERE d.tenant = p_tenant),
           (SELECT count(*) FROM public.kildedokument d
             WHERE d.tenant = p_tenant
               AND ((d.gyldig_til IS NOT NULL
                     AND d.gyldig_til < current_date)
                 OR (d.gyldig_til IS NULL AND v_dogn IS NOT NULL
                     AND d.registrert
                         < now() - make_interval(days => v_dogn)))),
           EXISTS (SELECT 1 FROM public.anbudsprofil p
                    WHERE p.tenant = p_tenant),
           (SELECT p.versjon FROM public.anbudsprofil p
             WHERE p.tenant = p_tenant);
END $$;
REVOKE ALL ON FUNCTION m46_anbudsstatus(TEXT) FROM PUBLIC;


CREATE FUNCTION m46_funnene(p_tenant TEXT, p_bare_apne BOOLEAN)
RETURNS TABLE (anbud_id UUID, ekstern_ref TEXT, tittel TEXT,
               frist TIMESTAMPTZ, funntype TEXT, over_grense INT,
               detalj TEXT, profilversjon INT,
               forst_sett TIMESTAMPTZ, sist_sett_sveip TIMESTAMPTZ,
               apen BOOLEAN, lukket_ts TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm46_funnene');
    RETURN QUERY
    SELECT f.anbud_id, a.ekstern_ref, a.tittel, a.frist, f.funntype,
           f.over_grense, f.detalj, f.profilversjon, f.forst_sett,
           f.sist_sett_sveip, f.apen, f.lukket_ts
      FROM public.anbudsfunn f
      JOIN public.anbud a
        ON a.tenant = f.tenant AND a.anbud_id = f.anbud_id
     WHERE f.tenant = p_tenant
       AND (NOT coalesce(p_bare_apne, true) OR f.apen)
     ORDER BY f.apen DESC, a.frist ASC;
END $$;
REVOKE ALL ON FUNCTION m46_funnene(TEXT, BOOLEAN) FROM PUBLIC;


-- ------------------------------------------------------------
-- 5. Sveipen.
-- ------------------------------------------------------------

-- TENANTLISTA MATERIALISERES FØR LØKKA (112s lærdom, gjentatt i
-- 116/117): `FOR t IN SELECT ...` er en LAT markør.
--
-- SVEIPEN FYLLER INGENTING INN. Den ser hvert udekket krav og hvert
-- kildedokument — og en «hjelpsom» automatikk som fant nærmeste
-- passende kilde og skrev et punkt, ville vært nøyaktig den utfylte
-- gjetningen vakten forbyr. Et udekket krav blir et FUNN.
CREATE FUNCTION m46_sveip_anbud(p_maks_tenanter INT)
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
        RAISE EXCEPTION 'm46_sveip_anbud: maks_tenanter må være minst'
            ' 1 (%)', p_maks_tenanter
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.tenant', '', true);
    SELECT array_agg(DISTINCT a.tenant ORDER BY a.tenant)
      INTO v_tenanter FROM public.anbud a;
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

        WITH profil AS (
            SELECT p.nace_koder, p.geografi, p.min_verdi_ore,
                   p.maks_verdi_ore, p.frist_varsel_dogn,
                   p.kilde_gyldig_dogn, p.versjon
              FROM public.anbudsprofil p WHERE p.tenant = v_t),
        -- SISTE UTKAST PER ANBUD, og dekningen målt mot NETTOPP det.
        siste AS (
            SELECT a.anbud_id, a.frist, a.nace_kode, a.verdi_ore,
                   u.utkast_id, u.klar_til_gjennomgang AS klar
              FROM public.anbud a
              LEFT JOIN LATERAL (
                   SELECT uu.utkast_id, uu.klar_til_gjennomgang
                     FROM public.anbudsutkast uu
                    WHERE uu.tenant = a.tenant
                      AND uu.anbud_id = a.anbud_id
                    ORDER BY uu.versjon DESC
                    LIMIT 1) u ON true
             WHERE a.tenant = v_t AND a.aktiv),
        dekning AS (
            SELECT s.anbud_id,
                   count(*) FILTER (WHERE k.absolutt
                                      AND p.punkt_id IS NULL)
                       AS udekkede_abs,
                   count(*) FILTER (WHERE NOT k.absolutt
                                      AND p.punkt_id IS NULL)
                       AS udekkede_vektede,
                   count(*) AS alle_krav,
                   min(k.kravnummer) FILTER (WHERE k.absolutt
                                               AND p.punkt_id IS NULL)
                       AS forste_abs,
                   min(k.kravnummer) FILTER (WHERE NOT k.absolutt
                                               AND p.punkt_id IS NULL)
                       AS forste_vektet
              FROM siste s
              JOIN public.kvalifikasjonskrav k
                ON k.tenant = v_t AND k.anbud_id = s.anbud_id
              LEFT JOIN public.utkastpunkt p
                ON p.tenant = v_t AND p.krav_id = k.krav_id
               AND p.utkast_id = s.utkast_id
             GROUP BY s.anbud_id),
        utlopt AS (
            -- PUNKTER SOM PEKER PÅ ET UTLØPT DOKUMENT. Kilden var
            -- gyldig da punktet ble skrevet; sertifikatet gikk ut
            -- siden. Utkastet påstår da noe kilden ikke lenger bærer.
            SELECT DISTINCT s.anbud_id, min(d.tittel) AS tittel
              FROM siste s
              JOIN public.utkastpunkt p
                ON p.tenant = v_t AND p.utkast_id = s.utkast_id
              JOIN public.kildedokument d
                ON d.tenant = v_t AND d.kilde_id = p.kilde_id
              CROSS JOIN profil pr
             WHERE (d.gyldig_til IS NOT NULL
                    AND d.gyldig_til < current_date)
                OR (d.gyldig_til IS NULL
                    AND d.registrert < now()
                        - make_interval(days => pr.kilde_gyldig_dogn))
             GROUP BY s.anbud_id),
        kand AS (
            SELECT s.anbud_id, 'ingen_profil'::text AS funntype,
                   NULL::int AS over_grense, NULL::text AS detalj,
                   NULL::int AS profilversjon
              FROM siste s WHERE NOT EXISTS (SELECT 1 FROM profil)

            UNION ALL
            -- FRISTEN NÆRMER SEG. Modulens mest betente funn: en
            -- frist som passerer er den ene feilen som ikke kan
            -- rettes dagen etter.
            SELECT s.anbud_id, 'frist_naermer_seg',
                   (s.frist::date - current_date),
                   to_char(s.frist, 'YYYY-MM-DD'), pr.versjon
              FROM siste s CROSS JOIN profil pr
             WHERE s.frist >= now()
               AND s.frist <= now()
                   + make_interval(days => pr.frist_varsel_dogn)
               AND NOT coalesce(s.klar, false)

            UNION ALL
            SELECT s.anbud_id, 'frist_passert',
                   (s.frist::date - current_date),
                   to_char(s.frist, 'YYYY-MM-DD'), pr.versjon
              FROM siste s CROSS JOIN profil pr
             WHERE s.frist < now() AND NOT coalesce(s.klar, false)

            UNION ALL
            SELECT d.anbud_id, 'udekket_absolutt_krav',
                   d.udekkede_abs::int, d.forste_abs, pr.versjon
              FROM dekning d CROSS JOIN profil pr
             WHERE d.udekkede_abs > 0

            UNION ALL
            SELECT d.anbud_id, 'udekket_krav',
                   d.udekkede_vektede::int, d.forste_vektet, pr.versjon
              FROM dekning d CROSS JOIN profil pr
             WHERE d.udekkede_vektede > 0

            UNION ALL
            SELECT s.anbud_id, 'ingen_krav_registrert', NULL::int,
                   NULL::text, pr.versjon
              FROM siste s CROSS JOIN profil pr
             WHERE NOT EXISTS (SELECT 1
                                 FROM public.kvalifikasjonskrav k
                                WHERE k.tenant = v_t
                                  AND k.anbud_id = s.anbud_id)

            UNION ALL
            SELECT u.anbud_id, 'utlopt_kilde', NULL::int, u.tittel,
                   pr.versjon
              FROM utlopt u CROSS JOIN profil pr

            UNION ALL
            -- UTENFOR PROFILEN. Et anbud noen førte inn som ikke
            -- passer søkeprofilen er ikke nødvendigvis feil — men det
            -- er verdt å se, fordi det enten er en god grunn eller en
            -- profil som er for smal.
            SELECT s.anbud_id, 'utenfor_profil', NULL::int,
                   s.nace_kode, pr.versjon
              FROM siste s CROSS JOIN profil pr
             WHERE NOT (s.nace_kode = ANY (pr.nace_koder))
                OR (s.verdi_ore IS NOT NULL
                    AND (s.verdi_ore < pr.min_verdi_ore
                      OR s.verdi_ore > pr.maks_verdi_ore))
        ),
        skrevet AS (
            INSERT INTO public.anbudsfunn
                (tenant, anbud_id, funntype, over_grense, detalj,
                 profilversjon)
            SELECT v_t, k.anbud_id, k.funntype, k.over_grense,
                   k.detalj, k.profilversjon
              FROM kand k
            ON CONFLICT (tenant, anbud_id, funntype) DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                detalj = EXCLUDED.detalj,
                profilversjon = EXCLUDED.profilversjon,
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
        -- retting, gjentatt i 116/117).
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);

        PERFORM set_config('disponit.tenant', '', true);
    END LOOP;

    -- LUKKINGEN I EGEN RUNDE: kandidatsettet må regnes på nytt i
    -- tenantens egen RLS-kontekst (117s form).
    FOREACH v_t IN ARRAY v_tenanter
    LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        WITH profil AS (
            SELECT p.nace_koder, p.min_verdi_ore, p.maks_verdi_ore,
                   p.frist_varsel_dogn, p.kilde_gyldig_dogn
              FROM public.anbudsprofil p WHERE p.tenant = v_t),
        siste AS (
            SELECT a.anbud_id, a.frist, a.nace_kode, a.verdi_ore,
                   u.utkast_id, u.klar_til_gjennomgang AS klar
              FROM public.anbud a
              LEFT JOIN LATERAL (
                   SELECT uu.utkast_id, uu.klar_til_gjennomgang
                     FROM public.anbudsutkast uu
                    WHERE uu.tenant = a.tenant
                      AND uu.anbud_id = a.anbud_id
                    ORDER BY uu.versjon DESC
                    LIMIT 1) u ON true
             WHERE a.tenant = v_t AND a.aktiv),
        dekning AS (
            SELECT s.anbud_id,
                   count(*) FILTER (WHERE k.absolutt
                                      AND p.punkt_id IS NULL)
                       AS udekkede_abs,
                   count(*) FILTER (WHERE NOT k.absolutt
                                      AND p.punkt_id IS NULL)
                       AS udekkede_vektede
              FROM siste s
              JOIN public.kvalifikasjonskrav k
                ON k.tenant = v_t AND k.anbud_id = s.anbud_id
              LEFT JOIN public.utkastpunkt p
                ON p.tenant = v_t AND p.krav_id = k.krav_id
               AND p.utkast_id = s.utkast_id
             GROUP BY s.anbud_id),
        utlopt AS (
            SELECT DISTINCT s.anbud_id
              FROM siste s
              JOIN public.utkastpunkt p
                ON p.tenant = v_t AND p.utkast_id = s.utkast_id
              JOIN public.kildedokument d
                ON d.tenant = v_t AND d.kilde_id = p.kilde_id
              CROSS JOIN profil pr
             WHERE (d.gyldig_til IS NOT NULL
                    AND d.gyldig_til < current_date)
                OR (d.gyldig_til IS NULL
                    AND d.registrert < now()
                        - make_interval(days => pr.kilde_gyldig_dogn))),
        kand AS (
            SELECT s.anbud_id, 'ingen_profil'::text AS funntype
              FROM siste s WHERE NOT EXISTS (SELECT 1 FROM profil)
            UNION ALL
            SELECT s.anbud_id, 'frist_naermer_seg'
              FROM siste s CROSS JOIN profil pr
             WHERE s.frist >= now()
               AND s.frist <= now()
                   + make_interval(days => pr.frist_varsel_dogn)
               AND NOT coalesce(s.klar, false)
            UNION ALL
            SELECT s.anbud_id, 'frist_passert'
              FROM siste s CROSS JOIN profil pr
             WHERE s.frist < now() AND NOT coalesce(s.klar, false)
            UNION ALL
            SELECT d.anbud_id, 'udekket_absolutt_krav'
              FROM dekning d WHERE d.udekkede_abs > 0
            UNION ALL
            SELECT d.anbud_id, 'udekket_krav'
              FROM dekning d WHERE d.udekkede_vektede > 0
            UNION ALL
            SELECT s.anbud_id, 'ingen_krav_registrert'
              FROM siste s CROSS JOIN profil pr
             WHERE NOT EXISTS (SELECT 1
                                 FROM public.kvalifikasjonskrav k
                                WHERE k.tenant = v_t
                                  AND k.anbud_id = s.anbud_id)
            UNION ALL
            SELECT u.anbud_id, 'utlopt_kilde' FROM utlopt u
            UNION ALL
            SELECT s.anbud_id, 'utenfor_profil'
              FROM siste s CROSS JOIN profil pr
             WHERE NOT (s.nace_kode = ANY (pr.nace_koder))
                OR (s.verdi_ore IS NOT NULL
                    AND (s.verdi_ore < pr.min_verdi_ore
                      OR s.verdi_ore > pr.maks_verdi_ore))
        ),
        lukket AS (
            UPDATE public.anbudsfunn f
               SET apen = false, lukket_ts = now()
             WHERE f.tenant = v_t AND f.apen
               AND NOT EXISTS (
                   SELECT 1 FROM kand k
                    WHERE k.anbud_id = f.anbud_id
                      AND k.funntype = f.funntype)
            RETURNING 1)
        SELECT count(*) INTO v_n FROM lukket;
        v_lukket := v_lukket + coalesce(v_n, 0);
        PERFORM set_config('disponit.tenant', '', true);
    END LOOP;

    RETURN QUERY SELECT v_antall, v_nye, v_oppdaterte, v_lukket;
END $$;
REVOKE ALL ON FUNCTION m46_sveip_anbud(INT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 6. RLS. ENABLE + FORCE på alle sju.
-- ------------------------------------------------------------

RESET ROLE;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['anbudsprofil', 'anbud',
                             'kvalifikasjonskrav', 'kildedokument',
                             'anbudsutkast', 'utkastpunkt',
                             'anbudsfunn']
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
                       ' disponit_anbud_eier', t);
    END LOOP;
END $$;

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER (111s form, gjentatt
-- i 112–117): bare på ANBUDSTABELLEN, bare FOR SELECT, bare til
-- eieren, og bare når ingen tenantkontekst står.
CREATE POLICY m46_sveip_tenantliste ON anbud
    FOR SELECT TO disponit_anbud_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- HISTORIKKTABELLENE FÅR IKKE UPDATE, HELLER IKKE FOR EIEREN. To
-- gjerder, av samme grunn som i 110–117.
--
-- `anbudsutkast` ER IKKE MED: `m46_merk_klart` må kunne sette
-- klarmerket. Åpningen lukkes fra den andre siden av radvakten under,
-- som nekter enhver endring av noe ANNET enn de tre klarfeltene — og
-- enhver endring i det hele tatt av en rad som alt er merket klar.
REVOKE UPDATE ON public.kvalifikasjonskrav FROM disponit_anbud_eier;
REVOKE UPDATE ON public.kildedokument FROM disponit_anbud_eier;
REVOKE UPDATE ON public.utkastpunkt FROM disponit_anbud_eier;

-- RADVAKTEN PÅ UTKASTET. Den frosne delen av en tabell som må være
-- delvis skrivbar.
CREATE FUNCTION m46_utkast_frosset()
RETURNS TRIGGER LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF OLD.klar_til_gjennomgang THEN
        RAISE EXCEPTION 'anbudsutkast: utkast % er merket klart og er'
            ' frosset — et nytt punkt hører til et nytt utkast',
            OLD.utkast_id USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.utkast_id IS DISTINCT FROM OLD.utkast_id
       OR NEW.anbud_id IS DISTINCT FROM OLD.anbud_id
       OR NEW.versjon IS DISTINCT FROM OLD.versjon
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av THEN
        RAISE EXCEPTION 'anbudsutkast: utkastets egne felter er'
            ' frosset — bare klarmerket kan settes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER anbudsutkast_frosset
    BEFORE UPDATE ON anbudsutkast
    FOR EACH ROW EXECUTE FUNCTION m46_utkast_frosset();

-- SLETTING ER ALDRI LOVLIG.
REVOKE DELETE ON public.anbudsprofil FROM disponit_anbud_eier;
REVOKE DELETE ON public.anbud FROM disponit_anbud_eier;
REVOKE DELETE ON public.kvalifikasjonskrav FROM disponit_anbud_eier;
REVOKE DELETE ON public.kildedokument FROM disponit_anbud_eier;
REVOKE DELETE ON public.anbudsutkast FROM disponit_anbud_eier;
REVOKE DELETE ON public.utkastpunkt FROM disponit_anbud_eier;
REVOKE DELETE ON public.anbudsfunn FROM disponit_anbud_eier;


-- ------------------------------------------------------------
-- 7. Rettighetene. SP-7: kjøretidsrollen får INGEN tabellrettigheter.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_anbud_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m46_anbudsstatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m46_profilen(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m46_anbudene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m46_kravene(TEXT, UUID, UUID) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m46_utkastene(TEXT, UUID) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m46_kildene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m46_funnene(TEXT, BOOLEAN) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m46_sett_profil(TEXT, TEXT[], TEXT[], BIGINT, BIGINT,'
            ' INT, INT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m46_registrer_anbud(TEXT, UUID, TEXT, TEXT, TEXT, TEXT,'
            ' TEXT, TEXT, BIGINT, TIMESTAMPTZ, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m46_registrer_krav(TEXT, UUID, UUID, TEXT, TEXT, TEXT,'
            ' BOOLEAN, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m46_registrer_kilde(TEXT, UUID, TEXT, TEXT, DATE, TEXT,'
            ' TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m46_opprett_utkast(TEXT, UUID, UUID, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m46_registrer_punkt(TEXT, UUID, UUID, UUID, UUID, TEXT,'
            ' TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m46_merk_klart(TEXT, UUID, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m46_sett_anbudaktiv(TEXT, UUID, BOOLEAN, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m46_lukk_funn(TEXT, UUID, TEXT, TEXT, TEXT)'
            ' TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_anbudssveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m46_sveip_anbud(INT)'
            ' TO disponit_anbudssveip';
    END IF;
END $$;

-- SVEIPEN ER IKKE KJØRETIDSROLLENS. Vaktet, av samme grunn som i 117:
-- `REVOKE ... FROM <rolle som ikke finnes>` er en FEIL i PostgreSQL,
-- ikke en no-op, og GRANT-blokken over behandler `disponit` som
-- valgfri.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'REVOKE EXECUTE ON FUNCTION m46_sveip_anbud(INT)'
            ' FROM disponit';
    END IF;
END $$;

RESET ROLE;
