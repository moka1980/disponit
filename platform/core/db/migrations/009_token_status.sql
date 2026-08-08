-- ============================================================
-- Disponit migrasjon 009 — tokenstatus: PENDING/AKTIV/TILBAKEKALT.
-- Spesifisert i PR-009 v3 §4 + v4 §1–2 + v5 §1 og klarsignalets V1/V2.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen. Kjøres av MIGRATOR.
--
-- FORWARD-ONLY (klarsignalets V1): å droppe `aktiv` er ikke
-- bakoverkompatibelt. Gammel applikasjonskode som leser kolonnen kan ikke
-- startes mot dette skjemaet — boot-sjekkens EKSAKTE migrasjonsmatch
-- håndhever det (gammel release forventer 1–8, basen har 1–9, oppstart
-- nektes). Automatisk rollback finnes ikke og loves ikke; opp.sh
-- rapporterer det eksplisitt.
--
-- Rekkefølgen er PR-008-mønsteret: en samlet ADD COLUMN ... DEFAULT
-- 'PENDING' ville satt ALLE eksisterende tokens til PENDING — umiddelbar
-- utestenging av hver kunde. Trinnene under lar eksisterende tokens virke
-- FØR og ETTER commit (kall kan blokkeres av DDL-låsen i selve vinduet —
-- kort, og loves ikke bort).
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_authenticator') THEN
        RAISE EXCEPTION 'rollen disponit_authenticator mangler — kjør oppsett først';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_token_admin') THEN
        RAISE EXCEPTION 'rollen disponit_token_admin mangler — kjør oppsett først';
    END IF;
END $$;

-- 1. Nullable, ingen default, ingen CHECK.
ALTER TABLE api_tokener ADD COLUMN IF NOT EXISTS status TEXT;

-- 2. Backfill fra eksisterende sannhet.
UPDATE api_tokener
   SET status = CASE WHEN aktiv THEN 'AKTIV' ELSE 'TILBAKEKALT' END
 WHERE status IS NULL;

-- 3. Fail-hard: ingen NULL igjen.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.api_tokener WHERE status IS NULL) THEN
        RAISE EXCEPTION 'migrasjon 009: status-backfill ufullstendig — avbryter';
    END IF;
END $$;

-- 4. CHECK (NOT VALID -> VALIDATE) + NOT NULL.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'api_tokener_status_ck') THEN
        ALTER TABLE public.api_tokener ADD CONSTRAINT api_tokener_status_ck
            CHECK (status IN ('PENDING','AKTIV','TILBAKEKALT')) NOT VALID;
        ALTER TABLE public.api_tokener
            VALIDATE CONSTRAINT api_tokener_status_ck;
    END IF;
END $$;
ALTER TABLE api_tokener ALTER COLUMN status SET NOT NULL;

-- 5. Default for FREMTIDIGE rader: et nytt token er PENDING til den
--    interaktive utleveringen har bevist at et menneske faktisk holder
--    hemmeligheten (v4 §1).
ALTER TABLE api_tokener ALTER COLUMN status SET DEFAULT 'PENDING';

-- 6. PENDING-verifikasjon for CLI-en (klarsignalets V2): en AVGRENSET
--    SECURITY DEFINER-funksjon som gir token-admin metadata + lagret MAC
--    for et PENDING-token — og BARE et PENDING-token. CLI-en beregner
--    kandidat-MAC lokalt med pepperet (som aldri finnes i databasen og
--    aldri er funksjonsargument) og sammenligner konstant-tid hos seg.
--    Funksjonen gjør ALDRI PENDING gyldig som API-principal — den leser,
--    den beviser ingenting mot API-et.
CREATE OR REPLACE FUNCTION hent_pending_token(p_token_id TEXT)
RETURNS TABLE (tenant TEXT, rolle TEXT, scopes TEXT[], secret_mac TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    RETURN QUERY
    SELECT t.tenant, t.rolle, t.scopes, t.secret_mac
      FROM public.api_tokener t
     WHERE t.token_id = p_token_id
       AND t.status = 'PENDING';
END $$;

ALTER FUNCTION hent_pending_token(TEXT) OWNER TO disponit_authenticator;
REVOKE ALL ON FUNCTION hent_pending_token(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hent_pending_token(TEXT) TO disponit_token_admin;

-- 7. Verifikatoren strammes: KUN status='AKTIV'. Ellers identisk med
--    004-versjonen (konstant-tids sammenligning, last_used_at-throttle).
--    Kjøres ETTER backfillen — eksisterende gyldige tokens er alt AKTIV.
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
    IF p_kandidat_mac IS NULL OR p_kandidat_mac !~ '^[0-9a-f]{64}$' THEN
        RETURN;
    END IF;

    SELECT t.secret_mac, t.tenant, t.rolle, t.scopes
      INTO v_mac, v_tenant, v_rolle, v_scopes
      FROM public.api_tokener t
     WHERE t.token_id = p_token_id
       AND t.status = 'AKTIV'
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

-- 8. `status` er ENESTE autoritet — `aktiv` fjernes i SAMME migrasjon
--    (v5 §1). To muterbare sannheter ville før eller siden gitt
--    `aktiv=false` + `status='AKTIV'`, og da er tilbakekalling en
--    kolonneleser-avhengig påstand. Kolonnegrants forsvinner med kolonnen;
--    deploy/staging/migrer.py setter de nye status-baserte rettighetene.
ALTER TABLE api_tokener DROP COLUMN IF EXISTS aktiv;
