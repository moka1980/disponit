-- =====================================================================
-- 152 — M-23: UTFALLET «KREVER ET MENNESKE» I PURRINGSBESTILLINGEN
-- =====================================================================
--
-- ARC B, PR 8 (eiervedtak 9/9, valg 2): inkassovarsel sendes aldri av
-- agenten, inkasso aldri av systemet. Utløseren bestiller derfor ikke
-- disse trinnene — den bokfører at et menneske må ta dem, så kandidaten
-- ikke plukkes på nytt hver runde og flaten kan si det. Ingen
-- beslutning brennes, ingen sak fødes: det er ikke et brudd, det er
-- planens eget valg.
-- ---------------------------------------------------------------------
ALTER TABLE purringsbestilling DROP CONSTRAINT IF EXISTS
    purringsbestilling_utfall_check;
ALTER TABLE purringsbestilling ADD CONSTRAINT purringsbestilling_utfall_check
    CHECK (utfall IN ('tillat', 'brudd', 'stopp', 'menneske_kreves')
           OR utfall LIKE 'feil:%');
