-- 206 — EMNET I KØEN.
--
-- EIERS BESLUTNING 16/9 («Den er god»): køen viser emnet. Til nå bar
-- listen aldri kundetekst — emnet kom først når raden ble åpnet, bak
-- `kundeservice:innhold`. Det skillet står: døren her gir emnet KRYPTERT
-- for de samme radene `m17_koen` gir (samme WHERE, samme rekkefølge,
-- samme tak), og API-et dekrypterer bare for en økt som har
-- innholdsscopet. En økt som bare ser køen, ser den som før.
--
-- Kroppen følger ikke med. Emnet er den ene linjen man skanner en kø
-- etter (#490 i innboksen). Kroppen er henvendelsen, og hentes per rad.

SET LOCAL ROLE disponit_kundeservice_eier;

CREATE FUNCTION m17_koens_emner(p_tenant TEXT, p_grense INT)
RETURNS TABLE(henvendelse_id UUID, emne_kryptert BYTEA, nonce_emne BYTEA,
              key_id TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_koens_emner');
    RETURN QUERY
    SELECT h.henvendelse_id, h.emne_kryptert, h.nonce_emne, h.key_id
      FROM public.henvendelse h
     WHERE h.tenant = p_tenant AND h.lukket_ts IS NULL
     ORDER BY h.mottatt, h.henvendelse_id
     LIMIT greatest(least(coalesce(p_grense, 100), 1000), 1);
END $$;
REVOKE ALL ON FUNCTION m17_koens_emner(TEXT, INT) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_koens_emner(TEXT, INT)'
            ' TO disponit';
    END IF;
END $$;
RESET ROLE;
