-- 117: M-49 sanksjonskontroll v1 — KONTROLLEN, IKKE BLOKKERINGEN.
-- Seks tenant-skopede tabeller, seksten dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- KLYNGENS VANSKELIGSTE MODUL, OG DEN ENESTE DER SPESIFIKASJONEN VIL
-- AT MODULEN SKAL HANDLE.
--
-- De fire andre i klynge 6 holder tilbake en utgående handling —
-- «sender aldri inn tilbud», «setter aldri kredittgrensen selv»,
-- «sender aldri inn søknad», «sender aldri juridiske krav». Her sier
-- vakten det motsatte: «treff blokkerer fail-closed og løses kun av
-- menneske». Og samtidig: «navnelikhet er aldri automatisk avfeid».
--
-- DE TO SAMMEN ER PROBLEMET. Navnelikhet mot sanksjonslister gir
-- STORE mengder kandidater — «Mohammed Ali» treffer mange — og ingen
-- av dem kan lukkes maskinelt. En modul som blokkerte automatisk på
-- det grunnlaget ville stanset LOVLIG HANDEL fra første natt, uten at
-- noen hadde målt hvor ofte den tar feil.
--
-- BESLUTNINGEN (eier delegerte 3/9): v1 BLOKKERER IKKE.
--
-- Den tyngste grunnen er ikke falsk-positiv-raten. Den er at DET IKKE
-- FINNES NOE Å BLOKKERE MED. Et register stanser ingen handel; det
-- måtte M-23, M-14 eller M-42 spurt registeret FØR de handlet, og den
-- koblingen finnes ikke i v1. «Blokkering» ville i praksis vært å
-- skrive et flagg ingen leser — nøyaktig `alarm`-feltet fra 115, som
-- så ut som vern i to klynger uten å være det. I en etterlevelses-
-- kontroll er den feilen verre enn et ærlig hull: hullet blir
-- rapportert som hull, teateret blir rapportert som dekning.
--
-- Og: Å IKKE BLOKKERE ER INGEN FORVERRING. I dag finnes ingen
-- sanksjonskontroll i det hele tatt. v1 gjør situasjonen strengt bedre
-- ved å produsere den første målingen. Argumentet «uten blokkering er
-- vi eksponert» sammenligner v1 med en ferdig modul, ikke med i dag.
--
-- MOTARGUMENTET, ÆRLIG SAGT: sanksjonsbrudd har objektivt ansvar i
-- flere jurisdiksjoner — det holder ikke å ha ment godt. Det trekker
-- mot fail-closed fra dag én, og det er grunnen til at
-- spesifikasjonen ber om det. Det endrer likevel ikke v1, fordi en
-- blokkering uten konsument ikke reduserer det ansvaret; den skjuler
-- bare at kontrollen ikke er koblet til noe.
--
-- UTLØSEREN, SÅ BESLUTNINGEN IKKE BLIR LIGGENDE: den dagen den FØRSTE
-- handlende modulen spør sanksjonsregisteret, skrus blokkeringen på —
-- men kun for EKSAKT IDENTIFIKATORTREFF (organisasjonsnummer eller
-- nasjonal ID mot listeversjon), aldri for navnelikhet.
--
-- DERFOR ER DATAMODELLEN FORMET ETTER DEN PÅSKRUINGEN.
-- `sanksjonstreff.matchtype` er et lukket sett med TRE verdier som
-- skiller nettopp der grensen kommer til å gå, og `matchfelt` sier
-- HVILKE felter som traff. Den dagen noen skrur på blokkering, er det
-- en policyrad og en konsument — ikke et nytt datamodellarbeid, og
-- ikke en migrering av treff som aldri ble klassifisert.
--
-- DOMMENE v1 HVILER PÅ, HÅNDHEVET I DATAMODELLEN:
--
--   1. HISTORIKKEN OVERSKRIVES ALDRI. Kontroller, treff og avklaringer
--      er append-only. M-42s dom (110), gjentatt i 112–116.
--
--   2. ET TREFF UTEN LISTEVERSJON ER UBRUKELIG I ETTERTID. «Sto de på
--      lista den dagen» er hele spørsmålet et tilsyn stiller, og en
--      liste som oppdateres på stedet ville slettet svaret.
--      `sanksjonsliste` er derfor frosset, og hver kontroll peker på
--      NØYAKTIG én listeversjon.
--
--   3. NAVNELIKHET AVFEIES ALDRI AV MASKINEN. Det finnes ingen dør som
--      lukker et `navnelikhet`-treff uten en avklaring med aktør og
--      begrunnelse. Fraværet ER porten `modulen_avfeide_navnelikhet`.
--
--   4. INGEN KOLONNE FOR «BLOKKERT». Hadde skjemaet hatt et flagg for
--      det, ville fullmakten vært bygget allerede — og det er bare et
--      spørsmål om tid før noe skriver til den. Fraværet ER porten
--      `modulen_blokkerte_motpart`.
--
--   5. MATCHTERSKELEN ER TENANTENS. Hvor lik en streng må være for å
--      bli et treff er en risikoavveining, ikke en konstant.
--
-- GRENSEN MOT M-48: M-48 eier MOTPARTEN som forretningsforbindelse.
-- M-49 eier spørsmålet om den samme parten står på en liste. v1
-- kobler dem ikke — koblingen er nettopp fullmakten vi ikke gir.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_sanksjon_eier') THEN
        RAISE EXCEPTION 'rollen disponit_sanksjon_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_sanksjon_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `sanksjonskrav` — ÉN per tenant. DOM 5: TERSKELEN ER TENANTENS.
CREATE TABLE sanksjonskrav (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- HVOR LIK EN STRENG MÅ VÆRE FOR Å BLI ET TREFF, i hele prosent.
    -- En risikoavveining, ikke en konstant: en bank vil ha lavere
    -- terskel enn en nettbutikk, og begge skal kunne begrunne sitt
    -- valg overfor et tilsyn.
    --
    -- 100 BETYR IKKE «BARE EKSAKTE TREFF». Eksakte treff finnes
    -- uansett terskel — de er en egen `matchtype`. Terskelen styrer
    -- kun hvor mange NAVNELIKHETER som registreres, og en tenant som
    -- setter 100 sier «vis meg bare det som er identisk», ikke «slå
    -- av kontrollen».
    matchterskel INT NOT NULL DEFAULT 85
        CHECK (matchterskel BETWEEN 50 AND 100),
    -- Hvor lenge en kontroll regnes som gyldig. Lister endres; en
    -- kontroll fra i fjor sier ingenting om lista i dag.
    kontroll_gyldig_dogn INT NOT NULL DEFAULT 90
        CHECK (kontroll_gyldig_dogn BETWEEN 1 AND 3650),
    -- Hvor lenge et UAVKLART treff kan stå før det er et funn. Dette
    -- er modulens mest betente frist: et treff ingen har sett på er
    -- ikke et vern, det er en udokumentert risiko.
    uavklart_frist_dogn INT NOT NULL DEFAULT 3
        CHECK (uavklart_frist_dogn BETWEEN 0 AND 365),
    -- Hvor lenge et subjekt kan stå UKONTROLLERT før det er et funn.
    ukontrollert_dogn INT NOT NULL DEFAULT 30
        CHECK (ukontrollert_dogn BETWEEN 0 AND 3650),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT sanksjonskrav_pk PRIMARY KEY (tenant)
);

-- `sanksjonsliste` — DOM 2. HVILKEN LISTE, I HVILKEN VERSJON.
--
-- FROSSET, og det er hele poenget. Spørsmålet et tilsyn stiller er
-- «sto de på lista DEN DAGEN», og en liste som oppdateres på stedet
-- ville slettet svaret. Hver kontroll peker på nøyaktig én rad her.
--
-- v1 LASTER INGEN LISTE SELV. Raden registreres av et menneske som
-- har lastet den ned, med kilde, versjon og innholdssum. Det er
-- porten `modulen_hentet_eksternt`: M-48 fikk unntaket fra klyngens
-- «ingen utgående forespørsel», M-49 fikk det ikke.
CREATE TABLE sanksjonsliste (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    liste_id UUID NOT NULL,
    -- Lukket sett: de tre regimene spesifikasjonen navngir. En ukjent
    -- kilde er en feil, ikke en åpning — en «liste» ingen vet hvor
    -- kommer fra er ikke et grunnlag å avvise handel på.
    kilde TEXT NOT NULL
        CONSTRAINT sanksjonsliste_kilde_lukket CHECK (kilde IN (
            'ofac', 'eu', 'fn')),
    -- Utgivers egen versjonsbetegnelse. FRI TEKST med formkrav: de tre
    -- regimene versjonerer forskjellig, og en modul som krevde ETT
    -- format ville nektet å registrere en ekte liste.
    listeversjon TEXT NOT NULL CHECK (listeversjon ~ '[^[:space:]]'),
    -- Datoen utgiver sier lista gjelder fra.
    gjelder_fra DATE NOT NULL,
    -- Innholdsadressen til fila slik den ble lastet ned. Uten den kan
    -- ingen etterpå vise at det var NØYAKTIG denne lista som ble brukt.
    innhold_sha256 TEXT NOT NULL
        CHECK (innhold_sha256 ~ '^[0-9a-f]{64}$'),
    antall_oppforinger INT NOT NULL
        CHECK (antall_oppforinger >= 0),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT sanksjonsliste_pk PRIMARY KEY (tenant, liste_id),
    -- SAMME KILDE OG VERSJON REGISTRERES ÉN GANG. En import som kjøres
    -- to ganger er ikke to lister.
    CONSTRAINT sanksjonsliste_versjon_unik
        UNIQUE (tenant, kilde, listeversjon)
);
CREATE INDEX sanksjonsliste_nyeste
    ON sanksjonsliste (tenant, kilde, gjelder_fra DESC,
                       registrert DESC);

-- `sanksjonssubjekt` — den vi kontrollerer. Identitetsraden, og den
-- ENESTE muterbare tabellen i modulen (`aktiv`).
--
-- DEN ER OGSÅ LÅSERADEN (M-42s lærdom, 110): `SELECT ... FOR UPDATE`
-- krever UPDATE-rett, og de frosne tabellene har den ikke.
--
-- IDENTIFIKATOREN ER VALGFRI, OG DET ER MODULENS VIKTIGSTE FELT.
-- Har vi et organisasjonsnummer eller en nasjonal ID, kan et treff bli
-- EKSAKT — den ene klassen som en dag kan blokkere automatisk. Har vi
-- bare et navn, kan treffet aldri bli mer enn en navnelikhet, uansett
-- hvor likt det ser ut. Å late som forskjellen ikke finnes er nettopp
-- feilen som ville stanset lovlig handel.
CREATE TABLE sanksjonssubjekt (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    subjekt_id UUID NOT NULL,
    -- Tenantens egen referanse. FRI TEKST og ingen fremmednøkkel:
    -- sanksjonshistorikken skal kunne stå alene, og v1 kobler seg
    -- ikke til M-48 (grensen i toppen).
    ekstern_ref TEXT NOT NULL CHECK (ekstern_ref ~ '[^[:space:]]'),
    -- Navnet SLIK DET BLE OPPGITT. Aldri rørt (112s dom): blander man
    -- oppgitt og normalisert form, kan ingen etterpå se om et treff
    -- skyldtes det tenanten skrev eller det vi gjorde med det.
    navn_oppgitt TEXT NOT NULL CHECK (navn_oppgitt ~ '[^[:space:]]'),
    -- HVA VI SAMMENLIGNER PÅ. Regnes i basen av `m49_normaliser`, som
    -- slår sammen mellomrom og senker bokstavstørrelse — og gjør
    -- INGENTING annet. Ingen translitterering, ingen navnebytte, ingen
    -- gjetning: en normalisering som GJETTER er en match i forkledning.
    navn_normalisert TEXT NOT NULL,
    -- Fysisk person eller foretak. Avgjør hvilke identifikatorer som
    -- gir mening, og hvilke felter et eksakt treff kan hvile på.
    subjekttype TEXT NOT NULL
        CONSTRAINT sanksjonssubjekt_type_lukket CHECK (subjekttype IN (
            'person', 'foretak')),
    -- ISO 3166-1 alfa-2, eller NULL når landet ikke er kjent. NULL er
    -- et ærlig svar; 'XX' ville vært en oppdiktet opplysning.
    land TEXT CHECK (land IS NULL OR land ~ '^[A-Z]{2}$'),
    -- Fødselsdato for personer. Skiller to like navn, og er derfor et
    -- av feltene et eksakt treff kan hvile på.
    fodselsdato DATE,
    -- ORGANISASJONSNUMMER ELLER NASJONAL ID. NULL når vi ikke har den
    -- — og da kan et treff aldri bli `eksakt_identifikator`.
    identifikator TEXT
        CHECK (identifikator IS NULL
               OR length(btrim(identifikator)) >= 4),
    aktiv BOOLEAN NOT NULL DEFAULT true,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT sanksjonssubjekt_pk PRIMARY KEY (tenant, subjekt_id),
    CONSTRAINT sanksjonssubjekt_ref_unik UNIQUE (tenant, ekstern_ref),
    -- En fødselsdato på et FORETAK er en forveksling, ikke en
    -- opplysning.
    CONSTRAINT sanksjonssubjekt_dato_bare_person CHECK (
        subjekttype = 'person' OR fodselsdato IS NULL)
);
CREATE INDEX sanksjonssubjekt_aktive
    ON sanksjonssubjekt (tenant) WHERE aktiv;

-- `sanksjonskontroll` — DOM 1 OG 2. HVA VI SPURTE OM, MOT HVILKEN
-- LISTE, OG HVA SOM KOM UT.
--
-- FROSSET. Nøklet på subjektet OG listeversjonen: en kontroll gjelder
-- den lista som sto da den ble gjort. Kommer en ny listeversjon i
-- morgen, er gårsdagens kontroll fortsatt sann om gårsdagens liste —
-- og sier ingenting om dagens. Det er nettopp den forskjellen en
-- fail-closed-blokkering ville stått og falt på.
CREATE TABLE sanksjonskontroll (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kontroll_id UUID NOT NULL,
    subjekt_id UUID NOT NULL,
    liste_id UUID NOT NULL,
    -- MATCHGRUNNLAGET (porten `kontroll_uten_matchgrunnlag`): hvilken
    -- terskel som gjaldt, og hvilke felter kontrollen faktisk
    -- sammenlignet. Uten begge kan ingen etterpå skille «lista var
    -- slik» fra «vi lette feil sted».
    matchterskel INT NOT NULL CHECK (matchterskel BETWEEN 50 AND 100),
    sammenlignede_felt TEXT[] NOT NULL
        CHECK (cardinality(sammenlignede_felt) > 0),
    kravversjon INT NOT NULL CHECK (kravversjon >= 1),
    -- Utfallet AV KONTROLLEN, ikke av vurderingen. `treff` betyr «det
    -- kom kandidater ut», ikke «personen er sanksjonert» — den
    -- forskjellen er hele modulen.
    utfall TEXT NOT NULL
        CONSTRAINT sanksjonskontroll_utfall_lukket CHECK (utfall IN (
            'ingen_treff', 'treff')),
    antall_treff INT NOT NULL CHECK (antall_treff >= 0),
    kontrollert TIMESTAMPTZ NOT NULL DEFAULT now(),
    kontrollert_av TEXT NOT NULL
        CHECK (kontrollert_av ~ '[^[:space:]]'),
    CONSTRAINT sanksjonskontroll_pk PRIMARY KEY (tenant, kontroll_id),
    CONSTRAINT sanksjonskontroll_subjekt_fk
        FOREIGN KEY (tenant, subjekt_id)
        REFERENCES sanksjonssubjekt (tenant, subjekt_id),
    CONSTRAINT sanksjonskontroll_liste_fk
        FOREIGN KEY (tenant, liste_id)
        REFERENCES sanksjonsliste (tenant, liste_id),
    -- Utfallet og tellingen skal aldri kunne si hver sin ting.
    CONSTRAINT sanksjonskontroll_telling_stemmer CHECK (
        (utfall = 'treff') = (antall_treff > 0))
);
CREATE INDEX sanksjonskontroll_oppslag
    ON sanksjonskontroll (tenant, subjekt_id, kontrollert DESC);

-- `sanksjonstreff` — TABELLEN HELE BESLUTNINGEN HVILER PÅ.
--
-- v1 blokkerer ikke. Utløseren for at den en dag skal gjøre det, er at
-- den første handlende modulen spør registeret — og da skrus
-- blokkeringen på KUN for eksakt identifikatortreff, aldri for
-- navnelikhet.
--
-- DERFOR ER `matchtype` ET LUKKET SETT MED TRE VERDIER SOM SKILLER
-- NØYAKTIG DER GRENSEN KOMMER TIL Å GÅ:
--
--   `eksakt_identifikator` — organisasjonsnummer eller nasjonal ID
--     stemmer mot listeoppføringen. Den ENESTE klassen som noen gang
--     skal kunne blokkere maskinelt: to foretak deler ikke
--     organisasjonsnummer.
--
--   `eksakt_navn` — navnet er identisk etter normalisering, men vi har
--     ingen identifikator å bekrefte det med. Ser sikkert ut og er det
--     ikke: «Mohammed Ali» er identisk med mange mennesker.
--
--   `navnelikhet` — under 100 % likhet, over tenantens terskel.
--
-- Skillet lagres PER TREFF og ikke utledes i ettertid, og det er
-- forskjellen på en policyrad og et migreringsprosjekt: treff som
-- aldri ble klassifisert kan ingen klassifisere i etterkant, fordi
-- lista de ble målt mot kan være borte.
--
-- FROSSET. Et treff er en observasjon, ikke en tilstand.
CREATE TABLE sanksjonstreff (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    treff_id UUID NOT NULL,
    kontroll_id UUID NOT NULL,
    matchtype TEXT NOT NULL
        CONSTRAINT sanksjonstreff_matchtype_lukket CHECK (
            matchtype IN ('eksakt_identifikator', 'eksakt_navn',
                          'navnelikhet')),
    -- HVILKE FELTER SOM TRAFF. Uten dette er `matchtype` en påstand
    -- ingen kan etterprøve — og porten `kontroll_uten_matchgrunnlag`
    -- måler nettopp at grunnlaget står på raden.
    matchfelt TEXT[] NOT NULL CHECK (cardinality(matchfelt) > 0),
    -- Likhetsgraden i hele prosent. 100 for begge de eksakte typene.
    likhet INT NOT NULL CHECK (likhet BETWEEN 0 AND 100),
    -- Oppføringen slik den står PÅ LISTA, kopiert inn. Ikke en peker:
    -- lista er en fil vi ikke eier, og en peker til en rad i den ville
    -- vært en referanse til noe som kan forsvinne.
    listenavn TEXT NOT NULL CHECK (listenavn ~ '[^[:space:]]'),
    liste_referanse TEXT NOT NULL
        CHECK (liste_referanse ~ '[^[:space:]]'),
    liste_program TEXT,
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT sanksjonstreff_pk PRIMARY KEY (tenant, treff_id),
    CONSTRAINT sanksjonstreff_kontroll_fk
        FOREIGN KEY (tenant, kontroll_id)
        REFERENCES sanksjonskontroll (tenant, kontroll_id),
    -- ET EKSAKT TREFF HAR 100 % LIKHET. Alt annet ville vært en
    -- selvmotsigelse på raden — og nettopp den raden en framtidig
    -- blokkering skal kunne stole på.
    CONSTRAINT sanksjonstreff_eksakt_er_hundre CHECK (
        matchtype = 'navnelikhet' OR likhet = 100),
    -- …og en NAVNELIKHET er per definisjon UNDER 100. Var den 100,
    -- er den `eksakt_navn`, og skillet ville vært utvisket.
    CONSTRAINT sanksjonstreff_likhet_under_hundre CHECK (
        matchtype <> 'navnelikhet' OR likhet < 100),
    -- ET IDENTIFIKATORTREFF MÅ HA IDENTIFIKATOREN BLANT MATCHFELTENE.
    -- Uten denne kunne den ene klassen som en dag skal kunne blokkere
    -- automatisk, settes på et navn.
    CONSTRAINT sanksjonstreff_identifikator_er_belagt CHECK (
        matchtype <> 'eksakt_identifikator'
        OR 'identifikator' = ANY (matchfelt))
);
CREATE INDEX sanksjonstreff_kontroll
    ON sanksjonstreff (tenant, kontroll_id, matchtype);

-- `sanksjonsavklaring` — DOM 3. MENNESKET SOM TOK STILLING.
--
-- ET TREFF LUKKES ALDRI AV MASKINEN. Det finnes ingen dør som
-- avklarer et treff uten en aktør og en begrunnelse, og fraværet ER
-- porten `modulen_avfeide_navnelikhet`.
--
-- Append-only: en avklaring som kunne skrives om ville gjort «hvem
-- sa hva, når» til et åpent spørsmål — og det er nøyaktig spørsmålet
-- et tilsyn stiller etter et sanksjonsbrudd.
--
-- KONKLUSJONEN ER TRE VERDIER, IKKE TO. «Bekreftet» og «ikke samme
-- part» er de opplagte; `uavklart_eskalert` er den ærlige tredje: en
-- saksbehandler som IKKE klarer å avgjøre skal kunne si det, i stedet
-- for å velge en av de to for å bli ferdig. En modul som bare tilbød
-- ja og nei ville presset fram gjetninger og kalt dem avklaringer.
CREATE TABLE sanksjonsavklaring (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    avklaring_id UUID NOT NULL,
    treff_id UUID NOT NULL,
    konklusjon TEXT NOT NULL
        CONSTRAINT sanksjonsavklaring_konklusjon_lukket CHECK (
            konklusjon IN ('bekreftet_treff', 'ikke_samme_part',
                           'uavklart_eskalert')),
    -- PÅKREVD, og minstelengden er ikke pynt: «ok» er ikke en
    -- begrunnelse for å slippe en sanksjonert part gjennom.
    begrunnelse TEXT NOT NULL CHECK (length(btrim(begrunnelse)) >= 12),
    avklart TIMESTAMPTZ NOT NULL DEFAULT now(),
    avklart_av TEXT NOT NULL CHECK (avklart_av ~ '[^[:space:]]'),
    CONSTRAINT sanksjonsavklaring_pk PRIMARY KEY (tenant, avklaring_id),
    CONSTRAINT sanksjonsavklaring_treff_fk
        FOREIGN KEY (tenant, treff_id)
        REFERENCES sanksjonstreff (tenant, treff_id),
    -- ÉN GJELDENDE AVKLARING PER TREFF. Historikken beholdes ved at
    -- raden aldri endres; en NY vurdering av samme treff er en ny
    -- kontroll, ikke en overskriving av gårsdagens dom.
    CONSTRAINT sanksjonsavklaring_treff_unik UNIQUE (tenant, treff_id)
);
CREATE INDEX sanksjonsavklaring_oppslag
    ON sanksjonsavklaring (tenant, avklart DESC);

-- `sanksjonsfunn` — 112s gjenbruksform: nøklet på (tenant, subjekt,
-- funntype) og GJENBRUKES, så «antall åpne funn» måler antall
-- problemer og ikke antall netter.
CREATE TABLE sanksjonsfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    subjekt_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CONSTRAINT sanksjonsfunn_type_lukket CHECK (funntype IN (
            'uavklart_treff',        -- treff eldre enn fristen
            'ukontrollert_subjekt',  -- aldri kontrollert innen fristen
            'kontroll_utlopt',       -- eldre enn gyldighetsvinduet
            'kontroll_mot_gammel_liste',  -- nyere listeversjon finnes
            'bekreftet_treff',       -- et menneske sa ja
            'ingen_liste',           -- tenanten har ingen listeversjon
            'ingen_krav')),          -- tenanten har ingen policy
    over_grense INT,
    -- Den siste kontrollen og det groveste treffet, når funnet handler
    -- om dem. Et funn uten det ene faktumet som forklarer det tvinger
    -- leseren til å slå opp selv (112s dom).
    siste_matchtype TEXT,
    siste_utfall TEXT,
    kravversjon INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT sanksjonsfunn_pk PRIMARY KEY (tenant, subjekt_id,
                                             funntype),
    CONSTRAINT sanksjonsfunn_subjekt_fk FOREIGN KEY (tenant, subjekt_id)
        REFERENCES sanksjonssubjekt (tenant, subjekt_id),
    CONSTRAINT sanksjonsfunn_lukking_helhet CHECK (
        (apen AND lukket_ts IS NULL)
     OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE INDEX sanksjonsfunn_apne
    ON sanksjonsfunn (tenant, funntype) WHERE apen;


-- ------------------------------------------------------------
-- 2. Evidenskjeden. SECURITY DEFINER, eid av
--    `disponit_sanksjon_eier`, SP-1.
-- ------------------------------------------------------------

-- Eieren trenger å kunne kalle SP-1-vakten og å skrive evidens.
-- `krev_tenantkontekst` eies av `disponit_m37_claimer` (111s form), så
-- EXECUTE må gis AV den rollen — en GRANT fra migratoren er en no-op
-- med en advarsel, ikke en feil, og det er nettopp slik en manglende
-- rettighet blir usynlig til første kall (116s lærdom).
GRANT INSERT ON revisjonslogg TO disponit_sanksjon_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_sanksjon_eier;
RESET ROLE;

-- HERFRA OG TIL SEKSJON 6 EIES ALT SOM LAGES AV SANKSJONSEIEREN.
-- Dørene er SECURITY DEFINER, så eierskapet ER fullmakten de kjører
-- med — lages de av migratoren, kjører de med migratorens rettigheter
-- og kryss-tenant-policyen i seksjon 6 treffer dem aldri (116).
SET LOCAL ROLE disponit_sanksjon_eier;

CREATE FUNCTION m49_evidens(p_tenant TEXT, p_subjekt_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm49_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm49_sanksjon', 'handling', p_handling,
        'subjekt_id', p_subjekt_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm49_sanksjon',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:sanksjon', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m49_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;


-- ------------------------------------------------------------
-- 2a. Normaliseringen.
-- ------------------------------------------------------------

-- HVA VI SAMMENLIGNER PÅ, OG INGENTING MER.
--
-- Slår sammen mellomrom og senker bokstavstørrelse. Den
-- translittererer ikke, bytter ikke navnerekkefølge, fjerner ikke
-- mellomnavn og gjetter ikke på stavemåter.
--
-- DET ER EN BEVISST BEGRENSNING, IKKE EN MANGEL. En normalisering som
-- GJETTER er en match i forkledning: gjør den «Mohamed» om til
-- «Mohammed», har den tatt en beslutning ingen kan etterprøve — og
-- den beslutningen ville stått mellom en person og retten til å
-- handle. Slike omforminger hører hjemme som EGNE treff med sin egen
-- `matchtype`, ikke skjult inne i sammenligningsgrunnlaget.
--
-- IMMUTABLE: samme inn gir samme ut, alltid. Det er også det som gjør
-- den trygg å bruke i en generert kolonne om noen senere vil ha det.
CREATE FUNCTION m49_normaliser(p_tekst TEXT)
RETURNS TEXT LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT lower(btrim(regexp_replace(coalesce(p_tekst, ''),
                                      '\s+', ' ', 'g')))
$$;
GRANT EXECUTE ON FUNCTION m49_normaliser(TEXT) TO PUBLIC;


-- ------------------------------------------------------------
-- 3. Skrivedørene.
-- ------------------------------------------------------------

-- DOM 5: TERSKELEN ER TENANTENS. Hver endring hever `versjon`, og
-- hver kontroll skriver ned hvilken versjon som gjaldt.
CREATE FUNCTION m49_sett_krav(
    p_tenant TEXT, p_matchterskel INT, p_kontroll_gyldig_dogn INT,
    p_uavklart_frist_dogn INT, p_ukontrollert_dogn INT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm49_sett_krav');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    INSERT INTO public.sanksjonskrav
        (tenant, matchterskel, kontroll_gyldig_dogn,
         uavklart_frist_dogn, ukontrollert_dogn, oppdatert_av)
    VALUES (p_tenant, p_matchterskel, p_kontroll_gyldig_dogn,
            p_uavklart_frist_dogn, p_ukontrollert_dogn, p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        matchterskel = EXCLUDED.matchterskel,
        kontroll_gyldig_dogn = EXCLUDED.kontroll_gyldig_dogn,
        uavklart_frist_dogn = EXCLUDED.uavklart_frist_dogn,
        ukontrollert_dogn = EXCLUDED.ukontrollert_dogn,
        versjon = public.sanksjonskrav.versjon + 1,
        oppdatert = now(),
        oppdatert_av = EXCLUDED.oppdatert_av
    RETURNING versjon INTO v_versjon;

    -- Kravet gjelder HELE tenanten: NULL er det ærlige svaret på
    -- «hvilket subjekt», ikke et manglende felt.
    PERFORM public.m49_evidens(p_tenant, NULL, 'sanksjonskrav_satt',
        p_aktor, jsonb_build_object('versjon', v_versjon,
                                    'matchterskel', p_matchterskel));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION m49_sett_krav(TEXT, INT, INT, INT, INT, TEXT)
    FROM PUBLIC;


-- DOM 2. Registrerer at en listeversjon er lastet ned og tatt i bruk.
--
-- MODULEN LASTER DEN IKKE SELV. Et menneske har hentet fila, og
-- oppgir kilde, versjon, dato og innholdssum. Det er porten
-- `modulen_hentet_eksternt`: M-48 fikk klyngens ene unntak, M-49
-- fikk det ikke — og grunnen er at en sanksjonsliste er noe helt
-- annet enn et organisasjonsnummer. Fila er stor, den oppdateres
-- uforutsigbart, og en modul som hentet den automatisk ville tatt
-- ansvaret for at NØYAKTIG den versjonen er den gjeldende.
CREATE FUNCTION m49_registrer_liste(
    p_tenant TEXT, p_liste_id UUID, p_kilde TEXT,
    p_listeversjon TEXT, p_gjelder_fra DATE, p_innhold_sha256 TEXT,
    p_antall_oppforinger INT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm49_registrer_liste');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    IF p_gjelder_fra IS NULL OR p_gjelder_fra > current_date THEN
        RAISE EXCEPTION 'm49_registrer_liste: en liste kan ikke gjelde'
            ' fra framtida (%)', p_gjelder_fra
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO public.sanksjonsliste
        (tenant, liste_id, kilde, listeversjon, gjelder_fra,
         innhold_sha256, antall_oppforinger, registrert_av)
    VALUES (p_tenant, p_liste_id, p_kilde, btrim(p_listeversjon),
            p_gjelder_fra, lower(btrim(p_innhold_sha256)),
            p_antall_oppforinger, p_aktor);

    -- Lista gjelder hele tenanten, ikke ett subjekt.
    PERFORM public.m49_evidens(p_tenant, NULL, 'sanksjonsliste_lastet',
        p_aktor, jsonb_build_object(
            'liste_id', p_liste_id, 'kilde', p_kilde,
            'listeversjon', btrim(p_listeversjon),
            'antall_oppforinger', p_antall_oppforinger));
END $$;
REVOKE ALL ON FUNCTION m49_registrer_liste(
    TEXT, UUID, TEXT, TEXT, DATE, TEXT, INT, TEXT) FROM PUBLIC;


CREATE FUNCTION m49_registrer_subjekt(
    p_tenant TEXT, p_subjekt_id UUID, p_ekstern_ref TEXT,
    p_navn TEXT, p_subjekttype TEXT, p_land TEXT, p_fodselsdato DATE,
    p_identifikator TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm49_registrer_subjekt');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    IF p_fodselsdato IS NOT NULL
       AND p_fodselsdato > current_date THEN
        RAISE EXCEPTION 'm49_registrer_subjekt: fødselsdato i framtida'
            ' (%)', p_fodselsdato
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- NORMALISERINGEN REGNES HER, ikke sendes inn. Sendte kalleren den
    -- selv, kunne den vært hva som helst — og kolonnen ville sluttet å
    -- bety «det vi faktisk sammenlignet på» (112s dom).
    INSERT INTO public.sanksjonssubjekt
        (tenant, subjekt_id, ekstern_ref, navn_oppgitt,
         navn_normalisert, subjekttype, land, fodselsdato,
         identifikator, opprettet_av)
    VALUES (p_tenant, p_subjekt_id, btrim(p_ekstern_ref),
            btrim(p_navn), public.m49_normaliser(p_navn),
            p_subjekttype,
            nullif(btrim(coalesce(p_land, '')), ''),
            p_fodselsdato,
            nullif(btrim(coalesce(p_identifikator, '')), ''),
            p_aktor);

    PERFORM public.m49_evidens(p_tenant, p_subjekt_id,
        'sanksjonssubjekt_registrert', p_aktor,
        jsonb_build_object('subjekttype', p_subjekttype,
                           'har_identifikator',
                           nullif(btrim(coalesce(p_identifikator, '')),
                                  '') IS NOT NULL));
END $$;
REVOKE ALL ON FUNCTION m49_registrer_subjekt(
    TEXT, UUID, TEXT, TEXT, TEXT, TEXT, DATE, TEXT, TEXT)
    FROM PUBLIC;


-- DOM 1, 2 OG 4. MODULENS KJERNEDØR.
--
-- KONTROLLEN OG TREFFENE SKRIVES SAMMEN, i én transaksjon, og
-- `antall_treff` REGNES av døra. Tok kalleren begge deler hver for
-- seg, kunne en kontroll påstå «ingen treff» mens treffradene sto der
-- — og «antall åpne treff» ville målt noe annet enn virkeligheten.
--
-- SAMMENLIGNINGEN SKJER IKKE HER, OG DET ER EN BEVISST GRENSE.
-- Basen lagrer listeVERSJONEN, ikke listeINNHOLDET: fila er stor, den
-- eies av utgiver, og å kopiere den inn ville gjort oss til
-- distributør av en sanksjonsliste. Kalleren sammenligner mot fila og
-- leverer treffene hit.
--
-- DERFOR MÅ DØRA VOKTE DET BASEN FAKTISK KAN VITE, og det ene den kan
-- vite er om SUBJEKTET har en identifikator. Et `eksakt_identifikator`
-- på et subjekt uten identifikator er en påstand basen kan avvise —
-- og MÅ avvise, fordi det er nettopp den klassen som en dag skal kunne
-- blokkere handel maskinelt. Uten denne vakten kunne en klientfeil
-- gjort en navnelikhet til et blokkeringsgrunnlag.
CREATE FUNCTION m49_registrer_kontroll(
    p_tenant TEXT, p_kontroll_id UUID, p_subjekt_id UUID,
    p_liste_id UUID, p_sammenlignede_felt TEXT[], p_treff JSONB,
    p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_aktiv BOOLEAN;
    v_har_id BOOLEAN;
    v_terskel INT;
    v_kravversjon INT;
    v_antall INT;
    v_rad JSONB;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm49_registrer_kontroll');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    IF p_treff IS NULL OR jsonb_typeof(p_treff) <> 'array' THEN
        RAISE EXCEPTION 'm49_registrer_kontroll: treffene må være en'
            ' JSON-liste — en kontroll uten treffliste er ikke en'
            ' kontroll, den er en påstand'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_sammenlignede_felt IS NULL
       OR cardinality(p_sammenlignede_felt) = 0 THEN
        RAISE EXCEPTION 'm49_registrer_kontroll: matchgrunnlaget må si'
            ' HVILKE felter som ble sammenlignet'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Subjektet låses: den ene muterbare tabellen, og låsen
    -- serialiserer to samtidige kontroller på samme subjekt.
    SELECT s.aktiv, s.identifikator IS NOT NULL
      INTO v_aktiv, v_har_id
      FROM public.sanksjonssubjekt s
     WHERE s.tenant = p_tenant AND s.subjekt_id = p_subjekt_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm49_registrer_kontroll: ukjent subjekt %',
            p_subjekt_id USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_aktiv THEN
        RAISE EXCEPTION 'm49_registrer_kontroll: subjektet % er'
            ' deaktivert', p_subjekt_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Ingen krav-rad betyr ingen terskel, og terskelen er tenantens.
    -- `coalesce` mot tabellens default ville vært å hardkode policyen
    -- i en annen fil enn den som eier den (116s form).
    SELECT k.matchterskel, k.versjon INTO v_terskel, v_kravversjon
      FROM public.sanksjonskrav k WHERE k.tenant = p_tenant;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm49_registrer_kontroll: tenanten % har ingen'
            ' sanksjonskrav — en kontroll uten terskel er en kontroll'
            ' ingen kan etterprøve', p_tenant
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    PERFORM 1 FROM public.sanksjonsliste l
     WHERE l.tenant = p_tenant AND l.liste_id = p_liste_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm49_registrer_kontroll: ukjent listeversjon %',
            p_liste_id USING ERRCODE = 'no_data_found';
    END IF;

    v_antall := jsonb_array_length(p_treff);

    INSERT INTO public.sanksjonskontroll
        (tenant, kontroll_id, subjekt_id, liste_id, matchterskel,
         sammenlignede_felt, kravversjon, utfall, antall_treff,
         kontrollert_av)
    VALUES (p_tenant, p_kontroll_id, p_subjekt_id, p_liste_id,
            v_terskel, p_sammenlignede_felt, v_kravversjon,
            CASE WHEN v_antall > 0 THEN 'treff' ELSE 'ingen_treff' END,
            v_antall, p_aktor);

    FOR v_rad IN SELECT * FROM jsonb_array_elements(p_treff)
    LOOP
        -- VAKTEN BASEN FAKTISK KAN HÅNDHEVE. Se dommen over.
        IF v_rad->>'matchtype' = 'eksakt_identifikator'
           AND NOT v_har_id THEN
            RAISE EXCEPTION 'm49_registrer_kontroll: subjektet % har'
                ' ingen identifikator, så et treff kan ikke være'
                ' «eksakt_identifikator» — det er navnelikhet, og'
                ' skillet er hele grunnlaget en framtidig blokkering'
                ' skal hvile på', p_subjekt_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        INSERT INTO public.sanksjonstreff
            (tenant, treff_id, kontroll_id, matchtype, matchfelt,
             likhet, listenavn, liste_referanse, liste_program)
        VALUES (p_tenant, (v_rad->>'treff_id')::uuid, p_kontroll_id,
                v_rad->>'matchtype',
                ARRAY(SELECT jsonb_array_elements_text(
                          v_rad->'matchfelt')),
                (v_rad->>'likhet')::int,
                btrim(v_rad->>'listenavn'),
                btrim(v_rad->>'liste_referanse'),
                nullif(btrim(coalesce(v_rad->>'liste_program', '')),
                       ''));
    END LOOP;

    PERFORM public.m49_evidens(p_tenant, p_subjekt_id,
        'sanksjonskontroll_utfort', p_aktor,
        jsonb_build_object('kontroll_id', p_kontroll_id,
                           'liste_id', p_liste_id,
                           'matchterskel', v_terskel,
                           'antall_treff', v_antall));
    RETURN v_antall;
END $$;
REVOKE ALL ON FUNCTION m49_registrer_kontroll(
    TEXT, UUID, UUID, UUID, TEXT[], JSONB, TEXT) FROM PUBLIC;


-- DOM 3. MENNESKET SOM TOK STILLING.
--
-- DETTE ER DEN ENESTE VEIEN ET TREFF KAN LUKKES, og fraværet av
-- enhver annen vei ER porten `modulen_avfeide_navnelikhet`. Det finnes
-- ingen sveip, ingen batchjobb og ingen «lukk alle under 90 %»-dør.
--
-- BEGRUNNELSEN ER PÅKREVD, og minstelengden på tolv tegn er ikke pynt:
-- «ok» er ikke en begrunnelse for å slippe en mulig sanksjonert part
-- gjennom, og det er nøyaktig den raden et tilsyn ber om å få se.
CREATE FUNCTION m49_avklar_treff(
    p_tenant TEXT, p_avklaring_id UUID, p_treff_id UUID,
    p_konklusjon TEXT, p_begrunnelse TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_subjekt UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm49_avklar_treff');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    IF length(btrim(coalesce(p_begrunnelse, ''))) < 12 THEN
        RAISE EXCEPTION 'm49_avklar_treff: en avklaring krever en'
            ' begrunnelse — dette er raden et tilsyn ber om å få se'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Treffet leses UTEN lås (frosset), subjektet låses. M-42s lærdom
    -- (110): `FOR UPDATE` krever UPDATE-retten, og de frosne
    -- tabellene har den ikke.
    SELECT k.subjekt_id INTO v_subjekt
      FROM public.sanksjonstreff t
      JOIN public.sanksjonskontroll k
        ON k.tenant = t.tenant AND k.kontroll_id = t.kontroll_id
     WHERE t.tenant = p_tenant AND t.treff_id = p_treff_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm49_avklar_treff: ukjent treff %', p_treff_id
            USING ERRCODE = 'no_data_found';
    END IF;
    PERFORM 1 FROM public.sanksjonssubjekt
     WHERE tenant = p_tenant AND subjekt_id = v_subjekt FOR UPDATE;

    -- En NY vurdering av samme treff er en ny KONTROLL, ikke en
    -- overskriving av gårsdagens dom. Unikhetsvakten på tabellen sier
    -- det samme; denne gir den ærlige feilmeldingen.
    IF EXISTS (SELECT 1 FROM public.sanksjonsavklaring a
                WHERE a.tenant = p_tenant AND a.treff_id = p_treff_id)
    THEN
        RAISE EXCEPTION 'm49_avklar_treff: treffet % er alt avklart —'
            ' en ny vurdering er en ny kontroll, ikke en overskriving',
            p_treff_id USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO public.sanksjonsavklaring
        (tenant, avklaring_id, treff_id, konklusjon, begrunnelse,
         avklart_av)
    VALUES (p_tenant, p_avklaring_id, p_treff_id, p_konklusjon,
            btrim(p_begrunnelse), p_aktor);

    PERFORM public.m49_evidens(p_tenant, v_subjekt, 'treff_avklart',
        p_aktor, jsonb_build_object('treff_id', p_treff_id,
                                    'konklusjon', p_konklusjon));
END $$;
REVOKE ALL ON FUNCTION m49_avklar_treff(
    TEXT, UUID, UUID, TEXT, TEXT, TEXT) FROM PUBLIC;


CREATE FUNCTION m49_sett_subjektaktiv(
    p_tenant TEXT, p_subjekt_id UUID, p_aktiv BOOLEAN, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_var BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm49_sett_subjektaktiv');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    SELECT aktiv INTO v_var FROM public.sanksjonssubjekt
     WHERE tenant = p_tenant AND subjekt_id = p_subjekt_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm49_sett_subjektaktiv: ukjent subjekt %',
            p_subjekt_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_var = p_aktiv THEN
        RETURN;  -- idempotent
    END IF;

    UPDATE public.sanksjonssubjekt SET aktiv = p_aktiv
     WHERE tenant = p_tenant AND subjekt_id = p_subjekt_id;

    -- HISTORIKKEN BLIR STÅENDE. Kontroller, treff og avklaringer er
    -- frosset og røres ikke: «vi kontrollerer ikke denne lenger» er
    -- ikke «kontrollen skjedde aldri».
    PERFORM public.m49_evidens(p_tenant, p_subjekt_id,
        CASE WHEN p_aktiv THEN 'subjekt_aktivert'
             ELSE 'subjekt_deaktivert' END,
        p_aktor, '{}'::jsonb);
END $$;
REVOKE ALL ON FUNCTION m49_sett_subjektaktiv(
    TEXT, UUID, BOOLEAN, TEXT) FROM PUBLIC;


CREATE FUNCTION m49_lukk_funn(
    p_tenant TEXT, p_subjekt_id UUID, p_funntype TEXT,
    p_notat TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_apen BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm49_lukk_funn');
    PERFORM set_config('disponit.aktor', p_aktor, true);

    IF length(btrim(coalesce(p_notat, ''))) < 4 THEN
        RAISE EXCEPTION 'm49_lukk_funn: et funn lukkes med en'
            ' begrunnelse, ikke med et klikk'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- ET BEKREFTET TREFF LUKKES IKKE HER, OG DET ER MODULENS
    -- SKARPESTE NEKT. `bekreftet_treff` betyr at et menneske har sagt
    -- at parten ER sanksjonert. Kunne det funnet lukkes med et notat,
    -- ville modulen tilbudt en knapp for å gjøre den observasjonen
    -- borte — og den knappen er farligere enn manglende blokkering,
    -- fordi den ser ut som saksbehandling.
    IF p_funntype = 'bekreftet_treff' THEN
        RAISE EXCEPTION 'm49_lukk_funn: et bekreftet sanksjonstreff'
            ' kan ikke lukkes bort. Det lukkes når subjektet'
            ' deaktiveres eller når en ny kontroll mot en ny'
            ' listeversjon ikke lenger gir treffet'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT apen INTO v_apen FROM public.sanksjonsfunn
     WHERE tenant = p_tenant AND subjekt_id = p_subjekt_id
       AND funntype = p_funntype
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm49_lukk_funn: ukjent funn %/%',
            p_subjekt_id, p_funntype USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_apen THEN
        RETURN;  -- idempotent
    END IF;

    UPDATE public.sanksjonsfunn
       SET apen = false, lukket_ts = now()
     WHERE tenant = p_tenant AND subjekt_id = p_subjekt_id
       AND funntype = p_funntype;

    PERFORM public.m49_evidens(p_tenant, p_subjekt_id, 'funn_lukket',
        p_aktor, jsonb_build_object('funntype', p_funntype,
                                    'notat', btrim(p_notat)));
END $$;
REVOKE ALL ON FUNCTION m49_lukk_funn(TEXT, UUID, TEXT, TEXT, TEXT)
    FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Lesedørene.
-- ------------------------------------------------------------

CREATE FUNCTION m49_kravene(p_tenant TEXT)
RETURNS TABLE (matchterskel INT, kontroll_gyldig_dogn INT,
               uavklart_frist_dogn INT, ukontrollert_dogn INT,
               versjon INT, oppdatert TIMESTAMPTZ, oppdatert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm49_kravene');
    RETURN QUERY
    SELECT k.matchterskel, k.kontroll_gyldig_dogn,
           k.uavklart_frist_dogn, k.ukontrollert_dogn, k.versjon,
           k.oppdatert, k.oppdatert_av
      FROM public.sanksjonskrav k WHERE k.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m49_kravene(TEXT) FROM PUBLIC;


CREATE FUNCTION m49_listene(p_tenant TEXT)
RETURNS TABLE (liste_id UUID, kilde TEXT, listeversjon TEXT,
               gjelder_fra DATE, innhold_sha256 TEXT,
               antall_oppforinger INT, registrert TIMESTAMPTZ,
               registrert_av TEXT, er_nyeste BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm49_listene');
    RETURN QUERY
    SELECT l.liste_id, l.kilde, l.listeversjon, l.gjelder_fra,
           l.innhold_sha256, l.antall_oppforinger, l.registrert,
           l.registrert_av,
           -- «ER DETTE DEN NYESTE PER KILDE» er spørsmålet flaten
           -- stiller, og det regnes HER så to lesere ikke kan komme
           -- til hver sin konklusjon.
           l.liste_id = first_value(l.liste_id) OVER (
               PARTITION BY l.kilde
               ORDER BY l.gjelder_fra DESC, l.registrert DESC)
      FROM public.sanksjonsliste l
     WHERE l.tenant = p_tenant
     ORDER BY l.kilde, l.gjelder_fra DESC, l.registrert DESC;
END $$;
REVOKE ALL ON FUNCTION m49_listene(TEXT) FROM PUBLIC;


CREATE FUNCTION m49_subjektene(p_tenant TEXT, p_grense INT)
RETURNS TABLE (subjekt_id UUID, ekstern_ref TEXT, navn_oppgitt TEXT,
               subjekttype TEXT, land TEXT, har_identifikator BOOLEAN,
               aktiv BOOLEAN, opprettet TIMESTAMPTZ,
               siste_kontroll TIMESTAMPTZ, siste_utfall TEXT,
               apne_treff BIGINT, groveste_matchtype TEXT,
               apne_funn BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm49_subjektene');
    IF p_grense IS NULL OR p_grense < 1 OR p_grense > 500 THEN
        RAISE EXCEPTION 'm49_subjektene: grensen må være 1..500 (%)',
            p_grense USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    SELECT s.subjekt_id, s.ekstern_ref, s.navn_oppgitt, s.subjekttype,
           s.land, s.identifikator IS NOT NULL, s.aktiv, s.opprettet,
           k.kontrollert, k.utfall,
           coalesce(u.apne, 0), u.groveste, coalesce(f.antall, 0)
      FROM public.sanksjonssubjekt s
      LEFT JOIN LATERAL (
           SELECT mk.kontrollert, mk.utfall
             FROM public.sanksjonskontroll mk
            WHERE mk.tenant = s.tenant
              AND mk.subjekt_id = s.subjekt_id
            ORDER BY mk.kontrollert DESC
            LIMIT 1) k ON true
      LEFT JOIN LATERAL (
           -- UAVKLARTE TREFF, OG DET GROVESTE AV DEM. Rekkefølgen er
           -- ikke alfabetisk: identifikatortreff er det alvorligste,
           -- så eksakt navn, så navnelikhet. En flate som sorterte
           -- alfabetisk ville vist «eksakt_navn» som verre enn
           -- «eksakt_identifikator».
           SELECT count(*) AS apne,
                  min(CASE t.matchtype
                          WHEN 'eksakt_identifikator' THEN 1
                          WHEN 'eksakt_navn' THEN 2
                          ELSE 3 END) AS grov,
                  (ARRAY['eksakt_identifikator', 'eksakt_navn',
                         'navnelikhet'])[
                      min(CASE t.matchtype
                              WHEN 'eksakt_identifikator' THEN 1
                              WHEN 'eksakt_navn' THEN 2
                              ELSE 3 END)] AS groveste
             FROM public.sanksjonstreff t
             JOIN public.sanksjonskontroll mk
               ON mk.tenant = t.tenant
              AND mk.kontroll_id = t.kontroll_id
            WHERE t.tenant = s.tenant
              AND mk.subjekt_id = s.subjekt_id
              AND NOT EXISTS (
                  SELECT 1 FROM public.sanksjonsavklaring a
                   WHERE a.tenant = t.tenant
                     AND a.treff_id = t.treff_id)) u ON true
      LEFT JOIN LATERAL (
           SELECT count(*) AS antall FROM public.sanksjonsfunn mf
            WHERE mf.tenant = s.tenant
              AND mf.subjekt_id = s.subjekt_id AND mf.apen) f ON true
     WHERE s.tenant = p_tenant
     ORDER BY s.opprettet DESC
     LIMIT p_grense;
END $$;
REVOKE ALL ON FUNCTION m49_subjektene(TEXT, INT) FROM PUBLIC;


CREATE FUNCTION m49_kontrollene(p_tenant TEXT, p_subjekt_id UUID)
RETURNS TABLE (kontroll_id UUID, liste_id UUID, kilde TEXT,
               listeversjon TEXT, matchterskel INT,
               sammenlignede_felt TEXT[], kravversjon INT,
               utfall TEXT, antall_treff INT,
               kontrollert TIMESTAMPTZ, kontrollert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm49_kontrollene');
    RETURN QUERY
    SELECT k.kontroll_id, k.liste_id, l.kilde, l.listeversjon,
           k.matchterskel, k.sammenlignede_felt, k.kravversjon,
           k.utfall, k.antall_treff, k.kontrollert, k.kontrollert_av
      FROM public.sanksjonskontroll k
      JOIN public.sanksjonsliste l
        ON l.tenant = k.tenant AND l.liste_id = k.liste_id
     WHERE k.tenant = p_tenant AND k.subjekt_id = p_subjekt_id
     ORDER BY k.kontrollert DESC;
END $$;
REVOKE ALL ON FUNCTION m49_kontrollene(TEXT, UUID) FROM PUBLIC;


-- TREFFENE MED SIN AVKLARING. Et treff uten avklaring er UAVKLART, og
-- det skilles ikke fra et avklart ved å utelate det: begge står i
-- samme liste, og `konklusjon` er NULL for de uavklarte. En flate som
-- bare viste de uavklarte ville skjult hva noen faktisk konkluderte.
CREATE FUNCTION m49_treffene(p_tenant TEXT, p_subjekt_id UUID)
RETURNS TABLE (treff_id UUID, kontroll_id UUID, matchtype TEXT,
               matchfelt TEXT[], likhet INT, listenavn TEXT,
               liste_referanse TEXT, liste_program TEXT,
               kilde TEXT, listeversjon TEXT,
               registrert TIMESTAMPTZ, konklusjon TEXT,
               begrunnelse TEXT, avklart TIMESTAMPTZ,
               avklart_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm49_treffene');
    RETURN QUERY
    SELECT t.treff_id, t.kontroll_id, t.matchtype, t.matchfelt,
           t.likhet, t.listenavn, t.liste_referanse, t.liste_program,
           l.kilde, l.listeversjon, t.registrert,
           a.konklusjon, a.begrunnelse, a.avklart, a.avklart_av
      FROM public.sanksjonstreff t
      JOIN public.sanksjonskontroll k
        ON k.tenant = t.tenant AND k.kontroll_id = t.kontroll_id
      JOIN public.sanksjonsliste l
        ON l.tenant = k.tenant AND l.liste_id = k.liste_id
      LEFT JOIN public.sanksjonsavklaring a
        ON a.tenant = t.tenant AND a.treff_id = t.treff_id
     WHERE t.tenant = p_tenant AND k.subjekt_id = p_subjekt_id
     ORDER BY t.registrert DESC, t.likhet DESC;
END $$;
REVOKE ALL ON FUNCTION m49_treffene(TEXT, UUID) FROM PUBLIC;


CREATE FUNCTION m49_sanksjonsstatus(p_tenant TEXT)
RETURNS TABLE (subjekter BIGINT, aktive BIGINT, kontrollerte BIGINT,
               uavklarte_treff BIGINT, bekreftede_treff BIGINT,
               apne_funn BIGINT, lister BIGINT,
               nyeste_listeversjon TEXT, har_krav BOOLEAN,
               kravversjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm49_sanksjonsstatus');
    RETURN QUERY
    SELECT (SELECT count(*) FROM public.sanksjonssubjekt s
             WHERE s.tenant = p_tenant),
           (SELECT count(*) FROM public.sanksjonssubjekt s
             WHERE s.tenant = p_tenant AND s.aktiv),
           (SELECT count(DISTINCT k.subjekt_id)
              FROM public.sanksjonskontroll k
             WHERE k.tenant = p_tenant),
           -- UAVKLARTE TREFF ER MODULENS VIKTIGSTE TALL. Et treff
           -- ingen har sett på er ikke et vern; det er en
           -- udokumentert risiko.
           (SELECT count(*) FROM public.sanksjonstreff t
             WHERE t.tenant = p_tenant
               AND NOT EXISTS (SELECT 1
                                 FROM public.sanksjonsavklaring a
                                WHERE a.tenant = t.tenant
                                  AND a.treff_id = t.treff_id)),
           (SELECT count(*) FROM public.sanksjonsavklaring a
             WHERE a.tenant = p_tenant
               AND a.konklusjon = 'bekreftet_treff'),
           (SELECT count(*) FROM public.sanksjonsfunn f
             WHERE f.tenant = p_tenant AND f.apen),
           (SELECT count(*) FROM public.sanksjonsliste l
             WHERE l.tenant = p_tenant),
           (SELECT l.kilde || ' ' || l.listeversjon
              FROM public.sanksjonsliste l
             WHERE l.tenant = p_tenant
             ORDER BY l.gjelder_fra DESC, l.registrert DESC LIMIT 1),
           EXISTS (SELECT 1 FROM public.sanksjonskrav k
                    WHERE k.tenant = p_tenant),
           (SELECT k.versjon FROM public.sanksjonskrav k
             WHERE k.tenant = p_tenant);
END $$;
REVOKE ALL ON FUNCTION m49_sanksjonsstatus(TEXT) FROM PUBLIC;


CREATE FUNCTION m49_funnene(p_tenant TEXT, p_bare_apne BOOLEAN)
RETURNS TABLE (subjekt_id UUID, ekstern_ref TEXT, navn_oppgitt TEXT,
               funntype TEXT, over_grense INT, siste_matchtype TEXT,
               siste_utfall TEXT, kravversjon INT,
               forst_sett TIMESTAMPTZ, sist_sett_sveip TIMESTAMPTZ,
               apen BOOLEAN, lukket_ts TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm49_funnene');
    RETURN QUERY
    SELECT f.subjekt_id, s.ekstern_ref, s.navn_oppgitt, f.funntype,
           f.over_grense, f.siste_matchtype, f.siste_utfall,
           f.kravversjon, f.forst_sett, f.sist_sett_sveip, f.apen,
           f.lukket_ts
      FROM public.sanksjonsfunn f
      JOIN public.sanksjonssubjekt s
        ON s.tenant = f.tenant AND s.subjekt_id = f.subjekt_id
     WHERE f.tenant = p_tenant
       AND (NOT coalesce(p_bare_apne, true) OR f.apen)
     ORDER BY f.apen DESC, f.forst_sett DESC;
END $$;
REVOKE ALL ON FUNCTION m49_funnene(TEXT, BOOLEAN) FROM PUBLIC;


-- ------------------------------------------------------------
-- 5. Sveipen.
-- ------------------------------------------------------------

-- TENANTLISTA MATERIALISERES FØR LØKKA (112s lærdom, gjentatt i 116):
-- `FOR t IN SELECT ...` er en LAT markør, og `set_config` inne i løkka
-- endrer RLS-konteksten markøren fortsatt leser gjennom.
--
-- SVEIPEN AVKLARER INGENTING. Den finner treff som har stått for
-- lenge; den kan ikke lukke dem. Det er porten
-- `modulen_avfeide_navnelikhet`, sett fra nattens side.
CREATE FUNCTION m49_sveip_sanksjoner(p_maks_tenanter INT)
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
        RAISE EXCEPTION 'm49_sveip_sanksjoner: maks_tenanter må være'
            ' minst 1 (%)', p_maks_tenanter
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.tenant', '', true);
    SELECT array_agg(DISTINCT s.tenant ORDER BY s.tenant)
      INTO v_tenanter FROM public.sanksjonssubjekt s;
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
            SELECT k.kontroll_gyldig_dogn, k.uavklart_frist_dogn,
                   k.ukontrollert_dogn, k.versjon
              FROM public.sanksjonskrav k WHERE k.tenant = v_t),
        nyeste_liste AS (
            SELECT DISTINCT ON (l.kilde) l.kilde, l.liste_id
              FROM public.sanksjonsliste l
             WHERE l.tenant = v_t
             ORDER BY l.kilde, l.gjelder_fra DESC, l.registrert DESC),
        siste AS (
            SELECT s.subjekt_id, s.opprettet, k.kontroll_id,
                   k.kontrollert, k.utfall, k.liste_id
              FROM public.sanksjonssubjekt s
              LEFT JOIN LATERAL (
                   SELECT mk.kontroll_id, mk.kontrollert, mk.utfall,
                          mk.liste_id
                     FROM public.sanksjonskontroll mk
                    WHERE mk.tenant = s.tenant
                      AND mk.subjekt_id = s.subjekt_id
                    ORDER BY mk.kontrollert DESC
                    LIMIT 1) k ON true
             WHERE s.tenant = v_t AND s.aktiv),
        uavklart AS (
            SELECT mk.subjekt_id,
                   min(t.registrert) AS eldste,
                   (ARRAY['eksakt_identifikator', 'eksakt_navn',
                          'navnelikhet'])[
                       min(CASE t.matchtype
                               WHEN 'eksakt_identifikator' THEN 1
                               WHEN 'eksakt_navn' THEN 2
                               ELSE 3 END)] AS groveste
              FROM public.sanksjonstreff t
              JOIN public.sanksjonskontroll mk
                ON mk.tenant = t.tenant
               AND mk.kontroll_id = t.kontroll_id
             WHERE t.tenant = v_t
               AND NOT EXISTS (SELECT 1
                                 FROM public.sanksjonsavklaring a
                                WHERE a.tenant = t.tenant
                                  AND a.treff_id = t.treff_id)
             GROUP BY mk.subjekt_id),
        bekreftet AS (
            -- BARE PÅ SISTE KONTROLL. Et bekreftet treff fra i fjor,
            -- der en ny kontroll mot en ny listeversjon ikke lenger
            -- gir treffet, er løst — og funnet skal lukkes. Uten
            -- bindingen til siste kontroll ville funnet stått for
            -- alltid, og M-39s felle vært gjentatt.
            SELECT DISTINCT mk.subjekt_id
              FROM public.sanksjonsavklaring a
              JOIN public.sanksjonstreff t
                ON t.tenant = a.tenant AND t.treff_id = a.treff_id
              JOIN public.sanksjonskontroll mk
                ON mk.tenant = t.tenant
               AND mk.kontroll_id = t.kontroll_id
              JOIN siste s2 ON s2.kontroll_id = mk.kontroll_id
             WHERE a.tenant = v_t
               AND a.konklusjon = 'bekreftet_treff'),
        kand AS (
            SELECT s.subjekt_id, 'ingen_krav'::text AS funntype,
                   NULL::int AS over_grense,
                   NULL::text AS siste_matchtype,
                   s.utfall AS siste_utfall,
                   NULL::int AS kravversjon
              FROM siste s WHERE NOT EXISTS (SELECT 1 FROM krav)

            UNION ALL
            -- INGEN LISTE. Uten en registrert listeversjon kan ingen
            -- kontroll gjøres i det hele tatt, og et register som ser
            -- rolig ut fordi ingen har lastet lista er farligere enn
            -- et som viser funn.
            SELECT s.subjekt_id, 'ingen_liste', NULL::int, NULL::text,
                   s.utfall, k.versjon
              FROM siste s CROSS JOIN krav k
             WHERE NOT EXISTS (SELECT 1 FROM nyeste_liste)

            UNION ALL
            SELECT s.subjekt_id, 'ukontrollert_subjekt',
                   (current_date - s.opprettet::date)
                   - k.ukontrollert_dogn,
                   NULL::text, NULL::text, k.versjon
              FROM siste s CROSS JOIN krav k
             WHERE s.kontrollert IS NULL
               AND s.opprettet
                   < now() - make_interval(days => k.ukontrollert_dogn)

            UNION ALL
            SELECT s.subjekt_id, 'kontroll_utlopt',
                   (current_date - s.kontrollert::date)
                   - k.kontroll_gyldig_dogn,
                   NULL::text, s.utfall, k.versjon
              FROM siste s CROSS JOIN krav k
             WHERE s.kontrollert IS NOT NULL
               AND s.kontrollert < now()
                   - make_interval(days => k.kontroll_gyldig_dogn)

            UNION ALL
            -- KONTROLLERT MOT EN GAMMEL LISTE. En fersk kontroll mot
            -- fjorårets liste er ikke en fersk kontroll.
            SELECT s.subjekt_id, 'kontroll_mot_gammel_liste',
                   NULL::int, NULL::text, s.utfall, k.versjon
              FROM siste s CROSS JOIN krav k
             WHERE s.liste_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM nyeste_liste)
               AND NOT EXISTS (SELECT 1 FROM nyeste_liste nl
                                WHERE nl.liste_id = s.liste_id)

            UNION ALL
            SELECT u.subjekt_id, 'uavklart_treff',
                   (current_date - u.eldste::date)
                   - k.uavklart_frist_dogn,
                   u.groveste, NULL::text, k.versjon
              FROM uavklart u CROSS JOIN krav k
             WHERE u.eldste
                   < now() - make_interval(days => k.uavklart_frist_dogn)

            UNION ALL
            -- BEKREFTET TREFF. Ikke en frist, men en TILSTAND: noen
            -- har sagt at parten står på lista. Funnet står til
            -- tilstanden er borte.
            SELECT b.subjekt_id, 'bekreftet_treff', NULL::int,
                   NULL::text, NULL::text, (SELECT versjon FROM krav)
              FROM bekreftet b
        ),
        skrevet AS (
            INSERT INTO public.sanksjonsfunn
                (tenant, subjekt_id, funntype, over_grense,
                 siste_matchtype, siste_utfall, kravversjon)
            SELECT v_t, k.subjekt_id, k.funntype, k.over_grense,
                   k.siste_matchtype, k.siste_utfall, k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, subjekt_id, funntype) DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                siste_matchtype = EXCLUDED.siste_matchtype,
                siste_utfall = EXCLUDED.siste_utfall,
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
        -- `INTO` SETTER variabelen; akkumuleringen må stå her (112s
        -- retting, gjentatt i 116).
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);

        PERFORM set_config('disponit.tenant', '', true);
    END LOOP;

    -- LUKKINGEN GJØRES I EN EGEN RUNDE, av samme grunn som skrivingen
    -- gjøres per tenant: kandidatsettet må regnes på nytt i tenantens
    -- egen RLS-kontekst.
    FOREACH v_t IN ARRAY v_tenanter
    LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        WITH krav AS (
            SELECT k.kontroll_gyldig_dogn, k.uavklart_frist_dogn,
                   k.ukontrollert_dogn
              FROM public.sanksjonskrav k WHERE k.tenant = v_t),
        nyeste_liste AS (
            SELECT DISTINCT ON (l.kilde) l.kilde, l.liste_id
              FROM public.sanksjonsliste l
             WHERE l.tenant = v_t
             ORDER BY l.kilde, l.gjelder_fra DESC, l.registrert DESC),
        siste AS (
            SELECT s.subjekt_id, s.opprettet, k.kontroll_id,
                   k.kontrollert, k.liste_id
              FROM public.sanksjonssubjekt s
              LEFT JOIN LATERAL (
                   SELECT mk.kontroll_id, mk.kontrollert, mk.liste_id
                     FROM public.sanksjonskontroll mk
                    WHERE mk.tenant = s.tenant
                      AND mk.subjekt_id = s.subjekt_id
                    ORDER BY mk.kontrollert DESC
                    LIMIT 1) k ON true
             WHERE s.tenant = v_t AND s.aktiv),
        uavklart AS (
            SELECT mk.subjekt_id, min(t.registrert) AS eldste
              FROM public.sanksjonstreff t
              JOIN public.sanksjonskontroll mk
                ON mk.tenant = t.tenant
               AND mk.kontroll_id = t.kontroll_id
             WHERE t.tenant = v_t
               AND NOT EXISTS (SELECT 1
                                 FROM public.sanksjonsavklaring a
                                WHERE a.tenant = t.tenant
                                  AND a.treff_id = t.treff_id)
             GROUP BY mk.subjekt_id),
        bekreftet AS (
            SELECT DISTINCT mk.subjekt_id
              FROM public.sanksjonsavklaring a
              JOIN public.sanksjonstreff t
                ON t.tenant = a.tenant AND t.treff_id = a.treff_id
              JOIN public.sanksjonskontroll mk
                ON mk.tenant = t.tenant
               AND mk.kontroll_id = t.kontroll_id
              JOIN siste s2 ON s2.kontroll_id = mk.kontroll_id
             WHERE a.tenant = v_t
               AND a.konklusjon = 'bekreftet_treff'),
        kand AS (
            SELECT s.subjekt_id, 'ingen_krav'::text AS funntype
              FROM siste s WHERE NOT EXISTS (SELECT 1 FROM krav)
            UNION ALL
            SELECT s.subjekt_id, 'ingen_liste'
              FROM siste s CROSS JOIN krav k
             WHERE NOT EXISTS (SELECT 1 FROM nyeste_liste)
            UNION ALL
            SELECT s.subjekt_id, 'ukontrollert_subjekt'
              FROM siste s CROSS JOIN krav k
             WHERE s.kontrollert IS NULL
               AND s.opprettet
                   < now() - make_interval(days => k.ukontrollert_dogn)
            UNION ALL
            SELECT s.subjekt_id, 'kontroll_utlopt'
              FROM siste s CROSS JOIN krav k
             WHERE s.kontrollert IS NOT NULL
               AND s.kontrollert < now()
                   - make_interval(days => k.kontroll_gyldig_dogn)
            UNION ALL
            SELECT s.subjekt_id, 'kontroll_mot_gammel_liste'
              FROM siste s CROSS JOIN krav k
             WHERE s.liste_id IS NOT NULL
               AND EXISTS (SELECT 1 FROM nyeste_liste)
               AND NOT EXISTS (SELECT 1 FROM nyeste_liste nl
                                WHERE nl.liste_id = s.liste_id)
            UNION ALL
            SELECT u.subjekt_id, 'uavklart_treff'
              FROM uavklart u CROSS JOIN krav k
             WHERE u.eldste
                   < now() - make_interval(days => k.uavklart_frist_dogn)
            UNION ALL
            SELECT b.subjekt_id, 'bekreftet_treff' FROM bekreftet b
        ),
        lukket AS (
            UPDATE public.sanksjonsfunn f
               SET apen = false, lukket_ts = now()
             WHERE f.tenant = v_t AND f.apen
               AND NOT EXISTS (
                   SELECT 1 FROM kand k
                    WHERE k.subjekt_id = f.subjekt_id
                      AND k.funntype = f.funntype)
            RETURNING 1)
        SELECT count(*) INTO v_n FROM lukket;
        v_lukket := v_lukket + coalesce(v_n, 0);
        PERFORM set_config('disponit.tenant', '', true);
    END LOOP;

    RETURN QUERY SELECT v_antall, v_nye, v_oppdaterte, v_lukket;
END $$;
REVOKE ALL ON FUNCTION m49_sveip_sanksjoner(INT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 6. RLS. ENABLE + FORCE på alle seks.
-- ------------------------------------------------------------

RESET ROLE;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['sanksjonskrav', 'sanksjonsliste',
                             'sanksjonssubjekt', 'sanksjonskontroll',
                             'sanksjonstreff', 'sanksjonsavklaring',
                             'sanksjonsfunn']
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
                       ' disponit_sanksjon_eier', t);
    END LOOP;
END $$;

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER (111s form, gjentatt
-- i 112–116): bare på SUBJEKTTABELLEN, bare FOR SELECT, bare til
-- eieren, og bare når ingen tenantkontekst står.
CREATE POLICY m49_sveip_tenantliste ON sanksjonssubjekt
    FOR SELECT TO disponit_sanksjon_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- HISTORIKKTABELLENE FÅR IKKE UPDATE, HELLER IKKE FOR EIEREN. Vaktene
-- i dørene stanser den som skulle prøve; disse gjerdene stanser
-- forsøket før det når vakten. To gjerder, av samme grunn som i
-- 110–116.
--
-- `sanksjonsavklaring` ER MED I LISTA, og det er modulens skarpeste
-- gjerde: en avklaring som kunne skrives om ville gjort «hvem sa hva,
-- når» til et åpent spørsmål — og det er nøyaktig spørsmålet et
-- tilsyn stiller etter et sanksjonsbrudd. En ny vurdering er en ny
-- kontroll, ikke en retting av gårsdagens dom.
REVOKE UPDATE ON public.sanksjonsliste FROM disponit_sanksjon_eier;
REVOKE UPDATE ON public.sanksjonskontroll FROM disponit_sanksjon_eier;
REVOKE UPDATE ON public.sanksjonstreff FROM disponit_sanksjon_eier;
REVOKE UPDATE ON public.sanksjonsavklaring FROM disponit_sanksjon_eier;

-- SLETTING ER ALDRI LOVLIG. Ingen rolle får DELETE, og eieren ba aldri
-- om det — men et REVOKE som står, er et REVOKE noen kan lese.
REVOKE DELETE ON public.sanksjonskrav FROM disponit_sanksjon_eier;
REVOKE DELETE ON public.sanksjonsliste FROM disponit_sanksjon_eier;
REVOKE DELETE ON public.sanksjonssubjekt FROM disponit_sanksjon_eier;
REVOKE DELETE ON public.sanksjonskontroll FROM disponit_sanksjon_eier;
REVOKE DELETE ON public.sanksjonstreff FROM disponit_sanksjon_eier;
REVOKE DELETE ON public.sanksjonsavklaring FROM disponit_sanksjon_eier;
REVOKE DELETE ON public.sanksjonsfunn FROM disponit_sanksjon_eier;


-- ------------------------------------------------------------
-- 7. Rettighetene. SP-7: kjøretidsrollen får INGEN tabellrettigheter.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_sanksjon_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m49_sanksjonsstatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m49_kravene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m49_listene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m49_subjektene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m49_kontrollene(TEXT, UUID) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m49_treffene(TEXT, UUID) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m49_funnene(TEXT, BOOLEAN) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m49_sett_krav(TEXT, INT, INT, INT, INT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m49_registrer_liste(TEXT, UUID, TEXT, TEXT, DATE, TEXT,'
            ' INT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m49_registrer_subjekt(TEXT, UUID, TEXT, TEXT, TEXT,'
            ' TEXT, DATE, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m49_registrer_kontroll(TEXT, UUID, UUID, UUID, TEXT[],'
            ' JSONB, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m49_avklar_treff(TEXT, UUID, UUID, TEXT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m49_sett_subjektaktiv(TEXT, UUID, BOOLEAN, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m49_lukk_funn(TEXT, UUID, TEXT, TEXT, TEXT)'
            ' TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_sanksjonssveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m49_sveip_sanksjoner(INT)'
            ' TO disponit_sanksjonssveip';
    END IF;
END $$;

-- SVEIPEN ER IKKE KJØRETIDSROLLENS.
--
-- VAKTET, OG DET AVVIKER FRA 112/114/116 MED VILJE (CodeRabbit, 117).
-- GRANT-blokken over behandler `disponit` som VALGFRI — den står bak
-- `IF EXISTS (SELECT 1 FROM pg_roles ...)`. En UVAKTET `REVOKE` sier
-- det motsatte: `REVOKE ... FROM <rolle som ikke finnes>` er en FEIL i
-- PostgreSQL, ikke en no-op (målt), så migrasjonen ville stoppet i et
-- miljø uten kjøretidsrollen. De to kan ikke begge ha rett.
--
-- SØSKENMIGRASJONENE HAR DEN UVAKTEDE FORMEN, og de er ikke rettet
-- her: migrasjonsbytene er pinnet i `migrasjons-fasit.json` og fryses
-- ved merge. En retting av 112 ville brutt pinnen og
-- append-only-porten. Avviket er derfor bevisst og enveis — nye
-- migrasjoner vakter, gamle står som de står — og det står skrevet
-- her så neste leser ikke tror det er en forglemmelse.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'REVOKE EXECUTE ON FUNCTION'
            ' m49_sveip_sanksjoner(INT) FROM disponit';
    END IF;
END $$;

RESET ROLE;
