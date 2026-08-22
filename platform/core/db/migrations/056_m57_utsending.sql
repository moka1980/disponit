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
-- TRUNCATE fyrer ALDRI radtriggere (Codex P2, runde 1): uten en egen
-- statement-vakt kunne eieren tømme hele bevisrekken uten å møte
-- `avvis_endring` én eneste gang. Samme par som resten av husets
-- append-only-tabeller (011, 014, 053).
DROP TRIGGER IF EXISTS utsendingsliste_ingen_truncate ON utsendingsliste;
CREATE TRIGGER utsendingsliste_ingen_truncate
    BEFORE TRUNCATE ON utsendingsliste
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

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
DROP TRIGGER IF EXISTS utsendingssignatur_ingen_truncate
    ON utsendingssignatur;
CREATE TRIGGER utsendingssignatur_ingen_truncate
    BEFORE TRUNCATE ON utsendingssignatur
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- ------------------------------------------------------------
-- 3. Frigivelsen: én rad per mottaker, og FK-kjeden krever at listen er
--    SIGNERT (port 6) — det finnes ingen representerbar frigivelse av en
--    usignert liste, uansett hvilken vei noen skriver.
--
--    RUNDE 3-BESLUTNING (K2 utløst på #140, ansvarlig valgte eksplisitt):
--    klarsignal §5s krav om at `mottaker_ref` slettes ved TTL-utløp hører
--    til TTL-kontrollpunktet (portene 18–20), IKKE til CP1. Runde 2 gjorde
--    kolonnen nullbar for å innfri §5 her og nå — men reaperen (funksjon
--    + rolle) finnes ikke ennå, og en nullbar mottakerreferanse under en
--    unik-nøkkel PÅ nøyaktig den kolonnen brøt idempotensen: PostgreSQL
--    regner NULL som ulik NULL, så en TTL-redigert mottaker kunne frigis
--    PÅ NYTT — mens et samtidig FØRSTEGANGS-kall (uten redaksjon) traff en
--    beslektet kappløpsklasse i samme runde. To runder på samme kolonne
--    for to ULIKE krav (slett kandidatreferansen vs. gjenkjenn den for
--    alltid) er definisjonen på et spesifikasjonsvalg — ikke et
--    formforsøk (K2 stopper formforsøk, ikke beslutninger).
--
--    Kolonnen er derfor NOT NULL igjen — skissens opprinnelige form i
--    §3. §5s krav flyttes i sin helhet til reaper-PR-en, som uansett må
--    eie funksjonen, rollen OG ta det spesifikasjonsvalget (durabel
--    pseudonymnøkkel, eller at TTL-utløp stenger listen for videre
--    frigivelse) — se diskusjonen på #140 for avveiningen.
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
DROP TRIGGER IF EXISTS utsendingsfrigivelse_ingen_truncate
    ON utsendingsfrigivelse;
CREATE TRIGGER utsendingsfrigivelse_ingen_truncate
    BEFORE TRUNCATE ON utsendingsfrigivelse
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

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
DECLARE v_id UUID := gen_random_uuid(); v_forelder_oppdrag BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'opprett_utsendingsliste');
    -- SERIEN PEKER PÅ ÉN EVALUERING (Cursor P2 på #140, runde 3): uten
    -- dette kunne et barn i en «lineær» serie likevel adoptere en ANNEN
    -- fullført evaluering enn forelderen sin — proveniensen ville
    -- forgrene seg inni en kjede klarsignalet beskriver som lineær.
    -- Forelderen eier evalueringspekeren; barnet arver den, det velger
    -- den ikke.
    IF p_forrige IS NOT NULL THEN
        SELECT oppdrag_id INTO v_forelder_oppdrag
          FROM public.utsendingsliste
         WHERE tenant = p_tenant AND liste_id = p_forrige;
        IF FOUND AND v_forelder_oppdrag IS DISTINCT FROM p_oppdrag_id THEN
            RAISE EXCEPTION 'opprett_utsendingsliste: barn må peke på'
                ' samme evalueringsoppdrag som forelderen (%), ikke %',
                v_forelder_oppdrag, p_oppdrag_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
    END IF;
    -- LISTEN PROMOTERER EN FULLFØRT EVALUERING (Codex P1 + Cursor P2 på
    -- #140, runde 2). FK-en på (tenant, oppdrag_id) sier bare at
    -- oppdraget finnes hos tenanten. Den skiller verken
    --   * RETNING — et frigivelsesoppdrag kunne startet en ny liste, og
    --     kjeden ville sirklet inn i seg selv, eller
    --   * PROVENIENS — feil oppdragstype, en kjøring som fortsatt går,
    --     eller en som feilet/ble kansellert, kunne bære en liste videre
    --     gjennom signatur og frigivelse som en gyldig kjede.
    -- Klarsignalet er entydig på begge: ÉN oppdragstype for evalueringen
    -- (`rekruttering.evaluering`, §1), og «avbrutt kjøring → INGEN
    -- promotert liste» (§7, port 28). Måles her, før raden finnes —
    -- etterpå er listen signerbar, og en signert liste er sendbar.
    PERFORM 1 FROM public.oppdrag o
     WHERE o.tenant = p_tenant AND o.id = p_oppdrag_id
       AND o.oppdragstype = 'rekruttering.evaluering'
       AND o.status = 'utfort'
       AND o.opprinnelse IN ('beslutning', 'm37_reparasjon');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'opprett_utsendingsliste: oppdrag % er ikke en'
            ' FULLFØRT rekruttering.evaluering hos % (kjeden starter aldri'
            ' i et frigivelsesoppdrag, og en avbrutt kjøring promoteres'
            ' aldri)', p_oppdrag_id, p_tenant
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
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
    -- SIGNATAREN ER AUTORISASJONEN (Codex P1 + Cursor P1, runde 2).
    -- FK-en mot `brukeridentitet` er GLOBAL: den sier bare at strengen er
    -- en kjent bruker et sted i installasjonen. Runtime har EXECUTE her og
    -- INSERT på `brukeridentitet` (010) — uten denne porten kunne den
    -- tilskrive signaturen en bruker i en ANNEN tenant, en avskrudd bruker
    -- eller en identitet den nettopp fabrikkerte, og senderen ville
    -- deretter sendt irreversibel e-post på et menneske som aldri sa ja.
    -- 043-doktrinen: den ene autorisasjonsinngangen runtime IKKE kan
    -- skrive er medlemskapet (OIDC-forvaltet, runtime har kun SELECT).
    -- ÆRLIG OM RESTEN: en kompromittert runtime kan fortsatt lese
    -- medlemskapstabellen og UTGI SEG FOR et aktivt medlem. Den resten er
    -- ikke lukkbar herfra (den forutsetter at basen kan verifisere
    -- konvolutten) — presis samme avgrensning som 043 skrev ned. Rolle-
    -- og scope-nivået hører til flatens egen autorisasjon (CP3), der
    -- signeringsrollen defineres; her bindes det basen faktisk eier.
    IF NOT EXISTS (
        SELECT 1 FROM public.brukermedlemskap m
         WHERE m.tenant = p_tenant AND m.bruker_id = p_signatar
           AND m.aktiv
    ) THEN
        RAISE EXCEPTION 'signer_utsendingsliste: signatar % mangler'
            ' aktivt medlemskap i %', p_signatar, p_tenant
            USING ERRCODE = 'insufficient_privilege';
    END IF;
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
        v_frigitt INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'frigi_utsendelse');
    -- SNAPSHOTKRAVET (Codex på #140, runde 4). Rotårsaken bak de tre
    -- rundene på denne funksjonen: BEGGE løftene her — taket mot det
    -- signerte `antall`, og «samme mottaker gir samme id» — er utledet av
    -- en LESNING (`count(*)`, og gjenlesningen etter `ON CONFLICT DO
    -- NOTHING`), ikke av en skrivekonflikt. En lesning ser bare det
    -- transaksjonens snapshot inneholder. Advisory-låsen serialiserer
    -- UTFØRELSEN, men den friskner ikke opp et snapshot: under REPEATABLE
    -- READ tas snapshotet ved transaksjonens første setning, så to kall
    -- som begge startet før den første committet, teller begge det gamle
    -- tallet og kan begge sette inn — antall=1 gir to irreversible
    -- e-poster. Samme snapshot gjør at gjenlesningen til slutt kan bomme
    -- på vinnerens rad og returnere NULL der kontrakten lover en id.
    --
    -- READ COMMITTED tar ferskt snapshot PER setning, så både tellingen
    -- og gjenlesningen etter låsen ser vinneren. SERIALIZABLE beholder
    -- ett snapshot, men SSI ser rw-avhengigheten mellom tellingen og den
    -- samtidige innsettingen og avbryter taperen med serialiseringsfeil —
    -- et retry, ikke en oversending. REPEATABLE READ er det ene nivået
    -- med snapshot OG uten SSI, og er derfor det ene vi må avvise.
    --
    -- K1: alternativet — en skrivekonfliktende teller på listeraden — ville
    -- krevd et hull i append-only-vakten (`avvis_endring`) og er ny maskin,
    -- altså egen PR. Se tråden.
    IF current_setting('transaction_isolation') = 'repeatable read' THEN
        RAISE EXCEPTION 'frigi_utsendelse: krever READ COMMITTED eller'
            ' SERIALIZABLE — under REPEATABLE READ er både telleporten mot'
            ' det signerte antallet og idempotensoppslaget blinde for'
            ' samtidige frigivelser'
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    -- Signaturen OG listens `antall` i samme oppslag: tallet mennesket
    -- fikk se i signaturdialogen («Dette sender N e-poster. Kan ikke
    -- angres.») bor på listeversjonen signaturen binder.
    SELECT sg.innhold_hash, sg.utkast_serie, l.antall INTO s
      FROM public.utsendingssignatur sg
      JOIN public.utsendingsliste l
        ON l.tenant = sg.tenant AND l.liste_id = sg.liste_id
     WHERE sg.tenant = p_tenant AND sg.liste_id = p_liste_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'frigi_utsendelse: liste % er ikke signert',
            p_liste_id USING ERRCODE = 'no_data_found';
    END IF;
    -- Allerede frigitt for DENNE mottakeren? Da er svaret gitt, og
    -- replayet skal aldri møte telleporten under — en liste som står på
    -- taket må fortsatt kunne svare idempotent på et retry.
    SELECT frigivelse_id INTO v_eksisterende
      FROM public.utsendingsfrigivelse
     WHERE tenant = p_tenant AND liste_id = p_liste_id
       AND mottaker_ref = p_mottaker_ref;
    IF v_eksisterende IS NOT NULL THEN
        RETURN v_eksisterende;
    END IF;
    -- DET SIGNERTE ANTALLET ER ET TAK (Codex P1, runde 2). Unikheten på
    -- (liste, mottaker) hindret bare DUBLETTER — den sa ingenting om hvor
    -- MANGE forskjellige mottakere senderen kunne frigi. En liste
    -- presentert som «N e-poster» kunne dermed gi flere enn N irreversible
    -- utsendelser, forbi til og med skjemaets 5000-grense, uten at noe
    -- menneske signerte for det. Taket er en del av signaturens løfte.
    --
    -- Serialisert med en advisory-lås per (tenant, liste) — 014s mønster.
    -- Uten den kunne to samtidige kall begge lese `antall - 1` og begge
    -- sette inn: en ren count-så-INSERT er nøyaktig det TOCTOU-et runde 1
    -- lukket andre steder i denne filen. Låsen krever ingen rettighet (i
    -- motsetning til `SELECT ... FOR UPDATE`, som ville krevd UPDATE på en
    -- append-only tabell) og faller ved commit.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'm57:frigi:' || p_tenant || ':' || p_liste_id::text, 0));
    -- GJENLES ETTER LÅSEN (Codex på #140, runde 3): to FØRSTEGANGS-kall
    -- for SAMME mottaker kan begge bomme på oppslaget over (ingen rad
    -- ennå), og taperen ville da møtt telleporten under i stedet for
    -- replay-svaret — presis når taket er lite (f.eks. antall=1) og
    -- vinneren alt har committet før taperen får låsen. Uten denne
    -- gjenlesningen fikk et helt legitimt samtidig FØRSTE forsøk et
    -- avvist svar der idempotens-kontrakten lovte samme id.
    SELECT frigivelse_id INTO v_eksisterende
      FROM public.utsendingsfrigivelse
     WHERE tenant = p_tenant AND liste_id = p_liste_id
       AND mottaker_ref = p_mottaker_ref;
    IF v_eksisterende IS NOT NULL THEN
        RETURN v_eksisterende;
    END IF;
    SELECT count(*) INTO v_frigitt FROM public.utsendingsfrigivelse
     WHERE tenant = p_tenant AND liste_id = p_liste_id;
    IF v_frigitt >= s.antall THEN
        RAISE EXCEPTION 'frigi_utsendelse: liste % er signert for %'
            ' mottakere, og % er alt frigitt — en ny mottaker krever en ny'
            ' signert versjon', p_liste_id, s.antall, v_frigitt
            USING ERRCODE = 'invalid_parameter_value';
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
        -- RUNDE 3-BESLUTNING (K2 utløst på #140, ansvarlig valgte
        -- eksplisitt): materialiteten snevres til de DETERMINISTISKE
        -- feltene. `db/kryptering.py` genererer en FERSK tilfeldig nonce
        -- for HVER kryptering (AES-GCM) — et legitimt retry som
        -- krypterer identisk klartekst på nytt (etter en tvetydig
        -- commit/timeout) ville ALDRI fått samme `payload_kryptert`/
        -- `nonce` som forrige forsøk. Runde 2s fulle byte-likhet målte
        -- derfor feil ting: den avviste nøyaktig det retryet som skulle
        -- godkjennes (Codex + Cursor, runde 3).
        --
        -- Ekte binding til det SIGNERTE innholdet er en annen sak (Funn 8,
        -- utsatt fra runde 2 — krever et per-mottaker-manifest ELLER en
        -- kalleroppgitt digest, altså ny maskin under K1) og står fortsatt
        -- åpen som eget spørsmål i tråden. Denne porten løser bare
        -- kappløps-/retry-klassen: samme frigivelse + samme
        -- jobbtype/håndterer/eiermodul/nøkkel er samme logiske utsendelse,
        -- uansett chiffertekstbyte. Fristene er utenfor materialiteten av
        -- samme grunn som før: et legitimt retry regner klokkefrister på
        -- nytt.
        SELECT id INTO v_id FROM public.oppdrag o
         WHERE o.tenant = p_tenant AND o.frigivelse_id = p_frigivelse_id
           AND o.oppdragstype = p_oppdragstype
           AND o.handling = p_handling
           AND o.eiermodul = p_eiermodul
           AND o.key_id = p_key_id;
        IF NOT FOUND THEN
            IF EXISTS (SELECT 1 FROM public.oppdrag
                        WHERE tenant = p_tenant
                          AND frigivelse_id = p_frigivelse_id) THEN
                RAISE EXCEPTION 'opprett_frigivelsesoppdrag: frigivelse %'
                    ' bærer alt et oppdrag med ANNET innhold — et retry'
                    ' beskriver samme oppdrag eller feiler', p_frigivelse_id
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
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

-- ... og `disponit` er LOKAL-/TESTNAVNET på runtime-rollen (Codex P1,
-- runde 5 — samme klasse 043 §14b skrev ned). `deploy/staging/migrer.py`
-- tar runtime-rollens navn som ARGUMENT: på en installasjon som kjører
-- med et annet navn er en literal grant her enten en hard feil (rollen
-- finnes ikke → hele 056 ruller tilbake, FØR kjøreren rekker
-- `M37_RETTIGHETER_API`), eller en STILLE feiltildeling — en urelatert
-- eller utrangert `disponit`-innlogging beholder EXECUTE på
-- signeringsveien, for kjøreren revoker aldri funksjonsgrants fra andre
-- roller enn den konfigurerte. Den autoritative granten er kjørerens
-- parameteriserte blokk; denne står betinget, med 043s form.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
    GRANT EXECUTE ON FUNCTION opprett_utsendingsliste(TEXT, UUID, UUID,
      BIGINT, TEXT, TEXT, TEXT, INT) TO disponit;
    GRANT EXECUTE ON FUNCTION signer_utsendingsliste(TEXT, UUID, TEXT, TEXT)
      TO disponit;
  END IF;
END $$;
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
-- Samme betingelse og samme grunn som funksjonsgrantene over: kjørerens
-- `M37_RETTIGHETER_API` eier lesetilgangen for den KONFIGURERTE
-- runtime-rollen (migrer.py §056-linjen), denne er lokal-/testnavnets
-- betingede speiling.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
    GRANT SELECT ON utsendingsliste, utsendingssignatur,
      utsendingsfrigivelse TO disponit;
  END IF;
END $$;
GRANT SELECT, INSERT ON utsendingsliste, utsendingssignatur,
    utsendingsfrigivelse TO disponit_m37_claimer;
