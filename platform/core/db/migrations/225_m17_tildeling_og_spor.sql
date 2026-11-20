-- 225 — M-17: TILDELING OG SPOR PER HENVENDELSE (felles innboks).
--
-- Eiers bestilling 20.11.2026: bedrifter med 5–30 ansatte og en felles
-- innboks (post@, kundeservice@, ordre@) som flere deler på. To ting
-- registeret ikke kunne:
--
--   1. SI HVEM SOM HAR SAKEN. Køen viste henvendelsen, ikke hvem som
--      holdt i den — to kolleger kunne skrive hvert sitt svar.
--   2. VISE HVA SOM ER GJORT MED ÉN HENVENDELSE. `m17_evidens` bokfører
--      hver handling i `revisjonslogg`, men henvendelsens id står bare
--      INNE I HASHEN (`input_hash`). Loggen kan bevise at noe skjedde;
--      den kan ikke slås opp per henvendelse.
--
-- Sporet er derfor en EGEN, tenantbundet tabell som peker på loggposten.
-- Den skrives av samme dør som skriver evidensen (`m17_evidens`), i samme
-- transaksjon — det finnes ikke en handling med evidens og uten spor fra
-- denne migrasjonen av. ELDRE handlinger kan ikke gjenskapes fra loggen
-- (hashen er enveis); mottak og lukking fylles inn fra registerets egne
-- kolonner, resten begynner her. Flaten sier det.
--
-- HVERKEN EMNE, KROPP ELLER AVSENDER STÅR I SPORET — samme regel som
-- evidensen (102): handling, aktør, tid og den korte detaljen døra alt
-- bokfører. `bruker_id` i en tildeling er en intern id, ikke en adresse.

CREATE TABLE henvendelsesspor (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    spor_id BIGINT GENERATED ALWAYS AS IDENTITY,
    henvendelse_id UUID NOT NULL,
    handling TEXT NOT NULL CHECK (handling ~ '^[a-z_]+(\.[a-z_]+)+$'),
    aktor TEXT NOT NULL CHECK (length(btrim(aktor)) > 0),
    detalj JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- NULL bare for radene som fylles inn fra registeret under: de har
    -- en loggpost, men hvilken kan ikke utledes av hashen.
    loggpost_id BIGINT,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT henvendelsesspor_pk PRIMARY KEY (tenant, spor_id)
);
CREATE INDEX henvendelsesspor_per_henvendelse
    ON henvendelsesspor (tenant, henvendelse_id, ts, spor_id);

-- ÉN RAD PER HENVENDELSE: den som har saken NÅ. Historikken er sporet
-- (`henvendelse.tildelt` / `henvendelse.frigitt`), ikke denne tabellen.
CREATE TABLE henvendelsestildeling (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    henvendelse_id UUID NOT NULL,
    bruker_id TEXT NOT NULL CHECK (length(btrim(bruker_id)) > 0),
    tildelt_av TEXT NOT NULL CHECK (length(btrim(tildelt_av)) > 0),
    tildelt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT henvendelsestildeling_pk PRIMARY KEY (tenant, henvendelse_id),
    CONSTRAINT henvendelsestildeling_fk FOREIGN KEY (tenant, henvendelse_id)
        REFERENCES henvendelse (tenant, henvendelse_id)
);

ALTER TABLE henvendelsesspor ENABLE ROW LEVEL SECURITY;
ALTER TABLE henvendelsesspor FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON henvendelsesspor
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE henvendelsestildeling ENABLE ROW LEVEL SECURITY;
ALTER TABLE henvendelsestildeling FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON henvendelsestildeling
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

REVOKE ALL ON henvendelsesspor, henvendelsestildeling FROM PUBLIC;
-- Sporet er APPEND-ONLY ved grant: eieren kan skrive og lese, aldri
-- endre eller slette. Ingen runtime-rolle får en tabellrettighet (SP-7).
GRANT SELECT, INSERT ON henvendelsesspor TO disponit_kundeservice_eier;
GRANT SELECT, INSERT, UPDATE, DELETE ON henvendelsestildeling
    TO disponit_kundeservice_eier;

-- ------------------------------------------------------------
-- Dørene. Eid av `disponit_kundeservice_eier`, som resten av M-17.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_kundeservice_eier;

-- DET SOM KAN FYLLES INN, FYLLES INN: mottak og lukking står i
-- registerets egne kolonner. Eieren finner tenantene uten kontekst
-- (`m17_sveip_tenantliste`, 102) og skriver med RADENS tenant satt —
-- samme form som sveipen.
DO $$
DECLARE v_tenanter TEXT[]; v_t TEXT;
BEGIN
    PERFORM set_config('disponit.tenant', '', true);
    SELECT array_agg(DISTINCT h.tenant ORDER BY h.tenant) INTO v_tenanter
      FROM public.henvendelse h;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        INSERT INTO public.henvendelsesspor
            (tenant, henvendelse_id, handling, aktor, detalj, ts)
        SELECT h.tenant, h.henvendelse_id, 'henvendelse.mottatt',
               h.opprettet_av,
               jsonb_build_object('kanal', h.kanal, 'etterfylt', true),
               h.opprettet
          FROM public.henvendelse h WHERE h.tenant = v_t;
        INSERT INTO public.henvendelsesspor
            (tenant, henvendelse_id, handling, aktor, detalj, ts)
        SELECT h.tenant, h.henvendelse_id, 'henvendelse.lukket',
               h.lukket_av,
               jsonb_build_object('utfall', h.lukket_utfall,
                                  'etterfylt', true),
               h.lukket_ts
          FROM public.henvendelse h
         WHERE h.tenant = v_t AND h.lukket_ts IS NOT NULL;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
END $$;

-- EVIDENSDØRA SKRIVER NÅ OGSÅ SPORET. Signaturen og loggposten er
-- uendret (102); det eneste nye er raden i `henvendelsesspor` når
-- handlingen gjelder en henvendelse. Handlinger uten henvendelse
-- (`avsenderprofil.satt`, `stilleregler.satt`) har ikke noe spor å stå i.
CREATE OR REPLACE FUNCTION m17_evidens(p_tenant TEXT, p_henvendelse_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT; v_id BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm17_kundeservice', 'handling', p_handling,
        'henvendelse_id', p_henvendelse_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm17_kundeservice',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:henvendelsesregister', 'TILLAT',
            jsonb_build_array(p_handling), p_handling)
    RETURNING id INTO v_id;
    IF p_henvendelse_id IS NOT NULL THEN
        INSERT INTO public.henvendelsesspor
            (tenant, henvendelse_id, handling, aktor, detalj, loggpost_id)
        VALUES (p_tenant, p_henvendelse_id, p_handling, p_aktor,
                coalesce(p_detalj, '{}'::jsonb), v_id);
    END IF;
    RETURN v_id;
END $$;
REVOKE ALL ON FUNCTION m17_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- TILDELINGSDØRA. `p_bruker_id` NULL frigir saken. Svaret er om noe
-- ENDRET seg: å tildele den som alt har saken er et stille ja uten ny
-- sporrad — ellers ville et dobbeltklikk sett ut som to handlinger.
--
-- AT BRUKEREN ER MEDLEM AV FIRMAET prøves i API-laget (`brukermedlemskap`
-- er ikke kundeserviceeierens tabell, og skal ikke bli det). Døra krever
-- bare at henvendelsen finnes og er ÅPEN: en lukket sak har ingen som
-- «har» den.
CREATE FUNCTION m17_tildel(p_tenant TEXT, p_henvendelse_id UUID,
                           p_bruker_id TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_lukket TIMESTAMPTZ; v_naa TEXT; v_ny TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_tildel');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    v_ny := nullif(btrim(coalesce(p_bruker_id, '')), '');
    -- LÅS FØRST, LES ETTERPÅ: to kolleger som tar samme sak samtidig
    -- skal serialiseres på henvendelsen, ikke begge se den ledig.
    SELECT lukket_ts INTO v_lukket FROM public.henvendelse
     WHERE tenant = p_tenant AND henvendelse_id = p_henvendelse_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm17_tildel: henvendelsen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_lukket IS NOT NULL THEN
        RAISE EXCEPTION 'm17_tildel: henvendelsen er lukket'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT bruker_id INTO v_naa FROM public.henvendelsestildeling
     WHERE tenant = p_tenant AND henvendelse_id = p_henvendelse_id;
    IF v_naa IS NOT DISTINCT FROM v_ny THEN
        RETURN false;                              -- stille ja
    END IF;
    IF v_ny IS NULL THEN
        DELETE FROM public.henvendelsestildeling
         WHERE tenant = p_tenant AND henvendelse_id = p_henvendelse_id;
        PERFORM public.m17_evidens(
            p_tenant, p_henvendelse_id, 'henvendelse.frigitt', p_aktor,
            jsonb_build_object('fra', v_naa));
    ELSE
        INSERT INTO public.henvendelsestildeling
            (tenant, henvendelse_id, bruker_id, tildelt_av)
        VALUES (p_tenant, p_henvendelse_id, v_ny, p_aktor)
        ON CONFLICT (tenant, henvendelse_id) DO UPDATE
           SET bruker_id = EXCLUDED.bruker_id,
               tildelt_av = EXCLUDED.tildelt_av, tildelt_ts = now();
        PERFORM public.m17_evidens(
            p_tenant, p_henvendelse_id, 'henvendelse.tildelt', p_aktor,
            jsonb_build_object('til', v_ny, 'fra', v_naa));
    END IF;
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m17_tildel(TEXT, UUID, TEXT, TEXT) FROM PUBLIC;

-- HVEM HAR HVA: én rad per ÅPEN henvendelse som er tildelt. Egen dør og
-- ikke en ny kolonne i `m17_koen` — køens form er bundet av flere porter,
-- og en tildeling er et annet spørsmål enn køens rekkefølge.
CREATE FUNCTION m17_tildelingene(p_tenant TEXT)
RETURNS TABLE(henvendelse_id UUID, bruker_id TEXT, tildelt_av TEXT,
              tildelt_ts TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_tildelingene');
    RETURN QUERY
    SELECT t.henvendelse_id, t.bruker_id, t.tildelt_av, t.tildelt_ts
      FROM public.henvendelsestildeling t
      JOIN public.henvendelse h
        ON h.tenant = t.tenant AND h.henvendelse_id = t.henvendelse_id
     WHERE t.tenant = p_tenant AND h.lukket_ts IS NULL
     ORDER BY t.henvendelse_id;
END $$;
REVOKE ALL ON FUNCTION m17_tildelingene(TEXT) FROM PUBLIC;

-- SPORET FOR ÉN HENVENDELSE, eldst først. Avkortet, og sier det ikke
-- selv — API-laget ber om én mer enn det viser.
CREATE FUNCTION m17_sporet(p_tenant TEXT, p_henvendelse_id UUID,
                           p_grense INT)
RETURNS TABLE(spor_id BIGINT, handling TEXT, aktor TEXT, detalj JSONB,
              ts TIMESTAMPTZ, loggpost_id BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_sporet');
    RETURN QUERY
    SELECT s.spor_id, s.handling, s.aktor, s.detalj, s.ts, s.loggpost_id
      FROM public.henvendelsesspor s
     WHERE s.tenant = p_tenant AND s.henvendelse_id = p_henvendelse_id
     ORDER BY s.ts, s.spor_id
     LIMIT greatest(least(coalesce(p_grense, 200), 1000), 1);
END $$;
REVOKE ALL ON FUNCTION m17_sporet(TEXT, UUID, INT) FROM PUBLIC;

RESET ROLE;
