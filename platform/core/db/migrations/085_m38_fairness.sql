-- 084 — M-38 PR-A: fairness i køene (per-tenant plass-rangering)
--
-- Én tenants backlog skal ikke sulte en annens enkeltssak: begge
-- claim-funksjonene får en per-tenant plass-rangering som TREDJE
-- sorteringsnøkkel. Fase- og prioritetsklassene beholder forrang
-- (pinnede porter i test_m37.py) — en tenant med hoy-backlog går
-- foran andres normalsaker; det er prioritetsdoktrine, ikke
-- fairness-feil. Ingen per-tenant-tak på oppdragskøen i v1, og ingen
-- ny indeks uten måling.
--
-- Formen (dommen, ratifisert): PostgreSQL forbyr FOR UPDATE sammen
-- med vindusfunksjoner, så rangeringen skjer ulåst i en MATERIALIZED
-- CTE (`rangert`), og kandidat-nivået JOINer den og GJENTAR
-- spisbarhetsfilteret — FOR UPDATE-rechecken måler kun det nivåets
-- predikater (041-lærdommen). Vindustaket plass <= 100 begrenser
-- rangeringskostnaden, aldri claimbarheten (LEFT JOIN + coalesce 101).
--
-- Kroppene er KOPIERT byte for byte fra gjeldende definisjoner —
-- claim_neste_sak fra 041 (§18), claim_neste_oppdrag fra 049 — og
-- diff-endret KUN i kandidatvalget. CREATE OR REPLACE med uendrede
-- signaturer beholder eier og grants; ingen DROP.

SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION public.claim_neste_sak(p_claim_id text, p_lease_s integer DEFAULT 120)
 RETURNS TABLE(tenant text, id bigint, handling text, kategori text, loggpost_id bigint, claim_generation integer, claim_utloper timestamp with time zone, forsok integer, maks_auto_forsok_snapshot integer, fase text, verification_generation integer)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE
    v_lease INT;
BEGIN
    IF p_claim_id IS NULL OR p_claim_id !~ '^[0-9a-f]{32,}$' THEN
        RAISE EXCEPTION 'claim_neste_sak: ugyldig claim_id-format';
    END IF;
    v_lease := least(greatest(coalesce(p_lease_s, 120), 30), 600);

    -- 041 (§18): kandidaten velges i en MATERIALIZED CTE, IKKE i en
    -- FROM-subspørring. MÅLT på rebuilt base: adjudikator-policyen (§9)
    -- endret planformen slik at subspørringen ble INNER side i en nested
    -- loop og RESKANNET per ytre rad — og fordi FOR UPDATE re-sjekker
    -- radens SISTE versjon, så hver rescan forrige rad som alt claimet
    -- ('under_behandling'), hoppet videre, og ÉN claim tømte hele køen
    -- (8/8 rader, alle med samme claim_id). LIMIT 1 i en subspørring er
    -- bare en grense per EVALUERING; MATERIALIZED er garantien for at
    -- evalueringen skjer nøyaktig én gang. Feilen var latent siden 005 —
    -- policy-endringen gjorde den målbar, den skapte den ikke.
    RETURN QUERY
    WITH rangert AS MATERIALIZED (
      -- M-38: per-tenant PLASS i køen — fairness som TREDJE
      -- sorteringsnøkkel (fase- og prioritetsklassene beholder forrang).
      -- Partisjonen teller også 'under_behandling': arbeid en tenant
      -- alt har i flukt skyver tenantens neste saker bakover — det er
      -- hele interleaving-mekanismen. Terminale statuser er IKKE med:
      -- historikk skal aldri koste plass i køen — og det skal heller
      -- ikke DØD VEKT: en 'ny' sak med oppbrukt forsøksbudsjett kan
      -- aldri claimes og rangerer derfor ikke (CodeRabbit-funn; uten
      -- dette ville 100 budsjett-tomme saker skjøvet tenantens levende
      -- saker bak vinduet for godt). Ulåst (ingen FOR
      -- UPDATE): PostgreSQL forbyr FOR UPDATE sammen med
      -- vindusfunksjoner — derfor to nivåer; låsingen skjer i
      -- kandidat under, som GJENTAR hele spisbarhetsfilteret, for
      -- FOR UPDATE-rechecken måler kun dette nivåets predikater
      -- (041-lærdommen). Vindustaket plass <= 100 begrenser
      -- rangeringsKOSTNADEN, aldri claimbarheten: kandidat LEFT JOINer
      -- og coalescer manglende plass til 101 (bak hele vinduet).
      SELECT r.tenant, r.id, r.plass
        FROM (SELECT q.tenant, q.id,
                     pg_catalog.row_number() OVER (
                         PARTITION BY q.tenant
                         ORDER BY q.ts, q.id) AS plass
                FROM public.unntak q
               WHERE q.sakstype = 'normal'
                 AND q.status IN ('ny','verifikasjon_klar',
                                  'verifikasjon_retry_klar',
                                  'under_behandling')
                 AND (q.status <> 'ny'
                      OR q.forsok < least(
                             coalesce(q.maks_auto_forsok_snapshot, 0),
                             3))) r
       WHERE r.plass <= 100
    ),
    kandidat AS MATERIALIZED (
      SELECT k.tenant, k.id, k.status
        FROM public.unntak k
        LEFT JOIN rangert r ON r.tenant = k.tenant AND r.id = k.id
       WHERE k.sakstype = 'normal'
         AND k.status IN ('ny','verifikasjon_klar','verifikasjon_retry_klar')
         AND (k.status <> 'ny'
              OR k.forsok < least(coalesce(k.maks_auto_forsok_snapshot, 0), 3))
         AND (SELECT pg_catalog.count(*) FROM public.unntak b
               WHERE b.tenant = k.tenant
                 AND b.status = 'under_behandling'
                 AND b.claim_utloper > pg_catalog.now()) < 5
       -- Klar-tilstandene går FØRST: en sak som har ventet på en
       -- verifikator har allerede brukt tid, og å la den stå bak ferske
       -- saker ville gjort tofaseveien systematisk tregere enn enfase.
       ORDER BY (CASE WHEN k.status <> 'ny' THEN 0 ELSE 1 END),
                (CASE k.prioritet WHEN 'hoy' THEN 0 ELSE 1 END),
                coalesce(r.plass, 101), k.ts, k.id
         FOR UPDATE OF k SKIP LOCKED
       LIMIT 1
    )
    UPDATE public.unntak u
       SET status = 'under_behandling',
           claim_id = p_claim_id,
           claim_generation = u.claim_generation + 1,
           claim_utloper = pg_catalog.now() + (v_lease || ' seconds')::INTERVAL,
           -- Forsøkstelleren teller BEHANDLINGSforsøk. En fase-2-claim er
           -- ikke et nytt forsøk på saken — den er andre halvdel av det
           -- samme. Uten dette skillet ville en tofasesak brukt opp
           -- budsjettet sitt dobbelt så fort som en enfasesak.
           forsok = u.forsok + CASE WHEN u.status = 'ny' THEN 1 ELSE 0 END
      FROM kandidat k
     WHERE u.tenant = k.tenant AND u.id = k.id
    RETURNING u.tenant, u.id, u.handling, u.kategori, u.loggpost_id,
              u.claim_generation, u.claim_utloper, u.forsok,
              u.maks_auto_forsok_snapshot,
              -- FASEN FØLGER STATUSEN, ikke generasjonstelleren.
              --
              -- MÅLT: med `verification_generation = 0 => ny, ellers fase2`
              -- rapporterte en sak i `verifikasjon_retry_klar` fase2, og
              -- arbeideren lette etter et positivt bevis som per definisjon
              -- ikke fantes — retryen ga `manuell: intet_positivt_bevis`.
              -- Retry-veien kunne dermed ALDRI åpne en ny generasjon, selv
              -- om både statusmaskinen og kommentarene sa at den skulle.
              --
              -- Statusen er den autoritative fasen: `verifikasjon_klar`
              -- betyr «et bevis foreligger», `verifikasjon_retry_klar`
              -- betyr «forrige runde slo feil, kjør en ny». Telleren sier
              -- bare hvor mange runder som har vært.
              --
              -- `k` er raden slik den var FØR UPDATE-en; `u` ville gitt
              -- `under_behandling` for alle tre.
              CASE k.status WHEN 'verifikasjon_klar' THEN 'fase2'
                            WHEN 'verifikasjon_retry_klar' THEN 'retry'
                            ELSE 'ny' END,
              u.verification_generation;
END $function$
;
RESET ROLE;

-- ------------------------------------------------------------
-- claim_neste_oppdrag — KOPI av gjeldende kropp (049), diff-endret
-- KUN i kandidat-SELECTen i loopen (se toppkommentaren).
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION claim_neste_oppdrag(
    p_modul_id TEXT, p_prefiks TEXT[], p_claim_id TEXT,
    p_lease_s INT DEFAULT 300, p_release_id TEXT DEFAULT NULL,
    p_miljo TEXT DEFAULT NULL, p_module_epoch BIGINT DEFAULT NULL)
RETURNS TABLE (id BIGINT, tenant TEXT, unntak_id BIGINT, oppdragstype TEXT,
               handling TEXT, repair_operation_id TEXT,
               payload_kryptert BYTEA, key_id TEXT, nonce BYTEA,
               owner_generation INT, utforelsesfrist TIMESTAMPTZ,
               evidensfrist TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_lease INT := least(greatest(coalesce(p_lease_s, 300), 30), 3600);
    v_hoppet BIGINT[] := ARRAY[]::BIGINT[];
    v_id BIGINT; v_ot TEXT; r RECORD; v_ok BOOLEAN;
    v_b_modul TEXT; v_b_ver INT; v_b_hash TEXT; v_b_epoch BIGINT;
    v_b_rel TEXT; v_b_miljo TEXT;
BEGIN
    IF p_claim_id IS NULL OR p_claim_id !~ '^[0-9a-f]{32,}$' THEN
        RAISE EXCEPTION 'claim_neste_oppdrag: ugyldig claim_id-format';
    END IF;
    IF p_modul_id IS NULL OR length(btrim(p_modul_id)) = 0 THEN
        RAISE EXCEPTION 'claim_neste_oppdrag: modul_id mangler';
    END IF;

    -- Codex P1: DELT gjerde mot registrering av oppdragstyper — SAMME nøkkel som
    -- `registrer_oppdragstype` tar EKSKLUSIVT (014). Uten dette er «uregistrert»
    -- en avgjørelse tatt på et snapshot: en `registrer_oppdragstype` kan committe
    -- ETTER at kandidatsøket og registeroppslaget her har lest, men FØR claimet
    -- committer, og oppdraget blir da claimet UBUNDET (ingen kontrakt, ingen
    -- epoch) og uten aktiv-modul-/deployment-portene — nøyaktig den porten
    -- registreringen skulle innføre. Gjerdet tas FØR kandidatsøket, som selv leser
    -- `oppdragstype_register` i predikatet, så begge lesningene ligger innenfor.
    -- Delt: samtidige claims (også på tvers av moduler) sperrer ikke for
    -- hverandre; kun en registrering venter, og registrering er en sjelden
    -- admin-/deployhandling. Låserekkefølgen er global → modul → oppdragsrad i
    -- ALLE stier (overgangsfunksjonene tar bare modul-låsen), så ingen syklus.
    PERFORM pg_advisory_xact_lock_shared(
        hashtextextended('modulregister:oppdragstype', 0));

    LOOP
        -- Oppdragslås: neste kandidat for eiermodulen (SKIP LOCKED). Den betingede
        -- binding-tilgjengeligheten er ALT et predikat her (Codex P2: da låses
        -- ikke en uclaimbar backlog rad for rad) — en registrert oppdragstype
        -- selekteres bare når kallerens deployment er claiming og modulen aktiv.
        -- v_hoppet holder kun de sjeldne som taper race-en mot noddeaktiver under
        -- modul-låsen (re-verifiseringen nedenfor); SKIP LOCKED hopper ikke over
        -- rader denne transaksjonen selv har låst.
        WITH rangert AS MATERIALIZED (
          -- M-38: per-tenant PLASS i eiermodulens kø — fairness-
          -- interleaving mellom tenanter. Partisjonen teller også
          -- 'plukket' (både levende leases og reclaimbare): arbeid i
          -- flukt skyver tenantens neste oppdrag bakover, og reclaim-
          -- grenen rangeres i SAMME partisjon på opprettet. Oppdrag
          -- med passert utførelsesfrist er DØD VEKT — aldri claimbare
          -- — og rangerer derfor ikke (CodeRabbit-funn). Ulåst
          -- (ingen FOR UPDATE — vindusfunksjon); låsingen skjer i
          -- kandidat-SELECTen under, som beholder hele
          -- spisbarhetsfilteret uendret. Vindustaket plass <= 100
          -- begrenser rangeringskostnaden, aldri claimbarheten:
          -- LEFT JOIN + coalesce til 101 setter rader utenfor vinduet
          -- bakerst i stedet for å gjemme dem.
          SELECT rg.id, rg.plass
            FROM (SELECT q.id,
                         pg_catalog.row_number() OVER (
                             PARTITION BY q.tenant
                             ORDER BY q.opprettet, q.id) AS plass
                    FROM public.oppdrag q
                   WHERE q.eiermodul = p_modul_id
                     AND q.status IN ('opprettet','plukket')
                     AND q.utforelsesfrist > now()) rg
           WHERE rg.plass <= 100
        )
        SELECT k.id, k.oppdragstype INTO v_id, v_ot
          FROM public.oppdrag k
          LEFT JOIN rangert rg ON rg.id = k.id
         WHERE (
                 k.status = 'opprettet'
                 OR (k.status = 'plukket' AND k.owner_lease_utloper IS NOT NULL
                     AND k.owner_lease_utloper < now())
               )
           AND k.eiermodul = p_modul_id
           AND p_prefiks IS NOT NULL
           AND array_length(p_prefiks, 1) > 0
           AND EXISTS (SELECT 1 FROM unnest(p_prefiks) AS pre
                        WHERE k.handling LIKE pre || '%')
           AND k.utforelsesfrist > now()
           AND k.id <> ALL (v_hoppet)
           AND (
                 NOT EXISTS (SELECT 1 FROM public.oppdragstype_register reg
                              WHERE reg.oppdragstype = k.oppdragstype)
                 OR EXISTS (
                     SELECT 1 FROM public.oppdragstype_register reg
                       JOIN public.modulhode h
                         ON h.modul_id = reg.eiermodul AND h.status = 'aktiv'
                        AND h.module_epoch IS NOT DISTINCT FROM p_module_epoch
                       JOIN public.moduldeployment d
                         ON d.modul_id = reg.eiermodul
                        AND d.kontraktversjon = reg.kontraktversjon
                        AND d.kontrakt_hash = reg.kontrakt_hash
                        AND d.release_id = p_release_id AND d.miljo = p_miljo
                        AND d.livslop = 'claiming'
                      WHERE reg.oppdragstype = k.oppdragstype
                        AND reg.eiermodul = p_modul_id)
               )
         ORDER BY coalesce(rg.plass, 101), k.opprettet, k.id
           FOR UPDATE OF k SKIP LOCKED
         LIMIT 1;
        IF NOT FOUND THEN
            RETURN;   -- tom kø (eller alle gjenværende ikke claimbare av kalleren)
        END IF;

        -- Tabellen aliases (reg): funksjonens RETURNS TABLE-kolonner (id, tenant,
        -- oppdragstype, ...) er OUT-variabler i skopet og ville ellers kollidert
        -- med en ukvalifisert kolonnereferanse (AmbiguousColumn).
        SELECT reg.eiermodul, reg.kontraktversjon, reg.kontrakt_hash INTO r
          FROM public.oppdragstype_register reg WHERE reg.oppdragstype = v_ot;

        IF NOT FOUND THEN
            -- Legacy: uregistrert oppdragstype → ingen binding (som før).
            v_b_modul := NULL; v_b_ver := NULL; v_b_hash := NULL; v_b_epoch := NULL;
            v_b_rel := NULL; v_b_miljo := NULL;
        ELSE
            -- Modul-lås, DELT (Codex P2): claims skal serialiseres mot
            -- overgangene (nødstopp/status/releasebytte tar den EKSKLUSIVT),
            -- men ikke mot hverandre — en eksklusiv lås her ville køet alle
            -- modulens pollere bak hele claim-transaksjonen (API-et committer
            -- først etter dekryptering, minimering og kapabilitetsutstedelse),
            -- selv når SKIP LOCKED alt har gitt dem hver sin rad. Delt lås gir
            -- samme gjerde mot nødstopp: den venter til et evt. samtidig
            -- noddeaktiver_modul har committet, så re-lesingen under er FERSK.
            PERFORM pg_advisory_xact_lock_shared(
                hashtextextended('modul:' || r.eiermodul, 0));
            SELECT (r.eiermodul = p_modul_id)   -- Codex P1: eiermodulen eier typen
               AND EXISTS (SELECT 1 FROM public.modulhode h
                            WHERE h.modul_id = r.eiermodul AND h.status = 'aktiv'
                              AND h.module_epoch IS NOT DISTINCT FROM p_module_epoch)
               AND EXISTS (SELECT 1 FROM public.moduldeployment d
                            WHERE d.modul_id = r.eiermodul
                              AND d.kontraktversjon = r.kontraktversjon
                              AND d.kontrakt_hash = r.kontrakt_hash
                              AND d.release_id = p_release_id      -- Codex P1:
                              AND d.miljo = p_miljo                -- KALLERENS
                              AND d.livslop = 'claiming')          -- deployment
              INTO v_ok;
            IF NOT v_ok THEN
                v_hoppet := array_append(v_hoppet, v_id);
                CONTINUE;   -- ikke claimbar av denne kalleren; prøv neste
            END IF;
            v_b_modul := r.eiermodul; v_b_ver := r.kontraktversjon;
            v_b_hash := r.kontrakt_hash; v_b_epoch := p_module_epoch;
            -- Verifisert av porten rett over: NETTOPP denne releasen er
            -- den claiming deploymenten for kontrakten (Codex P1, runde
            -- 20). Da er den også den eneste som kan ha tatt raden.
            v_b_rel := p_release_id; v_b_miljo := p_miljo;
        END IF;

        RETURN QUERY
        UPDATE public.oppdrag o
           SET status = 'plukket',
               owner_claim_id = p_claim_id,
               owner_generation = o.owner_generation + 1,
               -- Codex P1 (037): leasen dekker oppdragets EGEN frist.
               -- `greatest(...)` strekker den til `utforelsesfrist` når
               -- kallerens tall er kortere enn arbeidet plattformen selv
               -- har gitt tid til; `least(...)` holder funksjonens tak på
               -- 3600 s. Etter fristen er raden uansett ikke claimbar
               -- (`k.utforelsesfrist > now()` over), så en lease som
               -- slutter der stenger nøyaktig det vinduet der en
               -- «utløpt» lease bare betydde at eieren fortsatt jobbet.
               owner_lease_utloper = least(
                   now() + '3600 seconds'::INTERVAL,
                   greatest(now() + (v_lease || ' seconds')::INTERVAL,
                            o.utforelsesfrist)),
               modul_id = v_b_modul, kontraktversjon = v_b_ver,
               kontrakt_hash = v_b_hash, module_epoch = v_b_epoch,
               claim_release_id = v_b_rel, claim_miljo = v_b_miljo,
               -- Portens egen klokke, ikke kallerens påstand — derfor
               -- stemples den også på legacy-grenen, der release-sporet
               -- står NULL: NÅR raden ble tatt er sant uansett om det
               -- finnes en verifisert release å skrive ned.
               -- `coalesce`: første claim vinner (se vakten over).
               forste_claim_ts = coalesce(o.forste_claim_ts, now())
         WHERE o.id = v_id
        RETURNING o.id, o.tenant, o.unntak_id, o.oppdragstype, o.handling,
                  o.repair_operation_id, o.payload_kryptert, o.key_id, o.nonce,
                  o.owner_generation, o.utforelsesfrist, o.evidensfrist;
        RETURN;
    END LOOP;
END $$;
RESET ROLE;
