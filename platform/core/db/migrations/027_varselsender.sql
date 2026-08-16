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
-- senderen rendrer på mottakerens språk. Databasen skal ikke kunne noe språk.
--
-- Oppdateringen av status går gjennom sin egen funksjon, så senderen aldri får
-- generell UPDATE på tvers av tenanter: den kan flytte en rad fra sitt eget
-- klaim til `sendt`/`feilet`, og ingenting annet.
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
-- Ordningen er FIFO på `opprettet` — den eldste ventende varsles først, som i
-- innboksen.
CREATE OR REPLACE FUNCTION varsel_klaim_epost(p_grense int,
                                              p_maks int DEFAULT 3)
RETURNS TABLE (id bigint, tenant text, epost text, tekstnokkel text,
               parametre jsonb, forsok int)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    UPDATE varsel v
       SET epost_status = 'under_sending',
           epost_ts     = now(),
           epost_forsok = v.epost_forsok + 1
      FROM brukeridentitet i
     WHERE i.bruker_id = v.bruker_id
       AND v.id IN (SELECT k.id
                      FROM varsel k
                      JOIN brukeridentitet b ON b.bruker_id = k.bruker_id
                     WHERE k.epost_status = 'koet'
                       AND (b.profil->>'epost') IS NOT NULL
                       AND (b.profil->>'epost_verifisert')::boolean IS TRUE
                       AND k.epost_forsok < greatest(1, coalesce(p_maks, 3))
                     ORDER BY k.opprettet
                     LIMIT greatest(1, least(coalesce(p_grense, 50), 500))
                     FOR UPDATE OF k SKIP LOCKED)
 RETURNING v.id, v.tenant, i.profil->>'epost', v.tekstnokkel, v.parametre,
           v.epost_forsok;
$$;

-- `sendt` er terminalt. `feilet` beholder forsøkstelleren klaimet satte, så en
-- adresse som aldri tar imot ikke prøves i evighet — men raden BLIR STÅENDE i
-- portalen: innboksen er sannheten, e-posten er kopien.
CREATE OR REPLACE FUNCTION varsel_sett_epoststatus(
    p_id bigint, p_status text, p_feil text DEFAULT NULL)
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
    -- Telleren økes IKKE her: det gjorde klaimet. Ble den økt begge steder,
    -- ville et forsøk kostet to, og `MAKS_FORSOK` betydd halvparten av det
    -- den sier.
    UPDATE varsel
       SET epost_status = p_status,
           epost_ts     = now(),
           epost_feil   = left(p_feil, 500)
     WHERE id = p_id AND epost_status = 'under_sending';
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n = 1;
END $$;

-- SECURITY DEFINER kjører som EIEREN, så eieren må selv ha lesetilgangen —
-- ellers feiler funksjonen med InsufficientPrivilege uansett hvem som kaller
-- den. Rettighetene er minimale og med vilje asymmetriske: LES på de to
-- tabellene funksjonen slår opp i, SKRIV bare på statusfeltene i .
GRANT SELECT ON varsel, brukeridentitet TO disponit_domene_eier;
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
    UPDATE varsel
       SET epost_status = 'koet'
     WHERE epost_forsok < greatest(1, p_maks)
       AND epost_ts IS NOT NULL
       AND ((epost_status = 'feilet'
             AND epost_ts < now() - p_backoff)
         OR (epost_status = 'under_sending'
             AND epost_ts < now() - greatest(p_lease, interval '5 minutes')));
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END $$;

ALTER FUNCTION varsel_rekoe(interval, int, interval)
    OWNER TO disponit_domene_eier;
REVOKE ALL ON FUNCTION varsel_rekoe(interval, int, interval) FROM PUBLIC;
ALTER FUNCTION varsel_klaim_epost(int, int) OWNER TO disponit_domene_eier;
ALTER FUNCTION varsel_sett_epoststatus(bigint, text, text)
    OWNER TO disponit_domene_eier;
REVOKE ALL ON FUNCTION varsel_klaim_epost(int, int) FROM PUBLIC;
REVOKE ALL ON FUNCTION varsel_sett_epoststatus(bigint, text, text) FROM PUBLIC;

-- …OG SÅ TILBAKE TIL DEN ROLLEN SOM FAKTISK KALLER DEM.
--
-- EXECUTE er PUBLIC som standard i PostgreSQL, så `REVOKE … FROM PUBLIC` over
-- er det som gjør funksjonene utilgjengelige — for ALLE utenom eieren. Uten
-- linjene under kunne senderen ikke kalle en eneste av dem: unitten kobler med
-- runtime-DSN-en (`skriv_cred varsel DISPONIT_DATABASE_URL "$DATABASE_URL"`),
-- altså rollen `disponit`, og den er ikke medlem av `disponit_domene_eier`.
-- Hver eneste timerkjøring ville endt i «permission denied for function
-- varsel_klaim_epost», med køen urørt.
--
-- Og det ville ikke vist seg i CI: testene kobler som `disponit_migrator`, som
-- ER medlem av eierrollen. Samme klasse som de to andre P1-ene i denne runden
-- — grønn der den prøves, død der den kjører. `test_varselsender` måler derfor
-- disse tre gjennom runtime-rollen, ikke gjennom migratorrollen.
--
-- Paret REVOKE + GRANT er husets form (009, 014, 016, 017, 019): revokeringen
-- alene er ikke en tilgangsstyring, den er bare en stengt dør.
GRANT EXECUTE ON FUNCTION varsel_klaim_epost(int, int) TO disponit;
GRANT EXECUTE ON FUNCTION varsel_sett_epoststatus(bigint, text, text)
    TO disponit;
GRANT EXECUTE ON FUNCTION varsel_rekoe(interval, int, interval) TO disponit;
