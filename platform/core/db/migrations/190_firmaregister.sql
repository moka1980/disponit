-- 190: FIRMAREGISTERET — firmaet vet endelig hvem det selv er.
--
-- MÅLT FØR DENNE: det finnes ingen firmatabell. Et firma eksisterer bare som
-- en TEKSTSTRENG spredt over 347 `tenant`-kolonner. Ingen rad sier «firma X
-- finnes, heter Y, har orgnr Z, og er i prøveperiode til dato D».
-- `sett_tenant` tar imot hvilken som helst streng og validerer den ikke mot
-- noe register, fordi registeret ikke fantes.
--
-- Konsekvensene, alle tre målt i prod:
--   1. Ingen prøveperiode kan bokføres. En prøveperiode er ikke en funksjon
--      man bolter på; det er en tilstand på en rad som ikke var skrevet.
--   2. Firmaets EGET navn står kopiert i fire tabeller — `purreplan`,
--      `kampanjeavsender`, `kundeserviceavsender` og `tilbudsavsender` —
--      hver med sitt eget skjema. Alle fire sier «Fjordlys Elektro AS» i
--      dag, men det er fire oppsettsteg et selvbetjent firma må finne og
--      fylle ut før fire moduler kan sende noe. Denne migrasjonen skriver
--      den kanoniske raden; PR 2b lar de fire lese fra den.
--   3. Ingenting definerte hva et tenantnavn ER. `init-tenant.sh _plattform`
--      ville virket. Her får det endelig en form.
--
-- ÉN RAD PER TENANT, og `tenant` ER primærnøkkelen. Firmaet er ikke noe som
-- HAR en tenant; det ER tenanten. En egen `firma_id` ville invitert til at
-- de to kunne divergere.
-- ============================================================

-- ------------------------------------------------------------------
-- Organisasjonsnummer: MOD-11, ikke bare ni sifre.
--
-- `part` (183) sjekker `^[0-9]{9}$`, og det er riktig DER: en kunde kan
-- være utenlandsk, og feltet er valgfritt. Men firmaraden er vår EGEN
-- juridiske identitet — den skal til Altinn og Brønnøysund, og et ugyldig
-- nummer oppdages først der, måneder senere, av noen andre. Kontrollsifferet
-- er billig å regne og gratis å stole på.
--
-- Vektene 3,2,7,6,5,4,3,2 og kontrollsiffer = 11 − (sum mod 11), der 11 → 0
-- og 10 er ugyldig (Brønnøysundregistrenes definisjon).
-- ------------------------------------------------------------------
CREATE OR REPLACE FUNCTION er_gyldig_orgnummer(p_nr TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql IMMUTABLE
SET search_path = pg_catalog AS $$
DECLARE
    v_vekter INT[] := ARRAY[3, 2, 7, 6, 5, 4, 3, 2];
    v_sum    INT := 0;
    v_rest   INT;
    v_kontroll INT;
    i        INT;
BEGIN
    -- Ledende null finnes ikke i et organisasjonsnummer — det ville gjort
    -- det til et åttesifret tall. Uten denne linjen passerer '000000000'
    -- MOD-11 (sum 0 → kontrollsiffer 0) og blir en «gyldig» identitet som
    -- ikke tilhører noen.
    --
    -- Jeg legger IKKE på regelen om at nummeret må begynne på 8 eller 9.
    -- Den stemmer for dagens serier, men er en tildelingsregel Brønnøysund
    -- kan endre, og en CHECK som avviser en ekte kunde er verre enn en som
    -- slipper gjennom et syntetisk nummer.
    IF p_nr IS NULL OR p_nr !~ '^[1-9][0-9]{8}$' THEN
        RETURN false;
    END IF;
    FOR i IN 1..8 LOOP
        v_sum := v_sum + substr(p_nr, i, 1)::INT * v_vekter[i];
    END LOOP;
    v_rest := v_sum % 11;
    v_kontroll := CASE WHEN v_rest = 0 THEN 0 ELSE 11 - v_rest END;
    -- Kontrollsiffer 10 finnes ikke: nummeret er da ugyldig, ikke avrundet.
    IF v_kontroll = 10 THEN
        RETURN false;
    END IF;
    RETURN v_kontroll = substr(p_nr, 9, 1)::INT;
END $$;

COMMENT ON FUNCTION er_gyldig_orgnummer(TEXT) IS
    'MOD-11-kontroll av norsk organisasjonsnummer (Brønnøysund). '
    'Brukes av firma-tabellen; `part.orgnummer` holder seg til ni sifre '
    'fordi en kunde kan være utenlandsk.';

-- ------------------------------------------------------------------
-- Selve registeret.
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS firma (
    -- Formen på et tenantnavn, definert for første gang: små bokstaver,
    -- sifre og bindestrek, som en DNS-etikett. Understrek er UTELATT med
    -- vilje — `_oidc` og `_plattform` er plattformens egne kontekster, og
    -- et kundefirma skal aldri kunne kollidere med dem.
    tenant      TEXT PRIMARY KEY
                CONSTRAINT firma_tenant_form
                CHECK (tenant ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
    navn        TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    orgnummer   TEXT
                CONSTRAINT firma_orgnr_gyldig
                CHECK (orgnummer IS NULL OR public.er_gyldig_orgnummer(orgnummer)),

    -- LIVSSYKLUSEN. `prove` er utgangspunktet fordi et firma som registrerer
    -- seg selv ikke har betalt for noe ennå.
    status      TEXT NOT NULL DEFAULT 'prove'
                CONSTRAINT firma_status_lukket
                CHECK (status IN ('prove', 'aktiv', 'utlopt', 'stengt')),
    prove_utloper DATE,
    stengt_ts   TIMESTAMPTZ,
    stengt_av   TEXT,

    -- Angrefristen etter stenging. Innen den kan firmaet gjenåpnes med alt
    -- i behold; etter den destrueres nøkkelen og reaperen rydder resten.
    slettefrist_dogn INT NOT NULL DEFAULT 30
                CHECK (slettefrist_dogn BETWEEN 7 AND 365),

    opprettet   TIMESTAMPTZ NOT NULL DEFAULT now(),
    endret      TIMESTAMPTZ NOT NULL DEFAULT now(),
    endret_av   TEXT NOT NULL CHECK (endret_av ~ '[^[:space:]]'),

    -- Tilstanden og datoene MÅ henge sammen. En `prove` uten frist er en
    -- evig gratisperiode ingen har bestemt; en `stengt` uten tidspunkt gjør
    -- angrefristen umulig å regne ut.
    CONSTRAINT firma_status_helhet CHECK (
        (status = 'prove'  AND prove_utloper IS NOT NULL AND stengt_ts IS NULL)
     OR (status = 'aktiv'  AND stengt_ts IS NULL)
     OR (status = 'utlopt' AND prove_utloper IS NOT NULL AND stengt_ts IS NULL)
     OR (status = 'stengt' AND stengt_ts IS NOT NULL AND stengt_av IS NOT NULL))
);

ALTER TABLE firma ENABLE ROW LEVEL SECURITY;
ALTER TABLE firma FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON firma
    USING (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

CREATE INDEX firma_provefrist ON firma (prove_utloper)
    WHERE status = 'prove';
CREATE INDEX firma_stengt ON firma (stengt_ts) WHERE status = 'stengt';

-- ============================================================
-- DØRENE.
--
-- `disponit` får ALDRI direkte INSERT/UPDATE på `firma`. Med et direkte
-- grant ville RLS riktignok holdt firmaet inne i sin egen rad — men
-- ingenting hindret det i å sette sin egen `status` til 'aktiv' og gi seg
-- selv gratis abonnement. Livssyklusen er husets beslutning, ikke kundens,
-- og da må den gå gjennom en dør som kjenner de lovlige overgangene.
--
-- Dørene er SECURITY DEFINER eid av migrator (som eier tabellen), samme
-- form som `laas_godkjenner` i 013. FORCE RLS gjelder også eieren, så
-- `krev_tenantkontekst` er det som slipper dem inn i riktig rad — ikke et
-- privilegium.
-- ============================================================

CREATE OR REPLACE FUNCTION firma_registrer(p_tenant TEXT, p_navn TEXT,
                                           p_orgnummer TEXT,
                                           p_prove_dogn INT,
                                           p_aktor TEXT)
RETURNS DATE LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_frist DATE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'firma_registrer');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'firma_registrer: et firma har en registrator'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_prove_dogn IS NULL OR p_prove_dogn NOT BETWEEN 1 AND 365 THEN
        RAISE EXCEPTION 'firma_registrer: prøveperioden må være 1-365 døgn'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_frist := (now() AT TIME ZONE 'Europe/Oslo')::date
               + p_prove_dogn * interval '1 day';

    -- REGISTRERING ER IKKE EN OPPDATERING. `part_registrer` er idempotent
    -- fordi en import kaller den tusen ganger på rad med samme referanse.
    -- Her er det motsatt: finnes firmaet allerede, er et nytt kall enten en
    -- feil eller et forsøk på å overta noen andres rad. Begge skal si fra.
    INSERT INTO public.firma (tenant, navn, orgnummer, status,
                              prove_utloper, endret_av)
         VALUES (p_tenant, btrim(p_navn), nullif(btrim(p_orgnummer), ''),
                 'prove', v_frist, p_aktor)
    ON CONFLICT (tenant) DO NOTHING;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'firma_registrer: % er allerede registrert', p_tenant
            USING ERRCODE = 'unique_violation';
    END IF;
    RETURN v_frist;
END $$;
REVOKE ALL ON FUNCTION firma_registrer(TEXT, TEXT, TEXT, INT, TEXT) FROM PUBLIC;


CREATE OR REPLACE FUNCTION firma_oppdater(p_tenant TEXT, p_navn TEXT,
                                          p_orgnummer TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'firma_oppdater');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'firma_oppdater: en endring har en endrer'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- IDENTITET, ALDRI STATUS. Firmaet kan rette sitt eget navn; det kan
    -- ikke forlenge sin egen prøveperiode.
    UPDATE public.firma
       SET navn = btrim(p_navn),
           orgnummer = nullif(btrim(p_orgnummer), ''),
           endret = now(), endret_av = p_aktor
     WHERE firma.tenant = p_tenant;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'firma_oppdater: % er ikke registrert', p_tenant
            USING ERRCODE = 'foreign_key_violation';
    END IF;
END $$;
REVOKE ALL ON FUNCTION firma_oppdater(TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;


-- ------------------------------------------------------------------
-- Livssyklusen. De lovlige overgangene står i ÉN tabell i koden, ikke
-- spredt som IF-er, så den som leser kan se hele maskinen på én skjerm:
--
--     prove  → aktiv | utlopt | stengt
--     aktiv  → stengt
--     utlopt → aktiv | stengt
--     stengt → aktiv        (gjenåpning, KUN innen angrefristen)
--
-- `stengt → aktiv` er den eneste som har en tidsbetingelse, og det er med
-- vilje: etter angrefristen er nøkkelen destruert, og en «gjenåpning» ville
-- gitt et tomt firma som SER helt ut. Bedre å nekte enn å love noe tilbake
-- som ikke finnes lenger.
-- ------------------------------------------------------------------
CREATE OR REPLACE FUNCTION firma_sett_status(p_tenant TEXT, p_status TEXT,
                                             p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_naa TEXT; v_stengt TIMESTAMPTZ; v_frist INT; v_lovlig TEXT[];
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'firma_sett_status');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'firma_sett_status: en overgang har en aktør'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT status, stengt_ts, slettefrist_dogn
      INTO v_naa, v_stengt, v_frist
      FROM public.firma WHERE firma.tenant = p_tenant FOR UPDATE;
    IF v_naa IS NULL THEN
        RAISE EXCEPTION 'firma_sett_status: % er ikke registrert', p_tenant
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    v_lovlig := CASE v_naa
        WHEN 'prove'  THEN ARRAY['aktiv', 'utlopt', 'stengt']
        WHEN 'aktiv'  THEN ARRAY['stengt']
        WHEN 'utlopt' THEN ARRAY['aktiv', 'stengt']
        WHEN 'stengt' THEN ARRAY['aktiv']
    END;
    IF NOT (p_status = ANY(v_lovlig)) THEN
        RAISE EXCEPTION 'firma_sett_status: % → % er ikke en lovlig overgang',
                        v_naa, p_status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF v_naa = 'stengt' AND p_status = 'aktiv'
       AND now() > v_stengt + v_frist * interval '1 day' THEN
        RAISE EXCEPTION 'firma_sett_status: angrefristen på % døgn er ute',
                        v_frist
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    UPDATE public.firma
       SET status = p_status,
           stengt_ts = CASE WHEN p_status = 'stengt' THEN now() ELSE NULL END,
           stengt_av = CASE WHEN p_status = 'stengt' THEN p_aktor ELSE NULL END,
           endret = now(), endret_av = p_aktor
     WHERE firma.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION firma_sett_status(TEXT, TEXT, TEXT) FROM PUBLIC;


CREATE OR REPLACE FUNCTION firma_hent(p_tenant TEXT)
RETURNS TABLE (tenant TEXT, navn TEXT, orgnummer TEXT, status TEXT,
               prove_utloper DATE, stengt_ts TIMESTAMPTZ,
               slettefrist_dogn INT, opprettet TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'firma_hent');
    RETURN QUERY
    SELECT f.tenant, f.navn, f.orgnummer, f.status, f.prove_utloper,
           f.stengt_ts, f.slettefrist_dogn, f.opprettet
      FROM public.firma f WHERE f.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION firma_hent(TEXT) FROM PUBLIC;

-- HVA RUNTIME FÅR, OG HVA DEN IKKE FÅR.
--
-- `firma_registrer`, `firma_oppdater` og `firma_hent` er KUNDENS egne
-- handlinger: registrere seg, rette sitt eget navn, se sin egen rad. De
-- hører til web-API-rollen.
--
-- `firma_sett_status` gjør det IKKE, og det er hele poenget med at
-- livssyklusen ligger i en dør. Døra krever `krev_tenantkontekst` — men
-- den binder tenanten, ikke FULLMAKTEN. En kundesesjon står i sin egen
-- kontekst, så med et grant til runtime ville enhver rute som nådde døra
-- latt kunden sette sin egen status til 'aktiv' og gi seg selv gratis
-- abonnement. (CodeRabbit fant det: jeg hadde skrevet begrunnelsen i
-- kommentaren over og så gitt bort nøkkelen ti linjer under.)
--
-- Døra står derfor ugrantet til runtime. Eieren (migrator) beholder sin
-- EXECUTE, og PR 2b gir den til plattformeier-veien — den ene som har
-- fullmakt til å avgjøre om et firma betaler.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        GRANT EXECUTE ON FUNCTION firma_registrer(TEXT, TEXT, TEXT, INT, TEXT)
            TO disponit;
        GRANT EXECUTE ON FUNCTION firma_oppdater(TEXT, TEXT, TEXT, TEXT)
            TO disponit;
        GRANT EXECUTE ON FUNCTION firma_hent(TEXT) TO disponit;
    END IF;
END $$;

-- ============================================================
-- PRØVEPERIODEN SOM FAKTISK UTLØPER.
--
-- Uten denne er `prove_utloper` en dato ingen leser — og en prøveperiode
-- som aldri tar slutt er ikke en prøveperiode, den er et gratisabonnement
-- med en misvisende etikett.
--
-- Sveipen er KRYSS-TENANT av natur: den skal treffe hvert firmas frist, og
-- det finnes ingen ett-tenant-kontekst som er riktig å sette. Formen er
-- 184s ordrett (som igjen er 088s): egen radpolicy for claimeren, funksjon
-- eid av claimeren, og bredden innelukket i sveipen — alle per-tenant-
-- dørene over krever `krev_tenantkontekst` og filtrerer på parameteret.
-- ============================================================
CREATE POLICY firma_sveiper ON firma TO disponit_m37_claimer
    USING (CURRENT_USER = 'disponit_m37_claimer')
    WITH CHECK (CURRENT_USER = 'disponit_m37_claimer');

-- Radpolicyen sier hvilke RADER claimeren får se; grantet sier om den får
-- røre TABELLEN i det hele tatt. Begge trengs — uten dette feiler sveipen
-- med «permission denied» før policyen i det hele tatt blir vurdert.
GRANT SELECT, UPDATE ON firma TO disponit_m37_claimer;

SET LOCAL ROLE disponit_m37_claimer;

CREATE FUNCTION firma_sveip_proveutlop(p_grense INT DEFAULT 100)
RETURNS TABLE (tenant TEXT, utlopt_dato DATE)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_idag DATE;
BEGIN
    -- Fristen er en DATO, og datoer er lokale. Uten tidssonen ville et
    -- firma i Norge mistet prøveperioden sin klokken 01:00 om sommeren.
    v_idag := (now() AT TIME ZONE 'Europe/Oslo')::date;
    FOR r IN
        SELECT f.tenant AS t, f.prove_utloper AS d
          FROM public.firma f
         WHERE f.status = 'prove' AND f.prove_utloper < v_idag
         ORDER BY f.prove_utloper
         LIMIT p_grense
         FOR UPDATE SKIP LOCKED
    LOOP
        UPDATE public.firma
           SET status = 'utlopt', endret = now(), endret_av = 'proveutlop'
         WHERE firma.tenant = r.t;
        tenant := r.t; utlopt_dato := r.d;
        RETURN NEXT;
    END LOOP;
END $$;

-- REVOKE OG GRANT SKJER MENS VI FORTSATT ER EIEREN. Dette er 184s
-- rekkefølge (dens REVOKE står før dens RESET ROLE), og den er ikke
-- kosmetisk: den implisitte `EXECUTE TO PUBLIC` en ny funksjon får, er gitt
-- av EIEREN. Trekker migrator den tilbake etter `RESET ROLE`, svarer
-- Postgres «no privileges could be revoked» — en WARNING, ikke en feil —
-- og funksjonen står igjen med `=X` i ACL-en.
--
-- Jeg skrev den i feil rekkefølge, og målte det: acl ble
-- `{=X/disponit_m37_claimer, disponit_m37_claimer=X/...}`. Enhver rolle i
-- basen kunne da kalt en SECURITY DEFINER-sveip som utløper prøveperioden
-- til HVERT firma. Porten under måler ACL-en direkte, ikke bare at de
-- navngitte rollene mangler.
--
-- HER AVVIKER JEG OGSÅ BEVISST FRA 184s GRANTFORM, og avviket skal stå
-- navngitt. 184 har en ELSIF som gir reaperen til runtime når ryddekontoen
-- mangler, slik at et utviklingsmiljø uten `disponit_domener` fortsatt kan
-- rydde. Blast-radiusen er ulik: `reap_partkontakt` blanker kontaktrader som
-- ALLEREDE er deaktivert og forfalt, mens denne setter hvert firma i basen
-- ut av prøveperioden. En reservevei som gir web-API-rollen den knappen er
-- ikke verdt bekvemmeligheten.
--
-- Prod HAR `disponit_domener` (målt), så sveipen havner der. Mangler rollen,
-- står funksjonen ugrantet — eieren kan fortsatt kalle den, og porten gjør
-- nettopp det. En sveip som ikke kjører er et synlig problem; en sveip
-- kunderollen kan kalle er et usynlig ett.
REVOKE ALL ON FUNCTION firma_sveip_proveutlop(INT) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_domener') THEN
        GRANT EXECUTE ON FUNCTION firma_sveip_proveutlop(INT)
            TO disponit_domener;
    END IF;
END $$;

RESET ROLE;

-- ------------------------------------------------------------------
-- M-4: LAGERET NAVNGIS. Et lager som ikke står i `retensjonslager` kan
-- ingen personvernsak dekke — funnet fra eiers gjennomgang, og grunnen
-- til at 184 måtte legge til to rader etterpå.
-- ------------------------------------------------------------------
-- `retensjonslager` eies av `disponit_lager_eier`, ikke av migrator (093).
-- Samme rollebytte som 184 gjør rundt sin egen registrering.
SET LOCAL ROLE disponit_lager_eier;

INSERT INTO retensjonslager
    (lager_id, relasjon, klasse, tenantkolonne, alderskolonne,
     reapetkolonne, fristkilde, frist_dogn, reaper, dom,
     dom_begrunnelse, dom_migrasjon)
VALUES
    -- FIRMARADEN har ingen frist så lenge firmaet lever, og det er en
    -- beslutning, ikke en forglemmelse: raden ER kundeforholdet, og den
    -- refereres av alt firmaet eier. Fristen slår inn først ved stenging
    -- (`slettefrist_dogn`), og da er det hele tenanten som ryddes — en
    -- operasjon som får sin egen migrasjon, ikke en reaper her.
    ('firma', 'firma', 'konfigurasjon', 'tenant', 'opprettet',
     NULL, NULL, NULL, NULL, 'uten_frist_akseptert',
     'Firmaraden ER kundeforholdet og refereres av alt firmaet eier. '
     'Fristen gjelder først ved stenging (slettefrist_dogn, standard 30 '
     'døgn), og selve tenantryddingen er sin egen arbeidsflyt: nøkkelen '
     'destrueres først, deretter ryddes radene.',
     '190_firmaregister.sql')
ON CONFLICT (lager_id) DO NOTHING;

RESET ROLE;
