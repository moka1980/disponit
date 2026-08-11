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
    -- CP5: kontraktbindingen er write-once. Satt (non-null) → frosset.
    IF OLD.modul_id IS NOT NULL AND (
           NEW.modul_id        IS DISTINCT FROM OLD.modul_id
        OR NEW.kontraktversjon IS DISTINCT FROM OLD.kontraktversjon
        OR NEW.kontrakt_hash   IS DISTINCT FROM OLD.kontrakt_hash) THEN
        RAISE EXCEPTION 'oppdrag: kontraktbindingen er frosset når den er satt';
    END IF;
    -- module_epoch er monoton (fencing-generasjon kan aldri gå bakover).
    IF OLD.module_epoch IS NOT NULL AND NEW.module_epoch IS NOT NULL
       AND NEW.module_epoch < OLD.module_epoch THEN
        RAISE EXCEPTION 'oppdrag: module_epoch kan aldri reduseres';
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

-- ------------------------------------------------------------
-- 2. m37_claimer (eier av claim-funksjonen) må kunne LESE registeret for å
--    avgjøre om en registrert oppdragstype har en aktiv, claiming kontrakt.
--    Registertabellene er globale (ingen RLS) → ren SELECT-grant.
-- ------------------------------------------------------------
GRANT SELECT ON oppdragstype_register TO disponit_m37_claimer;
GRANT SELECT ON modulhode             TO disponit_m37_claimer;
GRANT SELECT ON moduldeployment       TO disponit_m37_claimer;

-- ------------------------------------------------------------
-- 3. claim_neste_oppdrag med BETINGET kontraktbinding. Signaturen og de
--    returnerte kolonnene er UENDRET (app-laget rører ikke) — kontrakt/epoch
--    stemples på raden og leses derfra av kvitteringskapabiliteten.
--    Funksjonen eies av disponit_m37_claimer (SECURITY DEFINER) → REPLACE må
--    kjøres som eieren.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION claim_neste_oppdrag(p_modul_id TEXT, p_prefiks TEXT[],
                                               p_claim_id TEXT,
                                               p_lease_s INT DEFAULT 300)
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
BEGIN
    IF p_claim_id IS NULL OR p_claim_id !~ '^[0-9a-f]{32,}$' THEN
        RAISE EXCEPTION 'claim_neste_oppdrag: ugyldig claim_id-format';
    END IF;
    IF p_modul_id IS NULL OR length(btrim(p_modul_id)) = 0 THEN
        RAISE EXCEPTION 'claim_neste_oppdrag: modul_id mangler';
    END IF;

    RETURN QUERY
    UPDATE public.oppdrag o
       SET status = 'plukket',
           owner_claim_id = p_claim_id,
           owner_generation = o.owner_generation + 1,
           owner_lease_utloper = pg_catalog.now() + (v_lease || ' seconds')::INTERVAL,
           -- CP5: stemple autorisert kontrakt + fencing-epoch (NULL for legacy).
           modul_id = (SELECT r.eiermodul FROM public.oppdragstype_register r
                        WHERE r.oppdragstype = o.oppdragstype),
           kontraktversjon = (SELECT r.kontraktversjon
                                FROM public.oppdragstype_register r
                               WHERE r.oppdragstype = o.oppdragstype),
           kontrakt_hash = (SELECT r.kontrakt_hash
                              FROM public.oppdragstype_register r
                             WHERE r.oppdragstype = o.oppdragstype),
           module_epoch = (SELECT h.module_epoch
                             FROM public.oppdragstype_register r
                             JOIN public.modulhode h ON h.modul_id = r.eiermodul
                            WHERE r.oppdragstype = o.oppdragstype)
     WHERE o.id = (
        SELECT k.id FROM public.oppdrag k
         WHERE (
                 k.status = 'opprettet'
                 OR (k.status = 'plukket'
                     AND k.owner_lease_utloper IS NOT NULL
                     AND k.owner_lease_utloper < pg_catalog.now())
               )
           AND k.eiermodul = p_modul_id
           AND p_prefiks IS NOT NULL
           AND pg_catalog.array_length(p_prefiks, 1) > 0
           AND EXISTS (SELECT 1 FROM pg_catalog.unnest(p_prefiks) AS pre
                        WHERE k.handling LIKE pre || '%')
           AND k.utforelsesfrist > pg_catalog.now()
           -- CP5 BETINGET BINDING: er oppdragstypen registrert, MÅ eiermodulen
           -- være aktiv med en claiming-deployment for den autoriserte
           -- kontrakten. Uregistrert oppdragstype → ingen ekstra betingelse.
           AND (
                 NOT EXISTS (SELECT 1 FROM public.oppdragstype_register r
                              WHERE r.oppdragstype = k.oppdragstype)
                 OR EXISTS (
                     SELECT 1 FROM public.oppdragstype_register r
                       JOIN public.modulhode h
                         ON h.modul_id = r.eiermodul AND h.status = 'aktiv'
                       JOIN public.moduldeployment d
                         ON d.modul_id = r.eiermodul
                        AND d.kontraktversjon = r.kontraktversjon
                        AND d.kontrakt_hash = r.kontrakt_hash
                        AND d.livslop = 'claiming'
                      WHERE r.oppdragstype = k.oppdragstype)
               )
         ORDER BY k.opprettet, k.id
           FOR UPDATE SKIP LOCKED
         LIMIT 1)
    RETURNING o.id, o.tenant, o.unntak_id, o.oppdragstype, o.handling,
              o.repair_operation_id, o.payload_kryptert, o.key_id, o.nonce,
              o.owner_generation, o.utforelsesfrist, o.evidensfrist;
END $$;
REVOKE ALL ON FUNCTION claim_neste_oppdrag(TEXT, TEXT[], TEXT, INT) FROM PUBLIC;
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
