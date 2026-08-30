-- 079: kundeeid utsendingstekst (#160 — Codex P2 på #153, runde 10)
--
-- Klarsignalet §6 lover «ingen vei fra modellutdata til
-- utsendingstekst», men port 13 måler bare importgrafen i maler.py:
-- `firmatekst` var en fri streng, og en orkestrator som sendte
-- modellutdata dit fikk det ordrett ut i en invitasjon eller et avslag.
-- Den ekte lukkingen (#160, sagt der ordrett): firmateksten slutter å
-- være en VERDI og blir en REFERANSE til kundeeid, lagret tekst —
-- tenant-bundet, versjonert, forfattet av kunden, aldri av en modul
-- eller en modell. `flett` har ingen produksjonskaller ennå; maskinen
-- lander FØR den første kalleren skrives.
--
-- Formen er stillingsprofilens (061/074), medlem for medlem: append-
-- only versjonering, dør-eid skriving, enveis skjuling, RLS. Modulen
-- (runtime) kan LESE teksten og aldri SKRIVE den — skrivedøren er bak
-- kundens bestillingsmyndighet i HTTP-laget, og selve teksten hentes av
-- resolveren i core, aldri av modellsiden.

CREATE TABLE utsendingstekst (
    tenant TEXT NOT NULL,
    tekst_id UUID NOT NULL,
    versjon INT NOT NULL CHECK (versjon >= 1),
    navn TEXT NOT NULL CHECK (length(btrim(navn)) BETWEEN 1 AND 200),
    -- Kundens tone. Tom tekst er en ekte tilstand («ingen tone») og
    -- håndteres av malene; her kreves bare at teksten er bundet.
    tekst TEXT NOT NULL CHECK (length(tekst) <= 4000),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    operasjonsnokkel TEXT NOT NULL,
    innhold_hash TEXT NOT NULL,
    skjult_ts TIMESTAMPTZ,
    CONSTRAINT utsendingstekst_pk PRIMARY KEY (tenant, tekst_id, versjon),
    CONSTRAINT utsendingstekst_idem UNIQUE (tenant, operasjonsnokkel)
);

-- Append-only + enveis skjuling — 074-formen for stillingsprofilen,
-- ordrett for dette lageret.
CREATE FUNCTION utsendingstekst_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'utsendingstekst: % avvist — versjonene er'
            ' append-only (redigering = ny versjon)', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.tekst_id IS DISTINCT FROM OLD.tekst_id
       OR NEW.versjon IS DISTINCT FROM OLD.versjon
       OR NEW.navn IS DISTINCT FROM OLD.navn
       OR NEW.tekst IS DISTINCT FROM OLD.tekst
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av
       OR NEW.operasjonsnokkel IS DISTINCT FROM OLD.operasjonsnokkel
       OR NEW.innhold_hash IS DISTINCT FROM OLD.innhold_hash THEN
        RAISE EXCEPTION 'utsendingstekst: kun skjuling kan skrives —'
            ' redigering er en ny versjon'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.skjult_ts IS DISTINCT FROM OLD.skjult_ts THEN
        IF OLD.skjult_ts IS NOT NULL THEN
            RAISE EXCEPTION 'utsendingstekst: skjulingen er enveis'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.skjult_ts IS NULL
           OR NEW.skjult_ts > pg_catalog.now() THEN
            RAISE EXCEPTION 'utsendingstekst: skjulingen settes nå,'
                ' aldri frem i tid'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER utsendingstekst_vakt
    BEFORE UPDATE OR DELETE ON utsendingstekst
    FOR EACH ROW EXECUTE FUNCTION utsendingstekst_vakt();
CREATE TRIGGER utsendingstekst_ingen_truncate
    BEFORE TRUNCATE ON utsendingstekst
    FOR EACH STATEMENT EXECUTE FUNCTION utsendingstekst_vakt();

ALTER TABLE utsendingstekst ENABLE ROW LEVEL SECURITY;
ALTER TABLE utsendingstekst FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON utsendingstekst
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- Dørenes eier trenger radrettighetene (defineren leser/skriver
-- gjennom RLS med kontekst — kontekstporten binder tenant).
GRANT SELECT, INSERT, UPDATE ON utsendingstekst TO disponit_domene_eier;

-- ------------------------------------------------------------
-- Dørene eies av domene-eieren, som stillingsprofilens (061-formen —
-- grantene til runtime bor i migrer.py-blokka som kjører som eieren).
SET LOCAL ROLE disponit_domene_eier;
-- Skrivedøren — 061-formens gjenspill (samme nøkkel + samme innhold =
-- samme svar; annet innhold på brukt nøkkel er en kollisjon), samme
-- READ COMMITTED-port, samme advisory-serialiserte versjonstildeling.
CREATE FUNCTION opprett_utsendingstekst_versjon(
    p_tenant TEXT, p_tekst_id UUID, p_navn TEXT, p_tekst TEXT,
    p_opprettet_av TEXT, p_operasjonsnokkel TEXT)
RETURNS TABLE (ut_tekst_id UUID, ut_versjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID; v_versjon INT; v_hash TEXT; v_lagret TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'opprett_utsendingstekst_versjon');
    IF current_setting('transaction_isolation')
       NOT IN ('read committed', 'read uncommitted') THEN
        RAISE EXCEPTION 'opprett_utsendingstekst_versjon: krever READ'
            ' COMMITTED (fikk %) — gjenspill-løftet er utledet av en'
            ' LESNING', current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF p_navn IS NULL OR length(btrim(p_navn)) NOT BETWEEN 1 AND 200 THEN
        RAISE EXCEPTION 'utsendingstekst: navnet må være 1–200 tegn'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_tekst IS NULL OR length(p_tekst) > 4000 THEN
        RAISE EXCEPTION 'utsendingstekst: teksten må være 0–4000 tegn'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_hash := md5(coalesce(p_tekst_id::text, '') || '·'
                  || coalesce(btrim(p_navn), '') || '·' || p_tekst);
    SELECT t.tekst_id, t.versjon, t.innhold_hash
      INTO ut_tekst_id, ut_versjon, v_lagret
      FROM public.utsendingstekst t
     WHERE t.tenant = p_tenant
       AND t.operasjonsnokkel = p_operasjonsnokkel;
    IF FOUND THEN
        IF v_lagret IS DISTINCT FROM v_hash THEN
            RAISE EXCEPTION 'utsendingstekst: nøkkelen er brukt for'
                ' ANNET innhold' USING ERRCODE = 'unique_violation';
        END IF;
        RETURN NEXT;
        RETURN;
    END IF;
    IF p_tekst_id IS NULL THEN
        v_id := gen_random_uuid();
        v_versjon := 1;
    ELSE
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'utsendingstekst:' || p_tenant || ':' || p_tekst_id::text, 0));
        SELECT coalesce(max(t.versjon), 0) INTO v_versjon
          FROM public.utsendingstekst t
         WHERE t.tenant = p_tenant AND t.tekst_id = p_tekst_id;
        IF v_versjon = 0 THEN
            RAISE EXCEPTION 'utsendingstekst: ukjent tekst'
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        v_id := p_tekst_id;
        v_versjon := v_versjon + 1;
    END IF;
    INSERT INTO public.utsendingstekst
        (tenant, tekst_id, versjon, navn, tekst, opprettet_av,
         operasjonsnokkel, innhold_hash)
    VALUES (p_tenant, v_id, v_versjon, btrim(p_navn), p_tekst,
            p_opprettet_av, p_operasjonsnokkel, v_hash);
    ut_tekst_id := v_id; ut_versjon := v_versjon;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION opprett_utsendingstekst_versjon(
    TEXT, UUID, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;

-- Slett = enveis skjuling gjennom døren (074-formen).
CREATE FUNCTION skjul_utsendingstekst(p_tenant TEXT, p_tekst_id UUID)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_antall INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'skjul_utsendingstekst');
    UPDATE public.utsendingstekst
       SET skjult_ts = pg_catalog.now()
     WHERE tenant = p_tenant AND tekst_id = p_tekst_id
       AND skjult_ts IS NULL;
    GET DIAGNOSTICS v_antall = ROW_COUNT;
    RETURN v_antall;
END $$;
REVOKE ALL ON FUNCTION skjul_utsendingstekst(TEXT, UUID) FROM PUBLIC;
RESET ROLE;
