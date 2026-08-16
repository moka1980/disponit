-- ============================================================
-- 020 — Aktiveringen lagrer policyens EGEN versjon (Codex P1)
--
-- 🔴 FUNNET: `policyer` hadde TO skrivere med TO versjonskonvensjoner.
--   * `policyregister.registrer` (bootstrap/oppsett) lagrer dokumentets egen
--     `meta.versjon` — semantisk, «1.0.0», slik skjemaet krever
--     (`policies/policy-schema-v0.2.json`: `^\d+\.\d+\.\d+$`).
--   * Den styrte aktiveringen (013) allokerte i stedet fra telleren
--     `policy_hode.neste_versjon` og lagret «1», «2», «3» …
--
-- `policyregister.hent_aktiv` krever at registerets `versjon` er NØYAKTIG lik
-- `innhold.meta.versjon` — den kontrollen finnes for at indekskolonnen ikke
-- skal kunne drive fra dokumentets eget utsagn. Den styrte aktiveringen brøt
-- den ved HVER aktivering: aktiveringen svarte «aktivert», og hver påfølgende
-- beslutningsforespørsel avviste den ferske policyen som KORRUPT.
--
-- Feilen er eldre enn PR-en som avdekket den, men var maskert: en normalt
-- bootstrappet tenant veltet før den kom hit (delindeksen `en_aktiv_per_policy`
-- felte INSERT-en fordi ankerraden manglet). Da ankerraden kom på plass, ble
-- veien nåbar — og en maskert korrupsjon er verre enn en høylytt 500.
--
-- 🟢 RETNINGEN: DOKUMENTET EIER VERSJONEN. Det er dokumentet som valideres,
-- hashes, differes og attesteres; registerkolonnen finnes for å indeksere det.
-- Aktiveringen leser derfor `meta.versjon` fra UTKASTET (inne i funksjonen,
-- som eieren — kalleren kan ikke oppgi den) og lagrer den som `versjon`.
--
-- Alternativet Codex nevner — å skrive om den innbakte versjonen ved
-- aktivering — er stengt: innholdet er frosset ved validering, `innholds_hash`
-- er bundet i attestasjonene, og en omskriving ville aktivert noe ANNET enn
-- det godkjennerne signerte på.
--
-- INVARIANTENE aktiveringen nå håndhever, under `policy_hode`-låsen:
--   1. utkastet MÅ ha en semantisk `meta.versjon`;
--   2. versjonen MÅ være ubrukt for (tenant, policy_id) — PK-en ville uansett
--      felt INSERT-en, men som en rå `unique_violation` kalleren ikke kan skille
--      fra pekerdrift;
--   3. versjonen MÅ være nyere enn den aktive (monotoni — det telleren skulle
--      gitt oss).
-- Bruddene reises som `check_violation`, som er UBRUKT ellers i funksjonen og
-- derfor entydig for kalleren. Porten (`policyadmin`) kontrollerer det samme
-- FØR runden åpnes og før noen attesterer; dette er siste skanse, på linje med
-- fire-øyne-kontrollen.
--
-- `neste_versjon` FJERNES. Kolonnen allokerer ingenting lenger, og `revisjon`
-- er allerede den monotone telleren over aktiveringer. En sovende teller som
-- HETER «neste versjon» er nøyaktig fellen som skapte funnet: neste skriver
-- ville brukt den igjen.
--
-- ⚠️ EKSISTERENDE DATA: rader som ALT er skrevet av den gamle allokeringen
-- bærer «1»/«2» i `versjon` mens dokumentet sier noe annet. De er allerede
-- korrupte for `hent_aktiv` — denne migrasjonen gjør dem verken bedre eller
-- verre, og retter dem BEVISST ikke automatisk: `versjon` er del av primær-
-- nøkkelen og målet for pekerens FK, og å skrive om versjonen på en aktiv,
-- attestert policyrad er en operatørhandling, ikke en stille sideeffekt av en
-- skjemamigrasjon. Merk også at monotonikontrollen måler mot den aktive raden
-- som den STÅR: er den «2», må neste dokument ligge over 2 (f.eks. «3.0.0»).
-- ============================================================

SET LOCAL ROLE disponit_policy_eier;
CREATE OR REPLACE FUNCTION aktiver_policy(
    p_tenant       TEXT,
    p_utkast_id    TEXT,
    p_runde        INT,
    p_base_versjon TEXT)           -- forventet gjeldende aktiv (NULL = deny-all)
RETURNS TEXT
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_policy_id     TEXT;
    v_innhold       JSONB;
    v_innholds_hash TEXT;
    v_ustatus       TEXT;
    v_rstatus       TEXT;
    v_diff_hash     TEXT;
    v_pakrevd       INT;
    v_utloper       TIMESTAMPTZ;
    v_opid          TEXT;
    v_total         INT;
    v_uavhengige    INT;
    v_diff_avvik    INT;
    v_aktiv         TEXT;
    v_ny            TEXT;
BEGIN
    -- 1. Utkastet — låst. Innholdet som aktiveres kommer HERFRA, ikke fra
    --    kalleren (så det som aktiveres er nøyaktig det som ble attestert).
    SELECT policy_id, innhold, innholds_hash, status
      INTO v_policy_id, v_innhold, v_innholds_hash, v_ustatus
      FROM public.policyutkast
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'aktiver_policy: ukjent utkast %', p_utkast_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_ustatus NOT IN ('validert', 'godkjent') THEN
        RAISE EXCEPTION 'aktiver_policy: utkast % er ikke aktiverbart (status=%)',
            p_utkast_id, v_ustatus USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_innholds_hash IS NULL THEN
        RAISE EXCEPTION 'aktiver_policy: utkast % mangler frosset innholds_hash',
            p_utkast_id USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- 2. Runden — låst. Må være aktiverbar og ikke allerede brukt.
    SELECT status, diff_hash, pakrevd_antall_godkjennere, utloper,
           decision_operation_id
      INTO v_rstatus, v_diff_hash, v_pakrevd, v_utloper, v_opid
      FROM public.aktiveringsrunde
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id AND runde = p_runde
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'aktiver_policy: ukjent runde %/%', p_utkast_id, p_runde
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_opid IS NOT NULL OR v_rstatus NOT IN ('apen', 'klar') THEN
        RAISE EXCEPTION 'aktiver_policy: runde %/% er ikke aktiverbar (status=%)',
            p_utkast_id, p_runde, v_rstatus USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_utloper <= now() THEN
        RAISE EXCEPTION 'aktiver_policy: runde %/% er utløpt', p_utkast_id, p_runde
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- 3. FIRE-ØYNE (V6), håndhevet i funksjonen: antall ≥ påkrevd, minst én
    --    ikke-forfatter, og HVER attestasjon bandt rundens diff. Et direkte
    --    kall uten en tilstrekkelig attestert runde avvises her.
    SELECT count(*),
           count(*) FILTER (WHERE NOT er_forfatter),
           count(*) FILTER (WHERE diff_hash IS DISTINCT FROM v_diff_hash)
      INTO v_total, v_uavhengige, v_diff_avvik
      FROM public.aktiveringsattestasjon
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id AND runde = p_runde;
    IF v_total < v_pakrevd THEN
        RAISE EXCEPTION 'aktiver_policy: for få godkjennere (% < %) for %/%',
            v_total, v_pakrevd, p_utkast_id, p_runde
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF v_uavhengige < 1 THEN
        RAISE EXCEPTION 'aktiver_policy: ingen uavhengig godkjenner for %/%',
            p_utkast_id, p_runde USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF v_diff_avvik > 0 THEN
        RAISE EXCEPTION 'aktiver_policy: % attestasjon(er) bandt ikke rundens '
            'diff for %/%', v_diff_avvik, p_utkast_id, p_runde
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- 4. Lås ankerraden (V1). Finnes den ikke, opprett den (onboarding).
    INSERT INTO public.policy_hode (tenant, policy_id)
        VALUES (p_tenant, v_policy_id)
        ON CONFLICT (tenant, policy_id) DO NOTHING;
    SELECT aktiv_versjon INTO v_aktiv
      FROM public.policy_hode
     WHERE tenant = p_tenant AND policy_id = v_policy_id
       FOR UPDATE;

    -- Konfliktdeteksjon (§4): den godkjennerne diffet mot MÅ fortsatt være
    -- aktiv. En konkurrerende aktivering flyttet pekeren → rebasering.
    IF v_aktiv IS DISTINCT FROM p_base_versjon THEN
        RAISE EXCEPTION 'aktiver_policy: base % er ikke lenger aktiv (%) — '
            'rebasering kreves', p_base_versjon, v_aktiv
            USING ERRCODE = 'serialization_failure';
    END IF;

    -- 4b. VERSJONEN LESES FRA DOKUMENTET (se toppen). Kontrollene under kjøres
    --     med hoderaden låst, så ingen annen STYRT aktivering kan legge seg
    --     imellom dette og INSERT-en i steg 5.
    v_ny := v_innhold -> 'meta' ->> 'versjon';
    IF v_ny IS NULL OR v_ny !~ '^[0-9]+\.[0-9]+\.[0-9]+$' THEN
        RAISE EXCEPTION 'aktiver_policy: utkast % mangler semantisk '
            'meta.versjon (%)', p_utkast_id, coalesce(v_ny, '<null>')
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM public.policyer
                WHERE tenant = p_tenant AND policy_id = v_policy_id
                  AND versjon = v_ny) THEN
        RAISE EXCEPTION 'aktiver_policy: versjon % er allerede registrert for '
            '%/%', v_ny, p_tenant, v_policy_id USING ERRCODE = 'check_violation';
    END IF;
    -- Monotoni: kun når den aktive versjonen selv er tallpunktet. Eldre rader
    -- (registrert før PR-013) kan bære hva som helst i TEXT-kolonnen, og en
    -- kastefeil på en cast ville vært en dårligere feil enn ingen kontroll.
    IF v_aktiv IS NOT NULL AND v_aktiv ~ '^[0-9]+(\.[0-9]+)*$'
       AND string_to_array(v_ny, '.')::int[]
           <= string_to_array(v_aktiv, '.')::int[] THEN
        RAISE EXCEPTION 'aktiver_policy: versjon % er ikke nyere enn aktiv % '
            '(%/%)', v_ny, v_aktiv, p_tenant, v_policy_id
            USING ERRCODE = 'check_violation';
    END IF;

    -- 5. Deaktiver forrige + sett inn etterfølger i SAMME operasjon (V10).
    IF v_aktiv IS NOT NULL THEN
        UPDATE public.policyer SET aktiv = false
         WHERE tenant = p_tenant AND policy_id = v_policy_id AND versjon = v_aktiv;
    END IF;
    INSERT INTO public.policyer
        (tenant, policy_id, versjon, innholds_hash, status, innhold, aktiv)
      VALUES (p_tenant, v_policy_id, v_ny, v_innholds_hash, 'produksjon',
              v_innhold, true);
    UPDATE public.policy_hode
       SET aktiv_versjon = v_ny,
           revisjon       = revisjon + 1
     WHERE tenant = p_tenant AND policy_id = v_policy_id;

    -- 6. Lukk runden (apen→klar→brukt følger statemaskinen) + utkast→aktivert
    --    (validert→godkjent→aktivert). Alt i SAMME tx: runden kan aldri brukes
    --    to ganger (decision_operation_id unik når satt).
    IF v_rstatus = 'apen' THEN
        UPDATE public.aktiveringsrunde SET status = 'klar'
         WHERE tenant = p_tenant AND utkast_id = p_utkast_id AND runde = p_runde;
    END IF;
    UPDATE public.aktiveringsrunde
       SET status = 'brukt',
           decision_operation_id = 'aktiver-' || p_utkast_id || '-r' || p_runde
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id AND runde = p_runde;

    IF v_ustatus = 'validert' THEN
        UPDATE public.policyutkast SET status = 'godkjent'
         WHERE tenant = p_tenant AND utkast_id = p_utkast_id;
    END IF;
    UPDATE public.policyutkast SET status = 'aktivert'
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id;

    RETURN v_ny;
END $$;
REVOKE ALL ON FUNCTION aktiver_policy(TEXT, TEXT, INT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION aktiver_policy(TEXT, TEXT, INT, TEXT) TO disponit;
RESET ROLE;

-- Telleren er død — først NÅ, etter at funksjonen som leste den er byttet ut.
ALTER TABLE policy_hode DROP COLUMN IF EXISTS neste_versjon;
