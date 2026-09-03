-- 116: M-48 foretaks- og kredittvakt v1 — REGISTERET, OG ETT OPPSLAG.
-- Seks tenant-skopede tabeller, femten dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- KLYNGE 6s UNNTAK, OG HVORFOR DET ER ETT (eierbeslutning 3/9).
-- Klyngefundamentet skrev at ingen av de fem gjør en utgående
-- forespørsel. M-48 er unntaket, og snittet går INNE i modulen fordi
-- spesifikasjonen gir den TO eksterne kilder som ikke ligner hverandre:
--
--   FORETAKSREGISTERET er offentlig, krever ingen hemmeligheter, og
--   det vi sender ut er et ORGANISASJONSNUMMER — offentlige
--   foretaksdata, ikke persondata. Oppslaget er dessuten nødvendig i
--   doktrinens egen forstand: motpartens roller og registerstatus
--   finnes ikke andre steder. «Den unødvendige forespørselen ER
--   skaden» taler FOR dette oppslaget, ikke mot det.
--
--   KREDITTLEVERANDØREN er noe annet. Kommersiell, krever
--   hemmeligheter, sender de reelle rettighetshavernes NAVN til en
--   tredjepart — og gir tilbake en SCORE vi ville blitt fristet til å
--   handle på. Spesifikasjonens egen vakt, «oppslag logges som
--   behandling av persondata», handler om den. Den står bak porten
--   `modulen_hentet_kredittdata`, og v1 rører den ikke.
--
-- DOMMENE v1 HVILER PÅ, HÅNDHEVET I DATAMODELLEN:
--
--   1. HISTORIKKEN OVERSKRIVES ALDRI. Det finnes ingen kolonne som
--      holder «gjeldende motpartsprofil». Den gjeldende profilen ER
--      den siste versjonen, og hver versjon er FROSSET. M-42s dom
--      (110), gjentatt i 112, 113 og 114.
--
--   2. VURDERINGEN ER ET FORSLAG, OG DET FINNES INGEN KOLONNE FOR
--      «GJELDENDE KREDITTGRENSE». Spesifikasjonen sier at
--      kredittgrensen er INNGANG til fordringsagenten M-23, ikke
--      omvendt — og vakten sier «setter aldri kredittgrensen selv».
--      Hadde skjemaet hatt et felt for den aktive grensen, ville
--      fullmakten vært bygget allerede; da er det bare et spørsmål om
--      tid før noe skriver til den. Fraværet ER porten
--      `modulen_satte_kredittgrense`.
--
--   3. HVERT OPPSLAG HAR FORMÅL OG HJEMMEL, ELLER SKJER IKKE.
--      `foretaksoppslag` har to NOT NULL-kolonner som ingen dør har
--      standardverdi for. Et tilsyn spør ikke «slo dere opp?» — det
--      spør «med hvilken hjemmel». Porten er
--      `oppslag_uten_formaal_og_hjemmel`.
--
--   4. FERSKHETSVINDUET ER TENANTENS, OG BASEN HÅNDHEVER DET.
--      Doktrinen sier at den unødvendige forespørselen er skaden. Et
--      oppslag på et organisasjonsnummer vi alt har ferske data om er
--      per definisjon unødvendig — så `m48_registrer_oppslag` NEKTER
--      det, i basen, ikke i en klient som kan glemmes. Porten er
--      `oppslag_uten_ferskhetsvindu`.
--
--      Vinduet ligger i `motpartskrav` og ikke i koden: en tenant som
--      handler med byggebransjen vil ha kortere vindu enn en som
--      selger abonnement. Det er `kredittpolicy_hardkodet`.
--
--   5. HVER VURDERING BÆRER POLICYVERSJONEN SIN. En kredittvurdering
--      uten hvilken policy som gjaldt er ubrukelig i ettertid: man kan
--      ikke skille «policyen var slik» fra «noen regnet feil».
--      `vurdering_uten_policyversjon`.
--
--   6. BELØP I ØRE, HELTALL. BIGINT, ingen unntak (101s form).
--
-- GRENSEN MOT M-23: M-23 eier FORDRINGEN — hva som faktisk skylder
-- oss penger. M-48 eier MOTPARTEN og grunnlaget for hva vi tør gi den.
-- v1 kobler dem ikke; koblingen er nettopp grensen vi ikke setter.
--
-- GRENSEN MOT M-18: M-18 eier onboardingen av en kunde. M-48 eier
-- kredittvurderingen av den. En kunde kan være onboardet uten å ha
-- kreditt — to forskjellige beslutninger.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_motpart_eier') THEN
        RAISE EXCEPTION 'rollen disponit_motpart_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_motpart_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `motpartskrav` — ÉN per tenant. DOM 4 OG 5: POLICYEN ER TENANTENS.
CREATE TABLE motpartskrav (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- FERSKHETSVINDUET (dom 4). Hvor lenge et foretaksoppslag regnes
    -- som ferskt nok til at et NYTT oppslag på samme organisasjons-
    -- nummer er unødvendig — og derfor nektes.
    --
    -- 0 ER TILLATT OG BETYR «ALLTID FERSKT NOK Å SPØRRE PÅ NYTT».
    -- Det er ikke en bakdør: en tenant som setter 0 har tatt et valg
    -- som står i `motpartskrav` med navn og tidspunkt, og sveipen
    -- måler hvor mange oppslag det faktisk ble. Alternativet — et
    -- gulv i koden — ville flyttet valget dit ingen kan se det.
    oppslag_ferskhet_timer INT NOT NULL DEFAULT 24
        CHECK (oppslag_ferskhet_timer BETWEEN 0 AND 8760),
    -- Hvor lenge en vurdering regnes som gyldig. En kredittvurdering
    -- fra i fjor sier ingenting om hvem motparten er i dag.
    vurdering_gyldig_dogn INT NOT NULL DEFAULT 180
        CHECK (vurdering_gyldig_dogn BETWEEN 1 AND 3650),
    -- Hvor lenge en motpart kan stå UVURDERT før det er et funn.
    uvurdert_dogn INT NOT NULL DEFAULT 30
        CHECK (uvurdert_dogn BETWEEN 0 AND 3650),
    -- TAKET PÅ HVA MODULEN KAN FORESLÅ, i øre (dom 6). Ikke en
    -- kredittgrense — et tak på FORSLAGET. Skiller seg fra en grense
    -- ved at ingenting utenfor modulen leser det.
    maks_forslag_ore BIGINT NOT NULL DEFAULT 50000000
        CHECK (maks_forslag_ore BETWEEN 0 AND 100000000000),
    -- GRUNNLAGENE SOM TELLER, fra det lukkede settet under. TOM LISTE
    -- ER FORBUDT: et krav uten grunnlag ville gjort hver vurdering
    -- utilstrekkelig og hver motpart til et funn — det ser ut som en
    -- streng policy og er en konfigurasjonsfeil (112s dom).
    godkjente_grunnlag TEXT[] NOT NULL
        DEFAULT ARRAY['foretaksregister', 'manuell_gjennomgang']
        CHECK (cardinality(godkjente_grunnlag) > 0),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT motpartskrav_pk PRIMARY KEY (tenant)
);

-- `motpartssubjekt` — motparten selv. Identitetsraden, og DEN ENESTE
-- muterbare tabellen i modulen (`aktiv`). Alt annet er frosset.
--
-- DEN ER OGSÅ LÅSERADEN. M-42s lærdom, gjentatt i 112 og 114:
-- `SELECT ... FOR UPDATE` krever UPDATE-rett, og de frosne tabellene
-- har den ikke. Skal noe serialiseres, låses FORELDREraden her.
CREATE TABLE motpartssubjekt (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    motpart_id UUID NOT NULL,
    -- Norsk organisasjonsnummer: ni siffer. CHECK-en validerer FORMEN,
    -- ikke at foretaket finnes — det er nettopp det oppslaget er til
    -- for, og en base som lot som den visste ville tatt spørsmålet
    -- vekk fra der det hører hjemme.
    organisasjonsnummer TEXT NOT NULL
        CHECK (organisasjonsnummer ~ '^[0-9]{9}$'),
    -- Navnet SLIK TENANTEN OPPGA DET. 112s dom om originalen: navnet
    -- registeret svarer med havner i `motpartsversjon`, aldri her.
    -- Blander man dem, kan ingen etterpå se om en forveksling skyldtes
    -- det tenanten skrev eller det vi hentet.
    navn_oppgitt TEXT NOT NULL CHECK (navn_oppgitt ~ '[^[:space:]]'),
    aktiv BOOLEAN NOT NULL DEFAULT true,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT motpartssubjekt_pk PRIMARY KEY (tenant, motpart_id),
    CONSTRAINT motpartssubjekt_orgnr_unik
        UNIQUE (tenant, organisasjonsnummer)
);
CREATE INDEX motpartssubjekt_aktive
    ON motpartssubjekt (tenant) WHERE aktiv;

-- `foretaksoppslag` — DOM 3 OG 4. LOGGEN OVER HVER UTGÅENDE FORESPØRSEL.
--
-- Dette er raden klyngens unntak står og faller på. Et oppslag er ikke
-- en teknisk detalj, men en HANDLING noen må kunne svare for: hvem
-- spurte, om hvem, hvorfor, med hvilken hjemmel, mot hvilken vert, og
-- hva kom tilbake.
--
-- RADEN SKRIVES FØR FORESPØRSELEN GÅR UT, IKKE ETTER. Det er formens
-- viktigste egenskap, og den er en RETTING av det opplagte designet.
-- En dør som registrerer et ALLEREDE UTFØRT oppslag kan ikke håndheve
-- ferskhetsvinduet: forespørselen er ute, og doktrinen sier at
-- forespørselen ER skaden. Å nekte i etterkant gir det verste utfallet
-- av alle — oppslaget skjedde OG ble usynlig.
--
-- Så oppslaget RESERVERES: `m48_reserver_oppslag` sjekker vinduet og
-- skriver raden i én atomisk handling, og returnerer id-en. Deretter
-- gjør klienten forespørselen. Deretter fyller `m48_fullfor_oppslag`
-- inn svaret. Tre konsekvenser, alle tilsiktede:
--
--   * Man kan ikke gjøre en forespørsel uten at det finnes en rad.
--   * To samtidige arbeidere kan ikke begge passere vinduet — den
--     førstes reservasjon stenger for den andre.
--   * Et oppslag gjort UTEN reservasjon har ingen id, og uten id kan
--     `motpartsversjon` ikke skrives (fremmednøkkelen). Svaret blir
--     ubrukelig, som er den eneste håndhevingen som virker mot en
--     klient vi ikke kontrollerer.
--
-- FROSSET, MED ÉN UNNTAKSKOLONNE: `m48_fullfor_oppslag` må kunne
-- skrive svaret. Eieren beholder derfor UPDATE her — men bare her, og
-- radvakten i seksjon 6 nekter enhver endring av en rad som alt er
-- fullført.
CREATE TABLE foretaksoppslag (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    oppslag_id UUID NOT NULL,
    motpart_id UUID NOT NULL,
    -- Gjentatt fra subjektet MED VILJE: raden skal kunne leses alene av
    -- noen som spør «hvilke organisasjonsnumre sendte dere ut?» uten å
    -- måtte joine seg til svaret.
    organisasjonsnummer TEXT NOT NULL
        CHECK (organisasjonsnummer ~ '^[0-9]{9}$'),
    -- Verten forespørselen går til. STÅR PÅ RADEN, ikke bare i koden:
    -- porten `oppslag_mot_uregistrert_vert` måler at modulen bare kan
    -- nå ÉN registrert vert, men en konstant i en Python-fil er ikke
    -- evidens i ettertid. På raden blir en endring av konstanten
    -- synlig som en ny verdi i historikken i stedet for som en stille
    -- commit.
    vert TEXT NOT NULL CHECK (vert ~ '^[a-z0-9.-]+$'),
    -- DOM 3. Ingen dør har standardverdi for disse to.
    formaal TEXT NOT NULL
        CONSTRAINT foretaksoppslag_formaal_lukket CHECK (formaal IN (
            'kredittvurdering', 'onboarding', 'periodisk_kontroll',
            'manuell_gjennomgang')),
    hjemmel TEXT NOT NULL CHECK (length(btrim(hjemmel)) >= 8),
    -- Hva som kom tilbake. 'reservert' er tilstanden MELLOM de to
    -- dørene: forespørselen er lovlig og i ferd med å gå ut.
    --
    -- HVILKE STATUSER SOM STENGER VINDUET, og hvorfor det ikke er alle:
    --
    --   'reservert', 'treff', 'ikke_funnet' STENGER. De to siste fordi
    --   vi HAR et svar — et nytt oppslag ville vært unødvendig. Den
    --   første fordi forespørselen er i lufta.
    --
    --   'feil', 'avvist' og 'forlatt' STENGER IKKE. En forespørsel som
    --   aldri kom fram ga oss ingen kunnskap, og et vindu som stengte
    --   på den ville låst en tenant ute i et døgn på grunn av en
    --   nettverksfeil. De TELLES likevel — de gikk ut av huset — og
    --   sveipen finner en motpart vi har prøvd for mange ganger.
    --
    -- 'forlatt' ER SVEIPENS TERMINALTILSTAND, og den finnes fordi
    -- alternativet var et funn som aldri kunne lukkes. En klient som
    -- dør mellom de to dørene etterlater en reservasjon ingen fyller
    -- ut; uten en vei ut ville `oppslag_uten_svar` stått åpent for
    -- alltid — M-39s felle (113): en funntype uten øvre grense OG uten
    -- botemiddel er et varsel som aldri kan lukkes, og et varsel som
    -- aldri lukkes blir et varsel ingen leser.
    --
    -- Sveipen setter den, og den er ÆRLIG på en måte 'feil' ikke ville
    -- vært: 'feil' påstår at vi fikk et negativt svar. 'forlatt' sier
    -- det som faktisk skjedde — forespørselen gikk ut, og vi
    -- registrerte aldri hva som kom tilbake.
    svarstatus TEXT NOT NULL DEFAULT 'reservert'
        CONSTRAINT foretaksoppslag_svarstatus_lukket
        CHECK (svarstatus IN ('reservert', 'treff', 'ikke_funnet',
                              'avvist', 'feil', 'forlatt')),
    -- Innholdsadressen til svaret. Ikke svaret selv: registerdata om et
    -- foretak hører hjemme i `motpartsversjon` der de er TOLKET, ikke
    -- som en rå kropp ingen har lest. Summen gjør likevel to like svar
    -- gjenkjennelige.
    svar_sha256 TEXT
        CHECK (svar_sha256 IS NULL OR svar_sha256 ~ '^[0-9a-f]{64}$'),
    reservert TIMESTAMPTZ NOT NULL DEFAULT now(),
    reservert_av TEXT NOT NULL CHECK (reservert_av ~ '[^[:space:]]'),
    fullfort TIMESTAMPTZ,
    CONSTRAINT foretaksoppslag_pk PRIMARY KEY (tenant, oppslag_id),
    CONSTRAINT foretaksoppslag_motpart_fk
        FOREIGN KEY (tenant, motpart_id)
        REFERENCES motpartssubjekt (tenant, motpart_id),
    -- Reservert og fullført er den samme opplysningen sagt to ganger;
    -- de skal aldri kunne si hver sin ting.
    CONSTRAINT foretaksoppslag_fullfort_helhet CHECK (
        (svarstatus = 'reservert') = (fullfort IS NULL)),
    -- Et treff UTEN sum, eller en sum uten treff, er en selvmotsigelse:
    -- raden ville påstått at vi fikk et svar vi ikke kan kjenne igjen.
    CONSTRAINT foretaksoppslag_treff_har_sum CHECK (
        (svarstatus = 'treff') = (svar_sha256 IS NOT NULL))
);
-- Ferskhetsspørsmålet er «finnes det et STENGENDE oppslag på dette
-- orgnr nylig», og indeksen er formet etter nettopp det spørsmålet.
CREATE INDEX foretaksoppslag_ferskhet
    ON foretaksoppslag (tenant, organisasjonsnummer, reservert DESC)
    WHERE svarstatus IN ('reservert', 'treff', 'ikke_funnet');
CREATE INDEX foretaksoppslag_motpart
    ON foretaksoppslag (tenant, motpart_id, reservert DESC);
-- Reservasjoner som aldri ble fullført: sveipens spørsmål, og en
-- indeks som gjør det billig å stille hver natt.
CREATE INDEX foretaksoppslag_apne_reservasjoner
    ON foretaksoppslag (tenant, reservert) WHERE svarstatus = 'reservert';

-- `motpartsversjon` — DOM 1. HOVEDBOKEN FOR MOTPARTSPROFILEN.
--
-- Den gjeldende profilen er den SISTE raden her. Det finnes ingen annen
-- profil noe sted i skjemaet, og det er hele poenget.
--
-- HVER VERSJON PEKER PÅ SIN KILDE. Kom den fra et oppslag, står
-- `oppslag_id`. Ble den registrert manuelt, er den NULL og `kilde` sier
-- 'manuell'. CHECK-en under gjør de to enige, slik at ingen versjon kan
-- påstå å komme fra registeret uten å kunne peke på forespørselen.
CREATE TABLE motpartsversjon (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    versjon_id UUID NOT NULL,
    motpart_id UUID NOT NULL,
    oppslag_id UUID,
    kilde TEXT NOT NULL
        CONSTRAINT motpartsversjon_kilde_lukket CHECK (kilde IN (
            'foretaksregister', 'manuell')),
    -- KILDEVERSJONEN. For registeret: datoen registeret selv oppgir at
    -- opplysningene gjelder fra. En påstand uten versjon er ubrukelig i
    -- ettertid — klyngefundamentets andre dom.
    kildeversjon TEXT NOT NULL CHECK (kildeversjon ~ '[^[:space:]]'),
    -- Navnet REGISTERET oppga. Står side om side med `navn_oppgitt` på
    -- subjektet, aldri oppå det (112s dom).
    navn_registrert TEXT NOT NULL CHECK (navn_registrert ~ '[^[:space:]]'),
    -- Organisasjonsform slik registeret koder den (AS, ANS, ENK, …).
    -- Fri tekst med formkrav, ikke et lukket sett: settet er
    -- registerets, ikke vårt, og en modul som avviste en ukjent kode
    -- ville nektet å registrere et foretak som faktisk finnes.
    organisasjonsform TEXT NOT NULL
        CHECK (organisasjonsform ~ '^[A-ZÆØÅ]{2,10}$'),
    registerstatus TEXT NOT NULL
        CONSTRAINT motpartsversjon_status_lukket CHECK (registerstatus IN (
            'aktiv', 'under_avvikling', 'avviklet', 'slettet', 'ukjent')),
    -- To flagg registeret svarer eksplisitt på. De står som egne
    -- kolonner og ikke som en tolkning, fordi tolkningen er
    -- vurderingens jobb — og vurderingen skal kunne gjøres om igjen
    -- med en annen policy uten at grunnlaget er borte.
    konkurs BOOLEAN NOT NULL DEFAULT false,
    under_tvangsavvikling BOOLEAN NOT NULL DEFAULT false,
    gjelder_fra DATE NOT NULL,
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT motpartsversjon_pk PRIMARY KEY (tenant, versjon_id),
    CONSTRAINT motpartsversjon_motpart_fk
        FOREIGN KEY (tenant, motpart_id)
        REFERENCES motpartssubjekt (tenant, motpart_id),
    -- FREMMEDNØKKELEN ER HÅNDHEVINGEN. Et oppslag gjort uten
    -- reservasjon har ingen id, og uten id kan ingen versjon skrives.
    -- At oppslaget dessuten må være FULLFØRT med 'treff' kan ingen
    -- CHECK uttrykke på tvers av tabeller — det håndheves i
    -- `m48_registrer_versjon`, og radvakten i seksjon 6 lukker
    -- omveien.
    CONSTRAINT motpartsversjon_oppslag_fk
        FOREIGN KEY (tenant, oppslag_id)
        REFERENCES foretaksoppslag (tenant, oppslag_id),
    -- Kilden og pekeren må mene det samme.
    CONSTRAINT motpartsversjon_kilde_peker CHECK (
        (kilde = 'foretaksregister') = (oppslag_id IS NOT NULL)),
    -- SAMME OPPSLAG GIR ÉN VERSJON. Et oppslag som tolkes to ganger er
    -- ikke to profilendringer.
    CONSTRAINT motpartsversjon_oppslag_unik UNIQUE (tenant, oppslag_id)
);
CREATE INDEX motpartsversjon_oppslag_idx
    ON motpartsversjon (tenant, motpart_id, gjelder_fra DESC,
                        registrert DESC);

-- `motpartsvurdering` — DOM 2 OG 5. FORSLAGET, OG BARE FORSLAGET.
--
-- LES KOLONNENAVNET: `foreslatt_grense_ore`. Det finnes ingen
-- `gjeldende_grense_ore`, ingen `godkjent`, ingen `avslag`. Vurderingen
-- er en MÅLING av motparten mot tenantens policy, og hva noen gjør med
-- målingen er utenfor modulen. Fraværet av de kolonnene er portene
-- `modulen_satte_kredittgrense` og `modulen_avslo_motpart`.
--
-- Nøklet på VERSJONEN, ikke på motparten: en vurdering gjelder den
-- profilen som sto da den ble gjort. Går motparten konkurs i morgen, er
-- gårsdagens vurdering fortsatt sann om gårsdagens profil — og sier
-- ingenting om dagens. Det er nettopp den forskjellen en kredittgrense
-- ville stått og falt på (112s form).
CREATE TABLE motpartsvurdering (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    vurdering_id UUID NOT NULL,
    versjon_id UUID NOT NULL,
    -- DOM 5. Hvilken `motpartskrav.versjon` som gjaldt da forslaget ble
    -- regnet. Uten den kan ingen skille «policyen var slik» fra «noen
    -- regnet feil».
    policyversjon INT NOT NULL CHECK (policyversjon >= 1),
    grunnlag TEXT NOT NULL
        CONSTRAINT motpartsvurdering_grunnlag_lukket CHECK (grunnlag IN (
            'foretaksregister', 'manuell_gjennomgang')),
    -- DOM 6. Øre, heltall, aldri desimaltall.
    foreslatt_grense_ore BIGINT NOT NULL
        CHECK (foreslatt_grense_ore >= 0),
    -- Hvorfor forslaget ble som det ble, i tekst et menneske kan lese.
    -- PÅKREVD: et tall uten begrunnelse er ikke en vurdering.
    begrunnelse TEXT NOT NULL CHECK (length(btrim(begrunnelse)) >= 8),
    vurdert TIMESTAMPTZ NOT NULL DEFAULT now(),
    vurdert_av TEXT NOT NULL CHECK (vurdert_av ~ '[^[:space:]]'),
    CONSTRAINT motpartsvurdering_pk PRIMARY KEY (tenant, vurdering_id),
    CONSTRAINT motpartsvurdering_versjon_fk
        FOREIGN KEY (tenant, versjon_id)
        REFERENCES motpartsversjon (tenant, versjon_id)
);
CREATE INDEX motpartsvurdering_oppslag
    ON motpartsvurdering (tenant, versjon_id, vurdert DESC);

-- `motpartsfunn` — det sveipen finner. 112s form: funnraden er nøklet
-- på (tenant, motpart, funntype) og GJENBRUKES. Sveipen kjører daglig;
-- uten den nøkkelen ville den skrevet et nytt funn hver natt om samme
-- forhold, og «antall åpne funn» hadde målt antall netter i stedet for
-- antall problemer.
CREATE TABLE motpartsfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    motpart_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CONSTRAINT motpartsfunn_type_lukket CHECK (funntype IN (
            'uvurdert_motpart',        -- ingen vurdering innen fristen
            'utdatert_vurdering',      -- eldre enn vurdering_gyldig_dogn
            'profil_uten_vurdering',   -- ny versjon, ingen ny vurdering
            'motpart_avviklet',        -- registerstatus krever handling
            'forslag_over_tak',        -- forslag > maks_forslag_ore
            'oppslag_uten_svar',       -- reservasjon aldri fullført
            'gjentatte_oppslagsfeil',  -- for mange 'feil'/'avvist'
            'ingen_krav')),            -- tenanten har ingen policy
    -- DØGN for tidsfunnene, NULL for de andre.
    over_grense INT,
    -- Den siste vurderingen og profilen, når funnet handler om dem. Et
    -- funn uten det ene faktumet som forklarer det tvinger leseren til
    -- å slå opp selv (112s dom).
    siste_registerstatus TEXT,
    siste_forslag_ore BIGINT,
    kravversjon INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT motpartsfunn_pk PRIMARY KEY (tenant, motpart_id,
                                            funntype),
    CONSTRAINT motpartsfunn_motpart_fk FOREIGN KEY (tenant, motpart_id)
        REFERENCES motpartssubjekt (tenant, motpart_id),
    CONSTRAINT motpartsfunn_lukking_helhet CHECK (
        (apen AND lukket_ts IS NULL)
     OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE INDEX motpartsfunn_apne
    ON motpartsfunn (tenant, funntype) WHERE apen;


-- ------------------------------------------------------------
-- 2. Evidenskjeden. SECURITY DEFINER, eid av `disponit_motpart_eier`,
--    SP-1.
-- ------------------------------------------------------------

-- Eieren trenger å kunne kalle SP-1-vakten og å skrive evidens.
-- `krev_tenantkontekst` eies av `disponit_m37_claimer` (111s form), så
-- EXECUTE må gis AV den rollen — en GRANT fra migratoren er en
-- no-op med en advarsel, ikke en feil, og det er nettopp slik en
-- manglende rettighet blir usynlig til første kall.
GRANT INSERT ON revisjonslogg TO disponit_motpart_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_motpart_eier;
RESET ROLE;

-- HERFRA OG TIL SEKSJON 6 EIES ALT SOM LAGES AV MOTPARTSEIEREN.
-- Dørene er SECURITY DEFINER, så eierskapet ER fullmakten de kjører
-- med — lages de av migratoren, kjører de med migratorens rettigheter
-- og kryss-tenant-policyen i seksjon 6 treffer dem aldri.
SET LOCAL ROLE disponit_motpart_eier;

CREATE FUNCTION m48_evidens(p_tenant TEXT, p_motpart_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm48_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm48_motpart', 'handling', p_handling,
        'motpart_id', p_motpart_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm48_motpart',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:motpart', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m48_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;




-- ------------------------------------------------------------
-- 2a. Den registrerte verten.
-- ------------------------------------------------------------

-- PORTEN `oppslag_mot_uregistrert_vert`, SOM ÉN KILDE.
--
-- Verten står HER og ikke i `motpartskrav`, og det er en
-- sikkerhetsbeslutning, ikke en smakssak: `motpartskrav` er TENANTENS
-- tabell. Kunne en tenant sette verten, hadde vi bygget en dør der
-- kunden bestemmer hvor plattformen sender forespørsler — en SSRF-dør
-- med policyklær på. Hvilket register vi spør er plattformens valg.
--
-- Den står i BASEN og ikke bare i Python fordi begge sider må mene det
-- samme, og basen er den siden som kan NEKTE. Klienten leser verten
-- herfra; `m48_reserver_oppslag` avviser alt annet. En klient som ble
-- endret til å spørre et annet sted, får ingen reservasjon å gjøre det
-- under — og uten reservasjon kan svaret ikke bli en versjon.
--
-- IMMUTABLE og uten argumenter: dette er en konstant, og skal kunne
-- brukes i en CHECK om noen senere vil ha det.
CREATE FUNCTION m48_registrert_vert()
RETURNS TEXT LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT 'data.brreg.no'::text
$$;
GRANT EXECUTE ON FUNCTION m48_registrert_vert() TO PUBLIC;
-- ------------------------------------------------------------
-- 3. Skrivedørene.
-- ------------------------------------------------------------

-- DOM 4 OG 5: POLICYEN ER TENANTENS. Hver endring hever `versjon`, og
-- vurderingene peker på den versjonen som gjaldt da de ble regnet.
CREATE FUNCTION m48_sett_krav(
    p_tenant TEXT, p_oppslag_ferskhet_timer INT,
    p_vurdering_gyldig_dogn INT, p_uvurdert_dogn INT,
    p_maks_forslag_ore BIGINT, p_godkjente_grunnlag TEXT[],
    p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm48_sett_krav');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    -- Det lukkede settet står i ÉN kilde. En tenant kan velge fra det,
    -- ikke utvide det: et grunnlag modulen ikke vet hvordan den skal
    -- måle, ville gjort hver vurdering på det grunnlaget uetterrettelig.
    IF EXISTS (SELECT 1 FROM unnest(p_godkjente_grunnlag) g
                WHERE g NOT IN ('foretaksregister',
                                'manuell_gjennomgang')) THEN
        RAISE EXCEPTION 'm48_sett_krav: ukjent grunnlag i %',
            p_godkjente_grunnlag USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO public.motpartskrav
        (tenant, oppslag_ferskhet_timer, vurdering_gyldig_dogn,
         uvurdert_dogn, maks_forslag_ore, godkjente_grunnlag,
         oppdatert_av)
    VALUES (p_tenant, p_oppslag_ferskhet_timer, p_vurdering_gyldig_dogn,
            p_uvurdert_dogn, p_maks_forslag_ore, p_godkjente_grunnlag,
            p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        oppslag_ferskhet_timer = EXCLUDED.oppslag_ferskhet_timer,
        vurdering_gyldig_dogn = EXCLUDED.vurdering_gyldig_dogn,
        uvurdert_dogn = EXCLUDED.uvurdert_dogn,
        maks_forslag_ore = EXCLUDED.maks_forslag_ore,
        godkjente_grunnlag = EXCLUDED.godkjente_grunnlag,
        versjon = public.motpartskrav.versjon + 1,
        oppdatert = now(),
        oppdatert_av = EXCLUDED.oppdatert_av
    RETURNING versjon INTO v_versjon;

    -- Kravet gjelder HELE tenanten, ikke én motpart: NULL er det
    -- ærlige svaret på «hvilken motpart», ikke et manglende felt.
    PERFORM public.m48_evidens(p_tenant, NULL, 'motpartskrav_satt',
        p_aktor, jsonb_build_object(
            'versjon', v_versjon,
            'oppslag_ferskhet_timer', p_oppslag_ferskhet_timer,
            'maks_forslag_ore', p_maks_forslag_ore));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION m48_sett_krav(
    TEXT, INT, INT, INT, BIGINT, TEXT[], TEXT) FROM PUBLIC;


CREATE FUNCTION m48_registrer_motpart(
    p_tenant TEXT, p_motpart_id UUID, p_organisasjonsnummer TEXT,
    p_navn_oppgitt TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm48_registrer_motpart');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    INSERT INTO public.motpartssubjekt
        (tenant, motpart_id, organisasjonsnummer, navn_oppgitt,
         opprettet_av)
    VALUES (p_tenant, p_motpart_id, btrim(p_organisasjonsnummer),
            btrim(p_navn_oppgitt), p_aktor);

    PERFORM public.m48_evidens(p_tenant, p_motpart_id,
        'motpart_registrert', p_aktor,
        jsonb_build_object('organisasjonsnummer',
                           btrim(p_organisasjonsnummer)));
END $$;
REVOKE ALL ON FUNCTION m48_registrer_motpart(
    TEXT, UUID, TEXT, TEXT, TEXT) FROM PUBLIC;


-- DOM 4, HÅNDHEVET. DEN VIKTIGSTE DØRA I MODULEN.
--
-- Reserverer ett foretaksoppslag: sjekker ferskhetsvinduet og skriver
-- raden i SAMME transaksjon. Klienten gjør ikke forespørselen før den
-- har fått en id herfra, og kan ikke skrive en versjon uten den.
--
-- LÅSEN ER PÅ SUBJEKTET, IKKE PÅ OPPSLAGSTABELLEN. M-42s lærdom (110),
-- gjentatt i 112 og 114: `SELECT ... FOR UPDATE` krever UPDATE-retten.
-- Subjektet er den ene muterbare tabellen her, og låsen på den
-- serialiserer to samtidige reservasjoner på samme motpart — som er
-- nettopp kappløpet vinduet må vinne.
--
-- VERTEN VALIDERES MOT `m48_registrert_vert()`. Porten
-- `oppslag_mot_uregistrert_vert` er dermed i basen og ikke bare i
-- klienten: en klient som ble endret til å spørre et annet sted, får
-- ingen reservasjon å gjøre det under.
CREATE FUNCTION m48_reserver_oppslag(
    p_tenant TEXT, p_oppslag_id UUID, p_motpart_id UUID, p_vert TEXT,
    p_formaal TEXT, p_hjemmel TEXT, p_aktor TEXT)
-- RETURNERER ORGANISASJONSNUMMERET, ikke bare tidspunktet. Kalleren
-- trenger det for å gjøre selve forespørselen, og døra har det alt —
-- den leste det under låsen. Måtte kalleren slå det opp selv, ville
-- den enten trengt en lesedør til (og en rundtur til) eller lest det
-- fra en liste med grense på, der motpart nummer 201 ville gitt en
-- falsk «ikke funnet».
RETURNS TABLE (organisasjonsnummer TEXT, forrige_oppslag TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_aktiv BOOLEAN;
    v_orgnr TEXT;
    v_vindu INT;
    v_siste TIMESTAMPTZ;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm48_reserver_oppslag');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    IF btrim(coalesce(p_vert, '')) <> public.m48_registrert_vert() THEN
        RAISE EXCEPTION 'm48_reserver_oppslag: % er ikke den'
            ' registrerte verten (%) — modulen slår opp ETT sted',
            p_vert, public.m48_registrert_vert()
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- KOLONNENE KVALIFISERES. `RETURNS TABLE` innfører
    -- `organisasjonsnummer` som en OUT-variabel, og en ukvalifisert
    -- referanse blir da tvetydig mellom variabelen og kolonnen —
    -- PostgreSQL nekter, og med rette.
    SELECT s.aktiv, s.organisasjonsnummer INTO v_aktiv, v_orgnr
      FROM public.motpartssubjekt s
     WHERE s.tenant = p_tenant AND s.motpart_id = p_motpart_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm48_reserver_oppslag: ukjent motpart %',
            p_motpart_id USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_aktiv THEN
        RAISE EXCEPTION 'm48_reserver_oppslag: motparten % er'
            ' deaktivert — et oppslag om en motpart ingen handler med'
            ' er selve den unødvendige forespørselen', p_motpart_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Ingen krav-rad betyr ingen policy, og da er standardvinduet
    -- ikke vårt å gjette på. `coalesce` mot tabellens egen default
    -- ville vært å hardkode policyen i en annen fil enn den som eier
    -- den — sveipen finner `ingen_krav` og noen må ta stilling.
    SELECT oppslag_ferskhet_timer INTO v_vindu
      FROM public.motpartskrav WHERE tenant = p_tenant;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm48_reserver_oppslag: tenanten % har ingen'
            ' motpartskrav — ferskhetsvinduet er tenantens, og et'
            ' oppslag uten policy er et oppslag ingen har hjemlet',
            p_tenant USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF v_vindu > 0 THEN
        SELECT max(o.reservert) INTO v_siste
          FROM public.foretaksoppslag o
         WHERE o.tenant = p_tenant
           AND o.organisasjonsnummer = v_orgnr
           AND o.svarstatus IN ('reservert', 'treff', 'ikke_funnet');
        IF v_siste IS NOT NULL
           AND v_siste > now() - make_interval(hours => v_vindu) THEN
            RAISE EXCEPTION 'm48_reserver_oppslag: forrige oppslag på'
                ' % var %, innenfor ferskhetsvinduet på % timer —'
                ' forespørselen er unødvendig', v_orgnr, v_siste,
                v_vindu USING ERRCODE = 'invalid_parameter_value';
        END IF;
    END IF;

    INSERT INTO public.foretaksoppslag
        (tenant, oppslag_id, motpart_id, organisasjonsnummer, vert,
         formaal, hjemmel, reservert_av)
    VALUES (p_tenant, p_oppslag_id, p_motpart_id, v_orgnr,
            btrim(p_vert), p_formaal, btrim(p_hjemmel), p_aktor);

    PERFORM public.m48_evidens(p_tenant, p_motpart_id,
        'foretaksoppslag_reservert', p_aktor,
        jsonb_build_object('oppslag_id', p_oppslag_id,
                           'organisasjonsnummer', v_orgnr,
                           'vert', btrim(p_vert),
                           'formaal', p_formaal,
                           'hjemmel', btrim(p_hjemmel)));
    RETURN QUERY SELECT v_orgnr, v_siste;
END $$;
REVOKE ALL ON FUNCTION m48_reserver_oppslag(
    TEXT, UUID, UUID, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;


-- Fyller inn svaret på en reservasjon. Kan bare gjøres ÉN gang: en rad
-- som alt er fullført, er frosset (radvakten i seksjon 6 lukker
-- omveien, denne døra gir den ærlige feilmeldingen).
CREATE FUNCTION m48_fullfor_oppslag(
    p_tenant TEXT, p_oppslag_id UUID, p_svarstatus TEXT,
    p_svar_sha256 TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_motpart UUID;
    v_status TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm48_fullfor_oppslag');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    IF p_svarstatus = 'reservert' THEN
        RAISE EXCEPTION 'm48_fullfor_oppslag: «reservert» er ikke et'
            ' svar — en forespørsel som gikk ut har et utfall'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Motparten låses, ikke oppslagsraden: samme grunn som over, og
    -- den serialiserer to samtidige fullføringer.
    SELECT o.motpart_id INTO v_motpart
      FROM public.foretaksoppslag o
     WHERE o.tenant = p_tenant AND o.oppslag_id = p_oppslag_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm48_fullfor_oppslag: ukjent oppslag %',
            p_oppslag_id USING ERRCODE = 'no_data_found';
    END IF;
    PERFORM 1 FROM public.motpartssubjekt
     WHERE tenant = p_tenant AND motpart_id = v_motpart FOR UPDATE;

    -- STATUSEN LESES ETTER LÅSEN, IKKE FØR (CodeRabbit, 116).
    --
    -- Leste vi den sammen med `motpart_id` over, kunne to samtidige
    -- fullføringer begge sett «reservert»: den andre ville stått og
    -- ventet på låsen med en FORELDET verdi i hånda, og passert
    -- vakten under. Radvakten ville riktignok stanset selve
    -- skrivingen — den leser raden på nytt — men kalleren hadde fått
    -- `insufficient_privilege` fra en trigger i stedet for det ærlige
    -- «alt fullført». Vakten i døra skal være den som svarer; vakten
    -- på raden er det andre gjerdet, ikke det første.
    SELECT o.svarstatus INTO v_status
      FROM public.foretaksoppslag o
     WHERE o.tenant = p_tenant AND o.oppslag_id = p_oppslag_id;
    IF v_status <> 'reservert' THEN
        RAISE EXCEPTION 'm48_fullfor_oppslag: oppslaget % er alt'
            ' fullført med «%» — et svar overskrives ikke',
            p_oppslag_id, v_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.foretaksoppslag
       SET svarstatus = p_svarstatus,
           svar_sha256 = nullif(btrim(coalesce(p_svar_sha256, '')), ''),
           fullfort = now()
     WHERE tenant = p_tenant AND oppslag_id = p_oppslag_id;

    PERFORM public.m48_evidens(p_tenant, v_motpart,
        'foretaksoppslag_fullfort', p_aktor,
        jsonb_build_object('oppslag_id', p_oppslag_id,
                           'svarstatus', p_svarstatus));
END $$;
REVOKE ALL ON FUNCTION m48_fullfor_oppslag(
    TEXT, UUID, TEXT, TEXT, TEXT) FROM PUBLIC;


-- DOM 1. En versjon kan bare bygges på et FULLFØRT oppslag med treff.
-- Fremmednøkkelen sikrer at oppslaget finnes; denne døra sikrer at det
-- faktisk ga et svar. Uten den kunne en reservasjon som aldri gikk ut,
-- blitt til en «registerprofil».
CREATE FUNCTION m48_registrer_versjon(
    p_tenant TEXT, p_versjon_id UUID, p_motpart_id UUID,
    p_oppslag_id UUID, p_kilde TEXT, p_kildeversjon TEXT,
    p_navn_registrert TEXT, p_organisasjonsform TEXT,
    p_registerstatus TEXT, p_konkurs BOOLEAN,
    p_under_tvangsavvikling BOOLEAN, p_gjelder_fra DATE, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_aktiv BOOLEAN;
    v_status TEXT;
    v_oppslagsmotpart UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm48_registrer_versjon');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    IF p_gjelder_fra IS NULL OR p_gjelder_fra > current_date THEN
        RAISE EXCEPTION 'm48_registrer_versjon: en profil kan ikke'
            ' gjelde fra framtida (%)', p_gjelder_fra
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT aktiv INTO v_aktiv FROM public.motpartssubjekt
     WHERE tenant = p_tenant AND motpart_id = p_motpart_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm48_registrer_versjon: ukjent motpart %',
            p_motpart_id USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_aktiv THEN
        RAISE EXCEPTION 'm48_registrer_versjon: motparten % er'
            ' deaktivert', p_motpart_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF p_kilde = 'foretaksregister' THEN
        SELECT svarstatus, motpart_id
          INTO v_status, v_oppslagsmotpart
          FROM public.foretaksoppslag
         WHERE tenant = p_tenant AND oppslag_id = p_oppslag_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'm48_registrer_versjon: ukjent oppslag %',
                p_oppslag_id USING ERRCODE = 'no_data_found';
        END IF;
        IF v_status <> 'treff' THEN
            RAISE EXCEPTION 'm48_registrer_versjon: oppslaget % har'
                ' status «%» — bare et treff er en profil',
                p_oppslag_id, v_status
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        -- Oppslaget må gjelde SAMME motpart. Uten dette kunne en
        -- profil for ett foretak bygges på et treff om et annet.
        IF v_oppslagsmotpart <> p_motpart_id THEN
            RAISE EXCEPTION 'm48_registrer_versjon: oppslaget % gjelder'
                ' motpart %, ikke %', p_oppslag_id, v_oppslagsmotpart,
                p_motpart_id USING ERRCODE = 'invalid_parameter_value';
        END IF;
    END IF;

    INSERT INTO public.motpartsversjon
        (tenant, versjon_id, motpart_id, oppslag_id, kilde,
         kildeversjon, navn_registrert, organisasjonsform,
         registerstatus, konkurs, under_tvangsavvikling, gjelder_fra,
         registrert_av)
    VALUES (p_tenant, p_versjon_id, p_motpart_id, p_oppslag_id,
            p_kilde, btrim(p_kildeversjon), btrim(p_navn_registrert),
            upper(btrim(p_organisasjonsform)), p_registerstatus,
            p_konkurs, p_under_tvangsavvikling, p_gjelder_fra, p_aktor);

    PERFORM public.m48_evidens(p_tenant, p_motpart_id,
        'motpartsversjon_registrert', p_aktor,
        jsonb_build_object('versjon_id', p_versjon_id,
                           'kilde', p_kilde,
                           'registerstatus', p_registerstatus));
END $$;
REVOKE ALL ON FUNCTION m48_registrer_versjon(
    TEXT, UUID, UUID, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN,
    BOOLEAN, DATE, TEXT) FROM PUBLIC;


-- DOM 2 OG 5. FORSLAGET. Policyversjonen leses HER og skrives på
-- raden — den er ikke et argument, fordi en kaller som fikk oppgi den
-- kunne oppgitt en annen enn den som faktisk gjaldt.
CREATE FUNCTION m48_registrer_vurdering(
    p_tenant TEXT, p_vurdering_id UUID, p_versjon_id UUID,
    p_grunnlag TEXT, p_foreslatt_grense_ore BIGINT,
    p_begrunnelse TEXT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_motpart UUID;
    v_policyversjon INT;
    v_godkjente TEXT[];
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm48_registrer_vurdering');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    -- Versjonen leses uten lås (frosset), motparten låses.
    SELECT motpart_id INTO v_motpart FROM public.motpartsversjon
     WHERE tenant = p_tenant AND versjon_id = p_versjon_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm48_registrer_vurdering: ukjent'
            ' motpartsversjon %', p_versjon_id
            USING ERRCODE = 'no_data_found';
    END IF;
    PERFORM 1 FROM public.motpartssubjekt
     WHERE tenant = p_tenant AND motpart_id = v_motpart FOR UPDATE;

    SELECT versjon, godkjente_grunnlag
      INTO v_policyversjon, v_godkjente
      FROM public.motpartskrav WHERE tenant = p_tenant;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm48_registrer_vurdering: tenanten % har ingen'
            ' motpartskrav — en vurdering uten policy er et tall uten'
            ' grunnlag', p_tenant
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT (p_grunnlag = ANY (v_godkjente)) THEN
        RAISE EXCEPTION 'm48_registrer_vurdering: grunnlaget «%» er'
            ' ikke blant tenantens godkjente (%)', p_grunnlag,
            v_godkjente USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- TAKET HÅNDHEVES IKKE HER, DET MÅLES. Et forslag over tenantens
    -- tak er et FUNN sveipen finner, ikke en avvisning: nekter vi å
    -- lagre det, forsvinner nettopp den observasjonen noen skulle tatt
    -- stilling til, og modulen ville tatt en beslutning den ikke har
    -- fullmakt til.
    INSERT INTO public.motpartsvurdering
        (tenant, vurdering_id, versjon_id, policyversjon, grunnlag,
         foreslatt_grense_ore, begrunnelse, vurdert_av)
    VALUES (p_tenant, p_vurdering_id, p_versjon_id, v_policyversjon,
            p_grunnlag, p_foreslatt_grense_ore, btrim(p_begrunnelse),
            p_aktor);

    PERFORM public.m48_evidens(p_tenant, v_motpart,
        'motpartsvurdering_registrert', p_aktor,
        jsonb_build_object('vurdering_id', p_vurdering_id,
                           'policyversjon', v_policyversjon,
                           'foreslatt_grense_ore',
                           p_foreslatt_grense_ore));
    RETURN v_policyversjon;
END $$;
REVOKE ALL ON FUNCTION m48_registrer_vurdering(
    TEXT, UUID, UUID, TEXT, BIGINT, TEXT, TEXT) FROM PUBLIC;


CREATE FUNCTION m48_deaktiver_motpart(
    p_tenant TEXT, p_motpart_id UUID, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_aktiv BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm48_deaktiver_motpart');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    SELECT aktiv INTO v_aktiv FROM public.motpartssubjekt
     WHERE tenant = p_tenant AND motpart_id = p_motpart_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm48_deaktiver_motpart: ukjent motpart %',
            p_motpart_id USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_aktiv THEN
        RETURN;  -- idempotent
    END IF;

    UPDATE public.motpartssubjekt SET aktiv = false
     WHERE tenant = p_tenant AND motpart_id = p_motpart_id;

    -- HISTORIKKEN BLIR STÅENDE. Deaktivering betyr «vi handler ikke
    -- med denne lenger», ikke «dette skjedde aldri». Versjonene,
    -- oppslagene og vurderingene er frosset og røres ikke.
    PERFORM public.m48_evidens(p_tenant, p_motpart_id,
        'motpart_deaktivert', p_aktor, '{}'::jsonb);
END $$;
REVOKE ALL ON FUNCTION m48_deaktiver_motpart(TEXT, UUID, TEXT)
    FROM PUBLIC;


-- Lukking krever et menneske og et notat: et funn som lukkes uten at
-- noen sier hvorfor, er et funn som ble gjemt.
CREATE FUNCTION m48_lukk_funn(
    p_tenant TEXT, p_motpart_id UUID, p_funntype TEXT,
    p_notat TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_apen BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm48_lukk_funn');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    IF length(btrim(coalesce(p_notat, ''))) < 4 THEN
        RAISE EXCEPTION 'm48_lukk_funn: et funn lukkes med en'
            ' begrunnelse, ikke med et klikk'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT apen INTO v_apen FROM public.motpartsfunn
     WHERE tenant = p_tenant AND motpart_id = p_motpart_id
       AND funntype = p_funntype
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm48_lukk_funn: ukjent funn %/%',
            p_motpart_id, p_funntype USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_apen THEN
        RETURN;  -- idempotent
    END IF;

    UPDATE public.motpartsfunn
       SET apen = false, lukket_ts = now()
     WHERE tenant = p_tenant AND motpart_id = p_motpart_id
       AND funntype = p_funntype;

    PERFORM public.m48_evidens(p_tenant, p_motpart_id, 'funn_lukket',
        p_aktor, jsonb_build_object('funntype', p_funntype,
                                    'notat', btrim(p_notat)));
END $$;
REVOKE ALL ON FUNCTION m48_lukk_funn(TEXT, UUID, TEXT, TEXT, TEXT)
    FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Lesedørene.
-- ------------------------------------------------------------

-- SAMMENDRAGET FLATEN ÅPNER PÅ. `oppslag_siste_dogn` står her fordi
-- den er modulens mest betente tall: klyngens unntak er begrunnet med
-- at forespørselen er nødvendig, og da må antallet forespørsler være
-- det første noen ser — ikke noe man må grave etter.
CREATE FUNCTION m48_motpartsstatus(p_tenant TEXT)
RETURNS TABLE (motparter BIGINT, aktive BIGINT, med_profil BIGINT,
               vurderte BIGINT, apne_funn BIGINT,
               apne_avviklet BIGINT, oppslag_siste_dogn BIGINT,
               apne_reservasjoner BIGINT, har_krav BOOLEAN,
               kravversjon INT, registrert_vert TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm48_motpartsstatus');
    RETURN QUERY
    SELECT (SELECT count(*) FROM public.motpartssubjekt s
             WHERE s.tenant = p_tenant),
           (SELECT count(*) FROM public.motpartssubjekt s
             WHERE s.tenant = p_tenant AND s.aktiv),
           (SELECT count(DISTINCT v.motpart_id)
              FROM public.motpartsversjon v WHERE v.tenant = p_tenant),
           (SELECT count(DISTINCT v.motpart_id)
              FROM public.motpartsvurdering u
              JOIN public.motpartsversjon v
                ON v.tenant = u.tenant AND v.versjon_id = u.versjon_id
             WHERE u.tenant = p_tenant),
           (SELECT count(*) FROM public.motpartsfunn f
             WHERE f.tenant = p_tenant AND f.apen),
           (SELECT count(*) FROM public.motpartsfunn f
             WHERE f.tenant = p_tenant AND f.apen
               AND f.funntype = 'motpart_avviklet'),
           (SELECT count(*) FROM public.foretaksoppslag o
             WHERE o.tenant = p_tenant
               AND o.reservert > now() - interval '24 hours'),
           (SELECT count(*) FROM public.foretaksoppslag o
             WHERE o.tenant = p_tenant AND o.svarstatus = 'reservert'),
           EXISTS (SELECT 1 FROM public.motpartskrav k
                    WHERE k.tenant = p_tenant),
           (SELECT k.versjon FROM public.motpartskrav k
             WHERE k.tenant = p_tenant),
           public.m48_registrert_vert();
END $$;
REVOKE ALL ON FUNCTION m48_motpartsstatus(TEXT) FROM PUBLIC;


CREATE FUNCTION m48_kravene(p_tenant TEXT)
RETURNS TABLE (oppslag_ferskhet_timer INT, vurdering_gyldig_dogn INT,
               uvurdert_dogn INT, maks_forslag_ore BIGINT,
               godkjente_grunnlag TEXT[], versjon INT,
               oppdatert TIMESTAMPTZ, oppdatert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm48_kravene');
    RETURN QUERY
    SELECT k.oppslag_ferskhet_timer, k.vurdering_gyldig_dogn,
           k.uvurdert_dogn, k.maks_forslag_ore, k.godkjente_grunnlag,
           k.versjon, k.oppdatert, k.oppdatert_av
      FROM public.motpartskrav k WHERE k.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m48_kravene(TEXT) FROM PUBLIC;


CREATE FUNCTION m48_motpartene(p_tenant TEXT, p_grense INT)
RETURNS TABLE (motpart_id UUID, organisasjonsnummer TEXT,
               navn_oppgitt TEXT, aktiv BOOLEAN,
               opprettet TIMESTAMPTZ, siste_versjon TIMESTAMPTZ,
               siste_registerstatus TEXT, siste_vurdering TIMESTAMPTZ,
               siste_forslag_ore BIGINT, apne_funn BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm48_motpartene');
    IF p_grense IS NULL OR p_grense < 1 OR p_grense > 500 THEN
        RAISE EXCEPTION 'm48_motpartene: grensen må være 1..500 (%)',
            p_grense USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    SELECT s.motpart_id, s.organisasjonsnummer, s.navn_oppgitt,
           s.aktiv, s.opprettet,
           v.registrert, v.siste_status,
           u.vurdert, u.foreslatt_grense_ore,
           coalesce(f.antall, 0)
      FROM public.motpartssubjekt s
      LEFT JOIN LATERAL (
           SELECT mv.registrert,
                  mv.registerstatus AS siste_status,
                  mv.versjon_id
             FROM public.motpartsversjon mv
            WHERE mv.tenant = s.tenant AND mv.motpart_id = s.motpart_id
            ORDER BY mv.gjelder_fra DESC, mv.registrert DESC
            LIMIT 1) v ON true
      LEFT JOIN LATERAL (
           SELECT mu.vurdert, mu.foreslatt_grense_ore
             FROM public.motpartsvurdering mu
            WHERE mu.tenant = s.tenant AND mu.versjon_id = v.versjon_id
            ORDER BY mu.vurdert DESC
            LIMIT 1) u ON true
      LEFT JOIN LATERAL (
           SELECT count(*) AS antall FROM public.motpartsfunn mf
            WHERE mf.tenant = s.tenant AND mf.motpart_id = s.motpart_id
              AND mf.apen) f ON true
     WHERE s.tenant = p_tenant
     ORDER BY s.opprettet DESC
     LIMIT p_grense;
END $$;
REVOKE ALL ON FUNCTION m48_motpartene(TEXT, INT) FROM PUBLIC;


CREATE FUNCTION m48_versjonene(p_tenant TEXT, p_motpart_id UUID)
RETURNS TABLE (versjon_id UUID, oppslag_id UUID, kilde TEXT,
               kildeversjon TEXT, navn_registrert TEXT,
               organisasjonsform TEXT, registerstatus TEXT,
               konkurs BOOLEAN, under_tvangsavvikling BOOLEAN,
               gjelder_fra DATE, registrert TIMESTAMPTZ,
               registrert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm48_versjonene');
    RETURN QUERY
    SELECT v.versjon_id, v.oppslag_id, v.kilde, v.kildeversjon,
           v.navn_registrert, v.organisasjonsform, v.registerstatus,
           v.konkurs, v.under_tvangsavvikling, v.gjelder_fra,
           v.registrert, v.registrert_av
      FROM public.motpartsversjon v
     WHERE v.tenant = p_tenant AND v.motpart_id = p_motpart_id
     ORDER BY v.gjelder_fra DESC, v.registrert DESC;
END $$;
REVOKE ALL ON FUNCTION m48_versjonene(TEXT, UUID) FROM PUBLIC;


-- OPPSLAGSLOGGEN ER EN LESEDØR MED VILJE. Spørsmålet «hvilke
-- organisasjonsnumre har dere sendt ut, når, og med hvilken hjemmel»
-- skal kunne besvares av tenanten selv, ikke bare av oss.
CREATE FUNCTION m48_oppslagene(p_tenant TEXT, p_motpart_id UUID)
RETURNS TABLE (oppslag_id UUID, organisasjonsnummer TEXT, vert TEXT,
               formaal TEXT, hjemmel TEXT, svarstatus TEXT,
               svar_sha256 TEXT, reservert TIMESTAMPTZ,
               reservert_av TEXT, fullfort TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm48_oppslagene');
    RETURN QUERY
    SELECT o.oppslag_id, o.organisasjonsnummer, o.vert, o.formaal,
           o.hjemmel, o.svarstatus, o.svar_sha256, o.reservert,
           o.reservert_av, o.fullfort
      FROM public.foretaksoppslag o
     WHERE o.tenant = p_tenant AND o.motpart_id = p_motpart_id
     ORDER BY o.reservert DESC;
END $$;
REVOKE ALL ON FUNCTION m48_oppslagene(TEXT, UUID) FROM PUBLIC;


CREATE FUNCTION m48_vurderingene(p_tenant TEXT, p_motpart_id UUID)
RETURNS TABLE (vurdering_id UUID, versjon_id UUID, policyversjon INT,
               grunnlag TEXT, foreslatt_grense_ore BIGINT,
               begrunnelse TEXT, vurdert TIMESTAMPTZ, vurdert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm48_vurderingene');
    RETURN QUERY
    SELECT u.vurdering_id, u.versjon_id, u.policyversjon, u.grunnlag,
           u.foreslatt_grense_ore, u.begrunnelse, u.vurdert,
           u.vurdert_av
      FROM public.motpartsvurdering u
      JOIN public.motpartsversjon v
        ON v.tenant = u.tenant AND v.versjon_id = u.versjon_id
     WHERE u.tenant = p_tenant AND v.motpart_id = p_motpart_id
     ORDER BY u.vurdert DESC;
END $$;
REVOKE ALL ON FUNCTION m48_vurderingene(TEXT, UUID) FROM PUBLIC;


CREATE FUNCTION m48_funnene(p_tenant TEXT, p_bare_apne BOOLEAN)
RETURNS TABLE (motpart_id UUID, organisasjonsnummer TEXT,
               navn_oppgitt TEXT, funntype TEXT, over_grense INT,
               siste_registerstatus TEXT, siste_forslag_ore BIGINT,
               kravversjon INT, forst_sett TIMESTAMPTZ,
               sist_sett_sveip TIMESTAMPTZ, apen BOOLEAN,
               lukket_ts TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm48_funnene');
    RETURN QUERY
    SELECT f.motpart_id, s.organisasjonsnummer, s.navn_oppgitt,
           f.funntype, f.over_grense, f.siste_registerstatus,
           f.siste_forslag_ore, f.kravversjon, f.forst_sett,
           f.sist_sett_sveip, f.apen, f.lukket_ts
      FROM public.motpartsfunn f
      JOIN public.motpartssubjekt s
        ON s.tenant = f.tenant AND s.motpart_id = f.motpart_id
     WHERE f.tenant = p_tenant
       AND (NOT coalesce(p_bare_apne, true) OR f.apen)
     ORDER BY f.apen DESC, f.forst_sett DESC;
END $$;
REVOKE ALL ON FUNCTION m48_funnene(TEXT, BOOLEAN) FROM PUBLIC;


-- ------------------------------------------------------------
-- 5. Sveipen.
-- ------------------------------------------------------------

-- TENANTLISTA MATERIALISERES FØR LØKKA. 112s lærdom, og den er
-- lettere å gjøre feil enn å se: `FOR t IN SELECT ...` er en LAT
-- markør. `set_config('disponit.tenant', ...)` inne i løkka endrer
-- RLS-konteksten markøren fortsatt leser gjennom, så den ville sett
-- færre og færre tenanter for hver runde. Arrayet er hele fiksen.
CREATE FUNCTION m48_sveip_motparter(p_maks_tenanter INT)
RETURNS TABLE (tenanter INT, nye BIGINT, oppdaterte BIGINT,
               lukkede BIGINT, forlatte BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_tenanter TEXT[];
    v_t TEXT;
    v_antall INT := 0;
    v_nye BIGINT := 0;
    v_oppdaterte BIGINT := 0;
    v_lukket BIGINT := 0;
    v_forlatt BIGINT := 0;
    v_n BIGINT;
    -- EGEN VARIABEL, IKKE `v_oppdaterte` DIREKTE. `SELECT ... INTO`
    -- SETTER variabelen, den legger ikke til — så en `INTO v_oppdaterte`
    -- inne i tenantløkka ville rapportert siste tenants tall som hele
    -- flåtens. CodeRabbits funn i 112, gjentatt her med vilje.
    v_n2 BIGINT;
BEGIN
    IF p_maks_tenanter IS NULL OR p_maks_tenanter < 1 THEN
        RAISE EXCEPTION 'm48_sveip_motparter: maks_tenanter må være'
            ' minst 1 (%)', p_maks_tenanter
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.tenant', '', true);
    SELECT array_agg(DISTINCT s.tenant ORDER BY s.tenant)
      INTO v_tenanter
      FROM public.motpartssubjekt s;
    IF v_tenanter IS NULL THEN
        RETURN QUERY SELECT 0, 0::bigint, 0::bigint, 0::bigint,
                            0::bigint;
        RETURN;
    END IF;
    IF cardinality(v_tenanter) > p_maks_tenanter THEN
        v_tenanter := v_tenanter[1:p_maks_tenanter];
    END IF;

    FOREACH v_t IN ARRAY v_tenanter
    LOOP
        v_antall := v_antall + 1;
        PERFORM set_config('disponit.tenant', v_t, true);

        -- FORLATTE RESERVASJONER FØRST. De må lukkes før funnene
        -- regnes, ellers ville `oppslag_uten_svar` stått åpent i
        -- samme runde som den ble ryddet.
        --
        -- SEKS TIMER er ikke en policy, det er en tålegrense: en
        -- HTTP-forespørsel med tidsavbrudd på sekunder som fortsatt
        -- står som «reservert» seks timer senere, har ingen klient
        -- igjen som kan fylle den ut.
        WITH forlatt AS (
            UPDATE public.foretaksoppslag o
               SET svarstatus = 'forlatt', fullfort = now()
             WHERE o.tenant = v_t AND o.svarstatus = 'reservert'
               AND o.reservert < now() - interval '6 hours'
            RETURNING 1)
        SELECT count(*) INTO v_n FROM forlatt;
        v_forlatt := v_forlatt + coalesce(v_n, 0);

        WITH krav AS (
            SELECT k.uvurdert_dogn, k.vurdering_gyldig_dogn,
                   k.maks_forslag_ore, k.versjon
              FROM public.motpartskrav k WHERE k.tenant = v_t),
        -- Gjeldende profil og gjeldende vurdering per motpart. LATERAL
        -- fordi «den siste» er et per-motpart-spørsmål.
        siste AS (
            SELECT s.motpart_id, s.opprettet, v.versjon_id,
                   v.registerstatus, v.konkurs,
                   v.under_tvangsavvikling, v.registrert AS v_registrert,
                   u.vurdert, u.foreslatt_grense_ore
              FROM public.motpartssubjekt s
              LEFT JOIN LATERAL (
                   SELECT mv.versjon_id, mv.registerstatus, mv.konkurs,
                          mv.under_tvangsavvikling, mv.registrert
                     FROM public.motpartsversjon mv
                    WHERE mv.tenant = s.tenant
                      AND mv.motpart_id = s.motpart_id
                    ORDER BY mv.gjelder_fra DESC, mv.registrert DESC
                    LIMIT 1) v ON true
              LEFT JOIN LATERAL (
                   SELECT mu.vurdert, mu.foreslatt_grense_ore
                     FROM public.motpartsvurdering mu
                    WHERE mu.tenant = s.tenant
                      AND mu.versjon_id = v.versjon_id
                    ORDER BY mu.vurdert DESC
                    LIMIT 1) u ON true
             WHERE s.tenant = v_t AND s.aktiv),
        feiltelling AS (
            SELECT o.motpart_id, count(*) AS antall
              FROM public.foretaksoppslag o
             WHERE o.tenant = v_t
               AND o.svarstatus IN ('feil', 'avvist', 'forlatt')
               AND o.reservert > now() - interval '24 hours'
             GROUP BY o.motpart_id
            HAVING count(*) >= 5),
        kand AS (
            -- INGEN KRAV. Tenanten har motparter, men ingen policy —
            -- og uten policy kan verken vindu eller tak måles. Ett
            -- funn per motpart, som de andre, slik at lukking er
            -- individuell.
            SELECT s.motpart_id, 'ingen_krav'::text AS funntype,
                   NULL::int AS over_grense,
                   s.registerstatus AS siste_registerstatus,
                   s.foreslatt_grense_ore AS siste_forslag_ore,
                   NULL::int AS kravversjon
              FROM siste s
             WHERE NOT EXISTS (SELECT 1 FROM krav)

            UNION ALL
            -- UVURDERT. Ingen vurdering i det hele tatt, og motparten
            -- er eldre enn fristen.
            SELECT s.motpart_id, 'uvurdert_motpart',
                   (current_date - s.opprettet::date)
                   - k.uvurdert_dogn,
                   s.registerstatus, NULL::bigint, k.versjon
              FROM siste s CROSS JOIN krav k
             WHERE s.vurdert IS NULL
               AND s.opprettet
                   < now() - make_interval(days => k.uvurdert_dogn)

            UNION ALL
            -- UTDATERT. Det finnes en vurdering, men den er for gammel.
            SELECT s.motpart_id, 'utdatert_vurdering',
                   (current_date - s.vurdert::date)
                   - k.vurdering_gyldig_dogn,
                   s.registerstatus, s.foreslatt_grense_ore, k.versjon
              FROM siste s CROSS JOIN krav k
             WHERE s.vurdert IS NOT NULL
               AND s.vurdert < now()
                   - make_interval(days => k.vurdering_gyldig_dogn)

            UNION ALL
            -- NY PROFIL, INGEN NY VURDERING. Registeret har sagt noe
            -- nytt om motparten, og ingen har målt det mot policyen.
            SELECT s.motpart_id, 'profil_uten_vurdering', NULL::int,
                   s.registerstatus, NULL::bigint, k.versjon
              FROM siste s CROSS JOIN krav k
             WHERE s.versjon_id IS NOT NULL AND s.vurdert IS NULL

            UNION ALL
            -- AVVIKLET. Registeret sier at foretaket er på vei ut, og
            -- det er en opplysning noen må ta stilling til.
            SELECT s.motpart_id, 'motpart_avviklet', NULL::int,
                   s.registerstatus, s.foreslatt_grense_ore, k.versjon
              FROM siste s CROSS JOIN krav k
             WHERE s.registerstatus IN ('under_avvikling', 'avviklet',
                                        'slettet')
                OR s.konkurs OR s.under_tvangsavvikling

            UNION ALL
            -- FORSLAG OVER TAK. Måles, ikke nektes: nektet døra å
            -- lagre det, ville nettopp denne observasjonen forsvunnet.
            SELECT s.motpart_id, 'forslag_over_tak', NULL::int,
                   s.registerstatus, s.foreslatt_grense_ore, k.versjon
              FROM siste s CROSS JOIN krav k
             WHERE s.foreslatt_grense_ore IS NOT NULL
               AND s.foreslatt_grense_ore > k.maks_forslag_ore

            UNION ALL
            -- RESERVASJON UTEN SVAR. Etter ryddingen over står bare de
            -- ferske igjen — så dette funnet betyr «en forespørsel er i
            -- lufta lenger enn den burde», ikke «en er glemt».
            SELECT DISTINCT o.motpart_id, 'oppslag_uten_svar',
                   NULL::int, NULL::text, NULL::bigint,
                   (SELECT versjon FROM krav)
              FROM public.foretaksoppslag o
             WHERE o.tenant = v_t AND o.svarstatus = 'reservert'
               AND o.reservert < now() - interval '1 hour'

            UNION ALL
            -- GJENTATTE FEIL. Fem eller flere mislykkede forespørsler
            -- på ett døgn om samme motpart er ikke et uhell — det er
            -- enten et register som er nede eller en klient som maser,
            -- og begge deler er utgående trafikk vi lovte å måle.
            SELECT ft.motpart_id, 'gjentatte_oppslagsfeil',
                   ft.antall::int, NULL::text, NULL::bigint,
                   (SELECT versjon FROM krav)
              FROM feiltelling ft
        ),
        skrevet AS (
            INSERT INTO public.motpartsfunn
                (tenant, motpart_id, funntype, over_grense,
                 siste_registerstatus, siste_forslag_ore, kravversjon)
            SELECT v_t, k.motpart_id, k.funntype, k.over_grense,
                   k.siste_registerstatus, k.siste_forslag_ore,
                   k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, motpart_id, funntype) DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                siste_registerstatus = EXCLUDED.siste_registerstatus,
                siste_forslag_ore = EXCLUDED.siste_forslag_ore,
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
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);

        -- LUKKING: et åpent funn som ikke lenger er en kandidat, er
        -- løst. Akkumuleres over tenanter (112s retting: `INTO` SETTER
        -- variabelen, den legger ikke til).
        WITH krav AS (
            SELECT k.uvurdert_dogn, k.vurdering_gyldig_dogn,
                   k.maks_forslag_ore, k.versjon
              FROM public.motpartskrav k WHERE k.tenant = v_t),
        siste AS (
            SELECT s.motpart_id, s.opprettet, v.versjon_id,
                   v.registerstatus, v.konkurs,
                   v.under_tvangsavvikling,
                   u.vurdert, u.foreslatt_grense_ore
              FROM public.motpartssubjekt s
              LEFT JOIN LATERAL (
                   SELECT mv.versjon_id, mv.registerstatus, mv.konkurs,
                          mv.under_tvangsavvikling
                     FROM public.motpartsversjon mv
                    WHERE mv.tenant = s.tenant
                      AND mv.motpart_id = s.motpart_id
                    ORDER BY mv.gjelder_fra DESC, mv.registrert DESC
                    LIMIT 1) v ON true
              LEFT JOIN LATERAL (
                   SELECT mu.vurdert, mu.foreslatt_grense_ore
                     FROM public.motpartsvurdering mu
                    WHERE mu.tenant = s.tenant
                      AND mu.versjon_id = v.versjon_id
                    ORDER BY mu.vurdert DESC
                    LIMIT 1) u ON true
             WHERE s.tenant = v_t AND s.aktiv),
        feiltelling AS (
            SELECT o.motpart_id, count(*) AS antall
              FROM public.foretaksoppslag o
             WHERE o.tenant = v_t
               AND o.svarstatus IN ('feil', 'avvist', 'forlatt')
               AND o.reservert > now() - interval '24 hours'
             GROUP BY o.motpart_id
            HAVING count(*) >= 5),
        kand AS (
            SELECT s.motpart_id, 'ingen_krav'::text AS funntype
              FROM siste s WHERE NOT EXISTS (SELECT 1 FROM krav)
            UNION ALL
            SELECT s.motpart_id, 'uvurdert_motpart'
              FROM siste s CROSS JOIN krav k
             WHERE s.vurdert IS NULL
               AND s.opprettet
                   < now() - make_interval(days => k.uvurdert_dogn)
            UNION ALL
            SELECT s.motpart_id, 'utdatert_vurdering'
              FROM siste s CROSS JOIN krav k
             WHERE s.vurdert IS NOT NULL
               AND s.vurdert < now()
                   - make_interval(days => k.vurdering_gyldig_dogn)
            UNION ALL
            SELECT s.motpart_id, 'profil_uten_vurdering'
              FROM siste s CROSS JOIN krav k
             WHERE s.versjon_id IS NOT NULL AND s.vurdert IS NULL
            UNION ALL
            SELECT s.motpart_id, 'motpart_avviklet'
              FROM siste s CROSS JOIN krav k
             WHERE s.registerstatus IN ('under_avvikling', 'avviklet',
                                        'slettet')
                OR s.konkurs OR s.under_tvangsavvikling
            UNION ALL
            SELECT s.motpart_id, 'forslag_over_tak'
              FROM siste s CROSS JOIN krav k
             WHERE s.foreslatt_grense_ore IS NOT NULL
               AND s.foreslatt_grense_ore > k.maks_forslag_ore
            UNION ALL
            SELECT DISTINCT o.motpart_id, 'oppslag_uten_svar'
              FROM public.foretaksoppslag o
             WHERE o.tenant = v_t AND o.svarstatus = 'reservert'
               AND o.reservert < now() - interval '1 hour'
            UNION ALL
            SELECT ft.motpart_id, 'gjentatte_oppslagsfeil'
              FROM feiltelling ft
        ),
        lukket AS (
            UPDATE public.motpartsfunn f
               SET apen = false, lukket_ts = now()
             WHERE f.tenant = v_t AND f.apen
               AND NOT EXISTS (
                   SELECT 1 FROM kand k
                    WHERE k.motpart_id = f.motpart_id
                      AND k.funntype = f.funntype)
            RETURNING 1)
        SELECT count(*) INTO v_n FROM lukket;
        v_lukket := v_lukket + coalesce(v_n, 0);

        PERFORM set_config('disponit.tenant', '', true);
    END LOOP;

    RETURN QUERY SELECT v_antall, v_nye, v_oppdaterte, v_lukket,
                        v_forlatt;
END $$;
REVOKE ALL ON FUNCTION m48_sveip_motparter(INT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 6. RLS. ENABLE + FORCE på alle seks, `tenant_isolasjon` på hver.
-- ------------------------------------------------------------

RESET ROLE;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['motpartskrav', 'motpartssubjekt',
                             'foretaksoppslag', 'motpartsversjon',
                             'motpartsvurdering', 'motpartsfunn']
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
                       ' disponit_motpart_eier', t);
    END LOOP;
END $$;

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER (111s form, gjentatt i
-- 112/113/114): bare på SUBJEKTTABELLEN, bare FOR SELECT, bare til
-- eieren, og bare når ingen tenantkontekst står. Sveipen trenger
-- nøyaktig ett kryss-tenant-svar: HVILKE tenanter finnes. Alt annet
-- den leser, leser den inne i én tenants kontekst.
CREATE POLICY m48_sveip_tenantliste ON motpartssubjekt
    FOR SELECT TO disponit_motpart_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- HISTORIKKTABELLENE FÅR IKKE UPDATE, HELLER IKKE FOR EIEREN. Vaktene
-- i dørene stanser den som skulle prøve; dette gjerdet stanser forsøket
-- før det når vakten. To gjerder, av samme grunn som i 110–114.
--
-- `foretaksoppslag` ER IKKE MED I LISTA, og det er en bevisst åpning:
-- `m48_fullfor_oppslag` og sveipens rydding av forlatte reservasjoner
-- må kunne skrive svaret. Åpningen lukkes fra den andre siden av
-- radvakten under, som nekter enhver endring av en rad som alt er
-- fullført — så den ene lovlige overgangen er 'reservert' → et svar.
REVOKE UPDATE ON public.motpartsversjon FROM disponit_motpart_eier;
REVOKE UPDATE ON public.motpartsvurdering FROM disponit_motpart_eier;

-- RADVAKTEN PÅ OPPSLAGSLOGGEN. Den frosne delen av en tabell som må
-- være delvis skrivbar.
CREATE FUNCTION m48_oppslag_frosset()
RETURNS TRIGGER LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF OLD.svarstatus <> 'reservert' THEN
        RAISE EXCEPTION 'foretaksoppslag: rad % er fullført med «%»'
            ' og er frosset — et svar overskrives ikke',
            OLD.oppslag_id, OLD.svarstatus
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Bare svarfeltene kan settes. Alt som beskriver FORESPØRSELEN —
    -- hvem, om hvem, hvorfor, mot hvilken vert — er skrevet før den
    -- gikk ut, og skal ikke kunne skrives om etterpå.
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.oppslag_id IS DISTINCT FROM OLD.oppslag_id
       OR NEW.motpart_id IS DISTINCT FROM OLD.motpart_id
       OR NEW.organisasjonsnummer
          IS DISTINCT FROM OLD.organisasjonsnummer
       OR NEW.vert IS DISTINCT FROM OLD.vert
       OR NEW.formaal IS DISTINCT FROM OLD.formaal
       OR NEW.hjemmel IS DISTINCT FROM OLD.hjemmel
       OR NEW.reservert IS DISTINCT FROM OLD.reservert
       OR NEW.reservert_av IS DISTINCT FROM OLD.reservert_av THEN
        RAISE EXCEPTION 'foretaksoppslag: forespørselens egne felter er'
            ' frosset — bare svaret kan fylles inn'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER foretaksoppslag_frosset
    BEFORE UPDATE ON foretaksoppslag
    FOR EACH ROW EXECUTE FUNCTION m48_oppslag_frosset();

-- SLETTING ER ALDRI LOVLIG PÅ NOEN AV DEM. Ingen rolle får DELETE, og
-- eieren ba aldri om det — men et REVOKE som står, er et REVOKE noen
-- kan lese.
REVOKE DELETE ON public.motpartskrav FROM disponit_motpart_eier;
REVOKE DELETE ON public.motpartssubjekt FROM disponit_motpart_eier;
REVOKE DELETE ON public.foretaksoppslag FROM disponit_motpart_eier;
REVOKE DELETE ON public.motpartsversjon FROM disponit_motpart_eier;
REVOKE DELETE ON public.motpartsvurdering FROM disponit_motpart_eier;
REVOKE DELETE ON public.motpartsfunn FROM disponit_motpart_eier;

-- ------------------------------------------------------------
-- 7. Rettighetene. SP-7: kjøretidsrollen får INGEN tabellrettigheter.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_motpart_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m48_motpartsstatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m48_kravene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m48_motpartene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m48_versjonene(TEXT, UUID) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m48_oppslagene(TEXT, UUID) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m48_vurderingene(TEXT, UUID) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m48_funnene(TEXT, BOOLEAN) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m48_sett_krav(TEXT, INT, INT, INT, BIGINT, TEXT[], TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m48_registrer_motpart(TEXT, UUID, TEXT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m48_deaktiver_motpart(TEXT, UUID, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m48_reserver_oppslag(TEXT, UUID, UUID, TEXT, TEXT, TEXT,'
            ' TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m48_fullfor_oppslag(TEXT, UUID, TEXT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m48_registrer_versjon(TEXT, UUID, UUID, UUID, TEXT,'
            ' TEXT, TEXT, TEXT, TEXT, BOOLEAN, BOOLEAN, DATE, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m48_registrer_vurdering(TEXT, UUID, UUID, TEXT, BIGINT,'
            ' TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m48_lukk_funn(TEXT, UUID, TEXT, TEXT, TEXT)'
            ' TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_motpartssveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m48_sveip_motparter(INT)'
            ' TO disponit_motpartssveip';
    END IF;
END $$;

-- SVEIPEN ER IKKE KJØRETIDSROLLENS.
REVOKE EXECUTE ON FUNCTION m48_sveip_motparter(INT) FROM disponit;

RESET ROLE;
