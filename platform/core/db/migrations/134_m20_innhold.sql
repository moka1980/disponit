-- =====================================================================
-- M-20 NETTSIDE- OG INNHOLDSAGENT (v1) — KLYNGE 9s ANDRE.
-- =====================================================================
--
-- V1-DOMMEN: MODULEN PUBLISERER INGENTING SELV. Et utkast blir KLART;
-- aktiveringen er et menneskes, og navnet står på raden.
--
-- ---------------------------------------------------------------------
-- KLYNGENS DELTE DOM:
--
--   EN YTRING AVGITT I HUSETS NAVN KAN IKKE TAS TILBAKE — OG DEN SOM
--   LESER DEN VET IKKE AT EN MASKIN SKREV DEN.
--
-- En rollback fjerner siden. Den fjerner ikke at noen leste den og
-- handlet på den. Derfor er ikke rollback en unnskyldning for å
-- publisere lett — den er et krav om at veien TILBAKE finnes FØR veien
-- FRAM tas.
--
-- ---------------------------------------------------------------------
-- HVA DENNE MODULEN ARVER, OG HVA FUNDAMENTET IKKE HADDE SETT.
--
-- Fundamentet slo fast at M-20 arver M-1s utkastform (`policyutkast`,
-- 012) og skrev at «det som er NYTT for M-20 er kildekravet».
--
-- DET ER DET IKKE. `kildedokument` (M-46, migrasjon 118) finnes, med
-- samme doktrine og nesten samme ord:
--
--   «Et utkastpunkt kan bare peke hit. Det er hele mekanismen bak
--    utkast markerer hvert faktapunkt med kilde.»
--
-- Den er frosset, har innholdssum, og eies av migrator med husets
-- vanlige tenantpolicy. DENNE MODULEN ARVER DEN. To kilderegistre
-- ville gitt to svar på «kan vi belegge dette», og det er ett for mye
-- — nøyaktig argumentet fundamentet selv brukte for at M-7 og M-43
-- skal dele ÉN opptakshjemmel.
--
-- Prisen er ærlig og står her: `kildedokument_type_lukket` utvides med
-- de dokumentformene en PRODUKTPÅSTAND hviler på. Et lukket sett som
-- tjener to moduler må romme begges vokabular, ellers tvinger det den
-- ene til å skrive «annet» — og «annet» er ingen kilde.
--
-- ---------------------------------------------------------------------
-- ARVET FORM, MEN IKKE ARVET DISIPLIN.
--
-- M-1s `policyutkast` er «eneste muterbare tilstand»: én rad per
-- utkast, med `utkastversjon` som optimistisk lås. Det er riktig for
-- en policy, som ingen leser før den er aktivert.
--
-- M-20s egen grense navngir `utkast_overskrevet`, og da holder ikke
-- den formen. HER ER HVER VERSJON EN NY RAD (M-46s form), fordi
-- spørsmålet «hva sto i utkastet da mennesket så på det og sa ja» må
-- kunne besvares etter at noen har endret det. En optimistisk lås
-- hindrer at to skriver samtidig. Den bevarer ingenting.
--
-- KOLONNENE ER M-1s, ORD FOR ORD: `basert_pa_versjon`,
-- `basert_pa_hash`, `rollback_av_versjon`, `innholds_hash`, `status`.
--
-- ---------------------------------------------------------------------
-- TRE FUNN SOM ALDRI KAN REISES, OG DET ER BEVISET.
--
--   `paastand_uten_kilde`          — `kilde_id` er NOT NULL med
--                                    fremmednøkkel til `kildedokument`.
--   `publisering_uten_forhaandsvisning`
--                                  — `visning_id` er NOT NULL med
--                                    fremmednøkkel til en visning av
--                                    NØYAKTIG dette utkastet.
--   `publisering_uten_rollbackvei` — `rollbackform` er et lukket sett
--                                    med to verdier, begge en vei.
--
-- Det er 133s form: et nekt som kommer etter er ikke et nekt. En sveip
-- som fant en udokumentert påstand ETTER publisering ville funnet en
-- SKADE, ikke hindret den.
--
-- ---------------------------------------------------------------------
-- GRENSEN MOT M-1.
--
-- M-1 eier POLICYER — regler huset håndhever mot seg selv. M-20 eier
-- INNHOLD — det huset sier til andre. En modul som utvidet
-- `policyutkast` til å bære produktpåstander ville gjort
-- policyforvaltning til markedsføring i stillhet.
-- =====================================================================

-- ---------------------------------------------------------------------
-- KILDEREGISTERET UTVIDES, OG BARE DET.
--
-- INGEN NY TABELL. `kildedokument` (118) er husets kilderegister fra
-- og med nå, og settet av dokumentformer rommer begge modulenes
-- vokabular. De fire nye er former en produktpåstand faktisk hviler
-- på; ingen av dem gjør et anbudssvar dårligere.
-- ---------------------------------------------------------------------
ALTER TABLE kildedokument
    DROP CONSTRAINT IF EXISTS kildedokument_type_lukket;
ALTER TABLE kildedokument
    ADD CONSTRAINT kildedokument_type_lukket CHECK (dokumenttype IN (
        -- M-46s sett (118), uendret.
        'sertifikat', 'attest', 'regnskap', 'referanse',
        'policy', 'cv', 'annet',
        -- M-20s (134). En produktpåstand hviler på en MÅLING, en
        -- TESTRAPPORT, et DATABLAD eller en LEVERANDØRERKLÆRING.
        'testrapport', 'maaling', 'datablad', 'leverandorerklaering'));

-- MODULROLLEN MÅ KUNNE EIE NOE FØR DEN KAN EIE DØRENE.
GRANT USAGE, CREATE ON SCHEMA public TO disponit_innhold_eier;
GRANT INSERT ON revisjonslogg TO disponit_innhold_eier;
-- HUSETS EGEN TENANTVAKT (038). Dørene kaller den i stedet for
-- å skrive sin egen sammenligning: én regel, ett sted.
--
-- GRANTEN GIS AV EIEREN, og eieren er `disponit_m37_claimer` —
-- ikke migrator. 133 gjorde det samme; et REVOKE eller GRANT fra
-- en som ikke eier funksjonen er en FEIL, ikke en stille no-op.
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_innhold_eier;
RESET ROLE;

-- HUSETS KILDEREGISTER (118) LESES OG SKRIVES AV DENNE MODULEN
-- OGSÅ. Tabellen eies av migrator, så det trengs ingen
-- `SET LOCAL ROLE` her — 132 måtte sette rollen tretti ganger
-- fordi den grantet på tvers av tretti eiere.
GRANT SELECT, INSERT ON kildedokument TO disponit_innhold_eier;

-- ---------------------------------------------------------------------
-- `innholdskrav` — TENANTENS GRENSER, IKKE VÅRE.
--
-- INGEN TALL ER LÅST I EN DRIFTSFIL. Hvor lenge et datablad står seg,
-- og hvor lenge en forhåndsvisning er fersk nok til å publisere på, er
-- forskjellig for en nettbutikk og en legemiddelprodusent. En terskel
-- låst her ville vært en påstand om hvor mye det koster å ta feil i en
-- produktpåstand — og de to tåler ikke det samme.
-- ---------------------------------------------------------------------
CREATE TABLE innholdskrav (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kravversjon INT NOT NULL CHECK (kravversjon >= 1),
    -- Hvor lenge et kildedokument uten egen utløpsdato regnes som
    -- gyldig. M-46 har det samme tallet i sin profil, og de to skal
    -- IKKE slås sammen: samme dokument kan være ferskt nok for et
    -- anbudssvar og for gammelt for en påstand på forsiden.
    kilde_gyldig_dogn INT NOT NULL
        CHECK (kilde_gyldig_dogn BETWEEN 1 AND 3650),
    -- Hvor lenge en forhåndsvisning er fersk nok til å publisere på.
    -- ET MENNESKE SOM SÅ NOE FOR TRE UKER SIDEN HAR IKKE SETT DETTE.
    visning_gyldig_min INT NOT NULL
        CHECK (visning_gyldig_min BETWEEN 1 AND 20160),
    -- Hvor mange døgn før en kilde utløper sveipen sier fra.
    varselfrist_dogn INT NOT NULL
        CHECK (varselfrist_dogn BETWEEN 0 AND 365),
    satt TIMESTAMPTZ NOT NULL DEFAULT now(),
    satt_av TEXT NOT NULL CHECK (satt_av ~ '[^[:space:]]'),
    CONSTRAINT innholdskrav_pk PRIMARY KEY (tenant, kravversjon)
);

-- ---------------------------------------------------------------------
-- `innholdsutkast` — M-1s KOLONNER, M-46s DISIPLIN.
--
-- HVER VERSJON ER EN NY RAD. Se filhodet: en optimistisk lås hindrer
-- at to skriver samtidig, men den BEVARER INGENTING, og «hva sto her
-- da mennesket sa ja» er hele spørsmålet denne modulen finnes for.
-- ---------------------------------------------------------------------
CREATE TABLE innholdsutkast (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    utkast_id UUID NOT NULL,
    -- Siden utkastet gjelder. M-1s `policy_id` i form og rolle.
    side_id TEXT NOT NULL CHECK (side_id ~ '^[a-z0-9][a-z0-9_/-]*$'),
    versjon INT NOT NULL CHECK (versjon >= 1),
    -- M-1s tre konfliktkolonner, ord for ord.
    basert_pa_versjon INT CHECK (basert_pa_versjon >= 1),
    basert_pa_hash TEXT CHECK (basert_pa_hash ~ '^[0-9a-f]{64}$'),
    rollback_av_versjon INT CHECK (rollback_av_versjon >= 1),
    innhold JSONB NOT NULL,
    -- Settes ved registrering og FRYSES av radvakten. Det er denne
    -- summen forhåndsvisningen måles mot.
    innholds_hash TEXT NOT NULL CHECK (innholds_hash ~ '^[0-9a-f]{64}$'),
    status TEXT NOT NULL DEFAULT 'utkast'
        CONSTRAINT innholdsutkast_status_lukket
        CHECK (status IN ('utkast', 'klar', 'publisert', 'forkastet')),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT innholdsutkast_pk PRIMARY KEY (tenant, utkast_id),
    CONSTRAINT innholdsutkast_versjon_unik
        UNIQUE (tenant, side_id, versjon),
    -- EN ROLLBACK ER ET UTKAST SOM PEKER BAKOVER, og den kan ikke
    -- samtidig påstå å bygge videre på noe.
    CONSTRAINT innholdsutkast_rollback_bygger_ikke_videre CHECK (
        rollback_av_versjon IS NULL
        OR (basert_pa_versjon IS NULL AND basert_pa_hash IS NULL)),
    -- `basert_pa_versjon` og `basert_pa_hash` følges ad: en peker uten
    -- sum kan ikke oppdage at grunnlaget er endret, og en sum uten
    -- peker vet ikke hva den er summen av.
    CONSTRAINT innholdsutkast_grunnlaget_er_helt CHECK (
        (basert_pa_versjon IS NULL) = (basert_pa_hash IS NULL))
);
CREATE INDEX innholdsutkast_side
    ON innholdsutkast (tenant, side_id, versjon DESC);

-- ---------------------------------------------------------------------
-- `innholdspaastand` — HELE MODULEN, I ÉN FREMMEDNØKKEL.
--
-- `kilde_id` er NOT NULL med fremmednøkkel til `kildedokument`. EN
-- PÅSTAND UTEN KILDE ER UREPRESENTERBAR — ikke oppdaget, ikke varslet,
-- ikke rapportert i en nattlig sveip. Den finnes ikke.
-- ---------------------------------------------------------------------
CREATE TABLE innholdspaastand (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    paastand_id UUID NOT NULL,
    utkast_id UUID NOT NULL,
    rekkefolge INT NOT NULL CHECK (rekkefolge >= 1),
    tekst TEXT NOT NULL CHECK (length(btrim(tekst)) > 0),
    kilde_id UUID NOT NULL,
    -- SUMMEN AV KILDEN SLIK DEN VAR DA PÅSTANDEN BLE SKREVET. Uten
    -- den kan ingen etterpå vise at det var NØYAKTIG denne versjonen
    -- av testrapporten som ble sitert. 118s ord, og de gjelder her.
    kilde_sha256 TEXT NOT NULL CHECK (kilde_sha256 ~ '^[0-9a-f]{64}$'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT innholdspaastand_pk PRIMARY KEY (tenant, paastand_id),
    CONSTRAINT innholdspaastand_utkast_fk
        FOREIGN KEY (tenant, utkast_id)
        REFERENCES innholdsutkast (tenant, utkast_id),
    CONSTRAINT innholdspaastand_kilde_fk
        FOREIGN KEY (tenant, kilde_id)
        REFERENCES kildedokument (tenant, kilde_id),
    CONSTRAINT innholdspaastand_nummer_unikt
        UNIQUE (tenant, utkast_id, rekkefolge)
);
CREATE INDEX innholdspaastand_utkast
    ON innholdspaastand (tenant, utkast_id, rekkefolge);
CREATE INDEX innholdspaastand_kilde
    ON innholdspaastand (tenant, kilde_id);

-- ---------------------------------------------------------------------
-- `innholdsvisning` — HVA MENNESKET FAKTISK SÅ.
--
-- Ikke «at det ble forhåndsvist», men HVA. Summen står på raden, og
-- publiseringsdøra krever at den er lik utkastets. Et menneske som
-- godkjente noe annet enn det som publiseres, HAR IKKE GODKJENT DET —
-- og forskjellen mellom de to er usynlig uten denne kolonnen.
-- ---------------------------------------------------------------------
CREATE TABLE innholdsvisning (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    visning_id UUID NOT NULL,
    utkast_id UUID NOT NULL,
    -- SUMMEN AV DET SOM BLE VIST. Kopieres fra utkastet ved
    -- registrering og fryses.
    vist_hash TEXT NOT NULL CHECK (vist_hash ~ '^[0-9a-f]{64}$'),
    vist_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    vist_for TEXT NOT NULL CHECK (vist_for ~ '[^[:space:]]'),
    CONSTRAINT innholdsvisning_pk PRIMARY KEY (tenant, visning_id),
    CONSTRAINT innholdsvisning_utkast_fk
        FOREIGN KEY (tenant, utkast_id)
        REFERENCES innholdsutkast (tenant, utkast_id)
);
CREATE INDEX innholdsvisning_utkast
    ON innholdsvisning (tenant, utkast_id, vist_ts DESC);

-- ---------------------------------------------------------------------
-- `innholdspublisering` — DEN ENESTE HANDLINGEN SOM NÅR ET PUBLIKUM.
--
-- `publisert_av` er NOT NULL, og det er V1-DOMMEN: en publisering uten
-- et navn bak er en publisering MODULEN gjorde.
--
-- `visning_id` er NOT NULL med fremmednøkkel: det finnes ingen vei
-- forbi forhåndsvisningen, heller ikke en som logger at man gikk forbi.
--
-- `rollbackform` er et LUKKET SETT MED TO VERDIER, og begge er en vei
-- tilbake. Den første publiseringen av en side har ingen forrige
-- versjon å falle tilbake til — da er veien AVPUBLISERING, og den er
-- fortsatt en vei. «Ingen vei tilbake» er ikke en verdi i settet.
-- ---------------------------------------------------------------------
CREATE TABLE innholdspublisering (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    publisering_id UUID NOT NULL,
    utkast_id UUID NOT NULL,
    side_id TEXT NOT NULL CHECK (side_id ~ '^[a-z0-9][a-z0-9_/-]*$'),
    versjon INT NOT NULL CHECK (versjon >= 1),
    visning_id UUID NOT NULL,
    publisert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    publisert_av TEXT NOT NULL CHECK (publisert_av ~ '[^[:space:]]'),
    rollbackform TEXT NOT NULL
        CONSTRAINT innholdspublisering_rollbackform_lukket
        CHECK (rollbackform IN ('forrige_versjon', 'avpublisering')),
    rollback_til_versjon INT CHECK (rollback_til_versjon >= 1),
    -- Avpublisering ELLER tilbakerulling. Begge er terminale for
    -- raden, og begge bærer et navn.
    tilbake_ts TIMESTAMPTZ,
    tilbake_av TEXT CHECK (tilbake_av ~ '[^[:space:]]'),
    CONSTRAINT innholdspublisering_pk PRIMARY KEY (tenant, publisering_id),
    CONSTRAINT innholdspublisering_utkast_fk
        FOREIGN KEY (tenant, utkast_id)
        REFERENCES innholdsutkast (tenant, utkast_id),
    CONSTRAINT innholdspublisering_visning_fk
        FOREIGN KEY (tenant, visning_id)
        REFERENCES innholdsvisning (tenant, visning_id),
    -- FORMEN OG PEKEREN FØLGES AD. `forrige_versjon` uten et nummer å
    -- gå tilbake til er «ingen vei» skrevet med et pent ord.
    CONSTRAINT innholdspublisering_veien_er_hel CHECK (
        (rollbackform = 'forrige_versjon') = (rollback_til_versjon IS NOT NULL)),
    CONSTRAINT innholdspublisering_tilbake_er_helt CHECK (
        (tilbake_ts IS NULL) = (tilbake_av IS NULL))
    -- INGEN UNIKHET PÅ `utkast_id`, OG DET ER EN DOM.
    --
    -- Første utkast hadde `UNIQUE (tenant, utkast_id)`, og den gjorde
    -- GJENOPPRETTING UREPRESENTERBAR: å rulle tilbake til forrige
    -- versjon er å publisere det samme utkastet på nytt, og det er
    -- nøyaktig veien modulen krever at finnes.
    --
    -- Hver PERIODE en versjon var levende er sin egen rad, med sitt
    -- eget navn og sine egne to tidspunkter. En rad som ble «levende
    -- igjen» ved at tilbakerullingen ble visket ut, ville ikke kunnet
    -- svare på hvor lenge siden faktisk sto ute — og det er det
    -- spørsmålet noen stiller etterpå.
);
CREATE INDEX innholdspublisering_side
    ON innholdspublisering (tenant, side_id, versjon DESC);
CREATE INDEX innholdspublisering_levende
    ON innholdspublisering (tenant, side_id)
    WHERE tilbake_ts IS NULL;

-- ---------------------------------------------------------------------
-- `innholdsfunn` — HUSETS FORM (`apen_kolonne`).
-- ---------------------------------------------------------------------
CREATE TABLE innholdsfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    funn_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CONSTRAINT innholdsfunn_type_lukket CHECK (funntype IN (
            -- SVEIPENS EGNE. Lukkes av at tilstanden opphører.
            'publisert_paastand_uten_gyldig_kilde',
            'klart_utkast_uten_forhaandsvisning',
            -- ET MENNESKE KAN LUKKE DENNE, med et navn på.
            'kilde_utloper_snart_uavklart',
            -- DE TRE SOM ALDRI KAN REISES. Se filhodet: at de står i
            -- settet OG er umulige er hele beviset.
            'paastand_uten_kilde',
            'publisering_uten_forhaandsvisning',
            'publisering_uten_rollbackvei')),
    referanse UUID NOT NULL,
    detaljer TEXT NOT NULL CHECK (length(btrim(detaljer)) > 0),
    over_grense BIGINT,
    apen BOOLEAN NOT NULL DEFAULT true,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT CHECK (lukket_av ~ '[^[:space:]]'),
    lukket_grunn TEXT,
    CONSTRAINT innholdsfunn_pk PRIMARY KEY (tenant, funn_id),
    CONSTRAINT innholdsfunn_unikt UNIQUE (tenant, funntype, referanse),
    -- 125/126s VAKT: en lukking uten navn er urepresenterbar.
    CONSTRAINT innholdsfunn_lukking_har_navn CHECK (
        apen OR (lukket_ts IS NOT NULL AND lukket_av IS NOT NULL))
);
CREATE INDEX innholdsfunn_apne
    ON innholdsfunn (tenant, funntype) WHERE apen;

-- =====================================================================
-- RADVAKTENE. APPEND-ONLY ER EN TRIGGER, IKKE EN VANE.
--
-- `utkast_overskrevet` er en invariant i grensen, og en REVOKE alene
-- måler den ikke: migrator beholder rettighetene sine, og en fremtidig
-- migrasjon som «rydder» ville kunnet skrive over historikken uten at
-- noe sa fra. Vakten står på TABELLEN og gjelder alle.
--
-- STATUSKOLONNEN ER DET ENESTE SOM KAN ENDRES i utkastet, og bare
-- FRAMOVER: `utkast` → `klar` → `publisert`, eller til `forkastet`.
-- Alt annet — innholdet, summen, versjonen, navnet — er frosset.
-- =====================================================================
CREATE FUNCTION m20_utkastvakt() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'innholdsutkast er append-only: et utkast som'
            ' kan slettes kan ikke svare paa hva som sto i det';
    END IF;
    IF NEW.utkast_id <> OLD.utkast_id
       OR NEW.side_id <> OLD.side_id
       OR NEW.versjon <> OLD.versjon
       OR NEW.innhold::TEXT <> OLD.innhold::TEXT
       OR NEW.innholds_hash <> OLD.innholds_hash
       OR NEW.opprettet_av <> OLD.opprettet_av
       OR NEW.opprettet <> OLD.opprettet
       OR NEW.basert_pa_versjon IS DISTINCT FROM OLD.basert_pa_versjon
       OR NEW.basert_pa_hash IS DISTINCT FROM OLD.basert_pa_hash
       OR NEW.rollback_av_versjon IS DISTINCT FROM OLD.rollback_av_versjon
    THEN
        RAISE EXCEPTION 'innholdsutkast: bare status kan endres — en'
            ' rettelse er en NY versjon, ikke en overskriving';
    END IF;
    -- STATUS GÅR ÉN VEI. En publisert side som «gaar tilbake til
    -- utkast» ville vaert en side som aldri hadde vaert publisert, og
    -- den finnes ikke: noen leste den.
    IF OLD.status IN ('publisert', 'forkastet')
       AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'innholdsutkast: % er terminal', OLD.status;
    END IF;
    IF OLD.status = 'klar' AND NEW.status = 'utkast' THEN
        RAISE EXCEPTION 'innholdsutkast: klar gaar ikke tilbake til'
            ' utkast — lag en ny versjon';
    END IF;
    RETURN NEW;
END $$;
-- RIVES FRA PUBLIC HER, SOM MIGRATOR. Loekka nederst i filen
-- loeper som modulrollen, og et REVOKE fra en som ikke eier
-- funksjonen er en FEIL — ikke en stille no-op. Derfor staar
-- migrators egne vakter her, og loekka spoer om eierskap.
REVOKE ALL ON FUNCTION m20_utkastvakt() FROM PUBLIC;
CREATE TRIGGER m20_utkastvakt
    BEFORE UPDATE OR DELETE ON innholdsutkast
    FOR EACH ROW EXECUTE FUNCTION m20_utkastvakt();

-- PÅSTANDEN, VISNINGEN OG KILDESUMMEN ER HELT FROSNE.
--
-- En påstand som kunne endres etter at kilden ble registrert, ville
-- vært en påstand som byttet kilde uten å si fra — og da måler
-- fremmednøkkelen ingenting.
CREATE FUNCTION m20_frossenvakt() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    RAISE EXCEPTION '%: raden er frossen — % er ikke tillatt',
        TG_TABLE_NAME, TG_OP;
END $$;
-- RIVES FRA PUBLIC HER, SOM MIGRATOR. Loekka nederst i filen
-- loeper som modulrollen, og et REVOKE fra en som ikke eier
-- funksjonen er en FEIL — ikke en stille no-op. Derfor staar
-- migrators egne vakter her, og loekka spoer om eierskap.
REVOKE ALL ON FUNCTION m20_frossenvakt() FROM PUBLIC;
CREATE TRIGGER m20_paastandsvakt
    BEFORE UPDATE OR DELETE ON innholdspaastand
    FOR EACH ROW EXECUTE FUNCTION m20_frossenvakt();
CREATE TRIGGER m20_visningsvakt
    BEFORE UPDATE OR DELETE ON innholdsvisning
    FOR EACH ROW EXECUTE FUNCTION m20_frossenvakt();

-- PUBLISERINGEN: bare tilbakerullingen kan skrives, og bare én gang.
CREATE FUNCTION m20_publiseringsvakt() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'innholdspublisering er append-only: en'
            ' publisering som kan slettes kan ikke rulles tilbake';
    END IF;
    IF NEW.publisering_id <> OLD.publisering_id
       OR NEW.utkast_id <> OLD.utkast_id
       OR NEW.side_id <> OLD.side_id
       OR NEW.versjon <> OLD.versjon
       OR NEW.visning_id <> OLD.visning_id
       OR NEW.publisert_av <> OLD.publisert_av
       OR NEW.publisert_ts <> OLD.publisert_ts
       OR NEW.rollbackform <> OLD.rollbackform
       OR NEW.rollback_til_versjon IS DISTINCT FROM OLD.rollback_til_versjon
    THEN
        RAISE EXCEPTION 'innholdspublisering: bare tilbakerullingen'
            ' kan skrives i ettertid';
    END IF;
    IF OLD.tilbake_ts IS NOT NULL THEN
        RAISE EXCEPTION 'innholdspublisering: allerede rullet tilbake'
            ' % — en gang er en gang', OLD.tilbake_ts;
    END IF;
    RETURN NEW;
END $$;
-- RIVES FRA PUBLIC HER, SOM MIGRATOR. Loekka nederst i filen
-- loeper som modulrollen, og et REVOKE fra en som ikke eier
-- funksjonen er en FEIL — ikke en stille no-op. Derfor staar
-- migrators egne vakter her, og loekka spoer om eierskap.
REVOKE ALL ON FUNCTION m20_publiseringsvakt() FROM PUBLIC;
CREATE TRIGGER m20_publiseringsvakt
    BEFORE UPDATE OR DELETE ON innholdspublisering
    FOR EACH ROW EXECUTE FUNCTION m20_publiseringsvakt();

-- FUNNET: bare lukkingen og `sist_sett` kan skrives (133s form).
CREATE FUNCTION m20_funnvakt() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'innholdsfunn er append-only';
    END IF;
    IF NEW.funntype <> OLD.funntype OR NEW.referanse <> OLD.referanse
       OR NEW.forst_sett <> OLD.forst_sett THEN
        RAISE EXCEPTION 'innholdsfunn: funntype, referanse og'
            ' forst_sett er frosne';
    END IF;
    RETURN NEW;
END $$;
-- RIVES FRA PUBLIC HER, SOM MIGRATOR. Loekka nederst i filen
-- loeper som modulrollen, og et REVOKE fra en som ikke eier
-- funksjonen er en FEIL — ikke en stille no-op. Derfor staar
-- migrators egne vakter her, og loekka spoer om eierskap.
REVOKE ALL ON FUNCTION m20_funnvakt() FROM PUBLIC;
CREATE TRIGGER m20_funnvakt
    BEFORE UPDATE OR DELETE ON innholdsfunn
    FOR EACH ROW EXECUTE FUNCTION m20_funnvakt();

-- =====================================================================
-- RADVAKT OG RETTIGHETER. FORCE RLS PÅ ALLE SEKS.
--
-- `tenantlekkasje_i_innholdsregister` er en invariant i grensen, og
-- FORCE er forskjellen: uten den ser eieren av tabellen forbi sin egen
-- policy, og en SECURITY DEFINER-dør som eide tabellen ville lest alle
-- tenanter uten å vite det.
-- =====================================================================
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['innholdskrav', 'innholdsutkast',
                             'innholdspaastand', 'innholdsvisning',
                             'innholdspublisering', 'innholdsfunn']
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format($f$CREATE POLICY tenant_isolasjon ON public.%I
            USING (tenant = current_setting('disponit.tenant', true))
            WITH CHECK (tenant = current_setting('disponit.tenant', true))$f$, t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO'
                       ' disponit_innhold_eier', t);
    END LOOP;
END $$;

-- SVEIPENS KRYSS-TENANT-POLICY (130s LÆRDOM).
--
-- En sveip uten `disponit.tenant` ville sett NULL RADER under FORCE
-- RLS og rapportert null funn — MED GRØNN EXIT-KODE. Den ser ut som en
-- vellykket kjøring. Sveipen løkker derfor én tenant om gangen, og
-- DENNE policyen er den eneste veien til tenantlisten.
CREATE POLICY m20_sveip_tenantliste ON innholdskrav
    FOR SELECT
    USING (current_setting('disponit.tenant', true) IS NULL
           OR current_setting('disponit.tenant', true) = '');

-- =====================================================================
-- HERFRA EIES DØRENE AV INNHOLDSEIEREN.
--
-- SP-7: kjøretiden får EXECUTE på dørene og INGEN tabellrettigheter.
-- =====================================================================
SET LOCAL ROLE disponit_innhold_eier;

-- ---------------------------------------------------------------------
-- `m20_kilde_gyldig` — ÉN REGEL, ETT STED.
--
-- Dokumentets egen utløpsdato vinner når den finnes. Har det ingen,
-- gjelder tenantens vindu regnet fra registreringen. To steder som
-- regnet dette ut ville kunnet gi to svar på om en påstand var belagt.
-- ---------------------------------------------------------------------
CREATE FUNCTION m20_kilde_gyldig(p_gyldig_til DATE,
                                 p_registrert TIMESTAMPTZ,
                                 p_vindu_dogn INT,
                                 p_paa DATE)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN p_gyldig_til IS NOT NULL THEN p_paa <= p_gyldig_til
        ELSE p_paa <= (p_registrert AT TIME ZONE 'UTC')::DATE + p_vindu_dogn
    END
$$;

-- ---------------------------------------------------------------------
-- `m20_funn_er_sveipens` — HVEM SOM KAN LUKKE HVA.
--
-- To av funnene lukkes BARE av at tilstanden opphører: kilden blir
-- gyldig igjen, eller siden avpubliseres. `kilde_utloper_snart_uavklart`
-- KAN lukkes av et menneske — «vi har sjekket, dokumentet står seg» er
-- en legitim avklaring med et navn på.
--
-- DE TRE UMULIGE STÅR HER SOM SVEIPENS, og det er riktig: hvis en av
-- dem noen gang dukket opp, er det ingen et menneske skal kunne huke
-- bort.
-- ---------------------------------------------------------------------
CREATE FUNCTION m20_funn_er_sveipens(p_funntype TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE AS $$
    SELECT p_funntype <> 'kilde_utloper_snart_uavklart'
$$;

-- ---------------------------------------------------------------------
-- `m20_evidens` — HUSETS SPOR.
--
-- Hver skrivedør legger igjen en linje i `revisjonslogg`. Uten den kan
-- «hvem publiserte dette, og når» besvares av modulens egne tabeller —
-- men «hvem PRØVDE» kan bare besvares av huset.
-- ---------------------------------------------------------------------
CREATE FUNCTION m20_evidens(p_tenant TEXT, p_ref UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm20_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm20_innhold', 'handling', p_handling,
        'ref', p_ref::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm20_innhold',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:innhold', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;

-- ---------------------------------------------------------------------
-- `m20_sett_krav` — TENANTENS GRENSER. NY VERSJON, ALDRI OVERSKRIVING.
-- ---------------------------------------------------------------------
CREATE FUNCTION m20_sett_krav(p_tenant TEXT, p_kilde_gyldig_dogn INT,
                              p_visning_gyldig_min INT,
                              p_varselfrist_dogn INT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm20_sett_krav');
    SELECT coalesce(max(kravversjon), 0) + 1 INTO v_versjon
      FROM public.innholdskrav WHERE tenant = p_tenant;
    INSERT INTO public.innholdskrav
        (tenant, kravversjon, kilde_gyldig_dogn, visning_gyldig_min,
         varselfrist_dogn, satt_av)
    VALUES (p_tenant, v_versjon, p_kilde_gyldig_dogn,
            p_visning_gyldig_min, p_varselfrist_dogn, p_aktor);
    PERFORM public.m20_evidens(p_tenant, NULL, 'sett_krav', p_aktor,
        jsonb_build_object('versjon', v_versjon));
    RETURN v_versjon;
END $$;

-- ---------------------------------------------------------------------
-- `m20_registrer_kilde` — SKRIVER I HUSETS KILDEREGISTER (118).
--
-- INGEN EGEN TABELL, og døra er M-20s egen fordi en tenant som bruker
-- innholdsmodulen uten anbudsmodulen fortsatt må kunne registrere det
-- en påstand hviler på.
--
-- `kildedokument_sum_unik` gjør at det SAMME dokumentet registreres én
-- gang uansett hvilken av de to dørene som skriver det. Det er hele
-- poenget med å dele registeret.
-- ---------------------------------------------------------------------
CREATE FUNCTION m20_registrer_kilde(p_tenant TEXT, p_kilde_id UUID,
                                    p_tittel TEXT, p_dokumenttype TEXT,
                                    p_gyldig_til DATE,
                                    p_innhold_sha256 TEXT, p_aktor TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_finnes UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm20_registrer_kilde');
    -- SAMME SUM ER SAMME DOKUMENT. Svar med raden som finnes i stedet
    -- for å reise en unikhetsfeil kalleren ikke kan gjøre noe med.
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
    PERFORM public.m20_evidens(p_tenant, p_kilde_id,
        'registrer_kilde', p_aktor,
        jsonb_build_object('type', p_dokumenttype));
    RETURN p_kilde_id;
END $$;

-- ---------------------------------------------------------------------
-- `m20_registrer_utkast` — EN NY VERSJON, ALDRI EN ENDRING.
--
-- Summen regnes HER og fryses av vakten. En sum kalleren oppga ville
-- vært en påstand om innholdet, ikke en måling av det.
-- ---------------------------------------------------------------------
CREATE FUNCTION m20_registrer_utkast(p_tenant TEXT, p_utkast_id UUID,
                                     p_side_id TEXT, p_innhold JSONB,
                                     p_basert_pa_versjon INT,
                                     p_rollback_av_versjon INT,
                                     p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_versjon INT;
    v_hash TEXT;
    v_basert_hash TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm20_registrer_utkast');
    IF p_basert_pa_versjon IS NOT NULL AND p_rollback_av_versjon IS NOT NULL THEN
        RAISE EXCEPTION 'm20_registrer_utkast: en rollback bygger ikke'
            ' videre paa noe — oppgi enten grunnlaget eller rollbacken';
    END IF;
    -- Grunnlagets sum HENTES, den oppgis ikke. Da kan
    -- `basert_pa_hash` faktisk oppdage at grunnlaget er endret.
    IF p_basert_pa_versjon IS NOT NULL THEN
        SELECT innholds_hash INTO v_basert_hash
          FROM public.innholdsutkast
         WHERE tenant = p_tenant AND side_id = p_side_id
           AND versjon = p_basert_pa_versjon;
        IF v_basert_hash IS NULL THEN
            RAISE EXCEPTION 'm20_registrer_utkast: versjon % av % finnes'
                ' ikke', p_basert_pa_versjon, p_side_id;
        END IF;
    END IF;
    IF p_rollback_av_versjon IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM public.innholdsutkast
                        WHERE tenant = p_tenant AND side_id = p_side_id
                          AND versjon = p_rollback_av_versjon) THEN
        RAISE EXCEPTION 'm20_registrer_utkast: kan ikke rulle tilbake'
            ' versjon % av % — den finnes ikke',
            p_rollback_av_versjon, p_side_id;
    END IF;
    SELECT coalesce(max(versjon), 0) + 1 INTO v_versjon
      FROM public.innholdsutkast
     WHERE tenant = p_tenant AND side_id = p_side_id;
    -- `jsonb` normaliserer nøkkelrekkefølgen, så to like innhold gir
    -- samme tekst og samme sum uansett hvordan de ble skrevet.
    v_hash := encode(sha256(convert_to(p_innhold::TEXT, 'UTF8')), 'hex');
    INSERT INTO public.innholdsutkast
        (tenant, utkast_id, side_id, versjon, basert_pa_versjon,
         basert_pa_hash, rollback_av_versjon, innhold, innholds_hash,
         opprettet_av)
    VALUES (p_tenant, p_utkast_id, p_side_id, v_versjon,
            p_basert_pa_versjon, v_basert_hash, p_rollback_av_versjon,
            p_innhold, v_hash, p_aktor);
    PERFORM public.m20_evidens(p_tenant, p_utkast_id,
        'registrer_utkast', p_aktor,
        jsonb_build_object('side', p_side_id, 'versjon', v_versjon));
    RETURN v_versjon;
END $$;

-- ---------------------------------------------------------------------
-- `m20_registrer_paastand` — DØRA SOM IKKE KAN ÅPNES UTEN KILDE.
--
-- `p_kilde_id` er en NOT NULL-parameter mot en NOT NULL-kolonne med
-- fremmednøkkel. Det finnes ingen kallform som lager en påstand uten
-- kilde, og derfor heller ingen sveip som kan finne en.
--
-- KILDESUMMEN KOPIERES INN. Dokumentet er frosset i 118, men det som
-- betyr noe her er at påstanden BÆRER hvilken sum den ble skrevet mot
-- — da kan flaten vise det uten et oppslag til, og et bytte av kilde
-- er synlig som en forskjell og ikke som et fravær.
-- ---------------------------------------------------------------------
CREATE FUNCTION m20_registrer_paastand(p_tenant TEXT, p_paastand_id UUID,
                                       p_utkast_id UUID, p_rekkefolge INT,
                                       p_tekst TEXT, p_kilde_id UUID,
                                       p_aktor TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_status TEXT;
    v_kilde RECORD;
    v_krav RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm20_registrer_paastand');
    SELECT status INTO v_status FROM public.innholdsutkast
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id;
    IF v_status IS NULL THEN
        RAISE EXCEPTION 'm20_registrer_paastand: ukjent utkast %',
            p_utkast_id;
    END IF;
    IF v_status <> 'utkast' THEN
        RAISE EXCEPTION 'm20_registrer_paastand: utkastet er % — en'
            ' paastand lagt til etter at noen saa paa det ville ikke'
            ' vaert sett', v_status;
    END IF;
    SELECT * INTO v_kilde FROM public.kildedokument
     WHERE tenant = p_tenant AND kilde_id = p_kilde_id;
    IF v_kilde IS NULL THEN
        RAISE EXCEPTION 'm20_registrer_paastand: ukjent kildedokument %',
            p_kilde_id;
    END IF;
    SELECT * INTO v_krav FROM public.innholdskrav
     WHERE tenant = p_tenant ORDER BY kravversjon DESC LIMIT 1;
    IF v_krav IS NULL THEN
        RAISE EXCEPTION 'm20_registrer_paastand: tenanten har ingen'
            ' innholdskrav — grensene settes foer det skrives';
    END IF;
    -- EN UTLØPT KILDE ER INGEN KILDE. Nektet kommer FØR raden finnes,
    -- ikke i en sveip natten etter.
    IF NOT public.m20_kilde_gyldig(v_kilde.gyldig_til, v_kilde.registrert,
                                   v_krav.kilde_gyldig_dogn,
                                   current_date) THEN
        RAISE EXCEPTION 'm20_registrer_paastand: kilden % er utloept —'
            ' en paastand kan ikke hvile paa den', v_kilde.tittel;
    END IF;
    INSERT INTO public.innholdspaastand
        (tenant, paastand_id, utkast_id, rekkefolge, tekst, kilde_id,
         kilde_sha256, registrert_av)
    VALUES (p_tenant, p_paastand_id, p_utkast_id, p_rekkefolge, p_tekst,
            p_kilde_id, v_kilde.innhold_sha256, p_aktor);
    PERFORM public.m20_evidens(p_tenant, p_paastand_id,
        'registrer_paastand', p_aktor,
        jsonb_build_object('kilde', p_kilde_id));
    RETURN p_paastand_id;
END $$;

-- ---------------------------------------------------------------------
-- `m20_registrer_visning` — HVA SOM BLE VIST, IKKE AT DET BLE VIST.
--
-- Summen KOPIERES FRA UTKASTET. En sum kalleren oppga ville vært en
-- påstand om hva som ble vist, og hele mekanismen hviler på at den er
-- en måling.
-- ---------------------------------------------------------------------
CREATE FUNCTION m20_registrer_visning(p_tenant TEXT, p_visning_id UUID,
                                      p_utkast_id UUID, p_vist_for TEXT,
                                      p_aktor TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_utkast RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm20_registrer_visning');
    SELECT * INTO v_utkast FROM public.innholdsutkast
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id;
    IF v_utkast IS NULL THEN
        RAISE EXCEPTION 'm20_registrer_visning: ukjent utkast %',
            p_utkast_id;
    END IF;
    IF v_utkast.status NOT IN ('utkast', 'klar') THEN
        RAISE EXCEPTION 'm20_registrer_visning: utkastet er %',
            v_utkast.status;
    END IF;
    INSERT INTO public.innholdsvisning
        (tenant, visning_id, utkast_id, vist_hash, vist_for)
    VALUES (p_tenant, p_visning_id, p_utkast_id, v_utkast.innholds_hash,
            p_vist_for);
    PERFORM public.m20_evidens(p_tenant, p_visning_id,
        'registrer_visning', p_aktor,
        jsonb_build_object('vist_for', p_vist_for));
    RETURN p_visning_id;
END $$;

-- ---------------------------------------------------------------------
-- `m20_merk_klar` — MODULEN SIER AT DEN ER FERDIG. IKKE MER.
--
-- 118s form (`m46_merk_klart`), og den samme grensen: `klar` er en
-- tilstand HOS OSS. Hva som skjer videre er et menneskes.
--
-- DØRA NEKTER SÅ LENGE ÉN PÅSTAND HVILER PÅ EN UTLØPT KILDE. Et
-- utkast som ble klart med et utløpt datablad ville vært klart til å
-- publisere en udokumentert påstand.
-- ---------------------------------------------------------------------
CREATE FUNCTION m20_merk_klar(p_tenant TEXT, p_utkast_id UUID, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_status TEXT;
    v_krav RECORD;
    v_utlopte INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm20_merk_klar');
    SELECT status INTO v_status FROM public.innholdsutkast
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id FOR UPDATE;
    IF v_status IS NULL THEN
        RAISE EXCEPTION 'm20_merk_klar: ukjent utkast %', p_utkast_id;
    END IF;
    IF v_status = 'klar' THEN
        RETURN 0;
    END IF;
    IF v_status <> 'utkast' THEN
        RAISE EXCEPTION 'm20_merk_klar: utkastet er %', v_status;
    END IF;
    SELECT * INTO v_krav FROM public.innholdskrav
     WHERE tenant = p_tenant ORDER BY kravversjon DESC LIMIT 1;
    SELECT count(*) INTO v_utlopte
      FROM public.innholdspaastand p
      JOIN public.kildedokument d
        ON d.tenant = p.tenant AND d.kilde_id = p.kilde_id
     WHERE p.tenant = p_tenant AND p.utkast_id = p_utkast_id
       AND NOT public.m20_kilde_gyldig(d.gyldig_til, d.registrert,
                                       v_krav.kilde_gyldig_dogn,
                                       current_date);
    IF v_utlopte > 0 THEN
        RAISE EXCEPTION 'm20_merk_klar: % paastand(er) hviler paa en'
            ' utloept kilde', v_utlopte;
    END IF;
    UPDATE public.innholdsutkast u SET status = 'klar'
     WHERE u.tenant = p_tenant AND u.utkast_id = p_utkast_id;
    PERFORM public.m20_evidens(p_tenant, p_utkast_id, 'merk_klar',
        p_aktor, '{}'::jsonb);
    RETURN 1;
END $$;

-- ---------------------------------------------------------------------
-- `m20_publiser` — DEN ENESTE HANDLINGEN SOM NÅR ET PUBLIKUM.
--
-- FEM NEKT, ALLE FØR RADEN FINNES:
--
--   1. Utkastet er ikke `klar`.
--   2. Forhåndsvisningen gjelder et annet utkast.
--   3. UTKASTET ER ENDRET SIDEN VISNINGEN — summene spriker. Et
--      menneske som godkjente noe annet enn det som publiseres har
--      ikke godkjent det, og forskjellen er usynlig uten summen.
--   4. Visningen er for gammel etter tenantens eget vindu.
--   5. En påstand hviler på en kilde som har utløpt siden `klar`.
--
-- OG `publisert_av` ER NOT NULL. Det er V1-dommen: modulen publiserer
-- ingenting selv, og en publisering uten et navn bak er en publisering
-- modulen gjorde.
--
-- ROLLBACKVEIEN REGNES UT HER OG FRYSES. Ikke fordi det er praktisk,
-- men fordi den må finnes FØR veien fram tas: en rollback som skulle
-- vært funnet ut av etterpå er ingen rollback, det er et håp.
-- ---------------------------------------------------------------------
CREATE FUNCTION m20_publiser(p_tenant TEXT, p_publisering_id UUID,
                             p_utkast_id UUID, p_visning_id UUID,
                             p_publisert_av TEXT, p_aktor TEXT)
RETURNS TABLE (publisering_id UUID, rollbackform TEXT,
               rollback_til_versjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_utkast RECORD;
    v_visning RECORD;
    v_krav RECORD;
    v_levende RECORD;
    v_form TEXT;
    v_til INT;
    v_utlopte INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm20_publiser');
    IF p_publisert_av IS NULL OR btrim(p_publisert_av) = '' THEN
        RAISE EXCEPTION 'm20_publiser: publisert_av mangler — en'
            ' publisering uten et navn bak er modulens egen';
    END IF;
    SELECT * INTO v_utkast FROM public.innholdsutkast
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id FOR UPDATE;
    IF v_utkast IS NULL THEN
        RAISE EXCEPTION 'm20_publiser: ukjent utkast %', p_utkast_id;
    END IF;
    -- NEKT 1.
    IF v_utkast.status <> 'klar' THEN
        RAISE EXCEPTION 'm20_publiser: utkastet er % og ikke klar',
            v_utkast.status;
    END IF;
    SELECT * INTO v_visning FROM public.innholdsvisning
     WHERE tenant = p_tenant AND visning_id = p_visning_id;
    IF v_visning IS NULL THEN
        RAISE EXCEPTION 'm20_publiser: ukjent forhaandsvisning %',
            p_visning_id;
    END IF;
    -- NEKT 2.
    IF v_visning.utkast_id <> p_utkast_id THEN
        RAISE EXCEPTION 'm20_publiser: forhaandsvisningen gjelder et'
            ' annet utkast';
    END IF;
    -- NEKT 3. DEN VIKTIGSTE.
    IF v_visning.vist_hash <> v_utkast.innholds_hash THEN
        RAISE EXCEPTION 'm20_publiser: utkastet er endret siden'
            ' forhaandsvisningen — det som ble sett er ikke det som'
            ' ville blitt publisert';
    END IF;
    SELECT * INTO v_krav FROM public.innholdskrav
     WHERE tenant = p_tenant ORDER BY kravversjon DESC LIMIT 1;
    IF v_krav IS NULL THEN
        RAISE EXCEPTION 'm20_publiser: tenanten har ingen innholdskrav';
    END IF;
    -- NEKT 4.
    IF v_visning.vist_ts < now() - make_interval(mins => v_krav.visning_gyldig_min) THEN
        RAISE EXCEPTION 'm20_publiser: forhaandsvisningen er eldre enn'
            ' % minutter — et menneske som saa dette da har ikke sett'
            ' dette naa', v_krav.visning_gyldig_min;
    END IF;
    -- NEKT 5.
    SELECT count(*) INTO v_utlopte
      FROM public.innholdspaastand p
      JOIN public.kildedokument d
        ON d.tenant = p.tenant AND d.kilde_id = p.kilde_id
     WHERE p.tenant = p_tenant AND p.utkast_id = p_utkast_id
       AND NOT public.m20_kilde_gyldig(d.gyldig_til, d.registrert,
                                       v_krav.kilde_gyldig_dogn,
                                       current_date);
    IF v_utlopte > 0 THEN
        RAISE EXCEPTION 'm20_publiser: % paastand(er) hviler paa en'
            ' utloept kilde', v_utlopte;
    END IF;
    -- ROLLBACKVEIEN. Den levende publiseringen av samme side er det
    -- vi faller tilbake TIL. Finnes ingen, er veien avpublisering —
    -- og det er fortsatt en vei.
    SELECT * INTO v_levende FROM public.innholdspublisering pb
     WHERE pb.tenant = p_tenant AND pb.side_id = v_utkast.side_id
       AND pb.tilbake_ts IS NULL
     ORDER BY pb.versjon DESC LIMIT 1;
    IF v_levende IS NULL THEN
        v_form := 'avpublisering';
        v_til := NULL;
    ELSE
        v_form := 'forrige_versjon';
        v_til := v_levende.versjon;
        -- DEN FORRIGE RULLES UT I SAMME TRANSAKSJON. To levende
        -- versjoner av samme side ville gjort «hva sto der» til et
        -- spoersmaal med to svar.
        -- ALIAS, IKKE BARE KOLONNENAVN: OUT-parameteren heter
        -- `publisering_id`, og uten aliaset er referansen tvetydig.
        -- 132 gikk i nøyaktig den fella med `antall` og `relasjon`.
        UPDATE public.innholdspublisering pb
           SET tilbake_ts = now(), tilbake_av = p_publisert_av
         WHERE pb.tenant = p_tenant
           AND pb.publisering_id = v_levende.publisering_id;
    END IF;
    INSERT INTO public.innholdspublisering
        (tenant, publisering_id, utkast_id, side_id, versjon,
         visning_id, publisert_av, rollbackform, rollback_til_versjon)
    VALUES (p_tenant, p_publisering_id, p_utkast_id, v_utkast.side_id,
            v_utkast.versjon, p_visning_id, p_publisert_av, v_form, v_til);
    UPDATE public.innholdsutkast u SET status = 'publisert'
     WHERE u.tenant = p_tenant AND u.utkast_id = p_utkast_id;
    PERFORM public.m20_evidens(p_tenant, p_publisering_id,
        'publiser', p_aktor,
        jsonb_build_object('av', p_publisert_av, 'side',
                           v_utkast.side_id, 'rollbackform', v_form));
    RETURN QUERY SELECT p_publisering_id, v_form, v_til;
END $$;

-- ---------------------------------------------------------------------
-- `m20_rull_tilbake` — VEIEN SOM ALLEREDE VAR REGNET UT.
--
-- Døra finner ikke ut noe nytt. Den GÅR veien som ble frosset da
-- siden ble publisert, og bærer navnet på den som gikk den.
--
-- EN ROLLBACK FJERNER SIDEN. Den fjerner ikke at noen leste den, og
-- derfor står publiseringsraden — med begge tidspunktene og begge
-- navnene.
-- ---------------------------------------------------------------------
CREATE FUNCTION m20_rull_tilbake(p_tenant TEXT, p_publisering_id UUID,
                                 p_tilbake_av TEXT, p_aktor TEXT)
RETURNS TABLE (utfall TEXT, gjenopprettet_versjon INT, grunn TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_rad RECORD;
    v_forrige RECORD;
    v_krav RECORD;
    v_utlopte INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm20_rull_tilbake');
    IF p_tilbake_av IS NULL OR btrim(p_tilbake_av) = '' THEN
        RAISE EXCEPTION 'm20_rull_tilbake: tilbake_av mangler — en'
            ' tilbakerulling uten et navn bak er modulens egen';
    END IF;
    SELECT * INTO v_rad FROM public.innholdspublisering
     WHERE tenant = p_tenant AND publisering_id = p_publisering_id
       FOR UPDATE;
    IF v_rad IS NULL THEN
        RAISE EXCEPTION 'm20_rull_tilbake: ukjent publisering %',
            p_publisering_id;
    END IF;
    IF v_rad.tilbake_ts IS NOT NULL THEN
        RAISE EXCEPTION 'm20_rull_tilbake: allerede rullet tilbake %',
            v_rad.tilbake_ts;
    END IF;
    UPDATE public.innholdspublisering
       SET tilbake_ts = now(), tilbake_av = p_tilbake_av
     WHERE tenant = p_tenant AND publisering_id = p_publisering_id;
    IF v_rad.rollbackform = 'avpublisering' THEN
        -- ALLE TRE UTFALLENE SKRIVER EVIDENS, og det er ikke
        -- symmetri for symmetriens skyld: alle tre MUTERER
        -- publiseringsraden. Et utfall uten en linje i sporet er en
        -- side som ble tatt ned uten at huset vet hvem som gjorde det.
        PERFORM public.m20_evidens(p_tenant, p_publisering_id,
            'rull_tilbake', p_aktor,
            jsonb_build_object('av', p_tilbake_av,
                               'utfall', 'avpublisert'));
        RETURN QUERY SELECT 'avpublisert'::TEXT, NULL::INT, NULL::TEXT;
        RETURN;
    END IF;
    -- Å GJENOPPRETTE FORRIGE VERSJON ER Å PUBLISERE DEN. Da gjelder
    -- kildekravet, og det gjelder like fullt fordi teksten er gammel:
    -- et datablad som utløp i mellomtiden gjør den gamle siden like
    -- udokumentert som en ny ville vært.
    SELECT * INTO v_forrige FROM public.innholdspublisering
     WHERE tenant = p_tenant AND side_id = v_rad.side_id
       AND versjon = v_rad.rollback_til_versjon
     ORDER BY publisert_ts DESC LIMIT 1;
    SELECT * INTO v_krav FROM public.innholdskrav
     WHERE tenant = p_tenant ORDER BY kravversjon DESC LIMIT 1;
    SELECT count(*) INTO v_utlopte
      FROM public.innholdspaastand p
      JOIN public.kildedokument d
        ON d.tenant = p.tenant AND d.kilde_id = p.kilde_id
     WHERE p.tenant = p_tenant AND p.utkast_id = v_forrige.utkast_id
       AND NOT public.m20_kilde_gyldig(d.gyldig_til, d.registrert,
                                       v_krav.kilde_gyldig_dogn,
                                       current_date);
    IF v_utlopte > 0 THEN
        -- SIDEN BLIR STÅENDE AVPUBLISERT, OG DET ER RIKTIG UTFALL.
        -- Å nekte tilbakerullingen ville låst huset til den nye siden
        -- det nettopp ville bort fra, og å gjenopprette den gamle
        -- ville publisert en udokumentert paastand. Tomrommet er det
        -- eneste av de tre som ikke paastaar noe.
        PERFORM public.m20_evidens(p_tenant, p_publisering_id,
            'rull_tilbake', p_aktor,
            jsonb_build_object('av', p_tilbake_av, 'utfall',
                               'forrige_ikke_gjenopprettet',
                               'utlopte_kilder', v_utlopte));
        RETURN QUERY SELECT 'forrige_ikke_gjenopprettet'::TEXT,
                            v_rad.rollback_til_versjon,
                            format('%s kilde(r) er utloept siden'
                                   ' versjon %s ble publisert',
                                   v_utlopte, v_rad.rollback_til_versjon);
        RETURN;
    END IF;
    INSERT INTO public.innholdspublisering
        (tenant, publisering_id, utkast_id, side_id, versjon,
         visning_id, publisert_av, rollbackform, rollback_til_versjon)
    VALUES (p_tenant, gen_random_uuid(), v_forrige.utkast_id,
            v_forrige.side_id, v_forrige.versjon, v_forrige.visning_id,
            p_tilbake_av, 'avpublisering', NULL);
    PERFORM public.m20_evidens(p_tenant, p_publisering_id,
        'rull_tilbake', p_aktor,
        jsonb_build_object('av', p_tilbake_av, 'utfall',
                           'forrige_gjenopprettet'));
    RETURN QUERY SELECT 'forrige_gjenopprettet'::TEXT,
                        v_rad.rollback_til_versjon, NULL::TEXT;
END $$;

-- =====================================================================
-- LESEDØRENE. SECURITY DEFINER, OG DERFOR EIET AV MODULROLLEN.
--
-- Eide migrator dem, ville de lest med migrators rettigheter — altså
-- forbi radvakten. 130s lærdom, og den gjelder hver eneste av dem.
-- =====================================================================

-- `m20_utkastet` — utkastet med sine påstander OG hver påstands kilde.
--
-- KILDEN STÅR I SAMME RAD SOM PÅSTANDEN. Et oppslag til ville gjort
-- det mulig å lese påstanden uten å se hva den hviler på, og det er
-- nøyaktig tilstanden modulen finnes for å hindre.
CREATE FUNCTION m20_utkastet(p_tenant TEXT, p_utkast_id UUID)
RETURNS TABLE (paastand_id UUID, rekkefolge INT, tekst TEXT,
               kilde_id UUID, kilde_tittel TEXT, dokumenttype TEXT,
               kilde_sha256 TEXT, kilde_gyldig_til DATE,
               kilde_gyldig BOOLEAN, registrert TIMESTAMPTZ,
               registrert_av TEXT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT p.paastand_id, p.rekkefolge, p.tekst, p.kilde_id, d.tittel,
           d.dokumenttype, p.kilde_sha256, d.gyldig_til,
           public.m20_kilde_gyldig(d.gyldig_til, d.registrert,
               coalesce((SELECT k.kilde_gyldig_dogn
                           FROM public.innholdskrav k
                          WHERE k.tenant = p_tenant
                          ORDER BY k.kravversjon DESC LIMIT 1), 365),
               current_date),
           p.registrert, p.registrert_av
      FROM public.innholdspaastand p
      JOIN public.kildedokument d
        ON d.tenant = p.tenant AND d.kilde_id = p.kilde_id
     WHERE p.tenant = p_tenant AND p.utkast_id = p_utkast_id
     ORDER BY p.rekkefolge
$$;

-- `m20_sideregister` — sidene med sin levende versjon og sitt utkast.
CREATE FUNCTION m20_sideregister(p_tenant TEXT, p_maks INT)
RETURNS TABLE (side_id TEXT, siste_versjon INT, siste_utkast_id UUID,
               siste_status TEXT, levende_versjon INT,
               levende_publisert TIMESTAMPTZ, levende_publisert_av TEXT,
               antall_paastander INT, antall_utlopte_kilder INT,
               antall_visninger INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    WITH vindu AS (
        SELECT coalesce((SELECT k.kilde_gyldig_dogn
                           FROM public.innholdskrav k
                          WHERE k.tenant = p_tenant
                          ORDER BY k.kravversjon DESC LIMIT 1), 365) AS d),
    siste AS (
        SELECT DISTINCT ON (u.side_id) u.side_id, u.versjon, u.utkast_id,
               u.status
          FROM public.innholdsutkast u
         WHERE u.tenant = p_tenant
         ORDER BY u.side_id, u.versjon DESC)
    SELECT s.side_id, s.versjon, s.utkast_id, s.status,
           lev.versjon, lev.publisert_ts, lev.publisert_av,
           (SELECT count(*)::INT FROM public.innholdspaastand p
             WHERE p.tenant = p_tenant AND p.utkast_id = s.utkast_id),
           (SELECT count(*)::INT
              FROM public.innholdspaastand p
              JOIN public.kildedokument d
                ON d.tenant = p.tenant AND d.kilde_id = p.kilde_id
             WHERE p.tenant = p_tenant AND p.utkast_id = s.utkast_id
               AND NOT public.m20_kilde_gyldig(d.gyldig_til, d.registrert,
                                               (SELECT d FROM vindu),
                                               current_date)),
           (SELECT count(*)::INT FROM public.innholdsvisning v
             WHERE v.tenant = p_tenant AND v.utkast_id = s.utkast_id)
      FROM siste s
      LEFT JOIN LATERAL (
           SELECT pb.versjon, pb.publisert_ts, pb.publisert_av
             FROM public.innholdspublisering pb
            WHERE pb.tenant = p_tenant AND pb.side_id = s.side_id
              AND pb.tilbake_ts IS NULL
            ORDER BY pb.versjon DESC LIMIT 1) lev ON true
     ORDER BY s.side_id
     LIMIT greatest(p_maks, 1)
$$;

-- `m20_kildene` — kilderegisteret slik denne modulen ser det.
CREATE FUNCTION m20_kildene(p_tenant TEXT)
RETURNS TABLE (kilde_id UUID, tittel TEXT, dokumenttype TEXT,
               gyldig_til DATE, gyldig BOOLEAN, dogn_igjen INT,
               innhold_sha256 TEXT, registrert TIMESTAMPTZ,
               registrert_av TEXT, antall_paastander INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    WITH vindu AS (
        SELECT coalesce((SELECT k.kilde_gyldig_dogn
                           FROM public.innholdskrav k
                          WHERE k.tenant = p_tenant
                          ORDER BY k.kravversjon DESC LIMIT 1), 365) AS d)
    SELECT d.kilde_id, d.tittel, d.dokumenttype, d.gyldig_til,
           public.m20_kilde_gyldig(d.gyldig_til, d.registrert,
                                   (SELECT v.d FROM vindu v), current_date),
           (coalesce(d.gyldig_til,
                     (d.registrert AT TIME ZONE 'UTC')::DATE
                     + (SELECT v.d FROM vindu v)) - current_date)::INT,
           d.innhold_sha256, d.registrert, d.registrert_av,
           (SELECT count(*)::INT FROM public.innholdspaastand p
             WHERE p.tenant = p_tenant AND p.kilde_id = d.kilde_id)
      FROM public.kildedokument d
     WHERE d.tenant = p_tenant
     ORDER BY d.registrert DESC
$$;

-- `m20_visningene` — FORHÅNDSVISNINGENE AV ETT UTKAST.
--
-- DEN FINNES FORDI PUBLISERINGSVEIEN TRENGER `visning_id`, OG
-- PUBLISERINGSRADEN BÆRER DEN IKKE. Første utkast lot flaten lete
-- etter visninger i `m20_publiseringene`, og det var galt på to måter:
-- en side som publiseres FØR FØRSTE GANG har ingen publiseringer i det
-- hele tatt, og en publiseringsrad bærer `vist_ts`/`vist_for` men ikke
-- id-en. Panelet ville sagt «ingen har forhåndsvist dette» om et
-- utkast noen nettopp hadde sett på.
--
-- CodeRabbit fant den 5/9, og den var ikke synlig i portene mine:
-- fixturen hadde publiseringer, så den ene veien som virket var den
-- eneste som ble målt.
CREATE FUNCTION m20_visningene(p_tenant TEXT, p_utkast_id UUID)
RETURNS TABLE (visning_id UUID, vist_hash TEXT, vist_ts TIMESTAMPTZ,
               vist_for TEXT, gjelder_dette_innholdet BOOLEAN)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT v.visning_id, v.vist_hash, v.vist_ts, v.vist_for,
           -- SUMMEN SAMMENLIGNES HER OGSÅ, og ikke bare i døra: den
           -- som skal velge en visning skal se hvilken som gjelder
           -- innholdet, ikke oppdage det av et nekt.
           v.vist_hash = u.innholds_hash
      FROM public.innholdsvisning v
      JOIN public.innholdsutkast u
        ON u.tenant = v.tenant AND u.utkast_id = v.utkast_id
     WHERE v.tenant = p_tenant AND v.utkast_id = p_utkast_id
     ORDER BY v.vist_ts DESC
$$;

-- `m20_publiseringene` — hver periode en versjon var levende.
CREATE FUNCTION m20_publiseringene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (publisering_id UUID, side_id TEXT, versjon INT,
               publisert_ts TIMESTAMPTZ, publisert_av TEXT,
               rollbackform TEXT, rollback_til_versjon INT,
               tilbake_ts TIMESTAMPTZ, tilbake_av TEXT, levende BOOLEAN,
               vist_ts TIMESTAMPTZ, vist_for TEXT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT pb.publisering_id, pb.side_id, pb.versjon, pb.publisert_ts,
           pb.publisert_av, pb.rollbackform, pb.rollback_til_versjon,
           pb.tilbake_ts, pb.tilbake_av, pb.tilbake_ts IS NULL,
           v.vist_ts, v.vist_for
      FROM public.innholdspublisering pb
      JOIN public.innholdsvisning v
        ON v.tenant = pb.tenant AND v.visning_id = pb.visning_id
     WHERE pb.tenant = p_tenant
     ORDER BY pb.publisert_ts DESC
     LIMIT greatest(p_maks, 1)
$$;

-- `m20_innholdsfunn` — funnene, med hvem som kan lukke hvert av dem.
CREATE FUNCTION m20_innholdsfunn(p_tenant TEXT, p_maks INT)
RETURNS TABLE (funn_id UUID, funntype TEXT, referanse UUID,
               detaljer TEXT, over_grense BIGINT, apen BOOLEAN,
               forst_sett TIMESTAMPTZ, sist_sett TIMESTAMPTZ,
               lukket_av TEXT, kan_lukkes BOOLEAN)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT f.funn_id, f.funntype, f.referanse, f.detaljer,
           f.over_grense, f.apen, f.forst_sett, f.sist_sett, f.lukket_av,
           NOT public.m20_funn_er_sveipens(f.funntype)
      FROM public.innholdsfunn f
     WHERE f.tenant = p_tenant
     ORDER BY f.apen DESC, f.sist_sett DESC
     LIMIT greatest(p_maks, 1)
$$;

-- `m20_bildet` — hele modulen i ett kall.
CREATE FUNCTION m20_bildet(p_tenant TEXT)
RETURNS TABLE (sider INT, utkast INT, klare INT, publiserte INT,
               levende_sider INT, paastander INT, kilder INT,
               utlopte_kilder INT, paastander_paa_utlopt_kilde INT,
               visninger INT, apne_funn INT, har_krav BOOLEAN,
               kilde_gyldig_dogn INT, visning_gyldig_min INT,
               varselfrist_dogn INT, kravversjon INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    WITH k AS (
        SELECT * FROM public.innholdskrav
         WHERE tenant = p_tenant ORDER BY kravversjon DESC LIMIT 1),
    vindu AS (SELECT coalesce((SELECT kilde_gyldig_dogn FROM k), 365) AS d)
    SELECT (SELECT count(DISTINCT side_id)::INT
              FROM public.innholdsutkast WHERE tenant = p_tenant),
           (SELECT count(*) FILTER (WHERE status = 'utkast')::INT
              FROM public.innholdsutkast WHERE tenant = p_tenant),
           (SELECT count(*) FILTER (WHERE status = 'klar')::INT
              FROM public.innholdsutkast WHERE tenant = p_tenant),
           (SELECT count(*) FILTER (WHERE status = 'publisert')::INT
              FROM public.innholdsutkast WHERE tenant = p_tenant),
           (SELECT count(DISTINCT side_id)::INT
              FROM public.innholdspublisering
             WHERE tenant = p_tenant AND tilbake_ts IS NULL),
           (SELECT count(*)::INT FROM public.innholdspaastand
             WHERE tenant = p_tenant),
           (SELECT count(*)::INT FROM public.kildedokument
             WHERE tenant = p_tenant),
           (SELECT count(*)::INT FROM public.kildedokument d
             WHERE d.tenant = p_tenant
               AND NOT public.m20_kilde_gyldig(d.gyldig_til, d.registrert,
                       (SELECT v.d FROM vindu v), current_date)),
           (SELECT count(*)::INT
              FROM public.innholdspaastand p
              JOIN public.kildedokument d
                ON d.tenant = p.tenant AND d.kilde_id = p.kilde_id
             WHERE p.tenant = p_tenant
               AND NOT public.m20_kilde_gyldig(d.gyldig_til, d.registrert,
                       (SELECT v.d FROM vindu v), current_date)),
           (SELECT count(*)::INT FROM public.innholdsvisning
             WHERE tenant = p_tenant),
           (SELECT count(*)::INT FROM public.innholdsfunn
             WHERE tenant = p_tenant AND apen),
           (SELECT count(*) > 0 FROM k),
           (SELECT kilde_gyldig_dogn FROM k),
           (SELECT visning_gyldig_min FROM k),
           (SELECT varselfrist_dogn FROM k),
           (SELECT kravversjon FROM k)
$$;

-- `m20_lukk_funn` — OG DEN NEKTER PÅ SVEIPENS EGNE.
CREATE FUNCTION m20_lukk_funn(p_tenant TEXT, p_funn_id UUID,
                              p_grunn TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_type TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm20_lukk_funn');
    SELECT funntype INTO v_type FROM public.innholdsfunn
     WHERE tenant = p_tenant AND funn_id = p_funn_id FOR UPDATE;
    IF v_type IS NULL THEN
        RAISE EXCEPTION 'm20_lukk_funn: ukjent funn %', p_funn_id;
    END IF;
    IF public.m20_funn_er_sveipens(v_type) THEN
        RAISE EXCEPTION 'm20_lukk_funn: % lukkes av at tilstanden'
            ' opphoerer, ikke av at noen huker av', v_type;
    END IF;
    UPDATE public.innholdsfunn
       SET apen = false, lukket_ts = now(), lukket_av = p_aktor,
           lukket_grunn = p_grunn
     WHERE tenant = p_tenant AND funn_id = p_funn_id AND apen;
    PERFORM public.m20_evidens(p_tenant, p_funn_id, 'lukk_funn',
        p_aktor, jsonb_build_object('type', v_type));
    RETURN FOUND;
END $$;

-- =====================================================================
-- `m20_sveip_innhold` — SVEIPEN SKRIVER INGENTING OG PUBLISERER
-- INGENTING. Den sier fra, og der stopper den.
--
-- ÉN TENANT OM GANGEN (130s lærdom). En sveip uten `disponit.tenant`
-- ville sett NULL RADER under FORCE RLS og meldt null funn — med grønn
-- exit-kode. Den ser ut som en vellykket kjøring.
--
-- TO AV TRE LUKKES HERFRA. `publisert_paastand_uten_gyldig_kilde`
-- forsvinner når kilden fornyes eller siden avpubliseres,
-- `klart_utkast_uten_forhaandsvisning` når noen ser utkastet.
-- `kilde_utloper_snart_uavklart` KAN lukkes av et menneske, og 125/126s
-- vakt sørger for at den lukkingen står natten over.
-- =====================================================================
CREATE FUNCTION m20_sveip_innhold(p_maks_tenanter INT)
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
        SELECT DISTINCT tenant FROM public.innholdskrav
         ORDER BY tenant LIMIT greatest(p_maks_tenanter, 1)
    LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        v_antall := v_antall + 1;
        SELECT * INTO v_krav FROM public.innholdskrav
         WHERE tenant = v_t ORDER BY kravversjon DESC LIMIT 1;

        -- 1. EN LEVENDE SIDE MED EN PÅSTAND PÅ EN UTLØPT KILDE.
        --    Skaden er allerede skjedd — siden står ute nå — og det er
        --    derfor dette er sveipens viktigste funn og ikke dens
        --    eneste unnskyldning for å slippe nektet i døra.
        WITH treff AS (
            SELECT pb.publisering_id, count(*) AS antall
              FROM public.innholdspublisering pb
              JOIN public.innholdspaastand p
                ON p.tenant = pb.tenant AND p.utkast_id = pb.utkast_id
              JOIN public.kildedokument d
                ON d.tenant = p.tenant AND d.kilde_id = p.kilde_id
             WHERE pb.tenant = v_t AND pb.tilbake_ts IS NULL
               AND NOT public.m20_kilde_gyldig(d.gyldig_til, d.registrert,
                                               v_krav.kilde_gyldig_dogn,
                                               current_date)
             GROUP BY pb.publisering_id),
        satt AS (
            INSERT INTO public.innholdsfunn
                (tenant, funn_id, funntype, referanse, detaljer,
                 over_grense)
            SELECT v_t, gen_random_uuid(),
                   'publisert_paastand_uten_gyldig_kilde', t.publisering_id,
                   format('%s publisert paastand(er) hviler paa en'
                          ' utloept kilde', t.antall), t.antall
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) DO UPDATE
                SET sist_sett = now(), apen = true,
                    detaljer = EXCLUDED.detaljer,
                    over_grense = EXCLUDED.over_grense,
                    lukket_ts = NULL, lukket_av = NULL, lukket_grunn = NULL
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.innholdsfunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm20_sveip',
                   lukket_grunn = 'kilden er gyldig igjen eller siden er'
                                  ' avpublisert'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'publisert_paastand_uten_gyldig_kilde'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.publisering_id = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 2. ET UTKAST SOM ER `klar` UTEN AT NOEN HAR SETT DET.
        --
        --    FØRSTE UTKAST LETTE ETTER «utkast endret etter visning»,
        --    og den tilstanden ER UREPRESENTERBAR I DENNE MODELLEN:
        --    `innholdsvisning.vist_hash` KOPIERES fra utkastet, og
        --    utkastet er frosset av radvakten. En endring er en NY
        --    versjon med en NY rad, som ikke har noen visning i det
        --    hele tatt. Et funn som aldri kan reises OG ikke er ment
        --    som et bevis, er dødt — det ser ut som en vakt og er det
        --    ikke.
        --
        --    DET SOM FAKTISK KAN SKJE er dette: noen merker siden klar
        --    uten at et eneste menneske har sett den. `m20_merk_klar`
        --    krever ingen visning — den skal ikke gjøre det heller,
        --    for «klar fra modulens side» og «godkjent av et menneske»
        --    er to forskjellige ting. Døra nekter når de prøver å
        --    publisere. SVEIPEN SIER DET FØR DE PRØVER.
        WITH treff AS (
            SELECT u.utkast_id
              FROM public.innholdsutkast u
             WHERE u.tenant = v_t AND u.status = 'klar'
               AND NOT EXISTS (SELECT 1 FROM public.innholdsvisning v
                                WHERE v.tenant = v_t
                                  AND v.utkast_id = u.utkast_id)),
        satt AS (
            INSERT INTO public.innholdsfunn
                (tenant, funn_id, funntype, referanse, detaljer)
            SELECT v_t, gen_random_uuid(),
                   'klart_utkast_uten_forhaandsvisning', t.utkast_id,
                   'utkastet er merket klart, men ingen har sett det'
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) DO UPDATE
                SET sist_sett = now(), apen = true,
                    lukket_ts = NULL, lukket_av = NULL, lukket_grunn = NULL
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.innholdsfunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm20_sveip',
                   lukket_grunn = 'noen har sett utkastet'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'klart_utkast_uten_forhaandsvisning'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.utkast_id = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 3. EN KILDE SOM SNART UTLØPER OG BÆRER EN LEVENDE PÅSTAND.
        --    DENNE KAN ET MENNESKE LUKKE — «vi har sjekket, dokumentet
        --    staar seg» er en avklaring med et navn paa. Sveipen
        --    gjenaapner den derfor IKKE naar den er lukket (131s
        --    laerdom): `NOT EXISTS`-leddet paa lukkede funn er hele
        --    forskjellen.
        WITH treff AS (
            SELECT d.kilde_id,
                   (coalesce(d.gyldig_til,
                             (d.registrert AT TIME ZONE 'UTC')::DATE
                             + v_krav.kilde_gyldig_dogn)
                    - current_date)::BIGINT AS dogn
              FROM public.kildedokument d
             WHERE d.tenant = v_t
               AND public.m20_kilde_gyldig(d.gyldig_til, d.registrert,
                                           v_krav.kilde_gyldig_dogn,
                                           current_date)
               AND coalesce(d.gyldig_til,
                            (d.registrert AT TIME ZONE 'UTC')::DATE
                            + v_krav.kilde_gyldig_dogn)
                   <= current_date + v_krav.varselfrist_dogn
               AND EXISTS (
                   SELECT 1 FROM public.innholdspaastand p
                     JOIN public.innholdspublisering pb
                       ON pb.tenant = p.tenant AND pb.utkast_id = p.utkast_id
                    WHERE p.tenant = v_t AND p.kilde_id = d.kilde_id
                      AND pb.tilbake_ts IS NULL)),
        satt AS (
            INSERT INTO public.innholdsfunn
                (tenant, funn_id, funntype, referanse, detaljer,
                 over_grense)
            SELECT v_t, gen_random_uuid(), 'kilde_utloper_snart_uavklart',
                   t.kilde_id,
                   format('kilden utloeper om %s doegn og baerer en'
                          ' levende paastand', t.dogn), t.dogn
              FROM treff t
             WHERE NOT EXISTS (
                   SELECT 1 FROM public.innholdsfunn f
                    WHERE f.tenant = v_t AND NOT f.apen
                      AND f.funntype = 'kilde_utloper_snart_uavklart'
                      AND f.referanse = t.kilde_id)
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
REVOKE ALL ON FUNCTION m20_sveip_innhold(INT) FROM PUBLIC;

-- =====================================================================
-- RETTIGHETENE. SP-7: KJØRETIDEN NÅR DØRENE OG INGENTING ANNET.
--
-- FØRST RIVES ALT FRA `PUBLIC`, OG DET ER IKKE EN FORMALITET.
-- Postgres gir EXECUTE til PUBLIC på hver nye funksjon. Uten denne
-- løkka når SVEIPEROLLEN alle dørene i modulen — også `m20_publiser`
-- — og «sveipen publiserer ingenting» ville vært en påstand om
-- oppførsel i stedet for en rettighet.
--
-- LØKKE OG IKKE 22 LINJER: en liste over navn ville gått ut av takt
-- med filen første gang noen la til en dør, og da ville hullet vært
-- usynlig. Denne spør katalogen.
-- =====================================================================
DO $$
DECLARE r RECORD;
BEGIN
    -- EIERSKAPSLEDDET ER IKKE PYNT: et REVOKE fra en rolle som ikke
    -- eier funksjonen AVBRYTER migrasjonen. Loekka tar modulrollens
    -- egne doerer; migrators fire vakter er revokert der de lages.
    FOR r IN SELECT p.oid::regprocedure AS sig
               FROM pg_proc p
              WHERE p.pronamespace = 'public'::regnamespace
                AND p.proname LIKE 'm20\_%'
                AND pg_get_userbyid(p.proowner) = current_user
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', r.sig);
    END LOOP;
END $$;

GRANT EXECUTE ON FUNCTION m20_sett_krav(TEXT, INT, INT, INT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m20_registrer_kilde(TEXT, UUID, TEXT, TEXT,
    DATE, TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m20_registrer_utkast(TEXT, UUID, TEXT, JSONB,
    INT, INT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m20_registrer_paastand(TEXT, UUID, UUID, INT,
    TEXT, UUID, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m20_registrer_visning(TEXT, UUID, UUID, TEXT,
    TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m20_merk_klar(TEXT, UUID, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m20_publiser(TEXT, UUID, UUID, UUID, TEXT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m20_rull_tilbake(TEXT, UUID, TEXT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m20_utkastet(TEXT, UUID) TO disponit;
GRANT EXECUTE ON FUNCTION m20_sideregister(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m20_kildene(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m20_visningene(TEXT, UUID) TO disponit;
GRANT EXECUTE ON FUNCTION m20_publiseringene(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m20_innholdsfunn(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m20_bildet(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m20_lukk_funn(TEXT, UUID, TEXT, TEXT) TO disponit;

-- SVEIPEROLLEN NÅR ÉN FUNKSJON, OG BARE DEN (111s form).
GRANT EXECUTE ON FUNCTION m20_sveip_innhold(INT) TO disponit_innholdssveip;

RESET ROLE;

-- =====================================================================
-- M-36s FUNNKATALOG (132). 133s LÆRDOM, GJENTATT UTEN Å BLI STOPPET.
--
-- `innholdsfunn` er et nytt funnregister i huset, og M-36 nekter å
-- rangere så lenge det finnes ett den ikke kjenner. RADEN ALENE ER
-- BARE EN LOVNAD: `m36_apne_funn` løper som optimalisatoreieren og
-- LESER tabellen, så lesretten må følge med. Registrert uten SELECT er
-- verre enn uregistrert — det første ser komplett ut.
-- =====================================================================
INSERT INTO m36_funnregister
    (relasjon, modul, typekolonne, apenform, begrunnelse)
VALUES
    ('innholdsfunn', 'm20_innhold', 'funntype', 'apen_kolonne',
     'husets form')
ON CONFLICT (relasjon) DO NOTHING;
GRANT SELECT ON innholdsfunn TO disponit_optimalisator_eier;
