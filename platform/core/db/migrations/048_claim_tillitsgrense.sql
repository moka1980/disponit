-- 048 — claim-tillitsgrensen (#108) + kvorumsvilkåret R47-1.
--
-- FUNNET (#106→#108): hele planvindus-protokollen sto bak den ENE
-- runtimerollen — en kompromittert runtime kunne claime sitt eget
-- forfalte vindu og felle et forfalsket tick. 045 strammet BEVISKRAVET;
-- denne migrasjonen flytter RETTEN TIL Å CLAIME: claim/terminaliser/
-- frigi mister EXECUTE for `disponit` og gis kun til den nye
-- innloggingsrollen `disponit_plan_arbeider` — varselsender-modellen,
-- ordrett (egen rolle, egen DSN-cred, runtime uten EXECUTE; se
-- disponit-varselsender.service for begrunnelsen som ble malen).
--
-- DEPLOY-REKKEFØLGEN ER BINDENDE (klarsignal §2): rolle
-- (oppsett-postgresql.sh) → rettighetssteg (migrer.py) → DENNE
-- migrasjonen → DSN-cred (opp.sh) — og den verifiseres i
-- SP-10-prøvekjøringen mot bebodd base, ikke bare beskrives. Derfor
-- porten under: en base uten rollen skal stoppe HØYT og TIDLIG, med
-- oppskriften i feilmeldingen — aldri revoke runtime uten at avløseren
-- finnes.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                   WHERE rolname = 'disponit_plan_arbeider') THEN
        RAISE EXCEPTION '048: rollen disponit_plan_arbeider finnes ikke — '
            'kjør oppsett-postgresql.sh først (deploy-rekkefølgen i '
            '#108-klarsignalet §2 er bindende)';
    END IF;
END $$;

-- ------------------------------------------------------------
-- 1. Claimerens identitet: systemattestert, ikke selvrapportert (§3).
--    `claimet_av` skrives av defineren som `session_user` — rollen som
--    faktisk autentiserte, uten noe parameter kalleren kan lyve i.
--    Eksplisitt session_user og ikke current_user: inne i en definer er
--    current_user funksjonseieren. Roller kan ikke FK-refereres, så
--    dette er et DOKUMENTERT systemattestert snapshot (SP-§3) — den
--    sterkeste formen som finnes for akkurat denne identiteten.
--    Fencingen på claim_id er urørt.
-- ------------------------------------------------------------
ALTER TABLE bestillingsplan_vindu ADD COLUMN claimet_av NAME;

-- Levende claims fra FØR 048 ble holdt av runtimerollen — det er et
-- faktum om fortiden, og raden skal si det i stedet for å bryte
-- CHECK-en under. Engangsattestering gjort av migrasjonen selv, med
-- samme selvreverserende bro som 047 (FORCE RLS binder migratoren).
CREATE POLICY c108_bro ON bestillingsplan_vindu FOR ALL
    TO disponit_migrator USING (true) WITH CHECK (true);
UPDATE bestillingsplan_vindu SET claimet_av = 'disponit'
 WHERE tilstand = 'aktivt';
DO $$
DECLARE v_att INT; v_uten INT;
BEGIN  -- rapporten teller (port 16-disiplinen): hva overgangen faktisk tok
    SELECT count(*) FILTER (WHERE claimet_av IS NOT NULL),
           count(*) FILTER (WHERE tilstand = 'aktivt'
                            AND claimet_av IS NULL)
      INTO v_att, v_uten FROM bestillingsplan_vindu;
    RAISE NOTICE '048 vinduer: % attestert, % aktive uten holder',
        v_att, v_uten;
END $$;
DROP POLICY c108_bro ON bestillingsplan_vindu;
-- SP-10-regelen: masse-skriving → fyr utsatte kontroller nå.
SET CONSTRAINTS ALL IMMEDIATE;

-- Tilstandskomplettheten utvides (SP-5-totalt, port 9): aktivt krever
-- holder, ledig krever fravær; terminal BEHOLDER verdien som historikk
-- (og kan bære NULL for rader terminalisert før 048).
ALTER TABLE bestillingsplan_vindu DROP CONSTRAINT vindu_tilstand_komplett;
ALTER TABLE bestillingsplan_vindu ADD CONSTRAINT vindu_tilstand_komplett
  CHECK (
     (tilstand = 'ledig'    AND claim_id IS NULL
                            AND lease_utloper IS NULL
                            AND terminalisert_ts IS NULL
                            AND claimet_av IS NULL)
  OR (tilstand = 'aktivt'   AND claim_id IS NOT NULL
                            AND lease_utloper IS NOT NULL
                            AND terminalisert_ts IS NULL
                            AND claimet_av IS NOT NULL)
  OR (tilstand = 'terminal' AND terminalisert_ts IS NOT NULL));

-- Terminal er endelig for HELE raden (044) — også for den nye kolonnen:
-- hvem som holdt claimet da vinduet ble terminalisert er evidens.
DROP TRIGGER vindu_terminal_er_endelig ON bestillingsplan_vindu;
CREATE TRIGGER vindu_terminal_er_endelig
  BEFORE UPDATE ON bestillingsplan_vindu
  FOR EACH ROW WHEN (OLD.tilstand = 'terminal' AND (
        NEW.tilstand         IS DISTINCT FROM OLD.tilstand
     OR NEW.terminalisert_ts IS DISTINCT FROM OLD.terminalisert_ts
     OR NEW.claim_id         IS DISTINCT FROM OLD.claim_id
     OR NEW.claimet_av       IS DISTINCT FROM OLD.claimet_av
     OR NEW.lease_utloper    IS DISTINCT FROM OLD.lease_utloper
     OR NEW.vindu_slutt      IS DISTINCT FROM OLD.vindu_slutt
     OR NEW.vindu_start      IS DISTINCT FROM OLD.vindu_start
     OR NEW.plan_id          IS DISTINCT FROM OLD.plan_id
     OR NEW.tenant           IS DISTINCT FROM OLD.tenant))
  EXECUTE FUNCTION avvis_endring();

-- ------------------------------------------------------------
-- 2. Funksjonene: claim skriver holderen, frigi visker den. Kroppene er
--    de GJELDENDE (main 47995ea) med 048-leddene spleiset inn — samme
--    disiplin som 047: en invariant som ikke står her er droppet stille.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_m37_claimer;

CREATE OR REPLACE FUNCTION public.claim_planvindu(p_tenant text, p_plan uuid, p_vindu timestamp with time zone, p_lease_s integer)
 RETURNS TABLE(utfall text, claim_id uuid)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE v RECORD; v_claim UUID; v_status TEXT; v_forfall TIMESTAMPTZ;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'claim_planvindu');
    -- `w.tenant = p_tenant` er ikke pynt (Codex P1): kontekstsjekken over
    -- beviser bare hvem KALLEREN er, ikke at raden hører til den. Uten
    -- leddet kunne en kaller med gyldig egen kontekst oppgi en annen
    -- tenants plan-id og få raden. Se terminaliser_planvindu.
    SELECT * INTO v FROM public.bestillingsplan_vindu w
     WHERE w.plan_id = p_plan AND w.tenant = p_tenant
       AND w.vindu_start = p_vindu FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'ukjent'::text, NULL::uuid; RETURN;
    END IF;
    IF v.tilstand = 'terminal' THEN
        -- AVBRYT — ingen POST. Terminal er absorberende.
        RETURN QUERY SELECT 'terminal'::text, NULL::uuid; RETURN;
    END IF;
    -- UTLØPET SJEKKES HER, IKKE BARE I PLUKKET (Codex P1). Plukket
    -- returnerer en BATCH som arbeides ned sekvensielt, og hver
    -- bestilling er et HTTP-kall: en rad som var innenfor vinduet da
    -- batchen ble valgt, kan være minutter utenfor når turen kommer til
    -- den. Uten dette leddet ble et misset vindu til en INNHENTING —
    -- stikk i strid med §5s aldri-ta-igjen. Kontrollen må skje atomisk
    -- med selve claimet: alt annet er et tidsvindu mellom sjekk og bruk.
    -- Vinduet står `ledig` og klassifisereren feller `hoppet_over`.
    IF now() >= v.vindu_slutt THEN
        RETURN QUERY SELECT 'utlopt'::text, NULL::uuid; RETURN;
    END IF;
    IF v.tilstand = 'aktivt' AND v.lease_utloper > now() THEN
        RETURN QUERY SELECT 'aktivt'::text, NULL::uuid; RETURN;
    END IF;
    -- PLANENS TILSTAND REVALIDERES HER OGSÅ (Codex P1). Plukket
    -- kvalifiserte batchen i sin egen, committede transaksjon; radene
    -- arbeides ned sekvensielt med et HTTP-kall hver. Pauser eller stanser
    -- en administrator planen i mellomtiden, var det bare vindusraden som
    -- sto imot — og en STANSET plan kunne fortsatt konsumere en kvoteplass
    -- og starte en ekstern skanning. En stans er en menneskelig ordre om at
    -- planen ikke skal bestille mer; da må den gjelde fra det øyeblikket
    -- den committes, ikke fra neste sveip.
    --
    -- Planraden låses FOR SHARE, ikke bare leses: `pause_plan`,
    -- `stans_plan` og `gjenoppta_plan` tar alle FOR UPDATE på nettopp den
    -- raden først. Låsen er det som gjør revalideringen ATOMISK med
    -- claimet — uten den ville en pause som committer mellom lesningen og
    -- UPDATE-en under sluppet forbi. Låserekkefølgen er vindu → plan
    -- overalt her; ingen vei går motsatt vei.
    --
    -- Regelen er PLUKKETS EGEN, ikke en ny — og det er poenget: claimet
    -- skal aldri være strengere enn utvalget som ga det raden. Status
    -- `aktiv` fanger pausen og stansen; periodeleddet fanger et vindu
    -- planen aldri var aktiv for (aktivert etter forfall, port 32).
    -- Gjenopptas planen mens vinduet fortsatt er åpent, hører forfallet
    -- fortsatt til den perioden planen VAR aktiv i — lukket ved pausen,
    -- altså etter forfallet — og både plukket og claimet gir raden ut
    -- igjen. Det er riktig: vinduet er ikke utløpt, og gjenopptaket er
    -- nettopp en ordre om å kjøre igjen. Aldri-ta-igjen (§5) håndheves av
    -- `vindu_slutt` over, ikke her.
    SELECT b.status INTO v_status FROM public.bestillingsplan b
     WHERE b.plan_id = p_plan AND b.tenant = p_tenant FOR SHARE;
    v_forfall := p_vindu + public.plan_forfallsminutt(p_plan)
                           * interval '1 minute';
    IF v_status IS DISTINCT FROM 'aktiv'
       OR NOT EXISTS (SELECT 1 FROM public.bestillingsplan_aktiv_periode pr
                       WHERE pr.plan_id = p_plan
                         AND pr.fra_ts <= v_forfall
                         AND (pr.til_ts IS NULL OR pr.til_ts > v_forfall))
    THEN
        -- Vinduet står `ledig`; klassifisereren feller `hoppet_over` når
        -- det utløper. Ingen tick her — intet forsøk ble gjort.
        RETURN QUERY SELECT 'ikke_aktiv'::text, NULL::uuid; RETURN;
    END IF;
    v_claim := gen_random_uuid();
    UPDATE public.bestillingsplan_vindu w
       SET tilstand = 'aktivt', claim_id = v_claim,
           lease_utloper = now() + make_interval(secs =>
               least(greatest(p_lease_s, 30), 600)),
           -- 048 (§3): holderen er SYSTEMATTESTERT — session_user er
           -- rollen som faktisk logget inn, ikke et kallerargument, og
           -- ikke current_user (som i en definer er funksjonseieren).
           claimet_av = session_user
     WHERE w.plan_id = p_plan AND w.tenant = p_tenant
       AND w.vindu_start = p_vindu;
    RETURN QUERY SELECT 'claimet'::text, v_claim;
END $function$;

CREATE OR REPLACE FUNCTION public.frigi_planvindu(p_tenant text, p_plan uuid, p_vindu timestamp with time zone, p_claim uuid)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE v RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'frigi_planvindu');
    SELECT * INTO v FROM public.bestillingsplan_vindu w
     WHERE w.plan_id = p_plan AND w.tenant = p_tenant
       AND w.vindu_start = p_vindu FOR UPDATE;
    IF NOT FOUND THEN
        RETURN 'ukjent';
    END IF;
    IF v.tilstand = 'terminal' THEN
        RETURN 'terminal';
    END IF;
    IF v.claim_id IS DISTINCT FROM p_claim THEN
        RETURN 'ikke_ditt';
    END IF;
    UPDATE public.bestillingsplan_vindu w
       SET tilstand = 'ledig', claim_id = NULL, lease_utloper = NULL,
           claimet_av = NULL
     WHERE w.plan_id = p_plan AND w.tenant = p_tenant
       AND w.vindu_start = p_vindu;
    RETURN 'frigitt';
END $function$;

-- ------------------------------------------------------------
-- 3. EXECUTE-flyttet — selve tillitsgrensen. terminaliser_planvindu er
--    uendret i kropp (terminal beholder holderen), men grensen dens
--    flyttes sammen med claim og frigi: runtime mister alle tre.
--    Flate-CRUD-en (opprett/aktiver/pause/gjenoppta/stans/hent_*) blir
--    stående hos runtime — det er HTTP-flatens, ikke arbeiderens.
-- ------------------------------------------------------------
REVOKE EXECUTE ON FUNCTION claim_planvindu(TEXT, UUID, TIMESTAMPTZ, INT)
    FROM disponit;
REVOKE EXECUTE ON FUNCTION terminaliser_planvindu(TEXT, UUID, TIMESTAMPTZ,
    UUID, TEXT, TEXT, BIGINT, JSONB) FROM disponit;
REVOKE EXECUTE ON FUNCTION frigi_planvindu(TEXT, UUID, TIMESTAMPTZ, UUID)
    FROM disponit;
GRANT EXECUTE ON FUNCTION claim_planvindu(TEXT, UUID, TIMESTAMPTZ, INT)
    TO disponit_plan_arbeider;
GRANT EXECUTE ON FUNCTION terminaliser_planvindu(TEXT, UUID, TIMESTAMPTZ,
    UUID, TEXT, TEXT, BIGINT, JSONB) TO disponit_plan_arbeider;
GRANT EXECUTE ON FUNCTION frigi_planvindu(TEXT, UUID, TIMESTAMPTZ, UUID)
    TO disponit_plan_arbeider;
-- De gjenskapte kroppene trenger sine grants på nytt (REPLACE bevarer
-- ACL, men eksplisitt er kontrakten):
REVOKE ALL ON FUNCTION claim_planvindu(TEXT, UUID, TIMESTAMPTZ, INT)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION frigi_planvindu(TEXT, UUID, TIMESTAMPTZ, UUID)
    FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 4. R47-1: kvorumsvilkåret — hendelsen får sin EGEN kilde-kolonne.
--    Ratifiseringens skisse refererte `aktiveringskilde` på hendelsen,
--    men kolonnen bor på policyer (047:309) og CHECK kan ikke join'e —
--    kolonnen her er den ærlige formen (lesesvarets flagg 1). Backfill
--    via versjonsbindingen i samme migrasjon, så NOT NULL går.
-- ------------------------------------------------------------
ALTER TABLE policyaktivering ADD COLUMN aktiveringskilde TEXT;
-- ⚠️ DOKUMENTERT AVVIK FRA RATIFISERINGENS SKISSE (målt av SP-10-
-- prøvekjøringen mot bebodd base — skissen veltet på en EKTE rad):
-- «styrt krever attestant_b» ville både avvist historiske
-- enkeltattestant-hendelser og BRUTT fremtidige INNSNEVRER/NØYTRAL-
-- aktiveringer (kvorumet er 1 der — nøyaktig grunnen til at attestant_b
-- ble nullbar i 047, ratifisert). Lagringsporten ratifiseringen VIL ha
-- («kvorumet skal ikke bare bo i funksjonen») bygges i stedet med
-- rundens eget krav som referanse: hendelsen bærer `pakrevd_antall`,
-- FK-bundet til RUNDENS pakrevd_antall_godkjennere — så hendelsen kan
-- ikke lyve om hvor mange som krevdes — og CHECK-en krever attestanter
-- deretter. Det er sterkere enn skissen (kravet er bevist, ikke antatt)
-- og sant for begge kvorumklassene.
ALTER TABLE policyaktivering ADD COLUMN pakrevd_antall SMALLINT;
ALTER TABLE aktiveringsrunde ADD CONSTRAINT runde_kvorum_refererbar
  UNIQUE (tenant, utkast_id, runde, pakrevd_antall_godkjennere);
ALTER TABLE policyaktivering ADD CONSTRAINT hendelse_kvorum_fk
  FOREIGN KEY (tenant, utkast_id, runde, pakrevd_antall)
  REFERENCES aktiveringsrunde
      (tenant, utkast_id, runde, pakrevd_antall_godkjennere)
  DEFERRABLE INITIALLY DEFERRED;

-- Kopien er en engangsovergang som immutabilitetsvernet ellers (riktig)
-- ville nektet: vernet slås av og på igjen I SAMME transaksjon — selv-
-- reverserende, som broene. RLS-broen trengs av samme grunn som i 047.
ALTER TABLE policyaktivering DISABLE TRIGGER policyaktivering_immutabel;
CREATE POLICY r471_bro ON policyaktivering FOR ALL
    TO disponit_migrator USING (true) WITH CHECK (true);
CREATE POLICY r471_bro ON policyer FOR SELECT
    TO disponit_migrator USING (true);
CREATE POLICY r471_bro ON aktiveringsrunde FOR SELECT
    TO disponit_migrator USING (true);
UPDATE policyaktivering pa SET aktiveringskilde = p.aktiveringskilde
  FROM policyer p
 WHERE p.tenant = pa.tenant AND p.policy_id = pa.policy_id
   AND p.versjon = pa.versjon;
-- Kvorumskravet hentes fra RUNDEN — sannheten hendelsen alt er bundet
-- til; ingen gjetting.
UPDATE policyaktivering pa
   SET pakrevd_antall = ar.pakrevd_antall_godkjennere
  FROM aktiveringsrunde ar
 WHERE ar.tenant = pa.tenant AND ar.utkast_id = pa.utkast_id
   AND ar.runde = pa.runde;
-- En hendelse kan stå igjen etter at versjonsraden dens ble slettet
-- (slett_ubrukt_policy sletter policyer; hendelsen er immutabel).
-- Kvorumssemantikken dens kan ikke bevises i ettertid — 'historisk' er
-- den ærlige verdien: den PÅSTÅR ingenting om to attestanter.
UPDATE policyaktivering SET aktiveringskilde = 'historisk'
 WHERE aktiveringskilde IS NULL;
DO $$
DECLARE v_s INT; v_h INT; v_b INT; v_kv INT;
BEGIN  -- rapporten teller: bundet via versjonsbindingen, ikke gjetting
    SELECT count(*) FILTER (WHERE aktiveringskilde = 'styrt'),
           count(*) FILTER (WHERE aktiveringskilde = 'historisk'),
           count(*) FILTER (WHERE aktiveringskilde = 'bootstrap'),
           count(*) FILTER (WHERE pakrevd_antall IS NOT NULL)
      INTO v_s, v_h, v_b, v_kv FROM policyaktivering;
    RAISE NOTICE '048 hendelser: % styrt, % historisk, % bootstrap,'
        ' % med rundebundet kvorumskrav', v_s, v_h, v_b, v_kv;
END $$;
DROP POLICY r471_bro ON policyaktivering;
DROP POLICY r471_bro ON policyer;
DROP POLICY r471_bro ON aktiveringsrunde;
ALTER TABLE policyaktivering ENABLE TRIGGER policyaktivering_immutabel;
-- SP-10-regelen: masse-skriving → fyr utsatte kontroller nå.
SET CONSTRAINTS ALL IMMEDIATE;

ALTER TABLE policyaktivering
  ALTER COLUMN aktiveringskilde SET NOT NULL;
ALTER TABLE policyaktivering
  ADD CONSTRAINT hendelse_kilde_gyldig CHECK
    (aktiveringskilde IN ('styrt', 'historisk', 'bootstrap'));
-- SP-5-totalformen fra ratifiseringen — inkludert den negative porten:
-- NULL kilde er alt umulig (NOT NULL), og CHECK-en er skrevet så en
-- NULL aldri kunne sluppet gjennom som «vet ikke, derfor tillatt».
-- ⚠️ ANDRE MÅLING FRA SP-10-PRØVEKJØRINGEN: heller ikke «pakrevd < 2
-- OR attestant_b» er sant — pakrevd teller ALLE attestasjoner
-- (forfatteren inkludert, V6: «forfatter kan være én, aldri begge»),
-- mens hendelsen registrerer de KVALIFISERENDE (ikke-forfatter). En
-- UTVIDER-runde attestert av forfatter + én uavhengig er lovlig, har
-- pakrevd = 2 og NØYAKTIG ÉN kvalifiserende attestant. Ingen CHECK på
-- hendelsesraden alene kan telle attestasjonsradene — men SP-9s andre
-- gyldige form kan: TRIGGER VED ETABLERING pluss immutabilitet.
-- Attestasjonene er append-only (012) og FK-nøkkelen holder
-- kvalifikasjonen varig, så kvorumet målt ved INSERT forblir sant.
-- CHECK-en under beholder det raden selv KAN love (NULL-sikret, SP-5).
ALTER TABLE policyaktivering
  ADD CONSTRAINT hendelse_styrt_krever_kvorum CHECK (
    aktiveringskilde IS NOT NULL
    AND (   (aktiveringskilde = 'styrt'
             AND pakrevd_antall IS NOT NULL
             AND attestant_a IS NOT NULL)
         OR (aktiveringskilde IN ('historisk', 'bootstrap'))));

-- Kvorumsgaten i lagringen: V6-regelen ordrett — antall attestasjoner
-- på runden (med rundens diff) >= kravet hendelsen selv bærer
-- (FK-bundet mot runden), og minst én er ikke-forfatter. Fyrer ved
-- etableringen av en STYRT hendelse; historisk/bootstrap er ærlig
-- ufullstendige og fritatt.
CREATE OR REPLACE FUNCTION hendelse_kvorum_gate() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE v_total INT; v_uavh INT; v_diff TEXT;
BEGIN
    IF NEW.aktiveringskilde IS DISTINCT FROM 'styrt' THEN
        RETURN NEW;
    END IF;
    SELECT ar.diff_hash INTO v_diff FROM public.aktiveringsrunde ar
     WHERE ar.tenant = NEW.tenant AND ar.utkast_id = NEW.utkast_id
       AND ar.runde = NEW.runde;
    SELECT count(*), count(*) FILTER (WHERE NOT a.er_forfatter)
      INTO v_total, v_uavh
      FROM public.aktiveringsattestasjon a
     WHERE a.tenant = NEW.tenant AND a.utkast_id = NEW.utkast_id
       AND a.runde = NEW.runde AND a.diff_hash = v_diff;
    IF v_total < NEW.pakrevd_antall OR v_uavh < 1 THEN
        RAISE EXCEPTION 'policyaktivering: styrt hendelse uten kvorum '
            '(% attestasjoner, % uavhengige, krav %)', v_total, v_uavh,
            NEW.pakrevd_antall
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'hendelse_kvorum_gate';
    END IF;
    RETURN NEW;
END $$;
ALTER FUNCTION hendelse_kvorum_gate() OWNER TO disponit_policy_eier;
CREATE TRIGGER hendelse_kvorum_gate
  BEFORE INSERT ON policyaktivering
  FOR EACH ROW EXECUTE FUNCTION hendelse_kvorum_gate();

-- ------------------------------------------------------------
-- 5. aktiver_policy — hele kroppen erstattes (047-disiplinen): dumpen av
--    den GJELDENDE (main 47995ea) med R47-1-leddet spleiset inn.
--    DROP som migrator via skjemaeierskapet (031-presedensen), CREATE,
--    ALTER OWNER, grants i eiervinduet.
-- ------------------------------------------------------------
DROP FUNCTION IF EXISTS aktiver_policy(TEXT, TEXT, INT, TEXT);

CREATE OR REPLACE FUNCTION public.aktiver_policy(p_tenant text, p_utkast_id text, p_runde integer, p_base_versjon text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE
    v_policy_id     TEXT;
    v_innhold       JSONB;
    v_innholds_hash TEXT;
    v_ustatus       TEXT;
    v_rstatus       TEXT;
    v_diff_hash     TEXT;
    v_pakrevd       INT;
    v_utloper       TIMESTAMPTZ;
    v_opid          TEXT;
    v_total         INT;
    v_uavhengige    INT;
    v_diff_avvik    INT;
    v_aktiv         TEXT;
    v_ny            TEXT;
    v_ny_ledd       TEXT[];
    v_aktiv_ledd    TEXT[];
    v_bredde        INT;
    v_dok           JSONB;
    v_ugyldig_id    TEXT;
    v_ulesbar_ref   TEXT;
    v_uanvendelig   TEXT;
    v_dok_pid       TEXT;
    v_dok_status    TEXT;
    v_att_a         TEXT;
    v_att_b         TEXT;
    v_i             INT;
    v_a             TEXT;
    v_b             TEXT;
    v_nyere         BOOLEAN;
BEGIN
    -- 1. Utkastet — låst. Innholdet som aktiveres kommer HERFRA, ikke fra
    --    kalleren (så det som aktiveres er nøyaktig det som ble attestert).
    SELECT policy_id, innhold, innholds_hash, status
      INTO v_policy_id, v_innhold, v_innholds_hash, v_ustatus
      FROM public.policyutkast
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'aktiver_policy: ukjent utkast %', p_utkast_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_ustatus NOT IN ('validert', 'godkjent') THEN
        RAISE EXCEPTION 'aktiver_policy: utkast % er ikke aktiverbart (status=%)',
            p_utkast_id, v_ustatus USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_innholds_hash IS NULL THEN
        RAISE EXCEPTION 'aktiver_policy: utkast % mangler frosset innholds_hash',
            p_utkast_id USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- 1b. IDENTITETEN (se toppen): dokumentet må bære den policyen raden
    --     aktiveres under. Ellers indekseres innholdet under én id mens motoren
    --     bygger beslutningens policyreferanse fra en annen.
    v_dok_pid := v_innhold -> 'meta' ->> 'policy_id';
    IF v_dok_pid IS DISTINCT FROM v_policy_id THEN
        RAISE EXCEPTION 'aktiver_policy: utkast % bærer meta.policy_id %, men '
            'aktiveres under %', p_utkast_id, coalesce(v_dok_pid, '<null>'),
            v_policy_id
            USING ERRCODE = 'check_violation', CONSTRAINT = 'dokument_policy_id';
    END IF;

    -- 1c. STATUSEN (se toppen): raden skrives som `produksjon` i steg 5, og
    --     `hent_aktiv` krever at dokumentet sier det samme. Et utkast merket
    --     `utkast`/`validert_pilot` ville derfor blitt aktivert — og deretter
    --     avvist som korrupt av hver eneste beslutning.
    v_dok_status := v_innhold -> 'meta' ->> 'status';
    IF v_dok_status IS DISTINCT FROM 'produksjon' THEN
        RAISE EXCEPTION 'aktiver_policy: utkast % har meta.status %, men '
            'aktivering skriver produksjon', p_utkast_id,
            coalesce(v_dok_status, '<null>')
            USING ERRCODE = 'check_violation', CONSTRAINT = 'dokument_status';
    END IF;

    -- 2. Runden — låst. Må være aktiverbar og ikke allerede brukt.
    SELECT status, diff_hash, pakrevd_antall_godkjennere, utloper,
           decision_operation_id
      INTO v_rstatus, v_diff_hash, v_pakrevd, v_utloper, v_opid
      FROM public.aktiveringsrunde
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id AND runde = p_runde
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'aktiver_policy: ukjent runde %/%', p_utkast_id, p_runde
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_opid IS NOT NULL OR v_rstatus NOT IN ('apen', 'klar') THEN
        RAISE EXCEPTION 'aktiver_policy: runde %/% er ikke aktiverbar (status=%)',
            p_utkast_id, p_runde, v_rstatus USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_utloper <= now() THEN
        RAISE EXCEPTION 'aktiver_policy: runde %/% er utløpt', p_utkast_id, p_runde
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- 3. FIRE-ØYNE (V6), håndhevet i funksjonen: antall ≥ påkrevd, minst én
    --    ikke-forfatter, og HVER attestasjon bandt rundens diff. Et direkte
    --    kall uten en tilstrekkelig attestert runde avvises her.
    SELECT count(*),
           count(*) FILTER (WHERE NOT er_forfatter),
           count(*) FILTER (WHERE diff_hash IS DISTINCT FROM v_diff_hash)
      INTO v_total, v_uavhengige, v_diff_avvik
      FROM public.aktiveringsattestasjon
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id AND runde = p_runde;
    IF v_total < v_pakrevd THEN
        RAISE EXCEPTION 'aktiver_policy: for få godkjennere (% < %) for %/%',
            v_total, v_pakrevd, p_utkast_id, p_runde
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF v_uavhengige < 1 THEN
        RAISE EXCEPTION 'aktiver_policy: ingen uavhengig godkjenner for %/%',
            p_utkast_id, p_runde USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF v_diff_avvik > 0 THEN
        RAISE EXCEPTION 'aktiver_policy: % attestasjon(er) bandt ikke rundens '
            'diff for %/%', v_diff_avvik, p_utkast_id, p_runde
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- 3b. HENDELSENS ATTESTANTER (047): de KVALIFISERENDE radene — ikke
    --     forfatter, rundens diff — i attestasjonsrekkefølge (ts, id).
    --     Gaten over garanterer minst én (v_uavhengige >= 1); to når to
    --     finnes. Kvorumet håndheves i steg 3 — hendelsen er EVIDENSEN
    --     for hva som kvalifiserte, ikke gaten (dokumentert avvik, se
    --     migrasjonshodet i 047).
    SELECT min(q.bruker_id) FILTER (WHERE q.rn = 1),
           min(q.bruker_id) FILTER (WHERE q.rn = 2)
      INTO v_att_a, v_att_b
      FROM (SELECT a.bruker_id,
                   row_number() OVER (ORDER BY a.ts, a.id) AS rn
              FROM public.aktiveringsattestasjon a
             WHERE a.tenant = p_tenant AND a.utkast_id = p_utkast_id
               AND a.runde = p_runde AND NOT a.er_forfatter
               AND a.diff_hash = v_diff_hash) q
     WHERE q.rn <= 2;

    -- 4. Lås ankerraden (V1). Finnes den ikke, opprett den (onboarding).
    INSERT INTO public.policy_hode (tenant, policy_id)
        VALUES (p_tenant, v_policy_id)
        ON CONFLICT (tenant, policy_id) DO NOTHING;
    SELECT aktiv_versjon INTO v_aktiv
      FROM public.policy_hode
     WHERE tenant = p_tenant AND policy_id = v_policy_id
       FOR UPDATE;

    -- Konfliktdeteksjon (§4): den godkjennerne diffet mot MÅ fortsatt være
    -- aktiv. En konkurrerende aktivering flyttet pekeren → rebasering.
    IF v_aktiv IS DISTINCT FROM p_base_versjon THEN
        RAISE EXCEPTION 'aktiver_policy: base % er ikke lenger aktiv (%) — '
            'rebasering kreves', p_base_versjon, v_aktiv
            USING ERRCODE = 'serialization_failure';
    END IF;

    -- 4b. VERSJONEN LESES FRA DOKUMENTET (se 020). Kontrollene under kjøres
    --     med hoderaden låst, så ingen annen STYRT aktivering kan legge seg
    --     imellom dette og INSERT-en i steg 5.
    v_ny := v_innhold -> 'meta' ->> 'versjon';
    -- Formen OG plassen. `policyer_pkey` er (tenant, policy_id, versjon), og en
    -- btree-oppføring har et hardt tak (~2704 byte) som de tre DELER — så ingen
    -- av dem er trygg målt for seg. Verken `policy_id` eller versjonen har noen
    -- maks i skjemaet, og uten dette ville en for stor nøkkel passert hit og
    -- først veltet på INSERT-en i steg 5, som `program_limit_exceeded`: en
    -- uhåndtert 500 etter at godkjennerne hadde signert. `octet_length` måler
    -- nøyaktig det btree teller. Porten håndhever samme tall
    -- (`policyadmin._MAKS_NOKKELBYTES`); dette er siste skanse.
    IF v_ny IS NULL OR v_ny !~ '^[0-9]+\.[0-9]+\.[0-9]+$'
       OR octet_length(p_tenant) + octet_length(v_policy_id)
          + octet_length(v_ny) > 2400 THEN
        RAISE EXCEPTION 'aktiver_policy: utkast % mangler brukbar meta.versjon '
            '(nøkkel % byte)', p_utkast_id,
            octet_length(p_tenant) + octet_length(v_policy_id)
            + coalesce(octet_length(v_ny), 0)
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM public.policyer
                WHERE tenant = p_tenant AND policy_id = v_policy_id
                  AND versjon = v_ny) THEN
        RAISE EXCEPTION 'aktiver_policy: versjon % er allerede registrert for '
            '%/%', v_ny, p_tenant, v_policy_id USING ERRCODE = 'check_violation';
    END IF;
    -- Monotoni: kun når den aktive versjonen selv er tallpunktet. Eldre rader
    -- (registrert før PR-013) kan bære hva som helst i TEXT-kolonnen, og en
    -- versjon vi ikke kan lese som tall, kan vi heller ikke måle mot.
    --
    -- Leddene NULLPADDES til samme bredde FØR sammenligningen (se 021): uten
    -- det slår «2.0.0» en aktiv «2» — likt prefiks, flest ledd vinner — og en
    -- aktiv «2» fra den gamle telleren ville sluppet gjennom nøyaktig den
    -- versjonen den allerede bærer, som ikke er en nyere versjon i det hele
    -- tatt. Paddet til samme bredde er «2» det den betyr: 2.0.0.
    IF v_aktiv IS NOT NULL AND v_aktiv ~ '^[0-9]+(\.[0-9]+)*$' THEN
        v_ny_ledd    := string_to_array(v_ny, '.');
        v_aktiv_ledd := string_to_array(v_aktiv, '.');
        v_bredde := greatest(array_length(v_ny_ledd, 1),
                             array_length(v_aktiv_ledd, 1));
        v_ny_ledd := (v_ny_ledd
                      || array_fill('0'::text, ARRAY[v_bredde]))[1:v_bredde];
        v_aktiv_ledd := (v_aktiv_ledd
                      || array_fill('0'::text, ARRAY[v_bredde]))[1:v_bredde];
        -- INGEN tallcast (se toppen): både `int` og `numeric` har et tak, og
        -- skjemaet har ingen. Leddene måles som (antall sifre, sifrene) med
        -- innledende nuller strøket — nøyaktig tallorden for ikke-negative
        -- heltall, uten øvre grense. `COLLATE "C"` gjør at teksten sorterer på
        -- sifrene selv, ikke etter en lokaltilpasset kollasjon.
        v_nyere := NULL;                       -- NULL = like så langt
        FOR v_i IN 1..v_bredde LOOP
            v_a := ltrim(v_ny_ledd[v_i], '0');
            v_b := ltrim(v_aktiv_ledd[v_i], '0');
            IF length(v_a) <> length(v_b) THEN
                v_nyere := length(v_a) > length(v_b);
                EXIT;
            ELSIF v_a COLLATE "C" <> v_b THEN
                v_nyere := v_a COLLATE "C" > v_b;
                EXIT;
            END IF;
        END LOOP;
        -- `IS NOT TRUE` dekker begge nei-ene: leddene var like hele veien
        -- (v_nyere = NULL, altså SAMME versjon) eller den nye lå under.
        IF v_nyere IS NOT TRUE THEN
            RAISE EXCEPTION 'aktiver_policy: versjon % er ikke nyere enn '
                'aktiv % (%/%)', v_ny, v_aktiv, p_tenant, v_policy_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    -- 4c. INNFØRINGSKONTRAKTEN (022, Codex P2 på #63): verifikator-id-en er
    --     den eneste FRIE nøkkelen i policyen, og den havner UTOLKET i
    --     diffstien godkjenneren attesterer (`verifikatorer.<id>.<felt>`).
    --     Med id-ene `foo` og `foo.beskrivelse` er `verifikatorer.foo.
    --     beskrivelse` både beskrivelsen til den ene og roten til den andre,
    --     og attestasjonen kan tilskrive en tillitsendring FEIL verifikator.
    --     En tom id gir stien `verifikatorer.` og et blad uten eier.
    --
    --     Python stiller kravet ved runde-åpning og attestasjon, men begge
    --     kan være passert før utrullingen — og et direkte kall hit går
    --     utenom dem. Speiler `schema._valider_innforing`: KUN de to tegnene
    --     som skaper flertydigheten, ikke husmønsteret, og ikke resten av
    --     lastekontrakten (den er bakoverkompatibel og sier ingenting nytt).
    v_dok := CASE WHEN jsonb_typeof(v_innhold) = 'object'
                  THEN v_innhold ELSE '{}'::jsonb END;
    SELECT string_agg(format('%s: %L', k.felt, k.vid), ', '
                      ORDER BY k.felt, k.vid)
      INTO v_ugyldig_id
      FROM (SELECT f.felt, o.vid
              FROM (VALUES ('verifikatorer'), ('verifikator_prioritet'))
                        AS f(felt)
              CROSS JOIN LATERAL jsonb_object_keys(
                  CASE WHEN jsonb_typeof(v_dok -> f.felt) = 'object'
                       THEN v_dok -> f.felt ELSE '{}'::jsonb END) AS o(vid)
             WHERE o.vid = '' OR position('.' in o.vid) > 0
                             OR position('[' in o.vid) > 0) AS k;
    --     `CONSTRAINT` settes bevisst: orkestreringen fanger check_violation
    --     fra denne funksjonen og har til nå kunnet anta at det var
    --     VERSJONEN (020). Uten et strukturert skille måtte den lest
    --     feilteksten for å vite forskjellen, og eier ville fått «versjonen er
    --     i bruk» om en id. `diag.constraint_name` er den maskinlesbare
    --     kanalen for nettopp det.
    IF v_ugyldig_id IS NOT NULL THEN
        RAISE EXCEPTION 'aktiver_policy: utkast % har verifikator-id som gjør '
            'diffstien flertydig (%)', p_utkast_id, v_ugyldig_id
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'verifikator_id_entydig';
    END IF;
    --     BÆRES VIDERE: denne migrasjonen erstatter hele kroppen til
    --     `aktiver_policy`, så en invariant som ikke står her er droppet
    --     — stille. 022 og denne kom hver sin vei inn i samme funksjon.

    -- 4d. POLICYREFERANSEN MÅ VÆRE LESBAR (Codex P2 på denne PR-en).
    --     `_valider_innforing` fikk ankerkravet — mønstrene målt med ECMA-262
    --     sin `$` i stedet for Pythons, som også matcher rett FØR en
    --     avsluttende linjeskift. Den porten står i Python, og har samme to
    --     hull som verifikator-id-kravet over: et utkast som ble validert og
    --     fullt attestert FØR utrullingen bærer statusen sin videre hit, og
    --     runtime-rollen kan kalle denne funksjonen direkte.
    --
    --     Utfallet uten gaten er ikke en pen feil. `engine.les_policyref`
    --     leser `<policy_id>@<versjon>/<handling>` med `fullmatch`, altså
    --     ekte slutt. Et `handlinger[].id` = 'foo.bar' + linjeskift blir
    --     aktivert her og gjør referansen ULESBAR i motoren: beslutningen
    --     produserer evidens uten policyidentitet, og det oppdages først
    --     etter at policyen er i produksjon.
    --
    --     OMFANGET er de tre feltene referansen bygges av — det er nøyaktig
    --     de feltene bruddet forplanter seg gjennom. `meta.versjon` er alt
    --     dekket i 4b (PostgreSQLs `~` leser `$` som ekte slutt), så det som
    --     står igjen er identiteten og handlings-id-ene.
    --
    --     Og som `_pattern_ecma`: KUN DIFFERANSEN mot lastekontrakten. En
    --     verdi som feiler BEGGE lesningene — en handlings-id uten punktum,
    --     f.eks. — er en helt vanlig skjemafeil som lastekontrakten sier fra
    --     om ved validering. Reiste vi den her, ville en runde blitt
    --     KANSELLERT med «bryter et nytt krav» for et dokument som ganske
    --     enkelt er strukturelt ødelagt, og det er sammenblandingen de to
    --     kontraktene ble delt for å unngå. Differansen er derfor målt
    --     eksplisitt: verdien har en avsluttende linjeskift (som PostgreSQL
    --     avviser og Python slipper gjennom), og resten matcher mønsteret.
    SELECT string_agg(format('%s: %L', x.felt, x.verdi), ', '
                      ORDER BY x.felt, x.verdi)
      INTO v_ulesbar_ref
      FROM (SELECT 'meta.policy_id'::TEXT AS felt,
                   v_policy_id            AS verdi,
                   '^[a-z0-9-]+$'::TEXT   AS monster
            UNION ALL
            SELECT 'handlinger[].id', e.el ->> 'id',
                   '^[a-z0-9_]+(\.[a-z0-9_]+)+$'
              FROM jsonb_array_elements(
                       CASE WHEN jsonb_typeof(v_dok -> 'handlinger') = 'array'
                            THEN v_dok -> 'handlinger'
                            ELSE '[]'::jsonb END) AS e(el)
             WHERE jsonb_typeof(e.el) = 'object') AS x
     WHERE x.verdi IS NOT NULL
       AND right(x.verdi, 1) = E'\n'
       -- Pythons `$` godtar ÉN avsluttende linjeskift, ikke flere: matcher
       -- resten mønsteret, er dette presis den strengen lastekontrakten
       -- slapp gjennom og databasen aldri kunne lest.
       AND left(x.verdi, length(x.verdi) - 1) ~ x.monster;
    IF v_ulesbar_ref IS NOT NULL THEN
        RAISE EXCEPTION 'aktiver_policy: utkast % har felt som gjør '
            'policyreferansen ulesbar (%)', p_utkast_id, v_ulesbar_ref
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'policyref_lesbar';
    END IF;

    -- 4e. OVERSTYRINGEN MÅ KUNNE ANVENDES (Codex P2 på denne PR-en).
    --     `schema._overstyring_kan_anvendes` avviser en `godkjennbare`-
    --     oppføring motoren aldri kan løfte: ikke-løftbar grunnkode, manglende
    --     verdi, eller en verdi som ikke flytter noe blokkert utfall. Den
    --     porten står i Python, og har de samme to hullene som 4c og 4d: en
    --     runde validert og attestert FØR utrullingen bærer statusen sin
    --     videre hit, og runtime-rollen har EXECUTE på denne funksjonen.
    --     Uten gaten kan altså en virkningsløs overstyring aktiveres — og
    --     utfallet er stille: policyen SER konfigurert ut, mens hver
    --     matchende godkjenning ender i STOPP.
    --
    --     OMFANGET er det SQL kan måle EKSAKT, som i 4d. Enumerasjonen av
    --     løftbare koder, det påkrevde feltet, modusen som feller før
    --     grensene i det hele tatt vurderes, en grense som ikke finnes, og
    --     valutamedlemskap er alle eksakte prøver. Beløpssammenligningen
    --     gjøres kun når BEGGE verdiene er lesbare tall — er de ikke det, er
    --     dommen `belop_ugyldig` og tilhører lastekontrakten, nøyaktig som i
    --     `_loftet_flytter_noe`. Ukjent handling måles ikke her heller.
    --
    --     ÉN KILDE, TO SPRÅK: `test_sql_gaten_kjenner_de_samme_loftbare_kodene`
    --     måler enumerasjonen og modusnavnet her mot `engine`, så en ny
    --     løftbar kode ikke kan legges til i Python alene.
    WITH oppf AS (
        SELECT (e.ord - 1)         AS i,
               e.el                AS post,
               e.el ->> 'grunnkode' AS gk,
               h.el                AS handling
          FROM jsonb_array_elements(
                   CASE WHEN jsonb_typeof(
                            v_dok -> 'menneskelig_overstyring'
                                  -> 'godkjennbare') = 'array'
                        THEN v_dok -> 'menneskelig_overstyring'
                                   -> 'godkjennbare'
                        ELSE '[]'::jsonb END) WITH ORDINALITY AS e(el, ord)
          LEFT JOIN LATERAL (
              SELECT hh.el FROM jsonb_array_elements(
                       CASE WHEN jsonb_typeof(v_dok -> 'handlinger') = 'array'
                            THEN v_dok -> 'handlinger'
                            ELSE '[]'::jsonb END) AS hh(el)
               WHERE hh.el ->> 'id' = e.el ->> 'handling'
               LIMIT 1) AS h ON true
         WHERE jsonb_typeof(e.el) = 'object'
           AND jsonb_typeof(e.el -> 'grunnkode') = 'string'
    ),
    -- MÅLT FØRST, DØMT ETTERPÅ. Et cast eller en `jsonb_array_length` som
    -- står som et AND-ledd ved siden av vakten sin, er ikke vernet: SQL
    -- lover ingen venstre-mot-høyre-evaluering av AND, så en ulesbar verdi
    -- kunne veltet aktiveringen med «invalid input syntax for numeric» i
    -- stedet for å bli hoppet over. `CASE` lover derimot at THEN-armen
    -- bare evalueres når WHEN holder, og `MATERIALIZED` hindrer at CTE-en
    -- flates inn i dommen under og mister nettopp den rekkefølgen.
    -- OG LESBAR BETYR «ET TALL DENNE BASEN KAN BÆRE» (Codex P2). Mønsteret
    -- alene sier bare at tegnene er sifre: en sifferstreng lengre enn
    -- `NUMERIC` kan representere passerte det, og castet feilet da med
    -- `numeric_value_out_of_range`. Den koden er ikke et av
    -- aktiveringsutfallene kalleren håndterer, så en ferdig attestert
    -- fire-øyne-runde endte i 500 — for et dokument, ikke for en tilstand.
    -- Lengdeprøven står FORAN mønsteret og inne i samme CASE, så castet
    -- fortsatt bare evalueres når begge holder.
    --
    -- Tallet er ingen gyldighetsregel; skjemaet eier den (og kapper nå
    -- `belop_maks` på 18 sifre). Dette er punktet der «vi kan lese det»
    -- slutter — langt over ethvert beløp og langt under `NUMERIC` sitt
    -- eget tak, så castet ikke kan velte uansett hva som står der.
    maalt AS MATERIALIZED (
        SELECT o.i, o.gk, o.post, o.handling,
               CASE WHEN length(o.post ->> 'belop_maks') <= 1000
                     AND (o.post ->> 'belop_maks')
                         ~ '^-?[0-9]+(\.[0-9]+)?$'
                    THEN (o.post ->> 'belop_maks')::NUMERIC END AS e_maks,
               CASE WHEN length(o.handling -> 'grenser' ->> 'belop_maks')
                          <= 1000
                     AND (o.handling -> 'grenser' ->> 'belop_maks')
                         ~ '^-?[0-9]+(\.[0-9]+)?$'
                    THEN (o.handling -> 'grenser' ->> 'belop_maks')::NUMERIC
                    END AS h_maks,
               -- EN TOM LISTE ER INGEN LISTE (Codex P2). Python måler
               -- `isinstance(v, list) and v` — en tom `grenser.valuta` er
               -- falsy og teller som fravær, og `_evaluer` hopper over
               -- valutaprøven for den, så `valuta_ikke_tillatt` kan aldri
               -- oppstå. SQL-en spurte bare om typen, og slapp da den
               -- uanvendelige overstyringen gjennom for `[]`. Tomheten
               -- måles ÉN gang, her, så de tre bruksstedene under ikke kan
               -- svare ulikt om samme rad.
               --
               -- Nestet CASE, ikke et AND-ledd: `jsonb_array_length` på
               -- noe som ikke er en array kaster, og SQL lover ingen
               -- venstre-mot-høyre-evaluering av AND. THEN-armen
               -- evalueres derimot bare når WHEN holder.
               CASE WHEN jsonb_typeof(o.handling -> 'grenser' -> 'valuta')
                         = 'array'
                    THEN CASE WHEN jsonb_array_length(
                                       o.handling -> 'grenser' -> 'valuta') > 0
                              THEN o.handling -> 'grenser' -> 'valuta' END
                    END AS h_valuta
          FROM oppf o
    ), dom AS (
        SELECT o.i, o.gk,
               CASE
                 -- Ikke-løftbar kode: `_loft_policy` uttrykker bare disse to.
                 WHEN o.gk NOT IN ('belop_over_grense', 'valuta_ikke_tillatt')
                   THEN 'grunnkoden kan ikke løftes av motoren'
                 -- Verdien grunnkoden krever mangler. JSON-`null` teller
                 -- som fravær, som `e.get(felt) is None` i Python.
                 WHEN o.gk = 'belop_over_grense'
                      AND coalesce(jsonb_typeof(o.post -> 'belop_maks'),
                                   'null') = 'null'
                   THEN 'mangler ''belop_maks'''
                 WHEN o.gk = 'valuta_ikke_tillatt'
                      AND coalesce(jsonb_typeof(o.post -> 'valuta'),
                                   'null') = 'null'
                   THEN 'mangler ''valuta'''
                 -- Ukjent handling er lastekontraktens dom, ikke vår.
                 WHEN o.handling IS NULL THEN NULL
                 -- Modusen feller i steg 2, før grensene vurderes.
                 WHEN coalesce(o.handling ->> 'modus', 'alltid_stopp')
                      = 'alltid_stopp'
                   THEN 'handlingen har modus ''alltid_stopp'''
                 -- ROLLEN FELLER I STEG 3, like foran grensene (Codex P1).
                 -- `_evaluer` måler `aktor_rolle NOT IN tillatt_for`; er
                 -- lista tom eller fraværende, er den prøven usann for
                 -- ENHVER rolle, og ingen løftbar kode kan oppstå. Bare
                 -- det utvetydige fraværet felles — en `tillatt_for` som
                 -- ikke er en liste er lastekontraktens dom, som i Python.
                 WHEN (o.handling -> 'tillatt_for' IS NULL
                       OR jsonb_typeof(o.handling -> 'tillatt_for') = 'null'
                       OR (jsonb_typeof(o.handling -> 'tillatt_for') = 'array'
                           AND NOT EXISTS (
                               SELECT 1 FROM jsonb_array_elements(
                                          o.handling -> 'tillatt_for') AS r(el)
                                -- `#>> '{}'` er teksten i en jsonb-SKALAR;
                                -- `->> 0` ville vært en array-indeks.
                                WHERE jsonb_typeof(r.el) = 'string'
                                  AND (r.el #>> '{}') <> '')))
                   THEN 'handlingen har ingen rolle i ''tillatt_for'''
                 WHEN o.gk = 'belop_over_grense'
                      AND o.handling -> 'grenser' -> 'belop_maks' IS NULL
                   THEN 'handlingen har ingen ''grenser.belop_maks'''
                 -- «Ingen» dekker både fraværet, en ikke-liste og den TOMME
                 -- lista: `maalt` har alt slått dem sammen, og de betyr det
                 -- samme for motoren.
                 WHEN o.gk = 'valuta_ikke_tillatt' AND o.h_valuta IS NULL
                   THEN 'handlingen har ingen ''grenser.valuta'''
                 -- Taket må ligge OVER handlingens egen grense; ellers er
                 -- hvert blokkert beløp også over taket. Er en av verdiene
                 -- ULESBAR, er dommen `belop_ugyldig` og tilhører
                 -- lastekontrakten — da måler vi ingenting, som i Python.
                 WHEN o.gk = 'belop_over_grense'
                      AND o.e_maks IS NOT NULL AND o.h_maks IS NOT NULL
                      AND o.e_maks <= o.h_maks
                   THEN 'taket er ikke høyere enn handlingens egen grense'
                 -- Løftet hever beløpet, ikke valutaen.
                 WHEN o.gk = 'belop_over_grense'
                      AND o.h_valuta IS NOT NULL
                      AND o.post -> 'valuta' IS NOT NULL
                      AND NOT (o.h_valuta
                               @> jsonb_build_array(o.post -> 'valuta'))
                   THEN 'valutaen er ikke tillatt for handlingen'
                 -- En valuta handlingen ALT tillater kan aldri blokkeres.
                 WHEN o.gk = 'valuta_ikke_tillatt'
                      AND o.h_valuta @> jsonb_build_array(o.post -> 'valuta')
                   THEN 'valutaen er allerede tillatt for handlingen'
                 ELSE NULL END AS grunn
          FROM maalt o
    )
    SELECT string_agg(format('menneskelig_overstyring[%s] (%s): %s',
                             d.i, d.gk, d.grunn), ', ' ORDER BY d.i)
      INTO v_uanvendelig
      FROM dom d WHERE d.grunn IS NOT NULL;
    IF v_uanvendelig IS NOT NULL THEN
        RAISE EXCEPTION 'aktiver_policy: utkast % har menneskelig '
            'overstyring motoren aldri kan anvende (%)',
            p_utkast_id, v_uanvendelig
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'overstyring_anvendbar';
    END IF;

    -- 5. HENDELSEN FØRST (047, klarsignal §2.4): raden som binder
    --    attestasjonene til versjonen. Operasjons-id-en er deterministisk
    --    som før; FK-ene er DEFERRED og kontrolleres ved commit — de
    --    beviser at attestantene faktisk attesterte DENNE runden med
    --    DENNE diffen, og at runden bærer nøyaktig dette innholdet.
    v_opid := 'aktiver-' || p_utkast_id || '-r' || p_runde;
    -- 048/R47-1: den styrte veien skriver 'styrt', og CHECK-en
    -- hendelse_styrt_krever_kvorum krever da BEGGE attestantene — kvorumet
    -- står nå i lagringen, ikke bare i steg 3 over.
    INSERT INTO public.policyaktivering
        (tenant, policy_id, utkast_id, runde, decision_operation_id,
         versjon, innholds_hash, diff_hash, attestant_a, attestant_b,
         aktiveringskilde, pakrevd_antall)
      VALUES (p_tenant, v_policy_id, p_utkast_id, p_runde, v_opid,
              v_ny, v_innholds_hash, v_diff_hash, v_att_a, v_att_b,
              'styrt', v_pakrevd);

    -- 5b. Deaktiver forrige + sett inn etterfølger i SAMME operasjon (V10).
    --     Versjonsraden bærer operasjonen — FK-en gjør at den ikke kan
    --     peke på en hendelse for annet innhold enn sitt eget.
    IF v_aktiv IS NOT NULL THEN
        UPDATE public.policyer SET aktiv = false
         WHERE tenant = p_tenant AND policy_id = v_policy_id AND versjon = v_aktiv;
    END IF;
    INSERT INTO public.policyer
        (tenant, policy_id, versjon, innholds_hash, status, innhold, aktiv,
         aktivert_av_operasjon, aktiveringskilde)
      VALUES (p_tenant, v_policy_id, v_ny, v_innholds_hash, 'produksjon',
              v_innhold, true, v_opid, 'styrt');
    UPDATE public.policy_hode
       SET aktiv_versjon = v_ny,
           revisjon       = revisjon + 1
     WHERE tenant = p_tenant AND policy_id = v_policy_id;

    -- 6. Lukk runden (apen→klar→brukt følger statemaskinen) + utkast→aktivert
    --    (validert→godkjent→aktivert). Alt i SAMME tx: runden kan aldri brukes
    --    to ganger (decision_operation_id unik når satt).
    IF v_rstatus = 'apen' THEN
        UPDATE public.aktiveringsrunde SET status = 'klar'
         WHERE tenant = p_tenant AND utkast_id = p_utkast_id AND runde = p_runde;
    END IF;
    UPDATE public.aktiveringsrunde
       SET status = 'brukt',
           decision_operation_id = v_opid,
           aktivert_som_versjon  = v_ny
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id AND runde = p_runde;

    IF v_ustatus = 'validert' THEN
        UPDATE public.policyutkast SET status = 'godkjent'
         WHERE tenant = p_tenant AND utkast_id = p_utkast_id;
    END IF;
    UPDATE public.policyutkast SET status = 'aktivert'
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id;

    RETURN v_ny;
END $function$;

ALTER FUNCTION aktiver_policy(TEXT, TEXT, INT, TEXT)
    OWNER TO disponit_policy_eier;
SET LOCAL ROLE disponit_policy_eier;
REVOKE ALL ON FUNCTION aktiver_policy(TEXT, TEXT, INT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION aktiver_policy(TEXT, TEXT, INT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION aktiver_policy(TEXT, TEXT, INT, TEXT)
    TO disponit_migrator;
RESET ROLE;
