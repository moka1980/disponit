-- ============================================================
-- 030 — Gjerdet må settes av EIEREN, også i en gjenskaping (Codex P1 på #68)
--
-- 🔴 FUNNET: 028 gjenskaper `varsel_klaim_epost` og legger
-- `REVOKE ALL … FROM PUBLIC` ETTER `ALTER FUNCTION … OWNER TO
-- disponit_domene_eier`, men FØR `SET LOCAL ROLE`. Migrator eier da ikke
-- lenger funksjonen, og er medlem av eierrollen `WITH INHERIT FALSE` — så
-- REVOKE-en gir bare en WARNING og går videre. Men den materialiserer
-- samtidig standard-ACL-en, som for en funksjon er EXECUTE for PUBLIC.
-- Default-deny-en var altså ikke bare uinnført, den var snudd.
--
-- Det er nøyaktig fellen 027 skrev ned og 019 lærte på den harde måten. 028
-- gikk i den likevel, fordi den GJENSKAPTE en funksjon som alt var herdet:
-- en DROP tar ACL-en med seg, og gjenskapingen arver ingenting av seg selv.
-- Samme lærdom som kroppen i 028 alt bærer for granter — den gjaldt
-- REVOKE-en også.
--
-- HVA SOM STÅR PÅ SPILL: `varsel_klaim_epost` er SECURITY DEFINER og
-- kryss-tenant med vilje. Den returnerer tenant, VERIFISERT e-postadresse,
-- tekstnøkkel og parametre for alle kunders køede varsler, og RLS verner
-- ikke mot den — omgåelsen er funksjonens formål. EXECUTE for PUBLIC betyr
-- derfor at hver eneste rolle i klyngen kan lese mottakerlisten for hele
-- installasjonen og tømme køen.
--
-- HVORFOR TESTENE IKKE SÅ DET: både CI og staging migrerer med
-- `deploy/staging/migrer.py`, som ETTER migrasjonene kjører sin egen REVOKE
-- som eier (`VARSLER_RETTIGHETER`). ACL-porten i `test_varselsender` måler
-- tilstanden etter den oppryddingen, og der er hullet lukket. Det står åpent
-- i vinduet mellom de to stegene — og PERMANENT for den som kjører
-- `db.kjorer.migrer` direkte, eller for et deploy som stanser der imellom.
-- Derfor er porten mot gjentakelse i denne runden en KILDETEST over alle
-- migrasjonsfiler, ikke en ny måling på den ferdig ryddede basen: det er
-- filen som er feil, og den er feil også der oppryddingen skjuler den.
--
-- Ingen gjenskaping her. Kroppen fra 028 er riktig; det er bare ACL-en som
-- skal repareres, og en unødvendig DROP/CREATE ville åpnet den på nytt.
-- ============================================================

-- Eieren selv, som i 027. Migrator ville fått samme stille WARNING som
-- funnet handler om — og denne gangen uten at noen la merke til det.
SET LOCAL ROLE disponit_domene_eier;

REVOKE ALL ON FUNCTION varsel_klaim_epost(int, int) FROM PUBLIC;

-- Søsknene er tatt med selv om 028 ikke rørte dem. De tre deler eier,
-- gjerde og risiko, og en migrasjon som bare reparerer den ene lar neste
-- leser tro at de to andre er en annen sak. En REVOKE av et privilegium som
-- alt er borte er en no-op.
REVOKE ALL ON FUNCTION varsel_sett_epoststatus(bigint, uuid, text, text)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION varsel_rekoe(interval, int, interval) FROM PUBLIC;

RESET ROLE;

-- KONTROLL, FAIL-HARD. REVOKE-en over kan ikke feile stille når den kjøres
-- som eier — men det var nettopp «kan ikke feile stille» som var feil
-- antakelse i 028. Porten koster ingenting og gjør at en base som mot
-- formodning står med åpen ACL stanser deployet i stedet for å bli oppdaget
-- av noen andre. `acldefault` gjør en NULL-ACL (altså den underforståtte
-- standarden) synlig, slik ACL-testen alt gjør det.
DO $$
DECLARE sig text;
BEGIN
    FOREACH sig IN ARRAY ARRAY[
        'varsel_klaim_epost(int,int)',
        'varsel_sett_epoststatus(bigint,uuid,text,text)',
        'varsel_rekoe(interval,int,interval)']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_proc p,
                        aclexplode(coalesce(p.proacl,
                                   acldefault('f', p.proowner))) a
                    WHERE p.oid = sig::regprocedure
                      AND a.privilege_type = 'EXECUTE'
                      AND a.grantee = 0)      -- 0 = PUBLIC
        THEN
            RAISE EXCEPTION
                'PUBLIC har fortsatt EXECUTE på % — gjerdet står ikke', sig;
        END IF;
    END LOOP;
END $$;
