-- =====================================================================
-- M-7 MØTEOPERASJONSAGENT (v1) — KLYNGE 9s FØRSTE.
-- =====================================================================
--
-- V1-DOMMEN: MODULEN FATTER INGEN BESLUTNING. En «beslutning» i et
-- referat er noe MENNESKER tok. Modulen kan skrive den ned — den kan
-- ikke fatte den, og den kan ikke skrive den ned uten et navn på hvem
-- som tok den.
--
-- ---------------------------------------------------------------------
-- KLYNGENS DELTE DOM:
--
--   EN YTRING AVGITT I HUSETS NAVN KAN IKKE TAS TILBAKE — OG DEN SOM
--   LESER DEN VET IKKE AT EN MASKIN SKREV DEN.
--
-- Klynge 7s feilform kunne SLÅS OPP: en foreldet regel finnes et sted.
-- Klynge 8s kunne MÅLES: en gal prognose møter horisonten sin. Denne
-- kan ingen av delene. Et referat er lest av noen, og det som er lest
-- kan ikke uleses.
--
-- ---------------------------------------------------------------------
-- OPPTAKET ER DET ENESTE I HELE MODULEN SOM IKKE KAN GJØRES UGJORT.
--
-- Vaktsetningen sier «opptak starter kun med gyldig policy/varsling».
-- Rekkefølgen i den setningen er hele poenget, og den er håndhevet i
-- basen: `moteopptak_varsling_kom_forst` krever
-- `varslet_ts <= startet_ts`.
--
-- ET NEKT SOM KOMMER ETTER MIKROFONEN ER IKKE ET NEKT. Et opptak tatt
-- uten grunnlag er ulovlig i det øyeblikket det starter; å oppdage det
-- i en nattlig sveip er å oppdage en skade, ikke å hindre den. Derfor
-- er `hjemmel_id` og `varslet_ts` `NOT NULL` med fremmednøkkel, og
-- døra nekter FØR raden finnes.
--
-- ---------------------------------------------------------------------
-- HJEMMELEN ER DELT MED M-43, OG DEN BYGGES HER.
--
-- Klynge 9-fundamentet slo fast at M-7 og M-43 deler ÉN opptakshjemmel.
-- `opptak_uten_hjemmel` og `opptak_uten_varsling` står i BEGGE
-- grensene nettopp for at den ene modulen ikke skal kunne landes med
-- sin egen modell. To modeller for samme hjemmel ville gitt to svar på
-- «hadde vi lov».
--
-- `samtykkehendelse` (M-44, 114) ER IKKE DEN HJEMMELEN, og det ble
-- målt mot basen før første linje kode: den er nøklet på
-- `mottaker_id`, `kanal` og `formal`. Den svarer på «har vi lov til å
-- SENDE dette», ikke på «har vi lov til å TA OPP denne samtalen».
--
-- OG SAMTYKKE ER BARE ÉN AV FIRE GRUNNLAGSTYPER HER. I en
-- arbeidsrelasjon er samtykke ofte IKKE gyldig — maktubalansen gjør
-- det — og en modell som bare kjente samtykke ville tvunget fram et
-- ugyldig grunnlag for å komme videre.
--
-- ---------------------------------------------------------------------
-- M-8 ER EN NABO, IKKE ET FUNDAMENT.
--
-- Målt mot basen: `m8_slot` er nøklet på `prosess_id`, og
-- `m8_slotvalg` på `kandidat_id`. Det er REKRUTTERINGENS
-- tidsbooking — «finn et tidspunkt som passer for kandidaten» — ikke
-- møter med agenda og referat.
--
-- M-8 FINNER TIDSPUNKTET, M-7 EIER DET SOM SKJER I MØTET. En modul
-- som utvidet slotregisteret til å bære referater ville gjort
-- tidsbooking til møteledelse i stillhet.
--
-- ---------------------------------------------------------------------
-- HUSET HAR INGET PERSONREGISTER, og M-7 later ikke som det finnes.
--
-- Verifisert: M-30 bruker `subjekt_ref`, M-50 `journalperson.navn`,
-- M-39 `lonnstaker.ekstern_ref`. Tre moduler har hver valgt en ÅPEN
-- referanse fordi det ikke finnes noe å peke på. M-7 gjør det samme:
-- `deltaker_ref` er en tekst, ikke en fremmednøkkel til en tabell som
-- ikke er skrevet.
-- =====================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_mote_eier') THEN
        RAISE EXCEPTION 'rollen disponit_mote_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_motesveip') THEN
        RAISE EXCEPTION 'rollen disponit_motesveip mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_mote_eier;
GRANT INSERT ON revisjonslogg TO disponit_mote_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_mote_eier;
RESET ROLE;

-- ---------------------------------------------------------------------
-- TENANTENS EGNE GRENSER.
--
-- `sikkerhetsterskel_bp` ER DEN VIKTIGSTE. Vaktsetningen sier «lav
-- sikkerhet merkes som ubekreftet», men HVA som er lavt er en
-- vurdering av hvor mye det koster å ta feil — og den vurderingen er
-- tenantens, ikke modulens. Et styremøte og en ukentlig statusrunde
-- tåler ikke det samme.
-- ---------------------------------------------------------------------
CREATE TABLE motekrav (
    tenant TEXT PRIMARY KEY CHECK (length(btrim(tenant)) > 0),
    -- Hvor lenge etter møtet vi krever at referatet finnes. Et referat
    -- som kommer tre uker senere er en rekonstruksjon, ikke et referat.
    referatfrist_dogn INT NOT NULL DEFAULT 3
        CHECK (referatfrist_dogn BETWEEN 1 AND 60),
    -- Hvor lenge en aksjon får stå over fristen sin før det er et funn.
    aksjonsfrist_dogn INT NOT NULL DEFAULT 7
        CHECK (aksjonsfrist_dogn BETWEEN 1 AND 180),
    -- I BASISPUNKTER, som resten av huset regner usikkerhet i
    -- (M-15/M-36). 7000 = 70 %: et punkt maskinen er mindre enn 70 %
    -- sikker på, merkes ubekreftet.
    sikkerhetsterskel_bp INT NOT NULL DEFAULT 7000
        CHECK (sikkerhetsterskel_bp BETWEEN 1 AND 10000),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon > 0),
    -- IDEMPOTENSNØKKELEN LEVER PÅ RADEN (119s lærdom).
    siste_nokkel TEXT,
    satt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    satt_av TEXT NOT NULL CHECK (length(btrim(satt_av)) > 0)
);

-- ---------------------------------------------------------------------
-- OPPTAKSHJEMMELEN — REGISTERET M-43 ARVER.
--
-- IDENTITETEN ER FROSSET etter innsetting; bare `gyldig_til` kan
-- settes. 121s dom, og den er skarp her: en hjemmel som kunne
-- redigeres i ettertid ville gjort «hva hvilte opptaket på?» til et
-- oppslag i noe som har endret seg siden.
--
-- GRUNNLAGSTYPEN ER ET LUKKET SETT PÅ FIRE, ikke en boolsk «samtykke
-- ja/nei». Samtykke er ETT av grunnlagene, og i en arbeidsrelasjon
-- ofte det svakeste.
-- ---------------------------------------------------------------------
CREATE TABLE opptakshjemmel (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    hjemmel_id UUID NOT NULL,
    PRIMARY KEY (tenant, hjemmel_id),
    grunnlagstype TEXT NOT NULL
        CONSTRAINT opptakshjemmel_grunnlagstype_lukket
        CHECK (grunnlagstype IN ('samtykke', 'avtale',
                                 'berettiget_interesse',
                                 'rettslig_forpliktelse')),
    -- HVA GRUNNLAGET FAKTISK ER, SKREVET UT. Ikke en enum: den som
    -- skal svare for opptaket skal kunne lese hvorfor det var lov,
    -- ikke slå opp en kode.
    beskrivelse TEXT NOT NULL CHECK (length(btrim(beskrivelse)) >= 16),
    -- FORMÅLET. Et grunnlag uten formål er et grunnlag for hva som
    -- helst, og det er ikke et grunnlag.
    formal TEXT NOT NULL CHECK (length(btrim(formal)) >= 8),
    gyldig_fra DATE NOT NULL,
    gyldig_til DATE,
    CONSTRAINT opptakshjemmel_gyldighet CHECK (
        gyldig_til IS NULL OR gyldig_til >= gyldig_fra),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (length(btrim(registrert_av)) > 0)
);

CREATE INDEX opptakshjemmel_gjeldende
    ON opptakshjemmel (tenant, gyldig_fra DESC)
    WHERE gyldig_til IS NULL;

-- ---------------------------------------------------------------------
-- MØTET.
--
-- `deltakere` ER ÅPNE REFERANSER, ikke fremmednøkler: huset har intet
-- personregister, og M-30, M-50 og M-39 har hver valgt samme løsning.
-- En fremmednøkkel til en tabell som ikke er skrevet ville vært en
-- løgn med en constraint på.
-- ---------------------------------------------------------------------
CREATE TABLE mote (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    mote_id UUID NOT NULL,
    PRIMARY KEY (tenant, mote_id),
    tittel TEXT NOT NULL CHECK (tittel ~ '[^[:space:]]'),
    start_ts TIMESTAMPTZ NOT NULL,
    slutt_ts TIMESTAMPTZ NOT NULL,
    CONSTRAINT mote_varer_en_stund CHECK (slutt_ts > start_ts),
    -- HVEM SOM KALTE INN. Et møte uten innkaller er et møte ingen
    -- eier.
    innkalt_av TEXT NOT NULL CHECK (length(btrim(innkalt_av)) > 0),
    -- ÅPNE REFERANSER (se filhodet).
    deltakere TEXT[] NOT NULL
        CONSTRAINT mote_har_deltakere
        CHECK (cardinality(deltakere) > 0),
    agenda TEXT NOT NULL CHECK (length(btrim(agenda)) > 0),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (length(btrim(opprettet_av)) > 0)
);

CREATE INDEX mote_ferskeste ON mote (tenant, start_ts DESC);

-- ---------------------------------------------------------------------
-- OPPTAKET — DER REKKEFØLGEN ER HELE REGELEN.
--
-- APPEND-ONLY. Et opptak er en HENDELSE som fant sted; en rad som
-- kunne redigeres ville gjort «var det varslet?» til et spørsmål med
-- et svar som kan endres etterpå.
--
-- `moteopptak_varsling_kom_forst` ER MODULENS VIKTIGSTE CHECK:
-- varslingen må ha skjedd FØR opptaket startet. En varsling registrert
-- i etterkant er ikke en varsling — det er en unnskyldning.
-- ---------------------------------------------------------------------
CREATE TABLE moteopptak (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    opptak_id UUID NOT NULL,
    PRIMARY KEY (tenant, opptak_id),
    mote_id UUID NOT NULL,
    -- HJEMMELEN, IKKE VALGFRI OG IKKE EN TEKST. En fremmednøkkel,
    -- slik at et opptak ALLTID kan spores til grunnlaget som gjorde
    -- det lovlig.
    hjemmel_id UUID NOT NULL,
    -- VARSLINGEN. Hvem som ble varslet, når, og av hvem.
    varslet_ts TIMESTAMPTZ NOT NULL,
    varslet_av TEXT NOT NULL CHECK (length(btrim(varslet_av)) > 0),
    varslede TEXT[] NOT NULL
        CONSTRAINT moteopptak_noen_ble_varslet
        CHECK (cardinality(varslede) > 0),
    startet_ts TIMESTAMPTZ NOT NULL,
    -- DEN VIKTIGSTE LINJEN I FILA.
    CONSTRAINT moteopptak_varsling_kom_forst
        CHECK (varslet_ts <= startet_ts),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (length(btrim(registrert_av)) > 0),
    CONSTRAINT moteopptak_mote_fk FOREIGN KEY (tenant, mote_id)
        REFERENCES mote (tenant, mote_id),
    CONSTRAINT moteopptak_hjemmel_fk FOREIGN KEY (tenant, hjemmel_id)
        REFERENCES opptakshjemmel (tenant, hjemmel_id)
);

CREATE INDEX moteopptak_pr_mote ON moteopptak (tenant, mote_id);

-- ---------------------------------------------------------------------
-- REFERATPUNKTET — DER `usikkerhet_skjult` STOPPES.
--
-- `sikkerhet_bp` OG `ubekreftet` ER BEGGE `NOT NULL`, og det er ikke
-- dobbeltføring. Tallet er hva maskinen mente; flagget er hva TERSKELEN
-- gjorde med det. Terskelen er tenantens og kan endres — men et punkt
-- som ble merket ubekreftet DEN GANG, skal fortsatt stå som ubekreftet
-- når noen leser referatet et halvt år senere.
--
-- Derfor bæres `terskel_bp` med på raden: uten den kan «hvorfor er
-- dette merket?» ikke besvares etter at grensen er justert. Samme form
-- som `kravversjon` i klynge 7, og `modellversjon` i klynge 8.
--
-- `kilde` ER ET LUKKET SETT. Et referatpunkt uten kilde er en påstand
-- om hva som ble sagt, og den som skal svare for det må finne hva det
-- hviler på. `manuell` ER en gyldig kilde — et menneske som skriver
-- selv, er den beste kilden som finnes. Det som ikke er gyldig, er
-- INGEN kilde.
--
-- APPEND-ONLY. Et referat er en gjengivelse avgitt på et tidspunkt.
-- RETTELSER GJØRES SOM NYE PUNKTER som peker på det de retter — ikke
-- ved å skrive om det som sto der, for da ville «hva sto i referatet
-- da vi vedtok dette?» vært ubesvarlig.
-- ---------------------------------------------------------------------
CREATE TABLE referatpunkt (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    punkt_id UUID NOT NULL,
    PRIMARY KEY (tenant, punkt_id),
    mote_id UUID NOT NULL,
    -- Rekkefølgen i referatet. Ikke unik: to punkter kan gjelde samme
    -- sted hvis det ene retter det andre.
    rekkefolge INT NOT NULL CHECK (rekkefolge > 0),
    tekst TEXT NOT NULL CHECK (length(btrim(tekst)) > 0),
    kilde TEXT NOT NULL
        CONSTRAINT referatpunkt_kilde_lukket
        CHECK (kilde IN ('opptak', 'manuell', 'agenda')),
    -- HVA I KILDEN. For et opptak: hvilket opptak. For agenda: hvilket
    -- punkt. For manuell: hvem som skrev.
    kilde_ref TEXT NOT NULL CHECK (kilde_ref ~ '[^[:space:]]'),
    sikkerhet_bp INT NOT NULL
        CHECK (sikkerhet_bp BETWEEN 0 AND 10000),
    terskel_bp INT NOT NULL CHECK (terskel_bp BETWEEN 1 AND 10000),
    ubekreftet BOOLEAN NOT NULL,
    -- FLAGGET KAN IKKE LYVE OM SITT EGET TALL. Uten denne kunne en rad
    -- si «bekreftet» på 20 % sikkerhet — og det er nøyaktig løgnen
    -- vaktsetningen forbyr.
    CONSTRAINT referatpunkt_flagget_stemmer CHECK (
        ubekreftet = (sikkerhet_bp < terskel_bp)),
    -- ET MANUELT PUNKT ER ALLTID SIKKERT. Et menneske som skriver selv
    -- er ikke 60 % sikker på hva det selv mente.
    CONSTRAINT referatpunkt_manuell_er_sikker CHECK (
        kilde <> 'manuell' OR sikkerhet_bp = 10000),
    -- RETTELSER PEKER PÅ DET DE RETTER.
    retter_punkt_id UUID,
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (length(btrim(registrert_av)) > 0),
    CONSTRAINT referatpunkt_mote_fk FOREIGN KEY (tenant, mote_id)
        REFERENCES mote (tenant, mote_id),
    CONSTRAINT referatpunkt_retter_fk
        FOREIGN KEY (tenant, retter_punkt_id)
        REFERENCES referatpunkt (tenant, punkt_id)
);

CREATE INDEX referatpunkt_pr_mote
    ON referatpunkt (tenant, mote_id, rekkefolge);
CREATE INDEX referatpunkt_ubekreftede
    ON referatpunkt (tenant, mote_id) WHERE ubekreftet;

-- ---------------------------------------------------------------------
-- BESLUTNINGEN — V1-DOMMEN, GJORT UREPRESENTERBAR.
--
-- `besluttet_av` ER `NOT NULL` OG IKKE-TOM. En beslutning uten et
-- menneske bak er ikke en beslutning modulen skrev ned — det er en
-- beslutning modulen FATTET, og det er nettopp det den ikke gjør.
--
-- APPEND-ONLY, av samme grunn som referatpunktet.
-- ---------------------------------------------------------------------
CREATE TABLE motebeslutning (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    beslutning_id UUID NOT NULL,
    PRIMARY KEY (tenant, beslutning_id),
    mote_id UUID NOT NULL,
    tekst TEXT NOT NULL CHECK (length(btrim(tekst)) >= 8),
    -- ET NAVN, IKKE ET FLAGG.
    besluttet_av TEXT NOT NULL
        CHECK (length(btrim(besluttet_av)) > 0),
    besluttet_ts TIMESTAMPTZ NOT NULL,
    -- HVILKET REFERATPUNKT BESLUTNINGEN HVILER PÅ. Valgfritt, fordi
    -- en beslutning kan være ført direkte — men da må `kilde` si det.
    punkt_id UUID,
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (length(btrim(registrert_av)) > 0),
    CONSTRAINT motebeslutning_mote_fk FOREIGN KEY (tenant, mote_id)
        REFERENCES mote (tenant, mote_id),
    CONSTRAINT motebeslutning_punkt_fk FOREIGN KEY (tenant, punkt_id)
        REFERENCES referatpunkt (tenant, punkt_id)
);

CREATE INDEX motebeslutning_pr_mote
    ON motebeslutning (tenant, mote_id);

-- ---------------------------------------------------------------------
-- AKSJONEN — `aksjon_uten_eier`, GJORT UMULIG.
--
-- En aksjon uten eier er en aksjon ingen gjør. `eier` er `NOT NULL` og
-- ikke-tom, og det er hele invarianten: sveipen kan ikke reise
-- `aksjon_uten_eier`, og AT DEN IKKE KAN er beviset på at vernet
-- ligger i datamodellen.
--
-- STATUS FÅR ENDRES — det er den eneste tabellen i modulen der noe
-- kan skrives om, og bare der. En aksjon som ikke kunne lukkes ville
-- vært en liste som bare vokser.
-- ---------------------------------------------------------------------
CREATE TABLE moteaksjon (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    aksjon_id UUID NOT NULL,
    PRIMARY KEY (tenant, aksjon_id),
    mote_id UUID NOT NULL,
    tekst TEXT NOT NULL CHECK (length(btrim(tekst)) >= 8),
    eier TEXT NOT NULL CHECK (length(btrim(eier)) > 0),
    frist DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'apen'
        CONSTRAINT moteaksjon_status_lukket
        CHECK (status IN ('apen', 'utfort', 'henlagt')),
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT,
    lukkebegrunnelse TEXT,
    -- 125s DOM: en lukking uten et navn er urepresenterbar.
    CONSTRAINT moteaksjon_lukking_har_navn CHECK (
        status = 'apen'
        OR (lukket_ts IS NOT NULL AND lukket_av IS NOT NULL
            AND length(btrim(lukket_av)) > 0)),
    CONSTRAINT moteaksjon_apen_er_ulukket CHECK (
        status <> 'apen'
        OR (lukket_ts IS NULL AND lukket_av IS NULL)),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (length(btrim(opprettet_av)) > 0),
    CONSTRAINT moteaksjon_mote_fk FOREIGN KEY (tenant, mote_id)
        REFERENCES mote (tenant, mote_id)
);

CREATE INDEX moteaksjon_apne
    ON moteaksjon (tenant, frist) WHERE status = 'apen';

-- ---------------------------------------------------------------------
-- FUNNENE. Lukket sett, ett åpent funn per (tenant, funntype, ref).
-- ---------------------------------------------------------------------
CREATE TABLE motefunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    funn_id UUID NOT NULL,
    PRIMARY KEY (tenant, funn_id),
    funntype TEXT NOT NULL
        CONSTRAINT motefunn_type_lukket CHECK (funntype IN (
            'mote_uten_referat',
            'aksjon_over_frist',
            'ubekreftet_punkt_uavklart',
            'opptak_uten_hjemmel')),
    referanse TEXT NOT NULL CHECK (referanse ~ '[^[:space:]]'),
    detaljer TEXT NOT NULL CHECK (detaljer ~ '[^[:space:]]'),
    over_grense BIGINT NOT NULL DEFAULT 0,
    apen BOOLEAN NOT NULL DEFAULT true,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT,
    lukket_begrunnelse TEXT,
    CONSTRAINT motefunn_lukking_har_navn CHECK (
        apen
        OR (lukket_ts IS NOT NULL AND lukket_av IS NOT NULL
            AND length(btrim(lukket_av)) > 0)),
    CONSTRAINT motefunn_apen_er_ulukket CHECK (
        NOT apen OR (lukket_ts IS NULL AND lukket_av IS NULL))
);

CREATE UNIQUE INDEX motefunn_ett_apent
    ON motefunn (tenant, funntype, referanse) WHERE apen;

-- =====================================================================
-- APPEND-ONLY-VAKTENE. Gjelder også migrator (130s dom).
-- =====================================================================
CREATE OR REPLACE FUNCTION m7_evidensvakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION '%: % avvist — et referat, et opptak eller en'
        ' beslutning som kan endres i ettertid er ikke evidens',
        TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE ALL ON FUNCTION m7_evidensvakt() FROM PUBLIC;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['moteopptak', 'referatpunkt',
                             'motebeslutning'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS m7_evidensvakt'
                       ' ON public.%I', t);
        EXECUTE format('CREATE TRIGGER m7_evidensvakt'
                       ' BEFORE UPDATE OR DELETE ON public.%I'
                       ' FOR EACH ROW'
                       ' EXECUTE FUNCTION public.m7_evidensvakt()', t);
        EXECUTE format('DROP TRIGGER IF EXISTS m7_ingen_truncate'
                       ' ON public.%I', t);
        EXECUTE format('CREATE TRIGGER m7_ingen_truncate'
                       ' BEFORE TRUNCATE ON public.%I'
                       ' FOR EACH STATEMENT'
                       ' EXECUTE FUNCTION public.avvis_endring()', t);
    END LOOP;
END $$;

-- HJEMMELENS IDENTITET ER FROSSET; BARE `gyldig_til` KAN SETTES.
CREATE OR REPLACE FUNCTION m7_hjemmelvakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'opptakshjemmel: sletting avvist — et opptak'
            ' som peker på hjemmelen kan ikke miste grunnlaget sitt'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.grunnlagstype IS DISTINCT FROM OLD.grunnlagstype
       OR NEW.beskrivelse IS DISTINCT FROM OLD.beskrivelse
       OR NEW.formal IS DISTINCT FROM OLD.formal
       OR NEW.gyldig_fra IS DISTINCT FROM OLD.gyldig_fra
       OR NEW.registrert IS DISTINCT FROM OLD.registrert
       OR NEW.registrert_av IS DISTINCT FROM OLD.registrert_av THEN
        RAISE EXCEPTION 'opptakshjemmel: grunnlaget er frosset — bare'
            ' gyldig_til kan settes. Kunne det endres, ville «hva'
            ' hvilte opptaket på?» vært et oppslag i noe som har'
            ' endret seg siden'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.gyldig_til IS NOT NULL
       AND NEW.gyldig_til IS DISTINCT FROM OLD.gyldig_til THEN
        RAISE EXCEPTION 'opptakshjemmel: en avsluttet hjemmel kan ikke'
            ' gjenoppvekkes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m7_hjemmelvakt() FROM PUBLIC;
DROP TRIGGER IF EXISTS m7_hjemmelvakt ON opptakshjemmel;
CREATE TRIGGER m7_hjemmelvakt
    BEFORE UPDATE OR DELETE ON opptakshjemmel
    FOR EACH ROW EXECUTE FUNCTION m7_hjemmelvakt();

-- MØTETS IDENTITET ER FROSSET. Et møte som kunne flyttes i ettertid
-- ville gjort referatfristen til noe som alltid kan overholdes.
CREATE OR REPLACE FUNCTION m7_motevakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'mote: sletting avvist — et møte med referat'
            ' kan ikke forsvinne'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RAISE EXCEPTION 'mote: endring avvist — et møte som kunne flyttes'
        ' i ettertid ville gjort referatfristen til noe som alltid kan'
        ' overholdes'
        USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE ALL ON FUNCTION m7_motevakt() FROM PUBLIC;
DROP TRIGGER IF EXISTS m7_motevakt ON mote;
CREATE TRIGGER m7_motevakt
    BEFORE UPDATE OR DELETE ON mote
    FOR EACH ROW EXECUTE FUNCTION m7_motevakt();

-- AKSJONEN FÅR BARE LUKKES, OG BARE ÉN GANG.
CREATE OR REPLACE FUNCTION m7_aksjonsvakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'moteaksjon: sletting avvist — en aksjon som'
            ' kan slettes er en aksjon ingen kan bevise sto'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tekst IS DISTINCT FROM OLD.tekst
       OR NEW.eier IS DISTINCT FROM OLD.eier
       OR NEW.frist IS DISTINCT FROM OLD.frist
       OR NEW.mote_id IS DISTINCT FROM OLD.mote_id
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av THEN
        RAISE EXCEPTION 'moteaksjon: aksjonens innhold er frosset —'
            ' bare status kan settes. En frist som kunne flyttes er'
            ' ikke en frist'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status <> 'apen' AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'moteaksjon: % er alt lukket som %',
            OLD.aksjon_id, OLD.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m7_aksjonsvakt() FROM PUBLIC;
DROP TRIGGER IF EXISTS m7_aksjonsvakt ON moteaksjon;
CREATE TRIGGER m7_aksjonsvakt
    BEFORE UPDATE OR DELETE ON moteaksjon
    FOR EACH ROW EXECUTE FUNCTION m7_aksjonsvakt();

-- =====================================================================
-- RADVAKTEN. `FORCE`, ellers er eieren unntatt og
-- `tenantlekkasje_i_moteregister` en invariant uten håndhevelse.
-- =====================================================================
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['motekrav', 'opptakshjemmel', 'mote',
                             'moteopptak', 'referatpunkt',
                             'motebeslutning', 'moteaksjon',
                             'motefunn'] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL'
                       ' SECURITY', t);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL'
                       ' SECURITY', t);
        IF NOT EXISTS (SELECT 1 FROM pg_policy
                        WHERE polrelid = format('public.%I', t)::regclass
                          AND polname = 'tenant_isolasjon') THEN
            EXECUTE format(
                'CREATE POLICY tenant_isolasjon ON public.%I'
                ' USING      (tenant = current_setting(''disponit.tenant'', true))'
                ' WITH CHECK (tenant = current_setting(''disponit.tenant'', true))',
                t);
        END IF;
        -- Dørene er SECURITY DEFINER og løper som modulrollen (130s
        -- lærdom fra riggen).
        EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO'
                       ' disponit_mote_eier', t);
    END LOOP;
END $$;

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER (111s form).
CREATE POLICY m7_sveip_tenantliste ON motekrav
    FOR SELECT TO disponit_mote_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- HISTORIKKTABELLENE FÅR IKKE UPDATE. En rettighet som ikke finnes er
-- sterkere enn en trigger som nekter.
REVOKE UPDATE ON public.moteopptak FROM disponit_mote_eier;
REVOKE UPDATE ON public.referatpunkt FROM disponit_mote_eier;
REVOKE UPDATE ON public.motebeslutning FROM disponit_mote_eier;
REVOKE UPDATE ON public.mote FROM disponit_mote_eier;

-- `opptakshjemmel` FÅR BARE AVSLUTTES.
REVOKE UPDATE ON public.opptakshjemmel FROM disponit_mote_eier;
GRANT UPDATE (gyldig_til) ON public.opptakshjemmel
    TO disponit_mote_eier;

-- `moteaksjon` FÅR BARE LUKKES.
REVOKE UPDATE ON public.moteaksjon FROM disponit_mote_eier;
GRANT UPDATE (status, lukket_ts, lukket_av, lukkebegrunnelse)
    ON public.moteaksjon TO disponit_mote_eier;

-- INGEN AV TABELLENE FÅR SLETTES. `DELETE` står ikke i noen GRANT
-- over — lista er `SELECT, INSERT, UPDATE`. Det står her fordi et
-- FRAVÆR er lettere å overse enn en setning, og porten leser begge.

-- =====================================================================
-- HERFRA EIES DØRENE AV MØTEEIEREN.
-- =====================================================================
SET LOCAL ROLE disponit_mote_eier;

-- FUNNENE INGEN KAN LUKKE, SOM EN FUNKSJON OG IKKE EN HUSKEREGEL.
CREATE FUNCTION m7_funn_er_sveipens(p_funntype TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT p_funntype IN ('mote_uten_referat', 'aksjon_over_frist')
$$;
REVOKE ALL ON FUNCTION m7_funn_er_sveipens(TEXT) FROM PUBLIC;

-- STABLE, IKKE IMMUTABLE (125s lærdom).
CREATE FUNCTION m7_hjemmel_gyldig(p_fra DATE, p_til DATE)
RETURNS BOOLEAN LANGUAGE sql STABLE
SET search_path = pg_catalog AS $$
    SELECT p_fra <= current_date
       AND (p_til IS NULL OR p_til >= current_date)
$$;
REVOKE ALL ON FUNCTION m7_hjemmel_gyldig(DATE, DATE) FROM PUBLIC;

-- EVIDENSKJEDEN. `input_hash` er sha256 over den KANONISKE
-- BESKRIVELSEN AV HANDLINGEN, ikke over møteinnholdet: en evidenskjede
-- som arkiverte referattekster ville vært et nytt sted
-- personopplysninger lå lagret, og et som aldri kan rettes.
CREATE FUNCTION m7_evidens(p_tenant TEXT, p_ref UUID,
                           p_handling TEXT, p_aktor TEXT,
                           p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm7_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm07_moteoperasjon', 'handling', p_handling,
        'ref', p_ref::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm07_moteoperasjon',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:moteoperasjon', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m7_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

CREATE FUNCTION m7_sett_krav(p_tenant TEXT, p_referatfrist INT,
                             p_aksjonsfrist INT, p_terskel INT,
                             p_aktor TEXT, p_nokkel TEXT)
RETURNS TABLE (referatfrist_dogn INT, aksjonsfrist_dogn INT,
               sikkerhetsterskel_bp INT, versjon INT, endret BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_rad public.motekrav%ROWTYPE;
    v_endret BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm7_sett_krav');
    IF p_nokkel IS NULL OR btrim(p_nokkel) = '' THEN
        RAISE EXCEPTION 'm7_sett_krav: idempotensnøkkel mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- LÅS FØRST, LES ETTERPÅ (klynge 6s lærdom).
    SELECT * INTO v_rad FROM public.motekrav
     WHERE tenant = p_tenant FOR UPDATE;
    IF FOUND AND v_rad.siste_nokkel IS NOT DISTINCT FROM p_nokkel THEN
        RETURN QUERY SELECT v_rad.referatfrist_dogn,
                            v_rad.aksjonsfrist_dogn,
                            v_rad.sikkerhetsterskel_bp,
                            v_rad.versjon, false;
        RETURN;
    END IF;
    v_endret := NOT FOUND
        OR v_rad.referatfrist_dogn IS DISTINCT FROM p_referatfrist
        OR v_rad.aksjonsfrist_dogn IS DISTINCT FROM p_aksjonsfrist
        OR v_rad.sikkerhetsterskel_bp IS DISTINCT FROM p_terskel;
    INSERT INTO public.motekrav
        (tenant, referatfrist_dogn, aksjonsfrist_dogn,
         sikkerhetsterskel_bp, versjon, siste_nokkel, satt_av)
    VALUES (p_tenant, p_referatfrist, p_aksjonsfrist, p_terskel, 1,
            p_nokkel, p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        referatfrist_dogn = EXCLUDED.referatfrist_dogn,
        aksjonsfrist_dogn = EXCLUDED.aksjonsfrist_dogn,
        sikkerhetsterskel_bp = EXCLUDED.sikkerhetsterskel_bp,
        -- VERSJONEN ØKER BARE VED EN EKTE ENDRING (119s lærdom).
        versjon = public.motekrav.versjon
                  + CASE WHEN v_endret THEN 1 ELSE 0 END,
        siste_nokkel = EXCLUDED.siste_nokkel,
        satt_ts = now(), satt_av = EXCLUDED.satt_av
    RETURNING * INTO v_rad;
    PERFORM public.m7_evidens(p_tenant, NULL, 'sett_krav', p_aktor,
        jsonb_build_object('versjon', v_rad.versjon,
                           'endret', v_endret));
    RETURN QUERY SELECT v_rad.referatfrist_dogn,
                        v_rad.aksjonsfrist_dogn,
                        v_rad.sikkerhetsterskel_bp, v_rad.versjon,
                        v_endret;
END $$;
REVOKE ALL ON FUNCTION m7_sett_krav(TEXT, INT, INT, INT, TEXT, TEXT)
    FROM PUBLIC;

-- HJEMMELSDØRA. Gjenspill fra fødselen (131s lærdom).
CREATE FUNCTION m7_registrer_hjemmel(
    p_tenant TEXT, p_hjemmel_id UUID, p_grunnlagstype TEXT,
    p_beskrivelse TEXT, p_formal TEXT, p_gyldig_fra DATE,
    p_gyldig_til DATE, p_aktor TEXT)
RETURNS TABLE (hjemmel_id UUID, gjelder BOOLEAN, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.opptakshjemmel%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm7_registrer_hjemmel');
    SELECT * INTO v_rad FROM public.opptakshjemmel
     WHERE tenant = p_tenant
       AND opptakshjemmel.hjemmel_id = p_hjemmel_id;
    IF FOUND THEN
        IF v_rad.grunnlagstype IS DISTINCT FROM p_grunnlagstype THEN
            RAISE EXCEPTION 'm7_registrer_hjemmel: % finnes med et'
                ' annet grunnlag (%)', p_hjemmel_id,
                v_rad.grunnlagstype
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN QUERY SELECT v_rad.hjemmel_id,
            public.m7_hjemmel_gyldig(v_rad.gyldig_fra,
                                     v_rad.gyldig_til), false;
        RETURN;
    END IF;
    INSERT INTO public.opptakshjemmel
        (tenant, hjemmel_id, grunnlagstype, beskrivelse, formal,
         gyldig_fra, gyldig_til, registrert_av)
    VALUES (p_tenant, p_hjemmel_id, p_grunnlagstype, p_beskrivelse,
            p_formal, p_gyldig_fra, p_gyldig_til, p_aktor)
    RETURNING * INTO v_rad;
    PERFORM public.m7_evidens(p_tenant, p_hjemmel_id,
        'registrer_hjemmel', p_aktor,
        jsonb_build_object('grunnlagstype', p_grunnlagstype,
                           'formal', p_formal));
    RETURN QUERY SELECT v_rad.hjemmel_id,
        public.m7_hjemmel_gyldig(v_rad.gyldig_fra, v_rad.gyldig_til),
        true;
END $$;
REVOKE ALL ON FUNCTION m7_registrer_hjemmel(TEXT, UUID, TEXT, TEXT,
    TEXT, DATE, DATE, TEXT) FROM PUBLIC;

CREATE FUNCTION m7_avslutt_hjemmel(p_tenant TEXT, p_hjemmel_id UUID,
                                   p_gyldig_til DATE, p_aktor TEXT)
RETURNS TABLE (hjemmel_id UUID, gyldig_til DATE, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.opptakshjemmel%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm7_avslutt_hjemmel');
    SELECT * INTO v_rad FROM public.opptakshjemmel
     WHERE tenant = p_tenant
       AND opptakshjemmel.hjemmel_id = p_hjemmel_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm7_avslutt_hjemmel: hjemmelen finnes ikke'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_rad.gyldig_til IS NOT NULL THEN
        IF v_rad.gyldig_til IS DISTINCT FROM p_gyldig_til THEN
            RAISE EXCEPTION 'm7_avslutt_hjemmel: hjemmelen er alt'
                ' avsluttet per %', v_rad.gyldig_til
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN QUERY SELECT v_rad.hjemmel_id, v_rad.gyldig_til, false;
        RETURN;
    END IF;
    UPDATE public.opptakshjemmel SET gyldig_til = p_gyldig_til
     WHERE tenant = p_tenant
       AND opptakshjemmel.hjemmel_id = p_hjemmel_id
    RETURNING * INTO v_rad;
    PERFORM public.m7_evidens(p_tenant, p_hjemmel_id,
        'avslutt_hjemmel', p_aktor,
        jsonb_build_object('gyldig_til', p_gyldig_til));
    RETURN QUERY SELECT v_rad.hjemmel_id, v_rad.gyldig_til, true;
END $$;
REVOKE ALL ON FUNCTION m7_avslutt_hjemmel(TEXT, UUID, DATE, TEXT)
    FROM PUBLIC;

CREATE FUNCTION m7_registrer_mote(
    p_tenant TEXT, p_mote_id UUID, p_tittel TEXT,
    p_start TIMESTAMPTZ, p_slutt TIMESTAMPTZ, p_innkalt_av TEXT,
    p_deltakere TEXT[], p_agenda TEXT, p_aktor TEXT)
RETURNS TABLE (mote_id UUID, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.mote%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm7_registrer_mote');
    SELECT * INTO v_rad FROM public.mote
     WHERE tenant = p_tenant AND mote.mote_id = p_mote_id;
    IF FOUND THEN
        IF v_rad.start_ts IS DISTINCT FROM p_start THEN
            RAISE EXCEPTION 'm7_registrer_mote: % finnes med et annet'
                ' tidspunkt (%)', p_mote_id, v_rad.start_ts
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN QUERY SELECT v_rad.mote_id, false;
        RETURN;
    END IF;
    -- EN TOM DELTAKER ER IKKE EN DELTAKER. `cardinality > 0` fanger
    -- den tomme LISTA; dette fanger lista med tomme strenger i.
    IF EXISTS (SELECT 1 FROM unnest(p_deltakere) d
                WHERE d IS NULL OR btrim(d) = '') THEN
        RAISE EXCEPTION 'm7_registrer_mote: en navnløs deltaker er'
            ' ikke en deltaker'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.mote
        (tenant, mote_id, tittel, start_ts, slutt_ts, innkalt_av,
         deltakere, agenda, opprettet_av)
    VALUES (p_tenant, p_mote_id, p_tittel, p_start, p_slutt,
            p_innkalt_av, p_deltakere, p_agenda, p_aktor)
    RETURNING * INTO v_rad;
    PERFORM public.m7_evidens(p_tenant, p_mote_id, 'registrer_mote',
        p_aktor, jsonb_build_object(
            'deltakere', cardinality(p_deltakere)));
    RETURN QUERY SELECT v_rad.mote_id, true;
END $$;
REVOKE ALL ON FUNCTION m7_registrer_mote(TEXT, UUID, TEXT,
    TIMESTAMPTZ, TIMESTAMPTZ, TEXT, TEXT[], TEXT, TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- OPPTAKSDØRA — MODULENS VIKTIGSTE FUNKSJON.
--
-- ET NEKT SOM KOMMER ETTER MIKROFONEN ER IKKE ET NEKT.
--
-- Døra nekter på fire ting, og rekkefølgen er valgt slik at den
-- billigste og mest opplysende feilen kommer først:
--
--   1. HJEMMELEN FINNES IKKE. Da er det ingenting å diskutere.
--   2. HJEMMELEN GJELDER IKKE I DAG. En utløpt hjemmel ser nøyaktig
--      ut som en gyldig — klynge 7s dom, og den gjelder her også.
--   3. VARSLINGEN MANGLER ELLER ER TOM.
--   4. VARSLINGEN KOM ETTER. Dette er den siste sperren, og CHECKen i
--      tabellen tar den også — to lag, fordi et opptak er den ene
--      handlingen i modulen som ikke kan gjøres ugjort.
--
-- `p_startet` OPPGIS AV KALLEREN OG SETTES IKKE TIL `now()`. Det er et
-- bevisst valg: opptaket startet da det startet, ikke da noen rakk å
-- registrere det. Men da må døra sjekke at det ikke ligger i
-- FRAMTIDEN — et opptak registrert på forskudd ville gjort
-- varslingskravet trivielt å oppfylle.
-- ---------------------------------------------------------------------
CREATE FUNCTION m7_start_opptak(
    p_tenant TEXT, p_opptak_id UUID, p_mote_id UUID,
    p_hjemmel_id UUID, p_varslet_ts TIMESTAMPTZ, p_varslet_av TEXT,
    p_varslede TEXT[], p_startet TIMESTAMPTZ, p_aktor TEXT)
RETURNS TABLE (opptak_id UUID, grunnlagstype TEXT, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_hjemmel public.opptakshjemmel%ROWTYPE;
    v_rad public.moteopptak%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm7_start_opptak');

    -- GJENSPILL FØRST (SP-2). Et opptak er append-only, så et
    -- gjenspill kan ikke skrive på nytt — det må svare med raden.
    SELECT * INTO v_rad FROM public.moteopptak
     WHERE tenant = p_tenant AND moteopptak.opptak_id = p_opptak_id;
    IF FOUND THEN
        SELECT * INTO v_hjemmel FROM public.opptakshjemmel
         WHERE tenant = p_tenant
           AND hjemmel_id = v_rad.hjemmel_id;
        RETURN QUERY SELECT v_rad.opptak_id, v_hjemmel.grunnlagstype,
                            false;
        RETURN;
    END IF;

    -- 1. HJEMMELEN FINNES.
    SELECT * INTO v_hjemmel FROM public.opptakshjemmel
     WHERE tenant = p_tenant
       AND opptakshjemmel.hjemmel_id = p_hjemmel_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm7_start_opptak: hjemmelen finnes ikke — et'
            ' opptak uten grunnlag er ulovlig i det øyeblikket det'
            ' starter'
            USING ERRCODE = 'no_data_found';
    END IF;

    -- 2. HJEMMELEN GJELDER I DAG. En utløpt hjemmel ser nøyaktig ut
    --    som en gyldig (klynge 7s dom).
    IF NOT public.m7_hjemmel_gyldig(v_hjemmel.gyldig_fra,
                                    v_hjemmel.gyldig_til) THEN
        RAISE EXCEPTION 'm7_start_opptak: hjemmelen gjaldt til %, og'
            ' en utløpt hjemmel ser nøyaktig ut som en gyldig',
            v_hjemmel.gyldig_til
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- 3. NOEN BLE VARSLET, MED NAVN.
    IF p_varslet_av IS NULL OR btrim(p_varslet_av) = '' THEN
        RAISE EXCEPTION 'm7_start_opptak: en varsling uten et navn på'
            ' er ikke en varsling'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_varslede IS NULL OR cardinality(p_varslede) = 0
       OR EXISTS (SELECT 1 FROM unnest(p_varslede) v
                   WHERE v IS NULL OR btrim(v) = '') THEN
        RAISE EXCEPTION 'm7_start_opptak: ingen er varslet — «alle ble'
            ' varslet» er ikke en liste'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- 4. VARSLINGEN KOM FØRST. Rekkefølgen ER regelen.
    IF p_varslet_ts > p_startet THEN
        RAISE EXCEPTION 'm7_start_opptak: varslingen (%) kom etter at'
            ' opptaket startet (%) — en varsling registrert i'
            ' etterkant er ikke en varsling, det er en unnskyldning',
            p_varslet_ts, p_startet
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- …OG OPPTAKET LIGGER IKKE I FRAMTIDEN. Uten dette kunne
    -- varslingskravet oppfylles ved å datere opptaket fram i tid.
    IF p_startet > now() THEN
        RAISE EXCEPTION 'm7_start_opptak: opptaket er datert fram i'
            ' tid (%) — da ville varslingskravet vært trivielt å'
            ' oppfylle', p_startet
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    INSERT INTO public.moteopptak
        (tenant, opptak_id, mote_id, hjemmel_id, varslet_ts,
         varslet_av, varslede, startet_ts, registrert_av)
    VALUES (p_tenant, p_opptak_id, p_mote_id, p_hjemmel_id,
            p_varslet_ts, p_varslet_av, p_varslede, p_startet, p_aktor)
    RETURNING * INTO v_rad;

    PERFORM public.m7_evidens(p_tenant, p_opptak_id, 'start_opptak',
        p_aktor, jsonb_build_object(
            'grunnlagstype', v_hjemmel.grunnlagstype,
            'varslede', cardinality(p_varslede)));
    RETURN QUERY SELECT v_rad.opptak_id, v_hjemmel.grunnlagstype, true;
END $$;
REVOKE ALL ON FUNCTION m7_start_opptak(TEXT, UUID, UUID, UUID,
    TIMESTAMPTZ, TEXT, TEXT[], TIMESTAMPTZ, TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- REFERATDØRA — DER `usikkerhet_skjult` OG `referat_uten_kilde`
-- STOPPES.
--
-- TERSKELEN LESES FRA TENANTENS KRAV OG SKRIVES PÅ RADEN. Kalleren
-- oppgir den ikke: en kaller som fikk sette sin egen terskel kunne
-- sette den til 1 og få alt bekreftet.
--
-- `ubekreftet` REGNES AV DØRA, ikke av kalleren, av samme grunn.
-- CHECKen i tabellen fanger det uansett — to lag, fordi et referat
-- som skjuler usikkerhet er en påstand om at maskinen var sikker.
-- ---------------------------------------------------------------------
CREATE FUNCTION m7_registrer_referatpunkt(
    p_tenant TEXT, p_punkt_id UUID, p_mote_id UUID, p_rekkefolge INT,
    p_tekst TEXT, p_kilde TEXT, p_kilde_ref TEXT, p_sikkerhet_bp INT,
    p_retter UUID, p_aktor TEXT)
RETURNS TABLE (punkt_id UUID, ubekreftet BOOLEAN, terskel_bp INT,
               ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_krav public.motekrav%ROWTYPE;
    v_rad public.referatpunkt%ROWTYPE;
    v_sikkerhet INT := p_sikkerhet_bp;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm7_registrer_referatpunkt');
    SELECT * INTO v_rad FROM public.referatpunkt
     WHERE tenant = p_tenant AND referatpunkt.punkt_id = p_punkt_id;
    IF FOUND THEN
        IF v_rad.tekst IS DISTINCT FROM p_tekst THEN
            RAISE EXCEPTION 'm7_registrer_referatpunkt: % finnes med'
                ' en annen tekst', p_punkt_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN QUERY SELECT v_rad.punkt_id, v_rad.ubekreftet,
                            v_rad.terskel_bp, false;
        RETURN;
    END IF;

    SELECT * INTO v_krav FROM public.motekrav WHERE tenant = p_tenant;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm7_registrer_referatpunkt: tenanten har ingen'
            ' registrert sikkerhetsterskel — uten den kan ingenting'
            ' merkes ubekreftet, og da er merkingen en tilfeldighet'
            USING ERRCODE = 'no_data_found';
    END IF;

    -- ET MANUELT PUNKT ER ALLTID SIKKERT. Et menneske som skriver
    -- selv er ikke 60 % sikker på hva det selv mente, og CHECKen i
    -- tabellen krever det — døra setter det i stedet for å avvise
    -- kalleren for noe som ikke er en feil.
    IF p_kilde = 'manuell' THEN
        v_sikkerhet := 10000;
    END IF;

    -- ET OPPTAKSPUNKT MÅ PEKE PÅ ET OPPTAK SOM FINNES. Uten dette
    -- kunne `kilde_ref` vært hva som helst, og «hva hviler dette på?»
    -- ubesvarlig.
    IF p_kilde = 'opptak'
       AND NOT EXISTS (SELECT 1 FROM public.moteopptak o
                        WHERE o.tenant = p_tenant
                          AND o.opptak_id::text = p_kilde_ref) THEN
        RAISE EXCEPTION 'm7_registrer_referatpunkt: kilde_ref % er'
            ' ikke et opptak som finnes', p_kilde_ref
            USING ERRCODE = 'no_data_found';
    END IF;

    INSERT INTO public.referatpunkt
        (tenant, punkt_id, mote_id, rekkefolge, tekst, kilde,
         kilde_ref, sikkerhet_bp, terskel_bp, ubekreftet,
         retter_punkt_id, registrert_av)
    VALUES (p_tenant, p_punkt_id, p_mote_id, p_rekkefolge, p_tekst,
            p_kilde, p_kilde_ref, v_sikkerhet,
            v_krav.sikkerhetsterskel_bp,
            v_sikkerhet < v_krav.sikkerhetsterskel_bp,
            p_retter, p_aktor)
    RETURNING * INTO v_rad;

    PERFORM public.m7_evidens(p_tenant, p_punkt_id,
        'registrer_referatpunkt', p_aktor,
        jsonb_build_object('kilde', p_kilde,
                           'ubekreftet', v_rad.ubekreftet));
    RETURN QUERY SELECT v_rad.punkt_id, v_rad.ubekreftet,
                        v_rad.terskel_bp, true;
END $$;
REVOKE ALL ON FUNCTION m7_registrer_referatpunkt(TEXT, UUID, UUID,
    INT, TEXT, TEXT, TEXT, INT, UUID, TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- BESLUTNINGSDØRA — V1-DOMMEN.
--
-- `p_besluttet_av` MÅ VÆRE ET NAVN. Døra nekter på tomt, og kolonnen
-- er `NOT NULL` — to lag, fordi en beslutning uten et menneske bak er
-- en beslutning MODULEN fattet.
-- ---------------------------------------------------------------------
CREATE FUNCTION m7_registrer_beslutning(
    p_tenant TEXT, p_beslutning_id UUID, p_mote_id UUID, p_tekst TEXT,
    p_besluttet_av TEXT, p_besluttet_ts TIMESTAMPTZ, p_punkt_id UUID,
    p_aktor TEXT)
RETURNS TABLE (beslutning_id UUID, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.motebeslutning%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm7_registrer_beslutning');
    SELECT * INTO v_rad FROM public.motebeslutning
     WHERE tenant = p_tenant
       AND motebeslutning.beslutning_id = p_beslutning_id;
    IF FOUND THEN
        RETURN QUERY SELECT v_rad.beslutning_id, false;
        RETURN;
    END IF;
    IF p_besluttet_av IS NULL OR btrim(p_besluttet_av) = '' THEN
        RAISE EXCEPTION 'm7_registrer_beslutning: en beslutning uten'
            ' et menneske bak er ikke en beslutning modulen skrev ned'
            ' — det er en beslutning modulen fattet'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.motebeslutning
        (tenant, beslutning_id, mote_id, tekst, besluttet_av,
         besluttet_ts, punkt_id, registrert_av)
    VALUES (p_tenant, p_beslutning_id, p_mote_id, p_tekst,
            p_besluttet_av, p_besluttet_ts, p_punkt_id, p_aktor)
    RETURNING * INTO v_rad;
    PERFORM public.m7_evidens(p_tenant, p_beslutning_id,
        'registrer_beslutning', p_aktor,
        jsonb_build_object('besluttet_av', p_besluttet_av));
    RETURN QUERY SELECT v_rad.beslutning_id, true;
END $$;
REVOKE ALL ON FUNCTION m7_registrer_beslutning(TEXT, UUID, UUID, TEXT,
    TEXT, TIMESTAMPTZ, UUID, TEXT) FROM PUBLIC;

CREATE FUNCTION m7_registrer_aksjon(
    p_tenant TEXT, p_aksjon_id UUID, p_mote_id UUID, p_tekst TEXT,
    p_eier TEXT, p_frist DATE, p_aktor TEXT)
RETURNS TABLE (aksjon_id UUID, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.moteaksjon%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm7_registrer_aksjon');
    SELECT * INTO v_rad FROM public.moteaksjon
     WHERE tenant = p_tenant AND moteaksjon.aksjon_id = p_aksjon_id;
    IF FOUND THEN
        RETURN QUERY SELECT v_rad.aksjon_id, false;
        RETURN;
    END IF;
    IF p_eier IS NULL OR btrim(p_eier) = '' THEN
        RAISE EXCEPTION 'm7_registrer_aksjon: en aksjon uten eier er'
            ' en aksjon ingen gjør'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.moteaksjon
        (tenant, aksjon_id, mote_id, tekst, eier, frist, opprettet_av)
    VALUES (p_tenant, p_aksjon_id, p_mote_id, p_tekst, p_eier,
            p_frist, p_aktor)
    RETURNING * INTO v_rad;
    PERFORM public.m7_evidens(p_tenant, p_aksjon_id,
        'registrer_aksjon', p_aktor,
        jsonb_build_object('eier', p_eier, 'frist', p_frist));
    RETURN QUERY SELECT v_rad.aksjon_id, true;
END $$;
REVOKE ALL ON FUNCTION m7_registrer_aksjon(TEXT, UUID, UUID, TEXT,
    TEXT, DATE, TEXT) FROM PUBLIC;

CREATE FUNCTION m7_lukk_aksjon(p_tenant TEXT, p_aksjon_id UUID,
                               p_status TEXT, p_begrunnelse TEXT,
                               p_aktor TEXT)
RETURNS TABLE (aksjon_id UUID, status TEXT, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.moteaksjon%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm7_lukk_aksjon');
    IF p_status NOT IN ('utfort', 'henlagt') THEN
        RAISE EXCEPTION 'm7_lukk_aksjon: % er ikke en lukking',
            p_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- 125s LÆRDOM: en lukking uten et navn er urepresenterbar.
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm7_lukk_aksjon: en lukking uten et navn på er'
            ' ikke en lukking'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO v_rad FROM public.moteaksjon
     WHERE tenant = p_tenant AND moteaksjon.aksjon_id = p_aksjon_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm7_lukk_aksjon: aksjonen finnes ikke'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_rad.status <> 'apen' THEN
        IF v_rad.status IS DISTINCT FROM p_status THEN
            RAISE EXCEPTION 'm7_lukk_aksjon: % er alt lukket som %',
                p_aksjon_id, v_rad.status
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN QUERY SELECT v_rad.aksjon_id, v_rad.status, false;
        RETURN;
    END IF;
    UPDATE public.moteaksjon
       SET status = p_status, lukket_ts = now(), lukket_av = p_aktor,
           lukkebegrunnelse = p_begrunnelse
     WHERE tenant = p_tenant AND moteaksjon.aksjon_id = p_aksjon_id
    RETURNING * INTO v_rad;
    PERFORM public.m7_evidens(p_tenant, p_aksjon_id, 'lukk_aksjon',
        p_aktor, jsonb_build_object('status', p_status));
    RETURN QUERY SELECT v_rad.aksjon_id, v_rad.status, true;
END $$;
REVOKE ALL ON FUNCTION m7_lukk_aksjon(TEXT, UUID, TEXT, TEXT, TEXT)
    FROM PUBLIC;

RESET ROLE;

-- =====================================================================
-- RETTIGHETENE (SP-7). Kjøretiden får EXECUTE på dørene og INGEN
-- tabellrettigheter.
-- =====================================================================
SET LOCAL ROLE disponit_mote_eier;
GRANT EXECUTE ON FUNCTION m7_sett_krav(TEXT, INT, INT, INT, TEXT,
    TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m7_registrer_hjemmel(TEXT, UUID, TEXT, TEXT,
    TEXT, DATE, DATE, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m7_avslutt_hjemmel(TEXT, UUID, DATE, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m7_registrer_mote(TEXT, UUID, TEXT,
    TIMESTAMPTZ, TIMESTAMPTZ, TEXT, TEXT[], TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m7_start_opptak(TEXT, UUID, UUID, UUID,
    TIMESTAMPTZ, TEXT, TEXT[], TIMESTAMPTZ, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m7_registrer_referatpunkt(TEXT, UUID, UUID,
    INT, TEXT, TEXT, TEXT, INT, UUID, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m7_registrer_beslutning(TEXT, UUID, UUID,
    TEXT, TEXT, TIMESTAMPTZ, UUID, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m7_registrer_aksjon(TEXT, UUID, UUID, TEXT,
    TEXT, DATE, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m7_lukk_aksjon(TEXT, UUID, TEXT, TEXT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m7_hjemmel_gyldig(DATE, DATE) TO disponit;
-- ---------------------------------------------------------------------
-- LESEDØRENE.
--
-- `m7_referatet` GIR ALDRI ET PUNKT UTEN `ubekreftet`. Det er
-- `usikkerhet_skjult` håndhevet der den faktisk kan brytes: i det som
-- forlater basen. En flate kan velge å ikke merke punktet, men den kan
-- ikke få et svar der merkingen mangler.
-- ---------------------------------------------------------------------
CREATE FUNCTION m7_referatet(p_tenant TEXT, p_mote_id UUID)
RETURNS TABLE (punkt_id UUID, rekkefolge INT, tekst TEXT,
               kilde TEXT, kilde_ref TEXT, sikkerhet_bp INT,
               terskel_bp INT, ubekreftet BOOLEAN,
               retter_punkt_id UUID, registrert TIMESTAMPTZ,
               registrert_av TEXT, er_rettet BOOLEAN)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT p.punkt_id, p.rekkefolge, p.tekst, p.kilde, p.kilde_ref,
           p.sikkerhet_bp, p.terskel_bp, p.ubekreftet,
           p.retter_punkt_id, p.registrert, p.registrert_av,
           -- ET RETTET PUNKT SKAL VÆRE SYNLIG SOM RETTET, ikke
           -- borte: referatet er append-only, og den som leser skal
           -- se at noe ble korrigert.
           EXISTS (SELECT 1 FROM public.referatpunkt r
                    WHERE r.tenant = p.tenant
                      AND r.retter_punkt_id = p.punkt_id)
      FROM public.referatpunkt p
     WHERE p.tenant = p_tenant AND p.mote_id = p_mote_id
     ORDER BY p.rekkefolge, p.registrert
$$;
REVOKE ALL ON FUNCTION m7_referatet(TEXT, UUID) FROM PUBLIC;

CREATE FUNCTION m7_moteregister(p_tenant TEXT, p_grense INT)
RETURNS TABLE (mote_id UUID, tittel TEXT, start_ts TIMESTAMPTZ,
               slutt_ts TIMESTAMPTZ, innkalt_av TEXT,
               antall_deltakere INT, antall_punkter INT,
               antall_ubekreftede INT, antall_beslutninger INT,
               antall_apne_aksjoner INT, har_opptak BOOLEAN,
               opptakshjemmel TEXT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT m.mote_id, m.tittel, m.start_ts, m.slutt_ts, m.innkalt_av,
           cardinality(m.deltakere),
           (SELECT count(*)::INT FROM public.referatpunkt p
             WHERE p.tenant = m.tenant AND p.mote_id = m.mote_id),
           (SELECT count(*)::INT FROM public.referatpunkt p
             WHERE p.tenant = m.tenant AND p.mote_id = m.mote_id
               AND p.ubekreftet),
           (SELECT count(*)::INT FROM public.motebeslutning b
             WHERE b.tenant = m.tenant AND b.mote_id = m.mote_id),
           (SELECT count(*)::INT FROM public.moteaksjon a
             WHERE a.tenant = m.tenant AND a.mote_id = m.mote_id
               AND a.status = 'apen'),
           EXISTS (SELECT 1 FROM public.moteopptak o
                    WHERE o.tenant = m.tenant
                      AND o.mote_id = m.mote_id),
           -- HVILKET GRUNNLAG OPPTAKET HVILTE PÅ, i lista. Den som
           -- ser at et møte ble tatt opp, skal se hvorfor det var
           -- lov — uten et klikk til.
           (SELECT h.grunnlagstype
              FROM public.moteopptak o
              JOIN public.opptakshjemmel h
                ON h.tenant = o.tenant AND h.hjemmel_id = o.hjemmel_id
             WHERE o.tenant = m.tenant AND o.mote_id = m.mote_id
             ORDER BY o.startet_ts LIMIT 1)
      FROM public.mote m
     WHERE m.tenant = p_tenant
     ORDER BY m.start_ts DESC
     LIMIT greatest(p_grense, 1)
$$;
REVOKE ALL ON FUNCTION m7_moteregister(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m7_hjemmelregister(p_tenant TEXT)
RETURNS TABLE (hjemmel_id UUID, grunnlagstype TEXT,
               beskrivelse TEXT, formal TEXT, gyldig_fra DATE,
               gyldig_til DATE, gjelder BOOLEAN, antall_opptak INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT h.hjemmel_id, h.grunnlagstype, h.beskrivelse, h.formal,
           h.gyldig_fra, h.gyldig_til,
           public.m7_hjemmel_gyldig(h.gyldig_fra, h.gyldig_til),
           (SELECT count(*)::INT FROM public.moteopptak o
             WHERE o.tenant = h.tenant
               AND o.hjemmel_id = h.hjemmel_id)
      FROM public.opptakshjemmel h
     WHERE h.tenant = p_tenant
     ORDER BY h.gyldig_fra DESC, h.registrert
$$;
REVOKE ALL ON FUNCTION m7_hjemmelregister(TEXT) FROM PUBLIC;

CREATE FUNCTION m7_beslutningene(p_tenant TEXT, p_mote_id UUID)
RETURNS TABLE (beslutning_id UUID, tekst TEXT, besluttet_av TEXT,
               besluttet_ts TIMESTAMPTZ, punkt_id UUID,
               punkt_ubekreftet BOOLEAN)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT b.beslutning_id, b.tekst, b.besluttet_av, b.besluttet_ts,
           b.punkt_id,
           -- EN BESLUTNING SOM HVILER PÅ ET UBEKREFTET PUNKT SKAL
           -- BÆRE DET VIDERE. Usikkerheten forsvinner ikke fordi
           -- noen skrev «besluttet» over den.
           (SELECT p.ubekreftet FROM public.referatpunkt p
             WHERE p.tenant = b.tenant AND p.punkt_id = b.punkt_id)
      FROM public.motebeslutning b
     WHERE b.tenant = p_tenant AND b.mote_id = p_mote_id
     ORDER BY b.besluttet_ts
$$;
REVOKE ALL ON FUNCTION m7_beslutningene(TEXT, UUID) FROM PUBLIC;

CREATE FUNCTION m7_aksjonene(p_tenant TEXT, p_grense INT)
RETURNS TABLE (aksjon_id UUID, mote_id UUID, tekst TEXT, eier TEXT,
               frist DATE, status TEXT, lukket_av TEXT,
               dogn_over_frist INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT a.aksjon_id, a.mote_id, a.tekst, a.eier, a.frist, a.status,
           a.lukket_av,
           CASE WHEN a.status = 'apen' AND a.frist < current_date
                THEN (current_date - a.frist)::INT ELSE 0 END
      FROM public.moteaksjon a
     WHERE a.tenant = p_tenant
     ORDER BY (a.status = 'apen') DESC, a.frist
     LIMIT greatest(p_grense, 1)
$$;
REVOKE ALL ON FUNCTION m7_aksjonene(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m7_motefunn(p_tenant TEXT, p_grense INT)
RETURNS TABLE (funn_id UUID, funntype TEXT, referanse TEXT,
               detaljer TEXT, over_grense BIGINT, apen BOOLEAN,
               forst_sett TIMESTAMPTZ, sist_sett TIMESTAMPTZ,
               lukket_av TEXT, kan_lukkes BOOLEAN)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT f.funn_id, f.funntype, f.referanse, f.detaljer,
           f.over_grense, f.apen, f.forst_sett, f.sist_sett,
           f.lukket_av,
           (f.apen AND NOT public.m7_funn_er_sveipens(f.funntype))
      FROM public.motefunn f
     WHERE f.tenant = p_tenant
     ORDER BY f.apen DESC, f.sist_sett DESC
     LIMIT greatest(p_grense, 1)
$$;
REVOKE ALL ON FUNCTION m7_motefunn(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m7_bildet(p_tenant TEXT)
RETURNS TABLE (moter INT, moter_uten_referat INT, punkter INT,
               ubekreftede INT, beslutninger INT,
               beslutninger_paa_ubekreftet INT, apne_aksjoner INT,
               aksjoner_over_frist INT, opptak INT, hjemler INT,
               gyldige_hjemler INT, apne_funn INT, har_krav BOOLEAN,
               referatfrist_dogn INT, aksjonsfrist_dogn INT,
               sikkerhetsterskel_bp INT, kravversjon INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT
      (SELECT count(*)::INT FROM public.mote m
        WHERE m.tenant = p_tenant),
      -- BARE MØTER SOM ER OVER. Et møte som pågår mangler ikke
      -- referat — det er ikke ferdig, og å telle det ville gjort
      -- tallet til en anklage mot noen som ikke har gjort noe galt.
      (SELECT count(*)::INT FROM public.mote m
        WHERE m.tenant = p_tenant AND m.slutt_ts < now()
          AND NOT EXISTS (SELECT 1 FROM public.referatpunkt p
                           WHERE p.tenant = m.tenant
                             AND p.mote_id = m.mote_id)),
      (SELECT count(*)::INT FROM public.referatpunkt p
        WHERE p.tenant = p_tenant),
      (SELECT count(*)::INT FROM public.referatpunkt p
        WHERE p.tenant = p_tenant AND p.ubekreftet),
      (SELECT count(*)::INT FROM public.motebeslutning b
        WHERE b.tenant = p_tenant),
      -- EN BESLUTNING TATT PÅ ET UBEKREFTET PUNKT ER DET DYRESTE
      -- TALLET I MODULEN.
      (SELECT count(*)::INT FROM public.motebeslutning b
         JOIN public.referatpunkt p
           ON p.tenant = b.tenant AND p.punkt_id = b.punkt_id
        WHERE b.tenant = p_tenant AND p.ubekreftet),
      (SELECT count(*)::INT FROM public.moteaksjon a
        WHERE a.tenant = p_tenant AND a.status = 'apen'),
      (SELECT count(*)::INT FROM public.moteaksjon a
        WHERE a.tenant = p_tenant AND a.status = 'apen'
          AND a.frist < current_date),
      (SELECT count(*)::INT FROM public.moteopptak o
        WHERE o.tenant = p_tenant),
      (SELECT count(*)::INT FROM public.opptakshjemmel h
        WHERE h.tenant = p_tenant),
      (SELECT count(*)::INT FROM public.opptakshjemmel h
        WHERE h.tenant = p_tenant
          AND public.m7_hjemmel_gyldig(h.gyldig_fra, h.gyldig_til)),
      (SELECT count(*)::INT FROM public.motefunn f
        WHERE f.tenant = p_tenant AND f.apen),
      (SELECT EXISTS (SELECT 1 FROM public.motekrav k
                       WHERE k.tenant = p_tenant)),
      (SELECT k.referatfrist_dogn FROM public.motekrav k
        WHERE k.tenant = p_tenant),
      (SELECT k.aksjonsfrist_dogn FROM public.motekrav k
        WHERE k.tenant = p_tenant),
      (SELECT k.sikkerhetsterskel_bp FROM public.motekrav k
        WHERE k.tenant = p_tenant),
      (SELECT k.versjon FROM public.motekrav k
        WHERE k.tenant = p_tenant)
$$;
REVOKE ALL ON FUNCTION m7_bildet(TEXT) FROM PUBLIC;

CREATE FUNCTION m7_lukk_funn(p_tenant TEXT, p_funn_id UUID,
                             p_begrunnelse TEXT, p_aktor TEXT)
RETURNS TABLE (funn_id UUID, apen BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.motefunn%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm7_lukk_funn');
    -- 125s LÆRDOM: en tom aktør ville gitt NULL i CHECKen, og NULL i
    -- en NOT NULL-kolonne dreper hele transaksjonen — i sveipen river
    -- ett navnløst kall med seg alle lukkingene i samme kjøring.
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'm7_lukk_funn: en lukking uten et navn på er'
            ' ikke en lukking'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_begrunnelse IS NULL OR btrim(p_begrunnelse) = '' THEN
        RAISE EXCEPTION 'm7_lukk_funn: begrunnelse mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO v_rad FROM public.motefunn
     WHERE tenant = p_tenant AND motefunn.funn_id = p_funn_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm7_lukk_funn: funnet finnes ikke'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF public.m7_funn_er_sveipens(v_rad.funntype) THEN
        RAISE EXCEPTION 'm7_lukk_funn: % lukkes ikke av et menneske —'
            ' det lukkes av at tilstanden opphører', v_rad.funntype
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT v_rad.apen THEN
        RETURN QUERY SELECT v_rad.funn_id, v_rad.apen;
        RETURN;
    END IF;
    UPDATE public.motefunn
       SET apen = false, lukket_ts = now(), lukket_av = p_aktor,
           lukket_begrunnelse = p_begrunnelse
     WHERE tenant = p_tenant AND motefunn.funn_id = p_funn_id
    RETURNING * INTO v_rad;
    PERFORM public.m7_evidens(p_tenant, p_funn_id, 'lukk_funn',
        p_aktor, jsonb_build_object('funntype', v_rad.funntype));
    RETURN QUERY SELECT v_rad.funn_id, v_rad.apen;
END $$;
REVOKE ALL ON FUNCTION m7_lukk_funn(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

-- =====================================================================
-- SVEIPEN. Én tenant om gangen, med konteksten satt (130s lærdom:
-- FORCE RLS gjør en kryss-tenant-spørring BLIND, ikke bred).
--
-- TRE FUNN REISES, OG ETT KAN ALDRI REISES:
--
-- 1. `mote_uten_referat` — møtet er over med referatfristen, og ingen
--    har skrevet et punkt. Et referat som kommer tre uker senere er
--    en rekonstruksjon.
--
-- 2. `aksjon_over_frist` — en åpen aksjon står over fristen sin med
--    nådeperioden. En aksjon ingen lukker og ingen sier fra om, er en
--    beslutning som stilnet.
--
-- 3. `ubekreftet_punkt_uavklart` — et punkt maskinen var usikker på,
--    og som ingen har rettet eller bekreftet. Dette er det ENESTE et
--    menneske kan lukke, og det er riktig: «vi har lest det, det
--    stemmer» er en legitim avklaring med et navn på.
--
-- 4. `opptak_uten_hjemmel` KAN ALDRI REISES, og at den ikke kan er
--    beviset. `hjemmel_id` er `NOT NULL` med fremmednøkkel, og døra
--    nekter før raden finnes. Funntypen står i det lukkede settet
--    fordi invarianten heter det — og porten som viser at sveipen
--    aldri reiser den, er beviset på at vernet ligger i datamodellen
--    og ikke i en nattlig sjekk.
-- =====================================================================
CREATE FUNCTION m7_sveip_moter(p_maks_tenanter INT)
RETURNS TABLE (tenanter INT, nye BIGINT, oppdaterte BIGINT,
               lukkede BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_sveip CONSTANT TEXT := 'm7_sveip';
    v_t TEXT;
    v_antall INT := 0;
    v_nye BIGINT := 0;
    v_oppdaterte BIGINT := 0;
    v_lukket BIGINT := 0;
    v_n BIGINT; v_n2 BIGINT; v_n3 BIGINT;
BEGIN
    FOR v_t IN
        SELECT k.tenant FROM public.motekrav k
         ORDER BY k.tenant LIMIT greatest(p_maks_tenanter, 1)
    LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        v_antall := v_antall + 1;

        -- 1. MØTE UTEN REFERAT. Reising og lukking over samme
        --    `kand`-CTE (127s feil, ikke gjentatt).
        WITH kand AS (
            SELECT m.mote_id, m.tittel, m.slutt_ts,
                   (current_date - m.slutt_ts::date)
                       - k.referatfrist_dogn AS dogn_over
              FROM public.mote m
              JOIN public.motekrav k ON k.tenant = m.tenant
             WHERE m.tenant = v_t
               AND m.slutt_ts < now()
               AND current_date - m.slutt_ts::date
                   > k.referatfrist_dogn
               AND NOT EXISTS (SELECT 1 FROM public.referatpunkt p
                                WHERE p.tenant = m.tenant
                                  AND p.mote_id = m.mote_id)),
        skrevet AS (
            INSERT INTO public.motefunn
                (tenant, funn_id, funntype, referanse, detaljer,
                 over_grense)
            SELECT v_t, gen_random_uuid(), 'mote_uten_referat',
                   c.mote_id::text,
                   format('«%s» sluttet %s og er %s døgn over'
                          ' referatfristen', c.tittel,
                          c.slutt_ts::date, c.dogn_over),
                   c.dogn_over
              FROM kand c
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
            DO UPDATE SET sist_sett = now(),
                          over_grense = EXCLUDED.over_grense,
                          detaljer = EXCLUDED.detaljer
            RETURNING (xmax = 0) AS var_ny),
        lukket AS (
            UPDATE public.motefunn f
               SET apen = false, lukket_ts = now(),
                   lukket_av = v_sveip,
                   lukket_begrunnelse = 'referatet er skrevet'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'mote_uten_referat'
               AND NOT EXISTS (SELECT 1 FROM kand c
                                WHERE c.mote_id::text = f.referanse)
            RETURNING 1)
        SELECT (SELECT count(*) FILTER (WHERE var_ny) FROM skrevet),
               (SELECT count(*) FILTER (WHERE NOT var_ny) FROM skrevet),
               (SELECT count(*) FROM lukket)
          INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 2. AKSJON OVER FRIST.
        WITH kand AS (
            SELECT a.aksjon_id, a.eier, a.frist,
                   (current_date - a.frist) - k.aksjonsfrist_dogn
                       AS dogn_over
              FROM public.moteaksjon a
              JOIN public.motekrav k ON k.tenant = a.tenant
             WHERE a.tenant = v_t AND a.status = 'apen'
               AND current_date - a.frist > k.aksjonsfrist_dogn),
        skrevet AS (
            INSERT INTO public.motefunn
                (tenant, funn_id, funntype, referanse, detaljer,
                 over_grense)
            SELECT v_t, gen_random_uuid(), 'aksjon_over_frist',
                   c.aksjon_id::text,
                   format('aksjonen hos %s hadde frist %s og er %s'
                          ' døgn over nådeperioden', c.eier, c.frist,
                          c.dogn_over),
                   c.dogn_over
              FROM kand c
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
            DO UPDATE SET sist_sett = now(),
                          over_grense = EXCLUDED.over_grense,
                          detaljer = EXCLUDED.detaljer
            RETURNING (xmax = 0) AS var_ny),
        lukket AS (
            UPDATE public.motefunn f
               SET apen = false, lukket_ts = now(),
                   lukket_av = v_sveip,
                   lukket_begrunnelse = 'aksjonen er lukket'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'aksjon_over_frist'
               AND NOT EXISTS (SELECT 1 FROM kand c
                                WHERE c.aksjon_id::text = f.referanse)
            RETURNING 1)
        SELECT (SELECT count(*) FILTER (WHERE var_ny) FROM skrevet),
               (SELECT count(*) FILTER (WHERE NOT var_ny) FROM skrevet),
               (SELECT count(*) FROM lukket)
          INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 3. UBEKREFTET PUNKT SOM INGEN HAR RETTET.
        --
        --    LUKKES IKKE AV SVEIPEN når et menneske har lukket det:
        --    `datakvalitet`-lærdommen fra 131. Et punkt som er
        --    avklart av et menneske skal ikke gjenåpnes hver natt,
        --    og `NOT EXISTS`-leddet på lukkede funn sørger for det.
        WITH kand AS (
            SELECT p.punkt_id, p.mote_id, p.sikkerhet_bp
              FROM public.referatpunkt p
             WHERE p.tenant = v_t AND p.ubekreftet
               AND NOT EXISTS (SELECT 1 FROM public.referatpunkt r
                                WHERE r.tenant = p.tenant
                                  AND r.retter_punkt_id = p.punkt_id)
               AND NOT EXISTS (
                   SELECT 1 FROM public.motefunn f2
                    WHERE f2.tenant = p.tenant
                      AND f2.funntype = 'ubekreftet_punkt_uavklart'
                      AND f2.referanse = p.punkt_id::text
                      AND NOT f2.apen)),
        skrevet AS (
            INSERT INTO public.motefunn
                (tenant, funn_id, funntype, referanse, detaljer,
                 over_grense)
            SELECT v_t, gen_random_uuid(),
                   'ubekreftet_punkt_uavklart', c.punkt_id::text,
                   format('punktet ble ført med %s basispunkters'
                          ' sikkerhet og er verken rettet eller'
                          ' bekreftet', c.sikkerhet_bp),
                   c.sikkerhet_bp
              FROM kand c
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
            DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS var_ny),
        lukket AS (
            UPDATE public.motefunn f
               SET apen = false, lukket_ts = now(),
                   lukket_av = v_sveip,
                   lukket_begrunnelse = 'punktet er rettet'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'ubekreftet_punkt_uavklart'
               AND NOT EXISTS (SELECT 1 FROM kand c
                                WHERE c.punkt_id::text = f.referanse)
            RETURNING 1)
        SELECT (SELECT count(*) FILTER (WHERE var_ny) FROM skrevet),
               (SELECT count(*) FILTER (WHERE NOT var_ny) FROM skrevet),
               (SELECT count(*) FROM lukket)
          INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);
    END LOOP;

    PERFORM set_config('disponit.tenant', '', true);
    RETURN QUERY SELECT v_antall, v_nye, v_oppdaterte, v_lukket;
END $$;
REVOKE ALL ON FUNCTION m7_sveip_moter(INT) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION m7_referatet(TEXT, UUID) TO disponit;
GRANT EXECUTE ON FUNCTION m7_moteregister(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m7_hjemmelregister(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m7_beslutningene(TEXT, UUID) TO disponit;
GRANT EXECUTE ON FUNCTION m7_aksjonene(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m7_motefunn(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m7_bildet(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m7_lukk_funn(TEXT, UUID, TEXT, TEXT)
    TO disponit;

-- SVEIPEROLLEN NÅR ÉN FUNKSJON, OG BARE DEN (111s form).
GRANT EXECUTE ON FUNCTION m7_sveip_moter(INT) TO disponit_motesveip;

RESET ROLE;

-- =====================================================================
-- M-36s FUNNKATALOG (132).
--
-- `motefunn` ER ET NYTT FUNNREGISTER I HUSET, og M-36 nekter å rangere
-- så lenge det finnes ett den ikke kjenner:
--
--   «en rangering laget nå ville hvilt på et grunnlag ingen visste var
--    ufullstendig»
--
-- Det er ikke en feil i 132 — det er 132 som gjør nøyaktig det den ble
-- bygget for. Den fanget denne modulen i dag, og porten under holder
-- den fanget for de tre neste.
--
-- HVER MODUL SOM LEGGER TIL EN FUNNTABELL EIER SIN EGEN RAD HER.
-- Fundamentet kunne ikke skrevet den: den krever at noen har lest
-- koden og vet HVORDAN «åpen» er kodet i akkurat denne tabellen.
-- ET FUNDAMENT KAN TILDELE NUMRE OG ROLLER UTEN Å LESE KODEN. DET KAN
-- IKKE TILDELE DATA.
--
-- `apen_kolonne`: `motefunn.apen` er husets form.
INSERT INTO m36_funnregister
    (relasjon, modul, typekolonne, apenform, begrunnelse)
VALUES
    ('motefunn', 'm7_moteoperasjon', 'funntype', 'apen_kolonne',
     'husets form')
ON CONFLICT (relasjon) DO NOTHING;

-- …OG RADEN ALENE ER BARE EN LOVNAD.
--
-- `m36_apne_funn` løper som `disponit_optimalisator_eier` og LESER
-- tabellen registeret navngir. Uten `SELECT` nekter den — og nektet
-- kommer inne i `m36_rangere`, altså på rangeringsveien til en helt
-- annen modul. Registrert uten lesrett er verre enn uregistrert: det
-- første ser komplett ut.
--
-- RETTIGHETEN ER `SELECT` OG BARE DET. Radvakten i `motefunn` står
-- urørt: M-36 leser tenantens åpne funn, den lukker ingen.
-- INGEN `SET LOCAL ROLE` HER: `motefunn` eies av migrator, som
-- eier tabellen radvakten henger på. 132 måtte sette rollen
-- tretti ganger fordi den grantet på tvers av tretti eiere.
GRANT SELECT ON motefunn TO disponit_optimalisator_eier;
