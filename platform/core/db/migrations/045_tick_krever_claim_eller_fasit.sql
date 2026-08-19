-- ============================================================
-- 045 — et tick krever BEVIS, og tick-skriveren deler planlåsen
--       (Codex P1 + P2 på #105, levert tre minutter etter mergen)
--
-- Begge funnene bor i `terminaliser_planvindu`, og begge har samme form:
-- funksjonen er den eneste tick-SKRIVEREN, men den deltok verken i
-- autoritetskravet den håndhever for andre veier eller i låsen resten av
-- planfamilien bygger sine beslutninger på.
--
-- ------------------------------------------------------------
-- FUNN 1 (P1) — et tick krever et LEVENDE claim eller en VERIFISERT fasit
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
--
-- ------------------------------------------------------------
-- FUNN 2 (P2) — tick-innsettingen deler planlåsen med pausebeslutningen
--
-- Runde 4 flyttet kappløpet mellom artefaktpromotering og pausen inn
-- under en lås på FAKTUMET. Det gjenstående kappløpet er et annet:
-- `pause_gjentatt_uten_resultat` og `varsle_plan_brudd` låser planraden
-- FOR UPDATE, leser DERETTER «de tre siste tickene», låser deres
-- oppdragsnøkler og avgjør — men denne funksjonen, som er den ENESTE
-- som kan endre hvilke tick det er, tok aldri den låsen.
--
-- Et fjerde, ferskere `brudd`-tick som committer etter utvalget, men før
-- pausen, bryter altså stripen uten at sveipen ser det: planen pauses
-- som `gjentatt_uten_resultat` på en strek som ikke lenger finnes, og
-- den pausen kan bare et menneske oppheve. Speilvendt var et nytt
-- `tillat`-ticks oppdrag ikke med i låsesettet, så artefaktlåsen fra
-- runde 4 dekket det ikke.
--
-- Roten er at låsen beskyttet BESLUTNINGEN, men ikke EVIDENSEN
-- beslutningen leser. Rettingen er å la skriveren delta i den samme
-- låsen; predikatene er uendret. Begrunnelsen for FOR UPDATE framfor
-- FOR SHARE, og for at låserekkefølgen vindu → plan står urørt, ligger
-- ved selve låsen i funksjonen under.
-- ============================================================

-- Eierskapet er 044s: planfunksjonene er CLAIMER-eide, og CREATE OR
-- REPLACE beholder eieren den har. Rollen settes likevel eksplisitt —
-- en erstatning som kjørte som migrator ville krevd medlemskap for å
-- lykkes i det hele tatt, og en fremtidig leser skal ikke måtte slå opp
-- i 044 for å vite hvem funksjonen kjører som.
SET LOCAL ROLE disponit_m37_claimer;

-- ------------------------------------------------------------
-- NØKKELEN ER EN FUNKSJON AV VINDUET, IKKE ET ARGUMENT
-- (Codex P1 på #106)
--
-- Fasitporten over spør «finnes det en idempotensrad på nøkkelen med
-- dette utfallet?» — og `p_nokkel` var til nå et fritt kallerargument.
-- Beviset var dermed bundet til en STRENG kalleren valgte, ikke til
-- vinduet som skulle felles: enhver eksisterende `bestilling_idempotens`
-- -rad i kallerens egen tenant, med `beslutning` lik det påståtte
-- utfallet, kunne pekes på for å terminalisere et helt annet åpent
-- vindu. Det er nøyaktig det forfalskede ticket 045 er skrevet for å
-- stenge, bare med ett hopp til.
--
-- `hoppet_over` bar speilbildet: der er porten at det IKKE finnes en rad
-- på nøkkelen, og en oppdiktet nøkkel treffer garantert ingenting. Et
-- vindu som VAR bestilt kunne dermed felles som `hoppet_over` likevel.
--
-- Roten er at nøkkelen ble BÅRET inn i stedet for UTLEDET. Den er en ren
-- funksjon av (plan, vindu) — det er nettopp derfor både materialisereren
-- og klassifisereren kan regne den ut hver for seg — så den utledes her,
-- i den ene grensen som ikke kan lyve om den. Formen er den samme som
-- `plan.materialiser.idempotensnokkel`: `plan:<plan_id>:<vindu_start i
-- UTC, ISO 8601>`, der `to_char(...,'US')` gjengir Pythons `isoformat()`
-- som utelater brøkdelen når den er null.
CREATE OR REPLACE FUNCTION plan_vindu_idempotensnokkel(
    p_plan UUID, p_vindu TIMESTAMPTZ)
RETURNS TEXT LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
    SELECT 'plan:' || p_plan::text || ':'
        || to_char(p_vindu AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS')
        || CASE WHEN to_char(p_vindu AT TIME ZONE 'UTC', 'US') = '000000'
                THEN '' ELSE '.' || to_char(p_vindu AT TIME ZONE 'UTC', 'US')
           END
        || '+00:00'
$$;
REVOKE ALL ON FUNCTION plan_vindu_idempotensnokkel(UUID, TIMESTAMPTZ)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION plan_vindu_idempotensnokkel(UUID, TIMESTAMPTZ)
    TO disponit;

-- ------------------------------------------------------------
-- FASITEN MÅ HA ET OPPHAV, IKKE BARE ET INNHOLD
-- (Codex P1 på #106, runde 2)
--
-- Forrige runde bandt beviset til VINDUET: `p_nokkel` må være den
-- utledede nøkkelen for (plan, vindu). Det stengte gjenbruken av en
-- fremmed nøkkel, men ikke selve rommet nøkkelen peker inn i. Runtime-
-- rollen `disponit` har nemlig DIREKTE `INSERT` på `bestilling_idempotens`
-- (038 §6.1), og radens eneste øvrige port er RLS-policyen, som bare
-- krever at raden bærer kallerens egen tenant. Trusselmodellen 045 er
-- skrevet mot — én SQL-injeksjon, én kompromittert runtime-forbindelse i
-- sin EGEN lovlige tenantkontekst — kan altså regne ut vinduets kanoniske
-- nøkkel selv, skrive en rad med `beslutning='tillat'` på den, og i neste
-- setning kalle `terminaliser_planvindu` og få sitt forfalskede tick.
--
-- ROTEN. Fasitporten leser en rad og tror på INNHOLDET. Immutabilitet
-- etter innsetting (triggeren i 038 §3) beviser at raden ikke er ENDRET;
-- den sier ingenting om hvem som skrev den. Et bevis uten opphav er ikke
-- et bevis — det er et innhold kalleren selv kan velge.
--
-- RETTINGEN følger 038 §4 sin egen presedens for `oppdrag`: runtime
-- mister den direkte skriveveien, og får i stedet ÉN herdet funksjon som
-- setter det kalleren ikke skal få velge. Her er det opphavet som settes:
--
--   * Nøkkelrommet «plan:» er planmaskineriets. En bestilling som ikke
--     kommer fra et claimet vindu kan ikke skrive i det i det hele tatt.
--     Dette leddet er ikke pynt: `Idempotency-Key` er en HTTP-header
--     klienten velger fritt, så uten det kunne en helt vanlig,
--     autentisert bestilling sende `plan:<plan_id>:<vindu_start>` som sin
--     egen nøkkel og legge fasiten for et fremmed vindu gjennom
--     hoveddøra — uten injeksjon, uten kompromittert forbindelse.
--   * Kommer den fra et vindu, må kalleren HOLDE vinduets claim: nøkkelen
--     må være den utledede for (p_plan, p_vindu), og vindusraden må stå
--     `aktivt` med nøyaktig dette claimet. `claim_id` er en UUID basen
--     genererte og bare ga til vinneren av `claim_planvindu` — den kan
--     ikke gjettes, og den er dermed det opphavet raden manglet.
--
-- INVARIANTEN ETTERPÅ: fasitveien er ikke lenger SVAKERE enn claimveien.
-- Å skrive en `plan:`-fasit krever nå nøyaktig den samme autoriteten som
-- å terminalisere direkte med et levende claim — så gjenopprettingen i
-- `terminaliser_planvindu` gir ingen makt claimveien ikke alt ga. Den er
-- det den skulle være: en vei tilbake for et forsøk som ALT hadde
-- autoriteten, og som mistet svaret sitt.
--
-- INGEN LÅS PÅ VINDUSRADEN HER. Oppslaget er en ren lesning med vilje.
-- Funksjonen tar ingen BESLUTNING som må serialiseres — den beviser et
-- OPPHAV — og eksklusjonen mot samtidige forsøk bæres av
-- `terminaliser_planvindu` som før. Et `FOR UPDATE` her ville lagt en ny
-- kant inn i låsegrafen (vindu tas midt i bestillingstransaksjonen, som
-- alt holder rader på logg/oppdrag/kvote) uten å kjøpe noe: skulle en ny
-- arbeider rekke å overta vinduet mens bestillingen pågår, er det gamle
-- forsøket utgjerdet, og at fasiten da avvises er nøyaktig riktig.
CREATE OR REPLACE FUNCTION registrer_bestilling_idempotens(
    p_tenant TEXT, p_nokkel TEXT, p_intensjonshash TEXT, p_oppdrag BIGINT,
    p_beslutning TEXT, p_svarkropp JSONB,
    p_plan UUID, p_vindu TIMESTAMPTZ, p_claim UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'registrer_bestilling_idempotens');
    IF p_plan IS NULL OR p_vindu IS NULL OR p_claim IS NULL THEN
        -- Ingen vindusopprinnelse oppgitt: nøkkelen er klientens egen, og
        -- da er planrommet stengt. Alle tre må være med — en delvis
        -- oppgitt opprinnelse er ingen opprinnelse, og skal ikke kunne
        -- forhandle seg forbi porten ved å utelate leddet som binder.
        IF p_nokkel LIKE 'plan:%' THEN
            RAISE EXCEPTION 'registrer_bestilling_idempotens: nøkkelrommet '
                '«plan:» tilhører planmaskineriet — en bestilling uten et '
                'claimet vindu kan ikke skrive i det'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    ELSE
        IF p_nokkel IS DISTINCT FROM
           public.plan_vindu_idempotensnokkel(p_plan, p_vindu) THEN
            RAISE EXCEPTION 'registrer_bestilling_idempotens: nøkkelen '
                'hører ikke til vinduet'
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        -- `w.tenant = p_tenant` av samme grunn som i `claim_planvindu` og
        -- `terminaliser_planvindu`: definer-veien ser forbi RLS, så raden
        -- må bindes til kalleren eksplisitt.
        SELECT w.tilstand, w.claim_id INTO v
          FROM public.bestillingsplan_vindu w
         WHERE w.plan_id = p_plan AND w.tenant = p_tenant
           AND w.vindu_start = p_vindu;
        IF NOT FOUND OR v.tilstand IS DISTINCT FROM 'aktivt'
           OR v.claim_id IS DISTINCT FROM p_claim THEN
            RAISE EXCEPTION 'registrer_bestilling_idempotens: fasiten '
                'krever at kalleren holder vinduets claim'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    -- `ON CONFLICT DO NOTHING` er bestillingsveiens egen semantikk, flyttet
    -- hit uendret: taperen av et kappløp på nøkkelen leser vinnerens rad,
    -- den overskriver den aldri (raden er uansett immutabel).
    INSERT INTO public.bestilling_idempotens
        (tenant, idempotensnokkel, intensjonshash, oppdrag_id, beslutning,
         svarkropp)
    VALUES (p_tenant, p_nokkel, p_intensjonshash, p_oppdrag, p_beslutning,
            coalesce(p_svarkropp, '{}'::jsonb))
    ON CONFLICT (tenant, idempotensnokkel) DO NOTHING;
END $$;
REVOKE ALL ON FUNCTION registrer_bestilling_idempotens(TEXT, TEXT, TEXT,
    BIGINT, TEXT, JSONB, UUID, TIMESTAMPTZ, UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION registrer_bestilling_idempotens(TEXT, TEXT, TEXT,
    BIGINT, TEXT, JSONB, UUID, TIMESTAMPTZ, UUID) TO disponit;

CREATE OR REPLACE FUNCTION terminaliser_planvindu(
    p_tenant TEXT, p_plan UUID, p_vindu TIMESTAMPTZ, p_claim UUID,
    p_nokkel TEXT, p_utfall TEXT, p_oppdrag BIGINT, p_detalj JSONB)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v RECORD; v_eksisterende RECORD; v_nokkel TEXT;
        v_fasit RECORD; v_oppdrag BIGINT := p_oppdrag;
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
    -- NØKKELEN BINDES TIL VINDUET (Codex P1 på #106). Kontrollen står
    -- ETTER eierskapsoppslaget med vilje: et kall på en annen tenants
    -- vindu skal fortsatt svare `ukjent vindu` og ikke røpe at nøkkelen
    -- var riktig avledet. Under dette punktet er `v_nokkel` bevist lik
    -- det kalleren oppga, og hver eneste port nedenfor — fasiten,
    -- `hoppet_over`-nekten og ticket selv — leser den utledede verdien.
    v_nokkel := public.plan_vindu_idempotensnokkel(p_plan, p_vindu);
    IF p_nokkel IS DISTINCT FROM v_nokkel THEN
        RAISE EXCEPTION 'terminaliser_planvindu: idempotensnøkkelen hører '
            'ikke til vinduet' USING ERRCODE = 'invalid_parameter_value';
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
                      AND bi.idempotensnokkel = v_nokkel) THEN
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
        SELECT bi.oppdrag_id, bi.svarkropp INTO v_fasit
          FROM public.bestilling_idempotens bi
         WHERE bi.tenant = p_tenant
           AND bi.idempotensnokkel = v_nokkel
           AND bi.beslutning = p_utfall;
        IF p_claim IS NOT NULL
           OR (v.tilstand = 'aktivt' AND v.lease_utloper > now())
           OR NOT FOUND THEN
            RAISE EXCEPTION 'terminaliser_planvindu: utfallet krever et '
                'levende claim eller en verifisert idempotensrad'
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        -- OPPDRAGET HENTES FRA FASITEN, DET PÅSTÅS IKKE (Codex P2 på
        -- #106). Porten over verifiserte `beslutning` mot den immutable
        -- raden — og skrev deretter kallerens `p_oppdrag` inn i ticket.
        -- Halve raden var altså bevist og halve trodd, på nøyaktig den
        -- veien som per §5 IKKE eier noe claim og derfor ikke kan vite
        -- noe. Et tick med feil eller oppdiktet `oppdrag_id` er ikke
        -- bare unøyaktig evidens: `planer_gjentatt_uten_resultat` leser
        -- feltet for å finne resultatet, og et `tillat` som peker på et
        -- oppdrag uten resultat teller som «uten resultat» — tre av dem
        -- og planen pauses, en pause bare et menneske kan oppheve.
        --
        -- Avledningen er den samme som klassifisererens `_fasit`:
        -- kolonnen først, og `svarkropp->>'oppdrag_id'` som fallback for
        -- rader skrevet før kolonnen ble fylt. Fallbacken er vaktet mot
        -- ikke-numerisk innhold — `svarkropp` er fritt JSONB, og en
        -- castfeil her ville veltet gjenopprettingen i stedet for å
        -- utføre den. `p_oppdrag` ignoreres på denne veien med vilje:
        -- raden ER fasiten, og et argument som spriker fra den er per
        -- definisjon det som ikke skal vinne.
        v_oppdrag := coalesce(
            v_fasit.oppdrag_id,
            CASE WHEN v_fasit.svarkropp->>'oppdrag_id' ~ '^[0-9]+$'
                 THEN (v_fasit.svarkropp->>'oppdrag_id')::BIGINT END);
    END IF;
    -- TICK-INNSETTINGEN DELER PLANLÅSEN MED PAUSEBESLUTNINGEN
    -- (Codex P2 på #105). Sveipene bygde hele sin serialisering på
    -- planraden: `pause_gjentatt_uten_resultat` og `varsle_plan_brudd`
    -- tar den FOR UPDATE, leser så de tre siste tickene, låser deres
    -- oppdragsnøkler og avgjør. Men SKRIVEREN av tick — denne
    -- funksjonen — tok den aldri. Låsen serialiserte altså sveipene mot
    -- hverandre og mot promoteringen, og ikke mot det ene som avgjør
    -- hva «de tre siste» ER.
    --
    -- Følgen: et fjerde, ferskere `brudd`-tick som committer etter at
    -- sveipen leste utvalget, men før pausen, BRYTER stripen uten at
    -- sveipen ser det — planen pauses som `gjentatt_uten_resultat` på
    -- en strek som ikke lenger finnes, og bare et menneske kan oppheve
    -- den pausen. Speilvendt: et nytt `tillat`-ticks oppdrag var ikke
    -- med i låsesettet, så artefaktlåsen fra runde 4 dekket det ikke,
    -- og promoteringen av nettopp DET resultatet var fri igjen.
    --
    -- Låsen tas her, ETTER vindusraden, så den globale rekkefølgen
    -- vindu → plan → oppdragsresultat → varsellås står uendret; ingen
    -- vei går motsatt vei (`pause_plan`, `stans_plan`, `gjenoppta_plan`
    -- og sveipene rører aldri en vindusrad under planlåsen).
    --
    -- FOR UPDATE, ikke FOR SHARE, selv om eksklusjon mot sveipen er alt
    -- som trengs: materialisereren terminaliserer og pauser i SAMME
    -- transaksjon (runde 3), og med FOR SHARE her ville den oppgradert
    -- til FOR UPDATE på samme rad. To arbeidere som terminaliserer hvert
    -- sitt vindu på samme plan ville da holdt hver sin delte lås og
    -- ventet på hverandres oppgradering — en vranglås som først dukker
    -- opp den dagen en plan har to vinduer i kø. Å ta den eksklusivt med
    -- én gang koster serialisering av terminaliseringer på samme plan,
    -- som skjer én gang per vindu.
    PERFORM 1 FROM public.bestillingsplan b
     WHERE b.plan_id = p_plan AND b.tenant = p_tenant FOR UPDATE;
    UPDATE public.bestillingsplan_vindu w
       SET tilstand = 'terminal', terminalisert_ts = now()
     WHERE w.plan_id = p_plan AND w.tenant = p_tenant
       AND w.vindu_start = p_vindu;
    INSERT INTO public.bestillingsplan_tick
        (plan_id, tenant, vindu_start, idempotensnokkel, utfall,
         oppdrag_id, detalj)
    VALUES (p_plan, p_tenant, p_vindu, v_nokkel, p_utfall, v_oppdrag,
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

-- ------------------------------------------------------------
-- DEN DIREKTE SKRIVEVEIEN STENGES (Codex P1 på #106, runde 2)
--
-- Grantene under gis av TABELLEIEREN (migrator, 038 §3), derfor står de
-- utenfor claimer-blokken over: et GRANT fra en ikke-eier er en stille
-- WARNING og ingen rettighet.
--
-- `disponit` beholder SELECT — gjenspillet i `utfor_bestilling` leser
-- raden — men mister INSERT. Etter dette finnes det nøyaktig ÉN vei inn i
-- `bestilling_idempotens`, og den setter opphavet selv. Uten dette
-- REVOKE-et er funksjonen over bare et alternativ, ikke en port.
GRANT INSERT ON bestilling_idempotens TO disponit_m37_claimer;
REVOKE INSERT ON bestilling_idempotens FROM disponit;
