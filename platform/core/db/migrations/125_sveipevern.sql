-- =====================================================================
-- 125 — SVEIPEVERNET. ET MENNESKES LUKKING SKAL STÅ.
-- =====================================================================
--
-- CODERABBIT FANT DET PÅ 124, OG DET VAR IKKE 124s FEIL ALENE:
--
--   «Update the sweep upsert logic … so manually closed findings
--   remain closed while their underlying state is unchanged.»
--
-- SVEIPEN GJENÅPNET HVER NATT ET FUNN ET MENNESKE HADDE LUKKET.
-- `DO UPDATE SET ... apen = true` var ubetinget. For funntypene sveipen
-- selv eier er det riktig — de lukkes bare av at tilstanden er borte.
-- For de LUKKBARE var det galt: «jeg har sett den, den skal forlenges»
-- var borte til neste morgen, og lukkeknappen var pynt.
--
-- OG PORTEN MIN BEKREFTET PYNTEN. Den målte at lukkedøra SVARTE
-- `apen = false`, og kjørte aldri sveipen etterpå.
--
-- ---------------------------------------------------------------------
-- FØRST EN RETTING AV MIN EGEN MÅLING.
--
-- `docs/FUNN-SVEIPEN-GJENAAPNER.md` (skrevet i 124) listet ti
-- migrasjoner. Den lista var GAL PÅ TRE MÅTER, og jeg fant det ved å
-- telle i stedet for å huske:
--
--   * 112, 113 og 114 sto på lista og hører ikke hjemme der. De har
--     INGEN lukkedør — ingen kan lukke et funn, og da er en ubetinget
--     gjenåpning ikke et brudd på noe.
--   * 120 (M-55) manglet på lista. Den treffer `merkevarevarsel`, ikke
--     `merkevarefunn`, og et navnesøk på «funn» gikk forbi den.
--   * Og det verste sto ikke der i det hele tatt: FIRE AV DEM HAR
--     INGEN `lukket_av`-KOLONNE. 116, 117, 118 og 119 tar imot en
--     `p_aktor` i lukkedøra, skriver den i revisjonsloggen og lar
--     RADEN være anonym. Ingen som leser funnlista ser hvem som
--     lukket — og sveipen kan umulig skille sin egen lukking fra et
--     menneskes, fordi opplysningen ikke finnes.
--
-- DET FAKTISKE OMFANGET ER NI TABELLER I NI MIGRASJONER: 116–124.
--
-- ---------------------------------------------------------------------
-- HVORFOR DETTE ER EN TRIGGER OG IKKE SYTTEN RETTELSER.
--
-- Den nærliggende fiksen er å skrive om `DO UPDATE`-blokken på hvert
-- av de sytten stedene. Det ville krevd at ni store sveipefunksjoner
-- ble gjenskapt ORDRETT bortsett fra én klausul — og gjenskaping av
-- tusen linjer for å endre fire er nøyaktig der nye feil kommer fra.
--
-- Verre: det ville rettet fortiden og ikke fremtiden. Modul nummer ti
-- kopierer sveipen fra modul nummer ni, og kopierer feilen med, slik
-- 116–124 alle kopierte den fra hverandre.
--
-- REGELEN BOR DERFOR I BASEN, ETT STED, PÅ RADEN. En trigger foran
-- hver UPDATE:
--
--   * går raden fra ÅPEN til LUKKET uten at noen navnga seg, stemples
--     sveipens navn på (etter §4 er sveipen den eneste skriveveien som
--     ikke navngir seg selv, og porten måler nettopp det);
--   * går den fra LUKKET til ÅPEN, avgjør `lukket_av` hva som skjer:
--     var det sveipens egen lukking, gjenåpnes den og sporet ryddes;
--     var det et menneske, STÅR LUKKINGEN.
--
-- TRIGGEREN RETTER STILLE, DEN FEILER IKKE. En exception her ville
-- drept hele nattens sveip på det første funnet noen hadde lukket —
-- vernet ville blitt et driftsavbrudd. Sveipen får skrive alt annet
-- den ville skrevet (`sist_sett_sveip`, `over_grense`, `detalj`), og
-- teller raden som oppdatert, fordi den ER oppdatert. Det eneste den
-- ikke får, er å gjøre om et menneskes beslutning.
--
-- BLIR TILSTANDEN VERRE, ER DET EN ANNEN FUNNTYPE og dermed en annen
-- rad: et lukket «nærmer seg» skjuler ikke et «passert».
--
-- ---------------------------------------------------------------------
-- TO FUNN TIL FRA SAMME RUNDE, BEGGE AV FAMILIEN «SER RIKTIG UT»:
--
--   1. `m50_kilde_gyldig` og `m47_regelverk_gyldig` er merket
--      IMMUTABLE og leser `current_date`. Planleggeren har LOV til å
--      folde en IMMUTABLE funksjon med konstante argumenter til en
--      konstant ved planlegging, og gjenbruke den i en bufret plan.
--      En kildeversjon kunne dermed stå som gyldig etter at den var
--      utløpt — i nettopp den sjekken som skal nekte en ny post mot et
--      avviklet format. `STABLE` er den riktige merkingen; 093 sier
--      det samme om sin egen `m4_...`-familie, og jeg skrev det likevel
--      feil to ganger.
--
--   2. `m50_lukk_funn` godtok en NULL aktør. Da ble
--      `apen = (apen OR lukket_av = 'm50_sveip')` til `false OR NULL`
--      = NULL, og `apen NOT NULL` felte HELE sveipetransaksjonen.
--      DETTE ER `cardinality(NULL)` OM IGJEN — samme NULL-form jeg
--      selv fant i 122 og skrev en port for, gjeninnført i selve
--      rettelsen. En sammenligning mot NULL gir NULL, ikke USANN, og
--      det slutter aldri å overraske meg.
--
--      Rettet i to lag: dørene NEKTER en tom aktør, og en CHECK gjør en
--      lukket rad uten `lukket_av` UREPRESENTERBAR. Da er uttrykket
--      totalt, og ikke bare riktig så lenge alle husker det.
-- =====================================================================

-- ---------------------------------------------------------------------
-- §1. KOLONNENE SOM MANGLET (116–119).
--
-- `lukket_av` og `lukkenotat` fantes i 120–124 og ikke i 116–119.
-- Dørene der tok imot begge og skrev dem BARE i revisjonsloggen. En
-- opplysning som bare finnes i evidenskjeden er en opplysning ingen
-- flate kan vise og ingen sveip kan lese.
-- ---------------------------------------------------------------------
ALTER TABLE motpartsfunn  ADD COLUMN lukket_av TEXT;
ALTER TABLE motpartsfunn  ADD COLUMN lukkenotat TEXT;
ALTER TABLE sanksjonsfunn ADD COLUMN lukket_av TEXT;
ALTER TABLE sanksjonsfunn ADD COLUMN lukkenotat TEXT;
ALTER TABLE anbudsfunn    ADD COLUMN lukket_av TEXT;
ALTER TABLE anbudsfunn    ADD COLUMN lukkenotat TEXT;
ALTER TABLE tilskuddsfunn ADD COLUMN lukket_av TEXT;
ALTER TABLE tilskuddsfunn ADD COLUMN lukkenotat TEXT;

-- ETTERFYLLING FØR VAKTEN. En rad som alt er lukket har ingen aktør å
-- oppgi, og den skal ikke DIKTES OPP. `ukjent_for_125` er et navn som
-- sier hva det er: lukket før kolonnen fantes. Alternativet — å
-- stemple sveipens navn på — ville gjort historikken uleselig ved å
-- påstå noe vi ikke vet.
UPDATE motpartsfunn  SET lukket_av = 'ukjent_for_125' WHERE NOT apen;
UPDATE sanksjonsfunn SET lukket_av = 'ukjent_for_125' WHERE NOT apen;
UPDATE anbudsfunn    SET lukket_av = 'ukjent_for_125' WHERE NOT apen;
UPDATE tilskuddsfunn SET lukket_av = 'ukjent_for_125' WHERE NOT apen;

-- ---------------------------------------------------------------------
-- §2. EN LUKKET RAD BÆRER ET NAVN.
--
-- Dette er laget som gjør NULL-formen i §0 punkt 2 umulig, ikke bare
-- usannsynlig.
--
-- SEKS AV NI FÅR DEN HER. De tre andre har den alt, og det er verdt å
-- skrive ned NØYAKTIG hvordan — første utgave av denne kommentaren
-- nevnte bare 121 og 122, og en leser (CodeRabbit) trodde derfor at
-- 120 manglet et gjerde den faktisk har:
--
--   120 `merkevarevarsel`: `apen = (lukket_ts IS NULL)` OG
--       `num_nulls(lukket_ts, lukket_av, lukkenotat) IN (0, 3)`. En
--       lukket rad har `lukket_ts`, altså num_nulls = 0, altså
--       `lukket_av` NOT NULL. `lukket_av ~ '[^[:space:]]'` stenger
--       for blanke.
--   121 `ehffunn` og 122 `tollfunn`: samme to CHECKer, samme slutning.
--
-- EN KOMMENTAR SOM UNDERVURDERER ET GJERDE ER IKKE UFARLIG. Neste
-- leser tror gjerdet mangler og bygger det på nytt — eller verre,
-- tror det finnes der det ikke gjør.
-- ---------------------------------------------------------------------

-- ETTERFYLLING FØR CHECKEN, RUNDE TO (CodeRabbit).
--
-- 123 og 124 tillater en lukket rad med `lukket_av IS NULL`:
-- `*_lukking`-CHECKen deres krever bare `lukket_ts`. Basen er tom på
-- staging, men en migrasjon som bare virker mot en tom base er ikke en
-- migrasjon — den er en antakelse. Uten disse to linjene ville
-- `ALTER TABLE ... ADD CONSTRAINT` feilet mot en base der noen ALT
-- hadde lukket et funn gjennom `m47_lukk_funn` uten aktør.
UPDATE myndighetsfunn SET lukket_av = 'ukjent_for_125'
 WHERE NOT apen AND (lukket_av IS NULL OR btrim(lukket_av) = '');
UPDATE journalfunn SET lukket_av = 'ukjent_for_125'
 WHERE NOT apen AND (lukket_av IS NULL OR btrim(lukket_av) = '');
ALTER TABLE motpartsfunn ADD CONSTRAINT motpartsfunn_lukket_har_navn
    CHECK (apen OR (lukket_av IS NOT NULL
                    AND lukket_av ~ '[^[:space:]]'));
ALTER TABLE sanksjonsfunn ADD CONSTRAINT sanksjonsfunn_lukket_har_navn
    CHECK (apen OR (lukket_av IS NOT NULL
                    AND lukket_av ~ '[^[:space:]]'));
ALTER TABLE anbudsfunn ADD CONSTRAINT anbudsfunn_lukket_har_navn
    CHECK (apen OR (lukket_av IS NOT NULL
                    AND lukket_av ~ '[^[:space:]]'));
ALTER TABLE tilskuddsfunn ADD CONSTRAINT tilskuddsfunn_lukket_har_navn
    CHECK (apen OR (lukket_av IS NOT NULL
                    AND lukket_av ~ '[^[:space:]]'));
ALTER TABLE myndighetsfunn ADD CONSTRAINT myndighetsfunn_lukket_har_navn
    CHECK (apen OR (lukket_av IS NOT NULL
                    AND lukket_av ~ '[^[:space:]]'));
ALTER TABLE journalfunn ADD CONSTRAINT journalfunn_lukket_har_navn
    CHECK (apen OR (lukket_av IS NOT NULL
                    AND lukket_av ~ '[^[:space:]]'));

-- ---------------------------------------------------------------------
-- §3. VAKTEN.
--
-- ÉN funksjon for ni tabeller med ulik form: 116–119 har `lukkenotat`
-- først fra §1, 120–124 har den fra fødselen, og kolonnenavnene rundt
-- er forskjellige. `to_jsonb`/`jsonb_populate_record` gjør vakten
-- TABELLUAVHENGIG uten dynamisk SQL — den rører nøyaktig de fire
-- nøklene den kjenner, og lar resten av raden være.
--
-- TG_ARGV[0] er sveipens navn for tabellen. Det står i CREATE TRIGGER
-- under, ett sted per tabell, og er den ENESTE parameteren vakten har.
-- ---------------------------------------------------------------------
CREATE FUNCTION sveipefunn_lukkevern()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE
    v_sveip TEXT := TG_ARGV[0];
    v_ny JSONB;
BEGIN
    -- LUKKING UTEN NAVN ER SVEIPENS.
    --
    -- Etter §4 navngir hver menneskelig lukkedør seg selv. Den eneste
    -- skriveveien som lukker uten å navngi seg er da sveipens egen
    -- lukkegren i 116–119 — den ble skrevet før kolonnen fantes.
    -- Antakelsen er ikke gratis, og den MÅLES: porten lukker som
    -- menneske og som sveip og leser navnet på raden begge veier.
    IF OLD.apen AND NOT NEW.apen AND NEW.lukket_av IS NULL THEN
        v_ny := to_jsonb(NEW) || jsonb_build_object('lukket_av',
                                                    v_sveip);
        NEW := jsonb_populate_record(NEW, v_ny);
        RETURN NEW;
    END IF;

    IF NOT OLD.apen AND NEW.apen THEN
        IF OLD.lukket_av IS DISTINCT FROM v_sveip THEN
            -- ET MENNESKES LUKKING STÅR. Alt annet sveipen skrev blir
            -- stående — bare selve gjenåpningen rulles tilbake.
            v_ny := to_jsonb(NEW) || jsonb_build_object(
                'apen', false,
                'lukket_ts', to_jsonb(OLD)->'lukket_ts',
                'lukket_av', to_jsonb(OLD)->'lukket_av');
            IF to_jsonb(NEW) ? 'lukkenotat' THEN
                v_ny := v_ny || jsonb_build_object(
                    'lukkenotat', to_jsonb(OLD)->'lukkenotat');
            END IF;
            IF to_jsonb(NEW) ? 'lukkebegrunnelse' THEN
                v_ny := v_ny || jsonb_build_object(
                    'lukkebegrunnelse',
                    to_jsonb(OLD)->'lukkebegrunnelse');
            END IF;
        ELSE
            -- SVEIPENS EGEN LUKKING BETYR «tilstanden var borte». Er
            -- tilstanden tilbake, er funnet tilbake — og sporet skal
            -- ryddes, ellers står gårsdagens lukkenotat på et åpent
            -- funn.
            v_ny := to_jsonb(NEW) || jsonb_build_object(
                'lukket_ts', NULL, 'lukket_av', NULL);
            IF to_jsonb(NEW) ? 'lukkenotat' THEN
                v_ny := v_ny || jsonb_build_object('lukkenotat', NULL);
            END IF;
            IF to_jsonb(NEW) ? 'lukkebegrunnelse' THEN
                v_ny := v_ny || jsonb_build_object('lukkebegrunnelse',
                                                   NULL);
            END IF;
        END IF;
        NEW := jsonb_populate_record(NEW, v_ny);
    END IF;
    RETURN NEW;
END $$;

COMMENT ON FUNCTION sveipefunn_lukkevern() IS
    'Et menneskes lukking av et sveipefunn skal stå natten over. '
    'TG_ARGV[0] er sveipens navn for tabellen. Se 125 og '
    'docs/FUNN-SVEIPEN-GJENAAPNER.md.';

CREATE TRIGGER motpartsfunn_lukkevern BEFORE UPDATE ON motpartsfunn
    FOR EACH ROW EXECUTE FUNCTION sveipefunn_lukkevern('m48_sveip');
CREATE TRIGGER sanksjonsfunn_lukkevern BEFORE UPDATE ON sanksjonsfunn
    FOR EACH ROW EXECUTE FUNCTION sveipefunn_lukkevern('m49_sveip');
CREATE TRIGGER anbudsfunn_lukkevern BEFORE UPDATE ON anbudsfunn
    FOR EACH ROW EXECUTE FUNCTION sveipefunn_lukkevern('m46_sveip');
CREATE TRIGGER tilskuddsfunn_lukkevern BEFORE UPDATE ON tilskuddsfunn
    FOR EACH ROW EXECUTE FUNCTION sveipefunn_lukkevern('m51_sveip');
CREATE TRIGGER merkevarevarsel_lukkevern BEFORE UPDATE
    ON merkevarevarsel FOR EACH ROW
    EXECUTE FUNCTION sveipefunn_lukkevern('m55_sveip_merkevare');
CREATE TRIGGER ehffunn_lukkevern BEFORE UPDATE ON ehffunn
    FOR EACH ROW EXECUTE FUNCTION sveipefunn_lukkevern('m54_sveip_ehf');
CREATE TRIGGER tollfunn_lukkevern BEFORE UPDATE ON tollfunn
    FOR EACH ROW
    EXECUTE FUNCTION sveipefunn_lukkevern('m52_sveip_tollkode');
CREATE TRIGGER myndighetsfunn_lukkevern BEFORE UPDATE ON myndighetsfunn
    FOR EACH ROW EXECUTE FUNCTION sveipefunn_lukkevern('m47_sveip');
CREATE TRIGGER journalfunn_lukkevern BEFORE UPDATE ON journalfunn
    FOR EACH ROW EXECUTE FUNCTION sveipefunn_lukkevern('m50_sveip');

-- ---------------------------------------------------------------------
-- §4. LUKKEDØRENE SOM IKKE SKREV NAVNET SITT (116-119).
--
-- Alle fire tok imot `p_aktor` og `p_notat`, og skrev dem BARE i
-- revisjonsloggen. Radene ble anonyme, og vakten i §3 ville ikke hatt
-- noe å skille et menneske fra sveipen med.
--
-- Kroppene er 116-119s EGNE, ordrett, med nøyaktig tre tillegg: nekt
-- av tom aktør, `lukket_av` og `lukkenotat` i UPDATE-en. Alt annet -
-- funntypenektene, idempotensen, evidenskallet - står som det sto.
-- ---------------------------------------------------------------------

SET LOCAL ROLE disponit_motpart_eier;
CREATE OR REPLACE FUNCTION m48_lukk_funn(
    p_tenant TEXT, p_motpart_id UUID, p_funntype TEXT,
    p_notat TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_apen BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm48_lukk_funn');

    -- EN TOM AKTØR ER IKKE EN AKTØR (125). Uten dette kunne raden
    -- lukkes uten et navn, og §2s CHECK ville felt setningen med en
    -- melding om en constraint i stedet for om det som mangler.
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm48_lukk_funn: en lukking bærer navnet'
            ' til den som lukket'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    PERFORM set_config('disponit.aktor', p_aktor, true);

    IF length(btrim(coalesce(p_notat, ''))) < 4 THEN
        RAISE EXCEPTION 'm48_lukk_funn: et funn lukkes med en'
            ' begrunnelse, ikke med et klikk'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT apen INTO v_apen FROM public.motpartsfunn
     WHERE tenant = p_tenant AND motpart_id = p_motpart_id
       AND funntype = p_funntype
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm48_lukk_funn: ukjent funn %/%',
            p_motpart_id, p_funntype USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_apen THEN
        RETURN;  -- idempotent
    END IF;

    UPDATE public.motpartsfunn
       SET apen = false, lukket_ts = now(),
           lukket_av = btrim(p_aktor),
           lukkenotat = btrim(p_notat)
     WHERE tenant = p_tenant AND motpart_id = p_motpart_id
       AND funntype = p_funntype;

    PERFORM public.m48_evidens(p_tenant, p_motpart_id, 'funn_lukket',
        p_aktor, jsonb_build_object('funntype', p_funntype,
                                    'notat', btrim(p_notat)));
END $$;
RESET ROLE;

SET LOCAL ROLE disponit_sanksjon_eier;
CREATE OR REPLACE FUNCTION m49_lukk_funn(
    p_tenant TEXT, p_subjekt_id UUID, p_funntype TEXT,
    p_notat TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_apen BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm49_lukk_funn');

    -- EN TOM AKTØR ER IKKE EN AKTØR (125). Uten dette kunne raden
    -- lukkes uten et navn, og §2s CHECK ville felt setningen med en
    -- melding om en constraint i stedet for om det som mangler.
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm49_lukk_funn: en lukking bærer navnet'
            ' til den som lukket'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    PERFORM set_config('disponit.aktor', p_aktor, true);

    IF length(btrim(coalesce(p_notat, ''))) < 4 THEN
        RAISE EXCEPTION 'm49_lukk_funn: et funn lukkes med en'
            ' begrunnelse, ikke med et klikk'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- ET BEKREFTET TREFF LUKKES IKKE HER, OG DET ER MODULENS
    -- SKARPESTE NEKT. `bekreftet_treff` betyr at et menneske har sagt
    -- at parten ER sanksjonert. Kunne det funnet lukkes med et notat,
    -- ville modulen tilbudt en knapp for å gjøre den observasjonen
    -- borte — og den knappen er farligere enn manglende blokkering,
    -- fordi den ser ut som saksbehandling.
    IF p_funntype = 'bekreftet_treff' THEN
        RAISE EXCEPTION 'm49_lukk_funn: et bekreftet sanksjonstreff'
            ' kan ikke lukkes bort. Det lukkes når subjektet'
            ' deaktiveres eller når en ny kontroll mot en ny'
            ' listeversjon ikke lenger gir treffet'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT apen INTO v_apen FROM public.sanksjonsfunn
     WHERE tenant = p_tenant AND subjekt_id = p_subjekt_id
       AND funntype = p_funntype
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm49_lukk_funn: ukjent funn %/%',
            p_subjekt_id, p_funntype USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_apen THEN
        RETURN;  -- idempotent
    END IF;

    UPDATE public.sanksjonsfunn
       SET apen = false, lukket_ts = now(),
           lukket_av = btrim(p_aktor),
           lukkenotat = btrim(p_notat)
     WHERE tenant = p_tenant AND subjekt_id = p_subjekt_id
       AND funntype = p_funntype;

    PERFORM public.m49_evidens(p_tenant, p_subjekt_id, 'funn_lukket',
        p_aktor, jsonb_build_object('funntype', p_funntype,
                                    'notat', btrim(p_notat)));
END $$;
RESET ROLE;

SET LOCAL ROLE disponit_anbud_eier;
CREATE OR REPLACE FUNCTION m46_lukk_funn(
    p_tenant TEXT, p_anbud_id UUID, p_funntype TEXT, p_notat TEXT,
    p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_apen BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm46_lukk_funn');

    -- EN TOM AKTØR ER IKKE EN AKTØR (125). Uten dette kunne raden
    -- lukkes uten et navn, og §2s CHECK ville felt setningen med en
    -- melding om en constraint i stedet for om det som mangler.
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm46_lukk_funn: en lukking bærer navnet'
            ' til den som lukket'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    PERFORM set_config('disponit.aktor', p_aktor, true);

    IF length(btrim(coalesce(p_notat, ''))) < 4 THEN
        RAISE EXCEPTION 'm46_lukk_funn: et funn lukkes med en'
            ' begrunnelse, ikke med et klikk'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- ET UDEKKET ABSOLUTT KRAV LUKKES IKKE HER, av samme grunn som
    -- M-49s bekreftede treff (117): et absolutt krav uten
    -- dokumentasjon fører til avvisning av tilbudet, og en knapp som
    -- gjorde den observasjonen borte ville sett ut som saksbehandling.
    -- Funnet lukkes når kravet FAKTISK dekkes, eller når anbudet
    -- deaktiveres fordi vi ikke går for det.
    IF p_funntype = 'udekket_absolutt_krav' THEN
        RAISE EXCEPTION 'm46_lukk_funn: et udekket ABSOLUTT krav kan'
            ' ikke lukkes bort. Det lukkes når kravet dekkes av et'
            ' punkt med kilde, eller når anbudet deaktiveres'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT apen INTO v_apen FROM public.anbudsfunn
     WHERE tenant = p_tenant AND anbud_id = p_anbud_id
       AND funntype = p_funntype FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm46_lukk_funn: ukjent funn %/%', p_anbud_id,
            p_funntype USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_apen THEN
        RETURN;  -- idempotent
    END IF;

    UPDATE public.anbudsfunn
       SET apen = false, lukket_ts = now(),
           lukket_av = btrim(p_aktor),
           lukkenotat = btrim(p_notat)
     WHERE tenant = p_tenant AND anbud_id = p_anbud_id
       AND funntype = p_funntype;

    PERFORM public.m46_evidens(p_tenant, p_anbud_id, 'funn_lukket',
        p_aktor, jsonb_build_object('funntype', p_funntype,
                                    'notat', btrim(p_notat)));
END $$;
RESET ROLE;

SET LOCAL ROLE disponit_tilskudd_eier;
CREATE OR REPLACE FUNCTION m51_lukk_funn(
    p_tenant TEXT, p_ordning_id UUID, p_funntype TEXT, p_notat TEXT,
    p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_apen BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm51_lukk_funn');

    -- EN TOM AKTØR ER IKKE EN AKTØR (125). Uten dette kunne raden
    -- lukkes uten et navn, og §2s CHECK ville felt setningen med en
    -- melding om en constraint i stedet for om det som mangler.
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm51_lukk_funn: en lukking bærer navnet'
            ' til den som lukket'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    PERFORM set_config('disponit.aktor', p_aktor, true);

    IF length(btrim(coalesce(p_notat, ''))) < 4 THEN
        RAISE EXCEPTION 'm51_lukk_funn: et funn lukkes med en'
            ' begrunnelse, ikke med et klikk'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- ET ESTIMAT OVER ORDNINGENS TAK LUKKES IKKE HER, av samme grunn
    -- som M-46s udekkede absolutte krav (118) og M-49s bekreftede
    -- treff (117): et estimat som overstiger taket vil bli avkortet
    -- eller avslått, og en knapp som gjorde den observasjonen borte
    -- ville sett ut som saksbehandling. Funnet lukkes når summen
    -- FAKTISK kommer under taket, eller når ordningen deaktiveres.
    IF p_funntype = 'estimat_over_ordningstak' THEN
        RAISE EXCEPTION 'm51_lukk_funn: et estimat over ordningens tak'
            ' kan ikke lukkes bort. Det lukkes når summen kommer under'
            ' taket, eller når ordningen deaktiveres'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT apen INTO v_apen FROM public.tilskuddsfunn
     WHERE tenant = p_tenant AND ordning_id = p_ordning_id
       AND funntype = p_funntype FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm51_lukk_funn: ukjent funn %/%', p_ordning_id,
            p_funntype USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_apen THEN
        RETURN;  -- idempotent
    END IF;

    UPDATE public.tilskuddsfunn
       SET apen = false, lukket_ts = now(),
           lukket_av = btrim(p_aktor),
           lukkenotat = btrim(p_notat)
     WHERE tenant = p_tenant AND ordning_id = p_ordning_id
       AND funntype = p_funntype;

    PERFORM public.m51_evidens(p_tenant, p_ordning_id, 'funn_lukket',
        p_aktor, jsonb_build_object('funntype', p_funntype,
                                    'notat', btrim(p_notat)));
END $$;
RESET ROLE;

-- ---------------------------------------------------------------------
-- §5. VOLATILITETEN. `IMMUTABLE` PÅ EN FUNKSJON SOM LESER KLOKKA.
--
-- Begge leser `current_date`. Planleggeren har LOV til å folde en
-- IMMUTABLE funksjon med konstante argumenter til en konstant ved
-- planlegging og gjenbruke resultatet i en bufret plan - og da kan en
-- kildeversjon stå som gyldig etter at den er utløpt, i nettopp den
-- sjekken som skal nekte en ny post mot et avviklet format.
--
-- `STABLE` er den riktige merkingen: samme svar innenfor én setning,
-- ikke på tvers av dager. Kroppene er uendret; bare løftet til
-- planleggeren er rettet.
--
-- AVHENGIGHETENE ER SJEKKET FØR BYTTET, og det er ikke en formalitet:
-- PostgreSQL krever IMMUTABLE i et indeksuttrykk og i en lagret
-- generert kolonne, og `CREATE OR REPLACE` NEKTER IKKE et bytte som
-- gjør en slik indeks ugyldig — den ville bare stått der og løyet.
-- Ingen indeks og ingen generert kolonne bruker noen av de to; det er
-- målt mot katalogen, ikke antatt, og porten
-- `test_volatilitetsbyttet_river_ingen_indeks` måler det samme.
-- ---------------------------------------------------------------------

CREATE OR REPLACE FUNCTION m47_regelverk_gyldig(p_fra DATE, p_til DATE)
RETURNS BOOLEAN LANGUAGE sql STABLE
SET search_path = pg_catalog AS $$
    SELECT p_fra <= current_date
       AND (p_til IS NULL OR p_til >= current_date)
$$;

CREATE OR REPLACE FUNCTION m50_kilde_gyldig(p_fra DATE, p_til DATE)
RETURNS BOOLEAN LANGUAGE sql STABLE
SET search_path = pg_catalog AS $$
    SELECT p_fra <= current_date
       AND (p_til IS NULL OR p_til >= current_date)
$$;

-- ---------------------------------------------------------------------
-- §6. NULL-AKTØREN, DØRENE. Alle seks lukkedørene i 120-124.
--
-- CodeRabbits andre funn på 124: `m50_lukk_funn` godtok en NULL aktør,
-- og da ble `apen = (apen OR lukket_av = 'm50_sveip')` til NULL. Med
-- §2 er raden umulig; her sier døra det tydelig i stedet for å la en
-- CHECK gjøre det.
--
-- 121 og 122 har alt `num_nulls(...) IN (0, 3)` og var ikke sårbare,
-- men får nektet likevel: forskjellen mellom «feilet på en constraint»
-- og «du oppga ingen aktør» er forskjellen mellom en logglinje og et
-- svar den som ringer kan gjøre noe med.
-- ---------------------------------------------------------------------

SET LOCAL ROLE disponit_merkevare_eier;
CREATE OR REPLACE FUNCTION m55_lukk_funn(
    p_tenant TEXT, p_funn_id UUID, p_begrunnelse TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_lukket TIMESTAMPTZ; v_henvist UUID; v_merkevare_id UUID;
    v_over BOOLEAN; v_likhet INT; v_terskel INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm55_lukk_funn');

    -- EN TOM AKTØR ER IKKE EN AKTØR (125).
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm55_lukk_funn: en lukking bærer navnet'
            ' til den som lukket'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    PERFORM set_config('disponit.aktor', p_aktor, true);

    -- LÅS, SÅ LES. Et `m55_vurder_funn` eller `m55_henvis_funn` som
    -- committer mens vi venter på låsen er ellers usynlig — og da
    -- ville et funn som NETTOPP ble målt over terskelen blitt lukket
    -- uten henvisning, som er nøyaktig det denne døra finnes for.
    PERFORM 1 FROM public.merkevarefunn
     WHERE tenant = p_tenant AND funn_id = p_funn_id FOR UPDATE;
    SELECT f.lukket_ts, f.henvist_unntak_id, f.merkevare_id
      INTO v_lukket, v_henvist, v_merkevare_id
      FROM public.merkevarefunn f
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;
    IF v_merkevare_id IS NULL THEN
        RAISE EXCEPTION 'm55_lukk_funn: ukjent funn %', p_funn_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_lukket IS NOT NULL THEN
        RAISE EXCEPTION 'm55_lukk_funn: funnet % er alt lukket',
            p_funn_id USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- NYESTE VURDERING, ikke en vilkårlig av dem.
    SELECT v.over_terskel, v.likhet, v.terskel_brukt
      INTO v_over, v_likhet, v_terskel
      FROM public.forvekslingsvurdering v
     WHERE v.tenant = p_tenant AND v.funn_id = p_funn_id
     ORDER BY v.vurdert DESC, v.vurdering_id DESC
     LIMIT 1;

    IF coalesce(v_over, false) AND v_henvist IS NULL THEN
        -- «prosent» skrevet ut, ikke «%%»: i RAISE er `%` en
        -- plassholder, og `%%%` leses som literal-prosent FULGT AV
        -- plassholder — altså i motsatt rekkefølge av det man mente.
        RAISE EXCEPTION 'm55_lukk_funn: funnet % er vurdert til %'
            ' prosent likhet mot en terskel på % prosent, og er ikke'
            ' henvist. Et funn over tenantens egen terskel lukkes'
            ' ikke her — det henvises til unntakskøen, og et menneske'
            ' beslutter',
            p_funn_id, v_likhet, v_terskel
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.merkevarefunn
       SET lukket_ts = now(), lukket_av = p_aktor,
           lukkebegrunnelse = btrim(p_begrunnelse)
     WHERE tenant = p_tenant AND funn_id = p_funn_id;

    PERFORM public.m55_evidens(p_tenant, v_merkevare_id,
        'merkevarefunn_lukket', p_aktor, jsonb_build_object(
            'funn_id', p_funn_id::text, 'likhet', v_likhet,
            'var_henvist', v_henvist IS NOT NULL));
END $$;
RESET ROLE;

SET LOCAL ROLE disponit_merkevare_eier;
CREATE OR REPLACE FUNCTION m55_lukk_varsel(
    p_tenant TEXT, p_varsel_id UUID, p_notat TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_type TEXT; v_apen BOOLEAN; v_merkevare_id UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm55_lukk_varsel');

    -- EN TOM AKTØR ER IKKE EN AKTØR (125).
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm55_lukk_varsel: en lukking bærer navnet'
            ' til den som lukket'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    PERFORM set_config('disponit.aktor', p_aktor, true);

    PERFORM 1 FROM public.merkevarevarsel
     WHERE tenant = p_tenant AND varsel_id = p_varsel_id FOR UPDATE;
    SELECT w.varseltype, w.apen, w.merkevare_id
      INTO v_type, v_apen, v_merkevare_id
      FROM public.merkevarevarsel w
     WHERE w.tenant = p_tenant AND w.varsel_id = p_varsel_id;
    IF v_type IS NULL THEN
        RAISE EXCEPTION 'm55_lukk_varsel: ukjent varsel %',
            p_varsel_id USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_apen THEN
        RAISE EXCEPTION 'm55_lukk_varsel: varselet % er alt lukket',
            p_varsel_id USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_type = 'forveksling_ikke_henvist' THEN
        RAISE EXCEPTION 'm55_lukk_varsel: % kan ikke lukkes. En'
            ' forveksling over tenantens egen terskel som ingen har'
            ' sett på er nøyaktig det modulen finnes for å vise —'
            ' varselet forsvinner når funnet henvises, ikke før',
            v_type USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.merkevarevarsel
       SET apen = false, lukket_ts = now(), lukket_av = p_aktor,
           lukkenotat = btrim(p_notat)
     WHERE tenant = p_tenant AND varsel_id = p_varsel_id;

    PERFORM public.m55_evidens(p_tenant, v_merkevare_id,
        'merkevarevarsel_lukket', p_aktor,
        jsonb_build_object('varseltype', v_type));
END $$;
RESET ROLE;

SET LOCAL ROLE disponit_ehf_eier;
CREATE OR REPLACE FUNCTION m54_lukk_funn(
    p_tenant TEXT, p_funn_id UUID, p_notat TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_type TEXT; v_apen BOOLEAN; v_dokument_id UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm54_lukk_funn');

    -- EN TOM AKTØR ER IKKE EN AKTØR (125).
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm54_lukk_funn: en lukking bærer navnet'
            ' til den som lukket'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    PERFORM set_config('disponit.aktor', p_aktor, true);

    PERFORM 1 FROM public.ehffunn
     WHERE tenant = p_tenant AND funn_id = p_funn_id FOR UPDATE;
    SELECT f.funntype, f.apen, f.dokument_id
      INTO v_type, v_apen, v_dokument_id
      FROM public.ehffunn f
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;
    IF v_type IS NULL THEN
        RAISE EXCEPTION 'm54_lukk_funn: ukjent funn %', p_funn_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_apen THEN
        RAISE EXCEPTION 'm54_lukk_funn: funnet % er alt lukket',
            p_funn_id USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_type = 'validering_mot_utlopt_regelsett' THEN
        RAISE EXCEPTION 'm54_lukk_funn: % kan ikke lukkes. Dommen ble'
            ' felt under en regel som siden har gått ut, og funnet'
            ' forsvinner når dokumentet valideres på nytt mot et'
            ' gyldig sett — det er en handling, ikke en mening',
            v_type USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.ehffunn
       SET apen = false, lukket_ts = now(), lukket_av = p_aktor,
           lukkenotat = btrim(p_notat)
     WHERE tenant = p_tenant AND funn_id = p_funn_id;

    PERFORM public.m54_evidens(p_tenant, v_dokument_id,
        'ehffunn_lukket', p_aktor,
        jsonb_build_object('funntype', v_type));
END $$;
RESET ROLE;

SET LOCAL ROLE disponit_tollkode_eier;
CREATE OR REPLACE FUNCTION m52_lukk_funn(
    p_tenant TEXT, p_funn_id UUID, p_notat TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_type TEXT; v_apen BOOLEAN; v_vare_id UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm52_lukk_funn');

    -- EN TOM AKTØR ER IKKE EN AKTØR (125).
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm52_lukk_funn: en lukking bærer navnet'
            ' til den som lukket'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    PERFORM set_config('disponit.aktor', p_aktor, true);

    PERFORM 1 FROM public.tollfunn
     WHERE tenant = p_tenant AND funn_id = p_funn_id FOR UPDATE;
    SELECT f.funntype, f.apen, f.vare_id
      INTO v_type, v_apen, v_vare_id
      FROM public.tollfunn f
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;
    IF v_type IS NULL THEN
        RAISE EXCEPTION 'm52_lukk_funn: ukjent funn %', p_funn_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_apen THEN
        RAISE EXCEPTION 'm52_lukk_funn: funnet % er alt lukket',
            p_funn_id USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_type = 'forslag_mot_utlopt_nomenklatur' THEN
        RAISE EXCEPTION 'm52_lukk_funn: % kan ikke lukkes. Forslaget'
            ' hviler på en nomenklatur som siden er avviklet, og'
            ' funnet forsvinner når varen klassifiseres på nytt mot'
            ' en gyldig versjon — det er en handling, ikke en mening',
            v_type USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.tollfunn
       SET apen = false, lukket_ts = now(), lukket_av = p_aktor,
           lukkenotat = btrim(p_notat)
     WHERE tenant = p_tenant AND funn_id = p_funn_id;

    PERFORM public.m52_evidens(p_tenant, v_vare_id,
        'tollfunn_lukket', p_aktor,
        jsonb_build_object('funntype', v_type));
END $$;
RESET ROLE;

SET LOCAL ROLE disponit_myndighet_eier;
CREATE OR REPLACE FUNCTION m47_lukk_funn(p_tenant TEXT, p_funn_id UUID,
                              p_notat TEXT, p_aktor TEXT)
RETURNS TABLE (funn_id UUID, apen BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_type TEXT;
    v_plikt UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm47_lukk_funn');

    -- EN TOM AKTØR ER IKKE EN AKTØR (125).
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm47_lukk_funn: en lukking bærer navnet'
            ' til den som lukket'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF p_notat IS NULL OR length(btrim(p_notat)) < 4 THEN
        RAISE EXCEPTION 'm47_lukk_funn: lukkingen krever et notat'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT f.funntype, f.rapportplikt_id INTO v_type, v_plikt
      FROM public.myndighetsfunn f
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id
       FOR UPDATE;
    IF v_type IS NULL THEN
        RAISE EXCEPTION 'm47_lukk_funn: ukjent funn %', p_funn_id
            USING ERRCODE = 'no_data_found';
    END IF;

    IF public.m47_funn_er_sveipens(v_type) THEN
        RAISE EXCEPTION 'm47_lukk_funn: % kan ikke lukkes for hånd.'
            ' Det forsvinner når tilstanden er borte — plikten'
            ' registrert på nytt mot gjeldende regelverk, eller et'
            ' bevis på at noen faktisk sendte inn. Det er en handling,'
            ' ikke en mening', v_type
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.myndighetsfunn f
       SET apen = false, lukket_ts = now(), lukket_av = p_aktor,
           lukkenotat = btrim(p_notat)
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;

    PERFORM public.m47_evidens(p_tenant, v_plikt, 'funn_lukket',
        p_aktor, jsonb_build_object('funn_id', p_funn_id::text,
                                    'funntype', v_type,
                                    'notat', btrim(p_notat)));

    RETURN QUERY SELECT f.funn_id, f.apen FROM public.myndighetsfunn f
                  WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;
END $$;
RESET ROLE;

SET LOCAL ROLE disponit_postjournal_eier;
CREATE OR REPLACE FUNCTION m50_lukk_funn(p_tenant TEXT, p_funn_id UUID,
                              p_notat TEXT, p_aktor TEXT)
RETURNS TABLE (funn_id UUID, apen BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_type TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm50_lukk_funn');

    -- EN TOM AKTØR ER IKKE EN AKTØR (125).
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm50_lukk_funn: en lukking bærer navnet'
            ' til den som lukket'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF p_notat IS NULL OR length(btrim(p_notat)) < 4 THEN
        RAISE EXCEPTION 'm50_lukk_funn: lukkingen krever et notat'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT f.funntype INTO v_type FROM public.journalfunn f
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id
       FOR UPDATE;
    IF v_type IS NULL THEN
        RAISE EXCEPTION 'm50_lukk_funn: ukjent funn %', p_funn_id
            USING ERRCODE = 'no_data_found';
    END IF;

    IF public.m50_funn_er_sveipens(v_type) THEN
        RAISE EXCEPTION 'm50_lukk_funn: % kan ikke lukkes for hånd.'
            ' Det forsvinner når tilstanden er borte — posten'
            ' registrert på nytt mot gjeldende kildeversjon, eller'
            ' personopplysningen faktisk anonymisert. Det er en'
            ' handling, ikke en mening', v_type
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.journalfunn f
       SET apen = false, lukket_ts = now(), lukket_av = p_aktor,
           lukkenotat = btrim(p_notat)
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;

    PERFORM public.m50_evidens(p_tenant, NULL, 'funn_lukket', p_aktor,
        jsonb_build_object('funn_id', p_funn_id::text,
                           'funntype', v_type,
                           'notat', btrim(p_notat)));

    RETURN QUERY SELECT f.funn_id, f.apen FROM public.journalfunn f
                  WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;
END $$;
RESET ROLE;

-- ---------------------------------------------------------------------
-- §7. DET JEG IKKE GJØR, OG HVORFOR.
--
-- CodeRabbit ba også om `IS NOT DISTINCT FROM` på 124s tre
-- `DO UPDATE`-steder. Det ville krevd at `m50_sveip_postjournal` ble
-- gjenskapt i sin helhet - over tre hundre linjer kopiert for å endre
-- tre sammenligninger, i en fil der en enkelt uteglemt linje er en ny
-- feil ingen port leter etter.
--
-- MED §2 ER SAMMENLIGNINGEN TOTAL. `lukket_av` kan ikke være NULL på
-- en lukket rad, og på en åpen rad gir `apen OR ...` sant uansett hva
-- høyresiden svarer. NULL-en CodeRabbit beskrev kan ikke oppstå.
--
-- Det er en påstand, og påstander måles: porten setter en lukket rad
-- uten `lukket_av` direkte som eier og krever at CHECKen feller den.
--
-- DE NI SVEIPEFUNKSJONENE RØRES IKKE. Vakten i §3 gjør dem riktige
-- uten at en eneste av dem endres - og den gjør nummer ti riktig også,
-- den som ennå ikke er skrevet.
-- ---------------------------------------------------------------------
