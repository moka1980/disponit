-- =====================================================================
-- M-28 LOGISTIKK- OG TRANSPORTAGENT (v1) — KLYNGE 10s TREDJE.
-- =====================================================================
--
-- V1-DOMMEN: MODULEN BESTILLER INGEN TRANSPORT OG OMBOOKER INGENTING.
-- Den planlegger mot registrerte kolli og validerte adresser, og
-- `transportforslag` er et forslag med begrunnelse — ikke en booking.
--
-- ---------------------------------------------------------------------
-- KLYNGENS DELTE DOM, OG INGEN MODUL VISER DEN TYDELIGERE:
--
--   EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN ANGRES IKKE AV EN
--   ROLLBACK.
--
-- Bilen kjører uansett hva basen sier. En booking som ble rullet
-- tilbake er fortsatt en bil på veien, en pakke i en terminal og en
-- faktura fra en transportør.
--
-- ---------------------------------------------------------------------
-- TRE AV SEKS INNGANGSDATA FINNES IKKE. MÅLT, IKKE ANTATT.
--
--   ORDRE            — nei. `bestillingsplan` er en RYTME for
--                      gjentakende bestillinger; `bestillingspunkt`
--                      (M-27) er en terskel.
--   KOLLI            — nei. Ingen kolonne i de 340 tabellene bar
--                      dimensjon, vekt eller fareklasse.
--   TRANSPORTPRISER  — nei. Ingen prisliste, ingen transportør.
--   LAGERLOKASJON    — delvis. `lagerbevegelse` (M-27, 109) har
--                      `vare_id` og `endring`, men ingen lokasjon:
--                      huset teller beholdning, det vet ikke hvor den
--                      står.
--   ADRESSE          — JA. `adressesubjekt`/`adresseversjon`/
--                      `adressekontroll` (M-19, 111).
--   SLA              — JA. `leveranseavtale.sla_type` (M-24).
--
-- KONSEKVENSEN, SOM FOR M-15 OG M-45: KOLLIET REGISTRERES AV ET
-- MENNESKE. Og det gjør modulen ærligere, ikke fattigere.
--
-- ---------------------------------------------------------------------
-- FARECLASSEN OPPGIS. DEN UTLEDES ALDRI.
--
-- Vaktsetningen sier «farlig gods, toll og persondata følger land- og
-- transportørregler». En modul som utledet fareklassen av en
-- produktbeskrivelse ville PÅSTÅTT noe om farlig gods — og en gal
-- påstand der er en brann i en lastebil, ikke en feil i en rapport.
--
-- `kolli.fareklasse_oppgitt_av` er NOT NULL, og settet er ADRs egne ni
-- klasser pluss `ingen`. DET ER IKKE ET SETT VI FANT PÅ: det er den
-- internasjonale standarden, og den er komplett — derfor trenger den
-- ingen `annet`-verdi, og har den ikke.
--
-- ---------------------------------------------------------------------
-- ARVEN FRA 138: LANDPAKKEN ER PORTEN TIL ET LAND.
--
-- «Farlig gods og toll følger LANDregler» er nøyaktig det registeret
-- M-32 bygget. `landpakke` sier hvilke land HUSET HAR LEST REGLENE
-- FOR — et land uten pakke er et land ingen har sjekket.
--
-- `transportforslag.landpakke_regelversjon` er NOT NULL med
-- fremmednøkkel dit. `farlig_gods_uten_landregel` kan derfor aldri
-- reises: et forslag til et land uten pakke lar seg ikke skrive.
--
-- EN M-28 BYGGET FØR M-32 VILLE LAGET SITT EGET LANDBEGREP, og huset
-- ville hatt to. Det er hele grunnen til rekkefølgen i
-- docs/KLYNGE10-FUNDAMENT.md.
--
-- ---------------------------------------------------------------------
-- ADRESSEN ER VALIDERT, ELLER DET BLIR INGEN PLAN.
--
-- Akseptansekravet sier «adresse og tjeneste valideres før booking».
-- Tjenesten finnes ikke; adressen gjør. `m28_foresla` krever en
-- `adressekontroll` med `utfall = 'godkjent'` for den versjonen —
-- ikke bare at adressen finnes.
--
-- OG DET ER EN KOLONNEGRANT, IKKE EN TABELLGRANT (093s form, arvet fra
-- 138): modulen ser `land`, `gjelder_fra` og `versjon_id`, ALDRI gate,
-- postnummer eller poststed.
--
-- HVORFOR TRENGER EN TRANSPORTMODUL IKKE ADRESSEN? Fordi v1 ikke
-- sender noe. Den dagen den gjør det, må granten utvides — og da skal
-- det være en synlig endring i en migrasjon, ikke noe som alt lå der.
--
-- ---------------------------------------------------------------------
-- FIRE FUNN SOM ALDRI KAN REISES, OG DET ER BEVISET.
--
--   `kolli_bestilt_to_ganger`     — ett ÅPENT forslag per kolli
--                                   (partiell unik indeks), og
--                                   ingenting bestilles i det hele
--                                   tatt.
--   `fareklasse_utledet_av_maskin` — `fareklasse_oppgitt_av` NOT NULL,
--                                   og ingen dør utleder den.
--   `farlig_gods_uten_landregel`  — `landpakke_regelversjon` NOT NULL
--                                   med fremmednøkkel til 138.
--   `forslag_uten_validert_adresse` — døra krever `adressekontroll`
--                                   med `utfall = 'godkjent'`.
--
-- ---------------------------------------------------------------------
-- GRENSEN MOT M-27 OG M-52.
--
-- M-27 eier BEHOLDNINGEN — hvor mye vi har. M-52 eier FORTOLLINGEN —
-- HS-koden og deklarasjonen. M-28 eier PLANEN for å flytte et kolli
-- fra et sted til et annet. En modul som utvidet M-27s lagerbevegelse
-- til å bære kolli ville gjort beholdningstelling til forsendelse i
-- stillhet.
-- =====================================================================

-- MODULROLLEN MÅ KUNNE EIE NOE FØR DEN KAN EIE DØRENE.
GRANT USAGE, CREATE ON SCHEMA public TO disponit_transport_eier;
GRANT INSERT ON revisjonslogg TO disponit_transport_eier;

-- HUSETS TENANTVAKT (038). Granten gis av EIEREN.
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_transport_eier;
RESET ROLE;

-- ---------------------------------------------------------------------
-- ARVEN FRA 111 OG 138. BEGGE ER LESERETT, INGEN AV DEM SKRIVERETT.
--
-- Adressen er en KOLONNEGRANT: landet og datoen, aldri gata.
-- Landpakken er en tabellgrant, men bare SELECT — registeret felles i
-- git, og M-28 er en leser der som alle andre.
-- ---------------------------------------------------------------------
GRANT SELECT (tenant, subjekt_id) ON adressesubjekt
    TO disponit_transport_eier;
GRANT SELECT (tenant, versjon_id, subjekt_id, land, gjelder_fra)
    ON adresseversjon TO disponit_transport_eier;
-- KONTROLLEN, IKKE INNHOLDET: utfallet og hvilken versjon det gjaldt.
GRANT SELECT (tenant, kontroll_id, versjon_id, utfall, kontrollert)
    ON adressekontroll TO disponit_transport_eier;
GRANT SELECT ON landpakke TO disponit_transport_eier;

-- ---------------------------------------------------------------------
-- `transportkrav` — TENANTENS GRENSER, APPEND-ONLY.
--
-- 137/138s form, arvet: versjonen tildeles av DØRA, og raden
-- oppdateres aldri. `transportforslag.kravversjon` peker hit.
-- ---------------------------------------------------------------------
CREATE TABLE transportkrav (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kravversjon INT NOT NULL CHECK (kravversjon >= 1),
    -- Avsenderlandet. Uten det kan ingen si hva som er innenlands, og
    -- det er samme grunn som M-32s `selgerland`.
    avsenderland CHAR(2) NOT NULL CHECK (avsenderland ~ '^[A-Z]{2}$'),
    -- Tyngste kolli modulen planlegger for. Over dette er det
    -- partifrakt, og det er en annen samtale enn en pakke.
    maks_kolli_gram BIGINT NOT NULL
        CHECK (maks_kolli_gram BETWEEN 1 AND 100000000),
    -- Over denne vekten skal et menneske se på planen uansett.
    manuell_kontroll_over_gram BIGINT NOT NULL
        CHECK (manuell_kontroll_over_gram >= 0),
    -- Hvor mange døgn et åpent forslag kan stå før det er et funn.
    forslagsfrist_dogn INT NOT NULL
        CHECK (forslagsfrist_dogn BETWEEN 1 AND 365),
    versjon_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    satt_av TEXT NOT NULL CHECK (satt_av ~ '[^[:space:]]'),
    CONSTRAINT transportkrav_pk PRIMARY KEY (tenant, kravversjon)
);

-- ---------------------------------------------------------------------
-- `kolli` — DET ET MENNESKE HAR MÅLT.
--
-- Huset har ingen kolli. Katalogen lover dem; basen har dem ikke, og
-- ingen kan finnes uten en integrasjon huset ikke har. Derfor
-- registreres de her, av noen med et navn.
--
-- FAREKLASSEN ER ADRs NI KLASSER PLUSS `ingen`. Settet er lukket og
-- KOMPLETT — det er den internasjonale standarden, ikke vår
-- oppfinnelse — og derfor trenger det ingen `annet`-verdi.
-- ---------------------------------------------------------------------
CREATE TABLE kolli (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kolli_id UUID NOT NULL,
    -- Tenantens egen referanse. FRI TEKST og ingen fremmednøkkel:
    -- forsendelseshistorikken skal kunne stå alene.
    referanse TEXT NOT NULL CHECK (referanse ~ '[^[:space:]]'),
    -- MILLIMETER OG GRAM, I HELTALL. Flyttall og fysiske mål hører
    -- ikke sammen når noen skal laste en bil etter dem.
    vekt_gram BIGINT NOT NULL CHECK (vekt_gram BETWEEN 1 AND 100000000),
    lengde_mm INT NOT NULL CHECK (lengde_mm BETWEEN 1 AND 20000),
    bredde_mm INT NOT NULL CHECK (bredde_mm BETWEEN 1 AND 20000),
    hoyde_mm INT NOT NULL CHECK (hoyde_mm BETWEEN 1 AND 20000),
    fareklasse TEXT NOT NULL
        CONSTRAINT kolli_fareklasse_lukket
        CHECK (fareklasse IN (
            'ingen',
            'klasse_1_eksplosiver',
            'klasse_2_gasser',
            'klasse_3_brannfarlige_vaesker',
            'klasse_4_brannfarlige_faste_stoffer',
            'klasse_5_oksiderende',
            'klasse_6_giftige_og_smittefarlige',
            'klasse_7_radioaktive',
            'klasse_8_etsende',
            'klasse_9_ovrige_farlige')),
    -- DEN VIKTIGSTE KOLONNEN I TABELLEN.
    --
    -- Et menneske har sagt hva dette er. `fareklasse_utledet_av_maskin`
    -- kan aldri reises fordi ingen dør utleder den, og fordi raden
    -- ikke lar seg skrive uten et navn her.
    fareklasse_oppgitt_av TEXT NOT NULL
        CHECK (fareklasse_oppgitt_av ~ '[^[:space:]]'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT kolli_pk PRIMARY KEY (tenant, kolli_id),
    CONSTRAINT kolli_referanse_unik UNIQUE (tenant, referanse)
);
CREATE INDEX kolli_farlige ON kolli (tenant, fareklasse)
    WHERE fareklasse <> 'ingen';

-- ---------------------------------------------------------------------
-- `transportforslag` — DER VEIEN SLUTTER.
--
-- LEGG MERKE TIL HVA SOM IKKE FINNES: ingen `bestilt_ts`, ingen
-- `booking_ref`, ingen `sporingsnummer`, ingen `transportor`, ingen
-- `etikett`. Forslaget ER endestasjonen, og det er ikke en
-- forglemmelse — det er v1-dommen skrevet som kolonner.
--
-- Samme form som `inngrepsforslag` (137) fikk to migrasjoner tidligere,
-- og av samme grunn.
--
-- TRE FREMMEDNØKLER BÆRER TRE AV KLYNGENS FIRE UMULIGE FUNN:
--
--   `kolli_id`               → et forslag uten et målt kolli finnes ikke
--   `landpakke_regelversjon` → `farlig_gods_uten_landregel`
--   `kravversjon`            → grensen som gjaldt er gjenfinnbar
--
-- OG `adressekontroll_id` ER NOT NULL: `forslag_uten_validert_adresse`
-- kan ikke reises, fordi raden ikke lar seg skrive uten en kontroll å
-- peke på.
-- ---------------------------------------------------------------------
CREATE TABLE transportforslag (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    forslag_id UUID NOT NULL,
    kolli_id UUID NOT NULL,
    kravversjon INT NOT NULL,
    -- ADRESSEN, OG KONTROLLEN AV DEN. Versjonen sier hvor det skulle,
    -- kontrollen sier at noen har sett at adressen finnes.
    adresseversjon_id UUID NOT NULL,
    adressekontroll_id UUID NOT NULL,
    -- LANDENE. Mottakerens leses fra adresseversjonen, aldri oppgitt.
    mottakerland CHAR(2) NOT NULL CHECK (mottakerland ~ '^[A-Z]{2}$'),
    avsenderland CHAR(2) NOT NULL CHECK (avsenderland ~ '^[A-Z]{2}$'),
    -- ARVEN FRA 138. Uten en landpakke for mottakerlandet finnes det
    -- ingen leste regler — og da finnes det ikke noe forslag heller.
    landpakke_regelversjon INT NOT NULL,
    -- FAREKLASSEN SLIK DEN STO. Kolliet kan omklassifiseres senere;
    -- planen ble laget under den klassen som gjaldt.
    fareklasse TEXT NOT NULL,
    -- Hvorfor denne planen. Fri tekst, lest av et menneske.
    begrunnelse TEXT NOT NULL CHECK (begrunnelse ~ '[^[:space:]]'),
    status TEXT NOT NULL DEFAULT 'apen'
        CONSTRAINT transportforslag_status_lukket
        CHECK (status IN ('apen', 'forkastet')),
    forkastet_ts TIMESTAMPTZ,
    forkastet_av TEXT,
    forkastet_grunn TEXT,
    -- FORKASTINGEN ER HEL ELLER IKKE SKJEDD.
    CONSTRAINT transportforslag_forkastingen_er_hel CHECK (
        (status = 'forkastet')
        = (forkastet_ts IS NOT NULL AND forkastet_av IS NOT NULL
           AND forkastet_grunn IS NOT NULL)),
    foreslatt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    foreslatt_av TEXT NOT NULL CHECK (foreslatt_av ~ '[^[:space:]]'),
    CONSTRAINT transportforslag_pk PRIMARY KEY (tenant, forslag_id),
    CONSTRAINT transportforslag_kolli_fk
        FOREIGN KEY (tenant, kolli_id) REFERENCES kolli (tenant, kolli_id),
    CONSTRAINT transportforslag_krav_fk
        FOREIGN KEY (tenant, kravversjon)
        REFERENCES transportkrav (tenant, kravversjon),
    CONSTRAINT transportforslag_landpakke_fk
        FOREIGN KEY (mottakerland, landpakke_regelversjon)
        REFERENCES landpakke (landkode, regelversjon)
);

-- ETT ÅPENT FORSLAG PER KOLLI.
--
-- «SAMME KOLLI BESTILLES ALDRI TO GANGER» er akseptansekravet ord for
-- ord. I v1 bestilles ingenting i det hele tatt — men formen står
-- likevel, fordi den er det som skal gjelde den dagen noe bestilles:
-- to åpne planer for samme kolli er to biler til samme pakke.
--
-- Et FORKASTET forslag sperrer ikke: en plan som ble vraket skal kunne
-- erstattes av en ny.
CREATE UNIQUE INDEX transportforslag_ett_apent_per_kolli
    ON transportforslag (tenant, kolli_id) WHERE status = 'apen';
CREATE INDEX transportforslag_apne
    ON transportforslag (tenant, foreslatt_ts) WHERE status = 'apen';

-- ---------------------------------------------------------------------
-- `transportfunn` — HUSETS FORM, MED ETT LUKKET SETT.
-- ---------------------------------------------------------------------
CREATE TABLE transportfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    funn_id UUID NOT NULL DEFAULT gen_random_uuid(),
    funntype TEXT NOT NULL
        CONSTRAINT transportfunn_funntype_lukket
        CHECK (funntype IN (
            -- DE FIRE SOM ALDRI KAN REISES.
            'kolli_bestilt_to_ganger',
            'fareklasse_utledet_av_maskin',
            'farlig_gods_uten_landregel',
            'forslag_uten_validert_adresse',
            -- DE SOM FAKTISK KAN REISES.
            'apent_forslag_over_frist',
            'tungt_kolli_ukontrollert',
            'kolli_uten_forslag',
            'land_uten_pakke',
            'krav_mangler')),
    referanse TEXT NOT NULL CHECK (referanse ~ '[^[:space:]]'),
    detalj TEXT NOT NULL CHECK (detalj ~ '[^[:space:]]'),
    apen BOOLEAN NOT NULL DEFAULT true,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT,
    lukket_grunn TEXT,
    CONSTRAINT transportfunn_lukkingen_er_hel CHECK (
        apen = (lukket_ts IS NULL AND lukket_av IS NULL
                AND lukket_grunn IS NULL)),
    CONSTRAINT transportfunn_pk PRIMARY KEY (tenant, funn_id)
);
CREATE UNIQUE INDEX transportfunn_ett_apent
    ON transportfunn (tenant, funntype, referanse) WHERE apen;

-- =====================================================================
-- RADVAKT OG RETTIGHETER. FORCE RLS PÅ ALLE FIRE.
-- =====================================================================
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['transportkrav', 'kolli',
                             'transportforslag', 'transportfunn']
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format($f$CREATE POLICY tenant_isolasjon ON public.%I
            USING (tenant = current_setting('disponit.tenant', true))
            WITH CHECK (tenant = current_setting('disponit.tenant', true))$f$, t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO'
                       ' disponit_transport_eier', t);
    END LOOP;
END $$;

-- APPEND-ONLY MÅLT SOM EN RETTIGHET.
--
-- Kravet: en grense som kunne endres etter at et forslag pekte på den
-- ville gjort «grensen som gjaldt» til «grensen som gjelder nå»
-- (137s lærdom).
--
-- Kolliet: målene og fareklassen er det et menneske MÅLTE. Kunne de
-- endres, ville planen hvilt på noe annet enn det som ble målt — og
-- `fareklasse_oppgitt_av` ville pekt på feil person.
REVOKE UPDATE ON public.transportkrav FROM disponit_transport_eier;
REVOKE UPDATE ON public.kolli FROM disponit_transport_eier;

-- SVEIPENS KRYSS-TENANT-POLICY (130s LÆRDOM).
CREATE POLICY m28_sveip_tenantliste ON transportkrav
    FOR SELECT
    USING (current_setting('disponit.tenant', true) IS NULL
           OR current_setting('disponit.tenant', true) = '');

-- =====================================================================
-- HERFRA EIES DØRENE AV TRANSPORTEIEREN.
-- =====================================================================
SET LOCAL ROLE disponit_transport_eier;

-- `m28_evidens` — HUSETS SPOR.
CREATE FUNCTION m28_evidens(p_tenant TEXT, p_ref UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm28_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm28_transport', 'handling', p_handling,
        'ref', p_ref::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm28_transport',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:transport', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;

-- `m28_er_farlig` — ADRs SETT, SOM EN FUNKSJON OG IKKE EN HUSKEREGEL.
CREATE FUNCTION m28_er_farlig(p_fareklasse TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT p_fareklasse IS DISTINCT FROM 'ingen'
$$;
REVOKE ALL ON FUNCTION m28_er_farlig(TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- `m28_funn_er_sveipens` — HVEM SOM KAN LUKKE HVA.
--
-- SETTET ER NØYAKTIG DET SVEIPEN REISER, OG DET ER IKKE EN
-- TILFELDIGHET.
--
-- Første utgave hadde `krav_mangler` her og manglet
-- `tungt_kolli_ukontrollert`. Begge deler var galt, og CodeRabbit fant
-- det:
--
--   * `tungt_kolli_ukontrollert` REISES av sveipen, og tilstanden er
--     «gammel OG tung OG åpen». Et menneske kan ikke gjøre planen
--     yngre. Lukket hun den, ville sveipen reist den på nytt neste
--     natt — og det er nettopp den formen 132 kaller «å lukke en
--     måling og ikke en sak».
--   * `krav_mangler` REISES ALDRI: sveipeløkka går over tenanter som
--     HAR et krav, så en tenant uten krav besøkes ikke. Den står i det
--     lukkede settet av samme grunn som de fire umulige — for å
--     NAVNGI tilstanden — og skal ikke merkes som sveipens.
--
-- EN KLASSIFISERING SOM IKKE MATCHER SVEIPEN GJØR DØRA TIL EN
-- HØFLIGHETSSJEKK: den nekter et menneske å lukke noe ingen reiser, og
-- slipper henne til på noe som kommer tilbake neste natt.
-- ---------------------------------------------------------------------
CREATE FUNCTION m28_funn_er_sveipens(p_funntype TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT p_funntype IN ('apent_forslag_over_frist',
                          'kolli_uten_forslag',
                          'tungt_kolli_ukontrollert',
                          'land_uten_pakke')
$$;
REVOKE ALL ON FUNCTION m28_funn_er_sveipens(TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- `m28_sett_krav` — APPEND-ONLY, VERSJONEN TILDELES AV DØRA.
--
-- 137/138s form, og før dem 135s. `max(kravversjon) + 1` uten lås er
-- husets etablerte mønster gjennom seks moduler.
--
-- CODERABBIT BA OM EN RÅDGIVENDE LÅS PER TENANT HER, og det er en
-- ekte observasjon: to samtidige kallere kan lese samme `max` og
-- velge samme nummer.
--
-- DET ER LIKEVEL IKKE RETTET, OG GRUNNEN SKAL STÅ:
--
--   1. PRIMÆRNØKKELEN TAR DET. Den andre kalleren får en
--      `UniqueViolation`, som API-et gjør om til en 400 — ingen rad
--      går tapt, ingen versjon overskrives, ingen data blir gale.
--      Utfallet er «prøv igjen», ikke stille skade.
--   2. Å LEGGE EN LÅS I ÉN AV SEKS MODULER ville gjort M-28 til
--      unntaket, og et mønster som er ulikt seks steder er vanskeligere
--      å stole på enn ett som er likt overalt — også når det likheten
--      bærer er en liten svakhet.
--
-- SKAL DET RETTES, SKAL DET RETTES FOR ALLE SEKS, i en egen migrasjon
-- med sin egen port. Det er eierens prioritering, ikke en nattjobbs.
-- ---------------------------------------------------------------------
CREATE FUNCTION m28_sett_krav(p_tenant TEXT, p_avsenderland TEXT,
                              p_maks_kolli_gram BIGINT,
                              p_manuell_over_gram BIGINT,
                              p_forslagsfrist_dogn INT, p_av TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm28_sett_krav');
    -- AVSENDERLANDET MÅ HA EN LANDPAKKE, av samme grunn som M-32s
    -- selgerland: uten leste regler for landet vi sender FRA, er
    -- ingenting av det som følger etterprøvbart.
    IF NOT EXISTS (SELECT 1 FROM public.landpakke l
                    WHERE l.landkode = p_avsenderland) THEN
        RAISE EXCEPTION 'm28: ingen landpakke for avsenderlandet %.'
                        ' En landpakke felles i en migrasjon, ikke her',
            p_avsenderland;
    END IF;
    SELECT coalesce(max(kravversjon), 0) + 1 INTO v_versjon
      FROM public.transportkrav WHERE tenant = p_tenant;
    INSERT INTO public.transportkrav
        (tenant, kravversjon, avsenderland, maks_kolli_gram,
         manuell_kontroll_over_gram, forslagsfrist_dogn, satt_av)
    VALUES (p_tenant, v_versjon, p_avsenderland, p_maks_kolli_gram,
            p_manuell_over_gram, p_forslagsfrist_dogn, p_av);
    PERFORM public.m28_evidens(p_tenant, NULL, 'sett_krav', p_av,
        jsonb_build_object('kravversjon', v_versjon,
                           'avsenderland', p_avsenderland));
    RETURN v_versjon;
END $$;

-- ---------------------------------------------------------------------
-- `m28_registrer_kolli` — ET MENNESKE HAR MÅLT DETTE.
--
-- `p_fareklasse_oppgitt_av` er PÅKREVD og er et NAVN, ikke en flagg.
-- Døra utleder ingenting: den tar imot det noen har sett på pakken og
-- skrevet ned.
-- ---------------------------------------------------------------------
CREATE FUNCTION m28_registrer_kolli(p_tenant TEXT, p_kolli_id UUID,
                                    p_referanse TEXT,
                                    p_vekt_gram BIGINT,
                                    p_lengde_mm INT, p_bredde_mm INT,
                                    p_hoyde_mm INT, p_fareklasse TEXT,
                                    p_fareklasse_oppgitt_av TEXT,
                                    p_kravversjon INT, p_av TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_krav public.transportkrav%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm28_registrer_kolli');
    SELECT * INTO v_krav FROM public.transportkrav k
     WHERE k.tenant = p_tenant AND k.kravversjon = p_kravversjon;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm28: kravversjon % finnes ikke for %',
            p_kravversjon, p_tenant;
    END IF;
    IF p_vekt_gram > v_krav.maks_kolli_gram THEN
        RAISE EXCEPTION 'm28: % gram er over tenantens tak paa % —'
                        ' dette er partifrakt, ikke en pakke',
            p_vekt_gram, v_krav.maks_kolli_gram;
    END IF;
    INSERT INTO public.kolli
        (tenant, kolli_id, referanse, vekt_gram, lengde_mm, bredde_mm,
         hoyde_mm, fareklasse, fareklasse_oppgitt_av, registrert_av)
    VALUES (p_tenant, p_kolli_id, p_referanse, p_vekt_gram, p_lengde_mm,
            p_bredde_mm, p_hoyde_mm, p_fareklasse,
            p_fareklasse_oppgitt_av, p_av);
    PERFORM public.m28_evidens(p_tenant, p_kolli_id, 'registrer_kolli',
        p_av, jsonb_build_object('fareklasse', p_fareklasse,
                                 'oppgitt_av', p_fareklasse_oppgitt_av,
                                 'vekt_gram', p_vekt_gram));
END $$;

-- =====================================================================
-- `m28_foresla` — MODULENS HOVEDDØR, OG DEN NEKTER FEM GANGER.
--
-- «ADRESSE OG TJENESTE VALIDERES FØR BOOKING» — akseptansekravet.
-- Tjenesten finnes ikke; adressen gjør, og den må være GODKJENT.
--
--   1. Kolliet finnes ikke for tenanten.
--   2. Adresseversjonen finnes ikke, eller har intet brukbart land.
--   3. Ingen `adressekontroll` med `utfall = 'godkjent'` for versjonen.
--   4. Mottakerlandet har ingen landpakke — INGEN LESTE REGLER.
--   5. Kolliet har alt et åpent forslag.
--
-- MOTTAKERLANDET LESES FRA ADRESSEVERSJONEN, ikke fra en parameter.
-- En parameter ville gjort modulen til en planlegger som planlegger
-- mot det den får beskjed om — og en plan til feil land er en pakke
-- som havner der.
-- =====================================================================
CREATE FUNCTION m28_foresla(p_tenant TEXT, p_forslag_id UUID,
                            p_kolli_id UUID, p_kravversjon INT,
                            p_adresseversjon_id UUID,
                            p_begrunnelse TEXT, p_av TEXT)
RETURNS TABLE (mottakerland TEXT, landpakke_regelversjon INT,
               fareklasse TEXT, farlig BOOLEAN, krever_kontroll BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_krav      public.transportkrav%ROWTYPE;
    v_kolli     public.kolli%ROWTYPE;
    v_land      CHAR(2);
    v_kontroll  UUID;
    v_regelversjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm28_foresla');

    -- INGEN `FOR UPDATE`: kravet er append-only og eieren har ingen
    -- UPDATE. En lås mot en umulig endring måler ingenting, og ville
    -- krevd nettopp den retten vi med vilje ikke har (137s lærdom).
    SELECT * INTO v_krav FROM public.transportkrav k
     WHERE k.tenant = p_tenant AND k.kravversjon = p_kravversjon;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm28: kravversjon % finnes ikke for %',
            p_kravversjon, p_tenant;
    END IF;

    -- 1. KOLLIET. Et forslag uten et målt kolli er en plan for
    -- ingenting.
    SELECT * INTO v_kolli FROM public.kolli k
     WHERE k.tenant = p_tenant AND k.kolli_id = p_kolli_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm28: kolli % finnes ikke for %',
            p_kolli_id, p_tenant;
    END IF;

    -- 2. LANDET LESES FRA ADRESSEVERSJONEN, ikke fra en parameter.
    SELECT a.land INTO v_land FROM public.adresseversjon a
     WHERE a.tenant = p_tenant AND a.versjon_id = p_adresseversjon_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm28: adresseversjon % finnes ikke for %',
            p_adresseversjon_id, p_tenant;
    END IF;
    IF v_land IS NULL OR v_land !~ '^[A-Z]{2}$' THEN
        RAISE EXCEPTION 'm28: adresseversjon % har intet brukbart land',
            p_adresseversjon_id;
    END IF;

    -- 3. KONTROLLEN. «Adresse valideres før booking» betyr at NOEN har
    -- sett at den finnes — ikke at den er skrevet inn.
    SELECT k.kontroll_id INTO v_kontroll FROM public.adressekontroll k
     WHERE k.tenant = p_tenant AND k.versjon_id = p_adresseversjon_id
       AND k.utfall = 'godkjent'
     ORDER BY k.kontrollert DESC LIMIT 1;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm28: adresseversjon % har ingen godkjent'
                        ' kontroll — adressen valideres FOER en plan',
            p_adresseversjon_id;
    END IF;

    -- 4. LANDPAKKEN. Uten leste regler for mottakerlandet finnes det
    -- ingen plan — og for farlig gods er det ikke en formalitet.
    SELECT l.regelversjon INTO v_regelversjon FROM public.landpakke l
     WHERE l.landkode = v_land
       AND l.gyldig_fra <= current_date
       AND (l.gyldig_til IS NULL OR l.gyldig_til >= current_date)
     ORDER BY l.regelversjon DESC LIMIT 1;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm28: ingen landpakke gjelder for % i dag —'
                        ' huset har ikke lest reglene for det landet',
            v_land;
    END IF;

    -- 5. ETT ÅPENT FORSLAG PER KOLLI. Den partielle unike indeksen er
    -- den EKTE vakten; dette gir en lesbar feil.
    IF EXISTS (SELECT 1 FROM public.transportforslag f
                WHERE f.tenant = p_tenant AND f.kolli_id = p_kolli_id
                  AND f.status = 'apen') THEN
        RAISE EXCEPTION 'm28: kolli % har alt et aapent forslag —'
                        ' forkast det foerst', p_kolli_id;
    END IF;

    INSERT INTO public.transportforslag
        (tenant, forslag_id, kolli_id, kravversjon, adresseversjon_id,
         adressekontroll_id, mottakerland, avsenderland,
         landpakke_regelversjon, fareklasse, begrunnelse, foreslatt_av)
    VALUES (p_tenant, p_forslag_id, p_kolli_id, p_kravversjon,
            p_adresseversjon_id, v_kontroll, v_land,
            v_krav.avsenderland, v_regelversjon, v_kolli.fareklasse,
            p_begrunnelse, p_av);

    PERFORM public.m28_evidens(p_tenant, p_forslag_id, 'foresla', p_av,
        jsonb_build_object('mottakerland', v_land,
                           'regelversjon', v_regelversjon,
                           'fareklasse', v_kolli.fareklasse));
    RETURN QUERY SELECT v_land::TEXT, v_regelversjon,
                        v_kolli.fareklasse,
                        public.m28_er_farlig(v_kolli.fareklasse),
                        v_kolli.vekt_gram
                            > v_krav.manuell_kontroll_over_gram;
END $$;

-- `m28_forkast` — EN PLAN SOM BLE VRAKET, IKKE SLETTET.
--
-- Sletting ville fjernet beviset på at vi hadde planen (M-50s dom,
-- 124). Og et forkastet forslag sperrer ikke for et nytt: en plan som
-- ble vraket skal kunne erstattes.
CREATE FUNCTION m28_forkast(p_tenant TEXT, p_forslag_id UUID,
                            p_grunn TEXT, p_av TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm28_forkast');
    UPDATE public.transportforslag f
       SET status = 'forkastet', forkastet_ts = now(),
           forkastet_av = p_av, forkastet_grunn = p_grunn
     WHERE f.tenant = p_tenant AND f.forslag_id = p_forslag_id
       AND f.status = 'apen';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm28: ingen aapent forslag % for %',
            p_forslag_id, p_tenant;
    END IF;
    PERFORM public.m28_evidens(p_tenant, p_forslag_id, 'forkast', p_av,
                               jsonb_build_object('grunn', p_grunn));
END $$;

-- `m28_lukk_funn` — OG SVEIPENS EGNE KAN INGEN LUKKE.
CREATE FUNCTION m28_lukk_funn(p_tenant TEXT, p_funn_id UUID,
                              p_grunn TEXT, p_av TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_type TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm28_lukk_funn');
    SELECT f.funntype INTO v_type FROM public.transportfunn f
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id AND f.apen;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm28: ingen aapent funn % for %',
            p_funn_id, p_tenant;
    END IF;
    IF public.m28_funn_er_sveipens(v_type) THEN
        RAISE EXCEPTION 'm28: % lukkes av sveipen naar tilstanden er'
                        ' borte, ikke av et menneske', v_type;
    END IF;
    UPDATE public.transportfunn f
       SET apen = false, lukket_ts = now(), lukket_av = p_av,
           lukket_grunn = p_grunn
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;
    PERFORM public.m28_evidens(p_tenant, p_funn_id, 'lukk_funn', p_av,
                               jsonb_build_object('funntype', v_type));
END $$;

-- =====================================================================
-- LESEDØRENE.
-- =====================================================================

CREATE FUNCTION m28_kolliene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (kolli_id UUID, referanse TEXT, vekt_gram BIGINT,
               lengde_mm INT, bredde_mm INT, hoyde_mm INT,
               fareklasse TEXT, farlig BOOLEAN,
               fareklasse_oppgitt_av TEXT, har_apent_forslag BOOLEAN,
               registrert TIMESTAMPTZ)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT k.kolli_id, k.referanse, k.vekt_gram, k.lengde_mm,
           k.bredde_mm, k.hoyde_mm, k.fareklasse,
           public.m28_er_farlig(k.fareklasse),
           -- HVEM SOM SA DET. En fareklasse uten et navn bak er en
           -- påstand ingen svarer for.
           k.fareklasse_oppgitt_av,
           EXISTS (SELECT 1 FROM public.transportforslag f
                    WHERE f.tenant = k.tenant AND f.kolli_id = k.kolli_id
                      AND f.status = 'apen'),
           k.registrert
      FROM public.kolli k
     WHERE k.tenant = p_tenant
     ORDER BY k.registrert DESC
     LIMIT greatest(1, least(coalesce(p_maks, 200), 500))
$$;
REVOKE ALL ON FUNCTION m28_kolliene(TEXT, INT) FROM PUBLIC;

-- `m28_forslagene` — MED LANDPAKKEVERSJONEN, ALLTID.
--
-- En plan uten versjonen av reglene den hviler på er en plan ingen kan
-- etterprøve når reglene endres.
CREATE FUNCTION m28_forslagene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (forslag_id UUID, kolli_id UUID, kolliref TEXT,
               mottakerland TEXT, avsenderland TEXT,
               landpakke_regelversjon INT, fareklasse TEXT,
               farlig BOOLEAN, vekt_gram BIGINT,
               over_kontrollgrense BOOLEAN, status TEXT,
               begrunnelse TEXT, foreslatt_ts TIMESTAMPTZ,
               foreslatt_av TEXT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT f.forslag_id, f.kolli_id, k.referanse, f.mottakerland::TEXT,
           f.avsenderland::TEXT, f.landpakke_regelversjon, f.fareklasse,
           public.m28_er_farlig(f.fareklasse), k.vekt_gram,
           k.vekt_gram > kr.manuell_kontroll_over_gram,
           f.status, f.begrunnelse, f.foreslatt_ts, f.foreslatt_av
      FROM public.transportforslag f
      JOIN public.kolli k
        ON k.tenant = f.tenant AND k.kolli_id = f.kolli_id
      JOIN public.transportkrav kr
        ON kr.tenant = f.tenant AND kr.kravversjon = f.kravversjon
     WHERE f.tenant = p_tenant
     ORDER BY f.foreslatt_ts DESC
     LIMIT greatest(1, least(coalesce(p_maks, 200), 500))
$$;
REVOKE ALL ON FUNCTION m28_forslagene(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m28_transportfunn(p_tenant TEXT, p_maks INT)
RETURNS TABLE (funn_id UUID, funntype TEXT, referanse TEXT,
               detalj TEXT, sveipens BOOLEAN, forst_sett TIMESTAMPTZ)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT f.funn_id, f.funntype, f.referanse, f.detalj,
           public.m28_funn_er_sveipens(f.funntype), f.forst_sett
      FROM public.transportfunn f
     WHERE f.tenant = p_tenant AND f.apen
     ORDER BY f.forst_sett DESC
     LIMIT greatest(1, least(coalesce(p_maks, 200), 500))
$$;
REVOKE ALL ON FUNCTION m28_transportfunn(TEXT, INT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- `m28_bildet` — MODULENS EGEN TILSTAND.
--
-- `bestillinger` er ALLTID 0, og den står her med vilje: tallet er
-- ikke en telling av en kolonne — det er en påstand om at kolonnen
-- ikke finnes. `transportforslag` har ingen `bestilt_ts`, ingen
-- `booking_ref` og ingen `sporingsnummer`.
-- ---------------------------------------------------------------------
CREATE FUNCTION m28_bildet(p_tenant TEXT)
RETURNS TABLE (kolli BIGINT, farlige_kolli BIGINT, apne_forslag BIGINT,
               forkastede BIGINT, land_i_bruk BIGINT,
               bestillinger BIGINT, apne_funn BIGINT, har_krav BOOLEAN,
               avsenderland TEXT, maks_kolli_gram BIGINT,
               manuell_kontroll_over_gram BIGINT,
               forslagsfrist_dogn INT, kravversjon INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    WITH kr AS (
        SELECT * FROM public.transportkrav k
         WHERE k.tenant = p_tenant
         ORDER BY k.kravversjon DESC LIMIT 1)
    SELECT (SELECT count(*) FROM public.kolli k WHERE k.tenant = p_tenant),
           (SELECT count(*) FROM public.kolli k
             WHERE k.tenant = p_tenant AND k.fareklasse <> 'ingen'),
           (SELECT count(*) FROM public.transportforslag f
             WHERE f.tenant = p_tenant AND f.status = 'apen'),
           (SELECT count(*) FROM public.transportforslag f
             WHERE f.tenant = p_tenant AND f.status = 'forkastet'),
           (SELECT count(DISTINCT f.mottakerland)
              FROM public.transportforslag f WHERE f.tenant = p_tenant),
           0::BIGINT,
           (SELECT count(*) FROM public.transportfunn f
             WHERE f.tenant = p_tenant AND f.apen),
           EXISTS (SELECT 1 FROM kr),
           (SELECT kr.avsenderland::TEXT FROM kr),
           (SELECT kr.maks_kolli_gram FROM kr),
           (SELECT kr.manuell_kontroll_over_gram FROM kr),
           (SELECT kr.forslagsfrist_dogn FROM kr),
           (SELECT kr.kravversjon FROM kr)
$$;
REVOKE ALL ON FUNCTION m28_bildet(TEXT) FROM PUBLIC;

-- =====================================================================
-- `m28_sveip_transport` — KRYSS-TENANT, ÉN TENANT OM GANGEN.
--
-- 130s LÆRDOM: under FORCE RLS ser en spørring UTEN `disponit.tenant`
-- NULL RADER, og en sveip som spurte på tvers ville rapportert null
-- funn MED GRØNN EXIT-KODE.
--
-- SVEIPEN BESTILLER INGENTING OG OMBOOKER INGENTING. Den sier fra om
-- at et forslag har stått åpent over fristen, om at et tungt kolli
-- ikke har fått en plan noen har sett på, om at et registrert kolli
-- aldri fikk et forslag, og om at en tenant har planer til et land som
-- ikke lenger har en landpakke.
-- =====================================================================
CREATE FUNCTION m28_sveip_transport(p_maks_tenanter INT DEFAULT 1000)
RETURNS TABLE (tenanter INT, nye INT, oppdaterte INT, lukket INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_t TEXT;
    v_antall INT := 0;
    v_nye INT := 0;
    v_oppdaterte INT := 0;
    v_lukket INT := 0;
    v_n INT; v_n2 INT; v_n3 INT;
BEGIN
    PERFORM set_config('disponit.tenant', '', true);
    FOR v_t IN
        SELECT DISTINCT k.tenant FROM public.transportkrav k
         ORDER BY 1 LIMIT greatest(1, coalesce(p_maks_tenanter, 1000))
    LOOP
        v_antall := v_antall + 1;
        PERFORM set_config('disponit.tenant', v_t, true);

        -- 1. ÅPENT FORSLAG OVER FRISTEN.
        --
        -- DEN GJELDENDE FRISTEN, ikke den lengste som noen gang sto.
        -- 137s lærdom: kravet er append-only, så `max()` her ville
        -- målt mot en frist som ikke gjelder.
        WITH krav AS (
            SELECT k.forslagsfrist_dogn AS frist
              FROM public.transportkrav k WHERE k.tenant = v_t
             ORDER BY k.kravversjon DESC LIMIT 1),
        treff AS (
            SELECT f.forslag_id, f.mottakerland, f.foreslatt_ts
              FROM public.transportforslag f, krav
             WHERE f.tenant = v_t AND f.status = 'apen'
               AND f.foreslatt_ts
                   < now() - make_interval(days => krav.frist)),
        satt AS (
            INSERT INTO public.transportfunn
                (tenant, funntype, referanse, detalj)
            SELECT v_t, 'apent_forslag_over_frist', t.forslag_id::text,
                   'planen til ' || t.mottakerland || ' har staatt'
                   || ' aapen siden ' || t.foreslatt_ts::date
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
                DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.transportfunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm28_sveip',
                   lukket_grunn = 'forslaget er forkastet eller fristen hevet'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'apent_forslag_over_frist'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.forslag_id::text = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 2. REGISTRERT KOLLI SOM ALDRI FIKK EN PLAN.
        --
        -- Et menneske har målt pakken og skrevet ned fareklassen. Da
        -- har noen ment at den skal et sted.
        WITH krav AS (
            SELECT k.forslagsfrist_dogn AS frist
              FROM public.transportkrav k WHERE k.tenant = v_t
             ORDER BY k.kravversjon DESC LIMIT 1),
        treff AS (
            SELECT k.kolli_id, k.referanse
              FROM public.kolli k, krav
             WHERE k.tenant = v_t
               AND k.registrert
                   < now() - make_interval(days => krav.frist)
               AND NOT EXISTS (SELECT 1 FROM public.transportforslag f
                                WHERE f.tenant = v_t
                                  AND f.kolli_id = k.kolli_id)),
        satt AS (
            INSERT INTO public.transportfunn
                (tenant, funntype, referanse, detalj)
            SELECT v_t, 'kolli_uten_forslag', t.kolli_id::text,
                   'kolliet «' || t.referanse || '» er maalt, men har'
                   || ' ingen plan'
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
                DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.transportfunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm28_sveip',
                   lukket_grunn = 'kolliet har faatt en plan'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'kolli_uten_forslag'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.kolli_id::text = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 3. TUNGT KOLLI MED EN PLAN INGEN HAR SETT PÅ.
        WITH krav AS (
            SELECT k.manuell_kontroll_over_gram AS grense,
                   k.forslagsfrist_dogn AS frist
              FROM public.transportkrav k WHERE k.tenant = v_t
             ORDER BY k.kravversjon DESC LIMIT 1),
        treff AS (
            SELECT f.forslag_id, k.referanse, k.vekt_gram
              FROM public.transportforslag f
              JOIN public.kolli k
                ON k.tenant = f.tenant AND k.kolli_id = f.kolli_id,
                   krav
             WHERE f.tenant = v_t AND f.status = 'apen'
               AND k.vekt_gram > krav.grense
               AND f.foreslatt_ts
                   < now() - make_interval(days => krav.frist)),
        satt AS (
            INSERT INTO public.transportfunn
                (tenant, funntype, referanse, detalj)
            SELECT v_t, 'tungt_kolli_ukontrollert', t.forslag_id::text,
                   'kolliet «' || t.referanse || '» paa ' || t.vekt_gram
                   || ' gram er over kontrollgrensen'
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
                DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.transportfunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm28_sveip',
                   lukket_grunn = 'planen er forkastet eller grensen hevet'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'tungt_kolli_ukontrollert'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.forslag_id::text = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 4. PLANER TIL ET LAND SOM IKKE LENGER HAR EN PAKKE.
        --
        -- Døra nektet ikke da — pakken gjaldt. Men neste plan til samme
        -- land vil stoppe, og for farlig gods er det ikke en
        -- formalitet: det betyr at ingen har lest reglene som gjelder
        -- nå.
        WITH treff AS (
            SELECT DISTINCT f.mottakerland
              FROM public.transportforslag f
             WHERE f.tenant = v_t
               AND NOT EXISTS (
                   SELECT 1 FROM public.landpakke l
                    WHERE l.landkode = f.mottakerland
                      AND l.gyldig_fra <= current_date
                      AND (l.gyldig_til IS NULL
                           OR l.gyldig_til >= current_date))),
        satt AS (
            INSERT INTO public.transportfunn
                (tenant, funntype, referanse, detalj)
            SELECT v_t, 'land_uten_pakke', t.mottakerland,
                   'det finnes planer til ' || t.mottakerland
                   || ', og ingen landpakke gjelder der i dag'
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
                DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.transportfunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm28_sveip',
                   lukket_grunn = 'landpakken gjelder igjen'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'land_uten_pakke'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.mottakerland = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
    RETURN QUERY SELECT v_antall, v_nye, v_oppdaterte, v_lukket;
END $$;

-- =====================================================================
-- RETTIGHETENE. SP-7: KJØRETIDEN NÅR DØRENE OG INGENTING ANNET.
-- =====================================================================
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT p.oid::regprocedure AS sig
               FROM pg_proc p
              WHERE p.pronamespace = 'public'::regnamespace
                AND p.proname LIKE 'm28\_%'
                AND pg_get_userbyid(p.proowner) = current_user
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', r.sig);
    END LOOP;
END $$;

GRANT EXECUTE ON FUNCTION m28_sett_krav(TEXT, TEXT, BIGINT, BIGINT, INT,
    TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m28_registrer_kolli(TEXT, UUID, TEXT, BIGINT,
    INT, INT, INT, TEXT, TEXT, INT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m28_foresla(TEXT, UUID, UUID, INT, UUID, TEXT,
    TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m28_forkast(TEXT, UUID, TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m28_lukk_funn(TEXT, UUID, TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m28_kolliene(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m28_forslagene(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m28_transportfunn(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m28_bildet(TEXT) TO disponit;

-- SVEIPEROLLEN NÅR ÉN FUNKSJON, OG BARE DEN (111s form).
GRANT EXECUTE ON FUNCTION m28_sveip_transport(INT)
    TO disponit_transportsveip;

RESET ROLE;

-- =====================================================================
-- M-36s FUNNKATALOG (132).
-- =====================================================================
INSERT INTO m36_funnregister
    (relasjon, modul, typekolonne, apenform, begrunnelse)
VALUES
    ('transportfunn', 'm28_transport', 'funntype', 'apen_kolonne',
     'husets form')
ON CONFLICT (relasjon) DO NOTHING;
GRANT SELECT ON transportfunn TO disponit_optimalisator_eier;

-- =====================================================================
-- M-4s RETENSJONSREGISTER (093). 137/138s form, arvet.
--
-- `uten_frist_apen` fordi lagrene er KJENT og fristen ikke er bestemt.
-- En `under_frist` uten reaper er urepresenterbar, og med rette.
-- =====================================================================
SET LOCAL ROLE disponit_lager_eier;
INSERT INTO retensjonslager
    (lager_id, relasjon, klasse, tenantkolonne, alderskolonne,
     reapetkolonne, fristkilde, frist_dogn, reaper, dom,
     dom_begrunnelse, dom_migrasjon)
VALUES
    ('m28_kolli', 'kolli', 'driftsspor', 'tenant', 'registrert',
     NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'Kolliet baerer maalene et menneske tok og fareklassen det oppga.'
     ' Slettes det, forsvinner ogsaa hvem som sa at pakken var trygg.',
     '139'),
    ('m28_transportforslag', 'transportforslag', 'driftsspor', 'tenant',
     'foreslatt_ts', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'Planen er det modulen faktisk mente. Uten den kan ingen i'
     ' ettertid se hva den foreslo for et farlig kolli.', '139'),
    ('m28_transportkrav', 'transportkrav', 'konfigurasjon', 'tenant',
     'versjon_ts', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'Kravversjonene er referert av forslag og kan ikke slettes'
     ' uavhengig av dem.', '139'),
    ('m28_transportfunn', 'transportfunn', 'driftsspor', 'tenant',
     'forst_sett', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'Funnene er modulens egen maaling av seg selv. Reaperen finnes'
     ' ikke i v1.', '139')
ON CONFLICT (lager_id) DO NOTHING;
RESET ROLE;
