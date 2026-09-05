-- =====================================================================
-- M-33 (130): FIRE RETTELSER I DØRENE, FRA CODERABBITS GJENNOMGANG
-- AV #394.
-- =====================================================================
--
-- FIRE HER, fordi §4 dekker to dører med samme rot. Gjennomgangen ga
-- åtte funn i alt; de øvrige ligger UTENFOR migrasjonen og er derfor
-- ikke talt her:
--
--   * `disponit-prognosesveip.timer` — spredningen kortet fra 30 til
--     15 minutter, slik at sveipen er ferdig før statussveipen kan
--     starte. En timer er ikke en migrasjon.
--   * `docs/KLYNGE8-FUNDAMENT.md` — et avsnitt som fortsatt sa at
--     flyttingen til 12:05 ville vært for tidlig.
--   * To bagateller i ANDRE modulers tekster, og én systemisk sak i
--     `opp.sh` som gjelder alle ti credential-blokkene. Listet i
--     PR-en, ikke endret her.
--
-- Migrasjoner er forward-only, så rettelsen tar 131 og skyver M-36 til
-- 132. FJERDE GANG I DENNE KJEDEN at en etterfunnet feil flytter et
-- modulnummer (125/126, 129, og nå denne). Mønsteret står i
-- KLYNGE8-FUNDAMENT: nummeret er ikke en plan, det er en kø — og en
-- feil i det som ALT er merget går foran en modul som ennå ikke finnes.
--
-- ---------------------------------------------------------------------
-- 1. SNITTET DELTE PÅ UKER TENANTEN IKKE HAR LEVD.  (den alvorligste)
--
-- `m33_lag_prognose` regnet `avg(minutter)` over ALLE `grunnlag_uker`
-- blokker. En blokk uten rader bidro med 0 — også blokker som ligger
-- FØR tenantens aller første timeregistrering.
--
-- En tenant med to ukers historikk og `grunnlag_uker = 8` fikk derfor
-- et forventet nivå på omtrent en fjerdedel av det virkelige, mens
-- `grunnlag_antall_uker` sa 2. RADEN OG DIVISOREN VAR IKKE ENIGE.
--
-- OG KONSEKVENSEN RAMMER MODULENS EGEN HOVEDINVARIANT: et snitt som
-- ligger fire ganger for lavt taper for basislinjen HVER målte uke, og
-- sveipen reiser `slaar_ikke_naiv_baseline` mot en modell som aldri
-- fikk sitt eget vindu. Funnet som skulle si «modellen er ikke god
-- nok» ville i stedet sagt «tenanten er ny».
--
-- DETTE ER SAMME SKILLE FILA SELV TREKKER FOR TOM HISTORIKK, brukt
-- motsatt vei: NULL ARBEID ER IKKE DET SAMME SOM INGEN DATA. En uke
-- der ingen jobbet er en observasjon; en uke før tenanten fantes er
-- ikke det.
--
-- RETTELSEN: bare blokker som OVERLAPPER historikken teller. En blokk
-- overlapper når slutten ligger etter den første registreringen. Den
-- eldste blokken kan være delvis dekket, og den regnes med — å kaste
-- den ville kastet ekte arbeid.
--
-- OG MINST TO DEKKEDE BLOKKER KREVES. Med én blokk ER snittet forrige
-- uke, og modellen er sin egen basislinje: den kan ikke tape, og
-- `slaar_ikke_naiv_baseline` blir et funn ingen kan reise. Det er
-- samme dom som `grunnlag_uker >= 2` i `prognosekrav`, håndhevet der
-- den faktisk kan brytes — på DATAENE, ikke på grensen.
--
-- ---------------------------------------------------------------------
-- 2. ET AVRUNDET SNITT KUNNE GI ET INTERVALL MED BREDDE NULL.
--
-- `v_punkt := round(v_snitt)`, og minstebredden var regnet av
-- `v_punkt`. Et lite totalarbeid fordelt på flere blokker runder til
-- 0 — og da ble alle tre leddene i `greatest(...)` null, og raden
-- skrevet med `nedre = forventet = ovre = 0`.
--
-- CHECKen `nedre <= forventet <= ovre` slipper det gjennom. Det er et
-- PUNKT SOM PÅSTÅR Å VÆRE ET INTERVALL — nøyaktig det kommentaren rett
-- over de linjene sa skulle være umulig.
--
-- RETTELSEN: minstebredden er ALLTID minst 1. Et intervall med bredde
-- null finnes ikke, uansett hvor lite tallet er.
--
-- ---------------------------------------------------------------------
-- 3. `m33_datakvalitet` BANDT IKKE TENANTEN TIL KONTEKSTEN.
--
-- Den var den ENESTE M-33-døra uten `krev_tenantkontekst`. Ingen data
-- lekket — radvakten på M-3s registre ser til det — men det er verre
-- enn en lekkasje ville vært SYNLIG: med en tenant som ikke matcher
-- konteksten ga funksjonen null rader, og svarte `ukjent` med 0 funn.
--
-- ALTSÅ NØYAKTIG DEN VERDIEN DENNE MODULEN BEHANDLER SOM DEN FARLIGE,
-- avgitt av feil grunn. «Ingen har sett etter» skal bety at ingen har
-- sett etter — ikke at kalleren spurte om feil tenant.
--
-- ---------------------------------------------------------------------
-- 4. EN GJENTATT MÅLING GA 400 PÅ EN SKRIVING SOM HADDE LYKTES.
--
-- API-et KREVER `Idempotency-Key`, og kastet den. `m33_registrer_
-- maaling` fanget `unique_violation` og reiste den på nytt, og API-et
-- oversatte det til 400.
--
-- Veien dit krever ingen feilbruk: en klient sender målingen, svaret
-- går tapt, klienten prøver igjen med samme nøkkel — og får vite at
-- det feilet. Modulens egen topptekst sier at `POST /maaling` er den
-- ENESTE veien til å lukke `prognose_uten_maaling`, så kalleren kan
-- ikke engang se om funnet ble lukket.
--
-- `m33_avvikle_modell` har samme rot: den kastet nøkkelen, og
-- `m33_modellvakt` avviser en ny avvikling — så et gjenspill med
-- IDENTISK `gyldig_til` ga 400 også der.
--
-- RETTELSEN: et EKTE gjenspill svarer med den lagrede raden. Et
-- gjenspill som sier noe ANNET er fortsatt en feil — to ulike
-- forespørsler som deler nøkkel er ikke én forespørsel.
--
-- 119s lærdom, tredje gang i denne modulen: den satt i kravdøra og i
-- prognosedøra, og manglet i de to siste.
-- =====================================================================

SET LOCAL ROLE disponit_prognose_eier;

-- ---------------------------------------------------------------------
-- 3. DATAKVALITETSDØRA BINDER TENANTEN.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION m33_datakvalitet(p_tenant TEXT)
RETURNS TABLE (flagg TEXT, antall INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_funn INT;
    v_kjort BOOLEAN;
BEGIN
    -- LAGT TIL I 131. Uten denne svarte funksjonen `ukjent` med 0 funn
    -- når kalleren oppga en annen tenant enn konteksten — et STILLE
    -- galt svar, i nettopp den verdien modulen behandler som den
    -- farlige.
    PERFORM public.krev_tenantkontekst(p_tenant, 'm33_datakvalitet');

    SELECT count(*) INTO v_funn
      FROM public.kvalitetsfunn WHERE tenant = p_tenant;

    -- HAR M-3 I DET HELE TATT KJØRT? `kvalitetskjoring` har ingen
    -- `tenant`-kolonne — den er husets kjøringshode, ikke tenantens —
    -- så spørsmålet er om PROFILEN finnes for denne tenanten.
    SELECT EXISTS (SELECT 1 FROM public.kvalitetsprofil
                    WHERE tenant = p_tenant) INTO v_kjort;

    IF v_funn > 0 THEN
        RETURN QUERY SELECT 'flagget'::TEXT, v_funn;
    ELSIF v_kjort THEN
        RETURN QUERY SELECT 'ren'::TEXT, 0;
    ELSE
        RETURN QUERY SELECT 'ukjent'::TEXT, 0;
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 1 OG 2: PROGNOSEDØRA.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION m33_lag_prognose(
    p_tenant TEXT, p_prognose_id UUID, p_modell_id UUID, p_aktor TEXT)
RETURNS TABLE (prognose_id UUID, horisont_uker INT,
               grunnlag_antall_uker INT, datakvalitet TEXT,
               baseline_minutter BIGINT, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_krav public.prognosekrav%ROWTYPE;
    v_modell public.prognosemodell%ROWTYPE;
    v_gml public.bemanningsprognose%ROWTYPE;
    v_dato DATE := current_date;
    v_flagg TEXT;
    v_antall INT;
    v_snitt NUMERIC;
    v_spredning NUMERIC;
    v_baseline BIGINT;
    v_blokker INT;
    v_siste DATE;
    -- 131: tenantens ALLER FØRSTE registrering. Blokker som ligger
    -- helt før den, er ikke observasjoner — de er uker tenanten ikke
    -- har levd, og et snitt som deler på dem ligger for lavt.
    v_forste DATE;
    v_punkt BIGINT;
    v_avvik BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm33_lag_prognose');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm33_lag_prognose: en prognose bærer navnet'
            ' til den som ba om den'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- GJENSPILL FØRST (SP-2, 127/128s form; CodeRabbit fant at det
    -- MANGLET her). API-et utleder `prognose_id` av
    -- Idempotency-Key-en, så et gjenspill treffer samme id — og uten
    -- denne grenen ville den truffet primærnøkkelen og gitt 400 på et
    -- helt lovlig gjentatt kall.
    --
    -- INGEN `FOR UPDATE` HER, OG DET ER IKKE EN FORGLEMMELSE:
    -- `FOR UPDATE` KREVER UPDATE-RETT, og §RETTIGHETER har REVOKEd
    -- den fra modulrollen nettopp fordi tabellen er append-only. En
    -- lås ville feilet med «permission denied» på en dør som gjør alt
    -- riktig — en lærdom huset har betalt for før (128).
    --
    -- Låsen trengs heller ikke: raden kan ALDRI endres, så det finnes
    -- ingenting å beskytte mot. Kappløpet mellom to samtidige kall
    -- med samme id fanges av primærnøkkelen.
    SELECT * INTO v_gml FROM public.bemanningsprognose
     WHERE tenant = p_tenant AND bemanningsprognose.prognose_id
                                 = p_prognose_id;
    IF FOUND THEN
        -- SAMME NØKKEL, ANNEN MODELL, ER IKKE ET GJENSPILL. Det er to
        -- ulike forespørsler som deler nøkkel, og å svare med den
        -- første ville skjult at den andre aldri ble utført.
        IF v_gml.modell_id IS DISTINCT FROM p_modell_id THEN
            RAISE EXCEPTION 'm33_lag_prognose: prognose % finnes mot'
                ' en annen modell', p_prognose_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN QUERY SELECT
            v_gml.prognose_id, v_gml.horisont_uker,
            v_gml.grunnlag_antall_uker, v_gml.datakvalitet,
            (SELECT max(b.baseline_minutter)
               FROM public.bemanningsbane b
              WHERE b.tenant = p_tenant
                AND b.prognose_id = p_prognose_id),
            false;
        RETURN;
    END IF;

    SELECT * INTO v_krav FROM public.prognosekrav
     WHERE tenant = p_tenant;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm33_lag_prognose: tenanten har ingen'
            ' registrerte prognosegrenser'
            USING ERRCODE = 'no_data_found';
    END IF;

    SELECT * INTO v_modell FROM public.prognosemodell
     WHERE tenant = p_tenant AND modell_id = p_modell_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm33_lag_prognose: modellen finnes ikke'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT public.m33_modell_gyldig(v_modell.gyldig_fra,
                                    v_modell.gyldig_til) THEN
        RAISE EXCEPTION 'm33_lag_prognose: modell % gjelder ikke i'
            ' dag — en prognose laget av en avviklet modell bærer en'
            ' autoritet ingen har gitt den', v_modell.versjon
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- OBSERVASJONSBLOKKENE. Blokk k dekker
    -- [v_dato - 7k, v_dato - 7(k-1)) — halvåpent, som resten av huset.
    --
    -- EN CTE OG IKKE EN TEMP-TABELL: en `SECURITY DEFINER`-dør som
    -- lager temp-tabeller krever `TEMP` på basen av den som kaller,
    -- og da hadde SP-7-skillet lekket ut i en rettighet ingen hadde
    -- bedt om. Alt som trengs, hentes i ett svep.
    SELECT min(t.dato), max(t.dato) INTO v_forste, v_siste
      FROM public.timeregistrering t
     WHERE t.tenant = p_tenant AND t.dato < v_dato;

    IF v_forste IS NULL THEN
        RAISE EXCEPTION 'm33_lag_prognose: ingen timeregistrering for'
            ' tenanten — en prognose på null observasjoner ville'
            ' påstått at ingen jobber, ikke at vi ikke vet'
            USING ERRCODE = 'no_data_found';
    END IF;

    -- OBSERVASJONSBLOKKENE, BEGRENSET TIL DEM SOM OVERLAPPER
    -- HISTORIKKEN (131).
    --
    -- Blokk k dekker [v_dato - 7k, v_dato - 7(k-1)) — halvåpent, som
    -- resten av huset. Den OVERLAPPER historikken når slutten ligger
    -- etter den første registreringen.
    --
    -- Den eldste blokken kan være DELVIS dekket, og den regnes med:
    -- å kaste den ville kastet ekte arbeid. Prisen er at det eldste
    -- vinduet kan ligge litt lavt — en kjent og liten unøyaktighet i
    -- randen, til forskjell fra den systematiske underdrivelsen som
    -- fulgte av å dele på uker tenanten ikke har levd.
    WITH blokk AS (
        SELECT b.k, coalesce(sum(t.minutter), 0) AS minutter
          FROM generate_series(1, v_krav.grunnlag_uker) AS b(k)
          LEFT JOIN public.timeregistrering t
                 ON t.tenant = p_tenant
                AND t.dato >= v_dato - (b.k * 7)
                AND t.dato <  v_dato - ((b.k - 1) * 7)
         WHERE v_dato - ((b.k - 1) * 7) > v_forste
         GROUP BY b.k)
    -- `v_blokker` TELLER NÅ BLOKKENE SNITTET FAKTISK BLE REGNET OVER.
    -- Før telte den blokkene med arbeid, mens snittet delte på alle —
    -- raden og divisoren var ikke enige, og det var hele feilen.
    SELECT count(*),
           avg(minutter),
           coalesce(stddev_pop(minutter), 0),
           max(minutter) FILTER (WHERE k = 1)
      INTO v_blokker, v_snitt, v_spredning, v_baseline
      FROM blokk;

    -- MINST TO DEKKEDE BLOKKER. Med én ER snittet forrige uke, og
    -- modellen er sin egen basislinje: den kan ikke tape, og
    -- `slaar_ikke_naiv_baseline` blir et funn ingen kan reise. Samme
    -- dom som `grunnlag_uker >= 2`, håndhevet på DATAENE i stedet for
    -- på grensen.
    IF v_blokker < 2 THEN
        RAISE EXCEPTION 'm33_lag_prognose: bare % hel(e) uke(r) med'
            ' historikk — en modell med færre enn to observerte uker'
            ' ER sin egen basislinje og kan ikke måles mot den',
            v_blokker
            USING ERRCODE = 'no_data_found';
    END IF;

    v_punkt := round(v_snitt)::BIGINT;

    -- INTERVALLETS MINSTEBREDDE. Med én observert blokk, eller med
    -- blokker som tilfeldigvis er like, ville spredningen vært null —
    -- og et intervall med bredde null er et PUNKT som later som det
    -- er et intervall. Det er nøyaktig løgnen `prognose_uten_intervall`
    -- finnes for å hindre, og en kolonne som er `NOT NULL` fanger den
    -- ikke: null er en gyldig verdi.
    -- INTERVALLET ER ALDRI DEGENERERT (131).
    --
    -- Før var minstebredden regnet av `v_punkt`, som er det AVRUNDEDE
    -- snittet. Et lite totalarbeid fordelt på flere blokker runder til
    -- 0, og da ble alle tre leddene null: raden fikk
    -- `nedre = forventet = ovre = 0`. CHECKen slipper det gjennom,
    -- fordi 0 <= 0 <= 0.
    --
    -- Det er et PUNKT SOM PÅSTÅR Å VÆRE ET INTERVALL — nøyaktig
    -- løgnen `prognose_uten_intervall` finnes for å hindre, og
    -- nøyaktig det kommentaren her sa var umulig.
    --
    -- `1` STÅR UBETINGET NÅ. Et bånd med bredde null finnes ikke,
    -- uansett hvor lite tallet er: å si «null minutter, med
    -- sikkerhet» er en påstand modulen ikke har lov til å avgi.
    v_avvik := greatest(ceil(v_spredning)::BIGINT,
                        ceil(v_snitt * 0.10)::BIGINT,
                        1::BIGINT);

    SELECT flagg, antall INTO v_flagg, v_antall
      FROM public.m33_datakvalitet(p_tenant);

    INSERT INTO public.bemanningsprognose
        (tenant, prognose_id, laget_dato, horisont_uker, modell_id,
         modellversjon, baselinje, grunnlag_uker, grunnlag_siste_dato,
         grunnlag_antall_uker, datakvalitet, datakvalitet_antall,
         gjelder_til, laget_av)
    VALUES (p_tenant, p_prognose_id, v_dato, v_krav.horisont_uker,
            p_modell_id, v_modell.versjon, v_modell.baselinje,
            v_krav.grunnlag_uker, v_siste, v_blokker, v_flagg,
            v_antall, v_dato + (v_krav.horisont_uker * 7), p_aktor);

    INSERT INTO public.bemanningsbane
        (tenant, prognose_id, uke_nr, ukeslutt, forventet_minutter,
         nedre_minutter, ovre_minutter, baseline_minutter)
    SELECT p_tenant, p_prognose_id, u.n,
           -- `til - 1`: ukens SISTE dag. 129s lærdom.
           (v_dato + (u.n * 7)) - 1,
           v_punkt,
           greatest(v_punkt - v_avvik, 0),
           v_punkt + v_avvik,
           v_baseline
      FROM generate_series(1, v_krav.horisont_uker) AS u(n);

    PERFORM public.m33_evidens(p_tenant, p_prognose_id,
        'lag_prognose', p_aktor,
        jsonb_build_object('modellversjon', v_modell.versjon,
                           'grunnlag_uker', v_blokker,
                           'datakvalitet', v_flagg));

    RETURN QUERY SELECT p_prognose_id, v_krav.horisont_uker,
                        v_blokker, v_flagg, v_baseline, true;
END $$;

-- ---------------------------------------------------------------------
-- 4: MÅLEDØRA OG AVVIKLINGSDØRA SVARER PÅ ET GJENSPILL.
--
-- `DROP` OG IKKE `CREATE OR REPLACE`, og det er ikke et valg:
-- returtypen får et felt til (`ny`), og PostgreSQL nekter å bytte
-- radtypen på en eksisterende funksjon. Rettighetene må derfor gis på
-- nytt under — en `DROP` tar dem med seg, og en dør uten EXECUTE er en
-- dør ingen kommer gjennom.
-- ---------------------------------------------------------------------
DROP FUNCTION IF EXISTS m33_registrer_maaling(TEXT, UUID, INT, BIGINT,
                                              TEXT);
CREATE FUNCTION m33_registrer_maaling(
    p_tenant TEXT, p_prognose_id UUID, p_uke INT,
    p_faktisk BIGINT, p_aktor TEXT)
RETURNS TABLE (uke_nr INT, avvik_minutter BIGINT,
               baseline_avvik_minutter BIGINT,
               innenfor_intervall BOOLEAN, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_bane public.bemanningsbane%ROWTYPE;
    v_rad public.bemanningsmaaling%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm33_registrer_maaling');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm33_registrer_maaling: en måling uten et'
            ' navn på er ikke evidens'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT * INTO v_bane FROM public.bemanningsbane
     WHERE tenant = p_tenant AND prognose_id = p_prognose_id
       AND bemanningsbane.uke_nr = p_uke;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm33_registrer_maaling: uke % finnes ikke i'
            ' denne banen', p_uke
            USING ERRCODE = 'no_data_found';
    END IF;

    -- GJENSPILL FØR NEKT (131). Rekkefølgen er en dom: en måling
    -- som ALT står, skal svares med — også om uken siden er blitt
    -- «ikke over» fordi noen fabrikerte datoer. Den lagrede raden er
    -- fasit, ikke predikatet.
    SELECT * INTO v_rad FROM public.bemanningsmaaling
     WHERE tenant = p_tenant AND prognose_id = p_prognose_id
       AND bemanningsmaaling.uke_nr = p_uke;
    IF FOUND THEN
        -- ET GJENSPILL SOM SIER NOE ANNET ER IKKE ET GJENSPILL.
        -- To ulike forespørsler som deler nøkkel er to forespørsler,
        -- og å svare med den første ville skjult at den andre aldri
        -- ble utført.
        IF v_rad.faktisk_minutter IS DISTINCT FROM p_faktisk THEN
            RAISE EXCEPTION 'm33_registrer_maaling: uke % er alt målt'
                ' til % minutter — en måling kan ikke rettes',
                p_uke, v_rad.faktisk_minutter
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN QUERY SELECT v_rad.uke_nr, v_rad.avvik_minutter,
                            v_rad.baseline_avvik_minutter,
                            v_rad.innenfor_intervall, false;
        RETURN;
    END IF;

    IF v_bane.ukeslutt >= current_date THEN
        RAISE EXCEPTION 'm33_registrer_maaling: uke % er ikke over'
            ' (slutter %) — en ukorrigerbar måling av en uke som'
            ' ennå løper er et delresultat som aldri kan rettes',
            p_uke, v_bane.ukeslutt
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    BEGIN
        INSERT INTO public.bemanningsmaaling
            (tenant, prognose_id, uke_nr, faktisk_minutter,
             forventet_minutter, baseline_minutter,
             innenfor_intervall, maalt_av)
        VALUES (p_tenant, p_prognose_id, p_uke, p_faktisk,
                v_bane.forventet_minutter, v_bane.baseline_minutter,
                p_faktisk BETWEEN v_bane.nedre_minutter
                              AND v_bane.ovre_minutter,
                p_aktor)
        RETURNING * INTO v_rad;
    EXCEPTION WHEN unique_violation THEN
        -- KAPPLØPET: to samtidige kall med samme nøkkel kom begge
        -- forbi gjenspillsjekken over. Den som taper leser raden som
        -- vant og svarer med den — er den lik, er dette fortsatt et
        -- gjenspill.
        SELECT * INTO v_rad FROM public.bemanningsmaaling
         WHERE tenant = p_tenant AND prognose_id = p_prognose_id
           AND bemanningsmaaling.uke_nr = p_uke;
        IF v_rad.faktisk_minutter IS DISTINCT FROM p_faktisk THEN
            RAISE EXCEPTION 'm33_registrer_maaling: uke % er allerede'
                ' målt — en måling kan ikke rettes', p_uke
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN QUERY SELECT v_rad.uke_nr, v_rad.avvik_minutter,
                            v_rad.baseline_avvik_minutter,
                            v_rad.innenfor_intervall, false;
        RETURN;
    END;

    PERFORM public.m33_evidens(p_tenant, p_prognose_id,
        'registrer_maaling', p_aktor,
        jsonb_build_object('uke', p_uke,
                           'innenfor', v_rad.innenfor_intervall));
    RETURN QUERY SELECT v_rad.uke_nr, v_rad.avvik_minutter,
                        v_rad.baseline_avvik_minutter,
                        v_rad.innenfor_intervall, true;
END $$;
DROP FUNCTION IF EXISTS m33_avvikle_modell(TEXT, UUID, DATE, TEXT);
CREATE FUNCTION m33_avvikle_modell(p_tenant TEXT, p_modell_id UUID,
                                   p_gyldig_til DATE, p_aktor TEXT)
RETURNS TABLE (modell_id UUID, gyldig_til DATE, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.prognosemodell%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm33_avvikle_modell');
    -- GJENSPILL FØRST (131). `m33_modellvakt` avviser en ny
    -- avvikling, så et gjentatt kall med IDENTISK dato ga 400 på noe
    -- som alt hadde lyktes. Avvikling er enveis — det gjør ikke et
    -- gjenspill til en feil, det gjør det til et spørsmål med et
    -- svar.
    SELECT * INTO v_rad FROM public.prognosemodell
     WHERE tenant = p_tenant AND prognosemodell.modell_id
                                 = p_modell_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm33_avvikle_modell: modellen finnes ikke'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_rad.gyldig_til IS NOT NULL THEN
        -- EN ANNEN DATO ER IKKE ET GJENSPILL. Avviklingen står som
        -- den ble avgitt, og en ny dato ville vært en omskriving av
        -- «hvilken modell gjaldt da».
        IF v_rad.gyldig_til IS DISTINCT FROM p_gyldig_til THEN
            RAISE EXCEPTION 'm33_avvikle_modell: modellen er alt'
                ' avviklet per % — en avvikling kan ikke flyttes',
                v_rad.gyldig_til
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN QUERY SELECT v_rad.modell_id, v_rad.gyldig_til, false;
        RETURN;
    END IF;

    UPDATE public.prognosemodell SET gyldig_til = p_gyldig_til
     WHERE tenant = p_tenant AND prognosemodell.modell_id = p_modell_id
    RETURNING * INTO v_rad;
    PERFORM public.m33_evidens(p_tenant, p_modell_id,
        'avvikle_modell', p_aktor,
        jsonb_build_object('gyldig_til', p_gyldig_til));
    RETURN QUERY SELECT v_rad.modell_id, v_rad.gyldig_til, true;
END $$;

-- RETTIGHETENE PÅ NYTT ETTER `DROP` (SP-7).
REVOKE ALL ON FUNCTION m33_registrer_maaling(TEXT, UUID, INT, BIGINT,
                                             TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION m33_avvikle_modell(TEXT, UUID, DATE, TEXT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION m33_registrer_maaling(TEXT, UUID, INT,
    BIGINT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m33_avvikle_modell(TEXT, UUID, DATE, TEXT)
    TO disponit;

RESET ROLE;
