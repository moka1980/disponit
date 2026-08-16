-- ============================================================
-- 031 — «Ikke uttrykt» er ikke det samme som «norsk» (Codex P2 på #68)
--
-- 🔴 FUNNET: `DISPONIT_VARSEL_SPRAK` er dokumentert som installasjonens
-- språkvalg, og senderen har fortsatt fallbacken `(sprak or SPRAK)` for en
-- rad uten språk. Men 028 lot ingen rad komme dit: klaimet avslutter med
-- `coalesce(…, 'nb')`, og `varselvalg.sprak` er `NOT NULL DEFAULT 'nb'`.
-- Senderen fikk altså alltid en GYLDIG verdi, tok den for et valg, og brukte
-- den. På en installasjon satt opp med `DISPONIT_VARSEL_SPRAK=en` fikk hver
-- mottaker uten eget valg e-posten på norsk — innstillingen var virkningsløs
-- for nettopp den gruppen den fantes for.
--
-- ROTEN er at 'nb' ble brukt som stedfortreder for «vet ikke», to steder:
-- i kolonnestandarden og i klaimets `coalesce`. Da finnes det ingen verdi
-- igjen som betyr «denne brukeren har ikke uttrykt noe språk», og et lag som
-- ikke kan uttrykke uvisshet, gjetter i stedet — her på vegne av driften.
--
-- 031 gjør uvissheten representerbar: `sprak` blir NULL-bar uten standard,
-- og klaimet returnerer raden slik den er. NULL betyr «ikke uttrykt», og da
-- er det senderens `(sprak or SPRAK)` som avgjør — altså driftens valg, som
-- dokumentasjonen alltid har lovet. En bruker som HAR uttrykt et språk får
-- fortsatt sitt, uendret.
--
-- INGEN DATA SKRIVES OM. En lagret 'nb' regnes som uttrykt og gjettes ikke
-- på nytt. Det er heller ikke nødvendig: kolonnen kom i 028, og 026–029
-- ruller ut sammen, så det finnes ingen base der `varselvalg`-rader er eldre
-- enn kolonnen og ble stemplet 'nb' av selve ALTER-en.
--
-- GJERDET: denne filen gjenskaper `varsel_klaim_epost`, og en DROP tar ACL-en
-- med seg. REVOKE-en står derfor inne i `SET LOCAL ROLE` — det var akkurat
-- den plasseringen 028 bommet på (Codex P1, reparert i 030).
-- ============================================================

-- NULL = ikke uttrykt. CHECK-en slipper NULL igjennom av seg selv (en
-- CHECK som evaluerer til NULL er oppfylt), så begrensningen på 'nb'/'en'
-- for de uttrykte verdiene står urørt.
ALTER TABLE varselvalg ALTER COLUMN sprak DROP DEFAULT;
ALTER TABLE varselvalg ALTER COLUMN sprak DROP NOT NULL;

-- Kroppen er DEN GJELDENDE 028-kroppen, ordrett, med ÉN endring: `coalesce`
-- rundt språkoppslaget er borte. Samme regel som 028 selv skrev ned — en
-- gjenskaping arver ingenting av seg selv og må skrives fra den siste
-- kroppen, ikke fra den man husker.
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
           -- Uten `coalesce`: NULL når raden mangler, og NULL når brukeren
           -- har en rad men aldri uttrykte noe språk. Begge betyr det samme
           -- for senderen, og skal behandles likt.
           (SELECT vv.sprak FROM varselvalg vv
             WHERE vv.tenant = v.tenant
               AND vv.bruker_id = v.bruker_id);
$$;

ALTER FUNCTION varsel_klaim_epost(int, int) OWNER TO disponit_domene_eier;

-- GJERDET OG GRANTENE, SATT AV EIEREN SELV — begge deler, i denne
-- rekkefølgen, inne i rolleskiftet. Speiler halen av 027, og er nøyaktig det
-- 028 ikke gjorde for REVOKE-en.
SET LOCAL ROLE disponit_domene_eier;

REVOKE ALL ON FUNCTION varsel_klaim_epost(int, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION varsel_klaim_epost(int, int) TO disponit_migrator;

-- Betinget på EXISTS, av samme grunn som i 027: roller er KLYNGEobjekter og
-- opprettes av `oppsett-postgresql.sh`, aldri i en migrasjon. En bar GRANT
-- ville feilet migrasjonen i en base der rollen ikke er satt opp.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_varselsender') THEN
        GRANT EXECUTE ON FUNCTION varsel_klaim_epost(int, int)
            TO disponit_varselsender;
    END IF;
END $$;

RESET ROLE;
