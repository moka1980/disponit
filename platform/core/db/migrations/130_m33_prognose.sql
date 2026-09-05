-- =====================================================================
-- M-33 PREDIKSJONS- OG SCENARIOAGENT (v1) — KLYNGE 8s ANDRE.
-- =====================================================================
--
-- V1-DOMMEN: MODULEN LAGER EN BEMANNINGSPROGNOSE OG STOPPER DER. Den
-- ansetter ingen, sier ingen opp, flytter ingen vakt og endrer ingen
-- arbeidsplan. Vaktsetningen sier det rett ut: «ingen
-- personalavgjørelse eller automatisk handling uten separat policy».
--
-- ---------------------------------------------------------------------
-- KLYNGENS DELTE DOM:
--
--   EN GAL PROGNOSE SER NØYAKTIG UT SOM EN RIKTIG PROGNOSE — HELT TIL
--   HORISONTEN ER PASSERT, OG DA HAR ALLE SLUTTET Å SE.
--
-- Men M-33 har en dom TIL, og den er modulens egen:
--
--   EN MODELL SOM IKKE KAN TAPE, HAR IKKE VUNNET.
--
-- Katalogen sier M-33 «backtester mot naive baselines», og klyngen
-- gjorde `slaar_ikke_naiv_baseline` til et funn ingen kan lukke.
-- FUNNET ER BARE EKTE HVIS MODELLEN FAKTISK KAN TAPE FOR BASISLINJEN.
-- En v1 som «prognoserer» ved å kopiere forrige uke ville hatt null
-- avvik mot basislinjen for alltid, invarianten ville vært grønn i all
-- evighet, og den ville ikke målt noe som helst.
--
-- Derfor er v1-modellen et GLIDENDE SNITT over de siste `grunnlag_uker`
-- observerte ukene, og basislinjen er «samme som forrige uke». De to
-- er FORSKJELLIGE tall, og på en tenant med stigende eller sesongpreget
-- bemanning taper snittet regelmessig. Det er meningen.
--
-- ---------------------------------------------------------------------
-- HVA PROGNOSEN HVILER PÅ — alt verifisert mot basen, ikke antatt.
--
-- Fundamentet skrev at M-33s `dep` er «datakvalitet, historikk,
-- evaluering og KPI-katalog». To av de fire finnes ikke som tabeller,
-- og det er tredje gang i denne klyngen at et fundament tildelte data
-- det ikke hadde lest. Lærdommen står uendret:
--
--   ET FUNDAMENT KAN TILDELE NUMRE OG ROLLER UTEN Å LESE KODEN. DET
--   KAN IKKE TILDELE DATA.
--
-- HVA SOM FAKTISK FINNES:
--
--   * `timeregistrering` (M-39, 113) — FAKTISK ARBEIDET TID. Minutter,
--     per person, per dato, med `kilde` i et lukket sett. Dette er den
--     observerte historikken, og den er ekte: den er ført av mennesker
--     eller importert, ikke utledet.
--
--   * `arbeidsplan` (M-39, 113) — PLANLAGT kapasitet,
--     `planlagt_minutter_dag`, versjonert per taker med
--     `gyldig_fra`/`gyldig_til`.
--
--   * `kvalitetsfunn` (M-3, 092) — datakvalitetsflagget. Per tenant,
--     per regel, med et lukket funntypesett.
--
-- HVORFOR HISTORIKKEN ER `timeregistrering` OG IKKE `arbeidsplan`:
-- en prognose regnet på PLANEN prognoserer planen. Den ville vært
-- perfekt og verdiløs — modellen ville spådd nøyaktig det noen alt
-- hadde skrevet ned, og «slår modellen basislinjen?» ville handlet om
-- hvor stabil planleggeren er, ikke om hvor godt vi forstår arbeidet.
-- HISTORIKKEN MÅ VÆRE NOE INGEN BESTEMTE PÅ FORHÅND.
--
-- ---------------------------------------------------------------------
-- FUNNET SOM KOM AV Å LESE M-3: «REN» OG «INGEN HAR SETT ETTER» ER
-- IKKE SAMME TILSTAND.
--
-- `prognose_uten_datakvalitetsflagg` kunne vært løst med en boolsk
-- «data_ok». Det ville vært galt, og galt i den farlige retningen.
--
-- `kvalitetsfunn` er tom for en tenant i to helt ulike tilfeller:
--   (a) M-3 har kjørt og fant ingenting — grunnlaget ER rent;
--   (b) M-3 har aldri kjørt for denne tenanten — vi VET INGENTING.
--
-- En boolsk kolonne ville gjort (b) til (a), og prognosen ville båret
-- et kvalitetsstempel ingen hadde utstedt. Derfor er `datakvalitet` et
-- LUKKET SETT PÅ TRE: `ren`, `flagget`, `ukjent`. Det er samme form
-- som `lukket_av`-dommen i 125: den stille standardverdien er den
-- farlige, og den skal være urepresenterbar.
--
-- OG PROGNOSEN NEKTES ALDRI. Fundamentet slo det fast: en prognose
-- regnet på data M-3 har flagget må BÆRE flagget, ikke avvises. Å
-- nekte ville gjort modulen ubrukelig i nettopp den situasjonen den er
-- nyttigst — når noe er galt og noen må planlegge likevel.
-- =====================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_prognose_eier') THEN
        RAISE EXCEPTION 'rollen disponit_prognose_eier mangler —'
            ' kjør deploy/staging/oppsett-postgresql.sh først';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_prognosesveip') THEN
        RAISE EXCEPTION 'rollen disponit_prognosesveip mangler —'
            ' kjør deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_prognose_eier;
GRANT INSERT ON revisjonslogg TO disponit_prognose_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_prognose_eier;
RESET ROLE;

-- TABELLENE EIES AV MIGRATOREN, FUNKSJONENE AV MODULROLLEN (122-128s
-- form). RLS slås på av en `ALTER TABLE`, og bare eieren kan gjøre
-- det: lager modulrollen tabellene, kan den også ta radvakten AV.

-- ---------------------------------------------------------------------
-- TENANTENS EGNE GRENSER.
--
-- `grunnlag_uker` ER DEN VIKTIGSTE, og den er tenantens valg fordi den
-- er en PÅSTAND OM HVOR RASKT VIRKELIGHETEN ENDRER SEG. Et bemannings-
-- mønster som svinger med sesong trenger et langt vindu; et selskap i
-- vekst trenger et kort, fordi et langt snitt da alltid ligger under.
-- En verdi vi låste ville vært en påstand om kundens drift.
-- ---------------------------------------------------------------------
CREATE TABLE prognosekrav (
    tenant TEXT PRIMARY KEY CHECK (length(btrim(tenant)) > 0),
    horisont_uker INT NOT NULL DEFAULT 8
        CHECK (horisont_uker BETWEEN 1 AND 52),
    -- HVOR MANGE OBSERVERTE UKER MODELLEN FÅR SE. Minst 2: med én uke
    -- ER snittet forrige uke, og da er modellen identisk med
    -- basislinjen sin. En modell som er sin egen basislinje kan ikke
    -- tape, og et funn som ikke kan reises er ikke et funn.
    grunnlag_uker INT NOT NULL DEFAULT 8
        CHECK (grunnlag_uker BETWEEN 2 AND 104),
    -- Hvor lenge etter at en uke er passert vi krever at den er MÅLT.
    -- Nådeperioden finnes fordi timelister føres i etterkant; den er
    -- ikke en unnskyldning for å la være.
    maalefrist_dogn INT NOT NULL DEFAULT 14
        CHECK (maalefrist_dogn BETWEEN 1 AND 180),
    -- HVOR MANGE MÅLTE UKER SOM SKAL TIL FØR VI FELLER DOM OVER
    -- MODELLEN. Å kalle en modell dårligere enn basislinjen etter én
    -- uke er å forveksle støy med kunnskap.
    domsgrunnlag_uker INT NOT NULL DEFAULT 4
        CHECK (domsgrunnlag_uker BETWEEN 2 AND 52),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon > 0),
    -- IDEMPOTENSNØKKELEN LEVER PÅ RADEN (M-51s lærdom 119, gjentatt i
    -- 123, 124, 127 og 128).
    siste_nokkel TEXT,
    satt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    satt_av TEXT NOT NULL CHECK (length(btrim(satt_av)) > 0)
);

-- ---------------------------------------------------------------------
-- MODELLEN — MED BASISLINJEN SKREVET UT.
--
-- Identiteten er FROSSET etter innsetting; bare `gyldig_til` kan
-- settes senere. 121s dom, og den er skarpere her enn noe sted: en
-- modell som kunne redigeres ville gjort hver backtest til en
-- sammenligning mot noe som ikke lenger står noe sted.
-- ---------------------------------------------------------------------
CREATE TABLE prognosemodell (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    modell_id UUID NOT NULL,
    PRIMARY KEY (tenant, modell_id),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    versjon TEXT NOT NULL CHECK (versjon ~ '[^[:space:]]'),
    -- METODEN, SKREVET UT. Ikke en enum: en prognosemetode er en
    -- beskrivelse noen skal kunne etterprøve, ikke et valg fra en
    -- nedtrekksliste.
    metode TEXT NOT NULL CHECK (length(btrim(metode)) >= 16),
    -- BASISLINJEN, NAVNGITT. Uten et navn på det modellen måles mot,
    -- er «slår den basislinjen?» et spørsmål uten referanse.
    baselinje TEXT NOT NULL CHECK (length(btrim(baselinje)) > 0),
    gyldig_fra DATE NOT NULL,
    gyldig_til DATE,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (length(btrim(opprettet_av)) > 0),
    CONSTRAINT prognosemodell_gyldighet CHECK (
        gyldig_til IS NULL OR gyldig_til >= gyldig_fra),
    CONSTRAINT prognosemodell_versjon_unik UNIQUE (tenant, versjon)
);

CREATE INDEX prognosemodell_gjeldende
    ON prognosemodell (tenant, gyldig_fra DESC)
    WHERE gyldig_til IS NULL;

-- ---------------------------------------------------------------------
-- PROGNOSEN — MODULENS TYNGSTE TABELL.
--
-- APPEND-ONLY. En prognose er en PÅSTAND AVGITT PÅ ET TIDSPUNKT. Kunne
-- den redigeres, ville enhver måling vært en sammenligning mot noe som
-- er endret etterpå — altså ingen måling. En prognose som kan justeres
-- i etterkant er en prognose som alltid stemmer.
--
-- `datakvalitet` ER `NOT NULL` OG HAR INGEN STANDARDVERDI PÅ TABELLEN.
-- Døra må ta stilling. Se dommen i toppteksten: en prognose uten et
-- kvalitetsflagg er en prognose som stilltiende påstår at grunnlaget
-- var greit.
-- ---------------------------------------------------------------------
CREATE TABLE bemanningsprognose (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    prognose_id UUID NOT NULL,
    PRIMARY KEY (tenant, prognose_id),
    laget_dato DATE NOT NULL,
    horisont_uker INT NOT NULL CHECK (horisont_uker BETWEEN 1 AND 52),
    -- SNAPSHOTET, ikke en fremmednøkkel til noe som kan endres.
    modell_id UUID NOT NULL,
    modellversjon TEXT NOT NULL CHECK (modellversjon ~ '[^[:space:]]'),
    baselinje TEXT NOT NULL CHECK (length(btrim(baselinje)) > 0),
    -- HVA MODELLEN FAKTISK SÅ. Uten disse kan «var grunnlaget godt
    -- nok?» ikke besvares i ettertid.
    grunnlag_uker INT NOT NULL CHECK (grunnlag_uker BETWEEN 2 AND 104),
    grunnlag_siste_dato DATE NOT NULL,
    grunnlag_antall_uker INT NOT NULL
        CHECK (grunnlag_antall_uker >= 0),
    -- DATAKVALITETSFLAGGET. Tre verdier, og den tredje er hele poenget.
    datakvalitet TEXT NOT NULL
        CONSTRAINT bemanningsprognose_datakvalitet_lukket
        CHECK (datakvalitet IN ('ren', 'flagget', 'ukjent')),
    datakvalitet_antall INT NOT NULL CHECK (datakvalitet_antall >= 0),
    -- `flagget` betyr at det FINNES funn; `ren` og `ukjent` at det
    -- ikke gjør det. Uten denne kunne en rad si «ren» og telle 12.
    CONSTRAINT bemanningsprognose_flagg_teller_stemmer CHECK (
        (datakvalitet = 'flagget') = (datakvalitet_antall > 0)),
    gjelder_til DATE NOT NULL,
    CONSTRAINT bemanningsprognose_horisont_stemmer CHECK (
        gjelder_til = laget_dato + (horisont_uker * 7)),
    laget_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    laget_av TEXT NOT NULL CHECK (length(btrim(laget_av)) > 0)
);

CREATE INDEX bemanningsprognose_ferskeste
    ON bemanningsprognose (tenant, laget_dato DESC);

-- ---------------------------------------------------------------------
-- BANEN — ÉN RAD PER UKE, OG ALDRI ET PUNKT UTEN ET INTERVALL.
--
-- `nedre_minutter` OG `ovre_minutter` ER `NOT NULL`. Det er
-- invarianten `prognose_uten_intervall`, gjort UMULIG i stedet for
-- oppdaget: et punktestimat uten spenn er nøyaktig den formen
-- vaktsetningen forbyr — «prognoser er ikke fakta» — og et tall uten
-- usikkerhet ER et tall som påstår å være et faktum.
--
-- `baseline_minutter` ER OGSÅ `NOT NULL`, og det er invarianten
-- `backtest_uten_baseline`. En baneuke uten basislinje kan aldri
-- inngå i en backtest, og en modell som er delvis umålbar er en
-- modell som kan gjemme sine dårligste uker.
--
-- `ukeslutt` ER UKENS SISTE DAG, IKKE NESTE UKES FØRSTE. Dette er
-- 129s lærdom, tatt med fra fødselen: M-15 skrev først `til` i et
-- halvåpent vindu `[fra, til)` inn i en kolonne som HETER «slutt», og
-- det kostet en egen migrasjon.
-- ---------------------------------------------------------------------
CREATE TABLE bemanningsbane (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    prognose_id UUID NOT NULL,
    uke_nr INT NOT NULL CHECK (uke_nr BETWEEN 1 AND 52),
    PRIMARY KEY (tenant, prognose_id, uke_nr),
    ukeslutt DATE NOT NULL,
    forventet_minutter BIGINT NOT NULL CHECK (forventet_minutter >= 0),
    nedre_minutter BIGINT NOT NULL CHECK (nedre_minutter >= 0),
    ovre_minutter BIGINT NOT NULL CHECK (ovre_minutter >= 0),
    -- DEN NAIVE BASISLINJEN: minutter i den siste HELE uken før
    -- prognosen ble laget. Samme tall for hver uke i banen — det er
    -- nettopp det som gjør den naiv, og det som gjør at et glidende
    -- snitt kan tape for den.
    baseline_minutter BIGINT NOT NULL CHECK (baseline_minutter >= 0),
    CONSTRAINT bemanningsbane_intervall_omslutter CHECK (
        nedre_minutter <= forventet_minutter
        AND forventet_minutter <= ovre_minutter),
    CONSTRAINT bemanningsbane_prognose_fk
        FOREIGN KEY (tenant, prognose_id)
        REFERENCES bemanningsprognose (tenant, prognose_id)
);

-- ---------------------------------------------------------------------
-- MÅLINGEN — DET SOM FAKTISK SKJEDDE.
--
-- APPEND-ONLY OG UKORRIGERBAR. En måling som kunne rettes ville gjort
-- backtesten til en forhandling.
--
-- `avvik_minutter` OG `baseline_avvik_minutter` ER GENERERTE, ikke
-- oppgitt av kalleren. En kaller som fikk oppgi sitt eget avvik kunne
-- oppgi null. Samme dom som M-3s `andel_avvik` (092): et tall som
-- utledes av andre tall i raden, skal utledes AV BASEN.
-- ---------------------------------------------------------------------
CREATE TABLE bemanningsmaaling (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    prognose_id UUID NOT NULL,
    uke_nr INT NOT NULL CHECK (uke_nr BETWEEN 1 AND 52),
    PRIMARY KEY (tenant, prognose_id, uke_nr),
    faktisk_minutter BIGINT NOT NULL CHECK (faktisk_minutter >= 0),
    -- Kopiert fra banen ved måling, slik at avviket kan regnes i
    -- raden og ikke avhenger av et join som kan endres.
    forventet_minutter BIGINT NOT NULL CHECK (forventet_minutter >= 0),
    baseline_minutter BIGINT NOT NULL CHECK (baseline_minutter >= 0),
    innenfor_intervall BOOLEAN NOT NULL,
    avvik_minutter BIGINT GENERATED ALWAYS AS (
        abs(faktisk_minutter - forventet_minutter)) STORED,
    baseline_avvik_minutter BIGINT GENERATED ALWAYS AS (
        abs(faktisk_minutter - baseline_minutter)) STORED,
    maalt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    maalt_av TEXT NOT NULL CHECK (length(btrim(maalt_av)) > 0),
    CONSTRAINT bemanningsmaaling_bane_fk
        FOREIGN KEY (tenant, prognose_id, uke_nr)
        REFERENCES bemanningsbane (tenant, prognose_id, uke_nr)
);

-- ---------------------------------------------------------------------
-- FUNNENE. Lukket funntypesett (099s form), ett åpent funn per
-- (tenant, funntype, referanse).
--
-- `slaar_ikke_naiv_baseline` KAN IKKE LUKKES AV ET MENNESKE. Det er
-- klyngens dom: en modell som taper for «samme som forrige uke» bærer
-- autoritet den ikke har fortjent, og den autoriteten tas ikke bort
-- ved at noen huker av. Den tas bort ved at modellen blir bedre eller
-- at noen avvikler den.
-- ---------------------------------------------------------------------
CREATE TABLE prognosefunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    funn_id UUID NOT NULL,
    PRIMARY KEY (tenant, funn_id),
    funntype TEXT NOT NULL
        CONSTRAINT prognosefunn_type_lukket CHECK (funntype IN (
            'prognose_uten_maaling',
            'slaar_ikke_naiv_baseline',
            'prognose_paa_ukjent_datakvalitet',
            'modell_uten_prognose')),
    referanse TEXT NOT NULL CHECK (referanse ~ '[^[:space:]]'),
    detaljer TEXT NOT NULL CHECK (detaljer ~ '[^[:space:]]'),
    over_grense BIGINT NOT NULL DEFAULT 0,
    apen BOOLEAN NOT NULL DEFAULT true,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 125s DOM, TATT MED FRA FØDSELEN: et lukket funn UTEN et navn er
    -- urepresenterbart. Sveipen skriver sitt eget navn, mennesket sitt.
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT,
    lukket_begrunnelse TEXT,
    CONSTRAINT prognosefunn_lukking_har_navn CHECK (
        apen
        OR (lukket_ts IS NOT NULL
            AND lukket_av IS NOT NULL
            AND length(btrim(lukket_av)) > 0)),
    CONSTRAINT prognosefunn_apen_er_ulukket CHECK (
        NOT apen OR (lukket_ts IS NULL AND lukket_av IS NULL))
);

CREATE UNIQUE INDEX prognosefunn_ett_apent
    ON prognosefunn (tenant, funntype, referanse) WHERE apen;

-- =====================================================================
-- APPEND-ONLY-VAKTENE.
--
-- Prognosen, banen og målingen er evidens. Målingen er den strengeste:
-- den kan ikke engang RETTES, fordi en måling som kan rettes er en
-- forhandling om hvor god modellen var.
--
-- VAKTENE GJELDER OGSÅ MIGRATOR. Det er ikke pedanteri: den eneste
-- grunnen til å slå av en append-only-vakt i en migrasjon er å rette
-- et tall i evidens, og det er nøyaktig det tabellen finnes for å
-- hindre. (Se #393: da 129 endret betydningen av en kolonne, var
-- svaret en port som måler skriveren — ikke en UPDATE som retter
-- historikken.)
-- =====================================================================
CREATE OR REPLACE FUNCTION m33_evidensvakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION '%: % avvist — en prognose eller måling som kan'
        ' endres i ettertid er ikke evidens',
        TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE ALL ON FUNCTION m33_evidensvakt() FROM PUBLIC;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['bemanningsprognose', 'bemanningsbane',
                             'bemanningsmaaling'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS m33_evidensvakt'
                       ' ON public.%I', t);
        EXECUTE format('CREATE TRIGGER m33_evidensvakt'
                       ' BEFORE UPDATE OR DELETE ON public.%I'
                       ' FOR EACH ROW'
                       ' EXECUTE FUNCTION public.m33_evidensvakt()', t);
        EXECUTE format('DROP TRIGGER IF EXISTS m33_ingen_truncate'
                       ' ON public.%I', t);
        EXECUTE format('CREATE TRIGGER m33_ingen_truncate'
                       ' BEFORE TRUNCATE ON public.%I'
                       ' FOR EACH STATEMENT'
                       ' EXECUTE FUNCTION public.avvis_endring()', t);
    END LOOP;
END $$;

-- MODELLENS IDENTITET ER FROSSET; BARE `gyldig_til` KAN SETTES.
CREATE OR REPLACE FUNCTION m33_modellvakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'prognosemodell: sletting avvist — en modell'
            ' andre prognoser peker på kan ikke forsvinne'
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
        RAISE EXCEPTION 'prognosemodell: identiteten er frosset —'
            ' bare gyldig_til kan settes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- AVVIKLING ER ENVEIS. En modell som kunne gjenoppvekkes ville
    -- gjort «hvilken modell gjaldt da?» ubesvarlig.
    IF OLD.gyldig_til IS NOT NULL
       AND NEW.gyldig_til IS DISTINCT FROM OLD.gyldig_til THEN
        RAISE EXCEPTION 'prognosemodell: en avviklet modell kan ikke'
            ' avvikles på nytt eller gjenoppvekkes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m33_modellvakt() FROM PUBLIC;
DROP TRIGGER IF EXISTS m33_modellvakt ON prognosemodell;
CREATE TRIGGER m33_modellvakt
    BEFORE UPDATE OR DELETE ON prognosemodell
    FOR EACH ROW EXECUTE FUNCTION m33_modellvakt();

-- =====================================================================
-- RADVAKTEN. `FORCE`, fordi eieren ellers er unntatt — og da er
-- `tenantlekkasje_i_prognoseregister` en invariant uten håndhevelse.
-- =====================================================================
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['prognosekrav', 'prognosemodell',
                             'bemanningsprognose', 'bemanningsbane',
                             'bemanningsmaaling', 'prognosefunn'] LOOP
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
        -- MODULROLLEN EIER DØRENE, MEN IKKE TABELLENE. Dørene er
        -- `SECURITY DEFINER` og løper som `disponit_prognose_eier`,
        -- så uten denne granten møter enhver dør «permission denied»
        -- på sin egen tabell. Riggen sa det med én gang; lesing av
        -- filen ville ikke ha gjort det.
        EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO'
                       ' disponit_prognose_eier', t);
    END LOOP;
END $$;

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER (111s form,
-- 112-128): bare `FOR SELECT`, bare til eieren, og bare NÅR
-- TENANTKONTEKSTEN ER TOM. Sveipen trenger å lese tenantlisten FØR
-- den setter konteksten — uten denne policyen ville løkka aldri fått
-- en eneste tenant, og sveipen ville rapportert null tenanter og null
-- funn med grønn exit-kode.
--
-- 122s LÆRDOM: policyen må stå på HVERT register sveipens tenantliste
-- leser. Her er det ett — `prognosekrav` — og at det er ett er en
-- egenskap ved sveipen, ikke en tilfeldighet: løkka henter tenantene
-- ETT sted, med vilje.
CREATE POLICY m33_sveip_tenantliste ON prognosekrav
    FOR SELECT TO disponit_prognose_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- HISTORIKKTABELLENE FÅR IKKE UPDATE.
--
-- `bemanningsprognose`, `bemanningsbane` og `bemanningsmaaling` er
-- HELT lukket. Append-only-triggeren ville uansett avvist en UPDATE,
-- men en rettighet som ikke finnes er sterkere enn en trigger som
-- nekter: triggere kan slås av av tabellens eier, rettigheter må
-- gis på nytt.
REVOKE UPDATE ON public.bemanningsprognose
    FROM disponit_prognose_eier;
REVOKE UPDATE ON public.bemanningsbane FROM disponit_prognose_eier;
REVOKE UPDATE ON public.bemanningsmaaling
    FROM disponit_prognose_eier;

-- `prognosemodell` FÅR BARE ENDRE `gyldig_til` (121s dom).
REVOKE UPDATE ON public.prognosemodell FROM disponit_prognose_eier;
GRANT UPDATE (gyldig_til) ON public.prognosemodell
    TO disponit_prognose_eier;

-- INGEN AV TABELLENE FÅR SLETTES. `DELETE` står ikke i noen GRANT
-- over — lista er `SELECT, INSERT, UPDATE`. Det står her fordi et
-- FRAVÆR er lettere å overse enn en setning, og porten leser begge.

-- =====================================================================
-- HERFRA EIES DØRENE AV PROGNOSEEIEREN.
-- =====================================================================
SET LOCAL ROLE disponit_prognose_eier;

-- FUNNENE INGEN KAN LUKKE, SOM EN FUNKSJON OG IKKE EN HUSKEREGEL.
-- Lista står her, én gang, og både lukkedøra og lesedøra leser den.
CREATE FUNCTION m33_funn_er_sveipens(p_funntype TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT p_funntype IN ('prognose_uten_maaling',
                          'slaar_ikke_naiv_baseline')
$$;
REVOKE ALL ON FUNCTION m33_funn_er_sveipens(TEXT) FROM PUBLIC;

-- STABLE, IKKE IMMUTABLE (125s lærdom). Funksjonen leser
-- `current_date`, og planleggeren har LOV til å folde en IMMUTABLE
-- funksjon til en konstant og gjenbruke den i en bufret plan.
CREATE FUNCTION m33_modell_gyldig(p_fra DATE, p_til DATE)
RETURNS BOOLEAN LANGUAGE sql STABLE
SET search_path = pg_catalog AS $$
    SELECT p_fra <= current_date
       AND (p_til IS NULL OR p_til >= current_date)
$$;
REVOKE ALL ON FUNCTION m33_modell_gyldig(DATE, DATE) FROM PUBLIC;

-- EVIDENSKJEDEN. `input_hash` er sha256 over den KANONISKE
-- BESKRIVELSEN AV HANDLINGEN, ikke over bemanningsdata: en
-- evidenskjede som arkiverte timetallene ville vært et nytt sted
-- personopplysninger lå lagret, og et som aldri kan rettes.
CREATE FUNCTION m33_evidens(p_tenant TEXT, p_ref UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm33_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm33_prognose', 'handling', p_handling,
        'ref', p_ref::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm33_prognose',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:prognose', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m33_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- ---------------------------------------------------------------------
-- DATAKVALITETSFLAGGET, SOM EN FUNKSJON MED ÉN JOBB.
--
-- Den skiller `ren` fra `ukjent`, og det skillet er hele grunnen til
-- at kolonnen har tre verdier og ikke to. Se toppteksten.
--
-- STABLE: den leser tabeller.
-- ---------------------------------------------------------------------
CREATE FUNCTION m33_datakvalitet(p_tenant TEXT)
RETURNS TABLE (flagg TEXT, antall INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_funn INT;
    v_kjort BOOLEAN;
BEGIN
    SELECT count(*) INTO v_funn
      FROM public.kvalitetsfunn WHERE tenant = p_tenant;

    -- HAR M-3 I DET HELE TATT KJØRT? `kvalitetskjoring` har ingen
    -- `tenant`-kolonne — den er husets kjøringshode, ikke tenantens —
    -- så spørsmålet er om PROFILEN finnes for denne tenanten. En
    -- tenant M-3 aldri har profilert, er `ukjent`.
    SELECT EXISTS (SELECT 1 FROM public.kvalitetsprofil
                    WHERE tenant = p_tenant) INTO v_kjort;

    IF v_funn > 0 THEN
        RETURN QUERY SELECT 'flagget'::TEXT, v_funn;
    ELSIF v_kjort THEN
        RETURN QUERY SELECT 'ren'::TEXT, 0;
    ELSE
        RETURN QUERY SELECT 'ukjent'::TEXT, 0;
    END IF;
END $$;
REVOKE ALL ON FUNCTION m33_datakvalitet(TEXT) FROM PUBLIC;

-- =====================================================================
-- DØRENE.
-- =====================================================================

CREATE FUNCTION m33_sett_krav(p_tenant TEXT, p_horisont INT,
                              p_grunnlag INT, p_maalefrist INT,
                              p_domsgrunnlag INT, p_aktor TEXT,
                              p_nokkel TEXT)
RETURNS TABLE (horisont_uker INT, grunnlag_uker INT,
               maalefrist_dogn INT, domsgrunnlag_uker INT,
               versjon INT, endret BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_rad public.prognosekrav%ROWTYPE;
    v_endret BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm33_sett_krav');
    IF p_nokkel IS NULL OR btrim(p_nokkel) = '' THEN
        RAISE EXCEPTION 'm33_sett_krav: idempotensnøkkel mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- LÅS FØRST, LES ETTERPÅ (klynge 6s lærdom): en lesing før
    -- `FOR UPDATE` bruker transaksjonens snapshot, og to samtidige
    -- kall ville begge sett den gamle raden.
    SELECT * INTO v_rad FROM public.prognosekrav
     WHERE tenant = p_tenant FOR UPDATE;

    IF FOUND AND v_rad.siste_nokkel IS NOT DISTINCT FROM p_nokkel THEN
        RETURN QUERY SELECT v_rad.horisont_uker, v_rad.grunnlag_uker,
                            v_rad.maalefrist_dogn,
                            v_rad.domsgrunnlag_uker,
                            v_rad.versjon, false;
        RETURN;
    END IF;

    v_endret := NOT FOUND
        OR v_rad.horisont_uker IS DISTINCT FROM p_horisont
        OR v_rad.grunnlag_uker IS DISTINCT FROM p_grunnlag
        OR v_rad.maalefrist_dogn IS DISTINCT FROM p_maalefrist
        OR v_rad.domsgrunnlag_uker IS DISTINCT FROM p_domsgrunnlag;

    INSERT INTO public.prognosekrav
        (tenant, horisont_uker, grunnlag_uker, maalefrist_dogn,
         domsgrunnlag_uker, versjon, siste_nokkel, satt_av)
    VALUES (p_tenant, p_horisont, p_grunnlag, p_maalefrist,
            p_domsgrunnlag, 1, p_nokkel, p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        horisont_uker = EXCLUDED.horisont_uker,
        grunnlag_uker = EXCLUDED.grunnlag_uker,
        maalefrist_dogn = EXCLUDED.maalefrist_dogn,
        domsgrunnlag_uker = EXCLUDED.domsgrunnlag_uker,
        -- VERSJONEN ØKER BARE NÅR EN GRENSE FAKTISK ENDRET SEG. En
        -- versjon som økte for hvert gjenspill ville gjort
        -- funnhistorikken uleselig (M-51s lærdom 119).
        versjon = public.prognosekrav.versjon
                  + CASE WHEN v_endret THEN 1 ELSE 0 END,
        siste_nokkel = EXCLUDED.siste_nokkel,
        satt_ts = now(), satt_av = EXCLUDED.satt_av
    RETURNING * INTO v_rad;

    PERFORM public.m33_evidens(p_tenant, NULL, 'sett_krav', p_aktor,
        jsonb_build_object('versjon', v_rad.versjon,
                           'endret', v_endret));
    RETURN QUERY SELECT v_rad.horisont_uker, v_rad.grunnlag_uker,
                        v_rad.maalefrist_dogn,
                        v_rad.domsgrunnlag_uker, v_rad.versjon,
                        v_endret;
END $$;
REVOKE ALL ON FUNCTION m33_sett_krav(TEXT, INT, INT, INT, INT, TEXT,
                                     TEXT) FROM PUBLIC;

-- MODELLDØRA. En avviklet versjon KAN registreres: arkivet skal kunne
-- svare på hvilken modell som gjaldt den gangen. Skillet går ved
-- PROGNOSEN — `m33_lag_prognose` nekter mot en modell som ikke
-- gjelder i dag (121s dom, 124/127/128s form).
CREATE FUNCTION m33_registrer_modell(
    p_tenant TEXT, p_modell_id UUID, p_navn TEXT, p_versjon TEXT,
    p_metode TEXT, p_baselinje TEXT, p_gyldig_fra DATE,
    p_gyldig_til DATE, p_aktor TEXT)
RETURNS TABLE (modell_id UUID, gjelder BOOLEAN, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.prognosemodell%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm33_registrer_modell');

    -- GJENSPILL FØRST (SP-2). API-et utleder `modell_id` av
    -- Idempotency-Key-en, og modellens identitet er FROSSET etter
    -- innsetting — et gjenspill kan derfor ikke skrive på nytt, det
    -- må svare med raden.
    SELECT * INTO v_rad FROM public.prognosemodell
     WHERE tenant = p_tenant AND prognosemodell.modell_id
                                 = p_modell_id;
    IF FOUND THEN
        IF v_rad.versjon IS DISTINCT FROM p_versjon THEN
            RAISE EXCEPTION 'm33_registrer_modell: modell % finnes'
                ' med en annen versjon (%)', p_modell_id,
                v_rad.versjon
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN QUERY SELECT v_rad.modell_id,
                            public.m33_modell_gyldig(v_rad.gyldig_fra,
                                                     v_rad.gyldig_til),
                            false;
        RETURN;
    END IF;

    INSERT INTO public.prognosemodell
        (tenant, modell_id, navn, versjon, metode, baselinje,
         gyldig_fra, gyldig_til, opprettet_av)
    VALUES (p_tenant, p_modell_id, p_navn, p_versjon, p_metode,
            p_baselinje, p_gyldig_fra, p_gyldig_til, p_aktor)
    RETURNING * INTO v_rad;

    PERFORM public.m33_evidens(p_tenant, p_modell_id,
        'registrer_modell', p_aktor,
        jsonb_build_object('versjon', p_versjon));
    RETURN QUERY SELECT v_rad.modell_id,
                        public.m33_modell_gyldig(v_rad.gyldig_fra,
                                                 v_rad.gyldig_til),
                        true;
END $$;
REVOKE ALL ON FUNCTION m33_registrer_modell(TEXT, UUID, TEXT, TEXT,
    TEXT, TEXT, DATE, DATE, TEXT) FROM PUBLIC;

-- AVVIKLINGSDØRA. Eneste lovlige endring på en modell, og den er
-- enveis (vakten håndhever det).
CREATE FUNCTION m33_avvikle_modell(p_tenant TEXT, p_modell_id UUID,
                                   p_gyldig_til DATE, p_aktor TEXT)
RETURNS TABLE (modell_id UUID, gyldig_til DATE)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.prognosemodell%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm33_avvikle_modell');
    UPDATE public.prognosemodell SET gyldig_til = p_gyldig_til
     WHERE tenant = p_tenant AND prognosemodell.modell_id = p_modell_id
    RETURNING * INTO v_rad;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm33_avvikle_modell: modellen finnes ikke'
            USING ERRCODE = 'no_data_found';
    END IF;
    PERFORM public.m33_evidens(p_tenant, p_modell_id,
        'avvikle_modell', p_aktor,
        jsonb_build_object('gyldig_til', p_gyldig_til));
    RETURN QUERY SELECT v_rad.modell_id, v_rad.gyldig_til;
END $$;
REVOKE ALL ON FUNCTION m33_avvikle_modell(TEXT, UUID, DATE, TEXT)
    FROM PUBLIC;

-- ---------------------------------------------------------------------
-- PROGNOSEDØRA — MODULENS TYNGSTE FUNKSJON.
--
-- UKEVINDUENE ER HALVÅPNE `[fra, til)` (husets definisjon siden M-16),
-- OG `ukeslutt` ER `til - 1`. Skrevet slik fra fødselen, fordi M-15
-- gjorde det motsatt i 128 og måtte ha en egen migrasjon (129) for å
-- rette det. En dag i sprekken mellom to uker er penger — eller her:
-- timer — som ikke telles noe sted.
--
-- OBSERVASJONSVINDUENE ER DE `grunnlag_uker` HELE 7-DAGERSBLOKKENE
-- RETT FØR `laget_dato`. Blokk 1 er den ferskeste, og den ER
-- basislinjen: «samme som forrige uke».
--
-- HVORFOR MODELLEN OG BASISLINJEN MÅ VÆRE ULIKE TALL: se toppteksten.
-- Snittet over `grunnlag_uker` blokker er ikke blokk 1, med mindre
-- alle blokkene er like — og da er det heller ingen forskjell å måle.
--
-- ---------------------------------------------------------------------
-- DØRA NEKTER NÅR DET IKKE FINNES HISTORIKK, OG DET ER EN DOM:
--
-- En tenant uten en eneste timeregistrering ville fått
-- `forventet_minutter = 0` av et snitt over ingenting. NULL ARBEID ER
-- IKKE DET SAMME SOM INGEN DATA, og en prognose som sier «null timer
-- neste uke» fordi ingen har ført timer, er den reneste formen for
-- `prognose_presentert_som_faktum`: modellen påstår noe om
-- virkeligheten når den bare har målt sin egen tomhet.
--
-- Dette er samme feilform som `Number("")` → 0 i M-15s flate, og som
-- `ren` versus `ukjent` over: TOMHET SOM BLIR TIL ET TALL.
-- ---------------------------------------------------------------------
CREATE FUNCTION m33_lag_prognose(
    p_tenant TEXT, p_prognose_id UUID, p_modell_id UUID, p_aktor TEXT)
RETURNS TABLE (prognose_id UUID, horisont_uker INT,
               grunnlag_antall_uker INT, datakvalitet TEXT,
               baseline_minutter BIGINT, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_krav public.prognosekrav%ROWTYPE;
    v_modell public.prognosemodell%ROWTYPE;
    v_gml public.bemanningsprognose%ROWTYPE;
    v_dato DATE := current_date;
    v_flagg TEXT;
    v_antall INT;
    v_snitt NUMERIC;
    v_spredning NUMERIC;
    v_baseline BIGINT;
    v_blokker INT;
    v_siste DATE;
    v_punkt BIGINT;
    v_avvik BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm33_lag_prognose');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm33_lag_prognose: en prognose bærer navnet'
            ' til den som ba om den'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- GJENSPILL FØRST (SP-2, 127/128s form; CodeRabbit fant at det
    -- MANGLET her). API-et utleder `prognose_id` av
    -- Idempotency-Key-en, så et gjenspill treffer samme id — og uten
    -- denne grenen ville den truffet primærnøkkelen og gitt 400 på et
    -- helt lovlig gjentatt kall.
    --
    -- INGEN `FOR UPDATE` HER, OG DET ER IKKE EN FORGLEMMELSE:
    -- `FOR UPDATE` KREVER UPDATE-RETT, og §RETTIGHETER har REVOKEd
    -- den fra modulrollen nettopp fordi tabellen er append-only. En
    -- lås ville feilet med «permission denied» på en dør som gjør alt
    -- riktig — en lærdom huset har betalt for før (128).
    --
    -- Låsen trengs heller ikke: raden kan ALDRI endres, så det finnes
    -- ingenting å beskytte mot. Kappløpet mellom to samtidige kall
    -- med samme id fanges av primærnøkkelen.
    SELECT * INTO v_gml FROM public.bemanningsprognose
     WHERE tenant = p_tenant AND bemanningsprognose.prognose_id
                                 = p_prognose_id;
    IF FOUND THEN
        -- SAMME NØKKEL, ANNEN MODELL, ER IKKE ET GJENSPILL. Det er to
        -- ulike forespørsler som deler nøkkel, og å svare med den
        -- første ville skjult at den andre aldri ble utført.
        IF v_gml.modell_id IS DISTINCT FROM p_modell_id THEN
            RAISE EXCEPTION 'm33_lag_prognose: prognose % finnes mot'
                ' en annen modell', p_prognose_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN QUERY SELECT
            v_gml.prognose_id, v_gml.horisont_uker,
            v_gml.grunnlag_antall_uker, v_gml.datakvalitet,
            (SELECT max(b.baseline_minutter)
               FROM public.bemanningsbane b
              WHERE b.tenant = p_tenant
                AND b.prognose_id = p_prognose_id),
            false;
        RETURN;
    END IF;

    SELECT * INTO v_krav FROM public.prognosekrav
     WHERE tenant = p_tenant;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm33_lag_prognose: tenanten har ingen'
            ' registrerte prognosegrenser'
            USING ERRCODE = 'no_data_found';
    END IF;

    SELECT * INTO v_modell FROM public.prognosemodell
     WHERE tenant = p_tenant AND modell_id = p_modell_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm33_lag_prognose: modellen finnes ikke'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT public.m33_modell_gyldig(v_modell.gyldig_fra,
                                    v_modell.gyldig_til) THEN
        RAISE EXCEPTION 'm33_lag_prognose: modell % gjelder ikke i'
            ' dag — en prognose laget av en avviklet modell bærer en'
            ' autoritet ingen har gitt den', v_modell.versjon
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- OBSERVASJONSBLOKKENE. Blokk k dekker
    -- [v_dato - 7k, v_dato - 7(k-1)) — halvåpent, som resten av huset.
    --
    -- EN CTE OG IKKE EN TEMP-TABELL: en `SECURITY DEFINER`-dør som
    -- lager temp-tabeller krever `TEMP` på basen av den som kaller,
    -- og da hadde SP-7-skillet lekket ut i en rettighet ingen hadde
    -- bedt om. Alt som trengs, hentes i ett svep.
    WITH blokk AS (
        SELECT b.k, coalesce(sum(t.minutter), 0) AS minutter
          FROM generate_series(1, v_krav.grunnlag_uker) AS b(k)
          LEFT JOIN public.timeregistrering t
                 ON t.tenant = p_tenant
                AND t.dato >= v_dato - (b.k * 7)
                AND t.dato <  v_dato - ((b.k - 1) * 7)
         GROUP BY b.k)
    -- `v_blokker` teller blokkene som FAKTISK HADDE ARBEID. En blokk
    -- med null minutter er en ekte observasjon («ingen jobbet den
    -- uken»); NULL slike blokker betyr at vi ikke har historikk i det
    -- hele tatt — og da nekter døra.
    SELECT count(*) FILTER (WHERE minutter > 0),
           avg(minutter),
           coalesce(stddev_pop(minutter), 0),
           max(minutter) FILTER (WHERE k = 1)
      INTO v_blokker, v_snitt, v_spredning, v_baseline
      FROM blokk;

    SELECT max(t.dato) INTO v_siste FROM public.timeregistrering t
     WHERE t.tenant = p_tenant AND t.dato < v_dato;

    IF v_blokker = 0 OR v_siste IS NULL THEN
        RAISE EXCEPTION 'm33_lag_prognose: ingen timeregistrering i'
            ' de siste % ukene — en prognose på null observasjoner'
            ' ville påstått at ingen jobber, ikke at vi ikke vet',
            v_krav.grunnlag_uker
            USING ERRCODE = 'no_data_found';
    END IF;

    v_punkt := round(v_snitt)::BIGINT;

    -- INTERVALLETS MINSTEBREDDE. Med én observert blokk, eller med
    -- blokker som tilfeldigvis er like, ville spredningen vært null —
    -- og et intervall med bredde null er et PUNKT som later som det
    -- er et intervall. Det er nøyaktig løgnen `prognose_uten_intervall`
    -- finnes for å hindre, og en kolonne som er `NOT NULL` fanger den
    -- ikke: null er en gyldig verdi.
    v_avvik := greatest(ceil(v_spredning)::BIGINT,
                        ceil(v_punkt * 0.10)::BIGINT,
                        CASE WHEN v_punkt > 0 THEN 1 ELSE 0 END);

    SELECT flagg, antall INTO v_flagg, v_antall
      FROM public.m33_datakvalitet(p_tenant);

    INSERT INTO public.bemanningsprognose
        (tenant, prognose_id, laget_dato, horisont_uker, modell_id,
         modellversjon, baselinje, grunnlag_uker, grunnlag_siste_dato,
         grunnlag_antall_uker, datakvalitet, datakvalitet_antall,
         gjelder_til, laget_av)
    VALUES (p_tenant, p_prognose_id, v_dato, v_krav.horisont_uker,
            p_modell_id, v_modell.versjon, v_modell.baselinje,
            v_krav.grunnlag_uker, v_siste, v_blokker, v_flagg,
            v_antall, v_dato + (v_krav.horisont_uker * 7), p_aktor);

    INSERT INTO public.bemanningsbane
        (tenant, prognose_id, uke_nr, ukeslutt, forventet_minutter,
         nedre_minutter, ovre_minutter, baseline_minutter)
    SELECT p_tenant, p_prognose_id, u.n,
           -- `til - 1`: ukens SISTE dag. 129s lærdom.
           (v_dato + (u.n * 7)) - 1,
           v_punkt,
           greatest(v_punkt - v_avvik, 0),
           v_punkt + v_avvik,
           v_baseline
      FROM generate_series(1, v_krav.horisont_uker) AS u(n);

    PERFORM public.m33_evidens(p_tenant, p_prognose_id,
        'lag_prognose', p_aktor,
        jsonb_build_object('modellversjon', v_modell.versjon,
                           'grunnlag_uker', v_blokker,
                           'datakvalitet', v_flagg));

    RETURN QUERY SELECT p_prognose_id, v_krav.horisont_uker,
                        v_blokker, v_flagg, v_baseline, true;
END $$;
REVOKE ALL ON FUNCTION m33_lag_prognose(TEXT, UUID, UUID, TEXT)
    FROM PUBLIC;

-- ---------------------------------------------------------------------
-- MÅLEDØRA — DEN SOM LUKKER `prognose_uten_maaling`.
--
-- DØRA NEKTER FØR UKA ER OVER. `ukeslutt` er ukens SISTE dag, så uka
-- er ferdig først når `ukeslutt < current_date`. Å måle en uke som
-- ennå løper er å registrere et delresultat som et sluttresultat —
-- og siden målingen er ukorrigerbar, ville det tallet stått for evig.
-- (129s dom, innebygd her fra fødselen.)
--
-- `faktisk_minutter` OPPGIS AV KALLEREN OG UTLEDES IKKE AV
-- `timeregistrering`. Det er et bevisst valg: timelister etterfylles,
-- og en måling som leste tabellen på nytt ville gitt et annet svar
-- hver gang den ble kjørt. Målingen er en PÅSTAND AVGITT AV ET
-- MENNESKE PÅ ET TIDSPUNKT, med et navn på.
-- ---------------------------------------------------------------------
CREATE FUNCTION m33_registrer_maaling(
    p_tenant TEXT, p_prognose_id UUID, p_uke INT,
    p_faktisk BIGINT, p_aktor TEXT)
RETURNS TABLE (uke_nr INT, avvik_minutter BIGINT,
               baseline_avvik_minutter BIGINT,
               innenfor_intervall BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_bane public.bemanningsbane%ROWTYPE;
    v_rad public.bemanningsmaaling%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm33_registrer_maaling');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm33_registrer_maaling: en måling uten et'
            ' navn på er ikke evidens'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT * INTO v_bane FROM public.bemanningsbane
     WHERE tenant = p_tenant AND prognose_id = p_prognose_id
       AND bemanningsbane.uke_nr = p_uke;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm33_registrer_maaling: uke % finnes ikke i'
            ' denne banen', p_uke
            USING ERRCODE = 'no_data_found';
    END IF;

    IF v_bane.ukeslutt >= current_date THEN
        RAISE EXCEPTION 'm33_registrer_maaling: uke % er ikke over'
            ' (slutter %) — en ukorrigerbar måling av en uke som'
            ' ennå løper er et delresultat som aldri kan rettes',
            p_uke, v_bane.ukeslutt
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    BEGIN
        INSERT INTO public.bemanningsmaaling
            (tenant, prognose_id, uke_nr, faktisk_minutter,
             forventet_minutter, baseline_minutter,
             innenfor_intervall, maalt_av)
        VALUES (p_tenant, p_prognose_id, p_uke, p_faktisk,
                v_bane.forventet_minutter, v_bane.baseline_minutter,
                p_faktisk BETWEEN v_bane.nedre_minutter
                              AND v_bane.ovre_minutter,
                p_aktor)
        RETURNING * INTO v_rad;
    EXCEPTION WHEN unique_violation THEN
        -- ALLEREDE MÅLT. Ikke en feil, og ikke en overskriving: den
        -- første målingen står. Append-only-vakten ville uansett
        -- avvist en `ON CONFLICT DO UPDATE`, og det er meningen.
        RAISE EXCEPTION 'm33_registrer_maaling: uke % er allerede'
            ' målt — en måling kan ikke rettes', p_uke
            USING ERRCODE = 'unique_violation';
    END;

    PERFORM public.m33_evidens(p_tenant, p_prognose_id,
        'registrer_maaling', p_aktor,
        jsonb_build_object('uke', p_uke,
                           'innenfor', v_rad.innenfor_intervall));
    RETURN QUERY SELECT v_rad.uke_nr, v_rad.avvik_minutter,
                        v_rad.baseline_avvik_minutter,
                        v_rad.innenfor_intervall;
END $$;
REVOKE ALL ON FUNCTION m33_registrer_maaling(TEXT, UUID, INT, BIGINT,
                                             TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- LESEDØRENE.
--
-- `m33_banen` GIR ALDRI ET PUNKT UTEN SITT INTERVALL. Det er
-- invarianten `prognose_presentert_som_faktum`, håndhevet der den
-- faktisk kan brytes: i det som forlater basen. En flate kan velge å
-- ikke tegne båndet, men den kan ikke få et svar der båndet mangler.
-- ---------------------------------------------------------------------
CREATE FUNCTION m33_banen(p_tenant TEXT, p_prognose_id UUID)
RETURNS TABLE (uke_nr INT, ukeslutt DATE, forventet_minutter BIGINT,
               nedre_minutter BIGINT, ovre_minutter BIGINT,
               baseline_minutter BIGINT, faktisk_minutter BIGINT,
               avvik_minutter BIGINT,
               baseline_avvik_minutter BIGINT,
               innenfor_intervall BOOLEAN, kan_maales BOOLEAN)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT b.uke_nr, b.ukeslutt, b.forventet_minutter,
           b.nedre_minutter, b.ovre_minutter, b.baseline_minutter,
           m.faktisk_minutter, m.avvik_minutter,
           m.baseline_avvik_minutter, m.innenfor_intervall,
           -- SAMME PREDIKAT SOM DØRA. Står det to steder med to
           -- formuleringer, blir knappen aktiv en dag før døra sier ja.
           (m.uke_nr IS NULL AND b.ukeslutt < current_date)
      FROM public.bemanningsbane b
      LEFT JOIN public.bemanningsmaaling m
             ON m.tenant = b.tenant
            AND m.prognose_id = b.prognose_id
            AND m.uke_nr = b.uke_nr
     WHERE b.tenant = p_tenant AND b.prognose_id = p_prognose_id
     ORDER BY b.uke_nr
$$;
REVOKE ALL ON FUNCTION m33_banen(TEXT, UUID) FROM PUBLIC;

-- SAMMENDRAGET. Flaten tegner tallene øverst uten å regne dem selv:
-- en flate som summerte ville hatt sin egen mening om hva «målt»
-- betyr, og to meninger om det samme er én for mange.
--
-- `uker_umaalt` TELLER BARE UKER SOM ER OVER. En uke som fortsatt
-- løper er ikke umålt — den er ikke målbar ennå, og å telle den ville
-- gjort tallet til en anklage mot noen som ikke har gjort noe galt.
CREATE FUNCTION m33_bildet(p_tenant TEXT)
RETURNS TABLE (prognoser INT, modeller INT, gyldige_modeller INT,
               uker_totalt INT, uker_maalt INT, uker_umaalt INT,
               treff INT, bom INT, apne_funn INT, har_krav BOOLEAN,
               horisont_uker INT, grunnlag_uker INT,
               maalefrist_dogn INT, domsgrunnlag_uker INT,
               kravversjon INT, prognoser_ukjent_kvalitet INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT
      (SELECT count(*)::INT FROM public.bemanningsprognose p
        WHERE p.tenant = p_tenant),
      (SELECT count(*)::INT FROM public.prognosemodell m
        WHERE m.tenant = p_tenant),
      (SELECT count(*)::INT FROM public.prognosemodell m
        WHERE m.tenant = p_tenant
          AND public.m33_modell_gyldig(m.gyldig_fra, m.gyldig_til)),
      (SELECT count(*)::INT FROM public.bemanningsbane b
        WHERE b.tenant = p_tenant),
      (SELECT count(*)::INT FROM public.bemanningsmaaling m
        WHERE m.tenant = p_tenant),
      (SELECT count(*)::INT FROM public.bemanningsbane b
        WHERE b.tenant = p_tenant AND b.ukeslutt < current_date
          AND NOT EXISTS (SELECT 1 FROM public.bemanningsmaaling m
                           WHERE m.tenant = b.tenant
                             AND m.prognose_id = b.prognose_id
                             AND m.uke_nr = b.uke_nr)),
      (SELECT count(*)::INT FROM public.bemanningsmaaling m
        WHERE m.tenant = p_tenant AND m.innenfor_intervall),
      (SELECT count(*)::INT FROM public.bemanningsmaaling m
        WHERE m.tenant = p_tenant AND NOT m.innenfor_intervall),
      (SELECT count(*)::INT FROM public.prognosefunn f
        WHERE f.tenant = p_tenant AND f.apen),
      (SELECT EXISTS (SELECT 1 FROM public.prognosekrav k
                       WHERE k.tenant = p_tenant)),
      -- ALLE FIRE GRENSENE (123s lærdom, gjentatt i 128): et skjema
      -- som viser mindre enn det lagrer er en felle — flaten
      -- forhåndsutfyller herfra.
      (SELECT k.horisont_uker FROM public.prognosekrav k
        WHERE k.tenant = p_tenant),
      (SELECT k.grunnlag_uker FROM public.prognosekrav k
        WHERE k.tenant = p_tenant),
      (SELECT k.maalefrist_dogn FROM public.prognosekrav k
        WHERE k.tenant = p_tenant),
      (SELECT k.domsgrunnlag_uker FROM public.prognosekrav k
        WHERE k.tenant = p_tenant),
      (SELECT k.versjon FROM public.prognosekrav k
        WHERE k.tenant = p_tenant),
      (SELECT count(*)::INT FROM public.bemanningsprognose p
        WHERE p.tenant = p_tenant AND p.datakvalitet = 'ukjent')
$$;
REVOKE ALL ON FUNCTION m33_bildet(TEXT) FROM PUBLIC;

CREATE FUNCTION m33_prognoseregister(p_tenant TEXT, p_grense INT)
RETURNS TABLE (prognose_id UUID, laget_dato DATE, horisont_uker INT,
               modellversjon TEXT, baselinje TEXT,
               grunnlag_uker INT, grunnlag_siste_dato DATE,
               grunnlag_antall_uker INT, datakvalitet TEXT,
               datakvalitet_antall INT, gjelder_til DATE,
               laget_av TEXT, antall_maalt INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT p.prognose_id, p.laget_dato, p.horisont_uker,
           p.modellversjon, p.baselinje, p.grunnlag_uker,
           p.grunnlag_siste_dato, p.grunnlag_antall_uker,
           p.datakvalitet, p.datakvalitet_antall, p.gjelder_til,
           p.laget_av,
           (SELECT count(*)::INT FROM public.bemanningsmaaling m
             WHERE m.tenant = p.tenant
               AND m.prognose_id = p.prognose_id)
      FROM public.bemanningsprognose p
     WHERE p.tenant = p_tenant
     ORDER BY p.laget_dato DESC, p.laget_ts DESC
     LIMIT greatest(p_grense, 1)
$$;
REVOKE ALL ON FUNCTION m33_prognoseregister(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m33_modellregister(p_tenant TEXT)
RETURNS TABLE (modell_id UUID, navn TEXT, versjon TEXT, metode TEXT,
               baselinje TEXT, gyldig_fra DATE, gyldig_til DATE,
               gjelder BOOLEAN, antall_prognoser INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT m.modell_id, m.navn, m.versjon, m.metode, m.baselinje,
           m.gyldig_fra, m.gyldig_til,
           public.m33_modell_gyldig(m.gyldig_fra, m.gyldig_til),
           (SELECT count(*)::INT FROM public.bemanningsprognose p
             WHERE p.tenant = m.tenant AND p.modell_id = m.modell_id)
      FROM public.prognosemodell m
     WHERE m.tenant = p_tenant
     ORDER BY m.gyldig_fra DESC, m.versjon
$$;
REVOKE ALL ON FUNCTION m33_modellregister(TEXT) FROM PUBLIC;

CREATE FUNCTION m33_prognosefunn(p_tenant TEXT, p_grense INT)
RETURNS TABLE (funn_id UUID, funntype TEXT, referanse TEXT,
               detaljer TEXT, over_grense BIGINT, apen BOOLEAN,
               forst_sett TIMESTAMPTZ, sist_sett TIMESTAMPTZ,
               lukket_av TEXT, kan_lukkes BOOLEAN)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT f.funn_id, f.funntype, f.referanse, f.detaljer,
           f.over_grense, f.apen, f.forst_sett, f.sist_sett,
           f.lukket_av,
           -- FLATEN SKAL IKKE HUSKE hvilke funn som er sveipens.
           (f.apen AND NOT public.m33_funn_er_sveipens(f.funntype))
      FROM public.prognosefunn f
     WHERE f.tenant = p_tenant
     ORDER BY f.apen DESC, f.sist_sett DESC
     LIMIT greatest(p_grense, 1)
$$;
REVOKE ALL ON FUNCTION m33_prognosefunn(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m33_lukk_funn(p_tenant TEXT, p_funn_id UUID,
                              p_begrunnelse TEXT, p_aktor TEXT)
RETURNS TABLE (funn_id UUID, apen BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.prognosefunn%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm33_lukk_funn');
    -- 125s LÆRDOM, INNEBYGD: en tom aktør ville gitt `false OR NULL`
    -- = NULL i CHECKen, og NULL i en `NOT NULL`-kolonne dreper hele
    -- transaksjonen — i sveipen betyr det at ETT navnløst kall river
    -- med seg alle lukkingene i samme kjøring.
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm33_lukk_funn: en lukking uten et navn på er'
            ' ikke en lukking'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_begrunnelse IS NULL OR btrim(p_begrunnelse) = '' THEN
        RAISE EXCEPTION 'm33_lukk_funn: begrunnelse mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT * INTO v_rad FROM public.prognosefunn
     WHERE tenant = p_tenant AND prognosefunn.funn_id = p_funn_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm33_lukk_funn: funnet finnes ikke'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF public.m33_funn_er_sveipens(v_rad.funntype) THEN
        RAISE EXCEPTION 'm33_lukk_funn: % lukkes ikke av et menneske'
            ' — det lukkes av at tilstanden opphører',
            v_rad.funntype
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT v_rad.apen THEN
        RETURN QUERY SELECT v_rad.funn_id, v_rad.apen;
        RETURN;
    END IF;

    UPDATE public.prognosefunn
       SET apen = false, lukket_ts = now(), lukket_av = p_aktor,
           lukket_begrunnelse = p_begrunnelse
     WHERE tenant = p_tenant AND prognosefunn.funn_id = p_funn_id
    RETURNING * INTO v_rad;

    PERFORM public.m33_evidens(p_tenant, p_funn_id, 'lukk_funn',
        p_aktor, jsonb_build_object('funntype', v_rad.funntype));
    RETURN QUERY SELECT v_rad.funn_id, v_rad.apen;
END $$;
REVOKE ALL ON FUNCTION m33_lukk_funn(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

-- =====================================================================
-- SVEIPEN — DER MODELLEN FÅR SIN DOM.
--
-- Tre funn reises her, og ett av dem er klyngens tyngste.
--
-- 1. `prognose_uten_maaling` — en uke er over, målefristen er passert,
--    og ingen har målt. Dette er funnet som gjør at klyngens dom
--    («en gal prognose ser ut som en riktig prognose helt til
--    horisonten er passert, og da har alle sluttet å se») ikke bare
--    er en setning i en kommentar.
--
-- 2. `slaar_ikke_naiv_baseline` — MODELLEN TAPER FOR «SAMME SOM
--    FORRIGE UKE». Målt over minst `domsgrunnlag_uker` målte uker,
--    per modellversjon: er summen av modellens absoluttavvik større
--    enn eller lik basislinjens, har modellen ikke fortjent
--    autoriteten sin.
--
--    LIKHET TELLER SOM TAP. En modell som er nøyaktig like god som å
--    kopiere forrige uke, har ikke tilført noe — og den koster
--    tillit, fordi den ser ut som analyse.
--
-- 3. `prognose_paa_ukjent_datakvalitet` — prognosen ble laget uten at
--    M-3 noensinne har sett på tenantens data. Dette er det ENESTE av
--    de tre et menneske kan lukke, og det er riktig: det lukkes ved at
--    noen sier «vi vet, vi planlegger likevel».
--
-- SVEIPEN LUKKER OGSÅ. Uten lukkedelen ville et funn stått åpent for
-- alltid etter at tilstanden opphørte — det var 122-124s feil, og
-- 125/126 ryddet den. LUKKINGEN LESER SAMME `kand`-CTE SOM
-- REISINGEN, i samme setning: gjentok jeg predikatet i en egen CTE,
-- ville de to versjonene kunne gli fra hverandre. Det var nøyaktig
-- feilen i 127, der sveipen lukket 2 av 5 funntyper.
--
-- ---------------------------------------------------------------------
-- OG DEN GÅR ÉN TENANT OM GANGEN, MED KONTEKSTEN SATT.
--
-- Første utkast spurte på tvers av tenanter i ett svep. Det ville sett
-- riktig ut og funnet NULL RADER: tabellene har `FORCE ROW LEVEL
-- SECURITY`, og en spørring uten `disponit.tenant` passerer ingen
-- policy. Sveipen ville rapportert null funn, hver natt, med grønn
-- exit-kode.
--
-- DET ER DEN FARLIGSTE FEILFORMEN EN SVEIP KAN HA — den ser ut som en
-- vellykket kjøring. Samme familie som klyngens egen dom: en gal
-- prognose ser ut som en riktig prognose. Her: en blind sveip ser ut
-- som en ren base.
-- =====================================================================
CREATE FUNCTION m33_sveip_prognose(p_maks_tenanter INT)
RETURNS TABLE (tenanter INT, nye BIGINT, oppdaterte BIGINT,
               lukkede BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_sveip CONSTANT TEXT := 'm33_sveip';
    v_t TEXT;
    v_antall INT := 0;
    v_nye BIGINT := 0;
    v_oppdaterte BIGINT := 0;
    v_lukket BIGINT := 0;
    v_n BIGINT; v_n2 BIGINT; v_n3 BIGINT;
BEGIN
    -- ÉN TENANT OM GANGEN, MED KONTEKSTEN SATT. Tabellene har `FORCE
    -- ROW LEVEL SECURITY`, så en sveip som spurte på tvers uten
    -- kontekst ville sett NULL RADER — og rapportert null funn med
    -- god samvittighet. Det er den farligste feilformen en sveip kan
    -- ha: den ser ut som en grønn kjøring.
    FOR v_t IN
        SELECT k.tenant FROM public.prognosekrav k
         ORDER BY k.tenant LIMIT greatest(p_maks_tenanter, 1)
    LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        v_antall := v_antall + 1;

        -- -----------------------------------------------------------
        -- 1. `prognose_uten_maaling` — REISING OG LUKKING I SAMME
        --    SETNING, over samme `kand`-CTE.
        --
        --    127s FEIL, IKKE GJENTATT: der sto lukkingen i en egen CTE
        --    som gjentok predikatet, og sveipen lukket 2 av 5
        --    funntyper fordi de to formuleringene gled fra hverandre.
        --    Her er «uken er umålt» definert ÉN gang, og lukkingen er
        --    «finnes ikke i kand».
        -- -----------------------------------------------------------
        WITH kand AS (
            SELECT b.prognose_id, b.uke_nr, b.ukeslutt,
                   (current_date - b.ukeslutt) - k.maalefrist_dogn
                       AS dogn_over,
                   b.prognose_id::text || ':' || b.uke_nr AS ref
              FROM public.bemanningsbane b
              JOIN public.prognosekrav k ON k.tenant = b.tenant
              LEFT JOIN public.bemanningsmaaling m
                     ON m.tenant = b.tenant
                    AND m.prognose_id = b.prognose_id
                    AND m.uke_nr = b.uke_nr
             WHERE b.tenant = v_t
               AND m.uke_nr IS NULL
               AND b.ukeslutt < current_date
               AND current_date - b.ukeslutt > k.maalefrist_dogn),
        skrevet AS (
            INSERT INTO public.prognosefunn
                (tenant, funn_id, funntype, referanse, detaljer,
                 over_grense)
            SELECT v_t, gen_random_uuid(), 'prognose_uten_maaling',
                   c.ref,
                   format('uke %s sluttet %s og er %s døgn over'
                          ' målefristen', c.uke_nr, c.ukeslutt,
                          c.dogn_over),
                   c.dogn_over
              FROM kand c
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
            DO UPDATE SET sist_sett = now(),
                          over_grense = EXCLUDED.over_grense,
                          detaljer = EXCLUDED.detaljer
            RETURNING (xmax = 0) AS var_ny),
        lukket AS (
            UPDATE public.prognosefunn f
               SET apen = false, lukket_ts = now(),
                   lukket_av = v_sveip,
                   lukket_begrunnelse = 'uken er målt'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'prognose_uten_maaling'
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

        -- -----------------------------------------------------------
        -- 2. MODELLDOMMEN: `slaar_ikke_naiv_baseline`.
        --
        --    Målt per MODELLVERSJON, over minst `domsgrunnlag_uker`
        --    målte uker. LIKHET TELLER SOM TAP (`>=`): en modell som
        --    er nøyaktig like god som å kopiere forrige uke, har ikke
        --    tilført noe — og den koster tillit, fordi den ser ut som
        --    analyse.
        --
        --    Lukkingen er «finnes ikke i kand» igjen, og `kand` er
        --    dommen selv. En modell som blir bedre, lukker sitt eget
        --    funn; ingen kan lukke det for den.
        -- -----------------------------------------------------------
        WITH dom AS (
            SELECT p.modellversjon,
                   count(*) AS uker,
                   sum(m.avvik_minutter) AS modellavvik,
                   sum(m.baseline_avvik_minutter) AS baselineavvik,
                   max(k.domsgrunnlag_uker) AS domsgrunnlag
              FROM public.bemanningsmaaling m
              JOIN public.bemanningsprognose p
                ON p.tenant = m.tenant
               AND p.prognose_id = m.prognose_id
              JOIN public.prognosekrav k ON k.tenant = p.tenant
             WHERE m.tenant = v_t
             GROUP BY p.modellversjon),
        kand AS (
            SELECT d.* FROM dom d
             WHERE d.uker >= d.domsgrunnlag
               AND d.modellavvik >= d.baselineavvik),
        skrevet AS (
            INSERT INTO public.prognosefunn
                (tenant, funn_id, funntype, referanse, detaljer,
                 over_grense)
            SELECT v_t, gen_random_uuid(),
                   'slaar_ikke_naiv_baseline', c.modellversjon,
                   format('modellversjon %s: samlet avvik %s minutter'
                          ' over %s målte uker, mot basislinjens %s —'
                          ' modellen har ikke fortjent autoriteten'
                          ' sin', c.modellversjon, c.modellavvik,
                          c.uker, c.baselineavvik),
                   c.modellavvik - c.baselineavvik
              FROM kand c
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
            DO UPDATE SET sist_sett = now(),
                          over_grense = EXCLUDED.over_grense,
                          detaljer = EXCLUDED.detaljer
            RETURNING (xmax = 0) AS var_ny),
        lukket AS (
            UPDATE public.prognosefunn f
               SET apen = false, lukket_ts = now(),
                   lukket_av = v_sveip,
                   lukket_begrunnelse = 'modellen slår basislinjen'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'slaar_ikke_naiv_baseline'
               AND NOT EXISTS (SELECT 1 FROM kand c
                                WHERE c.modellversjon = f.referanse)
            RETURNING 1)
        SELECT (SELECT count(*) FILTER (WHERE var_ny) FROM skrevet),
               (SELECT count(*) FILTER (WHERE NOT var_ny) FROM skrevet),
               (SELECT count(*) FROM lukket)
          INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- -----------------------------------------------------------
        -- 3. `prognose_paa_ukjent_datakvalitet`.
        --
        --    REISES PÅ PROGNOSEN, IKKE PÅ TENANTEN: det er den
        --    enkelte prognosen som ble laget i blinde, og en tenant
        --    som senere blir profilert skal ikke få historien
        --    omskrevet.
        --
        --    DERFOR LUKKER SVEIPEN DET HELLER ALDRI. `datakvalitet`
        --    står på en append-only rad og kan ikke endre seg, så
        --    «tilstanden opphørte» kan ikke inntreffe. Funnet lukkes
        --    av et MENNESKE som sier «vi vet, vi planlegger likevel»
        --    — og `m33_funn_er_sveipens` slipper det gjennom nettopp
        --    derfor.
        -- -----------------------------------------------------------
        WITH skrevet AS (
            INSERT INTO public.prognosefunn
                (tenant, funn_id, funntype, referanse, detaljer)
            SELECT v_t, gen_random_uuid(),
                   'prognose_paa_ukjent_datakvalitet',
                   p.prognose_id::text,
                   format('prognosen fra %s ble laget uten at M-3 har'
                          ' profilert tenantens data — «ingen funn»'
                          ' og «ingen har sett etter» er ikke samme'
                          ' tilstand', p.laget_dato)
              FROM public.bemanningsprognose p
             WHERE p.tenant = v_t AND p.datakvalitet = 'ukjent'
               -- ET LUKKET FUNN SKAL IKKE GJENÅPNES (CodeRabbit).
               --
               -- Delindeksen `prognosefunn_ett_apent` dekker bare de
               -- ÅPNE radene, så etter at et menneske har lukket
               -- funnet treffer `ON CONFLICT` ingenting — og
               -- INSERTen ville laget en NY åpen rad. Hver natt.
               --
               -- For de to andre funntypene er det RIKTIG: der
               -- betyr en gjenreising at tilstanden faktisk kom
               -- tilbake. Her kan den ikke det: `datakvalitet` står
               -- på en append-only rad og endrer seg aldri. Uten
               -- dette leddet ville «vi vet, vi planlegger likevel»
               -- vært en beslutning som ikke overlevde natten —
               -- nøyaktig 125/126s feilform.
               AND NOT EXISTS (
                   SELECT 1 FROM public.prognosefunn f
                    WHERE f.tenant = p.tenant
                      AND f.funntype
                          = 'prognose_paa_ukjent_datakvalitet'
                      AND f.referanse = p.prognose_id::text
                      AND NOT f.apen)
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
            DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS var_ny)
        SELECT count(*) FILTER (WHERE var_ny),
               count(*) FILTER (WHERE NOT var_ny)
          INTO v_n, v_n2 FROM skrevet;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
    END LOOP;

    PERFORM set_config('disponit.tenant', '', true);
    RETURN QUERY SELECT v_antall, v_nye, v_oppdaterte, v_lukket;
END $$;
REVOKE ALL ON FUNCTION m33_sveip_prognose(INT) FROM PUBLIC;

RESET ROLE;

-- =====================================================================
-- RETTIGHETENE (SP-7).
--
-- Kjøretiden får EXECUTE på dørene og INGEN tabellrettigheter.
-- Modulrollen får SELECT på det den leser, og ingenting mer: den kan
-- ikke skrive en eneste rad i M-39s eller M-3s tabeller.
-- =====================================================================
GRANT SELECT ON public.timeregistrering TO disponit_prognose_eier;

-- M-3s TABELLER EIES AV `disponit_kvalitet_eier`, IKKE AV MIGRATOR.
-- Migrator kan ikke gi bort rettigheter på noe den ikke eier, så
-- granten avgis av eieren selv — samme vei som 128 gikk for M-37s
-- `krev_tenantkontekst`. Første kjøring mot riggen fant dette;
-- lesing av filen ville ikke ha gjort det.
--
-- LESERETT ER IKKE INNSYN: `kvalitetsfunn` og `kvalitetsprofil` har
-- `FORCE ROW LEVEL SECURITY`, så `m33_datakvalitet` ser bare radene
-- til tenanten den er kalt for. En `SELECT` uten tenantkontekst gir
-- null rader, ikke hele huset.
SET LOCAL ROLE disponit_kvalitet_eier;
GRANT SELECT ON public.kvalitetsfunn TO disponit_prognose_eier;
GRANT SELECT ON public.kvalitetsprofil TO disponit_prognose_eier;
RESET ROLE;

-- DØRENE EIES AV MODULROLLEN, SÅ DET ER MODULROLLEN SOM GIR DEM BORT.
-- Migrator kan ikke `GRANT EXECUTE` på en funksjon den ikke eier —
-- 128s form (linje 2015), og riggen minnet meg på hvorfor.
SET LOCAL ROLE disponit_prognose_eier;

GRANT EXECUTE ON FUNCTION m33_sett_krav(TEXT, INT, INT, INT, INT,
                                        TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m33_registrer_modell(TEXT, UUID, TEXT, TEXT,
    TEXT, TEXT, DATE, DATE, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m33_avvikle_modell(TEXT, UUID, DATE, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m33_lag_prognose(TEXT, UUID, UUID, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m33_registrer_maaling(TEXT, UUID, INT,
    BIGINT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m33_banen(TEXT, UUID) TO disponit;
GRANT EXECUTE ON FUNCTION m33_bildet(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m33_prognoseregister(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m33_modellregister(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m33_prognosefunn(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m33_lukk_funn(TEXT, UUID, TEXT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m33_datakvalitet(TEXT) TO disponit;

-- SVEIPEROLLEN. Den får ÉN dør, og den er den eneste som skriver funn
-- uten et menneske i den andre enden.
GRANT EXECUTE ON FUNCTION m33_sveip_prognose(INT)
    TO disponit_prognosesveip;

RESET ROLE;
