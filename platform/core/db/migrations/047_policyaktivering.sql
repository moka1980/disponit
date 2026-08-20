-- 047 — policyaktivering: hendelsen som binder attestasjonene til versjonen
-- (editorklarsignalet §2, E1–E1f konsolidert; SP-1…SP-9).
--
-- LINEAGE-HULLET SOM LUKKES: aktiveringen etterlot til nå bare
-- tilstandsoverganger — runde `brukt`, utkast `aktivert`, ny policyer-rad —
-- og bindingen attestasjon↔versjon fantes kun som hash-LIKHET
-- (`utkast_innholds_hash` = `innholds_hash`), aldri som referanse. Denne
-- migrasjonen innfører HENDELSEN: en rad som bare kan eksistere fordi
-- aktiveringen faktisk skjedde, med FK-kjeder som beviser hvert ledd:
--
--   attestasjonene finnes for DENNE runden og bandt SAMME diff_hash
--     → begge er er_forfatter = false, og forblir det (nøkkelen bryter
--       ellers — SP-9: kvalifikasjonen holder både ved etablering og varig)
--     → runden er den hvis utkast_innholds_hash er hendelsens innholds_hash
--     → versjonen som aktiveres har samme innholds_hash.
--
-- Runden er leddet som binder diff_hash-siden til innholds_hash-siden:
-- attestasjons-FK-ene og runde-FK-en deler (tenant, utkast_id, runde), så
-- attestasjonene kan ikke gjelde en annen runde enn den hvis innhold ble
-- versjon. Ingen direkte diff_hash↔innholds_hash-kobling finnes å binde
-- mot (lesesvar runde 2: de hasher ulike objekter).
--
-- ALLE lineage-FK-er er DEFERRABLE INITIALLY DEFERRED: kjeden er sirkulær
-- (hendelse→runde→hendelse, hendelse→versjon→hendelse), og
-- `decision_operation_id` settes i samme transaksjon som hendelsen skrives
-- — commit er kontrollpunktet (klarsignalet §2.4 steg 6).
--
-- ⚠️ DOKUMENTERT AVVIK FRA KLARSIGNALET: `attestant_b` er NULLBAR.
-- Klarsignalets skisse krevde to attestanter ubetinget, men det ville
-- gjort halvparten av lovlige aktiveringer umulige, målt mot koden:
-- `pakrevd_antall_godkjennere` er 1 for INNSNEVRER/NØYTRAL (policyadmin.
-- _vurder), og for UTVIDER kan forfatteren være ÉN av de to («forfatter
-- kan være én, aldri begge» — V6). Hendelsen registrerer de
-- KVALIFISERENDE attestasjonene (er_forfatter = false, rundens diff) som
-- faktisk fantes: alltid minst én (gaten krever ≥1 uavhengig), to når to
-- finnes. KVORUMET håndheves der det alltid har bodd — i `aktiver_policy`
-- steg 3 — hendelsen er evidensen for hva som kvalifiserte, ikke gaten.
-- Backfillen (nederst) følger samme regel: ≥1 kvalifiserende + entydig
-- rundematch → bundet; ellers NULL. Codex-portene 4–9 og 17 står uendret
-- der to attestanter finnes; port 7 blir «hendelse oppgir en attestant
-- uten attestasjonsrad → FK-avvist».

-- ------------------------------------------------------------
-- 1. Referansenøklene lineage-FK-ene peker på (E1d: eksplisitte, aldri PK)
-- ------------------------------------------------------------
ALTER TABLE aktiveringsrunde ADD CONSTRAINT runde_refererbar
  UNIQUE (tenant, utkast_id, runde, decision_operation_id, utkast_innholds_hash);
ALTER TABLE aktiveringsattestasjon ADD CONSTRAINT attestasjon_refererbar
  UNIQUE (tenant, utkast_id, runde, bruker_id, diff_hash, er_forfatter);
ALTER TABLE policyutkast ADD CONSTRAINT utkast_policy_refererbar
  UNIQUE (tenant, utkast_id, policy_id);

-- ------------------------------------------------------------
-- 2. Hendelsen
-- ------------------------------------------------------------
CREATE TABLE policyaktivering (
  tenant TEXT NOT NULL,
  policy_id TEXT NOT NULL,
  utkast_id TEXT NOT NULL,          -- policyutkast.utkast_id er TEXT (012)
  runde INT NOT NULL,
  decision_operation_id TEXT NOT NULL,
  versjon TEXT NOT NULL,
  innholds_hash TEXT NOT NULL,      -- binder versjonen
  diff_hash TEXT NOT NULL,          -- binder attestasjonene
  attestant_a TEXT NOT NULL,
  -- NULL når bare én attestasjon kvalifiserte (INNSNEVRER/NØYTRAL, eller
  -- UTVIDER der forfatteren var den andre) — se avviksblokken i hodet.
  attestant_b TEXT,
  attestant_er_forfatter BOOLEAN NOT NULL DEFAULT false
    CHECK (attestant_er_forfatter = false),  -- hendelsens side er alltid false
  aktivert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, decision_operation_id),
  CONSTRAINT hendelse_to_distinkte
    CHECK (attestant_b IS NULL OR attestant_a <> attestant_b),
  -- «Én hendelse per versjon» står IKKE som UNIQUE (tenant, policy_id,
  -- versjon) (Codex P2). Tabellen er immutabel og evig, mens versjonsnumre
  -- IKKE er det: `slett_ubrukt_policy` (032) sletter ubrukte versjoner
  -- nettopp for at de skal kunne gjenskapes — en uttrykkelig støttet flyt
  -- (`test_identisk_gjenskapt_policy_gjenoppliver_ikke_slettet_generasjon`).
  -- En UNIQUE her ville reservert `(tenant, policy_id, versjon)` for alltid
  -- ved første aktivering, og den neste aktiveringen av et GJENSKAPT
  -- policy-id/versjon-par ville dødd på en hendelse for en generasjon som
  -- ikke lenger finnes — selv om `policyer` med rette meldte versjonen fri.
  -- Invarianten er derfor flyttet til `hendelse_en_per_levende_versjon`
  -- under, som måler mot de LEVENDE radene, der sletting faktisk virker.
  CONSTRAINT hendelse_en_per_runde   UNIQUE (tenant, utkast_id, runde),
  -- E1d: eksplisitte referansenøkler for FK-ene som peker HIT
  CONSTRAINT hendelse_runde_nokkel
    UNIQUE (tenant, utkast_id, runde, decision_operation_id, versjon),
  CONSTRAINT hendelse_versjon_nokkel
    UNIQUE (tenant, policy_id, versjon, innholds_hash, decision_operation_id),
  -- hendelse → utkast/policy (konsistens tenant·utkast·policy)
  CONSTRAINT hendelse_utkast_fk
    FOREIGN KEY (tenant, utkast_id, policy_id)
    REFERENCES policyutkast (tenant, utkast_id, policy_id)
    DEFERRABLE INITIALLY DEFERRED,
  -- hendelse → runde, med RUNDENS innholdshash (broen fra lesesvar 2)
  CONSTRAINT hendelse_runde_fk
    FOREIGN KEY (tenant, utkast_id, runde, decision_operation_id, innholds_hash)
    REFERENCES aktiveringsrunde
      (tenant, utkast_id, runde, decision_operation_id, utkast_innholds_hash)
    DEFERRABLE INITIALLY DEFERRED,
  -- hendelse → attestasjon a og b: faktiske rader, samme runde, samme
  -- diff_hash, ikke forfatter (E1e/E1f). MATCH SIMPLE: b-FK-en er sovende
  -- når attestant_b er NULL.
  CONSTRAINT hendelse_attestasjon_a_fk
    FOREIGN KEY (tenant, utkast_id, runde, attestant_a, diff_hash,
                 attestant_er_forfatter)
    REFERENCES aktiveringsattestasjon
      (tenant, utkast_id, runde, bruker_id, diff_hash, er_forfatter)
    DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT hendelse_attestasjon_b_fk
    FOREIGN KEY (tenant, utkast_id, runde, attestant_b, diff_hash,
                 attestant_er_forfatter)
    REFERENCES aktiveringsattestasjon
      (tenant, utkast_id, runde, bruker_id, diff_hash, er_forfatter)
    DEFERRABLE INITIALLY DEFERRED);

ALTER TABLE policyaktivering ENABLE ROW LEVEL SECURITY;
ALTER TABLE policyaktivering FORCE ROW LEVEL SECURITY;
CREATE POLICY policyaktivering_tenant ON policyaktivering
  USING (tenant = current_setting('disponit.tenant', true));
-- Eierrollen selv (SECURITY DEFINER-funksjonene) må se på tvers ved
-- backfill/verifikasjon — samme mønster som m37-dispatcheren i 044.
CREATE POLICY policyaktivering_eier ON policyaktivering
  USING (CURRENT_USER = 'disponit_policy_eier');

CREATE TRIGGER policyaktivering_immutabel
  BEFORE UPDATE OR DELETE ON policyaktivering
  FOR EACH ROW EXECUTE FUNCTION avvis_endring();

-- TRUNCATE fyrer ALDRI rad-triggere (Codex P2). Rad-vakten over sier
-- ingenting om `TRUNCATE policyaktivering CASCADE`, og den setningen står
-- åpen for tabelleieren — migratoren, altså den rollen enhver senere
-- migrasjon og ethvert vedlikeholdsskript kjører som. Hele
-- aktiveringslinjen kunne dermed forsvinne i én setning, tross at tabellen
-- er erklært evig: attestasjonene ville stått igjen uten hendelsen de
-- beviser, og `policyer.aktivert_av_operasjon` uten noe å peke på.
-- `revisjonslogg` (001) har hatt nøyaktig denne setnings-nivå-vakten siden
-- første migrasjon; den er ikke valgfri for en append-only-tabell.
-- (`avvis_endring` leser bare TG_TABLE_NAME/TG_OP og virker derfor like
-- godt på setningsnivå.)
CREATE TRIGGER policyaktivering_ingen_truncate
  BEFORE TRUNCATE ON policyaktivering
  FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- Indeksen den frafalte `hendelse_en_per_versjon` ga oss gratis. Både
-- vakten under og historikkens versjonsoppslag går denne veien.
CREATE INDEX policyaktivering_versjon_idx
  ON policyaktivering (tenant, policy_id, versjon);

-- «Én hendelse per LEVENDE versjon» (Codex P2) — invarianten den frafalte
-- UNIQUE-en skulle bære, målt der sletting faktisk virker.
--
-- Regelen: to hendelser kan gjerne bære samme (policy_id, versjon), men
-- høyst ÉN av dem kan være den en levende `policyer`-rad peker på. En
-- hendelse hvis versjonsrad er slettet er historikk uten krav på nummeret;
-- den skal ikke kunne blokkere at nummeret tas i bruk på nytt.
--
-- DEFERRABLE INITIALLY DEFERRED er ikke pynt: `aktiver_policy` skriver
-- hendelsen (steg 5) FØR `policyer`-raden (steg 5b), så en umiddelbar
-- prøve ville lest et halvferdig bilde. Ved commit er begge på plass, og
-- `policyer.aktivert_av_operasjon` sier entydig hvilken hendelse som eier
-- den levende raden.
-- SECURITY DEFINER er nødvendig, ikke pyntelig: en DEFERRED trigger fyrer
-- ved COMMIT, utenfor `aktiver_policy` sin definer-kontekst, altså som
-- runtime-rollen. Den har verken SELECT på `policyaktivering` (grantet går
-- kun til eieren, over) eller eierens RLS-policy, så en vanlig
-- INVOKER-funksjon ville falt på «permission denied» i selve commit-en.
-- Tenant-GUC-en er `SET LOCAL` og står fortsatt ved commit, så
-- tenantporten i RLS gjelder som ellers.
CREATE OR REPLACE FUNCTION hendelse_en_per_levende_versjon()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_annen TEXT;
BEGIN
    SELECT a.decision_operation_id INTO v_annen
      FROM public.policyaktivering a
      JOIN public.policyer p
        ON p.tenant = a.tenant
       AND p.aktivert_av_operasjon = a.decision_operation_id
     WHERE a.tenant = NEW.tenant AND a.policy_id = NEW.policy_id
       AND a.versjon = NEW.versjon
       AND a.decision_operation_id <> NEW.decision_operation_id
     LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'policyaktivering: versjon %/% er allerede aktivert '
            'av operasjon %', NEW.policy_id, NEW.versjon, v_annen
            USING ERRCODE = 'unique_violation',
                  CONSTRAINT = 'hendelse_en_per_levende_versjon';
    END IF;
    RETURN NULL;
END $$;

ALTER FUNCTION hendelse_en_per_levende_versjon()
    OWNER TO disponit_policy_eier;

CREATE CONSTRAINT TRIGGER hendelse_en_per_levende_versjon
  AFTER INSERT ON policyaktivering
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION hendelse_en_per_levende_versjon();

-- SELECT og INSERT kun til funksjonseieren. Runtime-rollen får INGEN av
-- delene: flaten leser historikken gjennom definer-funksjonene i del 6
-- (SP-7). Oppsettsveien (`policyregister.registrer`) leser tabellen
-- direkte for bootstrap-vakten, men den kjører på migratorforbindelsen,
-- som eier tabellen — og RLS-tenantpolicyen står også for den.
GRANT SELECT ON policyaktivering TO disponit_policy_eier;
GRANT INSERT ON policyaktivering TO disponit_policy_eier;

-- ------------------------------------------------------------
-- 3. Runden: terminal krever hendelse
-- ------------------------------------------------------------
ALTER TABLE aktiveringsrunde ADD COLUMN aktivert_som_versjon TEXT;

-- NOT VALID med vilje, og det er NOT VALID ALENE som bærer historikk-
-- unntaket: historiske `brukt`-runder fra før hendelsen fantes kan stå
-- ubundet der backfillen ikke fant et entydig match, og de skal ikke velte
-- migrasjonen. Postgres hopper over dem i skanningen, men håndhever
-- constrainten på HVER INSERT og UPDATE etterpå — unntaket gjelder altså
-- radene som alt fantes, ikke formen.
--
-- Derfor krever `brukt` også BINDINGEN (Codex P1). Uten den kunne en rad
-- fødes terminal med `aktivert_som_versjon = NULL`: FK-en under er MATCH
-- SIMPLE og SOVER så lenge én av de fem kolonnene er NULL, og
-- tilstandsmaskin-triggeren er BEFORE UPDATE — en rad som fødes terminal
-- får aldri den UPDATE-en. Runtime-rollen har fortsatt INSERT på tabellen,
-- så det var en åpen dør rett til den hendelsesløse terminalrunden
-- migrasjonen sier den forbyr. Med kravet her er alle fem FK-kolonnene
-- NOT NULL i nøyaktig den formen som betyr noe, og FK-en biter.
ALTER TABLE aktiveringsrunde ADD CONSTRAINT runde_versjon_krever_brukt CHECK (
     (status = 'brukt'  AND decision_operation_id IS NOT NULL
                        AND aktivert_som_versjon IS NOT NULL)
  OR (status <> 'brukt' AND aktivert_som_versjon IS NULL)) NOT VALID;

ALTER TABLE aktiveringsrunde ADD CONSTRAINT runde_terminal_krever_hendelse
  FOREIGN KEY (tenant, utkast_id, runde, decision_operation_id,
               aktivert_som_versjon)
  REFERENCES policyaktivering
      (tenant, utkast_id, runde, decision_operation_id, versjon)
  DEFERRABLE INITIALLY DEFERRED;

-- Tilstandsmaskinregel (ikke autorisasjon): brukt nås kun fra klar, er
-- terminal, og versjonsbindingen er immutabel når satt.
CREATE OR REPLACE FUNCTION runde_brukt_kun_fra_klar() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = 'brukt' AND OLD.status IS DISTINCT FROM 'brukt'
     AND OLD.status <> 'klar' THEN
    RAISE EXCEPTION 'aktiveringsrunde: ulovlig overgang % -> brukt',
        OLD.status;
  END IF;
  IF OLD.status = 'brukt' AND NEW.status IS DISTINCT FROM 'brukt' THEN
    RAISE EXCEPTION 'aktiveringsrunde: brukt er terminal';
  END IF;
  IF OLD.aktivert_som_versjon IS NOT NULL
     AND NEW.aktivert_som_versjon IS DISTINCT FROM OLD.aktivert_som_versjon
  THEN
    RAISE EXCEPTION 'aktiveringsrunde: versjonsbindingen er immutabel';
  END IF;
  -- Ny binding krever at raden er (eller blir) brukt i samme setning.
  -- CHECK-en over sier det samme for enhver skriving; her står den likevel,
  -- fordi triggeren kjører FØR constrainten og gir eier en navngitt
  -- forklaring i stedet for et anonymt CHECK-brudd.
  IF NEW.aktivert_som_versjon IS NOT NULL
     AND OLD.aktivert_som_versjon IS NULL
     AND NEW.status <> 'brukt' THEN
    RAISE EXCEPTION 'aktiveringsrunde: binding uten brukt';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER aktiveringsrunde_terminal_vakt
  BEFORE UPDATE ON aktiveringsrunde
  FOR EACH ROW EXECUTE FUNCTION runde_brukt_kun_fra_klar();

-- Kolonnenivå: runtime mister tabell-UPDATE og får kun (status) — speiles
-- i migrer.py sitt rettighetssteg, som ellers ville lagt tabellgrantet
-- tilbake ved neste deploy. Ingen GUC, ingen transaksjonskontekst: den
-- kausale porten er FK-en mot hendelsen (E1c).
REVOKE UPDATE ON aktiveringsrunde FROM disponit;
GRANT UPDATE (status) ON aktiveringsrunde TO disponit;

-- ------------------------------------------------------------
-- 4. Versjonen: operasjonen må være en faktisk hendelse
-- ------------------------------------------------------------
ALTER TABLE policyer ADD COLUMN aktivert_av_operasjon TEXT;  -- NULL: ubundet historisk

-- VEIEN raden kom inn (Codex P2). `aktivert_av_operasjon IS NULL` alene
-- kan ikke bære det: kolonnen må være nullbar for de MIGRERTE radene —
-- versjoner aktivert før hendelsen fantes, som backfillen nederst ikke
-- kunne binde entydig — men nullbarheten gjaldt dermed også FRAMOVER.
-- `policyregister.registrer(..., aktiver=True)` er fortsatt oppsetts- og
-- bootstrapveien (`init-tenant.sh`), og den skriver en aktiv `policyer`-rad
-- uten operasjon. Uten et eget merke ble hver eneste slike rad — skrevet
-- ETTER 047 — rapportert som «ubundet historisk versjon», altså som noe
-- som ligger foran migrasjonen i tid. Da kan ingen lese historikken og se
-- forskjell på «dette er fra før lineagen fantes» og «dette gikk utenom
-- lineagen i går».
--
--   styrt      — `aktiver_policy`: fire-øyne-runde + hendelse. Krever
--                `aktivert_av_operasjon`.
--   bootstrap  — inn via `policyregister.registrer`, oppsettsveien. Typisk
--                første policy for en tenant (`init-tenant.sh`), før det
--                finnes noe å ha fire øyne på. Har ingen runde å vise til
--                og skal ikke late som. `registrer` nekter nå å gå forbi en
--                versjon som ER styrt aktivert — bootstrap er en start, ikke
--                en omvei rundt fire-øyne-veien.
--   historisk  — fantes da 047 landet. Kan ikke skrives etterpå.
ALTER TABLE policyer ADD COLUMN aktiveringskilde TEXT
  CONSTRAINT policyer_aktiveringskilde_kjent
  CHECK (aktiveringskilde IN ('styrt', 'bootstrap', 'historisk'));

-- NÅR bootstrapraden ble aktivert (Codex P2). Den styrte veien har
-- `policyaktivering.aktivert_ts`; bootstrapen har ingen hendelse, og
-- historikken lånte derfor `policyer.opprettet` — REGISTRERINGS-
-- tidspunktet. De to er ikke det samme: `registrer(..., aktiver=False)`
-- legger inn en versjon uten å aktivere den, og en senere
-- `registrer(..., aktiver=True)` aktiverer NØYAKTIG DEN RADEN gjennom
-- upserten. `opprettet` står da urørt, så historikken sorterte den
-- nyaktiverte versjonen på et gammelt tidspunkt — ble en annen versjon
-- laget i mellomtiden, var lista ikke lenger nyest-først, og diffens
-- default-retning bygger på den rekkefølgen. Kolonnen er tom for de
-- migrerte radene: for dem finnes tidspunktet ikke noe sted, og
-- `opprettet` blir stående som den ærligste tilnærmingen vi har.
ALTER TABLE policyer ADD COLUMN bootstrap_aktivert_ts TIMESTAMPTZ;

-- GENERASJONEN: radens ugjenbrukelige identitet (Codex P2).
--
-- `(policy_id, versjon)` er IKKE en identitet over tid: `slett_ubrukt_policy`
-- frigjør uttrykkelig nummeret, og det kan gjenskapes. Innholdshashen er
-- heller ikke nok — `test_identisk_gjenskapt_policy_gjenoppliver_ikke_slettet_
-- generasjon` viser nettopp at samme dokument kan settes inn igjen under
-- samme nummer, altså med IDENTISK hash og som en ANNEN generasjon. Alt som
-- utledes av radens innhold kan gjenskapes; det eneste som ikke kan det, er
-- et tall ingen får igjen.
--
-- Sekvensen gir det. Kolonnen skrives ved INSERT og aldri etterpå: en
-- upsert (`registrer`) rører den ikke, så en re-registrering beholder
-- generasjonen sin, mens en sletting + gjenskaping får en ny. Rullbakkens
-- opphav peker hit, og «bundet» blir da en påstand som ikke kan
-- fabrikkeres i ettertid.
--
-- Radene som alt finnes får hver sin verdi når kolonnen legges til:
-- `nextval` er volatil, så Postgres skriver tabellen om og evaluerer
-- defaulten PER RAD.
CREATE SEQUENCE policyer_generasjon_seq;
ALTER TABLE policyer ADD COLUMN generasjon BIGINT NOT NULL
  DEFAULT nextval('policyer_generasjon_seq');
ALTER TABLE policyer ADD CONSTRAINT policyer_generasjon_unik
  UNIQUE (generasjon);
-- SEKVENSEN LEVER AV KOLONNEN, OG SKAL DØ MED DEN. `OWNED BY` er ikke
-- pynt: dette er den FØRSTE frittstående sekvensen i hele
-- migrasjonshistorikken, og uten eierskapsbåndet henger den igjen når
-- `policyer` slippes. `DROP TABLE ... CASCADE` fjerner defaulten som
-- peker hit, men ikke sekvensen selv — den er et eget objekt, ikke en
-- del av tabellen.
--
-- Det er nøyaktig det som veltet gjenoppbyggingsveien: `_nullstill` i
-- `test_kjorer_og_kryptering` river basen dynamisk (alle TABELLER, alle
-- FUNKSJONER — «en reset som etterlater objekter er ingen reset») og
-- kjører 1→47 på nytt. Sekvenser sto ikke på lista, for det har aldri
-- FUNNES en frittstående sekvens før nå. 047 møtte da sin egen sekvens
-- igjen og døde på «already exists» — og siden den migrasjonen aldri
-- ble registrert, prøvde hver eneste senere test det samme, med
-- samme utfall. Én uryddet rest, hele suiten rød.
--
-- Båndet hører uansett hjemme her: sekvensen har ingen mening utenfor
-- `policyer.generasjon`, og den samsvarer med hvordan `SERIAL` ville
-- knyttet dem. Da trenger ikke opprydderen å kjenne til sekvenser i det
-- hele tatt — objektet forsvinner med tabellen det tilhører.
ALTER SEQUENCE policyer_generasjon_seq OWNED BY policyer.generasjon;

-- Generasjonen er identiteten, og en identitet som kan skrives om er ingen.
-- Samme form som `policyer_operasjon_immutabel` (nederst, etter backfillen).
CREATE TRIGGER policyer_generasjon_immutabel
  BEFORE UPDATE ON policyer
  FOR EACH ROW WHEN (NEW.generasjon IS DISTINCT FROM OLD.generasjon)
  EXECUTE FUNCTION avvis_endring();

-- `aktiver_policy` er SECURITY DEFINER og setter inn som policy-eieren;
-- uten USAGE på sekvensen kan den ikke skrive raden i det hele tatt.
GRANT USAGE ON SEQUENCE policyer_generasjon_seq TO disponit_policy_eier;

-- Alt som fantes da migrasjonen landet ER historikk, per definisjon.
-- Backfillen nederst løfter de radene den klarer å binde til 'styrt'.
--
-- FORCE RLS binder også migratoren, og uten tenantkontekst ser den NULL
-- rader — en naken UPDATE her ville truffet ingenting og gjort det stille
-- (0 rader er ikke en feil). Derfor samme migrasjonslokale, selv-
-- reverserende bro som backfillen i del 7 bruker, bare med skriveretten
-- den trenger.
CREATE POLICY kildebackfill_047 ON policyer FOR ALL
    TO disponit_migrator USING (true) WITH CHECK (true);
UPDATE policyer SET aktiveringskilde = 'historisk';
DROP POLICY kildebackfill_047 ON policyer;

-- 'historisk' er en TILSTAND, ikke et valg: den beskriver rader som lå der
-- da 047 landet, og kan derfor ikke skrives av noen etterpå. Og 'styrt'
-- betyr nøyaktig én ting — det finnes en hendelse — så merket og
-- operasjonen må følges ad i begge retninger.
CREATE OR REPLACE FUNCTION policyer_kilde_vakt() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- MERKET KAN HELLER IKKE SETTES AV EN UPDATE (Codex P2). Vakten
  -- reserverte 'historisk' bare på INSERT, og da var forbeholdet ikke et
  -- forbehold: en UPDATE — fra tabelleieren selv, eller fra en senere
  -- vedlikeholdsskriver — kunne merke en hvilken som helst bootstrap- eller
  -- umerket rad etterpå. Følgen er ikke kosmetisk. `policyversjon_i_kraft`
  -- leser nettopp dette merket som «har vært i kraft», så en rad som ALDRI
  -- ble aktivert gikk fra usann til sann: historikken begynner å påstå en
  -- aktivering uten hendelse og uten tidspunkt, og raden blir gyldig
  -- rullbakk-kilde. Det er en aktivering ingen har gjort.
  --
  -- Det som felles er OVERGANGEN INN i merket, ikke merket i seg selv: en
  -- rad som ALT er backfilt kan fortsatt oppdateres (en re-registrering
  -- bevarer merket sitt, og backfillen i del 7 løfter rader videre til
  -- 'styrt' etter at vakten er på). Bare veien inn er stengt, og den er
  -- stengt for alle: 'historisk' skrives av nøyaktig én setning, den over,
  -- og den er ferdig før vakten begynner å gjelde.
  IF NEW.aktiveringskilde = 'historisk'
     AND (TG_OP = 'INSERT'
          OR OLD.aktiveringskilde IS DISTINCT FROM 'historisk') THEN
    RAISE EXCEPTION 'policyer: aktiveringskilde=historisk er forbeholdt rader '
        'som fantes da 047 landet (%/%)', NEW.policy_id, NEW.versjon
        USING ERRCODE = 'check_violation',
              CONSTRAINT = 'policyer_kilde_ikke_historisk';
  END IF;
  -- Vakten måler at MERKET ikke lyver, ikke at det finnes. En rad uten
  -- merke sier ingenting, og da er det FK-en mot hendelsen som avgjør om
  -- operasjonen er ekte — nettopp den porten `test_versjonsrad_kan_ikke_
  -- laane_en_annens_hendelse` måler, og som en tidligere, strengere utgave
  -- av denne vakten stjal ved å kaste først. De DB-nære fixturene skriver
  -- også umerkede rader. Skriverne som faktisk aktiverer
  -- (`aktiver_policy`, `policyregister.registrer`) setter merket begge.
  IF NEW.aktiveringskilde IS NOT NULL
     AND (NEW.aktiveringskilde = 'styrt')
         IS DISTINCT FROM (NEW.aktivert_av_operasjon IS NOT NULL) THEN
    RAISE EXCEPTION 'policyer: aktiveringskilde=% og aktivert_av_operasjon=% '
        'må følges ad (%/%)', coalesce(NEW.aktiveringskilde, '<null>'),
        coalesce(NEW.aktivert_av_operasjon, '<null>'),
        NEW.policy_id, NEW.versjon
        USING ERRCODE = 'check_violation',
              CONSTRAINT = 'policyer_kilde_speiler_operasjon';
  END IF;
  RETURN NEW;
END $$;

ALTER TABLE policyer ADD CONSTRAINT policyer_aktivert_av_hendelse_fk
  FOREIGN KEY (tenant, policy_id, versjon, innholds_hash,
               aktivert_av_operasjon)
  REFERENCES policyaktivering
      (tenant, policy_id, versjon, innholds_hash, decision_operation_id)
  DEFERRABLE INITIALLY DEFERRED;

-- `policyer_operasjon_immutabel` sto HER, og vernet bare rader som ALT bar
-- en operasjon. Den står nå nederst, etter backfillen, og verner også
-- NULL-en — se begrunnelsen der (Codex P2).

-- Vakten settes opp ETTER `UPDATE ... SET aktiveringskilde = 'historisk'`
-- over: den setningen er den ENE som har lov til å skrive merket, og den
-- er ferdig når vakten begynner å gjelde.
CREATE TRIGGER policyer_kilde_vakt_trg
  BEFORE INSERT OR UPDATE ON policyer
  FOR EACH ROW EXECUTE FUNCTION policyer_kilde_vakt();


-- ------------------------------------------------------------
-- 5. aktiver_policy — hele kroppen erstattes (samme disiplin som 020–025:
--    en invariant som ikke står her er droppet, stille — kroppen over er
--    den GJELDENDE (main 05f3da6) med 047-stegene spleiset inn).
--    DROP som migrator er lovlig via skjemaeierskapet (031-presedensen,
--    målt i test_skjemaeieren_kan_droppe_en_funksjon_den_ikke_eier);
--    CREATE gir en fersk migrator-eid funksjon, ALTER OWNER trenger bare
--    medlemskapet.
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
    INSERT INTO public.policyaktivering
        (tenant, policy_id, utkast_id, runde, decision_operation_id,
         versjon, innholds_hash, diff_hash, attestant_a, attestant_b)
      VALUES (p_tenant, v_policy_id, p_utkast_id, p_runde, v_opid,
              v_ny, v_innholds_hash, v_diff_hash, v_att_a, v_att_b);

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

-- ------------------------------------------------------------
-- 6. Historikk-leseveiene (klarsignal §6, SP-1/SP-7): flaten leser aldri
--    policyer direkte — definere eid av policy-eieren, med eksplisitt
--    tenantport OG kallerens GUC-RLS som dobbelt lag.
-- ------------------------------------------------------------

-- OPPHAVET MÅ FRYSES FØR DET BLIR HISTORIKK (Codex P2).
-- `policyversjoner_for_tenant` under leser `policyutkast.rollback_av_versjon`
-- og rapporterer den som LINJE: «denne versjonen er en rullbakk av N». Den
-- kolonnen var ikke frosset av noe. `policyutkast_kolonnelaas` nevner den
-- ikke, terminalvernet der måler bare status-OVERGANGER — et `aktivert`
-- utkast kan altså oppdateres så lenge statusen står stille — og
-- kjøretidsrollen beholder UPDATE på tabellen. En direkte skriving eller en
-- fremtidig skriver kunne dermed gjøre en helt ordinær, alt aktivert
-- versjon om til «rullbakk av N», eller flytte N, uten å røre den
-- immutable hendelsen eller attestasjonene. Historikken ville da fortalt et
-- opphav ingen har attestert, og ingenting i lenken sa fra.
--
-- Kolonnen skrives KUN ved opprettelsen (`opprett_utkast`) og aldri
-- oppdateres, så den fryses helt — ikke bare «når den er satt». NULL → N
-- er nettopp fabrikasjonen: en ordinær versjon som i ettertid får et
-- opphav. Samme form som `policyer_operasjon_immutabel` (nederst, etter
-- backfillen), og av nøyaktig samme grunn.

-- OPPHAVET MÅ PEKE PÅ EN GENERASJON, IKKE PÅ ET NUMMER (Codex P2).
-- `rollback_av_versjon` bærer bare tallet, og et versjonsnummer er ikke en
-- varig identitet: `slett_ubrukt_policy` frigjør uttrykkelig
-- `(policy_id, versjon)`, og nummeret kan gjenskapes med ET ANNET innhold
-- (`test_identisk_gjenskapt_policy_gjenoppliver_ikke_slettet_generasjon`).
-- Lages en rullbakk av versjon 1, slettes serien, og gjenskapes 1 gjennom
-- en styrt aktivering før rullbakkutkastet aktiveres, står historikken og
-- påstår «rullbakk fra versjon 1» ved siden av en generasjon 1 kopien
-- aldri kom fra. Påstanden er da fabrikkert på nøyaktig samme måte som en
-- flyttet `rollback_av_versjon` ville vært — bare uten at noen skrev noe.
--
-- Kilden bindes derfor med radens GENERASJON (se `policyer.generasjon`) —
-- et sekvenstall ingen får igjen. Innholdshashen er ikke nok: samme
-- dokument kan settes inn på nytt under samme nummer etter en sletting,
-- og hashen ville da sagt «bundet» om en generasjon kopien aldri kom fra.
-- Generasjonen er valgt framfor `aktivert_av_operasjon` fordi den finnes
-- for HVER rad — også de migrerte, ubundne og bootstrappede, som ingen
-- hendelse har.
--
-- NULL betyr «kilden er ikke bundet»: rullbakkutkast fra før 047 har
-- ingen generasjon, og historikken sier det i stedet for å påstå noe den
-- ikke kan vite. En generasjon uten et versjonsnummer er derimot
-- meningsløs.
ALTER TABLE policyutkast ADD COLUMN rollback_av_generasjon BIGINT;
ALTER TABLE policyutkast ADD CONSTRAINT utkast_rullbakkekilde_krever_versjon
  CHECK (rollback_av_generasjon IS NULL OR rollback_av_versjon IS NOT NULL);

-- Begge halvdelene av opphavet fryses av SAMME vakt: en generasjon som
-- kunne flyttes alene ville gjort «bundet» til en påstand kjøretidsrollen
-- kan skrive seg til, og et nummer som kunne flyttes alene er funnet over.
CREATE TRIGGER policyutkast_rullbakkeopphav_immutabel
  BEFORE UPDATE ON policyutkast
  FOR EACH ROW WHEN (NEW.rollback_av_versjon
                     IS DISTINCT FROM OLD.rollback_av_versjon
                     OR NEW.rollback_av_generasjon
                     IS DISTINCT FROM OLD.rollback_av_generasjon)
  EXECUTE FUNCTION avvis_endring();

-- «HAR denne versjonen vært i kraft?» — ÉN definisjon (Codex P2).
--
-- Spørsmålet stilles fire steder: historikkens `aktivert`-kolonne, dens
-- sortering, rullbakkens kildeport under, og `policyregister.registrer`s
-- vakt mot å bytte ut innhold som har vært i bruk. Sto prøven skrevet ut
-- på hvert sted, kunne stedene svare ULIKT om samme rad — og da er det
-- ikke én kontrakt lenger, men fire som tilfeldigvis ligner. Nettopp den
-- differansen er feilen under: flaten visste at raden aldri hadde vært
-- aktivert, mens porten som lager rullbakken ikke spurte.
--
-- Prøven: raden er i kraft NÅ, den ble aktivert av oppsettsveien (som
-- setter tidspunktet), den bærer en aktiveringshendelse, eller den kom
-- ikke inn gjennom oppsettsveien i det hele tatt — `styrt` har en hendelse
-- bak seg, `historisk` lå der da 047 landet, og en umerket fixture-rad
-- sier ingenting vi kan bruke mot den. Bare
-- `registrer(..., aktiver=False)` faller utenfor: merket `bootstrap`, uten
-- tidspunkt, aldri aktiv.
--
-- `p_aktivert_ts` er hendelsens tidspunkt og har DEFAULT NULL fordi ikke
-- alle kallerne har joinen: `policyer_kilde_vakt` binder `styrt` og
-- `aktivert_av_operasjon` til hverandre i begge retninger, så en
-- bootstrap-merket rad KAN ikke bære en hendelse — den som utelater
-- argumentet får derfor samme svar som den som slår opp NULL.
--
-- Ren funksjon av fire skalarer: ingen tabellesing, ingenting å skjerme.
-- EXECUTE står derfor til PUBLIC som normalt; en REVOKE her hadde bare
-- vært en grant å miste for `registrer`, som kjører som migrator.
CREATE OR REPLACE FUNCTION public.policyversjon_i_kraft(
    p_aktiv BOOLEAN, p_bootstrap_aktivert_ts TIMESTAMPTZ,
    p_aktiveringskilde TEXT, p_aktivert_ts TIMESTAMPTZ DEFAULT NULL)
RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE PARALLEL SAFE
SET search_path = pg_catalog AS $$
    SELECT p_aktivert_ts IS NOT NULL
        OR p_bootstrap_aktivert_ts IS NOT NULL
        OR coalesce(p_aktiv, false)
        OR coalesce(p_aktiveringskilde, 'historisk') <> 'bootstrap';
$$;

ALTER FUNCTION public.policyversjon_i_kraft(
    BOOLEAN, TIMESTAMPTZ, TEXT, TIMESTAMPTZ) OWNER TO disponit_policy_eier;

-- OPPHAVET MÅ VÆRE SANT ALLEREDE VED FØDSELEN (Codex P2).
--
-- Frysingen over verner et opphav som ALT er skrevet: ingen kan flytte
-- det etterpå. Men fødselen var uvoktet, og `deploy/staging/migrer.py`
-- gir kjøretidsrollen direkte INSERT på `policyutkast`. En direkte — eller
-- en fremtidig, uoppmerksom — skriver kunne derfor sette inn et hvilket
-- som helst innhold sammen med versjonen og generasjonen til en levende,
-- urelatert kilde. `aktiver_policy` spør aldri om utkastet FAKTISK er en
-- kopi, og etter aktiveringen står historikken og sier `bundet` om et
-- opphav ingen har kopiert fra. Frysingen gjorde da bare løgnen varig.
--
-- Porten måler de tre påstandene «rullbakk» består av, i den rekkefølgen
-- de kan felles:
--   1. Generasjonen er NAVNGITT. Et versjonsnummer alene er en peker
--      `slett_ubrukt_policy` frigjør; en rullbakk uten generasjon er en
--      påstand uten adresse. (NULL er fortsatt lovlig i KOLONNEN — rader
--      fra før 047 har den — men ingen ny rad får fødes slik.)
--   2. Kilden FINNES, med nøyaktig den generasjonen, og har VÆRT I KRAFT.
--      Samme prøve som `policyversjon_kilde` gjør for HTTP-veien, her for
--      alle andre: en rullbakk til noe som aldri virket er ingen rullbakk.
--   3. Innholdet ER kopien. Bare `meta.versjon` og `meta.status` får
--      avvike, og bare fordi OPPRETTELSEN skriver dem: versjonen bumpes
--      (den gamle kan aldri aktiveres om igjen), og statusen normaliseres
--      til `produksjon` (den er en konsekvens av aktivering, ikke et valg).
--      Alt eier selv kunne endret, må være kildens.
--
-- Prøve 3 binder OPPRETTELSEN, ikke utkastets videre liv: et rullbakk-
-- utkast kan redigeres som ethvert annet utkast, og da er det eierens
-- egen, sporede handling. Det porten stenger, er den formen ingen har
-- gjort: et utkast som fødes med et opphav det aldri hadde.
CREATE OR REPLACE FUNCTION policyutkast_rullbakkeopphav_vakt()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_innhold JSONB;
    v_i_kraft BOOLEAN;
BEGIN
    IF NEW.rollback_av_versjon IS NULL THEN
        RETURN NEW;
    END IF;
    IF NEW.rollback_av_generasjon IS NULL THEN
        RAISE EXCEPTION 'policyutkast: en rullbakk må navngi kildens '
            'generasjon (%)', NEW.utkast_id
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'utkast_rullbakk_krever_generasjon';
    END IF;
    SELECT p.innhold,
           public.policyversjon_i_kraft(p.aktiv, p.bootstrap_aktivert_ts,
                                        p.aktiveringskilde)
      INTO v_innhold, v_i_kraft
      FROM public.policyer p
     WHERE p.tenant = NEW.tenant AND p.policy_id = NEW.policy_id
       AND p.versjon = NEW.rollback_av_versjon
       AND p.generasjon = NEW.rollback_av_generasjon;
    IF NOT FOUND OR NOT coalesce(v_i_kraft, false) THEN
        RAISE EXCEPTION 'policyutkast: rullbakkekilden %/% (generasjon %) '
            'finnes ikke eller har aldri vært i kraft', NEW.policy_id,
            NEW.rollback_av_versjon, NEW.rollback_av_generasjon
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'utkast_rullbakk_kilde_finnes';
    END IF;
    -- `meta.versjon` og `meta.status` er de eneste feltene som får avvike,
    -- og BEGGE fordi opprettelsen skriver dem selv. Sammenligningen tar
    -- dokumentet i to deler i stedet for å `jsonb_set`-e: et utkast uten
    -- `meta` skal falle på prøven, ikke få et `meta` skrevet inn av den.
    --
    -- STATUSEN ER IKKE KOPIERBAR (Codex P2). `opprett_utkast` normaliserer
    -- `meta.status` til `produksjon` for ETHVERT utkast — feltet er en
    -- konsekvens av å bli aktivert, ikke eiers valg, og `aktiver_policy`
    -- steg 1c avviser alt annet. En kilde kan likevel STÅ med en annen
    -- status: `policyregister.registrer` godtar `utkast`/`validert_pilot` i
    -- staging (`tillatte_statuser`), og aktiverer raden med den. Krevde
    -- prøven at statusen var kopiert, felte den derfor HVER rullbakk av en
    -- slik kilde — og siden `CheckViolation` er vaktens språk, ble det en
    -- 500 på en helt lovlig handling.
    --
    -- Avviket er tillatt i ÉN retning: den nye statusen må være nøyaktig
    -- den normaliseringen skriver. Er de to like, er det en ren kopi og
    -- ingenting å tillate. Slik kan ingen skriver bruke unntaket til å
    -- smugle en vilkårlig status inn under et opphav.
    IF (NEW.innhold - 'meta') IS DISTINCT FROM (v_innhold - 'meta')
       OR ((NEW.innhold -> 'meta') - 'versjon' - 'status')
          IS DISTINCT FROM ((v_innhold -> 'meta') - 'versjon' - 'status')
       OR ((NEW.innhold -> 'meta' ->> 'status')
           IS DISTINCT FROM (v_innhold -> 'meta' ->> 'status')
           AND (NEW.innhold -> 'meta' ->> 'status')
               IS DISTINCT FROM 'produksjon') THEN
        RAISE EXCEPTION 'policyutkast: en rullbakk er en KOPI av kilden '
            '(%/%)', NEW.policy_id, NEW.rollback_av_versjon
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'utkast_rullbakk_er_kopi';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER policyutkast_rullbakkeopphav_vakt_trg
  BEFORE INSERT ON policyutkast
  FOR EACH ROW EXECUTE FUNCTION policyutkast_rullbakkeopphav_vakt();

CREATE OR REPLACE FUNCTION policyversjoner_for_tenant(
    p_tenant TEXT, p_policy_id TEXT)
RETURNS TABLE (versjon TEXT, innholds_hash TEXT, aktiv BOOLEAN,
               opprettet TIMESTAMPTZ, aktivert_ts TIMESTAMPTZ,
               attestant_a TEXT, attestant_b TEXT,
               aktivert_av_operasjon TEXT, rollback_av_versjon TEXT,
               rollback_kilde TEXT, aktiveringskilde TEXT,
               aktivert BOOLEAN, innhold_finnes BOOLEAN,
               generasjon BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    -- SP-1: tenantporten er eksplisitt, ikke bare RLS-implisitt. En kaller
    -- med kontekst for én tenant kan ikke be om en annens historikk.
    IF current_setting('disponit.tenant', true) IS DISTINCT FROM p_tenant
    THEN
        RAISE EXCEPTION 'policyversjoner_for_tenant: tenantkontekst % '
            'dekker ikke %', current_setting('disponit.tenant', true),
            p_tenant USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- HISTORIKKEN ER HENDELSENES, IKKE BARE DE OVERLEVENDE RADENES
    -- (Codex P2). Spørringen var forankret i `policyer`, men
    -- `slett_ubrukt_policy` sletter uttrykkelig en aktivert, ubrukt
    -- versjon MENS hendelsen står igjen — den er uforanderlig og evig,
    -- det er hele grunnen til at den finnes. Aktiveringen forsvant da fra
    -- historikken sammen med raden, og var nummeret gjenskapt etterpå,
    -- viste flaten bare den nye generasjonen: revisjonssporet fortalte at
    -- serien hadde ÉN aktivering der loggen holdt to.
    --
    -- Den hendelsesbårne linjen bærer alt hendelsen selv vet —
    -- tidspunkt, attestanter, operasjon, rullbakk-opphav — men INNHOLDET
    -- er borte, og `innhold_finnes` sier det. Flaten tilbyr da verken
    -- diff eller rullbakk for den: `policyer` er innholdets eneste hjem,
    -- og et nummer som er gjenskapt ville ellers servert den NYE
    -- generasjonens dokument under den gamle aktiveringens linje.
    RETURN QUERY
    WITH linjer (versjon, innholds_hash, aktiv, opprettet, aktivert_ts,
                 attestant_a, attestant_b, aktivert_av_operasjon,
                 rollback_av_versjon, rollback_kilde, aktiveringskilde,
                 aktivert, innhold_finnes, generasjon) AS (
    SELECT p.versjon, p.innholds_hash, p.aktiv, p.opprettet,
           -- Aktiveringstidspunktet fra HENDELSEN (runden har ingen
           -- brukt_ts — lesesvar runde 2), og fra `bootstrap_aktivert_ts`
           -- for oppsettsveien, som ingen hendelse har (Codex P2). Bare
           -- den migrerte, ubundne raden står igjen med NULL — for den
           -- finnes tidspunktet ikke noe sted.
           coalesce(a.aktivert_ts, p.bootstrap_aktivert_ts),
           a.attestant_a, a.attestant_b,
           p.aktivert_av_operasjon, u.rollback_av_versjon,
           -- TILSTANDEN til opphavet, ikke bare nummeret (Codex P2):
           --   bundet  — generasjonen kopien ble tatt fra ER den som
           --             bærer nummeret nå (samme `policyer.generasjon`)
           --   borte   — nummeret finnes ikke lenger, eller bærer en ANNEN
           --             generasjon: kilden er slettet/gjenskapt. Også når
           --             det gjenskapte innholdet er BYTE-LIKT — det er en
           --             ny rad, og opphavspåstanden gjelder den gamle.
           --   ubundet — rullbakk fra før generasjonen fantes; kilden kan
           --             ikke avgjøres, og flaten sier det i stedet for å
           --             gjette
           -- NULL når versjonen ikke er en rullbakk i det hele tatt.
           CASE WHEN u.rollback_av_versjon IS NULL THEN NULL
                WHEN u.rollback_av_generasjon IS NULL THEN 'ubundet'
                WHEN rb.generasjon IS NOT DISTINCT FROM
                     u.rollback_av_generasjon THEN 'bundet'
                ELSE 'borte' END::TEXT,
           -- Veien raden kom inn. Rader fra før 047 bærer 'historisk';
           -- NULL kan bare forekomme på direkte innsatte fixture-rader.
           coalesce(p.aktiveringskilde, 'historisk'),
           -- HAR denne versjonen noen gang vært i kraft (Codex P2)?
           -- `registrer(..., aktiver=False)` legger inn en versjon UTEN å
           -- aktivere den: ingen hendelse, ingen `bootstrap_aktivert_ts`.
           -- Uten dette skillet lånte historikken `opprettet` og viste
           -- REGISTRERINGStidspunktet under «Aktivert» — en aktivering som
           -- aldri skjedde. Rader fra før 047 ('historisk') og aktive rader
           -- regnes som aktivert: for dem er det TIDSPUNKTET som mangler,
           -- ikke aktiveringen. Prøven bor i `policyversjon_i_kraft`, delt
           -- med sorteringen under og med rullbakkens kildeport.
           public.policyversjon_i_kraft(p.aktiv, p.bootstrap_aktivert_ts,
                                        p.aktiveringskilde, a.aktivert_ts),
           -- Raden lever: innholdet kan leses, diffes og rulles tilbake.
           true,
           -- GENERASJONEN LINJEN VISER (Codex P2). Et versjonsnummer
           -- frigjøres av sletting og kan gjenskapes; generasjonen kan
           -- ikke. Flaten sender den tilbake når eier bekrefter en
           -- rullbakk, slik `slett_policy` sender identiteten den viste,
           -- så kopien blir tatt av den raden eier faktisk så — ikke av
           -- en erstatning som kom til mellom visning og klikk.
           p.generasjon
      FROM public.policyer p
      -- Koblingen går via OPERASJONEN, ikke via (policy_id, versjon)
      -- (Codex P2). `aktivert_av_operasjon` ER FK-en til hendelsen, og
      -- den er entydig; versjonsnummeret er det ikke over tid. Er en
      -- versjon slettet og gjenskapt, står den gamle generasjonens
      -- hendelse igjen med samme nummer — en nummerkobling ville da gitt
      -- den levende raden TO historikklinjer, med den slettede
      -- generasjonens attestanter på den ene. Ubundet historisk rad
      -- (`aktivert_av_operasjon IS NULL`) gir NULL som før.
      LEFT JOIN public.policyaktivering a
        ON a.tenant = p.tenant
       AND a.decision_operation_id = p.aktivert_av_operasjon
      LEFT JOIN public.policyutkast u
        ON u.tenant = a.tenant AND u.utkast_id = a.utkast_id
      -- Generasjonen som bærer kildenummeret NÅ. Den kan være en annen
      -- enn den kopien ble tatt fra (sletting frigjør nummeret), og den
      -- kan mangle helt — begge deler gir 'borte' over.
      LEFT JOIN public.policyer rb
        ON rb.tenant = u.tenant AND rb.policy_id = u.policy_id
       AND rb.versjon = u.rollback_av_versjon
     WHERE p.tenant = p_tenant AND p.policy_id = p_policy_id
    UNION ALL
    -- Aktiveringer uten en overlevende rad. `NOT EXISTS` mot OPERASJONEN,
    -- samme entydige nøkkel joinen over bruker: er nummeret gjenskapt,
    -- bærer den nye raden en ANNEN operasjon (eller ingen), og den gamle
    -- hendelsen står fortsatt uten rad — den skal ha sin egen linje, ikke
    -- smelte sammen med gjenskapingen.
    SELECT a.versjon, a.innholds_hash, false,
           -- «Opprettet» finnes ikke lenger; aktiveringen er det eneste
           -- tidspunktet hendelsen selv kjenner, og det er det ærligste
           -- svaret her. Kolonnen er NOT NULL i returtypen, og flaten
           -- viser uansett `aktivert_ts` for en aktivert linje.
           a.aktivert_ts, a.aktivert_ts,
           a.attestant_a, a.attestant_b,
           a.decision_operation_id, u.rollback_av_versjon,
           CASE WHEN u.rollback_av_versjon IS NULL THEN NULL
                WHEN u.rollback_av_generasjon IS NULL THEN 'ubundet'
                WHEN rb.generasjon IS NOT DISTINCT FROM
                     u.rollback_av_generasjon THEN 'bundet'
                ELSE 'borte' END::TEXT,
           -- En hendelse finnes bare for den styrte veien; det er
           -- invarianten `policyer_kilde_speiler_operasjon` holder.
           'styrt', true,
           -- Innholdet fulgte raden. Uten dette merket ville flaten bedt
           -- `policyversjon_innhold` om et nummer som enten er borte
           -- (404) eller bærer en HELT ANNEN generasjons dokument.
           false,
           -- Generasjonen fulgte raden den også; hendelsen kjenner bare
           -- innholdets hash, og en hash er ikke en identitet.
           NULL::BIGINT
      FROM public.policyaktivering a
      LEFT JOIN public.policyutkast u
        ON u.tenant = a.tenant AND u.utkast_id = a.utkast_id
      LEFT JOIN public.policyer rb
        ON rb.tenant = u.tenant AND rb.policy_id = u.policy_id
       AND rb.versjon = u.rollback_av_versjon
     WHERE a.tenant = p_tenant AND a.policy_id = p_policy_id
       AND NOT EXISTS (SELECT 1 FROM public.policyer p2
                        WHERE p2.tenant = a.tenant
                          AND p2.aktivert_av_operasjon
                              = a.decision_operation_id)
    )
    -- Kronologien er AKTIVERINGENS, ikke registreringens (Codex P2):
    -- `opprettet` er når raden ble skrevet, og en rad kan skrives lenge
    -- før den aktiveres. `opprettet` er siste utvei — for de MIGRERTE
    -- radene, der aktiveringen skjedde, men tidspunktet ikke finnes noe
    -- sted.
    --
    -- En ALDRI AKTIVERT rad står utenfor denne kronologien (Codex P2).
    -- Sorterte den på `opprettet` sammen med aktiveringene, la en fersk
    -- `registrer(..., aktiver=False)` seg øverst som om den var nyest
    -- aktivert — og dro med seg diffens default-retning, som leser
    -- nettopp de to øverste. Den sorteres derfor etter alle
    -- aktiveringene, med samme test som `aktivert`-kolonnen over.
    --
    -- `aktivert` er nå BEREGNET i grenene, så sorteringen leser den
    -- kolonnen i stedet for å regne prøven ut på nytt: to utskrifter av
    -- samme spørsmål er to spørsmål, og de kan gå fra hverandre.
    SELECT l.versjon, l.innholds_hash, l.aktiv, l.opprettet, l.aktivert_ts,
           l.attestant_a, l.attestant_b, l.aktivert_av_operasjon,
           l.rollback_av_versjon, l.rollback_kilde, l.aktiveringskilde,
           l.aktivert, l.innhold_finnes, l.generasjon
      FROM linjer l
     ORDER BY CASE WHEN l.aktivert THEN 0 ELSE 1 END,
              coalesce(l.aktivert_ts, l.opprettet) DESC,
              l.versjon DESC, l.innhold_finnes DESC;
END $$;

-- Innholdet i EN NAVNGITT GENERASJON (Codex P2). Versjonsnummeret er en
-- PEKER, ikke en identitet: `slett_ubrukt_policy` frigjør det med vilje,
-- og det samme `(policy_id, versjon)` kan senere navngi en helt annen
-- generasjon. Uten `p_generasjon` leste diffen da erstatningen mens flaten
-- fortsatt merket den med den valgte versjonens etikett — og en diff er
-- nettopp en påstand om HVA som skiller de to dokumentene eier så.
--
-- Generasjonen er et ARGUMENT, ikke noe kalleren kan sammenligne etterpå:
-- er porten valgfri, er hullet der fortsatt for enhver kaller som utelater
-- den, og da er den ingen port. NULL faller derfor på samme prøve som en
-- foreldet verdi.
--
-- Avslaget er `invalid_parameter_value`, ikke `no_data_found`, av samme
-- grunn som i `policyversjon_kilde`: raden FINNES og er lesbar — det er
-- bare ikke den generasjonen som ble bedt om. HTTP-laget svarer 409, og
-- flaten laster historikken på nytt.
CREATE OR REPLACE FUNCTION policyversjon_innhold(
    p_tenant TEXT, p_policy_id TEXT, p_versjon TEXT, p_generasjon BIGINT)
RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v            JSONB;
    v_generasjon BIGINT;
BEGIN
    IF current_setting('disponit.tenant', true) IS DISTINCT FROM p_tenant
    THEN
        RAISE EXCEPTION 'policyversjon_innhold: tenantkontekst % dekker '
            'ikke %', current_setting('disponit.tenant', true), p_tenant
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Innholdet OG identiteten i ETT oppslag, som i `policyversjon_kilde`:
    -- måles de i hvert sitt kall, kan raden ha blitt slettet og gjenskapt
    -- mellom prøven og lesingen.
    SELECT p.innhold, p.generasjon INTO v, v_generasjon
      FROM public.policyer p
     WHERE p.tenant = p_tenant AND p.policy_id = p_policy_id
       AND p.versjon = p_versjon;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'policyversjon_innhold: ukjent versjon %',
            p_versjon USING ERRCODE = 'no_data_found';
    END IF;
    IF v_generasjon IS DISTINCT FROM p_generasjon THEN
        RAISE EXCEPTION 'policyversjon_innhold: versjon % bæres nå av '
            'generasjon %, ikke %', p_versjon, v_generasjon, p_generasjon
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN v;
END $$;

-- Kilden for en RULLBAKK: innholdet OG radens generasjon, lest i SAMME
-- snapshot (Codex P2). Rullbakken lagrer `rollback_av_generasjon`, og de
-- to må komme fra ETT oppslag: hentes innholdet nå og identiteten senere,
-- kan `(policy_id, versjon)` ha blitt slettet og gjenskapt i mellomtiden,
-- og kopien ville blitt bundet til en generasjon den aldri kom fra —
-- nøyaktig løgnen kolonnen finnes for å hindre.
--
-- KILDEN MÅ HA VÆRT I KRAFT (Codex P2). En rullbakk er en påstand om at vi
-- går TILBAKE til noe: lineagen skriver `rollback_av_versjon`, og
-- historikken leser den som «utkast fra versjon N». `registrer(...,
-- aktiver=False)` legger med vilje inn versjoner som ALDRI har vært i
-- kraft — arbeidsstykker, lagt inn før de tas i bruk — og en kopi av et
-- slikt arbeidsstykke er en helt vanlig ny versjon, ikke en tilbakerulling.
-- Uten porten her kunne flaten (eller enhver annen kaller) be om nettopp
-- den kopien, og historikken ville i ettertid fortalt at et utkast som
-- aldri hadde virket en gang var det vi vendte tilbake til.
--
-- Avslaget er BEVISST ikke `no_data_found`: raden finnes, den er lesbar,
-- og den kan diffes. Det er rollen som kilde den ikke har, og
-- `invalid_parameter_value` sier det — HTTP-laget svarer 409, ikke 404.
CREATE OR REPLACE FUNCTION policyversjon_kilde(
    p_tenant TEXT, p_policy_id TEXT, p_versjon TEXT)
RETURNS TABLE (innhold JSONB, generasjon BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_innhold    JSONB;
    v_generasjon BIGINT;
    v_i_kraft    BOOLEAN;
BEGIN
    IF current_setting('disponit.tenant', true) IS DISTINCT FROM p_tenant
    THEN
        RAISE EXCEPTION 'policyversjon_kilde: tenantkontekst % dekker '
            'ikke %', current_setting('disponit.tenant', true), p_tenant
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Innholdet, generasjonen OG kildedugeligheten i ETT oppslag, av samme
    -- grunn som de to første: måles dugeligheten i et eget kall, kan raden
    -- ha blitt slettet og gjenskapt mellom prøven og kopien.
    SELECT p.innhold, p.generasjon,
           public.policyversjon_i_kraft(p.aktiv, p.bootstrap_aktivert_ts,
                                        p.aktiveringskilde)
      INTO v_innhold, v_generasjon, v_i_kraft
      FROM public.policyer p
     WHERE p.tenant = p_tenant AND p.policy_id = p_policy_id
       AND p.versjon = p_versjon;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'policyversjon_kilde: ukjent versjon %',
            p_versjon USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT v_i_kraft THEN
        RAISE EXCEPTION 'policyversjon_kilde: versjon % har aldri vært '
            'aktivert og er ingen rullbakk-kilde', p_versjon
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY SELECT v_innhold, v_generasjon;
END $$;

ALTER FUNCTION policyversjoner_for_tenant(TEXT, TEXT)
    OWNER TO disponit_policy_eier;
ALTER FUNCTION policyversjon_innhold(TEXT, TEXT, TEXT, BIGINT)
    OWNER TO disponit_policy_eier;
ALTER FUNCTION policyversjon_kilde(TEXT, TEXT, TEXT)
    OWNER TO disponit_policy_eier;
SET LOCAL ROLE disponit_policy_eier;
REVOKE ALL ON FUNCTION policyversjoner_for_tenant(TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION policyversjon_innhold(TEXT, TEXT, TEXT, BIGINT)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION policyversjon_kilde(TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION policyversjoner_for_tenant(TEXT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION policyversjon_innhold(TEXT, TEXT, TEXT, BIGINT)
    TO disponit;
GRANT EXECUTE ON FUNCTION policyversjon_kilde(TEXT, TEXT, TEXT)
    TO disponit;
RESET ROLE;
-- Eieren trenger lesing på utkastet for rollback-joinen (policyer og
-- runden har den fra 013-æraen via aktiver_policy; grants er idempotente).
GRANT SELECT ON policyutkast TO disponit_policy_eier;
GRANT SELECT ON policyer TO disponit_policy_eier;

-- ------------------------------------------------------------
-- 7. Backfill (klarsignal §2.5): entydig eller NULL, aldri gjetting.
--    FORCE RLS binder også migratoren, og tenantene kan ikke engang
--    ENUMERERES uten kontekst — derfor en MIGRASJONSLOKAL, selv-
--    reverserende bro: scoped backfill-policyer som opprettes og DROPPES
--    i samme transaksjon (samme doktrine som nødbroene i deploy: broer
--    skal være selv-reverserende). Ingen tie-breaker på tidspunkt:
--    flertydig match → begge bindingskolonner blir stående NULL. En match
--    har TO sider, og «flertydig» gjelder begge: flere runder som passer
--    versjonen, ELLER flere versjoner som konkurrerer om runden.
-- ------------------------------------------------------------
CREATE POLICY backfill_047 ON policyer FOR SELECT
    TO disponit_migrator USING (true);
CREATE POLICY backfill_047 ON policyutkast FOR SELECT
    TO disponit_migrator USING (true);
CREATE POLICY backfill_047 ON aktiveringsrunde FOR SELECT
    TO disponit_migrator USING (true);
CREATE POLICY backfill_047 ON aktiveringsattestasjon FOR SELECT
    TO disponit_migrator USING (true);

DO $$
DECLARE
    r RECORD;
    v_runde RECORD;
    v_antall INT;
    v_kandidater INT;
    v_att_a TEXT;
    v_att_b TEXT;
    v_bundet INT := 0;
    v_aapne INT := 0;
    v_flertydige INT := 0;
    v_kontekst TEXT;
BEGIN
    v_kontekst := current_setting('disponit.tenant', true);
    FOR r IN SELECT p.tenant, p.policy_id, p.versjon, p.innholds_hash,
                    p.opprettet
               FROM public.policyer p
              WHERE p.status = 'produksjon'
                AND p.aktivert_av_operasjon IS NULL
              ORDER BY p.tenant, p.policy_id, p.opprettet
    LOOP
        -- Skrivingene under skjer med RADENS tenantkontekst (038-mønsteret:
        -- kryss-tenant-autoriteten brukes én rad om gangen, bundet til
        -- nøyaktig den radens tenant).
        PERFORM set_config('disponit.tenant', r.tenant, true);

        SELECT count(*) INTO v_antall
          FROM public.aktiveringsrunde ar
          JOIN public.policyutkast pu
            ON pu.tenant = ar.tenant AND pu.utkast_id = ar.utkast_id
         WHERE ar.tenant = r.tenant AND pu.policy_id = r.policy_id
           AND ar.status = 'brukt' AND ar.decision_operation_id IS NOT NULL
           AND ar.utkast_innholds_hash = r.innholds_hash;
        IF v_antall <> 1 THEN
            v_aapne := v_aapne + 1;
            IF v_antall > 1 THEN
                v_flertydige := v_flertydige + 1;
            END IF;
            CONTINUE;
        END IF;

        -- Den ANDRE siden av samme match (Codex P1). Tellingen over måler
        -- bare hvor mange runder som passer versjonen; to versjonsrader med
        -- SAMME `innholds_hash` og ÉN brukt runde gir v_antall = 1 for
        -- BEGGE. Uten denne tellingen arver den raden som tilfeldigvis ble
        -- opprettet først attestasjonene — `ORDER BY ... opprettet` i
        -- ytterløkka er en lesrekkefølge, ikke et bevis — og den andre
        -- slipper unna bare fordi runden nå er tatt. Duplikat innhold er
        -- uttrykkelig mulig for historiske versjoner (samme policy
        -- gjenaktivert), og valget kan lande på en INAKTIV versjon.
        -- Hendelsen er udødelig, så en gjetning her kan aldri rettes:
        -- flertydig → begge står åpne, som ellers.
        --
        -- Raden vi står i teller alltid seg selv (den er per definisjon
        -- 'produksjon' og ubundet her), så < 1 er umulig; og ingen
        -- flertydig rad bindes, så tellingen er uavhengig av rekkefølgen.
        SELECT count(*) INTO v_kandidater
          FROM public.policyer p2
         WHERE p2.tenant = r.tenant AND p2.policy_id = r.policy_id
           AND p2.status = 'produksjon'
           AND p2.aktivert_av_operasjon IS NULL
           AND p2.innholds_hash = r.innholds_hash;
        IF v_kandidater > 1 THEN
            v_aapne := v_aapne + 1;
            v_flertydige := v_flertydige + 1;
            CONTINUE;
        END IF;

        SELECT ar.utkast_id, ar.runde, ar.decision_operation_id,
               ar.diff_hash, ar.aktivert_som_versjon
          INTO v_runde
          FROM public.aktiveringsrunde ar
          JOIN public.policyutkast pu
            ON pu.tenant = ar.tenant AND pu.utkast_id = ar.utkast_id
         WHERE ar.tenant = r.tenant AND pu.policy_id = r.policy_id
           AND ar.status = 'brukt' AND ar.decision_operation_id IS NOT NULL
           AND ar.utkast_innholds_hash = r.innholds_hash;

        -- To versjoner med identisk innhold kan begge peke entydig på
        -- SAMME runde (gjenaktivert innhold) — da er runden alt bundet,
        -- og DENNE versjonen forblir åpen: å velge ville vært gjetting.
        IF v_runde.aktivert_som_versjon IS NOT NULL THEN
            v_aapne := v_aapne + 1;
            CONTINUE;
        END IF;

        SELECT min(q.bruker_id) FILTER (WHERE q.rn = 1),
               min(q.bruker_id) FILTER (WHERE q.rn = 2)
          INTO v_att_a, v_att_b
          FROM (SELECT a.bruker_id,
                       row_number() OVER (ORDER BY a.ts, a.id) AS rn
                  FROM public.aktiveringsattestasjon a
                 WHERE a.tenant = r.tenant
                   AND a.utkast_id = v_runde.utkast_id
                   AND a.runde = v_runde.runde AND NOT a.er_forfatter
                   AND a.diff_hash = v_runde.diff_hash) q
         WHERE q.rn <= 2;
        IF v_att_a IS NULL THEN
            -- Ingen kvalifiserende attestasjon å binde — åpen.
            v_aapne := v_aapne + 1;
            CONTINUE;
        END IF;

        INSERT INTO public.policyaktivering
            (tenant, policy_id, utkast_id, runde, decision_operation_id,
             versjon, innholds_hash, diff_hash, attestant_a, attestant_b,
             aktivert_ts)
          VALUES (r.tenant, r.policy_id, v_runde.utkast_id, v_runde.runde,
                  v_runde.decision_operation_id, r.versjon,
                  r.innholds_hash, v_runde.diff_hash, v_att_a, v_att_b,
                  r.opprettet);
        UPDATE public.policyer
           SET aktivert_av_operasjon = v_runde.decision_operation_id,
               aktiveringskilde      = 'styrt'
         WHERE tenant = r.tenant AND policy_id = r.policy_id
           AND versjon = r.versjon;
        UPDATE public.aktiveringsrunde
           SET aktivert_som_versjon = r.versjon
         WHERE tenant = r.tenant AND utkast_id = v_runde.utkast_id
           AND runde = v_runde.runde;
        v_bundet := v_bundet + 1;
    END LOOP;
    PERFORM set_config('disponit.tenant', coalesce(v_kontekst, ''), true);
    RAISE NOTICE '047-backfill policyaktivering: % bundet, % åpne '
        '(derav % flertydige)', v_bundet, v_aapne, v_flertydige;
END $$;

-- Broen rives i samme transaksjon som den ble bygget.
DROP POLICY backfill_047 ON policyer;
DROP POLICY backfill_047 ON policyutkast;
DROP POLICY backfill_047 ON aktiveringsrunde;
DROP POLICY backfill_047 ON aktiveringsattestasjon;


-- OGSÅ NULL-EN ER EN BINDING (Codex P2). Vakten sto lenger oppe og fyrte
-- bare når raden ALT bar en operasjon, så overgangen NULL → hendelse sto
-- åpen etter at migrasjonen var ferdig. Ingen av de to prøvene rundt den
-- fanger den: FK-en binder hendelsen til `(tenant, policy_id, versjon,
-- innholds_hash)` og sier ingenting om GENERASJONEN, og `policyer_kilde_
-- vakt` måler bare at merket og operasjonen følges ad — setter man
-- `aktiveringskilde='styrt'` i samme setning, er den fornøyd.
--
-- Følgen er en tilskrivning ingen har gjort. `slett_ubrukt_policy` sletter
-- en aktivert, ubrukt versjon mens `policyaktivering` blir stående
-- (immutabel), og det samme `(policy_id, versjon, innholds_hash)` kan
-- senere gjenskapes som en NY, ubundet generasjon. En vedlikeholdsskriver
-- kunne da binde erstatningen til den gamle, etterlatte hendelsen — og
-- historikken leser attestantene GJENNOM `aktivert_av_operasjon` (se
-- `policyversjon_lineage`), så den gamle aktiveringens tidspunkt og begge
-- attestantene ble stående under en generasjon de aldri så. Attestasjonen
-- er nettopp det som ikke kan flyttes: de to signerte ÉN rad.
--
-- Én skriver har lov til å gjøre overgangen, og den er ferdig når vakten
-- settes opp: backfillen over. Samme mønster som `policyer_kilde_vakt_trg`
-- og `UPDATE ... SET aktiveringskilde = 'historisk'` — engangsovergangen
-- bevares ved at porten stenges ETTER den, ikke ved at porten får et
-- unntak den ikke kan skille fra misbruk.
--
-- Ingen produksjonsvei taper noe: `aktiver_policy` SETTER INN raden med
-- operasjonen sin (5b), og `policyregister.registrer` rører aldri kolonnen
-- — den nekter tvert imot å skrive en rad som bærer den.
CREATE TRIGGER policyer_operasjon_immutabel
  BEFORE UPDATE ON policyer
  FOR EACH ROW WHEN (NEW.aktivert_av_operasjon
                     IS DISTINCT FROM OLD.aktivert_av_operasjon)
  EXECUTE FUNCTION avvis_endring();
