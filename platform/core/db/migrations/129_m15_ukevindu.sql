-- =====================================================================
-- 129 — UKEVINDUET: PENGER MED FORFALL I DAG FALT MELLOM TO UKER.
-- =====================================================================
--
-- CODERABBIT FANT DET PÅ 128, ETTER MERGE, OG DET ER SAMME KLASSE SOM
-- GJENTAKELSESFEILEN SAMME PR RETTET — MED SAMME FARLIGE RETNING.
--
--   «Week 1 excludes amounts that fall due today. … The error goes in
--    the dangerous direction that this migration describes: the path
--    undercounts what goes out, the cash line looks better than it
--    is, and `bane_under_null` can stay silent on the day a payment
--    is due.»
--
-- Uke 1 hadde `fra = current_date`, og HVERT ukepredikat var eksklusivt
-- på nedre grense: `forfall > uke.fra`. En forpliktelse eller en
-- fordring med forfall NØYAKTIG I DAG traff derfor ingen uke i det
-- hele tatt, for det finnes ingen tidligere uke å falle i.
--
-- OG BELØPET VAR IKKE DEKKET NOE ANNET STED. `startsaldo_ore` kommer
-- fra `bankpost`, som bare holder BOKFØRTE bevegelser. En ubetalt
-- regning som forfaller i dag var altså borte fra banen — på den ene
-- dagen den betyr mest.
--
-- ---------------------------------------------------------------------
-- FIKSEN ER HUSETS EGEN VINDUSDEFINISJON, IKKE EN NY EN.
--
-- M-16 (`platform/core/api/lesing.py`) har hatt den siden fase 2:
-- halvåpent `[fra, til)`, og «en hendelse nøyaktig på `til` tilhører
-- neste vindu». Jeg skrev `(fra, til]` i stedet, uten å legge merke
-- til at det ga uke 1 en åpen nedre kant mot ingenting.
--
-- EN NY VINDUSARITMETIKK I HVER MODUL ER SELVE FEILEN. M-16s §3 sier
-- det rett ut om sine egne kortspørringer: «ingen kortspørring har
-- egen vindusaritmetikk». Denne modulen har det nå heller ikke.
--
-- ---------------------------------------------------------------------
-- `ukeslutt` BLIR SISTE FAKTISKE DAG, ikke den første i neste uke.
--
-- Med `[fra, til)` er `til` dagen ETTER uken. En kolonne som heter
-- `ukeslutt` og inneholder en dag utenfor uken er nøyaktig den samme
-- løgnen som `grunnlag_eldste_bevegelse` var (rettet i 128s egen
-- runde): et navn som ikke stemmer med innholdet er den neste feilen
-- noen gjør.
--
-- Derfor flyttes de to stedene som LESER `ukeslutt` med:
--
--   * `m15_registrer_maaling` nektet når `ukeslutt > current_date`.
--     Nå `>=`: uken er ikke over før dagen ETTER siste dag.
--   * `m15_banen` ga `kan_maales` på `ukeslutt <= current_date`.
--     Nå `<`, av samme grunn.
--
-- INGEN RADER ER BERØRT: 128 landet i dag, og ingen tenant har laget
-- en prognose. Rettelsen står her fordi migrasjoner er forward-only —
-- og fordi en bane som mangler dagens regning er en bane som lyver
-- den dagen den blir lest.
-- =====================================================================

SET LOCAL ROLE disponit_likviditet_eier;

CREATE OR REPLACE FUNCTION m15_lag_prognose(
    p_tenant TEXT, p_prognose_id UUID, p_modell_id UUID,
    p_usikkerhet_bp INT, p_aktor TEXT)
RETURNS TABLE (horisont_uker INT, gjelder_til DATE,
               startsaldo_ore BIGINT, uker INT, laveste_ore BIGINT,
               modellversjon TEXT, kravversjon INT, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_krav public.likviditetskrav%ROWTYPE;
    v_modell public.likviditetsmodell%ROWTYPE;
    v_gml public.likviditetsprognose%ROWTYPE;
    v_dato DATE := current_date;
    v_til DATE;
    v_saldo BIGINT;
    v_eldste DATE;
    v_n_poster INT;
    v_n_fordringer INT;
    v_n_registrert INT;
    v_lavest BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm15_lag_prognose');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm15_lag_prognose: en prognose bærer navnet'
            ' til den som ba om den'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_usikkerhet_bp IS NULL
       OR p_usikkerhet_bp NOT BETWEEN 1 AND 10000 THEN
        RAISE EXCEPTION 'm15_lag_prognose: usikkerheten oppgis i'
            ' basispunkter, 1-10000 (%)', p_usikkerhet_bp
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- GJENSPILL FØRST (SP-2, 127s form). Prognosen er append-only, så
    -- et gjenspill kan ikke skrive på nytt — det må svare med raden.
    --
    -- INGEN `FOR UPDATE` HER, OG DET ER IKKE EN FORGLEMMELSE:
    -- `FOR UPDATE` KREVER UPDATE-RETT, og §RETTIGHETER har REVOKEd
    -- den fra modulrollen nettopp fordi tabellen er append-only. En
    -- lås ville feilet med «permission denied» på en dør som gjør alt
    -- riktig — og det er en lærdom huset har betalt for før.
    --
    -- Låsen trengs heller ikke: raden kan ALDRI endres, så det finnes
    -- ingenting å beskytte mot. Kappløpet mellom to samtidige kall
    -- med samme id fanges av primærnøkkelen og
    -- `unique_violation`-grenen under (127s form).
    SELECT * INTO v_gml FROM public.likviditetsprognose
     WHERE tenant = p_tenant AND prognose_id = p_prognose_id;
    IF FOUND THEN
        IF v_gml.modell_id IS DISTINCT FROM p_modell_id THEN
            RAISE EXCEPTION 'm15_lag_prognose: prognose % finnes mot'
                ' en annen modell', p_prognose_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN QUERY SELECT
            v_gml.horisont_uker, v_gml.gjelder_til,
            v_gml.startsaldo_ore,
            (SELECT count(*)::int FROM public.prognosebane b
              WHERE b.tenant = p_tenant
                AND b.prognose_id = p_prognose_id),
            (SELECT min(b.punkt_ore) FROM public.prognosebane b
              WHERE b.tenant = p_tenant
                AND b.prognose_id = p_prognose_id),
            v_gml.modellversjon, v_gml.kravversjon, false;
        RETURN;
    END IF;

    SELECT * INTO v_krav FROM public.likviditetskrav
     WHERE tenant = p_tenant;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm15_lag_prognose: tenantens grenser er ikke'
            ' satt — uten horisonten finnes ingen dato å måle mot'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT * INTO v_modell FROM public.likviditetsmodell m
     WHERE m.tenant = p_tenant AND m.modell_id = p_modell_id
       AND public.m15_modell_gyldig(m.gyldig_fra, m.gyldig_til);
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm15_lag_prognose: modellen finnes ikke eller'
            ' gjelder ikke i dag. Arkivet tar imot en avviklet'
            ' versjon; det er BRUKEN som er stengt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- GRUNNLAGET, LEST ÉN GANG OG SKREVET NED PÅ RADEN.
    SELECT coalesce(sum(p.belop_ore), 0), max(p.bokfort), count(*)
      INTO v_saldo, v_eldste, v_n_poster
      FROM public.bankpost p
      JOIN public.bankkonto k ON k.tenant = p.tenant
                             AND k.konto_id = p.konto_id
     WHERE p.tenant = p_tenant AND k.aktiv;

    SELECT count(*) INTO v_n_fordringer FROM public.fordring f
     WHERE f.tenant = p_tenant AND f.status = 'apen'
       AND f.belop_ore > f.betalt_ore;

    SELECT count(*) INTO v_n_registrert FROM public.likviditetspost l
     WHERE l.tenant = p_tenant AND l.aktiv;

    IF v_n_poster + v_n_fordringer + v_n_registrert = 0 THEN
        RAISE EXCEPTION 'm15_lag_prognose: intet grunnlag. En bane'
            ' tegnet på ingenting er husets mest selvsikre løgn'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    v_til := v_dato + (v_krav.horisont_uker * 7);

    -- KAPPLØPET SOM ERSTATTER LÅSEN (127s lærdom). To samtidige kall
    -- med samme Idempotency-Key ser begge `NOT FOUND`; én INSERT
    -- vinner primærnøkkelen, og taperen svarer som et gjenspill i
    -- stedet for å gi en klientfeil.
    BEGIN
        INSERT INTO public.likviditetsprognose
            (tenant, prognose_id, laget_dato, horisont_uker,
             gjelder_til, modell_id, modellversjon, baselinje,
             startsaldo_ore, grunnlag_siste_bevegelse,
             grunnlag_antall_poster, grunnlag_antall_fordringer,
             grunnlag_antall_poster_registrert, kravversjon,
             opprettet_av)
        VALUES (p_tenant, p_prognose_id, v_dato, v_krav.horisont_uker,
                v_til, p_modell_id, v_modell.versjon,
                v_modell.baselinje, v_saldo, v_eldste, v_n_poster,
                v_n_fordringer, v_n_registrert, v_krav.versjon,
                btrim(p_aktor));
    EXCEPTION WHEN unique_violation THEN
        SELECT * INTO v_gml FROM public.likviditetsprognose
         WHERE tenant = p_tenant AND prognose_id = p_prognose_id;
        RETURN QUERY SELECT
            v_gml.horisont_uker, v_gml.gjelder_til,
            v_gml.startsaldo_ore,
            (SELECT count(*)::int FROM public.prognosebane b
              WHERE b.tenant = p_tenant
                AND b.prognose_id = p_prognose_id),
            (SELECT min(b.punkt_ore) FROM public.prognosebane b
              WHERE b.tenant = p_tenant
                AND b.prognose_id = p_prognose_id),
            v_gml.modellversjon, v_gml.kravversjon, false;
        RETURN;
    END;

    -- BANEN, I SAMME SETNING SOM PROGNOSEN LEVER I.
    --
    -- KUMULERINGEN ER EN VINDUSFUNKSJON, ikke en nøstet delspørring
    -- per uke. Første utgave regnet summen fram til uke N ved å
    -- gjenta hele ukeberegningen for hver j <= N — riktig svar,
    -- kvadratisk arbeid, og fullstendig uleselig.
    --
    -- Det siste var det verste. `metode` på modellraden lover at
    -- beregningen kan etterprøves, og EN MODELL INGEN KAN LESE ER EN
    -- MODELL INGEN KAN SI ER FEIL. En prognose ingen kan motsi er
    -- ikke en prognose — det er en påstand med desimaler.
    INSERT INTO public.prognosebane
        (tenant, prognose_id, uke_nr, ukeslutt, punkt_ore, nedre_ore,
         ovre_ore, inn_ore, ut_ore)
    WITH uke AS (
        SELECT u.n AS uke_nr,
               v_dato + ((u.n - 1) * 7) AS fra,
               v_dato + (u.n * 7) AS til
          FROM generate_series(1, v_krav.horisont_uker) AS u(n)),
    -- GJENTAKELSEN UTVIDES, DEN LESES IKKE BARE (CodeRabbit).
    --
    -- Første utgave talte hver post ÉN gang — i uken `forste_forfall`
    -- falt. En månedlig husleie på 85 000 dukket da opp én gang på
    -- tretten uker i stedet for tre, og `gjentakelse` var en kolonne
    -- som ble lagret og aldri brukt.
    --
    -- DET ER FEIL I DEN FARLIGE RETNINGEN. Banen underteller det som
    -- skal UT, så kontantbeholdningen ser bedre ut enn den er — og
    -- `bane_under_null`, funnet modulen finnes for, uteblir nettopp
    -- når den trengs.
    --
    -- `engang` får et intervall på tusen år: `generate_series` gir da
    -- nøyaktig én rad (startpunktet). Det er samme kodevei for alle
    -- fem gjentakelsene, og en gren mindre å ta feil i.
    forekomst AS (
        SELECT l.belop_ore, f.dato::date AS dato
          FROM public.likviditetspost l
          CROSS JOIN LATERAL generate_series(
              l.forste_forfall::timestamp,
              least(coalesce(l.gjelder_til,
                             v_dato + (v_krav.horisont_uker * 7)),
                    v_dato + (v_krav.horisont_uker * 7))::timestamp,
              CASE l.gjentakelse
                WHEN 'ukentlig' THEN interval '1 week'
                WHEN 'maanedlig' THEN interval '1 month'
                WHEN 'kvartalsvis' THEN interval '3 months'
                WHEN 'aarlig' THEN interval '1 year'
                ELSE interval '1000 years'
              END) AS f(dato)
         WHERE l.tenant = p_tenant AND l.aktiv),
    bevegelse AS (
        SELECT uke.uke_nr, uke.til,
               -- INN: utestående krav med forfall i uken. `status` og
               -- `belop > betalt` er M-23s egne begreper, ikke våre —
               -- vi tolker ikke fordringene, vi leser dem.
               (SELECT coalesce(sum(f.belop_ore - f.betalt_ore), 0)
                  FROM public.fordring f
                 WHERE f.tenant = p_tenant AND f.status = 'apen'
                   AND f.belop_ore > f.betalt_ore
                   AND f.forfall >= uke.fra
                   AND f.forfall < uke.til) AS inn,
               -- UT: bare de NEGATIVE forekomstene. `least(x,0)` og
               -- ikke `WHERE belop_ore < 0`: en positiv post er en
               -- forventet innbetaling noen har registrert, og den
               -- hører til i `inn`-kolonnen — ikke i en utgiftssum med
               -- feil fortegn.
               (SELECT coalesce(sum(least(fo.belop_ore, 0)), 0)
                  FROM forekomst fo
                 WHERE fo.dato >= uke.fra
                   AND fo.dato < uke.til) AS ut,
               (SELECT coalesce(sum(greatest(fo.belop_ore, 0)), 0)
                  FROM forekomst fo
                 WHERE fo.dato >= uke.fra
                   AND fo.dato < uke.til) AS inn_reg
          FROM uke),
    kumulativ AS (
        SELECT b.uke_nr, b.til, b.inn + b.inn_reg AS inn, b.ut,
               v_saldo + sum(b.inn + b.inn_reg + b.ut)
                   OVER (ORDER BY b.uke_nr
                         ROWS BETWEEN UNBOUNDED PRECEDING
                                  AND CURRENT ROW) AS kum
          FROM bevegelse b)
    SELECT p_tenant, p_prognose_id, k.uke_nr, k.til - 1, k.kum,
           -- BÅNDET REGNES HER, det mottas ikke. Et bånd kalleren
           -- kunne skrive fritt ville latt noen levere
           -- `nedre = ovre = punkt` og kalle det usikkerhet.
           k.kum - abs(k.kum * p_usikkerhet_bp / 10000),
           k.kum + abs(k.kum * p_usikkerhet_bp / 10000),
           k.inn, k.ut
      FROM kumulativ k;

    SELECT min(b.punkt_ore) INTO v_lavest FROM public.prognosebane b
     WHERE b.tenant = p_tenant AND b.prognose_id = p_prognose_id;

    PERFORM public.m15_evidens(p_tenant, p_prognose_id,
        'prognose_laget', btrim(p_aktor),
        jsonb_build_object('horisont_uker', v_krav.horisont_uker,
                           'modellversjon', v_modell.versjon,
                           'usikkerhet_bp', p_usikkerhet_bp));

    RETURN QUERY SELECT v_krav.horisont_uker, v_til, v_saldo,
                        v_krav.horisont_uker, v_lavest,
                        v_modell.versjon, v_krav.versjon, true;
END $$;
REVOKE ALL ON FUNCTION m15_lag_prognose(TEXT, UUID, UUID, INT, TEXT)
    FROM PUBLIC;

CREATE OR REPLACE FUNCTION m15_registrer_maaling(
    p_tenant TEXT, p_prognose_id UUID, p_uke_nr INT,
    p_faktisk_ore BIGINT, p_baselinje_ore BIGINT, p_aktor TEXT)
RETURNS TABLE (avvik_ore BIGINT, innenfor_intervall BOOLEAN,
               baselinje_avvik_ore BIGINT, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_bane public.prognosebane%ROWTYPE;
    v_gml public.prognosemaaling%ROWTYPE;
    v_avvik BIGINT;
    v_innenfor BOOLEAN;
    v_bavvik BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm15_registrer_maaling');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm15_registrer_maaling: en måling bærer navnet'
            ' til den som gjorde den'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- INGEN `FOR UPDATE`: banen er append-only, og låsen ville krevd
    -- en UPDATE-rett modulrollen med vilje ikke har.
    SELECT * INTO v_bane FROM public.prognosebane
     WHERE tenant = p_tenant AND prognose_id = p_prognose_id
       AND uke_nr = p_uke_nr;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm15_registrer_maaling: ukjent uke %/%',
            p_prognose_id, p_uke_nr USING ERRCODE = 'no_data_found';
    END IF;

    IF v_bane.ukeslutt >= current_date THEN
        RAISE EXCEPTION 'm15_registrer_maaling: uke % er ikke over'
            ' (slutter %). En måling av en uke som fortsatt løper er'
            ' et delvis tall som ser ut som et endelig',
            p_uke_nr, v_bane.ukeslutt
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    v_avvik := p_faktisk_ore - v_bane.punkt_ore;
    -- BÅNDET FRA RADEN, ikke fra kalleren. Se dørkommentaren.
    v_innenfor := p_faktisk_ore BETWEEN v_bane.nedre_ore
                                    AND v_bane.ovre_ore;
    v_bavvik := CASE WHEN p_baselinje_ore IS NULL THEN NULL
                     ELSE p_faktisk_ore - p_baselinje_ore END;

    SELECT * INTO v_gml FROM public.prognosemaaling
     WHERE tenant = p_tenant AND prognose_id = p_prognose_id
       AND uke_nr = p_uke_nr;
    IF FOUND THEN
        -- MÅLINGEN ER APPEND-ONLY. Et gjenspill med SAMME tall er et
        -- stille ja; et med ANDRE tall er en materiell konflikt, ikke
        -- en korreksjon. En måling som lot seg justere er en måling
        -- som alltid bekrefter.
        IF v_gml.faktisk_ore IS DISTINCT FROM p_faktisk_ore THEN
            RAISE EXCEPTION 'm15_registrer_maaling: uke % er alt målt'
                ' til et annet tall. En måling rettes ikke — den står'
                ' som den ble avgitt', p_uke_nr
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN QUERY SELECT v_gml.avvik_ore, v_gml.innenfor_intervall,
                            v_gml.baselinje_avvik_ore, false;
        RETURN;
    END IF;

    -- KAPPLØPET, SAMME FORM: to samtidige målinger av samme uke.
    -- Taperen leser vinnerens rad og svarer som et gjenspill — men
    -- BARE hvis tallet er det samme. Ulike tall er en materiell
    -- konflikt, og en måling rettes ikke.
    BEGIN
        INSERT INTO public.prognosemaaling
            (tenant, prognose_id, uke_nr, faktisk_ore, avvik_ore,
             innenfor_intervall, baselinje_avvik_ore, maalt_av)
        VALUES (p_tenant, p_prognose_id, p_uke_nr, p_faktisk_ore,
                v_avvik, v_innenfor, v_bavvik, btrim(p_aktor));
    EXCEPTION WHEN unique_violation THEN
        SELECT * INTO v_gml FROM public.prognosemaaling
         WHERE tenant = p_tenant AND prognose_id = p_prognose_id
           AND uke_nr = p_uke_nr;
        IF v_gml.faktisk_ore IS DISTINCT FROM p_faktisk_ore THEN
            RAISE EXCEPTION 'm15_registrer_maaling: uke % er alt målt'
                ' til et annet tall. En måling rettes ikke — den står'
                ' som den ble avgitt', p_uke_nr
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN QUERY SELECT v_gml.avvik_ore, v_gml.innenfor_intervall,
                            v_gml.baselinje_avvik_ore, false;
        RETURN;
    END;

    PERFORM public.m15_evidens(p_tenant, p_prognose_id,
        'maaling_registrert', btrim(p_aktor),
        jsonb_build_object('uke_nr', p_uke_nr,
                           'innenfor_intervall', v_innenfor));
    RETURN QUERY SELECT v_avvik, v_innenfor, v_bavvik, true;
END $$;
REVOKE ALL ON FUNCTION m15_registrer_maaling(TEXT, UUID, INT, BIGINT,
    BIGINT, TEXT) FROM PUBLIC;

CREATE OR REPLACE FUNCTION m15_banen(p_tenant TEXT, p_prognose_id UUID)
RETURNS TABLE (uke_nr INT, ukeslutt DATE, punkt_ore BIGINT,
               nedre_ore BIGINT, ovre_ore BIGINT, inn_ore BIGINT,
               ut_ore BIGINT, faktisk_ore BIGINT, avvik_ore BIGINT,
               innenfor_intervall BOOLEAN, maalt_av TEXT,
               kan_maales BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm15_banen');
    RETURN QUERY
    SELECT b.uke_nr, b.ukeslutt, b.punkt_ore, b.nedre_ore, b.ovre_ore,
           b.inn_ore, b.ut_ore, ma.faktisk_ore, ma.avvik_ore,
           ma.innenfor_intervall, ma.maalt_av,
           -- FLATEN SKAL IKKE REGNE UT SELV om uken er over. Regelen
           -- bor i basen, og lesedøra bærer den med hver rad (124s
           -- `kan_lukkes`-form).
           (b.ukeslutt < current_date AND ma.uke_nr IS NULL)
      FROM public.prognosebane b
      LEFT JOIN public.prognosemaaling ma
        ON ma.tenant = b.tenant AND ma.prognose_id = b.prognose_id
       AND ma.uke_nr = b.uke_nr
     WHERE b.tenant = p_tenant AND b.prognose_id = p_prognose_id
     ORDER BY b.uke_nr;
END $$;
REVOKE ALL ON FUNCTION m15_banen(TEXT, UUID) FROM PUBLIC;

RESET ROLE;
