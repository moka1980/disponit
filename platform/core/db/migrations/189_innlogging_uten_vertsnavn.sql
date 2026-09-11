-- 189: INNLOGGING UTEN VERTSNAVN — firmaet kommer fra brukeren.
--
-- I dag utledes tenant av det FØRSTE LEDDET i den kanoniske hosten
-- (`api/sesjon.py::_tenant_fra_host`). Kommentaren der har sagt siden v1 at
-- dette er midlertidig: «I v1 er workspace == host; en ekte slug→tenant-
-- mapping er data.» Konsekvensen er målt i prod: seks firmaer har egen
-- krypteringsnøkkel, fire har medlemmer, og ETT kan logge inn — fordi bare
-- ett har en rad i `tenant_oidc_provider`. Hvert nytt firma krever DNS,
-- sertifikat og en nginx-blokk, alle tre som root på verten.
--
-- Denne migrasjonen gir innloggingen det ene oppslaget den mangler:
-- bruker_id → tenant, FØR noen tenantkontekst er satt.
--
-- HVORFOR EN EGEN TABELL OG IKKE EN DØR I `brukermedlemskap`:
-- `brukermedlemskap` har FORCE ROW LEVEL SECURITY med policyen
-- `tenant_isolasjon` (tenant = current_setting('disponit.tenant')). FORCE
-- gjelder OGSÅ tabelleieren, så en SECURITY DEFINER-dør eid av migrator ser
-- null rader uten kontekst — og konteksten er nettopp det vi ikke har ennå.
-- Det etterlater to veier:
--   (a) en ny rolle med egen policy (mønsteret i 184), som krever at
--       `oppsett-postgresql.sh` kjøres som root FØR deployen — nøyaktig den
--       friksjonen denne arcen finnes for å fjerne; eller
--   (b) en oppslagstabell UTEN RLS, som er formen innloggingsveien allerede
--       har: `brukeridentitet`, `brukersesjon` og `oidc_logintransaksjon`
--       står alle tre uten RLS, fordi de leses før tenant er kjent.
-- (b) er valgt. Den importerer ikke et privilegert mønster til et sted som
-- ikke trenger det, og den legger ingen nye stående rettigheter igjen.
--
-- HVA TABELLEN INNEHOLDER: medlemskapsFAKTUMET, ikke medlemskapet. Ingen
-- roller, ingen authz_version, ingen profil — kun (bruker_id, tenant) for
-- AKTIVE medlemskap. Rollene hentes fortsatt fra `brukermedlemskap` under
-- riktig kontekst i `_opprett_sesjon`, som før. Den som får lese denne
-- tabellen lærer altså hvilke firmaer en bruker_id hører til, og ingenting
-- om hva brukeren kan gjøre der.
--
-- TRIGGEREN ER ENESTE SKRIVER. Ingen kode skriver `bruker_tenant` direkte;
-- den følger `brukermedlemskap` automatisk. Driver de fra hverandre, er det
-- en feil i triggeren, ikke i et kallsted som glemte å oppdatere begge.
-- ============================================================

CREATE TABLE IF NOT EXISTS bruker_tenant (
    bruker_id TEXT NOT NULL REFERENCES brukeridentitet (bruker_id)
                   ON DELETE CASCADE,
    tenant    TEXT NOT NULL,
    PRIMARY KEY (bruker_id, tenant)
);

COMMENT ON TABLE bruker_tenant IS
    'Speil av AKTIVE brukermedlemskap, uten RLS, for oppslaget bruker_id → '
    'tenant i innloggingen (før tenantkontekst finnes). Vedlikeholdes KUN '
    'av triggeren bruker_tenant_speil på brukermedlemskap.';

-- ------------------------------------------------------------
-- Triggeren: ett speil per sikkerhetsrelevant endring.
--
-- `aktiv` er det eneste feltet som avgjør om raden skal finnes. Roller og
-- authz_version rører ikke speilet — de leses fra kilden ved innlogging.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION bruker_tenant_speil()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM public.bruker_tenant
         WHERE bruker_id = OLD.bruker_id AND tenant = OLD.tenant;
        RETURN OLD;
    END IF;
    IF NEW.aktiv THEN
        INSERT INTO public.bruker_tenant (bruker_id, tenant)
             VALUES (NEW.bruker_id, NEW.tenant)
        ON CONFLICT (bruker_id, tenant) DO NOTHING;
    ELSE
        DELETE FROM public.bruker_tenant
         WHERE bruker_id = NEW.bruker_id AND tenant = NEW.tenant;
    END IF;
    -- Et UPDATE som flytter raden til en annen tenant eller bruker ville
    -- etterlatt det gamle speilet. `brukermedlemskap` har PK (tenant,
    -- bruker_id) og ingen kode oppdaterer nøkkelen, men speilet skal ikke
    -- HVILE på det: rydd den gamle raden eksplisitt.
    IF TG_OP = 'UPDATE'
       AND (OLD.tenant, OLD.bruker_id) IS DISTINCT FROM
           (NEW.tenant, NEW.bruker_id) THEN
        DELETE FROM public.bruker_tenant
         WHERE bruker_id = OLD.bruker_id AND tenant = OLD.tenant;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS bruker_tenant_speil ON brukermedlemskap;
CREATE TRIGGER bruker_tenant_speil
    AFTER INSERT OR UPDATE OR DELETE ON brukermedlemskap
    FOR EACH ROW EXECUTE FUNCTION bruker_tenant_speil();

-- ============================================================
-- BACKFILL — og hvorfor RLS-vinduet, ikke en rolle.
--
-- Migrator har ingen tenantkontekst, og `brukermedlemskap` har FORCE RLS.
-- Uten vinduet ville SELECT-en truffet null rader, INSERT-en satt inn
-- ingenting, og migrasjonen sett ut som en suksess. Det er FIX-008-klassen
-- 029 navngir ordrett — og den jeg selv skrev inn i 187 og måtte rette i
-- 188. Backfillen er dessuten KRYSS-TENANT av natur: det finnes ingen
-- ett-tenant-kontekst som er riktig å sette.
--
-- FORCE slås av for EIEREN i vinduet og på igjen etter KONTROLLSTEGET, alt i
-- samme transaksjon — feiler noe underveis, rulles også RLS-endringen
-- tilbake. `ALTER TABLE` holder ACCESS EXCLUSIVE, så ingen annen sesjon kan
-- lese i vinduet, og vanlige roller er urørt hele veien (NO FORCE unntar kun
-- tabelleieren). En engangsjobb skal ikke etterlate seg stående rettigheter.
-- ============================================================
ALTER TABLE brukermedlemskap NO FORCE ROW LEVEL SECURITY;

INSERT INTO bruker_tenant (bruker_id, tenant)
SELECT m.bruker_id, m.tenant FROM brukermedlemskap m WHERE m.aktiv
ON CONFLICT (bruker_id, tenant) DO NOTHING;

-- KONTROLLSTEGET: speilet skal være NØYAKTIG de aktive medlemskapene.
-- En backfill som ikke fant noe ser ut som en som ikke hadde noe å finne,
-- så forskjellen måles i begge retninger og migrasjonen stopper på avvik.
DO $$
DECLARE v_kilde INT; v_speil INT; v_manglende INT; v_overskytende INT;
BEGIN
    SELECT count(*) INTO v_kilde FROM brukermedlemskap WHERE aktiv;
    SELECT count(*) INTO v_speil FROM bruker_tenant;
    SELECT count(*) INTO v_manglende FROM (
        SELECT bruker_id, tenant FROM brukermedlemskap WHERE aktiv
        EXCEPT SELECT bruker_id, tenant FROM bruker_tenant) s;
    SELECT count(*) INTO v_overskytende FROM (
        SELECT bruker_id, tenant FROM bruker_tenant
        EXCEPT SELECT bruker_id, tenant FROM brukermedlemskap WHERE aktiv) s;
    IF v_manglende <> 0 OR v_overskytende <> 0 THEN
        RAISE EXCEPTION 'bruker_tenant-speilet stemmer ikke: % aktive '
                        'medlemskap, % speilrader, % manglende, % overskytende',
                        v_kilde, v_speil, v_manglende, v_overskytende;
    END IF;
    RAISE NOTICE 'bruker_tenant: % aktive medlemskap speilet', v_speil;
END $$;

-- RLS-vinduet lukkes.
ALTER TABLE brukermedlemskap FORCE ROW LEVEL SECURITY;

-- ------------------------------------------------------------
-- Rettigheter: runtime LESER speilet og skriver det aldri. Triggeren er
-- SECURITY DEFINER (eid av migrator), så den skriver uavhengig av hvem som
-- rørte `brukermedlemskap`.
-- ------------------------------------------------------------
REVOKE ALL ON bruker_tenant FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        GRANT SELECT ON bruker_tenant TO disponit;
    END IF;
END $$;
