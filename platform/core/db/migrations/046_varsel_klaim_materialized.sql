-- 046 — varsel_klaim_epost: kandidatvalget i en MATERIALIZED CTE
-- (issue #100; samme klasse som 041 §18).
--
-- 🔴 MØNSTERET: `UPDATE … FROM (SELECT … FOR UPDATE SKIP LOCKED LIMIT n)`
-- er bare korrekt når planleggeren evaluerer subspørringen ÉN gang. I
-- `claim_neste_sak` flippet planformen da adjudikator-policyen kom på
-- `unntak` (041): subspørringen ble INNER side i en nested loop og
-- reskannet per ytre rad; FOR UPDATE-recheck så forrige rad som alt
-- claimet og gikk videre — ÉN claim tømte hele køen (målt: 8/8 rader,
-- samme claim_id). `varsel_klaim_epost` (027, sist gjenskapt i 031)
-- bærer NØYAKTIG samme form, med kaskadevilkåret til stede: klaimet
-- endrer `epost_status`, så en rescan ser klaimede rader falle ut av
-- filteret og fortsette forbi LIMIT-intensjonen. Feilen er LATENT — den
-- er ikke utløst i dag fordi policyflatene på varseltabellene er
-- uendret — men korrektheten hviler på en planform ingen har lovet oss.
-- En LIMIT i en subspørring begrenser per EVALUERING; MATERIALIZED er
-- garantien for nøyaktig én evaluering.
--
-- Kroppen er en KOPI av 031-utgaven, diff-endret: kandidatvalget er
-- flyttet ordrett fra `v.id IN (SELECT …)` til `WITH kandidat AS
-- MATERIALIZED (…)`, joinet på `v.id = kd.id`. Filtre, ordning, grense,
-- RETURNING og språkoppslaget er uendret tegn for tegn.
--
-- DROP-en er lovlig som migrator av samme grunn som i 028/031 (og målt
-- i `test_skjemaeieren_kan_droppe_en_funksjon_den_ikke_eier`): for DROP
-- godtar PostgreSQL eieren av SKJEMAET som alternativ til eieren av
-- objektet, og skjemaet eies av migrator. Etter DROP er CREATE-en en
-- fersk migrator-eid funksjon, og ALTER … OWNER trenger bare
-- medlemskapet (`WITH INHERIT FALSE`).

DROP FUNCTION IF EXISTS varsel_klaim_epost(int, int);

CREATE OR REPLACE FUNCTION varsel_klaim_epost(p_grense int,
                                              p_maks int DEFAULT 3)
RETURNS TABLE (id bigint, tenant text, epost text, tekstnokkel text,
               parametre jsonb, forsok int, klaim uuid, sprak text)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    WITH kandidat AS MATERIALIZED (
        SELECT k.id
          FROM varsel k
          JOIN brukeridentitet b ON b.bruker_id = k.bruker_id
         WHERE k.epost_status = 'koet'
           AND k.lest_ts IS NULL
           AND (b.profil->>'epost') IS NOT NULL
           AND (b.profil->>'epost_verifisert')::boolean IS TRUE
           AND k.epost_forsok < greatest(1, coalesce(p_maks, 3))
           AND NOT EXISTS (SELECT 1 FROM varselvalg vv
                            WHERE vv.tenant = k.tenant
                              AND vv.bruker_id = k.bruker_id
                              AND vv.kanal = 'kun_portal')
         ORDER BY k.opprettet
         LIMIT greatest(1, least(coalesce(p_grense, 50), 500))
           FOR UPDATE OF k SKIP LOCKED
    )
    UPDATE varsel v
       SET epost_status = 'under_sending',
           epost_ts     = now(),
           epost_klaim  = gen_random_uuid(),
           epost_forsok = v.epost_forsok + 1
      FROM brukeridentitet i, kandidat kd
     WHERE i.bruker_id = v.bruker_id
       AND v.id = kd.id
 RETURNING v.id, v.tenant, i.profil->>'epost', v.tekstnokkel, v.parametre,
           v.epost_forsok, v.epost_klaim,
           -- Uten `coalesce`: NULL når raden mangler, og NULL når brukeren
           -- har en rad men aldri uttrykte noe språk. Begge betyr det samme
           -- for senderen, og skal behandles likt.
           (SELECT vv.sprak FROM varselvalg vv
             WHERE vv.tenant = v.tenant
               AND vv.bruker_id = v.bruker_id);
$$;

ALTER FUNCTION varsel_klaim_epost(int, int) OWNER TO disponit_domene_eier;

-- Gjerdet og grantene, satt av eieren selv — speiler halen av 031.
SET LOCAL ROLE disponit_domene_eier;

REVOKE ALL ON FUNCTION varsel_klaim_epost(int, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION varsel_klaim_epost(int, int) TO disponit_migrator;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_varselsender') THEN
        GRANT EXECUTE ON FUNCTION varsel_klaim_epost(int, int)
            TO disponit_varselsender;
    END IF;
END $$;

RESET ROLE;
