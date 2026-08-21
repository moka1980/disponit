-- 054 — plattformmodulaksept: søsterfunksjonen i akseptmaskineriet
-- (m02-aksept-klarsignalet, frosset 6d1cf8ecb850e457, F1 valg a).
--
-- m02 har INGEN rad i modulregisteret — ingen modulhode, release,
-- deployment eller kontrakt. Koden er lagringslaget i m01s prosess.
-- `aksepter_moduldeployment` kan derfor ikke kalles: den krever
-- drillrad, claiming-release, E2E-artefakt og kontrollkjøringer, og
-- ingen av delene FINNES for en ren plattformmodul.
--
-- Å opprette en syntetisk deployment-rad så signaturen passer, er
-- UTELUKKET (klarsignalets sikkerhetsinvariant
-- `register.syntetisk_rad_opprettet = 0`): registeret beskriver
-- virkeligheten, det gjøres ikke kompatibelt med en funksjonssignatur —
-- samme feil som en 'produksjon'-rad for m56 ville vært.
--
-- Formen er en SØSTERFUNKSJON i samme maskineri: samme eier, samme
-- disiplin (append-only, komplett punktsett eller ingen hendelse,
-- attestant ≠ akseptør målt på session_user, SP-2-replay, målinger mot
-- REGISTERETS grense — aldri kallerens), egen tabell fordi identiteten
-- er en annen: innholdsadressert (manifest_commit + manifest_sha256,
-- SP-11/SP-12) i stedet for en deploymentrad.
--
-- «UTENFOR GRENSEN» ER EN FØRSTEKLASSES TILSTAND, ikke et fravær:
-- punkter for mekanismer modulen ikke har (moduldrill uten bootbar
-- enhet, axe uten flate) skrives som rader med PÅKREVD begrunnelse, og
-- registeret FORHÅNDSGODKJENNER hvilke punkter som kan stå slik
-- (maalt_krav-sentinelen under). Skoping er ikke svekkelse — men den
-- avgjøres i grensedefinisjonen, aldri av kalleren.
--
-- Neste plattformmodul arver formen. `modulaksept` for deployments er
-- urørt.

-- ------------------------------------------------------------
-- 1. Grensen `m02-aksept-v1` i kravpunktregisteret (definert FØR
--    målingene). `delt_maaling` inn i kildedomenet: en måling som EIES
--    av et annet punkts artefakt og refereres VED HASH (klarsignalets
--    delingsregel: to punkter kan dele en måling, aldri en TYPE måling
--    — kilde_ref uten sha avvises av funksjonen).
--    Constraint-bytte er lovlig: krav-låsen verner RADENE, ikke skjemaet.
-- ------------------------------------------------------------
ALTER TABLE akseptkrav_punkt
    DROP CONSTRAINT akseptkrav_punkt_kilde_type_check;
ALTER TABLE akseptkrav_punkt
    ADD CONSTRAINT akseptkrav_punkt_kilde_type_check CHECK (kilde_type IN
        ('artefakt', 'registerhendelse', 'evidensfil', 'ci_kjoring',
         'delt_maaling'));

-- Sentinelen `<utenfor grensen>` i maalt_krav er registerets
-- FORHÅNDSGODKJENNING av skoping: bare punkter med den kan skrives som
-- `utenfor_grensen`, og de kan aldri skrives som målt.
INSERT INTO akseptkrav_punkt (krav_id, punkt, kilde_type, grenseverdi,
                              maalt_krav) VALUES
    ('m02-aksept-v1', 'feilinjisering_til_unntakskø', 'delt_maaling',
     'historikk_komplett=true og klartekst_payload_funnet=false'
     ' (feilinjisering-m01-v1)',
     'historikk_komplett=true, klartekst=0'),
    ('m02-aksept-v1', 'ytelse_bestatt', 'delt_maaling',
     '6000 auditerte svar -> 6000 loggposter, en_til_en (perf-m01-v1)',
     '6000/6000 en_til_en'),
    ('m02-aksept-v1', 'rollback_testet', 'delt_maaling',
     'tapte_loggposter=0 og identisk radtelling gjennom av-vinduet'
     ' (rollback-m01-v1; service-rollback, ikke moduldrill)',
     'tapte_loggposter=0, radtelling identisk'),
    -- Tredelt binding (klarsignalet §2): den delte r21-målingen bærer
    -- rad-per-hendelse-leddet og refereres ved artefaktets sha; de to
    -- CI-leddene (aktør fra kontekst; append-only i basen) bæres av
    -- grensens egen CI-attest — én rød porttest gjør kjøringen rød og
    -- attesten umulig.
    ('m02-aksept-v1', 'revisjonslogg_korrekt', 'delt_maaling',
     '10/10 revisjonsrader mot bestilt, 0 avvik (wcag-kontroll-v2,'
     ' r21) + CI-leddene aktør-fra-kontekst og append-only grønne',
     '10/10 rader, 0 avvik'),
    ('m02-aksept-v1', 'tester_gronne_pa_staging', 'artefakt',
     'suitekjøring på staging som artefakt; M-2s andel navngitt'
     ' (m02-suite-v1)', 'suite grønn, m2-andel grønn'),
    ('m02-aksept-v1', 'syntetisk_datasett_likt_lokalt', 'artefakt',
     'fordelingen 84/3/93 over 180 hendelser re-målbar av artefaktet'
     ' (m02-fordeling-v1)', '84/3/93 av 180, re-målt'),
    ('m02-aksept-v1', 'moduldrill_boot', 'delt_maaling',
     'utenfor grensen: m02 har ingen bootbar enhet — ingen modulhode-,'
     ' release- eller deploymentrad; koden kjører i m01s prosess.'
     ' Service-rollbacken er målt i rollback_testet',
     '<utenfor grensen>'),
    ('m02-aksept-v1', 'flate_axe_tastatur', 'delt_maaling',
     'utenfor grensen: m02 eier ingen flate — loggen leses av M-1'
     ' (beslutninger) og M-16 (nøkkeltall), som axe-portes av sine'
     ' egne moduler', '<utenfor grensen>');

INSERT INTO akseptkrav_ci (krav_id, arbeidsflyt, hendelse, gren,
                           konklusjon) VALUES
    ('m02-aksept-v1', '.github/workflows/ci.yml', 'push', 'main',
     'success');

-- ------------------------------------------------------------
-- 2. Tabellene — identiteten er innholdet, ikke en deploymentrad.
--    Migrator-eide, append-only; INSERT-fullmakten ligger hos
--    funksjonseieren alene (samme mønster som akseptbordene i 049).
-- ------------------------------------------------------------
CREATE TABLE plattformmodulaksept (
    modul_id        TEXT NOT NULL,
    -- SP-12: innholdsadressert identitet. Commiten navngir historien,
    -- sha-en navngir BYTENE — begge kreves, og formene håndheves.
    manifest_commit TEXT NOT NULL CHECK (manifest_commit ~ '^[0-9a-f]{40}$'),
    manifest_sha256 TEXT NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    grense_id       TEXT NOT NULL,
    ci_run          TEXT NOT NULL,
    ci_commit       TEXT NOT NULL,
    nokkel          TEXT NOT NULL UNIQUE,
    aktor           TEXT NOT NULL,
    akseptert_ts    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (modul_id, manifest_commit),
    UNIQUE (modul_id, manifest_commit, grense_id)
);

CREATE TABLE plattformmodulaksept_punkt (
    modul_id        TEXT NOT NULL,
    manifest_commit TEXT NOT NULL,
    grense_id       TEXT NOT NULL,
    punkt           TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('maalt',
                                                    'utenfor_grensen')),
    grenseverdi     TEXT,
    maalt_verdi     TEXT,
    kilde_type      TEXT CHECK (kilde_type IN ('artefakt', 'delt_maaling',
                                               'ci_kjoring')),
    kilde_ref       TEXT,
    begrunnelse     TEXT,
    PRIMARY KEY (modul_id, manifest_commit, grense_id, punkt),
    -- Klarsignalets CHECK: et målt punkt er komplett, et skopet punkt
    -- er begrunnet — aldri noe midt imellom.
    CONSTRAINT punkt_komplett CHECK (
         (status = 'maalt' AND grenseverdi IS NOT NULL
                           AND maalt_verdi IS NOT NULL
                           AND kilde_type IS NOT NULL
                           AND kilde_ref IS NOT NULL)
      OR (status = 'utenfor_grensen' AND begrunnelse IS NOT NULL
                                     AND btrim(begrunnelse) <> '')),
    FOREIGN KEY (modul_id, manifest_commit, grense_id)
        REFERENCES plattformmodulaksept (modul_id, manifest_commit,
                                         grense_id)
);

REVOKE ALL ON plattformmodulaksept, plattformmodulaksept_punkt
    FROM PUBLIC;
GRANT SELECT, INSERT ON plattformmodulaksept, plattformmodulaksept_punkt
    TO disponit_modul_eier;

CREATE TRIGGER plattformmodulaksept_immutable BEFORE UPDATE OR DELETE
    ON plattformmodulaksept
    FOR EACH ROW EXECUTE FUNCTION modulregister_append_only();
CREATE TRIGGER plattformmodulaksept_ingen_truncate BEFORE TRUNCATE
    ON plattformmodulaksept
    FOR EACH STATEMENT EXECUTE FUNCTION modulregister_append_only();
CREATE TRIGGER plattformmodulaksept_punkt_immutable
    BEFORE UPDATE OR DELETE ON plattformmodulaksept_punkt
    FOR EACH ROW EXECUTE FUNCTION modulregister_append_only();
CREATE TRIGGER plattformmodulaksept_punkt_ingen_truncate BEFORE TRUNCATE
    ON plattformmodulaksept_punkt
    FOR EACH STATEMENT EXECUTE FUNCTION modulregister_append_only();

-- ------------------------------------------------------------
-- 3. Søsterfunksjonen — i eiervinduet (047/048-disiplinen).
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_modul_eier;

CREATE FUNCTION aksepter_plattformmodul(
    p_modul_id TEXT, p_manifest_commit TEXT, p_manifest_sha TEXT,
    p_grense_id TEXT, p_ci_run TEXT, p_ci_commit TEXT,
    p_punkter JSONB, p_nokkel TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_punkt RECORD; v_verdi JSONB; v_ref TEXT; v_status TEXT;
        v_ci RECORD; v_ci_av TEXT; v_mangler TEXT; v_rad RECORD;
        v_avvik TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'plattformmodul:' || p_modul_id, 0));
    IF p_manifest_commit !~ '^[0-9a-f]{40}$'
       OR p_manifest_sha !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'aksepter_plattformmodul: identiteten er'
            ' innholdet — commit (40 hex) og manifest-sha (64 hex)'
            ' kreves; fikk «%» / «%»', p_manifest_commit, p_manifest_sha
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- SP-2: samme nøkkel -> samme hendelse, aldri to. Materialiteten er
    -- HELE innholdet: identiteten, grensen, CI-referansen og hvert
    -- punkts observasjon.
    SELECT * INTO v_rad FROM public.plattformmodulaksept
     WHERE nokkel = p_nokkel;
    IF FOUND THEN
        IF v_rad.modul_id IS DISTINCT FROM p_modul_id
           OR v_rad.manifest_commit IS DISTINCT FROM lower(p_manifest_commit)
           OR v_rad.manifest_sha256 IS DISTINCT FROM lower(p_manifest_sha)
           OR v_rad.grense_id IS DISTINCT FROM p_grense_id
           OR v_rad.ci_run IS DISTINCT FROM p_ci_run
           OR v_rad.ci_commit IS DISTINCT FROM lower(p_ci_commit) THEN
            RAISE EXCEPTION 'aksepter_plattformmodul: nøkkel % gjenbrukt'
                ' med annet innhold', p_nokkel
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        SELECT pk.punkt INTO v_avvik
          FROM public.plattformmodulaksept_punkt pk
         WHERE pk.modul_id = p_modul_id
           AND pk.manifest_commit = lower(p_manifest_commit)
           AND pk.grense_id = p_grense_id
           AND ((p_punkter -> pk.punkt) IS NULL
             OR (p_punkter -> pk.punkt) ->> 'status'
                    IS DISTINCT FROM pk.status
             OR (p_punkter -> pk.punkt) ->> 'maalt_verdi'
                    IS DISTINCT FROM pk.maalt_verdi
             OR (p_punkter -> pk.punkt) ->> 'kilde_ref'
                    IS DISTINCT FROM pk.kilde_ref
             OR (p_punkter -> pk.punkt) ->> 'begrunnelse'
                    IS DISTINCT FROM pk.begrunnelse)
         LIMIT 1;
        IF v_avvik IS NOT NULL THEN
            RAISE EXCEPTION 'aksepter_plattformmodul: nøkkel % gjenbrukt'
                ' med andre punktobservasjoner (%)', p_nokkel, v_avvik
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN;
    END IF;
    -- CI-kjøringen måles mot ATTESTEN og kravet i registeret — aldri
    -- mot kallerens egne parametre (053-formen) — og attestanten er en
    -- annen autentisert identitet enn akseptøren (fire øyne på
    -- session_user; SET ROLE rører den ikke).
    SELECT c.arbeidsflyt, c.hendelse, c.gren, c.konklusjon INTO v_ci
      FROM public.akseptkrav_ci c WHERE c.krav_id = p_grense_id;
    IF v_ci.arbeidsflyt IS NULL THEN
        RAISE EXCEPTION 'aksepter_plattformmodul: grensen % har intet'
            ' CI-krav registrert — en aksept uten CI-kontrakt finnes'
            ' ikke', p_grense_id USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF lower(p_ci_commit) IS DISTINCT FROM lower(p_manifest_commit) THEN
        RAISE EXCEPTION 'aksepter_plattformmodul: CI-kjøringen gjelder'
            ' %, identiteten er % — punktene påberoper seg «grønn CI på'
            ' akseptcommiten», og da må det være den som er målt',
            lower(p_ci_commit), lower(p_manifest_commit)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT a.attestert_av INTO v_ci_av
      FROM public.ci_kjoringsattest a
     WHERE a.ci_run = p_ci_run
       AND a.arbeidsflyt = v_ci.arbeidsflyt
       AND a.hendelse = v_ci.hendelse
       AND a.gren = v_ci.gren
       AND a.konklusjon = v_ci.konklusjon
       AND a.hode_sha = lower(p_ci_commit);
    IF v_ci_av IS NULL THEN
        RAISE EXCEPTION 'aksepter_plattformmodul: ingen attest sier at'
            ' kravets workflow (%) kjørte % på %/% for commit % — en'
            ' kjøring aksepten selv navngir beviser ingenting; det gjør'
            ' referatet fra veien som spurte', v_ci.arbeidsflyt,
            v_ci.konklusjon, v_ci.hendelse, v_ci.gren, lower(p_ci_commit)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_ci_av = session_user THEN
        RAISE EXCEPTION 'aksepter_plattformmodul: CI-attesten er skrevet'
            ' av % — samme innlogging som skriver aksepten; attestant og'
            ' akseptør er to autentiserte identiteter', v_ci_av
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Ett punkt per registerrad, komplett eller ingen hendelse — og
    -- grensen er REGISTERETS: verdier, kilder og skoping alike.
    SELECT string_agg(k.punkt, ', ') INTO v_mangler
      FROM public.akseptkrav_punkt k
     WHERE k.krav_id = p_grense_id
       AND (p_punkter -> k.punkt) IS NULL;
    IF v_mangler IS NOT NULL THEN
        RAISE EXCEPTION 'aksepter_plattformmodul: punktsettet er'
            ' ufullstendig — mangler %', v_mangler
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT j.key INTO v_avvik FROM jsonb_each(p_punkter) j
     WHERE NOT EXISTS (SELECT 1 FROM public.akseptkrav_punkt k
                        WHERE k.krav_id = p_grense_id
                          AND k.punkt = j.key)
     LIMIT 1;
    IF v_avvik IS NOT NULL THEN
        RAISE EXCEPTION 'aksepter_plattformmodul: punktet % står ikke i'
            ' grensen % — et punkt utenfor registeret er ingen'
            ' observasjon', v_avvik, p_grense_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.plattformmodulaksept (modul_id, manifest_commit,
        manifest_sha256, grense_id, ci_run, ci_commit, nokkel, aktor)
    VALUES (p_modul_id, lower(p_manifest_commit), lower(p_manifest_sha),
            p_grense_id, p_ci_run, lower(p_ci_commit), p_nokkel, p_aktor);
    FOR v_punkt IN SELECT k.punkt, k.kilde_type, k.grenseverdi,
                          k.maalt_krav
                     FROM public.akseptkrav_punkt k
                    WHERE k.krav_id = p_grense_id LOOP
        v_verdi := p_punkter -> v_punkt.punkt;
        v_status := v_verdi ->> 'status';
        v_ref := v_verdi ->> 'kilde_ref';
        IF v_punkt.maalt_krav = '<utenfor grensen>' THEN
            -- Registerets forhåndsgodkjente skoping: punktet SKAL stå
            -- utenfor, med kallerens begrunnelse — aldri som målt.
            IF v_status IS DISTINCT FROM 'utenfor_grensen' THEN
                RAISE EXCEPTION 'aksepter_plattformmodul: punkt % er'
                    ' skopet utenfor grensen av registeret og kan ikke'
                    ' skrives som «%» — skoping avgjøres i'
                    ' grensedefinisjonen, ikke av kalleren',
                    v_punkt.punkt, coalesce(v_status, '<mangler>')
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
            INSERT INTO public.plattformmodulaksept_punkt (modul_id,
                manifest_commit, grense_id, punkt, status, begrunnelse)
            VALUES (p_modul_id, lower(p_manifest_commit), p_grense_id,
                    v_punkt.punkt, 'utenfor_grensen',
                    v_verdi ->> 'begrunnelse');
            CONTINUE;
        END IF;
        IF v_status IS DISTINCT FROM 'maalt' THEN
            RAISE EXCEPTION 'aksepter_plattformmodul: punkt % er i'
                ' grensen og må være MÅLT — «%» er ikke en måling, og'
                ' UMAALTE-regelen blokkerer', v_punkt.punkt,
                coalesce(v_status, '<mangler>')
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        IF v_verdi ->> 'grenseverdi' IS DISTINCT FROM v_punkt.grenseverdi
           OR v_verdi ->> 'kilde_type' IS DISTINCT FROM v_punkt.kilde_type
        THEN
            RAISE EXCEPTION 'aksepter_plattformmodul: punkt % oppgir'
                ' grense «%» av type «%», mens kravet er «%» av type «%»'
                ' — grensen er registerets, ikke kallerens',
                v_punkt.punkt, v_verdi ->> 'grenseverdi',
                v_verdi ->> 'kilde_type', v_punkt.grenseverdi,
                v_punkt.kilde_type
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        IF v_verdi ->> 'maalt_verdi' IS DISTINCT FROM v_punkt.maalt_krav
        THEN
            RAISE EXCEPTION 'aksepter_plattformmodul: punkt % målte «%»,'
                ' men en grønn observasjon er «%» — en aksept skrives av'
                ' målinger som oppfyller kravet', v_punkt.punkt,
                v_verdi ->> 'maalt_verdi', v_punkt.maalt_krav
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        -- Kilden er en PEKER som holder, aldri en fortelling:
        -- `delt_maaling` og `artefakt` refereres VED HASH (klarsignalets
        -- delingsregel — to punkter kan dele en måling, aldri en type
        -- måling), `ci_kjoring` navngir nøyaktig akseptens egen kjøring.
        IF v_punkt.kilde_type IN ('delt_maaling', 'artefakt') THEN
            IF v_ref !~ '@sha256:[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'aksepter_plattformmodul: punkt % — «%»'
                    ' refererer ikke ved hash («sti@sha256:<64 hex>»);'
                    ' en delt måling uten sha er en beskrivelse, ikke en'
                    ' referanse', v_punkt.punkt, v_ref
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
        ELSIF v_punkt.kilde_type = 'ci_kjoring' THEN
            IF v_ref IS DISTINCT FROM
               ('run ' || p_ci_run || ' @ ' || lower(p_ci_commit)) THEN
                RAISE EXCEPTION 'aksepter_plattformmodul: punkt % viser'
                    ' til «%», mens aksepten bærer «run % @ %»',
                    v_punkt.punkt, v_ref, p_ci_run, lower(p_ci_commit)
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
        END IF;
        INSERT INTO public.plattformmodulaksept_punkt (modul_id,
            manifest_commit, grense_id, punkt, status, grenseverdi,
            maalt_verdi, kilde_type, kilde_ref)
        VALUES (p_modul_id, lower(p_manifest_commit), p_grense_id,
                v_punkt.punkt, 'maalt', v_punkt.grenseverdi,
                v_punkt.maalt_krav, v_punkt.kilde_type, v_ref);
    END LOOP;
    INSERT INTO public.modulregister_hendelse (modul_id, hendelse,
        aktor, detalj)
    VALUES (p_modul_id, 'plattformmodulaksept', p_aktor,
            pg_catalog.jsonb_build_object(
                'manifest_commit', lower(p_manifest_commit),
                'manifest_sha256', lower(p_manifest_sha),
                'grense_id', p_grense_id,
                'ci_run', p_ci_run, 'ci_commit', lower(p_ci_commit)));
END $$;

RESET ROLE;

SET LOCAL ROLE disponit_modul_eier;
REVOKE ALL ON FUNCTION aksepter_plattformmodul(TEXT, TEXT, TEXT, TEXT,
    TEXT, TEXT, JSONB, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION aksepter_plattformmodul(TEXT, TEXT, TEXT,
    TEXT, TEXT, TEXT, JSONB, TEXT, TEXT) TO disponit_modules_admin;
RESET ROLE;
