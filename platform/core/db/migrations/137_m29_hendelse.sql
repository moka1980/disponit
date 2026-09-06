-- =====================================================================
-- M-29 SIKKERHETS- OG HENDELSESAGENT (v1) — KLYNGE 10s FØRSTE.
-- =====================================================================
--
-- V1-DOMMEN: MODULEN GJØR INGEN INNGREP. Den isolerer ingen konto,
-- roterer ingen hemmelighet og kjører ingen kommando. Den korrelerer,
-- scorer med forklarbare regler, og stopper der.
--
-- ---------------------------------------------------------------------
-- KLYNGENS DELTE DOM:
--
--   EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN ANGRES IKKE AV EN
--   ROLLBACK.
--
-- Klynge 9s ytring kunne ikke tas tilbake fordi noen hadde LEST den.
-- Denne trenger ingen leser: kontoen er stengt, hemmeligheten er
-- rullet, og tokenet den gamle klienten holdt er dødt. Databasen kan
-- rulles tilbake til sekundet før — klienten er fortsatt logget ut.
--
-- ---------------------------------------------------------------------
-- DETTE ER KLYNGENS FARLIGSTE MODUL, OG GRUNNEN ER MÅLT.
--
-- De tre andre venter på data som ikke finnes. M-29 gjør ikke det:
-- FULLMAKTSMÅLENE LIGGER ALLEREDE I BASEN.
--
--   `api_tokener`            — `secret_mac`, `status`, `utloper`
--   `modultoken`             — `tilbakekalt_ts`, `tilbakekalt_grunn`
--   `brukersesjon`           — `tilbakekalt`, `authz_snapshot`
--   `tenant_pseudonymnokkel` — `nokkel`
--   `brukeridentitet`        — `issuer`, `sub`
--
-- Det er nøyaktig de radene en «isoler konto, token eller workload og
-- roter secrets»-modul ville skrevet i. En modul med UPDATE på dem
-- kunne stengt huset ute av seg selv, og den ville gjort det raskere
-- enn noe menneske rakk å lese logglinjen.
--
-- DENNE MIGRASJONEN GIR INGEN AV DEM. Ikke SELECT, ikke INSERT, ikke
-- UPDATE, ikke DELETE — verken til eieren eller til sveipen.
-- `test_m29_hendelse.py` måler det mot `has_table_privilege`, ikke mot
-- prosaen her.
--
-- ---------------------------------------------------------------------
-- «INGEN FRI KOMMANDOKJØRING» ER IKKE EN POLICY. DET ER EN GRAMMATIKK.
--
-- En playbook i dette huset er en LISTE MED NAVNGITTE STEG fra et
-- lukket sett, og `playbooksteg` har INGEN kolonne som kan bære en
-- parameter, en streng, en sti eller et skript.
--
-- Det er forskjellen på å forby noe og å gjøre det uuttrykkelig:
-- playbooken kan ikke SI en fri kommando. Et sett med en
-- `annet`-verdi og en fritekstkolonne ville vært det samme som ingen
-- grense — det er nettopp den formen 116 kalte
-- `klassifisering_utenfor_lukket_sett`.
--
-- ---------------------------------------------------------------------
-- MODULEN LESER NOE DEN OGSÅ SKRIVER I, OG DEN ER ALENE OM DET.
--
-- `revisjonslogg` (M-2) er husets eneste applikasjonslogg OG M-29s
-- viktigste signalkilde. Hver modul skriver evidens dit — `m29_evidens`
-- er husets form og ikke et unntak.
--
-- Faren er ikke skrivingen. Den er at modulen leser SITT EGET spor som
-- et signal, korrelerer på det, og scorer sin egen forrige handling.
-- DA VOKSER HENDELSEN AV Å BLI SETT PÅ.
--
-- `m29_signalkilden` filtrerer derfor `kilde <> 'm29_hendelse'` i
-- SPØRRINGEN, ikke i en instruks. Grensen heter
-- `leste_sitt_eget_spor_som_signal`.
--
-- ---------------------------------------------------------------------
-- FIRE FUNN SOM ALDRI KAN REISES, OG DET ER BEVISET.
--
--   `inngrep_uten_playbook`  — `inngrepsforslag.playbook_id` NOT NULL,
--                              fremmednøkkel. OG: tabellen har ingen
--                              `utfort_ts`. Forslaget er endestasjonen.
--   `fri_kommando_kjort`     — `playbooksteg.stegtype` er et lukket
--                              sett uten fritekstfølge.
--   `hendelse_uten_score`    — `score` NOT NULL.
--   `score_uten_regel`       — `regel_id` NOT NULL, fremmednøkkel til
--                              tenantens egen, daterte regel.
--
-- Alle fire står i funntypesettet OG er umulige. Et sett som ikke
-- navnga dem ville ikke sagt noe; et sett som navnga dem og kunne
-- fylles ville sagt at vernet er en sveip.
--
-- ---------------------------------------------------------------------
-- INNDATAENE FINNES IKKE, OG DET SKAL STÅ.
--
-- Katalogen lover «SIEM, IdP, EDR, skanner, applikasjonslogger og
-- aktivaregister». Målt mot basen finnes ÉN av seks: applikasjonsloggen.
-- Ingen av de fem andre kan finnes uten en utgående integrasjon huset
-- ikke har.
--
-- Modulen later derfor ikke som. Signaltypene er et LUKKET SETT over
-- det basen faktisk kan se, og et signal huset ikke kan observere har
-- ingen verdi å skrives med.
--
-- ---------------------------------------------------------------------
-- GRENSEN MOT M-12 OG M-42.
--
-- M-12 eier TILGANGEN — hvem som skal ha hva, og revisjonen av det.
-- M-42 eier KONTOVAKTEN — transaksjoner som ser ut som svindel. M-29
-- eier KORRELASJONEN: at fire enkeltsignaler som hver for seg er
-- uskyldige, til sammen er en hendelse. En modul som utvidet M-12s
-- tilgangsfunn til å bære sikkerhetshendelser ville gjort
-- tilgangsrevisjon til hendelseshåndtering i stillhet.
-- =====================================================================

-- MODULROLLEN MÅ KUNNE EIE NOE FØR DEN KAN EIE DØRENE.
GRANT USAGE, CREATE ON SCHEMA public TO disponit_hendelse_eier;
GRANT INSERT ON revisjonslogg TO disponit_hendelse_eier;

-- HUSETS TENANTVAKT (038). Granten gis av EIEREN, og eieren er
-- `disponit_m37_claimer` — ikke migrator. Et GRANT fra en som ikke
-- eier funksjonen er en FEIL, ikke en stille no-op.
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_hendelse_eier;
RESET ROLE;

-- ---------------------------------------------------------------------
-- SIGNALKILDEN: LESERETT PÅ `revisjonslogg`, OG INGENTING MER.
--
-- Tabellen eies av migrator, så granten trenger ingen `SET LOCAL ROLE`.
-- Merk hva som IKKE står her: ingen `api_tokener`, ingen `modultoken`,
-- ingen `brukersesjon`, ingen `tenant_pseudonymnokkel`, ingen
-- `brukeridentitet`.
-- ---------------------------------------------------------------------
GRANT SELECT ON revisjonslogg TO disponit_hendelse_eier;

-- =====================================================================
-- EN RETTELSE KLYNGE 10-FUNDAMENTET FANT I 132.
--
-- `m36_funnregister` merket `retensjonsfunn` som `m29_retensjon`.
-- TABELLEN ER M-4s — bygget i 093 (`m4_retensjonsregister`) — og med
-- denne klyngen blir merkelappen en KOLLISJON: M-29 er sikkerhets- og
-- hendelsesagenten, ikke retensjon.
--
-- Kolonnen har ingen referanseintegritet og har ikke ødelagt noe. Men
-- den er det eneste stedet et funnregister sier hvem det tilhører, og
-- en sveip som grupperte på den ville tilskrevet M-4s funn til M-29.
-- =====================================================================
UPDATE m36_funnregister
   SET modul = 'm4_dataforvalter',
       begrunnelse = 'Lukking kodes med lukket_maaling, ikke med apen.'
                     ' Registeret er M-4s (093); merkelappen sa m29 til'
                     ' 137 rettet den.'
 WHERE relasjon = 'retensjonsfunn'
   AND modul = 'm29_retensjon';

-- ---------------------------------------------------------------------
-- `hendelseskrav` — TENANTENS GRENSER, IKKE VÅRE.
--
-- INGEN TALL ER LÅST I EN DRIFTSFIL. Hvor mange poeng som gjør fire
-- uskyldige signaler til en hendelse, er en vurdering av hva det
-- koster å ta feil begge veier — og en tannlegeklinikk og en bank
-- tåler ikke det samme antallet falske alarmer.
-- ---------------------------------------------------------------------
CREATE TABLE hendelseskrav (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kravversjon INT NOT NULL CHECK (kravversjon >= 1),
    -- Hvor lenge to signaler kan ligge fra hverandre og likevel høre
    -- til samme hendelse. Null er ikke lov: et vindu på null sekunder
    -- korrelerer ingenting, og en modul som ikke korrelerer er ikke
    -- denne modulen.
    korrelasjonsvindu_min INT NOT NULL
        CHECK (korrelasjonsvindu_min BETWEEN 1 AND 10080),
    -- Poengsummen som gjør signalene til en hendelse verdt å se på.
    alvorsterskel INT NOT NULL CHECK (alvorsterskel BETWEEN 1 AND 10000),
    -- Hvor mange døgn en åpen hendelse kan stå før den er et funn.
    apen_hendelse_frist_dogn INT NOT NULL
        CHECK (apen_hendelse_frist_dogn BETWEEN 1 AND 365),
    -- Hvor mange signaler én hendelse kan samle. Taket er ikke pynt:
    -- en hendelse som samler alt blir en hendelse som forklarer
    -- ingenting.
    signaltak INT NOT NULL CHECK (signaltak BETWEEN 2 AND 1000),
    versjon_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    satt_av TEXT NOT NULL CHECK (satt_av ~ '[^[:space:]]'),
    CONSTRAINT hendelseskrav_pk PRIMARY KEY (tenant, kravversjon)
);

-- ---------------------------------------------------------------------
-- `sikkerhetsregel` — DET ENESTE SOM KAN GI POENG.
--
-- «Scorer hendelse med FORKLARBARE REGLER» er vaktsetningens eget ord,
-- og forklarbarheten er ikke en egenskap ved modellen — den er en
-- fremmednøkkel. En score uten regel er en påstand, og
-- `sikkerhetshendelse.regel_id` er NOT NULL nettopp derfor.
--
-- SIGNALTYPENE ER ET LUKKET SETT OVER DET BASEN FAKTISK KAN SE.
-- Katalogen lover SIEM, IdP, EDR og skanner; ingen av dem finnes. Å ta
-- imot en `signaltype` huset ikke kan observere ville vært å love en
-- korrelasjon som aldri kommer.
--
-- Reglene er DATERTE, som prisen i 108 og hjemmelen i 133: en regel
-- som ble endret i går skal ikke forklare en score fra i forfjor.
-- ---------------------------------------------------------------------
CREATE TABLE sikkerhetsregel (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    regel_id UUID NOT NULL,
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    signaltype TEXT NOT NULL
        CONSTRAINT sikkerhetsregel_signaltype_lukket
        CHECK (signaltype IN (
            -- ALLE SEKS LESES AV `revisjonslogg`. Ingen av dem krever
            -- en integrasjon huset ikke har.
            'policy_avslag_gjentatt',
            'unntak_gjentatt',
            'handling_utenfor_tidsvindu',
            'aktor_ukjent_for_tenant',
            'beslutning_uten_policyhash',
            'revisjonshull')),
    -- Poengene regelen gir per treff.
    poeng INT NOT NULL CHECK (poeng BETWEEN 1 AND 1000),
    -- Hvor mange treff som skal til før regelen gir poeng i det hele
    -- tatt. Én feilinnlogging er ikke en hendelse.
    terskel_treff INT NOT NULL CHECK (terskel_treff BETWEEN 1 AND 1000),
    begrunnelse TEXT NOT NULL CHECK (begrunnelse ~ '[^[:space:]]'),
    gyldig_fra DATE NOT NULL,
    gyldig_til DATE,
    CONSTRAINT sikkerhetsregel_datoene_gaar_riktig_vei
        CHECK (gyldig_til IS NULL OR gyldig_til >= gyldig_fra),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT sikkerhetsregel_pk PRIMARY KEY (tenant, regel_id)
);
CREATE INDEX sikkerhetsregel_aktive ON sikkerhetsregel (tenant, signaltype)
    WHERE gyldig_til IS NULL;

-- ---------------------------------------------------------------------
-- `playbook` — DEN FORHÅNDSDEFINERTE RESPONSEN, SOM ALDRI KJØRES I v1.
--
-- Vaktsetningen krever «forhåndsdefinerte playbooks» og «tofaktor for
-- utvidet inngrep». Begge deler REGISTRERES her, og ingen av dem
-- utføres: det finnes ingen dør i denne migrasjonen som gjør et steg.
--
-- HVORFOR BYGGE DEN DA? Fordi `inngrep_uten_playbook` skal være
-- UMULIG og ikke bare uønsket, og en fremmednøkkel må peke på noe.
-- Registeret er også det stedet et menneske skriver ned hva den ville
-- gjort — og et inngrep ingen har skrevet ned på forhånd er nøyaktig
-- det vaktsetningen forbyr.
--
-- `krever_tofaktor` er NOT NULL uten DEFAULT. Et forvalg her ville
-- vært huset som bestemte hvilke inngrep som er «utvidede», og det er
-- tenantens vurdering.
-- ---------------------------------------------------------------------
CREATE TABLE playbook (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    playbook_id UUID NOT NULL,
    navn TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    -- Hva playbooken svarer på. Fri tekst her er trygt: det er en
    -- BESKRIVELSE for et menneske, ikke noe som utføres.
    naar_gjelder_den TEXT NOT NULL
        CHECK (naar_gjelder_den ~ '[^[:space:]]'),
    krever_tofaktor BOOLEAN NOT NULL,
    -- Playbooken er GODKJENT AV ET MENNESKE, og navnet står. En
    -- playbook ingen har satt navnet sitt på er ikke forhåndsdefinert,
    -- den er bare skrevet.
    godkjent_av TEXT NOT NULL CHECK (godkjent_av ~ '[^[:space:]]'),
    godkjent_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    gyldig_fra DATE NOT NULL,
    gyldig_til DATE,
    CONSTRAINT playbook_datoene_gaar_riktig_vei
        CHECK (gyldig_til IS NULL OR gyldig_til >= gyldig_fra),
    CONSTRAINT playbook_pk PRIMARY KEY (tenant, playbook_id)
);

-- ---------------------------------------------------------------------
-- `playbooksteg` — DEN VIKTIGSTE TABELLEN I FILA.
--
-- «INGEN FRI KOMMANDOKJØRING» ER IKKE EN POLICY HER. DET ER EN
-- GRAMMATIKK: et steg er ET NAVN FRA ET LUKKET SETT, og tabellen har
-- ingen kolonne som kan bære en parameter, en streng, en sti eller et
-- skript.
--
-- LEGG MERKE TIL HVA SOM IKKE FINNES: ingen `argumenter`, ingen
-- `kommando`, ingen `payload`, ingen `parametre JSONB`. En slik
-- kolonne ville gjort hele det lukkede settet til pynt — «isoler_konto»
-- pluss en fri parameterstreng ER en fri kommando med et pent navn.
--
-- OG INGEN `annet`-VERDI. Det er 116s `klassifisering_utenfor_lukket_
-- sett` anvendt på seg selv: et lukket sett med en åpen dør er et
-- åpent sett.
--
-- STEGENE ER MED VILJE GROVE. De navngir HVA som skal skje, ikke
-- hvordan. Hvordan er menneskets, og det er hele poenget med at v1
-- ikke utfører dem.
-- ---------------------------------------------------------------------
CREATE TABLE playbooksteg (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    playbook_id UUID NOT NULL,
    stegnr INT NOT NULL CHECK (stegnr BETWEEN 1 AND 100),
    stegtype TEXT NOT NULL
        CONSTRAINT playbooksteg_stegtype_lukket
        CHECK (stegtype IN (
            'varsle_sikkerhetsansvarlig',
            'varsle_daglig_leder',
            'samle_tidslinje',
            'kartlegg_beroerte_data',
            'isoler_konto',
            'isoler_token',
            'roter_hemmelighet',
            'tilbakestill_sesjoner',
            'verifiser_gjenoppretting',
            'skriv_laeringsregel')),
    -- ET LAGER SOM IKKE KAN DATERES KAN IKKE MÅLES. M-4s
    -- `retensjonslager.alderskolonne` er NOT NULL, og den er det av en
    -- grunn: en tabell uten et tidspunkt kan ingen si noe om alderen
    -- på. Kolonnen sto ikke her i første utgave, og basen sa fra.
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT playbooksteg_pk PRIMARY KEY (tenant, playbook_id, stegnr),
    CONSTRAINT playbooksteg_playbook_fk
        FOREIGN KEY (tenant, playbook_id)
        REFERENCES playbook (tenant, playbook_id),
    -- Samme steg to ganger i én playbook er en skrivefeil, ikke en
    -- plan.
    CONSTRAINT playbooksteg_ett_av_hvert UNIQUE (tenant, playbook_id, stegtype)
);

-- ---------------------------------------------------------------------
-- `sikkerhetshendelse` — DET KORRELASJONEN GA.
--
-- TO KOLONNER BÆRER TO AV KLYNGENS FIRE UMULIGE FUNN:
--
--   `score`    NOT NULL  →  `hendelse_uten_score` kan ikke reises.
--   `regel_id` NOT NULL  →  `score_uten_regel` kan ikke reises, og
--                           fremmednøkkelen gjør at regelen finnes.
--
-- `kravversjon` er også NOT NULL og med fremmednøkkel: en hendelse
-- scoret mot en terskel som senere ble endret skal fortsatt kunne
-- forklares med terskelen som GJALDT. Klynge 7s dom, anvendt her.
-- ---------------------------------------------------------------------
CREATE TABLE sikkerhetshendelse (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    hendelse_id UUID NOT NULL,
    regel_id UUID NOT NULL,
    kravversjon INT NOT NULL,
    score INT NOT NULL CHECK (score >= 0),
    -- Alvor er AVLEDET av score mot terskel, men LAGRES: terskelen kan
    -- endres, og da skal gårsdagens hendelse ikke stille skifte alvor.
    alvor TEXT NOT NULL
        CONSTRAINT sikkerhetshendelse_alvor_lukket
        CHECK (alvor IN ('under_terskel', 'over_terskel')),
    forste_signal_ts TIMESTAMPTZ NOT NULL,
    siste_signal_ts TIMESTAMPTZ NOT NULL,
    CONSTRAINT sikkerhetshendelse_signalene_gaar_riktig_vei
        CHECK (siste_signal_ts >= forste_signal_ts),
    oppdaget_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- KORRELASJONEN KOMMER ETTER SIGNALENE. En hendelse oppdaget før
    -- det første signalet den hviler på er en tidsreise, ikke en
    -- deteksjon — samme form som 133s
    -- `moteopptak_varsling_kom_forst` og 135s
    -- `samtale_identifikasjon_kom_forst`.
    CONSTRAINT sikkerhetshendelse_oppdaget_etter_signalet
        CHECK (oppdaget_ts >= forste_signal_ts),
    status TEXT NOT NULL DEFAULT 'apen'
        CONSTRAINT sikkerhetshendelse_status_lukket
        CHECK (status IN ('apen', 'lukket')),
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT,
    lukket_grunn TEXT,
    -- LUKKINGEN ER HEL ELLER IKKE SKJEDD. Tre felt som kan stå hver
    -- for seg gir en hendelse som er «lukket» uten at noen vet av hvem.
    CONSTRAINT sikkerhetshendelse_lukkingen_er_hel CHECK (
        (status = 'lukket')
        = (lukket_ts IS NOT NULL AND lukket_av IS NOT NULL
           AND lukket_grunn IS NOT NULL)),
    CONSTRAINT sikkerhetshendelse_pk PRIMARY KEY (tenant, hendelse_id),
    CONSTRAINT sikkerhetshendelse_regel_fk
        FOREIGN KEY (tenant, regel_id)
        REFERENCES sikkerhetsregel (tenant, regel_id),
    CONSTRAINT sikkerhetshendelse_krav_fk
        FOREIGN KEY (tenant, kravversjon)
        REFERENCES hendelseskrav (tenant, kravversjon)
);
CREATE INDEX sikkerhetshendelse_apne ON sikkerhetshendelse (tenant, oppdaget_ts)
    WHERE status = 'apen';

-- ---------------------------------------------------------------------
-- `hendelsessignal` — DET KORRELASJONEN HVILER PÅ.
--
-- APPEND-ONLY, og det er målt som en RETTIGHET og ikke bare som en
-- trigger: `REVOKE UPDATE` lenger ned. Et signal som kunne endres i
-- ettertid ville gjort tidslinjen til en påstand.
--
-- `kilde_ref` peker på `revisjonslogg.id` og er IKKE en fremmednøkkel.
-- Det er med vilje: revisjonsloggen er husets, den reapes etter sin
-- egen frist, og en fremmednøkkel herfra ville gjort M-29 til en
-- oppbevaringsplikt for M-2s rader.
-- ---------------------------------------------------------------------
CREATE TABLE hendelsessignal (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    signal_id UUID NOT NULL,
    hendelse_id UUID NOT NULL,
    signaltype TEXT NOT NULL
        CONSTRAINT hendelsessignal_signaltype_lukket
        CHECK (signaltype IN (
            'policy_avslag_gjentatt',
            'unntak_gjentatt',
            'handling_utenfor_tidsvindu',
            'aktor_ukjent_for_tenant',
            'beslutning_uten_policyhash',
            'revisjonshull')),
    kilde_ref BIGINT NOT NULL,
    aktor TEXT NOT NULL CHECK (aktor ~ '[^[:space:]]'),
    observert_ts TIMESTAMPTZ NOT NULL,
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT hendelsessignal_pk PRIMARY KEY (tenant, signal_id),
    CONSTRAINT hendelsessignal_hendelse_fk
        FOREIGN KEY (tenant, hendelse_id)
        REFERENCES sikkerhetshendelse (tenant, hendelse_id),
    -- SAMME LOGGRAD TELLER ÉN GANG. Uten denne ville en gjentatt
    -- korrelasjonskjøring blåst opp scoren uten at noe nytt hadde
    -- skjedd — en hendelse som vokser av å bli sett på.
    CONSTRAINT hendelsessignal_kilden_telles_en_gang
        UNIQUE (tenant, hendelse_id, kilde_ref)
);
CREATE INDEX hendelsessignal_pr_hendelse
    ON hendelsessignal (tenant, hendelse_id, observert_ts);

-- ---------------------------------------------------------------------
-- `inngrepsforslag` — DER VEIEN SLUTTER.
--
-- LEGG MERKE TIL HVA SOM IKKE FINNES I DENNE TABELLEN: ingen
-- `utfort_ts`, ingen `resultat`, ingen `kvittering`, ingen `status`
-- som kan bli `utfort`. Forslaget er endestasjonen, og det er ikke en
-- forglemmelse — det er v1-dommen skrevet som kolonner.
--
-- `playbook_id` er NOT NULL med fremmednøkkel. `inngrep_uten_playbook`
-- kan derfor aldri reises: et forslag UTEN playbook lar seg ikke
-- skrive, og et inngrep uten forslag lar seg ikke skrive i det hele
-- tatt.
--
-- AT FUNNET STÅR I SETTET OG ER UMULIG ER BEVISET. Et sett som ikke
-- navnga det ville ikke sagt noe; et sett som navnga det og kunne
-- fylles ville sagt at vernet er en sveip.
-- ---------------------------------------------------------------------
CREATE TABLE inngrepsforslag (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    forslag_id UUID NOT NULL,
    hendelse_id UUID NOT NULL,
    playbook_id UUID NOT NULL,
    begrunnelse TEXT NOT NULL CHECK (begrunnelse ~ '[^[:space:]]'),
    foreslatt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    foreslatt_av TEXT NOT NULL CHECK (foreslatt_av ~ '[^[:space:]]'),
    CONSTRAINT inngrepsforslag_pk PRIMARY KEY (tenant, forslag_id),
    CONSTRAINT inngrepsforslag_hendelse_fk
        FOREIGN KEY (tenant, hendelse_id)
        REFERENCES sikkerhetshendelse (tenant, hendelse_id),
    CONSTRAINT inngrepsforslag_playbook_fk
        FOREIGN KEY (tenant, playbook_id)
        REFERENCES playbook (tenant, playbook_id),
    -- ÉN PLAYBOOK FORESLÅS ÉN GANG PER HENDELSE. To like forslag er
    -- ikke to meninger, det er den samme meningen skrevet to ganger.
    CONSTRAINT inngrepsforslag_en_gang_per_playbook
        UNIQUE (tenant, hendelse_id, playbook_id)
);

-- ---------------------------------------------------------------------
-- `hendelsesfunn` — HUSETS FORM, MED ETT LUKKET SETT.
--
-- ETT ÅPENT FUNN PER (funntype, referanse): funnlisten er ikke en logg
-- som vokser med kadensen. Det er 093s form, arvet gjennom hele huset.
-- ---------------------------------------------------------------------
CREATE TABLE hendelsesfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    funn_id UUID NOT NULL DEFAULT gen_random_uuid(),
    funntype TEXT NOT NULL
        CONSTRAINT hendelsesfunn_funntype_lukket
        CHECK (funntype IN (
            -- DE FIRE SOM ALDRI KAN REISES. At de står her OG er
            -- umulige er hele beviset.
            'inngrep_uten_playbook',
            'fri_kommando_kjort',
            'hendelse_uten_score',
            'score_uten_regel',
            -- DE SOM FAKTISK KAN REISES.
            'apen_hendelse_over_frist',
            'hendelse_uten_forslag',
            'regel_uten_treff',
            'playbook_uten_steg',
            'signaltak_naadd',
            'krav_mangler')),
    referanse TEXT NOT NULL CHECK (referanse ~ '[^[:space:]]'),
    detalj TEXT NOT NULL CHECK (detalj ~ '[^[:space:]]'),
    apen BOOLEAN NOT NULL DEFAULT true,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT,
    lukket_grunn TEXT,
    CONSTRAINT hendelsesfunn_lukkingen_er_hel CHECK (
        apen = (lukket_ts IS NULL AND lukket_av IS NULL
                AND lukket_grunn IS NULL)),
    CONSTRAINT hendelsesfunn_pk PRIMARY KEY (tenant, funn_id)
);
CREATE UNIQUE INDEX hendelsesfunn_ett_apent
    ON hendelsesfunn (tenant, funntype, referanse) WHERE apen;

-- =====================================================================
-- RADVAKT OG RETTIGHETER. FORCE RLS PÅ ALLE SJU.
--
-- `tenantlekkasje_i_hendelsesregister` er en invariant i grensen, og
-- FORCE er forskjellen: uten den ser eieren av tabellen forbi sin egen
-- policy, og en SECURITY DEFINER-dør som eide tabellen ville lest alle
-- tenanter uten å vite det.
-- =====================================================================
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['hendelseskrav', 'sikkerhetsregel',
                             'playbook', 'playbooksteg',
                             'sikkerhetshendelse', 'hendelsessignal',
                             'inngrepsforslag', 'hendelsesfunn']
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format($f$CREATE POLICY tenant_isolasjon ON public.%I
            USING (tenant = current_setting('disponit.tenant', true))
            WITH CHECK (tenant = current_setting('disponit.tenant', true))$f$, t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO'
                       ' disponit_hendelse_eier', t);
    END LOOP;
END $$;

-- APPEND-ONLY MÅLT SOM EN RETTIGHET OG IKKE BARE SOM EN TRIGGER.
--
-- Signalet er observasjonen; en observasjon som kan endres i ettertid
-- er ingen observasjon. Og forslaget er endestasjonen — kunne det
-- oppdateres, ville noen før eller siden lagt en `utfort`-verdi i det.
REVOKE UPDATE ON public.hendelsessignal FROM disponit_hendelse_eier;
REVOKE UPDATE ON public.inngrepsforslag FROM disponit_hendelse_eier;
REVOKE UPDATE ON public.playbooksteg FROM disponit_hendelse_eier;
-- …OG KRAVET. En terskel som kunne endres etter at en hendelse
-- pekte på den, ville gjort «terskelen som gjaldt» til «terskelen
-- som gjelder nå» — og oppslaget ville sett like riktig ut.
REVOKE UPDATE ON public.hendelseskrav FROM disponit_hendelse_eier;

-- SVEIPENS KRYSS-TENANT-POLICY (130s LÆRDOM).
--
-- En sveip uten `disponit.tenant` ville sett NULL RADER under FORCE
-- RLS og rapportert null funn — MED GRØNN EXIT-KODE.
CREATE POLICY m29_sveip_tenantliste ON hendelseskrav
    FOR SELECT
    USING (current_setting('disponit.tenant', true) IS NULL
           OR current_setting('disponit.tenant', true) = '');

-- =====================================================================
-- HERFRA EIES DØRENE AV HENDELSESEIEREN.
--
-- SP-7: kjøretiden får EXECUTE på dørene og INGEN tabellrettigheter.
-- =====================================================================
SET LOCAL ROLE disponit_hendelse_eier;

-- `m29_evidens` — HUSETS SPOR.
--
-- Modulen SKRIVER her, som alle andre. At den også LESER
-- `revisjonslogg` er det som gjør den spesiell, og svaret på det står
-- i `m29_signalkilden` — ikke her.
CREATE FUNCTION m29_evidens(p_tenant TEXT, p_ref UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm29_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm29_hendelse', 'handling', p_handling,
        'ref', p_ref::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm29_hendelse',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:hendelse', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;

-- ---------------------------------------------------------------------
-- `m29_signalkilden` — DEN ENE SPØRRINGEN SOM LESER M-2.
--
-- MODULEN LESER NOE DEN OGSÅ SKRIVER I, OG DEN ER ALENE OM DET I HELE
-- HUSET. Uten filteret ville hver `m29_evidens`-rad blitt et nytt
-- signal, korrelasjonen ville plukket det opp, scoren ville steget, og
-- hendelsen ville vokst AV Å BLI SETT PÅ.
--
-- Filteret står i SPØRRINGEN og ikke i en instruks til den som kaller.
-- Grensen heter `leste_sitt_eget_spor_som_signal`, og porten muterer
-- nettopp denne linjen.
--
-- STABLE, ikke IMMUTABLE (125s lærdom): den leser tabeller.
-- ---------------------------------------------------------------------
CREATE FUNCTION m29_signalkilden(p_tenant TEXT, p_fra TIMESTAMPTZ)
RETURNS TABLE (logg_id BIGINT, aktor TEXT, kilde TEXT,
               beslutning TEXT, policy_content_hash TEXT, ts TIMESTAMPTZ)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT r.id, r.aktor, r.kilde, r.beslutning,
           r.policy_content_hash, r.ts
      FROM public.revisjonslogg r
     WHERE r.tenant = p_tenant
       AND r.ts >= p_fra
       -- MODULENS EGET SPOR ER IKKE ET SIGNAL.
       AND r.kilde <> 'm29_hendelse'
     ORDER BY r.ts
$$;
REVOKE ALL ON FUNCTION m29_signalkilden(TEXT, TIMESTAMPTZ) FROM PUBLIC;

-- `m29_regel_gyldig` — STABLE og ikke IMMUTABLE (125s lærdom).
CREATE FUNCTION m29_regel_gyldig(p_fra DATE, p_til DATE)
RETURNS BOOLEAN LANGUAGE sql STABLE
SET search_path = pg_catalog AS $$
    SELECT p_fra <= current_date
       AND (p_til IS NULL OR p_til >= current_date)
$$;
REVOKE ALL ON FUNCTION m29_regel_gyldig(DATE, DATE) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- `m29_funn_er_sveipens` — HVEM SOM KAN LUKKE HVA.
--
-- De fire umulige funnene kan ingen lukke, fordi ingen kan reise dem.
-- De som sveipen selv setter, lukker sveipen selv når tilstanden er
-- borte; et menneske som kunne lukket dem ville lukket en måling og
-- ikke en sak.
-- ---------------------------------------------------------------------
CREATE FUNCTION m29_funn_er_sveipens(p_funntype TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT p_funntype IN ('apen_hendelse_over_frist',
                          'hendelse_uten_forslag',
                          'regel_uten_treff',
                          'playbook_uten_steg',
                          'signaltak_naadd',
                          'krav_mangler')
$$;
REVOKE ALL ON FUNCTION m29_funn_er_sveipens(TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- `m29_sett_krav` — TENANTENS GRENSER, APPEND-ONLY.
--
-- VERSJONEN TILDELES AV DØRA, IKKE AV KALLEREN, og raden OPPDATERES
-- ALDRI. Begge deler er nødvendige av samme grunn:
--
-- `sikkerhetshendelse.kravversjon` er en fremmednøkkel hit, og hele
-- poenget med den er at «terskelen som gjaldt da hendelsen ble scoret»
-- skal kunne slås opp i ettertid. Kunne raden endres, ville
-- oppslaget gitt DAGENS terskel og sett like riktig ut.
--
-- Første utgave lot kalleren velge versjonen og gjorde
-- `ON CONFLICT DO UPDATE`. To kallere kunne da valgt samme tall, og
-- den siste ville stille overskrevet forklaringen på hver hendelse som
-- alt pekte dit. EN VERSJON KALLEREN VELGER ER INGEN VERSJON.
--
-- Husets form, ordrett fra `m43_sett_krav` (135).
-- ---------------------------------------------------------------------
CREATE FUNCTION m29_sett_krav(p_tenant TEXT,
                              p_korrelasjonsvindu_min INT,
                              p_alvorsterskel INT,
                              p_apen_hendelse_frist_dogn INT,
                              p_signaltak INT, p_av TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_versjon INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm29_sett_krav');
    SELECT coalesce(max(kravversjon), 0) + 1 INTO v_versjon
      FROM public.hendelseskrav WHERE tenant = p_tenant;
    INSERT INTO public.hendelseskrav
        (tenant, kravversjon, korrelasjonsvindu_min, alvorsterskel,
         apen_hendelse_frist_dogn, signaltak, satt_av)
    VALUES (p_tenant, v_versjon, p_korrelasjonsvindu_min,
            p_alvorsterskel, p_apen_hendelse_frist_dogn, p_signaltak,
            p_av);
    PERFORM public.m29_evidens(p_tenant, NULL, 'sett_krav', p_av,
                               jsonb_build_object('kravversjon',
                                                  v_versjon));
    RETURN v_versjon;
END $$;

-- `m29_registrer_regel` — DET ENESTE SOM KAN GI POENG.
CREATE FUNCTION m29_registrer_regel(p_tenant TEXT, p_regel_id UUID,
                                    p_navn TEXT, p_signaltype TEXT,
                                    p_poeng INT, p_terskel_treff INT,
                                    p_begrunnelse TEXT,
                                    p_gyldig_fra DATE, p_gyldig_til DATE,
                                    p_av TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm29_registrer_regel');
    INSERT INTO public.sikkerhetsregel
        (tenant, regel_id, navn, signaltype, poeng, terskel_treff,
         begrunnelse, gyldig_fra, gyldig_til, opprettet_av)
    VALUES (p_tenant, p_regel_id, p_navn, p_signaltype, p_poeng,
            p_terskel_treff, p_begrunnelse, p_gyldig_fra, p_gyldig_til,
            p_av);
    PERFORM public.m29_evidens(p_tenant, p_regel_id, 'registrer_regel',
                               p_av,
                               jsonb_build_object('signaltype',
                                                  p_signaltype));
END $$;

-- `m29_avvikle_regel` — EN REGEL DØR MED EN DATO, IKKE MED EN SLETTING.
--
-- 135s form: en score forklart av en regel som er borte, er en score
-- uten forklaring.
CREATE FUNCTION m29_avvikle_regel(p_tenant TEXT, p_regel_id UUID,
                                  p_gyldig_til DATE, p_av TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm29_avvikle_regel');
    -- `gyldig_til IS NULL` GJØR AVVIKLINGEN ENVEIS I BASEN OG IKKE
    -- BARE I DOCSTRINGEN.
    --
    -- Uten leddet kunne et andre kall med en senere dato gjenopplive
    -- en avviklet regel — og «hvilken regel forklarte denne scoren»
    -- ville hatt to svar. CodeRabbit fant det; prosaen over sa
    -- allerede ENVEIS.
    UPDATE public.sikkerhetsregel r
       SET gyldig_til = p_gyldig_til
     WHERE r.tenant = p_tenant AND r.regel_id = p_regel_id
       AND r.gyldig_til IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm29: ingen gjeldende regel % for % — den'
                        ' finnes ikke, eller den er alt avviklet',
            p_regel_id, p_tenant;
    END IF;
    PERFORM public.m29_evidens(p_tenant, p_regel_id, 'avvikle_regel',
                               p_av,
                               jsonb_build_object('gyldig_til',
                                                  p_gyldig_til));
END $$;

-- ---------------------------------------------------------------------
-- `m29_registrer_playbook` — DEN FORHÅNDSDEFINERTE RESPONSEN.
--
-- STEGENE KOMMER SOM ET TEXT[] AV NAVN, og hvert navn må stå i det
-- lukkede settet. Ingen parameter følger med — det er hele poenget.
-- Signaturen KAN ikke bære en kommando, fordi den ikke har noe sted å
-- legge den.
-- ---------------------------------------------------------------------
CREATE FUNCTION m29_registrer_playbook(p_tenant TEXT, p_playbook_id UUID,
                                       p_navn TEXT,
                                       p_naar_gjelder_den TEXT,
                                       p_krever_tofaktor BOOLEAN,
                                       p_steg TEXT[],
                                       p_gyldig_fra DATE,
                                       p_gyldig_til DATE, p_av TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_nr INT := 0; v_steg TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm29_registrer_playbook');
    -- EN PLAYBOOK UTEN STEG ER IKKE EN PLAYBOOK. Den ville tilfredsstilt
    -- fremmednøkkelen i `inngrepsforslag` og forklart ingenting — altså
    -- nøyaktig den fail-open-formen resten av fila handler om.
    IF p_steg IS NULL OR array_length(p_steg, 1) IS NULL THEN
        RAISE EXCEPTION 'm29: en playbook uten steg forklarer ingenting';
    END IF;
    INSERT INTO public.playbook
        (tenant, playbook_id, navn, naar_gjelder_den, krever_tofaktor,
         godkjent_av, gyldig_fra, gyldig_til)
    VALUES (p_tenant, p_playbook_id, p_navn, p_naar_gjelder_den,
            p_krever_tofaktor, p_av, p_gyldig_fra, p_gyldig_til);
    FOREACH v_steg IN ARRAY p_steg
    LOOP
        v_nr := v_nr + 1;
        -- CHECK-en på tabellen er den EKTE vakten; denne gir bare en
        -- lesbar feil. Faller den ene bort, står den andre.
        INSERT INTO public.playbooksteg
            (tenant, playbook_id, stegnr, stegtype)
        VALUES (p_tenant, p_playbook_id, v_nr, v_steg);
    END LOOP;
    PERFORM public.m29_evidens(p_tenant, p_playbook_id,
                               'registrer_playbook', p_av,
                               jsonb_build_object('steg', p_steg,
                                                  'tofaktor',
                                                  p_krever_tofaktor));
END $$;

-- ---------------------------------------------------------------------
-- `m29_korreler` — MODULENS HOVEDDØR.
--
-- Den tar signalene den har fått oppgitt, finner regelen som gjelder,
-- regner scoren, og skriver hendelsen. INGEN AV STEGENE RØRER NOE
-- UTENFOR MODULENS EGNE TABELLER.
--
-- `p_kilde_refs` er `revisjonslogg.id`-er som kalleren har hentet
-- gjennom `m29_signalkilden` — altså allerede filtrert for modulens
-- eget spor. `hendelsessignal_kilden_telles_en_gang` gjør at en
-- gjentatt kjøring ikke blåser opp scoren.
--
-- SCOREN ER REGELENS, IKKE KALLERENS. Ingen parameter her setter en
-- score; den regnes av `poeng * treff` mot regelens egen terskel.
-- Det er 132s lærdom («treffet regnes av båndet, ikke av kalleren»)
-- anvendt på en sikkerhetsscore.
-- ---------------------------------------------------------------------
CREATE FUNCTION m29_korreler(p_tenant TEXT, p_hendelse_id UUID,
                             p_regel_id UUID, p_kravversjon INT,
                             p_kilde_refs BIGINT[],
                             p_aktorer TEXT[],
                             p_observert TIMESTAMPTZ[], p_av TEXT)
RETURNS TABLE (score INT, alvor TEXT, signaler INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_regel   public.sikkerhetsregel%ROWTYPE;
    v_krav    public.hendelseskrav%ROWTYPE;
    v_treff   INT;
    v_score   INT;
    v_alvor   TEXT;
    v_forste  TIMESTAMPTZ;
    v_siste   TIMESTAMPTZ;
    i         INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm29_korreler');

    IF p_kilde_refs IS NULL OR array_length(p_kilde_refs, 1) IS NULL THEN
        RAISE EXCEPTION 'm29: en korrelasjon uten signaler er ingen'
                        ' korrelasjon';
    END IF;
    -- DE TRE LISTENE ER ÉN TABELL SNUDD PÅ SIDEN. Er de ulike lange,
    -- ville løkka under stilltiende brukt den korteste og tapt
    -- signaler — en hendelse som mangler halvparten av grunnlaget sitt
    -- og ikke sier fra.
    IF array_length(p_aktorer, 1) IS DISTINCT FROM
       array_length(p_kilde_refs, 1)
       OR array_length(p_observert, 1) IS DISTINCT FROM
          array_length(p_kilde_refs, 1) THEN
        RAISE EXCEPTION 'm29: % kilder, % aktoerer, % tidspunkt —'
                        ' listene maa vaere like lange',
            array_length(p_kilde_refs, 1), array_length(p_aktorer, 1),
            array_length(p_observert, 1);
    END IF;

    -- INGEN `FOR UPDATE`, OG DET ER EN KONSEKVENS AV FORRIGE AVSNITT.
    --
    -- 136 trengte låsen fordi perioden kunne lukkes mellom lesing og
    -- skriving. Her KAN ikke kravet endres: raden er append-only, og
    -- `disponit_hendelse_eier` har ingen UPDATE på tabellen. En lås mot
    -- en endring som er umulig er en lås som måler ingenting — og
    -- `FOR UPDATE` ville dessuten KREVD den UPDATE-retten vi nettopp
    -- fjernet, så låsen ville brutt døra i stedet for å verne den.
    SELECT * INTO v_krav FROM public.hendelseskrav k
     WHERE k.tenant = p_tenant AND k.kravversjon = p_kravversjon;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm29: kravversjon % finnes ikke for %',
            p_kravversjon, p_tenant;
    END IF;

    SELECT * INTO v_regel FROM public.sikkerhetsregel r
     WHERE r.tenant = p_tenant AND r.regel_id = p_regel_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm29: regel % finnes ikke for %',
            p_regel_id, p_tenant;
    END IF;
    IF NOT public.m29_regel_gyldig(v_regel.gyldig_fra,
                                   v_regel.gyldig_til) THEN
        RAISE EXCEPTION 'm29: regel % gjelder ikke i dag (% til %)',
            p_regel_id, v_regel.gyldig_fra, v_regel.gyldig_til;
    END IF;

    v_treff := array_length(p_kilde_refs, 1);
    IF v_treff > v_krav.signaltak THEN
        RAISE EXCEPTION 'm29: % signaler overstiger tenantens tak paa %',
            v_treff, v_krav.signaltak;
    END IF;

    -- SCOREN ER REGELENS. Under regelens egen terskel gir den null —
    -- én feilinnlogging er ikke en hendelse.
    v_score := CASE WHEN v_treff >= v_regel.terskel_treff
                    THEN v_regel.poeng * v_treff ELSE 0 END;
    v_alvor := CASE WHEN v_score >= v_krav.alvorsterskel
                    THEN 'over_terskel' ELSE 'under_terskel' END;

    SELECT min(t), max(t) INTO v_forste, v_siste
      FROM unnest(p_observert) AS t;

    INSERT INTO public.sikkerhetshendelse
        (tenant, hendelse_id, regel_id, kravversjon, score, alvor,
         forste_signal_ts, siste_signal_ts)
    VALUES (p_tenant, p_hendelse_id, p_regel_id, p_kravversjon,
            v_score, v_alvor, v_forste, v_siste);

    FOR i IN 1 .. v_treff
    LOOP
        INSERT INTO public.hendelsessignal
            (tenant, signal_id, hendelse_id, signaltype, kilde_ref,
             aktor, observert_ts)
        VALUES (p_tenant, gen_random_uuid(), p_hendelse_id,
                v_regel.signaltype, p_kilde_refs[i], p_aktorer[i],
                p_observert[i])
        ON CONFLICT ON CONSTRAINT hendelsessignal_kilden_telles_en_gang
            DO NOTHING;
    END LOOP;

    PERFORM public.m29_evidens(p_tenant, p_hendelse_id, 'korreler', p_av,
                               jsonb_build_object('score', v_score,
                                                  'alvor', v_alvor,
                                                  'signaler', v_treff));
    RETURN QUERY SELECT v_score, v_alvor, v_treff;
END $$;

-- ---------------------------------------------------------------------
-- `m29_foresla_inngrep` — DER MODULEN STOPPER.
--
-- Den skriver et FORSLAG som peker på en playbook. Den utfører
-- ingenting, og det finnes ingen dør i denne migrasjonen som gjør det.
-- ---------------------------------------------------------------------
CREATE FUNCTION m29_foresla_inngrep(p_tenant TEXT, p_forslag_id UUID,
                                    p_hendelse_id UUID,
                                    p_playbook_id UUID,
                                    p_begrunnelse TEXT, p_av TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_pb public.playbook%ROWTYPE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm29_foresla_inngrep');
    SELECT * INTO v_pb FROM public.playbook p
     WHERE p.tenant = p_tenant AND p.playbook_id = p_playbook_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm29: playbook % finnes ikke for %',
            p_playbook_id, p_tenant;
    END IF;
    IF NOT public.m29_regel_gyldig(v_pb.gyldig_fra, v_pb.gyldig_til) THEN
        RAISE EXCEPTION 'm29: playbook % gjelder ikke i dag (% til %)',
            p_playbook_id, v_pb.gyldig_fra, v_pb.gyldig_til;
    END IF;
    INSERT INTO public.inngrepsforslag
        (tenant, forslag_id, hendelse_id, playbook_id, begrunnelse,
         foreslatt_av)
    VALUES (p_tenant, p_forslag_id, p_hendelse_id, p_playbook_id,
            p_begrunnelse, p_av);
    PERFORM public.m29_evidens(p_tenant, p_forslag_id,
                               'foresla_inngrep', p_av,
                               jsonb_build_object(
                                   'playbook', p_playbook_id,
                                   'krever_tofaktor',
                                   v_pb.krever_tofaktor));
END $$;

-- `m29_lukk_hendelse` — ET MENNESKE SIER AT SAKEN ER OVER.
CREATE FUNCTION m29_lukk_hendelse(p_tenant TEXT, p_hendelse_id UUID,
                                  p_grunn TEXT, p_av TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm29_lukk_hendelse');
    UPDATE public.sikkerhetshendelse h
       SET status = 'lukket', lukket_ts = now(), lukket_av = p_av,
           lukket_grunn = p_grunn
     WHERE h.tenant = p_tenant AND h.hendelse_id = p_hendelse_id
       AND h.status = 'apen';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm29: ingen aapen hendelse % for %',
            p_hendelse_id, p_tenant;
    END IF;
    PERFORM public.m29_evidens(p_tenant, p_hendelse_id, 'lukk_hendelse',
                               p_av,
                               jsonb_build_object('grunn', p_grunn));
END $$;

-- ---------------------------------------------------------------------
-- `m29_lukk_funn` — OG DE FIRE UMULIGE KAN INGEN LUKKE.
--
-- 132s form: sveipens egne funn lukkes av sveipen når tilstanden er
-- borte. De fire umulige kan ikke lukkes fordi de ikke kan reises — og
-- en dør som lot et menneske lukke dem ville sagt at de KAN oppstå.
-- ---------------------------------------------------------------------
CREATE FUNCTION m29_lukk_funn(p_tenant TEXT, p_funn_id UUID,
                              p_grunn TEXT, p_av TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_type TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm29_lukk_funn');
    SELECT f.funntype INTO v_type FROM public.hendelsesfunn f
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id AND f.apen;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm29: ingen aapent funn % for %',
            p_funn_id, p_tenant;
    END IF;
    IF public.m29_funn_er_sveipens(v_type) THEN
        RAISE EXCEPTION 'm29: % lukkes av sveipen naar tilstanden er'
                        ' borte, ikke av et menneske', v_type;
    END IF;
    UPDATE public.hendelsesfunn f
       SET apen = false, lukket_ts = now(), lukket_av = p_av,
           lukket_grunn = p_grunn
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;
    PERFORM public.m29_evidens(p_tenant, p_funn_id, 'lukk_funn', p_av,
                               jsonb_build_object('funntype', v_type));
END $$;

-- =====================================================================
-- LESEDØRENE.
-- =====================================================================

CREATE FUNCTION m29_hendelsene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (hendelse_id UUID, regel TEXT, signaltype TEXT,
               score INT, alvor TEXT, signaler BIGINT,
               forslag BIGINT, status TEXT, oppdaget_ts TIMESTAMPTZ)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT h.hendelse_id, r.navn, r.signaltype, h.score, h.alvor,
           (SELECT count(*) FROM public.hendelsessignal s
             WHERE s.tenant = h.tenant AND s.hendelse_id = h.hendelse_id),
           (SELECT count(*) FROM public.inngrepsforslag f
             WHERE f.tenant = h.tenant AND f.hendelse_id = h.hendelse_id),
           h.status, h.oppdaget_ts
      FROM public.sikkerhetshendelse h
      JOIN public.sikkerhetsregel r
        ON r.tenant = h.tenant AND r.regel_id = h.regel_id
     WHERE h.tenant = p_tenant
     ORDER BY h.oppdaget_ts DESC
     LIMIT greatest(1, least(coalesce(p_maks, 100), 500))
$$;
REVOKE ALL ON FUNCTION m29_hendelsene(TEXT, INT) FROM PUBLIC;

-- `m29_tidslinjen` — SIGNALENE EN HENDELSE HVILER PÅ.
CREATE FUNCTION m29_tidslinjen(p_tenant TEXT, p_hendelse_id UUID)
RETURNS TABLE (signal_id UUID, signaltype TEXT, aktor TEXT,
               kilde_ref BIGINT, observert_ts TIMESTAMPTZ)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT s.signal_id, s.signaltype, s.aktor, s.kilde_ref,
           s.observert_ts
      FROM public.hendelsessignal s
     WHERE s.tenant = p_tenant AND s.hendelse_id = p_hendelse_id
     ORDER BY s.observert_ts
$$;
REVOKE ALL ON FUNCTION m29_tidslinjen(TEXT, UUID) FROM PUBLIC;

CREATE FUNCTION m29_reglene(p_tenant TEXT)
RETURNS TABLE (regel_id UUID, navn TEXT, signaltype TEXT, poeng INT,
               terskel_treff INT, gyldig_fra DATE, gyldig_til DATE,
               gjelder_i_dag BOOLEAN, brukt BIGINT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT r.regel_id, r.navn, r.signaltype, r.poeng, r.terskel_treff,
           r.gyldig_fra, r.gyldig_til,
           public.m29_regel_gyldig(r.gyldig_fra, r.gyldig_til),
           (SELECT count(*) FROM public.sikkerhetshendelse h
             WHERE h.tenant = r.tenant AND h.regel_id = r.regel_id)
      FROM public.sikkerhetsregel r
     WHERE r.tenant = p_tenant
     ORDER BY r.navn
$$;
REVOKE ALL ON FUNCTION m29_reglene(TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- `m29_playbookene` — MED STEGENE, SLIK ET MENNESKE KAN LESE DEM.
--
-- Stegene kommer ut som et TEXT[] i rekkefølge. Det er ikke en
-- utførelsesplan; det er en liste noen har skrevet ned på forhånd.
-- ---------------------------------------------------------------------
CREATE FUNCTION m29_playbookene(p_tenant TEXT)
RETURNS TABLE (playbook_id UUID, navn TEXT, naar_gjelder_den TEXT,
               krever_tofaktor BOOLEAN, steg TEXT[],
               gjelder_i_dag BOOLEAN, godkjent_av TEXT,
               foreslatt_ganger BIGINT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT p.playbook_id, p.navn, p.naar_gjelder_den, p.krever_tofaktor,
           (SELECT array_agg(s.stegtype ORDER BY s.stegnr)
              FROM public.playbooksteg s
             WHERE s.tenant = p.tenant AND s.playbook_id = p.playbook_id),
           public.m29_regel_gyldig(p.gyldig_fra, p.gyldig_til),
           p.godkjent_av,
           (SELECT count(*) FROM public.inngrepsforslag f
             WHERE f.tenant = p.tenant AND f.playbook_id = p.playbook_id)
      FROM public.playbook p
     WHERE p.tenant = p_tenant
     ORDER BY p.navn
$$;
REVOKE ALL ON FUNCTION m29_playbookene(TEXT) FROM PUBLIC;

CREATE FUNCTION m29_hendelsesfunn(p_tenant TEXT, p_maks INT)
RETURNS TABLE (funn_id UUID, funntype TEXT, referanse TEXT,
               detalj TEXT, sveipens BOOLEAN, forst_sett TIMESTAMPTZ)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT f.funn_id, f.funntype, f.referanse, f.detalj,
           public.m29_funn_er_sveipens(f.funntype), f.forst_sett
      FROM public.hendelsesfunn f
     WHERE f.tenant = p_tenant AND f.apen
     ORDER BY f.forst_sett DESC
     LIMIT greatest(1, least(coalesce(p_maks, 100), 500))
$$;
REVOKE ALL ON FUNCTION m29_hendelsesfunn(TEXT, INT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- `m29_bildet` — MODULENS EGEN TILSTAND, PÅ ETT BLIKK.
--
-- `inngrep_utfort` er ALLTID 0, og den står her med vilje: tallet er
-- ikke en telling av en kolonne — det er en påstand om at kolonnen
-- ikke finnes. Blir den noen gang noe annet enn 0, er v1-dommen brutt
-- av noen som la til en tabell.
-- ---------------------------------------------------------------------
CREATE FUNCTION m29_bildet(p_tenant TEXT)
RETURNS TABLE (apne_hendelser BIGINT, over_terskel BIGINT,
               regler BIGINT, playbooker BIGINT, forslag BIGINT,
               inngrep_utfort BIGINT, apne_funn BIGINT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT (SELECT count(*) FROM public.sikkerhetshendelse h
             WHERE h.tenant = p_tenant AND h.status = 'apen'),
           (SELECT count(*) FROM public.sikkerhetshendelse h
             WHERE h.tenant = p_tenant AND h.alvor = 'over_terskel'),
           (SELECT count(*) FROM public.sikkerhetsregel r
             WHERE r.tenant = p_tenant),
           (SELECT count(*) FROM public.playbook p
             WHERE p.tenant = p_tenant),
           (SELECT count(*) FROM public.inngrepsforslag f
             WHERE f.tenant = p_tenant),
           0::BIGINT,
           (SELECT count(*) FROM public.hendelsesfunn f
             WHERE f.tenant = p_tenant AND f.apen)
$$;
REVOKE ALL ON FUNCTION m29_bildet(TEXT) FROM PUBLIC;

-- =====================================================================
-- `m29_sveip_hendelse` — KRYSS-TENANT, ÉN TENANT OM GANGEN.
--
-- 130s LÆRDOM, OG DEN ER HELE GRUNNEN TIL LØKKA: under FORCE RLS ser
-- en spørring UTEN `disponit.tenant` NULL RADER. En sveip som spurte
-- på tvers ville rapportert null funn MED GRØNN EXIT-KODE — en måling
-- som ikke kjørte, lest som en måling som var ren.
--
-- Sveipen setter derfor konteksten per tenant, og `m29_sveip_tenantliste`
-- er den ene policyen som lar den lese tenantlista i det hele tatt.
--
-- OG DEN GJØR INGEN INNGREP. Den skriver funn i sin egen tabell og
-- lukker sine egne når tilstanden er borte. Ingenting annet.
-- =====================================================================
CREATE FUNCTION m29_sveip_hendelse(p_maks_tenanter INT DEFAULT 1000)
RETURNS TABLE (tenanter INT, nye INT, oppdaterte INT, lukket INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_t TEXT;
    v_antall INT := 0;
    v_nye INT := 0;
    v_oppdaterte INT := 0;
    v_lukket INT := 0;
    v_n INT; v_n2 INT; v_n3 INT;
BEGIN
    PERFORM set_config('disponit.tenant', '', true);
    FOR v_t IN
        SELECT DISTINCT k.tenant FROM public.hendelseskrav k
         ORDER BY 1 LIMIT greatest(1, coalesce(p_maks_tenanter, 1000))
    LOOP
        v_antall := v_antall + 1;
        PERFORM set_config('disponit.tenant', v_t, true);

        -- 1. ÅPEN HENDELSE OVER FRISTEN.
        -- DEN GJELDENDE FRISTEN, IKKE DEN LENGSTE SOM NOEN GANG STO.
        --
        -- Her sto `max(apen_hendelse_frist_dogn)` til CodeRabbit fant
        -- den. Det var riktig så lenge hver tenant hadde ÉN kravrad —
        -- og det sluttet å være riktig i samme runde, da kravet ble
        -- APPEND-ONLY med en ny versjon per endring.
        --
        -- En tenant som strammet fristen fra 30 til 7 døgn ville
        -- fortsatt blitt målt mot 30, og funnet ville uteblitt i tre
        -- uker. Rettelsen én dør lenger inne skapte feilen her.
        WITH krav AS (
            SELECT k.apen_hendelse_frist_dogn AS frist
              FROM public.hendelseskrav k WHERE k.tenant = v_t
             ORDER BY k.kravversjon DESC LIMIT 1),
        treff AS (
            SELECT h.hendelse_id, h.oppdaget_ts, h.score
              FROM public.sikkerhetshendelse h, krav
             WHERE h.tenant = v_t AND h.status = 'apen'
               AND h.oppdaget_ts
                   < now() - make_interval(days => krav.frist)),
        satt AS (
            INSERT INTO public.hendelsesfunn
                (tenant, funntype, referanse, detalj)
            SELECT v_t, 'apen_hendelse_over_frist',
                   t.hendelse_id::text,
                   'aapen siden ' || t.oppdaget_ts::date
                   || ', score ' || t.score
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
                DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.hendelsesfunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm29_sveip',
                   lukket_grunn = 'hendelsen er lukket'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'apen_hendelse_over_frist'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.hendelse_id::text = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 2. HENDELSE OVER TERSKEL UTEN ET ENESTE FORSLAG.
        --
        -- Dette er sveipens viktigste funn, og det er nettopp der v1
        -- er ærlig: modulen kan IKKE gjøre noe med hendelsen, og da
        -- er det å stå uten et forslag den eneste feilen den kan
        -- oppdage i seg selv.
        WITH treff AS (
            SELECT h.hendelse_id, h.score
              FROM public.sikkerhetshendelse h
             WHERE h.tenant = v_t AND h.status = 'apen'
               AND h.alvor = 'over_terskel'
               AND NOT EXISTS (SELECT 1 FROM public.inngrepsforslag f
                                WHERE f.tenant = v_t
                                  AND f.hendelse_id = h.hendelse_id)),
        satt AS (
            INSERT INTO public.hendelsesfunn
                (tenant, funntype, referanse, detalj)
            SELECT v_t, 'hendelse_uten_forslag', t.hendelse_id::text,
                   'over terskel med score ' || t.score
                   || ', ingen playbook foreslaatt'
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
                DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.hendelsesfunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm29_sveip',
                   lukket_grunn = 'forslag er skrevet eller hendelsen lukket'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'hendelse_uten_forslag'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.hendelse_id::text = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 3. PLAYBOOK UTEN STEG.
        --
        -- Døra nekter det, men en playbook kan ha mistet stegene sine
        -- på en annen vei — og en playbook uten steg tilfredsstiller
        -- fremmednøkkelen i `inngrepsforslag` og forklarer ingenting.
        -- Det er nøyaktig den fail-open-formen resten av fila handler
        -- om, og derfor måles den her og ikke bare i døra.
        WITH treff AS (
            SELECT p.playbook_id, p.navn
              FROM public.playbook p
             WHERE p.tenant = v_t
               AND NOT EXISTS (SELECT 1 FROM public.playbooksteg s
                                WHERE s.tenant = v_t
                                  AND s.playbook_id = p.playbook_id)),
        satt AS (
            INSERT INTO public.hendelsesfunn
                (tenant, funntype, referanse, detalj)
            SELECT v_t, 'playbook_uten_steg', t.playbook_id::text,
                   'playbooken «' || t.navn || '» har ingen steg'
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
                DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.hendelsesfunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm29_sveip',
                   lukket_grunn = 'stegene er skrevet'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'playbook_uten_steg'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.playbook_id::text = f.referanse)
            RETURNING 1)
        SELECT count(*) FILTER (WHERE ny), count(*) FILTER (WHERE NOT ny),
               (SELECT count(*) FROM lukket)
          FROM satt INTO v_n, v_n2, v_n3;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
        v_lukket := v_lukket + coalesce(v_n3, 0);

        -- 4. REGEL SOM ALDRI HAR GITT ET TREFF.
        --
        -- En regel uten treff er ikke nødvendigvis feil — men en
        -- regelsamling der ingen regel noen gang traff, er et
        -- deteksjonsapparat som ikke detekterer.
        WITH treff AS (
            SELECT r.regel_id, r.navn
              FROM public.sikkerhetsregel r
             WHERE r.tenant = v_t
               AND public.m29_regel_gyldig(r.gyldig_fra, r.gyldig_til)
               AND r.opprettet < now() - INTERVAL '90 days'
               AND NOT EXISTS (SELECT 1 FROM public.sikkerhetshendelse h
                                WHERE h.tenant = v_t
                                  AND h.regel_id = r.regel_id)),
        satt AS (
            INSERT INTO public.hendelsesfunn
                (tenant, funntype, referanse, detalj)
            SELECT v_t, 'regel_uten_treff', t.regel_id::text,
                   'regelen «' || t.navn || '» har staatt i over 90'
                   || ' doegn uten et eneste treff'
              FROM treff t
            ON CONFLICT (tenant, funntype, referanse) WHERE apen
                DO UPDATE SET sist_sett = now()
            RETURNING (xmax = 0) AS ny),
        lukket AS (
            UPDATE public.hendelsesfunn f
               SET apen = false, lukket_ts = now(), lukket_av = 'm29_sveip',
                   lukket_grunn = 'regelen traff, eller ble avviklet'
             WHERE f.tenant = v_t AND f.apen
               AND f.funntype = 'regel_uten_treff'
               AND NOT EXISTS (SELECT 1 FROM treff t
                                WHERE t.regel_id::text = f.referanse)
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
-- ikke eier funksjonen AVBRYTER migrasjonen — migrators egne vakter er
-- revokert der de lages.
-- =====================================================================
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT p.oid::regprocedure AS sig
               FROM pg_proc p
              WHERE p.pronamespace = 'public'::regnamespace
                AND p.proname LIKE 'm29\_%'
                AND pg_get_userbyid(p.proowner) = current_user
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', r.sig);
    END LOOP;
END $$;

GRANT EXECUTE ON FUNCTION m29_sett_krav(TEXT, INT, INT, INT, INT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m29_registrer_regel(TEXT, UUID, TEXT, TEXT,
    INT, INT, TEXT, DATE, DATE, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m29_avvikle_regel(TEXT, UUID, DATE, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m29_registrer_playbook(TEXT, UUID, TEXT, TEXT,
    BOOLEAN, TEXT[], DATE, DATE, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m29_korreler(TEXT, UUID, UUID, INT, BIGINT[],
    TEXT[], TIMESTAMPTZ[], TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m29_foresla_inngrep(TEXT, UUID, UUID, UUID,
    TEXT, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m29_lukk_hendelse(TEXT, UUID, TEXT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m29_lukk_funn(TEXT, UUID, TEXT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION m29_signalkilden(TEXT, TIMESTAMPTZ) TO disponit;
GRANT EXECUTE ON FUNCTION m29_hendelsene(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m29_tidslinjen(TEXT, UUID) TO disponit;
GRANT EXECUTE ON FUNCTION m29_reglene(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m29_playbookene(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION m29_hendelsesfunn(TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION m29_bildet(TEXT) TO disponit;

-- SVEIPEROLLEN NÅR ÉN FUNKSJON, OG BARE DEN (111s form).
GRANT EXECUTE ON FUNCTION m29_sveip_hendelse(INT) TO disponit_hendelsessveip;

RESET ROLE;

-- =====================================================================
-- M-36s FUNNKATALOG (132). Raden alene er bare en lovnad — lesretten
-- innfrir den.
-- =====================================================================
INSERT INTO m36_funnregister
    (relasjon, modul, typekolonne, apenform, begrunnelse)
VALUES
    ('hendelsesfunn', 'm29_hendelse', 'funntype', 'apen_kolonne',
     'husets form')
ON CONFLICT (relasjon) DO NOTHING;
GRANT SELECT ON hendelsesfunn TO disponit_optimalisator_eier;

-- =====================================================================
-- M-4s RETENSJONSREGISTER (093) — OG BASEN RETTET MEG.
--
-- 093 sier at et lager i katalogen UTEN EN SKREVET DOM er et funn
-- (`uregistrert`), og at det ikke er en feil i modulen — det er
-- modulens hele poeng. Åtte nye tabeller ville reist åtte slike.
--
-- FØRSTE UTGAVE REGISTRERTE DEM SOM `under_frist` MED reaper NULL.
-- Den ville aldri kjørt: `retensjonslager_dom_vakt` sier at
-- `under_frist` KREVER `reaper`, `fristkilde` og `reapetkolonne`, og
-- CHECKen gjør «skrevet frist uten noen som håndhever den»
-- UREPRESENTERBART.
--
-- DET ER RIKTIG, OG DET ER VERDT Å SKRIVE NED: en dom som lover en
-- frist ingen reaper håndhever er verre enn ingen dom. `uregistrert`
-- sier «ingen har bestemt seg». En tom `under_frist` ville sagt «noen
-- har bestemt seg, og det skjer» — om noe som aldri skjer.
--
-- DOMMEN ER DERFOR `uten_frist_apen`, og den er nøyaktig sann: lagrene
-- er KJENT, og ingen har ennå skrevet hva som gjelder. 093 gjør den om
-- til et `uten_dom`-funn, og det er meningen — det er den åpne saken
-- gjort synlig framfor å ligge i en absens.
--
-- Å velge `uten_frist_akseptert` ville vært en løgn: det leses som
-- «ingen frist, og det er greit», og en sikkerhetshendelse skal ikke
-- ligge for alltid.
--
-- REAPEREN KOMMER NÅR NOEN HAR BESTEMT HVOR LENGE. Det er ikke v1s
-- arbeid — v1 holder tilbake handlinger, og en reaper er en handling.
--
-- Granten er `disponit_lager_eier`s: tabellen er M-4s, og et INSERT
-- fra en som ikke eier den er en FEIL, ikke en stille no-op.
-- =====================================================================
SET LOCAL ROLE disponit_lager_eier;
INSERT INTO retensjonslager
    (lager_id, relasjon, klasse, tenantkolonne, alderskolonne,
     reapetkolonne, fristkilde, frist_dogn, reaper, dom,
     dom_begrunnelse, dom_migrasjon)
VALUES
    ('m29_hendelsessignal', 'hendelsessignal', 'driftsspor', 'tenant',
     'registrert', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'Signalene er beviset paa at hendelsen skjedde. Hvor lenge de skal'
     ' staa er ikke bestemt, og v1 utfoerer ingen sletting.', '137'),
    ('m29_sikkerhetshendelse', 'sikkerhetshendelse', 'driftsspor',
     'tenant', 'oppdaget_ts', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'En sikkerhetshendelse er dokumentasjon overfor tilsyn og'
     ' forsikring. Fristen maa settes av noen som vet hvilket tilsyn.',
     '137'),
    ('m29_inngrepsforslag', 'inngrepsforslag', 'driftsspor', 'tenant',
     'foreslatt_ts', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'Forslaget er det modulen faktisk mente burde skje. Fristen foelger'
     ' hendelsens, og den er ikke satt.', '137'),
    ('m29_hendelsesfunn', 'hendelsesfunn', 'driftsspor', 'tenant',
     'forst_sett', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'Funnene er modulens egen maaling av seg selv. Lukkede funn kunne'
     ' reapes, men reaperen finnes ikke i v1.', '137'),
    ('m29_hendelseskrav', 'hendelseskrav', 'konfigurasjon', 'tenant',
     'versjon_ts', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'Kravversjonene er referert av hendelser og kan ikke slettes'
     ' uavhengig av dem.', '137'),
    ('m29_sikkerhetsregel', 'sikkerhetsregel', 'konfigurasjon', 'tenant',
     'opprettet', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'En regel som forklarer en score kan ikke forsvinne foer scoren'
     ' gjoer det — en score uten regel er en paastand.', '137'),
    ('m29_playbook', 'playbook', 'konfigurasjon', 'tenant',
     'godkjent_ts', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'Playbooken er referert av forslag og kan ikke slettes uavhengig'
     ' av dem.', '137'),
    ('m29_playbooksteg', 'playbooksteg', 'konfigurasjon', 'tenant',
     'opprettet', NULL, NULL, NULL, NULL, 'uten_frist_apen',
     'Stegene er playbookens innhold og foelger den.', '137')
ON CONFLICT (lager_id) DO NOTHING;
RESET ROLE;
