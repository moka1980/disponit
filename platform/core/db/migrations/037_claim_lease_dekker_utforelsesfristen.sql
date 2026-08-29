-- ============================================================
-- 037 — eier-leasen dekker oppdragets EGEN utførelsesfrist (Codex P1)
--
-- `claim_neste_oppdrag` (015) ga hvert claim en fast lease på det
-- API-et ba om — 300 sekunder fra `/v1/oppdrag/claim`. Samtidig lar
-- reclaim-grenen fra 005 et `plukket` oppdrag med UTLØPT lease bli
-- claimbart igjen. De to tallene var enige så lenge alt arbeid var
-- kort. Med PR-014c er de det ikke: en WCAG-kontroll har en annonsert
-- frist på 30 min (enkeltside) / 60 min (nettsted), og motorens eget
-- tidsavbrudd er 3600 s.
--
-- Da oppsto dette, uten at noe var nede eller feilkonfigurert:
--
--   t+0     controller A claimer, lease t+300, starter motoren
--   t+300   leasen utløper mens A fortsatt skanner
--   t+301   controller B poller, ser en utløpt lease, CLAIMER SAMME
--           oppdrag og starter ET NYTT eksternt skann av kundens
--           nettsted. `owner_generation` økes.
--   t+900   A blir ferdig, laster opp og kvitterer — og blir avvist på
--           fencing, fordi generasjonen har flyttet seg.
--
-- Tre skader i én: DOBBEL ekstern trafikk mot noen andres nettsted
-- (`ekstern_lesing` er nettopp den trafikken målautorisasjon og
-- frekvensport finnes for å begrense), et fullført arbeid som ikke kan
-- avsluttes, og et oppdrag som kan gjenta runden til fristen.
--
-- FIKSEN: leasen er plattformens løfte om EKSKLUSIVITET for arbeidet,
-- og plattformen har allerede sagt hvor lenge arbeidet får ta —
-- `utforelsesfrist` på raden. Leasen strekkes derfor til minst den
-- fristen, aldri lenger enn funksjonens eksisterende tak på 3600 s, og
-- aldri kortere enn kallerens `p_lease_s`. Ingen ny parameter, ingen ny
-- kolonne, ingen ny kilde til sannhet.
--
-- HVA DETTE KOSTER, uttalt: reclaim-grenen krever `utforelsesfrist >
-- now()`, så et oppdrag var uansett ALDRI reclaimbart etter fristen. Den
-- eneste tilstanden 300-sekunderleasen faktisk åpnet for, var reclaim
-- MENS den første eieren fortsatt hadde lov til å jobbe — og der kan
-- ingen skille en krasjet eier fra en travel. Etter denne endringen
-- venter et krasjet oppdrag til `utforelsesfrist` og går den veien
-- 014c/§10 alt beskriver: ufullført → M-37-sak. Det er en tregere
-- gjenopptagelse, mot at plattformen slutter å bestille det samme
-- eksterne arbeidet to ganger. En ekte fornyelsesvei (heartbeat fra
-- utføreren) ville gitt begge deler, men den er en ny autentisert
-- endepunktsflate med egen spec-runde — ikke noe å improvisere her.
--
-- Funksjonen er ellers ordrett 015: kun UPDATE-ens `owner_lease_utloper`
-- er endret. Signaturen er uendret, så `CREATE OR REPLACE` beholder både
-- eierskap (disponit_m37_claimer) og privilegier — designtabellen i
-- reparasjonen står urørt.
-- ============================================================

SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION claim_neste_oppdrag(
    p_modul_id TEXT, p_prefiks TEXT[], p_claim_id TEXT,
    p_lease_s INT DEFAULT 300, p_release_id TEXT DEFAULT NULL,
    p_miljo TEXT DEFAULT NULL, p_module_epoch BIGINT DEFAULT NULL)
RETURNS TABLE (id BIGINT, tenant TEXT, unntak_id BIGINT, oppdragstype TEXT,
               handling TEXT, repair_operation_id TEXT,
               payload_kryptert BYTEA, key_id TEXT, nonce BYTEA,
               owner_generation INT, utforelsesfrist TIMESTAMPTZ,
               evidensfrist TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_lease INT := least(greatest(coalesce(p_lease_s, 300), 30), 3600);
    v_hoppet BIGINT[] := ARRAY[]::BIGINT[];
    v_id BIGINT; v_ot TEXT; r RECORD; v_ok BOOLEAN;
    v_b_modul TEXT; v_b_ver INT; v_b_hash TEXT; v_b_epoch BIGINT;
BEGIN
    IF p_claim_id IS NULL OR p_claim_id !~ '^[0-9a-f]{32,}$' THEN
        RAISE EXCEPTION 'claim_neste_oppdrag: ugyldig claim_id-format';
    END IF;
    IF p_modul_id IS NULL OR length(btrim(p_modul_id)) = 0 THEN
        RAISE EXCEPTION 'claim_neste_oppdrag: modul_id mangler';
    END IF;

    -- Codex P1: DELT gjerde mot registrering av oppdragstyper — SAMME nøkkel som
    -- `registrer_oppdragstype` tar EKSKLUSIVT (014). Uten dette er «uregistrert»
    -- en avgjørelse tatt på et snapshot: en `registrer_oppdragstype` kan committe
    -- ETTER at kandidatsøket og registeroppslaget her har lest, men FØR claimet
    -- committer, og oppdraget blir da claimet UBUNDET (ingen kontrakt, ingen
    -- epoch) og uten aktiv-modul-/deployment-portene — nøyaktig den porten
    -- registreringen skulle innføre. Gjerdet tas FØR kandidatsøket, som selv leser
    -- `oppdragstype_register` i predikatet, så begge lesningene ligger innenfor.
    -- Delt: samtidige claims (også på tvers av moduler) sperrer ikke for
    -- hverandre; kun en registrering venter, og registrering er en sjelden
    -- admin-/deployhandling. Låserekkefølgen er global → modul → oppdragsrad i
    -- ALLE stier (overgangsfunksjonene tar bare modul-låsen), så ingen syklus.
    PERFORM pg_advisory_xact_lock_shared(
        hashtextextended('modulregister:oppdragstype', 0));

    LOOP
        -- Oppdragslås: neste kandidat for eiermodulen (SKIP LOCKED). Den betingede
        -- binding-tilgjengeligheten er ALT et predikat her (Codex P2: da låses
        -- ikke en uclaimbar backlog rad for rad) — en registrert oppdragstype
        -- selekteres bare når kallerens deployment er claiming og modulen aktiv.
        -- v_hoppet holder kun de sjeldne som taper race-en mot noddeaktiver under
        -- modul-låsen (re-verifiseringen nedenfor); SKIP LOCKED hopper ikke over
        -- rader denne transaksjonen selv har låst.
        SELECT k.id, k.oppdragstype INTO v_id, v_ot
          FROM public.oppdrag k
         WHERE (
                 k.status = 'opprettet'
                 OR (k.status = 'plukket' AND k.owner_lease_utloper IS NOT NULL
                     AND k.owner_lease_utloper < now())
               )
           AND k.eiermodul = p_modul_id
           AND p_prefiks IS NOT NULL
           AND array_length(p_prefiks, 1) > 0
           AND EXISTS (SELECT 1 FROM unnest(p_prefiks) AS pre
                        WHERE k.handling LIKE pre || '%')
           AND k.utforelsesfrist > now()
           AND k.id <> ALL (v_hoppet)
           AND (
                 NOT EXISTS (SELECT 1 FROM public.oppdragstype_register reg
                              WHERE reg.oppdragstype = k.oppdragstype)
                 OR EXISTS (
                     SELECT 1 FROM public.oppdragstype_register reg
                       JOIN public.modulhode h
                         ON h.modul_id = reg.eiermodul AND h.status = 'aktiv'
                        AND h.module_epoch IS NOT DISTINCT FROM p_module_epoch
                       JOIN public.moduldeployment d
                         ON d.modul_id = reg.eiermodul
                        AND d.kontraktversjon = reg.kontraktversjon
                        AND d.kontrakt_hash = reg.kontrakt_hash
                        AND d.release_id = p_release_id AND d.miljo = p_miljo
                        AND d.livslop = 'claiming'
                      WHERE reg.oppdragstype = k.oppdragstype
                        AND reg.eiermodul = p_modul_id)
               )
         ORDER BY k.opprettet, k.id
           FOR UPDATE SKIP LOCKED
         LIMIT 1;
        IF NOT FOUND THEN
            RETURN;   -- tom kø (eller alle gjenværende ikke claimbare av kalleren)
        END IF;

        -- Tabellen aliases (reg): funksjonens RETURNS TABLE-kolonner (id, tenant,
        -- oppdragstype, ...) er OUT-variabler i skopet og ville ellers kollidert
        -- med en ukvalifisert kolonnereferanse (AmbiguousColumn).
        SELECT reg.eiermodul, reg.kontraktversjon, reg.kontrakt_hash INTO r
          FROM public.oppdragstype_register reg WHERE reg.oppdragstype = v_ot;

        IF NOT FOUND THEN
            -- Legacy: uregistrert oppdragstype → ingen binding (som før).
            v_b_modul := NULL; v_b_ver := NULL; v_b_hash := NULL; v_b_epoch := NULL;
        ELSE
            -- Modul-lås, DELT (Codex P2): claims skal serialiseres mot
            -- overgangene (nødstopp/status/releasebytte tar den EKSKLUSIVT),
            -- men ikke mot hverandre — en eksklusiv lås her ville køet alle
            -- modulens pollere bak hele claim-transaksjonen (API-et committer
            -- først etter dekryptering, minimering og kapabilitetsutstedelse),
            -- selv når SKIP LOCKED alt har gitt dem hver sin rad. Delt lås gir
            -- samme gjerde mot nødstopp: den venter til et evt. samtidig
            -- noddeaktiver_modul har committet, så re-lesingen under er FERSK.
            PERFORM pg_advisory_xact_lock_shared(
                hashtextextended('modul:' || r.eiermodul, 0));
            SELECT (r.eiermodul = p_modul_id)   -- Codex P1: eiermodulen eier typen
               AND EXISTS (SELECT 1 FROM public.modulhode h
                            WHERE h.modul_id = r.eiermodul AND h.status = 'aktiv'
                              AND h.module_epoch IS NOT DISTINCT FROM p_module_epoch)
               AND EXISTS (SELECT 1 FROM public.moduldeployment d
                            WHERE d.modul_id = r.eiermodul
                              AND d.kontraktversjon = r.kontraktversjon
                              AND d.kontrakt_hash = r.kontrakt_hash
                              AND d.release_id = p_release_id      -- Codex P1:
                              AND d.miljo = p_miljo                -- KALLERENS
                              AND d.livslop = 'claiming')          -- deployment
              INTO v_ok;
            IF NOT v_ok THEN
                v_hoppet := array_append(v_hoppet, v_id);
                CONTINUE;   -- ikke claimbar av denne kalleren; prøv neste
            END IF;
            v_b_modul := r.eiermodul; v_b_ver := r.kontraktversjon;
            v_b_hash := r.kontrakt_hash; v_b_epoch := p_module_epoch;
        END IF;

        RETURN QUERY
        UPDATE public.oppdrag o
           SET status = 'plukket',
               owner_claim_id = p_claim_id,
               owner_generation = o.owner_generation + 1,
               -- Codex P1 (037): leasen dekker oppdragets EGEN frist.
               -- `greatest(...)` strekker den til `utforelsesfrist` når
               -- kallerens tall er kortere enn arbeidet plattformen selv
               -- har gitt tid til; `least(...)` holder funksjonens tak på
               -- 3600 s. Etter fristen er raden uansett ikke claimbar
               -- (`k.utforelsesfrist > now()` over), så en lease som
               -- slutter der stenger nøyaktig det vinduet der en
               -- «utløpt» lease bare betydde at eieren fortsatt jobbet.
               owner_lease_utloper = least(
                   now() + '3600 seconds'::INTERVAL,
                   greatest(now() + (v_lease || ' seconds')::INTERVAL,
                            o.utforelsesfrist)),
               modul_id = v_b_modul, kontraktversjon = v_b_ver,
               kontrakt_hash = v_b_hash, module_epoch = v_b_epoch
         WHERE o.id = v_id
        RETURNING o.id, o.tenant, o.unntak_id, o.oppdragstype, o.handling,
                  o.repair_operation_id, o.payload_kryptert, o.key_id, o.nonce,
                  o.owner_generation, o.utforelsesfrist, o.evidensfrist;
        RETURN;
    END LOOP;
END $$;
REVOKE ALL ON FUNCTION claim_neste_oppdrag(TEXT, TEXT[], TEXT, INT, TEXT, TEXT, BIGINT) FROM PUBLIC;
RESET ROLE;
