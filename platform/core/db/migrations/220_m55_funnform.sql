-- 220 — M-55: VARSELREGISTERET FÅR HUSETS FUNNFORM, SOM AVLEDNING.
--
-- Alle andre sveipmoduler skriver `funntype` og ett subjekt per rad. M-55
-- skriver `varseltype`, og subjektet er ENTEN funnet (funnivå) ELLER
-- merket (merkenivå, `funn_id IS NULL`). Den generiske sveiplesten leser
-- `funntype` og én subjektkolonne; å lære fellesverktøyet en tredje form
-- ville endret bytene i bevisroten til hver modul som alt er målt.
--
-- TO GENERERTE KOLONNER, ALDRI SKREVET: `funntype` er `varseltype`, og
-- `subjekt` er funnet når varselet gjelder et funn, ellers merket — nøyaktig
-- nivåregelen `merkevarevarsel_nivaa_folger_type` allerede håndhever. De er
-- STORED og avledet av raden selv, så de kan aldri fortelle noe annet enn
-- kolonnene de er regnet av, og ingen dør, sveip eller vakt endres.
--
-- Tabellen eies av migratoren (120 §1); ingen rolleveksling trengs.

ALTER TABLE merkevarevarsel
    ADD COLUMN IF NOT EXISTS funntype TEXT
        GENERATED ALWAYS AS (varseltype) STORED,
    ADD COLUMN IF NOT EXISTS subjekt TEXT
        GENERATED ALWAYS AS (coalesce(funn_id, merkevare_id)::text) STORED;
