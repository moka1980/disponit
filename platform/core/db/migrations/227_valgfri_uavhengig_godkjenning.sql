-- 227 — FIRE ØYNE ER FIRMAETS VALG (ADR-010).
--
-- Eiers vedtak 21.11.2026: «to godkjennere kan vi ha som alternativ og
-- ikke som hindring … firmaene kan velge selv». Kundetypen er bedrifter
-- med 5–30 ansatte, og mange av dem har ÉN person som bestemmer. Kravet om
-- at en ANNEN enn forfatteren må attestere en policyendring (V6) stoppet
-- dem før de kom i gang: fullmakten til å sende et svar lot seg ikke gi.
--
-- REGELEN ETTER DENNE MIGRASJONEN
--   * `firma.uavhengig_godkjenning = true`  → som før: minst én attestasjon
--     fra en som IKKE skrev endringen, og to attestasjoner når endringen
--     utvider fullmaktene.
--   * `false` → forfatteren kan attestere og aktivere alene. Sporet sier
--     fortsatt hvem som gjorde det.
--
-- HVEM KAN ENDRE REGELEN
--   * FØRSTE valg (ved registrering): den som registrerer firmaet.
--   * `false → true` (strengere): én person alene.
--   * `true → false` (svakere): TO personer — én foreslår, en ANNEN
--     bekrefter innen sju døgn. Å fjerne fire øyne er selv en endring som
--     krever fire øyne; ellers var regelen bare en bryter.
--
-- EKSISTERENDE FIRMAER beholder `true`, og regnes som å ha valgt: ingen
-- firma får svakere kontroll av en migrasjon. Et firma UTEN rad i
-- `firma` (eldre tenanter) er `true`.

ALTER TABLE firma
    ADD COLUMN uavhengig_godkjenning BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN godkjenning_valgt_ts TIMESTAMPTZ,
    ADD COLUMN godkjenning_valgt_av TEXT,
    ADD COLUMN godkjenning_forslag_av TEXT,
    ADD COLUMN godkjenning_forslag_ts TIMESTAMPTZ,
    ADD CONSTRAINT firma_godkjenningsforslag_helhet
        CHECK ((godkjenning_forslag_av IS NULL)
               = (godkjenning_forslag_ts IS NULL));

-- De som finnes, har valgt — av historien.
UPDATE firma SET godkjenning_valgt_ts = opprettet,
                 godkjenning_valgt_av = 'migrasjon:227';

-- KVORUMGATEN leser regelen fra firmaets egen rad. Gaten er policy-eierens
-- (048); den får lese NØYAKTIG de to kolonnene den trenger.
GRANT SELECT (tenant, uavhengig_godkjenning) ON firma TO disponit_policy_eier;

SET LOCAL ROLE disponit_policy_eier;
CREATE OR REPLACE FUNCTION hendelse_kvorum_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_total INT; v_uavh INT; v_diff TEXT; v_krever BOOLEAN;
BEGIN
    IF NEW.aktiveringskilde IS DISTINCT FROM 'styrt' THEN
        RETURN NEW;
    END IF;
    SELECT ar.diff_hash INTO v_diff FROM public.aktiveringsrunde ar
     WHERE ar.tenant = NEW.tenant AND ar.utkast_id = NEW.utkast_id
       AND ar.runde = NEW.runde;
    SELECT count(*), count(*) FILTER (WHERE NOT a.er_forfatter)
      INTO v_total, v_uavh
      FROM public.aktiveringsattestasjon a
     WHERE a.tenant = NEW.tenant AND a.utkast_id = NEW.utkast_id
       AND a.runde = NEW.runde AND a.diff_hash = v_diff;
    -- 227: uten rad (eldre tenant) eller uten svar er regelen STRENG.
    SELECT f.uavhengig_godkjenning INTO v_krever FROM public.firma f
     WHERE f.tenant = NEW.tenant;
    v_krever := coalesce(v_krever, true);
    IF v_total < NEW.pakrevd_antall OR (v_krever AND v_uavh < 1) THEN
        RAISE EXCEPTION 'policyaktivering: styrt hendelse uten kvorum '
            '(% attestasjoner, % uavhengige, krav %)', v_total, v_uavh,
            NEW.pakrevd_antall
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'hendelse_kvorum_gate';
    END IF;
    RETURN NEW;
END $$;
RESET ROLE;

-- LESEDØRA: regelen, og om et forslag om å fjerne den står åpent.
CREATE FUNCTION firma_godkjenningsregel(p_tenant TEXT)
RETURNS TABLE (uavhengig BOOLEAN, valgt BOOLEAN, forslag_av TEXT,
               forslag_ts TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'firma_godkjenningsregel');
    RETURN QUERY
    SELECT f.uavhengig_godkjenning, f.godkjenning_valgt_ts IS NOT NULL,
           CASE WHEN f.godkjenning_forslag_ts > now() - interval '7 days'
                THEN f.godkjenning_forslag_av END,
           CASE WHEN f.godkjenning_forslag_ts > now() - interval '7 days'
                THEN f.godkjenning_forslag_ts END
      FROM public.firma f WHERE f.tenant = p_tenant;
    IF NOT FOUND THEN
        -- Ingen firmarad: regelen er streng, og den er ikke til å endre.
        uavhengig := true; valgt := true; forslag_av := NULL;
        forslag_ts := NULL;
        RETURN NEXT;
    END IF;
END $$;
REVOKE ALL ON FUNCTION firma_godkjenningsregel(TEXT) FROM PUBLIC;

-- SKRIVEDØRA. -> utfallet som ord:
--   'satt'      regelen er endret
--   'uendret'   den sto alt slik
--   'foreslatt' svekkelsen er FORESLÅTT og venter på en annen person
--   'venter'    aktøren har alt foreslått; hun kan ikke bekrefte sitt eget
CREATE FUNCTION firma_sett_godkjenningsregel(p_tenant TEXT,
                                             p_uavhengig BOOLEAN,
                                             p_aktor TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r public.firma%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'firma_sett_godkjenningsregel');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' OR p_uavhengig IS NULL THEN
        RAISE EXCEPTION 'firma_sett_godkjenningsregel: aktør og valg kreves'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO r FROM public.firma f WHERE f.tenant = p_tenant FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'firma_sett_godkjenningsregel: firmaet finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    -- FØRSTE VALG: den som registrerer firmaet velger, alene.
    IF r.godkjenning_valgt_ts IS NULL THEN
        UPDATE public.firma
           SET uavhengig_godkjenning = p_uavhengig,
               godkjenning_valgt_ts = now(), godkjenning_valgt_av = p_aktor,
               godkjenning_forslag_av = NULL, godkjenning_forslag_ts = NULL,
               endret = now(), endret_av = p_aktor
         WHERE firma.tenant = p_tenant;
        RETURN 'satt';
    END IF;

    IF r.uavhengig_godkjenning = p_uavhengig THEN
        -- Å be om det som alt gjelder trekker også et åpent forslag.
        IF r.godkjenning_forslag_av IS NOT NULL THEN
            UPDATE public.firma
               SET godkjenning_forslag_av = NULL,
                   godkjenning_forslag_ts = NULL,
                   endret = now(), endret_av = p_aktor
             WHERE firma.tenant = p_tenant;
        END IF;
        RETURN 'uendret';
    END IF;

    -- STRENGERE: én person alene.
    IF p_uavhengig THEN
        UPDATE public.firma
           SET uavhengig_godkjenning = true,
               godkjenning_valgt_ts = now(), godkjenning_valgt_av = p_aktor,
               godkjenning_forslag_av = NULL, godkjenning_forslag_ts = NULL,
               endret = now(), endret_av = p_aktor
         WHERE firma.tenant = p_tenant;
        RETURN 'satt';
    END IF;

    -- SVAKERE: to personer. Et ferskt forslag fra en ANNEN bekreftes her.
    IF r.godkjenning_forslag_av IS NOT NULL
       AND r.godkjenning_forslag_ts > now() - interval '7 days' THEN
        IF r.godkjenning_forslag_av = p_aktor THEN
            RETURN 'venter';
        END IF;
        UPDATE public.firma
           SET uavhengig_godkjenning = false,
               godkjenning_valgt_ts = now(),
               godkjenning_valgt_av = r.godkjenning_forslag_av || '+' || p_aktor,
               godkjenning_forslag_av = NULL, godkjenning_forslag_ts = NULL,
               endret = now(), endret_av = p_aktor
         WHERE firma.tenant = p_tenant;
        RETURN 'satt';
    END IF;
    UPDATE public.firma
       SET godkjenning_forslag_av = p_aktor, godkjenning_forslag_ts = now(),
           endret = now(), endret_av = p_aktor
     WHERE firma.tenant = p_tenant;
    RETURN 'foreslatt';
END $$;
REVOKE ALL ON FUNCTION firma_sett_godkjenningsregel(TEXT, BOOLEAN, TEXT)
    FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        GRANT EXECUTE ON FUNCTION firma_godkjenningsregel(TEXT) TO disponit;
        GRANT EXECUTE ON FUNCTION
            firma_sett_godkjenningsregel(TEXT, BOOLEAN, TEXT) TO disponit;
    END IF;
END $$;
