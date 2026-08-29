-- 055: aktøren er materiell i hvert nøkkel-replay (Codex etterkontroll
-- på #129, K2-fellesfiks for begge akseptformene + drillregistreringen).
--
-- SP-2 sier: samme nøkkel + samme innhold -> samme hendelse; samme nøkkel
-- med ANNET innhold skal høres. Materialitetslistene sammenlignet alt
-- kallergitt innhold — unntatt aktøren. Et nytt kall med en annen aktør
-- returnerte derfor suksess, mens den immutable raden fortsatt bar den
-- opprinnelige: to parter kunne hver tro at DE skrev hendelsen, og
-- revisjonssporet pekte bare på den ene. Nå er aktøren med i
-- sammenligningen i alle tre: `aksepter_moduldeployment`,
-- `aksepter_plattformmodul` og `registrer_moduldrill`.
--
-- VURDERT OG UTELATT: `attester_evidensfil`. Attesten er
-- innholdsadressert (sha + punkt + krav + drillscope), ikke
-- nøkkel-adressert, og to attestanter som avgir SAMME påstand om samme
-- bytes er ikke en motsigelse — no-op-en der er designet (fire-øyne-
-- maskineriet bor i attestert_av/session_user-skillet, ikke her).
--
-- Funksjonskroppene under er de GJELDENDE (lest med pg_get_functiondef
-- fra migrert base), med aktør-vilkåret som eneste endring — aldri
-- skrevet fra hukommelsen.

-- Kroppene eies av modul_eier (053/054); CREATE OR REPLACE må kjøres
-- i samme eierkontekst — som i originalene.
SET LOCAL ROLE disponit_modul_eier;

CREATE OR REPLACE FUNCTION public.aksepter_moduldeployment(p_modul_id text, p_miljo text, p_release_id text, p_drill_id bigint, p_krav_id text, p_e2e_tenant text, p_e2e_artefakt uuid, p_evidens_sha text, p_manifest_commit text, p_ci_run text, p_ci_commit text, p_punkter jsonb, p_nokkel text, p_aktor text, p_kjoringer bigint[])
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE v_livslop TEXT; v_mangler TEXT; v_punkt RECORD; v_verdi JSONB;
        v_epoch BIGINT; v_avvik TEXT; v_drill_tenant TEXT;
        v_kandidat_oppdrag BIGINT; v_forrige_tenant TEXT; v_ref TEXT;
        v_holder BOOLEAN; v_ci RECORD; v_ci_attest BOOLEAN;
        v_evidens RECORD; v_ci_av TEXT; v_drill_artefakt TEXT;
        v_drillet_release TEXT; v_kjoring BIGINT; v_att RECORD;
        v_loggposter BIGINT[] := '{}'; v_inflight BIGINT;
        v_kontrolltype TEXT; v_antall_typer INT;
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
           AND ci_run = p_ci_run AND ci_commit = p_ci_commit
           -- AKTØREN ER MATERIELL (Codex etterkontroll på #129): raden er
           -- immutabel og bærer hvem som aksepterte. Et «replay» fra en
           -- ANNEN aktør som fikk suksess tilbake, hadde gitt et
           -- revisjonsspor der to parter mener de skrev hendelsen — og
           -- bare den ene står der.
           AND aktor = p_aktor;
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
           d.drillet_release, d.inflight_oppdrag
      INTO v_drill_tenant, v_kandidat_oppdrag, v_drill_artefakt,
           v_drillet_release, v_inflight
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
    -- …og KRAVETS ANTALL, i porten selv (Codex P2, #125 r3): «9/10 er
    -- rødt» håndheves ellers bare av fil- og skriptlaget, og et
    -- direktekall med ett gyldig oppdrag kunne skrive raden. Terskelen
    -- bor i sin egen definer (moduldrill_min_ventetid_s-formen), og
    -- testene binder den til filgrensens 10/10.
    IF cardinality(p_kjoringer) < public.modulaksept_min_kjoringer() THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: % kontrollkjøringer'
            ' navngitt, kravet er minst % — 9/10 er rødt, ikke «nesten»',
            cardinality(p_kjoringer), public.modulaksept_min_kjoringer()
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
    -- KONTROLLTYPEN ER REGISTERETS, OG DEN MÅ VÆRE ENTYDIG (Codex
    -- P1, #125 r4). Runde 3 deriverte den av drillens inflight-oppdrag
    -- — men det oppdraget er selv ubundet i type
    -- (`registrer_moduldrill` måler eiermodul, aldri typen), så en
    -- drill kjørt helt på en annen registrert type ville gjort DEN til
    -- fasit. Fasiten er registerets egen: modulen som aksepteres skal
    -- ha NØYAKTIG ÉN registrert oppdragstype — kontrolltypen — og både
    -- drillens inflight-oppdrag og hver av de ti kjøringene må bære
    -- den. Får en modul legitimt flere typer en dag, må porten
    -- revideres SYNLIG; til da er flertydighet en stopp, aldri et
    -- valg — også når noen med deployfullmakten registrerer en type
    -- til for å komme forbi: da låser de aksepten, de åpner den ikke.
    SELECT min(t.oppdragstype), count(*)
      INTO v_kontrolltype, v_antall_typer
      FROM public.oppdragstype_register t
     WHERE t.eiermodul = p_modul_id;
    IF v_antall_typer IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: modul % har %'
            ' registrerte oppdragstyper — aksepten krever nøyaktig én'
            ' entydig kontrolltype; flertydighet er en stopp, aldri et'
            ' valg', p_modul_id, coalesce(v_antall_typer, 0)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- …og DRILLEN selv må ha kjørt kontrolltypen: inflight-oppdraget
    -- er drillens claim-beviste kjøring, og en drill målt på en annen
    -- type er ikke denne modulens kontrolldrill.
    IF NOT EXISTS (SELECT 1 FROM public.oppdrag o
                    WHERE o.tenant = p_e2e_tenant AND o.id = v_inflight
                      AND o.oppdragstype = v_kontrolltype) THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: drillens'
            ' inflight-oppdrag % bærer ikke kontrolltypen «%» — drillen'
            ' må selv ha kjørt det løpet aksepten binder',
            v_inflight, v_kontrolltype
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    FOREACH v_kjoring IN ARRAY p_kjoringer LOOP
        SELECT * INTO v_att FROM public.maal_kjoringsattest(
            p_e2e_tenant, v_kjoring, v_drillet_release, p_miljo,
            p_modul_id, v_kontrolltype);
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
END $function$
;

CREATE OR REPLACE FUNCTION public.aksepter_plattformmodul(p_modul_id text, p_manifest_commit text, p_manifest_sha text, p_grense_id text, p_ci_run text, p_ci_commit text, p_punkter jsonb, p_nokkel text, p_aktor text)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
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
           OR v_rad.ci_commit IS DISTINCT FROM lower(p_ci_commit)
           -- AKTØREN ER MATERIELL (Codex etterkontroll på #129), samme
           -- regel som i deployment-aksepten: hendelsen bærer hvem som
           -- aksepterte, og en annen aktørs kall er ikke en replay.
           OR v_rad.aktor IS DISTINCT FROM p_aktor THEN
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
        -- STRENG OBJEKTKONTRAKT (SP-2-regresjonen, Codex P2 runde 4 —
        -- K2-valg b): skriveveien lagret et KANONISERT punkt (målt uten
        -- `begrunnelse`, skopet uten kildefeltene), mens replayet
        -- sammenlignet kallerens RÅ JSON mot raden. Samme kall som
        -- nettopp lyktes, feilet dermed ved gjentakelse. I stedet for å
        -- kanonisere i to veier avvises fremmede felter på FØRSTE kall:
        -- det som godtas, er nøyaktig det som lagres, og replayets
        -- likhet er ekte likhet. Et felt uten mening for statusen er
        -- ikke støy — det er en påstand ingen rad kan bære.
        SELECT string_agg(j.key, ', ' ORDER BY j.key) INTO v_avvik
          FROM pg_catalog.jsonb_object_keys(v_verdi)
               WITH ORDINALITY AS j(key, i)
         WHERE (v_punkt.maalt_krav = '<utenfor grensen>'
                AND j.key NOT IN ('status', 'begrunnelse'))
            OR (v_punkt.maalt_krav <> '<utenfor grensen>'
                AND j.key NOT IN ('status', 'grenseverdi', 'maalt_verdi',
                                  'kilde_type', 'kilde_ref'));
        IF v_avvik IS NOT NULL THEN
            RAISE EXCEPTION 'aksepter_plattformmodul: punkt % bærer'
                ' fremmede felter (%) for statusen sin — det som godtas'
                ' er nøyaktig det som lagres, og et replay er den samme'
                ' observasjonen', v_punkt.punkt, v_avvik
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
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
END $function$
;

CREATE OR REPLACE FUNCTION public.registrer_moduldrill(p_modul_id text, p_miljo text, p_drillet text, p_rullback text, p_kandidat text, p_tenant text, p_inflight_oppdrag bigint, p_rullback_oppdrag bigint, p_kandidat_oppdrag bigint, p_module_epoch bigint, p_artefakt_sha text, p_nokkel text, p_aktor text, p_utfort_ts timestamp with time zone)
 RETURNS bigint
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
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
           AND utfort_ts = p_utfort_ts
           -- AKTØREN ER MATERIELL (Codex etterkontroll på #129), samme
           -- regel som i akseptfunksjonene: samme nøkkel fra en annen
           -- aktør er to påstander om hvem som drillet, ikke en replay.
           AND aktor = p_aktor;
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
        -- …OG UTFALLET ER UTFORT (Codex P1, #123 runde 5).
        -- `maal_rent_utfall` måler avtrykket, ikke statusen: en signert
        -- `feilet`-kvittering med et promotert artefakt bar hele
        -- avtrykket og passerte alle leddene rundt — og en direkte
        -- kaller med deployfullmakten kunne skrive `claim_stopp_ok =
        -- true` for en rullbakk som FEILET. «Rullbakken fullførte det
        -- ventende oppdraget selv» (klarsignalet §1.1a) betyr utført —
        -- det er nøyaktig det drillskriptet krever (`st_rb == utfort`),
        -- og basen skal aldri være svakere enn skriptet den måler for.
        AND EXISTS (SELECT 1 FROM public.oppdrag o
                     WHERE o.tenant = p_tenant
                       AND o.id = p_rullback_oppdrag
                       AND o.status = 'utfort')
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
END $function$
;

RESET ROLE;
