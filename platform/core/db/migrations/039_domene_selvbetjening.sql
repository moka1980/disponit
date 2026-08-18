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

REVOKE ALL ON FUNCTION ventende_domenechallenges(INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION bekreft_domenechallenge(TEXT, TEXT, TEXT, TEXT[])
    FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_domener') THEN
        GRANT EXECUTE ON FUNCTION ventende_domenechallenges(INT)
            TO disponit_domener;
        GRANT EXECUTE ON FUNCTION bekreft_domenechallenge(TEXT, TEXT, TEXT,
            TEXT[]) TO disponit_domener;
    END IF;
END $$;

-- Selvbetjeningens skriveende: kunden (via runtime-API-et, tenantbundet
-- økt + CSRF) kan be om en utfordring for sitt eget hostname. Funksjonen
-- skaper kun `ventende` + hash — autorisasjon oppstår først når beviset
-- står i kundens egen DNS-sone og arbeideren har funnet det.
GRANT EXECUTE ON FUNCTION utsted_challenge(TEXT, TEXT, BOOLEAN, TEXT, TEXT)
    TO disponit;
-- MERK: bekreft/ventende gis ALDRI til runtime. API-et genererte tokenet
-- og kunne dermed «bevist» det uten at noen DNS-sone noensinne bar det —
-- bekreftelsen tilhører arbeideren, som bare kan ferge det den faktisk
-- fant i DNS. Tester kaller funksjonene som eieren (SET LOCAL ROLE
-- disponit_domene_eier), samme mønster som _rydd_kapabiliteter.

RESET ROLE;
