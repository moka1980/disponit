-- 049 — modulaksept: aksept er en bevisbåren hendelse, ikke en status
-- noen setter (m56-akseptflipp-klarsignalet, A1–A3; policyaktivering-
-- mønsteret: registerets påstand er CHECK-bundet til en immutabel
-- hendelse som FK-refererer bevisene).
--
-- ⚠️ DOKUMENTERT AVVIK FRA KLARSIGNALET (livsløpsrealiteten):
-- klarsignalet skisserte drillen som «rull r5 → forrige release →
-- tilbake til r5» og aksept av (staging, wcag-r5). Det er strukturelt
-- umulig: moduldeployment-livsløpet er ENVEIS (claiming → draining →
-- retired, trigger `deployment_livslop` i 014), og `bytt_release`
-- nekter eksplisitt å re-claime en drenert release («ny release
-- kreves») — regelen står til og med i sjekklisteskriptets egne
-- kommentarer. Lesesvarets «r5→r4→r5 booter» målte bytene, ikke
-- livsløpet, og var feil. En drill KONSUMERER derfor nødvendigvis
-- releasen den ruller tilbake: tilbake-rullingen drenerer den drillede
-- deploymenten permanent, og «fram igjen» lander på en NY deployment
-- med de samme bytene — AKSEPTKANDIDATEN. Prinsippet i klarsignalet
-- («aksepten binder deploymentraden slik den faktisk kjører») bevares
-- ved at aksepten binder KANDIDATEN — raden som faktisk kjører etter
-- drillen, hvis fødsel og claim-opptak drillen selv bevitnet — og
-- A1-disiplinen holdes av digestlikhets-porten i registreringen:
-- kandidatens bytes SKAL være de drillede bytene. (At alle m56-releaser
-- deler digest er nettopp A1s levende bevis: digest kunne ikke skilt
-- drillet fra udrillet — bare deployment-identiteten kan.)

-- ------------------------------------------------------------
-- 0. CLAIMET ETTERLATER HVEM SOM TOK DET (Codex P1, #117 runde 20).
--
--    Drillens (b)-ledd påstår at NØYAKTIG den drillede releasen hadde et
--    oppdrag inne da rullingen traff. Målingen av det leddet hvilte på
--    artefaktlikheten: `utfort` ⇔ det finnes et promotert artefakt på
--    `p_drillet`. For `utfort` bærer artefaktet releasen og påstanden
--    holder — men `feilet` er også et rent utfall (SP-3 handler om at
--    utfallet er terminalt og signert, ikke om at det gikk bra), og for
--    den statusen sier likheten bare at det IKKE finnes et promotert
--    artefakt. Det er ingen binding; det er fravær av en.
--
--    Et helt vanlig oppdrag som ble bestilt før rullingen og claimet og
--    FEILET etterpå — av rullbakk- eller kandidatarbeideren — oppfyller
--    da alt (b) krever: terminalt, signert kvittering med brent
--    kapabilitet, opprettet før `v_rull_ts`, terminalt etter. En kaller
--    med `disponit_modules_admin` kunne pare et slikt oppdrag med de to
--    andre vindusoppfyllende oppdragene og skrive en grønn, UFORANDERLIG
--    drillrad uten at den drillede releasen noensinne hadde arbeid inne.
--
--    `oppdrag` visste ikke hvem som claimet. Kontraktbindingen
--    (modul/versjon/hash/epoch, 015) stemples ved claim, men RELEASEN —
--    den ene identiteten hele drillen handler om — ble aldri skrevet
--    ned, enda `claim_neste_oppdrag` fencer på nettopp den (kallerens
--    deployment må være `claiming`). Den skrives ned nå, av den samme
--    funksjonen, i det samme UPDATE-et, under den samme fullmakten.
--
--    ⚠️ DRIFTSKONSEKVENS, uttalt: sporet finnes bare for claim gjort
--    ETTER denne migrasjonen. Oppdragene fra staging-drillen 2026-08-20
--    ble claimet før kolonnen fantes og står med NULL, og en drillrad
--    registrert på dem får derfor `rene_utfall_ok = false` — aksepten
--    stopper på FK-en mot et grønt utfall, høylytt og før noe skrives.
--    Drillen må kjøres på nytt etter 049. Alternativet — å la NULL
--    passere som «vi vet ikke» — er nøyaktig hullet funnet peker på, og
--    en uforanderlig aksept er feil sted å ta den snarveien.
--
--    …OG NÅR DET BLE TATT (Codex P1, #117 runde 21). Claim-stoppet er en
--    VARIGHET: den drenerte releasen lot et claimbart oppdrag ligge i
--    minst `min_ventetid_s`. Basen kjente ingen slik varighet —
--    `oppdrag` bar `opprettet` og `status_ts`, og `status_ts` er den
--    SISTE overgangen, ikke claimet. `forste_claim_ts` er tidspunktet
--    oppdraget FØRSTE gang ble tatt; sammen med `opprettet` er det
--    nøyaktig hvor lenge det lå ubehandlet mens en levende forgjenger
--    ville tatt det. Første, ikke siste: en reclaim etter utløpt lease
--    skal ikke kunne strekke et ventevindu som aldri ble observert.
--    Derfor er DENNE kolonnen write-once, mens `claim_release_id`
--    bevisst ikke er det — de måler ulike ting og har hver sin regel.
--    Samme driftskonsekvens gjelder: et pre-049-claim står ustemplet, og
--    `claim_stopp_ok` blir da false.
-- ------------------------------------------------------------
ALTER TABLE oppdrag
    ADD COLUMN IF NOT EXISTS claim_release_id TEXT,
    ADD COLUMN IF NOT EXISTS claim_miljo      TEXT,
    ADD COLUMN IF NOT EXISTS forste_claim_ts  TIMESTAMPTZ;

-- Samme vakt som kontraktbindingen har (015): kolonnene settes KUN av
-- den herdede claim-funksjonen, og aldri ved opprettelse. Kjøretiden og
-- deployfullmakten har direkte `UPDATE`/`INSERT` på `oppdrag`; uten
-- vakten kunne begge skrive hvilken som helst release inn i sporet, og
-- sporet ville målt sin egen forfalskning.
--
-- Egen trigger, ikke en utvidelse av `oppdrag_kolonnelaas`: den
-- funksjonen bor i 015 og ville måttet kopieres hit i sin helhet for to
-- nye ledd. To BEFORE ROW-triggere fyrer i navnerekkefølge og må begge
-- passere; leddene her er uavhengige av statusmaskinen, så rekkefølgen
-- betyr ingenting.
--
-- IKKE write-once: en reclaim etter utløpt lease (005/037) skal
-- re-stemple, slik at sporet peker på den releasen som faktisk holdt
-- claimet til slutt. Et oppdrag er ikke reclaimbart når det først er
-- terminalt, så den siste stemplingen ER den som tok det i mål.
CREATE OR REPLACE FUNCTION oppdrag_claim_release_vakt()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.claim_release_id IS NOT NULL OR NEW.claim_miljo IS NOT NULL
           OR NEW.forste_claim_ts IS NOT NULL
        THEN
            RAISE EXCEPTION 'oppdrag: claim-releasen kan ikke settes ved'
                ' opprettelse (stemples kun ved claim)';
        END IF;
        RETURN NEW;
    END IF;
    IF current_user <> 'disponit_m37_claimer' AND (
           NEW.claim_release_id IS DISTINCT FROM OLD.claim_release_id
        OR NEW.claim_miljo      IS DISTINCT FROM OLD.claim_miljo
        OR NEW.forste_claim_ts  IS DISTINCT FROM OLD.forste_claim_ts) THEN
        RAISE EXCEPTION 'oppdrag: claim-releasen settes kun av'
            ' claim-funksjonen';
    END IF;
    -- Write-once, for ALLE — også claim-funksjonen selv (som skriver
    -- gjennom `coalesce`, så en reclaim aldri havner her). Ventetiden
    -- måles fra det FØRSTE claimet; kunne stempelet flyttes, ville et
    -- oppdrag som ble tatt med én gang og reclaimet et halvt minutt
    -- senere sett ut som et claim-stopp som aldri ble observert.
    IF OLD.forste_claim_ts IS NOT NULL
       AND NEW.forste_claim_ts IS DISTINCT FROM OLD.forste_claim_ts THEN
        RAISE EXCEPTION 'oppdrag: forste_claim_ts er write-once — det'
            ' første claimet er det som avslutter ventetiden';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS oppdrag_claim_release ON oppdrag;
CREATE TRIGGER oppdrag_claim_release BEFORE INSERT OR UPDATE ON oppdrag
    FOR EACH ROW EXECUTE FUNCTION oppdrag_claim_release_vakt();

-- Claim-funksjonen stempler sporet. Kroppen er ordrett 037 — kun
-- `v_b_rel`/`v_b_miljo` og de to kolonnene i UPDATE-et er nye — og
-- signaturen er uendret, så `CREATE OR REPLACE` beholder eierskap
-- (`disponit_m37_claimer`) og privilegier.
--
-- Sporet følger BINDINGEN: en uregistrert oppdragstype claimes uten
-- kontrakt og uten deployment-port (legacy-grenen), og da er det ingen
-- verifisert release å skrive ned. `p_release_id` fra en slik kaller er
-- en påstand ingen port har prøvd, og en påstand hører ikke hjemme i et
-- spor drillen senere måler på. NULL er det ærlige svaret.
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
        SELECT k.id, k.oppdragstype INTO v_id, v_ot
          FROM public.oppdrag k
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
         ORDER BY k.opprettet, k.id
           FOR UPDATE SKIP LOCKED
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

-- ------------------------------------------------------------
-- 1. Drillen: egen smal, immutabel tabell (lesesvar 2: detalj-jsonb i
--    modulregister_hendelse har ingen skjemahåndheving; en rad som skal
--    FK-refereres og bære kontrollpunktutfall fortjener kolonner).
--    Kvalifikasjonen (tre grønne kontrollpunkter) står I den
--    refererbare nøkkelen (E1f-formen): append-only gjør den varig,
--    så SP-9 holder i begge ledd.
-- ------------------------------------------------------------
CREATE TABLE moduldrill (
    drill_id       BIGINT GENERATED ALWAYS AS IDENTITY,
    modul_id       TEXT NOT NULL,
    miljo          TEXT NOT NULL,
    -- releasen som VAR claiming og ble rullet tilbake (drenert av drillen)
    drillet_release TEXT NOT NULL,
    -- releasen det ble rullet TILBAKE til (forrige-bytes-releasen)
    rullback_release TEXT NOT NULL,
    -- releasen «fram igjen» landet på — byte-identisk med den drillede
    -- (digestporten i registrer_moduldrill), og raden aksepten binder
    akseptkandidat_release TEXT NOT NULL,
    -- fencing-konteksten drillen målte i: module_epoch er
    -- registertilstand, ikke FK-bar identitet — den snapshottes (A1)
    epoch_snapshot  BIGINT NOT NULL,
    digest_snapshot TEXT NOT NULL,
    -- Codex' P1 på PR #117 (runde 5): de tre utfallene under var KALLERENS
    -- påstander. `disponit_modules_admin` er den brede deployfullmakten
    -- (registrer_release, bytt_release, onboarding …), og en som holdt
    -- den kunne kalle `registrer_moduldrill` direkte med tre håndskrevne
    -- `true` og få en immutabel, grønn drillrad uten å ha kjørt noen
    -- drill — hvorpå `aksepter_moduldeployment` FK-refererte den og
    -- aksepten var et faktum. HELE evidensapparatet lå i skriptet, og et
    -- skript er ingen skranke for den som kan la være å bruke det.
    --
    -- Utfallene MÅLES nå av definerne selv, i `oppdrag` og `artefakt`.
    -- Da må drillraden bære HVA den ble målt på: tenanten og de tre
    -- oppdragene drillen faktisk kjørte. De er FK-bundet, så en drillrad
    -- kan ikke peke på oppdrag som ikke finnes, og målingen kan regnes
    -- ut på nytt av hvem som helst i ettertid.
    tenant           TEXT NOT NULL,
    -- (b) oppdraget som VAR claimet da rullingen traff
    inflight_oppdrag BIGINT NOT NULL,
    -- (a)+(b2) oppdraget den drenerte releasen lot ligge, og som
    -- rullbakken plukket etter at den ble bootet
    rullback_oppdrag BIGINT NOT NULL,
    -- (c) kandidatens eget oppdrag — og kilden til akseptens E2E-bevis
    kandidat_oppdrag BIGINT NOT NULL,
    -- bytene raden hviler på: sha256 av drillartefaktet slik aksepten
    -- leste det. Basen kan ikke lese fila, men raden skal NAVNGI den.
    artefakt_sha256  TEXT NOT NULL CHECK (artefakt_sha256 ~ '^[0-9a-f]{64}$'),
    claim_stopp_ok  BOOLEAN NOT NULL,  -- (a) drenert release claimer ikke nye
    rene_utfall_ok  BOOLEAN NOT NULL,  -- (b) løpende oppdrag: rent utfall (SP-3)
    tilbake_ok      BOOLEAN NOT NULL,  -- (c) kandidaten plukker og fullfører
    nokkel          TEXT NOT NULL,     -- SP-2: replay-nøkkel
    aktor           TEXT NOT NULL,
    -- Codex' P2 på PR #117 (runde 3): `utfort_ts` sto med DEFAULT now()
    -- og ble aldri gitt en verdi, så en drill som ble kjørt timer eller
    -- dager før aksepten ble innskrevet som om den kjørte i
    -- akseptøyeblikket. Da kan ingen ferskhetskontroll skille UTFØRELSE
    -- fra senere REGISTRERING, og det immutable sporet er feil om det
    -- ene faktumet ingen kan rekonstruere i ettertid. De to
    -- tidspunktene er ulike fakta og har derfor hver sin kolonne:
    -- `utfort_ts` er drillartefaktets egen `ts` (målingen), og
    -- `registrert_ts` er innskrivingen.
    utfort_ts       TIMESTAMPTZ NOT NULL,
    registrert_ts   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- En drill kan ikke ha kjørt etter at den ble registrert.
    CHECK (utfort_ts <= registrert_ts),
    PRIMARY KEY (modul_id, drill_id),
    UNIQUE (nokkel),
    CHECK (drillet_release <> rullback_release),
    CHECK (akseptkandidat_release <> drillet_release),
    -- Ett oppdrag kan ikke være tre ledd: leddene måler ulike faser, og
    -- samme id tre steder er en drill som aldri fant sted.
    CHECK (inflight_oppdrag <> rullback_oppdrag),
    CHECK (inflight_oppdrag <> kandidat_oppdrag),
    CHECK (rullback_oppdrag <> kandidat_oppdrag),
    FOREIGN KEY (tenant, inflight_oppdrag) REFERENCES oppdrag (tenant, id),
    FOREIGN KEY (tenant, rullback_oppdrag) REFERENCES oppdrag (tenant, id),
    FOREIGN KEY (tenant, kandidat_oppdrag) REFERENCES oppdrag (tenant, id),
    FOREIGN KEY (modul_id, miljo, drillet_release)
        REFERENCES moduldeployment (modul_id, miljo, release_id),
    FOREIGN KEY (modul_id, miljo, akseptkandidat_release)
        REFERENCES moduldeployment (modul_id, miljo, release_id),
    FOREIGN KEY (modul_id, rullback_release)
        REFERENCES modulrelease (modul_id, release_id),
    -- den refererbare nøkkelen for aksepthendelsen: drill FOR nøyaktig
    -- denne deploymentraden, med utfallene i nøkkelen
    UNIQUE (modul_id, miljo, akseptkandidat_release, drill_id,
            claim_stopp_ok, rene_utfall_ok, tilbake_ok)
);
CREATE TRIGGER drill_immutable BEFORE UPDATE OR DELETE ON moduldrill
    FOR EACH ROW EXECUTE FUNCTION modulregister_append_only();
CREATE TRIGGER drill_ingen_truncate BEFORE TRUNCATE ON moduldrill
    FOR EACH STATEMENT EXECUTE FUNCTION modulregister_append_only();

-- Codex' P1 på PR #117 (runde 14): drillraden BÆRER tenant — den navngir
-- tenanten, tre oppdrags-IDer, aktøren og bytene den ble målt på — men
-- sto uten RLS, mens `migrer.py` ga kjøretidsrollen `SELECT` på hele
-- tabellen. Nabotabellen `artefakt` er tenant-filtrert (008/016, FORCE);
-- her var den ikke det, så én forespørselsvei utenfor sin egen
-- tenantkontekst — eller en kompromittert kjøretidsrolle — leste hver
-- eneste tenants driftsbevis. Tenantporten står nå PÅ RADEN, ikke i
-- fullmakten: en fullmakt kan gis igjen ved et uhell, en policy gjelder
-- uansett hvem som får `SELECT` senere.
--
-- Ikke FORCE: eieren er migrator, altså deployveien som LAGET tabellen
-- og som når som helst kan skru av enhver policy. Porten finnes for
-- forespørselsveien og for definerne, og en FORCE ville i tillegg
-- blindet driftens egen etterkontroll (akseptskriptets kvitteringslesning
-- og `modulaksept_punkt`, som ikke har noen tenantkolonne å filtrere på).
ALTER TABLE moduldrill ENABLE ROW LEVEL SECURITY;
CREATE POLICY moduldrill_tenant ON moduldrill
    USING (tenant = current_setting('disponit.tenant', true));
-- Definerne (`registrer_moduldrill` skriver raden, `aksepter_moduldeployment`
-- leser kandidatoppdraget ut av den) kjører som `disponit_modul_eier` og
-- måler tenanten EKSPLISITT i sine egne kontroller — de skal ikke også
-- måtte bære kallerens GUC. Samme mønster som `policyaktivering_eier` (047).
CREATE POLICY moduldrill_eier ON moduldrill
    USING (CURRENT_USER = 'disponit_modul_eier');

-- ------------------------------------------------------------
-- 2. A2: artefaktets releasesnapshot blir relasjonelt. Snapshotten er
--    alt kapabilitets-attestert ved skriving (017: verdiene kopieres fra
--    kapabilitetsraden, aldri fra kalleren) og modulrelease-PK-en kan
--    aldri gjenbrukes — FK-en gjør kjeden mekanisk etterprøvbar også.
--    Refererbar nøkkel med tilstanden I identiteten (E1f):
--    resultatlåsen (017) gjør 'promotert' varig.
-- ------------------------------------------------------------
ALTER TABLE artefakt ADD CONSTRAINT artefakt_release_fk
    FOREIGN KEY (modul_id, release_id)
    REFERENCES modulrelease (modul_id, release_id);
ALTER TABLE artefakt ADD CONSTRAINT artefakt_refererbar
    UNIQUE (tenant, artefakt_id, modul_id, release_id, tilstand);

-- ------------------------------------------------------------
-- 3. Kravpunkt-registeret: hva et KOMPLETT punktsett ER, står i
--    lagringen — akseptfunksjonen måler mot dette, ikke mot en liste i
--    kallerens hode (port 5). Punktene er evidensgrensen
--    `wcag-kontroll-v1` fra 014c-klarsignalet §12, ordrett.
-- ------------------------------------------------------------
-- REGISTERET EIER GRENSEN, KILDETYPEN OG DEN GRØNNE VERDIEN (Codex P1,
-- #117 runde 15). Før bar det bare punktnavnene, og
-- `aksepter_moduldeployment` tok `grenseverdi`, `maalt_verdi` og
-- `kilde_ref` som frie strenger fra kalleren — den kontrollerte at de
-- FANTES, aldri hva de sa. En kaller med `disponit_modules_admin` kunne
-- derfor skrive 21 immutable observasjoner med hjemmelagde grenser og
-- måletall og oppfylle A3 uten å ha vært innom `m56-aksept.py`. Porten
-- mot nettopp det fantes bare i skriptet, og et skript er ingen skranke
-- for den som kaller definern direkte — samme lærdom som runde 5.
--
-- Nå står grensen (§12) og hvilken KILDETYPE punktet skal bæres av i
-- lagringen, sammen med den verdien en grønn måling MÅ ha. Kalleren kan
-- bare gjenta dem; funksjonen regner målingen mot kravet og krever at
-- `kilde_ref` peker på evidens transaksjonen selv kan se.
CREATE TABLE akseptkrav_punkt (
    krav_id TEXT NOT NULL,
    punkt   TEXT NOT NULL,
    -- Hva slags evidens punktet skal bæres av. Samme domene som
    -- `modulaksept_punkt.kilde_type`, men her som KRAV, ikke som valg.
    kilde_type TEXT NOT NULL CHECK (kilde_type IN
        ('artefakt', 'registerhendelse', 'evidensfil', 'ci_kjoring')),
    grenseverdi TEXT NOT NULL,
    -- Verdien en GRØNN måling må ha. `maalt_verdi` sammenlignes med
    -- denne — en observasjon som ikke oppfyller kravet skrives ikke.
    maalt_krav  TEXT NOT NULL,
    PRIMARY KEY (krav_id, punkt)
);
-- KRAVET ER IMMUTABELT (Codex P2, #117 runde 17). Uten dette kunne en
-- senere migrasjon rette `grenseverdi`, `maalt_krav` eller `kilde_type`
-- for et krav_id som ALT har aksepter skrevet mot seg — og aksepten er
-- uforanderlig. Radene ville da stå som «akseptert mot wcag-kontroll-v1»
-- mens observasjonene deres bærer den forrige grensen: et revisjonsspor
-- som forteller noe annet enn kallet som lagde det. `modulaksept_punkt`
-- binder bare (krav_id, punkt), så FK-en fanger det ikke.
--
-- En ENDRET grense er et nytt krav, ikke en rettelse: skriv
-- `wcag-kontroll-v2` og la de gamle aksepene stå mot det de faktisk ble
-- målt mot.
CREATE TRIGGER akseptkrav_punkt_immutable BEFORE UPDATE OR DELETE
    ON akseptkrav_punkt
    FOR EACH ROW EXECUTE FUNCTION modulregister_append_only();
CREATE TRIGGER akseptkrav_punkt_ingen_truncate BEFORE TRUNCATE
    ON akseptkrav_punkt
    FOR EACH STATEMENT EXECUTE FUNCTION modulregister_append_only();

-- …OG ET NYTT PUNKT ER OGSÅ EN ENDRING (Codex P2, #117 runde 18).
-- Triggeren over dekket bare UPDATE og DELETE, så en senere migrasjon
-- kunne INSERT-e et punkt på et `krav_id` som alt har immutable aksepter
-- skrevet mot seg. Det endrer hva «komplett» BETYR: `aksepter_module-
-- deployment` krever hele punktsettet i registeret, så nye aksepter måles
-- mot ett sett mens de gamle bærer et annet — og de gamle står fortsatt
-- som «akseptert mot wcag-kontroll-v1» i `modulaksept_status`. Ingen kan
-- se på en akseptrad at kravet den ble målt mot, har vokst siden.
--
-- INSERT må likevel være åpen, ellers kan et NYTT krav ikke registreres i
-- det hele tatt. Skillet er ikke raden, men om kravet fantes FØR
-- setningen: en overgangstabell lar porten se nøyaktig det. Et krav
-- registreres derfor i ÉN setning — og skal det ha flere punkter senere,
-- er det `wcag-kontroll-v2`.
CREATE OR REPLACE FUNCTION akseptkrav_punkt_hele_kravet_i_ett()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_krav TEXT;
BEGIN
    SELECT k.krav_id INTO v_krav
      FROM public.akseptkrav_punkt k
     WHERE k.krav_id IN (SELECT n.krav_id FROM nye n)
       AND NOT EXISTS (SELECT 1 FROM nye n
                        WHERE n.krav_id = k.krav_id AND n.punkt = k.punkt)
     LIMIT 1;
    IF v_krav IS NOT NULL THEN
        RAISE EXCEPTION 'akseptkrav_punkt: kravet % er alt registrert —'
            ' et nytt punkt endrer hva «komplett» betyr for aksepter som'
            ' alt er skrevet, og hører hjemme i en NY kravversjon', v_krav;
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER akseptkrav_punkt_ingen_tillegg AFTER INSERT
    ON akseptkrav_punkt
    REFERENCING NEW TABLE AS nye
    FOR EACH STATEMENT EXECUTE FUNCTION akseptkrav_punkt_hele_kravet_i_ett();
-- ------------------------------------------------------------
-- 3b. HVILKEN CI-kjøring invariantpunktene krever, og HVA veien som
--     spurte GitHub faktisk så (Codex P1, #117 runde 16).
--
--     `aksepter_moduldeployment` sammenlignet `kilde_ref` med sine egne
--     to parametre — `p_ci_run` og `p_ci_commit`. To felter fra samme
--     kaller som er enige, er ingen CI-kjøring: en `disponit_modules_
--     admin`-kaller kunne velge et løpenummer og en commit fritt,
--     gjenta dem i alle 15 invariantpunktene, og få en immutabel aksept
--     der ingen kjøring hadde funnet sted. Likheten målte formen på en
--     streng, ikke at noe var kjørt.
--
--     Basen kan ikke spørre GitHub — like lite som den kan verifisere en
--     HMAC-signatur (jf. kvitteringsporten). Det den KAN kreve, er at
--     veien som spør har vært her og skrevet ned HVA DEN SÅ: workflowen,
--     hendelsen, grenen, konklusjonen og hode-SHA-en, i en immutabel
--     rad. Da er en fabrikkering ikke lenger to like strenger i samme
--     kall, men en attest som eksplisitt påstår at `ci.yml` kjørte grønt
--     på en push til main for nøyaktig akseptcommiten — og aksepten
--     REGNER punktet mot kravet i registeret, i stedet for mot kalleren.
-- ------------------------------------------------------------
CREATE TABLE akseptkrav_ci (
    krav_id     TEXT PRIMARY KEY,
    arbeidsflyt TEXT NOT NULL,
    hendelse    TEXT NOT NULL,
    gren        TEXT NOT NULL,
    konklusjon  TEXT NOT NULL
);
-- Samme grunn som for punktregisteret: en aksept er skrevet mot ET krav,
-- og kravet kan ikke endres under den i ettertid.
CREATE TRIGGER akseptkrav_ci_immutable BEFORE UPDATE OR DELETE
    ON akseptkrav_ci
    FOR EACH ROW EXECUTE FUNCTION modulregister_append_only();
CREATE TRIGGER akseptkrav_ci_ingen_truncate BEFORE TRUNCATE ON akseptkrav_ci
    FOR EACH STATEMENT EXECUTE FUNCTION modulregister_append_only();
-- Repoet har flere workflows, og en grønn kjøring av en ANNEN på samme
-- commit beviser ingenting om portene her. `ci.yml` trigges dessuten av
-- både `pull_request` og push til `main`, og bare den siste sier at
-- bytene faktisk ble en del av historikken.
INSERT INTO akseptkrav_ci (krav_id, arbeidsflyt, hendelse, gren,
                           konklusjon) VALUES
    ('wcag-kontroll-v1', '.github/workflows/ci.yml', 'push', 'main',
     'success');

CREATE TABLE ci_kjoringsattest (
    ci_run       TEXT PRIMARY KEY CHECK (btrim(ci_run) <> ''),
    arbeidsflyt  TEXT NOT NULL,
    hendelse     TEXT NOT NULL,
    gren         TEXT NOT NULL,
    konklusjon   TEXT NOT NULL,
    -- Commiten kjøringen faktisk kjørte på, i sin egen form: en attest
    -- som ikke navngir bytene, binder ingenting.
    hode_sha     TEXT NOT NULL CHECK (hode_sha ~ '^[0-9a-f]{40}$'),
    attestert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    aktor        TEXT NOT NULL,
    -- …og HVEM som var innlogget da referatet ble skrevet (Codex P1,
    -- #117 runde 19). `aktor` er en etikett kalleren velger fritt —
    -- 'm56-aksept' er like billig å skrive som hva som helst annet. Den
    -- autentiserte identiteten settes av funksjonen fra `session_user`
    -- og kan ikke oppgis i kallet: en attest skal bære hvem som faktisk
    -- sto der, ikke hvem kallet påsto å være.
    attestert_av TEXT NOT NULL
);
-- En kjøring har ett utfall. Attesten er et referat, ikke en kladd.
CREATE TRIGGER ci_attest_immutable BEFORE UPDATE OR DELETE
    ON ci_kjoringsattest
    FOR EACH ROW EXECUTE FUNCTION modulregister_append_only();
CREATE TRIGGER ci_attest_ingen_truncate BEFORE TRUNCATE ON ci_kjoringsattest
    FOR EACH STATEMENT EXECUTE FUNCTION modulregister_append_only();

-- ------------------------------------------------------------
-- 3c. …og HVA EVIDENSFILEN SA (Codex P1, #117 runde 19).
--
--     De fire målte punktene sto igjen med nøyaktig hullet runde 16
--     lukket for CI-punktene: `p_evidens_sha` og punktenes `kilde_ref`
--     kom BEGGE fra samme kall, og porten sammenlignet dem med hverandre.
--     To felter fra én kaller som er enige, er ingen evidensfil. Verre:
--     `p_evidens_sha` hadde ingen formkrav, så en tom hash og en
--     `kilde_ref` som endte på `@sha256:` var «enige» — og siden basen
--     dessuten KREVER at `maalt_verdi` er registerets egen grønne
--     `maalt_krav`, kunne en `disponit_modules_admin`-kaller lese de fire
--     fasitverdiene rett ut av `akseptkrav_punkt`, gjenta dem, og skrive
--     en immutabel aksept der ingen fil fantes og ingenting var målt.
--
--     Basen kan ikke hashe en fil, like lite som den kan spørre GitHub.
--     Den kan kreve det samme her som der: at veien som LESTE filen har
--     vært her og skrevet ned hva den så — stien, sha-en og verdien
--     filen bar FOR HVERT PUNKT — i en immutabel rad, skrevet med en
--     ANNEN fullmakt enn den som skriver aksepten. Da måles punktet mot
--     referatet, ikke mot kalleren, og «fire grønne observasjoner» er en
--     påstand noen har signert med sin egen identitet.
--
--     Nøkkelen er (sha256, punkt): attesten hører til BYTENE, ikke til
--     stien. Samme fil lest to ganger gir samme rad; en endret fil er en
--     annen sha og dermed en annen attest.
-- ------------------------------------------------------------
CREATE TABLE evidensfil_attest (
    sha256      TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    punkt       TEXT NOT NULL,
    krav_id     TEXT NOT NULL,
    -- stien filen ble lest fra, slik `kilde_ref` navngir den. Uten den
    -- ville aksepten kunnet peke på en hvilken som helst sti med riktig
    -- hale — og en observasjon skal navngi filen, ikke bare hashen.
    sti         TEXT NOT NULL CHECK (btrim(sti) <> ''),
    -- verdien FILEN bar for dette punktet. Det er DENNE aksepten regner
    -- mot kravet; kallerens egen `maalt_verdi` er bare en gjentakelse.
    maalt_verdi TEXT NOT NULL,
    attestert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    aktor        TEXT NOT NULL,
    -- samme skille som i CI-attesten: etiketten kallet oppga, og den
    -- autentiserte identiteten funksjonen selv skriver.
    attestert_av TEXT NOT NULL,
    PRIMARY KEY (sha256, punkt),
    FOREIGN KEY (krav_id, punkt)
        REFERENCES akseptkrav_punkt (krav_id, punkt)
);
-- Én fil har ett innhold, og ett innhold har én måling per punkt.
CREATE TRIGGER evidensfil_attest_immutable BEFORE UPDATE OR DELETE
    ON evidensfil_attest
    FOR EACH ROW EXECUTE FUNCTION modulregister_append_only();
CREATE TRIGGER evidensfil_attest_ingen_truncate BEFORE TRUNCATE
    ON evidensfil_attest
    FOR EACH STATEMENT EXECUTE FUNCTION modulregister_append_only();

-- Grensene er 014c-klarsignalet §12s, ordrett, og de fire målte
-- punktene bæres av runde-sammendraget (som selv er sha-bundet i
-- manifestet). Invariantpunktene har ingen historiske rader — de hviler
-- HELT på at CI-kjøringen finnes, er grønn og testet akseptcommiten.
INSERT INTO akseptkrav_punkt (krav_id, punkt, kilde_type, grenseverdi,
                              maalt_krav) VALUES
    ('wcag-kontroll-v1', 'kontroll.ti_kjoringer_signert_innen_frist',
     'evidensfil', '10/10', '10/10'),
    ('wcag-kontroll-v1', 'funn.avvik_mot_fasit', 'evidensfil', '0', '0'),
    ('wcag-kontroll-v1', 'robots.brudd_i_mallogg', 'evidensfil', '0', '0'),
    ('wcag-kontroll-v1', 'frekvens.over_grense_utfort',
     'evidensfil', '0', '0'),
    ('wcag-kontroll-v1', 'skjema.brudd_promotert',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('wcag-kontroll-v1', 'skjema.hash_uten_rad_akseptert',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('wcag-kontroll-v1', 'skjema.mutert_ureferert',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('wcag-kontroll-v1', 'skjema.slettet',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('wcag-kontroll-v1', 'rapport.uten_pakrevd_arlighetsfelt_akseptert',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('wcag-kontroll-v1', 'rapport.klartekst_i_logg_eller_dump',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('wcag-kontroll-v1', 'domene.kontroll_uten_verifisering',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('wcag-kontroll-v1', 'payload.felt_utover_skjema_utlevert',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('wcag-kontroll-v1', 'deploy.registerrad_uten_kodefestet_type',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('wcag-kontroll-v1', 'deploy.ekstern_lesing_uten_malautorisasjonsflagg',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('wcag-kontroll-v1', 'klasse.eksisterende_kontrakt_omklassifisert',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('wcag-kontroll-v1', 'klasse.aktivering_uten_frekvensgrense_lyktes',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('wcag-kontroll-v1', 'klasse.aktivering_uten_malautorisasjon_lyktes',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    -- §2.5: egress-punktet SKAL bæres av CI-kjøringen. Ingen kilde vi
    -- har stiller spørsmålet ennå (den utstedende proxyen finnes ikke),
    -- og `m56-aksept.py::UMAALTE` blokkerer derfor hele aksepten framfor
    -- å la punktet arve nærmeste tall. Raden her sier hva punktet KREVER
    -- når porten skrives — den gjør det ikke målt.
    ('wcag-kontroll-v1', 'egress.proxytoken_til_ikke_ekstern_lesing',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('wcag-kontroll-v1', 'malautorisasjon.ikke_registrert_vilkar_talte',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('wcag-kontroll-v1', 'malautorisasjon.feil_maldomene_godtatt',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('wcag-kontroll-v1', 'malautorisasjon.positiv_sti_virker',
     'ci_kjoring', 'ja', 'ja');

-- ------------------------------------------------------------
-- 4. Aksepthendelsen. Én per deploymentrad (PK) — port 14: hendelsen
--    for (staging, X) autoriserer aldri (produksjon, X); et reelt
--    produksjonsmiljø krever egen aksept med egen drill.
--    Drill-kvalifikasjonen bæres av FK-en med utfallene i nøkkelen
--    (kolonnene her er CHECK-låst true — de finnes for å bære FK-en,
--    ikke for å kunne variere).
-- ------------------------------------------------------------
CREATE TABLE modulaksept (
    modul_id   TEXT NOT NULL,
    miljo      TEXT NOT NULL,
    release_id TEXT NOT NULL,
    drill_id   BIGINT NOT NULL,
    drill_claim_stopp BOOLEAN NOT NULL DEFAULT true CHECK (drill_claim_stopp),
    drill_rene_utfall BOOLEAN NOT NULL DEFAULT true CHECK (drill_rene_utfall),
    drill_tilbake     BOOLEAN NOT NULL DEFAULT true CHECK (drill_tilbake),
    krav_id    TEXT NOT NULL,
    e2e_tenant TEXT NOT NULL,
    e2e_artefakt_id UUID NOT NULL,
    e2e_tilstand TEXT NOT NULL DEFAULT 'promotert'
        CHECK (e2e_tilstand = 'promotert'),
    -- SP-11: den INNSJEKKEDE filen — og den navngis av bytene sine
    -- (Codex P1, runde 19): uten formkravet var en tom streng en gyldig
    -- «hash», og porten mot `kilde_ref` sammenlignet den med en
    -- `kilde_ref` som endte på `@sha256:`.
    evidens_jsonl_sha256 TEXT NOT NULL
        CHECK (evidens_jsonl_sha256 ~ '^[0-9a-f]{64}$'),
    -- …og commiten raden peker på navngis som en commit (Codex P1,
    -- runde 22). `manifest_commit` gikk rett inn i den uforanderlige
    -- raden uten noe formkrav i det hele tatt: en tom streng, et
    -- grennavn eller en setning var like gyldig «proveniens» som en sha.
    manifest_commit TEXT NOT NULL CHECK (manifest_commit ~ '^[0-9a-f]{40}$'),
    ci_run     TEXT NOT NULL,
    ci_commit  TEXT NOT NULL CHECK (ci_commit ~ '^[0-9a-f]{40}$'),
    -- …og de to er DEN SAMME commiten. Punktene påberoper seg «grønn CI
    -- på akseptcommiten», og akseptskriptet har alltid krevd likheten —
    -- men et krav som bare finnes i et skript, er ingen skranke for den
    -- som kaller definereren direkte. To kolonner som får sprike, lar
    -- raden attestere én commit og bevise en annen.
    CHECK (manifest_commit = ci_commit),
    nokkel     TEXT NOT NULL,            -- SP-2: replay-nøkkel
    aktor      TEXT NOT NULL,
    akseptert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (modul_id, miljo, release_id),
    UNIQUE (nokkel),
    FOREIGN KEY (modul_id, miljo, release_id)
        REFERENCES moduldeployment (modul_id, miljo, release_id),
    -- A1: drillen gjelder NØYAKTIG denne deploymentraden, og alle tre
    -- kontrollpunktene var grønne (utfallene står i den refererte nøkkelen)
    FOREIGN KEY (modul_id, miljo, release_id, drill_id,
                 drill_claim_stopp, drill_rene_utfall, drill_tilbake)
        REFERENCES moduldrill (modul_id, miljo, akseptkandidat_release,
                               drill_id, claim_stopp_ok, rene_utfall_ok,
                               tilbake_ok),
    -- A2: E2E-artefaktet er promotert OG produsert av samme release —
    -- delt release_id-kolonne bærer båndet (E1e-formen), tilstanden står
    -- i nøkkelen (E1f-formen)
    FOREIGN KEY (e2e_tenant, e2e_artefakt_id, modul_id, release_id,
                 e2e_tilstand)
        REFERENCES artefakt (tenant, artefakt_id, modul_id, release_id,
                             tilstand)
);
CREATE TRIGGER aksept_immutable BEFORE UPDATE OR DELETE ON modulaksept
    FOR EACH ROW EXECUTE FUNCTION modulregister_append_only();
CREATE TRIGGER aksept_ingen_truncate BEFORE TRUNCATE ON modulaksept
    FOR EACH STATEMENT EXECUTE FUNCTION modulregister_append_only();

-- Samme port som på drillen (Codex P1, runde 14). Aksepten navngir E2E-
-- tenanten, artefakt-UUIDen, aktøren, evidensfilas hash og CI-kjøringen;
-- tenanten den ble målt i, er `e2e_tenant` (registreringen krever alt at
-- den er drillens tenant, så de to kan ikke sprike).
ALTER TABLE modulaksept ENABLE ROW LEVEL SECURITY;
CREATE POLICY modulaksept_tenant ON modulaksept
    USING (e2e_tenant = current_setting('disponit.tenant', true));
CREATE POLICY modulaksept_eier ON modulaksept
    USING (CURRENT_USER = 'disponit_modul_eier');

-- ------------------------------------------------------------
-- 5. A3: én immutabel observasjon per grensepunkt — referansen til
--    beviset, aldri en kopi av konklusjonen (SP-§3). FK-en mot
--    kravpunkt-registeret gjør «komplett» målbart; akseptfunksjonen
--    håndhever at HELE settet skrives i samme transaksjon.
-- ------------------------------------------------------------
CREATE TABLE modulaksept_punkt (
    modul_id   TEXT NOT NULL,
    miljo      TEXT NOT NULL,
    release_id TEXT NOT NULL,
    krav_id    TEXT NOT NULL,
    punkt      TEXT NOT NULL,
    grenseverdi TEXT NOT NULL,
    maalt_verdi TEXT NOT NULL,
    kilde_type TEXT NOT NULL CHECK (kilde_type IN
        ('artefakt', 'registerhendelse', 'evidensfil', 'ci_kjoring')),
    kilde_ref  TEXT NOT NULL,
    PRIMARY KEY (modul_id, miljo, release_id, punkt),
    FOREIGN KEY (modul_id, miljo, release_id) REFERENCES modulaksept,
    FOREIGN KEY (krav_id, punkt) REFERENCES akseptkrav_punkt
);
CREATE TRIGGER akseptpunkt_immutable
    BEFORE UPDATE OR DELETE ON modulaksept_punkt
    FOR EACH ROW EXECUTE FUNCTION modulregister_append_only();
CREATE TRIGGER akseptpunkt_ingen_truncate BEFORE TRUNCATE ON modulaksept_punkt
    FOR EACH STATEMENT EXECUTE FUNCTION modulregister_append_only();

-- Punktradene bærer `kilde_ref` — artefakt-UUIDer, CI-kjøringer,
-- evidensfilnavn — og har INGEN tenantkolonne å filtrere på. Da er det
-- ingen tenantport å skrive; raden er evidens for eier- og driftsveien,
-- og forespørselsveien leser aldri denne tabellen (Codex P1, runde 14).
-- Uten en policy som treffer, ser ingen annen rolle noen rad: RLS er
-- default-deny, og det er nøyaktig svaret her.
ALTER TABLE modulaksept_punkt ENABLE ROW LEVEL SECURITY;
CREATE POLICY modulaksept_punkt_eier ON modulaksept_punkt
    USING (CURRENT_USER = 'disponit_modul_eier');

-- ------------------------------------------------------------
-- 5b. Claim-stoppets MÅLETID (Codex P1, #117 runde 21).
--
-- `min_ventetid_s` bodde bare i `manifestskjema.py` og i drillskriptets
-- egen løkke — altså i filvalidatoren og i den som skriver fila. Basen
-- kjente ingen varighet, så `claim_stopp_ok` krevde bare at
-- rullbakkoppdraget lå MELLOM de to registerovergangene. En kaller med
-- `disponit_modules_admin` kunne derfor bytte til rullbakken, bestille
-- og fullføre et ekte oppdrag, og bytte videre til kandidaten på et par
-- sekunder: alle tidspredikatene passerte, og en uforanderlig grønn
-- drillrad — og aksepten som FK-refererer den — sto uten at claim-
-- stoppet noen gang var observert. Et claim-stopp er en VARIGHET; måles
-- den ikke, er «den drenerte claimet ingenting» bare en setning om et
-- øyeblikk der ingen rakk å claime noe uansett.
--
-- Terskelen bor her, i basen, fordi det er her den håndheves. Den er en
-- funksjon og ikke et litteral i porten, slik at drillsonden og prøvene
-- kan lese NØYAKTIG det tallet porten regner med — og
-- `test_ventetidsterskelen_er_den_samme_i_base_og_manifestskjema`
-- binder den til `manifestskjema.GRENSER['rollback-m56-v1']`, så de to
-- ikke kan gli fra hverandre i stillhet.
-- ------------------------------------------------------------
--
-- Denne ene står MED default-EXECUTE til PUBLIC, i motsetning til
-- definerne nederst i fila som revokes i eiervinduet: den leser ingen
-- rad, skriver ingenting og avslører intet annet enn terskelen porten
-- offentlig håndhever. Å lese kravet er ikke en fullmakt — sonden,
-- akseptskriptet og prøvene skal alle kunne se NØYAKTIG det tallet.
CREATE OR REPLACE FUNCTION moduldrill_min_ventetid_s()
RETURNS NUMERIC LANGUAGE sql IMMUTABLE AS $$ SELECT 20.0::NUMERIC $$;

-- …og SAMME GREP for de rene utfallene (Codex P2, #117 runde 22).
-- `manifestskjema.KRAVGRENSER` godtok `avbrutt` som et rent inflight-
-- utfall. Basen har aldri kjent det: `rene_utfall_ok` regnes bare for
-- `oppdrag.status IN ('utfort','feilet')`, og drillsonden venter bare på
-- de to. Filvalidatoren kunne derfor godkjenne — og manifestbindingen
-- merke rullbakkpunktet grønt på — evidens akseptbasen ALDRI kan
-- kvalifisere: en drill som består i fila og faller i basen.
-- Settet bor her, der det håndheves, og
-- `test_de_rene_utfallene_er_de_samme_i_base_og_manifestskjema` binder
-- det til filvalidatoren, så de to ikke kan gli fra hverandre i
-- stillhet. Samme offentlighet som terskelen over: å lese kravet er
-- ingen fullmakt.
CREATE OR REPLACE FUNCTION moduldrill_rene_utfall()
RETURNS TEXT[] LANGUAGE sql IMMUTABLE
AS $$ SELECT ARRAY['utfort', 'feilet']::TEXT[] $$;

-- ------------------------------------------------------------
-- 6. Funksjonene — modul_eier-eide definere, EXECUTE kun til
--    disponit_modules_admin (014-mønsteret). INSERT på tabellene er
--    eierens/migrators særrettighet; ingen andre roller får DML.
-- ------------------------------------------------------------
-- Utfallene er IKKE parametre (Codex P1, #117 runde 5): kalleren oppgir
-- HVA drillen ble målt på — tenanten og de tre oppdragene — og funksjonen
-- måler selv i `oppdrag`/`artefakt`. En kaller med `disponit_modules_admin`
-- kan derfor ikke lenger skrive en grønn drillrad; han må ha oppdrag som
-- faktisk bærer utfallene, og dem lager bare arbeid.
CREATE OR REPLACE FUNCTION registrer_moduldrill(
    p_modul_id TEXT, p_miljo TEXT, p_drillet TEXT, p_rullback TEXT,
    p_kandidat TEXT, p_tenant TEXT, p_inflight_oppdrag BIGINT,
    p_rullback_oppdrag BIGINT, p_kandidat_oppdrag BIGINT,
    p_module_epoch BIGINT, p_artefakt_sha TEXT, p_nokkel TEXT,
    p_aktor TEXT, p_utfort_ts TIMESTAMPTZ)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id BIGINT; v_drillet_digest TEXT; v_kandidat_digest TEXT;
        v_epoch BIGINT; v_livslop TEXT; v_forrige_tenant TEXT;
        v_status TEXT; v_kvittering BOOLEAN; v_funnet INT;
        v_claim_stopp BOOLEAN; v_rene_utfall BOOLEAN; v_tilbake BOOLEAN;
        v_rull_ts TIMESTAMPTZ; v_kand_ts TIMESTAMPTZ; v_vindu BOOLEAN;
        v_kver INT; v_khash TEXT;
        v_claimet_av_drillet BOOLEAN; v_claimet_av_rullback BOOLEAN;
        v_claimet_av_kandidat BOOLEAN;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('modul:' || p_modul_id, 0));
    -- SP-2: samme nøkkel → samme rad, aldri to. Avvikende innhold på
    -- samme nøkkel er en programfeil og skal høres.
    SELECT drill_id INTO v_id FROM public.moduldrill WHERE nokkel = p_nokkel;
    IF FOUND THEN
        PERFORM 1 FROM public.moduldrill
         WHERE nokkel = p_nokkel AND modul_id = p_modul_id
           AND miljo = p_miljo AND drillet_release = p_drillet
           AND rullback_release = p_rullback
           AND akseptkandidat_release = p_kandidat
           -- Utfallene måles nedenfor og er ikke lenger kallerens; det
           -- MATERIELLE i et replay-kall er derfor hva drillen ble målt
           -- på: tenanten, de tre oppdragene og bytene raden hviler på.
           -- Samme nøkkel med andre oppdrag er en annen drill.
           AND tenant = p_tenant
           AND inflight_oppdrag = p_inflight_oppdrag
           AND rullback_oppdrag = p_rullback_oppdrag
           AND kandidat_oppdrag = p_kandidat_oppdrag
           AND epoch_snapshot = p_module_epoch
           AND artefakt_sha256 = lower(p_artefakt_sha)
           -- Måletidspunktet er like materielt som utfallene: samme
           -- nøkkel med en ANNEN drillkjørings tidsstempel er to
           -- kjøringer, ikke en replay.
           AND utfort_ts = p_utfort_ts;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'registrer_moduldrill: nøkkel % gjenbrukt med'
                ' annet innhold', p_nokkel
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN v_id;
    END IF;
    -- Tilstandene drillen ETTERLATER: den drillede er drenert (rullingen
    -- konsumerte den — livsløpet er enveis), kandidaten er den som
    -- faktisk kjører. Kandidat claiming er også akseptens forutsetning.
    SELECT livslop INTO v_livslop FROM public.moduldeployment
     WHERE modul_id = p_modul_id AND miljo = p_miljo
       AND release_id = p_drillet;
    IF v_livslop IS DISTINCT FROM 'draining' THEN
        RAISE EXCEPTION 'registrer_moduldrill: drillet release %/% er %,'
            ' ventet draining (drillen skal ha konsumert den)',
            p_modul_id, p_drillet, coalesce(v_livslop, '<mangler>')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Kandidatens kontraktlinje er DRILLENS linje (Codex P1, #117 runde
    -- 17). `en_claiming_per_kontrakt` fører én linje per (modul, miljø,
    -- kontraktversjon, kontrakt_hash), så flere kan stå claiming
    -- samtidig, helt lovlig — og da må målingene under bindes til én av
    -- dem, ellers kan overganger fra én slekt pares med oppdrag fra en
    -- annen.
    SELECT livslop, kontraktversjon, kontrakt_hash
      INTO v_livslop, v_kver, v_khash
      FROM public.moduldeployment
     WHERE modul_id = p_modul_id AND miljo = p_miljo
       AND release_id = p_kandidat;
    IF v_livslop IS DISTINCT FROM 'claiming' THEN
        RAISE EXCEPTION 'registrer_moduldrill: kandidat %/% er %, ventet'
            ' claiming (aksepten binder raden som faktisk kjører)',
            p_modul_id, p_kandidat, coalesce(v_livslop, '<mangler>')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- …og den drillede og rullbakken må stå på SAMME linje. En drill som
    -- ruller mellom kontraktslekter er ingen rullbakk av det som ble
    -- drillet.
    IF NOT EXISTS (SELECT 1 FROM public.moduldeployment d
                    WHERE d.modul_id = p_modul_id AND d.miljo = p_miljo
                      AND d.release_id IN (p_drillet, p_rullback)
                      AND d.kontraktversjon = v_kver
                      AND d.kontrakt_hash = v_khash
                    HAVING count(*) = 2) THEN
        RAISE EXCEPTION 'registrer_moduldrill: %, % og % står ikke på samme'
            ' kontraktlinje (v%/%…) — en drill måler ÉN linje, og'
            ' overganger fra en annen slekt hører ikke til denne',
            p_drillet, p_rullback, p_kandidat, v_kver, left(v_khash, 12)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- A1-digestporten: kandidatens bytes SKAL være de drillede bytene.
    SELECT artifact_digest INTO v_drillet_digest FROM public.modulrelease
     WHERE modul_id = p_modul_id AND release_id = p_drillet;
    SELECT artifact_digest INTO v_kandidat_digest FROM public.modulrelease
     WHERE modul_id = p_modul_id AND release_id = p_kandidat;
    IF v_drillet_digest IS DISTINCT FROM v_kandidat_digest THEN
        RAISE EXCEPTION 'registrer_moduldrill: kandidatens digest (%) er'
            ' ikke den drillede (%) — aksepterte bytes må være drillede'
            ' bytes', left(v_kandidat_digest, 12), left(v_drillet_digest, 12)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT module_epoch INTO v_epoch FROM public.modulhode
     WHERE modul_id = p_modul_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'registrer_moduldrill: ukjent modul %', p_modul_id
            USING ERRCODE = 'no_data_found';
    END IF;
    -- Codex' P2 på PR #117 (runde 5): `epoch_snapshot` ble SNAPSHOTTET
    -- her, ved registreringen, mens drillartefaktets egen
    -- `oppsett.module_epoch` aldri ble sendt inn eller sammenlignet.
    -- Fencing-generasjonen er ikke pynt: den er konteksten claim-stoppet
    -- ble målt i, og en nødstopp eller reaktivering mellom drill og
    -- aksept flytter den. Raden kunne derfor påstå en ANNEN generasjon
    -- enn artefaktet som målte drillen — og skjule et misdannet bevis i
    -- stedet for å avvise det. Nå må artefaktet si hvilken generasjon det
    -- målte i, og den må være den levende.
    IF p_module_epoch IS DISTINCT FROM v_epoch THEN
        RAISE EXCEPTION 'registrer_moduldrill: drillen ble målt i epoch'
            ' %, men modulen står i epoch % — fencing-generasjonen har'
            ' flyttet seg siden målingen, og drillen gjelder da en annen'
            ' kontekst enn den som registreres',
            p_module_epoch, v_epoch
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Drillen ble utført FØR den ble registrert. Et tidsstempel fram i
    -- tid er ikke en måling, det er en påstand om framtiden — og
    -- CHECK-en under ville uansett stoppet raden; her får den et navn.
    IF p_utfort_ts IS NULL OR p_utfort_ts > now() THEN
        RAISE EXCEPTION 'registrer_moduldrill: utført-tidspunktet % er'
            ' tomt eller fram i tid — drillen skal bære sin EGEN måletid',
            p_utfort_ts USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- ------------------------------------------------------------
    -- DRILLVINDUET: de to overgangene drillen faktisk gjorde.
    --
    -- Codex' P1 på PR #117 (runde 16): utfallene ble målt som REN
    -- EKSISTENS av artefakter per release, uten et eneste ledd som
    -- knyttet oppdragene til rullingen. Har de tre releasene håndtert
    -- vanlig arbeid på noe tidspunkt i sine livsløp — og det har en
    -- release som har vært claiming — kunne et direkte
    -- `registrer_moduldrill`-kall plukke et signert, fullført oppdrag fra
    -- den drillede og promoterte oppdrag fra rullbakken og kandidaten, og
    -- få alle tre flaggene grønne uten at noe kappløp, noe claim-stopp
    -- eller noen rulling hadde funnet sted. Predikatene sa «det finnes
    -- arbeid på denne releasen», mens påstanden er «dette arbeidet krysset
    -- rullingen».
    --
    -- `bytt_release` (014) skriver én `releasebytte`-hendelse per
    -- overgang, med tidspunkt. De to overgangene drillen består av — inn
    -- på rullbakken og inn på kandidaten — gir derfor drillens egne to
    -- skillelinjer, og oppdragene måles MOT dem:
    --
    --   (b)  inflight: bestilt FØR rullingen, terminal ETTER den — det er
    --        nettopp «oppdraget krysset byttet».
    --   (a+b2) rullbakk: bestilt ETTER rullingen (mens den drillede
    --        drenerte) og ferdig FØR kandidaten overtok — claim-stoppet og
    --        overtakelsen er samme oppdrag, i det vinduet.
    --   (c)  kandidat: bestilt ETTER kandidatens registerbytte, terminal
    --        etterpå — arbeidet lå og ventet på nøyaktig den som overtok.
    --
    -- Alt sammen innenfor drillens egen måletid (`p_utfort_ts`), så et
    -- gammelt oppdrag ikke kan lånes inn i en ny drills vindu.
    --
    -- Mangler overgangene, er flaggene FALSE — ikke en exception. Et
    -- register uten drillens overganger bærer ingen drill å måle, og en
    -- rød drillrad er nettopp det riktige svaret: aksepten står på FK-en
    -- mot de tre grønne utfallene.
    -- ------------------------------------------------------------
    --
    -- OG OVERGANGEN MÅ VÆRE DEN SOM DRENERTE FORGJENGEREN (Codex P1,
    -- #117 runde 17). De to oppslagene fant sine `releasebytte`-
    -- hendelser hver for seg, og beviste aldri at byttet INN på
    -- rullbakken var det som drenerte den drillede. En modul med flere
    -- kontraktslekter — eller en eldre draining-release med overlappende
    -- arbeid — kunne derfor pare den drillede releasen og dens
    -- inflight-oppdrag fra én slekt med rullbakk- og kandidatoverganger
    -- fra en annen: alle tidspredikatene under kunne passere uten at
    -- noen rullbakk FRA den claimede drillede releasen hadde skjedd.
    --
    -- `bytt_release` skriver de to hendelsene i SAMME transaksjon:
    -- `drainet_ved_bytte` for den gamle, så `releasebytte` for den nye.
    -- `now()` er transaksjonsstabil, så de deler `ts` eksakt, og
    -- identiteten er stigende per INSERT, så dreneringen står FØR byttet.
    -- Paret er derfor selve overgangen — ikke to hendelser som tilfeldig
    -- fantes — og begge leddene bindes til drillens kontraktlinje.
    SELECT max(b.ts) INTO v_rull_ts
      FROM public.modulregister_hendelse b
      JOIN public.modulregister_hendelse d
        ON d.modul_id = b.modul_id AND d.miljo = b.miljo
       AND d.kontraktversjon = b.kontraktversjon
       AND d.kontrakt_hash = b.kontrakt_hash
       AND d.hendelse = 'drainet_ved_bytte' AND d.release_id = p_drillet
       AND d.ts = b.ts AND d.id < b.id
     WHERE b.modul_id = p_modul_id AND b.miljo = p_miljo
       AND b.hendelse = 'releasebytte' AND b.release_id = p_rullback
       AND b.kontraktversjon = v_kver AND b.kontrakt_hash = v_khash;
    SELECT max(b.ts) INTO v_kand_ts
      FROM public.modulregister_hendelse b
      JOIN public.modulregister_hendelse d
        ON d.modul_id = b.modul_id AND d.miljo = b.miljo
       AND d.kontraktversjon = b.kontraktversjon
       AND d.kontrakt_hash = b.kontrakt_hash
       AND d.hendelse = 'drainet_ved_bytte' AND d.release_id = p_rullback
       AND d.ts = b.ts AND d.id < b.id
     WHERE b.modul_id = p_modul_id AND b.miljo = p_miljo
       AND b.hendelse = 'releasebytte' AND b.release_id = p_kandidat
       AND b.kontraktversjon = v_kver AND b.kontrakt_hash = v_khash;
    v_vindu := v_rull_ts IS NOT NULL AND v_kand_ts IS NOT NULL
               AND v_rull_ts < v_kand_ts AND v_kand_ts <= p_utfort_ts;
    -- ------------------------------------------------------------
    -- UTFALLENE MÅLES (Codex P1, #117 runde 5).
    --
    -- `oppdrag` og `artefakt` står med FORCE ROW LEVEL SECURITY og
    -- tenant-policy; definerens rolle er ikke tabelleier, så policyen
    -- gjelder også her. Tenantkonteksten settes derfor eksplisitt til
    -- den drillen ble målt i, og legges tilbake etterpå — funksjonen
    -- skal ikke etterlate kallerens sesjon i et annet skop enn den fant
    -- den i.
    -- ------------------------------------------------------------
    v_forrige_tenant := current_setting('disponit.tenant', true);
    PERFORM set_config('disponit.tenant', p_tenant, true);
    SELECT count(*) INTO v_funnet FROM public.oppdrag o
     WHERE o.tenant = p_tenant
       AND o.id IN (p_inflight_oppdrag, p_rullback_oppdrag,
                    p_kandidat_oppdrag);
    IF v_funnet <> 3 THEN
        RAISE EXCEPTION 'registrer_moduldrill: fant % av 3 drilloppdrag'
            ' for tenant % — utfallene måles på oppdragene, og oppdrag'
            ' som ikke finnes har ingen utfall', v_funnet, p_tenant
            USING ERRCODE = 'no_data_found';
    END IF;
    IF EXISTS (SELECT 1 FROM public.oppdrag o
                WHERE o.tenant = p_tenant
                  AND o.id IN (p_inflight_oppdrag, p_rullback_oppdrag,
                               p_kandidat_oppdrag)
                  AND o.eiermodul IS DISTINCT FROM p_modul_id) THEN
        RAISE EXCEPTION 'registrer_moduldrill: minst ett drilloppdrag'
            ' eies av en annen modul enn % — en annen moduls arbeid er'
            ' ingen drill av denne', p_modul_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- ------------------------------------------------------------
    -- HVER LEDD MÅ VÆRE CLAIMET AV DEN RELEASEN LEDDET HANDLER OM
    -- (Codex P1, #117 runde 20).
    --
    -- Tidsvinduet over sier at oppdraget KRYSSET rullingen; det sier
    -- ikke hvem som hadde det. For `utfort` bar det promoterte
    -- artefaktet releasen, men `feilet` er også et rent utfall, og der
    -- krevde artefaktlikheten bare at det IKKE fantes et promotert
    -- artefakt på den drillede — fravær av en binding, ikke en binding.
    -- Et vanlig oppdrag bestilt før rullingen og feilet etterpå av
    -- rullbakk- eller kandidatarbeideren passerte derfor som den
    -- drillede releasens inflight-utfall.
    --
    -- `claim_neste_oppdrag` stempler nå claim-releasen på raden (§0), og
    -- de tre leddene måles mot den: inflight tilhører den DRILLEDE,
    -- rullbakkleddet RULLBAKKEN, kandidatleddet KANDIDATEN. Sporet er
    -- claim-funksjonens eget — ingen annen rolle kan skrive det — så
    -- dette er den ene identiteten drillen faktisk hviler på, ikke en
    -- slutning fra et fravær. Miljøet er med: samme release-ID i et
    -- annet miljø er en annen deployment.
    -- ------------------------------------------------------------
    SELECT EXISTS (SELECT 1 FROM public.oppdrag o
                    WHERE o.tenant = p_tenant AND o.id = p_inflight_oppdrag
                      AND o.claim_release_id = p_drillet
                      AND o.claim_miljo = p_miljo),
           EXISTS (SELECT 1 FROM public.oppdrag o
                    WHERE o.tenant = p_tenant AND o.id = p_rullback_oppdrag
                      AND o.claim_release_id = p_rullback
                      AND o.claim_miljo = p_miljo),
           EXISTS (SELECT 1 FROM public.oppdrag o
                    WHERE o.tenant = p_tenant AND o.id = p_kandidat_oppdrag
                      AND o.claim_release_id = p_kandidat
                      AND o.claim_miljo = p_miljo)
      INTO v_claimet_av_drillet, v_claimet_av_rullback,
           v_claimet_av_kandidat;
    -- (a)+(b2) claim-stopp: oppdraget som ble bestilt mens den drillede
    -- releasen drenerte, ble IKKE tatt av den — og ble tatt av
    -- rullbakken etter at hun ble bootet. Det andre leddet er det som
    -- skiller «den gamle sluttet å claime» fra «det gikk an å rulle
    -- tilbake»: uten det er claim-stoppet bare fravær av arbeid.
    --
    -- …OG OPPDRAGET MÅ LIGGE I VINDUET (Codex P1, #117 runde 16): bestilt
    -- etter rullingen, gjort ferdig før kandidaten overtok. Et hvilket
    -- som helst gammelt oppdrag med et promotert artefakt på rullbakken
    -- ville ellers holdt — og et claim-stopp som ikke ble målt MENS den
    -- drillede drenerte, er ikke et claim-stopp.
    v_claim_stopp :=
        v_vindu
        -- …og rullbakken må være den som CLAIMET det (runde 20): et
        -- promotert artefakt med rullbakkens release-ID er skrevet av
        -- arbeideren, mens claim-sporet er portens eget.
        AND v_claimet_av_rullback
        AND NOT EXISTS (SELECT 1 FROM public.artefakt a
                     WHERE a.tenant = p_tenant
                       AND a.oppdrag_id = p_rullback_oppdrag
                       AND a.release_id = p_drillet)
        AND EXISTS (SELECT 1 FROM public.artefakt a
                     WHERE a.tenant = p_tenant
                       AND a.oppdrag_id = p_rullback_oppdrag
                       AND a.release_id = p_rullback
                       AND a.tilstand = 'promotert')
        AND EXISTS (SELECT 1 FROM public.oppdrag o
                     WHERE o.tenant = p_tenant
                       AND o.id = p_rullback_oppdrag
                       AND o.opprettet > v_rull_ts
                       AND o.opprettet < v_kand_ts
                       AND o.status_ts > o.opprettet
                       AND o.status_ts < v_kand_ts)
        -- …OG DEN DRENERTE MÅ HA LATT DET LIGGE LENGE NOK (Codex P1,
        -- runde 21). Vinduet over sier at oppdraget lå INNENFOR
        -- rullingen, ikke hvor lenge. Måletiden er tiden fra oppdraget
        -- ble bestilt (etter `v_rull_ts`, altså etter at den drillede
        -- ble drenert) til det FØRSTE claimet — nøyaktig strekket der
        -- en levende, claimende forgjenger ville tatt raden. Sporet er
        -- claim-portens eget (§0), write-once, så verken kjøretiden
        -- eller deployfullmakten kan strekke det.
        --
        -- `>=`, ikke `>`: terskelen er «i minst så lenge», som i
        -- `manifestskjema`. Og claimet må ligge FØR kandidatbyttet:
        -- ventes det ut etter at kandidaten overtok, er det ikke den
        -- drenerte releasens claim-stopp lenger.
        AND EXISTS (SELECT 1 FROM public.oppdrag o
                     WHERE o.tenant = p_tenant
                       AND o.id = p_rullback_oppdrag
                       AND o.forste_claim_ts IS NOT NULL
                       AND o.forste_claim_ts < v_kand_ts
                       AND o.forste_claim_ts - o.opprettet >=
                           (public.moduldrill_min_ventetid_s() || ' seconds')
                               ::INTERVAL);
    -- (b) rent utfall (SP-3): terminalt, signert kvittering, og utfallet
    -- STEMMER med evidensen — et `utfort` uten promotert artefakt og et
    -- ikke-`utfort` MED er begge falske verdikter. Motsigelsen regnes
    -- her, av radene selv, ikke av et tall i et artefakt.
    --
    -- SIGNERT MÅLES PÅ SIGNATUREN, IKKE PÅ NYTTELASTEN (Codex P1, #117).
    -- Den forrige formen leste `kvittering IS NOT NULL` og kalte det
    -- «signert kvittering». Men signaturen er en EGEN kolonne
    -- (`oppdrag.kvittering_signatur`, 005), og `oppdrag`-skjemaet lar de
    -- to variere fritt: kolonnelåsen gjør kvitteringsfeltene uforanderlige
    -- ETTER at de er satt, og statusmaskinen sier ingenting om at en
    -- nyttelast må ha en signatur ved siden av seg. Kjøretidsrollen har
    -- direkte `UPDATE` på raden. Én `UPDATE oppdrag SET kvittering='{}'`
    -- uten signaturkolonne ga altså `rene_utfall_ok = true`, og aksepten —
    -- som er UFORANDERLIG når den først er skrevet — påsto for alltid at
    -- drillen endte i en signert kvittering det aldri fantes en signatur
    -- for. Det er nøyaktig den formen SP-3 finnes for å utelukke.
    --
    -- Signaturen kan ikke verifiseres kryptografisk her (nøklene bor i
    -- API-et, som er den ENESTE veien som verifiserer en konvolutt før den
    -- lagres). Det porten kan gjøre, er å kreve HELE avtrykket den veien
    -- setter igjen, i stedet for det ene feltet enhver skriver kan finne
    -- på: signaturen må stå i sin egen kolonne, den må ikke være tom, den
    -- må være IDENTISK med signaturverdien i konvolutten som ligger lagret
    -- (`kvittering_signatur` ER `kvittering->signatur->>verdi`, hentet ut
    -- av verifiseringen selv — spriker de, kommer raden ikke derfra), og
    -- `resultathash` må være satt, siden veien skriver alle tre i samme
    -- `UPDATE`.
    --
    -- …MEN DE TRE FELTENE EIES AV DEN SAMME SKRIVEREN (Codex P1, #117
    -- runde 15). Kjøretidsrollen har direkte `UPDATE` på oppdragsraden —
    -- det er den API-et selv bruker — og et `UPDATE oppdrag SET
    -- kvittering='{"signatur":{"verdi":"x"}}', kvittering_signatur='x',
    -- resultathash='x'` oppfyller hele likheten over uten at noen
    -- konvolutt noen gang er verifisert. Feltenighet mellom kolonner én
    -- rolle kan skrive fritt, er ikke et bevis; det er en form som er
    -- litt mer arbeid å fylle ut.
    --
    -- Derfor kreves AVTRYKKET utenfor raden: kvitteringskapabiliteten for
    -- oppdraget må være BRENT, med nøyaktig den `resultathash`-en raden
    -- bærer. `kvitteringskapabiliteter` (005) står `REVOKE ALL ... FROM
    -- PUBLIC` uten et eneste tabellgrant — ingen rolle skriver den
    -- direkte. Den fylles bare av `utsted_kvitteringskapabilitet` (krever
    -- en claim kalleren HOLDER, med matchende `owner_claim_id`/
    -- `owner_generation` på et `plukket` oppdrag) og brennes bare av
    -- `bruk_kvitteringskapabilitet`, som API-et kaller FØRST etter at
    -- `attestering.verifiser` har godtatt signaturen mot nøkkelregisteret.
    -- Hashen er uforanderlig når den først er festet (statusmaskinen i
    -- 005), og `brukt` er engangs.
    --
    -- Det gjør ikke basen til en signaturverifiserer — HMAC-hemmelighetene
    -- bor i API-et, og ingen SQL kan regne dem ut på nytt. Men det flytter
    -- kravet fra «tre felter er enige» til «verifiseringsveien har
    -- FAKTISK vært her, på dette oppdraget, med denne hashen», og det er
    -- det sterkeste sporet den veien etterlater i basen.
    --
    -- Selve predikatet bor i `maal_rent_utfall` (Codex P1, runde 17):
    -- drillsonden og sjekklisten måler mot NØYAKTIG denne, i stedet for
    -- hver sin kopi bak en fullmakt de ikke har.
    SELECT o.status INTO v_status
      FROM public.oppdrag o
     WHERE o.tenant = p_tenant AND o.id = p_inflight_oppdrag;
    v_kvittering := public.maal_rent_utfall(p_tenant, p_inflight_oppdrag);
    v_rene_utfall := v_status = ANY (public.moduldrill_rene_utfall())
        AND v_kvittering
        -- DEN DRILLEDE RELEASEN HADDE DET INNE (Codex P1, runde 20).
        -- Uten dette leddet er `feilet` et utfall uten eier: ingen
        -- artefakt bærer releasen, og «det finnes ikke et promotert
        -- artefakt på den drillede» er sant for alt arbeid i verden.
        AND v_claimet_av_drillet
        AND ((v_status = 'utfort') = EXISTS (
                SELECT 1 FROM public.artefakt a
                 WHERE a.tenant = p_tenant
                   AND a.oppdrag_id = p_inflight_oppdrag
                   AND a.release_id = p_drillet
                   AND a.tilstand = 'promotert'))
        -- …og oppdraget må ha KRYSSET rullingen (Codex P1, #117 runde
        -- 16): bestilt før byttet, terminalt etter det, innenfor
        -- drillens måletid. Et rent utfall som lå ferdig før rullingen i
        -- det hele tatt ble fyrt, måler ikke SP-3.
        AND v_vindu
        AND EXISTS (SELECT 1 FROM public.oppdrag o
                     WHERE o.tenant = p_tenant
                       AND o.id = p_inflight_oppdrag
                       AND o.opprettet < v_rull_ts
                       AND o.status_ts > v_rull_ts
                       AND o.status_ts <= p_utfort_ts);
    -- (c) fram igjen: kandidaten plukket sitt eget oppdrag og promoterte
    -- — og oppdraget ble bestilt ETTER kandidatens registerbytte, så det
    -- lå og ventet på nøyaktig den som overtok (Codex P1, #117 runde 16).
    v_tilbake := v_vindu
        -- …og kandidaten må ha CLAIMET det (runde 20): overtakelsen er
        -- at nettopp hun tok raden, ikke at et artefakt bærer navnet.
        AND v_claimet_av_kandidat
        AND EXISTS (SELECT 1 FROM public.artefakt a
                          WHERE a.tenant = p_tenant
                            AND a.oppdrag_id = p_kandidat_oppdrag
                            AND a.release_id = p_kandidat
                            AND a.tilstand = 'promotert')
        AND EXISTS (SELECT 1 FROM public.oppdrag o
                     WHERE o.tenant = p_tenant
                       AND o.id = p_kandidat_oppdrag
                       AND o.opprettet > v_kand_ts
                       AND o.status_ts > o.opprettet
                       AND o.status_ts <= p_utfort_ts);
    PERFORM set_config('disponit.tenant',
                       coalesce(v_forrige_tenant, ''), true);
    INSERT INTO public.moduldrill (modul_id, miljo, drillet_release,
        rullback_release, akseptkandidat_release, epoch_snapshot,
        digest_snapshot, tenant, inflight_oppdrag, rullback_oppdrag,
        kandidat_oppdrag, artefakt_sha256, claim_stopp_ok, rene_utfall_ok,
        tilbake_ok, nokkel, aktor, utfort_ts)
    VALUES (p_modul_id, p_miljo, p_drillet, p_rullback, p_kandidat,
            v_epoch, v_kandidat_digest, p_tenant, p_inflight_oppdrag,
            p_rullback_oppdrag, p_kandidat_oppdrag, lower(p_artefakt_sha),
            v_claim_stopp, v_rene_utfall, v_tilbake,
            p_nokkel, p_aktor, p_utfort_ts)
    RETURNING drill_id INTO v_id;
    INSERT INTO public.modulregister_hendelse (modul_id, hendelse,
        release_id, miljo, module_epoch, aktor, detalj)
    VALUES (p_modul_id, 'rollback_drill', p_kandidat, p_miljo, v_epoch,
            p_aktor, jsonb_build_object(
                'drill_id', v_id, 'drillet', p_drillet,
                'rullback', p_rullback,
                'claim_stopp_ok', v_claim_stopp,
                'rene_utfall_ok', v_rene_utfall,
                'tilbake_ok', v_tilbake,
                'artefakt_sha256', lower(p_artefakt_sha),
                'utfort_ts', p_utfort_ts));
    RETURN v_id;
END $$;

-- ÉN MÅLING, ETT STED (Codex P1, #117 runde 17).
--
-- «Rent utfall» — avtrykket kvitteringsveien setter igjen — ble regnet
-- ut tre steder: her i `registrer_moduldrill`, i drillsondens
-- `_kvittering` og i sjekklistens `_kvittering_signert`. De to siste
-- spurte basen DIREKTE, som `disponit_migrator`, og
-- `kvitteringskapabiliteter` (005) er `REVOKE ALL ... FROM PUBLIC` med
-- kolonnegrant bare til `disponit_modul_eier`. Migrators medlemskap i
-- eierrollen er `WITH INHERIT FALSE`, så spørringen dør på «permission
-- denied» — og i drillen skjer det ETTER at rullingen har drenert den
-- levende deploymenten og brukt opp rullbakk-ID-en, altså i en enveis
-- måling som ikke kan kjøres om igjen.
--
-- Tre kopier av samme predikat er dessuten tre steder å drifte fra
-- hverandre. Målingen bor derfor HER, bak den fullmakten den trenger, og
-- alle tre kaller den samme funksjonen: sonden måler nøyaktig det
-- aksepten senere regner med.
CREATE OR REPLACE FUNCTION maal_rent_utfall(p_tenant TEXT,
                                            p_oppdrag BIGINT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_forrige TEXT; v_svar BOOLEAN;
BEGIN
    -- `oppdrag`/`kvitteringskapabiliteter` står med FORCE RLS og
    -- tenantpolicy; definerens rolle eier dem ikke, så konteksten settes
    -- eksplisitt og legges tilbake — funksjonen skal ikke etterlate
    -- kallerens sesjon i et annet skop enn den fant den i.
    v_forrige := current_setting('disponit.tenant', true);
    PERFORM set_config('disponit.tenant', p_tenant, true);
    SELECT o.kvittering IS NOT NULL
       AND o.kvittering_signatur IS NOT NULL
       AND pg_catalog.btrim(o.kvittering_signatur) <> ''
       AND o.kvittering_signatur
           IS NOT DISTINCT FROM (o.kvittering -> 'signatur' ->> 'verdi')
       AND o.resultathash IS NOT NULL
       AND EXISTS (SELECT 1 FROM public.kvitteringskapabiliteter k
                    WHERE k.tenant = o.tenant
                      AND k.oppdrag_id = o.id
                      AND k.status = 'brukt'
                      AND k.resultathash IS NOT NULL
                      AND k.resultathash IS NOT DISTINCT FROM o.resultathash)
      INTO v_svar
      FROM public.oppdrag o
     WHERE o.tenant = p_tenant AND o.id = p_oppdrag;
    PERFORM set_config('disponit.tenant', coalesce(v_forrige, ''), true);
    RETURN coalesce(v_svar, false);
END $$;

-- Attesten: hva veien som spurte GitHub SÅ. Den skrives av
-- `m56-aksept.py` rett etter at `verifiser_ci_kjoring` har godtatt
-- kjøringen, med verdiene svaret bar — ikke med det aksepten trenger.
-- SP-2: samme løpenummer med annet innhold er ikke en replay, det er to
-- motstridende referater av én kjøring, og det skal høres.
--
-- ATTESTANTEN ER EN ANNEN ENN AKSEPTØREN (Codex P1, #117 runde 19).
-- Runde 16 flyttet invariantpunktene fra kallerens to strenger til en
-- immutabel attestrad — men grantet lå på `disponit_modules_admin`,
-- nøyaktig den rollen som også kaller `aksepter_moduldeployment`. Da var
-- referatet fortsatt kallerens eget: én og samme fullmakt kunne finne på
-- et løpenummer, skrive «ci.yml kjørte grønt på en push til main for
-- commit X», og deretter la aksepten hvile på sin egen påstand — også
-- for utslippspunktet `m56-aksept.py::UMAALTE` uttrykkelig nekter å
-- måle. En attest som kan skrives av den som trenger den, er ingen
-- attest; den er en gjentakelse.
--
-- Basen kan ikke autentisere GitHub. Det den KAN, er å kreve at
-- referatet og aksepten kommer fra to FORSKJELLIGE fullmakter: attesten
-- skrives av registerets egen eierrolle (`disponit_modul_eier`, NOLOGIN,
-- kun tilgjengelig via eksplisitt, sporbar `SET ROLE` for migrator),
-- mens aksepten skrives av deployfullmakten. Den som holder
-- `disponit_modules_admin` — og bare den — kan fra nå ikke skrive noen
-- attest, og dermed heller ingen aksept som hviler på et CI-punkt.
-- Grantene under er hele porten; se GRANT-blokken ved funksjonseierne.
--
-- Og raden bærer den AUTENTISERTE identiteten: `attestert_av` settes fra
-- `session_user` her inne, ikke fra en parameter. `aktor` er fortsatt
-- kallerens etikett — den forteller hvilket skript som gikk, ikke hvem
-- som var logget inn, og de to fakta har hver sin kolonne.
CREATE OR REPLACE FUNCTION attester_ci_kjoring(
    p_ci_run TEXT, p_arbeidsflyt TEXT, p_hendelse TEXT, p_gren TEXT,
    p_konklusjon TEXT, p_hode_sha TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM 1 FROM public.ci_kjoringsattest WHERE ci_run = p_ci_run;
    IF FOUND THEN
        PERFORM 1 FROM public.ci_kjoringsattest
         WHERE ci_run = p_ci_run AND arbeidsflyt = p_arbeidsflyt
           AND hendelse = p_hendelse AND gren = p_gren
           AND konklusjon = p_konklusjon
           AND hode_sha = lower(p_hode_sha);
        IF NOT FOUND THEN
            RAISE EXCEPTION 'attester_ci_kjoring: kjøring % er alt attestert'
                ' med et annet utfall — én kjøring har ett utfall, og et'
                ' referat som spriker fra det lagrede er en programfeil',
                p_ci_run USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN;
    END IF;
    INSERT INTO public.ci_kjoringsattest (ci_run, arbeidsflyt, hendelse,
        gren, konklusjon, hode_sha, aktor, attestert_av)
    VALUES (p_ci_run, p_arbeidsflyt, p_hendelse, p_gren, p_konklusjon,
            lower(p_hode_sha), p_aktor, session_user);
END $$;

-- Referatet fra veien som LESTE evidensfilen (Codex P1, #117 runde 19).
-- Skrives av `m56-aksept.py` etter at `verifiser_kilde` har hashet
-- råfilen og `les_bundet_artefakt` har regnet ut invariantene på nytt —
-- med de verdiene FILEN bar, ikke med de verdiene aksepten trenger.
-- `p_punkter` er {punkt: målt verdi}; alle punktene for én lesning
-- skrives i ETT kall, av samme grunn som kravet registreres i én
-- setning: en attest som kan vokse etterpå, er ingen attest på hva
-- filen sa.
--
-- Samme replay-regel som CI-attesten (SP-2): samme bytes lest to ganger
-- gir samme rader og er en no-op; samme bytes med et ANNET måletall er
-- to motstridende referater av én fil, og det skal høres.
CREATE OR REPLACE FUNCTION attester_evidensfil(
    p_krav_id TEXT, p_sti TEXT, p_sha256 TEXT, p_punkter JSONB,
    p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_sha TEXT; v_punkt TEXT; v_verdi TEXT; v_lagret RECORD;
BEGIN
    v_sha := lower(p_sha256);
    IF v_sha !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'attester_evidensfil: «%» er ingen sha256 — en'
            ' attest som ikke navngir bytene, binder ingenting', p_sha256
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_punkter IS NULL OR jsonb_typeof(p_punkter) <> 'object'
       OR p_punkter = '{}'::jsonb THEN
        RAISE EXCEPTION 'attester_evidensfil: ingen punkter — en lesning'
            ' uten måletall er ikke et referat'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    FOR v_punkt, v_verdi IN
        SELECT j.k, j.v #>> '{}' FROM jsonb_each(p_punkter) AS j(k, v)
         ORDER BY j.k LOOP
        IF v_verdi IS NULL THEN
            RAISE EXCEPTION 'attester_evidensfil: punkt % mangler måletall',
                v_punkt USING ERRCODE = 'invalid_parameter_value';
        END IF;
        SELECT * INTO v_lagret FROM public.evidensfil_attest e
         WHERE e.sha256 = v_sha AND e.punkt = v_punkt;
        IF FOUND THEN
            IF v_lagret.krav_id IS DISTINCT FROM p_krav_id
               OR v_lagret.sti IS DISTINCT FROM p_sti
               OR v_lagret.maalt_verdi IS DISTINCT FROM v_verdi THEN
                RAISE EXCEPTION 'attester_evidensfil: sha256:% er alt'
                    ' attestert for punkt % med et annet innhold — én fil'
                    ' har ett innhold, og et referat som spriker fra det'
                    ' lagrede er en programfeil', v_sha, v_punkt
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
        ELSE
            INSERT INTO public.evidensfil_attest (sha256, punkt, krav_id,
                sti, maalt_verdi, aktor, attestert_av)
            VALUES (v_sha, v_punkt, p_krav_id, p_sti, v_verdi, p_aktor,
                    session_user);
        END IF;
    END LOOP;
END $$;

CREATE OR REPLACE FUNCTION aksepter_moduldeployment(
    p_modul_id TEXT, p_miljo TEXT, p_release_id TEXT, p_drill_id BIGINT,
    p_krav_id TEXT, p_e2e_tenant TEXT, p_e2e_artefakt UUID,
    p_evidens_sha TEXT, p_manifest_commit TEXT, p_ci_run TEXT,
    p_ci_commit TEXT, p_punkter JSONB, p_nokkel TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_livslop TEXT; v_mangler TEXT; v_punkt RECORD; v_verdi JSONB;
        v_epoch BIGINT; v_avvik TEXT; v_drill_tenant TEXT;
        v_kandidat_oppdrag BIGINT; v_forrige_tenant TEXT; v_ref TEXT;
        v_holder BOOLEAN; v_ci RECORD; v_ci_attest BOOLEAN;
        v_evidens RECORD; v_ci_av TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('modul:' || p_modul_id, 0));
    -- FORMEN FØRST (Codex P1, #117 runde 19). `p_evidens_sha` gikk rett
    -- inn i den immutable raden og inn i sammenligningen mot `kilde_ref`
    -- uten noe formkrav. En TOM streng var derfor en gyldig «hash», og
    -- en `kilde_ref` som endte på `@sha256:` var «enig» med den. En sha
    -- måles på formen sin før den brukes til noe som helst — samme
    -- disiplin som `hode_sha` i CI-attesten. Små bokstaver kreves, ikke
    -- normaliseres: raden og attesten skal ikke kunne stå med to
    -- skrivemåter av samme bytes.
    IF p_evidens_sha !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: «%» er ingen sha256 —'
            ' evidensfilen skal navngis av bytene sine', p_evidens_sha
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- …og PROVENIENSEN på samme måte (Codex P1, #117 runde 22).
    -- `p_manifest_commit` gikk rett inn i den uforanderlige raden uten
    -- noe formkrav, og uten noe bånd til commiten CI-attesten gjelder.
    -- En kaller med `disponit_modules_admin` kunne derfor oppgi en pen,
    -- grønt attestert `p_ci_commit` og skrive hva som helst i
    -- `p_manifest_commit` — og raden ville stått for alltid og påstått
    -- at manifestet og artefaktene fra ÉN commit var prøvd av en
    -- kjøring på en ANNEN. `m56-aksept.py` har alltid krevd likheten
    -- (punktene påberoper seg «grønn CI på akseptcommiten»), men et
    -- skript er ingen skranke for den som kaller definereren direkte.
    -- Små bokstaver kreves, ikke normaliseres — samme disiplin som
    -- evidenshashen over: raden og attesten skal ikke kunne stå med to
    -- skrivemåter av samme commit.
    IF p_manifest_commit !~ '^[0-9a-f]{40}$'
       OR p_ci_commit !~ '^[0-9a-f]{40}$' THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: «%»/«%» er ingen'
            ' commit-sha — en aksept navngir commiten den hviler på',
            p_manifest_commit, p_ci_commit
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_manifest_commit IS DISTINCT FROM p_ci_commit THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: manifestet er hentet'
            ' fra %, mens CI-kjøringen prøvde % — akseptcommiten er ÉN'
            ' commit, og punktene påberoper seg en grønn kjøring på'
            ' nøyaktig den', p_manifest_commit, p_ci_commit
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- SP-2: replay er et no-op, aldri en ny hendelse — men BARE når hele
    -- det materielle innholdet er det samme.
    --
    -- Codex' P2 på PR #117: den forrige formen returnerte på nøkkelen
    -- alene. Kjørte operatøren akseptkommandoen på nytt etter å ha
    -- rettet en CI-kjøring, evidenshash, drill eller E2E-artefakt, ble
    -- rettelsen STILLE forkastet — raden er immutabel, så den bar
    -- fortsatt de gamle bevisene — og skriptet skrev likevel AKSEPTERT.
    -- Revisjonssporet fortalte da noe annet enn kallet som lagde det.
    -- Avvikende gjenbruk av en nøkkel er en programfeil og skal høres,
    -- akkurat som i `registrer_moduldrill`.
    PERFORM 1 FROM public.modulaksept WHERE nokkel = p_nokkel;
    IF FOUND THEN
        PERFORM 1 FROM public.modulaksept
         WHERE nokkel = p_nokkel AND modul_id = p_modul_id
           AND miljo = p_miljo AND release_id = p_release_id
           AND drill_id = p_drill_id AND krav_id = p_krav_id
           AND e2e_tenant = p_e2e_tenant
           AND e2e_artefakt_id = p_e2e_artefakt
           AND evidens_jsonl_sha256 = p_evidens_sha
           AND manifest_commit = p_manifest_commit
           AND ci_run = p_ci_run AND ci_commit = p_ci_commit;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'aksepter_moduldeployment: nøkkel % gjenbrukt'
                ' med annet innhold — den lagrede aksepten bærer andre'
                ' bevis enn dette kallet', p_nokkel
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        -- Punktobservasjonene er like materielle som radens egne felt:
        -- en rettet måling på samme nøkkel er også en forkastet rettelse.
        SELECT string_agg(pk.punkt, ', ' ORDER BY pk.punkt) INTO v_avvik
          FROM public.modulaksept_punkt pk
         WHERE pk.modul_id = p_modul_id AND pk.miljo = p_miljo
           AND pk.release_id = p_release_id
           AND ((p_punkter -> pk.punkt) ->> 'grenseverdi'
                    IS DISTINCT FROM pk.grenseverdi
             OR (p_punkter -> pk.punkt) ->> 'maalt_verdi'
                    IS DISTINCT FROM pk.maalt_verdi
             OR (p_punkter -> pk.punkt) ->> 'kilde_type'
                    IS DISTINCT FROM pk.kilde_type
             OR (p_punkter -> pk.punkt) ->> 'kilde_ref'
                    IS DISTINCT FROM pk.kilde_ref);
        IF v_avvik IS NOT NULL THEN
            RAISE EXCEPTION 'aksepter_moduldeployment: nøkkel % gjenbrukt'
                ' med andre punktobservasjoner: %', p_nokkel, v_avvik
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN;
    END IF;
    -- Aksepten gjelder raden slik den faktisk kjører.
    SELECT livslop INTO v_livslop FROM public.moduldeployment
     WHERE modul_id = p_modul_id AND miljo = p_miljo
       AND release_id = p_release_id;
    IF v_livslop IS DISTINCT FROM 'claiming' THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: %/%/% er % — aksepten'
            ' binder deploymentraden slik den faktisk kjører (claiming)',
            p_modul_id, p_miljo, p_release_id,
            coalesce(v_livslop, '<mangler>')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- A2, siste ledd (Codex P1, #117 runde 5): E2E-beviset må komme fra
    -- DRILLENS KANDIDATOPPDRAG. FK-en på tabellen binder tenant, modul,
    -- release og promotert tilstand — men ikke HVILKET arbeid artefaktet
    -- kom av, så et hvilket som helst annet promotert artefakt fra samme
    -- release passerte den. Kontrollen fantes bare i `m56-aksept.py`, og
    -- et skript er ingen skranke for den som kaller funksjonen direkte.
    -- Drillraden bærer nå kandidatoppdraget, så båndet kan måles her.
    -- Kun DEN dimensjonen måles her; release og tilstand bæres fortsatt
    -- av FK-en, som gjelder enhver skrivevei og ikke bare denne.
    SELECT d.tenant, d.kandidat_oppdrag
      INTO v_drill_tenant, v_kandidat_oppdrag
      FROM public.moduldrill d
     WHERE d.modul_id = p_modul_id AND d.drill_id = p_drill_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: ukjent drill %/%',
            p_modul_id, p_drill_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_drill_tenant IS DISTINCT FROM p_e2e_tenant THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: drillen ble målt for'
            ' tenant %, mens E2E-beviset er % — evidens fra én tenant'
            ' aksepterer ingenting for en annen',
            v_drill_tenant, p_e2e_tenant
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_forrige_tenant := current_setting('disponit.tenant', true);
    PERFORM set_config('disponit.tenant', p_e2e_tenant, true);
    IF NOT EXISTS (SELECT 1 FROM public.artefakt a
                    WHERE a.tenant = p_e2e_tenant
                      AND a.artefakt_id = p_e2e_artefakt
                      AND a.oppdrag_id = v_kandidat_oppdrag) THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: E2E-artefaktet % kom'
            ' ikke av drillens kandidatoppdrag % — aksepten skal binde'
            ' beviset drillen SÅ, ikke et annet artefakt fra samme'
            ' release', p_e2e_artefakt, v_kandidat_oppdrag
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.tenant',
                       coalesce(v_forrige_tenant, ''), true);
    -- Port 5: KOMPLETT punktsett, målt mot kravpunkt-registeret — ikke
    -- mot kallerens liste. Hvert punkt må bære alle fire feltene.
    SELECT string_agg(k.punkt, ', ') INTO v_mangler
      FROM public.akseptkrav_punkt k
     WHERE k.krav_id = p_krav_id AND NOT (p_punkter ? k.punkt);
    IF v_mangler IS NOT NULL THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: ufullstendig punktsett'
            ' — mangler: %', v_mangler
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.akseptkrav_punkt
                    WHERE krav_id = p_krav_id) THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: ukjent krav %', p_krav_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.modulaksept (modul_id, miljo, release_id, drill_id,
        krav_id, e2e_tenant, e2e_artefakt_id, evidens_jsonl_sha256,
        manifest_commit, ci_run, ci_commit, nokkel, aktor)
    VALUES (p_modul_id, p_miljo, p_release_id, p_drill_id, p_krav_id,
            p_e2e_tenant, p_e2e_artefakt, p_evidens_sha, p_manifest_commit,
            p_ci_run, p_ci_commit, p_nokkel, p_aktor);
    -- ------------------------------------------------------------
    -- A3: HVER OBSERVASJON MÅLES (Codex P1, #117 runde 15).
    --
    -- Løkka under kontrollerte at de fire feltene FANTES, aldri hva de
    -- sa. En kaller med `disponit_modules_admin` kunne derfor sende
    -- hjemmelagde grenser, måletall og kildereferanser for alle 21
    -- punktene og oppfylle A3 uten å ha vært innom `m56-aksept.py` —
    -- og observasjonene er uforanderlige når de først står der.
    --
    -- Tre ting måles nå, i denne rekkefølgen:
    --   (1) GRENSEN OG KILDETYPEN er REGISTERETS, ikke kallerens.
    --       Kalleren må gjenta dem ordrett; spriker de, er kallet en
    --       annen påstand enn kravet og avvises. (Feltene sendes
    --       fortsatt — SP-2-replaykontrollen over sammenligner dem med
    --       de lagrede radene, og et kall som utelot dem ville gjort
    --       den kontrollen innholdsløs.)
    --   (2) MÅLINGEN REGNES MOT KRAVET. `maalt_verdi` må være den
    --       verdien registeret sier en grønn observasjon har. Et punkt
    --       som ikke oppfyller kravet skrives ikke — det er ikke et
    --       punkt med en dårlig verdi, det er en aksept som ikke skal
    --       finnes.
    --   (3) KILDEN MÅ PEKE PÅ EVIDENS DENNE TRANSAKSJONEN SER.
    --       `evidensfil` må ende på hashen aksepten selv binder,
    --       `ci_kjoring` må navngi nøyaktig aksepradens egen kjøring og
    --       commit, `artefakt` må være et promotert artefakt på den
    --       aksepterte releasen, og `registerhendelse` en hendelse på
    --       denne modulen. Da kan `kilde_ref` ikke lenger være en
    --       fortelling; den er en peker som holder.
    -- ------------------------------------------------------------
    -- CI-KJØRINGEN MÅLES MOT ATTESTEN, IKKE MOT KALLERENS EGNE PARAMETRE
    -- (Codex P1, #117 runde 16). `p_ci_run` og `p_ci_commit` kommer fra
    -- samme kall som `kilde_ref`; at de tre er enige, sier ingenting om
    -- at noe er kjørt. Kravet står i `akseptkrav_ci`, og det som skal
    -- oppfylle det er referatet veien som spurte GitHub skrev ned.
    SELECT c.arbeidsflyt, c.hendelse, c.gren, c.konklusjon INTO v_ci
      FROM public.akseptkrav_ci c WHERE c.krav_id = p_krav_id;
    SELECT a.attestert_av INTO v_ci_av
      FROM public.ci_kjoringsattest a
     WHERE a.ci_run = p_ci_run
       AND a.arbeidsflyt = v_ci.arbeidsflyt
       AND a.hendelse = v_ci.hendelse
       AND a.gren = v_ci.gren
       AND a.konklusjon = v_ci.konklusjon
       AND a.hode_sha = lower(p_ci_commit);
    v_ci_attest := v_ci.arbeidsflyt IS NOT NULL AND v_ci_av IS NOT NULL;
    -- ------------------------------------------------------------
    -- FIRE ØYNE, MÅLT PÅ INNLOGGINGEN (Codex P1, #117 runde 19→22).
    --
    -- Skillet mellom attestant og akseptør var hittil bare en
    -- RETTIGHETSGRENSE: `disponit_modules_admin` har ikke EXECUTE på
    -- attestfunksjonene. En rettighetsgrense holder bare så lenge ingen
    -- innlogging står på begge sider av den — og migrator er medlem av
    -- BÅDE `disponit_modul_eier` og `disponit_modules_admin`. `WITH
    -- INHERIT FALSE` sperrer arv, ikke `SET ROLE`, så én autentisert
    -- identitet kunne skrive attesten, legge fullmakten ned, ta den
    -- andre opp og skrive aksepten som hviler på sin egen attest.
    -- Nøyaktig det samme gjelder enhver ny rolle som får medlemskap i
    -- eierrollen: en smalere GRANT flytter grensen, den håndhever den
    -- ikke.
    --
    -- Regelen hører derfor hjemme HER, der forutsetningen forbrukes, og
    -- den måles på `session_user` — den AUTENTISERTE identiteten, som
    -- `SET ROLE` ikke rører. Attesten aksepten hviler på må være skrevet
    -- av en ANNEN innlogging enn den som skriver aksepten. To fullmakter
    -- i én sesjon er én identitet, og én identitet er ikke fire øyne.
    -- ------------------------------------------------------------
    IF v_ci_av IS NOT NULL AND v_ci_av = session_user THEN
        RAISE EXCEPTION 'aksepter_moduldeployment: CI-attesten for kjøring'
            ' % er skrevet av % — samme innlogging som skriver aksepten.'
            ' Attestanten er ikke akseptøren: referatet og aksepten som'
            ' hviler på det skal komme fra to autentiserte identiteter,'
            ' ikke fra to rolleskift i én sesjon', p_ci_run, v_ci_av
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_forrige_tenant := current_setting('disponit.tenant', true);
    PERFORM set_config('disponit.tenant', p_e2e_tenant, true);
    FOR v_punkt IN SELECT k.punkt, k.kilde_type, k.grenseverdi, k.maalt_krav
                     FROM public.akseptkrav_punkt k
                    WHERE k.krav_id = p_krav_id LOOP
        v_verdi := p_punkter -> v_punkt.punkt;
        IF v_verdi IS NULL
           OR v_verdi ->> 'grenseverdi' IS NULL
           OR v_verdi ->> 'maalt_verdi' IS NULL
           OR v_verdi ->> 'kilde_type' IS NULL
           OR v_verdi ->> 'kilde_ref' IS NULL THEN
            RAISE EXCEPTION 'aksepter_moduldeployment: punkt % mangler'
                ' grenseverdi/maalt_verdi/kilde_type/kilde_ref',
                v_punkt.punkt USING ERRCODE = 'invalid_parameter_value';
        END IF;
        IF v_verdi ->> 'grenseverdi' IS DISTINCT FROM v_punkt.grenseverdi
           OR v_verdi ->> 'kilde_type' IS DISTINCT FROM v_punkt.kilde_type
        THEN
            RAISE EXCEPTION 'aksepter_moduldeployment: punkt % oppgir'
                ' grense «%» av type «%», mens kravet er «%» av type'
                ' «%» — grensen er registerets, ikke kallerens',
                v_punkt.punkt, v_verdi ->> 'grenseverdi',
                v_verdi ->> 'kilde_type', v_punkt.grenseverdi,
                v_punkt.kilde_type
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        IF v_verdi ->> 'maalt_verdi' IS DISTINCT FROM v_punkt.maalt_krav THEN
            RAISE EXCEPTION 'aksepter_moduldeployment: punkt % målte «%»,'
                ' men en grønn observasjon er «%» — en aksept skrives av'
                ' målinger som oppfyller kravet, ikke av målinger som'
                ' ikke gjør det', v_punkt.punkt, v_verdi ->> 'maalt_verdi',
                v_punkt.maalt_krav
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        v_ref := v_verdi ->> 'kilde_ref';
        IF v_punkt.kilde_type = 'evidensfil' THEN
            -- REFERATET FRA VEIEN SOM LESTE FILEN (Codex P1, runde 19).
            -- Den forrige formen sammenlignet `kilde_ref` med aksepten
            -- sin egen `p_evidens_sha` — to felter fra samme kall — og
            -- godtok hele halen av strengen uten å se på stien. Med en
            -- tom `p_evidens_sha` holdt en `kilde_ref` som endte på
            -- `@sha256:`, og siden `maalt_verdi` uansett må være
            -- registerets grønne fasit, kunne fire observasjoner om en
            -- fil som ikke fantes bli en immutabel aksept.
            --
            -- Punktet måles nå mot ATTESTEN: en immutabel rad, skrevet
            -- med eierrollens fullmakt av veien som faktisk hashet fila,
            -- som sier hvilken sti bytene lå på og hva filen bar for
            -- NØYAKTIG dette punktet. Kalleren kan bare gjenta den.
            SELECT * INTO v_evidens FROM public.evidensfil_attest e
             WHERE e.sha256 = lower(p_evidens_sha)
               AND e.punkt = v_punkt.punkt AND e.krav_id = p_krav_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'aksepter_moduldeployment: punkt % hviler'
                    ' på evidensfilen sha256:%, men ingen attest sier at'
                    ' den filen er lest og hva den bar for punktet — en'
                    ' hash aksepten selv oppgir, beviser ingenting; det'
                    ' gjør referatet fra veien som leste',
                    v_punkt.punkt, lower(p_evidens_sha)
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
            -- …og evidensattesten måles på samme fire øyne som
            -- CI-attesten over: den som LESTE filen skal ikke være den
            -- som skriver aksepten filens måletall bærer.
            IF v_evidens.attestert_av = session_user THEN
                RAISE EXCEPTION 'aksepter_moduldeployment: punkt % hviler'
                    ' på en evidensattest skrevet av % — samme innlogging'
                    ' som skriver aksepten. Den som leste filen og den som'
                    ' aksepterer på det den bar, skal være to autentiserte'
                    ' identiteter', v_punkt.punkt, v_evidens.attestert_av
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
            -- Stien er attestens, ikke kallerens: LIKHET, ikke hale.
            IF v_ref IS DISTINCT FROM
               (v_evidens.sti || '@sha256:' || lower(p_evidens_sha)) THEN
                RAISE EXCEPTION 'aksepter_moduldeployment: punkt % viser'
                    ' til evidensfilen «%», mens attesten leste «%@sha256:%»'
                    ' — en observasjon skal navngi DEN filen som ble lest,'
                    ' ikke en sti med riktig hale', v_punkt.punkt, v_ref,
                    v_evidens.sti, lower(p_evidens_sha)
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
            -- …og det er FILENS måletall som skal oppfylle kravet.
            IF v_evidens.maalt_verdi IS DISTINCT FROM v_punkt.maalt_krav THEN
                RAISE EXCEPTION 'aksepter_moduldeployment: punkt % — filen'
                    ' sha256:% bar «%», men en grønn observasjon er «%».'
                    ' Aksepten regner mot det filen SA, ikke mot det'
                    ' kallet gjentar', v_punkt.punkt, lower(p_evidens_sha),
                    v_evidens.maalt_verdi, v_punkt.maalt_krav
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
        ELSIF v_punkt.kilde_type = 'ci_kjoring' THEN
            IF v_ref IS DISTINCT FROM
               ('run ' || p_ci_run || ' @ ' || p_ci_commit) THEN
                RAISE EXCEPTION 'aksepter_moduldeployment: punkt % viser'
                    ' til CI-kjøringen «%», mens aksepten bærer «run % @'
                    ' %» — invariantpunktene hviler HELT på den ene'
                    ' kjøringen raden navngir', v_punkt.punkt, v_ref,
                    p_ci_run, p_ci_commit
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
            -- …og den kjøringen må være ATTESTERT (Codex P1, runde 16):
            -- referatet fra veien som spurte GitHub må si at kravets
            -- workflow kjørte grønt, på kravets hendelse og gren, for
            -- nøyaktig akseptcommiten. Uten den er «run X @ Y» bare to
            -- av kallerens egne strenger som ligner på hverandre.
            IF NOT v_ci_attest THEN
                RAISE EXCEPTION 'aksepter_moduldeployment: punkt % hviler'
                    ' på CI-kjøring %, men ingen attest sier at kravets'
                    ' workflow (%) kjørte % på %/% for commit % — en'
                    ' kjøring aksepten selv navngir, beviser ingenting;'
                    ' det gjør referatet fra veien som spurte',
                    v_punkt.punkt, p_ci_run,
                    coalesce(v_ci.arbeidsflyt, '<krav uten ci-krav>'),
                    coalesce(v_ci.konklusjon, '?'),
                    coalesce(v_ci.hendelse, '?'), coalesce(v_ci.gren, '?'),
                    lower(p_ci_commit)
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
        ELSIF v_punkt.kilde_type = 'artefakt' THEN
            -- Formen FØRST, i sin egen IF: PostgreSQL lover ingen
            -- kortslutning av `OR`, så en `v_ref::uuid` ved siden av
            -- formkontrollen kunne blitt evaluert likevel og kastet
            -- `invalid_text_representation` i stedet for feilen her.
            v_holder := v_ref ~
                ('^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
                 || '[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$');
            IF v_holder THEN
                v_holder := EXISTS (SELECT 1 FROM public.artefakt a
                                     WHERE a.tenant = p_e2e_tenant
                                       AND a.artefakt_id = v_ref::uuid
                                       AND a.modul_id = p_modul_id
                                       AND a.release_id = p_release_id
                                       AND a.tilstand = 'promotert');
            END IF;
            IF NOT v_holder THEN
                RAISE EXCEPTION 'aksepter_moduldeployment: punkt % viser'
                    ' til artefaktet «%», som ikke er et promotert'
                    ' artefakt fra %/% for tenant % — et bevis som ikke'
                    ' finnes, beviser ingenting', v_punkt.punkt, v_ref,
                    p_modul_id, p_release_id, p_e2e_tenant
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
        ELSE   -- 'registerhendelse'
            -- `^[0-9]{1,18}$`: sifre ALENE holder ikke, for en id som er
            -- for stor for BIGINT kaster på castet (samme grunn som over).
            v_holder := v_ref ~ '^[0-9]{1,18}$';
            IF v_holder THEN
                v_holder := EXISTS (
                    SELECT 1 FROM public.modulregister_hendelse h
                     WHERE h.id = v_ref::bigint
                       AND h.modul_id = p_modul_id);
            END IF;
            IF NOT v_holder THEN
                RAISE EXCEPTION 'aksepter_moduldeployment: punkt % viser'
                    ' til registerhendelsen «%», som ikke er en hendelse'
                    ' på %', v_punkt.punkt, v_ref, p_modul_id
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
        END IF;
        INSERT INTO public.modulaksept_punkt (modul_id, miljo, release_id,
            krav_id, punkt, grenseverdi, maalt_verdi, kilde_type, kilde_ref)
        VALUES (p_modul_id, p_miljo, p_release_id, p_krav_id,
                v_punkt.punkt, v_punkt.grenseverdi,
                v_punkt.maalt_krav, v_punkt.kilde_type, v_ref);
    END LOOP;
    PERFORM set_config('disponit.tenant',
                       coalesce(v_forrige_tenant, ''), true);
    SELECT module_epoch INTO v_epoch FROM public.modulhode
     WHERE modul_id = p_modul_id;
    INSERT INTO public.modulregister_hendelse (modul_id, hendelse,
        release_id, miljo, module_epoch, aktor, detalj)
    VALUES (p_modul_id, 'modulaksept', p_release_id, p_miljo, v_epoch,
            p_aktor, jsonb_build_object(
                'drill_id', p_drill_id, 'krav_id', p_krav_id,
                'e2e_artefakt_id', p_e2e_artefakt::text,
                'evidens_jsonl_sha256', p_evidens_sha,
                'ci_run', p_ci_run, 'ci_commit', p_ci_commit));
END $$;

ALTER FUNCTION registrer_moduldrill(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    BIGINT, BIGINT, BIGINT, BIGINT, TEXT, TEXT, TEXT, TIMESTAMPTZ)
    OWNER TO disponit_modul_eier;
ALTER FUNCTION aksepter_moduldeployment(TEXT, TEXT, TEXT, BIGINT, TEXT,
    TEXT, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, TEXT)
    OWNER TO disponit_modul_eier;
ALTER FUNCTION attester_ci_kjoring(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT) OWNER TO disponit_modul_eier;
ALTER FUNCTION attester_evidensfil(TEXT, TEXT, TEXT, JSONB, TEXT)
    OWNER TO disponit_modul_eier;
ALTER FUNCTION maal_rent_utfall(TEXT, BIGINT) OWNER TO disponit_modul_eier;
-- Grants i EIERVINDUET (048-disiplinen): en REVOKE fra en ikke-eier er
-- en stille no-op, og PUBLIC ville beholdt default-EXECUTE på begge.
SET LOCAL ROLE disponit_modul_eier;
REVOKE ALL ON FUNCTION registrer_moduldrill(TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, BIGINT, BIGINT, BIGINT, BIGINT, TEXT, TEXT, TEXT, TIMESTAMPTZ)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION aksepter_moduldeployment(TEXT, TEXT, TEXT, BIGINT,
    TEXT, TEXT, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, TEXT)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION attester_ci_kjoring(TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION attester_evidensfil(TEXT, TEXT, TEXT, JSONB, TEXT)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION maal_rent_utfall(TEXT, BIGINT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION registrer_moduldrill(TEXT, TEXT, TEXT, TEXT,
    TEXT, TEXT, BIGINT, BIGINT, BIGINT, BIGINT, TEXT, TEXT, TEXT,
    TIMESTAMPTZ) TO disponit_modules_admin;
GRANT EXECUTE ON FUNCTION aksepter_moduldeployment(TEXT, TEXT, TEXT,
    BIGINT, TEXT, TEXT, UUID, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, TEXT)
    TO disponit_modules_admin;
-- INGEN GRANT PÅ DE TO ATTESTFUNKSJONENE (Codex P1, #117 runde 19).
-- Attestanten skal ikke være akseptøren. `disponit_modules_admin` er den
-- brede deployfullmakten — registrer_release, bytt_release, onboarding,
-- drillen OG aksepten — og med EXECUTE her kunne den skrevet sitt eget
-- CI-referat og latt aksepten hvile på det. Attesten er derfor
-- EIERROLLENS: `disponit_modul_eier` er funksjonseier og har EXECUTE i
-- kraft av det, uten at noen annen rolle får det. Rollen er NOLOGIN, og
-- migrator har den bare `WITH INHERIT FALSE` — veien dit er et
-- eksplisitt, sporbart `SET ROLE`, ikke stille arv (samme skille som
-- oppsettet gjør for m37_claimer og policy_eier).
-- Å legge attesten på PUBLIC eller på admin igjen er å gjenåpne hullet;
-- en ny fullmakt hører hjemme i en NY rolle i oppsett-skriptet.
--
-- …og attestveiens innlogging får den fullmakten HER, smalt (Codex P1,
-- #117 runde 22). `disponit_ci_verifikator` fikk først `GRANT
-- disponit_modul_eier ... WITH INHERIT FALSE`, men eierrollen eier
-- BEGGE sider — attestfunksjonene OG `registrer_moduldrill`/
-- `aksepter_moduldeployment` — og eier har EXECUTE i kraft av
-- eierskapet, uansett hva som er revokert fra PUBLIC. Et `SET ROLE`
-- ville altså gitt verifikatoren hele akseptveien. Den trenger to
-- kall og får nøyaktig to: EXECUTE direkte på attestfunksjonene, og
-- ingen vei til eierrollen. Rettighetsgrensen er dermed like smal som
-- oppgaven — og fire-øyne-regelen i `aksepter_moduldeployment` står
-- uansett bak den, for en grense som bare finnes i en GRANT, faller
-- den dagen noen gir et medlemskap som «bare» er praktisk.
GRANT EXECUTE ON FUNCTION attester_ci_kjoring(TEXT, TEXT, TEXT, TEXT,
    TEXT, TEXT, TEXT) TO disponit_ci_verifikator;
GRANT EXECUTE ON FUNCTION attester_evidensfil(TEXT, TEXT, TEXT, JSONB,
    TEXT) TO disponit_ci_verifikator;
-- Målingen er LESING og gir ingen skrivevei: den svarer ja/nei om ett
-- oppdrag i én tenant, og drillsonden og sjekklisten kaller den med
-- nøyaktig den deployfullmakten de alt har.
GRANT EXECUTE ON FUNCTION maal_rent_utfall(TEXT, BIGINT)
    TO disponit_modules_admin;
RESET ROLE;

-- Definerne leser moduldeployment/modulrelease/modulhode som modul_eier
-- (eier dem alt) og skriver de nye tabellene: de nye eies av migrator,
-- så modul_eier trenger DML-grant på nøyaktig dem.
GRANT SELECT, INSERT ON moduldrill, modulaksept, modulaksept_punkt
    TO disponit_modul_eier;
GRANT SELECT ON akseptkrav_punkt, akseptkrav_ci TO disponit_modul_eier;
-- Attestene skrives av `attester_ci_kjoring`/`attester_evidensfil` og
-- LESES av aksepten. Ingen annen rolle rører tabellene: de er referatet,
-- ikke en notatblokk.
GRANT SELECT, INSERT ON ci_kjoringsattest, evidensfil_attest
    TO disponit_modul_eier;
-- Definerne MÅLER nå drillutfallene i `oppdrag`/`artefakt` i stedet for å
-- motta dem (Codex P1, #117 runde 5), og trenger derfor lesetilgang dit.
-- KOLONNENIVÅ, ikke tabellnivå: målingene leser status, kvitteringen med
-- SIGNATUREN og resultathashen sin (Codex P1, runde 8 — «signert» måles på
-- signaturkolonnen, ikke på nyttelasten, så kolonnen må være lesbar her),
-- eierskap og artefaktenes tilhørighet — aldri `payload_kryptert`,
-- `key_id`, `nonce` eller `ciphertext`. En fullmakt som gir mer enn
-- målingen bruker, er en fullmakt som venter på et annet kall.
-- Tenant-policyen (016/008, FORCE) gjelder uansett: definerne eier
-- ikke tabellene og setter `disponit.tenant` eksplisitt.
-- `opprettet`/`status_ts` er drillvinduets to kolonner (Codex P1, runde
-- 16): utfallene måles mot NÅR oppdraget ble bestilt og når det ble
-- terminalt, holdt opp mot registerets to `releasebytte`-overganger.
-- Uten dem er «arbeidet krysset rullingen» ikke målbart.
-- `claim_release_id`/`claim_miljo` er sporet claim-porten setter (§0), og
-- den ene identiteten drillens tre ledd hviler på (Codex P1, runde 20).
-- `forste_claim_ts` er det samme sporets klokke (Codex P1, runde 21):
-- claim-stoppet er en varighet, og uten kolonnen kan porten bare måle at
-- oppdraget lå i vinduet — ikke at det lå der lenge nok.
GRANT SELECT (tenant, id, status, kvittering, kvittering_signatur,
              resultathash, eiermodul, opprettet, status_ts,
              claim_release_id, claim_miljo, forste_claim_ts)
    ON oppdrag TO disponit_modul_eier;
GRANT SELECT (tenant, artefakt_id, oppdrag_id, release_id, tilstand)
    ON artefakt TO disponit_modul_eier;
-- …og AVTRYKKET verifiseringsveien etterlater (Codex P1, runde 15):
-- «signert kvittering» måles ikke lenger på tre kolonner den samme
-- rollen kan skrive fritt, men på at kvitteringskapabiliteten for
-- oppdraget er brent med nøyaktig oppdragets `resultathash`.
-- `kvitteringskapabiliteter` eies av `disponit_m37_claimer` (005), så
-- granten må gis I EIERVINDUET — en GRANT fra en ikke-eier er en feil,
-- og en REVOKE fra en ikke-eier en stille no-op (048-disiplinen).
-- Kolonnenivå, som over: målingen leser fire felter, ikke jti-en, ikke
-- claim-identiteten, ikke fristene. Tabellen har ingen RLS — den står
-- `REVOKE ALL ... FROM PUBLIC` uten et eneste tabellgrant, og dette er
-- det første og eneste.
SET LOCAL ROLE disponit_m37_claimer;
GRANT SELECT (tenant, oppdrag_id, status, resultathash)
    ON kvitteringskapabiliteter TO disponit_modul_eier;
RESET ROLE;
DO $$
BEGIN  -- identity-sekvensen alene, aldri hele skjemaets (minste fullmakt)
    EXECUTE format('GRANT USAGE ON SEQUENCE %s TO disponit_modul_eier',
                   pg_get_serial_sequence('moduldrill', 'drill_id'));
END $$;

-- ------------------------------------------------------------
-- 8. Statusflaten: hva forespørselsveien SKAL kunne se (Codex P1, #117
--    runde 14). Kjøretidsrollen ble gitt `SELECT` på hele akseptflaten
--    «så statusetiketter og evidensvisninger skal kunne peke på
--    hendelsen» — men en statusetikett trenger FAKTUMET, ikke bevisene:
--    at (modul, miljø, release) er akseptert mot et krav, når, og hvilken
--    drill den hviler på. Tenanten, artefakt-UUIDen, oppdrags-IDene,
--    aktøren, evidenshashene og CI-referansene er driftens bevis og hører
--    til eier- og migratorveien.
--
--    Visningen er derfor SANERT — den bærer ingen tenantidentifikator i
--    det hele tatt, og har dermed ingenting å lekke på tvers av tenanter.
--    Den eies av migrator (tabelleieren), så den leser gjennom RLS-porten
--    over slik en visning skal; og fordi den ikke velger en eneste
--    tenantkolonne, er den samme rad for alle som ser den.
-- ------------------------------------------------------------
CREATE VIEW modulaksept_status AS
SELECT modul_id, miljo, release_id, krav_id, drill_id, akseptert_ts
  FROM modulaksept;

-- ------------------------------------------------------------
-- 9. DRILLENS RESERVASJON AV DEPLOYMENTFLATEN (Codex P1, #117 runde 14).
--
--    Flippedrillen holdt en advisory-lås i sitt EGET nøkkelrom
--    (to-heltallsrommet), mens registerets overganger tar
--    `pg_advisory_xact_lock(hashtextextended('modul:' || modul_id, 0))`.
--    To ulike låserom: en vanlig `bytt_release`, et `noddeaktiver_modul`
--    eller en `sett_modulstatus` så aldri drillens reservasjon, og kunne
--    drenere rullbakk- eller kandidatdeploymenten MELLOM to drillfaser.
--    Målingene blir da noe annet enn de sier, de enveis drill-id-ene er
--    brukt opp uansett, og miljøet står halvt over i en tilstand ingen
--    artefakt beskriver. Drillåsen gjerdet bare en annen drill.
--
--    Reservasjonen kan ikke være en lås i det rommet heller, og det er
--    poenget med at den er en RAD:
--      * eksklusivt ville den stengt claim-porten i 015, som tar den
--        samme modulnøkkelen DELT for hvert eneste claim — og drillen
--        måler nettopp claiming, så den ville ventet på seg selv;
--      * delt ville den stengt drillens EGNE overganger ute, for de går
--        gjennom sjekklistens faser 2/4/9 i EGNE prosesser med egne
--        sesjoner, og en advisory-lås gjelder én sesjon.
--    Innehaveren er derfor et TOKEN som kan presenteres av alle drillens
--    sesjoner (`disponit.deployreservasjon`), og porten står på tabellen
--    — så den gjelder enhver skrivevei inn i `moduldeployment`, ikke bare
--    de funksjonene noen husket å endre.
--
--    Utløpstiden er sikkerhetsventilen: en drill som dør uten å frigi,
--    skal ikke stenge modulen for alltid. Frigivelsen ved normal slutt
--    er den vanlige veien; utløpet er backstoppen.
-- ------------------------------------------------------------
CREATE TABLE moduldeployment_reservasjon (
    modul_id   TEXT NOT NULL REFERENCES modulhode (modul_id),
    miljo      TEXT NOT NULL,
    innehaver  TEXT NOT NULL CHECK (btrim(innehaver) <> ''),
    aktor      TEXT NOT NULL,
    tatt_ts    TIMESTAMPTZ NOT NULL DEFAULT now(),
    utloper_ts TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (modul_id, miljo),
    CHECK (utloper_ts > tatt_ts)
);

CREATE OR REPLACE FUNCTION moduldeployment_reservasjon_vakt()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE v_innehaver TEXT; v_aktor TEXT; v_utloper TIMESTAMPTZ;
BEGIN
    SELECT r.innehaver, r.aktor, r.utloper_ts
      INTO v_innehaver, v_aktor, v_utloper
      FROM public.moduldeployment_reservasjon r
     WHERE r.modul_id = NEW.modul_id AND r.miljo = NEW.miljo
       AND r.utloper_ts > now();
    -- Ingen reservasjon (det normale) → porten er ikke der. En modul uten
    -- pågående drill merker ingenting til denne triggeren.
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;
    IF v_innehaver IS DISTINCT FROM
           current_setting('disponit.deployreservasjon', true) THEN
        -- TOKENET NAVNGIS IKKE (Codex P2, #117 runde 16). Å HOLDE
        -- innehaver-tokenet ER hele autorisasjonen her, og en GUC kan
        -- enhver kaller sette selv. Sa feilmeldingen hvilket token som
        -- gjelder, kunne den avviste overgangen bare settes på nytt med
        -- verdien den nettopp fikk utlevert — og porten var borte. Rollen
        -- som avvises har ingen lesetilgang til `innehaver`; da skal
        -- heller ikke feilen gi den bort. Det operatøren TRENGER er hvem
        -- som holder flaten og hvor lenge, ikke tokenet.
        RAISE EXCEPTION 'moduldeployment: (%, %) er reservert av en pågående'
            ' flippedrill (aktør %, utløper %). En overgang her ville'
            ' drenert dens rullbakk- eller kandidatdeployment midt i en'
            ' enveis, uigjentakelig måling — og drill-id-ene er brukt opp'
            ' uansett hva målingen ender med. Vent til drillen er ferdig,'
            ' eller presenter drillens eget token i'
            ' disponit.deployreservasjon.',
            NEW.modul_id, NEW.miljo, v_aktor, v_utloper
            USING ERRCODE = 'lock_not_available';
    END IF;
    RETURN NEW;
END $$;
-- DELETE er alt forbudt av `deployment_ingen_delete` (015), så INSERT og
-- UPDATE er hele skriveflaten.
DROP TRIGGER IF EXISTS deployment_reservasjon ON moduldeployment;
CREATE TRIGGER deployment_reservasjon
    BEFORE INSERT OR UPDATE ON moduldeployment
    FOR EACH ROW EXECUTE FUNCTION moduldeployment_reservasjon_vakt();

CREATE OR REPLACE FUNCTION ta_deployreservasjon(
    p_modul_id TEXT, p_miljo TEXT, p_innehaver TEXT, p_aktor TEXT,
    p_varighet INTERVAL)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_annen TEXT; v_utloper TIMESTAMPTZ;
BEGIN
    IF p_innehaver IS NULL OR btrim(p_innehaver) = '' THEN
        RAISE EXCEPTION 'ta_deployreservasjon: innehaver er obligatorisk —'
            ' en reservasjon uten innehaver kan ingen presentere'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_varighet IS NULL OR p_varighet <= interval '0 seconds' THEN
        RAISE EXCEPTION 'ta_deployreservasjon: varigheten må være positiv —'
            ' en reservasjon som er utløpt i det den tas, gjerder ingenting'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Samme modul-lås overgangene tar: reservasjonen kan ikke tas midt i
    -- et `bytt_release` som alt er i gang, og to samtidige forsøk på å ta
    -- den kan ikke begge lese «ledig».
    PERFORM pg_advisory_xact_lock(hashtextextended('modul:' || p_modul_id, 0));
    -- AKTØREN, ikke tokenet (Codex P2, #117 runde 16): den som avvises her
    -- holder ikke reservasjonen, og skal ikke få utlevert verdien som ER
    -- adgangen til å gå forbi vakten på `moduldeployment`.
    SELECT r.aktor, r.utloper_ts INTO v_annen, v_utloper
      FROM public.moduldeployment_reservasjon r
     WHERE r.modul_id = p_modul_id AND r.miljo = p_miljo
       AND r.utloper_ts > now() AND r.innehaver <> p_innehaver;
    IF FOUND THEN
        RAISE EXCEPTION 'ta_deployreservasjon: (%, %) er alt reservert'
            ' (aktør %, utløper %)',
            p_modul_id, p_miljo, v_annen, v_utloper
            USING ERRCODE = 'lock_not_available';
    END IF;
    -- En utløpt rad, eller vår egen fra et tidligere forsøk, ryddes: å ta
    -- den samme reservasjonen om igjen er idempotent, ikke en kollisjon.
    DELETE FROM public.moduldeployment_reservasjon r
     WHERE r.modul_id = p_modul_id AND r.miljo = p_miljo;
    INSERT INTO public.moduldeployment_reservasjon
        (modul_id, miljo, innehaver, aktor, utloper_ts)
    VALUES (p_modul_id, p_miljo, p_innehaver, p_aktor, now() + p_varighet);
END $$;

CREATE OR REPLACE FUNCTION frigi_deployreservasjon(
    p_modul_id TEXT, p_miljo TEXT, p_innehaver TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    -- Bare sin EGEN: en annens reservasjon frigis av utløpet, ikke av en
    -- kaller som gjerne vil deploye.
    DELETE FROM public.moduldeployment_reservasjon r
     WHERE r.modul_id = p_modul_id AND r.miljo = p_miljo
       AND r.innehaver = p_innehaver;
END $$;

-- Reservasjonen FORNYES mens innehaveren lever (Codex P2, #117 runde 16).
-- Et fast utløp alene er enten for kort (gjerdet faller mens drillen står
-- midt i en enveis måling, og en vanlig `bytt_release` drenerer den
-- drillede deploymenten) eller for langt (en drill som dør uten å frigi,
-- stenger modulen for deploy i timevis). Med fornyelse kan utløpet være
-- kort: det måler «innehaveren er borte», ikke «drillen er lang».
--
-- Bare en LEVENDE reservasjon kan forlenges, og bare av sin egen
-- innehaver. Er den først utløpt, KAN en vanlig deployment ha gått i
-- vinduet — å ta gjerdet opp igjen som om ingenting hadde skjedd ville
-- skjult nettopp det. Feilen er `lock_not_available`, den samme kalleren
-- ser når flaten er andres, og innehaveren skal avbryte på den.
--
-- Ingen modul-lås her, med vilje: fornyelsen rører bare sin egen rad, og
-- et hjerteslag som stiller seg i kø bak en lang `bytt_release` ville
-- mistet gjerdet mens det ventet på det.
CREATE OR REPLACE FUNCTION forleng_deployreservasjon(
    p_modul_id TEXT, p_miljo TEXT, p_innehaver TEXT, p_varighet INTERVAL)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    IF p_varighet IS NULL OR p_varighet <= interval '0 seconds' THEN
        RAISE EXCEPTION 'forleng_deployreservasjon: varigheten må være'
            ' positiv — en fornyelse som utløper i det den skrives,'
            ' gjerder ingenting'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.moduldeployment_reservasjon r
       SET utloper_ts = now() + p_varighet
     WHERE r.modul_id = p_modul_id AND r.miljo = p_miljo
       AND r.innehaver = p_innehaver AND r.utloper_ts > now();
    IF NOT FOUND THEN
        RAISE EXCEPTION 'forleng_deployreservasjon: (%, %) holdes ikke av %'
            ' — reservasjonen er utløpt eller overtatt, og en deployment'
            ' kan ha gått i vinduet. Innehaveren må avbryte, ikke gjerde'
            ' på nytt.', p_modul_id, p_miljo, p_innehaver
            USING ERRCODE = 'lock_not_available';
    END IF;
END $$;

-- Vakten er en INVOKER-funksjon som `moduldeployment_livslop` (014) og
-- eies av migrator som den: den leser bare reservasjonsraden, og den skal
-- ikke bære en fullmakt kalleren ikke har. Definerne under er noe annet —
-- de SKRIVER reservasjonen, og eies av modul_eier som resten av CP2.
ALTER FUNCTION ta_deployreservasjon(TEXT, TEXT, TEXT, TEXT, INTERVAL)
    OWNER TO disponit_modul_eier;
ALTER FUNCTION forleng_deployreservasjon(TEXT, TEXT, TEXT, INTERVAL)
    OWNER TO disponit_modul_eier;
ALTER FUNCTION frigi_deployreservasjon(TEXT, TEXT, TEXT)
    OWNER TO disponit_modul_eier;
SET LOCAL ROLE disponit_modul_eier;
REVOKE ALL ON FUNCTION ta_deployreservasjon(TEXT, TEXT, TEXT, TEXT, INTERVAL)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION forleng_deployreservasjon(TEXT, TEXT, TEXT, INTERVAL)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION frigi_deployreservasjon(TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ta_deployreservasjon(TEXT, TEXT, TEXT, TEXT, INTERVAL)
    TO disponit_modules_admin;
GRANT EXECUTE ON FUNCTION forleng_deployreservasjon(TEXT, TEXT, TEXT, INTERVAL)
    TO disponit_modules_admin;
GRANT EXECUTE ON FUNCTION frigi_deployreservasjon(TEXT, TEXT, TEXT)
    TO disponit_modules_admin;
RESET ROLE;
-- Vakten leser tabellen som den rollen som skriver `moduldeployment` —
-- definerne (`disponit_modul_eier`) og migrator, som eier begge.
-- UPDATE er fornyelsens skrivevei (`forleng_deployreservasjon`); den er
-- radbundet til innehaveren i funksjonen, ikke til rollen.
GRANT SELECT, INSERT, UPDATE, DELETE ON moduldeployment_reservasjon
    TO disponit_modul_eier;
