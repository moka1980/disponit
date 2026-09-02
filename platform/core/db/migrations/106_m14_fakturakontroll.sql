-- 106: M-14 fakturakontrollagent v1 — FAKTURAREGISTERET.
-- Fem tenant-skopede tabeller, tolv dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- V1-AVGRENSNINGEN, ORDRETT FRA POLICYEN VI SENDER UT:
-- `bransjemal-tjenestebedrift.yaml` navngir denne modulen som
-- verifikatoren `v_regnskap`, betrodd for `dublettsjekk`,
-- `mva_validert` og `faktura_godkjent` — og bruker de tre til å slippe
-- `faktura.bokfor` gjennom som `modus: auto`.
--
-- v1 BOKFØRER INGENTING OG ATTESTERER INGENTING.
--
-- DOMMEN, OG DEN ER KLYNGENS NYE: klynge 1–3 holdt igjen på å UTFØRE en
-- handling. Her holder vi igjen på å AUTORISERE en. En attestasjon er
-- nettopp det som slipper en automatisk bokføring med penger i andre
-- enden gjennom, og å ta den fullmakten før treffraten under den er
-- målt, er å la modulen definere sin egen troverdighet.
--
-- Det finnes derfor ingen hovedbok her, ingen kontoplan, ingen
-- posteringsdør — og ingen signeringsnøkkel, ingen attestasjonstabell
-- og ingen kolonne som heter `signatur`.
--
-- DOMMENE v1 HVILER PÅ, håndhevet i DATAMODELLEN:
--
--   1. BELØP ER HELTALL I MINSTE ENHET (øre), `BIGINT`, uten unntak, og
--      MVA REGNES I HELTALL MED EN SKREVET AVRUNDINGSREGEL:
--          forventet_mva = (netto * promille + 500) / 1000
--      altså halv-opp. Regelen står HER fordi et flyttall ville gjort
--      «stemmer mva-en» til et spørsmål med to svar, og fordi en
--      avrunding som ikke er skrevet ned er en avrunding ingen kan
--      etterprøve.
--
--   2. MVA-SATSENE ER TENANTENS, og de er DATERTE. En sats leses etter
--      fakturaens DATO, ikke etter dagens dato — ellers ville en
--      satsendring gjort hver gammel faktura gal med tilbakevirkende
--      kraft. En sats kodet inn i modulen ville dessuten vært en
--      fullmakt modulen ga seg selv over et tall staten setter.
--
--   3. DEN EKSAKTE DUBLETTEN KAN IKKE REGISTRERES TO GANGER. Samme
--      leverandør og samme fakturanummer er ÉN faktura, håndhevet av en
--      UNIQUE — den skal ikke kunne betales to ganger fordi noen
--      importerte den fra to kanaler.
--
--      DEN NÆRE DUBLETTEN ER ET FUNN, ikke en nektelse: samme
--      leverandør, samme beløp, samme dato, ULIKT nummer er nøyaktig
--      mønsteret i en dobbeltfakturering — og det er en menneskelig
--      vurdering, ikke en regel basen kan felle.
--
--   4. EN KONTROLL ER APPEND-ONLY. En kontroll som kunne skrives om
--      ville gjort «denne fakturaen er kontrollert» til en påstand.
--
-- GRENSEN MOT M-13 (101): M-13 eier BANKPOSTENE — det som har skjedd på
-- konto. M-14 eier den INNGÅENDE FAKTURAEN — det noen krever av oss. En
-- betalt bankpost uten en kontrollert faktura er et funn å se på, aldri
-- en kobling basen feller selv.
--
-- GRENSEN MOT M-23 (104): M-23 eier det VI krever av noen. Samme form,
-- motsatt retning, to registre.
--
-- GRENSEN MOT M-24 (105): leverandøren er M-24s rad. M-14 bærer en
-- TEKSTREFERANSE og ingen fremmednøkkel, fordi en faktura kan komme fra
-- noen som ennå ikke står i leverandørregisteret — og «ukjent
-- leverandør» er nettopp et funn modulen skal reise, ikke en
-- registrering den skal nekte.
--
-- SVEIPEN HAR SIN EGEN ROLLE OG SIN EGEN TIMER (095/100–105):
-- `disponit_fakturasveip` har nøyaktig ÉN rettighet — EXECUTE på
-- `m14_sveip_fakturaer` — og INGEN tabellrettigheter. Sveipen BOKFØRER
-- INGEN og GODKJENNER INGEN faktura; den skriver FUNN.
--
-- FORMENE ER HUSETS: tabellene eies av migrator, dørene av NOLOGIN-rollen
-- `disponit_faktura_eier`, tenant TEXT + RLS ENABLE+FORCE +
-- `tenant_isolasjon`, SP-1 i hver tenantbundet definer, ingen BYPASSRLS.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_faktura_eier') THEN
        RAISE EXCEPTION 'rollen disponit_faktura_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_faktura_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `mvasats` — DOM 2 I TABELLFORM. Tenantens satser, DATERTE.
--
-- Satsen leses etter fakturaens dato. Uten `gyldig_fra`/`gyldig_til`
-- ville en satsendring gjort hver gammel faktura gal med tilbakevirkende
-- kraft — og et register som retter historien er ikke et register.
CREATE TABLE mvasats (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- Tenantens eget navn på satsen («høy», «lav», «null», «matvarer»).
    -- IKKE et lukket sett: ingen katalog vet hvilke satser et land har
    -- neste år.
    sats_kode TEXT NOT NULL CHECK (sats_kode ~ '[^[:space:]]'),
    -- PROMILLE, ikke prosent: 25 % er 250. Heltall, av samme grunn som
    -- alt annet her — en sats på 12,5 % finnes, og 125 er eksakt.
    promille INT NOT NULL CHECK (promille BETWEEN 0 AND 1000),
    gyldig_fra DATE NOT NULL,
    -- ÅPEN ENDE ER LOVLIG: den gjeldende satsen har ingen sluttdato.
    gyldig_til DATE,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT mvasats_pk PRIMARY KEY (tenant, sats_kode, gyldig_fra),
    CONSTRAINT mvasats_vindu_framover
        CHECK (gyldig_til IS NULL OR gyldig_til >= gyldig_fra)
);
CREATE INDEX mvasats_oppslag ON mvasats (tenant, sats_kode, gyldig_fra DESC);

-- `fakturaterskel` — ÉN per tenant. Tenantens kontrollgrenser.
CREATE TABLE fakturaterskel (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- Hvor mange ØRE mva-beløpet kan avvike fra det beregnede før det
    -- er et funn. Standard 1: avrundingen i DOM 1 er halv-opp, og en
    -- leverandør som runder ned på siste øre skal ikke fylle registeret
    -- med funn. Null er lovlig og betyr «eksakt».
    mva_slingring_ore BIGINT NOT NULL DEFAULT 1
        CHECK (mva_slingring_ore BETWEEN 0 AND 1000),
    -- Over dette beløpet er fakturaen et funn til den er kontrollert av
    -- et menneske — uansett hvor pen den ser ut maskinelt.
    belopsgrense_ore BIGINT NOT NULL DEFAULT 2500000
        CHECK (belopsgrense_ore BETWEEN 0 AND 10000000000000),
    -- Hvor lenge en faktura kan stå ukontrollert før det er et funn.
    kontrollfrist_dogn INT NOT NULL DEFAULT 7
        CHECK (kontrollfrist_dogn BETWEEN 0 AND 3650),
    -- Hvor mange døgn to fakturaer kan ligge fra hverandre og fortsatt
    -- regnes som den samme nære dubletten.
    dublettvindu_dogn INT NOT NULL DEFAULT 3
        CHECK (dublettvindu_dogn BETWEEN 0 AND 365),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT fakturaterskel_pk PRIMARY KEY (tenant)
);

-- `inngaaende_faktura` — det noen krever av oss.
--
-- INGEN KONTOPLAN, INGEN POSTERING, INGEN HOVEDBOK. Det er v1-dommen i
-- tabellform: her finnes ingen kolonne som peker på en konto, fordi det
-- ikke finnes noen konto å peke på.
CREATE TABLE inngaaende_faktura (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    faktura_id UUID NOT NULL,
    -- TEKSTREFERANSE, ikke fremmednøkkel til M-24: en faktura kan komme
    -- fra noen som ennå ikke står i leverandørregisteret, og «ukjent
    -- leverandør» er et FUNN modulen skal reise — ikke en registrering
    -- den skal nekte.
    leverandor_ref TEXT NOT NULL CHECK (leverandor_ref ~ '[^[:space:]]'),
    fakturanummer TEXT NOT NULL CHECK (fakturanummer ~ '[^[:space:]]'),
    -- BELØPENE. `netto + mva = brutto` er en CHECK og ikke en avledning:
    -- alle tre står på fakturaen, og et register som regnet det tredje
    -- selv ville skjult en faktura som ikke går opp.
    netto_ore BIGINT NOT NULL CHECK (netto_ore >= 0),
    mva_ore BIGINT NOT NULL CHECK (mva_ore >= 0),
    brutto_ore BIGINT NOT NULL CHECK (brutto_ore >= 0),
    -- Hvilken sats leverandøren OPPGIR. Kontrollen måler den mot
    -- tenantens `mvasats` på fakturadatoen.
    sats_kode TEXT NOT NULL CHECK (sats_kode ~ '[^[:space:]]'),
    valuta TEXT NOT NULL DEFAULT 'NOK'
        CONSTRAINT faktura_valuta_form CHECK (valuta ~ '^[A-Z]{3}$'),
    utstedt DATE NOT NULL,
    forfall DATE NOT NULL,
    mottatt DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'mottatt'
        CONSTRAINT faktura_status_lukket
        CHECK (status IN ('mottatt', 'kontrollert', 'avvist')),
    -- AVSLUTNINGEN ER HEL ELLER IKKE (101/102-lærdommen: `NULL ~ '...'`
    -- er NULL, og en CHECK som evaluerer til NULL PASSERER — derfor er
    -- hvert ledd eksplisitt `IS NOT NULL`).
    avgjort_ts TIMESTAMPTZ,
    avgjort_av TEXT,
    avgjort_begrunnelse TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT inngaaende_faktura_pk PRIMARY KEY (tenant, faktura_id),
    -- DOM 3, FØRSTE HALVDEL: den EKSAKTE dubletten finnes ikke. Samme
    -- leverandør og samme nummer er ÉN faktura, og den skal ikke kunne
    -- betales to ganger fordi noen importerte den fra to kanaler.
    CONSTRAINT faktura_en_per_nummer
        UNIQUE (tenant, leverandor_ref, fakturanummer),
    CONSTRAINT faktura_belop_gar_opp
        CHECK (netto_ore + mva_ore = brutto_ore),
    CONSTRAINT faktura_forfall_etter_utstedt CHECK (forfall >= utstedt),
    CONSTRAINT faktura_avgjorelse_helhet CHECK (
        (status = 'mottatt' AND avgjort_ts IS NULL
         AND avgjort_av IS NULL AND avgjort_begrunnelse IS NULL)
     OR (status <> 'mottatt' AND avgjort_ts IS NOT NULL
         AND avgjort_av IS NOT NULL AND avgjort_av ~ '[^[:space:]]'
         AND avgjort_begrunnelse IS NOT NULL
         AND avgjort_begrunnelse ~ '[^[:space:]]'))
);
CREATE INDEX faktura_apne
    ON inngaaende_faktura (tenant, mottatt) WHERE status = 'mottatt';
CREATE INDEX faktura_naerdublett
    ON inngaaende_faktura (tenant, leverandor_ref, brutto_ore, utstedt);

-- `fakturakontroll` — APPEND-ONLY. Hver kontroll som er kjørt, med
-- utfallet og hva den så.
--
-- DOM 4: en kontroll som kunne skrives om ville gjort «denne fakturaen
-- er kontrollert» til en påstand. Rettelser gjøres ved en NY kontroll.
CREATE TABLE fakturakontroll (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kontroll_id UUID NOT NULL,
    faktura_id UUID NOT NULL,
    -- LUKKET SETT. En kontrolltype ingen har definert er en kontroll
    -- ingen kan tolke.
    kontrolltype TEXT NOT NULL
        CONSTRAINT kontroll_type_lukket CHECK (kontrolltype IN (
            'dublett', 'mva', 'leverandor', 'belopsgrense', 'manuell')),
    utfall TEXT NOT NULL
        CONSTRAINT kontroll_utfall_lukket
        CHECK (utfall IN ('ok', 'avvik')),
    -- Det MÅLTE avviket, i øre der det er et beløp. NULL når typen ikke
    -- har et tall — en `leverandor`-kontroll måler ingen kroner.
    avvik_ore BIGINT,
    -- Hva kontrollen så, som tekst et menneske kan lese. Ingen JSON:
    -- dette er en setning, ikke en struktur noen skal spørre på.
    notat TEXT CHECK (notat IS NULL OR notat ~ '[^[:space:]]'),
    kjort TIMESTAMPTZ NOT NULL DEFAULT now(),
    kjort_av TEXT NOT NULL CHECK (kjort_av ~ '[^[:space:]]'),
    CONSTRAINT fakturakontroll_pk PRIMARY KEY (tenant, kontroll_id),
    CONSTRAINT fakturakontroll_faktura_fk
        FOREIGN KEY (tenant, faktura_id)
        REFERENCES inngaaende_faktura (tenant, faktura_id)
);
CREATE INDEX fakturakontroll_pr_faktura
    ON fakturakontroll (tenant, faktura_id, kjort DESC);

-- `fakturafunn` — funnene. Nøklet på fakturaen og typen.
CREATE TABLE fakturafunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    faktura_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CONSTRAINT fakturafunn_type_lukket CHECK (funntype IN (
            'naer_dublett', 'mva_avvik', 'ukjent_leverandor',
            'over_belopsgrense', 'ukontrollert', 'ingen_mvasats',
            'ingen_terskel')),
    -- Hvor langt over grensen forholdet ligger: øre for mva og beløp,
    -- døgn for ukontrollert. Ett tall med én betydning per funntype.
    over_grense BIGINT,
    -- Den andre fakturaen i en nær dublett. NULL for de øvrige typene.
    motpart_faktura_id UUID,
    terskelversjon INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT fakturafunn_pk PRIMARY KEY (tenant, faktura_id, funntype),
    CONSTRAINT fakturafunn_faktura_fk FOREIGN KEY (tenant, faktura_id)
        REFERENCES inngaaende_faktura (tenant, faktura_id),
    CONSTRAINT fakturafunn_lukking_helhet CHECK (
        (apen AND lukket_ts IS NULL)
     OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE INDEX fakturafunn_apne
    ON fakturafunn (tenant, funntype) WHERE apen;


-- ------------------------------------------------------------
-- 2. Vaktene. Det datamodellen ikke kan si i en CHECK.
-- ------------------------------------------------------------

CREATE FUNCTION m14_sats_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    -- TRUNCATE HAR SIN EGEN ARM (lærdommen fra 104: uten den faller
    -- TG_OP='TRUNCATE' gjennom til radlogikken og feiler på «record
    -- "new" is not assigned yet» — riktig utfall, men en intern feil som
    -- ikke sier hva som ble nektet).
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'mvasats: TRUNCATE avvist — en sats erstattes av'
            ' en ny med egen gyldighet, den tømmes ikke bort'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'mvasats: DELETE avvist — en slettet sats gjør'
            ' hver faktura som ble kontrollert mot den til et tall uten'
            ' dom' USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- SATSEN ER FROSSET. En sats som kunne få ny promille i ettertid
    -- ville omskrevet hver mva-kontroll som alt var kjørt mot den —
    -- historien ville rettet seg selv.
    IF TG_OP = 'UPDATE' THEN
        IF NEW.tenant IS DISTINCT FROM OLD.tenant
           OR NEW.sats_kode IS DISTINCT FROM OLD.sats_kode
           OR NEW.gyldig_fra IS DISTINCT FROM OLD.gyldig_fra
           OR NEW.promille IS DISTINCT FROM OLD.promille THEN
            RAISE EXCEPTION 'mvasats: satsen og gyldighetsstarten er'
                ' frosset — en ny sats registreres, den gamle skrives'
                ' ikke om' USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    -- TO SATSER MED SAMME KODE KAN IKKE OVERLAPPE I TID. Da ville
    -- «hvilken sats gjaldt denne dagen» hatt to svar, og mva-kontrollen
    -- ville avhengt av hvilken rad planleggeren leste først.
    IF EXISTS (
        SELECT 1 FROM public.mvasats s
         WHERE s.tenant = NEW.tenant AND s.sats_kode = NEW.sats_kode
           AND (s.gyldig_fra, s.gyldig_til) IS DISTINCT FROM
               (NEW.gyldig_fra, NEW.gyldig_til)
           AND s.gyldig_fra <= coalesce(NEW.gyldig_til, DATE '9999-12-31')
           AND coalesce(s.gyldig_til, DATE '9999-12-31') >= NEW.gyldig_fra
    ) THEN
        RAISE EXCEPTION 'mvasats: satsen % overlapper en annen periode —'
            ' da har «hvilken sats gjaldt» to svar', NEW.sats_kode
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m14_sats_vakt() FROM PUBLIC;
CREATE TRIGGER m14_sats_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON mvasats
    FOR EACH ROW EXECUTE FUNCTION m14_sats_vakt();
CREATE TRIGGER m14_sats_ingen_truncate
    BEFORE TRUNCATE ON mvasats EXECUTE FUNCTION m14_sats_vakt();

CREATE FUNCTION m14_terskel_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'fakturaterskel: TRUNCATE avvist — grensene'
            ' endres ved å sette nye, ikke ved å fjerne dem under'
            ' føttene på sveipen'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'fakturaterskel: DELETE avvist — en tenant uten'
            ' grenser kan ikke måle noe, og det er en tilstand sveipen'
            ' skal SI FRA om' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.versjon <= OLD.versjon THEN
        RAISE EXCEPTION 'fakturaterskel: versjonen må øke ved endring'
            ' (% -> %) — et funn bærer versjonen det ble vurdert mot',
            OLD.versjon, NEW.versjon USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m14_terskel_vakt() FROM PUBLIC;
CREATE TRIGGER m14_terskel_vakt
    BEFORE UPDATE OR DELETE ON fakturaterskel
    FOR EACH ROW EXECUTE FUNCTION m14_terskel_vakt();
CREATE TRIGGER m14_terskel_ingen_truncate
    BEFORE TRUNCATE ON fakturaterskel
    EXECUTE FUNCTION m14_terskel_vakt();

CREATE FUNCTION m14_faktura_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'inngaaende_faktura: TRUNCATE avvist — en tømt'
            ' fakturatabell gjør hver dublettsjekk verdiløs: den vet'
            ' ikke lenger hva vi har sett før'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'inngaaende_faktura: DELETE avvist — en faktura'
            ' avvises med begrunnelse. En slettet faktura er en dublett'
            ' vi ikke lenger kan oppdage'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- FAKTURAENS INNHOLD ER FROSSET. Beløpene, datoene og nummeret er
    -- det leverandøren KREVER; et register som lot dem endres ville
    -- gjort kontrollen til en kontroll av noe annet enn det som kom.
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.faktura_id IS DISTINCT FROM OLD.faktura_id
       OR NEW.leverandor_ref IS DISTINCT FROM OLD.leverandor_ref
       OR NEW.fakturanummer IS DISTINCT FROM OLD.fakturanummer
       OR NEW.netto_ore IS DISTINCT FROM OLD.netto_ore
       OR NEW.mva_ore IS DISTINCT FROM OLD.mva_ore
       OR NEW.brutto_ore IS DISTINCT FROM OLD.brutto_ore
       OR NEW.sats_kode IS DISTINCT FROM OLD.sats_kode
       OR NEW.valuta IS DISTINCT FROM OLD.valuta
       OR NEW.utstedt IS DISTINCT FROM OLD.utstedt
       OR NEW.forfall IS DISTINCT FROM OLD.forfall
       OR NEW.mottatt IS DISTINCT FROM OLD.mottatt
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'inngaaende_faktura: fakturaens innhold er'
            ' frosset — det er det leverandøren krever, ikke vårt tall'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- EN AVGJORT FAKTURA GJENÅPNES IKKE.
    IF OLD.status <> 'mottatt' AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'inngaaende_faktura: fakturaen er alt % — en'
            ' avgjørelse gjøres om ved en NY kontroll, ikke ved å bytte'
            ' status tilbake', OLD.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status <> 'mottatt' AND OLD.status = 'mottatt' THEN
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL OR NEW.avgjort_av IS DISTINCT FROM v_aktor THEN
            RAISE EXCEPTION 'inngaaende_faktura: avgjort_av (%) er ikke'
                ' aktøren som avgjør (%)',
                coalesce(NEW.avgjort_av, '<null>'),
                coalesce(v_aktor, '<ingen>')
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m14_faktura_vakt() FROM PUBLIC;
CREATE TRIGGER m14_faktura_vakt
    BEFORE UPDATE OR DELETE ON inngaaende_faktura
    FOR EACH ROW EXECUTE FUNCTION m14_faktura_vakt();
CREATE TRIGGER m14_faktura_ingen_truncate
    BEFORE TRUNCATE ON inngaaende_faktura
    EXECUTE FUNCTION m14_faktura_vakt();

-- DOM 4: kontrollene er APPEND-ONLY.
CREATE FUNCTION m14_kontroll_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'fakturakontroll: TRUNCATE avvist — en tømt'
            ' kontrolltabell gjør hver «kontrollert» til en påstand'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'fakturakontroll: DELETE avvist — en kjørt'
            ' kontroll forsvinner ikke fordi utfallet ble ubehagelig'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RAISE EXCEPTION 'fakturakontroll: UPDATE avvist — raden er'
        ' append-only. En kontroll rettes med en NY kontroll, ikke ved å'
        ' skrive om den gamle'
        USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE ALL ON FUNCTION m14_kontroll_vakt() FROM PUBLIC;
CREATE TRIGGER m14_kontroll_vakt
    BEFORE UPDATE OR DELETE ON fakturakontroll
    FOR EACH ROW EXECUTE FUNCTION m14_kontroll_vakt();
CREATE TRIGGER m14_kontroll_ingen_truncate
    BEFORE TRUNCATE ON fakturakontroll
    EXECUTE FUNCTION m14_kontroll_vakt();

CREATE FUNCTION m14_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    -- Her er armen IKKE kosmetisk: uten den faller TG_OP='TRUNCATE'
    -- glatt gjennom til RETURN NEW, og tømmingen skjer. En trigger som
    -- heter `ingen_truncate` og slipper TRUNCATE igjennom er verre enn
    -- ingen, fordi den leses som beskyttelse. (CodeRabbit på 104.)
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'fakturafunn: TRUNCATE avvist — et funn lukkes,'
            ' det tømmes ikke bort. En tom funntabell ser ut som en ren'
            ' natt' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'fakturafunn: DELETE avvist — et funn lukkes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.tenant IS DISTINCT FROM OLD.tenant
           OR NEW.faktura_id IS DISTINCT FROM OLD.faktura_id
           OR NEW.funntype IS DISTINCT FROM OLD.funntype
           OR NEW.forst_sett IS DISTINCT FROM OLD.forst_sett THEN
            RAISE EXCEPTION 'fakturafunn: identiteten og førstegangen er'
                ' frosset' USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m14_funn_vakt() FROM PUBLIC;
CREATE TRIGGER m14_funn_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON fakturafunn
    FOR EACH ROW EXECUTE FUNCTION m14_funn_vakt();
CREATE TRIGGER m14_funn_ingen_truncate
    BEFORE TRUNCATE ON fakturafunn EXECUTE FUNCTION m14_funn_vakt();


-- ------------------------------------------------------------
-- 2b. RLS og rettigheter.
-- ------------------------------------------------------------
ALTER TABLE mvasats ENABLE ROW LEVEL SECURITY;
ALTER TABLE mvasats FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON mvasats
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE fakturaterskel ENABLE ROW LEVEL SECURITY;
ALTER TABLE fakturaterskel FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON fakturaterskel
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE inngaaende_faktura ENABLE ROW LEVEL SECURITY;
ALTER TABLE inngaaende_faktura FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON inngaaende_faktura
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — tre gjerder, som i
-- 100–105: bare dørenes eier, bare SELECT, bare uten tenantkontekst.
CREATE POLICY m14_sveip_tenantliste ON inngaaende_faktura
    FOR SELECT TO disponit_faktura_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

ALTER TABLE fakturakontroll ENABLE ROW LEVEL SECURITY;
ALTER TABLE fakturakontroll FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON fakturakontroll
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE fakturafunn ENABLE ROW LEVEL SECURITY;
ALTER TABLE fakturafunn FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON fakturafunn
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

GRANT SELECT, INSERT, UPDATE ON mvasats TO disponit_faktura_eier;
GRANT SELECT, INSERT, UPDATE ON fakturaterskel TO disponit_faktura_eier;
GRANT SELECT, INSERT, UPDATE ON inngaaende_faktura
    TO disponit_faktura_eier;
-- `fakturakontroll` HAR VERKEN UPDATE ELLER DELETE — append-only helt
-- ned til grantet, ikke bare i vakten.
GRANT SELECT, INSERT ON fakturakontroll TO disponit_faktura_eier;
GRANT SELECT, INSERT, UPDATE ON fakturafunn TO disponit_faktura_eier;
GRANT INSERT ON revisjonslogg TO disponit_faktura_eier;

-- LEVERANDØRKONTROLLEN MÅ LESE M-24s PARTSTABELL, og det er det ENESTE
-- objektet i en annen modul denne rollen får se — SELECT, ingenting mer.
--
-- HVORFOR IKKE GJENNOM M-24s DØR, som er husets vanlige vei mellom
-- moduler: `m24_leverandorene` returnerer en SIDE (LIMIT 500). En
-- kontroll bygget på den ville svart «ukjent leverandør» for tenantens
-- leverandør nummer 501 — et STILLE galt svar, og et funn som ikke er
-- sant er verre enn intet funn. Kontrollen svarer for ÉN referanse om
-- gangen og kan ikke avkortes.
--
-- Grantet er derfor eksplisitt og smalt, og porten i
-- `test_m14_faktura.py` krever at det er den ENESTE rettigheten M-14s
-- eier har på noe M-24 eier. RLS gjelder fortsatt: `leverandorpart` har
-- FORCE, så oppslaget ser bare tenantens egne rader.
--
-- BETINGET, fordi M-24 kan mangle i en base der bare M-14 er rullet ut.
DO $$
BEGIN
    IF to_regclass('public.leverandorpart') IS NOT NULL THEN
        EXECUTE 'GRANT SELECT ON public.leverandorpart'
                ' TO disponit_faktura_eier';
    END IF;
END $$;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_faktura_eier;
RESET ROLE;


-- ------------------------------------------------------------
-- 3. Dørene. SECURITY DEFINER, eid av `disponit_faktura_eier`, SP-1
--    (`krev_tenantkontekst`) først i hver tenantbundet definer.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_faktura_eier;

-- DOM 1: MVA REGNES I HELTALL, MED EN SKREVET AVRUNDINGSREGEL.
--
-- Dette er modulens skarpeste enkeltfunksjon, og den er skarp fordi
-- avrunding er der en mva-kontroll blir stille gal. `netto * 0.25` i
-- flyttall gir 2499.9999999999995 øre på 99,99 kroner netto — og en
-- kontroll som sammenlignet det med leverandørens 2500 ville reist et
-- funn hver gang, på hver eneste faktura.
--
-- REGELEN ER HALV-OPP: `(netto * promille + 500) / 1000`. Heltallsdivisjon
-- i PostgreSQL trunkerer mot null, så `+ 500` er nøyaktig det som gjør
-- den til «rund halve opp» for ikke-negative tall — og netto kan ikke
-- være negativ (CHECK).
--
-- IMMUTABLE fordi den bare avhenger av argumentene.
CREATE FUNCTION m14_forventet_mva(p_netto_ore BIGINT, p_promille INT)
RETURNS BIGINT LANGUAGE plpgsql IMMUTABLE
SET search_path = pg_catalog AS $$
BEGIN
    IF p_netto_ore IS NULL OR p_promille IS NULL THEN
        -- En mva uten grunnlag eller uten sats er ingen mva. `NULL` som
        -- ble lest som «stemmer» er nøyaktig den stille feilen.
        RAISE EXCEPTION 'm14_forventet_mva: både grunnlag og sats må'
            ' finnes — en mva uten sats er ingen kontroll'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_netto_ore < 0 THEN
        RAISE EXCEPTION 'm14_forventet_mva: grunnlaget kan ikke være'
            ' negativt' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN (p_netto_ore * p_promille + 500) / 1000;
END $$;
REVOKE ALL ON FUNCTION m14_forventet_mva(BIGINT, INT) FROM PUBLIC;

-- DOM 2: SATSEN LESES ETTER FAKTURAENS DATO, ikke etter dagens.
--
-- En satsendring skal ikke gjøre gamle fakturaer gale med
-- tilbakevirkende kraft. Funksjonen returnerer NULL når tenanten ikke
-- har en sats som gjaldt den dagen — og NULL er det ærlige svaret:
-- «ingen mvasats» er et eget FUNN, ikke et avvik på null.
CREATE FUNCTION m14_sats_paa_dato(p_tenant TEXT, p_sats_kode TEXT,
                                  p_dato DATE)
RETURNS INT LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_promille INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm14_sats_paa_dato');
    SELECT s.promille INTO v_promille FROM public.mvasats s
     WHERE s.tenant = p_tenant AND s.sats_kode = p_sats_kode
       AND s.gyldig_fra <= p_dato
       AND (s.gyldig_til IS NULL OR s.gyldig_til >= p_dato)
     ORDER BY s.gyldig_fra DESC
     LIMIT 1;
    RETURN v_promille;
END $$;
REVOKE ALL ON FUNCTION m14_sats_paa_dato(TEXT, TEXT, DATE) FROM PUBLIC;

-- Evidenskjeden, ett sted. BELØP STÅR ALDRI HER — det er tenantens
-- forretningsdata, og evidenskjeden skal gjenfinne HANDLINGEN uten å
-- arkivere pengestrømmen på nytt et sted til (101s dom, ordrett).
CREATE FUNCTION m14_evidens(p_tenant TEXT, p_subjekt_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm14_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm14_faktura', 'handling', p_handling,
        'subjekt_id', p_subjekt_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm14_faktura',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:fakturaregister', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m14_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

CREATE FUNCTION m14_sett_terskler(
    p_tenant TEXT, p_mva_slingring_ore BIGINT, p_belopsgrense_ore BIGINT,
    p_kontrollfrist_dogn INT, p_dublettvindu_dogn INT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm14_sett_terskler');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.fakturaterskel
        (tenant, mva_slingring_ore, belopsgrense_ore, kontrollfrist_dogn,
         dublettvindu_dogn, versjon, oppdatert, oppdatert_av)
    VALUES (p_tenant, p_mva_slingring_ore, p_belopsgrense_ore,
            p_kontrollfrist_dogn, p_dublettvindu_dogn, 1, now(), p_aktor)
    ON CONFLICT (tenant) DO UPDATE
        SET mva_slingring_ore = EXCLUDED.mva_slingring_ore,
            belopsgrense_ore = EXCLUDED.belopsgrense_ore,
            kontrollfrist_dogn = EXCLUDED.kontrollfrist_dogn,
            dublettvindu_dogn = EXCLUDED.dublettvindu_dogn,
            versjon = fakturaterskel.versjon + 1,
            oppdatert = now(), oppdatert_av = p_aktor
    RETURNING versjon INTO v_versjon;
    PERFORM public.m14_evidens(
        p_tenant, '00000000-0000-0000-0000-000000000000'::uuid,
        'terskler.satt', p_aktor,
        jsonb_build_object('versjon', v_versjon));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION m14_sett_terskler(TEXT, BIGINT, BIGINT, INT, INT,
    TEXT) FROM PUBLIC;

-- MVA-SATSDØREN. Satsen er tenantens, og den er DATERT.
CREATE FUNCTION m14_sett_mvasats(
    p_tenant TEXT, p_sats_kode TEXT, p_promille INT, p_gyldig_fra DATE,
    p_gyldig_til DATE, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm14_sett_mvasats');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.mvasats
        (tenant, sats_kode, promille, gyldig_fra, gyldig_til,
         opprettet_av)
    VALUES (p_tenant, btrim(p_sats_kode), p_promille, p_gyldig_fra,
            p_gyldig_til, p_aktor)
        ON CONFLICT (tenant, sats_kode, gyldig_fra) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;                               -- SP-2: stille ja
    END IF;
    PERFORM public.m14_evidens(
        p_tenant, '00000000-0000-0000-0000-000000000000'::uuid,
        'mvasats.satt', p_aktor,
        jsonb_build_object('kode', btrim(p_sats_kode),
                           'promille', p_promille));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m14_sett_mvasats(TEXT, TEXT, INT, DATE, DATE,
    TEXT) FROM PUBLIC;

-- KONTROLLENS ID ER UTLEDET, ikke tilfeldig. Kjøres registreringen om
-- igjen med samme `faktura_id` (SP-2s stille ja), skal den ikke legge
-- igjen tre nye kontrollrader — og en kontrolltabell som vokste med tre
-- rader per gjentatt import ville gjort «hvor mange kontroller har vi
-- kjørt» til et tall om importen, ikke om kontrollen.
--
-- `md5` er ikke brukt som sikkerhet her: den er en DETERMINISTISK
-- avbildning til 128 bit, og kollisjonsmotstand betyr ingenting når
-- inndataene er våre egne to felter.
CREATE FUNCTION m14_utled_kontroll(p_faktura_id UUID, p_type TEXT)
RETURNS UUID LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT md5('m14|' || p_faktura_id::text || '|' || p_type)::uuid;
$$;
REVOKE ALL ON FUNCTION m14_utled_kontroll(UUID, TEXT) FROM PUBLIC;

-- LEVERANDØRKONTROLLEN, og den er med vilje MYK i én forstand: den slår
-- opp i M-24s register hvis det finnes, og svarer `false` hvis det ikke
-- gjør det.
--
-- `to_regclass` framfor en fremmednøkkel, av to grunner. Den ene er
-- teknisk: en base der bare M-14 er rullet ut har ingen
-- `leverandorpart`, og en kontroll som KRASJET på det ville stoppet
-- hele registreringen. Den andre er en dom: «ukjent leverandør» er et
-- FUNN modulen skal reise, ikke en registrering den skal nekte — en
-- faktura fra noen vi ikke kjenner er nettopp det noen må se på.
CREATE FUNCTION m14_leverandor_kjent(p_tenant TEXT, p_ref TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_finnes BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm14_leverandor_kjent');
    IF to_regclass('public.leverandorpart') IS NULL THEN
        RETURN false;
    END IF;
    EXECUTE 'SELECT EXISTS (SELECT 1 FROM public.leverandorpart l'
            '  WHERE l.tenant = $1 AND l.aktiv'
            '    AND (l.navn = $2 OR l.ekstern_ref = $2))'
        INTO v_finnes USING p_tenant, p_ref;
    RETURN coalesce(v_finnes, false);
END $$;
REVOKE ALL ON FUNCTION m14_leverandor_kjent(TEXT, TEXT) FROM PUBLIC;

-- FAKTURADØREN, og de tre MASKINELLE KONTROLLENE som kjøres i samme
-- transaksjon som registreringen.
--
-- HVORFOR I SAMME TRANSAKSJON: en faktura som lå ukontrollert i et
-- vindu mellom to kall er en faktura noen kunne betalt i mellomtiden.
-- Kontrollene er dessuten billige — de er tre spørringer — og den
-- MENNESKELIGE kontrollen er en annen dør, som den skal være.
--
-- DØREN GODKJENNER INGENTING. Den registrerer hva kontrollene SÅ.
CREATE FUNCTION m14_registrer_faktura(
    p_tenant TEXT, p_faktura_id UUID, p_leverandor_ref TEXT,
    p_fakturanummer TEXT, p_netto_ore BIGINT, p_mva_ore BIGINT,
    p_brutto_ore BIGINT, p_sats_kode TEXT, p_valuta TEXT,
    p_utstedt DATE, p_forfall DATE, p_mottatt DATE, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_promille INT; v_forventet BIGINT;
        v_slingring BIGINT; v_avvik BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm14_registrer_faktura');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.inngaaende_faktura
        (tenant, faktura_id, leverandor_ref, fakturanummer, netto_ore,
         mva_ore, brutto_ore, sats_kode, valuta, utstedt, forfall,
         mottatt, opprettet_av)
    VALUES (p_tenant, p_faktura_id, btrim(p_leverandor_ref),
            btrim(p_fakturanummer), p_netto_ore, p_mva_ore, p_brutto_ore,
            btrim(p_sats_kode), upper(coalesce(p_valuta, 'NOK')),
            p_utstedt, p_forfall, p_mottatt, p_aktor)
        ON CONFLICT (tenant, faktura_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;                               -- SP-2: stille ja
    END IF;

    -- KONTROLL 1: MVA. Satsen leses på FAKTURAENS dato.
    v_promille := public.m14_sats_paa_dato(p_tenant, btrim(p_sats_kode),
                                           p_utstedt);
    SELECT t.mva_slingring_ore INTO v_slingring
      FROM public.fakturaterskel t WHERE t.tenant = p_tenant;
    IF v_promille IS NULL THEN
        -- INGEN SATS ER IKKE «AVVIK PÅ NULL». Det er en tenant som ikke
        -- har ført satsen, og den forskjellen skal stå.
        INSERT INTO public.fakturakontroll
            (tenant, kontroll_id, faktura_id, kontrolltype, utfall,
             notat, kjort_av)
        VALUES (p_tenant, public.m14_utled_kontroll(p_faktura_id, 'mva'),
                p_faktura_id, 'mva', 'avvik',
                'ingen mvasats gjaldt fakturadatoen', p_aktor);
    ELSE
        v_forventet := public.m14_forventet_mva(p_netto_ore, v_promille);
        v_avvik := abs(p_mva_ore - v_forventet);
        INSERT INTO public.fakturakontroll
            (tenant, kontroll_id, faktura_id, kontrolltype, utfall,
             avvik_ore, notat, kjort_av)
        VALUES (p_tenant, public.m14_utled_kontroll(p_faktura_id, 'mva'),
                p_faktura_id, 'mva',
                CASE WHEN v_avvik <= coalesce(v_slingring, 1)
                     THEN 'ok' ELSE 'avvik' END,
                v_avvik, NULL, p_aktor);
    END IF;

    -- KONTROLL 2: NÆR DUBLETT. Samme leverandør, samme beløp, ULIKT
    -- nummer, innenfor tenantens vindu. Den EKSAKTE dubletten er
    -- allerede umulig (UNIQUE), så det som gjenstår er mønsteret et
    -- menneske må se på.
    INSERT INTO public.fakturakontroll
        (tenant, kontroll_id, faktura_id, kontrolltype, utfall, notat,
         kjort_av)
    SELECT p_tenant,
           public.m14_utled_kontroll(p_faktura_id, 'dublett'),
           p_faktura_id, 'dublett',
           CASE WHEN EXISTS (
               SELECT 1 FROM public.inngaaende_faktura f
                JOIN public.fakturaterskel t ON t.tenant = f.tenant
                WHERE f.tenant = p_tenant
                  AND f.faktura_id <> p_faktura_id
                  AND f.leverandor_ref = btrim(p_leverandor_ref)
                  AND f.brutto_ore = p_brutto_ore
                  AND abs(f.utstedt - p_utstedt) <= t.dublettvindu_dogn)
                THEN 'avvik' ELSE 'ok' END,
           NULL, p_aktor;

    -- KONTROLL 3: LEVERANDØREN. `to_regclass` framfor en fremmednøkkel:
    -- M-24 kan mangle i en base der bare M-14 er rullet ut, og en
    -- kontroll som KRASJET på det ville vært verre enn en som sier fra.
    INSERT INTO public.fakturakontroll
        (tenant, kontroll_id, faktura_id, kontrolltype, utfall, notat,
         kjort_av)
    SELECT p_tenant,
           public.m14_utled_kontroll(p_faktura_id, 'leverandor'),
           p_faktura_id, 'leverandor',
           CASE WHEN public.m14_leverandor_kjent(
                        p_tenant, btrim(p_leverandor_ref))
                THEN 'ok' ELSE 'avvik' END,
           NULL, p_aktor;

    PERFORM public.m14_evidens(
        p_tenant, p_faktura_id, 'faktura.registrert', p_aktor,
        jsonb_build_object('sats_kode', btrim(p_sats_kode)));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m14_registrer_faktura(TEXT, UUID, TEXT, TEXT,
    BIGINT, BIGINT, BIGINT, TEXT, TEXT, DATE, DATE, DATE, TEXT)
    FROM PUBLIC;

-- DEN MENNESKELIGE KONTROLLEN. Egen dør, og det er en dom: de tre
-- maskinelle kontrollene måler det som kan måles, mens DENNE er
-- vurderingen. `faktura_godkjent` i policyen hviler til slutt på den —
-- men v1 SIGNERER INGEN ATTESTASJON, den registrerer at et menneske så
-- på fakturaen og hva vedkommende konkluderte.
CREATE FUNCTION m14_registrer_kontroll(
    p_tenant TEXT, p_kontroll_id UUID, p_faktura_id UUID, p_utfall TEXT,
    p_notat TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm14_registrer_kontroll');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_notat IS NULL OR p_notat !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm14_registrer_kontroll: en manuell kontroll'
            ' uten et ord om hva som ble sett, er en kontroll ingen kan'
            ' etterprøve' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.fakturakontroll
        (tenant, kontroll_id, faktura_id, kontrolltype, utfall, notat,
         kjort_av)
    VALUES (p_tenant, p_kontroll_id, p_faktura_id, 'manuell', p_utfall,
            btrim(p_notat), p_aktor)
        ON CONFLICT (tenant, kontroll_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;                               -- SP-2: stille ja
    END IF;
    PERFORM public.m14_evidens(
        p_tenant, p_faktura_id, 'kontroll.registrert', p_aktor,
        jsonb_build_object('utfall', p_utfall));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m14_registrer_kontroll(TEXT, UUID, UUID, TEXT,
    TEXT, TEXT) FROM PUBLIC;

-- AVGJØRELSESDØREN. Et menneske setter fakturaen til `kontrollert`
-- eller `avvist`, med begrunnelse.
--
-- «KONTROLLERT» ER IKKE «BOKFØRT», og det er hele v1-snittet. Statusen
-- sier at noen har sett på den; den sier ingenting om at penger har
-- flyttet seg, og det finnes ingen dør her som får dem til å gjøre det.
CREATE FUNCTION m14_avgjor_faktura(
    p_tenant TEXT, p_faktura_id UUID, p_status TEXT, p_begrunnelse TEXT,
    p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm14_avgjor_faktura');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_status NOT IN ('kontrollert', 'avvist') THEN
        RAISE EXCEPTION 'm14_avgjor_faktura: ukjent utfall «%» — en'
            ' faktura blir kontrollert eller avvist, og ingen av delene'
            ' er en bokføring', p_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_begrunnelse IS NULL OR p_begrunnelse !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm14_avgjor_faktura: en avgjørelse uten'
            ' begrunnelse er den ene handlingen ingen kan etterprøve'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- FOR UPDATE, som i 104/105s skrivedører. Uten låsen kunne to
    -- samtidige avgjørelser begge lese 'mottatt', begge skrive en
    -- evidensrad, og bare den ene truffet en rad.
    SELECT f.status INTO v_status FROM public.inngaaende_faktura f
     WHERE f.tenant = p_tenant AND f.faktura_id = p_faktura_id
       FOR UPDATE;
    IF v_status IS NULL THEN
        RAISE EXCEPTION 'm14_avgjor_faktura: fakturaen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_status <> 'mottatt' THEN
        RETURN false;                               -- stille ja
    END IF;
    UPDATE public.inngaaende_faktura
       SET status = p_status, avgjort_ts = now(), avgjort_av = p_aktor,
           avgjort_begrunnelse = btrim(p_begrunnelse)
     WHERE tenant = p_tenant AND faktura_id = p_faktura_id
       AND status = 'mottatt';
    -- FUNNENE LUKKES MED AVGJØRELSEN. Et åpent funn om en faktura som
    -- er avgjort er et varsel ingen kan gjøre noe med.
    UPDATE public.fakturafunn
       SET apen = false, lukket_ts = now()
     WHERE tenant = p_tenant AND faktura_id = p_faktura_id AND apen;
    PERFORM public.m14_evidens(p_tenant, p_faktura_id, 'faktura.avgjort',
                               p_aktor,
                               jsonb_build_object('status', p_status));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m14_avgjor_faktura(TEXT, UUID, TEXT, TEXT, TEXT)
    FROM PUBLIC;

RESET ROLE;


-- ------------------------------------------------------------
-- 3b. Lesedørene.
--
--     INGEN AV DEM RETURNERER EN ATTESTASJON. `kontroll_utfall` er hva
--     en kontroll SÅ; det er ikke en signert påstand noen kan bygge en
--     automatisk bokføring på. Det snittet er klyngens nye dom.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_faktura_eier;

CREATE FUNCTION m14_fakturastatus(p_tenant TEXT)
RETURNS TABLE(mottatte INT, mottatt_ore BIGINT, kontrollerte INT,
              avviste INT, apne_funn INT, ukontrollerte INT,
              har_terskel BOOLEAN, terskelversjon INT, satser INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm14_fakturastatus');
    RETURN QUERY
    SELECT (SELECT count(*)::int FROM public.inngaaende_faktura f
             WHERE f.tenant = p_tenant AND f.status = 'mottatt'),
           -- SUM OVER BIGINT GIR NUMERIC; uten castet faller
           -- RETURNS TABLE(... BIGINT) på typen (101s lærdom).
           (SELECT coalesce(sum(f.brutto_ore), 0)::bigint
              FROM public.inngaaende_faktura f
             WHERE f.tenant = p_tenant AND f.status = 'mottatt'),
           (SELECT count(*)::int FROM public.inngaaende_faktura f
             WHERE f.tenant = p_tenant AND f.status = 'kontrollert'),
           (SELECT count(*)::int FROM public.inngaaende_faktura f
             WHERE f.tenant = p_tenant AND f.status = 'avvist'),
           (SELECT count(*)::int FROM public.fakturafunn ff
             WHERE ff.tenant = p_tenant AND ff.apen),
           (SELECT count(*)::int FROM public.fakturafunn ff
             WHERE ff.tenant = p_tenant AND ff.apen
               AND ff.funntype = 'ukontrollert'),
           EXISTS (SELECT 1 FROM public.fakturaterskel t
                    WHERE t.tenant = p_tenant),
           (SELECT t.versjon FROM public.fakturaterskel t
             WHERE t.tenant = p_tenant),
           (SELECT count(*)::int FROM public.mvasats s
             WHERE s.tenant = p_tenant);
END $$;
REVOKE ALL ON FUNCTION m14_fakturastatus(TEXT) FROM PUBLIC;

-- TREFFRATEN, og den er modulens egentlige leveranse i v1.
--
-- «En dublettsjekk ingen har målt er ikke en kontroll, det er en
-- påstand.» Her står tallet: hvor mange kontroller av hver type som er
-- kjørt, og hvor mange av dem som fant et avvik. ALLE FEM TYPENE STÅR I
-- SVARET, også de tenanten ikke har kjørt — en oversikt som endret form
-- fra dag til dag kan ingen sammenligne over tid.
CREATE FUNCTION m14_treffrate(p_tenant TEXT)
RETURNS TABLE(kontrolltype TEXT, kjort INT, avvik INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm14_treffrate');
    RETURN QUERY
    SELECT s.t,
           (SELECT count(*)::int FROM public.fakturakontroll k
             WHERE k.tenant = p_tenant AND k.kontrolltype = s.t),
           (SELECT count(*)::int FROM public.fakturakontroll k
             WHERE k.tenant = p_tenant AND k.kontrolltype = s.t
               AND k.utfall = 'avvik')
      FROM (VALUES ('dublett'), ('mva'), ('leverandor'),
                   ('belopsgrense'), ('manuell')) AS s(t);
END $$;
REVOKE ALL ON FUNCTION m14_treffrate(TEXT) FROM PUBLIC;

CREATE FUNCTION m14_fakturaene(p_tenant TEXT, p_grense INT)
RETURNS TABLE(faktura_id UUID, leverandor_ref TEXT, fakturanummer TEXT,
              netto_ore BIGINT, mva_ore BIGINT, brutto_ore BIGINT,
              sats_kode TEXT, valuta TEXT, utstedt DATE, forfall DATE,
              mottatt DATE, status TEXT, dogn_siden_mottatt INT,
              kontroller INT, avvik INT, apne_funn TEXT[])
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm14_fakturaene');
    RETURN QUERY
    SELECT f.faktura_id, f.leverandor_ref, f.fakturanummer, f.netto_ore,
           f.mva_ore, f.brutto_ore, f.sats_kode, f.valuta, f.utstedt,
           f.forfall, f.mottatt, f.status,
           (current_date - f.mottatt)::int,
           k.antall, k.avvik,
           coalesce(ff.typer, ARRAY[]::TEXT[])
      FROM public.inngaaende_faktura f
      CROSS JOIN LATERAL (
            SELECT count(*)::int AS antall,
                   count(*) FILTER (WHERE k2.utfall = 'avvik')::int
                       AS avvik
              FROM public.fakturakontroll k2
             WHERE k2.tenant = f.tenant
               AND k2.faktura_id = f.faktura_id) k
      LEFT JOIN LATERAL (
            SELECT array_agg(x.funntype ORDER BY x.funntype) AS typer
              FROM public.fakturafunn x
             WHERE x.tenant = f.tenant AND x.faktura_id = f.faktura_id
               AND x.apen) ff ON true
     WHERE f.tenant = p_tenant
     -- MOTTATTE FØRST, deretter de med flest avvik. Avkortingen skal ta
     -- det som betyr minst, ikke det som tilfeldigvis kom sist.
     ORDER BY (f.status = 'mottatt') DESC, k.avvik DESC, f.mottatt,
              f.faktura_id
     LIMIT greatest(least(coalesce(p_grense, 200), 1000), 1);
END $$;
REVOKE ALL ON FUNCTION m14_fakturaene(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m14_kontrollene(p_tenant TEXT, p_faktura_id UUID)
RETURNS TABLE(kontroll_id UUID, kontrolltype TEXT, utfall TEXT,
              avvik_ore BIGINT, notat TEXT, kjort TIMESTAMPTZ,
              kjort_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm14_kontrollene');
    RETURN QUERY
    SELECT k.kontroll_id, k.kontrolltype, k.utfall, k.avvik_ore, k.notat,
           k.kjort, k.kjort_av
      FROM public.fakturakontroll k
     WHERE k.tenant = p_tenant AND k.faktura_id = p_faktura_id
     ORDER BY k.kjort DESC, k.kontroll_id
     LIMIT 500;
END $$;
REVOKE ALL ON FUNCTION m14_kontrollene(TEXT, UUID) FROM PUBLIC;

CREATE FUNCTION m14_tersklene(p_tenant TEXT)
RETURNS TABLE(mva_slingring_ore BIGINT, belopsgrense_ore BIGINT,
              kontrollfrist_dogn INT, dublettvindu_dogn INT,
              versjon INT, oppdatert TIMESTAMPTZ, oppdatert_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm14_tersklene');
    RETURN QUERY
    SELECT t.mva_slingring_ore, t.belopsgrense_ore, t.kontrollfrist_dogn,
           t.dublettvindu_dogn, t.versjon, t.oppdatert, t.oppdatert_av
      FROM public.fakturaterskel t WHERE t.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m14_tersklene(TEXT) FROM PUBLIC;

CREATE FUNCTION m14_satsene(p_tenant TEXT)
RETURNS TABLE(sats_kode TEXT, promille INT, gyldig_fra DATE,
              gyldig_til DATE, gjelder_i_dag BOOLEAN)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm14_satsene');
    RETURN QUERY
    SELECT s.sats_kode, s.promille, s.gyldig_fra, s.gyldig_til,
           (s.gyldig_fra <= current_date
            AND (s.gyldig_til IS NULL OR s.gyldig_til >= current_date))
      FROM public.mvasats s
     WHERE s.tenant = p_tenant
     ORDER BY s.sats_kode, s.gyldig_fra DESC
     LIMIT 500;
END $$;
REVOKE ALL ON FUNCTION m14_satsene(TEXT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Sveipens kandidater. TERSKLENE LESES FRA TABELLEN — funksjonen tar
--    ingen terskelparameter, og det er `mvasats_hardkodet` i kode.
-- ------------------------------------------------------------
CREATE FUNCTION m14_funnkandidater(p_tenant TEXT, p_dag DATE)
RETURNS TABLE(faktura_id UUID, funntype TEXT, over_grense BIGINT,
              motpart_faktura_id UUID, terskelversjon INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_t RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm14_funnkandidater');
    SELECT * INTO v_t FROM public.fakturaterskel t
     WHERE t.tenant = p_tenant;
    IF NOT FOUND THEN
        -- 0. INGEN TERSKEL: tenanten har mottatte fakturaer, men ingen
        --    grenser å måle dem mot. Da vet ingen hva «for stort» eller
        --    «for lenge» betyr her, og hvert av de andre funnene ville
        --    vært en gjetning.
        RETURN QUERY
        SELECT f.faktura_id, 'ingen_terskel'::text, NULL::bigint,
               NULL::uuid, NULL::int
          FROM public.inngaaende_faktura f
         WHERE f.tenant = p_tenant AND f.status = 'mottatt';
        RETURN;
    END IF;

    RETURN QUERY
    -- 1. MVA-AVVIK: den maskinelle kontrollen fant et avvik større enn
    --    tenantens slingringsmonn. Dette er modulens hovedfunn sammen
    --    med den nære dubletten.
    SELECT f.faktura_id, 'mva_avvik'::text,
           (k.avvik_ore - v_t.mva_slingring_ore)::bigint, NULL::uuid,
           v_t.versjon
      FROM public.inngaaende_faktura f
      JOIN public.fakturakontroll k
        ON k.tenant = f.tenant AND k.faktura_id = f.faktura_id
       AND k.kontrolltype = 'mva' AND k.utfall = 'avvik'
     WHERE f.tenant = p_tenant AND f.status = 'mottatt'
       AND k.avvik_ore IS NOT NULL
    UNION ALL
    -- 1b. INGEN MVASATS er en EGEN funntype, ikke et avvik på null.
    --     «Vi har ikke ført satsen» og «leverandøren regnet feil» er to
    --     forskjellige problemer med to forskjellige løsninger.
    SELECT f.faktura_id, 'ingen_mvasats'::text, NULL::bigint, NULL::uuid,
           v_t.versjon
      FROM public.inngaaende_faktura f
      JOIN public.fakturakontroll k
        ON k.tenant = f.tenant AND k.faktura_id = f.faktura_id
       AND k.kontrolltype = 'mva' AND k.utfall = 'avvik'
     WHERE f.tenant = p_tenant AND f.status = 'mottatt'
       AND k.avvik_ore IS NULL
    UNION ALL
    -- 2. NÆR DUBLETT: samme leverandør, samme beløp, ulikt nummer,
    --    innenfor tenantens vindu. Motparten står PÅ funnet — uten den
    --    måtte et menneske lete etter hvilken faktura det gjelder.
    SELECT f.faktura_id, 'naer_dublett'::text, NULL::bigint, d.motpart,
           v_t.versjon
      FROM public.inngaaende_faktura f
      JOIN LATERAL (
            SELECT f2.faktura_id AS motpart
              FROM public.inngaaende_faktura f2
             WHERE f2.tenant = f.tenant
               AND f2.faktura_id <> f.faktura_id
               AND f2.leverandor_ref = f.leverandor_ref
               AND f2.brutto_ore = f.brutto_ore
               AND abs(f2.utstedt - f.utstedt) <= v_t.dublettvindu_dogn
             ORDER BY f2.utstedt, f2.faktura_id
             LIMIT 1) d ON true
     WHERE f.tenant = p_tenant AND f.status = 'mottatt'
    UNION ALL
    -- 3. UKJENT LEVERANDØR: den maskinelle kontrollen fant ingen rad i
    --    M-24. En faktura fra noen vi ikke kjenner er nettopp det noen
    --    må se på.
    SELECT f.faktura_id, 'ukjent_leverandor'::text, NULL::bigint,
           NULL::uuid, v_t.versjon
      FROM public.inngaaende_faktura f
      JOIN public.fakturakontroll k
        ON k.tenant = f.tenant AND k.faktura_id = f.faktura_id
       AND k.kontrolltype = 'leverandor' AND k.utfall = 'avvik'
     WHERE f.tenant = p_tenant AND f.status = 'mottatt'
    UNION ALL
    -- 4. OVER BELØPSGRENSEN: uansett hvor pen fakturaen ser ut
    --    maskinelt, skal et menneske ha sett på den over dette beløpet.
    SELECT f.faktura_id, 'over_belopsgrense'::text,
           (f.brutto_ore - v_t.belopsgrense_ore)::bigint, NULL::uuid,
           v_t.versjon
      FROM public.inngaaende_faktura f
     WHERE f.tenant = p_tenant AND f.status = 'mottatt'
       AND f.brutto_ore > v_t.belopsgrense_ore
       AND NOT EXISTS (SELECT 1 FROM public.fakturakontroll k
                        WHERE k.tenant = f.tenant
                          AND k.faktura_id = f.faktura_id
                          AND k.kontrolltype = 'manuell')
    UNION ALL
    -- 5. UKONTROLLERT: en faktura som har stått lenger enn tenantens
    --    frist uten at et menneske har sett på den. En faktura som
    --    forfaller mens den venter, er den dyreste raden i registeret.
    SELECT f.faktura_id, 'ukontrollert'::text,
           (p_dag - f.mottatt - v_t.kontrollfrist_dogn)::bigint,
           NULL::uuid, v_t.versjon
      FROM public.inngaaende_faktura f
     WHERE f.tenant = p_tenant AND f.status = 'mottatt'
       AND p_dag - f.mottatt > v_t.kontrollfrist_dogn;
END $$;
REVOKE ALL ON FUNCTION m14_funnkandidater(TEXT, DATE) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 4b. Sveipen. KRYSS-TENANT, egen LOGIN-rolle, egen timer.
--
--     SVEIPEN BOKFØRER INGEN OG GODKJENNER INGEN FAKTURA. Den kunne,
--     teknisk — den vet hvilke som er kontrollert uten avvik. Men en
--     bokføring er en skriving i regnskapet, og en godkjenning er
--     fullmakten som slipper den gjennom. Ingen av dem tas av en jobb
--     som kjører om natten.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_faktura_eier;

CREATE FUNCTION m14_sveip_fakturaer(p_grense INT DEFAULT 500)
RETURNS TABLE(tenanter INT, nye INT, oppdaterte INT, lukkede INT,
              avkortet INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_n INT; v_grense INT;
        v_dag DATE; v_naa TIMESTAMPTZ;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm14_sveip_fakturaer: sveipen er KRYSS-TENANT og'
            ' kjøres uten tenantkontekst — en kaller som har satt en'
            ' kontekst ber om noe annet enn det denne funksjonen gjør'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_grense := greatest(least(coalesce(p_grense, 500), 5000), 1);
    v_dag := current_date;
    v_naa := now();
    tenanter := 0; nye := 0; oppdaterte := 0; lukkede := 0; avkortet := 0;
    SELECT array_agg(DISTINCT f.tenant ORDER BY f.tenant) INTO v_tenanter
      FROM public.inngaaende_faktura f;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        tenanter := tenanter + 1;

        UPDATE public.fakturafunn ff
           SET sist_sett_sveip = v_naa, apen = true, lukket_ts = NULL,
               over_grense = kand.over_grense,
               motpart_faktura_id = kand.motpart_faktura_id,
               terskelversjon = kand.terskelversjon
          FROM public.m14_funnkandidater(v_t, v_dag) kand
         WHERE ff.tenant = v_t AND ff.faktura_id = kand.faktura_id
           AND ff.funntype = kand.funntype;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        oppdaterte := oppdaterte + v_n;

        INSERT INTO public.fakturafunn
            (tenant, faktura_id, funntype, over_grense,
             motpart_faktura_id, terskelversjon, forst_sett,
             sist_sett_sveip, apen)
        SELECT v_t, kand.faktura_id, kand.funntype, kand.over_grense,
               kand.motpart_faktura_id, kand.terskelversjon, v_naa,
               v_naa, true
          FROM public.m14_funnkandidater(v_t, v_dag) kand
         WHERE NOT EXISTS (
                SELECT 1 FROM public.fakturafunn ff
                 WHERE ff.tenant = v_t
                   AND ff.faktura_id = kand.faktura_id
                   AND ff.funntype = kand.funntype)
         ORDER BY coalesce(kand.over_grense, 0) DESC,
                  kand.faktura_id, kand.funntype
         LIMIT v_grense;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        nye := nye + v_n;
        IF v_n = v_grense THEN
            avkortet := avkortet + 1;
        END IF;

        UPDATE public.fakturafunn ff
           SET apen = false, lukket_ts = v_naa
         WHERE ff.tenant = v_t AND ff.apen
           AND NOT EXISTS (
                SELECT 1 FROM public.m14_funnkandidater(v_t, v_dag) kand
                 WHERE kand.faktura_id = ff.faktura_id
                   AND kand.funntype = ff.funntype);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        lukkede := lukkede + v_n;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m14_sveip_fakturaer(INT) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 5. Rettighetene. `m14_sveip_fakturaer` og `m14_funnkandidater`
--    grantes ingen: den første er sveiperollens, den andre internt ledd.
--
--    SP-7: kjøretidsrollen får INGEN tabellrettigheter, bare EXECUTE på
--    dørene. Registeret nås bare gjennom dem.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_faktura_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m14_fakturastatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m14_treffrate(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m14_fakturaene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m14_kontrollene(TEXT, UUID)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m14_tersklene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m14_satsene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m14_forventet_mva(BIGINT, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m14_sats_paa_dato(TEXT, TEXT, DATE) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m14_sett_terskler(TEXT, BIGINT, BIGINT, INT, INT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m14_sett_mvasats(TEXT, TEXT, INT, DATE, DATE, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m14_registrer_faktura(TEXT, UUID, TEXT, TEXT, BIGINT,'
            ' BIGINT, BIGINT, TEXT, TEXT, DATE, DATE, DATE, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m14_registrer_kontroll(TEXT, UUID, UUID, TEXT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m14_avgjor_faktura(TEXT, UUID, TEXT, TEXT, TEXT)'
            ' TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_fakturasveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m14_sveip_fakturaer(INT)'
            ' TO disponit_fakturasveip';
    END IF;
END $$;
RESET ROLE;
