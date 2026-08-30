-- 075: kandidatankeret (#157 — eiers dom 23/8 i #153, delegert myndighet)
--
-- «Fire eksistensvakter som etterligner en FK er nøyaktig formen huset
-- alltid avviser — deklarativt der det deklarative finnes.» 057 ga de
-- seks kandidatlagrene FK mot PROSESSEN, men kandidat_id var en fri
-- UUID i seks tabeller: ingenting bandt lagrene til SAMME kandidat, en
-- skrivefeil var en ny, lovlig kandidat med ett lager, og «alle lagre
-- for kandidat K» var en union, ikke et oppslag.
--
-- Ankeret bærer INGEN personopplysninger — identiteten er en UUID,
-- payloaden bor i lagrene. Fødselen går gjennom døren (aldri fri
-- INSERT fra runtime), reaperen lærer ankeret som SYVENDE medlem, og
-- port 19s katalogmåling fanger tabellen automatisk (FK-en mot
-- prosessen ER medlemskapet) — det er beviset på at porten virker.

-- ------------------------------------------------------------
-- 1. Ankeret.
CREATE TABLE kandidat (
    tenant TEXT NOT NULL,
    prosess_id UUID NOT NULL,
    kandidat_id UUID NOT NULL,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    slettet_ts TIMESTAMPTZ,
    CONSTRAINT kandidat_pk PRIMARY KEY (tenant, prosess_id, kandidat_id),
    CONSTRAINT kandidat_prosess_fk FOREIGN KEY (tenant, prosess_id)
        REFERENCES rekrutteringsprosess (tenant, prosess_id)
);

-- Samme RLS-form som lagrene (057-loopen, ordrett for det ene medlemmet).
ALTER TABLE kandidat ENABLE ROW LEVEL SECURITY;
ALTER TABLE kandidat FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON kandidat
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
CREATE POLICY m57_reaper ON kandidat TO disponit_m37_claimer
    USING (CURRENT_USER = 'disponit_m37_claimer')
    WITH CHECK (CURRENT_USER = 'disponit_m37_claimer');
-- Claimeren (reaperen + fødselsdøren) får radrettighetene her; RUNTIME
-- får sine (SELECT + EXECUTE på døren) av migrer.py-regranten — rollens
-- NAVN er et installasjonsvalg ({rolle}), aldri hardkodet i en
-- migrasjon (CodeRabbit, #140-formen).
GRANT SELECT, INSERT, UPDATE ON kandidat TO disponit_m37_claimer;

-- ------------------------------------------------------------
-- 2. Backfill fra bebodde lagre (#157: «hører til denne PR-en, ikke en
--    senere»): unionen av de seks lagrenes distinkte tripler, med
--    ankermerket speilet av PROSESSENS — en alt reapet prosess får et
--    alt reapet anker, så samlet-porten står like hel etterpå.
--    072-formen: FORCE RLS av under backfillen, på igjen i samme
--    transaksjon.
ALTER TABLE kandidat NO FORCE ROW LEVEL SECURITY;
ALTER TABLE rekrutteringsprosess NO FORCE ROW LEVEL SECURITY;
ALTER TABLE kandidat_originaldokument NO FORCE ROW LEVEL SECURITY;
ALTER TABLE kandidat_parsettekst NO FORCE ROW LEVEL SECURITY;
ALTER TABLE kandidat_evalueringsartefakt NO FORCE ROW LEVEL SECURITY;
ALTER TABLE kandidat_intervjusporsmal NO FORCE ROW LEVEL SECURITY;
ALTER TABLE kandidat_utsendingsdata NO FORCE ROW LEVEL SECURITY;
ALTER TABLE kandidat_avmaskering NO FORCE ROW LEVEL SECURITY;
INSERT INTO kandidat (tenant, prosess_id, kandidat_id, slettet_ts)
SELECT u.tenant, u.prosess_id, u.kandidat_id, p.slettet_ts
  FROM (SELECT DISTINCT tenant, prosess_id, kandidat_id
          FROM (SELECT tenant, prosess_id, kandidat_id
                  FROM kandidat_originaldokument
                UNION SELECT tenant, prosess_id, kandidat_id
                  FROM kandidat_parsettekst
                UNION SELECT tenant, prosess_id, kandidat_id
                  FROM kandidat_evalueringsartefakt
                UNION SELECT tenant, prosess_id, kandidat_id
                  FROM kandidat_intervjusporsmal
                UNION SELECT tenant, prosess_id, kandidat_id
                  FROM kandidat_utsendingsdata
                UNION SELECT tenant, prosess_id, kandidat_id
                  FROM kandidat_avmaskering) s) u
  JOIN rekrutteringsprosess p
    ON p.tenant = u.tenant AND p.prosess_id = u.prosess_id;
ALTER TABLE kandidat_avmaskering FORCE ROW LEVEL SECURITY;
ALTER TABLE kandidat_utsendingsdata FORCE ROW LEVEL SECURITY;
ALTER TABLE kandidat_intervjusporsmal FORCE ROW LEVEL SECURITY;
ALTER TABLE kandidat_evalueringsartefakt FORCE ROW LEVEL SECURITY;
ALTER TABLE kandidat_parsettekst FORCE ROW LEVEL SECURITY;
ALTER TABLE kandidat_originaldokument FORCE ROW LEVEL SECURITY;
ALTER TABLE rekrutteringsprosess FORCE ROW LEVEL SECURITY;
ALTER TABLE kandidat FORCE ROW LEVEL SECURITY;

-- ------------------------------------------------------------
-- 3. FK-ene: kandidaten er nå deklarativ i alle seks lagre.
ALTER TABLE kandidat_originaldokument
    ADD CONSTRAINT originaldokument_kandidat_fk
    FOREIGN KEY (tenant, prosess_id, kandidat_id)
    REFERENCES kandidat (tenant, prosess_id, kandidat_id);
ALTER TABLE kandidat_parsettekst
    ADD CONSTRAINT parsettekst_kandidat_fk
    FOREIGN KEY (tenant, prosess_id, kandidat_id)
    REFERENCES kandidat (tenant, prosess_id, kandidat_id);
ALTER TABLE kandidat_evalueringsartefakt
    ADD CONSTRAINT evalueringsartefakt_kandidat_fk
    FOREIGN KEY (tenant, prosess_id, kandidat_id)
    REFERENCES kandidat (tenant, prosess_id, kandidat_id);
ALTER TABLE kandidat_intervjusporsmal
    ADD CONSTRAINT intervjusporsmal_kandidat_fk
    FOREIGN KEY (tenant, prosess_id, kandidat_id)
    REFERENCES kandidat (tenant, prosess_id, kandidat_id);
ALTER TABLE kandidat_utsendingsdata
    ADD CONSTRAINT utsendingsdata_kandidat_fk
    FOREIGN KEY (tenant, prosess_id, kandidat_id)
    REFERENCES kandidat (tenant, prosess_id, kandidat_id);
ALTER TABLE kandidat_avmaskering
    ADD CONSTRAINT avmaskering_kandidat_fk
    FOREIGN KEY (tenant, prosess_id, kandidat_id)
    REFERENCES kandidat (tenant, prosess_id, kandidat_id);

-- ------------------------------------------------------------
-- 4. Fødselsdøren — samme form og eier som opprett_rekrutteringsprosess
--    (claimeren), samme FOR SHARE-serialisering mot reaperen som
--    lagervaktene: en reapet prosess føder ingen kandidat, og en
--    kandidat fødes LEVENDE. Idempotent: samme trippel er et stille ja
--    (dokumentstrømmen kaller den per medlem).
SET LOCAL ROLE disponit_m37_claimer;
CREATE FUNCTION opprett_kandidat(
    p_tenant TEXT, p_prosess_id UUID, p_kandidat_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_slettet TIMESTAMPTZ;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'opprett_kandidat');
    SELECT p.slettet_ts INTO v_slettet
      FROM public.rekrutteringsprosess p
     WHERE p.tenant = p_tenant AND p.prosess_id = p_prosess_id
     FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'opprett_kandidat: prosessen finnes ikke —'
            ' kandidater fødes bare under en levende prosess'
            ' (klarsignalet §5, port 18)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF v_slettet IS NOT NULL THEN
        RAISE EXCEPTION 'opprett_kandidat: prosessen er reapet —'
            ' ingen ny kandidat på en slettet prosess (klarsignalet §5)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    INSERT INTO public.kandidat (tenant, prosess_id, kandidat_id)
         VALUES (p_tenant, p_prosess_id, p_kandidat_id)
    ON CONFLICT (tenant, prosess_id, kandidat_id) DO NOTHING;
END $$;
REVOKE ALL ON FUNCTION opprett_kandidat(TEXT, UUID, UUID) FROM PUBLIC;

-- ------------------------------------------------------------
-- 5. Samlet-porten lærer ankeret (SPEIL av 057-kroppen + to nye armer):
--    et anker som lever mens lagrene er reapet — eller omvendt — er
--    nøyaktig den halvtomme tilstanden porten finnes for. Claimer-eid
--    definer: omskrivingen går samme vei inn (SP-10-læren i 057).
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
           AND k.slettet_ts IS NOT NULL) THEN
        RAISE EXCEPTION 'kandidatlagrene: prosess % hos % bærer både'
            ' levende og reapet payload ved COMMIT — de SJU medlemmene'
            ' reapes SAMLET, aldri ett alene (klarsignalet §5, port 19)',
            NEW.prosess_id, NEW.tenant
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NULL;
END $$;

-- ------------------------------------------------------------
-- 6. Reaperen lærer det syvende medlemmet (069/073-kroppen, diff-endret
--    i ÉN blokk: ankermerket settes i samme iterasjon og transaksjon
--    som de seks lagrene og makuleringsdøren).
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
RESET ROLE;
