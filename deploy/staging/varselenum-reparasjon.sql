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
-- Fra PostgreSQL 17 har en NOT NULL-begrensning sin EGEN rad i
-- pg_constraint (contype 'n'), og definisjonen er bokstavelig
-- «NOT NULL ressurs_type». Sporringen treffer derfor TO rader, og
-- SELECT ... INTO uten ORDER BY plukker en vilkaarlig av dem.
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
      'pliktfrist'];
  v_ressurs TEXT[] := ARRAY[
      'policyutkast',
      'modultoken',
      'domene',
      'plan',
      'backupverifisering',
      'selvtest',
      'plikt'];
  r RECORD; v_def TEXT; v_onsket TEXT; v_ulovlige TEXT;
BEGIN
  IF to_regclass('public.varsel') IS NULL THEN
    RAISE NOTICE 'varselenum: tabellen varsel finnes ikke ennaa -- hopper'
        ' over (fersk base, kjeden lager den kanonisk)';
    RETURN;
  END IF;
  FOR r IN
    SELECT 'varsel_art_chk' AS navn, 'art' AS kolonne, v_art AS sett
    UNION ALL
    SELECT 'varsel_ressurs_type_chk', 'ressurs_type', v_ressurs
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
    CONTINUE WHEN v_def = v_onsket;

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

    EXECUTE format('ALTER TABLE public.varsel DROP CONSTRAINT %I', r.navn);
    EXECUTE format('ALTER TABLE public.varsel ADD CONSTRAINT %I %s',
                   r.navn, v_onsket);
    RAISE NOTICE 'varselenum: % kanonisert', r.navn;
  END LOOP;
END $$;
