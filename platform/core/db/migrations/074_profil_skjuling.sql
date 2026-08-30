-- 074: slett for stillingsprofiler (eiers bestilling 30/8)
--
-- «Tromsø kk bør man ha alternativ til å slette den også.» Profilene er
-- append-only (061) og skal forbli det: versjonene refereres av
-- rapporter (v2-leseveien supplerer kravene fra nettopp disse radene),
-- så en sletting som fjernet rader ville revet vektene ut under gamle
-- rapporter. Slett betyr det samme som i evalueringslisten (071):
-- RADEN FORSVINNER FRA FLATEN — profilen skjules fra listen og fra
-- Ny bestilling, historikken består.
--
-- Merket er ENVEIS og gjelder hele profilen (alle versjoner): settes én
-- gang, fra NULL, aldri frem i tid, og fjernes eller flyttes aldri —
-- samme regel som 071-armen. Alle andre kolonner er like append-only
-- som før; kravtabellen røres ikke i det hele tatt.

ALTER TABLE stillingsprofil ADD COLUMN skjult_ts TIMESTAMPTZ;

-- UPDATE-vakten byttes fra blank avvisning (061) til «kun skjuling»:
-- nektelsen av alt annet står ordrett, som en kolonneliste denne gangen
-- (061-formen var TG_OP-nektelse uten kolonnesyn).
CREATE OR REPLACE FUNCTION stillingsprofil_skjuling_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.profil_id IS DISTINCT FROM OLD.profil_id
       OR NEW.versjon IS DISTINCT FROM OLD.versjon
       OR NEW.navn IS DISTINCT FROM OLD.navn
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av
       OR NEW.operasjonsnokkel IS DISTINCT FROM OLD.operasjonsnokkel
       OR NEW.innhold_hash IS DISTINCT FROM OLD.innhold_hash THEN
        RAISE EXCEPTION 'stillingsprofil: versjonene er append-only'
            ' (redigering = ny versjon) — kun skjuling kan skrives'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.skjult_ts IS DISTINCT FROM OLD.skjult_ts THEN
        IF OLD.skjult_ts IS NOT NULL THEN
            RAISE EXCEPTION 'stillingsprofil: skjulingen er enveis —'
                ' merket fjernes eller flyttes aldri'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.skjult_ts IS NULL
           OR NEW.skjult_ts > pg_catalog.now() THEN
            RAISE EXCEPTION 'stillingsprofil: skjulingen settes nå,'
                ' aldri frem i tid'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER stillingsprofil_vakt ON stillingsprofil;
CREATE TRIGGER stillingsprofil_vakt
    BEFORE UPDATE ON stillingsprofil
    FOR EACH ROW EXECUTE FUNCTION stillingsprofil_skjuling_vakt();
-- DELETE var dekket av samme trigger i 061 — den nektelsen skal bestå.
CREATE TRIGGER stillingsprofil_ingen_delete
    BEFORE DELETE ON stillingsprofil
    FOR EACH ROW EXECUTE FUNCTION stillingsprofil_append_only();

-- SKJULINGEN ER PROFILENS, IKKE VERSJONENS (CodeRabbit): en rå
-- kolonnegrant ville latt runtime skjule ÉN versjon og etterlate
-- listen i en halvtilstand (og en senere versjon ville «gjenopplivet»
-- profilen). Runtime SPØR derfor døren, som skjuler ALLE versjoner i
-- én setning — samme form som makuleringsdøren (#181): aldri rå
-- UPDATE på tabellen. Vakten over står som forsvar i dybden.
-- Døren eies av migrator og løper som den (SECURITY DEFINER) —
-- kontekstporten må derfor kunne kalles derfra. Porten eies av
-- claimeren (038), så grantet gis som eieren (039-formen).
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_migrator;
RESET ROLE;
CREATE FUNCTION skjul_stillingsprofil(p_tenant TEXT, p_profil_id UUID)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_antall INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'skjul_stillingsprofil');
    UPDATE public.stillingsprofil
       SET skjult_ts = pg_catalog.now()
     WHERE tenant = p_tenant AND profil_id = p_profil_id
       AND skjult_ts IS NULL;
    GET DIAGNOSTICS v_antall = ROW_COUNT;
    RETURN v_antall;
END $$;
REVOKE ALL ON FUNCTION skjul_stillingsprofil(TEXT, UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION skjul_stillingsprofil(TEXT, UUID) TO disponit;
