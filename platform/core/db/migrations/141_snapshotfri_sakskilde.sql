-- =====================================================================
-- 141 — `tenanter_uten_policysnapshot()` KJENNER SAKSKILDENE UTEN SNAPSHOT
-- =====================================================================
--
-- 041 unntok `domeneovertakelse` fra policysnapshotet, 102 unntok
-- `henvendelse` (CHECK `unntak_snapshot_komplett` KREVER NULL for begge).
-- Tellefunksjonen fra 005 kjente ingen av dem: den meldte tenanten som
-- «uten snapshot» for alltid, og backfillen (db/m37_backfill.py) unntok
-- bare den første. Første henvendelse i unntakskøen på disponit.com
-- (Fjordlys-kampanjen 8/9) fikk backfillen til å fylle snapshotet på en
-- henvendelse, CHECK-en nektet, og hver deploy etterpå ble avbrutt og
-- selv-reversert (#419).
--
-- Listen over snapshotfrie sakskilder står i
-- `m37_backfill.SAKSKILDER_UTEN_SNAPSHOT`; denne funksjonen speiler den.
-- Legges en tredje til, må begge og CHECK-en endres — porten
-- `test_backfill_sakskilde_port.py` leser alle tre.
--
-- Som eierrollen (005 gjorde det samme): CREATE OR REPLACE beholder
-- eier og GRANT-ene fra 005 — det er hele grunnen til at dette ikke er
-- DROP + CREATE.
-- ---------------------------------------------------------------------
SET LOCAL ROLE disponit_m37_claimer;

CREATE OR REPLACE FUNCTION tenanter_uten_policysnapshot()
RETURNS TABLE (tenant TEXT, antall BIGINT)
LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT u.tenant, pg_catalog.count(*)
      FROM public.unntak u
     WHERE (u.sakskilde IS NULL
            OR u.sakskilde NOT IN ('domeneovertakelse', 'henvendelse'))
       AND (u.maks_auto_forsok_snapshot IS NULL
            OR u.policy_versjon IS NULL
            OR u.policy_content_hash IS NULL)
     GROUP BY u.tenant
     ORDER BY u.tenant
$$;

RESET ROLE;
