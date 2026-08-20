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
    -- Codex' P1 på PR #117 (runde 5): de tre utfallene under var KALLERENS
    -- påstander. `disponit_modules_admin` er den brede deployfullmakten
    -- (registrer_release, bytt_release, onboarding …), og en som holdt
    -- den kunne kalle `registrer_moduldrill` direkte med tre håndskrevne
    -- `true` og få en immutabel, grønn drillrad uten å ha kjørt noen
    -- drill — hvorpå `aksepter_moduldeployment` FK-refererte den og
    -- aksepten var et faktum. HELE evidensapparatet lå i skriptet, og et
    -- skript er ingen skranke for den som kan la være å bruke det.
    --
    -- Utfallene MÅLES nå av definerne selv, i `oppdrag` og `artefakt`.
    -- Da må drillraden bære HVA den ble målt på: tenanten og de tre
    -- oppdragene drillen faktisk kjørte. De er FK-bundet, så en drillrad
    -- kan ikke peke på oppdrag som ikke finnes, og målingen kan regnes
    -- ut på nytt av hvem som helst i ettertid.
    tenant           TEXT NOT NULL,
    -- (b) oppdraget som VAR claimet da rullingen traff
    inflight_oppdrag BIGINT NOT NULL,
    -- (a)+(b2) oppdraget den drenerte releasen lot ligge, og som
    -- rullbakken plukket etter at den ble bootet
    rullback_oppdrag BIGINT NOT NULL,
    -- (c) kandidatens eget oppdrag — og kilden til akseptens E2E-bevis
    kandidat_oppdrag BIGINT NOT NULL,
    -- bytene raden hviler på: sha256 av drillartefaktet slik aksepten
    -- leste det. Basen kan ikke lese fila, men raden skal NAVNGI den.
    artefakt_sha256  TEXT NOT NULL CHECK (artefakt_sha256 ~ '^[0-9a-f]{64}$'),
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
    -- Ett oppdrag kan ikke være tre ledd: leddene måler ulike faser, og
    -- samme id tre steder er en drill som aldri fant sted.
    CHECK (inflight_oppdrag <> rullback_oppdrag),
    CHECK (inflight_oppdrag <> kandidat_oppdrag),
    CHECK (rullback_oppdrag <> kandidat_oppdrag),
    FOREIGN KEY (tenant, inflight_oppdrag) REFERENCES oppdrag (tenant, id),
    FOREIGN KEY (tenant, rullback_oppdrag) REFERENCES oppdrag (tenant, id),
    FOREIGN KEY (tenant, kandidat_oppdrag) REFERENCES oppdrag (tenant, id),
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

-- Codex' P1 på PR #117 (runde 14): drillraden BÆRER tenant — den navngir
-- tenanten, tre oppdrags-IDer, aktøren og bytene den ble målt på — men
-- sto uten RLS, mens `migrer.py` ga kjøretidsrollen `SELECT` på hele
-- tabellen. Nabotabellen `artefakt` er tenant-filtrert (008/016, FORCE);
-- her var den ikke det, så én forespørselsvei utenfor sin egen
-- tenantkontekst — eller en kompromittert kjøretidsrolle — leste hver
-- eneste tenants driftsbevis. Tenantporten står nå PÅ RADEN, ikke i
-- fullmakten: en fullmakt kan gis igjen ved et uhell, en policy gjelder
-- uansett hvem som får `SELECT` senere.
--
-- Ikke FORCE: eieren er migrator, altså deployveien som LAGET tabellen
-- og som når som helst kan skru av enhver policy. Porten finnes for
-- forespørselsveien og for definerne, og en FORCE ville i tillegg
-- blindet driftens egen etterkontroll (akseptskriptets kvitteringslesning
-- og `modulaksept_punkt`, som ikke har noen tenantkolonne å filtrere på).
ALTER TABLE moduldrill ENABLE ROW LEVEL SECURITY;
CREATE POLICY moduldrill_tenant ON moduldrill
    USING (tenant = current_setting('disponit.tenant', true));
-- Definerne (`registrer_moduldrill` skriver raden, `aksepter_moduldeployment`
-- leser kandidatoppdraget ut av den) kjører som `disponit_modul_eier` og
-- måler tenanten EKSPLISITT i sine egne kontroller — de skal ikke også
-- måtte bære kallerens GUC. Samme mønster som `policyaktivering_eier` (047).
CREATE POLICY moduldrill_eier ON moduldrill
    USING (CURRENT_USER = 'disponit_modul_eier');

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

-- Samme port som på drillen (Codex P1, runde 14). Aksepten navngir E2E-
-- tenanten, artefakt-UUIDen, aktøren, evidensfilas hash og CI-kjøringen;
-- tenanten den ble målt i, er `e2e_tenant` (registreringen krever alt at
-- den er drillens tenant, så de to kan ikke sprike).
ALTER TABLE modulaksept ENABLE ROW LEVEL SECURITY;
CREATE POLICY modulaksept_tenant ON modulaksept
    USING (e2e_tenant = current_setting('disponit.tenant', true));
CREATE POLICY modulaksept_eier ON modulaksept
    USING (CURRENT_USER = 'disponit_modul_eier');

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

-- Punktradene bærer `kilde_ref` — artefakt-UUIDer, CI-kjøringer,
-- evidensfilnavn — og har INGEN tenantkolonne å filtrere på. Da er det
-- ingen tenantport å skrive; raden er evidens for eier- og driftsveien,
-- og forespørselsveien leser aldri denne tabellen (Codex P1, runde 14).
-- Uten en policy som treffer, ser ingen annen rolle noen rad: RLS er
-- default-deny, og det er nøyaktig svaret her.
ALTER TABLE modulaksept_punkt ENABLE ROW LEVEL SECURITY;
CREATE POLICY modulaksept_punkt_eier ON modulaksept_punkt
    USING (CURRENT_USER = 'disponit_modul_eier');

-- ------------------------------------------------------------
-- 6. Funksjonene — modul_eier-eide definere, EXECUTE kun til
--    disponit_modules_admin (014-mønsteret). INSERT på tabellene er
--    eierens/migrators særrettighet; ingen andre roller får DML.
-- ------------------------------------------------------------
-- Utfallene er IKKE parametre (Codex P1, #117 runde 5): kalleren oppgir
-- HVA drillen ble målt på — tenanten og de tre oppdragene — og funksjonen
-- måler selv i `oppdrag`/`artefakt`. En kaller med `disponit_modules_admin`
-- kan derfor ikke lenger skrive en grønn drillrad; han må ha oppdrag som
-- faktisk bærer utfallene, og dem lager bare arbeid.
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
    -- (a)+(b2) claim-stopp: oppdraget som ble bestilt mens den drillede
    -- releasen drenerte, ble IKKE tatt av den — og ble tatt av
    -- rullbakken etter at hun ble bootet. Det andre leddet er det som
    -- skiller «den gamle sluttet å claime» fra «det gikk an å rulle
    -- tilbake»: uten det er claim-stoppet bare fravær av arbeid.
    v_claim_stopp :=
        NOT EXISTS (SELECT 1 FROM public.artefakt a
                     WHERE a.tenant = p_tenant
                       AND a.oppdrag_id = p_rullback_oppdrag
                       AND a.release_id = p_drillet)
        AND EXISTS (SELECT 1 FROM public.artefakt a
                     WHERE a.tenant = p_tenant
                       AND a.oppdrag_id = p_rullback_oppdrag
                       AND a.release_id = p_rullback
                       AND a.tilstand = 'promotert');
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
    -- `UPDATE`. Da må en forfalskning konstrueres helt, ikke bare utelates.
    SELECT o.status,
           o.kvittering IS NOT NULL
           AND o.kvittering_signatur IS NOT NULL
           AND pg_catalog.btrim(o.kvittering_signatur) <> ''
           AND o.kvittering_signatur
               IS NOT DISTINCT FROM (o.kvittering -> 'signatur' ->> 'verdi')
           AND o.resultathash IS NOT NULL
      INTO v_status, v_kvittering
      FROM public.oppdrag o
     WHERE o.tenant = p_tenant AND o.id = p_inflight_oppdrag;
    v_rene_utfall := v_status IN ('utfort', 'feilet') AND v_kvittering
        AND ((v_status = 'utfort') = EXISTS (
                SELECT 1 FROM public.artefakt a
                 WHERE a.tenant = p_tenant
                   AND a.oppdrag_id = p_inflight_oppdrag
                   AND a.release_id = p_drillet
                   AND a.tilstand = 'promotert'));
    -- (c) fram igjen: kandidaten plukket sitt eget oppdrag og promoterte.
    v_tilbake := EXISTS (SELECT 1 FROM public.artefakt a
                          WHERE a.tenant = p_tenant
                            AND a.oppdrag_id = p_kandidat_oppdrag
                            AND a.release_id = p_kandidat
                            AND a.tilstand = 'promotert');
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

CREATE OR REPLACE FUNCTION aksepter_moduldeployment(
    p_modul_id TEXT, p_miljo TEXT, p_release_id TEXT, p_drill_id BIGINT,
    p_krav_id TEXT, p_e2e_tenant TEXT, p_e2e_artefakt UUID,
    p_evidens_sha TEXT, p_manifest_commit TEXT, p_ci_run TEXT,
    p_ci_commit TEXT, p_punkter JSONB, p_nokkel TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_livslop TEXT; v_mangler TEXT; v_punkt RECORD; v_verdi JSONB;
        v_epoch BIGINT; v_avvik TEXT; v_drill_tenant TEXT;
        v_kandidat_oppdrag BIGINT; v_forrige_tenant TEXT;
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
    -- A2, siste ledd (Codex P1, #117 runde 5): E2E-beviset må komme fra
    -- DRILLENS KANDIDATOPPDRAG. FK-en på tabellen binder tenant, modul,
    -- release og promotert tilstand — men ikke HVILKET arbeid artefaktet
    -- kom av, så et hvilket som helst annet promotert artefakt fra samme
    -- release passerte den. Kontrollen fantes bare i `m56-aksept.py`, og
    -- et skript er ingen skranke for den som kaller funksjonen direkte.
    -- Drillraden bærer nå kandidatoppdraget, så båndet kan måles her.
    -- Kun DEN dimensjonen måles her; release og tilstand bæres fortsatt
    -- av FK-en, som gjelder enhver skrivevei og ikke bare denne.
    SELECT d.tenant, d.kandidat_oppdrag
      INTO v_drill_tenant, v_kandidat_oppdrag
      FROM public.moduldrill d
     WHERE d.modul_id = p_modul_id AND d.drill_id = p_drill_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: ukjent drill %/%',
            p_modul_id, p_drill_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_drill_tenant IS DISTINCT FROM p_e2e_tenant THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: drillen ble målt for'
            ' tenant %, mens E2E-beviset er % — evidens fra én tenant'
            ' aksepterer ingenting for en annen',
            v_drill_tenant, p_e2e_tenant
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_forrige_tenant := current_setting('disponit.tenant', true);
    PERFORM set_config('disponit.tenant', p_e2e_tenant, true);
    IF NOT EXISTS (SELECT 1 FROM public.artefakt a
                    WHERE a.tenant = p_e2e_tenant
                      AND a.artefakt_id = p_e2e_artefakt
                      AND a.oppdrag_id = v_kandidat_oppdrag) THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: E2E-artefaktet % kom'
            ' ikke av drillens kandidatoppdrag % — aksepten skal binde'
            ' beviset drillen SÅ, ikke et annet artefakt fra samme'
            ' release', p_e2e_artefakt, v_kandidat_oppdrag
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.tenant',
                       coalesce(v_forrige_tenant, ''), true);
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

ALTER FUNCTION registrer_moduldrill(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    BIGINT, BIGINT, BIGINT, BIGINT, TEXT, TEXT, TEXT, TIMESTAMPTZ)
    OWNER TO disponit_modul_eier;
ALTER FUNCTION aksepter_moduldeployment(TEXT, TEXT, TEXT, BIGINT, TEXT,
    TEXT, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, TEXT)
    OWNER TO disponit_modul_eier;
-- Grants i EIERVINDUET (048-disiplinen): en REVOKE fra en ikke-eier er
-- en stille no-op, og PUBLIC ville beholdt default-EXECUTE på begge.
SET LOCAL ROLE disponit_modul_eier;
REVOKE ALL ON FUNCTION registrer_moduldrill(TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, BIGINT, BIGINT, BIGINT, BIGINT, TEXT, TEXT, TEXT, TIMESTAMPTZ)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION aksepter_moduldeployment(TEXT, TEXT, TEXT, BIGINT,
    TEXT, TEXT, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, TEXT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION registrer_moduldrill(TEXT, TEXT, TEXT, TEXT,
    TEXT, TEXT, BIGINT, BIGINT, BIGINT, BIGINT, TEXT, TEXT, TEXT,
    TIMESTAMPTZ) TO disponit_modules_admin;
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
-- Definerne MÅLER nå drillutfallene i `oppdrag`/`artefakt` i stedet for å
-- motta dem (Codex P1, #117 runde 5), og trenger derfor lesetilgang dit.
-- KOLONNENIVÅ, ikke tabellnivå: målingene leser status, kvitteringen med
-- SIGNATUREN og resultathashen sin (Codex P1, runde 8 — «signert» måles på
-- signaturkolonnen, ikke på nyttelasten, så kolonnen må være lesbar her),
-- eierskap og artefaktenes tilhørighet — aldri `payload_kryptert`,
-- `key_id`, `nonce` eller `ciphertext`. En fullmakt som gir mer enn
-- målingen bruker, er en fullmakt som venter på et annet kall.
-- Tenant-policyen (016/008, FORCE) gjelder uansett: definerne eier
-- ikke tabellene og setter `disponit.tenant` eksplisitt.
GRANT SELECT (tenant, id, status, kvittering, kvittering_signatur,
              resultathash, eiermodul)
    ON oppdrag TO disponit_modul_eier;
GRANT SELECT (tenant, artefakt_id, oppdrag_id, release_id, tilstand)
    ON artefakt TO disponit_modul_eier;
DO $$
BEGIN  -- identity-sekvensen alene, aldri hele skjemaets (minste fullmakt)
    EXECUTE format('GRANT USAGE ON SEQUENCE %s TO disponit_modul_eier',
                   pg_get_serial_sequence('moduldrill', 'drill_id'));
END $$;

-- ------------------------------------------------------------
-- 8. Statusflaten: hva forespørselsveien SKAL kunne se (Codex P1, #117
--    runde 14). Kjøretidsrollen ble gitt `SELECT` på hele akseptflaten
--    «så statusetiketter og evidensvisninger skal kunne peke på
--    hendelsen» — men en statusetikett trenger FAKTUMET, ikke bevisene:
--    at (modul, miljø, release) er akseptert mot et krav, når, og hvilken
--    drill den hviler på. Tenanten, artefakt-UUIDen, oppdrags-IDene,
--    aktøren, evidenshashene og CI-referansene er driftens bevis og hører
--    til eier- og migratorveien.
--
--    Visningen er derfor SANERT — den bærer ingen tenantidentifikator i
--    det hele tatt, og har dermed ingenting å lekke på tvers av tenanter.
--    Den eies av migrator (tabelleieren), så den leser gjennom RLS-porten
--    over slik en visning skal; og fordi den ikke velger en eneste
--    tenantkolonne, er den samme rad for alle som ser den.
-- ------------------------------------------------------------
CREATE VIEW modulaksept_status AS
SELECT modul_id, miljo, release_id, krav_id, drill_id, akseptert_ts
  FROM modulaksept;

-- ------------------------------------------------------------
-- 9. DRILLENS RESERVASJON AV DEPLOYMENTFLATEN (Codex P1, #117 runde 14).
--
--    Flippedrillen holdt en advisory-lås i sitt EGET nøkkelrom
--    (to-heltallsrommet), mens registerets overganger tar
--    `pg_advisory_xact_lock(hashtextextended('modul:' || modul_id, 0))`.
--    To ulike låserom: en vanlig `bytt_release`, et `noddeaktiver_modul`
--    eller en `sett_modulstatus` så aldri drillens reservasjon, og kunne
--    drenere rullbakk- eller kandidatdeploymenten MELLOM to drillfaser.
--    Målingene blir da noe annet enn de sier, de enveis drill-id-ene er
--    brukt opp uansett, og miljøet står halvt over i en tilstand ingen
--    artefakt beskriver. Drillåsen gjerdet bare en annen drill.
--
--    Reservasjonen kan ikke være en lås i det rommet heller, og det er
--    poenget med at den er en RAD:
--      * eksklusivt ville den stengt claim-porten i 015, som tar den
--        samme modulnøkkelen DELT for hvert eneste claim — og drillen
--        måler nettopp claiming, så den ville ventet på seg selv;
--      * delt ville den stengt drillens EGNE overganger ute, for de går
--        gjennom sjekklistens faser 2/4/9 i EGNE prosesser med egne
--        sesjoner, og en advisory-lås gjelder én sesjon.
--    Innehaveren er derfor et TOKEN som kan presenteres av alle drillens
--    sesjoner (`disponit.deployreservasjon`), og porten står på tabellen
--    — så den gjelder enhver skrivevei inn i `moduldeployment`, ikke bare
--    de funksjonene noen husket å endre.
--
--    Utløpstiden er sikkerhetsventilen: en drill som dør uten å frigi,
--    skal ikke stenge modulen for alltid. Frigivelsen ved normal slutt
--    er den vanlige veien; utløpet er backstoppen.
-- ------------------------------------------------------------
CREATE TABLE moduldeployment_reservasjon (
    modul_id   TEXT NOT NULL REFERENCES modulhode (modul_id),
    miljo      TEXT NOT NULL,
    innehaver  TEXT NOT NULL CHECK (btrim(innehaver) <> ''),
    aktor      TEXT NOT NULL,
    tatt_ts    TIMESTAMPTZ NOT NULL DEFAULT now(),
    utloper_ts TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (modul_id, miljo),
    CHECK (utloper_ts > tatt_ts)
);

CREATE OR REPLACE FUNCTION moduldeployment_reservasjon_vakt()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE v_innehaver TEXT;
BEGIN
    SELECT r.innehaver INTO v_innehaver
      FROM public.moduldeployment_reservasjon r
     WHERE r.modul_id = NEW.modul_id AND r.miljo = NEW.miljo
       AND r.utloper_ts > now();
    -- Ingen reservasjon (det normale) → porten er ikke der. En modul uten
    -- pågående drill merker ingenting til denne triggeren.
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;
    IF v_innehaver IS DISTINCT FROM
           current_setting('disponit.deployreservasjon', true) THEN
        RAISE EXCEPTION 'moduldeployment: (%, %) er reservert av en pågående'
            ' flippedrill (%). En overgang her ville drenert dens rullbakk-'
            ' eller kandidatdeployment midt i en enveis, uigjentakelig'
            ' måling — og drill-id-ene er brukt opp uansett hva målingen'
            ' ender med. Vent til drillen er ferdig, eller presenter'
            ' reservasjonen i disponit.deployreservasjon.',
            NEW.modul_id, NEW.miljo, v_innehaver
            USING ERRCODE = 'lock_not_available';
    END IF;
    RETURN NEW;
END $$;
-- DELETE er alt forbudt av `deployment_ingen_delete` (015), så INSERT og
-- UPDATE er hele skriveflaten.
DROP TRIGGER IF EXISTS deployment_reservasjon ON moduldeployment;
CREATE TRIGGER deployment_reservasjon
    BEFORE INSERT OR UPDATE ON moduldeployment
    FOR EACH ROW EXECUTE FUNCTION moduldeployment_reservasjon_vakt();

CREATE OR REPLACE FUNCTION ta_deployreservasjon(
    p_modul_id TEXT, p_miljo TEXT, p_innehaver TEXT, p_aktor TEXT,
    p_varighet INTERVAL)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_annen TEXT;
BEGIN
    IF p_innehaver IS NULL OR btrim(p_innehaver) = '' THEN
        RAISE EXCEPTION 'ta_deployreservasjon: innehaver er obligatorisk —'
            ' en reservasjon uten innehaver kan ingen presentere'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_varighet IS NULL OR p_varighet <= interval '0 seconds' THEN
        RAISE EXCEPTION 'ta_deployreservasjon: varigheten må være positiv —'
            ' en reservasjon som er utløpt i det den tas, gjerder ingenting'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Samme modul-lås overgangene tar: reservasjonen kan ikke tas midt i
    -- et `bytt_release` som alt er i gang, og to samtidige forsøk på å ta
    -- den kan ikke begge lese «ledig».
    PERFORM pg_advisory_xact_lock(hashtextextended('modul:' || p_modul_id, 0));
    SELECT r.innehaver INTO v_annen
      FROM public.moduldeployment_reservasjon r
     WHERE r.modul_id = p_modul_id AND r.miljo = p_miljo
       AND r.utloper_ts > now() AND r.innehaver <> p_innehaver;
    IF FOUND THEN
        RAISE EXCEPTION 'ta_deployreservasjon: (%, %) er alt reservert av %',
            p_modul_id, p_miljo, v_annen USING ERRCODE = 'lock_not_available';
    END IF;
    -- En utløpt rad, eller vår egen fra et tidligere forsøk, ryddes: å ta
    -- den samme reservasjonen om igjen er idempotent, ikke en kollisjon.
    DELETE FROM public.moduldeployment_reservasjon r
     WHERE r.modul_id = p_modul_id AND r.miljo = p_miljo;
    INSERT INTO public.moduldeployment_reservasjon
        (modul_id, miljo, innehaver, aktor, utloper_ts)
    VALUES (p_modul_id, p_miljo, p_innehaver, p_aktor, now() + p_varighet);
END $$;

CREATE OR REPLACE FUNCTION frigi_deployreservasjon(
    p_modul_id TEXT, p_miljo TEXT, p_innehaver TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    -- Bare sin EGEN: en annens reservasjon frigis av utløpet, ikke av en
    -- kaller som gjerne vil deploye.
    DELETE FROM public.moduldeployment_reservasjon r
     WHERE r.modul_id = p_modul_id AND r.miljo = p_miljo
       AND r.innehaver = p_innehaver;
END $$;

-- Vakten er en INVOKER-funksjon som `moduldeployment_livslop` (014) og
-- eies av migrator som den: den leser bare reservasjonsraden, og den skal
-- ikke bære en fullmakt kalleren ikke har. Definerne under er noe annet —
-- de SKRIVER reservasjonen, og eies av modul_eier som resten av CP2.
ALTER FUNCTION ta_deployreservasjon(TEXT, TEXT, TEXT, TEXT, INTERVAL)
    OWNER TO disponit_modul_eier;
ALTER FUNCTION frigi_deployreservasjon(TEXT, TEXT, TEXT)
    OWNER TO disponit_modul_eier;
SET LOCAL ROLE disponit_modul_eier;
REVOKE ALL ON FUNCTION ta_deployreservasjon(TEXT, TEXT, TEXT, TEXT, INTERVAL)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION frigi_deployreservasjon(TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ta_deployreservasjon(TEXT, TEXT, TEXT, TEXT, INTERVAL)
    TO disponit_modules_admin;
GRANT EXECUTE ON FUNCTION frigi_deployreservasjon(TEXT, TEXT, TEXT)
    TO disponit_modules_admin;
RESET ROLE;
-- Vakten leser tabellen som den rollen som skriver `moduldeployment` —
-- definerne (`disponit_modul_eier`) og migrator, som eier begge.
GRANT SELECT, INSERT, DELETE ON moduldeployment_reservasjon
    TO disponit_modul_eier;
