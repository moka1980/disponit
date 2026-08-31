-- 083: firmateksten inn i den signerte listen (#160, siste ledd —
-- eiermandatet 31/8: kundens forfattede tone skal faktisk UT i
-- e-postene)
--
-- 079 ga kunden det versjonerte tekstlageret og 081 sendte med
-- `firmatekst=None` («ingen tone») — koblingen manglet: ingenting sa
-- HVILKEN tekst en liste skal sendes med. Koblingen hører til LISTEN,
-- og den må inn FØR signaturen: signataren autoriserer utsendelsens
-- innhold, og tonen er en del av det. Derfor:
--   * listen bærer (firmatekst_ref, firmatekst_versjon) — valgfritt
--     (NULL/NULL er 079-kontraktens ekte «ingen tone»),
--   * VERSJONEN PINNES AV DØREN ved innstilling (nyeste uskjulte når
--     kalleren ikke angir en) — det som sendes er bit-likt det
--     signataren så, uansett hva kunden forfatter etterpå,
--   * `innhold_hash` UTLEDES OGSÅ over referansen (080-doktrinen:
--     signaturen dekker medlemskap OG tone),
--   * en SKJULT tekst kan aldri velges ved innstilling, men en sendt
--     liste refererer sin eksakte versjon for alltid (079-dommen:
--     eksakte referanser lever videre).

ALTER TABLE utsendingsliste
    ADD COLUMN firmatekst_ref UUID,
    ADD COLUMN firmatekst_versjon INT,
    ADD CONSTRAINT firmatekst_par CHECK (
        (firmatekst_ref IS NULL) = (firmatekst_versjon IS NULL)),
    ADD CONSTRAINT firmatekst_fk
        FOREIGN KEY (tenant, firmatekst_ref, firmatekst_versjon)
        REFERENCES utsendingstekst (tenant, tekst_id, versjon);

-- Innstillingsdøren leser kundens tekstlager (defineren kjører som
-- claimer; 079 ga runtime lesingen via migrer.py, claimer manglet).
GRANT SELECT ON utsendingstekst TO disponit_m37_claimer;

-- ------------------------------------------------------------
-- Døren (080-kroppen SPEILET; nye ledd er firmatekst-oppslaget og
-- hash-utvidelsen). Signaturbytte: to nye parametre med DEFAULT — alle
-- eksisterende kallere står uendret.
SET LOCAL ROLE disponit_m37_claimer;
DROP FUNCTION opprett_utsendingsliste(TEXT, UUID, UUID, BIGINT,
                                      TEXT, TEXT, UUID[]);
CREATE FUNCTION opprett_utsendingsliste(
    p_tenant TEXT, p_utkast_serie UUID, p_forrige UUID, p_oppdrag_id BIGINT,
    p_listetype TEXT, p_malversjon TEXT, p_medlemmer UUID[],
    p_firmatekst UUID DEFAULT NULL, p_firmatekst_versjon INT DEFAULT NULL)
RETURNS TABLE (ut_liste_id UUID, ut_innhold_hash TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID := gen_random_uuid(); v_forelder_oppdrag BIGINT;
        v_prosess UUID; v_medlemmer UUID[]; v_kanonisk TEXT;
        v_antall INT; v_levende INT; v_ftversjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'opprett_utsendingsliste');
    -- SERIELÅSEN (065, SPEILET — #180): samme advisory-lås som
    -- signeringsveien tar, så «finnes det et barn» og signaturen aldri
    -- er to steg uten lås imellom.
    PERFORM pg_advisory_xact_lock(
        hashtextextended('m57:serie:' || p_tenant || ':'
                         || p_utkast_serie::text, 0));
    -- SERIEN PEKER PÅ ÉN EVALUERING (056-kroppen SPEILET).
    IF p_forrige IS NOT NULL THEN
        SELECT oppdrag_id INTO v_forelder_oppdrag
          FROM public.utsendingsliste
         WHERE tenant = p_tenant AND liste_id = p_forrige;
        IF FOUND AND v_forelder_oppdrag IS DISTINCT FROM p_oppdrag_id THEN
            RAISE EXCEPTION 'opprett_utsendingsliste: barn må peke på'
                ' samme evalueringsoppdrag som forelderen (%), ikke %',
                v_forelder_oppdrag, p_oppdrag_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
    END IF;
    -- LISTEN PROMOTERER EN FULLFØRT EVALUERING (056-kroppen SPEILET).
    PERFORM 1 FROM public.oppdrag o
     WHERE o.tenant = p_tenant AND o.id = p_oppdrag_id
       AND o.oppdragstype = 'rekruttering.evaluering'
       AND o.status = 'utfort'
       AND o.opprinnelse IN ('beslutning', 'm37_reparasjon');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'opprett_utsendingsliste: oppdrag % er ikke en'
            ' FULLFØRT rekruttering.evaluering hos % (kjeden starter aldri'
            ' i et frigivelsesoppdrag, og en avbrutt kjøring promoteres'
            ' aldri)', p_oppdrag_id, p_tenant
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- FIRMATEKSTEN PINNES HER (#160): kalleren velger TEKSTEN, døren
    -- pinner VERSJONEN — nyeste uskjulte når ingen er angitt. En skjult
    -- eller ukjent tekst kan aldri innstilles; en eksplisitt versjon må
    -- være uskjult ved innstilling (sendte lister beholder referansen
    -- sin uansett senere skjuling — det er FK-ens jobb, ikke denne).
    IF p_firmatekst IS NOT NULL THEN
        -- SAMME LÅS SOM SKRIVEDØREN (079s advisory-nøkkel — CodeRabbit):
        -- «nyeste uskjulte» er en LESNING, og uten låsen kunne en
        -- samtidig ny versjon eller skjuling committe i vinduet mellom
        -- oppslag og INSERT — signataren ville sett en annen tone enn
        -- den pinnede.
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'utsendingstekst:' || p_tenant || ':'
            || p_firmatekst::text, 0));
        IF p_firmatekst_versjon IS NULL THEN
            SELECT t.versjon INTO v_ftversjon
              FROM public.utsendingstekst t
             WHERE t.tenant = p_tenant AND t.tekst_id = p_firmatekst
               AND t.skjult_ts IS NULL
             ORDER BY t.versjon DESC LIMIT 1;
        ELSE
            SELECT t.versjon INTO v_ftversjon
              FROM public.utsendingstekst t
             WHERE t.tenant = p_tenant AND t.tekst_id = p_firmatekst
               AND t.versjon = p_firmatekst_versjon
               AND t.skjult_ts IS NULL;
        END IF;
        IF v_ftversjon IS NULL THEN
            RAISE EXCEPTION 'opprett_utsendingsliste: firmateksten er'
                ' ukjent eller skjult — tonen velges blant kundens'
                ' synlige tekster (#160)'
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
    END IF;
    -- MEDLEMSKAPET (#149, 080-kroppen SPEILET).
    SELECT array_agg(DISTINCT m ORDER BY m), count(DISTINCT m)
      INTO v_medlemmer, v_antall
      FROM unnest(p_medlemmer) AS m WHERE m IS NOT NULL;
    IF v_antall IS NULL OR v_antall < 1 OR v_antall > 5000 THEN
        RAISE EXCEPTION 'opprett_utsendingsliste: manifestet må ha 1–5000'
            ' medlemmer (056-taket) — fikk %', coalesce(v_antall, 0)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT p.prosess_id INTO v_prosess
      FROM public.rekrutteringsprosess p
     WHERE p.tenant = p_tenant AND p.oppdrag_id = p_oppdrag_id
       AND p.slettet_ts IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'opprett_utsendingsliste: oppdraget har ingen'
            ' levende rekrutteringsprosess — medlemmene kan ikke ankres'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT count(*) INTO v_levende
      FROM public.kandidat k
     WHERE k.tenant = p_tenant AND k.prosess_id = v_prosess
       AND k.kandidat_id = ANY (v_medlemmer)
       AND k.slettet_ts IS NULL;
    IF v_levende IS DISTINCT FROM v_antall THEN
        RAISE EXCEPTION 'opprett_utsendingsliste: % av % medlemmer er'
            ' ikke levende kandidater i prosessen — manifestet avvises'
            ' samlet', v_antall - v_levende, v_antall
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- HASHEN UTLEDES (080-doktrinen) — nå også over tonen: signataren
    -- signerer typen, malen, evalueringen, medlemmene OG firmatekstens
    -- eksakte versjon. Tomme ledd for «ingen tone» holder formen én.
    SELECT p_listetype || E'\x1f' || p_malversjon || E'\x1f'
           || p_oppdrag_id::text || E'\x1f'
           || string_agg(m::text, E'\x1f' ORDER BY m)
           || E'\x1f' || coalesce(p_firmatekst::text, '')
           || E'\x1f' || coalesce(v_ftversjon::text, '')
      INTO v_kanonisk FROM unnest(v_medlemmer) AS m;
    ut_innhold_hash := encode(sha256(convert_to(v_kanonisk, 'UTF8')),
                              'hex');
    INSERT INTO public.utsendingsliste (tenant, liste_id, utkast_serie,
        forrige_liste_id, oppdrag_id, listetype, malversjon, innhold_hash,
        antall, firmatekst_ref, firmatekst_versjon)
    VALUES (p_tenant, v_id, p_utkast_serie, p_forrige, p_oppdrag_id,
            p_listetype, p_malversjon, ut_innhold_hash, v_antall,
            p_firmatekst, v_ftversjon);
    INSERT INTO public.utsendingsliste_medlem
        (tenant, liste_id, prosess_id, kandidat_id)
    SELECT p_tenant, v_id, v_prosess, m FROM unnest(v_medlemmer) AS m;
    ut_liste_id := v_id;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION opprett_utsendingsliste(TEXT, UUID, UUID, BIGINT,
    TEXT, TEXT, UUID[], UUID, INT) FROM PUBLIC;

-- ------------------------------------------------------------
-- Sendeklar-lesingen bærer tonen (081-kroppen SPEILET; retur-utvidelse
-- krever DROP + CREATE). Teksten JOINes her — senderen skal aldri
-- trenge egen lesevei inn i kundens tekstlager.
DROP FUNCTION m57_neste_sendinger(TEXT, INT, INT);
CREATE FUNCTION m57_neste_sendinger(
    p_tenant TEXT, p_grense INT, p_maks_forsok INT)
RETURNS TABLE (ut_liste_id UUID, ut_listetype TEXT, ut_malversjon TEXT,
               ut_kandidat_id UUID, ut_mottaker TEXT, ut_flettefelt JSONB,
               ut_firmatekst_ref UUID, ut_firmatekst_versjon INT,
               ut_firmatekst TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm57_neste_sendinger');
    RETURN QUERY
    SELECT l.liste_id, l.listetype, l.malversjon, m.kandidat_id,
           u.mottaker_ref, u.flettefelt,
           l.firmatekst_ref, l.firmatekst_versjon, ft.tekst
      FROM public.utsendingsliste l
      JOIN public.utsendingssignatur s
        ON s.tenant = l.tenant AND s.liste_id = l.liste_id
      JOIN public.utsendingsliste_medlem m
        ON m.tenant = l.tenant AND m.liste_id = l.liste_id
      JOIN public.kandidat_utsendingsdata u
        ON u.tenant = m.tenant AND u.prosess_id = m.prosess_id
       AND u.kandidat_id = m.kandidat_id AND u.slettet_ts IS NULL
      JOIN public.rekrutteringsprosess p
        ON p.tenant = m.tenant AND p.prosess_id = m.prosess_id
      LEFT JOIN public.utsendingstekst ft
        ON ft.tenant = l.tenant AND ft.tekst_id = l.firmatekst_ref
       AND ft.versjon = l.firmatekst_versjon
     WHERE l.tenant = p_tenant
       AND public.m57_payloadvindu(p)
       AND NOT EXISTS (
           SELECT 1 FROM public.m57_utsendingskvittering k
            WHERE k.tenant = m.tenant AND k.liste_id = m.liste_id
              AND k.kandidat_id = m.kandidat_id
              AND (k.status IN ('sendt', 'uviss', 'under_sending')
                   OR (k.status = 'feilet' AND k.forsok >= p_maks_forsok)))
     ORDER BY l.liste_id, m.kandidat_id
     LIMIT p_grense;
END $$;
REVOKE ALL ON FUNCTION m57_neste_sendinger(TEXT, INT, INT) FROM PUBLIC;
RESET ROLE;
