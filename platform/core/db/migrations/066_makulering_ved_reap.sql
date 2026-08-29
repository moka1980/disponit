-- 066: reaperen makulerer den promoterte rapporten, og frist-feilingen
-- lukker ankeret (#222 — andre halvdel av Codex P1-2 på #220, pluss
-- eiers tillegg fra samme tråd)
--
-- TO FUNN, ÉN FRIST:
--
-- 1. `reap_kandidatdata` (057) nullet de seks kandidatlagrene og merket
--    prosessen — men rørte aldri `artefakt`. Den promoterte
--    evalueringsrapporten bærer per kandidat `funn`, `intervjusporsmal`
--    og hele den blindede `kildetekst`, kryptert på tenantens DEK, og
--    besto forbi retensjonsfristen. #220 lukket LESESIDEN (identisk 404
--    / `rapport_klar: false` på reapede prosesser); dette er selve
--    makuleringen. Kravet var en migrasjon fordi artefakt er
--    append-only/immutabelt: statemaskinens ENESTE payload-nulling var
--    overgangen staged → forkastet.
--
-- 2. `reap_evidensfrister` (056) flytter et utløpt claimet M-57-oppdrag
--    direkte til `feilet` i SQL — utenfor `_ingest_kvittering`, så
--    kvitteringsveiens ankerlukking aldri nås. Fristen regnes da fra
--    `opprettet` (forlatt-fallbacken), og delvis produserte
--    kandidatdata kan reapes inntil hele kjøretiden FOR TIDLIG i
--    forhold til kundens frist målt fra avslutningen. Reaperen lukker
--    nå matchende åpent anker i SAMME transaksjon som frist-feilingen.
--
-- FORMEN: makuleringen er en NAVNGITT mutasjon i statemaskinen —
-- `makulert_ts` settes én gang, i samme update som nuller begge
-- payloadfeltene, uten tilstandsskifte. Tilstanden består fordi raden
-- fortsatt ER evidensen om at rapporten fantes (hash, bindinger,
-- promotert_ts); det som slettes er kandidatpayloaden kunden fikk
-- frist på. `bevart` og `karantene` makuleres av samme grunn som
-- `promotert`: GDPR-fristen ser payloaden, ikke tilstandsmaskinen vår.
-- `staged` har sin egen dør (forkastelsen), og `forkastet` bærer
-- ingenting.
--
-- Kroppene er 016/056/057 ORDRETT, diff-endret (SPEIL-presedensen fra
-- 062/065: aldri skriv naboens dør fra hukommelsen).

-- ------------------------------------------------------------
-- 1. Merket og dets ærlighetskrav. Kolonnen er nullbar og settes av
-- makuleringen alene; CHECK-en binder den til formen — et merke uten
-- nullet payload er en løgn begge veier.
ALTER TABLE artefakt ADD COLUMN IF NOT EXISTS makulert_ts TIMESTAMPTZ;
DO $$ BEGIN
    ALTER TABLE artefakt ADD CONSTRAINT artefakt_makulert_uten_payload
        CHECK (makulert_ts IS NULL
               OR (ciphertext IS NULL AND nonce IS NULL));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ------------------------------------------------------------
-- 2. Statemaskinen lærer makuleringsformen (016-kroppen, diff-endret).
CREATE OR REPLACE FUNCTION artefakt_statemaskin()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.artefakt_id  IS DISTINCT FROM OLD.artefakt_id
       OR NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.oppdrag_id IS DISTINCT FROM OLD.oppdrag_id
       OR NEW.artefakttype IS DISTINCT FROM OLD.artefakttype
       OR NEW.modul_id IS DISTINCT FROM OLD.modul_id
       OR NEW.release_id IS DISTINCT FROM OLD.release_id
       OR NEW.kontraktversjon IS DISTINCT FROM OLD.kontraktversjon
       OR NEW.kontrakt_hash IS DISTINCT FROM OLD.kontrakt_hash
       OR NEW.module_epoch IS DISTINCT FROM OLD.module_epoch
       OR NEW.klartekst_sha256 IS DISTINCT FROM OLD.klartekst_sha256
       OR NEW.kapabilitet_jti IS DISTINCT FROM OLD.kapabilitet_jti
       -- Codex: dek_ref MÅ fryses. Krypteringen binder AES-GCM-AAD til
       -- tenant|key_id (db/kryptering.py); en repointing til en annen nøkkel
       -- ville gjort artefaktet UDEKRYPTERBART mens ciphertext/hash/tilstand står
       -- urørt — bevaret evidens stille korrumpert.
       OR NEW.dek_ref IS DISTINCT FROM OLD.dek_ref
       OR NEW.storrelse_bytes IS DISTINCT FROM OLD.storrelse_bytes THEN
        RAISE EXCEPTION 'artefakt: identitet/binding/hash er frosset';
    END IF;
    IF NOT (
        (OLD.tilstand = 'staged'
         AND NEW.tilstand IN ('staged','promotert','forkastet','karantene','bevart')) OR
        (OLD.tilstand = NEW.tilstand)
    ) THEN
        RAISE EXCEPTION 'artefakt: ulovlig tilstandsovergang % -> %',
            OLD.tilstand, NEW.tilstand;
    END IF;
    -- Codex: 'karantene' og 'bevart' er RETAINED (ryddes aldri) — 'karantene' er
    -- artefaktet til en kvittering som ikke lot seg verifisere (epoch/binding),
    -- 'bevart' er artefaktet til en godtatt SEN kvittering (evidens som aldri
    -- kan promoteres). Begge terminale.
    IF OLD.tilstand IN ('promotert','forkastet','karantene','bevart')
       AND NEW.tilstand <> OLD.tilstand THEN
        RAISE EXCEPTION 'artefakt: % er terminal', OLD.tilstand;
    END IF;
    -- MAKULERINGEN (#222) er den ANDRE navngitte nullingen, og den har én
    -- form: `makulert_ts` settes én gang, fra NULL, i SAMME update som
    -- nuller BEGGE payloadfeltene, uten tilstandsskifte, og bare på de
    -- tre retained-tilstandene som kan bære payload. Merket er ikke
    -- pynt — det er forskjellen på «payload slettet ved frist» og
    -- «evidens stille korrumpert», som er nøyaktig klassen vakten under
    -- finnes for. Tilstanden består: raden ER fortsatt den promoterte
    -- evidensen om at rapporten fantes (hash, bindinger, promotert_ts),
    -- den bærer bare ikke kandidatpayload forbi kundens §5-frist.
    IF NEW.makulert_ts IS DISTINCT FROM OLD.makulert_ts THEN
        IF NOT (OLD.makulert_ts IS NULL
                AND NEW.makulert_ts IS NOT NULL
                AND NEW.tilstand = OLD.tilstand
                AND OLD.tilstand IN ('promotert','bevart','karantene')
                AND NEW.ciphertext IS NULL AND NEW.nonce IS NULL) THEN
            RAISE EXCEPTION 'artefakt: makulert_ts settes én gang, av '
                'makuleringen selv — samme update nuller payloaden, '
                'tilstanden består';
        END IF;
    END IF;
    -- Codex: ciphertext/nonce er KOBLET til forkastelsen. Uten koblingen kunne en
    -- feilaktig privilegert UPDATE nulle nyttelasten til et staged/promotert/
    -- retained artefakt mens tilstand + hash fortsatt påsto at evidensen fantes.
    -- To lovlige mutasjoner: SAMME update setter staged → forkastet og nuller
    -- BEGGE feltene, eller SAMME update makulerer (#222) — nuller begge og
    -- setter merket; formkravene til den armen håndheves i sin helhet over.
    IF NEW.ciphertext IS DISTINCT FROM OLD.ciphertext
       OR NEW.nonce IS DISTINCT FROM OLD.nonce THEN
        IF NOT ((OLD.tilstand = 'staged' AND NEW.tilstand = 'forkastet'
                 AND NEW.ciphertext IS NULL AND NEW.nonce IS NULL)
                OR (NEW.makulert_ts IS NOT NULL
                    AND OLD.makulert_ts IS NULL
                    AND NEW.ciphertext IS NULL AND NEW.nonce IS NULL)) THEN
            RAISE EXCEPTION 'artefakt: ciphertext/nonce kan kun nulles i '
                'overgangen staged -> forkastet, eller av makuleringen';
        END IF;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS artefakt_laas ON artefakt;
CREATE TRIGGER artefakt_laas BEFORE UPDATE ON artefakt
    FOR EACH ROW EXECUTE FUNCTION artefakt_statemaskin();

-- ------------------------------------------------------------
-- 3. Makuleringsdøren. Eid av artefakt-autoriteten (domene_eier, som
-- eier resten av 016-familien og alt har UPDATE + BYPASSRLS på
-- artefakt); reaperen får EXECUTE på DØREN, aldri UPDATE på
-- evidenstabellen. `krev_tenantkontekst` binder parameteret til
-- konteksten reaperen alt setter per rad — fail-closed, som resten.
SET LOCAL ROLE disponit_domene_eier;
CREATE OR REPLACE FUNCTION makuler_artefakter_for_prosess(
    p_tenant TEXT, p_oppdrag_id BIGINT, p_naa TIMESTAMPTZ)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_antall INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'makuler_artefakter_for_prosess');
    UPDATE public.artefakt a
       SET ciphertext = NULL, nonce = NULL, makulert_ts = p_naa
     WHERE a.tenant = p_tenant AND a.oppdrag_id = p_oppdrag_id
       AND a.tilstand IN ('promotert','bevart','karantene')
       AND a.makulert_ts IS NULL
       AND (a.ciphertext IS NOT NULL OR a.nonce IS NOT NULL);
    GET DIAGNOSTICS v_antall = ROW_COUNT;
    RETURN v_antall;
END $$;
RESET ROLE;
REVOKE ALL ON FUNCTION makuler_artefakter_for_prosess(TEXT, BIGINT,
    TIMESTAMPTZ) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION makuler_artefakter_for_prosess(TEXT, BIGINT,
    TIMESTAMPTZ) TO disponit_m37_claimer;

-- ------------------------------------------------------------
-- 4. Reaperen kaller døren i samme iterasjon som de seks lagrene
-- (057-kroppen, diff-endret). `CREATE OR REPLACE` beholder eier og
-- grants (timerrolleblokka i 057).
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION reap_kandidatdata(p_grense INT DEFAULT 50)
RETURNS TABLE (tenant TEXT, prosess_id UUID)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_kontekst TEXT; v_naa TIMESTAMPTZ;
BEGIN
    v_kontekst := current_setting('disponit.tenant', true);
    v_naa := pg_catalog.now();
    FOR r IN
        -- Den FORLATTE prosessen (Codex P1): fristen løper fra lukkingen
        -- (§5), men en kjøring som krasjer eller kanselleres før
        -- `lukk_rekrutteringsprosess` etterlot en prosess som ALDRI ble
        -- lukket — og et predikat på `lukket_ts IS NOT NULL` utelukket
        -- den for alltid. Originaldokumentene og alt avledet ble stående
        -- i det uendelige, uansett hvor ferdig oppdraget var.
        -- Maks levetid er derfor den samme fristen målt fra FØDSELEN:
        -- ingen ny konstant, og strengere enn den lukkede veien (en
        -- prosess som lukkes, får alltid hele fristen fra lukkingen).
        -- Utførelsesfristen er 240 min, så en prosess som står åpen
        -- forbi hele slettefristen er forlatt, ikke i arbeid.
        SELECT p.tenant AS t, p.prosess_id AS pid, p.oppdrag_id AS oid
          FROM public.rekrutteringsprosess p
         WHERE p.slettet_ts IS NULL
           AND v_naa > coalesce(p.lukket_ts, p.opprettet)
                       + p.slettefrist_dogn * interval '1 day'
         ORDER BY coalesce(p.lukket_ts, p.opprettet)
         LIMIT p_grense
         FOR UPDATE OF p SKIP LOCKED
    LOOP
        PERFORM set_config('disponit.tenant', r.t, true);
        UPDATE public.kandidat_originaldokument k
           SET dokument = NULL, filnavn = NULL, innholdstype = NULL,
               storrelse_bytes = NULL, slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        UPDATE public.kandidat_parsettekst k
           SET tekst = NULL, slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        UPDATE public.kandidat_evalueringsartefakt k
           SET artefakt = NULL, slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        UPDATE public.kandidat_intervjusporsmal k
           SET sporsmal = NULL, slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        UPDATE public.kandidat_utsendingsdata k
           SET mottaker_ref = NULL, flettefelt = NULL, slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        UPDATE public.kandidat_avmaskering k
           SET felter = NULL, slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        -- DEN PROMOTERTE RAPPORTEN ER OGSÅ KANDIDATPAYLOAD (#222, andre
        -- halvdel av Codex P1-2 på #220): den bærer funn,
        -- intervjuspørsmål og hele den blindede kildeteksten per
        -- kandidat, kryptert på tenantens DEK — og besto forbi fristen,
        -- fordi ingen reaper rørte `artefakt`. Makuleringen skjer i
        -- SAMME iterasjon og transaksjon som de seks lagrene: det finnes
        -- ingen vei gjennom denne funksjonen der lagrene tømmes og
        -- rapporten består. Døren eies av artefakt-autoriteten
        -- (domene_eier, 016-familien) — reaperen SPØR den, den får aldri
        -- rå UPDATE på evidenstabellen (#181-formen).
        PERFORM public.makuler_artefakter_for_prosess(r.t, r.oid, v_naa);
        -- En forlatt prosess lukkes ved FØDSELEN i samme setning som den
        -- reapes: `prosess_reapet_krever_lukket` skal fortsatt holde, og
        -- radvakten godtar nettopp denne retningen (lukking bakover
        -- korter fristen, den forlenger den aldri).
        UPDATE public.rekrutteringsprosess p2
           SET lukket_ts = coalesce(p2.lukket_ts, p2.opprettet),
               slettet_ts = v_naa
         WHERE p2.tenant = r.t AND p2.prosess_id = r.pid;
        tenant := r.t; prosess_id := r.pid;
        RETURN NEXT;
    END LOOP;
    PERFORM set_config('disponit.tenant', coalesce(v_kontekst, ''), true);
END $$;
RESET ROLE;

-- ------------------------------------------------------------
-- 5. Frist-feilingen lukker ankeret (056-kroppen, diff-endret).
-- `CREATE OR REPLACE` beholder eier og grants (timerrollen).
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION reap_evidensfrister(p_grense INT DEFAULT 200)
RETURNS TABLE (tenant TEXT, oppdrag_id BIGINT, unntak_id BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_sak BIGINT; v_rid TEXT; v_kontekst TEXT;
        v_kandidat BIGINT; v_pid UUID;
BEGIN
    v_rid := 'reap-' || replace(gen_random_uuid()::text, '-', '');
    v_kontekst := current_setting('disponit.tenant', true);
    FOR r IN
        SELECT o.tenant AS t, o.id AS oid FROM public.oppdrag o
         WHERE o.opprinnelse IN ('beslutning', 'frigivelse')
           AND o.status IN ('opprettet', 'plukket')
           AND now() > o.evidensfrist
         ORDER BY o.evidensfrist
         LIMIT p_grense
         FOR UPDATE OF o SKIP LOCKED
    LOOP
        PERFORM set_config('disponit.tenant', r.t, true);
        -- SAKEN, MED SAMME REGEL SOM OPPDRAGET (043 §9): finnes den alt,
        -- må låsen være ledig — ellers er dette sveipets kandidat, ikke
        -- dette sveipets rad.
        SELECT u.id INTO v_kandidat FROM public.unntak u
         WHERE u.tenant = r.t AND u.oppdrag_id = r.oid
           AND u.arsak = 'evidensfrist' AND NOT u.terminal;
        IF v_kandidat IS NOT NULL THEN
            PERFORM 1 FROM public.unntak u
             WHERE u.tenant = r.t AND u.id = v_kandidat
               FOR UPDATE SKIP LOCKED;
            IF NOT FOUND THEN
                CONTINUE;
            END IF;
        END IF;
        v_sak := public.sikre_sak_for_oppdrag(
            r.t, r.oid, 'evidensfrist', 'evidensreaper', v_rid);
        UPDATE public.oppdrag o SET status = 'feilet'
         WHERE o.tenant = r.t AND o.id = r.oid
           AND o.status IN ('opprettet', 'plukket');
        -- RETENSJONSANKERET LUKKES AV SAMME TRANSAKSJON SOM FRIST-FEILER
        -- OPPDRAGET (#222, Codex på #220 9ca3aca4): denne veien flytter
        -- et utløpt claimet M-57-oppdrag til `feilet` UTENFOR
        -- `_ingest_kvittering`, så kvitteringsveiens ankerlukking nås
        -- aldri — fristen falt da til forlatt-fallbacken målt fra
        -- `opprettet`, og kandidatdata kunne reapes inntil hele
        -- kjøretiden for tidlig i forhold til kundens frist målt fra
        -- avslutningen. Lukkingen går gjennom DØREN, ikke et speilet
        -- UPDATE (#181-formen), og oppslaget er type-agnostisk: bare
        -- M-57-oppdrag HAR et anker — samme form som kvitteringsveien.
        -- LÅST lesning (CodeRabbit): kvitteringsveien kan lukke ankeret
        -- med SITT tidspunkt mellom et ulåst oppslag og dørkallet, og
        -- døren feller da et AVVIKENDE tidspunkt med vilje
        -- (unique_violation) — hele reap-transaksjonen ville rullet.
        -- `FOR UPDATE` venter og RE-EVALUERER predikatet: et anker som
        -- ble lukket under ventingen faller ut av treffet, og reaperen
        -- går videre i stedet for å dø på et kappløp den ikke eier.
        SELECT p.prosess_id INTO v_pid FROM public.rekrutteringsprosess p
         WHERE p.tenant = r.t AND p.oppdrag_id = r.oid
           AND p.lukket_ts IS NULL
           FOR UPDATE OF p;
        IF v_pid IS NOT NULL THEN
            PERFORM public.lukk_rekrutteringsprosess(
                r.t, v_pid, pg_catalog.now());
        END IF;
        tenant := r.t; oppdrag_id := r.oid; unntak_id := v_sak;
        RETURN NEXT;
    END LOOP;
    PERFORM set_config('disponit.tenant', coalesce(v_kontekst, ''), true);
END $$;

RESET ROLE;

-- ------------------------------------------------------------
-- 6. DEN BEBODDE BASEN MAKULERES ÉN GANG (Cursor P1 på #252).
--
-- Alt over gjelder FREMTIDIGE reap: en prosess plukkes bare mens
-- `slettet_ts IS NULL`. #222 finnes fordi promoterte rapporter
-- overlevde §5-fristen — og de som alt er reapet, av reaperen slik
-- den sto FØR denne migrasjonen, bærer merket allerede. De plukkes
-- derfor aldri igjen: uten dette steget beholder de ciphertext, og
-- migrasjonen lukker hullet bare for det som ennå ikke har skjedd.
--
-- Formen er dørens, ikke et speilet UPDATE: samme
-- `krev_tenantkontekst`, samme predikat, samme statemaskin.
-- Idempotent per konstruksjon
-- (`makulert_ts IS NULL` + levende payload), så en gjenkjøring — eller
-- en rad reaperen rakk først — er null rader, ikke en feil.
--
-- ROLLEN ER PÅKREVD, ikke pynt: `rekrutteringsprosess` har FORCE ROW
-- LEVEL SECURITY, og migrator har ingen tenantkontekst å arve. Uten
-- `SET LOCAL ROLE` ville løkken sett NULL rader og backfillen vært en
-- stille no-op — nøyaktig den blinde formen porten finnes for.
-- Claimeren har sin egen eksplisitte policy (`m57_reaper`, 057) og
-- EXECUTE på døren.
--
-- ORDNINGEN ER OGSÅ EN PÅSTAND: masse-skrivingen står ETTER `ALTER
-- TABLE`-setningene i §1. 047-stoppet var utsatte triggerhendelser i kø
-- foran en ALTER-klasse-setning; her er DDL-en unnagjort før den første
-- raden skrives. Målt av SP-10 mot bebodd base, ikke antatt.
SET LOCAL ROLE disponit_m37_claimer;
DO $$
DECLARE r RECORD; v_kontekst TEXT; v_naa TIMESTAMPTZ; v_antall INT := 0;
BEGIN
    v_kontekst := current_setting('disponit.tenant', true);
    v_naa := pg_catalog.now();
    -- Løkken spør ALLE reapede prosesser, uten å forhåndsfiltrere på
    -- levende artefaktpayload: et EXISTS mot `artefakt` her ville lest
    -- under tenant-policyen med FEIL kontekst (den settes først per rad
    -- under), og stille hoppet over nettopp radene steget finnes for.
    -- Døren avgjør i stedet per oppdrag, med konteksten satt.
    FOR r IN
        SELECT p.tenant AS t, p.oppdrag_id AS oid
          FROM public.rekrutteringsprosess p
         WHERE p.slettet_ts IS NOT NULL
         ORDER BY p.tenant, p.oppdrag_id
    LOOP
        PERFORM set_config('disponit.tenant', r.t, true);
        v_antall := v_antall + public.makuler_artefakter_for_prosess(
            r.t, r.oid, v_naa);
    END LOOP;
    PERFORM set_config('disponit.tenant', coalesce(v_kontekst, ''), true);
    RAISE NOTICE '066: engangs-makulering av alt reapet før denne'
        ' migrasjonen — % artefakt(er) tømt', v_antall;
END $$;
RESET ROLE;

-- ------------------------------------------------------------
-- 7. VAKTEN MÅLER OGSÅ RAPPORTEN (Cursor P1 på #252, 057-kroppen
-- diff-endret). `slettet_ts`-armen krevde at de seks kandidatlagrene
-- var tømt før merket kunne settes — nettopp fordi et merke satt uten
-- tømming utelukker prosessen fra reaperen for alltid. `artefakt` var
-- ikke med i den målingen, og etter #222 er den promoterte rapporten
-- like mye kandidatpayload som lagrene. Reaperen over kaller døren FØR
-- den merker, så den lovlige veien er uendret; det som nå er umulig, er
-- en fremtidig vei som merker uten å ha makulert.
--
-- `CREATE OR REPLACE` beholder eier og grants; triggeren peker på
-- funksjonsnavnet og trenger ingen ny binding.
CREATE OR REPLACE FUNCTION rekrutteringsprosess_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    -- FØDSELEN måles her, ikke bare i funksjonen (Cursor P2). Vakten var
    -- BEFORE UPDATE OR DELETE, og runtime ble derfor fratatt tabell-INSERT
    -- i forrige runde — men CLAIMEREN må ha INSERT: den er definer for
    -- `opprett_rekrutteringsprosess`. Direkte DML som claimer gikk dermed
    -- utenom hele fødselsporten (oppdragstype, eiermodul, levende status,
    -- åpen fødsel). En vakt som bare gjelder de rettighetsløse er ingen
    -- vakt — samme lærdom som resten av denne funksjonen bygger på.
    --
    -- Porten er den SAMME som funksjonens, med vilje duplisert: funksjonen
    -- eier den låste lesningen og det lesbare utfallet
    -- (`invalid_parameter_value`), vakten er backstoppen som gjelder
    -- ENHVER rolle, også eieren, og svarer i vaktens egen kode.
    IF TG_OP = 'INSERT' THEN
        IF NEW.lukket_ts IS NOT NULL OR NEW.slettet_ts IS NOT NULL THEN
            RAISE EXCEPTION 'rekrutteringsprosess: en prosess fødes ÅPEN —'
                ' lukking og reap-merke er egne, målte overganger'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- `opprettet` er den ANDRE enden av fristen (Cursor P2): reaperens
        -- maks-levetid-arm regner fra `coalesce(lukket_ts, opprettet)`, og
        -- kolonnen er immutabel etter fødselen. En fødsel med `opprettet`
        -- frem i tid ville derfor skjøvet utløpet for en forlatt prosess
        -- stille — nøyaktig den forlengelsen port 20 finnes for å nekte,
        -- bare gjennom den andre kolonnen. Bakover er lovlig: det KORTER
        -- levetiden, og det er retningen §5 tillater.
        IF NEW.opprettet > pg_catalog.now() THEN
            RAISE EXCEPTION 'rekrutteringsprosess: opprettet kan ikke stå'
                ' frem i tid — det ville forlenget maks levetid'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- LÅST lesning, som i funksjonen (Codex P2). Backstoppen leste
        -- ULÅST, og en ulåst sjekk er en påstand om fortiden: under READ
        -- COMMITTED kunne en samtidig overgang til `feilet`/`kansellert`
        -- committe mellom sjekken og INSERT-en, og prosessen ble
        -- født på et alt terminalt oppdrag. FK-en redder ikke: den tar
        -- `FOR KEY SHARE`, som ikke er i konflikt med et UPDATE av
        -- statuskolonnen. `FOR SHARE` er det, og PostgreSQL re-evaluerer
        -- predikatet etter låsen — en rad som ble terminal under
        -- ventingen faller ut av treffet i stedet for å bli lest fra et
        -- gammelt snapshot. Vakten skal være minst like sterk som
        -- funksjonen den er backstopp for; her var den svakere.
        -- PORTEN ER POSITIV, ikke en voksende denyliste (Codex P1).
        -- Den sto som `status NOT IN ('feilet','kansellert')`, og da var
        -- `utfort` lovlig: kom det FØRSTE kallet etter at kjøringen som
        -- skulle lukket prosessen var ferdig, fødtes en åpen prosess på
        -- et avsluttet oppdrag, og de seks lagrene tok imot persondata
        -- etterpå — med fristen løpende fra reaperens maks levetid, ikke
        -- fra en lukking som aldri kommer. `opprettet` hadde samme hull.
        --
        -- Å legge `utfort` til lista ville vært den fjerde runden på
        -- samme form (§9 K2): en liste over tilstandene noen kom på.
        -- Fødselen har ÉN lovlig tilstand, og kommentaren over sier den
        -- alt høyt — «dette ankeret fødes MENS kjøringen står på». Den
        -- skrives derfor ut som et krav i stedet for som et fravær:
        -- `plukket`, altså et AKTIVT CLAIMET oppdrag, samme form som
        -- 017/035 bruker for kapabilitetene. Da er det ingen femte
        -- tilstand igjen å oppdage.
        PERFORM 1 FROM public.oppdrag o
            WHERE o.tenant = NEW.tenant AND o.id = NEW.oppdrag_id
              AND o.oppdragstype = 'rekruttering.evaluering'
              AND o.eiermodul = 'm57_ats'
              AND o.status = 'plukket'
            FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'rekrutteringsprosess: oppdrag % hos % er ikke'
                ' et AKTIVT CLAIMET rekruttering.evaluering-oppdrag eid av'
                ' m57_ats — fødselen går gjennom'
                ' opprett_rekrutteringsprosess', NEW.oppdrag_id, NEW.tenant
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'rekrutteringsprosess: % avvist — raden består,'
            ' bare payloaden i lagrene reapes', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.prosess_id IS DISTINCT FROM OLD.prosess_id
       OR NEW.oppdrag_id IS DISTINCT FROM OLD.oppdrag_id
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'rekrutteringsprosess: identitetskolonnene er'
            ' immutable' USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Port 20, selve kjernen: INGEN overgang endrer fristen. Ikke
    -- modulen, ikke runtime, ikke eieren — «modulen kan ikke forlenge
    -- frist; ingen hold i v1» (§5).
    IF NEW.slettefrist_dogn IS DISTINCT FROM OLD.slettefrist_dogn THEN
        RAISE EXCEPTION 'rekrutteringsprosess: slettefristen er satt ved'
            ' fødselen og kan ikke endres (klarsignalet §5)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.lukket_ts IS DISTINCT FROM OLD.lukket_ts THEN
        IF OLD.lukket_ts IS NOT NULL THEN
            RAISE EXCEPTION 'rekrutteringsprosess: lukket_ts er alt satt'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- Fristen løper fra lukkingen. En lukking frem i tid ville
        -- skjøvet utløpet — altså forlenget fristen. Bakover korter den
        -- bare, og den retningen er lovlig (og testbar).
        IF NEW.lukket_ts > pg_catalog.now() THEN
            RAISE EXCEPTION 'rekrutteringsprosess: lukket_ts kan ikke stå'
                ' frem i tid — det ville forlenget slettefristen'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    IF NEW.slettet_ts IS DISTINCT FROM OLD.slettet_ts THEN
        IF OLD.slettet_ts IS NOT NULL THEN
            RAISE EXCEPTION 'rekrutteringsprosess: slettet_ts er alt satt'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- REAP-MERKET ER EN KONKLUSJON, IKKE EN PÅSTAND (Cursor P1).
        -- Reaperen velger bare prosesser med `slettet_ts IS NULL`, så et
        -- merke satt UTEN at lagrene er tømt utelukker prosessen fra
        -- reaping for alltid — payloaden blir stående, og evidensen sier
        -- at den er slettet. Det er den verst tenkelige formen: §5s løfte
        -- brutt og målingen selv gjort blind.
        --
        -- Merket måles derfor mot lagrene, ikke mot den som setter det:
        -- ingen levende payload igjen på prosessen. Reaperen tømmer alle
        -- seks FØR den merker ankeret, i samme transaksjon, så den lovlige
        -- veien er uendret. En rad uten payload har `slettet_ts` satt
        -- (CHECK-en binder de to begge veier), så predikatet er det samme
        -- spørsmålet lagervakten stiller per rad.
        IF EXISTS (SELECT 1 FROM public.kandidat_originaldokument k
                    WHERE k.tenant = NEW.tenant
                      AND k.prosess_id = NEW.prosess_id
                      AND k.slettet_ts IS NULL)
           OR EXISTS (SELECT 1 FROM public.kandidat_parsettekst k
                       WHERE k.tenant = NEW.tenant
                         AND k.prosess_id = NEW.prosess_id
                         AND k.slettet_ts IS NULL)
           OR EXISTS (SELECT 1 FROM public.kandidat_evalueringsartefakt k
                       WHERE k.tenant = NEW.tenant
                         AND k.prosess_id = NEW.prosess_id
                         AND k.slettet_ts IS NULL)
           OR EXISTS (SELECT 1 FROM public.kandidat_intervjusporsmal k
                       WHERE k.tenant = NEW.tenant
                         AND k.prosess_id = NEW.prosess_id
                         AND k.slettet_ts IS NULL)
           OR EXISTS (SELECT 1 FROM public.kandidat_utsendingsdata k
                       WHERE k.tenant = NEW.tenant
                         AND k.prosess_id = NEW.prosess_id
                         AND k.slettet_ts IS NULL)
           OR EXISTS (SELECT 1 FROM public.kandidat_avmaskering k
                       WHERE k.tenant = NEW.tenant
                         AND k.prosess_id = NEW.prosess_id
                         AND k.slettet_ts IS NULL) THEN
            RAISE EXCEPTION 'rekrutteringsprosess: % hos % kan ikke merkes'
                ' reapet mens et av de seks lagrene fortsatt bærer payload'
                ' — merket ville utelukket prosessen fra reaperen for'
                ' alltid (klarsignalet §5)', NEW.prosess_id, NEW.tenant
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- … OG RAPPORTEN ER DET SYVENDE LAGERET (#222, Cursor P1 på
        -- #252). Vakten over måler de seks kandidatlagrene, men den
        -- promoterte evalueringsrapporten bærer de samme personfeltene
        -- — funn, intervjuspørsmål og hele den blindede kildeteksten
        -- per kandidat. Fjernes `makuler_artefakter_for_prosess`-kallet
        -- fra `reap_kandidatdata`, settes merket fortsatt, prosessen er
        -- utelukket fra reaperen for alltid, og payloaden består: samme
        -- hull som armen over finnes for, bare i den ene tabellen den
        -- ikke så. Alle port 18-testene er grønne under den mutasjonen.
        --
        -- Predikatet er DØRENS, ordrett: de tre payloadbærende retained-
        -- tilstandene, umerket, med levende ciphertext/nonce. Da er
        -- «døren har tømt alt» og «vakten slipper merket gjennom»
        -- nøyaktig samme spørsmål, og ingen tredje form kan oppstå
        -- mellom dem.
        --
        -- SYNLIGHETEN ER MÅLT, IKKE ANTATT: `artefakt` har FORCE ROW
        -- LEVEL SECURITY med bare `tenant_isolasjon`, og vakten er ikke
        -- SECURITY DEFINER. Enhver vei som skal skrive `slettet_ts` må
        -- alt se raden i `rekrutteringsprosess` — også den er FORCE RLS
        -- — så konteksten ER radens tenant når vakten kjører; reaperen
        -- setter den per rad selv (`set_config` i løkken), claimeren har
        -- SELECT på `artefakt` fra 044. Uten kontekst ser ingen av de to
        -- tabellene noe, og merket rekker aldri hit.
        IF EXISTS (SELECT 1 FROM public.artefakt a
                    WHERE a.tenant = NEW.tenant
                      AND a.oppdrag_id = NEW.oppdrag_id
                      AND a.tilstand IN ('promotert','bevart','karantene')
                      AND a.makulert_ts IS NULL
                      AND (a.ciphertext IS NOT NULL
                           OR a.nonce IS NOT NULL)) THEN
            RAISE EXCEPTION 'rekrutteringsprosess: % hos % kan ikke merkes'
                ' reapet mens oppdragets promoterte rapport fortsatt bærer'
                ' payload — makuleringen går gjennom'
                ' makuler_artefakter_for_prosess, i samme transaksjon'
                ' (klarsignalet §5)', NEW.prosess_id, NEW.tenant
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
