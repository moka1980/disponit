-- 086 — M-16 PR-B: indeksene for nøkkeltallenes to uindekserte anker.
--
-- DOMMEN BAK TIDSPUNKTET: fase 2-planen sa «vent på målt > 100 ms» —
-- men det premisset var bundet til FORHÅNDSVINDUENE, der skannet aldri
-- kunne bli bredere enn 30 døgn bakover fra nå. PR-C åpner fritt
-- intervall ([fra, til) hvor som helst i historikken, og da er «hvor
-- dyrt kan det bli» ikke lenger begrenset av vindusvelgeren. Derfor
-- merges indeksene FØR intervallet — rekkefølgen er selve vedtaket.
--
-- To av M-16-definernes skann har i dag ingen indeks som starter på
-- sitt (tenant, tidsanker)-par:
--
--   * m16_oppdrag ankrer på `status_ts` (siste statusovergang — DEN
--     hendelsen vinduet teller). Nærmeste indekser er `oppdrag_ko`
--     (delindeks, starter på eiermodul) og `oppdrag_tenant_keyset`
--     (tenant, opprettet DESC, id DESC — feil tidsanker): hele
--     tellingen er et sekvensielt skann over tenantens historikk.
--   * m16_tick ankrer på `vindu_start` (perioden kontrollen gjaldt,
--     SP-6). Eneste vei er i dag PK-en (plan_id, vindu_start): et
--     hopp-skann med ett indekssøk PER PLAN, som i tillegg leser alle
--     tenanters rader i vinduet og filtrerer bort de fremmede etterpå.
--
-- Formen speiler skannene de betjener (samme grep som 066): `tenant`
-- er likhetsleddet, tidsankeret range-leddet. `INCLUDE (utfall)` på
-- tick-indeksen fordi utfallet er HELE kolonnesettet m16_tick leser
-- utover ankeret — tellingen kan da svares fra indeksen alene, uten
-- heap-oppslag per rad. m16_oppdrag leser `status` på samme måte, men
-- `status` muterer (opprettet→plukket→utfort/…), og en INCLUDE-kolonne
-- som muterer koster en indeksoppdatering også når nøkkelen står
-- stille — `utfall` er append-only (044: tick_ingen_update), `status`
-- er selve tilstandsmaskinen. Derfor bærer bare tick-indeksen last.
--
-- MÅLT på seedet base (250 000 oppdrag / 498 000 tick over 250 planer,
-- to tenants, PostgreSQL 18, EXPLAIN ANALYZE på definernes indre
-- spørringer, 24t-vindu, median av tre):
--   m16_oppdrag: 47,6 ms (Parallel Seq Scan, 249 714 rader forkastet)
--            →    0,33 ms (Bitmap Index Scan på oppdrag_status_ts,
--                          ett indekssøk, ingen rad forkastet)
--   m16_tick:     9,9 ms (Bitmap-hopp-skann på PK-en, 286 indekssøk,
--                         12 230 rader lest, 2 590 forkastet)
--            →    4,3 ms (Index Only Scan på tick_vindu, ett søk,
--                         0 heap-oppslag, ingen rad forkastet)
--
-- PRISEN, sagt høyt: én indeks til på hver av to skrivetunge tabeller
-- er skrivearbeid på hver insert — og for `oppdrag` også på hver
-- statusovergang, siden `status_ts` da endres og raden må flyttes i
-- indeksen. Det er samme vei som i dag betaler for `oppdrag_ko`,
-- `oppdrag_sak` og `oppdrag_tenant_keyset`.
--
-- Ingen backfill og ingen definer-endring: 084-settet står ordrett
-- (porten i test_m16_nokkeltall.py måler begge deler).

CREATE INDEX IF NOT EXISTS oppdrag_status_ts
    ON oppdrag (tenant, status_ts);

CREATE INDEX IF NOT EXISTS tick_vindu
    ON bestillingsplan_tick (tenant, vindu_start) INCLUDE (utfall);
