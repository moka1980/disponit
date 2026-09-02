-- 102: M-17 kundeserviceagent v1 (PR-A) — HENVENDELSESREGISTERET.
-- Fire tenant-skopede tabeller, åtte dører og ÉN sveip med sin egen
-- LOGIN-rolle og sin egen daglige timer.
--
-- V1-AVGRENSNINGEN, ORDRETT FRA MANIFESTET: katalogteksten lover at
-- repeterende henvendelser løses AUTOMATISK. Den veien finnes ikke her,
-- og fraværet er dommen: et automatisk svar til en kunde er en uttalelse
-- på firmaets vegne. M-57 har alt formen — utkastet lagres, et menneske
-- sender — og den formen gjelder her av samme grunn. Det finnes ingen
-- SMTP-vei i denne migrasjonen, ingen utgående kø, ingen mottakeradresse
-- og ingen kolonne som later som om noe ble sendt.
--
-- PR-A ER REGISTERET. Klassifiseringen SKRIVES NED — av et menneske i
-- denne PR-en, av modellen i PR-B — og registeret er formen begge fyller.
-- Det er samme rekkefølge som M-6 tok (PR-A datamodell, PR-B kilde), og
-- av samme grunn: en modell som klassifiserer inn i et skjema ingen har
-- avgrenset, produserer kategorier ingen kan sortere.
--
-- ÆRLIG OM GRENSEN: `m17-v1` bærer invarianten `modellinput_umaskert_felt`.
-- I PR-A finnes ingen modellinput, så invarianten har NULL FORSØK — og
-- null brudd uten forsøk er RØDT i parformen, ikke grønt. Den måles først
-- i PR-B. Dette står her fordi en grense som ser oppfylt ut uten å være
-- målt er verre enn en som er tom.
--
-- DOMMENE v1 HVILER PÅ, håndhevet i DATAMODELLEN og ikke i et API-lag
-- som kunne omgås:
--
--   1. EN HENVENDELSE FORSVINNER ALDRI. Det er den skarpeste, og den er
--      strukturell: `henvendelse` er append-only mot både UPDATE og
--      DELETE på innholdet, og veien til M-37s unntakskø er en
--      REFERANSE (`payload_type='referanse'`) — køen peker på raden, den
--      kopierer den ikke. En tapt henvendelse er verre enn en
--      uklassifisert, og den er usynlig.
--
--   2. KLASSIFISERINGEN ER ET LUKKET SETT, i tre akser: prioritet, tema
--      og handlingstype. En modell som får finne på egne kategorier gir
--      en kø ingen kan sortere — og settet må derfor være lukket FØR
--      modellen kommer, ikke etterpå.
--
--   3. UTKASTET ER APPEND-ONLY. Regenerering lager en NY rad. Et utkast
--      som endres under føttene på den som leser det, er et utkast ingen
--      kan stå for å ha sendt.
--
--   4. INGEN ANDRE KØ. Det uavklarte går til M-37s `unntak`, ikke til en
--      tabell som heter noe annet. En andre kø ved siden av den er
--      nøyaktig det M-37 ble bygget for å hindre.
--
-- PERSONDATA KRYPTERES MED TENANT-DEK (058/088-formen): emne, kropp og
-- utkastets tekst er `<felt>_kryptert BYTEA + nonce + key_id` med FK mot
-- `tenant_nokler`. Avsenderen lagres som HASH — registeret trenger å
-- kunne kjenne igjen den samme avsenderen på tvers av henvendelser, ikke
-- å kunne lese adressen.
--
-- GRENSENE MOT SØSKNENE, sagt eksplisitt: M-9 (095) eier BEGREPENE — det
-- vi vet. M-17 eier HENVENDELSENE — det noen spurte om. Et svarutkast
-- SITERER M-9 gjennom `kunnskapsref` og eier ikke innholdet; når
-- begrepet endres, er utkastet foreldet, og det er en egenskap ved
-- utkastet og ikke ved begrepet.
--
-- SVEIPEN HAR SIN EGEN ROLLE OG SIN EGEN TIMER (095/100/101-formen):
-- `disponit_henvendelsessveip` har nøyaktig ÉN rettighet i basen —
-- EXECUTE på `m17_sveip_henvendelser` — og INGEN tabellrettigheter.
-- Sveipen SVARER INGEN og KØER INGEN VARSEL; den skriver FUNN.
--
-- FORMENE ER HUSETS: tabellene eies av migrator, dørene av NOLOGIN-rollen
-- `disponit_kundeservice_eier`, tenant TEXT + RLS ENABLE+FORCE +
-- `tenant_isolasjon` på hver tabell, SP-1 (`krev_tenantkontekst` FØRST) i
-- hver tenantbundet definer, og INGEN BYPASSRLS.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_kundeservice_eier') THEN
        RAISE EXCEPTION 'rollen disponit_kundeservice_eier mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_kundeservice_eier;


-- ------------------------------------------------------------
-- 0. `unntak.sakskilde` utvides med `henvendelse`.
--
--    OPPSLAGET ER DETERMINISTISK: på NAVN og `contype='c'`, aldri på et
--    `LIKE`-søk over definisjonene. Det er 044-lærdommen, dyrt kjøpt:
--    044 fant sin CHECK med `LIKE '%ressurs_type%'` uten ORDER BY og
--    uten typefilter, og på PG18 har NOT NULL-constrainten SIN EGEN rad
--    i `pg_constraint` med definisjonen `NOT NULL <kolonne>` — som
--    matcher like godt. Hvilken rad som kom tilbake avhang av
--    spørreplanen, spleisen traff feil rad, og resultatet var to måneder
--    med varsler som ble avvist i stillhet.
--
--    Idempotent: står verdien der alt, gjøres ingenting.
-- ------------------------------------------------------------
DO $$
DECLARE v_def TEXT;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO v_def FROM pg_constraint
     WHERE conrelid = 'public.unntak'::regclass
       AND conname = 'unntak_sakskilde_verdi' AND contype = 'c';
    IF v_def IS NULL THEN
        RAISE EXCEPTION '102: fant ikke unntak_sakskilde_verdi';
    END IF;
    IF v_def LIKE '%''henvendelse''%' THEN
        RETURN;
    END IF;
    IF v_def NOT LIKE '%''domeneovertakelse''%' THEN
        RAISE EXCEPTION '102: uventet definisjonsform på'
            ' unntak_sakskilde_verdi: %', v_def;
    END IF;
    v_def := replace(v_def, '''domeneovertakelse''::text',
                     '''domeneovertakelse''::text, ''henvendelse''::text');
    ALTER TABLE public.unntak DROP CONSTRAINT unntak_sakskilde_verdi;
    EXECUTE format('ALTER TABLE public.unntak ADD CONSTRAINT'
                   ' unntak_sakskilde_verdi %s', v_def);
END $$;


-- ...og `unntak_snapshot_komplett` må si det samme om `henvendelse` som
-- den sier om `domeneovertakelse`: snapshot-trioen står NULL.
--
-- DOMMEN (041s egen, ordrett anvendt): trioen snapshotter en
-- POLICYBESLUTNING med et forsøksbudsjett. En henvendelse som ikke lot
-- seg klassifisere er ingen slik beslutning — det er en oppgave for et
-- menneske, uten automatiske forsøk å telle. `maks_auto_forsok_snapshot
-- = 0` og `policy_versjon = 'ukjent'` ville PÅSTÅTT at det fantes en
-- policybeslutning bak, og det er nettopp den løgnen 041 nektet å
-- skrive for domeneovertakelsene.
--
-- Samme deterministiske oppslag som over: navn OG contype.
DO $$
DECLARE v_def TEXT;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO v_def FROM pg_constraint
     WHERE conrelid = 'public.unntak'::regclass
       AND conname = 'unntak_snapshot_komplett' AND contype = 'c';
    IF v_def IS NULL THEN
        RAISE EXCEPTION '102: fant ikke unntak_snapshot_komplett';
    END IF;
    IF v_def LIKE '%henvendelse%' THEN
        RETURN;
    END IF;
    ALTER TABLE public.unntak DROP CONSTRAINT unntak_snapshot_komplett;
    ALTER TABLE public.unntak ADD CONSTRAINT unntak_snapshot_komplett CHECK (
        (sakskilde IN ('domeneovertakelse', 'henvendelse')
           AND maks_auto_forsok_snapshot IS NULL
           AND policy_versjon IS NULL AND policy_content_hash IS NULL)
        OR (sakskilde NOT IN ('domeneovertakelse', 'henvendelse')
           AND maks_auto_forsok_snapshot IS NOT NULL
           AND policy_versjon IS NOT NULL
           AND policy_content_hash IS NOT NULL));
END $$;


-- ...og `unntak_sakskilde_komplett`, som EN­UMERERER hvilke kolonner hver
-- sakskilde skal og ikke skal ha. En ny verdi som ikke står i noen gren
-- er per konstruksjon ulovlig — og det er meningen: `sakskilde` er en
-- LUKKET akse, og en fjerde verdi er en plattformendring, ikke en
-- moduldetalj. M-17 gjør den endringen bevisst og skriver den ned.
--
-- HENVENDELSENS PROFIL ER `policybrudd`s: ingen `oppdrag_id`, ingen
-- `arsak`, og ingen av domeneovertakelsens seks kolonner. Derfor utvides
-- den grenen i stedet for at det legges til en ny — én gren mindre å
-- holde i takt den dagen profilen endres.
--
-- SPLEISEN GJØRES PÅ DEN HENTEDE DEFINISJONEN, med et mønster som må
-- treffe NØYAKTIG ÉN gang, og resultatet verifiseres før det settes
-- inn. Det er varselenum-reparasjonens form, og grunnen er 044: en
-- spleis som ikke sjekker hva den traff, kan skrive en constraint som
-- ser riktig ut og betyr noe annet.
DO $$
DECLARE v_def TEXT; v_gammel TEXT; v_ny TEXT;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO v_def FROM pg_constraint
     WHERE conrelid = 'public.unntak'::regclass
       AND conname = 'unntak_sakskilde_komplett' AND contype = 'c';
    IF v_def IS NULL THEN
        RAISE EXCEPTION '102: fant ikke unntak_sakskilde_komplett';
    END IF;
    IF v_def LIKE '%henvendelse%' THEN
        RETURN;
    END IF;
    v_gammel := '(sakskilde = ''policybrudd''::text)';
    v_ny := '(sakskilde = ANY (ARRAY[''policybrudd''::text,'
            ' ''henvendelse''::text]))';
    IF (length(v_def) - length(replace(v_def, v_gammel, '')))
       / length(v_gammel) <> 1 THEN
        RAISE EXCEPTION '102: policybrudd-grenen i'
            ' unntak_sakskilde_komplett traff % ganger, ikke 1 — uventet'
            ' definisjonsform: %',
            (length(v_def) - length(replace(v_def, v_gammel, '')))
            / length(v_gammel), v_def;
    END IF;
    v_def := replace(v_def, v_gammel, v_ny);
    ALTER TABLE public.unntak DROP CONSTRAINT unntak_sakskilde_komplett;
    EXECUTE format('ALTER TABLE public.unntak ADD CONSTRAINT'
                   ' unntak_sakskilde_komplett %s', v_def);
END $$;

-- ------------------------------------------------------------
-- 0c. RETTELSE AV 101s `avstemming_opphevet_helhet`.
--
--     Den samme trestillede fellen, funnet av M-17s port og rettet der
--     den kan rettes. 101 skrev:
--
--       CHECK ((opphevet_ts IS NULL AND opphevet_av IS NULL
--               AND opphevet_begrunnelse IS NULL)
--              OR (opphevet_ts IS NOT NULL AND opphevet_av IS NOT NULL
--                  AND opphevet_begrunnelse ~ '[^[:space:]]'))
--
--     Med `opphevet_ts` og `opphevet_av` satt og `opphevet_begrunnelse`
--     NULL blir første gren FALSE og andre gren NULL — og `FALSE OR NULL`
--     er NULL, som PASSERER. En oppheving uten begrunnelse var altså
--     representerbar via direkte DML som tabellens eier, stikk i strid
--     med det constrainten sier.
--
--     I PRAKSIS var hullet utilgjengelig: `m13_opphev_avstemming` krever
--     en ikke-tom begrunnelse før den skriver, og vakten krever at
--     `opphevet_av` er aktøren. Men en invariant som bare holder fordi
--     ingen gikk utenom døren, er ikke en invariant — den er en vane.
--
--     RETTELSEN LIGGER HER OG IKKE I 101, fordi migrasjoner er
--     append-only (`test_fasiten_er_append_only_mot_basisgrenen`). Den er
--     idempotent og validerer mot hele tabellen: finnes det en rad som
--     bryter den nye formen, feiler migrasjonen — og det er riktig, for
--     da har hullet vært brukt.
-- ------------------------------------------------------------
DO $$
DECLARE v_def TEXT;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO v_def FROM pg_constraint
     WHERE conrelid = 'public.avstemming'::regclass
       AND conname = 'avstemming_opphevet_helhet' AND contype = 'c';
    IF v_def IS NULL THEN
        RAISE EXCEPTION '102: fant ikke avstemming_opphevet_helhet';
    END IF;
    IF v_def LIKE '%opphevet_begrunnelse IS NOT NULL%' THEN
        RETURN;
    END IF;
    ALTER TABLE public.avstemming
        DROP CONSTRAINT avstemming_opphevet_helhet;
    ALTER TABLE public.avstemming
        ADD CONSTRAINT avstemming_opphevet_helhet CHECK (
            (opphevet_ts IS NULL AND opphevet_av IS NULL
             AND opphevet_begrunnelse IS NULL)
            OR (opphevet_ts IS NOT NULL AND opphevet_av IS NOT NULL
                AND opphevet_begrunnelse IS NOT NULL
                AND opphevet_begrunnelse ~ '[^[:space:]]'));
END $$;


-- ------------------------------------------------------------
-- 1. Tabellene.
-- ------------------------------------------------------------

-- `henvendelse` — det noen spurte om.
--
-- APPEND-ONLY PÅ INNHOLDET. En henvendelse er en OBSERVASJON: det noen
-- skrev til oss endrer seg ikke fordi vi redigerer en rad. Det eneste
-- som lovlig endrer seg er `lukket_ts`/`lukket_av` og
-- `unntak_id` (køkoblingen) — vakten i §2 er den bindende.
--
-- `ekstern_ref` er kanalens egen id, og den unike indeksen på
-- (tenant, kanal, ekstern_ref) ER INNTAKSIDEMPOTENSEN: den samme
-- innboksen lest to ganger gir de samme radene, ikke dobbelt så mange.
-- Uten den ville en gjentatt import sett ut som at kunden spurte to
-- ganger — og da svarer noen to ganger.
CREATE TABLE henvendelse (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    henvendelse_id UUID NOT NULL,
    kanal TEXT NOT NULL CHECK (kanal IN ('epost', 'skjema', 'telefon',
                                         'chat')),
    ekstern_ref TEXT NOT NULL CHECK (ekstern_ref ~ '[^[:space:]]'),
    mottatt TIMESTAMPTZ NOT NULL,
    -- AVSENDEREN SOM HASH. Registeret trenger å kunne kjenne igjen den
    -- samme avsenderen på tvers av henvendelser; det trenger ikke å
    -- kunne lese adressen. Å lagre det man ikke trenger er hvordan et
    -- register blir et brudd (101s kontonummer-dom, samme form).
    avsender_hash TEXT NOT NULL CHECK (avsender_hash ~ '^[0-9a-f]{64}$'),
    -- EMNE OG KROPP ER PERSONDATA og bærer tenant-DEK-kryptert innhold
    -- på 058/088-formen. Ett nøkkelpar for begge: de skrives i samme
    -- transaksjon og leses sammen, og to key_id-er på samme rad ville
    -- vært to rotasjonstilstander å holde i takt.
    emne_kryptert BYTEA NOT NULL,
    kropp_kryptert BYTEA NOT NULL,
    nonce_emne BYTEA NOT NULL,
    nonce_kropp BYTEA NOT NULL,
    key_id TEXT NOT NULL,
    -- Køkoblingen. NULL = ikke i M-37s kø. Peker på `unntak.id`, som er
    -- BIGINT — ikke en kopi av innholdet.
    unntak_id BIGINT,
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT,
    lukket_utfall TEXT CHECK (lukket_utfall IN ('besvart', 'ikke_aktuell')),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    CONSTRAINT henvendelse_pk PRIMARY KEY (tenant, henvendelse_id),
    -- DEK-referansen bindes som i 003/005/007/011/016/058/088: en ukjent
    -- eller krysstenant nøkkel-id ville sett komplett ut helt til
    -- dekrypteringen feiler hos alle.
    CONSTRAINT henvendelse_dek_fk FOREIGN KEY (tenant, key_id)
        REFERENCES tenant_nokler (tenant, key_id),
    CONSTRAINT henvendelse_koe_fk FOREIGN KEY (tenant, unntak_id)
        REFERENCES unntak (tenant, id),
    -- Kryptostrukturen på TABELLEN (016/017/058/088-formen): 12-byte
    -- nonce fra db/kryptering.py, som overalt ellers.
    CONSTRAINT henvendelse_krypto_struktur
        CHECK (octet_length(nonce_emne) = 12
               AND octet_length(nonce_kropp) = 12),
    -- Lukkingens tre felter står eller faller sammen. En lukket
    -- henvendelse uten hvem og hvilket utfall er en rad som sier at noen
    -- gjorde noe, og ingenting mer.
    CONSTRAINT henvendelse_lukking_helhet
        CHECK ((lukket_ts IS NULL AND lukket_av IS NULL
                AND lukket_utfall IS NULL)
               OR (lukket_ts IS NOT NULL AND lukket_av IS NOT NULL
                   AND lukket_utfall IS NOT NULL))
);
CREATE UNIQUE INDEX henvendelse_inntak_unik
    ON henvendelse (tenant, kanal, ekstern_ref);
-- Sveipens spørring: åpne henvendelser sortert på alder.
CREATE INDEX henvendelse_apne ON henvendelse (tenant, mottatt)
    WHERE lukket_ts IS NULL;

-- `klassifisering` — 1:1 med henvendelsen. TRE LUKKEDE AKSER, fordi de
-- svarer på tre forskjellige spørsmål: hvor haster det, hva handler det
-- om, og hva skal skje. Ett felt med alle tre ville tvunget fram
-- kategorier som «haster_faktura_svar» — og et sett som vokser
-- multiplikativt er et sett ingen kan lukke.
CREATE TABLE klassifisering (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    henvendelse_id UUID NOT NULL,
    prioritet TEXT NOT NULL CHECK (prioritet IN ('kritisk', 'hoy',
                                                 'normal', 'lav')),
    tema TEXT NOT NULL CHECK (tema IN ('faktura', 'leveranse', 'teknisk',
                                       'salg', 'klage', 'annet')),
    handlingstype TEXT NOT NULL CHECK (handlingstype IN (
        'svar_kreves', 'til_info', 'oppgave', 'mote', 'nyhetsbrev',
        'mistenkelig')),
    -- HVEM KLASSIFISERTE. I PR-A alltid `menneske`; PR-B legger til
    -- `modell` OG `modell_digest`, og CHECK-en under er det som gjør at
    -- en modellklassifisering ALDRI kan stå uten å si hvilken modell.
    kilde TEXT NOT NULL CHECK (kilde IN ('menneske', 'modell')),
    modell_digest TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    CONSTRAINT klassifisering_pk PRIMARY KEY (tenant, henvendelse_id),
    CONSTRAINT klassifisering_henvendelse_fk
        FOREIGN KEY (tenant, henvendelse_id)
        REFERENCES henvendelse (tenant, henvendelse_id),
    -- EN MODELLKLASSIFISERING UTEN DIGEST ER USPORBAR: da kan ingen si
    -- hvilken modell som mente dette da modellen byttes (M-31s dom).
    -- Og et menneske har ingen digest — feltet skal stå tomt der.
    -- `IS NOT NULL` FØRST, OG DET ER IKKE OVERFLØDIG. `NULL ~ '...'` er
    -- NULL, ikke FALSE, og en CHECK som evaluerer til NULL PASSERER. Uten
    -- det første leddet ville `kilde='modell'` med `modell_digest IS NULL`
    -- gitt `(TRUE AND NULL) OR (FALSE AND ...)` = NULL — altså nøyaktig
    -- den raden invarianten finnes for å hindre, sluppet gjennom av
    -- trestillet logikk. Porten i `test_m17_kundeservice.py` fant det.
    CONSTRAINT klassifisering_modell_krever_digest
        CHECK ((kilde = 'modell' AND modell_digest IS NOT NULL
                AND modell_digest ~ '[^[:space:]]')
               OR (kilde = 'menneske' AND modell_digest IS NULL))
);

-- `svarutkast` — APPEND-ONLY. Regenerering lager en NY rad.
--
-- `status` er utkastets egen tilstand og ikke henvendelsens: et utkast
-- kan forkastes uten at henvendelsen lukkes, og et utkast som ble brukt
-- manuelt sier at et menneske sendte noe basert på det — aldri at
-- modulen sendte det.
CREATE TABLE svarutkast (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    utkast_id UUID NOT NULL,
    henvendelse_id UUID NOT NULL,
    tekst_kryptert BYTEA NOT NULL,
    nonce BYTEA NOT NULL,
    key_id TEXT NOT NULL,
    -- HVA UTKASTET SITERER. M-9 eier begrepene; utkastet peker på dem og
    -- eier ikke innholdet. Tom liste er lovlig — et utkast trenger ikke
    -- sitere noe — men en peker til et begrep som ikke finnes er det
    -- ikke, og det er derfor listen er TEXT[] og ikke fritekst.
    kunnskapsref TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    kilde TEXT NOT NULL CHECK (kilde IN ('menneske', 'modell')),
    modell_digest TEXT,
    status TEXT NOT NULL DEFAULT 'foreslatt'
        CHECK (status IN ('foreslatt', 'forkastet', 'brukt_manuelt')),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    CONSTRAINT svarutkast_pk PRIMARY KEY (tenant, utkast_id),
    CONSTRAINT svarutkast_henvendelse_fk
        FOREIGN KEY (tenant, henvendelse_id)
        REFERENCES henvendelse (tenant, henvendelse_id),
    CONSTRAINT svarutkast_dek_fk FOREIGN KEY (tenant, key_id)
        REFERENCES tenant_nokler (tenant, key_id),
    CONSTRAINT svarutkast_krypto_struktur CHECK (octet_length(nonce) = 12),
    -- Samme trestillede felle som på `klassifisering` over, samme form.
    CONSTRAINT svarutkast_modell_krever_digest
        CHECK ((kilde = 'modell' AND modell_digest IS NOT NULL
                AND modell_digest ~ '[^[:space:]]')
               OR (kilde = 'menneske' AND modell_digest IS NULL))
);
CREATE INDEX svarutkast_henvendelse
    ON svarutkast (tenant, henvendelse_id, opprettet DESC);

-- `henvendelsesfunn` — sveipens dom. Samme form som 100/101: idempotent
-- per (henvendelse, funntype), og en rad som lukkes består.
CREATE TABLE henvendelsesfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    henvendelse_id UUID NOT NULL,
    funntype TEXT NOT NULL CHECK (funntype IN (
        'uklassifisert_over_grense',
        'ubesvart_over_grense',
        'mistenkelig_uten_behandling')),
    dogn_over_grense INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    CONSTRAINT henvendelsesfunn_pk
        PRIMARY KEY (tenant, henvendelse_id, funntype),
    CONSTRAINT henvendelsesfunn_henvendelse_fk
        FOREIGN KEY (tenant, henvendelse_id)
        REFERENCES henvendelse (tenant, henvendelse_id),
    CONSTRAINT henvendelsesfunn_lukking
        CHECK ((apen AND lukket_ts IS NULL)
               OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE INDEX henvendelsesfunn_apne ON henvendelsesfunn (tenant, funntype)
    WHERE apen;


-- ------------------------------------------------------------
-- 2. Radvaktene. CHECK-ene over gjelder én rad; vaktene gjelder
--    FORHOLDET mellom rader og OVERGANGENE — og de gjelder enhver
--    skrivevei, også direkte DML som tabellens eier.
-- ------------------------------------------------------------

-- DOM 1, i vaktform: EN HENVENDELSE FORSVINNER ALDRI, og innholdet
-- endres aldri. Det eneste som lovlig beveger seg er køkoblingen og
-- lukkingen — og lukkingen går ÉN VEI, med en navngitt aktør.
CREATE FUNCTION m17_henvendelse_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'henvendelse: DELETE avvist — en tapt henvendelse'
            ' er verre enn en uklassifisert, og den er usynlig'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.henvendelse_id IS DISTINCT FROM OLD.henvendelse_id
       OR NEW.kanal IS DISTINCT FROM OLD.kanal
       OR NEW.ekstern_ref IS DISTINCT FROM OLD.ekstern_ref
       OR NEW.mottatt IS DISTINCT FROM OLD.mottatt
       OR NEW.avsender_hash IS DISTINCT FROM OLD.avsender_hash
       OR NEW.emne_kryptert IS DISTINCT FROM OLD.emne_kryptert
       OR NEW.kropp_kryptert IS DISTINCT FROM OLD.kropp_kryptert
       OR NEW.nonce_emne IS DISTINCT FROM OLD.nonce_emne
       OR NEW.nonce_kropp IS DISTINCT FROM OLD.nonce_kropp
       OR NEW.key_id IS DISTINCT FROM OLD.key_id
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'henvendelse: innholdet er append-only — det noen'
            ' skrev til oss endrer seg ikke fordi vi redigerer en rad'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- KØKOBLINGEN GÅR ÉN VEI. En henvendelse som kunne løsrives fra sin
    -- unntakssak ville gjort køen til et sted saker forsvinner fra.
    IF OLD.unntak_id IS NOT NULL
       AND NEW.unntak_id IS DISTINCT FROM OLD.unntak_id THEN
        RAISE EXCEPTION 'henvendelse: køkoblingen er satt og løsrives'
            ' ikke — M-37 eier saken derfra'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- …og lukkingen likeså.
    IF OLD.lukket_ts IS NOT NULL
       AND (NEW.lukket_ts IS DISTINCT FROM OLD.lukket_ts
            OR NEW.lukket_av IS DISTINCT FROM OLD.lukket_av
            OR NEW.lukket_utfall IS DISTINCT FROM OLD.lukket_utfall) THEN
        RAISE EXCEPTION 'henvendelse: en lukket henvendelse gjenåpnes'
            ' ikke — en ny sak er en ny henvendelse'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.lukket_ts IS NOT NULL AND OLD.lukket_ts IS NULL THEN
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL OR NEW.lukket_av IS DISTINCT FROM v_aktor THEN
            RAISE EXCEPTION 'henvendelse: lukket_av (%) er ikke aktøren'
                ' som lukker (%) — tiden besvarer ingen henvendelse',
                coalesce(NEW.lukket_av, '<null>'),
                coalesce(v_aktor, '<ingen>')
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- «BESVART» KREVER AT NOEN FAKTISK SKREV NOE. Et utkast merket
        -- `brukt_manuelt` er sporet etter at et menneske sendte et svar;
        -- uten det er «besvart» en påstand ingen kan etterprøve.
        -- `ikke_aktuell` har ikke kravet — den sier nettopp at det ikke
        -- skulle svares.
        IF NEW.lukket_utfall = 'besvart'
           AND NOT EXISTS (SELECT 1 FROM public.svarutkast u
                            WHERE u.tenant = NEW.tenant
                              AND u.henvendelse_id = NEW.henvendelse_id
                              AND u.status = 'brukt_manuelt') THEN
            RAISE EXCEPTION 'henvendelse: «besvart» krever et utkast'
                ' merket brukt_manuelt — ellers er det en påstand ingen'
                ' kan etterprøve'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m17_henvendelse_vakt() FROM PUBLIC;
CREATE TRIGGER m17_henvendelse_vakt
    BEFORE UPDATE OR DELETE ON henvendelse
    FOR EACH ROW EXECUTE FUNCTION m17_henvendelse_vakt();
CREATE TRIGGER m17_henvendelse_ingen_truncate
    BEFORE TRUNCATE ON henvendelse
    EXECUTE FUNCTION m17_henvendelse_vakt();

-- Klassifiseringen kan RETTES — et menneske som ser at modellen tok feil
-- skal kunne si det — men identiteten og kilden er frosset. En
-- modellklassifisering som stille ble til en menneskelig ville skjult
-- hvem som faktisk mente noe.
CREATE FUNCTION m17_klassifisering_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'klassifisering: DELETE avvist — en rettelse er'
            ' en ny verdi, ikke en slettet rad'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.henvendelse_id IS DISTINCT FROM OLD.henvendelse_id
       OR NEW.kilde IS DISTINCT FROM OLD.kilde
       OR NEW.modell_digest IS DISTINCT FROM OLD.modell_digest
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'klassifisering: identiteten og kilden er frosset'
            ' — hvem som mente noe endrer seg ikke'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m17_klassifisering_vakt() FROM PUBLIC;
CREATE TRIGGER m17_klassifisering_vakt
    BEFORE UPDATE OR DELETE ON klassifisering
    FOR EACH ROW EXECUTE FUNCTION m17_klassifisering_vakt();
CREATE TRIGGER m17_klassifisering_ingen_truncate
    BEFORE TRUNCATE ON klassifisering
    EXECUTE FUNCTION m17_klassifisering_vakt();

-- DOM 3, i vaktform: UTKASTET ER APPEND-ONLY PÅ TEKSTEN. Det eneste som
-- lovlig endrer seg er `status`, og bare fremover: et forkastet eller
-- brukt utkast går ikke tilbake til `foreslatt`.
CREATE FUNCTION m17_utkast_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'svarutkast: DELETE avvist — at et utkast fantes'
            ' er også historikk'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.utkast_id IS DISTINCT FROM OLD.utkast_id
       OR NEW.henvendelse_id IS DISTINCT FROM OLD.henvendelse_id
       OR NEW.tekst_kryptert IS DISTINCT FROM OLD.tekst_kryptert
       OR NEW.nonce IS DISTINCT FROM OLD.nonce
       OR NEW.key_id IS DISTINCT FROM OLD.key_id
       OR NEW.kunnskapsref IS DISTINCT FROM OLD.kunnskapsref
       OR NEW.kilde IS DISTINCT FROM OLD.kilde
       OR NEW.modell_digest IS DISTINCT FROM OLD.modell_digest
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'svarutkast: teksten er append-only — et utkast'
            ' som endres under føttene på den som leser det, er et'
            ' utkast ingen kan stå for å ha sendt. Regenerering er en NY'
            ' rad' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status <> 'foreslatt' AND NEW.status IS DISTINCT FROM OLD.status
       THEN
        RAISE EXCEPTION 'svarutkast: status går bare fra foreslatt — et'
            ' forkastet eller brukt utkast er avgjort'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m17_utkast_vakt() FROM PUBLIC;
CREATE TRIGGER m17_utkast_vakt
    BEFORE UPDATE OR DELETE ON svarutkast
    FOR EACH ROW EXECUTE FUNCTION m17_utkast_vakt();
CREATE TRIGGER m17_utkast_ingen_truncate
    BEFORE TRUNCATE ON svarutkast
    EXECUTE FUNCTION m17_utkast_vakt();

-- Funnene: samme form som 100/101.
CREATE FUNCTION m17_funn_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'henvendelsesfunn: DELETE avvist — et funn lukkes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.tenant IS DISTINCT FROM OLD.tenant
           OR NEW.henvendelse_id IS DISTINCT FROM OLD.henvendelse_id
           OR NEW.funntype IS DISTINCT FROM OLD.funntype
           OR NEW.forst_sett IS DISTINCT FROM OLD.forst_sett THEN
            RAISE EXCEPTION 'henvendelsesfunn: identiteten og førstegangen'
                ' er frosset — når vi FØRST så noe er hele poenget'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m17_funn_vakt() FROM PUBLIC;
CREATE TRIGGER m17_funn_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON henvendelsesfunn
    FOR EACH ROW EXECUTE FUNCTION m17_funn_vakt();
CREATE TRIGGER m17_funn_ingen_truncate
    BEFORE TRUNCATE ON henvendelsesfunn
    EXECUTE FUNCTION m17_funn_vakt();


-- ------------------------------------------------------------
-- 2b. RLS og rettigheter.
-- ------------------------------------------------------------
ALTER TABLE henvendelse ENABLE ROW LEVEL SECURITY;
ALTER TABLE henvendelse FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON henvendelse
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER — ingen BYPASSRLS.
-- Tre gjerder, som i 100/101: bare dørenes eier, bare SELECT, og bare
-- når det IKKE står en tenantkontekst i sesjonen. Dørene kommer alltid
-- gjennom `krev_tenantkontekst`, så inne i en dør er policyen ALLTID
-- usann — de to er disjunkte per konstruksjon.
CREATE POLICY m17_sveip_tenantliste ON henvendelse
    FOR SELECT TO disponit_kundeservice_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

ALTER TABLE klassifisering ENABLE ROW LEVEL SECURITY;
ALTER TABLE klassifisering FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON klassifisering
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE svarutkast ENABLE ROW LEVEL SECURITY;
ALTER TABLE svarutkast FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON svarutkast
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

ALTER TABLE henvendelsesfunn ENABLE ROW LEVEL SECURITY;
ALTER TABLE henvendelsesfunn FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON henvendelsesfunn
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
-- INGEN kryss-tenant-policy på de tre andre: sveipen finner tenantene i
-- `henvendelse` (uten kontekst) og gjør ALT arbeidet med RADENS tenant
-- satt. Skrivingen er dermed tenantbundet av RLS, også for sveipen selv.

-- Rettighetene dørenes eier trenger, og ikke mer. Ingen runtime-rolle
-- får en eneste tabellrettighet på de fire tabellene (SP-7).
GRANT SELECT, INSERT, UPDATE ON henvendelse TO disponit_kundeservice_eier;
GRANT SELECT, INSERT, UPDATE ON klassifisering
    TO disponit_kundeservice_eier;
GRANT SELECT, INSERT, UPDATE ON svarutkast TO disponit_kundeservice_eier;
GRANT SELECT, INSERT, UPDATE ON henvendelsesfunn
    TO disponit_kundeservice_eier;

-- Evidenskjeden. Én skrivevei, som alle andre modulers.
GRANT INSERT ON revisjonslogg TO disponit_kundeservice_eier;

-- KØVEIEN INN I M-37. Eieren får INSERT på `unntak` og på
-- `unntak_historikk` — og INGENTING MER: ingen UPDATE, ingen DELETE,
-- ingen SELECT på andres saker utover det RLS slipper gjennom. M-17
-- LEGGER noe i køen; M-37 eier den derfra. Å kunne endre en sak man har
-- lagt inn ville gjort køen til noe man kan trekke tilbake fra.
GRANT INSERT ON unntak TO disponit_kundeservice_eier;
GRANT SELECT (id, tenant, status, sakskilde) ON unntak
    TO disponit_kundeservice_eier;
GRANT USAGE, SELECT ON SEQUENCE unntak_id_seq TO disponit_kundeservice_eier;
GRANT INSERT ON unntak_historikk TO disponit_kundeservice_eier;
-- Loggposten køen peker på (`unntak.loggpost_id` er NOT NULL med FK):
-- eieren må kunne lese id-en den nettopp skrev.
GRANT SELECT (id, tenant) ON revisjonslogg TO disponit_kundeservice_eier;
-- DEK-oppslaget: dørene tar imot FERDIG kryptert innhold fra API-laget
-- (058/088-formen — krypteringen skjer i `db/kryptering.py`, aldri i
-- basen), men FK-en mot `tenant_nokler` må kunne verifiseres.
GRANT REFERENCES ON tenant_nokler TO disponit_kundeservice_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_kundeservice_eier;
RESET ROLE;


-- ------------------------------------------------------------
-- 3. Dørene. SECURITY DEFINER, eid av `disponit_kundeservice_eier`, og
--    hver tenantbundet dør kaller `krev_tenantkontekst` FØRST (SP-1).
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_kundeservice_eier;

-- Evidenskjeden, ett sted. Returnerer LOGGPOST-ID-en, til forskjell fra
-- 100/101s variant — køveien trenger den (`unntak.loggpost_id` er NOT
-- NULL med FK), og en andre skrivevei bare for å få tak i id-en ville
-- vært to steder å holde evidensformen i takt.
--
-- HVERKEN EMNE, KROPP ELLER AVSENDER STÅR HER. Det er kundens tekst og
-- den andre partens adresse; evidenskjeden skal kunne gjenfinne
-- HANDLINGEN uten å arkivere henvendelsen på nytt et sted til.
CREATE FUNCTION m17_evidens(p_tenant TEXT, p_henvendelse_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT; v_id BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm17_kundeservice', 'handling', p_handling,
        'henvendelse_id', p_henvendelse_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm17_kundeservice',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:henvendelsesregister', 'TILLAT',
            jsonb_build_array(p_handling), p_handling)
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;
REVOKE ALL ON FUNCTION m17_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- INNTAKSDØREN. Tar imot FERDIG kryptert emne og kropp (krypteringen
-- skjer i `db/kryptering.py` i API-laget, som i 058/088) og en HASH av
-- avsenderen.
--
-- DEN VIRKELIGE IDEMPOTENSEN ER `ekstern_ref`, ikke Idempotency-Key-en:
-- nøkkelen beskytter mot dobbeltklikk, kanalens egen id beskytter mot
-- den samme innboksen lest to ganger — og det siste er det som faktisk
-- skjer. Døren returnerer derfor `(ny, lagret_henvendelse_id)`: er
-- henvendelsen alt tatt inn under en annen nøkkel, er den lagrede raden
-- en ANNEN enn den kalleren utledet, og en dør som bare svarte `false`
-- ville latt flaten sitte igjen med en id ingen rad har (101s
-- CodeRabbit-funn, samme form og samme grunn).
CREATE FUNCTION m17_ta_imot(
    p_tenant TEXT, p_henvendelse_id UUID, p_kanal TEXT,
    p_ekstern_ref TEXT, p_mottatt TIMESTAMPTZ, p_avsender_hash TEXT,
    p_emne_kryptert BYTEA, p_nonce_emne BYTEA,
    p_kropp_kryptert BYTEA, p_nonce_kropp BYTEA, p_key_id TEXT,
    p_aktor TEXT)
RETURNS TABLE(ny BOOLEAN, lagret_henvendelse_id UUID)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_gammel RECORD; v_ref TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_ta_imot');
    IF p_kanal IS NULL OR p_kanal NOT IN ('epost', 'skjema', 'telefon',
                                          'chat') THEN
        RAISE EXCEPTION 'm17_ta_imot: ukjent kanal'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_ref := btrim(coalesce(p_ekstern_ref, ''));
    IF v_ref = '' THEN
        RAISE EXCEPTION 'm17_ta_imot: kanalens referanse kan ikke være'
            ' tom — den ER inntaksidempotensen'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_avsender_hash IS NULL OR p_avsender_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'm17_ta_imot: avsenderen lagres som sha256-hash,'
            ' aldri som adresse'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO v_gammel FROM public.henvendelse h
     WHERE h.tenant = p_tenant AND h.kanal = p_kanal
       AND h.ekstern_ref = v_ref;
    IF FOUND THEN
        -- SAMME HENVENDELSE, ALT TATT IMOT. Kalleren får id-en til raden
        -- som FAKTISK står der. Innholdet sammenlignes IKKE: ciphertext
        -- er ikke deterministisk (tilfeldig nonce), så to krypteringer av
        -- den samme teksten er alltid ulike bytes — en materialitets-
        -- sjekk på dem ville sagt «konflikt» hver eneste gang.
        ny := false; lagret_henvendelse_id := v_gammel.henvendelse_id;
        RETURN NEXT;
        RETURN;
    END IF;
    -- `ON CONFLICT DO NOTHING` UTEN MÅL, og det er ikke slurv: raden har
    -- TO unike nøkler — primærnøkkelen (kallerens utledede id) og
    -- `henvendelse_inntak_unik` (tenant, kanal, ekstern_ref). SELECT-en
    -- over fanger den andre i det ALMINNELIGE tilfellet, men to samtidige
    -- innlesinger av den samme innboksen kan begge passere den og møtes
    -- først i indeksen. Med et MÅL på `(tenant, henvendelse_id)` ville
    -- den andre da fått `unique_violation` — altså 409 på en henvendelse
    -- som beviselig er tatt imot, og en innboksimport som stopper på seg
    -- selv. Uten mål svelges begge, og oppslaget under gir kalleren
    -- id-en til raden som faktisk står der.
    INSERT INTO public.henvendelse
        (tenant, henvendelse_id, kanal, ekstern_ref, mottatt,
         avsender_hash, emne_kryptert, kropp_kryptert, nonce_emne,
         nonce_kropp, key_id, opprettet_av)
    VALUES (p_tenant, p_henvendelse_id, p_kanal, v_ref, p_mottatt,
            p_avsender_hash, p_emne_kryptert, p_kropp_kryptert,
            p_nonce_emne, p_nonce_kropp, p_key_id, p_aktor)
        ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        -- KAPPLØPSTAPEREN. Les raden som VANT, på den naturlige nøkkelen
        -- — den er den samme uansett hvilken av de to indeksene som
        -- stoppet oss.
        SELECT h.henvendelse_id INTO lagret_henvendelse_id
          FROM public.henvendelse h
         WHERE h.tenant = p_tenant AND h.kanal = p_kanal
           AND h.ekstern_ref = v_ref;
        IF lagret_henvendelse_id IS NULL THEN
            -- Kollisjonen var på primærnøkkelen med en ANNEN naturlig
            -- nøkkel: samme utledede id, annen melding. Det er en
            -- materiell konflikt kalleren skal se, ikke et stille ja.
            RAISE EXCEPTION 'm17_ta_imot: samme henvendelse_id med en'
                ' annen kanal/referanse — materiell konflikt'
                USING ERRCODE = 'unique_violation';
        END IF;
        ny := false;
        RETURN NEXT;
        RETURN;
    END IF;
    PERFORM public.m17_evidens(
        p_tenant, p_henvendelse_id, 'henvendelse.mottatt', p_aktor,
        jsonb_build_object('kanal', p_kanal));
    ny := true; lagret_henvendelse_id := p_henvendelse_id;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m17_ta_imot(TEXT, UUID, TEXT, TEXT, TIMESTAMPTZ,
    TEXT, BYTEA, BYTEA, BYTEA, BYTEA, TEXT, TEXT) FROM PUBLIC;

-- KLASSIFISERINGSDØREN. `p_kilde` er `menneske` i PR-A; PR-B sender
-- `modell` og en digest, og CHECK-en i §1 er det som gjør at en
-- modellklassifisering aldri kan stå uten å si hvilken modell.
--
-- REKLASSIFISERING ER TILLATT og skriver over de tre aksene — et
-- menneske som ser at klassifiseringen er feil skal kunne rette den.
-- Kilden er frosset (vakten): en modellklassifisering som stille ble til
-- en menneskelig ville skjult hvem som faktisk mente noe. Rettelsen fra
-- et menneske er derfor en NY rad bare når kilden endres, og det kan den
-- ikke — så en menneskelig rettelse av en modelldom krever at raden
-- slettes... som vakten nekter. DET ER MED VILJE: PR-B legger til
-- `m17_overstyr_klassifisering`, som skriver en EGEN rad med kilde
-- `menneske` og lar modellens dom stå ved siden av. v1 har bare
-- menneskelige klassifiseringer, så spørsmålet oppstår ikke ennå — men
-- formen skal ikke oppfinnes i hastverk når det gjør det.
CREATE FUNCTION m17_klassifiser(
    p_tenant TEXT, p_henvendelse_id UUID, p_prioritet TEXT, p_tema TEXT,
    p_handlingstype TEXT, p_kilde TEXT, p_modell_digest TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_finnes BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_klassifiser');
    IF NOT EXISTS (SELECT 1 FROM public.henvendelse h
                    WHERE h.tenant = p_tenant
                      AND h.henvendelse_id = p_henvendelse_id) THEN
        RAISE EXCEPTION 'm17_klassifiser: henvendelsen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    SELECT true INTO v_finnes FROM public.klassifisering k
     WHERE k.tenant = p_tenant AND k.henvendelse_id = p_henvendelse_id;
    IF v_finnes THEN
        UPDATE public.klassifisering
           SET prioritet = p_prioritet, tema = p_tema,
               handlingstype = p_handlingstype, opprettet_av = p_aktor
         WHERE tenant = p_tenant AND henvendelse_id = p_henvendelse_id;
        PERFORM public.m17_evidens(
            p_tenant, p_henvendelse_id, 'henvendelse.omklassifisert',
            p_aktor, jsonb_build_object('prioritet', p_prioritet,
                                        'tema', p_tema,
                                        'handlingstype', p_handlingstype));
        RETURN false;
    END IF;
    INSERT INTO public.klassifisering
        (tenant, henvendelse_id, prioritet, tema, handlingstype, kilde,
         modell_digest, opprettet_av)
    VALUES (p_tenant, p_henvendelse_id, p_prioritet, p_tema,
            p_handlingstype, p_kilde,
            nullif(btrim(coalesce(p_modell_digest, '')), ''), p_aktor);
    PERFORM public.m17_evidens(
        p_tenant, p_henvendelse_id, 'henvendelse.klassifisert', p_aktor,
        jsonb_build_object('prioritet', p_prioritet, 'tema', p_tema,
                           'handlingstype', p_handlingstype,
                           'kilde', p_kilde));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m17_klassifiser(TEXT, UUID, TEXT, TEXT, TEXT, TEXT,
    TEXT, TEXT) FROM PUBLIC;

-- KØVEIEN INN I M-37. DOM 4, i dørform.
--
-- Det uavklarte går til `unntak` — ikke til en tabell som heter noe
-- annet. En andre kø ved siden av M-37s er nøyaktig det M-37 ble bygget
-- for å hindre, og porten `andre_unntakskø_opprettet` måler at 102 ikke
-- oppretter en.
--
-- KØEN BÆRER EN REFERANSE, IKKE INNHOLDET. Henvendelsens tekst er
-- persondata og ligger kryptert i `henvendelse`; en kopi i køens payload
-- ville vært det samme persondatasettet i to lagre med hver sin
-- retensjon — altså nøyaktig den formen M-4s retensjonsregnskap finnes
-- for å hindre. Payloaden her er derfor bare henvendelsens ID, kanalen
-- og saksbehandlerens EGEN setning om hva som er uavklart.
--
-- OG DEN ER KRYPTERT (`payload_type='kryptert'`, 038/056-formen), ikke
-- `referanse`. Grunnen er konkret og ikke prinsipiell:
-- `unntak_referansepayload_speiler` (041) krever at en referansepayload
-- SPEILER de seks domeneovertakelses-kolonnene — `hostname_ref`,
-- `autorisasjonsgenerasjon`, `utfordrer_tenant`, `tapt_tenant` og de to
-- hendelses-id-ene — og alle seks er NULL for en henvendelse. Formen er
-- altså reservert for den ene sakstypen den ble laget for. Å utvide den
-- CHECKen ville vært å endre en annen moduls invariant for å slippe inn
-- min; den krypterte payloaden er husets ALMINNELIGE vei, den koster
-- ingenting her (vi har alt tenantens DEK for henvendelsen), og den er
-- strengere, ikke løsere.
--
-- SNAPSHOT-TRIOEN STÅR NULL, som for domeneovertakelsene (041): trioen
-- snapshotter en POLICYBESLUTNING med et forsøksbudsjett, og en
-- henvendelse som ikke lot seg klassifisere er ingen slik beslutning.
--
-- IDEMPOTENT: er henvendelsen alt i køen, returneres den samme
-- unntaks-id-en. To klikk på «kan ikke avgjøres» skal ikke gi to saker.
CREATE FUNCTION m17_til_unntakskoe(
    p_tenant TEXT, p_henvendelse_id UUID, p_begrunnelse TEXT,
    p_payload_kryptert BYTEA, p_nonce BYTEA, p_key_id TEXT, p_aktor TEXT)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_h RECORD; v_logg BIGINT; v_sak BIGINT; v_kl RECORD;
        v_prioritet TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_til_unntakskoe');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_begrunnelse IS NULL OR p_begrunnelse !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm17_til_unntakskoe: en sak uten begrunnelse er en'
            ' oppgave ingen vet hva er'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT * INTO v_h FROM public.henvendelse h
     WHERE h.tenant = p_tenant AND h.henvendelse_id = p_henvendelse_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm17_til_unntakskoe: henvendelsen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    -- ALT I KØEN er et stille ja: samme sak tilbake, ingen ny rad.
    IF v_h.unntak_id IS NOT NULL THEN
        RETURN v_h.unntak_id;
    END IF;
    IF v_h.lukket_ts IS NOT NULL THEN
        RAISE EXCEPTION 'm17_til_unntakskoe: en lukket henvendelse hører'
            ' ikke hjemme i køen'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- PRIORITETEN ARVES FRA KLASSIFISERINGEN der den finnes. En
    -- `kritisk`/`hoy` henvendelse skal ikke havne bakerst i køen fordi
    -- den tilfeldigvis kom inn gjennom M-17. `mistenkelig` blir
    -- SIKKERHETSSAK — det er den ene handlingstypen der køens egen
    -- klasse betyr noe annet enn hastverk.
    SELECT * INTO v_kl FROM public.klassifisering k
     WHERE k.tenant = p_tenant AND k.henvendelse_id = p_henvendelse_id;
    v_prioritet := CASE WHEN v_kl.prioritet IN ('kritisk', 'hoy')
                        THEN 'hoy' ELSE 'normal' END;
    v_logg := public.m17_evidens(
        p_tenant, p_henvendelse_id, 'henvendelse.til_unntakskoe', p_aktor,
        jsonb_build_object('begrunnelse_lengde', length(p_begrunnelse)));
    INSERT INTO public.unntak
        (tenant, loggpost_id, handling, kategori, sakstype, prioritet,
         sakskilde, payload_type, payload_kryptert, nonce, key_id,
         maks_auto_forsok_snapshot, policy_versjon, policy_content_hash)
    VALUES (p_tenant, v_logg, 'henvendelse.uavklart',
            'henvendelse_uavklart',
            CASE WHEN v_kl.handlingstype = 'mistenkelig'
                 THEN 'sikkerhet' ELSE 'normal' END,
            CASE WHEN v_kl.handlingstype = 'mistenkelig'
                 THEN 'hoy' ELSE v_prioritet END,
            'henvendelse', 'kryptert',
            p_payload_kryptert, p_nonce, p_key_id,
            NULL, NULL, NULL)
    RETURNING id INTO v_sak;
    UPDATE public.henvendelse
       SET unntak_id = v_sak
     WHERE tenant = p_tenant AND henvendelse_id = p_henvendelse_id;
    RETURN v_sak;
END $$;
REVOKE ALL ON FUNCTION m17_til_unntakskoe(TEXT, UUID, TEXT, BYTEA,
    BYTEA, TEXT, TEXT) FROM PUBLIC;

-- UTKASTDØREN. Append-only: hver regenerering er en NY rad.
CREATE FUNCTION m17_lagre_utkast(
    p_tenant TEXT, p_utkast_id UUID, p_henvendelse_id UUID,
    p_tekst_kryptert BYTEA, p_nonce BYTEA, p_key_id TEXT,
    p_kunnskapsref TEXT[], p_kilde TEXT, p_modell_digest TEXT,
    p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_lagre_utkast');
    IF NOT EXISTS (SELECT 1 FROM public.henvendelse h
                    WHERE h.tenant = p_tenant
                      AND h.henvendelse_id = p_henvendelse_id) THEN
        RAISE EXCEPTION 'm17_lagre_utkast: henvendelsen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    INSERT INTO public.svarutkast
        (tenant, utkast_id, henvendelse_id, tekst_kryptert, nonce,
         key_id, kunnskapsref, kilde, modell_digest, opprettet_av)
    VALUES (p_tenant, p_utkast_id, p_henvendelse_id, p_tekst_kryptert,
            p_nonce, p_key_id, coalesce(p_kunnskapsref, ARRAY[]::TEXT[]),
            p_kilde, nullif(btrim(coalesce(p_modell_digest, '')), ''),
            p_aktor)
        ON CONFLICT (tenant, utkast_id) DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;
    END IF;
    PERFORM public.m17_evidens(
        p_tenant, p_henvendelse_id, 'utkast.lagret', p_aktor,
        jsonb_build_object('utkast_id', p_utkast_id::text,
                           'kilde', p_kilde));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m17_lagre_utkast(TEXT, UUID, UUID, BYTEA, BYTEA,
    TEXT, TEXT[], TEXT, TEXT, TEXT) FROM PUBLIC;

-- UTKASTETS DOM. `brukt_manuelt` er sporet etter at ET MENNESKE sendte
-- noe basert på utkastet — aldri at modulen sendte det. Ordvalget er
-- dommen, og det står i CHECK-en i §1: det finnes ingen verdi som heter
-- `sendt`.
CREATE FUNCTION m17_avgjor_utkast(
    p_tenant TEXT, p_utkast_id UUID, p_status TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_hid UUID; v_gammel TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_avgjor_utkast');
    IF p_status IS NULL OR p_status NOT IN ('forkastet', 'brukt_manuelt')
       THEN
        RAISE EXCEPTION 'm17_avgjor_utkast: status må være forkastet'
            ' eller brukt_manuelt — modulen SENDER ingenting'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT henvendelse_id, status INTO v_hid, v_gammel
      FROM public.svarutkast
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id;
    IF v_hid IS NULL THEN
        RAISE EXCEPTION 'm17_avgjor_utkast: utkastet finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    -- ALT AVGJORT MED SAMME UTFALL er et stille ja.
    IF v_gammel = p_status THEN
        RETURN false;
    END IF;
    UPDATE public.svarutkast SET status = p_status
     WHERE tenant = p_tenant AND utkast_id = p_utkast_id
       AND status = 'foreslatt';
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RAISE EXCEPTION 'm17_avgjor_utkast: utkastet er alt avgjort som'
            ' %, og en avgjørelse går ikke om igjen', v_gammel
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM public.m17_evidens(
        p_tenant, v_hid, 'utkast.' || p_status, p_aktor,
        jsonb_build_object('utkast_id', p_utkast_id::text));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m17_avgjor_utkast(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

-- LUKKEDØREN. «Besvart» krever et utkast merket `brukt_manuelt` —
-- vakten i §2 er den bindende; døren sier det bare med en lesbar
-- setning først.
CREATE FUNCTION m17_lukk(
    p_tenant TEXT, p_henvendelse_id UUID, p_utfall TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT; v_lukket TIMESTAMPTZ;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_lukk');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_utfall IS NULL OR p_utfall NOT IN ('besvart', 'ikke_aktuell') THEN
        RAISE EXCEPTION 'm17_lukk: utfallet må være besvart eller'
            ' ikke_aktuell' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT lukket_ts INTO v_lukket FROM public.henvendelse
     WHERE tenant = p_tenant AND henvendelse_id = p_henvendelse_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm17_lukk: henvendelsen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_lukket IS NOT NULL THEN
        RETURN false;                              -- stille ja
    END IF;
    IF p_utfall = 'besvart'
       AND NOT EXISTS (SELECT 1 FROM public.svarutkast u
                        WHERE u.tenant = p_tenant
                          AND u.henvendelse_id = p_henvendelse_id
                          AND u.status = 'brukt_manuelt') THEN
        RAISE EXCEPTION 'm17_lukk: «besvart» krever et utkast merket'
            ' brukt_manuelt — uten det er det en påstand ingen kan'
            ' etterprøve' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.henvendelse
       SET lukket_ts = now(), lukket_av = p_aktor, lukket_utfall = p_utfall
     WHERE tenant = p_tenant AND henvendelse_id = p_henvendelse_id
       AND lukket_ts IS NULL;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        RETURN false;
    END IF;
    PERFORM public.m17_evidens(
        p_tenant, p_henvendelse_id, 'henvendelse.lukket', p_aktor,
        jsonb_build_object('utfall', p_utfall));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m17_lukk(TEXT, UUID, TEXT, TEXT) FROM PUBLIC;


-- ------------------------------------------------------------
-- 3b. Lesedørene.
-- ------------------------------------------------------------

-- SAMMENDRAGET TELLER ALT, listen viser de N eldste. Skillet er ikke
-- pynt (101s dom, ordrett): en flate som regnet totalen fra den
-- avkortede listen ville sagt «tre ubesvarte» når det var tre hundre.
CREATE FUNCTION m17_kostatus(p_tenant TEXT)
RETURNS TABLE(apne INT, uklassifiserte INT, i_unntakskoe INT,
              kritiske INT, apne_funn INT, lukkede_siste_30 INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_kostatus');
    SELECT count(*) FILTER (WHERE h.lukket_ts IS NULL)::int,
           count(*) FILTER (WHERE h.lukket_ts IS NULL AND NOT EXISTS (
               SELECT 1 FROM public.klassifisering k
                WHERE k.tenant = h.tenant
                  AND k.henvendelse_id = h.henvendelse_id))::int,
           count(*) FILTER (WHERE h.lukket_ts IS NULL
                              AND h.unntak_id IS NOT NULL)::int,
           count(*) FILTER (WHERE h.lukket_ts IS NULL AND EXISTS (
               SELECT 1 FROM public.klassifisering k
                WHERE k.tenant = h.tenant
                  AND k.henvendelse_id = h.henvendelse_id
                  AND k.prioritet = 'kritisk'))::int,
           0,
           count(*) FILTER (WHERE h.lukket_ts > now()
                                  - interval '30 days')::int
      INTO apne, uklassifiserte, i_unntakskoe, kritiske, apne_funn,
           lukkede_siste_30
      FROM public.henvendelse h WHERE h.tenant = p_tenant;
    SELECT count(*)::int INTO apne_funn FROM public.henvendelsesfunn f
     WHERE f.tenant = p_tenant AND f.apen;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m17_kostatus(TEXT) FROM PUBLIC;

-- KØEN. Eldst først blant de åpne — det er rekkefølgen et menneske
-- faktisk skal jobbe i, og flaten sorterer ikke om.
--
-- INNHOLDET FØLGER IKKE MED. Emne og kropp er kryptert og hentes av en
-- EGEN dør per henvendelse (`m17_hent_innhold`), aldri i listen. Et
-- listekall som dro med seg hver eneste kundetekst ville gjort ett
-- skjermbilde til en full eksport av persondata — og det er en helt
-- annen handling enn å se køen.
CREATE FUNCTION m17_koen(p_tenant TEXT, p_grense INT)
RETURNS TABLE(henvendelse_id UUID, kanal TEXT, ekstern_ref TEXT,
              mottatt TIMESTAMPTZ, avsender_hash TEXT, alder_dogn INT,
              prioritet TEXT, tema TEXT, handlingstype TEXT,
              klassifisert_av TEXT, i_unntakskoe BOOLEAN,
              antall_utkast INT, brukt_utkast BOOLEAN,
              apne_funn TEXT[])
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_koen');
    RETURN QUERY
    SELECT h.henvendelse_id, h.kanal, h.ekstern_ref, h.mottatt,
           h.avsender_hash,
           (current_date - h.mottatt::date)::int,
           k.prioritet, k.tema, k.handlingstype, k.kilde,
           h.unntak_id IS NOT NULL,
           (SELECT count(*)::int FROM public.svarutkast u
             WHERE u.tenant = h.tenant
               AND u.henvendelse_id = h.henvendelse_id),
           EXISTS (SELECT 1 FROM public.svarutkast u
                    WHERE u.tenant = h.tenant
                      AND u.henvendelse_id = h.henvendelse_id
                      AND u.status = 'brukt_manuelt'),
           coalesce((SELECT array_agg(f.funntype ORDER BY f.funntype)
                       FROM public.henvendelsesfunn f
                      WHERE f.tenant = h.tenant
                        AND f.henvendelse_id = h.henvendelse_id
                        AND f.apen), ARRAY[]::TEXT[])
      FROM public.henvendelse h
      LEFT JOIN public.klassifisering k
        ON k.tenant = h.tenant AND k.henvendelse_id = h.henvendelse_id
     WHERE h.tenant = p_tenant AND h.lukket_ts IS NULL
     -- Eldst først, `henvendelse_id` som tiebreaker: uten den ville to
     -- henvendelser mottatt samme sekund byttet plass mellom to kall, og
     -- en avkortet liste vist ulikt innhold på samme data (100s
     -- bitmap-lærdom).
     ORDER BY h.mottatt, h.henvendelse_id
     LIMIT greatest(least(coalesce(p_grense, 100), 1000), 1);
END $$;
REVOKE ALL ON FUNCTION m17_koen(TEXT, INT) FROM PUBLIC;

-- ÉN HENVENDELSES INNHOLD. Egen dør, ett kall per henvendelse, bak sitt
-- eget scope — å se KØEN og å lese hva kunden SKREV er to handlinger, og
-- bare den andre er persondata.
--
-- ET DOKUMENTERT GAP: LESINGEN SPORES IKKE. Døren skriver ingen
-- evidensrad, og det er ikke fordi den ikke burde. Å lese en kundes
-- tekst ER en persondatatilgang, og registeret vet i dag hvem som
-- SVARTE, ikke hvem som LESTE. Grunnen til at sporet mangler er
-- konkret: husets lesevei (`api/lesing.py::_les`) ruller ALLTID
-- transaksjonen tilbake, med en uttalt begrunnelse — «et leseendepunkt
-- som kan committe er ett refaktoreringsuhell unna å bli et skrivende».
-- En dør som skrev evidens ville derfor enten mistet raden ved
-- tilbakerullingen, eller tvunget GET-en gjennom skriverammen og dermed
-- undergravd nettopp den invarianten. Ingen av delene er en avgjørelse
-- en modul-PR skal ta alene.
--
-- Funksjonen er derfor `STABLE` og tar INGEN aktør: en parameter som
-- bare ble ignorert ville sett ut som et spor som fantes. Gapet lukkes
-- når huset får en registrert lesevei — og til da står det skrevet her
-- og i PR-en, ikke skjult.
--
-- Ciphertexten går ut som den er; dekrypteringen skjer i API-laget med
-- tenantens DEK (058/088-formen). Basen har aldri nøkkelen.
CREATE FUNCTION m17_hent_innhold(p_tenant TEXT, p_henvendelse_id UUID)
RETURNS TABLE(emne_kryptert BYTEA, nonce_emne BYTEA,
              kropp_kryptert BYTEA, nonce_kropp BYTEA, key_id TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_hent_innhold');
    IF NOT EXISTS (SELECT 1 FROM public.henvendelse h
                    WHERE h.tenant = p_tenant
                      AND h.henvendelse_id = p_henvendelse_id) THEN
        RAISE EXCEPTION 'm17_hent_innhold: henvendelsen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN QUERY
    SELECT h.emne_kryptert, h.nonce_emne, h.kropp_kryptert,
           h.nonce_kropp, h.key_id
      FROM public.henvendelse h
     WHERE h.tenant = p_tenant AND h.henvendelse_id = p_henvendelse_id;
END $$;
REVOKE ALL ON FUNCTION m17_hent_innhold(TEXT, UUID) FROM PUBLIC;

-- UTKASTENE for én henvendelse, nyest først.
CREATE FUNCTION m17_utkastene(p_tenant TEXT, p_henvendelse_id UUID)
RETURNS TABLE(utkast_id UUID, tekst_kryptert BYTEA, nonce BYTEA,
              key_id TEXT, kunnskapsref TEXT[], kilde TEXT,
              modell_digest TEXT, status TEXT, opprettet TIMESTAMPTZ,
              opprettet_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_utkastene');
    RETURN QUERY
    SELECT u.utkast_id, u.tekst_kryptert, u.nonce, u.key_id,
           u.kunnskapsref, u.kilde, u.modell_digest, u.status,
           u.opprettet, u.opprettet_av
      FROM public.svarutkast u
     WHERE u.tenant = p_tenant AND u.henvendelse_id = p_henvendelse_id
     ORDER BY u.opprettet DESC, u.utkast_id;
END $$;
REVOKE ALL ON FUNCTION m17_utkastene(TEXT, UUID) FROM PUBLIC;


-- ------------------------------------------------------------
-- 4. Sveipen. Kandidatene først, som en EGEN funksjon (100/101-formen):
--    sveipen kaller den tre ganger, og de tre må se NØYAKTIG det samme
--    settet.
-- ------------------------------------------------------------

-- `p_dogn_uklassifisert` og `p_dogn_ubesvart` er PARAMETRE med
-- forsvarlige standardsvar, ikke konstanter i kroppen. To døgn uten
-- klassifisering og fem uten svar er alminnelige servicemål; en tenant
-- med en annen SLA vil ha noe annet. At tallene kommer utenfra er det
-- som gjør den senere policyverdien til en endring i ett kall.
CREATE FUNCTION m17_funnkandidater(p_tenant TEXT, p_dag DATE,
                                   p_dogn_uklassifisert INT DEFAULT 2,
                                   p_dogn_ubesvart INT DEFAULT 5)
RETURNS TABLE(henvendelse_id UUID, funntype TEXT, dogn_over_grense INT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_ukl INT; v_ube INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_funnkandidater');
    v_ukl := greatest(coalesce(p_dogn_uklassifisert, 2), 1);
    v_ube := greatest(coalesce(p_dogn_ubesvart, 5), 1);
    RETURN QUERY
    -- 1. Åpne henvendelser uten klassifisering, eldre enn grensen.
    SELECT h.henvendelse_id, 'uklassifisert_over_grense'::text,
           ((p_dag - h.mottatt::date) - v_ukl)::int
      FROM public.henvendelse h
     WHERE h.tenant = p_tenant AND h.lukket_ts IS NULL
       AND (p_dag - h.mottatt::date) > v_ukl
       AND NOT EXISTS (SELECT 1 FROM public.klassifisering k
                        WHERE k.tenant = h.tenant
                          AND k.henvendelse_id = h.henvendelse_id)
    UNION ALL
    -- 2. Åpne henvendelser der klassifiseringen SIER at svar kreves, men
    --    ingen har svart. Kravet er klassifiseringens, ikke sveipens —
    --    en `til_info`-henvendelse skal ikke bli et funn fordi ingen
    --    svarte på noe som ikke ba om svar. Henvendelser som ALT står i
    --    unntakskøen utelates: de er ikke oversett, de er tildelt.
    SELECT h.henvendelse_id, 'ubesvart_over_grense'::text,
           ((p_dag - h.mottatt::date) - v_ube)::int
      FROM public.henvendelse h
      JOIN public.klassifisering k
        ON k.tenant = h.tenant AND k.henvendelse_id = h.henvendelse_id
     WHERE h.tenant = p_tenant AND h.lukket_ts IS NULL
       AND h.unntak_id IS NULL
       AND k.handlingstype = 'svar_kreves'
       AND (p_dag - h.mottatt::date) > v_ube
    UNION ALL
    -- 3. MISTENKELIG UTEN BEHANDLING. Egen funntype og INGEN
    --    aldersgrense: en henvendelse klassifisert som mistenkelig og
    --    ikke satt i sikkerhetskøen er et funn fra første sveip. Å vente
    --    to døgn på det ville vært å gi angriperen to døgn.
    SELECT h.henvendelse_id, 'mistenkelig_uten_behandling'::text,
           (p_dag - h.mottatt::date)::int
      FROM public.henvendelse h
      JOIN public.klassifisering k
        ON k.tenant = h.tenant AND k.henvendelse_id = h.henvendelse_id
     WHERE h.tenant = p_tenant AND h.lukket_ts IS NULL
       AND h.unntak_id IS NULL
       AND k.handlingstype = 'mistenkelig';
END $$;
REVOKE ALL ON FUNCTION m17_funnkandidater(TEXT, DATE, INT, INT)
    FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 4b. Sveipen selv. KRYSS-TENANT, egen LOGIN-rolle, egen timer og
--     nøyaktig ÉN rettighet i basen. Formen er 100/101s, ordrett.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_kundeservice_eier;

CREATE FUNCTION m17_sveip_henvendelser(p_grense INT DEFAULT 500,
                                       p_dogn_uklassifisert INT DEFAULT 2,
                                       p_dogn_ubesvart INT DEFAULT 5)
RETURNS TABLE(tenanter INT, nye INT, oppdaterte INT, lukkede INT,
              avkortet INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_n INT; v_grense INT;
        v_ukl INT; v_ube INT; v_dag DATE; v_naa TIMESTAMPTZ;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm17_sveip_henvendelser: sveipen er KRYSS-TENANT'
            ' og kjøres uten tenantkontekst — en kaller som har satt en'
            ' kontekst ber om noe annet enn det denne funksjonen gjør'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_grense := greatest(least(coalesce(p_grense, 500), 5000), 1);
    v_ukl := greatest(coalesce(p_dogn_uklassifisert, 2), 1);
    v_ube := greatest(coalesce(p_dogn_ubesvart, 5), 1);
    v_dag := current_date;
    v_naa := now();
    tenanter := 0; nye := 0; oppdaterte := 0; lukkede := 0; avkortet := 0;
    SELECT array_agg(DISTINCT h.tenant ORDER BY h.tenant) INTO v_tenanter
      FROM public.henvendelse h;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        tenanter := tenanter + 1;

        -- 1. Ferskheten på funn som ALT finnes. IDEMPOTENSEN BOR HER.
        UPDATE public.henvendelsesfunn f
           SET sist_sett_sveip = v_naa, apen = true, lukket_ts = NULL,
               dogn_over_grense = kand.dogn_over_grense
          FROM public.m17_funnkandidater(v_t, v_dag, v_ukl, v_ube) kand
         WHERE f.tenant = v_t
           AND f.henvendelse_id = kand.henvendelse_id
           AND f.funntype = kand.funntype;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        oppdaterte := oppdaterte + v_n;

        -- 2. De nye — med taket. `ORDER BY` setter det verste øverst:
        --    treffer sveipen taket sitt, er det de eldste henvendelsene
        --    som HAR fått funn.
        INSERT INTO public.henvendelsesfunn
            (tenant, henvendelse_id, funntype, dogn_over_grense,
             forst_sett, sist_sett_sveip, apen)
        SELECT v_t, kand.henvendelse_id, kand.funntype,
               kand.dogn_over_grense, v_naa, v_naa, true
          FROM public.m17_funnkandidater(v_t, v_dag, v_ukl, v_ube) kand
         WHERE NOT EXISTS (
                SELECT 1 FROM public.henvendelsesfunn f
                 WHERE f.tenant = v_t
                   AND f.henvendelse_id = kand.henvendelse_id
                   AND f.funntype = kand.funntype)
         ORDER BY coalesce(kand.dogn_over_grense, 0) DESC,
                  kand.henvendelse_id, kand.funntype
         LIMIT v_grense;
        GET DIAGNOSTICS v_n = ROW_COUNT;
        nye := nye + v_n;
        IF v_n = v_grense THEN
            avkortet := avkortet + 1;
        END IF;

        -- 3. Lukkingen. Et funn som ikke lenger gjelder — henvendelsen
        --    ble klassifisert, besvart, lukket eller satt i køen — lukkes.
        --    Raden består: at noe VAR et funn er også historikk.
        UPDATE public.henvendelsesfunn f
           SET apen = false, lukket_ts = v_naa
         WHERE f.tenant = v_t AND f.apen
           AND NOT EXISTS (
                SELECT 1
                  FROM public.m17_funnkandidater(v_t, v_dag, v_ukl,
                                                 v_ube) kand
                 WHERE kand.henvendelse_id = f.henvendelse_id
                   AND kand.funntype = f.funntype);
        GET DIAGNOSTICS v_n = ROW_COUNT;
        lukkede := lukkede + v_n;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m17_sveip_henvendelser(INT, INT, INT) FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 4c. Rollemønsteret i basen (043 §6b) speiler `ROLLE_TIL_SCOPES`
--     EKSAKT, og port 26 måler nettopp det.
--
--     `kundeservice:innhold` går til BÅDE `leser` og `admin`, og
--     det er en dom: den som svarer kunder MÅ kunne lese hva de skrev.
--     Scopet er likevel skilt ut fra `decisions:read` for at en tenant
--     som vil ha en rolle som ser KØEN uten å kunne lese INNHOLDET, skal
--     kunne lage den uten skjemaendring — og fordi de to er ulike
--     handlinger mot ulike data.
-- ------------------------------------------------------------
INSERT INTO rolle_scope (rolle, scope) VALUES
    ('leser', 'kundeservice:innhold'),
    -- `sikkerhet` er alltid en SUPERMENGDE av `leser` — og saklig: en
    -- `mistenkelig` henvendelse blir en sikkerhetssak i M-37s kø, og den
    -- som behandler den må kunne lese hva som sto der.
    ('sikkerhet', 'kundeservice:innhold'),
    ('admin', 'kundeservice:innhold')
    ON CONFLICT DO NOTHING;


-- ------------------------------------------------------------
-- 5. Rettighetene. Migrasjonen NAVNGIR IKKE runtime-rollen (057-
--    lærdommen); `deploy/staging/migrer.py` er autoritativ.
--
--    MERK HVA SOM IKKE GRANTES: `m17_sveip_henvendelser` (kryss-tenant,
--    sveiperollens) og `m17_funnkandidater` (internt ledd).
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_kundeservice_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_kostatus(TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_koen(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m17_hent_innhold(TEXT, UUID) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_utkastene(TEXT, UUID)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_ta_imot(TEXT, UUID, TEXT,'
            ' TEXT, TIMESTAMPTZ, TEXT, BYTEA, BYTEA, BYTEA, BYTEA, TEXT,'
            ' TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_klassifiser(TEXT, UUID,'
            ' TEXT, TEXT, TEXT, TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_til_unntakskoe(TEXT, UUID,'
            ' TEXT, BYTEA, BYTEA, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_lagre_utkast(TEXT, UUID,'
            ' UUID, BYTEA, BYTEA, TEXT, TEXT[], TEXT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_avgjor_utkast(TEXT, UUID,'
            ' TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_lukk(TEXT, UUID, TEXT,'
            ' TEXT) TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_henvendelsessveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m17_sveip_henvendelser(INT, INT, INT)'
            ' TO disponit_henvendelsessveip';
    END IF;
END $$;
RESET ROLE;
