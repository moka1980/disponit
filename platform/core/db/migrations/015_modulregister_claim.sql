-- ============================================================
-- 015 — Modulregister (PR-014a CP5): claim ↔ modulkontrakt-binding under
-- oppdragslåsen.
--
-- Et oppdrag hvis `oppdragstype` er REGISTRERT i `oppdragstype_register` (014)
-- kan bare claimes hvis eiermodulen er AKTIV og har en `claiming`-deployment for
-- den AUTORISERTE kontrakten (modul_id + kontraktversjon + kontrakt_hash). Uten
-- det er raden ikke claimbar (port 4: rett hash/feil versjon avvist; port 5:
-- kontrakt-A kan ikke claime B). Bindingen er BETINGET: et oppdrag med UREGISTRERT
-- oppdragstype claimes NØYAKTIG som før (legacy) — ingen eksisterende M-37-flyt
-- endres. Ved claim stemples kontrakt + `module_epoch` på oppdraget (write-once;
-- epoch monoton), slik at fencing-generasjonen kan følges gjennom kjeden.
--
-- Kontrollen skjer UNDER oppdragslåsen — den samme `FOR UPDATE SKIP LOCKED`-raden
-- som `claim_neste_oppdrag` alt tar (005). Ingen ny lås, ingen ny låserekkefølge.
--
-- V3 (kvittering etter retired): `retired` betyr «kan aldri claime», IKKE at
-- historiske kvitteringer er ugyldige. Release/deployment kan derfor ALDRI
-- slettes mens bindinger finnes — moduldeployment får en slett-vakt her (release
-- hadde den alt via append-only-triggeren).
-- ============================================================

-- ------------------------------------------------------------
-- 1. Kontraktbinding på oppdrag (NULL for legacy/uregistrerte oppdragstyper).
-- ------------------------------------------------------------
ALTER TABLE oppdrag
    ADD COLUMN IF NOT EXISTS modul_id        TEXT,
    ADD COLUMN IF NOT EXISTS kontraktversjon INT,
    ADD COLUMN IF NOT EXISTS kontrakt_hash   TEXT,
    ADD COLUMN IF NOT EXISTS module_epoch    BIGINT;

-- Kontraktbindingen er skrivbar ÉN gang (ved første claim) og deretter frosset;
-- en reclaim re-stempler de SAMME kontraktverdiene (oppdragstypen pinner
-- kontrakten). `module_epoch` kan bare stige (nød/reaktivering hos modulen).
-- Innfelt i den eksisterende kolonnelåsen — ellers ville de nye kolonnene vært
-- fritt muterbare (m37_claimer har UPDATE på oppdrag).
CREATE OR REPLACE FUNCTION oppdrag_kolonnelaas()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.unntak_id IS DISTINCT FROM OLD.unntak_id
       OR NEW.loggpost_id IS DISTINCT FROM OLD.loggpost_id
       OR NEW.repair_operation_id IS DISTINCT FROM OLD.repair_operation_id
       OR NEW.oppdragstype IS DISTINCT FROM OLD.oppdragstype
       OR NEW.handling IS DISTINCT FROM OLD.handling
       OR NEW.eiermodul IS DISTINCT FROM OLD.eiermodul
       OR NEW.payload_kryptert IS DISTINCT FROM OLD.payload_kryptert
       OR NEW.key_id IS DISTINCT FROM OLD.key_id
       OR NEW.alg IS DISTINCT FROM OLD.alg
       OR NEW.nonce IS DISTINCT FROM OLD.nonce
       OR NEW.utforelsesfrist IS DISTINCT FROM OLD.utforelsesfrist
       OR NEW.evidensfrist IS DISTINCT FROM OLD.evidensfrist
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'oppdrag: kun status-, owner- og kvitteringsfelter kan endres';
    END IF;
    IF NEW.owner_generation < OLD.owner_generation THEN
        RAISE EXCEPTION 'oppdrag: owner_generation kan aldri reduseres';
    END IF;
    -- CP5 + Codex P1: kontraktbinding/epoch endres KUN av den herdede claim-
    -- funksjonen (eid av disponit_m37_claimer). Runtime har direkte UPDATE på
    -- oppdrag; uten denne current_user-sjekken kunne runtime initialisere en
    -- uclaimet rad med vilkårlig modul/versjon/hash (forfalske bindingen) eller
    -- nulle en stemplet epoch.
    IF current_user <> 'disponit_m37_claimer' AND (
           NEW.modul_id        IS DISTINCT FROM OLD.modul_id
        OR NEW.kontraktversjon IS DISTINCT FROM OLD.kontraktversjon
        OR NEW.kontrakt_hash   IS DISTINCT FROM OLD.kontrakt_hash
        OR NEW.module_epoch    IS DISTINCT FROM OLD.module_epoch) THEN
        RAISE EXCEPTION 'oppdrag: kontraktbinding/epoch settes kun av claim-funksjonen';
    END IF;
    -- Write-once (gjelder også claim-funksjonen på en reclaim): satt → frosset.
    IF OLD.modul_id IS NOT NULL AND (
           NEW.modul_id        IS DISTINCT FROM OLD.modul_id
        OR NEW.kontraktversjon IS DISTINCT FROM OLD.kontraktversjon
        OR NEW.kontrakt_hash   IS DISTINCT FROM OLD.kontrakt_hash) THEN
        RAISE EXCEPTION 'oppdrag: kontraktbindingen er frosset når den er satt';
    END IF;
    -- module_epoch er monoton OG kan ikke nulles etter at den er satt (Codex P2:
    -- non-NULL→NULL fjernet fencing-generasjonen uten feil).
    IF OLD.module_epoch IS NOT NULL
       AND (NEW.module_epoch IS NULL OR NEW.module_epoch < OLD.module_epoch) THEN
        RAISE EXCEPTION 'oppdrag: module_epoch kan aldri reduseres/nulles';
    END IF;
    IF OLD.kvittering IS NOT NULL
       AND (NEW.kvittering IS DISTINCT FROM OLD.kvittering
            OR NEW.kvittering_signatur IS DISTINCT FROM OLD.kvittering_signatur
            OR NEW.resultathash IS DISTINCT FROM OLD.resultathash) THEN
        RAISE EXCEPTION 'oppdrag: kvitteringen er uforanderlig når den først er lagret';
    END IF;
    IF NOT (
        (OLD.status = 'opprettet' AND NEW.status IN ('plukket','kansellert','feilet')) OR
        (OLD.status = 'plukket'   AND NEW.status IN ('utfort','feilet','opprettet')) OR
        (OLD.status = NEW.status)
    ) THEN
        RAISE EXCEPTION 'oppdrag: ulovlig statusovergang % -> %', OLD.status, NEW.status;
    END IF;
    IF OLD.status IN ('utfort','feilet','kansellert') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'oppdrag: % er terminal', OLD.status;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        NEW.status_ts := now();
    END IF;
    RETURN NEW;
END $$;

-- Codex P1: kolonnelåsen er BEFORE UPDATE; runtime har direkte INSERT på oppdrag
-- og kunne ellers opprette en rad med FORFALSKET binding (eller en som gjør
-- oppdraget permanent uclaimbart). Et oppdrag opprettes ALLTID ubundet — bindingen
-- stemples kun ved claim (UPDATE, av claim-funksjonen). Egen BEFORE INSERT-vakt.
CREATE OR REPLACE FUNCTION oppdrag_binding_ved_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.modul_id IS NOT NULL OR NEW.kontraktversjon IS NOT NULL
       OR NEW.kontrakt_hash IS NOT NULL OR NEW.module_epoch IS NOT NULL THEN
        RAISE EXCEPTION 'oppdrag: kontraktbinding kan ikke settes ved opprettelse '
            '(stemples kun ved claim)';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS oppdrag_binding_insert ON oppdrag;
CREATE TRIGGER oppdrag_binding_insert BEFORE INSERT ON oppdrag
    FOR EACH ROW EXECUTE FUNCTION oppdrag_binding_ved_insert();

-- ------------------------------------------------------------
-- 2. m37_claimer (eier av claim-funksjonen) må kunne LESE registeret for å
--    avgjøre om en registrert oppdragstype har en aktiv, claiming kontrakt.
--    Registertabellene er globale (ingen RLS) → ren SELECT-grant.
-- ------------------------------------------------------------
GRANT SELECT ON oppdragstype_register TO disponit_m37_claimer;
GRANT SELECT ON modulhode             TO disponit_m37_claimer;
GRANT SELECT ON moduldeployment       TO disponit_m37_claimer;

-- ------------------------------------------------------------
-- 3. claim_neste_oppdrag med BETINGET, DEPLOYMENT-BUNDET kontraktbinding.
--    Kalleren oppgir sin egen deployment-identitet (release/miljø/epoch); for en
--    registrert oppdragstype claimes bare når NETTOPP den deploymenten er
--    `claiming` og eiermodulen matcher. `p_lease_s` beholder sin 4. posisjon, de
--    tre nye er valgfrie bakerst → eksisterende 3-/4-args-kall (app + legacy) er
--    uendret. Prosedyren tar modul-låsen DELT (serialiserer mot noddeaktiver_modul
--    og de andre overgangene, som tar den eksklusivt — men ikke mot andre claims)
--    og re-leser modulstatus/deployment UNDER låsen. Eid av disponit_m37_claimer.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_m37_claimer;
-- Utvidet signatur → dropp den gamle 4-args-varianten (005) FØR ny opprettes.
DROP FUNCTION IF EXISTS claim_neste_oppdrag(TEXT, TEXT[], TEXT, INT);
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
               owner_lease_utloper = now() + (v_lease || ' seconds')::INTERVAL,
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

-- ------------------------------------------------------------
-- 4. V3 / port 9: en deployment kan ALDRI slettes (utestående claim-/kvitterings-
--    bindinger). Release var alt slett-vernet av append-only-triggeren (014);
--    deployment hadde bare en UPDATE-statemaskin. Egen slett-vakt (den kan ikke
--    dele append_only, som ville blokkert de lovlige livsløps-UPDATE-ene).
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION moduldeployment_ingen_sletting()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'moduldeployment kan ikke slettes (utestående bindinger, V3): %',
        TG_OP;
END $$;
DROP TRIGGER IF EXISTS deployment_ingen_delete ON moduldeployment;
CREATE TRIGGER deployment_ingen_delete BEFORE DELETE ON moduldeployment
    FOR EACH ROW EXECUTE FUNCTION moduldeployment_ingen_sletting();
DROP TRIGGER IF EXISTS deployment_ingen_truncate ON moduldeployment;
CREATE TRIGGER deployment_ingen_truncate BEFORE TRUNCATE ON moduldeployment
    FOR EACH STATEMENT EXECUTE FUNCTION moduldeployment_ingen_sletting();
