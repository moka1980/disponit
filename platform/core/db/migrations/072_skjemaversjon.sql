-- 072: skjemaversjonen er relasjonell identitet (BESLUTNING-168,
-- arkitektdom 30/8 — docs/pr/BESLUTNING-168-rapportidentitet.md).
--
-- Identiteten er TUPPELEN (artefakttype, skjemaversjon), aldri navnet:
-- navneformen er prefikslukket, og at `…rapport.v2` avvises som overlapp
-- er navneregisterets eget bevis for at navnet ikke kan bære en versjon.
-- Navnet endres ikke, prefikslukkingen står urørt, og lese-API-ets par
-- trengs ikke — det kommer ikke noe nytt navn. Artefaktraden bærer
-- `skjemaversjon` med FK mot tuppelen: en v1-rapport KAN ikke leses som
-- v2, fordi raden peker på sin egen registrerte versjon (A4 gjort
-- relasjonelt, SP-12).
--
-- `payloadfri` er reap-sidens halvdel av samme dom: et payload-fritt
-- beslutningsspor (v2) er VARIG evidens og skal bestå etter reaping —
-- mens v1-rapporter bærer kandidatpayload og makuleres ved frist som i
-- 067. Egenskapen er versjonens, ikke radens: den registreres i samme
-- herdede dør som versjonen selv.

-- ------------------------------------------------------------
-- 1. Versjonstabellen. Lineær kjede med `forrige_versjon` (samme form
--    som utsendingsliste-serien, av samme grunn), nøyaktig ÉN gjeldende
--    per type, og immutabilitet med den ene lovlige overgangen
--    gjeldende -> avviklet — en avviklet versjon kan aldri bli
--    gjeldende igjen, ellers var «v2 er innført» reverserbart uten spor.
CREATE TABLE artefakttype_versjon (
    artefakttype    TEXT NOT NULL
        REFERENCES artefakttype_register (artefakttype),
    skjemaversjon   INT  NOT NULL CHECK (skjemaversjon >= 1),
    skjema_hash     TEXT NOT NULL,
    payloadfri      BOOLEAN NOT NULL DEFAULT false,
    forrige_versjon INT,
    status          TEXT NOT NULL
        CHECK (status IN ('gjeldende', 'avviklet')),
    registrert_ts   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (artefakttype, skjemaversjon),
    FOREIGN KEY (artefakttype, forrige_versjon)
        REFERENCES artefakttype_versjon (artefakttype, skjemaversjon),
    CHECK (forrige_versjon IS NULL OR forrige_versjon < skjemaversjon)
);
CREATE UNIQUE INDEX en_gjeldende_per_type
    ON artefakttype_versjon (artefakttype) WHERE status = 'gjeldende';

CREATE FUNCTION artefakttype_versjon_laas()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'artefakttype_versjon: rader slettes aldri —'
            ' historikken ER identiteten'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.artefakttype    IS DISTINCT FROM OLD.artefakttype
       OR NEW.skjemaversjon   IS DISTINCT FROM OLD.skjemaversjon
       OR NEW.skjema_hash     IS DISTINCT FROM OLD.skjema_hash
       OR NEW.payloadfri      IS DISTINCT FROM OLD.payloadfri
       OR NEW.forrige_versjon IS DISTINCT FROM OLD.forrige_versjon
       OR NEW.registrert_ts   IS DISTINCT FROM OLD.registrert_ts THEN
        RAISE EXCEPTION 'artefakttype_versjon: raden er immutabel —'
            ' kun status kan endres, og bare én vei'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status
       AND NOT (OLD.status = 'gjeldende' AND NEW.status = 'avviklet') THEN
        RAISE EXCEPTION 'artefakttype_versjon: eneste lovlige overgang er'
            ' gjeldende -> avviklet — en avviklet versjon gjenoppstår aldri'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER artefakttype_versjon_laas
    BEFORE UPDATE OR DELETE ON artefakttype_versjon
    FOR EACH ROW EXECUTE FUNCTION artefakttype_versjon_laas();

-- ------------------------------------------------------------
-- 1b. FØDSELEN ER RELASJONELL: en type som registreres ETTER denne
--     migrasjonen får versjon 1 av registerraden selv — uansett hvilken
--     vei raden kom (den herdede døren, en test, et deploy-skript).
--     Uten dette hadde enhver ny type stått uten gjeldende versjon, og
--     stemplingstriggeren under født NULL inn i NOT NULL.
-- SECURITY DEFINER (eid av domene_eier, som har INSERT): registerraden
-- kan settes inn av flere roller (døren, admin-veier, riggen), og
-- fødselen skal lykkes uansett hvem som skrev — rettigheten er
-- versjonstabellens eier sin, ikke innskriverens.
SET LOCAL ROLE disponit_domene_eier;
CREATE FUNCTION artefakttype_versjon_foedsel()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    INSERT INTO public.artefakttype_versjon
            (artefakttype, skjemaversjon, skjema_hash, status)
     VALUES (NEW.artefakttype, 1, NEW.skjema_hash, 'gjeldende')
     ON CONFLICT DO NOTHING;
    RETURN NEW;
END $$;
RESET ROLE;
CREATE TRIGGER artefakttype_versjon_foedsel
    AFTER INSERT ON artefakttype_register
    FOR EACH ROW EXECUTE FUNCTION artefakttype_versjon_foedsel();

-- ------------------------------------------------------------
-- 2. Backfill: hver registrert type får sin versjon 1 = registerets
--    egen skjema_hash, gjeldende. Dette er avlesning av faktisk
--    tilstand (typene ER registrert og i bruk), 067-backfillformen —
--    NYE versjoner går alltid gjennom den herdede døren under.
INSERT INTO artefakttype_versjon
        (artefakttype, skjemaversjon, skjema_hash, payloadfri, status)
    SELECT artefakttype, 1, skjema_hash, false, 'gjeldende'
      FROM artefakttype_register;

-- ------------------------------------------------------------
-- 3. Artefaktraden bærer versjonen sin. Backfill = 1 (alt som finnes er
--    skrevet mot registerets ene hash, som ER versjon 1); triggere og
--    FORCE RLS må vike for backfillen på 059-formen — tabellen eies av
--    migrator, og vaktene skrus på igjen i samme transaksjon.
ALTER TABLE artefakt ADD COLUMN skjemaversjon INT;
ALTER TABLE artefakt DISABLE TRIGGER USER;
ALTER TABLE artefakt NO FORCE ROW LEVEL SECURITY;
UPDATE artefakt SET skjemaversjon = 1;
ALTER TABLE artefakt ENABLE TRIGGER USER;
ALTER TABLE artefakt FORCE ROW LEVEL SECURITY;
-- NULLABLE MED VILJE: regimet gjelder når versjonen bæres. Stemplingen
-- under gir hver ny rad gjeldende versjon der typen har en (alle
-- registrerte typer har, via backfillen og fødselstriggeren), og FK-en
-- dømmer verdien når den er satt. En hard NOT NULL ville flyttet
-- feilREKKEFØLGEN for ugyldige rader (CHECK-porter som
-- artefakt_payload_struktur skal fortsatt navngi sitt eget brudd, ikke
-- skygges av en null i en kolonne raden aldri rakk å få).
ALTER TABLE artefakt ADD CONSTRAINT artefakt_skjemaversjon_registrert
    FOREIGN KEY (artefakttype, skjemaversjon)
    REFERENCES artefakttype_versjon (artefakttype, skjemaversjon);

-- Stemplingen: enhver skrivevei får GJELDENDE versjon når den ikke sier
-- noe selv — og versjonen er WRITE-ONCE, promotering mot en avviklet
-- versjon avvises (dommens port 5: registreringsvinduet er synlig, og
-- et staged artefakt fra før flippen skal ikke bli varig evidens mot en
-- versjon som er avviklet).
CREATE FUNCTION artefakt_skjemaversjon_vakt()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_status TEXT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.skjemaversjon IS NULL THEN
            SELECT v.skjemaversjon INTO NEW.skjemaversjon
              FROM public.artefakttype_versjon v
             WHERE v.artefakttype = NEW.artefakttype
               AND v.status = 'gjeldende';
        END IF;
    ELSE
        IF NEW.skjemaversjon IS DISTINCT FROM OLD.skjemaversjon THEN
            RAISE EXCEPTION 'artefakt: skjemaversjon er fødselsattributt'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    -- Port 5 gjelder BEGGE skriveveier: en rad som FØDES promotert mot
    -- en avviklet versjon er samme hull som en som promoteres dit.
    IF NEW.tilstand = 'promotert'
       AND (TG_OP = 'INSERT' OR OLD.tilstand IS DISTINCT FROM 'promotert') THEN
        SELECT v.status INTO v_status
          FROM public.artefakttype_versjon v
         WHERE v.artefakttype = NEW.artefakttype
           AND v.skjemaversjon = NEW.skjemaversjon;
        IF v_status = 'avviklet' THEN
            RAISE EXCEPTION 'artefakt: skjemaversjon % av % er avviklet —'
                ' promotering mot en avviklet versjon finnes ikke',
                NEW.skjemaversjon, NEW.artefakttype
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER artefakt_skjemaversjon_vakt
    BEFORE INSERT OR UPDATE ON artefakt
    FOR EACH ROW EXECUTE FUNCTION artefakt_skjemaversjon_vakt();

-- Skriveveiene går gjennom SECURITY DEFINER-dører eid av domene_eier;
-- stemplingens oppslag trenger lesing der, og lese-/promoterings-
-- veiene (runtime + claimer) det samme.
-- Registreringsdøren (domene_eier, SECURITY DEFINER) skriver tabellen;
-- vakt-triggeren snevrer skrivingen til dørens egne former.
GRANT SELECT, INSERT, UPDATE ON artefakttype_versjon
    TO disponit_domene_eier;
GRANT SELECT ON artefakttype_versjon TO disponit_m37_claimer;

-- ------------------------------------------------------------
-- 4. Reap-siden av dommen: makuleringsdøren (067-kroppen ORDRETT —
--    SPEIL-presedensen) lærer at en PAYLOADFRI versjon består. Et
--    beslutningsspor uten kandidatdata er varig evidens; å makulere det
--    ville revet nettopp det varige. v1 (payloadfri=false) makuleres
--    som før, og A1-porten måler BEGGE halvdelene: rapporten finnes
--    etterpå, og den har null treff.
SET LOCAL ROLE disponit_domene_eier;
CREATE OR REPLACE FUNCTION makuler_artefakter_for_prosess(
    p_tenant TEXT, p_oppdrag_id BIGINT, p_naa TIMESTAMPTZ)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_antall INT; v_staged INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'makuler_artefakter_for_prosess');
    UPDATE public.artefakt a
       SET ciphertext = NULL, nonce = NULL, makulert_ts = p_naa
     WHERE a.tenant = p_tenant AND a.oppdrag_id = p_oppdrag_id
       AND a.tilstand IN ('promotert','bevart','karantene')
       AND a.makulert_ts IS NULL
       AND (a.ciphertext IS NOT NULL OR a.nonce IS NOT NULL)
       -- 072: payloadfrie versjoner består — de ER det varige sporet.
       AND NOT EXISTS (SELECT 1 FROM public.artefakttype_versjon v
                        WHERE v.artefakttype = a.artefakttype
                          AND v.skjemaversjon = a.skjemaversjon
                          AND v.payloadfri);
    GET DIAGNOSTICS v_antall = ROW_COUNT;
    UPDATE public.artefakt a
       SET tilstand = 'forkastet', ciphertext = NULL, nonce = NULL
     WHERE a.tenant = p_tenant AND a.oppdrag_id = p_oppdrag_id
       AND a.tilstand = 'staged'
       AND (a.ciphertext IS NOT NULL OR a.nonce IS NOT NULL)
       AND NOT EXISTS (SELECT 1 FROM public.artefakttype_versjon v
                        WHERE v.artefakttype = a.artefakttype
                          AND v.skjemaversjon = a.skjemaversjon
                          AND v.payloadfri);
    GET DIAGNOSTICS v_staged = ROW_COUNT;
    RETURN v_antall + v_staged;
END $$;

-- ------------------------------------------------------------
-- 4b. Reap-vakten (069-kroppen ORDRETT + payloadfri-unntaket): merket
--     kan settes selv om det payloadfrie beslutningssporet består —
--     det ER det varige sporet, og vaktens spørsmål er fortsatt
--     nøyaktig makuleringsdørens. Vakten eies av MIGRATOR (057/069
--     definerte den utenfor rolleblokkene) — REPLACE krever eieren.
RESET ROLE;
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
                           OR a.nonce IS NOT NULL)
                      -- 072 (BESLUTNING-168): en PAYLOADFRI versjon er
                      -- varig evidens og skal nettopp IKKE makuleres —
                      -- samme unntak som makuleringsdøren selv, så
                      -- «døren har tømt alt den skal» og «vakten
                      -- slipper merket» forblir samme spørsmål.
                      AND NOT EXISTS (
                          SELECT 1 FROM public.artefakttype_versjon v
                           WHERE v.artefakttype = a.artefakttype
                             AND v.skjemaversjon = a.skjemaversjon
                             AND v.payloadfri)) THEN
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

RESET ROLE;
SET LOCAL ROLE disponit_domene_eier;

-- ------------------------------------------------------------
-- 5. Den herdede døren: NY versjon settes gjeldende og forgjengeren
--    avviklet i SAMME transaksjon (dommens §3 — «bør ikke promoteres i
--    mellomtiden» slutter å være en intensjon). Skjemaet må finnes
--    (036-regelen), kjeden er lineær, og kallet er idempotent på
--    identisk innhold.
CREATE FUNCTION registrer_artefaktskjemaversjon(
    p_artefakttype TEXT, p_skjemaversjon INT, p_skjema_hash TEXT,
    p_payloadfri BOOLEAN, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_forrige INT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.artefakttype_register t
                    WHERE t.artefakttype = p_artefakttype) THEN
        RAISE EXCEPTION 'artefakttype % er ikke registrert', p_artefakttype
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.artefaktskjema s
                    WHERE s.skjema_hash = p_skjema_hash) THEN
        RAISE EXCEPTION 'skjemaversjon %: skjema_hash % finnes ikke —'
            ' registrer skjemaet først', p_skjemaversjon, p_skjema_hash
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM pg_advisory_xact_lock(
        hashtextextended('artefakttype_versjon:' || p_artefakttype, 0));
    SELECT skjema_hash, payloadfri, status INTO r
      FROM public.artefakttype_versjon
     WHERE artefakttype = p_artefakttype
       AND skjemaversjon = p_skjemaversjon;
    IF FOUND THEN
        IF (r.skjema_hash, r.payloadfri)
           IS DISTINCT FROM (p_skjema_hash, p_payloadfri) THEN
            RAISE EXCEPTION 'skjemaversjon (%, %) er immutabel',
                p_artefakttype, p_skjemaversjon
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN;  -- idempotent no-op
    END IF;
    SELECT skjemaversjon INTO v_forrige
      FROM public.artefakttype_versjon
     WHERE artefakttype = p_artefakttype AND status = 'gjeldende';
    IF v_forrige IS NOT NULL AND v_forrige >= p_skjemaversjon THEN
        RAISE EXCEPTION 'skjemaversjon % er ikke nyere enn gjeldende (%)',
            p_skjemaversjon, v_forrige
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.artefakttype_versjon
       SET status = 'avviklet'
     WHERE artefakttype = p_artefakttype AND status = 'gjeldende';
    INSERT INTO public.artefakttype_versjon
            (artefakttype, skjemaversjon, skjema_hash, payloadfri,
             forrige_versjon, status)
     VALUES (p_artefakttype, p_skjemaversjon, p_skjema_hash, p_payloadfri,
             v_forrige, 'gjeldende');
    -- Aktøren SKRIVES (036-presedensen, Codex P2 der): en dør som tar
    -- p_aktor og kaster den er et grensesnitt som lyver. Kun ved faktisk
    -- registrering — idempotent no-op lager ingen hendelse.
    INSERT INTO public.modulregister_hendelse (modul_id, hendelse, aktor,
                                               detalj)
        VALUES ('plattform', 'artefaktskjemaversjon_registrert', p_aktor,
                jsonb_build_object('artefakttype', p_artefakttype,
                                   'skjemaversjon', p_skjemaversjon,
                                   'skjema_hash', p_skjema_hash,
                                   'payloadfri', p_payloadfri));
END $$;
REVOKE ALL ON FUNCTION makuler_artefakter_for_prosess(TEXT, BIGINT,
    TIMESTAMPTZ) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION makuler_artefakter_for_prosess(TEXT, BIGINT,
    TIMESTAMPTZ) TO disponit_m37_claimer;
REVOKE ALL ON FUNCTION registrer_artefaktskjemaversjon(TEXT, INT, TEXT,
    BOOLEAN, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION registrer_artefaktskjemaversjon(TEXT, INT, TEXT,
    BOOLEAN, TEXT) TO disponit_domains_admin;
RESET ROLE;

-- Døren er SECURITY DEFINER hos domene-eieren og skriver aktørhendelsen
-- til plattformregisteret — eieren trenger INSERT der (identitetskolonnen
-- krever ingen egen sekvensgrant).
GRANT INSERT ON modulregister_hendelse TO disponit_domene_eier;

-- Versjonstabellen leses også av valideringsveien (runtime slår opp
-- gjeldende skjema ved opplasting/promotering).
GRANT SELECT ON artefakttype_versjon TO disponit;
