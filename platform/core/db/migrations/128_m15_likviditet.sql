-- =====================================================================
-- M-15 LIKVIDITETS- OG KOSTNADSAGENT (v1) — KLYNGE 8s FØRSTE.
-- =====================================================================
--
-- V1-DOMMEN: MODULEN UTFØRER INGENTING. Den sier ingenting opp og
-- betaler ingenting. Et kostnadstiltak er et FORSLAG, og utførelsen
-- går gjennom modulen som eier den — M-41s policykontrollerte vei, av
-- et menneske.
--
-- ---------------------------------------------------------------------
-- KLYNGENS DELTE DOM:
--
--   EN GAL PROGNOSE SER NØYAKTIG UT SOM EN RIKTIG PROGNOSE — HELT TIL
--   HORISONTEN ER PASSERT, OG DA HAR ALLE SLUTTET Å SE.
--
-- Klynge 7s feilform var «en foreldet regel ser ut som en riktig
-- regel». Denne er den samme ett hakk verre: en foreldet regel kan
-- SLÅS OPP, en prognose har ingenting å slå opp mot før tiden har
-- gått — og da er den uinteressant, fordi nå vet vi jo hva som
-- skjedde.
--
-- Derfor er ikke modulens vanskeligste jobb å LAGE prognoser. Det er
-- å sørge for at de blir MÅLT. `prognose_uten_maaling` er funnet
-- ingen kan lukke, og det lukkes av at målingen registreres.
--
-- ---------------------------------------------------------------------
-- ET FUNN FØR FØRSTE LINJE KODE: FUNDAMENTET TOK FEIL OM
-- LØNNSGRUNNLAGET.
--
-- `docs/KLYNGE8-FUNDAMENT.md` skrev at M-15 har inngangsdataene sine,
-- og listet lønnsgrunnlaget (M-39, 113) blant dem. DET STEMMER IKKE.
--
-- M-39 MÅLER TIMER, IKKE KRONER. `arbeidsplan.planlagt_minutter_dag`
-- er minutter; `lonnstaker` har navn og ekstern referanse. Det finnes
-- INGEN sats noe sted i huset — verifisert mot katalogen: ingen
-- kolonne heter `timelonn`, `sats`, `maanedslonn` eller noe i den
-- familien utenfor moms, toll og støtteordninger.
--
-- Dette er den SAMME feilformen som fundamentet selv fanget for M-36
-- («leser en KPI-katalog som ikke finnes»), og det er andre gang i
-- denne klyngen at en antakelse ikke overlevde møtet med skjemaet.
-- Lærdommen står: ET FUNDAMENT KAN TILDELE NUMRE OG ROLLER UTEN Å
-- LESE KODEN. DET KAN IKKE TILDELE DATA.
--
-- KONSEKVENSEN, OG DEN GJØR MODULEN BEDRE: forpliktelser huset ikke
-- kan PRISE, registreres av et menneske i `likviditetspost`. Lønn,
-- husleie, skattetrekk. Da vet vi ALLTID hvem som satte tallet, og
-- prognosen kan aldri hvile på en utledning ingen har sett.
--
-- ---------------------------------------------------------------------
-- HVA PROGNOSEN FAKTISK HVILER PÅ, alt verifisert mot basen:
--
--   * `bankpost` (M-13, 101) — bokførte bevegelser. Saldoen er summen,
--     og historikken er den naive basislinjen.
--   * `fordring` (M-23, 104) — utestående krav med forfallsdato. Det
--     som skal INN.
--   * `likviditetspost` — det et menneske har registrert at skal UT.
--
-- INGEN AV DE TRE HENTES UTENFRA. Modulen har ingen `httpx`, ingen
-- bankintegrasjon, og ingen kolonne som betyr «hentet fra banken».
-- =====================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_likviditet_eier') THEN
        RAISE EXCEPTION 'rollen disponit_likviditet_eier mangler —'
            ' kjør deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_likviditet_eier;
GRANT INSERT ON revisjonslogg TO disponit_likviditet_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_likviditet_eier;
RESET ROLE;

-- TABELLENE EIES AV MIGRATOREN, FUNKSJONENE AV MODULROLLEN (122-127s
-- form). RLS slås på av en `ALTER TABLE`, og bare eieren kan gjøre
-- det: lager modulrollen tabellene, kan den også ta radvakten AV.

-- ---------------------------------------------------------------------
-- TENANTENS EGNE GRENSER.
--
-- HORISONTEN ER TENANTENS BESLUTNING. Katalogen sier «13-ukers
-- prognose», og 13 er standardverdien — men et byggefirma med
-- kvartalsvise innbetalinger og en abonnementsbedrift med månedlig
-- inntekt har ikke samme planleggingshorisont. En horisont vi låste
-- ville vært en fullmakt modulen ga seg selv over kundens økonomi.
-- ---------------------------------------------------------------------
CREATE TABLE likviditetskrav (
    tenant TEXT PRIMARY KEY CHECK (length(btrim(tenant)) > 0),
    horisont_uker INT NOT NULL DEFAULT 13
        CHECK (horisont_uker BETWEEN 1 AND 104),
    -- HVOR GAMMELT GRUNNLAGET FÅR VÆRE. Banksaldoen er fra i går,
    -- prognosen fra i dag — og forskjellen er ikke null. Er den
    -- eldste bevegelsen eldre enn dette, er prognosen regnet på noe
    -- utdatert, og det er et funn.
    grunnlag_maks_alder_dogn INT NOT NULL DEFAULT 7
        CHECK (grunnlag_maks_alder_dogn BETWEEN 1 AND 90),
    -- Hvor lenge etter at en uke er passert vi krever at den er MÅLT.
    -- Nådeperioden finnes fordi banken bruker noen dager på å bokføre;
    -- den er ikke en unnskyldning for å la være.
    maalefrist_dogn INT NOT NULL DEFAULT 14
        CHECK (maalefrist_dogn BETWEEN 1 AND 180),
    -- Hvor lenge før en modellversjon avvikles vi sier fra.
    modellvarsel_dogn INT NOT NULL DEFAULT 30
        CHECK (modellvarsel_dogn BETWEEN 1 AND 365),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon > 0),
    -- IDEMPOTENSNØKKELEN LEVER PÅ RADEN (M-51s lærdom 119, gjentatt i
    -- 123, 124 og 127).
    siste_nokkel TEXT,
    satt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    satt_av TEXT NOT NULL CHECK (length(btrim(satt_av)) > 0)
);

-- ---------------------------------------------------------------------
-- MODELLEN — MED GYLDIGHET, FORDI DEN BLIR GAMMEL.
--
-- Identiteten er FROSSET etter innsetting; bare `gyldig_til` kan
-- settes senere. Det er 121s dom, gjentatt gjennom hele klynge 7 — og
-- den er SKARPERE her: en regel endres av en myndighet, en modell
-- endres av OSS. Det er lettere å endre en modell enn å innrømme at
-- den forrige tok feil, og et snapshot som kunne redigeres ville gjort
-- hver måling til en påstand om noe som ikke lenger står noe sted.
-- ---------------------------------------------------------------------
CREATE TABLE likviditetsmodell (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    modell_id UUID NOT NULL,
    PRIMARY KEY (tenant, modell_id),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    versjon TEXT NOT NULL CHECK (versjon ~ '[^[:space:]]'),
    -- METODEN, SKREVET UT. Ikke en enum: en prognosemetode er en
    -- beskrivelse noen skal kunne etterprøve, ikke et valg fra en
    -- nedtrekksliste. Ikke-tom med vilje.
    metode TEXT NOT NULL CHECK (length(btrim(metode)) >= 16),
    -- DEN NAIVE BASISLINJEN MODELLEN MÅLES MOT. Katalogens M-33-flyt
    -- sier «backtester mot naive baselines», og klyngen deler dommen:
    -- en modell som ikke slår «samme som forrige uke» bærer autoritet
    -- den ikke har fortjent. Her NAVNGIS basislinjen, slik at
    -- sammenligningen er mulig i det hele tatt.
    baselinje TEXT NOT NULL CHECK (length(btrim(baselinje)) > 0),
    gyldig_fra DATE NOT NULL,
    gyldig_til DATE,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (length(btrim(opprettet_av)) > 0),
    CONSTRAINT likviditetsmodell_gyldighet CHECK (
        gyldig_til IS NULL OR gyldig_til >= gyldig_fra),
    CONSTRAINT likviditetsmodell_versjon_unik UNIQUE (tenant, versjon)
);

CREATE INDEX likviditetsmodell_gjeldende
    ON likviditetsmodell (tenant, gyldig_fra DESC)
    WHERE gyldig_til IS NULL;

-- ---------------------------------------------------------------------
-- FORPLIKTELSEN ET MENNESKE HAR REGISTRERT.
--
-- DETTE ER TABELLEN SOM FINNES FORDI HUSET IKKE KAN PRISE LØNN.
--
-- M-39 har `arbeidsplan.planlagt_minutter_dag` og `lonnstaker`, og
-- ingen sats. En modul som «utledet» lønnskostnaden fra timer uten
-- pris ville regnet på et tall den fant på — og et oppfunnet tall i en
-- likviditetsprognose er verre enn ingen prognose, fordi det ser like
-- presist ut som de riktige.
--
-- `registrert_av` ER DERFOR NOT NULL OG BÆRENDE. Den som leser en
-- prognose skal kunne spørre «hvem sa at husleien er 85 000?» og få et
-- navn. Et beløp uten et menneske bak er en antakelse som har fått
-- status som faktum.
-- ---------------------------------------------------------------------
CREATE TABLE likviditetspost (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    post_id UUID NOT NULL,
    PRIMARY KEY (tenant, post_id),
    -- LUKKET SETT. En «type» utenfor settet er ikke en ny kategori —
    -- det er en feilregistrering, og et lukket sett er den eneste
    -- formen som kan si det (099s begrunnelse, som har holdt gjennom
    -- seks moduler).
    posttype TEXT NOT NULL
        CHECK (posttype IN ('lonn', 'husleie', 'skatt', 'avgift',
                            'abonnement', 'laan', 'annet')),
    beskrivelse TEXT NOT NULL CHECK (beskrivelse ~ '[^[:space:]]'),
    -- ØRE, SOM RESTEN AV HUSET. Negativt er UT, positivt er INN — og
    -- fortegnet er en del av registreringen, ikke noe modulen gjetter
    -- av typen. En «annet»-post kan være begge deler.
    belop_ore BIGINT NOT NULL CHECK (belop_ore <> 0),
    forste_forfall DATE NOT NULL,
    -- GJENTAKELSEN, SOM ET LUKKET SETT OG IKKE EN CRON-STRENG. En
    -- cron-streng ville vært et lite programmeringsspråk i en
    -- økonomitabell, og det er nøyaktig så uleselig som det høres ut.
    gjentakelse TEXT NOT NULL
        CHECK (gjentakelse IN ('engang', 'ukentlig', 'maanedlig',
                               'kvartalsvis', 'aarlig')),
    -- NÅR FORPLIKTELSEN SLUTTER. NULL = løper til noen sier stopp.
    gjelder_til DATE,
    aktiv BOOLEAN NOT NULL DEFAULT true,
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (length(btrim(registrert_av)) > 0),
    CONSTRAINT likviditetspost_periode CHECK (
        gjelder_til IS NULL OR gjelder_til >= forste_forfall)
);

CREATE INDEX likviditetspost_aktive
    ON likviditetspost (tenant, forste_forfall) WHERE aktiv;

-- ---------------------------------------------------------------------
-- PROGNOSEN — MODULENS TYNGSTE TABELL.
--
-- FIRE KOLONNER SOM ER `NOT NULL`, OG HVER AV DEM ER EN DOM:
--
--   `horisont_uker` og `gjelder_til` — en prognose uten et tidspunkt
--   den kan etterprøves mot er ikke en prognose, det er en mening med
--   tall i. Samme form som M-50s `journalperson.slettefrist` (124) og
--   M-53s `hmsavvik.oppbevaring_til` (127): DET FARLIGE GJØRES UMULIG,
--   IKKE OPPDAGET.
--
--   `modell_id` og `modellversjon` — snapshotet, ikke en fremmednøkkel
--   til noe som kan endres.
--
--   `grunnlag_siste_bevegelse` og `grunnlag_antall_poster` — hva
--   prognosen faktisk SÅ da den ble laget. Uten dem kan «var
--   grunnlaget godt nok?» ikke besvares i ettertid, og da er
--   `prognose_mot_utdatert_grunnlag` et funn ingen kan etterprøve.
--
-- APPEND-ONLY. En prognose er en PÅSTAND AVGITT PÅ ET TIDSPUNKT. Kunne
-- den redigeres, ville enhver måling vært en sammenligning mot noe som
-- er endret etterpå — altså ingen måling. M-42s dom (110), og her er
-- den strengere enn noe annet sted: en prognose som kan justeres i
-- etterkant er en prognose som alltid stemmer.
-- ---------------------------------------------------------------------
CREATE TABLE likviditetsprognose (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    prognose_id UUID NOT NULL,
    PRIMARY KEY (tenant, prognose_id),
    laget_dato DATE NOT NULL,
    horisont_uker INT NOT NULL CHECK (horisont_uker BETWEEN 1 AND 104),
    gjelder_til DATE NOT NULL,
    modell_id UUID NOT NULL,
    modellversjon TEXT NOT NULL CHECK (modellversjon ~ '[^[:space:]]'),
    baselinje TEXT NOT NULL CHECK (baselinje ~ '[^[:space:]]'),
    -- UTGANGSPUNKTET: saldoen prognosen starter fra.
    startsaldo_ore BIGINT NOT NULL,
    -- HVA GRUNNLAGET VAR. Se tabellkommentaren.
    --
    -- NAVNET VAR GALT I FØRSTE UTGAVE (CodeRabbit): kolonnen het
    -- `grunnlag_eldste_bevegelse`, men verdien er `max(bokfort)` —
    -- den SISTE bevegelsen. Regnestykket var riktig (alderen er
    -- `laget_dato - siste bevegelse`), og funnets detaljtekst sa
    -- allerede «siste bevegelse». Bare kolonnen løy, og en kolonne
    -- som løy om hva den inneholder er den neste feilen noen gjør.
    grunnlag_siste_bevegelse DATE,
    grunnlag_antall_poster INT NOT NULL
        CHECK (grunnlag_antall_poster >= 0),
    grunnlag_antall_fordringer INT NOT NULL
        CHECK (grunnlag_antall_fordringer >= 0),
    grunnlag_antall_poster_registrert INT NOT NULL
        CHECK (grunnlag_antall_poster_registrert >= 0),
    kravversjon INT NOT NULL CHECK (kravversjon > 0),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (length(btrim(opprettet_av)) > 0),
    CONSTRAINT likviditetsprognose_horisont_er_regnet CHECK (
        gjelder_til = laget_dato + (horisont_uker * 7)),
    -- ET TOMT GRUNNLAG ER IKKE EN PROGNOSE. Har vi verken bankposter,
    -- fordringer eller registrerte forpliktelser, finnes det ingenting
    -- å prognostisere FRA — og en bane tegnet på ingenting ville vært
    -- husets mest selvsikre løgn.
    CONSTRAINT likviditetsprognose_har_grunnlag CHECK (
        grunnlag_antall_poster
        + grunnlag_antall_fordringer
        + grunnlag_antall_poster_registrert > 0)
);

CREATE INDEX likviditetsprognose_nyeste
    ON likviditetsprognose (tenant, laget_dato DESC);

-- ---------------------------------------------------------------------
-- BANEN — ÉN RAD PER UKE.
--
-- INTERVALL, ALDRI BARE PUNKT. `nedre_ore` og `ovre_ore` er NOT NULL
-- ved siden av `punkt_ore`.
--
-- DETTE ER EN KOSTNAD VI TAR MED VILJE. Det er lettere å svare
-- «kontantbeholdningen om 13 uker: 2 340 000». Det ser bedre ut på en
-- skjerm, og det er verdiløst. Intervallet er det eneste som gjør en
-- prognose mulig å ta en beslutning på: en bane som med 80 %
-- sannsynlighet ligger mellom 200 000 og 4 millioner sier at DU VET
-- IKKE — og «du vet ikke» er et brukbart svar.
--
-- CHECKen `nedre <= punkt <= ovre` er ikke pynt: et intervall der
-- punktet ligger utenfor er ikke et intervall, det er tre tall.
-- ---------------------------------------------------------------------
CREATE TABLE prognosebane (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    prognose_id UUID NOT NULL,
    uke_nr INT NOT NULL CHECK (uke_nr BETWEEN 1 AND 104),
    PRIMARY KEY (tenant, prognose_id, uke_nr),
    CONSTRAINT prognosebane_prognose_fk
        FOREIGN KEY (tenant, prognose_id)
        REFERENCES likviditetsprognose (tenant, prognose_id),
    ukeslutt DATE NOT NULL,
    punkt_ore BIGINT NOT NULL,
    nedre_ore BIGINT NOT NULL,
    ovre_ore BIGINT NOT NULL,
    -- HVA SOM DRIVER UKEN. Katalogens flyt sier «forklarer drivere og
    -- usikkerhet», og en bane uten drivere er et tall ingen kan
    -- handle på: man ser at det går nedover, ikke hvorfor.
    inn_ore BIGINT NOT NULL DEFAULT 0,
    ut_ore BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT prognosebane_intervall CHECK (
        nedre_ore <= punkt_ore AND punkt_ore <= ovre_ore),
    CONSTRAINT prognosebane_retning CHECK (inn_ore >= 0 AND ut_ore <= 0)
);

-- ---------------------------------------------------------------------
-- MÅLINGEN — MODULENS EGENTLIGE PRODUKT.
--
-- En prognosemodul uten målinger er en maskin som produserer
-- selvsikre setninger og aldri får vite at den tar feil. Målingen er
-- det ENESTE som skiller den fra en kvalifisert gjetning.
--
-- `faktisk_ore` ER IKKE NULLBAR, og det er poenget: en måling uten et
-- faktisk tall er ikke en måling. Kan man ikke måle uken ennå, skal
-- raden ikke finnes — og da står `prognose_uten_maaling` åpent, som
-- det skal.
--
-- APPEND-ONLY, av samme grunn som prognosen selv: en måling som kunne
-- justeres er en måling som alltid bekrefter.
-- ---------------------------------------------------------------------
CREATE TABLE prognosemaaling (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    prognose_id UUID NOT NULL,
    uke_nr INT NOT NULL CHECK (uke_nr BETWEEN 1 AND 104),
    PRIMARY KEY (tenant, prognose_id, uke_nr),
    CONSTRAINT prognosemaaling_bane_fk
        FOREIGN KEY (tenant, prognose_id, uke_nr)
        REFERENCES prognosebane (tenant, prognose_id, uke_nr),
    faktisk_ore BIGINT NOT NULL,
    -- AVVIKET REGNES HER, det mottas ikke. Et avvik kalleren kunne
    -- skrive fritt ville gjort «traff vi?» til en mening.
    avvik_ore BIGINT NOT NULL,
    -- TRAFF INTERVALLET? Det er den ene måltallet som betyr noe for en
    -- intervallprognose: et punkt bommer alltid, spørsmålet er om
    -- sannheten lå innenfor båndet vi oppga.
    innenfor_intervall BOOLEAN NOT NULL,
    -- SLO VI BASISLINJEN? Klyngens delte krav. Uten dette er en
    -- måling bare et tall om oss selv.
    baselinje_avvik_ore BIGINT,
    maalt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    maalt_av TEXT NOT NULL CHECK (length(btrim(maalt_av)) > 0)
);

-- ---------------------------------------------------------------------
-- KOSTNADSTILTAKET — ET FORSLAG, OG BARE DET.
--
-- `reversibilitet` ER NOT NULL OG ET LUKKET SETT. Katalogens flyt
-- sier «simulerer tiltak med effekt og reversibilitet», og
-- invarianten `tiltak_uten_reversibilitet` gjør det umulig å la være:
-- et tiltak ingen har vurdert reversibiliteten av er et tiltak ingen
-- kan angre, og det er nettopp de tiltakene en optimalisator
-- foreslår først, fordi de ser billigst ut.
--
-- STATUSSETTET HAR INGEN `iverksatt`, OG DET ER V1-DOMMEN.
-- «Foreslått», «vurdert», «avvist» — modulen følger et forslag til
-- noen har SETT på det, og der stopper den. Oppsigelsen av et
-- abonnement går gjennom M-41s policykontrollerte vei, av et
-- menneske, og den veien vet ingenting om denne tabellen.
-- ---------------------------------------------------------------------
CREATE TABLE kostnadstiltak (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    tiltak_id UUID NOT NULL,
    PRIMARY KEY (tenant, tiltak_id),
    beskrivelse TEXT NOT NULL
        CHECK (length(btrim(beskrivelse)) >= 16),
    -- HVA VI TROR DET SPARER. Negativt = koster penger; et tiltak kan
    -- være verdt å gjøre likevel, og et skjema som bare tok imot
    -- besparelser ville skjult det.
    forventet_effekt_ore BIGINT NOT NULL,
    reversibilitet TEXT NOT NULL
        CHECK (reversibilitet IN ('reversibel', 'delvis_reversibel',
                                  'irreversibel')),
    -- HVORFOR VI TROR DET. Ikke-tom: et tiltak uten begrunnelse er et
    -- tall noen fant på.
    grunnlag TEXT NOT NULL CHECK (length(btrim(grunnlag)) >= 16),
    status TEXT NOT NULL DEFAULT 'foreslatt'
        CHECK (status IN ('foreslatt', 'vurdert', 'avvist')),
    vurdert_ts TIMESTAMPTZ,
    vurdert_av TEXT,
    vurderingsnotat TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (length(btrim(opprettet_av)) > 0),
    -- LUKKEREGELEN, TOTAL (099s `personvernsak_apen_er_ubesvart`, og
    -- den formen har holdt gjennom hele klynge 7): et felt som bare
    -- betyr noe i én status skal være NULL i de andre.
    CONSTRAINT kostnadstiltak_vurdert_krever_menneske CHECK (
        status = 'foreslatt'
        OR (vurdert_ts IS NOT NULL AND vurdert_av IS NOT NULL
            AND length(btrim(vurdert_av)) > 0
            AND vurderingsnotat IS NOT NULL
            AND length(btrim(vurderingsnotat)) >= 4)),
    CONSTRAINT kostnadstiltak_foreslatt_er_uvurdert CHECK (
        status <> 'foreslatt'
        OR (vurdert_ts IS NULL AND vurdert_av IS NULL
            AND vurderingsnotat IS NULL))
);

CREATE INDEX kostnadstiltak_apne
    ON kostnadstiltak (tenant, opprettet DESC)
    WHERE status = 'foreslatt';

-- ---------------------------------------------------------------------
-- FUNNENE. LUKKET SETT, ETT ÅPENT PER (nøkkel, funntype).
--
-- Formen er 124/127s, og begrunnelsen med den: en funnliste som
-- vokser med kadensen er en funnliste folk lærer seg å overse.
--
-- TO FUNN INGEN KAN LUKKE:
--
--   `prognose_uten_maaling` — horisonten er passert og ingen har
--   sammenlignet med det som faktisk skjedde. KLYNGENS DELTE FUNN.
--   Det lukkes av at målingen registreres. En knapp som fjernet det
--   ville fjernet det eneste signalet om at modulen har sluttet å
--   lære — og en prognosemodul som ikke måles blir gradvis dårligere
--   uten at noen oppdager det, mens den beholder autoriteten sin.
--
--   `prognose_mot_utdatert_grunnlag` — banken har ikke levert på en
--   uke, og prognosen later som ingenting. Det lukkes av at det
--   kommer ferske bevegelser.
--
-- `bane_under_null` KAN lukkes av et menneske: «jeg vet, kassekreditt
-- er avtalt» er en legitim beslutning om noe som ennå ikke er
-- brutt — og 125/126s vakt sørger for at lukkingen står natten over.
-- ---------------------------------------------------------------------
CREATE TABLE likviditetsfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    funn_id UUID NOT NULL,
    PRIMARY KEY (tenant, funn_id),
    funntype TEXT NOT NULL CHECK (funntype IN (
        'ingen_krav',
        'ingen_gyldig_modell',
        'modell_utloper_snart',
        'prognose_uten_maaling',
        'prognose_mot_utdatert_grunnlag',
        'bane_under_null')),
    -- `tiltak_uvurdert` STO HER OG ER FJERNET (CodeRabbit).
    --
    -- Sveipen produserte den aldri, og et funn ville krevd en frist
    -- ingen har bedt om — altså en terskel jeg fant på for å fylle en
    -- verdi jeg selv hadde skrevet. EN VERDI I ET LUKKET SETT SOM
    -- INGEN KODE KAN PRODUSERE ER ET LØFTE INGENTING HOLDER (127s
    -- egen lærdom, samme feil gjentatt her).
    --
    -- Opplysningen er ikke tapt: `uvurderte_tiltak` står i
    -- `m15_bildet`, og flaten sier «N tiltak venter på en vurdering» i
    -- sammendraget. Det som manglet var en frist å måle mot, ikke et
    -- sted å vise tallet.
    prognose_id UUID,
    modell_id UUID,
    CONSTRAINT likviditetsfunn_nivaa CHECK (
        CASE funntype
          WHEN 'ingen_krav' THEN
            prognose_id IS NOT NULL OR modell_id IS NOT NULL
          WHEN 'ingen_gyldig_modell' THEN prognose_id IS NOT NULL
          WHEN 'modell_utloper_snart' THEN modell_id IS NOT NULL
          ELSE prognose_id IS NOT NULL
        END),
    CONSTRAINT likviditetsfunn_en_noekkel CHECK (
        num_nonnulls(prognose_id, modell_id) = 1),
    -- DØGN ELLER ØRE, MED FORTEGN BÅRET AV FUNNTYPEN. Tallet er
    -- poenget: en bane som såvidt går under null og en som går tolv
    -- millioner under er to ulike brudd (124s formulering, som holder).
    over_grense BIGINT,
    detalj TEXT CHECK (detalj IS NULL OR detalj ~ '[^[:space:]]'),
    kravversjon INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT,
    lukkenotat TEXT,
    CONSTRAINT likviditetsfunn_lukking CHECK (
        (apen AND lukket_ts IS NULL AND lukket_av IS NULL
             AND lukkenotat IS NULL)
        OR (NOT apen AND lukket_ts IS NOT NULL)),
    -- FRA FØDSELEN (125): en lukket rad uten navn gjorde
    -- `apen OR lukket_av = '...'` til NULL og felte hele
    -- sveipetransaksjonen på 124.
    CONSTRAINT likviditetsfunn_lukket_har_navn CHECK (
        apen OR (lukket_av IS NOT NULL
                 AND lukket_av ~ '[^[:space:]]'))
);
CREATE UNIQUE INDEX likviditetsfunn_prognose_unik
    ON likviditetsfunn (tenant, prognose_id, funntype)
    WHERE prognose_id IS NOT NULL;
CREATE UNIQUE INDEX likviditetsfunn_modell_unik
    ON likviditetsfunn (tenant, modell_id, funntype)
    WHERE modell_id IS NOT NULL;
CREATE INDEX likviditetsfunn_apne_idx
    ON likviditetsfunn (tenant, apen, funntype);

-- =====================================================================
-- HERFRA EIES DØRENE AV LIKVIDITETSEIEREN.
-- =====================================================================
SET LOCAL ROLE disponit_likviditet_eier;

-- FUNNENE INGEN KAN LUKKE, SOM EN FUNKSJON OG IKKE EN HUSKEREGEL.
-- Lista står her, én gang, og både lukkedøra og lesedøra leser den.
CREATE FUNCTION m15_funn_er_sveipens(p_funntype TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT p_funntype IN ('prognose_uten_maaling',
                          'prognose_mot_utdatert_grunnlag')
$$;
REVOKE ALL ON FUNCTION m15_funn_er_sveipens(TEXT) FROM PUBLIC;

-- STABLE, IKKE IMMUTABLE (125s lærdom, innebygd fra fødselen).
-- Funksjonen leser `current_date`, og planleggeren har LOV til å folde
-- en IMMUTABLE funksjon til en konstant og gjenbruke den i en bufret
-- plan. Jeg skrev det feil i både 123 og 124.
CREATE FUNCTION m15_modell_gyldig(p_fra DATE, p_til DATE)
RETURNS BOOLEAN LANGUAGE sql STABLE
SET search_path = pg_catalog AS $$
    SELECT p_fra <= current_date
       AND (p_til IS NULL OR p_til >= current_date)
$$;
REVOKE ALL ON FUNCTION m15_modell_gyldig(DATE, DATE) FROM PUBLIC;

-- EVIDENSKJEDEN. Formen er `m53_evidens` sin (127), ordrett.
--
-- `input_hash` er sha256 over den KANONISKE BESKRIVELSEN AV
-- HANDLINGEN, ikke over kundedata: en evidenskjede som arkiverte
-- saldoen ville vært et nytt sted tenantens økonomi lå lagret, og et
-- som er append-only og aldri kan rettes.
CREATE FUNCTION m15_evidens(p_tenant TEXT, p_ref UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm15_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm15_likviditet', 'handling', p_handling,
        'ref', p_ref::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm15_likviditet',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:likviditet', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m15_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- =====================================================================
-- DØRENE.
-- =====================================================================

CREATE FUNCTION m15_sett_krav(p_tenant TEXT, p_horisont INT,
                              p_grunnlagsalder INT, p_maalefrist INT,
                              p_modellvarsel INT, p_aktor TEXT,
                              p_nokkel TEXT)
RETURNS TABLE (horisont_uker INT, grunnlag_maks_alder_dogn INT,
               maalefrist_dogn INT, modellvarsel_dogn INT,
               versjon INT, endret BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_rad public.likviditetskrav%ROWTYPE;
    v_endret BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm15_sett_krav');
    IF p_nokkel IS NULL OR btrim(p_nokkel) = '' THEN
        RAISE EXCEPTION 'm15_sett_krav: idempotensnøkkel mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- LÅS FØRST, LES ETTERPÅ (klynge 6s lærdom, gjentatt i 123, 124
    -- og 127): en lesing før `FOR UPDATE` bruker transaksjonens
    -- snapshot, og to samtidige kall ville begge sett den gamle raden.
    SELECT * INTO v_rad FROM public.likviditetskrav
     WHERE tenant = p_tenant FOR UPDATE;

    IF FOUND AND v_rad.siste_nokkel IS NOT DISTINCT FROM p_nokkel THEN
        RETURN QUERY SELECT v_rad.horisont_uker,
                            v_rad.grunnlag_maks_alder_dogn,
                            v_rad.maalefrist_dogn,
                            v_rad.modellvarsel_dogn,
                            v_rad.versjon, false;
        RETURN;
    END IF;

    v_endret := NOT FOUND
        OR v_rad.horisont_uker IS DISTINCT FROM p_horisont
        OR v_rad.grunnlag_maks_alder_dogn IS DISTINCT FROM
           p_grunnlagsalder
        OR v_rad.maalefrist_dogn IS DISTINCT FROM p_maalefrist
        OR v_rad.modellvarsel_dogn IS DISTINCT FROM p_modellvarsel;

    INSERT INTO public.likviditetskrav
        (tenant, horisont_uker, grunnlag_maks_alder_dogn,
         maalefrist_dogn, modellvarsel_dogn, versjon, siste_nokkel,
         satt_av)
    VALUES (p_tenant, p_horisont, p_grunnlagsalder, p_maalefrist,
            p_modellvarsel, 1, p_nokkel, p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        horisont_uker = EXCLUDED.horisont_uker,
        grunnlag_maks_alder_dogn = EXCLUDED.grunnlag_maks_alder_dogn,
        maalefrist_dogn = EXCLUDED.maalefrist_dogn,
        modellvarsel_dogn = EXCLUDED.modellvarsel_dogn,
        -- VERSJONEN ØKER BARE NÅR EN GRENSE FAKTISK ENDRET SEG. En
        -- versjon som økte for hvert gjenspill ville gjort
        -- funnhistorikken uleselig (M-51s lærdom 119, M-47s 123).
        versjon = public.likviditetskrav.versjon
                  + CASE WHEN v_endret THEN 1 ELSE 0 END,
        siste_nokkel = EXCLUDED.siste_nokkel,
        satt_ts = now(), satt_av = EXCLUDED.satt_av
    RETURNING * INTO v_rad;

    PERFORM public.m15_evidens(p_tenant, NULL, 'sett_krav', p_aktor,
        jsonb_build_object('versjon', v_rad.versjon,
                           'endret', v_endret));
    RETURN QUERY SELECT v_rad.horisont_uker,
                        v_rad.grunnlag_maks_alder_dogn,
                        v_rad.maalefrist_dogn,
                        v_rad.modellvarsel_dogn, v_rad.versjon,
                        v_endret;
END $$;
REVOKE ALL ON FUNCTION m15_sett_krav(TEXT, INT, INT, INT, INT, TEXT,
                                     TEXT) FROM PUBLIC;

-- MODELLDØRA. En avviklet versjon KAN registreres: arkivet skal kunne
-- svare på hvilken modell som gjaldt den gangen. Skillet går ved
-- PROGNOSEN — `m15_lag_prognose` nekter mot en modell som ikke
-- gjelder i dag (124/127s form, og 121s dom).
CREATE FUNCTION m15_registrer_modell(
    p_tenant TEXT, p_modell_id UUID, p_navn TEXT, p_versjon TEXT,
    p_metode TEXT, p_baselinje TEXT, p_gyldig_fra DATE,
    p_gyldig_til DATE, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_gml public.likviditetsmodell%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm15_registrer_modell');
    SELECT * INTO v_gml FROM public.likviditetsmodell
     WHERE tenant = p_tenant AND modell_id = p_modell_id FOR UPDATE;

    -- SP-2-MATERIALITET (m35/096-formen, gjentatt i 121-127).
    IF FOUND THEN
        IF v_gml.navn IS DISTINCT FROM btrim(p_navn)
           OR v_gml.versjon IS DISTINCT FROM btrim(p_versjon)
           OR v_gml.metode IS DISTINCT FROM btrim(p_metode)
           OR v_gml.baselinje IS DISTINCT FROM btrim(p_baselinje)
           OR v_gml.gyldig_fra IS DISTINCT FROM p_gyldig_fra THEN
            RAISE EXCEPTION 'm15_registrer_modell: modell % finnes med'
                ' annet innhold', p_modell_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        IF p_gyldig_til IS NOT NULL
           AND v_gml.gyldig_til IS DISTINCT FROM p_gyldig_til THEN
            UPDATE public.likviditetsmodell
               SET gyldig_til = p_gyldig_til
             WHERE tenant = p_tenant AND modell_id = p_modell_id;
            PERFORM public.m15_evidens(p_tenant, p_modell_id,
                'modell_avviklet', p_aktor,
                jsonb_build_object('gyldig_til', p_gyldig_til));
        END IF;
        RETURN false;
    END IF;

    INSERT INTO public.likviditetsmodell
        (tenant, modell_id, navn, versjon, metode, baselinje,
         gyldig_fra, gyldig_til, opprettet_av)
    VALUES (p_tenant, p_modell_id, btrim(p_navn), btrim(p_versjon),
            btrim(p_metode), btrim(p_baselinje), p_gyldig_fra,
            p_gyldig_til, p_aktor);
    PERFORM public.m15_evidens(p_tenant, p_modell_id,
        'modell_registrert', p_aktor,
        jsonb_build_object('versjon', btrim(p_versjon),
                           'baselinje', btrim(p_baselinje)));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m15_registrer_modell(TEXT, UUID, TEXT, TEXT,
    TEXT, TEXT, DATE, DATE, TEXT) FROM PUBLIC;

-- FORPLIKTELSESDØRA — den som finnes fordi huset ikke kan prise lønn.
CREATE FUNCTION m15_registrer_post(
    p_tenant TEXT, p_post_id UUID, p_posttype TEXT,
    p_beskrivelse TEXT, p_belop_ore BIGINT, p_forste_forfall DATE,
    p_gjentakelse TEXT, p_gjelder_til DATE, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_gml public.likviditetspost%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm15_registrer_post');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm15_registrer_post: en registrert forpliktelse'
            ' bærer navnet til den som satte tallet'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT * INTO v_gml FROM public.likviditetspost
     WHERE tenant = p_tenant AND post_id = p_post_id FOR UPDATE;
    IF FOUND THEN
        IF v_gml.posttype IS DISTINCT FROM p_posttype
           OR v_gml.beskrivelse IS DISTINCT FROM btrim(p_beskrivelse)
           OR v_gml.belop_ore IS DISTINCT FROM p_belop_ore
           OR v_gml.forste_forfall IS DISTINCT FROM p_forste_forfall
           OR v_gml.gjentakelse IS DISTINCT FROM p_gjentakelse THEN
            RAISE EXCEPTION 'm15_registrer_post: post % finnes med'
                ' annet innhold', p_post_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN false;
    END IF;

    INSERT INTO public.likviditetspost
        (tenant, post_id, posttype, beskrivelse, belop_ore,
         forste_forfall, gjentakelse, gjelder_til, registrert_av)
    VALUES (p_tenant, p_post_id, p_posttype, btrim(p_beskrivelse),
            p_belop_ore, p_forste_forfall, p_gjentakelse,
            p_gjelder_til, btrim(p_aktor));
    PERFORM public.m15_evidens(p_tenant, p_post_id,
        'post_registrert', btrim(p_aktor),
        jsonb_build_object('posttype', p_posttype,
                           'gjentakelse', p_gjentakelse));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m15_registrer_post(TEXT, UUID, TEXT, TEXT,
    BIGINT, DATE, TEXT, DATE, TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- PROGNOSEDØRA — MODULENS TYNGSTE, OG DEN ENESTE SOM REGNER.
--
-- PROGNOSEN OG BANEN SKRIVES I SAMME SETNING, av M-50s grunn (124) og
-- M-53s (127): var banen et eget kall etterpå, ville en prognose uten
-- bane eksistert i vinduet mellom de to — og en prognose uten bane er
-- en påstand uten innhold, som likevel teller som «målt» i
-- funnlogikken.
--
-- FIRE NEKT, hvert fordi det motsatte ville sett riktig ut:
--
--   1. INGEN GRENSER → ingen prognose. Uten horisonten finnes det
--      ingen `gjelder_til` å måle mot.
--   2. INGEN GYLDIG MODELL → ingen prognose. Arkivet tar imot en
--      avviklet modellversjon; det er BRUKEN som er stengt.
--   3. TOMT GRUNNLAG → ingen prognose. En bane tegnet på ingenting
--      ville vært husets mest selvsikre løgn. CHECKen i §1 stenger
--      for det uansett, men døra skal si det med ord.
--   4. INTERVALLET MÅ VÆRE ET INTERVALL. Kalleren oppgir usikkerheten
--      i prosent, og døra regner båndet — et bånd kalleren kunne
--      skrive fritt ville latt noen levere `nedre = ovre = punkt` og
--      kalle det en prognose med usikkerhet.
--
-- BEREGNINGEN ER MED VILJE ENKEL OG SKREVET UT: startsaldo fra
-- bankpostene, fordringer inn på forfallsuken, registrerte
-- forpliktelser ut på sin. DEN ER IKKE SMART, OG DET ER POENGET I v1 —
-- en modell ingen kan lese er en modell ingen kan si er feil.
-- ---------------------------------------------------------------------
CREATE FUNCTION m15_lag_prognose(
    p_tenant TEXT, p_prognose_id UUID, p_modell_id UUID,
    p_usikkerhet_bp INT, p_aktor TEXT)
RETURNS TABLE (horisont_uker INT, gjelder_til DATE,
               startsaldo_ore BIGINT, uker INT, laveste_ore BIGINT,
               modellversjon TEXT, kravversjon INT, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_krav public.likviditetskrav%ROWTYPE;
    v_modell public.likviditetsmodell%ROWTYPE;
    v_gml public.likviditetsprognose%ROWTYPE;
    v_dato DATE := current_date;
    v_til DATE;
    v_saldo BIGINT;
    v_eldste DATE;
    v_n_poster INT;
    v_n_fordringer INT;
    v_n_registrert INT;
    v_lavest BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm15_lag_prognose');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm15_lag_prognose: en prognose bærer navnet'
            ' til den som ba om den'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_usikkerhet_bp IS NULL
       OR p_usikkerhet_bp NOT BETWEEN 1 AND 10000 THEN
        RAISE EXCEPTION 'm15_lag_prognose: usikkerheten oppgis i'
            ' basispunkter, 1-10000 (%)', p_usikkerhet_bp
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- GJENSPILL FØRST (SP-2, 127s form). Prognosen er append-only, så
    -- et gjenspill kan ikke skrive på nytt — det må svare med raden.
    --
    -- INGEN `FOR UPDATE` HER, OG DET ER IKKE EN FORGLEMMELSE:
    -- `FOR UPDATE` KREVER UPDATE-RETT, og §RETTIGHETER har REVOKEd
    -- den fra modulrollen nettopp fordi tabellen er append-only. En
    -- lås ville feilet med «permission denied» på en dør som gjør alt
    -- riktig — og det er en lærdom huset har betalt for før.
    --
    -- Låsen trengs heller ikke: raden kan ALDRI endres, så det finnes
    -- ingenting å beskytte mot. Kappløpet mellom to samtidige kall
    -- med samme id fanges av primærnøkkelen og
    -- `unique_violation`-grenen under (127s form).
    SELECT * INTO v_gml FROM public.likviditetsprognose
     WHERE tenant = p_tenant AND prognose_id = p_prognose_id;
    IF FOUND THEN
        IF v_gml.modell_id IS DISTINCT FROM p_modell_id THEN
            RAISE EXCEPTION 'm15_lag_prognose: prognose % finnes mot'
                ' en annen modell', p_prognose_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN QUERY SELECT
            v_gml.horisont_uker, v_gml.gjelder_til,
            v_gml.startsaldo_ore,
            (SELECT count(*)::int FROM public.prognosebane b
              WHERE b.tenant = p_tenant
                AND b.prognose_id = p_prognose_id),
            (SELECT min(b.punkt_ore) FROM public.prognosebane b
              WHERE b.tenant = p_tenant
                AND b.prognose_id = p_prognose_id),
            v_gml.modellversjon, v_gml.kravversjon, false;
        RETURN;
    END IF;

    SELECT * INTO v_krav FROM public.likviditetskrav
     WHERE tenant = p_tenant;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm15_lag_prognose: tenantens grenser er ikke'
            ' satt — uten horisonten finnes ingen dato å måle mot'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT * INTO v_modell FROM public.likviditetsmodell m
     WHERE m.tenant = p_tenant AND m.modell_id = p_modell_id
       AND public.m15_modell_gyldig(m.gyldig_fra, m.gyldig_til);
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm15_lag_prognose: modellen finnes ikke eller'
            ' gjelder ikke i dag. Arkivet tar imot en avviklet'
            ' versjon; det er BRUKEN som er stengt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- GRUNNLAGET, LEST ÉN GANG OG SKREVET NED PÅ RADEN.
    SELECT coalesce(sum(p.belop_ore), 0), max(p.bokfort), count(*)
      INTO v_saldo, v_eldste, v_n_poster
      FROM public.bankpost p
      JOIN public.bankkonto k ON k.tenant = p.tenant
                             AND k.konto_id = p.konto_id
     WHERE p.tenant = p_tenant AND k.aktiv;

    SELECT count(*) INTO v_n_fordringer FROM public.fordring f
     WHERE f.tenant = p_tenant AND f.status = 'apen'
       AND f.belop_ore > f.betalt_ore;

    SELECT count(*) INTO v_n_registrert FROM public.likviditetspost l
     WHERE l.tenant = p_tenant AND l.aktiv;

    IF v_n_poster + v_n_fordringer + v_n_registrert = 0 THEN
        RAISE EXCEPTION 'm15_lag_prognose: intet grunnlag. En bane'
            ' tegnet på ingenting er husets mest selvsikre løgn'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    v_til := v_dato + (v_krav.horisont_uker * 7);

    -- KAPPLØPET SOM ERSTATTER LÅSEN (127s lærdom). To samtidige kall
    -- med samme Idempotency-Key ser begge `NOT FOUND`; én INSERT
    -- vinner primærnøkkelen, og taperen svarer som et gjenspill i
    -- stedet for å gi en klientfeil.
    BEGIN
        INSERT INTO public.likviditetsprognose
            (tenant, prognose_id, laget_dato, horisont_uker,
             gjelder_til, modell_id, modellversjon, baselinje,
             startsaldo_ore, grunnlag_siste_bevegelse,
             grunnlag_antall_poster, grunnlag_antall_fordringer,
             grunnlag_antall_poster_registrert, kravversjon,
             opprettet_av)
        VALUES (p_tenant, p_prognose_id, v_dato, v_krav.horisont_uker,
                v_til, p_modell_id, v_modell.versjon,
                v_modell.baselinje, v_saldo, v_eldste, v_n_poster,
                v_n_fordringer, v_n_registrert, v_krav.versjon,
                btrim(p_aktor));
    EXCEPTION WHEN unique_violation THEN
        SELECT * INTO v_gml FROM public.likviditetsprognose
         WHERE tenant = p_tenant AND prognose_id = p_prognose_id;
        RETURN QUERY SELECT
            v_gml.horisont_uker, v_gml.gjelder_til,
            v_gml.startsaldo_ore,
            (SELECT count(*)::int FROM public.prognosebane b
              WHERE b.tenant = p_tenant
                AND b.prognose_id = p_prognose_id),
            (SELECT min(b.punkt_ore) FROM public.prognosebane b
              WHERE b.tenant = p_tenant
                AND b.prognose_id = p_prognose_id),
            v_gml.modellversjon, v_gml.kravversjon, false;
        RETURN;
    END;

    -- BANEN, I SAMME SETNING SOM PROGNOSEN LEVER I.
    --
    -- KUMULERINGEN ER EN VINDUSFUNKSJON, ikke en nøstet delspørring
    -- per uke. Første utgave regnet summen fram til uke N ved å
    -- gjenta hele ukeberegningen for hver j <= N — riktig svar,
    -- kvadratisk arbeid, og fullstendig uleselig.
    --
    -- Det siste var det verste. `metode` på modellraden lover at
    -- beregningen kan etterprøves, og EN MODELL INGEN KAN LESE ER EN
    -- MODELL INGEN KAN SI ER FEIL. En prognose ingen kan motsi er
    -- ikke en prognose — det er en påstand med desimaler.
    INSERT INTO public.prognosebane
        (tenant, prognose_id, uke_nr, ukeslutt, punkt_ore, nedre_ore,
         ovre_ore, inn_ore, ut_ore)
    WITH uke AS (
        SELECT u.n AS uke_nr,
               v_dato + ((u.n - 1) * 7) AS fra,
               v_dato + (u.n * 7) AS til
          FROM generate_series(1, v_krav.horisont_uker) AS u(n)),
    -- GJENTAKELSEN UTVIDES, DEN LESES IKKE BARE (CodeRabbit).
    --
    -- Første utgave talte hver post ÉN gang — i uken `forste_forfall`
    -- falt. En månedlig husleie på 85 000 dukket da opp én gang på
    -- tretten uker i stedet for tre, og `gjentakelse` var en kolonne
    -- som ble lagret og aldri brukt.
    --
    -- DET ER FEIL I DEN FARLIGE RETNINGEN. Banen underteller det som
    -- skal UT, så kontantbeholdningen ser bedre ut enn den er — og
    -- `bane_under_null`, funnet modulen finnes for, uteblir nettopp
    -- når den trengs.
    --
    -- `engang` får et intervall på tusen år: `generate_series` gir da
    -- nøyaktig én rad (startpunktet). Det er samme kodevei for alle
    -- fem gjentakelsene, og en gren mindre å ta feil i.
    forekomst AS (
        SELECT l.belop_ore, f.dato::date AS dato
          FROM public.likviditetspost l
          CROSS JOIN LATERAL generate_series(
              l.forste_forfall::timestamp,
              least(coalesce(l.gjelder_til,
                             v_dato + (v_krav.horisont_uker * 7)),
                    v_dato + (v_krav.horisont_uker * 7))::timestamp,
              CASE l.gjentakelse
                WHEN 'ukentlig' THEN interval '1 week'
                WHEN 'maanedlig' THEN interval '1 month'
                WHEN 'kvartalsvis' THEN interval '3 months'
                WHEN 'aarlig' THEN interval '1 year'
                ELSE interval '1000 years'
              END) AS f(dato)
         WHERE l.tenant = p_tenant AND l.aktiv),
    bevegelse AS (
        SELECT uke.uke_nr, uke.til,
               -- INN: utestående krav med forfall i uken. `status` og
               -- `belop > betalt` er M-23s egne begreper, ikke våre —
               -- vi tolker ikke fordringene, vi leser dem.
               (SELECT coalesce(sum(f.belop_ore - f.betalt_ore), 0)
                  FROM public.fordring f
                 WHERE f.tenant = p_tenant AND f.status = 'apen'
                   AND f.belop_ore > f.betalt_ore
                   AND f.forfall > uke.fra
                   AND f.forfall <= uke.til) AS inn,
               -- UT: bare de NEGATIVE forekomstene. `least(x,0)` og
               -- ikke `WHERE belop_ore < 0`: en positiv post er en
               -- forventet innbetaling noen har registrert, og den
               -- hører til i `inn`-kolonnen — ikke i en utgiftssum med
               -- feil fortegn.
               (SELECT coalesce(sum(least(fo.belop_ore, 0)), 0)
                  FROM forekomst fo
                 WHERE fo.dato > uke.fra AND fo.dato <= uke.til) AS ut,
               (SELECT coalesce(sum(greatest(fo.belop_ore, 0)), 0)
                  FROM forekomst fo
                 WHERE fo.dato > uke.fra
                   AND fo.dato <= uke.til) AS inn_reg
          FROM uke),
    kumulativ AS (
        SELECT b.uke_nr, b.til, b.inn + b.inn_reg AS inn, b.ut,
               v_saldo + sum(b.inn + b.inn_reg + b.ut)
                   OVER (ORDER BY b.uke_nr
                         ROWS BETWEEN UNBOUNDED PRECEDING
                                  AND CURRENT ROW) AS kum
          FROM bevegelse b)
    SELECT p_tenant, p_prognose_id, k.uke_nr, k.til, k.kum,
           -- BÅNDET REGNES HER, det mottas ikke. Et bånd kalleren
           -- kunne skrive fritt ville latt noen levere
           -- `nedre = ovre = punkt` og kalle det usikkerhet.
           k.kum - abs(k.kum * p_usikkerhet_bp / 10000),
           k.kum + abs(k.kum * p_usikkerhet_bp / 10000),
           k.inn, k.ut
      FROM kumulativ k;

    SELECT min(b.punkt_ore) INTO v_lavest FROM public.prognosebane b
     WHERE b.tenant = p_tenant AND b.prognose_id = p_prognose_id;

    PERFORM public.m15_evidens(p_tenant, p_prognose_id,
        'prognose_laget', btrim(p_aktor),
        jsonb_build_object('horisont_uker', v_krav.horisont_uker,
                           'modellversjon', v_modell.versjon,
                           'usikkerhet_bp', p_usikkerhet_bp));

    RETURN QUERY SELECT v_krav.horisont_uker, v_til, v_saldo,
                        v_krav.horisont_uker, v_lavest,
                        v_modell.versjon, v_krav.versjon, true;
END $$;
REVOKE ALL ON FUNCTION m15_lag_prognose(TEXT, UUID, UUID, INT, TEXT)
    FROM PUBLIC;

-- ---------------------------------------------------------------------
-- MÅLEDØRA — DEN SOM LUKKER FUNNET INGEN ANDRE KAN LUKKE.
--
-- AVVIKET REGNES HER, det mottas ikke. Og `innenfor_intervall` regnes
-- av båndet som faktisk STO PÅ RADEN, ikke av et bånd kalleren oppgir
-- nå: hadde kalleren fått lov å si «ja, dette var innenfor», ville
-- målingen vært en karakter modulen ga seg selv.
--
-- MAN KAN IKKE MÅLE EN UKE SOM IKKE ER OVER. Nekten er ikke pedanteri:
-- en måling av en uke som fortsatt løper er et delvis tall som ser ut
-- som et endelig, og den ville lukket `prognose_uten_maaling` uten at
-- noen faktisk hadde sett hva som skjedde.
-- ---------------------------------------------------------------------
CREATE FUNCTION m15_registrer_maaling(
    p_tenant TEXT, p_prognose_id UUID, p_uke_nr INT,
    p_faktisk_ore BIGINT, p_baselinje_ore BIGINT, p_aktor TEXT)
RETURNS TABLE (avvik_ore BIGINT, innenfor_intervall BOOLEAN,
               baselinje_avvik_ore BIGINT, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_bane public.prognosebane%ROWTYPE;
    v_gml public.prognosemaaling%ROWTYPE;
    v_avvik BIGINT;
    v_innenfor BOOLEAN;
    v_bavvik BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm15_registrer_maaling');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm15_registrer_maaling: en måling bærer navnet'
            ' til den som gjorde den'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- INGEN `FOR UPDATE`: banen er append-only, og låsen ville krevd
    -- en UPDATE-rett modulrollen med vilje ikke har.
    SELECT * INTO v_bane FROM public.prognosebane
     WHERE tenant = p_tenant AND prognose_id = p_prognose_id
       AND uke_nr = p_uke_nr;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm15_registrer_maaling: ukjent uke %/%',
            p_prognose_id, p_uke_nr USING ERRCODE = 'no_data_found';
    END IF;

    IF v_bane.ukeslutt > current_date THEN
        RAISE EXCEPTION 'm15_registrer_maaling: uke % er ikke over'
            ' (slutter %). En måling av en uke som fortsatt løper er'
            ' et delvis tall som ser ut som et endelig',
            p_uke_nr, v_bane.ukeslutt
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    v_avvik := p_faktisk_ore - v_bane.punkt_ore;
    -- BÅNDET FRA RADEN, ikke fra kalleren. Se dørkommentaren.
    v_innenfor := p_faktisk_ore BETWEEN v_bane.nedre_ore
                                    AND v_bane.ovre_ore;
    v_bavvik := CASE WHEN p_baselinje_ore IS NULL THEN NULL
                     ELSE p_faktisk_ore - p_baselinje_ore END;

    SELECT * INTO v_gml FROM public.prognosemaaling
     WHERE tenant = p_tenant AND prognose_id = p_prognose_id
       AND uke_nr = p_uke_nr;
    IF FOUND THEN
        -- MÅLINGEN ER APPEND-ONLY. Et gjenspill med SAMME tall er et
        -- stille ja; et med ANDRE tall er en materiell konflikt, ikke
        -- en korreksjon. En måling som lot seg justere er en måling
        -- som alltid bekrefter.
        IF v_gml.faktisk_ore IS DISTINCT FROM p_faktisk_ore THEN
            RAISE EXCEPTION 'm15_registrer_maaling: uke % er alt målt'
                ' til et annet tall. En måling rettes ikke — den står'
                ' som den ble avgitt', p_uke_nr
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN QUERY SELECT v_gml.avvik_ore, v_gml.innenfor_intervall,
                            v_gml.baselinje_avvik_ore, false;
        RETURN;
    END IF;

    -- KAPPLØPET, SAMME FORM: to samtidige målinger av samme uke.
    -- Taperen leser vinnerens rad og svarer som et gjenspill — men
    -- BARE hvis tallet er det samme. Ulike tall er en materiell
    -- konflikt, og en måling rettes ikke.
    BEGIN
        INSERT INTO public.prognosemaaling
            (tenant, prognose_id, uke_nr, faktisk_ore, avvik_ore,
             innenfor_intervall, baselinje_avvik_ore, maalt_av)
        VALUES (p_tenant, p_prognose_id, p_uke_nr, p_faktisk_ore,
                v_avvik, v_innenfor, v_bavvik, btrim(p_aktor));
    EXCEPTION WHEN unique_violation THEN
        SELECT * INTO v_gml FROM public.prognosemaaling
         WHERE tenant = p_tenant AND prognose_id = p_prognose_id
           AND uke_nr = p_uke_nr;
        IF v_gml.faktisk_ore IS DISTINCT FROM p_faktisk_ore THEN
            RAISE EXCEPTION 'm15_registrer_maaling: uke % er alt målt'
                ' til et annet tall. En måling rettes ikke — den står'
                ' som den ble avgitt', p_uke_nr
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN QUERY SELECT v_gml.avvik_ore, v_gml.innenfor_intervall,
                            v_gml.baselinje_avvik_ore, false;
        RETURN;
    END;

    PERFORM public.m15_evidens(p_tenant, p_prognose_id,
        'maaling_registrert', btrim(p_aktor),
        jsonb_build_object('uke_nr', p_uke_nr,
                           'innenfor_intervall', v_innenfor));
    RETURN QUERY SELECT v_avvik, v_innenfor, v_bavvik, true;
END $$;
REVOKE ALL ON FUNCTION m15_registrer_maaling(TEXT, UUID, INT, BIGINT,
    BIGINT, TEXT) FROM PUBLIC;

-- TILTAKSDØRA. `reversibilitet` er obligatorisk fordi CHECKen krever
-- den — døra sier det med ord slik at kalleren får vite HVA som
-- mangler, ikke navnet på en constraint.
CREATE FUNCTION m15_foresla_tiltak(
    p_tenant TEXT, p_tiltak_id UUID, p_beskrivelse TEXT,
    p_effekt_ore BIGINT, p_reversibilitet TEXT, p_grunnlag TEXT,
    p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_gml public.kostnadstiltak%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm15_foresla_tiltak');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm15_foresla_tiltak: et forslag bærer navnet'
            ' til den som fremmet det'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_reversibilitet IS NULL
       OR p_reversibilitet NOT IN ('reversibel', 'delvis_reversibel',
                                   'irreversibel') THEN
        RAISE EXCEPTION 'm15_foresla_tiltak: reversibiliteten må'
            ' vurderes. Et tiltak ingen har vurdert reversibiliteten'
            ' av er et tiltak ingen kan angre'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT * INTO v_gml FROM public.kostnadstiltak
     WHERE tenant = p_tenant AND tiltak_id = p_tiltak_id FOR UPDATE;
    IF FOUND THEN
        IF v_gml.beskrivelse IS DISTINCT FROM btrim(p_beskrivelse)
           OR v_gml.forventet_effekt_ore IS DISTINCT FROM p_effekt_ore
           OR v_gml.reversibilitet IS DISTINCT FROM p_reversibilitet
           OR v_gml.grunnlag IS DISTINCT FROM btrim(p_grunnlag) THEN
            RAISE EXCEPTION 'm15_foresla_tiltak: tiltak % finnes med'
                ' annet innhold', p_tiltak_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN false;
    END IF;

    INSERT INTO public.kostnadstiltak
        (tenant, tiltak_id, beskrivelse, forventet_effekt_ore,
         reversibilitet, grunnlag, opprettet_av)
    VALUES (p_tenant, p_tiltak_id, btrim(p_beskrivelse), p_effekt_ore,
            p_reversibilitet, btrim(p_grunnlag), btrim(p_aktor));
    PERFORM public.m15_evidens(p_tenant, p_tiltak_id,
        'tiltak_foreslatt', btrim(p_aktor),
        jsonb_build_object('reversibilitet', p_reversibilitet,
                           'effekt_ore', p_effekt_ore));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m15_foresla_tiltak(TEXT, UUID, TEXT, BIGINT,
    TEXT, TEXT, TEXT) FROM PUBLIC;

-- VURDERINGSDØRA — OG DEN ENESTE VEIEN UT AV `foreslatt`.
--
-- DET FINNES INGEN `iverksatt`. Et menneske kan si at det er VURDERT
-- eller AVVIST, og der stopper modulen. Oppsigelsen av abonnementet
-- går gjennom M-41s policykontrollerte vei, og den veien vet
-- ingenting om denne tabellen.
CREATE FUNCTION m15_vurder_tiltak(
    p_tenant TEXT, p_tiltak_id UUID, p_status TEXT, p_notat TEXT,
    p_aktor TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_gml public.kostnadstiltak%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm15_vurder_tiltak');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm15_vurder_tiltak: en vurdering bærer navnet'
            ' til den som gjorde den'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_status NOT IN ('vurdert', 'avvist') THEN
        RAISE EXCEPTION 'm15_vurder_tiltak: status må være vurdert'
            ' eller avvist. Modulen iverksetter ingenting (%)',
            p_status USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF length(btrim(coalesce(p_notat, ''))) < 4 THEN
        RAISE EXCEPTION 'm15_vurder_tiltak: en vurdering har en'
            ' begrunnelse, ikke bare et klikk'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT * INTO v_gml FROM public.kostnadstiltak
     WHERE tenant = p_tenant AND tiltak_id = p_tiltak_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm15_vurder_tiltak: ukjent tiltak %',
            p_tiltak_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_gml.status <> 'foreslatt' THEN
        RETURN v_gml.status;  -- idempotent
    END IF;

    UPDATE public.kostnadstiltak
       SET status = p_status, vurdert_ts = now(),
           vurdert_av = btrim(p_aktor),
           vurderingsnotat = btrim(p_notat)
     WHERE tenant = p_tenant AND tiltak_id = p_tiltak_id;

    PERFORM public.m15_evidens(p_tenant, p_tiltak_id,
        'tiltak_vurdert', btrim(p_aktor),
        jsonb_build_object('status', p_status));
    RETURN p_status;
END $$;
REVOKE ALL ON FUNCTION m15_vurder_tiltak(TEXT, UUID, TEXT, TEXT, TEXT)
    FROM PUBLIC;

CREATE FUNCTION m15_lukk_funn(p_tenant TEXT, p_funn_id UUID,
                              p_notat TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_type TEXT;
    v_apen BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm15_lukk_funn');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm15_lukk_funn: en lukking bærer navnet til'
            ' den som lukket'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF length(btrim(coalesce(p_notat, ''))) < 4 THEN
        RAISE EXCEPTION 'm15_lukk_funn: et funn lukkes med en'
            ' begrunnelse, ikke med et klikk'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT funntype, apen INTO v_type, v_apen
      FROM public.likviditetsfunn
     WHERE tenant = p_tenant AND funn_id = p_funn_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm15_lukk_funn: ukjent funn %', p_funn_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_apen THEN
        RETURN;  -- idempotent
    END IF;

    IF public.m15_funn_er_sveipens(v_type) THEN
        RAISE EXCEPTION 'm15_lukk_funn: % lukkes ikke av et menneske.'
            ' Det lukkes av at tilstanden er borte — for'
            ' prognose_uten_maaling betyr det at målingen registreres',
            v_type USING ERRCODE = 'insufficient_privilege';
    END IF;

    UPDATE public.likviditetsfunn
       SET apen = false, lukket_ts = now(), lukket_av = btrim(p_aktor),
           lukkenotat = btrim(p_notat)
     WHERE tenant = p_tenant AND funn_id = p_funn_id;

    PERFORM public.m15_evidens(p_tenant, NULL, 'funn_lukket',
        btrim(p_aktor), jsonb_build_object('funn_id', p_funn_id::text,
                                           'funntype', v_type));
END $$;
REVOKE ALL ON FUNCTION m15_lukk_funn(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

-- =====================================================================
-- SVEIPEN.
--
-- TENANTLISTA ER BEGGE REGISTRENE (122s CodeRabbit-funn, gjentatt i
-- 123, 124 og 127): en tenant som har prognoser men ingen modeller —
-- eller omvendt — skal ikke hoppes over. Han er nettopp den som har
-- konfigurert halvveis.
--
-- MATERIALISERT FØR LØKKA (klynge 6s lærdom om den late markøren).
--
-- LUKKINGEN LESER SAMME `kand` SOM SKRIVINGEN (127s lærdom, som kom
-- av at et eget `gjeldende`-CTE gjentok predikatene og lukket to av
-- fem funntyper). Det som ikke lenger er en kandidat, ER lukket.
-- =====================================================================
CREATE FUNCTION m15_sveip_likviditet(p_maks_tenanter INT)
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
        RAISE EXCEPTION 'm15_sveip_likviditet: maks_tenanter må være'
            ' minst 1 (%)', p_maks_tenanter
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.tenant', '', true);

    SELECT array_agg(DISTINCT t ORDER BY t) INTO v_tenanter
      FROM (SELECT m.tenant AS t FROM public.likviditetsmodell m
            UNION
            SELECT p.tenant FROM public.likviditetsprognose p) s;
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

        -- MODELLNIVÅET.
        WITH krav AS (
            SELECT k.modellvarsel_dogn, k.versjon
              FROM public.likviditetskrav k WHERE k.tenant = v_t),
        kand AS (
            -- INGEN CROSS JOIN krav (121s funn): funnet handler om at
            -- kravet MANGLER, og et CROSS JOIN mot en tom rad ville
            -- gitt null rader — altså ingen melding om at det mangler.
            SELECT m.modell_id, 'ingen_krav'::text AS funntype,
                   NULL::bigint AS over_grense,
                   'tenantens grenser er ikke satt'::text AS detalj,
                   NULL::int AS kravversjon
              FROM public.likviditetsmodell m
             WHERE m.tenant = v_t AND NOT EXISTS (SELECT 1 FROM krav)
            UNION ALL
            SELECT m.modell_id, 'modell_utloper_snart',
                   (m.gyldig_til - current_date)::bigint,
                   m.navn || ' ' || m.versjon, kr.versjon
              FROM public.likviditetsmodell m CROSS JOIN krav kr
             WHERE m.tenant = v_t AND m.gyldig_til IS NOT NULL
               AND m.gyldig_til >= current_date
               AND m.gyldig_til <= current_date
                   + make_interval(days => kr.modellvarsel_dogn)
        ),
        skrevet AS (
            INSERT INTO public.likviditetsfunn
                (tenant, funn_id, modell_id, funntype, over_grense,
                 detalj, kravversjon)
            SELECT v_t, gen_random_uuid(), k.modell_id, k.funntype,
                   k.over_grense, k.detalj, k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, modell_id, funntype)
                WHERE modell_id IS NOT NULL
            -- ET MENNESKES LUKKING SKAL STÅ. 125/126s vakt gjør den
            -- sann uansett hva som står her, men den står her likevel:
            -- en leser skal se regelen der handlingen er.
            DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                detalj = EXCLUDED.detalj,
                kravversjon = EXCLUDED.kravversjon,
                sist_sett_sveip = now(),
                apen = (public.likviditetsfunn.apen
                        OR public.likviditetsfunn.lukket_av
                           = 'm15_sveip'),
                lukket_ts = CASE
                    WHEN public.likviditetsfunn.apen
                      OR public.likviditetsfunn.lukket_av = 'm15_sveip'
                    THEN NULL ELSE public.likviditetsfunn.lukket_ts END,
                lukket_av = CASE
                    WHEN public.likviditetsfunn.apen
                      OR public.likviditetsfunn.lukket_av = 'm15_sveip'
                    THEN NULL ELSE public.likviditetsfunn.lukket_av END,
                lukkenotat = CASE
                    WHEN public.likviditetsfunn.apen
                      OR public.likviditetsfunn.lukket_av = 'm15_sveip'
                    THEN NULL
                    ELSE public.likviditetsfunn.lukkenotat END
            RETURNING (xmax = 0) AS var_ny),
        lukket AS (
            -- RADENE ER DISJUNKTE fra `skrevet`s: den rører dem som
            -- ER i `kand`, denne dem som IKKE er det.
            UPDATE public.likviditetsfunn f
               SET apen = false, lukket_ts = now(),
                   lukket_av = 'm15_sveip',
                   lukkenotat = 'tilstanden er ikke lenger til stede'
             WHERE f.tenant = v_t AND f.apen
               AND f.modell_id IS NOT NULL
               AND f.funntype IN ('ingen_krav', 'modell_utloper_snart')
               AND NOT EXISTS (
                   SELECT 1 FROM kand k
                    WHERE k.modell_id = f.modell_id
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

        -- PROGNOSENIVÅET. Fire funntyper, og to av dem kan ingen lukke.
        WITH krav AS (
            SELECT k.grunnlag_maks_alder_dogn, k.maalefrist_dogn,
                   k.versjon
              FROM public.likviditetskrav k WHERE k.tenant = v_t),
        kand AS (
            SELECT p.prognose_id, 'ingen_krav'::text AS funntype,
                   NULL::bigint AS over_grense,
                   'tenantens grenser er ikke satt'::text AS detalj,
                   NULL::int AS kravversjon
              FROM public.likviditetsprognose p
             WHERE p.tenant = v_t AND NOT EXISTS (SELECT 1 FROM krav)
            UNION ALL
            -- MODELLEN BAK PROGNOSEN ER AVVIKLET. INGEN
            -- ETTERFØLGER-UNNTAK (123s lærdom, funnet av min egen port
            -- der): en prognose regnet under modell 1 er fortsatt
            -- regnet under modell 1 etter at modell 2 er registrert.
            SELECT p.prognose_id, 'ingen_gyldig_modell',
                   (current_date - m.gyldig_til)::bigint,
                   p.modellversjon, NULL
              FROM public.likviditetsprognose p
              JOIN public.likviditetsmodell m
                ON m.tenant = p.tenant AND m.modell_id = p.modell_id
             WHERE p.tenant = v_t AND m.gyldig_til IS NOT NULL
               AND m.gyldig_til < current_date
               AND p.gjelder_til >= current_date
            UNION ALL
            -- FUNNET INGEN KAN LUKKE, ÉN: grunnlaget var gammelt da
            -- prognosen ble laget. Banken har ikke levert, og
            -- prognosen later som ingenting.
            SELECT p.prognose_id, 'prognose_mot_utdatert_grunnlag',
                   (p.laget_dato - p.grunnlag_siste_bevegelse)::bigint,
                   'siste bevegelse '
                   || coalesce(p.grunnlag_siste_bevegelse::text,
                               'ingen'),
                   kr.versjon
              FROM public.likviditetsprognose p CROSS JOIN krav kr
             WHERE p.tenant = v_t
               AND (p.grunnlag_siste_bevegelse IS NULL
                    OR p.laget_dato - p.grunnlag_siste_bevegelse
                       > kr.grunnlag_maks_alder_dogn)
            UNION ALL
            -- FUNNET INGEN KAN LUKKE, TO — OG KLYNGENS DELTE:
            -- horisonten er passert, nådefristen med, og ingen har
            -- sammenlignet med det som faktisk skjedde.
            SELECT p.prognose_id, 'prognose_uten_maaling',
                   (current_date - p.gjelder_til)::bigint,
                   p.horisont_uker::text || ' uker, ingen måling',
                   kr.versjon
              FROM public.likviditetsprognose p CROSS JOIN krav kr
             WHERE p.tenant = v_t
               AND p.gjelder_til
                   + make_interval(days => kr.maalefrist_dogn)
                   < current_date
               AND NOT EXISTS (
                   SELECT 1 FROM public.prognosemaaling ma
                    WHERE ma.tenant = v_t
                      AND ma.prognose_id = p.prognose_id)
            UNION ALL
            -- MODULENS EGEN GRUNN TIL Å FINNES: banen går under null
            -- innenfor horisonten. Dette KAN et menneske lukke —
            -- «kassekreditt er avtalt» er en legitim beslutning om
            -- noe som ennå ikke er brutt.
            -- TO SANNE TALL, HVERT MED SITT NAVN. `over_grense` er
            -- DYBDEN (hvor langt under null det laveste punktet går),
            -- og `detalj` er NÅR det begynner. Første utgave kalte
            -- `min(uke_nr)` for «laveste punkt», og det er en annen
            -- uke: den første under null er sjelden den dypeste. Et
            -- funn som setter feil navn på et riktig tall sender
            -- leseren til feil uke.
            SELECT p.prognose_id, 'bane_under_null',
                   -min(b.punkt_ore),
                   'under null fra uke ' || min(b.uke_nr)::text,
                   max(kr.versjon)
              FROM public.likviditetsprognose p
              JOIN public.prognosebane b
                ON b.tenant = p.tenant AND b.prognose_id = p.prognose_id
              CROSS JOIN krav kr
             WHERE p.tenant = v_t AND b.punkt_ore < 0
               AND p.gjelder_til >= current_date
             GROUP BY p.prognose_id
        ),
        skrevet AS (
            INSERT INTO public.likviditetsfunn
                (tenant, funn_id, prognose_id, funntype, over_grense,
                 detalj, kravversjon)
            SELECT v_t, gen_random_uuid(), k.prognose_id, k.funntype,
                   k.over_grense, k.detalj, k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, prognose_id, funntype)
                WHERE prognose_id IS NOT NULL
            DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                detalj = EXCLUDED.detalj,
                kravversjon = EXCLUDED.kravversjon,
                sist_sett_sveip = now(),
                apen = (public.likviditetsfunn.apen
                        OR public.likviditetsfunn.lukket_av
                           = 'm15_sveip'),
                lukket_ts = CASE
                    WHEN public.likviditetsfunn.apen
                      OR public.likviditetsfunn.lukket_av = 'm15_sveip'
                    THEN NULL ELSE public.likviditetsfunn.lukket_ts END,
                lukket_av = CASE
                    WHEN public.likviditetsfunn.apen
                      OR public.likviditetsfunn.lukket_av = 'm15_sveip'
                    THEN NULL ELSE public.likviditetsfunn.lukket_av END,
                lukkenotat = CASE
                    WHEN public.likviditetsfunn.apen
                      OR public.likviditetsfunn.lukket_av = 'm15_sveip'
                    THEN NULL
                    ELSE public.likviditetsfunn.lukkenotat END
            RETURNING (xmax = 0) AS var_ny),
        lukket AS (
            UPDATE public.likviditetsfunn f
               SET apen = false, lukket_ts = now(),
                   lukket_av = 'm15_sveip',
                   lukkenotat = 'tilstanden er ikke lenger til stede'
             WHERE f.tenant = v_t AND f.apen
               AND f.prognose_id IS NOT NULL
               AND f.funntype IN ('ingen_krav', 'ingen_gyldig_modell',
                                  'prognose_uten_maaling',
                                  'prognose_mot_utdatert_grunnlag',
                                  'bane_under_null')
               AND NOT EXISTS (
                   SELECT 1 FROM kand k
                    WHERE k.prognose_id = f.prognose_id
                      AND k.funntype = f.funntype)
            RETURNING 1)
        SELECT (SELECT count(*) FILTER (WHERE var_ny) FROM skrevet),
               (SELECT count(*) FILTER (WHERE NOT var_ny) FROM skrevet),
               (SELECT count(*) FROM lukket)
          INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);
    END LOOP;

    PERFORM set_config('disponit.tenant', '', true);
    RETURN QUERY SELECT v_antall, v_nye, v_oppdaterte, v_lukket;
END $$;
REVOKE ALL ON FUNCTION m15_sveip_likviditet(INT) FROM PUBLIC;

-- =====================================================================
-- LESEDØRENE.
-- =====================================================================

CREATE FUNCTION m15_bildet(p_tenant TEXT)
RETURNS TABLE (prognoser BIGINT, aktive BIGINT, maalte BIGINT,
               umaalte BIGINT, treff BIGINT, bom BIGINT,
               modeller BIGINT, gyldige_modeller BIGINT,
               poster BIGINT, tiltak BIGINT, uvurderte_tiltak BIGINT,
               apne_funn BIGINT, laveste_ore BIGINT,
               har_krav BOOLEAN, horisont_uker INT,
               grunnlag_maks_alder_dogn INT, maalefrist_dogn INT,
               modellvarsel_dogn INT, kravversjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm15_bildet');
    RETURN QUERY
    WITH k AS (SELECT * FROM public.likviditetskrav
                WHERE tenant = p_tenant),
    aktiv AS (
        SELECT p.prognose_id FROM public.likviditetsprognose p
         WHERE p.tenant = p_tenant AND p.gjelder_til >= current_date)
    SELECT (SELECT count(*) FROM public.likviditetsprognose p
             WHERE p.tenant = p_tenant),
           (SELECT count(*) FROM aktiv),
           (SELECT count(DISTINCT ma.prognose_id)
              FROM public.prognosemaaling ma
             WHERE ma.tenant = p_tenant),
           (SELECT count(*) FROM public.likviditetsprognose p
             WHERE p.tenant = p_tenant
               AND p.gjelder_til < current_date
               AND NOT EXISTS (SELECT 1 FROM public.prognosemaaling ma
                                WHERE ma.tenant = p_tenant
                                  AND ma.prognose_id = p.prognose_id)),
           -- TREFF OG BOM PÅ INTERVALLET, ikke på punktet. Et punkt
           -- bommer alltid; spørsmålet er om sannheten lå innenfor
           -- båndet vi oppga.
           (SELECT count(*) FROM public.prognosemaaling ma
             WHERE ma.tenant = p_tenant AND ma.innenfor_intervall),
           (SELECT count(*) FROM public.prognosemaaling ma
             WHERE ma.tenant = p_tenant AND NOT ma.innenfor_intervall),
           (SELECT count(*) FROM public.likviditetsmodell m
             WHERE m.tenant = p_tenant),
           (SELECT count(*) FROM public.likviditetsmodell m
             WHERE m.tenant = p_tenant
               AND public.m15_modell_gyldig(m.gyldig_fra,
                                            m.gyldig_til)),
           (SELECT count(*) FROM public.likviditetspost l
             WHERE l.tenant = p_tenant AND l.aktiv),
           (SELECT count(*) FROM public.kostnadstiltak t
             WHERE t.tenant = p_tenant),
           (SELECT count(*) FROM public.kostnadstiltak t
             WHERE t.tenant = p_tenant AND t.status = 'foreslatt'),
           (SELECT count(*) FROM public.likviditetsfunn f
             WHERE f.tenant = p_tenant AND f.apen),
           -- LAVESTE PUNKT I EN AKTIV BANE. Det ene tallet modulen
           -- finnes for.
           (SELECT min(b.punkt_ore) FROM public.prognosebane b
             JOIN aktiv a ON a.prognose_id = b.prognose_id
            WHERE b.tenant = p_tenant),
           (SELECT count(*) > 0 FROM k),
           -- ALLE FIRE GRENSENE (123s lærdom: et skjema som viser
           -- mindre enn det lagrer er en felle — flaten
           -- forhåndsutfyller herfra).
           (SELECT k.horisont_uker FROM k),
           (SELECT k.grunnlag_maks_alder_dogn FROM k),
           (SELECT k.maalefrist_dogn FROM k),
           (SELECT k.modellvarsel_dogn FROM k),
           (SELECT k.versjon FROM k);
END $$;
REVOKE ALL ON FUNCTION m15_bildet(TEXT) FROM PUBLIC;

CREATE FUNCTION m15_prognosene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (prognose_id UUID, laget_dato DATE, horisont_uker INT,
               gjelder_til DATE, modellversjon TEXT, baselinje TEXT,
               startsaldo_ore BIGINT, laveste_ore BIGINT,
               grunnlag_alder_dogn INT, antall_uker BIGINT,
               antall_maalinger BIGINT, treff BIGINT,
               kravversjon INT, opprettet_av TEXT, aktiv BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm15_prognosene');
    RETURN QUERY
    SELECT p.prognose_id, p.laget_dato, p.horisont_uker,
           p.gjelder_til, p.modellversjon, p.baselinje,
           p.startsaldo_ore,
           (SELECT min(b.punkt_ore) FROM public.prognosebane b
             WHERE b.tenant = p_tenant
               AND b.prognose_id = p.prognose_id),
           CASE WHEN p.grunnlag_siste_bevegelse IS NULL THEN NULL
                ELSE (p.laget_dato
                      - p.grunnlag_siste_bevegelse)::int END,
           (SELECT count(*) FROM public.prognosebane b
             WHERE b.tenant = p_tenant
               AND b.prognose_id = p.prognose_id),
           (SELECT count(*) FROM public.prognosemaaling ma
             WHERE ma.tenant = p_tenant
               AND ma.prognose_id = p.prognose_id),
           (SELECT count(*) FROM public.prognosemaaling ma
             WHERE ma.tenant = p_tenant
               AND ma.prognose_id = p.prognose_id
               AND ma.innenfor_intervall),
           p.kravversjon, p.opprettet_av,
           (p.gjelder_til >= current_date)
      FROM public.likviditetsprognose p
     WHERE p.tenant = p_tenant
     ORDER BY p.laget_dato DESC, p.prognose_id
     LIMIT p_maks;
END $$;
REVOKE ALL ON FUNCTION m15_prognosene(TEXT, INT) FROM PUBLIC;

-- BANEN MED MÅLINGEN VED SIDEN AV. De to hører sammen: en bane uten
-- målingen viser hva vi trodde, og det er halve historien.
CREATE FUNCTION m15_banen(p_tenant TEXT, p_prognose_id UUID)
RETURNS TABLE (uke_nr INT, ukeslutt DATE, punkt_ore BIGINT,
               nedre_ore BIGINT, ovre_ore BIGINT, inn_ore BIGINT,
               ut_ore BIGINT, faktisk_ore BIGINT, avvik_ore BIGINT,
               innenfor_intervall BOOLEAN, maalt_av TEXT,
               kan_maales BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm15_banen');
    RETURN QUERY
    SELECT b.uke_nr, b.ukeslutt, b.punkt_ore, b.nedre_ore, b.ovre_ore,
           b.inn_ore, b.ut_ore, ma.faktisk_ore, ma.avvik_ore,
           ma.innenfor_intervall, ma.maalt_av,
           -- FLATEN SKAL IKKE REGNE UT SELV om uken er over. Regelen
           -- bor i basen, og lesedøra bærer den med hver rad (124s
           -- `kan_lukkes`-form).
           (b.ukeslutt <= current_date AND ma.uke_nr IS NULL)
      FROM public.prognosebane b
      LEFT JOIN public.prognosemaaling ma
        ON ma.tenant = b.tenant AND ma.prognose_id = b.prognose_id
       AND ma.uke_nr = b.uke_nr
     WHERE b.tenant = p_tenant AND b.prognose_id = p_prognose_id
     ORDER BY b.uke_nr;
END $$;
REVOKE ALL ON FUNCTION m15_banen(TEXT, UUID) FROM PUBLIC;

CREATE FUNCTION m15_postene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (post_id UUID, posttype TEXT, beskrivelse TEXT,
               belop_ore BIGINT, forste_forfall DATE,
               gjentakelse TEXT, gjelder_til DATE, aktiv BOOLEAN,
               registrert TIMESTAMPTZ, registrert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm15_postene');
    RETURN QUERY
    SELECT l.post_id, l.posttype, l.beskrivelse, l.belop_ore,
           l.forste_forfall, l.gjentakelse, l.gjelder_til, l.aktiv,
           l.registrert, l.registrert_av
      FROM public.likviditetspost l
     WHERE l.tenant = p_tenant
     ORDER BY l.forste_forfall, l.post_id
     LIMIT p_maks;
END $$;
REVOKE ALL ON FUNCTION m15_postene(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m15_modellene(p_tenant TEXT)
RETURNS TABLE (modell_id UUID, navn TEXT, versjon TEXT, metode TEXT,
               baselinje TEXT, gyldig_fra DATE, gyldig_til DATE,
               gyldig_naa BOOLEAN, dogn_til_utlop INT,
               antall_prognoser BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm15_modellene');
    RETURN QUERY
    SELECT m.modell_id, m.navn, m.versjon, m.metode, m.baselinje,
           m.gyldig_fra, m.gyldig_til,
           public.m15_modell_gyldig(m.gyldig_fra, m.gyldig_til),
           CASE WHEN m.gyldig_til IS NULL THEN NULL
                ELSE (m.gyldig_til - current_date)::int END,
           (SELECT count(*) FROM public.likviditetsprognose p
             WHERE p.tenant = p_tenant AND p.modell_id = m.modell_id)
      FROM public.likviditetsmodell m
     WHERE m.tenant = p_tenant
     ORDER BY m.gyldig_fra DESC, m.versjon;
END $$;
REVOKE ALL ON FUNCTION m15_modellene(TEXT) FROM PUBLIC;

CREATE FUNCTION m15_tiltakene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (tiltak_id UUID, beskrivelse TEXT,
               forventet_effekt_ore BIGINT, reversibilitet TEXT,
               grunnlag TEXT, status TEXT, vurdert_av TEXT,
               vurderingsnotat TEXT, opprettet TIMESTAMPTZ,
               opprettet_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm15_tiltakene');
    RETURN QUERY
    SELECT t.tiltak_id, t.beskrivelse, t.forventet_effekt_ore,
           t.reversibilitet, t.grunnlag, t.status, t.vurdert_av,
           t.vurderingsnotat, t.opprettet, t.opprettet_av
      FROM public.kostnadstiltak t
     WHERE t.tenant = p_tenant
     ORDER BY (t.status = 'foreslatt') DESC, t.opprettet DESC
     LIMIT p_maks;
END $$;
REVOKE ALL ON FUNCTION m15_tiltakene(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m15_funnene(p_tenant TEXT, p_apne BOOLEAN)
RETURNS TABLE (funn_id UUID, funntype TEXT, prognose_id UUID,
               modell_id UUID, over_grense BIGINT,
               detalj TEXT, kravversjon INT, forst_sett TIMESTAMPTZ,
               sist_sett_sveip TIMESTAMPTZ, apen BOOLEAN,
               lukket_ts TIMESTAMPTZ, lukket_av TEXT,
               lukkenotat TEXT, kan_lukkes BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm15_funnene');
    RETURN QUERY
    SELECT f.funn_id, f.funntype, f.prognose_id, f.modell_id,
           f.over_grense, f.detalj, f.kravversjon,
           f.forst_sett, f.sist_sett_sveip, f.apen, f.lukket_ts,
           f.lukket_av, f.lukkenotat,
           NOT public.m15_funn_er_sveipens(f.funntype)
      FROM public.likviditetsfunn f
     WHERE f.tenant = p_tenant
       AND (p_apne IS NULL OR f.apen = p_apne)
     ORDER BY f.apen DESC, f.forst_sett DESC;
END $$;
REVOKE ALL ON FUNCTION m15_funnene(TEXT, BOOLEAN) FROM PUBLIC;

-- =====================================================================
-- RETTIGHETENE, RADVAKTENE OG FRYSINGEN.
-- =====================================================================

RESET ROLE;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['likviditetskrav', 'likviditetsmodell',
                             'likviditetspost',
                             'likviditetsprognose', 'prognosebane',
                             'prognosemaaling', 'kostnadstiltak',
                             'likviditetsfunn']
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
                       ' disponit_likviditet_eier', t);
    END LOOP;
END $$;

-- MODULEN LESER TRE ANDRE MODULERS REGISTRE, OG DET ER EN DOM.
--
-- `bankpost`, `bankkonto` og `fordring` eies av M-13 og M-23.
-- Prognosen trenger dem, og den får NØYAKTIG SELECT — ingen INSERT,
-- ingen UPDATE, ingen DELETE. En likviditetsmodul som kunne skrive i
-- bankregisteret ville kunnet «rette» virkeligheten til å passe
-- prognosen, og det er den ene feilen ingen ville oppdaget.
--
-- RLS-EN PÅ DE TRE GJELDER FORTSATT: lesedørene her er SECURITY
-- DEFINER og løper som `disponit_likviditet_eier`, som ikke er unntatt
-- noen radvakt. Tenantisolasjonen er den samme som for eierne.
GRANT SELECT ON public.bankpost TO disponit_likviditet_eier;
GRANT SELECT ON public.bankkonto TO disponit_likviditet_eier;
GRANT SELECT ON public.fordring TO disponit_likviditet_eier;

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER (111s form, 112-127):
-- bare FOR SELECT, bare til eieren, bare uten tenantkontekst — og på
-- BEGGE registrene sveipens tenantliste leser (122s lærdom).
CREATE POLICY m15_sveip_tenantliste ON likviditetsmodell
    FOR SELECT TO disponit_likviditet_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);
CREATE POLICY m15_sveip_tenantliste_prognose ON likviditetsprognose
    FOR SELECT TO disponit_likviditet_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- HISTORIKKTABELLENE FÅR IKKE UPDATE.
--
-- `likviditetsprognose`, `prognosebane` og `prognosemaaling` er HELT
-- lukket. En prognose er en PÅSTAND AVGITT PÅ ET TIDSPUNKT: kunne den
-- redigeres, ville enhver måling vært en sammenligning mot noe som er
-- endret etterpå — altså ingen måling. OG EN PROGNOSE SOM KAN
-- JUSTERES I ETTERKANT ER EN PROGNOSE SOM ALLTID STEMMER.
--
-- Målingen er lukket av samme grunn, speilvendt: en måling som lot
-- seg justere er en måling som alltid bekrefter.
REVOKE UPDATE ON public.likviditetsprognose
    FROM disponit_likviditet_eier;
REVOKE UPDATE ON public.prognosebane FROM disponit_likviditet_eier;
REVOKE UPDATE ON public.prognosemaaling
    FROM disponit_likviditet_eier;

-- `likviditetsmodell` FÅR BARE ENDRE `gyldig_til` (121s dom).
REVOKE UPDATE ON public.likviditetsmodell
    FROM disponit_likviditet_eier;
GRANT UPDATE (gyldig_til) ON public.likviditetsmodell
    TO disponit_likviditet_eier;

-- `kostnadstiltak` FÅR BARE VURDERES. Beskrivelsen, effekten,
-- reversibiliteten og grunnlaget er frosset: kunne de endres etter at
-- noen hadde sett forslaget, ville vurderingen gjeldt et annet tiltak.
REVOKE UPDATE ON public.kostnadstiltak FROM disponit_likviditet_eier;
GRANT UPDATE (status, vurdert_ts, vurdert_av, vurderingsnotat)
    ON public.kostnadstiltak TO disponit_likviditet_eier;

-- `likviditetspost` FÅR BARE DEAKTIVERES. Beløpet et menneske satte
-- skal ikke kunne endres i stillhet — en ny sum er en ny post, og da
-- står begge i historikken med hvert sitt navn.
REVOKE UPDATE ON public.likviditetspost FROM disponit_likviditet_eier;
GRANT UPDATE (aktiv) ON public.likviditetspost
    TO disponit_likviditet_eier;

-- INGEN AV TABELLENE FÅR SLETTES. `DELETE` står ikke i noen GRANT
-- over — listen er `SELECT, INSERT, UPDATE`. Det står her fordi et
-- fravær er lettere å overse enn en setning, og porten leser begge.

-- RADVAKTENE. Triggerne settes av MIGRATOREN, som eier tabellene:
-- `CREATE TRIGGER` krever eierskap, og en modulrolle som kunne sette
-- dem kunne også ta dem av igjen.
CREATE FUNCTION m15_modell_frosset()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.modell_id IS DISTINCT FROM OLD.modell_id
       OR NEW.navn IS DISTINCT FROM OLD.navn
       OR NEW.versjon IS DISTINCT FROM OLD.versjon
       OR NEW.metode IS DISTINCT FROM OLD.metode
       OR NEW.baselinje IS DISTINCT FROM OLD.baselinje
       OR NEW.gyldig_fra IS DISTINCT FROM OLD.gyldig_fra
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av THEN
        RAISE EXCEPTION 'likviditetsmodell: identiteten er frosset —'
            ' bare gyldig_til kan settes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER likviditetsmodell_frosset
    BEFORE UPDATE ON likviditetsmodell
    FOR EACH ROW EXECUTE FUNCTION m15_modell_frosset();

-- PROGNOSEN, BANEN OG MÅLINGEN ER APPEND-ONLY, HÅNDHEVET.
--
-- Rettighetene over stenger modulrollen ute; denne vakten stenger
-- ALLE — også migrator og en fremtidig migrasjon som «bare skal
-- rette en skrivefeil». M-42s dom (110), og her er den strengest i
-- hele huset: en prognose som kan endres etter at utfallet er kjent,
-- er ikke en prognose.
CREATE FUNCTION m15_prognose_append_only()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION '%: append-only — % er forbudt. En prognose som'
        ' kan endres etter at utfallet er kjent, er ikke en prognose',
        TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TRIGGER likviditetsprognose_append_only
    BEFORE UPDATE OR DELETE ON likviditetsprognose
    FOR EACH ROW EXECUTE FUNCTION m15_prognose_append_only();
CREATE TRIGGER likviditetsprognose_ingen_truncate
    BEFORE TRUNCATE ON likviditetsprognose
    FOR EACH STATEMENT EXECUTE FUNCTION m15_prognose_append_only();
CREATE TRIGGER prognosebane_append_only
    BEFORE UPDATE OR DELETE ON prognosebane
    FOR EACH ROW EXECUTE FUNCTION m15_prognose_append_only();
CREATE TRIGGER prognosebane_ingen_truncate
    BEFORE TRUNCATE ON prognosebane
    FOR EACH STATEMENT EXECUTE FUNCTION m15_prognose_append_only();
CREATE TRIGGER prognosemaaling_append_only
    BEFORE UPDATE OR DELETE ON prognosemaaling
    FOR EACH ROW EXECUTE FUNCTION m15_prognose_append_only();
CREATE TRIGGER prognosemaaling_ingen_truncate
    BEFORE TRUNCATE ON prognosemaaling
    FOR EACH STATEMENT EXECUTE FUNCTION m15_prognose_append_only();

-- TILTAKET: bare veien FRA `foreslatt` er lovlig, og aldri tilbake.
CREATE FUNCTION m15_tiltak_frosset()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.tiltak_id IS DISTINCT FROM OLD.tiltak_id
       OR NEW.beskrivelse IS DISTINCT FROM OLD.beskrivelse
       OR NEW.forventet_effekt_ore IS DISTINCT FROM
          OLD.forventet_effekt_ore
       OR NEW.reversibilitet IS DISTINCT FROM OLD.reversibilitet
       OR NEW.grunnlag IS DISTINCT FROM OLD.grunnlag
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av THEN
        RAISE EXCEPTION 'kostnadstiltak: forslaget er frosset — bare'
            ' vurderingen er en lovlig endring'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status <> 'foreslatt' AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'kostnadstiltak: en vurdering står. Et nytt'
            ' syn er et nytt forslag'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER kostnadstiltak_frosset
    BEFORE UPDATE ON kostnadstiltak
    FOR EACH ROW EXECUTE FUNCTION m15_tiltak_frosset();

-- POSTEN: beløpet et menneske satte er frosset; bare `aktiv` flippes.
CREATE FUNCTION m15_post_frosset()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.post_id IS DISTINCT FROM OLD.post_id
       OR NEW.posttype IS DISTINCT FROM OLD.posttype
       OR NEW.beskrivelse IS DISTINCT FROM OLD.beskrivelse
       OR NEW.belop_ore IS DISTINCT FROM OLD.belop_ore
       OR NEW.forste_forfall IS DISTINCT FROM OLD.forste_forfall
       OR NEW.gjentakelse IS DISTINCT FROM OLD.gjentakelse
       OR NEW.registrert IS DISTINCT FROM OLD.registrert
       OR NEW.registrert_av IS DISTINCT FROM OLD.registrert_av THEN
        RAISE EXCEPTION 'likviditetspost: beløpet er frosset — en ny'
            ' sum er en ny post, så begge står i historikken med'
            ' hvert sitt navn'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER likviditetspost_frosset
    BEFORE UPDATE ON likviditetspost
    FOR EACH ROW EXECUTE FUNCTION m15_post_frosset();

-- 125/126s VAKT GJELDER OGSÅ HER. Nummer ti kopierte sveipen fra
-- nummer ni; det er nøyaktig det vakten ble skrevet for.
CREATE TRIGGER likviditetsfunn_lukkevern BEFORE UPDATE
    ON likviditetsfunn FOR EACH ROW
    EXECUTE FUNCTION sveipefunn_lukkevern('m15_sveip');

-- =====================================================================
-- EXECUTE — HVEM SOM FÅR ÅPNE HVILKEN DØR.
-- =====================================================================
SET LOCAL ROLE disponit_likviditet_eier;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m15_bildet(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m15_prognosene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m15_banen(TEXT, UUID)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m15_postene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m15_modellene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m15_tiltakene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m15_funnene(TEXT, BOOLEAN)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m15_sett_krav(TEXT, INT, INT, INT, INT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m15_registrer_modell(TEXT,'
            ' UUID, TEXT, TEXT, TEXT, TEXT, DATE, DATE, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m15_registrer_post(TEXT,'
            ' UUID, TEXT, TEXT, BIGINT, DATE, TEXT, DATE, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m15_lag_prognose(TEXT,'
            ' UUID, UUID, INT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m15_registrer_maaling('
            'TEXT, UUID, INT, BIGINT, BIGINT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m15_foresla_tiltak(TEXT,'
            ' UUID, TEXT, BIGINT, TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m15_vurder_tiltak(TEXT,'
            ' UUID, TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m15_lukk_funn(TEXT, UUID,'
            ' TEXT, TEXT) TO disponit';
    END IF;
END $$;

-- SVEIPEROLLEN NÅR ÉN FUNKSJON, OG BARE DEN (111s form, 112-127).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_likviditetssveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m15_sveip_likviditet(INT)'
            ' TO disponit_likviditetssveip';
    END IF;
END $$;

RESET ROLE;

COMMENT ON TABLE likviditetsprognose IS
    'M-15 (128). Append-only: en prognose er en PÅSTAND AVGITT PÅ ET '
    'TIDSPUNKT, og en som kan justeres i etterkant er en prognose som '
    'alltid stemmer. horisont_uker, gjelder_til, modellversjon og '
    'grunnlagstellingene er NOT NULL — se docs/KLYNGE8-FUNDAMENT.md.';
COMMENT ON TABLE likviditetspost IS
    'Forpliktelser et MENNESKE har registrert. Finnes fordi huset '
    'ikke kan prise lønn: M-39 måler timer, og ingen sats finnes. '
    'registrert_av er bærende — den som leser en prognose skal kunne '
    'spørre hvem som satte tallet.';
