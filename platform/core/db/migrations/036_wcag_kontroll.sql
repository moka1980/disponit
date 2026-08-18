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
-- DE HASHEDE BYTENE ER RADEN (Codex P2). Hashen er over `kanonisk` —
-- JCS-bytene kalleren sendte — mens `skjema` var en `::jsonb`-kastet
-- KOPI av dem. jsonb er en normalisert representasjon, ikke de bytene:
-- den kaster uvesentlig blanktegn, sorterer nøkler på sin egen måte og
-- normaliserer tall. Adressen kunne derfor ikke regnes ut på nytt fra
-- innholdet den adresserer — nøyaktig det et innholdsadressert lager
-- lover. Fikseren i testfixturene var symptomet i klartekst: de satte
-- inn `{"type": "object"}` under hashen til `{"type":"object"}`.
--
-- Nå LAGRES bytene, og `skjema` UTLEDES av dem ved innsetting — den er en
-- oppslagsform, ikke en andre sannhet. Vakten under gjør adressen
-- etterprøvbar av databasen selv: sha256 over de lagrede bytene ER
-- primærnøkkelen, uansett hvilken rolle som setter inn raden.
CREATE TABLE IF NOT EXISTS artefaktskjema (
    skjema_hash TEXT PRIMARY KEY CHECK (skjema_hash ~ '^[0-9a-f]{64}$'),
    kanonisk    TEXT NOT NULL,
    skjema      JSONB NOT NULL CHECK (jsonb_typeof(skjema) = 'object'),
    registrert  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Vakten er en TRIGGER, ikke en CHECK/generert kolonne: begge de to
-- krever et immutabelt uttrykk, og `text::jsonb` er en I/O-konvertering
-- som Postgres kan avvise i den rollen. En BEFORE INSERT-trigger har
-- ingen slik begrensning, og den er dessuten mønsteret denne filen
-- allerede bruker på resten av portene sine.
CREATE OR REPLACE FUNCTION artefaktskjema_adresse() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF encode(sha256(convert_to(NEW.kanonisk, 'UTF8')), 'hex')
       IS DISTINCT FROM NEW.skjema_hash THEN
        RAISE EXCEPTION 'artefaktskjema: skjema_hash % er ikke sha256 av'
            ' de lagrede bytene', NEW.skjema_hash
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Oppgitt `skjema` AVVISES når den er uenig med bytene — den blir
    -- ikke stille skrevet om. En kaller som mener noe annet enn det den
    -- selv hashet, har en feil vi ikke kan gjette oss ut av.
    IF NEW.skjema IS NOT NULL
       AND NEW.skjema IS DISTINCT FROM NEW.kanonisk::jsonb THEN
        RAISE EXCEPTION 'artefaktskjema: oppgitt skjema er ikke de'
            ' kanoniske bytene'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    NEW.skjema := NEW.kanonisk::jsonb;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS artefaktskjema_adressert ON artefaktskjema;
CREATE TRIGGER artefaktskjema_adressert
    BEFORE INSERT ON artefaktskjema
    FOR EACH ROW EXECUTE FUNCTION artefaktskjema_adresse();

-- `avvis_endring()` finnes fra 035 og bærer tabellnavn + operasjon i
-- feilmeldingen. UPDATE og DELETE avvises ALLTID — også for en rad ingen
-- artefakttype refererer ennå (portene 26–28): et skjema som har fått en
-- hash er publisert innhold; å «rette» det ville gitt to sannheter under
-- samme identitet.
DROP TRIGGER IF EXISTS artefaktskjema_immutable ON artefaktskjema;
CREATE TRIGGER artefaktskjema_immutable
    BEFORE UPDATE OR DELETE ON artefaktskjema
    FOR EACH ROW EXECUTE FUNCTION avvis_endring();

-- Codex P2: TRUNCATE fyrer INGEN rad-trigger i PostgreSQL, så vakten over
-- ser den ikke. Og her finnes ingen fremmednøkkel fra
-- `artefakttype_register.skjema_hash` som kunne stoppet den indirekte:
-- bindingen er en HASH, ikke en referanse. Tabelleieren kunne derfor tømt
-- hele skjemalageret i ett statement, og etterlatt hver registrerte
-- artefakttype uten et oppslagbart skjema — altså hver opplastning avvist,
-- for alltid, siden både skjemarader og typebindinger er immutable.
--
-- Statement-vakt, samme mønster som 014/016/035 bruker på sine append-only
-- registre. Den gjelder også eieren og migratoren: en TRUNCATE her er
-- alltid en feil, aldri en driftsoppgave.
DROP TRIGGER IF EXISTS artefaktskjema_ingen_truncate ON artefaktskjema;
CREATE TRIGGER artefaktskjema_ingen_truncate
    BEFORE TRUNCATE ON artefaktskjema
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

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

-- Samme vakt her, og av samme grunn: `registrer_malautorisasjonsvilkar`
-- sier «raden er immutabel» i sin egen feilmelding, men tabellen hadde
-- ingen trigger som gjorde påstanden sann. Et register aktiveringsporten
-- leser POSITIVT — bare rader teller — er ikke bare noe som ikke skal
-- endres: en fjernet rad slår av målautorisasjonskravet for alle policyer
-- som bruker vilkåret, i stillhet.
DROP TRIGGER IF EXISTS malautorisasjonsvilkar_immutable
    ON malautorisasjonsvilkar;
CREATE TRIGGER malautorisasjonsvilkar_immutable
    BEFORE UPDATE OR DELETE ON malautorisasjonsvilkar
    FOR EACH ROW EXECUTE FUNCTION avvis_endring();
DROP TRIGGER IF EXISTS malautorisasjonsvilkar_ingen_truncate
    ON malautorisasjonsvilkar;
CREATE TRIGGER malautorisasjonsvilkar_ingen_truncate
    BEFORE TRUNCATE ON malautorisasjonsvilkar
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

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
-- Runde 3 (Codex P2): første versjon så bare på `type` og på formen til
-- de subskjemaene den rakk å nå. `{"required": "x"}` og
-- `{"minLength": "x"}` gikk derfor rett gjennom SQL-veien — og de er
-- NØYAKTIG samme skade som en ugyldig `type`: `check_schema()` i Python
-- avviser dem, så artefakttypen som bindes til hashen dør på hver
-- opplastning, permanent, fordi både skjemaraden og typebindingen er
-- immutable.
--
-- Vakten dekker nå hele NØKKELORDGRAMMATIKKEN i Draft 2020-12: hvert
-- nøkkelord metaskjemaet gir en fast verditype er sjekket mot den typen,
-- rekursivt. Det er det metaskjemaet i all hovedsak ER — resten (uri-,
-- regex- og `format`-syntaks) er annotasjoner `check_schema()` heller
-- ikke håndhever som feil. Grensen står fortsatt uttalt: plpgsql kjører
-- ingen JSON Schema-validator, og funksjonen later ikke som.
--
-- To ting er verdt å skrive ned:
--
--   * ER nøkkelordet der, MÅ typen stemme. Første versjon sjekket
--     `IF jsonb_typeof(...) = 'array'` før den gikk inn i `allOf` — altså
--     hoppet den STILLE over `{"allOf": "x"}`, som er den samme feilen
--     den skulle fange. Fravær er lovlig; feil type er det ikke.
--   * Ukjente nøkkelord slipper gjennom med vilje. Draft 2020-12 sier
--     eksplisitt at de ignoreres, og `check_schema()` godtar dem — en
--     avvisning her ville gjort SQL-veien STRENGERE enn Python-veien og
--     dermed blitt et nytt, motsatt avvik mellom de to.
--
-- Rekursjonen følger de stedene Draft 2020-12 FAKTISK plasserer
-- subskjemaer. Det er hele grunnen til at den kan gjøres uten falske
-- treff: en naiv `$.**.type` ville også truffet et felt som tilfeldigvis
-- HETER «type» (`properties.type`), og avvist et fullt lovlig skjema for
-- alltid. Av samme grunn gås `enum`, `const`, `default` og `examples`
-- ALDRI inn i: der er innholdet data, ikke skjema.
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
                                     'unevaluatedProperties','contentSchema'];
    -- Nøkkelord metaskjemaet gir en FAST verditype.
    k_streng CONSTANT TEXT[] := ARRAY['$id','$schema','$ref','$anchor',
                                      '$dynamicRef','$dynamicAnchor',
                                      '$comment','title','description',
                                      'pattern','format','contentEncoding',
                                      'contentMediaType'];
    k_bool  CONSTANT TEXT[] := ARRAY['uniqueItems','deprecated','readOnly',
                                     'writeOnly'];
    k_tall  CONSTANT TEXT[] := ARRAY['maximum','minimum','exclusiveMaximum',
                                     'exclusiveMinimum'];
    -- Ikke-negative heltall (`minLength`, `maxItems`, ...).
    k_antall CONSTANT TEXT[] := ARRAY['maxLength','minLength','maxItems',
                                      'minItems','maxContains','minContains',
                                      'maxProperties','minProperties'];
    -- Lister av data, ikke av subskjemaer — sjekkes som lister, gås aldri
    -- inn i.
    k_dataliste CONSTANT TEXT[] := ARRAY['enum','examples'];
    v_t JSONB; v_e JSONB; v_n TEXT; v_feil TEXT; v_num NUMERIC;
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
            -- Metaskjemaet krever minst ett element og unike verdier;
            -- `{"type": []}` matcher ingenting og ville drept typen like
            -- permanent som en stavefeil.
            IF jsonb_array_length(v_t) = 0 THEN
                RETURN 'type-listen er tom';
            END IF;
            IF (SELECT count(*) FROM jsonb_array_elements(v_t))
               <> (SELECT count(DISTINCT e.value) FROM
                     jsonb_array_elements(v_t) AS e) THEN
                RETURN 'type-listen har duplikater';
            END IF;
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

    -- Nøkkelord med fast verditype. ER de der, MÅ typen stemme.
    FOREACH v_n IN ARRAY k_streng LOOP
        IF p_skjema ? v_n AND jsonb_typeof(p_skjema -> v_n) <> 'string' THEN
            RETURN format('%s må være streng, er %s', v_n,
                          jsonb_typeof(p_skjema -> v_n));
        END IF;
    END LOOP;
    FOREACH v_n IN ARRAY k_bool LOOP
        IF p_skjema ? v_n AND jsonb_typeof(p_skjema -> v_n) <> 'boolean' THEN
            RETURN format('%s må være boolsk, er %s', v_n,
                          jsonb_typeof(p_skjema -> v_n));
        END IF;
    END LOOP;
    FOREACH v_n IN ARRAY k_tall LOOP
        IF p_skjema ? v_n AND jsonb_typeof(p_skjema -> v_n) <> 'number' THEN
            RETURN format('%s må være tall, er %s', v_n,
                          jsonb_typeof(p_skjema -> v_n));
        END IF;
    END LOOP;
    FOREACH v_n IN ARRAY k_antall LOOP
        IF p_skjema ? v_n THEN
            IF jsonb_typeof(p_skjema -> v_n) <> 'number' THEN
                RETURN format('%s må være tall, er %s', v_n,
                              jsonb_typeof(p_skjema -> v_n));
            END IF;
            v_num := (p_skjema ->> v_n)::numeric;
            IF v_num < 0 OR v_num <> trunc(v_num) THEN
                RETURN format('%s må være et ikke-negativt heltall, er %s',
                              v_n, v_num);
            END IF;
        END IF;
    END LOOP;
    IF p_skjema ? 'multipleOf' THEN
        IF jsonb_typeof(p_skjema -> 'multipleOf') <> 'number' THEN
            RETURN format('multipleOf må være tall, er %s',
                          jsonb_typeof(p_skjema -> 'multipleOf'));
        END IF;
        IF (p_skjema ->> 'multipleOf')::numeric <= 0 THEN
            RETURN 'multipleOf må være større enn 0';
        END IF;
    END IF;
    FOREACH v_n IN ARRAY k_dataliste LOOP
        IF p_skjema ? v_n AND jsonb_typeof(p_skjema -> v_n) <> 'array' THEN
            RETURN format('%s må være liste, er %s', v_n,
                          jsonb_typeof(p_skjema -> v_n));
        END IF;
    END LOOP;
    -- `required` er en liste av STRENGER. `{"required": "resultat"}` er den
    -- klassiske: den ser ut som den virker, og validatoren avviser den.
    IF p_skjema ? 'required' THEN
        IF jsonb_typeof(p_skjema -> 'required') <> 'array' THEN
            RETURN format('required må være liste, er %s',
                          jsonb_typeof(p_skjema -> 'required'));
        END IF;
        FOR v_e IN SELECT * FROM jsonb_array_elements(p_skjema -> 'required')
        LOOP
            IF jsonb_typeof(v_e) <> 'string' THEN
                RETURN format('required-element må være streng, er %s',
                              jsonb_typeof(v_e));
            END IF;
        END LOOP;
        IF (SELECT count(*) FROM
              jsonb_array_elements(p_skjema -> 'required'))
           <> (SELECT count(DISTINCT e.value) FROM
                 jsonb_array_elements(p_skjema -> 'required') AS e) THEN
            RETURN 'required har duplikater';
        END IF;
    END IF;
    -- `dependentRequired`: objekt av strenglister.
    IF p_skjema ? 'dependentRequired' THEN
        IF jsonb_typeof(p_skjema -> 'dependentRequired') <> 'object' THEN
            RETURN format('dependentRequired må være objekt, er %s',
                          jsonb_typeof(p_skjema -> 'dependentRequired'));
        END IF;
        FOR v_t IN SELECT value FROM jsonb_each(p_skjema -> 'dependentRequired')
        LOOP
            IF jsonb_typeof(v_t) <> 'array' THEN
                RETURN format('dependentRequired-verdi må være liste, er %s',
                              jsonb_typeof(v_t));
            END IF;
            FOR v_e IN SELECT * FROM jsonb_array_elements(v_t) LOOP
                IF jsonb_typeof(v_e) <> 'string' THEN
                    RETURN format('dependentRequired-element må være streng,'
                                  ' er %s', jsonb_typeof(v_e));
                END IF;
            END LOOP;
        END LOOP;
    END IF;
    -- `$vocabulary`: objekt av boolske verdier.
    IF p_skjema ? '$vocabulary' THEN
        IF jsonb_typeof(p_skjema -> '$vocabulary') <> 'object' THEN
            RETURN format('$vocabulary må være objekt, er %s',
                          jsonb_typeof(p_skjema -> '$vocabulary'));
        END IF;
        FOR v_e IN SELECT value FROM jsonb_each(p_skjema -> '$vocabulary')
        LOOP
            IF jsonb_typeof(v_e) <> 'boolean' THEN
                RETURN format('$vocabulary-verdi må være boolsk, er %s',
                              jsonb_typeof(v_e));
            END IF;
        END LOOP;
    END IF;

    -- Subskjemaene. ER nøkkelordet der, må BÆREREN ha riktig form også —
    -- `{"properties": "x"}` og `{"allOf": "x"}` slapp gjennom da sjekken
    -- sto som `IF jsonb_typeof(...) = 'object'` og bare hoppet over.
    FOREACH v_n IN ARRAY k_kart LOOP
        IF p_skjema ? v_n THEN
            IF jsonb_typeof(p_skjema -> v_n) <> 'object' THEN
                RETURN format('%s må være objekt, er %s', v_n,
                              jsonb_typeof(p_skjema -> v_n));
            END IF;
            FOR v_e IN SELECT value FROM jsonb_each(p_skjema -> v_n) LOOP
                v_feil := public._artefaktskjema_typefeil(v_e);
                IF v_feil IS NOT NULL THEN
                    RETURN v_n || '/' || v_feil;
                END IF;
            END LOOP;
        END IF;
    END LOOP;
    FOREACH v_n IN ARRAY k_liste LOOP
        IF p_skjema ? v_n THEN
            IF jsonb_typeof(p_skjema -> v_n) <> 'array' THEN
                RETURN format('%s må være liste, er %s', v_n,
                              jsonb_typeof(p_skjema -> v_n));
            END IF;
            IF jsonb_array_length(p_skjema -> v_n) = 0 THEN
                RETURN format('%s må ha minst ett subskjema', v_n);
            END IF;
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
-- sha256 over de kanoniske bytene kalleren sender. Alle veier inn fra
-- Python går via `policy_validator.jcs.kanoniske_bytes`, men begge
-- admin-rollene har EXECUTE her, så en direkte SQL-kaller kan sende hva
-- som helst som er gyldig JSON.
--
-- BYTENE LAGRES (Codex P2). Før sto hashen over `p_kanonisk` mens raden
-- bar `p_kanonisk::jsonb`, og de to er ikke samme representasjon: en
-- kaller som sendte JSON med uvesentlig blanktegn fikk en adresse ingen
-- kunne regne ut på nytt fra det som faktisk ble lagret, og to
-- semantisk like skjemaer kunne få hver sin adresse. Nå settes
-- `kanonisk` inn, `skjema` utledes av den, og `artefaktskjema_adressert`
-- binder adressen til bytene — også for en INSERT utenom denne
-- funksjonen.
--
-- Og den grovt ukanoniske inngangen AVVISES: JCS-utdata har ikke
-- blanktegn utenfor strenger. Sjekken er en nødvendig, ikke
-- tilstrekkelig, betingelse — plpgsql kan ikke serialisere JCS selv, og
-- skal ikke late som — men den fanger den vanlige veien inn (pretty-
-- printet JSON fra et adminverktøy) i stedet for å la den bli en
-- udødelig rad. Kanoniseringen for øvrig er fortsatt kallerens kontrakt.
--
-- Idempotent for identisk innhold; samme hash med ANNET innhold er
-- umulig (sha256-kollisjon) og PK-en stopper uansett.
--
-- AKTØREN SKRIVES (Codex P2). Funksjonen TOK `p_aktor` og kastet den:
-- raden bar skjemaet og et tidsstempel, ingenting om hvem. Skjemaraden
-- er udødelig og kan senere bli den permanente valideringskontrakten for
-- en artefakttype — nettopp den bindingen ingen kan angre — og da er
-- «hvilken administrator publiserte dette» spørsmålet en driftsperson
-- faktisk sitter med når en type oppfører seg uventet. Uten svaret var
-- den eneste sporbarheten et tidsstempel å korrelere mot loggene, hvis
-- de fortsatt fantes.
--
-- Hendelsen skrives i SAMME transaksjon som innsettingen: en registrering
-- uten spor, eller et spor uten registrering, ville begge vært en løgn om
-- hva som skjedde. `modulregister_hendelse` er append-only (014, egne
-- triggere mot UPDATE/DELETE/TRUNCATE), altså like uangripelig som raden
-- den beskriver — samme spor som `registrer_malautorisasjonsvilkar`
-- under bruker, og av samme grunn: dette er et PLATTFORMREGISTER.
--
-- Bare den EKTE innsettingen logges. Den idempotente gjentakelsen
-- publiserer ingenting nytt, og en hendelse for den ville flyttet svaret
-- på «hvem publiserte skjemaet» til den siste som kjørte deployet på
-- nytt.
--
-- `aktor` er NOT NULL i hendelsestabellen, så en registrering uten
-- oppgitt aktør feiler nå i stedet for å bli en udødelig rad ingen kan
-- knyttes til. Det er samme fail-closed-linje som resten av funksjonen,
-- og samme kontrakt `registrer_malautorisasjonsvilkar` alt har.
CREATE OR REPLACE FUNCTION registrer_artefaktskjema(
    p_kanonisk TEXT, p_oppgitt_hash TEXT, p_aktor TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_hash TEXT; v_skjema JSONB; v_typefeil TEXT; v_ny INT;
BEGIN
    v_hash := encode(sha256(convert_to(p_kanonisk, 'UTF8')), 'hex');
    IF v_hash IS DISTINCT FROM p_oppgitt_hash THEN
        RAISE EXCEPTION 'artefaktskjema: oppgitt hash % matcher ikke'
            ' innholdet (rekalkulert %)', p_oppgitt_hash, v_hash
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Strengliteralene fjernes først (blanktegn INNE i en streng er
    -- innhold, ikke formatering); står det blanktegn igjen, er teksten
    -- ikke JCS-utdata.
    IF regexp_replace(p_kanonisk, '"([^"\\]|\\.)*"', '', 'g') ~ '\s' THEN
        RAISE EXCEPTION 'artefaktskjema: innholdet er ikke kanonisk'
            ' (blanktegn utenfor strenger)'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_skjema := p_kanonisk::jsonb;
    -- Full JSON Schema-metavalidering krever validatoren selv, og den bor i
    -- Python (`api.artefaktskjema.registrer`, som er DEN delte
    -- registreringsveien derfra). Grensen står uttalt her i stedet for at
    -- funksjonen later som den har kontrollert mer enn den har.
    --
    -- Men SQL-siden er ikke tom (Codex P2, runde 2 og 3): begge
    -- admin-rollene har EXECUTE, så en direkte kaller ser aldri
    -- Python-sjekken, og gapet er ikke reparerbart etterpå — skjemaraden
    -- OG typebindingen er immutable, så en artefakttype bundet til et
    -- ødelagt skjema ville dødd på hver opplastning for alltid. Derfor
    -- kjøres `_artefaktskjema_typefeil` her, og den dekker nå hele
    -- nøkkelordgrammatikken i Draft 2020-12 — ikke bare `type`, men også
    -- `{"required": "x"}` og `{"minLength": "x"}`, som `check_schema()`
    -- avviser og SQL-veien slapp gjennom.
    --
    -- `valider()` kjører metasjekken en tredje gang, før innhold måles,
    -- slik at et skjema som likevel skulle ha kommet inn gir en ærlig
    -- avvisning i stedet for en 500-er.
    --
    -- REFERANSEOPPSLAG er en uttalt grense her, ikke en glemsel (Codex
    -- P2): å avgjøre om en `$ref` treffer noe krever at JSON-pekere løses
    -- mot dokumentet, og den vandringen bor i Python
    -- (`api.artefaktskjema._referansefeil`, kjørt på den delte
    -- registreringsveien FØR innsetting). Kommer en direkte SQL-kaller
    -- likevel forbi, er skaden ikke lenger en permanent 500-er: `valider`
    -- fanger oppslagsfeilen og svarer med en avvisning.
    IF jsonb_typeof(v_skjema) <> 'object' THEN
        RAISE EXCEPTION 'artefaktskjema: skjemaet må være et objekt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_typefeil := public._artefaktskjema_typefeil(v_skjema);
    IF v_typefeil IS NOT NULL THEN
        RAISE EXCEPTION 'artefaktskjema: ugyldig skjema — %', v_typefeil
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- `skjema` settes IKKE her: triggeren utleder den av `kanonisk`, så
    -- raden bærer de bytene hashen faktisk er over.
    INSERT INTO public.artefaktskjema (skjema_hash, kanonisk)
        VALUES (v_hash, p_kanonisk)
        ON CONFLICT (skjema_hash) DO NOTHING;
    GET DIAGNOSTICS v_ny = ROW_COUNT;
    IF v_ny > 0 THEN
        INSERT INTO public.modulregister_hendelse (modul_id, hendelse, aktor,
                                                   detalj)
            VALUES ('plattform', 'artefaktskjema_registrert', p_aktor,
                    jsonb_build_object('skjema_hash', v_hash));
    END IF;
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
--    Bytene settes inn som BYTER (`kanonisk`), ikke som jsonb: hashen
--    over er sha256 av nøyaktig denne strengen, og `skjema` utledes av
--    den. En `::jsonb` her ville vært den samme driften vakten stopper.
INSERT INTO artefaktskjema (skjema_hash, kanonisk)
    VALUES ('e30ef85662f0967117cf3d0dc2e28b9efd3da50b501429be79bd8e5cea5fc40e',
            '{"additionalProperties":false,"properties":{"kjoring_id":{"minLength":8,"type":"string"},"resultat":{"enum":["ok","feil"],"type":"string"},"tidspunkt":{"format":"date-time","type":"string"}},"required":["kjoring_id","tidspunkt","resultat"],"type":"object"}')
    ON CONFLICT (skjema_hash) DO NOTHING;

-- ------------------------------------------------------------
-- 7. PORTEN som krever at hver registrert artefakttype har et oppslagbart
--    skjema er INGEN MIGRASJON. Den står i `deploy/staging/
--    deployport-modultyper.py`, som steg 6b i opp.sh (Codex P1, rd. 1-3).
--
-- Kravet er ekte: fra og med denne filen slår `/v1/artefakt` opp skjemaet
-- ubetinget, og en type registrert på en oppgradert base før 036 kan bære
-- en `skjema_hash` uten rad i `artefaktskjema` — bindingen er en HASH,
-- ikke en fremmednøkkel. Innholdet kan ikke bakfylles fra basen (den har
-- hashen, ikke skjemaet), så noe MÅ stoppe før den nye koden aktiveres.
--
-- Men det kan ikke være en migrasjon, av to grunner som begge er lært:
--
--   * Sto blokka i DENNE filen, rullet unntaket tilbake den samme
--     transaksjonen som oppretter `artefaktskjema` og
--     `registrer_artefaktskjema`. Reparasjonen porten ber om krever
--     nettopp det verktøyet, så hvert nye forsøk feilet identisk.
--   * Sto den i en EGEN migrasjonsfil (037), var den gjenopprettelig i
--     runtime-basen — men `opp.sh` migrerer BEGGE basene, og testbasen
--     bærer syntetiske typerader per konstruksjon: pre-036-tester
--     committet rader med tilfeldige hasher det aldri har eksistert et
--     skjema for. Ingen kan produsere et skjema som hasher til `_hex64()`,
--     så porten låste den persistente testbasen for godt.
--
-- Deploy-porten kjører mot RUNTIME-DSN-en alene — nøyaktig det skillet
-- steg 6b finnes for — etter migrasjonene og før release-byttet. Da er
-- lageret og funksjonen på plass, den GAMLE koden står fortsatt og tar
-- imot opplastninger, og operatøren kan registrere de navngitte skjemaene
-- og kjøre opp.sh på nytt. Deployen stopper fortsatt; den er bare ikke
-- lenger en blindvei — for noen av basene.
-- ------------------------------------------------------------
