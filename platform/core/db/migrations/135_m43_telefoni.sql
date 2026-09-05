-- =====================================================================
-- M-43 TALE- OG TELEFONIAGENT (v1) — KLYNGE 9s TREDJE.
-- =====================================================================
--
-- V1-DOMMEN: MODULEN INNGÅR INGEN AVTALE OG GIR INGEN ØKONOMISKE
-- LØFTER. Vaktsetningen krever «eksplisitt policy» for begge, og v1
-- har ingen vei dit i det hele tatt — ikke en avslått vei, ikke en
-- vei bak en bryter. Det finnes ingen kolonne for et beløp, ingen
-- parameter for en pris, og ingen dør som binder noe.
--
-- ---------------------------------------------------------------------
-- KLYNGENS DELTE DOM:
--
--   EN YTRING AVGITT I HUSETS NAVN KAN IKKE TAS TILBAKE — OG DEN SOM
--   LESER DEN VET IKKE AT EN MASKIN SKREV DEN.
--
-- HER ER DEN BOKSTAVELIG. Den andre parten HØRER en stemme, og en
-- stemme høres ikke ut som en maskin lenger. Den som tror hun snakker
-- med et menneske, svarer annerledes: hun sier ting hun ikke ville
-- sagt til et system, og hun tror et løfte hun får er husets.
--
-- ---------------------------------------------------------------------
-- IDENTIFIKASJONEN ER MODULENS VIKTIGSTE RAD, OG REKKEFØLGEN ER DOMMEN.
--
-- `samtale.identifisert_ts` er NOT NULL med
-- `samtale_identifikasjon_kom_forst` (`identifisert_ts >= startet_ts`),
-- og `m43_registrer_linje` NEKTER en linje datert FØR identifikasjonen.
--
-- INGENTING BLE SAGT FØR VI SA HVA VI ER. Det er den samme formen som
-- 133s `moteopptak_varsling_kom_forst`, og den er valgt av samme grunn:
-- et nekt som kommer etter samtalen er ikke et nekt.
--
-- ---------------------------------------------------------------------
-- HJEMMELEN ER M-7s, OG DEN ARVES — DEN BYGGES IKKE PÅ NYTT.
--
-- Klynge 9-fundamentet slo fast at M-7 og M-43 deler ÉN
-- opptakshjemmel, og at M-7 bygger den. `opptakshjemmel` (133) eies av
-- migrator med husets tenantpolicy, så arven er en GRANT — ikke en
-- kopi.
--
-- OG REGELEN ARVES MED DEN: `m7_hjemmel_gyldig(DATE, DATE)` er den
-- ENESTE funksjonen som avgjør om en hjemmel gjelder. To funksjoner
-- ville gitt to svar på «hadde vi lov», og det er ett for mye.
--
-- ---------------------------------------------------------------------
-- FIRE FUNN SOM ALDRI KAN REISES, OG DET ER BEVISET.
--
--   `opptak_uten_hjemmel`     — `hjemmel_id` NOT NULL, fremmednøkkel.
--   `opptak_uten_varsling`    — `varslet_ts` NOT NULL, og den må komme
--                               FØR opptaket startet.
--   `agenten_skjulte_at_den_er_automatisert`
--                             — `identifisert_ts` NOT NULL, og ingen
--                               linje kan dateres før den.
--   `eskalering_uten_regel`   — `regel_id` NOT NULL, fremmednøkkel til
--                               tenantens egen regel.
--
-- Alle fire står i funntypesettet OG er umulige. Et sett som ikke
-- navnga dem ville ikke sagt noe; et sett som navnga dem og kunne
-- fylles ville sagt at vernet er en sveip.
--
-- ---------------------------------------------------------------------
-- GRENSEN MOT M-17 OG M-8.
--
-- M-17 eier HENVENDELSER — det kunden ber om, i tekst, med en frist.
-- M-8 eier TIDSPUNKTER — «finn et møte som passer». M-43 eier det som
-- ble SAGT i en samtale, og hva maskinen var usikker på at den hørte.
-- En modul som utvidet M-17s svarutkast til å bære transkripsjoner
-- ville gjort henvendelsesbehandling til taleopptak i stillhet.
-- =====================================================================

-- MODULROLLEN MÅ KUNNE EIE NOE FØR DEN KAN EIE DØRENE.
GRANT USAGE, CREATE ON SCHEMA public TO disponit_telefoni_eier;
GRANT INSERT ON revisjonslogg TO disponit_telefoni_eier;

-- HUSETS TENANTVAKT (038). Granten gis av EIEREN, og eieren er
-- `disponit_m37_claimer` — ikke migrator.
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_telefoni_eier;
RESET ROLE;

-- ---------------------------------------------------------------------
-- ARVEN FRA 133, OG DEN ER TO GRANTS — IKKE EN KOPI.
--
-- Tabellen eies av migrator, så den første trengs ingen `SET LOCAL
-- ROLE` for. Den andre gjør: `m7_hjemmel_gyldig` eies av
-- `disponit_mote_eier`, og et GRANT fra en som ikke eier funksjonen er
-- en FEIL, ikke en stille no-op.
-- ---------------------------------------------------------------------
GRANT SELECT, INSERT ON opptakshjemmel TO disponit_telefoni_eier;
SET LOCAL ROLE disponit_mote_eier;
GRANT EXECUTE ON FUNCTION m7_hjemmel_gyldig(DATE, DATE)
    TO disponit_telefoni_eier;
RESET ROLE;

-- ---------------------------------------------------------------------
-- `telefonikrav` — TENANTENS GRENSER, IKKE VÅRE.
--
-- INGEN TALL ER LÅST I EN DRIFTSFIL. Hvor sikker en transkripsjon må
-- være før den regnes som hørt riktig, er en vurdering av hvor mye det
-- koster å ta feil — og en bestilling av pizza og en avtale om
-- oppsigelse tåler ikke det samme.
-- ---------------------------------------------------------------------
CREATE TABLE telefonikrav (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kravversjon INT NOT NULL CHECK (kravversjon >= 1),
    -- Under denne merkes en transkripsjonslinje `ubekreftet`.
    sikkerhetsterskel_bp INT NOT NULL
        CHECK (sikkerhetsterskel_bp BETWEEN 1 AND 10000),
    -- Hvor mange sekunder etter at samtalen startet agenten SENEST må
    -- ha sagt hva den er. Null er ikke lov: et menneske rekker ikke å
    -- oppfatte noe som sies i samme sekund som det ringer.
    identifikasjonsfrist_sek INT NOT NULL
        CHECK (identifikasjonsfrist_sek BETWEEN 1 AND 120),
    -- Hvor mange døgn en åpen eskalering kan stå.
    eskaleringsfrist_dogn INT NOT NULL
        CHECK (eskaleringsfrist_dogn BETWEEN 1 AND 90),
    -- Hvor mange timer en samtale kan stå uten sluttidspunkt før den
    -- regnes som hengende.
    samtaletak_timer INT NOT NULL
        CHECK (samtaletak_timer BETWEEN 1 AND 168),
    satt TIMESTAMPTZ NOT NULL DEFAULT now(),
    satt_av TEXT NOT NULL CHECK (satt_av ~ '[^[:space:]]'),
    CONSTRAINT telefonikrav_pk PRIMARY KEY (tenant, kravversjon)
);

-- ---------------------------------------------------------------------
-- `eskaleringsregel` — KUNDENS REGLER, IKKE MODULENS.
--
-- Vaktsetningen sier «eskaleringsregler er kundens». En eskalering
-- uten en regel å peke på er MODULENS EGEN BESLUTNING om at noe var
-- viktig nok til å vekke et menneske — og det er nettopp det den ikke
-- skal ta.
-- ---------------------------------------------------------------------
CREATE TABLE eskaleringsregel (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    regel_id UUID NOT NULL,
    -- Regelen SKREVET UT. Ikke en kode: den som eskalerer skal kunne
    -- lese hvorfor, og den som blir vekket skal kunne se det.
    beskrivelse TEXT NOT NULL CHECK (length(btrim(beskrivelse)) >= 16),
    -- Hvem regelen sender saken TIL. En eskalering uten en mottaker er
    -- en alarm i et tomt rom.
    mottaker TEXT NOT NULL CHECK (mottaker ~ '[^[:space:]]'),
    gyldig_fra DATE NOT NULL,
    gyldig_til DATE,
    CONSTRAINT eskaleringsregel_gyldighet CHECK (
        gyldig_til IS NULL OR gyldig_til >= gyldig_fra),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT eskaleringsregel_pk PRIMARY KEY (tenant, regel_id)
);
CREATE INDEX eskaleringsregel_gjeldende
    ON eskaleringsregel (tenant, gyldig_fra DESC)
    WHERE gyldig_til IS NULL;

-- ---------------------------------------------------------------------
-- `samtale` — OG `identifisert_ts` ER MODULENS VIKTIGSTE KOLONNE.
--
-- `motpart` ER EN ÅPEN REFERANSE, ikke en fremmednøkkel: huset har
-- intet personregister, og M-7, M-30, M-50 og M-39 har hver valgt
-- samme løsning. En fremmednøkkel til en tabell som ikke er skrevet
-- ville vært en løgn med en constraint på.
-- ---------------------------------------------------------------------
CREATE TABLE samtale (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    samtale_id UUID NOT NULL,
    retning TEXT NOT NULL
        CONSTRAINT samtale_retning_lukket
        CHECK (retning IN ('inngaaende', 'utgaaende')),
    motpart TEXT NOT NULL CHECK (motpart ~ '[^[:space:]]'),
    startet_ts TIMESTAMPTZ NOT NULL,
    -- DEN VIKTIGSTE LINJEN I FILA.
    --
    -- Den som tror hun snakker med et menneske, svarer annerledes.
    -- Kolonnen er NOT NULL, og tidspunktet må ligge PÅ ELLER ETTER at
    -- samtalen startet — en identifikasjon datert før samtalen er en
    -- identifikasjon ingen hørte.
    identifisert_ts TIMESTAMPTZ NOT NULL,
    CONSTRAINT samtale_identifikasjon_kom_forst
        CHECK (identifisert_ts >= startet_ts),
    -- ORDLYDEN STÅR PÅ RADEN. «Agenten identifiserte seg» er en
    -- påstand; DETTE er hva den faktisk sa, og den som skal svare for
    -- samtalen skal kunne lese det.
    identifikasjonstekst TEXT NOT NULL
        CHECK (length(btrim(identifikasjonstekst)) >= 8),
    slutt_ts TIMESTAMPTZ,
    CONSTRAINT samtale_slutt_etter_start CHECK (
        slutt_ts IS NULL OR slutt_ts >= startet_ts),
    avsluttet_av TEXT CHECK (avsluttet_av ~ '[^[:space:]]'),
    CONSTRAINT samtale_avslutning_er_hel CHECK (
        (slutt_ts IS NULL) = (avsluttet_av IS NULL)),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT samtale_pk PRIMARY KEY (tenant, samtale_id)
);
CREATE INDEX samtale_apne
    ON samtale (tenant, startet_ts) WHERE slutt_ts IS NULL;
CREATE INDEX samtale_ferskeste
    ON samtale (tenant, startet_ts DESC);

-- ---------------------------------------------------------------------
-- `samtaleopptak` — 133s FORM, ORDRETT, OG DET ER MENINGEN.
--
-- Et opptak av en telefonsamtale og et opptak av et møte er samme
-- behandling av personopplysninger. To modeller ville gitt to svar på
-- «hadde vi lov», og fundamentet slo fast at det er ett for mye.
-- ---------------------------------------------------------------------
CREATE TABLE samtaleopptak (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    opptak_id UUID NOT NULL,
    samtale_id UUID NOT NULL,
    -- ARVET FRA 133. NOT NULL med fremmednøkkel til den DELTE
    -- hjemmelen: et opptak uten grunnlag er urepresenterbart.
    hjemmel_id UUID NOT NULL,
    varslet_ts TIMESTAMPTZ NOT NULL,
    varslet_av TEXT NOT NULL CHECK (varslet_av ~ '[^[:space:]]'),
    varslede TEXT[] NOT NULL CHECK (cardinality(varslede) > 0),
    startet_ts TIMESTAMPTZ NOT NULL,
    -- DEN NEST VIKTIGSTE LINJEN I FILA, og den er 133s.
    CONSTRAINT samtaleopptak_varsling_kom_forst
        CHECK (varslet_ts <= startet_ts),
    ekstern_ref TEXT CHECK (ekstern_ref ~ '[^[:space:]]'),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT samtaleopptak_pk PRIMARY KEY (tenant, opptak_id),
    CONSTRAINT samtaleopptak_samtale_fk
        FOREIGN KEY (tenant, samtale_id) REFERENCES samtale (tenant, samtale_id),
    CONSTRAINT samtaleopptak_hjemmel_fk
        FOREIGN KEY (tenant, hjemmel_id)
        REFERENCES opptakshjemmel (tenant, hjemmel_id),
    -- ETT OPPTAK PER SAMTALE. To ville gjort «hva ble tatt opp» til et
    -- spørsmål med to svar.
    CONSTRAINT samtaleopptak_en_per_samtale UNIQUE (tenant, samtale_id)
);
CREATE INDEX samtaleopptak_hjemmel ON samtaleopptak (tenant, hjemmel_id);

-- ---------------------------------------------------------------------
-- `transkripsjonslinje` — EN TRANSKRIPSJON UTEN USIKKERHET ER EN
-- PÅSTAND OM AT MASKINEN HØRTE RIKTIG.
--
-- 133s `referatpunkt`, ordrett i formen: tallet OG terskelen som
-- gjaldt DA står på raden, og `ubekreftet` er bundet til de to.
-- Uten den frosne terskelen kan «hvorfor er dette merket?» ikke
-- besvares etter at grensen er justert.
-- ---------------------------------------------------------------------
CREATE TABLE transkripsjonslinje (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    linje_id UUID NOT NULL,
    samtale_id UUID NOT NULL,
    rekkefolge INT NOT NULL CHECK (rekkefolge >= 1),
    -- HVEM SOM SNAKKET. Et lukket sett: en linje uten en taler er en
    -- linje ingen kan svare for.
    taler TEXT NOT NULL
        CONSTRAINT transkripsjonslinje_taler_lukket
        CHECK (taler IN ('agent', 'motpart', 'menneske')),
    linje_ts TIMESTAMPTZ NOT NULL,
    tekst TEXT NOT NULL CHECK (length(btrim(tekst)) > 0),
    kilde TEXT NOT NULL
        CONSTRAINT transkripsjonslinje_kilde_lukket
        CHECK (kilde IN ('transkripsjon', 'manuell')),
    sikkerhet_bp INT NOT NULL CHECK (sikkerhet_bp BETWEEN 0 AND 10000),
    terskel_bp INT NOT NULL CHECK (terskel_bp BETWEEN 1 AND 10000),
    ubekreftet BOOLEAN NOT NULL,
    CONSTRAINT transkripsjonslinje_flagget_stemmer CHECK (
        ubekreftet = (sikkerhet_bp < terskel_bp)),
    -- ET MENNESKE SOM SKREV SELV, HØRTE IKKE FEIL. 133s form.
    CONSTRAINT transkripsjonslinje_manuell_er_sikker CHECK (
        kilde <> 'manuell' OR sikkerhet_bp = 10000),
    -- EN RETTELSE ER EN NY LINJE SOM PEKER PÅ DEN GAMLE, og begge
    -- står. Transkripsjonen er append-only.
    retter_linje_id UUID,
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL CHECK (registrert_av ~ '[^[:space:]]'),
    CONSTRAINT transkripsjonslinje_pk PRIMARY KEY (tenant, linje_id),
    CONSTRAINT transkripsjonslinje_samtale_fk
        FOREIGN KEY (tenant, samtale_id) REFERENCES samtale (tenant, samtale_id),
    CONSTRAINT transkripsjonslinje_retter_fk
        FOREIGN KEY (tenant, retter_linje_id)
        REFERENCES transkripsjonslinje (tenant, linje_id),
    CONSTRAINT transkripsjonslinje_nummer_unikt
        UNIQUE (tenant, samtale_id, rekkefolge)
);
CREATE INDEX transkripsjonslinje_samtale
    ON transkripsjonslinje (tenant, samtale_id, rekkefolge);
CREATE INDEX transkripsjonslinje_ubekreftede
    ON transkripsjonslinje (tenant, samtale_id) WHERE ubekreftet;

-- ---------------------------------------------------------------------
-- `eskalering` — REGELEN ER EN NOT NULL FREMMEDNØKKEL.
--
-- «Eskaleringsregler er kundens.» En eskalering uten en regel å peke
-- på er urepresenterbar — ikke oppdaget, ikke varslet, ikke rapportert
-- i en nattlig sveip.
-- ---------------------------------------------------------------------
CREATE TABLE eskalering (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    eskalering_id UUID NOT NULL,
    samtale_id UUID NOT NULL,
    regel_id UUID NOT NULL,
    -- REGELENS MOTTAKER KOPIERES INN. Regelen kan avvikles i morgen;
    -- «hvem ble vekket» skal fortsatt kunne besvares.
    mottaker TEXT NOT NULL CHECK (mottaker ~ '[^[:space:]]'),
    begrunnelse TEXT NOT NULL CHECK (length(btrim(begrunnelse)) >= 8),
    eskalert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    eskalert_av TEXT NOT NULL CHECK (eskalert_av ~ '[^[:space:]]'),
    -- LUKKINGEN BÆRER ET NAVN (125/126s vakt).
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT CHECK (lukket_av ~ '[^[:space:]]'),
    lukket_utfall TEXT
        CONSTRAINT eskalering_utfall_lukket
        CHECK (lukket_utfall IS NULL
               OR lukket_utfall IN ('haandtert', 'henlagt')),
    CONSTRAINT eskalering_lukking_er_hel CHECK (
        (lukket_ts IS NULL) = (lukket_av IS NULL)
        AND (lukket_ts IS NULL) = (lukket_utfall IS NULL)),
    CONSTRAINT eskalering_pk PRIMARY KEY (tenant, eskalering_id),
    CONSTRAINT eskalering_samtale_fk
        FOREIGN KEY (tenant, samtale_id) REFERENCES samtale (tenant, samtale_id),
    CONSTRAINT eskalering_regel_fk
        FOREIGN KEY (tenant, regel_id)
        REFERENCES eskaleringsregel (tenant, regel_id)
);
CREATE INDEX eskalering_apne
    ON eskalering (tenant, eskalert_ts) WHERE lukket_ts IS NULL;

-- ---------------------------------------------------------------------
-- `telefonifunn` — HUSETS FORM (`apen_kolonne`).
-- ---------------------------------------------------------------------
CREATE TABLE telefonifunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    funn_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CONSTRAINT telefonifunn_type_lukket CHECK (funntype IN (
            -- SVEIPENS EGNE. Lukkes av at tilstanden opphører.
            'samtale_uten_avslutning',
            'eskalering_over_frist',
            -- ET MENNESKE KAN LUKKE DENNE, med et navn på.
            'ubekreftet_linje_uavklart',
            -- DE FIRE SOM ALDRI KAN REISES. Se filhodet: at de står i
            -- settet OG er umulige er hele beviset.
            'opptak_uten_hjemmel',
            'opptak_uten_varsling',
            'agenten_skjulte_at_den_er_automatisert',
            'eskalering_uten_regel')),
    referanse UUID NOT NULL,
    detaljer TEXT NOT NULL CHECK (length(btrim(detaljer)) > 0),
    over_grense BIGINT,
    apen BOOLEAN NOT NULL DEFAULT true,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT CHECK (lukket_av ~ '[^[:space:]]'),
    lukket_grunn TEXT,
    CONSTRAINT telefonifunn_pk PRIMARY KEY (tenant, funn_id),
    CONSTRAINT telefonifunn_unikt UNIQUE (tenant, funntype, referanse),
    CONSTRAINT telefonifunn_lukking_har_navn CHECK (
        apen OR (lukket_ts IS NOT NULL AND lukket_av IS NOT NULL))
);
CREATE INDEX telefonifunn_apne
    ON telefonifunn (tenant, funntype) WHERE apen;

-- =====================================================================
-- RADVAKTENE. APPEND-ONLY ER EN TRIGGER, IKKE EN VANE.
--
-- `samtale_overskrevet` er en invariant i grensen, og en REVOKE alene
-- måler den ikke: migrator beholder rettighetene sine, og en fremtidig
-- migrasjon som «rydder» ville kunnet skrive over historikken uten at
-- noe sa fra. Vakten står på TABELLEN og gjelder alle.
-- =====================================================================
CREATE FUNCTION m43_samtalevakt() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'samtale er append-only: en samtale som kan'
            ' slettes kan ikke svare paa hva som ble sagt';
    END IF;
    -- BARE AVSLUTNINGEN KAN SKRIVES I ETTERTID, og bare én gang.
    IF NEW.samtale_id <> OLD.samtale_id
       OR NEW.retning <> OLD.retning
       OR NEW.motpart <> OLD.motpart
       OR NEW.startet_ts <> OLD.startet_ts
       OR NEW.identifisert_ts <> OLD.identifisert_ts
       OR NEW.identifikasjonstekst <> OLD.identifikasjonstekst
       OR NEW.registrert_av <> OLD.registrert_av
    THEN
        RAISE EXCEPTION 'samtale: bare avslutningen kan skrives i'
            ' ettertid — identifikasjonen er frossen';
    END IF;
    IF OLD.slutt_ts IS NOT NULL THEN
        RAISE EXCEPTION 'samtale: allerede avsluttet %', OLD.slutt_ts;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m43_samtalevakt() FROM PUBLIC;
CREATE TRIGGER m43_samtalevakt
    BEFORE UPDATE OR DELETE ON samtale
    FOR EACH ROW EXECUTE FUNCTION m43_samtalevakt();

-- OPPTAKET, LINJEN OG REGELEN ER HELT FROSNE.
--
-- En transkripsjonslinje som kunne endres etter at sikkerheten ble
-- skrevet, ville vært en linje som byttet sikkerhet uten å si fra — og
-- da måler `transkripsjonslinje_flagget_stemmer` ingenting.
CREATE FUNCTION m43_frossenvakt() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    RAISE EXCEPTION '%: raden er frossen — % er ikke tillatt',
        TG_TABLE_NAME, TG_OP;
END $$;
REVOKE ALL ON FUNCTION m43_frossenvakt() FROM PUBLIC;
CREATE TRIGGER m43_opptaksvakt
    BEFORE UPDATE OR DELETE ON samtaleopptak
    FOR EACH ROW EXECUTE FUNCTION m43_frossenvakt();
CREATE TRIGGER m43_linjevakt
    BEFORE UPDATE OR DELETE ON transkripsjonslinje
    FOR EACH ROW EXECUTE FUNCTION m43_frossenvakt();

-- REGELEN: bare avviklingen kan skrives, og bare framover.
CREATE FUNCTION m43_regelvakt() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'eskaleringsregel er append-only: en regel som'
            ' kan slettes kan ikke svare paa hva som gjaldt da';
    END IF;
    IF NEW.regel_id <> OLD.regel_id
       OR NEW.beskrivelse <> OLD.beskrivelse
       OR NEW.mottaker <> OLD.mottaker
       OR NEW.gyldig_fra <> OLD.gyldig_fra
       OR NEW.registrert_av <> OLD.registrert_av
    THEN
        RAISE EXCEPTION 'eskaleringsregel: bare avviklingen kan skrives';
    END IF;
    IF OLD.gyldig_til IS NOT NULL THEN
        RAISE EXCEPTION 'eskaleringsregel: allerede avviklet %',
            OLD.gyldig_til;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m43_regelvakt() FROM PUBLIC;
CREATE TRIGGER m43_regelvakt
    BEFORE UPDATE OR DELETE ON eskaleringsregel
    FOR EACH ROW EXECUTE FUNCTION m43_regelvakt();

-- ESKALERINGEN: bare lukkingen, og bare én gang.
CREATE FUNCTION m43_eskaleringsvakt() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'eskalering er append-only';
    END IF;
    IF NEW.eskalering_id <> OLD.eskalering_id
       OR NEW.samtale_id <> OLD.samtale_id
       OR NEW.regel_id <> OLD.regel_id
       OR NEW.mottaker <> OLD.mottaker
       OR NEW.begrunnelse <> OLD.begrunnelse
       OR NEW.eskalert_ts <> OLD.eskalert_ts
       OR NEW.eskalert_av <> OLD.eskalert_av
    THEN
        RAISE EXCEPTION 'eskalering: bare lukkingen kan skrives';
    END IF;
    IF OLD.lukket_ts IS NOT NULL THEN
        RAISE EXCEPTION 'eskalering: allerede lukket %', OLD.lukket_ts;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m43_eskaleringsvakt() FROM PUBLIC;
CREATE TRIGGER m43_eskaleringsvakt
    BEFORE UPDATE OR DELETE ON eskalering
    FOR EACH ROW EXECUTE FUNCTION m43_eskaleringsvakt();

-- FUNNET: bare lukkingen og `sist_sett` (133/134s form).
CREATE FUNCTION m43_funnvakt() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'telefonifunn er append-only';
    END IF;
    IF NEW.funntype <> OLD.funntype OR NEW.referanse <> OLD.referanse
       OR NEW.forst_sett <> OLD.forst_sett THEN
        RAISE EXCEPTION 'telefonifunn: funntype, referanse og'
            ' forst_sett er frosne';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m43_funnvakt() FROM PUBLIC;
CREATE TRIGGER m43_funnvakt
    BEFORE UPDATE OR DELETE ON telefonifunn
    FOR EACH ROW EXECUTE FUNCTION m43_funnvakt();

-- =====================================================================
-- RADVAKT OG RETTIGHETER. FORCE RLS PÅ ALLE SJU.
--
-- `tenantlekkasje_i_samtaleregister` er en invariant i grensen, og
-- FORCE er forskjellen: uten den ser eieren av tabellen forbi sin egen
-- policy, og en SECURITY DEFINER-dør som eide tabellen ville lest alle
-- tenanter uten å vite det.
-- =====================================================================
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['telefonikrav', 'eskaleringsregel',
                             'samtale', 'samtaleopptak',
                             'transkripsjonslinje', 'eskalering',
                             'telefonifunn']
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format($f$CREATE POLICY tenant_isolasjon ON public.%I
            USING (tenant = current_setting('disponit.tenant', true))
            WITH CHECK (tenant = current_setting('disponit.tenant', true))$f$, t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO'
                       ' disponit_telefoni_eier', t);
    END LOOP;
END $$;

-- APPEND-ONLY MÅLT SOM EN RETTIGHET OG IKKE BARE SOM EN TRIGGER.
REVOKE UPDATE ON public.samtaleopptak FROM disponit_telefoni_eier;
REVOKE UPDATE ON public.transkripsjonslinje FROM disponit_telefoni_eier;

-- SVEIPENS KRYSS-TENANT-POLICY (130s LÆRDOM).
--
-- En sveip uten `disponit.tenant` ville sett NULL RADER under FORCE
-- RLS og rapportert null funn — MED GRØNN EXIT-KODE.
CREATE POLICY m43_sveip_tenantliste ON telefonikrav
    FOR SELECT
    USING (current_setting('disponit.tenant', true) IS NULL
           OR current_setting('disponit.tenant', true) = '');

-- =====================================================================
-- HERFRA EIES DØRENE AV TELEFONIEIEREN.
--
-- SP-7: kjøretiden får EXECUTE på dørene og INGEN tabellrettigheter.
-- =====================================================================
SET LOCAL ROLE disponit_telefoni_eier;

-- `m43_evidens` — HUSETS SPOR.
CREATE FUNCTION m43_evidens(p_tenant TEXT, p_ref UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm43_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm43_telefoni', 'handling', p_handling,
        'ref', p_ref::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm43_telefoni',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:telefoni', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;

-- `m43_funn_er_sveipens` — HVEM SOM KAN LUKKE HVA.
--
-- `ubekreftet_linje_uavklart` KAN lukkes av et menneske — «vi har hørt
-- opptaket, det stemmer» er en legitim avklaring med et navn på. Alt
-- annet lukkes av at TILSTANDEN opphører, og de fire umulige står her
-- som sveipens: dukket en av dem opp, er den ingen skal kunne huke
-- bort.
CREATE FUNCTION m43_funn_er_sveipens(p_funntype TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE AS $$
    SELECT p_funntype <> 'ubekreftet_linje_uavklart'
$$;

-- `m43_regel_gjelder` — ÉN REGEL FOR OM EN REGEL GJELDER.
--
-- Merk at hjemmelens gyldighet IKKE har en søsterfunksjon her:
-- `m7_hjemmel_gyldig` (133) er husets, og den arves. To funksjoner
-- ville gitt to svar på «hadde vi lov».
CREATE FUNCTION m43_regel_gjelder(p_fra DATE, p_til DATE, p_paa DATE)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE AS $$
    SELECT p_paa >= p_fra AND (p_til IS NULL OR p_paa <= p_til)
$$;

-- ---------------------------------------------------------------------
-- `m43_sett_krav` — TENANTENS GRENSER. NY VERSJON, ALDRI OVERSKRIVING.
-- ---------------------------------------------------------------------
CREATE FUNCTION m43_sett_krav(p_tenant TEXT, p_terskel INT,
                              p_identfrist_sek INT,
                              p_eskaleringsfrist INT,
                              p_samtaletak_timer INT, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm43_sett_krav');
    SELECT coalesce(max(kravversjon), 0) + 1 INTO v_versjon
      FROM public.telefonikrav WHERE tenant = p_tenant;
    INSERT INTO public.telefonikrav
        (tenant, kravversjon, sikkerhetsterskel_bp,
         identifikasjonsfrist_sek, eskaleringsfrist_dogn,
         samtaletak_timer, satt_av)
    VALUES (p_tenant, v_versjon, p_terskel, p_identfrist_sek,
            p_eskaleringsfrist, p_samtaletak_timer, p_aktor);
    PERFORM public.m43_evidens(p_tenant, NULL, 'sett_krav', p_aktor,
        jsonb_build_object('versjon', v_versjon));
    RETURN v_versjon;
END $$;

-- ---------------------------------------------------------------------
-- `m43_registrer_hjemmel` — SKRIVER I DEN DELTE HJEMMELEN (133).
--
-- INGEN EGEN TABELL, og døra er M-43s egen fordi en tenant som bruker
-- telefonimodulen uten møtemodulen fortsatt må kunne registrere
-- grunnlaget et opptak hviler på.
-- ---------------------------------------------------------------------
CREATE FUNCTION m43_registrer_hjemmel(p_tenant TEXT, p_hjemmel_id UUID,
                                      p_grunnlagstype TEXT,
                                      p_beskrivelse TEXT, p_formal TEXT,
                                      p_gyldig_fra DATE,
                                      p_gyldig_til DATE, p_aktor TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm43_registrer_hjemmel');
    INSERT INTO public.opptakshjemmel
        (tenant, hjemmel_id, grunnlagstype, beskrivelse, formal,
         gyldig_fra, gyldig_til, registrert_av)
    VALUES (p_tenant, p_hjemmel_id, p_grunnlagstype, p_beskrivelse,
            p_formal, p_gyldig_fra, p_gyldig_til, p_aktor)
    ON CONFLICT (tenant, hjemmel_id) DO NOTHING;
    PERFORM public.m43_evidens(p_tenant, p_hjemmel_id,
        'registrer_hjemmel', p_aktor,
        jsonb_build_object('grunnlag', p_grunnlagstype));
    RETURN p_hjemmel_id;
END $$;

-- ---------------------------------------------------------------------
-- `m43_registrer_regel` — KUNDENS REGEL, IKKE MODULENS.
-- ---------------------------------------------------------------------
CREATE FUNCTION m43_registrer_regel(p_tenant TEXT, p_regel_id UUID,
                                    p_beskrivelse TEXT, p_mottaker TEXT,
                                    p_gyldig_fra DATE, p_gyldig_til DATE,
                                    p_aktor TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm43_registrer_regel');
    INSERT INTO public.eskaleringsregel
        (tenant, regel_id, beskrivelse, mottaker, gyldig_fra,
         gyldig_til, registrert_av)
    VALUES (p_tenant, p_regel_id, p_beskrivelse, p_mottaker,
            p_gyldig_fra, p_gyldig_til, p_aktor)
    ON CONFLICT (tenant, regel_id) DO NOTHING;
    PERFORM public.m43_evidens(p_tenant, p_regel_id, 'registrer_regel',
        p_aktor, jsonb_build_object('mottaker', p_mottaker));
    RETURN p_regel_id;
END $$;

CREATE FUNCTION m43_avvikle_regel(p_tenant TEXT, p_regel_id UUID,
                                  p_gyldig_til DATE, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_fra DATE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm43_avvikle_regel');
    SELECT gyldig_fra INTO v_fra FROM public.eskaleringsregel
     WHERE tenant = p_tenant AND regel_id = p_regel_id FOR UPDATE;
    IF v_fra IS NULL THEN
        RAISE EXCEPTION 'm43_avvikle_regel: ukjent regel %', p_regel_id;
    END IF;
    IF p_gyldig_til < v_fra THEN
        RAISE EXCEPTION 'm43_avvikle_regel: kan ikke avvikles foer den'
            ' gjaldt';
    END IF;
    UPDATE public.eskaleringsregel r SET gyldig_til = p_gyldig_til
     WHERE r.tenant = p_tenant AND r.regel_id = p_regel_id;
    PERFORM public.m43_evidens(p_tenant, p_regel_id, 'avvikle_regel',
        p_aktor, jsonb_build_object('gyldig_til', p_gyldig_til));
    RETURN FOUND;
END $$;

-- ---------------------------------------------------------------------
-- `m43_start_samtale` — MODULENS VIKTIGSTE DØR.
--
-- TRE NEKT, ALLE FØR RADEN FINNES:
--
--   1. Identifikasjonen er datert FØR samtalen startet. En
--      identifikasjon ingen kunne hørt er ingen identifikasjon.
--   2. Identifikasjonen kom for SENT etter tenantens egen frist. Den
--      som har snakket i to minutter før hun får vite hva hun snakker
--      med, har allerede svart som til et menneske.
--   3. Tenanten har ingen grenser. Uten en frist kan «for sent» ikke
--      måles, og da er identifikasjonen en formalitet.
--
-- `identifikasjonstekst` ER PÅKREVD OG SKREVET UT. «Agenten
-- identifiserte seg» er en påstand; teksten er hva den faktisk sa.
-- ---------------------------------------------------------------------
CREATE FUNCTION m43_start_samtale(p_tenant TEXT, p_samtale_id UUID,
                                  p_retning TEXT, p_motpart TEXT,
                                  p_startet_ts TIMESTAMPTZ,
                                  p_identifisert_ts TIMESTAMPTZ,
                                  p_identtekst TEXT, p_aktor TEXT)
RETURNS TABLE (samtale_id UUID, sekunder_til_identifikasjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_krav RECORD;
    v_sek INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm43_start_samtale');
    SELECT * INTO v_krav FROM public.telefonikrav
     WHERE tenant = p_tenant ORDER BY kravversjon DESC LIMIT 1;
    -- NEKT 3.
    IF v_krav IS NULL THEN
        RAISE EXCEPTION 'm43_start_samtale: tenanten har ingen'
            ' telefonikrav — grensene settes foer det ringes';
    END IF;
    -- NEKT 1. Constrainten fanger den ogsaa, men et nekt med en
    -- setning bak er en feilmelding noen kan handle paa.
    IF p_identifisert_ts < p_startet_ts THEN
        RAISE EXCEPTION 'm43_start_samtale: identifikasjonen er datert'
            ' foer samtalen startet — den kunne ingen hoert';
    END IF;
    v_sek := extract(epoch FROM (p_identifisert_ts - p_startet_ts))::INT;
    -- NEKT 2.
    IF v_sek > v_krav.identifikasjonsfrist_sek THEN
        RAISE EXCEPTION 'm43_start_samtale: agenten sa hva den er'
            ' etter % sekunder, fristen er % — den som har snakket'
            ' saa lenge har allerede svart som til et menneske',
            v_sek, v_krav.identifikasjonsfrist_sek;
    END IF;
    INSERT INTO public.samtale
        (tenant, samtale_id, retning, motpart, startet_ts,
         identifisert_ts, identifikasjonstekst, registrert_av)
    VALUES (p_tenant, p_samtale_id, p_retning, p_motpart, p_startet_ts,
            p_identifisert_ts, p_identtekst, p_aktor)
    -- CONSTRAINTEN VED NAVN, IKKE KOLONNENE: OUT-parameteren heter
    -- `samtale_id`, og `ON CONFLICT (tenant, samtale_id)` er da
    -- tvetydig. Et alias hjelper ikke — ON CONFLICT tar ikke ett.
    -- 132 gikk i den samme fella med `antall` og `relasjon`.
    ON CONFLICT ON CONSTRAINT samtale_pk DO NOTHING;
    PERFORM public.m43_evidens(p_tenant, p_samtale_id, 'start_samtale',
        p_aktor, jsonb_build_object('retning', p_retning,
                                    'sekunder', v_sek));
    RETURN QUERY SELECT p_samtale_id, v_sek;
END $$;

-- ---------------------------------------------------------------------
-- `m43_avslutt_samtale` — ENVEIS, OG BÆRER ET NAVN.
-- ---------------------------------------------------------------------
CREATE FUNCTION m43_avslutt_samtale(p_tenant TEXT, p_samtale_id UUID,
                                    p_slutt_ts TIMESTAMPTZ, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_start TIMESTAMPTZ;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm43_avslutt_samtale');
    SELECT startet_ts INTO v_start FROM public.samtale
     WHERE tenant = p_tenant AND samtale_id = p_samtale_id FOR UPDATE;
    IF v_start IS NULL THEN
        RAISE EXCEPTION 'm43_avslutt_samtale: ukjent samtale %',
            p_samtale_id;
    END IF;
    IF p_slutt_ts < v_start THEN
        RAISE EXCEPTION 'm43_avslutt_samtale: sluttidspunktet ligger'
            ' foer starten';
    END IF;
    UPDATE public.samtale s
       SET slutt_ts = p_slutt_ts, avsluttet_av = p_aktor
     WHERE s.tenant = p_tenant AND s.samtale_id = p_samtale_id;
    PERFORM public.m43_evidens(p_tenant, p_samtale_id,
        'avslutt_samtale', p_aktor, '{}'::jsonb);
    RETURN FOUND;
END $$;

-- ---------------------------------------------------------------------
-- `m43_start_opptak` — 133s FIRE NEKT, ARVET ORDRETT.
--
--   1. Hjemmelen finnes ikke.
--   2. Hjemmelen er UTLØPT. En utløpt hjemmel ser nøyaktig ut som en
--      gyldig — klynge 7s dom, og den gjelder her.
--   3. Ingen er varslet.
--   4. Varslingen kom ETTER at opptaket startet.
--
-- ET NEKT SOM KOMMER ETTER MIKROFONEN ER IKKE ET NEKT. Alle fire
-- kommer FØR raden finnes, og gyldigheten måles med M-7s egen
-- funksjon — ikke med en kopi.
-- ---------------------------------------------------------------------
CREATE FUNCTION m43_start_opptak(p_tenant TEXT, p_opptak_id UUID,
                                 p_samtale_id UUID, p_hjemmel_id UUID,
                                 p_varslet_ts TIMESTAMPTZ,
                                 p_varslet_av TEXT, p_varslede TEXT[],
                                 p_startet_ts TIMESTAMPTZ, p_aktor TEXT)
RETURNS TABLE (opptak_id UUID, grunnlagstype TEXT, ny BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_hjemmel RECORD;
    v_finnes UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm43_start_opptak');
    -- NEKT 1.
    SELECT * INTO v_hjemmel FROM public.opptakshjemmel
     WHERE tenant = p_tenant AND hjemmel_id = p_hjemmel_id;
    IF v_hjemmel IS NULL THEN
        RAISE EXCEPTION 'm43_start_opptak: ukjent opptakshjemmel % —'
            ' et opptak uten grunnlag er ulovlig i det oeyeblikket det'
            ' starter', p_hjemmel_id;
    END IF;
    -- NEKT 2. M-7s EGEN FUNKSJON, ikke en kopi av regelen.
    IF NOT public.m7_hjemmel_gyldig(v_hjemmel.gyldig_fra,
                                    v_hjemmel.gyldig_til) THEN
        RAISE EXCEPTION 'm43_start_opptak: hjemmelen % er utloept',
            v_hjemmel.formal;
    END IF;
    -- NEKT 3.
    IF p_varslede IS NULL OR cardinality(p_varslede) = 0
       OR p_varslet_av IS NULL OR btrim(p_varslet_av) = '' THEN
        RAISE EXCEPTION 'm43_start_opptak: ingen er varslet';
    END IF;
    -- NEKT 4. DEN VIKTIGSTE.
    IF p_varslet_ts > p_startet_ts THEN
        RAISE EXCEPTION 'm43_start_opptak: varslingen kom % etter at'
            ' opptaket startet — et nekt som kommer etter mikrofonen'
            ' er ikke et nekt', p_varslet_ts - p_startet_ts;
    END IF;
    SELECT o.opptak_id INTO v_finnes FROM public.samtaleopptak o
     WHERE o.tenant = p_tenant AND o.samtale_id = p_samtale_id;
    IF v_finnes IS NOT NULL THEN
        RETURN QUERY SELECT v_finnes, v_hjemmel.grunnlagstype, false;
        RETURN;
    END IF;
    INSERT INTO public.samtaleopptak
        (tenant, opptak_id, samtale_id, hjemmel_id, varslet_ts,
         varslet_av, varslede, startet_ts, registrert_av)
    VALUES (p_tenant, p_opptak_id, p_samtale_id, p_hjemmel_id,
            p_varslet_ts, p_varslet_av, p_varslede, p_startet_ts,
            p_aktor);
    PERFORM public.m43_evidens(p_tenant, p_opptak_id, 'start_opptak',
        p_aktor, jsonb_build_object('hjemmel', p_hjemmel_id,
                                    'grunnlag', v_hjemmel.grunnlagstype));
    RETURN QUERY SELECT p_opptak_id, v_hjemmel.grunnlagstype, true;
END $$;

-- ---------------------------------------------------------------------
-- `m43_registrer_linje` — INGENTING BLE SAGT FØR VI SA HVA VI ER.
--
-- Døra nekter en linje datert FØR `identifisert_ts`. Det er den andre
-- halvdelen av `agenten_skjulte_at_den_er_automatisert`: kolonnen sier
-- AT vi identifiserte oss, denne døra sier at ingenting kom før.
--
-- TERSKELEN OPPGIS ALDRI AV KALLEREN. Døra leser den fra tenantens
-- krav og skriver den på raden: en kaller som fikk sette sin egen
-- kunne satt den til 1 og fått alt bekreftet.
-- ---------------------------------------------------------------------
CREATE FUNCTION m43_registrer_linje(p_tenant TEXT, p_linje_id UUID,
                                    p_samtale_id UUID, p_rekkefolge INT,
                                    p_taler TEXT, p_linje_ts TIMESTAMPTZ,
                                    p_tekst TEXT, p_kilde TEXT,
                                    p_sikkerhet_bp INT,
                                    p_retter_linje_id UUID, p_aktor TEXT)
RETURNS TABLE (linje_id UUID, terskel_bp INT, ubekreftet BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_samtale RECORD;
    v_krav RECORD;
    v_sikkerhet INT;
    v_ubekreftet BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm43_registrer_linje');
    SELECT * INTO v_samtale FROM public.samtale
     WHERE tenant = p_tenant AND samtale_id = p_samtale_id;
    IF v_samtale IS NULL THEN
        RAISE EXCEPTION 'm43_registrer_linje: ukjent samtale %',
            p_samtale_id;
    END IF;
    -- DEN VIKTIGSTE SETNINGEN I DENNE DØRA.
    IF p_linje_ts < v_samtale.identifisert_ts THEN
        RAISE EXCEPTION 'm43_registrer_linje: linjen er datert foer'
            ' agenten sa hva den er — ingenting ble sagt foer vi sa'
            ' hva vi er';
    END IF;
    SELECT * INTO v_krav FROM public.telefonikrav
     WHERE tenant = p_tenant ORDER BY kravversjon DESC LIMIT 1;
    IF v_krav IS NULL THEN
        RAISE EXCEPTION 'm43_registrer_linje: tenanten har ingen'
            ' telefonikrav — uten en terskel er merkingen en'
            ' tilfeldighet';
    END IF;
    -- ET MENNESKE SOM SKREV SELV, HØRTE IKKE FEIL.
    v_sikkerhet := CASE WHEN p_kilde = 'manuell' THEN 10000
                        ELSE p_sikkerhet_bp END;
    v_ubekreftet := v_sikkerhet < v_krav.sikkerhetsterskel_bp;
    IF p_retter_linje_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM public.transkripsjonslinje l
                        WHERE l.tenant = p_tenant
                          AND l.linje_id = p_retter_linje_id
                          AND l.samtale_id = p_samtale_id) THEN
        RAISE EXCEPTION 'm43_registrer_linje: linjen som rettes hoerer'
            ' ikke til denne samtalen';
    END IF;
    INSERT INTO public.transkripsjonslinje
        (tenant, linje_id, samtale_id, rekkefolge, taler, linje_ts,
         tekst, kilde, sikkerhet_bp, terskel_bp, ubekreftet,
         retter_linje_id, registrert_av)
    VALUES (p_tenant, p_linje_id, p_samtale_id, p_rekkefolge, p_taler,
            p_linje_ts, p_tekst, p_kilde, v_sikkerhet,
            v_krav.sikkerhetsterskel_bp, v_ubekreftet,
            p_retter_linje_id, p_aktor);
    PERFORM public.m43_evidens(p_tenant, p_linje_id, 'registrer_linje',
        p_aktor, jsonb_build_object('ubekreftet', v_ubekreftet,
                                    'kilde', p_kilde));
    RETURN QUERY SELECT p_linje_id, v_krav.sikkerhetsterskel_bp,
                        v_ubekreftet;
END $$;

-- ---------------------------------------------------------------------
-- `m43_eskaler` — REGELEN ER PÅKREVD, OG DEN MÅ GJELDE.
--
-- «Eskaleringsregler er kundens.» En eskalering på en AVVIKLET regel
-- ville vært modulens egen beslutning med et gammelt papir foran seg.
-- ---------------------------------------------------------------------
CREATE FUNCTION m43_eskaler(p_tenant TEXT, p_eskalering_id UUID,
                            p_samtale_id UUID, p_regel_id UUID,
                            p_begrunnelse TEXT, p_aktor TEXT)
RETURNS TABLE (eskalering_id UUID, mottaker TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_regel RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm43_eskaler');
    SELECT * INTO v_regel FROM public.eskaleringsregel
     WHERE tenant = p_tenant AND regel_id = p_regel_id;
    IF v_regel IS NULL THEN
        RAISE EXCEPTION 'm43_eskaler: ukjent eskaleringsregel % — en'
            ' eskalering uten en regel aa peke paa er modulens egen'
            ' beslutning', p_regel_id;
    END IF;
    IF NOT public.m43_regel_gjelder(v_regel.gyldig_fra,
                                    v_regel.gyldig_til,
                                    current_date) THEN
        RAISE EXCEPTION 'm43_eskaler: regelen % er avviklet',
            v_regel.beskrivelse;
    END IF;
    INSERT INTO public.eskalering
        (tenant, eskalering_id, samtale_id, regel_id, mottaker,
         begrunnelse, eskalert_av)
    VALUES (p_tenant, p_eskalering_id, p_samtale_id, p_regel_id,
            v_regel.mottaker, p_begrunnelse, p_aktor)
    -- CONSTRAINTEN VED NAVN: `eskalering_id` er OUT-parameter her.
    ON CONFLICT ON CONSTRAINT eskalering_pk DO NOTHING;
    PERFORM public.m43_evidens(p_tenant, p_eskalering_id, 'eskaler',
        p_aktor, jsonb_build_object('regel', p_regel_id,
                                    'mottaker', v_regel.mottaker));
    RETURN QUERY SELECT p_eskalering_id, v_regel.mottaker;
END $$;

CREATE FUNCTION m43_lukk_eskalering(p_tenant TEXT, p_eskalering_id UUID,
                                    p_utfall TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm43_lukk_eskalering');
    UPDATE public.eskalering e
       SET lukket_ts = now(), lukket_av = p_aktor,
           lukket_utfall = p_utfall
     WHERE e.tenant = p_tenant AND e.eskalering_id = p_eskalering_id
       AND e.lukket_ts IS NULL;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    PERFORM public.m43_evidens(p_tenant, p_eskalering_id,
        'lukk_eskalering', p_aktor,
        jsonb_build_object('utfall', p_utfall));
    RETURN true;
END $$;

-- =====================================================================
-- LESEDØRENE. SECURITY DEFINER, OG DERFOR EIET AV MODULROLLEN.
-- =====================================================================

-- `m43_transkripsjonen` — LINJENE MED SIN USIKKERHET.
--
-- TALLET OG TERSKELEN SOM GJALDT DA står side om side, og et rettet
-- punkt er SYNLIG SOM RETTET — ikke borte.
CREATE FUNCTION m43_transkripsjonen(p_tenant TEXT, p_samtale_id UUID)
RETURNS TABLE (linje_id UUID, rekkefolge INT, taler TEXT,
               linje_ts TIMESTAMPTZ, tekst TEXT, kilde TEXT,
               sikkerhet_bp INT, terskel_bp INT, ubekreftet BOOLEAN,
               retter_linje_id UUID, er_rettet BOOLEAN,
               registrert TIMESTAMPTZ, registrert_av TEXT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT l.linje_id, l.rekkefolge, l.taler, l.linje_ts, l.tekst,
           l.kilde, l.sikkerhet_bp, l.terskel_bp, l.ubekreftet,
           l.retter_linje_id,
           EXISTS (SELECT 1 FROM public.transkripsjonslinje r
                    WHERE r.tenant = l.tenant
                      AND r.retter_linje_id = l.linje_id),
           l.registrert, l.registrert_av
      FROM public.transkripsjonslinje l
     WHERE l.tenant = p_tenant AND l.samtale_id = p_samtale_id
     ORDER BY l.rekkefolge
$$;

-- `m43_samtaleregister` — samtalene med identifikasjonen SYNLIG.
CREATE FUNCTION m43_samtaleregister(p_tenant TEXT, p_maks INT)
RETURNS TABLE (samtale_id UUID, retning TEXT, motpart TEXT,
               startet_ts TIMESTAMPTZ, slutt_ts TIMESTAMPTZ,
               sekunder_til_identifikasjon INT,
               identifikasjonstekst TEXT, antall_linjer INT,
               antall_ubekreftede INT, antall_apne_eskaleringer INT,
               har_opptak BOOLEAN, opptakshjemmel TEXT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT s.samtale_id, s.retning, s.motpart, s.startet_ts, s.slutt_ts,
           extract(epoch FROM (s.identifisert_ts - s.startet_ts))::INT,
           s.identifikasjonstekst,
           (SELECT count(*)::INT FROM public.transkripsjonslinje l
             WHERE l.tenant = s.tenant AND l.samtale_id = s.samtale_id),
           (SELECT count(*)::INT FROM public.transkripsjonslinje l
             WHERE l.tenant = s.tenant AND l.samtale_id = s.samtale_id
               AND l.ubekreftet),
           (SELECT count(*)::INT FROM public.eskalering e
             WHERE e.tenant = s.tenant AND e.samtale_id = s.samtale_id
               AND e.lukket_ts IS NULL),
           o.opptak_id IS NOT NULL,
           -- DEN SOM SER AT EN SAMTALE BLE TATT OPP, SKAL SE HVORFOR
           -- DET VAR LOV — uten et klikk til.
           h.grunnlagstype
      FROM public.samtale s
      LEFT JOIN public.samtaleopptak o
        ON o.tenant = s.tenant AND o.samtale_id = s.samtale_id
      LEFT JOIN public.opptakshjemmel h
        ON h.tenant = o.tenant AND h.hjemmel_id = o.hjemmel_id
     WHERE s.tenant = p_tenant
     ORDER BY s.startet_ts DESC
     LIMIT greatest(p_maks, 1)
$$;

-- `m43_reglene` — kundens regler, med hvor mange eskaleringer de bar.
CREATE FUNCTION m43_reglene(p_tenant TEXT)
RETURNS TABLE (regel_id UUID, beskrivelse TEXT, mottaker TEXT,
               gyldig_fra DATE, gyldig_til DATE, gjelder BOOLEAN,
               antall_eskaleringer INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT r.regel_id, r.beskrivelse, r.mottaker, r.gyldig_fra,
           r.gyldig_til,
           public.m43_regel_gjelder(r.gyldig_fra, r.gyldig_til,
                                    current_date),
           (SELECT count(*)::INT FROM public.eskalering e
             WHERE e.tenant = r.tenant AND e.regel_id = r.regel_id)
      FROM public.eskaleringsregel r
     WHERE r.tenant = p_tenant
     ORDER BY r.gyldig_fra DESC
$$;

-- `m43_hjemlene` — den DELTE hjemmelen, lest med M-7s egen regel.
CREATE FUNCTION m43_hjemlene(p_tenant TEXT)
RETURNS TABLE (hjemmel_id UUID, grunnlagstype TEXT, beskrivelse TEXT,
               formal TEXT, gyldig_fra DATE, gyldig_til DATE,
               gjelder BOOLEAN, antall_opptak INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT h.hjemmel_id, h.grunnlagstype, h.beskrivelse, h.formal,
           h.gyldig_fra, h.gyldig_til,
           public.m7_hjemmel_gyldig(h.gyldig_fra, h.gyldig_til),
           (SELECT count(*)::INT FROM public.samtaleopptak o
             WHERE o.tenant = h.tenant AND o.hjemmel_id = h.hjemmel_id)
      FROM public.opptakshjemmel h
     WHERE h.tenant = p_tenant
     ORDER BY h.gyldig_fra DESC
$$;

-- `m43_eskaleringene` — med regelen som bar dem.
CREATE FUNCTION m43_eskaleringene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (eskalering_id UUID, samtale_id UUID, regel_id UUID,
               regeltekst TEXT, mottaker TEXT, begrunnelse TEXT,
               eskalert_ts TIMESTAMPTZ, eskalert_av TEXT,
               lukket_ts TIMESTAMPTZ, lukket_av TEXT,
               lukket_utfall TEXT, dogn_apen INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT e.eskalering_id, e.samtale_id, e.regel_id, r.beskrivelse,
           e.mottaker, e.begrunnelse, e.eskalert_ts, e.eskalert_av,
           e.lukket_ts, e.lukket_av, e.lukket_utfall,
           CASE WHEN e.lukket_ts IS NULL
                THEN (current_date
                      - (e.eskalert_ts AT TIME ZONE 'UTC')::DATE)::INT
                ELSE NULL END
      FROM public.eskalering e
      JOIN public.eskaleringsregel r
        ON r.tenant = e.tenant AND r.regel_id = e.regel_id
     WHERE e.tenant = p_tenant
     ORDER BY e.lukket_ts IS NULL DESC, e.eskalert_ts DESC
     LIMIT greatest(p_maks, 1)
$$;

-- `m43_telefonifunn` — med hvem som kan lukke hvert av dem.
CREATE FUNCTION m43_telefonifunn(p_tenant TEXT, p_maks INT)
RETURNS TABLE (funn_id UUID, funntype TEXT, referanse UUID,
               detaljer TEXT, over_grense BIGINT, apen BOOLEAN,
               forst_sett TIMESTAMPTZ, sist_sett TIMESTAMPTZ,
               lukket_av TEXT, kan_lukkes BOOLEAN)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT f.funn_id, f.funntype, f.referanse, f.detaljer,
           f.over_grense, f.apen, f.forst_sett, f.sist_sett, f.lukket_av,
           NOT public.m43_funn_er_sveipens(f.funntype)
      FROM public.telefonifunn f
     WHERE f.tenant = p_tenant
     ORDER BY f.apen DESC, f.sist_sett DESC
     LIMIT greatest(p_maks, 1)
$$;

-- `m43_bildet` — hele modulen i ett kall.
CREATE FUNCTION m43_bildet(p_tenant TEXT)
RETURNS TABLE (samtaler INT, apne_samtaler INT, linjer INT,
               ubekreftede INT, opptak INT, hjemler INT,
               gyldige_hjemler INT, regler INT, gjeldende_regler INT,
               eskaleringer INT, apne_eskaleringer INT, apne_funn INT,
               tregeste_identifikasjon_sek INT, har_krav BOOLEAN,
               sikkerhetsterskel_bp INT, identifikasjonsfrist_sek INT,
               eskaleringsfrist_dogn INT, samtaletak_timer INT,
               kravversjon INT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    WITH k AS (
        SELECT * FROM public.telefonikrav
         WHERE tenant = p_tenant ORDER BY kravversjon DESC LIMIT 1)
    SELECT (SELECT count(*)::INT FROM public.samtale
             WHERE tenant = p_tenant),
           (SELECT count(*)::INT FROM public.samtale
             WHERE tenant = p_tenant AND slutt_ts IS NULL),
           (SELECT count(*)::INT FROM public.transkripsjonslinje
             WHERE tenant = p_tenant),
           (SELECT count(*)::INT FROM public.transkripsjonslinje
             WHERE tenant = p_tenant AND ubekreftet),
           (SELECT count(*)::INT FROM public.samtaleopptak
             WHERE tenant = p_tenant),
           (SELECT count(*)::INT FROM public.opptakshjemmel
             WHERE tenant = p_tenant),
           (SELECT count(*)::INT FROM public.opptakshjemmel h
             WHERE h.tenant = p_tenant
               AND public.m7_hjemmel_gyldig(h.gyldig_fra, h.gyldig_til)),
           (SELECT count(*)::INT FROM public.eskaleringsregel
             WHERE tenant = p_tenant),
           (SELECT count(*)::INT FROM public.eskaleringsregel r
             WHERE r.tenant = p_tenant
               AND public.m43_regel_gjelder(r.gyldig_fra, r.gyldig_til,
                                            current_date)),
           (SELECT count(*)::INT FROM public.eskalering
             WHERE tenant = p_tenant),
           (SELECT count(*)::INT FROM public.eskalering
             WHERE tenant = p_tenant AND lukket_ts IS NULL),
           (SELECT count(*)::INT FROM public.telefonifunn
             WHERE tenant = p_tenant AND apen),
           -- DET DYRESTE TALLET I MODULEN: den lengste tiden noen
           -- snakket med en maskin uten å vite det.
           (SELECT max(extract(epoch FROM
                (s.identifisert_ts - s.startet_ts)))::INT
              FROM public.samtale s WHERE s.tenant = p_tenant),
           (SELECT count(*) > 0 FROM k),
           (SELECT sikkerhetsterskel_bp FROM k),
           (SELECT identifikasjonsfrist_sek FROM k),
           (SELECT eskaleringsfrist_dogn FROM k),
           (SELECT samtaletak_timer FROM k),
           (SELECT kravversjon FROM k)
$$;

-- `m43_lukk_funn` — OG DEN NEKTER PÅ SVEIPENS EGNE.
CREATE FUNCTION m43_lukk_funn(p_tenant TEXT, p_funn_id UUID,
                              p_grunn TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE v_type TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm43_lukk_funn');
    SELECT funntype INTO v_type FROM public.telefonifunn
     WHERE tenant = p_tenant AND funn_id = p_funn_id FOR UPDATE;
    IF v_type IS NULL THEN
        RAISE EXCEPTION 'm43_lukk_funn: ukjent funn %', p_funn_id;
    END IF;
    IF public.m43_funn_er_sveipens(v_type) THEN
        RAISE EXCEPTION 'm43_lukk_funn: % lukkes av at tilstanden'
            ' opphoerer, ikke av at noen huker av', v_type;
    END IF;
    UPDATE public.telefonifunn f
       SET apen = false, lukket_ts = now(), lukket_av = p_aktor,
           lukket_grunn = p_grunn
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id AND f.apen;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    PERFORM public.m43_evidens(p_tenant, p_funn_id, 'lukk_funn',
        p_aktor, jsonb_build_object('type', v_type));
    RETURN true;
END $$;

-- =====================================================================
-- `m43_sveip_telefoni` — SVEIPEN RINGER INGEN, LUKKER INGEN
-- ESKALERING OG AVSLUTTER INGEN SAMTALE. Den sier fra, og der stopper
-- den.
--
-- ÉN TENANT OM GANGEN (130s lærdom). En sveip uten `disponit.tenant`
-- ville sett NULL RADER under FORCE RLS og meldt null funn — med grønn
-- exit-kode.
--
-- TO AV TRE LUKKES HERFRA. `samtale_uten_avslutning` forsvinner når
-- samtalen avsluttes, `eskalering_over_frist` når den lukkes.
-- `ubekreftet_linje_uavklart` KAN lukkes av et menneske, og 125/126s
-- vakt sørger for at den lukkingen står natten over.
-- =====================================================================
CREATE FUNCTION m43_sveip_telefoni(p_maks_tenanter INT)
RETURNS TABLE (tenanter INT, nye INT, oppdaterte INT, lukket INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    v_t TEXT;
    v_antall INT := 0;
    v_nye INT := 0;
    v_oppdaterte INT := 0;
    v_lukket INT := 0;
    v_n INT;
    v_n2 INT;
    v_n3 INT;
    v_krav RECORD;
BEGIN
    PERFORM set_config('disponit.tenant', '', true);
    FOR v_t IN
        SELECT DISTINCT tenant FROM public.telefonikrav
         ORDER BY tenant LIMIT greatest(p_maks_tenanter, 1)
    LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        v_antall := v_antall + 1;
        SELECT * INTO v_krav FROM public.telefonikrav
         WHERE tenant = v_t ORDER BY kravversjon DESC LIMIT 1;

        -- 1. EN SAMTALE SOM ALDRI BLE AVSLUTTET.
        --    En hengende integrasjon etterlater samtalen åpen, og en
        --    åpen samtale er et opptak som formelt fortsatt går.
        WITH treff AS (
            SELECT s.samtale_id,
                   (extract(epoch FROM (now() - s.startet_ts))
                    / 3600)::BIGINT AS timer
              FROM public.samtale s
             WHERE s.tenant = v_t AND s.slutt_ts IS NULL
               AND s.startet_ts < now()
                   - make_interval(hours => v_krav.samtaletak_timer)),
        satt AS (
            INSERT INTO public.telefonifunn
                (tenant, funn_id, funntype, referanse, detaljer,
                 over_grense)
            SELECT v_t, gen_random_uuid(), 'samtale_uten_avslutning',
                   t.samtale_id,
                   format('samtalen har staatt aapen i %s timer',
                          t.timer), t.timer
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) DO UPDATE
                SET sist_sett = now(), apen = true,
                    detaljer = EXCLUDED.detaljer,
                    over_grense = EXCLUDED.over_grense,
                    lukket_ts = NULL, lukket_av = NULL, lukket_grunn = NULL
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.telefonifunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm43_sveip',
                   lukket_grunn = 'samtalen er avsluttet'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'samtale_uten_avslutning'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.samtale_id = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 2. EN ESKALERING INGEN TOK.
        --    Modulen vekket et menneske etter kundens egen regel, og
        --    så skjedde det ingenting. Det er den dyreste stillheten i
        --    modulen: den andre parten fikk beskjed om at noen skulle
        --    ta over.
        WITH treff AS (
            SELECT e.eskalering_id,
                   (current_date
                    - (e.eskalert_ts AT TIME ZONE 'UTC')::DATE)::BIGINT
                   AS dogn
              FROM public.eskalering e
             WHERE e.tenant = v_t AND e.lukket_ts IS NULL
               AND (e.eskalert_ts AT TIME ZONE 'UTC')::DATE
                   + v_krav.eskaleringsfrist_dogn < current_date),
        satt AS (
            INSERT INTO public.telefonifunn
                (tenant, funn_id, funntype, referanse, detaljer,
                 over_grense)
            SELECT v_t, gen_random_uuid(), 'eskalering_over_frist',
                   t.eskalering_id,
                   format('eskaleringen har staatt aapen i %s doegn',
                          t.dogn), t.dogn
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) DO UPDATE
                SET sist_sett = now(), apen = true,
                    detaljer = EXCLUDED.detaljer,
                    over_grense = EXCLUDED.over_grense,
                    lukket_ts = NULL, lukket_av = NULL, lukket_grunn = NULL
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.telefonifunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm43_sveip',
                   lukket_grunn = 'eskaleringen er lukket'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'eskalering_over_frist'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.eskalering_id = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 3. EN UBEKREFTET LINJE SOM VERKEN ER RETTET ELLER AVKLART.
        --    DENNE KAN ET MENNESKE LUKKE — «vi har hoert opptaket, det
        --    stemmer» er en avklaring med et navn paa. Sveipen
        --    gjenaapner den derfor IKKE naar den er lukket (131s
        --    laerdom): `NOT EXISTS`-leddet paa lukkede funn er hele
        --    forskjellen.
        WITH treff AS (
            SELECT l.linje_id, l.sikkerhet_bp::BIGINT AS bp
              FROM public.transkripsjonslinje l
             WHERE l.tenant = v_t AND l.ubekreftet
               AND NOT EXISTS (
                   SELECT 1 FROM public.transkripsjonslinje r
                    WHERE r.tenant = v_t
                      AND r.retter_linje_id = l.linje_id)),
        satt AS (
            INSERT INTO public.telefonifunn
                (tenant, funn_id, funntype, referanse, detaljer,
                 over_grense)
            SELECT v_t, gen_random_uuid(), 'ubekreftet_linje_uavklart',
                   t.linje_id,
                   format('hoert med %s basispunkters sikkerhet', t.bp),
                   t.bp
              FROM treff t
             WHERE NOT EXISTS (
                   SELECT 1 FROM public.telefonifunn f
                    WHERE f.tenant = v_t AND NOT f.apen
                      AND f.funntype = 'ubekreftet_linje_uavklart'
                      AND f.referanse = t.linje_id)
            ON CONFLICT (tenant, funntype, referanse) DO UPDATE
                SET sist_sett = now()
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.telefonifunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm43_sveip',
                   lukket_grunn = 'linjen er rettet'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'ubekreftet_linje_uavklart'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.linje_id = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
    RETURN QUERY SELECT v_antall, v_nye, v_oppdaterte, v_lukket;
END $$;

-- =====================================================================
-- RETTIGHETENE. SP-7: KJØRETIDEN NÅR DØRENE OG INGENTING ANNET.
--
-- FØRST RIVES ALT FRA `PUBLIC`. Postgres gir EXECUTE til PUBLIC på
-- hver nye funksjon; uten denne løkka når SVEIPEROLLEN alle dørene i
-- modulen. Eierskapsleddet er ikke pynt: et REVOKE fra en rolle som
-- ikke eier funksjonen AVBRYTER migrasjonen — migrators FEM vakter er
-- revokert der de lages.
-- =====================================================================
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT p.oid::regprocedure AS sig
               FROM pg_proc p
              WHERE p.pronamespace = 'public'::regnamespace
                AND p.proname LIKE 'm43\_%'
                AND pg_get_userbyid(p.proowner) = current_user
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', r.sig);
    END LOOP;
END $$;

GRANT EXECUTE ON FUNCTION m43_sett_krav(TEXT, INT, INT, INT, INT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m43_registrer_hjemmel(TEXT, UUID, TEXT, TEXT,
    TEXT, DATE, DATE, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m43_registrer_regel(TEXT, UUID, TEXT, TEXT,
    DATE, DATE, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m43_avvikle_regel(TEXT, UUID, DATE, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m43_start_samtale(TEXT, UUID, TEXT, TEXT,
    TIMESTAMPTZ, TIMESTAMPTZ, TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m43_avslutt_samtale(TEXT, UUID, TIMESTAMPTZ,
    TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m43_start_opptak(TEXT, UUID, UUID, UUID,
    TIMESTAMPTZ, TEXT, TEXT[], TIMESTAMPTZ, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m43_registrer_linje(TEXT, UUID, UUID, INT,
    TEXT, TIMESTAMPTZ, TEXT, TEXT, INT, UUID, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m43_eskaler(TEXT, UUID, UUID, UUID, TEXT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m43_lukk_eskalering(TEXT, UUID, TEXT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m43_transkripsjonen(TEXT, UUID) TO disponit;
GRANT EXECUTE ON FUNCTION m43_samtaleregister(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m43_reglene(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m43_hjemlene(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m43_eskaleringene(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m43_telefonifunn(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m43_bildet(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m43_lukk_funn(TEXT, UUID, TEXT, TEXT) TO disponit;

-- SVEIPEROLLEN NÅR ÉN FUNKSJON, OG BARE DEN (111s form).
GRANT EXECUTE ON FUNCTION m43_sveip_telefoni(INT) TO disponit_telefonisveip;

RESET ROLE;

-- =====================================================================
-- M-36s FUNNKATALOG (132). 133/134s LÆRDOM, GJENTATT UTEN Å BLI
-- STOPPET. Raden alene er bare en lovnad — lesretten innfrir den.
-- =====================================================================
INSERT INTO m36_funnregister
    (relasjon, modul, typekolonne, apenform, begrunnelse)
VALUES
    ('telefonifunn', 'm43_telefoni', 'funntype', 'apen_kolonne',
     'husets form')
ON CONFLICT (relasjon) DO NOTHING;
GRANT SELECT ON telefonifunn TO disponit_optimalisator_eier;
