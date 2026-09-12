-- 193: PRØVEUTLØPET MÅ KUNNE KJØRES — også der ryddekontoen ikke finnes.
--
-- 190 ga `firma_sveip_proveutlop` til `disponit_domener` OG fjernet bevisst
-- 184s reservevei, som gir sveipen til runtime når den rollen mangler.
-- Begrunnelsen sto navngitt: blast-radiusen er ulik, siden denne setter
-- HVERT firma i basen ut av prøveperioden.
--
-- KONSEKVENSEN VAR VERRE ENN PROBLEMET. `disponit_domener` finnes i drift,
-- men ikke i CI og ikke lokalt — og `_reaperkobling` (038-formen) faller da
-- tilbake på runtime. Sveipen var altså ikke kallbar i noe miljø der den
-- kunne MÅLES, og porten som skal bevise at driftsveien kaller den, kunne
-- ikke kjøre. En sveip ingen test kan nå, er nøyaktig slik «definert,
-- testet, GRANTet — og aldri kalt» (057s Codex P1) oppstår.
--
-- Reserveveien er derfor tilbake, i 184s ordrette form. Den gjelder KUN der
-- ryddekontoen mangler: i drift eier `disponit_domener` jobben, og runtime
-- er eksplisitt nektet — akkurat som for `reap_partkontakt`, som blanker
-- kontaktdata på tvers av tenanter under nøyaktig samme betingelse.
--
-- Avveiningen jeg gjorde i 190 var ikke gal, den var bare feil prioritert:
-- et vern som gjør regelen utestbar, verner ingenting i praksis.
--
-- OG GRANTET GJØRES SOM EIEREN. Funksjonen eies av `disponit_m37_claimer`;
-- migrator kan ikke gi bort en rettighet den ikke har. Det er nøyaktig
-- rekkefølgefeilen 190 selv måtte rette — der sto `REVOKE` etter
-- `RESET ROLE` og gjorde ingenting, med en WARNING i stedet for en feil.
-- Her feiler den høylytt i stedet, som den skal.
-- ============================================================
SET LOCAL ROLE disponit_m37_claimer;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_domener') THEN
        GRANT EXECUTE ON FUNCTION firma_sveip_proveutlop(INT)
            TO disponit_domener;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
            REVOKE EXECUTE ON FUNCTION firma_sveip_proveutlop(INT)
                FROM disponit;
        END IF;
    ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        GRANT EXECUTE ON FUNCTION firma_sveip_proveutlop(INT) TO disponit;
    END IF;
END $$;

RESET ROLE;
