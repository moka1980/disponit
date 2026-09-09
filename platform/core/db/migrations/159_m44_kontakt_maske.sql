-- =====================================================================
-- 159 — M-44: EN NY ADRESSE FÅR NY MASKE (bevisrunden 9/9, gult funn)
-- =====================================================================
--
-- 153 lot en mottaker få adressen satt og rettet — men bare chifferteksten:
-- masken og hashen fra registreringen (114) sto igjen. Bevisrunden mot
-- disponit.com viste det: kunde-tb fikk ny adresse, leveringen gikk dit
-- (claim-veien dekrypterer chifferteksten), men flaten, kvitteringen og
-- leveringslinjen viste den GAMLE masken. En maske som ikke er adressens
-- er ikke en maske, den er en påstand.
--
-- Døra får en overlast som tar klarteksten og setter maske, hash og
-- chiffertekst i samme skriving — normalisering og maskeform er 114s.
-- Klartekst lever bare i kallet, aldri i basen. 153-døra beholdes for
-- kallere som bare bærer chiffertekst.
--
-- EKSISTERENDE RADER REPARERES IKKE HER, OG KAN IKKE: masken regnes av
-- klarteksten, og klarteksten finnes bare kryptert med tenantens DEK —
-- basen har verken KEK eller nøkkel, og skal ikke ha det (146/153). En
-- migrasjon som «regnet ut» en maske uten adressen ville dikte. Rader
-- der adressen ble byttet gjennom 153 (masken eldre enn `kontakt_satt_ts`
-- og ulik chifferteksten) repareres ved å sette adressen på nytt
-- gjennom `POST /v1/kampanje/mottaker/{id}/kontakt` — som nå går denne
-- veien. Bevisrunden 9/9 hadde ÉN slik rad (kunde-tb på tenant
-- disponit); den rettes slik, og runboken sier det.
-- ---------------------------------------------------------------------
SET LOCAL ROLE disponit_kampanje_eier;

CREATE FUNCTION m44_sett_kontakt(
    p_tenant TEXT, p_mottaker_id UUID, p_kontakt TEXT, p_kryptert BYTEA,
    p_nonce BYTEA, p_key_id TEXT, p_aktor TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_norm TEXT; v_maske TEXT; v_salt TEXT;
BEGIN
    v_norm := public.m44_normaliser(p_kontakt);
    IF length(v_norm) < 3 THEN
        RAISE EXCEPTION 'm44_sett_kontakt: kontaktpunktet er for kort til'
            ' å kunne maskeres meningsfullt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_maske := CASE
        WHEN position('@' IN v_norm) > 1 THEN
            left(v_norm, 1) || '****'
            || substring(v_norm FROM position('@' IN v_norm))
        ELSE left(v_norm, 1) || '****' || right(v_norm, 2)
    END;
    -- Chifferteksten, vaktene og evidensen: 153-døra (samme tenant-
    -- kontekst, samme lås, samme nei til en deaktivert mottaker).
    PERFORM public.m44_sett_kontakt(p_tenant, p_mottaker_id, p_kryptert,
                                    p_nonce, p_key_id, p_aktor);
    SELECT hash_salt INTO v_salt FROM public.kampanjemottaker
     WHERE tenant = p_tenant AND mottaker_id = p_mottaker_id;
    UPDATE public.kampanjemottaker
       SET kontakt_maske = v_maske,
           kontakt_hash = encode(
               sha256(convert_to(v_salt || v_norm, 'UTF8')), 'hex')
     WHERE tenant = p_tenant AND mottaker_id = p_mottaker_id;
    RETURN v_maske;
END $$;
REVOKE ALL ON FUNCTION m44_sett_kontakt(TEXT, UUID, TEXT, BYTEA, BYTEA, TEXT,
    TEXT) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_sett_kontakt(TEXT, UUID, TEXT,'
            ' BYTEA, BYTEA, TEXT, TEXT) TO disponit';
    END IF;
END $$;
RESET ROLE;
