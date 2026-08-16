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
--   * bare rader med `epost_status='koet'` — aldri leste, aldri sendte;
--   * bare til VERIFISERTE adresser. En uverifisert e-post i profilen er en
--     påstand fra en IdP, ikke et bevis, og et varsel om en fullmaktsrunde
--     skal ikke sendes til en adresse ingen har bekreftet;
--   * ingen `lest_ts`, ingen historikk, ingen andre tenanters tilstand.
--
-- Teksten sendes IKKE herfra: funksjonen gir `tekstnokkel` + `parametre`, og
-- senderen rendrer på mottakerens språk. Databasen skal ikke kunne noe språk.
--
-- Oppdateringen av status går gjennom sin egen funksjon, så senderen aldri får
-- generell UPDATE på tvers av tenanter: den kan flytte en rad fra `koet` til
-- `sendt`/`feilet`, og ingenting annet.
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
DROP FUNCTION IF EXISTS varsel_sett_epoststatus(bigint, text, text);
DROP FUNCTION IF EXISTS varsel_rekoe_feilede(interval, int);

CREATE OR REPLACE FUNCTION varselkandidater(p_grense int)
RETURNS TABLE (id bigint, tenant text, epost text, tekstnokkel text,
               parametre jsonb, forsok int)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT v.id, v.tenant, i.profil->>'epost', v.tekstnokkel, v.parametre,
           v.epost_forsok
      FROM varsel v
      JOIN brukeridentitet i ON i.bruker_id = v.bruker_id
     WHERE v.epost_status = 'koet'
       AND (i.profil->>'epost') IS NOT NULL
       AND (i.profil->>'epost_verifisert')::boolean IS TRUE
     ORDER BY v.opprettet
     LIMIT greatest(1, least(coalesce(p_grense, 50), 500));
$$;

-- `sendt` er terminalt. `feilet` teller forsøk, så en adresse som aldri tar
-- imot ikke prøves i evighet — men raden BLIR STÅENDE i portalen: innboksen er
-- sannheten, e-posten er kopien.
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
    -- Bare fra `koet`: en rad som alt er `sendt` skal ikke kunne settes om, og
    -- to samtidige sendere skal ikke kunne sende samme varsel to ganger.
    UPDATE varsel
       SET epost_status = p_status,
           epost_ts     = now(),
           epost_forsok = epost_forsok + 1,
           epost_feil   = left(p_feil, 500)
     WHERE id = p_id AND epost_status = 'koet';
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n = 1;
END $$;

-- SECURITY DEFINER kjører som EIEREN, så eieren må selv ha lesetilgangen —
-- ellers feiler funksjonen med InsufficientPrivilege uansett hvem som kaller
-- den. Rettighetene er minimale og med vilje asymmetriske: LES på de to
-- tabellene funksjonen slår opp i, SKRIV bare på statusfeltene i .
GRANT SELECT ON varsel, brukeridentitet TO disponit_domene_eier;
GRANT UPDATE ON varsel TO disponit_domene_eier;

-- RE-KØING: en feilet sending er ikke endelig.
--
-- Uten dette var `feilet` en blindvei: `varselkandidater` plukker bare `koet`,
-- så en rad som feilet én gang ble aldri forsøkt igjen — og forsøkstelleren i
-- senderen var død kode. Ett forbigående SMTP-hikk mistet e-posten for godt.
--
-- Re-køingen er et EGET steg, ikke en utvidelse av plukket, nettopp for å
-- beholde garantien om at ingenting sendes to ganger: `koet` er fortsatt den
-- ENESTE sendbare tilstanden, og `varsel_sett_epoststatus` flytter bare
-- derfra. To sendere som kjører samtidig kan dermed ikke ta samme rad.
--
-- Backoff, ikke umiddelbar retry: en adresse som nettopp avviste, avviser
-- sannsynligvis igjen. Og et tak på forsøk, så en adresse som aldri tar imot
-- ikke banker på i evighet — raden blir uansett stående i portalen, som er
-- der varselet egentlig bor.
CREATE OR REPLACE FUNCTION varsel_rekoe_feilede(
    p_backoff interval DEFAULT interval '15 minutes',
    p_maks int DEFAULT 3)
RETURNS int
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE n int;
BEGIN
    UPDATE varsel
       SET epost_status = 'koet'
     WHERE epost_status = 'feilet'
       AND epost_forsok < greatest(1, p_maks)
       AND epost_ts < now() - p_backoff;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END $$;

ALTER FUNCTION varsel_rekoe_feilede(interval, int) OWNER TO disponit_domene_eier;
REVOKE ALL ON FUNCTION varsel_rekoe_feilede(interval, int) FROM PUBLIC;
ALTER FUNCTION varselkandidater(int) OWNER TO disponit_domene_eier;
ALTER FUNCTION varsel_sett_epoststatus(bigint, text, text)
    OWNER TO disponit_domene_eier;
REVOKE ALL ON FUNCTION varselkandidater(int) FROM PUBLIC;
REVOKE ALL ON FUNCTION varsel_sett_epoststatus(bigint, text, text) FROM PUBLIC;
