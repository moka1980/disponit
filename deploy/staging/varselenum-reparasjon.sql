-- VARSELENUMENE KANONISERES. Kjøres av oppsett-postgresql.sh, ved siden
-- av eierskap-reparasjon.sql og av samme grunn: dette er drift en
-- migrasjon ikke kan reparere innenfra, fordi migrasjonen som feiler PÅ
-- driften kommer først i kjeden.
--
-- BAKGRUNNEN, målt og ikke gjettet. Deployen av #324 stoppet på 090 med
--   «090: kunne ikke utvide varsel_ressurs_type_chk -- uventet
--    definisjonsform: CHECK ((ressurs_type = ANY (ARRAY['policyutkast',
--    'modultoken','domene'])))»
-- mens en FERSK kjede (001-091) gir seks verdier. Produksjonsbasen og
-- migrasjonskjeden hadde drevet fra hverandre, uten at noen port kunne
-- se det -- fordi ingen port sammenlignet dem.
--
-- AARSAKEN er en for bred sporring i 044, kombinert med en manglende
-- vakt:
--
--   SELECT conname, pg_get_constraintdef(oid) INTO c, def FROM pg_constraint
--    WHERE conrelid = 'varsel'::regclass
--      AND pg_get_constraintdef(oid) LIKE '%ressurs_type%'
--
-- Paa PostgreSQL 18.6 -- versjonen bade riggen og produksjonen kjoerer,
-- maalt med SHOW server_version -- har en NOT NULL-begrensning sin EGEN
-- rad i pg_constraint (contype 'n'), med definisjonen bokstavelig
-- «NOT NULL ressurs_type». Sporringen treffer derfor TO rader, og
-- SELECT ... INTO uten ORDER BY plukker en vilkaarlig av dem.
--
-- Katalogfoerte NOT NULL-begrensninger er en NYERE egenskap enn 044 ble
-- skrevet for. 044 var altsaa riktig da den ble skrevet, og ble feil av
-- at basen under den endret seg -- verdt aa merke seg foer noen leser
-- dette som slurv.
--
-- Traff den NOT NULL-raden, ble def = 'NOT NULL ressurs_type',
-- replace() fant ingenting aa bytte, og 044 DROPPET og la tilbake
-- NOT NULL-begrensningen -- uendret, uten feil, og uten aa roere
-- CHECKen. 041 og 090 har en vakt som RAISE-r naar spliceen ikke tok;
-- 044s ressurstype-arm hadde ingen. Den ene manglende vakten er hele
-- forskjellen mellom «roedt i deployen» og «stille drift i to maaneder».
--
-- 044s ART-arm slapp unna ved flaks: den soeker paa
-- '%attestering_venter%', som bare CHECKen inneholder. Derfor er
-- art-enumet riktig i produksjon og ressurstype-enumet ikke. Det er ikke
-- en teoretisk fallgruve -- det er forskjellen mellom de to armene i den
-- samme DO-blokken.
--
-- KONSEKVENSEN, sagt rett ut: pause_plan og varsle_plan_brudd (044)
-- setter inn varsel med ressurs_type='plan'. I produksjon har den
-- INSERT-en vaert avvist av CHECKen siden 044 landet. Begge kallene er
-- pakket i «EXCEPTION WHEN OTHERS THEN RAISE WARNING» -- varselet er
-- ikke evidens, og pausen skal staa selv om varselet ryker -- saa feilen
-- har staatt som WARNING i driftsloggen og ingen andre steder. Ingen
-- planpause-varsler har naadd en mottaker i produksjon. Denne filen
-- aapner veien; den kan ikke sende de tapte.
--
-- FORMEN HER er en annen enn spliceens. Enumet DEKLARERES, og
-- begrensningen bygges fra deklarasjonen. En splice leser gjeldende
-- tilstand og legger til; den kan bare vaere like riktig som tilstanden
-- den leste. En deklarasjon kan sammenlignes med en fasit -- og
-- test_varselenum.py gjoer nettopp det, mot en base som har kjoert hele
-- kjeden. Neste avvik blir roedt i CI, ikke stille i drift.
--
-- Idempotent: en base som alt er kanonisk roeres ikke.
-- NB (eierskap-reparasjonens laerdom): testen parser KANONISK-blokken
-- under. Hold formen -- ett element per linje, enkle fnutter.

DO $$
DECLARE
  -- KANONISK: unionen av det hele kjeden HAR ment aa bygge.
  -- Rekkefoelgen er den kjeden gir; den er del av fasiten.
  v_art TEXT[] := ARRAY[
      'attestering_venter',
      'validering_venter',
      'runde_apnet',
      'tokenfamilie_utloper',
      'domeneovertakelse',
      'plan_pauset',
      'plan_gjentatt_brudd',
      'backupverifisering_uteblitt',
      'selvtest_rodt',
      'selvtest_uteblitt',
      'pliktfrist',
      'lisensutlop'];
  v_ressurs TEXT[] := ARRAY[
      'policyutkast',
      'modultoken',
      'domene',
      'plan',
      'backupverifisering',
      'selvtest',
      'plikt',
      'lisens'];
  r RECORD; v_def TEXT; v_onsket TEXT; v_ulovlige TEXT; v_forst TEXT;
BEGIN
  IF to_regclass('public.varsel') IS NULL THEN
    RAISE NOTICE 'varselenum: tabellen varsel finnes ikke ennaa -- hopper'
        ' over (fersk base, kjeden lager den kanonisk)';
    RETURN;
  END IF;
  -- Maalingen av rekkefoelgen skal skje under den planen produksjonen
  -- faktisk velger — bitmap heap scan, som gir FYSISK rekkefoelge. Med
  -- indeksskann kommer radene i conname-rekkefoelge og CHECKen vinner
  -- alltid; da hadde porten maalt den snille planen og sluppet gjennom
  -- den farlige.
  SET LOCAL enable_indexscan = off;
  SET LOCAL enable_indexonlyscan = off;
  FOR r IN
    -- `monster` er 090/091s EGET LIKE-moenster, ordrett. Det er ikke en
    -- detalj: art-oppslaget deres soeker paa '%attestering_venter%', som
    -- BARE CHECKen inneholder, mens ressurstype-oppslaget soeker paa
    -- '%ressurs_type%', som ogsaa treffer NOT NULL-raden. Derfor er art
    -- aldri i fare og ressurstype alltid — og en port som maalte
    -- '%art%' ville maalt noe HELT ANNET enn migrasjonene gjoer, og
    -- krevd en gjenskaping ingen trenger.
    SELECT 'varsel_art_chk' AS navn, 'art' AS kolonne, v_art AS sett,
           '%attestering_venter%' AS monster
    UNION ALL
    SELECT 'varsel_ressurs_type_chk', 'ressurs_type', v_ressurs,
           '%ressurs_type%'
  LOOP
    -- DETERMINISTISK oppslag: paa navn OG contype='c'. Det er denne ene
    -- linjen som gjoer at NOT NULL-raden aldri kan bli plukket i stedet.
    SELECT pg_get_constraintdef(oid) INTO v_def FROM pg_constraint
     WHERE conrelid = 'varsel'::regclass
       AND conname = r.navn AND contype = 'c';
    IF v_def IS NULL THEN
      RAISE EXCEPTION 'varselenum: fant ikke CHECK-begrensningen % paa'
          ' varsel -- basen har drevet mer enn denne filen kan reparere',
          r.navn;
    END IF;

    v_onsket := format('CHECK ((%I = ANY (ARRAY[%s])))', r.kolonne,
        (SELECT string_agg(format('%L::text', v), ', ')
           FROM unnest(r.sett) AS v));
    -- TO grunner til aa gjenskape, ikke én. Innholdet kan vaere riktig
    -- mens REKKEFOELGEN er gal — og det er nøyaktig tilstanden
    -- produksjonsbasen sto i etter foerste kjoering av denne filen:
    -- CHECKen var kanonisk, men laa fysisk etter NOT NULL-raden, saa 090
    -- plukket feil rad. En `CONTINUE WHEN v_def = v_onsket` alene ville
    -- hoppet over og latt feilen staa.
    --
    -- Rekkefoelgen maales under BITMAPPLANEN, den ene som kan gi feil
    -- rad (se blokken nederst).
    SELECT conname INTO v_forst FROM pg_constraint
     WHERE conrelid = 'public.varsel'::regclass
       AND pg_get_constraintdef(oid) LIKE r.monster;
    CONTINUE WHEN v_def = v_onsket AND v_forst = r.navn;

    -- Kanoniseringen er en UTVIDELSE: ingen eksisterende rad skal kunne
    -- falle utenfor. Skulle en gjoere det, er basen i en tilstand denne
    -- filen ikke forstaar, og da skal den stoppe -- ikke slette.
    EXECUTE format(
        'SELECT string_agg(DISTINCT %I, '', '') FROM public.varsel'
        ' WHERE NOT (%I = ANY ($1))', r.kolonne, r.kolonne)
      INTO v_ulovlige USING r.sett;
    IF v_ulovlige IS NOT NULL THEN
      RAISE EXCEPTION 'varselenum: varsel har rader utenfor det kanoniske'
          ' %-settet: % -- utvid deklarasjonen framfor aa slette rader',
          r.kolonne, v_ulovlige;
    END IF;

    -- BEGGE begrensningene legges tilbake, CHECKen FOERST. Grunnen staar
    -- i blokken nederst i filen: 090/091 leser en UORDNET
    -- `SELECT ... INTO` som treffer baade CHECKen og NOT NULL-raden, og
    -- produksjonsbasens plan gir dem i FYSISK rekkefoelge. Legges CHECKen
    -- tilbake foerst, faar den den tidligste ledige plassen.
    --
    -- Vinduet der kolonnen er nullbar ligger INNE i denne transaksjonen,
    -- som holder ACCESS EXCLUSIVE paa varsel: ingen annen oekt ser det,
    -- og en feil ruller alt tilbake. SET NOT NULL skanner tabellen paa
    -- nytt — det er prisen, og den er liten.
    EXECUTE format('ALTER TABLE public.varsel DROP CONSTRAINT %I', r.navn);
    EXECUTE format('ALTER TABLE public.varsel ALTER COLUMN %I DROP NOT NULL',
                   r.kolonne);
    EXECUTE format('ALTER TABLE public.varsel ADD CONSTRAINT %I %s',
                   r.navn, v_onsket);
    EXECUTE format('ALTER TABLE public.varsel ALTER COLUMN %I SET NOT NULL',
                   r.kolonne);
    RAISE NOTICE 'varselenum: % kanonisert', r.navn;
  END LOOP;
END $$;

-- ---------------------------------------------------------------------
-- RADREKKEFOELGEN — maalt, ikke antatt.
--
-- 090 og 091 slaar opp begrensningen slik:
--
--   SELECT conname, pg_get_constraintdef(oid) INTO c, def FROM pg_constraint
--    WHERE conrelid = 'varsel'::regclass
--      AND pg_get_constraintdef(oid) LIKE '%ressurs_type%'
--
-- Det treffer TO rader — CHECKen og NOT NULL-raden, som paa PostgreSQL
-- 18.6 er en egen katalograd — og SELECT ... INTO tar den FOERSTE planen
-- gir, uten ORDER BY.
--
-- HVILKEN som kommer foerst avhenger av PLANEN, og det er dette som gjoer
-- feilen saa vanskelig aa se:
--
--   * Paa en liten base velger planleggeren Index Scan paa
--     pg_constraint_conrelid_contypid_conname_index, og radene kommer i
--     CONNAME-rekkefoelge. `varsel_ressurs_type_chk` sorterer foer
--     `varsel_ressurs_type_not_null`, saa CHECKen vinner alltid.
--   * Paa produksjonsbasen velger den BITMAP HEAP SCAN — og en bitmap
--     heap scan returnerer rader i FYSISK rekkefoelge. Da vinner den
--     tuppelen som ligger tidligst i haugen.
--
-- Foerste versjon av denne filen gjenskapte CHECKen med DROP + ADD. Den
-- nye tuppelen havnet FYSISK ETTER NOT NULL-raden, og neste deploy
-- stoppet paa
--
--   090: kunne ikke utvide varsel_ressurs_type_not_null
--        — uventet definisjonsform: NOT NULL ressurs_type
--
-- altsaa: reparasjonen loeste innholdet og skapte rekkefoelgeproblemet.
-- Begge deler er verifisert i en base med tvunget bitmap-plan
-- (enable_indexscan=off): foer gjenskaping vant CHECKen, etter vant
-- NOT NULL-raden, og etter rettelsen under vant CHECKen igjen.
--
-- 090 og 091 er MERGET HISTORIKK og kan ikke rettes:
-- test_fasiten_er_append_only_mot_basisgrenen slaar ned paa enhver
-- endring i en pinnet migrasjon — «en fasit som er sin egen fasit er
-- ingen fasit». Feilen maa derfor omgaas FORFRA, og loesningen er at
-- BLOKKEN OVER legger CHECKen tilbake FOER NOT NULL-raden, saa CHECKen
-- faar den tidligste ledige plassen.
--
-- Blokken under kjoerer 090s og 091s EGEN spoerring og krever at den
-- lander paa CHECKen — under BEGGE planformene, fordi bitmapplanen
-- tvinges fram i den ene maalingen. Gjoer den ikke det, stopper vi HER,
-- foer deployen har roert noe, i stedet for aa la migrasjonen feile midt
-- i kjeden. En reparasjon som ikke kan bevise sitt eget resultat er
-- ingen reparasjon.
-- ---------------------------------------------------------------------
DO $$
DECLARE r RECORD; v_navn TEXT;
BEGIN
  IF to_regclass('public.varsel') IS NULL THEN
    RETURN;
  END IF;
  -- Bitmap heap scan er planen produksjonsbasen faktisk velger, og den
  -- ene som kan gi feil rad. Vi maaler DEN, ikke den snille.
  SET LOCAL enable_indexscan = off;
  SET LOCAL enable_indexonlyscan = off;
  FOR r IN
    SELECT 'ressurs_type' AS kolonne, 'varsel_ressurs_type_chk' AS forventet,
           '%ressurs_type%' AS monster
    UNION ALL
    SELECT 'art', 'varsel_art_chk', '%attestering_venter%'
  LOOP
    -- Moensteret er migrasjonenes EGET. Porten skal maale det 090/091
    -- faktisk spoer om — ikke noe som ligner.
    SELECT conname INTO v_navn FROM pg_constraint
     WHERE conrelid = 'public.varsel'::regclass
       AND pg_get_constraintdef(oid) LIKE r.monster;
    IF v_navn IS DISTINCT FROM r.forventet THEN
      RAISE EXCEPTION
        'varselenum: 090/091 ville plukket % i stedet for % — CHECKen'
        ' ligger fysisk etter NOT NULL-raden, og migrasjonen ville'
        ' stoppet midt i kjeden. Kjoer denne filen paa nytt.',
        coalesce(v_navn, '(ingen rad)'), r.forventet;
    END IF;
    RAISE NOTICE 'varselenum: %-oppslaget lander paa % — som 090/091 krever',
        r.kolonne, v_navn;
  END LOOP;
  -- Ingen RESET: `SET LOCAL` er transaksjonsskopet og faller bort naar
  -- blokken commiter. Et RESET her ville dessuten satt SESJONENS verdi,
  -- ikke den som gjaldt foer — altsaa en annen tilstand enn den vi laante.
END $$;
