-- 092: M-3 Datakvalitetsagent v1 — REN MÅLING.
--
-- V1-DOMMEN, ORDRETT FRA MANIFESTET: katalogteksten lover retting,
-- semantisk sammenslåing og karantene. Alt det ENDRER kundens data, og
-- en ny endringsvei er den farligste klassen kode i huset. v1 er derfor
-- profilering og ingenting annet: den teller tomme felt, formatavvik,
-- duplikatnøkler og døde referanser i plattformens EGNE tenant-tabeller,
-- skriver tallene til sine egne lagre og alarmerer. Den retter
-- ingenting, slår ingenting sammen, og BLOKKERER INGEN BESTILLING —
-- blokkering er en policyendring med attestasjon, ikke en bieffekt av at
-- noen la til en terskel. Invarianten
-- `bestilling_blokkert_av_kvalitetsmaaling` håndhever dommen statisk
-- (modulen importerer ingenting fra bestillingsveien og skriver ingen
-- DML mot `bestilling*`/`oppdrag`/`policyer`) og funksjonelt (en
-- bestilling går uendret gjennom med et rødt kvalitetsfunn i basen).
--
-- ============================================================
-- DESIGNET, OG HVORFOR DET SER SLIK UT (kravet i byggebriefen)
-- ============================================================
--
-- 1. FIRE LAGRE, IKKE DELT MED M-4. Formen speiler M-4s
--    retensjonsregister med vilje — to registre som måler huset bør
--    kunne leses likt — men de er ULIKE moduler, og delte tabeller
--    ville gjort den ene modulens migrasjon til den andres
--    avhengighet. `kvalitetsregel` er hva som profileres,
--    `kvalitetskjoring` er runden, `kvalitetsprofil` er tallene og
--    `kvalitetsfunn` er det som må gjøres noe med.
--
-- 2. REGISTERET ER GLOBALT OG SEEDET HER (M-4s registerform).
--    En kvalitetsregel er en plattformdom om hva som er riktig form på
--    en kolonne — ikke en kundepreferanse. Derfor ingen tenant-kolonne,
--    ingen RLS, og endring KUN i migrasjon: runtime har ingen
--    INSERT/UPDATE noe sted (migrer.py gir bare EXECUTE på lesedørene).
--
-- 3. TRIGGER-DOKTRINEN: `to_regclass(relasjon)` MÅ svare, kolonnen MÅ
--    stå i information_schema.columns, og relasjonen MÅ ha en
--    `tenant`-kolonne. En regel som peker på en kolonne som ikke finnes
--    er en løgn registeret ikke skal kunne bære — og en regel uten
--    tenant-kolonne kunne aldri gitt en profilrad per tenant.
--    `fremmednokkel_lever` verifiseres i BEGGE ender.
--
-- 4. KOLONNEGRANT, ALDRI TABELLGRANT. `disponit_kvalitet_eier` har
--    `GRANT SELECT (tenant, <profilert kolonne>)` og ikke én kolonne
--    mer. At profileren aldri leser persondata er dermed en egenskap
--    ved BASEN, ikke ved disiplinen — og porten måler det mot
--    information_schema.column_privileges, ikke mot kildeteksten.
--
--    HVILKE KOLONNER JEG BEVISST IKKE PROFILERER, og hvorfor:
--      * ALT som er tenant-DEK-kryptert: `kandidat_*.*_kryptert`,
--        `epost_melding.kropp_kryptert`, `epost_utkast.tekst_kryptert`,
--        `epost_vedlegg.navn_kryptert`, `inndata_artefakt`-payloaden,
--        `oppdrag.payload` med `nonce`/`key_id`. En NULL-telling der
--        ville krevd grant på selve persondataen, og ciphertext har
--        uansett verken tomhet eller format å måle.
--      * `epost_kilde.postboks` og `.auth_kryptert`: en e-postadresse
--        ER persondata selv om den ser ut som et format å validere.
--      * `brukeridentitet.profil`, `.issuer`, `.sub`: identiteten hos
--        IdP-en. KUN `bruker_id` er grantet, og bare fordi den er et
--        pseudonymt surrogat (`bid_<uuid>`) og målet for
--        `fremmednokkel_lever`.
--      * `api_tokener.*`, `tenant_nokler.*`, `tenant_pseudonymnokkel.*`:
--        hemmeligheter. En måler har ingenting der å gjøre.
--      * HELE bestillings- og beslutningsveien (`bestilling_*`,
--        `oppdrag`, `policyer`, `unntak`, `revisjonslogg`): ikke fordi
--        den er persondata, men fordi v1-dommen sier at M-3 ikke skal
--        ha noe forhold til den i det hele tatt. Et SELECT-grant der
--        ville vært det første steget mot en måler som «bare» ser på
--        en bestilling.
--
-- 5. KRYSS-TENANT ER EN EKSPLISITT POLICY PER TABELL (m6_reaper-/
--    m57_reaper-formen), aldri BYPASSRLS. Profileren må aggregere per
--    tenant over hele relasjonen; policyen `m3_profilering` er derfor
--    `FOR SELECT TO disponit_kvalitet_eier` — strengere enn forbildene,
--    som står på ALL. Leseretten er dermed en egenskap ved rollen som
--    ikke har ett eneste skrivegrant noe sted.
--
-- 6. PROFILEN SKRIVES MED TENANTKONTEKST PER RAD. `kvalitetsprofil` og
--    `kvalitetsfunn` har `tenant_isolasjon` og INGEN kryss-tenant-
--    policy for skriving: profileren setter `disponit.tenant` til
--    radens egen tenant før hver INSERT (035/088-formen). Én tenant kan
--    ikke få en annens tall skrevet på seg selv om koden skulle ville.
--
-- 7. KJØRINGEN ER PLATTFORMSKOP (090/091-dommen). Hodet har ingen
--    tenant-kolonne: det er fire tall om plattformens egen måling.
--    `avbrutt` skiller «målt, ingenting å melde» fra «rakk ikke
--    ferdig», og `umaalbare_regler` NAVNGIR dem, med
--    `CHECK (antall_umaalbare = cardinality(umaalbare_regler))` slik at
--    tallet ikke kan forfalskes uten listen. Hodet skrives ÉN gang, til
--    slutt, med alt utfylt — derfor er append-only her en ekte
--    append-only og ikke en overgangsvakt. FK-en fra profilraden er
--    DEFERRABLE INITIALLY DEFERRED nettopp for det.
--
-- 8. DEN BÆRENDE REGELEN: en regel som ikke kunne måles gir FUNN, aldri
--    en profilrad med 0 avvik. «0 tomme felt» fordi grantet manglet er
--    ikke en grønn profil — det er en profil som ikke kjørte. Derfor
--    finnes ingen INSERT i feilveien, bare `umaalbar` + navnet i
--    kjøringens `umaalbare_regler`.
--
-- 9. FUNN ER LEVENDE, IKKE EVIDENS. Ett funn per (regel, tenant,
--    funntype) holdes åpent og oppdateres med `sist_sett_kjoring` —
--    funnlisten vokser ikke med kadensen. Identiteten er frosset;
--    DELETE er avvist. De tre REGISTERfunntypene (`umaalbar`,
--    `regel_uten_kolonne`, `ukjent_tabell`) er egenskaper ved REGELEN
--    og ikke ved en kundes data, og bæres derfor av sentinel-tenanten
--    `__plattform_kvalitet` (`__plattform_domener`-presedensen fra 041).
--    Bare `terskel_overskredet` hører en ekte tenant til.
--
-- 10. SP-10: migrasjonen er ren DDL bortsett fra seedet av sitt EGET,
--     nyopprettede register (044/088/089-formen). Ingen backfill, ingen
--     masse-DML, ingen utsatte triggerhendelser å presse en ALTER
--     forbi — derfor er begge kjøringene (tom base / bebodd base) den
--     samme kjøringen.

-- ------------------------------------------------------------
-- 0. Eierrollen må kunne skape i skjemaet den skal eie i.
--    Skjemaet eies av migrator, så granten hører hjemme her og ikke i
--    oppsettsskriptet. (Klyngefundamentet ga de fem nye eierrollene
--    hverken denne granten eller migrator-medlemskap; medlemskapet MÅ
--    komme fra oppsett-postgresql.sh/ci.yml, denne linjen trenger det
--    ikke.)
-- ------------------------------------------------------------
GRANT USAGE, CREATE ON SCHEMA public TO disponit_kvalitet_eier;

-- ------------------------------------------------------------
-- 1. KOLONNEGRANTENE — gitt av tabellenes egen eier (migrator), fordi
--    en GRANT bare kan gis av den som eier objektet. Dette er modulens
--    sikkerhetsgrense: nøyaktig disse kolonnene, og ikke én mer.
-- ------------------------------------------------------------
GRANT SELECT (tenant, hostname) ON domenekontroll
    TO disponit_kvalitet_eier;
GRANT SELECT (tenant, oppdatert_av, playbook_ref) ON kontinuitet_tjeneste
    TO disponit_kvalitet_eier;
-- `varsel` er det tydeligste eksempelet på hvorfor granten er per
-- KOLONNE: `tekstnokkel` er en locale-nøkkel og bærer ingenting om
-- noen, mens `parametre` i samme rad kan bære hva som helst. Måleren
-- får den første og aldri den andre — og det er basen som sier det,
-- ikke en kommentar i en jobb.
GRANT SELECT (tenant, tekstnokkel) ON varsel TO disponit_kvalitet_eier;
GRANT SELECT (tenant, rolle, bruker_id, bekreftet_av) ON beredskapskontakt
    TO disponit_kvalitet_eier;
-- Målet for `fremmednokkel_lever`. `brukeridentitet` er GLOBAL (ingen
-- tenant, ingen RLS), og KUN surrogatnøkkelen er grantet: `profil`,
-- `issuer` og `sub` er identiteten hos IdP-en og skal ikke kunne leses
-- av en teller.
GRANT SELECT (bruker_id) ON brukeridentitet TO disponit_kvalitet_eier;

-- Kryss-tenant-vinduet, eksplisitt og KUN for lesing. Uten det ville
-- `tenant_isolasjon` gjort profileren blind (fail-closed), og en blind
-- profil ville rapportert null avvik over null rader — nøyaktig løgnen
-- invarianten `umaalbar_tabell_talt_som_null` finnes for å hindre.
DO $$
DECLARE r TEXT;
BEGIN
    FOREACH r IN ARRAY ARRAY['domenekontroll', 'kontinuitet_tjeneste',
                             'beredskapskontakt', 'varsel']
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_policy
                        WHERE polrelid = to_regclass('public.' || r)
                          AND polname = 'm3_profilering') THEN
            EXECUTE format(
                'CREATE POLICY m3_profilering ON public.%I FOR SELECT'
                ' TO disponit_kvalitet_eier'
                ' USING (CURRENT_USER = ''disponit_kvalitet_eier'')', r);
        END IF;
    END LOOP;
END $$;

-- SP-1-PORTEN SELV. `krev_tenantkontekst` eies av
-- `disponit_m37_claimer`, og EXECUTE er trukket fra PUBLIC: en definer
-- som skal PASSERE porten må få den eksplisitt (`disponit_domene_eier`
-- har den fra før, av samme grunn). Uten denne linjen ville hver av
-- M-3s fire lesedører falt på «permission denied for function
-- krev_tenantkontekst» — altså blitt en 503 i stedet for et svar, og
-- porten ville sett ut som en driftsfeil i stedet for en manglende
-- rettighet. Granten gis AV eieren, som alle andre.
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_kvalitet_eier;
RESET ROLE;

SET LOCAL ROLE disponit_kvalitet_eier;

-- ------------------------------------------------------------
-- 2. kvalitetsregel — hva som profileres, og med hvilken forventning.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kvalitetsregel (
    regel_id TEXT PRIMARY KEY
        CHECK (regel_id ~ '^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$'),
    relasjon TEXT NOT NULL CHECK (relasjon ~ '^[a-z][a-z0-9_]*$'),
    kolonne  TEXT NOT NULL CHECK (kolonne  ~ '^[a-z][a-z0-9_]*$'),
    regeltype TEXT NOT NULL CHECK (regeltype IN (
        'ikke_tom', 'format', 'unik_innen_tenant', 'fremmednokkel_lever')),
    -- `format`: et POSIX-regex målt med `!~`. `fremmednokkel_lever`:
    -- «relasjon.kolonne» for målet. De to andre typene trenger ingen —
    -- og skal derfor ikke ha en, ellers kan et uttrykk stå og se ut som
    -- om det betyr noe.
    uttrykk TEXT,
    alvorlighet TEXT NOT NULL CHECK (alvorlighet IN ('lav','middels','hoy')),
    -- Andelen avvik som tolereres FØR funnet `terskel_overskredet`
    -- reises. 0 betyr «ethvert avvik er et funn». Terskelen står på
    -- REGELEN og ikke i koden: en terskel i kode er en terskel ingen
    -- kan lese uten å lese koden.
    terskel_andel NUMERIC NOT NULL DEFAULT 0
        CHECK (terskel_andel >= 0 AND terskel_andel <= 1),
    begrunnelse TEXT NOT NULL CHECK (length(btrim(begrunnelse)) > 0),
    CONSTRAINT kvalitetsregel_uttrykk_naar_kreves CHECK (
        (regeltype IN ('format', 'fremmednokkel_lever'))
        = (uttrykk IS NOT NULL AND length(btrim(uttrykk)) > 0))
);

-- Vakten: registeret kan ikke bære en regel som peker på noe som ikke
-- finnes. Målt mot katalogen ved HVER skriving, ikke bare ved seeding —
-- en regel lagt til i en senere migrasjon møter samme dør.
CREATE OR REPLACE FUNCTION m3_regel_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_maalrel TEXT; v_maalkol TEXT; v_punkt INT;
BEGIN
    IF to_regclass('public.' || NEW.relasjon) IS NULL THEN
        RAISE EXCEPTION 'kvalitetsregel %: relasjonen % finnes ikke —'
            ' en regel som peker på en tabell som ikke finnes er en'
            ' løgn registeret ikke skal kunne bære',
            NEW.regel_id, NEW.relasjon
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns c
                    WHERE c.table_schema = 'public'
                      AND c.table_name = NEW.relasjon
                      AND c.column_name = NEW.kolonne) THEN
        RAISE EXCEPTION 'kvalitetsregel %: kolonnen %.% finnes ikke',
            NEW.regel_id, NEW.relasjon, NEW.kolonne
            USING ERRCODE = 'check_violation';
    END IF;
    -- Profilen er per (kjøring, regel, TENANT). En relasjon uten
    -- tenant-kolonne kunne aldri gitt en slik rad, og regelen ville
    -- stått i registeret og aldri produsert et tall.
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns c
                    WHERE c.table_schema = 'public'
                      AND c.table_name = NEW.relasjon
                      AND c.column_name = 'tenant') THEN
        RAISE EXCEPTION 'kvalitetsregel %: % har ingen tenant-kolonne —'
            ' profilen er per tenant og kan ikke skrives for den',
            NEW.regel_id, NEW.relasjon
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.regeltype = 'fremmednokkel_lever' THEN
        v_punkt := position('.' IN NEW.uttrykk);
        IF v_punkt < 2 OR v_punkt = length(NEW.uttrykk) THEN
            RAISE EXCEPTION 'kvalitetsregel %: uttrykket «%» er ikke'
                ' «relasjon.kolonne»', NEW.regel_id, NEW.uttrykk
                USING ERRCODE = 'check_violation';
        END IF;
        v_maalrel := substr(NEW.uttrykk, 1, v_punkt - 1);
        v_maalkol := substr(NEW.uttrykk, v_punkt + 1);
        IF to_regclass('public.' || v_maalrel) IS NULL
           OR NOT EXISTS (SELECT 1 FROM information_schema.columns c
                           WHERE c.table_schema = 'public'
                             AND c.table_name = v_maalrel
                             AND c.column_name = v_maalkol) THEN
            RAISE EXCEPTION 'kvalitetsregel %: fremmednøkkelmålet %.%'
                ' finnes ikke', NEW.regel_id, v_maalrel, v_maalkol
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.regeltype = 'format' THEN
        -- Et ugyldig regex ville felt HVER kjøring med `umaalbar` i
        -- stedet for å bli avvist der feilen ble gjort.
        BEGIN
            PERFORM 'x' ~ NEW.uttrykk;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'kvalitetsregel %: uttrykket er ikke et'
                ' gyldig regex', NEW.regel_id
                USING ERRCODE = 'check_violation';
        END;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m3_regel_vakt() FROM PUBLIC;
DROP TRIGGER IF EXISTS m3_regel_vakt ON kvalitetsregel;
CREATE TRIGGER m3_regel_vakt
    BEFORE INSERT OR UPDATE ON kvalitetsregel
    FOR EACH ROW EXECUTE FUNCTION m3_regel_vakt();

-- ------------------------------------------------------------
-- 3. kvalitetskjoring — runden. PLATTFORMSKOP (090/091-dommen): fire
--    tall om målingen selv, ingen tenant, ingen RLS. Skrives ÉN gang,
--    til slutt, med alt utfylt.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kvalitetskjoring (
    kjoring_id UUID PRIMARY KEY,
    startet_ts TIMESTAMPTZ NOT NULL,
    fullfort_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    antall_regler INT NOT NULL CHECK (antall_regler >= 0),
    antall_umaalbare INT NOT NULL CHECK (antall_umaalbare >= 0),
    antall_funn INT NOT NULL CHECK (antall_funn >= 0),
    -- Navnene, ikke bare tallet: en flate som skal si «ikke målt» ved
    -- riktig regel må vite HVILKEN. CHECK-en gjør tallet uforfalskbart.
    umaalbare_regler TEXT[] NOT NULL DEFAULT '{}'::text[],
    -- `avbrutt` skiller «målt, ingenting å melde» fra «rakk ikke
    -- ferdig». Uten den ville en runde som stoppet på batchgrensen sett
    -- ut som en grønn runde med få regler.
    avbrutt BOOLEAN NOT NULL,
    CONSTRAINT kjoring_umaalbare_stemmer
        CHECK (antall_umaalbare = cardinality(umaalbare_regler)),
    CONSTRAINT kjoring_fullfort_etter_start
        CHECK (fullfort_ts >= startet_ts)
);
CREATE INDEX IF NOT EXISTS kvalitetskjoring_startet
    ON kvalitetskjoring (startet_ts DESC);

CREATE OR REPLACE FUNCTION m3_kjoring_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'kvalitetskjoring: % avvist — en måling som kan'
        ' endres i ettertid er ikke en måling', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE ALL ON FUNCTION m3_kjoring_vakt() FROM PUBLIC;
DROP TRIGGER IF EXISTS m3_kjoring_vakt ON kvalitetskjoring;
CREATE TRIGGER m3_kjoring_vakt
    BEFORE UPDATE OR DELETE ON kvalitetskjoring
    FOR EACH ROW EXECUTE FUNCTION m3_kjoring_vakt();
DROP TRIGGER IF EXISTS m3_kjoring_ingen_truncate ON kvalitetskjoring;
CREATE TRIGGER m3_kjoring_ingen_truncate
    BEFORE TRUNCATE ON kvalitetskjoring
    FOR EACH STATEMENT EXECUTE FUNCTION public.avvis_endring();

-- ------------------------------------------------------------
-- 4. kvalitetsprofil — tallene, én rad per (kjøring, regel, tenant).
--    RLS tenant_isolasjon, append-only. FRAVÆRET AV EN RAD ER
--    INFORMASJON: regelen ble enten ikke målt (da står den i
--    kjøringens `umaalbare_regler`) eller tenanten har ingen rader.
--    Det er nøyaktig derfor det ikke skrives en 0-rad i feilveien.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kvalitetsprofil (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kjoring_id UUID NOT NULL,
    regel_id TEXT NOT NULL REFERENCES kvalitetsregel (regel_id),
    rader_vurdert BIGINT NOT NULL CHECK (rader_vurdert >= 0),
    rader_avvik BIGINT NOT NULL CHECK (rader_avvik >= 0),
    -- Generert, ikke beregnet av kalleren: en andel en skriver kunne
    -- oppgi selv er en andel som kan lyve om sine egne tellere.
    andel_avvik NUMERIC GENERATED ALWAYS AS (
        CASE WHEN rader_vurdert = 0 THEN NULL
             ELSE rader_avvik::numeric / rader_vurdert END) STORED,
    CONSTRAINT kvalitetsprofil_pk PRIMARY KEY (tenant, kjoring_id, regel_id),
    CONSTRAINT profil_avvik_innenfor_vurdert
        CHECK (rader_avvik <= rader_vurdert),
    -- DEFERRABLE: profilradene skrives UNDERVEIS, kjøringshodet TIL
    -- SLUTT (§7 over). Uten utsettelsen måtte hodet skrives først og
    -- oppdateres etterpå — og da hadde append-only vært en løgn.
    CONSTRAINT profil_kjoring_fk FOREIGN KEY (kjoring_id)
        REFERENCES kvalitetskjoring (kjoring_id)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX IF NOT EXISTS kvalitetsprofil_kjoring
    ON kvalitetsprofil (kjoring_id);

CREATE OR REPLACE FUNCTION m3_profil_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'kvalitetsprofil: % avvist — en profil som kan'
        ' endres i ettertid er ikke evidens', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE ALL ON FUNCTION m3_profil_vakt() FROM PUBLIC;
DROP TRIGGER IF EXISTS m3_profil_vakt ON kvalitetsprofil;
CREATE TRIGGER m3_profil_vakt
    BEFORE UPDATE OR DELETE ON kvalitetsprofil
    FOR EACH ROW EXECUTE FUNCTION m3_profil_vakt();
DROP TRIGGER IF EXISTS m3_profil_ingen_truncate ON kvalitetsprofil;
CREATE TRIGGER m3_profil_ingen_truncate
    BEFORE TRUNCATE ON kvalitetsprofil
    FOR EACH STATEMENT EXECUTE FUNCTION public.avvis_endring();

ALTER TABLE kvalitetsprofil ENABLE ROW LEVEL SECURITY;
ALTER TABLE kvalitetsprofil FORCE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policy
                    WHERE polrelid = 'kvalitetsprofil'::regclass
                      AND polname = 'tenant_isolasjon') THEN
        CREATE POLICY tenant_isolasjon ON kvalitetsprofil
            USING      (tenant = current_setting('disponit.tenant', true))
            WITH CHECK (tenant = current_setting('disponit.tenant', true));
    END IF;
END $$;

-- ------------------------------------------------------------
-- 5. kvalitetsfunn — det som må gjøres noe med. LUKKET funntype-sett.
--    Ett funn per (regel, tenant, funntype) holdes åpent og oppdateres
--    med `sist_sett_kjoring`: funnlisten vokser ikke med kadensen.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kvalitetsfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    regel_id TEXT NOT NULL REFERENCES kvalitetsregel (regel_id),
    -- LUKKET SETT (m6-formen): en ukjent funntype er en feil, aldri en
    -- ny kategori som stille oppstår.
    funntype TEXT NOT NULL CHECK (funntype IN (
        'umaalbar', 'terskel_overskredet',
        'regel_uten_kolonne', 'ukjent_tabell')),
    forst_sett_kjoring UUID NOT NULL,
    forst_sett_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_kjoring UUID NOT NULL,
    sist_sett_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    ganger_sett BIGINT NOT NULL DEFAULT 1 CHECK (ganger_sett > 0),
    -- Det MÅLTE grunnlaget for funnet, i funnets egen form. Aldri en
    -- verdi fra kundens data — bare tall og maskinkoder (porten i
    -- test_m3_datakvalitet måler formen).
    detaljer JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(detaljer) = 'object'),
    CONSTRAINT kvalitetsfunn_pk PRIMARY KEY (tenant, regel_id, funntype)
);
CREATE INDEX IF NOT EXISTS kvalitetsfunn_sist_sett
    ON kvalitetsfunn (sist_sett_ts DESC);

-- Vakten: identiteten er frosset, DELETE er avvist, og et funn kan
-- ikke gjøres YNGRE eller sjeldnere enn det er. Uten den siste biten
-- kunne en oppdatering skjult at funnet har stått i månedsvis.
CREATE OR REPLACE FUNCTION m3_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'kvalitetsfunn: % avvist — et funn lukkes ved'
            ' at målingen slutter å reise det, ikke ved sletting', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.regel_id IS DISTINCT FROM OLD.regel_id
       OR NEW.funntype IS DISTINCT FROM OLD.funntype
       OR NEW.forst_sett_kjoring IS DISTINCT FROM OLD.forst_sett_kjoring
       OR NEW.forst_sett_ts IS DISTINCT FROM OLD.forst_sett_ts THEN
        RAISE EXCEPTION 'kvalitetsfunn: identiteten og førstegangen er'
            ' frosset — et annet funn er en ny rad'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.sist_sett_ts < OLD.sist_sett_ts
       OR NEW.ganger_sett < OLD.ganger_sett THEN
        RAISE EXCEPTION 'kvalitetsfunn: et funn kan ikke bli yngre'
            ' eller sjeldnere enn det er'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m3_funn_vakt() FROM PUBLIC;
DROP TRIGGER IF EXISTS m3_funn_vakt ON kvalitetsfunn;
CREATE TRIGGER m3_funn_vakt
    BEFORE UPDATE OR DELETE ON kvalitetsfunn
    FOR EACH ROW EXECUTE FUNCTION m3_funn_vakt();
DROP TRIGGER IF EXISTS m3_funn_ingen_truncate ON kvalitetsfunn;
CREATE TRIGGER m3_funn_ingen_truncate
    BEFORE TRUNCATE ON kvalitetsfunn
    FOR EACH STATEMENT EXECUTE FUNCTION public.avvis_endring();

ALTER TABLE kvalitetsfunn ENABLE ROW LEVEL SECURITY;
ALTER TABLE kvalitetsfunn FORCE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policy
                    WHERE polrelid = 'kvalitetsfunn'::regclass
                      AND polname = 'tenant_isolasjon') THEN
        CREATE POLICY tenant_isolasjon ON kvalitetsfunn
            USING      (tenant = current_setting('disponit.tenant', true))
            WITH CHECK (tenant = current_setting('disponit.tenant', true));
    END IF;
    -- Plattformdriftens kryss-tenant-vindu. TO-klausulen er den ekte
    -- vakten (ingen LOGIN-rolle er medlem av eierrollen), og GUC-en
    -- gjør vinduet EKSPLISITT per transaksjon: nøyaktig én dør åpner
    -- det, og bare for lesing. Uten GUC-en ville tenantdøren under
    -- vært avhengig av sitt eget WHERE-ledd i stedet for av RLS.
    IF NOT EXISTS (SELECT 1 FROM pg_policy
                    WHERE polrelid = 'kvalitetsfunn'::regclass
                      AND polname = 'm3_tverrgaaende_lesing') THEN
        CREATE POLICY m3_tverrgaaende_lesing ON kvalitetsfunn
            FOR SELECT TO disponit_kvalitet_eier
            USING (current_setting('disponit.m3_tverrgaaende', true) = 'ja');
    END IF;
END $$;

-- ------------------------------------------------------------
-- 6. SEEDET. Åtte regler som dekker alle fire regeltypene, på kolonner
--    profileren FAKTISK har grant på (§1). Ikke én av dem er
--    persondata: et hostname, en playbook-referanse, en rolletekst, et
--    aktørnavn, en locale-nøkkel og to pseudonyme bruker-surrogater.
--
--    TO KLASSER, ÆRLIG SKILT, og begrunnelsene sier hvilken:
--      * REGRESJONSDETEKTORER — reglene der en CHECK i basen alt holder
--        formen (hostname, playbook_ref, rolle). De kan i dag ikke gi
--        annet enn 0, og det ER svaret: de måler at vernet står. Faller
--        en CHECK i en senere migrasjon, er profilen det første stedet
--        det synes.
--      * EKTE MÅLINGER — kolonnene basen IKKE verner: `oppdatert_av` og
--        `tekstnokkel` er NOT NULL uten CHECK, `bruker_id` har ingen
--        unikhet per person, og `bekreftet_av` har ingen fremmednøkkel.
--        Der kan tallet bli noe annet enn null, og det er der modulen
--        tjener til noe fra dag én.
-- ------------------------------------------------------------
INSERT INTO kvalitetsregel
    (regel_id, relasjon, kolonne, regeltype, uttrykk, alvorlighet,
     terskel_andel, begrunnelse)
VALUES
    ('domene.hostname.ikke_tom', 'domenekontroll', 'hostname',
     'ikke_tom', NULL, 'hoy', 0,
     'Et domene uten hostname kan ikke verifiseres og kan ikke bære en'
     ' bestilling. Tomheten er et register som har mistet sitt eget'
     ' nøkkelfelt.'),
    ('domene.hostname.format', 'domenekontroll', 'hostname',
     'format',
     '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$',
     'hoy', 0,
     'Kanonisk form er alt domeneverifiseringen sammenligner mot. Et'
     ' hostname som ikke er kanonisk vil aldri matche en challenge, og'
     ' feilen ser ut som en verifisering som «bare ikke går gjennom».'
     ' CHECK-en er_kanonisk_hostname holder dette i dag; regelen måler'
     ' at den fortsatt gjør det — en profil på 0 er et SVAR, ikke et'
     ' fravær.'),
    ('kontinuitet.oppdatert_av.ikke_tom', 'kontinuitet_tjeneste',
     'oppdatert_av', 'ikke_tom', NULL, 'lav', 0,
     'oppdatert_av er NOT NULL uten CHECK: den tomme strengen slipper'
     ' gjennom. Et kartinnslag ingen står ved er et innslag uten'
     ' eier — ferskheten kan måles, men ikke etterspørres hos noen.'),
    ('kontinuitet.playbook.format', 'kontinuitet_tjeneste', 'playbook_ref',
     'format', '^[a-z0-9][a-z0-9._-]{0,127}@[0-9a-f]{64}$', 'middels', 0,
     'Playbook-referansen er navn@sha256. Uten hashen er referansen'
     ' ikke innholdsbundet, og øvelsen kan ha kjørt mot et annet'
     ' dokument enn det kartet lover.'),
    ('beredskap.rolle.ikke_tom', 'beredskapskontakt', 'rolle',
     'ikke_tom', NULL, 'lav', 0,
     'En beredskapskontakt uten rolle kan ikke varsles i riktig'
     ' rekkefølge — listen er sortert på rolle og prioritet.'),
    ('beredskap.bruker.unik', 'beredskapskontakt', 'bruker_id',
     'unik_innen_tenant', NULL, 'lav', 0,
     'Unikhetsvilkåret i basen er (rolle, prioritet), ikke personen.'
     ' Samme person i to roller ser ut som to kontakter og gir en'
     ' kontaktdekning som er halvparten så bred som den ser ut.'),
    ('varsel.tekstnokkel.format', 'varsel', 'tekstnokkel',
     'format', '^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$', 'middels', 0,
     'Tekstnøkkelen slås opp i locale-settet. Er den ikke en nøkkel,'
     ' rendres den rå på skjermen — varselet blir en maskinkode i'
     ' innboksen til et menneske. Kolonnen er NOT NULL uten CHECK, så'
     ' formen er ikke garantert av basen.'),
    ('beredskap.bekrefter.lever', 'beredskapskontakt', 'bekreftet_av',
     'fremmednokkel_lever', 'brukeridentitet.bruker_id', 'hoy', 0,
     'bekreftet_av har med vilje INGEN fremmednøkkel (089): den er en'
     ' påstand om hvem som bekreftet. Uten en lever-måling kan en'
     ' bekreftelse stå igjen etter at bekrefteren er borte, og'
     ' kontaktdekningen forblir grønn på en signatur ingen eier.')
ON CONFLICT (regel_id) DO NOTHING;

-- ------------------------------------------------------------
-- 7. PROFILEREN. SECURITY DEFINER, eid av `disponit_kvalitet_eier`, og
--    dermed avgrenset av NØYAKTIG kolonnegrantene i §1.
--
--    SP-1, EKSPLISITT: funksjonen tar INGEN tenantparameter og kaller
--    derfor ikke `krev_tenantkontekst`. Det er `reap_epostdata`-formen
--    (088 §9), ikke et unntak fra SP-1: SP-1 binder en OPPGITT tenant
--    til kallerens kontekst, og her finnes ingen oppgitt tenant å binde
--    — UTVALGET ER PREDIKATET. Konteksten settes i stedet av
--    funksjonen selv, til RADENS egen tenant, før hver skriving, og
--    gjenopprettes til kallerens ved retur.
--
--    Dynamisk SQL bygges KUN med `format(%I)` over identifikatorer som
--    er verifisert mot katalogen i samme kall — aldri fra et
--    kallargument. `p_grense` er et tall.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION m3_profiler(p_grense INT DEFAULT 50)
RETURNS TABLE (kjoring_id UUID, antall_regler INT, antall_umaalbare INT,
               antall_funn INT, avbrutt BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_kjoring UUID := gen_random_uuid();
    v_start TIMESTAMPTZ := now();
    v_kontekst TEXT := current_setting('disponit.tenant', true);
    v_grense INT := greatest(least(coalesce(p_grense, 50), 500), 1);
    -- PER-REGEL-TIDSGRENSEN, og hvorfor den ser ut som den gjør.
    --
    -- MÅLT FUNN, IKKE ANTAKELSE: `statement_timeout` armes ÉN gang per
    -- toppnivåsetning (`start_xact_command`). En `SET LOCAL
    -- statement_timeout` INNE i en funksjon påvirker derfor ikke
    -- funksjonens egne setninger — verifisert i denne basen: en
    -- funksjon som setter 50 ms og deretter sover 0,5 s returnerer
    -- normalt, med `statement_timeout = 50ms` lest tilbake. Et
    -- «statement_timeout per regel» inne i en plpgsql-løkke finnes
    -- altså ikke å skrive.
    --
    -- Grensen håndheves derfor i to lag:
    --   1. HER, som et BUDSJETT målt på klokka: koster en regel mer enn
    --      grensen, FORKASTES målingen (RAISE inne i subtransaksjonen
    --      ruller profilradene tilbake) og regelen rapporteres
    --      `umaalbar`. En måling som koster mer enn plattformen har satt
    --      av til den, er ikke en måling vi kan kjøre hver dag — og da
    --      skal den ikke stå som et grønt tall.
    --   2. I JOBBEN, som `SET statement_timeout` på sesjonen FØR kallet.
    --      Det er det HARDE taket rundt hele runden, og det er det ene
    --      laget som faktisk kan avbryte en spørring som har løpt løpsk.
    -- Trygg lesning: GUC-en er en STRENG, og en verdi som ikke er et
    -- tall skal gi standardgrensen — ikke felle hele runden med en
    -- castfeil. En profilering som dør av en feilstavet innstilling ville
    -- vært en umålt base, og fraværet av tall ser ut som ingen problemer.
    v_raa TEXT := nullif(current_setting(
        'disponit.kvalitet_tidsgrense_ms', true), '');
    v_grense_ms INT := greatest(CASE WHEN v_raa ~ '^[0-9]+$'
                                     THEN v_raa::int ELSE 5000 END, 1);
    v_t0 TIMESTAMPTZ; v_brukt_ms INT;
    r RECORD; p RECORD;
    v_sql TEXT; v_maalrel TEXT; v_maalkol TEXT; v_punkt INT;
    v_regler INT := 0; v_umaalbare TEXT[] := '{}'::text[];
    v_funn INT := 0; v_funn_for INT; v_avbrutt BOOLEAN := false;
    v_totalt INT;
    v_maalt BOOLEAN;
BEGIN
    SELECT count(*) INTO v_totalt FROM public.kvalitetsregel;
    FOR r IN
        SELECT k.regel_id, k.relasjon, k.kolonne, k.regeltype, k.uttrykk,
               k.alvorlighet, k.terskel_andel
          FROM public.kvalitetsregel k
         ORDER BY k.regel_id
         LIMIT v_grense
    LOOP
        v_regler := v_regler + 1;

        -- ---- Registerets integritet, målt PÅ NYTT ved hver kjøring.
        -- Vakten holdt ved skrivingen; en tabell eller kolonne kan være
        -- droppet siden. Da er regelen et FUNN, aldri en stille null.
        IF to_regclass('public.' || r.relasjon) IS NULL THEN
            v_umaalbare := v_umaalbare || r.regel_id;
            v_funn := v_funn + public.m3_reis_funn(
                '__plattform_kvalitet', r.regel_id, 'ukjent_tabell',
                v_kjoring, jsonb_build_object('relasjon', r.relasjon));
            CONTINUE;
        END IF;
        -- pg_attribute, IKKE information_schema.columns: den siste
        -- filtrerer på rettigheter, så en TRUKKET kolonnegrant ville
        -- sett ut som en SLETTET kolonne — og et `regel_uten_kolonne`
        -- der svaret er `umaalbar` er nøyaktig den forvekslingen
        -- modulens bærende regel handler om. Katalogen sier hva som
        -- FINNES; om vi får lese det, avgjøres av at målingen under
        -- feiler.
        IF NOT EXISTS (SELECT 1 FROM pg_attribute a
                        WHERE a.attrelid = to_regclass('public.' || r.relasjon)
                          AND a.attname = r.kolonne
                          AND a.attnum > 0 AND NOT a.attisdropped) THEN
            v_umaalbare := v_umaalbare || r.regel_id;
            v_funn := v_funn + public.m3_reis_funn(
                '__plattform_kvalitet', r.regel_id, 'regel_uten_kolonne',
                v_kjoring, jsonb_build_object('relasjon', r.relasjon,
                                              'kolonne', r.kolonne));
            CONTINUE;
        END IF;

        -- ---- Spørringen. Identifikatorene kommer fra registeret og er
        -- nettopp verifisert mot katalogen; verdiene (regex) går inn som
        -- literal, ikke som identifikator.
        IF r.regeltype = 'ikke_tom' THEN
            v_sql := format(
                'SELECT t.tenant AS tn, count(*)::bigint AS n,'
                ' count(*) FILTER (WHERE t.%1$I IS NULL'
                '   OR btrim(t.%1$I::text) = %3$L)::bigint AS a'
                ' FROM public.%2$I t GROUP BY t.tenant',
                r.kolonne, r.relasjon, '');
        ELSIF r.regeltype = 'format' THEN
            -- NULL er IKKE et formatavvik — tomhet er `ikke_tom` sin
            -- jobb. To regler som teller det samme ville gjort summen
            -- av funn større enn antallet feil.
            v_sql := format(
                'SELECT t.tenant AS tn, count(*)::bigint AS n,'
                ' count(*) FILTER (WHERE t.%1$I IS NOT NULL'
                '   AND t.%1$I::text !~ %3$L)::bigint AS a'
                ' FROM public.%2$I t GROUP BY t.tenant',
                r.kolonne, r.relasjon, r.uttrykk);
        ELSIF r.regeltype = 'unik_innen_tenant' THEN
            v_sql := format(
                'SELECT t.tenant AS tn, count(*)::bigint AS n,'
                ' (count(t.%1$I) - count(DISTINCT t.%1$I))::bigint AS a'
                ' FROM public.%2$I t GROUP BY t.tenant',
                r.kolonne, r.relasjon);
        ELSE   -- fremmednokkel_lever
            v_punkt := position('.' IN r.uttrykk);
            v_maalrel := substr(r.uttrykk, 1, v_punkt - 1);
            v_maalkol := substr(r.uttrykk, v_punkt + 1);
            IF to_regclass('public.' || v_maalrel) IS NULL
               OR NOT EXISTS (SELECT 1 FROM pg_attribute a
                               WHERE a.attrelid = to_regclass(
                                         'public.' || v_maalrel)
                                 AND a.attname = v_maalkol
                                 AND a.attnum > 0 AND NOT a.attisdropped) THEN
                v_umaalbare := v_umaalbare || r.regel_id;
                v_funn := v_funn + public.m3_reis_funn(
                    '__plattform_kvalitet', r.regel_id, 'ukjent_tabell',
                    v_kjoring, jsonb_build_object('maal', r.uttrykk));
                CONTINUE;
            END IF;
            -- NULL er «ikke satt», ikke «død referanse».
            v_sql := format(
                'SELECT t.tenant AS tn, count(*)::bigint AS n,'
                ' count(*) FILTER (WHERE t.%1$I IS NOT NULL'
                '   AND NOT EXISTS (SELECT 1 FROM public.%3$I m'
                '     WHERE m.%4$I::text = t.%1$I::text))::bigint AS a'
                ' FROM public.%2$I t GROUP BY t.tenant',
                r.kolonne, r.relasjon, v_maalrel, v_maalkol);
        END IF;

        -- ---- Målingen, i sin EGEN subtransaksjon med sin egen
        -- tidsgrense. Feiler den — manglende grant, tidsavbrudd,
        -- hva som helst — er regelen UMÅLBAR, og det er et funn. Det
        -- skrives ikke én profilrad; «0 avvik» fordi målingen ikke
        -- kjørte er den ene løgnen denne modulen ikke skal kunne
        -- fortelle.
        -- Funntelleren snapshottes FØR blokka. Rulles subtransaksjonen
        -- tilbake, forsvinner også funnene den rakk å reise — men en
        -- plpgsql-VARIABEL rulles ikke tilbake, og uten dette ville
        -- kjøringens `antall_funn` talt funn som ikke finnes i basen.
        -- En måler som overrapporterer sine egne funn er like uærlig som
        -- en som underrapporterer dem.
        v_maalt := true; v_brukt_ms := 0; v_funn_for := v_funn;
        BEGIN
            v_t0 := clock_timestamp();
            FOR p IN EXECUTE v_sql LOOP
                -- Konteksten bindes til RADENS tenant før skrivingen.
                -- `tenant_isolasjon` er dermed vakten, ikke et WHERE
                -- vi må stole på.
                PERFORM set_config('disponit.tenant', p.tn, true);
                INSERT INTO public.kvalitetsprofil
                    (tenant, kjoring_id, regel_id, rader_vurdert, rader_avvik)
                VALUES (p.tn, v_kjoring, r.regel_id, p.n, p.a);
                IF p.n > 0 AND (p.a::numeric / p.n) > r.terskel_andel THEN
                    v_funn := v_funn + public.m3_reis_funn(
                        p.tn, r.regel_id, 'terskel_overskredet', v_kjoring,
                        jsonb_build_object('rader_vurdert', p.n,
                                           'rader_avvik', p.a,
                                           'terskel_andel', r.terskel_andel,
                                           'alvorlighet', r.alvorlighet));
                END IF;
            END LOOP;
            v_brukt_ms := (EXTRACT(EPOCH FROM
                (clock_timestamp() - v_t0)) * 1000)::int;
            IF v_brukt_ms > v_grense_ms THEN
                -- FORKASTES. RAISE her ruller subtransaksjonen — og med
                -- den hver profilrad regelen rakk å skrive — tilbake.
                -- Alternativet, å beholde tallene og bare notere at de
                -- var dyre, ville gitt en grønn profil for en måling
                -- plattformen ikke har råd til å gjenta.
                -- ERRCODE er BEVISST ikke `query_canceled`: `WHEN
                -- OTHERS` fanger per definisjon ikke den klassen, og en
                -- budsjettoverskridelse som veltet hele runden ville
                -- vært det motsatte av poenget. Klassen er den samme
                -- forskjellen som skiller de to lagene: en EKTE
                -- avbrytelse (jobbens `statement_timeout`) SKAL slippe
                -- ut og felle runden, budsjettet SKAL bare felle regelen.
                RAISE EXCEPTION 'm3: regelen % brukte % ms av % tillatte',
                    r.regel_id, v_brukt_ms, v_grense_ms
                    USING ERRCODE = 'program_limit_exceeded';
            END IF;
        EXCEPTION WHEN OTHERS THEN
            -- Subtransaksjonen rulles tilbake, og med den BÅDE de halve
            -- profilradene og funnene de utløste. En regel er målt HELT
            -- eller ikke i det hele tatt — «0 avvik» over en halv tabell
            -- er den samme løgnen som «0 avvik» over en tabell vi ikke
            -- fikk lese.
            v_funn := v_funn_for;
            v_maalt := false;
        END;
        IF NOT v_maalt THEN
            v_umaalbare := v_umaalbare || r.regel_id;
            v_funn := v_funn + public.m3_reis_funn(
                '__plattform_kvalitet', r.regel_id, 'umaalbar', v_kjoring,
                jsonb_build_object('regeltype', r.regeltype,
                                   'relasjon', r.relasjon,
                                   'brukt_ms', v_brukt_ms,
                                   'grense_ms', v_grense_ms));
        END IF;
    END LOOP;

    -- Batchgrensen: rakk vi ikke gjennom registeret, er runden AVBRUTT.
    v_avbrutt := v_regler < v_totalt;

    PERFORM set_config('disponit.tenant', coalesce(v_kontekst, ''), true);
    INSERT INTO public.kvalitetskjoring
        (kjoring_id, startet_ts, fullfort_ts, antall_regler,
         antall_umaalbare, antall_funn, umaalbare_regler, avbrutt)
    VALUES (v_kjoring, v_start, now(), v_regler,
            cardinality(v_umaalbare), v_funn, v_umaalbare, v_avbrutt);

    kjoring_id := v_kjoring; antall_regler := v_regler;
    antall_umaalbare := cardinality(v_umaalbare); antall_funn := v_funn;
    avbrutt := v_avbrutt;
    RETURN NEXT;
END $$;

-- Funnreiseren. Egen funksjon fordi den kalles fra seks steder i
-- profileren og MÅ oppføre seg likt hver gang: ett funn per (regel,
-- tenant, funntype), oppdatert med `sist_sett_kjoring`. Returnerer 1
-- bare når funnet er NYTT — kjøringens `antall_funn` er «nye funn i
-- denne runden», ikke «hvor mange ganger vi så noe».
CREATE OR REPLACE FUNCTION public.m3_reis_funn(
    p_tenant TEXT, p_regel TEXT, p_funntype TEXT, p_kjoring UUID,
    p_detaljer JSONB)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT;
BEGIN
    PERFORM set_config('disponit.tenant', p_tenant, true);
    INSERT INTO public.kvalitetsfunn
        (tenant, regel_id, funntype, forst_sett_kjoring, sist_sett_kjoring,
         detaljer)
    VALUES (p_tenant, p_regel, p_funntype, p_kjoring, p_kjoring, p_detaljer)
        ON CONFLICT (tenant, regel_id, funntype) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 1 THEN
        RETURN 1;
    END IF;
    -- Funnet står fra før: OPPDATER det, ikke skriv et nytt. Dette er
    -- hele grunnen til at funnlisten ikke vokser med kadensen.
    UPDATE public.kvalitetsfunn f
       SET sist_sett_kjoring = p_kjoring,
           sist_sett_ts = now(),
           ganger_sett = f.ganger_sett + 1,
           detaljer = p_detaljer
     WHERE f.tenant = p_tenant AND f.regel_id = p_regel
       AND f.funntype = p_funntype;
    RETURN 0;
END $$;

-- ------------------------------------------------------------
-- 8. LESEDØRENE (051-formen: `krev_tenantkontekst` FØRST). Runtime har
--    INGEN SELECT på noen av de fire tabellene — den når dem kun her.
-- ------------------------------------------------------------

-- Registeret. Globalt og uten tenantdata; tenanten kreves fordi RETTEN
-- til å spørre er øktens selv når dataene ikke er det (090/091-formen).
CREATE OR REPLACE FUNCTION m3_regelregister(p_tenant TEXT)
RETURNS TABLE (regel_id TEXT, relasjon TEXT, kolonne TEXT, regeltype TEXT,
               uttrykk TEXT, alvorlighet TEXT, terskel_andel NUMERIC,
               begrunnelse TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm3_regelregister');
    RETURN QUERY
    SELECT k.regel_id, k.relasjon, k.kolonne, k.regeltype, k.uttrykk,
           k.alvorlighet, k.terskel_andel, k.begrunnelse
      FROM public.kvalitetsregel k ORDER BY k.regel_id;
END $$;

-- Kjøringene med tenantens EGNE profiltall. Flate rader (kjøring ×
-- regel), som selvtestens dør: grupperingen er presentasjon og bor i
-- API-laget. LEFT JOIN, fordi en kjøring uten en eneste rad for denne
-- tenanten fortsatt er en kjøring som skjedde — og `avbrutt` og
-- `umaalbare_regler` er nettopp det flaten trenger for å si «ikke
-- målt» i stedet for «0».
CREATE OR REPLACE FUNCTION m3_kvalitetsprofil(p_tenant TEXT, p_grense INT)
RETURNS TABLE (kjoring_id UUID, startet_ts TIMESTAMPTZ,
               fullfort_ts TIMESTAMPTZ, antall_regler INT,
               antall_umaalbare INT, antall_funn INT,
               umaalbare_regler TEXT[], avbrutt BOOLEAN, alder_s BIGINT,
               regel_id TEXT, rader_vurdert BIGINT, rader_avvik BIGINT,
               andel_avvik NUMERIC)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm3_kvalitetsprofil');
    RETURN QUERY
    WITH siste AS (
        SELECT k.* FROM public.kvalitetskjoring k
         ORDER BY k.startet_ts DESC
         LIMIT greatest(least(coalesce(p_grense, 20), 100), 1)
    )
    SELECT s.kjoring_id, s.startet_ts, s.fullfort_ts, s.antall_regler,
           s.antall_umaalbare, s.antall_funn, s.umaalbare_regler,
           s.avbrutt,
           EXTRACT(EPOCH FROM (now() - s.startet_ts))::bigint,
           p.regel_id, p.rader_vurdert, p.rader_avvik, p.andel_avvik
      FROM siste s
      LEFT JOIN public.kvalitetsprofil p ON p.kjoring_id = s.kjoring_id
     ORDER BY s.startet_ts DESC, p.regel_id;
END $$;

-- Tenantens EGNE funn. Ren RLS: ingen WHERE-klausul på tenant, fordi
-- `tenant_isolasjon` allerede er den eneste sannheten her.
CREATE OR REPLACE FUNCTION m3_kvalitetsfunn(p_tenant TEXT, p_grense INT)
RETURNS TABLE (regel_id TEXT, funntype TEXT, forst_sett_ts TIMESTAMPTZ,
               sist_sett_ts TIMESTAMPTZ, ganger_sett BIGINT,
               detaljer JSONB)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm3_kvalitetsfunn');
    RETURN QUERY
    SELECT f.regel_id, f.funntype, f.forst_sett_ts, f.sist_sett_ts,
           f.ganger_sett, f.detaljer
      FROM public.kvalitetsfunn f
     ORDER BY f.sist_sett_ts DESC, f.regel_id, f.funntype
     LIMIT greatest(least(coalesce(p_grense, 100), 500), 1);
END $$;

-- PLATTFORMDRIFTENS funnliste, på tvers. Kryss-tenant-vinduet åpnes
-- LOKALT i denne transaksjonen og lukkes igjen før retur; policyen
-- `m3_tverrgaaende_lesing` er FOR SELECT, så vinduet kan ikke bli en
-- skrivevei uansett hvem som kaller. `platform:admin`-avgjørelsen er
-- endepunktets (/v1/utrulling-presedensen) — denne døren er
-- mekanismen, ikke autorisasjonen, og runtime får den bare fordi
-- endepunktet gater den.
CREATE OR REPLACE FUNCTION m3_kvalitetsfunn_tverrgaaende(
    p_tenant TEXT, p_grense INT)
RETURNS TABLE (tenant TEXT, regel_id TEXT, funntype TEXT,
               forst_sett_ts TIMESTAMPTZ, sist_sett_ts TIMESTAMPTZ,
               ganger_sett BIGINT, detaljer JSONB)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(
        p_tenant, 'm3_kvalitetsfunn_tverrgaaende');
    PERFORM set_config('disponit.m3_tverrgaaende', 'ja', true);
    RETURN QUERY
    SELECT f.tenant, f.regel_id, f.funntype, f.forst_sett_ts,
           f.sist_sett_ts, f.ganger_sett, f.detaljer
      FROM public.kvalitetsfunn f
     ORDER BY f.sist_sett_ts DESC, f.tenant, f.regel_id, f.funntype
     LIMIT greatest(least(coalesce(p_grense, 200), 1000), 1);
    -- VINDUET LUKKES IGJEN, i samme kall som åpnet det. `RETURN QUERY`
    -- har alt materialisert radene, så resten av transaksjonen — også
    -- tenantdøren over, om kalleren spør i motsatt rekkefølge — møter
    -- `tenant_isolasjon` alene. Et vindu som blir stående åpent er ikke
    -- et vindu, det er en dør.
    PERFORM set_config('disponit.m3_tverrgaaende', '', true);
    RETURN;
END $$;

-- ------------------------------------------------------------
-- 9. RETTIGHETENE. Migrasjonen navngir ALDRI runtime-rollen
--    (057-lærdommen) — `migrer.py` er eneste rettighetskilde for den.
--    Her gis kun målerollens ENE EXECUTE, og alt annet trekkes.
-- ------------------------------------------------------------
REVOKE ALL ON FUNCTION m3_profiler(INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION m3_reis_funn(TEXT, TEXT, TEXT, UUID, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION m3_regelregister(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION m3_kvalitetsprofil(TEXT, INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION m3_kvalitetsfunn(TEXT, INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION m3_kvalitetsfunn_tverrgaaende(TEXT, INT) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_kvalitetsmaaler') THEN
        -- NØYAKTIG ÉN funksjon. Ikke lesedørene: en profileringsjobb
        -- som kunne lese profilene tilbake ville hatt et
        -- kryss-tenant-vindu den ikke trenger for å telle.
        GRANT EXECUTE ON FUNCTION m3_profiler(INT)
            TO disponit_kvalitetsmaaler;
    END IF;
END $$;

RESET ROLE;
