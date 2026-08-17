-- ============================================================
-- Eierskapsreparasjon — OBJEKTSPESIFIKK designmodell (FIX-009 runde 2).
--
-- Codex' P1 på første utgave: en reparasjon som allowlister ROLLENE
-- $AUTH/$M37 bevarer også FEILPLASSERTE ordinære objekter hos de
-- privilegerte rollene. Og den symmetriske feilen var målbar i den andre
-- retningen: den håndholdte gjenopprettingslisten på staging manglet fem
-- claimer-funksjoner (trigger- og adminfunksjonene 005/007 oppretter i
-- SET ROLE-vinduet) — en rolleliste kunne aldri ha oppdaget det.
--
-- Modellen er derfor DESIGNTABELLEN under: hvert objekt de privilegerte
-- rollene skal eie, med full signatur, speilet fra det migrasjonene
-- faktisk oppretter (003/004 for authenticator, 005/007 for m37_claimer).
-- Alt annet ikke-extension i public eies av migrator. Reparasjonen virker
-- BEGGE veier: et designobjekt hos feil eier flyttes til sin designede
-- eier, et ordinært objekt hos en privilegert rolle flyttes til migrator.
-- Til slutt en SLUTTKONTROLL som feiler hardt hvis noe fortsatt står hos
-- feil eier — en reparasjon som ikke kan bevise sitt eget resultat er en
-- anbefaling.
--
-- Kjøres av postgres (oppsett-postgresql.sh) eller migrator (testene) —
-- begge er medlem av/har SET ROLE til de designede eierne.
-- Rollenavnene er konstanter her som i migrer.py sin M37_RETTIGHETER:
-- de er ikke konfigurasjon, de er arkitektur.
-- ============================================================

-- Egen transaksjon: temp-tabellen lever nøyaktig så lenge reparasjonen —
-- og feiler sluttkontrollen, rulles også reparasjonen tilbake, så en
-- halvreparert base ikke kan bli stående.
BEGIN;

CREATE TEMP TABLE _design (art TEXT, ident TEXT, eier TEXT) ON COMMIT DROP;
INSERT INTO _design VALUES
    -- 003/004: tokenlagring og verifisering eies av authenticator —
    -- runtime når tabellen KUN gjennom SECURITY DEFINER-funksjonen.
    ('TABLE',    'api_tokener',                    'disponit_authenticator'),
    ('FUNCTION', 'verifiser_token(text,text)',     'disponit_authenticator'),
    -- 009: PENDING-verifikasjonen for token-CLI-en (PR-009 V2).
    -- Paritetstesten fanget selv at denne manglet da 009 landet — nøyaktig
    -- jobben dens: en ny privilegert eid funksjon kan ikke bli stille udekket.
    ('FUNCTION', 'hent_pending_token(text)',       'disponit_authenticator'),
    -- 010: herdet sesjonsoppslag (PR-010 §1). Definer-funksjon eid av
    -- authenticator, fanget av paritetstesten som hent_pending_token.
    ('FUNCTION', 'slaa_opp_sesjon(text)',          'disponit_authenticator'),
    -- 005 §6–9 + 007: M-37-flaten eies av NOLOGIN-rollen m37_claimer.
    ('TABLE',    'arbeidskapabiliteter',           'disponit_m37_claimer'),
    ('TABLE',    'kvitteringskapabiliteter',       'disponit_m37_claimer'),
    ('FUNCTION', 'arkiver_policyversjon(text,text,text)',            'disponit_m37_claimer'),
    ('FUNCTION', 'bruk_kapabilitet(text,text)',                      'disponit_m37_claimer'),
    ('FUNCTION', 'bruk_kvitteringskapabilitet(text,text)',           'disponit_m37_claimer'),
    ('FUNCTION', 'claim_neste_oppdrag(text,text[],text,integer,text,text,bigint)', 'disponit_m37_claimer'),
    -- 005→015 (Codex P1): den GAMLE 4-args-signaturen står her til den er borte
    -- overalt. Reparasjonen kjører FØR migrer.py (oppsett-postgresql.sh) og som
    -- superbruker: på en base som ennå ikke har kjørt 015 ville steg 2
    -- klassifisert den fortsatt installerte gamle funksjonen som strøgods og
    -- flyttet den til migrator. 015 dropper den under `SET LOCAL ROLE
    -- disponit_m37_claimer` og ville da feilet på manglende eierskap —
    -- medlemskapet er `WITH INHERIT FALSE`, så migrator kan heller ikke droppe
    -- den på claimers vegne, og HELE oppgraderingen fra 005 stopper. Raden er
    -- transitorisk: etter 015 finnes ikke funksjonen, og designrader uten
    -- objekt hoppes stille over (oppslaget er to_regprocedure → NULL).
    ('FUNCTION', 'claim_neste_oppdrag(text,text[],text,integer)',    'disponit_m37_claimer'),
    ('FUNCTION', 'claim_neste_sak(text,integer)',                    'disponit_m37_claimer'),
    ('FUNCTION', 'forny_claim(text,bigint,text,integer,integer)',    'disponit_m37_claimer'),
    ('FUNCTION', 'frigi_hengende_kapabiliteter()',                   'disponit_m37_claimer'),
    ('FUNCTION', 'frigi_utlopte_claims()',                           'disponit_m37_claimer'),
    ('FUNCTION', 'innlos_kvitteringskapabilitet(text,text)',         'disponit_m37_claimer'),
    ('FUNCTION', 'kapabilitet_innenfor_claim()',                     'disponit_m37_claimer'),
    ('FUNCTION', 'kapabilitet_statusmaskin()',                       'disponit_m37_claimer'),
    ('FUNCTION', 'knytt_verifikasjonsoppdrag(text,bigint,text,integer,bigint)', 'disponit_m37_claimer'),
    ('FUNCTION', 'kvitteringskapabilitet_statusmaskin()',            'disponit_m37_claimer'),
    ('FUNCTION', 'registrer_verifikasjonsbevis(bigint,text,text,text,text,text,jsonb,text,integer,integer,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'reserver_kapabilitet(text,text,integer)',          'disponit_m37_claimer'),
    -- 011 (PR-012 gate 14a): lesevei for utestående oppdrag/kapabilitet.
    -- Eid av m37_claimer fordi arbeidskapabiliteter er off-limits for runtime
    -- (runtime får KUN EXECUTE). Paritetstesten fanget denne da 14a landet.
    ('FUNCTION', 'sak_utestaaende(text,bigint)',                     'disponit_m37_claimer'),
    ('FUNCTION', 'start_verifikasjonsgenerasjon(text,bigint,text,integer,jsonb,text,text,text)', 'disponit_m37_claimer'),
    ('FUNCTION', 'tenanter_uten_policysnapshot()',                   'disponit_m37_claimer'),
    ('FUNCTION', 'utsted_arbeidskapabilitet(text,integer,text,integer)', 'disponit_m37_claimer'),
    ('FUNCTION', 'utsted_kvitteringskapabilitet(bigint,text,integer,text)', 'disponit_m37_claimer'),
    -- 013 (PR-013): den herdede aktiveringsfunksjonen. Eid av
    -- disponit_policy_eier fordi policyer/policy_hode er off-limits for runtime
    -- (runtime får KUN EXECUTE). Ny privilegert eier — paritetstesten dekker den.
    ('FUNCTION', 'aktiver_policy(text,text,integer,text)',           'disponit_policy_eier'),
    -- 032: signaturen bærer den forventede aktive versjonen + innholdshashen
    -- (den optimistiske låsen). Toargumentsformen finnes ikke lenger — den
    -- slettet uten å binde seg til en versjon, og migrasjonen dropper den.
    ('FUNCTION', 'slett_ubrukt_policy(text,text,text,text)', 'disponit_policy_eier'),
    -- 014 (PR-014a): modulregisterets herdede overgangsfunksjoner. Eid av
    -- disponit_modul_eier fordi registertabellene er off-limits for runtime
    -- (runtime får KUN SELECT). Paritetstesten dekker dem.
    ('FUNCTION', 'installer_modul(text,text)',                        'disponit_modul_eier'),
    ('FUNCTION', 'registrer_oppdragstype(text,text,integer,text,text)', 'disponit_modul_eier'),
    ('FUNCTION', 'sett_modulstatus(text,text,text,text)',             'disponit_modul_eier'),
    ('FUNCTION', 'registrer_kontrakt(text,integer,text,text,text,text,text,text)', 'disponit_modul_eier'),
    ('FUNCTION', 'registrer_release(text,text,integer,text,text,text,text)', 'disponit_modul_eier'),
    ('FUNCTION', 'bytt_release(text,text,text,integer,text,text)',    'disponit_modul_eier'),
    ('FUNCTION', 'pensjoner_release(text,text,text,text)',            'disponit_modul_eier'),
    ('FUNCTION', 'noddeaktiver_modul(text,text,text)',               'disponit_modul_eier'),
    ('FUNCTION', 'reaktiver_modul(text,bigint,text)',                'disponit_modul_eier'),
    -- PR-014b: domene/artefakt-funksjonene, eid av disponit_domene_eier.
    ('FUNCTION', 'utsted_challenge(text,text,boolean,text,text)',     'disponit_domene_eier'),
    ('FUNCTION', 'verifiser_domenekontroll(text,text,boolean,text)',  'disponit_domene_eier'),
    ('FUNCTION', 'revalider_domenekontroll(text,text,text)',          'disponit_domene_eier'),
    ('FUNCTION', 'tilbakekall_domenekontroll(text,text,text,text)',   'disponit_domene_eier'),
    ('FUNCTION', 'avgjor_domeneovertakelse(text,text,bigint,boolean,text)', 'disponit_domene_eier'),
    -- 027: varselsenderens kryss-tenant-vindu. Samme eier og samme grunn —
    -- én rolle for «funksjoner som med vilje ser på tvers av tenanter».
    ('FUNCTION', 'varsel_klaim_epost(integer,integer)',              'disponit_domene_eier'),
    ('FUNCTION', 'varsel_sett_epoststatus(bigint,uuid,text,text)',        'disponit_domene_eier'),
    ('FUNCTION', 'varsel_rekoe(interval,integer,interval)',          'disponit_domene_eier'),
    ('FUNCTION', 'registrer_artefakttype(text,text,integer,text,text,text)', 'disponit_domene_eier'),
    ('FUNCTION', 'lagre_artefakt_staged(text,bigint,text,text,text,integer,text,bigint,integer,text,bytea,bytea,text,text)', 'disponit_domene_eier'),
    ('FUNCTION', 'promoter_artefakt(uuid,text,bigint,text,bigint,text,text)', 'disponit_domene_eier'),
    ('FUNCTION', 'rydd_staged_artefakter()',                          'disponit_domene_eier'),
    ('FUNCTION', 'karantenesett_artefakt(uuid,text,bigint)',         'disponit_domene_eier'),
    ('FUNCTION', 'bevar_artefakt(uuid,text,bigint,text)',                 'disponit_domene_eier'),
    -- PR-014b CP5: artefakt-opplastingskapabilitet. Den frittstående brenneren
    -- `bruk_artefaktkapabilitet` er fjernet (forbruk skjer i staged-writen).
    ('FUNCTION', 'utsted_artefaktkapabilitet(text,bigint,text,text,integer,text,bigint,text,text,integer)', 'disponit_domene_eier'),
    ('FUNCTION', 'innlos_artefaktkapabilitet(text,text)',            'disponit_domene_eier'),
    -- PR-015: fire øyne + de bundne driftsformene (migrasjon 019). Samme eier
    -- som resten av domenelaget — avgjørelsen er iboende kryss-tenant, og
    -- `rydd_staged_artefakter(integer)` er 016-regelen med en bunn, ikke en ny
    -- regel, så den hører hjemme hos samme rolle som 0-argumentsformen.
    -- 8. argument (`p_bruker_id`) kom med reautoriseringen av tellende
    -- stemmer: signaturen her MÅ følge migrasjon 019, ellers finner
    -- reparasjonsløkka ingen funksjon å eie og den reelle funksjonen blir
    -- behandlet som en vanlig, eierløs funksjon ved neste kjøring.
    ('FUNCTION', 'avgi_overtakelse_attestasjon(text,bigint,text,text,text,text,bigint,text)', 'disponit_domene_eier'),
    -- `lukk_overtakelsessak` kalles NESTET fra avgi_overtakelse_attestasjon og
    -- må derfor ha samme eier: utelatt herfra ville reparasjonssløyfa nedenfor
    -- behandlet den som en vanlig funksjon og flyttet den til
    -- disponit_migrator ved andre kjøring av `oppsett-postgresql.sh`. 019
    -- hoppes da over på sjekksum, og siden EXECUTE er revoket fra PUBLIC uten
    -- en grant tilbake til disponit_domene_eier, ville hver senere
    -- godkjenning/avvisning truffet permission denied i det nestede kallet og
    -- rullet tilbake selve domenevedtaket.
    ('FUNCTION', 'lukk_overtakelsessak(text,bigint,text,text)',       'disponit_domene_eier'),
    ('FUNCTION', 'degrader_forbigatte_utfordrere(text,text)',         'disponit_domene_eier'),
    -- Triggerfunksjonen på `hostname_binding` (019 §3.25) er SECURITY DEFINER
    -- og MÅ eies av samme rolle som funksjonen den kaller. Havnet den hos
    -- disponit_migrator ved andre kjøring av `oppsett-postgresql.sh`, ville
    -- degraderingen kjørt med migratorens rettigheter i stedet for
    -- domenelagets — en stille privilegieutvidelse på en vei ingen kaller
    -- eksplisitt.
    ('FUNCTION', 'trg_degrader_forbigatte_utfordrere()',              'disponit_domene_eier'),
    ('FUNCTION', 'antall_avgitte_attestasjoner(bigint,bigint)',       'disponit_domene_eier'),
    ('FUNCTION', 'rydd_staged_artefakter(integer)',                   'disponit_domene_eier'),
    ('FUNCTION', 'antall_karantenesatte()',                           'disponit_domene_eier'),
    -- Revalideringsscheduleren. Eid av domene_eier fordi den MÅ lese
    -- `domenekontroll` på tvers av tenanter (BYPASSRLS) for å regne budsjettet
    -- på riktig nevner — arbeiderrollen har bevisst ingen bordtilgang.
    ('FUNCTION', 'revalideringskandidater(integer,integer,integer,integer,integer)', 'disponit_domene_eier'),
    ('FUNCTION', 'revalideringspopulasjon()',                         'disponit_domene_eier'),
    -- Bevisporten foran 016s revalidering. Må ha SAMME eier som
    -- `revalider_domenekontroll(text,text,text)`: den delegerer til den
    -- nestet, og eierskapet er det eneste som gir den EXECUTE der.
    ('FUNCTION', 'revalider_domenekontroll(text,text,text,text[])',   'disponit_domene_eier');

DO $$
DECLARE
    r RECORD;
BEGIN
    -- 1. Designobjekter til sin designede eier (retning: flatet -> design).
    --    Objekter som ikke finnes ennå (fersk base før migrasjonene)
    --    hoppes stille over — migrasjonene oppretter dem riktig selv.
    FOR r IN
        SELECT 'TABLE' AS art, c.oid::regclass::text AS ident, d.eier
          FROM _design d
          JOIN pg_class c ON d.art = 'TABLE'
           AND c.oid = to_regclass('public.' || d.ident)
         WHERE pg_get_userbyid(c.relowner) <> d.eier
        UNION ALL
        SELECT 'FUNCTION', p.oid::regprocedure::text, d.eier
          FROM _design d
          JOIN pg_proc p ON d.art = 'FUNCTION'
           AND p.oid = to_regprocedure('public.' || d.ident)
         WHERE pg_get_userbyid(p.proowner) <> d.eier
    LOOP
        EXECUTE format('ALTER %s %s OWNER TO %I', r.art, r.ident, r.eier);
        RAISE NOTICE 'eierskap: % % -> %', r.art, r.ident, r.eier;
    END LOOP;

    -- 2. Alt annet ikke-extension i public til migrator (retning:
    --    feilplassert/postgres/runtime -> migrator). Dekker også ordinære
    --    objekter som har havnet hos en privilegert rolle.
    FOR r IN
        SELECT 'TABLE' AS art, c.oid::regclass::text AS ident
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
           AND pg_get_userbyid(c.relowner) <> 'disponit_migrator'
           AND NOT EXISTS (SELECT 1 FROM _design d WHERE d.art = 'TABLE'
                            AND to_regclass('public.' || d.ident) = c.oid)
           AND NOT EXISTS (SELECT 1 FROM pg_depend dep
                            WHERE dep.classid = 'pg_class'::regclass
                              AND dep.objid = c.oid
                              AND dep.refclassid = 'pg_extension'::regclass
                              AND dep.deptype = 'e')
        UNION ALL
        SELECT 'FUNCTION', p.oid::regprocedure::text
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.prokind = 'f'
           AND pg_get_userbyid(p.proowner) <> 'disponit_migrator'
           -- Event-trigger-funksjoner er UTENFOR modellen med vilje:
           -- `disponit_vern_policy_retention` eies av superbrukeren
           -- nettopp for at migrator ikke skal kunne røre vakten
           -- (GO-vilkår V3). En reparasjon som tok den, ville demontert
           -- vernet den selv er en del av.
           AND p.prorettype <> 'event_trigger'::regtype
           AND NOT EXISTS (SELECT 1 FROM _design d WHERE d.art = 'FUNCTION'
                            AND to_regprocedure('public.' || d.ident) = p.oid)
           AND NOT EXISTS (SELECT 1 FROM pg_depend dep
                            WHERE dep.classid = 'pg_proc'::regclass
                              AND dep.objid = p.oid
                              AND dep.refclassid = 'pg_extension'::regclass
                              AND dep.deptype = 'e')
    LOOP
        EXECUTE format('ALTER %s %s OWNER TO %I', r.art, r.ident,
                       'disponit_migrator');
        RAISE NOTICE 'eierskap: % % -> disponit_migrator', r.art, r.ident;
    END LOOP;

    -- 3. SLUTTKONTROLL — fail-hard. Etter reparasjonen skal HVERT
    --    ikke-extension-objekt i public eies av nøyaktig sin designede
    --    eier eller migrator. Står noe igjen (f.eks. en eier reparatøren
    --    ikke har medlemskap i), skal kjøringen FEILE — ikke advare.
    FOR r IN
        SELECT c.oid::regclass::text AS ident,
               pg_get_userbyid(c.relowner) AS eier
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
           AND pg_get_userbyid(c.relowner) <> COALESCE(
                   (SELECT d.eier FROM _design d WHERE d.art = 'TABLE'
                     AND to_regclass('public.' || d.ident) = c.oid),
                   'disponit_migrator')
           AND NOT EXISTS (SELECT 1 FROM pg_depend dep
                            WHERE dep.classid = 'pg_class'::regclass
                              AND dep.objid = c.oid
                              AND dep.refclassid = 'pg_extension'::regclass
                              AND dep.deptype = 'e')
        UNION ALL
        SELECT p.oid::regprocedure::text, pg_get_userbyid(p.proowner)
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.prokind = 'f'
           AND p.prorettype <> 'event_trigger'::regtype   -- V3, som over
           AND pg_get_userbyid(p.proowner) <> COALESCE(
                   (SELECT d.eier FROM _design d WHERE d.art = 'FUNCTION'
                     AND to_regprocedure('public.' || d.ident) = p.oid),
                   'disponit_migrator')
           AND NOT EXISTS (SELECT 1 FROM pg_depend dep
                            WHERE dep.classid = 'pg_proc'::regclass
                              AND dep.objid = p.oid
                              AND dep.refclassid = 'pg_extension'::regclass
                              AND dep.deptype = 'e')
    LOOP
        RAISE EXCEPTION
            'eierskapsreparasjon: % eies fortsatt av % — utenfor designmodellen',
            r.ident, r.eier;
    END LOOP;
END $$;

COMMIT;
