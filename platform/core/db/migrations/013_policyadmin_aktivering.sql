-- ============================================================
-- 013 — Policyadministrasjon v1: herdet `aktiver_policy` (PR-013 CP5, V10)
--
-- Runtime-rollene mangler direkte INSERT/UPDATE/DELETE på `policyer` og
-- `policy_hode` (kun SELECT). Aktivering skjer KUN gjennom denne herdede
-- funksjonen — SECURITY DEFINER, eid av NOLOGIN-rollen `disponit_policy_eier`,
-- `search_path=pg_catalog`, EXECUTE kun til runtime. Den kan ALDRI deaktivere
-- uten å sette inn en etterfølger i SAMME transaksjon: deaktivering og
-- innsetting er én udelelig operasjon, så en tenant aldri står med NULL aktive
-- (V6/v3 §6). Feiler innsettingen, rulles hele operasjonen tilbake og den gamle
-- aktive policyen består (krasjtest, v3 §6).
--
-- Kalleren (Python-orkestreringen) holder allerede `policy_hode`-låsen og har
-- rekalkulert diff/klasse under den; funksjonen tar låsen på nytt (idempotent i
-- samme tx) for defense-in-depth og gjør konfliktkontroll mot base-versjonen.
-- ============================================================

SET LOCAL ROLE disponit_policy_eier;
CREATE OR REPLACE FUNCTION aktiver_policy(
    p_tenant        TEXT,
    p_policy_id     TEXT,
    p_innhold       JSONB,
    p_innholds_hash TEXT,
    p_status        TEXT,
    p_base_versjon  TEXT)          -- forventet gjeldende aktiv (NULL = deny-all)
RETURNS TEXT
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    v_neste INT;
    v_aktiv TEXT;
    v_ny    TEXT;
BEGIN
    -- Lås ankerraden (V1). Finnes den ikke, opprett den (onboarding-idempotens).
    INSERT INTO public.policy_hode (tenant, policy_id)
        VALUES (p_tenant, p_policy_id)
        ON CONFLICT (tenant, policy_id) DO NOTHING;
    SELECT neste_versjon, aktiv_versjon INTO v_neste, v_aktiv
      FROM public.policy_hode
     WHERE tenant = p_tenant AND policy_id = p_policy_id
       FOR UPDATE;

    -- Konfliktdeteksjon (§4): den vi diffet/godkjente mot MÅ fortsatt være
    -- aktiv. En konkurrerende aktivering flyttet pekeren → rebasering kreves.
    IF v_aktiv IS DISTINCT FROM p_base_versjon THEN
        RAISE EXCEPTION 'aktiver_policy: base % er ikke lenger aktiv (%) — '
            'rebasering kreves', p_base_versjon, v_aktiv
            USING ERRCODE = 'serialization_failure';
    END IF;

    v_ny := v_neste::text;

    -- Deaktiver forrige + sett inn etterfølger i SAMME operasjon (V10).
    IF v_aktiv IS NOT NULL THEN
        UPDATE public.policyer SET aktiv = false
         WHERE tenant = p_tenant AND policy_id = p_policy_id AND versjon = v_aktiv;
    END IF;
    INSERT INTO public.policyer
        (tenant, policy_id, versjon, innholds_hash, status, innhold, aktiv)
      VALUES (p_tenant, p_policy_id, v_ny, p_innholds_hash, p_status,
              p_innhold, true);
    -- Flytt den autoritative pekeren + monoton allokering/revisjon.
    UPDATE public.policy_hode
       SET aktiv_versjon = v_ny,
           neste_versjon  = v_neste + 1,
           revisjon       = revisjon + 1
     WHERE tenant = p_tenant AND policy_id = p_policy_id;

    RETURN v_ny;
END $$;
REVOKE ALL ON FUNCTION aktiver_policy(TEXT, TEXT, JSONB, TEXT, TEXT, TEXT)
    FROM PUBLIC;
-- EXECUTE gis av EIEREN (disponit_policy_eier) — som reserver_kapabilitet i
-- migrer.py — men her ligger den i migrasjonen fordi funksjonen defineres her.
GRANT EXECUTE ON FUNCTION aktiver_policy(TEXT, TEXT, JSONB, TEXT, TEXT, TEXT)
    TO disponit;
RESET ROLE;

-- Eieren (policy_eier) må kunne SKRIVE `policyer`/`policy_hode` når funksjonen
-- kjører (SECURITY DEFINER kjører som eier). Grantet hører hjemme HER, sammen
-- med funksjonen — ikke som løs kode i migrer.py sin kjor(): da overlever det
-- ENHVER skjemagjenoppbygging (også testenes `_nullstill` + re-migrer, som
-- dropper tabellene og dermed grantene før de re-migrerer), ikke bare den
-- aller første driftsoppsett-kjøringen. Kjøres som migrator (eier av
-- tabellene) etter RESET ROLE.
GRANT SELECT, INSERT, UPDATE ON policyer, policy_hode TO disponit_policy_eier;
