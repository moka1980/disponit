-- 095: M-9 kunnskaps- og ordlisteagent v1 — BEGREPSREGISTERET MED
-- KILDEKRAV, og husets FØRSTE fritekstsøk.
--
-- V1-DOMMEN (manifestets hodekommentar, bokstavelig): katalogteksten
-- lover indeksering av intranett og filarkiv og svar med presise
-- kildepekere — altså en RAG-vei over en vektordatabase modulen selv
-- oppgir som forutsetning. Verken søkeindeksen over eksterne kilder
-- eller vektorbasen finnes. Det som ER ekte i dag, og som resten
-- uansett hviler på, er ordlisten: hvert begrep navngir en EIER, en
-- KILDE og en GYLDIGHETSDATO, og det finnes et fritekstsøk over den.
-- Denne migrasjonen bygger nøyaktig det, og ingenting den ikke kan
-- holde.
--
-- «Svar uten tilstrekkelig kildegrunnlag avvises» er katalogens eget
-- krav. Her er det en NOT NULL med en ikke-tom-CHECK, ikke en policy:
-- et begrep uten kilde er UREPRESENTERBART, målt med direkte DML og
-- ikke bare gjennom døren.
--
-- ============================================================
-- FALLGRUVEN, OG HVORFOR HELE MODULEN ER SKREVET RUNDT DEN
-- ============================================================
-- `to_tsvector(x)` og `to_tsquery(x)` med ETT argument leser
-- `default_text_search_config` fra SESJONEN. En indeks bygget under én
-- konfigurasjon slutter STILLE å treffe spørringer kjørt under en
-- annen — og en søkeindeks som ikke treffer er verre enn ingen, fordi
-- den ser ut til å virke. Riggen denne migrasjonen ble målt i har
-- `default_text_search_config = pg_catalog.english`; en norsk ordliste
-- indeksert med den ville stemmet «avtaler» til «avtaler» og aldri
-- truffet «avtale».
--
-- Derfor: ALLE tekstsøkkall her har EKSPLISITT regconfig —
-- `to_tsvector('norwegian', …)`, `websearch_to_tsquery('norwegian', …)`.
-- To-arguments-formen er i tillegg IMMUTABLE (én-arguments er STABLE),
-- og det er nettopp derfor Postgres selv nekter den generte kolonnen og
-- uttrykksindeksen under. At basen sier nei er en GOD ting — men den
-- sier bare nei til indeksen, ikke til en spørring, så porten
-- `sok_uten_eksplisitt_regconfig` i `test_m9_kunnskap.py` måler
-- modulens SQL og Python statisk i tillegg.
--
-- ============================================================
-- YTELSEN ER MÅLT, IKKE ANTATT — OG MÅLINGEN FELTE GIN-INDEKSEN
-- ============================================================
-- 086 lærte huset at et indekstall skal MÅLES (47,6 → 0,33 ms), ikke
-- antas. Denne migrasjonen skulle etter planen bære en GIN-indeks på
-- søkekolonnen. Den gjør den ikke, og grunnen er målt:
--
-- **`ts_match_vq` — operatoren `tsvector @@ tsquery` — er IKKE
-- LEAKPROOF** (`SELECT proleakproof FROM pg_catalog.pg_proc WHERE
-- proname = 'ts_match_vq'` gir `f`). En ikke-leakproof kval kan ikke
-- evalueres FØR en RLS-sikkerhetskval, og en indeksbetingelse er per
-- definisjon det som evalueres først. På en tabell med ENABLE + FORCE
-- ROW LEVEL SECURITY blir `sok @@ q` derfor ALLTID et `Filter:` og
-- ALDRI et `Index Cond:` — uansett hvor mange GIN-indekser som står
-- der, og også med `enable_seqscan = off`.
--
-- Målt mot riggen (PostgreSQL 18.6), 50 006 begreper i én tenant,
-- `EXPLAIN (ANALYZE)`, median av 15 kjøringer, samme spørring som
-- `m9_sok` kjører:
--
--   UTEN GIN, RLS på  →  20.956 ms   Filter: (sok @@ …), 50 000 forkastet
--   MED  GIN, RLS på  →  20.362 ms   NØYAKTIG SAMME PLAN — indeksen røres aldri
--   MED  GIN, RLS AV  →   0.264 ms   Index Cond: (sok @@ …)
--
-- Indeksen VIRKER (79×) — den er bare utilgjengelig så lenge RLS står,
-- og RLS står. Da er den ikke gratis, den er verre enn ingenting: den
-- koster skriving og disk, og den SER UT SOM om søket er indeksert.
-- «En søkeindeks som ikke treffer er verre enn ingen» er modulens egen
-- dom; den gjelder også når det er planleggeren og ikke regconfigen som
-- gjør den blind. GIN-indeksen er derfor MÅLT OG FJERNET, og porten
-- `test_gin_paa_tsvector_er_uraakelig_under_rls` i `test_m9_kunnskap.py`
-- holder funnet fast så ingen legger den inn igjen uten å måle.
--
-- INDEKSEN SOM VIRKER, og som står, er sveipens:
-- `begrep_gjeldende_gyldig_til`. Den kvalen er `date <= date`, og
-- `date_le` ER leakproof (`proleakproof = t`) — derfor kan den bli en
-- indeksbetingelse under RLS. Samme rigg, samme metode, sveipens
-- kandidatutvalg over 50 006 begreper:
--
--   UTEN `begrep_gjeldende_gyldig_til`  →  40.976 ms  (Parallel Seq Scan)
--   MED  `begrep_gjeldende_gyldig_til`  →   0.013 ms  (Index Cond)
--
-- Altså 3 150×. Begge tallpar står også i commit-teksten.
--
-- HVA DETTE BETYR FOR SØKET: kostnaden er lineær i tenantens egen
-- ordliste — 0.67 ms ved 3 006 begreper, 20.96 ms ved 50 006. For en
-- bedriftsordliste er det rikelig. Blir en tenants ordliste så stor at
-- 21 ms ikke holder, er valget EKSPLISITT og skal tas av et menneske:
-- enten faller tenantgrensen i søkeveien fra RLS til et predikat
-- (raskt, og ett refaktoreringsuhell fra en kryss-tenant-lekkasje),
-- eller søket flytter ut av denne tabellen. v1 velger RLS.
--
-- ============================================================
-- HUSFORMENE
-- ============================================================
--   * tenant TEXT + RLS ENABLE+FORCE + `tenant_isolasjon` på begge
--     tabeller (057/082/089-formen); ingen BYPASSRLS noe sted;
--   * ALL skriving og all lesing gjennom SECURITY DEFINER-dører eid av
--     `disponit_kunnskap_eier`, med `krev_tenantkontekst` FØRST (SP-1);
--   * radvakter som gjelder ENHVER rolle, også eieren (011/053/056);
--   * runtime har INGEN tabellrettigheter her — EXECUTE på de to
--     LESEdørene speiles i `migrer.py`, aldri i migrasjonen (057-læren);
--   * INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.
--
-- KRYSS-TENANT ER ÉN EKSPLISITT POLICY, OG DEN ER SNEVRERE ENN
-- 057/088-FORMEN. Utløpssveipen må se alle tenanters begreper for å
-- finne tenantene i det hele tatt. 057/088 løser det med
-- `USING (CURRENT_USER = '<eier>')` — men her eier SAMME rolle også
-- søkedøren, og en slik policy ville gjort `m9_sok` kryss-tenant med
-- ett feiltrinn i et WHERE-ledd. Policyen under er derfor betinget av
-- at det IKKE er satt noen tenantkontekst, og gjelder KUN `FOR SELECT`:
-- dørene kan ikke nå den, fordi `krev_tenantkontekst` krever en
-- ikke-tom kontekst FØR første setning. Tenantisolasjonen i
-- `m9_sok` er dermed RLS sin, ikke et predikat noen kan glemme.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_kunnskap_eier') THEN
        RAISE EXCEPTION 'rollen disponit_kunnskap_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

-- FERSK-SERVER-FUNNET (samme klasse som oppsett-postgresql.sh sin egen
-- kommentar): PostgreSQL >= 15 gir ingen CREATE på `public` til andre
-- enn skjemaeieren. Blokken under oppretter funksjoner UNDER
-- `SET LOCAL ROLE disponit_kunnskap_eier`, og uten CREATE dør
-- migrasjonen med «permission denied for schema public».
--
-- Grantet står HER og ikke i `migrer.py`, fordi rettighetsblokkene der
-- kjøres ETTER migrasjonene — altså for sent til å redde denne. Det er
-- migrasjonens egen forutsetning, og fundamentets regel er nettopp at
-- modulen GIR sin rolle rettigheter (den oppretter den ikke).
--
-- MERK: dette dekker CREATE-halvdelen. MEDLEMSKAPET migrator trenger for
-- å kunne `SET LOCAL ROLE` i det hele tatt
-- (`GRANT disponit_kunnskap_eier TO disponit_migrator WITH INHERIT
-- FALSE`) kan IKKE gis herfra — det krever ADMIN OPTION på rollen, som
-- bare superbrukeren har. Det hører til rolleoppsettet (ci.yml og
-- oppsett-postgresql.sh), og gjelder alle fem eierne i klyngen.
GRANT USAGE, CREATE ON SCHEMA public TO disponit_kunnskap_eier;

-- ------------------------------------------------------------
-- 1. begrep — ordlisten, som TIDSLINJE og ikke som tilstand.
--
-- Et publisert begrep endres ALDRI på plass. En endring skriver en NY
-- rad med `versjonsnr + 1` og flytter `gjeldende`. Det gjør ordlisten
-- til en tidslinje: en ordliste uten historikk kan ikke svare på «hva
-- sa vi den gangen», og nettopp det spørsmålet er hele grunnen til at
-- en bedrift fører ordliste.
--
-- `gjeldende` er en PARTIAL UNIQUE INDEX, ikke bare en boolean:
-- `UNIQUE (tenant, term) WHERE gjeldende` gjør «ett gjeldende begrep
-- per term» URERPRESENTERBART framfor usannsynlig. To gjeldende
-- versjoner av samme term er ikke en rar tilstand — det er to ulike
-- svar på samme spørsmål, og da er ordlisten verdiløs.
--
-- SØKEKOLONNEN er GENERATED ALWAYS ... STORED med EKSPLISITT
-- regconfig, og `setweight` gir termen vekt A og forklaringen vekt B —
-- et treff i selve termen skal rangeres over et treff i en forklaring
-- som bare nevner ordet. Både `to_tsvector(regconfig, text)`,
-- `setweight` og tsvector-konkateneringen er IMMUTABLE, som en generert
-- kolonne krever. Kolonnen består selv om GIN-indeksen ikke gjør det
-- (se hodekommentaren): den er det som gjør SØKET riktig — norsk
-- stemming, ett stabilt oppslag, samme regconfig ved skriving og
-- lesing. Indeksen ville bare gjort det raskere.
-- IKKE-TOM-FORMEN ER `~ '[^[:space:]]'`, IKKE husets vanlige
-- lengde-etter-trimming. Det er en RETTELSE, og den ble funnet ved å
-- MÅLE: ett-arguments-trimmingen fjerner BARE mellomrom, så en verdi som
-- er én tabulator passerer den. En kilde som er én tabulator er nøyaktig
-- den «tilstrekkelige kildegrunnlag»-påstanden katalogen sier skal
-- avvises, og skjemaet skal ikke slippe den gjennom fordi blanktegnet
-- var av en annen klasse enn det vanlige. Formen her krever ETT tegn som
-- ikke er blankt — uansett hva resten er.
CREATE TABLE begrep (
    tenant TEXT NOT NULL,
    begrep_id UUID NOT NULL,
    term TEXT NOT NULL CHECK (term ~ '[^[:space:]]'),
    forklaring TEXT NOT NULL CHECK (forklaring ~ '[^[:space:]]'),
    -- EIEREN er et menneske eller en rolle som svarer for begrepet. Et
    -- begrep ingen eier er et begrep ingen retter.
    eier TEXT NOT NULL CHECK (eier ~ '[^[:space:]]'),
    -- KILDEKRAVET, som DATAMODELL og ikke som policy.
    kilde TEXT NOT NULL CHECK (kilde ~ '[^[:space:]]'),
    gyldig_til DATE NOT NULL,
    versjonsnr INT NOT NULL CHECK (versjonsnr >= 1),
    gjeldende BOOLEAN NOT NULL,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    sok tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('norwegian', coalesce(term, '')), 'A')
        || setweight(to_tsvector('norwegian', coalesce(forklaring, '')), 'B')
    ) STORED,
    CONSTRAINT begrep_pk PRIMARY KEY (tenant, begrep_id),
    CONSTRAINT begrep_en_per_versjon UNIQUE (tenant, term, versjonsnr)
);

-- «Ett gjeldende begrep per term» som en UMULIGHET, ikke en regel.
-- Dette er en CONSTRAINT, ikke en ytelsesindeks: listeveien er målt til
-- 0.049 ms uten den og 0.043 ms med den (LIMIT 50 stopper uansett
-- tidlig, og PK-en gir tenantpruningen). Den står for det den GJØR
-- umulig, ikke for det den gjør raskt.
CREATE UNIQUE INDEX begrep_ett_gjeldende
    ON begrep (tenant, term) WHERE gjeldende;

-- SVEIPENS INDEKS — den som faktisk er nåbar under RLS, fordi
-- `date_le` er leakproof. Målt: 40.976 → 0.013 ms over 50 006 begreper
-- (se hodekommentaren). Den bærer også `SELECT DISTINCT tenant WHERE
-- gjeldende` i sveipens første steg.
CREATE INDEX begrep_gjeldende_gyldig_til
    ON begrep (tenant, gyldig_til) WHERE gjeldende;

-- INGEN `CREATE INDEX ... USING gin (sok)`. Det er ikke en
-- forglemmelse — det er et målt vedtak, og hele begrunnelsen med tall
-- står i hodekommentaren over. Legger du den inn igjen: MÅL FØRST.

-- Radvakten (057/089-formen: samme port som døren, med vilje duplisert
-- — vakten gjelder ENHVER rolle, også eieren). Den er invarianten
-- `begrep_endret_uten_ny_versjon`: alt INNHOLD er frosset etter
-- fødselen, og eneste lovlige UPDATE er at `gjeldende` faller fra sann
-- til usann. Også `eier` og `gyldig_til` er frosset — strengere enn
-- planen krevde, og med vilje: «hva sa vi den gangen» inkluderer hvem
-- som svarte for det og hvor lenge det gjaldt. En fornyet
-- gyldighetsdato er en ny versjon, ikke en stille forlengelse av en
-- gammel sannhet.
CREATE FUNCTION m9_begrep_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NOT NEW.gjeldende THEN
            RAISE EXCEPTION 'begrep: en ny versjon fødes GJELDENDE —'
                ' en historisk rad skrives aldri i etterkant'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'begrep: % avvist — et publisert begrep'
            ' erstattes av en ny versjon, det slettes aldri', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.begrep_id IS DISTINCT FROM OLD.begrep_id
       OR NEW.term IS DISTINCT FROM OLD.term
       OR NEW.forklaring IS DISTINCT FROM OLD.forklaring
       OR NEW.kilde IS DISTINCT FROM OLD.kilde
       OR NEW.eier IS DISTINCT FROM OLD.eier
       OR NEW.gyldig_til IS DISTINCT FROM OLD.gyldig_til
       OR NEW.versjonsnr IS DISTINCT FROM OLD.versjonsnr
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av THEN
        RAISE EXCEPTION 'begrep: innholdet er frosset etter publisering'
            ' — en endring er en NY versjon (m9_ny_begrepsversjon), så'
            ' ordlisten kan svare på hva som gjaldt den gangen'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.gjeldende AND NOT OLD.gjeldende THEN
        RAISE EXCEPTION 'begrep: en avløst versjon blir aldri gjeldende'
            ' igjen — å gå tilbake er også en ny versjon'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m9_begrep_vakt() FROM PUBLIC;
CREATE TRIGGER m9_begrep_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON begrep
    FOR EACH ROW EXECUTE FUNCTION m9_begrep_vakt();
CREATE TRIGGER m9_begrep_ingen_truncate
    BEFORE TRUNCATE ON begrep
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

ALTER TABLE begrep ENABLE ROW LEVEL SECURITY;
ALTER TABLE begrep FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON begrep
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- Kryss-tenant-lesingen sveipen trenger, og INGENTING mer: kun SELECT,
-- kun for eierrollen, og kun når det ikke er satt noen tenantkontekst.
-- `krev_tenantkontekst` krever en ikke-tom kontekst FØR første setning
-- i hver dør, så ingen av dørene kan noensinne stå i dette vinduet.
CREATE POLICY m9_sveip_leser_uten_tenantkontekst ON begrep
    FOR SELECT TO disponit_kunnskap_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

GRANT SELECT, INSERT, UPDATE ON begrep TO disponit_kunnskap_eier;

-- ------------------------------------------------------------
-- 2. begrepsfunn — utløpte og snart utløpte begreper.
--
-- Et begrep forbi `gyldig_til` er et FUNN, ikke en stille gammel
-- sannhet folk fortsetter å lese. Funntypesettet er LUKKET.
--
-- ETT FUNN PER (begrep_id, funntype) HOLDES ÅPENT og oppdateres med
-- `sist_sett_sveip` — funnlisten vokser ikke med kadensen. En daglig
-- sveip over et begrep som har vært utløpt i et år skal gi ETT funn,
-- ikke 365. Funn som ikke lenger gjelder LUKKES (en ny versjon flyttet
-- `gjeldende`, eller datoen ble fornyet), de slettes aldri: at noe var
-- utløpt en periode er også historikk.
CREATE TABLE begrepsfunn (
    tenant TEXT NOT NULL,
    begrep_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CHECK (funntype IN ('utlopt', 'utloper_snart')),
    -- Termen KOPIERES inn i funnet. Funnet skal kunne leses uten å
    -- måtte slå opp begrepsraden det peker på — og en avløst rad kan
    -- være en annen tekst enn den funnet ble reist på.
    term TEXT NOT NULL,
    gyldig_til DATE NOT NULL,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT begrepsfunn_pk PRIMARY KEY (tenant, begrep_id, funntype),
    CONSTRAINT begrepsfunn_begrep_fk FOREIGN KEY (tenant, begrep_id)
        REFERENCES begrep (tenant, begrep_id),
    -- Et åpent funn har ingen lukketid, et lukket har alltid en. Den
    -- halve lukkingen er urepresenterbar (089-formen).
    CONSTRAINT begrepsfunn_lukking_komplett
        CHECK (apen = (lukket_ts IS NULL))
);

CREATE INDEX begrepsfunn_apne ON begrepsfunn (tenant, funntype)
    WHERE apen;

-- Radvakten: identiteten er frosset, DELETE/TRUNCATE avvises — også
-- for eieren. Sveipen får bare flytte ferskhets- og livsløpsfeltene.
CREATE FUNCTION m9_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'begrepsfunn: % avvist — et funn lukkes, det'
            ' slettes aldri; at noe VAR utløpt er også historikk', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.begrep_id IS DISTINCT FROM OLD.begrep_id
       OR NEW.funntype IS DISTINCT FROM OLD.funntype
       OR NEW.forst_sett IS DISTINCT FROM OLD.forst_sett THEN
        RAISE EXCEPTION 'begrepsfunn: identiteten (tenant, begrep,'
            ' funntype) og førstegangsobservasjonen er frosset — et'
            ' annet funn er en annen rad'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.sist_sett_sveip < OLD.sist_sett_sveip THEN
        RAISE EXCEPTION 'begrepsfunn: sist_sett_sveip går aldri bakover'
            ' — en ferskhet som kan settes tilbake er ingen ferskhet'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m9_funn_vakt() FROM PUBLIC;
CREATE TRIGGER m9_funn_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON begrepsfunn
    FOR EACH ROW EXECUTE FUNCTION m9_funn_vakt();
CREATE TRIGGER m9_funn_ingen_truncate
    BEFORE TRUNCATE ON begrepsfunn
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

ALTER TABLE begrepsfunn ENABLE ROW LEVEL SECURITY;
ALTER TABLE begrepsfunn FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON begrepsfunn
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- INGEN kryss-tenant-policy her, og det er ikke en forglemmelse:
-- sveipen finner tenantene i `begrep` (uten kontekst) og gjør ALT
-- funnarbeidet med RADENS tenant satt i konteksten. Skrivingen er
-- dermed tenantbundet av RLS, også for sveipen selv.
GRANT SELECT, INSERT, UPDATE ON begrepsfunn TO disponit_kunnskap_eier;

-- ============================================================
-- 3. Dørene — eid av `disponit_kunnskap_eier` (056/057/089-formen:
--    SECURITY DEFINER bak `krev_tenantkontekst`-porten). Hele blokken
--    kjører under SET LOCAL ROLE, og ALLE rettighetsendringer på
--    eier-eide funksjoner står INNE i blokken (#140-læren:
--    PUBLIC-EXECUTE-klassen).
-- ============================================================
-- SP-1-porten selv er REVOKEd fra PUBLIC i 038 og eies av
-- `disponit_m37_claimer`. Dørene under er SECURITY DEFINER og kjører
-- altså som `disponit_kunnskap_eier` — uten dette grantet feiler hvert
-- eneste dørkall med «permission denied for function
-- krev_tenantkontekst», og SP-1 ville vært en port som stengte alt.
-- Grantet gis AV eieren av porten (039/074-formen); migrator er medlem
-- av begge roller.
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_kunnskap_eier;
RESET ROLE;

SET LOCAL ROLE disponit_kunnskap_eier;

-- Førstegangsregistrering. SP-2-materialitet på `p_begrep_id`
-- (056-formen): gjenspill med identisk innhold er et STILLE JA, samme
-- id med annet innhold er en materiell konflikt. NULL = direktekall,
-- fersk id.
CREATE FUNCTION m9_registrer_begrep(
    p_tenant TEXT, p_term TEXT, p_forklaring TEXT, p_eier TEXT,
    p_kilde TEXT, p_gyldig_til DATE, p_aktor TEXT,
    p_begrep_id UUID DEFAULT NULL)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID; v_rad public.begrep;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm9_registrer_begrep');
    -- Kildekravet ligger i CHECKen på tabellen; her står bare den
    -- SETNINGEN kalleren skal få i stedet for en constraint-melding.
    IF p_kilde IS NULL OR p_kilde !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm9_registrer_begrep: begrepet «%» har ingen'
            ' kilde — svar uten tilstrekkelig kildegrunnlag avvises',
            coalesce(p_term, '<null>')
            USING ERRCODE = 'check_violation';
    END IF;
    v_id := coalesce(p_begrep_id, gen_random_uuid());
    SELECT b.* INTO v_rad FROM public.begrep b
     WHERE b.tenant = p_tenant AND b.begrep_id = v_id;
    IF FOUND THEN
        IF v_rad.term = p_term AND v_rad.forklaring = p_forklaring
           AND v_rad.eier = p_eier AND v_rad.kilde = p_kilde
           AND v_rad.gyldig_til = p_gyldig_til THEN
            RETURN v_id;              -- gjenspill: stille ja
        END IF;
        RAISE EXCEPTION 'm9_registrer_begrep: begrep_id % finnes alt med'
            ' annet innhold — materiell idempotenskonflikt', v_id
            USING ERRCODE = 'unique_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM public.begrep b
                WHERE b.tenant = p_tenant AND b.term = p_term
                  AND b.gjeldende) THEN
        RAISE EXCEPTION 'm9_registrer_begrep: «%» er alt registrert —'
            ' en endring er en NY VERSJON (m9_ny_begrepsversjon), aldri'
            ' en ny førstegangsregistrering', p_term
            USING ERRCODE = 'unique_violation';
    END IF;
    INSERT INTO public.begrep (tenant, begrep_id, term, forklaring, eier,
        kilde, gyldig_til, versjonsnr, gjeldende, opprettet_av)
    VALUES (p_tenant, v_id, p_term, p_forklaring, p_eier, p_kilde,
            p_gyldig_til, 1, true, p_aktor);
    RETURN v_id;
END $$;

-- Ny versjon. Den gamle raden BESTÅR (uendret, `gjeldende` faller);
-- den nye fødes med `versjonsnr + 1`. Rekkefølgen er ikke valgfri:
-- den partielle unike indeksen `begrep_ett_gjeldende` er ikke utsatt,
-- så avløsningen må skje FØR innsettingen. `FOR UPDATE` serialiserer
-- to samtidige versjoneringer av samme term — uten den ville den ene
-- fått en unik-violation i stedet for å vente, og kalleren ville sett
-- en tilfeldig feil på en lovlig handling.
CREATE FUNCTION m9_ny_begrepsversjon(
    p_tenant TEXT, p_term TEXT, p_forklaring TEXT, p_eier TEXT,
    p_kilde TEXT, p_gyldig_til DATE, p_aktor TEXT,
    p_begrep_id UUID DEFAULT NULL)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID; v_gammel public.begrep; v_rad public.begrep;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm9_ny_begrepsversjon');
    IF p_kilde IS NULL OR p_kilde !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm9_ny_begrepsversjon: begrepet «%» har ingen'
            ' kilde — svar uten tilstrekkelig kildegrunnlag avvises',
            coalesce(p_term, '<null>')
            USING ERRCODE = 'check_violation';
    END IF;
    v_id := coalesce(p_begrep_id, gen_random_uuid());
    SELECT b.* INTO v_rad FROM public.begrep b
     WHERE b.tenant = p_tenant AND b.begrep_id = v_id;
    IF FOUND THEN
        IF v_rad.term = p_term AND v_rad.forklaring = p_forklaring
           AND v_rad.eier = p_eier AND v_rad.kilde = p_kilde
           AND v_rad.gyldig_til = p_gyldig_til THEN
            RETURN v_id;              -- gjenspill: stille ja
        END IF;
        RAISE EXCEPTION 'm9_ny_begrepsversjon: begrep_id % finnes alt'
            ' med annet innhold — materiell idempotenskonflikt', v_id
            USING ERRCODE = 'unique_violation';
    END IF;
    SELECT b.* INTO v_gammel FROM public.begrep b
     WHERE b.tenant = p_tenant AND b.term = p_term AND b.gjeldende
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm9_ny_begrepsversjon: «%» finnes ikke i'
            ' ordlisten — en ny versjon forutsetter en gammel', p_term
            USING ERRCODE = 'no_data_found';
    END IF;
    UPDATE public.begrep SET gjeldende = false
     WHERE tenant = p_tenant AND begrep_id = v_gammel.begrep_id;
    INSERT INTO public.begrep (tenant, begrep_id, term, forklaring, eier,
        kilde, gyldig_til, versjonsnr, gjeldende, opprettet_av)
    VALUES (p_tenant, v_id, p_term, p_forklaring, p_eier, p_kilde,
            p_gyldig_til, v_gammel.versjonsnr + 1, true, p_aktor);
    RETURN v_id;
END $$;

-- SØKEDØREN. Tom eller blank spørring er LISTINGEN (hele den gjeldende
-- ordlisten, alfabetisk) — én dør, ikke to, fordi «vis meg alt» og «søk
-- etter x» er det samme spørsmålet med ulik avgrensning.
--
-- `websearch_to_tsquery` og ikke `to_tsquery`: spørringen kommer fra et
-- menneske i et søkefelt. `to_tsquery` kaster syntaksfeil på et
-- alminnelig mellomrom; `websearch_to_tsquery` tar imot det folk
-- faktisk skriver og har ingen syntaksfeil å kaste. REGCONFIGEN ER
-- EKSPLISITT begge steder — det er hele fallgruven.
--
-- `utlopt` regnes i BASEN, i samme skann som radene (090/091-formen):
-- flaten skal ikke sammenligne en dato med dagens for å vite om et
-- begrep har gått ut på dato.
--
-- INGEN `tenant`-predikat i WHERE-leddet, og det er med vilje:
-- isolasjonen er RLS sin (`tenant_isolasjon`), og eierrollens eneste
-- kryss-tenant-policy krever fravær av tenantkontekst — som
-- `krev_tenantkontekst` over nettopp har utelukket. Et predikat her
-- ville vært en ANDRE port som ser ut som den første, og den dagen noen
-- refaktorerer WHERE-leddet er det RLS som fortsatt står.
CREATE FUNCTION m9_sok(p_tenant TEXT, p_sporring TEXT, p_grense INT)
RETURNS TABLE(begrep_id UUID, term TEXT, forklaring TEXT, eier TEXT,
              kilde TEXT, gyldig_til DATE, versjonsnr INT,
              utlopt BOOLEAN, rang REAL)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_q tsquery; v_grense INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm9_sok');
    v_grense := greatest(least(coalesce(p_grense, 50), 200), 1);
    IF p_sporring IS NULL OR p_sporring !~ '[^[:space:]]' THEN
        RETURN QUERY
        SELECT b.begrep_id, b.term, b.forklaring, b.eier, b.kilde,
               b.gyldig_til, b.versjonsnr,
               (b.gyldig_til < current_date) AS utlopt,
               0::real AS rang
          FROM public.begrep b
         WHERE b.gjeldende
         ORDER BY b.term
         LIMIT v_grense;
        RETURN;
    END IF;
    v_q := websearch_to_tsquery('norwegian', p_sporring);
    -- En spørring som bare er stoppord gir en TOM tsquery, og en tom
    -- tsquery matcher ingenting. Det er riktig svar — men det er et
    -- ANNET svar enn listingen over, og de to skal ikke smelte sammen.
    RETURN QUERY
    SELECT b.begrep_id, b.term, b.forklaring, b.eier, b.kilde,
           b.gyldig_til, b.versjonsnr,
           (b.gyldig_til < current_date) AS utlopt,
           ts_rank_cd(b.sok, v_q) AS rang
      FROM public.begrep b
     WHERE b.gjeldende AND b.sok @@ v_q
     ORDER BY ts_rank_cd(b.sok, v_q) DESC, b.term
     LIMIT v_grense;
END $$;

-- Funnlesingen. Et funn ingen kan se er ikke et funn — det er en rad.
-- Denne døren er grunnen til at utløpssveipen er synlig i flaten og
-- ikke bare i en JSON-linje i journalen.
CREATE FUNCTION m9_apne_funn(p_tenant TEXT, p_grense INT)
RETURNS TABLE(begrep_id UUID, funntype TEXT, term TEXT, gyldig_til DATE,
              forst_sett TIMESTAMPTZ, sist_sett_sveip TIMESTAMPTZ,
              alder_s BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm9_apne_funn');
    RETURN QUERY
    SELECT f.begrep_id, f.funntype, f.term, f.gyldig_til, f.forst_sett,
           f.sist_sett_sveip,
           EXTRACT(EPOCH FROM (now() - f.forst_sett))::bigint AS alder_s
      FROM public.begrepsfunn f
     WHERE f.apen
     ORDER BY f.funntype, f.gyldig_til, f.term
     LIMIT greatest(least(coalesce(p_grense, 100), 500), 1);
END $$;

-- UTLØPSSVEIPEN. Kryss-tenant, innelukket autoritet (038/057/088-formen
-- i ånd, men strengere i formen — se hodekommentaren): INTET
-- tenantparameter, utvalget ER predikatet, og alt funnarbeid gjøres med
-- RADENS tenant i konteksten.
--
-- Kandidatsettet MATERIALISERES før første `set_config`. En åpen
-- cursor over `begrep` mens tenantkonteksten endres under føttene på
-- den ville vært et RLS-predikat som skifter mening midt i en løkke —
-- riktig svar i test, uforutsigbart under last.
CREATE FUNCTION m9_sveip_utlopte(p_varselvindu_dogn INT DEFAULT 30)
RETURNS TABLE(tenanter INT, nye INT, oppdaterte INT, lukkede INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_n INT;
        v_dag DATE; v_naa TIMESTAMPTZ; v_vindu INT;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm9_sveip_utlopte: sveipen er KRYSS-TENANT og'
            ' kjøres uten tenantkontekst — en kaller som har satt en'
            ' kontekst ber om noe annet enn det denne funksjonen gjør'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_vindu := greatest(least(coalesce(p_varselvindu_dogn, 30), 365), 0);
    v_dag := current_date;
    v_naa := now();
    tenanter := 0; nye := 0; oppdaterte := 0; lukkede := 0;
    SELECT array_agg(DISTINCT b.tenant) INTO v_tenanter
      FROM public.begrep b WHERE b.gjeldende;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        tenanter := tenanter + 1;
        -- 1. Ferskheten på funn som alt finnes. IDEMPOTENSEN BOR HER:
        --    en sveip nummer to på det samme utløpte begrepet flytter
        --    `sist_sett_sveip` og skriver ingen ny rad.
        UPDATE public.begrepsfunn f
           SET sist_sett_sveip = v_naa, apen = true, lukket_ts = NULL,
               gyldig_til = k.gyldig_til, term = k.term
          FROM (SELECT b.begrep_id, b.term, b.gyldig_til,
                       CASE WHEN b.gyldig_til < v_dag THEN 'utlopt'
                            ELSE 'utloper_snart' END AS funntype
                  FROM public.begrep b
                 WHERE b.gjeldende
                   AND b.gyldig_til <= v_dag + v_vindu) k
         WHERE f.begrep_id = k.begrep_id AND f.funntype = k.funntype;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        oppdaterte := oppdaterte + v_n;
        -- 2. De nye.
        INSERT INTO public.begrepsfunn
            (tenant, begrep_id, funntype, term, gyldig_til,
             forst_sett, sist_sett_sveip, apen)
        SELECT v_t, b.begrep_id,
               CASE WHEN b.gyldig_til < v_dag THEN 'utlopt'
                    ELSE 'utloper_snart' END,
               b.term, b.gyldig_til, v_naa, v_naa, true
          FROM public.begrep b
         WHERE b.gjeldende AND b.gyldig_til <= v_dag + v_vindu
            ON CONFLICT (tenant, begrep_id, funntype) DO NOTHING;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        nye := nye + v_n;
        -- 3. Lukkingen. Et funn som ikke lenger gjelder — en ny versjon
        --    flyttet `gjeldende`, eller datoen ble fornyet — lukkes.
        --    Raden består: at noe VAR utløpt er også historikk.
        UPDATE public.begrepsfunn f
           SET apen = false, lukket_ts = v_naa
         WHERE f.apen
           AND NOT EXISTS (
                SELECT 1 FROM public.begrep b
                 WHERE b.begrep_id = f.begrep_id AND b.gjeldende
                   AND b.gyldig_til <= v_dag + v_vindu
                   AND (CASE WHEN b.gyldig_til < v_dag THEN 'utlopt'
                             ELSE 'utloper_snart' END) = f.funntype);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        lukkede := lukkede + v_n;
    END LOOP;
    -- Konteksten legges tilbake der den sto: en sveip skal ikke
    -- etterlate seg en tenant i sesjonen den ikke ble kalt med.
    PERFORM set_config('disponit.tenant', '', true);
    RETURN NEXT;
END $$;

-- ------------------------------------------------------------
-- 4. Rettighetene — INNE i eierblokken (#140-læren: en REVOKE utenfor
--    lot funksjonen stå PUBLIC-kjørbar mellom to setninger).
--
-- SKRIVEDØRENE GIS IKKE TIL NOEN HER. Runtime får kun de to LESEdørene
-- (`migrer.py`), sveiperollen kun sveipen. v1 har INGEN HTTP-mutasjon:
-- ordlisten fylles gjennom dørene av en menneskelig, sporet
-- deploy-handling — M-31s form (086), av samme grunn. Den dagen en
-- skrivevei over HTTP kommer, er DEN en egen registrering i
-- autorisasjonslaget med sitt eget scope og sin egen CSRF-port.
-- ------------------------------------------------------------
REVOKE ALL ON FUNCTION m9_registrer_begrep(
    TEXT, TEXT, TEXT, TEXT, TEXT, DATE, TEXT, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION m9_ny_begrepsversjon(
    TEXT, TEXT, TEXT, TEXT, TEXT, DATE, TEXT, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION m9_sok(TEXT, TEXT, INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION m9_apne_funn(TEXT, INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION m9_sveip_utlopte(INT) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_kunnskapssveip') THEN
        GRANT EXECUTE ON FUNCTION m9_sveip_utlopte(INT)
            TO disponit_kunnskapssveip;
    END IF;
    -- En rettighet som bare slutter å bli gitt er ikke trukket tilbake
    -- (035). Runtime skal ALDRI kunne kjøre sveipen: den er kryss-tenant
    -- og setter selv RLS-konteksten, altså nøyaktig det vinduet
    -- sveiperollen finnes for å nekte forespørselsveien.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        REVOKE ALL ON FUNCTION m9_sveip_utlopte(INT) FROM disponit;
        REVOKE ALL ON FUNCTION m9_registrer_begrep(
            TEXT, TEXT, TEXT, TEXT, TEXT, DATE, TEXT, UUID) FROM disponit;
        REVOKE ALL ON FUNCTION m9_ny_begrepsversjon(
            TEXT, TEXT, TEXT, TEXT, TEXT, DATE, TEXT, UUID) FROM disponit;
    END IF;
END $$;

RESET ROLE;
