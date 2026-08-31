-- 088: M-6 e-postoperasjonsagenten — datamodellen + e-postdatagrensen
-- (TTL). PR-A av M-6-planen (dommene 31/8): KUN lagrene og reaperen —
-- ingen OAuth-dør (PR-B), ingen innhenter/modellvei (PR-C), ingen flate
-- (PR-D).
--
-- Formene er 057/058 sine, med vilje og ordrett der de passer:
--
--   * payload-lagrene bærer tenant-DEK-kryptert innhold på 058-formen
--     (<felt>_kryptert BYTEA + nonce + key_id med FK mot `tenant_nokler`
--     — 058s `inndata_dek_fk`), og CHECK-en binder payload til
--     `slettet_ts` BEGGE veier (057s `payload_folger_slettet`);
--   * slettefristen er kundevalgt 30–365 døgn (standard 90), immutabel
--     etter INSERT (057 port 20), og løper fra `mottatt_ts` — meldingen
--     er sitt eget frist-anker, det finnes ingen prosess å lukke;
--   * reaperen (`reap_epostdata`) er 057s `reap_kandidatdata`-form:
--     kryss-tenant, innelukket autoritet, én melding om gangen med
--     RADENS tenant i konteksten, SKIP LOCKED, ALLE payload-lagre i
--     SAMME transaksjon (kropp, sammendrag, utkast-tekst, vedleggsnavn)
--     — aldri ett lager alene;
--   * «aldri ett lager alene» håndheves også mot direkte DML av ÉN
--     UTSATT constraint-trigger på ANKERET (meldingen) ved COMMIT,
--     armert av billige per-rad-markører på barnelagrene — 076/#163-
--     formen, IKKE 057s per-rad-form: invariantens nivå og krokens
--     nivå skal være det samme, og `test_163_samletporten_staar_pa_-
--     ankeret_ikke_per_rad` feller per-rad-klassen basevidt.
--
-- Hva som BESTÅR etter reaping: rad-ID-er, tidsstempler, hasher
-- (avsender/emne/leverandør), retning, prioritet/handlingstype,
-- modell-digest og `slettet_ts` — minimal revisjonsevidens, som i 057.
-- `epost_oppfolging` er ren metadata (tråd-referanse, type, frister) og
-- består reaping i sin helhet.
--
-- Eierskap: tabellene og vaktene eies av migrator; reaperen og den
-- utsatte porten eies av `disponit_m37_claimer` (057-formen), og ALLE
-- rettighetsendringer på claimer-eide funksjoner står INNE i samme
-- SET LOCAL ROLE-blokk (PUBLIC-EXECUTE-klassen fra #140). Runtime-
-- grantene bor i `migrer.py` på `{rolle}`-form — migrasjonen navngir
-- ALDRI runtime-rollen ved lokalnavn utenfor den betingede
-- reaperblokken (056/057-læren).

-- ------------------------------------------------------------
-- 1. Kilden: én tilkoblet postboks per rad. Leverandørsettet er LUKKET
-- (dommen pkt. 1: M365 først; Gmail er en senere CHECK-utvidelse i egen
-- migrasjon — en ny leverandør er en kontraktsendring, aldri et
-- kallargument, 058s eiermodul-lærdom).
--
-- `auth_kryptert` er refresh-tokenet, kryptert med tenant-DEK
-- (058-formen). Det er IKKE under slettefristen: credentials er ikke
-- meldingspayload, de reapes ikke — de roteres/destrueres med kilden
-- (deaktivering, PR-B). `delta_token` er Graphs delta-cursor: en
-- leverandørintern peker, ikke persondata, og står derfor ukryptert.
CREATE TABLE epost_kilde (
    tenant TEXT NOT NULL,
    kilde_id UUID NOT NULL DEFAULT gen_random_uuid(),
    leverandor TEXT NOT NULL
        CONSTRAINT kilde_leverandor_lukket CHECK (leverandor IN ('m365')),
    postboks TEXT NOT NULL
        CONSTRAINT kilde_postboks_ikke_tom
        CHECK (length(btrim(postboks)) > 0),
    auth_kryptert BYTEA NOT NULL,
    nonce BYTEA NOT NULL,
    key_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'aktiv'
        CONSTRAINT kilde_status_lukket
        CHECK (status IN ('aktiv', 'feilet', 'deaktivert')),
    sist_hentet_ts TIMESTAMPTZ,
    delta_token TEXT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT epost_kilde_pk PRIMARY KEY (tenant, kilde_id),
    -- Én levende tilkobling per postboks: to kilder mot samme boks er
    -- to innhentere som dublerer hverandres meldinger — idempotensen i
    -- §2 hviler på (kilde, leverandor_melding_id), og den er verdiløs
    -- om samme boks kan ha to kilde-id-er.
    CONSTRAINT kilde_en_per_postboks UNIQUE (tenant, leverandor, postboks),
    -- DEK-referansen bindes som i 003/005/007/011/016/058: en ukjent
    -- eller krysstenant nøkkel-id ville sett komplett ut helt til
    -- dekrypteringen feiler hos alle.
    CONSTRAINT kilde_dek_fk FOREIGN KEY (tenant, key_id)
        REFERENCES tenant_nokler (tenant, key_id),
    -- Kryptostrukturen på TABELLEN (016/017/058-formen): 12-byte nonce
    -- fra db/kryptering.py, som overalt ellers.
    CONSTRAINT kilde_krypto_struktur CHECK (octet_length(nonce) = 12)
);

-- Kildevakten: identiteten er immutabel; det som lovlig endres er
-- driftstilstanden (status), hentemerkene (sist_hentet_ts, delta_token)
-- og credentials-trioen (token-refresh, PR-B). DELETE finnes ikke —
-- `deaktivert` er avviklingsformen, og raden består som evidens for at
-- tilkoblingen fantes.
CREATE OR REPLACE FUNCTION epost_kilde_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'epost_kilde: % avvist — en kilde avvikles ved'
            ' status deaktivert, raden består som evidens', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.kilde_id IS DISTINCT FROM OLD.kilde_id
       OR NEW.leverandor IS DISTINCT FROM OLD.leverandor
       OR NEW.postboks IS DISTINCT FROM OLD.postboks
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'epost_kilde: identitetskolonnene er immutable —'
            ' en annen postboks er en NY kilde'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS epost_kilde_vakt ON epost_kilde;
CREATE TRIGGER epost_kilde_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON epost_kilde
    FOR EACH ROW EXECUTE FUNCTION epost_kilde_vakt();
DROP TRIGGER IF EXISTS epost_kilde_ingen_truncate ON epost_kilde;
CREATE TRIGGER epost_kilde_ingen_truncate
    BEFORE TRUNCATE ON epost_kilde
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- ------------------------------------------------------------
-- 2. Meldingen: frist-ankeret OG det første payload-lageret i ett.
-- Innhentingsidempotensen er UNIQUE-en på (tenant, kilde_id,
-- leverandor_melding_id): et gjensyn med samme leverandørmelding er
-- samme rad (ON CONFLICT DO NOTHING i innhenteren, PR-C), aldri en
-- dublett.
--
-- `avsender_hash`/`emne_hash` er den minimale revisjonsevidensen som
-- består etter reaping (057s `innhold_sha256`-rolle) — hasher, aldri
-- klartekst: avsender og emne er persondata, og de ligger KUN i den
-- krypterte kroppen. Hashene skrives av innhenteren (PR-C) og kan ikke
-- utledes av basen — den ser aldri klarteksten; det er formens pris,
-- sagt høyt (samme ærlighet som 058 har for leverandørverdier).
CREATE TABLE epost_melding (
    tenant TEXT NOT NULL,
    melding_id UUID NOT NULL DEFAULT gen_random_uuid(),
    kilde_id UUID NOT NULL,
    leverandor_melding_id TEXT NOT NULL
        CONSTRAINT melding_leverandor_id_ikke_tom
        CHECK (length(btrim(leverandor_melding_id)) > 0),
    trad_id TEXT,
    mottatt_ts TIMESTAMPTZ NOT NULL,
    retning TEXT NOT NULL
        CONSTRAINT melding_retning_lukket CHECK (retning IN ('inn', 'ut')),
    avsender_hash TEXT NOT NULL,
    emne_hash TEXT NOT NULL,
    kropp_kryptert BYTEA,
    nonce BYTEA,
    key_id TEXT,
    har_vedlegg BOOLEAN NOT NULL DEFAULT false,
    -- 057 port 20, ordrett: kundevalgt spenn, immutabel etter INSERT
    -- (radvakten under). Fristen løper fra `mottatt_ts`.
    slettefrist_dogn INT NOT NULL DEFAULT 90
        CONSTRAINT melding_frist_i_spennet
        CHECK (slettefrist_dogn BETWEEN 30 AND 365),
    slettet_ts TIMESTAMPTZ,
    CONSTRAINT epost_melding_pk PRIMARY KEY (tenant, melding_id),
    CONSTRAINT melding_kilde_fk FOREIGN KEY (tenant, kilde_id)
        REFERENCES epost_kilde (tenant, kilde_id),
    -- Innhentingsidempotensen (§2 / port 1).
    CONSTRAINT melding_en_per_leverandormelding
        UNIQUE (tenant, kilde_id, leverandor_melding_id),
    CONSTRAINT melding_dek_fk FOREIGN KEY (tenant, key_id)
        REFERENCES tenant_nokler (tenant, key_id),
    CONSTRAINT melding_krypto_struktur
        CHECK (nonce IS NULL OR octet_length(nonce) = 12),
    -- 057-formen: en levende rad HAR payload, en reapet rad HAR IKKE —
    -- begge veier, så en «gravstein» eller en halvtømt rad ikke kan
    -- uttrykkes.
    CONSTRAINT melding_payload_folger_slettet
        CHECK ((slettet_ts IS NULL AND kropp_kryptert IS NOT NULL
                AND nonce IS NOT NULL AND key_id IS NOT NULL)
            OR (slettet_ts IS NOT NULL AND kropp_kryptert IS NULL
                AND nonce IS NULL AND key_id IS NULL))
);

-- Reap-timerens utvalg (057s indeksform): partial over de UREAPEDE,
-- uttrykket er den enden fristen løper fra, så samme indeks gir både
-- utvalget og ORDER BY-en. Selve fristen (`mottatt_ts +
-- slettefrist_dogn * interval '1 day'`) kan ikke indekseres
-- (`timestamptz + interval` er STABLE) og står som radfilter — men bare
-- over halen, i frist-rekkefølge.
CREATE INDEX epost_melding_ureapet_frist
    ON epost_melding (mottatt_ts)
    WHERE slettet_ts IS NULL;

-- Meldingsvakten: fødsel LEVENDE, frist immutabel, eneste lovlige
-- UPDATE er reap-overgangen, og reap-merket er en KONKLUSJON om at
-- barnelagrene alt er tømt (057s ankervakt-form) — DELETE/TRUNCATE
-- aldri.
CREATE OR REPLACE FUNCTION epost_melding_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        -- En melding fødes LEVENDE — reap-merket settes bare av
        -- reap-overgangen (057s gravstein-port: en fødsel med satt
        -- `slettet_ts` ville brent idempotensnøkkelen for alltid).
        IF NEW.slettet_ts IS NOT NULL THEN
            RAISE EXCEPTION 'epost_melding: en melding fødes LEVENDE —'
                ' slettet_ts settes bare av reap-overgangen'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- `mottatt_ts` er enden fristen løper fra og immutabel etterpå.
        -- En mottakstid frem i tid ville skjøvet utløpet stille —
        -- nøyaktig forlengelsen port 20 nekter, gjennom den andre
        -- kolonnen (057s `opprettet`-port, ordrett).
        IF NEW.mottatt_ts > pg_catalog.now() THEN
            RAISE EXCEPTION 'epost_melding: mottatt_ts kan ikke stå frem'
                ' i tid — det ville forlenget slettefristen'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'epost_melding: % avvist — meldinger reapes'
            ' (payload til NULL), de slettes aldri som rader', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- PER-KOLONNE-FORM (057-ANKERETS, ikke lager-jsonb-formen): en
    -- UPDATE som ikke endrer noe skal SLIPPE GJENNOM — det er
    -- markørens no-op-armering av den utsatte porten (076/#163-formen
    -- under), og radvaktens DISTINCT-porter skal være stille for den.
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.melding_id IS DISTINCT FROM OLD.melding_id
       OR NEW.kilde_id IS DISTINCT FROM OLD.kilde_id
       OR NEW.leverandor_melding_id
           IS DISTINCT FROM OLD.leverandor_melding_id
       OR NEW.trad_id IS DISTINCT FROM OLD.trad_id
       OR NEW.mottatt_ts IS DISTINCT FROM OLD.mottatt_ts
       OR NEW.retning IS DISTINCT FROM OLD.retning
       OR NEW.avsender_hash IS DISTINCT FROM OLD.avsender_hash
       OR NEW.emne_hash IS DISTINCT FROM OLD.emne_hash
       OR NEW.har_vedlegg IS DISTINCT FROM OLD.har_vedlegg THEN
        RAISE EXCEPTION 'epost_melding: identitets- og evidenskolonnene'
            ' er immutable' USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Port 20, selve kjernen (057 ordrett): INGEN overgang endrer
    -- fristen. Ikke modulen, ikke runtime, ikke eieren (dommen pkt. 3).
    IF NEW.slettefrist_dogn IS DISTINCT FROM OLD.slettefrist_dogn THEN
        RAISE EXCEPTION 'epost_melding: slettefristen er satt ved'
            ' innhentingen og kan ikke endres (M-6-dommen pkt. 3)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.slettet_ts IS DISTINCT FROM OLD.slettet_ts THEN
        IF OLD.slettet_ts IS NOT NULL THEN
            RAISE EXCEPTION 'epost_melding: slettet_ts er alt satt'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- Reap-overgangen: payloaden BLIR NULL i samme skriv
        -- (payload_folger_slettet binder de to — armen her gir det
        -- lesbare utfallet).
        IF NEW.kropp_kryptert IS NOT NULL OR NEW.nonce IS NOT NULL
           OR NEW.key_id IS NOT NULL THEN
            RAISE EXCEPTION 'epost_melding: reaping krever at payloaden'
                ' (kropp_kryptert, nonce, key_id) blir NULL'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    ELSIF NEW.kropp_kryptert IS DISTINCT FROM OLD.kropp_kryptert
          OR NEW.nonce IS DISTINCT FROM OLD.nonce
          OR NEW.key_id IS DISTINCT FROM OLD.key_id THEN
        RAISE EXCEPTION 'epost_melding: payloaden endres bare av'
            ' reap-overgangen' USING ERRCODE = 'insufficient_privilege';
    ELSE
        RETURN NEW;   -- ingen endring (markørens armering) — stille
    END IF;
    -- REAP-MERKET ER EN KONKLUSJON, IKKE EN PÅSTAND (057s ankervakt):
    -- reaperen velger bare meldinger med `slettet_ts IS NULL`, så et
    -- merke satt mens et barnelager fortsatt bærer payload utelukker
    -- meldingen fra reaping for alltid. Reaperen tømmer barna FØR den
    -- merker meldingen, i samme transaksjon, så den lovlige veien er
    -- uendret.
    IF EXISTS (SELECT 1 FROM public.epost_klassifisering k
                WHERE k.tenant = NEW.tenant
                  AND k.melding_id = NEW.melding_id
                  AND k.slettet_ts IS NULL)
       OR EXISTS (SELECT 1 FROM public.epost_utkast u
                   WHERE u.tenant = NEW.tenant
                     AND u.melding_id = NEW.melding_id
                     AND u.slettet_ts IS NULL)
       OR EXISTS (SELECT 1 FROM public.epost_vedlegg v
                   WHERE v.tenant = NEW.tenant
                     AND v.melding_id = NEW.melding_id
                     AND v.slettet_ts IS NULL) THEN
        RAISE EXCEPTION 'epost_melding: % hos % kan ikke merkes reapet'
            ' mens et barnelager fortsatt bærer payload — merket ville'
            ' utelukket meldingen fra reaperen for alltid',
            NEW.melding_id, NEW.tenant
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS epost_melding_vakt ON epost_melding;
CREATE TRIGGER epost_melding_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON epost_melding
    FOR EACH ROW EXECUTE FUNCTION epost_melding_vakt();
DROP TRIGGER IF EXISTS epost_melding_ingen_truncate ON epost_melding;
CREATE TRIGGER epost_melding_ingen_truncate
    BEFORE TRUNCATE ON epost_melding
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- ------------------------------------------------------------
-- 3. Barnelagrene: klassifisering (1:1), utkast (append-only tekst) og
-- vedlegg. Felles 057-form: payload nullable, CHECK binder den til
-- `slettet_ts` begge veier, evidensen (enums, digester, hasher,
-- størrelser) består.

CREATE TABLE epost_klassifisering (
    tenant TEXT NOT NULL,
    melding_id UUID NOT NULL,
    prioritet TEXT NOT NULL
        CONSTRAINT klassifisering_prioritet_lukket
        CHECK (prioritet IN ('kritisk', 'hoy', 'normal', 'lav')),
    tema TEXT,
    handlingstype TEXT NOT NULL
        CONSTRAINT klassifisering_handlingstype_lukket
        CHECK (handlingstype IN ('svar_kreves', 'til_info', 'oppgave',
                                 'mote', 'nyhetsbrev', 'mistenkelig')),
    sammendrag_kryptert BYTEA,
    nonce BYTEA,
    key_id TEXT,
    -- Hvilken modell som klassifiserte — bindingen biasmålingene og
    -- den daglige statusen (PR-C/D) attesterer mot (m57s
    -- digest-disiplin).
    modell_digest TEXT NOT NULL
        CONSTRAINT klassifisering_digest_ikke_tom
        CHECK (length(btrim(modell_digest)) > 0),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    slettet_ts TIMESTAMPTZ,
    CONSTRAINT epost_klassifisering_pk PRIMARY KEY (tenant, melding_id),
    CONSTRAINT klassifisering_melding_fk FOREIGN KEY (tenant, melding_id)
        REFERENCES epost_melding (tenant, melding_id),
    CONSTRAINT klassifisering_dek_fk FOREIGN KEY (tenant, key_id)
        REFERENCES tenant_nokler (tenant, key_id),
    CONSTRAINT klassifisering_krypto_struktur
        CHECK (nonce IS NULL OR octet_length(nonce) = 12),
    CONSTRAINT klassifisering_payload_folger_slettet
        CHECK ((slettet_ts IS NULL AND sammendrag_kryptert IS NOT NULL
                AND nonce IS NOT NULL AND key_id IS NOT NULL)
            OR (slettet_ts IS NOT NULL AND sammendrag_kryptert IS NULL
                AND nonce IS NULL AND key_id IS NULL))
);

CREATE TABLE epost_utkast (
    tenant TEXT NOT NULL,
    utkast_id UUID NOT NULL DEFAULT gen_random_uuid(),
    melding_id UUID NOT NULL,
    tekst_kryptert BYTEA,
    nonce BYTEA,
    key_id TEXT,
    -- Dommen pkt. 4: utkast finnes KUN i Disponit-flaten — ingen
    -- Drafts-skriving i v1. Statusmaskinen er flatens: foreslått →
    -- forkastet | brukt_manuelt. Teksten er APPEND-ONLY: regenerering
    -- er en NY rad, aldri en UPDATE av teksten (vakten under).
    status TEXT NOT NULL DEFAULT 'foreslatt'
        CONSTRAINT utkast_status_lukket
        CHECK (status IN ('foreslatt', 'forkastet', 'brukt_manuelt')),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    slettet_ts TIMESTAMPTZ,
    CONSTRAINT epost_utkast_pk PRIMARY KEY (tenant, utkast_id),
    CONSTRAINT utkast_melding_fk FOREIGN KEY (tenant, melding_id)
        REFERENCES epost_melding (tenant, melding_id),
    CONSTRAINT utkast_dek_fk FOREIGN KEY (tenant, key_id)
        REFERENCES tenant_nokler (tenant, key_id),
    CONSTRAINT utkast_krypto_struktur
        CHECK (nonce IS NULL OR octet_length(nonce) = 12),
    CONSTRAINT utkast_payload_folger_slettet
        CHECK ((slettet_ts IS NULL AND tekst_kryptert IS NOT NULL
                AND nonce IS NOT NULL AND key_id IS NOT NULL)
            OR (slettet_ts IS NOT NULL AND tekst_kryptert IS NULL
                AND nonce IS NULL AND key_id IS NULL))
);

CREATE TABLE epost_vedlegg (
    tenant TEXT NOT NULL,
    vedlegg_id UUID NOT NULL DEFAULT gen_random_uuid(),
    melding_id UUID NOT NULL,
    -- Filnavnet er persondata så godt som noe
    -- (fornavn.etternavn-cv.pdf, 057s egen formulering) og er payload.
    navn_kryptert BYTEA,
    nonce BYTEA,
    key_id TEXT,
    innholdstype TEXT,
    storrelse_bytes BIGINT
        CONSTRAINT vedlegg_storrelse_ikke_negativ
        CHECK (storrelse_bytes IS NULL OR storrelse_bytes >= 0),
    leverandor_hash TEXT,
    -- v1 henter aldri vedleggsinnhold (kun metadata fra Graph);
    -- settet utvides ved migrasjon i v1.1 — lukket til da.
    skannstatus TEXT NOT NULL DEFAULT 'ikke_hentet'
        CONSTRAINT vedlegg_skannstatus_lukket
        CHECK (skannstatus IN ('ikke_hentet')),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    slettet_ts TIMESTAMPTZ,
    CONSTRAINT epost_vedlegg_pk PRIMARY KEY (tenant, vedlegg_id),
    CONSTRAINT vedlegg_melding_fk FOREIGN KEY (tenant, melding_id)
        REFERENCES epost_melding (tenant, melding_id),
    CONSTRAINT vedlegg_dek_fk FOREIGN KEY (tenant, key_id)
        REFERENCES tenant_nokler (tenant, key_id),
    CONSTRAINT vedlegg_krypto_struktur
        CHECK (nonce IS NULL OR octet_length(nonce) = 12),
    CONSTRAINT vedlegg_payload_folger_slettet
        CHECK ((slettet_ts IS NULL AND navn_kryptert IS NOT NULL
                AND nonce IS NOT NULL AND key_id IS NOT NULL)
            OR (slettet_ts IS NOT NULL AND navn_kryptert IS NULL
                AND nonce IS NULL AND key_id IS NULL))
);

-- Meldingsoppslagene (reaperen, samlet-porten, flaten i PR-D) går på
-- (tenant, melding_id) i begge de flerbarns-lagrene — klassifiseringen
-- har paret som PK, disse to ikke, og uten indeks er hvert oppslag et
-- fullt skann som vokser med all historisk bruk (CodeRabbit på PR-A;
-- 057s egen indekslærdom).
CREATE INDEX epost_utkast_melding
    ON epost_utkast (tenant, melding_id);
CREATE INDEX epost_vedlegg_melding
    ON epost_vedlegg (tenant, melding_id);

-- ------------------------------------------------------------
-- 4. Lagervakten for klassifisering og vedlegg — 057s
-- `m57_kandidatlager_vakt`-form: eneste lovlige UPDATE er
-- reap-overgangen (payloadkolonnene står som trigger-argumenter),
-- INSERT bare på en LEVENDE melding, DELETE/TRUNCATE aldri.
--
-- SECURITY DEFINER av 057s grunn, ordrett: INSERT-porten må LÅSE
-- meldingsraden (`FOR SHARE` konflikter med reaperens `FOR UPDATE`, så
-- en forsinket skriver venter og leser radens NYE versjon i stedet for
-- et gammelt snapshot), og en radlåsende SELECT krever UPDATE-rettighet
-- på tabellen — som runtime med vilje ikke har. Definer er migrator,
-- som eier tabellene; FORCE RLS gjelder også eieren, så en melding
-- vakten ikke SER (ingen/feil tenantkontekst) er en avvisning, aldri
-- et fripass (057s «INGEN RAD er like rødt som en reapet rad»).
CREATE OR REPLACE FUNCTION m6_epostlager_vakt()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE nj jsonb; oj jsonb; kol TEXT; v_slettet TIMESTAMPTZ;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT m.slettet_ts INTO v_slettet
          FROM public.epost_melding m
         WHERE m.tenant = NEW.tenant AND m.melding_id = NEW.melding_id
         FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION '%: meldingen er ikke synlig for vakten —'
                ' e-postpayload skrives bare under en melding vakten kan'
                ' lese og låse', TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF v_slettet IS NOT NULL THEN
            RAISE EXCEPTION '%: meldingen er reapet — payload skrives'
                ' ikke tilbake til en slettet melding', TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.slettet_ts IS NOT NULL THEN
            RAISE EXCEPTION '%: en rad fødes LEVENDE — reap-merket'
                ' settes bare av reap-overgangen', TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- `opprettet` er basens klokke, ikke skriverens (057s Codex
        -- P2-port): kolonnen består etter reaping som revisjonsevidens
        -- og skal ikke være kallerens påstand.
        NEW.opprettet := pg_catalog.now();
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION '%: % avvist — rader reapes (payload til NULL),'
            ' de slettes aldri', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.slettet_ts IS NOT NULL THEN
        RAISE EXCEPTION '%: raden er alt reapet og immutabel',
            TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
    END IF;
    nj := to_jsonb(NEW); oj := to_jsonb(OLD);
    IF nj->>'slettet_ts' IS NULL THEN
        RAISE EXCEPTION '%: eneste lovlige UPDATE er reap-overgangen'
            ' (slettet_ts settes, payload til NULL)', TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    FOREACH kol IN ARRAY TG_ARGV LOOP
        IF (nj->kol) IS DISTINCT FROM 'null'::jsonb THEN
            RAISE EXCEPTION '%: reaping krever at payloadkolonnen % blir'
                ' NULL', TG_TABLE_NAME, kol
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        nj := nj - kol; oj := oj - kol;
    END LOOP;
    nj := nj - 'slettet_ts'; oj := oj - 'slettet_ts';
    IF nj IS DISTINCT FROM oj THEN
        RAISE EXCEPTION '%: bare payload og slettet_ts endres ved reaping'
            ' — resten av raden er revisjonsevidens', TG_TABLE_NAME
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

DO $$
DECLARE par RECORD;
BEGIN
    FOR par IN SELECT * FROM (VALUES
        ('epost_klassifisering',
         ARRAY['sammendrag_kryptert', 'nonce', 'key_id']),
        ('epost_vedlegg', ARRAY['navn_kryptert', 'nonce', 'key_id'])
    ) AS v(tab, payload) LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS %I ON %I',
            par.tab || '_vakt', par.tab);
        EXECUTE format(
            'CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE ON %I'
            ' FOR EACH ROW EXECUTE FUNCTION m6_epostlager_vakt(%s)',
            par.tab || '_vakt', par.tab,
            (SELECT string_agg(quote_literal(k), ', ')
               FROM unnest(par.payload) AS k));
        EXECUTE format(
            'DROP TRIGGER IF EXISTS %I ON %I',
            par.tab || '_ingen_truncate', par.tab);
        EXECUTE format(
            'CREATE TRIGGER %I BEFORE TRUNCATE ON %I'
            ' FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring()',
            par.tab || '_ingen_truncate', par.tab);
    END LOOP;
END $$;

-- Utkastet har sin EGEN vakt: samme INSERT-port og samme reap-overgang
-- som den generiske, pluss NØYAKTIG ÉN lovlig tilstandsovergang til —
-- flatens dom over utkastet (foreslått → forkastet | brukt_manuelt),
-- med alt annet uendret. Teksten kan aldri endres: regenerering er en
-- ny rad (append-only-vakten planen krever).
CREATE OR REPLACE FUNCTION epost_utkast_vakt()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE nj jsonb; oj jsonb; kol TEXT; v_slettet TIMESTAMPTZ;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT m.slettet_ts INTO v_slettet
          FROM public.epost_melding m
         WHERE m.tenant = NEW.tenant AND m.melding_id = NEW.melding_id
         FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'epost_utkast: meldingen er ikke synlig for'
                ' vakten — utkast skrives bare under en melding vakten'
                ' kan lese og låse'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF v_slettet IS NOT NULL THEN
            RAISE EXCEPTION 'epost_utkast: meldingen er reapet — utkast'
                ' skrives ikke til en slettet melding'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.slettet_ts IS NOT NULL THEN
            RAISE EXCEPTION 'epost_utkast: et utkast fødes LEVENDE —'
                ' reap-merket settes bare av reap-overgangen'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- ... og som FORESLÅTT: dommen over utkastet er flatens egen,
        -- målte overgang, aldri en fødselsverdi.
        IF NEW.status <> 'foreslatt' THEN
            RAISE EXCEPTION 'epost_utkast: et utkast fødes foreslått —'
                ' forkastet/brukt_manuelt er flatens egne overganger'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        NEW.opprettet := pg_catalog.now();
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'epost_utkast: % avvist — utkast reapes, de'
            ' slettes aldri', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.slettet_ts IS NOT NULL THEN
        RAISE EXCEPTION 'epost_utkast: raden er alt reapet og immutabel'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    nj := to_jsonb(NEW); oj := to_jsonb(OLD);
    IF nj->>'slettet_ts' IS NOT NULL THEN
        -- Reap-overgangen, 057-formen.
        FOREACH kol IN ARRAY ARRAY['tekst_kryptert', 'nonce',
                                   'key_id'] LOOP
            IF (nj->kol) IS DISTINCT FROM 'null'::jsonb THEN
                RAISE EXCEPTION 'epost_utkast: reaping krever at'
                    ' payloadkolonnen % blir NULL', kol
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            nj := nj - kol; oj := oj - kol;
        END LOOP;
        nj := nj - 'slettet_ts'; oj := oj - 'slettet_ts';
        IF nj IS DISTINCT FROM oj THEN
            RAISE EXCEPTION 'epost_utkast: bare payload og slettet_ts'
                ' endres ved reaping — resten er revisjonsevidens'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    -- Tilstandsovergangen: foreslått → forkastet | brukt_manuelt, alt
    -- annet uendret. Teksten er append-only: en «rettet» tekst er en
    -- NY rad, aldri denne.
    IF NOT (OLD.status = 'foreslatt'
            AND NEW.status IN ('forkastet', 'brukt_manuelt')) THEN
        RAISE EXCEPTION 'epost_utkast: overgang % -> % finnes ikke —'
            ' teksten er append-only, dommen felles én gang',
            OLD.status, NEW.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    nj := nj - 'status'; oj := oj - 'status';
    IF nj IS DISTINCT FROM oj THEN
        RAISE EXCEPTION 'epost_utkast: bare status endres i flatens'
            ' overgang — teksten og resten av raden er immutable'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS epost_utkast_vakt ON epost_utkast;
CREATE TRIGGER epost_utkast_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON epost_utkast
    FOR EACH ROW EXECUTE FUNCTION epost_utkast_vakt();
DROP TRIGGER IF EXISTS epost_utkast_ingen_truncate ON epost_utkast;
CREATE TRIGGER epost_utkast_ingen_truncate
    BEFORE TRUNCATE ON epost_utkast
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- ------------------------------------------------------------
-- 5. Oppfølgingen: ren metadata (tråd-referanse, type, frister) —
-- BESTÅR reaping (§2). Lukking settes én gang; DELETE/TRUNCATE aldri.
CREATE TABLE epost_oppfolging (
    tenant TEXT NOT NULL,
    oppfolging_id UUID NOT NULL DEFAULT gen_random_uuid(),
    trad_ref TEXT NOT NULL
        CONSTRAINT oppfolging_trad_ikke_tom
        CHECK (length(btrim(trad_ref)) > 0),
    type TEXT NOT NULL
        CONSTRAINT oppfolging_type_lukket
        CHECK (type IN ('ubesvart', 'arkivklar')),
    frist_ts TIMESTAMPTZ NOT NULL,
    lukket_ts TIMESTAMPTZ,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT epost_oppfolging_pk PRIMARY KEY (tenant, oppfolging_id)
);

CREATE OR REPLACE FUNCTION epost_oppfolging_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.lukket_ts IS NOT NULL THEN
            RAISE EXCEPTION 'epost_oppfolging: en oppfølging fødes ÅPEN'
                ' — lukkingen er sin egen, målte overgang'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'epost_oppfolging: % avvist — oppfølginger'
            ' lukkes, de slettes aldri', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.oppfolging_id IS DISTINCT FROM OLD.oppfolging_id
       OR NEW.trad_ref IS DISTINCT FROM OLD.trad_ref
       OR NEW.type IS DISTINCT FROM OLD.type
       OR NEW.frist_ts IS DISTINCT FROM OLD.frist_ts
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'epost_oppfolging: bare lukket_ts endres —'
            ' resten av raden er immutabel'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.lukket_ts IS DISTINCT FROM OLD.lukket_ts
       AND OLD.lukket_ts IS NOT NULL THEN
        RAISE EXCEPTION 'epost_oppfolging: lukket_ts er alt satt'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS epost_oppfolging_vakt ON epost_oppfolging;
CREATE TRIGGER epost_oppfolging_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON epost_oppfolging
    FOR EACH ROW EXECUTE FUNCTION epost_oppfolging_vakt();
DROP TRIGGER IF EXISTS epost_oppfolging_ingen_truncate ON epost_oppfolging;
CREATE TRIGGER epost_oppfolging_ingen_truncate
    BEFORE TRUNCATE ON epost_oppfolging
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- ------------------------------------------------------------
-- 6. «ALDRI ETT LAGER ALENE» — målt ved COMMIT, på ANKERETS nivå
-- (076/#163-formen, ordrett). Radvaktene over ser ÉN rad; dette er
-- påstanden om meldingsgrafen SAMLET: for én melding kan lagrene
-- (meldingen selv + klassifisering + utkast + vedlegg) ikke bære både
-- levende og reapet payload når transaksjonen er over.
--
-- NIVÅET er #163s dom, født riktig her i stedet for å gjenta 057s
-- runde: en utsatt constraint-trigger kan ikke være FOR EACH STATEMENT,
-- så en per-rad-port på barnelagrene ville kjørt de samme meldingsvide
-- EXISTS-skannene én gang per rad — kvadratisk i barnetallet, 076s
-- eksakte skadeklasse. Derfor: barnelagrene bærer en BILLIG markør
-- («denne meldingen ble rørt» i en transaksjonslokal temptabell +
-- no-op-armering av ankerraden), og selve EXISTS-sjekken bor i ÉN
-- utsatt constraint-trigger på `epost_melding` — én gang per melding.
--
-- SECURITY DEFINER eid av CLAIMEREN (057s Codex P1, ordrett): porten
-- kjører UTSATT, etter at reaperens definer-identitet er borte, og må
-- lese gjennom claimerens `m6_reaper`-policy uansett hvem som
-- committer — ellers er den blind (null rader ⇒ «ingen blanding») eller
-- rød på rettigheter for timerrollen. Markøren er claimer-eid definer
-- av 076s grunn: skriverne er både runtime (INSERT, PR-C) og reaperen
-- (UPDATE), og no-op-armeringen trenger claimerens UPDATE-rett og
-- kryss-tenant-policy.
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION m6_marker_beroert_melding()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_vaar BOOLEAN;
BEGIN
    CREATE TEMP TABLE IF NOT EXISTS m6_beroerte_meldinger (
        tenant TEXT NOT NULL,
        melding_id UUID NOT NULL,
        PRIMARY KEY (tenant, melding_id)
    ) ON COMMIT DROP;
    -- MARKØRTABELLEN MÅ VÆRE VÅR (076s CodeRabbit-port, ordrett):
    -- TEMP-retten er PUBLICs, så en kaller kunne pre-lage tabellen —
    -- ferdig seedet — og kvele armeringen for sine egne skriv. Skapes
    -- den her (SECURITY DEFINER) eies den av claimeren; alt annet
    -- eierskap er en forfalskning, og da avvises SKRIVET (fail-closed),
    -- aldri bare markeringen.
    SELECT c.relowner = (SELECT r.oid FROM pg_catalog.pg_roles r
                          WHERE r.rolname = 'disponit_m37_claimer')
      INTO v_vaar
      FROM pg_catalog.pg_class c
     WHERE c.relnamespace = pg_catalog.pg_my_temp_schema()
       AND c.relname = 'm6_beroerte_meldinger';
    IF v_vaar IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'epostlagrene: markørtabellen er ikke portens'
            ' egen — skrivet avvises (076-formen, fail-closed)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    INSERT INTO pg_temp.m6_beroerte_meldinger (tenant, melding_id)
         VALUES (NEW.tenant, NEW.melding_id)
    ON CONFLICT DO NOTHING;
    IF NOT FOUND THEN
        RETURN NULL;                 -- alt armert i denne transaksjonen
    END IF;
    UPDATE public.epost_melding m
       SET melding_id = m.melding_id
     WHERE m.tenant = NEW.tenant AND m.melding_id = NEW.melding_id;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION m6_marker_beroert_melding() FROM PUBLIC;
-- CREATE TRIGGER krever EXECUTE for TABELLEIEREN, og grantet må gis av
-- funksjonens EIER — altså her, inne i claimer-blokka (057/076-formen).
DO $$
DECLARE v_eier TEXT;
BEGIN
    SELECT pg_catalog.pg_get_userbyid(relowner) INTO v_eier
      FROM pg_catalog.pg_class
     WHERE oid = 'public.epost_melding'::regclass;
    EXECUTE format('GRANT EXECUTE ON FUNCTION'
                   ' m6_marker_beroert_melding() TO %I', v_eier);
END $$;

CREATE OR REPLACE FUNCTION m6_lagrene_reapes_samlet()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.epost_melding m
         WHERE m.tenant = NEW.tenant AND m.melding_id = NEW.melding_id
           AND m.slettet_ts IS NULL
        UNION ALL
        SELECT 1 FROM public.epost_klassifisering k
         WHERE k.tenant = NEW.tenant AND k.melding_id = NEW.melding_id
           AND k.slettet_ts IS NULL
        UNION ALL
        SELECT 1 FROM public.epost_utkast u
         WHERE u.tenant = NEW.tenant AND u.melding_id = NEW.melding_id
           AND u.slettet_ts IS NULL
        UNION ALL
        SELECT 1 FROM public.epost_vedlegg v
         WHERE v.tenant = NEW.tenant AND v.melding_id = NEW.melding_id
           AND v.slettet_ts IS NULL)
       AND EXISTS (
        SELECT 1 FROM public.epost_melding m
         WHERE m.tenant = NEW.tenant AND m.melding_id = NEW.melding_id
           AND m.slettet_ts IS NOT NULL
        UNION ALL
        SELECT 1 FROM public.epost_klassifisering k
         WHERE k.tenant = NEW.tenant AND k.melding_id = NEW.melding_id
           AND k.slettet_ts IS NOT NULL
        UNION ALL
        SELECT 1 FROM public.epost_utkast u
         WHERE u.tenant = NEW.tenant AND u.melding_id = NEW.melding_id
           AND u.slettet_ts IS NOT NULL
        UNION ALL
        SELECT 1 FROM public.epost_vedlegg v
         WHERE v.tenant = NEW.tenant AND v.melding_id = NEW.melding_id
           AND v.slettet_ts IS NOT NULL) THEN
        RAISE EXCEPTION 'epostlagrene: melding % hos % bærer både levende'
            ' og reapet payload ved COMMIT — lagrene reapes SAMLET,'
            ' aldri ett alene (057 port 19-formen)',
            NEW.melding_id, NEW.tenant
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NULL;
END $$;

-- Rettighetsendringen står INNE i claimer-blokka (#140-læren), og
-- CREATE OR REPLACE gjør det samme: en andre kjøring av migrasjonen må
-- gå samme vei inn.
REVOKE ALL ON FUNCTION m6_lagrene_reapes_samlet() FROM PUBLIC;
-- ... men migrator eier TABELLENE og lager triggerne, og CREATE TRIGGER
-- forutsetter EXECUTE på funksjonen (057-formen: mottakeren slås opp
-- som TABELLEIEREN, ikke som et rollenavn).
DO $$
DECLARE v_eier TEXT;
BEGIN
    SELECT pg_catalog.pg_get_userbyid(relowner) INTO v_eier
      FROM pg_catalog.pg_class
     WHERE oid = 'public.epost_melding'::regclass;
    EXECUTE format('GRANT EXECUTE ON FUNCTION m6_lagrene_reapes_samlet()'
                   ' TO %I', v_eier);
END $$;
RESET ROLE;

-- Triggerne er migrators: CREATE (CONSTRAINT) TRIGGER krever eierskap
-- på TABELLEN, ikke på funksjonen. Barnelagrene bærer MARKØREN (AFTER
-- INSERT OR UPDATE — 057s Codex P2: porten måler BLANDINGEN, ikke
-- merket, så også en forsinket INSERT som lager blandingen skal
-- armere); ankeret bærer DEN ENE utsatte porten. Meldingens egne
-- INSERT/UPDATE armerer porten direkte — triggeren står på ankerraden.
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'epost_klassifisering', 'epost_utkast', 'epost_vedlegg'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I',
                       t || '_beroert', t);
        EXECUTE format(
            'CREATE TRIGGER %I AFTER INSERT OR UPDATE ON %I'
            ' FOR EACH ROW EXECUTE FUNCTION m6_marker_beroert_melding()',
            t || '_beroert', t);
    END LOOP;
END $$;

DROP TRIGGER IF EXISTS epost_melding_reapes_samlet ON epost_melding;
CREATE CONSTRAINT TRIGGER epost_melding_reapes_samlet
    AFTER INSERT OR UPDATE ON epost_melding
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION m6_lagrene_reapes_samlet();

-- ------------------------------------------------------------
-- 7. Tenant-isolasjon — samme form som 038/056/057.
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'epost_kilde', 'epost_melding', 'epost_klassifisering',
        'epost_utkast', 'epost_oppfolging', 'epost_vedlegg'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolasjon ON %I
                USING      (tenant = current_setting(''disponit.tenant'', true))
                WITH CHECK (tenant = current_setting(''disponit.tenant'', true))',
            t);
    END LOOP;
END $$;

-- Reaperen er kryss-tenant og eies av claimeren — 005/057s valg gjelder
-- ordrett: en EKSPLISITT policy for akkurat den rollen, aldri
-- BYPASSRLS. Kun payload-lagrene: reaperen rører aldri kilden eller
-- oppfølgingen.
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'epost_melding', 'epost_klassifisering', 'epost_utkast',
        'epost_vedlegg'] LOOP
        EXECUTE format(
            'CREATE POLICY m6_reaper ON %I TO disponit_m37_claimer
                USING (CURRENT_USER = ''disponit_m37_claimer'')
                WITH CHECK (CURRENT_USER = ''disponit_m37_claimer'')', t);
    END LOOP;
END $$;

-- ------------------------------------------------------------
-- 8. Reaperen. Eieren av tabellene (migrator) gir claimeren
-- radrettighetene funksjonen trenger — UPDATE er her, men radvaktene
-- snevrer den til reap-overgangen, også for claimeren. INGEN INSERT:
-- PR-A har ingen skrivedører, og innhenterens vei (PR-C) får sine
-- rettigheter når den fødes.
GRANT SELECT, UPDATE ON epost_melding, epost_klassifisering,
    epost_utkast, epost_vedlegg TO disponit_m37_claimer;

SET LOCAL ROLE disponit_m37_claimer;

-- 038/057-formen, ordrett: kryss-tenant-autoriteten er innelukket —
-- intet tenantparameter, utvalget ER predikatet, én melding om gangen
-- med RADENS tenant i konteksten, SKIP LOCKED gjør overlappende
-- kjøringer trygge. Alle payload-lagre tømmes i SAMME iterasjon og
-- samme transaksjon som meldingsmerket: det finnes ingen vei gjennom
-- denne funksjonen der ett lager reapes alene.
CREATE OR REPLACE FUNCTION reap_epostdata(p_grense INT DEFAULT 50)
RETURNS TABLE (tenant TEXT, melding_id UUID)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_kontekst TEXT; v_naa TIMESTAMPTZ;
BEGIN
    v_kontekst := current_setting('disponit.tenant', true);
    v_naa := pg_catalog.now();
    FOR r IN
        SELECT m.tenant AS t, m.melding_id AS mid
          FROM public.epost_melding m
         WHERE m.slettet_ts IS NULL
           AND v_naa > m.mottatt_ts
                       + m.slettefrist_dogn * interval '1 day'
         ORDER BY m.mottatt_ts
         LIMIT p_grense
         FOR UPDATE OF m SKIP LOCKED
    LOOP
        PERFORM set_config('disponit.tenant', r.t, true);
        UPDATE public.epost_vedlegg v
           SET navn_kryptert = NULL, nonce = NULL, key_id = NULL,
               slettet_ts = v_naa
         WHERE v.tenant = r.t AND v.melding_id = r.mid
           AND v.slettet_ts IS NULL;
        UPDATE public.epost_klassifisering k
           SET sammendrag_kryptert = NULL, nonce = NULL, key_id = NULL,
               slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.melding_id = r.mid
           AND k.slettet_ts IS NULL;
        UPDATE public.epost_utkast u
           SET tekst_kryptert = NULL, nonce = NULL, key_id = NULL,
               slettet_ts = v_naa
         WHERE u.tenant = r.t AND u.melding_id = r.mid
           AND u.slettet_ts IS NULL;
        UPDATE public.epost_melding m2
           SET kropp_kryptert = NULL, nonce = NULL, key_id = NULL,
               slettet_ts = v_naa
         WHERE m2.tenant = r.t AND m2.melding_id = r.mid;
        tenant := r.t; melding_id := r.mid;
        RETURN NEXT;
    END LOOP;
    PERFORM set_config('disponit.tenant', coalesce(v_kontekst, ''), true);
END $$;

-- Funksjonsblokka kjører fortsatt SOM CLAIMEREN — det er eierens egne
-- REVOKE/GRANT som gjelder (#140-læren).
REVOKE ALL ON FUNCTION reap_epostdata(INT) FROM PUBLIC;
-- Reaperen er kryss-tenant (038-læren, 057-blokken ORDRETT): i et
-- oppsett MED egen timerrolle hører den hjemme der, og web-API-rollen
-- skal ikke ha den — et grant som bare slutter å bli gitt er ikke
-- trukket tilbake. BEGGE armene er vaktet på at rollen `disponit`
-- FINNES: runtime-rollen kan hete noe annet (navnet er et argument til
-- `migrer.py`), og `REVOKE ... FROM <ukjent rolle>` er en FEIL, ikke en
-- no-op.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_domener') THEN
        GRANT EXECUTE ON FUNCTION reap_epostdata(INT)
            TO disponit_domener;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
            REVOKE EXECUTE ON FUNCTION reap_epostdata(INT) FROM disponit;
        END IF;
    ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        GRANT EXECUTE ON FUNCTION reap_epostdata(INT) TO disponit;
    END IF;
END $$;

RESET ROLE;

-- Radvaktene og tabellene er migrators egne. Den UTSATTE porten
-- (`m6_lagrene_reapes_samlet`) er det ikke — den eies av claimeren og
-- har sin egen REVOKE inne i sin egen blokk, der den virker.
REVOKE ALL ON FUNCTION epost_kilde_vakt() FROM PUBLIC;
REVOKE ALL ON FUNCTION epost_melding_vakt() FROM PUBLIC;
REVOKE ALL ON FUNCTION m6_epostlager_vakt() FROM PUBLIC;
REVOKE ALL ON FUNCTION epost_utkast_vakt() FROM PUBLIC;
REVOKE ALL ON FUNCTION epost_oppfolging_vakt() FROM PUBLIC;

REVOKE ALL ON epost_kilde, epost_melding, epost_klassifisering,
    epost_utkast, epost_oppfolging, epost_vedlegg FROM PUBLIC;
-- Runtime-grantene bor i `migrer.py` på `{rolle}`-form (056/057-læren):
-- PR-A gir runtime KUN SELECT (RLS-gated leseflate; port 2 måler at et
-- direkte SELECT bare gir ciphertext). Innhenterens INSERT-vei kommer
-- med PR-C og får sine grants der — en rettighet uten en fødte vei er
-- bare en dør som står ulåst.

-- Rollemønsteret i basen (043 §6b) speiler ROLLE_TIL_SCOPES eksakt —
-- M-6-scopene føyes til her (044-formen ordrett), ellers er port 26
-- rød: et scope lagt til i app-laget uten migrasjon er et sprik basen
-- først ser når et lovlig nei avvises.
INSERT INTO rolle_scope (rolle, scope) VALUES
    ('leser', 'epost:read'),
    ('sikkerhet', 'epost:read'),
    ('admin', 'epost:read'),
    ('admin', 'epost:kilde:administrer'),
    ('admin', 'epost:utkast:behandle')
ON CONFLICT DO NOTHING;
