-- 073: v1-restansen inn i kandidatdatagrensen — med dato (BESLUTNING-168
-- §2, eierdato 30/8: payloaden tømmes senest 14. september 2026).
--
-- Restansen er prosessene som ble merket reapet av 057-reaperen FØR
-- 067/069 fantes: den nullet de seks kandidatlagrene og satte
-- `slettet_ts`, men rørte aldri `artefakt` — så den promoterte
-- v1-rapporten står igjen med funn, intervjuspørsmål og hele den
-- blindede kildeteksten, kryptert på tenantens DEK, mens merket
-- utelukker prosessen fra reaperen for alltid. «Terminale, reapes ikke»
-- er ikke et unntak fra sletteplikten (§2 i dommen).
--
-- Mekanismen er den som ALT er ratifisert: payload-tømming er ikke en
-- tilstandsendring (klarsignalet §5) — raden består med hash,
-- `makulert_ts` og tilstanden sin, og tømmingen går gjennom samme dør
-- (`makuler_artefakter_for_prosess`) og samme reaper som resten av
-- kandidatdatagrensen. Ingen ny mekanisme, ingen manuell makulering.
--
-- FRISTEN ER KUNDENS, DATOEN ER TAKET: restansen tømmes ved
-- coalesce(lukket_ts, opprettet) + slettefrist_dogn som alt annet — men
-- aldri senere enn 14. september 2026 (dommens «egen liten PR, med
-- dato»; eiervalget 30/8 ga to uker). `LEAST` er bare kortere-eller-lik
-- (§5s lovlige retning), og ingen historisk kolonne skrives om:
-- `slettefrist_dogn` har CHECK-spennet 30–365, og både lukkingen og
-- reap-merket er audit — en backfill som «kortet» dem ville forfalsket
-- historikken for å slippe å si datoen høyt.
--
-- Restansen er LUKKET BAKOVER: 069-vakten nekter merket mens rapporten
-- bærer payload, så ingen prosess reapet etter at den var på plass kan
-- være i denne klassen. Grensen 31. august 2026 (vakten var i drift
-- 30/8) gjør settet endelig — armen kan aldri vokse.
--
-- Armen kan ikke spørre `artefakt` i ytre spørring: tabellen har FORCE
-- RLS med bare `tenant_isolasjon`, og konteksten settes først per rad
-- inne i løkka. Derfor velges restanseradene på prosessen alene — et
-- ENDELIG sett — og løkka spør døren, som ser radene sine når
-- konteksten står. Etter tømmingen er dørens svar 0 og raden
-- rapporteres ikke igjen; at det endelige settet re-låses i senere
-- sveip er bundet av grensen og sortert ETTER alt levende arbeid, så
-- det kan aldri fortrenge en bestilt eller frist-utløpt prosess.
--
-- Reaperen (069-kroppen, diff-endret i predikatet, ordreringen og to
-- steder i løkka). `CREATE OR REPLACE` beholder eier og grants
-- (timerrolleblokka i 057).
SET LOCAL ROLE disponit_m37_claimer;
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
