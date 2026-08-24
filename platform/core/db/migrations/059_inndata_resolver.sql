-- 059: inndata-resolveren (#162 PR-2) — modulens lesevei.
--
-- Modulen er TENANTLØS (035): autorisasjonen er CLAIMET. Retten til å
-- hente en bunt er nøyaktig retten til å holde det plukkede oppdraget
-- bunten er BUNDET til — ikke en rolle, ikke et scope, ikke en liste.
-- Funksjonen er derfor kryss-tenant med BYPASSRLS-eieren (domene_eier,
-- 016/019-formen: avgjørelsen er iboende kryss-tenant), og hele
-- predikatet står i én spørring: bundet inndata ∧ samme eiermodul ∧
-- oppdraget PLUKKET. Ingen rad → ingenting, samme svar uansett årsak
-- (et oppslagsverk over andres bunter skal ikke finnes).

SET LOCAL ROLE disponit_domene_eier;

CREATE FUNCTION hent_inndata_for_modul(
    p_inndata_id UUID, p_eiermodul TEXT)
RETURNS TABLE (tenant TEXT, lager_sti TEXT, key_id TEXT, nonce BYTEA,
               innhold_sha256 TEXT, faktiske_bytes BIGINT)
LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT i.tenant, i.lager_sti, i.key_id, i.nonce, i.innhold_sha256,
           i.faktiske_bytes
      FROM public.inndata_artefakt i
      JOIN public.oppdrag o
        ON o.tenant = i.tenant AND o.id = i.oppdrag_id
     WHERE i.inndata_id = p_inndata_id
       AND i.status = 'bundet'
       AND i.eiermodul = p_eiermodul
       AND o.eiermodul = p_eiermodul
       AND o.status = 'plukket';
$$;

REVOKE ALL ON FUNCTION hent_inndata_for_modul(UUID, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hent_inndata_for_modul(UUID, TEXT) TO disponit;

RESET ROLE;
