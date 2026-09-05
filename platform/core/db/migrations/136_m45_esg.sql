-- =====================================================================
-- M-45 BÆREKRAFTS- OG ESG-AGENT (v1) — KLYNGE 9s FJERDE OG SISTE.
-- =====================================================================
--
-- V1-DOMMEN: MODULEN SENDER INGEN RAPPORT. Den samler, regner og
-- stopper der. Innsendingen til en myndighet er et menneskes, og det
-- finnes ingen kolonne for «sendt» i hele modulen.
--
-- ---------------------------------------------------------------------
-- KLYNGENS DELTE DOM, OG DENNE MODULEN ER DENS SKARPESTE TILFELLE:
--
--   EN YTRING AVGITT I HUSETS NAVN KAN IKKE TAS TILBAKE — OG DEN SOM
--   LESER DEN VET IKKE AT EN MASKIN SKREV DEN.
--
-- En bærekraftsrapport leses av investorer, kunder og et tilsyn. Et
-- estimat lest som en måling er grønnvasking, uansett hva som var
-- ment.
--
-- ---------------------------------------------------------------------
-- OG M-45 ER EN KLYNGE 7-MODUL I FORKLEDNING.
--
-- Den er den eneste av de fire som rapporterer til en MYNDIGHET
-- (CSRD/ESRS). Regelen er ikke vår, og den endres uten å si fra:
--
--   EN FORELDET REGEL SER NØYAKTIG UT SOM EN RIKTIG REGEL.
--
-- `standardversjon_laast_per_periode` er den dommen anvendt på
-- rapporteringsstandarden SELV. Et tall regnet med fjorårets faktor og
-- lest som årets er feil på nøyaktig den måten CSRD skal hindre.
--
-- ---------------------------------------------------------------------
-- LÅSINGEN ER STRUKTURELL, IKKE EN SJEKK.
--
-- `rapportperiode` bærer `standardversjon`, og hver måling peker på
-- perioden MED versjonen gjennom en SAMMENSATT FREMMEDNØKKEL:
--
--   FOREIGN KEY (tenant, periode_id, standardversjon)
--       REFERENCES rapportperiode (tenant, periode_id, standardversjon)
--
-- Faktoren peker samme vei. EN MÅLING SOM BRUKTE EN FAKTOR FRA EN
-- ANNEN STANDARDVERSJON ENN PERIODEN SIN, ER DERFOR UREPRESENTERBAR —
-- ikke oppdaget av en sveip, ikke validert bort i en dør. Den finnes
-- ikke.
--
-- ---------------------------------------------------------------------
-- FEM FUNN SOM ALDRI KAN REISES, OG DET ER BEVISET.
--
--   `tall_uten_kilde`          — `kilde_id` NOT NULL mot husets
--                                kilderegister (118).
--   `tall_uten_faktorversjon`  — `faktor_id` + `standardversjon` NOT
--                                NULL, sammensatt fremmednøkkel.
--   `estimat_ikke_merket`      — `er_estimat` er NOT NULL UTEN DEFAULT,
--                                og bundet til `estimatgrunnlag` av en
--                                CHECK.
--   `paastand_uten_kilde`      — `kilde_id` NOT NULL, som M-20.
--   `modulen_sendte_rapport`   — det finnes ingen kolonne for «sendt».
--
-- ---------------------------------------------------------------------
-- KILDEREGISTERET ER HUSETS, FOR TREDJE GANG.
--
-- `kildedokument` (M-46, migrasjon 118) bærer alt en påstand og et
-- tall hviler på. M-20 arvet den i 134; M-45 arver den her. Tre
-- registre for «hva hviler dette på» ville gitt tre svar.
--
-- ---------------------------------------------------------------------
-- GRENSEN MOT M-47.
--
-- M-47 eier MYNDIGHETSPLIKTER — frister mot et tilsyn, og hva som er
-- sendt inn. M-45 eier GRUNNLAGET en bærekraftsrapport hviler på. En
-- modul som utvidet M-45 til å bære innsendingen ville gjort
-- datagrunnlag til myndighetsrapportering i stillhet, og da ville
-- «sendte vi?» hatt to svar.
-- =====================================================================

-- MODULROLLEN MÅ KUNNE EIE NOE FØR DEN KAN EIE DØRENE.
GRANT USAGE, CREATE ON SCHEMA public TO disponit_esg_eier;
GRANT INSERT ON revisjonslogg TO disponit_esg_eier;

-- HUSETS TENANTVAKT (038). Granten gis av EIEREN.
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_esg_eier;
RESET ROLE;

-- HUSETS KILDEREGISTER (118), arvet for tredje gang. Tabellen eies av
-- migrator, så det trengs ingen `SET LOCAL ROLE`.
GRANT SELECT, INSERT ON kildedokument TO disponit_esg_eier;

-- ---------------------------------------------------------------------
-- `esgkrav` — TENANTENS GRENSER, IKKE VÅRE.
-- ---------------------------------------------------------------------
CREATE TABLE esgkrav (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kravversjon INT NOT NULL CHECK (kravversjon >= 1),
    -- Hvor stor andel av et tall som kan hvile på estimat før det
    -- skal sies fra. I basispunkter: 2000 = 20 %.
    estimatterskel_bp INT NOT NULL
        CHECK (estimatterskel_bp BETWEEN 0 AND 10000),
    -- Hvor mange døgn et estimat kan stå før det skal være erstattet
    -- av en måling. ET ESTIMAT ER LOV — DET ER MIDLERTIDIGHETEN SOM
    -- GJØR DET LOVLIG.
    estimatfrist_dogn INT NOT NULL
        CHECK (estimatfrist_dogn BETWEEN 1 AND 3650),
    -- Hvor lenge et kildedokument uten egen utløpsdato regnes som
    -- gyldig. M-20 og M-46 har hvert sitt tall for det samme
    -- registeret, og det er meningen: samme dokument kan være ferskt
    -- nok for en forsidepåstand og for gammelt for en CSRD-rapport.
    kilde_gyldig_dogn INT NOT NULL
        CHECK (kilde_gyldig_dogn BETWEEN 1 AND 3650),
    satt TIMESTAMPTZ NOT NULL DEFAULT now(),
    satt_av TEXT NOT NULL CHECK (satt_av ~ '[^[:space:]]'),
    CONSTRAINT esgkrav_pk PRIMARY KEY (tenant, kravversjon)
);

-- ---------------------------------------------------------------------
-- `rapportperiode` — OG STANDARDVERSJONEN LÅSES HER.
--
-- «Standardversjoner låses per rapportperiode.» Låsen er ikke en
-- kolonne som sier at den er låst — den er en NØKKEL andre tabeller
-- peker på. Se `esgmaaling_periode_fk` under.
-- ---------------------------------------------------------------------
CREATE TABLE rapportperiode (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    periode_id UUID NOT NULL,
    -- Menneskets navn på perioden, for eksempel «2026».
    merke TEXT NOT NULL CHECK (merke ~ '[^[:space:]]'),
    fra DATE NOT NULL,
    til DATE NOT NULL,
    CONSTRAINT rapportperiode_vindu CHECK (til >= fra),
    -- STANDARDEN OG VERSJONEN. Begge er tekst og ikke en enum: ESRS
    -- endrer versjonsnavn uten å spørre oss, og et lukket sett ville
    -- vært en påstand om at vi vet hva de kommer til å hete.
    standard TEXT NOT NULL CHECK (standard ~ '[^[:space:]]'),
    standardversjon TEXT NOT NULL
        CHECK (standardversjon ~ '[^[:space:]]'),
    status TEXT NOT NULL DEFAULT 'apen'
        CONSTRAINT rapportperiode_status_lukket
        CHECK (status IN ('apen', 'lukket')),
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT CHECK (lukket_av ~ '[^[:space:]]'),
    CONSTRAINT rapportperiode_lukking_er_hel CHECK (
        (status = 'lukket') = (lukket_ts IS NOT NULL)
        AND (lukket_ts IS NULL) = (lukket_av IS NULL)),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT rapportperiode_pk PRIMARY KEY (tenant, periode_id),
    -- DEN SAMMENSATTE NØKKELEN LÅSEN HVILER PÅ. Uten den kunne en
    -- måling pekt på perioden og oppgitt hvilken versjon som helst.
    CONSTRAINT rapportperiode_versjonsnokkel
        UNIQUE (tenant, periode_id, standardversjon),
    CONSTRAINT rapportperiode_merke_unikt UNIQUE (tenant, merke)
);
CREATE INDEX rapportperiode_apne
    ON rapportperiode (tenant, fra DESC) WHERE status = 'apen';

-- ---------------------------------------------------------------------
-- `utslippsfaktor` — REGISTRERT AV ET MENNESKE, MED SIN VERSJON.
--
-- FUNDAMENTETS TREDJE AVKLARING, OG DEN GJØR MODULEN BEDRE: huset har
-- ingen energi-, transport-, innkjøps- eller avfallsdata. Ingen. Da
-- registreres både mengden og faktoren av et menneske, med kilde — og
-- da vet vi alltid hvem som oppga tallet.
--
-- `verdi` er NUMERIC og ikke `double precision`: en utslippsfaktor
-- som flyttet seg i siste desimal mellom to kjøringer ville gjort
-- «samme tall» til et spørsmål med to svar.
-- ---------------------------------------------------------------------
CREATE TABLE utslippsfaktor (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    faktor_id UUID NOT NULL,
    -- Hva faktoren gjelder, for eksempel «elektrisitet_no».
    kategori TEXT NOT NULL CHECK (kategori ~ '^[a-z][a-z0-9_]*$'),
    -- Enheten mengden måles i, for eksempel «kWh».
    enhet TEXT NOT NULL CHECK (enhet ~ '[^[:space:]]'),
    -- kg CO2-ekvivalenter per enhet.
    verdi NUMERIC(20, 8) NOT NULL CHECK (verdi >= 0),
    standard TEXT NOT NULL CHECK (standard ~ '[^[:space:]]'),
    standardversjon TEXT NOT NULL
        CHECK (standardversjon ~ '[^[:space:]]'),
    -- FAKTOREN HVILER OGSÅ PÅ ET DOKUMENT. En faktor uten kilde er et
    -- tall noen husket.
    kilde_id UUID NOT NULL,
    gyldig_fra DATE NOT NULL,
    gyldig_til DATE,
    CONSTRAINT utslippsfaktor_gyldighet CHECK (
        gyldig_til IS NULL OR gyldig_til >= gyldig_fra),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT utslippsfaktor_pk PRIMARY KEY (tenant, faktor_id),
    CONSTRAINT utslippsfaktor_kilde_fk
        FOREIGN KEY (tenant, kilde_id)
        REFERENCES kildedokument (tenant, kilde_id),
    -- DEN ANDRE HALVDELEN AV LÅSEN.
    CONSTRAINT utslippsfaktor_versjonsnokkel
        UNIQUE (tenant, faktor_id, standardversjon),
    -- SAMME KATEGORI, SAMME VERSJON, ÉN FAKTOR. To ville gjort
    -- «hvilken faktor gjaldt» til et spørsmål med to svar.
    CONSTRAINT utslippsfaktor_kategori_unik
        UNIQUE (tenant, kategori, standardversjon, gyldig_fra)
);
CREATE INDEX utslippsfaktor_gjeldende
    ON utslippsfaktor (tenant, kategori, gyldig_fra DESC);

-- ---------------------------------------------------------------------
-- `esgmaaling` — MODULENS KJERNE, OG TRE INVARIANTER I ÉN TABELL.
--
-- `kilde_id`          NOT NULL → `tall_uten_kilde` er umulig.
-- `faktor_id` +
-- `standardversjon`   sammensatt FK → `tall_uten_faktorversjon` er
--                     umulig, OG faktoren KAN IKKE komme fra en annen
--                     standardversjon enn perioden sin.
-- `er_estimat`        NOT NULL UTEN DEFAULT, bundet til
--                     `estimatgrunnlag` → `estimat_ikke_merket` er
--                     umulig.
--
-- INGEN DEFAULT PÅ `er_estimat`, OG DET ER HELE POENGET. En default
-- ville stille merket alt som målt, og en glemt kolonne ville blitt en
-- FALSK PÅSTAND i stedet for en feil. Å glemme feltet skal stoppe
-- skrivingen.
-- ---------------------------------------------------------------------
CREATE TABLE esgmaaling (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    maaling_id UUID NOT NULL,
    periode_id UUID NOT NULL,
    -- STANDARDVERSJONEN BÆRES PÅ RADEN, og de to fremmednøklene under
    -- gjør at den MÅ være periodens OG faktorens. Låsen er strukturell.
    standardversjon TEXT NOT NULL
        CHECK (standardversjon ~ '[^[:space:]]'),
    kategori TEXT NOT NULL CHECK (kategori ~ '^[a-z][a-z0-9_]*$'),
    mengde NUMERIC(20, 6) NOT NULL CHECK (mengde >= 0),
    enhet TEXT NOT NULL CHECK (enhet ~ '[^[:space:]]'),
    faktor_id UUID NOT NULL,
    -- UTSLIPPET REGNES VED SKRIVING OG FRYSES. Regnet på lesetidspunkt
    -- ville tallet endret seg når faktoren ble korrigert — og en
    -- rapport som endrer seg etter at den er lest, er ikke en rapport.
    utslipp_kg NUMERIC(24, 6) NOT NULL CHECK (utslipp_kg >= 0),
    -- ESTIMATET, MERKET.
    er_estimat BOOLEAN NOT NULL,
    estimatgrunnlag TEXT,
    CONSTRAINT esgmaaling_estimat_er_begrunnet CHECK (
        er_estimat = (estimatgrunnlag IS NOT NULL)),
    CONSTRAINT esgmaaling_estimatgrunnlag_er_skrevet CHECK (
        estimatgrunnlag IS NULL
        OR length(btrim(estimatgrunnlag)) >= 16),
    -- EN MÅLING SOM ERSTATTER ET ESTIMAT PEKER PÅ DET, og begge står.
    erstatter_maaling_id UUID,
    kilde_id UUID NOT NULL,
    -- KILDENS SUM SLIK DEN VAR. 134s form: uten den kan ingen etterpå
    -- vise at det var NØYAKTIG denne versjonen av fakturaen som ble
    -- lest.
    kilde_sha256 TEXT NOT NULL CHECK (kilde_sha256 ~ '^[0-9a-f]{64}$'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT esgmaaling_pk PRIMARY KEY (tenant, maaling_id),
    -- DEN SAMMENSATTE NØKKELEN. Perioden OG versjonen sammen.
    CONSTRAINT esgmaaling_periode_fk
        FOREIGN KEY (tenant, periode_id, standardversjon)
        REFERENCES rapportperiode (tenant, periode_id, standardversjon),
    -- …OG FAKTOREN MÅ HA DEN SAMME.
    CONSTRAINT esgmaaling_faktor_fk
        FOREIGN KEY (tenant, faktor_id, standardversjon)
        REFERENCES utslippsfaktor (tenant, faktor_id, standardversjon),
    CONSTRAINT esgmaaling_kilde_fk
        FOREIGN KEY (tenant, kilde_id)
        REFERENCES kildedokument (tenant, kilde_id),
    CONSTRAINT esgmaaling_erstatter_fk
        FOREIGN KEY (tenant, erstatter_maaling_id)
        REFERENCES esgmaaling (tenant, maaling_id)
);
CREATE INDEX esgmaaling_periode
    ON esgmaaling (tenant, periode_id, kategori);
CREATE INDEX esgmaaling_estimater
    ON esgmaaling (tenant, registrert) WHERE er_estimat;
CREATE INDEX esgmaaling_kilde ON esgmaaling (tenant, kilde_id);

-- ---------------------------------------------------------------------
-- `esgpaastand` — 134s FORM, OG SAMME REGISTER.
--
-- «Ingen påstand uten datagrunnlag (anti-grønnvasking).» En påstand
-- kan hvile på et DOKUMENT, på en MÅLING, eller på begge — men aldri
-- på ingenting.
-- ---------------------------------------------------------------------
CREATE TABLE esgpaastand (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    paastand_id UUID NOT NULL,
    periode_id UUID NOT NULL,
    rekkefolge INT NOT NULL CHECK (rekkefolge >= 1),
    tekst TEXT NOT NULL CHECK (length(btrim(tekst)) > 0),
    kilde_id UUID NOT NULL,
    kilde_sha256 TEXT NOT NULL CHECK (kilde_sha256 ~ '^[0-9a-f]{64}$'),
    -- VALGFRI PEKER TIL TALLET påstanden hviler på. En påstand om at
    -- utslippet gikk ned skal kunne peke på målingen som viser det.
    maaling_id UUID,
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT esgpaastand_pk PRIMARY KEY (tenant, paastand_id),
    CONSTRAINT esgpaastand_periode_fk
        FOREIGN KEY (tenant, periode_id)
        REFERENCES rapportperiode (tenant, periode_id),
    CONSTRAINT esgpaastand_kilde_fk
        FOREIGN KEY (tenant, kilde_id)
        REFERENCES kildedokument (tenant, kilde_id),
    CONSTRAINT esgpaastand_maaling_fk
        FOREIGN KEY (tenant, maaling_id)
        REFERENCES esgmaaling (tenant, maaling_id),
    CONSTRAINT esgpaastand_nummer_unikt
        UNIQUE (tenant, periode_id, rekkefolge)
);
CREATE INDEX esgpaastand_periode
    ON esgpaastand (tenant, periode_id, rekkefolge);

-- ---------------------------------------------------------------------
-- `esgrapport` — SAMMENSTILLINGEN, OG INGEN «SENDT»-KOLONNE.
--
-- FRAVÆRET AV `sendt_ts` ER PORTEN `modulen_sendte_rapport`. Modulen
-- sammenstiller et grunnlag; innsendingen til et tilsyn er et
-- menneskes, og den hører hjemme i M-47 — ikke her. En kolonne for
-- «sendt» ville gjort «sendte vi?» til et spørsmål med to svar.
--
-- APPEND-ONLY: en ny sammenstilling er en NY RAD med et nytt
-- versjonsnummer. «Hva sto i rapporten da noen leste den» må kunne
-- besvares etterpå — det er hele `rapport_overskrevet`.
-- ---------------------------------------------------------------------
CREATE TABLE esgrapport (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    rapport_id UUID NOT NULL,
    periode_id UUID NOT NULL,
    versjon INT NOT NULL CHECK (versjon >= 1),
    -- SUMMEN AV DET SOM BLE SAMMENSTILT. Regnes av døra, aldri oppgitt.
    innholds_hash TEXT NOT NULL CHECK (innholds_hash ~ '^[0-9a-f]{64}$'),
    -- TALLENE SLIK DE STO DA. En rapport som pekte på tabellene i
    -- stedet for å bære tallene, ville endret seg når en måling ble
    -- rettet — og en rapport som endrer seg etter at den er lest, er
    -- ikke en rapport.
    sum_utslipp_kg NUMERIC(24, 6) NOT NULL CHECK (sum_utslipp_kg >= 0),
    antall_maalinger INT NOT NULL CHECK (antall_maalinger >= 0),
    antall_estimater INT NOT NULL CHECK (antall_estimater >= 0),
    -- ESTIMATANDELEN, I BASISPUNKTER AV UTSLIPPET. Den som leser
    -- rapporten skal se hvor mye av tallet som er gjettet.
    estimatandel_bp INT NOT NULL
        CHECK (estimatandel_bp BETWEEN 0 AND 10000),
    antall_paastander INT NOT NULL CHECK (antall_paastander >= 0),
    standardversjon TEXT NOT NULL
        CHECK (standardversjon ~ '[^[:space:]]'),
    sammenstilt TIMESTAMPTZ NOT NULL DEFAULT now(),
    sammenstilt_av TEXT NOT NULL
        CHECK (sammenstilt_av ~ '[^[:space:]]'),
    CONSTRAINT esgrapport_pk PRIMARY KEY (tenant, rapport_id),
    CONSTRAINT esgrapport_periode_fk
        FOREIGN KEY (tenant, periode_id, standardversjon)
        REFERENCES rapportperiode (tenant, periode_id, standardversjon),
    CONSTRAINT esgrapport_versjon_unik
        UNIQUE (tenant, periode_id, versjon),
    CONSTRAINT esgrapport_estimatandel_stemmer CHECK (
        antall_estimater <= antall_maalinger)
);
CREATE INDEX esgrapport_periode
    ON esgrapport (tenant, periode_id, versjon DESC);

-- ---------------------------------------------------------------------
-- `esgfunn` — HUSETS FORM (`apen_kolonne`).
-- ---------------------------------------------------------------------
CREATE TABLE esgfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    funn_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CONSTRAINT esgfunn_type_lukket CHECK (funntype IN (
            -- SVEIPENS EGNE. Lukkes av at tilstanden opphører.
            'estimat_ikke_erstattet_over_frist',
            'standardversjon_foreldet_i_apen_periode',
            -- ET MENNESKE KAN LUKKE DENNE, med et navn på.
            'estimatandel_over_terskel_uavklart',
            -- DE FEM SOM ALDRI KAN REISES. Se filhodet: at de står i
            -- settet OG er umulige er hele beviset.
            'tall_uten_kilde',
            'tall_uten_faktorversjon',
            'estimat_ikke_merket',
            'paastand_uten_kilde',
            'modulen_sendte_rapport')),
    referanse UUID NOT NULL,
    detaljer TEXT NOT NULL CHECK (length(btrim(detaljer)) > 0),
    over_grense BIGINT,
    apen BOOLEAN NOT NULL DEFAULT true,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT CHECK (lukket_av ~ '[^[:space:]]'),
    lukket_grunn TEXT,
    CONSTRAINT esgfunn_pk PRIMARY KEY (tenant, funn_id),
    CONSTRAINT esgfunn_unikt UNIQUE (tenant, funntype, referanse),
    CONSTRAINT esgfunn_lukking_har_navn CHECK (
        apen OR (lukket_ts IS NOT NULL AND lukket_av IS NOT NULL))
);
CREATE INDEX esgfunn_apne ON esgfunn (tenant, funntype) WHERE apen;

-- =====================================================================
-- RADVAKTENE. APPEND-ONLY ER EN TRIGGER, IKKE EN VANE.
-- =====================================================================

-- PERIODEN: bare lukkingen kan skrives, og STANDARDVERSJONEN ALDRI.
--
-- En periode som kunne bytte standardversjon i ettertid ville gjort
-- låsen til en anbefaling. Fremmednøklene ville dessuten pekt på en
-- nøkkel som ikke lenger fantes — men vakten står her uansett, fordi
-- «kunne ikke» og «ville feilet et annet sted» ikke er det samme.
CREATE FUNCTION m45_periodevakt() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'rapportperiode er append-only: en periode som'
            ' kan slettes kan ikke svare paa hvilken standard som'
            ' gjaldt';
    END IF;
    IF NEW.periode_id <> OLD.periode_id
       OR NEW.merke <> OLD.merke
       OR NEW.fra <> OLD.fra OR NEW.til <> OLD.til
       OR NEW.standard <> OLD.standard
       OR NEW.standardversjon <> OLD.standardversjon
       OR NEW.opprettet_av <> OLD.opprettet_av
    THEN
        RAISE EXCEPTION 'rapportperiode: standardversjonen er laast —'
            ' bare lukkingen kan skrives i ettertid';
    END IF;
    IF OLD.status = 'lukket' THEN
        RAISE EXCEPTION 'rapportperiode: allerede lukket %',
            OLD.lukket_ts;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m45_periodevakt() FROM PUBLIC;
CREATE TRIGGER m45_periodevakt
    BEFORE UPDATE OR DELETE ON rapportperiode
    FOR EACH ROW EXECUTE FUNCTION m45_periodevakt();

-- MÅLINGEN, PÅSTANDEN, FAKTOREN OG RAPPORTEN ER HELT FROSNE.
--
-- En måling som kunne endres etter at utslippet ble regnet, ville
-- vært et tall som byttet grunnlag uten å si fra — og en rapport som
-- endrer seg etter at den er lest, er ikke en rapport.
CREATE FUNCTION m45_frossenvakt() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    RAISE EXCEPTION '%: raden er frossen — % er ikke tillatt',
        TG_TABLE_NAME, TG_OP;
END $$;
REVOKE ALL ON FUNCTION m45_frossenvakt() FROM PUBLIC;
CREATE TRIGGER m45_maalingsvakt
    BEFORE UPDATE OR DELETE ON esgmaaling
    FOR EACH ROW EXECUTE FUNCTION m45_frossenvakt();
CREATE TRIGGER m45_paastandsvakt
    BEFORE UPDATE OR DELETE ON esgpaastand
    FOR EACH ROW EXECUTE FUNCTION m45_frossenvakt();
CREATE TRIGGER m45_rapportvakt
    BEFORE UPDATE OR DELETE ON esgrapport
    FOR EACH ROW EXECUTE FUNCTION m45_frossenvakt();

-- FAKTOREN: bare avviklingen kan skrives, og VERDIEN ALDRI.
--
-- En faktor som kunne korrigeres i ettertid ville endret hvert tall
-- som noen gang ble regnet med den. Skal faktoren rettes, er det en NY
-- faktor med en ny versjon — og de gamle tallene står, med sitt
-- grunnlag.
CREATE FUNCTION m45_faktorvakt() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'utslippsfaktor er append-only';
    END IF;
    IF NEW.faktor_id <> OLD.faktor_id
       OR NEW.kategori <> OLD.kategori OR NEW.enhet <> OLD.enhet
       OR NEW.verdi <> OLD.verdi
       OR NEW.standard <> OLD.standard
       OR NEW.standardversjon <> OLD.standardversjon
       OR NEW.kilde_id <> OLD.kilde_id
       OR NEW.gyldig_fra <> OLD.gyldig_fra
    THEN
        RAISE EXCEPTION 'utslippsfaktor: verdien er frossen — en'
            ' rettelse er en NY faktor med en ny versjon';
    END IF;
    IF OLD.gyldig_til IS NOT NULL THEN
        RAISE EXCEPTION 'utslippsfaktor: allerede avviklet %',
            OLD.gyldig_til;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m45_faktorvakt() FROM PUBLIC;
CREATE TRIGGER m45_faktorvakt
    BEFORE UPDATE OR DELETE ON utslippsfaktor
    FOR EACH ROW EXECUTE FUNCTION m45_faktorvakt();

-- FUNNET: bare lukkingen og `sist_sett` (133/134/135s form).
CREATE FUNCTION m45_funnvakt() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'esgfunn er append-only';
    END IF;
    IF NEW.funntype <> OLD.funntype OR NEW.referanse <> OLD.referanse
       OR NEW.forst_sett <> OLD.forst_sett THEN
        RAISE EXCEPTION 'esgfunn: funntype, referanse og forst_sett er'
            ' frosne';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m45_funnvakt() FROM PUBLIC;
CREATE TRIGGER m45_funnvakt
    BEFORE UPDATE OR DELETE ON esgfunn
    FOR EACH ROW EXECUTE FUNCTION m45_funnvakt();

-- =====================================================================
-- RADVAKT OG RETTIGHETER. FORCE RLS PÅ ALLE SJU.
-- =====================================================================
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['esgkrav', 'rapportperiode',
                             'utslippsfaktor', 'esgmaaling',
                             'esgpaastand', 'esgrapport', 'esgfunn']
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format($f$CREATE POLICY tenant_isolasjon ON public.%I
            USING (tenant = current_setting('disponit.tenant', true))
            WITH CHECK (tenant = current_setting('disponit.tenant', true))$f$, t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO'
                       ' disponit_esg_eier', t);
    END LOOP;
END $$;

-- APPEND-ONLY MÅLT SOM EN RETTIGHET OG IKKE BARE SOM EN TRIGGER.
REVOKE UPDATE ON public.esgmaaling FROM disponit_esg_eier;
REVOKE UPDATE ON public.esgpaastand FROM disponit_esg_eier;
REVOKE UPDATE ON public.esgrapport FROM disponit_esg_eier;

-- SVEIPENS KRYSS-TENANT-POLICY (130s LÆRDOM).
CREATE POLICY m45_sveip_tenantliste ON esgkrav
    FOR SELECT
    USING (current_setting('disponit.tenant', true) IS NULL
           OR current_setting('disponit.tenant', true) = '');

-- =====================================================================
-- HERFRA EIES DØRENE AV ESG-EIEREN.
-- =====================================================================
SET LOCAL ROLE disponit_esg_eier;

CREATE FUNCTION m45_evidens(p_tenant TEXT, p_ref UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm45_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm45_esg', 'handling', p_handling,
        'ref', p_ref::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm45_esg',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:esg', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;

-- `m45_funn_er_sveipens` — HVEM SOM KAN LUKKE HVA.
CREATE FUNCTION m45_funn_er_sveipens(p_funntype TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE AS $$
    SELECT p_funntype <> 'estimatandel_over_terskel_uavklart'
$$;

-- `m45_kilde_gyldig` — SAMME REGEL SOM 134, MEN TENANTENS EGET VINDU.
--
-- Merk at REGELEN ikke dupliseres: 134s `m20_kilde_gyldig` er M-20s,
-- og den leser M-20s vindu. Her leses `esgkrav.kilde_gyldig_dogn`.
-- Samme DOKUMENT kan være ferskt nok for en forsidepåstand og for
-- gammelt for en CSRD-rapport, og da er to vinduer riktig — men
-- formen er den samme, og det er med vilje.
CREATE FUNCTION m45_kilde_gyldig(p_gyldig_til DATE,
                                 p_registrert TIMESTAMPTZ,
                                 p_vindu_dogn INT, p_paa DATE)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN p_gyldig_til IS NOT NULL THEN p_paa <= p_gyldig_til
        ELSE p_paa <= (p_registrert AT TIME ZONE 'UTC')::DATE + p_vindu_dogn
    END
$$;

CREATE FUNCTION m45_sett_krav(p_tenant TEXT, p_estimatterskel INT,
                              p_estimatfrist INT, p_kilde_dogn INT,
                              p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm45_sett_krav');
    SELECT coalesce(max(kravversjon), 0) + 1 INTO v_versjon
      FROM public.esgkrav WHERE tenant = p_tenant;
    INSERT INTO public.esgkrav
        (tenant, kravversjon, estimatterskel_bp, estimatfrist_dogn,
         kilde_gyldig_dogn, satt_av)
    VALUES (p_tenant, v_versjon, p_estimatterskel, p_estimatfrist,
            p_kilde_dogn, p_aktor);
    PERFORM public.m45_evidens(p_tenant, NULL, 'sett_krav', p_aktor,
        jsonb_build_object('versjon', v_versjon));
    RETURN v_versjon;
END $$;

-- `m45_registrer_kilde` — HUSETS KILDEREGISTER (118), tredje modul.
CREATE FUNCTION m45_registrer_kilde(p_tenant TEXT, p_kilde_id UUID,
                                    p_tittel TEXT, p_dokumenttype TEXT,
                                    p_gyldig_til DATE,
                                    p_innhold_sha256 TEXT, p_aktor TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_finnes UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm45_registrer_kilde');
    -- SAMME SUM ER SAMME DOKUMENT (134s form).
    SELECT kilde_id INTO v_finnes FROM public.kildedokument
     WHERE tenant = p_tenant AND innhold_sha256 = p_innhold_sha256;
    IF v_finnes IS NOT NULL THEN
        RETURN v_finnes;
    END IF;
    INSERT INTO public.kildedokument
        (tenant, kilde_id, tittel, dokumenttype, gyldig_til,
         innhold_sha256, registrert_av)
    VALUES (p_tenant, p_kilde_id, p_tittel, p_dokumenttype,
            p_gyldig_til, p_innhold_sha256, p_aktor);
    PERFORM public.m45_evidens(p_tenant, p_kilde_id, 'registrer_kilde',
        p_aktor, jsonb_build_object('type', p_dokumenttype));
    RETURN p_kilde_id;
END $$;

-- ---------------------------------------------------------------------
-- `m45_apne_periode` — OG STANDARDVERSJONEN LÅSES I DET SAMME.
--
-- Versjonen oppgis ÉN GANG, ved åpning, og kan aldri endres. Det er
-- ikke en bekvemmelighet: en periode som kunne bytte versjon i
-- ettertid ville gjort hvert tall i den til et tall regnet med en
-- annen standard enn det står at det er.
-- ---------------------------------------------------------------------
CREATE FUNCTION m45_apne_periode(p_tenant TEXT, p_periode_id UUID,
                                 p_merke TEXT, p_fra DATE, p_til DATE,
                                 p_standard TEXT, p_versjon TEXT,
                                 p_aktor TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm45_apne_periode');
    INSERT INTO public.rapportperiode
        (tenant, periode_id, merke, fra, til, standard,
         standardversjon, opprettet_av)
    VALUES (p_tenant, p_periode_id, p_merke, p_fra, p_til, p_standard,
            p_versjon, p_aktor)
    ON CONFLICT ON CONSTRAINT rapportperiode_pk DO NOTHING;
    PERFORM public.m45_evidens(p_tenant, p_periode_id, 'apne_periode',
        p_aktor, jsonb_build_object('standard', p_standard,
                                    'versjon', p_versjon));
    RETURN p_periode_id;
END $$;

CREATE FUNCTION m45_lukk_periode(p_tenant TEXT, p_periode_id UUID,
                                 p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm45_lukk_periode');
    -- `UPDATE` tar radlåsen selv, og `AND p.status = 'apen'` gjør
    -- lukkingen atomisk. De to skrivedørene over venter på DENNE
    -- låsen, og ser status etter at lukkingen har committet.
    UPDATE public.rapportperiode p
       SET status = 'lukket', lukket_ts = now(), lukket_av = p_aktor
     WHERE p.tenant = p_tenant AND p.periode_id = p_periode_id
       AND p.status = 'apen';
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    PERFORM public.m45_evidens(p_tenant, p_periode_id, 'lukk_periode',
        p_aktor, '{}'::jsonb);
    RETURN true;
END $$;

-- ---------------------------------------------------------------------
-- `m45_registrer_faktor` — FAKTOREN HVILER OGSÅ PÅ ET DOKUMENT.
--
-- En utslippsfaktor uten kilde er et tall noen husket, og hele
-- rapporten hviler på det.
-- ---------------------------------------------------------------------
CREATE FUNCTION m45_registrer_faktor(p_tenant TEXT, p_faktor_id UUID,
                                     p_kategori TEXT, p_enhet TEXT,
                                     p_verdi NUMERIC, p_standard TEXT,
                                     p_versjon TEXT, p_kilde_id UUID,
                                     p_gyldig_fra DATE, p_aktor TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_krav RECORD;
        v_kilde RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm45_registrer_faktor');
    SELECT * INTO v_krav FROM public.esgkrav
     WHERE tenant = p_tenant ORDER BY kravversjon DESC LIMIT 1;
    IF v_krav IS NULL THEN
        RAISE EXCEPTION 'm45_registrer_faktor: tenanten har ingen'
            ' esgkrav — grensene settes foer det regnes';
    END IF;
    SELECT * INTO v_kilde FROM public.kildedokument
     WHERE tenant = p_tenant AND kilde_id = p_kilde_id;
    IF v_kilde IS NULL THEN
        RAISE EXCEPTION 'm45_registrer_faktor: ukjent kildedokument %',
            p_kilde_id;
    END IF;
    IF NOT public.m45_kilde_gyldig(v_kilde.gyldig_til, v_kilde.registrert,
                                   v_krav.kilde_gyldig_dogn,
                                   current_date) THEN
        RAISE EXCEPTION 'm45_registrer_faktor: kilden % er utloept — en'
            ' faktor kan ikke hvile paa den', v_kilde.tittel;
    END IF;
    INSERT INTO public.utslippsfaktor
        (tenant, faktor_id, kategori, enhet, verdi, standard,
         standardversjon, kilde_id, gyldig_fra, registrert_av)
    VALUES (p_tenant, p_faktor_id, p_kategori, p_enhet, p_verdi,
            p_standard, p_versjon, p_kilde_id, p_gyldig_fra, p_aktor)
    ON CONFLICT ON CONSTRAINT utslippsfaktor_pk DO NOTHING;
    PERFORM public.m45_evidens(p_tenant, p_faktor_id,
        'registrer_faktor', p_aktor,
        jsonb_build_object('kategori', p_kategori, 'versjon', p_versjon));
    RETURN p_faktor_id;
END $$;

CREATE FUNCTION m45_avvikle_faktor(p_tenant TEXT, p_faktor_id UUID,
                                   p_gyldig_til DATE, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_fra DATE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm45_avvikle_faktor');
    SELECT gyldig_fra INTO v_fra FROM public.utslippsfaktor
     WHERE tenant = p_tenant AND faktor_id = p_faktor_id FOR UPDATE;
    IF v_fra IS NULL THEN
        RAISE EXCEPTION 'm45_avvikle_faktor: ukjent faktor %',
            p_faktor_id;
    END IF;
    IF p_gyldig_til < v_fra THEN
        RAISE EXCEPTION 'm45_avvikle_faktor: kan ikke avvikles foer den'
            ' gjaldt';
    END IF;
    UPDATE public.utslippsfaktor f SET gyldig_til = p_gyldig_til
     WHERE f.tenant = p_tenant AND f.faktor_id = p_faktor_id;
    PERFORM public.m45_evidens(p_tenant, p_faktor_id, 'avvikle_faktor',
        p_aktor, jsonb_build_object('gyldig_til', p_gyldig_til));
    RETURN FOUND;
END $$;

-- ---------------------------------------------------------------------
-- `m45_registrer_maaling` — MODULENS VIKTIGSTE DØR.
--
-- SEKS NEKT, ALLE FØR RADEN FINNES:
--
--   1. Perioden finnes ikke.
--   2. Perioden er LUKKET. Et tall lagt til etter at rapporten ble
--      sammenstilt ville endret et tall noen alt hadde lest.
--   3. Faktoren finnes ikke.
--   4. Faktoren har en ANNEN STANDARDVERSJON enn perioden. Døra sier
--      det med en setning; fremmednøkkelen ville nektet uansett, og
--      det er meningen — men en `foreign key violation` er ikke en
--      feilmelding noen kan handle på.
--   5. Faktoren gjaldt ikke i perioden.
--   6. Kilden finnes ikke eller er utløpt.
--
-- `er_estimat` HAR INGEN DEFAULT, og døra krever `estimatgrunnlag`
-- sammen med den. ET ESTIMAT SOM IKKE SIER HVA DET HVILER PÅ, ER ET
-- TALL NOEN GJETTET.
--
-- UTSLIPPET REGNES HER OG FRYSES. Regnet på lesetidspunkt ville tallet
-- endret seg når faktoren ble korrigert.
-- ---------------------------------------------------------------------
CREATE FUNCTION m45_registrer_maaling(p_tenant TEXT, p_maaling_id UUID,
                                      p_periode_id UUID,
                                      p_kategori TEXT, p_mengde NUMERIC,
                                      p_enhet TEXT, p_faktor_id UUID,
                                      p_er_estimat BOOLEAN,
                                      p_estimatgrunnlag TEXT,
                                      p_erstatter UUID, p_kilde_id UUID,
                                      p_aktor TEXT)
RETURNS TABLE (maaling_id UUID, utslipp_kg NUMERIC,
               standardversjon TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_periode RECORD;
    v_faktor RECORD;
    v_kilde RECORD;
    v_krav RECORD;
    v_utslipp NUMERIC;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm45_registrer_maaling');
    IF p_er_estimat IS NULL THEN
        RAISE EXCEPTION 'm45_registrer_maaling: er_estimat mangler —'
            ' et tall som ikke sier om det er maalt eller gjettet, er'
            ' en paastand om at det er maalt';
    END IF;
    IF p_er_estimat AND (p_estimatgrunnlag IS NULL
                         OR length(btrim(p_estimatgrunnlag)) < 16) THEN
        RAISE EXCEPTION 'm45_registrer_maaling: et estimat maa si hva'
            ' det hviler paa';
    END IF;
    IF NOT p_er_estimat AND p_estimatgrunnlag IS NOT NULL THEN
        RAISE EXCEPTION 'm45_registrer_maaling: en maaling har ikke et'
            ' estimatgrunnlag';
    END IF;
    -- NEKT 1 OG 2. LÅS FØRST, LES ETTERPÅ (klynge 6s lærdom).
    --
    -- UTEN `FOR UPDATE` ER DET ET KAPPLØP: to transaksjoner kan begge
    -- lese `status = 'apen'`, den ene lukker perioden og committer, og
    -- den andre skriver et tall inn i en lukket periode. Da ville et
    -- tall landet etter at rapporten var sammenstilt — og «hva sto i
    -- rapporten da noen leste den» hadde to svar. CodeRabbit fant den
    -- 5/9.
    SELECT * INTO v_periode FROM public.rapportperiode
     WHERE tenant = p_tenant AND periode_id = p_periode_id
       FOR UPDATE;
    IF v_periode IS NULL THEN
        RAISE EXCEPTION 'm45_registrer_maaling: ukjent rapportperiode %',
            p_periode_id;
    END IF;
    IF v_periode.status <> 'apen' THEN
        RAISE EXCEPTION 'm45_registrer_maaling: perioden % er lukket —'
            ' et tall lagt til naa ville endret et tall noen alt har'
            ' lest', v_periode.merke;
    END IF;
    -- NEKT 3 OG 4.
    SELECT * INTO v_faktor FROM public.utslippsfaktor
     WHERE tenant = p_tenant AND faktor_id = p_faktor_id;
    IF v_faktor IS NULL THEN
        RAISE EXCEPTION 'm45_registrer_maaling: ukjent utslippsfaktor %',
            p_faktor_id;
    END IF;
    IF v_faktor.standardversjon <> v_periode.standardversjon THEN
        -- `%` ER PLASSHOLDEREN I `RAISE`, ikke `%s`. Første utkast
        -- skrev «faktoren er 2027.1s», med en loes bokstav paa
        -- slutten — `format()` og `RAISE` er ikke samme sprak.
        RAISE EXCEPTION 'm45_registrer_maaling: faktoren er %, mens'
            ' perioden er laast til % — et tall regnet med feil'
            ' standardversjon er feil paa noeyaktig den maaten CSRD'
            ' skal hindre',
            v_faktor.standardversjon, v_periode.standardversjon;
    END IF;
    -- NEKT 5. Faktoren måtte gjelde i perioden den brukes for.
    IF v_faktor.gyldig_fra > v_periode.til
       OR (v_faktor.gyldig_til IS NOT NULL
           AND v_faktor.gyldig_til < v_periode.fra) THEN
        RAISE EXCEPTION 'm45_registrer_maaling: faktoren gjaldt ikke i'
            ' perioden %', v_periode.merke;
    END IF;
    IF v_faktor.enhet <> p_enhet THEN
        RAISE EXCEPTION 'm45_registrer_maaling: faktoren er per %,'
            ' mengden er oppgitt i %', v_faktor.enhet, p_enhet;
    END IF;
    -- NEKT 6.
    SELECT * INTO v_krav FROM public.esgkrav
     WHERE tenant = p_tenant ORDER BY kravversjon DESC LIMIT 1;
    SELECT * INTO v_kilde FROM public.kildedokument
     WHERE tenant = p_tenant AND kilde_id = p_kilde_id;
    IF v_kilde IS NULL THEN
        RAISE EXCEPTION 'm45_registrer_maaling: ukjent kildedokument %',
            p_kilde_id;
    END IF;
    IF NOT public.m45_kilde_gyldig(v_kilde.gyldig_til, v_kilde.registrert,
                                   v_krav.kilde_gyldig_dogn,
                                   current_date) THEN
        RAISE EXCEPTION 'm45_registrer_maaling: kilden % er utloept',
            v_kilde.tittel;
    END IF;
    IF p_erstatter IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM public.esgmaaling m
                        WHERE m.tenant = p_tenant
                          AND m.maaling_id = p_erstatter
                          AND m.periode_id = p_periode_id) THEN
        RAISE EXCEPTION 'm45_registrer_maaling: maalingen som erstattes'
            ' hoerer ikke til denne perioden';
    END IF;
    v_utslipp := round(p_mengde * v_faktor.verdi, 6);
    INSERT INTO public.esgmaaling
        (tenant, maaling_id, periode_id, standardversjon, kategori,
         mengde, enhet, faktor_id, utslipp_kg, er_estimat,
         estimatgrunnlag, erstatter_maaling_id, kilde_id, kilde_sha256,
         registrert_av)
    VALUES (p_tenant, p_maaling_id, p_periode_id,
            v_periode.standardversjon, p_kategori, p_mengde, p_enhet,
            p_faktor_id, v_utslipp, p_er_estimat, p_estimatgrunnlag,
            p_erstatter, p_kilde_id, v_kilde.innhold_sha256, p_aktor);
    PERFORM public.m45_evidens(p_tenant, p_maaling_id,
        'registrer_maaling', p_aktor,
        jsonb_build_object('kategori', p_kategori,
                           'er_estimat', p_er_estimat,
                           'utslipp_kg', v_utslipp));
    RETURN QUERY SELECT p_maaling_id, v_utslipp,
                        v_periode.standardversjon;
END $$;

-- ---------------------------------------------------------------------
-- `m45_registrer_paastand` — 134s FORM, ORDRETT.
--
-- «Ingen påstand uten datagrunnlag.» `kilde_id` er NOT NULL med
-- fremmednøkkel; peker påstanden også på en måling, må den hore til
-- samme periode.
-- ---------------------------------------------------------------------
CREATE FUNCTION m45_registrer_paastand(p_tenant TEXT, p_paastand_id UUID,
                                       p_periode_id UUID,
                                       p_rekkefolge INT, p_tekst TEXT,
                                       p_kilde_id UUID,
                                       p_maaling_id UUID, p_aktor TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_periode RECORD;
    v_kilde RECORD;
    v_krav RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm45_registrer_paastand');
    -- LÅS FØRST, LES ETTERPÅ. Samme kappløp som i
    -- `m45_registrer_maaling`: en påstand skrevet inn i en periode som
    -- nettopp ble lukket, ville stått i en rapport ingen sammenstilte
    -- den med.
    SELECT * INTO v_periode FROM public.rapportperiode
     WHERE tenant = p_tenant AND periode_id = p_periode_id
       FOR UPDATE;
    IF v_periode IS NULL THEN
        RAISE EXCEPTION 'm45_registrer_paastand: ukjent rapportperiode %',
            p_periode_id;
    END IF;
    IF v_periode.status <> 'apen' THEN
        RAISE EXCEPTION 'm45_registrer_paastand: perioden % er lukket',
            v_periode.merke;
    END IF;
    SELECT * INTO v_krav FROM public.esgkrav
     WHERE tenant = p_tenant ORDER BY kravversjon DESC LIMIT 1;
    SELECT * INTO v_kilde FROM public.kildedokument
     WHERE tenant = p_tenant AND kilde_id = p_kilde_id;
    IF v_kilde IS NULL THEN
        RAISE EXCEPTION 'm45_registrer_paastand: ukjent kildedokument %',
            p_kilde_id;
    END IF;
    IF NOT public.m45_kilde_gyldig(v_kilde.gyldig_til, v_kilde.registrert,
                                   v_krav.kilde_gyldig_dogn,
                                   current_date) THEN
        RAISE EXCEPTION 'm45_registrer_paastand: kilden % er utloept —'
            ' en paastand kan ikke hvile paa den', v_kilde.tittel;
    END IF;
    IF p_maaling_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM public.esgmaaling m
                        WHERE m.tenant = p_tenant
                          AND m.maaling_id = p_maaling_id
                          AND m.periode_id = p_periode_id) THEN
        RAISE EXCEPTION 'm45_registrer_paastand: maalingen hoerer ikke'
            ' til denne perioden';
    END IF;
    INSERT INTO public.esgpaastand
        (tenant, paastand_id, periode_id, rekkefolge, tekst, kilde_id,
         kilde_sha256, maaling_id, registrert_av)
    VALUES (p_tenant, p_paastand_id, p_periode_id, p_rekkefolge,
            p_tekst, p_kilde_id, v_kilde.innhold_sha256, p_maaling_id,
            p_aktor);
    PERFORM public.m45_evidens(p_tenant, p_paastand_id,
        'registrer_paastand', p_aktor,
        jsonb_build_object('kilde', p_kilde_id));
    RETURN p_paastand_id;
END $$;

-- ---------------------------------------------------------------------
-- `m45_sammenstill` — OG DEN SENDER INGENTING.
--
-- Døra samler tallene som står i perioden, regner summen og
-- estimatandelen, og skriver en NY RAD. Det finnes ingen kolonne for
-- «sendt», og ingen dør som setter en. Innsendingen til et tilsyn er
-- et menneskes, og den hører hjemme i M-47.
--
-- ESTIMATANDELEN REGNES AV UTSLIPPET, IKKE AV ANTALLET. Ti små
-- estimater og én stor måling er ikke «91 % gjettet» — det er tallet
-- som betyr noe, ikke radene.
-- ---------------------------------------------------------------------
CREATE FUNCTION m45_sammenstill(p_tenant TEXT, p_rapport_id UUID,
                                p_periode_id UUID, p_aktor TEXT)
RETURNS TABLE (rapport_id UUID, versjon INT, sum_utslipp_kg NUMERIC,
               estimatandel_bp INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_periode RECORD;
    v_sum NUMERIC;
    v_est NUMERIC;
    v_antall INT;
    v_antall_est INT;
    v_paastander INT;
    v_andel INT;
    v_versjon INT;
    v_hash TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm45_sammenstill');
    SELECT * INTO v_periode FROM public.rapportperiode
     WHERE tenant = p_tenant AND periode_id = p_periode_id;
    IF v_periode IS NULL THEN
        RAISE EXCEPTION 'm45_sammenstill: ukjent rapportperiode %',
            p_periode_id;
    END IF;
    -- EN MÅLING SOM ER ERSTATTET TELLER IKKE. Den står i registeret —
    -- historikken overskrives ikke — men rapporten bærer det siste
    -- tallet.
    SELECT coalesce(sum(m.utslipp_kg), 0),
           coalesce(sum(m.utslipp_kg) FILTER (WHERE m.er_estimat), 0),
           count(*)::INT,
           count(*) FILTER (WHERE m.er_estimat)::INT
      INTO v_sum, v_est, v_antall, v_antall_est
      FROM public.esgmaaling m
     WHERE m.tenant = p_tenant AND m.periode_id = p_periode_id
       AND NOT EXISTS (SELECT 1 FROM public.esgmaaling r
                        WHERE r.tenant = p_tenant
                          AND r.erstatter_maaling_id = m.maaling_id);
    SELECT count(*)::INT INTO v_paastander FROM public.esgpaastand
     WHERE tenant = p_tenant AND periode_id = p_periode_id;
    v_andel := CASE WHEN v_sum = 0 THEN 0
                    ELSE round(v_est * 10000 / v_sum)::INT END;
    SELECT coalesce(max(r.versjon), 0) + 1 INTO v_versjon
      FROM public.esgrapport r
     WHERE r.tenant = p_tenant AND r.periode_id = p_periode_id;
    v_hash := encode(sha256(convert_to(
        jsonb_build_object('periode', p_periode_id,
                           'versjon', v_periode.standardversjon,
                           'sum', v_sum, 'estimat', v_est,
                           'maalinger', v_antall,
                           'paastander', v_paastander)::text,
        'UTF8')), 'hex');
    INSERT INTO public.esgrapport
        (tenant, rapport_id, periode_id, versjon, innholds_hash,
         sum_utslipp_kg, antall_maalinger, antall_estimater,
         estimatandel_bp, antall_paastander, standardversjon,
         sammenstilt_av)
    VALUES (p_tenant, p_rapport_id, p_periode_id, v_versjon, v_hash,
            v_sum, v_antall, v_antall_est, v_andel, v_paastander,
            v_periode.standardversjon, p_aktor);
    PERFORM public.m45_evidens(p_tenant, p_rapport_id, 'sammenstill',
        p_aktor, jsonb_build_object('versjon', v_versjon,
                                    'estimatandel_bp', v_andel));
    RETURN QUERY SELECT p_rapport_id, v_versjon, v_sum, v_andel;
END $$;

-- =====================================================================
-- LESEDØRENE.
-- =====================================================================

-- `m45_maalingene` — TALLENE MED SITT GRUNNLAG, i én rad hver.
CREATE FUNCTION m45_maalingene(p_tenant TEXT, p_periode_id UUID)
RETURNS TABLE (maaling_id UUID, kategori TEXT, mengde NUMERIC,
               enhet TEXT, utslipp_kg NUMERIC, er_estimat BOOLEAN,
               estimatgrunnlag TEXT, faktor_verdi NUMERIC,
               standardversjon TEXT, kilde_tittel TEXT,
               kilde_sha256 TEXT, kilde_gyldig BOOLEAN,
               erstattet BOOLEAN, dogn_gammelt INT,
               registrert TIMESTAMPTZ, registrert_av TEXT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT m.maaling_id, m.kategori, m.mengde, m.enhet, m.utslipp_kg,
           m.er_estimat, m.estimatgrunnlag, f.verdi, m.standardversjon,
           d.tittel, m.kilde_sha256,
           public.m45_kilde_gyldig(d.gyldig_til, d.registrert,
               coalesce((SELECT k.kilde_gyldig_dogn FROM public.esgkrav k
                          WHERE k.tenant = p_tenant
                          ORDER BY k.kravversjon DESC LIMIT 1), 365),
               current_date),
           -- ET ERSTATTET TALL ER SYNLIG SOM ERSTATTET, ikke borte.
           EXISTS (SELECT 1 FROM public.esgmaaling r
                    WHERE r.tenant = m.tenant
                      AND r.erstatter_maaling_id = m.maaling_id),
           (current_date - (m.registrert AT TIME ZONE 'UTC')::DATE)::INT,
           m.registrert, m.registrert_av
      FROM public.esgmaaling m
      JOIN public.utslippsfaktor f
        ON f.tenant = m.tenant AND f.faktor_id = m.faktor_id
      JOIN public.kildedokument d
        ON d.tenant = m.tenant AND d.kilde_id = m.kilde_id
     WHERE m.tenant = p_tenant AND m.periode_id = p_periode_id
     ORDER BY m.kategori, m.registrert
$$;

-- `m45_paastandene` — påstandene med sin kilde.
CREATE FUNCTION m45_paastandene(p_tenant TEXT, p_periode_id UUID)
RETURNS TABLE (paastand_id UUID, rekkefolge INT, tekst TEXT,
               kilde_tittel TEXT, dokumenttype TEXT,
               kilde_sha256 TEXT, kilde_gyldig BOOLEAN,
               maaling_id UUID, maaling_er_estimat BOOLEAN,
               registrert TIMESTAMPTZ, registrert_av TEXT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT p.paastand_id, p.rekkefolge, p.tekst, d.tittel,
           d.dokumenttype, p.kilde_sha256,
           public.m45_kilde_gyldig(d.gyldig_til, d.registrert,
               coalesce((SELECT k.kilde_gyldig_dogn FROM public.esgkrav k
                          WHERE k.tenant = p_tenant
                          ORDER BY k.kravversjon DESC LIMIT 1), 365),
               current_date),
           p.maaling_id,
           -- EN PÅSTAND SOM HVILER PÅ ET ESTIMAT BÆRER DET VIDERE.
           -- Usikkerheten forsvinner ikke fordi noen skrev en setning
           -- rundt tallet (133/134s form).
           m.er_estimat,
           p.registrert, p.registrert_av
      FROM public.esgpaastand p
      JOIN public.kildedokument d
        ON d.tenant = p.tenant AND d.kilde_id = p.kilde_id
      LEFT JOIN public.esgmaaling m
        ON m.tenant = p.tenant AND m.maaling_id = p.maaling_id
     WHERE p.tenant = p_tenant AND p.periode_id = p_periode_id
     ORDER BY p.rekkefolge
$$;

-- `m45_perioderegister` — periodene med sin låste versjon.
CREATE FUNCTION m45_perioderegister(p_tenant TEXT, p_maks INT)
RETURNS TABLE (periode_id UUID, merke TEXT, fra DATE, til DATE,
               standard TEXT, standardversjon TEXT, status TEXT,
               antall_maalinger INT, antall_estimater INT,
               antall_paastander INT, sum_utslipp_kg NUMERIC,
               estimatandel_bp INT, siste_rapportversjon INT,
               antall_utlopte_kilder INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    WITH vindu AS (
        SELECT coalesce((SELECT k.kilde_gyldig_dogn FROM public.esgkrav k
                          WHERE k.tenant = p_tenant
                          ORDER BY k.kravversjon DESC LIMIT 1), 365) AS d),
    levende AS (
        SELECT m.* FROM public.esgmaaling m
         WHERE m.tenant = p_tenant
           AND NOT EXISTS (SELECT 1 FROM public.esgmaaling r
                            WHERE r.tenant = p_tenant
                              AND r.erstatter_maaling_id = m.maaling_id))
    SELECT p.periode_id, p.merke, p.fra, p.til, p.standard,
           p.standardversjon, p.status,
           (SELECT count(*)::INT FROM levende l
             WHERE l.periode_id = p.periode_id),
           (SELECT count(*)::INT FROM levende l
             WHERE l.periode_id = p.periode_id AND l.er_estimat),
           (SELECT count(*)::INT FROM public.esgpaastand s
             WHERE s.tenant = p_tenant AND s.periode_id = p.periode_id),
           (SELECT coalesce(sum(l.utslipp_kg), 0) FROM levende l
             WHERE l.periode_id = p.periode_id),
           (SELECT CASE WHEN coalesce(sum(l.utslipp_kg), 0) = 0 THEN 0
                        ELSE round(coalesce(sum(l.utslipp_kg)
                             FILTER (WHERE l.er_estimat), 0)
                             * 10000 / sum(l.utslipp_kg))::INT END
              FROM levende l WHERE l.periode_id = p.periode_id),
           (SELECT max(r.versjon) FROM public.esgrapport r
             WHERE r.tenant = p_tenant AND r.periode_id = p.periode_id),
           (SELECT count(*)::INT
              FROM levende l
              JOIN public.kildedokument d
                ON d.tenant = p_tenant AND d.kilde_id = l.kilde_id
             WHERE l.periode_id = p.periode_id
               AND NOT public.m45_kilde_gyldig(d.gyldig_til, d.registrert,
                       (SELECT v.d FROM vindu v), current_date))
      FROM public.rapportperiode p
     WHERE p.tenant = p_tenant
     ORDER BY p.fra DESC
     LIMIT greatest(p_maks, 1)
$$;

-- `m45_faktorene` — faktorene med sin versjon og sin kilde.
CREATE FUNCTION m45_faktorene(p_tenant TEXT)
RETURNS TABLE (faktor_id UUID, kategori TEXT, enhet TEXT,
               verdi NUMERIC, standard TEXT, standardversjon TEXT,
               kilde_tittel TEXT, gyldig_fra DATE, gyldig_til DATE,
               gjelder BOOLEAN, antall_maalinger INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT f.faktor_id, f.kategori, f.enhet, f.verdi, f.standard,
           f.standardversjon, d.tittel, f.gyldig_fra, f.gyldig_til,
           f.gyldig_til IS NULL OR f.gyldig_til >= current_date,
           (SELECT count(*)::INT FROM public.esgmaaling m
             WHERE m.tenant = f.tenant AND m.faktor_id = f.faktor_id)
      FROM public.utslippsfaktor f
      JOIN public.kildedokument d
        ON d.tenant = f.tenant AND d.kilde_id = f.kilde_id
     WHERE f.tenant = p_tenant
     ORDER BY f.kategori, f.gyldig_fra DESC
$$;

-- `m45_rapportene` — hver sammenstilling, med tallene slik de sto DA.
CREATE FUNCTION m45_rapportene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (rapport_id UUID, periode_id UUID, periodemerke TEXT,
               versjon INT, innholds_hash TEXT, sum_utslipp_kg NUMERIC,
               antall_maalinger INT, antall_estimater INT,
               estimatandel_bp INT, antall_paastander INT,
               standardversjon TEXT, sammenstilt TIMESTAMPTZ,
               sammenstilt_av TEXT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT r.rapport_id, r.periode_id, p.merke, r.versjon,
           r.innholds_hash, r.sum_utslipp_kg, r.antall_maalinger,
           r.antall_estimater, r.estimatandel_bp, r.antall_paastander,
           r.standardversjon, r.sammenstilt, r.sammenstilt_av
      FROM public.esgrapport r
      JOIN public.rapportperiode p
        ON p.tenant = r.tenant AND p.periode_id = r.periode_id
     WHERE r.tenant = p_tenant
     ORDER BY r.sammenstilt DESC
     LIMIT greatest(p_maks, 1)
$$;

-- `m45_esgfunn` — med hvem som kan lukke hvert av dem.
CREATE FUNCTION m45_esgfunn(p_tenant TEXT, p_maks INT)
RETURNS TABLE (funn_id UUID, funntype TEXT, referanse UUID,
               detaljer TEXT, over_grense BIGINT, apen BOOLEAN,
               forst_sett TIMESTAMPTZ, sist_sett TIMESTAMPTZ,
               lukket_av TEXT, kan_lukkes BOOLEAN)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT f.funn_id, f.funntype, f.referanse, f.detaljer,
           f.over_grense, f.apen, f.forst_sett, f.sist_sett, f.lukket_av,
           NOT public.m45_funn_er_sveipens(f.funntype)
      FROM public.esgfunn f
     WHERE f.tenant = p_tenant
     ORDER BY f.apen DESC, f.sist_sett DESC
     LIMIT greatest(p_maks, 1)
$$;

-- `m45_bildet` — hele modulen i ett kall.
CREATE FUNCTION m45_bildet(p_tenant TEXT)
RETURNS TABLE (perioder INT, apne_perioder INT, maalinger INT,
               estimater INT, paastander INT, faktorer INT,
               gjeldende_faktorer INT, rapporter INT, kilder INT,
               utlopte_kilder INT, apne_funn INT,
               hoyeste_estimatandel_bp INT, har_krav BOOLEAN,
               estimatterskel_bp INT, estimatfrist_dogn INT,
               kilde_gyldig_dogn INT, kravversjon INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    WITH k AS (
        SELECT * FROM public.esgkrav
         WHERE tenant = p_tenant ORDER BY kravversjon DESC LIMIT 1),
    vindu AS (SELECT coalesce((SELECT kilde_gyldig_dogn FROM k), 365) AS d)
    SELECT (SELECT count(*)::INT FROM public.rapportperiode
             WHERE tenant = p_tenant),
           (SELECT count(*)::INT FROM public.rapportperiode
             WHERE tenant = p_tenant AND status = 'apen'),
           (SELECT count(*)::INT FROM public.esgmaaling
             WHERE tenant = p_tenant),
           (SELECT count(*)::INT FROM public.esgmaaling
             WHERE tenant = p_tenant AND er_estimat),
           (SELECT count(*)::INT FROM public.esgpaastand
             WHERE tenant = p_tenant),
           (SELECT count(*)::INT FROM public.utslippsfaktor
             WHERE tenant = p_tenant),
           (SELECT count(*)::INT FROM public.utslippsfaktor
             WHERE tenant = p_tenant
               AND (gyldig_til IS NULL OR gyldig_til >= current_date)),
           (SELECT count(*)::INT FROM public.esgrapport
             WHERE tenant = p_tenant),
           (SELECT count(*)::INT FROM public.kildedokument
             WHERE tenant = p_tenant),
           (SELECT count(*)::INT FROM public.kildedokument d
             WHERE d.tenant = p_tenant
               AND NOT public.m45_kilde_gyldig(d.gyldig_til, d.registrert,
                       (SELECT v.d FROM vindu v), current_date)),
           (SELECT count(*)::INT FROM public.esgfunn
             WHERE tenant = p_tenant AND apen),
           -- DET DYRESTE TALLET I MODULEN: den perioden der mest av
           -- utslippet er gjettet.
           -- ERSTATTEDE TALL TELLER IKKE, og det står her fordi
           -- de tre andre stedene som regner denne andelen
           -- (`m45_perioderegister`, `m45_sammenstill` og sveipen)
           -- filtrerer likt. Første utkast gjorde det ikke HER, og da
           -- ville sammendraget vist en høyere estimatandel enn
           -- rapporten for samme periode — det verste av alt: to tall
           -- som begge ser riktige ut. CodeRabbit fant den 5/9.
           (SELECT max(CASE WHEN s.sum = 0 THEN 0
                            ELSE round(s.est * 10000 / s.sum)::INT END)
              FROM (SELECT m.periode_id,
                           coalesce(sum(m.utslipp_kg), 0) AS sum,
                           coalesce(sum(m.utslipp_kg)
                               FILTER (WHERE m.er_estimat), 0) AS est
                      FROM public.esgmaaling m
                     WHERE m.tenant = p_tenant
                       AND NOT EXISTS (
                           SELECT 1 FROM public.esgmaaling r
                            WHERE r.tenant = p_tenant
                              AND r.erstatter_maaling_id = m.maaling_id)
                     GROUP BY m.periode_id) s),
           (SELECT count(*) > 0 FROM k),
           (SELECT estimatterskel_bp FROM k),
           (SELECT estimatfrist_dogn FROM k),
           (SELECT kilde_gyldig_dogn FROM k),
           (SELECT kravversjon FROM k)
$$;

-- `m45_lukk_funn` — OG DEN NEKTER PÅ SVEIPENS EGNE.
CREATE FUNCTION m45_lukk_funn(p_tenant TEXT, p_funn_id UUID,
                              p_grunn TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_type TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm45_lukk_funn');
    SELECT funntype INTO v_type FROM public.esgfunn
     WHERE tenant = p_tenant AND funn_id = p_funn_id FOR UPDATE;
    IF v_type IS NULL THEN
        RAISE EXCEPTION 'm45_lukk_funn: ukjent funn %', p_funn_id;
    END IF;
    IF public.m45_funn_er_sveipens(v_type) THEN
        RAISE EXCEPTION 'm45_lukk_funn: % lukkes av at tilstanden'
            ' opphoerer, ikke av at noen huker av', v_type;
    END IF;
    UPDATE public.esgfunn f
       SET apen = false, lukket_ts = now(), lukket_av = p_aktor,
           lukket_grunn = p_grunn
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id AND f.apen;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    PERFORM public.m45_evidens(p_tenant, p_funn_id, 'lukk_funn',
        p_aktor, jsonb_build_object('type', v_type));
    RETURN true;
END $$;

-- =====================================================================
-- `m45_sveip_esg` — SVEIPEN SENDER INGEN RAPPORT, ERSTATTER INGEN
-- ESTIMATER OG LUKKER INGEN PERIODE. Den sier fra, og der stopper den.
--
-- TO AV TRE LUKKES HERFRA. `estimat_ikke_erstattet_over_frist`
-- forsvinner når en måling erstatter estimatet,
-- `standardversjon_foreldet_i_apen_periode` når perioden lukkes.
-- `estimatandel_over_terskel_uavklart` KAN lukkes av et menneske —
-- «vi vet, og det står i rapporten» er en avklaring med et navn på.
-- =====================================================================
CREATE FUNCTION m45_sveip_esg(p_maks_tenanter INT)
RETURNS TABLE (tenanter INT, nye INT, oppdaterte INT, lukket INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_t TEXT;
    v_antall INT := 0;
    v_nye INT := 0;
    v_oppdaterte INT := 0;
    v_lukket INT := 0;
    v_n INT;
    v_n2 INT;
    v_n3 INT;
    v_krav RECORD;
BEGIN
    PERFORM set_config('disponit.tenant', '', true);
    FOR v_t IN
        SELECT DISTINCT tenant FROM public.esgkrav
         ORDER BY tenant LIMIT greatest(p_maks_tenanter, 1)
    LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        v_antall := v_antall + 1;
        SELECT * INTO v_krav FROM public.esgkrav
         WHERE tenant = v_t ORDER BY kravversjon DESC LIMIT 1;

        -- 1. ET ESTIMAT SOM ALDRI BLE ERSTATTET.
        --    ET ESTIMAT ER LOV — DET ER MIDLERTIDIGHETEN SOM GJØR DET
        --    LOVLIG. Et estimat som har stått i to år er ikke et
        --    estimat lenger, det er et tall huset har bestemt seg for.
        WITH treff AS (
            SELECT m.maaling_id,
                   (current_date
                    - (m.registrert AT TIME ZONE 'UTC')::DATE)::BIGINT
                   AS dogn
              FROM public.esgmaaling m
             WHERE m.tenant = v_t AND m.er_estimat
               AND (m.registrert AT TIME ZONE 'UTC')::DATE
                   + v_krav.estimatfrist_dogn < current_date
               AND NOT EXISTS (SELECT 1 FROM public.esgmaaling r
                                WHERE r.tenant = v_t
                                  AND r.erstatter_maaling_id = m.maaling_id)),
        satt AS (
            INSERT INTO public.esgfunn
                (tenant, funn_id, funntype, referanse, detaljer,
                 over_grense)
            SELECT v_t, gen_random_uuid(),
                   'estimat_ikke_erstattet_over_frist', t.maaling_id,
                   format('estimatet har staatt i %s doegn uten aa bli'
                          ' erstattet av en maaling', t.dogn), t.dogn
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) DO UPDATE
                SET sist_sett = now(), apen = true,
                    detaljer = EXCLUDED.detaljer,
                    over_grense = EXCLUDED.over_grense,
                    lukket_ts = NULL, lukket_av = NULL, lukket_grunn = NULL
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.esgfunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm45_sveip',
                   lukket_grunn = 'estimatet er erstattet av en maaling'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'estimat_ikke_erstattet_over_frist'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.maaling_id = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 2. EN ÅPEN PERIODE LÅST TIL EN VERSJON SOM ER FORELDET.
        --
        --    KLYNGE 7s DOM, ANVENDT PÅ STANDARDEN SELV: en foreldet
        --    regel ser nøyaktig ut som en riktig regel. Finnes det
        --    faktorer i en NYERE versjon av samme standard, har
        --    standarden flyttet seg mens perioden sto åpen.
        --
        --    SVEIPEN LÅSER IKKE OM. Låsen er dommen — den skal ikke
        --    kunne endres av en nattjobb. Den sier fra, og et menneske
        --    avgjør om perioden skal lukkes og en ny åpnes.
        WITH treff AS (
            SELECT p.periode_id, p.standard, p.standardversjon
              FROM public.rapportperiode p
             WHERE p.tenant = v_t AND p.status = 'apen'
               AND EXISTS (
                   SELECT 1 FROM public.utslippsfaktor f
                    WHERE f.tenant = v_t AND f.standard = p.standard
                      AND f.standardversjon <> p.standardversjon
                      AND f.gyldig_fra > (
                          SELECT min(g.gyldig_fra)
                            FROM public.utslippsfaktor g
                           WHERE g.tenant = v_t
                             AND g.standard = p.standard
                             AND g.standardversjon = p.standardversjon))),
        satt AS (
            INSERT INTO public.esgfunn
                (tenant, funn_id, funntype, referanse, detaljer)
            SELECT v_t, gen_random_uuid(),
                   'standardversjon_foreldet_i_apen_periode',
                   t.periode_id,
                   format('perioden er laast til %s, men det finnes'
                          ' nyere faktorer for %s',
                          t.standardversjon, t.standard)
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) DO UPDATE
                SET sist_sett = now(), apen = true,
                    detaljer = EXCLUDED.detaljer,
                    lukket_ts = NULL, lukket_av = NULL, lukket_grunn = NULL
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.esgfunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm45_sveip',
                   lukket_grunn = 'perioden er lukket'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'standardversjon_foreldet_i_apen_periode'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.periode_id = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 3. EN PERIODE DER FOR MYE AV UTSLIPPET ER GJETTET.
        --
        --    DENNE KAN ET MENNESKE LUKKE — «vi vet, og det staar i
        --    rapporten» er en avklaring med et navn paa. Terskelen er
        --    TENANTENS, og andelen regnes av UTSLIPPET og ikke av
        --    antallet: ti smaa estimater og en stor maaling er ikke
        --    «91 prosent gjettet».
        WITH andel AS (
            SELECT m.periode_id,
                   coalesce(sum(m.utslipp_kg), 0) AS sum_alle,
                   coalesce(sum(m.utslipp_kg)
                       FILTER (WHERE m.er_estimat), 0) AS sum_est
              FROM public.esgmaaling m
             WHERE m.tenant = v_t
               AND NOT EXISTS (SELECT 1 FROM public.esgmaaling r
                                WHERE r.tenant = v_t
                                  AND r.erstatter_maaling_id = m.maaling_id)
             GROUP BY m.periode_id),
        treff AS (
            SELECT a.periode_id,
                   round(a.sum_est * 10000 / a.sum_alle)::BIGINT AS bp
              FROM andel a
             WHERE a.sum_alle > 0
               AND round(a.sum_est * 10000 / a.sum_alle)
                   > v_krav.estimatterskel_bp),
        satt AS (
            INSERT INTO public.esgfunn
                (tenant, funn_id, funntype, referanse, detaljer,
                 over_grense)
            SELECT v_t, gen_random_uuid(),
                   'estimatandel_over_terskel_uavklart', t.periode_id,
                   format('%s basispunkter av utslippet hviler paa'
                          ' estimat', t.bp), t.bp
              FROM treff t
             WHERE NOT EXISTS (
                   SELECT 1 FROM public.esgfunn f
                    WHERE f.tenant = v_t AND NOT f.apen
                      AND f.funntype = 'estimatandel_over_terskel_uavklart'
                      AND f.referanse = t.periode_id)
            ON CONFLICT (tenant, funntype, referanse) DO UPDATE
                SET sist_sett = now(), detaljer = EXCLUDED.detaljer,
                    over_grense = EXCLUDED.over_grense
            RETURNING (xmax = 0) AS ny)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny)
          FROM satt INTO v_n, v_n2;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
    RETURN QUERY SELECT v_antall, v_nye, v_oppdaterte, v_lukket;
END $$;

-- =====================================================================
-- RETTIGHETENE. SP-7.
-- =====================================================================
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT p.oid::regprocedure AS sig
               FROM pg_proc p
              WHERE p.pronamespace = 'public'::regnamespace
                AND p.proname LIKE 'm45\_%'
                AND pg_get_userbyid(p.proowner) = current_user
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', r.sig);
    END LOOP;
END $$;

GRANT EXECUTE ON FUNCTION m45_sett_krav(TEXT, INT, INT, INT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m45_registrer_kilde(TEXT, UUID, TEXT, TEXT,
    DATE, TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m45_apne_periode(TEXT, UUID, TEXT, DATE, DATE,
    TEXT, TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m45_lukk_periode(TEXT, UUID, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m45_registrer_faktor(TEXT, UUID, TEXT, TEXT,
    NUMERIC, TEXT, TEXT, UUID, DATE, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m45_avvikle_faktor(TEXT, UUID, DATE, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m45_registrer_maaling(TEXT, UUID, UUID, TEXT,
    NUMERIC, TEXT, UUID, BOOLEAN, TEXT, UUID, UUID, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m45_registrer_paastand(TEXT, UUID, UUID, INT,
    TEXT, UUID, UUID, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m45_sammenstill(TEXT, UUID, UUID, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m45_maalingene(TEXT, UUID) TO disponit;
GRANT EXECUTE ON FUNCTION m45_paastandene(TEXT, UUID) TO disponit;
GRANT EXECUTE ON FUNCTION m45_perioderegister(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m45_faktorene(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m45_rapportene(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m45_esgfunn(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m45_bildet(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m45_lukk_funn(TEXT, UUID, TEXT, TEXT)
    TO disponit;

-- SVEIPEROLLEN NÅR ÉN FUNKSJON, OG BARE DEN (111s form).
GRANT EXECUTE ON FUNCTION m45_sveip_esg(INT) TO disponit_esgsveip;

RESET ROLE;

-- =====================================================================
-- M-36s FUNNKATALOG (132), FJERDE OG SISTE GANG I DENNE KLYNGEN.
-- =====================================================================
INSERT INTO m36_funnregister
    (relasjon, modul, typekolonne, apenform, begrunnelse)
VALUES
    ('esgfunn', 'm45_esg', 'funntype', 'apen_kolonne', 'husets form')
ON CONFLICT (relasjon) DO NOTHING;
GRANT SELECT ON esgfunn TO disponit_optimalisator_eier;
