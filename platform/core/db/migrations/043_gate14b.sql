-- ============================================================
-- 043 — GATE 14b: kansellering med fencing (klarsignal 2026-08-18)
--
-- Klarsignalet sier «migrasjon 041»; det nummeret var foreldet i det
-- arbeidet startet (overtakelsessaken tok det) — samme lærdom som sist:
-- NÅ-blokkas numre er skrevet før forrige PR landet.
--
-- 14a står i produksjon: avvis på sak med levende oppdrag → 409
-- `utestaaende_oppdrag`. 14b er hva som skjer I STEDET FOR 409:
-- kansellering med fencing — nei-et tar effekt i det databasen kan
-- BEVISE at ingen kvittering fra det gamle claimet noen gang kan
-- fullføre oppdraget. Statusmaskinen KOMPONERES, ikke utvides:
-- `plukket → opprettet → kansellert` i én transaksjon, to autoritative
-- revisjonshendelser for ett klikk.
-- ============================================================

-- ------------------------------------------------------------
-- 1. `oppdrag.kansellert_aarsak` — lukket, statusimplisert, immutabel
-- ------------------------------------------------------------
ALTER TABLE oppdrag ADD COLUMN IF NOT EXISTS kansellert_aarsak TEXT;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'oppdrag_kansellert_aarsak_gyldig') THEN
    ALTER TABLE oppdrag ADD CONSTRAINT oppdrag_kansellert_aarsak_gyldig
      CHECK (kansellert_aarsak IS NULL
             OR kansellert_aarsak IN ('menneskelig_avvis'));
  END IF;
  -- Konsolideringspresiseringen: årsak impliserer status, RELASJONELT —
  -- en årsak på en rad som ikke er kansellert er en løgn skjemaet selv
  -- skal nekte å bære.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'kansellert_aarsak_krever_status') THEN
    ALTER TABLE oppdrag ADD CONSTRAINT kansellert_aarsak_krever_status
      CHECK (kansellert_aarsak IS NULL OR status = 'kansellert');
  END IF;
END $$;

-- Immutabel når satt, og kan aldri fjernes — `avvis_endring` er samme
-- vakt som oppdragsbindingen på unntak bruker.
--
-- ... MEN IMMUTABILITET ALENE ER FOR SENT (Codex P2, runde 5).
--
-- Første utgave fyrte bare når OLD-verdien ALT var satt. Da var det bare
-- OMSKRIVING som var stengt — ikke ETTERSTEMPLING. En rad som lenge har
-- stått terminal `kansellert` med NULL årsak (en tidsavbrutt eller
-- systemkansellert jobb) kunne senere få `menneskelig_avvis` skrevet på
-- seg: kolonnelåsen (005) tillater eksplisitt `OLD.status = NEW.status`,
-- CHECKen over er fornøyd så lenge statusen ER `kansellert`, og
-- runtime-rollen har direkte UPDATE på `oppdrag`. En feilende eller
-- kompromittert same-tenant-spørring kunne dermed få en ordinær gammel
-- kansellering til å se ut som resultatet av et menneskelig nei — og
-- `kansellert_aarsak` er nettopp den raden revisjonen leser for å skille
-- de to (§5 utleder kompensasjons-/irreversibilitetssaken av den).
--
-- Årsaken er en påstand om en OVERGANG, ikke om en tilstand, og kan
-- derfor bare fødes i selve overgangen. Den ene lovlige veien inn i
-- `kansellert` er `opprettet -> kansellert` (kolonnelåsen, 005) — som er
-- nøyaktig den §7 komponerer (`plukket -> opprettet -> kansellert`).
-- Vakten slipper derfor bare gjennom en setting der OLD ikke er
-- kansellert og NEW er det; alt annet — omskriving, fjerning,
-- etterstempling på en alt terminal rad, eller en årsak uten
-- statusskifte — avvises.
DROP TRIGGER IF EXISTS oppdrag_kansellert_aarsak_immutable ON oppdrag;
CREATE TRIGGER oppdrag_kansellert_aarsak_immutable
  BEFORE UPDATE ON oppdrag
  FOR EACH ROW WHEN (NEW.kansellert_aarsak
                         IS DISTINCT FROM OLD.kansellert_aarsak
                     AND (OLD.kansellert_aarsak IS NOT NULL
                          OR OLD.status = 'kansellert'
                          OR NEW.status <> 'kansellert'))
  EXECUTE FUNCTION avvis_endring();

-- Og den samme påstanden kan heller ikke FØDES ferdig. Kolonnelåsen er
-- BEFORE UPDATE, og runtime har direkte INSERT på `oppdrag` (samme
-- utgangspunkt som bindingsvakten i 015): uten dette kunne en rad settes
-- inn ferdig `kansellert` med `menneskelig_avvis` på seg — et nei ingen
-- har sagt, uten en eneste overgang bak seg. Et oppdrag opprettes aldri
-- allerede kansellert.
CREATE OR REPLACE FUNCTION oppdrag_kansellert_aarsak_ved_insert()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'oppdrag: kansellert_aarsak kan ikke settes ved '
        'opprettelse (settes kun i overgangen til kansellert, 043)'
        USING ERRCODE = 'check_violation';
END $$;
DROP TRIGGER IF EXISTS oppdrag_kansellert_aarsak_insert ON oppdrag;
CREATE TRIGGER oppdrag_kansellert_aarsak_insert
  BEFORE INSERT ON oppdrag
  FOR EACH ROW WHEN (NEW.kansellert_aarsak IS NOT NULL)
  EXECUTE FUNCTION oppdrag_kansellert_aarsak_ved_insert();

-- ------------------------------------------------------------
-- 2. `unntak.arsak` utvides: kompensasjon og irreversibel utført
--    (038s partial UNIQUE (tenant, oppdrag_id, arsak) WHERE NOT terminal
--    dekker de nye verdiene uten endring.)
-- ------------------------------------------------------------
ALTER TABLE unntak DROP CONSTRAINT IF EXISTS unntak_arsak_check;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'unntak_arsak_gyldig') THEN
    ALTER TABLE unntak ADD CONSTRAINT unntak_arsak_gyldig
      CHECK (arsak IN ('evidensfrist', 'sikkerhet',
                       'kompensasjon_kreves', 'irreversibel_utfort'));
  END IF;
END $$;

-- ------------------------------------------------------------
-- 3. Kvitteringskapabiliteten får utfallet `avvist`
-- ------------------------------------------------------------
-- Tabellen er CLAIMER-EID (005) — kolonne-/constraint-ALTER må skje i
-- eierens vindu, ellers «must be owner» (målt på fersk rebuild).
SET LOCAL ROLE disponit_m37_claimer;
ALTER TABLE kvitteringskapabiliteter
  DROP CONSTRAINT IF EXISTS kvitteringskapabiliteter_status_check;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'kvitteringskapabilitet_status_gyldig') THEN
    ALTER TABLE kvitteringskapabiliteter
      ADD CONSTRAINT kvitteringskapabilitet_status_gyldig
      CHECK (status IN ('utstedt', 'brukt', 'feilet', 'avvist'));
  END IF;
END $$;
RESET ROLE;

-- Statusmaskinen: `avvist` er terminal, på linje med `feilet`.
-- EIERSKAPSNORMALISERING FØRST (lærdommen fra 041/#102): designeieren er
-- claimer, men en eldre base kan bære funksjonen migrator-eid —
-- reparasjonen kjører ETTER migrasjonene i oppsett. Guarded ALTER gjør
-- rollevinduet riktig i begge eierskapsverdener.
DO $$ BEGIN
  IF (SELECT pg_get_userbyid(proowner) FROM pg_proc
       WHERE oid = to_regprocedure(
         'public.kvitteringskapabilitet_statusmaskin()')) = current_user THEN
    EXECUTE 'ALTER FUNCTION public.kvitteringskapabilitet_statusmaskin()'
         || ' OWNER TO disponit_m37_claimer';
  END IF;
END $$;
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION kvitteringskapabilitet_statusmaskin()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.jti IS DISTINCT FROM OLD.jti
       OR NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.oppdrag_id IS DISTINCT FROM OLD.oppdrag_id
       OR NEW.modul_id IS DISTINCT FROM OLD.modul_id
       OR NEW.owner_claim_id IS DISTINCT FROM OLD.owner_claim_id
       OR NEW.owner_generation IS DISTINCT FROM OLD.owner_generation
       OR NEW.utloper IS DISTINCT FROM OLD.utloper
       OR NEW.utstedt IS DISTINCT FROM OLD.utstedt THEN
        RAISE EXCEPTION 'kvitteringskapabiliteter: bindingsfelter er uforanderlige';
    END IF;
    IF OLD.resultathash IS NOT NULL
       AND NEW.resultathash IS DISTINCT FROM OLD.resultathash THEN
        RAISE EXCEPTION 'kvitteringskapabiliteter: resultatet er uforanderlig';
    END IF;
    IF OLD.status = 'feilet' AND NEW.status <> 'feilet' THEN
        RAISE EXCEPTION 'kvitteringskapabiliteter: feilet er terminal';
    END IF;
    -- 043: menneskets nei er like terminalt som modulens feil. En brent
    -- `avvist` som kunne flippes tilbake ville gjenåpnet nøyaktig den
    -- fullføringsveien fencingen finnes for å bevise død.
    IF OLD.status = 'avvist' AND NEW.status <> 'avvist' THEN
        RAISE EXCEPTION 'kvitteringskapabiliteter: avvist er terminal';
    END IF;
    IF OLD.status = 'brukt' AND NEW.status <> 'brukt' THEN
        RAISE EXCEPTION 'kvitteringskapabiliteter: brukt er terminal';
    END IF;
    RETURN NEW;
END $$;
RESET ROLE;

-- Treargs-utgaven: oppløsningen brenner kapabiliteten med `avvist` —
-- SAMME atomiske kappløpssemantikk som kvitteringsveien, for det ER
-- samme kappløp: hvem som brenner først, vinner. Toargs-utgaven står
-- uendret (alle eksisterende kallere er kvitteringer).
--
-- Det tredje utfallet, `sen_evidens`, er Codex' P1: en signert kvittering
-- som kommer ETTER at nei-et brant kapabiliteten. Første utgave antok at
-- «modulens retry ender i sen-evidens-stien via generasjonsgjerdet» — men
-- retryen bærer SAMME jti, treffer den samme avviste kapabiliteten, og
-- toargsformen svarer `ugyldig` for evig. `_forbruk_kapabilitet` rullet da
-- tilbake med `kapabilitet_ugyldig` før sen-evidensgrenen i det hele tatt
-- ble nådd: `sen_kvittering` ble aldri skrevet, og kompensasjons-/
-- irreversibilitetssaken §5 lover ble aldri født. Fencingen skal hindre
-- FULLFØRING, ikke gjøre systemet blindt for det som allerede skjedde.
--
-- `sen_evidens` fester derfor resultathashen på den avviste kapabiliteten
-- uten å røre statusen: `avvist` forblir terminal, oppdraget forblir
-- kansellert, og hashen er det sen-evidensveien trenger for at reglene
-- «identisk kvittering => idempotent» og «to hasher => sikkerhetssak` skal
-- gjelde HER OGSÅ. Uten den kunne samme jti postet ubegrenset mange
-- motstridende sene kvitteringer — nøyaktig funnet forrige runde lukket
-- for stale-generation-veien.
DO $$ BEGIN
  IF to_regprocedure('public.bruk_kvitteringskapabilitet(text,text)')
       IS NOT NULL
     AND (SELECT pg_get_userbyid(proowner) FROM pg_proc
           WHERE oid = to_regprocedure(
             'public.bruk_kvitteringskapabilitet(text,text)')) = current_user
  THEN
    EXECUTE 'ALTER FUNCTION public.bruk_kvitteringskapabilitet(text,text)'
         || ' OWNER TO disponit_m37_claimer';
  END IF;
END $$;
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION bruk_kvitteringskapabilitet(
    p_jti TEXT, p_resultathash TEXT, p_utfall TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_treff  INT;
    v_status TEXT;
    v_hash   TEXT;
BEGIN
    IF p_utfall NOT IN ('brukt', 'avvist', 'sen_evidens') THEN
        RAISE EXCEPTION 'bruk_kvitteringskapabilitet: ukjent utfall %',
            p_utfall USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_utfall <> 'avvist' AND p_resultathash IS NULL THEN
        RAISE EXCEPTION 'bruk_kvitteringskapabilitet: % krever hash',
            p_utfall USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Er kapabiliteten fortsatt LEVENDE, er dette det ordinære kappløpet, og
    -- `sen_evidens` er nøyaktig en kvittering: den brenner som `brukt`.
    -- Skillet oppstår først når nei-et alt har brent den (under).
    UPDATE public.kvitteringskapabiliteter k
       SET status = CASE WHEN p_utfall = 'avvist' THEN 'avvist'
                         ELSE 'brukt' END,
           -- `avvist` bærer INGEN hash fra selve nei-et: det finnes intet
           -- resultat å attestere — det er hele poenget.
           resultathash = CASE WHEN p_utfall = 'avvist' THEN NULL
                               ELSE p_resultathash END,
           brukt_ts = pg_catalog.now()
     WHERE k.jti = p_jti
       AND k.status = 'utstedt'
       AND k.utloper > pg_catalog.now();
    GET DIAGNOSTICS v_treff = ROW_COUNT;
    IF v_treff = 1 THEN
        RETURN CASE WHEN p_utfall = 'avvist' THEN 'avvist' ELSE 'brukt' END;
    END IF;

    -- Kappløpet tapt, eller kapabiliteten var alt terminal/utløpt.
    SELECT k.status, k.resultathash INTO v_status, v_hash
      FROM public.kvitteringskapabiliteter k
     WHERE k.jti = p_jti;
    IF NOT FOUND THEN
        RETURN 'ugyldig';
    END IF;
    IF v_status = 'brukt' THEN
        IF p_utfall = 'avvist' THEN
            -- Kvitteringen vant: kan systemet bevise at handlingen ble
            -- utført, skal det ikke skrive en terminal «avvist» som om
            -- nei-et rakk fram. Taperen får sannheten, ikke stillhet.
            RETURN 'konflikt';
        END IF;
        IF v_hash IS NOT DISTINCT FROM p_resultathash THEN
            RETURN 'idempotent';
        END IF;
        RETURN 'konflikt';
    END IF;
    IF v_status = 'avvist' THEN
        -- To samtidige avvis: én oppløsning, resten idempotente (port 5).
        IF p_utfall = 'avvist' THEN
            RETURN 'idempotent';
        END IF;
        -- En KVITTERING som treffer en avvist kapabilitet er fra et fencet
        -- claim. Den skal aldri FULLFØRE noe — men den er evidens for at
        -- modulen rakk å utføre før nei-et nådde den, og den veien må
        -- finnes (Codex P1). Hashen festes uten å røre statusen; første
        -- sene kvittering vinner, og fra da av gjelder de vanlige reglene.
        IF p_utfall = 'sen_evidens' THEN
            UPDATE public.kvitteringskapabiliteter k
               SET resultathash = p_resultathash
             WHERE k.jti = p_jti AND k.status = 'avvist'
               AND k.resultathash IS NULL;
            GET DIAGNOSTICS v_treff = ROW_COUNT;
            IF v_treff = 1 THEN
                RETURN 'sen_evidens';
            END IF;
            -- Kappløpet mellom to sene kvitteringer avgjøres her, atomisk,
            -- av samme grunn som det ordinære: taperen blokkerte på
            -- radlåsen og leser vinnerens committede hash.
            SELECT k.resultathash INTO v_hash
              FROM public.kvitteringskapabiliteter k
             WHERE k.jti = p_jti;
            IF v_hash IS NOT DISTINCT FROM p_resultathash THEN
                RETURN 'idempotent';
            END IF;
            RETURN 'konflikt';
        END IF;
        -- Toargsformens semantikk (`brukt`): fail-closed.
    END IF;
    RETURN 'ugyldig';
END $$;
REVOKE ALL ON FUNCTION bruk_kvitteringskapabilitet(TEXT, TEXT, TEXT)
  FROM PUBLIC;
-- Granten gis AV EIEREN, i vinduet — migrator har ingen grant-option her.
--
-- ... men rollenavnet `disponit` er LOKALT/TEST-navnet (Codex P1).
-- `deploy/staging/migrer.py` tar runtime-rollens navn som argument, og på en
-- installasjon med et annet navn er en literal grant her i beste fall
-- virkningsløs og i verste fall en hard feil på en rolle som ikke finnes.
-- Den AUTORITATIVE granten for den konfigurerte rollen er derfor den
-- parameteriserte `M37_RETTIGHETER_API`-blokken i kjøreren; denne står
-- betinget, av samme grunn og med samme form som 038 brukte for
-- arbeider-/timerrollene.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
    GRANT EXECUTE ON FUNCTION bruk_kvitteringskapabilitet(TEXT, TEXT, TEXT)
      TO disponit;
  END IF;
END $$;
RESET ROLE;

-- ------------------------------------------------------------
-- 4. `sak_utestaaende` dekker BEGGE opphav (klarsignal §2)
--    038 gjorde `unntak_id` til OPPHAV, ikke generell sakstilknytning:
--    et beslutningsoppdrag peker den andre veien (`unntak.oppdrag_id`).
--    14a så bare reparasjonsveien — et levende beslutningsoppdrag kunne
--    avvises rett forbi vakten.
-- ------------------------------------------------------------
DO $$ BEGIN
  IF (SELECT pg_get_userbyid(proowner) FROM pg_proc
       WHERE oid = to_regprocedure('public.sak_utestaaende(text,bigint)'))
     = current_user THEN
    EXECUTE 'ALTER FUNCTION public.sak_utestaaende(text,bigint)'
         || ' OWNER TO disponit_m37_claimer';
  END IF;
END $$;
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION sak_utestaaende(p_tenant TEXT, p_unntak_id BIGINT)
RETURNS TABLE(kilde TEXT, ref TEXT, status TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    -- TENANTPORTEN, OGSÅ HER (Codex P2, runde 7).
    --
    -- Funksjonen er SECURITY DEFINER eid av claimer-rollen og gitt DIREKTE
    -- til runtime (011), men bandt aldri `p_tenant` til konteksten. Den var
    -- allerede et smalt orakel; den nye reverse-grenen under gjorde den
    -- bredere: med en annen tenant og en gjettet saks-id kunne en
    -- kompromittert runtime-spørring lese ut OM den tenantens beslutningssak
    -- har et oppdrag — inkludert oppdrags-id og status — med eierrollens
    -- RLS-forbigåelse. Porten er den samme de andre 043-definer-veiene
    -- bruker, og den står FØR enhver lesning. Funksjonen er derfor
    -- plpgsql nå: en `LANGUAGE sql`-kropp har ingen plass å sette den.
    --
    -- Alle tre kallstedene (`unntaksbehandling` §14a-vakten og
    -- oppløsningsveien, `lesing` sin avvis-knapp) står bak `sett_kontekst`
    -- med nøyaktig den tenanten de sender inn.
    PERFORM public.krev_tenantkontekst(p_tenant, 'sak_utestaaende');
    RETURN QUERY
    SELECT DISTINCT * FROM (
        SELECT 'oppdrag'::text, o.id::text, o.status
          FROM public.oppdrag o
         WHERE o.tenant = p_tenant AND o.unntak_id = p_unntak_id
           AND o.status <> 'kansellert'
        UNION ALL
        -- Beslutningsopphavet: saken peker på oppdraget, ikke omvendt.
        SELECT 'oppdrag'::text, o.id::text, o.status
          FROM public.oppdrag o
          JOIN public.unntak u ON u.tenant = o.tenant AND u.oppdrag_id = o.id
         WHERE u.tenant = p_tenant AND u.id = p_unntak_id
           AND o.status <> 'kansellert'
        UNION ALL
        SELECT 'kapabilitet'::text, k.jti, k.status
          FROM public.arbeidskapabiliteter k
         WHERE k.tenant = p_tenant AND k.unntak_id = p_unntak_id
           AND k.status NOT IN ('brukt', 'feilet')
    ) s
    ORDER BY 1, 2;
END $$;
RESET ROLE;

-- ------------------------------------------------------------
-- 5. Historikk-hendelsen for fencinghoppet
-- ------------------------------------------------------------
DO $$
DECLARE def TEXT;
BEGIN
  IF NOT EXISTS (
      SELECT 1 FROM pg_constraint
       WHERE conname = 'unntak_historikk_hendelse_check'
         AND pg_get_constraintdef(oid) LIKE '%oppdrag_fencet%') THEN
    SELECT pg_get_constraintdef(oid) INTO def FROM pg_constraint
     WHERE conname = 'unntak_historikk_hendelse_check';
    IF def IS NULL THEN
      RAISE EXCEPTION '042: fant ikke hendelse-CHECKen på unntak_historikk';
    END IF;
    -- Splice: utvid arrayen med den nye hendelsen — samme grep som 041
    -- brukte for varselenumene: den GJELDENDE definisjonen er kilden,
    -- aldri en avskrift av den.
    def := replace(def, '''oppdrag_kansellert''::text',
                   '''oppdrag_kansellert''::text, ''oppdrag_fencet''::text');
    EXECUTE 'ALTER TABLE unntak_historikk DROP CONSTRAINT'
         || ' unntak_historikk_hendelse_check';
    EXECUTE 'ALTER TABLE unntak_historikk ADD CONSTRAINT'
         || ' unntak_historikk_hendelse_check ' || def;
  END IF;
END $$;

-- ------------------------------------------------------------
-- 6. Reversibiliteten for et oppdrag — lesejobb for sen-utført-veien
--    (§5): utledes av MODULKONTRAKTEN oppdraget ble claimet under,
--    aldri av gjetning. NULL når oppdraget aldri ble modulbundet — da
--    finnes heller ingen motor som kan ha utført noe.
-- ------------------------------------------------------------
-- Kontrakttabellen er migrator-eid (014 registrerer i eierens vindu kun
-- funksjonene): lesegranten gis rett frem, av migrator selv.
GRANT SELECT ON modulkontrakt TO disponit_m37_claimer;
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION reversibilitet_for_oppdrag(
    p_tenant TEXT, p_oppdrag_id BIGINT)
RETURNS TEXT LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rev TEXT;
BEGIN
    -- 038-porten, også her: funksjonen er SECURITY DEFINER og gitt DIREKTE
    -- til runtime, så `p_tenant` er kallerens frie valg. Uten bindingen
    -- kunne en kompromittert runtime lese ut hvilken reversibilitetsklasse
    -- en ANNEN tenants oppdrag kjørte under, bare ved å gjette id-er.
    PERFORM public.krev_tenantkontekst(p_tenant, 'reversibilitet_for_oppdrag');
    SELECT k.reversibilitet INTO v_rev
      FROM public.oppdrag o
      JOIN public.modulkontrakt k
        ON k.modul_id = o.modul_id
       AND k.kontraktversjon = o.kontraktversjon
       AND k.kontrakt_hash = o.kontrakt_hash
     WHERE o.tenant = p_tenant AND o.id = p_oppdrag_id;
    RETURN v_rev;
END $$;
REVOKE ALL ON FUNCTION reversibilitet_for_oppdrag(TEXT, BIGINT) FROM PUBLIC;
-- Betinget som over: den konfigurerte runtime-rollen får denne av
-- `M37_RETTIGHETER_API` i kjøreren, `disponit` er lokal-/testnavnet.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
    GRANT EXECUTE ON FUNCTION reversibilitet_for_oppdrag(TEXT, BIGINT)
      TO disponit;
  END IF;
END $$;
RESET ROLE;

-- ------------------------------------------------------------
-- 7. `avvis_med_opplosning` — nei-et og beviset i ÉN transaksjon
-- ------------------------------------------------------------
-- Claimer-eid: én skrivevei til oppdrag/kapabiliteter, som resten av
-- M-37-flaten.
--
-- LÅSEORDEN I HELE KAPPLØPET — tre rader, ÉN rekkefølge:
--     sak (`unntak`)  →  kvitteringskapabilitet  →  oppdrag
-- Kalleren (avvis-veien i unntaksbehandlingen) holder alt SAKS-låsen når
-- den kommer hit; her tas først KAPABILITETSLÅSEN (pre-passet i kroppen),
-- så oppdragslåsen. Motparten — kvitteringsingesten — tar nå de samme tre
-- radene i samme rekkefølge: den låser saken FØR `_forbruk_kapabilitet`
-- (`app.py`, steg 3c), brenner så kapabiliteten og skifter oppdragets
-- status til slutt.
--
-- Begge halvdelene er nødvendige, og hver av dem var et eget Codex-funn:
-- retter man bare den indre (kapabilitet før oppdrag), står den ytre
-- sakslåsen igjen som en fullgod vranglås — avvis-veien holder saken og
-- venter på kapabiliteten mens kvitteringen holder kapabiliteten og venter
-- på saken. Utfallet skal avgjøres av hvem som brenner kapabiliteten
-- først (`oppdrag_utfort` eller gjennomført kansellering), aldri av
-- deadlock-detektoren.
--
-- KONTRAKT: `p_forventet` er oppdragene kalleren så som levende under
-- sakslåsen. Funksjonen re-evaluerer under oppdragslåsen:
--   * fortsatt levende → kapabiliteten brennes `avvist`, claimet fences
--     (`plukket → opprettet`, owner_generation++, eierbindingen fjernes),
--     og oppdraget kanselleres med `menneskelig_avvis`. To hendelser i
--     historikken: fencingen ER en autoritativ tilstandsendring, ikke støy.
--   * rukket å bli `utfort` (kvitteringen vant kappløpet) → utfallet
--     `oppdrag_utfort` med kvitteringsreferansen. Kalleren ruller HELE
--     transaksjonen tilbake: kan systemet bevise at handlingen ble
--     utført, skal det ikke skrive en terminal «avvist» som om nei-et
--     rakk fram — mennesket beslutter på nytt med fakta.
--   * rukket å bli `feilet`/`kansellert` → ingenting å løse opp; føres
--     med status ved avvis, som evidens for hva mennesket visste.
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION avvis_med_opplosning(
    p_tenant TEXT, p_unntak_id BIGINT, p_forventet BIGINT[],
    p_aktor TEXT, p_request_id TEXT)
RETURNS TABLE(utfall TEXT, oppdrag_id BIGINT,
              oppdrag_status_ved_avvis TEXT, kvitteringsref TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    r RECORD; v_jti TEXT; v_brenning TEXT; v_status TEXT; v_hash TEXT;
    v_fremmede BIGINT[];
BEGIN
    -- TENANTPORTEN FØRST (Codex P1). Funksjonen er SECURITY DEFINER, eid av
    -- claimer-rollen, og gitt DIREKTE til runtime — akkurat som 038-veiene.
    -- Da er `p_tenant` kallerens frie valg med mindre den bindes: en
    -- kompromittert runtime kunne ellers oppgi en annen tenant sammen med
    -- gjettede oppdrag-id-er og kansellere DEN tenantens levende oppdrag
    -- med eierrollens rettigheter. Porten er den samme alle andre
    -- runtime-kallbare definer-funksjoner bruker, og den står FØR enhver
    -- lesning eller lås: en avvist kaller skal ikke engang ha rørt en rad.
    PERFORM public.krev_tenantkontekst(p_tenant, 'avvis_med_opplosning');
    IF p_forventet IS NULL OR array_length(p_forventet, 1) IS NULL THEN
        RAISE EXCEPTION 'avvis_med_opplosning: ingen oppdrag å løse opp'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- ... OG MÅLENE MÅ HØRE TIL SAKEN (Codex P1, runde 4).
    --
    -- Tenantporten over binder HVEM kalleren er, ikke HVA den peker på.
    -- `p_forventet` var bare filtrert på tenant: en kompromittert
    -- runtime-spørring kunne oppgi en hvilken som helst av sine egne saker
    -- sammen med id-ene til helt urelaterte oppdrag i samme tenant, og få
    -- DEM fencet og kansellert med claimer-eierens rettigheter — mens
    -- `oppdrag_fencet`/`oppdrag_kansellert` ble ført på saken angriperen
    -- valgte. Skaden er dobbel: levende arbeid dør uten et menneskelig nei
    -- bak seg, og revisjonssporet forteller at en annen sak avgjorde det.
    --
    -- Autoriteten ligger i SAKSTILKNYTNINGEN, og den har to former — de
    -- samme to `sak_utestaaende` (§4) bruker for å FINNE oppdragene:
    -- reparasjonsopphavet (`oppdrag.unntak_id`) og beslutningsopphavet
    -- (`unntak.oppdrag_id`, som peker den andre veien). Den lovlige
    -- kalleren henter `p_forventet` derfra og kan derfor aldri utløse
    -- dette; en kaller som kan, sier per definisjon noe den ikke har
    -- dekning for.
    --
    -- Porten står FØR pre-låsingen og løkka: et fremmed oppdrag skal ikke
    -- engang bli låst. Utfallet er en HARD feil, ikke stille frafall —
    -- kalleren har oppgitt en oppløsningsmengde den ikke eier, og et
    -- delvis nei er ikke det mennesket sa nei til. Tilknytningen er
    -- uforanderlig, så det trengs ingen lås for å lese den.
    SELECT array_agg(f.id ORDER BY f.id) INTO v_fremmede
      FROM unnest(p_forventet) AS f(id)
     WHERE NOT EXISTS (
        SELECT 1 FROM public.oppdrag o
         WHERE o.tenant = p_tenant AND o.id = f.id
           AND (o.unntak_id = p_unntak_id
                OR EXISTS (SELECT 1 FROM public.unntak u
                            WHERE u.tenant = p_tenant
                              AND u.id = p_unntak_id
                              AND u.oppdrag_id = o.id)));
    IF v_fremmede IS NOT NULL THEN
        RAISE EXCEPTION
            'avvis_med_opplosning: oppdrag % hører ikke til sak %',
            v_fremmede, p_unntak_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- LÅSEORDEN: KAPABILITET FØR OPPDRAG (Codex P1).
    --
    -- Første utgave låste oppdraget først og kapabiliteten inne i sløyfa.
    -- Motparten i det kappløpet — kvitteringsveien — gjør det MOTSATT:
    -- `_forbruk_kapabilitet` brenner kapabiliteten (`app.py`), og oppdragets
    -- statusskifte skjer først etterpå, i samme transaksjon. To
    -- transaksjoner som tar de samme to radene i motsatt rekkefølge kan
    -- holde hver sin og vente på den andre: PostgreSQL avbryter én med en
    -- vranglås — altså en 40P01 i stedet for det avgjorte utfallet
    -- (`oppdrag_utfort` eller en gjennomført kansellering). Kappløpet SKAL
    -- avgjøres av hvem som brenner kapabiliteten først; da må kapabiliteten
    -- også være raden begge sider tar først.
    --
    -- Pre-passet låser derfor ALLE levende kvitteringskapabiliteter for de
    -- forventede oppdragene, i stigende (oppdrag, jti) — deterministisk
    -- også mot en annen samtidig oppløsning. Det er en overmengde av det
    -- sløyfa trenger (den plukker den nyeste per oppdrag), og med vilje:
    -- en lås som avhenger av statusen vi ennå ikke har lest, er ingen
    -- låseorden. Er det ingen kapabilitet, låses ingenting og oppdraget tas
    -- som før.
    PERFORM 1 FROM public.kvitteringskapabiliteter k
      WHERE k.tenant = p_tenant AND k.oppdrag_id = ANY(p_forventet)
        AND k.status = 'utstedt'
      ORDER BY k.oppdrag_id, k.jti
        FOR UPDATE;
    FOR r IN
        SELECT o.id, o.status FROM public.oppdrag o
         WHERE o.tenant = p_tenant AND o.id = ANY(p_forventet)
         ORDER BY o.id
           FOR UPDATE
    LOOP
        IF r.status = 'utfort' THEN
            -- Kvitteringen vant før vi rakk å låse.
            SELECT k.resultathash INTO v_hash
              FROM public.kvitteringskapabiliteter k
             WHERE k.tenant = p_tenant AND k.oppdrag_id = r.id
               AND k.status = 'brukt'
             ORDER BY k.brukt_ts DESC LIMIT 1;
            RETURN QUERY SELECT 'oppdrag_utfort'::text, r.id,
                                r.status, v_hash;
            CONTINUE;
        END IF;
        IF r.status NOT IN ('opprettet', 'plukket') THEN
            RETURN QUERY SELECT 'alt_terminal'::text, r.id, r.status,
                                NULL::text;
            CONTINUE;
        END IF;

        -- Kappløpet avgjøres av kapabiliteten — porten som alt finnes.
        SELECT k.jti INTO v_jti
          FROM public.kvitteringskapabiliteter k
         WHERE k.tenant = p_tenant AND k.oppdrag_id = r.id
           AND k.status = 'utstedt'
         ORDER BY k.utstedt DESC LIMIT 1
           FOR UPDATE;
        IF v_jti IS NOT NULL THEN
            v_brenning := public.bruk_kvitteringskapabilitet(
                v_jti, NULL, 'avvist');
            IF v_brenning = 'konflikt' THEN
                -- Kvitteringen brant først: oppdraget er (i ferd med å
                -- bli) utført. Referansen er vinnerens hash.
                SELECT k.resultathash INTO v_hash
                  FROM public.kvitteringskapabiliteter k
                 WHERE k.jti = v_jti;
                RETURN QUERY SELECT 'oppdrag_utfort'::text, r.id,
                                    r.status, v_hash;
                CONTINUE;
            END IF;
        END IF;

        IF r.status = 'plukket' THEN
            -- Hopp 1 — FENCINGEN: en reell hendelse med eget spor.
            -- Generasjonsbumpen er beviset på at gammel utførelses-
            -- autoritet er død; sen kvittering møter generasjonsgjerdet
            -- og ender som sen evidens, aldri som fullføring.
            UPDATE public.oppdrag o
               SET status = 'opprettet',
                   owner_claim_id = NULL,
                   owner_generation = o.owner_generation + 1,
                   owner_lease_utloper = NULL
             WHERE o.tenant = p_tenant AND o.id = r.id;
            INSERT INTO public.unntak_historikk (tenant, unntak_id,
                hendelse, aktor, request_id, detalj)
            VALUES (p_tenant, p_unntak_id, 'oppdrag_fencet', p_aktor,
                    p_request_id, jsonb_build_object(
                        'oppdrag_id', r.id,
                        'fra_status', 'plukket',
                        'kapabilitet_brent', v_jti IS NOT NULL));
        END IF;

        -- Hopp 2 — menneskets beslutning. Veien via `feilet` er avvist:
        -- et menneskelig nei er ikke en modulfeil, og revisjonssporet
        -- skal ikke lyve om det.
        UPDATE public.oppdrag o
           SET status = 'kansellert',
               kansellert_aarsak = 'menneskelig_avvis'
         WHERE o.tenant = p_tenant AND o.id = r.id;
        INSERT INTO public.unntak_historikk (tenant, unntak_id, hendelse,
            aktor, request_id, detalj)
        VALUES (p_tenant, p_unntak_id, 'oppdrag_kansellert', p_aktor,
                p_request_id, jsonb_build_object(
                    'oppdrag_id', r.id,
                    'kansellert_aarsak', 'menneskelig_avvis',
                    'oppdrag_status_ved_avvis', r.status));
        RETURN QUERY SELECT 'kansellert'::text, r.id, r.status, NULL::text;
    END LOOP;
END $$;
REVOKE ALL ON FUNCTION avvis_med_opplosning(TEXT, BIGINT, BIGINT[], TEXT,
    TEXT) FROM PUBLIC;
-- Kalles av avvis-veien i unntaksbehandlingen (runtime, scope-gatet
-- `exceptions:handle` i app-laget — samme scopeport som resten av veien).
-- Betinget som de to over: `M37_RETTIGHETER_API` i kjøreren er den
-- autoritative granten for den KONFIGURERTE runtime-rollen. Arbeideren står
-- bevisst utenfor begge — et menneskelig nei er ikke arbeiderens vei.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
    GRANT EXECUTE ON FUNCTION avvis_med_opplosning(TEXT, BIGINT, BIGINT[],
        TEXT, TEXT) TO disponit;
  END IF;
END $$;
RESET ROLE;

-- ------------------------------------------------------------
-- 8. `verifiser_artefaktbinding` — validering UTEN bevaring
-- ------------------------------------------------------------
-- Codex P2 (runde 2): den sene kvitteringsveien kalte `bevar_artefakt` FØR
-- den slo opp reversibiliteten. For et `direkte` oppdrag sier kontrakten —
-- og veiens egen kommentar — at resultatet forkastes og at artefaktet skal
-- forbli `staged` og ryddes av 038-reaperen; men `bevart` er RETAINED og
-- terminalt, så oppryddingen rører det aldri igjen. Artefaktet ble altså
-- liggende for alltid, nøyaktig motsatt av det veien lovte.
--
-- Bevaringen må derfor kunne UTELATES uten at valideringen faller bort:
-- kvitteringen skal fortsatt avvises som sikkerhetskonflikt om den navngir
-- et fremmed/ikke-eksisterende artefakt eller påstår feil hash. Denne
-- funksjonen er `bevar_artefakt` MINUS UPDATE-en — samme eier, samme
-- akseptmengde (raden finnes for (artefakt, tenant, oppdrag), signert hash
-- stemmer, tilstanden er `staged` eller alt `bevart`), samme `FOR UPDATE`
-- så avgjørelsen serialiseres mot `rydd_staged_artefakter` i vinduet rundt
-- evidensfristen. Runtime har kun SELECT på `artefakt` og kan ikke låse
-- raden selv; derfor bor porten her, som resten av artefaktveien.
-- Returnerer 'gyldig' | 'ugyldig'.
SET LOCAL ROLE disponit_domene_eier;
CREATE OR REPLACE FUNCTION verifiser_artefaktbinding(
    p_artefakt_id UUID, p_tenant TEXT, p_oppdrag_id BIGINT,
    p_klartekst_sha256 TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE r RECORD;
BEGIN
    -- TENANTPORTEN FØRST (Codex P2, runde 3) — se §7 for den samme
    -- begrunnelsen på avvis-veien. Eierrollen omgår artefakttabellens
    -- tenant-isolasjon, og funksjonen er gitt DIREKTE til runtime; uten
    -- porten er `p_tenant` kallerens frie valg. En kompromittert
    -- runtime-spørring kunne da oppgi en ANNEN tenants uuid, oppdrag-id og
    -- hash og lese svaret som et orakel: 'gyldig' betyr at artefaktet
    -- finnes og er staged/bevart hos den tenanten. `FOR UPDATE` gjør det
    -- verre enn en lekkasje — den tar en kryss-tenant radlås som holdes til
    -- kallerens commit.
    --
    -- Porten står FØR spørringen, ikke etter: en avvist kaller skal ikke ha
    -- rørt raden, og slett ikke ha låst den.
    PERFORM public.krev_tenantkontekst(p_tenant, 'verifiser_artefaktbinding');
    SELECT klartekst_sha256, tilstand INTO r FROM public.artefakt
     WHERE artefakt_id = p_artefakt_id AND tenant = p_tenant
       AND oppdrag_id = p_oppdrag_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'ugyldig'; END IF;
    IF r.klartekst_sha256 IS DISTINCT FROM p_klartekst_sha256 THEN
        RETURN 'ugyldig';
    END IF;
    IF r.tilstand NOT IN ('staged', 'bevart') THEN RETURN 'ugyldig'; END IF;
    RETURN 'gyldig';
END $$;
REVOKE ALL ON FUNCTION verifiser_artefaktbinding(UUID, TEXT, BIGINT, TEXT)
    FROM PUBLIC;
-- Samme form som de tre over: den KONFIGURERTE runtime-rollen får denne av
-- kjøreren (`RETTIGHETER`, i domene_eier-blokken sammen med de andre
-- artefaktfunksjonene den hører hjemme ved), `disponit` er lokal-/testnavnet
-- og betinget av at rollen finnes.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
    GRANT EXECUTE ON FUNCTION verifiser_artefaktbinding(UUID, TEXT, BIGINT,
        TEXT) TO disponit;
  END IF;
END $$;

-- ... og TVILLINGEN må ha den samme porten, ellers er den bare flyttet.
-- `bevar_artefakt` (016) er den ANDRE halvdelen av det samme valget på det
-- samme kallstedet (`app.py`: `bevar` → bevar, ellers verifiser), med
-- identisk signatur, identisk eier og identisk `FOR UPDATE`. Gav vi porten
-- til den ene og ikke den andre, ville kryss-tenant-orakelet og
-- kryss-tenant-låsen Codex fant fortsatt ligge åpne — og til og med på den
-- MEST brukte grenen. Da er det ikke roten som er rettet, bare den ene
-- veien dit.
--
-- Kroppen er ellers uendret fra 016 (validering, lås, `bevart`/`idempotent`/
-- `ugyldig`); CREATE OR REPLACE beholder eier og eksisterende grants.
-- Runtime setter alltid tenantkonteksten før kvitteringsingesten kaller
-- denne, så porten er ingen ny betingelse for den legitime veien.
CREATE OR REPLACE FUNCTION bevar_artefakt(
    p_artefakt_id UUID, p_tenant TEXT, p_oppdrag_id BIGINT,
    p_klartekst_sha256 TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE r RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'bevar_artefakt');
    SELECT klartekst_sha256, tilstand INTO r FROM public.artefakt
     WHERE artefakt_id = p_artefakt_id AND tenant = p_tenant
       AND oppdrag_id = p_oppdrag_id FOR UPDATE;
    IF NOT FOUND THEN RETURN 'ugyldig'; END IF;
    IF r.klartekst_sha256 IS DISTINCT FROM p_klartekst_sha256 THEN
        RETURN 'ugyldig';
    END IF;
    IF r.tilstand = 'bevart' THEN RETURN 'idempotent'; END IF;
    IF r.tilstand <> 'staged' THEN RETURN 'ugyldig'; END IF;   -- forkastet/…
    UPDATE public.artefakt SET tilstand = 'bevart'
     WHERE artefakt_id = p_artefakt_id;
    RETURN 'bevart';
END $$;
RESET ROLE;
