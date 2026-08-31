-- 082: M-8 tidsvalg v1 (#M8 — tidsvalg-benen M-57s invitasjoner
-- trenger; eiers delegerte dommer 31/8, M8-DOMMER 1–5)
--
-- Kunden definerer intervjuslots per prosess, kandidaten velger uten
-- innlogging bak et kapabilitetstoken, kunden ser valgene. Ingen
-- kalenderskriving, ingen ICS, ingen oppdragstype. Husformene
-- gjenbrukes ordrett: tenant TEXT + RLS FORCE + tenant_isolasjon på
-- alle tre tabellene; 057-lagerformen for persondata (m8_slotvalg er
-- det ÅTTENDE medlemmet bak kandidatdatagrensen); 005-statusmaskinformen
-- for tokenet; 004-formen for konstanttids MAC; 077 for payloadvinduet
-- og 081 for klaim/revalidering.
--
-- SP-10: denne migrasjonen REPLACEr `reap_kandidatdata` og
-- `m57_lagrene_reapes_samlet` på bebodde tabeller — hele de gjeldende
-- kroppene (075/076) er kopiert, kun m8_slotvalg-armene er nye.
--
-- INGEN BEGIN/COMMIT: kjøreren eier transaksjonen.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_authenticator') THEN
        RAISE EXCEPTION 'rollen disponit_authenticator mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
    IF NOT pg_has_role(current_user, 'disponit_authenticator', 'MEMBER') THEN
        RAISE EXCEPTION 'migratorrollen % er ikke medlem av'
            ' disponit_authenticator — kreves for OWNER TO', current_user;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_m37_claimer') THEN
        RAISE EXCEPTION 'rollen disponit_m37_claimer mangler — kjør'
            ' deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

-- ------------------------------------------------------------
-- 1. m8_slot — kundens tilbudte tider. IKKE persondata; består etter
--    reaping som evidens (som prosessraden selv).
CREATE TABLE m8_slot (
    tenant TEXT NOT NULL,
    slot_id UUID NOT NULL,
    prosess_id UUID NOT NULL,
    start_ts TIMESTAMPTZ NOT NULL,
    slutt_ts TIMESTAMPTZ NOT NULL CHECK (slutt_ts > start_ts),
    kapasitet INT NOT NULL DEFAULT 1 CHECK (kapasitet BETWEEN 1 AND 50),
    status TEXT NOT NULL DEFAULT 'aktiv'
        CHECK (status IN ('aktiv','deaktivert')),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, slot_id),
    -- FK-målet for valget: sloten er bundet til prosessen sin også
    -- DEKLARATIVT (CodeRabbit på 082: et valg skal ikke kunne peke på
    -- en annen prosess' slot engang med direkte DML fra eieren).
    UNIQUE (tenant, prosess_id, slot_id),
    FOREIGN KEY (tenant, prosess_id)
        REFERENCES rekrutteringsprosess (tenant, prosess_id));
CREATE INDEX m8_slot_per_prosess ON m8_slot (tenant, prosess_id);

-- Radvakt (utsendingsliste-formen, med ETT lovlig unntak): tidene er
-- immutable etter INSERT — eneste lovlige UPDATE er statusovergangen
-- aktiv → deaktivert, DELETE/TRUNCATE avvises. Flytting av en tid er
-- deaktiver + ny rad, aldri en redigering — kandidaten valgte en TID,
-- og en slot som kan flyttes under valget ville gjort valget til en
-- påstand om noe annet enn det kandidaten så (Codex-port: slot-tid
-- endret etter opprettelse → vaktavvist).
CREATE FUNCTION m8_slot_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'm8_slot: % avvist — slots deaktiveres, de'
            ' slettes aldri som rader', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT (OLD.status = 'aktiv' AND NEW.status = 'deaktivert') THEN
        RAISE EXCEPTION 'm8_slot: eneste lovlige overgang er'
            ' aktiv -> deaktivert (fikk % -> %)', OLD.status, NEW.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF to_jsonb(NEW) - 'status' IS DISTINCT FROM to_jsonb(OLD) - 'status'
    THEN
        RAISE EXCEPTION 'm8_slot: tidene er immutable etter INSERT —'
            ' flytting er deaktiver + ny rad'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m8_slot_vakt() FROM PUBLIC;
CREATE TRIGGER m8_slot_vakt
    BEFORE UPDATE OR DELETE ON m8_slot
    FOR EACH ROW EXECUTE FUNCTION m8_slot_vakt();
CREATE TRIGGER m8_slot_ingen_truncate
    BEFORE TRUNCATE ON m8_slot
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

ALTER TABLE m8_slot ENABLE ROW LEVEL SECURITY;
ALTER TABLE m8_slot FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON m8_slot
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- Claimer eier kundedørene (opprett/deaktiver) og trenger radrettighetene.
GRANT SELECT, INSERT, UPDATE ON m8_slot TO disponit_m37_claimer;
-- Authenticator-døren m8_velg_slot LESER slots og tar radlåsen
-- (SELECT ... FOR UPDATE) som serialiserer kapasiteten. En låseklausul
-- krever UPDATE-rett på minst én kolonne — kolonnegranten er den
-- smaleste som gir låsen, og radvakten snevrer enhver faktisk UPDATE
-- til aktiv -> deaktivert uansett.
GRANT SELECT ON m8_slot TO disponit_authenticator;
GRANT UPDATE (status) ON m8_slot TO disponit_authenticator;

-- ------------------------------------------------------------
-- 2. m8_slotvalg — kandidatens valg. PERSONDATA: 057-lagerformen
--    ordrett, åttende lager bak kandidatdatagrensen. ETT valg per
--    kandidat per prosess (PK), payloaden er slot_id.
CREATE TABLE m8_slotvalg (
    tenant TEXT NOT NULL,
    prosess_id UUID NOT NULL,
    kandidat_id UUID NOT NULL,
    slot_id UUID,                          -- payloaden
    innhold_sha256 TEXT NOT NULL,          -- utledes av vakten
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    slettet_ts TIMESTAMPTZ,
    PRIMARY KEY (tenant, prosess_id, kandidat_id),   -- ETT valg
    FOREIGN KEY (tenant, prosess_id, kandidat_id)
        REFERENCES kandidat (tenant, prosess_id, kandidat_id),
    FOREIGN KEY (tenant, prosess_id, slot_id)
        REFERENCES m8_slot (tenant, prosess_id, slot_id),
    CONSTRAINT slotvalg_payload_folger_slettet
        CHECK ((slettet_ts IS NULL AND slot_id IS NOT NULL)
            OR (slettet_ts IS NOT NULL AND slot_id IS NULL)));
-- Kapasitetstellingen: levende valg per slot.
CREATE INDEX m8_slotvalg_per_slot ON m8_slotvalg (tenant, slot_id)
    WHERE slettet_ts IS NULL;

-- Lagervakten (077-versjonen av m57_kandidatlager_vakt) gjenbrukes
-- UENDRET: INSERT-forutsetningen utledes av payloadvinduet med FOR
-- SHARE, sha utledes av de lagrede bytene, eneste lovlige UPDATE er
-- reap-overgangen (slot_id til NULL), DELETE avvises.
CREATE TRIGGER m8_slotvalg_vakt
    BEFORE INSERT OR UPDATE OR DELETE ON m8_slotvalg
    FOR EACH ROW EXECUTE FUNCTION m57_kandidatlager_vakt('slot_id');
CREATE TRIGGER m8_slotvalg_ingen_truncate
    BEFORE TRUNCATE ON m8_slotvalg
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

ALTER TABLE m8_slotvalg ENABLE ROW LEVEL SECURITY;
ALTER TABLE m8_slotvalg FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON m8_slotvalg
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
-- Reaperen og den utsatte samlet-porten leser kryss-tenant (057-formen
-- ordrett: eksplisitt policy, aldri BYPASSRLS).
CREATE POLICY m57_reaper ON m8_slotvalg TO disponit_m37_claimer
    USING (CURRENT_USER = 'disponit_m37_claimer')
    WITH CHECK (CURRENT_USER = 'disponit_m37_claimer');

-- Claimer: reap-overgangen (UPDATE) og samlet-/deaktiveringslesing.
-- INGEN INSERT: kandidatens valg fødes KUN gjennom authenticator-døren
-- m8_velg_slot — kunden skriver aldri et valg på kandidatens vegne.
GRANT SELECT, UPDATE ON m8_slotvalg TO disponit_m37_claimer;
-- Authenticator-døren føder valget og leser kandidatens eget.
GRANT SELECT, INSERT ON m8_slotvalg TO disponit_authenticator;

-- Vindusdommen (077) og prosesslåsen: m8_velg_slot revaliderer
-- payloadvinduet med FOR SHARE på prosessraden (081-formen).
-- Kolonnegranten gir KUN låsen; radvakten på prosessen står uansett.
GRANT SELECT ON rekrutteringsprosess TO disponit_authenticator;
GRANT UPDATE (prosess_id) ON rekrutteringsprosess TO disponit_authenticator;
GRANT EXECUTE ON FUNCTION m57_payloadvindu(rekrutteringsprosess)
    TO disponit_authenticator;

-- ------------------------------------------------------------
-- 3. m8_tidsvalgtoken — kapabiliteten. Ingen klartekst-PII: serversiden
--    lagrer kun HMAC-SHA256(pepper, secret) (004-formen); lenkens token
--    er `tid_<token_id>.<secret>` og bæres i URL-FRAGMENTET, som aldri
--    forlater klienten. Radene består etter reaping (null PII) — alle
--    dører dømmer via m57_payloadvindu, så en gammel lenke på en reapet
--    prosess er død by construction.
CREATE TABLE m8_tidsvalgtoken (
    token_id TEXT PRIMARY KEY
        CHECK (token_id ~ '^[0-9a-f]{32}$'),
    -- Globalt oppslag: kandidaten har ingen tenantkontekst — raden
    -- BÆRER tenanten, som brukersesjon/api_tokener.
    tenant TEXT NOT NULL,
    prosess_id UUID NOT NULL,
    kandidat_id UUID NOT NULL,
    liste_id UUID NOT NULL,         -- den signerte invitasjonslisten
    mac TEXT NOT NULL CHECK (mac ~ '^[0-9a-f]{64}$'),
    status TEXT NOT NULL DEFAULT 'aktiv'
        CHECK (status IN ('aktiv','brukt','erstattet')),
    utstedt TIMESTAMPTZ NOT NULL DEFAULT now(),
    utloper TIMESTAMPTZ NOT NULL,
    brukt_ts TIMESTAMPTZ,
    FOREIGN KEY (tenant, prosess_id, kandidat_id)
        REFERENCES kandidat (tenant, prosess_id, kandidat_id),
    FOREIGN KEY (tenant, liste_id, kandidat_id)
        REFERENCES utsendingsliste_medlem (tenant, liste_id, kandidat_id));
CREATE UNIQUE INDEX en_aktiv_token_per_medlem ON m8_tidsvalgtoken
    (tenant, liste_id, kandidat_id) WHERE status = 'aktiv';

-- Statusmaskinen (005-formen): bindingsfeltene immutable; aktiv->brukt
-- og aktiv->erstattet er de eneste overgangene; brukt/erstattet er
-- terminale.
CREATE FUNCTION m8_tidsvalgtoken_statusmaskin()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.token_id IS DISTINCT FROM OLD.token_id
       OR NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.prosess_id IS DISTINCT FROM OLD.prosess_id
       OR NEW.kandidat_id IS DISTINCT FROM OLD.kandidat_id
       OR NEW.liste_id IS DISTINCT FROM OLD.liste_id
       OR NEW.mac IS DISTINCT FROM OLD.mac
       OR NEW.utstedt IS DISTINCT FROM OLD.utstedt
       OR NEW.utloper IS DISTINCT FROM OLD.utloper THEN
        RAISE EXCEPTION 'm8_tidsvalgtoken: identitets- og bindingsfelter'
            ' er uforanderlige';
    END IF;
    IF NOT (
        (OLD.status = 'aktiv' AND NEW.status IN ('brukt','erstattet')) OR
        (OLD.status = NEW.status)
    ) THEN
        RAISE EXCEPTION 'm8_tidsvalgtoken: ulovlig overgang % -> %',
            OLD.status, NEW.status;
    END IF;
    IF OLD.status IN ('brukt','erstattet') AND NEW.status <> OLD.status
    THEN
        RAISE EXCEPTION 'm8_tidsvalgtoken: % er terminal', OLD.status;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m8_tidsvalgtoken_statusmaskin() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION m8_tidsvalgtoken_statusmaskin()
    TO disponit_authenticator;
CREATE TRIGGER m8_tidsvalgtoken_overgang
    BEFORE UPDATE ON m8_tidsvalgtoken
    FOR EACH ROW EXECUTE FUNCTION m8_tidsvalgtoken_statusmaskin();

-- RLS: tenant_isolasjon som alle andre — men de offentlige dørene slår
-- opp på token_id ALENE, uten tenantkontekst, så eierrollen får en
-- eksplisitt policy (m57_reaper-formen: aldri BYPASSRLS).
ALTER TABLE m8_tidsvalgtoken ENABLE ROW LEVEL SECURITY;
ALTER TABLE m8_tidsvalgtoken FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON m8_tidsvalgtoken
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
CREATE POLICY m8_authenticator ON m8_tidsvalgtoken
    TO disponit_authenticator
    USING (CURRENT_USER = 'disponit_authenticator')
    WITH CHECK (CURRENT_USER = 'disponit_authenticator');

-- 004-presedensen: tokentabellen og de offentlige dørene eies av
-- authenticator; runtime får NULL tabellrettigheter (api_tokener-formen)
-- — kun definer-dørene.
ALTER TABLE m8_tidsvalgtoken OWNER TO disponit_authenticator;
REVOKE ALL ON m8_tidsvalgtoken FROM PUBLIC;
-- Utstederdøren er claimer-eid (den kalles av utsenderen med
-- tenantkontekst) og trenger radrettighetene.
GRANT SELECT, INSERT, UPDATE ON m8_tidsvalgtoken TO disponit_m37_claimer;

-- ------------------------------------------------------------
-- 4. Kunde-/utstederdørene — claimer-eide (056/057-formen: SECURITY
--    DEFINER bak krev_tenantkontekst-porten).
SET LOCAL ROLE disponit_m37_claimer;

-- Kundens nye tid: FOR SHARE på prosessraden + payloadvindu (077-formen
-- fra opprett_kandidat, ordrett) — en reapet eller frist-passert
-- prosess tilbyr ingen tider.
--
-- `p_slot_id` er SP-2-nøkkelens hånd inn i døren (056-materialitets-
-- formen): API-veien utleder id-en deterministisk av Idempotency-Key,
-- så et gjenspill med identisk innhold er et stille ja (raden finnes
-- alt), og samme id med ANNET innhold er en materiell konflikt. NULL
-- (direktekall) gir en fersk id.
CREATE FUNCTION m8_opprett_slot(
    p_tenant TEXT, p_prosess_id UUID, p_start TIMESTAMPTZ,
    p_slutt TIMESTAMPTZ, p_kapasitet INT DEFAULT 1,
    p_slot_id UUID DEFAULT NULL)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_prosess public.rekrutteringsprosess; v_id UUID;
        v_rad public.m8_slot;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm8_opprett_slot');
    v_id := coalesce(p_slot_id, gen_random_uuid());
    SELECT s.* INTO v_rad FROM public.m8_slot s
     WHERE s.tenant = p_tenant AND s.slot_id = v_id;
    IF FOUND THEN
        IF v_rad.prosess_id = p_prosess_id AND v_rad.start_ts = p_start
           AND v_rad.slutt_ts = p_slutt
           AND v_rad.kapasitet = p_kapasitet THEN
            RETURN v_id;              -- gjenspill: stille ja
        END IF;
        RAISE EXCEPTION 'm8_opprett_slot: slot_id % finnes alt med annet'
            ' innhold — materiell idempotenskonflikt', v_id
            USING ERRCODE = 'unique_violation';
    END IF;
    SELECT p.* INTO v_prosess
      FROM public.rekrutteringsprosess p
     WHERE p.tenant = p_tenant AND p.prosess_id = p_prosess_id
     FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm8_opprett_slot: prosessen finnes ikke —'
            ' tider tilbys bare under en levende prosess'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT public.m57_payloadvindu(v_prosess) THEN
        RAISE EXCEPTION 'm8_opprett_slot: prosessen er utenfor'
            ' payloadvinduet (reapet, bestilt sletting eller passert'
            ' frist) — forutsetningen utledes av ankerets'
            ' tilstandsmaskin (077)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    INSERT INTO public.m8_slot
        (tenant, slot_id, prosess_id, start_ts, slutt_ts, kapasitet)
    VALUES (p_tenant, v_id, p_prosess_id, p_start, p_slutt, p_kapasitet);
    RETURN v_id;
END $$;
REVOKE ALL ON FUNCTION m8_opprett_slot(TEXT, UUID, TIMESTAMPTZ,
    TIMESTAMPTZ, INT, UUID) FROM PUBLIC;

-- Deaktivering er fail-closed mot bekreftede valg (DOM 3: valget er
-- endelig — en slot et levende valg peker på, kan ikke trekkes under
-- kandidaten). Idempotent: en alt deaktivert slot er et stille ja.
CREATE FUNCTION m8_deaktiver_slot(p_tenant TEXT, p_slot_id UUID)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm8_deaktiver_slot');
    SELECT s.status INTO v_status
      FROM public.m8_slot s
     WHERE s.tenant = p_tenant AND s.slot_id = p_slot_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm8_deaktiver_slot: sloten finnes ikke'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_status = 'deaktivert' THEN
        RETURN TRUE;                  -- stille ja (idempotent)
    END IF;
    IF EXISTS (SELECT 1 FROM public.m8_slotvalg v
                WHERE v.tenant = p_tenant AND v.slot_id = p_slot_id
                  AND v.slettet_ts IS NULL) THEN
        RAISE EXCEPTION 'm8_deaktiver_slot: et bekreftet valg peker på'
            ' sloten — valget er endelig (DOM 3), tiden kan ikke trekkes'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    UPDATE public.m8_slot
       SET status = 'deaktivert'
     WHERE tenant = p_tenant AND slot_id = p_slot_id;
    RETURN TRUE;
END $$;
REVOKE ALL ON FUNCTION m8_deaktiver_slot(TEXT, UUID) FROM PUBLIC;

-- Utstederdøren — kalles av utsenderen (senderrollen) i egen committet
-- transaksjon FØR send(): en e-post med død lenke er urepresenterbar.
-- Krever manifestmedlem MED signatur på en invitasjonsliste og åpent
-- payloadvindu. Ev. eksisterende aktiv token settes `erstattet` (005-
-- statusmaskinen tillater nettopp den overgangen), så et nytt forsøk
-- etter `feilet` aldri etterlater to levende kapabiliteter.
-- utloper = least(now() + levetid, payloadvinduets slutt) — DOM 5:
-- ETT tak for oppslag og valg, og aldri lenger enn kundens frist.
CREATE FUNCTION m8_utsted_tidsvalgtoken(
    p_tenant TEXT, p_liste UUID, p_kandidat UUID, p_token_id TEXT,
    p_mac TEXT, p_levetid_dogn INT DEFAULT 30)
RETURNS TIMESTAMPTZ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_prosess UUID; v_utloper TIMESTAMPTZ;
        v_p public.rekrutteringsprosess;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm8_utsted_tidsvalgtoken');
    IF p_levetid_dogn IS NULL OR p_levetid_dogn < 1 THEN
        RAISE EXCEPTION 'm8_utsted_tidsvalgtoken: levetiden må være'
            ' minst ett døgn' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Manifestmedlem MED signatur, på en INVITASJONSLISTE.
    SELECT m.prosess_id INTO v_prosess
      FROM public.utsendingsliste_medlem m
      JOIN public.utsendingssignatur s
        ON s.tenant = m.tenant AND s.liste_id = m.liste_id
      JOIN public.utsendingsliste l
        ON l.tenant = m.tenant AND l.liste_id = m.liste_id
     WHERE m.tenant = p_tenant AND m.liste_id = p_liste
       AND m.kandidat_id = p_kandidat AND l.listetype = 'invitasjon';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm8_utsted_tidsvalgtoken: (%, %) er ikke et'
            ' SIGNERT invitasjonsmedlem — kapabiliteten utstedes bare'
            ' bak signaturen', p_liste, p_kandidat
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Payloadvinduet dømmes på radens NYE versjon (081-formen).
    SELECT pr.* INTO v_p FROM public.rekrutteringsprosess pr
     WHERE pr.tenant = p_tenant AND pr.prosess_id = v_prosess
       AND public.m57_payloadvindu(pr)
     FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm8_utsted_tidsvalgtoken: payloadvinduet er'
            ' lukket — ingen ny kapabilitet på en prosess forbi fristen'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    UPDATE public.m8_tidsvalgtoken
       SET status = 'erstattet'
     WHERE tenant = p_tenant AND liste_id = p_liste
       AND kandidat_id = p_kandidat AND status = 'aktiv';
    v_utloper := LEAST(
        pg_catalog.now() + p_levetid_dogn * interval '1 day',
        coalesce(v_p.lukket_ts, v_p.opprettet)
            + v_p.slettefrist_dogn * interval '1 day');
    INSERT INTO public.m8_tidsvalgtoken
        (token_id, tenant, prosess_id, kandidat_id, liste_id, mac,
         utloper)
    VALUES (p_token_id, p_tenant, v_prosess, p_kandidat, p_liste, p_mac,
            v_utloper);
    RETURN v_utloper;
END $$;
REVOKE ALL ON FUNCTION m8_utsted_tidsvalgtoken(TEXT, UUID, UUID, TEXT,
    TEXT, INT) FROM PUBLIC;

-- ------------------------------------------------------------
-- 5. Reaperen lærer det ÅTTENDE medlemmet. HELE 075-kroppen (gjeldende
--    versjon), diff-endret i ÉN blokk: m8_slotvalg tømmes i samme
--    iterasjon og transaksjon som de syv andre. Ingen egen frist:
--    valget følger prosessens slettefrist_dogn og tidligslettingen
--    gratis (077: vinduet er den ene kilden).
CREATE OR REPLACE FUNCTION reap_kandidatdata(p_grense INT DEFAULT 50)
RETURNS TABLE (tenant TEXT, prosess_id UUID)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_kontekst TEXT; v_naa TIMESTAMPTZ; v_makulert INT;
BEGIN
    v_kontekst := current_setting('disponit.tenant', true);
    v_naa := pg_catalog.now();
    FOR r IN
        -- Den FORLATTE prosessen (Codex P1): fristen løper fra lukkingen
        -- (§5), men en kjøring som krasjer eller kanselleres før
        -- `lukk_rekrutteringsprosess` etterlot en prosess som ALDRI ble
        -- lukket — og et predikat på `lukket_ts IS NOT NULL` utelukket
        -- den for alltid. Originaldokumentene og alt avledet ble stående
        -- i det uendelige, uansett hvor ferdig oppdraget var.
        -- Maks levetid er derfor den samme fristen målt fra FØDSELEN:
        -- ingen ny konstant, og strengere enn den lukkede veien (en
        -- prosess som lukkes, får alltid hele fristen fra lukkingen).
        -- Utførelsesfristen er 240 min, så en prosess som står åpen
        -- forbi hele slettefristen er forlatt, ikke i arbeid.
        SELECT p.tenant AS t, p.prosess_id AS pid, p.oppdrag_id AS oid,
               p.slettet_ts IS NOT NULL AS restanse
          FROM public.rekrutteringsprosess p
         WHERE (p.slettet_ts IS NULL
                AND (v_naa > coalesce(p.lukket_ts, p.opprettet)
                             + p.slettefrist_dogn * interval '1 day'
                     -- Tidligslettingen (069): en bestilt sletting reapes
                     -- i FØRSTE sveip, uavhengig av fristen — merket er
                     -- kundens egen korting av den.
                     OR p.slett_bestilt_ts IS NOT NULL))
            -- v1-restansen (073, BESLUTNING-168 §2): reapet av
            -- 057-reaperen FØR vaktene fantes, med rapportpayloaden
            -- stående. Tømmes ved kundefristen, dog SENEST på dommens
            -- dato. Grensen 31/8 gjør settet endelig — 069-vakten
            -- nekter merket mens payloaden består, så ingen nyere
            -- prosess kan høre til her.
            OR (p.slettet_ts IS NOT NULL
                AND p.slettet_ts < TIMESTAMPTZ '2026-08-31 00:00:00+00'
                AND v_naa > LEAST(coalesce(p.lukket_ts, p.opprettet)
                                  + p.slettefrist_dogn * interval '1 day',
                                  TIMESTAMPTZ '2026-09-14 00:00:00+02'))
         -- Bestilte slettinger FØRST (CodeRabbit): kunden har bedt
         -- eksplisitt, og et fullt sveip (p_grense) av frist-utløpte
         -- skal ikke skyve bestillingen til neste runde. Restansen SIST
         -- (073): den er terminal og kan aldri fortrenge levende arbeid.
         ORDER BY (p.slett_bestilt_ts IS NULL),
                  (p.slettet_ts IS NOT NULL),
                  coalesce(p.lukket_ts, p.opprettet)
         LIMIT p_grense
         FOR UPDATE OF p SKIP LOCKED
    LOOP
        PERFORM set_config('disponit.tenant', r.t, true);
        UPDATE public.kandidat_originaldokument k
           SET dokument = NULL, filnavn = NULL, innholdstype = NULL,
               storrelse_bytes = NULL, slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        UPDATE public.kandidat_parsettekst k
           SET tekst = NULL, slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        UPDATE public.kandidat_evalueringsartefakt k
           SET artefakt = NULL, slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        UPDATE public.kandidat_intervjusporsmal k
           SET sporsmal = NULL, slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        UPDATE public.kandidat_utsendingsdata k
           SET mottaker_ref = NULL, flettefelt = NULL, slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        UPDATE public.kandidat_avmaskering k
           SET felter = NULL, slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        -- M-8 (082): kandidatens tidsvalg er payload som alt annet —
        -- tømmes i SAMME iterasjon og transaksjon som de andre lagrene.
        -- Sloten selv består (evidens uten persondata); det er PEKEREN
        -- fra kandidaten som er persondata.
        UPDATE public.m8_slotvalg k
           SET slot_id = NULL, slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        -- ANKERET ER DET SYVENDE MEDLEMMET (#157): merket i samme
        -- iterasjon og transaksjon som lagrene — port 19s samlet-port
        -- måler nettopp at ingen vei gjennom denne funksjonen kan tømme
        -- lagrene og la ankeret leve.
        UPDATE public.kandidat k
           SET slettet_ts = v_naa
         WHERE k.tenant = r.t AND k.prosess_id = r.pid
           AND k.slettet_ts IS NULL;
        -- DEN PROMOTERTE RAPPORTEN ER OGSÅ KANDIDATPAYLOAD (#222, andre
        -- halvdel av Codex P1-2 på #220): den bærer funn,
        -- intervjuspørsmål og hele den blindede kildeteksten per
        -- kandidat, kryptert på tenantens DEK — og besto forbi fristen,
        -- fordi ingen reaper rørte `artefakt`. Makuleringen skjer i
        -- SAMME iterasjon og transaksjon som de seks lagrene: det finnes
        -- ingen vei gjennom denne funksjonen der lagrene tømmes og
        -- rapporten består. Døren eies av artefakt-autoriteten
        -- (domene_eier, 016-familien) — reaperen SPØR den, den får aldri
        -- rå UPDATE på evidenstabellen (#181-formen).
        v_makulert := public.makuler_artefakter_for_prosess(r.t, r.oid,
                                                            v_naa);
        -- En restanserad der døren alt har tømt alt er et stille nei:
        -- den rapporteres ikke som reapet igjen, og prosessraden røres
        -- ikke (merket og lukkingen står som de historisk ble satt).
        IF r.restanse THEN
            IF v_makulert > 0 THEN
                tenant := r.t; prosess_id := r.pid;
                RETURN NEXT;
            END IF;
            CONTINUE;
        END IF;
        -- En forlatt prosess lukkes ved FØDSELEN i samme setning som den
        -- reapes: `prosess_reapet_krever_lukket` skal fortsatt holde, og
        -- radvakten godtar nettopp denne retningen (lukking bakover
        -- korter fristen, den forlenger den aldri).
        UPDATE public.rekrutteringsprosess p2
           SET lukket_ts = coalesce(p2.lukket_ts, p2.opprettet),
               slettet_ts = v_naa
         WHERE p2.tenant = r.t AND p2.prosess_id = r.pid;
        tenant := r.t; prosess_id := r.pid;
        RETURN NEXT;
    END LOOP;
    PERFORM set_config('disponit.tenant', coalesce(v_kontekst, ''), true);
END $$;

-- ------------------------------------------------------------
-- 6. Samlet-porten lærer det åttende medlemmet (SPEIL av 076-kroppen,
--    som er 075-kroppen med de sju medlemmene — m8_slotvalg-armene er
--    de eneste nye).
CREATE OR REPLACE FUNCTION m57_lagrene_reapes_samlet()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.kandidat k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_originaldokument k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_parsettekst k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_evalueringsartefakt k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_intervjusporsmal k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_utsendingsdata k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_avmaskering k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NULL
        UNION ALL
        SELECT 1 FROM public.m8_slotvalg k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NULL)
       AND EXISTS (
        SELECT 1 FROM public.kandidat k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NOT NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_originaldokument k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NOT NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_parsettekst k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NOT NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_evalueringsartefakt k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NOT NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_intervjusporsmal k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NOT NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_utsendingsdata k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NOT NULL
        UNION ALL
        SELECT 1 FROM public.kandidat_avmaskering k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NOT NULL
        UNION ALL
        SELECT 1 FROM public.m8_slotvalg k
         WHERE k.tenant = NEW.tenant AND k.prosess_id = NEW.prosess_id
           AND k.slettet_ts IS NOT NULL) THEN
        RAISE EXCEPTION 'kandidatlagrene: prosess % hos % bærer både'
            ' levende og reapet payload ved COMMIT — de ÅTTE medlemmene'
            ' reapes SAMLET, aldri ett alene (klarsignalet §5, port 19)',
            NEW.prosess_id, NEW.tenant
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NULL;
END $$;

-- 076-markørsveipen utvides: m8_slotvalg armerer ankerets utsatte port
-- som de andre medlemmene. CREATE TRIGGER krever EXECUTE for
-- TABELLEIEREN, og grantet må gis av funksjonens EIER — altså her,
-- inne i claimer-blokka (076-formen).
DO $$
DECLARE v_eier TEXT;
BEGIN
    SELECT pg_catalog.pg_get_userbyid(relowner) INTO v_eier
      FROM pg_catalog.pg_class
     WHERE oid = 'public.m8_slotvalg'::regclass;
    EXECUTE format('GRANT EXECUTE ON FUNCTION'
                   ' m57_marker_beroert_prosess() TO %I', v_eier);
END $$;
RESET ROLE;

CREATE TRIGGER m8_slotvalg_beroert
    AFTER INSERT OR UPDATE ON m8_slotvalg
    FOR EACH ROW EXECUTE FUNCTION m57_marker_beroert_prosess();

-- ------------------------------------------------------------
-- 7. De offentlige dørene — authenticator-eide (004-presedensen:
--    konstanttiden bor i defineren). INGEN tenantkontekst inn: oppslag
--    på token_id alene, og først ETTER at MAC-en holdt settes
--    tenantkonteksten for lesingen.
SET LOCAL ROLE disponit_authenticator;

-- Oppslaget: (kandidatens eget valg, slots med binært ledig/fullt).
-- Uniform feildom utad: ukjent token, feil MAC, utløpt, erstattet,
-- reapet, lukket vindu = TOM RETUR — appen svarer tidsvalg_avvist uten
-- årsaksskille. Per slot KUN (slot_id, start_ts, slutt_ts, ledig
-- BOOLEAN) — aldri tellere, aldri hvem (DOM 4; restlekkasjen ved
-- kapasitet 1 — «fullt» = «én annen har valgt» — er sagt høyt i
-- klarsignalet).
CREATE FUNCTION m8_tidsvalg_oppslag(p_token_id TEXT, p_kandidat_mac TEXT)
RETURNS TABLE (ut_valgt_slot UUID, ut_slot_id UUID, ut_start TIMESTAMPTZ,
               ut_slutt TIMESTAMPTZ, ut_ledig BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tok public.m8_tidsvalgtoken; v_avvik INT;
        v_valgt UUID;
BEGIN
    -- Format-guarden FØR alt annet (004): fast lengde er forutsetningen
    -- for at posisjonssammenligningen under er konstant i det hele tatt.
    IF p_kandidat_mac IS NULL OR p_kandidat_mac !~ '^[0-9a-f]{64}$' THEN
        RETURN;
    END IF;
    SELECT t.* INTO v_tok FROM public.m8_tidsvalgtoken t
     WHERE t.token_id = p_token_id;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    -- Konstanttids MAC-sammenligning (004-kroppen ordrett): samme
    -- arbeid ved treff på tegn 1 som ved treff på tegn 64. Pepperet bor
    -- hos utsenderen/API-prosessen og aldri i basen.
    SELECT pg_catalog.count(*)::INT INTO v_avvik
      FROM pg_catalog.generate_series(1, 64) AS i
     WHERE pg_catalog.substr(v_tok.mac, i, 1)
           IS DISTINCT FROM pg_catalog.substr(p_kandidat_mac, i, 1);
    IF v_avvik <> 0 THEN
        RETURN;
    END IF;
    IF v_tok.status NOT IN ('aktiv', 'brukt')
       OR v_tok.utloper <= pg_catalog.now() THEN
        RETURN;
    END IF;
    -- Først NÅ — etter at MAC-en holdt — settes tenantkonteksten, for
    -- lesingen, transaksjonslokalt: prosessraden og slots står bak
    -- tenant_isolasjon, og uten kontekst er de usynlige (fail-closed).
    PERFORM set_config('disponit.tenant', v_tok.tenant, true);
    -- Payloadvinduet er den ene kilden (077): en reapet eller
    -- frist-passert prosess svarer som et ukjent token.
    PERFORM 1 FROM public.rekrutteringsprosess pr
      WHERE pr.tenant = v_tok.tenant AND pr.prosess_id = v_tok.prosess_id
        AND public.m57_payloadvindu(pr);
    IF NOT FOUND THEN
        RETURN;
    END IF;
    SELECT v.slot_id INTO v_valgt FROM public.m8_slotvalg v
     WHERE v.tenant = v_tok.tenant AND v.prosess_id = v_tok.prosess_id
       AND v.kandidat_id = v_tok.kandidat_id AND v.slettet_ts IS NULL;
    RETURN QUERY
    SELECT v_valgt, s.slot_id, s.start_ts, s.slutt_ts,
           ((SELECT pg_catalog.count(*) FROM public.m8_slotvalg v
              WHERE v.tenant = s.tenant AND v.slot_id = s.slot_id
                AND v.slettet_ts IS NULL) < s.kapasitet)
      FROM public.m8_slot s
     WHERE s.tenant = v_tok.tenant AND s.prosess_id = v_tok.prosess_id
       AND s.status = 'aktiv'
     ORDER BY s.start_ts, s.slot_id;
END $$;
REVOKE ALL ON FUNCTION m8_tidsvalg_oppslag(TEXT, TEXT) FROM PUBLIC;

-- Valget: samme autentisering, radlåsen på sloten serialiserer
-- kapasiteten (FOR UPDATE), payloadvindu-revalidering med FOR SHARE på
-- prosessraden (081-formen), kapasitetstelling UNDER låsen, INSERT
-- (vakten utleder sha) og token -> brukt i SAMME transaksjon.
-- Utfall: 'valgt' (også stille ja ved gjenspill med samme slot),
-- 'slot_fullt', 'valg_alt_registrert' (annen slot etter bekreftet valg
-- — DOM 3: valget er endelig). TOM RETUR er den uniforme avvisningen.
CREATE FUNCTION m8_velg_slot(p_token_id TEXT, p_kandidat_mac TEXT,
                             p_slot_id UUID)
RETURNS TABLE (ut_utfall TEXT, ut_start TIMESTAMPTZ, ut_slutt TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tok public.m8_tidsvalgtoken; v_avvik INT;
        v_valgt UUID; v_slot public.m8_slot; v_antall BIGINT;
BEGIN
    IF p_kandidat_mac IS NULL OR p_kandidat_mac !~ '^[0-9a-f]{64}$' THEN
        RETURN;
    END IF;
    -- TOKENRADEN LÅSES i skriveveien (CodeRabbit): to samtidige
    -- gjenspill av SAMME token serialiseres her — den andre venter, ser
    -- så `brukt` + det levende valget, og får sitt stille ja i stedet
    -- for et kappløp inn i PK-en eller et falskt `slot_fullt`.
    -- Oppslagsveien låser aldri (lesing skal ikke kunne holde skrivere).
    SELECT t.* INTO v_tok FROM public.m8_tidsvalgtoken t
     WHERE t.token_id = p_token_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    SELECT pg_catalog.count(*)::INT INTO v_avvik
      FROM pg_catalog.generate_series(1, 64) AS i
     WHERE pg_catalog.substr(v_tok.mac, i, 1)
           IS DISTINCT FROM pg_catalog.substr(p_kandidat_mac, i, 1);
    IF v_avvik <> 0 THEN
        RETURN;
    END IF;
    IF v_tok.status NOT IN ('aktiv', 'brukt')
       OR v_tok.utloper <= pg_catalog.now() THEN
        RETURN;
    END IF;
    -- Konteksten settes etter at MAC-en holdt (som i oppslaget):
    -- prosessraden står bak tenant_isolasjon og er ellers usynlig.
    PERFORM set_config('disponit.tenant', v_tok.tenant, true);
    -- PAYLOADVINDUET REVALIDERES UNDER SKRIVINGEN (081-formen): samme
    -- FOR SHARE som lagervakten — vinduet dømmes på radens NYE versjon,
    -- serialisert mot lukkeveiens FOR UPDATE.
    PERFORM 1 FROM public.rekrutteringsprosess pr
      WHERE pr.tenant = v_tok.tenant AND pr.prosess_id = v_tok.prosess_id
        AND public.m57_payloadvindu(pr)
      FOR SHARE;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    -- Et alt registrert, levende valg dømmer FØRST (DOM 3: endelig):
    -- gjenspill med samme slot er et stille ja, annen slot avvises.
    SELECT v.slot_id INTO v_valgt FROM public.m8_slotvalg v
     WHERE v.tenant = v_tok.tenant AND v.prosess_id = v_tok.prosess_id
       AND v.kandidat_id = v_tok.kandidat_id AND v.slettet_ts IS NULL;
    IF FOUND THEN
        IF v_valgt = p_slot_id THEN
            IF v_tok.status = 'aktiv' THEN
                UPDATE public.m8_tidsvalgtoken
                   SET status = 'brukt', brukt_ts = pg_catalog.now()
                 WHERE token_id = v_tok.token_id;
            END IF;
            RETURN QUERY SELECT 'valgt'::TEXT, s.start_ts, s.slutt_ts
              FROM public.m8_slot s
             WHERE s.tenant = v_tok.tenant AND s.slot_id = v_valgt;
            RETURN;
        END IF;
        RETURN QUERY SELECT 'valg_alt_registrert'::TEXT,
                            NULL::TIMESTAMPTZ, NULL::TIMESTAMPTZ;
        RETURN;
    END IF;
    -- Et brukt token uten levende valg (valget er reapet): kapabiliteten
    -- er forbrukt — uniform avvisning.
    IF v_tok.status <> 'aktiv' THEN
        RETURN;
    END IF;
    -- Radlåsen serialiserer kapasiteten: to samtidige valg på siste
    -- plass gir én vinner — telleren måles UNDER låsen.
    SELECT s.* INTO v_slot FROM public.m8_slot s
     WHERE s.tenant = v_tok.tenant AND s.slot_id = p_slot_id
       AND s.prosess_id = v_tok.prosess_id AND s.status = 'aktiv'
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN;                       -- ukjent/deaktivert slot: uniform
    END IF;
    SELECT pg_catalog.count(*) INTO v_antall FROM public.m8_slotvalg v
     WHERE v.tenant = v_slot.tenant AND v.slot_id = v_slot.slot_id
       AND v.slettet_ts IS NULL;
    IF v_antall >= v_slot.kapasitet THEN
        RETURN QUERY SELECT 'slot_fullt'::TEXT,
                            NULL::TIMESTAMPTZ, NULL::TIMESTAMPTZ;
        RETURN;
    END IF;
    INSERT INTO public.m8_slotvalg
        (tenant, prosess_id, kandidat_id, slot_id, innhold_sha256)
    VALUES (v_tok.tenant, v_tok.prosess_id, v_tok.kandidat_id,
            p_slot_id, '');           -- sha utledes av vakten
    UPDATE public.m8_tidsvalgtoken
       SET status = 'brukt', brukt_ts = pg_catalog.now()
     WHERE token_id = v_tok.token_id;
    RETURN QUERY SELECT 'valgt'::TEXT, v_slot.start_ts, v_slot.slutt_ts;
END $$;
REVOKE ALL ON FUNCTION m8_velg_slot(TEXT, TEXT, UUID) FROM PUBLIC;
RESET ROLE;
