-- 165 — M-14 (ARC B bokføring, PR 1): bestillingsveiens målport for
-- `faktura.bokfor` og `faktura.bokfor_stor`.
--
-- HVA DØRA SIER, OG HVA DEN IKKE SIER. Den leser fakturaens tilstand og
-- de tre kontrollene registeret alt kjørte ved registreringen (106:
-- `dublett`, `mva`, `leverandor` — hver med utfall `ok`/`avvik`), om
-- fakturaen ligger over tenantens egen beløpsgrense og i så fall om et
-- menneske har kjørt den `manuell`-kontrollen 106 krever der, og hvor
-- mange funn som står åpne. Den bokfører ingenting og attesterer
-- ingenting: attestasjonene mintes i bestillingsveien (API-ets tillit,
-- 161-formen) av det denne døra MÅLTE — `dublettsjekk` og
-- `mva_validert` som `v_regnskap`, `leverandor_i_register` som
-- `v_register` — og et `avvik` blir en USANN attestasjon, som er en sak
-- i unntakskøen, aldri en stille bokføring.
--
-- Hvorfor kontrollRADENE og ikke en ny beregning: 106 regner mva i
-- basen med én skrevet avrundingsregel, og treffraten (`m14_treffrate`)
-- måler nettopp de radene. En andre beregning her ville vært den andre
-- avrundingsregelen 106 advarer mot — og en attestasjon som ikke er
-- målt av treffraten.
SET LOCAL ROLE disponit_faktura_eier;

CREATE FUNCTION m14_for_bokforing(p_tenant TEXT, p_faktura_id UUID)
RETURNS TABLE(status TEXT, leverandor_ref TEXT, fakturanummer TEXT,
              netto_ore BIGINT, mva_ore BIGINT, brutto_ore BIGINT,
              valuta TEXT, utstedt DATE, forfall DATE,
              dublett_ok BOOLEAN, mva_ok BOOLEAN, leverandor_ok BOOLEAN,
              over_belopsgrense BOOLEAN, manuell_ok BOOLEAN,
              apne_funn INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm14_for_bokforing');
    RETURN QUERY
    SELECT f.status, f.leverandor_ref, f.fakturanummer,
           f.netto_ore, f.mva_ore, f.brutto_ore, f.valuta,
           f.utstedt, f.forfall,
           EXISTS (SELECT 1 FROM public.fakturakontroll k
                    WHERE k.tenant = f.tenant AND k.faktura_id = f.faktura_id
                      AND k.kontrolltype = 'dublett' AND k.utfall = 'ok'),
           EXISTS (SELECT 1 FROM public.fakturakontroll k
                    WHERE k.tenant = f.tenant AND k.faktura_id = f.faktura_id
                      AND k.kontrolltype = 'mva' AND k.utfall = 'ok'),
           EXISTS (SELECT 1 FROM public.fakturakontroll k
                    WHERE k.tenant = f.tenant AND k.faktura_id = f.faktura_id
                      AND k.kontrolltype = 'leverandor' AND k.utfall = 'ok'),
           COALESCE((SELECT f.brutto_ore > t.belopsgrense_ore
                       FROM public.fakturaterskel t
                      WHERE t.tenant = f.tenant), true),
           EXISTS (SELECT 1 FROM public.fakturakontroll k
                    WHERE k.tenant = f.tenant AND k.faktura_id = f.faktura_id
                      AND k.kontrolltype = 'manuell' AND k.utfall = 'ok'),
           (SELECT count(*)::int FROM public.fakturafunn ff
             WHERE ff.tenant = f.tenant AND ff.faktura_id = f.faktura_id
               AND ff.apen)
      FROM public.inngaaende_faktura f
     WHERE f.tenant = p_tenant AND f.faktura_id = p_faktura_id;
END $$;
REVOKE ALL ON FUNCTION m14_for_bokforing(TEXT, UUID) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m14_for_bokforing(TEXT, UUID)'
            ' TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
               WHERE rolname = 'disponit_plan_arbeider') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m14_for_bokforing(TEXT, UUID)'
            ' TO disponit_plan_arbeider';
    END IF;
END $$;

RESET ROLE;
