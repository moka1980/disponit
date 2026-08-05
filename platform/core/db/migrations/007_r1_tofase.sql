-- ============================================================
-- Disponit migrasjon 007 — R1 som tofaseprotokoll.
-- Spesifisert i PR-007 v1 + v2/v3/v4-delta + de fem GO-vilkårene.
--
-- HVORFOR DENNE FINNES: R1 kunne aldri lykkes. Den sendte den MINIMERTE
-- payloaden som ny beslutning, og minimeringen har ingen `attestasjoner`
-- — så saken manglet nøyaktig det som gjorde den til et unntak, og ble
-- UNNTAK igjen. Målt på en levende trekjede (API + arbeider + eiermodul),
-- ikke resonnert.
--
-- Løsningen er en FASE FØR beslutningen: en verifikator attesterer det
-- manglende vilkåret sideeffektfritt, og først da bygges den nye
-- hendelsen. Fase 1 har null forretningsfullmakter; fase 2 går gjennom
-- hele policyporten som enhver annen beslutning.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen. Kjøres av MIGRATOR.
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_m37_claimer') THEN
        RAISE EXCEPTION
            'rollen disponit_m37_claimer mangler — kjør deploy/staging/oppsett-postgresql.sh først';
    END IF;
    IF NOT pg_has_role(current_user, 'disponit_m37_claimer', 'MEMBER') THEN
        RAISE EXCEPTION
            'migratorrollen % er ikke medlem av disponit_m37_claimer', current_user;
    END IF;
END $$;

-- ------------------------------------------------------------
-- 1. Tre nye IKKE-TERMINALE statuser + veien tilbake
--
-- `venter_verifikasjon` er den eneste tilstanden i hele modellen som har
-- en vei TILBAKE til behandling. Det er bevisst og avgrenset: den nås kun
-- fra `under_behandling`, og den forlates kun gjennom en fenced overgang
-- som krever et positivt, ikke-utløpt bevis.
--
-- `verifikasjon_klar` og `verifikasjon_retry_klar` er egne statuser og
-- ikke en utledning fra «finnes det en bevisrad». v2-delta pkt. 2:
-- fasen SKAL være eksplisitt i databasen. En fase man må gjette seg til
-- ved å telle rader i en annen tabell, er ikke en tilstand — den er en
-- rekonstruksjon, og to lesere kan rekonstruere ulikt.
-- ------------------------------------------------------------
ALTER TABLE unntak DROP CONSTRAINT IF EXISTS unntak_status_check;
ALTER TABLE unntak ADD CONSTRAINT unntak_status_check CHECK (
    status IN ('ny','under_behandling','løst','avvist','manuell',
               'venter_utførelse','venter_verifikasjon','verifikasjon_klar',
               'verifikasjon_retry_klar'));

-- GO-vilkår V5: maskinell nummerering starter på 1. Første generasjon er
-- `1`, ikke `0` — og kolonnen starter på 0 nettopp for at «ingen
-- verifikasjon startet» skal være forskjellig fra «første generasjon».
-- Ny generasjon tillates når `generation < maks_auto_forsok_snapshot`,
-- så totalt antall verifikasjonsoppdrag er nøyaktig snapshotet.
ALTER TABLE unntak
    ADD COLUMN IF NOT EXISTS verification_generation INT NOT NULL DEFAULT 0;
-- Det FROSNE vilkårssettet (v6 pkt. 1). Bestemmes ÉN gang ved første
-- klassifisering og slås aldri opp på nytt mot aktiv policy. Endrer
-- policyen vilkårene etterpå, påvirker det aldri en pågående sak.
--
-- 🔴 AVVIK FRA KLARSIGNALET: kolonnen er NULLABLE, ikke NOT NULL.
-- Spesifikasjonen sier `krav_sett JSONB NOT NULL`, men settet bestemmes av
-- ARBEIDEREN ved første klassifisering — ikke av API-veien ved
-- opprettelse. En NOT NULL-kolonne måtte da fylles med noe meningsløst i
-- det øyeblikket saken skrives, og «frosset sett» ville betydd «frosset
-- tomt sett». Kolonnelåsen under gir den egenskapen spesifikasjonen
-- faktisk er ute etter: uforanderlig når den først er satt.
ALTER TABLE unntak ADD COLUMN IF NOT EXISTS krav_sett JSONB;
ALTER TABLE unntak DROP CONSTRAINT IF EXISTS unntak_verifikasjonsgenerasjon;
ALTER TABLE unntak ADD CONSTRAINT unntak_verifikasjonsgenerasjon
    CHECK (verification_generation >= 0);

-- GO-vilkår V4: `ventet_bevis_id` opprettes ALDRI.
--
-- v2-delta foreslo den som peker fra saken til beviset fase 2 skal bruke.
-- v4-delta viste at en slik peker enten må bindes med kompositt-FK eller
-- er en ubundet bekvemmelighet som tillater kryss-tenant-referanser
-- utenfor funksjonskoden. Valget er å FJERNE den: generasjonsraden bærer
-- allerede bindingen med full kontekst, og fase 2 henter beviset derfra.
-- En ubundet peker i tillitsankeret er mer kode for null gevinst.
--
-- Kolonnen finnes ikke i noen base (den ble aldri skipet), så det er
-- ingenting å droppe. Dette er en NEGATIV kontrakt: den skal aldri
-- opprettes, og `test_ventet_bevis_id_finnes_ikke` håndhever det.

-- ------------------------------------------------------------
-- 2. Statusmaskinen — komplett etter v4-delta pkt. 1
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION unntak_kolonnelaas()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.loggpost_id IS DISTINCT FROM OLD.loggpost_id
       OR NEW.handling IS DISTINCT FROM OLD.handling
       OR NEW.kategori IS DISTINCT FROM OLD.kategori
       OR NEW.sakstype IS DISTINCT FROM OLD.sakstype
       OR NEW.prioritet IS DISTINCT FROM OLD.prioritet
       OR NEW.payload_kryptert IS DISTINCT FROM OLD.payload_kryptert
       OR NEW.key_id IS DISTINCT FROM OLD.key_id
       OR NEW.alg IS DISTINCT FROM OLD.alg
       OR NEW.nonce IS DISTINCT FROM OLD.nonce
       OR NEW.ts IS DISTINCT FROM OLD.ts THEN
        RAISE EXCEPTION 'unntak: kun status/status_ts/forsok/claim-felter kan endres';
    END IF;

    IF OLD.maks_auto_forsok_snapshot IS NOT NULL
       AND NEW.maks_auto_forsok_snapshot IS DISTINCT FROM OLD.maks_auto_forsok_snapshot THEN
        RAISE EXCEPTION 'unntak: maks_auto_forsok_snapshot er uforanderlig etter at den er satt';
    END IF;
    IF OLD.policy_versjon IS NOT NULL
       AND NEW.policy_versjon IS DISTINCT FROM OLD.policy_versjon THEN
        RAISE EXCEPTION 'unntak: policy_versjon er uforanderlig etter at den er satt';
    END IF;
    IF OLD.policy_content_hash IS NOT NULL
       AND NEW.policy_content_hash IS DISTINCT FROM OLD.policy_content_hash THEN
        RAISE EXCEPTION 'unntak: policy_content_hash er uforanderlig etter at den er satt';
    END IF;

    IF NEW.claim_generation < OLD.claim_generation THEN
        RAISE EXCEPTION 'unntak: claim_generation kan aldri reduseres (% -> %)',
            OLD.claim_generation, NEW.claim_generation;
    END IF;
    -- Verifikasjonsgenerasjonen er like monoton som claim-generasjonen, og
    -- av samme grunn: den inngår i `fase1_id`, så en reduksjon ville gjort
    -- en gammel fase-1-identitet gyldig igjen.
    IF NEW.verification_generation < OLD.verification_generation THEN
        RAISE EXCEPTION 'unntak: verification_generation kan aldri reduseres (% -> %)',
            OLD.verification_generation, NEW.verification_generation;
    END IF;

    -- Settet er FROSSET. Kunne det endres, ville «saken behandles mot det
    -- settet den ble klassifisert mot» vært noe man kan skrive om i
    -- ettertid — og da beviser det ingenting.
    IF OLD.krav_sett IS NOT NULL
       AND NEW.krav_sett IS DISTINCT FROM OLD.krav_sett THEN
        RAISE EXCEPTION 'unntak: krav_sett er frosset og kan ikke endres';
    END IF;

    IF NOT (
        (OLD.status = 'ny'               AND NEW.status IN ('under_behandling','manuell')) OR
        -- `verifikasjon_retry_klar` HERFRA er fase 2s vei tilbake: den
        -- fenced claimen fant et utløpt bevis eller et skjerpet kravsett,
        -- og settet må verifiseres på nytt i en NY generasjon. Uten dette
        -- leddet kastet triggeren, arbeideren mistet saken, og fase 2s
        -- retry-vei var uoppnåelig — MÅLT, ikke resonnert frem.
        (OLD.status = 'under_behandling' AND NEW.status IN
             ('løst','avvist','manuell','venter_utførelse',
              'venter_verifikasjon','verifikasjon_retry_klar')) OR
        (OLD.status = 'under_behandling' AND NEW.status = 'ny'
             AND OLD.claim_utloper IS NOT NULL AND OLD.claim_utloper < now()) OR
        (OLD.status = 'venter_utførelse' AND NEW.status IN ('løst','manuell')) OR
        -- Fase 1s utfall. `verifikasjon_klar` = positivt bevis foreligger;
        -- `verifikasjon_retry_klar` = negativt/utløpt MED budsjett igjen;
        -- `manuell` = negativt/utløpt UTEN budsjett.
        (OLD.status = 'venter_verifikasjon' AND NEW.status IN
             ('verifikasjon_klar','verifikasjon_retry_klar','manuell')) OR
        -- Begge klar-tilstandene claimes av arbeideren og går tilbake til
        -- behandling. `verifikasjon_klar` -> fase 2; `retry_klar` -> ny
        -- generasjon + nytt verifikasjonsoppdrag.
        (OLD.status IN ('verifikasjon_klar','verifikasjon_retry_klar')
             AND NEW.status IN ('under_behandling','manuell')) OR
        (OLD.status = NEW.status)  -- forsok/claim-oppdatering uten statusskifte
    ) THEN
        RAISE EXCEPTION 'unntak: ulovlig statusovergang % -> %', OLD.status, NEW.status;
    END IF;

    -- Codex-port 9: `manuell` gjenåpnes ALDRI automatisk. Administrativ
    -- gjenåpning er eksplisitt utenfor PR-007 (v3-delta pkt. 1) og krever
    -- en egen auditert prosedyre som ikke finnes ennå.
    IF OLD.status IN ('løst','avvist','manuell') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'unntak: % er terminal og kan ikke forlates', OLD.status;
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status THEN
        NEW.status_ts := now();
    END IF;
    RETURN NEW;
END $$;

-- ------------------------------------------------------------
-- 3. Historikk: nye hendelser
-- ------------------------------------------------------------
ALTER TABLE unntak_historikk DROP CONSTRAINT IF EXISTS unntak_historikk_hendelse_check;
ALTER TABLE unntak_historikk ADD CONSTRAINT unntak_historikk_hendelse_check CHECK (
    hendelse IN (
        'opprettet','statusendring','claim','claim_utlopt','dek_destruert',
        'claim_fornyet','klassifisert','repair_generation_ny',
        'generation_blokkert_aktiv_utforelse','kapabilitet_utstedt',
        'kapabilitet_brukt','oppdrag_opprettet','oppdrag_kansellert',
        'kvittering','sen_kvittering','motstridende_kvittering',
        'policy_endret_siden_opprettelse','legacy_uten_snapshot',
        'dek_utilgjengelig','verifikator_utilgjengelig','frist_utlopt',
        -- PR-007
        'verifikasjon_bestilt','verifikasjon_positiv','verifikasjon_negativ',
        'verifikasjon_utlopt','verifikasjon_konflikt','verifikasjon_retry',
        'sikkerhetsfrysing'));

-- ------------------------------------------------------------
-- 4. Generasjonstilstand — MUTERBAR, auditert (v3-delta pkt. 1)
--
-- Skilt fra beviset med vilje. v2 hadde begge deler i én append-only
-- tabell med en `WHERE status='aktiv'`-delindeks, og da ville den FØRSTE
-- generasjonen blokkert alle fremtidige for alltid: en append-only rad
-- kan ikke slutte å være aktiv.
--
-- FIRE statuser, ikke fem. `konflikt` finnes IKKE som generasjonsstatus
-- (v4-delta pkt. 2): en terminal generasjonsstatus endres aldri etter
-- første aksepterte resultat. En motstridende kvittering er append-only
-- EVIDENS, ikke en tilstandsendring — ellers kunne en sen forfalskning
-- ugyldiggjort et bevis fase 2 allerede bruker.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS verifikasjonsgenerasjon (
    tenant     TEXT NOT NULL,
    unntak_id  BIGINT NOT NULL,
    vilkaar    TEXT NOT NULL,
    generation INT  NOT NULL CHECK (generation >= 1),
    status     TEXT NOT NULL DEFAULT 'aktiv'
               CHECK (status IN ('aktiv','positiv','negativ','utlopt')),
    bevis_id   BIGINT,
    oppdrag_id BIGINT,
    -- Scope v2 pkt. 1: valget fryses ved opprettelse og kan ALDRI påvirkes
    -- av arbeider eller klient. Det er utledet server-side (skjæringsmengde
    -- + deterministisk regel), lagret og låst FØR oppdraget bygges.
    valgt_verifikator          TEXT NOT NULL,
    -- Versjonen/hashen av `betrodd_for`-relasjonen valget ble gjort mot.
    -- Snapshotet beviser FORSØKET; en tilbaketrukket fullmakt må fanges på
    -- nåtid (Scope v2 pkt. 2), og da trenger vi å vite hva som gjaldt da.
    autoritetsregister_versjon TEXT NOT NULL,
    krav_sett_hash             TEXT NOT NULL,
    frist      TIMESTAMPTZ,
    opprettet  TIMESTAMPTZ NOT NULL DEFAULT now(),
    status_ts  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, unntak_id, vilkaar, generation),
    -- `positiv` uten bevis er en selvmotsigelse: statusen BETYR at det
    -- finnes et bevis, og en status som kan lyve om sin egen forutsetning
    -- er ingen status.
    CONSTRAINT positiv_krever_bevis CHECK (status <> 'positiv' OR bevis_id IS NOT NULL),
    FOREIGN KEY (tenant, unntak_id) REFERENCES unntak (tenant, id)
);

-- Maks ÉN aktiv generasjon per (sak, vilkår) — men nye generasjoner er
-- tillatt sekvensielt. Delindeksen ligger her og ikke på beviset, som er
-- hele poenget med å skille tabellene.
-- Ett oppdrag hører til NØYAKTIG én generasjon (Codex P1, runde 5).
--
-- Ingest slår opp generasjonsraden PÅ oppdraget. Kunne to rader delt
-- oppdrag, ville «den frosne generasjonen» vært flertydig — og et oppslag
-- som kan returnere feil rad er ingen binding. Delindeksen gjør entydig-
-- heten strukturell i stedet for en antakelse om skrivemønsteret.
CREATE UNIQUE INDEX IF NOT EXISTS en_generasjon_per_oppdrag
    ON verifikasjonsgenerasjon (tenant, unntak_id, vilkaar, oppdrag_id)
    WHERE oppdrag_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS en_aktiv_generasjon_per_sak_vilkaar
    ON verifikasjonsgenerasjon (tenant, unntak_id, vilkaar) WHERE status = 'aktiv';

CREATE OR REPLACE FUNCTION verifikasjonsgenerasjon_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'verifikasjonsgenerasjon: DELETE er forbudt';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.unntak_id IS DISTINCT FROM OLD.unntak_id
       OR NEW.vilkaar IS DISTINCT FROM OLD.vilkaar
       OR NEW.generation IS DISTINCT FROM OLD.generation
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.valgt_verifikator IS DISTINCT FROM OLD.valgt_verifikator
       OR NEW.autoritetsregister_versjon IS DISTINCT FROM OLD.autoritetsregister_versjon
       OR NEW.krav_sett_hash IS DISTINCT FROM OLD.krav_sett_hash THEN
        RAISE EXCEPTION 'verifikasjonsgenerasjon: identitetsfelter er uforanderlige';
    END IF;
    -- GO-vilkår V1 + v4-delta pkt. 2: KUN aktiv kan endres, og aldri til
    -- noe annet enn de tre terminale. Ingen kodevei kan uttrykke
    -- `positiv -> negativ` eller `positiv -> konflikt`, fordi overgangen
    -- ikke finnes.
    IF OLD.status <> 'aktiv' THEN
        IF NEW.status IS DISTINCT FROM OLD.status THEN
            RAISE EXCEPTION
                'verifikasjonsgenerasjon: % er terminal — første committede resultat vinner',
                OLD.status;
        END IF;
        IF NEW.bevis_id IS DISTINCT FROM OLD.bevis_id THEN
            RAISE EXCEPTION 'verifikasjonsgenerasjon: beviset kan ikke byttes etter terminal status';
        END IF;
    ELSIF NEW.status NOT IN ('aktiv','positiv','negativ','utlopt') THEN
        RAISE EXCEPTION 'verifikasjonsgenerasjon: ulovlig status %', NEW.status;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        NEW.status_ts := now();
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS verifikasjonsgenerasjon_overgang ON verifikasjonsgenerasjon;
CREATE TRIGGER verifikasjonsgenerasjon_overgang
    BEFORE UPDATE OR DELETE ON verifikasjonsgenerasjon
    FOR EACH ROW EXECUTE FUNCTION verifikasjonsgenerasjon_vakt();

-- ------------------------------------------------------------
-- 5. Verifikasjonsbevis — APPEND-ONLY og KRYPTERT (v2-delta pkt. 7)
--
-- Samme envelope som `unntak.payload_kryptert`: tenant-DEK, AES-256-GCM,
-- nonce i egen kolonne. Attestasjonen er saksinnhold og skal ikke ligge i
-- klartekst i en database noen kan dumpe.
--
-- `integritet_hash` er over CIPHERTEXT, ikke klartekst (v3-delta pkt. 3).
-- En hash over klartekst ville vært et ORAKEL: attestasjonens resultat har
-- få utfall, så en dump kunne gjettet innholdet ved å prøve dem.
-- GCM-taggen autentiserer klarteksten ved dekryptering; denne hashen
-- beskytter mot bit-flipping i lagring og lekker ingenting.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS verifikasjonsbevis (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant        TEXT NOT NULL,
    unntak_id     BIGINT NOT NULL,
    -- Nøkkelkolonnen bærer sentinelen `*sett*` (v5 pkt. 2): ETT
    -- verifikasjonsløp per sak+generasjon, ikke per vilkår. Delindeksen på
    -- generasjonstabellen gir dermed «én aktiv R1-generasjon per sak» uten
    -- skjemaendring.
    vilkaar       TEXT NOT NULL,
    -- Det FAKTISKE vilkåret dette beviset gjelder.
    bevis_vilkaar TEXT NOT NULL,
    generation    INT  NOT NULL CHECK (generation >= 1),
    oppdrag_id    BIGINT NOT NULL,
    fase1_repair_operation_id TEXT NOT NULL
                  CHECK (fase1_repair_operation_id ~ '^[0-9a-f]{64}$'),
    -- Verifikatoren kommer fra den VERIFISERTE NØKKELEN, ikke fra et
    -- selvrapportert felt i konvolutten (v2-delta pkt. 4).
    verifikator   TEXT NOT NULL,
    nokkel_id     TEXT NOT NULL,
    signatur      TEXT NOT NULL,
    attestasjon_kryptert BYTEA NOT NULL,
    key_id        TEXT NOT NULL,
    alg           TEXT NOT NULL DEFAULT 'AES-256-GCM',
    nonce         BYTEA NOT NULL,
    integritet_hash TEXT NOT NULL CHECK (integritet_hash ~ '^[0-9a-f]{64}$'),
    gyldig_til    TIMESTAMPTZ NOT NULL,
    opprettet     TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- v4-delta pkt. 3: sammensatt unik nøkkel, slik at generasjonsraden
    -- kan binde til beviset med FULL kontekst.
    CONSTRAINT bevis_komposittnokkel
        UNIQUE (tenant, unntak_id, vilkaar, generation, id),
    -- Ett bevis per (sak, vilkår, generasjon). Idempotens i databasen,
    -- ikke bare i funksjonen.
    -- Ett bevis per VILKÅR per generasjon. Andre forsvarslinje mot at
    -- samme krav dobbeltbevises i ett løp (v5 pkt. 2).
    CONSTRAINT bevis_en_per_vilkaar_per_generasjon
        UNIQUE (tenant, unntak_id, generation, bevis_vilkaar),
    FOREIGN KEY (tenant, unntak_id) REFERENCES unntak (tenant, id),
    FOREIGN KEY (tenant, key_id)    REFERENCES tenant_nokler (tenant, key_id)
);

CREATE OR REPLACE FUNCTION verifikasjonsbevis_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'verifikasjonsbevis er append-only: % er forbudt', TG_OP;
END $$;
DROP TRIGGER IF EXISTS bevis_ingen_endring ON verifikasjonsbevis;
CREATE TRIGGER bevis_ingen_endring BEFORE UPDATE OR DELETE ON verifikasjonsbevis
    FOR EACH ROW EXECUTE FUNCTION verifikasjonsbevis_append_only();
DROP TRIGGER IF EXISTS bevis_ingen_truncate ON verifikasjonsbevis;
CREATE TRIGGER bevis_ingen_truncate BEFORE TRUNCATE ON verifikasjonsbevis
    FOR EACH STATEMENT EXECUTE FUNCTION verifikasjonsbevis_append_only();

-- v4-delta pkt. 3: generasjonsraden binder til beviset med FULL kontekst.
-- `bevis_id` alene ville tillatt en referanse på tvers av tenant, sak,
-- vilkår eller generasjon — og da ville integriteten hvilt på at
-- funksjonskoden er riktig, i stedet for på databasen.
--
-- DEFERRABLE INITIALLY DEFERRED fordi ingest setter inn beviset OG
-- oppdaterer generasjonsraden i samme transaksjon; uten utsettelse ville
-- rekkefølgen inne i den ene atomiske operasjonen blitt et problem.
ALTER TABLE verifikasjonsgenerasjon
    DROP CONSTRAINT IF EXISTS gen_bevis_fk;
ALTER TABLE verifikasjonsgenerasjon
    ADD CONSTRAINT gen_bevis_fk
    FOREIGN KEY (tenant, unntak_id, vilkaar, generation, bevis_id)
    REFERENCES verifikasjonsbevis (tenant, unntak_id, vilkaar, generation, id)
    DEFERRABLE INITIALLY DEFERRED;

-- ------------------------------------------------------------
-- 6. Konfliktevidens — append-only, ENDRER ALDRI NOE (v4-delta pkt. 2)
--
-- En motstridende kvittering etter et akseptert resultat er en
-- SIKKERHETSHENDELSE, ikke en tilstandsendring. Den lagres her, og
-- generasjonens terminale status står urørt. Var det motsatt, kunne den
-- som klarer å sende to ulike kvitteringer bestemme utfallet — og en sen
-- forfalskning kunne ugyldiggjort et bevis fase 2 alt har brukt.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS verifikasjonskonflikt (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant       TEXT NOT NULL,
    unntak_id    BIGINT NOT NULL,
    vilkaar      TEXT NOT NULL,
    generation   INT  NOT NULL,
    oppdrag_id   BIGINT,
    akseptert_resultathash TEXT,
    ny_resultathash        TEXT NOT NULL,
    generasjonsstatus_ved_konflikt TEXT NOT NULL,
    fase2_utfort BOOLEAN NOT NULL DEFAULT false,
    detalj       JSONB,
    opprettet    TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant, unntak_id) REFERENCES unntak (tenant, id)
);
DROP TRIGGER IF EXISTS konflikt_ingen_endring ON verifikasjonskonflikt;
CREATE TRIGGER konflikt_ingen_endring
    BEFORE UPDATE OR DELETE ON verifikasjonskonflikt
    FOR EACH ROW EXECUTE FUNCTION verifikasjonsbevis_append_only();

-- ------------------------------------------------------------
-- 7. Utførelsesklasse per (handler, målhandling) — v3-delta pkt. 4
--
-- Klassen er DATA som slås opp, ikke noe M-37 kan velge eller overstyre.
-- Ett enkelt kolonneuttrykk gir XOR-en gratis: en rad kan ikke være både
-- `sideeffektfri` og `krever_outbox`, fordi den bare har én verdi.
-- Ukjent par slås ikke opp til noe → fail-closed `manuell`.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS utforelsesklasser (
    handler_id    TEXT NOT NULL,
    target_action TEXT NOT NULL,
    klasse        TEXT NOT NULL CHECK (klasse IN ('sideeffektfri','krever_outbox')),
    opprettet     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (handler_id, target_action)
);

INSERT INTO utforelsesklasser (handler_id, target_action, klasse) VALUES
    -- R1s målhandlinger er forretningshandlinger: de MÅ gjennom outboxen,
    -- og kan aldri gå direkte til `løst` (Codex-port 10).
    ('r1_reinnsending', 'purring.send',      'krever_outbox'),
    ('r1_reinnsending', 'faktura.krediter',  'krever_outbox'),
    ('r1_reinnsending', 'melding.send',      'krever_outbox'),
    -- R2 er per definisjon lokale kontroller uten sideeffekt.
    ('r2_lokal_kontroll', 'kontroll.revalider', 'sideeffektfri'),
    ('r2_lokal_kontroll', 'kontroll.reparser',  'sideeffektfri')
ON CONFLICT (handler_id, target_action) DO NOTHING;

-- ------------------------------------------------------------
-- 8. RLS + FORCE på de nye tenant-tabellene
-- ------------------------------------------------------------
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['verifikasjonsgenerasjon','verifikasjonsbevis',
                             'verifikasjonskonflikt']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE  ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolasjon ON %I', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolasjon ON %I
                USING      (tenant = current_setting(''disponit.tenant'', true))
                WITH CHECK (tenant = current_setting(''disponit.tenant'', true))', t);
        EXECUTE format('DROP POLICY IF EXISTS m37_dispatcher ON %I', t);
        EXECUTE format(
            'CREATE POLICY m37_dispatcher ON %I TO disponit_m37_claimer
                USING      (current_user = ''disponit_m37_claimer'')
                WITH CHECK (current_user = ''disponit_m37_claimer'')', t);
    END LOOP;
END $$;

GRANT SELECT, UPDATE ON verifikasjonsgenerasjon TO disponit_m37_claimer;
GRANT SELECT, INSERT ON verifikasjonsbevis      TO disponit_m37_claimer;
GRANT SELECT, INSERT ON verifikasjonskonflikt   TO disponit_m37_claimer;
GRANT SELECT, INSERT ON verifikasjonsgenerasjon TO disponit_m37_claimer;
GRANT SELECT           ON utforelsesklasser     TO disponit_m37_claimer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO disponit_m37_claimer;

-- ============================================================
-- FRA HER OG UT KJØRER VI SOM `disponit_m37_claimer` (mønster fra 005).
-- Objektene opprettes SOM riktig eier — ingen eierskifter, og hver
-- kjøring gjør nøyaktig det samme. `RESET ROLE` til slutt, ellers ville
-- kjøreren skrevet sin egen `migrasjoner`-rad som feil rolle.
-- ============================================================
SET LOCAL ROLE disponit_m37_claimer;

-- ------------------------------------------------------------
-- 9. GO-VILKÅR V2: FAST LÅSEREKKEFØLGE
--
--        unntak → verifikasjonsgenerasjon → oppdrag → kapabilitet
--
-- `verifikasjonsbevis` står ikke i rekkefølgen fordi ingen vei LÅSER en
-- bevisrad: tabellen er append-only, og en INSERT av en ny rad kan ikke
-- vente på en annen transaksjons rad. Bevisene skrives derfor etter at
-- oppdraget er låst, uten at det er et brudd på rekkefølgen.
--
-- ALLE veier som låser mer enn én rad følger den. Claim, ingest og
-- utløpsjobb er tre uavhengige veier inn i de samme radene, og to av dem
-- som låser i motsatt rekkefølge er en vranglås som venter på nok last.
--
-- Rekkefølgen er ikke vilkårlig valgt: den går fra det mest generelle
-- (saken) til det mest spesifikke (kapabiliteten), så en vei som bare
-- trenger de første leddene aldri må «hoppe over» et ledd en annen
-- allerede holder.
-- ------------------------------------------------------------

-- Bevis-ingest. ALT eller INGENTING (v3-delta pkt. 2).
--
-- Den signerte konvolutten er SAMMENLIGNINGSGRUNNLAG, ikke autoritativ
-- kilde: hvert felt matches mot databasen, og avvik gir sikkerhetssak
-- uten bevisrad. Signaturen er allerede verifisert i app-laget, der
-- nøkkelregisteret bor — databasen ser aldri en nøkkel.
-- Bevis-ingest for HELE SETTET. Alt eller ingenting (v6 pkt. 2).
--
-- `p_resultater` er en JSONB-array med ett element per vilkår:
--   {vilkaar, status, permanent, attestasjon_kryptert(hex), key_id,
--    nonce(hex), integritet_hash, gyldig_til}
--
-- Signaturen, sett-hashen og verifikatorens AKTIVE autoritet er allerede
-- kontrollert i app-laget — der nøkkelregisteret og policyen bor.
-- Databasen ser aldri en nøkkel. Her re-kontrolleres alle DB-BINDINGENE
-- mot radene: konvolutten er sammenligningsgrunnlag, aldri autoritativ
-- kilde.
--
-- DET FINNES INGEN MELLOMTILSTAND. Enten committes hele settet med
-- generasjon `positiv`, eller ingen bevis lagres i det hele tatt.
-- Tilstanden «bevis for ett vilkår finnes, resten mangler» er strukturelt
-- umulig, ikke bare uønsket.
-- SIGNATUREN ER NI ARGUMENTER + TO BINDINGER (Codex P1, runde 4).
--
-- `p_verification_generation` og `p_fase1_repair_operation_id` kommer fra
-- den SIGNERTE konvolutten. De sto tidligere ikke i signaturen i det hele
-- tatt: konvolutten krevde dem, verifikatoren signerte dem, og ingen leste
-- dem. Et obligatorisk signert felt som ingen sammenligner er dekorasjon,
-- ikke binding — en ellers gyldig konvolutt kunne lyve om begge og
-- fortsatt bli akseptert som positivt bevis.
DROP FUNCTION IF EXISTS registrer_verifikasjonsbevis(
    BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, INT);
DROP FUNCTION IF EXISTS registrer_verifikasjonsbevis(
    BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, INT, INT, TEXT);
CREATE FUNCTION registrer_verifikasjonsbevis(
        p_oppdrag_id BIGINT, p_resultathash TEXT, p_krav_sett_hash TEXT,
        p_verifikator TEXT, p_nokkel_id TEXT, p_signatur TEXT,
        p_resultater JSONB, p_owner_claim_id TEXT, p_owner_generation INT,
        p_verification_generation INT, p_fase1_repair_operation_id TEXT)
RETURNS TEXT
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    o          RECORD;
    g          RECORD;
    v_sak      RECORD;
    e          JSONB;
    v_krav     TEXT[];
    v_levert   TEXT[];
    v_bevis_id BIGINT;
    v_siste    BIGINT;
    v_nystatus TEXT;
    v_permanent BOOLEAN := false;
    v_alle_ok  BOOLEAN := true;
    v_treff    INT;
BEGIN
    -- Et FØRSTE, ULÅST oppslag — og det brukes til ÉN ting: å finne hvilken
    -- sak og hvilken generasjon som skal låses. Ingen klassifisering skjer
    -- på disse verdiene.
    --
    -- Codex P1, runde 4: den forrige versjonen leste `resultathash` her og
    -- klassifiserte på den etter å ha tatt låsene. To samtidige kall frøs
    -- da begge `resultathash = NULL`; vinneren skrev bevis og hash, mens
    -- taperen våknet på generasjonslåsen med et FORELDET oppdragsrecord,
    -- så `g.status <> 'aktiv'` og returnerte ubetinget `idempotent`. To
    -- ULIKE konvolutter ble dermed «positiv + idempotent» i stedet for
    -- «positiv + konflikt», og forsøket på motstridende evidens forsvant.
    --
    -- Samme kappløpsklasse som ble lukket for kvitteringskapabiliteten i
    -- PR #9. En lås som tas ETTER at verdien er lest, verner ingenting.
    SELECT d.tenant, d.unntak_id INTO o
      FROM public.oppdrag d WHERE d.id = p_oppdrag_id;
    IF NOT FOUND THEN
        RETURN 'avvist';
    END IF;

    -- LÅSEREKKEFØLGE ledd 1: saken.
    SELECT u.status, u.verification_generation, u.maks_auto_forsok_snapshot,
           u.krav_sett
      INTO v_sak FROM public.unntak u
     WHERE u.tenant = o.tenant AND u.id = o.unntak_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN 'avvist';
    END IF;

    -- LÅSEREKKEFØLGE ledd 2: generasjonsraden. Dette er serialiseringen —
    -- to samtidige kvitteringer blokkerer her, og den som committer først
    -- avgjør.
    --
    -- RADEN VELGES FRA OPPDRAGET, ikke fra `unntak.verification_generation`
    -- (Codex P1, runde 5). Sakens peker er MUTABEL: går saken fra
    -- generasjon N til N+1, ville et oppslag på den ikke lenger funnet
    -- raden som hører til oppdraget kvitteringen faktisk gjelder. En
    -- re-post for N returnerte da `avvist` FØR den fikk klassifisert den
    -- committede hashen som `idempotent` eller `konflikt` — altså var
    -- terminalklassifiseringen bare korrekt så lenge saken tilfeldigvis
    -- fortsatt pekte på samme generasjon.
    --
    -- Oppdraget er FROSSET og peker på nøyaktig én generasjonsrad
    -- (delindeksen `en_generasjon_per_oppdrag` gjør det strukturelt).
    -- Sakens peker brukes fortsatt til statusmaskinen — men aldri til å
    -- velge hvilken historisk binding kvitteringen gjelder.
    SELECT vg.generation, vg.status, vg.oppdrag_id, vg.krav_sett_hash,
           vg.valgt_verifikator
      INTO g FROM public.verifikasjonsgenerasjon vg
     WHERE vg.tenant = o.tenant AND vg.unntak_id = o.unntak_id
       AND vg.vilkaar = '*sett*'
       AND vg.oppdrag_id = p_oppdrag_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN 'avvist';
    END IF;

    -- LÅSEREKKEFØLGE ledd 3: oppdraget — låst, og lest PÅ NYTT.
    --
    -- Alt som klassifiserer under står på DENNE raden, ikke på det uåste
    -- oppslaget øverst. Taperen av kappløpet ser her vinnerens committede
    -- `resultathash`, ikke NULL.
    SELECT d.tenant, d.unntak_id, d.status, d.owner_claim_id,
           d.owner_generation, d.oppdragstype, d.evidensfrist, d.resultathash,
           d.repair_operation_id
      INTO o FROM public.oppdrag d WHERE d.id = p_oppdrag_id FOR UPDATE;
    IF NOT FOUND OR o.oppdragstype <> 'verifikasjon' THEN
        RETURN 'avvist';
    END IF;

    -- DE TO SIGNERTE BINDINGENE (Codex P1, runde 4).
    --
    -- Bindingen går OPPDRAG → FROSSET GENERASJONSRAD, ikke via sakens
    -- nåværende generasjon: den kan ha rykket videre, og da ville en
    -- sammenligning mot «nå» godtatt en konvolutt for feil runde.
    IF p_verification_generation IS DISTINCT FROM g.generation
       OR p_fase1_repair_operation_id IS DISTINCT FROM o.repair_operation_id THEN
        INSERT INTO public.verifikasjonskonflikt
            (tenant, unntak_id, vilkaar, generation, oppdrag_id,
             ny_resultathash, generasjonsstatus_ved_konflikt, detalj)
        VALUES (o.tenant, o.unntak_id, '*sett*', g.generation, p_oppdrag_id,
                p_resultathash, g.status,
                jsonb_build_object(
                    'grunn', 'signert_binding_avvik',
                    'konvolutt_generation', p_verification_generation,
                    'faktisk_generation', g.generation,
                    'konvolutt_fase1_id_stemmer',
                        p_fase1_repair_operation_id IS NOT DISTINCT FROM
                        o.repair_operation_id));
        RETURN 'avvist';
    END IF;

    -- Kvitteringen må gjelde NØYAKTIG det settet saken ble klassifisert
    -- mot, og komme fra den verifikatoren som ble valgt og låst.
    IF g.krav_sett_hash IS DISTINCT FROM p_krav_sett_hash
       OR g.valgt_verifikator IS DISTINCT FROM p_verifikator THEN
        INSERT INTO public.verifikasjonskonflikt
            (tenant, unntak_id, vilkaar, generation, oppdrag_id,
             ny_resultathash, generasjonsstatus_ved_konflikt, detalj)
        VALUES (o.tenant, o.unntak_id, '*sett*', g.generation, p_oppdrag_id,
                p_resultathash, g.status,
                jsonb_build_object('grunn', 'sett_eller_verifikator_avvik'));
        RETURN 'avvist';
    END IF;

    -- Eier-fencing og evidensfrist. STATUSENE står IKKE her, og det er
    -- med vilje: etter en akseptert kvittering er oppdraget `utfort` og
    -- saken har gått videre. En re-post skal da klassifiseres som
    -- `idempotent` eller `konflikt` — sto statuskontrollen foran, ville
    -- begge blitt `avvist`, og idempotensen forsvunnet i det øyeblikket
    -- den ble relevant. Statusene kontrolleres rett før den ASEPTERENDE
    -- skrivingen, der de faktisk er en forutsetning.
    IF o.owner_claim_id IS DISTINCT FROM p_owner_claim_id
       OR o.owner_generation IS DISTINCT FROM p_owner_generation
       OR now() > o.evidensfrist THEN
        RETURN 'avvist';
    END IF;

    -- IDEMPOTENS OG KONFLIKT — alltid mot den LÅSTE raden.
    IF o.resultathash IS NOT NULL THEN
        IF o.resultathash = p_resultathash THEN
            RETURN 'idempotent';
        END IF;
        INSERT INTO public.verifikasjonskonflikt
            (tenant, unntak_id, vilkaar, generation, oppdrag_id,
             akseptert_resultathash, ny_resultathash,
             generasjonsstatus_ved_konflikt, fase2_utfort, detalj)
        VALUES (o.tenant, o.unntak_id, '*sett*', g.generation, p_oppdrag_id,
                o.resultathash, p_resultathash, g.status,
                v_sak.status IN ('løst','venter_utførelse'),
                jsonb_build_object('grunn', 'motstridende_resultat'));
        RETURN 'konflikt';
    END IF;

    IF g.status <> 'aktiv' THEN
        -- Terminal generasjon UTEN en akseptert hash på dette oppdraget.
        -- Den ENESTE skriveveien setter begge i samme transaksjon, så
        -- dette skal ikke kunne oppstå — og nettopp derfor er `idempotent`
        -- feil svar. `idempotent` betyr «dette er den samme kvitteringen»,
        -- og her finnes det ingen hash å si det om.
        INSERT INTO public.verifikasjonskonflikt
            (tenant, unntak_id, vilkaar, generation, oppdrag_id,
             ny_resultathash, generasjonsstatus_ved_konflikt, detalj)
        VALUES (o.tenant, o.unntak_id, '*sett*', g.generation, p_oppdrag_id,
                p_resultathash, g.status,
                jsonb_build_object('grunn', 'terminal_generasjon_uten_hash'));
        RETURN 'konflikt';
    END IF;

    -- STATUSFENCING for den aksepterende veien (Codex, runde 4).
    -- «Positiv» skal være ÉN atomisk tilstand: et oppdrag som ikke er
    -- plukket, eller en sak som ikke venter på verifikasjon, kan ikke bli
    -- gyldig evidens ved at vi later som om den er det.
    IF o.status <> 'plukket' OR v_sak.status <> 'venter_verifikasjon' THEN
        RETURN 'avvist';
    END IF;

    -- Settet må dekkes NØYAKTIG: verken færre eller flere. Et ekstra,
    -- uventet attestert vilkår er også et avvik (v6 pkt. 3).
    SELECT array_agg(k->>'vilkaar' ORDER BY k->>'vilkaar') INTO v_krav
      FROM jsonb_array_elements(v_sak.krav_sett->'krav') AS k
     WHERE (k->>'innhentbar')::BOOLEAN;
    SELECT array_agg(r->>'vilkaar' ORDER BY r->>'vilkaar') INTO v_levert
      FROM jsonb_array_elements(p_resultater) AS r;
    IF v_krav IS DISTINCT FROM v_levert THEN
        RETURN 'avvist';
    END IF;

    -- Utfallet per vilkår. `ikke_attesterbar` med `permanent` er
    -- prinsipiell u-innhentbarhet (v7 pkt. 2) og gir manuell UTEN å bruke
    -- budsjett; uten `permanent` er den forbigående og teller som negativ.
    FOR e IN SELECT * FROM jsonb_array_elements(p_resultater) LOOP
        IF e->>'status' <> 'attestert'
           OR (e->>'gyldig_til')::TIMESTAMPTZ <= now() THEN
            v_alle_ok := false;
        END IF;
        IF e->>'status' = 'ikke_attesterbar' AND (e->>'permanent')::BOOLEAN THEN
            v_permanent := true;
        END IF;
    END LOOP;

    IF v_alle_ok THEN
        -- LÅSEREKKEFØLGE ledd 4: bevisene. Hele settet, i én transaksjon.
        FOR e IN SELECT * FROM jsonb_array_elements(p_resultater) LOOP
            INSERT INTO public.verifikasjonsbevis
                (tenant, unntak_id, vilkaar, bevis_vilkaar, generation,
                 oppdrag_id, fase1_repair_operation_id, verifikator,
                 nokkel_id, signatur, attestasjon_kryptert, key_id, nonce,
                 integritet_hash, gyldig_til)
            VALUES (o.tenant, o.unntak_id, '*sett*', e->>'vilkaar',
                    g.generation, p_oppdrag_id, o.repair_operation_id,
                    p_verifikator, p_nokkel_id, p_signatur,
                    decode(e->>'attestasjon_kryptert', 'hex'), e->>'key_id',
                    decode(e->>'nonce', 'hex'), e->>'integritet_hash',
                    (e->>'gyldig_til')::TIMESTAMPTZ)
            RETURNING id INTO v_bevis_id;
            v_siste := v_bevis_id;
        END LOOP;
        UPDATE public.verifikasjonsgenerasjon vg
           SET status = 'positiv', bevis_id = v_siste
         WHERE vg.tenant = o.tenant AND vg.unntak_id = o.unntak_id
           AND vg.vilkaar = '*sett*' AND vg.generation = g.generation
           AND vg.status = 'aktiv';
        GET DIAGNOSTICS v_treff = ROW_COUNT;
        IF v_treff <> 1 THEN
            RAISE EXCEPTION 'registrer_verifikasjonsbevis: generasjonen kunne ikke settes positiv (traff % rader)', v_treff;
        END IF;
        v_nystatus := 'verifikasjon_klar';
    ELSE
        UPDATE public.verifikasjonsgenerasjon vg SET status = 'negativ'
         WHERE vg.tenant = o.tenant AND vg.unntak_id = o.unntak_id
           AND vg.vilkaar = '*sett*' AND vg.generation = g.generation
           AND vg.status = 'aktiv';
        GET DIAGNOSTICS v_treff = ROW_COUNT;
        IF v_treff <> 1 THEN
            RAISE EXCEPTION 'registrer_verifikasjonsbevis: generasjonen kunne ikke settes negativ (traff % rader)', v_treff;
        END IF;
        IF v_permanent THEN
            v_nystatus := 'manuell';        -- prinsipielt uinnhentbart
        ELSIF g.generation < least(v_sak.maks_auto_forsok_snapshot, 3) THEN
            v_nystatus := 'verifikasjon_retry_klar';
        ELSE
            v_nystatus := 'manuell';
        END IF;
    END IF;

    -- LÅSEREKKEFØLGE ledd 5: oppdraget og saken lukkes.
    --
    -- BEGGE må treffe NØYAKTIG én rad. Sto det ingen kontroll her, kunne
    -- funksjonen sette generasjonen positiv, returnere `positiv`, og
    -- likevel etterlate oppdraget eller saken uendret — «positiv» ville
    -- vært et DELVIS resultat, og alt-eller-ingenting-kontrakten en
    -- påstand om koden framfor en egenskap ved den.
    UPDATE public.oppdrag d SET status = 'utfort', resultathash = p_resultathash
     WHERE d.id = p_oppdrag_id AND d.status = 'plukket';
    GET DIAGNOSTICS v_treff = ROW_COUNT;
    IF v_treff <> 1 THEN
        RAISE EXCEPTION 'registrer_verifikasjonsbevis: oppdraget kunne ikke lukkes (traff % rader)', v_treff;
    END IF;

    UPDATE public.unntak u SET status = v_nystatus
     WHERE u.tenant = o.tenant AND u.id = o.unntak_id
       AND u.status = 'venter_verifikasjon';
    GET DIAGNOSTICS v_treff = ROW_COUNT;
    IF v_treff <> 1 THEN
        RAISE EXCEPTION 'registrer_verifikasjonsbevis: saken kunne ikke settes % (traff % rader)', v_nystatus, v_treff;
    END IF;

    RETURN CASE WHEN v_nystatus = 'verifikasjon_klar' THEN 'positiv'
                WHEN v_permanent THEN 'permanent_uinnhentbar'
                WHEN v_nystatus = 'verifikasjon_retry_klar' THEN 'negativ'
                ELSE 'negativ_uten_budsjett' END;
END $$;
REVOKE ALL ON FUNCTION registrer_verifikasjonsbevis(
    BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, INT, INT, TEXT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION registrer_verifikasjonsbevis(
    BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, INT, INT, TEXT)
    TO disponit;

-- ------------------------------------------------------------
-- 10. Claim utvidet: de to klar-tilstandene (v2-delta pkt. 1)
--
-- Fase 2 RE-CLAIMES alltid. Kvitteringsingest opptrer aldri som arbeider
-- og forlenger aldri en lease; når beviset er lagret er fase 1 terminal og
-- all lease sluppet. Først en NY claim — ny claim_id, inkrementert
-- claim_generation — starter fase 2.
--
-- Samme claim gjennom begge asynkrone faser er forbudt, og det er ikke en
-- stilregel: fase 1 kan ta timer, og en lease som overlever den er en
-- lease som ikke lenger beviser at noen jobber.
-- ------------------------------------------------------------
-- DROP først: funksjonen får to nye OUT-kolonner (`fase`,
-- `verification_generation`), og `CREATE OR REPLACE` kan ikke endre
-- returtypen. Rettighetene settes uansett på nytt av
-- `deploy/staging/migrer.py` etter migrasjonene — den er eneste vei inn.
DROP FUNCTION IF EXISTS claim_neste_sak(TEXT, INT);
CREATE FUNCTION claim_neste_sak(p_claim_id TEXT, p_lease_s INT DEFAULT 120)
RETURNS TABLE (tenant TEXT, id BIGINT, handling TEXT, kategori TEXT,
               loggpost_id BIGINT, claim_generation INT, claim_utloper TIMESTAMPTZ,
               forsok INT, maks_auto_forsok_snapshot INT, fase TEXT,
               verification_generation INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_lease INT;
BEGIN
    IF p_claim_id IS NULL OR p_claim_id !~ '^[0-9a-f]{32,}$' THEN
        RAISE EXCEPTION 'claim_neste_sak: ugyldig claim_id-format';
    END IF;
    v_lease := least(greatest(coalesce(p_lease_s, 120), 30), 600);

    RETURN QUERY
    UPDATE public.unntak u
       SET status = 'under_behandling',
           claim_id = p_claim_id,
           claim_generation = u.claim_generation + 1,
           claim_utloper = pg_catalog.now() + (v_lease || ' seconds')::INTERVAL,
           -- Forsøkstelleren teller BEHANDLINGSforsøk. En fase-2-claim er
           -- ikke et nytt forsøk på saken — den er andre halvdel av det
           -- samme. Uten dette skillet ville en tofasesak brukt opp
           -- budsjettet sitt dobbelt så fort som en enfasesak.
           forsok = u.forsok + CASE WHEN u.status = 'ny' THEN 1 ELSE 0 END
      FROM (
        SELECT k.tenant, k.id, k.status
          FROM public.unntak k
         WHERE k.sakstype = 'normal'
           AND k.status IN ('ny','verifikasjon_klar','verifikasjon_retry_klar')
           AND (k.status <> 'ny'
                OR k.forsok < least(coalesce(k.maks_auto_forsok_snapshot, 0), 3))
           AND (SELECT pg_catalog.count(*) FROM public.unntak b
                 WHERE b.tenant = k.tenant
                   AND b.status = 'under_behandling'
                   AND b.claim_utloper > pg_catalog.now()) < 5
         -- Klar-tilstandene går FØRST: en sak som har ventet på en
         -- verifikator har allerede brukt tid, og å la den stå bak ferske
         -- saker ville gjort tofaseveien systematisk tregere enn enfase.
         ORDER BY (CASE WHEN k.status <> 'ny' THEN 0 ELSE 1 END),
                  (CASE k.prioritet WHEN 'hoy' THEN 0 ELSE 1 END), k.ts, k.id
           FOR UPDATE SKIP LOCKED
         LIMIT 1) k
     WHERE u.tenant = k.tenant AND u.id = k.id
    RETURNING u.tenant, u.id, u.handling, u.kategori, u.loggpost_id,
              u.claim_generation, u.claim_utloper, u.forsok,
              u.maks_auto_forsok_snapshot,
              -- FASEN FØLGER STATUSEN, ikke generasjonstelleren.
              --
              -- MÅLT: med `verification_generation = 0 => ny, ellers fase2`
              -- rapporterte en sak i `verifikasjon_retry_klar` fase2, og
              -- arbeideren lette etter et positivt bevis som per definisjon
              -- ikke fantes — retryen ga `manuell: intet_positivt_bevis`.
              -- Retry-veien kunne dermed ALDRI åpne en ny generasjon, selv
              -- om både statusmaskinen og kommentarene sa at den skulle.
              --
              -- Statusen er den autoritative fasen: `verifikasjon_klar`
              -- betyr «et bevis foreligger», `verifikasjon_retry_klar`
              -- betyr «forrige runde slo feil, kjør en ny». Telleren sier
              -- bare hvor mange runder som har vært.
              --
              -- `k` er raden slik den var FØR UPDATE-en; `u` ville gitt
              -- `under_behandling` for alle tre.
              CASE k.status WHEN 'verifikasjon_klar' THEN 'fase2'
                            WHEN 'verifikasjon_retry_klar' THEN 'retry'
                            ELSE 'ny' END,
              u.verification_generation;
END $$;
REVOKE ALL ON FUNCTION claim_neste_sak(TEXT, INT) FROM PUBLIC;

-- ------------------------------------------------------------
-- 11. Start en verifikasjonsgenerasjon — KUN arbeideren (v4-delta pkt. 1)
--
-- Ingest oppretter ALDRI et oppdrag. Utløpsjobben heller ikke. Bare den
-- claimede arbeideren, og bare under gyldig fencing. Uten det skillet
-- ville to komponenter kunnet opprette hvert sitt oppdrag for samme
-- generasjon, og delindeksen ville avvist den ene med en unikfeil i
-- stedet for at rekkefølgen var riktig i utgangspunktet.
--
-- Monoton +1, og aldri fra en terminal tilstand.
-- ------------------------------------------------------------
-- Begge signaturene droppes: den GAMLE (5 argumenter) fordi returtypen
-- endres, og den NYE fordi migrasjonen må kunne kjøres to ganger. Bare den
-- gamle sto her først, og en gjenkjøring falt på «already exists with same
-- argument types» — samme idempotensfelle som kostet 80 tester i PR-006.
DROP FUNCTION IF EXISTS start_verifikasjonsgenerasjon(TEXT, BIGINT, TEXT, INT, TEXT);
DROP FUNCTION IF EXISTS start_verifikasjonsgenerasjon(TEXT, BIGINT, TEXT, INT, JSONB, TEXT, TEXT, TEXT);
CREATE FUNCTION start_verifikasjonsgenerasjon(
        p_tenant TEXT, p_unntak_id BIGINT, p_claim_id TEXT,
        p_claim_generation INT, p_krav_sett JSONB, p_krav_sett_hash TEXT,
        p_valgt_verifikator TEXT, p_autoritetsversjon TEXT)
RETURNS INT
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_ny  INT;
    v_sak RECORD;
BEGIN
    -- LÅSEREKKEFØLGE ledd 1: saken. Full fencing-WHERE.
    SELECT u.verification_generation, u.maks_auto_forsok_snapshot, u.status
      INTO v_sak
      FROM public.unntak u
     WHERE u.tenant = p_tenant AND u.id = p_unntak_id
       AND u.claim_id = p_claim_id
       AND u.claim_generation = p_claim_generation
       AND u.status = 'under_behandling'
       AND u.claim_utloper > pg_catalog.now()
       FOR UPDATE;
    IF NOT FOUND THEN
        RETURN NULL;               -- tapt lease: ingen generasjon
    END IF;

    v_ny := v_sak.verification_generation + 1;
    -- GO-vilkår V5: totalt maks `maks_auto_forsok_snapshot` generasjoner.
    IF v_ny > least(coalesce(v_sak.maks_auto_forsok_snapshot, 0), 3) THEN
        RETURN NULL;
    END IF;

    -- Settet fryses ved FØRSTE generasjon og røres aldri igjen. En senere
    -- generasjon verifiserer NØYAKTIG samme sett på nytt — den patcher
    -- aldri et gammelt (v7 pkt. 3).
    UPDATE public.unntak u
       SET krav_sett = COALESCE(u.krav_sett, p_krav_sett),
           verification_generation = v_ny
     WHERE u.tenant = p_tenant AND u.id = p_unntak_id;

    INSERT INTO public.verifikasjonsgenerasjon
        (tenant, unntak_id, vilkaar, generation, valgt_verifikator,
         autoritetsregister_versjon, krav_sett_hash)
    VALUES (p_tenant, p_unntak_id, '*sett*', v_ny, p_valgt_verifikator,
            p_autoritetsversjon, p_krav_sett_hash);
    RETURN v_ny;
END $$;
REVOKE ALL ON FUNCTION start_verifikasjonsgenerasjon(TEXT, BIGINT, TEXT, INT, JSONB, TEXT, TEXT, TEXT)
    FROM PUBLIC;

-- Knytt generasjonen til oppdraget den bestilte (fenced).
CREATE OR REPLACE FUNCTION knytt_verifikasjonsoppdrag(
        p_tenant TEXT, p_unntak_id BIGINT, p_vilkaar TEXT, p_generation INT,
        p_oppdrag_id BIGINT)
RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE v_treff INT;
BEGIN
    UPDATE public.verifikasjonsgenerasjon vg
       SET oppdrag_id = p_oppdrag_id
     WHERE vg.tenant = p_tenant AND vg.unntak_id = p_unntak_id
       AND vg.vilkaar = p_vilkaar AND vg.generation = p_generation
       AND vg.status = 'aktiv' AND vg.oppdrag_id IS NULL;
    GET DIAGNOSTICS v_treff = ROW_COUNT;
    RETURN v_treff = 1;
END $$;
REVOKE ALL ON FUNCTION knytt_verifikasjonsoppdrag(TEXT, BIGINT, TEXT, INT, BIGINT)
    FROM PUBLIC;

-- ===========================================================================
-- 9. Kapabiliteten bærer den ORIGINALE aktørrollen — M-37 har ingen egen
-- ===========================================================================
--
-- MÅLT, ikke antatt: fire-prosess-rundturen kom hele veien til fase 2, bygde
-- hendelsen med det komplette settet, spurte motoren — og fikk `UNNTAK` med
-- `rolle_ikke_tillatt`. Grunnen var at pre-auth ga arbeidskapabiliteten
-- rollen `'m37'`, og ingen policy har `m37` i `tillatt_for`. Det var ikke en
-- skrivefeil: `m37` var en rolle systemet fant på for seg selv.
--
-- Å legge `m37` inn i kundenes policyer ville løst symptomet ved å gi M-37
-- en EGEN fullmakt — nøyaktig det invarianten «null egne fullmakter»
-- forbyr. Riktig retning er motsatt: reparasjonen er den SAMME handlingen
-- den opprinnelige aktøren allerede hadde fullmakt til, og M-37 utfører den
-- på dens vegne. Rollen skal derfor komme fra sakens egen reviderte
-- loggpost, fryses ved utstedelsen og aldri kunne velges av arbeideren.
--
-- Kolonnen er NULLBAR med vilje: gamle kapabiliteter (utstedt før denne
-- migrasjonen) har ingen rolle, og en DEFAULT ville gitt dem en oppdiktet
-- én. Pre-auth feiler i stedet lukket på NULL — en fullmakt uten en
-- registrert rolle kan ikke brukes til noe.
ALTER TABLE arbeidskapabiliteter ADD COLUMN IF NOT EXISTS aktor_rolle TEXT;

-- Rollen er et BINDINGSFELT: like uforanderlig som handlingen og claimen.
-- Kunne den endres etter utstedelse, ville hele poenget falt bort.
CREATE OR REPLACE FUNCTION kapabilitet_statusmaskin()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.jti IS DISTINCT FROM OLD.jti
       OR NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.unntak_id IS DISTINCT FROM OLD.unntak_id
       OR NEW.claim_id IS DISTINCT FROM OLD.claim_id
       OR NEW.claim_generation IS DISTINCT FROM OLD.claim_generation
       OR NEW.repair_operation_id IS DISTINCT FROM OLD.repair_operation_id
       OR NEW.tillatt_handling IS DISTINCT FROM OLD.tillatt_handling
       OR NEW.aktor_rolle IS DISTINCT FROM OLD.aktor_rolle
       OR NEW.utloper IS DISTINCT FROM OLD.utloper
       OR NEW.utstedt IS DISTINCT FROM OLD.utstedt THEN
        RAISE EXCEPTION 'arbeidskapabiliteter: identitets- og bindingsfelter er uforanderlige';
    END IF;
    IF NOT (
        (OLD.status = 'utstedt'   AND NEW.status IN ('reservert','feilet')) OR
        (OLD.status = 'reservert' AND NEW.status IN ('brukt','feilet','reservert')) OR
        (OLD.status = NEW.status)
    ) THEN
        RAISE EXCEPTION 'arbeidskapabiliteter: ulovlig overgang % -> %',
            OLD.status, NEW.status;
    END IF;
    IF OLD.status IN ('brukt','feilet') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'arbeidskapabiliteter: % er terminal', OLD.status;
    END IF;
    RETURN NEW;
END $$;

-- Utstedelsen henter rollen fra sakens EGEN loggpost. Ikke fra en parameter
-- — arbeideren skal ikke kunne uttrykke ønsket rolle i det hele tatt, av
-- samme grunn som handlingen ikke er en parameter (v4-delta pkt. 1).
-- Returtypen får en kolonne til (`aktor_rolle`), og `CREATE OR REPLACE`
-- kan ikke endre den. DROP først — signaturen er uendret, så den nye
-- droppes like godt som den gamle, og migrasjonen tåler en gjenkjøring.
DROP FUNCTION IF EXISTS utsted_arbeidskapabilitet(TEXT, INT, TEXT, INT);
CREATE FUNCTION utsted_arbeidskapabilitet(
        p_claim_id TEXT, p_claim_generation INT, p_jti TEXT,
        p_levetid_s INT DEFAULT 60)
RETURNS TABLE (jti TEXT, tenant TEXT, unntak_id BIGINT,
               tillatt_handling TEXT, repair_operation_id TEXT,
               utloper TIMESTAMPTZ, aktor_rolle TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    r RECORD;
    v_utloper TIMESTAMPTZ;
BEGIN
    IF p_jti IS NULL OR p_jti !~ '^[0-9a-f]{32,}$' THEN
        RAISE EXCEPTION 'utsted_arbeidskapabilitet: ugyldig jti-format';
    END IF;
    IF p_claim_id IS NULL OR p_claim_id !~ '^[0-9a-f]{32,}$' THEN
        RAISE EXCEPTION 'utsted_arbeidskapabilitet: ugyldig claim_id-format';
    END IF;

    SELECT u.tenant, u.id, u.claim_utloper, o.repair_operation_id,
           o.maalhandling, l.aktor AS opprinnelig_rolle
      INTO r
      FROM public.unntak u
      JOIN public.reparasjonsoperasjoner o
        ON o.tenant = u.tenant AND o.unntak_id = u.id AND o.status = 'aktiv'
      JOIN public.revisjonslogg l
        ON l.tenant = u.tenant AND l.id = u.loggpost_id
     WHERE u.claim_id = p_claim_id
       AND u.claim_generation = p_claim_generation
       AND u.status = 'under_behandling'
       AND u.claim_utloper > pg_catalog.now();
    IF NOT FOUND THEN
        RETURN;   -- tapt lease eller ingen klassifisering: ingen kapabilitet
    END IF;
    IF r.opprinnelig_rolle IS NULL OR btrim(r.opprinnelig_rolle) = '' THEN
        -- Saken har ingen registrert opprinnelig rolle. Da finnes det ingen
        -- fullmakt å handle på vegne av, og en oppdiktet er verre enn ingen.
        RETURN;
    END IF;

    v_utloper := least(
        pg_catalog.now() + (least(greatest(
            coalesce(p_levetid_s, 60), 5), 300) || ' seconds')::INTERVAL,
        r.claim_utloper);

    INSERT INTO public.arbeidskapabiliteter
        (jti, tenant, unntak_id, claim_id, claim_generation,
         repair_operation_id, tillatt_handling, aktor_rolle, utloper)
    VALUES (p_jti, r.tenant, r.id, p_claim_id, p_claim_generation,
            r.repair_operation_id, r.maalhandling, r.opprinnelig_rolle,
            v_utloper);

    jti := p_jti;
    tenant := r.tenant;
    unntak_id := r.id;
    tillatt_handling := r.maalhandling;
    repair_operation_id := r.repair_operation_id;
    utloper := v_utloper;
    aktor_rolle := r.opprinnelig_rolle;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION utsted_arbeidskapabilitet(TEXT, INT, TEXT, INT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION utsted_arbeidskapabilitet(TEXT, INT, TEXT, INT)
    TO disponit;

DROP FUNCTION IF EXISTS reserver_kapabilitet(TEXT, TEXT, INT);
CREATE FUNCTION reserver_kapabilitet(p_jti TEXT, p_request_id TEXT,
                                     p_reservasjon_s INT DEFAULT 300)
RETURNS TABLE (tenant TEXT, unntak_id BIGINT, tillatt_handling TEXT,
               repair_operation_id TEXT, claim_id TEXT, claim_generation INT,
               aktor_rolle TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF p_jti IS NULL OR p_request_id IS NULL OR length(btrim(p_request_id)) = 0 THEN
        RETURN;
    END IF;
    RETURN QUERY
    UPDATE public.arbeidskapabiliteter k
       SET status = 'reservert',
           request_id = p_request_id,
           reservert_ts = pg_catalog.now(),
           reservasjon_utloper = least(
               pg_catalog.now() + (least(greatest(
                   coalesce(p_reservasjon_s, 300), 30), 300)
                   || ' seconds')::INTERVAL,
               k.utloper)
     WHERE k.jti = p_jti
       AND k.utloper > pg_catalog.now()
       -- Fail-closed på en kapabilitet uten registrert rolle: den er
       -- utstedt før rollen ble et bindingsfelt, og kan ikke brukes.
       AND k.aktor_rolle IS NOT NULL
       AND (k.status = 'utstedt'
            OR (k.status = 'reservert' AND k.request_id = p_request_id))
    RETURNING k.tenant, k.unntak_id, k.tillatt_handling,
              k.repair_operation_id, k.claim_id, k.claim_generation,
              k.aktor_rolle;
END $$;
REVOKE ALL ON FUNCTION reserver_kapabilitet(TEXT, TEXT, INT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION reserver_kapabilitet(TEXT, TEXT, INT) TO disponit;

RESET ROLE;
