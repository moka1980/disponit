-- 226 — M-17: MÅLTALL FOR KØEN (svartid og volum).
--
-- Eier 21.11.2026: «hva kan bedriftene spare?» Det ærlige svaret krever
-- tall fra kundens egen kø, ikke et regneeksempel. Registeret har alt
-- som trengs: når henvendelsen kom (`mottatt`), og når den ble lukket som
-- besvart (`lukket_ts`, `lukket_utfall`) — og «besvart» kan bare settes
-- når et utkast er brukt eller sendt (102/164).
--
-- SVARTIDEN ER MEDIANEN, ikke snittet: én henvendelse som lå i tre uker
-- skal ikke gjøre den typiske svartiden til noe ingen kjenner igjen.
-- `NULL` når ingenting er besvart i vinduet — null minutter ville vært
-- en påstand.
--
-- TALLENE GJELDER ALT I VINDUET, ikke det køen viser (101s dom: en
-- avkortet liste er ikke en total).

SET LOCAL ROLE disponit_kundeservice_eier;

CREATE FUNCTION m17_maaltall(p_tenant TEXT, p_dogn INT)
RETURNS TABLE(dogn INT, mottatt INT, besvart INT, ikke_aktuelle INT,
              median_svartid_min INT, apne INT, eldste_apne_dogn INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_fra TIMESTAMPTZ;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_maaltall');
    dogn := greatest(least(coalesce(p_dogn, 30), 365), 1);
    v_fra := now() - make_interval(days => dogn);
    SELECT count(*) FILTER (WHERE h.mottatt >= v_fra)::int,
           count(*) FILTER (WHERE h.lukket_ts >= v_fra
                              AND h.lukket_utfall = 'besvart')::int,
           count(*) FILTER (WHERE h.lukket_ts >= v_fra
                              AND h.lukket_utfall = 'ikke_aktuell')::int,
           (percentile_cont(0.5) WITHIN GROUP (
                ORDER BY extract(epoch FROM h.lukket_ts - h.mottatt) / 60.0)
              FILTER (WHERE h.lukket_ts >= v_fra
                        AND h.lukket_utfall = 'besvart'
                        AND h.lukket_ts >= h.mottatt))::int,
           count(*) FILTER (WHERE h.lukket_ts IS NULL)::int,
           (max(current_date - h.mottatt::date)
              FILTER (WHERE h.lukket_ts IS NULL))::int
      INTO mottatt, besvart, ikke_aktuelle, median_svartid_min, apne,
           eldste_apne_dogn
      FROM public.henvendelse h WHERE h.tenant = p_tenant;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m17_maaltall(TEXT, INT) FROM PUBLIC;

RESET ROLE;
