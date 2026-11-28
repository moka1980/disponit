-- 238 — FRISTVAKTEN VIRKER AV SEG SELV.
--
-- 237 ga kunden fristene da hun trykket «Lag fristene mine», og igjen når
-- hun trykket «Legg til» etter at katalogen fikk nye. En vakt kunden må
-- huske å passe på, er ingen vakt: firmaet som aldri logger inn, er
-- nettopp det som trenger varselet.
--
-- NÅ: en sveip (`lovfrister_synk`) legger katalogens nye frister inn hos
-- hvert firma som har en pliktprofil — når katalogen er endret, og ellers
-- én gang i døgnet (vinduet på 400 døgn flytter seg). Den kalles fra
-- `platform/drift/evidensreaper.py`, timeren som alt kjører
-- prøveutløpssveipen (190/193) hvert femte minutt.
--
-- ÉN REGISTRERINGSVEI. `lovfrister_registrer` er døra BÅDE kundens knapp og
-- sveipen går gjennom — utvalget (organisasjonsform, MVA, ansatte),
-- teksten og plikt-id-en bor ett sted. 237 hadde dem i Python; en sveip i
-- basen med sin egen kopi ville drevet fra den.
--
-- PLIKT-ID-EN er avledet av (tenant, plikt, frist), så et gjenspill
-- registrerer ingenting to ganger (M-21s SP-2). Avledningen flyttes hit
-- fra Python (uuid5 → md5): ingen firma hadde en profil da dette ble
-- skrevet (målt i drift 28/11), så ingenting er registrert med den gamle.
--
-- EIEREN AV PLIKTENE er den som lagret profilen (`oppdatert_av`). Er hun
-- ikke lenger et aktivt medlem, nekter M-21 (en plikt uten eier er en
-- plikt ingen gjør) — sveipen hopper da over firmaet og prøver igjen
-- neste døgn, og melder det i svaret sitt (`nye = -1`).
--
-- KRYSS-TENANT-SYNET ER EKSPLISITT OG SNEVERT (096-formen): en egen
-- SELECT-policy for dørenes eier, som bare gjelder UTEN tenantkontekst —
-- altså i det ene vinduet sveipen bruker for å finne hvem som står for tur.
-- ============================================================

ALTER TABLE firma_pliktprofil ADD COLUMN synket TIMESTAMPTZ;

CREATE POLICY lovfrister_synk_tenantliste ON firma_pliktprofil
    FOR SELECT TO disponit_plattform_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- Døra i 096 eies av `disponit_plikt_eier`; grantet gis av eieren (193s
-- lærdom: migrator kan ikke gi bort en rettighet den ikke har).
SET LOCAL ROLE disponit_plikt_eier;
GRANT EXECUTE ON FUNCTION m21_registrer_plikt(
    TEXT, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ, TEXT, INT[], TEXT)
    TO disponit_plattform_eier;
RESET ROLE;

SET LOCAL ROLE disponit_plattform_eier;

CREATE FUNCTION lovfrister_plikt_id(p_tenant TEXT, p_nokkel TEXT,
                                    p_frist DATE)
RETURNS UUID LANGUAGE sql STABLE
SET search_path = pg_catalog AS $$
    SELECT md5('disponit:lovfrister:' || p_tenant || '|' || p_nokkel || '|'
               || to_char(p_frist, 'YYYY-MM-DD'))::uuid
$$;

CREATE FUNCTION lovfrister_registrer(p_tenant TEXT, p_eier TEXT,
                                     p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_p RECORD; v_idag DATE; v_ny BOOLEAN; v_n INT := 0; r RECORD;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '')
       IS DISTINCT FROM p_tenant THEN
        RAISE EXCEPTION 'lovfrister_registrer: p_tenant er ikke kallerens'
                        ' kontekst' USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT p.orgform, p.mva, p.ansatte INTO v_p
      FROM public.firma_pliktprofil p WHERE p.tenant = p_tenant;
    IF NOT FOUND THEN
        RETURN 0;
    END IF;
    -- Fristen er en DATO, og datoer er lokale (190s lærdom).
    v_idag := (now() AT TIME ZONE 'Europe/Oslo')::date;
    FOR r IN
        SELECT m.nokkel, m.tittel, m.hjemmel, m.kilde_url, f.periode, f.frist
          FROM public.plattform_pliktmal m
          JOIN public.plattform_pliktfrist f ON f.nokkel = m.nokkel
         WHERE m.aktiv
           AND (cardinality(m.orgformer) = 0
                OR v_p.orgform = ANY (m.orgformer))
           AND (NOT m.krever_mva OR v_p.mva)
           AND (NOT m.krever_ansatte OR v_p.ansatte)
           AND f.frist BETWEEN v_idag AND v_idag + 400
         ORDER BY f.frist, m.nokkel
    LOOP
        BEGIN
            v_ny := public.m21_registrer_plikt(
                p_tenant,
                public.lovfrister_plikt_id(p_tenant, r.nokkel, r.frist),
                left(r.tittel || ' – ' || r.periode, 200), p_eier,
                left(r.hjemmel || ' — ' || r.kilde_url, 500),
                (r.frist + time '23:59') AT TIME ZONE 'Europe/Oslo',
                'engang', NULL, p_aktor);
        EXCEPTION WHEN unique_violation THEN
            -- Står fra før med en annen tekst (eieren har rettet malen,
            -- eller plikten har fått en annen eier). Den røres ikke.
            v_ny := false;
        END;
        IF v_ny THEN
            v_n := v_n + 1;
        END IF;
    END LOOP;
    UPDATE public.firma_pliktprofil SET synket = now()
     WHERE tenant = p_tenant;
    RETURN v_n;
END $$;

CREATE FUNCTION lovfrister_synk(p_grense INT DEFAULT 50)
RETURNS TABLE (tenant TEXT, nye INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kontekst TEXT; v_aktor TEXT; v_endret TIMESTAMPTZ;
        v_tenanter TEXT[]; v_av TEXT[]; v_i INT; v_n INT;
BEGIN
    v_kontekst := current_setting('disponit.tenant', true);
    v_aktor := current_setting('disponit.aktor', true);
    PERFORM set_config('disponit.tenant', '', true);
    SELECT greatest((SELECT max(m.endret) FROM public.plattform_pliktmal m),
                    (SELECT max(f.endret) FROM public.plattform_pliktfrist f))
      INTO v_endret;
    -- Hele utvalget hentes FØR konteksten settes: kryss-tenant-policyen
    -- gjelder bare uten kontekst, og en løkke over en åpen spørring ville
    -- mistet resten av radene i det første firmaet fikk sin.
    SELECT array_agg(k.tenant ORDER BY k.rn), array_agg(k.av ORDER BY k.rn)
      INTO v_tenanter, v_av
      FROM (SELECT p.tenant, p.oppdatert_av AS av,
                   row_number() OVER (ORDER BY p.synket NULLS FIRST,
                                               p.tenant) AS rn
              FROM public.firma_pliktprofil p
             WHERE p.synket IS NULL
                OR p.synket < coalesce(v_endret, '-infinity')
                OR p.synket < now() - interval '20 hours'
             ORDER BY p.synket NULLS FIRST, p.tenant
             LIMIT greatest(coalesce(p_grense, 50), 1)) k;
    FOR v_i IN 1 .. coalesce(array_length(v_tenanter, 1), 0) LOOP
        PERFORM set_config('disponit.tenant', v_tenanter[v_i], true);
        BEGIN
            IF v_av[v_i] NOT LIKE 'bruker:%' THEN
                RAISE EXCEPTION 'lovfrister_synk: profilen har ingen eier'
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
            v_n := public.lovfrister_registrer(
                v_tenanter[v_i], substr(v_av[v_i], 8), 'system:lovfrister');
        EXCEPTION WHEN invalid_parameter_value THEN
            -- Eieren er ikke lenger et aktivt medlem. Stempelet settes
            -- likevel, så firmaet prøves igjen i morgen og ikke hvert
            -- femte minutt.
            UPDATE public.firma_pliktprofil SET synket = now()
             WHERE firma_pliktprofil.tenant = v_tenanter[v_i];
            v_n := -1;
        END;
        IF v_n <> 0 THEN
            tenant := v_tenanter[v_i]; nye := v_n;
            RETURN NEXT;
        END IF;
    END LOOP;
    PERFORM set_config('disponit.tenant', coalesce(v_kontekst, ''), true);
    PERFORM set_config('disponit.aktor', coalesce(v_aktor, ''), true);
END $$;

REVOKE ALL ON FUNCTION lovfrister_plikt_id(TEXT, TEXT, DATE) FROM PUBLIC;
REVOKE ALL ON FUNCTION lovfrister_registrer(TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION lovfrister_synk(INT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION lovfrister_registrer(TEXT, TEXT, TEXT) TO disponit;

-- SVEIPEN ER KRYSS-TENANT: ryddekontoen eier jobben der den finnes, og
-- runtime er da nektet. Der den mangler (CI, lokalt) faller den til
-- runtime, så driftsveien kan MÅLES (193-formen, ordrett).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_domener') THEN
        GRANT EXECUTE ON FUNCTION lovfrister_synk(INT) TO disponit_domener;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
            REVOKE EXECUTE ON FUNCTION lovfrister_synk(INT) FROM disponit;
        END IF;
    ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        GRANT EXECUTE ON FUNCTION lovfrister_synk(INT) TO disponit;
    END IF;
END $$;

RESET ROLE;
