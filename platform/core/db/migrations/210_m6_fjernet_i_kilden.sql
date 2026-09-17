-- 210 — M-6: EN MELDING SOM ER SLETTET I POSTBOKSEN, SLETTES I DISPONIT.
--
-- Eiers funn 17/9: «eposter som jeg slettet fra m365/outlook blir ikke
-- oppdatert på disponit.com, de ligger på disponit selv om de er slettet
-- fra outlook». Innhenteren bruker Graph-delta, og delta leverer
-- slettinger som `@removed`-poster — innhenteren hoppet over dem. En
-- sletting i postboksen nådde derfor aldri registeret.
--
-- To dører, begge NØYAKTIG 176s overgang (alle fire lagrene i samme
-- transaksjon, `slettet_ts` satt, teksten borte, evidens skrevet) — men
-- aktøren er innhenteren, ikke et menneske, og hendelsen har sitt eget
-- navn: `epost.melding_fjernet_i_kilden`. Et menneske som leser
-- revisjonsloggen skal kunne skille «jeg slettet den her» fra «den
-- forsvant fra postboksen».
--
--  * `m6_melding_fjernet_i_kilden(tenant, kilde, leverandør-id, aktør)`:
--    delta-`@removed` og avstemmingens 404. Idempotent (alt slettet →
--    false; ukjent melding → false: en sletting av noe vi aldri hentet
--    er ingenting å bokføre).
--  * `m6_marker_sjekket(tenant, kilde, leverandør-id[])`: avstemmingen
--    (delta forteller bare om slettinger ETTER forrige henting; det som
--    alt var slettet må spørres om). Innhenteren sjekker et begrenset
--    antall av de eldst sjekkede meldingene per runde mot Graph; de som
--    fortsatt finnes får `kilde_sjekket_ts`, så køen roterer.
--
-- Eierskapet er claimerens som i 176 (den rollen 088 ga UPDATE på
-- payload-lagrene); EXECUTE til planarbeideren gis i `migrer.py`s
-- PLAN_RETTIGHETER, som de andre innhenterdørene.
-- Tabellen eies av migratoren (088); claimeren har SELECT/UPDATE.
ALTER TABLE epost_melding ADD COLUMN IF NOT EXISTS kilde_sjekket_ts TIMESTAMPTZ;

SET LOCAL ROLE disponit_m37_claimer;
CREATE FUNCTION m6_melding_fjernet_i_kilden(p_tenant TEXT, p_kilde_id UUID,
                                            p_lev_id TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_naa TIMESTAMPTZ := pg_catalog.now(); v_id UUID; v_alt TIMESTAMPTZ;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm6_melding_fjernet_i_kilden');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm6_melding_fjernet_i_kilden: en sletting har en aktør'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT m.melding_id, m.slettet_ts INTO v_id, v_alt
      FROM public.epost_melding m
     WHERE m.tenant = p_tenant AND m.kilde_id = p_kilde_id
       AND m.leverandor_melding_id = p_lev_id
       FOR UPDATE;
    IF NOT FOUND OR v_alt IS NOT NULL THEN
        RETURN false;                              -- ukjent eller alt slettet
    END IF;
    UPDATE public.epost_vedlegg v
       SET navn_kryptert = NULL, nonce = NULL, key_id = NULL, slettet_ts = v_naa
     WHERE v.tenant = p_tenant AND v.melding_id = v_id AND v.slettet_ts IS NULL;
    UPDATE public.epost_klassifisering k
       SET sammendrag_kryptert = NULL, nonce = NULL, key_id = NULL, slettet_ts = v_naa
     WHERE k.tenant = p_tenant AND k.melding_id = v_id AND k.slettet_ts IS NULL;
    UPDATE public.epost_utkast u
       SET tekst_kryptert = NULL, nonce = NULL, key_id = NULL, slettet_ts = v_naa
     WHERE u.tenant = p_tenant AND u.melding_id = v_id AND u.slettet_ts IS NULL;
    UPDATE public.epost_melding m
       SET kropp_kryptert = NULL, nonce = NULL, key_id = NULL, slettet_ts = v_naa,
           kilde_sjekket_ts = v_naa
     WHERE m.tenant = p_tenant AND m.melding_id = v_id;
    PERFORM public.m6_evidens(p_tenant, v_id, 'epost.melding_fjernet_i_kilden',
                              p_aktor, jsonb_build_object('slettet_ts', v_naa,
                                                          'kilde_id', p_kilde_id::text));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m6_melding_fjernet_i_kilden(TEXT, UUID, TEXT, TEXT) FROM PUBLIC;

CREATE FUNCTION m6_marker_sjekket(p_tenant TEXT, p_kilde_id UUID, p_lev_ids TEXT[])
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_n INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm6_marker_sjekket');
    UPDATE public.epost_melding m
       SET kilde_sjekket_ts = pg_catalog.now()
     WHERE m.tenant = p_tenant AND m.kilde_id = p_kilde_id
       AND m.leverandor_melding_id = ANY (p_lev_ids)
       AND m.slettet_ts IS NULL;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    RETURN v_n;
END $$;
REVOKE ALL ON FUNCTION m6_marker_sjekket(TEXT, UUID, TEXT[]) FROM PUBLIC;

-- Avstemmingens kø: de eldst sjekkede (aldri sjekkede først) av kildens
-- levende innkommende meldinger. Definer-dør fordi planarbeideren ikke
-- leser meldingene selv (088: den skriver dem).
CREATE FUNCTION m6_avstemmingskandidater(p_tenant TEXT, p_kilde_id UUID,
                                         p_grense INT)
RETURNS TABLE (leverandor_melding_id TEXT)
LANGUAGE plpgsql SECURITY DEFINER STABLE
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm6_avstemmingskandidater');
    RETURN QUERY
    SELECT m.leverandor_melding_id
      FROM public.epost_melding m
     WHERE m.tenant = p_tenant AND m.kilde_id = p_kilde_id
       AND m.retning = 'inn' AND m.slettet_ts IS NULL
     ORDER BY m.kilde_sjekket_ts NULLS FIRST, m.mottatt_ts
     LIMIT greatest(1, least(coalesce(p_grense, 20), 200));
END $$;
REVOKE ALL ON FUNCTION m6_avstemmingskandidater(TEXT, UUID, INT) FROM PUBLIC;
RESET ROLE;
