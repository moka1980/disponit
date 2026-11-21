-- 230 — M-17: SVARMALER.
--
-- Målt 21.11.2026: plattformen foreslår ikke svar. Hvert utkast skrives av
-- et menneske, fra blankt. Den lokale modellen på serveren brukte 114
-- sekunder på ett utkast, skrev dårlig norsk og fant på fakta — den er
-- ikke noe å gi til kunder. Det som sparer skrivetid i dag, uten modell og
-- uten at noe forlater serveren, er firmaets EGNE formuleringer: en mal per
-- vanlig henvendelse, valgt og tilpasset av den som har saken.
--
-- EN MAL ER FIRMAETS TEKST, IKKE KUNDENS. Den bærer ingen persondata og
-- lagres i klartekst (til forskjell fra utkast og henvendelser). Den er
-- et utgangspunkt: utkastet som lages av den, går gjennom nøyaktig samme
-- vei som alle andre — et menneske godkjenner før noe sendes.
--
-- Sletting er en markering (`slettet_ts`): navnet frigjøres, raden består.

CREATE TABLE svarmal (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    mal_id UUID NOT NULL,
    navn TEXT NOT NULL CHECK (length(btrim(navn)) BETWEEN 1 AND 80),
    tema TEXT CHECK (tema IS NULL OR tema IN
        ('faktura', 'leveranse', 'teknisk', 'salg', 'klage', 'annet')),
    tekst TEXT NOT NULL CHECK (length(btrim(tekst)) BETWEEN 1 AND 4000),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (length(btrim(opprettet_av)) > 0),
    endret TIMESTAMPTZ NOT NULL DEFAULT now(),
    endret_av TEXT NOT NULL CHECK (length(btrim(endret_av)) > 0),
    slettet_ts TIMESTAMPTZ,
    CONSTRAINT svarmal_pk PRIMARY KEY (tenant, mal_id)
);
-- Ett LEVENDE navn per firma: to maler med samme navn kan ikke skilles i
-- en nedtrekksliste.
CREATE UNIQUE INDEX svarmal_levende_navn
    ON svarmal (tenant, lower(btrim(navn))) WHERE slettet_ts IS NULL;

ALTER TABLE svarmal ENABLE ROW LEVEL SECURITY;
ALTER TABLE svarmal FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON svarmal
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

REVOKE ALL ON svarmal FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON svarmal TO disponit_kundeservice_eier;

SET LOCAL ROLE disponit_kundeservice_eier;

-- Lagrer en mal: ny, eller endring av en som finnes. -> ny?
CREATE FUNCTION m17_lagre_svarmal(p_tenant TEXT, p_mal_id UUID,
                                  p_navn TEXT, p_tema TEXT, p_tekst TEXT,
                                  p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_finnes BOOLEAN; v_antall INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_lagre_svarmal');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_aktor IS NULL OR btrim(p_aktor) = '' OR p_mal_id IS NULL THEN
        RAISE EXCEPTION 'm17_lagre_svarmal: mal og aktør kreves'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF length(btrim(coalesce(p_navn, ''))) NOT BETWEEN 1 AND 80
       OR length(btrim(coalesce(p_tekst, ''))) NOT BETWEEN 1 AND 4000 THEN
        RAISE EXCEPTION 'm17_lagre_svarmal: navn (1–80) og tekst (1–4000)'
            ' kreves' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_tema IS NOT NULL AND p_tema NOT IN
        ('faktura', 'leveranse', 'teknisk', 'salg', 'klage', 'annet') THEN
        RAISE EXCEPTION 'm17_lagre_svarmal: ukjent tema'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT true INTO v_finnes FROM public.svarmal s
     WHERE s.tenant = p_tenant AND s.mal_id = p_mal_id FOR UPDATE;
    IF v_finnes THEN
        UPDATE public.svarmal
           SET navn = btrim(p_navn), tema = p_tema, tekst = btrim(p_tekst),
               endret = now(), endret_av = p_aktor, slettet_ts = NULL
         WHERE tenant = p_tenant AND mal_id = p_mal_id;
    ELSE
        -- ET TAK: malene er en nedtrekksliste, ikke et arkiv.
        SELECT count(*) INTO v_antall FROM public.svarmal s
         WHERE s.tenant = p_tenant AND s.slettet_ts IS NULL;
        IF v_antall >= 100 THEN
            RAISE EXCEPTION 'm17_lagre_svarmal: taket på 100 maler er nådd'
                USING ERRCODE = 'check_violation';
        END IF;
        INSERT INTO public.svarmal (tenant, mal_id, navn, tema, tekst,
                                    opprettet_av, endret_av)
        VALUES (p_tenant, p_mal_id, btrim(p_navn), p_tema, btrim(p_tekst),
                p_aktor, p_aktor);
    END IF;
    PERFORM public.m17_evidens(
        p_tenant, NULL, 'svarmal.lagret', p_aktor,
        jsonb_build_object('mal_id', p_mal_id::text, 'tema', p_tema,
                           'ny', v_finnes IS NOT TRUE));
    RETURN v_finnes IS NOT TRUE;
END $$;
REVOKE ALL ON FUNCTION m17_lagre_svarmal(TEXT, UUID, TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC;

CREATE FUNCTION m17_slett_svarmal(p_tenant TEXT, p_mal_id UUID, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_slett_svarmal');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm17_slett_svarmal: en sletting har en aktør'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.svarmal
       SET slettet_ts = now(), endret = now(), endret_av = p_aktor
     WHERE tenant = p_tenant AND mal_id = p_mal_id AND slettet_ts IS NULL;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;                              -- stille ja
    END IF;
    PERFORM public.m17_evidens(
        p_tenant, NULL, 'svarmal.slettet', p_aktor,
        jsonb_build_object('mal_id', p_mal_id::text));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m17_slett_svarmal(TEXT, UUID, TEXT) FROM PUBLIC;

CREATE FUNCTION m17_svarmalene(p_tenant TEXT)
RETURNS TABLE(mal_id UUID, navn TEXT, tema TEXT, tekst TEXT,
              endret TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_svarmalene');
    RETURN QUERY
    SELECT s.mal_id, s.navn, s.tema, s.tekst, s.endret
      FROM public.svarmal s
     WHERE s.tenant = p_tenant AND s.slettet_ts IS NULL
     ORDER BY lower(s.navn), s.mal_id;
END $$;
REVOKE ALL ON FUNCTION m17_svarmalene(TEXT) FROM PUBLIC;

RESET ROLE;
