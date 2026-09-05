-- =====================================================================
-- 126 — VAKTEN MÅ SLIPPE SIN EGEN FORTID INN IGJEN.
-- =====================================================================
--
-- CODERABBIT FANT DET PÅ 125, ETTER MERGE, OG DET ER ALVORLIG:
--
--   «The `ukjent_for_125` backfill makes pre-existing sweep closures
--    permanent. … When the underlying state returns, the sweep sets
--    `apen = true`, the guard reverts it, and the finding stays closed
--    permanently.»
--
-- DETTE ER SPEILBILDET AV FEILEN 125 RETTET, OG DET ER DEN VERRE AV
-- DE TO.
--
-- I 116–119 lukket sveipen selv når tilstanden var borte. De radene
-- kunne ikke bære et navn — kolonnen fantes ikke — så 125 etterfylte
-- dem med `ukjent_for_125`. Vakten sammenligner mot sveipens navn og
-- leste dermed HVER av dem som et menneskes lukking. Kommer tilstanden
-- tilbake, gjenåpner sveipen funnet, vakten ruller det tilbake, og
-- funnet er lukket FOR ALLTID.
--
-- 125 rettet at sveipen overkjørte et menneske. Dette er at en gammel
-- SVEIPELUKKING overkjører virkeligheten — og forskjellen på de to
-- skadene er hele modulens dom:
--
--   Et funn som gjenåpnes for ivrig er en irritasjon noen lukker igjen.
--   ET FUNN SOM ALDRI KOMMER TILBAKE ER STILLHET.
--
-- ---------------------------------------------------------------------
-- HVORFOR SENTINELEN IKKE ERSTATTES MED SVEIPENS NAVN.
--
-- Den nærliggende fiksen er å etterfylle med `m48_sveip` i stedet, så
-- vakten kjenner den igjen. Det ville vært å PÅSTÅ at sveipen lukket
-- raden — og i 116–119 kunne ET MENNESKE også lukke uten å etterlate
-- et navn. Vi VET ikke hvem det var. `ukjent_for_125` sier nettopp
-- det, og skal fortsette å si det.
--
-- Det vi kan velge, er hva vakten GJØR med en rad vi ikke vet noe om,
-- og valget følger av skadene over: den slippes inn igjen. Prisen er
-- at et menneskes lukking fra før 125 kan komme tilbake ÉN gang, og
-- da lukkes den på nytt — denne gangen med et navn på raden.
--
-- INGEN RADER ER BERØRT I DAG: sveipene er ikke aktive på staging, og
-- ingen tenant har lukket et funn. Rettelsen står her fordi en
-- migrasjon som bare virker mot en tom base ikke er en migrasjon.
--
-- ---------------------------------------------------------------------
-- DET ANDRE FUNNET FRA SAMME RUNDE ER IKKE RETTET, OG DET SKAL STÅ:
-- CodeRabbit meldte at `l` som løkkevariabel feller «the lint gate»
-- med ruff E741. Det finnes ingen ruff-konfigurasjon i repoet og ingen
-- lint-jobb i `ci.yml`, og `for l in` står i et tjuetalls testfiler
-- fra før. Funnet er en bagatell mot en port som ikke finnes.
-- =====================================================================

CREATE OR REPLACE FUNCTION sveipefunn_lukkevern()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE
    v_sveip TEXT := TG_ARGV[0];
    v_ny JSONB;
BEGIN
    -- LUKKING UTEN NAVN ER SVEIPENS (125, uendret).
    IF OLD.apen AND NOT NEW.apen AND NEW.lukket_av IS NULL THEN
        v_ny := to_jsonb(NEW) || jsonb_build_object('lukket_av',
                                                    v_sveip);
        NEW := jsonb_populate_record(NEW, v_ny);
        RETURN NEW;
    END IF;

    IF NOT OLD.apen AND NEW.apen THEN
        -- ENDRINGEN I 126 STÅR I DENNE ENE BETINGELSEN.
        --
        -- `ukjent_for_125` er 125s etterfylling av rader som ble
        -- lukket FØR `lukket_av` fantes. Vi vet ikke om det var
        -- sveipen eller et menneske, og en rad vi ikke vet noe om
        -- skal slippes inn igjen: et funn som gjenåpnes for ivrig er
        -- en irritasjon, et funn som aldri kommer tilbake er
        -- stillhet.
        IF OLD.lukket_av IS DISTINCT FROM v_sveip
           AND OLD.lukket_av IS DISTINCT FROM 'ukjent_for_125' THEN
            -- ET MENNESKES LUKKING STÅR.
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
            -- SVEIPENS EGEN LUKKING — eller en vi ikke vet opphavet
            -- til. Begge gjenåpnes, og sporet ryddes.
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
    'TG_ARGV[0] er sveipens navn for tabellen. `ukjent_for_125` '
    '(125s etterfylling) regnes som gjenåpnbar: en rad vi ikke vet '
    'opphavet til skal slippes inn igjen, ikke stenges ute for '
    'alltid. Se 125, 126 og docs/FUNN-SVEIPEN-GJENAAPNER.md.';
