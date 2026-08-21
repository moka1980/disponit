-- 051 — M-16 nøkkeltall: LESE-definerne (klarsignalet 95174309…).
--
-- Klarsignalets «ingen migrasjon» = ingen skjema- eller dataendring, og
-- det holder denne fila: ren CREATE FUNCTION + to SELECT-grants. Ingen
-- tabeller, ingen indekser, ingen backfill — terskelen for senere
-- indeksarbeid er en målt spørring over 100 ms i prod, aldri en
-- flate-PR.
--
-- Formen er 044-leseform (SPEIL): STABLE SECURITY DEFINER eid av
-- disponit_m37_claimer, search_path pg_catalog, krev_tenantkontekst
-- (SP-1) + `tenant = p_tenant` i hvert skann. Flaten leser aldri
-- tabeller direkte (SP-7) — runtime får KUN EXECUTE.
--
-- SUMINVARIANTEN per partisjon bæres av GROUPING SETS ((nokkel), ()):
-- gruppene OG totalen telles i NØYAKTIG samme skann (ett snapshot), så
-- «delene summerer til totalen» holder per konstruksjon — også under
-- samtidig skriving (port 1). En verdi utenfor det kjente domenet
-- havner i sin egen gruppe og dermed I totalen, aldri stille utenfor.
-- INGEN divisjon i noen definer (statisk port); den eneste
-- differanseformen er radvis varighet på én lukket sak.

-- Kildene claimeren ikke alt leser (RLS-policyene er PUBLIC
-- tenant-isolasjon, så SELECT-granten er hele mangelen):
GRANT SELECT ON frekvens_hendelser, policyaktivering
    TO disponit_m37_claimer;

SET LOCAL ROLE disponit_m37_claimer;

-- Beslutninger: partisjon per beslutning + total, samme skann.
CREATE OR REPLACE FUNCTION m16_beslutninger(
    p_tenant TEXT, p_fra TIMESTAMPTZ, p_til TIMESTAMPTZ)
RETURNS TABLE(nokkel TEXT, antall BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm16_beslutninger');
    RETURN QUERY
    SELECT CASE WHEN GROUPING(r.beslutning) = 1 THEN '__total__'
                ELSE coalesce(r.beslutning, 'ukjent') END,
           count(*)
      FROM public.revisjonslogg r
     WHERE r.tenant = p_tenant AND r.ts >= p_fra AND r.ts < p_til
     GROUP BY GROUPING SETS ((r.beslutning), ());
END $$;

-- Frekvensreservasjoner: ett tall (kortets egen linje).
CREATE OR REPLACE FUNCTION m16_frekvensreservasjoner(
    p_tenant TEXT, p_fra TIMESTAMPTZ, p_til TIMESTAMPTZ)
RETURNS BIGINT
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm16_frekvensreservasjoner');
    SELECT count(*) INTO v FROM public.frekvens_hendelser f
     WHERE f.tenant = p_tenant
       AND f.tidspunkt >= p_fra AND f.tidspunkt < p_til;
    RETURN v;
END $$;

-- Policyaktiveringer: TRE partisjoner på samme kort — hver med sin egen
-- total fra sitt eget skann; de summeres aldri på tvers.
CREATE OR REPLACE FUNCTION m16_aktiveringer(
    p_tenant TEXT, p_fra TIMESTAMPTZ, p_til TIMESTAMPTZ)
RETURNS TABLE(partisjon TEXT, nokkel TEXT, antall BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm16_aktiveringer');
    RETURN QUERY
    SELECT 'kilde'::text,
           CASE WHEN GROUPING(a.aktiveringskilde) = 1 THEN '__total__'
                ELSE coalesce(a.aktiveringskilde, 'ukjent') END,
           count(*)
      FROM public.policyaktivering a
     WHERE a.tenant = p_tenant
       AND a.aktivert_ts >= p_fra AND a.aktivert_ts < p_til
     GROUP BY GROUPING SETS ((a.aktiveringskilde), ());
    RETURN QUERY
    SELECT 'kvorumskrav'::text,
           CASE WHEN GROUPING(a.pakrevd_antall) = 1 THEN '__total__'
                ELSE coalesce(a.pakrevd_antall::text, 'ukjent') END,
           count(*)
      FROM public.policyaktivering a
     WHERE a.tenant = p_tenant
       AND a.aktivert_ts >= p_fra AND a.aktivert_ts < p_til
     GROUP BY GROUPING SETS ((a.pakrevd_antall), ());
    -- Rå tellinger én/to attestanter — aldri en andel (grensen §1).
    RETURN QUERY
    SELECT 'attestanter'::text,
           CASE WHEN GROUPING((a.attestant_b IS NOT NULL)) = 1
                THEN '__total__'
                WHEN a.attestant_b IS NOT NULL THEN 'to'
                ELSE 'en' END,
           count(*)
      FROM public.policyaktivering a
     WHERE a.tenant = p_tenant
       AND a.aktivert_ts >= p_fra AND a.aktivert_ts < p_til
     GROUP BY GROUPING SETS (((a.attestant_b IS NOT NULL)), ());
END $$;

-- Oppdrag: partisjon per status, anker status_ts (siste overgang — det
-- er DEN hendelsen vinduet teller).
CREATE OR REPLACE FUNCTION m16_oppdrag(
    p_tenant TEXT, p_fra TIMESTAMPTZ, p_til TIMESTAMPTZ)
RETURNS TABLE(nokkel TEXT, antall BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm16_oppdrag');
    RETURN QUERY
    SELECT CASE WHEN GROUPING(o.status) = 1 THEN '__total__'
                ELSE coalesce(o.status, 'ukjent') END,
           count(*)
      FROM public.oppdrag o
     WHERE o.tenant = p_tenant
       AND o.status_ts >= p_fra AND o.status_ts < p_til
     GROUP BY GROUPING SETS ((o.status), ());
END $$;

-- Unntak, AKTIVITETSAKSEN: opprettet i vinduet, per kategori (anker ts).
-- Kun metadata — payloaden er kryptert og røres aldri herfra.
CREATE OR REPLACE FUNCTION m16_unntak_aktivitet(
    p_tenant TEXT, p_fra TIMESTAMPTZ, p_til TIMESTAMPTZ)
RETURNS TABLE(nokkel TEXT, antall BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm16_unntak_aktivitet');
    RETURN QUERY
    SELECT CASE WHEN GROUPING(u.kategori) = 1 THEN '__total__'
                ELSE coalesce(u.kategori, 'ukjent') END,
           count(*)
      FROM public.unntak u
     WHERE u.tenant = p_tenant AND u.ts >= p_fra AND u.ts < p_til
     GROUP BY GROUPING SETS ((u.kategori), ());
END $$;

-- Unntak, lukkede i vinduet (anker status_ts): RADFAKTA med radvis
-- varighet — aldri aggregert, aldri delt på noe. Terminalsettet er
-- KALLERENS (app.py::TERMINALE_UNNTAKSSTATUSER) — statusmaskinen
-- kopieres ikke inn i SQL (oversikt-lærdommen).
--
-- `antall_totalt` er HELE tellingen i vinduet, ikke antall RETURNERTE
-- rader: `count(*) OVER ()` regnes etter WHERE, men før ORDER BY og
-- LIMIT, altså i SAMME skann som radene. Uten den ville grensen
-- trunkert STILLE, og flaten påstått «lukket i vinduet» om et utsnitt
-- — en telling som ikke er hele tellingen skal aldri vises som om den
-- var det (SP: aldri stille ufullstendige fakta).
DROP FUNCTION IF EXISTS m16_unntak_lukkede(
    TEXT, TIMESTAMPTZ, TIMESTAMPTZ, TEXT[], INT);
CREATE FUNCTION m16_unntak_lukkede(
    p_tenant TEXT, p_fra TIMESTAMPTZ, p_til TIMESTAMPTZ,
    p_terminale TEXT[], p_grense INT)
RETURNS TABLE(id BIGINT, kategori TEXT, sakstype TEXT, status TEXT,
              opprettet TIMESTAMPTZ, lukket TIMESTAMPTZ, varighet_s BIGINT,
              antall_totalt BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
#variable_conflict use_column
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm16_unntak_lukkede');
    RETURN QUERY
    SELECT u.id, coalesce(u.kategori, 'ukjent'), u.sakstype, u.status,
           u.ts, u.status_ts,
           EXTRACT(EPOCH FROM (u.status_ts - u.ts))::bigint,
           count(*) OVER ()
      FROM public.unntak u
     WHERE u.tenant = p_tenant AND u.status = ANY(p_terminale)
       AND u.status_ts >= p_fra AND u.status_ts < p_til
     ORDER BY u.status_ts DESC
     LIMIT greatest(least(coalesce(p_grense, 50), 200), 1);
END $$;

-- Unntak, TILSTANDSAKSEN: «åpne nå» — utenfor enhver vindusvelger.
CREATE OR REPLACE FUNCTION m16_unntak_apne(
    p_tenant TEXT, p_terminale TEXT[])
RETURNS BIGINT
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm16_unntak_apne');
    SELECT count(*) INTO v FROM public.unntak u
     WHERE u.tenant = p_tenant AND NOT (u.status = ANY(p_terminale));
    RETURN v;
END $$;

-- Planer: tick per utfall, anker VINDU_START — perioden kontrollen
-- gjaldt, aldri `registrert` (SP-6: riktig tidsanker).
CREATE OR REPLACE FUNCTION m16_tick(
    p_tenant TEXT, p_fra TIMESTAMPTZ, p_til TIMESTAMPTZ)
RETURNS TABLE(nokkel TEXT, antall BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm16_tick');
    RETURN QUERY
    SELECT CASE WHEN GROUPING(t.utfall) = 1 THEN '__total__'
                ELSE coalesce(t.utfall, 'ukjent') END,
           count(*)
      FROM public.bestillingsplan_tick t
     WHERE t.tenant = p_tenant
       AND t.vindu_start >= p_fra AND t.vindu_start < p_til
     GROUP BY GROUPING SETS ((t.utfall), ());
END $$;

-- Runtime får KUN EXECUTE (flaten leser aldri tabeller — SP-7).
REVOKE ALL ON FUNCTION m16_beslutninger(TEXT, TIMESTAMPTZ, TIMESTAMPTZ) FROM PUBLIC;
REVOKE ALL ON FUNCTION m16_frekvensreservasjoner(TEXT, TIMESTAMPTZ, TIMESTAMPTZ) FROM PUBLIC;
REVOKE ALL ON FUNCTION m16_aktiveringer(TEXT, TIMESTAMPTZ, TIMESTAMPTZ) FROM PUBLIC;
REVOKE ALL ON FUNCTION m16_oppdrag(TEXT, TIMESTAMPTZ, TIMESTAMPTZ) FROM PUBLIC;
REVOKE ALL ON FUNCTION m16_unntak_aktivitet(TEXT, TIMESTAMPTZ, TIMESTAMPTZ) FROM PUBLIC;
REVOKE ALL ON FUNCTION m16_unntak_lukkede(TEXT, TIMESTAMPTZ, TIMESTAMPTZ, TEXT[], INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION m16_unntak_apne(TEXT, TEXT[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION m16_tick(TEXT, TIMESTAMPTZ, TIMESTAMPTZ) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION m16_beslutninger(TEXT, TIMESTAMPTZ, TIMESTAMPTZ) TO disponit;
GRANT EXECUTE ON FUNCTION m16_frekvensreservasjoner(TEXT, TIMESTAMPTZ, TIMESTAMPTZ) TO disponit;
GRANT EXECUTE ON FUNCTION m16_aktiveringer(TEXT, TIMESTAMPTZ, TIMESTAMPTZ) TO disponit;
GRANT EXECUTE ON FUNCTION m16_oppdrag(TEXT, TIMESTAMPTZ, TIMESTAMPTZ) TO disponit;
GRANT EXECUTE ON FUNCTION m16_unntak_aktivitet(TEXT, TIMESTAMPTZ, TIMESTAMPTZ) TO disponit;
GRANT EXECUTE ON FUNCTION m16_unntak_lukkede(TEXT, TIMESTAMPTZ, TIMESTAMPTZ, TEXT[], INT) TO disponit;
GRANT EXECUTE ON FUNCTION m16_unntak_apne(TEXT, TEXT[]) TO disponit;
GRANT EXECUTE ON FUNCTION m16_tick(TEXT, TIMESTAMPTZ, TIMESTAMPTZ) TO disponit;

RESET ROLE;
