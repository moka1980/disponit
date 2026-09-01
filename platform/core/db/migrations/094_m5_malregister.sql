-- 094: M-5 malregisteret — dokumentfamilier, versjoner, komponenter og
-- de obligatoriske feltene. v1 er REGISTERET og UTFYLLINGEN; ingen
-- DOCX/PDF-rendring, ingen semantisk versjonsdiff, ingen publisering
-- til kontorpakke eller e-signering (manifestets v1-dom, bokstavelig).
--
-- FORBILDET ER 079_utsendingstekst, og formen er speilet medlem for
-- medlem: versjonert, append-only, dør-eid, enveis skjuling i stedet
-- for sletting, RLS+FORCE, SECURITY DEFINER bak `krev_tenantkontekst`.
-- 079 er ÉN kundeeid tekst; M-5 generaliserer den til en malFAMILIE med
-- deklarerte felt. Der 079 har `(tekst_id, versjon)` i primærnøkkelen
-- har M-5 en egen `malversjon`-rad, fordi versjonen her bærer en
-- LIVSSYKLUS (utkast → publisert → tilbaketrukket) og ikke bare et
-- innhold.
--
-- HVOR JEG BEVISST AVVIKER FRA 079 (og hvorfor):
--
--  1. 079 skjuler med `skjult_ts` (NULL → satt). M-5 skjuler med en
--     STATUSOVERGANG (`publisert` → `tilbaketrukket`), fordi en mal har
--     tre tilstander og ikke to: et utkast er ikke en skjult publisert
--     versjon, det er en versjon som aldri har vært i kraft.
--     Enveisheten er den samme — `tilbaketrukket` er terminal.
--
--  2. 079s vakt lar UPDATE av skjulingen passere på ENHVER rad. M-5s
--     komponent- og feltvakter avviser UPDATE og DELETE TOTALT, for
--     enhver rolle, også eieren. Et malinnhold er ikke en tilstand som
--     kan flippes; det er bytene versjonen ER. En versjon redigeres ved
--     at det fødes en ny — 079s egen setning, gjort absolutt.
--
--  3. 079 tar innholdet som parametre. M-5 tar HELE versjonen i ett
--     kall (komponenter + felt som JSONB, 091-formen «hele runden i ett
--     kall»). Da finnes ikke en halvskrevet versjon: en versjon uten
--     komponenter, eller med en komponent som refererer et felt ingen
--     har deklarert, er ikke en tilstand basen kan stå i mellom to
--     kall. Det er også grunnen til at det ikke finnes en
--     «rediger utkast»-dør: utkastet forfattes ferdig eller ikke.
--
--  4. 079 validerer parametrene med IF-ledd i døren. M-5 lar
--     CHECK-ENE og NOT NULL-ENE være valideringen (dørene skriver rått
--     inn og lar basen felle dommen), fordi en totalitet som bare
--     finnes i en funksjonskropp er en totalitet neste skrivevei ikke
--     arver. Merk at INGEN felt får en stille default på veien inn:
--     `laast` og `paakrevd` blir NULL — altså avvist — hvis kalleren
--     sender noe som ikke er en boolean. En «hjelpsom» default her
--     ville låst opp en klausul, eller gjort et påkrevd felt valgfritt,
--     på grunn av en skrivefeil.
--
-- DE TO DOMMENE MODULEN HVILER PÅ, som DATAMODELL:
--
--  * FAKTA DIKTES ALDRI. `m5_fyll_mal` returnerer NULL for et felt uten
--    dekning i inndataene — ikke tom streng, ikke feltnøkkelen, ikke en
--    plassholder som ser ut som innhold. Den returnerer en KOMPONENT-
--    LISTE og ikke én tekststreng, nettopp for at flaten skal kunne
--    MARKERE hullet i stedet for å gjemme det i en streng.
--
--  * LÅSTE KLAUSULER ER LÅST I DATAMODELLEN. En `laast`-komponent er
--    `komponenttype='klausul'` (CHECK), og en klausul har ingen
--    `feltnokkel` (CHECK) — utfyllingen har derfor ingen inngang å
--    overstyre den gjennom. Forsøket er urepresenterbart, ikke
--    usannsynlig.
--
-- V1-DOMMEN, HÅNDHEVET AV POSTGRESQL OG IKKE AV DISIPLIN:
-- `m5_fyll_mal` er erklært STABLE. En ikke-volatil funksjon kan ikke
-- utføre INSERT/UPDATE/DELETE i det hele tatt — planleggeren avviser
-- det. «Utfyllingen lagrer ingenting» er altså en EGENSKAP VED
-- FUNKSJONEN, ikke en påstand om hva kroppen tilfeldigvis inneholder i
-- dag. Det er den sterkeste formen invarianten `utfylling_skrev_dokument`
-- kan ha uten en egen rolle uten skriverettigheter.
--
-- MERK — FORUTSETNING SOM IKKE ER OPPFYLT I FUNDAMENTET (PR #326):
-- `disponit_mal_eier` er OPPRETTET i `oppsett-postgresql.sh` og
-- `ci.yml`, men `GRANT disponit_mal_eier TO disponit_migrator WITH
-- INHERIT FALSE` mangler begge steder — og uten den kan migrator ikke
-- `SET LOCAL ROLE` til eieren under. Det samme gjelder de fire andre
-- klyngeeierne. Linjene hører fundamentet til (fem spor som hver legger
-- sin linje i ci.yml er nøyaktig kollisjonen fundamentet finnes for å
-- unngå); porten `test_m5_malregister.py::
-- test_port0_migrator_kan_sette_rolle_til_maleier` måler mangelen så
-- den er RØD FØR deploy, ikke etter at tjenestene er stoppet.

-- ============================================================
-- 1. malfamilie — dokumentfamilien («arbeidsavtale», «tilbudsbrev»).
--    Navnet er en ETIKETT og kan rettes; identiteten og fødselen er
--    frosset, og en familie slettes aldri (versjonene under den er
--    evidens for hva som en gang var i kraft).
-- ============================================================
CREATE TABLE malfamilie (
    tenant TEXT NOT NULL,
    familie_id UUID NOT NULL,
    navn TEXT NOT NULL CHECK (length(btrim(navn)) BETWEEN 1 AND 200),
    -- Tom beskrivelse er en ekte tilstand; fraværet skrives som NULL,
    -- aldri som en tom streng som later som den er utfylt.
    beskrivelse TEXT CHECK (beskrivelse IS NULL
                            OR length(btrim(beskrivelse)) BETWEEN 1 AND 2000),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (length(btrim(opprettet_av)) > 0),
    -- 079-formen: SP-2-gjenspill måles materielt, ikke på id-en alene.
    innhold_hash TEXT NOT NULL,
    CONSTRAINT malfamilie_pk PRIMARY KEY (tenant, familie_id)
);

CREATE FUNCTION m5_familie_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'malfamilie: % avvist — en familie bærer'
            ' versjonshistorikk og slettes aldri', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.familie_id IS DISTINCT FROM OLD.familie_id
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av
       OR NEW.innhold_hash IS DISTINCT FROM OLD.innhold_hash THEN
        RAISE EXCEPTION 'malfamilie: identiteten og fødselen er frosset'
            ' — kun navn og beskrivelse kan rettes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m5_familie_vakt() FROM PUBLIC;
CREATE TRIGGER m5_familie_vakt
    BEFORE UPDATE OR DELETE ON malfamilie
    FOR EACH ROW EXECUTE FUNCTION m5_familie_vakt();
CREATE TRIGGER m5_familie_ingen_truncate
    BEFORE TRUNCATE ON malfamilie
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

ALTER TABLE malfamilie ENABLE ROW LEVEL SECURITY;
ALTER TABLE malfamilie FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON malfamilie
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- INGEN UPDATE. Vakten over definerer hva en lovlig navnerettelse
-- ville vært, men v1 har ingen dør som gjør en — og en rettighet som
-- deles ut før den har en kaller er 035-lærdommen speilvendt. Landes
-- en omdøpingsdør i v2, følger `GRANT UPDATE (navn, beskrivelse)` med
-- den, i samme diff som porten som måler den.
GRANT SELECT, INSERT ON malfamilie TO disponit_mal_eier;

-- ============================================================
-- 2. malversjon — versjonen med sin LIVSSYKLUS.
--
--    APPEND-ONLY ETTER PUBLISERING: en publisert versjon endres aldri,
--    den etterfølges av et nytt versjonsnummer. De ENESTE lovlige
--    UPDATE-ene er de to livssyklusovergangene:
--        utkast     → publisert      (setter publisert_ts/av)
--        publisert  → tilbaketrukket (setter tilbaketrukket_ts/av)
--    `tilbaketrukket` er TERMINAL — 079s enveis skjuling, i statusform.
--
--    Et utkast kan verken publiseres om igjen eller trekkes tilbake:
--    et utkast som ikke skal i kraft blir bare aldri publisert. Å gi
--    det en egen «forkastet»-vei ville vært en tredje overgang å måle,
--    for en tilstand ingen leser.
-- ============================================================
CREATE TABLE malversjon (
    tenant TEXT NOT NULL,
    versjon_id UUID NOT NULL,
    familie_id UUID NOT NULL,
    versjonsnr INT NOT NULL CHECK (versjonsnr >= 1),
    status TEXT NOT NULL
        CHECK (status IN ('utkast', 'publisert', 'tilbaketrukket')),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (length(btrim(opprettet_av)) > 0),
    publisert_ts TIMESTAMPTZ,
    publisert_av TEXT,
    tilbaketrukket_ts TIMESTAMPTZ,
    tilbaketrukket_av TEXT,
    innhold_hash TEXT NOT NULL,
    CONSTRAINT malversjon_pk PRIMARY KEY (tenant, versjon_id),
    CONSTRAINT malversjon_familie_fk FOREIGN KEY (tenant, familie_id)
        REFERENCES malfamilie (tenant, familie_id),
    CONSTRAINT malversjon_nr_unik UNIQUE (tenant, familie_id, versjonsnr),
    -- TOTALITETEN som CHECK, ikke som disiplin: hver av de tre
    -- statusene har nøyaktig ett lovlig sett av tidsstempler. En
    -- «publisert» rad uten publiseringstidspunkt, eller en
    -- «tilbaketrukket» som aldri var publisert, er urepresenterbar —
    -- også for eieren, også ved direkte DML.
    CONSTRAINT malversjon_status_total CHECK (
        (status = 'utkast'
            AND publisert_ts IS NULL AND publisert_av IS NULL
            AND tilbaketrukket_ts IS NULL AND tilbaketrukket_av IS NULL)
     OR (status = 'publisert'
            AND publisert_ts IS NOT NULL AND publisert_av IS NOT NULL
            AND tilbaketrukket_ts IS NULL AND tilbaketrukket_av IS NULL)
     OR (status = 'tilbaketrukket'
            AND publisert_ts IS NOT NULL AND publisert_av IS NOT NULL
            AND tilbaketrukket_ts IS NOT NULL
            AND tilbaketrukket_av IS NOT NULL))
);

CREATE FUNCTION m5_versjon_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'malversjon: % avvist — versjonene er'
            ' append-only (redigering = ny versjon)', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Alt som IKKE er livssyklusen er frosset fra fødselen — også i
    -- utkaststilstanden. Innholdet bor i komponentene, og de er
    -- append-only; en versjon som kunne bytte familie eller
    -- versjonsnummer i ettertid ville brutt bindingen komponentene
    -- allerede peker på.
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.versjon_id IS DISTINCT FROM OLD.versjon_id
       OR NEW.familie_id IS DISTINCT FROM OLD.familie_id
       OR NEW.versjonsnr IS DISTINCT FROM OLD.versjonsnr
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av
       OR NEW.innhold_hash IS DISTINCT FROM OLD.innhold_hash THEN
        RAISE EXCEPTION 'malversjon: innholdet og identiteten er frosset'
            ' — en publisert mal endres aldri, den etterfølges'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status = 'utkast' AND NEW.status = 'publisert' THEN
        IF NEW.publisert_ts > pg_catalog.now() THEN
            RAISE EXCEPTION 'malversjon: publiseringen skjer nå, aldri'
                ' frem i tid' USING ERRCODE = 'insufficient_privilege';
        END IF;
    ELSIF OLD.status = 'publisert' AND NEW.status = 'tilbaketrukket' THEN
        IF NEW.publisert_ts IS DISTINCT FROM OLD.publisert_ts
           OR NEW.publisert_av IS DISTINCT FROM OLD.publisert_av THEN
            RAISE EXCEPTION 'malversjon: tilbaketrekkingen skriver ikke'
                ' om publiseringen' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.tilbaketrukket_ts > pg_catalog.now() THEN
            RAISE EXCEPTION 'malversjon: tilbaketrekkingen skjer nå,'
                ' aldri frem i tid'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    ELSE
        -- Inkluderer status=status (en «oppdatering» som ikke er en
        -- overgang) og alt ut av `tilbaketrukket`: skjulingen er
        -- enveis, som i 079.
        RAISE EXCEPTION 'malversjon: % → % er ikke en lovlig overgang'
            ' (utkast→publisert og publisert→tilbaketrukket er de to)',
            OLD.status, NEW.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m5_versjon_vakt() FROM PUBLIC;
CREATE TRIGGER m5_versjon_vakt
    BEFORE UPDATE OR DELETE ON malversjon
    FOR EACH ROW EXECUTE FUNCTION m5_versjon_vakt();
CREATE TRIGGER m5_versjon_ingen_truncate
    BEFORE TRUNCATE ON malversjon
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

ALTER TABLE malversjon ENABLE ROW LEVEL SECURITY;
ALTER TABLE malversjon FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON malversjon
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- KOLONNEGRANT, IKKE TABELLGRANT (husets regel: rettigheten skal
-- være så smal som jobben). Eieren av dørene kan skrive
-- LIVSSYKLUSKOLONNENE og ingenting annet — `versjonsnr`, `familie_id`
-- og `innhold_hash` er utenfor grantet, ikke bare utenfor vakten.
-- «En publisert mal endres aldri» blir dermed håndhevet TO ganger, av
-- to uavhengige mekanismer: at vakten avviser det, OG at rollen som
-- kunne prøvd ikke har rettigheten.
GRANT SELECT, INSERT ON malversjon TO disponit_mal_eier;
GRANT UPDATE (status, publisert_ts, publisert_av,
              tilbaketrukket_ts, tilbaketrukket_av)
    ON malversjon TO disponit_mal_eier;

-- Flaten lister versjonene per familie, nyeste først.
CREATE INDEX malversjon_familie_nr
    ON malversjon (tenant, familie_id, versjonsnr DESC);

-- ============================================================
-- 3. malkomponent — malens innhold, i rekkefølge.
--
--    CHECK-EN GJØR FORMEN TOTAL, og den er hele grunnen til at «låst
--    klausul kan ikke overstyres av utfyllingen» ikke trenger en
--    kontroll i utfyllingen:
--       'felt'    → feltnokkel PÅKREVD, innhold NULL,  laast = false
--       'tekst'   → innhold PÅKREVD,    feltnokkel NULL, laast = false
--       'klausul' → innhold PÅKREVD,    feltnokkel NULL, laast fritt
--    En låst komponent har altså ingen `feltnokkel`, og utfyllingen
--    leser KUN feltnøkler. Det finnes ingen inngang å overstyre den
--    gjennom.
--
--    APPEND-ONLY TOTALT: verken UPDATE eller DELETE, for noen rolle.
--    INSERT er lovlig bare mens versjonen er `utkast` — etter
--    publisering er versjonen bytene den var.
-- ============================================================
CREATE TABLE malkomponent (
    tenant TEXT NOT NULL,
    komponent_id UUID NOT NULL,
    versjon_id UUID NOT NULL,
    rekkefolge INT NOT NULL CHECK (rekkefolge >= 1),
    komponenttype TEXT NOT NULL
        CHECK (komponenttype IN ('tekst', 'felt', 'klausul')),
    innhold TEXT CHECK (innhold IS NULL
                        OR length(innhold) BETWEEN 1 AND 8000),
    -- Feltnøkkelen er et MASKINNAVN, ikke en overskrift: den bindes mot
    -- `malfelt` og mot nøklene i utfyllingens JSONB. Mønsteret holder
    -- den fri for mellomrom og store bokstaver, så «samme felt» aldri
    -- blir to nøkler som ser like ut for et menneske.
    feltnokkel TEXT CHECK (feltnokkel IS NULL
                           OR feltnokkel ~ '^[a-z][a-z0-9_.]{0,62}$'),
    laast BOOLEAN NOT NULL DEFAULT false,
    CONSTRAINT malkomponent_pk PRIMARY KEY (tenant, komponent_id),
    CONSTRAINT malkomponent_versjon_fk FOREIGN KEY (tenant, versjon_id)
        REFERENCES malversjon (tenant, versjon_id),
    CONSTRAINT malkomponent_rekkefolge_unik
        UNIQUE (tenant, versjon_id, rekkefolge),
    CONSTRAINT malkomponent_form_total CHECK (
        (komponenttype = 'felt'
            AND feltnokkel IS NOT NULL AND innhold IS NULL
            AND laast = false)
     OR (komponenttype = 'tekst'
            AND innhold IS NOT NULL AND feltnokkel IS NULL
            AND laast = false)
     OR (komponenttype = 'klausul'
            AND innhold IS NOT NULL AND feltnokkel IS NULL))
);

-- ============================================================
-- 4. malfelt — de obligatoriske feltene, deklarert PER VERSJON.
--    Deklarasjonen hører versjonen til og ikke familien: to versjoner
--    av samme avtale kan kreve ulike opplysninger, og et felt som
--    forsvant i v3 skal ikke gjøre v2 uleselig i ettertid.
-- ============================================================
CREATE TABLE malfelt (
    tenant TEXT NOT NULL,
    versjon_id UUID NOT NULL,
    feltnokkel TEXT NOT NULL
        CHECK (feltnokkel ~ '^[a-z][a-z0-9_.]{0,62}$'),
    paakrevd BOOLEAN NOT NULL,
    felttype TEXT NOT NULL
        CHECK (felttype IN ('tekst', 'tall', 'dato', 'belop')),
    -- NOT NULL med vilje: et felt et menneske skal fylle ut uten et ord
    -- om hva det er, blir fylt ut feil. Beskrivelsen er flatens etikett.
    beskrivelse TEXT NOT NULL
        CHECK (length(btrim(beskrivelse)) BETWEEN 1 AND 500),
    CONSTRAINT malfelt_pk PRIMARY KEY (tenant, versjon_id, feltnokkel),
    CONSTRAINT malfelt_versjon_fk FOREIGN KEY (tenant, versjon_id)
        REFERENCES malversjon (tenant, versjon_id)
);

-- Én vakt for begge innholdstabellene: samme regel, samme setning.
-- UPDATE og DELETE avvises for ENHVER rolle (011/031-doktrinen —
-- 079s «redigering er en ny versjon», uten unntaket for skjuling, som
-- her bor på versjonen og ikke på innholdet). INSERT er lovlig bare
-- mens versjonen er `utkast`.
CREATE FUNCTION m5_innhold_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION '%: % avvist — malinnhold er append-only'
            ' (redigering = ny versjon)', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT v.status INTO v_status FROM public.malversjon v
     WHERE v.tenant = NEW.tenant AND v.versjon_id = NEW.versjon_id;
    IF NOT FOUND THEN
        -- FK-en fanger dette også, men da som en fremmednøkkelfeil på
        -- en rad vakten allerede har sett: å si det her gir kalleren
        -- setningen om hva som mangler.
        RAISE EXCEPTION '%: ukjent malversjon', TG_TABLE_NAME
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_status <> 'utkast' THEN
        RAISE EXCEPTION '%: versjonen er %, ikke utkast — en publisert'
            ' mal får aldri nytt innhold, den etterfølges',
            TG_TABLE_NAME, v_status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m5_innhold_vakt() FROM PUBLIC;

CREATE TRIGGER m5_komponent_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON malkomponent
    FOR EACH ROW EXECUTE FUNCTION m5_innhold_vakt();
CREATE TRIGGER m5_komponent_ingen_truncate
    BEFORE TRUNCATE ON malkomponent
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();
CREATE TRIGGER m5_felt_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON malfelt
    FOR EACH ROW EXECUTE FUNCTION m5_innhold_vakt();
CREATE TRIGGER m5_felt_ingen_truncate
    BEFORE TRUNCATE ON malfelt
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

ALTER TABLE malkomponent ENABLE ROW LEVEL SECURITY;
ALTER TABLE malkomponent FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON malkomponent
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE malfelt ENABLE ROW LEVEL SECURITY;
ALTER TABLE malfelt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON malfelt
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- Ingen UPDATE, ingen DELETE — heller ikke for eieren av dørene.
GRANT SELECT, INSERT ON malkomponent TO disponit_mal_eier;
GRANT SELECT, INSERT ON malfelt TO disponit_mal_eier;

-- ============================================================
-- 5. Dørene — eid av `disponit_mal_eier` (056/057/089-formen:
--    SECURITY DEFINER bak `krev_tenantkontekst`, som claimeren eier og
--    PUBLIC mistet i 038). Runtime-EXECUTE speiles i migrer.py, aldri
--    her (057-lærdommen: migrasjonen navngir ikke runtime-rollen).
-- ============================================================

-- Kontekstporten eies av claimeren; grantet til maleieren gis derfor
-- som claimeren (039/074-formen).
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_mal_eier;
RESET ROLE;

SET LOCAL ROLE disponit_mal_eier;

-- ------------------------------------------------------------
-- 5.1 Familien. SP-2 på `p_familie_id` (056-materialitetsformen):
--     gjenspill med identisk innhold er et stille ja, samme id med
--     annet innhold en materiell konflikt, NULL = fersk id.
-- ------------------------------------------------------------
CREATE FUNCTION m5_opprett_malfamilie(
    p_tenant TEXT, p_navn TEXT, p_beskrivelse TEXT, p_aktor TEXT,
    p_familie_id UUID DEFAULT NULL)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID; v_hash TEXT; v_lagret TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm5_opprett_malfamilie');
    v_hash := md5(coalesce(btrim(p_navn), '') || E'\x1f'
                  || coalesce(btrim(p_beskrivelse), ''));
    IF p_familie_id IS NOT NULL THEN
        SELECT f.innhold_hash INTO v_lagret FROM public.malfamilie f
         WHERE f.tenant = p_tenant AND f.familie_id = p_familie_id;
        IF FOUND THEN
            IF v_lagret IS DISTINCT FROM v_hash THEN
                RAISE EXCEPTION 'malfamilie: id-en er brukt for ANNET'
                    ' innhold' USING ERRCODE = 'unique_violation';
            END IF;
            RETURN p_familie_id;
        END IF;
    END IF;
    v_id := coalesce(p_familie_id, gen_random_uuid());
    INSERT INTO public.malfamilie
        (tenant, familie_id, navn, beskrivelse, opprettet_av, innhold_hash)
    VALUES (p_tenant, v_id, btrim(p_navn),
            nullif(btrim(coalesce(p_beskrivelse, '')), ''),
            p_aktor, v_hash);
    RETURN v_id;
END $$;
REVOKE ALL ON FUNCTION m5_opprett_malfamilie(TEXT, TEXT, TEXT, TEXT, UUID)
    FROM PUBLIC;

-- ------------------------------------------------------------
-- 5.2 Versjonen — HELE utkastet i ett kall (091-formen).
--
--     `p_komponenter` er et JSON-array; ARRAYETS REKKEFØLGE ER
--     `rekkefolge`, så en kaller kan ikke levere to komponenter med
--     samme plass eller hoppe over en. `p_felt` er deklarasjonene.
--
--     Valideringen er CHECK-ENES, ikke en IF-stige her: dørene skriver
--     rått inn og lar basen felle dommen. En 'felt'-komponent med
--     innhold, en 'tekst' med feltnøkkel, en 'tekst' med laast=true —
--     alle tre blir `check_violation` fra `malkomponent_form_total`,
--     og de blir det uansett hvilken skrivevei som en gang kommer til.
-- ------------------------------------------------------------
CREATE FUNCTION m5_opprett_malversjon(
    p_tenant TEXT, p_familie_id UUID, p_komponenter JSONB, p_felt JSONB,
    p_aktor TEXT, p_versjon_id UUID DEFAULT NULL)
RETURNS TABLE (ut_versjon_id UUID, ut_versjonsnr INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID; v_nr INT; v_hash TEXT; v_lagret TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm5_opprett_malversjon');
    -- 079-formens isolasjonsport, ordrett: gjenspill-løftet under er
    -- utledet av en LESNING, og under REPEATABLE READ ville lesningen
    -- vært av et snapshot som ikke ser den samtidige skrivingen.
    IF current_setting('transaction_isolation')
       NOT IN ('read committed', 'read uncommitted') THEN
        RAISE EXCEPTION 'm5_opprett_malversjon: krever READ COMMITTED'
            ' (fikk %) — gjenspill-løftet er utledet av en LESNING',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    IF jsonb_typeof(p_komponenter) <> 'array'
       OR jsonb_array_length(p_komponenter) = 0 THEN
        RAISE EXCEPTION 'm5_opprett_malversjon: p_komponenter må være et'
            ' ikke-tomt array — en mal uten innhold er ikke en mal'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF jsonb_typeof(p_felt) <> 'array' THEN
        RAISE EXCEPTION 'm5_opprett_malversjon: p_felt må være et array'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_hash := md5(p_familie_id::text || E'\x1f' || p_komponenter::text
                  || E'\x1f' || p_felt::text);
    IF p_versjon_id IS NOT NULL THEN
        SELECT v.versjonsnr, v.innhold_hash INTO v_nr, v_lagret
          FROM public.malversjon v
         WHERE v.tenant = p_tenant AND v.versjon_id = p_versjon_id;
        IF FOUND THEN
            IF v_lagret IS DISTINCT FROM v_hash THEN
                RAISE EXCEPTION 'malversjon: id-en er brukt for ANNET'
                    ' innhold' USING ERRCODE = 'unique_violation';
            END IF;
            ut_versjon_id := p_versjon_id; ut_versjonsnr := v_nr;
            RETURN NEXT;
            RETURN;
        END IF;
    END IF;
    PERFORM 1 FROM public.malfamilie f
     WHERE f.tenant = p_tenant AND f.familie_id = p_familie_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'malversjon: ukjent malfamilie'
            USING ERRCODE = 'no_data_found';
    END IF;
    -- Versjonstildelingen serialiseres per familie (079-formen): to
    -- samtidige utkast skal få 4 og 5, aldri to ganger 4 og en
    -- unik-kollisjon kalleren må tolke.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'malversjon:' || p_tenant || ':' || p_familie_id::text, 0));
    SELECT coalesce(max(v.versjonsnr), 0) + 1 INTO v_nr
      FROM public.malversjon v
     WHERE v.tenant = p_tenant AND v.familie_id = p_familie_id;
    v_id := coalesce(p_versjon_id, gen_random_uuid());
    INSERT INTO public.malversjon
        (tenant, versjon_id, familie_id, versjonsnr, status,
         opprettet_av, innhold_hash)
    VALUES (p_tenant, v_id, p_familie_id, v_nr, 'utkast', p_aktor, v_hash);

    INSERT INTO public.malkomponent
        (tenant, komponent_id, versjon_id, rekkefolge, komponenttype,
         innhold, feltnokkel, laast)
    SELECT p_tenant, gen_random_uuid(), v_id, e.ord::INT,
           e.v ->> 'komponenttype',
           CASE WHEN jsonb_typeof(e.v -> 'innhold') = 'string'
                THEN e.v ->> 'innhold' END,
           CASE WHEN jsonb_typeof(e.v -> 'feltnokkel') = 'string'
                THEN e.v ->> 'feltnokkel' END,
           -- INGEN `ELSE false`. En fraværende `laast` er false (det
           -- er standardtilstanden), men en TILSTEDEVÆRENDE verdi som
           -- ikke er en boolean gir NULL — og NOT NULL avviser den.
           -- Alternativet ville vært å stille låse OPP en klausul
           -- forfatteren mente å låse, fordi hun skrev `"laast": "ja"`.
           -- Samme strenge form som `paakrevd` under.
           CASE WHEN jsonb_typeof(e.v -> 'laast') = 'boolean'
                THEN (e.v ->> 'laast')::BOOLEAN
                WHEN e.v -> 'laast' IS NULL THEN false END
      FROM jsonb_array_elements(p_komponenter) WITH ORDINALITY AS e(v, ord);

    -- `paakrevd` har INGEN default: et felt der kalleren glemte å si om
    -- det må fylles ut, blir en NOT NULL-feil og ikke stilltiende
    -- valgfritt. Den halvferdige deklarasjonen er den som gjør at et
    -- hull aldri blir rapportert.
    INSERT INTO public.malfelt
        (tenant, versjon_id, feltnokkel, paakrevd, felttype, beskrivelse)
    SELECT p_tenant, v_id, e.v ->> 'feltnokkel',
           CASE WHEN jsonb_typeof(e.v -> 'paakrevd') = 'boolean'
                THEN (e.v ->> 'paakrevd')::BOOLEAN END,
           e.v ->> 'felttype', e.v ->> 'beskrivelse'
      FROM jsonb_array_elements(p_felt) AS e(v);

    ut_versjon_id := v_id; ut_versjonsnr := v_nr;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m5_opprett_malversjon(
    TEXT, UUID, JSONB, JSONB, TEXT, UUID) FROM PUBLIC;

-- ------------------------------------------------------------
-- 5.3 Publiseringen. DEN BÆRENDE KONTROLLEN: deklarasjonen og
--     innholdet må være det SAMME settet, begge veier.
--
--       * en komponent som refererer et UDEKLARERT felt → avvist.
--         Uten den kunne en mal publiseres med et hull utfyllingen
--         aldri får vite om at den skal rapportere.
--       * et deklarert felt INGEN komponent bruker → også avvist.
--         Et `paakrevd`-felt uten plass i teksten ville blitt
--         rapportert manglende for alltid, uten at noe kunne fylle
--         det — en mal ingen kan gjøre ferdig.
--
--     Kontrollen bor HER og ikke i utfyllingen fordi den skal felles
--     ÉN gang, ved publiseringen, og ikke ved hver lesning.
-- ------------------------------------------------------------
CREATE FUNCTION m5_publiser_malversjon(
    p_tenant TEXT, p_versjon_id UUID, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_nr INT; v_nokkel TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm5_publiser_malversjon');
    SELECT v.status, v.versjonsnr INTO v_status, v_nr
      FROM public.malversjon v
     WHERE v.tenant = p_tenant AND v.versjon_id = p_versjon_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'malversjon: ukjent versjon'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_status <> 'utkast' THEN
        RAISE EXCEPTION 'malversjon: versjonen er %, ikke utkast —'
            ' publisering er en engangsovergang', v_status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    SELECT k.feltnokkel INTO v_nokkel FROM public.malkomponent k
     WHERE k.tenant = p_tenant AND k.versjon_id = p_versjon_id
       AND k.komponenttype = 'felt'
       AND NOT EXISTS (SELECT 1 FROM public.malfelt f
                        WHERE f.tenant = k.tenant
                          AND f.versjon_id = k.versjon_id
                          AND f.feltnokkel = k.feltnokkel)
     ORDER BY k.rekkefolge LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'malversjon: komponenten refererer feltet «%»'
            ' som ikke er deklarert i malfelt', v_nokkel
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    SELECT f.feltnokkel INTO v_nokkel FROM public.malfelt f
     WHERE f.tenant = p_tenant AND f.versjon_id = p_versjon_id
       AND NOT EXISTS (SELECT 1 FROM public.malkomponent k
                        WHERE k.tenant = f.tenant
                          AND k.versjon_id = f.versjon_id
                          AND k.komponenttype = 'felt'
                          AND k.feltnokkel = f.feltnokkel)
     ORDER BY f.feltnokkel LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'malversjon: feltet «%» er deklarert, men ingen'
            ' komponent bruker det', v_nokkel
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    UPDATE public.malversjon
       SET status = 'publisert', publisert_ts = pg_catalog.now(),
           publisert_av = p_aktor
     WHERE tenant = p_tenant AND versjon_id = p_versjon_id;
    RETURN v_nr;
END $$;
REVOKE ALL ON FUNCTION m5_publiser_malversjon(TEXT, UUID, TEXT)
    FROM PUBLIC;

-- ------------------------------------------------------------
-- 5.4 Tilbaketrekkingen — 079s enveis skjuling. Innholdet består;
--     versjonen slutter bare å være i kraft, og `m5_fyll_mal`
--     nekter å fylle den ut.
-- ------------------------------------------------------------
CREATE FUNCTION m5_trekk_tilbake_malversjon(
    p_tenant TEXT, p_versjon_id UUID, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_nr INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm5_trekk_tilbake_malversjon');
    SELECT v.status, v.versjonsnr INTO v_status, v_nr
      FROM public.malversjon v
     WHERE v.tenant = p_tenant AND v.versjon_id = p_versjon_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'malversjon: ukjent versjon'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_status <> 'publisert' THEN
        RAISE EXCEPTION 'malversjon: versjonen er %, ikke publisert —'
            ' bare det som er i kraft kan trekkes tilbake', v_status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    UPDATE public.malversjon
       SET status = 'tilbaketrukket',
           tilbaketrukket_ts = pg_catalog.now(),
           tilbaketrukket_av = p_aktor
     WHERE tenant = p_tenant AND versjon_id = p_versjon_id;
    RETURN v_nr;
END $$;
REVOKE ALL ON FUNCTION m5_trekk_tilbake_malversjon(TEXT, UUID, TEXT)
    FROM PUBLIC;

-- ------------------------------------------------------------
-- 5.5 UTFYLLINGEN — modulens eksistensberettigelse.
--
--     RETURNERER EN KOMPONENTLISTE, IKKE EN TEKST. Én rad per
--     komponent, i rekkefølge, slik at flaten kan MARKERE hullene i
--     stedet for å gjemme dem i en streng. `tekst IS NULL` sammen med
--     `dekket = false` er det ENESTE en manglende verdi noen gang blir
--     — aldri tom streng, aldri feltnøkkelen, aldri en plassholder som
--     ser ut som innhold.
--
--     STABLE er ikke en optimalisering. En ikke-volatil funksjon KAN
--     IKKE skrive: PostgreSQL avviser INSERT/UPDATE/DELETE i kroppen.
--     `utfylling_skrev_dokument` er derfor en egenskap ved funksjonen,
--     ikke en påstand om hva den gjør i dag.
--
--     Tre ting den nekter, og hvorfor:
--       * en versjon som ikke er `publisert` — et utkast er ikke i
--         kraft, en tilbaketrukket versjon er ikke lenger i kraft.
--       * en verdi som ikke er en JSON-streng eller et tall — en
--         `{}` eller en `true` på et tekstfelt er en kallerfeil, og
--         den skal si fra, ikke bli til «[object Object]».
--       * en NØKKEL malen ikke deklarerer — en skrivefeil i en
--         nøkkel ville ellers blitt til et STILLE manglende felt: to
--         feil som kansellerer hverandre i rapporten og ikke i
--         dokumentet.
-- ------------------------------------------------------------
CREATE FUNCTION m5_fyll_mal(
    p_tenant TEXT, p_versjon_id UUID, p_verdier JSONB)
RETURNS TABLE (rekkefolge INT, komponenttype TEXT, feltnokkel TEXT,
               laast BOOLEAN, paakrevd BOOLEAN, dekket BOOLEAN,
               tekst TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_nokkel TEXT; v_verdi JSONB; v_raa TEXT; k RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm5_fyll_mal');
    IF p_verdier IS NULL OR jsonb_typeof(p_verdier) <> 'object' THEN
        RAISE EXCEPTION 'm5_fyll_mal: p_verdier må være et JSON-objekt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT v.status INTO v_status FROM public.malversjon v
     WHERE v.tenant = p_tenant AND v.versjon_id = p_versjon_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'malversjon: ukjent versjon'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_status <> 'publisert' THEN
        RAISE EXCEPTION 'malversjon: versjonen er %, ikke publisert —'
            ' bare en mal som er i kraft fylles ut', v_status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    FOR v_nokkel IN SELECT jsonb_object_keys(p_verdier) LOOP
        PERFORM 1 FROM public.malfelt f
         WHERE f.tenant = p_tenant AND f.versjon_id = p_versjon_id
           AND f.feltnokkel = v_nokkel;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'm5_fyll_mal: «%» er ikke et felt i denne'
                ' malversjonen', v_nokkel
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
    END LOOP;

    FOR k IN
        SELECT c.rekkefolge, c.komponenttype, c.innhold, c.feltnokkel,
               c.laast, f.paakrevd
          FROM public.malkomponent c
          LEFT JOIN public.malfelt f
                 ON f.tenant = c.tenant AND f.versjon_id = c.versjon_id
                AND f.feltnokkel = c.feltnokkel
         WHERE c.tenant = p_tenant AND c.versjon_id = p_versjon_id
         ORDER BY c.rekkefolge
    LOOP
        rekkefolge := k.rekkefolge;
        komponenttype := k.komponenttype;
        feltnokkel := k.feltnokkel;
        laast := k.laast;
        IF k.komponenttype <> 'felt' THEN
            -- Fast tekst og klausuler bæres URØRT ut. En låst klausul
            -- passerer her uten at utfyllingen har hatt noen mulighet
            -- til å røre den: den har ingen feltnøkkel å treffes på.
            paakrevd := false;
            dekket := true;
            tekst := k.innhold;
            RETURN NEXT;
            CONTINUE;
        END IF;
        paakrevd := coalesce(k.paakrevd, true);
        v_verdi := p_verdier -> k.feltnokkel;
        IF v_verdi IS NULL OR jsonb_typeof(v_verdi) = 'null' THEN
            dekket := false;
            tekst := NULL;
        ELSIF jsonb_typeof(v_verdi) IN ('string', 'number') THEN
            v_raa := p_verdier ->> k.feltnokkel;
            IF btrim(v_raa) = '' THEN
                -- TOM STRENG ER IKKE EN VERDI. Det er nøyaktig den
                -- naive implementasjonen invarianten finnes for: et
                -- felt «fylt ut» med ingenting ser i dokumentet ut som
                -- et felt som er besvart.
                dekket := false;
                tekst := NULL;
            ELSE
                dekket := true;
                tekst := v_raa;
            END IF;
        ELSE
            RAISE EXCEPTION 'm5_fyll_mal: verdien for «%» er %, og bare'
                ' streng og tall kan bli tekst i et dokument',
                k.feltnokkel, jsonb_typeof(v_verdi)
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN NEXT;
    END LOOP;
END $$;
REVOKE ALL ON FUNCTION m5_fyll_mal(TEXT, UUID, JSONB) FROM PUBLIC;

RESET ROLE;
