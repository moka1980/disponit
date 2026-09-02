-- 101: M-13 bankavstemmingsagent v1 — AVSTEMMINGSREGISTERET.
-- Fem tenant-skopede tabeller, seks dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- V1-AVGRENSNINGEN, ORDRETT FRA MANIFESTET: katalogteksten lover
-- automatisk bokføring ved full match. DEN FINNES IKKE HER, og fraværet
-- er dommen og ikke en manglende funksjon. En automatisk bokføring er en
-- skriving i regnskapet, og et regnskap som endres av noe ingen leste er
-- ikke et regnskap. Det finnes derfor ingen hovedbokstabell i denne
-- migrasjonen, ingen posteringsdør, ingen utgående kobling mot noe
-- regnskapssystem — og ingen kolonne som later som om noe ble bokført.
--
-- Å SE DE UAVSTEMTE POSTENE ER DESSUTEN FORUTSETNINGEN for å kunne
-- automatisere trygt: uten en MÅLT treffrate på matchreglene er en
-- autobokføring et veddemål. v1 gjør matchen målbar; utførelsen kommer
-- når tallet finnes.
--
-- DOMMENE v1 HVILER PÅ, håndhevet i DATAMODELLEN og ikke i et API-lag
-- som kunne omgås:
--
--   1. BELØP ER HELTALL I MINSTE ENHET (øre), `BIGINT`, uten unntak.
--      Et flyttall i en avstemming viser seg først når summene ikke går
--      opp, og da er det ikke lenger til å finne ut av. Ingen kolonne
--      her er `NUMERIC`, `REAL` eller `DOUBLE PRECISION`, og porten i
--      testene måler nettopp det mot katalogen.
--
--   2. EN BANKPOST KAN VÆRE AVSTEMT HØYST ÉN GANG. Dobbeltmatch er
--      feilen som får et regnskap til å stemme på papiret og ikke i
--      virkeligheten, og den er stille. Formen er en PARTIELL unik
--      indeks — `WHERE opphevet_ts IS NULL` — så en opphevet avstemming
--      frigjør posten uten at raden forsvinner. At noe VAR avstemt er
--      også historikk.
--
--   3. EN AVSTEMMING ER ET FORHOLD MELLOM TO IDENTIFISERTE SIDER.
--      Sammensatte fremmednøkler `(tenant, post_id)` og
--      `(tenant, bilag_id)` gjør en match mot en annen tenants post
--      urepresenterbar — det er en egenskap ved NØKKELEN, ikke ved et
--      predikat noen kan glemme.
--
--   4. «AVSTEMT» ER IKKE EN KOLONNE. En bankpost er avstemt hvis og bare
--      hvis det finnes en ikke-opphevet rad i `avstemming` som peker på
--      den. To kilder på samme spørsmål kan aldri holdes i takt, og en
--      statuskolonne som drev fra matchtabellen ville vært nøyaktig den
--      stille feilen registeret finnes for å hindre.
--
-- DELBETALING ER TILLATT, DOBBELTMATCH ER DET IKKE. Flere bankposter kan
-- peke på SAMME bilag (en faktura betalt i to omganger er dagligdags),
-- men én post kan ikke peke på to bilag. Restbeløpet regnes i lesedøren
-- som `bilag.belop_ore - sum(matchede poster)` — det er derfor bilaget
-- IKKE har en unik indeks, og posten har.
--
-- KONTONUMMERET LAGRES ALDRI HELT. Døren `m13_registrer_konto` tar imot
-- det fulle nummeret, lagrer de fire siste sifrene og en sha256 av
-- resten, og glemmer originalen. Registeret trenger å kunne SI hvilken
-- konto en post hører til og å kjenne igjen den samme kontoen på nytt —
-- ingen av delene krever det hele nummeret. Å lagre det man ikke trenger
-- er hvordan et register blir et brudd.
--
-- GRENSEN MOT M-23 (104), sagt eksplisitt så ingen bygger det to ganger:
-- M-13 eier BANKPOSTER — det som har skjedd på konto. M-23 eier
-- FORDRINGER — det kunden skylder. En innbetaling er begge deler sett
-- fra hver sin side, og de kobles IKKE automatisk: en ubetalt fordring
-- med en umatchet innbetaling i samme størrelsesorden er noe et menneske
-- skal se på, aldri en lukking basen feller selv.
--
-- SVEIPEN HAR SIN EGEN ROLLE OG SIN EGEN TIMER (095/100-formen):
-- `disponit_avstemmingssveip` har nøyaktig ÉN rettighet i basen — EXECUTE
-- på `m13_sveip_avstemming` — og INGEN tabellrettigheter. Sveipen KØER
-- INGEN VARSEL; den skriver FUNN, og funnene leses i flaten. En ny
-- varselart ville krevd at både fasiten i
-- `deploy/staging/varselenum-reparasjon.sql` og `KANONISK` i
-- `test_varselenum.py` ble utvidet i samme commit, og v1 trenger ingen
-- av delene: en uavstemt post er en tilstand i registeret, ikke en frist
-- som løper fra noen.
--
-- FORMENE ER HUSETS (089/090/091/095/096/100): tabellene eies av
-- migrator, dørene av NOLOGIN-rollen `disponit_avstemming_eier`, tenant
-- TEXT + RLS ENABLE+FORCE + `tenant_isolasjon` på hver tabell, SP-1
-- (`krev_tenantkontekst` FØRST) i hver tenantbundet definer, og INGEN
-- BYPASSRLS: kryss-tenant-autoriteten sveipen trenger er en EKSPLISITT,
-- SNEVER policy og ikke en rolleegenskap.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_avstemming_eier') THEN
        RAISE EXCEPTION 'rollen disponit_avstemming_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

-- FERSK-SERVER-FUNNET (095/100-formen): PostgreSQL >= 15 gir ingen CREATE
-- på `public` til andre enn skjemaeieren, og §3 oppretter funksjoner
-- under `SET LOCAL ROLE disponit_avstemming_eier`. Grantet står HER og
-- ikke i `migrer.py`, fordi rettighetsblokkene der kjøres ETTER
-- migrasjonene — altså for sent til å redde denne.
GRANT USAGE, CREATE ON SCHEMA public TO disponit_avstemming_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `bankkonto` — hvilke kontoer vi avstemmer. Tenant-skopet, som alt
-- annet her.
--
-- `kontonummer_hale` er de FIRE SISTE sifrene og ikke mer: det er nok
-- til at et menneske kjenner igjen kontoen i en liste, og for lite til
-- å utgjøre et kontonummer. `kontonummer_hash` er sha256 over det
-- normaliserte hele nummeret og finnes for at DEN SAMME kontoen skal
-- kunne gjenkjennes ved en ny registrering — uten at nummeret ligger
-- her. Den unike indeksen står på hashen, ikke på halen: fire sifre
-- kolliderer, elleve gjør det ikke.
CREATE TABLE bankkonto (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    konto_id UUID NOT NULL,
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    kontonummer_hale TEXT NOT NULL CHECK (kontonummer_hale ~ '^[0-9]{4}$'),
    kontonummer_hash TEXT NOT NULL CHECK (kontonummer_hash ~ '^[0-9a-f]{64}$'),
    -- ISO 4217. Lukket form, ikke lukket sett: en CHECK med en liste
    -- ville krevd en migrasjon for hver nye valuta, og valutakoder er
    -- ikke en dom huset feller.
    valuta TEXT NOT NULL CHECK (valuta ~ '^[A-Z]{3}$'),
    aktiv BOOLEAN NOT NULL DEFAULT true,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    CONSTRAINT bankkonto_pk PRIMARY KEY (tenant, konto_id)
);
CREATE UNIQUE INDEX bankkonto_unik
    ON bankkonto (tenant, kontonummer_hash);

-- `bankpost` — det som FAKTISK har skjedd på konto.
--
-- `belop_ore` ER FORTEGNET: positivt = inn, negativt = ut. `<> 0` fordi
-- en bevegelse på null kroner ikke er en bevegelse, og en post uten
-- beløp ville vært en rad ingen avstemming kunne dømme.
--
-- `ekstern_ref` er bankens egen id for transaksjonen, og den unike
-- indeksen på (tenant, konto_id, ekstern_ref) ER importidempotensen: den
-- samme kontoutskriften lastet inn to ganger gir de samme radene, ikke
-- dobbelt så mange. Uten den ville en gjentatt import sett ut som doble
-- innbetalinger — og et regnskap som stemmer på papiret er nettopp den
-- feilen dette registeret finnes for.
CREATE TABLE bankpost (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    post_id UUID NOT NULL,
    konto_id UUID NOT NULL,
    ekstern_ref TEXT NOT NULL CHECK (ekstern_ref ~ '[^[:space:]]'),
    bokfort DATE NOT NULL,
    belop_ore BIGINT NOT NULL CHECK (belop_ore <> 0),
    tekst TEXT NOT NULL CHECK (tekst ~ '[^[:space:]]'),
    motpart TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    CONSTRAINT bankpost_pk PRIMARY KEY (tenant, post_id),
    CONSTRAINT bankpost_konto_fk FOREIGN KEY (tenant, konto_id)
        REFERENCES bankkonto (tenant, konto_id)
);
CREATE UNIQUE INDEX bankpost_ekstern_unik
    ON bankpost (tenant, konto_id, ekstern_ref);
-- Sveipens spørring: uavstemte poster sortert på alder. Indeksen står på
-- (tenant, bokfort) fordi det er både filteret og sorteringen.
CREATE INDEX bankpost_alder ON bankpost (tenant, bokfort);

-- `bilag` — det som SKULLE ha skjedd. Faktura, kvittering, krav.
--
-- `retning` er et LUKKET SETT, ikke fritekst: hele avstemmingen hviler på
-- at fortegnet på bankposten og retningen på bilaget hører sammen, og et
-- åpent sett ville gjort den regelen umulig å håndheve.
CREATE TABLE bilag (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    bilag_id UUID NOT NULL,
    bilagsnummer TEXT NOT NULL CHECK (bilagsnummer ~ '[^[:space:]]'),
    retning TEXT NOT NULL CHECK (retning IN ('inn', 'ut')),
    -- ALLTID POSITIVT. Retningen bærer fortegnet, ikke tallet — to
    -- steder å lese fortegnet fra er ett sted for mye, og et negativt
    -- beløp på et `inn`-bilag ville vært en rad ingen kunne tolke.
    belop_ore BIGINT NOT NULL CHECK (belop_ore > 0),
    motpart TEXT NOT NULL CHECK (motpart ~ '[^[:space:]]'),
    utstedt DATE NOT NULL,
    forfall DATE,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    CONSTRAINT bilag_pk PRIMARY KEY (tenant, bilag_id),
    -- Forfall før utstedelse er ikke en frist, det er en skrivefeil.
    CONSTRAINT bilag_forfall_etter_utstedt
        CHECK (forfall IS NULL OR forfall >= utstedt)
);
CREATE UNIQUE INDEX bilag_nummer_unik ON bilag (tenant, bilagsnummer);
CREATE INDEX bilag_forfall ON bilag (tenant, forfall)
    WHERE forfall IS NOT NULL;

-- `avstemming` — selve matchen. ÉN rad = én bankpost knyttet til ett
-- bilag.
--
-- OPPHEVING SLETTER IKKE. En feilaktig match rettes ved at raden får
-- `opphevet_ts`, `opphevet_av` og en begrunnelse — og den PARTIELLE
-- unike indeksen slipper da posten fri for en ny match. En slettet rad
-- ville skjult at noen en gang mente noe annet, og det er nøyaktig det
-- en revisor spør etter.
--
-- `avvik_ore` er differansen døren regnet DA matchen ble gjort, lagret
-- fordi den er en observasjon og ikke en avledning: bilagets beløp kan
-- ikke endres (vakten under), men restbeløpet endrer seg når en
-- delbetaling nummer to kommer. Å regne avviket på nytt ved lesing ville
-- gitt et annet tall enn det den som matchet så.
CREATE TABLE avstemming (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    avstemming_id UUID NOT NULL,
    post_id UUID NOT NULL,
    bilag_id UUID NOT NULL,
    metode TEXT NOT NULL CHECK (metode IN ('automatisk', 'manuell')),
    avvik_ore BIGINT NOT NULL,
    begrunnelse TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    opphevet_ts TIMESTAMPTZ,
    opphevet_av TEXT,
    opphevet_begrunnelse TEXT,
    CONSTRAINT avstemming_pk PRIMARY KEY (tenant, avstemming_id),
    CONSTRAINT avstemming_post_fk FOREIGN KEY (tenant, post_id)
        REFERENCES bankpost (tenant, post_id),
    CONSTRAINT avstemming_bilag_fk FOREIGN KEY (tenant, bilag_id)
        REFERENCES bilag (tenant, bilag_id),
    -- EN MANUELL MATCH KREVER EN BEGRUNNELSE. Det er ikke byråkrati:
    -- en manuell match er nettopp det tilfellet der regelen IKKE traff,
    -- og hvorfor et menneske likevel mente at de to hører sammen er den
    -- eneste opplysningen som gjør matchen etterprøvbar.
    CONSTRAINT avstemming_manuell_krever_begrunnelse
        CHECK (metode <> 'manuell'
               OR (begrunnelse IS NOT NULL
                   AND begrunnelse ~ '[^[:space:]]')),
    -- Opphevingens tre felter står eller faller sammen. En opphevet rad
    -- uten hvem og hvorfor er en rad som sier at noen gjorde noe, og
    -- ingenting mer.
    CONSTRAINT avstemming_opphevet_helhet
        CHECK ((opphevet_ts IS NULL AND opphevet_av IS NULL
                AND opphevet_begrunnelse IS NULL)
               OR (opphevet_ts IS NOT NULL AND opphevet_av IS NOT NULL
                   AND opphevet_begrunnelse ~ '[^[:space:]]'))
);
-- DOM 2, i indeksform: en post er avstemt høyst én gang OM GANGEN.
CREATE UNIQUE INDEX avstemming_post_unik
    ON avstemming (tenant, post_id) WHERE opphevet_ts IS NULL;
CREATE INDEX avstemming_bilag ON avstemming (tenant, bilag_id)
    WHERE opphevet_ts IS NULL;

-- `avstemmingsfunn` — sveipens dom. Samme form som `kontrollfunn` (100):
-- idempotent per (objekt, funntype), og en rad som lukkes består.
--
-- `objekttype` finnes fordi et funn kan gjelde EN POST (uavstemt for
-- lenge) eller ET BILAG (forfalt uten full dekning). To funntabeller
-- ville gitt to sveip, to lesedører og to steder å glemme en rettelse;
-- ett felt med et lukket sett gir én.
CREATE TABLE avstemmingsfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    objekttype TEXT NOT NULL CHECK (objekttype IN ('post', 'bilag')),
    objekt_id UUID NOT NULL,
    funntype TEXT NOT NULL CHECK (funntype IN (
        'uavstemt_post_over_grense',
        'forfalt_bilag_uten_dekning',
        'delvis_dekket_bilag')),
    -- Hvor mange døgn objektet var over sin egen grense da funnet sist
    -- ble sett.
    dogn_over_grense INT,
    -- Restbeløpet i øre for bilagsfunnene; NULL for postfunn, der
    -- spørsmålet ikke gir mening.
    rest_ore BIGINT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT avstemmingsfunn_pk
        PRIMARY KEY (tenant, objekttype, objekt_id, funntype),
    CONSTRAINT avstemmingsfunn_lukking
        CHECK ((apen AND lukket_ts IS NULL)
               OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE INDEX avstemmingsfunn_apne ON avstemmingsfunn (tenant, funntype)
    WHERE apen;


-- ------------------------------------------------------------
-- 2. Radvaktene. CHECK-ene over gjelder én rad; vaktene gjelder
--    FORHOLDET mellom rader — og de gjelder enhver skrivevei, også
--    direkte DML som tabellens eier.
-- ------------------------------------------------------------

-- En bankpost er en OBSERVASJON. Den skjedde, og det som skjedde endrer
-- seg ikke fordi noen redigerer en rad. Kom det inn feil, er svaret en
-- ny import med riktig `ekstern_ref` — ikke en retusjert historie.
CREATE FUNCTION m13_bankpost_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'bankpost: DELETE avvist — en kontobevegelse'
            ' slettes ikke, den er en observasjon'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RAISE EXCEPTION 'bankpost: UPDATE avvist — raden er append-only. Det'
        ' som skjedde på konto endrer seg ikke fordi noen redigerer den'
        USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE ALL ON FUNCTION m13_bankpost_vakt() FROM PUBLIC;
CREATE TRIGGER m13_bankpost_vakt
    BEFORE UPDATE OR DELETE ON bankpost
    FOR EACH ROW EXECUTE FUNCTION m13_bankpost_vakt();
CREATE TRIGGER m13_bankpost_ingen_truncate
    BEFORE TRUNCATE ON bankpost
    EXECUTE FUNCTION m13_bankpost_vakt();

-- Bilaget er nesten like fast. BELØPET ER FROSSET, og det er den
-- bærende: restbeløpet i lesedøren regnes mot det, og et beløp som
-- kunne endres etter en match ville gjort hver eldre avstemming til en
-- påstand om et tall som ikke lenger finnes. Endres fakturaen, er den en
-- ny faktura med et nytt bilagsnummer.
CREATE FUNCTION m13_bilag_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'bilag: DELETE avvist — et bilag som er matchet'
            ' eller forfalt er historikk'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.bilag_id IS DISTINCT FROM OLD.bilag_id
       OR NEW.bilagsnummer IS DISTINCT FROM OLD.bilagsnummer
       OR NEW.retning IS DISTINCT FROM OLD.retning
       OR NEW.belop_ore IS DISTINCT FROM OLD.belop_ore
       OR NEW.utstedt IS DISTINCT FROM OLD.utstedt
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'bilag: identiteten (tenant, bilag_id, nummer,'
            ' retning, beløp, utstedt) er frosset — et annet beløp er et'
            ' annet bilag'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m13_bilag_vakt() FROM PUBLIC;
CREATE TRIGGER m13_bilag_vakt
    BEFORE UPDATE OR DELETE ON bilag
    FOR EACH ROW EXECUTE FUNCTION m13_bilag_vakt();
CREATE TRIGGER m13_bilag_ingen_truncate
    BEFORE TRUNCATE ON bilag
    EXECUTE FUNCTION m13_bilag_vakt();

-- AVSTEMMINGSVAKTEN bærer dom 3 og halvparten av dom 2.
--
-- FORTEGNSREGELEN er den som gjør en match til noe mer enn to id-er ved
-- siden av hverandre: en INNGÅENDE bankpost (positivt beløp) kan bare
-- dekke et bilag med retning `inn`, og en utgående bare `ut`. Uten den
-- ville en utbetaling kunnet «dekke» en kundefaktura, og summene ville
-- gått opp i et regnskap som var galt.
--
-- KRYSSREFERANSEN mellom post og bilag over tenantgrensen er allerede
-- umulig via de sammensatte fremmednøklene. Vakten måler det som
-- nøklene ikke kan: at de to sidene faktisk finnes, og at forholdet
-- mellom dem er meningsfullt.
CREATE FUNCTION m13_avstemming_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_post RECORD; v_bilag RECORD; v_aktor TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'avstemming: DELETE avvist — en feilaktig match'
            ' oppheves med begrunnelse, den slettes aldri som rad'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.tenant IS DISTINCT FROM OLD.tenant
           OR NEW.avstemming_id IS DISTINCT FROM OLD.avstemming_id
           OR NEW.post_id IS DISTINCT FROM OLD.post_id
           OR NEW.bilag_id IS DISTINCT FROM OLD.bilag_id
           OR NEW.metode IS DISTINCT FROM OLD.metode
           OR NEW.avvik_ore IS DISTINCT FROM OLD.avvik_ore
           OR NEW.begrunnelse IS DISTINCT FROM OLD.begrunnelse
           OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
           OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av THEN
            RAISE EXCEPTION 'avstemming: alt unntatt opphevingen er'
                ' frosset — en match som kunne redigeres er ingen match'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- OPPHEVING GÅR ÉN VEI. En rad som kunne gjenåpnes ville gjort
        -- den partielle unike indeksen til en regel med hull: posten
        -- kunne fått en ny match mens den gamle lå og ventet på å bli
        -- levende igjen.
        IF OLD.opphevet_ts IS NOT NULL THEN
            RAISE EXCEPTION 'avstemming: en opphevet match gjenåpnes ikke'
                ' — en ny vurdering er en ny rad'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.opphevet_ts IS NULL THEN
            RAISE EXCEPTION 'avstemming: den eneste tillatte endringen er'
                ' oppheving' USING ERRCODE = 'insufficient_privilege';
        END IF;
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL OR NEW.opphevet_av IS DISTINCT FROM v_aktor THEN
            RAISE EXCEPTION 'avstemming: opphevet_av (%) er ikke aktøren'
                ' som feller beslutningen (%) — en oppheving uten navn er'
                ' ingen beslutning',
                coalesce(NEW.opphevet_av, '<null>'),
                coalesce(v_aktor, '<ingen>')
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    -- INSERT: de to sidene, og forholdet mellom dem.
    SELECT * INTO v_post FROM public.bankpost p
     WHERE p.tenant = NEW.tenant AND p.post_id = NEW.post_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'avstemming: bankposten finnes ikke — en match'
            ' uten begge sider er ingen match'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    SELECT * INTO v_bilag FROM public.bilag b
     WHERE b.tenant = NEW.tenant AND b.bilag_id = NEW.bilag_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'avstemming: bilaget finnes ikke — en match uten'
            ' begge sider er ingen match'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF (v_post.belop_ore > 0) <> (v_bilag.retning = 'inn') THEN
        RAISE EXCEPTION 'avstemming: fortegnet på bankposten (%) svarer'
            ' ikke til bilagets retning (%) — en utbetaling dekker ikke'
            ' en kundefaktura', v_post.belop_ore, v_bilag.retning
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Avviket er en OBSERVASJON og skal være den observasjonen døren
    -- faktisk gjorde. Vakten regner den samme differansen og nekter en
    -- rad som påstår noe annet — ellers kunne et avvik skrives fritt, og
    -- da måler ingenting.
    IF NEW.avvik_ore
       IS DISTINCT FROM (abs(v_post.belop_ore) - v_bilag.belop_ore) THEN
        RAISE EXCEPTION 'avstemming: avvik_ore (%) er ikke differansen'
            ' mellom postens beløp og bilagets (%) — et avvik man kan'
            ' skrive fritt måler ingenting',
            NEW.avvik_ore, abs(v_post.belop_ore) - v_bilag.belop_ore
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NEW.opphevet_ts IS NOT NULL THEN
        RAISE EXCEPTION 'avstemming: en match kan ikke fødes opphevet'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m13_avstemming_vakt() FROM PUBLIC;
CREATE TRIGGER m13_avstemming_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON avstemming
    FOR EACH ROW EXECUTE FUNCTION m13_avstemming_vakt();
CREATE TRIGGER m13_avstemming_ingen_truncate
    BEFORE TRUNCATE ON avstemming
    EXECUTE FUNCTION m13_avstemming_vakt();

-- Funnene: samme form som 100s `m34_funn_vakt`. Sveipen eier dem, og et
-- funn som kunne slettes ville gjort «vi hadde ingen funn» til noe man
-- kan produsere.
CREATE FUNCTION m13_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'avstemmingsfunn: DELETE avvist — et funn lukkes,'
            ' det slettes ikke'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.tenant IS DISTINCT FROM OLD.tenant
           OR NEW.objekttype IS DISTINCT FROM OLD.objekttype
           OR NEW.objekt_id IS DISTINCT FROM OLD.objekt_id
           OR NEW.funntype IS DISTINCT FROM OLD.funntype
           OR NEW.forst_sett IS DISTINCT FROM OLD.forst_sett THEN
            RAISE EXCEPTION 'avstemmingsfunn: identiteten og førstegangen'
                ' er frosset — når vi FØRST så noe er hele poenget'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m13_funn_vakt() FROM PUBLIC;
CREATE TRIGGER m13_funn_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON avstemmingsfunn
    FOR EACH ROW EXECUTE FUNCTION m13_funn_vakt();
CREATE TRIGGER m13_funn_ingen_truncate
    BEFORE TRUNCATE ON avstemmingsfunn
    EXECUTE FUNCTION m13_funn_vakt();


-- ------------------------------------------------------------
-- 2b. RLS og rettigheter.
-- ------------------------------------------------------------
ALTER TABLE bankkonto ENABLE ROW LEVEL SECURITY;
ALTER TABLE bankkonto FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON bankkonto
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE bankpost ENABLE ROW LEVEL SECURITY;
ALTER TABLE bankpost FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON bankpost
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — ingen BYPASSRLS.
--
-- Sveipen må finne HVILKE tenanter som har bankposter, og det spørsmålet
-- kan ikke stilles innenfra én tenantkontekst. Autoriteten er derfor en
-- policy, ikke en rolleegenskap, og den er gjerdet tre ganger:
--
--   * bare `disponit_avstemming_eier` (dørenes eier — ingen LOGIN-rolle),
--   * bare SELECT (sveipen SKRIVER aldri kryss-tenant: hvert funn
--     skrives etter at konteksten er bundet til RADENS tenant),
--   * bare når det IKKE står en tenantkontekst i sesjonen.
--
-- Det siste leddet er det bærende. Dørene i §3 kommer alltid gjennom
-- `krev_tenantkontekst`, som fail-closed krever en ikke-tom kontekst —
-- inne i en dør er denne policyen derfor ALLTID usann, og
-- `tenant_isolasjon` er den eneste som gjelder. De to er disjunkte per
-- konstruksjon (100s form, ordrett).
CREATE POLICY m13_sveip_tenantliste ON bankpost
    FOR SELECT TO disponit_avstemming_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

ALTER TABLE bilag ENABLE ROW LEVEL SECURITY;
ALTER TABLE bilag FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON bilag
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- SAMME SNEVRE KRYSS-TENANT-VINDU som på `bankpost`, og av samme grunn:
-- sveipens tenantliste er UNIONEN av de to sidene. En tenant som har
-- bilag men ingen bankposter ville ellers aldri blitt sveipet — og det
-- er nettopp den tenanten det forfalte, udekkede bilaget finnes hos.
-- Tre gjerder som over: bare dørenes eier, bare SELECT, bare uten
-- tenantkontekst i sesjonen.
CREATE POLICY m13_sveip_bilagsliste ON bilag
    FOR SELECT TO disponit_avstemming_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

ALTER TABLE avstemming ENABLE ROW LEVEL SECURITY;
ALTER TABLE avstemming FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON avstemming
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE avstemmingsfunn ENABLE ROW LEVEL SECURITY;
ALTER TABLE avstemmingsfunn FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON avstemmingsfunn
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
-- INGEN kryss-tenant-policy på funnene, og det er ikke en forglemmelse:
-- sveipen finner tenantene i `bankpost` (uten kontekst) og gjør ALT
-- funnarbeidet med RADENS tenant satt. Skrivingen er dermed tenantbundet
-- av RLS, også for sveipen selv.

-- Rettighetene dørenes eier trenger, og ikke mer. Merk hva som IKKE står
-- her: ingen runtime-rolle får en eneste tabellrettighet på de fem
-- tabellene (SP-7, 090/091/095/096/100-formen) — hele registeret nås KUN
-- gjennom dørene i §3, og de krever tenantkontekst først.
GRANT SELECT, INSERT ON bankkonto TO disponit_avstemming_eier;
GRANT SELECT, INSERT ON bankpost TO disponit_avstemming_eier;
GRANT SELECT, INSERT ON bilag TO disponit_avstemming_eier;
GRANT SELECT, INSERT, UPDATE ON avstemming TO disponit_avstemming_eier;
GRANT SELECT, INSERT, UPDATE ON avstemmingsfunn TO disponit_avstemming_eier;

-- Evidenskjeden. Én skrivevei, som alle andre modulers.
GRANT INSERT ON revisjonslogg TO disponit_avstemming_eier;

-- Kontekstporten eies av `disponit_m37_claimer` og er REVOKEd fra PUBLIC
-- (038). Dørene under er SECURITY DEFINER og løper som
-- `disponit_avstemming_eier` — uten dette grantet ville SP-1-porten
-- feilet med «permission denied», og registeret vært nede i stedet for
-- sikret. Grantet gis av eieren selv (039-formen).
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_avstemming_eier;
RESET ROLE;


-- ------------------------------------------------------------
-- 3. Dørene. SECURITY DEFINER, eid av `disponit_avstemming_eier`, og
--    hver tenantbundet dør kaller `krev_tenantkontekst` FØRST (SP-1).
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_avstemming_eier;

-- Evidenskjeden, ett sted. Kalles av hver skrivedør, i dens egen
-- transaksjon.
--
-- ÆRLIG OM FORMEN (096/100s begrunnelse, ordrett): `revisjonslogg` har
-- ingen ciphertext-kolonner, så `payload_type='kryptert'` med
-- `referansepayload IS NULL` er den ordinære formen HVER eksisterende
-- skriver bruker — ikke en påstand om at det finnes en kryptert payload.
--
-- `input_hash` er sha256 over den kanoniske beskrivelsen av HANDLINGEN.
-- BELØP, KONTONUMMER, MOTPART OG BILAGSTEKST STÅR ALDRI HER. Det er
-- kundens forretningsdata, og evidenskjeden skal kunne gjenfinne
-- handlingen uten å arkivere pengestrømmen på nytt et sted til.
CREATE FUNCTION m13_evidens(p_tenant TEXT, p_objekt_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm13_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm13_avstemming', 'handling', p_handling,
        'objekt_id', p_objekt_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm13_avstemming',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:avstemmingsregister', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m13_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- KONTODØREN. Tar imot det FULLE kontonummeret og glemmer det.
--
-- Normaliseringen (alt som ikke er siffer fjernes) gjør at «1234 56
-- 78903» og «12345678903» er den samme kontoen — ellers ville et
-- mellomrom skapt en ny konto, og posten havnet under feil hode.
-- Minstelengden er 8 fordi et firesifret «kontonummer» ville gjort halen
-- lik hele nummeret, og da lagres det man skulle latt være.
CREATE FUNCTION m13_registrer_konto(
    p_tenant TEXT, p_konto_id UUID, p_navn TEXT, p_kontonummer TEXT,
    p_valuta TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_siffer TEXT; v_hash TEXT; v_hale TEXT; v_valuta TEXT;
        v_rader INT; v_gammel RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm13_registrer_konto');
    IF p_navn IS NULL OR p_navn !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm13_registrer_konto: kontoen må ha et navn — en'
            ' konto ingen kan peke på i en liste er ingen konto'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_siffer := regexp_replace(coalesce(p_kontonummer, ''),
                               '[^0-9]', '', 'g');
    IF length(v_siffer) < 8 THEN
        RAISE EXCEPTION 'm13_registrer_konto: kontonummeret må ha minst'
            ' åtte siffer — kortere ville gjort de fire lagrede sifrene'
            ' til hele nummeret'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_valuta := upper(btrim(coalesce(p_valuta, '')));
    IF v_valuta !~ '^[A-Z]{3}$' THEN
        RAISE EXCEPTION 'm13_registrer_konto: valuta må være en'
            ' tre-bokstavs ISO 4217-kode'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_hash := encode(sha256(convert_to(v_siffer, 'UTF8')), 'hex');
    v_hale := right(v_siffer, 4);
    INSERT INTO public.bankkonto
        (tenant, konto_id, navn, kontonummer_hale, kontonummer_hash,
         valuta, opprettet_av)
    VALUES (p_tenant, p_konto_id, btrim(p_navn), v_hale, v_hash,
            v_valuta, p_aktor)
        ON CONFLICT (tenant, konto_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        -- SP-2-materialitet (100-formen): samme id igjen med identisk
        -- innhold er et STILLE JA; annet innhold er en materiell
        -- konflikt kalleren SKAL se.
        SELECT * INTO v_gammel FROM public.bankkonto
         WHERE tenant = p_tenant AND konto_id = p_konto_id;
        IF v_gammel.navn IS DISTINCT FROM btrim(p_navn)
           OR v_gammel.kontonummer_hash IS DISTINCT FROM v_hash
           OR v_gammel.valuta IS DISTINCT FROM v_valuta THEN
            RAISE EXCEPTION 'm13_registrer_konto: samme konto_id med annet'
                ' innhold — materiell konflikt'
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN false;
    END IF;
    PERFORM public.m13_evidens(
        p_tenant, p_konto_id, 'konto.registrert', p_aktor,
        jsonb_build_object('valuta', v_valuta));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m13_registrer_konto(TEXT, UUID, TEXT, TEXT, TEXT,
                                           TEXT) FROM PUBLIC;

-- BANKPOSTDØREN. Importidempotensen ligger i den unike indeksen på
-- `ekstern_ref`, ikke i en tidsvindusheuristikk — samme kontoutskrift
-- lastet to ganger gir de samme radene.
--
-- MERK AT DENNE DØREN IKKE HAR SP-2-KONFLIKT PÅ INNHOLD. Grunnen er at
-- `ekstern_ref` er BANKENS identitet for bevegelsen: dukker den samme
-- referansen opp med et annet beløp, er det ikke et gjenspill, det er en
-- kilde som motsier seg selv. Da skal registeret si nei og ikke velge en
-- av dem.
CREATE FUNCTION m13_registrer_post(
    p_tenant TEXT, p_post_id UUID, p_konto_id UUID, p_ekstern_ref TEXT,
    p_bokfort DATE, p_belop_ore BIGINT, p_tekst TEXT, p_motpart TEXT,
    p_aktor TEXT)
-- DØREN RETURNERER BEGGE DELER, og det er ikke pynt.
--
-- Denne døren har TO idempotenser: kallerens Idempotency-Key (som id-en
-- utledes av) og BANKENS EGEN referanse (den unike indeksen). Er posten
-- alt registrert under en ANNEN nøkkel — samme kontoutskrift, andre
-- fanen, i går — så er den lagrede raden en annen enn den kalleren
-- utledet. En dør som bare svarte `false` ville latt flaten sitte igjen
-- med en id ingen rad har, og neste kall som brukte den (en match, en
-- oppslag) ville fått «finnes ikke» om noe som beviselig finnes.
--
-- De tre andre registreringsdørene har ikke dette problemet: deres
-- naturlige nøkler (`kontonummer_hash`, `bilagsnummer`) er UNIKE, så en
-- kollisjon der blir en `unique_violation` kalleren ser — ikke et stille
-- ja med feil id.
-- OUT-KOLONNEN HETER `lagret_post_id` OG IKKE `post_id`, og det er ikke
-- smak: en OUT-parameter med samme navn som en kolonne skygger kolonnen
-- inne i funksjonskroppen, og `INSERT INTO bankpost (... post_id ...)`
-- blir tvetydig. Navnet sier dessuten det som er poenget — id-en til
-- raden som er LAGRET, ikke den kalleren utledet.
RETURNS TABLE(ny BOOLEAN, lagret_post_id UUID)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_gammel RECORD; v_ref TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm13_registrer_post');
    IF coalesce(p_belop_ore, 0) = 0 THEN
        RAISE EXCEPTION 'm13_registrer_post: en bevegelse på null er'
            ' ingen bevegelse'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_tekst IS NULL OR p_tekst !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm13_registrer_post: posteringsteksten kan ikke'
            ' være tom — den er det eneste som lar et menneske kjenne'
            ' igjen bevegelsen'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_ref := btrim(coalesce(p_ekstern_ref, ''));
    IF v_ref = '' THEN
        RAISE EXCEPTION 'm13_registrer_post: bankens referanse kan ikke'
            ' være tom — den ER importidempotensen'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.bankkonto k
                    WHERE k.tenant = p_tenant AND k.konto_id = p_konto_id) THEN
        RAISE EXCEPTION 'm13_registrer_post: kontoen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    -- SAMME BANKREFERANSE IGJEN: samme bevegelse, eller en kilde som
    -- motsier seg selv. Sjekken står FØR innsettingen fordi den unike
    -- indeksen ellers ville gitt en `unique_violation` uten å si hvilken
    -- av de to tilfellene det var.
    SELECT * INTO v_gammel FROM public.bankpost p
     WHERE p.tenant = p_tenant AND p.konto_id = p_konto_id
       AND p.ekstern_ref = v_ref;
    IF FOUND THEN
        IF v_gammel.belop_ore IS DISTINCT FROM p_belop_ore
           OR v_gammel.bokfort IS DISTINCT FROM p_bokfort THEN
            RAISE EXCEPTION 'm13_registrer_post: bankreferansen % finnes'
                ' med et annet beløp eller en annen dato — kilden'
                ' motsier seg selv, og registeret velger ikke', v_ref
                USING ERRCODE = 'unique_violation';
        END IF;
        -- SAMME BEVEGELSE, ALT REGISTRERT. Kalleren får id-en til raden
        -- som FAKTISK står der, ikke den den utledet av sin egen nøkkel.
        ny := false; lagret_post_id := v_gammel.post_id;
        RETURN NEXT;
        RETURN;
    END IF;
    INSERT INTO public.bankpost
        (tenant, post_id, konto_id, ekstern_ref, bokfort, belop_ore,
         tekst, motpart, opprettet_av)
    VALUES (p_tenant, p_post_id, p_konto_id, v_ref, p_bokfort,
            p_belop_ore, p_tekst,
            nullif(btrim(coalesce(p_motpart, '')), ''), p_aktor)
        ON CONFLICT (tenant, post_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        -- Samme post_id igjen (kallerens egen nøkkel gjenspilt). Raden
        -- er dens egen, så id-en er riktig — men den er ikke NY.
        ny := false; lagret_post_id := p_post_id;
        RETURN NEXT;
        RETURN;
    END IF;
    PERFORM public.m13_evidens(
        p_tenant, p_post_id, 'bankpost.registrert', p_aktor,
        jsonb_build_object('konto_id', p_konto_id::text));
    ny := true; lagret_post_id := p_post_id;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m13_registrer_post(TEXT, UUID, UUID, TEXT, DATE,
                                          BIGINT, TEXT, TEXT, TEXT)
    FROM PUBLIC;

-- BILAGSDØREN.
CREATE FUNCTION m13_registrer_bilag(
    p_tenant TEXT, p_bilag_id UUID, p_bilagsnummer TEXT, p_retning TEXT,
    p_belop_ore BIGINT, p_motpart TEXT, p_utstedt DATE, p_forfall DATE,
    p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_gammel RECORD; v_nr TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm13_registrer_bilag');
    IF p_retning IS NULL OR p_retning NOT IN ('inn', 'ut') THEN
        RAISE EXCEPTION 'm13_registrer_bilag: retning må være inn eller ut'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF coalesce(p_belop_ore, 0) <= 0 THEN
        RAISE EXCEPTION 'm13_registrer_bilag: beløpet er alltid positivt'
            ' — retningen bærer fortegnet, ikke tallet'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_nr := btrim(coalesce(p_bilagsnummer, ''));
    IF v_nr = '' THEN
        RAISE EXCEPTION 'm13_registrer_bilag: bilagsnummeret kan ikke'
            ' være tomt' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_motpart IS NULL OR p_motpart !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm13_registrer_bilag: motparten kan ikke være tom'
            ' — et bilag uten motpart kan ingen avstemme mot noe'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_forfall IS NOT NULL AND p_forfall < p_utstedt THEN
        RAISE EXCEPTION 'm13_registrer_bilag: forfall før utstedelse er'
            ' ingen frist' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.bilag
        (tenant, bilag_id, bilagsnummer, retning, belop_ore, motpart,
         utstedt, forfall, opprettet_av)
    VALUES (p_tenant, p_bilag_id, v_nr, p_retning, p_belop_ore,
            btrim(p_motpart), p_utstedt, p_forfall, p_aktor)
        ON CONFLICT (tenant, bilag_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        SELECT * INTO v_gammel FROM public.bilag
         WHERE tenant = p_tenant AND bilag_id = p_bilag_id;
        IF v_gammel.bilagsnummer IS DISTINCT FROM v_nr
           OR v_gammel.retning IS DISTINCT FROM p_retning
           OR v_gammel.belop_ore IS DISTINCT FROM p_belop_ore
           OR v_gammel.motpart IS DISTINCT FROM btrim(p_motpart)
           OR v_gammel.utstedt IS DISTINCT FROM p_utstedt
           OR v_gammel.forfall IS DISTINCT FROM p_forfall THEN
            RAISE EXCEPTION 'm13_registrer_bilag: samme bilag_id med annet'
                ' innhold — materiell konflikt'
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN false;
    END IF;
    PERFORM public.m13_evidens(
        p_tenant, p_bilag_id, 'bilag.registrert', p_aktor,
        jsonb_build_object('retning', p_retning));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m13_registrer_bilag(TEXT, UUID, TEXT, TEXT, BIGINT,
                                           TEXT, DATE, DATE, TEXT)
    FROM PUBLIC;

-- MATCHDØREN. Her felles selve avstemmingen.
--
-- OVERDEKNING AVVISES. Summen av matchede poster kan ikke overstige
-- bilagets beløp: to innbetalinger på 1000 mot én faktura på 1000 er
-- ikke en delbetaling, det er en dobbeltmatch med et annet ansikt.
-- Regelen står HER og ikke i en CHECK, fordi den gjelder et AGGREGAT
-- over flere rader — og en CHECK ser bare sin egen.
--
-- `metode` er kallerens påstand om HVORDAN matchen ble til, og
-- `automatisk` er ikke en fullmakt: v1 har ingen regelmotor, så verdien
-- finnes for at en senere motor skal kunne skilles fra et menneske i
-- ettertid. En manuell match krever begrunnelse (CHECK-en i §1).
CREATE FUNCTION m13_avstem(
    p_tenant TEXT, p_avstemming_id UUID, p_post_id UUID, p_bilag_id UUID,
    p_metode TEXT, p_begrunnelse TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_post RECORD; v_bilag RECORD; v_dekket BIGINT; v_avvik BIGINT;
        v_rader INT; v_gammel RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm13_avstem');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_metode IS NULL OR p_metode NOT IN ('automatisk', 'manuell') THEN
        RAISE EXCEPTION 'm13_avstem: metode må være automatisk eller'
            ' manuell' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO v_post FROM public.bankpost p
     WHERE p.tenant = p_tenant AND p.post_id = p_post_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm13_avstem: bankposten finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    SELECT * INTO v_bilag FROM public.bilag b
     WHERE b.tenant = p_tenant AND b.bilag_id = p_bilag_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm13_avstem: bilaget finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    -- POSTEN KAN VÆRE MATCHET FRA FØR. Sjekken står her for å kunne si
    -- HVA som er galt; den partielle unike indeksen er det som gjør det
    -- sant også for enhver annen skrivevei.
    IF EXISTS (SELECT 1 FROM public.avstemming a
                WHERE a.tenant = p_tenant AND a.post_id = p_post_id
                  AND a.opphevet_ts IS NULL
                  AND a.avstemming_id <> p_avstemming_id) THEN
        RAISE EXCEPTION 'm13_avstem: bankposten er alt avstemt — opphev'
            ' den gamle matchen først'
            USING ERRCODE = 'unique_violation';
    END IF;
    -- FORTEGNET FØRST. Vakten i §2 feller den samme dommen ved INSERT
    -- og er det som gjør den sann for enhver skrivevei — men da har
    -- overdekningssjekken alt kjørt, og en utbetaling mot en
    -- kundefaktura ville fått «overdekning» som forklaring på noe som
    -- er en helt annen feil. Rekkefølgen er dermed ikke pynt: den
    -- avgjør hvilken setning brukeren leser.
    IF (v_post.belop_ore > 0) <> (v_bilag.retning = 'inn') THEN
        RAISE EXCEPTION 'm13_avstem: bankposten er % og bilaget har'
            ' retning % — en utbetaling dekker ikke en kundefaktura',
            CASE WHEN v_post.belop_ore > 0 THEN 'inngående'
                 ELSE 'utgående' END, v_bilag.retning
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- OVERDEKNINGEN, OG LÅSEN SOM GJØR SJEKKEN SANN UNDER SAMTIDIGHET.
    -- To delbetalinger mot det samme bilaget i to transaksjoner ville
    -- hver for seg lest en dekning som lot dem begge passere, og til
    -- sammen oversteget beløpet.
    --
    -- LÅSEN ER RÅDGIVENDE OG IKKE `FOR UPDATE`, og det er et valg:
    -- `SELECT ... FOR UPDATE` krever UPDATE-rettighet på `bilag`, og
    -- bilagsraden er frosset med vilje (vakten i §2). Å gi dørenes eier
    -- skriverett på en tabell ingen dør skal skrive i, for å kunne LESE
    -- den trygt, ville byttet en samtidighetsgaranti mot en utvidet
    -- fullmakt. En rådgivende lås sier nøyaktig det den gjør —
    -- serialiser disse to kallene — og koster ingen rettighet.
    -- Første nøkkel navnerommer låsen til modulen; låsen faller ved
    -- transaksjonsslutt.
    PERFORM pg_advisory_xact_lock(
        hashtext('m13_avstem_bilag'),
        hashtext(p_tenant || '/' || p_bilag_id::text));
    SELECT coalesce(sum(abs(p.belop_ore)), 0)::bigint INTO v_dekket
      FROM public.avstemming a
      JOIN public.bankpost p
        ON p.tenant = a.tenant AND p.post_id = a.post_id
     WHERE a.tenant = p_tenant AND a.bilag_id = p_bilag_id
       AND a.opphevet_ts IS NULL AND a.avstemming_id <> p_avstemming_id;
    IF v_dekket + abs(v_post.belop_ore) > v_bilag.belop_ore THEN
        RAISE EXCEPTION 'm13_avstem: matchen ville dekket % av et bilag'
            ' på % — overdekning er dobbeltmatch med et annet ansikt',
            v_dekket + abs(v_post.belop_ore), v_bilag.belop_ore
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Avviket er postens beløp mot bilagets HELE beløp — ikke mot
    -- restbeløpet. Det er det tallet den som matchet faktisk så, og
    -- vakten regner den samme differansen.
    v_avvik := abs(v_post.belop_ore) - v_bilag.belop_ore;
    INSERT INTO public.avstemming
        (tenant, avstemming_id, post_id, bilag_id, metode, avvik_ore,
         begrunnelse, opprettet_av)
    VALUES (p_tenant, p_avstemming_id, p_post_id, p_bilag_id, p_metode,
            v_avvik, nullif(btrim(coalesce(p_begrunnelse, '')), ''),
            p_aktor)
        ON CONFLICT (tenant, avstemming_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        SELECT * INTO v_gammel FROM public.avstemming
         WHERE tenant = p_tenant AND avstemming_id = p_avstemming_id;
        IF v_gammel.post_id IS DISTINCT FROM p_post_id
           OR v_gammel.bilag_id IS DISTINCT FROM p_bilag_id
           OR v_gammel.metode IS DISTINCT FROM p_metode THEN
            RAISE EXCEPTION 'm13_avstem: samme avstemming_id med annet'
                ' innhold — materiell konflikt'
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN false;
    END IF;
    PERFORM public.m13_evidens(
        p_tenant, p_avstemming_id, 'avstemming.opprettet', p_aktor,
        jsonb_build_object('metode', p_metode,
                           'post_id', p_post_id::text,
                           'bilag_id', p_bilag_id::text));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m13_avstem(TEXT, UUID, UUID, UUID, TEXT, TEXT,
                                  TEXT) FROM PUBLIC;

-- OPPHEVINGSDØREN. Den ene tillatte endringen på en matchrad.
CREATE FUNCTION m13_opphev_avstemming(
    p_tenant TEXT, p_avstemming_id UUID, p_begrunnelse TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_gammel RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm13_opphev_avstemming');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_begrunnelse IS NULL OR p_begrunnelse !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm13_opphev_avstemming: en oppheving uten'
            ' begrunnelse er en endring ingen kan etterprøve'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO v_gammel FROM public.avstemming
     WHERE tenant = p_tenant AND avstemming_id = p_avstemming_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm13_opphev_avstemming: matchen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    -- ALT OPPHEVET ER ET STILLE JA (false), ikke en feil: to klikk på
    -- den samme knappen skal ikke gi en feilmelding om noe som alt er
    -- slik brukeren ville ha det.
    IF v_gammel.opphevet_ts IS NOT NULL THEN
        RETURN false;
    END IF;
    UPDATE public.avstemming
       SET opphevet_ts = now(), opphevet_av = p_aktor,
           opphevet_begrunnelse = btrim(p_begrunnelse)
     WHERE tenant = p_tenant AND avstemming_id = p_avstemming_id
       AND opphevet_ts IS NULL;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;
    END IF;
    PERFORM public.m13_evidens(
        p_tenant, p_avstemming_id, 'avstemming.opphevet', p_aktor,
        jsonb_build_object('post_id', v_gammel.post_id::text,
                           'bilag_id', v_gammel.bilag_id::text));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m13_opphev_avstemming(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

-- ------------------------------------------------------------
-- 3b. Lesedørene. TO lister og ETT sammendrag, ikke én blandet tabell:
--     en uavstemt bankpost og et underdekket bilag er to forskjellige
--     spørsmål, og en felles rad ville hatt halve kolonnene tomme
--     uansett hvilken side den kom fra.
-- ------------------------------------------------------------

-- SAMMENDRAGET TELLER ALT, listene viser de N verste. Det skillet er
-- ikke pynt: en flate som regnet totalen fra den avkortede listen ville
-- sagt «tre uavstemte poster» når det var tre hundre, og tallet ville
-- vært mest galt nettopp den dagen det betydde mest.
CREATE FUNCTION m13_avstemmingsstatus(p_tenant TEXT)
RETURNS TABLE(poster_totalt INT, poster_uavstemt INT,
              uavstemt_ore BIGINT, bilag_apne INT, rest_ore BIGINT,
              apne_funn INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm13_avstemmingsstatus');
    SELECT count(*)::int,
           count(*) FILTER (WHERE NOT EXISTS (
               SELECT 1 FROM public.avstemming a
                WHERE a.tenant = p.tenant AND a.post_id = p.post_id
                  AND a.opphevet_ts IS NULL))::int,
           coalesce(sum(abs(p.belop_ore)) FILTER (WHERE NOT EXISTS (
               SELECT 1 FROM public.avstemming a
                WHERE a.tenant = p.tenant AND a.post_id = p.post_id
                  AND a.opphevet_ts IS NULL)), 0)::bigint
      INTO poster_totalt, poster_uavstemt, uavstemt_ore
      FROM public.bankpost p WHERE p.tenant = p_tenant;
    SELECT count(*)::int, coalesce(sum(b.belop_ore - d.dekket), 0)
      INTO bilag_apne, rest_ore
      FROM public.bilag b
      CROSS JOIN LATERAL (
            SELECT coalesce(sum(abs(p.belop_ore)), 0)::bigint AS dekket
              FROM public.avstemming a
              JOIN public.bankpost p
                ON p.tenant = a.tenant AND p.post_id = a.post_id
             WHERE a.tenant = b.tenant AND a.bilag_id = b.bilag_id
               AND a.opphevet_ts IS NULL) d
     WHERE b.tenant = p_tenant AND d.dekket < b.belop_ore;
    SELECT count(*)::int INTO apne_funn
      FROM public.avstemmingsfunn f
     WHERE f.tenant = p_tenant AND f.apen;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m13_avstemmingsstatus(TEXT) FROM PUBLIC;

-- KONTOENE. En egen dør, og det er ikke overflødig ved siden av de to
-- listene: en nyopprettet konto har ingen poster ennå, så en flate som
-- utledet kontolisten fra postene ville hatt en tom nedtrekk nøyaktig
-- den gangen brukeren skulle registrere sin første bankpost. Døren gir
-- ALDRI kontonummeret — det finnes ikke i basen (§1), bare halen.
CREATE FUNCTION m13_kontoer(p_tenant TEXT)
RETURNS TABLE(konto_id UUID, navn TEXT, kontonummer_hale TEXT,
              valuta TEXT, aktiv BOOLEAN, poster INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm13_kontoer');
    RETURN QUERY
    SELECT k.konto_id, k.navn, k.kontonummer_hale, k.valuta, k.aktiv,
           (SELECT count(*)::int FROM public.bankpost p
             WHERE p.tenant = k.tenant AND p.konto_id = k.konto_id)
      FROM public.bankkonto k
     WHERE k.tenant = p_tenant
     ORDER BY k.navn, k.konto_id;
END $$;
REVOKE ALL ON FUNCTION m13_kontoer(TEXT) FROM PUBLIC;

-- De uavstemte bankpostene, eldst først. «Uavstemt» er ikke en kolonne
-- (dom 4) — det er fraværet av en ikke-opphevet matchrad, og spørringen
-- stiller nettopp det spørsmålet.
CREATE FUNCTION m13_uavstemte_poster(p_tenant TEXT, p_grense INT)
RETURNS TABLE(post_id UUID, konto_navn TEXT, konto_hale TEXT,
              valuta TEXT, ekstern_ref TEXT, bokfort DATE,
              belop_ore BIGINT, tekst TEXT, motpart TEXT,
              alder_dogn INT, apne_funn TEXT[])
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm13_uavstemte_poster');
    RETURN QUERY
    SELECT p.post_id, k.navn, k.kontonummer_hale, k.valuta,
           p.ekstern_ref, p.bokfort, p.belop_ore, p.tekst, p.motpart,
           (current_date - p.bokfort)::int,
           coalesce((SELECT array_agg(f.funntype ORDER BY f.funntype)
                       FROM public.avstemmingsfunn f
                      WHERE f.tenant = p.tenant
                        AND f.objekttype = 'post'
                        AND f.objekt_id = p.post_id AND f.apen),
                    ARRAY[]::TEXT[])
      FROM public.bankpost p
      JOIN public.bankkonto k
        ON k.tenant = p.tenant AND k.konto_id = p.konto_id
     WHERE p.tenant = p_tenant
       AND NOT EXISTS (SELECT 1 FROM public.avstemming a
                        WHERE a.tenant = p.tenant
                          AND a.post_id = p.post_id
                          AND a.opphevet_ts IS NULL)
     -- Eldst først, og `post_id` som tiebreaker: uten den ville to
     -- poster fra samme dag byttet plass mellom to kall, og en avkortet
     -- liste vist ulikt innhold på samme data (100s bitmap-lærdom).
     ORDER BY p.bokfort, p.post_id
     LIMIT greatest(least(coalesce(p_grense, 100), 1000), 1);
END $$;
REVOKE ALL ON FUNCTION m13_uavstemte_poster(TEXT, INT) FROM PUBLIC;

-- De åpne bilagene med restbeløp. Forfalte først, og blant dem det som
-- har stått lengst.
CREATE FUNCTION m13_apne_bilag(p_tenant TEXT, p_grense INT)
RETURNS TABLE(bilag_id UUID, bilagsnummer TEXT, retning TEXT,
              belop_ore BIGINT, dekket_ore BIGINT, rest_ore BIGINT,
              motpart TEXT, utstedt DATE, forfall DATE,
              dogn_over_forfall INT, apne_funn TEXT[])
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm13_apne_bilag');
    RETURN QUERY
    SELECT b.bilag_id, b.bilagsnummer, b.retning, b.belop_ore, d.dekket,
           b.belop_ore - d.dekket, b.motpart, b.utstedt, b.forfall,
           CASE WHEN b.forfall IS NULL THEN NULL
                ELSE (current_date - b.forfall)::int END,
           coalesce((SELECT array_agg(f.funntype ORDER BY f.funntype)
                       FROM public.avstemmingsfunn f
                      WHERE f.tenant = b.tenant
                        AND f.objekttype = 'bilag'
                        AND f.objekt_id = b.bilag_id AND f.apen),
                    ARRAY[]::TEXT[])
      FROM public.bilag b
      CROSS JOIN LATERAL (
            SELECT coalesce(sum(abs(p.belop_ore)), 0)::bigint AS dekket
              FROM public.avstemming a
              JOIN public.bankpost p
                ON p.tenant = a.tenant AND p.post_id = a.post_id
             WHERE a.tenant = b.tenant AND a.bilag_id = b.bilag_id
               AND a.opphevet_ts IS NULL) d
     WHERE b.tenant = p_tenant AND d.dekket < b.belop_ore
     -- Et bilag UTEN forfall er ikke forbigått, bare åpent. `NULLS LAST`
     -- setter det etter alt som har en frist, i stedet for foran alt
     -- (PostgreSQLs standard for ASC).
     ORDER BY b.forfall NULLS LAST, b.utstedt, b.bilag_id
     LIMIT greatest(least(coalesce(p_grense, 100), 1000), 1);
END $$;
REVOKE ALL ON FUNCTION m13_apne_bilag(TEXT, INT) FROM PUBLIC;

-- ------------------------------------------------------------
-- 4. Sveipen. Kandidatene først, som en EGEN funksjon (100-formen):
--    sveipen kaller den tre ganger — for ferskhet, for nye og for
--    lukking — og de tre må se NØYAKTIG det samme settet. Én
--    definisjon, ett sted.
-- ------------------------------------------------------------

-- `p_dogn_grense` er alderen en uavstemt bankpost må passere før den
-- blir et funn. Den er en PARAMETER med et forsvarlig standardsvar og
-- ikke en konstant i kroppen: tretti døgn er en måned kontoutskrift, og
-- en tenant som avstemmer ukentlig vil ha noe annet. At tallet kommer
-- utenfra er det som gjør den senere policyverdien til en endring i ett
-- kall, ikke i en migrasjon.
CREATE FUNCTION m13_funnkandidater(p_tenant TEXT, p_dag DATE,
                                   p_dogn_grense INT DEFAULT 30)
RETURNS TABLE(objekttype TEXT, objekt_id UUID, funntype TEXT,
              dogn_over_grense INT, rest_ore BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_grense INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm13_funnkandidater');
    v_grense := greatest(coalesce(p_dogn_grense, 30), 1);
    RETURN QUERY
    -- 1. Uavstemte bankposter eldre enn grensen.
    SELECT 'post'::text, p.post_id, 'uavstemt_post_over_grense'::text,
           ((p_dag - p.bokfort) - v_grense)::int, NULL::bigint
      FROM public.bankpost p
     WHERE p.tenant = p_tenant
       AND (p_dag - p.bokfort) > v_grense
       AND NOT EXISTS (SELECT 1 FROM public.avstemming a
                        WHERE a.tenant = p.tenant
                          AND a.post_id = p.post_id
                          AND a.opphevet_ts IS NULL)
    UNION ALL
    -- 2. og 3. Bilag med restbeløp. Forfalt uten EN ENESTE krone dekket
    --    er en annen situasjon enn forfalt med delvis dekning — det
    --    første kan være en faktura ingen har betalt, det andre er nesten
    --    alltid en avstemming som mangler sin siste post. To funntyper,
    --    fordi de fører til to forskjellige handlinger.
    SELECT 'bilag'::text, b.bilag_id,
           CASE WHEN d.dekket = 0 THEN 'forfalt_bilag_uten_dekning'
                ELSE 'delvis_dekket_bilag' END,
           (p_dag - b.forfall)::int, b.belop_ore - d.dekket
      FROM public.bilag b
      CROSS JOIN LATERAL (
            SELECT coalesce(sum(abs(p.belop_ore)), 0)::bigint AS dekket
              FROM public.avstemming a
              JOIN public.bankpost p
                ON p.tenant = a.tenant AND p.post_id = a.post_id
             WHERE a.tenant = b.tenant AND a.bilag_id = b.bilag_id
               AND a.opphevet_ts IS NULL) d
     WHERE b.tenant = p_tenant
       AND b.forfall IS NOT NULL AND b.forfall < p_dag
       AND d.dekket < b.belop_ore;
END $$;
REVOKE ALL ON FUNCTION m13_funnkandidater(TEXT, DATE, INT) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 4b. Sveipen selv. KRYSS-TENANT, og derfor med sin egen LOGIN-rolle,
--     sin egen timer og nøyaktig ÉN rettighet i basen.
--
--     TENANTLISTEN MATERIALISERES FØRST (100-formen): en cursor over
--     `bankpost` mens tenantkonteksten endres under føttene på den ville
--     vært et RLS-predikat som skifter mening midt i en løkke — riktig
--     svar i test, uforutsigbart under last.
--
--     `p_grense` er et tak på HVOR MANGE NYE FUNN én kjøring reiser per
--     tenant. Det begrenser transaksjonen, ikke sannheten: funnene er
--     idempotente, så neste kjøring tar igjen resten. Traff sveipen taket
--     sitt, SIER DEN DET (`avkortet`) — en jobb som ikke kunne måle
--     ferdig rapporterer funn, aldri null.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_avstemming_eier;

CREATE FUNCTION m13_sveip_avstemming(p_grense INT DEFAULT 500,
                                     p_dogn_grense INT DEFAULT 30)
RETURNS TABLE(tenanter INT, nye INT, oppdaterte INT, lukkede INT,
              avkortet INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_n INT; v_grense INT;
        v_dogn INT; v_dag DATE; v_naa TIMESTAMPTZ;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm13_sveip_avstemming: sveipen er KRYSS-TENANT og'
            ' kjøres uten tenantkontekst — en kaller som har satt en'
            ' kontekst ber om noe annet enn det denne funksjonen gjør'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_grense := greatest(least(coalesce(p_grense, 500), 5000), 1);
    v_dogn := greatest(coalesce(p_dogn_grense, 30), 1);
    v_dag := current_date;
    v_naa := now();
    tenanter := 0; nye := 0; oppdaterte := 0; lukkede := 0; avkortet := 0;
    -- TENANTLISTEN ER UNIONEN AV BEGGE SIDENE. En tenant som har
    -- registrert bilag men ennå ingen bankposter ville ellers aldri blitt
    -- sveipet — og det er nettopp den tenanten `forfalt_bilag_uten_dekning`
    -- finnes for: ingen har betalt, og ingen har importert en
    -- kontoutskrift som kunne vist det.
    SELECT array_agg(DISTINCT t ORDER BY t) INTO v_tenanter
      FROM (SELECT p.tenant AS t FROM public.bankpost p
             UNION
            SELECT b.tenant FROM public.bilag b) s;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        tenanter := tenanter + 1;

        -- 1. Ferskheten på funn som ALT finnes. IDEMPOTENSEN BOR HER: en
        --    sveip nummer to på den samme uavstemte posten flytter
        --    `sist_sett_sveip` og skriver ingen ny rad.
        UPDATE public.avstemmingsfunn f
           SET sist_sett_sveip = v_naa, apen = true, lukket_ts = NULL,
               dogn_over_grense = kand.dogn_over_grense,
               rest_ore = kand.rest_ore
          FROM public.m13_funnkandidater(v_t, v_dag, v_dogn) kand
         WHERE f.tenant = v_t AND f.objekttype = kand.objekttype
           AND f.objekt_id = kand.objekt_id
           AND f.funntype = kand.funntype;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        oppdaterte := oppdaterte + v_n;

        -- 2. De nye — med taket. `ORDER BY` gjør avkortingen forutsigbar
        --    og setter det verste øverst: treffer sveipen taket sitt, er
        --    det de eldste postene og de mest forfalte bilagene som HAR
        --    fått funn.
        INSERT INTO public.avstemmingsfunn
            (tenant, objekttype, objekt_id, funntype, dogn_over_grense,
             rest_ore, forst_sett, sist_sett_sveip, apen)
        SELECT v_t, kand.objekttype, kand.objekt_id, kand.funntype,
               kand.dogn_over_grense, kand.rest_ore, v_naa, v_naa, true
          FROM public.m13_funnkandidater(v_t, v_dag, v_dogn) kand
         WHERE NOT EXISTS (
                SELECT 1 FROM public.avstemmingsfunn f
                 WHERE f.tenant = v_t
                   AND f.objekttype = kand.objekttype
                   AND f.objekt_id = kand.objekt_id
                   AND f.funntype = kand.funntype)
         ORDER BY coalesce(kand.dogn_over_grense, 0) DESC,
                  kand.objekttype, kand.objekt_id, kand.funntype
         LIMIT v_grense;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        nye := nye + v_n;
        IF v_n = v_grense THEN
            avkortet := avkortet + 1;
        END IF;

        -- 3. Lukkingen. Et funn som ikke lenger gjelder — posten ble
        --    avstemt, bilaget ble dekket — lukkes. Raden består: at noe
        --    VAR et funn er også historikk.
        UPDATE public.avstemmingsfunn f
           SET apen = false, lukket_ts = v_naa
         WHERE f.tenant = v_t AND f.apen
           AND NOT EXISTS (
                SELECT 1
                  FROM public.m13_funnkandidater(v_t, v_dag, v_dogn) kand
                 WHERE kand.objekttype = f.objekttype
                   AND kand.objekt_id = f.objekt_id
                   AND kand.funntype = f.funntype);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        lukkede := lukkede + v_n;
    END LOOP;
    -- Konteksten legges tilbake der den sto: en sveip skal ikke etterlate
    -- seg en tenant i sesjonen den ikke ble kalt med.
    PERFORM set_config('disponit.tenant', '', true);
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m13_sveip_avstemming(INT, INT) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 4c. Rollemønsteret i basen (043 §6b) speiler `ROLLE_TIL_SCOPES`
--     EKSAKT, og port 26 måler nettopp det. `okonomi:read` er klynge 3s
--     ene nye scope; uten denne raden ville appen og basen sagt to
--     forskjellige ting om hva `admin` faktisk har lov til.
--
--     BARE `admin`. Verken `leser` eller `sikkerhet` får scopet, og det
--     er dommen: kontobevegelser, motparter og beløp er ikke allmenn
--     tilstandsinnsikt, og `security:read` er ops/compliance — ikke
--     økonomi. En tenant som vil skille regnskapsfører fra administrator
--     kan definere en snevrere rolle senere, uten skjemaendring.
-- ------------------------------------------------------------
INSERT INTO rolle_scope (rolle, scope) VALUES
    ('admin', 'okonomi:read')
    ON CONFLICT DO NOTHING;


-- ------------------------------------------------------------
-- 5. Rettighetene. Migrasjonen NAVNGIR IKKE runtime-rollen (057-
--    lærdommen): `deploy/staging/migrer.py` er autoritativ for den
--    konfigurerte rollen. Grantene her er de som gjelder lokalt og i
--    test, der runtime ER hele plattformen, og de faller bort i
--    driftsoppsettet.
--
--    MERK HVA SOM IKKE GRANTES: `m13_sveip_avstemming` og
--    `m13_funnkandidater`. Den første er kryss-tenant og hører
--    sveiperollen til; den andre er et INTERNT ledd i sveipen og en
--    lesedør ingen utenfor trenger. En rettighet som bare ikke blir
--    gitt, er ikke trukket tilbake — derfor REVOKE og ikke bare
--    fravær, i `migrer.py`s rettighetsblokk.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_avstemming_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m13_avstemmingsstatus(TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m13_kontoer(TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m13_uavstemte_poster(TEXT, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m13_apne_bilag(TEXT, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m13_registrer_konto(TEXT, UUID, TEXT, TEXT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m13_registrer_post(TEXT, UUID, UUID, TEXT, DATE, BIGINT,'
            ' TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m13_registrer_bilag(TEXT, UUID, TEXT, TEXT, BIGINT, TEXT,'
            ' DATE, DATE, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m13_avstem(TEXT, UUID, UUID, UUID, TEXT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m13_opphev_avstemming(TEXT, UUID, TEXT, TEXT) TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_avstemmingssveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m13_sveip_avstemming(INT, INT)'
            ' TO disponit_avstemmingssveip';
    END IF;
END $$;
RESET ROLE;
