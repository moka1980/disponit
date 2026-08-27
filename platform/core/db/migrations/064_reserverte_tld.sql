-- 064: reserverte TLD-er er ikke revalideringskandidater (#209) — navn som
-- per RFC 6761 ALDRI kan resolves, skal verken plukkes eller telle som
-- bevis om resolvernes helse.
--
-- MÅLT I DRIFT, ikke resonnert fram: `disponit-domenerevalidering.service`
-- har hatt 129 røde kjøringer siden 19/8 16:56Z. `fasit.test` og
-- `fasit-frekvens.test` (WCAG-fasit-fixturene) kan ikke resolves — `.test`
-- er reservert nettopp for at den aldri skal nå global DNS — så de får
-- aldri en fersk `siste_vellykkede_revalidering`. Dermed bor de PERMANENT
-- i kø 1 (sikkerhetsnettet), som er uten grense og aldri kappes: de
-- plukkes hver time, for alltid, og utgjør hele nevneren. 2 av 2 oppslag
-- feiler → `bred_resolverfeil` → exit 1. Alarmen som skal varsle om en BRED
-- resolverfeil har ropt ulv i åtte døgn.
--
-- Tilstanden er ABSORBERENDE, og det er poenget: en rad som ikke kan
-- lykkes, kan heller ikke slutte å feile. Ingen kjøring rydder opp etter
-- seg, for det finnes ingenting å rydde.
--
-- KLASSEN, IKKE INSTANSEN: å slette de to radene hadde stoppet alarmen i
-- dag og ingenting mer. Neste fixtur på et reservert navn starter den på
-- nytt. Regelen her er utledet av NAVNET og trenger ingen lagret tilstand
-- som kan settes feil: `.test`, `.example`, `.invalid` og `.localhost` er
-- reservert i RFC 6761 (`.invalid` også i RFC 2606 §2) og kan ikke
-- delegeres til noen. Ingen kan eie dem, så ingen kan miste dem heller.
--
-- HVORFOR I BASEN OG IKKE I ARBEIDEREN: filtrerte vi etter plukket, hadde
-- radene allerede spist oppslagene og stått i nevneren — og K-invarianten
-- ville bodd to steder. Utvalget og nevneren har ÉN kilde (019s eget
-- argument), og denne regelen hører til i den kilden.
--
-- GRENSEN GÅR VED TLD-EN, OG DET ER EN MÅLT GRENSE, IKKE EN BEKVEM EN:
-- `example.com` er reservert for dokumentasjon (RFC 2606 §3), men den er
-- faktisk delegert og svarer med en ekte A-post — den KAN resolves, og
-- hører derfor hjemme i populasjonen. TLD-en `.example` kan det ikke.
-- Skillet er «finnes navnet i global DNS», ikke «ser navnet oppdiktet ut».
-- Det er også grunnen til at husets `.example`-korpus i testene ikke er
-- rørt av dette: testene som måler scheduleren flyttes til `example.com`
-- og blir liggende i populasjonen der de hører hjemme.
--
-- HVA DENNE MIGRASJONEN IKKE GJØR: fixturradene står urørt. De er villet
-- (WCAG-fasit-arcen), statusen deres røres ikke, og ingenting slettes.
-- De er allerede utenfor `v_domeneautorisasjon.gyldig` (016 §6 krever
-- suksess innen 72 t), og det var de FØR denne endringen — å ta dem ut av
-- revalideringen endrer altså ingen autorisasjonstilstand. Det er nettopp
-- derfor fiksen er trygg: den fjerner støy, ikke en port.
--
-- Kroppene under er 019 og 039 ORDRETT — eneste endring er predikatet
-- (SPEIL-presedensen fra 062: aldri skriv naboens dør fra hukommelsen).

-- ------------------------------------------------------------
-- §1 Predikatet. IMMUTABLE, ren funksjon av navnet — samme klasse og
-- samme eierskap som `er_kanonisk_hostname` (018 §0a): den leser ingen
-- rader, så den har ingenting å lekke, og default EXECUTE er riktig.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.er_reservert_tld(p_hostname TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE PARALLEL SAFE
SET search_path = pg_catalog AS $fn$
    -- Siste label, ikke et suffikstreff: `sub.fasit.test` er reservert,
    -- mens `mintest.no` og `nyinvalid.no` ikke er det. En LIKE '%.test'
    -- ville tatt den første og bommet på begge de andre.
    SELECT p_hostname IS NOT NULL
       AND lower(substring(p_hostname from '[^.]+$'))
           IN ('test', 'example', 'invalid', 'localhost')
$fn$;

SET LOCAL ROLE disponit_domene_eier;

-- ------------------------------------------------------------
-- §2 Revalideringsscheduleren (019 §3.35) — predikatet inn i BEGGE
-- grenene. Kø 1 er den som faktisk blør i dag; kandidat-CTE-en tas med
-- fordi en reservert rad som EN GANG hadde en fersk revalidering (en
-- fixtur seedet med `siste_vellykkede_revalidering = now()`) ellers
-- ville falt ned i kø 2/kø 3 og spist budsjett i stedet for
-- sikkerhetsnett. Samme rad, samme umulighet, samme svar.
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
           AND NOT public.er_reservert_tld(d.hostname)
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
           AND NOT public.er_reservert_tld(d.hostname)
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

-- ------------------------------------------------------------
-- §3 Nevneren. Reserverte navn ut av N også — ellers regnes budsjettet
-- av en populasjon som er større enn den som kan plukkes, og K blir et
-- tak over rader som ikke finnes.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION revalideringspopulasjon()
RETURNS INT LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT count(*)::INT FROM public.domenekontroll
     WHERE status IN ('verifisert','avklaring_kreves')
       AND NOT public.er_reservert_tld(hostname);
$$;

-- ------------------------------------------------------------
-- §4 Verifiseringspasset (039). SAMME KLASSE, samme alarm: passet regner
-- `uenige / vurdert > 0.20` med nøyaktig samme terskel, og en ventende
-- utfordring på et reservert navn ville dratt den nevneren på samme
-- måte. At det ikke blør i dag er en egenskap ved dagens fixturdata, ikke
-- ved koden — og en fiks som lukker den ene døra og lar den andre stå,
-- lukker ikke klassen.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION ventende_domenechallenges(p_grense INT DEFAULT 200)
RETURNS TABLE (tenant TEXT, hostname TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    -- Stemplingen ligger i en CTE og resultatet leses ut med en SELECT:
    -- `RETURN QUERY` tar en QUERY, ikke en DML-setning med RETURNING.
    RETURN QUERY
    WITH plukk AS (
        SELECT k.tenant AS t, k.hostname AS h
          FROM public.domenekontroll k
         WHERE (k.status = 'ventende'
                -- REAPPLIKASJONEN (Codex P2): en kandidat M-37 AVVISTE står
                -- `tilbakekalt` MED motparten på seg, og 018 har en egen gren
                -- for nettopp den — ny generasjon, ny avklaring, nytt
                -- `konflikt:<motpart>`. Uten raden her hadde den grenen ingen
                -- produksjonskaller: arbeideren så bare `ventende`, og den
                -- eneste veien tilbake gikk gjennom at en operatør kalte
                -- administrasjonsfunksjonen manuelt. Raden flyttes IKKE til
                -- `ventende` for å komme hit — det er nettopp statusen
                -- `tilbakekalt` + motpart som ER gjerdet 018 kjenner igjen.
                OR (k.status = 'tilbakekalt'
                    AND k.konflikt_motpart IS NOT NULL))
           AND NOT public.er_reservert_tld(k.hostname)
           AND k.challenge_token_hash IS NOT NULL
           AND k.challenge_utloper > now()
         ORDER BY (CASE WHEN k.challenge_forsokt >= k.challenge_utstedt
                        THEN k.challenge_forsokt END) NULLS FIRST,
                  k.challenge_utstedt
         LIMIT p_grense
           FOR UPDATE SKIP LOCKED
    ), stemplet AS (
        UPDATE public.domenekontroll d
           SET challenge_forsokt = now()
          FROM plukk p
         WHERE d.tenant = p.t AND d.hostname = p.h
        RETURNING d.tenant AS t, d.hostname AS h
    )
    SELECT s.t, s.h FROM stemplet s;
END $$;

RESET ROLE;
