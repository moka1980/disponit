-- ============================================================
-- 028 — E-posten holder språkløftet (Codex P2 på #68)
--
-- 🔴 FUNNET: hele designet lover at varselet leses på MOTTAKERENS språk — det
-- er derfor raden bærer `tekstnokkel` + `parametre` i stedet for ferdig tekst.
-- Men serveren VISSTE ikke mottakerens språk: valget levde bare i nettleserens
-- localStorage, og senderen rendret hele kjøringen med ett globalt språk,
-- norsk som standard. En engelskspråklig godkjenner fikk portalen på engelsk
-- og e-posten på norsk — løftet holdt i den ene kanalen og brutt i den andre.
--
-- Språket lagres derfor der valget om kanalen allerede bor: `varselvalg`, per
-- bruker per tenant. Flaten sender språket sitt når valget lagres (og har en
-- fornuftig kilde: brukeren STÅR i det språket når hun velger). Fraværende
-- rad → 'nb', som er installasjonens standard — samme fallback som flaten.
--
-- `varsel_klaim_epost` returnerer språket per rad, så senderen kan rendre
-- HVER e-post riktig i én og samme kjøring. LEFT JOIN, ikke JOIN: en bruker
-- uten valgrad skal fortsatt få e-post — på standardspråket, ikke ingen.
-- ============================================================

ALTER TABLE varselvalg ADD COLUMN IF NOT EXISTS sprak text NOT NULL
    DEFAULT 'nb' CHECK (sprak IN ('nb', 'en'));

-- Kroppen er DEN GJELDENDE 027-kroppen (klaimtoken, avmeldingsvakt,
-- verifisert adresse, ulest, forsøkstak) + sprak som siste kolonne. Dette er
-- tredje gang en gjenskaping har mistet en senere rettelse på veien — først
-- avmeldingsvakten, så migrator-granten, så klaimtokenet. En gjenskaping
-- arver ingenting av seg selv; den må skrives fra den siste kroppen, ikke fra
-- den man husker.
DROP FUNCTION IF EXISTS varsel_klaim_epost(int, int);

CREATE OR REPLACE FUNCTION varsel_klaim_epost(p_grense int,
                                              p_maks int DEFAULT 3)
RETURNS TABLE (id bigint, tenant text, epost text, tekstnokkel text,
               parametre jsonb, forsok int, klaim uuid, sprak text)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    UPDATE varsel v
       SET epost_status = 'under_sending',
           epost_ts     = now(),
           epost_klaim  = gen_random_uuid(),
           epost_forsok = v.epost_forsok + 1
      FROM brukeridentitet i
     WHERE i.bruker_id = v.bruker_id
       AND v.id IN (SELECT k.id
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
                     FOR UPDATE OF k SKIP LOCKED)
 RETURNING v.id, v.tenant, i.profil->>'epost', v.tekstnokkel, v.parametre,
           v.epost_forsok, v.epost_klaim,
           coalesce((SELECT vv.sprak FROM varselvalg vv
                      WHERE vv.tenant = v.tenant
                        AND vv.bruker_id = v.bruker_id), 'nb');
$$;

-- Eieren må selv kunne lese språkvalget: SECURITY DEFINER kjører som eieren,
-- og 027 ga den bare `varsel` og `brukeridentitet`.
GRANT SELECT ON varselvalg TO disponit_domene_eier;

ALTER FUNCTION varsel_klaim_epost(int, int) OWNER TO disponit_domene_eier;
REVOKE ALL ON FUNCTION varsel_klaim_epost(int, int) FROM PUBLIC;
-- 028 GJENSKAPER funksjonen, og en DROP tar grantene med seg. Uten
-- re-granten her mistet migrator nettopp klaimet — mens de to andre
-- funksjonene beholdt sitt fra 027, og hullet var usynlig til testene
-- kjørte i riktig rekkefølge. Speiler halen av 027.
-- …og som EIEREN: granten står etter ALTER OWNER, så migrator eier ikke
-- lenger funksjonen, og en naken GRANT ville blitt samme stille WARNING som
-- hele denne runden handler om.
SET LOCAL ROLE disponit_domene_eier;
GRANT EXECUTE ON FUNCTION varsel_klaim_epost(int, int) TO disponit_migrator;
GRANT EXECUTE ON FUNCTION varsel_klaim_epost(int, int) TO disponit_varselsender;
RESET ROLE;
