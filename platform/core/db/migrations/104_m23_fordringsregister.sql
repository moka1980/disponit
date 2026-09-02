-- 104: M-23 kundefordringsagent v1 — FORDRINGSREGISTERET.
-- Fem tenant-skopede tabeller, tolv dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- V1-AVGRENSNINGEN, ORDRETT FRA MANIFESTET: katalogteksten lover at
-- agenten FORESLÅR en nedbetalingsplan innenfor eierens forhåndsgodkjente
-- grenser når kunden oppgir betalingsproblemer. v1 FORESLÅR INGENTING
-- OVERFOR KUNDEN og SENDER INGENTING.
--
-- DOMMEN, OG DEN ER DEN STRENGESTE I KLYNGEN: dette er PENGER OG EN
-- KUNDE. En purring sendt for tidlig, til feil kunde, eller på en
-- fordring som alt er betalt, er en skade som ikke kan trekkes tilbake —
-- den har forlatt systemet i det øyeblikket den ble sendt. Det finnes
-- derfor ingen SMTP-vei i denne migrasjonen, ingen utgående kø, ingen
-- mottakeradresse, og ingen status som heter `sendt` eller `purret`.
-- Trinnet SETTES av et menneske, og registeret viser hva som står for
-- tur.
--
-- «INNENFOR EIERENS FORHÅNDSGODKJENTE GRENSER» forutsetter dessuten at
-- de grensene finnes. Det gjør de ikke ennå — og å bygge tilbudsveien
-- før grensen er å la modulen definere sin egen fullmakt.
--
-- DOMMENE v1 HVILER PÅ, håndhevet i DATAMODELLEN:
--
--   1. BELØP ER HELTALL I MINSTE ENHET (øre), `BIGINT`, uten unntak.
--      Et flyttall i en aldersfordeling viser seg først når summene ikke
--      går opp. Ingen kolonne her er `NUMERIC`, `REAL` eller
--      `DOUBLE PRECISION`, og porten måler katalogen.
--
--   2. PURRETRINNENE ER TENANTENS EGNE, ikke en konstant i koden. De
--      ligger i `purretrinn`, skrives gjennom en dør, og versjoneres.
--      Et trinn kodet inn ville vært en fullmakt modulen ga seg selv:
--      «etter 14 døgn purrer vi» er en forretningsbeslutning, ikke en
--      teknisk detalj.
--
--      ÆRLIG OM HVA DETTE IKKE ER: det går IKKE gjennom M-1s
--      policymotor. M-1 er dokumentbasert (utkast → attestering →
--      aktivering) og har ingen fasilitet for en tenant-innstilling; å
--      presse purreplanen inn i den formen ville vært å oppfinne et
--      skjema for hele huset inne i en modul-PR. Invarianten
--      `purretrinn_hardkodet` er derfor oppfylt i den forstand som
--      betyr noe — tenanten eier og fører verdiene, og de er
--      revisjonssporet — men koblingen til M-1 står igjen som et
--      NAVNGITT gap, ikke som en påstand om at den finnes.
--
--   3. ET TRINN GÅR ÉN VEI OG ETT HAKK OM GANGEN. Et hopp fra trinn 1
--      til trinn 3 er en eskalering ingen besluttet — og for en kunde er
--      forskjellen mellom en påminnelse og et inkassovarsel hele saken.
--
--   4. EN BETALT FORDRING ESKALERER ALDRI. Trinnet kan ikke flyttes på
--      en fordring som er gjort opp, og betalinger er append-only: en
--      innbetaling som kunne slettes ville gjort «hvor mye skylder de»
--      til noe man kan skrive om.
--
-- GRENSEN MOT M-13 (101), sagt eksplisitt: M-13 eier BANKPOSTER — det
-- som har skjedd på konto. M-23 eier FORDRINGER — det kunden skylder. En
-- innbetaling er begge deler sett fra hver sin side, og de kobles IKKE
-- automatisk her: `m23_registrer_betaling` tar imot beløpet et menneske
-- har sett, og en ubetalt fordring med en umatchet innbetaling i samme
-- størrelsesorden er noe M-13s flate viser — aldri en lukking basen
-- feller selv.
--
-- SVEIPEN HAR SIN EGEN ROLLE OG SIN EGEN TIMER (095/100/101/102/103):
-- `disponit_fordringssveip` har nøyaktig ÉN rettighet — EXECUTE på
-- `m23_sveip_fordringer` — og INGEN tabellrettigheter. Sveipen PURRER
-- INGEN og flytter INGEN trinn; den skriver FUNN om hvilke fordringer
-- som har passert sitt neste trinn.
--
-- FORMENE ER HUSETS: tabellene eies av migrator, dørene av NOLOGIN-rollen
-- `disponit_fordring_eier`, tenant TEXT + RLS ENABLE+FORCE +
-- `tenant_isolasjon`, SP-1 i hver tenantbundet definer, ingen BYPASSRLS.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_fordring_eier') THEN
        RAISE EXCEPTION 'rollen disponit_fordring_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_fordring_eier;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `purreplan` — ÉN per tenant. Ikke en katalog av planer: en tenant har
-- én måte å purre på, og to samtidige planer ville gjort «hvilket trinn
-- står denne fordringen på» til et spørsmål med to svar.
CREATE TABLE purreplan (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- VERSJONEN ØKER VED HVER ENDRING. En fordring bærer versjonen den
    -- ble vurdert mot, så en endret plan ikke omskriver historien.
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon >= 1),
    oppdatert TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL,
    CONSTRAINT purreplan_pk PRIMARY KEY (tenant)
);

-- `purretrinn` — trinnene i planen, i rekkefølge.
--
-- DOM 2 I TABELLFORM: `dogn_etter_forfall` er TENANTENS tall, ikke
-- modulens. «Etter 14 døgn purrer vi» er en forretningsbeslutning.
CREATE TABLE purretrinn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    trinn_nr INT NOT NULL CHECK (trinn_nr >= 1),
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    -- Døgn ETTER forfall før dette trinnet er aktuelt. Trinn 1 kan ha 0
    -- (samme dag), og tallene må stige — vakten i §2 håndhever det.
    dogn_etter_forfall INT NOT NULL CHECK (dogn_etter_forfall >= 0),
    -- LUKKET SETT. Forskjellen mellom en påminnelse og et inkassovarsel
    -- er hele saken for kunden, og et åpent sett ville gjort
    -- eskaleringen umulig å lese.
    handling TEXT NOT NULL CHECK (handling IN (
        'paaminnelse', 'purring', 'inkassovarsel', 'inkasso')),
    -- Gebyr i øre. Null er lovlig — en påminnelse koster typisk ingenting.
    gebyr_ore BIGINT NOT NULL DEFAULT 0 CHECK (gebyr_ore >= 0),
    CONSTRAINT purretrinn_pk PRIMARY KEY (tenant, trinn_nr),
    CONSTRAINT purretrinn_plan_fk FOREIGN KEY (tenant)
        REFERENCES purreplan (tenant)
);

-- `fordring` — det kunden skylder.
CREATE TABLE fordring (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    fordring_id UUID NOT NULL,
    kunde_ref TEXT NOT NULL CHECK (kunde_ref ~ '[^[:space:]]'),
    fakturanummer TEXT NOT NULL CHECK (fakturanummer ~ '[^[:space:]]'),
    -- ALLTID POSITIVT. En negativ fordring er en kreditnota, og det er
    -- noe annet enn en fordring med et minustegn.
    belop_ore BIGINT NOT NULL CHECK (belop_ore > 0),
    -- Summen av registrerte innbetalinger. Vedlikeholdt av døren i SAMME
    -- transaksjon som hendelsen, og ETTERPRØVD AV VAKTEN mot
    -- `fordringshendelse` — en denormalisering ingen kontrollerer er en
    -- som driver.
    betalt_ore BIGINT NOT NULL DEFAULT 0 CHECK (betalt_ore >= 0),
    utstedt DATE NOT NULL,
    forfall DATE NOT NULL,
    -- TRINNET er hvor langt eskaleringen har kommet. 0 = ingen purring
    -- er sendt. Tallet peker inn i `purretrinn`, men er IKKE en
    -- fremmednøkkel: planen kan endres, og en fordring som pekte på et
    -- trinn som forsvant ville blitt uleselig. `purreplan_versjon`
    -- bærer hvilken plan den ble vurdert mot.
    trinn INT NOT NULL DEFAULT 0 CHECK (trinn >= 0),
    purreplan_versjon INT,
    status TEXT NOT NULL DEFAULT 'apen'
        CHECK (status IN ('apen', 'betalt', 'ettergitt')),
    avsluttet_ts TIMESTAMPTZ,
    avsluttet_av TEXT,
    ettergitt_begrunnelse TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    CONSTRAINT fordring_pk PRIMARY KEY (tenant, fordring_id),
    -- Forfall før utstedelse er ikke en frist, det er en skrivefeil.
    CONSTRAINT fordring_forfall_etter_utstedt CHECK (forfall >= utstedt),
    -- OVERBETALING ER IKKE EN FORDRING. Er det betalt mer enn skyldig,
    -- er differansen en tilgodehavende — et annet register.
    CONSTRAINT fordring_ikke_overbetalt CHECK (betalt_ore <= belop_ore),
    -- Avslutningens felter står eller faller sammen, og en ETTERGITT
    -- fordring koster en begrunnelse: å slette et krav uten å si hvorfor
    -- er den ene handlingen ingen kan etterprøve senere.
    CONSTRAINT fordring_avslutning_helhet
        CHECK ((status = 'apen' AND avsluttet_ts IS NULL
                AND avsluttet_av IS NULL
                AND ettergitt_begrunnelse IS NULL)
               OR (status = 'betalt' AND avsluttet_ts IS NOT NULL
                   AND avsluttet_av IS NOT NULL
                   AND ettergitt_begrunnelse IS NULL)
               OR (status = 'ettergitt' AND avsluttet_ts IS NOT NULL
                   AND avsluttet_av IS NOT NULL
                   AND ettergitt_begrunnelse IS NOT NULL
                   AND ettergitt_begrunnelse ~ '[^[:space:]]'))
);
CREATE UNIQUE INDEX fordring_faktura_unik
    ON fordring (tenant, fakturanummer);
CREATE INDEX fordring_apne ON fordring (tenant, forfall)
    WHERE status = 'apen';

-- `fordringshendelse` — APPEND-ONLY. Det som har skjedd med fordringen:
-- innbetalinger og trinnflyttinger.
--
-- DOM 4 I TABELLFORM: en innbetaling som kunne slettes ville gjort «hvor
-- mye skylder de» til noe man kan skrive om.
CREATE TABLE fordringshendelse (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    hendelse_id UUID NOT NULL,
    fordring_id UUID NOT NULL,
    art TEXT NOT NULL CHECK (art IN ('betaling', 'trinn', 'ettergitt')),
    -- Beløp for `betaling`, NULL ellers. Positivt: en negativ
    -- innbetaling er en tilbakebetaling, og den hører ikke hjemme her.
    belop_ore BIGINT CHECK (belop_ore IS NULL OR belop_ore > 0),
    -- Trinnet for `trinn`, NULL ellers.
    trinn INT CHECK (trinn IS NULL OR trinn >= 1),
    begrunnelse TEXT,
    inntruffet DATE NOT NULL,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    CONSTRAINT fordringshendelse_pk PRIMARY KEY (tenant, hendelse_id),
    CONSTRAINT fordringshendelse_fordring_fk
        FOREIGN KEY (tenant, fordring_id)
        REFERENCES fordring (tenant, fordring_id),
    -- HVER ART BÆRER SITT EGET FELT, og bare det. En `betaling` med et
    -- trinnummer ville vært to hendelser i én rad.
    CONSTRAINT fordringshendelse_felt_per_art CHECK (
        (art = 'betaling' AND belop_ore IS NOT NULL AND trinn IS NULL)
        OR (art = 'trinn' AND belop_ore IS NULL AND trinn IS NOT NULL)
        OR (art = 'ettergitt' AND belop_ore IS NULL AND trinn IS NULL
            AND begrunnelse IS NOT NULL AND begrunnelse ~ '[^[:space:]]'))
);
CREATE INDEX fordringshendelse_fordring
    ON fordringshendelse (tenant, fordring_id, inntruffet);

-- `fordringsfunn` — sveipens dom. Samme form som 100–103.
CREATE TABLE fordringsfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    fordring_id UUID NOT NULL,
    funntype TEXT NOT NULL CHECK (funntype IN (
        'trinn_forfalt',
        'ingen_purreplan',
        'forfalt_uten_trinn')),
    -- Hvilket trinn fordringen HAR NÅDD i planen, men ikke er satt på.
    moden_for_trinn INT,
    dogn_over_grense INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT fordringsfunn_pk
        PRIMARY KEY (tenant, fordring_id, funntype),
    CONSTRAINT fordringsfunn_fordring_fk FOREIGN KEY (tenant, fordring_id)
        REFERENCES fordring (tenant, fordring_id),
    CONSTRAINT fordringsfunn_lukking
        CHECK ((apen AND lukket_ts IS NULL)
               OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE INDEX fordringsfunn_apne ON fordringsfunn (tenant, funntype)
    WHERE apen;


-- ------------------------------------------------------------
-- 2. Radvaktene.
-- ------------------------------------------------------------

-- PLANEN KAN ENDRES, men trinnene må STIGE. En plan der trinn 2 kommer
-- før trinn 1 i tid er en eskalering som går bakover, og da betyr
-- «trinn» ingenting.
CREATE FUNCTION m23_purretrinn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_rad RECORD; v_forrige INT; v_neste INT;
BEGIN
    -- TRUNCATE HAR SIN EGEN ARM. Uten den faller TG_OP='TRUNCATE'
    -- gjennom til radlogikken under og feiler på «record "new" is not
    -- assigned yet» — riktig utfall, men en intern feil som ikke sier
    -- hva som ble nektet eller hvorfor.
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'purretrinn: TRUNCATE avvist — en purreplan'
            ' tømmes ved å sette en ny, ikke ved å fjerne trinnene under'
            ' føttene på sveipen'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_rad := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    IF TG_OP = 'DELETE' THEN
        RETURN v_rad;
    END IF;
    SELECT max(t.dogn_etter_forfall) INTO v_forrige
      FROM public.purretrinn t
     WHERE t.tenant = NEW.tenant AND t.trinn_nr < NEW.trinn_nr;
    IF v_forrige IS NOT NULL AND NEW.dogn_etter_forfall <= v_forrige THEN
        RAISE EXCEPTION 'purretrinn: trinn % kommer etter % døgn, men et'
            ' lavere trinn kommer etter % — eskaleringen går bakover',
            NEW.trinn_nr, NEW.dogn_etter_forfall, v_forrige
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT min(t.dogn_etter_forfall) INTO v_neste
      FROM public.purretrinn t
     WHERE t.tenant = NEW.tenant AND t.trinn_nr > NEW.trinn_nr;
    IF v_neste IS NOT NULL AND NEW.dogn_etter_forfall >= v_neste THEN
        RAISE EXCEPTION 'purretrinn: trinn % kommer etter % døgn, men et'
            ' høyere trinn kommer etter % — eskaleringen går bakover',
            NEW.trinn_nr, NEW.dogn_etter_forfall, v_neste
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m23_purretrinn_vakt() FROM PUBLIC;
CREATE TRIGGER m23_purretrinn_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON purretrinn
    FOR EACH ROW EXECUTE FUNCTION m23_purretrinn_vakt();
CREATE TRIGGER m23_purretrinn_ingen_truncate
    BEFORE TRUNCATE ON purretrinn
    EXECUTE FUNCTION m23_purretrinn_vakt();

-- FORDRINGENS VAKT bærer dom 3 og 4.
CREATE FUNCTION m23_fordring_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT; v_sum BIGINT;
BEGIN
    -- TRUNCATE HAR SIN EGEN ARM, av samme grunn som i purretrinnvakten.
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'fordring: TRUNCATE avvist — et krav ettergis med'
            ' begrunnelse, det forsvinner aldri i en tabelltømming'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'fordring: DELETE avvist — et krav ettergis med'
            ' begrunnelse, det slettes aldri som rad'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.fordring_id IS DISTINCT FROM OLD.fordring_id
       OR NEW.fakturanummer IS DISTINCT FROM OLD.fakturanummer
       OR NEW.belop_ore IS DISTINCT FROM OLD.belop_ore
       OR NEW.utstedt IS DISTINCT FROM OLD.utstedt
       OR NEW.forfall IS DISTINCT FROM OLD.forfall
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'fordring: identiteten og beløpet er frosset — et'
            ' annet beløp er et annet krav, og en flyttet forfallsdato er'
            ' en ny avtale' USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- DOM 4, første halvdel: EN AVSLUTTET FORDRING ESKALERER ALDRI.
    IF OLD.status <> 'apen' THEN
        IF NEW.status IS DISTINCT FROM OLD.status THEN
            RAISE EXCEPTION 'fordring: en avsluttet fordring gjenåpnes'
                ' ikke' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.trinn IS DISTINCT FROM OLD.trinn THEN
            RAISE EXCEPTION 'fordring: trinnet flyttes ikke på en % '
                'fordring — den er gjort opp', OLD.status
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    -- DOM 3: ETT HAKK OM GANGEN, OG BARE FRAMOVER. Et hopp fra trinn 1
    -- til trinn 3 er en eskalering ingen besluttet — og for kunden er
    -- forskjellen mellom en påminnelse og et inkassovarsel hele saken.
    IF NEW.trinn IS DISTINCT FROM OLD.trinn THEN
        IF NEW.trinn <> OLD.trinn + 1 THEN
            RAISE EXCEPTION 'fordring: trinnet går fra % til %, og et'
                ' trinn går ETT hakk framover om gangen — et hopp er en'
                ' eskalering ingen besluttet', OLD.trinn, NEW.trinn
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL THEN
            RAISE EXCEPTION 'fordring: en trinnflytting krever en navngitt'
                ' aktør (disponit.aktor) — tiden purrer ingen'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    -- DOM 4, andre halvdel: `betalt_ore` ER summen av hendelsene, ikke
    -- et fritt tall. En vedlikeholdt avledning ingen kontrollerer er en
    -- denormalisering som driver (100s `sist_etterprovd`-form).
    IF NEW.betalt_ore IS DISTINCT FROM OLD.betalt_ore THEN
        SELECT coalesce(sum(h.belop_ore), 0)::bigint INTO v_sum
          FROM public.fordringshendelse h
         WHERE h.tenant = NEW.tenant AND h.fordring_id = NEW.fordring_id
           AND h.art = 'betaling';
        IF NEW.betalt_ore <> v_sum THEN
            RAISE EXCEPTION 'fordring: betalt_ore (%) er ikke summen av'
                ' de registrerte innbetalingene (%) — et betalt-beløp man'
                ' kan skrive fritt måler ingenting', NEW.betalt_ore, v_sum
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    IF NEW.status <> OLD.status AND NEW.status <> 'apen' THEN
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL OR NEW.avsluttet_av IS DISTINCT FROM v_aktor THEN
            RAISE EXCEPTION 'fordring: avsluttet_av (%) er ikke aktøren'
                ' som avslutter (%)',
                coalesce(NEW.avsluttet_av, '<null>'),
                coalesce(v_aktor, '<ingen>')
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m23_fordring_vakt() FROM PUBLIC;
CREATE TRIGGER m23_fordring_vakt
    BEFORE UPDATE OR DELETE ON fordring
    FOR EACH ROW EXECUTE FUNCTION m23_fordring_vakt();
CREATE TRIGGER m23_fordring_ingen_truncate
    BEFORE TRUNCATE ON fordring
    EXECUTE FUNCTION m23_fordring_vakt();

-- HENDELSENE ER APPEND-ONLY. En innbetaling som kunne endres eller
-- slettes ville gjort hele saldoen til en påstand.
CREATE FUNCTION m23_hendelse_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    -- TRUNCATE HAR SIN EGEN ARM. Uten den falt den gjennom til
    -- UPDATE-armen: riktig utfall, men setningen sa «UPDATE avvist» om
    -- en tømming. En feilmelding som sier noe annet enn det som skjedde
    -- er en feilmelding ingen kan følge.
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'fordringshendelse: TRUNCATE avvist — en tømt'
            ' hendelsestabell gjør hver saldo i registeret til en påstand'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'fordringshendelse: DELETE avvist — en registrert'
            ' innbetaling forsvinner ikke fordi noen ombestemte seg'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RAISE EXCEPTION 'fordringshendelse: UPDATE avvist — raden er'
        ' append-only. En feilført innbetaling rettes med en ny hendelse,'
        ' ikke ved å skrive om den gamle'
        USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE ALL ON FUNCTION m23_hendelse_vakt() FROM PUBLIC;
CREATE TRIGGER m23_hendelse_vakt
    BEFORE UPDATE OR DELETE ON fordringshendelse
    FOR EACH ROW EXECUTE FUNCTION m23_hendelse_vakt();
CREATE TRIGGER m23_hendelse_ingen_truncate
    BEFORE TRUNCATE ON fordringshendelse
    EXECUTE FUNCTION m23_hendelse_vakt();

CREATE FUNCTION m23_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    -- TRUNCATE HAR SIN EGEN ARM, og her er den ikke kosmetisk: uten den
    -- falt TG_OP='TRUNCATE' GLATT GJENNOM til RETURN NEW, og tømmingen
    -- skjedde. Triggeren het `m23_funn_ingen_truncate` og slapp TRUNCATE
    -- igjennom — en vakt som ikke vakter er verre enn ingen, fordi den
    -- leses som beskyttelse. (CodeRabbit, PR M-23.)
    --
    -- `purreplan` har ingen egen truncatevakt og trenger ingen: den er
    -- referert av `purretrinn`, så TRUNCATE uten CASCADE feiler på
    -- fremmednøkkelen, og TRUNCATE ... CASCADE treffer purretrinnvakten.
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'fordringsfunn: TRUNCATE avvist — et funn lukkes,'
            ' det tømmes ikke bort. En tom funntabell ser ut som en ren'
            ' natt' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'fordringsfunn: DELETE avvist — et funn lukkes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.tenant IS DISTINCT FROM OLD.tenant
           OR NEW.fordring_id IS DISTINCT FROM OLD.fordring_id
           OR NEW.funntype IS DISTINCT FROM OLD.funntype
           OR NEW.forst_sett IS DISTINCT FROM OLD.forst_sett THEN
            RAISE EXCEPTION 'fordringsfunn: identiteten og førstegangen'
                ' er frosset' USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m23_funn_vakt() FROM PUBLIC;
CREATE TRIGGER m23_funn_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON fordringsfunn
    FOR EACH ROW EXECUTE FUNCTION m23_funn_vakt();
CREATE TRIGGER m23_funn_ingen_truncate
    BEFORE TRUNCATE ON fordringsfunn
    EXECUTE FUNCTION m23_funn_vakt();


-- ------------------------------------------------------------
-- 2b. RLS og rettigheter.
-- ------------------------------------------------------------
ALTER TABLE purreplan ENABLE ROW LEVEL SECURITY;
ALTER TABLE purreplan FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON purreplan
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE purretrinn ENABLE ROW LEVEL SECURITY;
ALTER TABLE purretrinn FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON purretrinn
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE fordring ENABLE ROW LEVEL SECURITY;
ALTER TABLE fordring FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON fordring
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — tre gjerder, som i
-- 100–103: bare dørenes eier, bare SELECT, bare uten tenantkontekst.
CREATE POLICY m23_sveip_tenantliste ON fordring
    FOR SELECT TO disponit_fordring_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

ALTER TABLE fordringshendelse ENABLE ROW LEVEL SECURITY;
ALTER TABLE fordringshendelse FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON fordringshendelse
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE fordringsfunn ENABLE ROW LEVEL SECURITY;
ALTER TABLE fordringsfunn FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON fordringsfunn
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

GRANT SELECT, INSERT, UPDATE ON purreplan TO disponit_fordring_eier;
GRANT SELECT, INSERT, UPDATE, DELETE ON purretrinn
    TO disponit_fordring_eier;
GRANT SELECT, INSERT, UPDATE ON fordring TO disponit_fordring_eier;
GRANT SELECT, INSERT ON fordringshendelse TO disponit_fordring_eier;
GRANT SELECT, INSERT, UPDATE ON fordringsfunn TO disponit_fordring_eier;
-- PURRETRINNENE ER DEN ENESTE TABELLEN MED DELETE: planen REDIGERES ved
-- at trinnene skrives om i én omgang. `fordringshendelse` har med vilje
-- verken UPDATE eller DELETE — den er append-only helt ned til grantet.
GRANT INSERT ON revisjonslogg TO disponit_fordring_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_fordring_eier;
RESET ROLE;


-- ------------------------------------------------------------
-- 3. Dørene. SECURITY DEFINER, eid av `disponit_fordring_eier`, SP-1
--    (`krev_tenantkontekst`) først i hver tenantbundet definer.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_fordring_eier;

-- Evidenskjeden, ett sted. BELØP STÅR ALDRI HER — det er kundens
-- forretningsdata, og evidenskjeden skal gjenfinne HANDLINGEN uten å
-- arkivere pengestrømmen på nytt et sted til (101s dom, ordrett).
CREATE FUNCTION m23_evidens(p_tenant TEXT, p_fordring_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm23_fordring', 'handling', p_handling,
        'fordring_id', p_fordring_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm23_fordring',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:fordringsregister', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m23_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- PURREPLANDØREN. Setter HELE trinnsettet i én transaksjon og øker
-- versjonen — en dør som la til ett trinn om gangen ville latt planen
-- stå i en halvferdig tilstand, og sveipen ville vurdert fordringer mot
-- den i det vinduet.
CREATE FUNCTION m23_sett_purreplan(
    p_tenant TEXT, p_trinn JSONB, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_antall INT; v_i INT; v_t JSONB; v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_sett_purreplan');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF jsonb_typeof(p_trinn) <> 'array' THEN
        RAISE EXCEPTION 'm23_sett_purreplan: trinnene må være en liste'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_antall := jsonb_array_length(p_trinn);
    IF v_antall < 1 THEN
        RAISE EXCEPTION 'm23_sett_purreplan: en purreplan uten trinn er'
            ' ingen plan — da vet ingen når noe eskalerer'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.purreplan (tenant, oppdatert_av)
    VALUES (p_tenant, p_aktor)
        ON CONFLICT (tenant) DO NOTHING;
    -- SLETT FØR INNSETT, i samme transaksjon: trinnvakten sammenligner
    -- mot naboene, og en delvis gammel plan ville gitt falske
    -- rekkefølgefeil underveis.
    DELETE FROM public.purretrinn WHERE tenant = p_tenant;
    FOR v_i IN 0 .. v_antall - 1 LOOP
        v_t := p_trinn -> v_i;
        INSERT INTO public.purretrinn
            (tenant, trinn_nr, navn, dogn_etter_forfall, handling,
             gebyr_ore)
        VALUES (p_tenant, v_i + 1, v_t ->> 'navn',
                (v_t ->> 'dogn_etter_forfall')::int,
                v_t ->> 'handling',
                coalesce((v_t ->> 'gebyr_ore')::bigint, 0));
    END LOOP;
    UPDATE public.purreplan
       SET versjon = versjon + 1, oppdatert = now(), oppdatert_av = p_aktor
     WHERE tenant = p_tenant
     RETURNING versjon INTO v_versjon;
    PERFORM public.m23_evidens(
        p_tenant, NULL, 'purreplan.satt', p_aktor,
        jsonb_build_object('trinn', v_antall, 'versjon', v_versjon));
    RETURN v_versjon;
END $$;
REVOKE ALL ON FUNCTION m23_sett_purreplan(TEXT, JSONB, TEXT) FROM PUBLIC;

-- FORDRINGSDØREN. SP-2-materialitet på `p_fordring_id`.
CREATE FUNCTION m23_registrer_fordring(
    p_tenant TEXT, p_fordring_id UUID, p_kunde_ref TEXT,
    p_fakturanummer TEXT, p_belop_ore BIGINT, p_utstedt DATE,
    p_forfall DATE, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_gammel RECORD; v_nr TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm23_registrer_fordring');
    IF coalesce(p_belop_ore, 0) <= 0 THEN
        RAISE EXCEPTION 'm23_registrer_fordring: beløpet må være positivt'
            ' — en negativ fordring er en kreditnota, og det er noe annet'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_nr := btrim(coalesce(p_fakturanummer, ''));
    IF v_nr = '' THEN
        RAISE EXCEPTION 'm23_registrer_fordring: fakturanummeret kan ikke'
            ' være tomt' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_kunde_ref IS NULL OR p_kunde_ref !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm23_registrer_fordring: kundereferansen kan ikke'
            ' være tom — et krav ingen kan si hvem gjelder, kan ingen'
            ' kreve inn' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_forfall < p_utstedt THEN
        RAISE EXCEPTION 'm23_registrer_fordring: forfall før utstedelse'
            ' er ingen frist' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.fordring
        (tenant, fordring_id, kunde_ref, fakturanummer, belop_ore,
         utstedt, forfall, opprettet_av)
    VALUES (p_tenant, p_fordring_id, btrim(p_kunde_ref), v_nr,
            p_belop_ore, p_utstedt, p_forfall, p_aktor)
        ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        SELECT * INTO v_gammel FROM public.fordring
         WHERE tenant = p_tenant AND fordring_id = p_fordring_id;
        IF v_gammel IS NULL
           OR v_gammel.fakturanummer IS DISTINCT FROM v_nr
           OR v_gammel.belop_ore IS DISTINCT FROM p_belop_ore
           OR v_gammel.kunde_ref IS DISTINCT FROM btrim(p_kunde_ref)
           OR v_gammel.forfall IS DISTINCT FROM p_forfall THEN
            RAISE EXCEPTION 'm23_registrer_fordring: fakturanummeret er i'
                ' bruk, eller samme fordring_id med annet innhold —'
                ' materiell konflikt' USING ERRCODE = 'unique_violation';
        END IF;
        RETURN false;
    END IF;
    PERFORM public.m23_evidens(
        p_tenant, p_fordring_id, 'fordring.registrert', p_aktor,
        jsonb_build_object('forfall', p_forfall));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m23_registrer_fordring(TEXT, UUID, TEXT, TEXT,
    BIGINT, DATE, DATE, TEXT) FROM PUBLIC;

-- BETALINGSDØREN. Skriver hendelsen OG avledningen i SAMME transaksjon,
-- og lukker fordringen når den er gjort opp.
--
-- REKKEFØLGEN ER HENDELSE → AVLEDNING, som i 100: vakten krever at
-- `betalt_ore` er summen av hendelsene, så raden MÅ finnes først.
CREATE FUNCTION m23_registrer_betaling(
    p_tenant TEXT, p_hendelse_id UUID, p_fordring_id UUID,
    p_belop_ore BIGINT, p_inntruffet DATE, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_f RECORD; v_sum BIGINT; v_rader INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm23_registrer_betaling');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF coalesce(p_belop_ore, 0) <= 0 THEN
        RAISE EXCEPTION 'm23_registrer_betaling: beløpet må være positivt'
            ' — en negativ innbetaling er en tilbakebetaling, og den'
            ' hører ikke hjemme her'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO v_f FROM public.fordring f
     WHERE f.tenant = p_tenant AND f.fordring_id = p_fordring_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm23_registrer_betaling: fordringen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_f.status <> 'apen' THEN
        RAISE EXCEPTION 'm23_registrer_betaling: fordringen er % — en'
            ' innbetaling på et avsluttet krav er en tilgodehavende, og'
            ' det er et annet register', v_f.status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_f.betalt_ore + p_belop_ore > v_f.belop_ore THEN
        RAISE EXCEPTION 'm23_registrer_betaling: innbetalingen ville gitt'
            ' % av et krav på % — overbetaling er en tilgodehavende, ikke'
            ' en fordring', v_f.betalt_ore + p_belop_ore, v_f.belop_ore
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.fordringshendelse
        (tenant, hendelse_id, fordring_id, art, belop_ore, inntruffet,
         opprettet_av)
    VALUES (p_tenant, p_hendelse_id, p_fordring_id, 'betaling',
            p_belop_ore, p_inntruffet, p_aktor)
        ON CONFLICT (tenant, hendelse_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;                               -- SP-2: stille ja
    END IF;
    SELECT coalesce(sum(h.belop_ore), 0)::bigint INTO v_sum
      FROM public.fordringshendelse h
     WHERE h.tenant = p_tenant AND h.fordring_id = p_fordring_id
       AND h.art = 'betaling';
    UPDATE public.fordring
       SET betalt_ore = v_sum,
           -- FULLT BETALT LUKKER KRAVET. Uten det ville en oppgjort
           -- fordring fortsatt stått som åpen og blitt et funn i natt.
           status = CASE WHEN v_sum >= belop_ore THEN 'betalt'
                         ELSE status END,
           avsluttet_ts = CASE WHEN v_sum >= belop_ore THEN now() END,
           avsluttet_av = CASE WHEN v_sum >= belop_ore THEN p_aktor END
     WHERE tenant = p_tenant AND fordring_id = p_fordring_id;
    PERFORM public.m23_evidens(
        p_tenant, p_fordring_id, 'fordring.betaling', p_aktor,
        jsonb_build_object('gjort_opp', v_sum >= v_f.belop_ore));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m23_registrer_betaling(TEXT, UUID, UUID, BIGINT,
    DATE, TEXT) FROM PUBLIC;

-- TRINNDØREN. DOM 3: ETT HAKK, FRAMOVER, AV ET MENNESKE.
--
-- Døren tar IKKE imot hvilket trinn det skal settes til — den flytter
-- til NESTE. En parameter ville invitert til nettopp det hoppet vakten
-- finnes for å hindre, og et API som lar deg be om noe basen alltid
-- avviser er et API som lyver.
CREATE FUNCTION m23_neste_trinn(
    p_tenant TEXT, p_hendelse_id UUID, p_fordring_id UUID,
    p_begrunnelse TEXT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_f RECORD; v_nytt INT; v_finnes BOOLEAN; v_versjon INT;
        v_rader INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_neste_trinn');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    SELECT * INTO v_f FROM public.fordring f
     WHERE f.tenant = p_tenant AND f.fordring_id = p_fordring_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm23_neste_trinn: fordringen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_f.status <> 'apen' THEN
        RAISE EXCEPTION 'm23_neste_trinn: fordringen er % — en avsluttet'
            ' fordring eskalerer ikke', v_f.status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_nytt := v_f.trinn + 1;
    SELECT true INTO v_finnes FROM public.purretrinn t
     WHERE t.tenant = p_tenant AND t.trinn_nr = v_nytt;
    IF v_finnes IS NULL THEN
        RAISE EXCEPTION 'm23_neste_trinn: purreplanen har ikke noe trinn'
            ' % — enten er planen ikke satt, eller fordringen har nådd'
            ' siste trinn', v_nytt
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT p.versjon INTO v_versjon FROM public.purreplan p
     WHERE p.tenant = p_tenant;
    INSERT INTO public.fordringshendelse
        (tenant, hendelse_id, fordring_id, art, trinn, begrunnelse,
         inntruffet, opprettet_av)
    VALUES (p_tenant, p_hendelse_id, p_fordring_id, 'trinn', v_nytt,
            nullif(btrim(coalesce(p_begrunnelse, '')), ''),
            current_date, p_aktor)
        ON CONFLICT (tenant, hendelse_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN v_f.trinn;                           -- SP-2: stille ja
    END IF;
    UPDATE public.fordring
       SET trinn = v_nytt, purreplan_versjon = v_versjon
     WHERE tenant = p_tenant AND fordring_id = p_fordring_id;
    PERFORM public.m23_evidens(
        p_tenant, p_fordring_id, 'fordring.trinn', p_aktor,
        jsonb_build_object('trinn', v_nytt,
                           'purreplan_versjon', v_versjon));
    RETURN v_nytt;
END $$;
REVOKE ALL ON FUNCTION m23_neste_trinn(TEXT, UUID, UUID, TEXT, TEXT)
    FROM PUBLIC;

-- ETTERGIVELSESDØREN. Å slette et krav uten å si hvorfor er den ene
-- handlingen ingen kan etterprøve senere.
CREATE FUNCTION m23_ettergi(
    p_tenant TEXT, p_hendelse_id UUID, p_fordring_id UUID,
    p_begrunnelse TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_rader INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_ettergi');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_begrunnelse IS NULL OR p_begrunnelse !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm23_ettergi: en ettergivelse uten begrunnelse er'
            ' en avskrivning ingen kan etterprøve'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- FOR UPDATE, som i de to andre skrivedørene. Uten låsen kunne to
    -- samtidige ettergivelser begge lese 'apen', begge legge inn en
    -- 'ettergitt'-hendelse og begge skrive en evidensrad — og den andre
    -- UPDATE-en ville truffet null rader (`AND status = 'apen'`) og
    -- likevel returnert true. Verre: en full innbetaling holder låsen,
    -- så uten den her kunne et krav endt som 'betalt' med en
    -- 'ettergitt'-hendelse ved siden av. (CodeRabbit, PR M-23.)
    SELECT f.status INTO v_status FROM public.fordring f
     WHERE f.tenant = p_tenant AND f.fordring_id = p_fordring_id
       FOR UPDATE;
    IF v_status IS NULL THEN
        RAISE EXCEPTION 'm23_ettergi: fordringen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_status <> 'apen' THEN
        RETURN false;                               -- stille ja
    END IF;
    INSERT INTO public.fordringshendelse
        (tenant, hendelse_id, fordring_id, art, begrunnelse, inntruffet,
         opprettet_av)
    VALUES (p_tenant, p_hendelse_id, p_fordring_id, 'ettergitt',
            btrim(p_begrunnelse), current_date, p_aktor)
        ON CONFLICT (tenant, hendelse_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;
    END IF;
    UPDATE public.fordring
       SET status = 'ettergitt', avsluttet_ts = now(),
           avsluttet_av = p_aktor,
           ettergitt_begrunnelse = btrim(p_begrunnelse)
     WHERE tenant = p_tenant AND fordring_id = p_fordring_id
       AND status = 'apen';
    PERFORM public.m23_evidens(
        p_tenant, p_fordring_id, 'fordring.ettergitt', p_aktor,
        jsonb_build_object());
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m23_ettergi(TEXT, UUID, UUID, TEXT, TEXT)
    FROM PUBLIC;


-- ------------------------------------------------------------
-- 3b. Lesedørene.
-- ------------------------------------------------------------

-- SAMMENDRAGET TELLER ALT, listen viser de N eldste (100–103s dom).
CREATE FUNCTION m23_fordringsstatus(p_tenant TEXT)
RETURNS TABLE(apne INT, apent_ore BIGINT, forfalte INT,
              forfalt_ore BIGINT, i_purring INT, apne_funn INT,
              har_purreplan BOOLEAN)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm23_fordringsstatus');
    SELECT count(*) FILTER (WHERE f.status = 'apen')::int,
           coalesce(sum(f.belop_ore - f.betalt_ore)
                    FILTER (WHERE f.status = 'apen'), 0)::bigint,
           count(*) FILTER (WHERE f.status = 'apen'
                              AND f.forfall < current_date)::int,
           coalesce(sum(f.belop_ore - f.betalt_ore)
                    FILTER (WHERE f.status = 'apen'
                              AND f.forfall < current_date), 0)::bigint,
           count(*) FILTER (WHERE f.status = 'apen' AND f.trinn > 0)::int
      INTO apne, apent_ore, forfalte, forfalt_ore, i_purring
      FROM public.fordring f WHERE f.tenant = p_tenant;
    SELECT count(*)::int INTO apne_funn FROM public.fordringsfunn ff
     WHERE ff.tenant = p_tenant AND ff.apen;
    SELECT EXISTS (SELECT 1 FROM public.purretrinn t
                    WHERE t.tenant = p_tenant) INTO har_purreplan;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m23_fordringsstatus(TEXT) FROM PUBLIC;

-- ALDERSFORDELINGEN. Regnet i BASEN, i faste bøtter.
--
-- BØTTEKANTENE ER HALVÅPNE og ligger i ÉN `width_bucket`-lignende
-- CASE: 0–30, 31–60, 61–90, over 90. En fordring som er nøyaktig 30
-- døgn gammel havner i den FØRSTE bøtta, ikke i begge og ikke i ingen —
-- en aldersfordeling som er feil på kanten er feil overalt der det
-- betyr noe, og det er nettopp kantene manifestets datasettkrav peker
-- på.
CREATE FUNCTION m23_aldersfordeling(p_tenant TEXT)
RETURNS TABLE(botte TEXT, antall INT, ore BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm23_aldersfordeling');
    RETURN QUERY
    WITH merket AS (
        SELECT CASE
                 WHEN current_date <= f.forfall THEN 'ikke_forfalt'
                 WHEN current_date - f.forfall <= 30 THEN '1_30'
                 WHEN current_date - f.forfall <= 60 THEN '31_60'
                 WHEN current_date - f.forfall <= 90 THEN '61_90'
                 ELSE 'over_90' END AS b,
               f.belop_ore - f.betalt_ore AS rest
          FROM public.fordring f
         WHERE f.tenant = p_tenant AND f.status = 'apen')
    -- ALLE BØTTENE STÅR I SVARET, også de tomme. En flate som bare fikk
    -- bøttene med innhold, ville tegnet en fordeling som endret form
    -- fra dag til dag — og et diagram uten faste kolonner kan ingen
    -- sammenligne over tid.
    SELECT k.b, coalesce(count(m.b), 0)::int,
           coalesce(sum(m.rest), 0)::bigint
      FROM (VALUES ('ikke_forfalt'), ('1_30'), ('31_60'), ('61_90'),
                   ('over_90')) AS k(b)
      LEFT JOIN merket m ON m.b = k.b
     GROUP BY k.b
     ORDER BY CASE k.b WHEN 'ikke_forfalt' THEN 0 WHEN '1_30' THEN 1
                       WHEN '31_60' THEN 2 WHEN '61_90' THEN 3
                       ELSE 4 END;
END $$;
REVOKE ALL ON FUNCTION m23_aldersfordeling(TEXT) FROM PUBLIC;

-- FORDRINGENE, mest forfalte først.
CREATE FUNCTION m23_fordringene(p_tenant TEXT, p_grense INT)
RETURNS TABLE(fordring_id UUID, kunde_ref TEXT, fakturanummer TEXT,
              belop_ore BIGINT, betalt_ore BIGINT, rest_ore BIGINT,
              utstedt DATE, forfall DATE, dogn_over_forfall INT,
              status TEXT, trinn INT, trinn_navn TEXT,
              moden_for_trinn INT, apne_funn TEXT[])
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_fordringene');
    RETURN QUERY
    SELECT f.fordring_id, f.kunde_ref, f.fakturanummer, f.belop_ore,
           f.betalt_ore, f.belop_ore - f.betalt_ore, f.utstedt, f.forfall,
           (current_date - f.forfall)::int,
           f.status, f.trinn,
           (SELECT t.navn FROM public.purretrinn t
             WHERE t.tenant = f.tenant AND t.trinn_nr = f.trinn),
           -- MODEN FOR TRINN: det HØYESTE trinnet fordringens alder har
           -- passert. Er det høyere enn `trinn`, står den og venter på
           -- et menneske — og det er hele opplysningen flaten finnes
           -- for. Regnet i basen, i samme skann som raden.
           (SELECT max(t.trinn_nr) FROM public.purretrinn t
             WHERE t.tenant = f.tenant
               AND current_date - f.forfall >= t.dogn_etter_forfall),
           coalesce((SELECT array_agg(ff.funntype ORDER BY ff.funntype)
                       FROM public.fordringsfunn ff
                      WHERE ff.tenant = f.tenant
                        AND ff.fordring_id = f.fordring_id
                        AND ff.apen), ARRAY[]::TEXT[])
      FROM public.fordring f
     WHERE f.tenant = p_tenant
     -- Åpne først, deretter mest forfalt. `fordring_id` som tiebreaker
     -- (100s bitmap-lærdom): to fordringer med samme forfall skal ikke
     -- bytte plass mellom to kall.
     ORDER BY (f.status <> 'apen'), f.forfall, f.fordring_id
     LIMIT greatest(least(coalesce(p_grense, 100), 1000), 1);
END $$;
REVOKE ALL ON FUNCTION m23_fordringene(TEXT, INT) FROM PUBLIC;

-- PURREPLANEN, med versjonen.
CREATE FUNCTION m23_purreplanen(p_tenant TEXT)
RETURNS TABLE(versjon INT, trinn_nr INT, navn TEXT,
              dogn_etter_forfall INT, handling TEXT, gebyr_ore BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_purreplanen');
    RETURN QUERY
    SELECT p.versjon, t.trinn_nr, t.navn, t.dogn_etter_forfall,
           t.handling, t.gebyr_ore
      FROM public.purretrinn t
      JOIN public.purreplan p ON p.tenant = t.tenant
     WHERE t.tenant = p_tenant
     ORDER BY t.trinn_nr;
END $$;
REVOKE ALL ON FUNCTION m23_purreplanen(TEXT) FROM PUBLIC;

-- ÉN FORDRINGS HISTORIKK, nyest først.
CREATE FUNCTION m23_hendelsene(p_tenant TEXT, p_fordring_id UUID)
RETURNS TABLE(hendelse_id UUID, art TEXT, belop_ore BIGINT, trinn INT,
              begrunnelse TEXT, inntruffet DATE, opprettet TIMESTAMPTZ,
              opprettet_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_hendelsene');
    RETURN QUERY
    SELECT h.hendelse_id, h.art, h.belop_ore, h.trinn, h.begrunnelse,
           h.inntruffet, h.opprettet, h.opprettet_av
      FROM public.fordringshendelse h
     WHERE h.tenant = p_tenant AND h.fordring_id = p_fordring_id
     ORDER BY h.inntruffet DESC, h.opprettet DESC, h.hendelse_id;
END $$;
REVOKE ALL ON FUNCTION m23_hendelsene(TEXT, UUID) FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Sveipens kandidater.
-- ------------------------------------------------------------
CREATE FUNCTION m23_funnkandidater(p_tenant TEXT, p_dag DATE)
RETURNS TABLE(fordring_id UUID, funntype TEXT, moden_for_trinn INT,
              dogn_over_grense INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_funnkandidater');
    RETURN QUERY
    -- 1. TRINN FORFALT: fordringen har passert et høyere trinn enn den
    --    står på. Dette er modulens hovedfunn — det er her «en fordring
    --    som passerer sitt purretrinn er et FUNN, ikke en stille gammel
    --    rad» blir sant. Sveipen FLYTTER ikke trinnet; den sier fra.
    SELECT f.fordring_id, 'trinn_forfalt'::text, m.moden,
           (p_dag - f.forfall - t.dogn_etter_forfall)::int
      FROM public.fordring f
      CROSS JOIN LATERAL (
            SELECT max(t2.trinn_nr) AS moden FROM public.purretrinn t2
             WHERE t2.tenant = f.tenant
               AND p_dag - f.forfall >= t2.dogn_etter_forfall) m
      JOIN public.purretrinn t
        ON t.tenant = f.tenant AND t.trinn_nr = m.moden
     WHERE f.tenant = p_tenant AND f.status = 'apen'
       AND m.moden IS NOT NULL AND m.moden > f.trinn
    UNION ALL
    -- 2. INGEN PURREPLAN: tenanten har forfalte fordringer, men ingen
    --    plan å måle dem mot. Funnet er PER FORDRING og ikke per tenant,
    --    fordi funntabellen er nøklet på fordringen — og fordi det er
    --    på fordringen et menneske faktisk ser det.
    SELECT f.fordring_id, 'ingen_purreplan'::text, NULL::int,
           (p_dag - f.forfall)::int
      FROM public.fordring f
     WHERE f.tenant = p_tenant AND f.status = 'apen'
       AND f.forfall < p_dag
       AND NOT EXISTS (SELECT 1 FROM public.purretrinn t
                        WHERE t.tenant = f.tenant)
    UNION ALL
    -- 3. FORFALT UTEN TRINN over lang tid: forfalt mer enn 90 døgn og
    --    fortsatt på trinn 0. Det er et krav ingen har rørt, og det er
    --    en annen tilstand enn «trinn forfalt» — her er det ikke
    --    eskaleringen som henger etter, det er hele oppfølgingen.
    SELECT f.fordring_id, 'forfalt_uten_trinn'::text, NULL::int,
           (p_dag - f.forfall - 90)::int
      FROM public.fordring f
     WHERE f.tenant = p_tenant AND f.status = 'apen'
       AND f.trinn = 0 AND (p_dag - f.forfall) > 90
       AND EXISTS (SELECT 1 FROM public.purretrinn t
                    WHERE t.tenant = f.tenant);
END $$;
REVOKE ALL ON FUNCTION m23_funnkandidater(TEXT, DATE) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 4b. Sveipen. KRYSS-TENANT, egen LOGIN-rolle, egen timer.
--
--     SVEIPEN FLYTTER INGEN TRINN. Den kunne, teknisk — den vet hvilke
--     fordringer som er modne. Men et trinn er en ESKALERING MOT EN
--     KUNDE, og en jobb som eskalerer om natten er nøyaktig den
--     fullmakten v1 ikke gir seg selv. Den skriver funn; et menneske
--     flytter trinnet.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_fordring_eier;

CREATE FUNCTION m23_sveip_fordringer(p_grense INT DEFAULT 500)
RETURNS TABLE(tenanter INT, nye INT, oppdaterte INT, lukkede INT,
              avkortet INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_n INT; v_grense INT;
        v_dag DATE; v_naa TIMESTAMPTZ;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm23_sveip_fordringer: sveipen er KRYSS-TENANT og'
            ' kjøres uten tenantkontekst — en kaller som har satt en'
            ' kontekst ber om noe annet enn det denne funksjonen gjør'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_grense := greatest(least(coalesce(p_grense, 500), 5000), 1);
    v_dag := current_date;
    v_naa := now();
    tenanter := 0; nye := 0; oppdaterte := 0; lukkede := 0; avkortet := 0;
    SELECT array_agg(DISTINCT f.tenant ORDER BY f.tenant) INTO v_tenanter
      FROM public.fordring f;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        tenanter := tenanter + 1;

        UPDATE public.fordringsfunn ff
           SET sist_sett_sveip = v_naa, apen = true, lukket_ts = NULL,
               moden_for_trinn = kand.moden_for_trinn,
               dogn_over_grense = kand.dogn_over_grense
          FROM public.m23_funnkandidater(v_t, v_dag) kand
         WHERE ff.tenant = v_t AND ff.fordring_id = kand.fordring_id
           AND ff.funntype = kand.funntype;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        oppdaterte := oppdaterte + v_n;

        INSERT INTO public.fordringsfunn
            (tenant, fordring_id, funntype, moden_for_trinn,
             dogn_over_grense, forst_sett, sist_sett_sveip, apen)
        SELECT v_t, kand.fordring_id, kand.funntype,
               kand.moden_for_trinn, kand.dogn_over_grense, v_naa, v_naa,
               true
          FROM public.m23_funnkandidater(v_t, v_dag) kand
         WHERE NOT EXISTS (
                SELECT 1 FROM public.fordringsfunn ff
                 WHERE ff.tenant = v_t
                   AND ff.fordring_id = kand.fordring_id
                   AND ff.funntype = kand.funntype)
         ORDER BY coalesce(kand.dogn_over_grense, 0) DESC,
                  kand.fordring_id, kand.funntype
         LIMIT v_grense;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        nye := nye + v_n;
        IF v_n = v_grense THEN
            avkortet := avkortet + 1;
        END IF;

        UPDATE public.fordringsfunn ff
           SET apen = false, lukket_ts = v_naa
         WHERE ff.tenant = v_t AND ff.apen
           AND NOT EXISTS (
                SELECT 1 FROM public.m23_funnkandidater(v_t, v_dag) kand
                 WHERE kand.fordring_id = ff.fordring_id
                   AND kand.funntype = ff.funntype);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        lukkede := lukkede + v_n;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m23_sveip_fordringer(INT) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 5. Rettighetene. `m23_sveip_fordringer` og `m23_funnkandidater`
--    grantes ingen: den første er sveiperollens, den andre internt ledd.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_fordring_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_fordringsstatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_aldersfordeling(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_fordringene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_purreplanen(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_hendelsene(TEXT, UUID)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m23_sett_purreplan(TEXT, JSONB, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_registrer_fordring(TEXT,'
            ' UUID, TEXT, TEXT, BIGINT, DATE, DATE, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_registrer_betaling(TEXT,'
            ' UUID, UUID, BIGINT, DATE, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m23_neste_trinn(TEXT, UUID, UUID, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m23_ettergi(TEXT, UUID, UUID, TEXT, TEXT) TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_fordringssveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_sveip_fordringer(INT)'
            ' TO disponit_fordringssveip';
    END IF;
END $$;
RESET ROLE;
