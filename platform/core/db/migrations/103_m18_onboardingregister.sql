-- 103: M-18 kunde-onboardingagent v1 — ONBOARDINGREGISTERET.
-- Fem tenant-skopede tabeller, elleve dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- V1-AVGRENSNINGEN, ORDRETT FRA MANIFESTET: katalogteksten lover 0
-- minutter per ny kunde — registrering, betaling, workspace og oppsett
-- maskinelt. v1 PROVISJONERER INGENTING. Den registrerer LØPET: hvilke
-- steg det består av, hvem som eier hvert av dem, når de forfaller, og
-- hvor løpet faktisk står.
--
-- DOMMEN er den samme som gjorde M-12 trygg, og den er ikke
-- forsiktighet: provisjonering er den farlige handlingen, og den er også
-- den som forutsetter at man vet hva et FULLFØRT LØP ER. Uten et målt
-- løp er en automatisk provisjonering en handling ingen kan si om
-- lyktes — og en halvferdig kunde er verre enn en uopprettet.
--
-- DOMMENE v1 HVILER PÅ, håndhevet i DATAMODELLEN og ikke i et API-lag
-- som kunne omgås:
--
--   1. ET LØP ER EN SEKVENS. Et obligatorisk steg kan ikke stå som
--      fullført mens et LAVERE nummerert obligatorisk steg ikke er det.
--      Uten regelen er «hvor står løpet» et spørsmål uten svar: tre av
--      fem steg gjort sier ingenting hvis det er de tre siste.
--
--   2. STEGENE SNAPSHOTTES FRA MALEN VED START. Et løp som endret seg
--      fordi noen redigerte malen etterpå, ville gjort hver eldre
--      statusvurdering til en påstand om noe annet enn det som faktisk
--      gjaldt. Malen er en form; løpet er en hendelse.
--
--   3. ET STEG UTEN EIER ER UREPRESENTERBART (NOT NULL + aktivt
--      medlemskap sjekket i døren). «Hvem gjør dette» er hele
--      spørsmålet et onboardingregister finnes for.
--
--   4. REGISTERET SPEILER IKKE M-12. Et steg kan NEVNE en tilgang i sin
--      egen tekst; det finnes ingen kolonne, ingen fremmednøkkel og
--      ingen dør her som sier noe om hvem som HAR den. To registre som
--      begge påstår å vite hvem som har hva, kan aldri holdes i takt.
--
-- GRENSENE MOT SØSKNENE, sagt eksplisitt: M-12 (097) eier TILGANGENE.
-- M-21 (096) eier PLIKTER — frister mot omverdenen. M-18s stegfrister er
-- INTERNE mål: de brytes uten at noen utenfor har krav på noe, og derfor
-- egen tabell og egne funn.
--
-- SVEIPEN HAR SIN EGEN ROLLE OG SIN EGEN TIMER (095/100/101/102-formen):
-- `disponit_onboardingsveip` har nøyaktig ÉN rettighet i basen — EXECUTE
-- på `m18_sveip_onboarding` — og INGEN tabellrettigheter. Sveipen
-- PROVISJONERER INGENTING og KØER INGEN VARSEL; den skriver FUNN.
--
-- FORMENE ER HUSETS: tabellene eies av migrator, dørene av NOLOGIN-rollen
-- `disponit_onboarding_eier`, tenant TEXT + RLS ENABLE+FORCE +
-- `tenant_isolasjon` på hver tabell, SP-1 (`krev_tenantkontekst` FØRST) i
-- hver tenantbundet definer, og INGEN BYPASSRLS.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_onboarding_eier') THEN
        RAISE EXCEPTION 'rollen disponit_onboarding_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_onboarding_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `onboardingmal` — FORMEN et løp instansieres fra.
--
-- Navnet er `onboardingmal` og ikke `mal`, fordi M-5 (094) eier
-- `dokumentmal`: to tabeller som begge heter noe med «mal» i samme
-- skjema er to ting ingen husker forskjellen på om et halvt år.
CREATE TABLE onboardingmal (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    mal_id UUID NOT NULL,
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    -- VERSJONEN ØKER VED HVER ENDRING av stegene, og løpet snapshotter
    -- den. Uten den kunne ingen si hvilken form et gammelt løp ble
    -- startet på.
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    aktiv BOOLEAN NOT NULL DEFAULT true,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    CONSTRAINT onboardingmal_pk PRIMARY KEY (tenant, mal_id)
);
CREATE UNIQUE INDEX onboardingmal_navn_unik
    ON onboardingmal (tenant, navn);

-- `onboardingmalsteg` — malens steg, i rekkefølge.
--
-- `steg_nr` ER SEKVENSEN. Et eksplisitt `forgjenger`-felt ville tillatt
-- sykler og grener, og ingen av delene har et svar på «hvor står løpet».
-- Ett tall, én rekkefølge, ett svar.
CREATE TABLE onboardingmalsteg (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    mal_id UUID NOT NULL,
    steg_nr INT NOT NULL CHECK (steg_nr >= 1),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    beskrivelse TEXT NOT NULL CHECK (beskrivelse ~ '[^[:space:]]'),
    -- Døgn fra løpets START. En dato ville vært malens dato, ikke
    -- løpets, og malen brukes om igjen.
    frist_dogn INT NOT NULL CHECK (frist_dogn >= 0),
    -- OBLIGATORISK STYRER SEKVENSREGELEN. Et valgfritt steg kan hoppes
    -- over uten at løpet stopper; et obligatorisk kan det ikke, og det
    -- er nettopp derfor skillet finnes.
    obligatorisk BOOLEAN NOT NULL DEFAULT true,
    CONSTRAINT onboardingmalsteg_pk PRIMARY KEY (tenant, mal_id, steg_nr),
    CONSTRAINT onboardingmalsteg_mal_fk FOREIGN KEY (tenant, mal_id)
        REFERENCES onboardingmal (tenant, mal_id)
);

-- `onboardinglop` — ett løp for én ny kunde.
CREATE TABLE onboardinglop (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    lop_id UUID NOT NULL,
    mal_id UUID NOT NULL,
    -- SNAPSHOTTET AV MALVERSJONEN. Endres malen etterpå, står løpet
    -- fortsatt på den formen det ble startet på.
    mal_versjon INT NOT NULL CHECK (mal_versjon >= 1),
    -- Kundens navn eller referanse. FRITEKST og ikke en FK: en
    -- onboarding starter typisk FØR kunden finnes som rad noe sted, og
    -- en fremmednøkkel ville gjort registeret ubrukelig i nøyaktig det
    -- vinduet det er til for.
    kunde_ref TEXT NOT NULL CHECK (kunde_ref ~ '[^[:space:]]'),
    startet DATE NOT NULL,
    eier_bruker_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'paagaar'
        CHECK (status IN ('paagaar', 'fullfort', 'avbrutt')),
    avsluttet_ts TIMESTAMPTZ,
    avsluttet_av TEXT,
    avbrutt_begrunnelse TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    CONSTRAINT onboardinglop_pk PRIMARY KEY (tenant, lop_id),
    CONSTRAINT onboardinglop_mal_fk FOREIGN KEY (tenant, mal_id)
        REFERENCES onboardingmal (tenant, mal_id),
    CONSTRAINT onboardinglop_eier_fk FOREIGN KEY (eier_bruker_id)
        REFERENCES brukeridentitet (bruker_id),
    -- Avslutningens felter står eller faller sammen, og et AVBRUTT løp
    -- koster en begrunnelse: «vi ga opp» uten hvorfor er den ene
    -- opplysningen ingen kan lære noe av senere.
    CONSTRAINT onboardinglop_avslutning_helhet
        CHECK ((status = 'paagaar' AND avsluttet_ts IS NULL
                AND avsluttet_av IS NULL AND avbrutt_begrunnelse IS NULL)
               OR (status = 'fullfort' AND avsluttet_ts IS NOT NULL
                   AND avsluttet_av IS NOT NULL
                   AND avbrutt_begrunnelse IS NULL)
               OR (status = 'avbrutt' AND avsluttet_ts IS NOT NULL
                   AND avsluttet_av IS NOT NULL
                   AND avbrutt_begrunnelse IS NOT NULL
                   AND avbrutt_begrunnelse ~ '[^[:space:]]'))
);
CREATE INDEX onboardinglop_paagaende ON onboardinglop (tenant, startet)
    WHERE status = 'paagaar';

-- `lopsteg` — løpets EGNE steg, snapshottet fra malen ved start.
--
-- DOM 2 I TABELLFORM: `navn`, `beskrivelse`, `frist_dogn` og
-- `obligatorisk` er KOPIER, ikke pekere. Et løp som endret seg fordi
-- noen redigerte malen etterpå, ville gjort hver eldre statusvurdering
-- til en påstand om noe annet enn det som faktisk gjaldt.
CREATE TABLE lopsteg (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    lop_id UUID NOT NULL,
    steg_nr INT NOT NULL CHECK (steg_nr >= 1),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    beskrivelse TEXT NOT NULL CHECK (beskrivelse ~ '[^[:space:]]'),
    frist_dogn INT NOT NULL CHECK (frist_dogn >= 0),
    obligatorisk BOOLEAN NOT NULL,
    -- DOM 3: hvert steg har sin EGEN eier. Løpets eier er den som svarer
    -- for helheten; stegets eier er den som faktisk gjør det. Uten
    -- skillet blir «hvem gjør dette» ett navn på fem oppgaver.
    eier_bruker_id TEXT NOT NULL,
    fullfort_ts TIMESTAMPTZ,
    fullfort_av TEXT,
    notat TEXT,
    CONSTRAINT lopsteg_pk PRIMARY KEY (tenant, lop_id, steg_nr),
    CONSTRAINT lopsteg_lop_fk FOREIGN KEY (tenant, lop_id)
        REFERENCES onboardinglop (tenant, lop_id),
    CONSTRAINT lopsteg_eier_fk FOREIGN KEY (eier_bruker_id)
        REFERENCES brukeridentitet (bruker_id),
    CONSTRAINT lopsteg_fullforing_helhet
        CHECK ((fullfort_ts IS NULL AND fullfort_av IS NULL)
               OR (fullfort_ts IS NOT NULL AND fullfort_av IS NOT NULL))
);
CREATE INDEX lopsteg_apne ON lopsteg (tenant, lop_id)
    WHERE fullfort_ts IS NULL;

-- `onboardingfunn` — sveipens dom. Samme form som 100/101/102.
--
-- `steg_nr = 0` betyr «gjelder LØPET, ikke et enkelt steg». Null er
-- ikke et lovlig stegnummer (CHECK-en over krever >= 1), så verdien kan
-- ikke forveksles med et ekte steg — og PRIMÆRNØKKELEN slipper en
-- nullbar kolonne, som ville gjort idempotensen avhengig av
-- NULL-sammenligning.
CREATE TABLE onboardingfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    lop_id UUID NOT NULL,
    steg_nr INT NOT NULL CHECK (steg_nr >= 0),
    funntype TEXT NOT NULL CHECK (funntype IN (
        'stoppet_lop',
        'steg_over_frist',
        'lop_uten_aktiv_eier')),
    dogn_over_grense INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT onboardingfunn_pk
        PRIMARY KEY (tenant, lop_id, steg_nr, funntype),
    CONSTRAINT onboardingfunn_lop_fk FOREIGN KEY (tenant, lop_id)
        REFERENCES onboardinglop (tenant, lop_id),
    CONSTRAINT onboardingfunn_lukking
        CHECK ((apen AND lukket_ts IS NULL)
               OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE INDEX onboardingfunn_apne ON onboardingfunn (tenant, funntype)
    WHERE apen;


-- ------------------------------------------------------------
-- 2. Radvaktene. CHECK-ene over gjelder én rad; vaktene gjelder
--    FORHOLDET mellom rader — og de gjelder enhver skrivevei.
-- ------------------------------------------------------------

-- MALEN KAN REDIGERES, men bare mens den ikke er i bruk i et PÅGÅENDE
-- løp — og versjonen må øke når stegene endres. Uten det ville et løp
-- startet på versjon 3 kunne peke på en mal som nå er noe annet, og
-- snapshotet i §1 hadde vært det eneste stedet sannheten fantes.
CREATE FUNCTION m18_malsteg_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_rad RECORD;
BEGIN
    v_rad := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    IF EXISTS (SELECT 1 FROM public.onboardinglop l
                WHERE l.tenant = v_rad.tenant AND l.mal_id = v_rad.mal_id
                  AND l.status = 'paagaar') THEN
        RAISE EXCEPTION 'onboardingmalsteg: malen har pågående løp —'
            ' endre den ikke under føttene på dem. Lag en ny mal, eller'
            ' vent til løpene er avsluttet'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN v_rad;
END $$;
REVOKE ALL ON FUNCTION m18_malsteg_vakt() FROM PUBLIC;
CREATE TRIGGER m18_malsteg_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON onboardingmalsteg
    FOR EACH ROW EXECUTE FUNCTION m18_malsteg_vakt();
CREATE TRIGGER m18_malsteg_ingen_truncate
    BEFORE TRUNCATE ON onboardingmalsteg
    EXECUTE FUNCTION m18_malsteg_vakt();

-- LØPETS VAKT. Identiteten og snapshotet er frosset; statusen går ÉN
-- vei, og avslutningen krever en navngitt aktør.
CREATE FUNCTION m18_lop_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT; v_ufullforte INT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'onboardinglop: DELETE avvist — at et løp fantes'
            ' er også historikk, også et som ble avbrutt'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.lop_id IS DISTINCT FROM OLD.lop_id
       OR NEW.mal_id IS DISTINCT FROM OLD.mal_id
       OR NEW.mal_versjon IS DISTINCT FROM OLD.mal_versjon
       OR NEW.kunde_ref IS DISTINCT FROM OLD.kunde_ref
       OR NEW.startet IS DISTINCT FROM OLD.startet
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'onboardinglop: identiteten og malsnapshotet er'
            ' frosset — et løp på en annen form er et annet løp'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status <> 'paagaar' AND NEW.status IS DISTINCT FROM OLD.status
       THEN
        RAISE EXCEPTION 'onboardinglop: et avsluttet løp gjenåpnes ikke —'
            ' en ny runde er et nytt løp'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status <> OLD.status THEN
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL OR NEW.avsluttet_av IS DISTINCT FROM v_aktor THEN
            RAISE EXCEPTION 'onboardinglop: avsluttet_av (%) er ikke'
                ' aktøren som avslutter (%) — tiden fullfører intet løp',
                coalesce(NEW.avsluttet_av, '<null>'),
                coalesce(v_aktor, '<ingen>')
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- «FULLFØRT» KREVER AT DE OBLIGATORISKE STEGENE FAKTISK ER GJORT.
        -- Uten kravet ville ordet vært noe man kunne klikke, og
        -- registeret ville hatt en tilstand det ikke kan vise noe bak.
        IF NEW.status = 'fullfort' THEN
            SELECT count(*) INTO v_ufullforte FROM public.lopsteg s
             WHERE s.tenant = NEW.tenant AND s.lop_id = NEW.lop_id
               AND s.obligatorisk AND s.fullfort_ts IS NULL;
            IF v_ufullforte > 0 THEN
                RAISE EXCEPTION 'onboardinglop: % obligatoriske steg står'
                    ' ufullførte — «fullført» er da en påstand ingen kan'
                    ' vise noe bak', v_ufullforte
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m18_lop_vakt() FROM PUBLIC;
CREATE TRIGGER m18_lop_vakt
    BEFORE UPDATE OR DELETE ON onboardinglop
    FOR EACH ROW EXECUTE FUNCTION m18_lop_vakt();
CREATE TRIGGER m18_lop_ingen_truncate
    BEFORE TRUNCATE ON onboardinglop
    EXECUTE FUNCTION m18_lop_vakt();

-- DOM 1, i vaktform: ET LØP ER EN SEKVENS.
--
-- Et obligatorisk steg kan ikke stå som fullført mens et LAVERE
-- nummerert obligatorisk steg ikke er det. Uten regelen er «hvor står
-- løpet» et spørsmål uten svar: tre av fem steg gjort sier ingenting
-- hvis det er de tre siste.
--
-- VALGFRIE STEG ER UNNTATT I BEGGE RETNINGER: de kan gjøres når som
-- helst, og de blokkerer ingen. Det er hele grunnen til at `obligatorisk`
-- finnes som eget felt og ikke som en konvensjon om nummerering.
--
-- SNAPSHOTET ER OGSÅ FROSSET: navn, frist og obligatorisk-flagget kan
-- ikke endres etter at løpet er startet, ellers ville sekvensregelen
-- kunne omgås ved å gjøre et blokkerende steg valgfritt i etterkant.
CREATE FUNCTION m18_steg_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT; v_forrige INT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'lopsteg: DELETE avvist — et steg som ble hoppet'
            ' over er også historikk'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.tenant IS DISTINCT FROM OLD.tenant
           OR NEW.lop_id IS DISTINCT FROM OLD.lop_id
           OR NEW.steg_nr IS DISTINCT FROM OLD.steg_nr
           OR NEW.navn IS DISTINCT FROM OLD.navn
           OR NEW.beskrivelse IS DISTINCT FROM OLD.beskrivelse
           OR NEW.frist_dogn IS DISTINCT FROM OLD.frist_dogn
           OR NEW.obligatorisk IS DISTINCT FROM OLD.obligatorisk THEN
            RAISE EXCEPTION 'lopsteg: snapshotet er frosset — et steg som'
                ' kunne gjøres valgfritt i etterkant, ville gjort'
                ' sekvensregelen til noe man kan klikke seg forbi'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF OLD.fullfort_ts IS NOT NULL
           AND (NEW.fullfort_ts IS DISTINCT FROM OLD.fullfort_ts
                OR NEW.fullfort_av IS DISTINCT FROM OLD.fullfort_av) THEN
            RAISE EXCEPTION 'lopsteg: et fullført steg gjøres ikke ugjort'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    IF NEW.fullfort_ts IS NOT NULL
       AND (TG_OP = 'INSERT' OR OLD.fullfort_ts IS NULL) THEN
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL OR NEW.fullfort_av IS DISTINCT FROM v_aktor THEN
            RAISE EXCEPTION 'lopsteg: fullfort_av (%) er ikke aktøren som'
                ' fullfører (%) — et steg fullfører ikke seg selv',
                coalesce(NEW.fullfort_av, '<null>'),
                coalesce(v_aktor, '<ingen>')
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.obligatorisk THEN
            SELECT min(s.steg_nr) INTO v_forrige FROM public.lopsteg s
             WHERE s.tenant = NEW.tenant AND s.lop_id = NEW.lop_id
               AND s.steg_nr < NEW.steg_nr
               AND s.obligatorisk AND s.fullfort_ts IS NULL;
            IF v_forrige IS NOT NULL THEN
                RAISE EXCEPTION 'lopsteg: steg % er obligatorisk og ikke'
                    ' fullført — steg % kan ikke stå som gjort før det',
                    v_forrige, NEW.steg_nr
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m18_steg_vakt() FROM PUBLIC;
CREATE TRIGGER m18_steg_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON lopsteg
    FOR EACH ROW EXECUTE FUNCTION m18_steg_vakt();
CREATE TRIGGER m18_steg_ingen_truncate
    BEFORE TRUNCATE ON lopsteg
    EXECUTE FUNCTION m18_steg_vakt();

-- Funnene: samme form som 100/101/102.
CREATE FUNCTION m18_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'onboardingfunn: DELETE avvist — et funn lukkes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.tenant IS DISTINCT FROM OLD.tenant
           OR NEW.lop_id IS DISTINCT FROM OLD.lop_id
           OR NEW.steg_nr IS DISTINCT FROM OLD.steg_nr
           OR NEW.funntype IS DISTINCT FROM OLD.funntype
           OR NEW.forst_sett IS DISTINCT FROM OLD.forst_sett THEN
            RAISE EXCEPTION 'onboardingfunn: identiteten og førstegangen'
                ' er frosset' USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m18_funn_vakt() FROM PUBLIC;
CREATE TRIGGER m18_funn_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON onboardingfunn
    FOR EACH ROW EXECUTE FUNCTION m18_funn_vakt();
CREATE TRIGGER m18_funn_ingen_truncate
    BEFORE TRUNCATE ON onboardingfunn
    EXECUTE FUNCTION m18_funn_vakt();


-- ------------------------------------------------------------
-- 2b. RLS og rettigheter.
-- ------------------------------------------------------------
ALTER TABLE onboardingmal ENABLE ROW LEVEL SECURITY;
ALTER TABLE onboardingmal FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON onboardingmal
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE onboardingmalsteg ENABLE ROW LEVEL SECURITY;
ALTER TABLE onboardingmalsteg FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON onboardingmalsteg
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE onboardinglop ENABLE ROW LEVEL SECURITY;
ALTER TABLE onboardinglop FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON onboardinglop
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — ingen BYPASSRLS.
-- Tre gjerder, som i 100/101/102: bare dørenes eier, bare SELECT, bare
-- uten tenantkontekst i sesjonen. De to policyene er disjunkte per
-- konstruksjon, siden hver dør går gjennom `krev_tenantkontekst`.
CREATE POLICY m18_sveip_tenantliste ON onboardinglop
    FOR SELECT TO disponit_onboarding_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

ALTER TABLE lopsteg ENABLE ROW LEVEL SECURITY;
ALTER TABLE lopsteg FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON lopsteg
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE onboardingfunn ENABLE ROW LEVEL SECURITY;
ALTER TABLE onboardingfunn FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON onboardingfunn
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- Rettighetene dørenes eier trenger, og ikke mer. Ingen runtime-rolle
-- får en eneste tabellrettighet på de fem tabellene (SP-7).
GRANT SELECT, INSERT, UPDATE ON onboardingmal TO disponit_onboarding_eier;
GRANT SELECT, INSERT, UPDATE, DELETE ON onboardingmalsteg
    TO disponit_onboarding_eier;
GRANT SELECT, INSERT, UPDATE ON onboardinglop TO disponit_onboarding_eier;
GRANT SELECT, INSERT, UPDATE ON lopsteg TO disponit_onboarding_eier;
GRANT SELECT, INSERT, UPDATE ON onboardingfunn
    TO disponit_onboarding_eier;
-- MALSTEGENE ER DEN ENESTE TABELLEN MED DELETE, og det er en avgrenset
-- fullmakt: en mal REDIGERES ved at stegene skrives om, og vakten i §2
-- nekter enhver endring mens malen har pågående løp. Løpenes egne steg
-- (`lopsteg`) kan aldri slettes.
GRANT INSERT ON revisjonslogg TO disponit_onboarding_eier;
GRANT SELECT ON brukermedlemskap TO disponit_onboarding_eier;
GRANT SELECT (bruker_id, profil) ON brukeridentitet
    TO disponit_onboarding_eier;
GRANT REFERENCES ON brukeridentitet TO disponit_onboarding_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_onboarding_eier;
RESET ROLE;


-- ------------------------------------------------------------
-- 3. Dørene. SECURITY DEFINER, eid av `disponit_onboarding_eier`, og
--    hver tenantbundet dør kaller `krev_tenantkontekst` FØRST (SP-1).
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_onboarding_eier;

CREATE FUNCTION m18_evidens(p_tenant TEXT, p_lop_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm18_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm18_onboarding', 'handling', p_handling,
        'lop_id', p_lop_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm18_onboarding',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:onboardingregister', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m18_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- MALDØREN. SP-2-materialitet på `p_mal_id`.
CREATE FUNCTION m18_registrer_mal(
    p_tenant TEXT, p_mal_id UUID, p_navn TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_gammel RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm18_registrer_mal');
    IF p_navn IS NULL OR p_navn !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm18_registrer_mal: malen må ha et navn'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.onboardingmal (tenant, mal_id, navn, opprettet_av)
    VALUES (p_tenant, p_mal_id, btrim(p_navn), p_aktor)
        ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        SELECT * INTO v_gammel FROM public.onboardingmal
         WHERE tenant = p_tenant AND mal_id = p_mal_id;
        IF v_gammel IS NULL OR v_gammel.navn IS DISTINCT FROM btrim(p_navn)
           THEN
            RAISE EXCEPTION 'm18_registrer_mal: navnet er i bruk, eller'
                ' samme mal_id med et annet navn — materiell konflikt'
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN false;
    END IF;
    PERFORM public.m18_evidens(
        p_tenant, p_mal_id, 'mal.registrert', p_aktor,
        jsonb_build_object());
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m18_registrer_mal(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

-- STEGDØREN FOR MALEN. Setter HELE stegsettet i én transaksjon og øker
-- versjonen — en dør som la til ett steg om gangen ville latt malen stå
-- i en halvferdig tilstand mellom kallene, og et løp startet i det
-- vinduet ville fått et ufullstendig snapshot.
--
-- STEGNUMRENE MÅ VÆRE 1..N UTEN HULL. Et hull ville gjort «steg 3 av 5»
-- til en påstand ingen kan telle seg til, og sekvensregelen i §2 leser
-- «alle lavere obligatoriske» — som er noe annet enn «det forrige» hvis
-- nummereringen er vilkårlig.
CREATE FUNCTION m18_sett_malsteg(
    p_tenant TEXT, p_mal_id UUID, p_steg JSONB, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_antall INT; v_i INT; v_s JSONB; v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm18_sett_malsteg');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF NOT EXISTS (SELECT 1 FROM public.onboardingmal m
                    WHERE m.tenant = p_tenant AND m.mal_id = p_mal_id) THEN
        RAISE EXCEPTION 'm18_sett_malsteg: malen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF jsonb_typeof(p_steg) <> 'array' THEN
        RAISE EXCEPTION 'm18_sett_malsteg: stegene må være en liste'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_antall := jsonb_array_length(p_steg);
    IF v_antall < 1 THEN
        RAISE EXCEPTION 'm18_sett_malsteg: en mal uten steg er ingen mal'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    DELETE FROM public.onboardingmalsteg
     WHERE tenant = p_tenant AND mal_id = p_mal_id;
    FOR v_i IN 0 .. v_antall - 1 LOOP
        v_s := p_steg -> v_i;
        INSERT INTO public.onboardingmalsteg
            (tenant, mal_id, steg_nr, navn, beskrivelse, frist_dogn,
             obligatorisk)
        VALUES (p_tenant, p_mal_id, v_i + 1,
                v_s ->> 'navn', v_s ->> 'beskrivelse',
                (v_s ->> 'frist_dogn')::int,
                coalesce((v_s ->> 'obligatorisk')::boolean, true));
    END LOOP;
    UPDATE public.onboardingmal SET versjon = versjon + 1
     WHERE tenant = p_tenant AND mal_id = p_mal_id
     RETURNING versjon INTO v_versjon;
    PERFORM public.m18_evidens(
        p_tenant, p_mal_id, 'mal.steg_satt', p_aktor,
        jsonb_build_object('antall', v_antall, 'versjon', v_versjon));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION m18_sett_malsteg(TEXT, UUID, JSONB, TEXT)
    FROM PUBLIC;

-- STARTDØREN. SNAPSHOTTER malens steg inn i løpet (dom 2).
--
-- EIEREN MÅ VÆRE AKTIVT MEDLEM — både løpets og hvert stegs. Et løp eid
-- av en fremmed tenants bruker er et løp ingen her gjør.
--
-- STEGEIERNE ARVES FRA LØPETS EIER ved start, og flyttes deretter ett og
-- ett. Alternativet — å kreve alle eierne opp front — ville gjort det
-- umulig å starte et løp før man visste hvem som skulle gjøre steg fem,
-- og da ville registeret ikke blitt brukt i vinduet det er til for.
CREATE FUNCTION m18_start_lop(
    p_tenant TEXT, p_lop_id UUID, p_mal_id UUID, p_kunde_ref TEXT,
    p_eier_bruker_id TEXT, p_startet DATE, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_gammel RECORD; v_versjon INT; v_antall INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm18_start_lop');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_kunde_ref IS NULL OR p_kunde_ref !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm18_start_lop: kundereferansen kan ikke være tom'
            ' — et løp ingen kan si hvem gjelder, er ingen onboarding'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.brukermedlemskap bm
                    WHERE bm.tenant = p_tenant
                      AND bm.bruker_id = p_eier_bruker_id AND bm.aktiv) THEN
        RAISE EXCEPTION 'm18_start_lop: % er ikke et aktivt medlem av'
            ' tenanten — et løp uten eier her er et løp ingen gjør',
            coalesce(p_eier_bruker_id, '<null>')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT m.versjon INTO v_versjon FROM public.onboardingmal m
     WHERE m.tenant = p_tenant AND m.mal_id = p_mal_id AND m.aktiv;
    IF v_versjon IS NULL THEN
        RAISE EXCEPTION 'm18_start_lop: malen finnes ikke eller er'
            ' deaktivert' USING ERRCODE = 'foreign_key_violation';
    END IF;
    SELECT count(*) INTO v_antall FROM public.onboardingmalsteg s
     WHERE s.tenant = p_tenant AND s.mal_id = p_mal_id;
    IF v_antall = 0 THEN
        RAISE EXCEPTION 'm18_start_lop: malen har ingen steg — et løp uten'
            ' steg er et løp uten status'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.onboardinglop
        (tenant, lop_id, mal_id, mal_versjon, kunde_ref, startet,
         eier_bruker_id, opprettet_av)
    VALUES (p_tenant, p_lop_id, p_mal_id, v_versjon, btrim(p_kunde_ref),
            p_startet, p_eier_bruker_id, p_aktor)
        ON CONFLICT (tenant, lop_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        SELECT * INTO v_gammel FROM public.onboardinglop
         WHERE tenant = p_tenant AND lop_id = p_lop_id;
        IF v_gammel.mal_id IS DISTINCT FROM p_mal_id
           OR v_gammel.kunde_ref IS DISTINCT FROM btrim(p_kunde_ref)
           OR v_gammel.eier_bruker_id IS DISTINCT FROM p_eier_bruker_id THEN
            RAISE EXCEPTION 'm18_start_lop: samme lop_id med annet innhold'
                ' — materiell konflikt'
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN false;
    END IF;
    -- SNAPSHOTET. Kopier, ikke pekere.
    INSERT INTO public.lopsteg
        (tenant, lop_id, steg_nr, navn, beskrivelse, frist_dogn,
         obligatorisk, eier_bruker_id)
    SELECT p_tenant, p_lop_id, s.steg_nr, s.navn, s.beskrivelse,
           s.frist_dogn, s.obligatorisk, p_eier_bruker_id
      FROM public.onboardingmalsteg s
     WHERE s.tenant = p_tenant AND s.mal_id = p_mal_id;
    PERFORM public.m18_evidens(
        p_tenant, p_lop_id, 'lop.startet', p_aktor,
        jsonb_build_object('mal_id', p_mal_id::text,
                           'mal_versjon', v_versjon, 'steg', v_antall));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m18_start_lop(TEXT, UUID, UUID, TEXT, TEXT, DATE,
                                     TEXT) FROM PUBLIC;

-- STEGEIERDØREN. Det ENE som lovlig flyttes på et steg utenom
-- fullføringen: hvem som skal gjøre det.
CREATE FUNCTION m18_sett_stegeier(
    p_tenant TEXT, p_lop_id UUID, p_steg_nr INT, p_eier_bruker_id TEXT,
    p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm18_sett_stegeier');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF NOT EXISTS (SELECT 1 FROM public.brukermedlemskap bm
                    WHERE bm.tenant = p_tenant
                      AND bm.bruker_id = p_eier_bruker_id AND bm.aktiv) THEN
        RAISE EXCEPTION 'm18_sett_stegeier: % er ikke et aktivt medlem av'
            ' tenanten', coalesce(p_eier_bruker_id, '<null>')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.lopsteg SET eier_bruker_id = p_eier_bruker_id
     WHERE tenant = p_tenant AND lop_id = p_lop_id
       AND steg_nr = p_steg_nr AND fullfort_ts IS NULL;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RAISE EXCEPTION 'm18_sett_stegeier: steget finnes ikke, eller er'
            ' alt fullført — en eier på et gjort steg endrer ingenting'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM public.m18_evidens(
        p_tenant, p_lop_id, 'steg.eier_satt', p_aktor,
        jsonb_build_object('steg_nr', p_steg_nr,
                           'eier_bruker_id', p_eier_bruker_id));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m18_sett_stegeier(TEXT, UUID, INT, TEXT, TEXT)
    FROM PUBLIC;

-- FULLFØRINGSDØREN. Sekvensregelen håndheves av VAKTEN; døren sier det
-- bare med en lesbar setning først, og bare der den kan.
CREATE FUNCTION m18_fullfor_steg(
    p_tenant TEXT, p_lop_id UUID, p_steg_nr INT, p_notat TEXT,
    p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_status TEXT; v_alt TIMESTAMPTZ;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm18_fullfor_steg');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT l.status INTO v_status FROM public.onboardinglop l
     WHERE l.tenant = p_tenant AND l.lop_id = p_lop_id;
    IF v_status IS NULL THEN
        RAISE EXCEPTION 'm18_fullfor_steg: løpet finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_status <> 'paagaar' THEN
        RAISE EXCEPTION 'm18_fullfor_steg: løpet er % — et avsluttet løp'
            ' tar ikke imot flere steg', v_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT s.fullfort_ts INTO v_alt FROM public.lopsteg s
     WHERE s.tenant = p_tenant AND s.lop_id = p_lop_id
       AND s.steg_nr = p_steg_nr;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm18_fullfor_steg: steget finnes ikke i dette løpet'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    -- ALT FULLFØRT er et STILLE JA: to klikk på den samme knappen er
    -- ikke en feil.
    IF v_alt IS NOT NULL THEN
        RETURN false;
    END IF;
    UPDATE public.lopsteg
       SET fullfort_ts = now(), fullfort_av = p_aktor,
           notat = nullif(btrim(coalesce(p_notat, '')), '')
     WHERE tenant = p_tenant AND lop_id = p_lop_id
       AND steg_nr = p_steg_nr AND fullfort_ts IS NULL;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;
    END IF;
    PERFORM public.m18_evidens(
        p_tenant, p_lop_id, 'steg.fullfort', p_aktor,
        jsonb_build_object('steg_nr', p_steg_nr));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m18_fullfor_steg(TEXT, UUID, INT, TEXT, TEXT)
    FROM PUBLIC;

-- AVSLUTNINGSDØREN. `fullfort` krever at de obligatoriske stegene er
-- gjort (vakten); `avbrutt` krever en begrunnelse (CHECK-en).
CREATE FUNCTION m18_avslutt_lop(
    p_tenant TEXT, p_lop_id UUID, p_status TEXT, p_begrunnelse TEXT,
    p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_gammel TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm18_avslutt_lop');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_status IS NULL OR p_status NOT IN ('fullfort', 'avbrutt') THEN
        RAISE EXCEPTION 'm18_avslutt_lop: status må være fullfort eller'
            ' avbrutt' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_status = 'avbrutt'
       AND (p_begrunnelse IS NULL OR p_begrunnelse !~ '[^[:space:]]') THEN
        RAISE EXCEPTION 'm18_avslutt_lop: et avbrutt løp koster en'
            ' begrunnelse — «vi ga opp» uten hvorfor er den ene'
            ' opplysningen ingen kan lære noe av senere'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT l.status INTO v_gammel FROM public.onboardinglop l
     WHERE l.tenant = p_tenant AND l.lop_id = p_lop_id;
    IF v_gammel IS NULL THEN
        RAISE EXCEPTION 'm18_avslutt_lop: løpet finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_gammel <> 'paagaar' THEN
        RETURN false;                              -- stille ja
    END IF;
    UPDATE public.onboardinglop
       SET status = p_status, avsluttet_ts = now(), avsluttet_av = p_aktor,
           avbrutt_begrunnelse = CASE WHEN p_status = 'avbrutt'
                                      THEN btrim(p_begrunnelse) END
     WHERE tenant = p_tenant AND lop_id = p_lop_id
       AND status = 'paagaar';
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;
    END IF;
    PERFORM public.m18_evidens(
        p_tenant, p_lop_id, 'lop.' || p_status, p_aktor,
        jsonb_build_object());
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m18_avslutt_lop(TEXT, UUID, TEXT, TEXT, TEXT)
    FROM PUBLIC;


-- ------------------------------------------------------------
-- 3b. Lesedørene.
-- ------------------------------------------------------------

-- SAMMENDRAGET TELLER ALT, listen viser de N eldste (100/101/102s dom).
CREATE FUNCTION m18_onboardingstatus(p_tenant TEXT)
RETURNS TABLE(paagaende INT, fullforte INT, avbrutte INT,
              stoppede INT, apne_funn INT, maler INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm18_onboardingstatus');
    SELECT count(*) FILTER (WHERE l.status = 'paagaar')::int,
           count(*) FILTER (WHERE l.status = 'fullfort')::int,
           count(*) FILTER (WHERE l.status = 'avbrutt')::int,
           0, 0,
           (SELECT count(*)::int FROM public.onboardingmal m
             WHERE m.tenant = p_tenant AND m.aktiv)
      INTO paagaende, fullforte, avbrutte, stoppede, apne_funn, maler
      FROM public.onboardinglop l WHERE l.tenant = p_tenant;
    SELECT count(*)::int,
           count(DISTINCT f.lop_id) FILTER (
               WHERE f.funntype = 'stoppet_lop')::int
      INTO apne_funn, stoppede
      FROM public.onboardingfunn f
     WHERE f.tenant = p_tenant AND f.apen;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m18_onboardingstatus(TEXT) FROM PUBLIC;

-- LØPENE, eldst først blant de pågående. `gjort`/`totalt` regnes i
-- BASEN — flaten skal ikke telle en liste den ikke har.
CREATE FUNCTION m18_lopene(p_tenant TEXT, p_grense INT)
RETURNS TABLE(lop_id UUID, kunde_ref TEXT, mal_navn TEXT,
              mal_versjon INT, startet DATE, status TEXT,
              eier_bruker_id TEXT, eier_navn TEXT, eier_aktiv BOOLEAN,
              alder_dogn INT, gjort INT, totalt INT,
              obligatoriske_igjen INT, neste_steg TEXT,
              apne_funn TEXT[])
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm18_lopene');
    RETURN QUERY
    SELECT l.lop_id, l.kunde_ref, m.navn, l.mal_versjon, l.startet,
           l.status, l.eier_bruker_id,
           nullif(btrim(coalesce(b.profil->>'visningsnavn', '')), ''),
           -- EIEREN SLIK DEN ER I DAG. Sveipen reiser funnet, men flaten
           -- skal ikke vente på nattens kjøring for å kunne si at eieren
           -- har sluttet (100s form).
           EXISTS (SELECT 1 FROM public.brukermedlemskap bm
                    WHERE bm.tenant = l.tenant
                      AND bm.bruker_id = l.eier_bruker_id AND bm.aktiv),
           (current_date - l.startet)::int,
           (SELECT count(*)::int FROM public.lopsteg s
             WHERE s.tenant = l.tenant AND s.lop_id = l.lop_id
               AND s.fullfort_ts IS NOT NULL),
           (SELECT count(*)::int FROM public.lopsteg s
             WHERE s.tenant = l.tenant AND s.lop_id = l.lop_id),
           (SELECT count(*)::int FROM public.lopsteg s
             WHERE s.tenant = l.tenant AND s.lop_id = l.lop_id
               AND s.obligatorisk AND s.fullfort_ts IS NULL),
           -- NESTE STEG er det laveste ufullførte. Det er svaret på
           -- «hva venter vi på», og det er ett oppslag i basen framfor
           -- en løkke i flaten.
           (SELECT s.navn FROM public.lopsteg s
             WHERE s.tenant = l.tenant AND s.lop_id = l.lop_id
               AND s.fullfort_ts IS NULL
             ORDER BY s.steg_nr LIMIT 1),
           coalesce((SELECT array_agg(DISTINCT f.funntype)
                       FROM public.onboardingfunn f
                      WHERE f.tenant = l.tenant AND f.lop_id = l.lop_id
                        AND f.apen), ARRAY[]::TEXT[])
      FROM public.onboardinglop l
      JOIN public.onboardingmal m
        ON m.tenant = l.tenant AND m.mal_id = l.mal_id
      LEFT JOIN public.brukeridentitet b
        ON b.bruker_id = l.eier_bruker_id
     WHERE l.tenant = p_tenant
     -- Pågående først, deretter eldst — det er rekkefølgen et menneske
     -- skal jobbe i. `lop_id` som tiebreaker (100s bitmap-lærdom).
     ORDER BY (l.status <> 'paagaar'), l.startet, l.lop_id
     LIMIT greatest(least(coalesce(p_grense, 100), 1000), 1);
END $$;
REVOKE ALL ON FUNCTION m18_lopene(TEXT, INT) FROM PUBLIC;

-- ETT LØPS STEG, i rekkefølge.
CREATE FUNCTION m18_stegene(p_tenant TEXT, p_lop_id UUID)
RETURNS TABLE(steg_nr INT, navn TEXT, beskrivelse TEXT, frist_dogn INT,
              obligatorisk BOOLEAN, eier_bruker_id TEXT, eier_navn TEXT,
              fullfort_ts TIMESTAMPTZ, fullfort_av TEXT, notat TEXT,
              forfaller DATE, dogn_over_frist INT, blokkert BOOLEAN)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm18_stegene');
    RETURN QUERY
    SELECT s.steg_nr, s.navn, s.beskrivelse, s.frist_dogn,
           s.obligatorisk, s.eier_bruker_id,
           nullif(btrim(coalesce(b.profil->>'visningsnavn', '')), ''),
           s.fullfort_ts, s.fullfort_av, s.notat,
           (l.startet + s.frist_dogn)::date,
           -- Regnet i BASEN, i samme skann som raden (M-16-regelen).
           -- Et FULLFØRT steg har ingen løpende frist: tallet er NULL,
           -- ikke et voksende antall døgn over en frist som ikke lenger
           -- gjelder (M-34s `ikke_relevant`-lærdom, samme form).
           CASE WHEN s.fullfort_ts IS NOT NULL THEN NULL
                ELSE (current_date
                      - (l.startet + s.frist_dogn))::int END,
           -- BLOKKERT: et obligatorisk steg lenger nede står ufullført,
           -- så dette kan ikke gjøres ennå. Flaten skal si det, ikke
           -- vise en knapp som gir 409.
           s.fullfort_ts IS NULL AND s.obligatorisk AND EXISTS (
               SELECT 1 FROM public.lopsteg f
                WHERE f.tenant = s.tenant AND f.lop_id = s.lop_id
                  AND f.steg_nr < s.steg_nr AND f.obligatorisk
                  AND f.fullfort_ts IS NULL)
      FROM public.lopsteg s
      JOIN public.onboardinglop l
        ON l.tenant = s.tenant AND l.lop_id = s.lop_id
      LEFT JOIN public.brukeridentitet b
        ON b.bruker_id = s.eier_bruker_id
     WHERE s.tenant = p_tenant AND s.lop_id = p_lop_id
     ORDER BY s.steg_nr;
END $$;
REVOKE ALL ON FUNCTION m18_stegene(TEXT, UUID) FROM PUBLIC;

-- MALENE, med antall steg.
CREATE FUNCTION m18_malene(p_tenant TEXT)
RETURNS TABLE(mal_id UUID, navn TEXT, versjon INT, aktiv BOOLEAN,
              antall_steg INT, paagaende_lop INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm18_malene');
    RETURN QUERY
    SELECT m.mal_id, m.navn, m.versjon, m.aktiv,
           (SELECT count(*)::int FROM public.onboardingmalsteg s
             WHERE s.tenant = m.tenant AND s.mal_id = m.mal_id),
           -- FLATEN SKAL KUNNE SI HVORFOR malen ikke kan redigeres, i
           -- stedet for å la brukeren møte vaktens feilmelding.
           (SELECT count(*)::int FROM public.onboardinglop l
             WHERE l.tenant = m.tenant AND l.mal_id = m.mal_id
               AND l.status = 'paagaar')
      FROM public.onboardingmal m
     WHERE m.tenant = p_tenant
     ORDER BY m.navn, m.mal_id;
END $$;
REVOKE ALL ON FUNCTION m18_malene(TEXT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Sveipens kandidater. EGEN funksjon (100/101/102-formen).
-- ------------------------------------------------------------

-- `p_dogn_stille` er hvor lenge et PÅGÅENDE løp kan stå uten at noe
-- skjer før det er et funn. Fjorten døgn er to uker uten framdrift på en
-- ny kunde. Parameter med et forsvarlig standardsvar, ikke en konstant.
CREATE FUNCTION m18_funnkandidater(p_tenant TEXT, p_dag DATE,
                                   p_dogn_stille INT DEFAULT 14)
RETURNS TABLE(lop_id UUID, steg_nr INT, funntype TEXT,
              dogn_over_grense INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_stille INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm18_funnkandidater');
    v_stille := greatest(coalesce(p_dogn_stille, 14), 1);
    RETURN QUERY
    -- 1. STOPPEDE LØP: pågående, og ingenting fullført på lenge. Målt
    --    fra SISTE fullføring der det finnes en, ellers fra starten —
    --    ellers ville et løp der noen jobbet i går blitt et funn fordi
    --    det ble startet for en måned siden.
    SELECT l.lop_id, 0, 'stoppet_lop'::text,
           (p_dag - coalesce(
               (SELECT max(s.fullfort_ts)::date FROM public.lopsteg s
                 WHERE s.tenant = l.tenant AND s.lop_id = l.lop_id
                   AND s.fullfort_ts IS NOT NULL),
               l.startet) - v_stille)::int
      FROM public.onboardinglop l
     WHERE l.tenant = p_tenant AND l.status = 'paagaar'
       AND (p_dag - coalesce(
               (SELECT max(s.fullfort_ts)::date FROM public.lopsteg s
                 WHERE s.tenant = l.tenant AND s.lop_id = l.lop_id
                   AND s.fullfort_ts IS NOT NULL),
               l.startet)) > v_stille
    UNION ALL
    -- 2. STEG OVER FRIST: ufullført og forbi sin egen frist, i et
    --    pågående løp. Et BLOKKERT steg er også et funn — det er
    --    nettopp forsinkelsen lenger nede som gjør det forsinket, og en
    --    flate som skjulte det ville skjult konsekvensen av den.
    SELECT s.lop_id, s.steg_nr, 'steg_over_frist'::text,
           (p_dag - (l.startet + s.frist_dogn))::int
      FROM public.lopsteg s
      JOIN public.onboardinglop l
        ON l.tenant = s.tenant AND l.lop_id = s.lop_id
     WHERE s.tenant = p_tenant AND l.status = 'paagaar'
       AND s.fullfort_ts IS NULL
       AND p_dag > (l.startet + s.frist_dogn)
    UNION ALL
    -- 3. LØP UTEN AKTIV EIER: eieren er ikke lenger aktivt medlem. Uten
    --    dette blir et løp stille foreldreløst når noen slutter, og
    --    ingen oppdager det før kunden ringer.
    SELECT l.lop_id, 0, 'lop_uten_aktiv_eier'::text, NULL::int
      FROM public.onboardinglop l
     WHERE l.tenant = p_tenant AND l.status = 'paagaar'
       AND NOT EXISTS (SELECT 1 FROM public.brukermedlemskap bm
                        WHERE bm.tenant = l.tenant
                          AND bm.bruker_id = l.eier_bruker_id
                          AND bm.aktiv);
END $$;
REVOKE ALL ON FUNCTION m18_funnkandidater(TEXT, DATE, INT) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 4b. Sveipen selv. KRYSS-TENANT, egen LOGIN-rolle, egen timer.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_onboarding_eier;

CREATE FUNCTION m18_sveip_onboarding(p_grense INT DEFAULT 500,
                                     p_dogn_stille INT DEFAULT 14)
RETURNS TABLE(tenanter INT, nye INT, oppdaterte INT, lukkede INT,
              avkortet INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_n INT; v_grense INT;
        v_stille INT; v_dag DATE; v_naa TIMESTAMPTZ;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm18_sveip_onboarding: sveipen er KRYSS-TENANT og'
            ' kjøres uten tenantkontekst — en kaller som har satt en'
            ' kontekst ber om noe annet enn det denne funksjonen gjør'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_grense := greatest(least(coalesce(p_grense, 500), 5000), 1);
    v_stille := greatest(coalesce(p_dogn_stille, 14), 1);
    v_dag := current_date;
    v_naa := now();
    tenanter := 0; nye := 0; oppdaterte := 0; lukkede := 0; avkortet := 0;
    SELECT array_agg(DISTINCT l.tenant ORDER BY l.tenant) INTO v_tenanter
      FROM public.onboardinglop l;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        tenanter := tenanter + 1;

        UPDATE public.onboardingfunn f
           SET sist_sett_sveip = v_naa, apen = true, lukket_ts = NULL,
               dogn_over_grense = kand.dogn_over_grense
          FROM public.m18_funnkandidater(v_t, v_dag, v_stille) kand
         WHERE f.tenant = v_t AND f.lop_id = kand.lop_id
           AND f.steg_nr = kand.steg_nr AND f.funntype = kand.funntype;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        oppdaterte := oppdaterte + v_n;

        INSERT INTO public.onboardingfunn
            (tenant, lop_id, steg_nr, funntype, dogn_over_grense,
             forst_sett, sist_sett_sveip, apen)
        SELECT v_t, kand.lop_id, kand.steg_nr, kand.funntype,
               kand.dogn_over_grense, v_naa, v_naa, true
          FROM public.m18_funnkandidater(v_t, v_dag, v_stille) kand
         WHERE NOT EXISTS (
                SELECT 1 FROM public.onboardingfunn f
                 WHERE f.tenant = v_t AND f.lop_id = kand.lop_id
                   AND f.steg_nr = kand.steg_nr
                   AND f.funntype = kand.funntype)
         ORDER BY coalesce(kand.dogn_over_grense, 0) DESC,
                  kand.lop_id, kand.steg_nr, kand.funntype
         LIMIT v_grense;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        nye := nye + v_n;
        IF v_n = v_grense THEN
            avkortet := avkortet + 1;
        END IF;

        UPDATE public.onboardingfunn f
           SET apen = false, lukket_ts = v_naa
         WHERE f.tenant = v_t AND f.apen
           AND NOT EXISTS (
                SELECT 1
                  FROM public.m18_funnkandidater(v_t, v_dag, v_stille) kand
                 WHERE kand.lop_id = f.lop_id
                   AND kand.steg_nr = f.steg_nr
                   AND kand.funntype = f.funntype);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        lukkede := lukkede + v_n;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m18_sveip_onboarding(INT, INT) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 5. Rettighetene. Migrasjonen NAVNGIR IKKE runtime-rollen (057-
--    lærdommen). `m18_sveip_onboarding` og `m18_funnkandidater` grantes
--    ingen: den første er sveiperollens, den andre et internt ledd.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_onboarding_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m18_onboardingstatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m18_lopene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m18_stegene(TEXT, UUID)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m18_malene(TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m18_registrer_mal(TEXT, UUID, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m18_sett_malsteg(TEXT, UUID, JSONB, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m18_start_lop(TEXT, UUID, UUID, TEXT, TEXT, DATE, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m18_sett_stegeier(TEXT, UUID, INT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m18_fullfor_steg(TEXT, UUID, INT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m18_avslutt_lop(TEXT, UUID, TEXT, TEXT, TEXT) TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_onboardingsveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m18_sveip_onboarding(INT, INT) TO disponit_onboardingsveip';
    END IF;
END $$;
RESET ROLE;
