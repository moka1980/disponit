-- 192: ET FIRMA KAN REGISTRERE SEG SELV.
--
-- HAKEN INGEN HADDE SETT: en helt ny bruker har ingen medlemskap, og
-- `_opprett_sesjon` avviser derfor med `ingen_tilgang` (v3 §2, «ingen JIT»)
-- FØR hun rekker å registrere noe. Hun kan ikke bli kunde fordi hun ikke
-- er kunde.
--
-- Løsningen er ikke å myke opp medlemskapskravet — det er riktig som det
-- er. Den er å gi den identiteten ETT sted å stå mens hun registrerer:
-- den reserverte tenanten `_registrering`, med nøyaktig ett scope. Google
-- har alt bevist hvem hun er; hun mangler bare et sted å høre til.
--
-- TO LÅSER, IKKE ÉN. Scopet `firma:opprett` er HTTP-siden. Døra under
-- krever i tillegg at kallerens kontekst ER `_registrering`. En kundesesjon
-- står aldri i den konteksten — `firma_tenant_form` (190) forbyr understrek
-- i et firmanavn, så ingen kunde kan hete det — og et scope alene er derfor
-- ikke nok til å opprette firmaer.
--
-- OG EN TREDJE, I DATA. `plattformeier`-rollen kommer i en senere PR, men
-- vernet den trenger hører hjemme her, sammen med de andre reserverte
-- tenantene: en rolle som gjelder HELE plattformen skal ikke kunne stå på
-- en kundes medlemskapsrad. Uten det ville en feilplassert rad gitt
-- plattformfullmakt inne i en kundekontekst.
-- ============================================================

-- ------------------------------------------------------------------
-- Reserverte tenanter, samlet ett sted for første gang.
--
-- `<ukjent>` (pg.py), `_oidc` (sesjon.py) og `_plattform` (189) har hver
-- for seg vært hardkodet i Python. `_registrering` blir den fjerde, og da
-- er det på tide at basen kjenner mengden — ellers er «reservert» bare en
-- konvensjon, og en konvensjon kan ingen CHECK bygge på.
-- ------------------------------------------------------------------
CREATE OR REPLACE FUNCTION er_reservert_tenant(p_tenant TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT p_tenant IN ('<ukjent>', '_oidc', '_plattform', '_registrering')
$$;

COMMENT ON FUNCTION er_reservert_tenant(TEXT) IS
    'Plattformens egne kontekster. Ingen kunde kan hete dette: '
    'firma_tenant_form (190) forbyr understrek og vinkelparentes, og '
    '_tenant_fra_host avviser understrek-prefiks.';

-- ------------------------------------------------------------------
-- Plattformroller kan ikke stå på en kunderad.
--
-- `brukermedlemskap.roller` er TEXT[] uten noen begrensning på innhold, og
-- INGEN dør skriver tabellen i dag — den fylles av en operatør med direkte
-- basetilgang. Det er trygt så lenge det varer, men rolletildeling får en
-- flate i en senere PR, og da er dette forskjellen mellom «en feil noen
-- gjør» og «en feil som ikke kan gjøres».
-- ------------------------------------------------------------------
ALTER TABLE brukermedlemskap
    DROP CONSTRAINT IF EXISTS brukermedlemskap_plattformrolle_bare_paa_plattform;
ALTER TABLE brukermedlemskap
    ADD CONSTRAINT brukermedlemskap_plattformrolle_bare_paa_plattform
    CHECK (NOT ('plattformeier' = ANY(roller)) OR tenant = '_plattform');

-- ============================================================
-- DØRA: firma + første medlemskap, i ÉN transaksjon.
--
-- Den setter tenantkonteksten SELV, som reaperne gjør (038/057/184), fordi
-- den skriver rader for en tenant som ikke fantes da kallet startet. Og den
-- legger konteksten tilbake etterpå, så kalleren står der hun sto.
--
-- HVORFOR MEDLEMSKAPET HØRER MED I SAMME DØR: et firma uten et eneste
-- medlem er et firma ingen kan komme inn i, og det ville stått igjen som
-- en foreldreløs rad hvis de to stegene var adskilt og det andre feilet.
-- Den som registrerer BLIR admin — det er den eneste rollen som gir mening
-- for den første, og hun kan invitere resten når invitasjonsflaten finnes.
-- ============================================================

-- Taket er et STARTPUNKT, ikke en lov. En regnskapsfører kan ha flere
-- klienter, så et tak på 1 ville vært feil; et tak på ingenting ville gjort
-- åpen registrering til en gratis tenantfabrikk. Tallet står her, alene, så
-- det er ett sted å endre når vi vet hvordan misbruket faktisk ser ut.
CREATE OR REPLACE FUNCTION firma_registreringstak()
RETURNS INT LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$ SELECT 3 $$;
-- Tallet er ingen hemmelighet, men porten `test_ingen_firmadoer_star_aapen
-- _for_public` måler ACL-en på ALLE `firma%`-dører, og et unntak «fordi
-- denne er harmløs» er nettopp formen en ekte åpning ville smuglet seg inn
-- i. Billigere å lukke enn å forklare.
REVOKE ALL ON FUNCTION firma_registreringstak() FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        GRANT EXECUTE ON FUNCTION firma_registreringstak() TO disponit;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION firma_selvregistrer(p_tenant TEXT, p_navn TEXT,
                                               p_orgnummer TEXT,
                                               p_bruker_id TEXT,
                                               p_prove_dogn INT DEFAULT 30)
RETURNS DATE LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kontekst TEXT; v_frist DATE; v_antall INT; v_annen TEXT;
BEGIN
    -- LÅS 1: kalleren må stå i registreringskonteksten. Scopet alene holder
    -- ikke — en kundesesjon med et feilaktig scope skal fortsatt ikke kunne
    -- opprette firmaer, og ingen kunde kan stå i denne konteksten.
    v_kontekst := coalesce(current_setting('disponit.tenant', true), '');
    IF v_kontekst <> '_registrering' THEN
        RAISE EXCEPTION 'firma_selvregistrer: kalles kun fra '
                        'registreringskonteksten (sto i %)',
                        coalesce(nullif(v_kontekst, ''), '(ingen)')
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF p_bruker_id IS NULL OR btrim(p_bruker_id) = '' THEN
        RAISE EXCEPTION 'firma_selvregistrer: registranten mangler identitet'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Identiteten må finnes. Uten dette kunne en tastefeil i bruker_id gitt
    -- et firma ingen eier, og FK-en under ville først slått til på
    -- medlemskapet — etter at firmaraden var skrevet.
    IF NOT EXISTS (SELECT 1 FROM public.brukeridentitet b
                    WHERE b.bruker_id = p_bruker_id) THEN
        RAISE EXCEPTION 'firma_selvregistrer: ukjent registrant'
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    -- Taket telles på LEVENDE firmaer. Den som registrerer tre og stenger
    -- to, skal kunne registrere igjen — taket verner mot en fabrikk, ikke
    -- mot en som ombestemmer seg.
    --
    -- TELLINGEN MÅ GÅ TENANT FOR TENANT, og det er ikke omstendelighet.
    -- `bruker_tenant` er RLS-fri (189) og svarer uten kontekst, men `firma`
    -- har FORCE RLS — og her står konteksten på `_registrering`. En
    -- `JOIN public.firma` ville truffet NULL RADER uansett hvor mange
    -- firmaer hun hadde, og taket ville aldri slått inn. (Første utgave
    -- gjorde nettopp det; porten `test_taket_teller_levende_firmaer` sa
    -- «DID NOT RAISE».)
    -- SERIALISER TELLINGEN MOT SEG SELV (CodeRabbit). Uten låsen leser to
    -- samtidige registreringer for SAMME bruker begge «to av tre», og begge
    -- skriver — taket blir fire. Samme form som sesjonsgrensen i
    -- `_opprett_sesjon`: advisory lock krever ingen tabellrettighet, og den
    -- er transaksjonsbundet, så den slipper når kallet er ferdig uansett
    -- utfall.
    PERFORM pg_advisory_xact_lock(
        hashtextextended('firma_selvregistrer:' || p_bruker_id, 0));
    v_antall := 0;
    FOR v_annen IN
        SELECT bt.tenant FROM public.bruker_tenant bt
         WHERE bt.bruker_id = p_bruker_id AND bt.tenant <> '_registrering'
    LOOP
        PERFORM set_config('disponit.tenant', v_annen, true);
        IF EXISTS (SELECT 1 FROM public.firma f
                    WHERE f.tenant = v_annen AND f.status <> 'stengt') THEN
            v_antall := v_antall + 1;
        END IF;
    END LOOP;
    PERFORM set_config('disponit.tenant', v_kontekst, true);
    IF v_antall >= public.firma_registreringstak() THEN
        RAISE EXCEPTION 'firma_selvregistrer: taket på % firmaer er nådd',
                        public.firma_registreringstak()
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    -- Konteksten flyttes til det NYE firmaet for selve skrivingen, og
    -- legges tilbake før vi returnerer.
    PERFORM set_config('disponit.tenant', p_tenant, true);
    v_frist := public.firma_registrer(p_tenant, p_navn, p_orgnummer,
                                      p_prove_dogn, 'bruker:' || p_bruker_id);
    -- Den som registrerer blir admin. Første medlem må kunne invitere de
    -- neste, og et firma uten et eneste medlem er et firma ingen kommer inn i.
    INSERT INTO public.brukermedlemskap (tenant, bruker_id, roller, aktiv)
         VALUES (p_tenant, p_bruker_id, ARRAY['admin'], true);

    -- REGISTRANTRADEN MÅ DØ HER, og det er ikke opprydding — det er en
    -- forutsetning for at hun kommer inn igjen.
    --
    -- Registranten har et ekte medlemskap på `_registrering` (det er det som
    -- gir henne scopet, uten særtilfeller i sesjonsveien). Blir den stående,
    -- har hun TO medlemskap ved neste innlogging, og `_firma_for_bruker`
    -- svarer `firma_ikke_valgt` — hun ville blitt låst ute av firmaet hun
    -- nettopp opprettet, av en rad som hadde gjort jobben sin.
    PERFORM set_config('disponit.tenant', '_registrering', true);
    DELETE FROM public.brukermedlemskap
     WHERE tenant = '_registrering' AND bruker_id = p_bruker_id;

    PERFORM set_config('disponit.tenant', v_kontekst, true);
    RETURN v_frist;
END $$;
REVOKE ALL ON FUNCTION firma_selvregistrer(TEXT, TEXT, TEXT, TEXT, INT)
    FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        GRANT EXECUTE ON FUNCTION
            firma_selvregistrer(TEXT, TEXT, TEXT, TEXT, INT) TO disponit;
    END IF;
END $$;

-- ============================================================
-- ROLLEN I BASEN. 043 §6b speiler `ROLLE_TIL_SCOPES` EKSAKT, og port 26 i
-- test_gate14b måler de to mot hverandre. Et scope lagt til i app-laget uten
-- migrasjon er et sprik basen først ser den dagen et lovlig nei avvises.
--
-- `registrant` ER IKKE EN KUNDEROLLE, og står derfor bevisst UTENFOR
-- `KUNDEROLLER` i plattformdata.js. Porten der går over guiden og sjekker at
-- hver oppføring finnes i `ROLLE_TIL_SCOPES` — ikke motsatt — så en rolle
-- kunden ikke skal kunne tildele, hører ikke hjemme i guiden. En kunde som
-- kunne gitt noen `registrant`, ville gitt bort retten til å opprette
-- firmaer på plattformen.
-- ============================================================
INSERT INTO rolle_scope (rolle, scope) VALUES
    ('registrant', 'firma:opprett')
ON CONFLICT DO NOTHING;

-- ============================================================
-- REGISTRANTRADEN. Innloggingen oppretter den når en identitet ikke hører
-- til noe firma — det er det som gir henne scopet, uten særtilfeller i
-- sesjonsveien.
--
-- Runtime har bare SELECT på `brukermedlemskap` (den fylles ellers av en
-- operatør med basetilgang), så dette må gå gjennom en dør. Døra håndhever
-- sin egen forutsetning i stedet for å stole på at kalleren sjekket den:
-- en registrantrad skal ALDRI oppstå for noen som allerede hører til et
-- firma, for da ville hun fått to medlemskap og `firma_ikke_valgt` ved
-- neste innlogging.
-- ============================================================
CREATE OR REPLACE FUNCTION registrant_medlemskap(p_bruker_id TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kontekst TEXT; v_andre INT;
BEGIN
    v_kontekst := coalesce(current_setting('disponit.tenant', true), '');
    IF p_bruker_id IS NULL OR btrim(p_bruker_id) = '' THEN
        RAISE EXCEPTION 'registrant_medlemskap: mangler identitet'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.brukeridentitet b
                    WHERE b.bruker_id = p_bruker_id) THEN
        RAISE EXCEPTION 'registrant_medlemskap: ukjent identitet'
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    -- Speilet er RLS-fritt (189) og svarer uten kontekst — nettopp derfor
    -- kan denne sjekken gjøres her, før noen tenant er kjent.
    SELECT count(*) INTO v_andre FROM public.bruker_tenant bt
     WHERE bt.bruker_id = p_bruker_id AND bt.tenant <> '_registrering';
    IF v_andre > 0 THEN
        RETURN false;      -- hører alt til et firma; ingen registrantrad
    END IF;

    PERFORM set_config('disponit.tenant', '_registrering', true);
    INSERT INTO public.brukermedlemskap (tenant, bruker_id, roller, aktiv)
         VALUES ('_registrering', p_bruker_id, ARRAY['registrant'], true)
    ON CONFLICT (tenant, bruker_id) DO NOTHING;
    PERFORM set_config('disponit.tenant', v_kontekst, true);
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION registrant_medlemskap(TEXT) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        GRANT EXECUTE ON FUNCTION registrant_medlemskap(TEXT) TO disponit;
    END IF;
END $$;

-- ============================================================
-- BRANSJEMALEN AKTIVERES FOR ET HELT NYTT FIRMA.
--
-- `policyregister.registrer` er oppsettsveien, og den kjører på
-- migratorforbindelsen: runtime har KUN `SELECT ON policyer`, og linja i
-- `migrer.py` sier hvorfor — «policyer endres av en egen vei». Å gi runtime
-- INSERT der ville latt en feil i en rute aktivere policy uten attestasjon,
-- altså svekket fire-øyne (V6). Det gjør vi ikke.
--
-- Men et HELT NYTT firma har ingen lineage å omgå: ingen versjoner, ingen
-- aktiveringshendelser, ingenting en bootstrap kunne gå forbi. Hele den
-- delikate logikken i `registrer` — advisory-låsene, prøven mot
-- `policyaktivering`, ankerradens FOR UPDATE — finnes for tenanter som
-- ALLEREDE har en historikk.
--
-- Denne døra håndhever derfor sin egen forutsetning i stedet for å gjenskape
-- vernene: den nekter hvis tenanten har så mye som ÉN policyrad. Da kan den
-- per konstruksjon aldri røre en serie som er inne i lineagen, og
-- `registrer` forblir eneste vei for alt annet.
--
-- Validering av innholdet skjer i Python før kallet (`valider_ny_policy`),
-- som før. Døra tar imot noe som allerede er godkjent, og skriver det.
-- ============================================================
CREATE OR REPLACE FUNCTION firma_bootstrap_policy(p_tenant TEXT,
                                                  p_policy_id TEXT,
                                                  p_versjon TEXT,
                                                  p_hash TEXT,
                                                  p_status TEXT,
                                                  p_innhold JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'firma_bootstrap_policy');
    -- FORUTSETNINGEN, IKKE ET VERN SOM ER GJENSKAPT: har tenanten én eneste
    -- policyrad, finnes det en historikk, og da er `registrer` riktig vei.
    IF EXISTS (SELECT 1 FROM public.policyer p WHERE p.tenant = p_tenant) THEN
        RAISE EXCEPTION 'firma_bootstrap_policy: % har allerede policy — '
                        'bruk den styrte veien', p_tenant
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    -- `aktiv = true` SELV NÅR STATUS ER 'utkast', og det er husets form,
    -- ikke en forglemmelse (CodeRabbit spurte). Bransjemalene i `policies/`
    -- bærer alle `status: utkast`, og `init-tenant.sh` aktiverer dem slik
    -- gjennom `policyregister.registrer(..., aktiver=True)`. Malen ER
    -- startpunktet; den løftes til `produksjon` gjennom den styrte veien
    -- med attestasjon når firmaet har tatt stilling til den. Å nekte
    -- aktivering her ville gitt et nyregistrert firma en agent uten
    -- fullmakter, og et avvik fra oppsettsveien som ingenting målte.
    INSERT INTO public.policyer (tenant, policy_id, versjon, innholds_hash,
                                 status, innhold, aktiv, aktiveringskilde,
                                 bootstrap_aktivert_ts)
         VALUES (p_tenant, p_policy_id, p_versjon, p_hash, p_status,
                 p_innhold, true, 'bootstrap', now());
    INSERT INTO public.policy_hode (tenant, policy_id, aktiv_versjon)
         VALUES (p_tenant, p_policy_id, p_versjon);
END $$;
REVOKE ALL ON FUNCTION
    firma_bootstrap_policy(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        GRANT EXECUTE ON FUNCTION
            firma_bootstrap_policy(TEXT, TEXT, TEXT, TEXT, TEXT, JSONB)
            TO disponit;
    END IF;
END $$;
