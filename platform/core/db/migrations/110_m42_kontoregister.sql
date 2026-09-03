-- 110: M-42 kontoverifikasjon og transaksjonsvakt v1 — KONTOHISTORIKKEN.
-- Fem tenant-skopede tabeller, tretten dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- V1-AVGRENSNINGEN, ORDRETT FRA POLICYENE VI SENDER UT: to av tre
-- bransjemaler navngir denne modulen som verifikatoren `v_kontovakt`,
-- betrodd for `konto_verifisert`, `konto_verifisert_uavhengig` og
-- `svindelsjekk_bestatt` — og bruker dem til å slippe
-- `ordre.bekreft_og_fakturer` og utgående betalinger gjennom som
-- `modus: auto`.
--
-- DETTE ER KLYNGENS SKARPESTE MODUL, og grunnen er ikke den man tror.
-- Det farligste en betalingsvakt kan gjøre er ikke å SLIPPE noe
-- gjennom — det er å STOPPE noe. En vakt som blokkerer feil er sin egen
-- skade: en leverandør som ikke får betalt, en lønn som uteblir, en
-- frist som ryker. Og en vakt ingen har målt vet ikke hvor ofte den tar
-- feil.
--
-- v1 STOPPER DERFOR INGEN BETALING, VERIFISERER INGENTING MOT EN
-- EKSTERN KANAL, OG ATTESTERER INGENTING.
--
-- v1 GJØR ÉN TING: den skriver ned HVEM SOM OPPGA HVILKEN KONTO, NÅR,
-- GJENNOM HVILKEN KANAL — og hvordan et MENNESKE verifiserte den. Og
-- den gjør EN KONTOENDRING PÅ EN MOTTAKER VI BETALER til et FUNN. Det
-- er det høyeste signalet som finnes i den svindelklassen, og ingen kan
-- handle på det hvis det ikke er skrevet ned.
--
-- DOMMENE v1 HVILER PÅ, håndhevet i DATAMODELLEN:
--
--   1. HISTORIKKEN OVERSKRIVES ALDRI. Det finnes ingen kolonne noe sted
--      som holder «gjeldende kontonummer». Den gjeldende kontoen ER den
--      siste oppgaven i historikken, og hver oppgave er FROSSET. Det er
--      hele modulen: svindelen avsløres av HISTORIKKEN, ikke av
--      gjeldende verdi — en tabell som ble oppdatert på stedet ville
--      slettet beviset i samme øyeblikk som det oppsto.
--
--   2. KONTONUMMERET LAGRES ALDRI. Registeret trenger å oppdage
--      ENDRINGER, ikke å betale — og til det holder en MASKE (siste
--      fire siffer) og en SALTET HASH. Saltet er mottakerens eget og
--      tilfeldig, så to like kontonumre hos to mottakere ikke ser like
--      ut, og en lekkasje av oppgavetabellen alene ikke gir
--      kontonumre. Mot en full basekompromittering hjelper det ikke, og
--      v1 later ikke som noe annet.
--
--   3. EN VERIFIKASJON HAR ET MENNESKE OG EN METODE. Begge er NOT NULL
--      i et lukket sett, og notatet er obligatorisk. «Verifisert» uten
--      hvem og hvordan er ikke en måling — det er en påstand, og
--      `konto_verifisert` ville hvilt på den.
--
--   4. DEN SOM OPPGA KONTOEN KAN IKKE VERIFISERE DEN. Er de samme, er
--      ingenting verifisert — og `konto_verifisert_uavhengig` er
--      nøyaktig navnet på det vilkåret. Håndhevet i døren OG i vakten.
--
--   5. EN KONTOENDRING BLIR ET FUNN I SAMME TRANSAKSJON. Den venter
--      ikke på nattens sveip: en endret utbetalingskonto er det
--      høyeste svindelsignalet vi har, og et døgns forsinkelse er et
--      døgn der pengene kan gå.
--
-- GRENSEN MOT M-24 (105): M-24 eier LEVERANDØREN. M-42 eier
-- KONTOHISTORIKKEN. `leverandor_ref` er en TEKSTREFERANSE, ikke en
-- fremmednøkkel: en hard kobling ville gjort kontohistorikken avhengig
-- av at leverandørregisteret er ført, og historikken skal kunne stå
-- alene — det er den som er beviset.
--
-- SVEIPEN HAR SIN EGEN ROLLE OG SIN EGEN TIMER (095/100–109):
-- `disponit_kontovaktsveip` har nøyaktig ÉN rettighet — EXECUTE på
-- `m42_sveip_konto` — og INGEN tabellrettigheter. Sveipen STOPPER INGEN
-- BETALING og VERIFISERER INGENTING; den skriver FUNN.
--
-- FORMENE ER HUSETS: tabellene eies av migrator, dørene av NOLOGIN-rollen
-- `disponit_kontovakt_eier`, tenant TEXT + RLS ENABLE+FORCE +
-- `tenant_isolasjon`, SP-1 i hver tenantbundet definer, ingen BYPASSRLS.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_kontovakt_eier') THEN
        RAISE EXCEPTION 'rollen disponit_kontovakt_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_kontovakt_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `kontoterskel` — ÉN per tenant. GRENSENE ER TENANTENS.
CREATE TABLE kontoterskel (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- Hvor lenge en verifikasjon regnes som gyldig. En verifikasjon fra
    -- 2019 sier ingenting om kontoen som står der i dag.
    reverifikasjon_dogn INT NOT NULL DEFAULT 365
        CHECK (reverifikasjon_dogn BETWEEN 0 AND 3650),
    -- Hvor lenge en oppgitt konto kan stå UVERIFISERT før det er et
    -- funn. En konto ingen har sjekket er en konto ingen har sjekket.
    uverifisert_dogn INT NOT NULL DEFAULT 7
        CHECK (uverifisert_dogn BETWEEN 0 AND 3650),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT kontoterskel_pk PRIMARY KEY (tenant)
);

-- `betalingsmottaker` — den vi betaler.
CREATE TABLE betalingsmottaker (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    mottaker_id UUID NOT NULL,
    -- Tenantens egen referanse til parten. FRI TEKST og ingen
    -- fremmednøkkel mot M-24: kontohistorikken skal kunne stå alene.
    ekstern_ref TEXT NOT NULL CHECK (ekstern_ref ~ '[^[:space:]]'),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    -- MOTTAKERENS EGET SALT. Uten det ville to like kontonumre hos to
    -- mottakere fått samme hash, og en angriper med én kjent konto
    -- kunne kartlagt hvem andre som bruker den. Med det krever et
    -- oppslag også denne raden.
    hash_salt TEXT NOT NULL
        DEFAULT (gen_random_uuid()::text || gen_random_uuid()::text)
        CHECK (length(hash_salt) >= 32),
    aktiv BOOLEAN NOT NULL DEFAULT true,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT betalingsmottaker_pk PRIMARY KEY (tenant, mottaker_id),
    CONSTRAINT betalingsmottaker_ref_unik UNIQUE (tenant, ekstern_ref)
);
CREATE INDEX betalingsmottaker_aktive
    ON betalingsmottaker (tenant) WHERE aktiv;

-- `kontooppgave` — DOM 1 OG 2. HVEM OPPGA HVILKEN KONTO, NÅR, HVORDAN.
--
-- Dette er modulen. Svindelen avsløres av HISTORIKKEN, ikke av
-- gjeldende verdi: en tabell som ble oppdatert på stedet ville slettet
-- beviset i samme øyeblikk som det oppsto.
CREATE TABLE kontooppgave (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    oppgave_id UUID NOT NULL,
    mottaker_id UUID NOT NULL,
    -- MASKEN, regnet av døren av nummeret selv: siste fire tegn, resten
    -- skjult. Kontonummeret LAGRES ALDRI.
    -- ALFANUMERISK, ikke bare siffer: en IBAN kan ende på bokstaver
    -- (`GB33BUKB…ABCD`), og et register som avviste den ville nektet å
    -- skrive ned nettopp den kontoen noen betalte til (CodeRabbit).
    kontonummer_maske TEXT NOT NULL
        CONSTRAINT kontooppgave_maske_form
        CHECK (kontonummer_maske ~ '^\*+[0-9A-Za-z]{4}$'),
    -- sha256 over mottakerens salt og det normaliserte nummeret, regnet
    -- av døren. Vakten krever at masken og hashen hører sammen med en
    -- oppgave som faktisk gikk gjennom døren.
    kontonummer_hash TEXT NOT NULL
        CONSTRAINT kontooppgave_hash_form
        CHECK (kontonummer_hash ~ '^[0-9a-f]{64}$'),
    -- HVEM SOM OPPGA DEN. Et navn, ikke en bruker-id: det er ofte en
    -- person hos motparten, og det er nettopp den opplysningen som
    -- betyr noe når en faktura kom fra en kapret e-postkonto.
    oppgitt_av TEXT NOT NULL CHECK (oppgitt_av ~ '[^[:space:]]'),
    -- KANALEN. Lukket sett: «hvordan kom denne kontoen inn» er det
    -- første spørsmålet i enhver etterforskning av fakturasvindel.
    oppgitt_kanal TEXT NOT NULL
        CONSTRAINT kontooppgave_kanal_lukket CHECK (oppgitt_kanal IN (
            'faktura', 'epost', 'telefon', 'portal', 'brev', 'annet')),
    oppgitt_dato DATE NOT NULL,
    notat TEXT NOT NULL CHECK (notat ~ '[^[:space:]]'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT kontooppgave_pk PRIMARY KEY (tenant, oppgave_id),
    CONSTRAINT kontooppgave_mottaker_fk FOREIGN KEY (tenant, mottaker_id)
        REFERENCES betalingsmottaker (tenant, mottaker_id)
);
CREATE INDEX kontooppgave_oppslag
    ON kontooppgave (tenant, mottaker_id, oppgitt_dato DESC,
                     registrert DESC);

-- `kontoverifikasjon` — DOM 3 OG 4. ET MENNESKE OG EN METODE.
CREATE TABLE kontoverifikasjon (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    verifikasjon_id UUID NOT NULL,
    oppgave_id UUID NOT NULL,
    -- METODEN. Lukket sett, og rekkefølgen er ikke tilfeldig: å ringe
    -- et nummer man hadde FRA FØR er den eneste metoden som ikke kan
    -- forfalskes av den som sendte fakturaen.
    metode TEXT NOT NULL
        CONSTRAINT kontoverifikasjon_metode_lukket CHECK (metode IN (
            'ringte_kjent_nummer', 'fysisk_mote', 'signert_dokument',
            'bankbekreftelse', 'annet')),
    -- MENNESKET. Ikke en tjeneste, ikke en jobb.
    verifisert_av TEXT NOT NULL
        CHECK (verifisert_av ~ '[^[:space:]]'),
    -- HVA SOM FAKTISK BLE GJORT. En verifikasjon uten notat er et
    -- avkrysset felt, og et avkrysset felt er ikke en kontroll.
    notat TEXT NOT NULL CHECK (notat ~ '[^[:space:]]'),
    verifisert_dato DATE NOT NULL,
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT kontoverifikasjon_pk PRIMARY KEY (tenant, verifikasjon_id),
    CONSTRAINT kontoverifikasjon_oppgave_fk
        FOREIGN KEY (tenant, oppgave_id)
        REFERENCES kontooppgave (tenant, oppgave_id)
);
CREATE INDEX kontoverifikasjon_oppslag
    ON kontoverifikasjon (tenant, oppgave_id, verifisert_dato DESC);

-- `kontofunn` — funnene. Nøklet på mottakeren og typen.
CREATE TABLE kontofunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    mottaker_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CONSTRAINT kontofunn_type_lukket CHECK (funntype IN (
            'kontoendring', 'uverifisert_konto', 'verifikasjon_utlopt',
            'ingen_terskel')),
    -- DØGN for de to tidsfunnene; NULL for `kontoendring`, som ikke er
    -- et spørsmål om tid.
    over_grense INT,
    -- Masken det ble endret FRA og TIL. Uten begge er funnet en påstand
    -- om at noe skjedde, ikke en beskrivelse av hva.
    fra_maske TEXT,
    til_maske TEXT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT kontofunn_pk PRIMARY KEY (tenant, mottaker_id, funntype),
    CONSTRAINT kontofunn_mottaker_fk FOREIGN KEY (tenant, mottaker_id)
        REFERENCES betalingsmottaker (tenant, mottaker_id),
    CONSTRAINT kontofunn_lukking_helhet CHECK (
        (apen AND lukket_ts IS NULL)
     OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE INDEX kontofunn_apne ON kontofunn (tenant, funntype) WHERE apen;


-- ------------------------------------------------------------
-- 2. Vaktene.
-- ------------------------------------------------------------

CREATE FUNCTION m42_terskel_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'kontoterskel: TRUNCATE avvist — grensene endres'
            ' ved å sette nye, ikke ved å fjerne dem under føttene på'
            ' sveipen' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'kontoterskel: DELETE avvist — en tenant uten'
            ' grenser kan ikke måle noe, og det er en tilstand sveipen'
            ' skal SI FRA om' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.versjon <= OLD.versjon THEN
        RAISE EXCEPTION 'kontoterskel: versjonen må øke ved endring'
            ' (% -> %)', OLD.versjon, NEW.versjon
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m42_terskel_vakt() FROM PUBLIC;
CREATE TRIGGER m42_terskel_vakt
    BEFORE UPDATE OR DELETE ON kontoterskel
    FOR EACH ROW EXECUTE FUNCTION m42_terskel_vakt();
CREATE TRIGGER m42_terskel_ingen_truncate
    BEFORE TRUNCATE ON kontoterskel
    EXECUTE FUNCTION m42_terskel_vakt();

CREATE FUNCTION m42_mottaker_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'betalingsmottaker: TRUNCATE avvist — en'
            ' mottaker deaktiveres, den tømmes ikke bort'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'betalingsmottaker: DELETE avvist — sett aktiv'
            ' til false. En slettet mottaker ville tatt kontohistorikken'
            ' med seg, og den er hele beviset'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- SALTET ER FROSSET. Et nytt salt ville gjort hver eldre hash
    -- usammenlignbar, og dermed skjult nettopp den endringen modulen
    -- finnes for å oppdage.
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.mottaker_id IS DISTINCT FROM OLD.mottaker_id
       OR NEW.ekstern_ref IS DISTINCT FROM OLD.ekstern_ref
       OR NEW.hash_salt IS DISTINCT FROM OLD.hash_salt
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'betalingsmottaker: identiteten, referansen og'
            ' SALTET er frosset — et nytt salt ville gjort hver eldre'
            ' hash usammenlignbar og skjult kontoendringen'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m42_mottaker_vakt() FROM PUBLIC;
CREATE TRIGGER m42_mottaker_vakt
    BEFORE UPDATE OR DELETE ON betalingsmottaker
    FOR EACH ROW EXECUTE FUNCTION m42_mottaker_vakt();
CREATE TRIGGER m42_mottaker_ingen_truncate
    BEFORE TRUNCATE ON betalingsmottaker
    EXECUTE FUNCTION m42_mottaker_vakt();

-- DOM 1: HISTORIKKEN OVERSKRIVES ALDRI.
--
-- Dette er modulens skarpeste vakt. Svindelen avsløres av historikken,
-- og en oppgave som kunne endres i ettertid ville latt den som kapret
-- e-postkontoen rydde opp etter seg.
CREATE FUNCTION m42_oppgave_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'kontooppgave: TRUNCATE avvist — en tømt'
            ' kontohistorikk er en svindel ingen kan se'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'kontooppgave: DELETE avvist — en oppgitt konto'
            ' erstattes av en NY oppgave, den slettes aldri. Historikken'
            ' er hele beviset' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'kontooppgave: raden er FROSSET — en feilført'
            ' oppgave rettes med en NY oppgave. En historikk som kunne'
            ' skrives om ville latt den som kapret e-postkontoen rydde'
            ' opp etter seg' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m42_oppgave_vakt() FROM PUBLIC;
CREATE TRIGGER m42_oppgave_vakt
    BEFORE UPDATE OR DELETE ON kontooppgave
    FOR EACH ROW EXECUTE FUNCTION m42_oppgave_vakt();
CREATE TRIGGER m42_oppgave_ingen_truncate
    BEFORE TRUNCATE ON kontooppgave
    EXECUTE FUNCTION m42_oppgave_vakt();

-- DOM 3 OG 4: ET MENNESKE, EN METODE — OG IKKE DEN SAMME SOM OPPGA
-- KONTOEN.
--
-- Regelen står i VAKTEN, ikke bare i døren: er de to samme person, er
-- ingenting verifisert, og `konto_verifisert_uavhengig` er nøyaktig
-- navnet på det vilkåret.
CREATE FUNCTION m42_verifikasjon_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_oppgitt_av TEXT;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'kontoverifikasjon: TRUNCATE avvist —'
            ' verifikasjonene er beviset for at noen faktisk sjekket'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'kontoverifikasjon: DELETE avvist — en'
            ' verifikasjon står, også når den viste seg å være feil'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'kontoverifikasjon: raden er FROSSET — en ny'
            ' kontroll er en NY verifikasjon'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT o.oppgitt_av INTO v_oppgitt_av FROM public.kontooppgave o
     WHERE o.tenant = NEW.tenant AND o.oppgave_id = NEW.oppgave_id;
    IF v_oppgitt_av IS NOT NULL
       AND lower(btrim(v_oppgitt_av)) = lower(btrim(NEW.verifisert_av))
    THEN
        RAISE EXCEPTION 'kontoverifikasjon: «%» oppga kontoen og kan'
            ' ikke verifisere den. Er de samme, er ingenting'
            ' verifisert', NEW.verifisert_av
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m42_verifikasjon_vakt() FROM PUBLIC;
CREATE TRIGGER m42_verifikasjon_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON kontoverifikasjon
    FOR EACH ROW EXECUTE FUNCTION m42_verifikasjon_vakt();
CREATE TRIGGER m42_verifikasjon_ingen_truncate
    BEFORE TRUNCATE ON kontoverifikasjon
    EXECUTE FUNCTION m42_verifikasjon_vakt();

CREATE FUNCTION m42_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'kontofunn: TRUNCATE avvist — funnene lukkes av'
            ' sveipen når tilstanden er borte, de tømmes ikke bort'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'kontofunn: DELETE avvist — et funn lukkes, det'
            ' slettes ikke. En kontoendring som forsvant fra listen er'
            ' en kontoendring ingen så'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.mottaker_id IS DISTINCT FROM OLD.mottaker_id
       OR NEW.funntype IS DISTINCT FROM OLD.funntype
       OR NEW.forst_sett IS DISTINCT FROM OLD.forst_sett THEN
        RAISE EXCEPTION 'kontofunn: identiteten og førstegangen er'
            ' frosset' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m42_funn_vakt() FROM PUBLIC;
CREATE TRIGGER m42_funn_vakt
    BEFORE UPDATE OR DELETE ON kontofunn
    FOR EACH ROW EXECUTE FUNCTION m42_funn_vakt();
CREATE TRIGGER m42_funn_ingen_truncate
    BEFORE TRUNCATE ON kontofunn EXECUTE FUNCTION m42_funn_vakt();


-- ------------------------------------------------------------
-- 2b. RLS og rettigheter.
-- ------------------------------------------------------------
ALTER TABLE kontoterskel ENABLE ROW LEVEL SECURITY;
ALTER TABLE kontoterskel FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON kontoterskel
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE betalingsmottaker ENABLE ROW LEVEL SECURITY;
ALTER TABLE betalingsmottaker FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON betalingsmottaker
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — tre gjerder: bare
-- SELECT, bare eierrollen, og bare når det IKKE står en tenantkontekst.
CREATE POLICY m42_sveip_tenantliste ON betalingsmottaker
    FOR SELECT TO disponit_kontovakt_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

ALTER TABLE kontooppgave ENABLE ROW LEVEL SECURITY;
ALTER TABLE kontooppgave FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON kontooppgave
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE kontoverifikasjon ENABLE ROW LEVEL SECURITY;
ALTER TABLE kontoverifikasjon FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON kontoverifikasjon
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE kontofunn ENABLE ROW LEVEL SECURITY;
ALTER TABLE kontofunn FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON kontofunn
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

GRANT SELECT, INSERT, UPDATE ON kontoterskel
    TO disponit_kontovakt_eier;
GRANT SELECT, INSERT, UPDATE ON betalingsmottaker
    TO disponit_kontovakt_eier;
-- `kontooppgave` og `kontoverifikasjon` har VERKEN UPDATE ELLER DELETE.
-- Historikken er append-only, og det er to gjerder som sier det:
-- rettigheten her, og vakten som stanser den som likevel har den.
GRANT SELECT, INSERT ON kontooppgave TO disponit_kontovakt_eier;
GRANT SELECT, INSERT ON kontoverifikasjon TO disponit_kontovakt_eier;
GRANT SELECT, INSERT, UPDATE ON kontofunn TO disponit_kontovakt_eier;
GRANT INSERT ON revisjonslogg TO disponit_kontovakt_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_kontovakt_eier;
RESET ROLE;


-- ------------------------------------------------------------
-- 3. Dørene. SECURITY DEFINER, eid av `disponit_kontovakt_eier`, SP-1.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_kontovakt_eier;

CREATE FUNCTION m42_evidens(p_tenant TEXT, p_subjekt_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm42_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm42_kontovakt', 'handling', p_handling,
        'subjekt_id', p_subjekt_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm42_kontovakt',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:kontovakt', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m42_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- NORMALISERINGEN, ETT STED. Mellomrom, punktum og bindestrek er
-- skrivemåter, ikke forskjellige kontonumre — og to skrivemåter av
-- samme konto måtte ellers blitt to «endringer».
CREATE FUNCTION m42_normaliser(p_konto TEXT)
RETURNS TEXT LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT regexp_replace(coalesce(p_konto, ''), '[^0-9A-Za-z]', '', 'g');
$$;
REVOKE ALL ON FUNCTION m42_normaliser(TEXT) FROM PUBLIC;

CREATE FUNCTION m42_sett_terskler(
    p_tenant TEXT, p_reverifikasjon_dogn INT, p_uverifisert_dogn INT,
    p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_ny INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm42_sett_terskler');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.kontoterskel
        (tenant, reverifikasjon_dogn, uverifisert_dogn, versjon,
         oppdatert, oppdatert_av)
    VALUES (p_tenant, p_reverifikasjon_dogn, p_uverifisert_dogn, 1,
            now(), p_aktor)
    ON CONFLICT (tenant) DO UPDATE
        SET reverifikasjon_dogn = excluded.reverifikasjon_dogn,
            uverifisert_dogn = excluded.uverifisert_dogn,
            versjon = public.kontoterskel.versjon + 1,
            oppdatert = now(), oppdatert_av = excluded.oppdatert_av
    RETURNING versjon INTO v_ny;
    PERFORM public.m42_evidens(
        p_tenant, NULL, 'kontoterskel.satt', p_aktor,
        jsonb_build_object('versjon', v_ny));
    RETURN v_ny;
END $$;
REVOKE ALL ON FUNCTION m42_sett_terskler(TEXT, INT, INT, TEXT)
    FROM PUBLIC;

CREATE FUNCTION m42_registrer_mottaker(
    p_tenant TEXT, p_mottaker_id UUID, p_ekstern_ref TEXT, p_navn TEXT,
    p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm42_registrer_mottaker');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.betalingsmottaker
        (tenant, mottaker_id, ekstern_ref, navn, opprettet_av)
    VALUES (p_tenant, p_mottaker_id, btrim(p_ekstern_ref),
            btrim(p_navn), p_aktor);
    PERFORM public.m42_evidens(
        p_tenant, p_mottaker_id, 'mottaker.registrert', p_aktor,
        jsonb_build_object('ekstern_ref', btrim(p_ekstern_ref)));
END $$;
REVOKE ALL ON FUNCTION m42_registrer_mottaker(TEXT, UUID, TEXT, TEXT,
    TEXT) FROM PUBLIC;

-- KONTODØREN. DOM 2 OG 5.
--
-- KONTONUMMERET LAGRES ALDRI. Døren normaliserer det, regner masken og
-- den saltede hashen, og KASTER nummeret. Parameteren er det eneste
-- stedet det finnes.
--
-- OG EN ENDRING BLIR ET FUNN I SAMME TRANSAKSJON. Den venter ikke på
-- nattens sveip: et døgns forsinkelse er et døgn der pengene kan gå.
CREATE FUNCTION m42_oppgi_konto(
    p_tenant TEXT, p_oppgave_id UUID, p_mottaker_id UUID,
    p_kontonummer TEXT, p_oppgitt_av TEXT, p_oppgitt_kanal TEXT,
    p_oppgitt_dato DATE, p_notat TEXT, p_aktor TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_salt TEXT; v_norm TEXT; v_hash TEXT; v_maske TEXT;
        v_forrige_hash TEXT; v_forrige_maske TEXT; v_endret BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm42_oppgi_konto');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    -- EN KONTO KAN IKKE OPPGIS I FRAMTIDA. Sveipen måler «siste
    -- oppgave med dato <= i dag»; en framtidsdatert linje ville derfor
    -- vært den siste for døren, men USYNLIG for sveipen — som så ville
    -- LUKKET kontoendringsfunnet fordi den eldre linjen fortsatt så
    -- uendret ut. Altså en måte å skjule en kontoendring på ved å sette
    -- feil dato (CodeRabbit).
    IF p_oppgitt_dato IS NULL OR p_oppgitt_dato > current_date THEN
        RAISE EXCEPTION 'm42_oppgi_konto: en konto kan ikke oppgis i'
            ' framtida (%). Sveipen måler mot i dag, og en framtidig'
            ' dato ville skjult endringen', p_oppgitt_dato
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_norm := public.m42_normaliser(p_kontonummer);
    -- FIRE TEGN ER MINSTEMÅLET for en maske som betyr noe.
    IF length(v_norm) < 6 THEN
        RAISE EXCEPTION 'm42_oppgi_konto: kontonummeret er for kort til'
            ' å være et kontonummer (% tegn etter normalisering)',
            length(v_norm) USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT m.hash_salt, m.aktiv INTO v_salt, v_endret
      FROM public.betalingsmottaker m
     WHERE m.tenant = p_tenant AND m.mottaker_id = p_mottaker_id
       FOR UPDATE;
    IF v_salt IS NULL THEN
        RAISE EXCEPTION 'm42_oppgi_konto: mottakeren finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF NOT v_endret THEN
        RAISE EXCEPTION 'm42_oppgi_konto: mottakeren er deaktivert —'
            ' aktiver den før du fører kontoer på den'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_hash := encode(sha256(convert_to(v_salt || v_norm, 'UTF8')), 'hex');
    v_maske := repeat('*', greatest(length(v_norm) - 4, 1))
               || right(v_norm, 4);
    -- SISTE OPPGAVE FØR DENNE — det er den nye måles mot.
    SELECT o.kontonummer_hash, o.kontonummer_maske
      INTO v_forrige_hash, v_forrige_maske
      FROM public.kontooppgave o
     WHERE o.tenant = p_tenant AND o.mottaker_id = p_mottaker_id
     ORDER BY o.oppgitt_dato DESC, o.registrert DESC, o.oppgave_id DESC
     LIMIT 1;

    INSERT INTO public.kontooppgave
        (tenant, oppgave_id, mottaker_id, kontonummer_maske,
         kontonummer_hash, oppgitt_av, oppgitt_kanal, oppgitt_dato,
         notat, registrert_av)
    VALUES (p_tenant, p_oppgave_id, p_mottaker_id, v_maske, v_hash,
            btrim(p_oppgitt_av), p_oppgitt_kanal, p_oppgitt_dato,
            btrim(p_notat), p_aktor);

    v_endret := v_forrige_hash IS NOT NULL
                AND v_forrige_hash IS DISTINCT FROM v_hash;
    IF v_endret THEN
        -- DOM 5: FUNNET SKRIVES NÅ, ikke i natt.
        INSERT INTO public.kontofunn
            (tenant, mottaker_id, funntype, fra_maske, til_maske,
             forst_sett, sist_sett_sveip, apen)
        VALUES (p_tenant, p_mottaker_id, 'kontoendring',
                v_forrige_maske, v_maske, now(), now(), true)
        ON CONFLICT (tenant, mottaker_id, funntype) DO UPDATE
            SET apen = true, lukket_ts = NULL, sist_sett_sveip = now(),
                fra_maske = excluded.fra_maske,
                til_maske = excluded.til_maske;
    END IF;
    PERFORM public.m42_evidens(
        p_tenant, p_mottaker_id, 'konto.oppgitt', p_aktor,
        jsonb_build_object('oppgave_id', p_oppgave_id::text,
                           'maske', v_maske, 'kanal', p_oppgitt_kanal,
                           'endret', v_endret));
    RETURN v_maske;
END $$;
REVOKE ALL ON FUNCTION m42_oppgi_konto(TEXT, UUID, UUID, TEXT, TEXT,
    TEXT, DATE, TEXT, TEXT) FROM PUBLIC;

-- VERIFIKASJONSDØREN. DOM 3 OG 4.
--
-- DØREN VERIFISERER INGENTING. Den SKRIVER NED at et menneske gjorde
-- det, med hvilken metode, og hva de faktisk gjorde. Det finnes ingen
-- oppslag mot en bank, ingen ekstern kanal, ingen automatikk — og
-- fraværet er dommen: en vakt som verifiserte selv, ville vært en vakt
-- ingen har målt.
CREATE FUNCTION m42_verifiser_konto(
    p_tenant TEXT, p_verifikasjon_id UUID, p_oppgave_id UUID,
    p_metode TEXT, p_verifisert_av TEXT, p_notat TEXT,
    p_verifisert_dato DATE, p_aktor TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_mottaker UUID; v_siste UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm42_verifiser_konto');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    -- SAMME GRUNN: en framtidsdatert verifikasjon er usynlig for
    -- sveipen, men lukker funnet i døren.
    IF p_verifisert_dato IS NULL OR p_verifisert_dato > current_date THEN
        RAISE EXCEPTION 'm42_verifiser_konto: en verifikasjon kan ikke'
            ' skje i framtida (%)', p_verifisert_dato
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT o.mottaker_id INTO v_mottaker FROM public.kontooppgave o
     WHERE o.tenant = p_tenant AND o.oppgave_id = p_oppgave_id;
    IF v_mottaker IS NULL THEN
        RAISE EXCEPTION 'm42_verifiser_konto: oppgaven finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    -- SERIALISERINGEN LIGGER PÅ MOTTAKEREN, ikke på oppgaven. `FOR
    -- UPDATE` krever UPDATE-rettigheten, og `kontooppgave` har den ikke
    -- — append-only nekter nettopp den. Oppgaven er dessuten FROSSET, så
    -- det er ingenting å låse der; det som må stå stille er hvilken
    -- oppgave som er den SISTE, og den endres bare av
    -- `m42_oppgi_konto`, som låser samme mottakerrad.
    PERFORM 1 FROM public.betalingsmottaker m
      WHERE m.tenant = p_tenant AND m.mottaker_id = v_mottaker
      FOR UPDATE;
    INSERT INTO public.kontoverifikasjon
        (tenant, verifikasjon_id, oppgave_id, metode, verifisert_av,
         notat, verifisert_dato, registrert_av)
    VALUES (p_tenant, p_verifikasjon_id, p_oppgave_id, p_metode,
            btrim(p_verifisert_av), btrim(p_notat), p_verifisert_dato,
            p_aktor);
    -- EN VERIFISERT ENDRING ER IKKE LENGER ET ÅPENT FUNN — men BARE
    -- hvis det er den SISTE oppgaven som ble verifisert. Å verifisere
    -- en gammel oppgave sier ingenting om kontoen som står der nå.
    SELECT o.oppgave_id INTO v_siste FROM public.kontooppgave o
     WHERE o.tenant = p_tenant AND o.mottaker_id = v_mottaker
     ORDER BY o.oppgitt_dato DESC, o.registrert DESC, o.oppgave_id DESC
     LIMIT 1;
    IF v_siste = p_oppgave_id THEN
        UPDATE public.kontofunn
           SET apen = false, lukket_ts = now()
         WHERE tenant = p_tenant AND mottaker_id = v_mottaker
           AND funntype IN ('kontoendring', 'uverifisert_konto',
                            'verifikasjon_utlopt')
           AND apen;
    END IF;
    PERFORM public.m42_evidens(
        p_tenant, v_mottaker, 'konto.verifisert', p_aktor,
        jsonb_build_object('oppgave_id', p_oppgave_id::text,
                           'metode', p_metode,
                           'gjaldt_siste', v_siste = p_oppgave_id));
    RETURN v_mottaker;
END $$;
REVOKE ALL ON FUNCTION m42_verifiser_konto(TEXT, UUID, UUID, TEXT, TEXT,
    TEXT, DATE, TEXT) FROM PUBLIC;

CREATE FUNCTION m42_sett_mottakeraktiv(
    p_tenant TEXT, p_mottaker_id UUID, p_aktiv BOOLEAN, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_for BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm42_sett_mottakeraktiv');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT m.aktiv INTO v_for FROM public.betalingsmottaker m
     WHERE m.tenant = p_tenant AND m.mottaker_id = p_mottaker_id
       FOR UPDATE;
    IF v_for IS NULL THEN
        RAISE EXCEPTION 'm42_sett_mottakeraktiv: mottakeren finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_for = p_aktiv THEN
        RETURN false;
    END IF;
    UPDATE public.betalingsmottaker SET aktiv = p_aktiv
     WHERE tenant = p_tenant AND mottaker_id = p_mottaker_id;
    IF NOT p_aktiv THEN
        UPDATE public.kontofunn
           SET apen = false, lukket_ts = now()
         WHERE tenant = p_tenant AND mottaker_id = p_mottaker_id
           AND apen;
    END IF;
    PERFORM public.m42_evidens(
        p_tenant, p_mottaker_id, 'mottaker.aktiv_satt', p_aktor,
        jsonb_build_object('aktiv', p_aktiv));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m42_sett_mottakeraktiv(TEXT, UUID, BOOLEAN, TEXT)
    FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Lesedørene.
--
--    DEN GJELDENDE KONTOEN ER DEN SISTE OPPGAVEN. Det finnes ingen
--    kolonne noe sted som holder den, og det er dom 1.
-- ------------------------------------------------------------

-- SISTE OPPGAVE FOR ÉN MOTTAKER, med sin siste verifikasjon.
CREATE FUNCTION m42_gjeldende_konto(p_tenant TEXT, p_mottaker_id UUID)
RETURNS TABLE(oppgave_id UUID, kontonummer_maske TEXT, oppgitt_av TEXT,
              oppgitt_kanal TEXT, oppgitt_dato DATE, notat TEXT,
              verifisert_av TEXT, metode TEXT, verifisert_dato DATE,
              dogn_siden_verifikasjon INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm42_gjeldende_konto');
    RETURN QUERY
    SELECT o.oppgave_id, o.kontonummer_maske, o.oppgitt_av,
           o.oppgitt_kanal, o.oppgitt_dato, o.notat,
           k.verifisert_av, k.metode, k.verifisert_dato,
           (current_date - k.verifisert_dato)::int
      FROM public.kontooppgave o
      LEFT JOIN LATERAL (
            SELECT x.verifisert_av, x.metode, x.verifisert_dato
              FROM public.kontoverifikasjon x
             WHERE x.tenant = o.tenant AND x.oppgave_id = o.oppgave_id
             ORDER BY x.verifisert_dato DESC, x.registrert DESC
             LIMIT 1) k ON true
     WHERE o.tenant = p_tenant AND o.mottaker_id = p_mottaker_id
     ORDER BY o.oppgitt_dato DESC, o.registrert DESC, o.oppgave_id DESC
     LIMIT 1;
END $$;
REVOKE ALL ON FUNCTION m42_gjeldende_konto(TEXT, UUID) FROM PUBLIC;

-- HELE HISTORIKKEN. Dette er beviset: hvem oppga hvilken konto, når, og
-- gjennom hvilken kanal — og hvem som eventuelt verifiserte den.
CREATE FUNCTION m42_kontohistorikken(p_tenant TEXT, p_mottaker_id UUID,
                                     p_grense INT)
RETURNS TABLE(oppgave_id UUID, kontonummer_maske TEXT, oppgitt_av TEXT,
              oppgitt_kanal TEXT, oppgitt_dato DATE, notat TEXT,
              registrert TIMESTAMPTZ, registrert_av TEXT,
              verifisert_av TEXT, metode TEXT, verifisert_dato DATE,
              verifikasjonsnotat TEXT, endret BOOLEAN)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm42_kontohistorikken');
    RETURN QUERY
    SELECT r.oppgave_id, r.kontonummer_maske, r.oppgitt_av,
           r.oppgitt_kanal, r.oppgitt_dato, r.notat, r.registrert,
           r.registrert_av, r.verifisert_av, r.metode,
           r.verifisert_dato, r.verifikasjonsnotat,
           -- `endret` SIER HVILKEN LINJE SOM VAR ET BYTTE. Uten den
           -- måtte leseren sammenligne maskene selv, og en maske kan
           -- gjenta seg.
           (r.forrige_hash IS NOT NULL
            AND r.forrige_hash IS DISTINCT FROM r.kontonummer_hash)
      FROM (
        SELECT o.*, k.verifisert_av, k.metode, k.verifisert_dato,
               k.notat AS verifikasjonsnotat,
               lag(o.kontonummer_hash) OVER (
                   ORDER BY o.oppgitt_dato, o.registrert, o.oppgave_id)
                   AS forrige_hash
          FROM public.kontooppgave o
          LEFT JOIN LATERAL (
                SELECT x.verifisert_av, x.metode, x.verifisert_dato,
                       x.notat
                  FROM public.kontoverifikasjon x
                 WHERE x.tenant = o.tenant
                   AND x.oppgave_id = o.oppgave_id
                 ORDER BY x.verifisert_dato DESC, x.registrert DESC
                 LIMIT 1) k ON true
         WHERE o.tenant = p_tenant
           AND o.mottaker_id = p_mottaker_id) r
     ORDER BY r.oppgitt_dato DESC, r.registrert DESC, r.oppgave_id DESC
     LIMIT greatest(least(coalesce(p_grense, 200), 5000), 1);
END $$;
REVOKE ALL ON FUNCTION m42_kontohistorikken(TEXT, UUID, INT)
    FROM PUBLIC;

CREATE FUNCTION m42_kontostatus(p_tenant TEXT)
RETURNS TABLE(mottakere INT, aktive INT, med_konto INT, verifiserte INT,
              apne_funn INT, apne_endringer INT, har_terskel BOOLEAN,
              terskelversjon INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm42_kontostatus');
    SELECT count(*)::int, count(*) FILTER (WHERE m.aktiv)::int
      INTO mottakere, aktive
      FROM public.betalingsmottaker m WHERE m.tenant = p_tenant;
    SELECT count(*)::int,
           count(*) FILTER (WHERE g.verifisert_dato IS NOT NULL)::int
      INTO med_konto, verifiserte
      FROM public.betalingsmottaker m
      JOIN LATERAL public.m42_gjeldende_konto(p_tenant, m.mottaker_id) g
        ON true
     WHERE m.tenant = p_tenant AND m.aktiv;
    SELECT count(*)::int,
           count(*) FILTER (WHERE f.funntype = 'kontoendring')::int
      INTO apne_funn, apne_endringer
      FROM public.kontofunn f WHERE f.tenant = p_tenant AND f.apen;
    SELECT true, t.versjon INTO har_terskel, terskelversjon
      FROM public.kontoterskel t WHERE t.tenant = p_tenant;
    har_terskel := coalesce(har_terskel, false);
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m42_kontostatus(TEXT) FROM PUBLIC;

CREATE FUNCTION m42_mottakerne(p_tenant TEXT, p_grense INT)
RETURNS TABLE(mottaker_id UUID, ekstern_ref TEXT, navn TEXT,
              aktiv BOOLEAN, kontonummer_maske TEXT, oppgitt_av TEXT,
              oppgitt_kanal TEXT, oppgitt_dato DATE,
              verifisert_av TEXT, metode TEXT, verifisert_dato DATE,
              oppgaver INT, apne_funn TEXT[])
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm42_mottakerne');
    RETURN QUERY
    SELECT m.mottaker_id, m.ekstern_ref, m.navn, m.aktiv,
           g.kontonummer_maske, g.oppgitt_av, g.oppgitt_kanal,
           g.oppgitt_dato, g.verifisert_av, g.metode, g.verifisert_dato,
           coalesce(a.n, 0)::int, coalesce(f.typer, ARRAY[]::TEXT[])
      FROM public.betalingsmottaker m
      LEFT JOIN LATERAL public.m42_gjeldende_konto(
            p_tenant, m.mottaker_id) g ON true
      LEFT JOIN LATERAL (
            SELECT count(*) AS n FROM public.kontooppgave o
             WHERE o.tenant = m.tenant
               AND o.mottaker_id = m.mottaker_id) a ON true
      LEFT JOIN LATERAL (
            SELECT array_agg(x.funntype ORDER BY x.funntype) AS typer
              FROM public.kontofunn x
             WHERE x.tenant = m.tenant
               AND x.mottaker_id = m.mottaker_id AND x.apen) f ON true
     WHERE m.tenant = p_tenant
     ORDER BY m.aktiv DESC, m.navn, m.ekstern_ref
     LIMIT greatest(least(coalesce(p_grense, 200), 5000), 1);
END $$;
REVOKE ALL ON FUNCTION m42_mottakerne(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m42_tersklene(p_tenant TEXT)
RETURNS TABLE(reverifikasjon_dogn INT, uverifisert_dogn INT,
              versjon INT, oppdatert TIMESTAMPTZ, oppdatert_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm42_tersklene');
    RETURN QUERY
    SELECT t.reverifikasjon_dogn, t.uverifisert_dogn, t.versjon,
           t.oppdatert, t.oppdatert_av
      FROM public.kontoterskel t WHERE t.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m42_tersklene(TEXT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 4b. Funnkandidatene.
-- ------------------------------------------------------------
CREATE FUNCTION m42_funnkandidater(p_tenant TEXT, p_dag DATE)
RETURNS TABLE(mottaker_id UUID, funntype TEXT, over_grense INT,
              fra_maske TEXT, til_maske TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_t RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm42_funnkandidater');
    SELECT * INTO v_t FROM public.kontoterskel t
     WHERE t.tenant = p_tenant;
    IF NOT FOUND THEN
        RETURN QUERY
        SELECT m.mottaker_id, 'ingen_terskel'::text, NULL::int,
               NULL::text, NULL::text
          FROM public.betalingsmottaker m
         WHERE m.tenant = p_tenant AND m.aktiv;
        RETURN;
    END IF;

    RETURN QUERY
    WITH siste AS (
        SELECT m.mottaker_id, s.oppgave_id, s.kontonummer_maske,
               s.kontonummer_hash, s.oppgitt_dato, s.forrige_maske,
               s.forrige_hash, v.verifisert_dato
          FROM public.betalingsmottaker m
          JOIN LATERAL (
                SELECT o.oppgave_id, o.kontonummer_maske,
                       o.kontonummer_hash, o.oppgitt_dato,
                       lag(o.kontonummer_maske) OVER w AS forrige_maske,
                       lag(o.kontonummer_hash) OVER w AS forrige_hash
                  FROM public.kontooppgave o
                 WHERE o.tenant = m.tenant
                   AND o.mottaker_id = m.mottaker_id
                   AND o.oppgitt_dato <= p_dag
                WINDOW w AS (ORDER BY o.oppgitt_dato, o.registrert,
                                      o.oppgave_id)
                 ORDER BY o.oppgitt_dato DESC, o.registrert DESC,
                          o.oppgave_id DESC
                 LIMIT 1) s ON true
          LEFT JOIN LATERAL (
                SELECT max(x.verifisert_dato) AS verifisert_dato
                  FROM public.kontoverifikasjon x
                 WHERE x.tenant = m.tenant
                   AND x.oppgave_id = s.oppgave_id
                   AND x.verifisert_dato <= p_dag) v ON true
         WHERE m.tenant = p_tenant AND m.aktiv)
    -- 1. KONTOENDRING. Den siste oppgaven er en ANNEN konto enn den
    --    forrige, og ingen har verifisert den. Dette er det høyeste
    --    signalet i svindelklassen, og det lukkes av en VERIFIKASJON —
    --    ikke av at det går tid.
    SELECT s.mottaker_id, 'kontoendring'::text, NULL::int,
           s.forrige_maske, s.kontonummer_maske
      FROM siste s
     WHERE s.forrige_hash IS NOT NULL
       AND s.forrige_hash IS DISTINCT FROM s.kontonummer_hash
       AND s.verifisert_dato IS NULL
    UNION ALL
    -- 2. UVERIFISERT KONTO. Ingen har sjekket den, og tenantens frist
    --    er passert. En konto ingen har sjekket er en konto ingen har
    --    sjekket — også når den aldri er endret.
    SELECT s.mottaker_id, 'uverifisert_konto'::text,
           (p_dag - s.oppgitt_dato - v_t.uverifisert_dogn)::int,
           NULL::text, s.kontonummer_maske
      FROM siste s
     WHERE s.verifisert_dato IS NULL
       AND p_dag - s.oppgitt_dato > v_t.uverifisert_dogn
    UNION ALL
    -- 3. VERIFIKASJON UTLØPT. Noen sjekket, men det er lenge siden. En
    --    verifikasjon fra 2019 sier ingenting om kontoen i dag.
    SELECT s.mottaker_id, 'verifikasjon_utlopt'::text,
           (p_dag - s.verifisert_dato - v_t.reverifikasjon_dogn)::int,
           NULL::text, s.kontonummer_maske
      FROM siste s
     WHERE s.verifisert_dato IS NOT NULL
       AND p_dag - s.verifisert_dato > v_t.reverifikasjon_dogn;
END $$;
REVOKE ALL ON FUNCTION m42_funnkandidater(TEXT, DATE) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 4c. Sveipen. KRYSS-TENANT, egen LOGIN-rolle, egen timer.
--
--     SVEIPEN STOPPER INGEN BETALING OG VERIFISERER INGENTING. Den
--     kunne, teknisk — den vet nøyaktig hvilke mottakere som byttet
--     konto i går. Men en vakt som blokkerer feil er sin egen skade, og
--     en vakt ingen har målt vet ikke hvor ofte den tar feil.
--
--     OG DEN ER IKKE DEN FØRSTE SOM SER EN KONTOENDRING: det funnet
--     skrives av døren, i samme transaksjon. Sveipen holder det åpent
--     og legger tidsfunnene til.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_kontovakt_eier;

CREATE FUNCTION m42_sveip_konto(p_grense INT DEFAULT 500)
RETURNS TABLE(tenanter INT, nye INT, oppdaterte INT, lukkede INT,
              avkortet INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_n INT; v_grense INT;
        v_dag DATE; v_naa TIMESTAMPTZ;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm42_sveip_konto: sveipen er KRYSS-TENANT og'
            ' kjøres uten tenantkontekst — en kaller som har satt en'
            ' kontekst ber om noe annet enn det denne funksjonen gjør'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_grense := greatest(least(coalesce(p_grense, 500), 5000), 1);
    v_dag := current_date;
    v_naa := now();
    tenanter := 0; nye := 0; oppdaterte := 0; lukkede := 0; avkortet := 0;
    SELECT array_agg(DISTINCT m.tenant ORDER BY m.tenant) INTO v_tenanter
      FROM public.betalingsmottaker m;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        tenanter := tenanter + 1;

        UPDATE public.kontofunn kf
           SET sist_sett_sveip = v_naa, apen = true, lukket_ts = NULL,
               over_grense = kand.over_grense,
               fra_maske = coalesce(kand.fra_maske, kf.fra_maske),
               til_maske = coalesce(kand.til_maske, kf.til_maske)
          FROM public.m42_funnkandidater(v_t, v_dag) kand
         WHERE kf.tenant = v_t AND kf.mottaker_id = kand.mottaker_id
           AND kf.funntype = kand.funntype;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        oppdaterte := oppdaterte + v_n;

        INSERT INTO public.kontofunn
            (tenant, mottaker_id, funntype, over_grense, fra_maske,
             til_maske, forst_sett, sist_sett_sveip, apen)
        SELECT v_t, kand.mottaker_id, kand.funntype, kand.over_grense,
               kand.fra_maske, kand.til_maske, v_naa, v_naa, true
          FROM public.m42_funnkandidater(v_t, v_dag) kand
         WHERE NOT EXISTS (
                SELECT 1 FROM public.kontofunn kf
                 WHERE kf.tenant = v_t
                   AND kf.mottaker_id = kand.mottaker_id
                   AND kf.funntype = kand.funntype)
         ORDER BY coalesce(kand.over_grense, 0) DESC,
                  kand.mottaker_id, kand.funntype
         LIMIT v_grense;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        nye := nye + v_n;
        IF v_n = v_grense THEN
            avkortet := avkortet + 1;
        END IF;

        UPDATE public.kontofunn kf
           SET apen = false, lukket_ts = v_naa
         WHERE kf.tenant = v_t AND kf.apen
           AND NOT EXISTS (
                SELECT 1 FROM public.m42_funnkandidater(v_t, v_dag) kand
                 WHERE kand.mottaker_id = kf.mottaker_id
                   AND kand.funntype = kf.funntype);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        lukkede := lukkede + v_n;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m42_sveip_konto(INT) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 5. Rettighetene. SP-7: kjøretidsrollen får INGEN tabellrettigheter.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_kontovakt_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m42_kontostatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m42_mottakerne(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m42_kontohistorikken(TEXT, UUID, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m42_gjeldende_konto(TEXT, UUID) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m42_tersklene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m42_sett_terskler(TEXT, INT, INT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m42_registrer_mottaker(TEXT, UUID, TEXT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m42_oppgi_konto(TEXT, UUID, UUID, TEXT, TEXT, TEXT, DATE,'
            ' TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m42_verifiser_konto(TEXT, UUID, UUID, TEXT, TEXT, TEXT,'
            ' DATE, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m42_sett_mottakeraktiv(TEXT, UUID, BOOLEAN, TEXT)'
            ' TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_kontovaktsveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m42_sveip_konto(INT)'
            ' TO disponit_kontovaktsveip';
    END IF;
END $$;
RESET ROLE;
