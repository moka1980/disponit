-- =====================================================================
-- M-40 HR- OG MEDARBEIDERAGENT (v1) — KLYNGE 10s FJERDE OG SISTE.
-- =====================================================================
--
-- V1-DOMMEN: MODULEN AVGJØR INGENTING OM ET MENNESKE. Ingen beslutning
-- med rettsvirkning (ansettelse, oppsigelse, lønn), ingen
-- individprofilering, ingen produktivitetsscore.
--
-- ---------------------------------------------------------------------
-- KLYNGENS DELTE DOM, FOR FJERDE OG SISTE GANG:
--
--   EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN ANGRES IKKE AV EN
--   ROLLBACK.
--
-- M-28 sa det om en bil på veien. Her er det tyngre: en oppsigelse som
-- ble rullet tilbake er fortsatt en samtale som fant sted, en beskjed
-- som ble lest og et menneske som brukte kvelden på den.
--
-- DERFOR BYGGES M-40 SIST. Ikke fordi den er vanskeligst å skrive, men
-- fordi den er den eneste i klyngen som rører enkeltmennesker som er
-- ansatt hos kunden.
--
-- ---------------------------------------------------------------------
-- DE TRE ANDRE HOLDT TILBAKE EN FULLMAKT. DENNE HOLDER TILBAKE TO —
-- OG MÅ I TILLEGG BYGGE NOE SOM FAKTISK ER SANT.
--
-- «Isoler kontoen», «bestill transporten», «lever oppgaven»: alle tre
-- ble til ved at døra ikke ble skrevet. Det samme gjelder de to
-- fullmaktene her — det finnes ingen beslutningsdør og ingen
-- score-kolonne.
--
-- MEN PULSMÅLINGEN ER IKKE SLIK. «Anonymiserte pulsmålinger aggregert
-- på gruppenivå» kan ikke oppfylles av å la være: modulen MÅ lagre
-- svarene, og de MÅ faktisk være uidentifiserbare. Det er klyngens
-- eneste krav som ikke lar seg innfri ved å holde noe tilbake.
--
-- ---------------------------------------------------------------------
-- ANONYMITET ER EN EGENSKAP VED SETTET, IKKE VED RADEN.
--
-- Fire svar fra en gruppe på fire er anonyme hver for seg og fullt
-- identifiserende til sammen. En gruppe som krymper fra åtte til tre
-- gjør gårsdagens anonyme svar identifiserbare i dag.
--
-- DERFOR ER K-ANONYMITETEN BYGGET INN I BASEN, IKKE SJEKKET I EN DØR,
-- og den står på tre bein som hver for seg er urepresenterbare å bryte:
--
--   1. `pulssvar` HAR INGEN `taker_id`. Ikke en nullbar kolonne, ikke
--      en pseudonymisert — kolonnen finnes ikke. En kobling som ikke
--      har noe sted å stå, kan ingen dør skrive og ingen feil lekke.
--   2. AGGREGATET NEKTER Å SVARE UNDER TERSKELEN. `m40_pulsbildet`
--      returnerer bare grupper som når `pulsmaaling.gruppeterskel`.
--   3. TERSKELEN ER LAGRET SAMMEN MED MÅLINGEN, og den kan ikke
--      endres etterpå: `REVOKE UPDATE` på hele tabellen, og
--      `GRANT UPDATE (lukket_ts, lukket_av)` tilbake. En terskel som
--      kan endres i ettertid er ingen terskel — den er en innstilling.
--
-- BEIN 3 ER GRUNNEN TIL AT DETTE ER EN KOLONNEGRANT OG IKKE EN CHECK.
-- En CHECK ville nektet endringen; en manglende rett gjør den umulig å
-- forsøke. Huset har valgt det siste seks ganger før.
--
-- ---------------------------------------------------------------------
-- ANSATTREGISTERET FINNES ALLEREDE, OG DET ER M-39s.
--
-- `lonnstaker` (113) er husets ENESTE register over mennesker som
-- jobber i bedriften. M-40 arver det og bygger ikke et nytt.
--
-- To registre over de samme menneskene gir to svar på «jobber hun
-- her», og det er ett for mange. Det er nøyaktig argumentet som ga M-7
-- og M-43 én delt opptakshjemmel.
--
-- OG GRANTEN KOMMER FRA MIGRATOR, IKKE FRA LØNNSEIEREN. `lonnstaker`
-- lages i 113 UTEN `SET LOCAL ROLE`, så eieren er `disponit_migrator`.
-- En GRANT fra en som ikke eier objektet er en FEIL, ikke et stille
-- null-tiltak — 133 gikk i den fella mot `krev_tenantkontekst`.
--
-- `onboardinglop` (M-18) ER IKKE MEDARBEIDERONBOARDING. Kolonnen heter
-- `kunde_ref`. M-18 er kundens innføring i produktet; dette er den
-- ansattes første uke. Fjerde gang samme felle i to klynger, og den
-- ble igjen avverget av å lese kolonnene og ikke navnet.
--
-- ---------------------------------------------------------------------
-- MALENE FINNES OG ER LÅST. M-5s, IKKE EN FEMTE UTKASTFORM.
--
-- Akseptansekravet sier «kontrakter kan alltid spores til malversjon
-- og kildefelt». `malversjon` (094) har `versjonsnr`, `status` og
-- `innhold_hash`; `malfelt` har kildefeltene. M-40 arver, slik M-20
-- fikk samme dom.
--
-- `medarbeiderkontrakt.malversjon_id` er NOT NULL med fremmednøkkel,
-- og `innhold_hash` KOPIERES fra malversjonen ved utstedelse. Så gjør
-- `REVOKE UPDATE` kontrakten uforanderlig: en juridisk klausul kan
-- ikke endres etter at et menneske har signert den, fordi ingen har
-- retten til å skrive i raden.
--
-- OG GRANTEN PÅ MALENE KOMMER OGSÅ FRA MIGRATOR: tabellene i 094 lages
-- på linje 157, FØR `SET LOCAL ROLE disponit_mal_eier` på 419. Samme
-- måling, samme svar, to ganger.
--
-- ---------------------------------------------------------------------
-- SEKS FUNN SOM ALDRI KAN REISES, OG DET ER BEVISET.
--
--   `beslutning_med_rettsvirkning`  — det finnes ingen beslutningsdør.
--   `individprofil_bygget`          — ingen tabell bærer både en
--                                     person og et tall om henne.
--   `puls_identifiserte_en_person`  — `pulssvar` har ingen personnøkkel.
--   `gruppeterskel_endret`          — ingen har UPDATE på kolonnen.
--   `kontrakt_uten_malversjon`      — NOT NULL med fremmednøkkel.
--   `krav_mangler`                  — sveipeløkka går over tenanter
--                                     som HAR et krav (M-28s lærdom).
--
-- Formen er klynge 9s og har nå gjentatt seg i ni moduler: skriv
-- funnet inn i det lukkede settet, og skriv en port som måler at
-- datamodellen utelukker det. Et sett som ikke navnga dem ville ikke
-- sagt noe; et sett som navnga dem og kunne fylles ville sagt at
-- vernet er en sveip.
--
-- ---------------------------------------------------------------------
-- KLYNGE 10 LUKKER KATALOGEN. Dette er migrasjon 140 av 140, og
-- modul 57 av 57.
-- =====================================================================

-- MODULROLLEN MÅ KUNNE EIE NOE FØR DEN KAN EIE DØRENE.
GRANT USAGE, CREATE ON SCHEMA public TO disponit_medarbeider_eier;
GRANT INSERT ON revisjonslogg TO disponit_medarbeider_eier;

-- HUSETS TENANTVAKT (038). Granten gis av EIEREN.
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_medarbeider_eier;
RESET ROLE;

-- ---------------------------------------------------------------------
-- ARVEN FRA 113 OG 094. BEGGE ER LESERETT, INGEN AV DEM SKRIVERETT.
--
-- BEGGE GRANTES AV MIGRATOR fordi migrator eier begge tabellsettene —
-- målt i 113 og 094, ikke antatt av modulnavnene.
--
-- `lonnstaker` er en KOLONNEGRANT uten `navn`: modulen trenger å vite
-- AT et menneske er ansatt, ikke hva hun heter. Navnet hører hjemme i
-- lønnskjøringen, som er den som skal skrive det på en slipp.
-- ---------------------------------------------------------------------
GRANT SELECT (tenant, taker_id, ekstern_ref, aktiv, opprettet)
    ON lonnstaker TO disponit_medarbeider_eier;
-- `opprettet` ER MED, OG DET ER SVEIPEN SOM KREVER DEN. `ansatt_uten_lop`
-- gir en modningstid: en som ble ansatt i går skal ikke gi et funn i
-- natt, og «siden når» finnes bare i M-39s rad. Uten kolonnen ville
-- sveipen falt på `permission denied for table lonnstaker` — og den
-- falt faktisk der, i den første kjøringen mot riggen.
GRANT SELECT (tenant, versjon_id, familie_id, versjonsnr, status,
              innhold_hash)
    ON malversjon TO disponit_medarbeider_eier;
GRANT SELECT (tenant, familie_id, navn)
    ON malfamilie TO disponit_medarbeider_eier;
-- KILDEFELTENE. Akseptansekravet sier «malversjon OG kildefelt», og
-- uten denne granten kunne modulen bare svart på halve spørsmålet.
GRANT SELECT (tenant, versjon_id, feltnokkel, paakrevd, felttype)
    ON malfelt TO disponit_medarbeider_eier;

-- ---------------------------------------------------------------------
-- `medarbeiderkrav` — TENANTENS GRENSER, APPEND-ONLY.
--
-- 135/137/138/139s form, arvet: versjonen tildeles av DØRA, og raden
-- oppdateres aldri.
--
-- `gruppeterskel_min` ER ET GULV, IKKE EN TERSKEL. Terskelen som
-- gjelder for en måling er malingens egen, låst ved åpning. Dette
-- tallet sier bare hvor lavt en tenant får lov å sette den — og en
-- senere heving av gulvet kan ikke røre en måling som alt er åpnet.
-- Det er hele grunnen til at det er to tall og ikke ett.
-- ---------------------------------------------------------------------
CREATE TABLE medarbeiderkrav (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kravversjon INT NOT NULL CHECK (kravversjon >= 1),
    -- HUSETS GULV ER 5, OG TENANTEN KAN BARE GÅ OPP.
    gruppeterskel_min INT NOT NULL
        CONSTRAINT medarbeiderkrav_terskel_gulv
        CHECK (gruppeterskel_min BETWEEN 5 AND 1000),
    apent_lop_frist_dogn INT NOT NULL
        CHECK (apent_lop_frist_dogn BETWEEN 1 AND 3650),
    versjon_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    satt_av TEXT NOT NULL CHECK (satt_av ~ '[^[:space:]]'),
    CONSTRAINT medarbeiderkrav_pk PRIMARY KEY (tenant, kravversjon)
);

-- ---------------------------------------------------------------------
-- `ansattlop` — DEN ANSATTES FØRSTE UKE.
--
-- `taker_id` peker på `lonnstaker` (M-39). Det er ingen fremmednøkkel,
-- og det er MÅLT og ikke glemt: `lonnstaker` eies av migrator, og en
-- fremmednøkkel dit ville krevd REFERENCES-rett på en tabell M-40 ikke
-- eier. Døra slår opp i registeret i stedet, med den leseretten den
-- faktisk har fått.
--
-- INGEN `navn`-KOLONNE. Modulen vet AT hun er ansatt, ikke hva hun
-- heter. Det er `modulen_bygget_eget_ansattregister` gjort
-- urepresenterbar: et andre register uten navn er ikke et register
-- over mennesker.
-- ---------------------------------------------------------------------
CREATE TABLE ansattlop (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    lop_id UUID NOT NULL,
    taker_id UUID NOT NULL,
    kravversjon INT NOT NULL,
    status TEXT NOT NULL
        CONSTRAINT ansattlop_status_lukket
        CHECK (status IN ('apent', 'fullfort', 'avbrutt')),
    startet TIMESTAMPTZ NOT NULL DEFAULT now(),
    startet_av TEXT NOT NULL CHECK (startet_av ~ '[^[:space:]]'),
    avsluttet_ts TIMESTAMPTZ,
    avsluttet_av TEXT,
    CONSTRAINT ansattlop_pk PRIMARY KEY (tenant, lop_id),
    CONSTRAINT ansattlop_krav_fk FOREIGN KEY (tenant, kravversjon)
        REFERENCES medarbeiderkrav (tenant, kravversjon),
    -- TOTALITETEN SOM CHECK, 094s form: et løp er enten åpent uten
    -- avslutning, eller lukket med begge delene utfylt.
    CONSTRAINT ansattlop_avslutning_hel CHECK (
        (status = 'apent'
             AND avsluttet_ts IS NULL AND avsluttet_av IS NULL)
        OR (status <> 'apent'
             AND avsluttet_ts IS NOT NULL AND avsluttet_av IS NOT NULL))
);
-- ETT ÅPENT LØP PER ANSATT. To parallelle førsteuker er ikke en
-- tilstand som betyr noe.
CREATE UNIQUE INDEX ansattlop_ett_apent_per_taker
    ON ansattlop (tenant, taker_id) WHERE status = 'apent';
CREATE INDEX ansattlop_apne ON ansattlop (tenant, startet)
    WHERE status = 'apent';

-- ---------------------------------------------------------------------
-- `ansattlopsteg` — STEGENE, MED ET LUKKET SETT.
--
-- 137s `playbooksteg` er formen: en åpen `stegtype` ville gjort
-- katalogen til fritekst, og da kan ingen port måle hva løpet
-- inneholder. Settet er husets, og det utvides i en migrasjon.
-- ---------------------------------------------------------------------
CREATE TABLE ansattlopsteg (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    lop_id UUID NOT NULL,
    stegnr INT NOT NULL CHECK (stegnr BETWEEN 1 AND 100),
    stegtype TEXT NOT NULL
        CONSTRAINT ansattlopsteg_stegtype_lukket
        CHECK (stegtype IN ('utstyr_utlevert', 'tilgang_opprettet',
                            'kontrakt_utstedt', 'introsamtale_holdt',
                            'hms_gjennomgatt', 'fadder_tildelt',
                            'opplaering_fullfort')),
    utfort_ts TIMESTAMPTZ,
    utfort_av TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ansattlopsteg_pk PRIMARY KEY (tenant, lop_id, stegnr),
    CONSTRAINT ansattlopsteg_lop_fk FOREIGN KEY (tenant, lop_id)
        REFERENCES ansattlop (tenant, lop_id),
    CONSTRAINT ansattlopsteg_type_unik UNIQUE (tenant, lop_id, stegtype),
    CONSTRAINT ansattlopsteg_utforing_hel CHECK (
        (utfort_ts IS NULL AND utfort_av IS NULL)
        OR (utfort_ts IS NOT NULL AND utfort_av IS NOT NULL))
);

-- ---------------------------------------------------------------------
-- `medarbeiderkontrakt` — DOKUMENTET, SPORET TIL MALEN.
--
-- «Kontrakter kan alltid spores til malversjon og kildefelt», ord for
-- ord fra akseptansekravet.
--
-- `malversjon_id` er NOT NULL. Det gjør `kontrakt_uten_malversjon`
-- urepresenterbar — ikke usannsynlig, men umulig å skrive.
--
-- INGEN FREMMEDNØKKEL, SAMME GRUNN SOM `ansattlop.taker_id`: M-40 eier
-- ikke `malversjon` og har ingen REFERENCES-rett der. Døra slår opp,
-- og oppslaget krever `status = 'publisert'` — et utkast er ikke en
-- kontrakt, og en tilbaketrukket mal er ikke en gyldig hjemmel.
--
-- `malversjon_hash` KOPIERES VED UTSTEDELSE, og sammen med
-- `REVOKE UPDATE` på tabellen er det `juridisk_klausul_endret` gjort
-- umulig: teksten kontrakten hviler på er festet til et tall ingen kan
-- skrive om etterpå. Endres malen, får den et nytt versjonsnummer og
-- en ny hash — og de gamle kontraktene peker fortsatt på det de
-- faktisk hvilte på.
-- ---------------------------------------------------------------------
CREATE TABLE medarbeiderkontrakt (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kontrakt_id UUID NOT NULL,
    taker_id UUID NOT NULL,
    malversjon_id UUID NOT NULL,
    -- MALENS EGEN HASH, KOPIERT. Ikke kontraktens innhold: v1 lagrer
    -- ingen kontraktstekst i det hele tatt, bare hvilken låst mal den
    -- ble til av og hvilke felter som ble fylt.
    malversjon_hash TEXT NOT NULL CHECK (malversjon_hash ~ '[^[:space:]]'),
    malversjonsnr INT NOT NULL CHECK (malversjonsnr >= 1),
    utstedt TIMESTAMPTZ NOT NULL DEFAULT now(),
    utstedt_av TEXT NOT NULL CHECK (utstedt_av ~ '[^[:space:]]'),
    CONSTRAINT medarbeiderkontrakt_pk PRIMARY KEY (tenant, kontrakt_id)
);
CREATE INDEX medarbeiderkontrakt_pr_taker
    ON medarbeiderkontrakt (tenant, taker_id);
CREATE INDEX medarbeiderkontrakt_pr_mal
    ON medarbeiderkontrakt (tenant, malversjon_id);

-- `medarbeiderkontraktfelt` — KILDEFELTENE, EN RAD PER FELT.
--
-- «Kildefelt» i akseptansekravet er flertall og betyr hvilke av malens
-- felter som faktisk ble fylt. VERDIENE LAGRES IKKE: en kontraktverdi
-- er persondata, og v1 har ingen grunn til å eie den. Modulen svarer
-- på «hvilke felter», ikke «hva sto det».
CREATE TABLE medarbeiderkontraktfelt (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kontrakt_id UUID NOT NULL,
    feltnokkel TEXT NOT NULL
        CHECK (feltnokkel ~ '^[a-z][a-z0-9_.]{0,62}$'),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT medarbeiderkontraktfelt_pk
        PRIMARY KEY (tenant, kontrakt_id, feltnokkel),
    CONSTRAINT medarbeiderkontraktfelt_kontrakt_fk
        FOREIGN KEY (tenant, kontrakt_id)
        REFERENCES medarbeiderkontrakt (tenant, kontrakt_id)
);

-- ---------------------------------------------------------------------
-- `pulsmaaling` — MÅLINGEN, OG TERSKELEN SOM GJALDT DEN.
--
-- `gruppeterskel` ER LÅST FRA FØDSELEN. Ikke ved en CHECK, men ved at
-- ingen har retten til å skrive den: `REVOKE UPDATE` på hele tabellen,
-- og bare `lukket_ts`/`lukket_av` gis tilbake som kolonnegrant.
--
-- HVORFOR IKKE EN TRIGGER? Fordi en trigger er en sjekk som kjører, og
-- en rett som ikke finnes er en handling som ikke kan forsøkes. Huset
-- har valgt det siste hver gang det har hatt valget.
-- ---------------------------------------------------------------------
CREATE TABLE pulsmaaling (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    maaling_id UUID NOT NULL,
    tittel TEXT NOT NULL CHECK (length(btrim(tittel)) BETWEEN 1 AND 200),
    -- KOPIERT FRA KRAVET VED ÅPNING, ikke lest fra det ved lesing.
    -- En tenant som hever gulvet i morgen skal ikke gjøre gårsdagens
    -- måling ulovlig å lese; hun skal gjøre morgendagens strengere.
    gruppeterskel INT NOT NULL
        CONSTRAINT pulsmaaling_terskel_gulv
        CHECK (gruppeterskel BETWEEN 5 AND 1000),
    kravversjon INT NOT NULL,
    apnet TIMESTAMPTZ NOT NULL DEFAULT now(),
    apnet_av TEXT NOT NULL CHECK (apnet_av ~ '[^[:space:]]'),
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT,
    CONSTRAINT pulsmaaling_pk PRIMARY KEY (tenant, maaling_id),
    CONSTRAINT pulsmaaling_krav_fk FOREIGN KEY (tenant, kravversjon)
        REFERENCES medarbeiderkrav (tenant, kravversjon),
    CONSTRAINT pulsmaaling_lukking_hel CHECK (
        (lukket_ts IS NULL AND lukket_av IS NULL)
        OR (lukket_ts IS NOT NULL AND lukket_av IS NOT NULL))
);
CREATE INDEX pulsmaaling_apne ON pulsmaaling (tenant, apnet)
    WHERE lukket_ts IS NULL;

-- ---------------------------------------------------------------------
-- `pulssvar` — OG DEN VIKTIGSTE KOLONNEN ER DEN SOM IKKE STÅR HER.
--
-- DET FINNES INGEN `taker_id`. Ikke nullbar, ikke pseudonymisert,
-- ikke hashet — kolonnen eksisterer ikke.
--
-- Det er forskjellen på et løfte og en umulighet. En nullbar
-- `taker_id` ville vært et løfte om at ingen fyller den. En hashet
-- ville vært et løfte om at ingen slår den opp. En kolonne som ikke
-- finnes kan ingen dør skrive, ingen feilmelding lekke, ingen
-- feilsøking finne og ingen framtidig utvikler «bare midlertidig»
-- fylle.
--
-- `gruppe` er en FRI TEKST og skal være det: en tenant grupperer etter
-- avdeling, lokasjon eller lag, og huset skal ikke ha en mening om
-- hvilken. Men den er ikke en person — CHECK-en under nekter en
-- gruppe som er kort nok til å være et navn eller en id, og det er
-- den eneste vakten som gir mening når feltet er fritt.
-- ---------------------------------------------------------------------
CREATE TABLE pulssvar (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    svar_id UUID NOT NULL,
    maaling_id UUID NOT NULL,
    gruppe TEXT NOT NULL
        CONSTRAINT pulssvar_gruppe_er_ikke_en_person
        CHECK (length(btrim(gruppe)) BETWEEN 2 AND 100),
    verdi INT NOT NULL CHECK (verdi BETWEEN 1 AND 5),
    avgitt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pulssvar_pk PRIMARY KEY (tenant, svar_id),
    CONSTRAINT pulssvar_maaling_fk FOREIGN KEY (tenant, maaling_id)
        REFERENCES pulsmaaling (tenant, maaling_id)
);
CREATE INDEX pulssvar_pr_gruppe ON pulssvar (tenant, maaling_id, gruppe);

-- PRISEN FOR AT KOLONNEN IKKE FINNES, OG DEN SKAL STÅ:
--
-- Uten en personnøkkel kan modulen ikke hindre at den samme personen
-- svarer to ganger. DET ER EN EKTE SVAKHET, og den er valgt med åpne
-- øyne: alternativet er en nøkkel som kobler et svar til et menneske,
-- og da er målingen ikke anonym lenger uansett hvor godt nøkkelen
-- gjemmes.
--
-- En engangslenke per invitasjon ville løst begge deler, og den hører
-- hjemme i v2 sammen med utsendelsen — som v1 heller ikke har.

-- ---------------------------------------------------------------------
-- `medarbeiderfunn` — MODULENS MÅLING AV SEG SELV.
-- ---------------------------------------------------------------------
CREATE TABLE medarbeiderfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    funn_id UUID NOT NULL DEFAULT gen_random_uuid(),
    funntype TEXT NOT NULL
        CONSTRAINT medarbeiderfunn_funntype_lukket
        CHECK (funntype IN (
            -- DE SEKS SOM ALDRI KAN REISES. De står her for å bli
            -- NAVNGITT, ikke for å bli fylt — og en port måler for
            -- hver av dem at datamodellen utelukker den.
            'beslutning_med_rettsvirkning',
            'individprofil_bygget',
            'puls_identifiserte_en_person',
            'gruppeterskel_endret',
            'kontrakt_uten_malversjon',
            'krav_mangler',
            -- DE FIRE SOM FAKTISK KAN REISES.
            'apent_lop_over_frist',
            'ansatt_uten_lop',
            'maaling_uten_lesbar_gruppe',
            'kontrakt_paa_tilbaketrukket_mal')),
    referanse TEXT NOT NULL CHECK (referanse ~ '[^[:space:]]'),
    detalj TEXT NOT NULL CHECK (detalj ~ '[^[:space:]]'),
    apen BOOLEAN NOT NULL DEFAULT true,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT,
    lukket_grunn TEXT,
    CONSTRAINT medarbeiderfunn_lukkingen_er_hel CHECK (
        apen = (lukket_ts IS NULL AND lukket_av IS NULL
                AND lukket_grunn IS NULL)),
    CONSTRAINT medarbeiderfunn_pk PRIMARY KEY (tenant, funn_id)
);
-- ETT ÅPENT FUNN PER TYPE OG REFERANSE. Sveipen skal oppdatere
-- `sist_sett`, ikke lage en ny rad hver natt.
CREATE UNIQUE INDEX medarbeiderfunn_ett_apent
    ON medarbeiderfunn (tenant, funntype, referanse) WHERE apen;

-- =====================================================================
-- RETTIGHETENE.
--
-- TABELLENE BLIR HOS MIGRATOR, OG DET ER HUSETS FORM (139 gjør det
-- samme). Modulrollen får grants, ikke eierskap — og det er nettopp
-- derfor `REVOKE UPDATE` under betyr noe: en eier kan ikke fratas
-- retten til sin egen tabell, en grantmottaker kan.
--
-- Første utkast satte `ALTER TABLE ... OWNER TO`, og migrasjonen falt
-- på `must be owner of table medarbeiderkrav` i sin egen RLS-blokk.
-- Feilen var verdt å gjøre: hadde den gått gjennom, ville de fire
-- REVOKE-ene under vært stille virkningsløse.
-- =====================================================================

-- ---------------------------------------------------------------------
-- RADVAKT. FORCE RLS PÅ ALLE ÅTTE.
--
-- GRANTEN HER ER DEN BREDE, OG REVOKE-ENE UNDER SMALNER DEN INN.
-- Rekkefølgen er ikke kosmetikk: en `REVOKE UPDATE` skrevet før denne
-- løkka ville blitt delt ut på nytt av løkka selv, og vernet ville
-- sett ut som det sto der uten å gjøre noe.
-- ---------------------------------------------------------------------
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['medarbeiderkrav', 'ansattlop',
                             'ansattlopsteg', 'medarbeiderkontrakt',
                             'medarbeiderkontraktfelt', 'pulsmaaling',
                             'pulssvar', 'medarbeiderfunn']
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format($f$CREATE POLICY tenant_isolasjon ON public.%I
            USING (tenant = current_setting('disponit.tenant', true))
            WITH CHECK (tenant = current_setting('disponit.tenant', true))$f$, t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO'
                       ' disponit_medarbeider_eier', t);
    END LOOP;
END $$;

-- ---------------------------------------------------------------------
-- DE FIRE `REVOKE`-ENE SOM ER HELE VERNET.
--
-- Ingen av dem er en sjekk. Alle fire er en RETT SOM IKKE FINNES, og
-- forskjellen er den samme hver gang: en sjekk kan omgås av den som
-- skriver neste dør, en manglende rett kan ikke.
--
--   `medarbeiderkrav`         — append-only, 135s form.
--   `medarbeiderkontrakt`     — en signert klausul endres ikke.
--   `medarbeiderkontraktfelt` — og heller ikke hvilke felter den bar.
--   `pulsmaaling`             — TERSKELEN. Se kolonnegranten under.
-- ---------------------------------------------------------------------
REVOKE UPDATE ON public.medarbeiderkrav FROM disponit_medarbeider_eier;
REVOKE UPDATE ON public.medarbeiderkontrakt FROM disponit_medarbeider_eier;
REVOKE UPDATE ON public.medarbeiderkontraktfelt
    FROM disponit_medarbeider_eier;
REVOKE UPDATE ON public.pulsmaaling FROM disponit_medarbeider_eier;
-- OG SÅ NØYAKTIG DE TO KOLONNENE EN LUKKING TRENGER, TILBAKE.
-- `gruppeterskel` er ikke blant dem, og det er ikke en forglemmelse:
-- det er `gruppeterskel_endret_etter_maaling` gjort umulig å forsøke.
GRANT UPDATE (lukket_ts, lukket_av)
    ON public.pulsmaaling TO disponit_medarbeider_eier;

-- OG SVARET SELV: en avgitt puls redigeres ikke, og slettes ikke av
-- modulen. Det er ikke persondata å angre, det er en måling å bevare.
REVOKE UPDATE, DELETE ON public.pulssvar FROM disponit_medarbeider_eier;

-- SVEIPENS KRYSS-TENANT-POLICY (130s LÆRDOM).
--
-- Under FORCE RLS ser en spørring UTEN `disponit.tenant` INGENTING —
-- ikke alt. En sveip som skulle finne tenantene å gå gjennom ville
-- vært blind, og en blind sveip melder null funn og ser frisk ut.
CREATE POLICY m40_sveip_tenantliste ON medarbeiderkrav
    FOR SELECT
    USING (current_setting('disponit.tenant', true) IS NULL
           OR current_setting('disponit.tenant', true) = '');

-- =====================================================================
-- HERFRA EIES DØRENE AV MEDARBEIDEREIEREN.
-- =====================================================================
SET LOCAL ROLE disponit_medarbeider_eier;

-- ---------------------------------------------------------------------
-- `m40_evidens` — HUSETS BEVISSPOR. 137/138/139s form.
-- ---------------------------------------------------------------------
CREATE FUNCTION m40_evidens(p_tenant TEXT, p_ref UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm40_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm40_medarbeider', 'handling', p_handling,
        'ref', p_ref::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm40_medarbeider',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:medarbeider', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;

-- DETALJEN ER ALDRI ET SVAR OG ALDRI ET NAVN.
--
-- Sporet skal si AT en kontrakt ble utstedt og hvilken malversjon den
-- hvilte på — aldri hva som sto i feltene, og aldri hva noen svarte på
-- en puls. Et bevisspor som bar svarene ville gjort revisjonsloggen
-- til den personnøkkelen `pulssvar` ikke har.

-- ---------------------------------------------------------------------
-- `m40_funn_er_sveipens` — HVEM SOM KAN LUKKE HVA.
--
-- SETTET ER NØYAKTIG DET SVEIPEN REISER, og M-28s lærdom er lest før
-- den ble gjentatt: et funn sveipen reiser og et menneske lukker, blir
-- reist på nytt neste natt. Det er å lukke en måling og ikke en sak.
--
-- DE SEKS UMULIGE STÅR IKKE HER, og `krav_mangler` heller ikke —
-- sveipeløkka går over tenanter som HAR et krav, så en tenant uten
-- krav besøkes aldri. De er navngitt i tabellens CHECK, som er der de
-- hører hjemme: et navn på en tilstand ingen kan skape.
-- ---------------------------------------------------------------------
CREATE FUNCTION m40_funn_er_sveipens(p_funntype TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT p_funntype IN ('apent_lop_over_frist',
                          'ansatt_uten_lop',
                          'maaling_uten_lesbar_gruppe',
                          'kontrakt_paa_tilbaketrukket_mal')
$$;
REVOKE ALL ON FUNCTION m40_funn_er_sveipens(TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- `m40_gjeldende_krav` — DEN GJELDENDE RADEN, IKKE `max()`.
--
-- Kravet er append-only, så det finnes flere rader per tenant. `max()`
-- på en enkeltkolonne ville plukket den høyeste verdien på tvers av
-- versjoner og satt sammen et krav som aldri har eksistert — 137s
-- feil, funnet av CodeRabbit og rettet der.
-- ---------------------------------------------------------------------
CREATE FUNCTION m40_gjeldende_krav(p_tenant TEXT)
RETURNS TABLE (kravversjon INT, gruppeterskel_min INT,
               apent_lop_frist_dogn INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT k.kravversjon, k.gruppeterskel_min, k.apent_lop_frist_dogn
      FROM public.medarbeiderkrav k
     WHERE k.tenant = p_tenant
     ORDER BY k.kravversjon DESC
     LIMIT 1
$$;
REVOKE ALL ON FUNCTION m40_gjeldende_krav(TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- `m40_sett_krav` — APPEND-ONLY, VERSJONEN TILDELES AV DØRA.
--
-- 135/137/138/139s form. `max(kravversjon) + 1` uten rådgivende lås er
-- husets etablerte mønster gjennom SEKS moduler, og denne er den
-- sjuende. CodeRabbit har meldt kappløpet på M-28, og svaret er det
-- samme her: primærnøkkelen fanger det, den andre kalleren får en 400,
-- og en lås i én modul ville gjort den til unntaket. Skal det rettes,
-- rettes det for alle sju i én migrasjon med sin egen port.
-- ---------------------------------------------------------------------
CREATE FUNCTION m40_sett_krav(p_tenant TEXT, p_gruppeterskel_min INT,
                              p_apent_lop_frist_dogn INT, p_av TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm40_sett_krav');
    SELECT coalesce(max(kravversjon), 0) + 1 INTO v_versjon
      FROM public.medarbeiderkrav WHERE tenant = p_tenant;
    INSERT INTO public.medarbeiderkrav
        (tenant, kravversjon, gruppeterskel_min, apent_lop_frist_dogn,
         satt_av)
    VALUES (p_tenant, v_versjon, p_gruppeterskel_min,
            p_apent_lop_frist_dogn, p_av);
    PERFORM public.m40_evidens(p_tenant, NULL, 'sett_krav', p_av,
        jsonb_build_object('kravversjon', v_versjon,
                           'gruppeterskel_min', p_gruppeterskel_min));
    RETURN v_versjon;
END $$;

-- ---------------------------------------------------------------------
-- `m40_start_lop` — OG HER SLÅS ANSATTREGISTERET OPP, IKKE BYGGES.
--
-- Døra krever at `taker_id` finnes i `lonnstaker` OG er aktiv. Det er
-- den eneste måten «jobber hun her» besvares i dette huset, og M-40
-- spør framfor å svare selv.
-- ---------------------------------------------------------------------
CREATE FUNCTION m40_start_lop(p_tenant TEXT, p_lop_id UUID,
                              p_taker_id UUID, p_kravversjon INT,
                              p_av TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm40_start_lop');
    IF NOT EXISTS (SELECT 1 FROM public.lonnstaker t
                    WHERE t.tenant = p_tenant
                      AND t.taker_id = p_taker_id
                      AND t.aktiv) THEN
        RAISE EXCEPTION 'm40: % er ikke en aktiv lonnstaker hos %.'
                        ' Ansattregisteret er M-39s, og M-40 spor det',
            p_taker_id, p_tenant;
    END IF;
    INSERT INTO public.ansattlop
        (tenant, lop_id, taker_id, kravversjon, status, startet_av)
    VALUES (p_tenant, p_lop_id, p_taker_id, p_kravversjon, 'apent', p_av);
    PERFORM public.m40_evidens(p_tenant, p_lop_id, 'start_lop', p_av,
        jsonb_build_object('kravversjon', p_kravversjon));
END $$;

-- `m40_utfor_steg` — ET STEG BLE GJORT, OG AV HVEM.
CREATE FUNCTION m40_utfor_steg(p_tenant TEXT, p_lop_id UUID,
                               p_stegnr INT, p_stegtype TEXT,
                               p_av TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm40_utfor_steg');
    IF NOT EXISTS (SELECT 1 FROM public.ansattlop l
                    WHERE l.tenant = p_tenant AND l.lop_id = p_lop_id
                      AND l.status = 'apent') THEN
        RAISE EXCEPTION 'm40: lopet % er ikke apent', p_lop_id;
    END IF;
    INSERT INTO public.ansattlopsteg
        (tenant, lop_id, stegnr, stegtype, utfort_ts, utfort_av)
    VALUES (p_tenant, p_lop_id, p_stegnr, p_stegtype, now(), p_av);
    PERFORM public.m40_evidens(p_tenant, p_lop_id, 'utfor_steg', p_av,
        jsonb_build_object('stegtype', p_stegtype, 'stegnr', p_stegnr));
END $$;

-- `m40_avslutt_lop` — FULLFØRT ELLER AVBRUTT, ALDRI BARE BORTE.
CREATE FUNCTION m40_avslutt_lop(p_tenant TEXT, p_lop_id UUID,
                                p_status TEXT, p_av TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm40_avslutt_lop');
    IF p_status NOT IN ('fullfort', 'avbrutt') THEN
        RAISE EXCEPTION 'm40: % er ikke en avslutning', p_status;
    END IF;
    UPDATE public.ansattlop
       SET status = p_status, avsluttet_ts = now(), avsluttet_av = p_av
     WHERE tenant = p_tenant AND lop_id = p_lop_id AND status = 'apent';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm40: lopet % var ikke apent', p_lop_id;
    END IF;
    PERFORM public.m40_evidens(p_tenant, p_lop_id, 'avslutt_lop', p_av,
        jsonb_build_object('status', p_status));
END $$;

-- ---------------------------------------------------------------------
-- `m40_utsted_kontrakt` — MALEN MÅ VÆRE PUBLISERT, OG HASHEN FESTES.
--
-- TRE VILKÅR, OG HVERT AV DEM ER ET AKSEPTANSEKRAV:
--
--   1. Malversjonen må finnes og være `publisert`. Et utkast er ikke
--      en hjemmel, og en tilbaketrukket mal er en hjemmel noen har
--      fjernet med vilje.
--   2. `innhold_hash` kopieres inn. Sammen med `REVOKE UPDATE` er det
--      «juridiske klausuler er låst» gjort til en umulighet framfor en
--      regel: ingen kan skrive om hvilken tekst kontrakten hvilte på.
--   3. Kildefeltene skrives som rader. Uten dem kan modulen si HVILKEN
--      mal, men ikke HVILKE FELTER — og akseptansekravet ber om begge.
--
-- FELTNØKLENE MÅ FINNES I MALEN. En kontrakt som viser til et felt
-- malen ikke har, er ikke sporbar til noe.
-- ---------------------------------------------------------------------
CREATE FUNCTION m40_utsted_kontrakt(p_tenant TEXT, p_kontrakt_id UUID,
                                    p_taker_id UUID,
                                    p_malversjon_id UUID,
                                    p_feltnokler TEXT[], p_av TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_hash TEXT; v_nr INT; v_ukjent TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm40_utsted_kontrakt');
    IF NOT EXISTS (SELECT 1 FROM public.lonnstaker t
                    WHERE t.tenant = p_tenant
                      AND t.taker_id = p_taker_id AND t.aktiv) THEN
        RAISE EXCEPTION 'm40: % er ikke en aktiv lonnstaker hos %',
            p_taker_id, p_tenant;
    END IF;
    SELECT v.innhold_hash, v.versjonsnr INTO v_hash, v_nr
      FROM public.malversjon v
     WHERE v.tenant = p_tenant AND v.versjon_id = p_malversjon_id
       AND v.status = 'publisert';
    IF v_hash IS NULL THEN
        RAISE EXCEPTION 'm40: malversjon % finnes ikke publisert hos %.'
                        ' Et utkast er ingen hjemmel, og en'
                        ' tilbaketrukket mal er en fjernet en',
            p_malversjon_id, p_tenant;
    END IF;
    IF p_feltnokler IS NULL OR cardinality(p_feltnokler) = 0 THEN
        RAISE EXCEPTION 'm40: en kontrakt uten kildefelt er ikke sporbar';
    END IF;
    SELECT n INTO v_ukjent
      FROM unnest(p_feltnokler) AS n
     WHERE NOT EXISTS (SELECT 1 FROM public.malfelt f
                        WHERE f.tenant = p_tenant
                          AND f.versjon_id = p_malversjon_id
                          AND f.feltnokkel = n)
     LIMIT 1;
    IF v_ukjent IS NOT NULL THEN
        RAISE EXCEPTION 'm40: feltet % finnes ikke i malversjon %',
            v_ukjent, p_malversjon_id;
    END IF;
    INSERT INTO public.medarbeiderkontrakt
        (tenant, kontrakt_id, taker_id, malversjon_id, malversjon_hash,
         malversjonsnr, utstedt_av)
    VALUES (p_tenant, p_kontrakt_id, p_taker_id, p_malversjon_id,
            v_hash, v_nr, p_av);
    INSERT INTO public.medarbeiderkontraktfelt
        (tenant, kontrakt_id, feltnokkel)
    SELECT p_tenant, p_kontrakt_id, n FROM unnest(p_feltnokler) AS n;
    -- SPORET BÆRER MALEN, IKKE INNHOLDET.
    PERFORM public.m40_evidens(p_tenant, p_kontrakt_id,
        'utsted_kontrakt', p_av,
        jsonb_build_object('malversjonsnr', v_nr,
                           'antall_felt', cardinality(p_feltnokler)));
END $$;

-- ---------------------------------------------------------------------
-- `m40_apne_maaling` — TERSKELEN LÅSES HER, ÉN GANG.
--
-- Den leses fra tenantens gjeldende krav og KOPIERES inn. Etterpå har
-- ingen retten til å skrive kolonnen, heller ikke denne døra.
--
-- `p_gruppeterskel` får være HØYERE enn gulvet, aldri lavere. En
-- tenant som vil beskytte små grupper bedre enn huset krever, skal få
-- lov; en som vil beskytte dem dårligere, skal ikke.
-- ---------------------------------------------------------------------
CREATE FUNCTION m40_apne_maaling(p_tenant TEXT, p_maaling_id UUID,
                                 p_tittel TEXT, p_gruppeterskel INT,
                                 p_av TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_krav INT; v_gulv INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm40_apne_maaling');
    SELECT kravversjon, gruppeterskel_min INTO v_krav, v_gulv
      FROM public.m40_gjeldende_krav(p_tenant);
    IF v_krav IS NULL THEN
        RAISE EXCEPTION 'm40: % har ingen medarbeiderkrav.'
                        ' Terskelen kan ikke laases mot et gulv som'
                        ' ikke er satt', p_tenant;
    END IF;
    IF p_gruppeterskel < v_gulv THEN
        RAISE EXCEPTION 'm40: terskel % er under tenantens gulv %',
            p_gruppeterskel, v_gulv;
    END IF;
    INSERT INTO public.pulsmaaling
        (tenant, maaling_id, tittel, gruppeterskel, kravversjon, apnet_av)
    VALUES (p_tenant, p_maaling_id, p_tittel, p_gruppeterskel, v_krav,
            p_av);
    PERFORM public.m40_evidens(p_tenant, p_maaling_id, 'apne_maaling',
        p_av, jsonb_build_object('gruppeterskel', p_gruppeterskel));
END $$;

-- ---------------------------------------------------------------------
-- `m40_avgi_puls` — OG DØRA TAR IKKE IMOT EN PERSON.
--
-- Signaturen har ingen `p_taker_id`, og det er ikke fordi den er
-- valgfri: det finnes ingen kolonne å skrive den i. En dør som tok
-- imot en personnøkkel og kastet den, ville vært et løfte. Denne kan
-- ikke bryte et løfte den ikke er i stand til å gi.
--
-- OG SPORET SKRIVES IKKE. Et bevisspor per svar ville hatt tidspunkt,
-- gruppe og aktør i samme rad — og det er den koblingen `pulssvar`
-- nettopp ikke har. Sporet føres på MÅLINGEN når den lukkes.
-- ---------------------------------------------------------------------
CREATE FUNCTION m40_avgi_puls(p_tenant TEXT, p_svar_id UUID,
                              p_maaling_id UUID, p_gruppe TEXT,
                              p_verdi INT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm40_avgi_puls');
    IF NOT EXISTS (SELECT 1 FROM public.pulsmaaling m
                    WHERE m.tenant = p_tenant
                      AND m.maaling_id = p_maaling_id
                      AND m.lukket_ts IS NULL) THEN
        RAISE EXCEPTION 'm40: maalingen % er ikke apen', p_maaling_id;
    END IF;
    INSERT INTO public.pulssvar
        (tenant, svar_id, maaling_id, gruppe, verdi)
    VALUES (p_tenant, p_svar_id, p_maaling_id, p_gruppe, p_verdi);
END $$;

-- `m40_lukk_maaling` — OG DET ER HER SPORET FØRES.
CREATE FUNCTION m40_lukk_maaling(p_tenant TEXT, p_maaling_id UUID,
                                 p_av TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_antall BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm40_lukk_maaling');
    UPDATE public.pulsmaaling
       SET lukket_ts = now(), lukket_av = p_av
     WHERE tenant = p_tenant AND maaling_id = p_maaling_id
       AND lukket_ts IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm40: maalingen % var ikke apen', p_maaling_id;
    END IF;
    SELECT count(*) INTO v_antall FROM public.pulssvar s
     WHERE s.tenant = p_tenant AND s.maaling_id = p_maaling_id;
    -- ANTALLET, ALDRI FORDELINGEN. Et spor som bar snittet ville vært
    -- et aggregat uten terskel, og det er nettopp det doera under
    -- nekter aa gi.
    PERFORM public.m40_evidens(p_tenant, p_maaling_id, 'lukk_maaling',
        p_av, jsonb_build_object('antall_svar', v_antall));
END $$;

-- ---------------------------------------------------------------------
-- `m40_pulsbildet` — AGGREGATET, OG DET ENESTE STEDET SVARENE LESES.
--
-- DEN NEKTER Å SVARE UNDER TERSKELEN, og terskelen er malingens egen —
-- lest fra raden, ikke fra en konstant og ikke fra kravet som gjelder
-- i dag. En tenant som hever gulvet etterpå skal ikke kunne gjøre en
-- lesbar måling ulesbar, og en som senker det skal ikke kunne gjøre en
-- ulesbar måling lesbar.
--
-- `HAVING count(*) >= m.gruppeterskel` ER HELE VERNET, og det er
-- derfor det står i den ENESTE spørringen som rører `pulssvar` på
-- vegne av et menneske. Sveipen teller også, men den ser aldri en
-- verdi — bare om en gruppe er stor nok til å kunne leses.
--
-- INGEN `taker_id` Å UTELATE. Aggregatet grupperer på det eneste
-- feltet som finnes, og det er allerede en gruppe.
-- ---------------------------------------------------------------------
CREATE FUNCTION m40_pulsbildet(p_tenant TEXT, p_maaling_id UUID)
RETURNS TABLE (gruppe TEXT, antall BIGINT, snitt NUMERIC)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm40_pulsbildet');
    RETURN QUERY
    SELECT s.gruppe, count(*)::BIGINT,
           round(avg(s.verdi)::NUMERIC, 2)
      FROM public.pulssvar s
      JOIN public.pulsmaaling m
        ON m.tenant = s.tenant AND m.maaling_id = s.maaling_id
     WHERE s.tenant = p_tenant AND s.maaling_id = p_maaling_id
     GROUP BY s.gruppe, m.gruppeterskel
    HAVING count(*) >= m.gruppeterskel
     ORDER BY s.gruppe;
END $$;
REVOKE ALL ON FUNCTION m40_pulsbildet(TEXT, UUID) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- LESEDØRENE.
-- ---------------------------------------------------------------------
CREATE FUNCTION m40_lopene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (lop_id UUID, taker_id UUID, ekstern_ref TEXT,
               status TEXT, startet TIMESTAMPTZ, steg BIGINT,
               steg_utfort BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm40_lopene');
    RETURN QUERY
    -- `ekstern_ref` OG IKKE `navn`: flaten skal kunne kjenne igjen
    -- ansattnummeret hun alt jobber med, uten at modulen eier navnet.
    SELECT l.lop_id, l.taker_id, t.ekstern_ref, l.status, l.startet,
           count(g.stegnr)::BIGINT,
           count(g.utfort_ts)::BIGINT
      FROM public.ansattlop l
      JOIN public.lonnstaker t
        ON t.tenant = l.tenant AND t.taker_id = l.taker_id
      LEFT JOIN public.ansattlopsteg g
        ON g.tenant = l.tenant AND g.lop_id = l.lop_id
     WHERE l.tenant = p_tenant
     GROUP BY l.lop_id, l.taker_id, t.ekstern_ref, l.status, l.startet
     ORDER BY l.startet DESC
     LIMIT greatest(1, least(coalesce(p_maks, 200), 200));
END $$;
REVOKE ALL ON FUNCTION m40_lopene(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m40_kontraktene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (kontrakt_id UUID, taker_id UUID, ekstern_ref TEXT,
               malversjon_id UUID, malversjonsnr INT, malnavn TEXT,
               malstatus TEXT, felt TEXT[], utstedt TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm40_kontraktene');
    RETURN QUERY
    -- `malstatus` ER MALENS STATUS I DAG, ikke da kontrakten ble
    -- utstedt. Det er med vilje: det er nettopp den forskjellen
    -- `kontrakt_paa_tilbaketrukket_mal` handler om, og et menneske som
    -- ser listen skal se den uten aa maatte slaa opp.
    SELECT k.kontrakt_id, k.taker_id, t.ekstern_ref, k.malversjon_id,
           k.malversjonsnr, f.navn, v.status,
           coalesce(array_agg(kf.feltnokkel ORDER BY kf.feltnokkel)
                    FILTER (WHERE kf.feltnokkel IS NOT NULL),
                    ARRAY[]::TEXT[]),
           k.utstedt
      FROM public.medarbeiderkontrakt k
      JOIN public.lonnstaker t
        ON t.tenant = k.tenant AND t.taker_id = k.taker_id
      JOIN public.malversjon v
        ON v.tenant = k.tenant AND v.versjon_id = k.malversjon_id
      JOIN public.malfamilie f
        ON f.tenant = v.tenant AND f.familie_id = v.familie_id
      LEFT JOIN public.medarbeiderkontraktfelt kf
        ON kf.tenant = k.tenant AND kf.kontrakt_id = k.kontrakt_id
     WHERE k.tenant = p_tenant
     GROUP BY k.kontrakt_id, k.taker_id, t.ekstern_ref, k.malversjon_id,
              k.malversjonsnr, f.navn, v.status, k.utstedt
     ORDER BY k.utstedt DESC
     LIMIT greatest(1, least(coalesce(p_maks, 200), 200));
END $$;
REVOKE ALL ON FUNCTION m40_kontraktene(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m40_maalingene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (maaling_id UUID, tittel TEXT, gruppeterskel INT,
               apnet TIMESTAMPTZ, lukket_ts TIMESTAMPTZ,
               lesbare_grupper BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm40_maalingene');
    RETURN QUERY
    -- ANTALL LESBARE GRUPPER, ALDRI ANTALL SVAR. Et totaltall for en
    -- maaling med én gruppe VILLE VÆRT gruppens tall, og da hadde
    -- terskelen vært omgaatt av oversikten framfor av aggregatet.
    SELECT m.maaling_id, m.tittel, m.gruppeterskel, m.apnet, m.lukket_ts,
           (SELECT count(*) FROM (
                SELECT 1 FROM public.pulssvar s
                 WHERE s.tenant = m.tenant AND s.maaling_id = m.maaling_id
                 GROUP BY s.gruppe
                HAVING count(*) >= m.gruppeterskel) AS g)::BIGINT
      FROM public.pulsmaaling m
     WHERE m.tenant = p_tenant
     ORDER BY m.apnet DESC
     LIMIT greatest(1, least(coalesce(p_maks, 200), 200));
END $$;
REVOKE ALL ON FUNCTION m40_maalingene(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m40_medarbeiderfunn(p_tenant TEXT, p_maks INT)
-- `referanse` ER TEXT, IKKE UUID, OG DET ER 139s FORM.
--
-- Funnene peker på fire ULIKE ting: et løp, en ansatt, en måling og en
-- kontrakt. De er alle UUID-er i dag, men `land_uten_pakke` i 139 var
-- en landkode — og en referansekolonne som bare kan bære en UUID
-- utelukker den neste funntypen før noen har tenkt på den.
--
-- Første utgave sa UUID her mens kolonnen var TEXT. Døra ville feilet
-- ved første kall, og INGEN PORT KALTE DEN. CodeRabbit fant begge
-- deler 6/9.
RETURNS TABLE (funn_id UUID, funntype TEXT, referanse TEXT,
               detalj TEXT, forst_sett TIMESTAMPTZ,
               sist_sett TIMESTAMPTZ, sveipens BOOLEAN)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm40_medarbeiderfunn');
    RETURN QUERY
    SELECT f.funn_id, f.funntype, f.referanse, f.detalj, f.forst_sett,
           f.sist_sett, public.m40_funn_er_sveipens(f.funntype)
      FROM public.medarbeiderfunn f
     WHERE f.tenant = p_tenant AND f.apen
     ORDER BY f.forst_sett DESC
     LIMIT greatest(1, least(coalesce(p_maks, 200), 200));
END $$;
REVOKE ALL ON FUNCTION m40_medarbeiderfunn(TEXT, INT) FROM PUBLIC;

-- `m40_lukk_funn` — OG DØRA NEKTER PÅ SVEIPENS EGNE.
CREATE FUNCTION m40_lukk_funn(p_tenant TEXT, p_funn_id UUID,
                              p_grunn TEXT, p_av TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_type TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm40_lukk_funn');
    SELECT funntype INTO v_type FROM public.medarbeiderfunn
     WHERE tenant = p_tenant AND funn_id = p_funn_id AND apen;
    IF v_type IS NULL THEN
        RAISE EXCEPTION 'm40: funnet % er ikke apent', p_funn_id;
    END IF;
    IF public.m40_funn_er_sveipens(v_type) THEN
        RAISE EXCEPTION 'm40: % lukkes av sveipen naar tilstanden er'
                        ' borte, ikke av et menneske. Aa lukke den her'
                        ' ville vaert aa lukke en maaling og ikke en sak',
            v_type;
    END IF;
    UPDATE public.medarbeiderfunn
       SET apen = false, lukket_ts = now(), lukket_av = p_av,
           lukket_grunn = p_grunn
     WHERE tenant = p_tenant AND funn_id = p_funn_id;
    PERFORM public.m40_evidens(p_tenant, p_funn_id, 'lukk_funn', p_av,
        jsonb_build_object('funntype', v_type));
END $$;

-- ---------------------------------------------------------------------
-- `m40_bildet` — MODULENS EGET TALL, OG DET VIKTIGSTE ER ALLTID 0.
-- ---------------------------------------------------------------------
CREATE FUNCTION m40_bildet(p_tenant TEXT)
RETURNS TABLE (apne_lop BIGINT, fullforte_lop BIGINT,
               kontrakter BIGINT, apne_maalinger BIGINT,
               lesbare_grupper BIGINT, apne_funn BIGINT,
               beslutninger BIGINT, individprofiler BIGINT,
               har_krav BOOLEAN, gruppeterskel_min INT,
               apent_lop_frist_dogn INT, kravversjon INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kv INT; v_gulv INT; v_frist INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm40_bildet');
    -- DEN GJELDENDE KRAVRADEN, IKKE `max()` PER KOLONNE. Kravet er
    -- append-only, og `max()` på hver kolonne for seg ville satt
    -- sammen et krav som aldri har eksistert (137s feil).
    SELECT k.kravversjon, k.gruppeterskel_min, k.apent_lop_frist_dogn
      INTO v_kv, v_gulv, v_frist
      FROM public.m40_gjeldende_krav(p_tenant) k;
    RETURN QUERY
    SELECT
      (SELECT count(*) FROM public.ansattlop l
        WHERE l.tenant = p_tenant AND l.status = 'apent')::BIGINT,
      (SELECT count(*) FROM public.ansattlop l
        WHERE l.tenant = p_tenant AND l.status = 'fullfort')::BIGINT,
      (SELECT count(*) FROM public.medarbeiderkontrakt k
        WHERE k.tenant = p_tenant)::BIGINT,
      (SELECT count(*) FROM public.pulsmaaling m
        WHERE m.tenant = p_tenant AND m.lukket_ts IS NULL)::BIGINT,
      (SELECT count(*) FROM (
           SELECT 1 FROM public.pulssvar s
             JOIN public.pulsmaaling m
               ON m.tenant = s.tenant AND m.maaling_id = s.maaling_id
            WHERE s.tenant = p_tenant
            GROUP BY s.maaling_id, s.gruppe, m.gruppeterskel
           HAVING count(*) >= m.gruppeterskel) AS g)::BIGINT,
      (SELECT count(*) FROM public.medarbeiderfunn f
        WHERE f.tenant = p_tenant AND f.apen)::BIGINT,
      -- DE TO SISTE TALLENE ER ALLTID 0, OG DE STÅR I BILDET NETTOPP
      -- DERFOR.
      --
      -- En modul som holder tilbake en fullmakt, skal VISE at den gjør
      -- det. Et tall som alltid er null er ikke pynt: det er stedet et
      -- menneske kan se etter for å oppdage den dagen det ikke er det.
      --
      -- Og de kan ikke bli annet enn 0: det finnes ingen tabell å
      -- telle en beslutning i, og ingen kolonne å telle en profil i.
      0::BIGINT, 0::BIGINT,
      (v_kv IS NOT NULL), v_gulv, v_frist, v_kv;
END $$;
REVOKE ALL ON FUNCTION m40_bildet(TEXT) FROM PUBLIC;

-- =====================================================================
-- `m40_sveip_medarbeider` — KRYSS-TENANT, ÉN TENANT OM GANGEN.
--
-- 130s LÆRDOM: under FORCE RLS ser en spørring UTEN `disponit.tenant`
-- NULL RADER, og en sveip som spurte på tvers ville rapportert null
-- funn MED GRØNN EXIT-KODE.
--
-- SVEIPEN AVGJØR INGENTING OG VARSLER INGEN. Den sier fra om at en
-- førsteuke har stått åpen over fristen, om at en aktiv ansatt aldri
-- fikk et løp, om at en måling ble samlet inn uten at en eneste gruppe
-- ble stor nok til å kunne leses, og om at en kontrakt hviler på en
-- mal noen siden har trukket tilbake.
--
-- DEN LESER ALDRI EN PULSVERDI. Sveipen teller grupper og sammenligner
-- med terskelen; den ser aldri hva noen svarte. Det er ikke en
-- forsiktighet, det er den samme grensen aggregatdøra har — og en
-- sveip som var unntatt ville vært hullet i den.
-- =====================================================================
CREATE FUNCTION m40_sveip_medarbeider(p_maks_tenanter INT DEFAULT 1000)
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
        SELECT DISTINCT k.tenant FROM public.medarbeiderkrav k
         ORDER BY 1 LIMIT greatest(1, coalesce(p_maks_tenanter, 1000))
    LOOP
        v_antall := v_antall + 1;
        PERFORM set_config('disponit.tenant', v_t, true);

        -- 1. ÅPEN FØRSTEUKE OVER FRISTEN.
        --
        -- DEN GJELDENDE FRISTEN, ikke den lengste som noen gang sto:
        -- kravet er append-only, så `max()` her ville målt mot en frist
        -- som ikke gjelder (137s lærdom, funnet av CodeRabbit).
        WITH krav AS (
            SELECT k.apent_lop_frist_dogn AS frist
              FROM public.medarbeiderkrav k WHERE k.tenant = v_t
             ORDER BY k.kravversjon DESC LIMIT 1),
        treff AS (
            SELECT l.lop_id, l.startet
              FROM public.ansattlop l, krav
             WHERE l.tenant = v_t AND l.status = 'apent'
               AND l.startet < now() - make_interval(days => krav.frist)),
        satt AS (
            INSERT INTO public.medarbeiderfunn
                (tenant, funntype, referanse, detalj)
            SELECT v_t, 'apent_lop_over_frist', t.lop_id::text,
                   'foersteuken har staatt aapen siden '
                   || t.startet::date
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
                DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.medarbeiderfunn f
               SET apen = false, lukket_ts = now(),
                   lukket_av = 'm40_sveip',
                   lukket_grunn = 'loepet er avsluttet eller fristen hevet'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'apent_lop_over_frist'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.lop_id::text = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 2. AKTIV ANSATT UTEN LØP.
        --
        -- Fristen brukes som modningstid: en som ble ansatt i går skal
        -- ikke gi et funn i natt. `lonnstaker.opprettet` er den eneste
        -- datoen huset har for «siden når», og den er M-39s.
        WITH krav AS (
            SELECT k.apent_lop_frist_dogn AS frist
              FROM public.medarbeiderkrav k WHERE k.tenant = v_t
             ORDER BY k.kravversjon DESC LIMIT 1),
        treff AS (
            SELECT t.taker_id, t.ekstern_ref
              FROM public.lonnstaker t, krav
             WHERE t.tenant = v_t AND t.aktiv
               AND t.opprettet < now() - make_interval(days => krav.frist)
               AND NOT EXISTS (SELECT 1 FROM public.ansattlop l
                                WHERE l.tenant = v_t
                                  AND l.taker_id = t.taker_id)),
        satt AS (
            INSERT INTO public.medarbeiderfunn
                (tenant, funntype, referanse, detalj)
            SELECT v_t, 'ansatt_uten_lop', t.taker_id::text,
                   'ansatt ' || t.ekstern_ref || ' har ingen foersteuke'
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
                DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.medarbeiderfunn f
               SET apen = false, lukket_ts = now(),
                   lukket_av = 'm40_sveip',
                   lukket_grunn = 'loepet er startet eller ansatt inaktiv'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'ansatt_uten_lop'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.taker_id::text = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 3. LUKKET MÅLING UTEN EN ENESTE LESBAR GRUPPE.
        --
        -- DETTE ER KLYNGENS SKARPESTE FUNN, og det er verdt å si
        -- hvorfor: en måling der ingen gruppe når terskelen er svar et
        -- menneske har gitt og INGEN NOEN GANG FÅR SE. Det er ikke en
        -- teknisk feil — aggregatet gjør nøyaktig det det skal — men
        -- det er en tenant som har spurt uten å kunne lytte.
        --
        -- ALTERNATIVET VILLE VÆRT Å SENKE TERSKELEN, og det er
        -- nettopp det ingen har retten til. Funnet er derfor ikke
        -- «senk terskelen», det er «du spurte for smått».
        --
        -- SVEIPEN TELLER, DEN LESER ALDRI. `count(*)` per gruppe og en
        -- sammenligning med terskelen — ingen `verdi` passerer her.
        WITH treff AS (
            SELECT m.maaling_id, m.tittel
              FROM public.pulsmaaling m
             WHERE m.tenant = v_t AND m.lukket_ts IS NOT NULL
               AND EXISTS (SELECT 1 FROM public.pulssvar s
                            WHERE s.tenant = v_t
                              AND s.maaling_id = m.maaling_id)
               AND NOT EXISTS (
                    SELECT 1 FROM public.pulssvar s
                     WHERE s.tenant = v_t AND s.maaling_id = m.maaling_id
                     GROUP BY s.gruppe
                    HAVING count(*) >= m.gruppeterskel)),
        satt AS (
            INSERT INTO public.medarbeiderfunn
                (tenant, funntype, referanse, detalj)
            SELECT v_t, 'maaling_uten_lesbar_gruppe', t.maaling_id::text,
                   'maalingen "' || t.tittel || '" har svar, men ingen'
                   || ' gruppe naar terskelen — ingen faar se dem'
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
                DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.medarbeiderfunn f
               SET apen = false, lukket_ts = now(),
                   lukket_av = 'm40_sveip',
                   lukket_grunn = 'en gruppe naar terskelen'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'maaling_uten_lesbar_gruppe'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.maaling_id::text = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 4. KONTRAKT PÅ EN TILBAKETRUKKET MAL.
        --
        -- Kontrakten er uforanderlig, og det skal den være. Men en mal
        -- som er trukket tilbake ETTER at kontrakten ble utstedt, er
        -- noen som har bestemt at teksten ikke lenger skal brukes — og
        -- da skal et menneske vite hvilke avtaler som hviler på den.
        --
        -- FUNNET SIER IKKE AT KONTRAKTEN ER UGYLDIG. Den var gyldig da
        -- den ble utstedt, og `malversjon_hash` beviser hva den hvilte
        -- på. Funnet sier at noen bør se på den.
        WITH treff AS (
            SELECT k.kontrakt_id, k.malversjonsnr
              FROM public.medarbeiderkontrakt k
              JOIN public.malversjon v
                ON v.tenant = k.tenant AND v.versjon_id = k.malversjon_id
             WHERE k.tenant = v_t AND v.status = 'tilbaketrukket'),
        satt AS (
            INSERT INTO public.medarbeiderfunn
                (tenant, funntype, referanse, detalj)
            SELECT v_t, 'kontrakt_paa_tilbaketrukket_mal',
                   t.kontrakt_id::text,
                   'kontrakten hviler paa malversjon '
                   || t.malversjonsnr || ', som er trukket tilbake'
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
                DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.medarbeiderfunn f
               SET apen = false, lukket_ts = now(),
                   lukket_av = 'm40_sveip',
                   lukket_grunn = 'malversjonen er ikke lenger tilbaketrukket'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'kontrakt_paa_tilbaketrukket_mal'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.kontrakt_id::text = f.referanse)
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
                AND p.proname LIKE 'm40\_%'
                AND pg_get_userbyid(p.proowner) = current_user
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', r.sig);
    END LOOP;
END $$;

GRANT EXECUTE ON FUNCTION m40_sett_krav(TEXT, INT, INT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m40_start_lop(TEXT, UUID, UUID, INT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m40_utfor_steg(TEXT, UUID, INT, TEXT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m40_avslutt_lop(TEXT, UUID, TEXT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m40_utsted_kontrakt(TEXT, UUID, UUID, UUID,
    TEXT[], TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m40_apne_maaling(TEXT, UUID, TEXT, INT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m40_avgi_puls(TEXT, UUID, UUID, TEXT, INT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m40_lukk_maaling(TEXT, UUID, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m40_lukk_funn(TEXT, UUID, TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m40_lopene(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m40_kontraktene(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m40_maalingene(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m40_pulsbildet(TEXT, UUID) TO disponit;
GRANT EXECUTE ON FUNCTION m40_medarbeiderfunn(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m40_bildet(TEXT) TO disponit;

-- SVEIPEROLLEN NÅR ÉN FUNKSJON, OG BARE DEN (111s form).
GRANT EXECUTE ON FUNCTION m40_sveip_medarbeider(INT)
    TO disponit_medarbeidersveip;

RESET ROLE;

-- =====================================================================
-- M-36s FUNNKATALOG (132).
-- =====================================================================
INSERT INTO m36_funnregister
    (relasjon, modul, typekolonne, apenform, begrunnelse)
VALUES
    ('medarbeiderfunn', 'm40_medarbeider', 'funntype', 'apen_kolonne',
     'husets form')
ON CONFLICT (relasjon) DO NOTHING;
GRANT SELECT ON medarbeiderfunn TO disponit_optimalisator_eier;

-- =====================================================================
-- M-4s RETENSJONSREGISTER (093).
--
-- ÅTTE LAGRE, OG ETT AV DEM ER KLYNGENS EGENTLIGE PRØVE.
--
-- `m40_pulssvar` FØRES SOM `driftsspor`, IKKE SOM `persondata` — og
-- den påstanden må kunne BEVISES av kolonnene, ikke av dommen.
--
-- Beviset er at det ikke finnes en `taker_id` å slette. En sletting
-- ville ikke fjernet en kobling, for det finnes ingen: raden bærer en
-- gruppe, et tall mellom 1 og 5 og et tidspunkt.
--
-- KLYNGE 10-FUNDAMENTET KALTE DENNE DOMMEN `anonym_ved_fodsel` og sa
-- at den var husets. DET STEMTE IKKE. 093s CHECK tillater tre dommer,
-- og den fjerde finnes ikke. Å utvide M-4s register fra M-40s
-- migrasjon ville vært at den siste modulen endret retensjonsregisteret
-- for å få plass til seg selv — og det er nøyaktig den formen
-- fundamentet selv advarer mot. Dokumentlinjen er rettet i stedet.
--
-- `uten_frist_akseptert` OG IKKE `uten_frist_apen` FOR PULSSVARENE:
-- forskjellen er om fristen er ubestemt eller om fraværet er valgt.
-- Her er det valgt, og grunnen er at det ikke finnes persondata å
-- sette en frist for.
-- =====================================================================
SET LOCAL ROLE disponit_lager_eier;
INSERT INTO retensjonslager
    (lager_id, relasjon, klasse, tenantkolonne, alderskolonne,
     reapetkolonne, fristkilde, frist_dogn, reaper, dom,
     dom_begrunnelse, dom_migrasjon)
VALUES
    ('m40_medarbeiderkrav', 'medarbeiderkrav', 'konfigurasjon', 'tenant',
     'versjon_ts', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'Kravversjonene er referert av loep og maalinger og kan ikke'
     ' slettes uavhengig av dem.', '140'),
    ('m40_ansattlop', 'ansattlop', 'persondata', 'tenant', 'startet',
     NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'Loepet peker paa et menneske via taker_id. Fristen foelger'
     ' ansettelsesforholdet, og den er ikke bestemt i v1.', '140'),
    ('m40_ansattlopsteg', 'ansattlopsteg', 'persondata', 'tenant',
     'opprettet', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'Stegene sier hva som ble gjort for et navngitt menneske og'
     ' foelger loepet.', '140'),
    ('m40_medarbeiderkontrakt', 'medarbeiderkontrakt', 'persondata',
     'tenant', 'utstedt', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'En kontrakt har oppbevaringsplikt, og fristen er ikke husets aa'
     ' sette. Den er uforanderlig og slettes ikke av modulen.', '140'),
    ('m40_medarbeiderkontraktfelt', 'medarbeiderkontraktfelt',
     'persondata', 'tenant', 'opprettet', NULL, NULL, NULL, NULL,
     'uten_frist_apen',
     'Feltnoeklene sier hvilke felter en kontrakt bar, ikke hva som'
     ' sto i dem. De foelger kontrakten.', '140'),
    ('m40_pulsmaaling', 'pulsmaaling', 'konfigurasjon', 'tenant',
     'apnet', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'Maalingen baerer terskelen svarene ble lest under. Slettes den,'
     ' finnes det ikke lenger et svar paa hvor godt de var vernet.',
     '140'),
    ('m40_pulssvar', 'pulssvar', 'driftsspor', 'tenant', 'avgitt_ts',
     NULL, NULL, NULL, NULL, 'uten_frist_akseptert',
     'Raden baerer en gruppe, et tall mellom 1 og 5 og et tidspunkt.'
     ' Det finnes INGEN taker_id-kolonne aa slette — ikke nullbar,'
     ' ikke pseudonymisert. Derfor er det ingen persondata her, og'
     ' fravaeret av frist er valgt og ikke ubestemt.', '140'),
    ('m40_medarbeiderfunn', 'medarbeiderfunn', 'driftsspor', 'tenant',
     'forst_sett', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'Funnene er modulens egen maaling av seg selv. Reaperen finnes'
     ' ikke i v1.', '140')
ON CONFLICT (lager_id) DO NOTHING;
RESET ROLE;
