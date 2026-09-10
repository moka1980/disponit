-- 180 — M-6: er dette svaret klart til å gå ut? (svar fra postboksen,
-- PR 3).
--
-- EIERVEDTAK 10/9, andre runde: menneskets eget svar er INGEN
-- agenthandling. Første utkast av denne PR-en la sendingen gjennom
-- policyporten med en egen handling og fire øyne — mønsteret fra M-17,
-- der PLATTFORMEN finner på teksten. Her skriver mennesket den selv, og
-- da er policyporten feil verktøy: den er bygget for å holde agenten i
-- bånd, ikke brukeren. Målet er at kunden skal lese og svare på ett
-- sted, uten å åpne Outlook.
--
-- Døra består, men svarer på et annet spørsmål: kan dette svaret gå ut
-- NÅ? Den regner det ferdig ett sted, fordi et fakta som regnes to
-- steder blir to fakta.
--
-- De fem tingene som må stemme, og hvorfor:
--   * utkastet er klart til sending — mennesket har trykket send (179).
--   * meldingen lever — et svar til en slettet melding har ingen tråd.
--   * kilden er AKTIV og har SENDETILGANG (178) — uten `Mail.Send` i
--     samtykket ville Graph avvist sendingen etter at policyen sa ja.
--   * leverandørens melding-id finnes — den ER tråden vi svarer i.
--   * avsenderhashen finnes — svaret går til den som skrev, og til
--     ingen andre. Selve adressen ligger kryptert i meldingen og
--     dekrypteres først ved claim.
-- ------------------------------------------------------------
-- STATUSEN `sendes`: MENNESKET HAR TRYKKET SEND.
--
-- 179 ga utkastet `godkjent` — et menneskes ja til at PLATTFORMEN kunne
-- sende. Nå er det mennesket selv som sender, og da trengs et annet ord:
-- `sendes` betyr «i kø hos bakgrunnsprosessen», ikke «godkjent for en
-- agent». `godkjent` blir stående for den dagen plattformen selv
-- foreslår svar — da er det igjen en godkjenning, og da hører
-- policyporten hjemme.
--
-- Hvorfor køen finnes i det hele tatt: nøkkelen til postboksen er med
-- vilje utenfor web-API-ets rekkevidde (088), så et innbrudd der ikke
-- gir noen retten til å sende i kundens navn. Bakgrunnsprosessen har
-- den. Prisen er noen minutter, og flaten sier det.
ALTER TABLE epost_utkast DROP CONSTRAINT IF EXISTS utkast_status_lukket;
ALTER TABLE epost_utkast ADD CONSTRAINT utkast_status_lukket
    CHECK (status IN ('foreslatt', 'godkjent', 'sendes', 'forkastet',
                      'brukt_manuelt', 'sendt', 'feilet'));

ALTER TABLE epost_utkast ADD COLUMN sendt_ts TIMESTAMPTZ;
ALTER TABLE epost_utkast ADD COLUMN feilgrunn TEXT;

CREATE OR REPLACE FUNCTION epost_utkast_vakt()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE nj jsonb; oj jsonb; kol TEXT; v_slettet TIMESTAMPTZ;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT m.slettet_ts INTO v_slettet
          FROM public.epost_melding m
         WHERE m.tenant = NEW.tenant AND m.melding_id = NEW.melding_id
         FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'epost_utkast: meldingen er ikke synlig for'
                ' vakten — utkast skrives bare under en melding vakten'
                ' kan lese og låse'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF v_slettet IS NOT NULL THEN
            RAISE EXCEPTION 'epost_utkast: meldingen er reapet — utkast'
                ' skrives ikke til en slettet melding'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.slettet_ts IS NOT NULL THEN
            RAISE EXCEPTION 'epost_utkast: et utkast fødes LEVENDE —'
                ' reap-merket settes bare av reap-overgangen'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.status <> 'foreslatt' THEN
            RAISE EXCEPTION 'epost_utkast: et utkast fødes foreslått —'
                ' sending og forkasting er egne, målte overganger'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.avgjort_ts IS NOT NULL OR NEW.avgjort_av IS NOT NULL
           OR NEW.sendt_ts IS NOT NULL THEN
            RAISE EXCEPTION 'epost_utkast: dommen settes av overgangen,'
                ' ikke av fødselen'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        NEW.opprettet := pg_catalog.now();
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'epost_utkast: % avvist — utkast reapes, de'
            ' slettes aldri', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.slettet_ts IS NOT NULL THEN
        RAISE EXCEPTION 'epost_utkast: raden er alt reapet og immutabel'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    nj := to_jsonb(NEW); oj := to_jsonb(OLD);
    IF nj->>'slettet_ts' IS NOT NULL THEN
        FOREACH kol IN ARRAY ARRAY['tekst_kryptert', 'nonce',
                                   'key_id'] LOOP
            IF (nj->kol) IS DISTINCT FROM 'null'::jsonb THEN
                RAISE EXCEPTION 'epost_utkast: reaping krever at'
                    ' payloadkolonnen % blir NULL', kol
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            nj := nj - kol; oj := oj - kol;
        END LOOP;
        nj := nj - 'slettet_ts'; oj := oj - 'slettet_ts';
        IF nj IS DISTINCT FROM oj THEN
            RAISE EXCEPTION 'epost_utkast: bare payload og slettet_ts'
                ' endres ved reaping — resten er revisjonsevidens'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    -- Overgangene. `sendes` er menneskets sendekommando; `sendt` og
    -- `feilet` er bakgrunnsprosessens svar, aldri noe et menneske setter.
    IF NOT ((OLD.status = 'foreslatt'
             AND NEW.status IN ('sendes', 'godkjent', 'forkastet',
                                'brukt_manuelt'))
            OR (OLD.status = 'godkjent'
                AND NEW.status IN ('sendes', 'forkastet', 'sendt'))
            OR (OLD.status = 'sendes'
                AND NEW.status IN ('sendt', 'feilet', 'forkastet'))
            OR (OLD.status = 'feilet' AND NEW.status = 'sendes')) THEN
        RAISE EXCEPTION 'epost_utkast: overgang % -> % finnes ikke —'
            ' teksten er append-only, og sendt/forkastet er terminale',
            OLD.status, NEW.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status IN ('godkjent', 'sendes', 'forkastet', 'brukt_manuelt')
       AND (NEW.avgjort_ts IS NULL OR NEW.avgjort_av IS NULL) THEN
        RAISE EXCEPTION 'epost_utkast: en beslutning har en aktør og et'
            ' tidspunkt' USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- SENDINGEN skriver ALDRI om hvem som ba om den. Et sendt svar må
    -- for alltid vise hvem som slapp det ut.
    IF NEW.status IN ('sendt', 'feilet')
       AND (NEW.avgjort_ts IS DISTINCT FROM OLD.avgjort_ts
            OR NEW.avgjort_av IS DISTINCT FROM OLD.avgjort_av) THEN
        RAISE EXCEPTION 'epost_utkast: sendingen skriver ikke om'
            ' beslutningen' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status = 'sendt' AND NEW.sendt_ts IS NULL THEN
        RAISE EXCEPTION 'epost_utkast: et sendt svar har et tidspunkt'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.sendt_ts IS NOT NULL
       AND NEW.sendt_ts IS DISTINCT FROM OLD.sendt_ts THEN
        RAISE EXCEPTION 'epost_utkast: sendingstidspunktet er frosset'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    nj := nj - 'status' - 'avgjort_ts' - 'avgjort_av' - 'sendt_ts'
             - 'feilgrunn';
    oj := oj - 'status' - 'avgjort_ts' - 'avgjort_av' - 'sendt_ts'
             - 'feilgrunn';
    IF nj IS DISTINCT FROM oj THEN
        RAISE EXCEPTION 'epost_utkast: bare status, beslutningen og'
            ' sendingen endres — teksten og resten av raden er immutable'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

-- Begge dørene eies av CLAIMEREN, som resten av M-6s dører (176/179):
-- sendedøra kaller `m6_evidens`, som er claimer-eid og lukket for
-- PUBLIC — en migrator-eid definer ville fått «permission denied» der.
-- Claimeren leser kilden gjennom kolonnegrantet fra 175, og `scope`
-- (178) sto ikke i det: uten denne linja svarer døra «permission
-- denied for table epost_kilde» i stedet for å si om boksen kan sende.
GRANT SELECT (scope) ON epost_kilde TO disponit_m37_claimer;

SET LOCAL ROLE disponit_m37_claimer;

CREATE FUNCTION m6_for_svar(p_tenant TEXT, p_utkast_id UUID)
RETURNS TABLE(utkast_status TEXT, melding_lever BOOLEAN,
              kilde_aktiv BOOLEAN, kilde_kan_svare BOOLEAN,
              har_leverandor_id BOOLEAN, har_avsender BOOLEAN,
              melding_id UUID, kilde_id UUID)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm6_for_svar');
    RETURN QUERY
    SELECT u.status,
           m.slettet_ts IS NULL,
           k.status = 'aktiv',
           position(lower('https://graph.microsoft.com/Mail.Send')
                    in lower(coalesce(k.scope, ''))) > 0,
           btrim(coalesce(m.leverandor_melding_id, '')) <> '',
           btrim(coalesce(m.avsender_hash, '')) <> '',
           m.melding_id, k.kilde_id
      FROM public.epost_utkast u
      JOIN public.epost_melding m
        ON m.tenant = u.tenant AND m.melding_id = u.melding_id
      JOIN public.epost_kilde k
        ON k.tenant = m.tenant AND k.kilde_id = m.kilde_id
     WHERE u.tenant = p_tenant AND u.utkast_id = p_utkast_id
       AND u.slettet_ts IS NULL;
END $$;
REVOKE ALL ON FUNCTION m6_for_svar(TEXT, UUID) FROM PUBLIC;

-- Menneskets sendekommando. Egen dør, ikke en utvidelse av
-- `m6_avgjor_utkast`: den døra er dommer over et FORSLAG, denne er en
-- ordre om at teksten skal ut. Den nekter alt som ikke kan sendes, med
-- sin egen setning — så flaten slipper å gjette.
CREATE FUNCTION m6_send_svaret(p_tenant TEXT, p_utkast_id UUID,
                               p_aktor TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_gammel TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm6_send_svaret');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm6_send_svaret: en sending har en avsender'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO r FROM public.m6_for_svar(p_tenant, p_utkast_id);
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm6_send_svaret: utkastet finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    v_gammel := r.utkast_status;
    IF v_gammel = 'sendes' THEN
        RETURN 'sendes';                           -- gjenspill: stille ja
    END IF;
    IF v_gammel NOT IN ('foreslatt', 'godkjent', 'feilet') THEN
        RAISE EXCEPTION 'm6_send_svaret: utkastet er % — bare et utkast'
            ' som ikke alt er sendt eller forkastet, kan sendes', v_gammel
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT r.melding_lever THEN
        RAISE EXCEPTION 'm6_send_svaret: meldingen er slettet — et svar'
            ' uten tråd har ingen mottaker'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT r.kilde_aktiv THEN
        RAISE EXCEPTION 'm6_send_svaret: postboksen er ikke aktiv'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT r.kilde_kan_svare THEN
        RAISE EXCEPTION 'm6_send_svaret: postboksen mangler sendetilgang'
            ' — koble den til på nytt'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NOT r.har_leverandor_id OR NOT r.har_avsender THEN
        RAISE EXCEPTION 'm6_send_svaret: meldingen mangler tråden eller'
            ' avsenderen' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    UPDATE public.epost_utkast
       SET status = 'sendes', avgjort_ts = pg_catalog.now(),
           avgjort_av = p_aktor, feilgrunn = NULL
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id;
    PERFORM public.m6_evidens(p_tenant, r.melding_id, 'epost.svar_bestilt',
                              p_aktor,
                              jsonb_build_object('utkast_id', p_utkast_id,
                                                 'fra', v_gammel));
    RETURN 'sendes';
END $$;
REVOKE ALL ON FUNCTION m6_send_svaret(TEXT, UUID, TEXT) FROM PUBLIC;

-- GRANTENE GIS AV EIEREN, altså her inne (#140-læren, 088s form):
-- migrator kan ikke dele ut rettigheter på en funksjon claimeren
-- eier, og et forsøk er en hard feil midt i migrasjonen.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m6_for_svar(TEXT, UUID)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m6_send_svaret(TEXT, UUID,'
            ' TEXT) TO disponit';
    END IF;
END $$;

RESET ROLE;


