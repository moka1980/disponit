-- 056: M-57 utsendingskjeden — oppdrag → frigivelse → signatur →
-- listeversjon → innhold, hvert ledd en navngitt constraint.
--
-- Klarsignalet (docs/pr/PR-M57-IMPLEMENTERINGSKLARSIGNAL.md, frosset):
-- evalueringen er RÅDGIVENDE (artefakter, ingenting utad); utsendelsen er
-- IRREVERSIBEL og skjer kun fordi et menneske signerte. Utsendelsen er
-- ikke et nytt modulnoppdrag, men en signaturbundet FRIGIVELSE:
-- opprinnelsesvalget er (b) — en tredje opprinnelse 'frigivelse' med sin
-- egen herdede funksjon, slik 038 selv valgte formen da beslutningsveien
-- kom til. CHECK-en dekker kombinasjonene uttømmende, og funksjonen er
-- eneste vei.
--
-- LISTEN ER EN VERSJON: immutabel rad; redigering gir ny liste_id i samme
-- utkast_serie. Lineage er lineær og serie-bundet — én rot per serie,
-- høyst ett barn per ledd, høyst én SIGNERT versjon per serie. Dermed:
-- ingen gyldig signatur => ingen representerbar ATS-utsendelse, bevisbart
-- med direkte DML (portene 6–12).

-- ------------------------------------------------------------
-- 1. Listeversjonene. Append-only: hele raden er innholdet signaturen
--    binder; en «redigering» er en NY versjon med forrige som forelder.
CREATE TABLE utsendingsliste (
    tenant TEXT NOT NULL,
    liste_id UUID NOT NULL,
    utkast_serie UUID NOT NULL,
    forrige_liste_id UUID,
    oppdrag_id BIGINT NOT NULL,          -- evalueringsoppdraget (BIGINT)
    listetype TEXT NOT NULL CHECK (listetype IN ('invitasjon', 'avslag')),
    malversjon TEXT NOT NULL,
    innhold_hash TEXT NOT NULL,
    antall INT NOT NULL CHECK (antall > 0 AND antall <= 5000),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, liste_id),
    UNIQUE (tenant, liste_id, innhold_hash),
    UNIQUE (tenant, utkast_serie, liste_id),           -- serie-refererbar
    UNIQUE (tenant, utkast_serie, innhold_hash),
    -- Forelderen må stå i SAMME serie (port 9): FK-en går på
    -- serie-nøkkelen, så en versjon aldri kan adoptere en annen series
    -- historikk.
    FOREIGN KEY (tenant, utkast_serie, forrige_liste_id)
        REFERENCES utsendingsliste (tenant, utkast_serie, liste_id),
    FOREIGN KEY (tenant, oppdrag_id) REFERENCES oppdrag (tenant, id));

-- Lineær lineage (portene 10–11): høyst ETT barn per forelder, og
-- nøyaktig ÉN rot per serie. Samtidige redigeringer serialiseres av
-- unik-bruddet — én vinner, taperen får konflikt og må lese vinnerens
-- versjon før et nytt forsøk.
CREATE UNIQUE INDEX ett_barn_per_versjon ON utsendingsliste
    (tenant, utkast_serie, forrige_liste_id)
    WHERE forrige_liste_id IS NOT NULL;
CREATE UNIQUE INDEX en_rot_per_serie ON utsendingsliste
    (tenant, utkast_serie)
    WHERE forrige_liste_id IS NULL;

DROP TRIGGER IF EXISTS utsendingsliste_append_only ON utsendingsliste;
CREATE TRIGGER utsendingsliste_append_only
    BEFORE UPDATE OR DELETE ON utsendingsliste
    FOR EACH ROW EXECUTE FUNCTION avvis_endring();

-- ------------------------------------------------------------
-- 2. Signaturen. Én signert versjon per serie (port 12) — det er
--    versjonen som får sendes, og bare den. Signaturen binder innholdet
--    (hash) OG serien: FK-ene krever at (liste, hash) og (serie, liste)
--    faktisk er samme rad i utsendingsliste.
CREATE TABLE utsendingssignatur (
    tenant TEXT NOT NULL,
    liste_id UUID NOT NULL,
    utkast_serie UUID NOT NULL,
    innhold_hash TEXT NOT NULL,
    signatar TEXT NOT NULL REFERENCES brukeridentitet (bruker_id),
    signert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    operasjonsnokkel TEXT NOT NULL,      -- SP-2
    PRIMARY KEY (tenant, liste_id),
    UNIQUE (tenant, liste_id, innhold_hash, utkast_serie), -- refererbar
    UNIQUE (tenant, operasjonsnokkel),
    FOREIGN KEY (tenant, utkast_serie, liste_id)
        REFERENCES utsendingsliste (tenant, utkast_serie, liste_id),
    FOREIGN KEY (tenant, liste_id, innhold_hash)
        REFERENCES utsendingsliste (tenant, liste_id, innhold_hash));

CREATE UNIQUE INDEX en_signert_versjon_per_serie
    ON utsendingssignatur (tenant, utkast_serie);

DROP TRIGGER IF EXISTS utsendingssignatur_append_only ON utsendingssignatur;
CREATE TRIGGER utsendingssignatur_append_only
    BEFORE UPDATE OR DELETE ON utsendingssignatur
    FOR EACH ROW EXECUTE FUNCTION avvis_endring();

-- ------------------------------------------------------------
-- 3. Frigivelsen: én rad per mottaker, og FK-kjeden krever at listen er
--    SIGNERT (port 6) — det finnes ingen representerbar frigivelse av en
--    usignert liste, uansett hvilken vei noen skriver.
CREATE TABLE utsendingsfrigivelse (
    tenant TEXT NOT NULL,
    frigivelse_id UUID NOT NULL,
    liste_id UUID NOT NULL,
    innhold_hash TEXT NOT NULL,
    utkast_serie UUID NOT NULL,
    mottaker_ref TEXT NOT NULL,
    frigitt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, frigivelse_id),
    UNIQUE (tenant, liste_id, mottaker_ref),
    FOREIGN KEY (tenant, liste_id, innhold_hash, utkast_serie)
        REFERENCES utsendingssignatur
            (tenant, liste_id, innhold_hash, utkast_serie));

DROP TRIGGER IF EXISTS utsendingsfrigivelse_append_only
    ON utsendingsfrigivelse;
CREATE TRIGGER utsendingsfrigivelse_append_only
    BEFORE UPDATE OR DELETE ON utsendingsfrigivelse
    FOR EACH ROW EXECUTE FUNCTION avvis_endring();

-- ------------------------------------------------------------
-- 4. Tenant-isolasjon — samme form som 038s tabeller.
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['utsendingsliste', 'utsendingssignatur',
                             'utsendingsfrigivelse'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolasjon ON %I
                USING      (tenant = current_setting(''disponit.tenant'', true))
                WITH CHECK (tenant = current_setting(''disponit.tenant'', true))',
            t);
    END LOOP;
END $$;

-- ------------------------------------------------------------
-- 5. Den tredje opprinnelsen på oppdrag. Generisk referansekolonne +
--    constraint-swap i SAMME migrasjon: totalformen tar eksplisitt
--    stilling til HVERT referansefelt i HVER arm (SP-5-total). Den gamle
--    m37-armen tok aldri stilling til `beslutning_loggpost_id` — det
--    rettes her, sammen med utvidelsen.
ALTER TABLE oppdrag ADD COLUMN frigivelse_id UUID;

ALTER TABLE oppdrag
    ADD CONSTRAINT oppdrag_frigivelse_fk
    FOREIGN KEY (tenant, frigivelse_id)
    REFERENCES utsendingsfrigivelse (tenant, frigivelse_id);

-- ÉN frigivelse -> ETT oppdrag (Cursor P1 på #140): utsendelsen er
-- IRREVERSIBEL, så kardinaliteten er en sikkerhetsinvariant — samme form
-- som beslutningsveiens `oppdrag_en_per_beslutning` (008). To kall for
-- samme frigivelse serialiseres av indeksen; funksjonen under gjør
-- taperen idempotent i stedet for feilende.
CREATE UNIQUE INDEX oppdrag_en_per_frigivelse
    ON oppdrag (tenant, frigivelse_id)
    WHERE frigivelse_id IS NOT NULL;

-- KLARSIGNAL-KORREKSJON, målt av SP-10-seedet: skissens m37-arm sa
-- `beslutning_loggpost_id IS NULL`, men m37-oppdrag bærer LEGITIMT en
-- fase-2-beslutningsloggpost (`koblingsstatus='KOBLET'` KREVER den —
-- se `oppdrag_kobling_konsistent`), og legacy-rader har den ikke.
-- Stillingen til `beslutning_loggpost_id` i m37-armen EIES derfor av
-- koblingsvilkåret, ikke av opprinnelsesformen — armen tar eksplisitt
-- stilling til alt annet. En swap etter skissen hadde stoppet på første
-- bebodde base, presis 047-klassen SP-10 finnes for.
-- Verdi-enumen utvides i samme swap: 'frigivelse' er en lovlig
-- opprinnelse fra nå.
ALTER TABLE oppdrag DROP CONSTRAINT oppdrag_opprinnelse_check;
ALTER TABLE oppdrag ADD CONSTRAINT oppdrag_opprinnelse_check CHECK (
    opprinnelse IN ('m37_reparasjon', 'beslutning', 'frigivelse'));

ALTER TABLE oppdrag DROP CONSTRAINT oppdrag_opprinnelse_komplett;
ALTER TABLE oppdrag ADD CONSTRAINT oppdrag_opprinnelse_komplett CHECK (
       (opprinnelse = 'm37_reparasjon'
          AND unntak_id IS NOT NULL AND loggpost_id IS NOT NULL
          AND repair_operation_id IS NOT NULL
          AND frigivelse_id IS NULL)
    OR (opprinnelse = 'beslutning'
          AND beslutning_loggpost_id IS NOT NULL
          AND unntak_id IS NULL AND loggpost_id IS NULL
          AND repair_operation_id IS NULL AND frigivelse_id IS NULL)
    OR (opprinnelse = 'frigivelse'
          AND frigivelse_id IS NOT NULL
          AND unntak_id IS NULL AND loggpost_id IS NULL
          AND repair_operation_id IS NULL
          AND beslutning_loggpost_id IS NULL));

-- Koblingsvilkåret får frigivelses-armen: et KOBLET oppdrag er koblet
-- til autorisasjonen sin — beslutningsloggposten ELLER frigivelsen.
-- Opprinnelsesformen over avgjør hvilken av dem som finnes per arm.
ALTER TABLE oppdrag DROP CONSTRAINT oppdrag_kobling_konsistent;
ALTER TABLE oppdrag ADD CONSTRAINT oppdrag_kobling_konsistent CHECK (
       (koblingsstatus = 'KOBLET'
          AND (beslutning_loggpost_id IS NOT NULL
               OR frigivelse_id IS NOT NULL)
          AND oppdragstype <> 'verifikasjon')
    OR (koblingsstatus = 'LEGACY_UKJENT'
          AND beslutning_loggpost_id IS NULL AND frigivelse_id IS NULL)
    OR (koblingsstatus = 'VERIFIKASJON'
          AND beslutning_loggpost_id IS NULL AND frigivelse_id IS NULL
          AND oppdragstype = 'verifikasjon'));

-- ------------------------------------------------------------
-- 6. Kolonnelåsen dekker BEGGE opprinnelses-referansene. Klarsignalet
--    antok at nye referansekolonner ble immutable «gratis» — målt mot
--    basen er låsen en EKSPLISITT liste, og 038 glemte å utvide den:
--    `beslutning_loggpost_id` kunne repekes av runtime etter INSERT.
--    Klassen rettes: begge inn i listen. Kroppen under er den GJELDENDE
--    (pg_get_functiondef fra migrert base) med de to linjene som eneste
--    endring.
CREATE OR REPLACE FUNCTION oppdrag_kolonnelaas()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.unntak_id IS DISTINCT FROM OLD.unntak_id
       OR NEW.loggpost_id IS DISTINCT FROM OLD.loggpost_id
       OR NEW.repair_operation_id IS DISTINCT FROM OLD.repair_operation_id
       -- 056: opprinnelses-referansene er fødselsattributter, alle tre.
       OR NEW.beslutning_loggpost_id IS DISTINCT FROM OLD.beslutning_loggpost_id
       OR NEW.frigivelse_id IS DISTINCT FROM OLD.frigivelse_id
       OR NEW.oppdragstype IS DISTINCT FROM OLD.oppdragstype
       OR NEW.handling IS DISTINCT FROM OLD.handling
       OR NEW.eiermodul IS DISTINCT FROM OLD.eiermodul
       OR NEW.payload_kryptert IS DISTINCT FROM OLD.payload_kryptert
       OR NEW.key_id IS DISTINCT FROM OLD.key_id
       OR NEW.alg IS DISTINCT FROM OLD.alg
       OR NEW.nonce IS DISTINCT FROM OLD.nonce
       OR NEW.utforelsesfrist IS DISTINCT FROM OLD.utforelsesfrist
       OR NEW.evidensfrist IS DISTINCT FROM OLD.evidensfrist
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'oppdrag: kun status-, owner- og kvitteringsfelter kan endres';
    END IF;
    IF NEW.owner_generation < OLD.owner_generation THEN
        RAISE EXCEPTION 'oppdrag: owner_generation kan aldri reduseres';
    END IF;
    -- CP5 + Codex P1: kontraktbinding/epoch endres KUN av den herdede claim-
    -- funksjonen (eid av disponit_m37_claimer). Runtime har direkte UPDATE på
    -- oppdrag; uten denne current_user-sjekken kunne runtime initialisere en
    -- uclaimet rad med vilkårlig modul/versjon/hash (forfalske bindingen) eller
    -- nulle en stemplet epoch.
    IF current_user <> 'disponit_m37_claimer' AND (
           NEW.modul_id        IS DISTINCT FROM OLD.modul_id
        OR NEW.kontraktversjon IS DISTINCT FROM OLD.kontraktversjon
        OR NEW.kontrakt_hash   IS DISTINCT FROM OLD.kontrakt_hash
        OR NEW.module_epoch    IS DISTINCT FROM OLD.module_epoch) THEN
        RAISE EXCEPTION 'oppdrag: kontraktbinding/epoch settes kun av claim-funksjonen';
    END IF;
    -- Write-once (gjelder også claim-funksjonen på en reclaim): satt → frosset.
    IF OLD.modul_id IS NOT NULL AND (
           NEW.modul_id        IS DISTINCT FROM OLD.modul_id
        OR NEW.kontraktversjon IS DISTINCT FROM OLD.kontraktversjon
        OR NEW.kontrakt_hash   IS DISTINCT FROM OLD.kontrakt_hash) THEN
        RAISE EXCEPTION 'oppdrag: kontraktbindingen er frosset når den er satt';
    END IF;
    -- module_epoch er monoton OG kan ikke nulles etter at den er satt (Codex P2:
    -- non-NULL→NULL fjernet fencing-generasjonen uten feil).
    IF OLD.module_epoch IS NOT NULL
       AND (NEW.module_epoch IS NULL OR NEW.module_epoch < OLD.module_epoch) THEN
        RAISE EXCEPTION 'oppdrag: module_epoch kan aldri reduseres/nulles';
    END IF;
    IF OLD.kvittering IS NOT NULL
       AND (NEW.kvittering IS DISTINCT FROM OLD.kvittering
            OR NEW.kvittering_signatur IS DISTINCT FROM OLD.kvittering_signatur
            OR NEW.resultathash IS DISTINCT FROM OLD.resultathash) THEN
        RAISE EXCEPTION 'oppdrag: kvitteringen er uforanderlig når den først er lagret';
    END IF;
    IF NOT (
        (OLD.status = 'opprettet' AND NEW.status IN ('plukket','kansellert','feilet')) OR
        (OLD.status = 'plukket'   AND NEW.status IN ('utfort','feilet','opprettet')) OR
        (OLD.status = NEW.status)
    ) THEN
        RAISE EXCEPTION 'oppdrag: ulovlig statusovergang % -> %', OLD.status, NEW.status;
    END IF;
    IF OLD.status IN ('utfort','feilet','kansellert') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'oppdrag: % er terminal', OLD.status;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        NEW.status_ts := now();
    END IF;
    RETURN NEW;
END $$;

-- ------------------------------------------------------------
-- 7. Kjedens funksjoner. Samme eier som outbox-familiens opphavsveier
--    (disponit_m37_claimer) — tabellene tar INSERT kun gjennom dem.
SET LOCAL ROLE disponit_m37_claimer;

-- 7a. Ny listeversjon. Roten (forrige=NULL) og barn samme vei; lineariteten
--     og serie-bindingen håndheves av indeksene/FK-ene i §1 — funksjonen
--     validerer ingenting indeksene alt beviser, den setter bare feltene
--     selv (aldri fra request-kroppen).
CREATE FUNCTION opprett_utsendingsliste(
    p_tenant TEXT, p_utkast_serie UUID, p_forrige UUID, p_oppdrag_id BIGINT,
    p_listetype TEXT, p_malversjon TEXT, p_innhold_hash TEXT, p_antall INT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID := gen_random_uuid();
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'opprett_utsendingsliste');
    INSERT INTO public.utsendingsliste (tenant, liste_id, utkast_serie,
        forrige_liste_id, oppdrag_id, listetype, malversjon, innhold_hash,
        antall)
    VALUES (p_tenant, v_id, p_utkast_serie, p_forrige, p_oppdrag_id,
            p_listetype, p_malversjon, p_innhold_hash, p_antall);
    RETURN v_id;
END $$;

-- 7b. Signaturen. SP-2 på operasjonsnøkkelen, og HELE innholdet er
--     materielt — også signataren (055-lærdommen: aktøren er materiell;
--     to parter kan ikke begge tro de signerte).
CREATE FUNCTION signer_utsendingsliste(
    p_tenant TEXT, p_liste_id UUID, p_signatar TEXT, p_nokkel TEXT)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE l RECORD; s RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'signer_utsendingsliste');
    SELECT * INTO l FROM public.utsendingsliste
     WHERE tenant = p_tenant AND liste_id = p_liste_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'signer_utsendingsliste: ukjent liste %', p_liste_id
            USING ERRCODE = 'no_data_found';
    END IF;
    SELECT * INTO s FROM public.utsendingssignatur
     WHERE tenant = p_tenant AND operasjonsnokkel = p_nokkel;
    IF FOUND THEN
        IF s.liste_id IS DISTINCT FROM p_liste_id
           OR s.signatar IS DISTINCT FROM p_signatar THEN
            RAISE EXCEPTION 'signer_utsendingsliste: nøkkel % gjenbrukt med'
                ' annet innhold', p_nokkel
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN;                                     -- identisk replay: no-op
    END IF;
    -- Innholdet signaturen binder settes fra LISTEN, aldri fra kallet:
    -- hash og serie er versjonens egne, og FK-ene i §2 beviser sammenhengen.
    -- Kappløpstaperen (Cursor P2 på #140): to samtidige kall passerer
    -- begge nøkkel-lesningen over, og taperen treffer unik-bruddet.
    -- Identisk replay er dokumentert no-op, så taperen skal inn i SAMME
    -- dom som en sekvensiell replay — 038s mønster.
    BEGIN
        INSERT INTO public.utsendingssignatur (tenant, liste_id,
            utkast_serie, innhold_hash, signatar, operasjonsnokkel)
        VALUES (p_tenant, p_liste_id, l.utkast_serie, l.innhold_hash,
                p_signatar, p_nokkel);
    EXCEPTION WHEN unique_violation THEN
        SELECT * INTO s FROM public.utsendingssignatur
         WHERE tenant = p_tenant AND operasjonsnokkel = p_nokkel;
        IF NOT FOUND
           OR s.liste_id IS DISTINCT FROM p_liste_id
           OR s.signatar IS DISTINCT FROM p_signatar THEN
            -- Bruddet var ikke nøkkelens (listen/serien alt signert av en
            -- annen operasjon) eller nøkkelen bærer annet innhold — det
            -- skal høres, aldri sluke.
            RAISE;
        END IF;
        -- vinnerens rad er identisk med dette kallet: no-op.
    END;
END $$;

-- 7c. Frigivelsen: én mottaker om gangen, idempotent på (liste, mottaker)
--     — unik-nøkkelen i §3 serialiserer, og FK-kjeden krever signaturen.
CREATE FUNCTION frigi_utsendelse(
    p_tenant TEXT, p_liste_id UUID, p_mottaker_ref TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE s RECORD; v_id UUID := gen_random_uuid(); v_eksisterende UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'frigi_utsendelse');
    SELECT * INTO s FROM public.utsendingssignatur
     WHERE tenant = p_tenant AND liste_id = p_liste_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'frigi_utsendelse: liste % er ikke signert',
            p_liste_id USING ERRCODE = 'no_data_found';
    END IF;
    -- Idempotent UNDER kappløp (Cursor P2 på #140): SELECT-så-INSERT lot
    -- taperen få unik-bruddet i fanget. `ON CONFLICT DO NOTHING` +
    -- gjenlesning gir begge kallerne VINNERENS id — 038s mønster
    -- (`sikre_sak_for_oppdrag`), i insert-form.
    INSERT INTO public.utsendingsfrigivelse (tenant, frigivelse_id,
        liste_id, innhold_hash, utkast_serie, mottaker_ref)
    VALUES (p_tenant, v_id, p_liste_id, s.innhold_hash, s.utkast_serie,
            p_mottaker_ref)
    ON CONFLICT (tenant, liste_id, mottaker_ref) DO NOTHING;
    SELECT frigivelse_id INTO v_eksisterende
      FROM public.utsendingsfrigivelse
     WHERE tenant = p_tenant AND liste_id = p_liste_id
       AND mottaker_ref = p_mottaker_ref;
    RETURN v_eksisterende;
END $$;

-- 7d. Eneste vei til opprinnelse='frigivelse' (klarsignal §2): setter
--     opprinnelse og frigivelse_id SELV, og krever at frigivelsesraden
--     finnes — CHECK-en i §5 håndhever formen ved direkte DML uansett.
CREATE FUNCTION opprett_frigivelsesoppdrag(
    p_tenant TEXT, p_frigivelse_id UUID, p_oppdragstype TEXT,
    p_handling TEXT, p_eiermodul TEXT, p_payload BYTEA, p_key_id TEXT,
    p_nonce BYTEA, p_utforelsesfrist TIMESTAMPTZ, p_evidensfrist TIMESTAMPTZ)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'opprett_frigivelsesoppdrag');
    PERFORM 1 FROM public.utsendingsfrigivelse
     WHERE tenant = p_tenant AND frigivelse_id = p_frigivelse_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'opprett_frigivelsesoppdrag: ukjent frigivelse %',
            p_frigivelse_id USING ERRCODE = 'no_data_found';
    END IF;
    -- Én frigivelse -> ett oppdrag (indeksen over håndhever det):
    -- kappløpstaperen får VINNERENS oppdrag tilbake — en irreversibel
    -- utsending skal aldri kunne dobles av et retry (Cursor P1 på #140).
    BEGIN
        INSERT INTO public.oppdrag (tenant, oppdragstype, handling,
            eiermodul, payload_kryptert, key_id, nonce, utforelsesfrist,
            evidensfrist, frigivelse_id, koblingsstatus, opprinnelse)
        VALUES (p_tenant, p_oppdragstype, p_handling, p_eiermodul,
                p_payload, p_key_id, p_nonce, p_utforelsesfrist,
                p_evidensfrist, p_frigivelse_id, 'KOBLET', 'frigivelse')
        RETURNING id INTO v_id;
    EXCEPTION WHEN unique_violation THEN
        SELECT id INTO v_id FROM public.oppdrag
         WHERE tenant = p_tenant AND frigivelse_id = p_frigivelse_id;
        IF NOT FOUND THEN
            RAISE;                       -- bruddet var ikke frigivelsens
        END IF;
    END;
    RETURN v_id;
END $$;

-- FUNKSJONSRETTIGHETENE EIES AV EIEREN (Cursor-runden på #140 målte
-- klassen): PostgreSQL gir nye funksjoner EXECUTE til PUBLIC ved
-- fødselen, og både REVOKE og GRANT på claimer-eide funksjoner fra
-- migrator er virkningsløse/ulovlige. Alt funksjonsrettslig skjer derfor
-- HER, under eierrollen, før fullmakten legges ned: PUBLIC trekkes, og
-- API-veiene (liste + signering) gis runtime; utsendingsveien (frigivelse
-- + frigivelsesoppdrag) gis driftsrollen NÅR den finnes — ingen
-- else-arm, for et fallback til runtime ville flytt videre til alt som
-- arver runtime (plan-arbeideren), og kjøreren (migrer.py) er den
-- autoritative rettighetskilden lokalt og i test.
REVOKE ALL ON FUNCTION opprett_utsendingsliste(TEXT, UUID, UUID, BIGINT,
    TEXT, TEXT, TEXT, INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION signer_utsendingsliste(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION frigi_utsendelse(TEXT, UUID, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION opprett_frigivelsesoppdrag(TEXT, UUID, TEXT, TEXT,
    TEXT, BYTEA, TEXT, BYTEA, TIMESTAMPTZ, TIMESTAMPTZ) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION opprett_utsendingsliste(TEXT, UUID, UUID, BIGINT,
    TEXT, TEXT, TEXT, INT) TO disponit;
GRANT EXECUTE ON FUNCTION signer_utsendingsliste(TEXT, UUID, TEXT, TEXT)
    TO disponit;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_varselsender') THEN
        GRANT EXECUTE ON FUNCTION frigi_utsendelse(TEXT, UUID, TEXT)
            TO disponit_varselsender;
        GRANT EXECUTE ON FUNCTION opprett_frigivelsesoppdrag(TEXT, UUID,
            TEXT, TEXT, TEXT, BYTEA, TEXT, BYTEA, TIMESTAMPTZ, TIMESTAMPTZ)
            TO disponit_varselsender;
    END IF;
END $$;

RESET ROLE;

-- ------------------------------------------------------------
-- 8. Tabellrettigheter (tabellene eies av migrator): lesing til runtime
--    og eieren av kjedefunksjonene; INSERT kun gjennom funksjonene.
REVOKE ALL ON utsendingsliste, utsendingssignatur, utsendingsfrigivelse
    FROM PUBLIC;
GRANT SELECT ON utsendingsliste, utsendingssignatur, utsendingsfrigivelse
    TO disponit;
GRANT SELECT, INSERT ON utsendingsliste, utsendingssignatur,
    utsendingsfrigivelse TO disponit_m37_claimer;
