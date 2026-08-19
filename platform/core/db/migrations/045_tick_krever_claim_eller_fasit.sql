-- ============================================================
-- 045 — et tick krever et LEVENDE claim eller en VERIFISERT fasit
--       (Codex P1 på #105, levert tre minutter etter mergen)
--
-- FUNNET. `terminaliser_planvindu` er den eneste tick-skriveren, og
-- fencingen der sto som en ELSIF-gren:
--
--     IF p_utfall = 'hoppet_over' THEN
--         ...                                     -- egne porter
--     ELSIF v.tilstand = 'aktivt' AND v.claim_id IS DISTINCT FROM p_claim
--     THEN
--         ...                                     -- claim eller fasit
--     END IF;
--
-- Grenen måler `v.tilstand = 'aktivt'`. Er vinduet `ledig`, er hele
-- betingelsen usann, ingen gren kjører, og funksjonen går rett på
-- UPDATE + INSERT: et hvilket som helst ikke-`hoppet_over`-utfall
-- godtas uten verken claim ELLER idempotensrad. Kode som kjører som den
-- innvilgede `disponit`-rollen — én SQL-injeksjon, én kompromittert
-- runtime-forbindelse — kan da, i sin EGEN og fullt lovlige
-- tenantkontekst, kalle hit på et av tenantens egne åpne vinduer med
-- `p_utfall='tillat'` og skrive et tick som aldri passerte policy,
-- kvote eller bestillingsvei. Vinduet er konsumert, evidensen sier at
-- kontrollen ble kjørt, og ingen kontroll ble kjørt.
--
-- HVORFOR DE FOREGÅENDE GJERDENE IKKE FANGER DEN. Dette er den tredje
-- porten inn til samme rom, og de to første forklarer hvorfor denne ble
-- stående:
--
--   * `krev_tenantkontekst` (044, Codex P1 runde 2) beviser hvem
--     KALLEREN er. Angrepet her lyver ikke om det — det står i sin egen
--     tenant hele veien.
--   * `w.tenant = p_tenant` + den sammensatte FK-kjeden (044, Codex P1
--     runde 3) beviser at RADEN hører til kalleren. Den gjør det også
--     her: vinduet ER tenantens eget. Det er nettopp derfor gjerdet
--     slipper det gjennom.
--
-- Begge måler EIERSKAP. Ingen av dem måler AUTORITET: at akkurat dette
-- forsøket har rett til å felle akkurat dette vinduet. Autoriteten er
-- claimet, og claimet ble bare krevd av vinduer som alt var claimet.
--
-- ROTEN. Betingelsen var skrevet som «når er claimet FEIL?» — og den
-- listen kan aldri bli komplett, for den må gjette hvilke tilstander en
-- fremtidig vei kan komme fra. Den snus her til «når er utfallet
-- BEVIST?», som er en lukket liste med nøyaktig to ledd:
--
--   1. Kalleren HOLDER det levende claimet på et `aktivt` vindu
--      (materialiseringsveien: policy og kvote er alt passert), eller
--   2. utfallet er VERIFISERT mot den immutable `bestilling_idempotens`
--      på vinduets nøkkel, og ingen andre eier forsøket
--      (klassifiseringsveien: den skriver ned det bestillingsveien alt
--      har besluttet, og kan ikke gjette).
--
-- Alt annet avvises — også tilstander ingen har tenkt på ennå. Merk at
-- ledd 2 med vilje gjelder `ledig` vinduer også: et vindu som ble
-- frigitt av `frigi_planvindu` etter et driftsuhell KAN bære en
-- idempotensrad (bestillingen committet, svarveien røk), og fasiten er
-- like bindende der. Det er kravet om fasit som er porten, ikke
-- tilstanden.
--
-- `hoppet_over` beholder sine egne porter uendret: den skal jo nettopp
-- felles uten claim, og har utløpt vindu + INTET idempotenstreff som
-- sine krav.
-- ============================================================

-- Eierskapet er 044s: planfunksjonene er CLAIMER-eide, og CREATE OR
-- REPLACE beholder eieren den har. Rollen settes likevel eksplisitt —
-- en erstatning som kjørte som migrator ville krevd medlemskap for å
-- lykkes i det hele tatt, og en fremtidig leser skal ikke måtte slå opp
-- i 044 for å vite hvem funksjonen kjører som.
SET LOCAL ROLE disponit_m37_claimer;

CREATE OR REPLACE FUNCTION terminaliser_planvindu(
    p_tenant TEXT, p_plan UUID, p_vindu TIMESTAMPTZ, p_claim UUID,
    p_nokkel TEXT, p_utfall TEXT, p_oppdrag BIGINT, p_detalj JSONB)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v RECORD; v_eksisterende RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'terminaliser_planvindu');
    -- VINDUET BINDES TIL p_tenant (Codex P1, 044 runde 3). Leddet står i
    -- hvert eneste oppslag og hver eneste UPDATE her; den sammensatte
    -- FK-en fra ticket til vinduet gjør avviket umulig å lagre selv om
    -- leddet skulle falle ut.
    SELECT * INTO v FROM public.bestillingsplan_vindu w
     WHERE w.plan_id = p_plan AND w.tenant = p_tenant
       AND w.vindu_start = p_vindu FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'terminaliser_planvindu: ukjent vindu'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v.tilstand = 'terminal' THEN
        -- Konflikt på tick er ikke suksess: eksisterende rad MÅ bære
        -- samme terminale utfall — avvik er en sikkerhetssak hos
        -- kalleren. Mutexen skal gjøre avviket umulig; kontrollen står
        -- fordi den beviser det.
        SELECT t.utfall INTO v_eksisterende
          FROM public.bestillingsplan_tick t
         WHERE t.plan_id = p_plan AND t.tenant = p_tenant
           AND t.vindu_start = p_vindu;
        IF v_eksisterende.utfall IS DISTINCT FROM p_utfall THEN
            -- Avviket fører SIN EGEN sikkerhetshendelse her, atomisk med
            -- oppdagelsen: kalleren har ingen tabellrettigheter (port 7),
            -- og en hendelse kalleren kunne glemme var ingen hendelse.
            INSERT INTO public.bestillingsplan_hendelse
                (plan_id, tenant, hendelse, aktor, request_id, detalj)
            VALUES (p_plan, p_tenant, 'sikkerhetsavvik',
                    'terminaliser_planvindu', NULL,
                    jsonb_build_object('ventet', p_utfall,
                        'fant', coalesce(v_eksisterende.utfall,
                                         '<uten tick>')));
            RETURN 'avvik:' || coalesce(v_eksisterende.utfall, '<uten tick>');
        END IF;
        RETURN 'idempotent';
    END IF;
    IF p_utfall = 'hoppet_over' THEN
        -- Uendret fra 044: `hoppet_over` er det ene utfallet som SKAL
        -- felles uten claim, og porten er utløpt vindu + intet
        -- idempotenstreff (§5).
        IF now() < v.vindu_slutt THEN
            RAISE EXCEPTION 'terminaliser_planvindu: hoppet_over før '
                'vindu_slutt' USING ERRCODE = 'invalid_parameter_value';
        END IF;
        IF v.tilstand = 'aktivt' AND v.lease_utloper > now() THEN
            RAISE EXCEPTION 'terminaliser_planvindu: et levende forsøk '
                'eier vinduet' USING ERRCODE = 'invalid_parameter_value';
        END IF;
        IF EXISTS (SELECT 1 FROM public.bestilling_idempotens bi
                    WHERE bi.tenant = p_tenant
                      AND bi.idempotensnokkel = p_nokkel) THEN
            RAISE EXCEPTION 'terminaliser_planvindu: det finnes en '
                'bestilling på vinduets nøkkel — utfallet skal hentes '
                'derfra, aldri hoppet_over'
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
    -- ALLE ANDRE UTFALL KREVER BEVIS (Codex P1 på #105). Betingelsen
    -- spør ikke lenger «er claimet feil?» — den spør «holder kalleren
    -- det LEVENDE claimet?», og alt som ikke gjør det må gjennom
    -- fasitporten under. `ledig`-vinduet, som falt utenfor den gamle
    -- ELSIF-en i sin helhet, er dermed dekket av nøyaktig samme krav
    -- som alle andre.
    ELSIF NOT (v.tilstand = 'aktivt' AND p_claim IS NOT NULL
               AND v.claim_id = p_claim) THEN
        -- ÉN VEI TILBAKE (Codex P1, 044 runde 2): committer arbeideren
        -- bestillingen og dør før terminaliseringen, står vinduet
        -- `aktivt` med et claim ingen lenger holder. Vinduet er da alt
        -- utløpt når leasen dør, så plukket tar det aldri igjen
        -- (`now() < vindu_slutt`), og klassifisereren — som per §5 IKKE
        -- eier noe claim — ble avvist av fencingen. Resultatet var en
        -- evig `ventet`: et vindu som ER bestilt, men som aldri fikk sitt
        -- tick, i hvert sveip for alltid.
        --
        -- Gjenopprettingen er smal med vilje, og hviler på den ENE
        -- fasiten §5 alt utpeker: `bestilling_idempotens`. Tre ledd må
        -- holde — kalleren har INTET claim (en arbeider med feil claim er
        -- fortsatt fenced), intet LEVENDE forsøk eier vinduet (port
        -- 44/51), og det påståtte utfallet er VERIFISERT mot den
        -- immutable raden på vinduets nøkkel. Klassifisereren kan altså
        -- ikke gjette et utfall — den kan bare skrive ned det
        -- bestillingsveien alt har besluttet.
        --
        -- Livshetssjekken er eksplisitt bundet til `aktivt`. Et `ledig`
        -- vindu har `lease_utloper IS NULL`, og `NULL > now()` er NULL:
        -- den gamle formen ville latt hele OR-kjeden bli NULL og dermed
        -- gjort porten avhengig av trekantlogikk. Ingen port skal hvile
        -- på at en ukjent verdi tilfeldigvis ikke er sann.
        IF p_claim IS NOT NULL
           OR (v.tilstand = 'aktivt' AND v.lease_utloper > now())
           OR NOT EXISTS (SELECT 1 FROM public.bestilling_idempotens bi
                           WHERE bi.tenant = p_tenant
                             AND bi.idempotensnokkel = p_nokkel
                             AND bi.beslutning = p_utfall) THEN
            RAISE EXCEPTION 'terminaliser_planvindu: utfallet krever et '
                'levende claim eller en verifisert idempotensrad'
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
    END IF;
    UPDATE public.bestillingsplan_vindu w
       SET tilstand = 'terminal', terminalisert_ts = now()
     WHERE w.plan_id = p_plan AND w.tenant = p_tenant
       AND w.vindu_start = p_vindu;
    INSERT INTO public.bestillingsplan_tick
        (plan_id, tenant, vindu_start, idempotensnokkel, utfall,
         oppdrag_id, detalj)
    VALUES (p_plan, p_tenant, p_vindu, p_nokkel, p_utfall, p_oppdrag,
            p_detalj);
    RETURN 'terminalisert';
END $$;

-- CREATE OR REPLACE beholder rettighetene fra 044; de gjentas her fordi
-- en migrasjon skal kunne leses alene.
REVOKE ALL ON FUNCTION terminaliser_planvindu(TEXT, UUID, TIMESTAMPTZ,
    UUID, TEXT, TEXT, BIGINT, JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION terminaliser_planvindu(TEXT, UUID, TIMESTAMPTZ,
    UUID, TEXT, TEXT, BIGINT, JSONB) TO disponit;

RESET ROLE;
