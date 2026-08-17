-- ============================================================
-- 036 — PR-014c: automatisk WCAG-kontroll — plattformlaget
--        (implementeringsklarsignal 2026-08-17 kveld, konsolidert v1–v3)
--
-- Tre ting bor her, og alle tre er PLATTFORM, ikke modul:
--
--  1. `artefaktskjema` — innholdsadressert skjemalager. Lukker CP5-hullet
--     fra 014b: artefaktinnhold har hatt en `skjema_hash` uten at noe
--     kunne slå den opp og validere. Hashen ER identiteten
--     (sha256 over JCS-kanonisk form); raden er immutabel for alltid —
--     UPDATE og DELETE avvises uavhengig av referanser, ingen GC i v1.
--
--  2. Sideeffektklassen `ekstern_lesing` (utvider CHECK-en fra 013/014a):
--     oppdraget kan generere observerbar trafikk mot systemer utenfor
--     plattformen, kan IKKE mutere autoritativ ekstern tilstand, trenger
--     ingen kompensasjons-outbox — men KREVER positivt autorisert mål,
--     egress-kontroll, frekvensgrense og eksplisitt robots-håndtering.
--     MERK REKKEVIDDEN (klarsignalet §4, ordrett i tabellform der):
--     enumverdien er ingen snarvei til 014b-garantiene — målautorisasjon
--     og frekvens bæres av denne migrasjonen + aktiveringsporten,
--     egress/robots/crawltak bæres av 014b-kontrakten. Eksisterende
--     kontrakter kan ikke omklassifiseres: modulkontrakt tåler ingen
--     UPDATE (014a §1).
--
--  3. `malautorisasjonsvilkar` — hvilke policyvilkår som TELLER som
--     målautorisasjon, per måldomene. Tom liste er default; et vilkår
--     teller kun hvis det har en rad — aldri navnetolkning
--     (`krever_malautorisasjon` uttrykker et BEHOV, ikke et bevis).
--
-- Kontrakt-, release- og typeregistreringer for `m_wcag_audit` skjer
-- gjennom de herdede funksjonene VED DEPLOY, ikke som rå INSERT her —
-- de skal være auditerte overganger som alle andre (014a).
-- ============================================================

-- ------------------------------------------------------------
-- 1. Innholdsadressert skjemalager
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artefaktskjema (
    skjema_hash TEXT PRIMARY KEY CHECK (skjema_hash ~ '^[0-9a-f]{64}$'),
    skjema      JSONB NOT NULL CHECK (jsonb_typeof(skjema) = 'object'),
    registrert  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- `avvis_endring()` finnes fra 035 og bærer tabellnavn + operasjon i
-- feilmeldingen. UPDATE og DELETE avvises ALLTID — også for en rad ingen
-- artefakttype refererer ennå (portene 26–28): et skjema som har fått en
-- hash er publisert innhold; å «rette» det ville gitt to sannheter under
-- samme identitet.
DROP TRIGGER IF EXISTS artefaktskjema_immutable ON artefaktskjema;
CREATE TRIGGER artefaktskjema_immutable
    BEFORE UPDATE OR DELETE ON artefaktskjema
    FOR EACH ROW EXECUTE FUNCTION avvis_endring();

-- ------------------------------------------------------------
-- 2. Sideeffektklassen `ekstern_lesing`
-- ------------------------------------------------------------
ALTER TABLE modulkontrakt DROP CONSTRAINT modulkontrakt_sideeffektklasse_check;
ALTER TABLE modulkontrakt ADD CONSTRAINT modulkontrakt_sideeffektklasse_check
    CHECK (sideeffektklasse IN ('sideeffektfri', 'ekstern_lesing',
                                'krever_outbox'));

-- ------------------------------------------------------------
-- 3. Målautorisasjonsregisteret
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS malautorisasjonsvilkar (
    vilkar_type TEXT PRIMARY KEY CHECK (length(btrim(vilkar_type)) > 0),
    maldomene   TEXT NOT NULL CHECK (maldomene IN ('web_hostname')),
    registrert  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed: domenekontroll-attestasjonen (014b) er beviset på at tenanten
-- faktisk autoriserer plattformen for hostnamet — nøyaktig det
-- `web_hostname`-målet krever.
INSERT INTO malautorisasjonsvilkar (vilkar_type, maldomene)
    VALUES ('domenekontroll_verifisert', 'web_hostname')
    ON CONFLICT (vilkar_type) DO NOTHING;

-- ------------------------------------------------------------
-- 4. Herdede funksjoner (eier: disponit_modul_eier, som 035)
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_modul_eier;

-- Codex P2, runde 2: metasjekken lå bare i det ene deploy-skriptet, mens
-- BEGGE admin-rollene fortsatt har EXECUTE på registreringsfunksjonen. En
-- direkte SQL-kaller — psql, et fremtidig deploy-verktøy, et retry-skript
-- — så aldri Python-sjekken. Derfor står denne vakten på SQL-siden, der
-- ingen kaller kan gå utenom.
--
-- Den validerer ikke HELE Draft 2020-12; det kan plpgsql ikke, og den
-- later ikke som. Den tar den ene klassen feil som er både STILLE og
-- PERMANENT: en ugyldig `type`. `{"type": "strng"}` er et lovlig
-- JSON-objekt, passerer alle formkrav, og får deretter validatoren til å
-- kaste `UnknownType` på hver eneste opplastning — og siden både
-- skjemaraden og typebindingen er immutable, kan typen aldri repareres.
--
-- Rekursjonen følger de stedene Draft 2020-12 FAKTISK plasserer
-- subskjemaer. Det er hele grunnen til at den kan gjøres uten falske
-- treff: en naiv `$.**.type` ville også truffet et felt som tilfeldigvis
-- HETER «type» (`properties.type`), og avvist et fullt lovlig skjema for
-- alltid.
CREATE OR REPLACE FUNCTION public._artefaktskjema_typefeil(p_skjema JSONB)
RETURNS TEXT LANGUAGE plpgsql IMMUTABLE
SET search_path = pg_catalog AS $$
DECLARE
    k_type  CONSTANT TEXT[] := ARRAY['object','array','string','number',
                                     'integer','boolean','null'];
    -- Objekter der HVER VERDI er et subskjema.
    k_kart  CONSTANT TEXT[] := ARRAY['properties','patternProperties',
                                     '$defs','definitions',
                                     'dependentSchemas'];
    -- Lister av subskjemaer.
    k_liste CONSTANT TEXT[] := ARRAY['allOf','anyOf','oneOf','prefixItems'];
    -- Ett subskjema direkte.
    k_ett   CONSTANT TEXT[] := ARRAY['items','contains','not','if','then',
                                     'else','additionalProperties',
                                     'propertyNames','unevaluatedItems',
                                     'unevaluatedProperties'];
    v_t JSONB; v_e JSONB; v_n TEXT; v_feil TEXT;
BEGIN
    -- Et boolsk subskjema er lovlig i 2020-12 (`true`/`false`).
    IF jsonb_typeof(p_skjema) = 'boolean' THEN
        RETURN NULL;
    END IF;
    IF jsonb_typeof(p_skjema) <> 'object' THEN
        RETURN format('subskjema er %s, må være objekt eller boolsk',
                      jsonb_typeof(p_skjema));
    END IF;

    v_t := p_skjema -> 'type';
    IF v_t IS NOT NULL THEN
        IF jsonb_typeof(v_t) = 'string' THEN
            IF NOT (v_t #>> '{}' = ANY(k_type)) THEN
                RETURN format('ukjent type %L', v_t #>> '{}');
            END IF;
        ELSIF jsonb_typeof(v_t) = 'array' THEN
            FOR v_e IN SELECT * FROM jsonb_array_elements(v_t) LOOP
                IF jsonb_typeof(v_e) <> 'string'
                   OR NOT (v_e #>> '{}' = ANY(k_type)) THEN
                    RETURN format('ukjent type %s', v_e::text);
                END IF;
            END LOOP;
        ELSE
            RETURN format('type må være streng eller liste, er %s',
                          jsonb_typeof(v_t));
        END IF;
    END IF;

    FOREACH v_n IN ARRAY k_kart LOOP
        IF jsonb_typeof(p_skjema -> v_n) = 'object' THEN
            FOR v_e IN SELECT value FROM jsonb_each(p_skjema -> v_n) LOOP
                v_feil := public._artefaktskjema_typefeil(v_e);
                IF v_feil IS NOT NULL THEN
                    RETURN v_n || '/' || v_feil;
                END IF;
            END LOOP;
        END IF;
    END LOOP;
    FOREACH v_n IN ARRAY k_liste LOOP
        IF jsonb_typeof(p_skjema -> v_n) = 'array' THEN
            FOR v_e IN SELECT * FROM jsonb_array_elements(p_skjema -> v_n)
            LOOP
                v_feil := public._artefaktskjema_typefeil(v_e);
                IF v_feil IS NOT NULL THEN
                    RETURN v_n || '/' || v_feil;
                END IF;
            END LOOP;
        END IF;
    END LOOP;
    FOREACH v_n IN ARRAY k_ett LOOP
        IF p_skjema ? v_n THEN
            v_feil := public._artefaktskjema_typefeil(p_skjema -> v_n);
            IF v_feil IS NOT NULL THEN
                RETURN v_n || '/' || v_feil;
            END IF;
        END IF;
    END LOOP;
    RETURN NULL;
END $$;

-- Skjemaregistrering: hashen REKALKULERES fra innholdet (port 16) —
-- sha256 over de kanoniske bytene kalleren sender. Kanonisiteten (JCS)
-- er KALLERENS kontrakt: alle veier inn går via
-- `policy_validator.jcs.kanoniske_bytes` (deploy-skriptet og testene) —
-- funksjonen binder hashen til NØYAKTIG de bytene den fikk, så en
-- ukanonisk kaller skader bare sin egen oppslagbarhet, aldri integriteten.
-- Idempotent for identisk innhold; samme hash med ANNET innhold er
-- umulig (sha256-kollisjon) og PK-en stopper uansett.
CREATE OR REPLACE FUNCTION registrer_artefaktskjema(
    p_kanonisk TEXT, p_oppgitt_hash TEXT, p_aktor TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_hash TEXT; v_skjema JSONB; v_typefeil TEXT;
BEGIN
    v_hash := encode(sha256(convert_to(p_kanonisk, 'UTF8')), 'hex');
    IF v_hash IS DISTINCT FROM p_oppgitt_hash THEN
        RAISE EXCEPTION 'artefaktskjema: oppgitt hash % matcher ikke'
            ' innholdet (rekalkulert %)', p_oppgitt_hash, v_hash
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_skjema := p_kanonisk::jsonb;
    -- Full JSON Schema-metavalidering krever validatoren selv, og den bor i
    -- Python (`api.artefaktskjema.registrer`, som er DEN delte
    -- registreringsveien derfra). Grensen står uttalt her i stedet for at
    -- funksjonen later som den har kontrollert mer enn den har.
    --
    -- Men SQL-siden er ikke lenger tom (Codex P2, runde 2): begge
    -- admin-rollene har EXECUTE, så en direkte kaller ser aldri
    -- Python-sjekken, og gapet er ikke reparerbart etterpå — skjemaraden
    -- OG typebindingen er immutable, så en artefakttype bundet til et
    -- ødelagt skjema ville dødd på hver opplastning for alltid. Derfor
    -- kjøres `_artefaktskjema_typefeil` her: den fanger den ene klassen
    -- feil som er både stille og permanent, uansett hvem som kaller.
    --
    -- `valider()` kjører metasjekken en tredje gang, før innhold måles,
    -- slik at et skjema som likevel skulle ha kommet inn gir en ærlig
    -- avvisning i stedet for en 500-er.
    IF jsonb_typeof(v_skjema) <> 'object' THEN
        RAISE EXCEPTION 'artefaktskjema: skjemaet må være et objekt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_typefeil := public._artefaktskjema_typefeil(v_skjema);
    IF v_typefeil IS NOT NULL THEN
        RAISE EXCEPTION 'artefaktskjema: ugyldig skjema — %', v_typefeil
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.artefaktskjema (skjema_hash, skjema)
        VALUES (v_hash, v_skjema)
        ON CONFLICT (skjema_hash) DO NOTHING;
    RETURN v_hash;
END $$;

-- Nye målautorisasjonsvilkår: herdet, auditert (modultoken_hendelse er
-- feil spor — dette er et PLATTFORMREGISTER; modulregister_hendelse
-- bærer det, med vilkårstypen i detaljfeltet). HTTP-laget scope-gater
-- med `modules:manage`; databasen gater med EXECUTE til admin-rollen.
CREATE OR REPLACE FUNCTION registrer_malautorisasjonsvilkar(
    p_vilkar_type TEXT, p_maldomene TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_eksisterende TEXT;
BEGIN
    -- Codex P2: serialiser check-then-insert på vilkårsidentiteten (samme
    -- mønster som `registrer_kontrakt` og `registrer_artefakttype`). Uten
    -- låsen kan to samtidige registreringer av samme NYE (vilkar_type,
    -- maldomene) — deploy og administrasjon, eller et retry — begge se
    -- «finnes ikke» her og gå videre til INSERT; én vinner, den andre får
    -- PK-brudd selv om innholdet er identisk. Funksjonen LOVER en
    -- idempotent no-op i nettopp det tilfellet.
    PERFORM pg_advisory_xact_lock(
        hashtextextended('malautorisasjonsvilkar:' || p_vilkar_type, 0));
    SELECT maldomene INTO v_eksisterende
      FROM public.malautorisasjonsvilkar WHERE vilkar_type = p_vilkar_type;
    IF FOUND THEN
        IF v_eksisterende IS DISTINCT FROM p_maldomene THEN
            RAISE EXCEPTION 'malautorisasjonsvilkar: % er registrert for %'
                ' — raden er immutabel', p_vilkar_type, v_eksisterende
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN;                                   -- idempotent
    END IF;
    INSERT INTO public.malautorisasjonsvilkar (vilkar_type, maldomene)
        VALUES (p_vilkar_type, p_maldomene);
    INSERT INTO public.modulregister_hendelse (modul_id, hendelse, aktor,
                                               detalj)
        VALUES ('plattform', 'malautorisasjonsvilkar_registrert', p_aktor,
                jsonb_build_object('vilkar_type', p_vilkar_type,
                                   'maldomene', p_maldomene));
END $$;

REVOKE ALL ON FUNCTION public._artefaktskjema_typefeil(JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION registrer_artefaktskjema(TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION registrer_malautorisasjonsvilkar(TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION registrer_artefaktskjema(TEXT, TEXT, TEXT)
    TO disponit_modules_admin;
-- Domeneforvaltningen registrerer artefakttyper (016) — den må også kunne
-- registrere skjemaet typen binder seg til; de to hører til samme deploy-steg.
GRANT EXECUTE ON FUNCTION registrer_artefaktskjema(TEXT, TEXT, TEXT)
    TO disponit_domains_admin;
GRANT EXECUTE ON FUNCTION registrer_malautorisasjonsvilkar(TEXT, TEXT, TEXT)
    TO disponit_modules_admin;

RESET ROLE;

-- Eieren må kunne skrive de nye tabellene (SECURITY DEFINER kjører som
-- eier); modulregister_hendelse har modul_eier alt (014a).
GRANT SELECT, INSERT ON artefaktskjema TO disponit_modul_eier;
GRANT SELECT, INSERT ON malautorisasjonsvilkar TO disponit_modul_eier;
-- Runtime LESER begge (skjemavalidering ved opplasting/promotering og
-- aktiveringsporten skjer i API-prosessen) — aldri skriver. Grantene bor i
-- migrer.py-RETTIGHETER: kjøreren NULLSTILLER runtime-rettighetene på alle
-- migrator-eide tabeller etter migrasjonene, så en GRANT her ville sett
-- riktig ut i fila og vært borte i basen (nøyaktig den klassen av stille
-- WARNING/wipe-feil 014/035 dokumenterer).

-- ------------------------------------------------------------
-- 5. `registrer_artefakttype` — GJELDENDE kropp (035, diff-endret:
--    skjema_hash må finnes i artefaktskjema — positiv regel, fail-closed;
--    merket «036:»). Eies av domene-eieren (016) — CREATE OR REPLACE må
--    kjøre som samme eier.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_domene_eier;
CREATE OR REPLACE FUNCTION registrer_artefakttype(
    p_artefakttype TEXT, p_eiermodul TEXT, p_kontraktversjon INT,
    p_kontrakt_hash TEXT, p_skjema_hash TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_konflikt TEXT;
BEGIN
    -- 035: navneformen er lukket — `<domene>.<underdomene>.<artefakt>`,
    -- kun [a-z0-9_.], globalt unik, MINST tre ledd (dypere hierarki er
    -- lov — det er nettopp da prefiks-overlappen under har arbeid å
    -- gjøre). Ingen versjon og intet modulnavn i navnet:
    -- (kontraktversjon, kontrakt_hash) versjonerer raden og eiermodul er
    -- egen kolonne.
    IF p_artefakttype !~ '^[a-z0-9_]+(\.[a-z0-9_]+){2,}$' THEN
        RAISE EXCEPTION 'artefakttype % har ugyldig navneform'
            ' (<domene>.<underdomene>.<artefakt>, [a-z0-9_.])',
            p_artefakttype USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- 036: skjemaet må FINNES før typen kan binde seg til det. Uten denne
    -- regelen var `skjema_hash` en påstand ingen kunne slå opp — nøyaktig
    -- CP5-hullet: innhold som ikke kan valideres, promotert i stillhet.
    IF NOT EXISTS (SELECT 1 FROM public.artefaktskjema s
                    WHERE s.skjema_hash = p_skjema_hash) THEN
        RAISE EXCEPTION 'artefakttype %: skjema_hash % finnes ikke i'
            ' artefaktskjema — registrer skjemaet først', p_artefakttype,
            p_skjema_hash USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- 035: GLOBAL lås (som `modulregister:oppdragstype`) — uten den er
    -- prefiks-overlappen under en avgjørelse tatt på et snapshot: to
    -- samtidige registreringer av `a.b.c` og `a.b.c_x` kan begge passere
    -- hver sin sjekk og committe. Identitetslåsen (016) beholdes for den
    -- idempotente no-op-veien.
    PERFORM pg_advisory_xact_lock(
        hashtextextended('modulregister:artefakttype', 0));
    -- Codex P2: serialiser på artefakttype-identiteten (samme mønster som
    -- modulregisteret). Uten låsen kan to samtidige registreringer av samme
    -- immutable tuppel begge passere eksistenssjekken under; én vinner, den andre
    -- får PK-brudd i stedet for den dokumenterte idempotente no-op-en.
    PERFORM pg_advisory_xact_lock(
        hashtextextended('artefakttype:' || p_artefakttype, 0));
    -- Sammenlign HELE den immutable tuppelen — en re-registrering med samme
    -- skjema_hash men ANNEN eier/kontrakt ble ellers rapportert som en vellykket
    -- no-op selv om bindingen ikke ble anvendt.
    SELECT eiermodul, kontraktversjon, kontrakt_hash, skjema_hash INTO r
      FROM public.artefakttype_register WHERE artefakttype = p_artefakttype;
    IF FOUND THEN
        IF (r.eiermodul, r.kontraktversjon, r.kontrakt_hash, r.skjema_hash)
           IS DISTINCT FROM
           (p_eiermodul, p_kontraktversjon, p_kontrakt_hash, p_skjema_hash) THEN
            RAISE EXCEPTION 'artefakttype % er immutable', p_artefakttype
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN;
    END IF;
    -- 035: prefiks-overlappssjekk under den globale låsen (speiler
    -- `registrer_oppdragstype`) — `a.b.c` og `a.b.c.d` skal ikke kunne
    -- sameksistere; punktumgrensen hindrer at `a.b.cd` regnes som overlapp.
    SELECT artefakttype INTO v_konflikt FROM public.artefakttype_register
     WHERE starts_with(p_artefakttype, artefakttype || '.')
        OR starts_with(artefakttype, p_artefakttype || '.')
     LIMIT 1;
    IF v_konflikt IS NOT NULL THEN
        RAISE EXCEPTION 'artefakttype % overlapper eksisterende %',
            p_artefakttype, v_konflikt USING ERRCODE = 'unique_violation';
    END IF;
    INSERT INTO public.artefakttype_register
        (artefakttype, eiermodul, kontraktversjon, kontrakt_hash, skjema_hash)
        VALUES (p_artefakttype, p_eiermodul, p_kontraktversjon, p_kontrakt_hash,
                p_skjema_hash);   -- FK → modulkontrakt
END $$;
RESET ROLE;

-- Domene-eieren må kunne LESE skjemalageret (eksistenssjekken over).
GRANT SELECT ON artefaktskjema TO disponit_domene_eier;

-- ------------------------------------------------------------
-- 6. 035-seeden `test.onboarding.kvittering` registrerte sin skjema_hash
--    FØR skjemalageret fantes. Innholdet er kjent (selvtest-skjemaet,
--    beregnet med policy_validator.jcs) — raden legges inn her så den
--    positive regelen holder for ALT som står i registeret, ikke bare
--    det som kommer etter.
-- ------------------------------------------------------------
INSERT INTO artefaktskjema (skjema_hash, skjema)
    VALUES ('e30ef85662f0967117cf3d0dc2e28b9efd3da50b501429be79bd8e5cea5fc40e',
            '{"additionalProperties":false,"properties":{"kjoring_id":{"minLength":8,"type":"string"},"resultat":{"enum":["ok","feil"],"type":"string"},"tidspunkt":{"format":"date-time","type":"string"}},"required":["kjoring_id","tidspunkt","resultat"],"type":"object"}'::jsonb)
    ON CONFLICT (skjema_hash) DO NOTHING;
