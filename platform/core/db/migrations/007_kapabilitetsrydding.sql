-- ============================================================
-- Disponit migrasjon 007 — skjemaeieren får rydde kapabilitetstabellene.
--
-- HVORFOR EN EGEN MIGRASJON OG IKKE EN ENDRING I 005:
-- 005 er kjørt og registrert med checksum på staging. Kjøreren avviser
-- enhver endring av en historisk fil, og jeg BEVISTE det før jeg skrev
-- denne: en endret 005 gir
--   «migrasjon 005 er endret etter kjøring (checksum-avvik)»
-- og hele deployet stopper. Historikken er immutable — rettelser kommer
-- alltid som neste versjon.
--
-- Jeg hadde først lagt GRANT-ene i 005 og flagget det i PR-beskrivelsen
-- som «ditt kall». Det var feil form: det er ikke en preferanse, det er
-- en hard feil, og et flagg lukker ikke et hull.
--
-- HVA DEN GJØR: gir skjemaeieren SELECT+DELETE på de to
-- kapabilitetstabellene. Det svekker ingenting — kapabilitetsmodellen
-- beskytter mot RUNTIME, som verken har tabelltilgang eller kan SET ROLE
-- hit, og migrator eier skjemaet og kunne uansett droppe tabellene.
--
-- HVORFOR DET TRENGS: testsuiten kunne ikke rydde radene. De hopet seg opp
-- på tvers av kjøringer mens `frigi_hengende_kapabiliteter()` er GLOBAL, og
-- suiten feilet på ULIKT sted mellom kjøringer. En suite som ikke er
-- hermetisk, måler tilfeldigheter i stedet for kode.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen. Kjøres av MIGRATOR.
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_m37_claimer') THEN
        RAISE EXCEPTION
            'rollen disponit_m37_claimer mangler — kjør deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

-- Bare EIEREN kan gi bort rettigheter, og eieren er NOLOGIN-rollen.
-- Migrator er medlem (kreves for OWNER TO i 005), men `WITH INHERIT FALSE`
-- gjør at den ikke arver noe — den kan bare SET ROLE. Den muligheten
-- brukes her, eksplisitt og avgrenset til to GRANT-er.
--
-- SELECT trengs i tillegg til DELETE: en `DELETE ... WHERE tenant=…` må
-- lese kolonnen den filtrerer på.
SET LOCAL ROLE disponit_m37_claimer;
GRANT SELECT, DELETE ON public.arbeidskapabiliteter   TO SESSION_USER;
GRANT SELECT, DELETE ON public.kvitteringskapabiliteter TO SESSION_USER;
RESET ROLE;
