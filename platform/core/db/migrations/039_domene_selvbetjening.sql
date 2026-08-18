-- ============================================================
-- 039 — SELVBETJENT DOMENEVERIFISERING (eiers krav 18/8: «det skal
-- være selvbetjening»)
--
-- Før: utstedelse og førstegangsverifisering var en ren ops-vei
-- (disponit_domains_admin, NOLOGIN) — kunden hadde INGEN flate for å få
-- domenet sitt verifisert, og bestillingsveien (038) svarte
-- bestilling_hostname_uverifisert uten noen vei videre.
--
-- Etter: kunden utsteder sin egen challenge fra flaten
-- (POST /v1/domener → utsted_challenge, runtime får EXECUTE — den
-- skaper KUN en `ventende` rad og et hash-lagret token; ingen
-- autorisasjon oppstår), legger TXT-verdien i sonen sin, og
-- DOMENER-arbeideren bekrefter automatisk: kryss-tenant-plukk via
-- `ventende_domenechallenges()` og DB-holdt bevis i
-- `bekreft_domenechallenge()` — NØYAKTIG samme form som revalideringen
-- (019 §3.35): arbeideren ferger TXT-verdier, DATABASEN holder dem mot
-- `challenge_token_hash`, og selve statusovergangen skjer i
-- `verifiser_domenekontroll` med alle dens overtakelses- og
-- avklaringsporter urørt.
--
-- Sikkerhetssnittet er BEVART: verken runtime-API-et eller
-- arbeiderrollen får verifiser_domenekontroll direkte
-- (oppsett-postgresql.sh-kontrakten står) — arbeideren kan bare bevise
-- et token den faktisk fant i DNS, og API-et kan bare be om at et bevis
-- blir mulig.
-- ============================================================

SET LOCAL ROLE disponit_domene_eier;

-- Kryss-tenant-plukket for arbeideren: rader som VENTER med et friskt,
-- ubrukt challenge-vindu. Speiler `revalideringskandidater` (019) — den
-- ene, revidérbare kryss-tenant-lesingen, i stedet for et tabellgrant
-- arbeideren kunne brukt til hva som helst.
CREATE OR REPLACE FUNCTION ventende_domenechallenges(p_grense INT DEFAULT 200)
RETURNS TABLE (tenant TEXT, hostname TEXT)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT d.tenant, d.hostname
      FROM public.domenekontroll d
     WHERE d.status = 'ventende'
       AND d.challenge_token_hash IS NOT NULL
       AND d.challenge_utloper > now()
     ORDER BY d.challenge_utstedt
     LIMIT p_grense
$$;

-- Førstegangsbekreftelsen — 019 §3.35-formen: TXT-verdiene sendes MED,
-- basen holder dem mot hashen. `RAISE` ved manglende bevis (arbeideren
-- teller det som oppslagsfeil, aldri suksess); statusovergangen og ALLE
-- portene (overtakelse, avklaring, generasjon++) eies fortsatt av
-- `verifiser_domenekontroll` — samme eier, kalt her.
CREATE OR REPLACE FUNCTION bekreft_domenechallenge(
    p_tenant TEXT, p_hostname TEXT, p_aktor TEXT, p_txt_verdier TEXT[])
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_bevis TEXT; v_utloper TIMESTAMPTZ; v_status TEXT;
        v_wildcard BOOLEAN; v_treff BOOLEAN;
BEGIN
    SELECT d.challenge_token_hash, d.challenge_utloper, d.status, d.wildcard
      INTO v_bevis, v_utloper, v_status, v_wildcard
      FROM public.domenekontroll d
     WHERE d.tenant = p_tenant AND d.hostname = p_hostname
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'bekreft_domenechallenge: %/% finnes ikke',
            p_tenant, p_hostname USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Idempotent mot dobbeltplukk: alt verifisert er et JA, ikke en feil.
    IF v_status = 'verifisert' THEN
        RETURN 'verifisert';
    END IF;
    IF v_status <> 'ventende' THEN
        -- avklaring_kreves/tilbakekalt: bare M-37-avgjørelsen (016) kan
        -- flytte raden — en DNS-post skal aldri overstyre en avklaring.
        RETURN v_status;
    END IF;
    IF v_bevis IS NULL OR v_utloper IS NULL OR v_utloper <= now() THEN
        RAISE EXCEPTION 'bekreft_domenechallenge: %/% har ingen gyldig '
            'utfordring (utløpt eller aldri utstedt)', p_tenant, p_hostname
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT EXISTS (
        SELECT 1 FROM unnest(coalesce(p_txt_verdier, ARRAY[]::TEXT[])) AS v
         WHERE v IS NOT NULL
           AND lower(encode(sha256(convert_to(btrim(v), 'UTF8')), 'hex'))
               = lower(btrim(v_bevis)))
      INTO v_treff;
    IF NOT v_treff THEN
        RAISE EXCEPTION 'bekreft_domenechallenge: %/% — utfordringsbeviset '
            'finnes ikke i TXT-svaret (kontroll ikke bevist)',
            p_tenant, p_hostname USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN public.verifiser_domenekontroll(
        p_tenant, p_hostname, v_wildcard, p_aktor);
END $$;

-- ------------------------------------------------------------
-- Konflikter som venter på sin M-37-sak (Codex P1).
--
-- `verifiser_domenekontroll` gjør overtakelsen OG adjudikasjonskravet i én
-- transaksjon: A tilbakekalles, B settes `avklaring_kreves` med motparten
-- på raden, og signalet `konflikt:<A>` returneres. SAKEN kan ikke opprettes
-- der: `opprett_overtakelsessak` krypterer payloaden med tenantens DEK
-- (KEK-en lever i prosessen, ikke i basen) og skriver `revisjonslogg` +
-- `unntak`. Det er runtime-autoritet basen ikke har, og som
-- verifiseringsarbeideren (`disponit_domener` — EXECUTE på nøyaktig to
-- funksjoner) med vilje ikke får.
--
-- Uten en vei videre var utfallet det verst mulige: A mistet
-- autorisasjonen, B ble stående i `avklaring_kreves`, og fordi KUN
-- `avgjor_domeneovertakelse` kan løfte noen ut av avklaring — og den nås
-- bare gjennom en sak — kunne ingen av dem noensinne bli løst.
--
-- Signalet er derfor ikke en melding som kan gå tapt, men TILSTANDEN selv:
-- en rad i `avklaring_kreves` MED `konflikt_motpart` ER en konflikt som
-- venter på sin sak. `sikre_ventende_overtakelsessaker()` (M-37-arbeideren,
-- runtime-rolle + KEK) drenerer den, én rad om gangen med konteksten bundet
-- til RADENS tenant — samme form som `reap_evidensfrister` (038 §5). Faller
-- prosessen ut midt i, står radene igjen og neste syklus finner dem på nytt:
-- det finnes ingen kø å reparere.
--
-- Utvalget filtrerer IKKE bort rader som alt har sin sak. Det ville krevd
-- SELECT på `unntak`/`revisjonslogg` for domeneeieren — en utvidelse av
-- eierens rekkevidde for en ren ytelsesdetalj. Idempotensen ligger der den
-- hører hjemme, i `opprett_overtakelsessak` (nøkkel = hostname+generasjon,
-- under advisory-lås), og en konflikt som venter på et menneske koster da
-- ett oppslag per dreneringssyklus.
CREATE OR REPLACE FUNCTION ventende_overtakelseskonflikter(
    p_grense INT DEFAULT 100)
RETURNS TABLE (tenant TEXT, hostname TEXT, motpart TEXT, generasjon BIGINT)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT d.tenant, d.hostname, d.konflikt_motpart,
           d.autorisasjonsgenerasjon
      FROM public.domenekontroll d
     WHERE d.status = 'avklaring_kreves'
       AND d.konflikt_motpart IS NOT NULL
     ORDER BY d.hostname, d.tenant
     LIMIT p_grense
$$;

REVOKE ALL ON FUNCTION ventende_domenechallenges(INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION bekreft_domenechallenge(TEXT, TEXT, TEXT, TEXT[])
    FROM PUBLIC;
REVOKE ALL ON FUNCTION ventende_overtakelseskonflikter(INT) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_domener') THEN
        GRANT EXECUTE ON FUNCTION ventende_domenechallenges(INT)
            TO disponit_domener;
        GRANT EXECUTE ON FUNCTION bekreft_domenechallenge(TEXT, TEXT, TEXT,
            TEXT[]) TO disponit_domener;
    END IF;
    -- Dreneringen kjøres av M-37-arbeideren, som har DEK/KEK og runtime-DML.
    -- Den rollen er `disponit_arbeider` i drift; lokalt/test kjører samme vei
    -- som runtime (038-presedensen), derfor begge.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_arbeider') THEN
        GRANT EXECUTE ON FUNCTION ventende_overtakelseskonflikter(INT)
            TO disponit_arbeider;
    END IF;
END $$;
GRANT EXECUTE ON FUNCTION ventende_overtakelseskonflikter(INT) TO disponit;

-- ------------------------------------------------------------
-- Selvbetjeningens skriveende — TENANTBUNDET (Codex P1).
--
-- Kunden (via runtime-API-et, tenantbundet økt + CSRF) kan be om en
-- utfordring for sitt eget hostname. Funksjonen skaper kun `ventende` +
-- hash; autorisasjon oppstår først når beviset står i kundens egen
-- DNS-sone og arbeideren har funnet det.
--
-- MEN `utsted_challenge` (016/018) er SECURITY DEFINER og STOLER PÅ
-- `p_tenant`. Gis den rått til den delte runtime-rollen, er den et
-- KRYSS-TENANT SKRIVEPRIMITIV: en kompromittert runtime-credential — eller
-- ett enkelt kall fra en fremtidig kodevei som glemte konteksten — kan
-- opprette rader og BYTTE `challenge_token_hash` for en vilkårlig tenant,
-- FORCE RLS til tross, siden definer-funksjonen omgår RLS per definisjon.
-- Å bytte hashen på en annens `ventende` rad er nok: da er det angriperens
-- token DNS-beviset holdes mot.
--
-- Derfor en GUARDET INNPAKNING, og bare den gis til runtime. Porten er
-- `krev_tenantkontekst` (038) — den samme alle andre runtime-kallbare
-- definer-funksjoner bruker, så regelen har ÉN definisjon: `p_tenant` må
-- være den tenantkonteksten kalleren faktisk står i, og fail-closed uten
-- kontekst. 016-kroppen røres ikke: ops-veien
-- (`disponit_domains_admin`) er en kryss-tenant ADMINISTRASJONSvei og
-- setter ingen tenantkontekst — en port der ville brutt den, ikke sikret
-- den.
-- Innpakningen KØER OGSÅ utstedelsen (Codex P2). 016s `utsted_challenge`
-- bytter hash og vindu men lar `status` stå — med vilje, for at en
-- re-utstedelse ikke skal kunne flytte en rad. `ventende_domenechallenges`
-- plukker bare `ventende`. Legger kunden til et hostname som står
-- `tilbakekalt` eller `utlopt`, fikk hun altså 201 med en brukbar TXT-
-- oppskrift som INGEN arbeider noensinne ville sett på — en selvbetjening
-- som svarte «gjort» og aldri ble ferdig.
--
-- Her, i selvbetjeningens egen inngang, er det trygt å flytte den:
-- HANDLINGEN «kunden ba om en ny utfordring for dette navnet» er nettopp
-- det som gjør raden ventende igjen. 016-kroppen og ops-veien beholder sin
-- «status uendret»-semantikk. Og at en tilbakekalt eier kan bevise kontroll
-- på nytt er den DOKUMENTERTE veien (016 §3 B4 rad 2), ikke en omvei rundt
-- tilbakekallet: beviset må stå i kundens egen DNS-sone, og
-- `verifiser_domenekontroll` holder alle portene sine.
--
-- To tilstander flyttes IKKE, og da skal svaret være nei — ikke 201 på en
-- utfordring som blir liggende:
--   * `avklaring_kreves` — en M-37-sak er under behandling; bare
--     `avgjor_domeneovertakelse` kan flytte raden.
--   * `tilbakekalt` MED `konflikt_motpart` — en kandidat som ble AVVIST av
--     M-37. Ville vi satt den `ventende`, mistet `verifiser_domenekontroll`
--     nettopp det gjerdet (018) som tvinger en reapplikasjon gjennom en NY
--     avklaring, og avvisningen var omgått med et DNS-oppslag.
CREATE OR REPLACE FUNCTION utsted_challenge_selvbetjent(
    p_tenant TEXT, p_hostname TEXT, p_wildcard BOOLEAN,
    p_token_hash TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_host TEXT; v_status TEXT; v_motpart TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'utsted_challenge_selvbetjent');
    -- §0-gjerdet FØR raden slås opp: `utsted_challenge` normaliserer aldri,
    -- den avviser, og to tekstlige former av samme navn skal ikke kunne bli
    -- to ulike oppslag her heller.
    v_host := public.krev_kanonisk_hostname(p_hostname);
    -- FOR UPDATE: statusen vi beslutter på skal ikke kunne endres av et
    -- samtidig `bekreft_domenechallenge`/`verifiser_domenekontroll` mellom
    -- lesningen og køingen under. Bare RADlåsen tas, aldri hostname-
    -- advisory-låsen: da finnes det ingen lås å ta i motsatt rekkefølge.
    SELECT d.status, d.konflikt_motpart INTO v_status, v_motpart
      FROM public.domenekontroll d
     WHERE d.tenant = p_tenant AND d.hostname = v_host
       FOR UPDATE;
    IF v_status = 'avklaring_kreves'
       OR (v_status = 'tilbakekalt' AND v_motpart IS NOT NULL) THEN
        RAISE EXCEPTION 'utsted_challenge_selvbetjent: %/% avventer en '
            'M-37-avgjørelse (%) — en ny utfordring kan ikke behandles',
            p_tenant, v_host, v_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM public.utsted_challenge(p_tenant, v_host, p_wildcard,
                                    p_token_hash, p_aktor);
    IF v_status IN ('tilbakekalt', 'utlopt') THEN
        UPDATE public.domenekontroll SET status = 'ventende'
         WHERE tenant = p_tenant AND hostname = v_host;
        INSERT INTO public.domenekontroll_hendelse
            (tenant, hostname, hendelse, fra_status, til_status, grunn, aktor)
            VALUES (p_tenant, v_host, 'ventende', v_status, 'ventende',
                    'challenge_reutstedt_selvbetjening', p_aktor);
    END IF;
END $$;

REVOKE ALL ON FUNCTION utsted_challenge_selvbetjent(TEXT, TEXT, BOOLEAN,
    TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION utsted_challenge_selvbetjent(TEXT, TEXT, BOOLEAN,
    TEXT, TEXT) TO disponit;
-- Og den rå formen gis ALDRI til runtime. Eksplisitt REVOKE, ikke bare et
-- fjernet GRANT: en base som rakk å kjøre en tidligere utgave av denne
-- migrasjonen skal miste rettigheten, ikke beholde den i stillhet.
REVOKE EXECUTE ON FUNCTION utsted_challenge(TEXT, TEXT, BOOLEAN, TEXT, TEXT)
    FROM disponit;
-- MERK: bekreft/ventende gis ALDRI til runtime. API-et genererte tokenet
-- og kunne dermed «bevist» det uten at noen DNS-sone noensinne bar det —
-- bekreftelsen tilhører arbeideren, som bare kan ferge det den faktisk
-- fant i DNS. Tester kaller funksjonene som eieren (SET LOCAL ROLE
-- disponit_domene_eier), samme mønster som _rydd_kapabiliteter.

RESET ROLE;

-- Porten er REVOKEd fra PUBLIC i 038 og eies av `disponit_m37_claimer`.
-- Innpakningen over er SECURITY DEFINER og kjører altså som
-- `disponit_domene_eier` — uten dette grantet ville porten feilet med
-- «permission denied» og selvbetjeningen vært nede, ikke sikret. Grantet
-- gis av eieren selv; migrator er medlem av begge roller.
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_domene_eier;
RESET ROLE;
