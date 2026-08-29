-- 069: tidligsletting — «Slett» i flaten er en bestilling reaperen
-- fullbyrder (eierbestilling 29/8, mobilflate-redesignet)
--
-- Fristen og lukkingen er IMMUTABLE by design (port 20): ingen kan
-- flytte dem, heller ikke for å slette tidligere. Tidligslettingen får
-- derfor sitt EGET, enveis merke: `slett_bestilt_ts` settes én gang av
-- den herdede døren, og reaperen tar prosessen i første sveip. §5 gir
-- retningen: kortere frist er alltid lovlig — det er forlengelse som
-- er forbudt. Selve slettingen skjer i reaperen, med alle portene den
-- alt har (seks lagre + makulering av artefaktet i samme transaksjon).
--
-- Kroppene er 067 ORDRETT (radvakten OG reaperen — 067 er
-- gjeldende kropp for begge), diff-endret (SPEIL-presedensen; 057-
-- kopien i første utkast overskrev 067s makulerings-armer — målt av
-- portene på 55432-riggen før noe ble pushet).

ALTER TABLE rekrutteringsprosess
    ADD COLUMN IF NOT EXISTS slett_bestilt_ts TIMESTAMPTZ;

-- ------------------------------------------------------------
-- 1. Radvakten lærer merket (067-kroppen, diff-endret).
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
        IF NEW.lukket_ts IS NOT NULL OR NEW.slettet_ts IS NOT NULL
           OR NEW.slett_bestilt_ts IS NOT NULL THEN
            -- ... og slettebestillingen er en overgang på samme linje
            -- (CodeRabbit på 069): en fødsel MED merket ville vært
            -- gravsteinsklassen fra lagervakten — en prosess født for å
            -- reapes, uten at noen bestilling fantes å revidere.
            RAISE EXCEPTION 'rekrutteringsprosess: en prosess fødes ÅPEN —'
                ' lukking, slettebestilling og reap-merke er egne, målte'
                ' overganger'
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
    -- TIDLIGSLETTINGEN ER EN ENVEIS BESTILLING (#173-flatens Slett,
    -- eierbestilling 29/8): merket settes én gang, fra NULL, aldri
    -- frem i tid — og fjernes eller flyttes aldri. Det KORTER kundens
    -- frist (reaperen tar prosessen i neste sveip), og §5 sier
    -- eksplisitt at kortere alltid er lovlig retning. Selve slettingen
    -- gjør reaperen — merket er bestillingen, ikke handlingen.
    IF NEW.slett_bestilt_ts IS DISTINCT FROM OLD.slett_bestilt_ts THEN
        IF OLD.slett_bestilt_ts IS NOT NULL THEN
            RAISE EXCEPTION 'rekrutteringsprosess: tidligslettingen er'
                ' alt bestilt og kan ikke flyttes eller angres'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.slett_bestilt_ts IS NULL
           OR NEW.slett_bestilt_ts > pg_catalog.now() THEN
            RAISE EXCEPTION 'rekrutteringsprosess: slettebestillingen'
                ' settes til NÅ — aldri fjernes, aldri frem i tid'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
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
        -- Predikatet er DØRENS RETAINED-ARM, ordrett: de tre
        -- payloadbærende retained-tilstandene, umerket, med levende
        -- ciphertext/nonce. Da er «døren har tømt alt» og «vakten
        -- slipper merket gjennom» nøyaktig samme spørsmål, og ingen
        -- tredje form kan oppstå mellom dem.
        --
        -- `staged` STÅR MED VILJE IKKE HER, selv om døren nå tar den
        -- (Codex P1 over). Vakten finnes for det UGJENOPPRETTELIGE:
        -- merket utelukker prosessen fra reaperen for ALLTID, og de tre
        -- retained-tilstandene har ingen annen sveiper — blir de stående,
        -- blir de stående for godt. `staged` har sin egen dør i tillegg
        -- til vår (`rydd_staged_artefakter`), så et staged artefakt som
        -- slapp forbi er en forsinkelse, ikke en permanent lekkasje.
        -- Døren tar den likevel, fordi fristen er kundens og ikke
        -- ryddejobbens; vakten avviser den ikke, fordi en vakt som
        -- blokkerer merket på noe en annen timer rydder ville stanset
        -- reaperen på en tilstand den ikke eier.
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

-- ------------------------------------------------------------
-- 2. Døren. Eid av claimeren (ankerets funksjonseier); runtime får
-- EXECUTE i migrer.py RETTIGHETER, som for opprett/lukk. Idempotent:
-- en alt bestilt eller alt reapet prosess er et stille ja — knappen
-- kan trykkes to ganger uten å bli en feil. En ULUKKET prosess lukkes
-- i samme transaksjon (fristen skal løpe fra avslutningen, og en
-- tidligslettet prosess ER avsluttet) — gjennom døren som eier
-- lukkingen, aldri et speilet UPDATE.
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION bestill_tidligsletting(
    p_tenant TEXT, p_prosess_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'bestill_tidligsletting');
    SELECT lukket_ts, slettet_ts, slett_bestilt_ts INTO v_rad
      FROM public.rekrutteringsprosess
     WHERE tenant = p_tenant AND prosess_id = p_prosess_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'rekrutteringsprosess: % finnes ikke hos %',
            p_prosess_id, p_tenant
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_rad.slettet_ts IS NOT NULL
       OR v_rad.slett_bestilt_ts IS NOT NULL THEN
        RETURN;  -- idempotent: alt slettet eller alt bestilt
    END IF;
    IF v_rad.lukket_ts IS NULL THEN
        PERFORM public.lukk_rekrutteringsprosess(
            p_tenant, p_prosess_id, pg_catalog.now());
    END IF;
    UPDATE public.rekrutteringsprosess
       SET slett_bestilt_ts = pg_catalog.now()
     WHERE tenant = p_tenant AND prosess_id = p_prosess_id;
END $$;
RESET ROLE;
REVOKE ALL ON FUNCTION bestill_tidligsletting(TEXT, UUID) FROM PUBLIC;

-- ------------------------------------------------------------
-- 3. Reaperen tar bestilte slettinger i første sveip (067-kroppen,
-- diff-endret i ETT predikat). `CREATE OR REPLACE` beholder eier og
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
           AND (v_naa > coalesce(p.lukket_ts, p.opprettet)
                        + p.slettefrist_dogn * interval '1 day'
                -- Tidligslettingen (069): en bestilt sletting reapes i
                -- FØRSTE sveip, uavhengig av fristen — merket er
                -- kundens egen korting av den.
                OR p.slett_bestilt_ts IS NOT NULL)
         -- Bestilte slettinger FØRST (CodeRabbit): kunden har bedt
         -- eksplisitt, og et fullt sveip (p_grense) av frist-utløpte
         -- skal ikke skyve bestillingen til neste runde.
         ORDER BY (p.slett_bestilt_ts IS NULL),
                  coalesce(p.lukket_ts, p.opprettet)
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
