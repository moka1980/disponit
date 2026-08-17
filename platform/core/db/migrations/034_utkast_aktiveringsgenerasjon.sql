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
-- teller som bare går oppover, og som ingen kode noen gang nullstiller eller
-- teller ned. Ankerraden slettes heller aldri (`hode_ingen_sletting`, 012).
-- Tre veier skriver den, og alle tre teller OPP: den styrte aktiveringen
-- (013, videreført gjennom 020–025), slettingen (032) og
-- `policyregister.registrer` når den aktiverer — oppsett-/token-veien som
-- skriver `policyer` direkte, helt uten utkast. Et tall derfra er derfor ett
-- bestemt punkt i policyens liv, og kan aldri komme igjen, uansett hvor mange
-- ganger id-en, versjonen eller innholdet gjenbrukes.
--
-- Den tredje veien er også grunnen til at prøven er RIKTIG konservativ:
-- skrives en ny generasjon utenom utkastveien, er den aktive generasjonen
-- ikke lenger den utkastet laget, og utkastet forsvinner fra køen. Det er
-- sant — og fail-closed, som er retningen en visningsregel skal bomme i.
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
-- INGEN BACKFILL. Historiske rader står med NULL, og det er svaret.
--
-- Koblingen «hvilket utkast ble hvilken generasjon» er nøyaktig det basen
-- ALDRI lagret — det er hele grunnen til at denne migrasjonen finnes. For
-- rader som ble aktivert før den, finnes den derfor ikke å hente noe sted, og
-- alt en backfill kan gjøre er å GJETTE ut fra beskrivelser. To forsøk ble
-- skrevet og begge var gale, hver på sin måte (Codex P2, to runder):
--
--   * «det nyeste utkastet med matchende hash» leste opprettelsesrekkefølge
--     som aktiveringsrekkefølge. Et utkast kan ligge lenge før det aktiveres,
--     så lages B før A, aktiveres A, slettes policyen og aktiveres så B med
--     samme innhold, peker «nyeste» på A — den SLETTEDE generasjonen.
--   * «bare når hashen peker på ett eneste utkast» antok at den aktive
--     generasjonen i det hele tatt kom fra et utkast. Det gjør den ikke
--     nødvendigvis: `policyregister.registrer` skriver `policyer` direkte og
--     teller opp `policy_hode.revisjon` UTEN noe utkast (oppsett-/token-veien).
--     Slettes en policy og gjenskapes den samme veien, er det gamle utkastet
--     det ENESTE som matcher hashen — entydig, og likevel feil.
--
-- Begge gjetningene feiler i samme retning: de stempler den SLETTEDE
-- generasjonens utkast som gjeldende, altså nøyaktig den feilen 034 finnes
-- for å fjerne. En rekonstruksjon som kan gjeninnføre feilen den skulle
-- rydde opp i, er verre enn ingen rekonstruksjon — og et tredje forsøk ville
-- vært en ny gjetning på samme manglende opplysning.
--
-- NULL er allerede det riktige, fail-closed svaret: «kan ikke vises som
-- gjeldende». KOSTNADEN, sagt rett ut: rett etter oppgraderingen forsvinner
-- også de aktiverte utkastene som ER gjeldende, fra arbeidskøen. Det er en
-- akseptabel pris her, og bare her:
--   * `aktivert` er terminalt (012/033) — raden har ingen handling igjen, så
--     det som går tapt er en visning av tilstand, ikke en arbeidsoppgave;
--   * radene består i basen som attestasjonsankre og nås via detalj-ruten;
--   * policylista viser den levende policyen uansett;
--   * og det retter seg selv: neste aktivering av policyen stempler eksakt.
-- Alternativet — å vise dem på en gjetning — kan presentere en SLETTET policy
-- som gjeldende tilstand, og det er den feilen eier meldte.
--
-- (Uten backfill trengs heller ikke RLS-vinduet fra 008/029: det fantes bare
-- fordi migrator måtte lese på tvers av tenanter. Én ALTER TABLE med ACCESS
-- EXCLUSIVE mindre på tre varme tabeller under oppgradering.)
-- ------------------------------------------------------------

-- ------------------------------------------------------------
-- STEMPELET.
--
-- MERK for en senere migrasjon som måtte ville fylle kolonnen for historiske
-- rader: triggeren under fryser den for alt annet enn selve overgangen til
-- `aktivert`, så en naken UPDATE etterpå blir STILLE overskrevet med den
-- gamle verdien — en skriving som rapporterer suksess og ikke gjør noe.
--
-- Egen trigger, ikke et nytt vilkår i `policyutkast_kolonnelaas`:
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
