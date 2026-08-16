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
-- DE 'nb'-ENE SOM ALT ER SKREVET, NULLES (Codex P2 på #71). Første utgave av
-- dette avsnittet sa at ingen data trengte å skrives om, og begrunnet det med
-- at ingen `varselvalg`-rad er eldre enn kolonnen — 026–029 ruller ut sammen,
-- så `ALTER`-ens DEFAULT stemplet aldri noen eksisterende rad. Det stemmer,
-- og det dekker bare halvparten: rader skrevet ETTER 028 gikk gjennom
-- `sett_kanal`, som skrev `coalesce(%s,'nb')` ved INSERT. Endepunktet tillater
-- uttrykkelig at språket utelates (`kropp.get("sprak")`), så en klient som
-- ikke sendte noe fikk raden sin stemplet 'nb' — «ikke uttrykt», lagret som et
-- valg. På en `DISPONIT_VARSEL_SPRAK=en`-installasjon ville nettopp de
-- brukerne fortsatt fått norsk e-post etter 031, som er funnet 031 finnes for.
--
-- DE TO TILFELLENE KAN IKKE SKILLES I DATAEN. «Valgte norsk» og «sa ingenting»
-- ble skrevet med samme byte, fordi kolonnen FØR denne migrasjonen ikke hadde
-- noen verdi for «vet ikke». Da er ingen lagret 'nb' et troverdig vitne om et
-- valg, og den ærlige lesningen av en kolonne hvis betydning endres her, er at
-- den historiske 'nb'-en er ukjent — ikke uttrykt.
--
-- KOSTNADEN, skrevet ned med vilje: en bruker som FAKTISK valgte norsk på en
-- engelsk installasjon havner tilbake på installasjonens språk. På en
-- `nb`-installasjon — standarden — er nullingen ikke observerbar i det hele
-- tatt, og på en `en`-installasjon er den akkurat den rettingen funnet ber om,
-- minus den lille gruppen. Valget er selvreparerende: neste gang hun rører
-- kanalvalget, lagres språket hennes igjen — og fra og med 031 er en lagret
-- 'nb' et EKTE uttrykk som aldri nulles på nytt. Retningen på tvilen er den
-- samme som ellers i dette funnet: et fravær skal ikke få gå for et valg.
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

-- Engangsnullingen av de historiske 'nb'-ene, jf. avsnittet over. Den må stå
-- ETTER `DROP NOT NULL` — før den ville hver rad brutt begrensningen — og den
-- er nødvendigvis grovkornet, fordi skillet den skulle brukt ikke ble lagret.
-- 'en' røres ikke: den verdien kunne bare komme fra en klient som uttrykkelig
-- sendte den, og er derfor et valg uansett hvilken vei tvilen faller.
--
-- RLS-VINDUET, nøyaktig som 029: `varselvalg` står med `FORCE ROW LEVEL
-- SECURITY` (026), og politikken `tenant_isolasjon` sammenligner mot
-- `current_setting('disponit.tenant')` — som er uset under migrering. Uten
-- `NO FORCE` ville UPDATE-en truffet NULL RADER og migrasjonen gått grønn uten
-- å ha gjort noe, som er den verste utgangen: funnet ville stått som lukket.
-- `NO FORCE` unntar bare tabelleieren (migrator, som opprettet tabellen i
-- 026), og `ALTER TABLE` holder ACCESS EXCLUSIVE, så ingen annen sesjon leser
-- i vinduet. Vanlige roller er urørt hele veien.
ALTER TABLE varselvalg NO FORCE ROW LEVEL SECURITY;
UPDATE varselvalg SET sprak = NULL WHERE sprak = 'nb';
ALTER TABLE varselvalg FORCE ROW LEVEL SECURITY;

-- Kroppen er DEN GJELDENDE 028-kroppen, ordrett, med ÉN endring: `coalesce`
-- rundt språkoppslaget er borte. Samme regel som 028 selv skrev ned — en
-- gjenskaping arver ingenting av seg selv og må skrives fra den siste
-- kroppen, ikke fra den man husker.
--
-- HVORFOR DROP-EN ER LOVLIG SOM MIGRATOR — funksjonen eies av
-- `disponit_domene_eier` siden 027, og migrator er medlem `WITH INHERIT
-- FALSE`, altså IKKE eier i PostgreSQLs forstand. Likevel går DROP-en
-- igjennom: for DROP godtar PostgreSQL eieren av SKJEMAET som alternativ til
-- eieren av objektet, og både CI (`.github/workflows/ci.yml`) og staging
-- (`deploy/staging/oppsett-postgresql.sh`) gjør `ALTER SCHEMA public OWNER TO
-- disponit_migrator` før første migrasjon. Etter DROP-en er CREATE-en en
-- FERSK funksjon eid av migrator, og `ALTER … OWNER TO` under trenger bare
-- medlemskapet — som er nettopp det `WITH INHERIT FALSE` beholder.
--
-- Rekkefølgen er derfor den samme som 028 bruker, med vilje. Et
-- `SET LOCAL ROLE` rundt DROP/CREATE her ville virket, men bare gjort 031
-- ulik 027 og 028 — som er kjørt og immutable — og latt neste leser tro at
-- de to er en annen sak. Det som faktisk bærer, er skjemaeierskapet over;
-- flyttes DET, faller 028 først, ikke denne.
--
-- OG DET STÅR IKKE LENGER BARE HER (Codex P1 på #71, andre runde). En
-- påstand om hva basen gjør hører hjemme i en måling, ikke i en kommentar:
-- `test_skjemaeieren_kan_droppe_en_funksjon_den_ikke_eier` i
-- `platform/core/tests/test_varselsender.py` kjører nøyaktig denne
-- setningen som migrator mot den migrerte basen, asserterer forutsetningene
-- den hviler på (funksjonen eid av domeneeieren, skjemaet av migrator,
-- medlemskap uten arv), og har motprøven: flyttes skjemaeierskapet, avviser
-- PostgreSQL den samme setningen. Holder ikke påstanden, blir CI rød her —
-- i stedet for at en migrasjon stopper halvveis i drift.
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
