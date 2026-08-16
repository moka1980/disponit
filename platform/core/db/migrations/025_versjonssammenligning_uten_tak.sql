-- ============================================================
-- 025 — Versjonsleddene sammenlignes uten tallcast i det hele tatt (Codex P2)
--
-- 🔴 FUNNET: monotonikontrollen caster leddene til `int[]`. Skjemaet setter
-- ingen øvre grense på et versjonsledd (`policy-schema-v0.2.json`:
-- `^\d+\.\d+\.\d+$`), så `2147483648.0.0` er et fullt gyldig utkast — og
-- casten reiser `numeric_value_out_of_range` (22003), ikke den `check_violation`
-- kalleren håndterer. Resultatet er en UHÅNDTERT feil: HTTP 500 midt i en
-- fire-øyne-runde, med attestasjonen tapt i rollbacken.
--
-- Verre: er en slik versjon først bootstrappet som AKTIV, treffer hver eneste
-- senere styrte aktivering for den policyen samme cast. Policyen blir umulig å
-- avløse gjennom den styrte veien, uten at noen feilmelding sier hvorfor.
--
-- 🔴 OG ETT TAK TIL (Codex, andre runde): `numeric` er ikke ubegrenset heller.
-- Heltallsdelen tar 131 072 sifre, og API-ets kroppsgrense på 256 KiB slipper
-- gjennom et ledd på ~140 000. Da reiste `::numeric[]` samme feil, og selv om
-- underblokken gjorde den om til et pent `check_violation`, var utfallet feil:
-- runden ble KANSELLERT som `versjon_i_bruk` for en versjon som i virkeligheten
-- er nyere, og som porten (Python) hadde godtatt. To gater som er uenige om
-- samme dokument er verre enn én som er streng.
--
-- 🟢 RETNINGEN: ingen tallcast. Leddene sammenlignes som (ANTALL SIFRE,
-- SIFRENE) etter at innledende nuller er strøket — for ikke-negative heltall er
-- det nøyaktig tallordenen, og det har ikke noe tak overhodet. Tekst-
-- sammenligningen tvinges til `COLLATE "C"` så ordenen er sifferordenen og
-- ikke en lokaltilpasset kollasjon.
--
-- Dette er PRESIS samme algoritme som porten bruker
-- (`policyadmin._versjonsnokkel`), og det er poenget: de to gatene måler
-- samme dokument likt, i stedet for å møtes i et hjørne der den ene sier
-- «nyere» og den andre kansellerer runden.
--
-- Alternativet Codex nevner — å sette en sifergrense i skjemaet — er ikke
-- valgt: det ville avvist dokumenter skjemaet i dag godtar, og flyttet
-- problemet til en grense noen må vedlikeholde. En sammenligning uten tak
-- trenger ingen grense.
--
-- 🔴 OG ETT HULL TIL (Codex, tredje runde): ankerkravet fra
-- `schema._valider_innforing` hadde ingen speiling her. Se 4d — kort sagt kan
-- Python-porten være passert før utrullingen, og et `handlinger[].id` med
-- avsluttende linjeskift gjør `engine.les_policyref` ute av stand til å lese
-- referansen etter at policyen er aktivert.
--
-- Funksjonen erstattes i sin helhet (024 er siste versjon); alt annet er
-- uendret. `db/kjorer.py` verifiserer SHA-256 på hver anvendt migrasjon, så
-- 020–023 kan ikke rettes i ettertid — rettelsen hører hjemme her. DENNE filen
-- er derimot rettet i seg selv, ikke etterfulgt av en 025: 024 har aldri vært
-- på `main`, den er ny i samme PR, og reglen som gjør historikken immutable
-- gjelder ANVENDTE migrasjoner. En 025 som erstattet en 024 ingen har kjørt,
-- ville vært historikk om vår egen review-runde, ikke om databasen.
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
    v_ulesbar_ref   TEXT;
    v_dok_pid       TEXT;
    v_dok_status    TEXT;
    v_i             INT;
    v_a             TEXT;
    v_b             TEXT;
    v_nyere         BOOLEAN;
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

    -- 1c. STATUSEN (se toppen): raden skrives som `produksjon` i steg 5, og
    --     `hent_aktiv` krever at dokumentet sier det samme. Et utkast merket
    --     `utkast`/`validert_pilot` ville derfor blitt aktivert — og deretter
    --     avvist som korrupt av hver eneste beslutning.
    v_dok_status := v_innhold -> 'meta' ->> 'status';
    IF v_dok_status IS DISTINCT FROM 'produksjon' THEN
        RAISE EXCEPTION 'aktiver_policy: utkast % har meta.status %, men '
            'aktivering skriver produksjon', p_utkast_id,
            coalesce(v_dok_status, '<null>')
            USING ERRCODE = 'check_violation', CONSTRAINT = 'dokument_status';
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
    -- Formen OG plassen. `policyer_pkey` er (tenant, policy_id, versjon), og en
    -- btree-oppføring har et hardt tak (~2704 byte) som de tre DELER — så ingen
    -- av dem er trygg målt for seg. Verken `policy_id` eller versjonen har noen
    -- maks i skjemaet, og uten dette ville en for stor nøkkel passert hit og
    -- først veltet på INSERT-en i steg 5, som `program_limit_exceeded`: en
    -- uhåndtert 500 etter at godkjennerne hadde signert. `octet_length` måler
    -- nøyaktig det btree teller. Porten håndhever samme tall
    -- (`policyadmin._MAKS_NOKKELBYTES`); dette er siste skanse.
    IF v_ny IS NULL OR v_ny !~ '^[0-9]+\.[0-9]+\.[0-9]+$'
       OR octet_length(p_tenant) + octet_length(v_policy_id)
          + octet_length(v_ny) > 2400 THEN
        RAISE EXCEPTION 'aktiver_policy: utkast % mangler brukbar meta.versjon '
            '(nøkkel % byte)', p_utkast_id,
            octet_length(p_tenant) + octet_length(v_policy_id)
            + coalesce(octet_length(v_ny), 0)
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
    -- versjon vi ikke kan lese som tall, kan vi heller ikke måle mot.
    --
    -- Leddene NULLPADDES til samme bredde FØR sammenligningen (se 021): uten
    -- det slår «2.0.0» en aktiv «2» — likt prefiks, flest ledd vinner — og en
    -- aktiv «2» fra den gamle telleren ville sluppet gjennom nøyaktig den
    -- versjonen den allerede bærer, som ikke er en nyere versjon i det hele
    -- tatt. Paddet til samme bredde er «2» det den betyr: 2.0.0.
    IF v_aktiv IS NOT NULL AND v_aktiv ~ '^[0-9]+(\.[0-9]+)*$' THEN
        v_ny_ledd    := string_to_array(v_ny, '.');
        v_aktiv_ledd := string_to_array(v_aktiv, '.');
        v_bredde := greatest(array_length(v_ny_ledd, 1),
                             array_length(v_aktiv_ledd, 1));
        v_ny_ledd := (v_ny_ledd
                      || array_fill('0'::text, ARRAY[v_bredde]))[1:v_bredde];
        v_aktiv_ledd := (v_aktiv_ledd
                      || array_fill('0'::text, ARRAY[v_bredde]))[1:v_bredde];
        -- INGEN tallcast (se toppen): både `int` og `numeric` har et tak, og
        -- skjemaet har ingen. Leddene måles som (antall sifre, sifrene) med
        -- innledende nuller strøket — nøyaktig tallorden for ikke-negative
        -- heltall, uten øvre grense. `COLLATE "C"` gjør at teksten sorterer på
        -- sifrene selv, ikke etter en lokaltilpasset kollasjon.
        v_nyere := NULL;                       -- NULL = like så langt
        FOR v_i IN 1..v_bredde LOOP
            v_a := ltrim(v_ny_ledd[v_i], '0');
            v_b := ltrim(v_aktiv_ledd[v_i], '0');
            IF length(v_a) <> length(v_b) THEN
                v_nyere := length(v_a) > length(v_b);
                EXIT;
            ELSIF v_a COLLATE "C" <> v_b THEN
                v_nyere := v_a COLLATE "C" > v_b;
                EXIT;
            END IF;
        END LOOP;
        -- `IS NOT TRUE` dekker begge nei-ene: leddene var like hele veien
        -- (v_nyere = NULL, altså SAMME versjon) eller den nye lå under.
        IF v_nyere IS NOT TRUE THEN
            RAISE EXCEPTION 'aktiver_policy: versjon % er ikke nyere enn '
                'aktiv % (%/%)', v_ny, v_aktiv, p_tenant, v_policy_id
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

    -- 4d. POLICYREFERANSEN MÅ VÆRE LESBAR (Codex P2 på denne PR-en).
    --     `_valider_innforing` fikk ankerkravet — mønstrene målt med ECMA-262
    --     sin `$` i stedet for Pythons, som også matcher rett FØR en
    --     avsluttende linjeskift. Den porten står i Python, og har samme to
    --     hull som verifikator-id-kravet over: et utkast som ble validert og
    --     fullt attestert FØR utrullingen bærer statusen sin videre hit, og
    --     runtime-rollen kan kalle denne funksjonen direkte.
    --
    --     Utfallet uten gaten er ikke en pen feil. `engine.les_policyref`
    --     leser `<policy_id>@<versjon>/<handling>` med `fullmatch`, altså
    --     ekte slutt. Et `handlinger[].id` = 'foo.bar' + linjeskift blir
    --     aktivert her og gjør referansen ULESBAR i motoren: beslutningen
    --     produserer evidens uten policyidentitet, og det oppdages først
    --     etter at policyen er i produksjon.
    --
    --     OMFANGET er de tre feltene referansen bygges av — det er nøyaktig
    --     de feltene bruddet forplanter seg gjennom. `meta.versjon` er alt
    --     dekket i 4b (PostgreSQLs `~` leser `$` som ekte slutt), så det som
    --     står igjen er identiteten og handlings-id-ene.
    --
    --     Og som `_pattern_ecma`: KUN DIFFERANSEN mot lastekontrakten. En
    --     verdi som feiler BEGGE lesningene — en handlings-id uten punktum,
    --     f.eks. — er en helt vanlig skjemafeil som lastekontrakten sier fra
    --     om ved validering. Reiste vi den her, ville en runde blitt
    --     KANSELLERT med «bryter et nytt krav» for et dokument som ganske
    --     enkelt er strukturelt ødelagt, og det er sammenblandingen de to
    --     kontraktene ble delt for å unngå. Differansen er derfor målt
    --     eksplisitt: verdien har en avsluttende linjeskift (som PostgreSQL
    --     avviser og Python slipper gjennom), og resten matcher mønsteret.
    SELECT string_agg(format('%s: %L', x.felt, x.verdi), ', '
                      ORDER BY x.felt, x.verdi)
      INTO v_ulesbar_ref
      FROM (SELECT 'meta.policy_id'::TEXT AS felt,
                   v_policy_id            AS verdi,
                   '^[a-z0-9-]+$'::TEXT   AS monster
            UNION ALL
            SELECT 'handlinger[].id', e.el ->> 'id',
                   '^[a-z0-9_]+(\.[a-z0-9_]+)+$'
              FROM jsonb_array_elements(
                       CASE WHEN jsonb_typeof(v_dok -> 'handlinger') = 'array'
                            THEN v_dok -> 'handlinger'
                            ELSE '[]'::jsonb END) AS e(el)
             WHERE jsonb_typeof(e.el) = 'object') AS x
     WHERE x.verdi IS NOT NULL
       AND right(x.verdi, 1) = E'\n'
       -- Pythons `$` godtar ÉN avsluttende linjeskift, ikke flere: matcher
       -- resten mønsteret, er dette presis den strengen lastekontrakten
       -- slapp gjennom og databasen aldri kunne lest.
       AND left(x.verdi, length(x.verdi) - 1) ~ x.monster;
    IF v_ulesbar_ref IS NOT NULL THEN
        RAISE EXCEPTION 'aktiver_policy: utkast % har felt som gjør '
            'policyreferansen ulesbar (%)', p_utkast_id, v_ulesbar_ref
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'policyref_lesbar';
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
