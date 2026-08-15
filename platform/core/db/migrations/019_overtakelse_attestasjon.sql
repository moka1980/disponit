-- ============================================================
-- 019 — Overtakelsesattestasjon (PR-015 §1 og §4)
--
-- Fire øyne ved POSITIV cross-tenant domenetildeling. 014b definerte
-- funksjonene; PR-015 er kallerne, pluss denne ene nye tabellen som
-- fire-øyne-kontrakten krever.
--
-- INGEN endring på 016/017. `verifiser_domenekontroll()` og
-- `avgjor_domeneovertakelse()` KALLES herfra, de endres ikke — det er nettopp
-- grensen 014b holdt, og en `CREATE OR REPLACE` av en 016-kropp her ville
-- flyttet den uten å si det.
--
-- Klarsignalet nummererte denne migrasjonen 018. Det nummeret ble tatt av
-- `018_kanonisk_hostname.sql` (PR-014b-oppfølgingen, merget i main før dette
-- arbeidet startet), så tabellen kommer som 019. Kjøreren er streng på
-- versjonsnummer + checksum, så et gjenbrukt nummer ville vært en hard feil,
-- ikke en kosmetisk detalj.
--
-- To avvik fra DDL-en i klarsignalet, begge tvunget av tabellen den skal
-- referere:
--
--   * `sak_id` er BIGINT, ikke UUID. M-37-saken ER `unntak.id`, og den er
--     `BIGINT GENERATED ALWAYS AS IDENTITY` (003). En UUID-kolonne kunne ikke
--     pekt på saken den er evidens for — attestasjonen ville vært en frittstående
--     rad som lignet på evidens.
--   * `tenant` er lagt til. Klarsignalet krever «RLS + FORCE», og RLS trenger en
--     diskriminator på raden. Tenanten er UTFORDREREN (B) — samme tenant som
--     eier `unntak`-raden saken ligger i, slik at attestasjonen er synlig i
--     nøyaktig den tenantkonteksten saken behandles i.
--
-- `saksrevisjon` er `domenekontroll.autorisasjonsgenerasjon` for utfordrerens
-- rad. Den er allerede monoton og økes av `verifiser_domenekontroll()` på HVER
-- konfliktgren (016 linje 494/530/554) — derfor «økes av en 016-funksjon som
-- allerede finnes; funksjonen kalles, ikke endres». Ingen ny kolonne, ingen ny
-- teller: en ny konflikt gir automatisk en ny revisjon, og attestasjoner avgitt
-- mot den forrige kan aldri telle mot den nye.
-- ============================================================

-- ------------------------------------------------------------
-- 1. overtakelse_attestasjon — én aktør, én stemme per revisjon.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS overtakelse_attestasjon (
    tenant          TEXT   NOT NULL CHECK (length(btrim(tenant)) > 0),
    sak_id          BIGINT NOT NULL,
    saksrevisjon    BIGINT NOT NULL,   -- = domenekontroll.autorisasjonsgenerasjon
    aktor           TEXT   NOT NULL CHECK (length(btrim(aktor)) > 0),
    -- Codex (P1): aktøren MÅ kunne reautoriseres når terskelen slår inn, ikke
    -- bare når stemmen avgis. `aktor` er sesjonsstrengen (`sesjon:<bid>`) og
    -- egner seg til evidens, ikke til oppslag. Prinsipalen bak den lagres
    -- derfor eksplisitt, sammen med DEN authz-versjonen medlemskapet hadde da
    -- stemmen ble avgitt: en rolle som trekkes tilbake bumper versjonen (010),
    -- og en gammel rad kan da ikke lenger telle som en gyldig godkjenning.
    -- Nullbar for radene en tidligere kjøring av denne migrasjonen kan ha
    -- rukket å skrive; slike rader teller IKKE (fail-closed, se opptellingen).
    bruker_id       TEXT   CHECK (bruker_id IS NULL OR length(btrim(bruker_id)) > 0),
    authz_versjon   INT,
    utfall          TEXT   NOT NULL CHECK (utfall IN ('godkjenn','avvis')),
    vinnende_tenant TEXT   NOT NULL,
    hostname        TEXT   NOT NULL,
    avgitt_ts       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Primærnøkkelen ER dobbeltstemme-vernet (§4): samme aktør kan ikke avgi to
    -- stemmer på samme revisjon. Håndhevet av databasen, ikke av UI-et — port 15
    -- sjekker nettopp at det er nøkkelen som avviser, ikke en frontend-sjekk.
    PRIMARY KEY (sak_id, saksrevisjon, aktor)
);

-- Idempotens: tabellen kan finnes fra en tidligere kjøring av migrasjonen, og
-- `CREATE TABLE IF NOT EXISTS` legger da ikke til de to nye kolonnene.
ALTER TABLE overtakelse_attestasjon ADD COLUMN IF NOT EXISTS bruker_id TEXT;
ALTER TABLE overtakelse_attestasjon ADD COLUMN IF NOT EXISTS authz_versjon INT;

-- Opptellingen i §4 går alltid på (sak, revisjon) og teller distinkte aktører.
CREATE INDEX IF NOT EXISTS overtakelse_attestasjon_sak
    ON overtakelse_attestasjon (sak_id, saksrevisjon);

-- ------------------------------------------------------------
-- 2. Append-only — også mot DELETE (§1).
--
-- Foreldede attestasjoner er evidens for at noen attesterte et utfall som ble
-- foreldet. Kan de slettes, mister vi akkurat den sporbarheten fire øyne skal
-- gi: «ingen rad» og «raden ble ryddet bort» ville sett like ut i ettertid.
-- Gjenbruker 016s `domene_append_only()` (rapporterer TG_OP), og statement-
-- vakten fordi TRUNCATE omgår rad-triggere i PostgreSQL.
-- ------------------------------------------------------------
DROP TRIGGER IF EXISTS attestasjon_append_only ON overtakelse_attestasjon;
CREATE TRIGGER attestasjon_append_only
    BEFORE UPDATE OR DELETE ON overtakelse_attestasjon
    FOR EACH ROW EXECUTE FUNCTION domene_append_only();
DROP TRIGGER IF EXISTS attestasjon_ingen_truncate ON overtakelse_attestasjon;
CREATE TRIGGER attestasjon_ingen_truncate
    BEFORE TRUNCATE ON overtakelse_attestasjon
    FOR EACH STATEMENT EXECUTE FUNCTION domene_append_only();

-- ------------------------------------------------------------
-- 3. RLS + FORCE.
-- ------------------------------------------------------------
ALTER TABLE overtakelse_attestasjon ENABLE ROW LEVEL SECURITY;
ALTER TABLE overtakelse_attestasjon FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolasjon ON overtakelse_attestasjon;
CREATE POLICY tenant_isolasjon ON overtakelse_attestasjon
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- ============================================================
-- §2 Herdede funksjoner. Samme modell som 016: SECURITY DEFINER, eid av
-- `disponit_domene_eier` (BYPASSRLS — avgjørelsen er iboende kryss-tenant),
-- search_path = pg_catalog.
-- ============================================================
SET LOCAL ROLE disponit_domene_eier;

-- ------------------------------------------------------------
-- 3.1 avgi_overtakelse_attestasjon — motoren teller, motoren beslutter.
--
-- «Ingen knapp skriver status» (invariant 3). API-et avgir én attestasjon;
-- DENNE funksjonen avgjør om terskelen er nådd og gjør i så fall overgangen
-- ved å kalle `avgjor_domeneovertakelse()`. Terskelen (§4):
--
--     avvis    → ÉN attestasjon    (fail-closed; ingen får autorisasjon)
--     godkjenn → TO DISTINKTE      (etablerer hvilken kunde vi autoriserer)
--
-- Returnerer utfallet som tekst: 'avgjort' | 'venter'.
--
-- Alt skjer under hostname-advisory-låsen — samme lås alle skrivere av dette
-- hostnavnet tar (016) — så opptelling og overgang kan ikke rives fra hverandre
-- av en samtidig overtakelse.
-- ------------------------------------------------------------
DROP FUNCTION IF EXISTS avgi_overtakelse_attestasjon(
    TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT);
CREATE OR REPLACE FUNCTION avgi_overtakelse_attestasjon(
    p_tenant               TEXT,     -- utfordreren (B) — eier saken
    p_sak_id               BIGINT,
    p_hostname             TEXT,
    p_utfall               TEXT,
    p_vinnende_tenant      TEXT,
    p_aktor                TEXT,
    p_forventet_generasjon BIGINT,   -- saksrevisjonen SAKEN ble opprettet for
    p_bruker_id            TEXT)     -- prinsipalen bak `p_aktor` (brukermedlemskap)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_gen BIGINT; v_status TEXT; v_antall INT; v_avvik INT; v_authz INT;
        v_roller TEXT[]; v_bruker TEXT;
BEGIN
    PERFORM public.krev_kanonisk_hostname(p_hostname);
    PERFORM pg_advisory_xact_lock(hashtextextended('domene:' || p_hostname, 0));

    -- Revisjonen leses UNDER låsen. Dette er hele foreldelsesmekanismen: tok C
    -- over mens B-attestasjonen var underveis, har B-raden fått ny generasjon,
    -- og stemmen registreres på den revisjonen som gjelder NÅ — ikke den
    -- kalleren så da den åpnet skjermbildet.
    SELECT autorisasjonsgenerasjon, status INTO v_gen, v_status
      FROM public.domenekontroll
     WHERE tenant = p_tenant AND hostname = p_hostname FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'avgi_overtakelse_attestasjon: ukjent domenekontroll %/%',
            p_tenant, p_hostname USING ERRCODE = 'no_data_found';
    END IF;
    IF v_status IS DISTINCT FROM 'avklaring_kreves' THEN
        RAISE EXCEPTION 'avgi_overtakelse_attestasjon: %/% er % '
            '(krever avklaring_kreves)', p_tenant, p_hostname, v_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Saken er bundet til REVISJONEN den ble opprettet for (Codex): uten
    -- denne sjekken leser funksjonen bare den GJELDENDE generasjonen, og en
    -- sak som overlevde en reapplikasjon (avvist B søker på nytt — ny
    -- generasjon, status tilbake i avklaring_kreves, SAMME sak-id) kunne fått
    -- stemmer beregnet mot den nyere, uavklarte konflikten uten at noen
    -- attestant noensinne så DEN. Avvik her betyr «saken er foreldet», ikke
    -- en basefeil.
    IF v_gen IS DISTINCT FROM p_forventet_generasjon THEN
        RAISE EXCEPTION 'avgi_overtakelse_attestasjon: %/% er på revisjon %, '
            'saken gjelder %', p_tenant, p_hostname, v_gen,
            p_forventet_generasjon USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- `avgjor_domeneovertakelse()` autoriserer ALLTID p_tenant (utfordreren
    -- saken tilhører) ved godkjenn — det finnes ingen gren som autoriserer en
    -- ANNEN tenant. En godkjenn-stemme som navngir noen andre som vinner ville
    -- latt to adjudikatorer attestere ett utfall mens overgangen faktisk
    -- gjennomfører et annet — evidens som motsier avgjørelsen den skal bevise.
    IF p_utfall = 'godkjenn' AND p_vinnende_tenant IS DISTINCT FROM p_tenant THEN
        RAISE EXCEPTION 'avgi_overtakelse_attestasjon: vinnende_tenant % '
            'stemmer ikke med saken (utfordrer %)', p_vinnende_tenant, p_tenant
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Stemmegiveren autoriseres i BASEN, ikke bare i API-laget, og
    -- medlemskapsraden LÅSES ut transaksjonen. Uten låsen kunne en
    -- tilbakekalling committe mellom denne sjekken og overgangen nedenfor.
    -- Rollen er den eneste som bærer `domains:adjudicate` (autorisasjon.py);
    -- basen sjekker rollen fordi scopene er en applikasjonsavledning, og
    -- fire øyne skal ikke hvile på at applikasjonen husket å spørre.
    IF p_bruker_id IS NULL OR length(btrim(p_bruker_id)) = 0 THEN
        RAISE EXCEPTION 'avgi_overtakelse_attestasjon: bruker_id mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Låsen tas gjennom `laas_godkjenner()` (013), ikke med en egen
    -- `FOR SHARE` her. Grunnen er en HARD grense i PostgreSQL, ikke en
    -- stilpreferanse: enhver radlåsklausul — også `FOR SHARE` — krever
    -- UPDATE-privilegium på tabellen I TILLEGG til SELECT. Domenelaget skal
    -- aldri kunne skrive `brukermedlemskap` (den er OIDC-forvaltet, og en
    -- rolle som kunne bumpe `authz_version` kunne gjenopplive en tilbakekalt
    -- stemme ved å flytte versjonen tilbake på plass), så låsen lånes av den
    -- herdede funksjonen PR-013 alt har for nøyaktig dette: SECURITY DEFINER
    -- eid av tabellens egen eier, `FOR UPDATE`, og returnerer rollene +
    -- authz_version den låste raden hadde.
    --
    -- `laas_godkjenner()` kjører som tabelleieren, som IKKE er BYPASSRLS —
    -- tenantkonteksten settes derfor eksplisitt her. Den er transaksjons-
    -- lokal, og p_tenant er samme tenant API-et alt har satt (saken tilhører
    -- utfordreren); test- og driftsveien får dermed samme RLS-vindu.
    PERFORM set_config('disponit.tenant', p_tenant, true);
    SELECT g.roller, g.authz_version INTO v_roller, v_authz
      FROM public.laas_godkjenner(p_tenant, p_bruker_id) g;
    IF NOT FOUND OR v_roller IS NULL
       OR NOT ('domeneadjudikator' = ANY (v_roller)) THEN
        RAISE EXCEPTION 'avgi_overtakelse_attestasjon: % er ikke aktiv '
            'domeneadjudikator for %', p_bruker_id, p_tenant
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- Dobbeltstemme avvises av primærnøkkelen. INGEN ON CONFLICT: en aktør som
    -- prøver å stemme to ganger skal få en hard feil, ikke en stille no-op som
    -- ser ut som en godkjent stemme fra kallerens side.
    INSERT INTO public.overtakelse_attestasjon
        (tenant, sak_id, saksrevisjon, aktor, bruker_id, authz_versjon,
         utfall, vinnende_tenant, hostname)
        VALUES (p_tenant, p_sak_id, v_gen, p_aktor, p_bruker_id, v_authz,
                p_utfall, p_vinnende_tenant, p_hostname);

    -- §4: de to radene må ha IDENTISK (saksrevisjon, utfall, vinnende_tenant,
    -- hostname). Avvik → ingen avgjørelse, ALDRI en sammenslåing. Vi teller
    -- derfor kun stemmer som er enige med denne, og sjekker samtidig om det
    -- finnes uenige stemmer på samme revisjon — de skal blokkere, ikke ignoreres.
    --
    -- Codex (P1): stemmene REAUTORISERES her, ikke bare da de ble avgitt.
    -- Attestasjonstabellen er append-only, så en rad fra en aktør som siden
    -- har mistet `domeneadjudikator` — eller hele medlemskapet — ville ellers
    -- fortsatt telt, og en andre stemme kunne gjennomført overtakelsen med
    -- ÉN faktisk autorisert aktør. Alle medlemskapsradene bak de tellende
    -- stemmene låses derfor først: en tilbakekalling som er underveis må
    -- vente til denne transaksjonen er ferdig, og en som alt har committet er
    -- synlig i tellingen under.
    -- Samme lån av `laas_godkjenner()` som over, én gang per DISTINKT
    -- prinsipal bak stemmene: en `FOR SHARE OF m` i en join her ville krevd
    -- skriverett på fullmaktstabellen.
    FOR v_bruker IN
        SELECT DISTINCT a.bruker_id
          FROM public.overtakelse_attestasjon a
         WHERE a.sak_id = p_sak_id AND a.saksrevisjon = v_gen
           AND a.tenant = p_tenant AND a.bruker_id IS NOT NULL
    LOOP
        PERFORM 1 FROM public.laas_godkjenner(p_tenant, v_bruker);
    END LOOP;
    SELECT count(*) INTO v_antall
      FROM public.overtakelse_attestasjon a
     WHERE a.sak_id = p_sak_id AND a.saksrevisjon = v_gen
       AND a.utfall = p_utfall AND a.vinnende_tenant = p_vinnende_tenant
       AND a.hostname = p_hostname
       -- Fail-closed: en rad uten prinsipal (skrevet før denne kolonnen
       -- fantes) kan ikke reautoriseres og teller derfor ikke.
       AND a.bruker_id IS NOT NULL
       AND EXISTS (SELECT 1 FROM public.brukermedlemskap m
                    WHERE m.tenant = a.tenant AND m.bruker_id = a.bruker_id
                      AND m.aktiv
                      AND 'domeneadjudikator' = ANY (m.roller)
                      -- Samme autorisasjonsversjon som da stemmen ble avgitt.
                      -- Enhver sikkerhetsrelevant endring på medlemskapet
                      -- bumper den (010) og ugyldiggjør allerede sesjonen —
                      -- da skal den heller ikke bære en gammel stemme videre.
                      AND m.authz_version = a.authz_versjon);
    SELECT count(*) INTO v_avvik
      FROM public.overtakelse_attestasjon
     WHERE sak_id = p_sak_id AND saksrevisjon = v_gen
       AND (utfall IS DISTINCT FROM p_utfall
            OR vinnende_tenant IS DISTINCT FROM p_vinnende_tenant);
    IF v_avvik > 0 THEN
        RETURN 'venter';   -- uenighet: ingen avgjørelse, begge rader bevart
    END IF;

    IF p_utfall = 'avvis' THEN
        -- Fail-closed: én stemme holder, ingen får autorisasjon.
        PERFORM public.avgjor_domeneovertakelse(
            p_tenant, p_hostname, v_gen, false, p_aktor);
        PERFORM public.lukk_overtakelsessak(p_tenant, p_sak_id, 'avvist', p_aktor);
        RETURN 'avgjort';
    END IF;

    -- godkjenn: to DISTINKTE aktører. Primærnøkkelen gjør at count(*) her ER
    -- antall distinkte aktører — ingen aktør kan bidra med mer enn én rad.
    IF v_antall >= 2 THEN
        PERFORM public.avgjor_domeneovertakelse(
            p_tenant, p_hostname, v_gen, true, p_aktor);
        PERFORM public.lukk_overtakelsessak(p_tenant, p_sak_id, 'løst', p_aktor);
        RETURN 'avgjort';
    END IF;
    RETURN 'venter';
END $$;

-- ------------------------------------------------------------
-- 3.15 lukk_overtakelsessak — Codex (P1): saken fulgte ALDRI domenevedtaket.
--
-- `unntak` opprettes med status='ny' (opprett_overtakelsessak); uten dette
-- ble raden stående i 'ny' for alltid etter avgjørelsen, og fortsatte å
-- vises som en åpen sak i PR-012-flaten. Statusmaskinen (011) tillater ikke
-- 'ny' -> 'løst'/'avvist' direkte — kun via 'under_behandling' — så
-- overgangen gjøres i to UPDATE-er i SAMME transaksjon som domenevedtaket.
-- `disponit.aktor` settes eksplisitt: `unntak_skriv_historikk()` (003) krever
-- den, og denne funksjonen kjører her under domene_eier, ikke under en
-- sesjon som allerede har satt den.
--
-- Rører KUN en sak som fortsatt er 'ny' (den forventede tilstanden for denne
-- sakstypen — normalarbeideren claimer den aldri, jf. modulens topp-
-- docstring). Er saken alt beveget av en annen vei, lar denne funksjonen den
-- stå urørt i stedet for å risikere en ulovlig overgang eller en feil som
-- ville rullet tilbake selve domenevedtaket.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION lukk_overtakelsessak(
    p_tenant TEXT, p_sak_id BIGINT, p_sluttstatus TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_flyttet INT;
BEGIN
    PERFORM set_config('disponit.aktor', p_aktor, true);
    UPDATE public.unntak SET status = 'under_behandling'
     WHERE tenant = p_tenant AND id = p_sak_id AND status = 'ny';
    GET DIAGNOSTICS v_flyttet = ROW_COUNT;
    -- Kun DENNE overgangens egen 'under_behandling' fullføres til
    -- sluttstatus — en sak som alt sto i 'under_behandling' av en annen vei
    -- (utenfor denne sakstypens forventede bruk) skal ikke bli flyttet av et
    -- kall den ikke var en del av.
    IF v_flyttet = 1 THEN
        UPDATE public.unntak SET status = p_sluttstatus
         WHERE tenant = p_tenant AND id = p_sak_id AND status = 'under_behandling';
    END IF;
END $$;

-- ------------------------------------------------------------
-- 3.2 degrader_forbigatte_utfordrere — A→B→C, §3.
--
-- Klarsignalet §3: «C overtar B-s plass i den åpne saken (B → `tilbakekalt`,
-- C → `avklaring_kreves`, ny hendelse)», og port 20 krever at KUN C står i
-- `avklaring_kreves` etterpå.
--
-- 016 gjør ikke dette selv. Grenen der en tredje tenant møter et hostnavn som
-- alt er under avklaring (016 linje 539–562) setter C i avklaring og flytter
-- `hostname_binding` til C, men lar B-raden stå urørt i `avklaring_kreves`.
-- B kan riktignok ikke bli godkjent — 016s godkjenningsgren gjerder mot
-- `hostname_binding`, så en forbigått utfordrer avvises — men statusen blir
-- aldri terminal, og port 20 måler statusen.
--
-- Å rette det i 016 ville endret en 016-kropp, altså nøyaktig grensen PR-015
-- ikke skal røre. Degraderingen legges derfor her, som et eget kall kalleren
-- gjør RETT ETTER `verifiser_domenekontroll()` returnerte `konflikt:*`.
-- Funksjonen er idempotent og tar samme hostname-lås, så den kan kjøres om
-- igjen uten å endre noe.
--
-- Den rører ALDRI den gjeldende bindingshaveren: kun utfordrere som er
-- forbigått. `hostname_binding` er autoriteten på hvem som er dagens utfordrer
-- (016 §3 B2), og den leses under låsen.
--
-- Codex (P2): SAKEN følger utfordreren ut. Blir B terminalt `tilbakekalt`,
-- kan B aldri fullføre adjudikasjonen — `avgi_overtakelse_attestasjon()`
-- krever `avklaring_kreves`, og den er den ENESTE kalleren av
-- `lukk_overtakelsessak()`. B-saken ville derfor blitt stående `ny` for
-- alltid og fortsatt vist seg som en åpen, handlingskrevende sak i
-- PR-012-flaten — nøyaktig den etterlatte tilstanden degraderingen finnes for
-- å rydde, bare ett lag opp. Saken lukkes derfor her, i SAMME overgang, med
-- `avvist`: ingen fikk autorisasjon, og en `løst` ville påstått at noen gjorde
-- det.
--
-- Saken finnes via IDEMPOTENSNØKKELEN (`<familie>:<hostname>:<generasjon>`,
-- domeneovertakelse.py), lest på generasjonen raden hadde FØR degraderingen
-- bumper den — det er den konflikten saken ble opprettet for. Joinen bærer
-- `kategori`/`handling`/`kilde` slik begge Python-oppslagene gjør, så en
-- fremmed rad i det DELTE idempotensnavnerommet ikke kan matche. Finnes ingen
-- slik sak (eller er den alt beveget videre), gjør vi ingenting — funksjonen
-- skal fortsatt være idempotent og aldri velte en degradering på en sak som
-- ikke står der den forventes.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION degrader_forbigatte_utfordrere(
    p_hostname TEXT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_binding TEXT; v_rad RECORD; v_antall INT := 0; v_sak BIGINT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('domene:' || p_hostname, 0));
    SELECT tenant INTO v_binding FROM public.hostname_binding
     WHERE hostname = p_hostname;
    IF v_binding IS NULL THEN
        RETURN 0;   -- ingen binding ennå: ingen er forbigått
    END IF;
    FOR v_rad IN
        SELECT tenant, autorisasjonsgenerasjon AS generasjon
          FROM public.domenekontroll
         WHERE hostname = p_hostname AND status = 'avklaring_kreves'
           AND tenant IS DISTINCT FROM v_binding
         FOR UPDATE
    LOOP
        UPDATE public.domenekontroll
           SET status = 'tilbakekalt',
               autorisasjonsgenerasjon = autorisasjonsgenerasjon + 1
         WHERE tenant = v_rad.tenant AND hostname = p_hostname;
        INSERT INTO public.domenekontroll_hendelse
            (tenant, hostname, hendelse, fra_status, til_status, grunn, aktor)
            VALUES (v_rad.tenant, p_hostname, 'forbigatt', 'avklaring_kreves',
                    'tilbakekalt', 'forbigatt_av_senere_overtakelse', p_aktor);
        SELECT u.id INTO v_sak
          FROM public.unntak u
          JOIN public.revisjonslogg r
            ON r.tenant = u.tenant AND r.id = u.loggpost_id
         WHERE u.tenant = v_rad.tenant AND u.status = 'ny'
           AND u.kategori = 'domeneovertakelse'
           AND u.handling = 'domene.overtakelse'
           AND r.kilde = 'domeneovertakelse'
           AND r.idempotency_key = 'domeneovertakelse:' || p_hostname || ':'
                                   || v_rad.generasjon::TEXT
         ORDER BY u.id
         LIMIT 1;
        IF FOUND THEN
            PERFORM public.lukk_overtakelsessak(
                v_rad.tenant, v_sak, 'avvist', p_aktor);
        END IF;
        v_antall := v_antall + 1;
    END LOOP;
    RETURN v_antall;
END $$;

-- ------------------------------------------------------------
-- 3.3 antall_autoriserte_adjudikatorer — legibel fail-closed (§4 siste kule).
--
-- «Én autorisert aktør → positiv tildeling er umulig» er riktig fail-closed,
-- men det skal SIES. Funksjonen gir API-et tallet som går inn i feilkoden
-- `krever_to_attestasjoner`, slik at tenanten får vite HVORFOR og ikke bare at
-- det ikke gikk. Stillhet her ville sett ut som en bug.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION antall_avgitte_attestasjoner(
    p_sak_id BIGINT, p_saksrevisjon BIGINT)
RETURNS INT LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    -- Teller de stemmene som fortsatt VILLE talt (samme reautorisering som
    -- terskelen bruker). Et tall som inkluderte tilbakekalte aktører ville
    -- lovet brukeren en fremgang mot to øyne som aldri kan innfris.
    SELECT count(DISTINCT a.aktor)::INT
      FROM public.overtakelse_attestasjon a
     WHERE a.sak_id = p_sak_id AND a.saksrevisjon = p_saksrevisjon
       AND a.bruker_id IS NOT NULL
       AND EXISTS (SELECT 1 FROM public.brukermedlemskap m
                    WHERE m.tenant = a.tenant AND m.bruker_id = a.bruker_id
                      AND m.aktiv AND 'domeneadjudikator' = ANY (m.roller)
                      AND m.authz_version = a.authz_versjon);
$$;

-- ------------------------------------------------------------
-- 3.35 Revalideringsscheduleren (PR-015 §2.1/§2.2).
--
-- Utvalget MÅ ligge i basen, ikke i arbeideren, av to grunner som begge er
-- sikkerhet og ikke bekvemmelighet:
--
--   * `domenekontroll` er RLS+FORCE og tenant-scopet. Scheduleren skal se HELE
--     populasjonen for å kunne planlegge den — en arbeider som leste gjennom
--     RLS ville sett én tenant om gangen og dermed regnet budsjettet på feil
--     nevner. SECURITY DEFINER hos `disponit_domene_eier` (BYPASSRLS) gir den
--     kryss-tenant-lesingen ETT sted, revidérbart, i stedet for å gi
--     arbeiderrollen et bordgrant den kunne brukt til hva som helst.
--   * K er en INVARIANT. Ligger `LIMIT` i SQL sammen med prioriteten, kan ikke
--     en fremtidig endring i Python-orkestreringen bryte den ved et uhell.
--
-- Arbeideren beholder likevel all AUTORITET den ikke har: funksjonen VELGER
-- rader, den revaliderer ingenting og setter ingen status.
--
-- Kø 1 (sikkerhetsnett) returneres UTEN grense — den kappes aldri. Kø 2 og
-- kø 3 deler `p_k` mellom seg, og kø 2 går først.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION revalideringskandidater(
    p_minutt_fra INT, p_minutt_til INT, p_k INT,
    p_nett_timer INT DEFAULT 26, p_alder_timer INT DEFAULT 20)
RETURNS TABLE (tenant TEXT, hostname TEXT, ko INT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_igjen INT;
BEGIN
    RETURN QUERY
    WITH ko1 AS (
        SELECT d.tenant, d.hostname
          FROM public.domenekontroll d
         WHERE d.status = 'verifisert'
           AND (d.siste_vellykkede_revalidering IS NULL
                OR d.siste_vellykkede_revalidering
                   < now() - make_interval(hours => p_nett_timer))
    )
    SELECT k.tenant, k.hostname, 1 FROM ko1 k;
    GET DIAGNOSTICS v_igjen = ROW_COUNT;

    -- Minuttet gjentas her, ikke i Python: utvalget og planen må komme fra
    -- SAMME utledning. `mod()` og ikke `%` — operatoren kolliderer med
    -- parameterplassholdere hos kalleren.
    RETURN QUERY
    WITH kandidat AS (
        SELECT d.tenant, d.hostname,
               mod(get_byte(sha256(convert_to(d.hostname,'UTF8')),0)::BIGINT * 16777216
                 + get_byte(sha256(convert_to(d.hostname,'UTF8')),1)::BIGINT * 65536
                 + get_byte(sha256(convert_to(d.hostname,'UTF8')),2)::BIGINT * 256
                 + get_byte(sha256(convert_to(d.hostname,'UTF8')),3)::BIGINT,
                   1440)::INT AS minutt,
               d.siste_vellykkede_revalidering AS sist
          FROM public.domenekontroll d
         WHERE d.status = 'verifisert'
           AND (d.siste_vellykkede_revalidering IS NULL
                OR d.siste_vellykkede_revalidering
                   < now() - make_interval(hours => p_alder_timer))
           AND (d.siste_vellykkede_revalidering IS NOT NULL
                AND d.siste_vellykkede_revalidering
                    >= now() - make_interval(hours => p_nett_timer))
    ),
    ko2 AS (
        SELECT k.tenant, k.hostname FROM kandidat k
         WHERE (p_minutt_fra <= p_minutt_til
                AND k.minutt >= p_minutt_fra AND k.minutt < p_minutt_til)
            OR (p_minutt_fra > p_minutt_til
                AND (k.minutt >= p_minutt_fra OR k.minutt < p_minutt_til))
         ORDER BY k.sist NULLS FIRST, k.hostname
         LIMIT p_k),
    ko3 AS (
        SELECT k.tenant, k.hostname FROM kandidat k
         WHERE NOT EXISTS (SELECT 1 FROM ko2 WHERE ko2.hostname = k.hostname)
         ORDER BY k.sist NULLS FIRST, k.hostname
         LIMIT greatest(p_k - (SELECT count(*) FROM ko2), 0))
    SELECT q.tenant, q.hostname, q.ko FROM (
        SELECT ko2.tenant, ko2.hostname, 2 AS ko FROM ko2
        UNION ALL
        SELECT ko3.tenant, ko3.hostname, 3 AS ko FROM ko3) q;
END $$;

-- N: nevneren budsjettet regnes av. Egen funksjon fordi den også må se på
-- tvers av tenanter — samme grunn som over.
CREATE OR REPLACE FUNCTION revalideringspopulasjon()
RETURNS INT LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT count(*)::INT FROM public.domenekontroll
     WHERE status IN ('verifisert','avklaring_kreves');
$$;

-- ------------------------------------------------------------
-- 3.35 revalider_domenekontroll(p_tenant, p_hostname, p_aktor, p_txt_verdier)
--
-- Codex (P1): resolverenighet er IKKE kontrollbevis. `enige()` fastslår kun
-- at resolverne returnerte samme TXT-mengde — mister tenanten kontrollen og
-- utfordrings-TXT-en fjernes, men sonen fortsatt har en hvilken som helst
-- stabil TXT-verdi (SPF, DMARC-eier, verifikasjonsstrenger fra andre
-- leverandører), er ALLE resolvere enige, og 016s 3-argumentsform ville
-- friskmeldt `siste_vellykkede_revalidering` for den tidligere kontrolløren.
--
-- Denne overlasten krever at den avtalte TXT-mengden faktisk INNEHOLDER
-- utfordringsbeviset. Sammenligningen ligger her, ikke i arbeideren, av to
-- grunner: `challenge_token_hash` er bevisst holdt utenfor ethvert grant
-- (016 gir den ikke engang som kolonne-grant til egress), og hash-
-- konvensjonen hører hjemme ved siden av kolonnen som lagrer den — ikke
-- duplisert i en arbeider som kunne kommet i utakt med den.
--
-- Fail-closed hele veien: mangler raden bevis (NULL), nektes revalideringen
-- i stedet for å tolke «ingen lagret utfordring» som «ingenting å bevise».
-- Selve oppdateringen delegeres til 016s form, så det finnes fortsatt
-- NØYAKTIG ett sted som skriver `siste_vellykkede_revalidering` og
-- hendelsesloggen — denne funksjonen legger en port foran, ikke en ny vei.
--
-- Codex (P2): beviset LÅSES mens det holdes mot TXT-svaret. Porten og
-- oppdateringen er to setninger, og `utsted_challenge()` bytter
-- `challenge_token_hash` med en `ON CONFLICT DO UPDATE` — altså en ren
-- radlås, ingen advisory-lås å synkronisere mot. Uten `FOR UPDATE` kunne en
-- rotasjon committe mellom lesningen og det nestede kallet: raden ville
-- fortsatt stått `verifisert`, og `siste_vellykkede_revalidering` ville blitt
-- friskmeldt av et TXT-token basen nettopp hadde forlatt. Med låsen venter
-- rotasjonen, og en rotasjon som kom FØR oss re-evalueres av predikatet her
-- (READ COMMITTED leser den oppdaterte raden når låsen slippes), så beviset
-- vi sammenligner mot er alltid det som gjelder ved oppdateringen.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION revalider_domenekontroll(
    p_tenant TEXT, p_hostname TEXT, p_aktor TEXT, p_txt_verdier TEXT[])
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_bevis TEXT; v_treff BOOLEAN;
BEGIN
    SELECT d.challenge_token_hash INTO v_bevis
      FROM public.domenekontroll d
     WHERE d.tenant = p_tenant AND d.hostname = p_hostname
       AND d.status = 'verifisert'
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'revalider_domenekontroll: %/% er ikke verifisert '
            '(ingen revalidering registrert)', p_tenant, p_hostname
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_bevis IS NULL THEN
        RAISE EXCEPTION 'revalider_domenekontroll: %/% har ingen lagret '
            'utfordring — kontroll kan ikke bevises', p_tenant, p_hostname
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- TXT-verdien ER utfordringstokenet; 016 lagrer kun sha256 av det
    -- («klartekst vises ÉN gang, lagres aldri»). Hex sammenlignes
    -- ufølsomt for store/små bokstaver, og verdiene trimmes: en TXT-verdi
    -- kan bære omkringliggende blanktegn fra sonefilen uten at det er en
    -- annen verdi.
    SELECT EXISTS (
        SELECT 1 FROM unnest(coalesce(p_txt_verdier, ARRAY[]::TEXT[])) AS v
         WHERE v IS NOT NULL
           AND lower(encode(sha256(convert_to(btrim(v), 'UTF8')), 'hex'))
               = lower(btrim(v_bevis)))
      INTO v_treff;
    IF NOT v_treff THEN
        RAISE EXCEPTION 'revalider_domenekontroll: %/% — utfordringsbeviset '
            'finnes ikke i TXT-svaret (kontroll ikke bevist)',
            p_tenant, p_hostname
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM public.revalider_domenekontroll(p_tenant, p_hostname, p_aktor);
END $$;

-- ------------------------------------------------------------
-- 3.4 rydd_staged_artefakter(p_grense) — samme positive regel, med bunn i én
--     transaksjon (§6).
--
-- Klarsignalet krever «batchgrense 500 per kjøring, så opphopning ikke låser
-- tabellen i én transaksjon». 016s `rydd_staged_artefakter()` har INGEN grense:
-- den forkaster alt som kvalifiserer i ett jafs. Å legge grensen inn i den
-- kroppen ville vært en 016-endring; å la timeren telle til 500 selv ville vært
-- «logikk oppå den positive regelen», som §6 forbyr.
--
-- Derfor en OVERLAST med samme predikat og et `LIMIT`. Den 0-argumentsformen
-- 016 eier står helt urørt, og fortsetter å være definisjonen av regelen —
-- denne er den samme regelen med en bunn.
--
-- Predikatet er bevisst kopiert ordrett fra 016 (staged · eldre enn 24 t · uten
-- refererende oppdrag med levende evidensfrist). Endres regelen i 016 må den
-- endres her også; det er prisen for å ikke røre 016-kroppen, og porten som
-- fanger et avvik er at begge former må gi samme utvalg på samme populasjon.
--
-- `karantene` og `bevart` nås ikke av predikatet i det hele tatt — de er ikke
-- `staged`. Karantenesatt evidens telles og rapporteres av timeren, aldri ryddes.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION rydd_staged_artefakter(p_grense INT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_antall INT;
BEGIN
    IF p_grense IS NULL OR p_grense <= 0 THEN
        RAISE EXCEPTION 'rydd_staged_artefakter: grense må være > 0 (fikk %)',
            coalesce(p_grense::TEXT, '(null)')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    WITH kandidat AS (
        SELECT a.artefakt_id FROM public.artefakt a
         WHERE a.tilstand = 'staged'
           AND a.opprettet < now() - interval '24 hours'
           AND NOT EXISTS (SELECT 1 FROM public.oppdrag o
                            WHERE o.tenant = a.tenant AND o.id = a.oppdrag_id
                              AND o.evidensfrist > now())
         ORDER BY a.opprettet
         LIMIT p_grense),
    forkastet AS (
        UPDATE public.artefakt a
           SET tilstand = 'forkastet', ciphertext = NULL, nonce = NULL
          FROM kandidat k
         WHERE a.artefakt_id = k.artefakt_id
           -- Codex (P1): predikatet GJENTAS på selve UPDATE-en, ikke bare i
           -- CTE-en. CTE-en leser transaksjonens snapshot; blir artefaktet
           -- promotert, bevart eller karantenesatt ETTER den lesningen men FØR
           -- denne raden låses, ville et `WHERE artefakt_id = ...` alene
           -- overskrevet den nye, terminale tilstanden med `forkastet` og
           -- nullet chiffertekst — altså slettet evidens som nettopp var
           -- akseptert. I READ COMMITTED re-evalueres WHERE-en mot den
           -- OPPDATERTE raden når UPDATE-en slipper opp fra radlåsen, så den
           -- gjentatte betingelsen er nøyaktig det som gjør den samtidige
           -- overgangen synlig her. Raden faller da bare ut av batchen; neste
           -- kjøring plukker den om den fortsatt kvalifiserer.
           AND a.tilstand = 'staged'
           AND a.opprettet < now() - interval '24 hours'
           AND NOT EXISTS (SELECT 1 FROM public.oppdrag o
                            WHERE o.tenant = a.tenant AND o.id = a.oppdrag_id
                              AND o.evidensfrist > now())
        RETURNING 1)
    SELECT count(*) INTO v_antall FROM forkastet;
    RETURN v_antall;
END $$;

-- Karantenesatt evidens: telles og rapporteres av ryddetimeren, aldri ryddet.
-- En stille ryddejobb er en voksende disk; en ryddejobb som ikke sier hvor mye
-- den BEVARER, skjuler at disken vokser av noe den aldri får røre.
CREATE OR REPLACE FUNCTION antall_karantenesatte()
RETURNS INT LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT count(*)::INT FROM public.artefakt WHERE tilstand = 'karantene';
$$;

RESET ROLE;

-- ============================================================
-- GRANT-modell (default-deny), samme mønster som 016.
-- ============================================================
GRANT SELECT, INSERT ON overtakelse_attestasjon TO disponit_domene_eier;
-- Codex (P1): saken skal LUKKES atomisk med domenevedtaket
-- (lukk_overtakelsessak, kalt fra avgi_overtakelse_attestasjon). Trigger-
-- kjeden (unntak_kolonnelaas + unntak_skriv_historikk, 003/011) kjører under
-- SAMME rolle som gjør UPDATE-en — domene_eier trenger derfor UPDATE på
-- unntak og INSERT på unntak_historikk, ikke bare EXECUTE på funksjonen.
--
-- Codex (P1): SELECT hører med til den UPDATE-en, den er ikke en ekstra
-- lesetilgang. `lukk_overtakelsessak()` gjerder begge overgangene på
-- `WHERE tenant = ... AND id = ... AND status = ...`, og PostgreSQL krever
-- SELECT på hver kolonne en kommando LESER — også når lesningen kun er et
-- UPDATE-predikat. Uten den feilet første avvisning (eller andre
-- godkjenning) med `permission denied for table unntak` inne i det nestede
-- kallet, og siden lukkingen er ATOMISK med domenevedtaket rullet hele
-- avgjørelsen tilbake: fire øyne var enige, og ingenting skjedde.
-- Nøyaktig samme bunt som M-37-eieren har (005: SELECT, UPDATE på unntak +
-- INSERT på unntak_historikk); identitetssekvensen bak `unntak_historikk.id`
-- trenger ingen egen grant — en `GENERATED ALWAYS AS IDENTITY`-kolonne
-- henter neste verdi internt, ikke gjennom `nextval()`s USAGE-sjekk, og
-- M-37-eieren har da heller aldri hatt et sekvensgrant.
GRANT SELECT, UPDATE ON unntak TO disponit_domene_eier;
GRANT INSERT ON unntak_historikk TO disponit_domene_eier;
-- Codex (P2): `degrader_forbigatte_utfordrere()` finner den forbigåtte
-- utfordrerens sak gjennom idempotensnøkkelen, som ligger på loggposten.
-- KUN SELECT: domenelaget skal kunne FINNE saken, aldri skrive revisjonen.
GRANT SELECT ON revisjonslogg TO disponit_domene_eier;
-- Codex (P1): reautoriseringen av de tellende stemmene leser `brukermedlemskap`
-- inne i `avgi_overtakelse_attestasjon` og i `antall_avgitte_attestasjoner`.
-- Kun SELECT — domenelaget skal kunne bekrefte et medlemskap, aldri endre det.
--
-- LÅSEN kan derfor ikke tas her: PostgreSQL krever UPDATE-privilegium for
-- ENHVER radlåsklausul, også `FOR SHARE`. Den lånes av `laas_godkjenner()`
-- (013), som er SECURITY DEFINER eid av tabellens egen eier og finnes for
-- nøyaktig dette — PR-013 møtte samme grense da runtime skulle reautorisere
-- policygodkjennere. EXECUTE, ikke DML: domenelaget får låse en
-- medlemskapsrad, aldri skrive den.
GRANT SELECT ON brukermedlemskap TO disponit_domene_eier;
GRANT EXECUTE ON FUNCTION laas_godkjenner(TEXT, TEXT) TO disponit_domene_eier;

REVOKE ALL ON FUNCTION avgi_overtakelse_attestasjon(TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION degrader_forbigatte_utfordrere(TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION antall_avgitte_attestasjoner(BIGINT, BIGINT) FROM PUBLIC;
REVOKE ALL ON FUNCTION lukk_overtakelsessak(TEXT, BIGINT, TEXT, TEXT) FROM PUBLIC;
-- Codex (P1): staging kjører API-et med DATABASE_URL som `disponit` — den
-- NOLOGIN admin-bunten (`disponit_domains_admin`) er kun tilgjengelig for
-- migrator (WITH INHERIT FALSE). Uten en direkte grant til runtime-rollen
-- feiler ENHVER produksjonsforespørsel mot endepunktet med
-- InsufficientPrivilege. Samme mønster som 016 (lagre_artefakt_staged m.fl.
-- granted direkte til `disponit`) — funksjonsspesifikk, ikke bunten.
GRANT EXECUTE ON FUNCTION avgi_overtakelse_attestasjon(TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION antall_avgitte_attestasjoner(BIGINT, BIGINT) TO disponit;
-- `opprett_overtakelsessak()` (domeneovertakelse.py) kaller nå
-- `degrader_forbigatte_utfordrere` som en del av å håndtere konfliktsignalet
-- — samme runtime-rolle som skriver `unntak`/`revisjonslogg` trenger derfor
-- EXECUTE på den også. Migrator kaller samme funksjon direkte (uten
-- SET ROLE) i test-oppsettet, som `opprett_overtakelsessak` selv alltid har
-- gjort for tabellskrivingen — en SET ROLE der ville mistet
-- tabelleierskapet INSERT-ene under trenger.
GRANT EXECUTE ON FUNCTION degrader_forbigatte_utfordrere(TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION degrader_forbigatte_utfordrere(TEXT, TEXT) TO disponit_migrator;
GRANT EXECUTE ON FUNCTION avgi_overtakelse_attestasjon(TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT, TEXT) TO disponit_domains_admin;
GRANT EXECUTE ON FUNCTION degrader_forbigatte_utfordrere(TEXT, TEXT) TO disponit_domains_admin;
GRANT EXECUTE ON FUNCTION antall_avgitte_attestasjoner(BIGINT, BIGINT) TO disponit_domains_admin;
REVOKE ALL ON FUNCTION revalideringskandidater(INT, INT, INT, INT, INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION revalideringspopulasjon() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION revalideringskandidater(INT, INT, INT, INT, INT) TO disponit_domains_admin;
GRANT EXECUTE ON FUNCTION revalideringspopulasjon() TO disponit_domains_admin;
REVOKE ALL ON FUNCTION rydd_staged_artefakter(INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION antall_karantenesatte() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rydd_staged_artefakter(INT) TO disponit_domains_admin;
GRANT EXECUTE ON FUNCTION antall_karantenesatte() TO disponit_domains_admin;
REVOKE ALL ON FUNCTION revalider_domenekontroll(TEXT, TEXT, TEXT, TEXT[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION revalider_domenekontroll(TEXT, TEXT, TEXT, TEXT[]) TO disponit_domains_admin;

-- ------------------------------------------------------------
-- Codex (P1): `disponit_domains_admin` bærer OGSÅ direkte EXECUTE på
-- `avgjor_domeneovertakelse` (016, urørt) — en holder av DEN credentialen
-- kunne kalt adjudikasjonen selv, med p_godkjent=true og ÉN aktør, og dermed
-- omgått fire øyne helt. Driftstimerne (revalidering + rydding) autentiserer
-- derfor som `disponit_domener`, en NY, minst-privilegert rolle som får
-- EXECUTE på nøyaktig de funksjonene arbeiderne kaller — aldri
-- `avgjor_domeneovertakelse`, aldri `avgi_overtakelse_attestasjon`, aldri
-- `verifiser_domenekontroll`. `revalider_domenekontroll` er 016s egen
-- (urørt kropp); denne GRANT-en er en ny, uavhengig DCL-setning, ikke en
-- endring av 016-filen.
-- ------------------------------------------------------------
-- Betinget: rollen opprettes av deploy/staging/oppsett-postgresql.sh (roller
-- er klyngeobjekter — «aldri i en migrasjon», se samme fils §-kommentar).
-- CI-arbeidsflyten (.github/workflows/ci.yml) speiler normalt rolleoppsettet,
-- men den filen kan ikke endres herfra (GitHub App-tillatelser); en bar GRANT
-- ville derfor feilet migrasjonen i CI med «role does not exist». Betinget
-- på EXISTS er ikke en svekkelse av kravet — staging får grantene, og en
-- fremtidig CI-oppdatering som legger til rollen får dem også, uten en ny
-- migrasjon.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_domener') THEN
        GRANT EXECUTE ON FUNCTION revalideringskandidater(INT, INT, INT, INT, INT) TO disponit_domener;
        GRANT EXECUTE ON FUNCTION revalideringspopulasjon() TO disponit_domener;
        GRANT EXECUTE ON FUNCTION rydd_staged_artefakter(INT) TO disponit_domener;
        GRANT EXECUTE ON FUNCTION antall_karantenesatte() TO disponit_domener;
        -- Bevisformen, ALDRI 016s 3-argumentsform: en arbeider som kunne
        -- kalt den siste ville kunnet friskmelde en domenekontroll uten å
        -- legge fram utfordringsbeviset i det hele tatt, som er nøyaktig
        -- den veien overlasten over stenger.
        GRANT EXECUTE ON FUNCTION revalider_domenekontroll(TEXT, TEXT, TEXT, TEXT[]) TO disponit_domener;
    END IF;
END $$;

-- Skulle en tidligere kjøring av denne migrasjonen ha gitt arbeiderrollen
-- 3-argumentsformen (bevisløs revalidering), trekkes den tilbake her —
-- migrasjonen er idempotent, og en grant som ble feil skal ikke overleve
-- fordi den ble gitt før porten fantes.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_domener') THEN
        REVOKE EXECUTE ON FUNCTION revalider_domenekontroll(TEXT, TEXT, TEXT) FROM disponit_domener;
    END IF;
END $$;
