-- ============================================================
-- 042 — adjudikatorsynligheten bindes til den autentiserte tenanten
--       (Codex P1 på #97, levert 93 sekunder etter mergen)
--
-- FUNNET, I SIN HELHET. 041 §9 ga `disponit_domains_adjudicator` SELECT på
-- `unntak` gjennom en policy uten tenant-ledd:
--
--     tenant = '__plattform_domener'
--     AND sakskilde = 'domeneovertakelse'
--     AND CURRENT_USER = 'disponit_domains_adjudicator'
--
-- Rollen ser altså HVER overtakelsessak i klyngen — det står med rene ord i
-- 041s egen dokumentasjon, og det var ment slik: «Rollen gir SYNLIGHETEN;
-- filteret gir OMFANGET.» Filteret er `utfordrer_tenant=%s` i
-- `api/domeneovertakelse.py`, altså i APPLIKASJONEN.
--
-- Det holder bare så lenge applikasjonens SQL er den eneste SQL-en som
-- kjøres. Runtime-rollen `disponit` har medlemskap i adjudikatoren
-- `WITH SET TRUE` (`deploy/staging/oppsett-postgresql.sh`), og §9.1s
-- restriktive `reservert_navnerom` slipper adjudikatoren gjennom eksplisitt.
-- Én SQL-injeksjon — eller én kompromittert runtime-forbindelse — gjør
-- derfor `SET ROLE disponit_domains_adjudicator`, utelater WHERE-leddet, og
-- får hele saksflaten i ETT svar: hvert omstridte vertsnavn, utfordreren og
-- motparten ved navn, generasjonen og lineagen, for samtlige kunder.
--
-- Dette er den TREDJE ulike døra inn til det samme rommet, og de to første
-- forklarer hvorfor denne ble stående: 041 lukket API-filteret (adjudikator
-- hos A som leser Bs saker) og den permissive GUC-veien (§9.1, restriktiv
-- policy). Begge gjerdene måler noe annet enn rolleskiftet. §9.1 har
-- adjudikatoren på INNSIDEN av allowlisten sin, så den restriktive policyen
-- er per konstruksjon blind for nøyaktig denne veien.
--
-- HVA SOM ER ROTEN. Ikke at rollen er for vid, men at avgrensningen bor på
-- feil side av grensen. En tenant-avgrensning håndhevet i applikasjonens
-- WHERE er en avgrensning enhver som kan skrive SQL kan la være å skrive.
-- Roten er at databasen selv ikke vet hvem som spør.
--
-- HVA SOM IKKE ER FIKSEN. To nærliggende former ble forkastet:
--
--   * Å legge `utfordrer_tenant = current_setting('disponit.tenant', true)`
--     inn i §9-policyen. `disponit.tenant` er en fritt skrivbar GUC — §9.1s
--     egen kommentar er skrevet på nettopp det. Angriperen som alt gjør
--     `SET ROLE` setter like gjerne GUC-en. Gjerdet ville stått i veien for
--     ingen.
--   * Å ta tenanten inn som ARGUMENT til en SECURITY DEFINER-funksjon. Et
--     argument er like fritt som predikatet det erstatter: den som kan
--     kalle `f('A')` kan kalle `f('B')`. Formen ser ut som en grense og er
--     det ikke.
--
-- FIKSEN. Adjudikatorrollens leseflate FJERNES — policyen droppes, grantet
-- trekkes — og de to API-veiene går i stedet gjennom claimer-eide SECURITY
-- DEFINER-funksjoner som leser omfanget sitt av SESJONEN, ikke av kallet:
--
--     NULLIF(current_setting('disponit.tenant', true), '')
--
-- Formen er 041s egen (§9.2): da vakten trengte ett svar og ikke et snitt,
-- ble den claimer-eid og SECURITY DEFINER nettopp for å slippe å dele ut
-- adjudikatorrollen. Denne migrasjonen fører de to gjenstående lesningene
-- etter samme regel, og da har ingen bruk for rollen igjen.
--
-- HVOR LANGT DETTE REKKER — presist, uten å overselge. `disponit.tenant` er
-- fortsatt skrivbar, så en kompromittert runtime kan sette den til B og se
-- Bs saker. Men det er nøyaktig den rekkevidden den kompromitteringen ALT
-- har mot Bs ordinære `unntak`-rader gjennom `tenant_isolasjon` (003 §9):
-- adjudikasjonen gir ikke lenger noe PÅ TOPPEN av tenant-isolasjonen. Det
-- som forsvinner, er det aggregatet ingen enkelt-tenant-kompromittering
-- skal kunne nå: hele klyngen i ett svar, uten å gjette et eneste
-- tenant-navn. Et uavgrenset lesehull byttes mot den grensen resten av
-- systemet allerede hviler på — ikke mot et løfte om at GUC-en er trygg.
--
-- FAIL-CLOSED FALLER UT AV FORMEN, den er ikke lagt oppå den: er GUC-en
-- uspesifisert eller tom, gir `NULLIF(...)` NULL, og `utfordrer_tenant =
-- NULL` er aldri sant. En glemt `sett_tenant` gir null rader, ikke alle.
-- ============================================================

-- ------------------------------------------------------------
-- 1. De to tenantbundne lesningene
--
-- Claimer-eide, som §9.2: inne i funksjonen er CURRENT_USER
-- `disponit_m37_claimer`, som ser plattformradene via `m37_dispatcher`
-- (005) og står i §9.1s allowlist. Ingen av dem tar tenant som argument —
-- det er hele poenget, og det er derfor ingen kallsted kan utvide omfanget
-- sitt.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_m37_claimer;

-- 1.1 Oppslaget bak attestasjonsveien (erstatter `slaa_opp_sak`s
--     `SET LOCAL ROLE` + `utfordrer_tenant=%s`).
--
-- TERMINALE SAKER RETURNERES OGSÅ, og det er en bevart egenskap, ikke en
-- forglemmelse: 041 slo fast at adjudikatoren skal få vite at saken er
-- avgjort (`avgi` avviser den med `attestasjon_avvist`), ikke at den «ikke
-- finnes» — et 404 på en sak man nettopp avgjorde er stillhet der svaret
-- finnes. Derfor står det ingen `NOT terminal` her, i motsetning til køen.
CREATE OR REPLACE FUNCTION overtakelsessak_for_utfordrer(p_sak_id BIGINT)
RETURNS TABLE (hostname_ref TEXT, autorisasjonsgenerasjon BIGINT,
               utfordrer_tenant TEXT, saksrevisjon BIGINT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
  SELECT u.hostname_ref, u.autorisasjonsgenerasjon, u.utfordrer_tenant,
         u.saksrevisjon
    FROM public.unntak u
   WHERE u.id = p_sak_id
     AND u.tenant = '__plattform_domener'
     AND u.sakskilde = 'domeneovertakelse'
     AND u.utfordrer_tenant
         = NULLIF(current_setting('disponit.tenant', true), '')
$$;

-- 1.2 Adjudikatorkøen (erstatter `SET LOCAL ROLE` + samme filter).
--
-- Keysettet er FLYTTET INN, ikke gjenskapt: `(saksrevisjon_ts, id)` og
-- sorteringen er 041s, med 041s begrunnelse — `saksrevisjon_ts` og ikke
-- `ts`, fordi A→B→C skifter utfordrer på den EKSISTERENDE raden og `ts` er
-- kolonnelåst (§11). Ligger nøkkelen igjen i applikasjonen mens filteret
-- flyttes hit, kan de to drive fra hverandre; de svarer på samme spørsmål
-- og hører i samme uttrykk.
--
-- `p_grense` klemmes mot `LISTE_MAKS` (api/lesing.py) HER OGSÅ. Taket i
-- `_grense` er den ekte kontrollen; dette er ikke en ny grensekontrakt,
-- bare nektelsen av å la en funksjon med SECURITY DEFINER ta imot et
-- ubegrenset LIMIT fra kalleren sin.
CREATE OR REPLACE FUNCTION overtakelsessaker_for_utfordrer(
    p_etter_saksrevisjon_ts TIMESTAMPTZ, p_etter_id BIGINT, p_grense INT)
RETURNS TABLE (id BIGINT, hostname_ref TEXT, saksrevisjon BIGINT,
               autorisasjonsgenerasjon BIGINT, utfordrer_tenant TEXT,
               tapt_tenant TEXT, status TEXT, ts TIMESTAMPTZ,
               saksrevisjon_ts TIMESTAMPTZ)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
  SELECT u.id, u.hostname_ref, u.saksrevisjon, u.autorisasjonsgenerasjon,
         u.utfordrer_tenant, u.tapt_tenant, u.status, u.ts, u.saksrevisjon_ts
    FROM public.unntak u
   WHERE u.tenant = '__plattform_domener'
     AND u.sakskilde = 'domeneovertakelse'
     AND NOT u.terminal
     AND u.utfordrer_tenant
         = NULLIF(current_setting('disponit.tenant', true), '')
     AND (p_etter_id IS NULL
          OR (u.saksrevisjon_ts, u.id)
             > (p_etter_saksrevisjon_ts, p_etter_id))
   ORDER BY u.saksrevisjon_ts, u.id
   LIMIT LEAST(GREATEST(COALESCE(p_grense, 1), 1), 100)
$$;

-- Rettighetene gis av FUNKSJONENES eier, altså her inne — samme regel som
-- 041 §9.2. Default-deny gjenopprettes eksplisitt: `CREATE FUNCTION` gir
-- EXECUTE til PUBLIC, og en SECURITY DEFINER-funksjon som står åpen for
-- PUBLIC er verre enn rollen den avløser.
REVOKE ALL ON FUNCTION overtakelsessak_for_utfordrer(BIGINT) FROM PUBLIC;
REVOKE ALL ON FUNCTION overtakelsessaker_for_utfordrer(
    TIMESTAMPTZ, BIGINT, INT) FROM PUBLIC;
-- Staging kjører API-et som `disponit` (019, Codex P1); `disponit_domains_admin`
-- er den andre veien inn i attestasjonen (041 §-slutt) og får samme snitt.
GRANT EXECUTE ON FUNCTION overtakelsessak_for_utfordrer(BIGINT) TO disponit;
GRANT EXECUTE ON FUNCTION overtakelsessaker_for_utfordrer(
    TIMESTAMPTZ, BIGINT, INT) TO disponit;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles
              WHERE rolname = 'disponit_domains_admin') THEN
    GRANT EXECUTE ON FUNCTION overtakelsessak_for_utfordrer(BIGINT)
        TO disponit_domains_admin;
    GRANT EXECUTE ON FUNCTION overtakelsessaker_for_utfordrer(
        TIMESTAMPTZ, BIGINT, INT) TO disponit_domains_admin;
  END IF;
END $$;

RESET ROLE;

-- ------------------------------------------------------------
-- 2. Adjudikatorrollens leseflate fjernes
--
-- Funksjonene over er ikke et TILLEGG til rollen — de er det som gjør den
-- overflødig. Blir policyen og grantet stående ved siden av dem, er funnet
-- ikke fikset: `SET ROLE` + uavgrenset SELECT virker nøyaktig som før, og
-- den nye veien er bare en pen dør ved siden av den åpne.
--
-- ROLLEN OG MEDLEMSKAPET BLIR STÅENDE — og det er et bevisst valg, ikke en
-- halv jobb. Det er PRIVILEGIET som er funnet, ikke medlemskapet: etter
-- linjene over er `disponit_domains_adjudicator` en rolle uten en eneste
-- rettighet. En `SET ROLE` til den gir `InsufficientPrivilege` på første
-- SELECT. Å kunne anta en rolle som ikke kan noe, er ikke en vei inn.
--
-- Å FJERNE MEDLEMSKAPET LIKEVEL VILLE BRUKKET FERSKE INSTALLASJONER, og det
-- er verdt å skrive ned hvorfor, for det ser ut som det åpenbare neste
-- steget: 041 §17.1 RAISER hvis `disponit` ikke er medlem med SET. 041 er
-- utrullet og kjører FØR 042 på enhver ny base. Trakk oppsettet medlemskapet,
-- ville 041 feilt i steg 6 — etter at tjenestene er stoppet — på nøyaktig de
-- installasjonene som trenger 042. En sikkerhetsfiks som feller den friske
-- basen for å fjerne en inert rolletilhørighet, har byttet et lukket hull mot
-- et åpent vedlikeholdsvindu.
--
-- Roller og medlemskap er uansett KLYNGEobjekter og settes i
-- `deploy/staging/oppsett-postgresql.sh`, aldri i en migrasjon —
-- migratorrollen har verken eller skal ha CREATEROLE (041 §0, opp.sh).
--
-- GJERDET MOT AT NOEN GIR RETTIGHETEN TILBAKE står i §2.1 under, og det er
-- der den egentlige holdbarheten ligger: selv et gjenopprettet
-- `GRANT SELECT ON unntak TO disponit_domains_adjudicator` gir ikke saksflaten
-- tilbake, fordi rollen da ikke lenger står i den restriktive policyens
-- allowlist.
-- ------------------------------------------------------------
DROP POLICY IF EXISTS domeneovertakelse_adjudikator ON unntak;
REVOKE SELECT ON unntak FROM disponit_domains_adjudicator;

-- 2.1 Allowlisten i §9.1 mister adjudikatoren.
--
-- Uten dette leddet blir fjerningen over reversibel ved et uhell: én
-- `GRANT SELECT ON unntak TO disponit_domains_adjudicator` — fra et
-- gjenkjørt oppsett, en operatør som «gjenoppretter» det 041 beskriver —
-- ville gitt hele saksflaten tilbake, fordi den restriktive policyen
-- fortsatt slapp rollen forbi. Allowlisten skal navngi dem som FAKTISK
-- trenger plattformnavnerommet, ikke dem som en gang gjorde det.
--
-- Formen er 041 §9.1s egen, ord for ord der den kan være det: prefiksbredt
-- ledd, eieren lest fra katalogen (FIX-009 kan flytte eierskapet), samme
-- tre tabeller — saken i `unntak`, speilet i `unntak_historikk`, og
-- `revisjonslogg` som bærer `referansepayload` med vertsnavnet og BEGGE
-- tenant-ID-ene i klartekst.
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['unntak', 'unntak_historikk', 'revisjonslogg']
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS reservert_navnerom ON %I', t);
    EXECUTE format($p$
      CREATE POLICY reservert_navnerom ON %I AS RESTRICTIVE
        USING      (tenant NOT LIKE E'\\_\\_%%'
                    OR CURRENT_USER = 'disponit_m37_claimer'
                    OR CURRENT_USER = (SELECT pg_catalog.pg_get_userbyid(
                                                c.relowner)
                                         FROM pg_catalog.pg_class c
                                        WHERE c.oid = %L::pg_catalog.regclass))
        WITH CHECK (tenant NOT LIKE E'\\_\\_%%'
                    OR CURRENT_USER = 'disponit_m37_claimer'
                    OR CURRENT_USER = (SELECT pg_catalog.pg_get_userbyid(
                                                c.relowner)
                                         FROM pg_catalog.pg_class c
                                        WHERE c.oid = %L::pg_catalog.regclass))
    $p$, t, 'public.' || t, 'public.' || t);
  END LOOP;
END $$;

-- ------------------------------------------------------------
-- 3. Porten: migrasjonen måler sitt eget resultat
--
-- 041 lærte dette den harde veien (§0, opp.sh): en migrasjon som REGISTRERES
-- som kjørt mens gjerdet den beskriver ikke står, gir et system som ser
-- utrullet ut og ikke er det. Her er innsatsen en leseflate, så porten
-- spør katalogen rett ut — ikke om funksjonene finnes, men om rollen
-- fortsatt kommer til.
-- ------------------------------------------------------------
DO $$
DECLARE v TEXT;
BEGIN
  IF EXISTS (SELECT 1 FROM pg_policy
              WHERE polname = 'domeneovertakelse_adjudikator'
                AND polrelid = 'unntak'::regclass) THEN
    RAISE EXCEPTION '042 §3: policyen domeneovertakelse_adjudikator står '
      'fortsatt på unntak — adjudikatorrollen ser hele klyngens saksflate';
  END IF;
  IF has_table_privilege('disponit_domains_adjudicator', 'unntak', 'SELECT')
  THEN
    RAISE EXCEPTION '042 §3: disponit_domains_adjudicator har fortsatt '
      'SELECT på unntak';
  END IF;
  SELECT string_agg(t, ', ') INTO v FROM unnest(
      ARRAY['unntak', 'unntak_historikk', 'revisjonslogg']) AS t
   WHERE NOT EXISTS (SELECT 1 FROM pg_policy
                      WHERE polname = 'reservert_navnerom'
                        AND polrelid = ('public.' || t)::regclass);
  IF v IS NOT NULL THEN
    RAISE EXCEPTION '042 §3: reservert_navnerom mangler på % — '
      'plattformnavnerommet står uten restriktiv policy', v;
  END IF;
END $$;
