-- 179 — M-6: mennesket skriver svaret, og godkjenner det (svar fra
-- postboksen, PR 2). Eiervedtak 10/9.
--
-- 088 tegnet `epost_utkast` for MODELLVEIEN: en foreslått tekst et
-- menneske kunne forkaste eller kopiere ut manuelt. Nå skal utkastet
-- kunne SENDES, og da må to ting til som 088 ikke hadde grunn til å ha:
--
--   * en skrivevei. 088 ga runtime bare SELECT, fordi ingen skrev
--     utkast ennå. Nå skriver mennesket dem, og det skjer gjennom en
--     dør med evidens — aldri med en bar INSERT-rettighet.
--   * to statuser til. `godkjent` er menneskets ja: det er DEN
--     tilstanden utløseren plukker, og uten den ville et forslag og en
--     beslutning sett like ut. `sendt` er kvitteringens vei tilbake
--     (PR 6).
--
-- OVERGANGENE, og hvorfor de er akkurat disse:
--   foreslatt → godkjent      mennesket sier ja
--   foreslatt → forkastet     mennesket sier nei
--   foreslatt → brukt_manuelt 088s opprinnelige vei (kopiert ut)
--   godkjent  → forkastet     angret FØR sendingen rakk å skje
--   godkjent  → sendt         kvitteringen (PR 6), aldri en dom
-- `sendt`, `forkastet` og `brukt_manuelt` er terminale. Teksten er
-- fortsatt append-only: et rettet svar er en NY rad, aldri denne.

ALTER TABLE epost_utkast DROP CONSTRAINT IF EXISTS utkast_status_lukket;
ALTER TABLE epost_utkast ADD CONSTRAINT utkast_status_lukket
    CHECK (status IN ('foreslatt', 'godkjent', 'forkastet',
                      'brukt_manuelt', 'sendt'));

ALTER TABLE epost_utkast
    ADD COLUMN avgjort_ts TIMESTAMPTZ,
    ADD COLUMN avgjort_av TEXT;

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
                ' godkjenning og forkasting er egne, målte overganger'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.avgjort_ts IS NOT NULL OR NEW.avgjort_av IS NOT NULL THEN
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
        -- Reap-overgangen, 057-formen.
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
    -- 179: fem overganger, og teksten er fortsatt append-only.
    IF NOT ((OLD.status = 'foreslatt'
             AND NEW.status IN ('godkjent', 'forkastet', 'brukt_manuelt'))
            OR (OLD.status = 'godkjent'
                AND NEW.status IN ('forkastet', 'sendt'))) THEN
        RAISE EXCEPTION 'epost_utkast: overgang % -> % finnes ikke —'
            ' teksten er append-only, og sendt/forkastet er terminale',
            OLD.status, NEW.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Hver DOM bærer sin egen aktør og sitt eget tidspunkt. En angring
    -- (godkjent → forkastet) er en NY dom, ikke en omskriving av den
    -- forrige, og skriver derfor sine egne felter.
    IF NEW.status IN ('godkjent', 'forkastet', 'brukt_manuelt')
       AND (NEW.avgjort_ts IS NULL OR NEW.avgjort_av IS NULL) THEN
        RAISE EXCEPTION 'epost_utkast: en dom har en aktør og et'
            ' tidspunkt' USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- SENDINGEN skriver ALDRI om godkjenningen. Et sendt svar må for
    -- alltid vise hvem som sa ja til at det gikk ut — det er hele
    -- grunnen til at et menneske står i veien.
    IF NEW.status = 'sendt'
       AND (NEW.avgjort_ts IS DISTINCT FROM OLD.avgjort_ts
            OR NEW.avgjort_av IS DISTINCT FROM OLD.avgjort_av) THEN
        RAISE EXCEPTION 'epost_utkast: sendingen skriver ikke om'
            ' godkjenningen' USING ERRCODE = 'insufficient_privilege';
    END IF;
    nj := nj - 'status' - 'avgjort_ts' - 'avgjort_av';
    oj := oj - 'status' - 'avgjort_ts' - 'avgjort_av';
    IF nj IS DISTINCT FROM oj THEN
        RAISE EXCEPTION 'epost_utkast: bare status og dommen endres —'
            ' teksten og resten av raden er immutable'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

SET LOCAL ROLE disponit_m37_claimer;

CREATE FUNCTION m6_skriv_svarutkast(
    p_tenant TEXT, p_melding_id UUID, p_tekst_kryptert BYTEA,
    p_nonce BYTEA, p_key_id TEXT, p_aktor TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm6_skriv_svarutkast');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm6_skriv_svarutkast: et utkast har en forfatter'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Vakten gjerder allerede en reapet eller ukjent melding; her er
    -- bare selve fødselen.
    INSERT INTO public.epost_utkast
        (tenant, melding_id, tekst_kryptert, nonce, key_id)
    VALUES (p_tenant, p_melding_id, p_tekst_kryptert, p_nonce, p_key_id)
    RETURNING utkast_id INTO v_id;
    PERFORM public.m6_evidens(p_tenant, p_melding_id, 'epost.utkast_skrevet',
                              p_aktor, jsonb_build_object('utkast_id', v_id));
    RETURN v_id;
END $$;
REVOKE ALL ON FUNCTION m6_skriv_svarutkast(TEXT, UUID, BYTEA, BYTEA, TEXT,
    TEXT) FROM PUBLIC;

CREATE FUNCTION m6_avgjor_utkast(
    p_tenant TEXT, p_utkast_id UUID, p_status TEXT, p_aktor TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_gammel TEXT; v_melding UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm6_avgjor_utkast');
    IF p_status NOT IN ('godkjent', 'forkastet', 'brukt_manuelt') THEN
        RAISE EXCEPTION 'm6_avgjor_utkast: «sendt» er kvitteringens vei,'
            ' ikke en dom et menneske setter'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT u.status, u.melding_id INTO v_gammel, v_melding
      FROM public.epost_utkast u
     WHERE u.tenant = p_tenant AND u.utkast_id = p_utkast_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm6_avgjor_utkast: utkastet finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_gammel = p_status THEN
        RETURN v_gammel;                           -- gjenspill: stille ja
    END IF;
    UPDATE public.epost_utkast
       SET status = p_status, avgjort_ts = pg_catalog.now(),
           avgjort_av = p_aktor
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id;
    PERFORM public.m6_evidens(
        p_tenant, v_melding, 'epost.utkast_' || p_status, p_aktor,
        jsonb_build_object('utkast_id', p_utkast_id, 'fra', v_gammel));
    RETURN p_status;
END $$;
REVOKE ALL ON FUNCTION m6_avgjor_utkast(TEXT, UUID, TEXT, TEXT) FROM PUBLIC;

RESET ROLE;

-- Claimeren eier dørene og skriver gjennom dem: den trenger INSERT på
-- utkastlageret (088 ga den bare SELECT/UPDATE, fordi ingen skrev ennå).
GRANT INSERT ON epost_utkast TO disponit_m37_claimer;
