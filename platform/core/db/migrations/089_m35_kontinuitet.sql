-- 089: M-35 krise- og kontinuitetsagent v1 — registeret og hendelses-
-- loggen (M-35-dommene 1–5, ratifisert 31/8 på eiers delegasjon).
--
-- Det bærende: registeret er TENANT-SKOPET (dom 1 — disponit-eier-
-- tenanten er første beboer, men formen er kundens fra dag én). Fire
-- tabeller: tjenestekartet (hva SKAL leve, med RTO/RPO-mål og
-- sha-bundet playbook-referanse — dom 3: godkjenning er git-merge,
-- filen er repo-YAML og referansen bærer innholdshashen), beredskaps-
-- kontaktene (hvem ringes, med målt bekreftelsesferskhet), hendelsen
-- (tekstnøkkel + parametre — ALDRI fritekst-PII i hodet) og hendelsens
-- tidslinje (append-only: en krisehåndtering som kan redigeres i
-- etterkant er ingen evidens). RTO/RPO-EVIDENSEN bor IKKE her: den
-- kommer fra backupskriptets statusfil (dom 4 — kun-ved-suksess,
-- atomisk; aldri journal-parsing, aldri egen restore), og tallet
-- navngis presist som restore-til-isolert-base-proxy (dom 5).
--
-- Husformene gjenbrukes ordrett: tenant TEXT + RLS ENABLE+FORCE +
-- tenant_isolasjon på alle fire (057/082-formen); radvakter som
-- gjelder ENHVER rolle, også eieren (011/053/056-doktrinen: «en vakt
-- som bare gjelder de rettighetsløse er ingen vakt»); ALL skriving
-- gjennom claimer-eide SECURITY DEFINER-dører bak krev_tenantkontekst
-- (056/057-formen), med SP-2-materialitet på de opprettende dørene
-- (056-formen: deterministisk id fra Idempotency-Key — gjenspill med
-- identisk innhold er et stille ja, samme id med annet innhold er en
-- materiell konflikt). Runtime har INGEN tabellrettigheter i
-- migrasjonen — SELECT og EXECUTE speiles i migrer.py (RETTIGHETER +
-- M37_RETTIGHETER_API), som for 057/082.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_m37_claimer') THEN
        RAISE EXCEPTION 'rollen disponit_m37_claimer mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

-- ------------------------------------------------------------
-- 1. kontinuitet_tjeneste — tjenestekartet. Én rad per referent per
--    tenant: hva som skal leve, hvor kritisk det er, hvilke RTO/RPO-mål
--    som gjelder og hvilken playbook (navn@sha256 — innholdsadressert
--    mot repo-YAML, dom 3) som eier gjenopprettingen. Raden er
--    OPPDATERBAR gjennom døren (kartet skal være ferskt), men
--    identiteten er frosset og rader slettes aldri — et kart der
--    innslag kan forsvinne stille er et kart ingen øvelse kan måle
--    ferskheten av.
-- ------------------------------------------------------------
CREATE TABLE kontinuitet_tjeneste (
    tenant TEXT NOT NULL,
    tjeneste_id UUID NOT NULL,
    referent_type TEXT NOT NULL
        CHECK (referent_type IN ('systemd_unit', 'modul', 'ekstern')),
    referent_id TEXT NOT NULL CHECK (length(btrim(referent_id)) > 0),
    kritikalitet TEXT NOT NULL
        CHECK (kritikalitet IN ('kritisk', 'viktig', 'normal')),
    rto_maal_s INT NOT NULL CHECK (rto_maal_s > 0),
    rpo_maal_s INT NOT NULL CHECK (rpo_maal_s > 0),
    -- navn@sha256: navnet peker på playbook-YAML-en i repoet, hashen
    -- pinner INNHOLDET (dom 3: merge er godkjenningen, deploy-porten
    -- verifiserer). En referanse uten hash er en peker på hva som
    -- helst; skjemaet nekter å bære den.
    playbook_ref TEXT NOT NULL
        CHECK (playbook_ref ~ '^[a-z0-9][a-z0-9._-]{0,127}@[0-9a-f]{64}$'),
    kontaktrolle TEXT NOT NULL CHECK (length(btrim(kontaktrolle)) > 0),
    oppdatert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL,
    CONSTRAINT kontinuitet_tjeneste_pk PRIMARY KEY (tenant, tjeneste_id),
    CONSTRAINT tjeneste_en_per_referent
        UNIQUE (tenant, referent_type, referent_id)
);

-- Radvakten: identiteten er frosset, DELETE/TRUNCATE avvises — også
-- for eieren. Innholdsfeltene er dørens (m35_oppdater_tjeneste setter
-- alltid oppdatert_ts/av); vakten måler at en UPDATE som rører innhold
-- også flytter oppdatert_ts — en «stille» redigering med gammelt
-- stempel er nøyaktig det ferskhetsmålingen ikke skal kunne lures av.
CREATE FUNCTION m35_tjeneste_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'kontinuitet_tjeneste: % avvist — kartinnslag'
            ' oppdateres, de slettes aldri som rader', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.tjeneste_id IS DISTINCT FROM OLD.tjeneste_id
       OR NEW.referent_type IS DISTINCT FROM OLD.referent_type
       OR NEW.referent_id IS DISTINCT FROM OLD.referent_id THEN
        RAISE EXCEPTION 'kontinuitet_tjeneste: identiteten (tenant,'
            ' tjeneste_id, referent) er frosset — en annen referent er'
            ' et nytt kartinnslag'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.oppdatert_ts IS NOT DISTINCT FROM OLD.oppdatert_ts
       AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
        RAISE EXCEPTION 'kontinuitet_tjeneste: en innholdsendring uten'
            ' nytt oppdatert_ts er en redigering ferskhetsmålingen ikke'
            ' ser — gå gjennom m35_oppdater_tjeneste'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m35_tjeneste_vakt() FROM PUBLIC;
CREATE TRIGGER m35_tjeneste_vakt
    BEFORE UPDATE OR DELETE ON kontinuitet_tjeneste
    FOR EACH ROW EXECUTE FUNCTION m35_tjeneste_vakt();
CREATE TRIGGER m35_tjeneste_ingen_truncate
    BEFORE TRUNCATE ON kontinuitet_tjeneste
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

ALTER TABLE kontinuitet_tjeneste ENABLE ROW LEVEL SECURITY;
ALTER TABLE kontinuitet_tjeneste FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON kontinuitet_tjeneste
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

GRANT SELECT, INSERT, UPDATE ON kontinuitet_tjeneste
    TO disponit_m37_claimer;

-- ------------------------------------------------------------
-- 2. beredskapskontakt — hvem ringes, i hvilken rekkefølge. Bindes til
--    en EKSISTERENDE brukeridentitet (aldri løse navn/numre her —
--    kontaktdata bor hos identiteten). Bekreftelsen er en MÅLT
--    handling (bekreftet_ts/av settes sammen, av bekreftelsesdøren):
--    kontaktdekningen i øvelsen krever bekreftet < 90 døgn, og en
--    kontakt ingen har bekreftet er et funn, ikke en formalitet.
-- ------------------------------------------------------------
CREATE TABLE beredskapskontakt (
    tenant TEXT NOT NULL,
    kontakt_id UUID NOT NULL,
    rolle TEXT NOT NULL CHECK (length(btrim(rolle)) > 0),
    prioritet SMALLINT NOT NULL CHECK (prioritet BETWEEN 1 AND 9),
    bruker_id TEXT NOT NULL REFERENCES brukeridentitet (bruker_id),
    bekreftet_ts TIMESTAMPTZ,
    bekreftet_av TEXT,
    CONSTRAINT beredskapskontakt_pk PRIMARY KEY (tenant, kontakt_id),
    CONSTRAINT kontakt_en_per_rolleprioritet
        UNIQUE (tenant, rolle, prioritet),
    -- Samme NULL-sammen-form som hendelsens lukking: en bekreftelse
    -- uten bekrefter (eller omvendt) er en påstand uten avsender.
    CONSTRAINT kontakt_bekreftelse_komplett
        CHECK ((bekreftet_ts IS NULL) = (bekreftet_av IS NULL))
);

-- Radvakten: identiteten (rolle, prioritet, bruker) er frosset — en
-- annen person i rollen er en NY kontakt, aldri en redigering (ellers
-- arver den nye personens rad den gamles bekreftelse). Eneste lovlige
-- UPDATE er bekreftelsesoverganger, og bekreftet_ts aldri frem i tid —
-- en fremtidsbekreftelse ville holdt dekningen kunstig grønn.
CREATE FUNCTION m35_kontakt_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'beredskapskontakt: % avvist — en kontakt'
            ' erstattes med en ny rad, historikken består', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.kontakt_id IS DISTINCT FROM OLD.kontakt_id
       OR NEW.rolle IS DISTINCT FROM OLD.rolle
       OR NEW.prioritet IS DISTINCT FROM OLD.prioritet
       OR NEW.bruker_id IS DISTINCT FROM OLD.bruker_id THEN
        RAISE EXCEPTION 'beredskapskontakt: identiteten er frosset —'
            ' en annen person/rolle/prioritet er en ny kontakt'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.bekreftet_ts IS NOT NULL
       AND NEW.bekreftet_ts > pg_catalog.now() THEN
        RAISE EXCEPTION 'beredskapskontakt: bekreftet_ts kan ikke stå'
            ' frem i tid — det ville holdt kontaktdekningen kunstig'
            ' grønn' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m35_kontakt_vakt() FROM PUBLIC;
CREATE TRIGGER m35_kontakt_vakt
    BEFORE UPDATE OR DELETE ON beredskapskontakt
    FOR EACH ROW EXECUTE FUNCTION m35_kontakt_vakt();
CREATE TRIGGER m35_kontakt_ingen_truncate
    BEFORE TRUNCATE ON beredskapskontakt
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

ALTER TABLE beredskapskontakt ENABLE ROW LEVEL SECURITY;
ALTER TABLE beredskapskontakt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON beredskapskontakt
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

GRANT SELECT, INSERT, UPDATE ON beredskapskontakt
    TO disponit_m37_claimer;

-- ------------------------------------------------------------
-- 3. kontinuitetshendelse — hodet. Tekstnøkkel + parametre, aldri
--    fritekst i hodet (fritekst hører tidslinjen til, der aktøren
--    står ved den). Fødes ÅPEN; eneste livsløpsovergang er lukkingen,
--    og lukkedøren krever at etteranalysen ALT står i tidslinjen —
--    en krise uten etterlæring lukkes ikke.
-- ------------------------------------------------------------
CREATE TABLE kontinuitetshendelse (
    tenant TEXT NOT NULL,
    hendelse_id UUID NOT NULL,
    tekstnokkel TEXT NOT NULL
        CHECK (tekstnokkel ~ '^[a-z0-9][a-z0-9._-]{0,127}$'),
    parametre JSONB NOT NULL DEFAULT '{}'::jsonb,
    alvor TEXT NOT NULL
        CHECK (alvor IN ('kritisk', 'alvorlig', 'begrenset')),
    apnet_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    apnet_av TEXT NOT NULL,
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT,
    CONSTRAINT kontinuitetshendelse_pk PRIMARY KEY (tenant, hendelse_id),
    -- Planens egen CHECK: lukket-tid og lukker settes SAMMEN eller
    -- ikke i det hele tatt — en halv lukking er urepresenterbar.
    CONSTRAINT hendelse_lukking_komplett
        CHECK ((lukket_ts IS NULL) = (lukket_av IS NULL))
);

-- Radvakten (057-formen: samme port som døren, med vilje duplisert —
-- vakten gjelder ENHVER rolle): fødes ÅPEN, identiteten er frosset,
-- eneste lovlige UPDATE er lukkeovergangen (én gang, aldri frem i
-- tid), DELETE/TRUNCATE avvises.
CREATE FUNCTION m35_hendelse_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.lukket_ts IS NOT NULL OR NEW.lukket_av IS NOT NULL THEN
            RAISE EXCEPTION 'kontinuitetshendelse: en hendelse fødes'
                ' ÅPEN — lukking er en egen, målt overgang'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'kontinuitetshendelse: % avvist — hendelser'
            ' lukkes, de slettes aldri', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.hendelse_id IS DISTINCT FROM OLD.hendelse_id
       OR NEW.tekstnokkel IS DISTINCT FROM OLD.tekstnokkel
       OR NEW.parametre IS DISTINCT FROM OLD.parametre
       OR NEW.alvor IS DISTINCT FROM OLD.alvor
       OR NEW.apnet_ts IS DISTINCT FROM OLD.apnet_ts
       OR NEW.apnet_av IS DISTINCT FROM OLD.apnet_av THEN
        RAISE EXCEPTION 'kontinuitetshendelse: hodet er frosset etter'
            ' fødselen — det som skjedde, står i tidslinjen'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.lukket_ts IS NOT NULL THEN
        RAISE EXCEPTION 'kontinuitetshendelse: hendelsen er alt lukket'
            ' — lukket er terminal'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.lukket_ts IS NULL THEN
        RAISE EXCEPTION 'kontinuitetshendelse: eneste lovlige overgang'
            ' er lukkingen' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.lukket_ts > pg_catalog.now() THEN
        RAISE EXCEPTION 'kontinuitetshendelse: lukket_ts kan ikke stå'
            ' frem i tid' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m35_hendelse_vakt() FROM PUBLIC;
CREATE TRIGGER m35_hendelse_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON kontinuitetshendelse
    FOR EACH ROW EXECUTE FUNCTION m35_hendelse_vakt();
CREATE TRIGGER m35_hendelse_ingen_truncate
    BEFORE TRUNCATE ON kontinuitetshendelse
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

ALTER TABLE kontinuitetshendelse ENABLE ROW LEVEL SECURITY;
ALTER TABLE kontinuitetshendelse FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON kontinuitetshendelse
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

GRANT SELECT, INSERT, UPDATE ON kontinuitetshendelse
    TO disponit_m37_claimer;

-- ------------------------------------------------------------
-- 4. kontinuitetshendelse_post — tidslinjen. APPEND-ONLY i basens
--    egen håndhevelse (011/031-formen): UPDATE, DELETE og TRUNCATE
--    avvises for enhver rolle. Posten som fødes på en LUKKET hendelse
--    avvises av vakten — tidslinjen er lukket når hendelsen er det
--    (lukkedøren skriver sin egen 'lukket'-post FØR den flipper
--    hodet, i samme transaksjon).
-- ------------------------------------------------------------
CREATE TABLE kontinuitetshendelse_post (
    tenant TEXT NOT NULL,
    hendelse_id UUID NOT NULL,
    post_id UUID NOT NULL,
    posttype TEXT NOT NULL
        CHECK (posttype IN ('opprettet', 'observasjon', 'tiltak',
                            'statusendring', 'etteranalyse', 'lukket')),
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    aktor TEXT NOT NULL,
    tekst TEXT NOT NULL,
    CONSTRAINT kontinuitetshendelse_post_pk
        PRIMARY KEY (tenant, hendelse_id, post_id),
    CONSTRAINT post_hendelse_fk FOREIGN KEY (tenant, hendelse_id)
        REFERENCES kontinuitetshendelse (tenant, hendelse_id)
);

CREATE FUNCTION m35_post_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'kontinuitetshendelse_post er append-only:'
            ' % er forbudt — tidslinjen er evidensen', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Fødsel bare på en ÅPEN hendelse — LÅST lesning (057-lærdommen:
    -- en ulåst sjekk er en påstand om fortiden; FOR SHARE står ikke i
    -- konflikt med andre posters FOR SHARE, men serialiserer mot
    -- lukkeveiens FOR UPDATE).
    PERFORM 1 FROM public.kontinuitetshendelse h
        WHERE h.tenant = NEW.tenant AND h.hendelse_id = NEW.hendelse_id
          AND h.lukket_ts IS NULL
        FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'kontinuitetshendelse_post: hendelsen er lukket'
            ' eller finnes ikke — tidslinjen tar ikke imot flere poster'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m35_post_vakt() FROM PUBLIC;
CREATE TRIGGER m35_post_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON kontinuitetshendelse_post
    FOR EACH ROW EXECUTE FUNCTION m35_post_vakt();
CREATE TRIGGER m35_post_ingen_truncate
    BEFORE TRUNCATE ON kontinuitetshendelse_post
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

ALTER TABLE kontinuitetshendelse_post ENABLE ROW LEVEL SECURITY;
ALTER TABLE kontinuitetshendelse_post FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON kontinuitetshendelse_post
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- Ingen UPDATE-grant: append-only også i rettighetslaget.
GRANT SELECT, INSERT ON kontinuitetshendelse_post
    TO disponit_m37_claimer;

-- Tidslinjelesing i FK-rekkefølge er flatens; en indeks på tid gjør
-- den til et indeksoppslag, ikke en sortering.
CREATE INDEX kontinuitetshendelse_post_tid
    ON kontinuitetshendelse_post (tenant, hendelse_id, ts);

-- ============================================================
-- 5. Dørene — claimer-eide (056/057-formen: SECURITY DEFINER bak
--    krev_tenantkontekst-porten, som claimeren eier og PUBLIC mistet
--    i 038; eierens egne kall er den eneste veien inn, derfor kjører
--    hele blokken under SET LOCAL ROLE og alle rettighetsendringer på
--    funksjonene står INNE i blokken, #140-læren). Runtime-EXECUTE
--    speiles i migrer.py (M37_RETTIGHETER_API), aldri her.
-- ============================================================
SET LOCAL ROLE disponit_m37_claimer;

-- Kartinnslag: SP-2 på p_tjeneste_id (056-materialitetsformen — API-
-- veien utleder id-en deterministisk av Idempotency-Key; gjenspill med
-- identisk innhold er et stille ja, samme id med annet innhold en
-- materiell konflikt; NULL = direktekall, fersk id).
CREATE FUNCTION m35_opprett_tjeneste(
    p_tenant TEXT, p_referent_type TEXT, p_referent_id TEXT,
    p_kritikalitet TEXT, p_rto_maal_s INT, p_rpo_maal_s INT,
    p_playbook_ref TEXT, p_kontaktrolle TEXT, p_aktor TEXT,
    p_tjeneste_id UUID DEFAULT NULL)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID; v_rad public.kontinuitet_tjeneste;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm35_opprett_tjeneste');
    v_id := coalesce(p_tjeneste_id, gen_random_uuid());
    SELECT t.* INTO v_rad FROM public.kontinuitet_tjeneste t
     WHERE t.tenant = p_tenant AND t.tjeneste_id = v_id;
    IF FOUND THEN
        IF v_rad.referent_type = p_referent_type
           AND v_rad.referent_id = p_referent_id
           AND v_rad.kritikalitet = p_kritikalitet
           AND v_rad.rto_maal_s = p_rto_maal_s
           AND v_rad.rpo_maal_s = p_rpo_maal_s
           AND v_rad.playbook_ref = p_playbook_ref
           AND v_rad.kontaktrolle = p_kontaktrolle THEN
            RETURN v_id;              -- gjenspill: stille ja
        END IF;
        RAISE EXCEPTION 'm35_opprett_tjeneste: tjeneste_id % finnes alt'
            ' med annet innhold — materiell idempotenskonflikt', v_id
            USING ERRCODE = 'unique_violation';
    END IF;
    INSERT INTO public.kontinuitet_tjeneste
        (tenant, tjeneste_id, referent_type, referent_id, kritikalitet,
         rto_maal_s, rpo_maal_s, playbook_ref, kontaktrolle,
         oppdatert_av)
    VALUES (p_tenant, v_id, p_referent_type, p_referent_id,
            p_kritikalitet, p_rto_maal_s, p_rpo_maal_s, p_playbook_ref,
            p_kontaktrolle, p_aktor);
    RETURN v_id;
END $$;
REVOKE ALL ON FUNCTION m35_opprett_tjeneste(TEXT, TEXT, TEXT, TEXT,
    INT, INT, TEXT, TEXT, TEXT, UUID) FROM PUBLIC;

-- Oppdatering: innholdsfeltene, aldri identiteten (vakten står
-- uansett). oppdatert_ts/av settes ALLTID — det er ferskhetsmålingens
-- datagrunnlag.
CREATE FUNCTION m35_oppdater_tjeneste(
    p_tenant TEXT, p_tjeneste_id UUID, p_kritikalitet TEXT,
    p_rto_maal_s INT, p_rpo_maal_s INT, p_playbook_ref TEXT,
    p_kontaktrolle TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm35_oppdater_tjeneste');
    UPDATE public.kontinuitet_tjeneste
       SET kritikalitet = p_kritikalitet,
           rto_maal_s = p_rto_maal_s,
           rpo_maal_s = p_rpo_maal_s,
           playbook_ref = p_playbook_ref,
           kontaktrolle = p_kontaktrolle,
           oppdatert_ts = pg_catalog.now(),
           oppdatert_av = p_aktor
     WHERE tenant = p_tenant AND tjeneste_id = p_tjeneste_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm35_oppdater_tjeneste: kartinnslaget finnes'
            ' ikke' USING ERRCODE = 'no_data_found';
    END IF;
    RETURN TRUE;
END $$;
REVOKE ALL ON FUNCTION m35_oppdater_tjeneste(TEXT, UUID, TEXT, INT,
    INT, TEXT, TEXT, TEXT) FROM PUBLIC;

-- Kontakt: SP-2 som over. Fødes UBEKREFTET — bekreftelsen er en egen,
-- målt handling (dekningsporten krever ferskhet, ikke eksistens).
CREATE FUNCTION m35_opprett_kontakt(
    p_tenant TEXT, p_rolle TEXT, p_prioritet SMALLINT, p_bruker_id TEXT,
    p_aktor TEXT, p_kontakt_id UUID DEFAULT NULL)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID; v_rad public.beredskapskontakt;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm35_opprett_kontakt');
    v_id := coalesce(p_kontakt_id, gen_random_uuid());
    SELECT k.* INTO v_rad FROM public.beredskapskontakt k
     WHERE k.tenant = p_tenant AND k.kontakt_id = v_id;
    IF FOUND THEN
        IF v_rad.rolle = p_rolle AND v_rad.prioritet = p_prioritet
           AND v_rad.bruker_id = p_bruker_id THEN
            RETURN v_id;              -- gjenspill: stille ja
        END IF;
        RAISE EXCEPTION 'm35_opprett_kontakt: kontakt_id % finnes alt'
            ' med annet innhold — materiell idempotenskonflikt', v_id
            USING ERRCODE = 'unique_violation';
    END IF;
    INSERT INTO public.beredskapskontakt
        (tenant, kontakt_id, rolle, prioritet, bruker_id)
    VALUES (p_tenant, v_id, p_rolle, p_prioritet, p_bruker_id);
    RETURN v_id;
END $$;
REVOKE ALL ON FUNCTION m35_opprett_kontakt(TEXT, TEXT, SMALLINT, TEXT,
    TEXT, UUID) FROM PUBLIC;

-- Bekreftelsen: re-bekreftelse er LOVLIG og er selve poenget —
-- dekningen måler ferskhet (< 90 døgn), og fornyelsen er veien til
-- grønt. Aktøren står i raden; vakten nekter fremtidsstempler.
CREATE FUNCTION m35_bekreft_kontakt(
    p_tenant TEXT, p_kontakt_id UUID, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm35_bekreft_kontakt');
    UPDATE public.beredskapskontakt
       SET bekreftet_ts = pg_catalog.now(), bekreftet_av = p_aktor
     WHERE tenant = p_tenant AND kontakt_id = p_kontakt_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm35_bekreft_kontakt: kontakten finnes ikke'
            USING ERRCODE = 'no_data_found';
    END IF;
    RETURN TRUE;
END $$;
REVOKE ALL ON FUNCTION m35_bekreft_kontakt(TEXT, UUID, TEXT)
    FROM PUBLIC;

-- Hendelsen: SP-2 som over; 'opprettet'-posten skrives i SAMME
-- transaksjon — en hendelse uten fødselspost i tidslinjen finnes ikke.
CREATE FUNCTION m35_opprett_hendelse(
    p_tenant TEXT, p_tekstnokkel TEXT, p_parametre JSONB, p_alvor TEXT,
    p_aktor TEXT, p_hendelse_id UUID DEFAULT NULL)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID; v_rad public.kontinuitetshendelse;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm35_opprett_hendelse');
    v_id := coalesce(p_hendelse_id, gen_random_uuid());
    SELECT h.* INTO v_rad FROM public.kontinuitetshendelse h
     WHERE h.tenant = p_tenant AND h.hendelse_id = v_id;
    IF FOUND THEN
        IF v_rad.tekstnokkel = p_tekstnokkel
           AND v_rad.parametre = coalesce(p_parametre, '{}'::jsonb)
           AND v_rad.alvor = p_alvor THEN
            RETURN v_id;              -- gjenspill: stille ja
        END IF;
        RAISE EXCEPTION 'm35_opprett_hendelse: hendelse_id % finnes'
            ' alt med annet innhold — materiell idempotenskonflikt',
            v_id USING ERRCODE = 'unique_violation';
    END IF;
    INSERT INTO public.kontinuitetshendelse
        (tenant, hendelse_id, tekstnokkel, parametre, alvor, apnet_av)
    VALUES (p_tenant, v_id, p_tekstnokkel,
            coalesce(p_parametre, '{}'::jsonb), p_alvor, p_aktor);
    INSERT INTO public.kontinuitetshendelse_post
        (tenant, hendelse_id, post_id, posttype, aktor, tekst)
    VALUES (p_tenant, v_id, gen_random_uuid(), 'opprettet', p_aktor,
            p_tekstnokkel);
    RETURN v_id;
END $$;
REVOKE ALL ON FUNCTION m35_opprett_hendelse(TEXT, TEXT, JSONB, TEXT,
    TEXT, UUID) FROM PUBLIC;

-- Tidslinjeposten: 'opprettet' og 'lukket' er DØRENES egne posttyper
-- (fødsel og lukking skriver dem selv) — et menneske legger
-- observasjon, tiltak, statusendring eller etteranalyse. SP-2 på
-- p_post_id; hendelsen låses FOR SHARE (serialisert mot lukkeveiens
-- FOR UPDATE), vakten backstopper.
CREATE FUNCTION m35_legg_post(
    p_tenant TEXT, p_hendelse_id UUID, p_posttype TEXT, p_tekst TEXT,
    p_aktor TEXT, p_post_id UUID DEFAULT NULL)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID; v_rad public.kontinuitetshendelse_post;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm35_legg_post');
    IF p_posttype NOT IN ('observasjon', 'tiltak', 'statusendring',
                          'etteranalyse') THEN
        RAISE EXCEPTION 'm35_legg_post: posttype % er dørens egen —'
            ' fødsel og lukking skriver sine poster selv', p_posttype
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_tekst IS NULL OR length(btrim(p_tekst)) = 0 THEN
        RAISE EXCEPTION 'm35_legg_post: en tom post er ingen post'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_id := coalesce(p_post_id, gen_random_uuid());
    SELECT p.* INTO v_rad FROM public.kontinuitetshendelse_post p
     WHERE p.tenant = p_tenant AND p.hendelse_id = p_hendelse_id
       AND p.post_id = v_id;
    IF FOUND THEN
        IF v_rad.posttype = p_posttype AND v_rad.tekst = p_tekst THEN
            RETURN v_id;              -- gjenspill: stille ja
        END IF;
        RAISE EXCEPTION 'm35_legg_post: post_id % finnes alt med annet'
            ' innhold — materiell idempotenskonflikt', v_id
            USING ERRCODE = 'unique_violation';
    END IF;
    PERFORM 1 FROM public.kontinuitetshendelse h
     WHERE h.tenant = p_tenant AND h.hendelse_id = p_hendelse_id
       AND h.lukket_ts IS NULL
     FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm35_legg_post: hendelsen er lukket eller'
            ' finnes ikke' USING ERRCODE = 'no_data_found';
    END IF;
    INSERT INTO public.kontinuitetshendelse_post
        (tenant, hendelse_id, post_id, posttype, aktor, tekst)
    VALUES (p_tenant, p_hendelse_id, v_id, p_posttype, p_aktor,
            p_tekst);
    RETURN v_id;
END $$;
REVOKE ALL ON FUNCTION m35_legg_post(TEXT, UUID, TEXT, TEXT, TEXT,
    UUID) FROM PUBLIC;

-- Lukkingen: KREVER at en etteranalyse-post finnes, og skriver
-- 'lukket'-posten + lukket_ts/av i ÉN transaksjon (planens §2-krav).
-- Hendelsen låses FOR UPDATE først, så etteranalyse-sjekken og
-- flippet dømmes på samme radversjon; en alt lukket hendelse er et
-- stille ja (gjenspill), aldri en ny 'lukket'-post.
CREATE FUNCTION m35_lukk_hendelse(
    p_tenant TEXT, p_hendelse_id UUID, p_aktor TEXT, p_tekst TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rad public.kontinuitetshendelse;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm35_lukk_hendelse');
    IF p_tekst IS NULL OR length(btrim(p_tekst)) = 0 THEN
        RAISE EXCEPTION 'm35_lukk_hendelse: lukkeposten trenger en'
            ' tekst' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT h.* INTO v_rad FROM public.kontinuitetshendelse h
     WHERE h.tenant = p_tenant AND h.hendelse_id = p_hendelse_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm35_lukk_hendelse: hendelsen finnes ikke'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_rad.lukket_ts IS NOT NULL THEN
        RETURN TRUE;                  -- alt lukket: stille ja
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.kontinuitetshendelse_post p
                    WHERE p.tenant = p_tenant
                      AND p.hendelse_id = p_hendelse_id
                      AND p.posttype = 'etteranalyse') THEN
        RAISE EXCEPTION 'm35_lukk_hendelse: ingen etteranalyse i'
            ' tidslinjen — en krise uten etterlæring lukkes ikke'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    INSERT INTO public.kontinuitetshendelse_post
        (tenant, hendelse_id, post_id, posttype, aktor, tekst)
    VALUES (p_tenant, p_hendelse_id, gen_random_uuid(), 'lukket',
            p_aktor, p_tekst);
    UPDATE public.kontinuitetshendelse
       SET lukket_ts = pg_catalog.now(), lukket_av = p_aktor
     WHERE tenant = p_tenant AND hendelse_id = p_hendelse_id;
    RETURN TRUE;
END $$;
REVOKE ALL ON FUNCTION m35_lukk_hendelse(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

RESET ROLE;

-- ------------------------------------------------------------
-- 6. rolle_scope-speilet (043 §6b-formen, som 044 §6 for plan-
--    scopene): basen skal kunne se det samme rollemønsteret som
--    app-laget — port 26 binder de to EKSAKT.
-- ------------------------------------------------------------
INSERT INTO rolle_scope (rolle, scope) VALUES
    ('leser', 'kontinuitet:read'),
    ('sikkerhet', 'kontinuitet:read'),
    ('admin', 'kontinuitet:read'),
    ('admin', 'kontinuitet:write')
ON CONFLICT DO NOTHING;
