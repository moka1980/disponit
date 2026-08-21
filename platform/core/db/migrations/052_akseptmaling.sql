-- 052 — m56-aksept-målekode (aksept-arc-klarsignalet, GO 2026-08-21).
--
-- Tre manifestpunkter står `blokkert` fordi målingene deres ikke fantes
-- (PR #117 runde 3/5/10/14). Denne migrasjonen er BASENS halvdel av
-- målekoden; sjekklisten, konverteren og akseptskriptet bærer resten:
--
--   1. `rollback_testet` (§1.1): rullbakk-releasen skal ha BOOTET og
--      selv fullført det ventende oppdraget — med signert kvittering
--      fra SIN kjøring. `registrer_moduldrill` måler nå også
--      rullbakk-oppdragets kvitteringsavtrykk (`maal_rent_utfall`),
--      i samme drillrad som de to andre kontrollpunktene.
--   2. `revisjonslogg_korrekt` (§1.3): egne observasjoner per kjøring.
--      `maal_kjoringsattest` er drilloppdragets port anvendt på
--      KONTROLLØPET: kvitteringsavtrykket, claim-sporet
--      (release/miljø), artefakt-likheten (utfort ⇔ promotert på
--      releasen) og revisjonsraden (fase-2-TILLAT-loggposten oppdraget
--      er koblet til, 008). Sjekklisten kaller den per kjøring og
--      skriver utfallene i evidensstrømmen.
--   3. `syntetisk_datasett_likt_lokalt` (§1.2) måles i filene
--      (sjekkliste → artefakt → manifestskjema-porten, SP-11) og
--      trenger ingen baseendring.
--
-- GRENSEREVISJONEN: akseptgrensen får de sju evidenspunktene fra
-- klarsignalets §5. Registeret er append-only med krav-lås
-- (`akseptkrav_punkt_ingen_tillegg`, 049): et «tillegg» til en navngitt
-- grense ER et nytt krav_id — samme form som 050. v2 består som
-- historie; ingen aksept ble skrevet mot den.
--
-- ENDRINGSNOTAT v2 → v3:
--   TILLAGT (klarsignalet §5, grensene fra §1–§2):
--     * `drill.rullbakk_bootet_og_fullforte` — alle tre kontrollpunkter
--       grønne i SAMME drillrad, inkl. rullbakkens egen signerte
--       kvittering (registerhendelse: `rollback_drill`-hendelsen; selve
--       målingen er FK-en modulaksept→moduldrill med utfallsboolene i
--       den refererbare nøkkelen, E1f).
--     * `drill.spleisede_malinger` — én måling, én rad: drillraden
--       bærer tenant, alle tre oppdrag og artefaktets sha256; punkter
--       fra to forsøk kan ikke refereres (samme registerhendelse).
--     * `datasett.sha_ulik_mellom_ledd` — sha256 over testnettstedet
--       målt på staging OG mot innsjekkede bytes (SP-11, byte-likhet
--       begge ledd).
--     * `kvittering.attest_avvik` — per kjøring: signatur satt og
--       identisk, resultathash, attestert release == kjøringens
--       (`maal_kjoringsattest`); null avvik — 9/10 er rødt.
--     * `revisjonsrad.avvik_mot_bestilt` — revisjonsrad per kjøring
--       talt mot de ti bestilte; null avvik.
--     * `evidens.pa_tvers_av_runder` — sammenhengskravet (§2): all
--       evidens fra ÉN ny stagingrunde + ÉN ny drill, samme
--       manifestgenerasjon; runde-releasen ER den drillede.
--     * `aksept.gjenbrukt_gammel_evidens` — 19/8-artefaktet og
--       20/8-drillen er historikk, aldri akseptbevis: runde-evidensen
--       må bære v2-artefaktkravets målinger, drillreleaser er ubrukte.
--   UENDRET: alle 22 v2-punkter består ordrett.

INSERT INTO akseptkrav_punkt (krav_id, punkt, kilde_type, grenseverdi,
                              maalt_krav) VALUES
    ('m56-akseptflipp-v3', 'kontroll.ti_kjoringer_signert_innen_frist',
     'evidensfil', '10/10', '10/10'),
    ('m56-akseptflipp-v3', 'funn.avvik_mot_fasit', 'evidensfil', '0', '0'),
    ('m56-akseptflipp-v3', 'robots.brudd_i_mallogg', 'evidensfil', '0', '0'),
    ('m56-akseptflipp-v3', 'frekvens.over_grense_utfort',
     'evidensfil', '0', '0'),
    -- (arvet fra v2 — proxytoken-erstatningene består)
    ('m56-akseptflipp-v3', 'egress.hemmeligheter_i_browsermiljo',
     'evidensfil', '0', '0'),
    ('m56-akseptflipp-v3', 'skjema.brudd_promotert',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v3', 'skjema.hash_uten_rad_akseptert',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v3', 'skjema.mutert_ureferert',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v3', 'skjema.slettet',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v3', 'rapport.uten_pakrevd_arlighetsfelt_akseptert',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v3', 'rapport.klartekst_i_logg_eller_dump',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v3', 'domene.kontroll_uten_verifisering',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v3', 'payload.felt_utover_skjema_utlevert',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v3', 'deploy.registerrad_uten_kodefestet_type',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v3',
     'deploy.ekstern_lesing_uten_malautorisasjonsflagg',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v3', 'klasse.eksisterende_kontrakt_omklassifisert',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v3', 'klasse.aktivering_uten_frekvensgrense_lyktes',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v3', 'klasse.aktivering_uten_malautorisasjon_lyktes',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    -- (arvet fra v2 — proxytoken-erstatningene består)
    ('m56-akseptflipp-v3', 'egress.sideeffektklasse_gater_aktivering',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v3', 'malautorisasjon.ikke_registrert_vilkar_talte',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v3', 'malautorisasjon.feil_maldomene_godtatt',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v3', 'malautorisasjon.positiv_sti_virker',
     'ci_kjoring', 'ja', 'ja'),
    -- — de sju nye punktene (klarsignalet §5) —
    ('m56-akseptflipp-v3', 'drill.rullbakk_bootet_og_fullforte',
     'registerhendelse', 'ja (alle tre kontrollpunkter i samme drillrad)',
     'ja'),
    ('m56-akseptflipp-v3', 'drill.spleisede_malinger',
     'registerhendelse', '0 (én måling, én rad)', '0'),
    ('m56-akseptflipp-v3', 'datasett.sha_ulik_mellom_ledd',
     'evidensfil', '0 (byte-likhet begge ledd, SP-11)', '0'),
    ('m56-akseptflipp-v3', 'kvittering.attest_avvik',
     'evidensfil', '0 (10/10, 9/10 er rødt)', '0'),
    ('m56-akseptflipp-v3', 'revisjonsrad.avvik_mot_bestilt',
     'evidensfil', '0 (10/10 mot de bestilte)', '0'),
    ('m56-akseptflipp-v3', 'evidens.pa_tvers_av_runder',
     'evidensfil', '0 (én runde, én drill, én manifestgenerasjon)', '0'),
    ('m56-akseptflipp-v3', 'aksept.gjenbrukt_gammel_evidens',
     'evidensfil', '0 (ny runde, ferske release-id-er)', '0');

-- CI-kravet følger grensen: samme arbeidsflyt-kontrakt som v1/v2.
INSERT INTO akseptkrav_ci (krav_id, arbeidsflyt, hendelse, gren,
                           konklusjon) VALUES
    ('m56-akseptflipp-v3', '.github/workflows/ci.yml', 'push', 'main',
     'success');

-- ------------------------------------------------------------
-- Kolonnegrants for den nye målingen — 049-disiplinen: KOLONNENIVÅ,
-- aldri tabellnivå. `maal_kjoringsattest` (modul_eier-eid definer)
-- leser revisjonsraden oppdraget er KOBLET til (008:
-- `beslutning_loggpost_id`, fase-2-TILLAT-loggposten) og selve radens
-- beslutning. Aldri `begrunnelse`, aldri `input_hash` — målingen
-- trenger identiteten og utfallet, ikke innholdet. Tabellene eies av
-- migrator, så grantene trenger intet eiervindu; FORCE RLS-policyene
-- (002/008) gjelder uansett — funksjonen setter tenantkonteksten selv.
-- ------------------------------------------------------------
GRANT SELECT (beslutning_loggpost_id) ON oppdrag TO disponit_modul_eier;
GRANT SELECT (tenant, id, beslutning) ON revisjonslogg
    TO disponit_modul_eier;

-- Funksjonene eies av `disponit_modul_eier` (049) og migrator har
-- rollen WITH INHERIT FALSE: erstatning og nyskaping skjer i
-- EIERVINDUET (047/048-disiplinen). CREATE OR REPLACE beholder eier og
-- ACL for drillfunksjonen; den nye funksjonen får sitt ACL-bilde
-- eksplisitt nederst i vinduet.
SET LOCAL ROLE disponit_modul_eier;

CREATE OR REPLACE FUNCTION registrer_moduldrill(
    p_modul_id TEXT, p_miljo TEXT, p_drillet TEXT, p_rullback TEXT,
    p_kandidat TEXT, p_tenant TEXT, p_inflight_oppdrag BIGINT,
    p_rullback_oppdrag BIGINT, p_kandidat_oppdrag BIGINT,
    p_module_epoch BIGINT, p_artefakt_sha TEXT, p_nokkel TEXT,
    p_aktor TEXT, p_utfort_ts TIMESTAMPTZ)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id BIGINT; v_drillet_digest TEXT; v_kandidat_digest TEXT;
        v_epoch BIGINT; v_livslop TEXT; v_forrige_tenant TEXT;
        v_status TEXT; v_kvittering BOOLEAN; v_funnet INT;
        v_claim_stopp BOOLEAN; v_rene_utfall BOOLEAN; v_tilbake BOOLEAN;
        v_rull_ts TIMESTAMPTZ; v_kand_ts TIMESTAMPTZ; v_vindu BOOLEAN;
        v_kver INT; v_khash TEXT;
        v_claimet_av_drillet BOOLEAN; v_claimet_av_rullback BOOLEAN;
        v_claimet_av_kandidat BOOLEAN;
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
           -- Utfallene måles nedenfor og er ikke lenger kallerens; det
           -- MATERIELLE i et replay-kall er derfor hva drillen ble målt
           -- på: tenanten, de tre oppdragene og bytene raden hviler på.
           -- Samme nøkkel med andre oppdrag er en annen drill.
           AND tenant = p_tenant
           AND inflight_oppdrag = p_inflight_oppdrag
           AND rullback_oppdrag = p_rullback_oppdrag
           AND kandidat_oppdrag = p_kandidat_oppdrag
           AND epoch_snapshot = p_module_epoch
           AND artefakt_sha256 = lower(p_artefakt_sha)
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
    -- Kandidatens kontraktlinje er DRILLENS linje (Codex P1, #117 runde
    -- 17). `en_claiming_per_kontrakt` fører én linje per (modul, miljø,
    -- kontraktversjon, kontrakt_hash), så flere kan stå claiming
    -- samtidig, helt lovlig — og da må målingene under bindes til én av
    -- dem, ellers kan overganger fra én slekt pares med oppdrag fra en
    -- annen.
    SELECT livslop, kontraktversjon, kontrakt_hash
      INTO v_livslop, v_kver, v_khash
      FROM public.moduldeployment
     WHERE modul_id = p_modul_id AND miljo = p_miljo
       AND release_id = p_kandidat;
    IF v_livslop IS DISTINCT FROM 'claiming' THEN
        RAISE EXCEPTION 'registrer_moduldrill: kandidat %/% er %, ventet'
            ' claiming (aksepten binder raden som faktisk kjører)',
            p_modul_id, p_kandidat, coalesce(v_livslop, '<mangler>')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- …og den drillede og rullbakken må stå på SAMME linje. En drill som
    -- ruller mellom kontraktslekter er ingen rullbakk av det som ble
    -- drillet.
    IF NOT EXISTS (SELECT 1 FROM public.moduldeployment d
                    WHERE d.modul_id = p_modul_id AND d.miljo = p_miljo
                      AND d.release_id IN (p_drillet, p_rullback)
                      AND d.kontraktversjon = v_kver
                      AND d.kontrakt_hash = v_khash
                    HAVING count(*) = 2) THEN
        RAISE EXCEPTION 'registrer_moduldrill: %, % og % står ikke på samme'
            ' kontraktlinje (v%/%…) — en drill måler ÉN linje, og'
            ' overganger fra en annen slekt hører ikke til denne',
            p_drillet, p_rullback, p_kandidat, v_kver, left(v_khash, 12)
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
    -- Codex' P2 på PR #117 (runde 5): `epoch_snapshot` ble SNAPSHOTTET
    -- her, ved registreringen, mens drillartefaktets egen
    -- `oppsett.module_epoch` aldri ble sendt inn eller sammenlignet.
    -- Fencing-generasjonen er ikke pynt: den er konteksten claim-stoppet
    -- ble målt i, og en nødstopp eller reaktivering mellom drill og
    -- aksept flytter den. Raden kunne derfor påstå en ANNEN generasjon
    -- enn artefaktet som målte drillen — og skjule et misdannet bevis i
    -- stedet for å avvise det. Nå må artefaktet si hvilken generasjon det
    -- målte i, og den må være den levende.
    IF p_module_epoch IS DISTINCT FROM v_epoch THEN
        RAISE EXCEPTION 'registrer_moduldrill: drillen ble målt i epoch'
            ' %, men modulen står i epoch % — fencing-generasjonen har'
            ' flyttet seg siden målingen, og drillen gjelder da en annen'
            ' kontekst enn den som registreres',
            p_module_epoch, v_epoch
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Drillen ble utført FØR den ble registrert. Et tidsstempel fram i
    -- tid er ikke en måling, det er en påstand om framtiden — og
    -- CHECK-en under ville uansett stoppet raden; her får den et navn.
    IF p_utfort_ts IS NULL OR p_utfort_ts > now() THEN
        RAISE EXCEPTION 'registrer_moduldrill: utført-tidspunktet % er'
            ' tomt eller fram i tid — drillen skal bære sin EGEN måletid',
            p_utfort_ts USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- ------------------------------------------------------------
    -- DRILLVINDUET: de to overgangene drillen faktisk gjorde.
    --
    -- Codex' P1 på PR #117 (runde 16): utfallene ble målt som REN
    -- EKSISTENS av artefakter per release, uten et eneste ledd som
    -- knyttet oppdragene til rullingen. Har de tre releasene håndtert
    -- vanlig arbeid på noe tidspunkt i sine livsløp — og det har en
    -- release som har vært claiming — kunne et direkte
    -- `registrer_moduldrill`-kall plukke et signert, fullført oppdrag fra
    -- den drillede og promoterte oppdrag fra rullbakken og kandidaten, og
    -- få alle tre flaggene grønne uten at noe kappløp, noe claim-stopp
    -- eller noen rulling hadde funnet sted. Predikatene sa «det finnes
    -- arbeid på denne releasen», mens påstanden er «dette arbeidet krysset
    -- rullingen».
    --
    -- `bytt_release` (014) skriver én `releasebytte`-hendelse per
    -- overgang, med tidspunkt. De to overgangene drillen består av — inn
    -- på rullbakken og inn på kandidaten — gir derfor drillens egne to
    -- skillelinjer, og oppdragene måles MOT dem:
    --
    --   (b)  inflight: bestilt FØR rullingen, terminal ETTER den — det er
    --        nettopp «oppdraget krysset byttet».
    --   (a+b2) rullbakk: bestilt ETTER rullingen (mens den drillede
    --        drenerte) og ferdig FØR kandidaten overtok — claim-stoppet og
    --        overtakelsen er samme oppdrag, i det vinduet.
    --   (c)  kandidat: bestilt ETTER kandidatens registerbytte, terminal
    --        etterpå — arbeidet lå og ventet på nøyaktig den som overtok.
    --
    -- Alt sammen innenfor drillens egen måletid (`p_utfort_ts`), så et
    -- gammelt oppdrag ikke kan lånes inn i en ny drills vindu.
    --
    -- Mangler overgangene, er flaggene FALSE — ikke en exception. Et
    -- register uten drillens overganger bærer ingen drill å måle, og en
    -- rød drillrad er nettopp det riktige svaret: aksepten står på FK-en
    -- mot de tre grønne utfallene.
    -- ------------------------------------------------------------
    --
    -- OG OVERGANGEN MÅ VÆRE DEN SOM DRENERTE FORGJENGEREN (Codex P1,
    -- #117 runde 17). De to oppslagene fant sine `releasebytte`-
    -- hendelser hver for seg, og beviste aldri at byttet INN på
    -- rullbakken var det som drenerte den drillede. En modul med flere
    -- kontraktslekter — eller en eldre draining-release med overlappende
    -- arbeid — kunne derfor pare den drillede releasen og dens
    -- inflight-oppdrag fra én slekt med rullbakk- og kandidatoverganger
    -- fra en annen: alle tidspredikatene under kunne passere uten at
    -- noen rullbakk FRA den claimede drillede releasen hadde skjedd.
    --
    -- `bytt_release` skriver de to hendelsene i SAMME transaksjon:
    -- `drainet_ved_bytte` for den gamle, så `releasebytte` for den nye.
    -- `now()` er transaksjonsstabil, så de deler `ts` eksakt, og
    -- identiteten er stigende per INSERT, så dreneringen står FØR byttet.
    -- Paret er derfor selve overgangen — ikke to hendelser som tilfeldig
    -- fantes — og begge leddene bindes til drillens kontraktlinje.
    SELECT max(b.ts) INTO v_rull_ts
      FROM public.modulregister_hendelse b
      JOIN public.modulregister_hendelse d
        ON d.modul_id = b.modul_id AND d.miljo = b.miljo
       AND d.kontraktversjon = b.kontraktversjon
       AND d.kontrakt_hash = b.kontrakt_hash
       AND d.hendelse = 'drainet_ved_bytte' AND d.release_id = p_drillet
       AND d.ts = b.ts AND d.id < b.id
     WHERE b.modul_id = p_modul_id AND b.miljo = p_miljo
       AND b.hendelse = 'releasebytte' AND b.release_id = p_rullback
       AND b.kontraktversjon = v_kver AND b.kontrakt_hash = v_khash;
    SELECT max(b.ts) INTO v_kand_ts
      FROM public.modulregister_hendelse b
      JOIN public.modulregister_hendelse d
        ON d.modul_id = b.modul_id AND d.miljo = b.miljo
       AND d.kontraktversjon = b.kontraktversjon
       AND d.kontrakt_hash = b.kontrakt_hash
       AND d.hendelse = 'drainet_ved_bytte' AND d.release_id = p_rullback
       AND d.ts = b.ts AND d.id < b.id
     WHERE b.modul_id = p_modul_id AND b.miljo = p_miljo
       AND b.hendelse = 'releasebytte' AND b.release_id = p_kandidat
       AND b.kontraktversjon = v_kver AND b.kontrakt_hash = v_khash;
    v_vindu := v_rull_ts IS NOT NULL AND v_kand_ts IS NOT NULL
               AND v_rull_ts < v_kand_ts AND v_kand_ts <= p_utfort_ts;
    -- ------------------------------------------------------------
    -- UTFALLENE MÅLES (Codex P1, #117 runde 5).
    --
    -- `oppdrag` og `artefakt` står med FORCE ROW LEVEL SECURITY og
    -- tenant-policy; definerens rolle er ikke tabelleier, så policyen
    -- gjelder også her. Tenantkonteksten settes derfor eksplisitt til
    -- den drillen ble målt i, og legges tilbake etterpå — funksjonen
    -- skal ikke etterlate kallerens sesjon i et annet skop enn den fant
    -- den i.
    -- ------------------------------------------------------------
    v_forrige_tenant := current_setting('disponit.tenant', true);
    PERFORM set_config('disponit.tenant', p_tenant, true);
    SELECT count(*) INTO v_funnet FROM public.oppdrag o
     WHERE o.tenant = p_tenant
       AND o.id IN (p_inflight_oppdrag, p_rullback_oppdrag,
                    p_kandidat_oppdrag);
    IF v_funnet <> 3 THEN
        RAISE EXCEPTION 'registrer_moduldrill: fant % av 3 drilloppdrag'
            ' for tenant % — utfallene måles på oppdragene, og oppdrag'
            ' som ikke finnes har ingen utfall', v_funnet, p_tenant
            USING ERRCODE = 'no_data_found';
    END IF;
    IF EXISTS (SELECT 1 FROM public.oppdrag o
                WHERE o.tenant = p_tenant
                  AND o.id IN (p_inflight_oppdrag, p_rullback_oppdrag,
                               p_kandidat_oppdrag)
                  AND o.eiermodul IS DISTINCT FROM p_modul_id) THEN
        RAISE EXCEPTION 'registrer_moduldrill: minst ett drilloppdrag'
            ' eies av en annen modul enn % — en annen moduls arbeid er'
            ' ingen drill av denne', p_modul_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- ------------------------------------------------------------
    -- HVER LEDD MÅ VÆRE CLAIMET AV DEN RELEASEN LEDDET HANDLER OM
    -- (Codex P1, #117 runde 20).
    --
    -- Tidsvinduet over sier at oppdraget KRYSSET rullingen; det sier
    -- ikke hvem som hadde det. For `utfort` bar det promoterte
    -- artefaktet releasen, men `feilet` er også et rent utfall, og der
    -- krevde artefaktlikheten bare at det IKKE fantes et promotert
    -- artefakt på den drillede — fravær av en binding, ikke en binding.
    -- Et vanlig oppdrag bestilt før rullingen og feilet etterpå av
    -- rullbakk- eller kandidatarbeideren passerte derfor som den
    -- drillede releasens inflight-utfall.
    --
    -- `claim_neste_oppdrag` stempler nå claim-releasen på raden (§0), og
    -- de tre leddene måles mot den: inflight tilhører den DRILLEDE,
    -- rullbakkleddet RULLBAKKEN, kandidatleddet KANDIDATEN. Sporet er
    -- claim-funksjonens eget — ingen annen rolle kan skrive det — så
    -- dette er den ene identiteten drillen faktisk hviler på, ikke en
    -- slutning fra et fravær. Miljøet er med: samme release-ID i et
    -- annet miljø er en annen deployment.
    -- ------------------------------------------------------------
    SELECT EXISTS (SELECT 1 FROM public.oppdrag o
                    WHERE o.tenant = p_tenant AND o.id = p_inflight_oppdrag
                      AND o.claim_release_id = p_drillet
                      AND o.claim_miljo = p_miljo),
           EXISTS (SELECT 1 FROM public.oppdrag o
                    WHERE o.tenant = p_tenant AND o.id = p_rullback_oppdrag
                      AND o.claim_release_id = p_rullback
                      AND o.claim_miljo = p_miljo),
           EXISTS (SELECT 1 FROM public.oppdrag o
                    WHERE o.tenant = p_tenant AND o.id = p_kandidat_oppdrag
                      AND o.claim_release_id = p_kandidat
                      AND o.claim_miljo = p_miljo)
      INTO v_claimet_av_drillet, v_claimet_av_rullback,
           v_claimet_av_kandidat;
    -- (a)+(b2) claim-stopp: oppdraget som ble bestilt mens den drillede
    -- releasen drenerte, ble IKKE tatt av den — og ble tatt av
    -- rullbakken etter at hun ble bootet. Det andre leddet er det som
    -- skiller «den gamle sluttet å claime» fra «det gikk an å rulle
    -- tilbake»: uten det er claim-stoppet bare fravær av arbeid.
    --
    -- …OG OPPDRAGET MÅ LIGGE I VINDUET (Codex P1, #117 runde 16): bestilt
    -- etter rullingen, gjort ferdig før kandidaten overtok. Et hvilket
    -- som helst gammelt oppdrag med et promotert artefakt på rullbakken
    -- ville ellers holdt — og et claim-stopp som ikke ble målt MENS den
    -- drillede drenerte, er ikke et claim-stopp.
    v_claim_stopp :=
        v_vindu
        -- …og rullbakken må være den som CLAIMET det (runde 20): et
        -- promotert artefakt med rullbakkens release-ID er skrevet av
        -- arbeideren, mens claim-sporet er portens eget.
        AND v_claimet_av_rullback
        -- …OG RULLBAKKEN FULLFØRTE MED SIN EGEN SIGNERTE KVITTERING
        -- (052, aksept-arc-klarsignalet §1.1a). Promoteringsleddet under
        -- sier at et artefakt fra rullbakkens kjøring ble promotert;
        -- det sier ikke at kvitteringsveien noen gang var innom
        -- oppdraget. «Rullbakken fullførte selv» er samme påstand som
        -- inflight-leddets: signaturen i sin egen kolonne, identisk med
        -- konvoluttens, resultathash satt, og kapabiliteten brent med
        -- nøyaktig den hashen — målt av `maal_rent_utfall`, samme
        -- funksjon, samme avtrykk. En kvittering attestert på en annen
        -- kjøring enn rullbakkens finnes ikke som grønn her: claim-leddet
        -- over binder oppdraget til rullbakk-releasen, og avtrykket
        -- binder kvitteringen til oppdraget.
        AND public.maal_rent_utfall(p_tenant, p_rullback_oppdrag)
        AND NOT EXISTS (SELECT 1 FROM public.artefakt a
                     WHERE a.tenant = p_tenant
                       AND a.oppdrag_id = p_rullback_oppdrag
                       AND a.release_id = p_drillet)
        AND EXISTS (SELECT 1 FROM public.artefakt a
                     WHERE a.tenant = p_tenant
                       AND a.oppdrag_id = p_rullback_oppdrag
                       AND a.release_id = p_rullback
                       AND a.tilstand = 'promotert')
        AND EXISTS (SELECT 1 FROM public.oppdrag o
                     WHERE o.tenant = p_tenant
                       AND o.id = p_rullback_oppdrag
                       AND o.opprettet > v_rull_ts
                       AND o.opprettet < v_kand_ts
                       AND o.status_ts > o.opprettet
                       AND o.status_ts < v_kand_ts)
        -- …OG DEN DRENERTE MÅ HA LATT DET LIGGE LENGE NOK (Codex P1,
        -- runde 21). Vinduet over sier at oppdraget lå INNENFOR
        -- rullingen, ikke hvor lenge. Måletiden er tiden fra oppdraget
        -- ble bestilt (etter `v_rull_ts`, altså etter at den drillede
        -- ble drenert) til det FØRSTE claimet — nøyaktig strekket der
        -- en levende, claimende forgjenger ville tatt raden. Sporet er
        -- claim-portens eget (§0), write-once, så verken kjøretiden
        -- eller deployfullmakten kan strekke det.
        --
        -- `>=`, ikke `>`: terskelen er «i minst så lenge», som i
        -- `manifestskjema`. Og claimet må ligge FØR kandidatbyttet:
        -- ventes det ut etter at kandidaten overtok, er det ikke den
        -- drenerte releasens claim-stopp lenger.
        AND EXISTS (SELECT 1 FROM public.oppdrag o
                     WHERE o.tenant = p_tenant
                       AND o.id = p_rullback_oppdrag
                       AND o.forste_claim_ts IS NOT NULL
                       AND o.forste_claim_ts < v_kand_ts
                       AND o.forste_claim_ts - o.opprettet >=
                           (public.moduldrill_min_ventetid_s() || ' seconds')
                               ::INTERVAL);
    -- (b) rent utfall (SP-3): terminalt, signert kvittering, og utfallet
    -- STEMMER med evidensen — et `utfort` uten promotert artefakt og et
    -- ikke-`utfort` MED er begge falske verdikter. Motsigelsen regnes
    -- her, av radene selv, ikke av et tall i et artefakt.
    --
    -- SIGNERT MÅLES PÅ SIGNATUREN, IKKE PÅ NYTTELASTEN (Codex P1, #117).
    -- Den forrige formen leste `kvittering IS NOT NULL` og kalte det
    -- «signert kvittering». Men signaturen er en EGEN kolonne
    -- (`oppdrag.kvittering_signatur`, 005), og `oppdrag`-skjemaet lar de
    -- to variere fritt: kolonnelåsen gjør kvitteringsfeltene uforanderlige
    -- ETTER at de er satt, og statusmaskinen sier ingenting om at en
    -- nyttelast må ha en signatur ved siden av seg. Kjøretidsrollen har
    -- direkte `UPDATE` på raden. Én `UPDATE oppdrag SET kvittering='{}'`
    -- uten signaturkolonne ga altså `rene_utfall_ok = true`, og aksepten —
    -- som er UFORANDERLIG når den først er skrevet — påsto for alltid at
    -- drillen endte i en signert kvittering det aldri fantes en signatur
    -- for. Det er nøyaktig den formen SP-3 finnes for å utelukke.
    --
    -- Signaturen kan ikke verifiseres kryptografisk her (nøklene bor i
    -- API-et, som er den ENESTE veien som verifiserer en konvolutt før den
    -- lagres). Det porten kan gjøre, er å kreve HELE avtrykket den veien
    -- setter igjen, i stedet for det ene feltet enhver skriver kan finne
    -- på: signaturen må stå i sin egen kolonne, den må ikke være tom, den
    -- må være IDENTISK med signaturverdien i konvolutten som ligger lagret
    -- (`kvittering_signatur` ER `kvittering->signatur->>verdi`, hentet ut
    -- av verifiseringen selv — spriker de, kommer raden ikke derfra), og
    -- `resultathash` må være satt, siden veien skriver alle tre i samme
    -- `UPDATE`.
    --
    -- …MEN DE TRE FELTENE EIES AV DEN SAMME SKRIVEREN (Codex P1, #117
    -- runde 15). Kjøretidsrollen har direkte `UPDATE` på oppdragsraden —
    -- det er den API-et selv bruker — og et `UPDATE oppdrag SET
    -- kvittering='{"signatur":{"verdi":"x"}}', kvittering_signatur='x',
    -- resultathash='x'` oppfyller hele likheten over uten at noen
    -- konvolutt noen gang er verifisert. Feltenighet mellom kolonner én
    -- rolle kan skrive fritt, er ikke et bevis; det er en form som er
    -- litt mer arbeid å fylle ut.
    --
    -- Derfor kreves AVTRYKKET utenfor raden: kvitteringskapabiliteten for
    -- oppdraget må være BRENT, med nøyaktig den `resultathash`-en raden
    -- bærer. `kvitteringskapabiliteter` (005) står `REVOKE ALL ... FROM
    -- PUBLIC` uten et eneste tabellgrant — ingen rolle skriver den
    -- direkte. Den fylles bare av `utsted_kvitteringskapabilitet` (krever
    -- en claim kalleren HOLDER, med matchende `owner_claim_id`/
    -- `owner_generation` på et `plukket` oppdrag) og brennes bare av
    -- `bruk_kvitteringskapabilitet`, som API-et kaller FØRST etter at
    -- `attestering.verifiser` har godtatt signaturen mot nøkkelregisteret.
    -- Hashen er uforanderlig når den først er festet (statusmaskinen i
    -- 005), og `brukt` er engangs.
    --
    -- Det gjør ikke basen til en signaturverifiserer — HMAC-hemmelighetene
    -- bor i API-et, og ingen SQL kan regne dem ut på nytt. Men det flytter
    -- kravet fra «tre felter er enige» til «verifiseringsveien har
    -- FAKTISK vært her, på dette oppdraget, med denne hashen», og det er
    -- det sterkeste sporet den veien etterlater i basen.
    --
    -- Selve predikatet bor i `maal_rent_utfall` (Codex P1, runde 17):
    -- drillsonden og sjekklisten måler mot NØYAKTIG denne, i stedet for
    -- hver sin kopi bak en fullmakt de ikke har.
    SELECT o.status INTO v_status
      FROM public.oppdrag o
     WHERE o.tenant = p_tenant AND o.id = p_inflight_oppdrag;
    v_kvittering := public.maal_rent_utfall(p_tenant, p_inflight_oppdrag);
    v_rene_utfall := v_status = ANY (public.moduldrill_rene_utfall())
        AND v_kvittering
        -- DEN DRILLEDE RELEASEN HADDE DET INNE (Codex P1, runde 20).
        -- Uten dette leddet er `feilet` et utfall uten eier: ingen
        -- artefakt bærer releasen, og «det finnes ikke et promotert
        -- artefakt på den drillede» er sant for alt arbeid i verden.
        AND v_claimet_av_drillet
        AND ((v_status = 'utfort') = EXISTS (
                SELECT 1 FROM public.artefakt a
                 WHERE a.tenant = p_tenant
                   AND a.oppdrag_id = p_inflight_oppdrag
                   AND a.release_id = p_drillet
                   AND a.tilstand = 'promotert'))
        -- …og oppdraget må ha KRYSSET rullingen (Codex P1, #117 runde
        -- 16): bestilt før byttet, terminalt etter det, innenfor
        -- drillens måletid. Et rent utfall som lå ferdig før rullingen i
        -- det hele tatt ble fyrt, måler ikke SP-3.
        AND v_vindu
        AND EXISTS (SELECT 1 FROM public.oppdrag o
                     WHERE o.tenant = p_tenant
                       AND o.id = p_inflight_oppdrag
                       AND o.opprettet < v_rull_ts
                       AND o.status_ts > v_rull_ts
                       AND o.status_ts <= p_utfort_ts);
    -- (c) fram igjen: kandidaten plukket sitt eget oppdrag og promoterte
    -- — og oppdraget ble bestilt ETTER kandidatens registerbytte, så det
    -- lå og ventet på nøyaktig den som overtok (Codex P1, #117 runde 16).
    v_tilbake := v_vindu
        -- …og kandidaten må ha CLAIMET det (runde 20): overtakelsen er
        -- at nettopp hun tok raden, ikke at et artefakt bærer navnet.
        AND v_claimet_av_kandidat
        AND EXISTS (SELECT 1 FROM public.artefakt a
                          WHERE a.tenant = p_tenant
                            AND a.oppdrag_id = p_kandidat_oppdrag
                            AND a.release_id = p_kandidat
                            AND a.tilstand = 'promotert')
        AND EXISTS (SELECT 1 FROM public.oppdrag o
                     WHERE o.tenant = p_tenant
                       AND o.id = p_kandidat_oppdrag
                       AND o.opprettet > v_kand_ts
                       AND o.status_ts > o.opprettet
                       AND o.status_ts <= p_utfort_ts);
    PERFORM set_config('disponit.tenant',
                       coalesce(v_forrige_tenant, ''), true);
    INSERT INTO public.moduldrill (modul_id, miljo, drillet_release,
        rullback_release, akseptkandidat_release, epoch_snapshot,
        digest_snapshot, tenant, inflight_oppdrag, rullback_oppdrag,
        kandidat_oppdrag, artefakt_sha256, claim_stopp_ok, rene_utfall_ok,
        tilbake_ok, nokkel, aktor, utfort_ts)
    VALUES (p_modul_id, p_miljo, p_drillet, p_rullback, p_kandidat,
            v_epoch, v_kandidat_digest, p_tenant, p_inflight_oppdrag,
            p_rullback_oppdrag, p_kandidat_oppdrag, lower(p_artefakt_sha),
            v_claim_stopp, v_rene_utfall, v_tilbake,
            p_nokkel, p_aktor, p_utfort_ts)
    RETURNING drill_id INTO v_id;
    INSERT INTO public.modulregister_hendelse (modul_id, hendelse,
        release_id, miljo, module_epoch, aktor, detalj)
    VALUES (p_modul_id, 'rollback_drill', p_kandidat, p_miljo, v_epoch,
            p_aktor, jsonb_build_object(
                'drill_id', v_id, 'drillet', p_drillet,
                'rullback', p_rullback,
                'claim_stopp_ok', v_claim_stopp,
                'rene_utfall_ok', v_rene_utfall,
                'tilbake_ok', v_tilbake,
                'artefakt_sha256', lower(p_artefakt_sha),
                'utfort_ts', p_utfort_ts));
    RETURN v_id;
END $$;

-- KONTROLLØPETS ATTESTMÅLING — drilloppdragets port, anvendt per
-- kjøring (aksept-arc-klarsignalet §1.3a: «samme port som
-- `registrer_moduldrill` alt måler for drilloppdraget — her for
-- kontrolløpet»). Fire målinger og identiteten bak den fjerde:
--
--   * `kvittering_ok`   — avtrykket kvitteringsveien setter igjen
--                         (`maal_rent_utfall`: signaturkolonnen identisk
--                         med konvoluttens, resultathash satt,
--                         kapabiliteten brent med nøyaktig den hashen).
--   * `claim_release_ok`— claim-sporet (§0 i 049): oppdraget ble claimet
--                         av NØYAKTIG den releasen kjøringen påstår, i
--                         det miljøet. Sporet er claim-funksjonens eget;
--                         ingen annen rolle kan skrive det.
--   * `artefakt_ok`     — utfallet stemmer med evidensen (SP-3): utfort
--                         ⇔ promotert artefakt på releasen. Samme form
--                         som drillens inflight-ledd.
--   * `revisjonsrad_ok` — oppdraget er KOBLET til sin
--                         fase-2-TILLAT-loggpost (008), og raden finnes
--                         med riktig tenant og beslutning. `loggpost`
--                         returneres så telleren kan kreve at ti
--                         kjøringer bærer ti DISTINKTE rader — én rad
--                         per kjøring, aldri én rad for alle.
--
-- Fail-closed: et oppdrag som ikke finnes i tenantens skop måler alt
-- til false. Funksjonen påstår ingenting — den leser sporene andre
-- fullmakter satte, og kalles av sjekklisten per kjøring så utfallene
-- står i evidensstrømmen og kan telles av konverteren.
CREATE OR REPLACE FUNCTION maal_kjoringsattest(p_tenant TEXT,
    p_oppdrag BIGINT, p_release TEXT, p_miljo TEXT)
RETURNS TABLE (kvittering_ok BOOLEAN, claim_release_ok BOOLEAN,
               artefakt_ok BOOLEAN, revisjonsrad_ok BOOLEAN,
               loggpost BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_forrige TEXT; v_status TEXT; v_claim_rel TEXT;
        v_claim_miljo TEXT; v_loggpost BIGINT; v_funnet BOOLEAN;
BEGIN
    v_forrige := current_setting('disponit.tenant', true);
    PERFORM set_config('disponit.tenant', p_tenant, true);
    SELECT true, o.status, o.claim_release_id, o.claim_miljo,
           o.beslutning_loggpost_id
      INTO v_funnet, v_status, v_claim_rel, v_claim_miljo, v_loggpost
      FROM public.oppdrag o
     WHERE o.tenant = p_tenant AND o.id = p_oppdrag;
    IF v_funnet IS NOT TRUE THEN
        -- Et oppdrag som ikke finnes har ingen grønne målinger — og
        -- heller ingen loggpost-identitet å telle.
        PERFORM set_config('disponit.tenant',
                           coalesce(v_forrige, ''), true);
        RETURN QUERY SELECT false, false, false, false, NULL::BIGINT;
        RETURN;
    END IF;
    RETURN QUERY SELECT
        public.maal_rent_utfall(p_tenant, p_oppdrag),
        v_claim_rel IS NOT DISTINCT FROM p_release
            AND v_claim_miljo IS NOT DISTINCT FROM p_miljo,
        (v_status = 'utfort') = EXISTS (
            SELECT 1 FROM public.artefakt a
             WHERE a.tenant = p_tenant AND a.oppdrag_id = p_oppdrag
               AND a.release_id = p_release
               AND a.tilstand = 'promotert'),
        v_loggpost IS NOT NULL AND EXISTS (
            SELECT 1 FROM public.revisjonslogg r
             WHERE r.tenant = p_tenant AND r.id = v_loggpost
               AND r.beslutning = 'TILLAT'),
        v_loggpost;
    PERFORM set_config('disponit.tenant', coalesce(v_forrige, ''), true);
END $$;

RESET ROLE;

-- ACL-bildet for den nye funksjonen — 049-mønsteret ordrett: PUBLIC
-- fratas default-EXECUTE, målingen kalles av sjekklisten under
-- `disponit_modules_admin`. REVOKE/GRANT i eiervinduet: en REVOKE fra
-- en ikke-eier er en stille no-op (048-disiplinen).
SET LOCAL ROLE disponit_modul_eier;
REVOKE ALL ON FUNCTION maal_kjoringsattest(TEXT, BIGINT, TEXT, TEXT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION maal_kjoringsattest(TEXT, BIGINT, TEXT, TEXT)
    TO disponit_modules_admin;
RESET ROLE;
