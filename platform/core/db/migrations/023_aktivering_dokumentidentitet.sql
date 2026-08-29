-- ============================================================
-- 023 — Dokumentet må bære den identiteten det aktiveres under (Codex P1)
--
-- 🔴 FUNNET: `policyutkast.policy_id` og dokumentets `innhold.meta.policy_id`
-- er TO uavhengige felter, og ingenting bandt dem sammen. Utkastendepunktet tar
-- `policy_id` fra forespørselens topp-nivå og `innhold` som en egen blokk; hver
-- av dem består sin egen formatkontroll. Redigeringsveien er verre: den skriver
-- nytt `innhold` UTEN å røre radens `policy_id`, så `meta.policy_id` kan endres
-- til hva som helst etter at utkastet er opprettet.
--
-- Aktiveringen lagrer da raden under databasens `policy_id` = «p», mens
-- dokumentet inni sier «q». Registeret er tilsynelatende friskt —
-- `hent_aktiv(p)` finner raden og validerer innholdet — men beslutningsmotoren
-- bygger policyreferansen sin fra DOKUMENTET
-- (`engine.policyreferanse`: `<meta.policy_id>@<meta.versjon>/<handling>`).
-- Revisjonsposten og M-37-gjenopprettingen slår derfor opp «q», der ingen aktiv
-- rad finnes, og sakene faller ut av automatisk behandling. Ingen feilmelding
-- peker på årsaken: begge halvdeler ser riktige ut hver for seg.
--
-- 🟢 RETNINGEN: identiteten er ÉN sak, ikke to. Dokumentet eier den — på samme
-- måte som det eier versjonen (020) — og registerkolonnen indekserer den.
-- Kontrollen ligger på tre nivåer, som versjonsinvariantene:
--   * `valider_utkast` nekter å FRYSE et dokument som bærer en annen identitet
--     enn raden (utfall `ugyldig`, med avviket i feillisten — eier ser hva som
--     er galt i editoren);
--   * porten kontrollerer det igjen før runden åpnes og før noen attesterer
--     (et utkast validert FØR denne fiksen kan bære avviket);
--   * denne funksjonen er siste skanse: et direkte kall utenom orkestreringen
--     når aldri forbi den.
--
-- Å SKRIVE OM `meta.policy_id` VED AKTIVERING er stengt, av samme grunn som for
-- versjonen: innholdet er frosset ved validering og `innholds_hash` er bundet i
-- attestasjonene. En omskriving ville aktivert noe ANNET enn det godkjennerne
-- signerte på.
--
-- Bruddet reises som `check_violation` med et NAVNGITT constraint
-- (`USING CONSTRAINT`), så kalleren kan skille dokumentavvik fra
-- versjonsinvariantene 020 innførte — som ellers deler feilkode. Uten navnet
-- ville et identitetsavvik blitt rapportert som `versjon_i_bruk`, altså en
-- korrekt kansellering med feil begrunnelse.
--
-- Funksjonen erstattes i sin helhet (022 er siste versjon); alt annet er
-- uendret. `db/kjorer.py` verifiserer SHA-256 på hver anvendt migrasjon, så
-- 020/021 kan ikke rettes i ettertid — rettelsen hører hjemme her.
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
    v_dok           JSONB;
    v_ugyldig_id    TEXT;
    v_dok_pid       TEXT;
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

    -- 1b. IDENTITETEN (se toppen): dokumentet må bære den policyen raden
    --     aktiveres under. Ellers indekseres innholdet under én id mens motoren
    --     bygger beslutningens policyreferanse fra en annen.
    v_dok_pid := v_innhold -> 'meta' ->> 'policy_id';
    IF v_dok_pid IS DISTINCT FROM v_policy_id THEN
        RAISE EXCEPTION 'aktiver_policy: utkast % bærer meta.policy_id %, men '
            'aktiveres under %', p_utkast_id, coalesce(v_dok_pid, '<null>'),
            v_policy_id
            USING ERRCODE = 'check_violation', CONSTRAINT = 'dokument_policy_id';
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
    -- Leddene NULLPADDES til samme bredde FØR sammenligningen (se 021):
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

    -- 4c. INNFØRINGSKONTRAKTEN (022, Codex P2 på #63): verifikator-id-en er
    --     den eneste FRIE nøkkelen i policyen, og den havner UTOLKET i
    --     diffstien godkjenneren attesterer (`verifikatorer.<id>.<felt>`).
    --     Med id-ene `foo` og `foo.beskrivelse` er `verifikatorer.foo.
    --     beskrivelse` både beskrivelsen til den ene og roten til den andre,
    --     og attestasjonen kan tilskrive en tillitsendring FEIL verifikator.
    --     En tom id gir stien `verifikatorer.` og et blad uten eier.
    --
    --     Python stiller kravet ved runde-åpning og attestasjon, men begge
    --     kan være passert før utrullingen — og et direkte kall hit går
    --     utenom dem. Speiler `schema._valider_innforing`: KUN de to tegnene
    --     som skaper flertydigheten, ikke husmønsteret, og ikke resten av
    --     lastekontrakten (den er bakoverkompatibel og sier ingenting nytt).
    v_dok := CASE WHEN jsonb_typeof(v_innhold) = 'object'
                  THEN v_innhold ELSE '{}'::jsonb END;
    SELECT string_agg(format('%s: %L', k.felt, k.vid), ', '
                      ORDER BY k.felt, k.vid)
      INTO v_ugyldig_id
      FROM (SELECT f.felt, o.vid
              FROM (VALUES ('verifikatorer'), ('verifikator_prioritet'))
                        AS f(felt)
              CROSS JOIN LATERAL jsonb_object_keys(
                  CASE WHEN jsonb_typeof(v_dok -> f.felt) = 'object'
                       THEN v_dok -> f.felt ELSE '{}'::jsonb END) AS o(vid)
             WHERE o.vid = '' OR position('.' in o.vid) > 0
                             OR position('[' in o.vid) > 0) AS k;
    --     `CONSTRAINT` settes bevisst: orkestreringen fanger check_violation
    --     fra denne funksjonen og har til nå kunnet anta at det var
    --     VERSJONEN (020). Uten et strukturert skille måtte den lest
    --     feilteksten for å vite forskjellen, og eier ville fått «versjonen er
    --     i bruk» om en id. `diag.constraint_name` er den maskinlesbare
    --     kanalen for nettopp det.
    IF v_ugyldig_id IS NOT NULL THEN
        RAISE EXCEPTION 'aktiver_policy: utkast % har verifikator-id som gjør '
            'diffstien flertydig (%)', p_utkast_id, v_ugyldig_id
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'verifikator_id_entydig';
    END IF;
    --     BÆRES VIDERE: denne migrasjonen erstatter hele kroppen til
    --     `aktiver_policy`, så en invariant som ikke står her er droppet
    --     — stille. 022 og denne kom hver sin vei inn i samme funksjon.

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
