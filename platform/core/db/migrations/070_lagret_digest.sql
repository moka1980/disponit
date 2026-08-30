-- 070: lagret digest for inndata-ciphertexten (sak #245, Codex P1 fra
-- #229-runden). Filen på disk er tenant-DEK-ciphertext og basens
-- `innhold_sha256` er over KLARTEKSTEN — det fantes ingen lagret verdi
-- å måle filen mot, så backupens arkivport kunne publisere et par som
-- «gjenopprettingsverifisert» mens ciphertexten var avkortet eller
-- korrupt. Digesten skrives i SAMME transaksjon som `lastet`-skiftet,
-- målt av API-et over nøyaktig de bytene som ble fsync-et — da kan
-- backupen (og enhver annen leser) måle filen uten å kunne lese den.
--
-- Eldre rader står med NULL: de ble født før målingen fantes, og en
-- backfill kan ikke vite om dagens fil er den som ble skrevet — en
-- diktet digest ville gjort porten til en løgn. Porten hopper over
-- NULL, med vilje.

ALTER TABLE inndata_artefakt ADD COLUMN lagret_sha256 TEXT;
ALTER TABLE inndata_artefakt ADD CONSTRAINT inndata_lagret_digest_form
    CHECK (lagret_sha256 IS NULL OR lagret_sha256 ~ '^[0-9a-f]{64}$');

-- DØREN EIES AV DOMENE-EIEREN (058/059-formen): CREATE OR REPLACE og
-- DROP krever eieren, og migrer.py-grantene kjører i samme
-- rollekontekst. Tabellen over eies derimot av migrator — derfor
-- byttes rollen først HER.
SET LOCAL ROLE disponit_domene_eier;

CREATE OR REPLACE FUNCTION registrer_inndata_lastet(
    p_tenant TEXT, p_jti TEXT, p_faktiske_bytes BIGINT,
    p_sha256 TEXT, p_key_id TEXT, p_nonce BYTEA, p_lagret_sha256 TEXT)
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
    -- GJENSPILLET SAMMENLIGNER ALDRI p_lagret_sha256 (070, sak #245):
    -- hvert forsøk krypterer med FERSK nonce, så retryens ciphertext er
    -- en annen byte-sekvens enn den som ligger på disk — mens filen på
    -- disk hører til RADENS nonce og radens lagrede digest. Å kreve
    -- match her ville felt hvert eneste tapt-201-gjenspill; å SKRIVE
    -- den nye ville løyet om filen. Samme regel som nonce/key_id, som
    -- gjenspillet heller aldri rører.
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
       OR p_sha256 IS NULL OR p_sha256 !~ '^[0-9a-f]{64}$'
       -- 070 (sak #245): digesten OVER CIPHERTEXTEN, målt av
       -- API-et på nøyaktig de bytene som ble fsync-et. Samme
       -- strukturkrav som klartekst-digesten — en dør uten den
       -- ville født rader backupporten ikke kan måle.
       OR p_lagret_sha256 IS NULL
       OR p_lagret_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'inndata: krypto/sti/hash er strukturelt ugyldig'
            ' (nonce=% B)', octet_length(p_nonce)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- B-MASKINEN (#192): stien ble generert av døren ved fødselen og
    -- står på raden — det finnes ikke lenger noe sti-ARGUMENT å vokte.
    -- (Navneroms-guardene fra #190-rundene levde av at kalleren sendte
    -- stien; en vakt over en umulig tilstand er død kode. Tabellens
    -- CHECKer består som dybdeforsvar.)
    UPDATE public.inndata_artefakt
       SET status = 'lastet', faktiske_bytes = p_faktiske_bytes,
           innhold_sha256 = p_sha256, key_id = p_key_id, nonce = p_nonce,
           lagret_sha256 = p_lagret_sha256,
           lastet_ts = pg_catalog.now()
     WHERE tenant = p_tenant AND inndata_id = r.inndata_id;
    ut_inndata_id := r.inndata_id; ut_lager_sti := r.lager_sti;
    RETURN NEXT;
END $$;

-- Gammel signatur ut (digest-argumentet er del av kontrakten nå):
DROP FUNCTION IF EXISTS registrer_inndata_lastet(TEXT, TEXT, BIGINT,
    TEXT, TEXT, BYTEA);

REVOKE ALL ON FUNCTION registrer_inndata_lastet(TEXT, TEXT, BIGINT, TEXT,
    TEXT, BYTEA, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION registrer_inndata_lastet(TEXT, TEXT, BIGINT,
    TEXT, TEXT, BYTEA, TEXT) TO disponit;

RESET ROLE;
