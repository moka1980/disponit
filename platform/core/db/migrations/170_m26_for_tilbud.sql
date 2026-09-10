-- 170 — M-26 (ARC B tilbud, PR 2): bestillingsveiens målport for
-- `tilbud.generer`.
--
-- Døra leser tilbudets tilstand og de to faktaene registeret regner av
-- radene (169): `priser_fra_boka` og `klausuler_uendret` — regnet HER,
-- for nøyaktig dette tilbudet, ikke slått opp i lista (som er avkortet;
-- CodeRabbit på ARC B tilbud PR 2). Den sender
-- ingenting og attesterer ingenting: attestasjonene mintes i
-- bestillingsveien (API-ets tillit) av det døra MÅLTE — begge som
-- `v_prisbok` — og et nei er en USANN attestasjon, altså en sak i
-- unntakskøen, aldri en stille sending.
SET LOCAL ROLE disponit_prisbok_eier;

CREATE FUNCTION m26_for_tilbud(p_tenant TEXT, p_tilbud_id UUID)
RETURNS TABLE(status TEXT, kunde_maske TEXT, sum_ore BIGINT, valuta TEXT,
              tilbudsdato DATE, gyldig_til DATE, antall_linjer INT,
              priser_fra_boka BOOLEAN, klausuler_uendret BOOLEAN)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_for_tilbud');
    RETURN QUERY
    SELECT t.status, t.kunde_maske, t.sum_ore, t.valuta, t.tilbudsdato,
           t.gyldig_til,
           (SELECT count(*)::int FROM public.tilbudslinje l
             WHERE l.tenant = t.tenant AND l.tilbud_id = t.tilbud_id),
           NOT EXISTS (
               SELECT 1 FROM public.tilbudslinje l
                WHERE l.tenant = t.tenant AND l.tilbud_id = t.tilbud_id
                  AND coalesce(public.m26_innenfor_rabatt(
                          t.tenant, l.produkt_id, t.tilbudsdato,
                          l.enhetspris_ore), l.enhetspris_ore = l.listepris_ore)
                      IS NOT TRUE),
           NOT EXISTS (
               SELECT 1 FROM public.tilbudsklausul tk
                WHERE tk.tenant = t.tenant AND tk.tilbud_id = t.tilbud_id
                  AND tk.tekst_hash IS DISTINCT FROM (
                      SELECT k.tekst_hash FROM public.klausul k
                       WHERE k.tenant = tk.tenant AND k.kode = tk.kode
                       ORDER BY k.versjon DESC LIMIT 1))
      FROM public.tilbud t
     WHERE t.tenant = p_tenant AND t.tilbud_id = p_tilbud_id;
END $$;
REVOKE ALL ON FUNCTION m26_for_tilbud(TEXT, UUID) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_for_tilbud(TEXT, UUID)'
            ' TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
               WHERE rolname = 'disponit_plan_arbeider') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_for_tilbud(TEXT, UUID)'
            ' TO disponit_plan_arbeider';
    END IF;
END $$;

RESET ROLE;
