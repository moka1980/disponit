-- =====================================================================
-- M-32 GLOBAL LOKALISERINGS- OG SKATTEAGENT (v1) — KLYNGE 10s ANDRE.
-- =====================================================================
--
-- V1-DOMMEN: MODULEN INNBERETTER INGENTING. Den svarer på «hvilken
-- jurisdiksjon, hvilken sats, hvilken regelversjon» — og et ubesvart
-- spørsmål er et FUNN, ikke en gjetning.
--
-- ---------------------------------------------------------------------
-- KLYNGENS DELTE DOM:
--
--   EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN ANGRES IKKE AV EN
--   ROLLBACK.
--
-- En innberettet mva-oppgave er hos skattemyndigheten. En rollback her
-- gjør den ikke usendt; den gjør bare at vi ikke lenger vet hva vi
-- sendte.
--
-- ---------------------------------------------------------------------
-- «LANDLANSERING ER DEAKTIVERT UTEN KOMPLETT OG TESTET LANDPAKKE» ER
-- IKKE EN SJEKK. DET ER EN TILSTAND.
--
-- Et land uten komplett pakke HAR INGEN RAD i `landpakke`. Uten rad
-- finnes ingen sats, og uten sats stopper beregningen. Vaktsetningens
-- andre halvdel — «usikker jurisdiksjon stopper transaksjonen» — er
-- den samme setningen sett fra andre siden.
--
-- En `komplett BOOLEAN`-kolonne ville vært det motsatte: en påstand
-- noen kunne satt til `true` uten at noe ble komplett av det.
--
-- ---------------------------------------------------------------------
-- LANDREGISTERET ER GLOBALT OG TENANTLØST, OG INGEN DØR SKRIVER I DET.
--
-- Det er M-31s plattformregisterform, arvet gjennom `retensjonslager`
-- (093), `kvalitetsregel` (092) og `m36_funnregister` (132): DOMMENE
-- FELLES I GIT, IKKE GJENNOM EN DØR.
--
-- `landpakke_endret_gjennom_dor` kan derfor aldri reises: det finnes
-- ingen dør som skriver, og `disponit_skatt_eier` har SELECT og
-- INGENTING ANNET på begge de globale tabellene. Porten måler det mot
-- `has_table_privilege`, ikke mot prosaen her.
--
-- HVORFOR SÅ STRENGT? En skattesats er en REGEL, ikke data. Kunne en
-- tenant endret satsen for et land gjennom en dør, ville den regelen
-- ikke lenger vært landets — den ville vært vår, og vi ville ikke
-- visst når den sluttet å stemme.
--
-- ---------------------------------------------------------------------
-- MVASATS FINNES, OG DEN ER TENANTENS EGEN. DEN RØRES IKKE.
--
-- `mvasats` (M-14, 106) har kolonnene `tenant, sats_kode, promille,
-- gyldig_fra, gyldig_til`. Et fundament som stoppet ved navnet ville
-- tildelt den til M-32 og kalt landpakken bygget.
--
-- `tenant`-kolonnen sier noe annet: det er satsen KUNDEN har
-- registrert for seg selv, i sitt eget land. Én bedrifts oppfatning av
-- sin egen mva er ikke en landpakke. Tredje gang samme felle i to
-- klynger: riktig navn, feil spørsmål.
--
-- ---------------------------------------------------------------------
-- JURISDIKSJONEN HVILER PÅ ADRESSEN SOM GJALDT, IKKE PÅ DAGENS.
--
-- `adresseversjon` (M-19, 111) er VERSJONERT med `gjelder_fra`, og det
-- er nettopp derfor den kan bære dette. En jurisdiksjon regnet ut fra
-- dagens adresse for fjorårets transaksjon er feil på nøyaktig den
-- måten klynge 7s dom advarer mot: EN FORELDET REGEL SER NØYAKTIG UT
-- SOM EN RIKTIG REGEL.
--
-- OG ARVEN ER EN KOLONNEGRANT, IKKE EN TABELLGRANT (093s form).
-- M-32 får `tenant`, `versjon_id`, `subjekt_id`, `land` og
-- `gjelder_fra` — aldri gate, postnummer eller poststed. AT
-- SKATTEMODULEN ALDRI LESER EN ADRESSE SKAL VÆRE EN EGENSKAP VED
-- BASEN, IKKE VED DISIPLINEN.
--
-- ---------------------------------------------------------------------
-- FIRE FUNN SOM ALDRI KAN REISES, OG DET ER BEVISET.
--
--   `transaksjon_uten_jurisdiksjon` — `jurisdiksjon` NOT NULL.
--   `sats_uten_regelversjon`        — `regelversjon` NOT NULL, med
--                                     fremmednøkkel til landpakken.
--   `sats_uten_komplett_landpakke`  — `landsats` har fremmednøkkel til
--                                     `landpakke`; uten pakke ingen
--                                     sats, og uten sats ingen
--                                     beregning.
--   `landpakke_endret_gjennom_dor`  — ingen dør skriver, og eieren har
--                                     bare SELECT.
--
-- Alle fire står i funntypesettet OG er umulige. Et sett som ikke
-- navnga dem ville ikke sagt noe; et sett som navnga dem og kunne
-- fylles ville sagt at vernet er en sveip.
--
-- ---------------------------------------------------------------------
-- GRENSEN MOT M-14 OG M-47.
--
-- M-14 eier BILAGET — kontrollen av en inngående faktura mot bestilling
-- og mottak. M-47 eier INNSENDINGEN til en myndighet. M-32 eier
-- SPØRSMÅLET «hvilken regel gjaldt for denne transaksjonen, i hvilket
-- land, i hvilken versjon». En modul som utvidet M-14s `mvasats` til å
-- bære landregler ville gjort én bedrifts sats til alles i stillhet.
-- =====================================================================

-- MODULROLLEN MÅ KUNNE EIE NOE FØR DEN KAN EIE DØRENE.
GRANT USAGE, CREATE ON SCHEMA public TO disponit_skatt_eier;
GRANT INSERT ON revisjonslogg TO disponit_skatt_eier;

-- HUSETS TENANTVAKT (038). Granten gis av EIEREN, og eieren er
-- `disponit_m37_claimer` — ikke migrator.
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_skatt_eier;
RESET ROLE;

-- ---------------------------------------------------------------------
-- ARVEN FRA 111: KOLONNEGRANT PÅ ADRESSEN.
--
-- `adressesubjekt` trengs for å vite AT subjektet finnes;
-- `adresseversjon` for landet og datoen det gjaldt fra. Ingen av
-- granten slipper gjennom en gate eller et postnummer.
--
-- Tabellene eies av migrator, så granten trenger ingen `SET LOCAL
-- ROLE`.
-- ---------------------------------------------------------------------
GRANT SELECT (tenant, subjekt_id) ON adressesubjekt TO disponit_skatt_eier;
GRANT SELECT (tenant, versjon_id, subjekt_id, land, gjelder_fra)
    ON adresseversjon TO disponit_skatt_eier;

-- =====================================================================
-- `landpakke` — GLOBALT OG TENANTLØST. INGEN DØR SKRIVER HER.
--
-- ET LAND UTEN KOMPLETT PAKKE HAR INGEN RAD. Det er hele
-- vaktsetningen, som en tilstand framfor en sjekk.
--
-- Raden bæres av et MENNESKE: `signert_av` er NOT NULL. En landpakke
-- ingen har satt navnet sitt på er ikke godkjent — den er bare skrevet.
-- Og `dom_migrasjon` sier hvilken migrasjon som felte dommen, som
-- `retensjonslager.dom_migrasjon` (093).
-- =====================================================================
CREATE TABLE landpakke (
    -- ISO 3166-1 alpha-2, og formen er håndhevet: et «land» som ikke
    -- er en landkode er en skrivefeil vi ikke skal regne skatt på.
    landkode CHAR(2) NOT NULL CHECK (landkode ~ '^[A-Z]{2}$'),
    -- REGELVERSJONEN ER LANDETS, IKKE VÅR. Den øker når landet endrer
    -- reglene, og gamle versjoner blir stående: en transaksjon fra i
    -- fjor skal fortsatt kunne forklares.
    regelversjon INT NOT NULL CHECK (regelversjon >= 1),
    valuta CHAR(3) NOT NULL CHECK (valuta ~ '^[A-Z]{3}$'),
    -- Hvor mange desimaler valutaen har. JPY har null, de fleste har
    -- to, og et par har tre. Feil tall her er feil avrunding på hver
    -- eneste linje.
    desimaler INT NOT NULL CHECK (desimaler BETWEEN 0 AND 3),
    avrundingsregel TEXT NOT NULL
        CONSTRAINT landpakke_avrunding_lukket
        CHECK (avrundingsregel IN ('halv_opp', 'halv_ned', 'mot_null')),
    dokumentformat TEXT NOT NULL
        CHECK (dokumentformat ~ '[^[:space:]]'),
    gyldig_fra DATE NOT NULL,
    gyldig_til DATE,
    CONSTRAINT landpakke_datoene_gaar_riktig_vei
        CHECK (gyldig_til IS NULL OR gyldig_til >= gyldig_fra),
    -- ET MENNESKE HAR SETT PÅ DEN.
    signert_av TEXT NOT NULL CHECK (signert_av ~ '[^[:space:]]'),
    signert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    dom_migrasjon TEXT NOT NULL CHECK (dom_migrasjon ~ '[^[:space:]]'),
    CONSTRAINT landpakke_pk PRIMARY KEY (landkode, regelversjon)
);

-- =====================================================================
-- `landsats` — OGSÅ GLOBAL, OG UTEN PAKKE FINNES DEN IKKE.
--
-- Fremmednøkkelen til `landpakke` er det som gjør
-- `sats_uten_komplett_landpakke` UMULIG: en sats uten pakke lar seg
-- ikke skrive, verken av en dør eller av en migrasjon.
-- =====================================================================
CREATE TABLE landsats (
    landkode CHAR(2) NOT NULL,
    regelversjon INT NOT NULL,
    -- `standard`, `redusert`, `null` osv. Fri tekst med form: settet
    -- av satskoder er LANDETS, ikke vårt, og et lukket sett her ville
    -- vært huset som bestemte hvilke satser verden har lov til å ha.
    satskode TEXT NOT NULL CHECK (satskode ~ '^[a-z_]{2,40}$'),
    -- Promille, ikke prosent: 25 % er 250. Heltall, fordi flyttall og
    -- skatt ikke hører sammen.
    promille INT NOT NULL CHECK (promille BETWEEN 0 AND 1000),
    begrunnelse TEXT NOT NULL CHECK (begrunnelse ~ '[^[:space:]]'),
    -- ET LAGER SOM IKKE KAN DATERES KAN IKKE MÅLES.
    -- `retensjonslager.alderskolonne` er NOT NULL, og 093 mener det:
    -- en tabell uten et tidspunkt kan ingen si noe om alderen på.
    -- Kolonnen sto ikke her i første utgave, og basen sa fra — samme
    -- rettelse som `playbooksteg` fikk i 137, samme natt.
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT landsats_pk PRIMARY KEY (landkode, regelversjon, satskode),
    CONSTRAINT landsats_pakke_fk
        FOREIGN KEY (landkode, regelversjon)
        REFERENCES landpakke (landkode, regelversjon)
);

-- ---------------------------------------------------------------------
-- `skattekrav` — TENANTENS GRENSER, APPEND-ONLY.
--
-- VERSJONEN TILDELES AV DØRA OG RADEN OPPDATERES ALDRI — 137s lærdom,
-- anvendt fra første linje her. `jurisdiksjonsvurdering.kravversjon`
-- peker hit, og «terskelen som gjaldt» må kunne slås opp i ettertid.
-- ---------------------------------------------------------------------
CREATE TABLE skattekrav (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kravversjon INT NOT NULL CHECK (kravversjon >= 1),
    -- Selgerens eget land. Uten det kan ingen si om en transaksjon er
    -- innenlands eller over en grense — og det er hele skillet.
    selgerland CHAR(2) NOT NULL CHECK (selgerland ~ '^[A-Z]{2}$'),
    -- Over dette beløpet skal et menneske se på beregningen. Tallet er
    -- TENANTENS: hva som er stort nok til å kontrolleres er en
    -- forretningsvurdering, ikke husets.
    manuell_kontroll_over_ore BIGINT NOT NULL
        CHECK (manuell_kontroll_over_ore >= 0),
    -- Hvor mange døgn en vurdering kan stå uten at noen har sett på
    -- den, når den er over beløpsgrensen.
    kontrollfrist_dogn INT NOT NULL
        CHECK (kontrollfrist_dogn BETWEEN 1 AND 365),
    versjon_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    satt_av TEXT NOT NULL CHECK (satt_av ~ '[^[:space:]]'),
    CONSTRAINT skattekrav_pk PRIMARY KEY (tenant, kravversjon)
);

-- ---------------------------------------------------------------------
-- `jurisdiksjonsvurdering` — MODULENS PRODUKT.
--
-- FIRE KOLONNER BÆRER TRE AV KLYNGENS FIRE UMULIGE FUNN:
--
--   `jurisdiksjon`  NOT NULL  → `transaksjon_uten_jurisdiksjon`
--   `regelversjon`  NOT NULL  → `sats_uten_regelversjon`, og
--                               fremmednøkkelen gjør at pakken finnes
--   `landkode`      NOT NULL  → sammen med versjonen: én ekte pakke
--
-- OG DEN ER APPEND-ONLY. Akseptansekravet sier «regelversjon lagres
-- per transaksjon»; kunne raden endres, ville det som ble lagret vært
-- det som gjelder NÅ, og oppslaget ville sett like riktig ut.
--
-- `adresseversjon_id` er en HENVISNING og ingen fremmednøkkel — samme
-- retning på autoriteten som 099s vakt mot `retensjonslager`. M-32 skal
-- ikke bli en oppbevaringsplikt for M-19s rader.
-- ---------------------------------------------------------------------
CREATE TABLE jurisdiksjonsvurdering (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    vurdering_id UUID NOT NULL,
    -- Tenantens egen referanse til transaksjonen. FRI TEKST og ingen
    -- fremmednøkkel: skattehistorikken skal kunne stå alene.
    transaksjonsref TEXT NOT NULL
        CHECK (transaksjonsref ~ '[^[:space:]]'),
    kravversjon INT NOT NULL,
    -- JURISDIKSJONEN. Landet hvis regler gjaldt for transaksjonen.
    jurisdiksjon CHAR(2) NOT NULL CHECK (jurisdiksjon ~ '^[A-Z]{2}$'),
    -- …OG DE TO LANDENE DEN BLE UTLEDET AV. Uten dem er
    -- jurisdiksjonen en påstand ingen kan etterprøve.
    kjoperland CHAR(2) NOT NULL CHECK (kjoperland ~ '^[A-Z]{2}$'),
    selgerland CHAR(2) NOT NULL CHECK (selgerland ~ '^[A-Z]{2}$'),
    -- ADRESSEVERSJONEN LANDET BLE LEST FRA. En jurisdiksjon regnet ut
    -- fra dagens adresse for fjorårets transaksjon er feil på nøyaktig
    -- den måten klynge 7s dom advarer mot.
    adresseversjon_id UUID NOT NULL,
    regelversjon INT NOT NULL,
    satskode TEXT NOT NULL,
    promille INT NOT NULL CHECK (promille BETWEEN 0 AND 1000),
    -- BELØPENE, BEGGE LAGRET. Skatten er REGNET og ikke oppgitt, og
    -- den lagres fordi avrundingsregelen kan endres med en ny
    -- regelversjon — og gårsdagens beløp skal ikke skifte da.
    belop_ore BIGINT NOT NULL CHECK (belop_ore >= 0),
    skatt_ore BIGINT NOT NULL CHECK (skatt_ore >= 0),
    -- Transaksjonens dato, ikke beregningens: satsen som gjaldt DA.
    transaksjonsdato DATE NOT NULL,
    beregnet_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    beregnet_av TEXT NOT NULL CHECK (beregnet_av ~ '[^[:space:]]'),
    CONSTRAINT jurisdiksjonsvurdering_pk PRIMARY KEY (tenant, vurdering_id),
    -- SAMME TRANSAKSJON VURDERES ÉN GANG. To vurderinger av samme
    -- transaksjon er to svar på ett spørsmål, og den som leser
    -- regnskapet vet ikke hvilket som gjaldt.
    CONSTRAINT jurisdiksjonsvurdering_en_per_transaksjon
        UNIQUE (tenant, transaksjonsref),
    CONSTRAINT jurisdiksjonsvurdering_krav_fk
        FOREIGN KEY (tenant, kravversjon)
        REFERENCES skattekrav (tenant, kravversjon),
    -- DEN VIKTIGSTE FREMMEDNØKKELEN I FILA: satsen som ble brukt må
    -- finnes, i den versjonen som ble brukt, for det landet.
    CONSTRAINT jurisdiksjonsvurdering_sats_fk
        FOREIGN KEY (jurisdiksjon, regelversjon, satskode)
        REFERENCES landsats (landkode, regelversjon, satskode)
);
CREATE INDEX jurisdiksjonsvurdering_pr_dato
    ON jurisdiksjonsvurdering (tenant, transaksjonsdato);

-- ---------------------------------------------------------------------
-- `skattefunn` — HUSETS FORM, MED ETT LUKKET SETT.
-- ---------------------------------------------------------------------
CREATE TABLE skattefunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    funn_id UUID NOT NULL DEFAULT gen_random_uuid(),
    funntype TEXT NOT NULL
        CONSTRAINT skattefunn_funntype_lukket
        CHECK (funntype IN (
            -- DE FIRE SOM ALDRI KAN REISES. At de står her OG er
            -- umulige er hele beviset.
            'transaksjon_uten_jurisdiksjon',
            'sats_uten_regelversjon',
            'sats_uten_komplett_landpakke',
            'landpakke_endret_gjennom_dor',
            -- DE SOM FAKTISK KAN REISES.
            'stor_vurdering_ukontrollert',
            'landpakke_utloper_snart',
            'landpakke_uten_sats',
            'jurisdiksjon_uten_pakke',
            'krav_mangler')),
    referanse TEXT NOT NULL CHECK (referanse ~ '[^[:space:]]'),
    detalj TEXT NOT NULL CHECK (detalj ~ '[^[:space:]]'),
    apen BOOLEAN NOT NULL DEFAULT true,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT,
    lukket_grunn TEXT,
    CONSTRAINT skattefunn_lukkingen_er_hel CHECK (
        apen = (lukket_ts IS NULL AND lukket_av IS NULL
                AND lukket_grunn IS NULL)),
    CONSTRAINT skattefunn_pk PRIMARY KEY (tenant, funn_id)
);
CREATE UNIQUE INDEX skattefunn_ett_apent
    ON skattefunn (tenant, funntype, referanse) WHERE apen;

-- =====================================================================
-- RADVAKT OG RETTIGHETER.
--
-- TO ARTER TABELLER, TO REGIMER:
--
--   `landpakke` og `landsats` er GLOBALE. Ingen RLS — det er ikke
--   tenantdata, det er verdens regler. Eieren får SELECT og
--   INGENTING ANNET: `landpakke_endret_gjennom_dor` er umulig fordi
--   rettigheten ikke finnes, ikke fordi ingen dør bruker den.
--
--   De tre andre er TENANTENS, med FORCE RLS.
-- =====================================================================
GRANT SELECT ON landpakke TO disponit_skatt_eier;
GRANT SELECT ON landsats TO disponit_skatt_eier;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['skattekrav', 'jurisdiksjonsvurdering',
                             'skattefunn']
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format($f$CREATE POLICY tenant_isolasjon ON public.%I
            USING (tenant = current_setting('disponit.tenant', true))
            WITH CHECK (tenant = current_setting('disponit.tenant', true))$f$, t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO'
                       ' disponit_skatt_eier', t);
    END LOOP;
END $$;

-- APPEND-ONLY MÅLT SOM EN RETTIGHET OG IKKE BARE SOM EN TRIGGER.
--
-- Akseptansekravet sier «regelversjon lagres per transaksjon». Kunne
-- raden endres, ville det som ER lagret vært det som gjelder NÅ — og
-- oppslaget ville sett like riktig ut.
--
-- Og kravet: en beløpsgrense som kunne endres etter at en vurdering
-- pekte på den, ville gjort «grensen som gjaldt» til «grensen som
-- gjelder nå» (137s lærdom).
REVOKE UPDATE ON public.jurisdiksjonsvurdering FROM disponit_skatt_eier;
REVOKE UPDATE ON public.skattekrav FROM disponit_skatt_eier;

-- SVEIPENS KRYSS-TENANT-POLICY (130s LÆRDOM).
--
-- En sveip uten `disponit.tenant` ville sett NULL RADER under FORCE
-- RLS og rapportert null funn — MED GRØNN EXIT-KODE.
CREATE POLICY m32_sveip_tenantliste ON skattekrav
    FOR SELECT
    USING (current_setting('disponit.tenant', true) IS NULL
           OR current_setting('disponit.tenant', true) = '');

-- =====================================================================
-- LANDPAKKENE. FELT I GIT, IKKE GJENNOM EN DØR.
--
-- TRE LAND, OG DE ER VALGT ÆRLIG: Norge fordi plattformen driver her,
-- Sverige og Danmark fordi de er de nærmeste markedene. ET FJERDE LAND
-- LEGGES TIL AV EN MIGRASJON, av et menneske som har lest reglene —
-- ikke av en kunde som trenger det i dag.
--
-- SATSENE ER OFFENTLIGE OG ETTERPRØVBARE. `begrunnelse` peker på
-- hjemmelen, slik at den som lurer kan slå opp framfor å stole på oss.
-- =====================================================================
INSERT INTO landpakke
    (landkode, regelversjon, valuta, desimaler, avrundingsregel,
     dokumentformat, gyldig_fra, gyldig_til, signert_av, dom_migrasjon)
VALUES
    ('NO', 1, 'NOK', 2, 'halv_opp', 'EHF 3.0', '2024-01-01', NULL,
     'plattform:138', '138'),
    ('SE', 1, 'SEK', 2, 'halv_opp', 'Peppol BIS 3.0', '2024-01-01', NULL,
     'plattform:138', '138'),
    ('DK', 1, 'DKK', 2, 'halv_opp', 'OIOUBL 2.1', '2024-01-01', NULL,
     'plattform:138', '138');

INSERT INTO landsats
    (landkode, regelversjon, satskode, promille, begrunnelse)
VALUES
    ('NO', 1, 'standard', 250,
     'Merverdiavgiftsloven kap. 5 — alminnelig sats 25 %.'),
    ('NO', 1, 'redusert', 150,
     'Merverdiavgiftsloven kap. 5 — naeringsmidler 15 %.'),
    ('NO', 1, 'lav', 120,
     'Merverdiavgiftsloven kap. 5 — persontransport og overnatting 12 %.'),
    ('NO', 1, 'nullsats', 0,
     'Merverdiavgiftsloven kap. 6 — fritatt omsetning, 0 %.'),
    ('SE', 1, 'standard', 250, 'Mervaerdesskattelagen — normalskattesats 25 %.'),
    ('SE', 1, 'redusert', 120, 'Mervaerdesskattelagen — livsmedel 12 %.'),
    ('SE', 1, 'lav', 60, 'Mervaerdesskattelagen — boecker og persontransport 6 %.'),
    ('SE', 1, 'nullsats', 0, 'Mervaerdesskattelagen — undantagen omsaettning.'),
    ('DK', 1, 'standard', 250, 'Momsloven — normalsats 25 %.'),
    ('DK', 1, 'nullsats', 0, 'Momsloven — fritagen levering.');

-- =====================================================================
-- HERFRA EIES DØRENE AV SKATTEEIEREN.
--
-- SP-7: kjøretiden får EXECUTE på dørene og INGEN tabellrettigheter.
-- =====================================================================
SET LOCAL ROLE disponit_skatt_eier;

-- `m32_evidens` — HUSETS SPOR.
CREATE FUNCTION m32_evidens(p_tenant TEXT, p_ref UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm32_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm32_skatt', 'handling', p_handling,
        'ref', p_ref::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm32_skatt',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:skatt', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;

-- `m32_pakke_gjelder` — STABLE og ikke IMMUTABLE (125s lærdom).
CREATE FUNCTION m32_pakke_gjelder(p_fra DATE, p_til DATE, p_dato DATE)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT p_fra <= p_dato AND (p_til IS NULL OR p_til >= p_dato)
$$;
REVOKE ALL ON FUNCTION m32_pakke_gjelder(DATE, DATE, DATE) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- `m32_avrund` — AVRUNDINGEN ER LANDETS, IKKE VÅR.
--
-- Akseptansekravet sier «avrunding og valuta avstemmes». Regelen ligger
-- i landpakken; her er den bare anvendt, deterministisk og i heltall.
--
-- HELTALL HELE VEIEN. `numeric` og ikke `float`: flyttall og skatt
-- hører ikke sammen, og et øre som forsvinner i en binærbrøk er et øre
-- noen må forklare et tilsyn.
-- ---------------------------------------------------------------------
CREATE FUNCTION m32_avrund(p_belop_ore BIGINT, p_promille INT,
                           p_regel TEXT)
RETURNS BIGINT LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT CASE p_regel
        WHEN 'halv_opp' THEN
            floor((p_belop_ore::numeric * p_promille) / 1000 + 0.5)::BIGINT
        WHEN 'halv_ned' THEN
            ceil((p_belop_ore::numeric * p_promille) / 1000 - 0.5)::BIGINT
        WHEN 'mot_null' THEN
            trunc((p_belop_ore::numeric * p_promille) / 1000)::BIGINT
    END
$$;
REVOKE ALL ON FUNCTION m32_avrund(BIGINT, INT, TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- `m32_funn_er_sveipens` — HVEM SOM KAN LUKKE HVA.
-- ---------------------------------------------------------------------
CREATE FUNCTION m32_funn_er_sveipens(p_funntype TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT p_funntype IN ('landpakke_utloper_snart',
                          'landpakke_uten_sats',
                          'jurisdiksjon_uten_pakke',
                          'krav_mangler')
$$;
REVOKE ALL ON FUNCTION m32_funn_er_sveipens(TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- `m32_sett_krav` — TENANTENS GRENSER, APPEND-ONLY.
--
-- 137s form, arvet: versjonen tildeles av DØRA, og raden oppdateres
-- aldri. `jurisdiksjonsvurdering.kravversjon` peker hit.
-- ---------------------------------------------------------------------
CREATE FUNCTION m32_sett_krav(p_tenant TEXT, p_selgerland TEXT,
                              p_manuell_over_ore BIGINT,
                              p_kontrollfrist_dogn INT, p_av TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm32_sett_krav');
    -- SELGERLANDET MÅ HA EN PAKKE. Uten den kan ingen si hva som er
    -- innenlands, og «usikker jurisdiksjon» begynner allerede her.
    IF NOT EXISTS (SELECT 1 FROM public.landpakke l
                    WHERE l.landkode = p_selgerland) THEN
        RAISE EXCEPTION 'm32: ingen landpakke for selgerlandet %.'
                        ' En landpakke felles i en migrasjon, ikke her',
            p_selgerland;
    END IF;
    SELECT coalesce(max(kravversjon), 0) + 1 INTO v_versjon
      FROM public.skattekrav WHERE tenant = p_tenant;
    INSERT INTO public.skattekrav
        (tenant, kravversjon, selgerland, manuell_kontroll_over_ore,
         kontrollfrist_dogn, satt_av)
    VALUES (p_tenant, v_versjon, p_selgerland, p_manuell_over_ore,
            p_kontrollfrist_dogn, p_av);
    PERFORM public.m32_evidens(p_tenant, NULL, 'sett_krav', p_av,
                               jsonb_build_object('kravversjon', v_versjon,
                                                  'selgerland',
                                                  p_selgerland));
    RETURN v_versjon;
END $$;

-- =====================================================================
-- `m32_beregn` — MODULENS HOVEDDØR, OG DEN NEKTER FIRE GANGER.
--
-- «USIKKER JURISDIKSJON STOPPER TRANSAKSJONEN» ER IKKE EN ADVARSEL.
-- Døra returnerer ingen sats den ikke kan forsvare:
--
--   1. Adresseversjonen finnes ikke for tenanten.
--   2. Kjøperlandet har ingen landpakke som gjaldt PÅ
--      TRANSAKSJONSDATOEN.
--   3. Pakken har ingen sats med den koden.
--   4. Kravversjonen finnes ikke.
--
-- JURISDIKSJONEN ER UTLEDET, IKKE OPPGITT. Kalleren sier hvilken
-- adresseversjon som gjelder; landet leses DERFRA. En parameter for
-- jurisdiksjonen ville gjort hele modulen til en kalkulator som regner
-- på det den får beskjed om.
--
-- REGELEN v1 BRUKER, SAGT RETT UT: jurisdiksjonen er KJØPERENS land.
-- Det er riktig for fjernsalg til forbruker i EØS og feil for flere
-- andre tilfeller — og nettopp derfor lagres BEGGE landene, sammen med
-- adresseversjonen, slik at en senere regel kan regnes om og
-- etterprøves. En modul som bare lagret svaret ville gjort en
-- forenkling til en sannhet.
-- =====================================================================
CREATE FUNCTION m32_beregn(p_tenant TEXT, p_vurdering_id UUID,
                           p_transaksjonsref TEXT, p_kravversjon INT,
                           p_adresseversjon_id UUID, p_satskode TEXT,
                           p_belop_ore BIGINT, p_transaksjonsdato DATE,
                           p_av TEXT)
RETURNS TABLE (jurisdiksjon TEXT, regelversjon INT, promille INT,
               skatt_ore BIGINT, valuta TEXT, krever_kontroll BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_krav      public.skattekrav%ROWTYPE;
    v_kjoperland CHAR(2);
    v_pakke     public.landpakke%ROWTYPE;
    v_promille  INT;
    v_skatt     BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm32_beregn');

    -- INGEN `FOR UPDATE`. Kravet er append-only og eieren har ingen
    -- UPDATE — en lås mot en umulig endring måler ingenting, og ville
    -- dessuten KREVD den retten vi med vilje ikke har (137s lærdom).
    SELECT * INTO v_krav FROM public.skattekrav k
     WHERE k.tenant = p_tenant AND k.kravversjon = p_kravversjon;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm32: kravversjon % finnes ikke for %',
            p_kravversjon, p_tenant;
    END IF;

    -- 1. LANDET LESES FRA ADRESSEVERSJONEN, ikke fra en parameter.
    SELECT a.land INTO v_kjoperland FROM public.adresseversjon a
     WHERE a.tenant = p_tenant AND a.versjon_id = p_adresseversjon_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm32: adresseversjon % finnes ikke for %',
            p_adresseversjon_id, p_tenant;
    END IF;
    IF v_kjoperland IS NULL OR v_kjoperland !~ '^[A-Z]{2}$' THEN
        RAISE EXCEPTION 'm32: adresseversjon % har intet brukbart land'
                        ' — usikker jurisdiksjon stopper transaksjonen',
            p_adresseversjon_id;
    END IF;

    -- 2. PAKKEN SOM GJALDT PÅ TRANSAKSJONSDATOEN, ikke i dag.
    SELECT * INTO v_pakke FROM public.landpakke l
     WHERE l.landkode = v_kjoperland
       AND public.m32_pakke_gjelder(l.gyldig_fra, l.gyldig_til,
                                    p_transaksjonsdato)
     ORDER BY l.regelversjon DESC LIMIT 1;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm32: ingen landpakke for % som gjaldt % —'
                        ' usikker jurisdiksjon stopper transaksjonen',
            v_kjoperland, p_transaksjonsdato;
    END IF;

    -- 3. SATSEN. Uten den er pakken ikke komplett, og da finnes ingen
    -- sats å regne med.
    SELECT s.promille INTO v_promille FROM public.landsats s
     WHERE s.landkode = v_pakke.landkode
       AND s.regelversjon = v_pakke.regelversjon
       AND s.satskode = p_satskode;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm32: landpakke %/% har ingen sats «%»',
            v_pakke.landkode, v_pakke.regelversjon, p_satskode;
    END IF;

    v_skatt := public.m32_avrund(p_belop_ore, v_promille,
                                 v_pakke.avrundingsregel);

    INSERT INTO public.jurisdiksjonsvurdering
        (tenant, vurdering_id, transaksjonsref, kravversjon,
         jurisdiksjon, kjoperland, selgerland, adresseversjon_id,
         regelversjon, satskode, promille, belop_ore, skatt_ore,
         transaksjonsdato, beregnet_av)
    VALUES (p_tenant, p_vurdering_id, p_transaksjonsref, p_kravversjon,
            v_kjoperland, v_kjoperland, v_krav.selgerland,
            p_adresseversjon_id, v_pakke.regelversjon, p_satskode,
            v_promille, p_belop_ore, v_skatt, p_transaksjonsdato, p_av);

    PERFORM public.m32_evidens(p_tenant, p_vurdering_id, 'beregn', p_av,
        jsonb_build_object('jurisdiksjon', v_kjoperland,
                           'regelversjon', v_pakke.regelversjon,
                           'promille', v_promille,
                           'skatt_ore', v_skatt));
    RETURN QUERY SELECT v_kjoperland::TEXT, v_pakke.regelversjon,
                        v_promille, v_skatt, v_pakke.valuta::TEXT,
                        p_belop_ore > v_krav.manuell_kontroll_over_ore;
END $$;

-- `m32_lukk_funn` — OG SVEIPENS EGNE KAN INGEN LUKKE.
CREATE FUNCTION m32_lukk_funn(p_tenant TEXT, p_funn_id UUID,
                              p_grunn TEXT, p_av TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_type TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm32_lukk_funn');
    SELECT f.funntype INTO v_type FROM public.skattefunn f
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id AND f.apen;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm32: ingen aapent funn % for %',
            p_funn_id, p_tenant;
    END IF;
    IF public.m32_funn_er_sveipens(v_type) THEN
        RAISE EXCEPTION 'm32: % lukkes av sveipen naar tilstanden er'
                        ' borte, ikke av et menneske', v_type;
    END IF;
    UPDATE public.skattefunn f
       SET apen = false, lukket_ts = now(), lukket_av = p_av,
           lukket_grunn = p_grunn
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;
    PERFORM public.m32_evidens(p_tenant, p_funn_id, 'lukk_funn', p_av,
                               jsonb_build_object('funntype', v_type));
END $$;

-- =====================================================================
-- LESEDØRENE.
-- =====================================================================

-- `m32_landene` — HELE REGISTERET, LESBART FOR ALLE TENANTER.
--
-- Det er ikke tenantdata; det er verdens regler. En tenant som lurer
-- på hvorfor en beregning stoppet, skal kunne se at landet mangler en
-- pakke — framfor å måtte spørre oss.
CREATE FUNCTION m32_landene(p_dato DATE)
RETURNS TABLE (landkode TEXT, regelversjon INT, valuta TEXT,
               desimaler INT, avrundingsregel TEXT, dokumentformat TEXT,
               gyldig_fra DATE, gyldig_til DATE, gjelder BOOLEAN,
               satser BIGINT, signert_av TEXT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT l.landkode::TEXT, l.regelversjon, l.valuta::TEXT,
           l.desimaler, l.avrundingsregel, l.dokumentformat,
           l.gyldig_fra, l.gyldig_til,
           public.m32_pakke_gjelder(l.gyldig_fra, l.gyldig_til,
                                    coalesce(p_dato, current_date)),
           (SELECT count(*) FROM public.landsats s
             WHERE s.landkode = l.landkode
               AND s.regelversjon = l.regelversjon),
           l.signert_av
      FROM public.landpakke l
     ORDER BY l.landkode, l.regelversjon DESC
$$;
REVOKE ALL ON FUNCTION m32_landene(DATE) FROM PUBLIC;

CREATE FUNCTION m32_satsene(p_landkode TEXT, p_regelversjon INT)
RETURNS TABLE (satskode TEXT, promille INT, begrunnelse TEXT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT s.satskode, s.promille, s.begrunnelse
      FROM public.landsats s
     WHERE s.landkode = p_landkode AND s.regelversjon = p_regelversjon
     ORDER BY s.promille DESC
$$;
REVOKE ALL ON FUNCTION m32_satsene(TEXT, INT) FROM PUBLIC;

-- `m32_vurderingene` — MED REGELVERSJONEN, ALLTID.
--
-- En sats uten versjonen den kom fra er et tall ingen kan etterprøve.
CREATE FUNCTION m32_vurderingene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (vurdering_id UUID, transaksjonsref TEXT,
               jurisdiksjon TEXT, kjoperland TEXT, selgerland TEXT,
               regelversjon INT, satskode TEXT, promille INT,
               belop_ore BIGINT, skatt_ore BIGINT,
               transaksjonsdato DATE, over_kontrollgrense BOOLEAN,
               beregnet_ts TIMESTAMPTZ)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT v.vurdering_id, v.transaksjonsref, v.jurisdiksjon::TEXT,
           v.kjoperland::TEXT, v.selgerland::TEXT, v.regelversjon,
           v.satskode, v.promille, v.belop_ore, v.skatt_ore,
           v.transaksjonsdato,
           v.belop_ore > k.manuell_kontroll_over_ore,
           v.beregnet_ts
      FROM public.jurisdiksjonsvurdering v
      JOIN public.skattekrav k
        ON k.tenant = v.tenant AND k.kravversjon = v.kravversjon
     WHERE v.tenant = p_tenant
     ORDER BY v.beregnet_ts DESC
     LIMIT greatest(1, least(coalesce(p_maks, 100), 500))
$$;
REVOKE ALL ON FUNCTION m32_vurderingene(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m32_skattefunn(p_tenant TEXT, p_maks INT)
RETURNS TABLE (funn_id UUID, funntype TEXT, referanse TEXT,
               detalj TEXT, sveipens BOOLEAN, forst_sett TIMESTAMPTZ)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT f.funn_id, f.funntype, f.referanse, f.detalj,
           public.m32_funn_er_sveipens(f.funntype), f.forst_sett
      FROM public.skattefunn f
     WHERE f.tenant = p_tenant AND f.apen
     ORDER BY f.forst_sett DESC
     LIMIT greatest(1, least(coalesce(p_maks, 100), 500))
$$;
REVOKE ALL ON FUNCTION m32_skattefunn(TEXT, INT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- `m32_bildet` — MODULENS EGEN TILSTAND.
--
-- `innberetninger` er ALLTID 0, og den står her med vilje: tallet er
-- ikke en telling av en kolonne — det er en påstand om at kolonnen
-- ikke finnes. Blir den noen gang noe annet, er v1-dommen brutt av
-- noen som la til en tabell.
-- ---------------------------------------------------------------------
CREATE FUNCTION m32_bildet(p_tenant TEXT)
RETURNS TABLE (vurderinger BIGINT, land_i_bruk BIGINT,
               over_kontrollgrense BIGINT, skatt_ore BIGINT,
               innberetninger BIGINT, apne_funn BIGINT,
               har_krav BOOLEAN, selgerland TEXT,
               manuell_kontroll_over_ore BIGINT,
               kontrollfrist_dogn INT, kravversjon INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    WITH k AS (
        SELECT * FROM public.skattekrav s
         WHERE s.tenant = p_tenant
         ORDER BY s.kravversjon DESC LIMIT 1)
    SELECT (SELECT count(*) FROM public.jurisdiksjonsvurdering v
             WHERE v.tenant = p_tenant),
           (SELECT count(DISTINCT v.jurisdiksjon)
              FROM public.jurisdiksjonsvurdering v
             WHERE v.tenant = p_tenant),
           (SELECT count(*) FROM public.jurisdiksjonsvurdering v, k
             WHERE v.tenant = p_tenant
               AND v.belop_ore > k.manuell_kontroll_over_ore),
           (SELECT coalesce(sum(v.skatt_ore), 0)
              FROM public.jurisdiksjonsvurdering v
             WHERE v.tenant = p_tenant),
           0::BIGINT,
           (SELECT count(*) FROM public.skattefunn f
             WHERE f.tenant = p_tenant AND f.apen),
           EXISTS (SELECT 1 FROM k),
           (SELECT k.selgerland::TEXT FROM k),
           (SELECT k.manuell_kontroll_over_ore FROM k),
           (SELECT k.kontrollfrist_dogn FROM k),
           (SELECT k.kravversjon FROM k)
$$;
REVOKE ALL ON FUNCTION m32_bildet(TEXT) FROM PUBLIC;

-- =====================================================================
-- `m32_sveip_skatt` — KRYSS-TENANT, ÉN TENANT OM GANGEN.
--
-- 130s LÆRDOM: under FORCE RLS ser en spørring UTEN `disponit.tenant`
-- NULL RADER. En sveip som spurte på tvers ville rapportert null funn
-- MED GRØNN EXIT-KODE.
--
-- SVEIPEN INNBERETTER INGENTING OG RETTER INGEN BEREGNING. Den sier
-- fra om at en stor vurdering står ukontrollert, om at en landpakke
-- utløper snart, om at en pakke mangler satser, og om at en tenant
-- handler med et land huset ikke har en pakke for.
-- =====================================================================
CREATE FUNCTION m32_sveip_skatt(p_maks_tenanter INT DEFAULT 1000)
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
        SELECT DISTINCT k.tenant FROM public.skattekrav k
         ORDER BY 1 LIMIT greatest(1, coalesce(p_maks_tenanter, 1000))
    LOOP
        v_antall := v_antall + 1;
        PERFORM set_config('disponit.tenant', v_t, true);

        -- 1. STOR VURDERING SOM INGEN HAR SETT PÅ.
        --
        -- DEN GJELDENDE GRENSEN, IKKE DEN LAVESTE SOM NOEN GANG STO.
        -- 137s lærdom, arvet: kravet er append-only, så `min()` eller
        -- `max()` her ville målt mot en grense som ikke gjelder.
        WITH krav AS (
            SELECT k.manuell_kontroll_over_ore AS grense,
                   k.kontrollfrist_dogn AS frist
              FROM public.skattekrav k WHERE k.tenant = v_t
             ORDER BY k.kravversjon DESC LIMIT 1),
        treff AS (
            SELECT v.vurdering_id, v.transaksjonsref, v.belop_ore
              FROM public.jurisdiksjonsvurdering v, krav
             WHERE v.tenant = v_t
               AND v.belop_ore > krav.grense
               AND v.beregnet_ts
                   < now() - make_interval(days => krav.frist)),
        satt AS (
            INSERT INTO public.skattefunn
                (tenant, funntype, referanse, detalj)
            SELECT v_t, 'stor_vurdering_ukontrollert',
                   t.vurdering_id::text,
                   'transaksjon ' || t.transaksjonsref || ' paa '
                   || t.belop_ore || ' oere er over kontrollgrensen'
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
                DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.skattefunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm32_sveip',
                   lukket_grunn = 'grensen er hevet eller vurderingen borte'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'stor_vurdering_ukontrollert'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.vurdering_id::text = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 2. TENANTEN HANDLER MED ET LAND HUSET IKKE HAR PAKKE FOR.
        --
        -- Vurderingen selv kan ikke ha skjedd uten pakke — døra nekter.
        -- Men pakken kan ha UTLØPT etterpå, og da vil neste transaksjon
        -- til samme land stoppe. Det skal noen få vite FØR den gjør det.
        WITH treff AS (
            SELECT DISTINCT v.jurisdiksjon
              FROM public.jurisdiksjonsvurdering v
             WHERE v.tenant = v_t
               AND NOT EXISTS (
                   SELECT 1 FROM public.landpakke l
                    WHERE l.landkode = v.jurisdiksjon
                      AND public.m32_pakke_gjelder(l.gyldig_fra,
                                                   l.gyldig_til,
                                                   current_date))),
        satt AS (
            INSERT INTO public.skattefunn
                (tenant, funntype, referanse, detalj)
            SELECT v_t, 'jurisdiksjon_uten_pakke', t.jurisdiksjon,
                   'tenanten har handlet med ' || t.jurisdiksjon
                   || ', og ingen landpakke gjelder der i dag'
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
                DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.skattefunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm32_sveip',
                   lukket_grunn = 'landpakken gjelder igjen'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'jurisdiksjon_uten_pakke'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.jurisdiksjon = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 3. LANDPAKKE SOM UTLØPER INNEN 90 DØGN.
        --
        -- Bare for land tenanten FAKTISK handler med. En varsling om
        -- alle verdens pakker ville vært støy; en om ingen ville vært
        -- taushet.
        WITH treff AS (
            SELECT DISTINCT l.landkode, l.regelversjon, l.gyldig_til
              FROM public.landpakke l
             WHERE l.gyldig_til IS NOT NULL
               AND l.gyldig_til BETWEEN current_date
                                    AND current_date + 90
               AND EXISTS (SELECT 1 FROM public.jurisdiksjonsvurdering v
                            WHERE v.tenant = v_t
                              AND v.jurisdiksjon = l.landkode)),
        satt AS (
            INSERT INTO public.skattefunn
                (tenant, funntype, referanse, detalj)
            SELECT v_t, 'landpakke_utloper_snart',
                   t.landkode || '/' || t.regelversjon,
                   'landpakken for ' || t.landkode || ' utloeper '
                   || t.gyldig_til
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
                DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.skattefunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm32_sveip',
                   lukket_grunn = 'pakken er fornyet eller utloept'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'landpakke_utloper_snart'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.landkode || '/' || t.regelversjon
                                      = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 4. LANDPAKKE UTEN EN ENESTE SATS.
        --
        -- Døra nekter en beregning mot den, så den kan ikke skade en
        -- transaksjon. Men den ER en ufullstendig pakke som ser
        -- komplett ut i registeret — og det er nøyaktig den formen
        -- vaktsetningen finnes for.
        WITH treff AS (
            SELECT l.landkode, l.regelversjon
              FROM public.landpakke l
             WHERE NOT EXISTS (SELECT 1 FROM public.landsats s
                                WHERE s.landkode = l.landkode
                                  AND s.regelversjon = l.regelversjon)),
        satt AS (
            INSERT INTO public.skattefunn
                (tenant, funntype, referanse, detalj)
            SELECT v_t, 'landpakke_uten_sats',
                   t.landkode || '/' || t.regelversjon,
                   'landpakken for ' || t.landkode || ' versjon '
                   || t.regelversjon || ' har ingen satser'
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
                DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.skattefunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm32_sveip',
                   lukket_grunn = 'satsene er skrevet'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'landpakke_uten_sats'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.landkode || '/' || t.regelversjon
                                      = f.referanse)
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
                AND p.proname LIKE 'm32\_%'
                AND pg_get_userbyid(p.proowner) = current_user
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', r.sig);
    END LOOP;
END $$;

GRANT EXECUTE ON FUNCTION m32_sett_krav(TEXT, TEXT, BIGINT, INT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m32_beregn(TEXT, UUID, TEXT, INT, UUID, TEXT,
    BIGINT, DATE, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m32_lukk_funn(TEXT, UUID, TEXT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m32_landene(DATE) TO disponit;
GRANT EXECUTE ON FUNCTION m32_satsene(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m32_vurderingene(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m32_skattefunn(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m32_bildet(TEXT) TO disponit;

-- SVEIPEROLLEN NÅR ÉN FUNKSJON, OG BARE DEN (111s form).
GRANT EXECUTE ON FUNCTION m32_sveip_skatt(INT) TO disponit_skattesveip;

RESET ROLE;

-- =====================================================================
-- M-36s FUNNKATALOG (132).
-- =====================================================================
INSERT INTO m36_funnregister
    (relasjon, modul, typekolonne, apenform, begrunnelse)
VALUES
    ('skattefunn', 'm32_skatt', 'funntype', 'apen_kolonne', 'husets form')
ON CONFLICT (relasjon) DO NOTHING;
GRANT SELECT ON skattefunn TO disponit_optimalisator_eier;

-- =====================================================================
-- M-4s RETENSJONSREGISTER (093). 137s form, arvet.
--
-- `uten_frist_apen` fordi lagrene er KJENT og fristen ikke er bestemt.
-- En `under_frist` uten reaper er urepresenterbar — og det er riktig:
-- en dom som lover en frist ingen håndhever er verre enn ingen dom.
--
-- DE GLOBALE TABELLENE STÅR OGSÅ HER, med `konfigurasjon` som klasse.
-- De er tenantløse, og `tenantkolonne` er derfor NULL — 093 tillater
-- det, og en oppdiktet kolonne ville gjort registeret usant.
-- =====================================================================
SET LOCAL ROLE disponit_lager_eier;
INSERT INTO retensjonslager
    (lager_id, relasjon, klasse, tenantkolonne, alderskolonne,
     reapetkolonne, fristkilde, frist_dogn, reaper, dom,
     dom_begrunnelse, dom_migrasjon)
VALUES
    ('m32_jurisdiksjonsvurdering', 'jurisdiksjonsvurdering', 'evidens',
     'tenant', 'beregnet_ts', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'Vurderingen er dokumentasjon overfor et skattetilsyn. Fristen'
     ' foelger bokfoeringsloven og maa settes av noen som vet hvilket'
     ' land som gjelder.', '138'),
    ('m32_skattekrav', 'skattekrav', 'konfigurasjon', 'tenant',
     'versjon_ts', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'Kravversjonene er referert av vurderinger og kan ikke slettes'
     ' uavhengig av dem.', '138'),
    ('m32_skattefunn', 'skattefunn', 'driftsspor', 'tenant',
     'forst_sett', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'Funnene er modulens egen maaling av seg selv. Reaperen finnes'
     ' ikke i v1.', '138'),
    ('m32_landpakke', 'landpakke', 'konfigurasjon', NULL, 'signert_ts',
     NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'GLOBALT REGISTER, tenantloest. En landpakke slettes aldri: en'
     ' vurdering fra i fjor peker paa den, og en sats uten sin regel er'
     ' et tall ingen kan etterproeve.', '138'),
    ('m32_landsats', 'landsats', 'konfigurasjon', NULL, 'opprettet',
     NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'GLOBALT REGISTER, tenantloest. Satsen foelger pakken sin.', '138')
ON CONFLICT (lager_id) DO NOTHING;
RESET ROLE;
