-- 099: M-30 personvern- og datasubjektagent v1 — FORESPØRSELSREGISTERET.
-- Tre tenant-skopede tabeller, seks dører og ÉN sveip med egen timer.
--
-- V1-DOMMEN, ORDRETT FRA MANIFESTET, OG DEN ER DEN VIKTIGSTE I HELE
-- KLYNGEN: MODULEN SLETTER INGENTING. Katalogteksten lover en agent som
-- finner, samler og SLETTER personopplysninger på tvers av lagrene når
-- noen ber om det. Sletting er allerede eid av M-4s retensjonsregnskap
-- (093) og de seks reaperne som kjører — og en ANDRE slettevei ved
-- siden av dem er nøyaktig det M-4 ble bygget for å hindre. To veier
-- som sletter det samme kan aldri holdes i takt.
--
-- Det modulen GJØR: den skriver ned HVEM som har bedt om HVA, når
-- fristen går ut (GDPR art. 12 nr. 3: én måned fra mottak, forlengbar
-- med to måneder når saken er kompleks), HVEM som eier den, og HVA
-- svaret ble. Den peker på lagrene M-4 navngir, og lar den som EIER
-- lageret gjøre selve utførelsen. Registeret er det som gjør at noen
-- kan SE at den ble gjort.
--
-- KOBLINGEN MOT M-4 ER EN VAKT, IKKE EN FREMMEDNØKKEL, og det er en dom
-- og ikke en forenkling — se §1.2 for hele begrunnelsen.
--
-- TRE DOMMER v1 hviler på, alle håndhevet i DATAMODELLEN og ikke i et
-- API-lag som kunne omgås:
--
--   1. EN SAK UTEN EIER ER UREPRESENTERBAR. `eier_bruker_id` er NOT NULL
--      med fremmednøkkel mot `brukeridentitet`, og døren krever i
--      tillegg at eieren er et AKTIVT MEDLEM av tenanten. En
--      innsynsforespørsel ingen eier er en forespørsel ingen besvarer.
--
--   2. EN SAK LUKKES AV ET SKREVET SVAR, ALDRI AV AT FRISTEN PASSERER.
--      `besvart` krever `svar_ref` + `svar_ts`, `avvist` krever
--      `avvist_begrunnelse` + `svar_ts`, begge krever en navngitt
--      `lukket_av` — CHECK-er som gjør formen TOTAL. Og enhver
--      statusovergang krever en NAVNGITT AKTØR i sesjonen (vakten i §2),
--      som en jobb ikke har å skrive. Det finnes ingen kodevei i denne
--      migrasjonen som setter `status`: sveipen reiser FUNN og rører
--      ikke statuskolonnen i det hele tatt.
--
--      DETTE ER STRENGERE ENN M-21s KVITTERINGSKRAV, og det er med
--      vilje: en oversittet innsynsforespørsel er et LOVBRUDD, ikke en
--      forsinkelse. M-21 lar `bortfalt` være en billig, begrunnet
--      utvei; her finnes ingen tilsvarende — `avvist` er et SVAR til
--      den registrerte (art. 12 nr. 4 krever at avslaget begrunnes), og
--      den koster derfor nøyaktig like mye som et ja.
--
--   3. FORLENGELSEN HAR ET TAK, OG DET STÅR I SKJEMAET. Art. 12 nr. 3
--      gir to måneder ekstra, ikke flere, og den krever en begrunnelse.
--      Begge er CHECK-er: `forlenget_til` uten begrunnelse er
--      urepresenterbar, og en forlengelse forbi tre måneder fra
--      `mottatt` likeså.
--
-- FORMENE ER HUSETS (089/090/091/096): tabellene eies av migrator,
-- dørene av NOLOGIN-rollen `disponit_personvern_eier` (registrert i
-- `deploy/staging/eierskap-reparasjon.sql`), tenant TEXT + RLS
-- ENABLE+FORCE + `tenant_isolasjon` på hver tabell, SP-1
-- (`krev_tenantkontekst` FØRST) i hver tenantbundet definer, og INGEN
-- BYPASSRLS: kryss-tenant-autoriteten sveipen trenger er en EKSPLISITT,
-- SNEVER policy (§3) og ikke en rolleegenskap.
--
-- SVEIPEN ER EN EGEN TIMER, ikke et forpass i varselsenderen som
-- M-21/M-22. Grunnen er at den ikke VARSLER: den reiser FUNN, på M-9s
-- form (095), og en funnreiser har ingenting i varselkøens rytme,
-- backoff og idempotens å gjøre. Rollen `disponit_personvernsveip` får
-- nøyaktig ÉN EXECUTE og ingen tabellrettigheter.
--
-- OG DET ER EN AVGRENSNING SOM SKAL STÅ HER, IKKE GJEMMES.
-- Manifestteksten sier «den varsler før fristen og gjør en oversittet
-- frist til et funn». v1 gjør det ANDRE fullt ut, og det FØRSTE som et
-- funn — `frist_naermer_seg`, synlig på flaten og gjennom
-- `m30_apne_funn` — ikke som en e-post i varselkøen. Tre grunner, og
-- den siste er den bærende:
--
--   * Grensen `m30-v1` ble registrert FØR koden (§0-regelen), og den
--     har INGEN invariant om varselidempotens. M-21s grense har en
--     (`varsel_duplisert_per_varslingspunkt`), og den finnes fordi en
--     varslingsvei uten den er en vei å sende det samme varselet hver
--     kadens til folk slutter å lese dem. Å bygge veien uten å ha felt
--     dommen om den ville vært å legge til en fullmakt utenfor grensen.
--   * En ny varselart krever et ankerlager per (sak, varslingspunkt,
--     frist) og en additiv utvidelse av `varsel`-CHECKene mot en ALT
--     BEBODD tabell (047-klassen). Det siste er den ENE setningen som
--     ville gjort «grønn fra tom base» og «grønn mot seedet base» til
--     to forskjellige utsagn for 099 — og v1 har ingen målt kjøring å
--     hvile den på.
--   * M-9 (095) løser nøyaktig det samme spørsmålet på nøyaktig denne
--     formen: `utloper_snart` er et funn, ikke et varsel. En ny form
--     her ville bare vært en ny å lære.
--
-- Prisen er ærlig: ingen får en e-post når en frist nærmer seg — den
-- som eier saken må lese flaten. Det er en TYNNERE lovnad enn
-- katalogens, og den står her framfor i en commit-melding ingen leser
-- igjen.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_personvern_eier') THEN
        RAISE EXCEPTION 'rollen disponit_personvern_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
    -- 093s egen forutsetning, arvet: koblingen mot M-4 krever ett
    -- KOLONNEGRANT på `retensjonslager` (§3), og det grantet må gis AV
    -- lagerets eier. 'MEMBER' og ikke 'USAGE': medlemskapet skal være
    -- WITH INHERIT FALSE (005/013/014-formen). Sjekken feiler HARDT med
    -- den ene linjen som mangler, framfor å la migrasjonen dø på en
    -- «permission denied» ingen kan lese fikset ut av.
    IF NOT pg_catalog.pg_has_role(current_user, 'disponit_lager_eier',
                                  'MEMBER') THEN
        RAISE EXCEPTION '099: % er ikke medlem av disponit_lager_eier.'
            ' Kjør som superbruker: GRANT disponit_lager_eier TO %'
            ' WITH INHERIT FALSE', current_user, current_user;
    END IF;
END $$;

-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- 1.1 `personvernsak` — forespørselen selv. Én rad per sak per tenant.
--
-- NAVNET ER ASCII MED VILJE. Katalogen og grensen kaller den en
-- «forespørsel», og invariantnavnene i `KRAVGRENSER` bruker den formen
-- ordrett (`forespørsel_uten_eier`, `forespørsel_lukket_uten_svar`,
-- `tenantlekkasje_i_forespørselsregister`) — grensen ble registrert før
-- koden og skal ikke endres. Men en SQL-identifikator med `ø` må
-- kvoteres i hver eneste referanse, i hver dump, i hvert psql-utdrag og
-- i hvert feilsøkingsutklipp noen limer inn et halvt år fram i tid, og
-- en identifikator som må kvoteres for å virke blir før eller senere
-- skrevet uten kvoteringen. `personvernsak` sier det samme, kortere.
--
-- `subjekt_ref` ER EN REFERANSE, IKKE PERSONOPPLYSNINGER I KLARTEKST,
-- og det er den mest omvendte beslutningen i hele modulen: et register
-- over dem som har krevd innsyn i sine personopplysninger er selv et av
-- husets mest sensitive persondatalagre. Skrev vi navnet og e-posten
-- hit, ville modulen som skal gjøre GDPR-etterlevelse mulig blitt et
-- nytt lager som må dekkes av en senere innsynsforespørsel — og
-- registeret over slettesaker ville vært det ene stedet en sletting
-- ikke nådde. Kolonnen bærer derfor en HENVISNING til der identiteten
-- alt er kjent: kundens saksnummer, arkivreferansen, ticket-id-en.
-- Registeret vet at sak X gjelder subjekt «SAK-2026-119», og den som
-- har lov å slå opp hvem det er, gjør det der oppslaget hører hjemme.
-- Ikke-tom med vilje: en sak uten et subjekt å knytte svaret til er
-- ingen forespørsel.
CREATE TABLE personvernsak (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    sak_id UUID NOT NULL,
    -- GDPR-rettighetene, som LUKKET SETT. Kap. III art. 15-21: innsyn,
    -- retting, sletting, begrensning, portabilitet, innsigelse. En
    -- «type» utenfor settet er ikke en ny rettighet — det er en
    -- feilregistrering, og et lukket sett er den eneste formen som kan
    -- si det.
    type TEXT NOT NULL
        CHECK (type IN ('innsyn', 'retting', 'sletting', 'begrensning',
                        'portabilitet', 'innsigelse')),
    subjekt_ref TEXT NOT NULL CHECK (length(btrim(subjekt_ref)) > 0),
    mottatt DATE NOT NULL,
    -- Art. 12 nr. 3: «uten unødig opphold og under enhver omstendighet
    -- innen én måned etter at anmodningen er mottatt». Fristen er en
    -- DATO, ikke et tidspunkt: loven teller måneder, og en tidssone som
    -- flyttet fristen et halvt døgn ville vært en presisjon som ikke
    -- finnes i hjemmelen.
    frist DATE NOT NULL,
    forlenget_til DATE,
    forlengelse_begrunnelse TEXT,
    -- DOM 1. NOT NULL + FK mot identiteten, ikke mot medlemskapet:
    -- mister eieren medlemskapet skal saken fortsatt ha en eier å
    -- navngi, men raden skal ikke rives ut under en løpende frist.
    -- Samme valg som `plikt.eier_bruker_id` (096) og `varsel.bruker_id`
    -- (026).
    eier_bruker_id TEXT NOT NULL REFERENCES brukeridentitet (bruker_id),
    status TEXT NOT NULL DEFAULT 'apen'
        CHECK (status IN ('apen', 'besvart', 'avvist')),
    -- Svaret er en HENVISNING, av samme grunn som `subjekt_ref`: selve
    -- innsynsutdraget er personopplysninger, og det skal ikke arkiveres
    -- en gang til her for at registeret skal kunne bevise at det ble
    -- sendt.
    svar_ref TEXT,
    svar_ts TIMESTAMPTZ,
    avvist_begrunnelse TEXT,
    -- MENNESKET BAK LUKKINGEN. Kolonnen står ikke i kravlisten, og den
    -- er lagt til med overlegg: uten den kunne vakten i §2 kreve en
    -- navngitt aktør i sesjonen, men ingen som LESER raden et år senere
    -- ville sett hvem det var. En statusovergang som ikke bærer navnet
    -- sitt er en overgang en jobb kunne ha gjort.
    lukket_av TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    CONSTRAINT personvernsak_pk PRIMARY KEY (tenant, sak_id),
    -- LUKKEREGELEN, halvdel én (DOM 2). En sak kan ikke STÅ som
    -- `besvart` uten at det finnes en svarhenvisning, et tidspunkt og
    -- et menneske bak. Dette er invarianten `forespørsel_lukket_uten_svar`,
    -- og den gjelder ENHVER skrivevei — også direkte DML som eier.
    CONSTRAINT personvernsak_besvart_krever_svar CHECK (
        status <> 'besvart'
        OR (svar_ref IS NOT NULL AND length(btrim(svar_ref)) > 0
            AND svar_ts IS NOT NULL
            AND lukket_av IS NOT NULL AND length(btrim(lukket_av)) > 0)),
    -- LUKKEREGELEN, halvdel to. `avvist` er den andre lovlige utgangen,
    -- og den er IKKE en billig en: art. 12 nr. 4 krever at den
    -- behandlingsansvarlige underretter den registrerte om grunnen til
    -- at anmodningen ikke etterkommes. Uten en skreven begrunnelse
    -- ville «avvist» vært en gratis vei ut av enhver frist, og
    -- registeret en liste over ting man kan klikke bort.
    CONSTRAINT personvernsak_avvist_krever_begrunnelse CHECK (
        status <> 'avvist'
        OR (avvist_begrunnelse IS NOT NULL
            AND length(btrim(avvist_begrunnelse)) > 0
            AND svar_ts IS NOT NULL
            AND lukket_av IS NOT NULL AND length(btrim(lukket_av)) > 0)),
    -- …og formen er TOTAL, ikke bare betinget: en ÅPEN sak kan heller
    -- ikke bære et svar. Uten denne halvdelen kunne en rad stå med
    -- `svar_ref` og `svar_ts` fylt og `status='apen'`, og flaten ville
    -- vist en besvart sak som fortsatt løpende — eller motsatt. Et felt
    -- som bare betyr noe i én status skal være NULL i de andre.
    CONSTRAINT personvernsak_apen_er_ubesvart CHECK (
        status <> 'apen'
        OR (svar_ref IS NULL AND svar_ts IS NULL
            AND avvist_begrunnelse IS NULL AND lukket_av IS NULL)),
    -- Svaret hører til sin egen status: en `besvart` sak har ingen
    -- avslagsbegrunnelse, en `avvist` har ingen svarhenvisning.
    CONSTRAINT personvernsak_svarform_matcher_status CHECK (
        (status <> 'besvart' OR avvist_begrunnelse IS NULL)
        AND (status <> 'avvist' OR svar_ref IS NULL)),
    -- ART. 12 NR. 3 SOM SKJEMA, FØRSTE HALVDEL: den ordinære fristen er
    -- én måned fra mottak, og registeret skal ikke kunne PÅSTÅ en
    -- lengre. En frist noen kunne skrive fritt ville gjort «oversittet»
    -- til en mening i stedet for et faktum.
    CONSTRAINT personvernsak_frist_innen_en_maaned CHECK (
        frist >= mottatt
        AND frist <= (mottatt + interval '1 month')::date),
    -- ART. 12 NR. 3 SOM SKJEMA, ANDRE HALVDEL (DOM 3). Forlengelsen er
    -- TO måneder, ikke flere, og den koster en begrunnelse. Begge deler
    -- står her fordi begge er lovkrav: «Fristen kan forlenges med to
    -- måneder … Den behandlingsansvarlige skal underrette den
    -- registrerte om enhver slik forlengelse innen én måned etter at
    -- anmodningen er mottatt, og om årsakene til forsinkelsen.»
    --
    -- BEGGE ELLER INGEN: en begrunnelse uten en forlenget dato er en
    -- setning ingen frist svarer til, og en forlenget dato uten
    -- begrunnelse er nøyaktig det loven forbyr.
    CONSTRAINT personvernsak_forlengelse_er_hel CHECK (
        (forlenget_til IS NULL AND forlengelse_begrunnelse IS NULL)
        OR (forlenget_til IS NOT NULL
            AND forlengelse_begrunnelse IS NOT NULL
            AND length(btrim(forlengelse_begrunnelse)) > 0
            AND forlenget_til > frist
            AND forlenget_til <= (mottatt + interval '3 months')::date))
);

-- Sveipens skann og flatens liste leser begge «åpne saker, frist
-- først». Delindeks på `apen`: besvarte og avviste saker er historikk,
-- og sveipen skal aldri betale for dem. Uttrykket er den GJELDENDE
-- fristen (forlengelsen når den finnes) — det er den datoen både
-- sveipen og flaten faktisk spør om.
CREATE INDEX personvernsak_apen_frist
    ON personvernsak (tenant, (COALESCE(forlenget_til, frist)))
    WHERE status = 'apen';

-- ------------------------------------------------------------
-- 1.2 `personvernsak_lager` — hvilke av M-4s lagre saken dekker.
--
-- HELE KOBLINGEN MOT M-4 LIGGER HER, og den er en peker og ikke en
-- fullmakt: saken sier hvilke lagre den gjelder, `retensjonslager`
-- (093) sier hvilken reaper og hvilken frist som gjelder for hvert av
-- dem, og UTFØRELSEN gjøres av den som eier lageret. Ingenting i denne
-- migrasjonen rører en eneste rad i et av dem.
--
-- VAKT, IKKE FREMMEDNØKKEL — og det er en dom, ikke en forenkling.
-- Tre grunner, i stigende styrke:
--
--   1. RETNINGEN PÅ AUTORITETEN. `retensjonslager` er GLOBALT og
--      tenantløst; det beskriver PLATTFORMENS lagre, og 093 sier
--      uttrykkelig at det «endres bare i migrasjon — dommene felles i
--      git, ikke gjennom en dør». En fremmednøkkel fra en TENANT-tabell
--      ville gitt hver enkelt kundes åpne innsynssak vetorett over en
--      plattformdom: så lenge én tenant hadde en sak som pekte på
--      `epost_vedlegg`, kunne ingen senere migrasjon døpe om eller
--      fjerne den raden. Det er feil vei. Registeret her er en LESER av
--      M-4s katalog, ikke en medeier av den.
--
--   2. PRIVILEGIET EN FK KOSTER. `retensjonslager` eies av
--      `disponit_lager_eier`. En FK ville krevd `REFERENCES` på den
--      tabellen — et permanent privilegium som også lar mottakeren
--      lage flere fremmednøkler senere. Vakten under klarer seg med et
--      KOLONNEGRANT på `lager_id` alene (§3): personvernregisteret kan
--      se hvilke lagre som FINNES, ved navn, og ingenting mer. Ikke
--      hvilken klasse de har, ikke hvilken relasjon de peker på, ikke
--      hvilken reaper som gjelder. At registeret aldri kan lese M-4s
--      dommer skal være en egenskap ved BASEN.
--
--   3. FORMEN ER M-4s EGEN. 093 validerer sine egne påstander om basen
--      — at relasjonen finnes, at kolonnene finnes, at reaperen finnes
--      i `pg_proc` — med `to_regclass`- og `pg_proc`-vakter, ikke med
--      fremmednøkler, «fordi de leser på vegne av hvem som helst som
--      skriver registeret». Dette er nøyaktig den samme situasjonen én
--      etasje ned, og en ny form ville bare vært en ny å lære.
--
-- Prisen er ærlig og skal stå her: vakten fanger IKKE at en senere
-- migrasjon fjerner en `retensjonslager`-rad noen sak alt peker på. Det
-- er akseptert — en peker til et lager som ikke lenger finnes er en
-- opplysning saken hadde da den ble registrert, og å slette den ville
-- vært å skrive om historikken for å redde en fremmednøkkel.
-- ------------------------------------------------------------
CREATE TABLE personvernsak_lager (
    tenant TEXT NOT NULL,
    sak_id UUID NOT NULL,
    lager_id TEXT NOT NULL,
    lagt_til TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT personvernsak_lager_pk PRIMARY KEY (tenant, sak_id, lager_id),
    CONSTRAINT personvernsak_lager_sak_fk FOREIGN KEY (tenant, sak_id)
        REFERENCES personvernsak (tenant, sak_id)
);

-- ------------------------------------------------------------
-- 1.3 `personvernfunn` — LUKKET SETT, ETT funn per (sak, funntype).
--
-- En oversittet innsynsfrist er et FUNN, ikke en stille gammel rad.
-- Formen er `begrepsfunn` sin (095), ordrett og av de samme grunnene:
-- ETT funn per (sak, funntype) holdes åpent og oppdateres med
-- `sist_sett_sveip`, så funnlisten ikke vokser med kadensen. En daglig
-- sveip over en sak som har vært oversittet i et halvt år skal gi ETT
-- funn, ikke 180. En funnliste som vokser er en funnliste folk lærer
-- seg å overse — og da forsvinner de viktige med dem.
--
-- FUNN SOM IKKE LENGER GJELDER LUKKES, de slettes aldri: at en frist
-- VAR oversittet er nøyaktig den historikken et tilsyn spør etter, og
-- den skal ikke kunne forsvinne fordi noen svarte til slutt.
--
-- `frist_naermer_seg` OG `frist_oversittet` er to ULIKE funn på samme
-- sak, ikke to navn på ett: en sak passerer fra det ene til det andre,
-- og sveipen lukker det første i samme kjøring som den reiser det
-- andre. Overgangen er selve opplysningen.
-- ------------------------------------------------------------
CREATE TABLE personvernfunn (
    tenant TEXT NOT NULL,
    sak_id UUID NOT NULL,
    funntype TEXT NOT NULL
        CHECK (funntype IN ('frist_oversittet', 'frist_naermer_seg',
                            'sak_uten_lagre')),
    -- Sakens type og gjeldende frist KOPIERES inn i funnet. Funnet skal
    -- kunne leses uten å slå opp saken det peker på — en driftsliste
    -- som må gjøre et oppslag per rad for å bli lesbar, blir ikke lest.
    saktype TEXT NOT NULL,
    gjeldende_frist DATE NOT NULL,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT personvernfunn_pk PRIMARY KEY (tenant, sak_id, funntype),
    CONSTRAINT personvernfunn_sak_fk FOREIGN KEY (tenant, sak_id)
        REFERENCES personvernsak (tenant, sak_id),
    -- Et åpent funn har ingen lukketid, et lukket har alltid en. Den
    -- halve lukkingen er urepresenterbar (089/095-formen).
    CONSTRAINT personvernfunn_lukking_komplett
        CHECK (apen = (lukket_ts IS NULL))
);

CREATE INDEX personvernfunn_apne ON personvernfunn (tenant, funntype)
    WHERE apen;

-- ------------------------------------------------------------
-- 2. Radvaktene.
-- ------------------------------------------------------------

-- Vakten på `personvernsak`. Fire regler, og den tredje er DOM 2:
--
--   * DELETE avvises. Et forespørselsregister der rader kan forsvinne
--     er et register ingen revisjon kan lese bakover — og det er
--     nettopp bakover et tilsyn leser.
--   * Identiteten er frosset (tenant, sak_id, type, subjekt_ref,
--     mottatt, frist, opprettet). En annen rettighet eller et annet
--     subjekt er en ANNEN sak, ikke en redigering av denne, og
--     mottaksdatoen er selve nullpunktet fristen regnes fra: kunne den
--     flyttes, kunne enhver oversittet frist gjøres ugjort.
--   * EN STATUSOVERGANG ER FORFATTET, ALDRI AVLEDET. Enhver endring av
--     `status` krever en navngitt aktør i sesjonen (`disponit.aktor`),
--     og den aktøren MÅ være den som står i `lukket_av`. EN JOBB SOM
--     SKULLE LUKKE EN SAK FORDI TIDEN GIKK HAR INGEN AKTØR Å SKRIVE —
--     og skrev den en, ville navnet stått i raden for enhver som leser.
--     Terminale statuser er terminale: ut av `besvart`/`avvist` finnes
--     ingen vei.
--   * `forlenget_til` kan bare flyttes FRAMOVER, og bare mens saken er
--     åpen. En forlengelse som kunne trekkes tilbake ville vært en vei
--     til å gjøre en oversittet frist uoversittet i ettertid.
CREATE FUNCTION m30_sak_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT;
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'personvernsak: % avvist — en forespørsel besvares'
            ' eller avvises, den slettes aldri som rad', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.sak_id IS DISTINCT FROM OLD.sak_id
       OR NEW.type IS DISTINCT FROM OLD.type
       OR NEW.subjekt_ref IS DISTINCT FROM OLD.subjekt_ref
       OR NEW.mottatt IS DISTINCT FROM OLD.mottatt
       OR NEW.frist IS DISTINCT FROM OLD.frist
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'personvernsak: identiteten (tenant, sak_id, type,'
            ' subjekt_ref, mottatt, frist, opprettet) er frosset — en'
            ' annen rettighet eller et annet subjekt er en annen sak,'
            ' og mottaksdatoen er nullpunktet fristen regnes fra'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.forlenget_til IS DISTINCT FROM OLD.forlenget_til THEN
        IF OLD.status <> 'apen' THEN
            RAISE EXCEPTION 'personvernsak: en lukket sak forlenges ikke'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- NULL fanges FOR SEG. `NULL < dato` er NULL, ikke sant, så en
        -- ren «sett begge feltene tilbake til NULL» ville gått rett
        -- gjennom sammenlikningen under — og da ville forlengelsen, og
        -- begrunnelsen loven krever for den, vært visket ut som om den
        -- aldri hadde skjedd.
        IF OLD.forlenget_til IS NOT NULL AND NEW.forlenget_til IS NULL THEN
            RAISE EXCEPTION 'personvernsak: en forlengelse trekkes ikke'
                ' tilbake — art. 12 nr. 3 krever at den registrerte er'
                ' underrettet om den, og et varsel kan ikke usendes'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF OLD.forlenget_til IS NOT NULL
           AND NEW.forlenget_til < OLD.forlenget_til THEN
            RAISE EXCEPTION 'personvernsak: en forlengelse kan bare flyttes'
                ' framover — en frist som kan trekkes tilbake gjør en'
                ' oversittet frist uoversittet i ettertid'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF OLD.status <> 'apen' THEN
            RAISE EXCEPTION 'personvernsak: % er terminal — en besvart'
                ' eller avvist sak gjenåpnes ikke', OLD.status
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL THEN
            RAISE EXCEPTION 'personvernsak: en statusovergang krever en'
                ' navngitt aktør (disponit.aktor) — tiden lukker ingenting'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.lukket_av IS DISTINCT FROM v_aktor THEN
            RAISE EXCEPTION 'personvernsak: lukket_av (%) er ikke aktøren'
                ' som lukker (%)', coalesce(NEW.lukket_av, '<null>'),
                v_aktor USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    -- SVARET ER EVIDENS, OG EVIDENS FRYSER (CodeRabbit, alvorlig).
    -- Regelen over gjelder bare når `status` SELV endrer seg — og det
    -- var hullet: en sak som alt sto `avvist` kunne få en ny
    -- `avvist_begrunnelse`, en `besvart` sak en annen `svar_ref`, uten
    -- at noen overgang fant sted og dermed uten aktørkravet. Et tilsyn
    -- leser nøyaktig disse fire feltene bakover, og en begrunnelse som
    -- kan skrives om i ettertid er ingen begrunnelse.
    IF OLD.status <> 'apen'
       AND (NEW.svar_ref IS DISTINCT FROM OLD.svar_ref
            OR NEW.svar_ts IS DISTINCT FROM OLD.svar_ts
            OR NEW.avvist_begrunnelse IS DISTINCT FROM OLD.avvist_begrunnelse
            OR NEW.lukket_av IS DISTINCT FROM OLD.lukket_av) THEN
        RAISE EXCEPTION 'personvernsak: svaret på en % sak er frosset —'
            ' svar_ref, svar_ts, avvist_begrunnelse og lukket_av er'
            ' evidensen et tilsyn etterprøver, ikke felt som kan rettes',
            OLD.status USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m30_sak_vakt() FROM PUBLIC;
CREATE TRIGGER m30_sak_vakt
    BEFORE UPDATE OR DELETE ON personvernsak
    FOR EACH ROW EXECUTE FUNCTION m30_sak_vakt();
CREATE TRIGGER m30_sak_ingen_truncate
    BEFORE TRUNCATE ON personvernsak
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- Funnvakten. Identiteten er frosset, DELETE/TRUNCATE avvises — også
-- for eieren. Sveipen får bare flytte ferskhets- og livsløpsfeltene.
-- 095s form, ordrett.
CREATE FUNCTION m30_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'personvernfunn: % avvist — et funn lukkes, det'
            ' slettes aldri; at en frist VAR oversittet er nøyaktig den'
            ' historikken et tilsyn spør etter', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.sak_id IS DISTINCT FROM OLD.sak_id
       OR NEW.funntype IS DISTINCT FROM OLD.funntype
       OR NEW.forst_sett IS DISTINCT FROM OLD.forst_sett THEN
        RAISE EXCEPTION 'personvernfunn: identiteten (tenant, sak,'
            ' funntype) og førstegangsobservasjonen er frosset — et'
            ' annet funn er en annen rad'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.sist_sett_sveip < OLD.sist_sett_sveip THEN
        RAISE EXCEPTION 'personvernfunn: sist_sett_sveip går aldri bakover'
            ' — en ferskhet som kan settes tilbake er ingen ferskhet'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m30_funn_vakt() FROM PUBLIC;
CREATE TRIGGER m30_funn_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON personvernfunn
    FOR EACH ROW EXECUTE FUNCTION m30_funn_vakt();
CREATE TRIGGER m30_funn_ingen_truncate
    BEFORE TRUNCATE ON personvernfunn
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- ------------------------------------------------------------
-- 3. Radsikkerheten og rettighetene.
-- ------------------------------------------------------------

ALTER TABLE personvernsak ENABLE ROW LEVEL SECURITY;
ALTER TABLE personvernsak FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON personvernsak
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — ingen BYPASSRLS.
--
-- Sveipen må finne HVILKE tenanter som har en frist på vei, og det
-- spørsmålet kan ikke stilles innenfra én tenantkontekst. Autoriteten
-- er derfor en policy, ikke en rolleegenskap, og den er gjerdet tre
-- ganger (096-formen, ordrett):
--
--   * bare `disponit_personvern_eier` (dørenes eier — ingen LOGIN-rolle),
--   * bare SELECT (sveipen SKRIVER aldri kryss-tenant: hvert funn
--     skrives etter at konteksten er bundet til RADENS tenant),
--   * bare når det IKKE står en tenantkontekst i sesjonen.
--
-- Det siste leddet er det bærende. Dørene i §4 kommer alltid gjennom
-- `krev_tenantkontekst`, som fail-closed krever en ikke-tom kontekst —
-- inne i en dør er denne policyen derfor ALLTID usann, og
-- `tenant_isolasjon` er den eneste som gjelder. De to er disjunkte per
-- konstruksjon, så kryss-tenant-synet finnes nøyaktig i det ene vinduet
-- sveipen bruker det, og ingen annen kodevei kan snuble inn i det.
CREATE POLICY m30_sveip_tenantliste ON personvernsak
    FOR SELECT TO disponit_personvern_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

ALTER TABLE personvernsak_lager ENABLE ROW LEVEL SECURITY;
ALTER TABLE personvernsak_lager FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON personvernsak_lager
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE personvernfunn ENABLE ROW LEVEL SECURITY;
ALTER TABLE personvernfunn FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON personvernfunn
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- INGEN kryss-tenant-policy på lagerkoblingen og funnene, og det er
-- ikke en forglemmelse: sveipen finner tenantene i `personvernsak`
-- (uten kontekst) og gjør ALT arbeidet med RADENS tenant satt i
-- konteksten. Skrivingen er dermed tenantbundet av RLS, også for
-- sveipen selv (095s begrunnelse, ordrett).

-- Rettighetene dørenes eier trenger, og ikke mer. Merk hva som IKKE
-- står her: ingen runtime-rolle får en eneste tabellrettighet på de tre
-- tabellene (SP-7, 090/091/096-formen) — hele registeret nås KUN gjennom
-- dørene i §4, og de krever tenantkontekst først.
GRANT SELECT, INSERT, UPDATE ON personvernsak TO disponit_personvern_eier;
GRANT SELECT, INSERT ON personvernsak_lager TO disponit_personvern_eier;
GRANT SELECT, INSERT, UPDATE ON personvernfunn TO disponit_personvern_eier;
-- Fremmednøkkelen mot identiteten opprettes over (som migrator, som
-- eier begge tabellene); eieren trenger REFERENCES bare hvis en senere
-- migrasjon skulle legge til flere.
GRANT REFERENCES ON brukeridentitet TO disponit_personvern_eier;
-- Visningsnavnet i lesedøren, og medlemskapssjekken ved registrering.
-- KOLONNEGRANT på identiteten: `issuer` og `sub` er identitetens
-- hemmelige halvdel (010), og et forespørselsregister har ingen bruk
-- for dem.
GRANT SELECT (bruker_id, profil) ON brukeridentitet
    TO disponit_personvern_eier;
GRANT SELECT ON brukermedlemskap TO disponit_personvern_eier;
-- EVIDENSKJEDEN (m02, manifestets første reelle avhengighet): hver
-- registrering, hvert svar, hvert avslag og hver forlengelse skriver
-- sin egen loggpost, i SAMME transaksjon som handlingen. Se §4. INSERT
-- alene — evidenskjeden skrives til, den leses aldri herfra.
GRANT INSERT ON revisjonslogg TO disponit_personvern_eier;

-- M-4-KOBLINGEN, OG NØYAKTIG SÅ MYE SOM VAKTEN TRENGER (§1.2 grunn 2).
-- KOLONNEGRANT, aldri tabellgrant: personvernregisteret skal kunne se
-- at et lager FINNES, ved navn, og ingenting mer — ikke klassen, ikke
-- relasjonen, ikke fristen, ikke reaperen. At registeret aldri kan lese
-- M-4s dommer skal være en egenskap ved BASEN og ikke ved koden som
-- tilfeldigvis ikke gjør det. Grantet gis AV lagerets eier (039/074-
-- formen); migrator er medlem av begge roller.
SET LOCAL ROLE disponit_lager_eier;
GRANT SELECT (lager_id) ON retensjonslager TO disponit_personvern_eier;
RESET ROLE;

-- Kontekstporten eies av `disponit_m37_claimer` og er REVOKEd fra
-- PUBLIC (038). Dørene under er SECURITY DEFINER og løper som
-- `disponit_personvern_eier` — uten dette grantet ville SP-1-porten
-- feilet med «permission denied», og registeret vært nede i stedet for
-- sikret. Grantet gis av eieren selv (039-formen).
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_personvern_eier;
RESET ROLE;

-- ------------------------------------------------------------
-- 4. Dørene. SECURITY DEFINER, eid av `disponit_personvern_eier`, og
--    hver tenantbundet dør kaller `krev_tenantkontekst` FØRST (SP-1).
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_personvern_eier;

-- M-4-VAKTEN (§1.2). SECURITY DEFINER fordi den leser `retensjonslager`
-- på vegne av hvem som helst som skriver koblingstabellen — nøyaktig
-- 093s egen begrunnelse for sine to registervakter. Uten definer ville
-- direkte DML som migrator dødd på manglende kolonnegrant, og vakten
-- vært en port som bare gjelder den ene skriveveien den ble skrevet
-- for.
--
-- Den leser INGEN tenantkontekst og kaller derfor ikke
-- `krev_tenantkontekst`: `retensjonslager` er globalt og tenantløst, og
-- en SP-1-port her ville krevd en kontekst av en vakt som ikke ser en
-- eneste tenantrad. Radens egen tenantisolasjon er RLS-en på
-- `personvernsak_lager`, som gjelder uansett hva vakten gjør.
-- Vakten er dessuten APPEND-ONLY: dekningslisten er en del av sakens
-- MATERIELLE innhold (den er med i SP-2-materialiteten i
-- `m30_registrer_sak`), og hvilke lagre en forespørsel dekket da den ble
-- registrert er nøyaktig det et tilsyn etterprøver svaret mot. Kunne
-- listen endres i ettertid, kunne et ufullstendig svar gjøres
-- fullstendig med en UPDATE.
CREATE FUNCTION m30_lager_vakt()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'personvernsak_lager er append-only: % er forbudt'
            ' — hvilke lagre en forespørsel dekket er det et tilsyn'
            ' etterprøver svaret mot', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.retensjonslager l
                    WHERE l.lager_id = NEW.lager_id) THEN
        RAISE EXCEPTION 'personvernsak_lager: lageret % står ikke i M-4s'
            ' retensjonsregister — en sak kan bare dekke lagre noen har'
            ' navngitt og felt en dom over', NEW.lager_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m30_lager_vakt() FROM PUBLIC;
-- Migrator EIER tabellen og må derfor kunne henge vakten på den (§5).
-- Grantet er EXECUTE på den ene vaktfunksjonen og ingenting mer — det
-- er prisen for at tabellen er migrators mens vakten er eierens, og den
-- delingen er selve poenget: vakten leser M-4s register på vegne av
-- hvem som helst som skriver koblingen.
GRANT EXECUTE ON FUNCTION m30_lager_vakt() TO disponit_migrator;

-- Evidenskjeden, ett sted. Kalles av hver skrivedør, i dørens egen
-- transaksjon. Formen er `m21_evidens` sin (096), ordrett — og
-- begrunnelsen for `payload_type`-valget der gjelder like fullt her.
--
-- `input_hash` er sha256 over den KANONISKE BESKRIVELSEN av handlingen,
-- ikke over kundedata. Og her er den regelen skarpere enn noe annet
-- sted i huset: `subjekt_ref` står ALDRI i evidensraden. En
-- evidenskjede som arkiverte hvem som hadde bedt om innsyn, ville gjort
-- selve dokumentasjonen av personvernarbeidet til et nytt persondatalager
-- — og et som er append-only og aldri kan rettes.
CREATE FUNCTION m30_evidens(p_tenant TEXT, p_sak_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm30_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm30_personvern', 'handling', p_handling,
        'sak_id', p_sak_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm30_personvern',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:personvernregister', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m30_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- Art. 12 nr. 3s ordinære frist, ett sted. IMMUTABLE: den leser ingen
-- klokke — den regner én dato ut av en annen, og «én måned» er
-- kalendermåneden loven mener, ikke 30 døgn. En feilmerket volatilitet
-- er en av de få feilene planleggeren gjør permanent.
CREATE FUNCTION m30_ordinaer_frist(p_mottatt DATE) RETURNS DATE
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
    SELECT (p_mottatt + interval '1 month')::date
$$;
REVOKE ALL ON FUNCTION m30_ordinaer_frist(DATE) FROM PUBLIC;

-- Registreringsdøren. SP-2-materialitet på `p_sak_id` (m35/096-formen):
-- kalleren utleder id-en deterministisk av sin Idempotency-Key, så et
-- gjenspill med identisk innhold er et STILLE JA (false), mens samme id
-- med ANNET innhold er en materiell konflikt.
--
-- FRISTEN REGNES HER, den mottas ikke. Kalleren oppgir `mottatt`;
-- registeret regner én måned. En frist kalleren kunne skrive fritt ville
-- gjort «oversittet» til en mening i stedet for et faktum — CHECK-en i
-- §1.1 stenger for det uansett, men døren skal ikke engang be om
-- tallet.
CREATE FUNCTION m30_registrer_sak(
    p_tenant TEXT, p_sak_id UUID, p_type TEXT, p_subjekt_ref TEXT,
    p_mottatt DATE, p_eier_bruker_id TEXT, p_lager_id TEXT[], p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_frist DATE; v_gammel RECORD; v_gamle_lagre TEXT[];
        v_lagre TEXT[];
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm30_registrer_sak');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    -- DOM 1, håndhevet FØR innsettingen slik at feilmeldingen sier hva
    -- som er galt: eieren må være et AKTIVT medlem av tenanten. FK-en
    -- alene sier bare at bruker-id-en finnes et sted i plattformen — og
    -- en innsynsforespørsel eid av en fremmed tenants bruker er en
    -- forespørsel ingen her besvarer.
    IF NOT EXISTS (SELECT 1 FROM public.brukermedlemskap bm
                    WHERE bm.tenant = p_tenant
                      AND bm.bruker_id = p_eier_bruker_id AND bm.aktiv) THEN
        RAISE EXCEPTION 'm30_registrer_sak: % er ikke et aktivt medlem av'
            ' tenanten — en forespørsel uten eier er en forespørsel ingen'
            ' besvarer', p_eier_bruker_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Lagerlisten normaliseres FØR den brukes, så materialitetsdommen
    -- under sammenlikner to sett og ikke to rekkefølger.
    SELECT array_agg(DISTINCT l ORDER BY l) INTO v_lagre
      FROM unnest(coalesce(p_lager_id, ARRAY[]::TEXT[])) AS l
     WHERE length(btrim(l)) > 0;
    -- MOTTAKSDATOEN ER NULLPUNKTET, og den kan ikke ligge FRAM i tid.
    -- Registeret kan ikke vite når anmodningen faktisk kom — det stoler
    -- på det som skrives — men en dato i framtiden er alltid en
    -- inntastingsfeil, og den ville skjøvet hele art. 12-klokka med seg.
    -- Vakten fryser `mottatt` etterpå, så feilen ville vært umulig å
    -- rette uten å registrere saken på nytt.
    IF p_mottatt IS NULL OR p_mottatt > current_date THEN
        RAISE EXCEPTION 'm30_registrer_sak: mottaksdatoen (%) kan ikke'
            ' ligge fram i tid — den er nullpunktet fristen regnes fra',
            p_mottatt USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_frist := public.m30_ordinaer_frist(p_mottatt);
    INSERT INTO public.personvernsak
        (tenant, sak_id, type, subjekt_ref, mottatt, frist,
         eier_bruker_id, opprettet_av)
    VALUES (p_tenant, p_sak_id, p_type, p_subjekt_ref, p_mottatt, v_frist,
            p_eier_bruker_id, p_aktor)
        ON CONFLICT (tenant, sak_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        -- SP-2: samme id igjen. Identisk innhold er et stille ja; annet
        -- innhold er en materiell konflikt kalleren SKAL se.
        --
        -- MATERIALITETEN DEKKER LAGERLISTEN (096s CodeRabbit-lærdom):
        -- hvilke lagre saken dekker er ikke pynt — det er hele
        -- koblingen mot M-4, og et gjenspill som stille utvidet eller
        -- innsnevret den ville endret hva saken FAKTISK gjelder uten at
        -- noen fikk vite det.
        SELECT * INTO v_gammel FROM public.personvernsak
         WHERE tenant = p_tenant AND sak_id = p_sak_id;
        SELECT array_agg(sl.lager_id ORDER BY sl.lager_id)
          INTO v_gamle_lagre
          FROM public.personvernsak_lager sl
         WHERE sl.tenant = p_tenant AND sl.sak_id = p_sak_id;
        IF v_gammel.type IS DISTINCT FROM p_type
           OR v_gammel.subjekt_ref IS DISTINCT FROM p_subjekt_ref
           OR v_gammel.mottatt IS DISTINCT FROM p_mottatt
           OR v_gammel.eier_bruker_id IS DISTINCT FROM p_eier_bruker_id
           OR v_gamle_lagre IS DISTINCT FROM v_lagre THEN
            RAISE EXCEPTION 'm30_registrer_sak: samme sak_id med annet'
                ' innhold — materiell konflikt'
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN false;
    END IF;
    INSERT INTO public.personvernsak_lager (tenant, sak_id, lager_id)
    SELECT p_tenant, p_sak_id, l
      FROM unnest(coalesce(v_lagre, ARRAY[]::TEXT[])) AS l;
    -- LAGERLISTEN STÅR I EVIDENSEN, subjektet gjør det ikke. Hvilke
    -- lagre en forespørsel dekker er den ene opplysningen et tilsyn
    -- trenger for å etterprøve om svaret var fullstendig.
    PERFORM public.m30_evidens(
        p_tenant, p_sak_id, 'personvernsak.registrert', p_aktor,
        jsonb_build_object('type', p_type, 'mottatt', p_mottatt,
                           'frist', v_frist,
                           'eier_bruker_id', p_eier_bruker_id,
                           'lager_id',
                           to_jsonb(coalesce(v_lagre, ARRAY[]::TEXT[]))));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m30_registrer_sak(
    TEXT, UUID, TEXT, TEXT, DATE, TEXT, TEXT[], TEXT) FROM PUBLIC;

-- Svardøren. SVARHENVISNINGEN ER PÅKREVD — det er hele akseptkravet, og
-- den står her som en RAISE og ikke bare som en CHECK, for at feilen
-- skal si hvorfor.
--
-- MERK HVA DØREN IKKE GJØR: den sletter ingenting, og den ber ingen
-- annen om å slette noe. Saken lukkes fordi et menneske har skrevet at
-- den er besvart — utførelsen gjøres av den som eier lageret, og M-4s
-- reapere fortsetter å gjøre den nøyaktig der de gjør den i dag.
CREATE FUNCTION m30_besvar_sak(
    p_tenant TEXT, p_sak_id UUID, p_svar_ref TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE s RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm30_besvar_sak');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_svar_ref IS NULL OR length(btrim(p_svar_ref)) = 0 THEN
        RAISE EXCEPTION 'm30_besvar_sak: en forespørsel lukkes av et'
            ' skrevet svar, aldri av at fristen passerer —'
            ' svarhenvisningen kan ikke være tom'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO s FROM public.personvernsak
     WHERE tenant = p_tenant AND sak_id = p_sak_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm30_besvar_sak: ukjent sak %', p_sak_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF s.status <> 'apen' THEN
        RAISE EXCEPTION 'm30_besvar_sak: saken er alt %', s.status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.personvernsak
       SET status = 'besvart', svar_ref = p_svar_ref, svar_ts = now(),
           lukket_av = p_aktor
     WHERE tenant = p_tenant AND sak_id = p_sak_id;
    -- Åpne funn på saken lukkes i SAMME transaksjon. Et funn som står
    -- igjen etter at saken er besvart er en driftsliste som lyver, og
    -- neste sveip ville lukket det uansett — men først i morgen, og en
    -- funnliste som er et døgn på etterskudd er en funnliste ingen
    -- stoler på. Raden består: at fristen VAR oversittet er historikk.
    UPDATE public.personvernfunn
       SET apen = false, lukket_ts = now()
     WHERE tenant = p_tenant AND sak_id = p_sak_id AND apen;
    PERFORM public.m30_evidens(
        p_tenant, p_sak_id, 'personvernsak.besvart', p_aktor,
        jsonb_build_object('type', s.type, 'svar_ref', p_svar_ref,
                           'frist', coalesce(s.forlenget_til, s.frist),
                           'innen_frist',
                           current_date <= coalesce(s.forlenget_til,
                                                    s.frist)));
END $$;
REVOKE ALL ON FUNCTION m30_besvar_sak(TEXT, UUID, TEXT, TEXT) FROM PUBLIC;

-- Avslagsdøren. Den ANDRE lovlige utgangen — og den er ikke en billig
-- en. Art. 12 nr. 4 krever at den registrerte får VITE hvorfor
-- anmodningen ikke etterkommes; uten en skreven begrunnelse ville
-- «avvist» vært en gratis vei ut av enhver frist, og registeret en
-- liste over ting man kan klikke bort.
CREATE FUNCTION m30_avvis_sak(
    p_tenant TEXT, p_sak_id UUID, p_begrunnelse TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE s RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm30_avvis_sak');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_begrunnelse IS NULL OR length(btrim(p_begrunnelse)) = 0 THEN
        RAISE EXCEPTION 'm30_avvis_sak: et avslag krever en skreven'
            ' begrunnelse — den registrerte har krav på å få vite hvorfor'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO s FROM public.personvernsak
     WHERE tenant = p_tenant AND sak_id = p_sak_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm30_avvis_sak: ukjent sak %', p_sak_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF s.status <> 'apen' THEN
        RAISE EXCEPTION 'm30_avvis_sak: saken er alt %', s.status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.personvernsak
       SET status = 'avvist', avvist_begrunnelse = p_begrunnelse,
           svar_ts = now(), lukket_av = p_aktor
     WHERE tenant = p_tenant AND sak_id = p_sak_id;
    UPDATE public.personvernfunn
       SET apen = false, lukket_ts = now()
     WHERE tenant = p_tenant AND sak_id = p_sak_id AND apen;
    -- LENGDEN, IKKE TEKSTEN. Begrunnelsen er skrevet om en navngitt
    -- person og hører hjemme i saken, ikke i en append-only evidenskjede
    -- som aldri kan rettes.
    PERFORM public.m30_evidens(
        p_tenant, p_sak_id, 'personvernsak.avvist', p_aktor,
        jsonb_build_object('type', s.type,
                           'begrunnelse_lengde', length(p_begrunnelse),
                           'frist', coalesce(s.forlenget_til, s.frist)));
END $$;
REVOKE ALL ON FUNCTION m30_avvis_sak(TEXT, UUID, TEXT, TEXT) FROM PUBLIC;

-- Forlengelsesdøren (DOM 3). Art. 12 nr. 3: to måneder ekstra, med en
-- begrunnelse, og den registrerte skal underrettes innen én måned.
-- Taket og begrunnelseskravet er CHECK-er i §1.1 — de tre RAISE-ene her
-- finnes for at feilen skal si HVA som er galt i stedet for hvilken
-- constraint som brøt.
CREATE FUNCTION m30_forleng_frist(
    p_tenant TEXT, p_sak_id UUID, p_forlenget_til DATE,
    p_begrunnelse TEXT, p_aktor TEXT)
RETURNS DATE LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE s RECORD; v_tak DATE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm30_forleng_frist');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_begrunnelse IS NULL OR length(btrim(p_begrunnelse)) = 0 THEN
        RAISE EXCEPTION 'm30_forleng_frist: en forlengelse krever en'
            ' skreven begrunnelse — art. 12 nr. 3 gir to måneder ekstra'
            ' MOT en årsak, ikke på forespørsel'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_forlenget_til IS NULL THEN
        RAISE EXCEPTION 'm30_forleng_frist: en forlengelse uten dato er'
            ' ingen forlengelse'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO s FROM public.personvernsak
     WHERE tenant = p_tenant AND sak_id = p_sak_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm30_forleng_frist: ukjent sak %', p_sak_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF s.status <> 'apen' THEN
        RAISE EXCEPTION 'm30_forleng_frist: saken er alt % — en lukket'
            ' sak forlenges ikke', s.status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_tak := (s.mottatt + interval '3 months')::date;
    IF p_forlenget_til > v_tak THEN
        RAISE EXCEPTION 'm30_forleng_frist: art. 12 nr. 3 gir TO måneder'
            ' ekstra — taket for denne saken er % (mottatt %)',
            v_tak, s.mottatt USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_forlenget_til <= coalesce(s.forlenget_til, s.frist) THEN
        RAISE EXCEPTION 'm30_forleng_frist: den nye fristen (%) er ikke'
            ' senere enn den gjeldende (%)', p_forlenget_til,
            coalesce(s.forlenget_til, s.frist)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.personvernsak
       SET forlenget_til = p_forlenget_til,
           forlengelse_begrunnelse = p_begrunnelse
     WHERE tenant = p_tenant AND sak_id = p_sak_id;
    PERFORM public.m30_evidens(
        p_tenant, p_sak_id, 'personvernsak.frist_forlenget', p_aktor,
        jsonb_build_object('fra', coalesce(s.forlenget_til, s.frist),
                           'til', p_forlenget_til,
                           'begrunnelse_lengde', length(p_begrunnelse)));
    RETURN p_forlenget_til;
END $$;
REVOKE ALL ON FUNCTION m30_forleng_frist(TEXT, UUID, DATE, TEXT, TEXT)
    FROM PUBLIC;

-- Lesedøren (051/090/096-formen): flatens hele lesetilstand i ett kall.
-- Runtime har INGEN SELECT på tabellene, så dette er den eneste veien
-- inn — og den krever tenantkontekst først.
--
-- `dogn_til_frist` REGNES HER, i samme skann som raden, fordi flaten
-- ikke skal trekke to datoer fra hverandre. Det er hele flatens
-- viktigste jobb (hvor mange dager er igjen), og et tall som regnes to
-- steder blir to ulike tall den dagen tidssonen spriker.
--
-- Lagerlisten er med som et array: hvilke lagre saken dekker er ikke en
-- detalj — det er koblingen mot M-4, og en flate som måtte gjøre et
-- ekstra kall for å se den ville vist saken uten den.
CREATE FUNCTION m30_saker(p_tenant TEXT, p_grense INT)
RETURNS TABLE(sak_id UUID, saktype TEXT, subjekt_ref TEXT, mottatt DATE,
              frist DATE, forlenget_til DATE,
              forlengelse_begrunnelse TEXT, gjeldende_frist DATE,
              dogn_til_frist INT, eier_bruker_id TEXT, eier_navn TEXT,
              status TEXT, svar_ref TEXT, svar_ts TIMESTAMPTZ,
              avvist_begrunnelse TEXT, lukket_av TEXT, lager_id TEXT[],
              apne_funn TEXT[])
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm30_saker');
    RETURN QUERY
    SELECT s.sak_id, s.type, s.subjekt_ref, s.mottatt, s.frist,
           s.forlenget_til, s.forlengelse_begrunnelse,
           coalesce(s.forlenget_til, s.frist),
           -- HELE DØGN til den gjeldende fristen, negativt når den er
           -- forbi. Ett tall avledet av én dato — ikke et forhold
           -- mellom to av svarets tall (M-16-regelen).
           (coalesce(s.forlenget_til, s.frist) - current_date)::int,
           s.eier_bruker_id,
           -- Visningsnavnet fra den LUKKEDE profil-DTO-en (010). NULL
           -- når IdP-en ikke ga noe — flaten viser da bruker-id-en, som
           -- er ærligere enn en tom celle.
           nullif(btrim(coalesce(b.profil->>'visningsnavn', '')), ''),
           s.status, s.svar_ref, s.svar_ts, s.avvist_begrunnelse,
           s.lukket_av,
           coalesce((SELECT array_agg(sl.lager_id ORDER BY sl.lager_id)
                       FROM public.personvernsak_lager sl
                      WHERE sl.tenant = s.tenant AND sl.sak_id = s.sak_id),
                    ARRAY[]::TEXT[]),
           coalesce((SELECT array_agg(f.funntype ORDER BY f.funntype)
                       FROM public.personvernfunn f
                      WHERE f.tenant = s.tenant AND f.sak_id = s.sak_id
                        AND f.apen),
                    ARRAY[]::TEXT[])
      FROM public.personvernsak s
      LEFT JOIN public.brukeridentitet b ON b.bruker_id = s.eier_bruker_id
     WHERE s.tenant = p_tenant
     -- Åpne først (det som fortsatt krever noe av noen), deretter frist
     -- stigende: det som forfaller først står øverst, og det som er
     -- oversittet står aller øverst.
     ORDER BY (s.status <> 'apen'), coalesce(s.forlenget_til, s.frist),
              s.sak_id
     LIMIT greatest(least(coalesce(p_grense, 200), 500), 1);
END $$;
REVOKE ALL ON FUNCTION m30_saker(TEXT, INT) FROM PUBLIC;

-- Funnlesingen. Et funn ingen kan se er ikke et funn — det er en rad.
-- Denne døren er grunnen til at fristsveipen er synlig i flaten og ikke
-- bare i en JSON-linje i journalen (095s begrunnelse, ordrett).
CREATE FUNCTION m30_apne_funn(p_tenant TEXT, p_grense INT)
RETURNS TABLE(sak_id UUID, funntype TEXT, saktype TEXT,
              gjeldende_frist DATE, dogn_til_frist INT,
              forst_sett TIMESTAMPTZ, sist_sett_sveip TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm30_apne_funn');
    RETURN QUERY
    SELECT f.sak_id, f.funntype, f.saktype, f.gjeldende_frist,
           (f.gjeldende_frist - current_date)::int,
           f.forst_sett, f.sist_sett_sveip
      FROM public.personvernfunn f
     WHERE f.apen
     -- Oversittede først: det er de som er lovbrudd, ikke forsinkelser.
     ORDER BY (f.funntype <> 'frist_oversittet'), f.gjeldende_frist,
              f.sak_id
     LIMIT greatest(least(coalesce(p_grense, 100), 500), 1);
END $$;
REVOKE ALL ON FUNCTION m30_apne_funn(TEXT, INT) FROM PUBLIC;

-- SVEIPENS KANDIDATSETT, ETT STED. Regelen for hva som ER et funn står
-- her og ingen andre steder: sveipen under bruker den tre ganger (fersk
-- opp, sett inn, lukk), og tre kopier av det samme predikatet er tre
-- steder å glemme den fjerde funntypen den dagen den kommer.
--
--   * `frist_oversittet` — den GJELDENDE fristen (forlengelsen når den
--     finnes) er passert. Dette er et lovbrudd, ikke en forsinkelse.
--   * `frist_naermer_seg` — fristen ligger innenfor varselvinduet.
--   * `sak_uten_lagre` — en åpen sak som ikke peker på ett eneste av
--     M-4s lagre. Den er ikke et formfeil: en innsyns- eller
--     sletteforespørsel som ikke sier HVOR den gjelder, kan ingen
--     etterprøve svaret på, og «vi svarte» blir en påstand uten en
--     flate å måle den mot.
--
-- STABLE, ikke IMMUTABLE: den leser tabeller. Og den tar `p_dag` som
-- parameter i stedet for å lese `current_date` selv, nettopp for at de
-- tre kallene i én sveipekjøring garantert skal se den SAMME dagen —
-- en kjøring som krysser midnatt skal ikke kunne reise et funn i steg 2
-- og lukke det i steg 3.
CREATE FUNCTION m30_sveipkandidater(p_tenant TEXT, p_dag DATE, p_vindu INT)
RETURNS TABLE(sak_id UUID, funntype TEXT, saktype TEXT,
              gjeldende_frist DATE)
LANGUAGE sql STABLE SET search_path = pg_catalog AS $$
    SELECT s.sak_id,
           CASE WHEN coalesce(s.forlenget_til, s.frist) < p_dag
                THEN 'frist_oversittet' ELSE 'frist_naermer_seg' END,
           s.type, coalesce(s.forlenget_til, s.frist)
      FROM public.personvernsak s
     WHERE s.tenant = p_tenant AND s.status = 'apen'
       AND coalesce(s.forlenget_til, s.frist) <= p_dag + p_vindu
    UNION ALL
    SELECT s.sak_id, 'sak_uten_lagre', s.type,
           coalesce(s.forlenget_til, s.frist)
      FROM public.personvernsak s
     WHERE s.tenant = p_tenant AND s.status = 'apen'
       AND NOT EXISTS (
            SELECT 1 FROM public.personvernsak_lager sl
             WHERE sl.tenant = s.tenant AND sl.sak_id = s.sak_id)
$$;
REVOKE ALL ON FUNCTION m30_sveipkandidater(TEXT, DATE, INT) FROM PUBLIC;

-- ------------------------------------------------------------
-- FRISTSVEIPEN. Kryss-tenant, innelukket autoritet (038/095-formen):
-- INTET tenantparameter, utvalget ER predikatet, og alt funnarbeid
-- gjøres med RADENS tenant i konteksten.
--
-- `p_grense` er VARSELVINDUET i døgn: hvor mange dager før den
-- gjeldende fristen en åpen sak blir et `frist_naermer_seg`-funn.
--
-- SVEIPEN SETTER ALDRI `status`, OG DEN SLETTER ALDRI NOE. Det er
-- v1-dommen målt i kode: de eneste tabellene den skriver til er
-- `personvernfunn`, og de eneste setningene er INSERT og UPDATE på den.
-- Tiden lukker ingenting; en oversittet frist blir et funn, ikke en
-- lukket sak.
--
-- TENANTLISTEN MATERIALISERES før første `set_config`. En åpen cursor
-- over `personvernsak` mens tenantkonteksten endres under føttene på
-- den ville vært et RLS-predikat som skifter mening midt i en løkke —
-- riktig svar i test, uforutsigbart under last. Selve KANDIDATSETTET
-- regnes per tenant, etter at konteksten er bundet til radens tenant,
-- og leses derfor gjennom `tenant_isolasjon` som alt annet.
-- ------------------------------------------------------------
CREATE FUNCTION m30_sveip_frister(p_grense INT DEFAULT 7)
RETURNS TABLE(tenanter INT, nye INT, oppdaterte INT, lukkede INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_n INT;
        v_dag DATE; v_naa TIMESTAMPTZ; v_vindu INT;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm30_sveip_frister: sveipen er KRYSS-TENANT og'
            ' kjøres uten tenantkontekst — en kaller som har satt en'
            ' kontekst ber om noe annet enn det denne funksjonen gjør'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_vindu := greatest(least(coalesce(p_grense, 7), 90), 0);
    v_dag := current_date;
    v_naa := now();
    tenanter := 0; nye := 0; oppdaterte := 0; lukkede := 0;
    SELECT array_agg(DISTINCT s.tenant ORDER BY s.tenant) INTO v_tenanter
      FROM public.personvernsak s WHERE s.status = 'apen';
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        tenanter := tenanter + 1;
        -- 1. Ferskheten på funn som alt finnes. IDEMPOTENSEN BOR HER:
        --    en sveip nummer to på den samme oversittede saken flytter
        --    `sist_sett_sveip` og skriver ingen ny rad. En sak som har
        --    vært oversittet i et halvt år har ETT funn, ikke 180.
        UPDATE public.personvernfunn f
           SET sist_sett_sveip = v_naa, apen = true, lukket_ts = NULL,
               saktype = k.saktype, gjeldende_frist = k.gjeldende_frist
          FROM (SELECT * FROM public.m30_sveipkandidater(v_t, v_dag,
                                                         v_vindu)) k
         WHERE f.sak_id = k.sak_id AND f.funntype = k.funntype;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        oppdaterte := oppdaterte + v_n;
        -- 2. De nye.
        INSERT INTO public.personvernfunn
            (tenant, sak_id, funntype, saktype, gjeldende_frist,
             forst_sett, sist_sett_sveip, apen)
        SELECT v_t, k.sak_id, k.funntype, k.saktype, k.gjeldende_frist,
               v_naa, v_naa, true
          FROM public.m30_sveipkandidater(v_t, v_dag, v_vindu) k
            ON CONFLICT (tenant, sak_id, funntype) DO NOTHING;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        nye := nye + v_n;
        -- 3. Lukkingen. Et funn som ikke lenger gjelder — saken ble
        --    besvart, fristen ble forlenget forbi vinduet, lagrene ble
        --    ført på — lukkes. Raden består: at en frist VAR oversittet
        --    er historikk et tilsyn spør etter.
        UPDATE public.personvernfunn f
           SET apen = false, lukket_ts = v_naa
         WHERE f.apen
           AND NOT EXISTS (
                SELECT 1 FROM public.m30_sveipkandidater(v_t, v_dag,
                                                         v_vindu) k
                 WHERE k.sak_id = f.sak_id AND k.funntype = f.funntype);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        lukkede := lukkede + v_n;
    END LOOP;
    -- Konteksten legges tilbake der den sto: en sveip skal ikke
    -- etterlate seg en tenant i sesjonen den ikke ble kalt med.
    PERFORM set_config('disponit.tenant', '', true);
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m30_sveip_frister(INT) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 5. Rettighetene — INNE i eierblokken der de gjelder eier-eide
--    funksjoner (#140-læren: en REVOKE utenfor lot funksjonen stå
--    PUBLIC-kjørbar mellom to setninger).
--
--    Migrasjonen NAVNGIR IKKE runtime-rollen (057-lærdommen):
--    `deploy/staging/migrer.py` er autoritativ for den konfigurerte
--    rollen. REVOKE-en under er lovlig og nødvendig (091-formen): en
--    rettighet som bare slutter å bli gitt er ikke trukket tilbake.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_personvern_eier;
DO $$
BEGIN
    -- Sveipen: ÉN EXECUTE til sveiperollen. Ingen tabellrettigheter —
    -- rollen har ingen i dag, og M-30 gir den ingen. Innsnevringen er
    -- skarpere enn den ser ut: `m30_sveip_frister` er kryss-tenant og
    -- setter selv RLS-konteksten per tenant, så rollen kan reise funn i
    -- alle tenanter uten å kunne LESE en eneste sak selv. Hele
    -- autoriteten står i den eier-eide defineren, revidérbar på ett
    -- sted.
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_personvernsveip') THEN
        GRANT EXECUTE ON FUNCTION m30_sveip_frister(INT)
            TO disponit_personvernsveip;
    END IF;
    -- Runtime skal ALDRI kunne kjøre sveipen: den er kryss-tenant, og
    -- et grant her ville gitt forespørselsveien nøyaktig det vinduet
    -- sveiperollen finnes for å nekte den (038-reaperens snitt).
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        REVOKE ALL ON FUNCTION m30_sveip_frister(INT) FROM disponit;
    END IF;
END $$;
RESET ROLE;

-- Vakten på koblingstabellen kobles på til slutt, som migrator: selve
-- TRIGGEREN eies av tabellens eier, mens FUNKSJONEN den kaller er
-- eier-eid og SECURITY DEFINER (§4). Det er samme deling som 093 gjør
-- med sine to registervakter.
CREATE TRIGGER m30_lager_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON personvernsak_lager
    FOR EACH ROW EXECUTE FUNCTION m30_lager_vakt();
CREATE TRIGGER m30_lager_ingen_truncate
    BEFORE TRUNCATE ON personvernsak_lager
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

COMMENT ON TABLE personvernsak IS
    'M-30 (099): forespørselsregisteret. Registeret SLETTER INGENTING — '
    'sletting eies av M-4s retensjonsregnskap (093) og de seks reaperne '
    'som kjører. En sak lukkes av et SKREVET SVAR, aldri av at fristen '
    'passerer.';
COMMENT ON TABLE personvernsak_lager IS
    'M-30 (099): hvilke av M-4s lagre en sak dekker. Peker på '
    'retensjonslager.lager_id gjennom en vakt, ikke en fremmednøkkel — '
    'registeret er en LESER av M-4s katalog, ikke en medeier av den.';
COMMENT ON TABLE personvernfunn IS
    'M-30 (099): ETT funn per (sak, funntype), oppdatert med '
    'sist_sett_sveip. En oversittet innsynsfrist er et lovbrudd, ikke '
    'en forsinkelse — og derfor et funn, ikke en stille gammel rad.';
