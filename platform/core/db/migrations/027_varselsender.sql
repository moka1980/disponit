-- ============================================================
-- 027 — Senderens KRYSS-TENANT vindu, og bare det
--
-- E-postsenderen er ett drifts-oneshot for hele installasjonen: den kan ikke
-- vite hvilke tenanter som har noe i kø uten å se på tvers av dem. Men RLS er
-- FORCE på `varsel`, og å gi senderen BYPASSRLS ville gitt den hele tabellen
-- for alle kunder — for å lese tre felter.
--
-- Samme løsning som PR-014b brukte for takeover: en SECURITY DEFINER-funksjon
-- eid av en BYPASSRLS-rolle. Kryss-tenant-evnen ligger da INNE i en smal,
-- lesbar funksjon i stedet for i en rolle noen kan logge inn med, og
-- funksjonen returnerer nøyaktig det senderen trenger:
--
--   * bare rader som står i kø — aldri leste, aldri sendte;
--   * bare til VERIFISERTE adresser. En uverifisert e-post i profilen er en
--     påstand fra en IdP, ikke et bevis, og et varsel om en fullmaktsrunde
--     skal ikke sendes til en adresse ingen har bekreftet;
--   * ingen `lest_ts`, ingen historikk, ingen andre tenanters tilstand.
--
-- Teksten sendes IKKE herfra: funksjonen gir `tekstnokkel` + `parametre`, og
-- senderen rendrer. Databasen skal ikke kunne noe språk.
--
-- Og den rendrer i INSTALLASJONENS språk, ikke mottakerens: funksjonen
-- returnerer ingen språkpreferanse fordi ingen finnes å returnere —
-- portalens språkvalg lever i nettleseren. Det står her fordi det er her
-- feltlista avgjøres: skal e-posten en dag følge mottakeren, er det denne
-- returtypen som må bære språket.
--
-- Oppdateringen av status går gjennom sin egen funksjon, så senderen aldri får
-- generell UPDATE på tvers av tenanter: den kan flytte en rad fra sitt eget
-- klaim til `sendt`/`feilet`, og ingenting annet.
--
-- OG EVNEN LIGGER HOS SENDEREN ALENE (eiers P1). Funksjonene er smale, men de
-- er fortsatt en kryss-tenant-kapabilitet, og hvem som får KALLE dem er
-- derfor like mye av vernet som hva de returnerer. EXECUTE gis kun til
-- `disponit_varselsender` — en egen LOGIN-rolle uten en eneste
-- tabellrettighet, med sin egen DSN i sin egen credential — aldri til
-- runtime-rollen `disponit` som betjener forespørsler. Se GRANT-blokken
-- nederst i filen.
--
-- PLUKKET ER ET KLAIM, IKKE EN LESNING (Codex P1).
--
-- Første utgave var en ren `SELECT` av `koet`-rader, og statusen ble flyttet
-- FØRST ETTER at `send()` hadde utført SMTP-kallet. To sendere som overlapper
-- — og de kan overlappe: timeren er hvert 5. minutt, og en treg SMTP-server
-- gjør en kjøring lengre enn intervallet — hentet da samme rad, sendte begge
-- e-posten, og konkurrerte om statusen etterpå. At den andre `UPDATE`-en
-- traff null rader er en opplysning som kommer for sent: SMTP er utført, og
-- en sendt e-post kan ingen `ROLLBACK` hente hjem igjen.
--
-- Rekkefølgen er derfor snudd: `varsel_klaim_epost` FLYTTER raden ut av køen
-- (`koet` → `under_sending`) i den samme setningen som leser den, med
-- `FOR UPDATE … SKIP LOCKED` så to samtidige klaim aldri ser hverandres
-- rader. Senderen committer klaimet før den rører SMTP. Etter det finnes
-- raden ikke i noen annen senders kø.
-- ============================================================

-- EIERSKAPET gjenbrukes: `disponit_domene_eier` er allerede rollen som eier
-- husets kryss-tenant SECURITY DEFINER-funksjoner (PR-014b takeover), og har
-- BYPASSRLS nettopp for det. En egen `varsel_eier` ville vært marginalt
-- klarere å lese, men roller opprettes av `oppsett-postgresql.sh` — ikke av
-- migrasjoner, som ikke har CREATEROLE — og ville dessuten måttet speiles i
-- ci.yml og i det lokale testoppsettet, som har en driftvakt mot nettopp
-- slike avvik. Én rolle for «funksjoner som med vilje ser på tvers av
-- tenanter» er en forsvarlig grense; å legge til en rolle til for å slippe å
-- forklare den er det ikke.
-- DROP først: funksjonene eies av  etter forrige
-- kjøring, og da kan ikke migratoren REPLACE dem. Uten dette er migrasjonen
-- ikke re-kjørbar — noe man først oppdager når man prøver.
DROP FUNCTION IF EXISTS varselkandidater(int);
DROP FUNCTION IF EXISTS varsel_klaim_epost(int, int);
DROP FUNCTION IF EXISTS varsel_sett_epoststatus(bigint, text, text);
DROP FUNCTION IF EXISTS varsel_sett_epoststatus(bigint, uuid, text, text);
DROP FUNCTION IF EXISTS varsel_rekoe_feilede(interval, int);
DROP FUNCTION IF EXISTS varsel_rekoe(interval, int, interval);

-- KLAIMET. Én setning som både leser raden og tar den ut av køen.
--
-- `FOR UPDATE OF k SKIP LOCKED` er det som gjør to samtidige kjøringer trygge
-- OGSÅ i vinduet der det første klaimet ennå ikke er committet: den andre
-- senderen hopper over raden i stedet for å vente på den. Ventet den, ville
-- den fått raden servert med `under_sending` og sett den forsvinne fra sitt
-- eget filter — riktig, men først etter en unødig blokkering på en jobb som
-- kjører hvert 5. minutt.
--
-- FORSØKSTELLEREN ØKES HER, ved klaimet — ikke ved resultatet. Et forsøk er
-- noe som er PÅBEGYNT: teller vi først når resultatet kommer, teller vi aldri
-- de forsøkene som døde underveis, og taket ville ikke holdt mot nettopp den
-- feilen som gjentar seg. Derfor er `p_maks` også med i dette filteret: en rad
-- som har brukt opp forsøkene sine er ikke i køen lenger, uansett hvilken vei
-- den kom dit.
--
-- ET LEST VARSEL ER IKKE I KØ (Codex P2). `lest_ts IS NULL` står i klaimets
-- eget filter, ikke bare i de veiene som avlyser en sending. Modulteksten over
-- lovte «aldri leste», men ingenting håndhevet det: `merk_lest` rørte bare
-- `lest_ts`, og raden ble stående `koet` — så mottakeren som leste varselet i
-- portalen fikk e-posten om det noen minutter senere likevel. Avlysningen
-- ligger nå i `merk_lest`, men den kan ikke være det eneste vernet: den når
-- ikke en rad som er `under_sending`, og en rad som kommer tilbake fra en
-- lease etter at den ble lest, ville ellers gått rett ut. Klaimet er den siste
-- porten før SMTP, og derfor den som må stille spørsmålet.
--
-- OG EN AVMELDING GJELDER OGSÅ HER (Codex P2). Samme resonnement, ett hakk
-- videre: `sett_kanal` avlyser hele køen (`I_KO`), men lar med vilje en rad som
-- er `under_sending` stå — den er i et SMTP-kall, og en e-post som er ute kan
-- ikke kalles hjem. Dør senderen FØR sendingen, kom den raden tilbake til
-- `koet` gjennom leasen, og avmeldingen som ble committet i mellomtiden fantes
-- det da ingen som spurte om. Klaimet spør nå: fravær av rad er standarden
-- (`epost_og_portal`), så `NOT EXISTS … kun_portal` er hele testen.
--
-- KLAIMET HAR EN IDENTITET, IKKE BARE EN TILSTAND (Codex P2). `under_sending`
-- sier at raden er tatt; tokenet sier av HVEM. Uten det var fullføringen
-- gjerdet på `id` + status alene, og leasen gjorde det gjerdet for grovt:
-- sender A pauses forbi leasen, re-køingen løfter raden tilbake, sender B
-- klaimer og begynner å sende — og når A våkner, står raden `under_sending`
-- igjen, så A sin fullføring traff. Da skrev A `sendt` over B sitt LEVENDE
-- klaim, B sin egen oppdatering fant ingenting, og resultatet av den sendingen
-- som faktisk pågikk hadde ingen plass å bli skrevet. Tokenet er ferskt for
-- hvert klaim, så et utløpt klaim kan ikke fullføre erstatteren sin.
--
-- Ordningen er FIFO på `opprettet` — den eldste ventende varsles først, som i
-- innboksen.
CREATE OR REPLACE FUNCTION varsel_klaim_epost(p_grense int,
                                              p_maks int DEFAULT 3)
RETURNS TABLE (id bigint, tenant text, epost text, tekstnokkel text,
               parametre jsonb, forsok int, klaim uuid)
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
           v.epost_forsok, v.epost_klaim;
$$;

-- `sendt` er terminalt. `feilet` beholder forsøkstelleren klaimet satte, så en
-- adresse som aldri tar imot ikke prøves i evighet — men raden BLIR STÅENDE i
-- portalen: innboksen er sannheten, e-posten er kopien.
CREATE OR REPLACE FUNCTION varsel_sett_epoststatus(
    p_id bigint, p_klaim uuid, p_status text, p_feil text DEFAULT NULL)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE n int;
BEGIN
    IF p_status NOT IN ('sendt', 'feilet') THEN
        RAISE EXCEPTION 'varsel_sett_epoststatus: ulovlig status %', p_status
            USING ERRCODE = 'check_violation';
    END IF;
    -- Bare fra `under_sending`, altså bare for den som FAKTISK har klaimet
    -- raden. En rad i `koet` kan ikke settes `sendt` uten å ha vært gjennom
    -- klaimet, og en rad som alt er `sendt` kan ikke settes om.
    --
    -- OG BARE FOR SITT EGET KLAIM (Codex P2). Statusen alene skiller ikke to
    -- klaim fra hverandre, og etter en lease-gjenopptaking er det nettopp to
    -- den må skille: den døde og den levende. Tokenet er det klaimet skrev og
    -- returnerte, så en sender som våkner etter leasen finner ikke sitt eget
    -- klaim igjen — den får `false`, teller `mistet`, og sier fra. Uten
    -- tokenet skrev den i stedet over den andres sending, som er den samme
    -- feilen én gang til: en e-post er ikke noe en tapt UPDATE kan hente hjem.
    --
    -- `p_klaim` har ingen DEFAULT med vilje: en kaller som ikke har et token
    -- har heller ikke et klaim, og skal stoppes ved signaturen. Og
    -- sammenligningen er `=`, ikke `IS NOT DISTINCT FROM`: NULL matcher da
    -- ingenting, så en rad som på et vis står `under_sending` uten token
    -- kan ikke fullføres av en kaller som heller ikke har et. Feiler den
    -- veien, feiler den lukket.
    --
    -- Tokenet NULLES ved fullføringen: raden er ikke lenger klaimet av noen.
    -- En rad som senere rekøes fra `feilet` får sitt eget, ferske token av
    -- klaimet som tar den.
    --
    -- Telleren økes IKKE her: det gjorde klaimet. Ble den økt begge steder,
    -- ville et forsøk kostet to, og `MAKS_FORSOK` betydd halvparten av det
    -- den sier.
    UPDATE varsel
       SET epost_status = p_status,
           epost_ts     = now(),
           epost_klaim  = NULL,
           epost_feil   = left(p_feil, 500)
     WHERE id = p_id AND epost_status = 'under_sending'
       AND epost_klaim = p_klaim;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n = 1;
END $$;

-- SECURITY DEFINER kjører som EIEREN, så eieren må selv ha lesetilgangen —
-- ellers feiler funksjonen med InsufficientPrivilege uansett hvem som kaller
-- den. Rettighetene er minimale og med vilje asymmetriske: LES på de to
-- tabellene funksjonen slår opp i, SKRIV bare på statusfeltene i .
GRANT SELECT ON varsel, brukeridentitet, varselvalg TO disponit_domene_eier;
GRANT UPDATE ON varsel TO disponit_domene_eier;

-- RE-KØING: en rad som ikke kom frem er ikke ferdig — men den er heller ikke
-- i noens kø før den står i `koet`.
--
-- To kilder, samme svar:
--
--   `feilet` etter BACKOFF. Uten dette var `feilet` en blindvei: klaimet tar
--   bare `koet`, så en rad som feilet én gang ble aldri forsøkt igjen, og
--   forsøkstelleren var død kode. Ett forbigående SMTP-hikk mistet e-posten
--   for godt. Backoff og ikke umiddelbar retry: en adresse som nettopp
--   avviste, avviser sannsynligvis igjen.
--
--   `under_sending` etter LEASE — gjenopptakingen etter krasj. Klaimet er
--   committet før SMTP nettopp for at ingen andre skal ta raden; dør prosessen
--   der (OOM, `systemctl stop` i vedlikeholdsvinduet, verten som forsvinner),
--   blir raden stående klaimet av en prosess som ikke finnes. Uten en lease
--   ville den blitt liggende der for alltid, i en tilstand ingen ser på.
--
-- LEASEN ER MYE LENGRE ENN SMTP-TIMEOUTEN (30 min mot 20 s), og det er et
-- bevisst valg: den skal aldri kunne løpe ut for en sending som fortsatt
-- pågår — det ville gjenskapt nøyaktig den dobbeltsendingen klaimet finnes
-- for å hindre. Prisen er den motsatte: krasjer prosessen i det smale vinduet
-- MELLOM et vellykket SMTP-kall og statusoppdateringen, kommer raden tilbake
-- og kopien kan bli sendt én gang til. Det er den ærlige avveiningen — for
-- alternativet, å la den stå klaimet for godt, er en stille tilstand ingen
-- overvåker, og et varsel som aldri når frem er verre enn et som når frem to
-- ganger. Forsøkstaket gjelder uansett vei, så heller ikke en krasjsløyfe kan
-- banke på i evighet.
--
-- Re-køingen er et EGET steg, ikke en utvidelse av klaimet: `koet` er
-- fortsatt den eneste tilstanden et klaim kan ta fra.
--
-- EN GJENOPPTATT RAD ARVER IKKE EN AVMELDING (Codex P2). Meldte mottakeren
-- seg av mens raden sto `under_sending`, lot `sett_kanal` det klaimet stå med
-- vilje — raden var i et SMTP-kall. Døde senderen før sendingen, løftet
-- leasen den blindt tilbake til `koet`, og avmeldingen var borte: den hadde
-- alt kjørt, og ingen kjører den igjen. Raden går derfor til `ikke_aktuelt`
-- når valget nå er `kun_portal`, altså dit `sett_kanal` ville sendt den om
-- den hadde vært synlig for den.
--
-- Klaimet spør om det samme, og det er ikke overflødig: de to svarer på hver
-- sin vei inn. Denne stenger døra for de radene RE-KØINGEN slipper gjennom;
-- klaimet er den siste porten før SMTP og gjelder også en rad som havnet i
-- `koet` på en måte ingen har tenkt på ennå. En avmelding som virker bare når
-- man kommer inn den ene veien, er ikke en avmelding.
--
-- Returverdien teller bare det som faktisk kom I KØ igjen. En rad som ble
-- avlyst er ikke gjenkøet, og `gjenkoet=1` i journalen for en e-post som
-- aldri skal sendes ville vært en tallmessig løgn i den ene linjen driften
-- faktisk leser.
CREATE OR REPLACE FUNCTION varsel_rekoe(
    p_backoff interval DEFAULT interval '15 minutes',
    p_maks int DEFAULT 3,
    p_lease interval DEFAULT interval '30 minutes')
RETURNS int
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE n int;
BEGIN
    WITH gjenopptatt AS (
        UPDATE varsel v
           SET epost_status = CASE
                   WHEN EXISTS (SELECT 1 FROM varselvalg vv
                                 WHERE vv.tenant = v.tenant
                                   AND vv.bruker_id = v.bruker_id
                                   AND vv.kanal = 'kun_portal')
                   THEN 'ikke_aktuelt' ELSE 'koet' END,
               -- Klaimet er dødt i det raden forlater `under_sending`, og
               -- tokenet dør med det. Det er ikke bare opprydding: fra dette
               -- øyeblikket kan senderen som holdt det ikke fullføre raden,
               -- heller ikke i vinduet FØR noen andre har klaimet den på
               -- nytt. Tilstanden sier da sannheten — ingen holder denne.
               epost_klaim = NULL
         WHERE v.epost_forsok < greatest(1, p_maks)
           AND v.epost_ts IS NOT NULL
           AND ((v.epost_status = 'feilet'
                 AND v.epost_ts < now() - p_backoff)
             OR (v.epost_status = 'under_sending'
                 AND v.epost_ts < now()
                     - greatest(p_lease, interval '5 minutes')))
        RETURNING v.epost_status AS ny_status)
    SELECT count(*) FILTER (WHERE ny_status = 'koet')
      INTO n FROM gjenopptatt;
    RETURN n;
END $$;

ALTER FUNCTION varsel_rekoe(interval, int, interval)
    OWNER TO disponit_domene_eier;
ALTER FUNCTION varsel_klaim_epost(int, int) OWNER TO disponit_domene_eier;
ALTER FUNCTION varsel_sett_epoststatus(bigint, uuid, text, text)
    OWNER TO disponit_domene_eier;

-- ------------------------------------------------------------
-- GJERDET, SATT AV EIEREN SELV.
--
-- `SET LOCAL ROLE` er ikke pynt her, og 019 lærte det på den harde måten:
-- kjøres `REVOKE … FROM PUBLIC` av en rolle som IKKE eier funksjonen,
-- ADVARER PostgreSQL og går videre — men materialiserer samtidig standard-
-- ACL-en, som for en funksjon er EXECUTE for PUBLIC. Migrator eier ikke
-- disse tre etter `ALTER … OWNER TO` over, og er dessuten medlem av
-- eierrollen `WITH INHERIT FALSE`. Uten rolleskiftet var default-deny-en
-- altså ikke bare uinnført, den var snudd: HVER rolle i klyngen kunne kalle
-- `varsel_klaim_epost` og lese alle tenanters mottakeradresser. Og ingen
-- test ville sett det, fordi et privilegium PUBLIC allerede har aldri
-- feiler — derfor måler `test_varselsender` gjerdet på ACL-en, ikke på at
-- et kall lykkes.
--
-- Medlemskapet gir fortsatt SET ROLE (en eksplisitt, sporbar handling, ikke
-- stille arv), og `SET LOCAL` varer bare ut kjørerens transaksjon.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_domene_eier;

REVOKE ALL ON FUNCTION varsel_rekoe(interval, int, interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION varsel_klaim_epost(int, int) FROM PUBLIC;
REVOKE ALL ON FUNCTION varsel_sett_epoststatus(bigint, uuid, text, text)
    FROM PUBLIC;

-- …OG SÅ TIL DEN ROLLEN SOM FAKTISK KALLER DEM — OG BARE DEN.
--
-- EXECUTE er PUBLIC som standard i PostgreSQL, så `REVOKE … FROM PUBLIC` over
-- er det som gjør funksjonene utilgjengelige. Uten et tilsvarende GRANT kunne
-- senderen ikke kalle en eneste av dem, og hver eneste timerkjøring endt i
-- «permission denied for function varsel_klaim_epost», med køen urørt.
--
-- MEN IKKE TIL `disponit` (eiers P1). Første utgave grantet alle tre til
-- runtime-rollen, fordi unitten koblet med runtime-DSN-en. Da fulgte evnen
-- med til hele web-API-prosessen: `varsel_klaim_epost` er med vilje
-- SECURITY DEFINER og kryss-tenant, og returnerer tenant, VERIFISERT
-- e-postadresse, tekstnøkkel og parametre for alle kunders køede varsler.
-- RLS verner ikke mot den — omgåelsen er nettopp funksjonens formål — så en
-- kompromittert forespørselsvei kunne lest ut mottakerlisten for hele
-- installasjonen og tømt køen. Det bryter husets rollemodell (egne DB-roller
-- for M-37, tokenadmin, egress og domenejobbene), og for en evne bare ett
-- oneshot trenger.
--
-- Senderen har derfor sin EGEN rolle med sin EGEN DSN
-- (`oppsett-postgresql.sh`: `$VARSELSENDER` + `DISPONIT_VARSEL_URL`;
-- `opp.sh`: `skriv_cred varsel DISPONIT_DATABASE_URL "$DISPONIT_VARSEL_URL"`).
-- Den rollen eier ingenting og har ingen tabellrettigheter — de tre
-- funksjonene er alt den kan gjøre i denne basen.
--
-- Betinget på EXISTS, av samme grunn som 019s grants til `disponit_domener`:
-- roller er KLYNGEobjekter og opprettes av `oppsett-postgresql.sh`, aldri i
-- en migrasjon (migrator har ikke CREATEROLE og skal ikke ha det).
-- CI-arbeidsflyten speiler normalt rolleoppsettet, men `.github/workflows/`
-- kan ikke endres herfra; en bar GRANT ville derfor feilet migrasjonen i CI
-- med «role does not exist». Staging får grantene, og en senere
-- CI-oppdatering som legger til rollen får dem også — uten en ny migrasjon.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_varselsender') THEN
        GRANT EXECUTE ON FUNCTION varsel_klaim_epost(int, int)
            TO disponit_varselsender;
        GRANT EXECUTE ON FUNCTION
            varsel_sett_epoststatus(bigint, uuid, text, text)
            TO disponit_varselsender;
        GRANT EXECUTE ON FUNCTION varsel_rekoe(interval, int, interval)
            TO disponit_varselsender;
    END IF;
END $$;

-- MIGRATOR FÅR DEM OGSÅ, og det er ikke en oppmykning av grensen over.
-- Migrator eier skjemaet og er MEDLEM av `disponit_domene_eier` (kreves for
-- `ALTER … OWNER TO` lenger oppe). Medlemskapet er `WITH INHERIT FALSE`, så
-- rettigheten arves ikke — men `SET ROLE` står åpen, som her i denne filen.
-- En GRANT gir den altså ingenting den ikke allerede kan ta; den fjerner et
-- `SET ROLE` fra testriggen, og ingenting annet. Samme form og samme
-- begrunnelse som 019 bruker for `degrader_forbigatte_utfordrere`.
--
-- Det er grunnen til at den står her og ikke i P1-ens forbudte selskap:
-- funnet handlet om `disponit`, rollen som betjener HTTP-forespørsler. En
-- kompromittert forespørselsvei er en trussel; en kompromittert migrator er
-- allerede skjemaeier og trenger ingen snarvei.
--
-- Og uten den kunne modulens tester ikke måle senderen i det hele tatt:
-- senderrollen er et klyngeobjekt som ikke finnes i CI, og testene bygger
-- tilstanden sin på migrator-forbindelsen. Det var ikke synlig før, fordi
-- `REVOKE … FROM PUBLIC` kjørte som en ikke-eier og MISLYKTES STILLE — testene
-- kalte funksjonene som PUBLIC. At de nå feiler uten denne linjen er selve
-- beviset på at gjerdet står.
GRANT EXECUTE ON FUNCTION varsel_klaim_epost(int, int) TO disponit_migrator;
GRANT EXECUTE ON FUNCTION varsel_sett_epoststatus(bigint, uuid, text, text)
    TO disponit_migrator;
GRANT EXECUTE ON FUNCTION varsel_rekoe(interval, int, interval)
    TO disponit_migrator;

-- Og hadde en TIDLIGERE kjøring av denne migrasjonen gitt runtime-rollen
-- EXECUTE, trekkes det tilbake her. Migrasjonen er idempotent, og en grant
-- som var feil skal ikke overleve fordi den ble gitt før porten fantes.
REVOKE ALL ON FUNCTION varsel_klaim_epost(int, int) FROM disponit;
REVOKE ALL ON FUNCTION varsel_sett_epoststatus(bigint, uuid, text, text)
    FROM disponit;
REVOKE ALL ON FUNCTION varsel_rekoe(interval, int, interval) FROM disponit;

RESET ROLE;

-- Schema-USAGE kan bare gis av skjemaeieren (migrator), derfor etter
-- RESET ROLE. Uten den er EXECUTE på funksjonene verdiløs: oppslaget av
-- navnet skjer i skjemaet.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_varselsender') THEN
        GRANT USAGE ON SCHEMA public TO disponit_varselsender;
    END IF;
END $$;
