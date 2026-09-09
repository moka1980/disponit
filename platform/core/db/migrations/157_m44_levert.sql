-- =====================================================================
-- 157 — M-44: KVITTERINGEN NÅR REGISTERET — KAMPANJEN ER LEVERT
-- =====================================================================
--
-- ARC B kampanje, PR 5. Eiermodulen (156) kvitterer signert og
-- ressursbundet til (kampanje, mottaker). Kvitteringen er evidensen —
-- men registeret må også VITE det: «fikk denne mottakeren denne
-- kampanjen, og når» er spørsmålet en avmelding, en klage og et tilsyn
-- stiller. `kampanjelevering` er svaret: én rad per (kampanje,
-- mottaker), skrevet av kvitteringens vei (API-et, i SAMME transaksjon
-- som oppdraget lukkes), append-only, med evidens `kampanje.levert`.
--
-- INGEN `sendt`-KOLONNE PÅ KAMPANJEN (114s doktrine står): leveringen er
-- en egen, målt hendelse per mottaker — ikke et flagg noen kunne satt.
-- Kontaktpunktet står ALDRI her: masken er det eneste sporet av
-- adressen, som i 150.
-- ---------------------------------------------------------------------

CREATE TABLE kampanjelevering (
    tenant         TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kampanje_id    UUID NOT NULL,
    mottaker_id    UUID NOT NULL,
    oppdrag_id     BIGINT NOT NULL,
    levert_ts      TIMESTAMPTZ NOT NULL,
    malversjon     TEXT CHECK (malversjon IS NULL OR length(malversjon) <= 64),
    mottaker_maske TEXT CHECK (mottaker_maske IS NULL
                               OR length(mottaker_maske) <= 254),
    registrert     TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av  TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT kampanjelevering_pk PRIMARY KEY (tenant, kampanje_id,
                                                mottaker_id),
    CONSTRAINT kampanjelevering_plan_fk
        FOREIGN KEY (tenant, kampanje_id, mottaker_id)
        REFERENCES kampanjeplan (tenant, kampanje_id, mottaker_id),
    CONSTRAINT kampanjelevering_oppdrag_unik UNIQUE (tenant, oppdrag_id)
);
ALTER TABLE kampanjelevering ENABLE ROW LEVEL SECURITY;
ALTER TABLE kampanjelevering FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON kampanjelevering
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
GRANT SELECT, INSERT ON kampanjelevering TO disponit_kampanje_eier;
CREATE TRIGGER m44_kampanjelevering_ingen_truncate
    BEFORE TRUNCATE ON kampanjelevering
    EXECUTE FUNCTION m44_plan_vakt();

SET LOCAL ROLE disponit_kampanje_eier;

CREATE FUNCTION m44_kampanje_levert(
    p_tenant TEXT, p_kampanje_id UUID, p_mottaker_id UUID,
    p_oppdrag_id BIGINT, p_levert_ts TIMESTAMPTZ, p_malversjon TEXT,
    p_mottaker_maske TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_n INT; v_ts TIMESTAMPTZ := coalesce(p_levert_ts, now());
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_kampanje_levert');
    IF p_oppdrag_id IS NULL THEN
        RAISE EXCEPTION 'm44_kampanje_levert: oppdraget må være satt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.kampanjeplan pl
                    WHERE pl.tenant = p_tenant
                      AND pl.kampanje_id = p_kampanje_id
                      AND pl.mottaker_id = p_mottaker_id) THEN
        RAISE EXCEPTION 'm44_kampanje_levert: paret står ikke i planen'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    INSERT INTO public.kampanjelevering
        (tenant, kampanje_id, mottaker_id, oppdrag_id, levert_ts,
         malversjon, mottaker_maske, registrert_av)
    VALUES (p_tenant, p_kampanje_id, p_mottaker_id, p_oppdrag_id,
            v_ts, p_malversjon, p_mottaker_maske, p_aktor)
        ON CONFLICT ON CONSTRAINT kampanjelevering_pk DO NOTHING;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    IF v_n = 0 THEN
        RETURN false;                              -- gjenspill: stille ja
    END IF;
    PERFORM public.m44_evidens(
        p_tenant, p_mottaker_id, 'kampanje.levert', p_aktor,
        jsonb_build_object('kampanje_id', p_kampanje_id::text,
                           'oppdrag_id', p_oppdrag_id,
                           'levert_ts', v_ts,
                           'malversjon', p_malversjon,
                           'mottaker_maske', p_mottaker_maske));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m44_kampanje_levert(TEXT, UUID, UUID, BIGINT,
    TIMESTAMPTZ, TEXT, TEXT, TEXT) FROM PUBLIC;

-- Lesedøra for flaten og bevisene: hvem fikk kampanjen, og når.
CREATE FUNCTION m44_leveringene(p_tenant TEXT, p_kampanje_id UUID)
RETURNS TABLE(mottaker_id UUID, oppdrag_id BIGINT, levert_ts TIMESTAMPTZ,
              malversjon TEXT, mottaker_maske TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm44_leveringene');
    RETURN QUERY
    SELECT l.mottaker_id, l.oppdrag_id, l.levert_ts, l.malversjon,
           l.mottaker_maske
      FROM public.kampanjelevering l
     WHERE l.tenant = p_tenant AND l.kampanje_id = p_kampanje_id
     ORDER BY l.levert_ts, l.mottaker_id;
END $$;
REVOKE ALL ON FUNCTION m44_leveringene(TEXT, UUID) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_kampanje_levert(TEXT, UUID,'
            ' UUID, BIGINT, TIMESTAMPTZ, TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m44_leveringene(TEXT, UUID)'
            ' TO disponit';
    END IF;
END $$;
RESET ROLE;
