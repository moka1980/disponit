-- 097: M-12 identitets- og tilgangsagent v1 — TILGANGSREGISTERET. Tre
-- tenant-skopede tabeller, fem dører og ÉN gjennomgangssveip med sin
-- egen timer og sin egen rolle.
--
-- V1-DOMMEN, ORDRETT FRA MANIFESTET: katalogteksten lover JML — joiner,
-- mover, leaver — altså at modulen OPPRETTER, FLYTTER og FJERNER
-- tilganger automatisk. Det er den farligste klassen kode en plattform
-- kan ha: en feil i provisjoneringen gir enten en ansatt tilgang hun
-- ikke skal ha, eller stenger noen ute fra jobben sin midt i
-- arbeidsdagen.
--
-- v1 PROVISJONERER INGENTING. Den REGISTRERER hvem som har hvilken
-- tilgang til hva, med eier og hjemmel, og gjør avvik synlige. Å se
-- sannheten er forutsetningen for å tørre å endre den — og den
-- rekkefølgen er ikke forsiktighet, den er den eneste som gir et
-- revisjonsspor å måle mot når provisjoneringen kommer.
--
-- HELE MIGRASJONEN ER SKREVET SÅ DEN DOMMEN ER MÅLBAR, ikke bare
-- påstått. Det finnes ingen identitetsklient her, ingen utgående kall,
-- og INGEN DML mot en eneste tabell utenfor modulens egne tre lagre.
-- `tilgang_endret_utenfor_registeret` er invariant nummer én i
-- `manifestskjema.M12_INVARIANTER`, og den måles både statisk (ingen
-- fremmed DML i denne filen) og funksjonelt (radantallet utenfor egne
-- lagre er uendret etter en sveip).
--
-- SVEIPEN SKRIVER DERFOR HELLER INGEN EVIDENSRAD. M-21 (096) lar
-- fristsveipen skrive `revisjonslogg`, og det er riktig der: den KØER
-- VARSEL, altså en handling utad. Denne sveipen observerer bare, og en
-- observasjon som skriver i evidenskjeden ville gjort den funksjonelle
-- porten over til noe som måtte unnta sin egen jobb. Dørene — de
-- menneskelige handlingene — skriver evidens; sveipen skriver funn.
--
-- ============================================================
-- TRE DOMMER v1 HVILER PÅ, ALLE HÅNDHEVET I DATAMODELLEN
-- ============================================================
--
--   1. EN TILGANG UTEN EIER ER UREPRESENTERBAR. «Hvem eier denne
--      tilgangen» er hele spørsmålet registeret finnes for, og svaret
--      er en NOT NULL med fremmednøkkel mot `brukeridentitet` — ikke en
--      rapport. En tilgang ingen eier er en tilgang ingen etterprøver.
--
--   2. EN TILGANG UTEN HJEMMEL ER UREPRESENTERBAR. Ikke-tom CHECK, og
--      den er skrevet med en regexklasse og IKKE som
--      `length(btrim(hjemmel)) > 0`. Den siste formen slipper gjennom en
--      ren tabulator — `btrim` trimmer som standard bare mellomrom — og
--      det funnet ble gjort i M-9. En hjemmel som er ett tabulatortegn
--      er en tom hjemmel med en usynlig maske på.
--
--      KLASSEN BÆRER OGSÅ NBSP (U+00A0), og det er et funn fra
--      byggingen av denne migrasjonen: `[[:space:]]` er ASCII-blanktegn
--      i denne basens ctype, så `E'\u00a0'` — det harde mellomrommet
--      man får med på kjøpet ved lim inn fra Word eller en nettside —
--      slapp gjennom den rene klassen. Det er NØYAKTIG samme feil som
--      tabulatoren, med en enda vanligere opprinnelse.
--
--      GRENSEN ER NAVNGITT OG IKKE UTVIDET VIDERE: den nullbreddes
--      familien (U+200B og slektningene) slipper fortsatt gjennom. Å
--      jage hvert usynlige Unicode-tegn i en CHECK er et tapt løp, og
--      en halvferdig liste er verre enn en navngitt grense. Skillet er
--      dette: NBSP kommer av ORDINÆR kopiering og rammer et menneske
--      som trodde det skrev noe; et nullbreddes tegn kommer av
--      handlinger ingen gjør ved et uhell, og «denne teksten SER tom
--      ut» er et datakvalitetsspørsmål (M-3), ikke et spørsmål om hva
--      registeret kan REPRESENTERE.
--
--   3. EN REGISTERRAD ENDRES ALDRI ETTER INNSETTINGEN. Subjektet,
--      nivået, objektet, eieren og hjemmelen er FROSSET av radvakten,
--      for enhver rolle — også tabellens egen eier. En annen hjemmel,
--      et annet nivå eller et annet subjekt er en ANNEN TILGANG, ikke en
--      redigering av denne. Det ene feltet som kan flyttes er
--      gjennomgangsmerket, og det bare FRAMOVER og bare med en navngitt
--      aktør i sesjonen. En gjennomgang er FORFATTET, aldri avledet: det
--      finnes ingen jobb her som setter `sist_gjennomgatt`, og en jobb
--      som skulle gjort det ville ikke hatt noe navn å skrive.
--
-- ============================================================
-- HVA JEG ENDRET FRA DEN FORESLÅTTE FORMEN, OG HVORFOR
-- ============================================================
--
--   * `sist_gjennomgatt` fikk en FØLGESVENN, `sist_gjennomgatt_av`, og
--     en CHECK som gjør den halve gjennomgangen urepresenterbar. En dato
--     alene sier at NOEN så på tilgangen; den sier ikke hvem, og en
--     gjennomgang uten et navn er nøyaktig like lite etterprøvbar som
--     ingen gjennomgang. Døren heter `m12_registrer_gjennomgang(...
--     p_gjennomgatt_av ...)` fordi navnet er halve handlingen.
--
--   * `gjennomgang_frist` er en GENERERT kolonne, ikke et uttrykk i
--     sveipens WHERE. «Når skal denne tilgangen etterprøves neste gang»
--     er et spørsmål registeret skal kunne svare på uten at spørreren
--     regner det ut selv — samme begrunnelse som M-21s `dogn_til_frist`
--     regnes i basen. Den gjør i tillegg fristindeksen mulig: et uttrykk
--     over to kolonner er ikke et indeksbart predikat, og en
--     gjennomgangssveip som må sekvensskanne hele registeret hver natt
--     er en sveip som blir slått av.
--
--   * `m12_registrer_objekt` er en FEMTE dør, og den står her fordi
--     alternativet var verre: uten den måtte `m12_registrer_tilgang`
--     opprettet objektet implisitt, og et register der objektet fødes av
--     en skrivefeil i tilgangsraden er et register der «Microsoft 365»
--     og «Microsft 365» er to systemer. Objektet er en EGEN,
--     SP-2-idempotent registrering.
--
--   * `p_aktor` finnes IKKE på gjennomgangsdøren ved siden av
--     `p_gjennomgatt_av`. To parametere for ett menneske er to som kan
--     være uenige. Døren setter `disponit.aktor := p_gjennomgatt_av`, og
--     vakten krever at raden bærer nøyaktig det navnet.
--
-- ============================================================
-- FUNNTYPESETTET ER LUKKET — OG TRE AV FIRE ER UREPRESENTERBARE
-- ============================================================
-- Settet er ('uten_eier', 'uten_hjemmel', 'gjennomgang_utlopt',
-- 'ukjent_objekt'). I v1 kan sveipen bare reise ÉN av dem:
--
--   * `uten_eier` er utelukket av `eier_bruker_id NOT NULL`,
--   * `uten_hjemmel` av den ikke-tomme CHECKen på `hjemmel`,
--   * `ukjent_objekt` av den sammensatte fremmednøkkelen mot
--     `tilgangsobjekt`.
--
-- AT DE IKKE KAN OPPSTÅ ER POENGET, ikke en mangel: en invariant som
-- holder er en funntype som står tom. De blir likevel stående i settet,
-- og det er et bevisst valg om hvor kostnaden skal ligge. Den dagen
-- tilgangene LESES INN fra en identitetsleverandør i stedet for å bli
-- skrevet inn — som er hele v2 — kommer det rader som mangler både eier
-- og hjemmel, og som peker på systemer ingen har registrert. Da skal
-- funntypen alt finnes, og sveipen alt vite hva den heter. Alternativet
-- er en `ALTER TABLE ... DROP/ADD CONSTRAINT` på en bebodd funntabell
-- midt i den migrasjonen som ellers har nok å gjøre.
--
-- ETT FUNN PER (tilgang_id, funntype) HOLDES ÅPENT og oppdateres med
-- `sist_sett_sveip` (M-9s form, 095). En daglig sveip over en tilgang
-- som har vært uetterprøvd i et år gir ETT funn, ikke 365. En funnliste
-- som vokser med kadensen er en funnliste folk lærer seg å overse — og
-- da forsvinner de viktige med dem.
--
-- FORMENE ER HUSETS (089/090/091/095/096): tabellene eies av migrator,
-- dørene av NOLOGIN-rollen `disponit_tilgang_eier`, tenant TEXT + RLS
-- ENABLE+FORCE + `tenant_isolasjon` på hver tabell, SP-1
-- (`krev_tenantkontekst` FØRST) i hver tenantbundet definer, og INGEN
-- BYPASSRLS: kryss-tenant-autoriteten sveipen trenger er en EKSPLISITT,
-- SNEVER policy (§2) og ikke en rolleegenskap.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_tilgang_eier') THEN
        RAISE EXCEPTION 'rollen disponit_tilgang_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `tilgangsobjekt` — HVA det gis tilgang TIL. Ett system, én ressurs:
-- «Microsoft 365», «regnskapssystemet», «lønnsmappa på filserveren».
--
-- Objektet er en EGEN rad og ikke et tekstfelt i tilgangsraden, og det
-- er ikke normalisering for normaliseringens skyld: kritikaliteten er en
-- egenskap ved OBJEKTET, ikke ved den enkelte tilgangen til det, og
-- skrevet per tilgang ville femti rader kunne være uenige om hvor
-- alvorlig det samme systemet er.
CREATE TABLE tilgangsobjekt (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    objekt_id UUID NOT NULL,
    -- Ikke-tom målt med regexklassen og ikke med `btrim`, og klassen
    -- bærer NBSP i tillegg til ASCII-blanktegnene — se dom 2 i
    -- hodekommentaren for begge funnene og for hvor grensen går. Det
    -- gjelder hvert eneste tekstfelt i denne filen som ikke har lov til
    -- å være tomt.
    --
    -- `\u00a0` tolkes av REGEXMOTOREN, ikke av strengparseren:
    -- `standard_conforming_strings` er på, så bakstreken går urørt
    -- gjennom den vanlige literalen og inn i mønsteret. Det er derfor
    -- formen er `'...'` og ikke `E'...'` — med E-formen måtte
    -- bakstreken vært doblet, og en doblet bakstrek i et regexmønster
    -- er nøyaktig den slags detalj som blir feil ved neste redigering.
    system TEXT NOT NULL CHECK (system ~ '[^[:space:]\u00a0]'),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]\u00a0]'),
    -- LUKKET SETT. Kritikaliteten er det som avgjør hvor hardt et funn
    -- på objektet skal leses, og en fritekstkolonne der ville gjort
    -- «høy», «Høy», «HØY» og «kritisk?» til fire nivåer.
    kritikalitet TEXT NOT NULL
        CHECK (kritikalitet IN ('lav', 'middels', 'hoy', 'kritisk')),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    CONSTRAINT tilgangsobjekt_pk PRIMARY KEY (tenant, objekt_id),
    -- Samme system + samme navn er det SAMME objektet. Uten dette ville
    -- to registreringer med hver sin id gitt to «Microsoft 365», og
    -- tilgangsbildet delt seg i to halve sannheter.
    CONSTRAINT tilgangsobjekt_unik UNIQUE (tenant, system, navn)
);

-- `tilgang` — SELVE TILDELINGEN. Én rad per (subjekt, objekt, nivå).
--
-- `gjennomgang_frist` er GENERERT: fristen for neste etterprøving er
-- `sist_gjennomgatt` (eller registreringsdagen, for en tilgang som
-- aldri er etterprøvd) pluss tilgangens egen `gjennomgang_dogn`. At
-- den er STORED og ikke et uttrykk i sveipen er det som gjør
-- fristindeksen under mulig — og det gjør «når forfaller denne» til en
-- egenskap ved raden i stedet for et regnestykke hver leser gjentar.
--
-- `opprettet_dato` finnes ved siden av `opprettet` NØYAKTIG for den
-- generte kolonnens skyld: `opprettet::date` leser tidssonen fra
-- sesjonen og er derfor ikke IMMUTABLE, og Postgres nekter (med rette)
-- å generere en kolonne av den. `DEFAULT current_date` trenger ingen
-- immutabilitet — den evalueres én gang, ved innsettingen.
CREATE TABLE tilgang (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    tilgang_id UUID NOT NULL,
    objekt_id UUID NOT NULL,
    -- HVEM tilgangen gjelder. Fritekst med vilje: subjektet er ofte en
    -- konto i et FREMMED system («ansatt@kunde.example»,
    -- «svc-fakturarobot»), og en fremmednøkkel mot `brukeridentitet`
    -- her ville låst registeret til de menneskene som tilfeldigvis
    -- logger inn i Disponit. EIEREN er den som må finnes hos oss.
    subjekt TEXT NOT NULL CHECK (subjekt ~ '[^[:space:]\u00a0]'),
    subjekttype TEXT NOT NULL
        CHECK (subjekttype IN ('person', 'tjenestekonto')),
    niva TEXT NOT NULL CHECK (niva IN ('les', 'skriv', 'admin')),
    -- DOM 1. NOT NULL + FK mot IDENTITETEN, ikke mot medlemskapet:
    -- mister eieren medlemskapet skal tilgangen fortsatt ha en eier å
    -- navngi, men raden skal ikke rives ut under en åpen gjennomgang.
    -- Samme valg som `plikt.eier_bruker_id` (096) og `varsel.bruker_id`
    -- (026). Døren krever i tillegg AKTIVT MEDLEMSKAP ved
    -- registreringen — FK-en alene sier bare at id-en finnes et sted i
    -- plattformen.
    eier_bruker_id TEXT NOT NULL REFERENCES brukeridentitet (bruker_id),
    -- DOM 2. Hjemmelen: rollen, vedtaket eller avtalen tilgangen kommer
    -- av. En tilgang ingen kan begrunne er et funn selv om den har en
    -- eier.
    hjemmel TEXT NOT NULL CHECK (hjemmel ~ '[^[:space:]\u00a0]'),
    -- Hvor ofte tilgangen skal etterprøves. Taket er ti år: en
    -- «gjennomgang» som kommer sjeldnere enn det er ikke en gjennomgang,
    -- det er en måte å slippe unna funnet på.
    gjennomgang_dogn INT NOT NULL
        CHECK (gjennomgang_dogn >= 1 AND gjennomgang_dogn <= 3650),
    sist_gjennomgatt DATE,
    sist_gjennomgatt_av TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_dato DATE NOT NULL DEFAULT current_date,
    opprettet_av TEXT NOT NULL,
    gjennomgang_frist DATE GENERATED ALWAYS AS
        (coalesce(sist_gjennomgatt, opprettet_dato) + gjennomgang_dogn)
        STORED,
    CONSTRAINT tilgang_pk PRIMARY KEY (tenant, tilgang_id),
    CONSTRAINT tilgang_objekt_fk FOREIGN KEY (tenant, objekt_id)
        REFERENCES tilgangsobjekt (tenant, objekt_id),
    -- DEN HALVE GJENNOMGANGEN ER UREPRESENTERBAR (089-formen): en dato
    -- uten et navn er en gjennomgang ingen kan spørres om, og et navn
    -- uten en dato er en påstand uten tidspunkt.
    CONSTRAINT tilgang_gjennomgang_komplett CHECK (
        (sist_gjennomgatt IS NULL) = (sist_gjennomgatt_av IS NULL)),
    -- Samme subjekt kan ha `les` OG `admin` på samme objekt — det er to
    -- tildelinger, og begge skal etterprøves. Det som IKKE skal finnes
    -- er den samme tildelingen to ganger: da ville en gjennomgang av den
    -- ene latt den andre stå.
    CONSTRAINT tilgang_unik UNIQUE (tenant, objekt_id, subjekt, niva)
);

-- Sveipens skann og flatens liste leser begge «frist først». Indeksen
-- er på den GENERERTE kolonnen, som er hele grunnen til at den finnes:
-- `coalesce(sist_gjennomgatt, opprettet_dato) + gjennomgang_dogn` som
-- uttrykk i WHERE er ikke et indeksbart predikat.
CREATE INDEX tilgang_frist ON tilgang (tenant, gjennomgang_frist);
-- Tilgangsbildet grupperes per objekt; funnlisten og flaten slår begge
-- opp «hvem har tilgang til DETTE».
CREATE INDEX tilgang_objekt ON tilgang (tenant, objekt_id);

-- `tilgangsfunn` — avvikene, gjort synlige.
--
-- Funntypesettet er LUKKET, og tre av fire er urepresenterbare i v1 —
-- se hodekommentaren for hvorfor de likevel står her.
CREATE TABLE tilgangsfunn (
    tenant TEXT NOT NULL,
    tilgang_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CHECK (funntype IN ('uten_eier', 'uten_hjemmel',
                            'gjennomgang_utlopt', 'ukjent_objekt')),
    -- Subjektet og systemet KOPIERES inn i funnet. Funnet skal kunne
    -- leses uten å slå opp de to radene det peker på — og en funnliste
    -- som må joine to tabeller for å si hvem det gjelder er en liste
    -- ingen orker å lese på en vakttelefon.
    subjekt TEXT NOT NULL,
    system TEXT NOT NULL,
    -- Fristen funnet ble reist på. NULL for de funntypene som ikke har
    -- en frist å reise seg av (de tre urepresenterbare i v1).
    frist DATE,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT tilgangsfunn_pk PRIMARY KEY (tenant, tilgang_id, funntype),
    CONSTRAINT tilgangsfunn_tilgang_fk FOREIGN KEY (tenant, tilgang_id)
        REFERENCES tilgang (tenant, tilgang_id),
    -- Et åpent funn har ingen lukketid, et lukket har alltid en. Den
    -- halve lukkingen er urepresenterbar (089/095-formen).
    CONSTRAINT tilgangsfunn_lukking_komplett
        CHECK (apen = (lukket_ts IS NULL))
);

CREATE INDEX tilgangsfunn_apne ON tilgangsfunn (tenant, funntype)
    WHERE apen;

-- ------------------------------------------------------------
-- 2. Radvaktene og radsikkerheten.
-- ------------------------------------------------------------

-- Vakten på `tilgangsobjekt`. TOTALT append-only: et objekt opprettes,
-- og deretter står det. Et system som skifter navn er et NYTT objekt —
-- ellers ville tilgangene som ble registrert til «Fileserver» stått som
-- tilganger til «SharePoint» uten at noen hadde flyttet dem, og hele
-- historikken bak et funn ville skiftet mening under føttene på den som
-- leser den.
CREATE FUNCTION m12_objekt_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'tilgangsobjekt er append-only: % er forbudt — et'
        ' system som skifter navn eller kritikalitet er et NYTT objekt,'
        ' ikke en redigering av det gamle', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE ALL ON FUNCTION m12_objekt_vakt() FROM PUBLIC;
CREATE TRIGGER m12_objekt_vakt
    BEFORE UPDATE OR DELETE ON tilgangsobjekt
    FOR EACH ROW EXECUTE FUNCTION m12_objekt_vakt();
CREATE TRIGGER m12_objekt_ingen_truncate
    BEFORE TRUNCATE ON tilgangsobjekt
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- Vakten på `tilgang` — DOM 3, og modulens viktigste sperre.
--
-- Fire regler:
--
--   * DELETE avvises. Et tilgangsregister der rader kan forsvinne er et
--     register ingen revisjon kan lese bakover — og det er nøyaktig den
--     revisjonen provisjoneringen en gang skal måles mot.
--   * HELE SUBSTANSEN ER FROSSET: objekt, subjekt, subjekttype, nivå,
--     eier, hjemmel, gjennomgangsintervall og opprettelsen. En annen
--     hjemmel, et annet nivå eller et annet subjekt er en ANNEN TILGANG.
--     Dette er invarianten `registerrad_endret_etter_innsetting`, og den
--     gjelder ENHVER skrivevei — også direkte DML som eier.
--   * EN GJENNOMGANG ER FORFATTET, ALDRI AVLEDET. `sist_gjennomgatt`
--     krever en navngitt aktør i sesjonen (`disponit.aktor`), og den
--     aktøren MÅ være den som står i `sist_gjennomgatt_av`. Det finnes
--     ingen jobb i denne migrasjonen som setter datoen — sveipen REISER
--     FUNN og rører ikke kolonnen i det hele tatt. En jobb som skulle
--     kvittert ut en gjennomgang fordi tiden gikk har ingen aktør å
--     skrive, og skrev den en, ville navnet stått i raden for enhver som
--     leser.
--   * `sist_gjennomgatt` kan bare flyttes FRAMOVER. En dato som kan
--     settes tilbake er en frist som kan skyves inn i fortiden for å
--     lukke et funn — altså nøyaktig det registeret finnes for å hindre.
--
-- Den genererte `gjennomgang_frist` røres ikke her: den beregnes av
-- basen ETTER denne triggeren, av kolonnene den nettopp har godkjent.
CREATE FUNCTION m12_tilgang_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT;
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'tilgang: % avvist — en tilgang etterprøves, den'
            ' slettes aldri som rad; at noen HADDE en tilgang er også'
            ' historikk', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.tilgang_id IS DISTINCT FROM OLD.tilgang_id
       OR NEW.objekt_id IS DISTINCT FROM OLD.objekt_id
       OR NEW.subjekt IS DISTINCT FROM OLD.subjekt
       OR NEW.subjekttype IS DISTINCT FROM OLD.subjekttype
       OR NEW.niva IS DISTINCT FROM OLD.niva
       OR NEW.eier_bruker_id IS DISTINCT FROM OLD.eier_bruker_id
       OR NEW.hjemmel IS DISTINCT FROM OLD.hjemmel
       OR NEW.gjennomgang_dogn IS DISTINCT FROM OLD.gjennomgang_dogn
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.opprettet_dato IS DISTINCT FROM OLD.opprettet_dato
       OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av THEN
        RAISE EXCEPTION 'tilgang: registerraden er frosset — en annen'
            ' hjemmel, et annet nivå eller et annet subjekt er en ANNEN'
            ' tilgang, ikke en redigering av denne'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.sist_gjennomgatt IS DISTINCT FROM OLD.sist_gjennomgatt
       OR NEW.sist_gjennomgatt_av IS DISTINCT FROM OLD.sist_gjennomgatt_av
    THEN
        IF NEW.sist_gjennomgatt IS NULL THEN
            RAISE EXCEPTION 'tilgang: en gjennomgang kan ikke tas bort —'
                ' at den ble gjort er også historikk'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF OLD.sist_gjennomgatt IS NOT NULL
           AND NEW.sist_gjennomgatt < OLD.sist_gjennomgatt THEN
            RAISE EXCEPTION 'tilgang: gjennomgangsdatoen går aldri'
                ' bakover — en frist som kan skyves inn i fortiden er'
                ' ingen frist'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL THEN
            RAISE EXCEPTION 'tilgang: en gjennomgang krever en navngitt'
                ' aktør (disponit.aktor) — tiden etterprøver ingenting'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.sist_gjennomgatt_av IS DISTINCT FROM v_aktor THEN
            RAISE EXCEPTION 'tilgang: sist_gjennomgatt_av (%) er ikke'
                ' aktøren som gjennomgår (%)',
                coalesce(NEW.sist_gjennomgatt_av, '<null>'), v_aktor
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m12_tilgang_vakt() FROM PUBLIC;
CREATE TRIGGER m12_tilgang_vakt
    BEFORE UPDATE OR DELETE ON tilgang
    FOR EACH ROW EXECUTE FUNCTION m12_tilgang_vakt();
CREATE TRIGGER m12_tilgang_ingen_truncate
    BEFORE TRUNCATE ON tilgang
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- Vakten på funnet (095-formen): identiteten er frosset, DELETE og
-- TRUNCATE avvises — også for eieren. Sveipen får bare flytte
-- ferskhets- og livsløpsfeltene. Et funn LUKKES, det slettes aldri: at
-- en tilgang VAR uetterprøvd en periode er også historikk, og det er
-- den historikken som gjør at «vi har ryddet opp» kan etterprøves.
CREATE FUNCTION m12_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'tilgangsfunn: % avvist — et funn lukkes, det'
            ' slettes aldri; at en tilgang VAR uetterprøvd er også'
            ' historikk', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.tilgang_id IS DISTINCT FROM OLD.tilgang_id
       OR NEW.funntype IS DISTINCT FROM OLD.funntype
       OR NEW.forst_sett IS DISTINCT FROM OLD.forst_sett THEN
        RAISE EXCEPTION 'tilgangsfunn: identiteten (tenant, tilgang,'
            ' funntype) og førstegangsobservasjonen er frosset — et'
            ' annet funn er en annen rad'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.sist_sett_sveip < OLD.sist_sett_sveip THEN
        RAISE EXCEPTION 'tilgangsfunn: sist_sett_sveip går aldri bakover'
            ' — en ferskhet som kan settes tilbake er ingen ferskhet'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m12_funn_vakt() FROM PUBLIC;
CREATE TRIGGER m12_funn_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON tilgangsfunn
    FOR EACH ROW EXECUTE FUNCTION m12_funn_vakt();
CREATE TRIGGER m12_funn_ingen_truncate
    BEFORE TRUNCATE ON tilgangsfunn
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

ALTER TABLE tilgangsobjekt ENABLE ROW LEVEL SECURITY;
ALTER TABLE tilgangsobjekt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON tilgangsobjekt
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE tilgang ENABLE ROW LEVEL SECURITY;
ALTER TABLE tilgang FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON tilgang
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — ingen BYPASSRLS
-- (096s form, ordrett, og av samme grunn).
--
-- Sveipen må finne HVILKE tenanter som har en gjennomgangsfrist på vei,
-- og det spørsmålet kan ikke stilles innenfra én tenantkontekst.
-- Autoriteten er derfor en policy, ikke en rolleegenskap, og den er
-- gjerdet tre ganger:
--
--   * bare `disponit_tilgang_eier` (dørenes eier — ingen LOGIN-rolle),
--   * bare SELECT (sveipen SKRIVER aldri kryss-tenant: hvert funn
--     skrives etter at konteksten er bundet til RADENS tenant),
--   * bare når det IKKE står en tenantkontekst i sesjonen.
--
-- Det siste leddet er det bærende. Dørene i §3 kommer alltid gjennom
-- `krev_tenantkontekst`, som fail-closed krever en ikke-tom kontekst —
-- inne i en dør er denne policyen derfor ALLTID usann, og
-- `tenant_isolasjon` er den eneste som gjelder. De to er disjunkte per
-- konstruksjon, så kryss-tenant-synet finnes nøyaktig i det ene vinduet
-- sveipen bruker det, og ingen annen kodevei kan snuble inn i det.
CREATE POLICY m12_sveip_tenantliste ON tilgang
    FOR SELECT TO disponit_tilgang_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

ALTER TABLE tilgangsfunn ENABLE ROW LEVEL SECURITY;
ALTER TABLE tilgangsfunn FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON tilgangsfunn
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- INGEN kryss-tenant-policy på `tilgangsfunn`, og det er ikke en
-- forglemmelse: sveipen finner tenantene i `tilgang` (uten kontekst) og
-- gjør ALT funnarbeidet med RADENS tenant satt i konteksten. Skrivingen
-- er dermed tenantbundet av RLS, også for sveipen selv (095s valg).

-- Rettighetene dørenes eier trenger, og ikke mer. Merk hva som IKKE står
-- her: ingen runtime-rolle får en eneste tabellrettighet på de tre
-- tabellene (SP-7, 090/091/095/096-formen) — hele registeret nås KUN
-- gjennom dørene i §3, og de krever tenantkontekst først.
GRANT SELECT, INSERT ON tilgangsobjekt TO disponit_tilgang_eier;
GRANT SELECT, INSERT, UPDATE ON tilgang TO disponit_tilgang_eier;
GRANT SELECT, INSERT, UPDATE ON tilgangsfunn TO disponit_tilgang_eier;
-- Fremmednøkkelen mot identiteten opprettes over (som migrator, som eier
-- begge tabellene); eieren trenger REFERENCES bare hvis en senere
-- migrasjon skulle legge til flere.
GRANT REFERENCES ON brukeridentitet TO disponit_tilgang_eier;
-- Medlemskapssjekken ved registrering: eieren må være et AKTIVT medlem
-- av tenanten. RLS-gjerdet på tenant.
GRANT SELECT ON brukermedlemskap TO disponit_tilgang_eier;
-- Visningsnavnet i lesedøren. KOLONNEGRANT, ikke tabellgrant
-- (husregelen): `issuer` og `sub` er identitetens hemmelige halvdel
-- (010), og tilgangsregisteret har ingen bruk for dem. At registerets
-- eier ALDRI kan lese dem skal være en egenskap ved BASEN, ikke en
-- egenskap ved kode som tilfeldigvis ikke gjør det.
GRANT SELECT (bruker_id, profil) ON brukeridentitet TO disponit_tilgang_eier;
-- EVIDENSKJEDEN (m02): hver registrering og hver gjennomgang skriver sin
-- egen loggpost, i SAMME transaksjon som handlingen. Se §3. INSERT alene
-- — evidenskjeden skrives til, den leses aldri herfra.
--
-- SVEIPEN BRUKER DEN IKKE. Se hodekommentaren: en observasjon som
-- skriver utenfor egne lagre ville gjort den funksjonelle porten på
-- `tilgang_endret_utenfor_registeret` til noe som måtte unnta seg selv.
GRANT INSERT ON revisjonslogg TO disponit_tilgang_eier;

-- Kontekstporten eies av `disponit_m37_claimer` og er REVOKEd fra PUBLIC
-- (038). Dørene under er SECURITY DEFINER og løper som
-- `disponit_tilgang_eier` — uten dette grantet ville SP-1-porten feilet
-- med «permission denied», og registeret vært nede i stedet for sikret.
-- Grantet gis av eieren av porten (039/074-formen); migrator er medlem
-- av begge.
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_tilgang_eier;
RESET ROLE;

-- ------------------------------------------------------------
-- 3. Dørene. SECURITY DEFINER, eid av `disponit_tilgang_eier`, og hver
--    tenantbundet dør kaller `krev_tenantkontekst` FØRST (SP-1). Hele
--    blokken kjører under SET LOCAL ROLE, og ALLE rettighetsendringer på
--    eier-eide funksjoner står INNE i blokken (#140-læren: en REVOKE
--    utenfor lot funksjonen stå PUBLIC-kjørbar mellom to setninger).
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_tilgang_eier;

-- Evidenskjeden, ett sted. Kalles av de to skrivedørene, i deres egen
-- transaksjon — ALDRI av sveipen.
--
-- ÆRLIG OM FORMEN (096s begrunnelse, ordrett): `revisjonslogg` har ingen
-- ciphertext-kolonner (041 §4 dokumenterer det mot levende base), så
-- `payload_type='kryptert'` med `referansepayload IS NULL` er den
-- ordinære formen HVER eksisterende skriver bruker — ikke en påstand om
-- at det finnes en kryptert payload et sted. `referanse`-formen er
-- lukket til domeneovertakelses-familien av `er_gyldig_referansepayload`,
-- og å utvide DEN validatoren for en tilgangshendelse ville vært å låne
-- en tolkning M-12 ikke er blitt gitt. `beslutning='TILLAT'` fordi
-- handlingen ER tillatt og utført; en tilgangsregistrering føder ingen
-- sak.
--
-- `input_hash` er sha256 over den kanoniske beskrivelsen av handlingen,
-- ikke over kundedata. SUBJEKTET, HJEMMELEN OG SYSTEMNAVNET STÅR ALDRI
-- HER: subjektet er et navn på et menneske eller en konto, hjemmelen er
-- kundens tekst, og evidenskjeden skal kunne gjenfinne handlingen uten å
-- arkivere hvem som har tilgang til hva én gang til, i et lager med en
-- annen retensjon.
CREATE FUNCTION m12_evidens(p_tenant TEXT, p_ressurs_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm12_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm12_tilgang', 'handling', p_handling,
        'ressurs_id', p_ressurs_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm12_tilgang',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:tilgangsregister', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m12_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- OBJEKTDØREN. SP-2-materialitet på `p_objekt_id` (m35/056-formen):
-- kalleren utleder id-en deterministisk av sin Idempotency-Key, så et
-- gjenspill med identisk innhold er et STILLE JA (false), mens samme id
-- med ANNET innhold er en materiell konflikt.
--
-- Objektet er en egen registrering og ikke et felt i tilgangsraden — se
-- hodekommentaren: et register der objektet fødes av en skrivefeil i en
-- tilgangsrad er et register der «Microsoft 365» og «Microsft 365» er to
-- systemer, og der halvparten av tilgangene til det ene er usynlige for
-- den som spør om det andre.
CREATE FUNCTION m12_registrer_objekt(
    p_tenant TEXT, p_objekt_id UUID, p_system TEXT, p_navn TEXT,
    p_kritikalitet TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_gammel RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm12_registrer_objekt');
    INSERT INTO public.tilgangsobjekt
        (tenant, objekt_id, system, navn, kritikalitet, opprettet_av)
    VALUES (p_tenant, p_objekt_id, p_system, p_navn, p_kritikalitet,
            p_aktor)
        ON CONFLICT (tenant, objekt_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        SELECT * INTO v_gammel FROM public.tilgangsobjekt
         WHERE tenant = p_tenant AND objekt_id = p_objekt_id;
        IF v_gammel.system IS DISTINCT FROM p_system
           OR v_gammel.navn IS DISTINCT FROM p_navn
           OR v_gammel.kritikalitet IS DISTINCT FROM p_kritikalitet THEN
            RAISE EXCEPTION 'm12_registrer_objekt: samme objekt_id med'
                ' annet innhold — materiell konflikt'
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN false;
    END IF;
    PERFORM public.m12_evidens(
        p_tenant, p_objekt_id, 'tilgangsobjekt.registrert', p_aktor,
        jsonb_build_object('kritikalitet', p_kritikalitet));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m12_registrer_objekt(
    TEXT, UUID, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;

-- TILGANGSDØREN. SP-2-materialiteten dekker HELE tilgangen, ikke bare
-- hodet (096s CodeRabbit-lærdom): nivået avgjør hva subjektet kan gjøre,
-- hjemmelen avgjør om tilgangen kan forsvares, og
-- gjennomgangsintervallet avgjør NÅR noen ser på den igjen. Et gjenspill
-- som endret ett av dem ville fått et stille ja på en tilgang som er noe
-- annet enn den kalleren tror den registrerte.
CREATE FUNCTION m12_registrer_tilgang(
    p_tenant TEXT, p_tilgang_id UUID, p_objekt_id UUID, p_subjekt TEXT,
    p_subjekttype TEXT, p_niva TEXT, p_eier_bruker_id TEXT,
    p_hjemmel TEXT, p_gjennomgang_dogn INT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_gammel RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm12_registrer_tilgang');
    -- DOM 1, håndhevet FØR innsettingen slik at feilmeldingen sier hva
    -- som er galt: eieren må være et AKTIVT medlem av tenanten. FK-en
    -- alene sier bare at bruker-id-en finnes et sted i plattformen — og
    -- en tilgang eid av en fremmed tenants bruker er en tilgang ingen her
    -- etterprøver.
    IF NOT EXISTS (SELECT 1 FROM public.brukermedlemskap bm
                    WHERE bm.tenant = p_tenant
                      AND bm.bruker_id = p_eier_bruker_id AND bm.aktiv) THEN
        RAISE EXCEPTION 'm12_registrer_tilgang: % er ikke et aktivt medlem'
            ' av tenanten — en tilgang uten eier her er en tilgang ingen'
            ' etterprøver', p_eier_bruker_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.tilgang
        (tenant, tilgang_id, objekt_id, subjekt, subjekttype, niva,
         eier_bruker_id, hjemmel, gjennomgang_dogn, opprettet_av)
    VALUES (p_tenant, p_tilgang_id, p_objekt_id, p_subjekt, p_subjekttype,
            p_niva, p_eier_bruker_id, p_hjemmel, p_gjennomgang_dogn,
            p_aktor)
        ON CONFLICT (tenant, tilgang_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        SELECT * INTO v_gammel FROM public.tilgang
         WHERE tenant = p_tenant AND tilgang_id = p_tilgang_id;
        IF v_gammel.objekt_id IS DISTINCT FROM p_objekt_id
           OR v_gammel.subjekt IS DISTINCT FROM p_subjekt
           OR v_gammel.subjekttype IS DISTINCT FROM p_subjekttype
           OR v_gammel.niva IS DISTINCT FROM p_niva
           OR v_gammel.eier_bruker_id IS DISTINCT FROM p_eier_bruker_id
           OR v_gammel.hjemmel IS DISTINCT FROM p_hjemmel
           OR v_gammel.gjennomgang_dogn IS DISTINCT FROM p_gjennomgang_dogn
        THEN
            RAISE EXCEPTION 'm12_registrer_tilgang: samme tilgang_id med'
                ' annet innhold — materiell konflikt'
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN false;
    END IF;
    PERFORM public.m12_evidens(
        p_tenant, p_tilgang_id, 'tilgang.registrert', p_aktor,
        jsonb_build_object('objekt_id', p_objekt_id::text,
                           'subjekttype', p_subjekttype,
                           'niva', p_niva,
                           'eier_bruker_id', p_eier_bruker_id,
                           'gjennomgang_dogn', p_gjennomgang_dogn));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m12_registrer_tilgang(
    TEXT, UUID, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, INT, TEXT)
    FROM PUBLIC;

-- GJENNOMGANGSDØREN. «Jeg har sett på denne tilgangen, og den skal
-- fortsatt finnes» — attestert av et navngitt menneske, i dag.
--
-- ÉN PARAMETER FOR ÉN PERSON. `p_gjennomgatt_av` ER aktøren: døren
-- setter `disponit.aktor` av den, og vakten (§2) krever at raden bærer
-- nøyaktig det navnet. To parametere for ett menneske er to som kan bli
-- uenige — og da ville registeret kunne si at Kari gjennomgikk noe Ola
-- klikket på.
--
-- DATOEN ER BASENS, ikke kallerens. Det finnes ikke et parameter å
-- angi «gjennomgått den 3. januar» med: en gjennomgang som kan
-- tilbakedateres er en frist som kan skyves, og vakten ville uansett
-- nektet en dato bakover. Returnerer den NYE fristen, så kalleren kan
-- vise når neste etterprøving forfaller uten å regne den ut selv.
CREATE FUNCTION m12_registrer_gjennomgang(
    p_tenant TEXT, p_tilgang_id UUID, p_gjennomgatt_av TEXT)
RETURNS DATE LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE t RECORD; v_dag DATE; v_frist DATE;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm12_registrer_gjennomgang');
    IF p_gjennomgatt_av IS NULL
       OR p_gjennomgatt_av !~ '[^[:space:]\u00a0]' THEN
        RAISE EXCEPTION 'm12_registrer_gjennomgang: en gjennomgang er'
            ' FORFATTET — den krever et navn, aldri bare et tidspunkt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.aktor', p_gjennomgatt_av, true);
    SELECT * INTO t FROM public.tilgang
     WHERE tenant = p_tenant AND tilgang_id = p_tilgang_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm12_registrer_gjennomgang: ukjent tilgang %',
            p_tilgang_id USING ERRCODE = 'no_data_found';
    END IF;
    v_dag := current_date;
    -- GJENSPILLET, STILLE JA (096s P1-lærdom). En tapt respons + nytt
    -- klikk samme dag skal ikke skrive evidensraden en gang til: to
    -- identiske gjennomganger i evidenskjeden ser ut som to
    -- etterprøvinger som aldri skjedde. Vakten ville sluppet UPDATEen
    -- gjennom (datoen går ikke bakover når den er lik), så porten må
    -- stå her.
    IF t.sist_gjennomgatt = v_dag
       AND t.sist_gjennomgatt_av IS NOT DISTINCT FROM p_gjennomgatt_av THEN
        RETURN t.gjennomgang_frist;
    END IF;
    UPDATE public.tilgang
       SET sist_gjennomgatt = v_dag, sist_gjennomgatt_av = p_gjennomgatt_av
     WHERE tenant = p_tenant AND tilgang_id = p_tilgang_id
     RETURNING gjennomgang_frist INTO v_frist;
    PERFORM public.m12_evidens(
        p_tenant, p_tilgang_id, 'tilgang.gjennomgatt', p_gjennomgatt_av,
        jsonb_build_object('gjennomgatt', v_dag,
                           'neste_frist', v_frist,
                           'gjennomgang_dogn', t.gjennomgang_dogn));
    RETURN v_frist;
END $$;
REVOKE ALL ON FUNCTION m12_registrer_gjennomgang(TEXT, UUID, TEXT)
    FROM PUBLIC;

-- LESEDØREN (051/090/096-formen): hele tilgangsbildet i ett kall.
-- Runtime har INGEN SELECT på tabellene, så dette er den eneste veien
-- inn — og den krever tenantkontekst først.
--
-- `dogn_til_gjennomgang` regnes HER, i samme skann som raden, fordi
-- flaten ikke skal trekke to datoer fra hverandre (M-16-regelen: ett
-- tall avledet av ett tidspunkt, ikke et forhold mellom to av svarets
-- tall).
CREATE FUNCTION m12_tilgangsbilde(p_tenant TEXT, p_grense INT)
RETURNS TABLE(tilgang_id UUID, objekt_id UUID, system TEXT,
              objektnavn TEXT, kritikalitet TEXT, subjekt TEXT,
              subjekttype TEXT, niva TEXT, eier_bruker_id TEXT,
              eier_navn TEXT, hjemmel TEXT, gjennomgang_dogn INT,
              sist_gjennomgatt DATE, sist_gjennomgatt_av TEXT,
              gjennomgang_frist DATE, dogn_til_gjennomgang INT,
              opprettet TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm12_tilgangsbilde');
    RETURN QUERY
    SELECT tg.tilgang_id, tg.objekt_id, o.system, o.navn, o.kritikalitet,
           tg.subjekt, tg.subjekttype, tg.niva, tg.eier_bruker_id,
           -- Visningsnavnet fra den LUKKEDE profil-DTO-en (010). NULL når
           -- IdP-en ikke ga noe — flaten viser da bruker-id-en, som er
           -- ærligere enn en tom celle.
           nullif(btrim(coalesce(b.profil->>'visningsnavn', '')), ''),
           tg.hjemmel, tg.gjennomgang_dogn, tg.sist_gjennomgatt,
           tg.sist_gjennomgatt_av, tg.gjennomgang_frist,
           (tg.gjennomgang_frist - current_date)::int,
           tg.opprettet
      FROM public.tilgang tg
      JOIN public.tilgangsobjekt o
        ON o.tenant = tg.tenant AND o.objekt_id = tg.objekt_id
      LEFT JOIN public.brukeridentitet b
        ON b.bruker_id = tg.eier_bruker_id
     WHERE tg.tenant = p_tenant
     -- Det som forfaller først står øverst, og det som er forfalt står
     -- aller øverst.
     ORDER BY tg.gjennomgang_frist, o.system, tg.subjekt, tg.tilgang_id
     LIMIT greatest(least(coalesce(p_grense, 200), 500), 1);
END $$;
REVOKE ALL ON FUNCTION m12_tilgangsbilde(TEXT, INT) FROM PUBLIC;

-- OBJEKTLISTEN. Den finnes for at flaten skal kunne LA NOEN VELGE et
-- objekt i stedet for å skrive inn en UUID, og det er ikke ergonomi
-- alene: et fritekstfelt for objekt-id-en er den korteste veien til at
-- en tilgang blir registrert på feil system, og en tilgang registrert på
-- feil system er en tilgang ingen finner igjen når den skal etterprøves.
--
-- `antall_tilganger` er tellingen registeret uansett gjør ved hvert
-- oppslag på et objekt. Den står her fordi «hvor mange har egentlig
-- tilgang til dette» er det første spørsmålet noen stiller om et
-- kritisk system — og fordi et objekt uten tilganger er det ENESTE
-- stedet flaten kan vise at en registrering ble halvferdig.
CREATE FUNCTION m12_objekter(p_tenant TEXT, p_grense INT)
RETURNS TABLE(objekt_id UUID, system TEXT, navn TEXT, kritikalitet TEXT,
              antall_tilganger INT, opprettet TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm12_objekter');
    RETURN QUERY
    SELECT o.objekt_id, o.system, o.navn, o.kritikalitet,
           (SELECT count(*) FROM public.tilgang tg
             WHERE tg.tenant = o.tenant
               AND tg.objekt_id = o.objekt_id)::int,
           o.opprettet
      FROM public.tilgangsobjekt o
     WHERE o.tenant = p_tenant
     ORDER BY o.system, o.navn
     LIMIT greatest(least(coalesce(p_grense, 200), 500), 1);
END $$;
REVOKE ALL ON FUNCTION m12_objekter(TEXT, INT) FROM PUBLIC;

-- FUNNLESINGEN. Et funn ingen kan se er ikke et funn — det er en rad.
-- Denne døren er grunnen til at gjennomgangssveipen er synlig i flaten
-- og ikke bare i en JSON-linje i journalen.
CREATE FUNCTION m12_apne_funn(p_tenant TEXT, p_grense INT)
RETURNS TABLE(tilgang_id UUID, funntype TEXT, subjekt TEXT, system TEXT,
              frist DATE, forst_sett TIMESTAMPTZ,
              sist_sett_sveip TIMESTAMPTZ, alder_s BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm12_apne_funn');
    RETURN QUERY
    SELECT f.tilgang_id, f.funntype, f.subjekt, f.system, f.frist,
           f.forst_sett, f.sist_sett_sveip,
           EXTRACT(EPOCH FROM (now() - f.forst_sett))::bigint
      FROM public.tilgangsfunn f
     WHERE f.apen
     ORDER BY f.frist, f.system, f.subjekt, f.funntype
     LIMIT greatest(least(coalesce(p_grense, 100), 500), 1);
END $$;
REVOKE ALL ON FUNCTION m12_apne_funn(TEXT, INT) FROM PUBLIC;

-- ------------------------------------------------------------
-- 4. Gjennomgangssveipen.
-- ------------------------------------------------------------

-- Per-tenant-arbeidet. Egen funksjon nettopp for at SP-1-PORTEN SKAL
-- GJELDE HER OGSÅ (096s `m21_koe_for_tenant`-form): sveipen under binder
-- konteksten til RADENS tenant og kaller hit, og da går funnarbeidet
-- gjennom nøyaktig den `krev_tenantkontekst` enhver annen kaller går
-- gjennom. Porten er ikke noe sveipen slipper unna, bare noe den
-- oppfyller per tenant.
--
-- Tre steg, i denne rekkefølgen (095s form):
--
--   1. FERSKHETEN på funn som alt finnes. IDEMPOTENSEN BOR HER: en sveip
--      nummer to på den samme utløpte gjennomgangen flytter
--      `sist_sett_sveip` og skriver ingen ny rad.
--   2. DE NYE — inntil `p_grense` av dem, de mest forfalte først.
--   3. LUKKINGEN av funn som ikke lenger gjelder (noen registrerte en
--      gjennomgang). Raden består: at en tilgang VAR uetterprøvd er også
--      historikk.
--
-- STEG 2 ER DET ENESTE SOM ER TAKET. Ferskheten og lukkingen er bundet
-- av hvor mange funn som ALT står åpne, altså av tidligere kjøringers
-- tak — de kan ikke løpe løpsk, og et tak på lukkingen ville tvert imot
-- latt et funn stå åpent etter at det var rettet. Returnerer `avkortet`
-- når taket faktisk ble truffet: en kjøring som ikke rakk hele
-- registeret har ikke MÅLT hele registeret, og «en jobb som ikke kunne
-- måle rapporterer FUNN, aldri null».
CREATE FUNCTION m12_sveip_for_tenant(p_tenant TEXT, p_grense INT)
RETURNS TABLE(nye INT, oppdaterte INT, lukkede INT, avkortet BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_dag DATE; v_naa TIMESTAMPTZ; v_grense INT; v_n INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm12_sveip_for_tenant');
    v_dag := current_date;
    v_naa := now();
    v_grense := greatest(coalesce(p_grense, 100), 1);
    nye := 0; oppdaterte := 0; lukkede := 0; avkortet := false;

    -- 1. Ferskheten.
    UPDATE public.tilgangsfunn f
       SET sist_sett_sveip = v_naa, apen = true, lukket_ts = NULL,
           frist = k.gjennomgang_frist, subjekt = k.subjekt,
           system = k.system
      FROM (SELECT tg.tilgang_id, tg.subjekt, tg.gjennomgang_frist,
                   o.system
              FROM public.tilgang tg
              JOIN public.tilgangsobjekt o
                ON o.tenant = tg.tenant AND o.objekt_id = tg.objekt_id
             WHERE tg.gjennomgang_frist < v_dag) k
     WHERE f.tilgang_id = k.tilgang_id
       AND f.funntype = 'gjennomgang_utlopt';
    GET DIAGNOSTICS v_n = ROW_COUNT;
    oppdaterte := v_n;

    -- 2. De nye — de mest forfalte først, så et tak aldri skjuler den
    --    tilgangen som har stått lengst uten at noen så på den.
    INSERT INTO public.tilgangsfunn
        (tenant, tilgang_id, funntype, subjekt, system, frist,
         forst_sett, sist_sett_sveip, apen)
    SELECT p_tenant, k.tilgang_id, 'gjennomgang_utlopt', k.subjekt,
           k.system, k.gjennomgang_frist, v_naa, v_naa, true
      FROM (SELECT tg.tilgang_id, tg.subjekt, tg.gjennomgang_frist,
                   o.system
              FROM public.tilgang tg
              JOIN public.tilgangsobjekt o
                ON o.tenant = tg.tenant AND o.objekt_id = tg.objekt_id
             WHERE tg.gjennomgang_frist < v_dag
               AND NOT EXISTS (
                   SELECT 1 FROM public.tilgangsfunn f
                    WHERE f.tilgang_id = tg.tilgang_id
                      AND f.funntype = 'gjennomgang_utlopt')
             ORDER BY tg.gjennomgang_frist, tg.tilgang_id
             LIMIT v_grense + 1) k
     ORDER BY k.gjennomgang_frist, k.tilgang_id
     LIMIT v_grense
        ON CONFLICT (tenant, tilgang_id, funntype) DO NOTHING;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    nye := v_n;
    -- `LIMIT v_grense + 1` i underspørringen er hvordan taket MÅLES uten
    -- en ekstra skanning: kom det flere kandidater enn taket, var
    -- kjøringen avkortet. En sammenlikning på `nye = v_grense` alene
    -- ville sagt «avkortet» om en kjøring som traff taket på siste rad og
    -- faktisk var ferdig.
    IF v_n >= v_grense THEN
        avkortet := EXISTS (
            SELECT 1
              FROM public.tilgang tg
             WHERE tg.gjennomgang_frist < v_dag
               AND NOT EXISTS (
                   SELECT 1 FROM public.tilgangsfunn f
                    WHERE f.tilgang_id = tg.tilgang_id
                      AND f.funntype = 'gjennomgang_utlopt'));
    END IF;

    -- 3. Lukkingen.
    UPDATE public.tilgangsfunn f
       SET apen = false, lukket_ts = v_naa
     WHERE f.apen
       AND f.funntype = 'gjennomgang_utlopt'
       AND NOT EXISTS (
            SELECT 1 FROM public.tilgang tg
             WHERE tg.tilgang_id = f.tilgang_id
               AND tg.gjennomgang_frist < v_dag);
    GET DIAGNOSTICS v_n = ROW_COUNT;
    lukkede := v_n;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m12_sveip_for_tenant(TEXT, INT) FROM PUBLIC;

-- GJENNOMGANGSSVEIPEN. Kryss-tenant per konstruksjon, med INNELUKKET
-- autoritet (038s reaperdoktrine, 096s form): funksjonen har ingen
-- `p_tenant` en kaller kan velge, hele utvalget ligger i policyen
-- `m12_sveip_tenantliste` og i predikatet under, og kallerens egen
-- kontekst legges tilbake til slutt så en kjøring aldri etterlater en
-- fremmed tenant i transaksjonen den ble kalt fra.
--
-- DEN NEKTER Å KJØRE MED EN TENANTKONTEXT SATT (095s form). En kaller
-- som har satt en kontekst ber om noe annet enn det denne funksjonen
-- gjør, og å svare den med et delvis kryss-tenant-resultat ville vært å
-- gjette hva den mente.
--
-- TENANTLISTEN MATERIALISERES FØR konteksten røres. Leste løkka rett fra
-- tabellen mens den satte kontekst per iterasjon, ville policyen (som
-- leser nøyaktig den GUC-en) slått seg av under føttene på sin egen
-- markør etter første tenant. Et array er billig og gjør rekkefølgen
-- deterministisk.
--
-- TAKET GJELDER PER TENANT, ikke over hele kjøringen. En global teller
-- som ble brukt opp av den første tenanten ville sultet de neste — hver
-- natt, i samme rekkefølge, for alltid. Hver tenant skal ha sin egen
-- kjøring hver natt; `avkortet` sier fra når noen av dem ikke ble
-- ferdig.
CREATE FUNCTION m12_sveip_gjennomganger(p_grense INT DEFAULT 100)
RETURNS TABLE(tenanter INT, nye INT, oppdaterte INT, lukkede INT,
              avkortet BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT; v_tenanter TEXT[]; v_t TEXT; r RECORD;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm12_sveip_gjennomganger: sveipen er KRYSS-TENANT'
            ' og kjøres uten tenantkontekst — en kaller som har satt en'
            ' kontekst ber om noe annet enn det denne funksjonen gjør'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_aktor := current_setting('disponit.aktor', true);
    tenanter := 0; nye := 0; oppdaterte := 0; lukkede := 0;
    avkortet := false;
    -- KUN `tilgang` leses her. Kryss-tenant-policyen (§2) står på nøyaktig
    -- den ene tabellen, og et JOIN mot `tilgangsobjekt` ville derfor
    -- returnert null rader uten kontekst — stille, og med et helt
    -- register som aldri ble sveipet.
    SELECT array_agg(DISTINCT tg.tenant ORDER BY tg.tenant)
      INTO v_tenanter
      FROM public.tilgang tg;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        -- Én tenant om gangen, bundet til RADENS tenant — og gjennom den
        -- samme porten alle andre kallere går gjennom.
        PERFORM set_config('disponit.tenant', v_t, true);
        tenanter := tenanter + 1;
        SELECT * INTO r FROM public.m12_sveip_for_tenant(v_t, p_grense);
        nye := nye + r.nye;
        oppdaterte := oppdaterte + r.oppdaterte;
        lukkede := lukkede + r.lukkede;
        avkortet := avkortet OR r.avkortet;
    END LOOP;
    -- Konteksten legges tilbake der den sto: en sveip skal ikke etterlate
    -- seg en tenant i sesjonen den ikke ble kalt med. Den ble kalt UTEN
    -- kontekst (porten over), så «der den sto» er tom.
    PERFORM set_config('disponit.tenant', '', true);
    PERFORM set_config('disponit.aktor', coalesce(v_aktor, ''), true);
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m12_sveip_gjennomganger(INT) FROM PUBLIC;

-- ------------------------------------------------------------
-- 5. Rettighetene — INNE i eierblokken (#140-læren: en REVOKE utenfor
--    lot funksjonen stå PUBLIC-kjørbar mellom to setninger).
--
--    Migrasjonen NAVNGIR IKKE runtime-rollen (057-lærdommen):
--    `deploy/staging/migrer.py` er autoritativ for den konfigurerte
--    rollen. En GRANT her ville lagt rettighetsmodellen to steder, og
--    det ene stedet ville vært usant på enhver installasjon som kaller
--    rollen noe annet.
-- ------------------------------------------------------------
DO $$
BEGIN
    -- Sveipen: ÉN EXECUTE til sveiperollen. Ingen tabellrettigheter —
    -- rollen har ingen i dag, og M-12 gir den ingen. Den kan reise funn
    -- i alle tenanter uten å kunne LESE en eneste tilgangsrad selv; hele
    -- autoriteten står i den eier-eide defineren, revidérbar på ett sted.
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_tilgangssveip') THEN
        GRANT EXECUTE ON FUNCTION m12_sveip_gjennomganger(INT)
            TO disponit_tilgangssveip;
    END IF;
    -- REVOKE-en er ikke pynt (091/095/096-formen): en rettighet som bare
    -- slutter å bli gitt er ikke trukket tilbake. Sveipen er kryss-tenant
    -- og setter selv RLS-konteksten — altså nøyaktig det vinduet
    -- sveiperollen finnes for å nekte forespørselsveien. Samme snitt som
    -- 038-reaperen, og samme grunn: en kompromittert runtime skal se ÉN
    -- tenant om gangen.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        REVOKE ALL ON FUNCTION m12_sveip_gjennomganger(INT) FROM disponit;
        REVOKE ALL ON FUNCTION m12_sveip_for_tenant(TEXT, INT)
            FROM disponit;
    END IF;
END $$;

RESET ROLE;
