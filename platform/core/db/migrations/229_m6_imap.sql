-- 229 — M-6: IMAP SOM KILDE (ADR-011) — vanlig Gmail med app-passord.
--
-- Eiers vedtak 21.11.2026: mange små bedrifter bruker en vanlig
-- Gmail-konto som flere deler. Googles egen innlogging (OAuth) krever
-- verifisering og en årlig sikkerhetsgjennomgang før fremmede kunder kan
-- koble til; IMAP med app-passord gjør det ikke. Kilden får derfor en
-- andre leverandør: `imap`.
--
-- M365-VEIEN RØRES IKKE. Innhenteren og senderen for Microsoft
-- (`plan/epost.py`) henter kandidatene sine gjennom de to dørene under —
-- de får ETT ledd til, `leverandor = 'm365'`, så en IMAP-kilde aldri havner
-- i en runde som ville forsøkt et Microsoft-token på den (og satt den
-- `feilet`). IMAP-veien har sine egne dører, med samme form.

ALTER TABLE epost_kilde DROP CONSTRAINT kilde_leverandor_lukket;
ALTER TABLE epost_kilde ADD CONSTRAINT kilde_leverandor_lukket
    CHECK (leverandor IN ('m365', 'imap'));

GRANT SELECT (leverandor) ON epost_kilde TO disponit_m37_claimer;
-- Svaret over SMTP trenger mottaker og emne fra meldingen det svarer på
-- (hos Microsoft holder leverandørens melding-id). De ligger i meldingens
-- krypterte payload; døra under gir ciphertexten videre, aldri klartekst.
GRANT SELECT (tenant, melding_id, kilde_id, leverandor_melding_id,
              kropp_kryptert, nonce, key_id, slettet_ts)
    ON epost_melding TO disponit_m37_claimer;

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
     ORDER BY u.avgjort_ts
     LIMIT greatest(least(coalesce(p_grense, 20), 200), 1);
END $$;

CREATE FUNCTION m6_imap_hentekandidater(p_grense INT)
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
     ORDER BY k.sist_hentet_ts NULLS FIRST, k.opprettet
     LIMIT greatest(least(coalesce(p_grense, 20), 200), 1);
END $$;
REVOKE ALL ON FUNCTION m6_imap_hentekandidater(INT) FROM PUBLIC;

CREATE FUNCTION m6_imap_sendekandidater(p_grense INT)
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
     ORDER BY u.avgjort_ts
     LIMIT greatest(least(coalesce(p_grense, 20), 200), 1);
END $$;
REVOKE ALL ON FUNCTION m6_imap_sendekandidater(INT) FROM PUBLIC;

-- KAN KILDEN SVARE? (180) Døra spurte bare etter Microsofts `Mail.Send`.
-- En IMAP-kilde svarer over SMTP med samme app-passord; scope-ordet
-- `smtp` sier det. Resten av døra er uendret — sendekommandoen
-- (`m6_send_svaret`) leser fortsatt svaret HERFRA og bare herfra.
CREATE OR REPLACE FUNCTION m6_for_svar(p_tenant TEXT, p_utkast_id UUID)
RETURNS TABLE(utkast_status TEXT, melding_lever BOOLEAN,
              kilde_aktiv BOOLEAN, kilde_kan_svare BOOLEAN,
              har_leverandor_id BOOLEAN, har_avsender BOOLEAN,
              melding_id UUID, kilde_id UUID)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm6_for_svar');
    RETURN QUERY
    SELECT u.status,
           m.slettet_ts IS NULL,
           k.status = 'aktiv',
           (position(lower('https://graph.microsoft.com/Mail.Send')
                     in lower(coalesce(k.scope, ''))) > 0
            OR (k.leverandor = 'imap'
                AND 'smtp' = ANY (string_to_array(
                        lower(coalesce(k.scope, '')), ' ')))),
           btrim(coalesce(m.leverandor_melding_id, '')) <> '',
           btrim(coalesce(m.avsender_hash, '')) <> '',
           m.melding_id, k.kilde_id
      FROM public.epost_utkast u
      JOIN public.epost_melding m
        ON m.tenant = u.tenant AND m.melding_id = u.melding_id
      JOIN public.epost_kilde k
        ON k.tenant = m.tenant AND k.kilde_id = m.kilde_id
     WHERE u.tenant = p_tenant AND u.utkast_id = p_utkast_id
       AND u.slettet_ts IS NULL;
END $$;

-- Det senderen trenger: utkastets tekst OG meldingen det svarer på —
-- begge som ciphertext; dekrypteringen skjer hos planarbeideren.
CREATE FUNCTION m6_imap_for_utsending(p_tenant TEXT, p_utkast_id UUID)
RETURNS TABLE(status TEXT, leverandor_melding_id TEXT,
              tekst_kryptert BYTEA, nonce BYTEA, key_id TEXT,
              kilde_id UUID, melding_kryptert BYTEA, melding_nonce BYTEA,
              melding_key_id TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm6_imap_for_utsending');
    RETURN QUERY
    SELECT u.status, m.leverandor_melding_id, u.tekst_kryptert, u.nonce,
           u.key_id, m.kilde_id, m.kropp_kryptert, m.nonce, m.key_id
      FROM public.epost_utkast u
      JOIN public.epost_melding m
        ON m.tenant = u.tenant AND m.melding_id = u.melding_id
     WHERE u.tenant = p_tenant AND u.utkast_id = p_utkast_id
       AND u.slettet_ts IS NULL;
END $$;
REVOKE ALL ON FUNCTION m6_imap_for_utsending(TEXT, UUID) FROM PUBLIC;

RESET ROLE;
