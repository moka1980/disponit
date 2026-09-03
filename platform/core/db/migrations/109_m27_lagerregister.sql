-- 109: M-27 lager- og logistikkagent v1 — BEHOLDNINGEN.
-- Fem tenant-skopede tabeller, sytten dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- V1-AVGRENSNINGEN, ORDRETT FRA POLICYENE VI SENDER UT: to av tre
-- bransjemaler navngir denne modulen som verifikatoren `v_lager`,
-- betrodd for `lager_reservert`, `retur_registrert` og
-- `prognose_konfidens` — og bruker dem til å slippe
-- `lager.bestill_pafyll` og `materiell.bestill` gjennom som
-- `modus: auto`.
--
-- v1 BESTILLER INGENTING, BEREGNER INGEN PROGNOSE OG ATTESTERER
-- INGENTING.
--
-- DOMMEN: et bestillingspunkt som er passert er et FUNN, ikke en
-- bestilling. En modul som bestilte påfyll om natten ville bundet
-- virksomheten økonomisk på et grunnlag ingen har målt — og den ville
-- gjort det mot en leverandøravtale den ikke eier (M-24).
--
-- OG DEN BEREGNER INGEN PROGNOSE. `prognose_konfidens` er en påstand om
-- hvor sikkert et framtidig forbruk er anslått. v1 har ingen anslag: det
-- finnes ikke ett glidende gjennomsnitt, ingen forbruksrate, ingen
-- ekstrapolering. Det er en bevisst tom plass — en konfidens uten en
-- målt treffrate bak seg er et tall som ser ut som kunnskap.
--
-- DOMMENE v1 HVILER PÅ, håndhevet i DATAMODELLEN:
--
--   1. BEHOLDNINGEN ER IKKE ET FELT. Det finnes ingen kolonne noen kan
--      sette. Beholdningen er SUMMEN AV BEVEGELSER, alltid, og
--      `m27_beholdning()` er den eneste veien til tallet. En lagerstatus
--      som kunne skrives direkte ville gjort «hvorfor står det 7 her»
--      til et spørsmål uten svar — og `lager_reservert` til en
--      attestasjon om et tall ingen kan spore.
--
--   2. EN BEVEGELSE ER FROSSET. Ingen UPDATE, ingen DELETE. En feilført
--      linje rettes med en NY linje. Et lager der historikken kunne
--      skrives om er et lager der svinn kan forsvinne.
--
--   3. BEHOLDNINGEN KAN IKKE BLI NEGATIV. Vakten låser vareraden og
--      summerer FØR linjen slippes inn. En negativ beholdning er ikke en
--      tilstand i verden; den er en måling som er feil, og et register
--      som tillot den ville rapportert usannhet med fullt alvor.
--
--   4. ET BESTILLINGSPUNKT ENDRES ALDRI — DET ERSTATTES. Versjonert,
--      datert, frosset, uten overlapp, akkurat som prisen i 108. «Hva
--      var punktet den dagen funnet ble reist» er spørsmålet som gjør
--      funnet etterprøvbart.
--
--   5. ANTALL ER HELTALL I VARENS MINSTE ENHET, `BIGINT`. Tenanten
--      bestemmer enheten («stk», «meter», «gram»); plattformen
--      bestemmer at det er et HELTALL. En beholdning i flyttall driver
--      fra sannheten én bevegelse av gangen.
--
-- v1 VERDSETTER IKKE BEHOLDNINGEN. `enhetskost_ore` står på den enkelte
-- bevegelsen som et FAKTUM om hva den linjen kostet. Å gange den opp til
-- en lagerverdi krever et kostprinsipp (FIFO, gjennomsnitt) — altså en
-- regnskapsbeslutning, og den tas ikke av en modul.
--
-- GRENSEN MOT M-24 (105): M-27 eier BEHOLDNINGEN. M-24 eier AVTALEN med
-- leverandøren et påfyll ville gått på. Et passert bestillingspunkt er
-- M-27s funn; hvilken avtale bestillingen ville løpt på, er M-24s rad.
-- v1 kobler dem ikke: koblingen er nettopp den bestillingen vi ikke gjør.
--
-- SVEIPEN HAR SIN EGEN ROLLE OG SIN EGEN TIMER (095/100–108):
-- `disponit_lagersveip` har nøyaktig ÉN rettighet — EXECUTE på
-- `m27_sveip_lager` — og INGEN tabellrettigheter. Sveipen BESTILLER
-- INGENTING og JUSTERER INGEN BEHOLDNING; den skriver FUNN.
--
-- EIERROLLEN HETER `disponit_beholdning_eier`, IKKE `disponit_lager_eier`:
-- det navnet er M-4s (093/099), og to moduler som deler eierrolle er
-- nøyaktig den fullmaktsdelingen «én rolle per modul» finnes for å hindre.
--
-- FORMENE ER HUSETS: tabellene eies av migrator, dørene av NOLOGIN-rollen
-- `disponit_beholdning_eier`, tenant TEXT + RLS ENABLE+FORCE +
-- `tenant_isolasjon`, SP-1 i hver tenantbundet definer, ingen BYPASSRLS.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_beholdning_eier') THEN
        RAISE EXCEPTION 'rollen disponit_beholdning_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_beholdning_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `lagerterskel` — ÉN per tenant. GRENSENE ER TENANTENS.
CREATE TABLE lagerterskel (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- Hvor lenge en aktiv vare kan stå uten én eneste bevegelse før det
    -- er et funn. Dødt lager er bundet kapital, og ingen ser det uten at
    -- noen sier fra.
    stille_dogn INT NOT NULL DEFAULT 180
        CHECK (stille_dogn BETWEEN 0 AND 3650),
    -- Hvor lenge en aktiv vare kan stå uten et bestillingspunkt. Uten
    -- punktet kan `under_bestillingspunkt` aldri bli sant for varen — og
    -- fraværet av funn ser da ut som «alt er i orden».
    uten_punkt_dogn INT NOT NULL DEFAULT 30
        CHECK (uten_punkt_dogn BETWEEN 0 AND 3650),
    -- Hvor lenge det kan gå mellom tellinger. En beholdning ingen har
    -- talt er en påstand, ikke en måling.
    telleintervall_dogn INT NOT NULL DEFAULT 365
        CHECK (telleintervall_dogn BETWEEN 0 AND 3650),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]'),
    CONSTRAINT lagerterskel_pk PRIMARY KEY (tenant)
);

-- `vare` — det vi fører beholdning på.
CREATE TABLE vare (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    vare_id UUID NOT NULL,
    -- Tenantens egen kode. Ingen validering av format: en modul som
    -- krevde et bestemt format ville låst lageret til ett verktøy.
    kode TEXT NOT NULL CHECK (kode ~ '[^[:space:]]'),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    -- VARENS MINSTE ENHET. Antall telles i HELE slike enheter — det er
    -- tenantens ord, men plattformens heltall.
    enhet TEXT NOT NULL CHECK (enhet ~ '[^[:space:]]'),
    aktiv BOOLEAN NOT NULL DEFAULT true,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT vare_pk PRIMARY KEY (tenant, vare_id),
    CONSTRAINT vare_kode_unik UNIQUE (tenant, kode)
);
CREATE INDEX vare_aktive ON vare (tenant) WHERE aktiv;

-- `bestillingspunkt` — DOM 4. Versjonert, datert, FROSSET.
--
-- «Hva var punktet den dagen funnet ble reist» er det som gjør funnet
-- etterprøvbart. Et punkt som kunne skrives om i ettertid ville gjort
-- hvert eldre funn til en gjetning.
CREATE TABLE bestillingspunkt (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    vare_id UUID NOT NULL,
    versjon INT NOT NULL CHECK (versjon >= 1),
    -- I VARENS MINSTE ENHET, heltall. Null er lovlig: «vi holder ikke
    -- lager på denne» er et svar, og det er noe annet enn å mangle et
    -- punkt.
    punkt_antall BIGINT NOT NULL CHECK (punkt_antall >= 0),
    gyldig_fra DATE NOT NULL,
    -- ÅPEN ENDE ER LOVLIG: det gjeldende punktet har ingen sluttdato.
    -- Døren SETTER den når en ny versjon avløser det.
    gyldig_til DATE,
    -- HVORFOR punktet ble satt. Et bestillingspunkt uten begrunnelse er
    -- en beslutning ingen kan etterprøve — og det er punktet som avgjør
    -- når noen blir bedt om å bruke penger.
    begrunnelse TEXT NOT NULL CHECK (begrunnelse ~ '[^[:space:]]'),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT bestillingspunkt_pk PRIMARY KEY (tenant, vare_id, versjon),
    CONSTRAINT bestillingspunkt_vare_fk FOREIGN KEY (tenant, vare_id)
        REFERENCES vare (tenant, vare_id),
    CONSTRAINT bestillingspunkt_vindu_framover
        CHECK (gyldig_til IS NULL OR gyldig_til >= gyldig_fra)
);
CREATE INDEX bestillingspunkt_oppslag
    ON bestillingspunkt (tenant, vare_id, gyldig_fra DESC);
-- ÉTT ÅPENT PUNKT PER VARE. To samtidige ville gjort «når skal vi
-- bestille» til et spørsmål med to svar.
CREATE UNIQUE INDEX bestillingspunkt_ett_apent
    ON bestillingspunkt (tenant, vare_id) WHERE gyldig_til IS NULL;

-- `lagerbevegelse` — DOM 1, 2 OG 5. HOVEDBOKEN FOR ANTALL.
--
-- Beholdningen er summen av disse radene. Det finnes ingen annen
-- beholdning noe sted i skjemaet, og det er hele poenget: en
-- lagerstatus som kunne settes direkte ville gjort «hvorfor står det 7
-- her» til et spørsmål uten svar.
CREATE TABLE lagerbevegelse (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    bevegelse_id UUID NOT NULL,
    vare_id UUID NOT NULL,
    -- LUKKET SETT. En fritekstlig type ville gjort «hvor mye svant»
    -- til en tekstsøk-oppgave.
    bevegelsestype TEXT NOT NULL
        CONSTRAINT lagerbevegelse_type_lukket CHECK (bevegelsestype IN (
            'mottak', 'uttak', 'retur', 'svinn', 'telling')),
    -- FORTEGNET FØLGER TYPEN, og CHECK-en håndhever det også ved
    -- direkte DML. `telling` er den ene som kan gå begge veier — og den
    -- ene som kan være NULL-endring: en telling som bekreftet
    -- beholdningen ER et bevis, og å droppe linjen ville gjort «når ble
    -- dette sist talt» ubesvarlig.
    endring BIGINT NOT NULL,
    CONSTRAINT lagerbevegelse_fortegn CHECK (
        CASE bevegelsestype
            WHEN 'mottak'  THEN endring > 0
            WHEN 'retur'   THEN endring > 0
            WHEN 'uttak'   THEN endring < 0
            WHEN 'svinn'   THEN endring < 0
            WHEN 'telling' THEN true
        END),
    -- HVA LINJEN KOSTET PER ENHET, i øre. Et FAKTUM om denne linjen,
    -- ikke en lagerverdi: å gange den opp krever et kostprinsipp, og
    -- det er en regnskapsbeslutning en modul ikke tar.
    enhetskost_ore BIGINT CHECK (enhetskost_ore IS NULL
                                 OR enhetskost_ore >= 0),
    utfort DATE NOT NULL,
    -- HVA SOM SKJEDDE. Et svinn uten notat er et tall ingen kan handle
    -- på.
    notat TEXT NOT NULL CHECK (notat ~ '[^[:space:]]'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT lagerbevegelse_pk PRIMARY KEY (tenant, bevegelse_id),
    CONSTRAINT lagerbevegelse_vare_fk FOREIGN KEY (tenant, vare_id)
        REFERENCES vare (tenant, vare_id)
);
CREATE INDEX lagerbevegelse_oppslag
    ON lagerbevegelse (tenant, vare_id, utfort DESC, registrert DESC);
CREATE INDEX lagerbevegelse_tellinger
    ON lagerbevegelse (tenant, vare_id, utfort DESC)
    WHERE bevegelsestype = 'telling';

-- `lagerfunn` — funnene. Nøklet på varen og typen.
CREATE TABLE lagerfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    vare_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CONSTRAINT lagerfunn_type_lukket CHECK (funntype IN (
            'under_bestillingspunkt', 'uten_bevegelse',
            'uten_bestillingspunkt', 'ikke_talt', 'ingen_terskel')),
    -- Ett tall med én betydning per funntype: DØGN for de tre
    -- tidsfunnene, MANGLENDE ANTALL for `under_bestillingspunkt`.
    over_grense BIGINT,
    -- Beholdningen slik den var da funnet ble reist. Den er summen av
    -- bevegelser og kan derfor regnes på nytt — men et funn som ikke
    -- bar tallet ville krevd at leseren gjorde det selv.
    beholdning BIGINT,
    punktversjon INT,
    terskelversjon INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT lagerfunn_pk PRIMARY KEY (tenant, vare_id, funntype),
    CONSTRAINT lagerfunn_vare_fk FOREIGN KEY (tenant, vare_id)
        REFERENCES vare (tenant, vare_id),
    CONSTRAINT lagerfunn_lukking_helhet CHECK (
        (apen AND lukket_ts IS NULL)
     OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE INDEX lagerfunn_apne ON lagerfunn (tenant, funntype) WHERE apen;


-- ------------------------------------------------------------
-- 2. Vaktene.
-- ------------------------------------------------------------

CREATE FUNCTION m27_terskel_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'lagerterskel: TRUNCATE avvist — grensene endres'
            ' ved å sette nye, ikke ved å fjerne dem under føttene på'
            ' sveipen' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'lagerterskel: DELETE avvist — en tenant uten'
            ' grenser kan ikke måle noe, og det er en tilstand sveipen'
            ' skal SI FRA om' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.versjon <= OLD.versjon THEN
        RAISE EXCEPTION 'lagerterskel: versjonen må øke ved endring'
            ' (% -> %)', OLD.versjon, NEW.versjon
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m27_terskel_vakt() FROM PUBLIC;
CREATE TRIGGER m27_terskel_vakt
    BEFORE UPDATE OR DELETE ON lagerterskel
    FOR EACH ROW EXECUTE FUNCTION m27_terskel_vakt();
CREATE TRIGGER m27_terskel_ingen_truncate
    BEFORE TRUNCATE ON lagerterskel
    EXECUTE FUNCTION m27_terskel_vakt();

CREATE FUNCTION m27_vare_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'vare: TRUNCATE avvist — en vare deaktiveres,'
            ' den tømmes ikke bort'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'vare: DELETE avvist — sett aktiv til false. En'
            ' slettet vare ville tatt bevegelseshistorikken med seg, og'
            ' den er hele beholdningen'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.vare_id IS DISTINCT FROM OLD.vare_id
       OR NEW.kode IS DISTINCT FROM OLD.kode
       OR NEW.enhet IS DISTINCT FROM OLD.enhet
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'vare: identiteten, koden og ENHETEN er frosset'
            ' — en endret enhet ville gjort hele bevegelseshistorikken'
            ' til tall uten måleenhet'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m27_vare_vakt() FROM PUBLIC;
CREATE TRIGGER m27_vare_vakt
    BEFORE UPDATE OR DELETE ON vare
    FOR EACH ROW EXECUTE FUNCTION m27_vare_vakt();
CREATE TRIGGER m27_vare_ingen_truncate
    BEFORE TRUNCATE ON vare EXECUTE FUNCTION m27_vare_vakt();

-- DOM 4: ET BESTILLINGSPUNKT ENDRES ALDRI, OG TO VERSJONER OVERLAPPER
-- IKKE. Samme vakt som prisen i 108, av samme grunn: punktet er det som
-- avgjør når noen blir bedt om å bruke penger.
CREATE FUNCTION m27_punkt_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'bestillingspunkt: TRUNCATE avvist — uten'
            ' punktene er hvert eldre funn en gjetning'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'bestillingspunkt: DELETE avvist — et punkt'
            ' erstattes av en ny versjon, det slettes aldri'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        -- BARE `gyldig_til` KAN ENDRES, og bare fra åpen til lukket.
        IF NEW.tenant IS DISTINCT FROM OLD.tenant
           OR NEW.vare_id IS DISTINCT FROM OLD.vare_id
           OR NEW.versjon IS DISTINCT FROM OLD.versjon
           OR NEW.punkt_antall IS DISTINCT FROM OLD.punkt_antall
           OR NEW.gyldig_fra IS DISTINCT FROM OLD.gyldig_fra
           OR NEW.begrunnelse IS DISTINCT FROM OLD.begrunnelse
           OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
           OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av THEN
            RAISE EXCEPTION 'bestillingspunkt: raden er FROSSET — bare'
                ' gyldig_til settes, og bare når en ny versjon avløser'
                ' denne' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF OLD.gyldig_til IS NOT NULL THEN
            RAISE EXCEPTION 'bestillingspunkt: versjon % er alt lukket'
                ' (%)', OLD.versjon, OLD.gyldig_til
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.gyldig_til IS NULL THEN
            RAISE EXCEPTION 'bestillingspunkt: en lukking må ha en dato'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    -- INGEN OVERLAPP, målt mot ALLE andre versjoner av samme vare.
    IF EXISTS (SELECT 1 FROM public.bestillingspunkt b
                WHERE b.tenant = NEW.tenant AND b.vare_id = NEW.vare_id
                  AND b.versjon <> NEW.versjon
                  AND b.gyldig_fra
                      <= coalesce(NEW.gyldig_til, DATE '9999-12-31')
                  AND coalesce(b.gyldig_til, DATE '9999-12-31')
                      >= NEW.gyldig_fra) THEN
        RAISE EXCEPTION 'bestillingspunkt: versjon % overlapper en annen'
            ' versjon i tid — «hva var punktet den dagen» ville da hatt'
            ' to svar', NEW.versjon USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m27_punkt_vakt() FROM PUBLIC;
CREATE TRIGGER m27_punkt_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON bestillingspunkt
    FOR EACH ROW EXECUTE FUNCTION m27_punkt_vakt();
CREATE TRIGGER m27_punkt_ingen_truncate
    BEFORE TRUNCATE ON bestillingspunkt
    EXECUTE FUNCTION m27_punkt_vakt();

-- DOM 2 OG 3: EN BEVEGELSE ER FROSSET, OG BEHOLDNINGEN KAN IKKE BLI
-- NEGATIV.
--
-- Dette er modulens skarpeste vakt. Den håndhever begge dommene ved
-- DIREKTE DML, ikke bare i døren: en regel som bare fantes i døren ville
-- vært borte i det øyeblikket noen skrev en INSERT for hånd — og
-- beholdningen er summen av nettopp disse radene.
CREATE FUNCTION m27_bevegelse_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_sum BIGINT;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'lagerbevegelse: TRUNCATE avvist — en tømt'
            ' hovedbok er en beholdning på null som ingen har talt'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'lagerbevegelse: DELETE avvist — en feilført'
            ' linje rettes med en NY linje. Et lager der historikken'
            ' kunne slettes er et lager der svinn forsvinner'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'lagerbevegelse: raden er FROSSET — en feilført'
            ' linje rettes med en NY linje, aldri ved å skrive om den'
            ' gamle' USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- DOM 3. Vareraden låses FØRST, slik at to samtidige uttak ikke kan
    -- lese samme beholdning og begge komme til at det er nok igjen.
    PERFORM 1 FROM public.vare v
      WHERE v.tenant = NEW.tenant AND v.vare_id = NEW.vare_id
      FOR UPDATE;
    SELECT coalesce(sum(b.endring), 0) INTO v_sum
      FROM public.lagerbevegelse b
     WHERE b.tenant = NEW.tenant AND b.vare_id = NEW.vare_id;
    IF v_sum + NEW.endring < 0 THEN
        RAISE EXCEPTION 'lagerbevegelse: beholdningen ville blitt'
            ' negativ (% + % = %) — en negativ beholdning er ikke en'
            ' tilstand i verden, den er en måling som er feil',
            v_sum, NEW.endring, v_sum + NEW.endring
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m27_bevegelse_vakt() FROM PUBLIC;
CREATE TRIGGER m27_bevegelse_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON lagerbevegelse
    FOR EACH ROW EXECUTE FUNCTION m27_bevegelse_vakt();
CREATE TRIGGER m27_bevegelse_ingen_truncate
    BEFORE TRUNCATE ON lagerbevegelse
    EXECUTE FUNCTION m27_bevegelse_vakt();

CREATE FUNCTION m27_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'lagerfunn: TRUNCATE avvist — funnene lukkes av'
            ' sveipen når tilstanden er borte, de tømmes ikke bort'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'lagerfunn: DELETE avvist — et funn lukkes, det'
            ' slettes ikke. Historikken er hele verdien'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.vare_id IS DISTINCT FROM OLD.vare_id
       OR NEW.funntype IS DISTINCT FROM OLD.funntype
       OR NEW.forst_sett IS DISTINCT FROM OLD.forst_sett THEN
        RAISE EXCEPTION 'lagerfunn: identiteten og førstegangen er'
            ' frosset' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m27_funn_vakt() FROM PUBLIC;
CREATE TRIGGER m27_funn_vakt
    BEFORE UPDATE OR DELETE ON lagerfunn
    FOR EACH ROW EXECUTE FUNCTION m27_funn_vakt();
CREATE TRIGGER m27_funn_ingen_truncate
    BEFORE TRUNCATE ON lagerfunn EXECUTE FUNCTION m27_funn_vakt();


-- ------------------------------------------------------------
-- 2b. RLS og rettigheter.
-- ------------------------------------------------------------
ALTER TABLE lagerterskel ENABLE ROW LEVEL SECURITY;
ALTER TABLE lagerterskel FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON lagerterskel
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE vare ENABLE ROW LEVEL SECURITY;
ALTER TABLE vare FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON vare
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — tre gjerder: bare
-- SELECT, bare eierrollen, og bare når det IKKE står en tenantkontekst.
CREATE POLICY m27_sveip_tenantliste ON vare
    FOR SELECT TO disponit_beholdning_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

ALTER TABLE bestillingspunkt ENABLE ROW LEVEL SECURITY;
ALTER TABLE bestillingspunkt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON bestillingspunkt
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE lagerbevegelse ENABLE ROW LEVEL SECURITY;
ALTER TABLE lagerbevegelse FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON lagerbevegelse
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE lagerfunn ENABLE ROW LEVEL SECURITY;
ALTER TABLE lagerfunn FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON lagerfunn
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

GRANT SELECT, INSERT, UPDATE ON lagerterskel
    TO disponit_beholdning_eier;
GRANT SELECT, INSERT, UPDATE ON vare TO disponit_beholdning_eier;
-- `bestillingspunkt` har ikke DELETE: en versjon erstattes, den slettes
-- aldri. UPDATE er med fordi det er slik `gyldig_til` settes når en ny
-- versjon avløser den forrige — vakten begrenser den til det ene feltet.
GRANT SELECT, INSERT, UPDATE ON bestillingspunkt
    TO disponit_beholdning_eier;
-- `lagerbevegelse` har VERKEN UPDATE ELLER DELETE. Hovedboken er
-- append-only, og det er to gjerder som sier det: rettigheten her, og
-- vakten som stanser den som likevel skulle ha den.
GRANT SELECT, INSERT ON lagerbevegelse TO disponit_beholdning_eier;
GRANT SELECT, INSERT, UPDATE ON lagerfunn TO disponit_beholdning_eier;
GRANT INSERT ON revisjonslogg TO disponit_beholdning_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_beholdning_eier;
RESET ROLE;


-- ------------------------------------------------------------
-- 3. Dørene. SECURITY DEFINER, eid av `disponit_beholdning_eier`, SP-1.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_beholdning_eier;

CREATE FUNCTION m27_evidens(p_tenant TEXT, p_subjekt_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm27_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm27_lager', 'handling', p_handling,
        'subjekt_id', p_subjekt_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm27_lager',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:lager', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m27_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

CREATE FUNCTION m27_sett_terskler(
    p_tenant TEXT, p_stille_dogn INT, p_uten_punkt_dogn INT,
    p_telleintervall_dogn INT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_ny INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm27_sett_terskler');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.lagerterskel
        (tenant, stille_dogn, uten_punkt_dogn, telleintervall_dogn,
         versjon, oppdatert, oppdatert_av)
    VALUES (p_tenant, p_stille_dogn, p_uten_punkt_dogn,
            p_telleintervall_dogn, 1, now(), p_aktor)
    ON CONFLICT (tenant) DO UPDATE
        SET stille_dogn = excluded.stille_dogn,
            uten_punkt_dogn = excluded.uten_punkt_dogn,
            telleintervall_dogn = excluded.telleintervall_dogn,
            versjon = public.lagerterskel.versjon + 1,
            oppdatert = now(), oppdatert_av = excluded.oppdatert_av
    RETURNING versjon INTO v_ny;
    PERFORM public.m27_evidens(
        p_tenant, NULL, 'lagerterskel.satt', p_aktor,
        jsonb_build_object('versjon', v_ny));
    RETURN v_ny;
END $$;
REVOKE ALL ON FUNCTION m27_sett_terskler(TEXT, INT, INT, INT, TEXT)
    FROM PUBLIC;

CREATE FUNCTION m27_registrer_vare(
    p_tenant TEXT, p_vare_id UUID, p_kode TEXT, p_navn TEXT,
    p_enhet TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm27_registrer_vare');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    INSERT INTO public.vare
        (tenant, vare_id, kode, navn, enhet, opprettet_av)
    VALUES (p_tenant, p_vare_id, btrim(p_kode), btrim(p_navn),
            btrim(p_enhet), p_aktor);
    PERFORM public.m27_evidens(
        p_tenant, p_vare_id, 'vare.registrert', p_aktor,
        jsonb_build_object('kode', btrim(p_kode)));
END $$;
REVOKE ALL ON FUNCTION m27_registrer_vare(TEXT, UUID, TEXT, TEXT, TEXT,
    TEXT) FROM PUBLIC;

-- BESTILLINGSPUNKTDØREN. DOM 4: punktet erstattes, det endres aldri.
CREATE FUNCTION m27_sett_bestillingspunkt(
    p_tenant TEXT, p_vare_id UUID, p_punkt_antall BIGINT,
    p_gyldig_fra DATE, p_begrunnelse TEXT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_forrige INT; v_forrige_fra DATE; v_maks INT; v_ny INT;
        v_finnes BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm27_sett_bestillingspunkt');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_begrunnelse IS NULL OR p_begrunnelse !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm27_sett_bestillingspunkt: et punkt uten'
            ' begrunnelse er en beslutning ingen kan etterprøve — og'
            ' det er punktet som avgjør når noen blir bedt om å bruke'
            ' penger' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT true INTO v_finnes FROM public.vare v
     WHERE v.tenant = p_tenant AND v.vare_id = p_vare_id
       FOR UPDATE;
    IF NOT coalesce(v_finnes, false) THEN
        RAISE EXCEPTION 'm27_sett_bestillingspunkt: varen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    SELECT b.versjon, b.gyldig_fra INTO v_forrige, v_forrige_fra
      FROM public.bestillingspunkt b
     WHERE b.tenant = p_tenant AND b.vare_id = p_vare_id
       AND b.gyldig_til IS NULL
       FOR UPDATE;
    IF v_forrige IS NOT NULL THEN
        -- DEN NYE MÅ BEGYNNE ETTER DEN FORRIGE. Et punkt som skulle
        -- gjelde FØR det gjeldende ville krevd at historikken ble
        -- skrevet om bakover — og da er eldre funn ikke etterprøvbare.
        IF p_gyldig_fra <= v_forrige_fra THEN
            RAISE EXCEPTION 'm27_sett_bestillingspunkt: det nye punktet'
                ' gjelder fra %, men det gjeldende begynte %. Et punkt'
                ' skrives ikke bakover', p_gyldig_fra, v_forrige_fra
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        UPDATE public.bestillingspunkt
           SET gyldig_til = p_gyldig_fra - 1
         WHERE tenant = p_tenant AND vare_id = p_vare_id
           AND versjon = v_forrige;
    END IF;
    SELECT max(b.versjon) INTO v_maks FROM public.bestillingspunkt b
     WHERE b.tenant = p_tenant AND b.vare_id = p_vare_id;
    v_ny := coalesce(v_maks, 0) + 1;
    INSERT INTO public.bestillingspunkt
        (tenant, vare_id, versjon, punkt_antall, gyldig_fra,
         begrunnelse, opprettet_av)
    VALUES (p_tenant, p_vare_id, v_ny, p_punkt_antall, p_gyldig_fra,
            btrim(p_begrunnelse), p_aktor);
    PERFORM public.m27_evidens(
        p_tenant, p_vare_id, 'bestillingspunkt.satt', p_aktor,
        jsonb_build_object('versjon', v_ny, 'antall', p_punkt_antall));
    RETURN v_ny;
END $$;
REVOKE ALL ON FUNCTION m27_sett_bestillingspunkt(TEXT, UUID, BIGINT,
    DATE, TEXT, TEXT) FROM PUBLIC;

-- BEVEGELSESDØREN. DOM 1: dette er den ENESTE veien beholdningen endrer
-- seg, og `p_antall` er en STØRRELSE — fortegnet følger av TYPEN, ett
-- sted, slik at ingen kaller kan snu det.
CREATE FUNCTION m27_registrer_bevegelse(
    p_tenant TEXT, p_bevegelse_id UUID, p_vare_id UUID, p_type TEXT,
    p_antall BIGINT, p_enhetskost_ore BIGINT, p_utfort DATE,
    p_notat TEXT, p_aktor TEXT)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_endring BIGINT; v_aktiv BOOLEAN; v_sum BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm27_registrer_bevegelse');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_antall IS NULL OR p_antall <= 0 THEN
        RAISE EXCEPTION 'm27_registrer_bevegelse: antallet er en'
            ' STØRRELSE og må være positivt — fortegnet følger av typen'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- LUKKET SETT, OG `telling` HØRER IKKE HJEMME HER: en telling er
    -- ikke en bevegelse noen observerte, den er differansen mellom det
    -- talte og det bokførte, og den regnes i sin egen dør.
    v_endring := CASE p_type
        WHEN 'mottak' THEN p_antall
        WHEN 'retur'  THEN p_antall
        WHEN 'uttak'  THEN -p_antall
        WHEN 'svinn'  THEN -p_antall
    END;
    IF v_endring IS NULL THEN
        RAISE EXCEPTION 'm27_registrer_bevegelse: ukjent bevegelsestype'
            ' «%» — lovlige er mottak, retur, uttak, svinn (en telling'
            ' går gjennom m27_registrer_telling)', p_type
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT v.aktiv INTO v_aktiv FROM public.vare v
     WHERE v.tenant = p_tenant AND v.vare_id = p_vare_id
       FOR UPDATE;
    IF v_aktiv IS NULL THEN
        RAISE EXCEPTION 'm27_registrer_bevegelse: varen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF NOT v_aktiv THEN
        RAISE EXCEPTION 'm27_registrer_bevegelse: varen er deaktivert —'
            ' aktiver den før du fører bevegelser på den'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.lagerbevegelse
        (tenant, bevegelse_id, vare_id, bevegelsestype, endring,
         enhetskost_ore, utfort, notat, registrert_av)
    VALUES (p_tenant, p_bevegelse_id, p_vare_id, p_type, v_endring,
            p_enhetskost_ore, p_utfort, btrim(p_notat), p_aktor);
    SELECT coalesce(sum(b.endring), 0) INTO v_sum
      FROM public.lagerbevegelse b
     WHERE b.tenant = p_tenant AND b.vare_id = p_vare_id;
    PERFORM public.m27_evidens(
        p_tenant, p_vare_id, 'bevegelse.registrert', p_aktor,
        jsonb_build_object('type', p_type, 'endring', v_endring,
                           'beholdning', v_sum));
    RETURN v_sum;
END $$;
REVOKE ALL ON FUNCTION m27_registrer_bevegelse(TEXT, UUID, UUID, TEXT,
    BIGINT, BIGINT, DATE, TEXT, TEXT) FROM PUBLIC;

-- TELLEDØREN. En telling SETTER ingen beholdning — den skriver
-- DIFFERANSEN som en linje, og dermed forblir beholdningen summen av
-- bevegelser. En telling som bekreftet tallet gir en linje med endring
-- 0, og den linjen er svaret på «når ble dette sist talt».
CREATE FUNCTION m27_registrer_telling(
    p_tenant TEXT, p_bevegelse_id UUID, p_vare_id UUID,
    p_talt_antall BIGINT, p_utfort DATE, p_notat TEXT, p_aktor TEXT)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_sum BIGINT; v_endring BIGINT; v_finnes BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm27_registrer_telling');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_talt_antall IS NULL OR p_talt_antall < 0 THEN
        RAISE EXCEPTION 'm27_registrer_telling: et talt antall kan ikke'
            ' være negativt' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT true INTO v_finnes FROM public.vare v
     WHERE v.tenant = p_tenant AND v.vare_id = p_vare_id
       FOR UPDATE;
    IF NOT coalesce(v_finnes, false) THEN
        RAISE EXCEPTION 'm27_registrer_telling: varen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    SELECT coalesce(sum(b.endring), 0) INTO v_sum
      FROM public.lagerbevegelse b
     WHERE b.tenant = p_tenant AND b.vare_id = p_vare_id;
    v_endring := p_talt_antall - v_sum;
    INSERT INTO public.lagerbevegelse
        (tenant, bevegelse_id, vare_id, bevegelsestype, endring,
         utfort, notat, registrert_av)
    VALUES (p_tenant, p_bevegelse_id, p_vare_id, 'telling', v_endring,
            p_utfort, btrim(p_notat), p_aktor);
    PERFORM public.m27_evidens(
        p_tenant, p_vare_id, 'telling.registrert', p_aktor,
        jsonb_build_object('talt', p_talt_antall, 'endring', v_endring,
                           'bokfort_for', v_sum));
    RETURN v_endring;
END $$;
REVOKE ALL ON FUNCTION m27_registrer_telling(TEXT, UUID, UUID, BIGINT,
    DATE, TEXT, TEXT) FROM PUBLIC;

CREATE FUNCTION m27_sett_vareaktiv(
    p_tenant TEXT, p_vare_id UUID, p_aktiv BOOLEAN, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_for BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm27_sett_vareaktiv');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT v.aktiv INTO v_for FROM public.vare v
     WHERE v.tenant = p_tenant AND v.vare_id = p_vare_id
       FOR UPDATE;
    IF v_for IS NULL THEN
        RAISE EXCEPTION 'm27_sett_vareaktiv: varen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_for = p_aktiv THEN
        RETURN false;
    END IF;
    UPDATE public.vare SET aktiv = p_aktiv
     WHERE tenant = p_tenant AND vare_id = p_vare_id;
    -- EN DEAKTIVERT VARE HAR INGEN ÅPNE FUNN: sveipen måler bare
    -- aktive varer, og et funn som ble stående ville vært et krav om
    -- handling på noe ingen lenger fører.
    IF NOT p_aktiv THEN
        UPDATE public.lagerfunn
           SET apen = false, lukket_ts = now()
         WHERE tenant = p_tenant AND vare_id = p_vare_id AND apen;
    END IF;
    PERFORM public.m27_evidens(
        p_tenant, p_vare_id, 'vare.aktiv_satt', p_aktor,
        jsonb_build_object('aktiv', p_aktiv));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m27_sett_vareaktiv(TEXT, UUID, BOOLEAN, TEXT)
    FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Lesedørene.
--
--    `m27_beholdning` ER DEN ENESTE VEIEN TIL TALLET. Det finnes ingen
--    kolonne noe sted som holder en beholdning, og det er dom 1.
-- ------------------------------------------------------------

-- BEHOLDNINGEN NÅ. `sum()` over BIGINT gir NUMERIC i PostgreSQL, og
-- castes derfor ved KILDEN — ellers ville tallet reist videre som et
-- flyttallsnært format gjennom hele stakken.
CREATE FUNCTION m27_beholdning(p_tenant TEXT, p_vare_id UUID)
RETURNS BIGINT LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_sum BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm27_beholdning');
    SELECT coalesce(sum(b.endring), 0)::bigint INTO v_sum
      FROM public.lagerbevegelse b
     WHERE b.tenant = p_tenant AND b.vare_id = p_vare_id;
    RETURN v_sum;
END $$;
REVOKE ALL ON FUNCTION m27_beholdning(TEXT, UUID) FROM PUBLIC;

-- BEHOLDNINGEN EN GITT DAG, målt på `utfort` — den datoen noe faktisk
-- skjedde, ikke den datoen noen rakk å skrive det inn.
CREATE FUNCTION m27_beholdning_paa_dato(p_tenant TEXT, p_vare_id UUID,
                                        p_dato DATE)
RETURNS BIGINT LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_sum BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm27_beholdning_paa_dato');
    SELECT coalesce(sum(b.endring), 0)::bigint INTO v_sum
      FROM public.lagerbevegelse b
     WHERE b.tenant = p_tenant AND b.vare_id = p_vare_id
       AND b.utfort <= p_dato;
    RETURN v_sum;
END $$;
REVOKE ALL ON FUNCTION m27_beholdning_paa_dato(TEXT, UUID, DATE)
    FROM PUBLIC;

-- PUNKTET SOM GJALDT DEN DAGEN. Dette er det som gjør et eldre funn
-- etterprøvbart.
CREATE FUNCTION m27_punkt_paa_dato(p_tenant TEXT, p_vare_id UUID,
                                   p_dato DATE)
RETURNS TABLE(versjon INT, punkt_antall BIGINT, gyldig_fra DATE,
              gyldig_til DATE, begrunnelse TEXT, opprettet_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm27_punkt_paa_dato');
    RETURN QUERY
    SELECT b.versjon, b.punkt_antall, b.gyldig_fra, b.gyldig_til,
           b.begrunnelse, b.opprettet_av
      FROM public.bestillingspunkt b
     WHERE b.tenant = p_tenant AND b.vare_id = p_vare_id
       AND b.gyldig_fra <= p_dato
       AND (b.gyldig_til IS NULL OR b.gyldig_til >= p_dato);
END $$;
REVOKE ALL ON FUNCTION m27_punkt_paa_dato(TEXT, UUID, DATE) FROM PUBLIC;

-- ER VI UNDER PUNKTET? HELTALLSSAMMENLIGNING, ingen divisjon.
--
-- INGEN PUNKT DEN DAGEN GIR `NULL`, ikke `false`. «Vi er over
-- bestillingspunktet» om en vare som ikke HAR et punkt ville vært en dom
-- uten grunnlag — og nettopp den dommen er det `uten_bestillingspunkt`
-- finnes for å avsløre.
CREATE FUNCTION m27_under_bestillingspunkt(
    p_tenant TEXT, p_vare_id UUID, p_dato DATE)
RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_punkt BIGINT; v_beholdning BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm27_under_bestillingspunkt');
    SELECT p.punkt_antall INTO v_punkt
      FROM public.m27_punkt_paa_dato(p_tenant, p_vare_id, p_dato) p;
    IF v_punkt IS NULL THEN
        RETURN NULL;
    END IF;
    v_beholdning := public.m27_beholdning_paa_dato(
        p_tenant, p_vare_id, p_dato);
    -- «PÅ ELLER UNDER»: et bestillingspunkt er punktet der noen skal
    -- bestille, ikke punktet der det er for sent.
    RETURN v_beholdning <= v_punkt;
END $$;
REVOKE ALL ON FUNCTION m27_under_bestillingspunkt(TEXT, UUID, DATE)
    FROM PUBLIC;

CREATE FUNCTION m27_lagerstatus(p_tenant TEXT)
RETURNS TABLE(varer INT, aktive INT, med_punkt INT, under_punkt INT,
              apne_funn INT, har_terskel BOOLEAN, terskelversjon INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm27_lagerstatus');
    SELECT count(*)::int,
           count(*) FILTER (WHERE v.aktiv)::int
      INTO varer, aktive
      FROM public.vare v WHERE v.tenant = p_tenant;
    SELECT count(*)::int,
           count(*) FILTER (WHERE public.m27_under_bestillingspunkt(
                p_tenant, v.vare_id, current_date))::int
      INTO med_punkt, under_punkt
      FROM public.vare v
     WHERE v.tenant = p_tenant AND v.aktiv
       AND EXISTS (SELECT 1 FROM public.m27_punkt_paa_dato(
                        p_tenant, v.vare_id, current_date));
    SELECT count(*)::int INTO apne_funn
      FROM public.lagerfunn f WHERE f.tenant = p_tenant AND f.apen;
    SELECT true, t.versjon INTO har_terskel, terskelversjon
      FROM public.lagerterskel t WHERE t.tenant = p_tenant;
    har_terskel := coalesce(har_terskel, false);
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m27_lagerstatus(TEXT) FROM PUBLIC;

CREATE FUNCTION m27_varene(p_tenant TEXT, p_grense INT)
RETURNS TABLE(vare_id UUID, kode TEXT, navn TEXT, enhet TEXT,
              aktiv BOOLEAN, beholdning BIGINT, punkt_antall BIGINT,
              punktversjon INT, dogn_siden_bevegelse INT,
              dogn_siden_telling INT, apne_funn TEXT[])
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm27_varene');
    RETURN QUERY
    SELECT v.vare_id, v.kode, v.navn, v.enhet, v.aktiv,
           public.m27_beholdning(p_tenant, v.vare_id),
           p.punkt_antall, p.versjon,
           (current_date - coalesce(b.siste, v.opprettet::date))::int,
           (current_date - coalesce(tl.siste, v.opprettet::date))::int,
           coalesce(f.typer, ARRAY[]::TEXT[])
      FROM public.vare v
      LEFT JOIN LATERAL public.m27_punkt_paa_dato(
            p_tenant, v.vare_id, current_date) p ON true
      LEFT JOIN LATERAL (
            SELECT max(x.utfort) AS siste FROM public.lagerbevegelse x
             WHERE x.tenant = v.tenant AND x.vare_id = v.vare_id) b
        ON true
      LEFT JOIN LATERAL (
            SELECT max(x.utfort) AS siste FROM public.lagerbevegelse x
             WHERE x.tenant = v.tenant AND x.vare_id = v.vare_id
               AND x.bevegelsestype = 'telling') tl ON true
      LEFT JOIN LATERAL (
            SELECT array_agg(x.funntype ORDER BY x.funntype) AS typer
              FROM public.lagerfunn x
             WHERE x.tenant = v.tenant AND x.vare_id = v.vare_id
               AND x.apen) f ON true
     WHERE v.tenant = p_tenant
     ORDER BY v.aktiv DESC, v.kode
     LIMIT greatest(least(coalesce(p_grense, 500), 5000), 1);
END $$;
REVOKE ALL ON FUNCTION m27_varene(TEXT, INT) FROM PUBLIC;

-- HOVEDBOKEN FOR ÉN VARE. Dette er svaret på «hvorfor står det 7 her».
CREATE FUNCTION m27_bevegelsene(p_tenant TEXT, p_vare_id UUID,
                                p_grense INT)
RETURNS TABLE(bevegelse_id UUID, bevegelsestype TEXT, endring BIGINT,
              enhetskost_ore BIGINT, utfort DATE, notat TEXT,
              registrert TIMESTAMPTZ, registrert_av TEXT,
              beholdning_etter BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm27_bevegelsene');
    RETURN QUERY
    -- `beholdning_etter` regnes som en LØPENDE SUM over hele
    -- historikken, og de nyeste vises først. En leser som måtte
    -- summere selv ville ikke kunne se hvor tallet kom fra.
    SELECT r.bevegelse_id, r.bevegelsestype, r.endring,
           r.enhetskost_ore, r.utfort, r.notat, r.registrert,
           r.registrert_av, r.lopende
      FROM (
        SELECT b.*, sum(b.endring) OVER (
                   ORDER BY b.utfort, b.registrert, b.bevegelse_id
                   ROWS UNBOUNDED PRECEDING)::bigint AS lopende
          FROM public.lagerbevegelse b
         WHERE b.tenant = p_tenant AND b.vare_id = p_vare_id) r
     ORDER BY r.utfort DESC, r.registrert DESC, r.bevegelse_id DESC
     LIMIT greatest(least(coalesce(p_grense, 500), 5000), 1);
END $$;
REVOKE ALL ON FUNCTION m27_bevegelsene(TEXT, UUID, INT) FROM PUBLIC;

CREATE FUNCTION m27_tersklene(p_tenant TEXT)
RETURNS TABLE(stille_dogn INT, uten_punkt_dogn INT,
              telleintervall_dogn INT, versjon INT,
              oppdatert TIMESTAMPTZ, oppdatert_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm27_tersklene');
    RETURN QUERY
    SELECT t.stille_dogn, t.uten_punkt_dogn, t.telleintervall_dogn,
           t.versjon, t.oppdatert, t.oppdatert_av
      FROM public.lagerterskel t WHERE t.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m27_tersklene(TEXT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 4b. Funnkandidatene.
-- ------------------------------------------------------------
CREATE FUNCTION m27_funnkandidater(p_tenant TEXT, p_dag DATE)
RETURNS TABLE(vare_id UUID, funntype TEXT, over_grense BIGINT,
              beholdning BIGINT, punktversjon INT, terskelversjon INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_t RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm27_funnkandidater');
    SELECT * INTO v_t FROM public.lagerterskel t
     WHERE t.tenant = p_tenant;
    IF NOT FOUND THEN
        RETURN QUERY
        SELECT v.vare_id, 'ingen_terskel'::text, NULL::bigint,
               NULL::bigint, NULL::int, NULL::int
          FROM public.vare v
         WHERE v.tenant = p_tenant AND v.aktiv;
        RETURN;
    END IF;

    RETURN QUERY
    -- 1. UNDER BESTILLINGSPUNKTET. Dette er funnet policyen ville latt
    --    `lager.bestill_pafyll` fyre på automatisk. v1 skriver funnet og
    --    lar et menneske bestemme.
    SELECT v.vare_id, 'under_bestillingspunkt'::text,
           (p.punkt_antall
            - public.m27_beholdning_paa_dato(p_tenant, v.vare_id, p_dag)),
           public.m27_beholdning_paa_dato(p_tenant, v.vare_id, p_dag),
           p.versjon, v_t.versjon
      FROM public.vare v
      JOIN LATERAL public.m27_punkt_paa_dato(p_tenant, v.vare_id, p_dag) p
        ON true
     WHERE v.tenant = p_tenant AND v.aktiv
       AND public.m27_beholdning_paa_dato(p_tenant, v.vare_id, p_dag)
           <= p.punkt_antall
    UNION ALL
    -- 2. UTEN BESTILLINGSPUNKT. Uten punktet kan funn 1 aldri bli sant
    --    for varen — og fraværet av funn ser da ut som «alt er i orden».
    --
    --    `FILTER (WHERE b.gyldig_fra <= p_dag)`: bare punkter som HAR
    --    BEGYNT teller (lærdommen fra 108). Uten filteret ville et punkt
    --    som gjelder fra neste år gjort denne porten stille.
    SELECT v.vare_id, 'uten_bestillingspunkt'::text,
           (p_dag - greatest(v.opprettet::date,
                             coalesce(s.siste, v.opprettet::date))
            - v_t.uten_punkt_dogn)::bigint,
           public.m27_beholdning_paa_dato(p_tenant, v.vare_id, p_dag),
           NULL::int, v_t.versjon
      FROM public.vare v
      LEFT JOIN LATERAL (
            SELECT max(coalesce(b.gyldig_til, p_dag))
                       FILTER (WHERE b.gyldig_fra <= p_dag) AS siste
              FROM public.bestillingspunkt b
             WHERE b.tenant = v.tenant AND b.vare_id = v.vare_id) s
        ON true
     WHERE v.tenant = p_tenant AND v.aktiv
       AND NOT EXISTS (SELECT 1 FROM public.m27_punkt_paa_dato(
                            p_tenant, v.vare_id, p_dag))
       AND p_dag - greatest(v.opprettet::date,
                            coalesce(s.siste, v.opprettet::date))
           > v_t.uten_punkt_dogn
    UNION ALL
    -- 3. UTEN BEVEGELSE. Dødt lager er bundet kapital, og ingen ser det
    --    uten at noen sier fra.
    SELECT v.vare_id, 'uten_bevegelse'::text,
           (p_dag - coalesce(b.siste, v.opprettet::date)
            - v_t.stille_dogn)::bigint,
           public.m27_beholdning_paa_dato(p_tenant, v.vare_id, p_dag),
           NULL::int, v_t.versjon
      FROM public.vare v
      LEFT JOIN LATERAL (
            SELECT max(x.utfort) AS siste FROM public.lagerbevegelse x
             WHERE x.tenant = v.tenant AND x.vare_id = v.vare_id
               AND x.utfort <= p_dag) b ON true
     WHERE v.tenant = p_tenant AND v.aktiv
       AND p_dag - coalesce(b.siste, v.opprettet::date)
           > v_t.stille_dogn
    UNION ALL
    -- 4. IKKE TALT. En beholdning ingen har talt er en påstand, ikke en
    --    måling — og `lager_reservert` ville hvilt på påstanden.
    SELECT v.vare_id, 'ikke_talt'::text,
           (p_dag - coalesce(tl.siste, v.opprettet::date)
            - v_t.telleintervall_dogn)::bigint,
           public.m27_beholdning_paa_dato(p_tenant, v.vare_id, p_dag),
           NULL::int, v_t.versjon
      FROM public.vare v
      LEFT JOIN LATERAL (
            SELECT max(x.utfort) AS siste FROM public.lagerbevegelse x
             WHERE x.tenant = v.tenant AND x.vare_id = v.vare_id
               AND x.bevegelsestype = 'telling'
               AND x.utfort <= p_dag) tl ON true
     WHERE v.tenant = p_tenant AND v.aktiv
       AND p_dag - coalesce(tl.siste, v.opprettet::date)
           > v_t.telleintervall_dogn;
END $$;
REVOKE ALL ON FUNCTION m27_funnkandidater(TEXT, DATE) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 4c. Sveipen. KRYSS-TENANT, egen LOGIN-rolle, egen timer.
--
--     SVEIPEN BESTILLER INGENTING OG JUSTERER INGEN BEHOLDNING. Den
--     kunne, teknisk — den vet nøyaktig hvilke varer som er under
--     punktet sitt, og hvor mye som mangler. Men en bestilling binder
--     virksomheten økonomisk, og en jobb som gjorde det om natten ville
--     tatt den beslutningen på ingens vegne. Klyngens dom: her holder vi
--     igjen på å AUTORISERE, ikke bare på å utføre.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_beholdning_eier;

CREATE FUNCTION m27_sveip_lager(p_grense INT DEFAULT 500)
RETURNS TABLE(tenanter INT, nye INT, oppdaterte INT, lukkede INT,
              avkortet INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_n INT; v_grense INT;
        v_dag DATE; v_naa TIMESTAMPTZ;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm27_sveip_lager: sveipen er KRYSS-TENANT og'
            ' kjøres uten tenantkontekst — en kaller som har satt en'
            ' kontekst ber om noe annet enn det denne funksjonen gjør'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_grense := greatest(least(coalesce(p_grense, 500), 5000), 1);
    v_dag := current_date;
    v_naa := now();
    tenanter := 0; nye := 0; oppdaterte := 0; lukkede := 0; avkortet := 0;
    SELECT array_agg(DISTINCT v.tenant ORDER BY v.tenant) INTO v_tenanter
      FROM public.vare v;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        tenanter := tenanter + 1;

        UPDATE public.lagerfunn lf
           SET sist_sett_sveip = v_naa, apen = true, lukket_ts = NULL,
               over_grense = kand.over_grense,
               beholdning = kand.beholdning,
               punktversjon = kand.punktversjon,
               terskelversjon = kand.terskelversjon
          FROM public.m27_funnkandidater(v_t, v_dag) kand
         WHERE lf.tenant = v_t AND lf.vare_id = kand.vare_id
           AND lf.funntype = kand.funntype;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        oppdaterte := oppdaterte + v_n;

        INSERT INTO public.lagerfunn
            (tenant, vare_id, funntype, over_grense, beholdning,
             punktversjon, terskelversjon, forst_sett, sist_sett_sveip,
             apen)
        SELECT v_t, kand.vare_id, kand.funntype, kand.over_grense,
               kand.beholdning, kand.punktversjon, kand.terskelversjon,
               v_naa, v_naa, true
          FROM public.m27_funnkandidater(v_t, v_dag) kand
         WHERE NOT EXISTS (
                SELECT 1 FROM public.lagerfunn lf
                 WHERE lf.tenant = v_t AND lf.vare_id = kand.vare_id
                   AND lf.funntype = kand.funntype)
         ORDER BY coalesce(kand.over_grense, 0) DESC,
                  kand.vare_id, kand.funntype
         LIMIT v_grense;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        nye := nye + v_n;
        IF v_n = v_grense THEN
            avkortet := avkortet + 1;
        END IF;

        UPDATE public.lagerfunn lf
           SET apen = false, lukket_ts = v_naa
         WHERE lf.tenant = v_t AND lf.apen
           AND NOT EXISTS (
                SELECT 1 FROM public.m27_funnkandidater(v_t, v_dag) kand
                 WHERE kand.vare_id = lf.vare_id
                   AND kand.funntype = lf.funntype);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        lukkede := lukkede + v_n;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m27_sveip_lager(INT) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 5. Rettighetene. SP-7: kjøretidsrollen får INGEN tabellrettigheter.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_beholdning_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m27_lagerstatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m27_varene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m27_bevegelsene(TEXT, UUID, INT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m27_tersklene(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m27_beholdning(TEXT, UUID) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m27_beholdning_paa_dato(TEXT, UUID, DATE) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m27_punkt_paa_dato(TEXT, UUID, DATE) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m27_under_bestillingspunkt(TEXT, UUID, DATE) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m27_sett_terskler(TEXT, INT, INT, INT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m27_registrer_vare(TEXT, UUID, TEXT, TEXT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m27_sett_bestillingspunkt(TEXT, UUID, BIGINT, DATE, TEXT,'
            ' TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m27_registrer_bevegelse(TEXT, UUID, UUID, TEXT, BIGINT,'
            ' BIGINT, DATE, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m27_registrer_telling(TEXT, UUID, UUID, BIGINT, DATE,'
            ' TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m27_sett_vareaktiv(TEXT, UUID, BOOLEAN, TEXT)'
            ' TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_lagersveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m27_sveip_lager(INT)'
            ' TO disponit_lagersveip';
    END IF;
END $$;
RESET ROLE;
