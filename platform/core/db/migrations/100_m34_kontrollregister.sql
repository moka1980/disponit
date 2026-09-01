-- 100: M-34 compliance- og sertifiseringsagent v1 — KONTROLLREGISTERET.
-- Fire tenant-skopede tabeller, fem dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- V1-AVGRENSNINGEN, ORDRETT FRA MANIFESTET: katalogteksten lover også
-- automatisk innsamling av evidens og innsending til sertifiseringsorgan.
-- Begge forutsetter connectorer per rammeverk og et mandat per mottaker
-- — ingen av delene finnes, og et compliance-verktøy som sender inn noe
-- på egen hånd er verre enn ingen: DET SKAPER EN PÅSTAND INGEN HAR LEST.
-- Ingenting her later som noe annet: det finnes ingen utgående vei i
-- denne migrasjonen, ingen mottakertabell, ingen kø, ingen adresse.
--
-- DOMMEN v1 HVILER PÅ, håndhevet i DATAMODELLEN og ikke i et API-lag som
-- kunne omgås:
--
--   EN KONTROLL ER «OPPFYLT» BARE MED SKREVET EVIDENSHENVISNING OG DATO.
--   Det er ikke byråkrati — hele poenget med et kontrollregister er at
--   forskjellen mellom «vi gjør dette» og «vi kan vise at vi gjorde
--   dette» er den eneste som betyr noe i en revisjon. Formen er TOTAL og
--   ligger i tre lag:
--
--     1. CHECK-en `kontroll_oppfylt_krever_evidens`: en rad kan ikke STÅ
--        som `oppfylt` uten både `sist_etterprovd` og en ikke-tom
--        `evidens_ref`. Gjelder enhver skrivevei, også direkte DML som
--        tabellens eier.
--     2. VAKTEN `m34_kontroll_vakt`: evidenshenvisningen må svare til en
--        FAKTISK rad i `etterproving` — og til den SISTE. En henvisning
--        man kan skrive fritt er ingen henvisning; da er «oppfylt» bare
--        et tekstfelt til.
--     3. DØRENS RAISE i `m34_registrer_etterproving`: en tom
--        evidenshenvisning avvises med en feilmelding som sier hvorfor,
--        før noe skrives.
--
--   En kontroll uten eier er URESPRESENTERBAR (NOT NULL + FK), og en
--   kontroll forbi sin etterprøvingsfrist er et FUNN — ikke en rad som
--   stille blir gammel.
--
-- HVORFOR TO TABELLER, OG IKKE ÉN. `kontroll.sist_etterprovd` er en
-- AVLEDNING av siste rad i `etterproving`. Historikken er det revisor
-- faktisk ber om — «vis meg de fire siste gangene dere kontrollerte
-- dette» — og en tilstandskolonne alene kan ikke svare på det.
--
-- HVORFOR `sist_etterprovd` IKKE ER EN GENERERT KOLONNE. Den kunne ikke
-- vært det: PostgreSQLs `GENERATED ALWAYS AS` må være et IMMUTABLE
-- uttrykk over SAMME RAD, og kan verken lese en annen tabell eller
-- aggregere. Samme grense gjelder CHECK-en — og CHECK-en er nettopp der
-- dommen må stå, fordi den er den ene formen som gjelder for enhver
-- skrivevei. Avledningen MÅ derfor materialiseres. Den vedlikeholdes av
-- døren, i SAMME transaksjon som historikkraden, og — dette er poenget —
-- den er ETTERPRØVD AV VAKTEN: `sist_etterprovd`/`evidens_ref` må svare
-- til en faktisk `etterproving`-rad, og det kan ikke finnes en NYERE.
-- En vedlikeholdt avledning som ingen kontrollerer er en denormalisering
-- som driver; denne kan ikke drive uten at basen sier nei.
--
-- GRENSENE MOT SØSKNENE, sagt eksplisitt så ingen bygger det to ganger:
-- M-21 (096) eier PLIKTER — frister mot omverdenen. M-30 (099) eier
-- FORESPØRSLER fra registrerte. M-34 eier KONTROLLER — våre egne,
-- gjentakende etterprøvinger. De tre deler form og deler bevisst ikke
-- tabeller: fristes de sammen, blir «hva er dette» et felt i stedet for
-- en type, og de tre modulenes ulike dommer om hva som lukker en frist
-- kolliderer i samme rad.
--
-- SVEIPEN HAR SIN EGEN ROLLE OG SIN EGEN TIMER (095-formen, ikke 096s
-- forpass): `disponit_compliancesveip` har nøyaktig ÉN rettighet i basen
-- — EXECUTE på `m34_sveip_etterprovinger` — og INGEN tabellrettigheter.
-- Sveipen KØER INGEN VARSEL. Den skriver FUNN, og funnene leses i
-- flaten. En ny varselart ville krevd at både fasiten i
-- `deploy/staging/varselenum-reparasjon.sql` og `KANONISK` i
-- `test_varselenum.py` ble utvidet i samme commit; v1 trenger ingen av
-- delene, fordi et kontrollfunn ikke er en frist som løper fra noen — det
-- er en tilstand i registeret, og den hører hjemme der registeret leses.
--
-- FORMENE ER HUSETS (089/090/091/095/096): tabellene eies av migrator,
-- dørene av NOLOGIN-rollen `disponit_compliance_eier`, tenant TEXT + RLS
-- ENABLE+FORCE + `tenant_isolasjon` på hver tabell, SP-1
-- (`krev_tenantkontekst` FØRST) i hver tenantbundet definer, og INGEN
-- BYPASSRLS: kryss-tenant-autoriteten sveipen trenger er en EKSPLISITT,
-- SNEVER policy (§2) og ikke en rolleegenskap.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_compliance_eier') THEN
        RAISE EXCEPTION 'rollen disponit_compliance_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

-- FERSK-SERVER-FUNNET (095-formen): PostgreSQL >= 15 gir ingen CREATE på
-- `public` til andre enn skjemaeieren, og §3 oppretter funksjoner under
-- `SET LOCAL ROLE disponit_compliance_eier`. Grantet står HER og ikke i
-- `migrer.py`, fordi rettighetsblokkene der kjøres ETTER migrasjonene —
-- altså for sent til å redde denne. Medlemskapet migrator trenger for i
-- det hele tatt å kunne `SET LOCAL ROLE` krever ADMIN OPTION og hører
-- til rolleoppsettet (`oppsett-postgresql.sh`, `ci.yml`).
GRANT USAGE, CREATE ON SCHEMA public TO disponit_compliance_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `rammeverk` — hva vi etterlever. «ISO 27001», «GDPR», «NIS2».
--
-- TENANT-SKOPET, IKKE GLOBALT, OG DET ER ET VALG. Argumentet for globalt
-- er reelt: navnet «ISO 27001» er det samme hos alle. Men det er ikke
-- NAVNET registeret handler om — det er HVILKE rammeverk denne kunden
-- har påtatt seg, i hvilken versjon, og et globalt sett ville krevd tre
-- ting huset ikke har: en kurator med mandat til å vedlikeholde det, en
-- plattformadmin-skrivevei som ikke finnes, og et hull i
-- tenantisolasjonen på hver dør som joiner hit. Sammensatt fremmednøkkel
-- (tenant, rammeverk_id) holder RLS ærlig gjennom joinet: en kontroll
-- kan ikke peke på et annet tenants rammeverk, og det er en egenskap ved
-- NØKKELEN, ikke ved et predikat noen kan glemme. Prisen er at to kunder
-- skriver «ISO 27001» hver for seg — og den prisen er en duplisert
-- tekststreng, ikke en delt sannhet noen må eie.
CREATE TABLE rammeverk (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    rammeverk_id UUID NOT NULL,
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    versjon TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    CONSTRAINT rammeverk_pk PRIMARY KEY (tenant, rammeverk_id)
);

-- «ISO 27001:2022» skal være ETT rammeverk hos en kunde, ikke fem fordi
-- fem kontroller ble registrert av fem personer. `coalesce` i uttrykket
-- fordi NULL ikke kolliderer med NULL i en unik indeks — uten det ville
-- «versjon ikke oppgitt» vært uendelig mange ulike rammeverk.
CREATE UNIQUE INDEX rammeverk_unikt
    ON rammeverk (tenant, navn, coalesce(versjon, ''));

-- `kontroll` — den enkelte etterprøvingen vi har påtatt oss å gjøre.
CREATE TABLE kontroll (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kontroll_id UUID NOT NULL,
    rammeverk_id UUID NOT NULL,
    -- HJEMMELEN: paragrafen eller kontrollnummeret. «A.8.16», «art. 32»,
    -- «§ 21». Ikke-tom med vilje — en kontroll uten krav å svare på er
    -- ikke en kontroll, det er en vane.
    krav_ref TEXT NOT NULL CHECK (krav_ref ~ '[^[:space:]]'),
    beskrivelse TEXT NOT NULL CHECK (beskrivelse ~ '[^[:space:]]'),
    -- EN KONTROLL UTEN EIER ER URESPRESENTERBAR. NOT NULL + FK mot
    -- identiteten, ikke mot medlemskapet: mister eieren medlemskapet skal
    -- kontrollen fortsatt ha en eier å navngi, og raden skal ikke rives
    -- ut under en åpen etterprøvingsfrist. Samme valg som `plikt.
    -- eier_bruker_id` (096) og `varsel.bruker_id` (026) — og NETTOPP
    -- derfor finnes funntypen `kontroll_uten_eier` i §2: FK-en fanger
    -- «ingen eier i det hele tatt», sveipen fanger «eieren har sluttet».
    eier_bruker_id TEXT NOT NULL REFERENCES brukeridentitet (bruker_id),
    -- Hvor ofte kontrollen skal etterprøves. Positiv med vilje: 0 ville
    -- betydd «alltid forbigått», og det er ikke en frekvens, det er en
    -- feil.
    etterproving_dogn INT NOT NULL CHECK (etterproving_dogn > 0),
    -- AVLEDNINGEN av siste rad i `etterproving`. Se hodekommentaren for
    -- hvorfor den er materialisert og ikke generert — og §2 for vakten
    -- som gjør at den ikke kan drive fra historikken.
    -- Samme ramme som `etterproving.utfort` (§1), og av samme grunn:
    -- avledningen leses av lesedøren og av sveipen, og en uendelig dato
    -- der sprenger døgnregningen for hele basen. Vakten binder den
    -- uansett til en faktisk historikkrad — dette er beltet.
    sist_etterprovd DATE
        CHECK (sist_etterprovd IS NULL
               OR (sist_etterprovd > DATE '1900-01-01'
                   AND sist_etterprovd < DATE '9999-01-01')),
    evidens_ref TEXT,
    status TEXT NOT NULL DEFAULT 'ikke_oppfylt'
        CHECK (status IN ('oppfylt', 'ikke_oppfylt', 'ikke_relevant')),
    -- `ikke_relevant` er en BESLUTNING, ikke et fravær, og koster derfor
    -- sin egen skrevne begrunnelse. Feltene beholdes når en kontroll
    -- senere blir relevant igjen: at den VAR valgt bort, og hvorfor, er
    -- også historikk.
    ikke_relevant_begrunnelse TEXT,
    ikke_relevant_ts TIMESTAMPTZ,
    ikke_relevant_av TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    CONSTRAINT kontroll_pk PRIMARY KEY (tenant, kontroll_id),
    CONSTRAINT kontroll_rammeverk_fk FOREIGN KEY (tenant, rammeverk_id)
        REFERENCES rammeverk (tenant, rammeverk_id),
    -- DOMMEN, LAG 1. En kontroll kan ikke STÅ som oppfylt uten både en
    -- dato og en skreven evidenshenvisning. Gjelder ENHVER skrivevei —
    -- også direkte DML som eier, også en fremtidig dør som glemmer det.
    CONSTRAINT kontroll_oppfylt_krever_evidens CHECK (
        status <> 'oppfylt'
        OR (sist_etterprovd IS NOT NULL
            AND evidens_ref IS NOT NULL
            AND evidens_ref ~ '[^[:space:]]')),
    -- Evidenshenvisningen og datoen hører sammen. En henvisning uten dato
    -- er «vi har et dokument et sted», en dato uten henvisning er «vi
    -- gjorde noe en gang» — ingen av delene er evidens.
    CONSTRAINT kontroll_evidens_er_hel CHECK (
        (sist_etterprovd IS NULL) = (evidens_ref IS NULL)),
    CONSTRAINT kontroll_ikke_relevant_krever_begrunnelse CHECK (
        status <> 'ikke_relevant'
        OR (ikke_relevant_begrunnelse IS NOT NULL
            AND ikke_relevant_begrunnelse ~ '[^[:space:]]'
            AND ikke_relevant_ts IS NOT NULL
            AND ikke_relevant_av IS NOT NULL))
);

-- Sveipens skann og flatens liste leser begge «kontroller som skal
-- etterprøves». `ikke_relevant` er unntatt fra fristregningen (den er en
-- skreven beslutning, ikke en glemt kontroll), så delindeksen speiler
-- nøyaktig predikatet i §4.
CREATE INDEX kontroll_aktiv_frist ON kontroll (tenant, sist_etterprovd)
    WHERE status <> 'ikke_relevant';

-- `etterproving` — HISTORIKKEN. Append-only.
--
-- Dette er det revisor faktisk ber om. En tilstandskolonne svarer på
-- «er den oppfylt i dag»; bare historikken svarer på «vis meg de fire
-- siste gangene dere kontrollerte dette, og hva dere så».
CREATE TABLE etterproving (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    etterproving_id UUID NOT NULL,
    kontroll_id UUID NOT NULL,
    -- ENDELIG OG IKKE I FREMTIDEN. `date 'infinity'` er en lovlig DATE,
    -- og den ville vært giftig to steder: `sist_etterprovd + dogn` blir
    -- `infinity`, og `current_date - infinity` sprenger `int` i BÅDE
    -- lesedøren og funnkandidatene — altså feller den hele den
    -- kryss-tenante sveipen i én transaksjon, for alle tenanter samtidig.
    -- CHECK-en kan ikke lese klokka (`current_date` er ikke IMMUTABLE),
    -- så den setter en fast, endelig ramme; dørens RAISE i §3 er den som
    -- avviser fremtiden. En etterprøving er noe som HAR skjedd.
    utfort DATE NOT NULL
        CHECK (utfort > DATE '1900-01-01' AND utfort < DATE '9999-01-01'),
    -- Den som FAKTISK gjorde etterprøvingen — ofte en annen enn
    -- kontrollens eier, og alltid en annen enn «systemet». FK mot
    -- identiteten av samme grunn som eieren over.
    utfort_av_bruker_id TEXT NOT NULL
        REFERENCES brukeridentitet (bruker_id),
    -- EVIDENSHENVISNINGEN. Ikke-tom, alltid. Det er denne raden som gjør
    -- «oppfylt» til noe annet enn en påstand, og en tom henvisning ville
    -- gjort hele registeret til en avkryssingsliste.
    evidens_ref TEXT NOT NULL CHECK (evidens_ref ~ '[^[:space:]]'),
    utfall TEXT NOT NULL CHECK (utfall IN ('oppfylt', 'avvik')),
    avviksbeskrivelse TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    CONSTRAINT etterproving_pk PRIMARY KEY (tenant, etterproving_id),
    CONSTRAINT etterproving_kontroll_fk FOREIGN KEY (tenant, kontroll_id)
        REFERENCES kontroll (tenant, kontroll_id),
    -- Et avvik uten beskrivelse er et avvik ingen kan lukke. Samme form
    -- og samme begrunnelse som bortfallsbegrunnelsen i 096: den billige
    -- utgangen skal koste en setning.
    CONSTRAINT etterproving_avvik_krever_beskrivelse CHECK (
        utfall <> 'avvik'
        OR (avviksbeskrivelse IS NOT NULL
            AND avviksbeskrivelse ~ '[^[:space:]]'))
);

-- Vakten i §2 og døren i §3 slår begge opp «siste etterprøving for denne
-- kontrollen» — det er avledningens hele definisjon, og den skal ikke
-- koste en sekvensskann.
CREATE INDEX etterproving_siste
    ON etterproving (tenant, kontroll_id, utfort DESC);

-- `kontrollfunn` — hva sveipen fant. LUKKET SETT, ETT funn per
-- (kontroll, funntype), oppdatert med `sist_sett_sveip`.
--
-- Funnlisten vokser ikke med kadensen: en daglig sveip over en kontroll
-- som har vært forbigått i et år gir ETT funn, ikke 365. En funnliste
-- som vokser med kadensen er en funnliste folk lærer seg å overse — og
-- da forsvinner de viktige med dem.
--
-- DE TRE FUNNTYPENE, og hvorfor hver av dem er noe SKJEMAET IKKE KAN SI:
--
--   * `etterproving_forbigatt` — kontrollen er forbi
--     `sist_etterprovd + etterproving_dogn` (eller aldri etterprøvd og
--     forbi samme frist fra registreringen). En CHECK kan ikke måle
--     tidens gang; det er hele grunnen til at sveipen finnes.
--   * `kontroll_uten_eier` — eieren er ikke lenger et AKTIVT medlem av
--     tenanten. FK-en peker på identiteten, ikke på medlemskapet (se
--     `kontroll` over), så raden har fortsatt en eier å navngi mens
--     virkeligheten ikke har det. Det er nøyaktig gapet katalogens egen
--     KPI handler om.
--   * `oppfylt_uten_evidens` — en kontroll står `oppfylt` uten at
--     evidenshenvisningen svarer til en faktisk `etterproving`-rad.
--     CHECK-en og vakten gjør dette urepresenterbart gjennom hver
--     skrivevei i denne migrasjonen, og funntypen finnes LIKEVEL: hele
--     registerets verdi hviler på den koblingen, og en vakt ingen måler
--     utenfra er en vakt ingen merker at forsvinner. Den dagen tallet er
--     større enn null, har noe skrevet utenom vakten — og da er DET
--     funnet.
CREATE TABLE kontrollfunn (
    tenant TEXT NOT NULL,
    kontroll_id UUID NOT NULL,
    funntype TEXT NOT NULL CHECK (funntype IN (
        'etterproving_forbigatt', 'oppfylt_uten_evidens',
        'kontroll_uten_eier')),
    -- Kravreferansen KOPIERES inn i funnet: et funn skal kunne leses uten
    -- å slå opp raden det peker på.
    krav_ref TEXT NOT NULL,
    -- Hvor mange døgn over fristen kontrollen var da funnet sist ble
    -- sett. NULL for funntyper der spørsmålet ikke gir mening.
    dogn_over_frist INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT kontrollfunn_pk PRIMARY KEY (tenant, kontroll_id, funntype),
    CONSTRAINT kontrollfunn_kontroll_fk FOREIGN KEY (tenant, kontroll_id)
        REFERENCES kontroll (tenant, kontroll_id),
    -- Et åpent funn har ingen lukketid, et lukket har alltid en. Den
    -- halve lukkingen er urepresenterbar (089/095-formen).
    CONSTRAINT kontrollfunn_lukking_komplett
        CHECK (apen = (lukket_ts IS NULL))
);

CREATE INDEX kontrollfunn_apne ON kontrollfunn (tenant, funntype)
    WHERE apen;


-- ------------------------------------------------------------
-- 2. Radvaktene og radsikkerheten.
-- ------------------------------------------------------------

-- Vakten på `kontroll`. Fire regler, og den fjerde er DOMMENS LAG 2.
--
--   * DELETE avvises. Et kontrollregister der rader kan forsvinne er et
--     register ingen revisjon kan lese bakover. En kontroll som ikke
--     lenger gjelder markeres `ikke_relevant`, med begrunnelse.
--   * Identiteten er frosset (tenant, kontroll_id, rammeverk_id,
--     krav_ref, opprettet). Et annet krav er en ANNEN kontroll, ikke en
--     redigering av denne — nøyaktig samme dom som `plikt.kilde` (096).
--   * En statusovergang er FORFATTET, aldri avledet: enhver endring av
--     `status` krever en navngitt aktør i sesjonen (`disponit.aktor`).
--     Det finnes ingen jobb i denne migrasjonen som setter status —
--     sveipen skriver FUNN og rører ikke statuskolonnen i det hele tatt.
--   * EVIDENSKOBLINGEN. Er `sist_etterprovd` satt, MÅ det finnes en rad i
--     `etterproving` med nøyaktig den datoen og nøyaktig den
--     henvisningen, og det MÅ ikke finnes en nyere. Uten dette leddet
--     ville CHECK-en over vært oppfylt av enhver streng noen skrev inn i
--     feltet, og «oppfylt med evidens» ville bare vært «oppfylt med et
--     tekstfelt til». Det er også dette leddet som gjør den
--     materialiserte avledningen etterprøvbar: den kan ikke drive fra
--     historikken uten at basen sier nei.
CREATE FUNCTION m34_kontroll_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'kontroll: DELETE avvist — en kontroll markeres'
            ' ikke_relevant med begrunnelse, den slettes aldri som rad'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.tenant IS DISTINCT FROM OLD.tenant
           OR NEW.kontroll_id IS DISTINCT FROM OLD.kontroll_id
           OR NEW.rammeverk_id IS DISTINCT FROM OLD.rammeverk_id
           OR NEW.krav_ref IS DISTINCT FROM OLD.krav_ref
           OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
            RAISE EXCEPTION 'kontroll: identiteten (tenant, kontroll_id,'
                ' rammeverk, krav_ref, opprettet) er frosset — et annet'
                ' krav er en annen kontroll'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- Avledningen går aldri bakover. `sist_etterprovd` ER «siste rad
        -- i historikken», og historikken er append-only — en avledning
        -- som kunne settes tilbake ville gjort en etterprøving usett.
        IF NEW.sist_etterprovd IS NOT NULL
           AND OLD.sist_etterprovd IS NOT NULL
           AND NEW.sist_etterprovd < OLD.sist_etterprovd THEN
            RAISE EXCEPTION 'kontroll: sist_etterprovd går aldri bakover'
                ' — den er avledet av en append-only historikk'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.status IS DISTINCT FROM OLD.status THEN
            v_aktor := nullif(current_setting('disponit.aktor', true), '');
            IF v_aktor IS NULL THEN
                RAISE EXCEPTION 'kontroll: en statusovergang krever en'
                    ' navngitt aktør (disponit.aktor) — tiden oppfyller'
                    ' ingen kontroll'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF NEW.status = 'ikke_relevant'
               AND NEW.ikke_relevant_av IS DISTINCT FROM v_aktor THEN
                RAISE EXCEPTION 'kontroll: ikke_relevant_av (%) er ikke'
                    ' aktøren som feller beslutningen (%)',
                    coalesce(NEW.ikke_relevant_av, '<null>'), v_aktor
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
        END IF;
    END IF;
    -- DOMMENS LAG 2 — gjelder BÅDE INSERT og UPDATE.
    IF NEW.sist_etterprovd IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM public.etterproving e
                        WHERE e.tenant = NEW.tenant
                          AND e.kontroll_id = NEW.kontroll_id
                          AND e.utfort = NEW.sist_etterprovd
                          AND e.evidens_ref
                              IS NOT DISTINCT FROM NEW.evidens_ref) THEN
            RAISE EXCEPTION 'kontroll: evidenshenvisningen svarer ikke til'
                ' noen etterprøving — en henvisning man kan skrive fritt'
                ' er ingen henvisning'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF EXISTS (SELECT 1 FROM public.etterproving e
                    WHERE e.tenant = NEW.tenant
                      AND e.kontroll_id = NEW.kontroll_id
                      AND e.utfort > NEW.sist_etterprovd) THEN
            RAISE EXCEPTION 'kontroll: sist_etterprovd er ikke den siste'
                ' etterprøvingen — avledningen skal være sann, ikke bare'
                ' velformet'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m34_kontroll_vakt() FROM PUBLIC;
CREATE TRIGGER m34_kontroll_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON kontroll
    FOR EACH ROW EXECUTE FUNCTION m34_kontroll_vakt();
CREATE TRIGGER m34_kontroll_ingen_truncate
    BEFORE TRUNCATE ON kontroll
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- Historikken er APPEND-ONLY mot både UPDATE og DELETE. Det er hele
-- evidensen: en etterprøving som kunne redigeres i etterkant er ingen
-- etterprøving, og en som kunne slettes er en revisjon uten spor.
CREATE FUNCTION m34_etterproving_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'etterproving er append-only: % er forbudt — en'
        ' etterprøving som kan endres i etterkant er ingen etterprøving',
        TG_OP USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE ALL ON FUNCTION m34_etterproving_vakt() FROM PUBLIC;
CREATE TRIGGER m34_etterproving_vakt
    BEFORE UPDATE OR DELETE ON etterproving
    FOR EACH ROW EXECUTE FUNCTION m34_etterproving_vakt();
CREATE TRIGGER m34_etterproving_ingen_truncate
    BEFORE TRUNCATE ON etterproving
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- Funnvakten (095-formen): identiteten er frosset, DELETE/TRUNCATE
-- avvises — også for eieren. Sveipen får bare flytte ferskhets- og
-- livsløpsfeltene.
CREATE FUNCTION m34_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'kontrollfunn: % avvist — et funn lukkes, det'
            ' slettes aldri; at noe VAR et funn er også historikk', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.kontroll_id IS DISTINCT FROM OLD.kontroll_id
       OR NEW.funntype IS DISTINCT FROM OLD.funntype
       OR NEW.forst_sett IS DISTINCT FROM OLD.forst_sett THEN
        RAISE EXCEPTION 'kontrollfunn: identiteten (tenant, kontroll,'
            ' funntype) og førstegangsobservasjonen er frosset — et annet'
            ' funn er en annen rad'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.sist_sett_sveip < OLD.sist_sett_sveip THEN
        RAISE EXCEPTION 'kontrollfunn: sist_sett_sveip går aldri bakover'
            ' — en ferskhet som kan settes tilbake er ingen ferskhet'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m34_funn_vakt() FROM PUBLIC;
CREATE TRIGGER m34_funn_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON kontrollfunn
    FOR EACH ROW EXECUTE FUNCTION m34_funn_vakt();
CREATE TRIGGER m34_funn_ingen_truncate
    BEFORE TRUNCATE ON kontrollfunn
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

ALTER TABLE rammeverk ENABLE ROW LEVEL SECURITY;
ALTER TABLE rammeverk FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON rammeverk
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE kontroll ENABLE ROW LEVEL SECURITY;
ALTER TABLE kontroll FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON kontroll
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — ingen BYPASSRLS.
--
-- Sveipen må finne HVILKE tenanter som har kontroller, og det spørsmålet
-- kan ikke stilles innenfra én tenantkontekst. Autoriteten er derfor en
-- policy, ikke en rolleegenskap, og den er gjerdet tre ganger:
--
--   * bare `disponit_compliance_eier` (dørenes eier — ingen LOGIN-rolle),
--   * bare SELECT (sveipen SKRIVER aldri kryss-tenant: hvert funn skrives
--     etter at konteksten er bundet til RADENS tenant),
--   * bare når det IKKE står en tenantkontekst i sesjonen.
--
-- Det siste leddet er det bærende. Dørene i §3 kommer alltid gjennom
-- `krev_tenantkontekst`, som fail-closed krever en ikke-tom kontekst —
-- inne i en dør er denne policyen derfor ALLTID usann, og
-- `tenant_isolasjon` er den eneste som gjelder. De to er disjunkte per
-- konstruksjon, så kryss-tenant-synet finnes nøyaktig i det ene vinduet
-- sveipen bruker det, og ingen annen kodevei kan snuble inn i det.
CREATE POLICY m34_sveip_tenantliste ON kontroll
    FOR SELECT TO disponit_compliance_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

ALTER TABLE etterproving ENABLE ROW LEVEL SECURITY;
ALTER TABLE etterproving FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON etterproving
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE kontrollfunn ENABLE ROW LEVEL SECURITY;
ALTER TABLE kontrollfunn FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON kontrollfunn
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
-- INGEN kryss-tenant-policy på funnene, og det er ikke en forglemmelse:
-- sveipen finner tenantene i `kontroll` (uten kontekst) og gjør ALT
-- funnarbeidet med RADENS tenant satt. Skrivingen er dermed tenantbundet
-- av RLS, også for sveipen selv.

-- Rettighetene dørenes eier trenger, og ikke mer. Merk hva som IKKE står
-- her: ingen runtime-rolle får en eneste tabellrettighet på de fire
-- tabellene (SP-7, 090/091/095/096-formen) — hele registeret nås KUN
-- gjennom dørene i §3, og de krever tenantkontekst først.
GRANT SELECT, INSERT ON rammeverk TO disponit_compliance_eier;
GRANT SELECT, INSERT, UPDATE ON kontroll TO disponit_compliance_eier;
GRANT SELECT, INSERT ON etterproving TO disponit_compliance_eier;
GRANT SELECT, INSERT, UPDATE ON kontrollfunn TO disponit_compliance_eier;
-- Fremmednøklene mot identiteten opprettes over (som migrator, som eier
-- begge tabellene); eieren trenger REFERENCES bare hvis en senere
-- migrasjon skulle legge til flere.
GRANT REFERENCES ON brukeridentitet TO disponit_compliance_eier;
-- Medlemskapssjekken ved registrering OG funntypen `kontroll_uten_eier`.
-- RLS-gjerdet på tenant.
GRANT SELECT ON brukermedlemskap TO disponit_compliance_eier;
-- Visningsnavnet i lesedøren. KOLONNEGRANT, ikke tabellgrant
-- (husregelen): `issuer` og `sub` er identitetens hemmelige halvdel
-- (010), og kontrollregisteret har ingen bruk for dem. At registerets
-- eier aldri kan lese dem skal være en egenskap ved BASEN, ikke ved kode
-- som tilfeldigvis ikke gjør det.
GRANT SELECT (bruker_id, profil) ON brukeridentitet
    TO disponit_compliance_eier;
-- EVIDENSKJEDEN (m02, manifestets ene reelle avhengighet): hver
-- registrering, hver etterprøving og hver ikke-relevant-beslutning
-- skriver sin egen loggpost, i SAMME transaksjon som handlingen. Se §3.
-- INSERT alene — evidenskjeden skrives til, den leses aldri herfra.
GRANT INSERT ON revisjonslogg TO disponit_compliance_eier;

-- Kontekstporten eies av `disponit_m37_claimer` og er REVOKEd fra PUBLIC
-- (038). Dørene under er SECURITY DEFINER og løper som
-- `disponit_compliance_eier` — uten dette grantet ville SP-1-porten
-- feilet med «permission denied», og registeret vært nede i stedet for
-- sikret. Grantet gis av eieren selv (039-formen); migrator er medlem av
-- begge.
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_compliance_eier;
RESET ROLE;


-- ------------------------------------------------------------
-- 3. Dørene. SECURITY DEFINER, eid av `disponit_compliance_eier`, og
--    hver tenantbundet dør kaller `krev_tenantkontekst` FØRST (SP-1).
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_compliance_eier;

-- Evidenskjeden, ett sted. Kalles av hver skrivedør, i dens egen
-- transaksjon.
--
-- ÆRLIG OM FORMEN (096s begrunnelse, ordrett): `revisjonslogg` har ingen
-- ciphertext-kolonner (041 §4 dokumenterer det mot levende base), så
-- `payload_type='kryptert'` med `referansepayload IS NULL` er den
-- ordinære formen HVER eksisterende skriver bruker — ikke en påstand om
-- at det finnes en kryptert payload et sted.
--
-- `input_hash` er sha256 over den kanoniske beskrivelsen av HANDLINGEN,
-- ikke over kundedata: kontrollens id, hva som skjedde og hvilken dato
-- det gjaldt. Beskrivelsen, kravreferansen og evidenshenvisningen står
-- ALDRI her — de er kundens tekst, og evidenskjeden skal kunne gjenfinne
-- handlingen uten å arkivere innholdet på nytt.
CREATE FUNCTION m34_evidens(p_tenant TEXT, p_kontroll_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm34_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm34_compliance', 'handling', p_handling,
        'kontroll_id', p_kontroll_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm34_compliance',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:kontrollregister', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m34_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- Rammeverket, funnet eller født. INGEN EGEN DØR, med vilje: et rammeverk
-- uten en eneste kontroll er en tom overskrift, og en flate som ba
-- brukeren opprette overskriften først ville invitert til nettopp det.
-- Rammeverket oppstår når den første kontrollen under det registreres,
-- og gjenbrukes deretter — den unike indeksen i §1 er det som gjør
-- «gjenbrukes» sant og ikke bare sannsynlig.
CREATE FUNCTION m34_rammeverk_id(p_tenant TEXT, p_navn TEXT,
                                 p_versjon TEXT, p_aktor TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID; v_versjon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm34_rammeverk_id');
    IF p_navn IS NULL OR p_navn !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm34_rammeverk_id: rammeverket må ha et navn'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_versjon := nullif(btrim(coalesce(p_versjon, '')), '');
    SELECT r.rammeverk_id INTO v_id FROM public.rammeverk r
     WHERE r.tenant = p_tenant AND r.navn = btrim(p_navn)
       AND coalesce(r.versjon, '') = coalesce(v_versjon, '');
    IF v_id IS NOT NULL THEN
        RETURN v_id;
    END IF;
    v_id := gen_random_uuid();
    INSERT INTO public.rammeverk (tenant, rammeverk_id, navn, versjon,
                                  opprettet_av)
    VALUES (p_tenant, v_id, btrim(p_navn), v_versjon, p_aktor)
        ON CONFLICT (tenant, navn, coalesce(versjon, '')) DO NOTHING;
    -- Tapte vi kappløpet, står den andre transaksjonens rad der nå.
    IF NOT FOUND THEN
        SELECT r.rammeverk_id INTO v_id FROM public.rammeverk r
         WHERE r.tenant = p_tenant AND r.navn = btrim(p_navn)
           AND coalesce(r.versjon, '') = coalesce(v_versjon, '');
    END IF;
    RETURN v_id;
END $$;
REVOKE ALL ON FUNCTION m34_rammeverk_id(TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC;

-- Registreringsdøren. SP-2-materialitet på `p_kontroll_id` (m35/096-
-- formen): kalleren utleder id-en deterministisk av sin
-- Idempotency-Key, så et gjenspill med identisk innhold er et STILLE JA
-- (false), mens samme id med ANNET innhold er en materiell konflikt.
--
-- EIEREN MÅ VÆRE AKTIVT MEDLEM. FK-en alene sier bare at bruker-id-en
-- finnes et sted i plattformen — og en kontroll eid av en fremmed
-- tenants bruker er en kontroll ingen her gjør.
--
-- STATUS VED FØDSELEN ER `ikke_oppfylt`, og det er ikke pessimisme: en
-- kontroll ingen har etterprøvd ennå KAN ikke vises fram, og registeret
-- skal si det den dagen den blir opprettet — ikke først når noen spør.
CREATE FUNCTION m34_registrer_kontroll(
    p_tenant TEXT, p_kontroll_id UUID, p_rammeverk_navn TEXT,
    p_rammeverk_versjon TEXT, p_krav_ref TEXT, p_beskrivelse TEXT,
    p_eier_bruker_id TEXT, p_etterproving_dogn INT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rammeverk UUID; v_rader INT; v_gammel RECORD; v_navn TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm34_registrer_kontroll');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_krav_ref IS NULL OR p_krav_ref !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm34_registrer_kontroll: kravreferansen kan ikke'
            ' være tom — en kontroll uten krav å svare på er en vane,'
            ' ikke en kontroll'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_beskrivelse IS NULL OR p_beskrivelse !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm34_registrer_kontroll: beskrivelsen kan ikke'
            ' være tom' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF coalesce(p_etterproving_dogn, 0) <= 0 THEN
        RAISE EXCEPTION 'm34_registrer_kontroll: etterprøvingsintervallet'
            ' må være minst ett døgn — 0 er ikke en frekvens'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.brukermedlemskap bm
                    WHERE bm.tenant = p_tenant
                      AND bm.bruker_id = p_eier_bruker_id
                      AND bm.aktiv) THEN
        RAISE EXCEPTION 'm34_registrer_kontroll: % er ikke et aktivt'
            ' medlem av tenanten — en kontroll uten eier her er en'
            ' kontroll ingen gjør', coalesce(p_eier_bruker_id, '<null>')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_navn := btrim(coalesce(p_rammeverk_navn, ''));
    v_rammeverk := public.m34_rammeverk_id(p_tenant, v_navn,
                                           p_rammeverk_versjon, p_aktor);
    INSERT INTO public.kontroll
        (tenant, kontroll_id, rammeverk_id, krav_ref, beskrivelse,
         eier_bruker_id, etterproving_dogn, opprettet_av)
    VALUES (p_tenant, p_kontroll_id, v_rammeverk, btrim(p_krav_ref),
            p_beskrivelse, p_eier_bruker_id, p_etterproving_dogn, p_aktor)
        ON CONFLICT (tenant, kontroll_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        -- SP-2: samme id igjen. Identisk innhold er et stille ja; annet
        -- innhold er en materiell konflikt kalleren SKAL se.
        -- MATERIALITETEN DEKKER HELE KONTROLLEN: `etterproving_dogn`
        -- avgjør NÅR den blir et funn, og eieren avgjør HVEM som får
        -- vite det. Et gjenspill som endret ett av dem ville fått et
        -- stille ja på en kontroll som oppfører seg annerledes enn den
        -- kalleren tror den registrerte.
        SELECT * INTO v_gammel FROM public.kontroll
         WHERE tenant = p_tenant AND kontroll_id = p_kontroll_id;
        IF v_gammel.rammeverk_id IS DISTINCT FROM v_rammeverk
           OR v_gammel.krav_ref IS DISTINCT FROM btrim(p_krav_ref)
           OR v_gammel.beskrivelse IS DISTINCT FROM p_beskrivelse
           OR v_gammel.eier_bruker_id IS DISTINCT FROM p_eier_bruker_id
           OR v_gammel.etterproving_dogn
              IS DISTINCT FROM p_etterproving_dogn THEN
            RAISE EXCEPTION 'm34_registrer_kontroll: samme kontroll_id med'
                ' annet innhold — materiell konflikt'
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN false;
    END IF;
    PERFORM public.m34_evidens(
        p_tenant, p_kontroll_id, 'kontroll.registrert', p_aktor,
        jsonb_build_object('rammeverk_id', v_rammeverk::text,
                           'eier_bruker_id', p_eier_bruker_id,
                           'etterproving_dogn', p_etterproving_dogn));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m34_registrer_kontroll(
    TEXT, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, INT, TEXT) FROM PUBLIC;

-- ETTERPRØVINGSDØREN — dommens lag 3.
--
-- Rekkefølgen er HISTORIKK → AVLEDNING, og den er ikke tilfeldig: vakten
-- i §2 krever at `kontroll.sist_etterprovd` svarer til en faktisk rad, så
-- raden MÅ finnes først. Begge skrives i samme transaksjon — ruller den
-- tilbake, forsvinner begge; committer den, står begge. En etterprøving
-- uten avledning og en avledning uten etterprøving er dermed like
-- urepresenterbare.
--
-- AVLEDNINGEN LESES UT AV TABELLEN, den regnes ikke fra parameteren. En
-- etterprøving som registreres i etterkant (utført 3. mars, skrevet inn
-- 10. mars, etter at en 5.-mars-etterprøving alt sto der) skal ikke
-- flytte tilstanden bakover — og en dør som bare hadde skrevet
-- `p_utfort` ville gjort nettopp det. Historikken får raden sin uansett;
-- det er hele grunnen til at historikken er en egen tabell.
--
-- UTFALLET STYRER STATUS: `oppfylt` gir `oppfylt`, `avvik` gir
-- `ikke_oppfylt`. Et avvik er ikke «litt oppfylt».
CREATE FUNCTION m34_registrer_etterproving(
    p_tenant TEXT, p_etterproving_id UUID, p_kontroll_id UUID,
    p_utfort DATE, p_utfort_av_bruker_id TEXT, p_evidens_ref TEXT,
    p_utfall TEXT, p_avviksbeskrivelse TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_gammel RECORD; k RECORD;
        v_dato DATE; v_ref TEXT; v_status TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm34_registrer_etterproving');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    -- DOMMENS LAG 3. Meldingen sier HVORFOR, ikke bare «feltet er
    -- påkrevd»: uten en evidenshenvisning er «oppfylt» en påstand, og en
    -- påstand er ikke noe man kan legge fram i en revisjon.
    IF p_evidens_ref IS NULL OR p_evidens_ref !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm34_registrer_etterproving: evidenshenvisningen'
            ' kan ikke være tom — en kontroll er oppfylt bare med en'
            ' skreven henvisning og en dato'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_utfort IS NULL THEN
        RAISE EXCEPTION 'm34_registrer_etterproving: datoen kan ikke være'
            ' tom — «vi gjør dette» og «vi kan vise at vi gjorde dette»'
            ' skilles av nettopp den'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- EN ETTERPRØVING ER NOE SOM HAR SKJEDD (CodeRabbit, kritisk). En
    -- uendelig eller fremtidig dato er verken evidens eller en frist: den
    -- fjerner kontrollen fra sveipens funn for alltid, og `infinity`
    -- sprenger i tillegg døgnregningen i `m34_kontrollbilde` og
    -- `m34_funnkandidater` — altså hele den kryss-tenante sveipen, i én
    -- transaksjon, for alle tenanter samtidig. CHECK-en i §1 stenger
    -- uendeligheten for enhver skrivevei; fremtiden kan bare stenges her,
    -- fordi `current_date` ikke er IMMUTABLE og derfor ikke kan stå i en
    -- CHECK.
    IF NOT isfinite(p_utfort) OR p_utfort > current_date THEN
        RAISE EXCEPTION 'm34_registrer_etterproving: datoen må være en'
            ' endelig dato som ikke ligger i fremtiden — en etterprøving'
            ' er noe som har skjedd, ikke noe som skal skje'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_utfall IS NULL OR p_utfall NOT IN ('oppfylt', 'avvik') THEN
        RAISE EXCEPTION 'm34_registrer_etterproving: ukjent utfall %',
            coalesce(p_utfall, '<null>')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_utfall = 'avvik'
       AND (p_avviksbeskrivelse IS NULL
            OR p_avviksbeskrivelse !~ '[^[:space:]]') THEN
        RAISE EXCEPTION 'm34_registrer_etterproving: et avvik uten'
            ' beskrivelse er et avvik ingen kan lukke'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO k FROM public.kontroll
     WHERE tenant = p_tenant AND kontroll_id = p_kontroll_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm34_registrer_etterproving: ukjent kontroll %',
            p_kontroll_id USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.brukermedlemskap bm
                    WHERE bm.tenant = p_tenant
                      AND bm.bruker_id = p_utfort_av_bruker_id
                      AND bm.aktiv) THEN
        RAISE EXCEPTION 'm34_registrer_etterproving: % er ikke et aktivt'
            ' medlem av tenanten — en etterprøving utført av ingen er'
            ' ingen etterprøving',
            coalesce(p_utfort_av_bruker_id, '<null>')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.etterproving
        (tenant, etterproving_id, kontroll_id, utfort,
         utfort_av_bruker_id, evidens_ref, utfall, avviksbeskrivelse,
         opprettet_av)
    VALUES (p_tenant, p_etterproving_id, p_kontroll_id, p_utfort,
            p_utfort_av_bruker_id, btrim(p_evidens_ref), p_utfall,
            p_avviksbeskrivelse, p_aktor)
        ON CONFLICT (tenant, etterproving_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        -- SP-2 igjen. Historikken er append-only, så et gjenspill KAN
        -- ikke rette noe — men det skal heller ikke få se ut som om det
        -- gjorde det.
        SELECT * INTO v_gammel FROM public.etterproving
         WHERE tenant = p_tenant AND etterproving_id = p_etterproving_id;
        -- MATERIALITETEN DEKKER HELE RADEN (CodeRabbit), ikke bare
        -- hodet: HVEM som utførte etterprøvingen og HVA avviket besto i
        -- er nettopp det revisor leser. Et gjenspill som endret ett av
        -- dem ville fått et stille ja på en etterprøving som sier noe
        -- annet enn den kalleren tror den bokførte — og SP-2s hele poeng
        -- er at et gjenspill ikke skal kunne endre noe i det stille.
        IF v_gammel.kontroll_id IS DISTINCT FROM p_kontroll_id
           OR v_gammel.utfort IS DISTINCT FROM p_utfort
           OR v_gammel.evidens_ref IS DISTINCT FROM btrim(p_evidens_ref)
           OR v_gammel.utfall IS DISTINCT FROM p_utfall
           OR v_gammel.utfort_av_bruker_id
              IS DISTINCT FROM p_utfort_av_bruker_id
           OR v_gammel.avviksbeskrivelse
              IS DISTINCT FROM p_avviksbeskrivelse THEN
            RAISE EXCEPTION 'm34_registrer_etterproving: samme'
                ' etterproving_id med annet innhold — materiell konflikt'
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN false;
    END IF;
    -- AVLEDNINGEN, lest ut av historikken slik den nå står.
    SELECT e.utfort, e.evidens_ref, e.utfall INTO v_dato, v_ref, v_status
      FROM public.etterproving e
     WHERE e.tenant = p_tenant AND e.kontroll_id = p_kontroll_id
     ORDER BY e.utfort DESC, e.opprettet DESC
     LIMIT 1;
    UPDATE public.kontroll
       SET sist_etterprovd = v_dato, evidens_ref = v_ref,
           status = CASE WHEN v_status = 'oppfylt' THEN 'oppfylt'
                         ELSE 'ikke_oppfylt' END
     WHERE tenant = p_tenant AND kontroll_id = p_kontroll_id;
    PERFORM public.m34_evidens(
        p_tenant, p_kontroll_id, 'kontroll.etterprovd', p_aktor,
        jsonb_build_object('etterproving_id', p_etterproving_id::text,
                           'utfort', p_utfort,
                           'utfall', p_utfall,
                           'utfort_av', p_utfort_av_bruker_id,
                           'evidens_ref_lengde',
                           length(btrim(p_evidens_ref))));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m34_registrer_etterproving(
    TEXT, UUID, UUID, DATE, TEXT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;

-- «Denne kontrollen gjelder ikke oss.» Den BILLIGSTE utgangen av et
-- kontrollregister, og derfor den som skal koste en setning: uten
-- begrunnelsen ville `ikke_relevant` vært en gratis vei ut av enhver
-- kontroll, og registeret en liste over ting man kan klikke bort.
--
-- IKKE TERMINAL, og det er et valg. En kontroll som var utenfor
-- virkeområdet kan komme innenfor — nytt marked, ny tjeneste, nytt
-- system. En etterprøving på den løfter den ut igjen, og begrunnelsen
-- blir stående i raden: at den VAR valgt bort, og hvorfor, er også
-- historikk.
CREATE FUNCTION m34_marker_ikke_relevant(
    p_tenant TEXT, p_kontroll_id UUID, p_begrunnelse TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE k RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm34_marker_ikke_relevant');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_begrunnelse IS NULL OR p_begrunnelse !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm34_marker_ikke_relevant: en kontroll man har'
            ' valgt bort er en beslutning, ikke et fravær — begrunnelsen'
            ' kan ikke være tom'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO k FROM public.kontroll
     WHERE tenant = p_tenant AND kontroll_id = p_kontroll_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm34_marker_ikke_relevant: ukjent kontroll %',
            p_kontroll_id USING ERRCODE = 'no_data_found';
    END IF;
    IF k.status = 'ikke_relevant' THEN
        RAISE EXCEPTION 'm34_marker_ikke_relevant: kontrollen står alt'
            ' som ikke relevant'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.kontroll
       SET status = 'ikke_relevant',
           ikke_relevant_begrunnelse = p_begrunnelse,
           ikke_relevant_ts = now(), ikke_relevant_av = p_aktor
     WHERE tenant = p_tenant AND kontroll_id = p_kontroll_id;
    PERFORM public.m34_evidens(
        p_tenant, p_kontroll_id, 'kontroll.ikke_relevant', p_aktor,
        jsonb_build_object('forrige_status', k.status,
                           'begrunnelse_lengde', length(p_begrunnelse)));
END $$;
REVOKE ALL ON FUNCTION m34_marker_ikke_relevant(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

-- LESEDØREN (051/090/096-formen): flatens hele lesetilstand i ett kall.
-- Runtime har INGEN SELECT på tabellene, så dette er den eneste veien
-- inn — og den krever tenantkontekst først.
--
-- `dogn_over_frist` REGNES HER, i samme skann som raden, fordi flaten
-- ikke skal trekke to datoer fra hverandre. Negativt tall = det er så
-- mange døgn IGJEN til fristen; positivt = så mange døgn OVER. Ett tall
-- avledet av én dato, ikke et forhold mellom to av svarets tall
-- (M-16-regelen).
--
-- `apne_funn` er sveipens dom, båret helt fram til raden den gjelder. Et
-- funn ingen kan se er ikke et funn — det er en rad.
CREATE FUNCTION m34_kontrollbilde(p_tenant TEXT, p_grense INT)
RETURNS TABLE(kontroll_id UUID, rammeverk_navn TEXT,
              rammeverk_versjon TEXT, krav_ref TEXT, beskrivelse TEXT,
              eier_bruker_id TEXT, eier_navn TEXT, eier_aktiv BOOLEAN,
              etterproving_dogn INT, sist_etterprovd DATE,
              evidens_ref TEXT, forfaller DATE, dogn_over_frist INT,
              status TEXT, ikke_relevant_begrunnelse TEXT,
              antall_etterprovinger INT, siste_utfall TEXT,
              siste_avvik TEXT, apne_funn TEXT[])
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm34_kontrollbilde');
    RETURN QUERY
    SELECT k.kontroll_id, r.navn, r.versjon, k.krav_ref, k.beskrivelse,
           k.eier_bruker_id,
           -- Visningsnavnet fra den LUKKEDE profil-DTO-en (010). NULL når
           -- IdP-en ikke ga noe — flaten viser da bruker-id-en, som er
           -- ærligere enn en tom celle.
           nullif(btrim(coalesce(b.profil->>'visningsnavn', '')), ''),
           -- Eieren SLIK DEN ER I DAG. Sveipen reiser funnet, men flaten
           -- skal ikke vente på nattens kjøring for å kunne si at eieren
           -- har sluttet.
           EXISTS (SELECT 1 FROM public.brukermedlemskap bm
                    WHERE bm.tenant = k.tenant
                      AND bm.bruker_id = k.eier_bruker_id AND bm.aktiv),
           k.etterproving_dogn, k.sist_etterprovd, k.evidens_ref,
           (coalesce(k.sist_etterprovd, k.opprettet::date)
            + k.etterproving_dogn)::date,
           (current_date
            - (coalesce(k.sist_etterprovd, k.opprettet::date)
               + k.etterproving_dogn))::int,
           k.status, k.ikke_relevant_begrunnelse,
           (SELECT count(*)::int FROM public.etterproving e
             WHERE e.tenant = k.tenant AND e.kontroll_id = k.kontroll_id),
           s.utfall, s.avviksbeskrivelse,
           coalesce((SELECT array_agg(f.funntype ORDER BY f.funntype)
                       FROM public.kontrollfunn f
                      WHERE f.tenant = k.tenant
                        AND f.kontroll_id = k.kontroll_id AND f.apen),
                    ARRAY[]::TEXT[])
      FROM public.kontroll k
      JOIN public.rammeverk r
        ON r.tenant = k.tenant AND r.rammeverk_id = k.rammeverk_id
      LEFT JOIN public.brukeridentitet b
        ON b.bruker_id = k.eier_bruker_id
      LEFT JOIN LATERAL (
            SELECT e.utfall, e.avviksbeskrivelse
              FROM public.etterproving e
             WHERE e.tenant = k.tenant AND e.kontroll_id = k.kontroll_id
             ORDER BY e.utfort DESC, e.opprettet DESC LIMIT 1) s ON true
     WHERE k.tenant = p_tenant
     -- Det som er lengst over fristen står øverst. `ikke_relevant` sist:
     -- den er en skreven beslutning og haster ikke.
     ORDER BY (k.status = 'ikke_relevant'),
              (current_date
               - (coalesce(k.sist_etterprovd, k.opprettet::date)
                  + k.etterproving_dogn)) DESC,
              r.navn, k.krav_ref, k.kontroll_id
     LIMIT greatest(least(coalesce(p_grense, 200), 500), 1);
END $$;
REVOKE ALL ON FUNCTION m34_kontrollbilde(TEXT, INT) FROM PUBLIC;

-- FUNNKANDIDATENE — sveipens tre spørsmål, ett sted.
--
-- Egen funksjon nettopp for at SP-1-PORTEN SKAL GJELDE HER OGSÅ
-- (038-reaperens form, ordrett fra 096s `m21_koe_for_tenant`): sveipen
-- binder konteksten til RADENS tenant og kaller hit, og da går
-- funnarbeidet gjennom nøyaktig den `krev_tenantkontekst` enhver annen
-- kaller går gjennom. Porten er ikke noe sveipen slipper unna, bare noe
-- den oppfyller per tenant.
--
-- At den er ÉN funksjon, og ikke tre predikater kopiert inn i tre
-- setninger i sveipen, er det som gjør lukkingen sann: steg 3 der lukker
-- «alt som ikke lenger er kandidat», og hadde reisingen og lukkingen lest
-- hver sin kopi av regelen, ville de to før eller siden sagt noe
-- forskjellig — og et funn ville hengt igjen etter at grunnen forsvant.
CREATE FUNCTION m34_funnkandidater(p_tenant TEXT, p_dag DATE)
RETURNS TABLE(kontroll_id UUID, funntype TEXT, krav_ref TEXT,
              dogn_over_frist INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm34_funnkandidater');
    RETURN QUERY
    -- 1. FORBIGÅTT ETTERPRØVING. Fristen løper fra siste etterprøving —
    --    eller, for en kontroll som aldri er etterprøvd, fra dagen den
    --    ble registrert. Det siste er poenget: en kontroll som ble
    --    skrevet ned og aldri gjort er ikke en kontroll uten frist, det
    --    er en kontroll som er forbigått fra første intervall.
    --    `ikke_relevant` er unntatt — den er en skreven beslutning, ikke
    --    en glemt kontroll, og det er hele lønnen for å skrive
    --    begrunnelsen.
    SELECT k.kontroll_id, 'etterproving_forbigatt'::text, k.krav_ref,
           (p_dag - (coalesce(k.sist_etterprovd, k.opprettet::date)
                     + k.etterproving_dogn))::int
      FROM public.kontroll k
     WHERE k.tenant = p_tenant
       AND k.status <> 'ikke_relevant'
       AND (coalesce(k.sist_etterprovd, k.opprettet::date)
            + k.etterproving_dogn) < p_dag
    UNION ALL
    -- 2. KONTROLL UTEN EIER. Fremmednøkkelen peker på identiteten, ikke
    --    på medlemskapet (se `kontroll` i §1), så raden har fortsatt en
    --    eier å navngi lenge etter at mennesket sluttet. Det er nøyaktig
    --    gapet katalogens egen KPI handler om, og det eneste stedet i
    --    modulen der «uten eier» kan bli sant.
    SELECT k.kontroll_id, 'kontroll_uten_eier'::text, k.krav_ref,
           NULL::int
      FROM public.kontroll k
     WHERE k.tenant = p_tenant
       AND k.status <> 'ikke_relevant'
       AND NOT EXISTS (SELECT 1 FROM public.brukermedlemskap bm
                        WHERE bm.tenant = k.tenant
                          AND bm.bruker_id = k.eier_bruker_id
                          AND bm.aktiv)
    UNION ALL
    -- 3. OPPFYLT UTEN EVIDENS. CHECK-en (§1) og vakten (§2) gjør dette
    --    urepresenterbart gjennom hver skrivevei i denne migrasjonen.
    --    Spørsmålet stilles LIKEVEL, hver natt: hele registerets verdi
    --    hviler på koblingen mellom «oppfylt» og en faktisk
    --    etterprøvingsrad, og en vakt ingen måler utenfra er en vakt
    --    ingen merker at forsvinner. Tallet skal være null. Den dagen det
    --    ikke er det, har noe skrevet utenom vakten — og DA er det
    --    funnet.
    SELECT k.kontroll_id, 'oppfylt_uten_evidens'::text, k.krav_ref,
           NULL::int
      FROM public.kontroll k
     WHERE k.tenant = p_tenant
       AND k.status = 'oppfylt'
       AND NOT EXISTS (
            SELECT 1 FROM public.etterproving e
             WHERE e.tenant = k.tenant
               AND e.kontroll_id = k.kontroll_id
               AND e.utfort IS NOT DISTINCT FROM k.sist_etterprovd
               AND e.evidens_ref IS NOT DISTINCT FROM k.evidens_ref);
END $$;
REVOKE ALL ON FUNCTION m34_funnkandidater(TEXT, DATE) FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. ETTERPRØVINGSSVEIPEN. Kryss-tenant, innelukket autoritet
--    (038/057/095-formen): INTET tenantparameter, utvalget ER
--    predikatet, og alt funnarbeid gjøres med RADENS tenant i
--    konteksten.
--
--    Kandidatsettet MATERIALISERES før første `set_config`. En åpen
--    cursor over `kontroll` mens tenantkonteksten endres under føttene
--    på den ville vært et RLS-predikat som skifter mening midt i en
--    løkke — riktig svar i test, uforutsigbart under last.
--
--    `p_grense` er et tak på HVOR MANGE NYE FUNN én kjøring reiser per
--    tenant. Det begrenser transaksjonen, ikke sannheten: funnene er
--    idempotente, så neste kjøring tar igjen resten. Traff sveipen taket
--    sitt, SIER DEN DET (`avkortet`) — en jobb som ikke kunne måle ferdig
--    rapporterer funn, aldri null.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_compliance_eier;

CREATE FUNCTION m34_sveip_etterprovinger(p_grense INT DEFAULT 500)
RETURNS TABLE(tenanter INT, nye INT, oppdaterte INT, lukkede INT,
              avkortet INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_n INT; v_grense INT;
        v_dag DATE; v_naa TIMESTAMPTZ;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm34_sveip_etterprovinger: sveipen er KRYSS-TENANT'
            ' og kjøres uten tenantkontekst — en kaller som har satt en'
            ' kontekst ber om noe annet enn det denne funksjonen gjør'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_grense := greatest(least(coalesce(p_grense, 500), 5000), 1);
    v_dag := current_date;
    v_naa := now();
    tenanter := 0; nye := 0; oppdaterte := 0; lukkede := 0; avkortet := 0;
    SELECT array_agg(DISTINCT k.tenant ORDER BY k.tenant) INTO v_tenanter
      FROM public.kontroll k;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        tenanter := tenanter + 1;

        -- 1. Ferskheten på funn som ALT finnes. IDEMPOTENSEN BOR HER: en
        --    sveip nummer to på den samme forbigåtte kontrollen flytter
        --    `sist_sett_sveip` og skriver ingen ny rad.
        UPDATE public.kontrollfunn f
           SET sist_sett_sveip = v_naa, apen = true, lukket_ts = NULL,
               krav_ref = kand.krav_ref,
               dogn_over_frist = kand.dogn_over_frist
          FROM public.m34_funnkandidater(v_t, v_dag) kand
         WHERE f.tenant = v_t AND f.kontroll_id = kand.kontroll_id
           AND f.funntype = kand.funntype;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        oppdaterte := oppdaterte + v_n;

        -- 2. De nye — med taket. `ORDER BY` gjør avkortingen forutsigbar
        --    og setter det verste øverst: treffer sveipen taket sitt, er
        --    det de mest forbigåtte kontrollene som HAR fått funn.
        INSERT INTO public.kontrollfunn
            (tenant, kontroll_id, funntype, krav_ref, dogn_over_frist,
             forst_sett, sist_sett_sveip, apen)
        SELECT v_t, kand.kontroll_id, kand.funntype, kand.krav_ref,
               kand.dogn_over_frist, v_naa, v_naa, true
          FROM public.m34_funnkandidater(v_t, v_dag) kand
         WHERE NOT EXISTS (
                SELECT 1 FROM public.kontrollfunn f
                 WHERE f.tenant = v_t
                   AND f.kontroll_id = kand.kontroll_id
                   AND f.funntype = kand.funntype)
         ORDER BY coalesce(kand.dogn_over_frist, 0) DESC,
                  kand.kontroll_id, kand.funntype
         LIMIT v_grense;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        nye := nye + v_n;
        IF v_n = v_grense THEN
            avkortet := avkortet + 1;
        END IF;

        -- 3. Lukkingen. Et funn som ikke lenger gjelder — kontrollen ble
        --    etterprøvd, eieren kom tilbake, kontrollen ble markert
        --    ikke_relevant — lukkes. Raden består: at noe VAR et funn er
        --    også historikk.
        UPDATE public.kontrollfunn f
           SET apen = false, lukket_ts = v_naa
         WHERE f.tenant = v_t AND f.apen
           AND NOT EXISTS (
                SELECT 1 FROM public.m34_funnkandidater(v_t, v_dag) kand
                 WHERE kand.kontroll_id = f.kontroll_id
                   AND kand.funntype = f.funntype);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        lukkede := lukkede + v_n;
    END LOOP;
    -- Konteksten legges tilbake der den sto: en sveip skal ikke etterlate
    -- seg en tenant i sesjonen den ikke ble kalt med.
    PERFORM set_config('disponit.tenant', '', true);
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m34_sveip_etterprovinger(INT) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 5. Rettighetene. Migrasjonen NAVNGIR IKKE runtime-rollen (057-
--    lærdommen): `deploy/staging/migrer.py` er autoritativ for den
--    konfigurerte rollen. Grantene her er de som gjelder lokalt og i
--    test, der runtime ER hele plattformen, og de faller bort i
--    driftsoppsettet.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_compliance_eier;
DO $$
BEGIN
    -- Sveipen: ÉN EXECUTE til sveiperollen. Ingen tabellrettigheter —
    -- rollen har ingen i dag, og M-34 gir den ingen. Funnene skrives av
    -- den eier-eide defineren, aldri av rollen direkte.
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_compliancesveip') THEN
        GRANT EXECUTE ON FUNCTION m34_sveip_etterprovinger(INT)
            TO disponit_compliancesveip;
    END IF;
    -- DØRENE TIL RUNTIME GRANTES IKKE HER (057/089/096-doktrinen):
    -- `disponit` er bare LOKALNAVNET på web-API-rollen, og `migrer.py`
    -- er eneste rettighetskilde for den konfigurerte rollen.
    --
    -- REVOKE-en står likevel, og den er ikke pynt (091/095-formen): en
    -- rettighet som bare slutter å bli gitt er ikke trukket tilbake.
    -- Sveipen er kryss-tenant og setter selv RLS-konteksten — altså
    -- nøyaktig det vinduet sveiperollen finnes for å nekte
    -- forespørselsveien. En kompromittert runtime skal se ÉN tenant om
    -- gangen.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        REVOKE ALL ON FUNCTION m34_sveip_etterprovinger(INT)
            FROM disponit;
    END IF;
END $$;

RESET ROLE;
