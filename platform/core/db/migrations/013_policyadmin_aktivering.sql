-- ============================================================
-- 013 — Policyadministrasjon v1: herdet `aktiver_policy` (PR-013 CP5, V10)
--
-- Runtime-rollene mangler direkte INSERT/UPDATE/DELETE på `policyer` og
-- `policy_hode` (kun SELECT). Aktivering skjer KUN gjennom denne herdede
-- funksjonen — SECURITY DEFINER, eid av NOLOGIN-rollen `disponit_policy_eier`,
-- `search_path=pg_catalog`, EXECUTE kun til runtime.
--
-- 🔴 FIRE-ØYNE-GATEN LIGGER I FUNKSJONEN, IKKE I KALLEREN (Codex P1 R1):
-- runtime har EXECUTE på funksjonen, så et direkte `SELECT aktiver_policy(...)`
-- utenom Python-orkestreringen MÅ avvises. Derfor tar funksjonen (utkast_id,
-- runde) — ikke rått innhold — og VERIFISERER SELV, som eieren, at:
--   * utkastet finnes og er aktiverbart (validert/godkjent, frosset hash),
--   * runden finnes, er åpen/klar, ikke utløpt og ikke allerede brukt,
--   * attestasjonene når terskelen: antall ≥ påkrevd OG minst én ikke-forfatter,
--     og HVER attestasjon bandt rundens `diff_hash` (godkjente DIFFEN),
--   * base-versjonen fortsatt er aktiv (ellers serialization_failure → rebasering).
-- Innholdet som aktiveres LESES fra utkastet (kan ikke spoofes av kalleren).
-- Deaktivering + innsetting + pekerflytt + runde→brukt + utkast→aktivert skjer
-- i SAMME udelelige operasjon (V10/V1): en tenant står aldri med NULL aktive,
-- og en runde kan aldri brukes to ganger.
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
    v_neste         INT;
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
    SELECT neste_versjon, aktiv_versjon INTO v_neste, v_aktiv
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

    v_ny := v_neste::text;

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
           neste_versjon  = v_neste + 1,
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
-- EXECUTE gis av EIEREN (disponit_policy_eier) — som reserver_kapabilitet i
-- migrer.py — men her ligger den i migrasjonen fordi funksjonen defineres her.
GRANT EXECUTE ON FUNCTION aktiver_policy(TEXT, TEXT, INT, TEXT) TO disponit;
RESET ROLE;

-- Eieren (policy_eier) må kunne LESE utkast/runde/attestasjon og SKRIVE
-- policyer/policy_hode + lukke runde/utkast når funksjonen kjører (SECURITY
-- DEFINER kjører som eier). Grantene hører hjemme HER, sammen med funksjonen —
-- ikke som løs kode i migrer.py sin kjor(): da overlever de ENHVER
-- skjemagjenoppbygging (også testenes `_nullstill` + re-migrer). Kjøres som
-- migrator (eier av tabellene) etter RESET ROLE.
GRANT SELECT, INSERT, UPDATE ON policyer, policy_hode TO disponit_policy_eier;
GRANT SELECT         ON aktiveringsattestasjon        TO disponit_policy_eier;
GRANT SELECT, UPDATE ON policyutkast, aktiveringsrunde TO disponit_policy_eier;
