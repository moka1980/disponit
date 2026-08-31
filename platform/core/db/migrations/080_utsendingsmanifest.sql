-- 080: mottakermanifestet (#149) — listen KJENNER medlemmene sine
-- (senderbenens del A; eiermandatet 31/8: M-57 gjøres ferdig)
--
-- Signaturkjeden (056) binder listens IDENTITET, men basen hadde ingen
-- representasjon av HVEM listen gjelder: `antall` var et TAK, ikke et
-- medlemskap, så «disse N mottakerne» var «høyst N strenger» (056-hodet,
-- #149). Lukkingen er 056-hodets egen: et per-mottaker-manifest skrevet
-- FØR signering og dekket av `innhold_hash`.
--
-- Formen: manifestet skrives i SAMME transaksjon som listen, av samme
-- dør, og `innhold_hash` UTLEDES av døren over nøyaktig (listetype,
-- malversjon, oppdrag, sorterte medlemmer) — hashen mottas ikke lenger
-- (057-doktrinen: evidens måles på de LAGREDE verdiene, aldri på
-- påstanden om dem). Signataren signerer dermed MEDLEMSKAPET.
-- Runtime har ingen rå skrivevei til `utsendingsliste` (RLS FORCE +
-- definer-dører alene), så produksjonsveien kan ikke føde en liste uten
-- manifest. Historiske rader (demo-seed) står uten medlemmer og er
-- USENDBARE: senderbenen leser manifestet, aldri `antall`.

CREATE TABLE utsendingsliste_medlem (
    tenant TEXT NOT NULL,
    liste_id UUID NOT NULL,
    prosess_id UUID NOT NULL,
    -- Lagerets kandidat-uuid (075-ankeret) — aldri buntens frie streng:
    -- medlemskapet peker på en kandidat basen KJENNER, og adressen slås
    -- opp ved sending fra kandidat_utsendingsdata bak samme anker.
    kandidat_id UUID NOT NULL,
    CONSTRAINT utsendingsliste_medlem_pk
        PRIMARY KEY (tenant, liste_id, kandidat_id),
    CONSTRAINT medlem_liste_fk FOREIGN KEY (tenant, liste_id)
        REFERENCES utsendingsliste (tenant, liste_id),
    CONSTRAINT medlem_kandidat_fk
        FOREIGN KEY (tenant, prosess_id, kandidat_id)
        REFERENCES kandidat (tenant, prosess_id, kandidat_id)
);

-- Manifestet er en del av det signerte innholdet: append-only, uten
-- unntak (056-formen for listen selv, ordrett for medlemmene).
CREATE FUNCTION utsendingsliste_medlem_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'utsendingsliste_medlem: % avvist — manifestet er en'
        ' del av det signerte innholdet og er append-only', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER utsendingsliste_medlem_vakt
    BEFORE UPDATE OR DELETE ON utsendingsliste_medlem
    FOR EACH ROW EXECUTE FUNCTION utsendingsliste_medlem_vakt();
CREATE TRIGGER utsendingsliste_medlem_ingen_truncate
    BEFORE TRUNCATE ON utsendingsliste_medlem
    FOR EACH STATEMENT EXECUTE FUNCTION utsendingsliste_medlem_vakt();

ALTER TABLE utsendingsliste_medlem ENABLE ROW LEVEL SECURITY;
ALTER TABLE utsendingsliste_medlem FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON utsendingsliste_medlem
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

GRANT SELECT, INSERT ON utsendingsliste_medlem TO disponit_m37_claimer;

-- ------------------------------------------------------------
-- Døren. Signaturbyttet (medlemsarray inn, hash UT) gjør at den gamle
-- formen må BORT — to dører ville latt en kaller velge veien uten
-- manifest. Claimer eier begge (056-formen).
SET LOCAL ROLE disponit_m37_claimer;
DROP FUNCTION opprett_utsendingsliste(TEXT, UUID, UUID, BIGINT,
                                      TEXT, TEXT, TEXT, INT);
CREATE FUNCTION opprett_utsendingsliste(
    p_tenant TEXT, p_utkast_serie UUID, p_forrige UUID, p_oppdrag_id BIGINT,
    p_listetype TEXT, p_malversjon TEXT, p_medlemmer UUID[])
RETURNS TABLE (ut_liste_id UUID, ut_innhold_hash TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID := gen_random_uuid(); v_forelder_oppdrag BIGINT;
        v_prosess UUID; v_medlemmer UUID[]; v_kanonisk TEXT;
        v_antall INT; v_levende INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'opprett_utsendingsliste');
    -- SERIELÅSEN (065, SPEILET — #180): samme advisory-lås som
    -- signeringsveien tar, så «finnes det et barn» og signaturen aldri
    -- er to steg uten lås imellom. Advisory og ikke radlås av 065s egen
    -- grunn: runtime har kun SELECT, og enhver radlåsklausul krever
    -- UPDATE-privilegium.
    PERFORM pg_advisory_xact_lock(
        hashtextextended('m57:serie:' || p_tenant || ':'
                         || p_utkast_serie::text, 0));
    -- SERIEN PEKER PÅ ÉN EVALUERING (056-kroppen SPEILET — Cursor P2 på
    -- #140, runde 3): forelderen eier evalueringspekeren; barnet arver
    -- den, det velger den ikke.
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
    -- LISTEN PROMOTERER EN FULLFØRT EVALUERING (056-kroppen SPEILET —
    -- Codex P1 + Cursor P2 på #140, runde 2; klarsignalet §1/§7).
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
    -- MEDLEMSKAPET (#149): dedupliseres og SORTERES her — hashen skal
    -- være en funksjon av MENGDEN, aldri av rekkefølgen kalleren tilbød.
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
    -- Hvert medlem må være en LEVENDE kandidat i prosessens anker: en
    -- reapet kandidat inviteres aldri, og FK-en alene skiller ikke
    -- levende fra gravlagt.
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
    -- HASHEN UTLEDES (057-doktrinen): signataren signerer nøyaktig
    -- dette — typen, malen, evalueringen og de sorterte medlemmene.
    SELECT p_listetype || E'\x1f' || p_malversjon || E'\x1f'
           || p_oppdrag_id::text || E'\x1f'
           || string_agg(m::text, E'\x1f' ORDER BY m)
      INTO v_kanonisk FROM unnest(v_medlemmer) AS m;
    ut_innhold_hash := encode(sha256(convert_to(v_kanonisk, 'UTF8')),
                              'hex');
    INSERT INTO public.utsendingsliste (tenant, liste_id, utkast_serie,
        forrige_liste_id, oppdrag_id, listetype, malversjon, innhold_hash,
        antall)
    VALUES (p_tenant, v_id, p_utkast_serie, p_forrige, p_oppdrag_id,
            p_listetype, p_malversjon, ut_innhold_hash, v_antall);
    INSERT INTO public.utsendingsliste_medlem
        (tenant, liste_id, prosess_id, kandidat_id)
    SELECT p_tenant, v_id, v_prosess, m FROM unnest(v_medlemmer) AS m;
    ut_liste_id := v_id;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION opprett_utsendingsliste(TEXT, UUID, UUID, BIGINT,
    TEXT, TEXT, UUID[]) FROM PUBLIC;
RESET ROLE;
