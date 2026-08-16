-- ============================================================
-- 021 — Monotonikontrollen må nullpadde versjonsleddene (Codex P1)
--
-- 🔴 FUNNET: monotonikontrollen 020 innførte sammenligner versjonsledd UTEN å
-- padde, og både Postgres' array-sammenligning og Pythons tuppelsammenligning
-- avgjør likt prefiks på LENGDE:
--
--     {2,0,0} > {2}        (2, 0, 0) > (2,)
--
-- Den aktive raden kan bære nøyaktig «2». Det er formen telleren
-- `policy_hode.neste_versjon` skrev, og 020 lar de radene BEVISST stå (de er
-- del av primærnøkkelen og målet for pekerens FK — å skrive dem om er en
-- operatørhandling). Et utkast med `meta.versjon = "2.0.0"` passerte derfor
-- kontrollen mot en aktiv «2», selv om det ikke er en NYERE versjon i det hele
-- tatt, men den SAMME. Resultatet: den innbakte versjonen blir en duplikat av
-- den som allerede står i den korrupte raden — ny korrupsjon skrevet oppå den
-- gamle, av nøyaktig den kontrollen som skulle hindret det.
--
-- 🟢 RETNINGEN: leddene NULLPADDES til samme bredde før sammenligningen, så
-- «2» leses som det den betyr — 2.0.0. Etterfølgeren må da ligge STRENGT over
-- den (2.0.1, 3.0.0), og «2.0.0» avvises som den dubletten den er. Bredden er
-- den STØRSTE av de to, ikke fast tre: en gammel rad med flere ledd enn tre
-- måles like riktig. Kravet er «samme bredde», ikke «tre ledd».
--
-- Porten (`policyadmin._krev_ny_versjon`) padder tilsvarende, så runden
-- stoppes før noen attesterer; denne funksjonen er siste skanse.
--
-- HVORFOR EN NY FIL OG IKKE EN RETTELSE I 020: `db/kjorer.py` verifiserer
-- SHA-256 på hver anvendte migrasjon og feiler hardt på endret fil —
-- historikken er immutable. 020 har landet på `main`, så rettelsen hører
-- hjemme her. Funksjonen erstattes i sin helhet, slik 020 selv erstattet den
-- fra 013; alt annet i den er uendret, og `neste_versjon` er allerede droppet.
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
    v_ny_ledd       TEXT[];
    v_aktiv_ledd    TEXT[];
    v_bredde        INT;
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

    -- 4b. VERSJONEN LESES FRA DOKUMENTET (se 020). Kontrollene under kjøres
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
    --
    -- Leddene NULLPADDES til samme bredde FØR sammenligningen (se toppen):
    -- array-sammenligningen lar ellers {2,0,0} slå {2} — likt prefiks, lengst
    -- vinner — og en aktiv «2» fra den gamle telleren ville sluppet gjennom
    -- dokumentversjonen «2.0.0», som er den samme versjonen, ikke en nyere.
    IF v_aktiv IS NOT NULL AND v_aktiv ~ '^[0-9]+(\.[0-9]+)*$' THEN
        v_ny_ledd    := string_to_array(v_ny, '.');
        v_aktiv_ledd := string_to_array(v_aktiv, '.');
        v_bredde := greatest(array_length(v_ny_ledd, 1),
                             array_length(v_aktiv_ledd, 1));
        v_ny_ledd := (v_ny_ledd
                      || array_fill('0'::text, ARRAY[v_bredde]))[1:v_bredde];
        v_aktiv_ledd := (v_aktiv_ledd
                      || array_fill('0'::text, ARRAY[v_bredde]))[1:v_bredde];
        IF v_ny_ledd::int[] <= v_aktiv_ledd::int[] THEN
            RAISE EXCEPTION 'aktiver_policy: versjon % er ikke nyere enn aktiv '
                '% (%/%)', v_ny, v_aktiv, p_tenant, v_policy_id
                USING ERRCODE = 'check_violation';
        END IF;
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
