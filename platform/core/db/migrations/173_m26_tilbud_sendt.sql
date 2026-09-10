-- 173 — M-26 (ARC B tilbud, PR 5): kvitteringen når registeret —
-- tilbudet er SENDT. 164-formen.
--
-- `sendt` er ingen dom et menneske setter (`m26_avgjor_tilbud` kjenner
-- den ikke): det er det som skjedde, og det går gjennom
-- `m26_tilbud_sendt`, og bare den. Statusen går bare framover: godkjent →
-- sendt, ingenting annet. Et gjenspill av kvitteringen er et stille ja.
ALTER TABLE tilbud DROP CONSTRAINT IF EXISTS tilbud_status_lukket;
ALTER TABLE tilbud ADD CONSTRAINT tilbud_status_lukket
    CHECK (status IN ('utkast', 'godkjent', 'forkastet', 'sendt'));
ALTER TABLE tilbud
    ADD COLUMN sendt_ts TIMESTAMPTZ,
    ADD COLUMN sendt_oppdrag_id BIGINT,
    ADD COLUMN sendt_malversjon TEXT;
ALTER TABLE tilbud ADD CONSTRAINT tilbud_sendt_helhet CHECK (
    (status = 'sendt') = (sendt_ts IS NOT NULL AND sendt_oppdrag_id IS NOT NULL));

CREATE OR REPLACE FUNCTION m26_tilbud_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'tilbud: DELETE avvist — et tilbud som ble gitt,'
            ' ble gitt' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.tilbud_id IS DISTINCT FROM OLD.tilbud_id
       OR NEW.kunde_navn IS DISTINCT FROM OLD.kunde_navn
       OR NEW.kunde_ref IS DISTINCT FROM OLD.kunde_ref
       OR NEW.kunde_hash IS DISTINCT FROM OLD.kunde_hash
       OR NEW.kunde_maske IS DISTINCT FROM OLD.kunde_maske
       OR NEW.kunde_kryptert IS DISTINCT FROM OLD.kunde_kryptert
       OR NEW.nonce_kunde IS DISTINCT FROM OLD.nonce_kunde
       OR NEW.kunde_key_id IS DISTINCT FROM OLD.kunde_key_id
       OR NEW.tilbudsdato IS DISTINCT FROM OLD.tilbudsdato
       OR NEW.gyldig_til IS DISTINCT FROM OLD.gyldig_til
       OR NEW.valuta IS DISTINCT FROM OLD.valuta
       OR NEW.innledning IS DISTINCT FROM OLD.innledning
       OR NEW.sum_ore IS DISTINCT FROM OLD.sum_ore
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av THEN
        RAISE EXCEPTION 'tilbud: innholdet er frosset — et rettet tilbud er'
            ' et NYTT tilbud' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status = 'sendt'
       AND (NEW.sendt_ts IS DISTINCT FROM OLD.sendt_ts
            OR NEW.sendt_oppdrag_id IS DISTINCT FROM OLD.sendt_oppdrag_id
            OR NEW.sendt_malversjon IS DISTINCT FROM OLD.sendt_malversjon) THEN
        RAISE EXCEPTION 'tilbud: sendingen er frosset'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status
       AND NOT ((OLD.status = 'utkast'
                 AND NEW.status IN ('godkjent', 'forkastet'))
                OR (OLD.status = 'godkjent' AND NEW.status = 'sendt')) THEN
        RAISE EXCEPTION 'tilbud: status går bare fra utkast til godkjent/'
            'forkastet, og fra godkjent til sendt (kvitteringens vei, 173)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status <> 'utkast' AND OLD.status = 'utkast' THEN
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL OR NEW.avgjort_av IS DISTINCT FROM v_aktor THEN
            RAISE EXCEPTION 'tilbud: avgjort_av (%) er ikke aktøren som'
                ' avgjør (%)', coalesce(NEW.avgjort_av, '<null>'),
                coalesce(v_aktor, '<ingen>')
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;

SET LOCAL ROLE disponit_prisbok_eier;

CREATE FUNCTION m26_tilbud_sendt(
    p_tenant TEXT, p_tilbud_id UUID, p_oppdrag_id BIGINT,
    p_sendt_ts TIMESTAMPTZ, p_malversjon TEXT, p_mottaker_maske TEXT,
    p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_ts TIMESTAMPTZ := coalesce(p_sendt_ts, now());
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_tilbud_sendt');
    IF p_oppdrag_id IS NULL THEN
        RAISE EXCEPTION 'm26_tilbud_sendt: oppdraget må være satt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT t.status INTO v_status FROM public.tilbud t
     WHERE t.tenant = p_tenant AND t.tilbud_id = p_tilbud_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm26_tilbud_sendt: tilbudet finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_status = 'sendt' THEN
        RETURN false;                              -- gjenspill: stille ja
    END IF;
    IF v_status <> 'godkjent' THEN
        RAISE EXCEPTION 'm26_tilbud_sendt: bare et godkjent tilbud kan bli'
            ' sendt — dette er %', v_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.tilbud
       SET status = 'sendt', sendt_ts = v_ts, sendt_oppdrag_id = p_oppdrag_id,
           sendt_malversjon = p_malversjon
     WHERE tenant = p_tenant AND tilbud_id = p_tilbud_id;
    PERFORM public.m26_evidens(
        p_tenant, p_tilbud_id, 'tilbud.sendt', p_aktor,
        jsonb_build_object('oppdrag_id', p_oppdrag_id, 'sendt_ts', v_ts,
                           'malversjon', p_malversjon,
                           'mottaker_maske', p_mottaker_maske));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m26_tilbud_sendt(TEXT, UUID, BIGINT, TIMESTAMPTZ,
    TEXT, TEXT, TEXT) FROM PUBLIC;

-- Hodet for ETT tilbud, samme kolonner og samme to fakta som lista
-- (169). Detaljveien lette før i den trunkerte lista (CodeRabbit,
-- PR 3): tilbud nr. 1001 fantes ikke for GET, men fantes for POST.
CREATE FUNCTION m26_tilbudshodet(p_tenant TEXT, p_tilbud_id UUID)
RETURNS TABLE(tilbud_id UUID, kunde_navn TEXT, kunde_ref TEXT,
              kunde_maske TEXT, tilbudsdato DATE, gyldig_til DATE,
              valuta TEXT, sum_ore BIGINT, status TEXT, antall_linjer INT,
              priser_fra_boka BOOLEAN, klausuler_uendret BOOLEAN,
              avgjort_ts TIMESTAMPTZ, avgjort_av TEXT,
              opprettet TIMESTAMPTZ, opprettet_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_tilbudshodet');
    RETURN QUERY
    SELECT t.tilbud_id, t.kunde_navn, t.kunde_ref, t.kunde_maske,
           t.tilbudsdato, t.gyldig_til, t.valuta, t.sum_ore, t.status,
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
                       ORDER BY k.versjon DESC LIMIT 1)),
           t.avgjort_ts, t.avgjort_av, t.opprettet, t.opprettet_av
      FROM public.tilbud t
     WHERE t.tenant = p_tenant AND t.tilbud_id = p_tilbud_id;
END $$;
REVOKE ALL ON FUNCTION m26_tilbudshodet(TEXT, UUID) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_tilbud_sendt(TEXT, UUID,'
            ' BIGINT, TIMESTAMPTZ, TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_tilbudshodet(TEXT, UUID)'
            ' TO disponit';
    END IF;
END $$;

RESET ROLE;
