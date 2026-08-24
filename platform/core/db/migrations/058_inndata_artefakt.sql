-- 058: inndata-artefaktkontrakten (#162) — buntens vei INN.
--
-- Speiler 017s form i motsatt retning: 017 er UTDATA (modulen leverer
-- en rapport med en kapabilitet utstedt VED claim); dette er INNDATA
-- (kunden laster opp en bunt med en reservasjon utstedt FØR claim, og
-- bestillingen BINDER den). Tre tilstander, tre dører:
--
--   reservert --(registrer_inndata_lastet)--> lastet
--   lastet    --(bind_inndata, i bestillingens tx)--> bundet
--
-- * `maks_bytes` er DEKLARERT ved reservasjonen og håndhevet både i
--   strømmen (middleware-tellingen) og her (faktiske_bytes <= maks) —
--   arkivgatens arbeid blir dermed bundet av et tall noen har signert
--   for (#162s hele poeng).
-- * Payloaden bor på FILSYSTEMET (kryptert med tenant-DEK av API-et);
--   raden bærer metadata + kryptoreferansen. En rad uten fil er en
--   død referanse og fanges av resolverens lesing, aldri av tillit.
-- * Eid av `disponit_domene_eier` (017-formen): all skriving går via
--   de herdede funksjonene; runtime har KUN EXECUTE.
--
-- v1-GRENSE (bevisst, dokumentert i kontrakt/KONTRAKT.md): fysisk tak
-- 64 MiB per bunt — engangs-kryptering i minnet. Chunket kryptering for
-- fullskala (opptil ~1 GiB fysisk) er egen maskin med eget issue; taket
-- står i `INNDATA_MAKS_FYSISK` her og i api/inndata.py og MÅLES likt.

CREATE TABLE inndata_artefakt (
    tenant TEXT NOT NULL,
    inndata_id UUID NOT NULL DEFAULT gen_random_uuid(),
    -- Lukket sett også i SQL (Cursor P2-3): `formaal` var CHECKet,
    -- `eiermodul` ikke — og `disponit` har EXECUTE på `reserver_inndata`,
    -- så en annen (eller buggy) kaller kunne reservere en bunt for en
    -- vilkårlig modulstreng. En ny eiermodul er en KONTRAKTSENDRING og
    -- hører hjemme i en migrasjon, ikke i et kallargument.
    eiermodul TEXT NOT NULL CHECK (eiermodul IN ('m57_ats')),
    formaal TEXT NOT NULL CHECK (formaal IN ('soknadsbunt')),
    innholdstype TEXT NOT NULL CHECK (innholdstype IN ('application/zip')),
    maks_bytes BIGINT NOT NULL CHECK (maks_bytes > 0
                                      AND maks_bytes <= 64 * 1024 * 1024),
    faktiske_bytes BIGINT,
    innhold_sha256 TEXT,
    key_id TEXT,
    nonce BYTEA,
    lager_sti TEXT,
    status TEXT NOT NULL DEFAULT 'reservert'
        CHECK (status IN ('reservert', 'lastet', 'bundet', 'forkastet')),
    reservasjon_jti TEXT NOT NULL CHECK (reservasjon_jti ~ '^[0-9a-f]{32,}$'),
    -- Klientens idempotensnøkkel (Codex P2). Reservasjonen er en
    -- OPPRETTELSE som ikke er naturlig idempotent: gikk 201-svaret tapt på
    -- veien ut — eller ga `conn.commit()` en tvetydig forbindelsesfeil —
    -- hadde klienten ingenting å slå opp den genererte `inndata_ref` og
    -- jti-en med. En retry laget da en ANDRE levende reservasjon med en
    -- annen referanse, mens den første ble uleselig for alle helt til
    -- reaperen tok den. Nøkkelen er derfor RADENS, ikke en sidetabells:
    -- hele svaret er utledbart av raden (id, jti, maks_bytes), så et
    -- lagret svarobjekt ville vært en andre representasjon av det samme.
    -- Grensene speiler `bestilling_idempotens` (038 §3).
    idempotensnokkel TEXT NOT NULL CHECK (length(idempotensnokkel)
                                          BETWEEN 8 AND 200),
    oppdrag_id BIGINT,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    utloper TIMESTAMPTZ NOT NULL,
    lastet_ts TIMESTAMPTZ,
    bundet_ts TIMESTAMPTZ,
    CONSTRAINT inndata_artefakt_pk PRIMARY KEY (tenant, inndata_id),
    CONSTRAINT inndata_jti_en_gang UNIQUE (tenant, reservasjon_jti),
    -- Én nøkkel, én reservasjon — per tenant. Dette er også konfliktmålet
    -- `reserver_inndata` bruker, så gjenspill og kappløp går samme vei.
    CONSTRAINT inndata_idempotens_unik UNIQUE (tenant, idempotensnokkel),
    CONSTRAINT inndata_oppdrag_fk FOREIGN KEY (tenant, oppdrag_id)
        REFERENCES oppdrag (tenant, id),
    -- DEK-referansen bindes som i 003/005/007/011/016 (Codex P2): uten
    -- den kunne `registrer_inndata_lastet` — som tar `key_id` fra en
    -- runtime-kaller — lande en `lastet` rad med en ukjent eller
    -- KRYSSTENANT nøkkel-id. Raden ville sett komplett ut, og først
    -- resolveren i PR-2 oppdaget at ciphertexten ikke kan dekrypteres av
    -- noen. NULL i `key_id` (en `reservert` rad) håndheves ikke av en
    -- sammensatt FK, som er nøyaktig riktig her.
    CONSTRAINT inndata_dek_fk FOREIGN KEY (tenant, key_id)
        REFERENCES tenant_nokler (tenant, key_id),
    -- Tilstanden bærer feltene sine, totalt (SP-5-formen): en lastet rad
    -- HAR måling+krypto+sti; en bundet rad HAR oppdraget; en reservert
    -- har ingen av delene.
    CONSTRAINT inndata_tilstand_totalt CHECK (
        (status = 'reservert' AND faktiske_bytes IS NULL
         AND innhold_sha256 IS NULL AND lager_sti IS NULL
         AND oppdrag_id IS NULL AND lastet_ts IS NULL
         AND bundet_ts IS NULL)
     OR (status = 'lastet' AND faktiske_bytes IS NOT NULL
         AND innhold_sha256 IS NOT NULL AND lager_sti IS NOT NULL
         AND key_id IS NOT NULL AND nonce IS NOT NULL
         AND oppdrag_id IS NULL AND lastet_ts IS NOT NULL
         AND bundet_ts IS NULL)
     -- `bundet` er en `lastet` som HAR fått et oppdrag — den mister ikke
     -- krypto eller lastetidspunkt på veien (Cursor P2-1). Grenen krevde
     -- dem ikke, så CHECKen var ikke «totalt» slik kommentaren lovte;
     -- write-once-vakten lukket veien gjennom dørene, men en CHECK som
     -- bare gjelder halve tilstandsrommet er 016-klassen om igjen.
     OR (status = 'bundet' AND faktiske_bytes IS NOT NULL
         AND innhold_sha256 IS NOT NULL AND lager_sti IS NOT NULL
         AND key_id IS NOT NULL AND nonce IS NOT NULL
         AND lastet_ts IS NOT NULL
         AND oppdrag_id IS NOT NULL AND bundet_ts IS NOT NULL)
     -- `forkastet` var en TOM gren (Cursor P2) — samme «CHECK som ikke er
     -- total» som `bundet` hadde. En forkasting kunne derfor sette
     -- `oppdrag_id` i samme UPDATE og STJELE plassen i
     -- `inndata_artefakt_oppdrag` foran en ekte `bind_inndata`: bunten var
     -- kastet, oppdraget hadde brukt opp sin ene bunteplass, og lineage
     -- pekte på ingenting. Reaperen som skal skrive denne overgangen
     -- kommer i egen PR — invarianten må stå før døren.
     OR (status = 'forkastet' AND oppdrag_id IS NULL
         AND bundet_ts IS NULL)),
    CONSTRAINT inndata_maaling_innenfor CHECK (
        faktiske_bytes IS NULL OR
        (faktiske_bytes > 0 AND faktiske_bytes <= maks_bytes)),
    -- Kryptostrukturen på TABELLEN (016:137/017:252-formen, Cursor P2-6):
    -- `registrer_inndata_lastet` tar `nonce` og `lager_sti` fra en
    -- runtime-kaller. Uten denne kunne et direkte kall lagre `nonce='\x'`
    -- eller en avkortet nonce — verdier AES-GCM aldri kan dekryptere —
    -- brenne jti-en og lande som `lastet`. Invarianten kommer fra
    -- db/kryptering.py: 12-byte nonce, som overalt ellers i repoet.
    CONSTRAINT inndata_krypto_struktur CHECK (
        (nonce IS NULL OR octet_length(nonce) = 12)
        AND (lager_sti IS NULL OR length(btrim(lager_sti)) > 0)),
    -- FS-NAVNEROMMET er tenantens (Cursor P1). `lager_sti` hadde bare
    -- «ikke tom», mens `registrer_inndata_lastet` tar den fra en
    -- runtime-kaller med EXECUTE. En kaller kunne dermed lande en `lastet`
    -- rad som PEKER inn i en annen tenants katalog — eller ut av lageret
    -- med `..` — og reaperen (egen PR), hvis hele jobb er å `unlink`
    -- stien raden bærer, ville utført slettingen. Nonce-hullet var
    -- udekrypterbare data; dette er isolasjonsbrudd med sletting på
    -- enden.
    --
    -- STIEN ER RELATIV: `<tenant>/<uuid>.bin`, uten rot (Cursor P1 runde
    -- 2). Første forsøk lagret den absolutt og lette etter
    -- `'/' || tenant || '/'` som DELSTRENG — og en delstreng har ingen
    -- ende: `<rot>/<offer>/<tenant>/x.bin` inneholder `/<tenant>/` og
    -- passerte, altså en peker ned i offerets katalog. Å bytte
    -- delstrengen mot et prefiks krever at basen KJENNER roten, og roten
    -- er `INNDATA_ROT` i API-et, ikke noe SQL har. Å flytte den hit ville
    -- vært å bygge en ny maskin for en verdi bare kalleren eier.
    --
    -- Uten roten i raden finnes ikke spørsmålet: første ledd ER tenanten,
    -- sammenlignet som streng (ikke LIKE — et tenantnavn med `_` er et
    -- LIKE-jokertegn og ville sluppet naboen inn igjen), og etter det
    -- ledd-skillet er det ingen flere `/`. En rad kan dermed ikke uttrykke
    -- noe utenfor sin egen tenantkatalog, uansett hva roten er. API-et
    -- setter roten på når det åpner filen, og reaperen gjør det samme.
    -- Målingen bærer sin egen form (Cursor P2, 049/053/054-klassen):
    -- `innhold_sha256` var bare NOT NULL i `lastet`-grenen, mens
    -- `registrer_inndata_lastet` tar verdien fra en runtime-kaller. `''`
    -- eller `'nei'` kunne dermed brenne jti-en og lande som `lastet` med
    -- en hash ingen resolver kan stole på — og replay-armen sammenligner
    -- nettopp mot den, altså mot søppel. Samme regex som søskentabellene.
    CONSTRAINT inndata_sha256_format CHECK (
        innhold_sha256 IS NULL OR innhold_sha256 ~ '^[0-9a-f]{64}$'),
    -- Traverseringen måles på KOMPONENTENE, ikke på den sammensatte
    -- strengen (Codex P2, runde 7). `position('..' in lager_sti) = 0`
    -- forbød `..` hvor som helst — også inne i en helt lovlig tenant-ID
    -- som `acme..corp`, som `brukermedlemskap.tenant` (ubegrenset TEXT)
    -- tillater og `api/inndata.py:_stikomponent` godtar som én trygg
    -- stikomponent. Reservasjonen gikk da gjennom mens hver opplasting
    -- ble slettet igjen og svarte `inndata_reservasjon_ugyldig`: tenanten
    -- kunne aldri laste opp noe.
    --
    -- Formen er `<tenant>/<filnavn>`, og traversering kan bare komme fra
    -- ett av de to leddene: tenanten selv (`../x` ville flyttet hele
    -- navnerommet) eller filnavnet (`..`). Begge måles der de bor. Samme
    -- positive form som `_stikomponent` i API-et; NUL kan ikke finnes i
    -- Postgres' TEXT.
    CONSTRAINT inndata_lagersti_navnerom CHECK (
        lager_sti IS NULL OR (
            tenant NOT IN ('.', '..')
            AND position('/' in tenant) = 0
            AND left(lager_sti, length(tenant) + 1) = tenant || '/'
            AND length(lager_sti) > length(tenant) + 1
            AND position('/' in substr(lager_sti, length(tenant) + 2)) = 0
            AND substr(lager_sti, length(tenant) + 2)
                NOT IN ('.', '..'))),
    -- ÉN FIL, ÉN RAD (Codex P1). Navnerommet over sier hvor stien kan
    -- peke, ikke at ingen andre rad peker samme sted. `disponit` har
    -- SELECT på tabellen og EXECUTE på `registrer_inndata_lastet`, så en
    -- kaller kunne lese en eksisterende rads sti, hash, key_id og nonce og
    -- registrere sin EGEN reservasjon på nøyaktig dem. Da bar to
    -- «engangs»-artefakter den samme fysiske bunten: de kan bindes til
    -- hvert sitt oppdrag (indeksen under er per oppdrag, ikke per fil), og
    -- ryddingen av den ene sletter ciphertexten den andre fortsatt
    -- refererer. Aliaset er heller ikke synlig i noen av de andre
    -- invariantene — begge radene ser komplette ut.
    --
    -- Dette er den ene invarianten som IKKE kan stå som en CHECK og
    -- dermed heller ikke som en guard i funksjonen: en forhåndssjekk mot
    -- de andre radene har et kappløpsvindu, mens UNIQUE er den samme
    -- avgjørelsen tatt av indeksen. Kollisjonen når API-et som
    -- `unique_violation`, altså den kanoniske `inndata_alt_lastet` (409),
    -- og den ærlige veien treffer den aldri: stien er en fersk uuid per
    -- kall. NULL-er er distinkte i Postgres, så reservasjoner uten sti er
    -- like mange som før.
    CONSTRAINT inndata_lagersti_unik UNIQUE (tenant, lager_sti)
);
-- «Én bunt, ett oppdrag» er en INVARIANT, ikke en kommentar (Cursor P1-3):
-- uten UNIQUE kunne to `lastet`-rader bindes til det samme oppdraget, og
-- lineage forgrenet seg — to bunter bak én bestilling, uten at noe sier
-- hvilken som gjelder. Partial fordi `oppdrag_id` er NULL helt til
-- bindingen skjer, og reservasjoner/ubundne bunter er mange.
CREATE UNIQUE INDEX inndata_artefakt_oppdrag
    ON inndata_artefakt (tenant, oppdrag_id) WHERE oppdrag_id IS NOT NULL;
-- Reaperens utvalg (ubundne som løp ut): partial på status+utloper.
CREATE INDEX inndata_artefakt_utlop
    ON inndata_artefakt (utloper) WHERE status IN ('reservert', 'lastet');

-- Statusmaskinen (017-formen): bindingsfelter immutable; overgangene er
-- nøyaktig de tre pilene + forkasting av ureist reservasjon/utløpt
-- lastet; DELETE/TRUNCATE aldri.
CREATE OR REPLACE FUNCTION inndata_artefakt_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'inndata_artefakt: % avvist', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.inndata_id IS DISTINCT FROM OLD.inndata_id
       OR NEW.eiermodul IS DISTINCT FROM OLD.eiermodul
       OR NEW.formaal IS DISTINCT FROM OLD.formaal
       OR NEW.innholdstype IS DISTINCT FROM OLD.innholdstype
       OR NEW.maks_bytes IS DISTINCT FROM OLD.maks_bytes
       OR NEW.reservasjon_jti IS DISTINCT FROM OLD.reservasjon_jti
       OR NEW.idempotensnokkel IS DISTINCT FROM OLD.idempotensnokkel
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.utloper IS DISTINCT FROM OLD.utloper THEN
        RAISE EXCEPTION 'inndata_artefakt: bindingsfeltene er immutable'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT ((OLD.status = 'reservert' AND NEW.status IN ('lastet',
                                                         'forkastet'))
         OR (OLD.status = 'lastet' AND NEW.status IN ('bundet',
                                                      'forkastet'))) THEN
        RAISE EXCEPTION 'inndata_artefakt: overgang % -> % finnes ikke',
            OLD.status, NEW.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Bindingen er BINDINGENS (Cursor P2): `oppdrag_id` var hverken
    -- bindingsfelt eller write-once, så enhver skrivevei med UPDATE kunne
    -- sette den — også en forkasting, som dermed kunne ta plassen i den
    -- unike indeksen foran `bind_inndata`. Kolonnen kan nå bare endres i
    -- nøyaktig den overgangen `bind_inndata` gjør.
    IF NEW.oppdrag_id IS DISTINCT FROM OLD.oppdrag_id
       AND NOT (OLD.status = 'lastet' AND NEW.status = 'bundet') THEN
        RAISE EXCEPTION 'inndata_artefakt: oppdrag_id settes kun i'
            ' overgangen lastet -> bundet'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Målingene er write-once: satt ved 'lastet', aldri endret siden.
    IF OLD.status <> 'reservert' AND (
           NEW.faktiske_bytes IS DISTINCT FROM OLD.faktiske_bytes
        OR NEW.innhold_sha256 IS DISTINCT FROM OLD.innhold_sha256
        OR NEW.key_id IS DISTINCT FROM OLD.key_id
        OR NEW.nonce IS DISTINCT FROM OLD.nonce
        OR NEW.lager_sti IS DISTINCT FROM OLD.lager_sti
        OR NEW.lastet_ts IS DISTINCT FROM OLD.lastet_ts) THEN
        RAISE EXCEPTION 'inndata_artefakt: målingene er write-once'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS inndata_artefakt_vakt ON inndata_artefakt;
CREATE TRIGGER inndata_artefakt_vakt
    BEFORE UPDATE OR DELETE ON inndata_artefakt
    FOR EACH ROW EXECUTE FUNCTION inndata_artefakt_vakt();
DROP TRIGGER IF EXISTS inndata_artefakt_ingen_truncate ON inndata_artefakt;
CREATE TRIGGER inndata_artefakt_ingen_truncate
    BEFORE TRUNCATE ON inndata_artefakt
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

ALTER TABLE inndata_artefakt ENABLE ROW LEVEL SECURITY;
ALTER TABLE inndata_artefakt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON inndata_artefakt
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- ------------------------------------------------------------
-- Dørene. Eid av domene_eier (017-formen); rettighetene INNE i blokka
-- (PUBLIC-EXECUTE-læren fra #140). Eieren av tabellen (migrator) gir
-- dør-eieren radrettighetene først — vaktene snevrer dem til
-- statusmaskinens overganger, også for denne rollen.
GRANT SELECT, INSERT, UPDATE ON inndata_artefakt TO disponit_domene_eier;

SET LOCAL ROLE disponit_domene_eier;

-- Reservasjonen: utstedes av bestillingsflaten FØR opplasting. Taket er
-- kontraktens — kunden ber aldri om et tall, hun får kontraktens.
CREATE FUNCTION reserver_inndata(
    p_tenant TEXT, p_eiermodul TEXT, p_formaal TEXT, p_maks_bytes BIGINT,
    p_idempotensnokkel TEXT)
RETURNS TABLE (inndata_id UUID, reservasjon_jti TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID; v_jti TEXT; r RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'reserver_inndata');
    -- ISOLASJONSPORTEN (Cursor P2, runde 8) — samme som 056:821-827 og
    -- 057:879-886, og den mangler her.
    --
    -- Idempotensløftet under («samme nøkkel ⇒ samme reservasjon») er
    -- utledet av en LESNING: `ON CONFLICT DO NOTHING` svelger taperens
    -- unik-brudd uten feil, og gjenlesningen rett etter MÅ se vinnerens
    -- rad. Under REPEATABLE READ eller SERIALIZABLE står transaksjonens
    -- snapshot fast fra første setning, så gjenlesningen er blind for en
    -- samtidig committet reservasjon: `v_id` blir NULL, `NOT FOUND`
    -- treffer, og «idempotenskonflikt uten lesbar rad» reises for en
    -- tilstand som IKKE er den feilen beskriver. `api/inndata.py` mapper
    -- `unique_violation` til 409 `idempotenskonflikt`, så et helt legitimt
    -- retry får «nøkkelen er brukt for en ANNEN reservasjon» der
    -- kontrakten lover 201 med den samme referansen tilbake.
    --
    -- READ COMMITTED er det eneste nivået der hver setning ser ferske
    -- data. `read uncommitted` er med fordi PostgreSQL BEHANDLER det som
    -- READ COMMITTED (nivået finnes bare som synonym). Poolen kjører i
    -- dag på basens default, altså READ COMMITTED — porten er derfor
    -- ingen oppførselsendring for HTTP-veien, men `disponit` har EXECUTE
    -- her, og en fremtidig kaller som setter nivået selv skal møte en
    -- ærlig feil framfor et brutt løfte.
    IF current_setting('transaction_isolation')
       NOT IN ('read committed', 'read uncommitted') THEN
        RAISE EXCEPTION 'reserver_inndata: krever READ COMMITTED (fikk %)'
            ' — idempotensløftet er utledet av en LESNING etter konflikt,'
            ' og et fastholdt snapshot gjør den blind for en samtidig'
            ' committet reservasjon',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    -- Speiler tabellens CHECK, men med det kanoniske feilkontraktet i
    -- stedet for check_violation (Cursor P2-3, 017-formen).
    IF p_eiermodul IS DISTINCT FROM 'm57_ats'
       OR p_formaal IS DISTINCT FROM 'soknadsbunt' THEN
        RAISE EXCEPTION 'reserver_inndata: %/% er ikke kontraktens'
            ' kombinasjon', p_eiermodul, p_formaal
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_idempotensnokkel IS NULL
       OR length(p_idempotensnokkel) NOT BETWEEN 8 AND 200 THEN
        RAISE EXCEPTION 'reserver_inndata: idempotensnøkkelen mangler eller'
            ' er utenfor 8..200'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Kjerne-PG, ingen pgcrypto (kjørerens egen regel): to UUID-er gir
    -- 64 hex-tegn entropi for engangs-jti-en.
    v_jti := replace(pg_catalog.gen_random_uuid()::text, '-', '')
             || replace(pg_catalog.gen_random_uuid()::text, '-', '');
    -- `ON CONFLICT ... DO NOTHING` og ikke et oppslag FØRST (038-formen,
    -- Codex P2): oppslag-så-insert har et vindu mellom de to der to
    -- samtidige retryer begge ser «ingen rad» og begge oppretter. Med
    -- konflikten som port taper nøyaktig én av dem, og taperen leser
    -- vinnerens rad under. Målet er navngitt, så en jti-kollisjon (som
    -- ikke kan skje med 128 bit entropi, men som ville vært en ekte feil)
    -- fortsatt reiser i stedet for å bli stille gjenspilt.
    INSERT INTO public.inndata_artefakt
        (tenant, eiermodul, formaal, innholdstype, maks_bytes,
         reservasjon_jti, idempotensnokkel, utloper)
    VALUES (p_tenant, p_eiermodul, p_formaal, 'application/zip',
            p_maks_bytes, v_jti, p_idempotensnokkel,
            pg_catalog.now() + interval '1 hour')
    ON CONFLICT ON CONSTRAINT inndata_idempotens_unik DO NOTHING
    RETURNING public.inndata_artefakt.inndata_id INTO v_id;
    IF v_id IS NULL THEN
        SELECT * INTO r FROM public.inndata_artefakt
         WHERE tenant = p_tenant
           AND idempotensnokkel = p_idempotensnokkel;
        IF NOT FOUND THEN
            -- Konflikten fantes, men raden er usynlig: da er tenantkonteksten
            -- ikke den vi tror, og et stille «ny reservasjon» ville vært
            -- verre enn en feil.
            RAISE EXCEPTION 'reserver_inndata: idempotenskonflikt uten'
                ' lesbar rad' USING ERRCODE = 'unique_violation';
        END IF;
        -- Samme nøkkel må bety samme BESTILLING (038-formen): en nøkkel
        -- gjenbrukt for en annen kombinasjon er en konflikt, ikke et
        -- gjenspill. I v1 finnes bare én lovlig kombinasjon, så dette er
        -- en vakt for kontraktsendringen som utvider settet — ikke pynt.
        IF r.eiermodul IS DISTINCT FROM p_eiermodul
           OR r.formaal IS DISTINCT FROM p_formaal
           OR r.maks_bytes IS DISTINCT FROM p_maks_bytes THEN
            RAISE EXCEPTION 'reserver_inndata: nøkkelen er brukt for en'
                ' ANNEN reservasjon (%/%/%)',
                r.eiermodul, r.formaal, r.maks_bytes
                USING ERRCODE = 'unique_violation';
        END IF;
        -- …men et gjenspill må gi et BRUKBART svar (Cursor P2). En
        -- reservasjon som fortsatt står `reservert` etter fristen er død:
        -- `registrer_inndata_lastet` avviser jti-en på `utloper`, og
        -- UNIQUE på `(tenant, idempotensnokkel)` sperrer en ny rad under
        -- den samme nøkkelen. Uten denne grenen svarte vi 201 med en jti
        -- som ikke kan brukes til noe, og klienten satt fast på nøkkelen
        -- sin uten noen vei ut — nettopp tapet gjenspillet finnes for å
        -- redde.
        --
        -- Runde 1 av denne grenen tok bare `reservert` + over fristen, og
        -- ba `forkastet` vente på reaperen som skriver den (Cursor P2,
        -- runde 2). Det var å svare på FORMEN i stedet for på spørsmålet:
        -- en `forkastet` rad er død av nøyaktig samme grunn — jti-en
        -- avvises av `registrer_inndata_lastet`, nøkkelen er sperret av
        -- UNIQUE — og en gren som må utvides hver gang en ny død tilstand
        -- oppstår er en gren som kommer tilbake. Her klassifiseres derfor
        -- HELE tilstandsrommet én gang, som CHECKen over gjør:
        --
        --   * `forkastet` — død uansett hvor den kom fra. Vakten tillater
        --     både `reservert -> forkastet` og `lastet -> forkastet`, og
        --     i begge tilfeller er bunten borte.
        --   * `reservert` etter fristen — død: ingen kan laste på jti-en,
        --     og ingen kan reservere på nytt under nøkkelen.
        --   * `reservert` innenfor fristen, `lastet`, `bundet` — LEVENDE,
        --     og gjenspilles uansett frist: der finnes bunten, og
        --     referansen er det klienten mistet. En `lastet` som passerer
        --     fristen mister ikke bytene sine.
        --
        -- Konflikt er det ærlige svaret på en død rad: nøkkelen er
        -- oppbrukt, ta en ny. Samme errcode som den andre konfliktarmen,
        -- så API-et svarer `idempotenskonflikt` uten en ny feilvei.
        IF r.status = 'forkastet'
           OR (r.status = 'reservert' AND pg_catalog.now() > r.utloper) THEN
            RAISE EXCEPTION 'reserver_inndata: nøkkelen hører til en DØD'
                ' reservasjon (%, utløper %)', r.status, r.utloper
                USING ERRCODE = 'unique_violation';
        END IF;
        -- Gjenspill: samme svar som første gang. Reservasjonen kan i
        -- mellomtiden ha blitt `lastet` eller `bundet` — referansen og
        -- jti-en er like fullt de samme, og det er nettopp DEM klienten
        -- mistet. Å utstede en ny her ville vært å svare noe annet på den
        -- samme forespørselen.
        v_id := r.inndata_id; v_jti := r.reservasjon_jti;
    END IF;
    inndata_id := v_id; reservasjon_jti := v_jti;
    RETURN NEXT;
END $$;

-- Lastingen: API-et har strømmet, målt, hashet og kryptert — HER møter
-- målingen deklarasjonen, og reservasjonen forbrukes (jti er engangs:
-- raden EIES av jti-en, og overgangen kan bare skje én gang).
CREATE FUNCTION registrer_inndata_lastet(
    p_tenant TEXT, p_jti TEXT, p_faktiske_bytes BIGINT,
    p_sha256 TEXT, p_key_id TEXT, p_nonce BYTEA, p_sti TEXT)
RETURNS TABLE (ut_inndata_id UUID, ut_lager_sti TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD;
BEGIN
    -- 017:201 / 016:779-formen: svaret ack-es ikke før WAL-en står på
    -- disk. Uten denne kunne et vertskrasj rulle raden tilbake til
    -- `reservert` ETTER at klienten hadde fått 201 — jti-en ville da vært
    -- «ubrukt» igjen, filen en orphan, og klienten trodd bunten var
    -- lastet. Filen fsync-es i API-et; raden må ha samme garanti
    -- (Cursor P1-4).
    SET LOCAL synchronous_commit = on;
    PERFORM public.krev_tenantkontekst(p_tenant, 'registrer_inndata_lastet');
    SELECT * INTO r FROM public.inndata_artefakt
     WHERE tenant = p_tenant AND reservasjon_jti = p_jti
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'inndata: ukjent reservasjon'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Engangs-jti, men IKKE engangs-SVAR (Cursor P1-1, 017-regresjonen):
    -- `bruk_artefaktkapabilitet` returnerer den eksisterende id-en når
    -- samme hash kommer igjen, og konflikter kun ved ANNEN hash. Gikk 201-
    -- svaret tapt på veien ut, er klientens retry med SAMME kropp den
    -- samme forespørselen — ikke et nytt forsøk på å brenne reservasjonen.
    -- Uten denne grenen var et tapt svar et PERMANENT tap av bunten.
    -- `ut_lager_sti` er den LAGREDE stien: kalleren som fikk en annen sti
    -- tilbake enn den skrev, vet at den nettopp skrev en orphan og rydder.
    IF r.status = 'lastet' THEN
        IF r.innhold_sha256 IS DISTINCT FROM p_sha256 THEN
            RAISE EXCEPTION 'inndata: reservasjonen er brukt for ANNET'
                ' innhold' USING ERRCODE = 'unique_violation';
        END IF;
        -- FRISTEN GJELDER OGSÅ GJENSPILLET (Codex P2 / Cursor P2, runde 6).
        -- Denne grenen svarte 201 uten å se på `utloper`, mens
        -- `bind_inndata` avviser NØYAKTIG den samme raden på den (509-512).
        -- Et tapt 201-svar som ble retryet etter fristen fikk derfor
        -- «gjenopprettet» tilbake på en bunt som aldri kan bindes: klienten
        -- var fortalt at opplastingen sto, idempotensnøkkelen var låst til
        -- den døde lineagen, og hver bestilling feilet siden. Et ærlig
        -- avslag ved opplasting er en klient som kan reservere på nytt.
        --
        -- Samme errcode som utløpssjekken under, altså den kanoniske
        -- `inndata_reservasjon_ugyldig` (409) — ikke en ny kode. Fristen
        -- måles fra reservasjonen, ikke fra `lastet_ts`: å FORLENGE den her
        -- ville gjort opplastingen til en frist-utsteder, og da er
        -- `inndata_artefakt_utlop` ikke lenger reaperens fasit.
        IF pg_catalog.now() > r.utloper THEN
            RAISE EXCEPTION 'inndata: bunten er utløpt og kan ikke gjenspilles'
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        ut_inndata_id := r.inndata_id; ut_lager_sti := r.lager_sti;
        RETURN NEXT;
        RETURN;
    END IF;
    -- FORBRUKT ER IKKE «BRUKT FOR ANNET INNHOLD» (Cursor P2, runde 7).
    -- Denne grenen er alt som IKKE er `reservert` og ikke `lastet`, altså
    -- `bundet`/`forkastet`. Den reiste `unique_violation`, og
    -- `inndata.py:265` mapper den til `inndata_alt_lastet` — men
    -- `feil.py:233-237` sier ordrett at ukjent, utløpt OG alt forbrukt
    -- skal ha SAMME svar, «et skille ville vært et orakel på hvilke
    -- jti-er som finnes». Slik den sto, svarte døren `alt_lastet` på en
    -- jti som HAR nådd minst `bundet` og `reservasjon_ugyldig` på en som
    -- aldri fantes: nøyaktig det orakelet, og i tillegg en løgn, for
    -- innholdet var aldri det som skilte.
    --
    -- `unique_violation` beholdes der den er sann: hash-mismatch på
    -- `lastet` (424-426), som ER «brukt for ANNET innhold», og den
    -- virkelige `inndata_lagersti_unik`-kollisjonen. 017 skiller på samme
    -- måte — replay/hash-konflikt mot utløpt/ugyldig — og «forbrukt uten
    -- hash-match» hører i den siste leiren.
    IF r.status <> 'reservert' THEN
        RAISE EXCEPTION 'inndata: reservasjonen er alt forbrukt (%)',
            r.status USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF pg_catalog.now() > r.utloper THEN
        RAISE EXCEPTION 'inndata: reservasjonen er utløpt'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_faktiske_bytes IS NULL OR p_faktiske_bytes <= 0
       OR p_faktiske_bytes > r.maks_bytes THEN
        RAISE EXCEPTION 'inndata: % byte bryter deklarasjonen (maks %)',
            p_faktiske_bytes, r.maks_bytes
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Kryptostrukturen håndheves FØR forbruket, med det kanoniske
    -- feilkontraktet (017:252-formen). `inndata_krypto_struktur` er samme
    -- invariant på tabellen og fanger enhver annen skrivevei; her gir den
    -- `inndata_reservasjon_ugyldig` i stedet for check_violation — og
    -- reservasjonen blir stående som `reservert`, ikke brent på en nonce
    -- som aldri kunne dekryptert (Cursor P2-6).
    IF p_key_id IS NULL OR p_nonce IS NULL
       OR octet_length(p_nonce) <> 12
       OR p_sti IS NULL OR length(btrim(p_sti)) = 0
       OR p_sha256 IS NULL OR p_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'inndata: krypto/sti/hash er strukturelt ugyldig'
            ' (nonce=% B)', octet_length(p_nonce)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Stien må ligge i TENANTENS eget navnerom (Cursor P1). Samme
    -- invariant som `inndata_lagersti_navnerom` på tabellen, men med det
    -- kanoniske feilkontraktet: en giftig sti skal gi
    -- `inndata_reservasjon_ugyldig` og la reservasjonen stå `reservert`,
    -- ikke brenne jti-en på en peker inn i en fremmed katalog som
    -- reaperen senere ville slettet. Formen er den relative
    -- `<tenant>/<uuid>.bin` — se tabellens constraint for hvorfor roten
    -- ikke er med, og for hvorfor traverseringen måles på KOMPONENTENE
    -- og ikke på den sammensatte strengen (Codex P2, runde 7).
    -- `p_tenant` er her allerede kallerens egen tenantkontekst:
    -- `krev_tenantkontekst` over avviser alt annet, og den avviser også
    -- NULL og tom streng.
    IF p_tenant IN ('.', '..') OR position('/' in p_tenant) > 0
       OR left(p_sti, length(p_tenant) + 1) IS DISTINCT FROM p_tenant || '/'
       OR length(p_sti) <= length(p_tenant) + 1
       OR position('/' in substr(p_sti, length(p_tenant) + 2)) > 0
       OR substr(p_sti, length(p_tenant) + 2) IN ('.', '..') THEN
        RAISE EXCEPTION 'inndata: lagerstien % ligger utenfor tenantens'
            ' navnerom', p_sti
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.inndata_artefakt
       SET status = 'lastet', faktiske_bytes = p_faktiske_bytes,
           innhold_sha256 = p_sha256, key_id = p_key_id, nonce = p_nonce,
           lager_sti = p_sti, lastet_ts = pg_catalog.now()
     WHERE tenant = p_tenant AND inndata_id = r.inndata_id;
    ut_inndata_id := r.inndata_id; ut_lager_sti := p_sti;
    RETURN NEXT;
END $$;

-- Bindingen: kalles i BESTILLINGENS transaksjon. Én bunt, ett oppdrag,
-- én gang — og modulen som skal lese må være den bunten ble reservert
-- for.
CREATE FUNCTION bind_inndata(
    p_tenant TEXT, p_inndata_id UUID, p_oppdrag_id BIGINT,
    p_eiermodul TEXT)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_oppdrag_eier TEXT; v_oppdragstype TEXT;
        v_oppdrag_status TEXT; v_konsument TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'bind_inndata');
    SELECT * INTO r FROM public.inndata_artefakt
     WHERE tenant = p_tenant AND inndata_id = p_inndata_id
     FOR UPDATE;
    IF NOT FOUND OR r.status <> 'lastet' THEN
        RAISE EXCEPTION 'inndata: % er ikke en lastet bunt',
            p_inndata_id USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Utløpet gjelder også HER (Cursor P2-2): `inndata_artefakt_utlop`
    -- lover at en `lastet` bunt løper ut, og `registrer_inndata_lastet`
    -- håndhever det. Uten samme sjekk i bindingen kunne en utgått bunt
    -- bindes for alltid — fristen ville da vært en påstand som bare gjaldt
    -- fram til reaperen (som kommer i en senere PR) faktisk fantes.
    IF pg_catalog.now() > r.utloper THEN
        RAISE EXCEPTION 'inndata: bunten % er utløpt', p_inndata_id
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Eierskapet AVLEDES av oppdraget, ikke av kallerens påstand
    -- (Codex P1). `p_eiermodul` kom fra kalleren, og `disponit` har
    -- EXECUTE her: en kaller som kjenner buntens eiermodul kunne ekko-e
    -- den tilbake og samtidig peke på et HVILKET SOM HELST oppdrag i egen
    -- tenant — en bunt reservert for én modul ble da bundet til en
    -- fremmed jobb. Sannheten om hvem som eier jobben står i `oppdrag`.
    --
    -- Ingen `FOR UPDATE` på oppdraget: `oppdrag.eiermodul` er
    -- kolonnelåst i `oppdrag_kolonnelaas()` (005) og raden kan ikke
    -- slettes (`oppdrag_ingen_sletting`), så verdien kan ikke endre seg
    -- under oss. En lås ville dessuten krevd UPDATE-rettighet på
    -- `oppdrag` for `disponit_domene_eier`, som i dag har KUN SELECT
    -- (016) — å utvide den for en lås vi ikke trenger ville byttet ett
    -- funn mot et større. RLS gjelder også for denne definer-rollen;
    -- `krev_tenantkontekst` over har alt bundet `disponit.tenant`.
    SELECT o.eiermodul, o.oppdragstype, o.status
      INTO v_oppdrag_eier, v_oppdragstype, v_oppdrag_status
      FROM public.oppdrag o
     WHERE o.tenant = p_tenant AND o.id = p_oppdrag_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'inndata: oppdrag % finnes ikke i tenant %',
            p_oppdrag_id, p_tenant
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- LIVSSYKLUSEN ER OGSÅ EN PORT (Cursor P2, runde 6). Eierskap og
    -- formål over sier HVEM og HVA, ikke NÅR: uten denne kunne en kaller
    -- med EXECUTE binde en lastet bunt til et TERMINALT oppdrag
    -- (`utfort`/`feilet`/`kansellert`). Engangsbunten ble da forbrukt, det
    -- terminale oppdraget brukte opp sin ENE bunteplass
    -- (`inndata_artefakt_oppdrag` er unik og har ingen vei tilbake — 005s
    -- vakt tillater ingen overgang UT av terminal), og lineage pekte på en
    -- jobb som var ferdig før bunten fantes.
    --
    -- Det AKTIVE settet er 038s (`opprettet`,`plukket`), ikke bare
    -- `opprettet`: bindingen skjer i bestillingens transaksjon og treffer
    -- i praksis `opprettet`, men å snevre inn til nøyaktig den ene ville
    -- vært å binde PR-2s bestillingsvei til en rekkefølge denne
    -- migrasjonen ikke får bestemme. Porten er fail-closed på det som er
    -- galt uansett rekkefølge: en jobb utenfor sin egen livssyklus.
    -- 017:110-111 gjør det samme strammere (`plukket` alene) fordi en
    -- kapabilitet utstedes ETTER claim; her er det motsatt ende av løpet.
    IF v_oppdrag_status NOT IN ('opprettet', 'plukket') THEN
        RAISE EXCEPTION 'inndata: oppdrag % er % og kan ikke binde inndata',
            p_oppdrag_id, v_oppdrag_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF r.eiermodul IS DISTINCT FROM v_oppdrag_eier
       OR r.eiermodul IS DISTINCT FROM p_eiermodul THEN
        RAISE EXCEPTION 'inndata: bunten er reservert for %, oppdrag %'
            ' eies av % (kalleren påsto %)',
            r.eiermodul, p_oppdrag_id, v_oppdrag_eier, p_eiermodul
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- EIERSKAP ER IKKE FORMÅL (Codex P1). Sjekken over sier at oppdraget
    -- eies av samme modul som bunten ble reservert for — men `m57_ats` eier
    -- flere oppdragstyper enn den ene hvis kontrakt faktisk KONSUMERER en
    -- søknadsbunt (`soknadsbunt_ref` er påkrevd i `rekruttering.evaluering`
    -- alene, se `oppdragskontrakt.FELTSTRENGER`). En kaller med EXECUTE
    -- kunne derfor bundet bunten til et vilkårlig annet m57-oppdrag i egen
    -- tenant: engangsbunten ble forbrukt, det uskyldige oppdraget brukte
    -- opp sin ENE bunteplass (`inndata_artefakt_oppdrag`), og lineage
    -- pekte på en jobb som aldri skulle lest den.
    --
    -- Kartet er lukket og fail-closed: en ny `formaal` i en senere
    -- migrasjon MÅ navngi sin konsument her, ellers er bindingen en feil —
    -- ikke en stille passering. Samme vedtak som `eiermodul`-CHECKen: et
    -- nytt formål er en kontraktsendring, ikke et kallargument.
    v_konsument := CASE r.formaal
                        WHEN 'soknadsbunt' THEN 'rekruttering.evaluering'
                   END;
    IF v_konsument IS NULL THEN
        RAISE EXCEPTION 'inndata: formålet % har ingen konsumerende'
            ' oppdragstype i denne kontrakten', r.formaal
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF v_oppdragstype IS DISTINCT FROM v_konsument THEN
        RAISE EXCEPTION 'inndata: % konsumeres av %, men oppdrag % er %',
            r.formaal, v_konsument, p_oppdrag_id, v_oppdragstype
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.inndata_artefakt
       SET status = 'bundet', oppdrag_id = p_oppdrag_id,
           bundet_ts = pg_catalog.now()
     WHERE tenant = p_tenant AND inndata_id = p_inndata_id;
END $$;

REVOKE ALL ON FUNCTION reserver_inndata(TEXT, TEXT, TEXT, BIGINT, TEXT)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION registrer_inndata_lastet(TEXT, TEXT, BIGINT, TEXT,
    TEXT, BYTEA, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION bind_inndata(TEXT, UUID, BIGINT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION reserver_inndata(TEXT, TEXT, TEXT, BIGINT, TEXT)
    TO disponit;
GRANT EXECUTE ON FUNCTION registrer_inndata_lastet(TEXT, TEXT, BIGINT,
    TEXT, TEXT, BYTEA, TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION bind_inndata(TEXT, UUID, BIGINT, TEXT)
    TO disponit;

RESET ROLE;

REVOKE ALL ON inndata_artefakt FROM PUBLIC;
GRANT SELECT ON inndata_artefakt TO disponit;
