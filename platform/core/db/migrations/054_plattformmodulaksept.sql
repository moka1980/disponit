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
-- SP-11/SP-12) i stedet for en deploymentrad. Digesten er MÅLT, ikke
-- påstått: grensens identitetspunkt binder den til bytes en annen
-- autentisert identitet har lest og attestert.
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
-- 0. GRENSEKLASSEN — hvilken akseptvei en grense hører til (Codex P1,
--    runde 2).
--
--    `m02-aksept-v1` MÅ stå i det delte kravpunktregisteret: attestene
--    binder `(krav_id, punkt)` dit (`evidensfil_attest`, 049), så en egen
--    kopi av registeret ville vært en ny attestmaskin, ikke en fiks. Men
--    `aksepter_moduldeployment` tar imot ENHVER registrert `p_krav_id`,
--    og plattformgrensens punkter er alle `artefakt`. En kaller med en
--    ekte drill, en claiming release og et promotert artefakt kunne
--    derfor velge plattformgrensen, gjenta de ni verdiene — sentinelene
--    inkludert, som for DEN funksjonen bare er literale grønne strenger —
--    og få en immutabel `modulaksept` uten å ha vært i nærheten av
--    WCAG-grensen deployment-veien finnes for.
--
--    Skranken legges i TABELLENE, ikke i en kopi av en 450-linjers
--    funksjon: grensen får en klasse i registeret, og hver aksepttabell
--    bærer sin egen klasse i en CHECK-låst kolonne som FK-en må matche.
--    Det er 049-idiomet fra drillens utfallskolonner — kolonnene finnes
--    for å bære FK-en, ikke for å kunne variere. Da felles feilvalget på
--    enhver skrivevei, ikke bare i definereren, og ingen av de to
--    akseptfunksjonene måtte skrives om for å si det.
-- ------------------------------------------------------------
CREATE TABLE akseptgrense_klasse (
    krav_id TEXT PRIMARY KEY,
    klasse  TEXT NOT NULL CHECK (klasse IN ('deployment', 'plattform')),
    UNIQUE (krav_id, klasse)
);
-- Samme grunn som for kravpunktene og CI-kravet (049): en aksept er
-- skrevet mot ÉN klasse, og klassen kan ikke endres under den etterpå.
CREATE TRIGGER akseptgrense_klasse_immutable BEFORE UPDATE OR DELETE
    ON akseptgrense_klasse
    FOR EACH ROW EXECUTE FUNCTION modulregister_append_only();
CREATE TRIGGER akseptgrense_klasse_ingen_truncate BEFORE TRUNCATE
    ON akseptgrense_klasse
    FOR EACH STATEMENT EXECUTE FUNCTION modulregister_append_only();
GRANT SELECT ON akseptgrense_klasse TO disponit_modul_eier;

-- Hver grense som fantes FØR denne migrasjonen er per definisjon en
-- deployment-grense: deployment-aksepten har vært den eneste veien.
-- Listen LESES derfor ut av registeret og av aksepene selv, i stedet for
-- å ramses opp for hånd — en håndskrevet liste kan gå ut av takt med
-- 049/050/052, og da ville FK-en under felt en ekte aksept.
INSERT INTO akseptgrense_klasse (krav_id, klasse)
SELECT krav_id, 'deployment' FROM akseptkrav_punkt
 UNION
SELECT krav_id, 'deployment' FROM modulaksept;

-- Deployment-aksepten bærer klassen sin, på hodet OG på punktradene:
-- funksjonen skriver begge, og eier-/migratorveien kan skrive dem
-- direkte (samme lærdom som punkt-FK-en lenger nede).
ALTER TABLE modulaksept
    ADD COLUMN krav_klasse TEXT NOT NULL DEFAULT 'deployment'
        CHECK (krav_klasse = 'deployment'),
    ADD FOREIGN KEY (krav_id, krav_klasse)
        REFERENCES akseptgrense_klasse (krav_id, klasse);
ALTER TABLE modulaksept_punkt
    ADD COLUMN krav_klasse TEXT NOT NULL DEFAULT 'deployment'
        CHECK (krav_klasse = 'deployment'),
    ADD FOREIGN KEY (krav_id, krav_klasse)
        REFERENCES akseptgrense_klasse (krav_id, klasse);

-- …og plattformgrensen er en plattformgrense.
INSERT INTO akseptgrense_klasse (krav_id, klasse) VALUES
    ('m02-aksept-v1', 'plattform');

-- ------------------------------------------------------------
-- 1. Grensen `m02-aksept-v1` i kravpunktregisteret (definert FØR
--    målingene).
--
--    DELINGEN BÆRES AV HASHEN, IKKE AV EN EGEN KILDETYPE (Codex P2,
--    runde 1). Første form utvidet det DELTE kildedomenet i
--    `akseptkrav_punkt` med `delt_maaling`. Domenet er felles med
--    deployment-grensene, og der er typen en blindvei: `modulaksept_punkt`
--    (049) avviser den, og `aksepter_moduldeployment` ruter alt utenfor
--    `evidensfil`/`ci_kjoring`/`artefakt` til `registerhendelse`. Et krav
--    registrert med den nye typen kunne altså aldri gi en
--    deployment-aksept — en registrerbar tilstand som ikke kan måles er
--    ingen utvidelse, den er en felle.
--
--    Typen er derfor borte, og domenet står som før. De delte målingene
--    er `artefakt` — som de andre — og delingsregelen (to punkter kan
--    dele en MÅLING, aldri en TYPE måling) håndheves der den hører
--    hjemme: `kilde_ref` må referere ved hash, og punktet måles mot
--    evidensattesten for NØYAKTIG de bytene. Hvilket punkts artefakt
--    målingen eies av, står i `grenseverdi`.
-- ------------------------------------------------------------

-- Sentinelen `<utenfor grensen>` i maalt_krav er registerets
-- FORHÅNDSGODKJENNING av skoping: bare punkter med den kan skrives som
-- `utenfor_grensen`, og de kan aldri skrives som målt.
--
-- IDENTITETEN MÅLES, DEN PÅSTÅS IKKE (Codex P1, runde 1). `manifest_sha`
-- var bare formkontrollert: en kaller med gyldig CI-attest kunne oppgi
-- hvilken som helst velformet 64-hex, og den immutable raden og
-- registerhendelsen påsto da en innholdsadressert identitet hvis digest
-- ikke hadde noe med manifestet å gjøre — SP-12-invarianten var en
-- påstand, ikke en måling. Grensen bærer derfor et IDENTITETSPUNKT med
-- sin egen sentinel: `<manifestets sha256>` betyr «den grønne verdien
-- for dette punktet ER akseptens manifest-digest». Punktet måles som de
-- andre artefaktpunktene — mot `evidensfil_attest` — og i tillegg må de
-- ATTESTERTE bytene være nøyaktig manifestets. Digesten er dermed noe en
-- annen autentisert identitet har lest, ikke noe kallet fant på.
INSERT INTO akseptkrav_punkt (krav_id, punkt, kilde_type, grenseverdi,
                              maalt_krav) VALUES
    ('m02-aksept-v1', 'manifest_identitet', 'artefakt',
     'manifestet ved akseptcommiten, lest og hashet utenfor aksepten;'
     ' digesten aksepten bærer er DE bytene (m02-manifest-v1)',
     '<manifestets sha256>'),
    ('m02-aksept-v1', 'feilinjisering_til_unntakskø', 'artefakt',
     'historikk_komplett=true og klartekst_payload_funnet=false'
     ' (feilinjisering-m01-v1)',
     'historikk_komplett=true, klartekst=0'),
    ('m02-aksept-v1', 'ytelse_bestatt', 'artefakt',
     '6000 auditerte svar -> 6000 loggposter, en_til_en (perf-m01-v1)',
     '6000/6000 en_til_en'),
    ('m02-aksept-v1', 'rollback_testet', 'artefakt',
     'tapte_loggposter=0 og identisk radtelling gjennom av-vinduet'
     ' (rollback-m01-v1; service-rollback, ikke moduldrill)',
     'tapte_loggposter=0, radtelling identisk'),
    -- Tredelt binding (klarsignalet §2): den delte r21-målingen bærer
    -- rad-per-hendelse-leddet og refereres ved artefaktets sha; de to
    -- CI-leddene (aktør fra kontekst; append-only i basen) bæres av
    -- grensens egen CI-attest — én rød porttest gjør kjøringen rød og
    -- attesten umulig.
    ('m02-aksept-v1', 'revisjonslogg_korrekt', 'artefakt',
     '10/10 revisjonsrader mot bestilt, 0 avvik (wcag-kontroll-v2,'
     ' r21) + CI-leddene aktør-fra-kontekst og append-only grønne',
     '10/10 rader, 0 avvik'),
    ('m02-aksept-v1', 'tester_gronne_pa_staging', 'artefakt',
     'suitekjøring på staging som artefakt; M-2s andel navngitt'
     ' (m02-suite-v1)', 'suite grønn, m2-andel grønn'),
    ('m02-aksept-v1', 'syntetisk_datasett_likt_lokalt', 'artefakt',
     'fordelingen 84/3/93 over 180 hendelser re-målbar av artefaktet'
     ' (m02-fordeling-v1)', '84/3/93 av 180, re-målt'),
    ('m02-aksept-v1', 'moduldrill_boot', 'artefakt',
     'utenfor grensen: m02 har ingen bootbar enhet — ingen modulhode-,'
     ' release- eller deploymentrad; koden kjører i m01s prosess.'
     ' Service-rollbacken er målt i rollback_testet',
     '<utenfor grensen>'),
    ('m02-aksept-v1', 'flate_axe_tastatur', 'artefakt',
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
    -- …og den er en PLATTFORM-grense (Codex P1, runde 2). CHECK-låst,
    -- som deployment-sidens kolonne: den finnes for å bære FK-en. Uten
    -- den kunne søsterfunksjonen speilvendt samme feil og aksepteres mot
    -- en deployment-grense.
    grense_klasse   TEXT NOT NULL DEFAULT 'plattform'
                    CHECK (grense_klasse = 'plattform'),
    ci_run          TEXT NOT NULL,
    ci_commit       TEXT NOT NULL,
    nokkel          TEXT NOT NULL UNIQUE,
    aktor           TEXT NOT NULL,
    akseptert_ts    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (modul_id, manifest_commit),
    UNIQUE (modul_id, manifest_commit, grense_id),
    FOREIGN KEY (grense_id, grense_klasse)
        REFERENCES akseptgrense_klasse (krav_id, klasse)
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
    kilde_type      TEXT CHECK (kilde_type IN ('artefakt', 'ci_kjoring')),
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
                                         grense_id),
    -- PUNKTET STÅR I GRENSEN — PÅ ENHVER SKRIVEVEI (Codex P2, runde 2).
    -- Funksjonen avviser punkter utenfor registeret, men funksjonen er
    -- ikke den eneste veien inn: eier- og migratorveien har INSERT på
    -- tabellen (det er nettopp den veien konstruksjonstesten måler), og
    -- et komplett-utseende punkt som aldri sto i grensen blir permanent
    -- i det append-only-triggeren har sett det. `modulaksept_punkt`
    -- bærer bindingen som FK i 049; søstertabellen gjorde det ikke, og
    -- en invariant som bare finnes i en funksjon, er ingen skranke for
    -- den som skriver forbi den.
    FOREIGN KEY (grense_id, punkt)
        REFERENCES akseptkrav_punkt (krav_id, punkt)
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
        v_evidens RECORD; v_kilde_sha TEXT; v_krav_verdi TEXT;
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
             -- HELE observasjonen, ikke de fleste feltene (Codex P2,
             -- runde 1). `grenseverdi` og `kilde_type` sto utenfor
             -- sammenligningen, og replayet returnerer FØR den vanlige
             -- registerkontrollen: et retry som byttet grense eller
             -- kildeform fikk dermed «vellykket» tilbake på en
             -- observasjon basen aldri lagret — og en kaller som
             -- korrigerte nettopp de feltene, trodde rettelsen sto.
             OR (p_punkter -> pk.punkt) ->> 'grenseverdi'
                    IS DISTINCT FROM pk.grenseverdi
             OR (p_punkter -> pk.punkt) ->> 'maalt_verdi'
                    IS DISTINCT FROM pk.maalt_verdi
             OR (p_punkter -> pk.punkt) ->> 'kilde_type'
                    IS DISTINCT FROM pk.kilde_type
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
        -- …OG SAMMENLIGNINGEN GÅR BEGGE VEIER (Codex P2, runde 2).
        -- Kontrollen over drives av de LAGREDE radene, så den ser bare
        -- det basen alt har: et retry som la til et toppnivåpunkt fant
        -- hver eneste lagrede rad uendret og fikk «vellykket» tilbake.
        -- Den vanlige veien avviser nettopp det punktet noen linjer
        -- lenger nede («står ikke i grensen»), men replayet returnerer
        -- FØR den kontrollen — så en nøkkel som var brukt én gang, gjorde
        -- en ellers ugyldig påstand til et vellykket kall, uten at noe
        -- av den ble lagret.
        SELECT j.key INTO v_avvik FROM pg_catalog.jsonb_each(p_punkter) j
         WHERE NOT EXISTS (
               SELECT 1 FROM public.plattformmodulaksept_punkt pk
                WHERE pk.modul_id = p_modul_id
                  AND pk.manifest_commit = lower(p_manifest_commit)
                  AND pk.grense_id = p_grense_id
                  AND pk.punkt = j.key)
         LIMIT 1;
        IF v_avvik IS NOT NULL THEN
            RAISE EXCEPTION 'aksepter_plattformmodul: nøkkel % gjenbrukt'
                ' med punktet %, som aldri ble lagret — et replay er den'
                ' SAMME observasjonen, ikke den samme pluss én',
                p_nokkel, v_avvik
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
        -- Registerets andre sentinel: for IDENTITETSPUNKTET er den
        -- grønne verdien manifestets digest — den står ikke i registeret,
        -- for registeret navngir kravet, ikke den enkelte aksepten.
        -- Ellers er kravet registerets literal, som før.
        v_krav_verdi := CASE
            WHEN v_punkt.maalt_krav = '<manifestets sha256>'
            THEN lower(p_manifest_sha) ELSE v_punkt.maalt_krav END;
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
        IF v_verdi ->> 'maalt_verdi' IS DISTINCT FROM v_krav_verdi THEN
            RAISE EXCEPTION 'aksepter_plattformmodul: punkt % målte «%»,'
                ' men en grønn observasjon er «%» — en aksept skrives av'
                ' målinger som oppfyller kravet', v_punkt.punkt,
                v_verdi ->> 'maalt_verdi', v_krav_verdi
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        -- Kilden er en PEKER som holder, aldri en fortelling:
        -- `artefakt` refereres VED HASH (delingsregelen — to punkter kan
        -- dele en måling, aldri en type måling), `ci_kjoring` navngir
        -- nøyaktig akseptens egen kjøring.
        IF v_punkt.kilde_type = 'artefakt' THEN
            IF v_ref !~ '@sha256:[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'aksepter_plattformmodul: punkt % — «%»'
                    ' refererer ikke ved hash («sti@sha256:<64 hex>»);'
                    ' en delt måling uten sha er en beskrivelse, ikke en'
                    ' referanse', v_punkt.punkt, v_ref
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
            -- …MEN FORMEN ER INGEN MÅLING (Codex P1, runde 1). Med
            -- gyldig CI-attest kunne en `disponit_modules_admin`-kaller
            -- lese registerets grønne `maalt_krav` rett ut av
            -- `akseptkrav_punkt`, gjenta dem, og feste en oppdiktet
            -- `oppdiktet@sha256:<64 hex>` til hvert punkt: alt basen så
            -- var at strengen hadde riktig hale. Aksepten ble immutabel
            -- uten at ÉN av de refererte målingene fantes — nøyaktig
            -- hullet `049`/`053` lukket for deployment-veien.
            --
            -- Punktet måles derfor mot REFERATET, som der: en immutabel
            -- `evidensfil_attest`-rad, skrevet av veien som faktisk
            -- hashet artefaktet (`attester_evidensfil` har EXECUTE bare
            -- til `disponit_ci_verifikator`), som sier hvilken sti
            -- bytene lå på og hva de bar for NØYAKTIG dette punktet.
            -- Kalleren kan bare gjenta den.
            --
            -- Attesten slås opp uten drill (`drill_sha256 = ''`): en
            -- plattformmodul har ingen moduldrill å regne målingen mot,
            -- så lesningen er filens egen. Delingen faller ut av
            -- nøkkelen: samme bytes attestert under to punkter er to
            -- rader om én fil, og `attester_evidensfil` hører selv at de
            -- to sier det samme.
            v_kilde_sha := substring(v_ref from '@sha256:([0-9a-f]{64})$');
            -- Identitetspunktet peker på MANIFESTET og ingenting annet:
            -- uten dette leddet kunne en attest om en hvilken som helst
            -- annen fil bære punktet, og digesten i den immutable raden
            -- ville fortsatt vært kallerens påstand.
            IF v_punkt.maalt_krav = '<manifestets sha256>'
               AND v_kilde_sha IS DISTINCT FROM lower(p_manifest_sha) THEN
                RAISE EXCEPTION 'aksepter_plattformmodul: punkt % skal'
                    ' navngi manifestets egne bytes (sha256:%), men viser'
                    ' til sha256:% — identiteten er innholdet, og da må'
                    ' det være innholdet som er lest',
                    v_punkt.punkt, lower(p_manifest_sha), v_kilde_sha
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
            SELECT * INTO v_evidens FROM public.evidensfil_attest e
             WHERE e.sha256 = v_kilde_sha AND e.punkt = v_punkt.punkt
               AND e.krav_id = p_grense_id AND e.drill_sha256 = '';
            IF NOT FOUND THEN
                RAISE EXCEPTION 'aksepter_plattformmodul: punkt % hviler'
                    ' på sha256:%, men ingen attest sier at de bytene er'
                    ' lest og hva de bar for punktet — en hash aksepten'
                    ' selv oppgir, beviser ingenting; det gjør referatet'
                    ' fra veien som leste', v_punkt.punkt, v_kilde_sha
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
            IF v_evidens.attestert_av = session_user THEN
                RAISE EXCEPTION 'aksepter_plattformmodul: punkt % hviler'
                    ' på en evidensattest skrevet av % — samme innlogging'
                    ' som skriver aksepten. Den som leste målingen og den'
                    ' som aksepterer på det den bar, skal være to'
                    ' autentiserte identiteter', v_punkt.punkt,
                    v_evidens.attestert_av
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
            -- Stien er attestens, ikke kallerens: LIKHET, ikke hale.
            IF v_ref IS DISTINCT FROM
               (v_evidens.sti || '@sha256:' || v_kilde_sha) THEN
                RAISE EXCEPTION 'aksepter_plattformmodul: punkt % viser'
                    ' til «%», mens attesten leste «%@sha256:%» — en'
                    ' observasjon skal navngi DEN målingen som ble lest,'
                    ' ikke en sti med riktig hale', v_punkt.punkt, v_ref,
                    v_evidens.sti, v_kilde_sha
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
            -- …og det er MÅLINGENS tall som skal oppfylle kravet.
            IF v_evidens.maalt_verdi IS DISTINCT FROM v_krav_verdi THEN
                RAISE EXCEPTION 'aksepter_plattformmodul: punkt % —'
                    ' sha256:% bar «%», men en grønn observasjon er «%».'
                    ' Aksepten regner mot det målingen SA, ikke mot det'
                    ' kallet gjentar', v_punkt.punkt, v_kilde_sha,
                    v_evidens.maalt_verdi, v_krav_verdi
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
                v_krav_verdi, v_punkt.kilde_type, v_ref);
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
