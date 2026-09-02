-- 105: M-24 leverandør- og innkjøpsagent v1 — LEVERANDØRREGISTERET.
-- Fem tenant-skopede tabeller, tolv dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- V1-AVGRENSNINGEN, ORDRETT FRA MANIFESTET: katalogteksten lover at
-- agenten BETALER leverandøren innen policygrenser, og at den varsler
-- når en innkjøpspris stiger over terskel. v1 BETALER INGENTING.
--
-- DOMMEN: en utgående betaling er den ene handlingen i hele katalogen
-- som er umulig å angre. En feilført postering kan korrigeres, en
-- purring kan beklages — men penger som har forlatt kontoen er borte,
-- og de er borte hos noen andre. Katalogen sier dessuten selv «innen
-- policygrenser», og de grensene må VÆRE MÅLT før de kan settes. En
-- terskel for prisstigning som ingen har målt normalvariasjonen bak, er
-- et tall noen gjettet.
--
-- Det finnes derfor ingen betalingsvei i denne migrasjonen: ingen
-- bankkonto, ingen betalingsstatus, ingen utgående kø, ingen
-- forfallsdato som utløser noe. Registeret REGISTRERER AVTALEN, MÅLER
-- LEVERANSEN MOT DEN, og gjør avviket til et funn.
--
-- DOMMENE v1 HVILER PÅ, håndhevet i DATAMODELLEN:
--
--   1. BELØP ER HELTALL I MINSTE ENHET (øre), `BIGINT`, uten unntak.
--      Prisavviket regnes i promille med heltallsaritmetikk — en
--      terskel som «nesten» er passert er ingen terskel.
--
--   2. EN MÅLING ER MOT EN AVTALT VERDI. `leveranse` kan ikke finnes
--      uten en `leveranseavtale`, og målingsdatoen må ligge INNENFOR
--      avtalens gyldighet. En leveranse målt mot en avtale som ikke
--      gjaldt den dagen er et tall uten dom — og et tall uten dom er
--      verre enn intet tall, fordi noen handler på det.
--
--   3. TERSKLENE ER TENANTENS EGNE, ikke konstanter i koden. De ligger
--      i `leverandorterskel`, skrives gjennom en dør, og versjoneres.
--      «Ti prosent prisøkning er for mye» er en forretningsbeslutning,
--      ikke en teknisk detalj — nøyaktig samme dom som M-23s
--      purretrinn. Sveipen tar INGEN terskelparameter.
--
--      ÆRLIG OM HVA DETTE IKKE ER: det går IKKE gjennom M-1s
--      policymotor. M-1 er dokumentbasert (utkast → attestering →
--      aktivering) og har ingen fasilitet for en tenant-innstilling.
--      Invarianten `terskel_hardkodet` er oppfylt i den forstand som
--      betyr noe — tenanten eier og fører verdiene, og de er
--      revisjonssporet — men koblingen til M-1 står igjen som et
--      NAVNGITT gap, ikke som en påstand om at den finnes.
--
--   4. RETNINGEN PÅ ET SLA ER EN LUKKET TABELL, ikke en gjetning.
--      «Oppetid 995 promille» er brutt når den faktiske er LAVERE;
--      «leveringstid 3 døgn» er brutt når den faktiske er HØYERE. Et
--      brudd regnet med feil fortegn er STILLE: det ser ut som at alt
--      er i orden. `m24_bryter_sla` er én IMMUTABLE funksjon med én
--      arm per type, og en ukjent type er en EXCEPTION — ikke `false`.
--
-- GRENSEN MOT M-26, sagt eksplisitt: katalogen deler
-- marginbeskyttelsen. M-24 OPPDAGER kostnadsøkningen, M-26 FORESLÅR ny
-- pris. v1 holder seg på sin side av snittet og beregner ikke ny pris i
-- det hele tatt — det finnes ingen kolonne, ingen dør og ingen
-- returverdi her som er et prisforslag.
--
-- GRENSEN MOT M-21: M-21 eier PLIKTER — våre frister mot omverdenen.
-- M-24 eier den andre veien: leverandørens forpliktelse MOT OSS, målt.
-- Et SLA-brudd er et funn om DEM, ikke en plikt for oss.
--
-- SVEIPEN HAR SIN EGEN ROLLE OG SIN EGEN TIMER (095/100–104):
-- `disponit_leverandorsveip` har nøyaktig ÉN rettighet — EXECUTE på
-- `m24_sveip_leverandorer` — og INGEN tabellrettigheter. Sveipen
-- BETALER INGEN og TERMINERER INGEN AVTALE; den skriver FUNN.
--
-- FORMENE ER HUSETS: tabellene eies av migrator, dørene av NOLOGIN-rollen
-- `disponit_leverandor_eier`, tenant TEXT + RLS ENABLE+FORCE +
-- `tenant_isolasjon`, SP-1 i hver tenantbundet definer, ingen BYPASSRLS.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_leverandor_eier') THEN
        RAISE EXCEPTION 'rollen disponit_leverandor_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_leverandor_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `leverandorterskel` — ÉN per tenant. DOM 3 I TABELLFORM: dette er
-- tenantens tall, ikke modulens.
--
-- Tabellen finnes for at ingen av dem skal stå i koden. En modul som
-- bar sin egen terskel ville gitt seg selv fullmakt til å bestemme hva
-- «for dyrt» betyr for en virksomhet den ikke kjenner.
CREATE TABLE leverandorterskel (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- Hvor mye en faktisk pris kan ligge over den avtalte før det er et
    -- funn, i PROMILLE. Promille og ikke prosent fordi terskelen skal
    -- kunne settes finere enn et helt prosentpoeng uten et flyttall.
    prisstigning_promille INT NOT NULL DEFAULT 100
        CHECK (prisstigning_promille BETWEEN 0 AND 100000),
    -- Hvor mange bruddleveranser som skal til før avtalen er et funn.
    -- ÉN forsinket leveranse er livet; tre er et mønster. Tenanten
    -- eier grensen mellom de to.
    sla_brudd_grense INT NOT NULL DEFAULT 1
        CHECK (sla_brudd_grense BETWEEN 1 AND 1000),
    -- Hvor mange døgn før utløp en aktiv avtale skal varsles.
    avtale_varsel_dogn INT NOT NULL DEFAULT 30
        CHECK (avtale_varsel_dogn BETWEEN 0 AND 3650),
    -- Hvor lenge en aktiv avtale kan stå uten en eneste måling før det
    -- er et funn. Vi betaler for noe ingen har målt.
    maling_stillhet_dogn INT NOT NULL DEFAULT 90
        CHECK (maling_stillhet_dogn BETWEEN 1 AND 3650),
    -- VERSJONEN ØKER VED HVER ENDRING, som M-23s purreplan: et funn
    -- bærer versjonen det ble vurdert mot, så en endret terskel ikke
    -- omskriver historien.
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT leverandorterskel_pk PRIMARY KEY (tenant)
);

-- `leverandorpart` — leverandøren. Navnet er `leverandorpart` og ikke
-- `leverandor` fordi `leverandor` alt er et KOLONNENAVN i 088 og 098,
-- og en tabell med samme navn som en utbredt kolonne gjør hver
-- feilmelding tvetydig.
CREATE TABLE leverandorpart (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    leverandor_id UUID NOT NULL,
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    -- Fri referanse (orgnummer, kundenummer hos oss, hva tenanten
    -- bruker). Ingen validering av format: en modul som krevde norsk
    -- orgnummer ville låst registeret til ett land.
    ekstern_ref TEXT CHECK (ekstern_ref IS NULL
                            OR ekstern_ref ~ '[^[:space:]]'),
    aktiv BOOLEAN NOT NULL DEFAULT true,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT leverandorpart_pk PRIMARY KEY (tenant, leverandor_id),
    CONSTRAINT leverandorpart_navn_unik UNIQUE (tenant, navn)
);

-- `leveranseavtale` — AVTALEN, og dermed DOMMEN enhver måling måles mot.
--
-- DOM 2 I TABELLFORM: `avtalt_verdi` og `avtalt_pris_ore` er NOT NULL.
-- En avtale uten avtalt verdi er ingen avtale, og en leveranse målt mot
-- den ville vært et tall uten dom.
CREATE TABLE leveranseavtale (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    avtale_id UUID NOT NULL,
    leverandor_id UUID NOT NULL,
    -- HVA som leveres. Tenantens ord, ikke et lukket sett: ingen
    -- katalog vet hva en virksomhet kjøper.
    ytelse TEXT NOT NULL CHECK (ytelse ~ '[^[:space:]]'),
    -- HVILKEN STØRRELSE som er avtalt. LUKKET SETT, og hver verdi
    -- bærer sin enhet i navnet — en `avtalt_enhet`-kolonne ved siden av
    -- ville vært to kilder til samme sannhet.
    sla_type TEXT NOT NULL
        CONSTRAINT avtale_sla_type_lukket CHECK (sla_type IN (
            'leveringstid_dogn', 'responstid_timer',
            'feilrate_promille', 'oppetid_promille')),
    -- Den AVTALTE verdien, i enheten `sla_type` navngir. Heltall: en
    -- oppetid på 99,5 % er 995 promille, ikke 0.995.
    avtalt_verdi INT NOT NULL CHECK (avtalt_verdi >= 0),
    -- Den AVTALTE prisen per leveranse, i ØRE.
    avtalt_pris_ore BIGINT NOT NULL CHECK (avtalt_pris_ore >= 0),
    gyldig_fra DATE NOT NULL,
    gyldig_til DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'aktiv'
        CONSTRAINT avtale_status_lukket
        CHECK (status IN ('aktiv', 'avsluttet')),
    avsluttet_ts TIMESTAMPTZ,
    avsluttet_av TEXT,
    avsluttet_begrunnelse TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT leveranseavtale_pk PRIMARY KEY (tenant, avtale_id),
    CONSTRAINT leveranseavtale_part_fk
        FOREIGN KEY (tenant, leverandor_id)
        REFERENCES leverandorpart (tenant, leverandor_id),
    -- EN AVTALE VARER FRAMOVER. `gyldig_til < gyldig_fra` er et vindu
    -- ingen leveranse kan ligge i, og en avtale ingen måling treffer er
    -- en avtale som stilltiende slutter å måle noe.
    CONSTRAINT avtale_vindu_framover CHECK (gyldig_til >= gyldig_fra),
    -- AVSLUTNINGEN ER HEL ELLER IKKE. Én av de tre uten de andre er en
    -- halv avslutning ingen kan etterprøve. (`NULL ~ '...'` er NULL, og
    -- en CHECK som evaluerer til NULL PASSERER — derfor er hvert ledd
    -- eksplisitt `IS NOT NULL`, ikke bare et mønster. Hullet ble funnet
    -- i 101 og lukket i 102; det gjentas ikke her.)
    CONSTRAINT avtale_avslutning_helhet CHECK (
        (status = 'aktiv' AND avsluttet_ts IS NULL
         AND avsluttet_av IS NULL AND avsluttet_begrunnelse IS NULL)
     OR (status = 'avsluttet' AND avsluttet_ts IS NOT NULL
         AND avsluttet_av IS NOT NULL
         AND avsluttet_av ~ '[^[:space:]]'
         AND avsluttet_begrunnelse IS NOT NULL
         AND avsluttet_begrunnelse ~ '[^[:space:]]'))
);

-- ÉN AKTIV AVTALE PER LEVERANDØR OG YTELSE. To samtidige ville gjort
-- «hva er avtalt» til et spørsmål med to svar — og et SLA-brudd til noe
-- som avhenger av hvilken rad man leste.
CREATE UNIQUE INDEX leveranseavtale_en_aktiv
    ON leveranseavtale (tenant, leverandor_id, ytelse)
    WHERE status = 'aktiv';
CREATE INDEX leveranseavtale_tenant_status
    ON leveranseavtale (tenant, status, gyldig_til);

-- `leveranse` — MÅLINGEN. Append-only: en måling som kunne skrives om
-- ville gjort hele SLA-historikken til en påstand.
CREATE TABLE leveranse (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    leveranse_id UUID NOT NULL,
    avtale_id UUID NOT NULL,
    levert DATE NOT NULL,
    -- Den FAKTISKE verdien, i samme enhet som avtalens `sla_type`.
    faktisk_verdi INT NOT NULL CHECK (faktisk_verdi >= 0),
    -- Den FAKTISKE prisen, i ØRE.
    faktisk_pris_ore BIGINT NOT NULL CHECK (faktisk_pris_ore >= 0),
    referanse TEXT CHECK (referanse IS NULL
                          OR referanse ~ '[^[:space:]]'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT leveranse_pk PRIMARY KEY (tenant, leveranse_id),
    CONSTRAINT leveranse_avtale_fk FOREIGN KEY (tenant, avtale_id)
        REFERENCES leveranseavtale (tenant, avtale_id)
);
CREATE INDEX leveranse_avtale_levert
    ON leveranse (tenant, avtale_id, levert DESC);

-- `leverandorfunn` — funnene. NØKLET PÅ AVTALEN, ikke på leveransen:
-- funnet er om FORHOLDET til leverandøren, og det er der et menneske
-- handler. Hvilken leveranse som brøt sist står som en MÅLT KJENNSGJERNING
-- på funnet, ikke som en egen rad per brudd — femti rader om samme
-- avtale er femti varsler om én ting.
CREATE TABLE leverandorfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    avtale_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CONSTRAINT leverandorfunn_type_lukket CHECK (funntype IN (
            'sla_brudd', 'pris_over_terskel', 'avtale_utlopt',
            'avtale_uten_maling', 'ingen_terskel')),
    -- Antall bruddleveranser bak funnet, og den siste av dem.
    antall INT NOT NULL DEFAULT 0 CHECK (antall >= 0),
    siste_leveranse_id UUID,
    -- Hvor langt over grensen forholdet ligger: promille for pris,
    -- antall over `sla_brudd_grense` for SLA, døgn for de øvrige. Ett
    -- tall med én betydning per funntype, ikke fire kolonner der tre
    -- alltid er tomme.
    over_grense INT,
    terskelversjon INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT leverandorfunn_pk PRIMARY KEY (tenant, avtale_id, funntype),
    CONSTRAINT leverandorfunn_avtale_fk FOREIGN KEY (tenant, avtale_id)
        REFERENCES leveranseavtale (tenant, avtale_id),
    -- ET LUKKET FUNN HAR ET LUKKETIDSPUNKT, et åpent har det ikke.
    -- Eksplisitt `IS NOT NULL` i begge armer, av samme grunn som over.
    CONSTRAINT leverandorfunn_lukking_helhet CHECK (
        (apen AND lukket_ts IS NULL)
     OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE INDEX leverandorfunn_apne
    ON leverandorfunn (tenant, funntype) WHERE apen;


-- ------------------------------------------------------------
-- 2. Vaktene. Det datamodellen ikke kan si i en CHECK.
-- ------------------------------------------------------------

CREATE FUNCTION m24_terskel_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    -- TRUNCATE HAR SIN EGEN ARM. Uten den faller TG_OP='TRUNCATE'
    -- gjennom til radlogikken og feiler på «record "new" is not
    -- assigned yet» — riktig utfall, men en intern feil som ikke sier
    -- hva som ble nektet eller hvorfor. (Lærdommen fra 104.)
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'leverandorterskel: TRUNCATE avvist — tersklene'
            ' endres ved å sette nye, ikke ved å fjerne dem under'
            ' føttene på sveipen'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'leverandorterskel: DELETE avvist — en tenant'
            ' uten terskler kan ikke måle noe, og det er en tilstand'
            ' sveipen skal SI FRA om, ikke en man sletter seg inn i'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.versjon <= OLD.versjon THEN
        RAISE EXCEPTION 'leverandorterskel: versjonen må øke ved endring'
            ' (% -> %) — et funn bærer versjonen det ble vurdert mot',
            OLD.versjon, NEW.versjon
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m24_terskel_vakt() FROM PUBLIC;
CREATE TRIGGER m24_terskel_vakt
    BEFORE UPDATE OR DELETE ON leverandorterskel
    FOR EACH ROW EXECUTE FUNCTION m24_terskel_vakt();
CREATE TRIGGER m24_terskel_ingen_truncate
    BEFORE TRUNCATE ON leverandorterskel
    EXECUTE FUNCTION m24_terskel_vakt();

CREATE FUNCTION m24_avtale_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'leveranseavtale: TRUNCATE avvist — en avtale'
            ' avsluttes med begrunnelse, den forsvinner aldri i en'
            ' tabelltømming' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'leveranseavtale: DELETE avvist — en avtale'
            ' avsluttes med begrunnelse. En slettet avtale gjør hver'
            ' måling mot den til et tall uten dom'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- IDENTITETEN OG DET AVTALTE ER FROSSET. En avtale som kunne få ny
    -- `avtalt_verdi` i ettertid ville omskrevet hvert SLA-brudd som
    -- alt var målt mot den — historien ville rettet seg selv.
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.avtale_id IS DISTINCT FROM OLD.avtale_id
       OR NEW.leverandor_id IS DISTINCT FROM OLD.leverandor_id
       OR NEW.ytelse IS DISTINCT FROM OLD.ytelse
       OR NEW.sla_type IS DISTINCT FROM OLD.sla_type
       OR NEW.avtalt_verdi IS DISTINCT FROM OLD.avtalt_verdi
       OR NEW.avtalt_pris_ore IS DISTINCT FROM OLD.avtalt_pris_ore
       OR NEW.gyldig_fra IS DISTINCT FROM OLD.gyldig_fra
       OR NEW.gyldig_til IS DISTINCT FROM OLD.gyldig_til
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'leveranseavtale: det AVTALTE er frosset — en ny'
            ' avtale registreres, den gamle skrives ikke om'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- EN AVSLUTTET AVTALE ÅPNES IKKE IGJEN.
    IF OLD.status = 'avsluttet' AND NEW.status = 'aktiv' THEN
        RAISE EXCEPTION 'leveranseavtale: en avsluttet avtale gjenåpnes'
            ' ikke — en ny avtale registreres'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'avsluttet' AND OLD.status = 'aktiv' THEN
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL OR NEW.avsluttet_av IS DISTINCT FROM v_aktor THEN
            RAISE EXCEPTION 'leveranseavtale: avsluttet_av (%) er ikke'
                ' aktøren som avslutter (%)',
                coalesce(NEW.avsluttet_av, '<null>'),
                coalesce(v_aktor, '<ingen>')
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m24_avtale_vakt() FROM PUBLIC;
CREATE TRIGGER m24_avtale_vakt
    BEFORE UPDATE OR DELETE ON leveranseavtale
    FOR EACH ROW EXECUTE FUNCTION m24_avtale_vakt();
CREATE TRIGGER m24_avtale_ingen_truncate
    BEFORE TRUNCATE ON leveranseavtale
    EXECUTE FUNCTION m24_avtale_vakt();

-- DOM 2, HÅNDHEVET I BASEN OG IKKE BARE I DØREN: en måling utenfor
-- avtalens vindu er et tall uten dom. Vakten står her fordi en CHECK
-- ikke kan slå opp i en annen tabell — og fordi en regel som bare
-- fantes i døren ville vært borte i det noen skrev direkte.
CREATE FUNCTION m24_leveranse_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_a RECORD;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'leveranse: TRUNCATE avvist — en tømt'
            ' målingstabell gjør hver SLA-vurdering til en påstand'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'leveranse: DELETE avvist — en registrert måling'
            ' forsvinner ikke fordi den ble ubehagelig'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'leveranse: UPDATE avvist — raden er append-only.'
            ' En feilført måling rettes med en ny måling, ikke ved å'
            ' skrive om den gamle'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT a.gyldig_fra, a.gyldig_til, a.status INTO v_a
      FROM public.leveranseavtale a
     WHERE a.tenant = NEW.tenant AND a.avtale_id = NEW.avtale_id;
    IF NOT FOUND THEN
        -- Fremmednøkkelen fanger dette òg; vakten sier det med ord.
        RAISE EXCEPTION 'leveranse: avtalen finnes ikke — en måling uten'
            ' avtale er et tall uten dom'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF NEW.levert < v_a.gyldig_fra OR NEW.levert > v_a.gyldig_til THEN
        RAISE EXCEPTION 'leveranse: levert % ligger utenfor avtalens'
            ' gyldighet (% til %) — en måling mot en avtale som ikke'
            ' gjaldt den dagen er et tall uten dom',
            NEW.levert, v_a.gyldig_fra, v_a.gyldig_til
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m24_leveranse_vakt() FROM PUBLIC;
CREATE TRIGGER m24_leveranse_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON leveranse
    FOR EACH ROW EXECUTE FUNCTION m24_leveranse_vakt();
CREATE TRIGGER m24_leveranse_ingen_truncate
    BEFORE TRUNCATE ON leveranse
    EXECUTE FUNCTION m24_leveranse_vakt();

CREATE FUNCTION m24_part_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'leverandorpart: TRUNCATE avvist — en leverandør'
            ' deaktiveres, den tømmes ikke bort'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'leverandorpart: DELETE avvist — sett aktiv til'
            ' false. En slettet leverandør ville tatt avtalehistorikken'
            ' med seg' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.leverandor_id IS DISTINCT FROM OLD.leverandor_id
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'leverandorpart: identiteten er frosset'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m24_part_vakt() FROM PUBLIC;
CREATE TRIGGER m24_part_vakt
    BEFORE UPDATE OR DELETE ON leverandorpart
    FOR EACH ROW EXECUTE FUNCTION m24_part_vakt();
CREATE TRIGGER m24_part_ingen_truncate
    BEFORE TRUNCATE ON leverandorpart
    EXECUTE FUNCTION m24_part_vakt();

CREATE FUNCTION m24_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    -- Her er armen IKKE kosmetisk: uten den faller TG_OP='TRUNCATE'
    -- glatt gjennom til RETURN NEW, og tømmingen skjer. En trigger som
    -- heter `ingen_truncate` og slipper TRUNCATE igjennom er verre enn
    -- ingen, fordi den leses som beskyttelse. (CodeRabbit på 104.)
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'leverandorfunn: TRUNCATE avvist — et funn'
            ' lukkes, det tømmes ikke bort. En tom funntabell ser ut'
            ' som en ren natt' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'leverandorfunn: DELETE avvist — et funn lukkes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.tenant IS DISTINCT FROM OLD.tenant
           OR NEW.avtale_id IS DISTINCT FROM OLD.avtale_id
           OR NEW.funntype IS DISTINCT FROM OLD.funntype
           OR NEW.forst_sett IS DISTINCT FROM OLD.forst_sett THEN
            RAISE EXCEPTION 'leverandorfunn: identiteten og førstegangen'
                ' er frosset' USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m24_funn_vakt() FROM PUBLIC;
CREATE TRIGGER m24_funn_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON leverandorfunn
    FOR EACH ROW EXECUTE FUNCTION m24_funn_vakt();
CREATE TRIGGER m24_funn_ingen_truncate
    BEFORE TRUNCATE ON leverandorfunn
    EXECUTE FUNCTION m24_funn_vakt();


-- ------------------------------------------------------------
-- 2b. RLS og rettigheter.
-- ------------------------------------------------------------
ALTER TABLE leverandorterskel ENABLE ROW LEVEL SECURITY;
ALTER TABLE leverandorterskel FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON leverandorterskel
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE leverandorpart ENABLE ROW LEVEL SECURITY;
ALTER TABLE leverandorpart FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON leverandorpart
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE leveranseavtale ENABLE ROW LEVEL SECURITY;
ALTER TABLE leveranseavtale FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON leveranseavtale
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — tre gjerder, som i
-- 100–104: bare dørenes eier, bare SELECT, bare uten tenantkontekst.
-- Sveipen må vite HVILKE tenanter som har avtaler; den får ikke lov til
-- noe mer enn det.
CREATE POLICY m24_sveip_tenantliste ON leveranseavtale
    FOR SELECT TO disponit_leverandor_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

ALTER TABLE leveranse ENABLE ROW LEVEL SECURITY;
ALTER TABLE leveranse FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON leveranse
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE leverandorfunn ENABLE ROW LEVEL SECURITY;
ALTER TABLE leverandorfunn FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON leverandorfunn
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

GRANT SELECT, INSERT, UPDATE ON leverandorterskel
    TO disponit_leverandor_eier;
GRANT SELECT, INSERT, UPDATE ON leverandorpart
    TO disponit_leverandor_eier;
GRANT SELECT, INSERT, UPDATE ON leveranseavtale
    TO disponit_leverandor_eier;
-- `leveranse` HAR VERKEN UPDATE ELLER DELETE — den er append-only helt
-- ned til grantet, ikke bare i vakten.
GRANT SELECT, INSERT ON leveranse TO disponit_leverandor_eier;
GRANT SELECT, INSERT, UPDATE ON leverandorfunn
    TO disponit_leverandor_eier;
GRANT INSERT ON revisjonslogg TO disponit_leverandor_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_leverandor_eier;
RESET ROLE;


-- ------------------------------------------------------------
-- 3. Dørene. SECURITY DEFINER, eid av `disponit_leverandor_eier`,
--    SP-1 (`krev_tenantkontekst`) først i hver tenantbundet definer.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_leverandor_eier;

-- DOM 4: RETNINGEN ER EN LUKKET TABELL.
--
-- Dette er modulens skarpeste enkeltfunksjon. Et brudd regnet med feil
-- fortegn er STILLE — det ser ut som at alt er i orden, og en
-- leverandør som leverer for dårlig går uoppdaget. Derfor: én arm per
-- type, og en UKJENT TYPE ER EN EXCEPTION, ikke `false`. Et `ELSE
-- RETURN false` ville gjort hver framtidig SLA-type usynlig fra dagen
-- den ble lagt til i CHECKen og glemt her.
--
-- IMMUTABLE fordi den bare avhenger av argumentene; da kan planleggeren
-- bruke den i indekserte uttrykk og i sveipens kandidatspørring uten å
-- kalle den på nytt per rad.
CREATE FUNCTION m24_bryter_sla(p_sla_type TEXT, p_avtalt INT,
                               p_faktisk INT)
RETURNS BOOLEAN LANGUAGE plpgsql IMMUTABLE
SET search_path = pg_catalog AS $$
BEGIN
    IF p_avtalt IS NULL OR p_faktisk IS NULL THEN
        -- EN MÅLING UTEN TALL ER INGEN MÅLING. `NULL > NULL` er NULL,
        -- og en NULL som ble lest som «ikke brudd» er nøyaktig den
        -- stille feilen denne funksjonen finnes for å hindre.
        RAISE EXCEPTION 'm24_bryter_sla: avtalt og faktisk verdi må'
            ' begge finnes — en måling uten tall er ingen måling'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- LAVERE ER BEDRE: brudd når den faktiske verdien er HØYERE.
    IF p_sla_type IN ('leveringstid_dogn', 'responstid_timer',
                      'feilrate_promille') THEN
        RETURN p_faktisk > p_avtalt;
    END IF;
    -- HØYERE ER BEDRE: brudd når den faktiske verdien er LAVERE.
    IF p_sla_type = 'oppetid_promille' THEN
        RETURN p_faktisk < p_avtalt;
    END IF;
    RAISE EXCEPTION 'm24_bryter_sla: ukjent sla_type «%» — en type uten'
        ' retning kan ikke vurderes, og et stille «ikke brudd» ville'
        ' gjort hver framtidig type usynlig', p_sla_type
        USING ERRCODE = 'invalid_parameter_value';
END $$;
REVOKE ALL ON FUNCTION m24_bryter_sla(TEXT, INT, INT) FROM PUBLIC;

-- Evidenskjeden, ett sted. BELØP STÅR ALDRI HER — det er tenantens
-- forretningsdata, og evidenskjeden skal gjenfinne HANDLINGEN uten å
-- arkivere pengestrømmen på nytt et sted til (101s dom, ordrett, som i
-- 104).
CREATE FUNCTION m24_evidens(p_tenant TEXT, p_subjekt_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm24_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm24_leverandor', 'handling', p_handling,
        'subjekt_id', p_subjekt_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm24_leverandor',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:leverandorregister', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m24_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- TERSKELDØREN. DOM 3: tersklene er tenantens.
CREATE FUNCTION m24_sett_terskler(
    p_tenant TEXT, p_prisstigning_promille INT, p_sla_brudd_grense INT,
    p_avtale_varsel_dogn INT, p_maling_stillhet_dogn INT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm24_sett_terskler');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.leverandorterskel
        (tenant, prisstigning_promille, sla_brudd_grense,
         avtale_varsel_dogn, maling_stillhet_dogn, versjon, oppdatert,
         oppdatert_av)
    VALUES (p_tenant, p_prisstigning_promille, p_sla_brudd_grense,
            p_avtale_varsel_dogn, p_maling_stillhet_dogn, 1, now(),
            p_aktor)
    ON CONFLICT (tenant) DO UPDATE
        SET prisstigning_promille = EXCLUDED.prisstigning_promille,
            sla_brudd_grense = EXCLUDED.sla_brudd_grense,
            avtale_varsel_dogn = EXCLUDED.avtale_varsel_dogn,
            maling_stillhet_dogn = EXCLUDED.maling_stillhet_dogn,
            versjon = leverandorterskel.versjon + 1,
            oppdatert = now(), oppdatert_av = p_aktor
    RETURNING versjon INTO v_versjon;
    PERFORM public.m24_evidens(
        p_tenant, '00000000-0000-0000-0000-000000000000'::uuid,
        'terskler.satt', p_aktor,
        jsonb_build_object('versjon', v_versjon));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION m24_sett_terskler(TEXT, INT, INT, INT, INT, TEXT)
    FROM PUBLIC;

CREATE FUNCTION m24_registrer_leverandor(
    p_tenant TEXT, p_leverandor_id UUID, p_navn TEXT, p_ekstern_ref TEXT,
    p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm24_registrer_leverandor');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.leverandorpart
        (tenant, leverandor_id, navn, ekstern_ref, opprettet_av)
    VALUES (p_tenant, p_leverandor_id, btrim(p_navn),
            nullif(btrim(coalesce(p_ekstern_ref, '')), ''), p_aktor)
        ON CONFLICT (tenant, leverandor_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;                               -- SP-2: stille ja
    END IF;
    PERFORM public.m24_evidens(p_tenant, p_leverandor_id,
                               'leverandor.registrert', p_aktor,
                               jsonb_build_object());
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m24_registrer_leverandor(TEXT, UUID, TEXT, TEXT,
    TEXT) FROM PUBLIC;

-- AVTALEDØREN. Her settes DOMMEN alle senere målinger vurderes mot.
CREATE FUNCTION m24_registrer_avtale(
    p_tenant TEXT, p_avtale_id UUID, p_leverandor_id UUID, p_ytelse TEXT,
    p_sla_type TEXT, p_avtalt_verdi INT, p_avtalt_pris_ore BIGINT,
    p_gyldig_fra DATE, p_gyldig_til DATE, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm24_registrer_avtale');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    -- RETNINGEN SLÅS OPP HER, FØR raden finnes. En avtale med en
    -- sla_type ingen kan vurdere ville stått i registeret og aldri gitt
    -- et funn — den ville sett ut som en avtale som holdes.
    PERFORM public.m24_bryter_sla(p_sla_type, p_avtalt_verdi,
                                  p_avtalt_verdi);
    INSERT INTO public.leveranseavtale
        (tenant, avtale_id, leverandor_id, ytelse, sla_type,
         avtalt_verdi, avtalt_pris_ore, gyldig_fra, gyldig_til,
         opprettet_av)
    VALUES (p_tenant, p_avtale_id, p_leverandor_id, btrim(p_ytelse),
            p_sla_type, p_avtalt_verdi, p_avtalt_pris_ore, p_gyldig_fra,
            p_gyldig_til, p_aktor)
        ON CONFLICT (tenant, avtale_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;                               -- SP-2: stille ja
    END IF;
    PERFORM public.m24_evidens(
        p_tenant, p_avtale_id, 'avtale.registrert', p_aktor,
        jsonb_build_object('sla_type', p_sla_type));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m24_registrer_avtale(TEXT, UUID, UUID, TEXT, TEXT,
    INT, BIGINT, DATE, DATE, TEXT) FROM PUBLIC;

-- MÅLINGSDØREN. DOM 2: mot en avtalt verdi, innenfor avtalens vindu.
-- Vakten håndhever begge; døren sier det med en setning et menneske kan
-- handle på.
CREATE FUNCTION m24_registrer_leveranse(
    p_tenant TEXT, p_leveranse_id UUID, p_avtale_id UUID, p_levert DATE,
    p_faktisk_verdi INT, p_faktisk_pris_ore BIGINT, p_referanse TEXT,
    p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_a RECORD; v_rader INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm24_registrer_leveranse');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT a.status, a.sla_type, a.avtalt_verdi INTO v_a
      FROM public.leveranseavtale a
     WHERE a.tenant = p_tenant AND a.avtale_id = p_avtale_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm24_registrer_leveranse: avtalen finnes ikke —'
            ' en måling uten avtale er et tall uten dom'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_a.status <> 'aktiv' THEN
        RAISE EXCEPTION 'm24_registrer_leveranse: avtalen er %, og en'
            ' måling mot en avsluttet avtale er et tall uten dom',
            v_a.status USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.leveranse
        (tenant, leveranse_id, avtale_id, levert, faktisk_verdi,
         faktisk_pris_ore, referanse, registrert_av)
    VALUES (p_tenant, p_leveranse_id, p_avtale_id, p_levert,
            p_faktisk_verdi, p_faktisk_pris_ore,
            nullif(btrim(coalesce(p_referanse, '')), ''), p_aktor)
        ON CONFLICT (tenant, leveranse_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;                               -- SP-2: stille ja
    END IF;
    -- EVIDENSEN BÆRER OM DET VAR ET BRUDD, ikke tallene bak. Om det var
    -- et brudd er en HANDLINGENS egenskap; verdiene står i registeret.
    PERFORM public.m24_evidens(
        p_tenant, p_avtale_id, 'leveranse.registrert', p_aktor,
        jsonb_build_object('brudd', public.m24_bryter_sla(
            v_a.sla_type, v_a.avtalt_verdi, p_faktisk_verdi)));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m24_registrer_leveranse(TEXT, UUID, UUID, DATE,
    INT, BIGINT, TEXT, TEXT) FROM PUBLIC;

-- AVSLUTNINGSDØREN. Å avslutte en avtale uten å si hvorfor er den ene
-- handlingen ingen kan etterprøve senere.
CREATE FUNCTION m24_avslutt_avtale(
    p_tenant TEXT, p_avtale_id UUID, p_begrunnelse TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm24_avslutt_avtale');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_begrunnelse IS NULL OR p_begrunnelse !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm24_avslutt_avtale: en avslutning uten'
            ' begrunnelse er en beslutning ingen kan etterprøve'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- FOR UPDATE, som i målingsdøren. Uten låsen kunne to samtidige
    -- avslutninger begge lese 'aktiv' og begge skrive en evidensrad,
    -- mens bare den ene UPDATE-en traff en rad. (CodeRabbits funn på
    -- 104s ettergivelsesdør, samme klasse.)
    SELECT a.status INTO v_status FROM public.leveranseavtale a
     WHERE a.tenant = p_tenant AND a.avtale_id = p_avtale_id
       FOR UPDATE;
    IF v_status IS NULL THEN
        RAISE EXCEPTION 'm24_avslutt_avtale: avtalen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_status <> 'aktiv' THEN
        RETURN false;                               -- stille ja
    END IF;
    UPDATE public.leveranseavtale
       SET status = 'avsluttet', avsluttet_ts = now(),
           avsluttet_av = p_aktor,
           avsluttet_begrunnelse = btrim(p_begrunnelse)
     WHERE tenant = p_tenant AND avtale_id = p_avtale_id
       AND status = 'aktiv';
    -- FUNNENE LUKKES MED AVTALEN. Et åpent funn om en avtale som ikke
    -- lenger finnes er et varsel ingen kan gjøre noe med.
    UPDATE public.leverandorfunn
       SET apen = false, lukket_ts = now()
     WHERE tenant = p_tenant AND avtale_id = p_avtale_id AND apen;
    PERFORM public.m24_evidens(p_tenant, p_avtale_id, 'avtale.avsluttet',
                               p_aktor, jsonb_build_object());
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m24_avslutt_avtale(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

RESET ROLE;


-- ------------------------------------------------------------
-- 3b. Lesedørene.
--
--     INGEN AV DEM RETURNERER EN PRIS Å SETTE. `prisavvik_promille` er
--     et AVVIK MELLOM TO MÅLTE TALL — hva vi avtalte, og hva vi
--     faktisk betalte. Det er oppdagelsen katalogen legger til M-24.
--     Forslaget om ny pris ligger hos M-26, og v1 holder seg på sin
--     side av snittet: det finnes ingen kolonne, ingen dør og ingen
--     returverdi her som er et prisforslag.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_leverandor_eier;

CREATE FUNCTION m24_leverandorstatus(p_tenant TEXT)
RETURNS TABLE(aktive_avtaler INT, leverandorer INT, apne_funn INT,
              avtaler_med_brudd INT, avtalt_ore BIGINT,
              har_terskel BOOLEAN, terskelversjon INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm24_leverandorstatus');
    RETURN QUERY
    SELECT (SELECT count(*)::int FROM public.leveranseavtale a
             WHERE a.tenant = p_tenant AND a.status = 'aktiv'),
           (SELECT count(*)::int FROM public.leverandorpart l
             WHERE l.tenant = p_tenant AND l.aktiv),
           (SELECT count(*)::int FROM public.leverandorfunn f
             WHERE f.tenant = p_tenant AND f.apen),
           (SELECT count(DISTINCT f.avtale_id)::int
              FROM public.leverandorfunn f
             WHERE f.tenant = p_tenant AND f.apen
               AND f.funntype = 'sla_brudd'),
           -- SUM OVER BIGINT GIR NUMERIC. Uten castet her faller
           -- RETURNS TABLE(... BIGINT) på typen — lærdommen fra 101.
           (SELECT coalesce(sum(a.avtalt_pris_ore), 0)::bigint
              FROM public.leveranseavtale a
             WHERE a.tenant = p_tenant AND a.status = 'aktiv'),
           EXISTS (SELECT 1 FROM public.leverandorterskel t
                    WHERE t.tenant = p_tenant),
           (SELECT t.versjon FROM public.leverandorterskel t
             WHERE t.tenant = p_tenant);
END $$;
REVOKE ALL ON FUNCTION m24_leverandorstatus(TEXT) FROM PUBLIC;

-- SLA-OVERSIKTEN. ALLE FIRE TYPENE STÅR I SVARET, også de tenanten
-- ikke bruker — en oversikt som endret form fra dag til dag kan ingen
-- sammenligne over tid (104s aldersfordeling, samme dom).
--
-- ALLE TRE KOLONNENE MÅLER DET SAMME UTVALGET: aktive avtaler. Første
-- utgave telte avtalene som var aktive NÅ, men målingene og bruddene fra
-- ALLE avtaler, også avsluttede — og da kunne en rad stått med «0
-- avtaler, 12 målinger, 1 brudd». Et tall om ett utvalg ved siden av to
-- tall om et annet er nøyaktig den slags rad ingen kan handle på.
-- (CodeRabbit, PR M-24.)
--
-- Historikken forsvinner ikke: den avsluttede avtalen står fortsatt i
-- `m24_avtalene` med sine målinger og sitt bruddtall. Det er DENNE
-- oversikten som svarer på «hva måler vi nå».
CREATE FUNCTION m24_slaoversikt(p_tenant TEXT)
RETURNS TABLE(sla_type TEXT, avtaler INT, malinger INT, brudd INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm24_slaoversikt');
    RETURN QUERY
    SELECT s.t,
           (SELECT count(*)::int FROM public.leveranseavtale a
             WHERE a.tenant = p_tenant AND a.status = 'aktiv'
               AND a.sla_type = s.t),
           (SELECT count(*)::int FROM public.leveranse v
              JOIN public.leveranseavtale a
                ON a.tenant = v.tenant AND a.avtale_id = v.avtale_id
             WHERE v.tenant = p_tenant AND a.sla_type = s.t
               AND a.status = 'aktiv'),
           (SELECT count(*)::int FROM public.leveranse v
              JOIN public.leveranseavtale a
                ON a.tenant = v.tenant AND a.avtale_id = v.avtale_id
             WHERE v.tenant = p_tenant AND a.sla_type = s.t
               AND a.status = 'aktiv'
               AND public.m24_bryter_sla(a.sla_type, a.avtalt_verdi,
                                         v.faktisk_verdi))
      FROM (VALUES ('leveringstid_dogn'), ('responstid_timer'),
                   ('feilrate_promille'), ('oppetid_promille'))
           AS s(t);
END $$;
REVOKE ALL ON FUNCTION m24_slaoversikt(TEXT) FROM PUBLIC;

-- AVTALELISTEN. Regnet i BASEN, i samme skann som raden: en flate som
-- regnet bruddene fra de viste radene ville tegnet et tall om et utvalg
-- og kalt det leverandørforholdet.
CREATE FUNCTION m24_avtalene(p_tenant TEXT, p_grense INT)
RETURNS TABLE(avtale_id UUID, leverandor_id UUID, leverandor_navn TEXT,
              leverandor_aktiv BOOLEAN, ytelse TEXT, sla_type TEXT,
              avtalt_verdi INT, avtalt_pris_ore BIGINT,
              gyldig_fra DATE, gyldig_til DATE, status TEXT,
              malinger INT, brudd INT, siste_levert DATE,
              siste_faktisk_verdi INT, siste_faktisk_pris_ore BIGINT,
              prisavvik_promille INT, dogn_til_utlop INT,
              apne_funn TEXT[])
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm24_avtalene');
    RETURN QUERY
    SELECT a.avtale_id, a.leverandor_id, l.navn, l.aktiv, a.ytelse,
           a.sla_type, a.avtalt_verdi, a.avtalt_pris_ore, a.gyldig_fra,
           a.gyldig_til, a.status,
           m.antall, m.brudd, s.levert, s.faktisk_verdi,
           s.faktisk_pris_ore,
           -- PRISAVVIKET I PROMILLE, heltallsaritmetikk. Delt på null
           -- er ingen prosentsats: en avtalt pris på null gir NULL, og
           -- NULL er det ærlige svaret på «hvor mange promille over
           -- null er dette».
           CASE WHEN s.faktisk_pris_ore IS NULL
                  OR a.avtalt_pris_ore = 0 THEN NULL
                ELSE ((s.faktisk_pris_ore - a.avtalt_pris_ore) * 1000
                      / a.avtalt_pris_ore)::int END,
           (a.gyldig_til - current_date)::int,
           coalesce(f.typer, ARRAY[]::TEXT[])
      FROM public.leveranseavtale a
      JOIN public.leverandorpart l
        ON l.tenant = a.tenant AND l.leverandor_id = a.leverandor_id
      CROSS JOIN LATERAL (
            SELECT count(*)::int AS antall,
                   count(*) FILTER (WHERE public.m24_bryter_sla(
                       a.sla_type, a.avtalt_verdi, v.faktisk_verdi))::int
                       AS brudd
              FROM public.leveranse v
             WHERE v.tenant = a.tenant AND v.avtale_id = a.avtale_id) m
      LEFT JOIN LATERAL (
            SELECT v.levert, v.faktisk_verdi, v.faktisk_pris_ore
              FROM public.leveranse v
             WHERE v.tenant = a.tenant AND v.avtale_id = a.avtale_id
             ORDER BY v.levert DESC, v.registrert DESC
             LIMIT 1) s ON true
      LEFT JOIN LATERAL (
            SELECT array_agg(ff.funntype ORDER BY ff.funntype) AS typer
              FROM public.leverandorfunn ff
             WHERE ff.tenant = a.tenant AND ff.avtale_id = a.avtale_id
               AND ff.apen) f ON true
     WHERE a.tenant = p_tenant
     -- AKTIVE FØRST, DERETTER DE MED FLEST BRUDD. Avkortingen skal ta
     -- det som betyr minst, ikke det som tilfeldigvis kom sist.
     ORDER BY (a.status = 'aktiv') DESC, m.brudd DESC, a.gyldig_til,
              a.avtale_id
     LIMIT greatest(least(coalesce(p_grense, 200), 1000), 1);
END $$;
REVOKE ALL ON FUNCTION m24_avtalene(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m24_leveransene(p_tenant TEXT, p_avtale_id UUID)
RETURNS TABLE(leveranse_id UUID, levert DATE, faktisk_verdi INT,
              faktisk_pris_ore BIGINT, referanse TEXT, brudd BOOLEAN,
              registrert TIMESTAMPTZ, registrert_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm24_leveransene');
    RETURN QUERY
    SELECT v.leveranse_id, v.levert, v.faktisk_verdi, v.faktisk_pris_ore,
           v.referanse,
           public.m24_bryter_sla(a.sla_type, a.avtalt_verdi,
                                 v.faktisk_verdi),
           v.registrert, v.registrert_av
      FROM public.leveranse v
      JOIN public.leveranseavtale a
        ON a.tenant = v.tenant AND a.avtale_id = v.avtale_id
     WHERE v.tenant = p_tenant AND v.avtale_id = p_avtale_id
     ORDER BY v.levert DESC, v.registrert DESC
     LIMIT 500;
END $$;
REVOKE ALL ON FUNCTION m24_leveransene(TEXT, UUID) FROM PUBLIC;

CREATE FUNCTION m24_tersklene(p_tenant TEXT)
RETURNS TABLE(prisstigning_promille INT, sla_brudd_grense INT,
              avtale_varsel_dogn INT, maling_stillhet_dogn INT,
              versjon INT, oppdatert TIMESTAMPTZ, oppdatert_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm24_tersklene');
    RETURN QUERY
    SELECT t.prisstigning_promille, t.sla_brudd_grense,
           t.avtale_varsel_dogn, t.maling_stillhet_dogn, t.versjon,
           t.oppdatert, t.oppdatert_av
      FROM public.leverandorterskel t WHERE t.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m24_tersklene(TEXT) FROM PUBLIC;

CREATE FUNCTION m24_leverandorene(p_tenant TEXT)
RETURNS TABLE(leverandor_id UUID, navn TEXT, ekstern_ref TEXT,
              aktiv BOOLEAN, aktive_avtaler INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm24_leverandorene');
    RETURN QUERY
    SELECT l.leverandor_id, l.navn, l.ekstern_ref, l.aktiv,
           (SELECT count(*)::int FROM public.leveranseavtale a
             WHERE a.tenant = l.tenant
               AND a.leverandor_id = l.leverandor_id
               AND a.status = 'aktiv')
      FROM public.leverandorpart l
     WHERE l.tenant = p_tenant
     ORDER BY l.aktiv DESC, l.navn
     LIMIT 500;
END $$;
REVOKE ALL ON FUNCTION m24_leverandorene(TEXT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Sveipens kandidater. TERSKLENE LESES FRA TABELLEN — funksjonen
--    tar ingen terskelparameter, og det er DOM 3 i kode.
-- ------------------------------------------------------------
CREATE FUNCTION m24_funnkandidater(p_tenant TEXT, p_dag DATE)
RETURNS TABLE(avtale_id UUID, funntype TEXT, antall INT,
              siste_leveranse_id UUID, over_grense INT,
              terskelversjon INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_t RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm24_funnkandidater');
    SELECT * INTO v_t FROM public.leverandorterskel t
     WHERE t.tenant = p_tenant;
    IF NOT FOUND THEN
        -- 0. INGEN TERSKEL: tenanten har aktive avtaler, men ingen
        --    grenser å måle dem mot. Da vet ingen hva «for dyrt» eller
        --    «for dårlig» betyr her, og hver av de andre funntypene
        --    ville vært en gjetning. Funnet er PER AVTALE fordi det er
        --    på avtalen et menneske faktisk ser det.
        RETURN QUERY
        SELECT a.avtale_id, 'ingen_terskel'::text, 0, NULL::uuid,
               NULL::int, NULL::int
          FROM public.leveranseavtale a
         WHERE a.tenant = p_tenant AND a.status = 'aktiv';
        RETURN;
    END IF;

    RETURN QUERY
    -- 1. SLA-BRUDD: antall bruddleveranser har nådd tenantens grense.
    --    Dette er modulens hovedfunn — det er her «et SLA-brudd er et
    --    funn, ikke en stille rad i en målingstabell» blir sant.
    --    Sveipen TERMINERER INGEN AVTALE; den sier fra.
    SELECT a.avtale_id, 'sla_brudd'::text, b.antall, b.siste,
           (b.antall - v_t.sla_brudd_grense)::int, v_t.versjon
      FROM public.leveranseavtale a
      CROSS JOIN LATERAL (
            SELECT count(*)::int AS antall,
                   (SELECT v2.leveranse_id FROM public.leveranse v2
                     WHERE v2.tenant = a.tenant
                       AND v2.avtale_id = a.avtale_id
                       AND public.m24_bryter_sla(a.sla_type,
                               a.avtalt_verdi, v2.faktisk_verdi)
                     ORDER BY v2.levert DESC, v2.registrert DESC
                     LIMIT 1) AS siste
              FROM public.leveranse v
             WHERE v.tenant = a.tenant AND v.avtale_id = a.avtale_id
               AND public.m24_bryter_sla(a.sla_type, a.avtalt_verdi,
                                         v.faktisk_verdi)) b
     WHERE a.tenant = p_tenant AND a.status = 'aktiv'
       AND b.antall >= v_t.sla_brudd_grense
    UNION ALL
    -- 2. PRIS OVER TERSKEL: den siste målte prisen ligger mer enn
    --    tenantens promille over den avtalte. HELTALLSARITMETIKK og
    --    ingen divisjon i sammenligningen: `faktisk * 1000 > avtalt *
    --    (1000 + promille)`. En terskel som «nesten» er passert er
    --    ingen terskel — og en avrunding ville avgjort hvilken.
    --
    --    OVER_GRENSE ER NULL NÅR DEN AVTALTE PRISEN ER NULL. «Hvor
    --    mange promille over null» har intet svar, og et oppdiktet tall
    --    ville sett ut som en måling.
    SELECT a.avtale_id, 'pris_over_terskel'::text, 1, s.leveranse_id,
           CASE WHEN a.avtalt_pris_ore = 0 THEN NULL
                ELSE (((s.faktisk_pris_ore - a.avtalt_pris_ore) * 1000
                       / a.avtalt_pris_ore)
                      - v_t.prisstigning_promille)::int END,
           v_t.versjon
      FROM public.leveranseavtale a
      JOIN LATERAL (
            SELECT v.leveranse_id, v.faktisk_pris_ore
              FROM public.leveranse v
             WHERE v.tenant = a.tenant AND v.avtale_id = a.avtale_id
             ORDER BY v.levert DESC, v.registrert DESC
             LIMIT 1) s ON true
     WHERE a.tenant = p_tenant AND a.status = 'aktiv'
       AND s.faktisk_pris_ore * 1000
           > a.avtalt_pris_ore * (1000 + v_t.prisstigning_promille)
    UNION ALL
    -- 3. AVTALE UTLØPT ELLER NÆR UTLØP: en aktiv avtale hvis gyldighet
    --    er passert, eller som utløper innen tenantens varselvindu. En
    --    avtale som gikk ut uten at noen merket det, er et forhold som
    --    fortsetter uten dom.
    --
    --    `over_grense` er DØGN OVER UTLØP: positivt betyr utløpt,
    --    negativt betyr så mange døgn igjen.
    SELECT a.avtale_id, 'avtale_utlopt'::text, 0, NULL::uuid,
           (p_dag - a.gyldig_til)::int, v_t.versjon
      FROM public.leveranseavtale a
     WHERE a.tenant = p_tenant AND a.status = 'aktiv'
       AND a.gyldig_til <= p_dag + v_t.avtale_varsel_dogn
    UNION ALL
    -- 4. AVTALE UTEN MÅLING: en aktiv avtale som ikke har fått en
    --    eneste måling på tenantens stillhetsgrense. VI BETALER FOR NOE
    --    INGEN HAR MÅLT — og en avtale uten målinger vil aldri gi et
    --    SLA-brudd, uansett hvor dårlig leveransen er.
    SELECT a.avtale_id, 'avtale_uten_maling'::text, 0, NULL::uuid,
           (p_dag - greatest(a.gyldig_fra, coalesce(m.siste, a.gyldig_fra))
            - v_t.maling_stillhet_dogn)::int,
           v_t.versjon
      FROM public.leveranseavtale a
      LEFT JOIN LATERAL (
            SELECT max(v.levert) AS siste FROM public.leveranse v
             WHERE v.tenant = a.tenant AND v.avtale_id = a.avtale_id) m
        ON true
     WHERE a.tenant = p_tenant AND a.status = 'aktiv'
       AND a.gyldig_fra <= p_dag
       AND p_dag - greatest(a.gyldig_fra,
                            coalesce(m.siste, a.gyldig_fra))
           > v_t.maling_stillhet_dogn;
END $$;
REVOKE ALL ON FUNCTION m24_funnkandidater(TEXT, DATE) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 4b. Sveipen. KRYSS-TENANT, egen LOGIN-rolle, egen timer.
--
--     SVEIPEN BETALER INGEN OG AVSLUTTER INGEN AVTALE. Den kunne,
--     teknisk — den vet hvilke avtaler som er utløpt og hvilke priser
--     som har steget. Men en betaling er umulig å angre, og en jobb som
--     betalte om natten er nøyaktig den fullmakten v1 ikke gir seg
--     selv. Den skriver funn; et menneske handler.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_leverandor_eier;

CREATE FUNCTION m24_sveip_leverandorer(p_grense INT DEFAULT 500)
RETURNS TABLE(tenanter INT, nye INT, oppdaterte INT, lukkede INT,
              avkortet INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_n INT; v_grense INT;
        v_dag DATE; v_naa TIMESTAMPTZ;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm24_sveip_leverandorer: sveipen er KRYSS-TENANT'
            ' og kjøres uten tenantkontekst — en kaller som har satt en'
            ' kontekst ber om noe annet enn det denne funksjonen gjør'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_grense := greatest(least(coalesce(p_grense, 500), 5000), 1);
    v_dag := current_date;
    v_naa := now();
    tenanter := 0; nye := 0; oppdaterte := 0; lukkede := 0; avkortet := 0;
    SELECT array_agg(DISTINCT a.tenant ORDER BY a.tenant) INTO v_tenanter
      FROM public.leveranseavtale a;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        tenanter := tenanter + 1;

        UPDATE public.leverandorfunn lf
           SET sist_sett_sveip = v_naa, apen = true, lukket_ts = NULL,
               antall = kand.antall,
               siste_leveranse_id = kand.siste_leveranse_id,
               over_grense = kand.over_grense,
               terskelversjon = kand.terskelversjon
          FROM public.m24_funnkandidater(v_t, v_dag) kand
         WHERE lf.tenant = v_t AND lf.avtale_id = kand.avtale_id
           AND lf.funntype = kand.funntype;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        oppdaterte := oppdaterte + v_n;

        INSERT INTO public.leverandorfunn
            (tenant, avtale_id, funntype, antall, siste_leveranse_id,
             over_grense, terskelversjon, forst_sett, sist_sett_sveip,
             apen)
        SELECT v_t, kand.avtale_id, kand.funntype, kand.antall,
               kand.siste_leveranse_id, kand.over_grense,
               kand.terskelversjon, v_naa, v_naa, true
          FROM public.m24_funnkandidater(v_t, v_dag) kand
         WHERE NOT EXISTS (
                SELECT 1 FROM public.leverandorfunn lf
                 WHERE lf.tenant = v_t
                   AND lf.avtale_id = kand.avtale_id
                   AND lf.funntype = kand.funntype)
         ORDER BY coalesce(kand.over_grense, 0) DESC,
                  kand.avtale_id, kand.funntype
         LIMIT v_grense;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        nye := nye + v_n;
        IF v_n = v_grense THEN
            avkortet := avkortet + 1;
        END IF;

        UPDATE public.leverandorfunn lf
           SET apen = false, lukket_ts = v_naa
         WHERE lf.tenant = v_t AND lf.apen
           AND NOT EXISTS (
                SELECT 1 FROM public.m24_funnkandidater(v_t, v_dag) kand
                 WHERE kand.avtale_id = lf.avtale_id
                   AND kand.funntype = lf.funntype);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        lukkede := lukkede + v_n;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m24_sveip_leverandorer(INT) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 5. Rettighetene. `m24_sveip_leverandorer` og `m24_funnkandidater`
--    grantes ingen: den første er sveiperollens, den andre internt ledd.
--
--    SP-7: kjøretidsrollen får INGEN tabellrettigheter, bare EXECUTE
--    på dørene. Registeret nås bare gjennom dem.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_leverandor_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m24_leverandorstatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m24_slaoversikt(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m24_avtalene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m24_leveransene(TEXT, UUID)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m24_tersklene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m24_leverandorene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m24_bryter_sla(TEXT, INT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m24_sett_terskler(TEXT, INT, INT, INT, INT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m24_registrer_leverandor(TEXT, UUID, TEXT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m24_registrer_avtale(TEXT, UUID, UUID, TEXT, TEXT, INT,'
            ' BIGINT, DATE, DATE, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m24_registrer_leveranse(TEXT, UUID, UUID, DATE, INT,'
            ' BIGINT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m24_avslutt_avtale(TEXT, UUID, TEXT, TEXT) TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_leverandorsveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m24_sveip_leverandorer(INT)'
            ' TO disponit_leverandorsveip';
    END IF;
END $$;
RESET ROLE;
