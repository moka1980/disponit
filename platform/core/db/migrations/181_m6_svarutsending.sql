-- 181 — M-6: bakgrunnsprosessen sender svaret (svar fra postboksen,
-- PR 4). Siste ledd i eiervedtaket 10/9.
--
-- Nøkkelen til postboksen ligger her, ikke i web-API-et (088). Denne
-- migrasjonen gir den prosessen tre ting: å finne køen, å hente det den
-- trenger for å sende, og å skrive tilbake hva som skjedde.
--
-- KØA ER KRYSS-TENANT (102-formen, som `m6_hentekandidater`): døra
-- nekter tenantkontekst, og innhenteren setter radens tenant selv før
-- den rører noe.
SET LOCAL ROLE disponit_m37_claimer;

CREATE FUNCTION m6_sendekandidater(p_grense INT)
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
     WHERE u.status = 'sendes' AND u.slettet_ts IS NULL
       AND m.slettet_ts IS NULL
     ORDER BY u.avgjort_ts
     LIMIT greatest(least(coalesce(p_grense, 20), 200), 1);
END $$;
REVOKE ALL ON FUNCTION m6_sendekandidater(INT) FROM PUBLIC;

-- Det sendingen trenger, og ikke ett felt mer: tråden å svare i, og
-- teksten. Adressen er IKKE med — Graphs `reply` svarer avsenderen selv,
-- så plattformen trenger den aldri for å sende.
CREATE FUNCTION m6_for_utsending(p_tenant TEXT, p_utkast_id UUID)
RETURNS TABLE(status TEXT, leverandor_melding_id TEXT,
              tekst_kryptert BYTEA, nonce BYTEA, key_id TEXT,
              kilde_id UUID)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm6_for_utsending');
    RETURN QUERY
    SELECT u.status, m.leverandor_melding_id, u.tekst_kryptert, u.nonce,
           u.key_id, m.kilde_id
      FROM public.epost_utkast u
      JOIN public.epost_melding m
        ON m.tenant = u.tenant AND m.melding_id = u.melding_id
     WHERE u.tenant = p_tenant AND u.utkast_id = p_utkast_id
       AND u.slettet_ts IS NULL;
END $$;
REVOKE ALL ON FUNCTION m6_for_utsending(TEXT, UUID) FROM PUBLIC;

-- Svaret gikk ut. Idempotent: et gjenspill er et stille ja, for en
-- e-post som ER sendt skal aldri sendes to ganger fordi kvitteringen
-- kom bort.
CREATE FUNCTION m6_svar_sendt(p_tenant TEXT, p_utkast_id UUID,
                              p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_melding UUID; v_naa TIMESTAMPTZ := pg_catalog.now();
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm6_svar_sendt');
    SELECT u.status, u.melding_id INTO v_status, v_melding
      FROM public.epost_utkast u
     WHERE u.tenant = p_tenant AND u.utkast_id = p_utkast_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm6_svar_sendt: utkastet finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_status = 'sendt' THEN
        RETURN false;
    END IF;
    IF v_status <> 'sendes' THEN
        RAISE EXCEPTION 'm6_svar_sendt: utkastet er % — bare et svar i'
            ' kø kan bli sendt', v_status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    UPDATE public.epost_utkast
       SET status = 'sendt', sendt_ts = v_naa, feilgrunn = NULL
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id;
    PERFORM public.m6_evidens(p_tenant, v_melding, 'epost.svar_sendt',
                              p_aktor,
                              jsonb_build_object('utkast_id', p_utkast_id,
                                                 'sendt_ts', v_naa));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m6_svar_sendt(TEXT, UUID, TEXT) FROM PUBLIC;

-- Sendingen feilet. GRUNNEN ER EN KODE, aldri leverandørens tekst: den
-- kan bære adresser og innhold, og den skal ikke havne i en kolonne
-- flaten viser. Utkastet kan sendes på nytt.
CREATE FUNCTION m6_svar_feilet(p_tenant TEXT, p_utkast_id UUID,
                               p_grunn TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_melding UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm6_svar_feilet');
    IF p_grunn !~ '^[a-z][a-z0-9_]{0,63}$' THEN
        RAISE EXCEPTION 'm6_svar_feilet: grunnen er en KODE, ikke en'
            ' leverandørtekst' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT u.status, u.melding_id INTO v_status, v_melding
      FROM public.epost_utkast u
     WHERE u.tenant = p_tenant AND u.utkast_id = p_utkast_id FOR UPDATE;
    IF NOT FOUND OR v_status <> 'sendes' THEN
        RETURN false;
    END IF;
    UPDATE public.epost_utkast
       SET status = 'feilet', feilgrunn = p_grunn
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id;
    PERFORM public.m6_evidens(p_tenant, v_melding, 'epost.svar_feilet',
                              p_aktor,
                              jsonb_build_object('utkast_id', p_utkast_id,
                                                 'grunn', p_grunn));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m6_svar_feilet(TEXT, UUID, TEXT, TEXT) FROM PUBLIC;

-- EXECUTE- og tabellgrantene til planarbeideren bor i `migrer.py`
-- (PLAN_RETTIGHETER): rollens rettigheter nullstilles og settes der
-- ved hver kjøring, så en migrasjon som gir dem blir stille borte.

RESET ROLE;
