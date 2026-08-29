-- 059: B-maskinen (#192, eier-kjennelsen på #190 runde 6) — dørene
-- utleder fra tilstand DE eier, aldri fra kall-argumenter.
--
-- * `lager_sti` genereres av `reserver_inndata` fra (tenant, inndata_id)
--   og står på raden fra fødselen; `registrer_inndata_lastet` mister
--   sti-argumentet. Navnerom/traversering-familien (seks runders funn på
--   #190) blir strukturelt umulig.
-- * X1/Z1: bindingen krever oppdragets EGEN fødselstransaksjon
--   (age(xmin) = 0) — kappløpsvinduet finnes ikke, og payloadens
--   soknadsbunt_ref skrives av samme kaller i samme transaksjon.
-- * Tilstands-CHECKen flyttes: `lager_sti` er NOT NULL fra fødselen i
--   alle levende tilstander.
--
-- Funksjonskroppene er #190-kjedens (all herding bevart: sync-commit,
-- utløp i hver dør, eierskaps-avledning, formåls-konsument-kartet,
-- idempotens-gjenspill med dødsklassifisering) — endringene over er de
-- eneste.

-- Tilstands-CHECKen: reservert-armen krever nå stien.
ALTER TABLE inndata_artefakt DROP CONSTRAINT inndata_tilstand_totalt;

-- 058-ARVEN FØRST (CodeRabbit major): under 058 var `lager_sti` NULL så
-- lenge raden sto `reservert` — døren skrev den først ved `lastet`. En
-- base med ÉN levende reservasjon i sitt éntimes vindu ville dermed felt
-- ADD CONSTRAINT under og rullet hele migrasjonen tilbake. Fødselsstien
-- er en ren funksjon av (tenant, inndata_id) — backfillen gir NØYAKTIG
-- stien 059-døren ville gitt, og `inndata_id` er PRIMARY KEY, så
-- `inndata_lagersti_unik` kan ikke felle den.
--
-- To vakter må vike i nøyaktig dette vinduet, og begge settes tilbake i
-- samme transaksjon som resten av migrasjonen:
-- * `inndata_artefakt_vakt` nekter enhver UPDATE som ikke er en
--   statusovergang — backfillen er `reservert` -> `reservert`.
-- * FORCE RLS uten tenant-kontekst gjør UPDATEN stille TOM, mens ADD
--   CONSTRAINT skanner HELE tabellen — porten hadde altså feilet på rader
--   ingen migrasjonssetning kunne se. (`sett tilstanden testen krever`-
--   klassen, bare i SQL.)
--
-- MEN IKKE ENHVER TENANT KAN FÅ EN STI (Codex P1 på #196). `lager_sti`
-- er fortsatt bevoktet av `inndata_lagersti_navnerom` (058:170-178), og
-- den CHECKen måler TENANTLEDDET: `tenant NOT IN ('.','..')` og
-- `position('/' in tenant) = 0`. Under 058 var stien NULL mens raden sto
-- `reservert`, så CHECKen sov — en tenant-ID med `/` i seg (eller
-- nøyaktig `.`/`..`) kunne reservere fritt, og `deploy/staging/
-- init-tenant.sh` tar argumentet uten stikomponent-sjekk. Å gi den raden
-- `<tenant>/<uuid>.bin` her ville brutt navneroms-CHECKen umiddelbart og
-- RULLET HELE 059 TILBAKE — inne i vedlikeholdsvinduet, for ALLE
-- tenanter, på grunn av én ubrukelig. En backfill som kan felle
-- oppgraderingen for alle andre er ikke en backfill, den er en mine.
--
-- De radene er dessuten alt døde og har alltid vært det: under 058 skrev
-- `registrer_inndata_lastet` den samme stien, så opplastingen deres
-- traff nøyaktig den samme CHECKen og ble avvist hver gang (058:157-163
-- beskriver klassen). De kan ikke lastes, og med 059s reservert-arm kan
-- de heller ikke stå. `forkastet` er den ærlige terminalen — den er den
-- ENE tilstanden som verken krever sti eller krypto, og reaperen
-- behandler den alt som død. Ingen tenant mister noe som kunne blitt
-- brukt; det ubrukelige navnerommet er tenantens egen feil å rette, og
-- rettes den, virker neste reservasjon.
ALTER TABLE inndata_artefakt DISABLE TRIGGER inndata_artefakt_vakt;
ALTER TABLE inndata_artefakt NO FORCE ROW LEVEL SECURITY;
DO $$
DECLARE v_ulovlige INT;
BEGIN
    UPDATE public.inndata_artefakt
       SET status = 'forkastet'
     WHERE status = 'reservert' AND lager_sti IS NULL
       AND (tenant IN ('.', '..') OR position('/' in tenant) > 0);
    GET DIAGNOSTICS v_ulovlige = ROW_COUNT;
    IF v_ulovlige > 0 THEN
        RAISE NOTICE '059: % reservasjon(er) i tenanter med ulovlig'
            ' stikomponent forkastet — de kunne aldri lastes opp under'
            ' 058 heller, og en fødselssti til dem ville felt hele'
            ' migrasjonen', v_ulovlige;
    END IF;
END $$;
-- OG STIEN KAN ALT VÆRE OPPTATT (Codex P1, runde 2 på #196). Navnerommet
-- over sier hvor fødselsstien KAN peke; `inndata_lagersti_unik`
-- (`058:196`) sier at ingen annen rad peker samme sted, og den er en ren
-- UNIQUE på `(tenant, lager_sti)` — den bryr seg ikke om status.
--
-- At `inndata_id` er PRIMARY KEY beviser bare at to RESERVASJONER får
-- hver sin fødselssti. Det sier ingenting om `lastet`/`bundet`-radene,
-- for under 058 kom stien deres fra kalleren: `registrer_inndata_lastet`
-- tok `p_sti TEXT` som sjuende argument (`058:440-441`), og runtime har
-- `SELECT` på `inndata_artefakt`. En kaller kunne altså lese en synlig
-- reservasjons `inndata_id` og laste opp SIN egen bunt på nøyaktig
-- `<tenant>/<den id-en>.bin`. Ærlige opplastinger traff aldri dette —
-- 058-API-et brukte en fersk uuid per kall — så en kollisjon her er alltid
-- en bevisst squatting, men den er fullt reproduserbar på en 058-base.
--
-- Backfillen under ville da delt ut nøyaktig den opptatte strengen,
-- unikheten hadde felt setningen, og hele 059 rullet tilbake for ALLE
-- tenanter. Samme mine som avsnittet over, bare en annen CHECK.
--
-- Reservasjonen må vike, ikke aliaset: aliaset er en ferdig `lastet`/
-- `bundet` bunt med en fil bak seg, mens reservasjonen er tom, kortlivet
-- og har sin egen `utloper` — den er laget for å kunne gå tapt. Å gi den
-- en ANNEN sti er ikke et alternativ; at stien er en ren funksjon av
-- (tenant, inndata_id) ER B-maskinen. `forkastet` er derfor terminalen
-- her også, og tenanten reserverer bare på nytt.
DO $$
DECLARE v_alias INT;
BEGIN
    UPDATE public.inndata_artefakt r
       SET status = 'forkastet'
     WHERE r.status = 'reservert' AND r.lager_sti IS NULL
       AND EXISTS (SELECT 1 FROM public.inndata_artefakt a
                    WHERE a.tenant = r.tenant
                      AND a.lager_sti = r.tenant || '/'
                          || r.inndata_id::text || '.bin');
    GET DIAGNOSTICS v_alias = ROW_COUNT;
    IF v_alias > 0 THEN
        RAISE NOTICE '059: % reservasjon(er) forkastet fordi fødselsstien'
            ' alt var opptatt av en annen rad — 058 lot kalleren velge'
            ' filnavnet, og aliaset ville felt inndata_lagersti_unik og'
            ' dermed hele migrasjonen', v_alias;
    END IF;
END $$;
UPDATE inndata_artefakt
   SET lager_sti = tenant || '/' || inndata_id::text || '.bin'
 WHERE status = 'reservert' AND lager_sti IS NULL;
ALTER TABLE inndata_artefakt FORCE ROW LEVEL SECURITY;
ALTER TABLE inndata_artefakt ENABLE TRIGGER inndata_artefakt_vakt;

-- Grenen for hver tilstand er 058 ORDRETT — eneste endring er at
-- `reservert` nå KREVER `lager_sti` (fødselsstien). Å skrive CHECKen «fra
-- hukommelsen» her mistet i første form både bundet-grenens krypto
-- (Cursor P2-1 hadde alt dekket regresjonen) og forkastet-grenens
-- `oppdrag_id`-forbud (bunteplass-tyveriet) — testene fant begge.
ALTER TABLE inndata_artefakt ADD CONSTRAINT inndata_tilstand_totalt CHECK (
    (status = 'reservert' AND faktiske_bytes IS NULL
     AND innhold_sha256 IS NULL AND lager_sti IS NOT NULL
     AND oppdrag_id IS NULL AND lastet_ts IS NULL
     AND bundet_ts IS NULL)
 OR (status = 'lastet' AND faktiske_bytes IS NOT NULL
     AND innhold_sha256 IS NOT NULL AND lager_sti IS NOT NULL
     AND key_id IS NOT NULL AND nonce IS NOT NULL
     AND oppdrag_id IS NULL AND lastet_ts IS NOT NULL
     AND bundet_ts IS NULL)
 OR (status = 'bundet' AND faktiske_bytes IS NOT NULL
     AND innhold_sha256 IS NOT NULL AND lager_sti IS NOT NULL
     AND key_id IS NOT NULL AND nonce IS NOT NULL
     AND lastet_ts IS NOT NULL
     AND oppdrag_id IS NOT NULL AND bundet_ts IS NOT NULL)
 OR (status = 'forkastet' AND oppdrag_id IS NULL
     AND bundet_ts IS NULL));

-- `lager_sti` ER ET BINDINGSFELT (Cursor P2-2, tredje pass): 058-vaktens
-- write-once gjaldt bare fra `lastet` og utover — i overgangen
-- `reservert -> lastet` kunne en dør med UPDATE fortsatt bytte
-- fødselsstien, og B-maskinen hadde fjernet kall-argumentet uten å låse
-- kolonnen. Vakten er 058 ORDRETT med to endringer: `lager_sti` står i
-- bindingsfelt-grenen (immutabel fra fødselen — den SETTES aldri i noen
-- UPDATE lenger; backfillen kjører med vakten avslått), og er derfor
-- fjernet fra målingsgrenen der den nå var uoppnåelig død dekning.
CREATE OR REPLACE FUNCTION inndata_artefakt_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'inndata_artefakt: % avvist', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.inndata_id IS DISTINCT FROM OLD.inndata_id
       OR NEW.eiermodul IS DISTINCT FROM OLD.eiermodul
       OR NEW.formaal IS DISTINCT FROM OLD.formaal
       OR NEW.innholdstype IS DISTINCT FROM OLD.innholdstype
       OR NEW.maks_bytes IS DISTINCT FROM OLD.maks_bytes
       OR NEW.reservasjon_jti IS DISTINCT FROM OLD.reservasjon_jti
       OR NEW.idempotensnokkel IS DISTINCT FROM OLD.idempotensnokkel
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.utloper IS DISTINCT FROM OLD.utloper
       OR NEW.lager_sti IS DISTINCT FROM OLD.lager_sti THEN
        RAISE EXCEPTION 'inndata_artefakt: bindingsfeltene er immutable'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT ((OLD.status = 'reservert' AND NEW.status IN ('lastet',
                                                         'forkastet'))
         OR (OLD.status = 'lastet' AND NEW.status IN ('bundet',
                                                      'forkastet'))) THEN
        RAISE EXCEPTION 'inndata_artefakt: overgang % -> % finnes ikke',
            OLD.status, NEW.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Bindingen er BINDINGENS (Cursor P2): `oppdrag_id` var hverken
    -- bindingsfelt eller write-once, så enhver skrivevei med UPDATE kunne
    -- sette den — også en forkasting, som dermed kunne ta plassen i den
    -- unike indeksen foran `bind_inndata`. Kolonnen kan nå bare endres i
    -- nøyaktig den overgangen `bind_inndata` gjør.
    IF NEW.oppdrag_id IS DISTINCT FROM OLD.oppdrag_id
       AND NOT (OLD.status = 'lastet' AND NEW.status = 'bundet') THEN
        RAISE EXCEPTION 'inndata_artefakt: oppdrag_id settes kun i'
            ' overgangen lastet -> bundet'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Målingene er write-once: satt ved 'lastet', aldri endret siden.
    IF OLD.status <> 'reservert' AND (
           NEW.faktiske_bytes IS DISTINCT FROM OLD.faktiske_bytes
        OR NEW.innhold_sha256 IS DISTINCT FROM OLD.innhold_sha256
        OR NEW.key_id IS DISTINCT FROM OLD.key_id
        OR NEW.nonce IS DISTINCT FROM OLD.nonce
        OR NEW.lastet_ts IS DISTINCT FROM OLD.lastet_ts) THEN
        RAISE EXCEPTION 'inndata_artefakt: målingene er write-once'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

-- FØDSELSSTI-FORMEN PÅ LAGRINGEN (Cursor P2-1, tredje pass): B-maskinen
-- lover at stien er en REN funksjon av (tenant, inndata_id) — men løftet
-- bodde bare i døren. En direkte skrivevei kunne fortsatt sette inn en
-- reservasjon med vilkårlig navn i eget navnerom, og «strukturelt
-- umulig» var da en funksjonspåstand, ikke en lagringspåstand (016-
-- klassen). Kun LEVENDE reservasjoner strammes: 058-arvens
-- `lastet`/`bundet` bærer kallervalgte navn og skal stå uendret, og
-- G1-forkastingene over har sti NULL og treffes ikke.
ALTER TABLE inndata_artefakt ADD CONSTRAINT inndata_fodselssti_kanonisk
    CHECK (status <> 'reservert'
        OR lager_sti = tenant || '/' || inndata_id::text || '.bin');

-- X1s FØDSELSATTEST (Codex P1 + Cursor P1-1 på #196). Første form målte
-- `pg_catalog.age(o.xmin) = 0` og trodde det var radens fødsel. `xmin` er
-- TUPPELVERSJONENS fødsel: enhver UPDATE lager en ny versjon med den
-- oppdaterende transaksjonens `xmin`, så et for lengst committet oppdrag
-- kunne gjøres «nyfødt» på ett steg. Veien er åpen i dag — runtime har
-- `GRANT SELECT, UPDATE ON oppdrag` (`deploy/staging/migrer.py:221`) og
-- `oppdrag_kolonnelaas` tillater eksplisitt `OLD.status = NEW.status`
-- (`056:529-532`), altså en no-op UPDATE som ingen annen vakt ser. Hele
-- angrepet var to setninger i én transaksjon: `UPDATE oppdrag SET status
-- = status` på et for lengst committet oppdrag, og så `bind_inndata` —
-- den ferske tuppelversjonen ga `age(xmin) = 0`, og porten slapp den
-- gjennom.
--
-- Da var vinduet «synlig, plukkbart oppdrag uten bunt» tilbake, og Z1
-- (payloadens `soknadsbunt_ref` skrevet i samme transaksjon) falt med.
--
-- Fødselen må derfor stå på RADEN, ikke på versjonen: `fodt_xid` settes av
-- triggeren under ved INSERT og kan aldri endres etterpå. Da er
-- `fodt_xid = pg_current_xact_id()` nøyaktig påstanden X1 trenger — «denne
-- raden ble født i DENNE transaksjonen» — og ingen UPDATE kan produsere
-- den. `xid8` er 64-bit og teller ikke rundt, så to fødsler i SAMME
-- cluster kan aldri dele attest — på tvers av clustere holder ikke den
-- påstanden, og det er hva `fodt_oppstart` under er til for.
--
-- Eksisterende rader får migrasjonens egen xid av det volatile defaultet
-- (én verdi for hele omskrivingen). Den er per definisjon en ANNEN
-- transaksjon enn enhver senere kaller, så arven er fail-closed: ingen av
-- dem kan bindes, hvilket er nøyaktig det X1 sier om et committet oppdrag.
ALTER TABLE oppdrag ADD COLUMN fodt_xid xid8 NOT NULL
    DEFAULT pg_catalog.pg_current_xact_id();

-- MEN ET TRANSAKSJONSNUMMER GJELDER BARE I SIN EGEN CLUSTER (Codex P1,
-- runde 2 på #196). `deploy/staging/backup-db.sh:70-82` er
-- `pg_dump --format=custom` + `pg_restore`, og en gjenoppretting etter
-- havari går inn i en FRISK cluster. Triggere ligger i POST-DATA, så
-- radene kommer inn med COPY før `oppdrag_fodselsattest` finnes:
-- `fodt_xid` blir liggende ORDRETT, med tall fra den gamle clusterens
-- teller. Den nye telleren starter på sitt eget lave tall og klatrer, og
-- den dagen den passerer en gjenopprettet rads `fodt_xid`, er
-- `fodt_xid = pg_current_xact_id()` sann for et for lengst committet
-- oppdrag. Da er X1-vinduet tilbake, uten at noen har gjort noe galt.
--
-- «Ingen fødsler deler attest» holder altså bare innenfor ÉN inkarnasjon
-- av databasen. Attesten må derfor navngi inkarnasjonen sin, og
-- `pg_postmaster_start_time()` ER den: den er clusterens egen oppstart, den
-- gjenskapes aldri av en restore (den nye postmasteren startet et annet
-- mikrosekund), og den er lesbar for enhver rolle — i motsetning til
-- `pg_control_system()`, som er superbruker-begrenset og måtte vært
-- åpnet med et GRANT for å brukes her.
--
-- Paret er nøyaktig sterkt nok, ingen av leddene er overflødige:
-- * SAMME inkarnasjon: `xid8` teller monotont og aldri rundt, så en
--   verdi som er brukt kan ikke komme igjen. `fodt_xid` alene avgjør.
-- * ANNEN inkarnasjon (restore, PITR, promotert standby, eller bare en
--   omstart): oppstartstiden er en annen enn den raden bærer, og hver
--   eneste gammel attest er ugyldig. Det er riktig svar — de radene ble
--   født i en tidligere transaksjon, hvilket er akkurat det X1 nekter.
-- Fail-closed begge veier: en attest kan miste gyldighet, aldri få den.
ALTER TABLE oppdrag ADD COLUMN fodt_oppstart timestamptz NOT NULL
    DEFAULT pg_catalog.pg_postmaster_start_time();

-- Egen trigger, ikke et nytt ledd i `oppdrag_kolonnelaas` — samme vedtak
-- som 049s `oppdrag_claim_release_vakt`: den funksjonen bor i 056 og
-- måtte vært kopiert hit i sin helhet for ett ledd. To BEFORE ROW-triggere
-- fyrer i navnerekkefølge og må begge passere; leddet her er uavhengig av
-- statusmaskinen, så rekkefølgen betyr ingenting.
--
-- INSERT-grenen SETTER verdien i stedet for å validere den. Et default
-- kan overstyres av den som skriver raden, og selv om ingen runtime-rolle
-- har INSERT på `oppdrag` i dag (038, port 7: begge opphavsveiene går
-- gjennom herdede funksjoner), er en attest kalleren kan fylle ut selv
-- ingen attest. Døren skriver den, som B-maskinen ellers gjør.
CREATE OR REPLACE FUNCTION oppdrag_fodselsattest()
RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.fodt_xid := pg_catalog.pg_current_xact_id();
        NEW.fodt_oppstart := pg_catalog.pg_postmaster_start_time();
        RETURN NEW;
    END IF;
    IF NEW.fodt_xid IS DISTINCT FROM OLD.fodt_xid
       OR NEW.fodt_oppstart IS DISTINCT FROM OLD.fodt_oppstart THEN
        RAISE EXCEPTION 'oppdrag: fodt_xid/fodt_oppstart er radens'
            ' fødselsattest og kan aldri endres';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS oppdrag_fodselsattest ON oppdrag;
CREATE TRIGGER oppdrag_fodselsattest BEFORE INSERT OR UPDATE ON oppdrag
    FOR EACH ROW EXECUTE FUNCTION oppdrag_fodselsattest();

SET LOCAL ROLE disponit_domene_eier;

-- Returtypen utvides (lager_sti) — CREATE OR REPLACE kan ikke endre den,
-- så den gamle droppes først (EXECUTE-grantene gjenreises i halen).
DROP FUNCTION reserver_inndata(TEXT, TEXT, TEXT, BIGINT, TEXT);

CREATE OR REPLACE FUNCTION reserver_inndata(
    p_tenant TEXT, p_eiermodul TEXT, p_formaal TEXT, p_maks_bytes BIGINT,
    p_idempotensnokkel TEXT)
RETURNS TABLE (inndata_id UUID, reservasjon_jti TEXT, lager_sti TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID; v_jti TEXT; v_sti TEXT; r RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'reserver_inndata');
    -- ISOLASJONSPORTEN (Cursor P2, runde 8) — samme som 056:821-827 og
    -- 057:879-886, og den mangler her.
    --
    -- Idempotensløftet under («samme nøkkel ⇒ samme reservasjon») er
    -- utledet av en LESNING: `ON CONFLICT DO NOTHING` svelger taperens
    -- unik-brudd uten feil, og gjenlesningen rett etter MÅ se vinnerens
    -- rad. Under REPEATABLE READ eller SERIALIZABLE står transaksjonens
    -- snapshot fast fra første setning, så gjenlesningen er blind for en
    -- samtidig committet reservasjon: `v_id` blir NULL, `NOT FOUND`
    -- treffer, og «idempotenskonflikt uten lesbar rad» reises for en
    -- tilstand som IKKE er den feilen beskriver. `api/inndata.py` mapper
    -- `unique_violation` til 409 `idempotenskonflikt`, så et helt legitimt
    -- retry får «nøkkelen er brukt for en ANNEN reservasjon» der
    -- kontrakten lover 201 med den samme referansen tilbake.
    --
    -- READ COMMITTED er det eneste nivået der hver setning ser ferske
    -- data. `read uncommitted` er med fordi PostgreSQL BEHANDLER det som
    -- READ COMMITTED (nivået finnes bare som synonym). Poolen kjører i
    -- dag på basens default, altså READ COMMITTED — porten er derfor
    -- ingen oppførselsendring for HTTP-veien, men `disponit` har EXECUTE
    -- her, og en fremtidig kaller som setter nivået selv skal møte en
    -- ærlig feil framfor et brutt løfte.
    IF current_setting('transaction_isolation')
       NOT IN ('read committed', 'read uncommitted') THEN
        RAISE EXCEPTION 'reserver_inndata: krever READ COMMITTED (fikk %)'
            ' — idempotensløftet er utledet av en LESNING etter konflikt,'
            ' og et fastholdt snapshot gjør den blind for en samtidig'
            ' committet reservasjon',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    -- Speiler tabellens CHECK, men med det kanoniske feilkontraktet i
    -- stedet for check_violation (Cursor P2-3, 017-formen).
    IF p_eiermodul IS DISTINCT FROM 'm57_ats'
       OR p_formaal IS DISTINCT FROM 'soknadsbunt' THEN
        RAISE EXCEPTION 'reserver_inndata: %/% er ikke kontraktens'
            ' kombinasjon', p_eiermodul, p_formaal
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_idempotensnokkel IS NULL
       OR length(p_idempotensnokkel) NOT BETWEEN 8 AND 200 THEN
        RAISE EXCEPTION 'reserver_inndata: idempotensnøkkelen mangler eller'
            ' er utenfor 8..200'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Kjerne-PG, ingen pgcrypto (kjørerens egen regel): to UUID-er gir
    -- 64 hex-tegn entropi for engangs-jti-en.
    v_jti := replace(pg_catalog.gen_random_uuid()::text, '-', '')
             || replace(pg_catalog.gen_random_uuid()::text, '-', '');
    -- `ON CONFLICT ... DO NOTHING` og ikke et oppslag FØRST (038-formen,
    -- Codex P2): oppslag-så-insert har et vindu mellom de to der to
    -- samtidige retryer begge ser «ingen rad» og begge oppretter. Med
    -- konflikten som port taper nøyaktig én av dem, og taperen leser
    -- vinnerens rad under. Målet er navngitt, så en jti-kollisjon (som
    -- ikke kan skje med 128 bit entropi, men som ville vært en ekte feil)
    -- fortsatt reiser i stedet for å bli stille gjenspilt.
    -- B-MASKINEN (#192): stien er DØRENS, aldri kallerens. Den
    -- genereres av (tenant, inndata_id) ved fødselen og er dermed i
    -- tenantens navnerom PER KONSTRUKSJON — hele familien navnerom/
    -- traversering/tenant-som-sti (seks runders funn på #190) er
    -- strukturelt umulig, ikke bevoktet. Tabellens navneroms-CHECKer
    -- består som dybdeforsvar mot enhver annen skrivevei.
    v_id := pg_catalog.gen_random_uuid();
    v_sti := p_tenant || '/' || v_id::text || '.bin';
    INSERT INTO public.inndata_artefakt
        (tenant, inndata_id, eiermodul, formaal, innholdstype, maks_bytes,
         reservasjon_jti, idempotensnokkel, lager_sti, utloper)
    VALUES (p_tenant, v_id, p_eiermodul, p_formaal, 'application/zip',
            p_maks_bytes, v_jti, p_idempotensnokkel, v_sti,
            pg_catalog.now() + interval '1 hour')
    ON CONFLICT ON CONSTRAINT inndata_idempotens_unik DO NOTHING;
    IF NOT FOUND THEN v_id := NULL; END IF;
    IF v_id IS NULL THEN
        SELECT * INTO r FROM public.inndata_artefakt
         WHERE tenant = p_tenant
           AND idempotensnokkel = p_idempotensnokkel;
        IF NOT FOUND THEN
            -- Konflikten fantes, men raden er usynlig: da er tenantkonteksten
            -- ikke den vi tror, og et stille «ny reservasjon» ville vært
            -- verre enn en feil.
            RAISE EXCEPTION 'reserver_inndata: idempotenskonflikt uten'
                ' lesbar rad' USING ERRCODE = 'unique_violation';
        END IF;
        -- Samme nøkkel må bety samme BESTILLING (038-formen): en nøkkel
        -- gjenbrukt for en annen kombinasjon er en konflikt, ikke et
        -- gjenspill. I v1 finnes bare én lovlig kombinasjon, så dette er
        -- en vakt for kontraktsendringen som utvider settet — ikke pynt.
        IF r.eiermodul IS DISTINCT FROM p_eiermodul
           OR r.formaal IS DISTINCT FROM p_formaal
           OR r.maks_bytes IS DISTINCT FROM p_maks_bytes THEN
            RAISE EXCEPTION 'reserver_inndata: nøkkelen er brukt for en'
                ' ANNEN reservasjon (%/%/%)',
                r.eiermodul, r.formaal, r.maks_bytes
                USING ERRCODE = 'unique_violation';
        END IF;
        -- …men et gjenspill må gi et BRUKBART svar (Cursor P2). En
        -- reservasjon som fortsatt står `reservert` etter fristen er død:
        -- `registrer_inndata_lastet` avviser jti-en på `utloper`, og
        -- UNIQUE på `(tenant, idempotensnokkel)` sperrer en ny rad under
        -- den samme nøkkelen. Uten denne grenen svarte vi 201 med en jti
        -- som ikke kan brukes til noe, og klienten satt fast på nøkkelen
        -- sin uten noen vei ut — nettopp tapet gjenspillet finnes for å
        -- redde.
        --
        -- Runde 1 av denne grenen tok bare `reservert` + over fristen, og
        -- ba `forkastet` vente på reaperen som skriver den (Cursor P2,
        -- runde 2). Det var å svare på FORMEN i stedet for på spørsmålet:
        -- en `forkastet` rad er død av nøyaktig samme grunn — jti-en
        -- avvises av `registrer_inndata_lastet`, nøkkelen er sperret av
        -- UNIQUE — og en gren som må utvides hver gang en ny død tilstand
        -- oppstår er en gren som kommer tilbake. Her klassifiseres derfor
        -- HELE tilstandsrommet én gang, som CHECKen over gjør:
        --
        --   * `forkastet` — død uansett hvor den kom fra. Vakten tillater
        --     både `reservert -> forkastet` og `lastet -> forkastet`, og
        --     i begge tilfeller er bunten borte.
        --   * `reservert` etter fristen — død: ingen kan laste på jti-en,
        --     og ingen kan reservere på nytt under nøkkelen.
        --   * `reservert` innenfor fristen, `lastet`, `bundet` — LEVENDE,
        --     og gjenspilles uansett frist: der finnes bunten, og
        --     referansen er det klienten mistet. En `lastet` som passerer
        --     fristen mister ikke bytene sine.
        --
        -- Konflikt er det ærlige svaret på en død rad: nøkkelen er
        -- oppbrukt, ta en ny. Samme errcode som den andre konfliktarmen,
        -- så API-et svarer `idempotenskonflikt` uten en ny feilvei.
        IF r.status = 'forkastet'
           OR (r.status = 'reservert' AND pg_catalog.now() > r.utloper) THEN
            RAISE EXCEPTION 'reserver_inndata: nøkkelen hører til en DØD'
                ' reservasjon (%, utløper %)', r.status, r.utloper
                USING ERRCODE = 'unique_violation';
        END IF;
        -- Gjenspill: samme svar som første gang. Reservasjonen kan i
        -- mellomtiden ha blitt `lastet` eller `bundet` — referansen og
        -- jti-en er like fullt de samme, og det er nettopp DEM klienten
        -- mistet. Å utstede en ny her ville vært å svare noe annet på den
        -- samme forespørselen.
        v_id := r.inndata_id; v_jti := r.reservasjon_jti;
        v_sti := r.lager_sti;
    END IF;
    inndata_id := v_id; reservasjon_jti := v_jti; lager_sti := v_sti;
    RETURN NEXT;
END $$;

-- Lastingen: API-et har strømmet, målt, hashet og kryptert — HER møter
-- målingen deklarasjonen, og reservasjonen forbrukes (jti er engangs:
-- raden EIES av jti-en, og overgangen kan bare skje én gang).

CREATE OR REPLACE FUNCTION registrer_inndata_lastet(
    p_tenant TEXT, p_jti TEXT, p_faktiske_bytes BIGINT,
    p_sha256 TEXT, p_key_id TEXT, p_nonce BYTEA)
RETURNS TABLE (ut_inndata_id UUID, ut_lager_sti TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD;
BEGIN
    -- 017:201 / 016:779-formen: svaret ack-es ikke før WAL-en står på
    -- disk. Uten denne kunne et vertskrasj rulle raden tilbake til
    -- `reservert` ETTER at klienten hadde fått 201 — jti-en ville da vært
    -- «ubrukt» igjen, filen en orphan, og klienten trodd bunten var
    -- lastet. Filen fsync-es i API-et; raden må ha samme garanti
    -- (Cursor P1-4).
    SET LOCAL synchronous_commit = on;
    PERFORM public.krev_tenantkontekst(p_tenant, 'registrer_inndata_lastet');
    SELECT * INTO r FROM public.inndata_artefakt
     WHERE tenant = p_tenant AND reservasjon_jti = p_jti
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'inndata: ukjent reservasjon'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Engangs-jti, men IKKE engangs-SVAR (Cursor P1-1, 017-regresjonen):
    -- `bruk_artefaktkapabilitet` returnerer den eksisterende id-en når
    -- samme hash kommer igjen, og konflikter kun ved ANNEN hash. Gikk 201-
    -- svaret tapt på veien ut, er klientens retry med SAMME kropp den
    -- samme forespørselen — ikke et nytt forsøk på å brenne reservasjonen.
    -- Uten denne grenen var et tapt svar et PERMANENT tap av bunten.
    -- `ut_lager_sti` er den LAGREDE stien: kalleren som fikk en annen sti
    -- tilbake enn den skrev, vet at den nettopp skrev en orphan og rydder.
    IF r.status = 'lastet' THEN
        IF r.innhold_sha256 IS DISTINCT FROM p_sha256 THEN
            RAISE EXCEPTION 'inndata: reservasjonen er brukt for ANNET'
                ' innhold' USING ERRCODE = 'unique_violation';
        END IF;
        -- FRISTEN GJELDER OGSÅ GJENSPILLET (Codex P2 / Cursor P2, runde 6).
        -- Denne grenen svarte 201 uten å se på `utloper`, mens
        -- `bind_inndata` avviser NØYAKTIG den samme raden på den (509-512).
        -- Et tapt 201-svar som ble retryet etter fristen fikk derfor
        -- «gjenopprettet» tilbake på en bunt som aldri kan bindes: klienten
        -- var fortalt at opplastingen sto, idempotensnøkkelen var låst til
        -- den døde lineagen, og hver bestilling feilet siden. Et ærlig
        -- avslag ved opplasting er en klient som kan reservere på nytt.
        --
        -- Samme errcode som utløpssjekken under, altså den kanoniske
        -- `inndata_reservasjon_ugyldig` (409) — ikke en ny kode. Fristen
        -- måles fra reservasjonen, ikke fra `lastet_ts`: å FORLENGE den her
        -- ville gjort opplastingen til en frist-utsteder, og da er
        -- `inndata_artefakt_utlop` ikke lenger reaperens fasit.
        IF pg_catalog.now() > r.utloper THEN
            RAISE EXCEPTION 'inndata: bunten er utløpt og kan ikke gjenspilles'
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        ut_inndata_id := r.inndata_id; ut_lager_sti := r.lager_sti;
        RETURN NEXT;
        RETURN;
    END IF;
    -- FORBRUKT ER IKKE «BRUKT FOR ANNET INNHOLD» (Cursor P2, runde 7).
    -- Denne grenen er alt som IKKE er `reservert` og ikke `lastet`, altså
    -- `bundet`/`forkastet`. Den reiste `unique_violation`, og
    -- `inndata.py:265` mapper den til `inndata_alt_lastet` — men
    -- `feil.py:233-237` sier ordrett at ukjent, utløpt OG alt forbrukt
    -- skal ha SAMME svar, «et skille ville vært et orakel på hvilke
    -- jti-er som finnes». Slik den sto, svarte døren `alt_lastet` på en
    -- jti som HAR nådd minst `bundet` og `reservasjon_ugyldig` på en som
    -- aldri fantes: nøyaktig det orakelet, og i tillegg en løgn, for
    -- innholdet var aldri det som skilte.
    --
    -- `unique_violation` beholdes der den er sann: hash-mismatch på
    -- `lastet` (424-426), som ER «brukt for ANNET innhold», og den
    -- virkelige `inndata_lagersti_unik`-kollisjonen. 017 skiller på samme
    -- måte — replay/hash-konflikt mot utløpt/ugyldig — og «forbrukt uten
    -- hash-match» hører i den siste leiren.
    IF r.status <> 'reservert' THEN
        RAISE EXCEPTION 'inndata: reservasjonen er alt forbrukt (%)',
            r.status USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF pg_catalog.now() > r.utloper THEN
        RAISE EXCEPTION 'inndata: reservasjonen er utløpt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_faktiske_bytes IS NULL OR p_faktiske_bytes <= 0
       OR p_faktiske_bytes > r.maks_bytes THEN
        RAISE EXCEPTION 'inndata: % byte bryter deklarasjonen (maks %)',
            p_faktiske_bytes, r.maks_bytes
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Kryptostrukturen håndheves FØR forbruket, med det kanoniske
    -- feilkontraktet (017:252-formen). `inndata_krypto_struktur` er samme
    -- invariant på tabellen og fanger enhver annen skrivevei; her gir den
    -- `inndata_reservasjon_ugyldig` i stedet for check_violation — og
    -- reservasjonen blir stående som `reservert`, ikke brent på en nonce
    -- som aldri kunne dekryptert (Cursor P2-6).
    IF p_key_id IS NULL OR p_nonce IS NULL
       OR octet_length(p_nonce) <> 12
       OR p_sha256 IS NULL OR p_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'inndata: krypto/sti/hash er strukturelt ugyldig'
            ' (nonce=% B)', octet_length(p_nonce)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- B-MASKINEN (#192): stien ble generert av døren ved fødselen og
    -- står på raden — det finnes ikke lenger noe sti-ARGUMENT å vokte.
    -- (Navneroms-guardene fra #190-rundene levde av at kalleren sendte
    -- stien; en vakt over en umulig tilstand er død kode. Tabellens
    -- CHECKer består som dybdeforsvar.)
    UPDATE public.inndata_artefakt
       SET status = 'lastet', faktiske_bytes = p_faktiske_bytes,
           innhold_sha256 = p_sha256, key_id = p_key_id, nonce = p_nonce,
           lastet_ts = pg_catalog.now()
     WHERE tenant = p_tenant AND inndata_id = r.inndata_id;
    ut_inndata_id := r.inndata_id; ut_lager_sti := r.lager_sti;
    RETURN NEXT;
END $$;

-- Bindingen: kalles i BESTILLINGENS transaksjon. Én bunt, ett oppdrag,
-- én gang — og modulen som skal lese må være den bunten ble reservert
-- for.

CREATE OR REPLACE FUNCTION bind_inndata(
    p_tenant TEXT, p_inndata_id UUID, p_oppdrag_id BIGINT,
    p_eiermodul TEXT)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_oppdrag_eier TEXT; v_oppdragstype TEXT;
        v_oppdrag_status TEXT; v_konsument TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'bind_inndata');
    SELECT * INTO r FROM public.inndata_artefakt
     WHERE tenant = p_tenant AND inndata_id = p_inndata_id
     FOR UPDATE;
    IF NOT FOUND OR r.status <> 'lastet' THEN
        RAISE EXCEPTION 'inndata: % er ikke en lastet bunt',
            p_inndata_id USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Utløpet gjelder også HER (Cursor P2-2): `inndata_artefakt_utlop`
    -- lover at en `lastet` bunt løper ut, og `registrer_inndata_lastet`
    -- håndhever det. Uten samme sjekk i bindingen kunne en utgått bunt
    -- bindes for alltid — fristen ville da vært en påstand som bare gjaldt
    -- fram til reaperen (som kommer i en senere PR) faktisk fantes.
    IF pg_catalog.now() > r.utloper THEN
        RAISE EXCEPTION 'inndata: bunten % er utløpt', p_inndata_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Eierskapet AVLEDES av oppdraget, ikke av kallerens påstand
    -- (Codex P1). `p_eiermodul` kom fra kalleren, og `disponit` har
    -- EXECUTE her: en kaller som kjenner buntens eiermodul kunne ekko-e
    -- den tilbake og samtidig peke på et HVILKET SOM HELST oppdrag i egen
    -- tenant — en bunt reservert for én modul ble da bundet til en
    -- fremmed jobb. Sannheten om hvem som eier jobben står i `oppdrag`.
    --
    -- Ingen `FOR UPDATE` på oppdraget: `oppdrag.eiermodul` er
    -- kolonnelåst i `oppdrag_kolonnelaas()` (005) og raden kan ikke
    -- slettes (`oppdrag_ingen_sletting`), så verdien kan ikke endre seg
    -- under oss. En lås ville dessuten krevd UPDATE-rettighet på
    -- `oppdrag` for `disponit_domene_eier`, som i dag har KUN SELECT
    -- (016) — å utvide den for en lås vi ikke trenger ville byttet ett
    -- funn mot et større. RLS gjelder også for denne definer-rollen;
    -- `krev_tenantkontekst` over har alt bundet `disponit.tenant`.
    SELECT o.eiermodul, o.oppdragstype, o.status
      INTO v_oppdrag_eier, v_oppdragstype, v_oppdrag_status
      FROM public.oppdrag o
     WHERE o.tenant = p_tenant AND o.id = p_oppdrag_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'inndata: oppdrag % finnes ikke i tenant %',
            p_oppdrag_id, p_tenant
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- LIVSSYKLUSEN ER OGSÅ EN PORT (Cursor P2, runde 6). Eierskap og
    -- formål over sier HVEM og HVA, ikke NÅR: uten denne kunne en kaller
    -- med EXECUTE binde en lastet bunt til et TERMINALT oppdrag
    -- (`utfort`/`feilet`/`kansellert`). Engangsbunten ble da forbrukt, det
    -- terminale oppdraget brukte opp sin ENE bunteplass
    -- (`inndata_artefakt_oppdrag` er unik og har ingen vei tilbake — 005s
    -- vakt tillater ingen overgang UT av terminal), og lineage pekte på en
    -- jobb som var ferdig før bunten fantes.
    --
    -- Det AKTIVE settet er 038s (`opprettet`,`plukket`), ikke bare
    -- `opprettet`: bindingen skjer i bestillingens transaksjon og treffer
    -- i praksis `opprettet`, men å snevre inn til nøyaktig den ene ville
    -- vært å binde PR-2s bestillingsvei til en rekkefølge denne
    -- migrasjonen ikke får bestemme. Porten er fail-closed på det som er
    -- galt uansett rekkefølge: en jobb utenfor sin egen livssyklus.
    -- 017:110-111 gjør det samme strammere (`plukket` alene) fordi en
    -- kapabilitet utstedes ETTER claim; her er det motsatt ende av løpet.
    IF v_oppdrag_status NOT IN ('opprettet', 'plukket') THEN
        RAISE EXCEPTION 'inndata: oppdrag % er % og kan ikke binde inndata',
            p_oppdrag_id, v_oppdrag_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- X1-SERIALISERINGEN (B-maskinen, #192 — overstyrer runde 6-notatet
    -- om å ikke binde rekkefølgen): bindingen skjer i oppdragets EGEN
    -- fødselstransaksjon, målt med radens `fodt_xid` OG `fodt_oppstart`
    -- (se fødselsattesten over — `age(xmin)` målte tuppelversjonen og lot
    -- en no-op UPDATE forfalske fødselen, og en `fodt_xid` alene ville
    -- vært et tall fra en annen clusters teller etter en restore). Da
    -- FINNES ikke vinduet
    -- Codex målte (status terminal mellom lesning og UPDATE): raden er
    -- usynlig for alle andre til transaksjonen committer, og ingen
    -- statusovergang kan flettes inn. Rekkefølgen ER kontrakten —
    -- docstringen har sagt det hele tiden («kalles i BESTILLINGENS
    -- transaksjon»), og PR-3s bestillingsvei bygges på den. Z1 (oppdragets
    -- egen soknadsbunt_ref) håndheves av samme regel: payloaden skrives i
    -- samme transaksjon av samme kaller, og en splittet sannhet kan ikke
    -- committes hver for seg.
    PERFORM 1 FROM public.oppdrag o
     WHERE o.tenant = p_tenant AND o.id = p_oppdrag_id
       AND o.fodt_xid = pg_catalog.pg_current_xact_id()
       AND o.fodt_oppstart = pg_catalog.pg_postmaster_start_time();
    IF NOT FOUND THEN
        RAISE EXCEPTION 'inndata: bindingen må skje i oppdragets egen'
            ' fødselstransaksjon (X1-serialiseringen, #192)'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF r.eiermodul IS DISTINCT FROM v_oppdrag_eier
       OR r.eiermodul IS DISTINCT FROM p_eiermodul THEN
        RAISE EXCEPTION 'inndata: bunten er reservert for %, oppdrag %'
            ' eies av % (kalleren påsto %)',
            r.eiermodul, p_oppdrag_id, v_oppdrag_eier, p_eiermodul
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- EIERSKAP ER IKKE FORMÅL (Codex P1). Sjekken over sier at oppdraget
    -- eies av samme modul som bunten ble reservert for — men `m57_ats` eier
    -- flere oppdragstyper enn den ene hvis kontrakt faktisk KONSUMERER en
    -- søknadsbunt (`soknadsbunt_ref` er påkrevd i `rekruttering.evaluering`
    -- alene, se `oppdragskontrakt.FELTSTRENGER`). En kaller med EXECUTE
    -- kunne derfor bundet bunten til et vilkårlig annet m57-oppdrag i egen
    -- tenant: engangsbunten ble forbrukt, det uskyldige oppdraget brukte
    -- opp sin ENE bunteplass (`inndata_artefakt_oppdrag`), og lineage
    -- pekte på en jobb som aldri skulle lest den.
    --
    -- Kartet er lukket og fail-closed: en ny `formaal` i en senere
    -- migrasjon MÅ navngi sin konsument her, ellers er bindingen en feil —
    -- ikke en stille passering. Samme vedtak som `eiermodul`-CHECKen: et
    -- nytt formål er en kontraktsendring, ikke et kallargument.
    v_konsument := CASE r.formaal
                        WHEN 'soknadsbunt' THEN 'rekruttering.evaluering'
                   END;
    IF v_konsument IS NULL THEN
        RAISE EXCEPTION 'inndata: formålet % har ingen konsumerende'
            ' oppdragstype i denne kontrakten', r.formaal
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_oppdragstype IS DISTINCT FROM v_konsument THEN
        RAISE EXCEPTION 'inndata: % konsumeres av %, men oppdrag % er %',
            r.formaal, v_konsument, p_oppdrag_id, v_oppdragstype
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.inndata_artefakt
       SET status = 'bundet', oppdrag_id = p_oppdrag_id,
           bundet_ts = pg_catalog.now()
     WHERE tenant = p_tenant AND inndata_id = p_inndata_id;
END $$;

-- Gammel signatur ut (sti-argumentet finnes ikke lenger):
DROP FUNCTION IF EXISTS registrer_inndata_lastet(TEXT, TEXT, BIGINT,
    TEXT, TEXT, BYTEA, TEXT);

REVOKE ALL ON FUNCTION reserver_inndata(TEXT, TEXT, TEXT, BIGINT, TEXT)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION registrer_inndata_lastet(TEXT, TEXT, BIGINT, TEXT,
    TEXT, BYTEA) FROM PUBLIC;
REVOKE ALL ON FUNCTION bind_inndata(TEXT, UUID, BIGINT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION reserver_inndata(TEXT, TEXT, TEXT, BIGINT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION registrer_inndata_lastet(TEXT, TEXT, BIGINT,
    TEXT, TEXT, BYTEA) TO disponit;
GRANT EXECUTE ON FUNCTION bind_inndata(TEXT, UUID, BIGINT, TEXT)
    TO disponit;

RESET ROLE;
