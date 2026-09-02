-- 107: M-25 prosjekt- og kontraktagent v1 — PROSJEKTREGISTERET.
-- Fem tenant-skopede tabeller, fjorten dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- V1-AVGRENSNINGEN, ORDRETT FRA POLICYENE VI SENDER UT:
-- `bransjemal-handverk-bygg.yaml` og `bransjemal-netthandel.yaml`
-- navngir denne modulen som verifikatoren `v_prosjekt`, betrodd for
-- `milepael_dokumentert`, `kontraktsfestet_betalingsplan`,
-- `prosjektbudsjett_ok`, `arbeid_dokumentert` og
-- `befaring_dokumentert` — og bruker dem til å slippe
-- `ordre.bekreft_og_fakturer` gjennom som `modus: auto`.
--
-- v1 FAKTURERER INGENTING OG ATTESTERER INGENTING.
--
-- DOMMEN: en automatisk faktura på en milepæl ingen har dokumentert, er
-- penger krevd for arbeid som kanskje ikke er gjort. Kravet har forlatt
-- systemet i det øyeblikket det ble sendt — og en kunde som får en
-- faktura for noe som ikke skjedde, husker det lenger enn vi husker
-- feilen.
--
-- Det finnes derfor ingen fakturadør her, ingen kobling til M-23s
-- fordringsregister, og ingen status som heter `fakturert`.
--
-- DOMMENE v1 HVILER PÅ, håndhevet i DATAMODELLEN:
--
--   1. BELØP ER HELTALL I MINSTE ENHET (øre), `BIGINT`, uten unntak.
--
--   2. EN MILEPÆL KAN IKKE MERKES NÅDD UTEN EN HENVISNING TIL HVA SOM
--      DOKUMENTERER DEN. Dette er modulens skarpeste regel, og den er
--      hel-eller-ingenting: tidspunktet, aktøren og dokumentasjonen
--      settes sammen eller ikke i det hele tatt. `milepael_dokumentert`
--      i policyen kan aldri bli sant om noe som ikke har en
--      dokumentasjon å peke på.
--
--      (`NULL ~ '...'` er NULL, og en CHECK som evaluerer til NULL
--      PASSERER. Hullet ble funnet i 101 og lukket i 102; hvert ledd her
--      er derfor eksplisitt `IS NOT NULL`.)
--
--   3. BETALINGSPLANEN ER KONTRAKTENS, ikke modulens. Milepælene og
--      beløpene deres er skrevet av et menneske gjennom en dør, og de er
--      FROSSET etter at milepælen er nådd: et beløp som kunne endres i
--      ettertid ville omskrevet grunnlaget for et krav som alt var
--      stilt.
--
--   4. FORBRUK OG BETALINGSPLAN ER TO FORSKJELLIGE STØRRELSER, og de
--      blandes aldri. `budsjett_ore` er hva prosjektet får KOSTE;
--      milepælenes `belop_ore` er hva kontrakten lar oss KREVE. Et
--      register som la dem i samme kolonne ville gjort «går prosjektet
--      i pluss» til et spørsmål ingen kunne svare på.
--
-- GRENSEN MOT M-21: M-21 eier FRISTENE våre mot omverdenen. En milepæl
-- er ikke en plikt mot omverdenen; den er et punkt i en kontrakt. To
-- registre som begge påstår å eie «hva som må skje innen når», kan
-- aldri holdes i takt.
--
-- GRENSEN MOT M-23: M-23 eier fordringen når den først er stilt. M-25
-- eier GRUNNLAGET for å stille den, og v1 stiller den ikke.
--
-- SVEIPEN HAR SIN EGEN ROLLE OG SIN EGEN TIMER (095/100–106):
-- `disponit_prosjektsveip` har nøyaktig ÉN rettighet — EXECUTE på
-- `m25_sveip_prosjekter` — og INGEN tabellrettigheter.
--
-- FORMENE ER HUSETS: tabellene eies av migrator, dørene av NOLOGIN-rollen
-- `disponit_prosjekt_eier`, tenant TEXT + RLS ENABLE+FORCE +
-- `tenant_isolasjon`, SP-1 i hver tenantbundet definer, ingen BYPASSRLS.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_prosjekt_eier') THEN
        RAISE EXCEPTION 'rollen disponit_prosjekt_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_prosjekt_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `prosjektterskel` — ÉN per tenant. Tenantens grenser.
CREATE TABLE prosjektterskel (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- Hvor mange PROMILLE over budsjettet forbruket kan ligge før det
    -- er et funn. Promille og ikke prosent: en grense på 2,5 % finnes,
    -- og 25 er eksakt der 2.5 ikke er.
    budsjettvarsel_promille INT NOT NULL DEFAULT 0
        CHECK (budsjettvarsel_promille BETWEEN 0 AND 10000),
    -- Hvor mange døgn en milepæl kan stå forbi sin planlagte dato uten
    -- å være nådd, før det er et funn.
    milepael_frist_dogn INT NOT NULL DEFAULT 7
        CHECK (milepael_frist_dogn BETWEEN 0 AND 3650),
    -- Hvor lenge et aktivt prosjekt kan stå uten et eneste registrert
    -- arbeid. Et prosjekt ingen har ført timer på er enten ferdig eller
    -- glemt, og de to er forskjellige.
    stillhet_dogn INT NOT NULL DEFAULT 30
        CHECK (stillhet_dogn BETWEEN 1 AND 3650),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT prosjektterskel_pk PRIMARY KEY (tenant)
);

-- `prosjekt` — kontrakten og budsjettet.
CREATE TABLE prosjekt (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    prosjekt_id UUID NOT NULL,
    kunde_ref TEXT NOT NULL CHECK (kunde_ref ~ '[^[:space:]]'),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    -- Kontraktens egen referanse, tenantens format.
    kontrakt_ref TEXT CHECK (kontrakt_ref IS NULL
                             OR kontrakt_ref ~ '[^[:space:]]'),
    -- DOM 4: hva prosjektet får KOSTE. Ikke hva vi kan kreve.
    budsjett_ore BIGINT NOT NULL CHECK (budsjett_ore >= 0),
    start DATE NOT NULL,
    planlagt_slutt DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'aktiv'
        CONSTRAINT prosjekt_status_lukket
        CHECK (status IN ('aktiv', 'avsluttet')),
    avsluttet_ts TIMESTAMPTZ,
    avsluttet_av TEXT,
    avsluttet_begrunnelse TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT prosjekt_pk PRIMARY KEY (tenant, prosjekt_id),
    CONSTRAINT prosjekt_navn_unik UNIQUE (tenant, kunde_ref, navn),
    CONSTRAINT prosjekt_slutt_etter_start
        CHECK (planlagt_slutt >= start),
    CONSTRAINT prosjekt_avslutning_helhet CHECK (
        (status = 'aktiv' AND avsluttet_ts IS NULL
         AND avsluttet_av IS NULL AND avsluttet_begrunnelse IS NULL)
     OR (status = 'avsluttet' AND avsluttet_ts IS NOT NULL
         AND avsluttet_av IS NOT NULL
         AND avsluttet_av ~ '[^[:space:]]'
         AND avsluttet_begrunnelse IS NOT NULL
         AND avsluttet_begrunnelse ~ '[^[:space:]]'))
);
CREATE INDEX prosjekt_aktive
    ON prosjekt (tenant, planlagt_slutt) WHERE status = 'aktiv';

-- `milepael` — KONTRAKTENS BETALINGSPLAN.
--
-- DOM 2 I TABELLFORM: `naadd_ts`, `naadd_av` og `dokumentasjon_ref`
-- settes SAMMEN eller ikke i det hele tatt. En milepæl merket nådd uten
-- en henvisning til hva som dokumenterer den, er en påstand — og den
-- påstanden er grunnlaget for et krav mot en kunde.
CREATE TABLE milepael (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    prosjekt_id UUID NOT NULL,
    milepael_nr INT NOT NULL CHECK (milepael_nr >= 1),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    planlagt_dato DATE NOT NULL,
    -- DOM 4: hva kontrakten lar oss KREVE når denne er nådd. Ikke en
    -- kostnad, og aldri i samme kolonne som budsjettet.
    belop_ore BIGINT NOT NULL CHECK (belop_ore >= 0),
    naadd_ts TIMESTAMPTZ,
    naadd_av TEXT,
    -- HVA SOM DOKUMENTERER DEN. Tenantens referanse — et bildenummer,
    -- en rapport-id, en signert overtakelse. Ingen validering av format:
    -- en modul som krevde et bestemt format ville låst registeret til
    -- ett verktøy.
    dokumentasjon_ref TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT milepael_pk PRIMARY KEY (tenant, prosjekt_id, milepael_nr),
    CONSTRAINT milepael_prosjekt_fk FOREIGN KEY (tenant, prosjekt_id)
        REFERENCES prosjekt (tenant, prosjekt_id),
    -- HEL ELLER INGENTING, med eksplisitt `IS NOT NULL` i begge armer.
    CONSTRAINT milepael_naadd_helhet CHECK (
        (naadd_ts IS NULL AND naadd_av IS NULL
         AND dokumentasjon_ref IS NULL)
     OR (naadd_ts IS NOT NULL
         AND naadd_av IS NOT NULL AND naadd_av ~ '[^[:space:]]'
         AND dokumentasjon_ref IS NOT NULL
         AND dokumentasjon_ref ~ '[^[:space:]]'))
);
CREATE INDEX milepael_ventende
    ON milepael (tenant, planlagt_dato) WHERE naadd_ts IS NULL;

-- `prosjektarbeid` — det som FAKTISK er gjort. APPEND-ONLY: et
-- timeregnskap som kunne skrives om ville gjort forbruket til en
-- påstand, og forbruket er den ene siden av budsjettspørsmålet.
CREATE TABLE prosjektarbeid (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    arbeid_id UUID NOT NULL,
    prosjekt_id UUID NOT NULL,
    utfort DATE NOT NULL,
    -- Timer i HELE MINUTTER, ikke desimaltimer. «1,5 time» er 90
    -- minutter og ikke 1.4999999999999998.
    minutter INT NOT NULL CHECK (minutter > 0 AND minutter <= 1440),
    kostnad_ore BIGINT NOT NULL CHECK (kostnad_ore >= 0),
    beskrivelse TEXT NOT NULL CHECK (beskrivelse ~ '[^[:space:]]'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT prosjektarbeid_pk PRIMARY KEY (tenant, arbeid_id),
    CONSTRAINT prosjektarbeid_prosjekt_fk FOREIGN KEY (tenant, prosjekt_id)
        REFERENCES prosjekt (tenant, prosjekt_id)
);
CREATE INDEX prosjektarbeid_pr_prosjekt
    ON prosjektarbeid (tenant, prosjekt_id, utfort DESC);

-- `prosjektfunn` — funnene. Nøklet på prosjektet og typen.
CREATE TABLE prosjektfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    prosjekt_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CONSTRAINT prosjektfunn_type_lukket CHECK (funntype IN (
            'milepael_over_frist', 'budsjett_overskredet',
            'ingen_arbeid_registrert', 'betalingsplan_mangler',
            'ingen_terskel')),
    -- Ett tall med én betydning per funntype: øre for budsjett, døgn
    -- for de øvrige.
    over_grense BIGINT,
    -- Hvilken milepæl som er over fristen. NULL for de øvrige typene.
    milepael_nr INT,
    terskelversjon INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT prosjektfunn_pk
        PRIMARY KEY (tenant, prosjekt_id, funntype),
    CONSTRAINT prosjektfunn_prosjekt_fk FOREIGN KEY (tenant, prosjekt_id)
        REFERENCES prosjekt (tenant, prosjekt_id),
    CONSTRAINT prosjektfunn_lukking_helhet CHECK (
        (apen AND lukket_ts IS NULL)
     OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE INDEX prosjektfunn_apne
    ON prosjektfunn (tenant, funntype) WHERE apen;


-- ------------------------------------------------------------
-- 2. Vaktene.
-- ------------------------------------------------------------

CREATE FUNCTION m25_terskel_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    -- TRUNCATE HAR SIN EGEN ARM (104s lærdom).
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'prosjektterskel: TRUNCATE avvist — grensene'
            ' endres ved å sette nye, ikke ved å fjerne dem under'
            ' føttene på sveipen'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'prosjektterskel: DELETE avvist — en tenant uten'
            ' grenser kan ikke måle noe, og det er en tilstand sveipen'
            ' skal SI FRA om' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.versjon <= OLD.versjon THEN
        RAISE EXCEPTION 'prosjektterskel: versjonen må øke ved endring'
            ' (% -> %) — et funn bærer versjonen det ble vurdert mot',
            OLD.versjon, NEW.versjon USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m25_terskel_vakt() FROM PUBLIC;
CREATE TRIGGER m25_terskel_vakt
    BEFORE UPDATE OR DELETE ON prosjektterskel
    FOR EACH ROW EXECUTE FUNCTION m25_terskel_vakt();
CREATE TRIGGER m25_terskel_ingen_truncate
    BEFORE TRUNCATE ON prosjektterskel
    EXECUTE FUNCTION m25_terskel_vakt();

CREATE FUNCTION m25_prosjekt_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'prosjekt: TRUNCATE avvist — et prosjekt'
            ' avsluttes med begrunnelse, det forsvinner aldri i en'
            ' tabelltømming' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'prosjekt: DELETE avvist — et slettet prosjekt'
            ' tar kontraktshistorikken med seg'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- KONTRAKTENS RAMMER ER FROSSET. Et budsjett som kunne endres i
    -- ettertid ville omskrevet hver overskridelse som alt var målt —
    -- historien ville rettet seg selv.
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.prosjekt_id IS DISTINCT FROM OLD.prosjekt_id
       OR NEW.kunde_ref IS DISTINCT FROM OLD.kunde_ref
       OR NEW.budsjett_ore IS DISTINCT FROM OLD.budsjett_ore
       OR NEW.start IS DISTINCT FROM OLD.start
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'prosjekt: kunden, budsjettet og starten er'
            ' frosset — en endringsordre er en ny avtale, ikke en'
            ' omskriving av den gamle'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status = 'avsluttet' AND NEW.status = 'aktiv' THEN
        RAISE EXCEPTION 'prosjekt: et avsluttet prosjekt gjenåpnes ikke'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'avsluttet' AND OLD.status = 'aktiv' THEN
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL OR NEW.avsluttet_av IS DISTINCT FROM v_aktor THEN
            RAISE EXCEPTION 'prosjekt: avsluttet_av (%) er ikke aktøren'
                ' som avslutter (%)',
                coalesce(NEW.avsluttet_av, '<null>'),
                coalesce(v_aktor, '<ingen>')
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m25_prosjekt_vakt() FROM PUBLIC;
CREATE TRIGGER m25_prosjekt_vakt
    BEFORE UPDATE OR DELETE ON prosjekt
    FOR EACH ROW EXECUTE FUNCTION m25_prosjekt_vakt();
CREATE TRIGGER m25_prosjekt_ingen_truncate
    BEFORE TRUNCATE ON prosjekt EXECUTE FUNCTION m25_prosjekt_vakt();

-- DOM 2 OG DOM 3, HÅNDHEVET I BASEN: en milepæl merkes nådd med
-- dokumentasjon, og det som er nådd er FROSSET.
CREATE FUNCTION m25_milepael_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'milepael: TRUNCATE avvist — en tømt'
            ' betalingsplan gjør hvert krav mot kunden til noe ingen kan'
            ' etterprøve' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        -- EN MILEPÆL SOM ER NÅDD SLETTES ALDRI. En som IKKE er nådd kan
        -- fjernes: betalingsplanen redigeres til den er avtalt, og en
        -- plan man ikke kunne rette ville tvunget fram et nytt prosjekt
        -- for hver skrivefeil.
        IF OLD.naadd_ts IS NOT NULL THEN
            RAISE EXCEPTION 'milepael: DELETE avvist — milepælen er nådd'
                ' og dokumentert, og den er grunnlaget for et krav'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        -- DET SOM ER NÅDD ER FROSSET, inkludert beløpet: et beløp som
        -- kunne endres i ettertid ville omskrevet grunnlaget for et krav
        -- som alt var stilt.
        IF OLD.naadd_ts IS NOT NULL THEN
            IF NEW.naadd_ts IS DISTINCT FROM OLD.naadd_ts
               OR NEW.naadd_av IS DISTINCT FROM OLD.naadd_av
               OR NEW.dokumentasjon_ref IS DISTINCT FROM
                  OLD.dokumentasjon_ref
               OR NEW.belop_ore IS DISTINCT FROM OLD.belop_ore
               OR NEW.navn IS DISTINCT FROM OLD.navn THEN
                RAISE EXCEPTION 'milepael: en nådd milepæl er frosset —'
                    ' den er grunnlaget for et krav mot kunden'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
        END IF;
        IF NEW.tenant IS DISTINCT FROM OLD.tenant
           OR NEW.prosjekt_id IS DISTINCT FROM OLD.prosjekt_id
           OR NEW.milepael_nr IS DISTINCT FROM OLD.milepael_nr THEN
            RAISE EXCEPTION 'milepael: identiteten er frosset'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    -- DOM 2: aktøren som merker milepælen nådd, MÅ være den som står
    -- der. En rad som sa at noen andre gjorde det er verre enn ingen
    -- rad, fordi noen handler på den.
    IF NEW.naadd_ts IS NOT NULL
       AND (TG_OP = 'INSERT' OR OLD.naadd_ts IS NULL) THEN
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL OR NEW.naadd_av IS DISTINCT FROM v_aktor THEN
            RAISE EXCEPTION 'milepael: naadd_av (%) er ikke aktøren som'
                ' merker den nådd (%)',
                coalesce(NEW.naadd_av, '<null>'),
                coalesce(v_aktor, '<ingen>')
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m25_milepael_vakt() FROM PUBLIC;
CREATE TRIGGER m25_milepael_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON milepael
    FOR EACH ROW EXECUTE FUNCTION m25_milepael_vakt();
CREATE TRIGGER m25_milepael_ingen_truncate
    BEFORE TRUNCATE ON milepael EXECUTE FUNCTION m25_milepael_vakt();

CREATE FUNCTION m25_arbeid_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'prosjektarbeid: TRUNCATE avvist — et tømt'
            ' timeregnskap gjør forbruket til en påstand'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'prosjektarbeid: DELETE avvist — ført arbeid'
            ' forsvinner ikke fordi budsjettet ble stramt'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RAISE EXCEPTION 'prosjektarbeid: UPDATE avvist — raden er'
        ' append-only. Feilført arbeid rettes med en ny føring, ikke ved'
        ' å skrive om den gamle'
        USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE ALL ON FUNCTION m25_arbeid_vakt() FROM PUBLIC;
CREATE TRIGGER m25_arbeid_vakt
    BEFORE UPDATE OR DELETE ON prosjektarbeid
    FOR EACH ROW EXECUTE FUNCTION m25_arbeid_vakt();
CREATE TRIGGER m25_arbeid_ingen_truncate
    BEFORE TRUNCATE ON prosjektarbeid
    EXECUTE FUNCTION m25_arbeid_vakt();

CREATE FUNCTION m25_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    -- Her er armen IKKE kosmetisk: uten den faller TG_OP='TRUNCATE'
    -- glatt gjennom til RETURN NEW, og tømmingen skjer. (CodeRabbit
    -- på 104.)
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'prosjektfunn: TRUNCATE avvist — et funn lukkes,'
            ' det tømmes ikke bort. En tom funntabell ser ut som en ren'
            ' natt' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'prosjektfunn: DELETE avvist — et funn lukkes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.tenant IS DISTINCT FROM OLD.tenant
           OR NEW.prosjekt_id IS DISTINCT FROM OLD.prosjekt_id
           OR NEW.funntype IS DISTINCT FROM OLD.funntype
           OR NEW.forst_sett IS DISTINCT FROM OLD.forst_sett THEN
            RAISE EXCEPTION 'prosjektfunn: identiteten og førstegangen'
                ' er frosset' USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m25_funn_vakt() FROM PUBLIC;
CREATE TRIGGER m25_funn_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON prosjektfunn
    FOR EACH ROW EXECUTE FUNCTION m25_funn_vakt();
CREATE TRIGGER m25_funn_ingen_truncate
    BEFORE TRUNCATE ON prosjektfunn EXECUTE FUNCTION m25_funn_vakt();


-- ------------------------------------------------------------
-- 2b. RLS og rettigheter.
-- ------------------------------------------------------------
ALTER TABLE prosjektterskel ENABLE ROW LEVEL SECURITY;
ALTER TABLE prosjektterskel FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON prosjektterskel
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE prosjekt ENABLE ROW LEVEL SECURITY;
ALTER TABLE prosjekt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON prosjekt
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — tre gjerder, som i
-- 100–106.
CREATE POLICY m25_sveip_tenantliste ON prosjekt
    FOR SELECT TO disponit_prosjekt_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

ALTER TABLE milepael ENABLE ROW LEVEL SECURITY;
ALTER TABLE milepael FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON milepael
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE prosjektarbeid ENABLE ROW LEVEL SECURITY;
ALTER TABLE prosjektarbeid FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON prosjektarbeid
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE prosjektfunn ENABLE ROW LEVEL SECURITY;
ALTER TABLE prosjektfunn FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON prosjektfunn
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

GRANT SELECT, INSERT, UPDATE ON prosjektterskel
    TO disponit_prosjekt_eier;
GRANT SELECT, INSERT, UPDATE ON prosjekt TO disponit_prosjekt_eier;
-- `milepael` HAR DELETE fordi betalingsplanen REDIGERES til den er
-- avtalt — men vakten nekter sletting av en milepæl som er NÅDD.
GRANT SELECT, INSERT, UPDATE, DELETE ON milepael
    TO disponit_prosjekt_eier;
-- `prosjektarbeid` HAR VERKEN UPDATE ELLER DELETE — append-only helt
-- ned til grantet.
GRANT SELECT, INSERT ON prosjektarbeid TO disponit_prosjekt_eier;
GRANT SELECT, INSERT, UPDATE ON prosjektfunn TO disponit_prosjekt_eier;
GRANT INSERT ON revisjonslogg TO disponit_prosjekt_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_prosjekt_eier;
RESET ROLE;


-- ------------------------------------------------------------
-- 3. Dørene. SECURITY DEFINER, eid av `disponit_prosjekt_eier`, SP-1.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_prosjekt_eier;

-- Evidenskjeden, ett sted. BELØP STÅR ALDRI HER (101s dom, ordrett).
CREATE FUNCTION m25_evidens(p_tenant TEXT, p_subjekt_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm25_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm25_prosjekt', 'handling', p_handling,
        'subjekt_id', p_subjekt_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm25_prosjekt',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:prosjektregister', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m25_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

CREATE FUNCTION m25_sett_terskler(
    p_tenant TEXT, p_budsjettvarsel_promille INT,
    p_milepael_frist_dogn INT, p_stillhet_dogn INT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm25_sett_terskler');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.prosjektterskel
        (tenant, budsjettvarsel_promille, milepael_frist_dogn,
         stillhet_dogn, versjon, oppdatert, oppdatert_av)
    VALUES (p_tenant, p_budsjettvarsel_promille, p_milepael_frist_dogn,
            p_stillhet_dogn, 1, now(), p_aktor)
    ON CONFLICT (tenant) DO UPDATE
        SET budsjettvarsel_promille =
                EXCLUDED.budsjettvarsel_promille,
            milepael_frist_dogn = EXCLUDED.milepael_frist_dogn,
            stillhet_dogn = EXCLUDED.stillhet_dogn,
            versjon = prosjektterskel.versjon + 1,
            oppdatert = now(), oppdatert_av = p_aktor
    RETURNING versjon INTO v_versjon;
    PERFORM public.m25_evidens(
        p_tenant, '00000000-0000-0000-0000-000000000000'::uuid,
        'terskler.satt', p_aktor,
        jsonb_build_object('versjon', v_versjon));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION m25_sett_terskler(TEXT, INT, INT, INT, TEXT)
    FROM PUBLIC;

CREATE FUNCTION m25_registrer_prosjekt(
    p_tenant TEXT, p_prosjekt_id UUID, p_kunde_ref TEXT, p_navn TEXT,
    p_kontrakt_ref TEXT, p_budsjett_ore BIGINT, p_start DATE,
    p_planlagt_slutt DATE, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm25_registrer_prosjekt');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.prosjekt
        (tenant, prosjekt_id, kunde_ref, navn, kontrakt_ref,
         budsjett_ore, start, planlagt_slutt, opprettet_av)
    VALUES (p_tenant, p_prosjekt_id, btrim(p_kunde_ref), btrim(p_navn),
            nullif(btrim(coalesce(p_kontrakt_ref, '')), ''),
            p_budsjett_ore, p_start, p_planlagt_slutt, p_aktor)
        ON CONFLICT (tenant, prosjekt_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;                               -- SP-2: stille ja
    END IF;
    PERFORM public.m25_evidens(p_tenant, p_prosjekt_id,
                               'prosjekt.registrert', p_aktor,
                               jsonb_build_object());
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m25_registrer_prosjekt(TEXT, UUID, TEXT, TEXT,
    TEXT, BIGINT, DATE, DATE, TEXT) FROM PUBLIC;

-- BETALINGSPLANDØREN. DOM 3: planen er KONTRAKTENS.
--
-- HELE SETTET I ETT KALL, som M-23s purreplan og av samme grunn: en dør
-- som la til én milepæl om gangen ville latt planen stå halvferdig, og
-- sveipen ville vurdert prosjektet mot den i det vinduet.
--
-- MILEPÆLER SOM ALT ER NÅDD RØRES IKKE. De er frosset (vakten), og en
-- omskriving av planen skal ikke kunne slette grunnlaget for et krav som
-- alt er stilt — derfor sletter døren bare de UNÅDDE, og en plan som
-- ville fjernet en nådd milepæl avvises av vakten.
CREATE FUNCTION m25_sett_betalingsplan(
    p_tenant TEXT, p_prosjekt_id UUID, p_milepaeler JSONB, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_antall INT; v_i INT; v_m JSONB; v_status TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm25_sett_betalingsplan');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT p.status INTO v_status FROM public.prosjekt p
     WHERE p.tenant = p_tenant AND p.prosjekt_id = p_prosjekt_id
       FOR UPDATE;
    IF v_status IS NULL THEN
        RAISE EXCEPTION 'm25_sett_betalingsplan: prosjektet finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_status <> 'aktiv' THEN
        RAISE EXCEPTION 'm25_sett_betalingsplan: prosjektet er %, og en'
            ' betalingsplan på et avsluttet prosjekt er en avtale ingen'
            ' skal handle på', v_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF jsonb_typeof(p_milepaeler) <> 'array' THEN
        RAISE EXCEPTION 'm25_sett_betalingsplan: milepælene må være en'
            ' liste' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_antall := jsonb_array_length(p_milepaeler);
    IF v_antall = 0 THEN
        RAISE EXCEPTION 'm25_sett_betalingsplan: en betalingsplan uten'
            ' milepæler er ingen plan — og `kontraktsfestet'
            '_betalingsplan` kan da aldri bli sant'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    DELETE FROM public.milepael m
     WHERE m.tenant = p_tenant AND m.prosjekt_id = p_prosjekt_id
       AND m.naadd_ts IS NULL;
    FOR v_i IN 0 .. v_antall - 1 LOOP
        v_m := p_milepaeler -> v_i;
        INSERT INTO public.milepael
            (tenant, prosjekt_id, milepael_nr, navn, planlagt_dato,
             belop_ore, opprettet_av)
        VALUES (p_tenant, p_prosjekt_id, v_i + 1,
                btrim(v_m ->> 'navn'),
                (v_m ->> 'planlagt_dato')::date,
                (v_m ->> 'belop_ore')::bigint, p_aktor);
    END LOOP;
    PERFORM public.m25_evidens(
        p_tenant, p_prosjekt_id, 'betalingsplan.satt', p_aktor,
        jsonb_build_object('milepaeler', v_antall));
    RETURN v_antall;
END $$;
REVOKE ALL ON FUNCTION m25_sett_betalingsplan(TEXT, UUID, JSONB, TEXT)
    FROM PUBLIC;

-- MILEPÆLSDØREN. DOM 2, OG DEN ER MODULENS SKARPESTE.
--
-- DOKUMENTASJONEN ER OBLIGATORISK. `milepael_dokumentert` i policyen kan
-- aldri bli sant om noe som ikke har en dokumentasjon å peke på — og en
-- automatisk faktura på en milepæl ingen har dokumentert er penger krevd
-- for arbeid som kanskje ikke er gjort.
--
-- DØREN FAKTURERER IKKE. Den merker milepælen nådd; hva som skjer med
-- kravet er et menneskes beslutning i et annet register.
CREATE FUNCTION m25_naa_milepael(
    p_tenant TEXT, p_prosjekt_id UUID, p_milepael_nr INT,
    p_dokumentasjon_ref TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_naadd TIMESTAMPTZ; v_finnes BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm25_naa_milepael');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_dokumentasjon_ref IS NULL
       OR p_dokumentasjon_ref !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm25_naa_milepael: en milepæl merket nådd uten'
            ' en henvisning til hva som dokumenterer den, er en påstand'
            ' — og den påstanden er grunnlaget for et krav mot kunden'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT (m.milepael_nr IS NOT NULL), m.naadd_ts
      INTO v_finnes, v_naadd
      FROM public.milepael m
     WHERE m.tenant = p_tenant AND m.prosjekt_id = p_prosjekt_id
       AND m.milepael_nr = p_milepael_nr
       FOR UPDATE;
    IF NOT coalesce(v_finnes, false) THEN
        RAISE EXCEPTION 'm25_naa_milepael: milepælen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_naadd IS NOT NULL THEN
        RETURN false;                               -- stille ja
    END IF;
    UPDATE public.milepael
       SET naadd_ts = now(), naadd_av = p_aktor,
           dokumentasjon_ref = btrim(p_dokumentasjon_ref)
     WHERE tenant = p_tenant AND prosjekt_id = p_prosjekt_id
       AND milepael_nr = p_milepael_nr AND naadd_ts IS NULL;
    PERFORM public.m25_evidens(
        p_tenant, p_prosjekt_id, 'milepael.naadd', p_aktor,
        jsonb_build_object('milepael_nr', p_milepael_nr));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m25_naa_milepael(TEXT, UUID, INT, TEXT, TEXT)
    FROM PUBLIC;

CREATE FUNCTION m25_registrer_arbeid(
    p_tenant TEXT, p_arbeid_id UUID, p_prosjekt_id UUID, p_utfort DATE,
    p_minutter INT, p_kostnad_ore BIGINT, p_beskrivelse TEXT,
    p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_rader INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm25_registrer_arbeid');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT p.status INTO v_status FROM public.prosjekt p
     WHERE p.tenant = p_tenant AND p.prosjekt_id = p_prosjekt_id
       FOR UPDATE;
    IF v_status IS NULL THEN
        RAISE EXCEPTION 'm25_registrer_arbeid: prosjektet finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_status <> 'aktiv' THEN
        RAISE EXCEPTION 'm25_registrer_arbeid: prosjektet er %, og'
            ' arbeid ført på et avsluttet prosjekt ville endret et'
            ' forbruk noen alt har konkludert på', v_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.prosjektarbeid
        (tenant, arbeid_id, prosjekt_id, utfort, minutter, kostnad_ore,
         beskrivelse, registrert_av)
    VALUES (p_tenant, p_arbeid_id, p_prosjekt_id, p_utfort, p_minutter,
            p_kostnad_ore, btrim(p_beskrivelse), p_aktor)
        ON CONFLICT (tenant, arbeid_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;                               -- SP-2: stille ja
    END IF;
    PERFORM public.m25_evidens(p_tenant, p_prosjekt_id,
                               'arbeid.registrert', p_aktor,
                               jsonb_build_object());
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m25_registrer_arbeid(TEXT, UUID, UUID, DATE, INT,
    BIGINT, TEXT, TEXT) FROM PUBLIC;

CREATE FUNCTION m25_avslutt_prosjekt(
    p_tenant TEXT, p_prosjekt_id UUID, p_begrunnelse TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm25_avslutt_prosjekt');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_begrunnelse IS NULL OR p_begrunnelse !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm25_avslutt_prosjekt: en avslutning uten'
            ' begrunnelse er en beslutning ingen kan etterprøve'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- FOR UPDATE, som i 104/105/106s skrivedører.
    SELECT p.status INTO v_status FROM public.prosjekt p
     WHERE p.tenant = p_tenant AND p.prosjekt_id = p_prosjekt_id
       FOR UPDATE;
    IF v_status IS NULL THEN
        RAISE EXCEPTION 'm25_avslutt_prosjekt: prosjektet finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_status <> 'aktiv' THEN
        RETURN false;                               -- stille ja
    END IF;
    UPDATE public.prosjekt
       SET status = 'avsluttet', avsluttet_ts = now(),
           avsluttet_av = p_aktor,
           avsluttet_begrunnelse = btrim(p_begrunnelse)
     WHERE tenant = p_tenant AND prosjekt_id = p_prosjekt_id
       AND status = 'aktiv';
    UPDATE public.prosjektfunn
       SET apen = false, lukket_ts = now()
     WHERE tenant = p_tenant AND prosjekt_id = p_prosjekt_id AND apen;
    PERFORM public.m25_evidens(p_tenant, p_prosjekt_id,
                               'prosjekt.avsluttet', p_aktor,
                               jsonb_build_object());
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m25_avslutt_prosjekt(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

RESET ROLE;


-- ------------------------------------------------------------
-- 3b. Lesedørene.
--
--     INGEN AV DEM RETURNERER ET KRAV. `klar_ore` er summen av de
--     milepælene som ER nådd og dokumentert — en MÅLT kjensgjerning om
--     hva kontrakten lar oss kreve, ikke et krav som er stilt.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_prosjekt_eier;

CREATE FUNCTION m25_prosjektstatus(p_tenant TEXT)
RETURNS TABLE(aktive INT, avsluttede INT, budsjett_ore BIGINT,
              forbruk_ore BIGINT, klar_ore BIGINT, apne_funn INT,
              over_budsjett INT, har_terskel BOOLEAN,
              terskelversjon INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm25_prosjektstatus');
    RETURN QUERY
    SELECT (SELECT count(*)::int FROM public.prosjekt p
             WHERE p.tenant = p_tenant AND p.status = 'aktiv'),
           (SELECT count(*)::int FROM public.prosjekt p
             WHERE p.tenant = p_tenant AND p.status = 'avsluttet'),
           -- SUM OVER BIGINT GIR NUMERIC (101s lærdom).
           (SELECT coalesce(sum(p.budsjett_ore), 0)::bigint
              FROM public.prosjekt p
             WHERE p.tenant = p_tenant AND p.status = 'aktiv'),
           (SELECT coalesce(sum(a.kostnad_ore), 0)::bigint
              FROM public.prosjektarbeid a
              JOIN public.prosjekt p ON p.tenant = a.tenant
               AND p.prosjekt_id = a.prosjekt_id
             WHERE a.tenant = p_tenant AND p.status = 'aktiv'),
           (SELECT coalesce(sum(m.belop_ore), 0)::bigint
              FROM public.milepael m
              JOIN public.prosjekt p ON p.tenant = m.tenant
               AND p.prosjekt_id = m.prosjekt_id
             WHERE m.tenant = p_tenant AND p.status = 'aktiv'
               AND m.naadd_ts IS NOT NULL),
           (SELECT count(*)::int FROM public.prosjektfunn f
             WHERE f.tenant = p_tenant AND f.apen),
           (SELECT count(*)::int FROM public.prosjektfunn f
             WHERE f.tenant = p_tenant AND f.apen
               AND f.funntype = 'budsjett_overskredet'),
           EXISTS (SELECT 1 FROM public.prosjektterskel t
                    WHERE t.tenant = p_tenant),
           (SELECT t.versjon FROM public.prosjektterskel t
             WHERE t.tenant = p_tenant);
END $$;
REVOKE ALL ON FUNCTION m25_prosjektstatus(TEXT) FROM PUBLIC;

CREATE FUNCTION m25_prosjektene(p_tenant TEXT, p_grense INT)
RETURNS TABLE(prosjekt_id UUID, kunde_ref TEXT, navn TEXT,
              kontrakt_ref TEXT, budsjett_ore BIGINT,
              forbruk_ore BIGINT, minutter BIGINT, start DATE,
              planlagt_slutt DATE, status TEXT, dogn_til_slutt INT,
              milepaeler INT, naadde INT, klar_ore BIGINT,
              plan_ore BIGINT, apne_funn TEXT[])
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm25_prosjektene');
    RETURN QUERY
    SELECT p.prosjekt_id, p.kunde_ref, p.navn, p.kontrakt_ref,
           p.budsjett_ore, a.kostnad, a.minutter, p.start,
           p.planlagt_slutt, p.status,
           (p.planlagt_slutt - current_date)::int,
           m.antall, m.naadde, m.klar, m.plan,
           coalesce(f.typer, ARRAY[]::TEXT[])
      FROM public.prosjekt p
      CROSS JOIN LATERAL (
            SELECT coalesce(sum(x.kostnad_ore), 0)::bigint AS kostnad,
                   coalesce(sum(x.minutter), 0)::bigint AS minutter
              FROM public.prosjektarbeid x
             WHERE x.tenant = p.tenant
               AND x.prosjekt_id = p.prosjekt_id) a
      CROSS JOIN LATERAL (
            SELECT count(*)::int AS antall,
                   count(*) FILTER (WHERE y.naadd_ts IS NOT NULL)::int
                       AS naadde,
                   coalesce(sum(y.belop_ore) FILTER (
                       WHERE y.naadd_ts IS NOT NULL), 0)::bigint AS klar,
                   coalesce(sum(y.belop_ore), 0)::bigint AS plan
              FROM public.milepael y
             WHERE y.tenant = p.tenant
               AND y.prosjekt_id = p.prosjekt_id) m
      LEFT JOIN LATERAL (
            SELECT array_agg(z.funntype ORDER BY z.funntype) AS typer
              FROM public.prosjektfunn z
             WHERE z.tenant = p.tenant AND z.prosjekt_id = p.prosjekt_id
               AND z.apen) f ON true
     WHERE p.tenant = p_tenant
     -- AKTIVE FØRST, deretter de som ligger nærmest sin planlagte slutt.
     ORDER BY (p.status = 'aktiv') DESC, p.planlagt_slutt, p.prosjekt_id
     LIMIT greatest(least(coalesce(p_grense, 200), 1000), 1);
END $$;
REVOKE ALL ON FUNCTION m25_prosjektene(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m25_milepaelene(p_tenant TEXT, p_prosjekt_id UUID)
RETURNS TABLE(milepael_nr INT, navn TEXT, planlagt_dato DATE,
              belop_ore BIGINT, naadd_ts TIMESTAMPTZ, naadd_av TEXT,
              dokumentasjon_ref TEXT, dogn_over_frist INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm25_milepaelene');
    RETURN QUERY
    SELECT m.milepael_nr, m.navn, m.planlagt_dato, m.belop_ore,
           m.naadd_ts, m.naadd_av, m.dokumentasjon_ref,
           -- EN NÅDD MILEPÆL HAR INGEN LØPENDE FRIST. `NULL` er det
           -- ærlige svaret; «0 døgn over» ville sett ut som en måling.
           CASE WHEN m.naadd_ts IS NULL
                THEN (current_date - m.planlagt_dato)::int END
      FROM public.milepael m
     WHERE m.tenant = p_tenant AND m.prosjekt_id = p_prosjekt_id
     ORDER BY m.milepael_nr
     LIMIT 500;
END $$;
REVOKE ALL ON FUNCTION m25_milepaelene(TEXT, UUID) FROM PUBLIC;

CREATE FUNCTION m25_arbeidet(p_tenant TEXT, p_prosjekt_id UUID)
RETURNS TABLE(arbeid_id UUID, utfort DATE, minutter INT,
              kostnad_ore BIGINT, beskrivelse TEXT,
              registrert TIMESTAMPTZ, registrert_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm25_arbeidet');
    RETURN QUERY
    SELECT a.arbeid_id, a.utfort, a.minutter, a.kostnad_ore,
           a.beskrivelse, a.registrert, a.registrert_av
      FROM public.prosjektarbeid a
     WHERE a.tenant = p_tenant AND a.prosjekt_id = p_prosjekt_id
     ORDER BY a.utfort DESC, a.registrert DESC
     LIMIT 500;
END $$;
REVOKE ALL ON FUNCTION m25_arbeidet(TEXT, UUID) FROM PUBLIC;

CREATE FUNCTION m25_tersklene(p_tenant TEXT)
RETURNS TABLE(budsjettvarsel_promille INT, milepael_frist_dogn INT,
              stillhet_dogn INT, versjon INT, oppdatert TIMESTAMPTZ,
              oppdatert_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm25_tersklene');
    RETURN QUERY
    SELECT t.budsjettvarsel_promille, t.milepael_frist_dogn,
           t.stillhet_dogn, t.versjon, t.oppdatert, t.oppdatert_av
      FROM public.prosjektterskel t WHERE t.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m25_tersklene(TEXT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Sveipens kandidater. TERSKLENE LESES FRA TABELLEN.
-- ------------------------------------------------------------
CREATE FUNCTION m25_funnkandidater(p_tenant TEXT, p_dag DATE)
RETURNS TABLE(prosjekt_id UUID, funntype TEXT, over_grense BIGINT,
              milepael_nr INT, terskelversjon INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_t RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm25_funnkandidater');
    SELECT * INTO v_t FROM public.prosjektterskel t
     WHERE t.tenant = p_tenant;
    IF NOT FOUND THEN
        -- 0. INGEN TERSKEL: aktive prosjekter, men ingen grenser å måle
        --    dem mot. Hvert av de andre funnene ville vært en gjetning.
        RETURN QUERY
        SELECT p.prosjekt_id, 'ingen_terskel'::text, NULL::bigint,
               NULL::int, NULL::int
          FROM public.prosjekt p
         WHERE p.tenant = p_tenant AND p.status = 'aktiv';
        RETURN;
    END IF;

    RETURN QUERY
    -- 1. MILEPÆL OVER FRIST: en unådd milepæl som har passert sin
    --    planlagte dato med mer enn tenantens frist. NUMMERET STÅR PÅ
    --    FUNNET — uten det måtte et menneske lete.
    SELECT p.prosjekt_id, 'milepael_over_frist'::text,
           (p_dag - d.planlagt_dato - v_t.milepael_frist_dogn)::bigint,
           d.milepael_nr, v_t.versjon
      FROM public.prosjekt p
      JOIN LATERAL (
            SELECT m.milepael_nr, m.planlagt_dato
              FROM public.milepael m
             WHERE m.tenant = p.tenant
               AND m.prosjekt_id = p.prosjekt_id
               AND m.naadd_ts IS NULL
               AND p_dag - m.planlagt_dato > v_t.milepael_frist_dogn
             ORDER BY m.planlagt_dato, m.milepael_nr
             LIMIT 1) d ON true
     WHERE p.tenant = p_tenant AND p.status = 'aktiv'
    UNION ALL
    -- 2. BUDSJETT OVERSKREDET: forbruket ligger mer enn tenantens
    --    promille over budsjettet. HELTALLSARITMETIKK OG INGEN
    --    DIVISJON: `forbruk * 1000 > budsjett * (1000 + promille)`.
    --    En grense som «nesten» er passert er ingen grense.
    SELECT p.prosjekt_id, 'budsjett_overskredet'::text,
           (a.kostnad - p.budsjett_ore)::bigint, NULL::int, v_t.versjon
      FROM public.prosjekt p
      CROSS JOIN LATERAL (
            SELECT coalesce(sum(x.kostnad_ore), 0)::bigint AS kostnad
              FROM public.prosjektarbeid x
             WHERE x.tenant = p.tenant
               AND x.prosjekt_id = p.prosjekt_id) a
     WHERE p.tenant = p_tenant AND p.status = 'aktiv'
       AND a.kostnad * 1000
           > p.budsjett_ore * (1000 + v_t.budsjettvarsel_promille)
    UNION ALL
    -- 3. INGEN BETALINGSPLAN: et aktivt prosjekt uten en eneste
    --    milepæl. `kontraktsfestet_betalingsplan` kan da aldri bli
    --    sant, og ingen vet hva vi har lov å kreve når.
    SELECT p.prosjekt_id, 'betalingsplan_mangler'::text, NULL::bigint,
           NULL::int, v_t.versjon
      FROM public.prosjekt p
     WHERE p.tenant = p_tenant AND p.status = 'aktiv'
       AND NOT EXISTS (SELECT 1 FROM public.milepael m
                        WHERE m.tenant = p.tenant
                          AND m.prosjekt_id = p.prosjekt_id)
    UNION ALL
    -- 4. INGEN ARBEID REGISTRERT: et aktivt prosjekt som ikke har fått
    --    en eneste føring på tenantens stillhetsgrense. Et prosjekt
    --    ingen har ført timer på er enten ferdig eller glemt, og de to
    --    er forskjellige tilstander med hver sin handling.
    SELECT p.prosjekt_id, 'ingen_arbeid_registrert'::text,
           (p_dag - greatest(p.start, coalesce(a.siste, p.start))
            - v_t.stillhet_dogn)::bigint, NULL::int, v_t.versjon
      FROM public.prosjekt p
      LEFT JOIN LATERAL (
            SELECT max(x.utfort) AS siste FROM public.prosjektarbeid x
             WHERE x.tenant = p.tenant
               AND x.prosjekt_id = p.prosjekt_id) a ON true
     WHERE p.tenant = p_tenant AND p.status = 'aktiv'
       AND p.start <= p_dag
       AND p_dag - greatest(p.start, coalesce(a.siste, p.start))
           > v_t.stillhet_dogn;
END $$;
REVOKE ALL ON FUNCTION m25_funnkandidater(TEXT, DATE) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 4b. Sveipen. KRYSS-TENANT, egen LOGIN-rolle, egen timer.
--
--     SVEIPEN FAKTURERER INGEN OG MERKER INGEN MILEPÆL NÅDD. Den
--     kunne, teknisk — den vet hvilke som har passert sin dato. Men en
--     milepæl er grunnlaget for et krav mot en kunde, og en jobb som
--     merket den nådd om natten ville skapt det kravet uten at noen
--     dokumenterte noe.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_prosjekt_eier;

CREATE FUNCTION m25_sveip_prosjekter(p_grense INT DEFAULT 500)
RETURNS TABLE(tenanter INT, nye INT, oppdaterte INT, lukkede INT,
              avkortet INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_n INT; v_grense INT;
        v_dag DATE; v_naa TIMESTAMPTZ;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm25_sveip_prosjekter: sveipen er KRYSS-TENANT og'
            ' kjøres uten tenantkontekst — en kaller som har satt en'
            ' kontekst ber om noe annet enn det denne funksjonen gjør'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_grense := greatest(least(coalesce(p_grense, 500), 5000), 1);
    v_dag := current_date;
    v_naa := now();
    tenanter := 0; nye := 0; oppdaterte := 0; lukkede := 0; avkortet := 0;
    SELECT array_agg(DISTINCT p.tenant ORDER BY p.tenant) INTO v_tenanter
      FROM public.prosjekt p;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        tenanter := tenanter + 1;

        UPDATE public.prosjektfunn pf
           SET sist_sett_sveip = v_naa, apen = true, lukket_ts = NULL,
               over_grense = kand.over_grense,
               milepael_nr = kand.milepael_nr,
               terskelversjon = kand.terskelversjon
          FROM public.m25_funnkandidater(v_t, v_dag) kand
         WHERE pf.tenant = v_t AND pf.prosjekt_id = kand.prosjekt_id
           AND pf.funntype = kand.funntype;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        oppdaterte := oppdaterte + v_n;

        INSERT INTO public.prosjektfunn
            (tenant, prosjekt_id, funntype, over_grense, milepael_nr,
             terskelversjon, forst_sett, sist_sett_sveip, apen)
        SELECT v_t, kand.prosjekt_id, kand.funntype, kand.over_grense,
               kand.milepael_nr, kand.terskelversjon, v_naa, v_naa, true
          FROM public.m25_funnkandidater(v_t, v_dag) kand
         WHERE NOT EXISTS (
                SELECT 1 FROM public.prosjektfunn pf
                 WHERE pf.tenant = v_t
                   AND pf.prosjekt_id = kand.prosjekt_id
                   AND pf.funntype = kand.funntype)
         ORDER BY coalesce(kand.over_grense, 0) DESC,
                  kand.prosjekt_id, kand.funntype
         LIMIT v_grense;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        nye := nye + v_n;
        IF v_n = v_grense THEN
            avkortet := avkortet + 1;
        END IF;

        UPDATE public.prosjektfunn pf
           SET apen = false, lukket_ts = v_naa
         WHERE pf.tenant = v_t AND pf.apen
           AND NOT EXISTS (
                SELECT 1 FROM public.m25_funnkandidater(v_t, v_dag) kand
                 WHERE kand.prosjekt_id = pf.prosjekt_id
                   AND kand.funntype = pf.funntype);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        lukkede := lukkede + v_n;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m25_sveip_prosjekter(INT) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 5. Rettighetene. SP-7: kjøretidsrollen får INGEN tabellrettigheter.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_prosjekt_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m25_prosjektstatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m25_prosjektene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m25_milepaelene(TEXT, UUID)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m25_arbeidet(TEXT, UUID)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m25_tersklene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m25_sett_terskler(TEXT, INT, INT, INT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m25_registrer_prosjekt(TEXT, UUID, TEXT, TEXT, TEXT,'
            ' BIGINT, DATE, DATE, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m25_sett_betalingsplan(TEXT, UUID, JSONB, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m25_naa_milepael(TEXT, UUID, INT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m25_registrer_arbeid(TEXT, UUID, UUID, DATE, INT, BIGINT,'
            ' TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m25_avslutt_prosjekt(TEXT, UUID, TEXT, TEXT) TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_prosjektsveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m25_sveip_prosjekter(INT)'
            ' TO disponit_prosjektsveip';
    END IF;
END $$;
RESET ROLE;
