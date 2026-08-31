-- 081: senderbenen (#151 — konsumsjonen av signerte lister;
-- eiermandatet 31/8: M-57 gjøres ferdig)
--
-- 056/CP1 bygde autorisasjonsbenen og stoppet med vilje ved «et
-- oppdrag i køen»; den konsumerende benen var avstengt ved roten, med
-- to navngitte funn (056-hodet): kvitteringsveien manglet en
-- eier-kontrollert dør, og «én rad er ikke én sending» — idempotensen
-- må bo i TRANSPORTEN.
--
-- ARKITEKTVALGET (eiers delegerte dom 31/8, dokumentert her): sendingen
-- utføres av PLATTFORMSENDEREN (varselsender-mønsteret: oneshot-timer,
-- rollen `disponit_varselsender` med KUN EXECUTE på smale definer-
-- dører), ikke av modularbeideren gjennom oppdragskøen. §6s setning
-- «e-post via plattformens signerte utsendingsvei» ER denne rollen; en
-- utfører uten DB kunne aldri holdt transportidempotensen fail-closed.
-- `rekruttering.utsending` registreres derfor IKKE i OPPDRAGSTYPER
-- (vaktesten i test_m57_utsending består), og `opprett_frigivelses-
-- oppdrag` står urørt for en eventuell senere kø-utfører. 056-funnene
-- lukkes i formen de handlet om:
--   * KVITTERINGEN er en eier-kontrollert definer-vei (dørene her) som
--     selv setter status — ingen rå UPDATE fra runtime.
--   * ÉN SENDING PER MOTTAKER håndheves av kvitteringstabellen: raden
--     klaimes FØR SMTP (commit først), og et klaim som dør mellom
--     aksept og kvittering blir TERMINALT `uviss` — aldri auto-resend,
--     for utsendelsen er irreversibel og et uvisst utfall er et
--     menneskes dom, ikke en retry.
--
-- Sendingens tilstand nøkles på (liste, kandidat) — manifestraden
-- (080) — så sendeklar-lesingen er en JOIN uten pseudonymregning;
-- frigivelses-id-en skrives på raden idet den klaimes (frigi_utsendelse
-- er idempotent og pseudonymiserer selv, 078).

CREATE TABLE m57_utsendingskvittering (
    tenant TEXT NOT NULL,
    liste_id UUID NOT NULL,
    kandidat_id UUID NOT NULL,
    frigivelse_id UUID,
    status TEXT NOT NULL CHECK
        (status IN ('under_sending', 'sendt', 'feilet', 'uviss')),
    klaim UUID,
    forsok INT NOT NULL DEFAULT 0 CHECK (forsok >= 0),
    sendt_ts TIMESTAMPTZ,
    feil TEXT,
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT m57_utsendingskvittering_pk
        PRIMARY KEY (tenant, liste_id, kandidat_id),
    CONSTRAINT kvittering_medlem_fk
        FOREIGN KEY (tenant, liste_id, kandidat_id)
        REFERENCES utsendingsliste_medlem (tenant, liste_id, kandidat_id),
    CONSTRAINT kvittering_frigivelse_fk
        FOREIGN KEY (tenant, frigivelse_id)
        REFERENCES utsendingsfrigivelse (tenant, frigivelse_id)
);

ALTER TABLE m57_utsendingskvittering ENABLE ROW LEVEL SECURITY;
ALTER TABLE m57_utsendingskvittering FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON m57_utsendingskvittering
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- Per-tenant-dørene eies av claimer (056-formen); kryss-tenant-dørene
-- av domene-eieren (027-formen: BYPASSRLS-evnen ligger INNE i en smal
-- definer, aldri på senderrollen selv). Claimer trenger radrettighetene.
GRANT SELECT, INSERT, UPDATE ON m57_utsendingskvittering
    TO disponit_m37_claimer;
GRANT SELECT, UPDATE ON m57_utsendingskvittering TO disponit_domene_eier;
-- Kryss-tenant-sveipen leser kjedens tabeller: BYPASSRLS åpner radene,
-- men tabellrettigheten må fortsatt GIS.
GRANT SELECT ON utsendingsliste, utsendingssignatur,
    utsendingsliste_medlem, kandidat_utsendingsdata, rekrutteringsprosess
    TO disponit_domene_eier;
-- Vindusdommen (077) er migrator-eid; sveipen dømmer med samme kilde.
GRANT EXECUTE ON FUNCTION m57_payloadvindu(rekrutteringsprosess)
    TO disponit_domene_eier;

-- ------------------------------------------------------------
SET LOCAL ROLE disponit_m37_claimer;

-- Sendeklar-lesingen: signert liste × manifestmedlem med levende
-- utsendingsdata, innenfor payloadvinduet (077 — ÉN kilde), uten
-- blokkerende kvittering. `feilet` under taket er sendeklar igjen
-- (backoff håndteres av timerkadensen); `uviss` og `sendt` er
-- terminale, `under_sending` er noens klaim.
CREATE FUNCTION m57_neste_sendinger(
    p_tenant TEXT, p_grense INT, p_maks_forsok INT)
RETURNS TABLE (ut_liste_id UUID, ut_listetype TEXT, ut_malversjon TEXT,
               ut_kandidat_id UUID, ut_mottaker TEXT, ut_flettefelt JSONB)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm57_neste_sendinger');
    RETURN QUERY
    SELECT l.liste_id, l.listetype, l.malversjon, m.kandidat_id,
           u.mottaker_ref, u.flettefelt
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

-- Klaimet: raden settes `under_sending` og committes FØR SMTP — to
-- overlappende kjøringer skal aldri sende samme e-post hver sin gang.
-- Mottakeren GJENLESES her (TOCTOU: reaping kan ha truffet mellom
-- lesing og klaim), og frigivelsen fødes idempotent (078 pseudonymiserer
-- og håndhever signatur + antallstak i 056-kjeden).
CREATE FUNCTION m57_start_sending(
    p_tenant TEXT, p_liste UUID, p_kandidat UUID, p_klaim UUID,
    p_maks_forsok INT)
RETURNS TABLE (ut_frigivelse UUID, ut_mottaker TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.m57_utsendingskvittering; v_mottaker TEXT;
        v_prosess UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm57_start_sending');
    IF current_setting('transaction_isolation')
       NOT IN ('read committed', 'read uncommitted') THEN
        RAISE EXCEPTION 'm57_start_sending: krever READ COMMITTED'
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    SELECT k.* INTO v_rad FROM public.m57_utsendingskvittering k
     WHERE k.tenant = p_tenant AND k.liste_id = p_liste
       AND k.kandidat_id = p_kandidat FOR UPDATE;
    IF FOUND AND (v_rad.status <> 'feilet'
                  OR v_rad.forsok >= p_maks_forsok) THEN
        RETURN;                       -- noens klaim, sendt eller terminal
    END IF;
    SELECT m.prosess_id INTO v_prosess
      FROM public.utsendingsliste_medlem m
     WHERE m.tenant = p_tenant AND m.liste_id = p_liste
       AND m.kandidat_id = p_kandidat;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm57_start_sending: (%, %) er ikke et'
            ' manifestmedlem', p_liste, p_kandidat
            USING ERRCODE = 'no_data_found';
    END IF;
    -- PAYLOADVINDUET REVALIDERES UNDER KLAIMET (CodeRabbit): lesingen i
    -- m57_neste_sendinger er et øyeblikksbilde, og en tidligsletting
    -- kan committe i mellomrommet. Samme FOR SHARE som lagervakten
    -- (077): vinduet dømmes på radens NYE versjon, serialisert mot
    -- lukkeveiens FOR UPDATE.
    PERFORM 1 FROM public.rekrutteringsprosess pr
      WHERE pr.tenant = p_tenant AND pr.prosess_id = v_prosess
        AND public.m57_payloadvindu(pr)
      FOR SHARE;
    IF NOT FOUND THEN
        RETURN;                       -- vinduet lukket: aldri send
    END IF;
    SELECT u.mottaker_ref INTO v_mottaker
      FROM public.kandidat_utsendingsdata u
     WHERE u.tenant = p_tenant AND u.prosess_id = v_prosess
       AND u.kandidat_id = p_kandidat AND u.slettet_ts IS NULL;
    IF v_mottaker IS NULL THEN
        RETURN;                       -- reapet i mellomtiden: aldri send
    END IF;
    ut_frigivelse := public.frigi_utsendelse(p_tenant, p_liste,
                                             v_mottaker);
    IF v_rad.tenant IS NOT NULL THEN
        UPDATE public.m57_utsendingskvittering
           SET status = 'under_sending', klaim = p_klaim,
               forsok = forsok + 1, frigivelse_id = ut_frigivelse,
               feil = NULL, oppdatert = pg_catalog.now()
         WHERE tenant = p_tenant AND liste_id = p_liste
           AND kandidat_id = p_kandidat;
    ELSE
        INSERT INTO public.m57_utsendingskvittering
            (tenant, liste_id, kandidat_id, frigivelse_id, status,
             klaim, forsok)
        VALUES (p_tenant, p_liste, p_kandidat, ut_frigivelse,
                'under_sending', p_klaim, 1);
    END IF;
    ut_mottaker := v_mottaker;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m57_start_sending(TEXT, UUID, UUID, UUID, INT)
    FROM PUBLIC;

-- Kvitteringen: bare klaimets eier fullfører, og bare fra
-- `under_sending`. FALSE betyr «klaimet er ikke lenger ditt» — utfallet
-- er da uvisst og telles hos kalleren, aldri overskrevet her.
CREATE FUNCTION m57_fullfor_sending(
    p_tenant TEXT, p_liste UUID, p_kandidat UUID, p_klaim UUID,
    p_status TEXT, p_feil TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm57_fullfor_sending');
    IF p_status NOT IN ('sendt', 'feilet') THEN
        RAISE EXCEPTION 'm57_fullfor_sending: % er ikke et utfall'
            ' (sendt/feilet)', p_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.m57_utsendingskvittering
       SET status = p_status,
           sendt_ts = CASE WHEN p_status = 'sendt'
                           THEN pg_catalog.now() ELSE sendt_ts END,
           feil = p_feil, klaim = NULL, oppdatert = pg_catalog.now()
     WHERE tenant = p_tenant AND liste_id = p_liste
       AND kandidat_id = p_kandidat AND status = 'under_sending'
       AND klaim = p_klaim;
    RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION m57_fullfor_sending(
    TEXT, UUID, UUID, UUID, TEXT, TEXT) FROM PUBLIC;
RESET ROLE;

-- ------------------------------------------------------------
-- Kryss-tenant-dørene (027-formen): domene-eieren har BYPASSRLS, og
-- evnen ligger INNE i den smale definerte funksjonen — aldri på
-- senderrollen.
SET LOCAL ROLE disponit_domene_eier;

-- Sveipen SPEILER sendeklar-predikatene (CodeRabbit): uten retry-tak,
-- levende utsendingsdata og payloadvindu ville en tenant med bare
-- terminale rader stått evig «sendeklar», og hver kjøring rapportert
-- grense-stans over ingenting.
CREATE FUNCTION m57_sendeklare_tenanter(p_grense INT, p_maks_forsok INT)
RETURNS SETOF TEXT LANGUAGE sql SECURITY DEFINER STABLE
SET search_path = pg_catalog AS $$
    SELECT DISTINCT l.tenant
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
     WHERE public.m57_payloadvindu(p)
       AND NOT EXISTS (
           SELECT 1 FROM public.m57_utsendingskvittering k
            WHERE k.tenant = m.tenant AND k.liste_id = m.liste_id
              AND k.kandidat_id = m.kandidat_id
              AND (k.status IN ('sendt', 'uviss', 'under_sending')
                   OR (k.status = 'feilet' AND k.forsok >= p_maks_forsok)))
     LIMIT p_grense
$$;
REVOKE ALL ON FUNCTION m57_sendeklare_tenanter(INT, INT) FROM PUBLIC;

-- Et klaim som døde mellom SMTP-aksept og kvittering: TERMINALT
-- `uviss`, aldri tilbake i køen — utsendelsen er irreversibel, og «kan
-- alt ha gått ut» er et menneskes dom (056-funn 2, motsatsen til
-- varsel-køens lease-retur som gjelder en KOPI av portalen).
CREATE FUNCTION m57_merk_uviss(p_lease INTERVAL)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_antall INT;
BEGIN
    UPDATE public.m57_utsendingskvittering
       SET status = 'uviss', klaim = NULL,
           feil = 'klaimet utløp under sending — utfallet er uvisst',
           oppdatert = pg_catalog.now()
     WHERE status = 'under_sending'
       AND oppdatert < pg_catalog.now() - p_lease;
    GET DIAGNOSTICS v_antall = ROW_COUNT;
    RETURN v_antall;
END $$;
REVOKE ALL ON FUNCTION m57_merk_uviss(INTERVAL) FROM PUBLIC;
RESET ROLE;
