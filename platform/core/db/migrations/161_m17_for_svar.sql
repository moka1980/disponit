-- =====================================================================
-- 161 — M-17: DØRA BESTILLINGSVEIEN SPØR FØR ET SVAR SENDES
-- =====================================================================
--
-- ARC B kundeservice, PR 2: `kundeservice.svar.send` blir en
-- bestillingstype — ETT godkjent utkast til ÉN henvendelse. Bestillings-
-- veien må vite FØR beslutningen brenner kvote: finnes henvendelsen og er
-- den åpen, står den i unntakskøen (da eier M-37 den), har den en
-- adresse og en kanal plattformen kan svare i, hører utkastet til
-- henvendelsen — og utkastets status og tekst. Statusen (`godkjent`) og
-- teksten er POLICYENS vilkår: registeret attesterer `svar_godkjent`
-- (v_kundeservice), og DLP-sjekken og «ingen økonomiske løfter» måles på
-- teksten i API-et (v_dlp) — et utkast som ikke er godkjent, eller som
-- bærer et fødselsnummer eller et løfte om rabatt, blir en SAK, aldri en
-- stille sending.
--
-- Teksten går kryptert ut av døra og dekrypteres i API-et (154/156-formen).
-- Planarbeideren får EXECUTE fordi bestillingsveien kjøres også av
-- utløseren (PR 3).
-- ---------------------------------------------------------------------
SET LOCAL ROLE disponit_kundeservice_eier;

CREATE FUNCTION m17_for_svar(p_tenant TEXT, p_henvendelse_id UUID,
                             p_utkast_id UUID)
RETURNS TABLE(lukket BOOLEAN, i_unntakskoe BOOLEAN, kanal TEXT,
              har_avsender BOOLEAN, avsender_maske TEXT,
              utkast_henvendelse_id UUID, utkast_status TEXT,
              utkast_kryptert BYTEA, utkast_nonce BYTEA,
              utkast_key_id TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_for_svar');
    RETURN QUERY
    SELECT (h.lukket_ts IS NOT NULL), (h.unntak_id IS NOT NULL), h.kanal,
           (h.avsender_kryptert IS NOT NULL), h.avsender_maske,
           u.henvendelse_id, u.status, u.tekst_kryptert, u.nonce, u.key_id
      FROM public.henvendelse h
      LEFT JOIN public.svarutkast u
        ON u.tenant = h.tenant AND u.utkast_id = p_utkast_id
     WHERE h.tenant = p_tenant AND h.henvendelse_id = p_henvendelse_id;
END $$;
REVOKE ALL ON FUNCTION m17_for_svar(TEXT, UUID, UUID) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_for_svar(TEXT, UUID, UUID)'
            ' TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
               WHERE rolname = 'disponit_plan_arbeider') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_for_svar(TEXT, UUID, UUID)'
            ' TO disponit_plan_arbeider';
    END IF;
END $$;
RESET ROLE;
