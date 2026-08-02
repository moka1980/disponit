-- ============================================================
-- Disponit migrasjon 004 — verifiser_token: konstant-tids
-- MAC-sammenligning og last_used_at uten tabelltilgang for runtime.
--
-- Hvorfor en NY migrasjon og ikke en endring i 003: 003 er kjørt og
-- registrert med checksum. Kjøreren avviser enhver endring av en
-- historisk fil (db/kjorer.py). Historikken er immutable — rettelser
-- kommer alltid som neste versjon.
--
-- INGEN BEGIN/COMMIT: fra versjon 3 eier kjøreren transaksjonen.
-- Kjøres av MIGRATOR-rollen.
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_authenticator') THEN
        RAISE EXCEPTION
            'rollen disponit_authenticator mangler — kjør deploy/staging/oppsett-postgresql.sh først';
    END IF;
    IF NOT pg_has_role(current_user, 'disponit_authenticator', 'MEMBER') THEN
        RAISE EXCEPTION
            'migratorrollen % er ikke medlem av disponit_authenticator — kreves for OWNER TO', current_user;
    END IF;
END $$;

-- ------------------------------------------------------------
-- To rettelser mot 003-versjonen av funksjonen:
--
-- 1. KONSTANT-TIDS SAMMENLIGNING (v3-delta pkt. 7 krevde det; 003
--    sammenlignet med `=` i WHERE-klausulen). `=` på tekst avslutter ved
--    første ulike byte, så tiden lekker hvor langt inn kandidaten traff.
--    Her hentes raden på token_id alene, og MAC-en sammenlignes deretter
--    posisjon for posisjon over ALLE 64 tegnene uansett utfall — samme
--    arbeid ved treff på tegn 1 som ved treff på tegn 64.
--
--    Merk hva dette IKKE er: en garanti på maskinnivå. PostgreSQL kan
--    fortsatt variere. Poenget er å fjerne det ene orakelet som var
--    trivielt å måle, ikke å påstå perfekt konstant tid. Pepperet ligger
--    uansett hos API-prosessen og aldri i databasen, så en angriper med
--    full DB-lesing kan ikke konstruere kandidater i utgangspunktet.
--
--    At token_id-oppslaget selv er raskere for et ukjent token_id er
--    bevisst akseptert: token_id er ikke hemmelig — det sendes i klartekst
--    som første halvdel av tokenet.
--
-- 2. last_used_at (v2 Del 3.2) oppdateres INNE i funksjonen. Korreksjon 2
--    gir runtime KUN `EXECUTE verifiser_token` og null tabelltilgang;
--    da kan runtime ikke skrive last_used_at selv. SECURITY DEFINER er
--    eneste vei. Maks én skriving per minutt per token holder
--    skrivestøyen — og radlåsen — borte fra lastveien: under lasttesten
--    (6 000 requests) blir det én UPDATE i minuttet, ikke 6 000.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION verifiser_token(p_token_id TEXT, p_kandidat_mac TEXT)
RETURNS TABLE (tenant TEXT, rolle TEXT, scopes TEXT[])
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_mac    TEXT;
    v_tenant TEXT;
    v_rolle  TEXT;
    v_scopes TEXT[];
    v_avvik  INT;
BEGIN
    -- Format-guarden FØR alt annet: fast lengde er forutsetningen for at
    -- posisjonssammenligningen under er konstant i det hele tatt.
    IF p_kandidat_mac IS NULL OR p_kandidat_mac !~ '^[0-9a-f]{64}$' THEN
        RETURN;
    END IF;

    SELECT t.secret_mac, t.tenant, t.rolle, t.scopes
      INTO v_mac, v_tenant, v_rolle, v_scopes
      FROM public.api_tokener t
     WHERE t.token_id = p_token_id
       AND t.aktiv
       AND (t.utloper IS NULL OR t.utloper > pg_catalog.now());
    IF NOT FOUND THEN
        RETURN;
    END IF;

    SELECT pg_catalog.count(*)::INT INTO v_avvik
      FROM pg_catalog.generate_series(1, 64) AS i
     WHERE pg_catalog.substr(v_mac, i, 1)
           IS DISTINCT FROM pg_catalog.substr(p_kandidat_mac, i, 1);
    IF v_avvik <> 0 THEN
        RETURN;
    END IF;

    UPDATE public.api_tokener t
       SET last_used_at = pg_catalog.now()
     WHERE t.token_id = p_token_id
       AND (t.last_used_at IS NULL
            OR t.last_used_at < pg_catalog.now() - INTERVAL '1 minute');

    tenant := v_tenant;
    rolle  := v_rolle;
    scopes := v_scopes;
    RETURN NEXT;
END $$;

ALTER FUNCTION verifiser_token(TEXT, TEXT) OWNER TO disponit_authenticator;
REVOKE ALL ON FUNCTION verifiser_token(TEXT, TEXT) FROM PUBLIC;
