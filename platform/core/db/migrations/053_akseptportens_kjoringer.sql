-- 053 — kontrollkjøringene inn i akseptporten (Codex 3×P1 på PR #125).
--
-- #124-maskinen la re-målingen av kontrolløpets ti kjøringer i
-- akseptSKRIPTET. Verdiktet på den var 049s egen lærdom tilbake: «et
-- skript er ingen skranke for den som kaller definereren direkte»
-- (#117 runde 22) — deployfullmakten kunne hoppe over skriptet og
-- skrive den immutable raden med en grønn aggregatattest. Samme form
-- som drillens tre oppdrag (#117 runde 5): kallet oppgir HVA som ble
-- målt, basen måler selv.
--
--   1. `maal_kjoringsattest` v2: `artefakt_ok` var en EKVIVALENS —
--      `status='feilet'` uten promotert artefakt ga true — og verken
--      eiermodul eller oppdragstype ble målt. Nå: KONJUNKSJON
--      (utfort OG promotert på releasen), pluss `modul_ok`: oppdraget
--      eies av modulen som aksepteres, og oppdragstypen er REGISTRERT
--      til den (oppdragstype_register, 014) — semantikken slås opp i
--      virkelig tilstand, aldri sendes inn som påstand (SP-13).
--   2. `aksepter_moduldeployment` v2: nytt parameter `p_kjoringer` —
--      identitetene bindes til evidensen via den drillscopede
--      `identiteter.kjoringer`-attestraden (verifikators innlogging,
--      to identiteter), og hver kjøring re-måles i basen mot
--      drillradens `drillet_release` før noe skrives. Distinkte
--      loggposter kreves; kjøringene skrives i hendelsens detalj.
--
-- REGELSETTET persisteres IKKE (verdiktets tredje funn, besvart):
-- liv-leddet finnes transitivt — claim-sporet binder kjøringen til
-- releasen, releasen til den registrerte digesten (liv-målt av
-- aksepten), og motoren i det imaget NEKTER å kjøre med axe-bytes ≠
-- pinnet sha256 (motor_axe/kjor.py, exit ≠ 0). Enhver fullført kjøring
-- på den digesten kjørte det pinnede regelsettet; filtelleren vokter
-- transkripsjonen av det.
--
-- Begge funksjonene eies av `disponit_modul_eier`; signaturbyttene
-- skjer i EIERVINDUET med DROP + CREATE + eksplisitt ACL
-- (052-mønsteret). De gamle signaturene består i
-- eierskap-reparasjonens designtabell som transitoriske rader (055-
-- fella fra #123 runde 3: reparasjonen kjører FØR migrer.py på en base
-- som ennå står på 052).

-- IDENTITETSREFERATET — hvilke kjøringer evidensfilen navngir, per
-- (fil, krav, drill). Eget bord med vilje: `evidensfil_attest` er
-- FK-bundet til `akseptkrav_punkt`, og krav-låsen (049) gjør at et
-- registrert krav aldri utvides — så listen kan ikke være et
-- registerpunkt. Immutabelt som punktattestene: skrives én gang av
-- verifikatorens innlogging, replay er no-op, sprik er en programfeil
-- (kontrollen bor i `attester_evidensfil`). Migrator-eid, som
-- `evidensfil_attest`; eierrollen får radene den trenger.
CREATE TABLE evidensfil_kjoringer (
    sha256       TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    krav_id      TEXT NOT NULL,
    drill_sha256 TEXT NOT NULL
        CHECK (drill_sha256 = '' OR drill_sha256 ~ '^[0-9a-f]{64}$'),
    -- kanonisk form: kommadelte positive heltall, håndhevet av
    -- attestveien (regex) — én skrivemåte, én identitet.
    kjoringer    TEXT NOT NULL,
    aktor        TEXT NOT NULL,
    attestert_av TEXT NOT NULL,
    attestert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (sha256, krav_id, drill_sha256)
);
REVOKE ALL ON evidensfil_kjoringer FROM PUBLIC;
GRANT SELECT, INSERT ON evidensfil_kjoringer TO disponit_modul_eier;
-- append-only som punktattestene (049): et referat endres eller
-- slettes aldri — sprik fanges av kontrollen i attestveien.
CREATE TRIGGER evidensfil_kjoringer_immutable BEFORE UPDATE OR DELETE
    ON evidensfil_kjoringer
    FOR EACH ROW EXECUTE FUNCTION modulregister_append_only();
CREATE TRIGGER evidensfil_kjoringer_ingen_truncate BEFORE TRUNCATE
    ON evidensfil_kjoringer
    FOR EACH STATEMENT EXECUTE FUNCTION modulregister_append_only();

-- Kolonnegrants for modul-leddet (049-disiplinen: kolonnenivå).
-- `eiermodul` fikk eierrollen i 049; `oppdragstype` er ny her — og
-- registerbordet (014) leses for «typen er registrert til modulen».
GRANT SELECT (oppdragstype) ON oppdrag TO disponit_modul_eier;
GRANT SELECT ON oppdragstype_register TO disponit_modul_eier;

SET LOCAL ROLE disponit_modul_eier;

DROP FUNCTION maal_kjoringsattest(TEXT, BIGINT, TEXT, TEXT);

-- KONTROLLØPETS ATTESTMÅLING v2 — drilloppdragets port per kjøring,
-- nå med utfallet og eierskapet i målingen:
--
--   * `kvittering_ok`   — avtrykket kvitteringsveien setter igjen
--                         (`maal_rent_utfall`).
--   * `claim_release_ok`— claim-sporet: NØYAKTIG (release, miljø).
--   * `artefakt_ok`     — KONJUNKSJON (053): oppdraget er `utfort` OG
--                         bærer et promotert artefakt på releasen. Den
--                         gamle ekvivalensen lot `feilet` uten artefakt
--                         måle grønt — «false = false» er ikke en
--                         fullført kjøring.
--   * `revisjonsrad_ok` — fase-2-TILLAT-loggposten finnes (008);
--                         `loggpost` returneres for distinkthetstelling.
--   * `modul_ok`        — NY (053): oppdraget eies av modulen som
--                         aksepteres, og oppdragstypen er registrert
--                         til den i `oppdragstype_register` (014) — et
--                         oppdrag fra en annen modul med samme
--                         release-etikett er ikke denne modulens
--                         kjøring.
CREATE FUNCTION maal_kjoringsattest(p_tenant TEXT,
    p_oppdrag BIGINT, p_release TEXT, p_miljo TEXT, p_modul TEXT)
RETURNS TABLE (kvittering_ok BOOLEAN, claim_release_ok BOOLEAN,
               artefakt_ok BOOLEAN, revisjonsrad_ok BOOLEAN,
               modul_ok BOOLEAN, loggpost BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_forrige TEXT; v_status TEXT; v_claim_rel TEXT;
        v_claim_miljo TEXT; v_loggpost BIGINT; v_funnet BOOLEAN;
        v_eiermodul TEXT; v_type TEXT;
BEGIN
    v_forrige := current_setting('disponit.tenant', true);
    PERFORM set_config('disponit.tenant', p_tenant, true);
    SELECT true, o.status, o.claim_release_id, o.claim_miljo,
           o.beslutning_loggpost_id, o.eiermodul, o.oppdragstype
      INTO v_funnet, v_status, v_claim_rel, v_claim_miljo, v_loggpost,
           v_eiermodul, v_type
      FROM public.oppdrag o
     WHERE o.tenant = p_tenant AND o.id = p_oppdrag;
    IF v_funnet IS NOT TRUE THEN
        -- Et oppdrag som ikke finnes har ingen grønne målinger — og
        -- heller ingen loggpost-identitet å telle.
        PERFORM set_config('disponit.tenant',
                           coalesce(v_forrige, ''), true);
        RETURN QUERY SELECT false, false, false, false, false,
                            NULL::BIGINT;
        RETURN;
    END IF;
    RETURN QUERY SELECT
        public.maal_rent_utfall(p_tenant, p_oppdrag),
        v_claim_rel IS NOT DISTINCT FROM p_release
            AND v_claim_miljo IS NOT DISTINCT FROM p_miljo,
        v_status = 'utfort' AND EXISTS (
            SELECT 1 FROM public.artefakt a
             WHERE a.tenant = p_tenant AND a.oppdrag_id = p_oppdrag
               AND a.release_id = p_release
               AND a.tilstand = 'promotert'),
        v_loggpost IS NOT NULL AND EXISTS (
            SELECT 1 FROM public.revisjonslogg r
             WHERE r.tenant = p_tenant AND r.id = v_loggpost
               AND r.beslutning = 'TILLAT'),
        v_eiermodul IS NOT DISTINCT FROM p_modul AND EXISTS (
            SELECT 1 FROM public.oppdragstype_register t
             WHERE t.oppdragstype = v_type
               AND t.eiermodul = p_modul),
        v_loggpost;
    PERFORM set_config('disponit.tenant', coalesce(v_forrige, ''), true);
END $$;

DROP FUNCTION aksepter_moduldeployment(TEXT, TEXT, TEXT, BIGINT, TEXT,
    TEXT, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, TEXT);

DROP FUNCTION attester_evidensfil(TEXT, TEXT, TEXT, JSONB, TEXT, TEXT);

CREATE FUNCTION attester_evidensfil(
    p_krav_id TEXT, p_sti TEXT, p_sha256 TEXT, p_punkter JSONB,
    p_aktor TEXT, p_drill_sha TEXT DEFAULT '',
    p_kjoringer TEXT DEFAULT NULL)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_sha TEXT; v_punkt TEXT; v_verdi TEXT; v_annet RECORD;
        v_drill TEXT;
BEGIN
    v_sha := lower(p_sha256);
    IF v_sha !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'attester_evidensfil: «%» er ingen sha256 — en'
            ' attest som ikke navngir bytene, binder ingenting', p_sha256
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- DRILLEN LESNINGEN HØRER TIL (Codex P1, PR #123). Tre av v3s
    -- evidenspunkter måles PÅ TVERS av runde-evidensen og
    -- drillartefaktet, men referatet navnga bare runden. Attesten
    -- bærer nå bytene til det drillartefaktet den ble regnet mot, og
    -- de er en del av identiteten: samme fil lest sammen med to
    -- ulike driller er to referater, ikke ett gjenbrukbart.
    -- Tom streng er den historiske lesningen uten drill (v1/v2);
    -- alt annet må navngi bytes.
    v_drill := lower(p_drill_sha);
    IF v_drill <> '' AND v_drill !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'attester_evidensfil: «%» er ingen sha256 —'
            ' drillartefaktet attesten hører til, navngis av bytene'
            ' sine', p_drill_sha
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_punkter IS NULL OR jsonb_typeof(p_punkter) <> 'object'
       OR p_punkter = '{}'::jsonb THEN
        RAISE EXCEPTION 'attester_evidensfil: ingen punkter — en lesning'
            ' uten måletall er ikke et referat'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- LÅSEN FØR KONTROLLEN (Codex P2, #121 runde 2).
    --
    -- I 049 var motsigelseskontrollen under gjorde jobben SAMMEN med
    -- nøkkelen: PK-en var `(sha256, punkt)`, så to samtidige attester om
    -- de samme bytene kolliderte i unikindeksen uansett hva SELECT-en
    -- rakk å se. Nøkkelbyttet over gir bort nettopp den serialiseringen
    -- — `(sha256, punkt, krav_id)` lar to revisjoner stå side om side,
    -- som er hele poenget, men da kan også to SESJONER som skriver hver
    -- sin revisjon begge se ingen konflikt og begge sette inn. Resultatet
    -- ville vært to immutable, motstridende referater om én fil: akkurat
    -- det kontrollen finnes for å hindre, tapt i et kappløp.
    --
    -- Låsen er transaksjonsscopet og slippes ved commit/rollback. Den
    -- nøkles på BYTENE, ikke på (bytes, punkt): hele kallet er ÉN
    -- fillesning, alle punktene hører til samme sha, og én lås pr. kall
    -- er både en overmengde av det invarianten trenger og fri for
    -- låserekkefølge mellom punktene. Attester skrives én gang pr. fil
    -- pr. grense, så det koster ingenting å ta den grovt.
    PERFORM pg_advisory_xact_lock(hashtextextended('evidensfil:' || v_sha,
                                                   0));
    FOR v_punkt, v_verdi IN
        SELECT j.k, j.v #>> '{}' FROM jsonb_each(p_punkter) AS j(k, v)
         ORDER BY j.k LOOP
        IF v_verdi IS NULL THEN
            RAISE EXCEPTION 'attester_evidensfil: punkt % mangler måletall',
                v_punkt USING ERRCODE = 'invalid_parameter_value';
        END IF;
        -- FØRST: «én fil har ett innhold», på tvers av kravrevisjoner —
        -- MEN innenfor SAMME drill-par (Codex P1, #123 runde 5).
        -- Sammenhengspunktene måles av (runde, drill)-PARET, og et
        -- mislykket akseptforsøk med feil drill committer sitt røde
        -- referat før basen avviser aksepten. Uten drill-leddet her
        -- ville det immutable røde referatet så sperret det RIKTIGE
        -- parets grønne verdi for alltid — nøkkelen tillater raden,
        -- men motsigelseskontrollen avviste den. To referater om samme
        -- bytes MOT SAMME DRILL skal fortsatt si det samme; to ulike
        -- drill-par er to målekontekster, og aksepten leser uansett
        -- bare raden for NØYAKTIG sitt par (oppslaget med
        -- `drill_sha256` nedenfor).
        SELECT * INTO v_annet FROM public.evidensfil_attest e
         WHERE e.sha256 = v_sha AND e.punkt = v_punkt
           AND e.drill_sha256 = v_drill
           AND (e.sti IS DISTINCT FROM p_sti
                OR e.maalt_verdi IS DISTINCT FROM v_verdi)
         LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'attester_evidensfil: sha256:% er alt'
                ' attestert for punkt % med et annet innhold («%» fra'
                ' «%», krav %) — én fil har ett innhold, og et referat'
                ' som spriker fra det lagrede er en programfeil',
                v_sha, v_punkt, v_annet.maalt_verdi, v_annet.sti,
                v_annet.krav_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        -- SÅ: replay for NØYAKTIG dette kravet er en no-op (SP-2), og
        -- en ny kravrevisjon får sin egen rad om de samme bytene.
        -- Innholdet er alt bevist likt av kontrollen over, så det som
        -- gjenstår er om DENNE revisjonens rad finnes.
        IF NOT EXISTS (SELECT 1 FROM public.evidensfil_attest e
                        WHERE e.sha256 = v_sha AND e.punkt = v_punkt
                          AND e.krav_id = p_krav_id
                          AND e.drill_sha256 = v_drill) THEN
            INSERT INTO public.evidensfil_attest (sha256, punkt, krav_id,
                drill_sha256, sti, maalt_verdi, aktor, attestert_av)
            VALUES (v_sha, v_punkt, p_krav_id, v_drill, p_sti, v_verdi,
                    p_aktor, session_user);
        END IF;
    END LOOP;
    -- IDENTITETSREFERATET (053): hvilke kjøringer filen navngir —
    -- eget bord, for `akseptkrav_punkt`-låsen gjør at listen aldri kan
    -- være et registerpunkt (et registrert krav utvides ikke). Samme
    -- immutabilitet og samme to-identitets-regel som punktattestene:
    -- SP-2-replay er no-op, samme nøkkel med en ANNEN liste er to
    -- motstridende referater om én fil og skal høres.
    IF p_kjoringer IS NOT NULL THEN
        IF p_kjoringer !~ '^[1-9][0-9]*(,[1-9][0-9]*)*$' THEN
            RAISE EXCEPTION 'attester_evidensfil: «%» er ingen kanonisk'
                ' kjøringsliste (kommadelte positive heltall)',
                p_kjoringer USING ERRCODE = 'invalid_parameter_value';
        END IF;
        SELECT * INTO v_annet FROM public.evidensfil_kjoringer e
         WHERE e.sha256 = v_sha AND e.krav_id = p_krav_id
           AND e.drill_sha256 = v_drill;
        IF FOUND THEN
            IF v_annet.kjoringer IS DISTINCT FROM p_kjoringer THEN
                RAISE EXCEPTION 'attester_evidensfil: sha256:% har alt'
                    ' et identitetsreferat for krav % og denne drillen'
                    ' («%») — et referat som navngir andre kjøringer'
                    ' («%») er en programfeil', v_sha, p_krav_id,
                    v_annet.kjoringer, p_kjoringer
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
        ELSE
            -- den NORMALISERTE digesten (Codex P2, #125 r2): raden
            -- valideres og slås opp som `v_drill` — å lagre kallerens
            -- råform ville latt en uppercase-attest passere skrivingen
            -- og aldri bli funnet av aksepten.
            INSERT INTO public.evidensfil_kjoringer (sha256, krav_id,
                drill_sha256, kjoringer, aktor, attestert_av)
            VALUES (v_sha, p_krav_id, v_drill, p_kjoringer,
                    p_aktor, session_user);
        END IF;
    END IF;
END $$;


CREATE FUNCTION aksepter_moduldeployment(
    p_modul_id TEXT, p_miljo TEXT, p_release_id TEXT, p_drill_id BIGINT,
    p_krav_id TEXT, p_e2e_tenant TEXT, p_e2e_artefakt UUID,
    p_evidens_sha TEXT, p_manifest_commit TEXT, p_ci_run TEXT,
    p_ci_commit TEXT, p_punkter JSONB, p_nokkel TEXT, p_aktor TEXT,
    p_kjoringer BIGINT[])
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_livslop TEXT; v_mangler TEXT; v_punkt RECORD; v_verdi JSONB;
        v_epoch BIGINT; v_avvik TEXT; v_drill_tenant TEXT;
        v_kandidat_oppdrag BIGINT; v_forrige_tenant TEXT; v_ref TEXT;
        v_holder BOOLEAN; v_ci RECORD; v_ci_attest BOOLEAN;
        v_evidens RECORD; v_ci_av TEXT; v_drill_artefakt TEXT;
        v_drillet_release TEXT; v_kjoring BIGINT; v_att RECORD;
        v_loggposter BIGINT[] := '{}';
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('modul:' || p_modul_id, 0));
    -- FORMEN FØRST (Codex P1, #117 runde 19). `p_evidens_sha` gikk rett
    -- inn i den immutable raden og inn i sammenligningen mot `kilde_ref`
    -- uten noe formkrav. En TOM streng var derfor en gyldig «hash», og
    -- en `kilde_ref` som endte på `@sha256:` var «enig» med den. En sha
    -- måles på formen sin før den brukes til noe som helst — samme
    -- disiplin som `hode_sha` i CI-attesten. Små bokstaver kreves, ikke
    -- normaliseres: raden og attesten skal ikke kunne stå med to
    -- skrivemåter av samme bytes.
    IF p_evidens_sha !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: «%» er ingen sha256 —'
            ' evidensfilen skal navngis av bytene sine', p_evidens_sha
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- …og PROVENIENSEN på samme måte (Codex P1, #117 runde 22).
    -- `p_manifest_commit` gikk rett inn i den uforanderlige raden uten
    -- noe formkrav, og uten noe bånd til commiten CI-attesten gjelder.
    -- En kaller med `disponit_modules_admin` kunne derfor oppgi en pen,
    -- grønt attestert `p_ci_commit` og skrive hva som helst i
    -- `p_manifest_commit` — og raden ville stått for alltid og påstått
    -- at manifestet og artefaktene fra ÉN commit var prøvd av en
    -- kjøring på en ANNEN. `m56-aksept.py` har alltid krevd likheten
    -- (punktene påberoper seg «grønn CI på akseptcommiten»), men et
    -- skript er ingen skranke for den som kaller definereren direkte.
    -- Små bokstaver kreves, ikke normaliseres — samme disiplin som
    -- evidenshashen over: raden og attesten skal ikke kunne stå med to
    -- skrivemåter av samme commit.
    IF p_manifest_commit !~ '^[0-9a-f]{40}$'
       OR p_ci_commit !~ '^[0-9a-f]{40}$' THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: «%»/«%» er ingen'
            ' commit-sha — en aksept navngir commiten den hviler på',
            p_manifest_commit, p_ci_commit
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_manifest_commit IS DISTINCT FROM p_ci_commit THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: manifestet er hentet'
            ' fra %, mens CI-kjøringen prøvde % — akseptcommiten er ÉN'
            ' commit, og punktene påberoper seg en grønn kjøring på'
            ' nøyaktig den', p_manifest_commit, p_ci_commit
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
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
        -- …og KJØRINGSLISTEN er like materiell (Codex P2, #125 r2): et
        -- replay med samme nøkkel men andre kjøringer er to
        -- motstridende referater av én aksept. Listen står i
        -- aksepthendelsen raden skrev — den er append-only, så
        -- sammenligningen er mot det som faktisk ble målt.
        IF NOT EXISTS (
            SELECT 1 FROM public.modulregister_hendelse h
             WHERE h.modul_id = p_modul_id
               AND h.hendelse = 'modulaksept'
               AND h.release_id = p_release_id
               AND h.miljo = p_miljo
               AND h.detalj -> 'kontrollkjoringer'
                       = pg_catalog.to_jsonb(p_kjoringer)) THEN
            RAISE EXCEPTION 'aksepter_moduldeployment: nøkkel % gjenbrukt'
                ' med andre kontrollkjøringer enn dem aksepten ble målt'
                ' på', p_nokkel
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
    SELECT d.tenant, d.kandidat_oppdrag, d.artefakt_sha256,
           d.drillet_release
      INTO v_drill_tenant, v_kandidat_oppdrag, v_drill_artefakt,
           v_drillet_release
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
    -- ------------------------------------------------------------
    -- A3: HVER OBSERVASJON MÅLES (Codex P1, #117 runde 15).
    --
    -- Løkka under kontrollerte at de fire feltene FANTES, aldri hva de
    -- sa. En kaller med `disponit_modules_admin` kunne derfor sende
    -- hjemmelagde grenser, måletall og kildereferanser for alle 21
    -- punktene og oppfylle A3 uten å ha vært innom `m56-aksept.py` —
    -- og observasjonene er uforanderlige når de først står der.
    --
    -- Tre ting måles nå, i denne rekkefølgen:
    --   (1) GRENSEN OG KILDETYPEN er REGISTERETS, ikke kallerens.
    --       Kalleren må gjenta dem ordrett; spriker de, er kallet en
    --       annen påstand enn kravet og avvises. (Feltene sendes
    --       fortsatt — SP-2-replaykontrollen over sammenligner dem med
    --       de lagrede radene, og et kall som utelot dem ville gjort
    --       den kontrollen innholdsløs.)
    --   (2) MÅLINGEN REGNES MOT KRAVET. `maalt_verdi` må være den
    --       verdien registeret sier en grønn observasjon har. Et punkt
    --       som ikke oppfyller kravet skrives ikke — det er ikke et
    --       punkt med en dårlig verdi, det er en aksept som ikke skal
    --       finnes.
    --   (3) KILDEN MÅ PEKE PÅ EVIDENS DENNE TRANSAKSJONEN SER.
    --       `evidensfil` må ende på hashen aksepten selv binder,
    --       `ci_kjoring` må navngi nøyaktig aksepradens egen kjøring og
    --       commit, `artefakt` må være et promotert artefakt på den
    --       aksepterte releasen, og `registerhendelse` en hendelse på
    --       denne modulen. Da kan `kilde_ref` ikke lenger være en
    --       fortelling; den er en peker som holder.
    -- ------------------------------------------------------------
    -- CI-KJØRINGEN MÅLES MOT ATTESTEN, IKKE MOT KALLERENS EGNE PARAMETRE
    -- (Codex P1, #117 runde 16). `p_ci_run` og `p_ci_commit` kommer fra
    -- samme kall som `kilde_ref`; at de tre er enige, sier ingenting om
    -- at noe er kjørt. Kravet står i `akseptkrav_ci`, og det som skal
    -- oppfylle det er referatet veien som spurte GitHub skrev ned.
    SELECT c.arbeidsflyt, c.hendelse, c.gren, c.konklusjon INTO v_ci
      FROM public.akseptkrav_ci c WHERE c.krav_id = p_krav_id;
    SELECT a.attestert_av INTO v_ci_av
      FROM public.ci_kjoringsattest a
     WHERE a.ci_run = p_ci_run
       AND a.arbeidsflyt = v_ci.arbeidsflyt
       AND a.hendelse = v_ci.hendelse
       AND a.gren = v_ci.gren
       AND a.konklusjon = v_ci.konklusjon
       AND a.hode_sha = lower(p_ci_commit);
    v_ci_attest := v_ci.arbeidsflyt IS NOT NULL AND v_ci_av IS NOT NULL;
    -- ------------------------------------------------------------
    -- FIRE ØYNE, MÅLT PÅ INNLOGGINGEN (Codex P1, #117 runde 19→22).
    --
    -- Skillet mellom attestant og akseptør var hittil bare en
    -- RETTIGHETSGRENSE: `disponit_modules_admin` har ikke EXECUTE på
    -- attestfunksjonene. En rettighetsgrense holder bare så lenge ingen
    -- innlogging står på begge sider av den — og migrator er medlem av
    -- BÅDE `disponit_modul_eier` og `disponit_modules_admin`. `WITH
    -- INHERIT FALSE` sperrer arv, ikke `SET ROLE`, så én autentisert
    -- identitet kunne skrive attesten, legge fullmakten ned, ta den
    -- andre opp og skrive aksepten som hviler på sin egen attest.
    -- Nøyaktig det samme gjelder enhver ny rolle som får medlemskap i
    -- eierrollen: en smalere GRANT flytter grensen, den håndhever den
    -- ikke.
    --
    -- Regelen hører derfor hjemme HER, der forutsetningen forbrukes, og
    -- den måles på `session_user` — den AUTENTISERTE identiteten, som
    -- `SET ROLE` ikke rører. Attesten aksepten hviler på må være skrevet
    -- av en ANNEN innlogging enn den som skriver aksepten. To fullmakter
    -- i én sesjon er én identitet, og én identitet er ikke fire øyne.
    -- ------------------------------------------------------------
    IF v_ci_av IS NOT NULL AND v_ci_av = session_user THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: CI-attesten for kjøring'
            ' % er skrevet av % — samme innlogging som skriver aksepten.'
            ' Attestanten er ikke akseptøren: referatet og aksepten som'
            ' hviler på det skal komme fra to autentiserte identiteter,'
            ' ikke fra to rolleskift i én sesjon', p_ci_run, v_ci_av
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_forrige_tenant := current_setting('disponit.tenant', true);
    PERFORM set_config('disponit.tenant', p_e2e_tenant, true);
    FOR v_punkt IN SELECT k.punkt, k.kilde_type, k.grenseverdi, k.maalt_krav
                     FROM public.akseptkrav_punkt k
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
        IF v_verdi ->> 'grenseverdi' IS DISTINCT FROM v_punkt.grenseverdi
           OR v_verdi ->> 'kilde_type' IS DISTINCT FROM v_punkt.kilde_type
        THEN
            RAISE EXCEPTION 'aksepter_moduldeployment: punkt % oppgir'
                ' grense «%» av type «%», mens kravet er «%» av type'
                ' «%» — grensen er registerets, ikke kallerens',
                v_punkt.punkt, v_verdi ->> 'grenseverdi',
                v_verdi ->> 'kilde_type', v_punkt.grenseverdi,
                v_punkt.kilde_type
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        IF v_verdi ->> 'maalt_verdi' IS DISTINCT FROM v_punkt.maalt_krav THEN
            RAISE EXCEPTION 'aksepter_moduldeployment: punkt % målte «%»,'
                ' men en grønn observasjon er «%» — en aksept skrives av'
                ' målinger som oppfyller kravet, ikke av målinger som'
                ' ikke gjør det', v_punkt.punkt, v_verdi ->> 'maalt_verdi',
                v_punkt.maalt_krav
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        v_ref := v_verdi ->> 'kilde_ref';
        IF v_punkt.kilde_type = 'evidensfil' THEN
            -- REFERATET FRA VEIEN SOM LESTE FILEN (Codex P1, runde 19).
            -- Den forrige formen sammenlignet `kilde_ref` med aksepten
            -- sin egen `p_evidens_sha` — to felter fra samme kall — og
            -- godtok hele halen av strengen uten å se på stien. Med en
            -- tom `p_evidens_sha` holdt en `kilde_ref` som endte på
            -- `@sha256:`, og siden `maalt_verdi` uansett må være
            -- registerets grønne fasit, kunne fire observasjoner om en
            -- fil som ikke fantes bli en immutabel aksept.
            --
            -- Punktet måles nå mot ATTESTEN: en immutabel rad, skrevet
            -- med eierrollens fullmakt av veien som faktisk hashet fila,
            -- som sier hvilken sti bytene lå på og hva filen bar for
            -- NØYAKTIG dette punktet. Kalleren kan bare gjenta den.
            --
            -- …OG ATTESTEN ER DENNE DRILLENS (Codex P1, PR #123).
            -- Tre av v3-punktene måles PÅ TVERS av runde-evidensen og
            -- drillartefaktet (`sammenheng_verdier`), men referatet ble
            -- skrevet med bare rundens sha, punktet og kravet i
            -- identiteten. Et forsøk som attesterte runde R sammen med
            -- drill A og deretter falt, etterlot altså en immutabel
            -- grønn attest som et senere direktekall kunne gjenbruke
            -- med en ANNEN drill B: aksepten fant attesten på
            -- evidenshashen alene og spurte aldri hvilken drill
            -- forholdet var regnet mot, så `evidens.pa_tvers_av_runder`
            -- kunne stå grønt selv om R målte en annen release enn den
            -- B drillet. Attesten navngir nå drillartefaktets bytes, og
            -- oppslaget krever at det er DE bytene drillraden aksepten
            -- hviler på ble registrert med: en måling regnet på tvers
            -- av to artefakter gjelder de to, ikke det ene.
            SELECT * INTO v_evidens FROM public.evidensfil_attest e
             WHERE e.sha256 = lower(p_evidens_sha)
               AND e.punkt = v_punkt.punkt AND e.krav_id = p_krav_id
               AND e.drill_sha256 = v_drill_artefakt;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'aksepter_moduldeployment: punkt % hviler'
                    ' på evidensfilen sha256:%, men ingen attest sier at'
                    ' den filen er lest og hva den bar for punktet —'
                    ' sammen med drillartefaktet sha256:%. En hash'
                    ' aksepten selv oppgir, beviser ingenting; det gjør'
                    ' referatet fra veien som leste',
                    v_punkt.punkt, lower(p_evidens_sha), v_drill_artefakt
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
            -- …og evidensattesten måles på samme fire øyne som
            -- CI-attesten over: den som LESTE filen skal ikke være den
            -- som skriver aksepten filens måletall bærer.
            IF v_evidens.attestert_av = session_user THEN
                RAISE EXCEPTION 'aksepter_moduldeployment: punkt % hviler'
                    ' på en evidensattest skrevet av % — samme innlogging'
                    ' som skriver aksepten. Den som leste filen og den som'
                    ' aksepterer på det den bar, skal være to autentiserte'
                    ' identiteter', v_punkt.punkt, v_evidens.attestert_av
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
            -- Stien er attestens, ikke kallerens: LIKHET, ikke hale.
            IF v_ref IS DISTINCT FROM
               (v_evidens.sti || '@sha256:' || lower(p_evidens_sha)) THEN
                RAISE EXCEPTION 'aksepter_moduldeployment: punkt % viser'
                    ' til evidensfilen «%», mens attesten leste «%@sha256:%»'
                    ' — en observasjon skal navngi DEN filen som ble lest,'
                    ' ikke en sti med riktig hale', v_punkt.punkt, v_ref,
                    v_evidens.sti, lower(p_evidens_sha)
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
            -- …og det er FILENS måletall som skal oppfylle kravet.
            IF v_evidens.maalt_verdi IS DISTINCT FROM v_punkt.maalt_krav THEN
                RAISE EXCEPTION 'aksepter_moduldeployment: punkt % — filen'
                    ' sha256:% bar «%», men en grønn observasjon er «%».'
                    ' Aksepten regner mot det filen SA, ikke mot det'
                    ' kallet gjentar', v_punkt.punkt, lower(p_evidens_sha),
                    v_evidens.maalt_verdi, v_punkt.maalt_krav
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
        ELSIF v_punkt.kilde_type = 'ci_kjoring' THEN
            IF v_ref IS DISTINCT FROM
               ('run ' || p_ci_run || ' @ ' || p_ci_commit) THEN
                RAISE EXCEPTION 'aksepter_moduldeployment: punkt % viser'
                    ' til CI-kjøringen «%», mens aksepten bærer «run % @'
                    ' %» — invariantpunktene hviler HELT på den ene'
                    ' kjøringen raden navngir', v_punkt.punkt, v_ref,
                    p_ci_run, p_ci_commit
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
            -- …og den kjøringen må være ATTESTERT (Codex P1, runde 16):
            -- referatet fra veien som spurte GitHub må si at kravets
            -- workflow kjørte grønt, på kravets hendelse og gren, for
            -- nøyaktig akseptcommiten. Uten den er «run X @ Y» bare to
            -- av kallerens egne strenger som ligner på hverandre.
            IF NOT v_ci_attest THEN
                RAISE EXCEPTION 'aksepter_moduldeployment: punkt % hviler'
                    ' på CI-kjøring %, men ingen attest sier at kravets'
                    ' workflow (%) kjørte % på %/% for commit % — en'
                    ' kjøring aksepten selv navngir, beviser ingenting;'
                    ' det gjør referatet fra veien som spurte',
                    v_punkt.punkt, p_ci_run,
                    coalesce(v_ci.arbeidsflyt, '<krav uten ci-krav>'),
                    coalesce(v_ci.konklusjon, '?'),
                    coalesce(v_ci.hendelse, '?'), coalesce(v_ci.gren, '?'),
                    lower(p_ci_commit)
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
        ELSIF v_punkt.kilde_type = 'artefakt' THEN
            -- Formen FØRST, i sin egen IF: PostgreSQL lover ingen
            -- kortslutning av `OR`, så en `v_ref::uuid` ved siden av
            -- formkontrollen kunne blitt evaluert likevel og kastet
            -- `invalid_text_representation` i stedet for feilen her.
            v_holder := v_ref ~
                ('^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
                 || '[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$');
            IF v_holder THEN
                v_holder := EXISTS (SELECT 1 FROM public.artefakt a
                                     WHERE a.tenant = p_e2e_tenant
                                       AND a.artefakt_id = v_ref::uuid
                                       AND a.modul_id = p_modul_id
                                       AND a.release_id = p_release_id
                                       AND a.tilstand = 'promotert');
            END IF;
            IF NOT v_holder THEN
                RAISE EXCEPTION 'aksepter_moduldeployment: punkt % viser'
                    ' til artefaktet «%», som ikke er et promotert'
                    ' artefakt fra %/% for tenant % — et bevis som ikke'
                    ' finnes, beviser ingenting', v_punkt.punkt, v_ref,
                    p_modul_id, p_release_id, p_e2e_tenant
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
        ELSE   -- 'registerhendelse'
            -- `^[0-9]{1,18}$`: sifre ALENE holder ikke, for en id som er
            -- for stor for BIGINT kaster på castet (samme grunn som over).
            --
            -- …OG HENDELSEN MÅ VÆRE DRILLENS (Codex P2, PR #123).
            -- 052s to drillpunkter påberoper seg `rollback_drill`-
            -- hendelsen akkurat DENNE drillraden skrev, men kontrollen
            -- spurte bare om IDen var EN hendelse på modulen.
            -- `m56-aksept.py` slår opp riktig hendelse; et skript er
            -- ingen skranke for den som holder `disponit_modules_admin`
            -- og kaller definereren direkte, og en aktivering, en
            -- nødstopp eller en eldre drills hendelse ville passert
            -- like godt — og stått for alltid som kildereferansen for
            -- BEGGE drillobservasjonene. Typen og drillbåndet måles nå
            -- her, der forutsetningen forbrukes.
            -- Sammenligningen er TEKSTLIG (`->>` mot `p_drill_id::text`)
            -- og ikke et cast: PostgreSQL lover ingen rekkefølge på
            -- predikatene, og en `detalj` uten et tall i `drill_id`
            -- skal gi denne feilmeldingen, ikke `invalid_text_
            -- representation` (samme grunn som uuid-formen over).
            v_holder := v_ref ~ '^[0-9]{1,18}$';
            IF v_holder THEN
                v_holder := EXISTS (
                    SELECT 1 FROM public.modulregister_hendelse h
                     WHERE h.id = v_ref::bigint
                       AND h.modul_id = p_modul_id
                       AND h.hendelse = 'rollback_drill'
                       AND h.detalj ->> 'drill_id' = p_drill_id::text);
            END IF;
            IF NOT v_holder THEN
                RAISE EXCEPTION 'aksepter_moduldeployment: punkt % viser'
                    ' til registerhendelsen «%», som ikke er en hendelse'
                    ' på % — nærmere bestemt ikke `rollback_drill`-'
                    'hendelsen drill % skrev. Et drillpunkt hviler på'
                    ' hendelsen DEN drillen skrev, ikke på en hvilken som'
                    ' helst hendelse på modulen',
                    v_punkt.punkt, v_ref, p_modul_id, p_drill_id
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
        END IF;
        INSERT INTO public.modulaksept_punkt (modul_id, miljo, release_id,
            krav_id, punkt, grenseverdi, maalt_verdi, kilde_type, kilde_ref)
        VALUES (p_modul_id, p_miljo, p_release_id, p_krav_id,
                v_punkt.punkt, v_punkt.grenseverdi,
                v_punkt.maalt_krav, v_punkt.kilde_type, v_ref);
    END LOOP;
    -- ------------------------------------------------------------
    -- KONTROLLKJØRINGENE MÅLES HER (053, Codex P1 på #125): et skript
    -- er ingen skranke for den som kaller definereren direkte (049s
    -- egen lærdom, #117 runde 22, samme form som drillens tre oppdrag
    -- i runde 5). Kallet sender HVILKE kjøringer runden består av;
    -- basen måler hver av dem selv, NÅ — mot releasen som faktisk ble
    -- drillet, i miljøet som aksepteres, for modulen raden gjelder.
    --
    -- Og identitetene er EVIDENSENS, ikke kallerens: listen må være
    -- ordrett den verifikatoren attesterte for nøyaktig denne
    -- evidensfilen, denne grensen og DETTE drillartefaktet
    -- (`identiteter.kjoringer`-raden, drillscopet som resten). En
    -- kaller med deployfullmakten kan dermed verken finne på ti IDer
    -- eller låne ti andre grønne kjøringer — de han sender må være dem
    -- filen bar, og de må bestå basens egen måling i dette øyeblikket.
    -- ------------------------------------------------------------
    IF p_kjoringer IS NULL OR cardinality(p_kjoringer) = 0 THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: ingen kontrollkjøringer'
            ' navngitt — en aksept hviler på kjøringer basen selv kan'
            ' måle, ikke på et aggregat alene'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF (SELECT count(DISTINCT u) FROM unnest(p_kjoringer) u)
           <> cardinality(p_kjoringer) THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: kontrollkjøringene'
            ' gjentar et oppdrag — ett oppdrag er én kjøring, aldri to'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO v_evidens FROM public.evidensfil_kjoringer e
     WHERE e.sha256 = lower(p_evidens_sha)
       AND e.krav_id = p_krav_id
       AND e.drill_sha256 = v_drill_artefakt;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: ingen attest sier'
            ' hvilke kjøringer evidensfilen sha256:% navngir for krav %'
            ' og dette drillartefaktet — identitetene er referatets, og'
            ' et referat som ikke er skrevet, kan ikke leses',
            lower(p_evidens_sha), p_krav_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_evidens.attestert_av = session_user THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: kjøringsidentitetene'
            ' er attestert av % — samme innlogging som skriver aksepten;'
            ' to identiteter, som for alle andre referater',
            v_evidens.attestert_av
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_evidens.kjoringer IS DISTINCT FROM
       pg_catalog.array_to_string(p_kjoringer, ',') THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: kallets kjøringer (%)'
            ' er ikke dem referatet navngir (%) — identitetene er'
            ' evidensens, ikke kallerens',
            pg_catalog.array_to_string(p_kjoringer, ','),
            v_evidens.kjoringer
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    FOREACH v_kjoring IN ARRAY p_kjoringer LOOP
        SELECT * INTO v_att FROM public.maal_kjoringsattest(
            p_e2e_tenant, v_kjoring, v_drillet_release, p_miljo,
            p_modul_id);
        IF NOT (v_att.kvittering_ok AND v_att.claim_release_ok
                AND v_att.artefakt_ok AND v_att.revisjonsrad_ok
                AND v_att.modul_ok AND v_att.loggpost IS NOT NULL) THEN
            RAISE EXCEPTION 'aksepter_moduldeployment: kontrollkjøring %'
                ' attesteres ikke av basen NÅ mot (%, %, %) —'
                ' kvittering=% claim=% artefakt=% revisjonsrad=%'
                ' modul=%. Artefaktets tall er transkripsjonen;'
                ' akseptraden hviler på basens eget svar',
                v_kjoring, v_drillet_release, p_miljo, p_modul_id,
                v_att.kvittering_ok, v_att.claim_release_ok,
                v_att.artefakt_ok, v_att.revisjonsrad_ok, v_att.modul_ok
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        v_loggposter := v_loggposter || v_att.loggpost;
    END LOOP;
    IF (SELECT count(DISTINCT l) FROM unnest(v_loggposter) l)
           <> cardinality(p_kjoringer) THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: % kjøringer bærer %'
            ' distinkte revisjonsrader — én rad per kjøring, og en rad'
            ' delt av flere er én', cardinality(p_kjoringer),
            (SELECT count(DISTINCT l) FROM unnest(v_loggposter) l)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.tenant',
                       coalesce(v_forrige_tenant, ''), true);
    SELECT module_epoch INTO v_epoch FROM public.modulhode
     WHERE modul_id = p_modul_id;
    INSERT INTO public.modulregister_hendelse (modul_id, hendelse,
        release_id, miljo, module_epoch, aktor, detalj)
    VALUES (p_modul_id, 'modulaksept', p_release_id, p_miljo, v_epoch,
            p_aktor, jsonb_build_object(
                'drill_id', p_drill_id, 'krav_id', p_krav_id,
                'e2e_artefakt_id', p_e2e_artefakt::text,
                'evidens_jsonl_sha256', p_evidens_sha,
                'kontrollkjoringer', pg_catalog.to_jsonb(p_kjoringer),
                'ci_run', p_ci_run, 'ci_commit', p_ci_commit));
END $$;

RESET ROLE;

-- ACL-bildet, eksplisitt (052-mønsteret): PUBLIC fratas
-- default-EXECUTE på begge de nye signaturene; målingen kalles av
-- sjekklisten og aksepten under `disponit_modules_admin`.
SET LOCAL ROLE disponit_modul_eier;
REVOKE ALL ON FUNCTION maal_kjoringsattest(TEXT, BIGINT, TEXT, TEXT,
    TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION maal_kjoringsattest(TEXT, BIGINT, TEXT, TEXT,
    TEXT) TO disponit_modules_admin;
REVOKE ALL ON FUNCTION aksepter_moduldeployment(TEXT, TEXT, TEXT,
    BIGINT, TEXT, TEXT, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT,
    TEXT, BIGINT[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION aksepter_moduldeployment(TEXT, TEXT, TEXT,
    BIGINT, TEXT, TEXT, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT,
    TEXT, BIGINT[]) TO disponit_modules_admin;
REVOKE ALL ON FUNCTION attester_evidensfil(TEXT, TEXT, TEXT, JSONB,
    TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION attester_evidensfil(TEXT, TEXT, TEXT, JSONB,
    TEXT, TEXT, TEXT) TO disponit_ci_verifikator;
RESET ROLE;
