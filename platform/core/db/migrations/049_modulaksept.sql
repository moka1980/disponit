-- 049 — modulaksept: aksept er en bevisbåren hendelse, ikke en status
-- noen setter (m56-akseptflipp-klarsignalet, A1–A3; policyaktivering-
-- mønsteret: registerets påstand er CHECK-bundet til en immutabel
-- hendelse som FK-refererer bevisene).
--
-- ⚠️ DOKUMENTERT AVVIK FRA KLARSIGNALET (livsløpsrealiteten):
-- klarsignalet skisserte drillen som «rull r5 → forrige release →
-- tilbake til r5» og aksept av (staging, wcag-r5). Det er strukturelt
-- umulig: moduldeployment-livsløpet er ENVEIS (claiming → draining →
-- retired, trigger `deployment_livslop` i 014), og `bytt_release`
-- nekter eksplisitt å re-claime en drenert release («ny release
-- kreves») — regelen står til og med i sjekklisteskriptets egne
-- kommentarer. Lesesvarets «r5→r4→r5 booter» målte bytene, ikke
-- livsløpet, og var feil. En drill KONSUMERER derfor nødvendigvis
-- releasen den ruller tilbake: tilbake-rullingen drenerer den drillede
-- deploymenten permanent, og «fram igjen» lander på en NY deployment
-- med de samme bytene — AKSEPTKANDIDATEN. Prinsippet i klarsignalet
-- («aksepten binder deploymentraden slik den faktisk kjører») bevares
-- ved at aksepten binder KANDIDATEN — raden som faktisk kjører etter
-- drillen, hvis fødsel og claim-opptak drillen selv bevitnet — og
-- A1-disiplinen holdes av digestlikhets-porten i registreringen:
-- kandidatens bytes SKAL være de drillede bytene. (At alle m56-releaser
-- deler digest er nettopp A1s levende bevis: digest kunne ikke skilt
-- drillet fra udrillet — bare deployment-identiteten kan.)

-- ------------------------------------------------------------
-- 1. Drillen: egen smal, immutabel tabell (lesesvar 2: detalj-jsonb i
--    modulregister_hendelse har ingen skjemahåndheving; en rad som skal
--    FK-refereres og bære kontrollpunktutfall fortjener kolonner).
--    Kvalifikasjonen (tre grønne kontrollpunkter) står I den
--    refererbare nøkkelen (E1f-formen): append-only gjør den varig,
--    så SP-9 holder i begge ledd.
-- ------------------------------------------------------------
CREATE TABLE moduldrill (
    drill_id       BIGINT GENERATED ALWAYS AS IDENTITY,
    modul_id       TEXT NOT NULL,
    miljo          TEXT NOT NULL,
    -- releasen som VAR claiming og ble rullet tilbake (drenert av drillen)
    drillet_release TEXT NOT NULL,
    -- releasen det ble rullet TILBAKE til (forrige-bytes-releasen)
    rullback_release TEXT NOT NULL,
    -- releasen «fram igjen» landet på — byte-identisk med den drillede
    -- (digestporten i registrer_moduldrill), og raden aksepten binder
    akseptkandidat_release TEXT NOT NULL,
    -- fencing-konteksten drillen målte i: module_epoch er
    -- registertilstand, ikke FK-bar identitet — den snapshottes (A1)
    epoch_snapshot  BIGINT NOT NULL,
    digest_snapshot TEXT NOT NULL,
    claim_stopp_ok  BOOLEAN NOT NULL,  -- (a) drenert release claimer ikke nye
    rene_utfall_ok  BOOLEAN NOT NULL,  -- (b) løpende oppdrag: rent utfall (SP-3)
    tilbake_ok      BOOLEAN NOT NULL,  -- (c) kandidaten plukker og fullfører
    nokkel          TEXT NOT NULL,     -- SP-2: replay-nøkkel
    aktor           TEXT NOT NULL,
    -- Codex' P2 på PR #117 (runde 3): `utfort_ts` sto med DEFAULT now()
    -- og ble aldri gitt en verdi, så en drill som ble kjørt timer eller
    -- dager før aksepten ble innskrevet som om den kjørte i
    -- akseptøyeblikket. Da kan ingen ferskhetskontroll skille UTFØRELSE
    -- fra senere REGISTRERING, og det immutable sporet er feil om det
    -- ene faktumet ingen kan rekonstruere i ettertid. De to
    -- tidspunktene er ulike fakta og har derfor hver sin kolonne:
    -- `utfort_ts` er drillartefaktets egen `ts` (målingen), og
    -- `registrert_ts` er innskrivingen.
    utfort_ts       TIMESTAMPTZ NOT NULL,
    registrert_ts   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- En drill kan ikke ha kjørt etter at den ble registrert.
    CHECK (utfort_ts <= registrert_ts),
    PRIMARY KEY (modul_id, drill_id),
    UNIQUE (nokkel),
    CHECK (drillet_release <> rullback_release),
    CHECK (akseptkandidat_release <> drillet_release),
    FOREIGN KEY (modul_id, miljo, drillet_release)
        REFERENCES moduldeployment (modul_id, miljo, release_id),
    FOREIGN KEY (modul_id, miljo, akseptkandidat_release)
        REFERENCES moduldeployment (modul_id, miljo, release_id),
    FOREIGN KEY (modul_id, rullback_release)
        REFERENCES modulrelease (modul_id, release_id),
    -- den refererbare nøkkelen for aksepthendelsen: drill FOR nøyaktig
    -- denne deploymentraden, med utfallene i nøkkelen
    UNIQUE (modul_id, miljo, akseptkandidat_release, drill_id,
            claim_stopp_ok, rene_utfall_ok, tilbake_ok)
);
CREATE TRIGGER drill_immutable BEFORE UPDATE OR DELETE ON moduldrill
    FOR EACH ROW EXECUTE FUNCTION modulregister_append_only();
CREATE TRIGGER drill_ingen_truncate BEFORE TRUNCATE ON moduldrill
    FOR EACH STATEMENT EXECUTE FUNCTION modulregister_append_only();

-- ------------------------------------------------------------
-- 2. A2: artefaktets releasesnapshot blir relasjonelt. Snapshotten er
--    alt kapabilitets-attestert ved skriving (017: verdiene kopieres fra
--    kapabilitetsraden, aldri fra kalleren) og modulrelease-PK-en kan
--    aldri gjenbrukes — FK-en gjør kjeden mekanisk etterprøvbar også.
--    Refererbar nøkkel med tilstanden I identiteten (E1f):
--    resultatlåsen (017) gjør 'promotert' varig.
-- ------------------------------------------------------------
ALTER TABLE artefakt ADD CONSTRAINT artefakt_release_fk
    FOREIGN KEY (modul_id, release_id)
    REFERENCES modulrelease (modul_id, release_id);
ALTER TABLE artefakt ADD CONSTRAINT artefakt_refererbar
    UNIQUE (tenant, artefakt_id, modul_id, release_id, tilstand);

-- ------------------------------------------------------------
-- 3. Kravpunkt-registeret: hva et KOMPLETT punktsett ER, står i
--    lagringen — akseptfunksjonen måler mot dette, ikke mot en liste i
--    kallerens hode (port 5). Punktene er evidensgrensen
--    `wcag-kontroll-v1` fra 014c-klarsignalet §12, ordrett.
-- ------------------------------------------------------------
CREATE TABLE akseptkrav_punkt (
    krav_id TEXT NOT NULL,
    punkt   TEXT NOT NULL,
    PRIMARY KEY (krav_id, punkt)
);
INSERT INTO akseptkrav_punkt (krav_id, punkt) VALUES
    ('wcag-kontroll-v1', 'kontroll.ti_kjoringer_signert_innen_frist'),
    ('wcag-kontroll-v1', 'funn.avvik_mot_fasit'),
    ('wcag-kontroll-v1', 'skjema.brudd_promotert'),
    ('wcag-kontroll-v1', 'skjema.hash_uten_rad_akseptert'),
    ('wcag-kontroll-v1', 'skjema.mutert_ureferert'),
    ('wcag-kontroll-v1', 'skjema.slettet'),
    ('wcag-kontroll-v1', 'rapport.uten_pakrevd_arlighetsfelt_akseptert'),
    ('wcag-kontroll-v1', 'rapport.klartekst_i_logg_eller_dump'),
    ('wcag-kontroll-v1', 'domene.kontroll_uten_verifisering'),
    ('wcag-kontroll-v1', 'payload.felt_utover_skjema_utlevert'),
    ('wcag-kontroll-v1', 'robots.brudd_i_mallogg'),
    ('wcag-kontroll-v1', 'frekvens.over_grense_utfort'),
    ('wcag-kontroll-v1', 'deploy.registerrad_uten_kodefestet_type'),
    ('wcag-kontroll-v1', 'deploy.ekstern_lesing_uten_malautorisasjonsflagg'),
    ('wcag-kontroll-v1', 'klasse.eksisterende_kontrakt_omklassifisert'),
    ('wcag-kontroll-v1', 'klasse.aktivering_uten_frekvensgrense_lyktes'),
    ('wcag-kontroll-v1', 'klasse.aktivering_uten_malautorisasjon_lyktes'),
    ('wcag-kontroll-v1', 'egress.proxytoken_til_ikke_ekstern_lesing'),
    ('wcag-kontroll-v1', 'malautorisasjon.ikke_registrert_vilkar_talte'),
    ('wcag-kontroll-v1', 'malautorisasjon.feil_maldomene_godtatt'),
    ('wcag-kontroll-v1', 'malautorisasjon.positiv_sti_virker');

-- ------------------------------------------------------------
-- 4. Aksepthendelsen. Én per deploymentrad (PK) — port 14: hendelsen
--    for (staging, X) autoriserer aldri (produksjon, X); et reelt
--    produksjonsmiljø krever egen aksept med egen drill.
--    Drill-kvalifikasjonen bæres av FK-en med utfallene i nøkkelen
--    (kolonnene her er CHECK-låst true — de finnes for å bære FK-en,
--    ikke for å kunne variere).
-- ------------------------------------------------------------
CREATE TABLE modulaksept (
    modul_id   TEXT NOT NULL,
    miljo      TEXT NOT NULL,
    release_id TEXT NOT NULL,
    drill_id   BIGINT NOT NULL,
    drill_claim_stopp BOOLEAN NOT NULL DEFAULT true CHECK (drill_claim_stopp),
    drill_rene_utfall BOOLEAN NOT NULL DEFAULT true CHECK (drill_rene_utfall),
    drill_tilbake     BOOLEAN NOT NULL DEFAULT true CHECK (drill_tilbake),
    krav_id    TEXT NOT NULL,
    e2e_tenant TEXT NOT NULL,
    e2e_artefakt_id UUID NOT NULL,
    e2e_tilstand TEXT NOT NULL DEFAULT 'promotert'
        CHECK (e2e_tilstand = 'promotert'),
    evidens_jsonl_sha256 TEXT NOT NULL,  -- SP-11: den INNSJEKKEDE filen
    manifest_commit TEXT NOT NULL,
    ci_run     TEXT NOT NULL,
    ci_commit  TEXT NOT NULL,
    nokkel     TEXT NOT NULL,            -- SP-2: replay-nøkkel
    aktor      TEXT NOT NULL,
    akseptert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (modul_id, miljo, release_id),
    UNIQUE (nokkel),
    FOREIGN KEY (modul_id, miljo, release_id)
        REFERENCES moduldeployment (modul_id, miljo, release_id),
    -- A1: drillen gjelder NØYAKTIG denne deploymentraden, og alle tre
    -- kontrollpunktene var grønne (utfallene står i den refererte nøkkelen)
    FOREIGN KEY (modul_id, miljo, release_id, drill_id,
                 drill_claim_stopp, drill_rene_utfall, drill_tilbake)
        REFERENCES moduldrill (modul_id, miljo, akseptkandidat_release,
                               drill_id, claim_stopp_ok, rene_utfall_ok,
                               tilbake_ok),
    -- A2: E2E-artefaktet er promotert OG produsert av samme release —
    -- delt release_id-kolonne bærer båndet (E1e-formen), tilstanden står
    -- i nøkkelen (E1f-formen)
    FOREIGN KEY (e2e_tenant, e2e_artefakt_id, modul_id, release_id,
                 e2e_tilstand)
        REFERENCES artefakt (tenant, artefakt_id, modul_id, release_id,
                             tilstand)
);
CREATE TRIGGER aksept_immutable BEFORE UPDATE OR DELETE ON modulaksept
    FOR EACH ROW EXECUTE FUNCTION modulregister_append_only();
CREATE TRIGGER aksept_ingen_truncate BEFORE TRUNCATE ON modulaksept
    FOR EACH STATEMENT EXECUTE FUNCTION modulregister_append_only();

-- ------------------------------------------------------------
-- 5. A3: én immutabel observasjon per grensepunkt — referansen til
--    beviset, aldri en kopi av konklusjonen (SP-§3). FK-en mot
--    kravpunkt-registeret gjør «komplett» målbart; akseptfunksjonen
--    håndhever at HELE settet skrives i samme transaksjon.
-- ------------------------------------------------------------
CREATE TABLE modulaksept_punkt (
    modul_id   TEXT NOT NULL,
    miljo      TEXT NOT NULL,
    release_id TEXT NOT NULL,
    krav_id    TEXT NOT NULL,
    punkt      TEXT NOT NULL,
    grenseverdi TEXT NOT NULL,
    maalt_verdi TEXT NOT NULL,
    kilde_type TEXT NOT NULL CHECK (kilde_type IN
        ('artefakt', 'registerhendelse', 'evidensfil', 'ci_kjoring')),
    kilde_ref  TEXT NOT NULL,
    PRIMARY KEY (modul_id, miljo, release_id, punkt),
    FOREIGN KEY (modul_id, miljo, release_id) REFERENCES modulaksept,
    FOREIGN KEY (krav_id, punkt) REFERENCES akseptkrav_punkt
);
CREATE TRIGGER akseptpunkt_immutable
    BEFORE UPDATE OR DELETE ON modulaksept_punkt
    FOR EACH ROW EXECUTE FUNCTION modulregister_append_only();
CREATE TRIGGER akseptpunkt_ingen_truncate BEFORE TRUNCATE ON modulaksept_punkt
    FOR EACH STATEMENT EXECUTE FUNCTION modulregister_append_only();

-- ------------------------------------------------------------
-- 6. Funksjonene — modul_eier-eide definere, EXECUTE kun til
--    disponit_modules_admin (014-mønsteret). INSERT på tabellene er
--    eierens/migrators særrettighet; ingen andre roller får DML.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION registrer_moduldrill(
    p_modul_id TEXT, p_miljo TEXT, p_drillet TEXT, p_rullback TEXT,
    p_kandidat TEXT, p_claim_stopp BOOLEAN, p_rene_utfall BOOLEAN,
    p_tilbake BOOLEAN, p_nokkel TEXT, p_aktor TEXT,
    p_utfort_ts TIMESTAMPTZ)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id BIGINT; v_drillet_digest TEXT; v_kandidat_digest TEXT;
        v_epoch BIGINT; v_livslop TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('modul:' || p_modul_id, 0));
    -- SP-2: samme nøkkel → samme rad, aldri to. Avvikende innhold på
    -- samme nøkkel er en programfeil og skal høres.
    SELECT drill_id INTO v_id FROM public.moduldrill WHERE nokkel = p_nokkel;
    IF FOUND THEN
        PERFORM 1 FROM public.moduldrill
         WHERE nokkel = p_nokkel AND modul_id = p_modul_id
           AND miljo = p_miljo AND drillet_release = p_drillet
           AND rullback_release = p_rullback
           AND akseptkandidat_release = p_kandidat
           AND claim_stopp_ok = p_claim_stopp
           AND rene_utfall_ok = p_rene_utfall
           AND tilbake_ok = p_tilbake
           -- Måletidspunktet er like materielt som utfallene: samme
           -- nøkkel med en ANNEN drillkjørings tidsstempel er to
           -- kjøringer, ikke en replay.
           AND utfort_ts = p_utfort_ts;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'registrer_moduldrill: nøkkel % gjenbrukt med'
                ' annet innhold', p_nokkel
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN v_id;
    END IF;
    -- Tilstandene drillen ETTERLATER: den drillede er drenert (rullingen
    -- konsumerte den — livsløpet er enveis), kandidaten er den som
    -- faktisk kjører. Kandidat claiming er også akseptens forutsetning.
    SELECT livslop INTO v_livslop FROM public.moduldeployment
     WHERE modul_id = p_modul_id AND miljo = p_miljo
       AND release_id = p_drillet;
    IF v_livslop IS DISTINCT FROM 'draining' THEN
        RAISE EXCEPTION 'registrer_moduldrill: drillet release %/% er %,'
            ' ventet draining (drillen skal ha konsumert den)',
            p_modul_id, p_drillet, coalesce(v_livslop, '<mangler>')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT livslop INTO v_livslop FROM public.moduldeployment
     WHERE modul_id = p_modul_id AND miljo = p_miljo
       AND release_id = p_kandidat;
    IF v_livslop IS DISTINCT FROM 'claiming' THEN
        RAISE EXCEPTION 'registrer_moduldrill: kandidat %/% er %, ventet'
            ' claiming (aksepten binder raden som faktisk kjører)',
            p_modul_id, p_kandidat, coalesce(v_livslop, '<mangler>')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- A1-digestporten: kandidatens bytes SKAL være de drillede bytene.
    SELECT artifact_digest INTO v_drillet_digest FROM public.modulrelease
     WHERE modul_id = p_modul_id AND release_id = p_drillet;
    SELECT artifact_digest INTO v_kandidat_digest FROM public.modulrelease
     WHERE modul_id = p_modul_id AND release_id = p_kandidat;
    IF v_drillet_digest IS DISTINCT FROM v_kandidat_digest THEN
        RAISE EXCEPTION 'registrer_moduldrill: kandidatens digest (%) er'
            ' ikke den drillede (%) — aksepterte bytes må være drillede'
            ' bytes', left(v_kandidat_digest, 12), left(v_drillet_digest, 12)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT module_epoch INTO v_epoch FROM public.modulhode
     WHERE modul_id = p_modul_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'registrer_moduldrill: ukjent modul %', p_modul_id
            USING ERRCODE = 'no_data_found';
    END IF;
    -- Drillen ble utført FØR den ble registrert. Et tidsstempel fram i
    -- tid er ikke en måling, det er en påstand om framtiden — og
    -- CHECK-en under ville uansett stoppet raden; her får den et navn.
    IF p_utfort_ts IS NULL OR p_utfort_ts > now() THEN
        RAISE EXCEPTION 'registrer_moduldrill: utført-tidspunktet % er'
            ' tomt eller fram i tid — drillen skal bære sin EGEN måletid',
            p_utfort_ts USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.moduldrill (modul_id, miljo, drillet_release,
        rullback_release, akseptkandidat_release, epoch_snapshot,
        digest_snapshot, claim_stopp_ok, rene_utfall_ok, tilbake_ok,
        nokkel, aktor, utfort_ts)
    VALUES (p_modul_id, p_miljo, p_drillet, p_rullback, p_kandidat,
            v_epoch, v_kandidat_digest, p_claim_stopp, p_rene_utfall,
            p_tilbake, p_nokkel, p_aktor, p_utfort_ts)
    RETURNING drill_id INTO v_id;
    INSERT INTO public.modulregister_hendelse (modul_id, hendelse,
        release_id, miljo, module_epoch, aktor, detalj)
    VALUES (p_modul_id, 'rollback_drill', p_kandidat, p_miljo, v_epoch,
            p_aktor, jsonb_build_object(
                'drill_id', v_id, 'drillet', p_drillet,
                'rullback', p_rullback,
                'claim_stopp_ok', p_claim_stopp,
                'rene_utfall_ok', p_rene_utfall,
                'tilbake_ok', p_tilbake,
                'utfort_ts', p_utfort_ts));
    RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION aksepter_moduldeployment(
    p_modul_id TEXT, p_miljo TEXT, p_release_id TEXT, p_drill_id BIGINT,
    p_krav_id TEXT, p_e2e_tenant TEXT, p_e2e_artefakt UUID,
    p_evidens_sha TEXT, p_manifest_commit TEXT, p_ci_run TEXT,
    p_ci_commit TEXT, p_punkter JSONB, p_nokkel TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_livslop TEXT; v_mangler TEXT; v_punkt RECORD; v_verdi JSONB;
        v_epoch BIGINT; v_avvik TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('modul:' || p_modul_id, 0));
    -- SP-2: replay er et no-op, aldri en ny hendelse — men BARE når hele
    -- det materielle innholdet er det samme.
    --
    -- Codex' P2 på PR #117: den forrige formen returnerte på nøkkelen
    -- alene. Kjørte operatøren akseptkommandoen på nytt etter å ha
    -- rettet en CI-kjøring, evidenshash, drill eller E2E-artefakt, ble
    -- rettelsen STILLE forkastet — raden er immutabel, så den bar
    -- fortsatt de gamle bevisene — og skriptet skrev likevel AKSEPTERT.
    -- Revisjonssporet fortalte da noe annet enn kallet som lagde det.
    -- Avvikende gjenbruk av en nøkkel er en programfeil og skal høres,
    -- akkurat som i `registrer_moduldrill`.
    PERFORM 1 FROM public.modulaksept WHERE nokkel = p_nokkel;
    IF FOUND THEN
        PERFORM 1 FROM public.modulaksept
         WHERE nokkel = p_nokkel AND modul_id = p_modul_id
           AND miljo = p_miljo AND release_id = p_release_id
           AND drill_id = p_drill_id AND krav_id = p_krav_id
           AND e2e_tenant = p_e2e_tenant
           AND e2e_artefakt_id = p_e2e_artefakt
           AND evidens_jsonl_sha256 = p_evidens_sha
           AND manifest_commit = p_manifest_commit
           AND ci_run = p_ci_run AND ci_commit = p_ci_commit;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'aksepter_moduldeployment: nøkkel % gjenbrukt'
                ' med annet innhold — den lagrede aksepten bærer andre'
                ' bevis enn dette kallet', p_nokkel
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        -- Punktobservasjonene er like materielle som radens egne felt:
        -- en rettet måling på samme nøkkel er også en forkastet rettelse.
        SELECT string_agg(pk.punkt, ', ' ORDER BY pk.punkt) INTO v_avvik
          FROM public.modulaksept_punkt pk
         WHERE pk.modul_id = p_modul_id AND pk.miljo = p_miljo
           AND pk.release_id = p_release_id
           AND ((p_punkter -> pk.punkt) ->> 'grenseverdi'
                    IS DISTINCT FROM pk.grenseverdi
             OR (p_punkter -> pk.punkt) ->> 'maalt_verdi'
                    IS DISTINCT FROM pk.maalt_verdi
             OR (p_punkter -> pk.punkt) ->> 'kilde_type'
                    IS DISTINCT FROM pk.kilde_type
             OR (p_punkter -> pk.punkt) ->> 'kilde_ref'
                    IS DISTINCT FROM pk.kilde_ref);
        IF v_avvik IS NOT NULL THEN
            RAISE EXCEPTION 'aksepter_moduldeployment: nøkkel % gjenbrukt'
                ' med andre punktobservasjoner: %', p_nokkel, v_avvik
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN;
    END IF;
    -- Aksepten gjelder raden slik den faktisk kjører.
    SELECT livslop INTO v_livslop FROM public.moduldeployment
     WHERE modul_id = p_modul_id AND miljo = p_miljo
       AND release_id = p_release_id;
    IF v_livslop IS DISTINCT FROM 'claiming' THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: %/%/% er % — aksepten'
            ' binder deploymentraden slik den faktisk kjører (claiming)',
            p_modul_id, p_miljo, p_release_id,
            coalesce(v_livslop, '<mangler>')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Port 5: KOMPLETT punktsett, målt mot kravpunkt-registeret — ikke
    -- mot kallerens liste. Hvert punkt må bære alle fire feltene.
    SELECT string_agg(k.punkt, ', ') INTO v_mangler
      FROM public.akseptkrav_punkt k
     WHERE k.krav_id = p_krav_id AND NOT (p_punkter ? k.punkt);
    IF v_mangler IS NOT NULL THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: ufullstendig punktsett'
            ' — mangler: %', v_mangler
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.akseptkrav_punkt
                    WHERE krav_id = p_krav_id) THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: ukjent krav %', p_krav_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.modulaksept (modul_id, miljo, release_id, drill_id,
        krav_id, e2e_tenant, e2e_artefakt_id, evidens_jsonl_sha256,
        manifest_commit, ci_run, ci_commit, nokkel, aktor)
    VALUES (p_modul_id, p_miljo, p_release_id, p_drill_id, p_krav_id,
            p_e2e_tenant, p_e2e_artefakt, p_evidens_sha, p_manifest_commit,
            p_ci_run, p_ci_commit, p_nokkel, p_aktor);
    FOR v_punkt IN SELECT k.punkt FROM public.akseptkrav_punkt k
                    WHERE k.krav_id = p_krav_id LOOP
        v_verdi := p_punkter -> v_punkt.punkt;
        IF v_verdi IS NULL
           OR v_verdi ->> 'grenseverdi' IS NULL
           OR v_verdi ->> 'maalt_verdi' IS NULL
           OR v_verdi ->> 'kilde_type' IS NULL
           OR v_verdi ->> 'kilde_ref' IS NULL THEN
            RAISE EXCEPTION 'aksepter_moduldeployment: punkt % mangler'
                ' grenseverdi/maalt_verdi/kilde_type/kilde_ref',
                v_punkt.punkt USING ERRCODE = 'invalid_parameter_value';
        END IF;
        INSERT INTO public.modulaksept_punkt (modul_id, miljo, release_id,
            krav_id, punkt, grenseverdi, maalt_verdi, kilde_type, kilde_ref)
        VALUES (p_modul_id, p_miljo, p_release_id, p_krav_id,
                v_punkt.punkt, v_verdi ->> 'grenseverdi',
                v_verdi ->> 'maalt_verdi', v_verdi ->> 'kilde_type',
                v_verdi ->> 'kilde_ref');
    END LOOP;
    SELECT module_epoch INTO v_epoch FROM public.modulhode
     WHERE modul_id = p_modul_id;
    INSERT INTO public.modulregister_hendelse (modul_id, hendelse,
        release_id, miljo, module_epoch, aktor, detalj)
    VALUES (p_modul_id, 'modulaksept', p_release_id, p_miljo, v_epoch,
            p_aktor, jsonb_build_object(
                'drill_id', p_drill_id, 'krav_id', p_krav_id,
                'e2e_artefakt_id', p_e2e_artefakt::text,
                'evidens_jsonl_sha256', p_evidens_sha,
                'ci_run', p_ci_run, 'ci_commit', p_ci_commit));
END $$;

ALTER FUNCTION registrer_moduldrill(TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN,
    BOOLEAN, BOOLEAN, TEXT, TEXT, TIMESTAMPTZ) OWNER TO disponit_modul_eier;
ALTER FUNCTION aksepter_moduldeployment(TEXT, TEXT, TEXT, BIGINT, TEXT,
    TEXT, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, TEXT)
    OWNER TO disponit_modul_eier;
-- Grants i EIERVINDUET (048-disiplinen): en REVOKE fra en ikke-eier er
-- en stille no-op, og PUBLIC ville beholdt default-EXECUTE på begge.
SET LOCAL ROLE disponit_modul_eier;
REVOKE ALL ON FUNCTION registrer_moduldrill(TEXT, TEXT, TEXT, TEXT, TEXT,
    BOOLEAN, BOOLEAN, BOOLEAN, TEXT, TEXT, TIMESTAMPTZ) FROM PUBLIC;
REVOKE ALL ON FUNCTION aksepter_moduldeployment(TEXT, TEXT, TEXT, BIGINT,
    TEXT, TEXT, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, TEXT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION registrer_moduldrill(TEXT, TEXT, TEXT, TEXT,
    TEXT, BOOLEAN, BOOLEAN, BOOLEAN, TEXT, TEXT, TIMESTAMPTZ)
    TO disponit_modules_admin;
GRANT EXECUTE ON FUNCTION aksepter_moduldeployment(TEXT, TEXT, TEXT,
    BIGINT, TEXT, TEXT, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, TEXT)
    TO disponit_modules_admin;
RESET ROLE;

-- Definerne leser moduldeployment/modulrelease/modulhode som modul_eier
-- (eier dem alt) og skriver de nye tabellene: de nye eies av migrator,
-- så modul_eier trenger DML-grant på nøyaktig dem.
GRANT SELECT, INSERT ON moduldrill, modulaksept, modulaksept_punkt
    TO disponit_modul_eier;
GRANT SELECT ON akseptkrav_punkt TO disponit_modul_eier;
DO $$
BEGIN  -- identity-sekvensen alene, aldri hele skjemaets (minste fullmakt)
    EXECUTE format('GRANT USAGE ON SEQUENCE %s TO disponit_modul_eier',
                   pg_get_serial_sequence('moduldrill', 'drill_id'));
END $$;
