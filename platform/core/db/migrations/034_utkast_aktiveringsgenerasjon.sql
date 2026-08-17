-- ============================================================
-- 034 — Et aktivert utkast bindes til GENERASJONEN det ble (Codex P2 på #79)
--
-- Arbeidskølista (`list_utkast`) viser et `aktivert` utkast bare når det ER
-- gjeldende tilstand. Spørsmålet den må svare på er «hvilken generasjon av
-- policyen ble dette utkastet, og er den fortsatt den aktive?» — og det
-- spørsmålet hadde ingen LAGRET svar. De to identitetene som lå nærmest ble
-- prøvd etter tur, og begge er GJENBRUKBARE:
--
--   * `policy_id`: 032 nullstiller pekeren ved sletting, så id-en blir ledig
--     igjen. Aktiveres en erstatning under samme id, er pekeren ikke-NULL på
--     nytt — og prøven «finnes det en aktiv-peker for denne id-en» ble sann
--     også for det SLETTEDE utkastet.
--   * `innholds_hash`: 020 gjorde versjonen til DOKUMENTETS versjon, og 032
--     sletter radene, så nøyaktig samme dokument (samme `meta.versjon`) kan
--     aktiveres på nytt etterpå — det er nettopp derfor versjonene frigjøres.
--     Hashen er deterministisk av innholdet, så erstatningen får da SAMME
--     hash som den slettede generasjonen, og prøven «samme hash som den
--     aktive raden» ble sann for BEGGE utkastene. Den slettede generasjonen
--     sto igjen i arbeidskøen, side om side med erstatningen, presentert som
--     gjeldende tilstand.
--
-- Ingen av dem er en identitet. De er BESKRIVELSER, og en beskrivelse kan
-- passe på to generasjoner samtidig — samme lærdom som 032 skriver ut om
-- `forventet_versjon`, der versjonsnummeret alene kunne peke på et helt annet
-- dokument. Forskjellen er at 032 kunne løse det med et PAR (versjon, hash),
-- fordi den bare måtte kjenne igjen NÅET; her må vi kjenne igjen et bestemt
-- punkt i FORTIDEN, og da hjelper ingen mengde beskrivelser av innholdet.
--
-- Det som ER unikt, og som allerede finnes, er `policy_hode.revisjon`: en
-- teller som bare går oppover — +1 for hver aktivering (013, videreført
-- gjennom 020–025) og +1 for hver sletting (032) — og som ingen kode noen
-- gang nullstiller eller teller ned. Ankerraden slettes heller aldri
-- (`hode_ingen_sletting`, 012). Et tall derfra er derfor ett bestemt punkt i
-- policyens liv, og kan aldri komme igjen, uansett hvor mange ganger id-en,
-- versjonen eller innholdet gjenbrukes.
--
-- 034 LAGRER svaret i stedet for å utlede det: `policyutkast.aktivert_revisjon`
-- settes i det utkastet blir `aktivert`. Prøven i lista blir da en likhet
-- mellom to tall — «hodets revisjon er fortsatt den jeg laget» — og hverken
-- gjenbrukt id, gjenbrukt versjon eller gjenbrukt innhold kan gjøre den sann
-- for en generasjon som er borte.
--
-- REKKEFØLGEN INNE I `aktiver_policy` ER FORUTSETNINGEN, og den er ikke
-- tilfeldig: steg 5 setter inn den nye policyraden, flytter pekeren og gjør
-- `revisjon = revisjon + 1`; FØRST steg 6 setter utkastet til `aktivert`.
-- Triggeren under leser derfor den allerede oppdaterte telleren, i samme
-- transaksjon, og får nøyaktig den generasjonen dette utkastet ble. Flyttes
-- utkastoppdateringen foran pekeroppdateringen, peker stemplet på FORRIGE
-- generasjon og lista slutter å vise det aktive utkastet — en migrasjon som
-- rører den funksjonen må lese dette avsnittet først.
--
-- VERDIEN ER SERVER-UTLEDET, som `er_forfatter` (V7, 012 7a). Runtime har
-- UPDATE på `policyutkast` (den skriver status og hash), så en kolonne
-- kalleren kunne fylt selv ville vært en identitet kalleren kunne DIKTET —
-- og da var vi tilbake til en påstand om generasjonen i stedet for et
-- vitnesbyrd om den. Triggeren overskriver derfor alltid det som skrives
-- utenfra: den setter verdien ved overgangen til `aktivert`, og holder den
-- ellers uendret.
-- ============================================================

ALTER TABLE policyutkast
    ADD COLUMN IF NOT EXISTS aktivert_revisjon BIGINT;

-- ------------------------------------------------------------
-- BACKFILL — og den må kjøre FØR triggeren opprettes.
--
-- Triggeren under fryser kolonnen for alt annet enn selve overgangen til
-- `aktivert`. En backfill etterpå ville derfor blitt stille overskrevet med
-- den gamle verdien (NULL) — en UPDATE som rapporterer suksess og ikke gjør
-- noe. Rekkefølgen her er altså en del av logikken, ikke en smakssak.
--
-- Hva som er RIKTIG verdi for et utkast som ble aktivert før denne
-- migrasjonen, kan ikke rekonstrueres eksakt: den koblingen er nettopp det
-- basen aldri lagret. Det beste tilgjengelige vitnet er den prøven koden
-- brukte fram til nå — utkastets frosne hash mot den AKTIVE policyraden — og
-- backfillen stempler de utkastene med hodets nåværende revisjon. Der vitnet
-- er entydig viser lista da nøyaktig det samme for historiske rader som den
-- gjorde før migrasjonen, mens alt som aktiveres ETTERPÅ har et eksakt
-- stempel.
--
-- ER VITNET FLERTYDIG, STEMPLES INGENTING (Codex P2). Har kollisjonen over
-- alt skjedd i basen, matcher hashen både den slettede generasjonens utkast
-- og erstatningens, og da finnes det ikke noe i basen som skiller dem. Et
-- første forsøk gjettet på det NYESTE utkastet, som om opprettelsesrekkefølge
-- var aktiveringsrekkefølge. Det er den ikke: et utkast kan ligge lenge før
-- det aktiveres. Lages B før A, aktiveres A, slettes policyen og aktiveres så
-- B med samme innhold, peker «nyeste» på A — den SLETTEDE generasjonen — og
-- backfillen ville stemplet nettopp den raden som skal skjules, og skjult den
-- som lever. En gjetning som kan ta feil på begge sider er verre enn ingen.
--
-- `NOT EXISTS`-leddet stempler derfor bare når det gamle vitnet peker på
-- NØYAKTIG ett utkast. Flertydige treff står igjen med NULL, og NULL er «kan
-- ikke vises som gjeldende»: da forsvinner begge radene fra køen i stedet for
-- at feil rad utgir seg for å være gjeldende tilstand. Ingenting går tapt —
-- radene består i basen og nås via detalj-ruten — og det gjelder uansett bare
-- utkast som ble aktivert FØR denne migrasjonen, i en base som allerede bærer
-- kollisjonen. Alt som aktiveres etterpå har et eksakt stempel.
--
-- RLS-VINDUET er det samme grepet og den samme grunnen som i 008 og 029:
-- tabellene står med FORCE ROW LEVEL SECURITY mot `disponit.tenant`, migrator
-- har ingen tenantkontekst, og backfillen er kryss-tenant av natur. Med FORCE
-- på ville UPDATE-en truffet null rader og migrasjonen sett ut som en suksess.
-- NO FORCE unntar KUN tabelleieren, og `ALTER TABLE` holder ACCESS EXCLUSIVE,
-- så ingen annen sesjon kan lese i vinduet; feiler noe underveis, rulles også
-- RLS-endringen tilbake med transaksjonen.
-- ------------------------------------------------------------
ALTER TABLE policyutkast NO FORCE ROW LEVEL SECURITY;
ALTER TABLE policyer     NO FORCE ROW LEVEL SECURITY;
ALTER TABLE policy_hode  NO FORCE ROW LEVEL SECURITY;

UPDATE policyutkast u
   SET aktivert_revisjon = h.revisjon
  FROM policyer p, policy_hode h
 WHERE u.status = 'aktivert' AND u.innholds_hash IS NOT NULL
   AND p.tenant = u.tenant AND p.policy_id = u.policy_id
   AND p.aktiv AND p.innholds_hash = u.innholds_hash
   AND h.tenant = u.tenant AND h.policy_id = u.policy_id
   AND NOT EXISTS (SELECT 1 FROM policyutkast a
                    WHERE a.tenant = u.tenant
                      AND a.policy_id = u.policy_id
                      AND a.status = 'aktivert'
                      AND a.innholds_hash = u.innholds_hash
                      AND a.utkast_id <> u.utkast_id);

ALTER TABLE policyutkast FORCE ROW LEVEL SECURITY;
ALTER TABLE policyer     FORCE ROW LEVEL SECURITY;
ALTER TABLE policy_hode  FORCE ROW LEVEL SECURITY;

-- ------------------------------------------------------------
-- STEMPELET. Egen trigger, ikke et nytt vilkår i `policyutkast_kolonnelaas`:
-- den funksjonen NEKTER (den er en lås og kaster), denne SETTER. Å blande de
-- to rollene i én kropp ville gjort begge vanskeligere å lese, og 033 viste
-- hva det koster å måtte kopiere en kropp riktig for å endre ett vilkår.
--
-- Ingen ankerrad → NULL, ikke unntak. `aktiver_policy` oppretter ankerraden
-- (`ON CONFLICT DO NOTHING`) FØR den rører utkastet, så tilfellet kan ikke
-- oppstå på den veien. Skulle det likevel finnes et `aktivert` utkast uten
-- hoderad, er sannheten om det at det ikke finnes noen levende generasjon å
-- være gjeldende for — og da er «vises ikke» det riktige svaret. En
-- visningsregel skal ikke legge en ny måte å avbryte en AKTIVERING på.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION policyutkast_aktiveringsgenerasjon()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    v_aktiveres BOOLEAN;
BEGIN
    -- Kalleren blir ALLTID overstyrt først: på INSERT til NULL, på UPDATE til
    -- den verdien raden alt hadde. Deretter — og bare ved selve overgangen
    -- til `aktivert` — settes stempelet fra ankerraden. Rekkefølgen er det
    -- som gjør kolonnen til et vitnesbyrd i stedet for et innspill.
    --
    -- `TG_OP` testes i EGEN gren, ikke som et ledd ved siden av `OLD.status`:
    -- plpgsql lover ikke å kortslutte `OR`, og en `OLD`-lesing i en
    -- INSERT-trigger er «record old is not assigned yet» — en feil som ville
    -- truffet hver eneste opprettelse av et utkast.
    IF TG_OP = 'INSERT' THEN
        NEW.aktivert_revisjon := NULL;
        -- «Født aktivert» skjer bare i tester og manuelle inngrep;
        -- aktiveringsveien går alltid `utkast → … → aktivert`.
        v_aktiveres := NEW.status = 'aktivert';
    ELSE
        NEW.aktivert_revisjon := OLD.aktivert_revisjon;
        v_aktiveres := NEW.status = 'aktivert'
                       AND OLD.status IS DISTINCT FROM 'aktivert';
    END IF;
    IF v_aktiveres THEN
        SELECT h.revisjon INTO NEW.aktivert_revisjon
          FROM public.policy_hode h
         WHERE h.tenant = NEW.tenant AND h.policy_id = NEW.policy_id;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS utkast_aktiveringsgenerasjon ON policyutkast;
CREATE TRIGGER utkast_aktiveringsgenerasjon
    BEFORE INSERT OR UPDATE ON policyutkast
    FOR EACH ROW EXECUTE FUNCTION policyutkast_aktiveringsgenerasjon();
