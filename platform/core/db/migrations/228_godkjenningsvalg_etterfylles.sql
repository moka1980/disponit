-- 228 — ETTERFYLLINGEN 227 IKKE GJORDE.
--
-- 227 skulle merke hvert EKSISTERENDE firma som «har valgt» (to
-- godkjennere), slik at regelen bare kan svekkes av to personer. Den
-- gjorde det med en vanlig UPDATE som migrator — og `firma` har FORCE ROW
-- LEVEL SECURITY, så migratoren så NULL rader. Målt i drift 21.11.2026
-- rett etter utrullingen: begge firmaene sto med `godkjenning_valgt_ts`
-- tom, og døra ville latt én administrator alene gjøre «første valg» og
-- slå av kravet.
--
-- Testbasen hadde ingen firmaer da 227 kjørte, så ingen test så det: en
-- etterfylling målt mot en tom tabell er ikke målt.
--
-- Claimeren er den ene rollen med kryss-tenant-policy på `firma` (190,
-- prøveutløpssveipen). Etterfyllingen kjøres som den.
SET LOCAL ROLE disponit_m37_claimer;
UPDATE public.firma
   SET godkjenning_valgt_ts = opprettet,
       godkjenning_valgt_av = 'migrasjon:228'
 WHERE godkjenning_valgt_ts IS NULL;
RESET ROLE;
