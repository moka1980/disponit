-- 232 — ET FIRMA SOM STÅR STILLE, HENTER OG SENDER INGEN E-POST.
--
-- EIERS VEDTAK 27/11 («Pause agentene, les + eksport»): når prøveperioden
-- er over (`utlopt`, 190) eller plattformeieren har stengt firmaet
-- (`stengt`, 199), skal plattformen slutte å gjøre ting av seg selv for
-- det. Bestillingsveien nekter alt nytt (`firmastatus.er_pauset_i`);
-- denne migrasjonen gjør det samme for e-posten: innhentingen og
-- utsendingen fra postboksen.
--
-- HVORFOR I DØRENE OG IKKE I INNHENTEREN: dørene er stedet kandidatene
-- VELGES, og de fire er de eneste veiene inn i en e-postrunde — Microsoft
-- og IMAP, inn og ut. Et filter i Python måtte stått fire steder, og
-- `plan/epost.py` er M-6s sertifiserte bevisrot. Et svar som ligger i kø,
-- blir liggende til firmaet er aktivt igjen; ingenting slettes.
--
-- KROPPENE ER 229s, ORD FOR ORD, med ETT ledd til hver. Claimeren ser alle
-- rader i `firma` gjennom `firma_sveiper` (190) — den samme policyen
-- prøveutløpssveipen bruker. Et firma UTEN rad (eierens egne tenanter, eldre
-- enn registeret) står ikke stille.

SET LOCAL ROLE disponit_m37_claimer;

CREATE OR REPLACE FUNCTION m6_hentekandidater(p_grense INT)
RETURNS TABLE(tenant TEXT, kilde_id UUID, postboks TEXT,
              delta_token TEXT, sist_hentet_ts TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm6_hentekandidater: kryss-tenant-døra nekter'
            ' tenantkontekst' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN QUERY
    SELECT k.tenant, k.kilde_id, k.postboks, k.delta_token, k.sist_hentet_ts
      FROM public.epost_kilde k
     WHERE k.status = 'aktiv' AND k.leverandor = 'm365'
       -- 232: et firma som står stille, hentes og sendes det ikke for.
       AND NOT EXISTS (SELECT 1 FROM public.firma f
                        WHERE f.tenant = k.tenant
                          AND f.status IN ('utlopt', 'stengt'))
     ORDER BY k.sist_hentet_ts NULLS FIRST, k.opprettet
     LIMIT greatest(least(coalesce(p_grense, 20), 200), 1);
END $$;

CREATE OR REPLACE FUNCTION m6_sendekandidater(p_grense INT)
RETURNS TABLE(tenant TEXT, utkast_id UUID, melding_id UUID, kilde_id UUID)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm6_sendekandidater: kryss-tenant-døra nekter'
            ' tenantkontekst' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN QUERY
    SELECT u.tenant, u.utkast_id, u.melding_id, m.kilde_id
      FROM public.epost_utkast u
      JOIN public.epost_melding m
        ON m.tenant = u.tenant AND m.melding_id = u.melding_id
      JOIN public.epost_kilde k
        ON k.tenant = m.tenant AND k.kilde_id = m.kilde_id
     WHERE u.status = 'sendes' AND u.slettet_ts IS NULL
       AND m.slettet_ts IS NULL AND k.leverandor = 'm365'
       -- 232: et firma som står stille, hentes og sendes det ikke for.
       AND NOT EXISTS (SELECT 1 FROM public.firma f
                        WHERE f.tenant = k.tenant
                          AND f.status IN ('utlopt', 'stengt'))
     ORDER BY u.avgjort_ts
     LIMIT greatest(least(coalesce(p_grense, 20), 200), 1);
END $$;

CREATE OR REPLACE FUNCTION m6_imap_hentekandidater(p_grense INT)
RETURNS TABLE(tenant TEXT, kilde_id UUID, postboks TEXT,
              delta_token TEXT, sist_hentet_ts TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm6_imap_hentekandidater: kryss-tenant-døra nekter'
            ' tenantkontekst' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN QUERY
    SELECT k.tenant, k.kilde_id, k.postboks, k.delta_token, k.sist_hentet_ts
      FROM public.epost_kilde k
     WHERE k.status = 'aktiv' AND k.leverandor = 'imap'
       -- 232: et firma som står stille, hentes og sendes det ikke for.
       AND NOT EXISTS (SELECT 1 FROM public.firma f
                        WHERE f.tenant = k.tenant
                          AND f.status IN ('utlopt', 'stengt'))
     ORDER BY k.sist_hentet_ts NULLS FIRST, k.opprettet
     LIMIT greatest(least(coalesce(p_grense, 20), 200), 1);
END $$;

CREATE OR REPLACE FUNCTION m6_imap_sendekandidater(p_grense INT)
RETURNS TABLE(tenant TEXT, utkast_id UUID, melding_id UUID, kilde_id UUID)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm6_imap_sendekandidater: kryss-tenant-døra nekter'
            ' tenantkontekst' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN QUERY
    SELECT u.tenant, u.utkast_id, u.melding_id, m.kilde_id
      FROM public.epost_utkast u
      JOIN public.epost_melding m
        ON m.tenant = u.tenant AND m.melding_id = u.melding_id
      JOIN public.epost_kilde k
        ON k.tenant = m.tenant AND k.kilde_id = m.kilde_id
     WHERE u.status = 'sendes' AND u.slettet_ts IS NULL
       AND m.slettet_ts IS NULL AND k.leverandor = 'imap'
       -- 232: et firma som står stille, hentes og sendes det ikke for.
       AND NOT EXISTS (SELECT 1 FROM public.firma f
                        WHERE f.tenant = k.tenant
                          AND f.status IN ('utlopt', 'stengt'))
     ORDER BY u.avgjort_ts
     LIMIT greatest(least(coalesce(p_grense, 20), 200), 1);
END $$;
RESET ROLE;
