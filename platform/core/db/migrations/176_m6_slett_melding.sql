-- 176 — M-6: et menneske kan slette en hentet melding NÅ (eiers ønske
-- 10/9: «eposten som hentes til disponit, er det mulig å slette den
-- også»).
--
-- 088 ga meldingene én slettevei: retensjonen. Reaperen tømmer alle
-- payload-lagrene når fristen (30–365 døgn) er ute, og lar rad-id,
-- tidsstempler og hasher stå som minimal revisjonsevidens. DELETE
-- finnes ikke, og skal ikke finnes: en melding som forsvinner sporløst
-- kan ingen etterpå si at fantes.
--
-- Denne døra er NØYAKTIG samme overgang, på menneskets kommando:
-- alle fire lagrene i SAMME transaksjon (den utsatte porten
-- `m6_lagrene_reapes_samlet` gjerder også dette kallet), `slettet_ts`
-- satt, teksten borte. Forskjellen fra reaperen er tre ting: den er
-- TENANTBUNDET (krever kontekst, 038-formen), den krever et menneske
-- (aktøren bokføres), og den skriver evidens — en sletting før fristen
-- er en beslutning, ikke husholdning.
--
-- Idempotent: en melding som alt er slettet gir `false`, ikke en feil.
-- Eierskapet er claimerens, som reaperens: det er den rollen 088 ga
-- UPDATE på payload-lagrene, og RLS-policyen `m6_reaper` er dens.

SET LOCAL ROLE disponit_m37_claimer;

CREATE FUNCTION m6_evidens(p_tenant TEXT, p_melding_id UUID,
                           p_handling TEXT, p_aktor TEXT, p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm6_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm06_epost', 'handling', p_handling,
        'subjekt_id', p_melding_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm06_epost',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:epost', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m6_evidens(TEXT, UUID, TEXT, TEXT, JSONB) FROM PUBLIC;

CREATE FUNCTION m6_slett_melding(p_tenant TEXT, p_melding_id UUID,
                                 p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_naa TIMESTAMPTZ := pg_catalog.now(); v_alt TIMESTAMPTZ;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm6_slett_melding');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm6_slett_melding: en sletting har en aktør'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT m.slettet_ts INTO v_alt FROM public.epost_melding m
     WHERE m.tenant = p_tenant AND m.melding_id = p_melding_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm6_slett_melding: meldingen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_alt IS NOT NULL THEN
        RETURN false;                              -- alt slettet: stille ja
    END IF;
    -- ALLE lagrene, samme transaksjon — reaperens rekkefølge ordrett.
    UPDATE public.epost_vedlegg v
       SET navn_kryptert = NULL, nonce = NULL, key_id = NULL,
           slettet_ts = v_naa
     WHERE v.tenant = p_tenant AND v.melding_id = p_melding_id
       AND v.slettet_ts IS NULL;
    UPDATE public.epost_klassifisering k
       SET sammendrag_kryptert = NULL, nonce = NULL, key_id = NULL,
           slettet_ts = v_naa
     WHERE k.tenant = p_tenant AND k.melding_id = p_melding_id
       AND k.slettet_ts IS NULL;
    UPDATE public.epost_utkast u
       SET tekst_kryptert = NULL, nonce = NULL, key_id = NULL,
           slettet_ts = v_naa
     WHERE u.tenant = p_tenant AND u.melding_id = p_melding_id
       AND u.slettet_ts IS NULL;
    UPDATE public.epost_melding m
       SET kropp_kryptert = NULL, nonce = NULL, key_id = NULL,
           slettet_ts = v_naa
     WHERE m.tenant = p_tenant AND m.melding_id = p_melding_id;
    -- Evidensen bærer ALDRI adressen eller emnet: de er nettopp det
    -- slettingen fjernet. Bare at det skjedde, av hvem, og på hvilken rad.
    PERFORM public.m6_evidens(p_tenant, p_melding_id, 'epost.melding_slettet',
                              p_aktor, jsonb_build_object('slettet_ts', v_naa,
                                                          'foer_frist', true));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m6_slett_melding(TEXT, UUID, TEXT) FROM PUBLIC;

RESET ROLE;

-- Claimeren eier døra og skriver evidensen som seg selv: den trenger
-- INSERT på revisjonsloggen (migrator eier den).
--
-- BAR GRANT, IKKE VAKTET: `disponit_m37_claimer` er et KRAV, ikke en
-- valgfri rolle — 005/007 stanser med en lesbar beskjed hvis den mangler
-- (`IF NOT EXISTS … RAISE`), og 088 granter til den på samme bare form.
-- En `IF EXISTS (… pg_roles …)`-vakt her ville sagt det motsatte, og
-- `test_modul_onboarding` utleder rolleklassen av nettopp den formen:
-- vakten gjorde claimeren «valgfri» i korpuset, og felte 035s bare
-- grant to migrasjoner unna.
GRANT INSERT ON revisjonslogg TO disponit_m37_claimer;
