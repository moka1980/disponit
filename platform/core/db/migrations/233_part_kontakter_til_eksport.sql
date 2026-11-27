-- 233 — KONTAKTPUNKTENE I EKSPORTEN, i klartekst.
--
-- Eksporten (27/11, «les + eksport») viste kunderegisterets kontakter bare
-- som MASKER — `part_liste` gir maskene og aldri mer, med vilje: lista er
-- det enhver `part:read` ser. En kunde som slutter, trenger adressene.
-- Hun la dem inn selv; de er hennes.
--
-- DØRA GIR CIPHERTEXT, ALDRI KLARTEKST. Basen har aldri tenantens DEK i
-- klartekst (183s løfte); API-et dekrypterer, og bare i eksportens egen
-- vei (`api/eksport.py`, scopet `firma:eksport`, admin alene). Samme form
-- som M-6s utsendingsdører: ciphertexten videre, klarteksten i API-ets
-- tillit.
--
-- TENANTBUNDET (`krev_tenantkontekst`), slettede kontaktpunkt er ute, og
-- avviklede kunder er med — en avviklet kunde er fortsatt firmaets
-- historikk til retensjonen (184) har tatt adressen.

SET LOCAL ROLE disponit_m37_claimer;

CREATE FUNCTION part_kontakter_eksport(p_tenant TEXT)
RETURNS TABLE(part_id UUID, kanal TEXT, verdi_maske TEXT,
              verdi_kryptert BYTEA, verdi_nonce BYTEA, verdi_key_id TEXT,
              primar BOOLEAN, merkelapp TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'part_kontakter_eksport');
    RETURN QUERY
    SELECT k.part_id, k.kanal, k.verdi_maske, k.verdi_kryptert,
           k.verdi_nonce, k.verdi_key_id, k.primar, k.merkelapp
      FROM public.partkontakt k
     WHERE k.tenant = p_tenant AND k.slettet_ts IS NULL
     ORDER BY k.part_id, k.primar DESC, k.opprettet;
END $$;
REVOKE ALL ON FUNCTION part_kontakter_eksport(TEXT) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION part_kontakter_eksport(TEXT)'
            ' TO disponit';
    END IF;
END $$;

RESET ROLE;
