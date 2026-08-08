-- ============================================================
-- Disponit migrasjon 008 — beslutning→oppdrag-kobling for lese-API-et.
-- Spesifisert i PR-008 v1–v6 + implementeringsklarsignalet (fem vilkår).
--
-- INGEN BEGIN/COMMIT: kjøreren (db/kjorer.py) eier transaksjonen. Feiler
-- et hvilket som helst steg, rulles ALT tilbake — kolonner, constraints og
-- triggere finnes ikke halvveis (Codex-port 2).
--
-- REKKEFØLGEN ER VAKTEN (v6 pkt. 2): backfillen kjører FØR
-- innsettingstriggeren opprettes. Det finnes ingen «migrasjonsmodus» å
-- forfalske — en runtime-innsetting av 'LEGACY_UKJENT' avvises av
-- triggeren uansett hvem som prøver, også migrator.
--
-- AVVIK FRA v5/v6, FLAGGET I PR-EN: `koblingsstatus` har TRE verdier, ikke
-- to. Fase-1 verifikasjonsoppdrag (PR-007, `arbeider._start_fase1`)
-- opprettes FØR noen beslutning finnes — verifikasjonen skjer per design
-- før policybeslutningen, og en sak kan ha flere generasjoner og dermed
-- flere verifikasjonsoppdrag. Med bare KOBLET|LEGACY_UKJENT kunne runtime
-- aldri opprettet et verifikasjonsoppdrag (KOBLET krever en
-- beslutningsloggpost som ikke finnes; LEGACY_UKJENT er forbudt fra
-- runtime), og fase 1 hadde stoppet helt. 'VERIFIKASJON' er derfor en
-- egen, LUKKET tilstand: kun lovlig for oppdragstype='verifikasjon', alltid
-- uten FK, og bundet begge veier (et verifikasjonsoppdrag kan aldri være
-- KOBLET, et forretningsoppdrag kan aldri være VERIFIKASJON).
-- ============================================================

-- ------------------------------------------------------------
-- 1. Kolonner: nullable, INGEN default, INGEN CHECK ennå (v6 steg 1).
--    CHECK før backfill ville brutt umiddelbart på eksisterende rader.
-- ------------------------------------------------------------
ALTER TABLE oppdrag ADD COLUMN IF NOT EXISTS beslutning_loggpost_id BIGINT;
ALTER TABLE oppdrag ADD COLUMN IF NOT EXISTS koblingsstatus TEXT;

-- ------------------------------------------------------------
-- 2. Backfill (v6 steg 2, vilkår V2: fail-hard ved tvetydighet).
--
-- RLS-VINDUET: `oppdrag` og `revisjonslogg` står med FORCE ROW LEVEL
-- SECURITY, og migrator har ingen tenantkontekst — med FORCE på ville
-- hver UPDATE her truffet NULL rader og SETT UT til å virke, og steg 3s
-- kontroll ville vært vakuøst grønn mot en tom radmengde. (Nøyaktig
-- FIX-008-feilklassen: en tilkobling uten kontekst ser ingenting.)
-- FORCE slås derfor av for EIEREN i backfill-vinduet og på igjen rett
-- etter kontrollsteget — alt i samme transaksjon, så en feil underveis
-- ruller også RLS-endringen tilbake. Vanlige roller er uansett bundet:
-- NO FORCE unntar kun tabelleieren.
-- ------------------------------------------------------------
ALTER TABLE oppdrag       NO FORCE ROW LEVEL SECURITY;
ALTER TABLE revisjonslogg NO FORCE ROW LEVEL SECURITY;

-- 2a. Verifikasjonsoppdrag er strukturelt beslutningsløse — deterministisk
--     avledet av oppdragstypen, aldri av matching.
UPDATE oppdrag SET koblingsstatus = 'VERIFIKASJON'
 WHERE oppdragstype = 'verifikasjon' AND koblingsstatus IS NULL;

-- 2b. Forretningsoppdrag: KUN entydig, direkte evidens. Fase-2-beslutningen
--     logges med `idempotency_key = repair_operation_id` og
--     `kilde='arbeidskapabilitet'` (arbeider.behandle_en/_fase2 →
--     `klient.beslutt(idempotency_key=rid)`), og bare en TILLAT skaper
--     oppdrag. Alle fire vilkårene fra v5 pkt. 1 ligger i joinen: samme
--     tenant, riktig logghendelsestype (kilde + TILLAT), riktig
--     reparasjonsidentitet (idempotency_key), og nøyaktig ÉN kandidat
--     (HAVING COUNT(*) = 1 — MIN() prosjekterer da RADEN, ikke et valg;
--     null eller flere kandidater faller helt utenfor og blir
--     LEGACY_UKJENT i 2c. Ingen MIN/MAX/LIMIT-semantikk avgjør noe valg).
UPDATE oppdrag o
   SET beslutning_loggpost_id = k.loggpost_id, koblingsstatus = 'KOBLET'
  FROM (SELECT r.tenant, r.idempotency_key, MIN(r.id) AS loggpost_id
          FROM revisjonslogg r
         WHERE r.kilde = 'arbeidskapabilitet'
           AND r.beslutning = 'TILLAT'
           AND r.idempotency_key IS NOT NULL
         GROUP BY r.tenant, r.idempotency_key
        HAVING COUNT(*) = 1) k
 WHERE o.tenant = k.tenant
   AND o.repair_operation_id = k.idempotency_key
   AND o.oppdragstype <> 'verifikasjon'
   AND o.koblingsstatus IS NULL;

-- 2c. Resten mangler entydig evidens → ærlig LEGACY_UKJENT (fail-closed:
--     heller ingen kobling enn feil kobling).
UPDATE oppdrag SET koblingsstatus = 'LEGACY_UKJENT'
 WHERE koblingsstatus IS NULL;

-- ------------------------------------------------------------
-- 3. Ingen rad uten koblingsstatus (v6 steg 3, fail-hard).
-- ------------------------------------------------------------
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.oppdrag WHERE koblingsstatus IS NULL) THEN
        RAISE EXCEPTION 'migrasjon 008: backfill ufullstendig — avbryter';
    END IF;
END $$;

-- RLS-vinduet lukkes: FORCE på igjen for begge tabellene.
ALTER TABLE oppdrag       FORCE ROW LEVEL SECURITY;
ALTER TABLE revisjonslogg FORCE ROW LEVEL SECURITY;

-- ------------------------------------------------------------
-- 4. Kompositt-FK (v6 steg 4). Tenantbundet som alle andre FK-er.
-- ------------------------------------------------------------
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'oppdrag_beslutning_fk') THEN
        ALTER TABLE public.oppdrag ADD CONSTRAINT oppdrag_beslutning_fk
            FOREIGN KEY (tenant, beslutning_loggpost_id)
            REFERENCES public.revisjonslogg (tenant, id);
    END IF;
END $$;

-- ------------------------------------------------------------
-- 5. CHECK: NOT VALID først, så VALIDATE (v6 steg 5). Radene ER
--    konsistente etter backfillen; NOT VALID+VALIDATE beviser det uten å
--    holde en tyngre lås enn nødvendig.
--
--    Bindingen er BEGGE veier: KOBLET er forbudt for verifikasjonsoppdrag
--    (de har ingen beslutning å koble til), og VERIFIKASJON er forbudt for
--    alt annet (en manglende kobling skal hete LEGACY_UKJENT, aldri
--    skjules bak verifikasjonsunntaket).
-- ------------------------------------------------------------
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'oppdrag_kobling_konsistent') THEN
        ALTER TABLE public.oppdrag ADD CONSTRAINT oppdrag_kobling_konsistent
        CHECK (
            (koblingsstatus = 'KOBLET'
                 AND beslutning_loggpost_id IS NOT NULL
                 AND oppdragstype <> 'verifikasjon') OR
            (koblingsstatus = 'LEGACY_UKJENT'
                 AND beslutning_loggpost_id IS NULL) OR
            (koblingsstatus = 'VERIFIKASJON'
                 AND beslutning_loggpost_id IS NULL
                 AND oppdragstype = 'verifikasjon')
        ) NOT VALID;
        ALTER TABLE public.oppdrag
            VALIDATE CONSTRAINT oppdrag_kobling_konsistent;
    END IF;
END $$;

-- ------------------------------------------------------------
-- 6+7. NOT NULL + default for fremtidige innsettinger (v6 steg 6–7).
--      Default er KOBLET: den som glemmer å sette status på et
--      forretningsoppdrag møter CHECK-en (KOBLET krever FK), ikke stillhet.
-- ------------------------------------------------------------
ALTER TABLE oppdrag ALTER COLUMN koblingsstatus SET NOT NULL;
ALTER TABLE oppdrag ALTER COLUMN koblingsstatus SET DEFAULT 'KOBLET';

-- ------------------------------------------------------------
-- 8. Kardinalitet: én beslutning → maks ett oppdrag (v4 pkt. 1, vilkår V2).
--    Partiell — NULL-rader (LEGACY_UKJENT/VERIFIKASJON) deltar ikke.
--    Finnes det duplikater i eksisterende data, STOPPER opprettelsen her
--    migrasjonen med PostgreSQLs egen diagnostikk — migrasjonen velger
--    aldri ett av dem automatisk.
-- ------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS oppdrag_en_per_beslutning
    ON oppdrag (tenant, beslutning_loggpost_id)
    WHERE beslutning_loggpost_id IS NOT NULL;

-- ------------------------------------------------------------
-- 9. Lese-API-ets indekser. Append-only tabeller — billige å bygge.
-- ------------------------------------------------------------
-- Keyset-paginering av beslutninger: (tenant, ts, id) dekker både DESC-
-- listen og tupler-predikatet (ts,id) < (…).
CREATE INDEX IF NOT EXISTS revisjonslogg_tenant_ts_id
    ON revisjonslogg (tenant, ts, id);
-- Arbeiderens (og backfillens) entydige oppslag fase-2-beslutning ←
-- repair_operation_id.
CREATE INDEX IF NOT EXISTS revisjonslogg_idem
    ON revisjonslogg (tenant, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
-- Beslutningsdetaljens unntaksoppslag (beslutning → unntak via loggpost).
CREATE INDEX IF NOT EXISTS unntak_loggpost
    ON unntak (tenant, loggpost_id);
-- Historikk-endepunktets keyset (ts ASC, id ASC per sak).
CREATE INDEX IF NOT EXISTS unntak_historikk_side
    ON unntak_historikk (tenant, unntak_id, ts, id);

-- ------------------------------------------------------------
-- 10. Runtime-vakten — TIL SLUTT (v6 steg 9), ETTER at backfillen er
--     ferdig. Fra nå av finnes ingen vei til 'LEGACY_UKJENT' for noen
--     rolle; en fremtidig legacy-reparasjon må droppe+gjenopprette vakten
--     i en egen, reviewet migrasjon (v6 pkt. 2 — deklarert, ikke bygget).
--
--     Herding (vilkår V3, samme mønster som 005-funksjonene):
--     - `SET search_path = pg_catalog` + full skjemakvalifisering.
--     - Eies av migrator (skjemaeieren) — runtime-rollen `disponit` eier
--       verken tabell eller trigger og kan hverken deaktivere eller endre
--       dem; `session_replication_role` krever superbruker.
--     - Ingen custom settings konsulteres — det finnes ingen flagg å sette.
--     - DELETE/TRUNCATE er allerede forbudt av `oppdrag_ingen_sletting`
--       (005) for alle rader, uansett koblingsstatus.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION oppdrag_koblingsvakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.koblingsstatus = 'LEGACY_UKJENT' THEN
            RAISE EXCEPTION
                'oppdrag: LEGACY_UKJENT kan kun settes av migrasjon 008 — '
                'runtime skal levere beslutning_loggpost_id (KOBLET) eller '
                'et verifikasjonsoppdrag (VERIFIKASJON)';
        END IF;
        IF NEW.koblingsstatus NOT IN ('KOBLET', 'VERIFIKASJON') THEN
            RAISE EXCEPTION 'oppdrag: ukjent koblingsstatus %',
                NEW.koblingsstatus;
        END IF;
        RETURN NEW;
    END IF;
    -- UPDATE: koblingen er uforanderlig etter innsetting (v5 pkt. 1).
    IF NEW.koblingsstatus IS DISTINCT FROM OLD.koblingsstatus
       OR NEW.beslutning_loggpost_id IS DISTINCT FROM OLD.beslutning_loggpost_id THEN
        RAISE EXCEPTION
            'oppdrag: koblingsstatus og beslutning_loggpost_id er '
            'uforanderlige etter innsetting';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS oppdrag_koblingslaas ON oppdrag;
CREATE TRIGGER oppdrag_koblingslaas BEFORE INSERT OR UPDATE ON oppdrag
    FOR EACH ROW EXECUTE FUNCTION oppdrag_koblingsvakt();
