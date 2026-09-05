-- =====================================================================
-- M-36 BEDRIFTSOPTIMALISATOR (v1) — KLYNGE 8s SISTE.
-- =====================================================================
--
-- V1-DOMMEN: MODULEN RANGERER TILTAK OG STOPPER DER. Den iverksetter
-- ingenting, overstyrer ingen annen moduls grense, og kan ikke utvide
-- sin egen fullmakt.
--
-- ---------------------------------------------------------------------
-- VAKTSETNINGEN ER EN ADVARSEL, IKKE EN SELVFØLGE.
--
-- «Kan aldri utvide egen fullmakt.» Klynge 8-fundamentet skrev hvorfor
-- den formuleringen finnes, og det er verdt å gjenta her:
--
--   EN OPTIMALISATOR SOM FINNER AT DEN BESTE FORBEDRINGEN ER «GI M-36
--   LOV TIL Å GJØRE X», ER IKKE ØDELAGT. DEN GJØR NØYAKTIG DET DEN BLE
--   BEDT OM.
--
-- Derfor er fullmaktsutvidelse gjort UREPRESENTERBAR, ikke frarådet:
-- modulrollen har ingen rettighet på `policyer`, `policyutkast` eller
-- `policyaktivering`, og det finnes ingen dør som skriver dit. Porten
-- måler BEGGE fravær — en dør uten rettighet og en rettighet uten dør
-- er hver for seg en halv sperre.
--
-- ---------------------------------------------------------------------
-- «KORRELASJON PRESENTERES IKKE SOM ÅRSAK» ER ET KRAV TIL
-- DATAMODELLEN, IKKE TIL TEKSTEN PÅ SKJERMEN.
--
-- Et tiltaksforslag bærer `grunnlagstype` med et LUKKET SETT:
-- `korrelasjon`, `eksperiment`, `regel`. En rangering som blandet dem
-- uten å si hvilken som er hvilken, er nøyaktig den påstanden vakten
-- forbyr — og en advarsel i en hjelpetekst ville ikke hindret den.
--
-- Kolonnen er `NOT NULL` uten standardverdi. Døra må ta stilling.
--
-- ---------------------------------------------------------------------
-- HVA MODULEN FAKTISK LESER — og hvorfor det ikke er en KPI-katalog.
--
-- Katalogen sier at M-36s input er «KPI-katalog, modulresultater,
-- strategi, budsjett, risiko og eksperimenthistorikk». KLYNGE
-- 8-FUNDAMENTET SLO FAST AT DET LAGET IKKE FINNES, og avklaringen står
-- der: v1 leser DE ÅPNE FUNNENE fra modulregistrene. Det er det eneste
-- tverrgående, standardiserte signalet huset faktisk har — og det er
-- et ærlig et: et åpent funn er noe en modul har MÅLT og et menneske
-- ikke har lukket.
--
-- MEN REGISTRENE DELER IKKE FORMEN HELT, og det ble målt mot basen før
-- første linje kode:
--
--   * 33 registre står i `m36_funnregister`, og 32 av dem LESES:
--     `merkevarefunn` står der for å være DEKKET, men er en
--     observasjonstabell uten funntype.
--   * 30 av de 33 har husets form: `tenant`, `funntype` og `apen`.
--   * `kvalitetsfunn` (M-3, 092) har INGEN `apen` — hver rad ER et
--     åpent funn, og lukking skjer ved at raden forsvinner.
--   * `retensjonsfunn` bruker `lukket_maaling IS NULL` i stedet.
--   * M-55 skiller OBSERVASJON fra VARSEL: `merkevarefunn` har verken
--     `apen` eller `funntype`, mens `merkevarevarsel` har `apen` og
--     `varseltype`.
--
-- EN OPTIMALISATOR SOM ANTOK ÉN FORM VILLE LEST 30 AV 32 OG MELDT
-- RENT FOR DE TO SISTE. Det er samme feilform som den blinde sveipen i 130:
-- den ser ut som en vellykket kjøring. Derfor bærer modulen et
-- EKSPLISITT register over hvilke tabeller den leser og hvordan «åpen»
-- er kodet i hver — og en port faller når en ny `*funn`-tabell dukker
-- opp utenfor registeret. DEN NESTE MODULENS FUNNREGISTER KAN IKKE
-- FALLE UT AV SYNET I STILLHET.
--
-- ---------------------------------------------------------------------
-- PORTEFØLJESTOPPEN STANSER M-36, IKKE PORTEFØLJEN — og det står i
-- klartekst fordi navnet lover mer.
--
-- Vaktsetningen krever at en porteføljestopp er «tilgjengelig», og
-- `portefoljestopp_uten_virkning` krever at den VIRKER. Men det eneste
-- M-36 lovlig kan stanse, er sin egen produksjon: å stanse en annen
-- modul ville vært `modulen_overstyrte_en_annen_moduls_grense`.
--
-- Virkningen er derfor ekte og målbar: med aktiv stopp NEKTER
-- `m36_rangere`, og ingen ny rangering blir til. Det er ikke en
-- pynteknapp — men det er heller ikke en nødbrems for driften, og en
-- flate som lot som noe annet ville løyet.
-- =====================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_optimalisator_eier') THEN
        RAISE EXCEPTION 'rollen disponit_optimalisator_eier mangler —'
            ' kjør deploy/staging/oppsett-postgresql.sh først';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_optimalisatorsveip') THEN
        RAISE EXCEPTION 'rollen disponit_optimalisatorsveip mangler —'
            ' kjør deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_optimalisator_eier;
GRANT INSERT ON revisjonslogg TO disponit_optimalisator_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_optimalisator_eier;
RESET ROLE;

-- ---------------------------------------------------------------------
-- TENANTENS EGNE GRENSER.
-- ---------------------------------------------------------------------
CREATE TABLE optimaliseringskrav (
    tenant TEXT PRIMARY KEY CHECK (length(btrim(tenant)) > 0),
    -- HORISONTEN EN EFFEKT MÅLES OVER. Et tiltak uten en dato det kan
    -- etterprøves mot er et forslag ingen kan si var feil.
    horisont_uker INT NOT NULL DEFAULT 12
        CHECK (horisont_uker BETWEEN 1 AND 104),
    maalefrist_dogn INT NOT NULL DEFAULT 14
        CHECK (maalefrist_dogn BETWEEN 1 AND 180),
    -- HVOR MANGE TILTAK EN RANGERING TAR MED. En liste ingen rekker å
    -- lese er en liste ingen tar stilling til.
    maks_i_rangering INT NOT NULL DEFAULT 10
        CHECK (maks_i_rangering BETWEEN 1 AND 100),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon > 0),
    -- IDEMPOTENSNØKKELEN LEVER PÅ RADEN (119s lærdom).
    siste_nokkel TEXT,
    satt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    satt_av TEXT NOT NULL CHECK (length(btrim(satt_av)) > 0)
);

-- ---------------------------------------------------------------------
-- MODELLEN, MED BASISLINJEN SKREVET UT (121s dom, 128/130s form).
-- ---------------------------------------------------------------------
CREATE TABLE optimaliseringsmodell (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    modell_id UUID NOT NULL,
    PRIMARY KEY (tenant, modell_id),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    versjon TEXT NOT NULL CHECK (versjon ~ '[^[:space:]]'),
    metode TEXT NOT NULL CHECK (length(btrim(metode)) >= 16),
    -- BASISLINJEN modellen måles mot. Uten et navn på den er «var
    -- rangeringen bedre enn ingen rangering?» et spørsmål uten
    -- referanse.
    baselinje TEXT NOT NULL CHECK (length(btrim(baselinje)) > 0),
    -- MODELLENS USIKKERHET, I BASISPUNKTER. Den er en EGENSKAP VED
    -- MODELLEN og ikke ved tenanten: to modellversjoner kan lese
    -- samme anslag og ha ulik tillit til det. Den er `NOT NULL` og
    -- minst 1 — en modell som påstår null usikkerhet er en modell som
    -- påstår å vite framtiden.
    usikkerhet_bp INT NOT NULL
        CHECK (usikkerhet_bp BETWEEN 1 AND 10000),
    gyldig_fra DATE NOT NULL,
    gyldig_til DATE,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (length(btrim(opprettet_av)) > 0),
    CONSTRAINT optimaliseringsmodell_gyldighet CHECK (
        gyldig_til IS NULL OR gyldig_til >= gyldig_fra),
    CONSTRAINT optimaliseringsmodell_versjon_unik
        UNIQUE (tenant, versjon)
);

CREATE INDEX optimaliseringsmodell_gjeldende
    ON optimaliseringsmodell (tenant, gyldig_fra DESC)
    WHERE gyldig_til IS NULL;

-- ---------------------------------------------------------------------
-- TILTAKSFORSLAGET — DER `korrelasjon_presentert_som_aarsak` STOPPES.
--
-- `grunnlagstype` ER `NOT NULL` UTEN STANDARDVERDI. Et forslag som
-- ikke sier hva det hviler på, er et forslag som later som det hviler
-- på noe sterkere enn det gjør.
--
-- `reversibilitet` ER `NOT NULL` av samme grunn (M-15s dom, 128): et
-- tiltak ingen har vurdert reversibiliteten av er et tiltak ingen kan
-- angre — og det er nettopp de som ser billigst ut.
--
-- `kilde_modul` OG `kilde_funntype` BÆRER HVOR SIGNALET KOM FRA. Uten
-- dem kunne et forslag ikke spores tilbake til den målingen som
-- utløste det, og «hvorfor står dette på lista?» ville vært
-- ubesvarlig.
-- ---------------------------------------------------------------------
CREATE TABLE tiltaksforslag (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    tiltak_id UUID NOT NULL,
    PRIMARY KEY (tenant, tiltak_id),
    beskrivelse TEXT NOT NULL CHECK (length(btrim(beskrivelse)) >= 16),
    -- LUKKET SETT, OG HELE VAKTSETNINGEN LEVER HER.
    grunnlagstype TEXT NOT NULL
        CONSTRAINT tiltaksforslag_grunnlagstype_lukket
        CHECK (grunnlagstype IN ('korrelasjon', 'eksperiment',
                                 'regel')),
    grunnlag TEXT NOT NULL CHECK (length(btrim(grunnlag)) >= 16),
    reversibilitet TEXT NOT NULL
        CONSTRAINT tiltaksforslag_reversibilitet_lukket
        CHECK (reversibilitet IN ('reversibel', 'delvis_reversibel',
                                  'irreversibel')),
    -- ANSLAGET ER ET MENNESKES, OG DET STÅR SOM DET.
    --
    -- Huset kan ikke prise effekten av et tiltak — det er samme
    -- fravær som gjorde at M-15 måtte la et menneske registrere
    -- forpliktelser (128). En optimalisator som «utledet» besparelsen
    -- ville rangert på tall den fant på, og et oppfunnet tall øverst
    -- på en tiltaksliste er verre enn ingen liste: det ser like
    -- presist ut som de riktige.
    --
    -- ØRE, SOM RESTEN AV HUSET. Positivt er forbedring. Null er
    -- forbudt: et tiltak uten anslått effekt er ikke et tiltak, det
    -- er en observasjon.
    anslag_effekt_ore BIGINT NOT NULL
        CHECK (anslag_effekt_ore <> 0),
    -- HVOR SIGNALET KOM FRA.
    kilde_modul TEXT NOT NULL CHECK (kilde_modul ~ '[^[:space:]]'),
    kilde_funntype TEXT NOT NULL
        CHECK (kilde_funntype ~ '[^[:space:]]'),
    -- STATUSSETTET HAR INGEN `iverksatt`, OG DET ER HELE V1-DOMMEN.
    -- Et tiltak kan bli vurdert eller avvist av et menneske, og der
    -- stopper M-36. Utførelsen går gjennom modulen som EIER
    -- handlingen, av et menneske, på M-41s policykontrollerte vei —
    -- og den veien vet ikke at denne tabellen finnes.
    status TEXT NOT NULL DEFAULT 'foreslatt'
        CONSTRAINT tiltaksforslag_status_lukket
        CHECK (status IN ('foreslatt', 'vurdert', 'avvist')),
    vurdert_ts TIMESTAMPTZ,
    vurdert_av TEXT,
    vurderingsnotat TEXT,
    CONSTRAINT tiltaksforslag_vurdering_har_navn CHECK (
        status = 'foreslatt'
        OR (vurdert_ts IS NOT NULL AND vurdert_av IS NOT NULL
            AND length(btrim(vurdert_av)) > 0)),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (length(btrim(opprettet_av)) > 0)
);

CREATE INDEX tiltaksforslag_apne
    ON tiltaksforslag (tenant, status, opprettet DESC);

-- ---------------------------------------------------------------------
-- PORTEFØLJESTOPPEN.
--
-- APPEND-ONLY, med `opphevet_ts` som eneste lovlige endring. En stopp
-- som kunne slettes ville gjort «var den på?» ubesvarlig i ettertid —
-- og det er nettopp da spørsmålet stilles.
--
-- STOPPEN HAR ET NAVN OG EN BEGRUNNELSE. En stopp uten dem er en
-- tilstand ingen kan forklare, og en oppheving uten dem er en
-- beslutning ingen tar ansvar for.
-- ---------------------------------------------------------------------
CREATE TABLE portefoljestopp (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    stopp_id UUID NOT NULL,
    PRIMARY KEY (tenant, stopp_id),
    begrunnelse TEXT NOT NULL CHECK (length(btrim(begrunnelse)) >= 16),
    satt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    satt_av TEXT NOT NULL CHECK (length(btrim(satt_av)) > 0),
    opphevet_ts TIMESTAMPTZ,
    opphevet_av TEXT,
    opphevingsbegrunnelse TEXT,
    CONSTRAINT portefoljestopp_oppheving_har_navn CHECK (
        opphevet_ts IS NULL
        OR (opphevet_av IS NOT NULL
            AND length(btrim(opphevet_av)) > 0
            AND opphevingsbegrunnelse IS NOT NULL
            AND length(btrim(opphevingsbegrunnelse)) >= 16))
);

-- ÉN AKTIV STOPP OM GANGEN. To samtidige ville gjort «er porteføljen
-- stoppet?» til et spørsmål med to svar.
CREATE UNIQUE INDEX portefoljestopp_en_aktiv
    ON portefoljestopp (tenant) WHERE opphevet_ts IS NULL;

-- ---------------------------------------------------------------------
-- RANGERINGEN — APPEND-ONLY, fordi `rangering_overskrevet` er en
-- invariant og ikke en anbefaling.
--
-- En rangering er en PÅSTAND AVGITT PÅ ET TIDSPUNKT, av en navngitt
-- modellversjon, på et kjent grunnlag. Kunne den redigeres, ville
-- enhver effektmåling vært en sammenligning mot noe som er endret
-- etterpå — og «tok vi feil?» ville alltid hatt svaret nei.
--
-- `stopp_aktiv` STÅR PÅ RADEN. Den er alltid `false` — en rangering
-- kan ikke lages med aktiv stopp, døra nekter. Kolonnen finnes fordi
-- fraværet av rader IKKE er evidens: uten den kunne ingen skille «det
-- var stoppet» fra «ingen ba om en rangering».
-- ---------------------------------------------------------------------
CREATE TABLE rangering (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    rangering_id UUID NOT NULL,
    PRIMARY KEY (tenant, rangering_id),
    laget_dato DATE NOT NULL,
    horisont_uker INT NOT NULL CHECK (horisont_uker BETWEEN 1 AND 104),
    modell_id UUID NOT NULL,
    -- SNAPSHOTET, ikke en fremmednøkkel til noe som kan endres.
    modellversjon TEXT NOT NULL CHECK (modellversjon ~ '[^[:space:]]'),
    baselinje TEXT NOT NULL CHECK (length(btrim(baselinje)) > 0),
    -- HVA RANGERINGEN FAKTISK SÅ. Antall åpne funn, og hvor mange
    -- registre som ble lest. Uten dem kan «var grunnlaget komplett?»
    -- ikke besvares i ettertid.
    grunnlag_apne_funn INT NOT NULL CHECK (grunnlag_apne_funn >= 0),
    grunnlag_registre INT NOT NULL CHECK (grunnlag_registre > 0),
    stopp_aktiv BOOLEAN NOT NULL DEFAULT false,
    gjelder_til DATE NOT NULL,
    CONSTRAINT rangering_horisont_stemmer CHECK (
        gjelder_til = laget_dato + (horisont_uker * 7)),
    laget_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    laget_av TEXT NOT NULL CHECK (length(btrim(laget_av)) > 0)
);

CREATE INDEX rangering_ferskeste
    ON rangering (tenant, laget_dato DESC);

-- ---------------------------------------------------------------------
-- RANGERINGSPOSTEN — ETT TILTAK PÅ ÉN PLASS, MED SITT INTERVALL.
--
-- `nedre_effekt` og `ovre_effekt` er `NOT NULL`. Det er invarianten
-- `prognose_uten_intervall`, gjort umulig i stedet for oppdaget: et
-- punktestimat uten spenn ER et tall som påstår å være et faktum, og
-- en rangering av slike tall er en rekkefølge som later som den er
-- sikker.
--
-- `grunnlagstype` KOPIERES HIT VED RANGERING. Et join ville gitt
-- dagens verdi, ikke den som gjaldt da rangeringen ble avgitt — og
-- `korrelasjon_presentert_som_aarsak` handler om hva vi PÅSTO, ikke om
-- hva som står nå.
-- ---------------------------------------------------------------------
CREATE TABLE rangeringspost (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    rangering_id UUID NOT NULL,
    plass INT NOT NULL CHECK (plass BETWEEN 1 AND 100),
    PRIMARY KEY (tenant, rangering_id, plass),
    tiltak_id UUID NOT NULL,
    -- ØRE, SOM RESTEN AV HUSET. Positivt er forbedring.
    forventet_effekt_ore BIGINT NOT NULL,
    nedre_effekt_ore BIGINT NOT NULL,
    ovre_effekt_ore BIGINT NOT NULL,
    CONSTRAINT rangeringspost_intervall_omslutter CHECK (
        nedre_effekt_ore <= forventet_effekt_ore
        AND forventet_effekt_ore <= ovre_effekt_ore),
    -- OG BÅNDET HAR ALDRI BREDDE NULL (131s lærdom, tatt med fra
    -- fødselen). Et intervall med bredde null er et PUNKT som later
    -- som det er et intervall, og en `NOT NULL`-kolonne fanger det
    -- ikke: null er en gyldig verdi.
    CONSTRAINT rangeringspost_intervall_har_bredde CHECK (
        nedre_effekt_ore < ovre_effekt_ore),
    grunnlagstype TEXT NOT NULL
        CONSTRAINT rangeringspost_grunnlagstype_lukket
        CHECK (grunnlagstype IN ('korrelasjon', 'eksperiment',
                                 'regel')),
    reversibilitet TEXT NOT NULL
        CONSTRAINT rangeringspost_reversibilitet_lukket
        CHECK (reversibilitet IN ('reversibel', 'delvis_reversibel',
                                  'irreversibel')),
    ukeslutt DATE NOT NULL,
    CONSTRAINT rangeringspost_rangering_fk
        FOREIGN KEY (tenant, rangering_id)
        REFERENCES rangering (tenant, rangering_id),
    CONSTRAINT rangeringspost_tiltak_fk
        FOREIGN KEY (tenant, tiltak_id)
        REFERENCES tiltaksforslag (tenant, tiltak_id),
    -- ETT TILTAK ÉN GANG PER RANGERING. To plasser til samme tiltak
    -- ville gjort rekkefølgen meningsløs.
    CONSTRAINT rangeringspost_tiltak_unik
        UNIQUE (tenant, rangering_id, tiltak_id)
);

-- ---------------------------------------------------------------------
-- EFFEKTMÅLINGEN — DET SOM FAKTISK SKJEDDE.
--
-- APPEND-ONLY OG UKORRIGERBAR (130s form). En måling som lot seg
-- justere er en måling som alltid bekrefter.
--
-- `avvik_ore` ER GENERERT. En kaller som fikk oppgi sitt eget avvik
-- kunne oppgi null.
-- ---------------------------------------------------------------------
CREATE TABLE effektmaaling (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    rangering_id UUID NOT NULL,
    plass INT NOT NULL CHECK (plass BETWEEN 1 AND 100),
    PRIMARY KEY (tenant, rangering_id, plass),
    faktisk_effekt_ore BIGINT NOT NULL,
    forventet_effekt_ore BIGINT NOT NULL,
    innenfor_intervall BOOLEAN NOT NULL,
    avvik_ore BIGINT GENERATED ALWAYS AS (
        abs(faktisk_effekt_ore - forventet_effekt_ore)) STORED,
    maalt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    maalt_av TEXT NOT NULL CHECK (length(btrim(maalt_av)) > 0),
    CONSTRAINT effektmaaling_post_fk
        FOREIGN KEY (tenant, rangering_id, plass)
        REFERENCES rangeringspost (tenant, rangering_id, plass)
);

-- ---------------------------------------------------------------------
-- FUNNENE. Lukket funntypesett, ett åpent funn per
-- (tenant, funntype, referanse).
-- ---------------------------------------------------------------------
CREATE TABLE optimaliseringsfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    funn_id UUID NOT NULL,
    PRIMARY KEY (tenant, funn_id),
    funntype TEXT NOT NULL
        CONSTRAINT optimaliseringsfunn_type_lukket CHECK (funntype IN (
            'rangering_uten_maaling',
            'tiltak_uten_reversibilitet',
            'korrelasjon_alene_paa_topp',
            'stopp_staar_uten_oppheving')),
    referanse TEXT NOT NULL CHECK (referanse ~ '[^[:space:]]'),
    detaljer TEXT NOT NULL CHECK (detaljer ~ '[^[:space:]]'),
    over_grense BIGINT NOT NULL DEFAULT 0,
    apen BOOLEAN NOT NULL DEFAULT true,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 125s DOM, TATT MED FRA FØDSELEN.
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT,
    lukket_begrunnelse TEXT,
    CONSTRAINT optimaliseringsfunn_lukking_har_navn CHECK (
        apen
        OR (lukket_ts IS NOT NULL AND lukket_av IS NOT NULL
            AND length(btrim(lukket_av)) > 0)),
    CONSTRAINT optimaliseringsfunn_apen_er_ulukket CHECK (
        NOT apen OR (lukket_ts IS NULL AND lukket_av IS NULL))
);

CREATE UNIQUE INDEX optimaliseringsfunn_ett_apent
    ON optimaliseringsfunn (tenant, funntype, referanse) WHERE apen;

-- =====================================================================
-- APPEND-ONLY-VAKTENE. Gjelder også migrator (130s dom).
-- =====================================================================
CREATE OR REPLACE FUNCTION m36_evidensvakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION '%: % avvist — en rangering eller måling som kan'
        ' endres i ettertid er ikke evidens',
        TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE ALL ON FUNCTION m36_evidensvakt() FROM PUBLIC;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['rangering', 'rangeringspost',
                             'effektmaaling'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS m36_evidensvakt'
                       ' ON public.%I', t);
        EXECUTE format('CREATE TRIGGER m36_evidensvakt'
                       ' BEFORE UPDATE OR DELETE ON public.%I'
                       ' FOR EACH ROW'
                       ' EXECUTE FUNCTION public.m36_evidensvakt()', t);
        EXECUTE format('DROP TRIGGER IF EXISTS m36_ingen_truncate'
                       ' ON public.%I', t);
        EXECUTE format('CREATE TRIGGER m36_ingen_truncate'
                       ' BEFORE TRUNCATE ON public.%I'
                       ' FOR EACH STATEMENT'
                       ' EXECUTE FUNCTION public.avvis_endring()', t);
    END LOOP;
END $$;

-- MODELLENS IDENTITET ER FROSSET; BARE `gyldig_til` KAN SETTES.
CREATE OR REPLACE FUNCTION m36_modellvakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'optimaliseringsmodell: sletting avvist — en'
            ' modell rangeringer peker på kan ikke forsvinne'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.modell_id IS DISTINCT FROM OLD.modell_id
       OR NEW.navn IS DISTINCT FROM OLD.navn
       OR NEW.versjon IS DISTINCT FROM OLD.versjon
       OR NEW.metode IS DISTINCT FROM OLD.metode
       OR NEW.baselinje IS DISTINCT FROM OLD.baselinje
       OR NEW.gyldig_fra IS DISTINCT FROM OLD.gyldig_fra
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av THEN
        RAISE EXCEPTION 'optimaliseringsmodell: identiteten er'
            ' frosset — bare gyldig_til kan settes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.gyldig_til IS NOT NULL
       AND NEW.gyldig_til IS DISTINCT FROM OLD.gyldig_til THEN
        RAISE EXCEPTION 'optimaliseringsmodell: en avviklet modell kan'
            ' ikke avvikles på nytt eller gjenoppvekkes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m36_modellvakt() FROM PUBLIC;
DROP TRIGGER IF EXISTS m36_modellvakt ON optimaliseringsmodell;
CREATE TRIGGER m36_modellvakt
    BEFORE UPDATE OR DELETE ON optimaliseringsmodell
    FOR EACH ROW EXECUTE FUNCTION m36_modellvakt();

-- TILTAKET FÅR BARE VURDERES. Beskrivelsen, grunnlagstypen,
-- reversibiliteten og kilden er FROSSET: kunne de endres etter at noen
-- hadde sett forslaget, ville vurderingen gjeldt et annet tiltak — og
-- `grunnlagstype` kunne blitt skrevet om fra `korrelasjon` til `regel`
-- i ettertid, som er nøyaktig løgnen vaktsetningen forbyr.
CREATE OR REPLACE FUNCTION m36_tiltaksvakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'tiltaksforslag: sletting avvist — et forslag'
            ' en rangering peker på kan ikke forsvinne'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.beskrivelse IS DISTINCT FROM OLD.beskrivelse
       OR NEW.grunnlagstype IS DISTINCT FROM OLD.grunnlagstype
       OR NEW.grunnlag IS DISTINCT FROM OLD.grunnlag
       OR NEW.reversibilitet IS DISTINCT FROM OLD.reversibilitet
       OR NEW.kilde_modul IS DISTINCT FROM OLD.kilde_modul
       OR NEW.kilde_funntype IS DISTINCT FROM OLD.kilde_funntype
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av THEN
        RAISE EXCEPTION 'tiltaksforslag: forslagets innhold er'
            ' frosset — bare vurderingen kan settes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- VURDERINGEN ER ENVEIS. Et vurdert tiltak som kunne settes
    -- tilbake til `foreslatt` ville gjort «har noen sett på dette?»
    -- ubesvarlig.
    IF OLD.status <> 'foreslatt' AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'tiltaksforslag: % er alt vurdert som %',
            OLD.tiltak_id, OLD.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m36_tiltaksvakt() FROM PUBLIC;
DROP TRIGGER IF EXISTS m36_tiltaksvakt ON tiltaksforslag;
CREATE TRIGGER m36_tiltaksvakt
    BEFORE UPDATE OR DELETE ON tiltaksforslag
    FOR EACH ROW EXECUTE FUNCTION m36_tiltaksvakt();

-- STOPPEN KAN BARE OPPHEVES. Å slette en stopp ville gjort «var den
-- på?» ubesvarlig i ettertid — og det er nettopp da spørsmålet
-- stilles.
CREATE OR REPLACE FUNCTION m36_stoppvakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'portefoljestopp: sletting avvist — en stopp'
            ' som kan slettes er en stopp ingen kan bevise sto'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.begrunnelse IS DISTINCT FROM OLD.begrunnelse
       OR NEW.satt_ts IS DISTINCT FROM OLD.satt_ts
       OR NEW.satt_av IS DISTINCT FROM OLD.satt_av THEN
        RAISE EXCEPTION 'portefoljestopp: settingen er frosset — bare'
            ' opphevingen kan skrives'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.opphevet_ts IS NOT NULL THEN
        RAISE EXCEPTION 'portefoljestopp: alt opphevet %',
            OLD.opphevet_ts
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m36_stoppvakt() FROM PUBLIC;
DROP TRIGGER IF EXISTS m36_stoppvakt ON portefoljestopp;
CREATE TRIGGER m36_stoppvakt
    BEFORE UPDATE OR DELETE ON portefoljestopp
    FOR EACH ROW EXECUTE FUNCTION m36_stoppvakt();

-- =====================================================================
-- RADVAKTEN. `FORCE`, ellers er eieren unntatt og
-- `tenantlekkasje_i_tiltaksregister` en invariant uten håndhevelse.
-- =====================================================================
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['optimaliseringskrav',
                             'optimaliseringsmodell',
                             'tiltaksforslag', 'portefoljestopp',
                             'rangering', 'rangeringspost',
                             'effektmaaling',
                             'optimaliseringsfunn'] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL'
                       ' SECURITY', t);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL'
                       ' SECURITY', t);
        IF NOT EXISTS (SELECT 1 FROM pg_policy
                        WHERE polrelid = format('public.%I', t)::regclass
                          AND polname = 'tenant_isolasjon') THEN
            EXECUTE format(
                'CREATE POLICY tenant_isolasjon ON public.%I'
                ' USING      (tenant = current_setting(''disponit.tenant'', true))'
                ' WITH CHECK (tenant = current_setting(''disponit.tenant'', true))',
                t);
        END IF;
        -- Dørene er SECURITY DEFINER og løper som modulrollen, så
        -- uten denne granten møter enhver dør «permission denied» på
        -- sin egen tabell (130s lærdom fra riggen).
        EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO'
                       ' disponit_optimalisator_eier', t);
    END LOOP;
END $$;

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER (111s form): bare
-- `FOR SELECT`, bare til eieren, og bare når tenantkonteksten er TOM.
-- Sveipen trenger tenantlisten FØR den setter konteksten.
CREATE POLICY m36_sveip_tenantliste ON optimaliseringskrav
    FOR SELECT TO disponit_optimalisator_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- HISTORIKKTABELLENE FÅR IKKE UPDATE. En rettighet som ikke finnes er
-- sterkere enn en trigger som nekter: triggere kan slås av av
-- tabellens eier, rettigheter må gis på nytt.
REVOKE UPDATE ON public.rangering FROM disponit_optimalisator_eier;
REVOKE UPDATE ON public.rangeringspost
    FROM disponit_optimalisator_eier;
REVOKE UPDATE ON public.effektmaaling
    FROM disponit_optimalisator_eier;

-- `optimaliseringsmodell` FÅR BARE ENDRE `gyldig_til` (121s dom).
REVOKE UPDATE ON public.optimaliseringsmodell
    FROM disponit_optimalisator_eier;
GRANT UPDATE (gyldig_til) ON public.optimaliseringsmodell
    TO disponit_optimalisator_eier;

-- `tiltaksforslag` FÅR BARE VURDERES.
REVOKE UPDATE ON public.tiltaksforslag
    FROM disponit_optimalisator_eier;
GRANT UPDATE (status, vurdert_ts, vurdert_av, vurderingsnotat)
    ON public.tiltaksforslag TO disponit_optimalisator_eier;

-- `portefoljestopp` FÅR BARE OPPHEVES.
REVOKE UPDATE ON public.portefoljestopp
    FROM disponit_optimalisator_eier;
GRANT UPDATE (opphevet_ts, opphevet_av, opphevingsbegrunnelse)
    ON public.portefoljestopp TO disponit_optimalisator_eier;

-- INGEN AV TABELLENE FÅR SLETTES. `DELETE` står ikke i noen GRANT
-- over — lista er `SELECT, INSERT, UPDATE`. Det står her fordi et
-- FRAVÆR er lettere å overse enn en setning, og porten leser begge.

CREATE TABLE m36_funnregister (
    relasjon TEXT PRIMARY KEY
        CHECK (relasjon ~ '^[a-z][a-z0-9_]*$'),
    -- Hvilken modul registeret hører til. Bæres videre til
    -- `tiltaksforslag.kilde_modul`, slik at et forslag kan spores
    -- tilbake til målingen som utløste det.
    modul TEXT NOT NULL CHECK (modul ~ '[^[:space:]]'),
    -- Kolonnen som navngir funntypen.
    typekolonne TEXT NOT NULL
        CHECK (typekolonne ~ '^[a-z][a-z0-9_]*$'),
    -- HVORDAN «ÅPEN» ER KODET. Lukket sett, fordi en ukjent form er
    -- en feil og ikke en ny variant som stille oppstår.
    apenform TEXT NOT NULL
        CONSTRAINT m36_funnregister_apenform_lukket
        CHECK (apenform IN ('apen_kolonne', 'alle_rader_apne',
                            'lukket_maaling_null')),
    begrunnelse TEXT NOT NULL CHECK (length(btrim(begrunnelse)) > 0)
);

ALTER TABLE m36_funnregister ENABLE ROW LEVEL SECURITY;
-- INGEN TENANTKOLONNE, OG DERFOR EN EGEN POLICY: registeret er husets,
-- ikke tenantens. Det inneholder TABELLNAVN, ikke kundedata.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policy
                    WHERE polrelid = 'm36_funnregister'::regclass
                      AND polname = 'husets_register') THEN
        CREATE POLICY husets_register ON m36_funnregister
            FOR SELECT USING (true);
    END IF;
END $$;
GRANT SELECT ON m36_funnregister TO disponit_optimalisator_eier;

-- =====================================================================
-- HERFRA EIES DØRENE AV OPTIMALISATOREIEREN.
-- =====================================================================
SET LOCAL ROLE disponit_optimalisator_eier;

-- ---------------------------------------------------------------------
-- REGISTERET LESES AV DENNE DØRA.
--
-- DETTE ER MODULENS VIKTIGSTE FUNKSJON, og den er en liste.
--
-- Huset har 31 funnregistre. 28 av dem har `tenant`, `funntype` og
-- `apen`. TRE HAR DET IKKE, og det ble målt mot basen før første linje
-- kode:
--
--   * `kvalitetsfunn` (M-3, 092) — ingen `apen`. HVER RAD ER ET ÅPENT
--     FUNN; lukking skjer ved at raden forsvinner.
--   * `retensjonsfunn` — `lukket_maaling IS NULL` betyr åpen.
--   * `merkevarevarsel` (M-55) — har `apen`, men kolonnen heter
--     `varseltype`, ikke `funntype`. Og `merkevarefunn` er
--     OBSERVASJONER, ikke funn: den har verken.
--
-- EN OPTIMALISATOR SOM ANTOK ÉN FORM VILLE LEST 30 AV 32 OG MELDT
-- RENT FOR DE TO SISTE. Det er samme feilform som den blinde sveipen
-- i 130: den ser ut som en vellykket kjøring.
--
-- Registeret er derfor EKSPLISITT, og `m36_udekkede_registre` under
-- finner tabeller som ikke står her. Porten som kaller den faller når
-- en ny modul legger til sitt funnregister — DEN NESTE MODULENS FUNN
-- KAN IKKE FALLE UT AV SYNET I STILLHET.
-- ---------------------------------------------------------------------

-- FINNER FUNNREGISTRE SOM IKKE STÅR I REGISTERET.
--
-- STABLE og ikke IMMUTABLE: den leser katalogen, som endrer seg.
CREATE FUNCTION m36_udekkede_registre()
RETURNS TABLE (relasjon TEXT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT c.relname::TEXT
      FROM pg_class c
     WHERE c.relkind = 'r'
       AND c.relnamespace = 'public'::regnamespace
       AND c.relname LIKE '%funn%'
       -- M-36s egne tabeller er ikke et signal M-36 leser.
       AND c.relname NOT LIKE 'm36\_%'
       AND c.relname <> 'optimaliseringsfunn'
       AND NOT EXISTS (SELECT 1 FROM public.m36_funnregister r
                        WHERE r.relasjon = c.relname)
     ORDER BY 1
$$;
REVOKE ALL ON FUNCTION m36_udekkede_registre() FROM PUBLIC;

RESET ROLE;

INSERT INTO m36_funnregister
    (relasjon, modul, typekolonne, apenform, begrunnelse)
VALUES
    ('adressefunn', 'm19_adresse', 'funntype', 'apen_kolonne', 'husets form'),
    ('anbudsfunn', 'm46_anbud', 'funntype', 'apen_kolonne', 'husets form'),
    ('avstemmingsfunn', 'm13_avstemming', 'funntype', 'apen_kolonne', 'husets form'),
    ('begrepsfunn', 'm9_begrep', 'funntype', 'apen_kolonne', 'husets form'),
    ('betalingsfunn', 'm41_betaling', 'funntype', 'apen_kolonne', 'husets form'),
    ('ehffunn', 'm54_ehf', 'funntype', 'apen_kolonne', 'husets form'),
    ('fakturafunn', 'm14_faktura', 'funntype', 'apen_kolonne', 'husets form'),
    ('fordringsfunn', 'm23_fordring', 'funntype', 'apen_kolonne', 'husets form'),
    ('henvendelsesfunn', 'm17_henvendelse', 'funntype', 'apen_kolonne', 'husets form'),
    ('hmsfunn', 'm53_hms', 'funntype', 'apen_kolonne', 'husets form'),
    ('journalfunn', 'm50_postjournal', 'funntype', 'apen_kolonne', 'husets form'),
    ('kampanjefunn', 'm44_kampanje', 'funntype', 'apen_kolonne', 'husets form'),
    ('kontofunn', 'm42_kontovakt', 'funntype', 'apen_kolonne', 'husets form'),
    ('kontrollfunn', 'm34_compliance', 'funntype', 'apen_kolonne', 'husets form'),
    ('lagerfunn', 'm27_lager', 'funntype', 'apen_kolonne', 'husets form'),
    ('leverandorfunn', 'm24_leverandor', 'funntype', 'apen_kolonne', 'husets form'),
    ('likviditetsfunn', 'm15_likviditet', 'funntype', 'apen_kolonne', 'husets form'),
    ('lonnsfunn', 'm39_lonnsgrunnlag', 'funntype', 'apen_kolonne', 'husets form'),
    ('motpartsfunn', 'm48_motpart', 'funntype', 'apen_kolonne', 'husets form'),
    ('myndighetsfunn', 'm47_myndighetsrapport', 'funntype', 'apen_kolonne', 'husets form'),
    ('onboardingfunn', 'm18_onboarding', 'funntype', 'apen_kolonne', 'husets form'),
    ('personvernfunn', 'm30_personvern', 'funntype', 'apen_kolonne', 'husets form'),
    ('prisbokfunn', 'm26_prisbok', 'funntype', 'apen_kolonne', 'husets form'),
    ('prognosefunn', 'm33_prognose', 'funntype', 'apen_kolonne', 'husets form'),
    ('prosjektfunn', 'm25_prosjekt', 'funntype', 'apen_kolonne', 'husets form'),
    ('sanksjonsfunn', 'm49_sanksjon', 'funntype', 'apen_kolonne', 'husets form'),
    ('tilgangsfunn', 'm12_tilgang', 'funntype', 'apen_kolonne', 'husets form'),
    ('tilskuddsfunn', 'm51_tilskudd', 'funntype', 'apen_kolonne', 'husets form'),
    ('tollfunn', 'm52_tollkode', 'funntype', 'apen_kolonne', 'husets form'),
    -- DE TRE SOM IKKE DELER FORMEN.
    ('kvalitetsfunn', 'm3_datakvalitet', 'funntype', 'alle_rader_apne',
     'M-3 (092) har ingen apen-kolonne: hver rad ER et aapent funn, og'
     ' lukking skjer ved at raden forsvinner.'),
    ('retensjonsfunn', 'm29_retensjon', 'funntype', 'lukket_maaling_null',
     'Lukking kodes med lukket_maaling, ikke med apen.'),
    ('merkevarevarsel', 'm55_merkevare', 'varseltype', 'apen_kolonne',
     'M-55 skiller OBSERVASJON fra VARSEL: merkevarefunn er'
     ' observasjoner uten funntype, og varselet er signalet.'),
    ('merkevarefunn', 'm55_merkevare', 'varseltype', 'alle_rader_apne',
     'STAAR HER FOR AA VAERE DEKKET, MEN LESES IKKE: det er en'
     ' observasjonstabell uten funntype. Signalet er merkevarevarsel.')
ON CONFLICT (relasjon) DO NOTHING;

SET LOCAL ROLE disponit_optimalisator_eier;

-- FUNNENE INGEN KAN LUKKE, SOM EN FUNKSJON OG IKKE EN HUSKEREGEL.
CREATE FUNCTION m36_funn_er_sveipens(p_funntype TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT p_funntype IN ('rangering_uten_maaling',
                          'korrelasjon_alene_paa_topp')
$$;
REVOKE ALL ON FUNCTION m36_funn_er_sveipens(TEXT) FROM PUBLIC;

-- STABLE, IKKE IMMUTABLE (125s lærdom).
CREATE FUNCTION m36_modell_gyldig(p_fra DATE, p_til DATE)
RETURNS BOOLEAN LANGUAGE sql STABLE
SET search_path = pg_catalog AS $$
    SELECT p_fra <= current_date
       AND (p_til IS NULL OR p_til >= current_date)
$$;
REVOKE ALL ON FUNCTION m36_modell_gyldig(DATE, DATE) FROM PUBLIC;

CREATE FUNCTION m36_evidens(p_tenant TEXT, p_ref UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm36_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm36_optimalisator', 'handling', p_handling,
        'ref', p_ref::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm36_optimalisator',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:optimalisator', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m36_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- ---------------------------------------------------------------------
-- ER PORTEFØLJEN STOPPET? Ett sted, lest av både døra og flaten.
--
-- STABLE: den leser en tabell.
-- ---------------------------------------------------------------------
CREATE FUNCTION m36_stopp_aktiv(p_tenant TEXT)
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT EXISTS (SELECT 1 FROM public.portefoljestopp s
                    WHERE s.tenant = p_tenant
                      AND s.opphevet_ts IS NULL)
$$;
REVOKE ALL ON FUNCTION m36_stopp_aktiv(TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- ÅPNE FUNN PÅ TVERS, GJENNOM REGISTERET.
--
-- Dynamisk SQL, fordi tabellnavnene ER dataene: hver rad i
-- `m36_funnregister` blir en spørring, og «åpen» oversettes av
-- `apenform`. En hardkodet liste her ville måttet vedlikeholdes ved
-- siden av registeret, og de to ville glidd fra hverandre — det er
-- 127s feil (sveipen som lukket 2 av 5) i en annen form.
--
-- `format(%I)` PÅ ALLE IDENTIFIKATORER. Navnene kommer fra en tabell,
-- og en tabell kan skrives i. CHECKene på `relasjon` og `typekolonne`
-- begrenser dem alt til `^[a-z][a-z0-9_]*$`, men quoting er ikke noe
-- man dropper fordi et CHECK ser strengt ut.
-- ---------------------------------------------------------------------
CREATE FUNCTION m36_apne_funn(p_tenant TEXT)
RETURNS TABLE (modul TEXT, relasjon TEXT, funntype TEXT, antall INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_sql TEXT; v_vilkaar TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm36_apne_funn');
    -- ALLE KOLONNER KVALIFISERES MED `fr.`.
    --
    -- Funksjonens OUT-parametre heter `modul`, `relasjon`, `funntype`
    -- og `antall` — de samme navnene som kolonnene den leser. PL/pgSQL
    -- ser da variabelen, ikke kolonnen, og svarer «column reference is
    -- ambiguous». Riggen fant det to ganger i denne fila før det satt.
    --
    -- Navnene BEHOLDES likevel: en lesedør skal returnere kolonner som
    -- heter det de er. Prisen er at innsiden må kvalifisere, og det er
    -- en pris verdt å betale.
    FOR r IN SELECT fr.* FROM public.m36_funnregister fr
              -- `merkevarefunn` STÅR I REGISTERET FOR Å VÆRE DEKKET,
              -- men leses ikke: den er en observasjonstabell uten
              -- funntype. Signalet er `merkevarevarsel`.
              WHERE fr.relasjon <> 'merkevarefunn'
              ORDER BY fr.relasjon
    LOOP
        v_vilkaar := CASE r.apenform
            WHEN 'apen_kolonne' THEN ' AND apen'
            WHEN 'lukket_maaling_null' THEN
                ' AND lukket_maaling IS NULL'
            ELSE ''   -- alle_rader_apne
        END;
        v_sql := format(
            'SELECT %L::text, %L::text, %I::text, count(*)::int'
            '  FROM public.%I WHERE tenant = %L%s'
            ' GROUP BY %I',
            r.modul, r.relasjon, r.typekolonne, r.relasjon,
            p_tenant, v_vilkaar, r.typekolonne);
        RETURN QUERY EXECUTE v_sql;
    END LOOP;
END $$;
REVOKE ALL ON FUNCTION m36_apne_funn(TEXT) FROM PUBLIC;

-- =====================================================================
-- DØRENE.
-- =====================================================================

CREATE FUNCTION m36_sett_krav(p_tenant TEXT, p_horisont INT,
                              p_maalefrist INT, p_maks INT,
                              p_aktor TEXT, p_nokkel TEXT)
RETURNS TABLE (horisont_uker INT, maalefrist_dogn INT,
               maks_i_rangering INT, versjon INT, endret BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_rad public.optimaliseringskrav%ROWTYPE;
    v_endret BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm36_sett_krav');
    IF p_nokkel IS NULL OR btrim(p_nokkel) = '' THEN
        RAISE EXCEPTION 'm36_sett_krav: idempotensnøkkel mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- LÅS FØRST, LES ETTERPÅ (klynge 6s lærdom).
    SELECT * INTO v_rad FROM public.optimaliseringskrav
     WHERE tenant = p_tenant FOR UPDATE;
    IF FOUND AND v_rad.siste_nokkel IS NOT DISTINCT FROM p_nokkel THEN
        RETURN QUERY SELECT v_rad.horisont_uker, v_rad.maalefrist_dogn,
                            v_rad.maks_i_rangering, v_rad.versjon,
                            false;
        RETURN;
    END IF;
    v_endret := NOT FOUND
        OR v_rad.horisont_uker IS DISTINCT FROM p_horisont
        OR v_rad.maalefrist_dogn IS DISTINCT FROM p_maalefrist
        OR v_rad.maks_i_rangering IS DISTINCT FROM p_maks;
    INSERT INTO public.optimaliseringskrav
        (tenant, horisont_uker, maalefrist_dogn, maks_i_rangering,
         versjon, siste_nokkel, satt_av)
    VALUES (p_tenant, p_horisont, p_maalefrist, p_maks, 1, p_nokkel,
            p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        horisont_uker = EXCLUDED.horisont_uker,
        maalefrist_dogn = EXCLUDED.maalefrist_dogn,
        maks_i_rangering = EXCLUDED.maks_i_rangering,
        -- VERSJONEN ØKER BARE VED EN EKTE ENDRING (119s lærdom).
        versjon = public.optimaliseringskrav.versjon
                  + CASE WHEN v_endret THEN 1 ELSE 0 END,
        siste_nokkel = EXCLUDED.siste_nokkel,
        satt_ts = now(), satt_av = EXCLUDED.satt_av
    RETURNING * INTO v_rad;
    PERFORM public.m36_evidens(p_tenant, NULL, 'sett_krav', p_aktor,
        jsonb_build_object('versjon', v_rad.versjon,
                           'endret', v_endret));
    RETURN QUERY SELECT v_rad.horisont_uker, v_rad.maalefrist_dogn,
                        v_rad.maks_i_rangering, v_rad.versjon,
                        v_endret;
END $$;
REVOKE ALL ON FUNCTION m36_sett_krav(TEXT, INT, INT, INT, TEXT, TEXT)
    FROM PUBLIC;

-- MODELLDØRA. Gjenspill fra fødselen (131s lærdom, som M-33 måtte
-- rette i etterkant).
CREATE FUNCTION m36_registrer_modell(
    p_tenant TEXT, p_modell_id UUID, p_navn TEXT, p_versjon TEXT,
    p_metode TEXT, p_baselinje TEXT, p_usikkerhet_bp INT,
    p_gyldig_fra DATE, p_gyldig_til DATE, p_aktor TEXT)
RETURNS TABLE (modell_id UUID, gjelder BOOLEAN, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.optimaliseringsmodell%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm36_registrer_modell');
    SELECT * INTO v_rad FROM public.optimaliseringsmodell
     WHERE tenant = p_tenant
       AND optimaliseringsmodell.modell_id = p_modell_id;
    IF FOUND THEN
        IF v_rad.versjon IS DISTINCT FROM p_versjon THEN
            RAISE EXCEPTION 'm36_registrer_modell: modell % finnes'
                ' med en annen versjon (%)', p_modell_id, v_rad.versjon
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN QUERY SELECT v_rad.modell_id,
            public.m36_modell_gyldig(v_rad.gyldig_fra,
                                     v_rad.gyldig_til), false;
        RETURN;
    END IF;
    INSERT INTO public.optimaliseringsmodell
        (tenant, modell_id, navn, versjon, metode, baselinje,
         usikkerhet_bp, gyldig_fra, gyldig_til, opprettet_av)
    VALUES (p_tenant, p_modell_id, p_navn, p_versjon, p_metode,
            p_baselinje, p_usikkerhet_bp, p_gyldig_fra, p_gyldig_til,
            p_aktor)
    RETURNING * INTO v_rad;
    PERFORM public.m36_evidens(p_tenant, p_modell_id,
        'registrer_modell', p_aktor,
        jsonb_build_object('versjon', p_versjon));
    RETURN QUERY SELECT v_rad.modell_id,
        public.m36_modell_gyldig(v_rad.gyldig_fra, v_rad.gyldig_til),
        true;
END $$;
REVOKE ALL ON FUNCTION m36_registrer_modell(TEXT, UUID, TEXT, TEXT,
    TEXT, TEXT, INT, DATE, DATE, TEXT) FROM PUBLIC;

CREATE FUNCTION m36_avvikle_modell(p_tenant TEXT, p_modell_id UUID,
                                   p_gyldig_til DATE, p_aktor TEXT)
RETURNS TABLE (modell_id UUID, gyldig_til DATE, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.optimaliseringsmodell%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm36_avvikle_modell');
    SELECT * INTO v_rad FROM public.optimaliseringsmodell
     WHERE tenant = p_tenant
       AND optimaliseringsmodell.modell_id = p_modell_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm36_avvikle_modell: modellen finnes ikke'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_rad.gyldig_til IS NOT NULL THEN
        IF v_rad.gyldig_til IS DISTINCT FROM p_gyldig_til THEN
            RAISE EXCEPTION 'm36_avvikle_modell: modellen er alt'
                ' avviklet per % — en avvikling kan ikke flyttes',
                v_rad.gyldig_til
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN QUERY SELECT v_rad.modell_id, v_rad.gyldig_til, false;
        RETURN;
    END IF;
    UPDATE public.optimaliseringsmodell SET gyldig_til = p_gyldig_til
     WHERE tenant = p_tenant
       AND optimaliseringsmodell.modell_id = p_modell_id
    RETURNING * INTO v_rad;
    PERFORM public.m36_evidens(p_tenant, p_modell_id,
        'avvikle_modell', p_aktor,
        jsonb_build_object('gyldig_til', p_gyldig_til));
    RETURN QUERY SELECT v_rad.modell_id, v_rad.gyldig_til, true;
END $$;
REVOKE ALL ON FUNCTION m36_avvikle_modell(TEXT, UUID, DATE, TEXT)
    FROM PUBLIC;

-- ---------------------------------------------------------------------
-- TILTAKSDØRA — DER `korrelasjon_presentert_som_aarsak` OG
-- `tiltak_uten_reversibilitet` STOPPES.
--
-- Begge er `NOT NULL` uten standardverdi i tabellen, så en rad uten
-- dem er urepresenterbar. Døra legger til det tabellen ikke kan si:
-- at KILDEN må finnes i `m36_funnregister`. Et forslag som pekte på et
-- register modulen ikke leser, ville vært sporet tilbake til noe som
-- ikke finnes.
-- ---------------------------------------------------------------------
CREATE FUNCTION m36_foresla_tiltak(
    p_tenant TEXT, p_tiltak_id UUID, p_beskrivelse TEXT,
    p_grunnlagstype TEXT, p_grunnlag TEXT, p_reversibilitet TEXT,
    p_kilde_modul TEXT, p_kilde_funntype TEXT, p_anslag_ore BIGINT,
    p_aktor TEXT)
RETURNS TABLE (tiltak_id UUID, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.tiltaksforslag%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm36_foresla_tiltak');
    SELECT * INTO v_rad FROM public.tiltaksforslag
     WHERE tenant = p_tenant AND tiltaksforslag.tiltak_id
                                 = p_tiltak_id;
    IF FOUND THEN
        IF v_rad.beskrivelse IS DISTINCT FROM p_beskrivelse THEN
            RAISE EXCEPTION 'm36_foresla_tiltak: % finnes med en'
                ' annen beskrivelse', p_tiltak_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN QUERY SELECT v_rad.tiltak_id, false;
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.m36_funnregister r
                    WHERE r.modul = p_kilde_modul) THEN
        RAISE EXCEPTION 'm36_foresla_tiltak: % står ikke i'
            ' funnregisteret — et forslag som peker på et register'
            ' modulen ikke leser, kan ikke spores tilbake til en'
            ' måling', p_kilde_modul
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.tiltaksforslag
        (tenant, tiltak_id, beskrivelse, grunnlagstype, grunnlag,
         reversibilitet, kilde_modul, kilde_funntype,
         anslag_effekt_ore, opprettet_av)
    VALUES (p_tenant, p_tiltak_id, p_beskrivelse, p_grunnlagstype,
            p_grunnlag, p_reversibilitet, p_kilde_modul,
            p_kilde_funntype, p_anslag_ore, p_aktor)
    RETURNING * INTO v_rad;
    PERFORM public.m36_evidens(p_tenant, p_tiltak_id,
        'foresla_tiltak', p_aktor,
        jsonb_build_object('grunnlagstype', p_grunnlagstype,
                           'reversibilitet', p_reversibilitet));
    RETURN QUERY SELECT v_rad.tiltak_id, true;
END $$;
REVOKE ALL ON FUNCTION m36_foresla_tiltak(TEXT, UUID, TEXT, TEXT,
    TEXT, TEXT, TEXT, TEXT, BIGINT, TEXT) FROM PUBLIC;

-- VURDERINGSDØRA. `vurdert` eller `avvist` — og INGEN `iverksatt`.
CREATE FUNCTION m36_vurder_tiltak(p_tenant TEXT, p_tiltak_id UUID,
                                  p_status TEXT, p_notat TEXT,
                                  p_aktor TEXT)
RETURNS TABLE (tiltak_id UUID, status TEXT, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.tiltaksforslag%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm36_vurder_tiltak');
    IF p_status NOT IN ('vurdert', 'avvist') THEN
        RAISE EXCEPTION 'm36_vurder_tiltak: % er ikke en vurdering —'
            ' modulen iverksetter ingenting, og statussettet har'
            ' ingen slik verdi', p_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm36_vurder_tiltak: en vurdering uten et navn'
            ' på er ikke en vurdering'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO v_rad FROM public.tiltaksforslag
     WHERE tenant = p_tenant AND tiltaksforslag.tiltak_id
                                 = p_tiltak_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm36_vurder_tiltak: tiltaket finnes ikke'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_rad.status <> 'foreslatt' THEN
        IF v_rad.status IS DISTINCT FROM p_status THEN
            RAISE EXCEPTION 'm36_vurder_tiltak: % er alt vurdert som'
                ' %', p_tiltak_id, v_rad.status
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN QUERY SELECT v_rad.tiltak_id, v_rad.status, false;
        RETURN;
    END IF;
    UPDATE public.tiltaksforslag
       SET status = p_status, vurdert_ts = now(), vurdert_av = p_aktor,
           vurderingsnotat = p_notat
     WHERE tenant = p_tenant AND tiltaksforslag.tiltak_id = p_tiltak_id
    RETURNING * INTO v_rad;
    PERFORM public.m36_evidens(p_tenant, p_tiltak_id, 'vurder_tiltak',
        p_aktor, jsonb_build_object('status', p_status));
    RETURN QUERY SELECT v_rad.tiltak_id, v_rad.status, true;
END $$;
REVOKE ALL ON FUNCTION m36_vurder_tiltak(TEXT, UUID, TEXT, TEXT, TEXT)
    FROM PUBLIC;

-- ---------------------------------------------------------------------
-- PORTEFØLJESTOPPEN — OG DEN VIRKER, ellers er
-- `portefoljestopp_uten_virkning` en invariant uten innhold.
--
-- Virkningen er at `m36_rangere` NEKTER. Det er det eneste M-36
-- lovlig kan stanse: å stanse en annen modul ville vært
-- `modulen_overstyrte_en_annen_moduls_grense`.
-- ---------------------------------------------------------------------
CREATE FUNCTION m36_sett_stopp(p_tenant TEXT, p_stopp_id UUID,
                               p_begrunnelse TEXT, p_aktor TEXT)
RETURNS TABLE (stopp_id UUID, aktiv BOOLEAN, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.portefoljestopp%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm36_sett_stopp');
    SELECT * INTO v_rad FROM public.portefoljestopp
     WHERE tenant = p_tenant AND portefoljestopp.stopp_id
                                 = p_stopp_id;
    IF FOUND THEN
        RETURN QUERY SELECT v_rad.stopp_id,
                            v_rad.opphevet_ts IS NULL, false;
        RETURN;
    END IF;
    BEGIN
        INSERT INTO public.portefoljestopp
            (tenant, stopp_id, begrunnelse, satt_av)
        VALUES (p_tenant, p_stopp_id, p_begrunnelse, p_aktor)
        RETURNING * INTO v_rad;
    EXCEPTION WHEN unique_violation THEN
        -- ÉN AKTIV STOPP OM GANGEN. To ville gjort «er porteføljen
        -- stoppet?» til et spørsmål med to svar.
        RAISE EXCEPTION 'm36_sett_stopp: porteføljen er alt stoppet'
            USING ERRCODE = 'unique_violation';
    END;
    PERFORM public.m36_evidens(p_tenant, p_stopp_id, 'sett_stopp',
        p_aktor, jsonb_build_object('begrunnelse', p_begrunnelse));
    RETURN QUERY SELECT v_rad.stopp_id, true, true;
END $$;
REVOKE ALL ON FUNCTION m36_sett_stopp(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

CREATE FUNCTION m36_opphev_stopp(p_tenant TEXT, p_stopp_id UUID,
                                 p_begrunnelse TEXT, p_aktor TEXT)
RETURNS TABLE (stopp_id UUID, aktiv BOOLEAN, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.portefoljestopp%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm36_opphev_stopp');
    SELECT * INTO v_rad FROM public.portefoljestopp
     WHERE tenant = p_tenant AND portefoljestopp.stopp_id = p_stopp_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm36_opphev_stopp: stoppen finnes ikke'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_rad.opphevet_ts IS NOT NULL THEN
        RETURN QUERY SELECT v_rad.stopp_id, false, false;
        RETURN;
    END IF;
    UPDATE public.portefoljestopp
       SET opphevet_ts = now(), opphevet_av = p_aktor,
           opphevingsbegrunnelse = p_begrunnelse
     WHERE tenant = p_tenant AND portefoljestopp.stopp_id = p_stopp_id
    RETURNING * INTO v_rad;
    PERFORM public.m36_evidens(p_tenant, p_stopp_id, 'opphev_stopp',
        p_aktor, jsonb_build_object('begrunnelse', p_begrunnelse));
    RETURN QUERY SELECT v_rad.stopp_id, false, true;
END $$;
REVOKE ALL ON FUNCTION m36_opphev_stopp(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

-- ---------------------------------------------------------------------
-- RANGERINGSDØRA — MODULENS TYNGSTE FUNKSJON.
--
-- REKKEFØLGEN ER MODELLENS, OG DEN STÅR SKREVET:
--
--   1. Størst anslått effekt først. Anslaget er et MENNESKES tall —
--      huset kan ikke prise effekten av et tiltak, og en modul som
--      «utledet» den ville rangert på noe den fant på.
--   2. Ved lik effekt: REVERSIBELT FØR IRREVERSIBELT. To tiltak med
--      samme anslag er ikke like gode — det man kan angre koster
--      mindre å ta feil om. Dette er den ENESTE meningen modellen har
--      utover tallet, og den skal være lesbar.
--   3. Deretter eldste forslag først, så rekkefølgen er determinert.
--
-- BÅNDET ER MODELLENS USIKKERHET, ikke forslagsstillerens. Det er
-- derfor `usikkerhet_bp` står på modellen: to modellversjoner kan lese
-- samme anslag og ha ulik tillit til det.
--
-- DØRA NEKTER MED AKTIV PORTEFØLJESTOPP. Det er stoppens hele
-- virkning, og porten måler den.
--
-- DØRA NEKTER OGSÅ NÅR ET FUNNREGISTER MANGLER I `m36_funnregister`.
-- En rangering laget mens et register var usynlig ville vært regnet
-- på et grunnlag ingen visste var ufullstendig — og den ville sett
-- like komplett ut som de riktige.
-- ---------------------------------------------------------------------
CREATE FUNCTION m36_rangere(p_tenant TEXT, p_rangering_id UUID,
                            p_modell_id UUID, p_aktor TEXT)
RETURNS TABLE (rangering_id UUID, antall INT, apne_funn INT,
               registre INT, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_krav public.optimaliseringskrav%ROWTYPE;
    v_modell public.optimaliseringsmodell%ROWTYPE;
    v_gml public.rangering%ROWTYPE;
    v_dato DATE := current_date;
    v_apne INT;
    v_registre INT;
    v_udekket TEXT;
    v_antall INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm36_rangere');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm36_rangere: en rangering bærer navnet til'
            ' den som ba om den'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- GJENSPILL FØRST (SP-2, fra fødselen — 131s lærdom).
    SELECT * INTO v_gml FROM public.rangering
     WHERE tenant = p_tenant AND rangering.rangering_id
                                 = p_rangering_id;
    IF FOUND THEN
        IF v_gml.modell_id IS DISTINCT FROM p_modell_id THEN
            RAISE EXCEPTION 'm36_rangere: rangering % finnes mot en'
                ' annen modell', p_rangering_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN QUERY SELECT v_gml.rangering_id,
            (SELECT count(*)::int FROM public.rangeringspost r
              WHERE r.tenant = p_tenant
                AND r.rangering_id = p_rangering_id),
            v_gml.grunnlag_apne_funn, v_gml.grunnlag_registre, false;
        RETURN;
    END IF;

    -- PORTEFØLJESTOPPEN VIRKER HER.
    IF public.m36_stopp_aktiv(p_tenant) THEN
        RAISE EXCEPTION 'm36_rangere: porteføljen er stoppet — ingen'
            ' ny rangering lages før stoppen oppheves'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT * INTO v_krav FROM public.optimaliseringskrav
     WHERE tenant = p_tenant;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm36_rangere: tenanten har ingen registrerte'
            ' grenser'
            USING ERRCODE = 'no_data_found';
    END IF;

    SELECT * INTO v_modell FROM public.optimaliseringsmodell
     WHERE tenant = p_tenant
       AND optimaliseringsmodell.modell_id = p_modell_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm36_rangere: modellen finnes ikke'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT public.m36_modell_gyldig(v_modell.gyldig_fra,
                                    v_modell.gyldig_til) THEN
        RAISE EXCEPTION 'm36_rangere: modell % gjelder ikke i dag',
            v_modell.versjon
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- ET USYNLIG REGISTER GJØR GRUNNLAGET UFULLSTENDIG.
    SELECT string_agg(relasjon, ', ') INTO v_udekket
      FROM public.m36_udekkede_registre();
    IF v_udekket IS NOT NULL THEN
        RAISE EXCEPTION 'm36_rangere: funnregistre utenfor'
            ' m36_funnregister (%) — en rangering laget nå ville'
            ' hvilt på et grunnlag ingen visste var ufullstendig',
            v_udekket
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- `f.antall` OG IKKE `antall`: funksjonen har en OUT-parameter
    -- som heter det samme, og PL/pgSQL kan ikke se forskjell. Riggen
    -- sa «column reference is ambiguous» ved første kall.
    SELECT coalesce(sum(f.antall), 0)::int INTO v_apne
      FROM public.m36_apne_funn(p_tenant) f;

    -- REGISTRE LEST, IKKE REGISTRE MED FUNN. Det er det siste som
    -- svarer på «hvor bredt så vi?» — et register uten åpne funn er
    -- fortsatt et register vi har sett i, og å telle bort det ville
    -- gjort et rent hus til et smalt grunnlag.
    SELECT count(*)::int INTO v_registre
      FROM public.m36_funnregister fr
     WHERE fr.relasjon <> 'merkevarefunn';

    INSERT INTO public.rangering
        (tenant, rangering_id, laget_dato, horisont_uker, modell_id,
         modellversjon, baselinje, grunnlag_apne_funn,
         grunnlag_registre, stopp_aktiv, gjelder_til, laget_av)
    VALUES (p_tenant, p_rangering_id, v_dato, v_krav.horisont_uker,
            p_modell_id, v_modell.versjon, v_modell.baselinje,
            v_apne, v_registre, false,
            v_dato + (v_krav.horisont_uker * 7), p_aktor);

    WITH kandidat AS (
        SELECT t.tiltak_id, t.anslag_effekt_ore, t.grunnlagstype,
               t.reversibilitet,
               row_number() OVER (
                   ORDER BY t.anslag_effekt_ore DESC,
                            CASE t.reversibilitet
                              WHEN 'reversibel' THEN 0
                              WHEN 'delvis_reversibel' THEN 1
                              ELSE 2 END,
                            t.opprettet) AS plass
          FROM public.tiltaksforslag t
         WHERE t.tenant = p_tenant AND t.status = 'foreslatt')
    INSERT INTO public.rangeringspost
        (tenant, rangering_id, plass, tiltak_id,
         forventet_effekt_ore, nedre_effekt_ore, ovre_effekt_ore,
         grunnlagstype, reversibilitet, ukeslutt)
    SELECT p_tenant, p_rangering_id, k.plass::int, k.tiltak_id,
           k.anslag_effekt_ore,
           k.anslag_effekt_ore
             - greatest((abs(k.anslag_effekt_ore)
                         * v_modell.usikkerhet_bp) / 10000, 1),
           k.anslag_effekt_ore
             + greatest((abs(k.anslag_effekt_ore)
                         * v_modell.usikkerhet_bp) / 10000, 1),
           k.grunnlagstype, k.reversibilitet,
           -- `til - 1`: horisontens SISTE dag (129s lærdom).
           (v_dato + (v_krav.horisont_uker * 7)) - 1
      FROM kandidat k
     WHERE k.plass <= v_krav.maks_i_rangering;
    GET DIAGNOSTICS v_antall = ROW_COUNT;

    PERFORM public.m36_evidens(p_tenant, p_rangering_id, 'rangere',
        p_aktor, jsonb_build_object('antall', v_antall,
                                    'apne_funn', v_apne,
                                    'registre', v_registre));
    RETURN QUERY SELECT p_rangering_id, v_antall, v_apne, v_registre,
                        true;
END $$;
REVOKE ALL ON FUNCTION m36_rangere(TEXT, UUID, UUID, TEXT)
    FROM PUBLIC;

-- ---------------------------------------------------------------------
-- EFFEKTMÅLINGEN — DEN SOM LUKKER `rangering_uten_maaling`.
--
-- NEKTER FØR HORISONTEN ER PASSERT (130s dom): `ukeslutt` er
-- horisontens siste dag, så den er over først når
-- `ukeslutt < current_date`. Målingen er ukorrigerbar, så et
-- delresultat registrert som endelig ville stått for alltid.
--
-- GJENSPILL FRA FØDSELEN (131s lærdom). Et gjentatt kall med samme
-- tall svarer med raden; et med et annet tall er fortsatt en feil.
-- ---------------------------------------------------------------------
CREATE FUNCTION m36_registrer_effekt(
    p_tenant TEXT, p_rangering_id UUID, p_plass INT,
    p_faktisk_ore BIGINT, p_aktor TEXT)
RETURNS TABLE (plass INT, avvik_ore BIGINT,
               innenfor_intervall BOOLEAN, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_post public.rangeringspost%ROWTYPE;
    v_rad public.effektmaaling%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm36_registrer_effekt');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm36_registrer_effekt: en måling uten et navn'
            ' på er ikke evidens'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT * INTO v_rad FROM public.effektmaaling
     WHERE tenant = p_tenant AND rangering_id = p_rangering_id
       AND effektmaaling.plass = p_plass;
    IF FOUND THEN
        IF v_rad.faktisk_effekt_ore IS DISTINCT FROM p_faktisk_ore THEN
            RAISE EXCEPTION 'm36_registrer_effekt: plass % er alt målt'
                ' til % øre — en måling kan ikke rettes',
                p_plass, v_rad.faktisk_effekt_ore
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN QUERY SELECT v_rad.plass, v_rad.avvik_ore,
                            v_rad.innenfor_intervall, false;
        RETURN;
    END IF;

    SELECT * INTO v_post FROM public.rangeringspost
     WHERE tenant = p_tenant AND rangering_id = p_rangering_id
       AND rangeringspost.plass = p_plass;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm36_registrer_effekt: plass % finnes ikke i'
            ' denne rangeringen', p_plass
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_post.ukeslutt >= current_date THEN
        RAISE EXCEPTION 'm36_registrer_effekt: horisonten er ikke'
            ' passert (slutter %) — en ukorrigerbar måling av en'
            ' periode som ennå løper er et delresultat som aldri kan'
            ' rettes', v_post.ukeslutt
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    BEGIN
        INSERT INTO public.effektmaaling
            (tenant, rangering_id, plass, faktisk_effekt_ore,
             forventet_effekt_ore, innenfor_intervall, maalt_av)
        VALUES (p_tenant, p_rangering_id, p_plass, p_faktisk_ore,
                v_post.forventet_effekt_ore,
                p_faktisk_ore BETWEEN v_post.nedre_effekt_ore
                                  AND v_post.ovre_effekt_ore,
                p_aktor)
        RETURNING * INTO v_rad;
    EXCEPTION WHEN unique_violation THEN
        SELECT * INTO v_rad FROM public.effektmaaling
         WHERE tenant = p_tenant AND rangering_id = p_rangering_id
           AND effektmaaling.plass = p_plass;
        IF v_rad.faktisk_effekt_ore IS DISTINCT FROM p_faktisk_ore THEN
            RAISE EXCEPTION 'm36_registrer_effekt: plass % er allerede'
                ' målt — en måling kan ikke rettes', p_plass
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN QUERY SELECT v_rad.plass, v_rad.avvik_ore,
                            v_rad.innenfor_intervall, false;
        RETURN;
    END;

    PERFORM public.m36_evidens(p_tenant, p_rangering_id,
        'registrer_effekt', p_aktor,
        jsonb_build_object('plass', p_plass,
                           'innenfor', v_rad.innenfor_intervall));
    RETURN QUERY SELECT v_rad.plass, v_rad.avvik_ore,
                        v_rad.innenfor_intervall, true;
END $$;
REVOKE ALL ON FUNCTION m36_registrer_effekt(TEXT, UUID, INT, BIGINT,
                                            TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- LESEDØRENE.
--
-- `m36_rangeringen` GIR ALDRI ET PUNKT UTEN SITT INTERVALL, OG ALDRI
-- ET TILTAK UTEN SIN `grunnlagstype`. Det siste er vaktsetningen
-- håndhevet der den faktisk kan brytes: i det som forlater basen. En
-- flate kan velge å ikke vise grunnlagstypen, men den kan ikke få et
-- svar der den mangler.
-- ---------------------------------------------------------------------
CREATE FUNCTION m36_rangeringen(p_tenant TEXT, p_rangering_id UUID)
RETURNS TABLE (plass INT, tiltak_id UUID, beskrivelse TEXT,
               forventet_effekt_ore BIGINT, nedre_effekt_ore BIGINT,
               ovre_effekt_ore BIGINT, grunnlagstype TEXT,
               reversibilitet TEXT, ukeslutt DATE,
               faktisk_effekt_ore BIGINT, avvik_ore BIGINT,
               innenfor_intervall BOOLEAN, status TEXT,
               kan_maales BOOLEAN)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT r.plass, r.tiltak_id, t.beskrivelse,
           r.forventet_effekt_ore, r.nedre_effekt_ore,
           r.ovre_effekt_ore, r.grunnlagstype, r.reversibilitet,
           r.ukeslutt, m.faktisk_effekt_ore, m.avvik_ore,
           m.innenfor_intervall, t.status,
           -- SAMME PREDIKAT SOM DØRA. Står det to steder med to
           -- formuleringer, blir knappen aktiv en dag før døra sier ja.
           (m.plass IS NULL AND r.ukeslutt < current_date)
      FROM public.rangeringspost r
      JOIN public.tiltaksforslag t
        ON t.tenant = r.tenant AND t.tiltak_id = r.tiltak_id
      LEFT JOIN public.effektmaaling m
             ON m.tenant = r.tenant
            AND m.rangering_id = r.rangering_id
            AND m.plass = r.plass
     WHERE r.tenant = p_tenant AND r.rangering_id = p_rangering_id
     ORDER BY r.plass
$$;
REVOKE ALL ON FUNCTION m36_rangeringen(TEXT, UUID) FROM PUBLIC;

CREATE FUNCTION m36_rangeringsregister(p_tenant TEXT, p_grense INT)
RETURNS TABLE (rangering_id UUID, laget_dato DATE, horisont_uker INT,
               modellversjon TEXT, baselinje TEXT,
               grunnlag_apne_funn INT, grunnlag_registre INT,
               gjelder_til DATE, laget_av TEXT, antall_poster INT,
               antall_maalt INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT r.rangering_id, r.laget_dato, r.horisont_uker,
           r.modellversjon, r.baselinje, r.grunnlag_apne_funn,
           r.grunnlag_registre, r.gjelder_til, r.laget_av,
           (SELECT count(*)::INT FROM public.rangeringspost p
             WHERE p.tenant = r.tenant
               AND p.rangering_id = r.rangering_id),
           (SELECT count(*)::INT FROM public.effektmaaling m
             WHERE m.tenant = r.tenant
               AND m.rangering_id = r.rangering_id)
      FROM public.rangering r
     WHERE r.tenant = p_tenant
     ORDER BY r.laget_dato DESC, r.laget_ts DESC
     LIMIT greatest(p_grense, 1)
$$;
REVOKE ALL ON FUNCTION m36_rangeringsregister(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m36_tiltakene(p_tenant TEXT, p_grense INT)
RETURNS TABLE (tiltak_id UUID, beskrivelse TEXT, grunnlagstype TEXT,
               grunnlag TEXT, reversibilitet TEXT, kilde_modul TEXT,
               kilde_funntype TEXT, anslag_effekt_ore BIGINT,
               status TEXT, vurdert_av TEXT, vurderingsnotat TEXT,
               opprettet TIMESTAMPTZ, opprettet_av TEXT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT t.tiltak_id, t.beskrivelse, t.grunnlagstype, t.grunnlag,
           t.reversibilitet, t.kilde_modul, t.kilde_funntype,
           t.anslag_effekt_ore, t.status, t.vurdert_av,
           t.vurderingsnotat, t.opprettet, t.opprettet_av
      FROM public.tiltaksforslag t
     WHERE t.tenant = p_tenant
     ORDER BY (t.status = 'foreslatt') DESC,
              t.anslag_effekt_ore DESC, t.opprettet
     LIMIT greatest(p_grense, 1)
$$;
REVOKE ALL ON FUNCTION m36_tiltakene(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m36_modellregister(p_tenant TEXT)
RETURNS TABLE (modell_id UUID, navn TEXT, versjon TEXT, metode TEXT,
               baselinje TEXT, usikkerhet_bp INT, gyldig_fra DATE,
               gyldig_til DATE, gjelder BOOLEAN,
               antall_rangeringer INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT m.modell_id, m.navn, m.versjon, m.metode, m.baselinje,
           m.usikkerhet_bp, m.gyldig_fra, m.gyldig_til,
           public.m36_modell_gyldig(m.gyldig_fra, m.gyldig_til),
           (SELECT count(*)::INT FROM public.rangering r
             WHERE r.tenant = m.tenant AND r.modell_id = m.modell_id)
      FROM public.optimaliseringsmodell m
     WHERE m.tenant = p_tenant
     ORDER BY m.gyldig_fra DESC, m.versjon
$$;
REVOKE ALL ON FUNCTION m36_modellregister(TEXT) FROM PUBLIC;

CREATE FUNCTION m36_stoppen(p_tenant TEXT)
RETURNS TABLE (stopp_id UUID, begrunnelse TEXT, satt_ts TIMESTAMPTZ,
               satt_av TEXT, opphevet_ts TIMESTAMPTZ,
               opphevet_av TEXT, aktiv BOOLEAN)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT s.stopp_id, s.begrunnelse, s.satt_ts, s.satt_av,
           s.opphevet_ts, s.opphevet_av, s.opphevet_ts IS NULL
      FROM public.portefoljestopp s
     WHERE s.tenant = p_tenant
     ORDER BY s.satt_ts DESC
     LIMIT 20
$$;
REVOKE ALL ON FUNCTION m36_stoppen(TEXT) FROM PUBLIC;

CREATE FUNCTION m36_optimaliseringsfunn(p_tenant TEXT, p_grense INT)
RETURNS TABLE (funn_id UUID, funntype TEXT, referanse TEXT,
               detaljer TEXT, over_grense BIGINT, apen BOOLEAN,
               forst_sett TIMESTAMPTZ, sist_sett TIMESTAMPTZ,
               lukket_av TEXT, kan_lukkes BOOLEAN)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT f.funn_id, f.funntype, f.referanse, f.detaljer,
           f.over_grense, f.apen, f.forst_sett, f.sist_sett,
           f.lukket_av,
           (f.apen AND NOT public.m36_funn_er_sveipens(f.funntype))
      FROM public.optimaliseringsfunn f
     WHERE f.tenant = p_tenant
     ORDER BY f.apen DESC, f.sist_sett DESC
     LIMIT greatest(p_grense, 1)
$$;
REVOKE ALL ON FUNCTION m36_optimaliseringsfunn(TEXT, INT)
    FROM PUBLIC;

CREATE FUNCTION m36_lukk_funn(p_tenant TEXT, p_funn_id UUID,
                              p_begrunnelse TEXT, p_aktor TEXT)
RETURNS TABLE (funn_id UUID, apen BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.optimaliseringsfunn%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm36_lukk_funn');
    -- 125s LÆRDOM: en tom aktør ville gitt NULL i CHECKen, og NULL i
    -- en NOT NULL-kolonne dreper hele transaksjonen.
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm36_lukk_funn: en lukking uten et navn på er'
            ' ikke en lukking'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_begrunnelse IS NULL OR btrim(p_begrunnelse) = '' THEN
        RAISE EXCEPTION 'm36_lukk_funn: begrunnelse mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO v_rad FROM public.optimaliseringsfunn
     WHERE tenant = p_tenant
       AND optimaliseringsfunn.funn_id = p_funn_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm36_lukk_funn: funnet finnes ikke'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF public.m36_funn_er_sveipens(v_rad.funntype) THEN
        RAISE EXCEPTION 'm36_lukk_funn: % lukkes ikke av et menneske'
            ' — det lukkes av at tilstanden opphører', v_rad.funntype
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT v_rad.apen THEN
        RETURN QUERY SELECT v_rad.funn_id, v_rad.apen;
        RETURN;
    END IF;
    UPDATE public.optimaliseringsfunn
       SET apen = false, lukket_ts = now(), lukket_av = p_aktor,
           lukket_begrunnelse = p_begrunnelse
     WHERE tenant = p_tenant
       AND optimaliseringsfunn.funn_id = p_funn_id
    RETURNING * INTO v_rad;
    PERFORM public.m36_evidens(p_tenant, p_funn_id, 'lukk_funn',
        p_aktor, jsonb_build_object('funntype', v_rad.funntype));
    RETURN QUERY SELECT v_rad.funn_id, v_rad.apen;
END $$;
REVOKE ALL ON FUNCTION m36_lukk_funn(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

-- SAMMENDRAGET. Flaten regner ikke selv.
CREATE FUNCTION m36_bildet(p_tenant TEXT)
RETURNS TABLE (rangeringer INT, modeller INT, gyldige_modeller INT,
               tiltak INT, uvurderte_tiltak INT,
               irreversible_uvurderte INT, poster INT, maalte INT,
               umaalte INT, treff INT, bom INT, apne_funn INT,
               stopp_aktiv BOOLEAN, har_krav BOOLEAN,
               horisont_uker INT, maalefrist_dogn INT,
               maks_i_rangering INT, kravversjon INT,
               apne_funn_i_huset INT, registre INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT
      (SELECT count(*)::INT FROM public.rangering r
        WHERE r.tenant = p_tenant),
      (SELECT count(*)::INT FROM public.optimaliseringsmodell m
        WHERE m.tenant = p_tenant),
      (SELECT count(*)::INT FROM public.optimaliseringsmodell m
        WHERE m.tenant = p_tenant
          AND public.m36_modell_gyldig(m.gyldig_fra, m.gyldig_til)),
      (SELECT count(*)::INT FROM public.tiltaksforslag t
        WHERE t.tenant = p_tenant),
      (SELECT count(*)::INT FROM public.tiltaksforslag t
        WHERE t.tenant = p_tenant AND t.status = 'foreslatt'),
      -- ET IRREVERSIBELT TILTAK INGEN HAR SETT PÅ ER DET DYRESTE Å
      -- OVERSE, og derfor et eget tall.
      (SELECT count(*)::INT FROM public.tiltaksforslag t
        WHERE t.tenant = p_tenant AND t.status = 'foreslatt'
          AND t.reversibilitet = 'irreversibel'),
      (SELECT count(*)::INT FROM public.rangeringspost r
        WHERE r.tenant = p_tenant),
      (SELECT count(*)::INT FROM public.effektmaaling m
        WHERE m.tenant = p_tenant),
      -- BARE POSTER DER HORISONTEN ER OVER. En post som ennå løper
      -- er ikke umålt — den er ikke målbar, og å telle den ville
      -- gjort tallet til en anklage mot noen som ikke har gjort noe.
      (SELECT count(*)::INT FROM public.rangeringspost r
        WHERE r.tenant = p_tenant AND r.ukeslutt < current_date
          AND NOT EXISTS (SELECT 1 FROM public.effektmaaling m
                           WHERE m.tenant = r.tenant
                             AND m.rangering_id = r.rangering_id
                             AND m.plass = r.plass)),
      (SELECT count(*)::INT FROM public.effektmaaling m
        WHERE m.tenant = p_tenant AND m.innenfor_intervall),
      (SELECT count(*)::INT FROM public.effektmaaling m
        WHERE m.tenant = p_tenant AND NOT m.innenfor_intervall),
      (SELECT count(*)::INT FROM public.optimaliseringsfunn f
        WHERE f.tenant = p_tenant AND f.apen),
      public.m36_stopp_aktiv(p_tenant),
      (SELECT EXISTS (SELECT 1 FROM public.optimaliseringskrav k
                       WHERE k.tenant = p_tenant)),
      (SELECT k.horisont_uker FROM public.optimaliseringskrav k
        WHERE k.tenant = p_tenant),
      (SELECT k.maalefrist_dogn FROM public.optimaliseringskrav k
        WHERE k.tenant = p_tenant),
      (SELECT k.maks_i_rangering FROM public.optimaliseringskrav k
        WHERE k.tenant = p_tenant),
      (SELECT k.versjon FROM public.optimaliseringskrav k
        WHERE k.tenant = p_tenant),
      (SELECT coalesce(sum(antall), 0)::INT
         FROM public.m36_apne_funn(p_tenant)),
      (SELECT count(*)::INT FROM public.m36_funnregister fr
        WHERE fr.relasjon <> 'merkevarefunn')
$$;
REVOKE ALL ON FUNCTION m36_bildet(TEXT) FROM PUBLIC;

-- =====================================================================
-- SVEIPEN. Én tenant om gangen, med konteksten satt (130s lærdom:
-- FORCE RLS gjør en kryss-tenant-spørring blind, ikke bred).
--
-- TRE FUNN:
--
-- 1. `rangering_uten_maaling` — horisonten er passert med
--    målefristen, og ingen har målt. Klyngens dom: en gal prognose
--    ser ut som en riktig prognose helt til horisonten er passert.
--
-- 2. `korrelasjon_alene_paa_topp` — den øverste posten i den ferskeste
--    rangeringen hviler BARE på korrelasjon. Dette er
--    `korrelasjon_presentert_som_aarsak` gjort observerbart: modellen
--    får rangere på korrelasjon, men at det ØVERSTE forslaget gjør
--    det, skal noen se.
--
-- 3. `tiltak_uten_reversibilitet` — kan ikke reises, og det er
--    meningen: kolonnen er NOT NULL med et lukket sett, så tilstanden
--    er urepresenterbar. Funntypen står i settet fordi invarianten
--    heter det, og porten som viser at den ALDRI reises er beviset på
--    at vernet er i datamodellen og ikke i sveipen.
--
-- 4. `stopp_staar_uten_oppheving` — en porteføljestopp har stått
--    lenger enn målefristen. Et menneske KAN lukke det: «vi vet, den
--    skal stå». En stopp som blir stående uten at noen tar stilling,
--    er en modul som er slått av i stillhet.
-- =====================================================================
CREATE FUNCTION m36_sveip_optimalisering(p_maks_tenanter INT)
RETURNS TABLE (tenanter INT, nye BIGINT, oppdaterte BIGINT,
               lukkede BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_sveip CONSTANT TEXT := 'm36_sveip';
    v_t TEXT;
    v_antall INT := 0;
    v_nye BIGINT := 0;
    v_oppdaterte BIGINT := 0;
    v_lukket BIGINT := 0;
    v_n BIGINT; v_n2 BIGINT; v_n3 BIGINT;
BEGIN
    FOR v_t IN
        SELECT k.tenant FROM public.optimaliseringskrav k
         ORDER BY k.tenant LIMIT greatest(p_maks_tenanter, 1)
    LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        v_antall := v_antall + 1;

        -- 1. UMÅLT RANGERINGSPOST.
        WITH kand AS (
            SELECT r.rangering_id, r.plass, r.ukeslutt,
                   (current_date - r.ukeslutt) - k.maalefrist_dogn
                       AS dogn_over,
                   r.rangering_id::text || ':' || r.plass AS ref
              FROM public.rangeringspost r
              JOIN public.optimaliseringskrav k ON k.tenant = r.tenant
              LEFT JOIN public.effektmaaling m
                     ON m.tenant = r.tenant
                    AND m.rangering_id = r.rangering_id
                    AND m.plass = r.plass
             WHERE r.tenant = v_t AND m.plass IS NULL
               AND r.ukeslutt < current_date
               AND current_date - r.ukeslutt > k.maalefrist_dogn),
        skrevet AS (
            INSERT INTO public.optimaliseringsfunn
                (tenant, funn_id, funntype, referanse, detaljer,
                 over_grense)
            SELECT v_t, gen_random_uuid(), 'rangering_uten_maaling',
                   c.ref,
                   format('plass %s hadde horisont til %s og er %s'
                          ' døgn over målefristen', c.plass,
                          c.ukeslutt, c.dogn_over),
                   c.dogn_over
              FROM kand c
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
            DO UPDATE SET sist_sett = now(),
                          over_grense = EXCLUDED.over_grense,
                          detaljer = EXCLUDED.detaljer
            RETURNING (xmax = 0) AS var_ny),
        lukket AS (
            UPDATE public.optimaliseringsfunn f
               SET apen = false, lukket_ts = now(),
                   lukket_av = v_sveip,
                   lukket_begrunnelse = 'effekten er målt'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'rangering_uten_maaling'
               AND NOT EXISTS (SELECT 1 FROM kand c
                                WHERE c.ref = f.referanse)
            RETURNING 1)
        SELECT (SELECT count(*) FILTER (WHERE var_ny) FROM skrevet),
               (SELECT count(*) FILTER (WHERE NOT var_ny) FROM skrevet),
               (SELECT count(*) FROM lukket)
          INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 2. KORRELASJON ALENE PÅ TOPP, i den FERSKESTE rangeringen.
        WITH fersk AS (
            SELECT r.rangering_id FROM public.rangering r
             WHERE r.tenant = v_t
             ORDER BY r.laget_dato DESC, r.laget_ts DESC LIMIT 1),
        kand AS (
            SELECT p.rangering_id, p.tiltak_id
              FROM public.rangeringspost p
              JOIN fersk f ON f.rangering_id = p.rangering_id
             WHERE p.tenant = v_t AND p.plass = 1
               AND p.grunnlagstype = 'korrelasjon'),
        skrevet AS (
            INSERT INTO public.optimaliseringsfunn
                (tenant, funn_id, funntype, referanse, detaljer)
            SELECT v_t, gen_random_uuid(),
                   'korrelasjon_alene_paa_topp',
                   c.rangering_id::text,
                   'det øverste forslaget hviler bare på korrelasjon'
                   ' — modellen får rangere på det, men at det står'
                   ' øverst skal noen se'
              FROM kand c
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
            DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS var_ny),
        lukket AS (
            UPDATE public.optimaliseringsfunn f
               SET apen = false, lukket_ts = now(),
                   lukket_av = v_sveip,
                   lukket_begrunnelse = 'toppen hviler ikke lenger'
                                        ' bare på korrelasjon'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'korrelasjon_alene_paa_topp'
               AND NOT EXISTS (SELECT 1 FROM kand c
                                WHERE c.rangering_id::text
                                      = f.referanse)
            RETURNING 1)
        SELECT (SELECT count(*) FILTER (WHERE var_ny) FROM skrevet),
               (SELECT count(*) FILTER (WHERE NOT var_ny) FROM skrevet),
               (SELECT count(*) FROM lukket)
          INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 3. EN STOPP SOM BLIR STÅENDE.
        WITH kand AS (
            SELECT s.stopp_id,
                   (current_date - s.satt_ts::date)
                       - k.maalefrist_dogn AS dogn_over
              FROM public.portefoljestopp s
              JOIN public.optimaliseringskrav k ON k.tenant = s.tenant
             WHERE s.tenant = v_t AND s.opphevet_ts IS NULL
               AND current_date - s.satt_ts::date > k.maalefrist_dogn),
        skrevet AS (
            INSERT INTO public.optimaliseringsfunn
                (tenant, funn_id, funntype, referanse, detaljer,
                 over_grense)
            SELECT v_t, gen_random_uuid(),
                   'stopp_staar_uten_oppheving', c.stopp_id::text,
                   format('porteføljestoppen har stått %s døgn over'
                          ' målefristen — en modul som er slått av i'
                          ' stillhet', c.dogn_over),
                   c.dogn_over
              FROM kand c
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
            DO UPDATE SET sist_sett = now(),
                          over_grense = EXCLUDED.over_grense,
                          detaljer = EXCLUDED.detaljer
            RETURNING (xmax = 0) AS var_ny),
        lukket AS (
            UPDATE public.optimaliseringsfunn f
               SET apen = false, lukket_ts = now(),
                   lukket_av = v_sveip,
                   lukket_begrunnelse = 'stoppen er opphevet'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'stopp_staar_uten_oppheving'
               AND NOT EXISTS (SELECT 1 FROM kand c
                                WHERE c.stopp_id::text = f.referanse)
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
REVOKE ALL ON FUNCTION m36_sveip_optimalisering(INT) FROM PUBLIC;

-- =====================================================================
-- RETTIGHETENE (SP-7).
--
-- MODULEN LESER 32 ANDRE REGISTRE, OG DEN FÅR NØYAKTIG `SELECT`.
-- Ingen INSERT, ingen UPDATE, ingen DELETE. En optimalisator som
-- kunne skrive i en annen moduls funnregister ville kunnet «lukke»
-- funnene som talte mot dens egen rangering — og det er den ene feilen
-- ingen ville oppdaget.
--
-- OG DEN HAR INGEN RETTIGHET PÅ `policyer`, `policyutkast` ELLER
-- `policyaktivering`. Det står som et FRAVÆR, og porten leser
-- fraværet: en optimalisator som fant at den beste forbedringen var
-- «gi M-36 lov til X», skal ikke kunne skrive det noe sted.
-- =====================================================================
-- GRANTEN AVGIS AV HVER EIER, IKKE AV MIGRATOR.
--
-- De 33 funnregistrene eies av 30 ULIKE ROLLER: migrator eier noen,
-- modulrollene eier resten (`kvalitetsfunn` er `disponit_kvalitet_eier`s,
-- `merkevarevarsel` er `disponit_merkevare_eier`s, og så videre). En
-- rolle kan ikke gi bort rettigheter på noe den ikke eier, så løkka
-- SETTER ROLLEN til eieren for hver tabell.
--
-- Riggen sa det med én gang; lesing av filen ville ikke ha gjort det.
-- Samme lærdom som 130 fikk for M-3s to tabeller — her gjelder den
-- tretti ganger.
--
-- AT DET KREVER TRETTI EIERE Å LESE HUSETS FUNN, ER SELV EN MÅLING:
-- ingen enkelt rolle ser hele bildet i dag, og M-36 er den første som
-- ber om å få gjøre det. Rettigheten er `SELECT` og bare det.
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT f.relasjon, pg_get_userbyid(c.relowner) AS eier
               FROM public.m36_funnregister f
               JOIN pg_class c
                 ON c.relname = f.relasjon
                AND c.relnamespace = 'public'::regnamespace
    LOOP
        EXECUTE format('SET LOCAL ROLE %I', r.eier);
        EXECUTE format('GRANT SELECT ON public.%I TO'
                       ' disponit_optimalisator_eier', r.relasjon);
    END LOOP;
    RESET ROLE;
END $$;

SET LOCAL ROLE disponit_optimalisator_eier;
GRANT EXECUTE ON FUNCTION m36_sett_krav(TEXT, INT, INT, INT, TEXT,
    TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m36_registrer_modell(TEXT, UUID, TEXT, TEXT,
    TEXT, TEXT, INT, DATE, DATE, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m36_avvikle_modell(TEXT, UUID, DATE, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m36_foresla_tiltak(TEXT, UUID, TEXT, TEXT,
    TEXT, TEXT, TEXT, TEXT, BIGINT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m36_vurder_tiltak(TEXT, UUID, TEXT, TEXT,
    TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m36_sett_stopp(TEXT, UUID, TEXT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m36_opphev_stopp(TEXT, UUID, TEXT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m36_rangere(TEXT, UUID, UUID, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m36_registrer_effekt(TEXT, UUID, INT,
    BIGINT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m36_lukk_funn(TEXT, UUID, TEXT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m36_bildet(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m36_rangeringen(TEXT, UUID) TO disponit;
GRANT EXECUTE ON FUNCTION m36_rangeringsregister(TEXT, INT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m36_tiltakene(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m36_modellregister(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m36_stoppen(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m36_optimaliseringsfunn(TEXT, INT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m36_stopp_aktiv(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m36_apne_funn(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m36_udekkede_registre() TO disponit;

-- SVEIPEROLLEN NÅR ÉN FUNKSJON, OG BARE DEN (111s form).
GRANT EXECUTE ON FUNCTION m36_sveip_optimalisering(INT)
    TO disponit_optimalisatorsveip;

RESET ROLE;
