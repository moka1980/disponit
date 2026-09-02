-- 108: M-26 prisbok- og tilbudsagent v1 — PRISBOKA.
-- Fem tenant-skopede tabeller, fjorten dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- V1-AVGRENSNINGEN, ORDRETT FRA POLICYENE VI SENDER UT: ALLE TRE
-- bransjemalene navngir denne modulen som verifikatoren `v_prisbok`,
-- betrodd for `priser_fra_prisbok`, `laste_klausuler_uendret` og
-- `standard_forbehold_inkludert` — og bruker dem til å slippe
-- `tilbud.generer` gjennom som `modus: auto`. `pris.endre` står som
-- `alltid_stopp` i policyen; det er den ene handlingen malene selv ikke
-- tør automatisere, og v1 er enig.
--
-- v1 SETTER INGEN PRIS, GENERERER INGET TILBUD OG ATTESTERER INGENTING.
--
-- DOMMEN: hver pris i boka er skrevet av et menneske gjennom en dør. En
-- modul som beregnet en ny pris ville tatt en beslutning som avgjør hva
-- virksomheten tjener — på et grunnlag ingen har målt. Katalogen deler
-- dessuten marginbeskyttelsen: M-24 OPPDAGER kostnadsøkningen, M-26 er
-- boka en ny pris til slutt må skrives inn i. Ingen av dem regner den
-- ut.
--
-- OG DEN GENERERER INGET TILBUD. Et tilbud er et bindende utspill mot en
-- kunde. Det finnes derfor ingen tilbudstabell her, ingen
-- dokumentgenerering og ingen kobling til M-5s maler.
--
-- DOMMENE v1 HVILER PÅ, håndhevet i DATAMODELLEN:
--
--   1. BELØP ER HELTALL I MINSTE ENHET (øre), `BIGINT`, uten unntak. En
--      prisbok med et flyttall gir et tilbud som er noen øre feil, hver
--      gang, for alltid.
--
--   2. EN PRIS ENDRES ALDRI — DEN ERSTATTES. Raden er FROSSET etter at
--      den er skrevet; en ny pris er en NY VERSJON, og døren lukker den
--      forrige i samme transaksjon. Det er hele grunnen til at modulen
--      finnes: `priser_fra_prisbok` er en attestasjon om at et tilbud
--      siterte boka, og den er verdiløs hvis ingen kan svare på HVA SOM
--      STO DER DA.
--
--   3. TO VERSJONER AV SAMME PRIS KAN IKKE OVERLAPPE I TID. Da ville
--      «hvilken pris gjaldt den dagen» hatt to svar, og et tilbud gitt i
--      går kunne blitt gjenfunnet mot to forskjellige tall. Vakten
--      håndhever det; en `EXCLUDE`-begrensning ville krevd btree_gist,
--      og en avhengighet til en utvidelse for én regel er en pris ingen
--      har bedt om.
--
--   4. EN KLAUSUL BÆRER SIN EGEN HASH. `laste_klausuler_uendret` kan
--      bare besvares av noe som VET hva teksten var — og en tekst som
--      kunne endres i stillhet er nøyaktig det attestasjonen skal kunne
--      benekte. Hashen regnes i basen, av teksten selv, og er ikke et
--      felt noen kan sette.
--
-- GRENSEN MOT M-24 (105): M-24 eier PRISEN VI BETALER. M-26 eier PRISEN
-- VI TAR. De to møtes i marginen, og ingen av dem regner den ut.
--
-- SVEIPEN HAR SIN EGEN ROLLE OG SIN EGEN TIMER (095/100–107):
-- `disponit_prisboksveip` har nøyaktig ÉN rettighet — EXECUTE på
-- `m26_sveip_prisbok` — og INGEN tabellrettigheter. Sveipen SETTER INGEN
-- PRIS og FORLENGER INGEN GYLDIGHET; den skriver FUNN.
--
-- FORMENE ER HUSETS: tabellene eies av migrator, dørene av NOLOGIN-rollen
-- `disponit_prisbok_eier`, tenant TEXT + RLS ENABLE+FORCE +
-- `tenant_isolasjon`, SP-1 i hver tenantbundet definer, ingen BYPASSRLS.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_prisbok_eier') THEN
        RAISE EXCEPTION 'rollen disponit_prisbok_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_prisbok_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `prisbokterskel` — ÉN per tenant.
CREATE TABLE prisbokterskel (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- Hvor mange PROMILLE under listeprisen en avtalt pris kan ligge før
    -- det er et funn. Promille og ikke prosent: en grense på 12,5 %
    -- finnes, og 125 er eksakt der 12.5 ikke er.
    rabattgrense_promille INT NOT NULL DEFAULT 100
        CHECK (rabattgrense_promille BETWEEN 0 AND 1000),
    -- Hvor mange døgn før en pris utløper den skal varsles.
    utlop_varsel_dogn INT NOT NULL DEFAULT 30
        CHECK (utlop_varsel_dogn BETWEEN 0 AND 3650),
    -- Hvor lenge et aktivt produkt kan stå uten en gyldig pris før det
    -- er et funn. Et produkt uten pris er et produkt ingen kan gi
    -- tilbud på.
    uten_pris_dogn INT NOT NULL DEFAULT 7
        CHECK (uten_pris_dogn BETWEEN 0 AND 3650),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT prisbokterskel_pk PRIMARY KEY (tenant)
);

-- `produkt` — det vi selger.
CREATE TABLE produkt (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    produkt_id UUID NOT NULL,
    -- Tenantens egen kode. Ingen validering av format: en modul som
    -- krevde et bestemt format ville låst boka til ett verktøy.
    kode TEXT NOT NULL CHECK (kode ~ '[^[:space:]]'),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    -- Enheten prisen gjelder for («time», «stk», «måned»). Tenantens
    -- ord, ikke et lukket sett.
    enhet TEXT NOT NULL CHECK (enhet ~ '[^[:space:]]'),
    aktiv BOOLEAN NOT NULL DEFAULT true,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT produkt_pk PRIMARY KEY (tenant, produkt_id),
    CONSTRAINT produkt_kode_unik UNIQUE (tenant, kode)
);
CREATE INDEX produkt_aktive ON produkt (tenant) WHERE aktiv;

-- `pris` — DOM 2 OG 3 I TABELLFORM. Versjonert, datert, FROSSET.
--
-- «Hva sto her da vi ga det tilbudet» er hele spørsmålet modulen finnes
-- for å svare på. En pris som kunne endres i ettertid ville gjort
-- `priser_fra_prisbok` til en attestasjon om noe ingen kan etterprøve.
CREATE TABLE pris (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    produkt_id UUID NOT NULL,
    versjon INT NOT NULL CHECK (versjon >= 1),
    listepris_ore BIGINT NOT NULL CHECK (listepris_ore >= 0),
    valuta TEXT NOT NULL DEFAULT 'NOK'
        CONSTRAINT pris_valuta_form CHECK (valuta ~ '^[A-Z]{3}$'),
    gyldig_fra DATE NOT NULL,
    -- ÅPEN ENDE ER LOVLIG: den gjeldende prisen har ingen sluttdato.
    -- Døren SETTER den når en ny versjon kommer.
    gyldig_til DATE,
    -- HVORFOR prisen ble satt. En prisendring uten begrunnelse er en
    -- beslutning ingen kan etterprøve — og prisen er det virksomheten
    -- tjener på.
    begrunnelse TEXT NOT NULL CHECK (begrunnelse ~ '[^[:space:]]'),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT pris_pk PRIMARY KEY (tenant, produkt_id, versjon),
    CONSTRAINT pris_produkt_fk FOREIGN KEY (tenant, produkt_id)
        REFERENCES produkt (tenant, produkt_id),
    CONSTRAINT pris_vindu_framover
        CHECK (gyldig_til IS NULL OR gyldig_til >= gyldig_fra)
);
CREATE INDEX pris_oppslag
    ON pris (tenant, produkt_id, gyldig_fra DESC);
-- ÉN ÅPEN PRIS PER PRODUKT. To samtidige ville gjort «hva koster dette
-- nå» til et spørsmål med to svar.
CREATE UNIQUE INDEX pris_en_apen
    ON pris (tenant, produkt_id) WHERE gyldig_til IS NULL;

-- `klausul` — DOM 4. Standardforbeholdene, versjonert, med HASH.
--
-- `laste_klausuler_uendret` kan bare besvares av noe som VET hva teksten
-- var. Hashen regnes i basen, av teksten selv, og er ikke et felt noen
-- kan sette — en hash kalleren oppga ville vært en påstand om innholdet,
-- ikke en måling av det.
CREATE TABLE klausul (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kode TEXT NOT NULL CHECK (kode ~ '[^[:space:]]'),
    versjon INT NOT NULL CHECK (versjon >= 1),
    tittel TEXT NOT NULL CHECK (tittel ~ '[^[:space:]]'),
    tekst TEXT NOT NULL CHECK (tekst ~ '[^[:space:]]'),
    -- sha256 over teksten, regnet av døren. Kolonnen er ikke NULLbar og
    -- vakten krever at den STEMMER med teksten — også ved direkte DML.
    tekst_hash TEXT NOT NULL
        CONSTRAINT klausul_hash_form CHECK (tekst_hash ~ '^[0-9a-f]{64}$'),
    -- Om klausulen skal med i ethvert tilbud. `standard_forbehold
    -- _inkludert` i policyen hviler på nettopp dette flagget.
    standard BOOLEAN NOT NULL DEFAULT false,
    gyldig_fra DATE NOT NULL,
    gyldig_til DATE,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT klausul_pk PRIMARY KEY (tenant, kode, versjon),
    CONSTRAINT klausul_vindu_framover
        CHECK (gyldig_til IS NULL OR gyldig_til >= gyldig_fra)
);
CREATE UNIQUE INDEX klausul_en_apen
    ON klausul (tenant, kode) WHERE gyldig_til IS NULL;

-- `prisbokfunn` — funnene. Nøklet på produktet og typen.
CREATE TABLE prisbokfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    produkt_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CONSTRAINT prisbokfunn_type_lukket CHECK (funntype IN (
            'uten_gyldig_pris', 'pris_utloper_snart', 'ingen_terskel')),
    -- Ett tall med én betydning per funntype: døgn i begge tilfeller.
    over_grense INT,
    prisversjon INT,
    terskelversjon INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT prisbokfunn_pk PRIMARY KEY (tenant, produkt_id, funntype),
    CONSTRAINT prisbokfunn_produkt_fk FOREIGN KEY (tenant, produkt_id)
        REFERENCES produkt (tenant, produkt_id),
    CONSTRAINT prisbokfunn_lukking_helhet CHECK (
        (apen AND lukket_ts IS NULL)
     OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE INDEX prisbokfunn_apne
    ON prisbokfunn (tenant, funntype) WHERE apen;


-- ------------------------------------------------------------
-- 2. Vaktene.
-- ------------------------------------------------------------

CREATE FUNCTION m26_terskel_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'prisbokterskel: TRUNCATE avvist — grensene'
            ' endres ved å sette nye, ikke ved å fjerne dem under'
            ' føttene på sveipen'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'prisbokterskel: DELETE avvist — en tenant uten'
            ' grenser kan ikke måle noe, og det er en tilstand sveipen'
            ' skal SI FRA om' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.versjon <= OLD.versjon THEN
        RAISE EXCEPTION 'prisbokterskel: versjonen må øke ved endring'
            ' (% -> %)', OLD.versjon, NEW.versjon
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m26_terskel_vakt() FROM PUBLIC;
CREATE TRIGGER m26_terskel_vakt
    BEFORE UPDATE OR DELETE ON prisbokterskel
    FOR EACH ROW EXECUTE FUNCTION m26_terskel_vakt();
CREATE TRIGGER m26_terskel_ingen_truncate
    BEFORE TRUNCATE ON prisbokterskel
    EXECUTE FUNCTION m26_terskel_vakt();

CREATE FUNCTION m26_produkt_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'produkt: TRUNCATE avvist — et produkt'
            ' deaktiveres, det tømmes ikke bort'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'produkt: DELETE avvist — sett aktiv til false.'
            ' Et slettet produkt ville tatt prishistorikken med seg, og'
            ' den er svaret på hva et gammelt tilbud siterte'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.produkt_id IS DISTINCT FROM OLD.produkt_id
       OR NEW.kode IS DISTINCT FROM OLD.kode
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'produkt: identiteten og koden er frosset — et'
            ' tilbud som siterte koden skal fortsatt finne den'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m26_produkt_vakt() FROM PUBLIC;
CREATE TRIGGER m26_produkt_vakt
    BEFORE UPDATE OR DELETE ON produkt
    FOR EACH ROW EXECUTE FUNCTION m26_produkt_vakt();
CREATE TRIGGER m26_produkt_ingen_truncate
    BEFORE TRUNCATE ON produkt EXECUTE FUNCTION m26_produkt_vakt();

-- DOM 2 OG 3: EN PRIS ENDRES ALDRI, OG TO VERSJONER OVERLAPPER IKKE.
--
-- Dette er modulens skarpeste vakt. Uten den er `priser_fra_prisbok` en
-- attestasjon om noe ingen kan etterprøve: en pris som ble skrevet om i
-- ettertid gjør hvert tilbud som siterte den til en gjetning, og to
-- overlappende versjoner gjør «hva gjaldt den dagen» til et spørsmål med
-- to svar.
CREATE FUNCTION m26_pris_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'pris: TRUNCATE avvist — en tømt prisbok gjør'
            ' hvert tilbud som siterte den til en gjetning'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'pris: DELETE avvist — en pris erstattes av en'
            ' ny versjon, den slettes aldri. «Hva sto her da» er hele'
            ' spørsmålet boka finnes for å svare på'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        -- DET ENESTE SOM KAN ENDRES ER `gyldig_til`, og bare fra ÅPEN
        -- til lukket: det er slik en ny versjon avløser den forrige.
        IF NEW.tenant IS DISTINCT FROM OLD.tenant
           OR NEW.produkt_id IS DISTINCT FROM OLD.produkt_id
           OR NEW.versjon IS DISTINCT FROM OLD.versjon
           OR NEW.listepris_ore IS DISTINCT FROM OLD.listepris_ore
           OR NEW.valuta IS DISTINCT FROM OLD.valuta
           OR NEW.gyldig_fra IS DISTINCT FROM OLD.gyldig_fra
           OR NEW.begrunnelse IS DISTINCT FROM OLD.begrunnelse
           OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
           OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av THEN
            RAISE EXCEPTION 'pris: prisen er FROSSET — en ny pris er en'
                ' ny versjon, ikke en omskriving av den gamle'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF OLD.gyldig_til IS NOT NULL THEN
            RAISE EXCEPTION 'pris: versjonen er alt lukket (% til %) og'
                ' gjenåpnes ikke', OLD.gyldig_fra, OLD.gyldig_til
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    -- DOM 3: INGEN OVERLAPP. `EXCLUDE` ville krevd btree_gist, og en
    -- utvidelsesavhengighet for én regel er en pris ingen har bedt om.
    IF EXISTS (
        SELECT 1 FROM public.pris p
         WHERE p.tenant = NEW.tenant AND p.produkt_id = NEW.produkt_id
           AND p.versjon <> NEW.versjon
           AND p.gyldig_fra <= coalesce(NEW.gyldig_til, DATE '9999-12-31')
           AND coalesce(p.gyldig_til, DATE '9999-12-31') >= NEW.gyldig_fra
    ) THEN
        RAISE EXCEPTION 'pris: versjon % overlapper en annen periode for'
            ' samme produkt — da har «hvilken pris gjaldt» to svar',
            NEW.versjon USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m26_pris_vakt() FROM PUBLIC;
CREATE TRIGGER m26_pris_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON pris
    FOR EACH ROW EXECUTE FUNCTION m26_pris_vakt();
CREATE TRIGGER m26_pris_ingen_truncate
    BEFORE TRUNCATE ON pris EXECUTE FUNCTION m26_pris_vakt();

-- DOM 4: HASHEN MÅ STEMME MED TEKSTEN, også ved direkte DML.
--
-- En hash kalleren oppga ville vært en PÅSTAND om innholdet, ikke en
-- MÅLING av det — og `laste_klausuler_uendret` ville da vært en
-- attestasjon om påstanden, ikke om teksten.
CREATE FUNCTION m26_klausul_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'klausul: TRUNCATE avvist — en tømt'
            ' klausulsamling gjør «laste klausuler uendret» til noe'
            ' ingen kan svare på'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'klausul: DELETE avvist — en klausul erstattes'
            ' av en ny versjon' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.tenant IS DISTINCT FROM OLD.tenant
           OR NEW.kode IS DISTINCT FROM OLD.kode
           OR NEW.versjon IS DISTINCT FROM OLD.versjon
           OR NEW.tittel IS DISTINCT FROM OLD.tittel
           OR NEW.tekst IS DISTINCT FROM OLD.tekst
           OR NEW.tekst_hash IS DISTINCT FROM OLD.tekst_hash
           -- `standard` HØRER MED: `standard_forbehold_inkludert`
           -- hviler på nettopp dette flagget, og et forbehold som
           -- stille sluttet å være standard ville gjort attestasjonen
           -- sann om noe annet enn det den ble gitt for.
           OR NEW.standard IS DISTINCT FROM OLD.standard
           OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
           OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av
           OR NEW.gyldig_fra IS DISTINCT FROM OLD.gyldig_fra THEN
            RAISE EXCEPTION 'klausul: teksten er FROSSET — en endret'
                ' klausul er en ny versjon. En tekst som kunne endres i'
                ' stillhet er nøyaktig det «laste klausuler uendret»'
                ' skal kunne benekte'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF OLD.gyldig_til IS NOT NULL THEN
            RAISE EXCEPTION 'klausul: versjonen er alt lukket og'
                ' gjenåpnes ikke' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.tekst_hash IS DISTINCT FROM
       encode(sha256(convert_to(NEW.tekst, 'UTF8')), 'hex') THEN
        RAISE EXCEPTION 'klausul: hashen stemmer ikke med teksten — en'
            ' hash kalleren oppga er en påstand om innholdet, ikke en'
            ' måling av det' USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.klausul k
         WHERE k.tenant = NEW.tenant AND k.kode = NEW.kode
           AND k.versjon <> NEW.versjon
           AND k.gyldig_fra <= coalesce(NEW.gyldig_til, DATE '9999-12-31')
           AND coalesce(k.gyldig_til, DATE '9999-12-31') >= NEW.gyldig_fra
    ) THEN
        RAISE EXCEPTION 'klausul: versjon % overlapper en annen periode'
            ' for samme kode', NEW.versjon
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m26_klausul_vakt() FROM PUBLIC;
CREATE TRIGGER m26_klausul_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON klausul
    FOR EACH ROW EXECUTE FUNCTION m26_klausul_vakt();
CREATE TRIGGER m26_klausul_ingen_truncate
    BEFORE TRUNCATE ON klausul EXECUTE FUNCTION m26_klausul_vakt();

CREATE FUNCTION m26_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    -- Her er armen IKKE kosmetisk (CodeRabbit på 104).
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'prisbokfunn: TRUNCATE avvist — et funn lukkes,'
            ' det tømmes ikke bort'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'prisbokfunn: DELETE avvist — et funn lukkes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.tenant IS DISTINCT FROM OLD.tenant
           OR NEW.produkt_id IS DISTINCT FROM OLD.produkt_id
           OR NEW.funntype IS DISTINCT FROM OLD.funntype
           OR NEW.forst_sett IS DISTINCT FROM OLD.forst_sett THEN
            RAISE EXCEPTION 'prisbokfunn: identiteten og førstegangen er'
                ' frosset' USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m26_funn_vakt() FROM PUBLIC;
CREATE TRIGGER m26_funn_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON prisbokfunn
    FOR EACH ROW EXECUTE FUNCTION m26_funn_vakt();
CREATE TRIGGER m26_funn_ingen_truncate
    BEFORE TRUNCATE ON prisbokfunn EXECUTE FUNCTION m26_funn_vakt();


-- ------------------------------------------------------------
-- 2b. RLS og rettigheter.
-- ------------------------------------------------------------
ALTER TABLE prisbokterskel ENABLE ROW LEVEL SECURITY;
ALTER TABLE prisbokterskel FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON prisbokterskel
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE produkt ENABLE ROW LEVEL SECURITY;
ALTER TABLE produkt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON produkt
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — tre gjerder.
CREATE POLICY m26_sveip_tenantliste ON produkt
    FOR SELECT TO disponit_prisbok_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

ALTER TABLE pris ENABLE ROW LEVEL SECURITY;
ALTER TABLE pris FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON pris
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE klausul ENABLE ROW LEVEL SECURITY;
ALTER TABLE klausul FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON klausul
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE prisbokfunn ENABLE ROW LEVEL SECURITY;
ALTER TABLE prisbokfunn FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON prisbokfunn
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

GRANT SELECT, INSERT, UPDATE ON prisbokterskel
    TO disponit_prisbok_eier;
GRANT SELECT, INSERT, UPDATE ON produkt TO disponit_prisbok_eier;
-- `pris` og `klausul` HAR VERKEN DELETE: en versjon erstattes, den
-- slettes aldri. UPDATE er med fordi det er slik `gyldig_til` settes når
-- en ny versjon avløser den forrige — vakten begrenser den til nettopp
-- det ene feltet.
GRANT SELECT, INSERT, UPDATE ON pris TO disponit_prisbok_eier;
GRANT SELECT, INSERT, UPDATE ON klausul TO disponit_prisbok_eier;
GRANT SELECT, INSERT, UPDATE ON prisbokfunn TO disponit_prisbok_eier;
GRANT INSERT ON revisjonslogg TO disponit_prisbok_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_prisbok_eier;
RESET ROLE;


-- ------------------------------------------------------------
-- 3. Dørene. SECURITY DEFINER, eid av `disponit_prisbok_eier`, SP-1.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_prisbok_eier;

CREATE FUNCTION m26_evidens(p_tenant TEXT, p_subjekt_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm26_prisbok', 'handling', p_handling,
        'subjekt_id', p_subjekt_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm26_prisbok',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:prisbok', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m26_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

CREATE FUNCTION m26_sett_terskler(
    p_tenant TEXT, p_rabattgrense_promille INT, p_utlop_varsel_dogn INT,
    p_uten_pris_dogn INT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_sett_terskler');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.prisbokterskel
        (tenant, rabattgrense_promille, utlop_varsel_dogn,
         uten_pris_dogn, versjon, oppdatert, oppdatert_av)
    VALUES (p_tenant, p_rabattgrense_promille, p_utlop_varsel_dogn,
            p_uten_pris_dogn, 1, now(), p_aktor)
    ON CONFLICT (tenant) DO UPDATE
        SET rabattgrense_promille = EXCLUDED.rabattgrense_promille,
            utlop_varsel_dogn = EXCLUDED.utlop_varsel_dogn,
            uten_pris_dogn = EXCLUDED.uten_pris_dogn,
            versjon = prisbokterskel.versjon + 1,
            oppdatert = now(), oppdatert_av = p_aktor
    RETURNING versjon INTO v_versjon;
    PERFORM public.m26_evidens(
        p_tenant, '00000000-0000-0000-0000-000000000000'::uuid,
        'terskler.satt', p_aktor,
        jsonb_build_object('versjon', v_versjon));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION m26_sett_terskler(TEXT, INT, INT, INT, TEXT)
    FROM PUBLIC;

CREATE FUNCTION m26_registrer_produkt(
    p_tenant TEXT, p_produkt_id UUID, p_kode TEXT, p_navn TEXT,
    p_enhet TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm26_registrer_produkt');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.produkt
        (tenant, produkt_id, kode, navn, enhet, opprettet_av)
    VALUES (p_tenant, p_produkt_id, btrim(p_kode), btrim(p_navn),
            btrim(p_enhet), p_aktor)
        ON CONFLICT (tenant, produkt_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;                               -- SP-2: stille ja
    END IF;
    PERFORM public.m26_evidens(p_tenant, p_produkt_id,
                               'produkt.registrert', p_aktor,
                               jsonb_build_object());
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m26_registrer_produkt(TEXT, UUID, TEXT, TEXT,
    TEXT, TEXT) FROM PUBLIC;

-- PRISDØREN. DOM 2, OG MODULENS SKARPESTE.
--
-- EN PRIS ENDRES ALDRI — DEN ERSTATTES. Døren lukker den forrige
-- versjonen i SAMME transaksjon som den nye skrives, så det aldri finnes
-- et vindu der to priser gjelder eller ingen gjør det.
--
-- DØREN BEREGNER INGENTING. `p_listepris_ore` er tallet et menneske
-- skrev; funksjonen ganger ikke, indekserer ikke og runder ikke.
CREATE FUNCTION m26_sett_pris(
    p_tenant TEXT, p_produkt_id UUID, p_listepris_ore BIGINT,
    p_valuta TEXT, p_gyldig_fra DATE, p_begrunnelse TEXT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_forrige INT; v_forrige_fra DATE; v_ny INT; v_finnes BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_sett_pris');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_begrunnelse IS NULL OR p_begrunnelse !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm26_sett_pris: en prisendring uten begrunnelse'
            ' er en beslutning ingen kan etterprøve — og prisen er det'
            ' virksomheten tjener på'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT true INTO v_finnes FROM public.produkt p
     WHERE p.tenant = p_tenant AND p.produkt_id = p_produkt_id
       FOR UPDATE;
    IF NOT coalesce(v_finnes, false) THEN
        RAISE EXCEPTION 'm26_sett_pris: produktet finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    SELECT p.versjon, p.gyldig_fra INTO v_forrige, v_forrige_fra
      FROM public.pris p
     WHERE p.tenant = p_tenant AND p.produkt_id = p_produkt_id
       AND p.gyldig_til IS NULL
       FOR UPDATE;
    IF v_forrige IS NOT NULL THEN
        -- DEN NYE MÅ BEGYNNE ETTER DEN FORRIGE. En pris som skulle gjelde
        -- FØR den gjeldende ville krevd at boka ble skrevet om bakover,
        -- og det er nettopp det som ikke skal kunne skje.
        IF p_gyldig_fra <= v_forrige_fra THEN
            RAISE EXCEPTION 'm26_sett_pris: den nye prisen gjelder fra'
                ' %, men den gjeldende begynte %. En pris skrives ikke'
                ' bakover — «hva sto her da» er hele spørsmålet boka'
                ' finnes for å svare på', p_gyldig_fra, v_forrige_fra
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        UPDATE public.pris
           SET gyldig_til = p_gyldig_fra - 1
         WHERE tenant = p_tenant AND produkt_id = p_produkt_id
           AND versjon = v_forrige;
    END IF;
    v_ny := coalesce(v_forrige, 0) + 1;
    INSERT INTO public.pris
        (tenant, produkt_id, versjon, listepris_ore, valuta, gyldig_fra,
         begrunnelse, opprettet_av)
    VALUES (p_tenant, p_produkt_id, v_ny, p_listepris_ore,
            upper(coalesce(p_valuta, 'NOK')), p_gyldig_fra,
            btrim(p_begrunnelse), p_aktor);
    PERFORM public.m26_evidens(
        p_tenant, p_produkt_id, 'pris.satt', p_aktor,
        jsonb_build_object('versjon', v_ny));
    RETURN v_ny;
END $$;
REVOKE ALL ON FUNCTION m26_sett_pris(TEXT, UUID, BIGINT, TEXT, DATE,
    TEXT, TEXT) FROM PUBLIC;

-- KLAUSULDØREN. DOM 4: HASHEN REGNES HER, av teksten selv.
CREATE FUNCTION m26_sett_klausul(
    p_tenant TEXT, p_kode TEXT, p_tittel TEXT, p_tekst TEXT,
    p_standard BOOLEAN, p_gyldig_fra DATE, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_forrige INT; v_forrige_fra DATE; v_ny INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_sett_klausul');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT k.versjon, k.gyldig_fra INTO v_forrige, v_forrige_fra
      FROM public.klausul k
     WHERE k.tenant = p_tenant AND k.kode = btrim(p_kode)
       AND k.gyldig_til IS NULL
       FOR UPDATE;
    IF v_forrige IS NOT NULL THEN
        IF p_gyldig_fra <= v_forrige_fra THEN
            RAISE EXCEPTION 'm26_sett_klausul: den nye versjonen gjelder'
                ' fra %, men den gjeldende begynte %. En klausul skrives'
                ' ikke bakover', p_gyldig_fra, v_forrige_fra
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        UPDATE public.klausul
           SET gyldig_til = p_gyldig_fra - 1
         WHERE tenant = p_tenant AND kode = btrim(p_kode)
           AND versjon = v_forrige;
    END IF;
    v_ny := coalesce(v_forrige, 0) + 1;
    INSERT INTO public.klausul
        (tenant, kode, versjon, tittel, tekst, tekst_hash, standard,
         gyldig_fra, opprettet_av)
    VALUES (p_tenant, btrim(p_kode), v_ny, btrim(p_tittel), p_tekst,
            encode(sha256(convert_to(p_tekst, 'UTF8')), 'hex'),
            coalesce(p_standard, false), p_gyldig_fra, p_aktor);
    PERFORM public.m26_evidens(
        p_tenant, '00000000-0000-0000-0000-000000000000'::uuid,
        'klausul.satt', p_aktor,
        jsonb_build_object('kode', btrim(p_kode), 'versjon', v_ny));
    RETURN v_ny;
END $$;
REVOKE ALL ON FUNCTION m26_sett_klausul(TEXT, TEXT, TEXT, TEXT, BOOLEAN,
    DATE, TEXT) FROM PUBLIC;

CREATE FUNCTION m26_sett_produktaktiv(
    p_tenant TEXT, p_produkt_id UUID, p_aktiv BOOLEAN, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm26_sett_produktaktiv');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    UPDATE public.produkt SET aktiv = p_aktiv
     WHERE tenant = p_tenant AND produkt_id = p_produkt_id
       AND aktiv IS DISTINCT FROM p_aktiv;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;                               -- stille ja
    END IF;
    -- ET DEAKTIVERT PRODUKT SKAL IKKE HA ÅPNE FUNN: de er varsler om noe
    -- ingen lenger selger.
    IF NOT p_aktiv THEN
        UPDATE public.prisbokfunn
           SET apen = false, lukket_ts = now()
         WHERE tenant = p_tenant AND produkt_id = p_produkt_id AND apen;
    END IF;
    PERFORM public.m26_evidens(
        p_tenant, p_produkt_id, 'produkt.aktiv', p_aktor,
        jsonb_build_object('aktiv', p_aktiv));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m26_sett_produktaktiv(TEXT, UUID, BOOLEAN, TEXT)
    FROM PUBLIC;

RESET ROLE;


-- ------------------------------------------------------------
-- 3b. Lesedørene.
--
--     `m26_pris_paa_dato` er den som betyr noe: den svarer på HVA SOM
--     STO I BOKA DEN DAGEN. `priser_fra_prisbok` er en attestasjon om
--     nettopp det, og uten dette oppslaget er den ikke etterprøvbar.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_prisbok_eier;

CREATE FUNCTION m26_pris_paa_dato(p_tenant TEXT, p_produkt_id UUID,
                                  p_dato DATE)
RETURNS TABLE(versjon INT, listepris_ore BIGINT, valuta TEXT,
              gyldig_fra DATE, gyldig_til DATE)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_pris_paa_dato');
    RETURN QUERY
    SELECT p.versjon, p.listepris_ore, p.valuta, p.gyldig_fra,
           p.gyldig_til
      FROM public.pris p
     WHERE p.tenant = p_tenant AND p.produkt_id = p_produkt_id
       AND p.gyldig_fra <= p_dato
       AND (p.gyldig_til IS NULL OR p.gyldig_til >= p_dato);
END $$;
REVOKE ALL ON FUNCTION m26_pris_paa_dato(TEXT, UUID, DATE) FROM PUBLIC;

-- RABATTKONTROLLEN. DEN SETTER INGEN PRIS — den svarer på om et tall
-- noen alt har bestemt ligger innenfor tenantens grense.
--
-- HELTALLSARITMETIKK OG INGEN DIVISJON: `tilbudt * 1000 >= listepris *
-- (1000 - promille)`. En grense som «nesten» er passert er ingen grense.
CREATE FUNCTION m26_innenfor_rabatt(p_tenant TEXT, p_produkt_id UUID,
                                    p_dato DATE, p_tilbudt_ore BIGINT)
RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_liste BIGINT; v_promille INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_innenfor_rabatt');
    IF p_tilbudt_ore IS NULL OR p_tilbudt_ore < 0 THEN
        RAISE EXCEPTION 'm26_innenfor_rabatt: et tilbudt beløp må finnes'
            ' og kan ikke være negativt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT r.listepris_ore INTO v_liste
      FROM public.m26_pris_paa_dato(p_tenant, p_produkt_id, p_dato) r;
    IF v_liste IS NULL THEN
        -- INGEN PRIS GJALDT DEN DAGEN. `NULL` er det ærlige svaret:
        -- «innenfor rabatt» om noe som ikke hadde en pris ville vært en
        -- dom uten grunnlag.
        RETURN NULL;
    END IF;
    SELECT t.rabattgrense_promille INTO v_promille
      FROM public.prisbokterskel t WHERE t.tenant = p_tenant;
    IF v_promille IS NULL THEN
        RETURN NULL;                    -- ingen grense satt: ingen dom
    END IF;
    RETURN p_tilbudt_ore * 1000 >= v_liste * (1000 - v_promille);
END $$;
REVOKE ALL ON FUNCTION m26_innenfor_rabatt(TEXT, UUID, DATE, BIGINT)
    FROM PUBLIC;

CREATE FUNCTION m26_prisbokstatus(p_tenant TEXT)
RETURNS TABLE(produkter INT, aktive INT, med_gyldig_pris INT,
              klausuler INT, standardklausuler INT, apne_funn INT,
              har_terskel BOOLEAN, terskelversjon INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_prisbokstatus');
    RETURN QUERY
    SELECT (SELECT count(*)::int FROM public.produkt p
             WHERE p.tenant = p_tenant),
           (SELECT count(*)::int FROM public.produkt p
             WHERE p.tenant = p_tenant AND p.aktiv),
           (SELECT count(*)::int FROM public.produkt p
             WHERE p.tenant = p_tenant AND p.aktiv
               AND EXISTS (SELECT 1 FROM public.pris x
                            WHERE x.tenant = p.tenant
                              AND x.produkt_id = p.produkt_id
                              AND x.gyldig_fra <= current_date
                              AND (x.gyldig_til IS NULL
                                   OR x.gyldig_til >= current_date))),
           (SELECT count(*)::int FROM public.klausul k
             WHERE k.tenant = p_tenant AND k.gyldig_til IS NULL),
           (SELECT count(*)::int FROM public.klausul k
             WHERE k.tenant = p_tenant AND k.gyldig_til IS NULL
               AND k.standard),
           (SELECT count(*)::int FROM public.prisbokfunn f
             WHERE f.tenant = p_tenant AND f.apen),
           EXISTS (SELECT 1 FROM public.prisbokterskel t
                    WHERE t.tenant = p_tenant),
           (SELECT t.versjon FROM public.prisbokterskel t
             WHERE t.tenant = p_tenant);
END $$;
REVOKE ALL ON FUNCTION m26_prisbokstatus(TEXT) FROM PUBLIC;

CREATE FUNCTION m26_produktene(p_tenant TEXT, p_grense INT)
RETURNS TABLE(produkt_id UUID, kode TEXT, navn TEXT, enhet TEXT,
              aktiv BOOLEAN, versjon INT, listepris_ore BIGINT,
              valuta TEXT, gyldig_fra DATE, gyldig_til DATE,
              dogn_til_utlop INT, versjoner INT, apne_funn TEXT[])
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_produktene');
    RETURN QUERY
    SELECT p.produkt_id, p.kode, p.navn, p.enhet, p.aktiv,
           g.versjon, g.listepris_ore, g.valuta, g.gyldig_fra,
           g.gyldig_til,
           CASE WHEN g.gyldig_til IS NULL THEN NULL
                ELSE (g.gyldig_til - current_date)::int END,
           v.antall, coalesce(f.typer, ARRAY[]::TEXT[])
      FROM public.produkt p
      LEFT JOIN LATERAL public.m26_pris_paa_dato(
            p.tenant, p.produkt_id, current_date) g ON true
      CROSS JOIN LATERAL (
            SELECT count(*)::int AS antall FROM public.pris x
             WHERE x.tenant = p.tenant
               AND x.produkt_id = p.produkt_id) v
      LEFT JOIN LATERAL (
            SELECT array_agg(z.funntype ORDER BY z.funntype) AS typer
              FROM public.prisbokfunn z
             WHERE z.tenant = p.tenant AND z.produkt_id = p.produkt_id
               AND z.apen) f ON true
     WHERE p.tenant = p_tenant
     -- AKTIVE FØRST, deretter alfabetisk på koden.
     ORDER BY p.aktiv DESC, p.kode
     LIMIT greatest(least(coalesce(p_grense, 200), 1000), 1);
END $$;
REVOKE ALL ON FUNCTION m26_produktene(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m26_prishistorikken(p_tenant TEXT, p_produkt_id UUID)
RETURNS TABLE(versjon INT, listepris_ore BIGINT, valuta TEXT,
              gyldig_fra DATE, gyldig_til DATE, begrunnelse TEXT,
              opprettet TIMESTAMPTZ, opprettet_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_prishistorikken');
    RETURN QUERY
    SELECT p.versjon, p.listepris_ore, p.valuta, p.gyldig_fra,
           p.gyldig_til, p.begrunnelse, p.opprettet, p.opprettet_av
      FROM public.pris p
     WHERE p.tenant = p_tenant AND p.produkt_id = p_produkt_id
     ORDER BY p.versjon DESC
     LIMIT 500;
END $$;
REVOKE ALL ON FUNCTION m26_prishistorikken(TEXT, UUID) FROM PUBLIC;

CREATE FUNCTION m26_klausulene(p_tenant TEXT)
RETURNS TABLE(kode TEXT, versjon INT, tittel TEXT, tekst TEXT,
              tekst_hash TEXT, standard BOOLEAN, gyldig_fra DATE,
              gyldig_til DATE)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_klausulene');
    RETURN QUERY
    SELECT k.kode, k.versjon, k.tittel, k.tekst, k.tekst_hash,
           k.standard, k.gyldig_fra, k.gyldig_til
      FROM public.klausul k
     WHERE k.tenant = p_tenant
     ORDER BY k.kode, k.versjon DESC
     LIMIT 500;
END $$;
REVOKE ALL ON FUNCTION m26_klausulene(TEXT) FROM PUBLIC;

CREATE FUNCTION m26_tersklene(p_tenant TEXT)
RETURNS TABLE(rabattgrense_promille INT, utlop_varsel_dogn INT,
              uten_pris_dogn INT, versjon INT, oppdatert TIMESTAMPTZ,
              oppdatert_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_tersklene');
    RETURN QUERY
    SELECT t.rabattgrense_promille, t.utlop_varsel_dogn,
           t.uten_pris_dogn, t.versjon, t.oppdatert, t.oppdatert_av
      FROM public.prisbokterskel t WHERE t.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m26_tersklene(TEXT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Sveipens kandidater. TERSKLENE LESES FRA TABELLEN.
-- ------------------------------------------------------------
CREATE FUNCTION m26_funnkandidater(p_tenant TEXT, p_dag DATE)
RETURNS TABLE(produkt_id UUID, funntype TEXT, over_grense INT,
              prisversjon INT, terskelversjon INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_t RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_funnkandidater');
    SELECT * INTO v_t FROM public.prisbokterskel t
     WHERE t.tenant = p_tenant;
    IF NOT FOUND THEN
        RETURN QUERY
        SELECT p.produkt_id, 'ingen_terskel'::text, NULL::int, NULL::int,
               NULL::int
          FROM public.produkt p
         WHERE p.tenant = p_tenant AND p.aktiv;
        RETURN;
    END IF;

    RETURN QUERY
    -- 1. UTEN GYLDIG PRIS: et aktivt produkt som ikke har hatt en pris
    --    på tenantens grense. Et produkt uten pris er et produkt ingen
    --    kan gi tilbud på — og `priser_fra_prisbok` kan da aldri bli
    --    sant for det.
    SELECT p.produkt_id, 'uten_gyldig_pris'::text,
           (p_dag - greatest(p.opprettet::date,
                             coalesce(s.siste, p.opprettet::date))
            - v_t.uten_pris_dogn)::int,
           NULL::int, v_t.versjon
      FROM public.produkt p
      -- `FILTER (WHERE x.gyldig_fra <= p_dag)`: bare priser som HAR
      -- BEGYNT teller når vi måler hvor lenge produktet har stått uten
      -- pris. Uten filteret ville en pris som begynner neste år gitt
      -- `coalesce(NULL, p_dag) = p_dag` — altså «sist hadde pris i dag»
      -- — og funnet ville vært stille så lenge noen hadde ført en
      -- framtidig pris. Da måler porten det motsatte av det den lover.
      LEFT JOIN LATERAL (
            SELECT max(coalesce(x.gyldig_til, p_dag))
                       FILTER (WHERE x.gyldig_fra <= p_dag) AS siste
              FROM public.pris x
             WHERE x.tenant = p.tenant
               AND x.produkt_id = p.produkt_id) s ON true
     WHERE p.tenant = p_tenant AND p.aktiv
       AND NOT EXISTS (SELECT 1 FROM public.pris x
                        WHERE x.tenant = p.tenant
                          AND x.produkt_id = p.produkt_id
                          AND x.gyldig_fra <= p_dag
                          AND (x.gyldig_til IS NULL
                               OR x.gyldig_til >= p_dag))
       AND p_dag - greatest(p.opprettet::date,
                            coalesce(s.siste, p.opprettet::date))
           > v_t.uten_pris_dogn
    UNION ALL
    -- 2. PRIS UTLØPER SNART: den gjeldende prisen har en sluttdato
    --    innenfor tenantens varselvindu. En pris som gikk ut uten at
    --    noen merket det, er et produkt som stille slutter å kunne
    --    tilbys.
    SELECT p.produkt_id, 'pris_utloper_snart'::text,
           (g.gyldig_til - p_dag)::int, g.versjon, v_t.versjon
      FROM public.produkt p
      JOIN LATERAL public.m26_pris_paa_dato(p.tenant, p.produkt_id,
                                            p_dag) g ON true
     WHERE p.tenant = p_tenant AND p.aktiv
       AND g.gyldig_til IS NOT NULL
       AND g.gyldig_til <= p_dag + v_t.utlop_varsel_dogn;
END $$;
REVOKE ALL ON FUNCTION m26_funnkandidater(TEXT, DATE) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 4b. Sveipen. KRYSS-TENANT, egen LOGIN-rolle, egen timer.
--
--     SVEIPEN SETTER INGEN PRIS OG FORLENGER INGEN GYLDIGHET. Den
--     kunne, teknisk — den vet hvilke priser som er i ferd med å gå ut.
--     Men en pris er det virksomheten tjener, og en jobb som forlenget
--     den om natten ville tatt den beslutningen på ingens vegne.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_prisbok_eier;

CREATE FUNCTION m26_sveip_prisbok(p_grense INT DEFAULT 500)
RETURNS TABLE(tenanter INT, nye INT, oppdaterte INT, lukkede INT,
              avkortet INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_n INT; v_grense INT;
        v_dag DATE; v_naa TIMESTAMPTZ;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm26_sveip_prisbok: sveipen er KRYSS-TENANT og'
            ' kjøres uten tenantkontekst — en kaller som har satt en'
            ' kontekst ber om noe annet enn det denne funksjonen gjør'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_grense := greatest(least(coalesce(p_grense, 500), 5000), 1);
    v_dag := current_date;
    v_naa := now();
    tenanter := 0; nye := 0; oppdaterte := 0; lukkede := 0; avkortet := 0;
    SELECT array_agg(DISTINCT p.tenant ORDER BY p.tenant) INTO v_tenanter
      FROM public.produkt p;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        tenanter := tenanter + 1;

        UPDATE public.prisbokfunn pf
           SET sist_sett_sveip = v_naa, apen = true, lukket_ts = NULL,
               over_grense = kand.over_grense,
               prisversjon = kand.prisversjon,
               terskelversjon = kand.terskelversjon
          FROM public.m26_funnkandidater(v_t, v_dag) kand
         WHERE pf.tenant = v_t AND pf.produkt_id = kand.produkt_id
           AND pf.funntype = kand.funntype;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        oppdaterte := oppdaterte + v_n;

        INSERT INTO public.prisbokfunn
            (tenant, produkt_id, funntype, over_grense, prisversjon,
             terskelversjon, forst_sett, sist_sett_sveip, apen)
        SELECT v_t, kand.produkt_id, kand.funntype, kand.over_grense,
               kand.prisversjon, kand.terskelversjon, v_naa, v_naa, true
          FROM public.m26_funnkandidater(v_t, v_dag) kand
         WHERE NOT EXISTS (
                SELECT 1 FROM public.prisbokfunn pf
                 WHERE pf.tenant = v_t
                   AND pf.produkt_id = kand.produkt_id
                   AND pf.funntype = kand.funntype)
         ORDER BY coalesce(kand.over_grense, 0) DESC,
                  kand.produkt_id, kand.funntype
         LIMIT v_grense;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        nye := nye + v_n;
        IF v_n = v_grense THEN
            avkortet := avkortet + 1;
        END IF;

        UPDATE public.prisbokfunn pf
           SET apen = false, lukket_ts = v_naa
         WHERE pf.tenant = v_t AND pf.apen
           AND NOT EXISTS (
                SELECT 1 FROM public.m26_funnkandidater(v_t, v_dag) kand
                 WHERE kand.produkt_id = pf.produkt_id
                   AND kand.funntype = pf.funntype);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        lukkede := lukkede + v_n;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m26_sveip_prisbok(INT) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 5. Rettighetene. SP-7: kjøretidsrollen får INGEN tabellrettigheter.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_prisbok_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_prisbokstatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_produktene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m26_prishistorikken(TEXT, UUID) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_klausulene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_tersklene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m26_pris_paa_dato(TEXT, UUID, DATE) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m26_innenfor_rabatt(TEXT, UUID, DATE, BIGINT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m26_sett_terskler(TEXT, INT, INT, INT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m26_registrer_produkt(TEXT, UUID, TEXT, TEXT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m26_sett_pris(TEXT, UUID, BIGINT, TEXT, DATE, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m26_sett_klausul(TEXT, TEXT, TEXT, TEXT, BOOLEAN, DATE,'
            ' TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m26_sett_produktaktiv(TEXT, UUID, BOOLEAN, TEXT)'
            ' TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_prisboksveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_sveip_prisbok(INT)'
            ' TO disponit_prisboksveip';
    END IF;
END $$;
RESET ROLE;
