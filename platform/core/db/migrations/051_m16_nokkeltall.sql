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
--
-- TOTALRADEN SIER SELV AT DEN ER TOTALEN (`er_total`), den skriver det
-- ikke inn i nøkkelen. Merket var en reservert nøkkelverdi, `__total__`,
-- i SAMME kolonne som kategoriene — men `unntak.kategori` er en fri
-- TEXT-kolonne uten skranke, så nøyaktig den strengen er også en lovlig
-- kategori. Da var en ekte gruppe ikke til å skille fra aggregatet:
-- API-laget leste begge som total, suminvarianten sprakk, og
-- `/v1/nokkeltall` svarte `intern_feil` på data som var helt i orden —
-- eller, i et sett med bare den kategorien, tegnet flaten et kort uten
-- en eneste rad. Skillet er `GROUPING()`, og det er en egenskap ved
-- RADEN, ikke ved verdien; nå bæres det som det, i sin egen kolonne.
-- Da finnes det ingen kategoristreng som kan kollidere med noe.

-- Kildene claimeren ikke alt leser (RLS-policyene er PUBLIC
-- tenant-isolasjon, så SELECT-granten er hele mangelen):
GRANT SELECT ON frekvens_hendelser, policyaktivering
    TO disponit_m37_claimer;

SET LOCAL ROLE disponit_m37_claimer;

-- DEFINERNE BYGGES FRA GRUNNEN. En signatur som endrer seg — argument
-- eller returtype — kan ikke `CREATE OR REPLACE`-es på plass, og
-- håndskrevne DROP-linjer er den andre utgaven av signaturen som
-- rettighetsblokken nederst nettopp ble kvitt: de sto igjen og pekte på
-- gamle overlaster forrige runde hadde fjernet. Katalogen vet hvilke
-- `m16_*`-definere som faktisk finnes fra en tidligere kjøring av DENNE
-- fila — 051 er M-16s eneste migrasjon, så prefikset er hele settet — og
-- de fjernes uten at noen signatur skrives ned et andre sted.
DO $rydd$
DECLARE signatur TEXT;
BEGIN
    FOR signatur IN
        SELECT p.oid::regprocedure::text
          FROM pg_catalog.pg_proc p
          JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname LIKE 'm16\_%'
    LOOP
        EXECUTE 'DROP FUNCTION ' || signatur;
    END LOOP;
END $rydd$;

-- Beslutninger: partisjon per beslutning + total, samme skann.
CREATE OR REPLACE FUNCTION m16_beslutninger(
    p_tenant TEXT, p_fra TIMESTAMPTZ, p_til TIMESTAMPTZ)
RETURNS TABLE(er_total BOOLEAN, nokkel TEXT, antall BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm16_beslutninger');
    RETURN QUERY
    -- Totalraden har ingen nøkkel: den er ikke en gruppe. Å la den bære
    -- den grupperte kolonnens NULL som «ukjent» ville gitt aggregatet et
    -- kategorinavn det ikke har.
    SELECT GROUPING(r.beslutning) = 1,
           CASE WHEN GROUPING(r.beslutning) = 1 THEN NULL
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
RETURNS TABLE(partisjon TEXT, er_total BOOLEAN, nokkel TEXT, antall BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm16_aktiveringer');
    RETURN QUERY
    SELECT 'kilde'::text,
           GROUPING(a.aktiveringskilde) = 1,
           CASE WHEN GROUPING(a.aktiveringskilde) = 1 THEN NULL
                ELSE coalesce(a.aktiveringskilde, 'ukjent') END,
           count(*)
      FROM public.policyaktivering a
     WHERE a.tenant = p_tenant
       AND a.aktivert_ts >= p_fra AND a.aktivert_ts < p_til
     GROUP BY GROUPING SETS ((a.aktiveringskilde), ());
    RETURN QUERY
    SELECT 'kvorumskrav'::text,
           GROUPING(a.pakrevd_antall) = 1,
           CASE WHEN GROUPING(a.pakrevd_antall) = 1 THEN NULL
                ELSE coalesce(a.pakrevd_antall::text, 'ukjent') END,
           count(*)
      FROM public.policyaktivering a
     WHERE a.tenant = p_tenant
       AND a.aktivert_ts >= p_fra AND a.aktivert_ts < p_til
     GROUP BY GROUPING SETS ((a.pakrevd_antall), ());
    -- Rå tellinger én/to attestanter — aldri en andel (grensen §1).
    RETURN QUERY
    SELECT 'attestanter'::text,
           GROUPING((a.attestant_b IS NOT NULL)) = 1,
           CASE WHEN GROUPING((a.attestant_b IS NOT NULL)) = 1 THEN NULL
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
RETURNS TABLE(er_total BOOLEAN, nokkel TEXT, antall BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm16_oppdrag');
    RETURN QUERY
    SELECT GROUPING(o.status) = 1,
           CASE WHEN GROUPING(o.status) = 1 THEN NULL
                ELSE coalesce(o.status, 'ukjent') END,
           count(*)
      FROM public.oppdrag o
     WHERE o.tenant = p_tenant
       AND o.status_ts >= p_fra AND o.status_ts < p_til
     GROUP BY GROUPING SETS ((o.status), ());
END $$;

-- SAKSTYPEVERNET på de tre unntaksdefinerne (`p_sakstyper`):
--
-- `sikkerhet` og `drift` er EGNE køer med eget scope (v3-delta pkt. 5),
-- og det vernede er ikke bare innholdet — det er at saken FINNES.
-- Unntakslisten holder dette allerede (`app.py`: `synlige_sakstyper`),
-- men de tre definerne under leser de samme radene UTENOM den veien, og
-- uten filteret ville en ren `decisions:read` fått kategoritellinger,
-- «åpne nå», og IDer/tidspunkter/sakstyper for lukkede sikkerhetssaker.
--
-- Settet er KALLERENS, akkurat som `p_terminale`: SQL avleder ikke
-- scope-regelen på egen hånd (oversikt-lærdommen — to utgaver av samme
-- kontroll er to kontroller). Fila her filtrerer bare på det den får.
--
-- At utelatte rader ikke nevnes i svaret er IKKE samme sak som den
-- stille trunkeringen `antall_totalt` løser: der skjulte grensen fakta
-- kalleren har rett til å se, her er selve eksistensen det scopet
-- verner. Å opplyse «+3 skjulte» ville lekket nøyaktig opplysningen.

-- Unntak, AKTIVITETSAKSEN: opprettet i vinduet, per kategori (anker ts).
-- Kun metadata — payloaden er kryptert og røres aldri herfra.
CREATE OR REPLACE FUNCTION m16_unntak_aktivitet(
    p_tenant TEXT, p_fra TIMESTAMPTZ, p_til TIMESTAMPTZ, p_sakstyper TEXT[])
RETURNS TABLE(er_total BOOLEAN, nokkel TEXT, antall BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm16_unntak_aktivitet');
    RETURN QUERY
    -- `unntak.kategori` er en fri TEXT-kolonne: HVER streng er en lovlig
    -- kategori, også den som før merket aggregatet. Derfor kan merket
    -- ikke bo i denne kolonnen — det er dette kortet kollisjonen faktisk
    -- kunne treffe.
    SELECT GROUPING(u.kategori) = 1,
           CASE WHEN GROUPING(u.kategori) = 1 THEN NULL
                ELSE coalesce(u.kategori, 'ukjent') END,
           count(*)
      FROM public.unntak u
     WHERE u.tenant = p_tenant AND u.ts >= p_fra AND u.ts < p_til
       AND u.sakstype = ANY(p_sakstyper)
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
CREATE FUNCTION m16_unntak_lukkede(
    p_tenant TEXT, p_fra TIMESTAMPTZ, p_til TIMESTAMPTZ,
    p_terminale TEXT[], p_sakstyper TEXT[], p_grense INT)
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
       AND u.sakstype = ANY(p_sakstyper)
       AND u.status_ts >= p_fra AND u.status_ts < p_til
     ORDER BY u.status_ts DESC
     LIMIT greatest(least(coalesce(p_grense, 50), 200), 1);
END $$;

-- Unntak, TILSTANDSAKSEN: «åpne nå» — utenfor enhver vindusvelger.
CREATE OR REPLACE FUNCTION m16_unntak_apne(
    p_tenant TEXT, p_terminale TEXT[], p_sakstyper TEXT[])
RETURNS BIGINT
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm16_unntak_apne');
    SELECT count(*) INTO v FROM public.unntak u
     WHERE u.tenant = p_tenant AND NOT (u.status = ANY(p_terminale))
       AND u.sakstype = ANY(p_sakstyper);
    RETURN v;
END $$;

-- Planer: tick per utfall, anker VINDU_START — perioden kontrollen
-- gjaldt, aldri `registrert` (SP-6: riktig tidsanker).
CREATE OR REPLACE FUNCTION m16_tick(
    p_tenant TEXT, p_fra TIMESTAMPTZ, p_til TIMESTAMPTZ)
RETURNS TABLE(er_total BOOLEAN, nokkel TEXT, antall BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm16_tick');
    RETURN QUERY
    SELECT GROUPING(t.utfall) = 1,
           CASE WHEN GROUPING(t.utfall) = 1 THEN NULL
                ELSE coalesce(t.utfall, 'ukjent') END,
           count(*)
      FROM public.bestillingsplan_tick t
     WHERE t.tenant = p_tenant
       AND t.vindu_start >= p_fra AND t.vindu_start < p_til
     GROUP BY GROUPING SETS ((t.utfall), ());
END $$;

-- Runtime får KUN EXECUTE (flaten leser aldri tabeller — SP-7).
--
-- ARGUMENTLISTENE SKRIVES IKKE EN GANG TIL HER. En rettighetssetning
-- som gjentar signaturen er en ANDRE utgave av definisjonen, og de to
-- driver fra hverandre i stillhet: da sakstypevernet la `p_sakstyper`
-- på de tre unntaksdefinerne, pekte REVOKE fortsatt på de gamle
-- overlastene. PostgreSQL slår opp funksjonsrettigheter på EKSAKT
-- argumentliste, så den FØRSTE av dem hadde stoppet hele migrasjonen
-- og rullet den tilbake — samme lærdom som ellers i repoet: én
-- kontroll, ett sted. Det er samme grunn som at ryddeblokken øverst
-- spør katalogen i stedet for å liste signaturer den også måtte holdt
-- i takt.
--
-- Navnene er intensjonen (åtte definere, verken flere eller færre);
-- signaturene henter vi fra funksjonene CREATE-setningene over
-- FAKTISK laget. Avviket kan da bare gå én vei, og `antall <> 8`
-- fanger begge retninger høylytt: en definer som mangler, eller en
-- overlast som ikke skulle finnes og ville stått igjen med sine egne
-- rettigheter.
DO $rettigheter$
DECLARE
    signatur TEXT;
    antall   INT := 0;
BEGIN
    FOR signatur IN
        SELECT p.oid::regprocedure::text
          FROM pg_catalog.pg_proc p
          JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND p.proname = ANY (ARRAY[
                   'm16_beslutninger', 'm16_frekvensreservasjoner',
                   'm16_aktiveringer', 'm16_oppdrag',
                   'm16_unntak_aktivitet', 'm16_unntak_lukkede',
                   'm16_unntak_apne', 'm16_tick'])
         ORDER BY 1
    LOOP
        EXECUTE 'REVOKE ALL ON FUNCTION ' || signatur || ' FROM PUBLIC';
        EXECUTE 'GRANT EXECUTE ON FUNCTION ' || signatur || ' TO disponit';
        antall := antall + 1;
    END LOOP;
    IF antall <> 8 THEN
        RAISE EXCEPTION
            'M-16 (051): fant % m16-definere, forventet 8 — en definer '
            'mangler, eller en overlast som ikke skal finnes står igjen',
            antall;
    END IF;
END $rettigheter$;

RESET ROLE;
