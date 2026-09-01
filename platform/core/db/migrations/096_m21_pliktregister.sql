-- 096: M-21 avtale- og fristagent v1 — FORPLIKTELSESREGISTERET og
-- fristvarslene. Tre tenant-skopede tabeller, fem dører og ÉN sveip som
-- kjøres som FORPASS i varselsenderen.
--
-- V1-AVGRENSNINGEN, ORDRETT FRA MANIFESTET: katalogteksten lover også
-- uttrekk av parter, datoer og plikter FRA avtaledokumenter, og
-- forberedt innsending til offentlige mottakere med kvitteringsbevis.
-- Uttrekket forutsetter dokumentanalysen modulen selv fører som
-- avhengighet; innsendingen forutsetter et mandat og et maskinelt
-- grensesnitt per mottaker. Ingen av delene finnes, og ingenting her
-- later som de gjør: plikter REGISTRERES manuelt, med eier, frist og
-- kildehenvisning.
--
-- TRE DOMMER v1 hviler på, alle håndhevet i DATAMODELLEN og ikke i et
-- API-lag som kunne omgås:
--
--   1. EN PLIKT UTEN EIER ER URESPRESENTERBAR. «Plikter uten eier» er
--      katalogens egen KPI; her er den en NOT NULL med fremmednøkkel mot
--      `brukeridentitet`, ikke en rapport. En plikt ingen eier er en
--      plikt ingen gjør.
--
--   2. EN FRIST LUKKES ALDRI AV AT TIDEN GÅR. `status='lukket'` krever
--      kvittering (CHECK), og enhver statusovergang krever en NAVNGITT
--      AKTØR i sesjonen (vakten i §2). Det finnes ingen jobb i denne
--      migrasjonen som setter status — sveipen KØER VARSEL og rører ikke
--      statuskolonnen i det hele tatt. `bortfalt` er den eksplisitte
--      skrevne statusen katalogens akseptkrav åpner for, og den krever en
--      ikke-tom begrunnelse.
--
--   3. VARSELET ER IDEMPOTENT PER (plikt, varslingspunkt, frist).
--      `pliktvarsel_sendt` er ankeret, og køingen skjer i SAMME
--      TRANSAKSJON som ankerraden: et varsel uten anker, eller et anker
--      uten varsel, er urepresenterbart. En frist som nærmer seg over
--      mange sveip gir ETT varsel per punkt — varsler som gjentar seg er
--      varsler folk lærer seg å overse, og da forsvinner de viktige med
--      dem.
--
-- SVEIPEN ER ET FORPASS, IKKE EN NY TIMER. `m21_koe_fristvarsler` kalles
-- fra `platform/drift/varselsender.py` sitt pre-pass (035/090/091-formen),
-- fordi senderen er den ene timerdrevne prosessen som allerede eier
-- varselkøens rytme, backoff og idempotens. En ny varslingsvei er en ny
-- vei å miste et varsel i. Prisen er invarianten
-- `forpass_stanset_ordinaer_sending`: en feil i forpasset skal under
-- INGEN omstendighet stanse den ordinære sendingen — derfor kjører
-- forpasset i sin EGEN transaksjon, med sin egen feiltelling, i senderen.
-- M-21 har av samme grunn INGEN egen LOGIN-rolle: sveipen kjører som
-- `disponit_varselsender`, og den rollen får nøyaktig ÉN ny EXECUTE og
-- ingen tabellrettigheter.
--
-- FORMENE ER HUSETS (089/090/091): tabellene eies av migrator, dørene av
-- NOLOGIN-rollen `disponit_plikt_eier` (registrert i
-- `deploy/staging/eierskap-reparasjon.sql`), tenant TEXT + RLS
-- ENABLE+FORCE + `tenant_isolasjon` på hver tabell, SP-1
-- (`krev_tenantkontekst` FØRST) i hver tenantbundet definer, og INGEN
-- BYPASSRLS: kryss-tenant-autoriteten sveipen trenger er en EKSPLISITT,
-- SNEVER policy (§1) og ikke en rolleegenskap.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_plikt_eier') THEN
        RAISE EXCEPTION 'rollen disponit_plikt_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `plikt` — forpliktelsen selv. Én rad per plikt per tenant.
--
-- `frist_ts` er DEN GJELDENDE forekomstens frist. For en `engang`-plikt
-- er den også den eneste; for en gjentakende plikt flyttes den fram av
-- `m21_lukk_plikt` når forekomsten kvitteres ut (se §3). Det er derfor
-- `frist_ts` er med i ankerets nøkkel: en flyttet frist er en NY
-- forekomst som skal varsles på nytt — og det gjelder like mye når en
-- frist er korrigert som når den er rullet.
CREATE TABLE plikt (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    plikt_id UUID NOT NULL,
    tittel TEXT NOT NULL CHECK (length(btrim(tittel)) > 0),
    -- DOM 1. NOT NULL + FK mot identiteten, ikke mot medlemskapet: mister
    -- eieren medlemskapet skal plikten fortsatt ha en eier å navngi (og
    -- registerets lesedør viser den), men raden skal ikke rives ut under
    -- en åpen frist. Samme valg som `varsel.bruker_id` (026).
    eier_bruker_id TEXT NOT NULL REFERENCES brukeridentitet (bruker_id),
    -- HJEMMELEN. Henvisningen til avtalen, paragrafen eller vedtaket
    -- plikten kommer av. Ikke-tom med vilje: en plikt uten kilde er en
    -- påstand, og et fristvarsel på en påstand er støy.
    kilde TEXT NOT NULL CHECK (length(btrim(kilde)) > 0),
    frist_ts TIMESTAMPTZ NOT NULL,
    gjentakelse TEXT NOT NULL DEFAULT 'engang'
        CHECK (gjentakelse IN ('engang', 'aarlig', 'kvartalsvis',
                               'manedlig')),
    status TEXT NOT NULL DEFAULT 'apen'
        CHECK (status IN ('apen', 'lukket', 'bortfalt')),
    -- Kvitteringen for den SIST utkvitterte forekomsten. For en
    -- `engang`-plikt er den kvitteringen som lukket den.
    kvittering_ref TEXT,
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT,
    bortfall_begrunnelse TEXT,
    bortfalt_ts TIMESTAMPTZ,
    bortfalt_av TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    CONSTRAINT plikt_pk PRIMARY KEY (tenant, plikt_id),
    -- DOM 2, i skjemaet: en plikt kan ikke STÅ som lukket uten at det
    -- finnes en kvittering, et tidspunkt og et menneske bak. Dette er
    -- invarianten `frist_lukket_uten_kvittering`, og den gjelder ENHVER
    -- skrivevei — også direkte DML som eier.
    CONSTRAINT plikt_lukket_krever_kvittering CHECK (
        status <> 'lukket'
        OR (kvittering_ref IS NOT NULL AND length(btrim(kvittering_ref)) > 0
            AND lukket_ts IS NOT NULL AND lukket_av IS NOT NULL)),
    -- `bortfalt` er den ANDRE lovlige utgangen — plikten gjelder ikke
    -- lenger. Den er billigere enn en kvittering og skal derfor koste en
    -- skreven begrunnelse: uten den ville «bortfalt» vært en gratis vei
    -- ut av enhver frist.
    CONSTRAINT plikt_bortfalt_krever_begrunnelse CHECK (
        status <> 'bortfalt'
        OR (bortfall_begrunnelse IS NOT NULL
            AND length(btrim(bortfall_begrunnelse)) > 0
            AND bortfalt_ts IS NOT NULL AND bortfalt_av IS NOT NULL))
);

-- Sveipens skann og flatens liste leser begge «åpne plikter, frist
-- først». Delindeks på `apen`: lukkede og bortfalte plikter er
-- historikk, og sveipen skal aldri betale for dem.
CREATE INDEX plikt_apen_frist ON plikt (tenant, frist_ts)
    WHERE status = 'apen';

-- `pliktvarsling` — varslingspunktene. `dogn_for` er antall døgn før
-- fristen punktet utløser på. Seedes med husets standard (30/7/1) ved
-- registrering og kan overstyres per plikt i samme kall.
CREATE TABLE pliktvarsling (
    tenant TEXT NOT NULL,
    plikt_id UUID NOT NULL,
    dogn_for INT NOT NULL CHECK (dogn_for >= 0 AND dogn_for <= 3650),
    CONSTRAINT pliktvarsling_pk PRIMARY KEY (tenant, plikt_id, dogn_for),
    CONSTRAINT pliktvarsling_plikt_fk FOREIGN KEY (tenant, plikt_id)
        REFERENCES plikt (tenant, plikt_id)
);

-- `pliktvarsel_sendt` — IDEMPOTENSANKERET (dom 3). PK-en bærer
-- `frist_ts` fordi forekomsten, ikke plikten, er det som varsles: uten
-- leddet ville en gjentakende plikt fått varsel om FØRSTE forekomst og
-- aldri om de neste, og en korrigert frist ville arvet den gamle
-- fristens taushet.
--
-- Raden skrives i SAMME TRANSAKSJON som `varsel`-raden den svarer til.
-- Append-only (vakten i §2): et anker som kan slettes er et anker som
-- kan varsle på nytt.
CREATE TABLE pliktvarsel_sendt (
    tenant TEXT NOT NULL,
    plikt_id UUID NOT NULL,
    dogn_for INT NOT NULL,
    frist_ts TIMESTAMPTZ NOT NULL,
    koet_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Peker på `varsel.id`. Ingen fremmednøkkel med vilje: varsler er
    -- driftsdata som skal kunne ryddes (026), mens ankeret er
    -- idempotensen og skal overleve ryddingen. En FK ville gjort
    -- varselryddingen til en vei tilbake til dobbeltvarsling.
    varsel_ref BIGINT NOT NULL,
    CONSTRAINT pliktvarsel_sendt_pk
        PRIMARY KEY (tenant, plikt_id, dogn_for, frist_ts),
    CONSTRAINT pliktvarsel_sendt_plikt_fk FOREIGN KEY (tenant, plikt_id)
        REFERENCES plikt (tenant, plikt_id)
);

-- ------------------------------------------------------------
-- 2. Radvaktene og radsikkerheten.
-- ------------------------------------------------------------

-- Vakten på `plikt`. Fire regler, og den tredje er DOM 2:
--
--   * DELETE avvises. Et forpliktelsesregister der rader kan forsvinne
--     er et register ingen revisjon kan lese bakover.
--   * Identiteten er frosset (tenant, plikt_id, opprettet, kilde). En
--     annen hjemmel er en ANNEN plikt, ikke en redigering av denne.
--   * EN STATUSOVERGANG ER FORFATTET, ALDRI AVLEDET. Enhver endring av
--     `status` krever en navngitt aktør i sesjonen
--     (`disponit.aktor`), og den aktøren MÅ være den som står i
--     `lukket_av`/`bortfalt_av`. En jobb som skulle lukke en frist
--     fordi tiden gikk har ingen aktør å skrive — og skrev den en, ville
--     navnet stått i raden for enhver som leser. Terminale statuser er
--     terminale: ut av `lukket`/`bortfalt` finnes ingen vei.
--   * `frist_ts` kan bare flyttes FRAMOVER. Det er rullingen av en
--     gjentakende plikt (§3) og korreksjonen av en feilskrevet frist —
--     aldri en vei til å skyve en forekomst bak et anker som alt er
--     skrevet.
CREATE FUNCTION m21_plikt_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT;
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'plikt: % avvist — plikter lukkes eller bortfaller,'
            ' de slettes aldri som rader', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.plikt_id IS DISTINCT FROM OLD.plikt_id
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.kilde IS DISTINCT FROM OLD.kilde THEN
        RAISE EXCEPTION 'plikt: identiteten (tenant, plikt_id, opprettet,'
            ' kilde) er frosset — en annen hjemmel er en annen plikt'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.frist_ts < OLD.frist_ts THEN
        RAISE EXCEPTION 'plikt: fristen kan bare flyttes framover —'
            ' en frist som flyttes bakover gjemmer seg bak et anker som'
            ' alt er skrevet'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF OLD.status <> 'apen' THEN
            RAISE EXCEPTION 'plikt: % er terminal — en lukket eller'
                ' bortfalt plikt gjenåpnes ikke', OLD.status
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL THEN
            RAISE EXCEPTION 'plikt: en statusovergang krever en navngitt'
                ' aktør (disponit.aktor) — tiden lukker ingenting'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.status = 'lukket' AND NEW.lukket_av IS DISTINCT FROM v_aktor
        THEN
            RAISE EXCEPTION 'plikt: lukket_av (%) er ikke aktøren som'
                ' lukker (%)', coalesce(NEW.lukket_av, '<null>'), v_aktor
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.status = 'bortfalt'
           AND NEW.bortfalt_av IS DISTINCT FROM v_aktor THEN
            RAISE EXCEPTION 'plikt: bortfalt_av (%) er ikke aktøren som'
                ' feller bortfallet (%)',
                coalesce(NEW.bortfalt_av, '<null>'), v_aktor
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m21_plikt_vakt() FROM PUBLIC;
CREATE TRIGGER m21_plikt_vakt
    BEFORE UPDATE OR DELETE ON plikt
    FOR EACH ROW EXECUTE FUNCTION m21_plikt_vakt();
CREATE TRIGGER m21_plikt_ingen_truncate
    BEFORE TRUNCATE ON plikt
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- Ankeret er append-only mot BÅDE UPDATE og DELETE. Det er hele
-- idempotensen: kunne raden fjernes, kunne varselet køes på nytt.
CREATE FUNCTION m21_anker_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'pliktvarsel_sendt er append-only: % er forbudt —'
        ' ankeret ER idempotensen', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE ALL ON FUNCTION m21_anker_vakt() FROM PUBLIC;
CREATE TRIGGER m21_anker_vakt
    BEFORE UPDATE OR DELETE ON pliktvarsel_sendt
    FOR EACH ROW EXECUTE FUNCTION m21_anker_vakt();
CREATE TRIGGER m21_anker_ingen_truncate
    BEFORE TRUNCATE ON pliktvarsel_sendt
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

ALTER TABLE plikt ENABLE ROW LEVEL SECURITY;
ALTER TABLE plikt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON plikt
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — ingen BYPASSRLS.
--
-- Sveipen må finne HVILKE tenanter som har en frist på vei, og det
-- spørsmålet kan ikke stilles innenfra én tenantkontekst. Autoriteten
-- er derfor en policy, ikke en rolleegenskap, og den er gjerdet tre
-- ganger:
--
--   * bare `disponit_plikt_eier` (dørenes eier — ingen LOGIN-rolle),
--   * bare SELECT (sveipen SKRIVER aldri kryss-tenant: hver innsetting
--     skjer etter at konteksten er bundet til RADENS tenant),
--   * bare når det IKKE står en tenantkontekst i sesjonen.
--
-- Det siste leddet er det bærende. Dørene i §3 kommer alltid gjennom
-- `krev_tenantkontekst`, som fail-closed krever en ikke-tom kontekst —
-- inne i en dør er denne policyen derfor ALLTID usann, og `tenant_isolasjon`
-- er den eneste som gjelder. De to er disjunkte per konstruksjon, så
-- kryss-tenant-synet finnes nøyaktig i det ene vinduet sveipen bruker
-- det, og ingen annen kodevei kan snuble inn i det.
CREATE POLICY m21_sveip_tenantliste ON plikt
    FOR SELECT TO disponit_plikt_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

ALTER TABLE pliktvarsling ENABLE ROW LEVEL SECURITY;
ALTER TABLE pliktvarsling FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON pliktvarsling
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE pliktvarsel_sendt ENABLE ROW LEVEL SECURITY;
ALTER TABLE pliktvarsel_sendt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON pliktvarsel_sendt
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- Rettighetene dørenes eier trenger, og ikke mer. Merk hva som IKKE
-- står her: ingen runtime-rolle får en eneste tabellrettighet på de tre
-- tabellene (SP-7, 090/091-formen) — hele registeret nås KUN gjennom
-- dørene i §3, og de krever tenantkontekst først.
GRANT SELECT, INSERT, UPDATE ON plikt TO disponit_plikt_eier;
GRANT SELECT, INSERT ON pliktvarsling TO disponit_plikt_eier;
GRANT SELECT, INSERT ON pliktvarsel_sendt TO disponit_plikt_eier;
-- Fremmednøkkelen mot identiteten opprettes over (som migrator, som
-- eier begge tabellene); eieren trenger REFERENCES bare hvis en senere
-- migrasjon skulle legge til flere.
GRANT REFERENCES ON brukeridentitet TO disponit_plikt_eier;
-- Varselveien: køing, kanalvalget og medlemskapssjekken ved
-- registrering. Alle tre er RLS-gjerdet på tenant, og eieren har INGEN
-- lesetilgang til varselinnholdet den ikke selv skrev.
GRANT INSERT ON varsel TO disponit_plikt_eier;
-- KOLONNEGRANT, ikke tabellgrant (husregelen): sveipen trenger id-en
-- den nettopp skrev for å binde ankeret til varselet — og INGENTING
-- annet. At registerets eier aldri kan lese et varsels innhold, heller
-- ikke sitt eget, skal være en egenskap ved BASEN og ikke en egenskap
-- ved koden som tilfeldigvis ikke gjør det.
GRANT SELECT (id) ON varsel TO disponit_plikt_eier;
GRANT SELECT ON varselvalg TO disponit_plikt_eier;
GRANT SELECT ON brukermedlemskap TO disponit_plikt_eier;
-- Visningsnavnet i lesedøren. Kolonnegrant igjen: `issuer` og `sub` er
-- identitetens hemmelige halvdel (010), og pliktregisteret har ingen
-- bruk for dem.
GRANT SELECT (bruker_id, profil) ON brukeridentitet TO disponit_plikt_eier;
-- EVIDENSKJEDEN (m02, manifestets ene reelle avhengighet): hver
-- pliktovergang og hvert køet fristvarsel skriver sin egen loggpost, i
-- SAMME transaksjon som handlingen. Se §3. INSERT alene — evidenskjeden
-- skrives til, den leses aldri herfra.
GRANT INSERT ON revisjonslogg TO disponit_plikt_eier;

-- Kontekstporten eies av `disponit_m37_claimer` og er REVOKEd fra
-- PUBLIC (038). Dørene under er SECURITY DEFINER og løper som
-- `disponit_plikt_eier` — uten dette grantet ville SP-1-porten feilet
-- med «permission denied», og registeret vært nede i stedet for sikret.
-- Grantet gis av eieren selv (039-formen); migrator er medlem av begge.
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_plikt_eier;
RESET ROLE;

-- ------------------------------------------------------------
-- 3. Dørene. SECURITY DEFINER, eid av `disponit_plikt_eier`, og hver
--    tenantbundet dør kaller `krev_tenantkontekst` FØRST (SP-1).
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_plikt_eier;

-- Evidenskjeden, ett sted. Kalles av hver dør og av sveipen, i deres
-- egen transaksjon.
--
-- ÆRLIG OM FORMEN: `revisjonslogg` har ingen ciphertext-kolonner (041
-- §4 dokumenterer det mot levende base), så `payload_type='kryptert'`
-- med `referansepayload IS NULL` er den ordinære formen HVER eksisterende
-- skriver bruker — ikke en påstand om at det finnes en kryptert payload
-- et sted. `referanse`-formen er lukket til domeneovertakelses-familien
-- av `er_gyldig_referansepayload`, og å utvide DEN validatoren for en
-- fristhendelse ville vært å låne en tolkning M-21 ikke er blitt gitt.
-- `beslutning='TILLAT'` fordi handlingen ER tillatt og utført; en
-- pliktregistrering føder ingen sak.
--
-- `input_hash` er sha256 over den kanoniske beskrivelsen av handlingen,
-- ikke over kundedata: pliktens id, hva som skjedde og fristen det
-- gjaldt. Tittelen og kilden står ALDRI her — de er kundens tekst, og
-- evidenskjeden skal kunne gjenfinne handlingen uten å arkivere
-- innholdet på nytt.
CREATE FUNCTION m21_evidens(p_tenant TEXT, p_plikt_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm21_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm21_avtalefrist', 'handling', p_handling,
        'plikt_id', p_plikt_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm21_avtalefrist',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:pliktregister', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m21_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- Husets standard varslingspunkter: 30, 7 og 1 døgn før frist. Samme
-- trekant som familiehorisonten (035) og av samme grunn — en langt
-- varsel til å planlegge etter, ett til å begynne på, og ett til å ikke
-- glemme.
CREATE FUNCTION m21_standardpunkter() RETURNS INT[]
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
    SELECT ARRAY[30, 7, 1]
$$;
REVOKE ALL ON FUNCTION m21_standardpunkter() FROM PUBLIC;

-- Registreringsdøren. SP-2-materialitet på `p_plikt_id` (m35-formen):
-- kalleren utleder id-en deterministisk av sin Idempotency-Key, så et
-- gjenspill med identisk innhold er et STILLE JA (false), mens samme id
-- med ANNET innhold er en materiell konflikt.
CREATE FUNCTION m21_registrer_plikt(
    p_tenant TEXT, p_plikt_id UUID, p_tittel TEXT, p_eier_bruker_id TEXT,
    p_kilde TEXT, p_frist_ts TIMESTAMPTZ, p_gjentakelse TEXT,
    p_dogn_for INT[], p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_punkter INT[]; v_gamle_punkter INT[];
        v_gammel RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm21_registrer_plikt');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    -- DOM 1, håndhevet FØR innsettingen slik at feilmeldingen sier hva
    -- som er galt: eieren må være et AKTIVT medlem av tenanten. FK-en
    -- alene sier bare at bruker-id-en finnes et sted i plattformen — og
    -- en plikt eid av en fremmed tenants bruker er en plikt ingen her
    -- gjør.
    IF NOT EXISTS (SELECT 1 FROM public.brukermedlemskap bm
                    WHERE bm.tenant = p_tenant
                      AND bm.bruker_id = p_eier_bruker_id AND bm.aktiv) THEN
        RAISE EXCEPTION 'm21_registrer_plikt: % er ikke et aktivt medlem'
            ' av tenanten — en plikt uten eier her er en plikt ingen gjør',
            p_eier_bruker_id USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_punkter := COALESCE(nullif(p_dogn_for, ARRAY[]::INT[]),
                          public.m21_standardpunkter());
    INSERT INTO public.plikt (tenant, plikt_id, tittel, eier_bruker_id,
                              kilde, frist_ts, gjentakelse, opprettet_av)
    VALUES (p_tenant, p_plikt_id, p_tittel, p_eier_bruker_id, p_kilde,
            p_frist_ts, COALESCE(p_gjentakelse, 'engang'), p_aktor)
        ON CONFLICT (tenant, plikt_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        -- SP-2: samme id igjen. Identisk innhold er et stille ja; annet
        -- innhold er en materiell konflikt kalleren SKAL se.
        --
        -- MATERIALITETEN DEKKER HELE PLIKTEN, ikke bare hodet
        -- (CodeRabbit): `gjentakelse` avgjør om fristen ruller, og
        -- varslingspunktene avgjør NÅR noen får vite om den. Et
        -- gjenspill som endret ett av dem ville fått et stille ja på en
        -- plikt som varsler noe annet enn den kalleren tror den
        -- registrerte — og SP-2s hele poeng er at et gjenspill ikke skal
        -- kunne endre noe i det stille.
        SELECT * INTO v_gammel FROM public.plikt
         WHERE tenant = p_tenant AND plikt_id = p_plikt_id;
        SELECT array_agg(v.dogn_for ORDER BY v.dogn_for)
          INTO v_gamle_punkter
          FROM public.pliktvarsling v
         WHERE v.tenant = p_tenant AND v.plikt_id = p_plikt_id;
        IF v_gammel.tittel IS DISTINCT FROM p_tittel
           OR v_gammel.eier_bruker_id IS DISTINCT FROM p_eier_bruker_id
           OR v_gammel.kilde IS DISTINCT FROM p_kilde
           OR v_gammel.frist_ts IS DISTINCT FROM p_frist_ts
           OR v_gammel.gjentakelse
              IS DISTINCT FROM COALESCE(p_gjentakelse, 'engang')
           OR v_gamle_punkter IS DISTINCT FROM (
                  SELECT array_agg(DISTINCT d ORDER BY d)
                    FROM unnest(v_punkter) AS d) THEN
            RAISE EXCEPTION 'm21_registrer_plikt: samme plikt_id med annet'
                ' innhold — materiell konflikt'
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN false;
    END IF;
    INSERT INTO public.pliktvarsling (tenant, plikt_id, dogn_for)
    SELECT p_tenant, p_plikt_id, d
      FROM unnest(v_punkter) AS d
     GROUP BY d;
    PERFORM public.m21_evidens(
        p_tenant, p_plikt_id, 'plikt.registrert', p_aktor,
        jsonb_build_object('frist_ts', p_frist_ts,
                           'gjentakelse', COALESCE(p_gjentakelse, 'engang'),
                           'eier_bruker_id', p_eier_bruker_id,
                           'dogn_for', to_jsonb(v_punkter)));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m21_registrer_plikt(
    TEXT, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ, TEXT, INT[], TEXT)
    FROM PUBLIC;

-- Neste forekomst av en gjentakende plikt: fristen flyttes med sitt eget
-- intervall, og videre til den ligger FRAM I TID. En plikt som ikke er
-- kvittert på tre kvartaler skal ikke gi tre etterslepte forekomster å
-- varsle om — den skal gi den neste som faktisk kommer.
CREATE FUNCTION m21_neste_frist(p_frist TIMESTAMPTZ, p_gjentakelse TEXT)
RETURNS TIMESTAMPTZ LANGUAGE plpgsql STABLE
SET search_path = pg_catalog AS $$
DECLARE v_steg INTERVAL; v_ny TIMESTAMPTZ;
BEGIN
    v_steg := CASE p_gjentakelse
                  WHEN 'aarlig' THEN interval '1 year'
                  WHEN 'kvartalsvis' THEN interval '3 months'
                  WHEN 'manedlig' THEN interval '1 month'
              END;
    IF v_steg IS NULL THEN
        RETURN NULL;                      -- 'engang' har ingen neste
    END IF;
    -- STABLE og ikke IMMUTABLE: funksjonen LESER transaksjonsklokka, og
    -- en feilmerket volatilitet er en av de få feilene planleggeren gjør
    -- permanent (den ville konstantfoldet svaret inn i en plan som
    -- overlever transaksjonen).
    v_ny := p_frist + v_steg;
    WHILE v_ny <= now() LOOP
        v_ny := v_ny + v_steg;
    END LOOP;
    RETURN v_ny;
END $$;
REVOKE ALL ON FUNCTION m21_neste_frist(TIMESTAMPTZ, TEXT) FROM PUBLIC;

-- Lukkedøren. KVITTERINGEN ER PÅKREVD — det er hele akseptkravet, og
-- den står her som en RAISE og ikke bare som en CHECK, for at feilen
-- skal si hvorfor.
--
-- For en `engang`-plikt er lukkingen terminal. For en GJENTAKENDE plikt
-- kvitteres FOREKOMSTEN ut: kvitteringen skrives i raden, og fristen
-- rulles til neste forekomst mens plikten står `apen`. Det er den ene
-- måten en gjentakende plikt kan være både «gjort for denne gangen» og
-- «gjelder fortsatt», og det er nettopp derfor `frist_ts` er med i
-- ankerets nøkkel: den nye forekomsten er et NYTT sett varslingspunkter
-- å fyre på. Selve PLIKTEN opphører gjennom `m21_marker_bortfalt` — det
-- er den skrevne veien ut, og den koster en begrunnelse.
--
-- Returnerer den nye fristen for en gjentakende plikt, NULL når plikten
-- ble lukket.
CREATE FUNCTION m21_lukk_plikt(
    p_tenant TEXT, p_plikt_id UUID, p_kvittering_ref TEXT, p_aktor TEXT)
RETURNS TIMESTAMPTZ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE p RECORD; v_ny TIMESTAMPTZ;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm21_lukk_plikt');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_kvittering_ref IS NULL
       OR length(btrim(p_kvittering_ref)) = 0 THEN
        RAISE EXCEPTION 'm21_lukk_plikt: en frist lukkes av en kvittering,'
            ' aldri av at tiden går — kvitteringsreferansen kan ikke være'
            ' tom' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO p FROM public.plikt
     WHERE tenant = p_tenant AND plikt_id = p_plikt_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm21_lukk_plikt: ukjent plikt %', p_plikt_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF p.status <> 'apen' THEN
        RAISE EXCEPTION 'm21_lukk_plikt: plikten er alt %', p.status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- GJENSPILLET (CodeRabbit P1). For en ENGANGS-plikt fanges en retry
    -- av statussjekken over: den er `lukket` og kallet avvises. For en
    -- GJENTAKENDE plikt gjør den ikke det — raden står `apen` igjen etter
    -- rullingen — og uten denne grenen ville en tapt respons + nytt
    -- klikk rullet fristen EN GANG TIL og hoppet over en hel forekomst i
    -- stillhet. Det er nøyaktig den feilen som ikke oppdages: ingen
    -- feilmelding, ingen dublett, bare en frist som stille ble borte.
    --
    -- IDENTITETEN ER KVITTERINGEN, og det er ikke en forenkling: en
    -- kvitteringsreferanse ER beviset for ÉN levering (et arkivnummer,
    -- et saksnummer, mottakerens kvittering). Den samme referansen kan
    -- ikke kvittere ut to forekomster — og at den ikke kan det, er en
    -- egenskap registeret bør ha uansett.
    IF p.lukket_ts IS NOT NULL
       AND p.kvittering_ref IS NOT DISTINCT FROM p_kvittering_ref
       AND p.lukket_av IS NOT DISTINCT FROM p_aktor THEN
        RETURN p.frist_ts;      -- alt utkvittert med DENNE kvitteringen
    END IF;
    v_ny := public.m21_neste_frist(p.frist_ts, p.gjentakelse);
    IF v_ny IS NULL THEN
        UPDATE public.plikt
           SET status = 'lukket', kvittering_ref = p_kvittering_ref,
               lukket_ts = now(), lukket_av = p_aktor
         WHERE tenant = p_tenant AND plikt_id = p_plikt_id;
    ELSE
        UPDATE public.plikt
           SET kvittering_ref = p_kvittering_ref,
               lukket_ts = now(), lukket_av = p_aktor, frist_ts = v_ny
         WHERE tenant = p_tenant AND plikt_id = p_plikt_id;
    END IF;
    PERFORM public.m21_evidens(
        p_tenant, p_plikt_id, 'plikt.kvittert', p_aktor,
        jsonb_build_object('frist_ts', p.frist_ts,
                           'kvittering_ref', p_kvittering_ref,
                           'neste_frist_ts', v_ny,
                           'gjentakelse', p.gjentakelse));
    RETURN v_ny;
END $$;
REVOKE ALL ON FUNCTION m21_lukk_plikt(TEXT, UUID, TEXT, TEXT) FROM PUBLIC;

-- Bortfallsdøren. Den ANDRE lovlige utgangen: plikten gjelder ikke
-- lenger. Begrunnelsen er påkrevd, av samme grunn som kvitteringen er
-- det på lukkedøren — uten den ville «bortfalt» vært en gratis vei ut av
-- enhver frist, og hele registeret ville vært en liste over ting man kan
-- klikke bort.
CREATE FUNCTION m21_marker_bortfalt(
    p_tenant TEXT, p_plikt_id UUID, p_begrunnelse TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE p RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm21_marker_bortfalt');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_begrunnelse IS NULL OR length(btrim(p_begrunnelse)) = 0 THEN
        RAISE EXCEPTION 'm21_marker_bortfalt: bortfall krever en skreven'
            ' begrunnelse — uten den er det en frist som forsvinner'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO p FROM public.plikt
     WHERE tenant = p_tenant AND plikt_id = p_plikt_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm21_marker_bortfalt: ukjent plikt %', p_plikt_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF p.status <> 'apen' THEN
        RAISE EXCEPTION 'm21_marker_bortfalt: plikten er alt %', p.status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.plikt
       SET status = 'bortfalt', bortfall_begrunnelse = p_begrunnelse,
           bortfalt_ts = now(), bortfalt_av = p_aktor
     WHERE tenant = p_tenant AND plikt_id = p_plikt_id;
    PERFORM public.m21_evidens(
        p_tenant, p_plikt_id, 'plikt.bortfalt', p_aktor,
        jsonb_build_object('frist_ts', p.frist_ts,
                           'begrunnelse_lengde', length(p_begrunnelse)));
END $$;
REVOKE ALL ON FUNCTION m21_marker_bortfalt(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

-- Lesedøren (051/090-formen): flatens hele lesetilstand i ett kall.
-- Runtime har INGEN SELECT på tabellene, så dette er den eneste veien
-- inn — og den krever tenantkontekst først. Flate rader (plikt ×
-- varslingspunkt er IKKE med: punktene er driftsdetalj, ikke noe flaten
-- viser i v1); `dogn_til_frist` regnes HER, i samme skann som raden,
-- fordi flaten ikke skal trekke to tidspunkter fra hverandre.
CREATE FUNCTION m21_plikter(p_tenant TEXT, p_grense INT)
RETURNS TABLE(plikt_id UUID, tittel TEXT, eier_bruker_id TEXT,
              eier_navn TEXT, kilde TEXT, frist_ts TIMESTAMPTZ,
              dogn_til_frist INT, gjentakelse TEXT, status TEXT,
              kvittering_ref TEXT, lukket_ts TIMESTAMPTZ, lukket_av TEXT,
              bortfall_begrunnelse TEXT, bortfalt_ts TIMESTAMPTZ,
              bortfalt_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm21_plikter');
    RETURN QUERY
    SELECT p.plikt_id, p.tittel, p.eier_bruker_id,
           -- Visningsnavnet fra den LUKKEDE profil-DTO-en (010). NULL
           -- når IdP-en ikke ga noe — flaten viser da bruker-id-en, som
           -- er ærligere enn en tom celle.
           nullif(btrim(coalesce(b.profil->>'visningsnavn', '')), ''),
           p.kilde, p.frist_ts,
           -- HELE DØGN til fristen, negativt når den er forbi. Ett tall
           -- avledet av ett tidspunkt — ikke et forhold mellom to av
           -- svarets tall (M-16-regelen).
           (floor(EXTRACT(EPOCH FROM (p.frist_ts - now())) / 86400))::int,
           p.gjentakelse, p.status, p.kvittering_ref, p.lukket_ts,
           p.lukket_av, p.bortfall_begrunnelse, p.bortfalt_ts,
           p.bortfalt_av
      FROM public.plikt p
      LEFT JOIN public.brukeridentitet b ON b.bruker_id = p.eier_bruker_id
     WHERE p.tenant = p_tenant
     -- Åpne først (det som fortsatt krever noe av noen), deretter frist
     -- stigende: det som forfaller først står øverst, og det som er
     -- forfalt står aller øverst.
     ORDER BY (p.status <> 'apen'), p.frist_ts, p.plikt_id
     LIMIT greatest(least(coalesce(p_grense, 200), 500), 1);
END $$;
REVOKE ALL ON FUNCTION m21_plikter(TEXT, INT) FROM PUBLIC;

-- ------------------------------------------------------------
-- 4. Sveipen. Kalles fra varselsenderens pre-pass.
-- ------------------------------------------------------------

-- Per-tenant-arbeidet. Egen funksjon nettopp for at PORTEN skal gjelde
-- her også: sveipen under binder konteksten til RADENS tenant og kaller
-- hit, og da går fristvarslingen gjennom nøyaktig den
-- `krev_tenantkontekst` enhver annen kaller går gjennom (038-reaperens
-- form). Porten er ikke noe sveipen slipper unna, bare noe den oppfyller
-- per tenant.
--
-- ANKER OG VARSEL I SAMME TRANSAKSJON, og et punkt om gangen under
-- advisory-lås. Rekkefølgen er varsel → anker, fordi ankeret bærer
-- `varsel_ref` NOT NULL og er append-only: id-en finnes ikke før varselet
-- er skrevet, og en etterfølgende UPDATE ville vakten (§2) med rette
-- nektet. Låsen er det som gjør rekkefølgen trygg — den eier punktet
-- gjennom hele paret, så to samtidige sveip kan ikke begge rekke å køe.
-- Ruller transaksjonen tilbake, forsvinner BEGGE; committer den, står
-- begge. Et varsel uten anker og et anker uten varsel er dermed like
-- urepresenterbare.
CREATE FUNCTION m21_koe_for_tenant(p_tenant TEXT, p_grense INT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_n INT := 0; v_kanal TEXT; v_varsel BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm21_koe_for_tenant');
    PERFORM set_config('disponit.aktor', 'fristsveip', true);
    FOR r IN
        SELECT p.plikt_id, p.tittel, p.frist_ts, p.eier_bruker_id,
               v.dogn_for
          FROM public.plikt p
          JOIN public.pliktvarsling v
            ON v.tenant = p.tenant AND v.plikt_id = p.plikt_id
         WHERE p.tenant = p_tenant
           AND p.status = 'apen'
           -- Punktet er NÅDD. En frist som alt er passert treffer alle
           -- sine punkter — en plikt som registreres for sent skal
           -- varsles, ikke ties i hjel.
           AND p.frist_ts - (v.dogn_for * interval '1 day') <= now()
           AND NOT EXISTS (
               SELECT 1 FROM public.pliktvarsel_sendt s
                WHERE s.tenant = p.tenant AND s.plikt_id = p.plikt_id
                  AND s.dogn_for = v.dogn_for AND s.frist_ts = p.frist_ts)
         -- Nærmeste frist først, og det mest presserende punktet først
         -- innen hver plikt: treffer sveipen taket sitt, er det de
         -- viktigste varslene som er ute.
         ORDER BY p.frist_ts, p.plikt_id, v.dogn_for
         LIMIT greatest(coalesce(p_grense, 100), 1)
    LOOP
        -- PUNKTET LÅSES FØRST (091-formen). Låsen er det som gjør at
        -- varselet kan skrives FØR ankeret uten at to samtidige sveip
        -- begge rekker å køe: uten den måtte ankeret vært først, og da
        -- ville `varsel_ref` krevd en etterfølgende UPDATE — som
        -- append-only-vakten på ankeret (§2) med rette nekter. Låsen er
        -- transaksjonslokal og slippes med forpassets egen commit.
        PERFORM pg_advisory_xact_lock(
            2101096,
            hashtext(p_tenant || E'\x1f' || r.plikt_id::text
                     || E'\x1f' || r.dogn_for::text
                     || E'\x1f' || r.frist_ts::text));
        -- …og re-les under låsen. Utvalget over ble lest før låsen, så
        -- en sveip som ventet her skal se det den andre nettopp skrev.
        CONTINUE WHEN EXISTS (
            SELECT 1 FROM public.pliktvarsel_sendt s
             WHERE s.tenant = p_tenant AND s.plikt_id = r.plikt_id
               AND s.dogn_for = r.dogn_for AND s.frist_ts = r.frist_ts);
        SELECT vv.kanal INTO v_kanal FROM public.varselvalg vv
         WHERE vv.tenant = p_tenant AND vv.bruker_id = r.eier_bruker_id;
        -- `hendelse` bærer BÅDE punktet og fristen: to forekomster av
        -- samme plikt er to forskjellige varsler, ikke ett gjentatt
        -- (026s begrunnelse for leddet, ordrett).
        INSERT INTO public.varsel (tenant, bruker_id, art, ressurs_type,
            ressurs_id, hendelse, tekstnokkel, parametre, epost_status)
        VALUES (p_tenant, r.eier_bruker_id, 'pliktfrist', 'plikt',
                r.plikt_id::text,
                r.dogn_for::text || '@'
                    -- Mikrosekundpresisjon i UTC: leddet må skille to
                    -- forekomster like nøyaktig som ankerets PK gjør det,
                    -- ellers ville to nære frister kollidert i
                    -- `varsel_en_per_hendelse` og felt hele sveipen.
                    || to_char(r.frist_ts AT TIME ZONE 'UTC',
                               'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                'varsel.pliktfrist',
                jsonb_build_object('tittel', r.tittel,
                                   'dogn_for', r.dogn_for,
                                   'frist', to_char(
                                       r.frist_ts AT TIME ZONE 'UTC',
                                       'YYYY-MM-DD')),
                CASE WHEN COALESCE(v_kanal, 'epost_og_portal')
                          = 'kun_portal'
                     THEN 'ikke_aktuelt' ELSE 'koet' END)
        RETURNING id INTO v_varsel;
        -- ANKERET, I SAMME TRANSAKSJON og ferdig utfylt ved fødselen.
        -- Rekkefølgen (varsel → anker) er trygg fordi låsen over eier
        -- punktet: ruller transaksjonen tilbake, forsvinner BEGGE, og
        -- committer den, står begge. Et varsel uten anker og et anker
        -- uten varsel er dermed like urepresenterbare.
        INSERT INTO public.pliktvarsel_sendt
            (tenant, plikt_id, dogn_for, frist_ts, varsel_ref)
        VALUES (p_tenant, r.plikt_id, r.dogn_for, r.frist_ts, v_varsel);
        PERFORM public.m21_evidens(
            p_tenant, r.plikt_id, 'plikt.fristvarsel_koet', 'fristsveip',
            jsonb_build_object('dogn_for', r.dogn_for,
                               'frist_ts', r.frist_ts,
                               'varsel_ref', v_varsel));
        v_n := v_n + 1;
    END LOOP;
    RETURN v_n;
END $$;
REVOKE ALL ON FUNCTION m21_koe_for_tenant(TEXT, INT) FROM PUBLIC;

-- FORPASSET. Kalles av `platform/drift/varselsender.py` i sin egen
-- transaksjon, med sin egen feilfangst — se invarianten
-- `forpass_stanset_ordinaer_sending`.
--
-- Dette er kryss-tenant-autoriteten, og den er innelukket her (038s
-- reaperdoktrine): funksjonen har ingen `p_tenant` en kaller kan velge,
-- hele utvalget ligger i policyen `m21_sveip_tenantliste` og i
-- predikatet under, og kallerens egen kontekst legges tilbake til slutt
-- så en kjøring aldri etterlater en fremmed tenant i transaksjonen den
-- ble kalt fra.
--
-- TENANTLISTEN MATERIALISERES FØR konteksten røres. Leste løkka rett
-- fra tabellen mens den satte kontekst per iterasjon, ville policyen
-- (som leser nøyaktig den GUC-en) slått seg av under føttene på sin egen
-- markør etter første tenant. Et array er billig og gjør rekkefølgen
-- deterministisk.
CREATE FUNCTION m21_koe_fristvarsler(p_grense INT DEFAULT 100)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kontekst TEXT; v_aktor TEXT; v_tenanter TEXT[];
        v_t TEXT; v_igjen INT; v_n INT := 0;
BEGIN
    v_kontekst := current_setting('disponit.tenant', true);
    v_aktor := current_setting('disponit.aktor', true);
    v_igjen := greatest(coalesce(p_grense, 100), 1);
    PERFORM set_config('disponit.tenant', '', true);
    -- KUN `plikt` leses her. Kryss-tenant-policyen (§1) står på nøyaktig
    -- den ene tabellen, og et JOIN mot `pliktvarsling` ville derfor
    -- returnert null rader uten kontekst — stille, og med et helt
    -- register som aldri varslet. Taket `dogn_for <= 3650` (CHECK-en i
    -- §1) gjør bunnfiltreringen sann og indeksbrukbar; det presise
    -- punktfilteret hører hjemme per tenant, der resten av registeret er
    -- lesbart.
    SELECT array_agg(DISTINCT p.tenant ORDER BY p.tenant)
      INTO v_tenanter
      FROM public.plikt p
     WHERE p.status = 'apen'
       AND p.frist_ts <= now() + interval '3650 days';
    FOREACH v_t IN ARRAY COALESCE(v_tenanter, ARRAY[]::TEXT[]) LOOP
        EXIT WHEN v_igjen <= 0;
        -- Én tenant om gangen, bundet til RADENS tenant — og gjennom
        -- den samme porten alle andre kallere går gjennom.
        PERFORM set_config('disponit.tenant', v_t, true);
        v_n := v_n + public.m21_koe_for_tenant(v_t, v_igjen);
        v_igjen := greatest(coalesce(p_grense, 100), 1) - v_n;
    END LOOP;
    PERFORM set_config('disponit.tenant', coalesce(v_kontekst, ''), true);
    PERFORM set_config('disponit.aktor', coalesce(v_aktor, ''), true);
    RETURN v_n;
END $$;
REVOKE ALL ON FUNCTION m21_koe_fristvarsler(INT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 5. Rettighetene. Migrasjonen NAVNGIR IKKE runtime-rollen (057-
--    lærdommen): `deploy/staging/migrer.py` er autoritativ for den
--    konfigurerte rollen. Grantene her er de som gjelder lokalt og i
--    test, der runtime ER hele plattformen, og de faller bort i
--    driftsoppsettet.
-- ------------------------------------------------------------
DO $$
BEGIN
    -- Sveipen: ÉN EXECUTE til varselsenderen. Ingen tabellrettigheter —
    -- rollen har ingen i dag (verifisert), og M-21 gir den ingen.
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_varselsender') THEN
        GRANT EXECUTE ON FUNCTION m21_koe_fristvarsler(INT)
            TO disponit_varselsender;
    END IF;
    -- DØRENE TIL RUNTIME GRANTES IKKE HER (057/089-doktrinen):
    -- `disponit` er bare LOKALNAVNET på web-API-rollen, og
    -- `deploy/staging/migrer.py` er eneste rettighetskilde for den
    -- konfigurerte rollen. En GRANT her ville lagt rettighetsmodellen to
    -- steder, og det ene stedet ville vært usant på enhver installasjon
    -- som kaller rollen noe annet.
    --
    -- REVOKE-en står likevel, og den er ikke pynt (091-formen): en
    -- rettighet som bare slutter å bli gitt er ikke trukket tilbake.
    -- Sveipen er kryss-tenant, og web-API-rollen skal ikke kunne kjøre
    -- den på kommando — samme snitt som 038-reaperen, og samme grunn: en
    -- kompromittert runtime skal se ÉN tenant om gangen.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        REVOKE ALL ON FUNCTION m21_koe_fristvarsler(INT) FROM disponit;
    END IF;
END $$;

RESET ROLE;

-- ------------------------------------------------------------
-- 6. Varselenumene: `art` og `ressurs_type` er lukkede CHECK-er (029) —
--    utvidet ADDITIVT i 041 §15-formen (`regexp_replace` på halen), som
--    er den formen som tåler at flere moduler utvider den samme
--    CHECK-en i vilkårlig rekkefølge.
-- ------------------------------------------------------------
DO $$
DECLARE r RECORD; def TEXT; ny TEXT;
BEGIN
    FOR r IN SELECT conname, pg_get_constraintdef(oid) AS def
               FROM pg_constraint
              WHERE conrelid = 'varsel'::regclass
                AND conname IN ('varsel_art_chk', 'varsel_ressurs_type_chk')
    LOOP
        ny := CASE r.conname WHEN 'varsel_art_chk' THEN 'pliktfrist'
                             ELSE 'plikt' END;
        -- `ressurs_type`-verdien 'plikt' er et delstrengtreff i ingenting
        -- annet i settet, men sammenlikningen gjøres på den KVOTERTE
        -- formen for å være sann også om noen senere legger til
        -- 'pliktbrudd': en delstrengsjekk som blir usann av en nabo er en
        -- migrasjon som kjører to ganger.
        CONTINUE WHEN r.def LIKE '%''' || ny || '''%';
        EXECUTE format('ALTER TABLE varsel DROP CONSTRAINT %I', r.conname);
        def := regexp_replace(r.def, '\]\)\)\)$',
                              format(', %L::text])))', ny));
        IF def NOT LIKE '%''' || ny || '''%' THEN
            RAISE EXCEPTION '096: kunne ikke utvide % — uventet'
                ' definisjonsform: %', r.conname, r.def;
        END IF;
        EXECUTE 'ALTER TABLE varsel ADD CONSTRAINT '
             || quote_ident(r.conname) || ' ' || def;
    END LOOP;
END $$;
