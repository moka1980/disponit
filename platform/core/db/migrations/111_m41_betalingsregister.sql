-- 111: M-41 betalings- og abonnementsstatusagent v1 — HISTORIKKEN.
-- Fem tenant-skopede tabeller, femten dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- V1-AVGRENSNINGEN, ORDRETT FRA POLICYEN VI SENDER UT: netthandelsmalen
-- navngir denne modulen som verifikatoren `v_betaling`, betrodd for
-- `betaling_autorisert` og `samme_betalingsmiddel`.
--
-- DEN SKARPESTE ENKELTRADEN I HELE POLICYSETTET er gatet på den:
--
--     - id: refusjon.utfor
--       modul: M-41
--       modus: auto
--       grenser: {belop_maks: "5000.00", valuta: [NOK]}
--       reversering: {type: irreversibel}
--       vilkaar: [{navn: samme_betalingsmiddel, verifikator: v_betaling}]
--
-- Automatisk. Irreversibel. Opp til fem tusen kroner. Gatet på en
-- verifikator som aldri har eksistert. Motoren feiler lukket, så den har
-- aldri fyrt — men den står der, merket auto.
--
-- OG M-25s EGEN AUTO-HANDLING VENTER PÅ DENNE:
-- `ordre.bekreft_og_fakturer` er gatet på `betaling_autorisert` herfra
-- OG `adresse_validert` fra M-19.
--
-- v1 REFUNDERER INGENTING OG AUTORISERER INGEN BETALING.
--
-- DOMMEN: en refusjon er penger ut døra, og den er irreversibel. Å ta
-- den fullmakten før noen har målt hvor ofte statusen vår stemmer med
-- betalingsleverandørens, er å la modulen definere sin egen
-- troverdighet — med kundens penger som innsats.
--
-- DOMMENE v1 HVILER PÅ, håndhevet i DATAMODELLEN:
--
--   1. HISTORIKKEN OVERSKRIVES ALDRI. Det finnes ingen kolonne som
--      holder «gjeldende status». Den gjeldende statusen ER den siste
--      hendelsen, og hver hendelse er FROSSET. M-42s dom (110), og den
--      gjelder like sterkt her: en statustabell som ble oppdatert på
--      stedet ville slettet sporet av at noe skiftet.
--
--   2. HVER STATUS HAR EN KILDE. `kilde` er et lukket sett og
--      `kilde_ref` er NOT NULL: hendelsen fra betalingsleverandøren,
--      avstemmingen, eller mennesket som førte den. En status uten
--      kilde er en PÅSTAND — og `betaling_autorisert` ville hvilt på
--      påstanden.
--
--   3. BETALINGSMIDDELET LAGRES ALDRI. `samme_betalingsmiddel` krever å
--      kunne SAMMENLIGNE to betalingsmidler, ikke å kjenne dem. Maske
--      (siste fire tegn) og SALTET hash holder, med subjektets eget
--      salt — så to like kort hos to kunder ikke ser like ut.
--
--   4. BELØP ER HELTALL I ØRE, `BIGINT`. Et flyttall i en betaling gir
--      et avvik som er noen øre feil, hver gang, for alltid.
--
--   5. ABONNEMENTSPERIODEN ERSTATTES, DEN ENDRES ALDRI. Versjonert,
--      datert, uten overlapp — samme form som prisen i 108. «Hvilken
--      abonnementsstatus gjaldt den dagen» skal ha nøyaktig ett svar.
--
-- GRENSEN MOT M-13: M-13 eier BANKPOSTENE, det som har skjedd på konto.
-- M-41 eier BETALINGSSTATUSEN slik betalingsleverandøren meldte den.
-- v1 kobler dem ikke: en bankpost uten en betalingsstatus er et funn å
-- se på, ikke en avstemming å gjøre automatisk.
--
-- SVEIPEN HAR SIN EGEN ROLLE OG SIN EGEN TIMER (095/100-110):
-- `disponit_betalingssveip` har nøyaktig ÉN rettighet — EXECUTE på
-- `m41_sveip_betalinger` — og INGEN tabellrettigheter. Sveipen
-- REFUNDERER INGENTING og AUTORISERER INGENTING; den skriver FUNN.
--
-- FORMENE ER HUSETS: tabellene eies av migrator, dørene av NOLOGIN-rollen
-- `disponit_betaling_eier`, tenant TEXT + RLS ENABLE+FORCE +
-- `tenant_isolasjon`, SP-1 i hver tenantbundet definer, ingen BYPASSRLS.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_betaling_eier') THEN
        RAISE EXCEPTION 'rollen disponit_betaling_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_betaling_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `betalingsterskel` — ÉN per tenant. GRENSENE ER TENANTENS.
CREATE TABLE betalingsterskel (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- Hvor lenge en betaling kan stå UAVKLART — verken gjennomført
    -- eller feilet — før det er et funn. En betaling ingen har fulgt
    -- opp er penger som verken er kommet eller etterlyst.
    uavklart_dogn INT NOT NULL DEFAULT 3
        CHECK (uavklart_dogn BETWEEN 0 AND 3650),
    -- Hvor mye det betalte beløpet kan avvike fra det forventede før
    -- det er et funn. I ØRE, ikke promille: et avvik på to kroner er to
    -- kroner enten fakturaen er på hundre eller hundre tusen.
    belopsavvik_ore BIGINT NOT NULL DEFAULT 0
        CHECK (belopsavvik_ore BETWEEN 0 AND 100000000),
    -- Hvor lenge en autorisasjon regnes som gyldig. En autorisasjon fra
    -- i fjor sier ingenting om kortet i dag.
    reautorisasjon_dogn INT NOT NULL DEFAULT 7
        CHECK (reautorisasjon_dogn BETWEEN 0 AND 3650),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT betalingsterskel_pk PRIMARY KEY (tenant)
);

-- `betalingssubjekt` — den vi registrerer betalinger for.
CREATE TABLE betalingssubjekt (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    subjekt_id UUID NOT NULL,
    -- Tenantens egen referanse (ordre-, kunde- eller abonnementsnummer).
    -- FRI TEKST og ingen fremmednøkkel: betalingshistorikken skal kunne
    -- stå alene.
    ekstern_ref TEXT NOT NULL CHECK (ekstern_ref ~ '[^[:space:]]'),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    -- SUBJEKTETS EGET SALT (M-42s form, 110). Uten det ville to like
    -- kort hos to kunder fått samme hash, og en angriper med ett kjent
    -- kort kunne kartlagt hvem andre som bruker det.
    hash_salt TEXT NOT NULL
        DEFAULT (gen_random_uuid()::text || gen_random_uuid()::text)
        CHECK (length(hash_salt) >= 32),
    aktiv BOOLEAN NOT NULL DEFAULT true,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT betalingssubjekt_pk PRIMARY KEY (tenant, subjekt_id),
    CONSTRAINT betalingssubjekt_ref_unik UNIQUE (tenant, ekstern_ref)
);
CREATE INDEX betalingssubjekt_aktive
    ON betalingssubjekt (tenant) WHERE aktiv;

-- `betalingshendelse` — DOM 1, 2, 3 OG 4. HOVEDBOKEN FOR STATUS.
--
-- Den gjeldende statusen er den SISTE raden her. Det finnes ingen annen
-- status noe sted i skjemaet, og det er hele poenget.
CREATE TABLE betalingshendelse (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    hendelse_id UUID NOT NULL,
    subjekt_id UUID NOT NULL,
    -- LUKKET SETT. `refundert` og `tilbakefort` REGISTRERES her når de
    -- har skjedd — de UTFØRES aldri av denne modulen.
    status TEXT NOT NULL
        CONSTRAINT betalingshendelse_status_lukket CHECK (status IN (
            'opprettet', 'autorisert', 'gjennomfort', 'feilet',
            'refundert', 'tilbakefort')),
    belop_ore BIGINT NOT NULL CHECK (belop_ore >= 0),
    -- Det FORVENTEDE beløpet, slik tenanten førte det. Avviket mellom
    -- de to er funnet; ingen av dem er en beregning modulen gjør.
    forventet_ore BIGINT CHECK (forventet_ore IS NULL
                                OR forventet_ore >= 0),
    valuta TEXT NOT NULL DEFAULT 'NOK'
        CONSTRAINT betalingshendelse_valuta_form
        CHECK (valuta ~ '^[A-Z]{3}$'),
    -- DOM 3: MASKEN OG HASHEN, regnet av døren. Kortnummeret lagres
    -- aldri. Begge er NULLbare fordi ikke enhver hendelse bærer et
    -- betalingsmiddel (en `feilet` fra en timeout gjør ikke det).
    betalingsmiddel_maske TEXT
        CONSTRAINT betalingshendelse_maske_form
        CHECK (betalingsmiddel_maske IS NULL
               OR betalingsmiddel_maske ~ '^\*+[0-9A-Za-z]{4}$'),
    betalingsmiddel_hash TEXT
        CONSTRAINT betalingshendelse_hash_form
        CHECK (betalingsmiddel_hash IS NULL
               OR betalingsmiddel_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT betalingshendelse_middel_helhet CHECK (
        (betalingsmiddel_maske IS NULL) = (betalingsmiddel_hash IS NULL)),
    -- DOM 2: HVER STATUS HAR EN KILDE. Lukket sett, og `kilde_ref` er
    -- NOT NULL — leverandørens hendelses-id, avstemmingsraden, eller
    -- referansen mennesket førte. En status uten kilde er en påstand.
    kilde TEXT NOT NULL
        CONSTRAINT betalingshendelse_kilde_lukket CHECK (kilde IN (
            'leverandor', 'avstemming', 'manuell', 'portal')),
    kilde_ref TEXT NOT NULL CHECK (kilde_ref ~ '[^[:space:]]'),
    inntruffet DATE NOT NULL,
    notat TEXT NOT NULL CHECK (notat ~ '[^[:space:]]'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT betalingshendelse_pk PRIMARY KEY (tenant, hendelse_id),
    CONSTRAINT betalingshendelse_subjekt_fk
        FOREIGN KEY (tenant, subjekt_id)
        REFERENCES betalingssubjekt (tenant, subjekt_id),
    -- SAMME KILDEHENDELSE REGISTRERES ÉN GANG. En webhook som kommer to
    -- ganger er ikke to statusskift.
    CONSTRAINT betalingshendelse_kilde_unik
        UNIQUE (tenant, subjekt_id, kilde, kilde_ref)
);
CREATE INDEX betalingshendelse_oppslag
    ON betalingshendelse (tenant, subjekt_id, inntruffet DESC,
                          registrert DESC);

-- `abonnementsperiode` — DOM 5. Versjonert, datert, FROSSET.
CREATE TABLE abonnementsperiode (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    subjekt_id UUID NOT NULL,
    versjon INT NOT NULL CHECK (versjon >= 1),
    status TEXT NOT NULL
        CONSTRAINT abonnementsperiode_status_lukket CHECK (status IN (
            'aktivt', 'pauset', 'i_restanse', 'avsluttet')),
    gyldig_fra DATE NOT NULL,
    gyldig_til DATE,
    -- HVORFOR statusen ble satt. En abonnementsstatus uten begrunnelse
    -- er en beslutning ingen kan etterprøve — og den avgjør om kunden
    -- får tjenesten.
    begrunnelse TEXT NOT NULL CHECK (begrunnelse ~ '[^[:space:]]'),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT abonnementsperiode_pk PRIMARY KEY (tenant, subjekt_id,
                                                  versjon),
    CONSTRAINT abonnementsperiode_subjekt_fk
        FOREIGN KEY (tenant, subjekt_id)
        REFERENCES betalingssubjekt (tenant, subjekt_id),
    CONSTRAINT abonnementsperiode_vindu_framover
        CHECK (gyldig_til IS NULL OR gyldig_til >= gyldig_fra)
);
CREATE INDEX abonnementsperiode_oppslag
    ON abonnementsperiode (tenant, subjekt_id, gyldig_fra DESC);
CREATE UNIQUE INDEX abonnementsperiode_en_apen
    ON abonnementsperiode (tenant, subjekt_id) WHERE gyldig_til IS NULL;

-- `betalingsfunn` — funnene. Nøklet på subjektet og typen.
CREATE TABLE betalingsfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    subjekt_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CONSTRAINT betalingsfunn_type_lukket CHECK (funntype IN (
            'uavklart_betaling', 'belopsavvik', 'autorisasjon_utlopt',
            'ingen_terskel')),
    -- DØGN for de to tidsfunnene, ØRE for `belopsavvik`.
    over_grense BIGINT,
    belop_ore BIGINT,
    forventet_ore BIGINT,
    terskelversjon INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT betalingsfunn_pk PRIMARY KEY (tenant, subjekt_id,
                                             funntype),
    CONSTRAINT betalingsfunn_subjekt_fk FOREIGN KEY (tenant, subjekt_id)
        REFERENCES betalingssubjekt (tenant, subjekt_id),
    CONSTRAINT betalingsfunn_lukking_helhet CHECK (
        (apen AND lukket_ts IS NULL)
     OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE INDEX betalingsfunn_apne
    ON betalingsfunn (tenant, funntype) WHERE apen;


-- ------------------------------------------------------------
-- 2. Vaktene.
-- ------------------------------------------------------------

CREATE FUNCTION m41_terskel_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'betalingsterskel: TRUNCATE avvist — grensene'
            ' endres ved å sette nye, ikke ved å fjerne dem under'
            ' føttene på sveipen'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'betalingsterskel: DELETE avvist — en tenant'
            ' uten grenser kan ikke måle noe, og det er en tilstand'
            ' sveipen skal SI FRA om'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.versjon <= OLD.versjon THEN
        RAISE EXCEPTION 'betalingsterskel: versjonen må øke ved endring'
            ' (% -> %)', OLD.versjon, NEW.versjon
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m41_terskel_vakt() FROM PUBLIC;
CREATE TRIGGER m41_terskel_vakt
    BEFORE UPDATE OR DELETE ON betalingsterskel
    FOR EACH ROW EXECUTE FUNCTION m41_terskel_vakt();
CREATE TRIGGER m41_terskel_ingen_truncate
    BEFORE TRUNCATE ON betalingsterskel
    EXECUTE FUNCTION m41_terskel_vakt();

CREATE FUNCTION m41_subjekt_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'betalingssubjekt: TRUNCATE avvist — et subjekt'
            ' deaktiveres, det tømmes ikke bort'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'betalingssubjekt: DELETE avvist — sett aktiv'
            ' til false. Et slettet subjekt ville tatt'
            ' betalingshistorikken med seg'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- SALTET ER FROSSET: et nytt salt ville gjort hver eldre hash
    -- usammenlignbar, og `samme_betalingsmiddel` kan da aldri besvares.
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.subjekt_id IS DISTINCT FROM OLD.subjekt_id
       OR NEW.ekstern_ref IS DISTINCT FROM OLD.ekstern_ref
       OR NEW.hash_salt IS DISTINCT FROM OLD.hash_salt
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'betalingssubjekt: identiteten, referansen og'
            ' SALTET er frosset — et nytt salt ville gjort hver eldre'
            ' hash usammenlignbar'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m41_subjekt_vakt() FROM PUBLIC;
CREATE TRIGGER m41_subjekt_vakt
    BEFORE UPDATE OR DELETE ON betalingssubjekt
    FOR EACH ROW EXECUTE FUNCTION m41_subjekt_vakt();
CREATE TRIGGER m41_subjekt_ingen_truncate
    BEFORE TRUNCATE ON betalingssubjekt
    EXECUTE FUNCTION m41_subjekt_vakt();

-- DOM 1: HISTORIKKEN OVERSKRIVES ALDRI.
--
-- Modulens skarpeste vakt. En statushendelse som kunne endres i
-- ettertid ville slettet sporet av at noe skiftet — og det er nettopp
-- skiftet `betaling_autorisert` en dag skal hvile på.
CREATE FUNCTION m41_hendelse_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'betalingshendelse: TRUNCATE avvist — en tømt'
            ' betalingshistorikk er en pengestrøm ingen kan følge'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'betalingshendelse: DELETE avvist — en feilført'
            ' hendelse rettes med en NY hendelse. Historikken er hele'
            ' beviset' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'betalingshendelse: raden er FROSSET — en'
            ' status som kunne endres i ettertid ville slettet sporet'
            ' av at noe skiftet'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m41_hendelse_vakt() FROM PUBLIC;
CREATE TRIGGER m41_hendelse_vakt
    BEFORE UPDATE OR DELETE ON betalingshendelse
    FOR EACH ROW EXECUTE FUNCTION m41_hendelse_vakt();
CREATE TRIGGER m41_hendelse_ingen_truncate
    BEFORE TRUNCATE ON betalingshendelse
    EXECUTE FUNCTION m41_hendelse_vakt();

-- DOM 5: ABONNEMENTSPERIODEN ERSTATTES, DEN ENDRES ALDRI.
CREATE FUNCTION m41_periode_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'abonnementsperiode: TRUNCATE avvist — uten'
            ' periodene er «hvilken status gjaldt da» ubesvarlig'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'abonnementsperiode: DELETE avvist — en periode'
            ' erstattes av en ny versjon'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.tenant IS DISTINCT FROM OLD.tenant
           OR NEW.subjekt_id IS DISTINCT FROM OLD.subjekt_id
           OR NEW.versjon IS DISTINCT FROM OLD.versjon
           OR NEW.status IS DISTINCT FROM OLD.status
           OR NEW.gyldig_fra IS DISTINCT FROM OLD.gyldig_fra
           OR NEW.begrunnelse IS DISTINCT FROM OLD.begrunnelse
           OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
           OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av THEN
            RAISE EXCEPTION 'abonnementsperiode: raden er FROSSET —'
                ' bare gyldig_til settes, og bare når en ny versjon'
                ' avløser denne'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF OLD.gyldig_til IS NOT NULL THEN
            RAISE EXCEPTION 'abonnementsperiode: versjon % er alt'
                ' lukket (%)', OLD.versjon, OLD.gyldig_til
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.gyldig_til IS NULL THEN
            RAISE EXCEPTION 'abonnementsperiode: en lukking må ha en'
                ' dato' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF EXISTS (SELECT 1 FROM public.abonnementsperiode a
                WHERE a.tenant = NEW.tenant
                  AND a.subjekt_id = NEW.subjekt_id
                  AND a.versjon <> NEW.versjon
                  AND a.gyldig_fra
                      <= coalesce(NEW.gyldig_til, DATE '9999-12-31')
                  AND coalesce(a.gyldig_til, DATE '9999-12-31')
                      >= NEW.gyldig_fra) THEN
        RAISE EXCEPTION 'abonnementsperiode: versjon % overlapper en'
            ' annen versjon i tid — «hvilken status gjaldt den dagen»'
            ' ville da hatt to svar', NEW.versjon
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m41_periode_vakt() FROM PUBLIC;
CREATE TRIGGER m41_periode_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON abonnementsperiode
    FOR EACH ROW EXECUTE FUNCTION m41_periode_vakt();
CREATE TRIGGER m41_periode_ingen_truncate
    BEFORE TRUNCATE ON abonnementsperiode
    EXECUTE FUNCTION m41_periode_vakt();

CREATE FUNCTION m41_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'betalingsfunn: TRUNCATE avvist — funnene'
            ' lukkes av sveipen når tilstanden er borte'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'betalingsfunn: DELETE avvist — et funn lukkes,'
            ' det slettes ikke'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.subjekt_id IS DISTINCT FROM OLD.subjekt_id
       OR NEW.funntype IS DISTINCT FROM OLD.funntype
       OR NEW.forst_sett IS DISTINCT FROM OLD.forst_sett THEN
        RAISE EXCEPTION 'betalingsfunn: identiteten og førstegangen er'
            ' frosset' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m41_funn_vakt() FROM PUBLIC;
CREATE TRIGGER m41_funn_vakt
    BEFORE UPDATE OR DELETE ON betalingsfunn
    FOR EACH ROW EXECUTE FUNCTION m41_funn_vakt();
CREATE TRIGGER m41_funn_ingen_truncate
    BEFORE TRUNCATE ON betalingsfunn EXECUTE FUNCTION m41_funn_vakt();


-- ------------------------------------------------------------
-- 2b. RLS og rettigheter.
-- ------------------------------------------------------------
ALTER TABLE betalingsterskel ENABLE ROW LEVEL SECURITY;
ALTER TABLE betalingsterskel FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON betalingsterskel
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE betalingssubjekt ENABLE ROW LEVEL SECURITY;
ALTER TABLE betalingssubjekt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON betalingssubjekt
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — tre gjerder.
CREATE POLICY m41_sveip_tenantliste ON betalingssubjekt
    FOR SELECT TO disponit_betaling_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

ALTER TABLE betalingshendelse ENABLE ROW LEVEL SECURITY;
ALTER TABLE betalingshendelse FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON betalingshendelse
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE abonnementsperiode ENABLE ROW LEVEL SECURITY;
ALTER TABLE abonnementsperiode FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON abonnementsperiode
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE betalingsfunn ENABLE ROW LEVEL SECURITY;
ALTER TABLE betalingsfunn FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON betalingsfunn
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

GRANT SELECT, INSERT, UPDATE ON betalingsterskel
    TO disponit_betaling_eier;
GRANT SELECT, INSERT, UPDATE ON betalingssubjekt
    TO disponit_betaling_eier;
-- `betalingshendelse` har VERKEN UPDATE ELLER DELETE. Historikken er
-- append-only, og det er to gjerder som sier det: rettigheten her, og
-- vakten som stanser den som likevel har den.
GRANT SELECT, INSERT ON betalingshendelse TO disponit_betaling_eier;
-- `abonnementsperiode` har ikke DELETE: en periode erstattes. UPDATE er
-- med fordi det er slik `gyldig_til` settes — vakten begrenser den til
-- det ene feltet.
GRANT SELECT, INSERT, UPDATE ON abonnementsperiode
    TO disponit_betaling_eier;
GRANT SELECT, INSERT, UPDATE ON betalingsfunn TO disponit_betaling_eier;
GRANT INSERT ON revisjonslogg TO disponit_betaling_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_betaling_eier;
RESET ROLE;


-- ------------------------------------------------------------
-- 3. Dørene. SECURITY DEFINER, eid av `disponit_betaling_eier`, SP-1.
--
--    DET FINNES INGEN DØR SOM REFUNDERER, OG INGEN SOM AUTORISERER.
--    `refundert` og `tilbakefort` er STATUSER man REGISTRERER når de
--    har skjedd — aldri handlinger denne modulen utfører.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_betaling_eier;

CREATE FUNCTION m41_evidens(p_tenant TEXT, p_subjekt_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm41_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm41_betaling', 'handling', p_handling,
        'subjekt_id', p_subjekt_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm41_betaling',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:betaling', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m41_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- NORMALISERINGEN, ETT STED (M-42s form, 110). Mellomrom og bindestrek
-- er skrivemåter, ikke forskjellige betalingsmidler.
CREATE FUNCTION m41_normaliser(p_middel TEXT)
RETURNS TEXT LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT regexp_replace(coalesce(p_middel, ''), '[^0-9A-Za-z]', '', 'g');
$$;
REVOKE ALL ON FUNCTION m41_normaliser(TEXT) FROM PUBLIC;

CREATE FUNCTION m41_sett_terskler(
    p_tenant TEXT, p_uavklart_dogn INT, p_belopsavvik_ore BIGINT,
    p_reautorisasjon_dogn INT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_ny INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm41_sett_terskler');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.betalingsterskel
        (tenant, uavklart_dogn, belopsavvik_ore, reautorisasjon_dogn,
         versjon, oppdatert, oppdatert_av)
    VALUES (p_tenant, p_uavklart_dogn, p_belopsavvik_ore,
            p_reautorisasjon_dogn, 1, now(), p_aktor)
    ON CONFLICT (tenant) DO UPDATE
        SET uavklart_dogn = excluded.uavklart_dogn,
            belopsavvik_ore = excluded.belopsavvik_ore,
            reautorisasjon_dogn = excluded.reautorisasjon_dogn,
            versjon = public.betalingsterskel.versjon + 1,
            oppdatert = now(), oppdatert_av = excluded.oppdatert_av
    RETURNING versjon INTO v_ny;
    PERFORM public.m41_evidens(
        p_tenant, NULL, 'betalingsterskel.satt', p_aktor,
        jsonb_build_object('versjon', v_ny));
    RETURN v_ny;
END $$;
REVOKE ALL ON FUNCTION m41_sett_terskler(TEXT, INT, BIGINT, INT, TEXT)
    FROM PUBLIC;

CREATE FUNCTION m41_registrer_subjekt(
    p_tenant TEXT, p_subjekt_id UUID, p_ekstern_ref TEXT, p_navn TEXT,
    p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm41_registrer_subjekt');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.betalingssubjekt
        (tenant, subjekt_id, ekstern_ref, navn, opprettet_av)
    VALUES (p_tenant, p_subjekt_id, btrim(p_ekstern_ref),
            btrim(p_navn), p_aktor);
    PERFORM public.m41_evidens(
        p_tenant, p_subjekt_id, 'subjekt.registrert', p_aktor,
        jsonb_build_object('ekstern_ref', btrim(p_ekstern_ref)));
END $$;
REVOKE ALL ON FUNCTION m41_registrer_subjekt(TEXT, UUID, TEXT, TEXT,
    TEXT) FROM PUBLIC;

-- STATUSDØREN. DOM 1, 2, 3 OG 4.
--
-- DEN REGISTRERER, DEN BESTEMMER IKKE. `p_status` er hva kilden meldte,
-- ikke hva modulen mener. `refundert` kan føres her — fordi en refusjon
-- KAN ha skjedd — men ingenting i denne migrasjonen kan UTLØSE en.
--
-- BETALINGSMIDDELET LAGRES ALDRI: døren normaliserer det, regner masken
-- og den saltede hashen, og kaster nummeret.
CREATE FUNCTION m41_registrer_status(
    p_tenant TEXT, p_hendelse_id UUID, p_subjekt_id UUID, p_status TEXT,
    p_belop_ore BIGINT, p_forventet_ore BIGINT, p_valuta TEXT,
    p_betalingsmiddel TEXT, p_kilde TEXT, p_kilde_ref TEXT,
    p_inntruffet DATE, p_notat TEXT, p_aktor TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_salt TEXT; v_aktiv BOOLEAN; v_norm TEXT;
        v_hash TEXT; v_maske TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm41_registrer_status');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    -- EN STATUS KAN IKKE INNTREFFE I FRAMTIDA. Sveipen måler «siste
    -- hendelse med dato <= i dag»; en framtidsdatert rad ville vært den
    -- siste for døren, men usynlig for sveipen — altså en måte å skjule
    -- et statusskift på ved å sette feil dato (110s lærdom).
    IF p_inntruffet IS NULL OR p_inntruffet > current_date THEN
        RAISE EXCEPTION 'm41_registrer_status: en betaling kan ikke'
            ' inntreffe i framtida (%)', p_inntruffet
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT s.hash_salt, s.aktiv INTO v_salt, v_aktiv
      FROM public.betalingssubjekt s
     WHERE s.tenant = p_tenant AND s.subjekt_id = p_subjekt_id
       FOR UPDATE;
    IF v_salt IS NULL THEN
        RAISE EXCEPTION 'm41_registrer_status: subjektet finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF NOT v_aktiv THEN
        RAISE EXCEPTION 'm41_registrer_status: subjektet er deaktivert'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_betalingsmiddel IS NOT NULL THEN
        v_norm := public.m41_normaliser(p_betalingsmiddel);
        IF length(v_norm) < 6 THEN
            RAISE EXCEPTION 'm41_registrer_status: betalingsmiddelet er'
                ' for kort til å være et betalingsmiddel (% tegn etter'
                ' normalisering)', length(v_norm)
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        v_hash := encode(sha256(convert_to(v_salt || v_norm, 'UTF8')),
                         'hex');
        v_maske := repeat('*', greatest(length(v_norm) - 4, 1))
                   || right(v_norm, 4);
    END IF;
    INSERT INTO public.betalingshendelse
        (tenant, hendelse_id, subjekt_id, status, belop_ore,
         forventet_ore, valuta, betalingsmiddel_maske,
         betalingsmiddel_hash, kilde, kilde_ref, inntruffet, notat,
         registrert_av)
    VALUES (p_tenant, p_hendelse_id, p_subjekt_id, p_status,
            p_belop_ore, p_forventet_ore,
            upper(coalesce(p_valuta, 'NOK')), v_maske, v_hash, p_kilde,
            btrim(p_kilde_ref), p_inntruffet, btrim(p_notat), p_aktor);
    PERFORM public.m41_evidens(
        p_tenant, p_subjekt_id, 'betalingsstatus.registrert', p_aktor,
        jsonb_build_object('status', p_status, 'kilde', p_kilde,
                           'maske', v_maske));
    -- NULL, ikke tom streng: `''` ville sett ut som en maske uten
    -- sifre. «Ingen betalingsmiddel oppgitt» er et eget svar.
    RETURN v_maske;
END $$;
REVOKE ALL ON FUNCTION m41_registrer_status(TEXT, UUID, UUID, TEXT,
    BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT, DATE, TEXT, TEXT)
    FROM PUBLIC;

-- ABONNEMENTSDØREN. DOM 5: perioden erstattes, den endres aldri.
CREATE FUNCTION m41_sett_abonnementsstatus(
    p_tenant TEXT, p_subjekt_id UUID, p_status TEXT, p_gyldig_fra DATE,
    p_begrunnelse TEXT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_forrige INT; v_forrige_fra DATE; v_maks INT; v_ny INT;
        v_finnes BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm41_sett_abonnementsstatus');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_begrunnelse IS NULL OR p_begrunnelse !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm41_sett_abonnementsstatus: en'
            ' abonnementsstatus uten begrunnelse er en beslutning ingen'
            ' kan etterprøve — og den avgjør om kunden får tjenesten'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT true INTO v_finnes FROM public.betalingssubjekt s
     WHERE s.tenant = p_tenant AND s.subjekt_id = p_subjekt_id
       FOR UPDATE;
    IF NOT coalesce(v_finnes, false) THEN
        RAISE EXCEPTION 'm41_sett_abonnementsstatus: subjektet finnes'
            ' ikke' USING ERRCODE = 'foreign_key_violation';
    END IF;
    SELECT a.versjon, a.gyldig_fra INTO v_forrige, v_forrige_fra
      FROM public.abonnementsperiode a
     WHERE a.tenant = p_tenant AND a.subjekt_id = p_subjekt_id
       AND a.gyldig_til IS NULL
       FOR UPDATE;
    IF v_forrige IS NOT NULL THEN
        IF p_gyldig_fra <= v_forrige_fra THEN
            RAISE EXCEPTION 'm41_sett_abonnementsstatus: den nye'
                ' perioden gjelder fra %, men den gjeldende begynte %.'
                ' En periode skrives ikke bakover',
                p_gyldig_fra, v_forrige_fra
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        UPDATE public.abonnementsperiode
           SET gyldig_til = p_gyldig_fra - 1
         WHERE tenant = p_tenant AND subjekt_id = p_subjekt_id
           AND versjon = v_forrige;
    END IF;
    SELECT max(a.versjon) INTO v_maks FROM public.abonnementsperiode a
     WHERE a.tenant = p_tenant AND a.subjekt_id = p_subjekt_id;
    v_ny := coalesce(v_maks, 0) + 1;
    INSERT INTO public.abonnementsperiode
        (tenant, subjekt_id, versjon, status, gyldig_fra, begrunnelse,
         opprettet_av)
    VALUES (p_tenant, p_subjekt_id, v_ny, p_status, p_gyldig_fra,
            btrim(p_begrunnelse), p_aktor);
    PERFORM public.m41_evidens(
        p_tenant, p_subjekt_id, 'abonnement.satt', p_aktor,
        jsonb_build_object('versjon', v_ny, 'status', p_status));
    RETURN v_ny;
END $$;
REVOKE ALL ON FUNCTION m41_sett_abonnementsstatus(TEXT, UUID, TEXT,
    DATE, TEXT, TEXT) FROM PUBLIC;

CREATE FUNCTION m41_sett_subjektaktiv(
    p_tenant TEXT, p_subjekt_id UUID, p_aktiv BOOLEAN, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_for BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm41_sett_subjektaktiv');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT s.aktiv INTO v_for FROM public.betalingssubjekt s
     WHERE s.tenant = p_tenant AND s.subjekt_id = p_subjekt_id
       FOR UPDATE;
    IF v_for IS NULL THEN
        RAISE EXCEPTION 'm41_sett_subjektaktiv: subjektet finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_for = p_aktiv THEN
        RETURN false;
    END IF;
    UPDATE public.betalingssubjekt SET aktiv = p_aktiv
     WHERE tenant = p_tenant AND subjekt_id = p_subjekt_id;
    IF NOT p_aktiv THEN
        UPDATE public.betalingsfunn
           SET apen = false, lukket_ts = now()
         WHERE tenant = p_tenant AND subjekt_id = p_subjekt_id AND apen;
    END IF;
    PERFORM public.m41_evidens(
        p_tenant, p_subjekt_id, 'subjekt.aktiv_satt', p_aktor,
        jsonb_build_object('aktiv', p_aktiv));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m41_sett_subjektaktiv(TEXT, UUID, BOOLEAN, TEXT)
    FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Lesedørene.
--
--    DEN GJELDENDE STATUSEN ER DEN SISTE HENDELSEN. Det finnes ingen
--    kolonne noe sted som holder den, og det er dom 1.
-- ------------------------------------------------------------

CREATE FUNCTION m41_gjeldende_status(p_tenant TEXT, p_subjekt_id UUID,
                                     p_dato DATE)
RETURNS TABLE(hendelse_id UUID, status TEXT, belop_ore BIGINT,
              forventet_ore BIGINT, valuta TEXT,
              betalingsmiddel_maske TEXT, kilde TEXT, kilde_ref TEXT,
              inntruffet DATE, notat TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm41_gjeldende_status');
    RETURN QUERY
    SELECT h.hendelse_id, h.status, h.belop_ore, h.forventet_ore,
           h.valuta, h.betalingsmiddel_maske, h.kilde, h.kilde_ref,
           h.inntruffet, h.notat
      FROM public.betalingshendelse h
     WHERE h.tenant = p_tenant AND h.subjekt_id = p_subjekt_id
       AND h.inntruffet <= p_dato
     ORDER BY h.inntruffet DESC, h.registrert DESC, h.hendelse_id DESC
     LIMIT 1;
END $$;
REVOKE ALL ON FUNCTION m41_gjeldende_status(TEXT, UUID, DATE)
    FROM PUBLIC;

-- HELE HISTORIKKEN. `endret` sier hvilken linje som var et STATUSSKIFT,
-- og `middel_endret` hvilken som byttet betalingsmiddel — det siste er
-- grunnlaget `samme_betalingsmiddel` en dag skal hvile på.
CREATE FUNCTION m41_statushistorikken(p_tenant TEXT, p_subjekt_id UUID,
                                      p_grense INT)
RETURNS TABLE(hendelse_id UUID, status TEXT, belop_ore BIGINT,
              forventet_ore BIGINT, valuta TEXT,
              betalingsmiddel_maske TEXT, kilde TEXT, kilde_ref TEXT,
              inntruffet DATE, notat TEXT, registrert TIMESTAMPTZ,
              registrert_av TEXT, endret BOOLEAN,
              middel_endret BOOLEAN)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm41_statushistorikken');
    RETURN QUERY
    SELECT r.hendelse_id, r.status, r.belop_ore, r.forventet_ore,
           r.valuta, r.betalingsmiddel_maske, r.kilde, r.kilde_ref,
           r.inntruffet, r.notat, r.registrert, r.registrert_av,
           (r.forrige_status IS NOT NULL
            AND r.forrige_status IS DISTINCT FROM r.status),
           (r.forrige_hash IS NOT NULL
            AND r.betalingsmiddel_hash IS NOT NULL
            AND r.forrige_hash IS DISTINCT FROM r.betalingsmiddel_hash)
      FROM (
        SELECT h.*,
               lag(h.status) OVER w AS forrige_status,
               lag(h.betalingsmiddel_hash) OVER w AS forrige_hash
          FROM public.betalingshendelse h
         WHERE h.tenant = p_tenant AND h.subjekt_id = p_subjekt_id
        WINDOW w AS (ORDER BY h.inntruffet, h.registrert,
                              h.hendelse_id)) r
     ORDER BY r.inntruffet DESC, r.registrert DESC, r.hendelse_id DESC
     LIMIT greatest(least(coalesce(p_grense, 200), 5000), 1);
END $$;
REVOKE ALL ON FUNCTION m41_statushistorikken(TEXT, UUID, INT)
    FROM PUBLIC;

CREATE FUNCTION m41_abonnement_paa_dato(p_tenant TEXT, p_subjekt_id UUID,
                                        p_dato DATE)
RETURNS TABLE(versjon INT, status TEXT, gyldig_fra DATE,
              gyldig_til DATE, begrunnelse TEXT, opprettet_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm41_abonnement_paa_dato');
    RETURN QUERY
    SELECT a.versjon, a.status, a.gyldig_fra, a.gyldig_til,
           a.begrunnelse, a.opprettet_av
      FROM public.abonnementsperiode a
     WHERE a.tenant = p_tenant AND a.subjekt_id = p_subjekt_id
       AND a.gyldig_fra <= p_dato
       AND (a.gyldig_til IS NULL OR a.gyldig_til >= p_dato);
END $$;
REVOKE ALL ON FUNCTION m41_abonnement_paa_dato(TEXT, UUID, DATE)
    FROM PUBLIC;

CREATE FUNCTION m41_betalingsstatus(p_tenant TEXT)
RETURNS TABLE(subjekter INT, aktive INT, med_status INT,
              gjennomforte INT, apne_funn INT, apne_avvik INT,
              har_terskel BOOLEAN, terskelversjon INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm41_betalingsstatus');
    SELECT count(*)::int, count(*) FILTER (WHERE s.aktiv)::int
      INTO subjekter, aktive
      FROM public.betalingssubjekt s WHERE s.tenant = p_tenant;
    SELECT count(*)::int,
           count(*) FILTER (WHERE g.status = 'gjennomfort')::int
      INTO med_status, gjennomforte
      FROM public.betalingssubjekt s
      JOIN LATERAL public.m41_gjeldende_status(
            p_tenant, s.subjekt_id, current_date) g ON true
     WHERE s.tenant = p_tenant AND s.aktiv;
    SELECT count(*)::int,
           count(*) FILTER (WHERE f.funntype = 'belopsavvik')::int
      INTO apne_funn, apne_avvik
      FROM public.betalingsfunn f
     WHERE f.tenant = p_tenant AND f.apen;
    SELECT true, t.versjon INTO har_terskel, terskelversjon
      FROM public.betalingsterskel t WHERE t.tenant = p_tenant;
    har_terskel := coalesce(har_terskel, false);
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m41_betalingsstatus(TEXT) FROM PUBLIC;

CREATE FUNCTION m41_subjektene(p_tenant TEXT, p_grense INT)
RETURNS TABLE(subjekt_id UUID, ekstern_ref TEXT, navn TEXT,
              aktiv BOOLEAN, status TEXT, belop_ore BIGINT,
              forventet_ore BIGINT, valuta TEXT,
              betalingsmiddel_maske TEXT, kilde TEXT, inntruffet DATE,
              abonnementsstatus TEXT, hendelser INT, apne_funn TEXT[])
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm41_subjektene');
    RETURN QUERY
    SELECT s.subjekt_id, s.ekstern_ref, s.navn, s.aktiv, g.status,
           g.belop_ore, g.forventet_ore, g.valuta,
           g.betalingsmiddel_maske, g.kilde, g.inntruffet, a.status,
           coalesce(h.n, 0)::int, coalesce(f.typer, ARRAY[]::TEXT[])
      FROM public.betalingssubjekt s
      LEFT JOIN LATERAL public.m41_gjeldende_status(
            p_tenant, s.subjekt_id, current_date) g ON true
      LEFT JOIN LATERAL public.m41_abonnement_paa_dato(
            p_tenant, s.subjekt_id, current_date) a ON true
      LEFT JOIN LATERAL (
            SELECT count(*) AS n FROM public.betalingshendelse x
             WHERE x.tenant = s.tenant
               AND x.subjekt_id = s.subjekt_id) h ON true
      LEFT JOIN LATERAL (
            SELECT array_agg(x.funntype ORDER BY x.funntype) AS typer
              FROM public.betalingsfunn x
             WHERE x.tenant = s.tenant
               AND x.subjekt_id = s.subjekt_id AND x.apen) f ON true
     WHERE s.tenant = p_tenant
     ORDER BY s.aktiv DESC, s.ekstern_ref
     LIMIT greatest(least(coalesce(p_grense, 200), 5000), 1);
END $$;
REVOKE ALL ON FUNCTION m41_subjektene(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m41_tersklene(p_tenant TEXT)
RETURNS TABLE(uavklart_dogn INT, belopsavvik_ore BIGINT,
              reautorisasjon_dogn INT, versjon INT,
              oppdatert TIMESTAMPTZ, oppdatert_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm41_tersklene');
    RETURN QUERY
    SELECT t.uavklart_dogn, t.belopsavvik_ore, t.reautorisasjon_dogn,
           t.versjon, t.oppdatert, t.oppdatert_av
      FROM public.betalingsterskel t WHERE t.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m41_tersklene(TEXT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 4b. Funnkandidatene.
-- ------------------------------------------------------------
CREATE FUNCTION m41_funnkandidater(p_tenant TEXT, p_dag DATE)
RETURNS TABLE(subjekt_id UUID, funntype TEXT, over_grense BIGINT,
              belop_ore BIGINT, forventet_ore BIGINT,
              terskelversjon INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_t RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm41_funnkandidater');
    SELECT * INTO v_t FROM public.betalingsterskel t
     WHERE t.tenant = p_tenant;
    IF NOT FOUND THEN
        RETURN QUERY
        SELECT s.subjekt_id, 'ingen_terskel'::text, NULL::bigint,
               NULL::bigint, NULL::bigint, NULL::int
          FROM public.betalingssubjekt s
         WHERE s.tenant = p_tenant AND s.aktiv;
        RETURN;
    END IF;

    RETURN QUERY
    WITH siste AS (
        SELECT s.subjekt_id, g.status, g.belop_ore, g.forventet_ore,
               g.inntruffet
          FROM public.betalingssubjekt s
          JOIN LATERAL public.m41_gjeldende_status(
                p_tenant, s.subjekt_id, p_dag) g ON true
         WHERE s.tenant = p_tenant AND s.aktiv)
    -- 1. UAVKLART BETALING: verken gjennomført eller feilet, og
    --    tenantens frist er passert. En betaling ingen har fulgt opp er
    --    penger som verken er kommet eller etterlyst.
    SELECT s.subjekt_id, 'uavklart_betaling'::text,
           (p_dag - s.inntruffet - v_t.uavklart_dogn)::bigint,
           s.belop_ore, s.forventet_ore, v_t.versjon
      FROM siste s
     WHERE s.status IN ('opprettet', 'autorisert')
       AND p_dag - s.inntruffet > v_t.uavklart_dogn
    UNION ALL
    -- 2. BELØPSAVVIK: det betalte er ikke det forventede, utover
    --    tenantens grense. HELTALLSSAMMENLIGNING, ingen divisjon —
    --    `abs()` på øre, aldri en prosentregning modulen fant på.
    SELECT s.subjekt_id, 'belopsavvik'::text,
           (abs(s.belop_ore - s.forventet_ore)
            - v_t.belopsavvik_ore)::bigint,
           s.belop_ore, s.forventet_ore, v_t.versjon
      FROM siste s
     WHERE s.forventet_ore IS NOT NULL
       AND s.status IN ('gjennomfort', 'autorisert')
       AND abs(s.belop_ore - s.forventet_ore) > v_t.belopsavvik_ore
    UNION ALL
    -- 3. AUTORISASJON UTLØPT: autorisert, men lenge siden. En
    --    autorisasjon fra i fjor sier ingenting om kortet i dag.
    SELECT s.subjekt_id, 'autorisasjon_utlopt'::text,
           (p_dag - s.inntruffet - v_t.reautorisasjon_dogn)::bigint,
           s.belop_ore, s.forventet_ore, v_t.versjon
      FROM siste s
     WHERE s.status = 'autorisert'
       AND p_dag - s.inntruffet > v_t.reautorisasjon_dogn;
END $$;
REVOKE ALL ON FUNCTION m41_funnkandidater(TEXT, DATE) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 4c. Sveipen. KRYSS-TENANT, egen LOGIN-rolle, egen timer.
--
--     SVEIPEN REFUNDERER INGENTING OG AUTORISERER INGENTING. Den
--     kunne, teknisk — den vet nøyaktig hvilke betalinger som står
--     uavklart og hvilke beløp som avviker. Men en refusjon er penger
--     ut døra og irreversibel, og en autorisasjon er det som slipper en
--     automatisk handling gjennom. Begge er fullmakter, ikke målinger.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_betaling_eier;

CREATE FUNCTION m41_sveip_betalinger(p_grense INT DEFAULT 500)
RETURNS TABLE(tenanter INT, nye INT, oppdaterte INT, lukkede INT,
              avkortet INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_n INT; v_grense INT;
        v_dag DATE; v_naa TIMESTAMPTZ;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm41_sveip_betalinger: sveipen er KRYSS-TENANT'
            ' og kjøres uten tenantkontekst — en kaller som har satt en'
            ' kontekst ber om noe annet enn det denne funksjonen gjør'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_grense := greatest(least(coalesce(p_grense, 500), 5000), 1);
    v_dag := current_date;
    v_naa := now();
    tenanter := 0; nye := 0; oppdaterte := 0; lukkede := 0; avkortet := 0;
    SELECT array_agg(DISTINCT s.tenant ORDER BY s.tenant) INTO v_tenanter
      FROM public.betalingssubjekt s;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        tenanter := tenanter + 1;

        UPDATE public.betalingsfunn bf
           SET sist_sett_sveip = v_naa, apen = true, lukket_ts = NULL,
               over_grense = kand.over_grense,
               belop_ore = kand.belop_ore,
               forventet_ore = kand.forventet_ore,
               terskelversjon = kand.terskelversjon
          FROM public.m41_funnkandidater(v_t, v_dag) kand
         WHERE bf.tenant = v_t AND bf.subjekt_id = kand.subjekt_id
           AND bf.funntype = kand.funntype;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        oppdaterte := oppdaterte + v_n;

        INSERT INTO public.betalingsfunn
            (tenant, subjekt_id, funntype, over_grense, belop_ore,
             forventet_ore, terskelversjon, forst_sett, sist_sett_sveip,
             apen)
        SELECT v_t, kand.subjekt_id, kand.funntype, kand.over_grense,
               kand.belop_ore, kand.forventet_ore, kand.terskelversjon,
               v_naa, v_naa, true
          FROM public.m41_funnkandidater(v_t, v_dag) kand
         WHERE NOT EXISTS (
                SELECT 1 FROM public.betalingsfunn bf
                 WHERE bf.tenant = v_t
                   AND bf.subjekt_id = kand.subjekt_id
                   AND bf.funntype = kand.funntype)
         ORDER BY coalesce(kand.over_grense, 0) DESC,
                  kand.subjekt_id, kand.funntype
         LIMIT v_grense;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        nye := nye + v_n;
        IF v_n = v_grense THEN
            avkortet := avkortet + 1;
        END IF;

        UPDATE public.betalingsfunn bf
           SET apen = false, lukket_ts = v_naa
         WHERE bf.tenant = v_t AND bf.apen
           AND NOT EXISTS (
                SELECT 1 FROM public.m41_funnkandidater(v_t, v_dag) kand
                 WHERE kand.subjekt_id = bf.subjekt_id
                   AND kand.funntype = bf.funntype);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        lukkede := lukkede + v_n;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m41_sveip_betalinger(INT) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 5. Rettighetene. SP-7: kjøretidsrollen får INGEN tabellrettigheter.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_betaling_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m41_betalingsstatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m41_subjektene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m41_statushistorikken(TEXT, UUID, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m41_gjeldende_status(TEXT, UUID, DATE) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m41_abonnement_paa_dato(TEXT, UUID, DATE) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m41_tersklene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m41_sett_terskler(TEXT, INT, BIGINT, INT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m41_registrer_subjekt(TEXT, UUID, TEXT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m41_registrer_status(TEXT, UUID, UUID, TEXT, BIGINT,'
            ' BIGINT, TEXT, TEXT, TEXT, TEXT, DATE, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m41_sett_abonnementsstatus(TEXT, UUID, TEXT, DATE, TEXT,'
            ' TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m41_sett_subjektaktiv(TEXT, UUID, BOOLEAN, TEXT)'
            ' TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_betalingssveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m41_sveip_betalinger(INT)'
            ' TO disponit_betalingssveip';
    END IF;
END $$;
RESET ROLE;
