-- 098: M-22 SaaS- og lisensagent v1 — LISENSREGISTERET og
-- utløpsvarslene. Tre tenant-skopede tabeller, seks dører og ÉN sveip som
-- kjøres som FORPASS i varselsenderen.
--
-- M-22 ER M-21s TVILLING, og det er med vilje: samme fristform, samme
-- forpass, samme idempotensanker. 096 er lest ordrett, og der M-22
-- avviker, står avviket navngitt under. En ny form ville bare vært en ny
-- å lære — og to nesten like former er verre enn to like.
--
-- V1-AVGRENSNINGEN, ORDRETT FRA MANIFESTET: katalogteksten lover også at
-- modulen NEDGRADERER eller AVSLUTTER abonnementer automatisk etter målt
-- inaktivitet. Katalogens EGEN guard sier hvorfor det ikke kan være v1:
-- «kritiske systemer og legal hold ekskluderes; tilgang kan gjenopprettes
-- i angreperioden». En modul som fjerner lisenser trenger altså BÅDE et
-- unntaksregister, en angrefrist og en gjenopprettingsvei — tre
-- mekanismer som ikke finnes. Og bruksmålingen («ubrukte lisenser»)
-- krever innloggingsdata fra SSO, en datakilde vi ikke har.
--
-- v1 måler derfor det den KAN vite: hva vi betaler for, hvem som eier
-- det, og hva som løper ut. Den fjerner ingenting, og det finnes ingen
-- kodevei her som setter `status` til noe som helst uten en navngitt
-- aktør bak.
--
-- FIRE DOMMER v1 hviler på, alle håndhevet i DATAMODELLEN og ikke i et
-- API-lag som kunne omgås:
--
--   1. EN LISENS UTEN EIER ER URESPRESENTERBAR. Det er samme dom som
--      M-21s, og av samme grunn: en lisens ingen eier er en lisens ingen
--      sier opp, ingen forhandler og ingen tør røre. NOT NULL med
--      fremmednøkkel mot `brukeridentitet`, ikke en rapport.
--
--   2. MODULEN AVSLUTTER INGENTING. `status='avsluttet'` krever en
--      skreven begrunnelse (CHECK), og enhver statusovergang krever en
--      NAVNGITT AKTØR i sesjonen (vakten i §2). Sveipen rører ikke
--      statuskolonnen i det hele tatt — den KØER VARSEL, og ikke noe
--      mer. Det er invarianten `lisens_avsluttet_av_modulen`, og den er
--      målt både statisk (ingen UPDATE av status i sveipveien) og
--      funksjonelt (en sveip endrer ingen lisensrad).
--
--   3. VARSLINGSPUNKTENE REGNES FRA OPPSIGELSESFRISTEN, IKKE FRA
--      FORNYELSESDATOEN. Dette er den ene tingen som skiller M-22 fra en
--      generisk fristmodul, og hele grunnen til at modulen finnes: et
--      abonnement med 90 døgns oppsigelsesfrist må varsles 90+ døgn før
--      fornyelse. Et varsel 30 døgn før en fornyelse man ikke lenger kan
--      komme ut av, er ikke et varsel — det er en regning som
--      annonserer seg selv.
--
--      `beslutningsdato` er derfor en GENERERT kolonne,
--      `fornyelsesdato - COALESCE(oppsigelsesfrist_dogn, 0)`: den siste
--      dagen et menneske faktisk KAN velge. Hvert varslingspunkt fyrer
--      på `beslutningsdato - dogn_for <= current_date`. Regnet fra
--      fornyelsesdatoen alene ville en 90-døgnslisens med 60 døgn igjen
--      ikke fått ETT eneste varsel før fristen var ute — og porten
--      `test_oppsigelsesfristen_flytter_varslingspunktet` er rød i
--      nøyaktig den formen.
--
--   4. VARSELET ER IDEMPOTENT PER (lisens, varslingspunkt,
--      fornyelsesdato). `lisensvarsel_sendt` er ankeret, og køingen skjer
--      i SAMME TRANSAKSJON som ankerraden: et varsel uten anker, eller et
--      anker uten varsel, er urepresenterbart. `fornyelsesdato` er med i
--      nøkkelen fordi PERIODEN, ikke lisensen, er det som varsles — uten
--      leddet ville en fornyet lisens aldri fått varsel om neste periode.
--
-- HVOR M-22 BEVISST AVVIKER FRA 096, og hvorfor:
--
--   * `fornyelsesdato` er DATE, ikke TIMESTAMPTZ. Et abonnement fornyes
--     på en KALENDERDAG — det står slik i avtalen, og ingen leverandør
--     oppgir et klokkeslett. En timestamptz her ville krevd at flaten
--     fant på et tidspunkt, og to kolleger i to tidssoner ville sett to
--     ulike frister for den samme avtalen. Ankerets nøkkel arver
--     dagpresisjonen, og det er nettopp derfor `hendelse`-leddet i §4
--     kan nøye seg med `YYYY-MM-DD` der 096 måtte ned på mikrosekund.
--
--   * DET FINNES INGEN KVITTERINGSDØR. M-21 lukker en frist med et
--     bevis for at noe ble LEVERT; en lisens leveres ikke, den løper —
--     og den avsluttes ved et vedtak. `m22_marker_avsluttet` er derfor
--     den eneste terminale veien, og den koster en begrunnelse av samme
--     grunn som M-21s bortfall gjør det: uten den ville «avsluttet»
--     vært en gratis vei ut av enhver kostnad, og registeret en liste
--     over ting man kan klikke bort. En avsluttet lisens uten
--     begrunnelse er dessuten en rad som blir gåtefull om et år.
--
--   * FORNYELSEN ER EN EGEN DØR, ikke en rulling i lukkedøren. M-21
--     ruller fristen når forekomsten kvitteres ut, fordi kvitteringen ER
--     hendelsen. En lisens har ingen slik hendelse: den bare fornyes, og
--     hvem som helst kunne ikke visst når. `m22_registrer_fornyelse`
--     skriver den nye perioden når et menneske vet den — og den er den
--     ENESTE veien fornyelsesdatoen flyttes. SVEIPEN FLYTTER DEN ALDRI:
--     en sveip som rullet datoen selv ville vært modulen som endrer en
--     lisensrad, altså dom 2 brutt i den ene retningen ingen tenker på.
--
--   * IKKE-TOMHET MÅLES MED `~ '[^[:space:]]'`, ikke med
--     `length(btrim(x)) > 0`. `btrim` trimmer bare mellomrom: en `kilde`
--     som består av en tabulator slipper gjennom 096-formen og er like
--     tom for et menneske. Formen her krever at det finnes MINST ETT
--     tegn som ikke er blankt.
--
-- SVEIPEN ER ET FORPASS, IKKE EN NY TIMER. `m22_koe_utlopsvarsler`
-- kalles fra `platform/drift/varselsender.py` sitt pre-pass ved siden av
-- M-21s, av samme grunn: senderen er den ene timerdrevne prosessen som
-- allerede eier varselkøens rytme, backoff og idempotens. En ny
-- varslingsvei er en ny vei å miste et varsel i. Prisen er invarianten
-- `forpass_stanset_ordinaer_sending`: forpasset kjører i SIN EGEN
-- transaksjon, med sin egen `conn.rollback()` og sin egen feilteller —
-- og M-22s forpass kan derfor heller ikke stanse M-21s. M-22 har av
-- samme grunn INGEN egen LOGIN-rolle og ingen egen systemd-enhet:
-- sveipen kjører som `disponit_varselsender`, og den rollen får nøyaktig
-- ÉN ny EXECUTE og ingen tabellrettigheter.
--
-- FORMENE ER HUSETS (089/090/091/096): tabellene eies av migrator, dørene
-- av NOLOGIN-rollen `disponit_lisens_eier` (registrert i
-- `deploy/staging/eierskap-reparasjon.sql`), tenant TEXT + RLS
-- ENABLE+FORCE + `tenant_isolasjon` på hver tabell, SP-1
-- (`krev_tenantkontekst` FØRST) i hver tenantbundet definer, og INGEN
-- BYPASSRLS: kryss-tenant-autoriteten sveipen trenger er en EKSPLISITT,
-- SNEVER policy (§1) og ikke en rolleegenskap.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_lisens_eier') THEN
        RAISE EXCEPTION 'rollen disponit_lisens_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `lisens` — abonnementet selv. Én rad per lisens per tenant.
--
-- `fornyelsesdato` er DEN GJELDENDE periodens fornyelse. Den flyttes
-- FRAMOVER av `m22_registrer_fornyelse` (§3) når et menneske vet at
-- lisensen løper videre, og den er derfor med i ankerets nøkkel: en ny
-- periode er et NYTT sett varslingspunkter å fyre på.
CREATE TABLE lisens (
    tenant TEXT NOT NULL CHECK (tenant ~ '[^[:space:]]'),
    lisens_id UUID NOT NULL,
    -- HVEM VI BETALER, og HVA vi betaler for. Begge er ikke-tomme: en
    -- lisens uten leverandør er en kostnad uten motpart, og en uten
    -- produkt er en linje i regnskapet ingen kan vurdere.
    leverandor TEXT NOT NULL CHECK (leverandor ~ '[^[:space:]]'),
    produkt TEXT NOT NULL CHECK (produkt ~ '[^[:space:]]'),
    -- DOM 1. NOT NULL + FK mot identiteten, ikke mot medlemskapet: mister
    -- eieren medlemskapet skal lisensen fortsatt ha en eier å navngi (og
    -- registerets lesedør viser den), men raden skal ikke rives ut under
    -- en løpende avtale. Samme valg som `plikt.eier_bruker_id` (096) og
    -- `varsel.bruker_id` (026).
    eier_bruker_id TEXT NOT NULL REFERENCES brukeridentitet (bruker_id),
    -- Setene og kostnaden er VALGFRIE, og det er en ærlighet og ikke en
    -- slapphet: en lisens registreres ofte før noen har funnet fakturaen.
    -- Et påkrevd kostnadsfelt ville gjort at folk skrev 0, og 0 er en
    -- LØGN der NULL er en opplysning om at vi ikke vet.
    antall_seter INT CHECK (antall_seter > 0),
    kostnad_aar NUMERIC(14, 2) CHECK (kostnad_aar >= 0),
    -- Lukket sett. Et fritekstfelt her ville gjort «NOK», «nok» og «kr»
    -- til tre valutaer i den samme summeringen.
    valuta TEXT CHECK (valuta IN ('NOK', 'EUR', 'USD', 'GBP', 'SEK',
                                  'DKK', 'CHF')),
    fornyelsesdato DATE NOT NULL,
    fornyelsestype TEXT NOT NULL DEFAULT 'automatisk'
        CHECK (fornyelsestype IN ('automatisk', 'manuell', 'engang')),
    -- DOM 3. Antall døgn før fornyelsen oppsigelsen må være inne. NULL
    -- betyr «ingen frist avtalt» — ikke null døgn, men at avtalen ikke
    -- sier noe; `beslutningsdato` faller da sammen med fornyelsesdatoen,
    -- som er den ærlige avlesningen av en avtale uten oppsigelsesfrist.
    oppsigelsesfrist_dogn INT
        CHECK (oppsigelsesfrist_dogn >= 0 AND oppsigelsesfrist_dogn <= 3650),
    -- SISTE DAG NOEN FAKTISK KAN VELGE. Generert og lagret, ikke regnet i
    -- hver spørring: det er kolonnen sveipen filtrerer på og flaten
    -- sorterer etter, og en avledning som gjentas fem steder er en
    -- avledning som før eller siden er fem forskjellige.
    --
    -- `date - integer` er IMMUTABLE i PostgreSQL, så uttrykket er lovlig
    -- i en STORED-kolonne og kan indekseres.
    beslutningsdato DATE GENERATED ALWAYS AS
        (fornyelsesdato - COALESCE(oppsigelsesfrist_dogn, 0)) STORED,
    -- HJEMMELEN. Henvisningen til avtalen, ordrenummeret eller
    -- fakturaen lisensen kommer av. Ikke-tom med vilje: en lisens uten
    -- kilde er en påstand, og et utløpsvarsel på en påstand er støy.
    kilde TEXT NOT NULL CHECK (kilde ~ '[^[:space:]]'),
    status TEXT NOT NULL DEFAULT 'aktiv'
        CHECK (status IN ('aktiv', 'avsluttet')),
    avslutt_begrunnelse TEXT,
    avsluttet_ts TIMESTAMPTZ,
    avsluttet_av TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    CONSTRAINT lisens_pk PRIMARY KEY (tenant, lisens_id),
    -- DOM 2, i skjemaet: en lisens kan ikke STÅ som avsluttet uten at det
    -- finnes en begrunnelse, et tidspunkt og et menneske bak. Dette er
    -- halvparten av invarianten `lisens_avsluttet_av_modulen` — den
    -- gjelder ENHVER skrivevei, også direkte DML som eier, og en sveip
    -- som skulle avslutte fordi bruken var lav har ingenting å skrive i
    -- noen av de tre feltene.
    CONSTRAINT lisens_avsluttet_krever_begrunnelse CHECK (
        status <> 'avsluttet'
        OR (avslutt_begrunnelse IS NOT NULL
            AND avslutt_begrunnelse ~ '[^[:space:]]'
            AND avsluttet_ts IS NOT NULL AND avsluttet_av IS NOT NULL)),
    -- Et beløp uten valuta er ikke et beløp. Den andre veien er derimot
    -- lovlig: en avtale kan være i EUR uten at noen har funnet summen.
    CONSTRAINT lisens_kostnad_krever_valuta CHECK (
        kostnad_aar IS NULL OR valuta IS NOT NULL)
);

-- Sveipens skann og flatens liste leser begge «aktive lisenser,
-- beslutningsdato først». Delindeks på `aktiv`: avsluttede lisenser er
-- historikk, og sveipen skal aldri betale for dem.
CREATE INDEX lisens_aktiv_beslutning ON lisens (tenant, beslutningsdato)
    WHERE status = 'aktiv';

-- `lisensvarsling` — varslingspunktene. `dogn_for` er antall døgn før
-- BESLUTNINGSDATOEN punktet utløser på — ikke før fornyelsen. Seedes med
-- husets standard (60/30/7) ved registrering og kan overstyres per lisens
-- i samme kall.
CREATE TABLE lisensvarsling (
    tenant TEXT NOT NULL,
    lisens_id UUID NOT NULL,
    dogn_for INT NOT NULL CHECK (dogn_for >= 0 AND dogn_for <= 3650),
    CONSTRAINT lisensvarsling_pk PRIMARY KEY (tenant, lisens_id, dogn_for),
    CONSTRAINT lisensvarsling_lisens_fk FOREIGN KEY (tenant, lisens_id)
        REFERENCES lisens (tenant, lisens_id)
);

-- `lisensvarsel_sendt` — IDEMPOTENSANKERET (dom 4), M-21s form ordrett.
-- PK-en bærer `fornyelsesdato` fordi PERIODEN, ikke lisensen, er det som
-- varsles: uten leddet ville en lisens fått varsel om FØRSTE periode og
-- aldri om de neste, og en fornyet avtale ville arvet den forrige
-- periodens taushet.
--
-- Raden skrives i SAMME TRANSAKSJON som `varsel`-raden den svarer til.
-- Append-only (vakten i §2): et anker som kan slettes er et anker som
-- kan varsle på nytt.
CREATE TABLE lisensvarsel_sendt (
    tenant TEXT NOT NULL,
    lisens_id UUID NOT NULL,
    dogn_for INT NOT NULL,
    fornyelsesdato DATE NOT NULL,
    koet_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Peker på `varsel.id`. Ingen fremmednøkkel med vilje: varsler er
    -- driftsdata som skal kunne ryddes (026), mens ankeret er
    -- idempotensen og skal overleve ryddingen. En FK ville gjort
    -- varselryddingen til en vei tilbake til dobbeltvarsling.
    varsel_ref BIGINT NOT NULL,
    CONSTRAINT lisensvarsel_sendt_pk
        PRIMARY KEY (tenant, lisens_id, dogn_for, fornyelsesdato),
    CONSTRAINT lisensvarsel_sendt_lisens_fk FOREIGN KEY (tenant, lisens_id)
        REFERENCES lisens (tenant, lisens_id)
);

-- ------------------------------------------------------------
-- 2. Radvaktene og radsikkerheten.
-- ------------------------------------------------------------

-- Vakten på `lisens`. Fire regler, og den tredje er DOM 2:
--
--   * DELETE avvises. Et lisensregister der rader kan forsvinne er et
--     register ingen revisjon — og ingen innkjøper — kan lese bakover.
--   * Identiteten er frosset (tenant, lisens_id, opprettet, leverandor,
--     produkt, kilde). En annen leverandør eller et annet produkt er en
--     ANNEN lisens, ikke en redigering av denne.
--   * OPPSIGELSESFRISTEN ER OGSÅ FROSSET, og det er ikke pynt:
--     `beslutningsdato` er avledet av den, mens ankerets nøkkel bærer
--     `fornyelsesdato`. Ble fristen endret, ville beslutningsdatoen
--     flyttet seg BAK ankre som alt er skrevet, og punktene for den
--     gjeldende perioden ville vært tause for en frist ingen hadde
--     varslet om. En reforhandlet oppsigelsesfrist er en ny avtale og
--     registreres som en ny lisens — det er også slik den ser ut i
--     leverandørens papirer.
--   * EN STATUSOVERGANG ER FORFATTET, ALDRI AVLEDET. Enhver endring av
--     `status` krever en navngitt aktør i sesjonen (`disponit.aktor`), og
--     den aktøren MÅ være den som står i `avsluttet_av`. En jobb som
--     skulle avslutte en lisens fordi bruken var lav har ingen aktør å
--     skrive — og skrev den en, ville navnet stått i raden for enhver som
--     leser. `avsluttet` er terminal: det finnes ingen vei ut.
--   * `fornyelsesdato` kan bare flyttes FRAMOVER. Det er fornyelsen (§3)
--     og korreksjonen av en feilskrevet dato — aldri en vei til å skyve
--     en periode bak et anker som alt er skrevet.
CREATE FUNCTION m22_lisens_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT;
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'lisens: % avvist — lisenser avsluttes, de slettes'
            ' aldri som rader', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.lisens_id IS DISTINCT FROM OLD.lisens_id
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.leverandor IS DISTINCT FROM OLD.leverandor
       OR NEW.produkt IS DISTINCT FROM OLD.produkt
       OR NEW.kilde IS DISTINCT FROM OLD.kilde THEN
        RAISE EXCEPTION 'lisens: identiteten (tenant, lisens_id, opprettet,'
            ' leverandor, produkt, kilde) er frosset — et annet produkt er'
            ' en annen lisens'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.oppsigelsesfrist_dogn IS DISTINCT FROM OLD.oppsigelsesfrist_dogn
    THEN
        RAISE EXCEPTION 'lisens: oppsigelsesfristen er frosset —'
            ' beslutningsdatoen er avledet av den, og en endret frist ville'
            ' flyttet den bak ankre som alt er skrevet'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.fornyelsesdato < OLD.fornyelsesdato THEN
        RAISE EXCEPTION 'lisens: fornyelsesdatoen kan bare flyttes framover'
            ' — en dato som flyttes bakover gjemmer seg bak et anker som'
            ' alt er skrevet'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF OLD.status <> 'aktiv' THEN
            RAISE EXCEPTION 'lisens: % er terminal — en avsluttet lisens'
                ' gjenåpnes ikke', OLD.status
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL THEN
            RAISE EXCEPTION 'lisens: en statusovergang krever en navngitt'
                ' aktør (disponit.aktor) — modulen avslutter ingenting'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.status = 'avsluttet'
           AND NEW.avsluttet_av IS DISTINCT FROM v_aktor THEN
            RAISE EXCEPTION 'lisens: avsluttet_av (%) er ikke aktøren som'
                ' avslutter (%)', coalesce(NEW.avsluttet_av, '<null>'),
                v_aktor USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m22_lisens_vakt() FROM PUBLIC;
CREATE TRIGGER m22_lisens_vakt
    BEFORE UPDATE OR DELETE ON lisens
    FOR EACH ROW EXECUTE FUNCTION m22_lisens_vakt();
CREATE TRIGGER m22_lisens_ingen_truncate
    BEFORE TRUNCATE ON lisens
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- Ankeret er append-only mot BÅDE UPDATE og DELETE. Det er hele
-- idempotensen: kunne raden fjernes, kunne varselet køes på nytt.
CREATE FUNCTION m22_anker_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'lisensvarsel_sendt er append-only: % er forbudt —'
        ' ankeret ER idempotensen', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE ALL ON FUNCTION m22_anker_vakt() FROM PUBLIC;
CREATE TRIGGER m22_anker_vakt
    BEFORE UPDATE OR DELETE ON lisensvarsel_sendt
    FOR EACH ROW EXECUTE FUNCTION m22_anker_vakt();
CREATE TRIGGER m22_anker_ingen_truncate
    BEFORE TRUNCATE ON lisensvarsel_sendt
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

ALTER TABLE lisens ENABLE ROW LEVEL SECURITY;
ALTER TABLE lisens FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON lisens
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — ingen BYPASSRLS.
-- 096s policy ordrett, med M-22s eier.
--
-- Sveipen må finne HVILKE tenanter som har en beslutningsdato på vei, og
-- det spørsmålet kan ikke stilles innenfra én tenantkontekst.
-- Autoriteten er derfor en policy, ikke en rolleegenskap, og den er
-- gjerdet tre ganger:
--
--   * bare `disponit_lisens_eier` (dørenes eier — ingen LOGIN-rolle),
--   * bare SELECT (sveipen SKRIVER aldri kryss-tenant: hver innsetting
--     skjer etter at konteksten er bundet til RADENS tenant),
--   * bare når det IKKE står en tenantkontekst i sesjonen.
--
-- Det siste leddet er det bærende. Dørene i §3 kommer alltid gjennom
-- `krev_tenantkontekst`, som fail-closed krever en ikke-tom kontekst —
-- inne i en dør er denne policyen derfor ALLTID usann, og
-- `tenant_isolasjon` er den eneste som gjelder. De to er disjunkte per
-- konstruksjon, så kryss-tenant-synet finnes nøyaktig i det ene vinduet
-- sveipen bruker det, og ingen annen kodevei kan snuble inn i det.
CREATE POLICY m22_sveip_tenantliste ON lisens
    FOR SELECT TO disponit_lisens_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

ALTER TABLE lisensvarsling ENABLE ROW LEVEL SECURITY;
ALTER TABLE lisensvarsling FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON lisensvarsling
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE lisensvarsel_sendt ENABLE ROW LEVEL SECURITY;
ALTER TABLE lisensvarsel_sendt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON lisensvarsel_sendt
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- Rettighetene dørenes eier trenger, og ikke mer. Merk hva som IKKE
-- står her: ingen runtime-rolle får en eneste tabellrettighet på de tre
-- tabellene (SP-7, 090/091/096-formen) — hele registeret nås KUN gjennom
-- dørene i §3, og de krever tenantkontekst først.
GRANT SELECT, INSERT, UPDATE ON lisens TO disponit_lisens_eier;
GRANT SELECT, INSERT ON lisensvarsling TO disponit_lisens_eier;
GRANT SELECT, INSERT ON lisensvarsel_sendt TO disponit_lisens_eier;
-- Fremmednøkkelen mot identiteten opprettes over (som migrator, som
-- eier begge tabellene); eieren trenger REFERENCES bare hvis en senere
-- migrasjon skulle legge til flere.
GRANT REFERENCES ON brukeridentitet TO disponit_lisens_eier;
-- Varselveien: køing, kanalvalget og medlemskapssjekken ved
-- registrering. Alle tre er RLS-gjerdet på tenant, og eieren har INGEN
-- lesetilgang til varselinnholdet den ikke selv skrev.
GRANT INSERT ON varsel TO disponit_lisens_eier;
-- KOLONNEGRANT, ikke tabellgrant (husregelen): sveipen trenger id-en
-- den nettopp skrev for å binde ankeret til varselet — og INGENTING
-- annet. At registerets eier aldri kan lese et varsels innhold, heller
-- ikke sitt eget, skal være en egenskap ved BASEN og ikke en egenskap
-- ved koden som tilfeldigvis ikke gjør det.
GRANT SELECT (id) ON varsel TO disponit_lisens_eier;
GRANT SELECT ON varselvalg TO disponit_lisens_eier;
GRANT SELECT ON brukermedlemskap TO disponit_lisens_eier;
-- Visningsnavnet i lesedøren. Kolonnegrant igjen: `issuer` og `sub` er
-- identitetens hemmelige halvdel (010), og lisensregisteret har ingen
-- bruk for dem.
GRANT SELECT (bruker_id, profil) ON brukeridentitet TO disponit_lisens_eier;
-- EVIDENSKJEDEN (m02, manifestets ene reelle avhengighet): hver
-- lisensovergang og hvert køet utløpsvarsel skriver sin egen loggpost, i
-- SAMME transaksjon som handlingen. Se §3. INSERT alene — evidenskjeden
-- skrives til, den leses aldri herfra.
GRANT INSERT ON revisjonslogg TO disponit_lisens_eier;

-- Kontekstporten eies av `disponit_m37_claimer` og er REVOKEd fra
-- PUBLIC (038). Dørene under er SECURITY DEFINER og løper som
-- `disponit_lisens_eier` — uten dette grantet ville SP-1-porten feilet
-- med «permission denied», og registeret vært nede i stedet for sikret.
-- Grantet gis av eieren selv (039-formen); migrator er medlem av begge.
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_lisens_eier;
RESET ROLE;

-- ------------------------------------------------------------
-- 3. Dørene. SECURITY DEFINER, eid av `disponit_lisens_eier`, og hver
--    tenantbundet dør kaller `krev_tenantkontekst` FØRST (SP-1).
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_lisens_eier;

-- Evidenskjeden, ett sted. Kalles av hver dør og av sveipen, i deres
-- egen transaksjon.
--
-- ÆRLIG OM FORMEN (096s begrunnelse, ordrett): `revisjonslogg` har ingen
-- ciphertext-kolonner (041 §4 dokumenterer det mot levende base), så
-- `payload_type='kryptert'` med `referansepayload IS NULL` er den
-- ordinære formen HVER eksisterende skriver bruker — ikke en påstand om
-- at det finnes en kryptert payload et sted. `referanse`-formen er
-- lukket til domeneovertakelses-familien av `er_gyldig_referansepayload`,
-- og å utvide DEN validatoren for en lisenshendelse ville vært å låne en
-- tolkning M-22 ikke er blitt gitt. `beslutning='TILLAT'` fordi
-- handlingen ER tillatt og utført; en lisensregistrering føder ingen sak.
--
-- `input_hash` er sha256 over den kanoniske beskrivelsen av HANDLINGEN,
-- ikke over kundedata: lisensens id, hva som skjedde og datoene det
-- gjaldt. LEVERANDØREN OG PRODUKTNAVNET STÅR ALDRI HER — de er kundens
-- tekst, og evidenskjeden skal kunne gjenfinne handlingen uten å
-- arkivere innholdet på nytt. At vi vet HVA en tenant betaler for, er
-- dessuten nettopp den opplysningen et lisensregister ikke skal spre
-- videre i en logg ingen tenker på.
CREATE FUNCTION m22_evidens(p_tenant TEXT, p_lisens_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm22_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm22_lisens', 'handling', p_handling,
        'lisens_id', p_lisens_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm22_lisens',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:lisensregister', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m22_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- Husets standardpunkter for en LISENS: 60, 30 og 7 døgn før
-- BESLUTNINGSDATOEN. Bevisst lengre lead enn M-21s 30/7/1, og
-- forskjellen er ikke smak: en frist mot det offentlige krever at noe
-- LEVERES, mens en lisens krever at noe BESLUTTES — alternativer må
-- vurderes, en avtale forhandles, og data kanskje flyttes. To måneder
-- til å undersøke, én til å bestemme, én uke til å handle.
CREATE FUNCTION m22_standardpunkter() RETURNS INT[]
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
    SELECT ARRAY[60, 30, 7]
$$;
REVOKE ALL ON FUNCTION m22_standardpunkter() FROM PUBLIC;

-- Registreringsdøren. SP-2-materialitet på `p_lisens_id` (m35/m21-formen):
-- kalleren utleder id-en deterministisk av sin Idempotency-Key, så et
-- gjenspill med identisk innhold er et STILLE JA (false), mens samme id
-- med ANNET innhold er en materiell konflikt.
CREATE FUNCTION m22_registrer_lisens(
    p_tenant TEXT, p_lisens_id UUID, p_leverandor TEXT, p_produkt TEXT,
    p_eier_bruker_id TEXT, p_antall_seter INT, p_kostnad_aar NUMERIC,
    p_valuta TEXT, p_fornyelsesdato DATE, p_fornyelsestype TEXT,
    p_oppsigelsesfrist_dogn INT, p_kilde TEXT, p_dogn_for INT[],
    p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_punkter INT[]; v_gamle_punkter INT[];
        v_gammel RECORD; v_type TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm22_registrer_lisens');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    -- DOM 1, håndhevet FØR innsettingen slik at feilmeldingen sier hva
    -- som er galt: eieren må være et AKTIVT medlem av tenanten. FK-en
    -- alene sier bare at bruker-id-en finnes et sted i plattformen — og
    -- en lisens eid av en fremmed tenants bruker er en lisens ingen her
    -- forvalter.
    IF NOT EXISTS (SELECT 1 FROM public.brukermedlemskap bm
                    WHERE bm.tenant = p_tenant
                      AND bm.bruker_id = p_eier_bruker_id AND bm.aktiv) THEN
        RAISE EXCEPTION 'm22_registrer_lisens: % er ikke et aktivt medlem'
            ' av tenanten — en lisens uten eier her er en lisens ingen'
            ' forvalter', p_eier_bruker_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_type := COALESCE(p_fornyelsestype, 'automatisk');
    v_punkter := COALESCE(nullif(p_dogn_for, ARRAY[]::INT[]),
                          public.m22_standardpunkter());
    INSERT INTO public.lisens (tenant, lisens_id, leverandor, produkt,
                               eier_bruker_id, antall_seter, kostnad_aar,
                               valuta, fornyelsesdato, fornyelsestype,
                               oppsigelsesfrist_dogn, kilde, opprettet_av)
    VALUES (p_tenant, p_lisens_id, p_leverandor, p_produkt,
            p_eier_bruker_id, p_antall_seter, p_kostnad_aar, p_valuta,
            p_fornyelsesdato, v_type, p_oppsigelsesfrist_dogn, p_kilde,
            p_aktor)
        ON CONFLICT (tenant, lisens_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        -- SP-2: samme id igjen. Identisk innhold er et stille ja; annet
        -- innhold er en materiell konflikt kalleren SKAL se.
        --
        -- MATERIALITETEN DEKKER HELE LISENSEN, ikke bare hodet (096s
        -- CodeRabbit-lærdom): oppsigelsesfristen avgjør NÅR noen får
        -- vite om fornyelsen, og varslingspunktene avgjør hvor mange
        -- ganger. Et gjenspill som endret ett av dem ville fått et
        -- stille ja på en lisens som varsler noe annet enn den kalleren
        -- tror den registrerte — og SP-2s hele poeng er at et gjenspill
        -- ikke skal kunne endre noe i det stille.
        SELECT * INTO v_gammel FROM public.lisens
         WHERE tenant = p_tenant AND lisens_id = p_lisens_id;
        SELECT array_agg(v.dogn_for ORDER BY v.dogn_for)
          INTO v_gamle_punkter
          FROM public.lisensvarsling v
         WHERE v.tenant = p_tenant AND v.lisens_id = p_lisens_id;
        IF v_gammel.leverandor IS DISTINCT FROM p_leverandor
           OR v_gammel.produkt IS DISTINCT FROM p_produkt
           OR v_gammel.eier_bruker_id IS DISTINCT FROM p_eier_bruker_id
           OR v_gammel.antall_seter IS DISTINCT FROM p_antall_seter
           OR v_gammel.kostnad_aar IS DISTINCT FROM p_kostnad_aar
           OR v_gammel.valuta IS DISTINCT FROM p_valuta
           OR v_gammel.fornyelsesdato IS DISTINCT FROM p_fornyelsesdato
           OR v_gammel.fornyelsestype IS DISTINCT FROM v_type
           OR v_gammel.oppsigelsesfrist_dogn
              IS DISTINCT FROM p_oppsigelsesfrist_dogn
           OR v_gammel.kilde IS DISTINCT FROM p_kilde
           OR v_gamle_punkter IS DISTINCT FROM (
                  SELECT array_agg(DISTINCT d ORDER BY d)
                    FROM unnest(v_punkter) AS d) THEN
            RAISE EXCEPTION 'm22_registrer_lisens: samme lisens_id med'
                ' annet innhold — materiell konflikt'
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN false;
    END IF;
    INSERT INTO public.lisensvarsling (tenant, lisens_id, dogn_for)
    SELECT p_tenant, p_lisens_id, d
      FROM unnest(v_punkter) AS d
     GROUP BY d;
    PERFORM public.m22_evidens(
        p_tenant, p_lisens_id, 'lisens.registrert', p_aktor,
        jsonb_build_object('fornyelsesdato', p_fornyelsesdato,
                           'fornyelsestype', v_type,
                           'oppsigelsesfrist_dogn', p_oppsigelsesfrist_dogn,
                           'eier_bruker_id', p_eier_bruker_id,
                           'dogn_for', to_jsonb(v_punkter)));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m22_registrer_lisens(
    TEXT, UUID, TEXT, TEXT, TEXT, INT, NUMERIC, TEXT, DATE, TEXT, INT,
    TEXT, INT[], TEXT) FROM PUBLIC;

-- FORNYELSESDØREN. Den ene veien `fornyelsesdato` flyttes, og den er
-- MENNESKELIG: et menneske vet at avtalen løper videre, og skriver den
-- nye perioden. Sveipen gjør det aldri — en sveip som rullet datoen selv
-- ville vært modulen som endrer en lisensrad, altså dom 2 brutt i den
-- ene retningen ingen tenker på.
--
-- Den nye datoen må ligge FRAMFOR den gjeldende. Uten kravet ville
-- perioden kunnet skyves bak et anker som alt er skrevet, og
-- varslingspunktene for den nye perioden ville vært tause. Vakten i §2
-- nekter det uansett; RAISE-en her finnes for at feilen skal si hvorfor.
--
-- GJENSPILLET ER ET STILLE JA (096s CodeRabbit-lærdom, samme klasse): en
-- tapt respons + nytt klikk med SAMME dato skal ikke føde en ny
-- evidensrad om en fornyelse som bare skjedde én gang.
CREATE FUNCTION m22_registrer_fornyelse(
    p_tenant TEXT, p_lisens_id UUID, p_ny_fornyelsesdato DATE, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE l RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm22_registrer_fornyelse');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_ny_fornyelsesdato IS NULL THEN
        RAISE EXCEPTION 'm22_registrer_fornyelse: den nye fornyelsesdatoen'
            ' kan ikke være tom' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO l FROM public.lisens
     WHERE tenant = p_tenant AND lisens_id = p_lisens_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm22_registrer_fornyelse: ukjent lisens %',
            p_lisens_id USING ERRCODE = 'no_data_found';
    END IF;
    IF l.status <> 'aktiv' THEN
        RAISE EXCEPTION 'm22_registrer_fornyelse: lisensen er alt %',
            l.status USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_ny_fornyelsesdato = l.fornyelsesdato THEN
        RETURN false;                     -- gjenspill: alt fornyet hit
    END IF;
    IF p_ny_fornyelsesdato < l.fornyelsesdato THEN
        RAISE EXCEPTION 'm22_registrer_fornyelse: den nye fornyelsesdatoen'
            ' (%) ligger før den gjeldende (%) — en periode som flyttes'
            ' bakover gjemmer seg bak et anker som alt er skrevet',
            p_ny_fornyelsesdato, l.fornyelsesdato
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.lisens SET fornyelsesdato = p_ny_fornyelsesdato
     WHERE tenant = p_tenant AND lisens_id = p_lisens_id;
    PERFORM public.m22_evidens(
        p_tenant, p_lisens_id, 'lisens.fornyet', p_aktor,
        jsonb_build_object('forrige_fornyelsesdato', l.fornyelsesdato,
                           'fornyelsesdato', p_ny_fornyelsesdato));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m22_registrer_fornyelse(TEXT, UUID, DATE, TEXT)
    FROM PUBLIC;

-- AVSLUTNINGSDØREN. Den eneste terminale veien, og den er MENNESKELIG.
-- Begrunnelsen er påkrevd av samme grunn som M-21s bortfall koster en:
-- uten den ville «avsluttet» vært en gratis vei ut av enhver kostnad, og
-- registeret en liste over ting man kan klikke bort. En avsluttet lisens
-- uten begrunnelse er dessuten en rad som blir gåtefull om et år — og et
-- lisensregister leses nettopp et år senere, når noen spør hvorfor vi
-- ikke lenger har verktøyet.
CREATE FUNCTION m22_marker_avsluttet(
    p_tenant TEXT, p_lisens_id UUID, p_begrunnelse TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE l RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm22_marker_avsluttet');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_begrunnelse IS NULL OR p_begrunnelse !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm22_marker_avsluttet: en avslutning krever en'
            ' skreven begrunnelse — uten den er det en kostnad som'
            ' forsvinner uten at noen vet hvorfor'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO l FROM public.lisens
     WHERE tenant = p_tenant AND lisens_id = p_lisens_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm22_marker_avsluttet: ukjent lisens %', p_lisens_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF l.status <> 'aktiv' THEN
        RAISE EXCEPTION 'm22_marker_avsluttet: lisensen er alt %', l.status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.lisens
       SET status = 'avsluttet', avslutt_begrunnelse = p_begrunnelse,
           avsluttet_ts = now(), avsluttet_av = p_aktor
     WHERE tenant = p_tenant AND lisens_id = p_lisens_id;
    PERFORM public.m22_evidens(
        p_tenant, p_lisens_id, 'lisens.avsluttet', p_aktor,
        jsonb_build_object('fornyelsesdato', l.fornyelsesdato,
                           'begrunnelse_lengde', length(p_begrunnelse)));
END $$;
REVOKE ALL ON FUNCTION m22_marker_avsluttet(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

-- Lesedøren (051/090/096-formen): flatens hele lesetilstand i ett kall.
-- Runtime har INGEN SELECT på tabellene, så dette er den eneste veien
-- inn — og den krever tenantkontekst først.
--
-- `beslutningsdato` og `dogn_til_beslutning` regnes HER, i samme skann
-- som raden, fordi flaten ikke skal trekke to datoer fra hverandre — og
-- fordi det er BESLUTNINGSDATOEN, ikke fornyelsesdatoen, som er det
-- tallet et menneske må handle på. Begge vises: en flate som bare viste
-- den ene ville skjult enten hva vi betaler for eller når vi må
-- bestemme oss.
CREATE FUNCTION m22_lisenser(p_tenant TEXT, p_grense INT)
RETURNS TABLE(lisens_id UUID, leverandor TEXT, produkt TEXT,
              eier_bruker_id TEXT, eier_navn TEXT, antall_seter INT,
              kostnad_aar NUMERIC, valuta TEXT, fornyelsesdato DATE,
              oppsigelsesfrist_dogn INT, beslutningsdato DATE,
              dogn_til_beslutning INT, fornyelsestype TEXT, kilde TEXT,
              status TEXT, avslutt_begrunnelse TEXT,
              avsluttet_ts TIMESTAMPTZ, avsluttet_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm22_lisenser');
    RETURN QUERY
    SELECT l.lisens_id, l.leverandor, l.produkt, l.eier_bruker_id,
           -- Visningsnavnet fra den LUKKEDE profil-DTO-en (010). NULL
           -- når IdP-en ikke ga noe — flaten viser da bruker-id-en, som
           -- er ærligere enn en tom celle.
           nullif(btrim(coalesce(b.profil->>'visningsnavn', '')), ''),
           l.antall_seter, l.kostnad_aar, l.valuta, l.fornyelsesdato,
           l.oppsigelsesfrist_dogn, l.beslutningsdato,
           -- HELE DØGN til beslutningsdatoen, negativt når den er forbi.
           -- Ett tall avledet av én dato — ikke et forhold mellom to av
           -- svarets tall (M-16-regelen).
           (l.beslutningsdato - current_date)::int,
           l.fornyelsestype, l.kilde, l.status, l.avslutt_begrunnelse,
           l.avsluttet_ts, l.avsluttet_av
      FROM public.lisens l
      LEFT JOIN public.brukeridentitet b ON b.bruker_id = l.eier_bruker_id
     WHERE l.tenant = p_tenant
     -- Aktive først (det som fortsatt koster penger og krever et valg),
     -- deretter beslutningsdato stigende: det som må besluttes først
     -- står øverst, og det som er forbi står aller øverst.
     ORDER BY (l.status <> 'aktiv'), l.beslutningsdato, l.lisens_id
     LIMIT greatest(least(coalesce(p_grense, 200), 500), 1);
END $$;
REVOKE ALL ON FUNCTION m22_lisenser(TEXT, INT) FROM PUBLIC;

-- ------------------------------------------------------------
-- 4. Sveipen. Kalles fra varselsenderens pre-pass, ved siden av M-21s.
-- ------------------------------------------------------------

-- Per-tenant-arbeidet. Egen funksjon nettopp for at PORTEN skal gjelde
-- her også: sveipen under binder konteksten til RADENS tenant og kaller
-- hit, og da går utløpsvarslingen gjennom nøyaktig den
-- `krev_tenantkontekst` enhver annen kaller går gjennom (038-reaperens
-- form). Porten er ikke noe sveipen slipper unna, bare noe den oppfyller
-- per tenant.
--
-- MERK HVA SOM IKKE STÅR HER: ingen UPDATE av `lisens`. Sveipen leser
-- registeret og skriver varsler og ankre — den rører verken `status`,
-- `fornyelsesdato` eller noen annen kolonne på lisensraden. Det er
-- invarianten `lisens_avsluttet_av_modulen`, og den måles både statisk
-- (ingen UPDATE i denne funksjonen) og funksjonelt (en sveip endrer
-- ingen lisensrad).
--
-- ANKER OG VARSEL I SAMME TRANSAKSJON, og et punkt om gangen under
-- advisory-lås. Rekkefølgen er varsel → anker, fordi ankeret bærer
-- `varsel_ref` NOT NULL og er append-only: id-en finnes ikke før varselet
-- er skrevet, og en etterfølgende UPDATE ville vakten (§2) med rette
-- nektet. Låsen er det som gjør rekkefølgen trygg — den eier punktet
-- gjennom hele paret, så to samtidige sveip kan ikke begge rekke å køe.
CREATE FUNCTION m22_koe_for_tenant(p_tenant TEXT, p_grense INT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_n INT := 0; v_kanal TEXT; v_varsel BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm22_koe_for_tenant');
    PERFORM set_config('disponit.aktor', 'lisenssveip', true);
    FOR r IN
        SELECT l.lisens_id, l.leverandor, l.produkt, l.fornyelsesdato,
               l.beslutningsdato, l.oppsigelsesfrist_dogn,
               l.eier_bruker_id, v.dogn_for
          FROM public.lisens l
          JOIN public.lisensvarsling v
            ON v.tenant = l.tenant AND v.lisens_id = l.lisens_id
         WHERE l.tenant = p_tenant
           AND l.status = 'aktiv'
           -- DOM 3, i den ene linjen som bærer den: punktet måles mot
           -- BESLUTNINGSDATOEN, altså fornyelsen minus oppsigelsesfristen
           -- — ikke mot fornyelsen. En lisens med 90 døgns
           -- oppsigelsesfrist og 60 døgn til fornyelse har PASSERT alle
           -- sine punkter, og skal varsles nå. Regnet fra
           -- `l.fornyelsesdato` ville den ikke fått ett eneste varsel før
           -- valget var tatt for den.
           --
           -- Et punkt som ER passert treffer: en lisens som registreres
           -- for sent skal varsles, ikke ties i hjel.
           AND l.beslutningsdato - v.dogn_for <= current_date
           AND NOT EXISTS (
               SELECT 1 FROM public.lisensvarsel_sendt s
                WHERE s.tenant = l.tenant AND s.lisens_id = l.lisens_id
                  AND s.dogn_for = v.dogn_for
                  AND s.fornyelsesdato = l.fornyelsesdato)
         -- Nærmeste beslutning først, og det mest presserende punktet
         -- først innen hver lisens: treffer sveipen taket sitt, er det de
         -- viktigste varslene som er ute.
         ORDER BY l.beslutningsdato, l.lisens_id, v.dogn_for
         LIMIT greatest(coalesce(p_grense, 100), 1)
    LOOP
        -- PUNKTET LÅSES FØRST (091/096-formen). Låsen er det som gjør at
        -- varselet kan skrives FØR ankeret uten at to samtidige sveip
        -- begge rekker å køe: uten den måtte ankeret vært først, og da
        -- ville `varsel_ref` krevd en etterfølgende UPDATE — som
        -- append-only-vakten på ankeret (§2) med rette nekter. Låsen er
        -- transaksjonslokal og slippes med forpassets egen commit.
        PERFORM pg_advisory_xact_lock(
            2201098,
            hashtext(p_tenant || E'\x1f' || r.lisens_id::text
                     || E'\x1f' || r.dogn_for::text
                     || E'\x1f' || r.fornyelsesdato::text));
        -- …og re-les under låsen. Utvalget over ble lest før låsen, så
        -- en sveip som ventet her skal se det den andre nettopp skrev.
        CONTINUE WHEN EXISTS (
            SELECT 1 FROM public.lisensvarsel_sendt s
             WHERE s.tenant = p_tenant AND s.lisens_id = r.lisens_id
               AND s.dogn_for = r.dogn_for
               AND s.fornyelsesdato = r.fornyelsesdato);
        SELECT vv.kanal INTO v_kanal FROM public.varselvalg vv
         WHERE vv.tenant = p_tenant AND vv.bruker_id = r.eier_bruker_id;
        -- `hendelse` bærer BÅDE punktet og fornyelsesdatoen: to perioder
        -- av samme lisens er to forskjellige varsler, ikke ett gjentatt
        -- (026s begrunnelse for leddet). Dagpresisjon holder her, til
        -- forskjell fra 096s mikrosekunder, fordi ankerets PK OGSÅ er en
        -- DATE — de to nøklene skiller nøyaktig like mange forekomster,
        -- og et `varsel_en_per_hendelse`-sammenstøt er derfor
        -- urepresenterbart.
        INSERT INTO public.varsel (tenant, bruker_id, art, ressurs_type,
            ressurs_id, hendelse, tekstnokkel, parametre, epost_status)
        VALUES (p_tenant, r.eier_bruker_id, 'lisensutlop', 'lisens',
                r.lisens_id::text,
                r.dogn_for::text || '@'
                    || to_char(r.fornyelsesdato, 'YYYY-MM-DD'),
                'varsel.lisensutlop',
                jsonb_build_object(
                    'produkt', r.produkt,
                    'leverandor', r.leverandor,
                    'dogn_for', r.dogn_for,
                    'fornyelsesdato',
                        to_char(r.fornyelsesdato, 'YYYY-MM-DD'),
                    'beslutningsdato',
                        to_char(r.beslutningsdato, 'YYYY-MM-DD')),
                CASE WHEN COALESCE(v_kanal, 'epost_og_portal')
                          = 'kun_portal'
                     THEN 'ikke_aktuelt' ELSE 'koet' END)
        RETURNING id INTO v_varsel;
        -- ANKERET, I SAMME TRANSAKSJON og ferdig utfylt ved fødselen.
        -- Rekkefølgen (varsel → anker) er trygg fordi låsen over eier
        -- punktet: ruller transaksjonen tilbake, forsvinner BEGGE, og
        -- committer den, står begge. Et varsel uten anker og et anker
        -- uten varsel er dermed like urepresenterbare.
        INSERT INTO public.lisensvarsel_sendt
            (tenant, lisens_id, dogn_for, fornyelsesdato, varsel_ref)
        VALUES (p_tenant, r.lisens_id, r.dogn_for, r.fornyelsesdato,
                v_varsel);
        PERFORM public.m22_evidens(
            p_tenant, r.lisens_id, 'lisens.utlopsvarsel_koet', 'lisenssveip',
            jsonb_build_object('dogn_for', r.dogn_for,
                               'fornyelsesdato', r.fornyelsesdato,
                               'beslutningsdato', r.beslutningsdato,
                               'varsel_ref', v_varsel));
        v_n := v_n + 1;
    END LOOP;
    RETURN v_n;
END $$;
REVOKE ALL ON FUNCTION m22_koe_for_tenant(TEXT, INT) FROM PUBLIC;

-- FORPASSET. Kalles av `platform/drift/varselsender.py` i sin egen
-- transaksjon, med sin egen feilfangst — se invarianten
-- `forpass_stanset_ordinaer_sending`. Den ligger ved siden av M-21s
-- forpass, ikke inni det: hvert forpass har sin egen transaksjon, sin
-- egen `except`, sin egen `conn.rollback()` og sin egen feilteller,
-- nettopp for at M-22s feil ikke skal kunne stanse M-21s sveip — og
-- omvendt.
--
-- Dette er kryss-tenant-autoriteten, og den er innelukket her (038s
-- reaperdoktrine): funksjonen har ingen `p_tenant` en kaller kan velge,
-- hele utvalget ligger i policyen `m22_sveip_tenantliste` og i
-- predikatet under, og kallerens egen kontekst legges tilbake til slutt
-- så en kjøring aldri etterlater en fremmed tenant i transaksjonen den
-- ble kalt fra.
--
-- TENANTLISTEN MATERIALISERES FØR konteksten røres. Leste løkka rett
-- fra tabellen mens den satte kontekst per iterasjon, ville policyen
-- (som leser nøyaktig den GUC-en) slått seg av under føttene på sin egen
-- markør etter første tenant. Et array er billig og gjør rekkefølgen
-- deterministisk.
CREATE FUNCTION m22_koe_utlopsvarsler(p_grense INT DEFAULT 100)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kontekst TEXT; v_aktor TEXT; v_tenanter TEXT[];
        v_t TEXT; v_igjen INT; v_n INT := 0;
BEGIN
    v_kontekst := current_setting('disponit.tenant', true);
    v_aktor := current_setting('disponit.aktor', true);
    v_igjen := greatest(coalesce(p_grense, 100), 1);
    PERFORM set_config('disponit.tenant', '', true);
    -- KUN `lisens` leses her. Kryss-tenant-policyen (§1) står på nøyaktig
    -- den ene tabellen, og et JOIN mot `lisensvarsling` ville derfor
    -- returnert null rader uten kontekst — stille, og med et helt
    -- register som aldri varslet. Taket `dogn_for <= 3650` (CHECK-en i
    -- §1) gjør bunnfiltreringen sann og indeksbrukbar; det presise
    -- punktfilteret hører hjemme per tenant, der resten av registeret er
    -- lesbart.
    SELECT array_agg(DISTINCT l.tenant ORDER BY l.tenant)
      INTO v_tenanter
      FROM public.lisens l
     WHERE l.status = 'aktiv'
       AND l.beslutningsdato <= current_date + 3650;
    FOREACH v_t IN ARRAY COALESCE(v_tenanter, ARRAY[]::TEXT[]) LOOP
        EXIT WHEN v_igjen <= 0;
        -- Én tenant om gangen, bundet til RADENS tenant — og gjennom
        -- den samme porten alle andre kallere går gjennom.
        PERFORM set_config('disponit.tenant', v_t, true);
        v_n := v_n + public.m22_koe_for_tenant(v_t, v_igjen);
        v_igjen := greatest(coalesce(p_grense, 100), 1) - v_n;
    END LOOP;
    PERFORM set_config('disponit.tenant', coalesce(v_kontekst, ''), true);
    PERFORM set_config('disponit.aktor', coalesce(v_aktor, ''), true);
    RETURN v_n;
END $$;
REVOKE ALL ON FUNCTION m22_koe_utlopsvarsler(INT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 5. Rettighetene. Migrasjonen NAVNGIR IKKE runtime-rollen (057-
--    lærdommen): `deploy/staging/migrer.py` er autoritativ for den
--    konfigurerte rollen. Grantene her er de som gjelder lokalt og i
--    test, der runtime ER hele plattformen, og de faller bort i
--    driftsoppsettet.
-- ------------------------------------------------------------
DO $$
BEGIN
    -- Sveipen: ÉN EXECUTE til varselsenderen. Ingen tabellrettigheter —
    -- rollen har ingen i dag (verifisert), og M-22 gir den ingen. Ingen
    -- egen sveiperolle heller: klyngefundamentet slo fast at
    -- utløpssveipen er et forpass i varselsenderen, ikke en ny timer.
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_varselsender') THEN
        GRANT EXECUTE ON FUNCTION m22_koe_utlopsvarsler(INT)
            TO disponit_varselsender;
    END IF;
    -- DØRENE TIL RUNTIME GRANTES IKKE HER (057/089/096-doktrinen):
    -- `disponit` er bare LOKALNAVNET på web-API-rollen, og
    -- `deploy/staging/migrer.py` er eneste rettighetskilde for den
    -- konfigurerte rollen. En GRANT her ville lagt rettighetsmodellen to
    -- steder, og det ene stedet ville vært usant på enhver installasjon
    -- som kaller rollen noe annet.
    --
    -- REVOKE-en står likevel, og den er ikke pynt (091-formen): en
    -- rettighet som bare slutter å bli gitt er ikke trukket tilbake.
    -- Sveipen er kryss-tenant, og web-API-rollen skal ikke kunne kjøre
    -- den på kommando — samme snitt som 038-reaperen, og samme grunn: en
    -- kompromittert runtime skal se ÉN tenant om gangen.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        REVOKE ALL ON FUNCTION m22_koe_utlopsvarsler(INT) FROM disponit;
    END IF;
END $$;

RESET ROLE;

-- ------------------------------------------------------------
-- 6. Varselenumene: `art` og `ressurs_type` er lukkede CHECK-er (029) —
--    utvidet ADDITIVT i 041 §15-formen (`regexp_replace` på halen), som
--    er den formen som tåler at flere moduler utvider den samme
--    CHECK-en i vilkårlig rekkefølge.
--
--    PORTEN KREVER MER ENN DETTE: `deploy/staging/varselenum-reparasjon.sql`
--    DEKLARERER fasiten, og `platform/core/tests/test_varselenum.py`
--    pinner den. Begge er utvidet i SAMME commit som denne migrasjonen —
--    det er nøyaktig arbeidsflyten porten finnes for å tvinge fram, og
--    den fanget M-21 på første forsøk.
-- ------------------------------------------------------------
DO $$
DECLARE r RECORD; def TEXT; ny TEXT;
BEGIN
    FOR r IN SELECT conname, pg_get_constraintdef(oid) AS def
               FROM pg_constraint
              WHERE conrelid = 'varsel'::regclass
                AND conname IN ('varsel_art_chk', 'varsel_ressurs_type_chk')
    LOOP
        ny := CASE r.conname WHEN 'varsel_art_chk' THEN 'lisensutlop'
                             ELSE 'lisens' END;
        -- Sammenlikningen gjøres på den KVOTERTE formen for å være sann
        -- også om noen senere legger til 'lisensbrudd' eller
        -- 'lisensutlopet': en delstrengsjekk som blir usann av en nabo er
        -- en migrasjon som kjører to ganger.
        CONTINUE WHEN r.def LIKE '%''' || ny || '''%';
        EXECUTE format('ALTER TABLE varsel DROP CONSTRAINT %I', r.conname);
        def := regexp_replace(r.def, '\]\)\)\)$',
                              format(', %L::text])))', ny));
        IF def NOT LIKE '%''' || ny || '''%' THEN
            RAISE EXCEPTION '098: kunne ikke utvide % — uventet'
                ' definisjonsform: %', r.conname, r.def;
        END IF;
        EXECUTE 'ALTER TABLE varsel ADD CONSTRAINT '
             || quote_ident(r.conname) || ' ' || def;
    END LOOP;
END $$;
