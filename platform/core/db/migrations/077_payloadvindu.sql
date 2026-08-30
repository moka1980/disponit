-- 077: payloadvinduet utledes av ankeret (#164 — K2-dom B, eiers
-- ratifisering i #153 14:33; «Ingen A-arm — §10 hadde rett»)
--
-- Lagervaktens INSERT-gren leste bare `slettet_ts`: en forsinket
-- skriver kunne sette inn kandidatpayload ETTER at fristen var passert
-- (før den batchede reaperen rakk prosessen) og ETTER en bestilt
-- tidligsletting — ny persondata etter det lovede slettetidspunktet.
-- Rotklassen er den samme som #163: en vakt som er en HÅNDTELT LISTE
-- over tilstander inviterer til én runde per tilstand; `lukket_ts`
-- ville vært den femte armen.
--
-- Eiers B: INSERT-forutsetningene UTLEDES av ankerets tilstandsmaskin —
-- ÉN kilde. `m57_payloadvindu` er den kilden: prosessen tar imot
-- payload hvis og bare hvis den er ureapet, uten bestilt sletting, og
-- innenfor kundens frist (samme grense som leseveien og reaperen alt
-- dømmer etter — lukking alene er en FRISTSTART, aldri en skrivesperre,
-- nøyaktig som eiers presisering i #153 sier).

CREATE FUNCTION m57_payloadvindu(p rekrutteringsprosess)
RETURNS BOOLEAN LANGUAGE sql STABLE
SET search_path = pg_catalog AS $$
    SELECT p.slettet_ts IS NULL
       AND p.slett_bestilt_ts IS NULL
       AND pg_catalog.now() < coalesce(p.lukket_ts, p.opprettet)
                              + p.slettefrist_dogn * interval '1 day'
$$;
REVOKE ALL ON FUNCTION m57_payloadvindu(rekrutteringsprosess) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION m57_payloadvindu(rekrutteringsprosess)
    TO disponit_m37_claimer;

-- ------------------------------------------------------------
-- Lagervakten (057-kroppen SPEILET; INSERT-grenens forutsetning er nå
-- utledet — resten står ordrett).
CREATE OR REPLACE FUNCTION m57_kandidatlager_vakt()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE nj jsonb; oj jsonb; kol TEXT; v_payload jsonb;
        v_prosess public.rekrutteringsprosess;
BEGIN
    -- Port 18, INSERT-siden: forutsetningene UTLEDES av ankerets
    -- tilstandsmaskin (#164, eiers B) — `m57_payloadvindu` er den ene
    -- kilden, og denne vakten teller aldri tilstander opp for hånd
    -- igjen. Lesningen LÅSER som før (Codex P1): `FOR SHARE`
    -- serialiserer mot reaperens `FOR UPDATE`, så vinduet dømmes på
    -- radens NYE versjon. En forelder vakten ikke SER (RLS/kontekst) er
    -- fortsatt en avvisning — vakten kan bare svare på det den så.
    IF TG_OP = 'INSERT' THEN
        SELECT p.* INTO v_prosess
          FROM public.rekrutteringsprosess p
         WHERE p.tenant = NEW.tenant AND p.prosess_id = NEW.prosess_id
         FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION '%: prosessen er ikke synlig for vakten —'
                ' kandidatpayload skrives bare under en prosess vakten'
                ' kan lese og låse (klarsignalet §5, port 18)',
                TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NOT public.m57_payloadvindu(v_prosess) THEN
            -- Detaljen under er DIAGNOSE, aldri en ny forutsetning:
            -- porten er vinduet alene.
            IF v_prosess.slettet_ts IS NOT NULL THEN
                RAISE EXCEPTION '%: prosessen er reapet — payload skrives'
                    ' ikke tilbake til en slettet prosess (klarsignalet'
                    ' §5)', TG_TABLE_NAME
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            RAISE EXCEPTION '%: prosessen er utenfor payloadvinduet'
                ' (bestilt sletting eller passert frist) — forutsetningen'
                ' utledes av ankerets tilstandsmaskin (#164, klarsignalet'
                ' §5)', TG_TABLE_NAME
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- En RAD fødes LEVENDE (Cursor P1, 057 ordrett): `slettet_ts`
        -- settes av reap-overgangen, aldri av en fødsel — en gravstein
        -- på en umerket prosess ville brent hele oppdraget permanent.
        IF NEW.slettet_ts IS NOT NULL THEN
            RAISE EXCEPTION '%: en kandidatrad fødes LEVENDE — reap-merket'
                ' settes bare av reap-overgangen (klarsignalet §5)',
                TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
        END IF;
        -- `innhold_sha256` UTLEDES her, den mottas ikke (Codex P2, 057
        -- ordrett): hashen er den eneste evidensen som består etter
        -- reaping, målt på de LAGREDE bytene, aldri på påstanden om dem.
        nj := to_jsonb(NEW);
        v_payload := '{}'::jsonb;
        FOREACH kol IN ARRAY TG_ARGV LOOP
            v_payload := v_payload || jsonb_build_object(kol, nj->kol);
        END LOOP;
        NEW.innhold_sha256 :=
            encode(sha256(convert_to(v_payload::text, 'UTF8')), 'hex');
        -- `opprettet` UTLEDES av samme grunn (Codex P2, 057 ordrett):
        -- basens klokke, ikke skriverens.
        NEW.opprettet := pg_catalog.now();
        RETURN NEW;
    END IF;
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION '%: % avvist — kandidatrader reapes (payload til'
            ' NULL), de slettes aldri som rader', TG_TABLE_NAME, TG_OP
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

-- ------------------------------------------------------------
-- Ankerdøren dømmer av SAMME kilde (075-kroppen, forutsetningen byttet).
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION opprett_kandidat(
    p_tenant TEXT, p_prosess_id UUID, p_kandidat_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_prosess public.rekrutteringsprosess;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'opprett_kandidat');
    SELECT p.* INTO v_prosess
      FROM public.rekrutteringsprosess p
     WHERE p.tenant = p_tenant AND p.prosess_id = p_prosess_id
     FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'opprett_kandidat: prosessen finnes ikke —'
            ' kandidater fødes bare under en levende prosess'
            ' (klarsignalet §5, port 18)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT public.m57_payloadvindu(v_prosess) THEN
        IF v_prosess.slettet_ts IS NOT NULL THEN
            RAISE EXCEPTION 'opprett_kandidat: prosessen er reapet —'
                ' ingen ny kandidat på en slettet prosess (klarsignalet'
                ' §5)' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RAISE EXCEPTION 'opprett_kandidat: prosessen er utenfor'
            ' payloadvinduet (bestilt sletting eller passert frist) —'
            ' forutsetningen utledes av ankerets tilstandsmaskin (#164)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    INSERT INTO public.kandidat (tenant, prosess_id, kandidat_id)
         VALUES (p_tenant, p_prosess_id, p_kandidat_id)
    ON CONFLICT (tenant, prosess_id, kandidat_id) DO NOTHING;
END $$;
RESET ROLE;
