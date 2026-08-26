-- ============================================================
-- 063: fornyelsesveien (#165) — heartbeat fra utføreren.
--
-- 037 sa det selv: «En ekte fornyelsesvei (heartbeat fra utføreren)
-- ville gitt begge deler, men den er en ny autentisert endepunktsflate
-- med egen spec-runde.» Dette er den runden. Uten den var autoriteten
-- én time (lease-taket + opplastingskapabilitetens klemme), og enhver
-- ordrefrist utover det var et løfte ingen kunne holde — #210 klemte
-- derfor rekrutteringsfristen ned til taket. Med fornyelsen kan en
-- LEVENDE utfører holde autoriteten sin gjennom hele oppdragets frist,
-- og fristen kan gå tilbake til klarsignalets tall.
--
-- FORMEN ER 037s EGEN, én rad om gangen:
--   * bare den SITTENDE eieren kan fornye — raden må matche
--     (modul, claim_id, generation) nøyaktig, og leasen må være I LIVE.
--     En død lease kan ALDRI fornyes: etter utløp kan en annen utfører
--     lovlig ha reclaimet, og en gjenoppstandelse ville slåss med
--     fencing-generasjonen i stedet for å respektere den.
--   * fornyelsen gir aldri mer enn ett nytt grant-vindu (3600 s-taket
--     står), og aldri lenger enn oppdragets egen utforelsesfrist —
--     etter fristen er arbeidet uansett dødt (037s reclaim-vilkår).
--   * modulepoch måles UNDER radlåsen, som ved claim (port 24): en
--     deployment som er rullet forbi skal ikke kunne holde liv i et
--     gammelt claim med friske heartbeats.
--
-- Eierskap og rolle er claim-veiens egne (disponit_m37_claimer, samme
-- som 015/037/049): fornyelsen ER et claim-livssyklussteg.
-- ============================================================

SET LOCAL ROLE disponit_m37_claimer;
CREATE FUNCTION forny_oppdragslease(
    p_oppdrag_id BIGINT, p_modul_id TEXT, p_claim_id TEXT,
    p_generation INT, p_lease_s INT DEFAULT 300)
RETURNS TABLE (owner_lease_utloper TIMESTAMPTZ, tenant TEXT,
               modul_id TEXT, kontraktversjon INT, kontrakt_hash TEXT,
               module_epoch BIGINT, evidensfrist TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_lease INT := least(greatest(coalesce(p_lease_s, 300), 30), 3600);
    v_epoch BIGINT;
    r RECORD;
BEGIN
    IF p_modul_id IS NULL OR p_claim_id IS NULL OR p_oppdrag_id IS NULL
       OR p_generation IS NULL THEN
        RAISE EXCEPTION 'forny_oppdragslease: identiteten er ufullstendig'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Radlåsen først: epoch-målingen og fornyelsen skjer mot samme
    -- øyeblikksbilde, som ved claim.
    SELECT o.id, o.tenant, o.eiermodul, o.modul_id, o.kontraktversjon,
           o.kontrakt_hash, o.module_epoch, o.evidensfrist,
           o.utforelsesfrist, o.status, o.owner_claim_id,
           o.owner_generation, o.owner_lease_utloper
      INTO r
      FROM public.oppdrag o
     WHERE o.id = p_oppdrag_id
       FOR UPDATE;
    IF NOT FOUND
       OR r.status <> 'plukket'
       OR r.eiermodul IS DISTINCT FROM p_modul_id
       OR r.owner_claim_id IS DISTINCT FROM p_claim_id
       OR r.owner_generation IS DISTINCT FROM p_generation THEN
        -- ÉN kode for alle identitetsavvik: et oppslagsverk over andres
        -- claims skal ikke finnes (058-formen).
        RAISE EXCEPTION 'forny_oppdragslease: ingen fornybar lease'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF r.owner_lease_utloper IS NULL
       OR r.owner_lease_utloper <= clock_timestamp() THEN
        -- Død lease: aldri gjenoppstandelse. Egen kode — dette er den
        -- ENE grenen utføreren kan handle på (arbeidet er tapt, slutt).
        RAISE EXCEPTION 'forny_oppdragslease: leasen er utløpt'
            USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    IF r.utforelsesfrist <= clock_timestamp() THEN
        RAISE EXCEPTION 'forny_oppdragslease: utforelsesfristen er ute'
            USING ERRCODE = 'object_not_in_prerequisite_state';
    END IF;
    -- Epoken måles mot LEVENDE modulhode under radlåsen (port 24-formen
    -- fra claim): en deployment som er rullet forbi — nøddeaktivering
    -- løfter epoken — skal ikke kunne holde liv i et gammelt claim med
    -- friske heartbeats. Kalleren sender ingenting; serveren VET.
    -- Legacy-claims (module_epoch IS NULL) bar aldri epokebindingen og
    -- måles ikke — som ved claim.
    IF r.module_epoch IS NOT NULL THEN
        SELECT h.module_epoch INTO v_epoch
          FROM public.modulhode h
         WHERE h.modul_id = coalesce(r.modul_id, r.eiermodul);
        IF v_epoch IS DISTINCT FROM r.module_epoch THEN
            -- EGEN SQLSTATE (CodeRabbit): en rullet epoch er en
            -- AUTORISASJONSDOM (28000 → `modulepoch_utdatert`, 403),
            -- ikke driftens «arbeidet er tapt» — de to skal kunne
            -- skilles av kalleren uten å parse meldingstekst.
            RAISE EXCEPTION 'forny_oppdragslease: modulepoken er rullet'
                USING ERRCODE = 'invalid_authorization_specification';
        END IF;
    END IF;
    UPDATE public.oppdrag o
       SET owner_lease_utloper = least(
               now() + '3600 seconds'::INTERVAL,
               least(now() + (v_lease || ' seconds')::INTERVAL,
                     o.utforelsesfrist))
     WHERE o.id = p_oppdrag_id;
    -- 037 strakk leasen TIL fristen ved claim (greatest); fornyelsen
    -- gjør det MOTSATTE (least): hvert heartbeat er et lite vindu, og
    -- det er selve poenget — en utfører som slutter å puste mister
    -- autoriteten ved neste vindu, ikke ved fristen.
    SELECT o.owner_lease_utloper INTO owner_lease_utloper
      FROM public.oppdrag o WHERE o.id = p_oppdrag_id;
    tenant := r.tenant; modul_id := r.modul_id;
    kontraktversjon := r.kontraktversjon;
    kontrakt_hash := r.kontrakt_hash; module_epoch := r.module_epoch;
    evidensfrist := r.evidensfrist;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION forny_oppdragslease(BIGINT, TEXT, TEXT, INT, INT) FROM PUBLIC;
RESET ROLE;
