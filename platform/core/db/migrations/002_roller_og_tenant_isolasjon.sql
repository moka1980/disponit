-- ============================================================
-- Disponit migrasjon 002 — rolleskille og ekte tenant-isolasjon
-- Svar på Codex-review av PR-004, to P1-funn:
--
--   1. Runtime-rollen eide tabellene og kunne dermed skru av eller
--      slette append-only-triggerne. En vakt du selv kan fjerne er
--      ingen vakt. Migrator og runtime skilles.
--   2. `revisjonslogg.tenant` tillot NULL, og en indeks er ikke
--      isolasjon — den er en ytelsesdetalj. Isolasjonen håndheves nå
--      i databasegrensen med row level security.
--
-- Kjøres av MIGRATOR-rollen (eier), aldri av runtime.
-- Idempotent: trygg å kjøre flere ganger.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1. tenant er obligatorisk. Gamle rader uten tenant merkes med en
--    reservert verdi i stedet for å slettes — revisjonsloggen er
--    append-only, og historikk som ikke lar seg henføre skal være
--    synlig som nettopp det.
-- ------------------------------------------------------------
-- Append-only-triggeren blokkerer ogsaa denne UPDATE-en — den skiller ikke
-- mellom en angriper og en migrasjon. Den slaas derfor av og paa igjen
-- INNENFOR transaksjonen: feiler noe, ruller alt tilbake og triggeren er
-- fortsatt paa. Dette krever eierskap, og er nettopp derfor migrator er en
-- annen rolle enn runtime: runtime kan aldri gjoere dette.
ALTER TABLE revisjonslogg DISABLE TRIGGER revisjonslogg_ingen_endring;
UPDATE revisjonslogg SET tenant = '<ukjent>' WHERE tenant IS NULL;
ALTER TABLE revisjonslogg ENABLE TRIGGER revisjonslogg_ingen_endring;

ALTER TABLE revisjonslogg ALTER COLUMN tenant SET NOT NULL;

ALTER TABLE revisjonslogg DROP CONSTRAINT IF EXISTS revisjonslogg_tenant_ikke_tom;
ALTER TABLE revisjonslogg ADD  CONSTRAINT revisjonslogg_tenant_ikke_tom
    CHECK (length(btrim(tenant)) > 0);

ALTER TABLE frekvens_hendelser DROP CONSTRAINT IF EXISTS frekvens_tenant_ikke_tom;
ALTER TABLE frekvens_hendelser ADD  CONSTRAINT frekvens_tenant_ikke_tom
    CHECK (length(btrim(tenant)) > 0);

-- ------------------------------------------------------------
-- 2. Tenant-isolasjon i databasegrensen.
--
--    Sesjonsvariabelen `disponit.tenant` settes per transaksjon av
--    `db.pg.sett_tenant()`. Er den ikke satt, gir current_setting(...,
--    true) NULL, og både USING og WITH CHECK blir usanne: null rader
--    synlige, ingen rader skrivbare. Fail-closed — glemmer koden å
--    sette tenant, stopper databasen den, i stedet for å vise alt.
--
--    FORCE gjør at policyen også gjelder tabelleieren. Uten FORCE er
--    eieren unntatt, og da ville hele isolasjonen forsvunnet i det
--    migrator-rollen (eller en feilkonfigurert runtime) kobler seg på.
-- ------------------------------------------------------------
ALTER TABLE revisjonslogg      ENABLE ROW LEVEL SECURITY;
ALTER TABLE revisjonslogg      FORCE  ROW LEVEL SECURITY;
ALTER TABLE frekvens_hendelser ENABLE ROW LEVEL SECURITY;
ALTER TABLE frekvens_hendelser FORCE  ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolasjon ON revisjonslogg;
CREATE POLICY tenant_isolasjon ON revisjonslogg
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

DROP POLICY IF EXISTS tenant_isolasjon ON frekvens_hendelser;
CREATE POLICY tenant_isolasjon ON frekvens_hendelser
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

INSERT INTO migrasjoner (versjon) VALUES (2) ON CONFLICT DO NOTHING;

COMMIT;
