-- 061: stillingsprofilen (#189) — kundens/adminens egen kravliste.
--
-- Eierkravet (24/8): kunden/admin definerer selv kravene og vektene
-- («Drift 3, Norsk 1, Skytjenester 2»), og profilen er det evalueringen
-- måler mot. To bærende valg:
--
-- * VERSJONERT OG APPEND-ONLY: en kjørt evaluering peker på profilen
--   SLIK DEN VAR (`stillingsprofil_ref` = profil@versjon). Redigering er
--   derfor aldri en UPDATE — det er en NY versjon. Vaktene under nekter
--   UPDATE/DELETE/TRUNCATE på begge tabellene i sin helhet; produsent-
--   mønsteret er #162s inndata (ref-en skal kunne slås opp, aldri bare
--   skrives).
-- * DØREN EIER SKRIVINGEN: `opprett_stillingsprofil_versjon` (definer,
--   domene_eier — 017-formen) validerer HELE kravsettet atomisk: navn
--   ikke-tomme og unike i settet, vekt 0–10 (eierens skala), 1–50 krav.
--   Runtime har EXECUTE på døren og SELECT på tabellene; INSERT-retten
--   bor hos eieren alene.

CREATE TABLE stillingsprofil (
    tenant TEXT NOT NULL,
    profil_id UUID NOT NULL,
    versjon INT NOT NULL CHECK (versjon >= 1),
    navn TEXT NOT NULL CHECK (length(btrim(navn)) BETWEEN 1 AND 200),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL,
    -- Idempotensnøkkelen (CodeRabbit major): opprettelsen er ikke
    -- naturlig idempotent — et tapt 201 og en retry ville laget en NY
    -- profil/versjon. Nøkkelen er per operasjon; gjenspill slår den opp
    -- og får det opprinnelige svaret.
    operasjonsnokkel TEXT NOT NULL CHECK (length(operasjonsnokkel)
                                          BETWEEN 8 AND 128),
    -- Innholdsbindingen (Cursor P1-2): gjenspill er bare lovlig for
    -- SAMME operasjon — nøkkelen alene ville latt et annet innhold få
    -- første operasjons svar stille. 056-signeringens mønster.
    innhold_hash TEXT NOT NULL,
    PRIMARY KEY (tenant, profil_id, versjon),
    CONSTRAINT stillingsprofil_idem UNIQUE (tenant, operasjonsnokkel)
);

CREATE TABLE stillingsprofil_krav (
    tenant TEXT NOT NULL,
    profil_id UUID NOT NULL,
    versjon INT NOT NULL,
    rekkefolge INT NOT NULL CHECK (rekkefolge >= 1),
    kravnavn TEXT NOT NULL CHECK (length(btrim(kravnavn)) BETWEEN 1 AND 120),
    vekt INT NOT NULL CHECK (vekt BETWEEN 0 AND 10),
    PRIMARY KEY (tenant, profil_id, versjon, rekkefolge),
    -- Samme krav kan ikke stå to ganger i samme versjon.
    UNIQUE (tenant, profil_id, versjon, kravnavn),
    FOREIGN KEY (tenant, profil_id, versjon)
        REFERENCES stillingsprofil (tenant, profil_id, versjon)
);

-- Append-only, hele familien: redigering er en ny versjon, aldri en
-- mutasjon — og en reaper som en dag skal finnes får sin egen dør.
CREATE OR REPLACE FUNCTION stillingsprofil_append_only()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'stillingsprofil: % avvist — versjonene er'
        ' append-only (redigering = ny versjon)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER stillingsprofil_vakt
    BEFORE UPDATE OR DELETE ON stillingsprofil
    FOR EACH ROW EXECUTE FUNCTION stillingsprofil_append_only();
CREATE TRIGGER stillingsprofil_krav_vakt
    BEFORE UPDATE OR DELETE ON stillingsprofil_krav
    FOR EACH ROW EXECUTE FUNCTION stillingsprofil_append_only();
CREATE TRIGGER stillingsprofil_ingen_truncate
    BEFORE TRUNCATE ON stillingsprofil
    FOR EACH STATEMENT EXECUTE FUNCTION stillingsprofil_append_only();
CREATE TRIGGER stillingsprofil_krav_ingen_truncate
    BEFORE TRUNCATE ON stillingsprofil_krav
    FOR EACH STATEMENT EXECUTE FUNCTION stillingsprofil_append_only();

ALTER TABLE stillingsprofil ENABLE ROW LEVEL SECURITY;
ALTER TABLE stillingsprofil FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON stillingsprofil
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
ALTER TABLE stillingsprofil_krav ENABLE ROW LEVEL SECURITY;
ALTER TABLE stillingsprofil_krav FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON stillingsprofil_krav
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

GRANT SELECT ON stillingsprofil, stillingsprofil_krav TO disponit;
GRANT SELECT, INSERT ON stillingsprofil, stillingsprofil_krav
    TO disponit_domene_eier;

SET LOCAL ROLE disponit_domene_eier;

-- Døren: én atomisk versjon. `p_profil_id` NULL = ny profil (fersk id,
-- versjon 1); ellers neste versjon av en EKSISTERENDE profil — og da må
-- profilen finnes hos tenanten (fail-closed: ellers kunne en gjettet id
-- «adopteres» med versjon 1 hos feil eier av historikken).
CREATE FUNCTION opprett_stillingsprofil_versjon(
    p_tenant TEXT, p_profil_id UUID, p_navn TEXT, p_opprettet_av TEXT,
    p_krav JSONB, p_operasjonsnokkel TEXT)
RETURNS TABLE (ut_profil_id UUID, ut_versjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_id UUID; v_versjon INT; v_n INT; r RECORD; v_i INT := 0;
    v_hash TEXT; v_lagret TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'opprett_stillingsprofil_versjon');
    -- Kanonisk innholdshash for idempotensbindingen: jsonb::text er
    -- nøkkelordnet og dedupet, så samme logiske kravsett hasher likt.
    v_hash := md5(coalesce(p_profil_id::text, '') || '·'
                  || coalesce(btrim(p_navn), '') || '·'
                  || coalesce(p_krav::text, ''));
    IF p_navn IS NULL OR length(btrim(p_navn)) NOT BETWEEN 1 AND 200 THEN
        RAISE EXCEPTION 'stillingsprofil: navnet må være 1–200 tegn'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- GJENSPILL FØRST: samme nøkkel + SAMME innhold = samme svar.
    -- Annet innhold på en brukt nøkkel er en KOLLISJON (Cursor P1-2) —
    -- aldri et stille replay av noe annet enn det kalleren ba om.
    SELECT s.profil_id, s.versjon, s.innhold_hash
      INTO ut_profil_id, ut_versjon, v_lagret
      FROM public.stillingsprofil s
     WHERE s.tenant = p_tenant
       AND s.operasjonsnokkel = p_operasjonsnokkel;
    IF FOUND THEN
        IF v_lagret IS DISTINCT FROM v_hash THEN
            RAISE EXCEPTION 'stillingsprofil: nøkkelen er brukt for'
                ' ANNET innhold'
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN NEXT;
        RETURN;
    END IF;
    IF p_krav IS NULL OR jsonb_typeof(p_krav) <> 'array' THEN
        RAISE EXCEPTION 'stillingsprofil: kravene må være en liste'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_n := jsonb_array_length(p_krav);
    IF v_n NOT BETWEEN 1 AND 50 THEN
        RAISE EXCEPTION 'stillingsprofil: 1–50 krav (fikk %)', v_n
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_profil_id IS NULL THEN
        v_id := gen_random_uuid();
        v_versjon := 1;
    ELSE
        -- VERSJONSTILDELINGEN SERIALISERES (CodeRabbit major): to
        -- samtidige redigeringer av samme profil skal få hver sin
        -- versjon, ikke kollidere på PK-en eller miste hverandres max.
        PERFORM pg_advisory_xact_lock(hashtextextended(
            'stillingsprofil:' || p_tenant || ':' || p_profil_id::text,
            0));
        SELECT coalesce(max(s.versjon), 0) INTO v_versjon
          FROM public.stillingsprofil s
         WHERE s.tenant = p_tenant AND s.profil_id = p_profil_id;
        IF v_versjon = 0 THEN
            RAISE EXCEPTION 'stillingsprofil: ukjent profil'
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        v_id := p_profil_id;
        v_versjon := v_versjon + 1;
    END IF;
    INSERT INTO public.stillingsprofil
        (tenant, profil_id, versjon, navn, opprettet_av,
         operasjonsnokkel, innhold_hash)
    VALUES (p_tenant, v_id, v_versjon, btrim(p_navn), p_opprettet_av,
            p_operasjonsnokkel, v_hash);
    FOR r IN SELECT elem FROM jsonb_array_elements(p_krav) AS t(elem)
    LOOP
        v_i := v_i + 1;
        IF jsonb_typeof(r.elem) <> 'object'
           OR jsonb_typeof(r.elem->'kravnavn') <> 'string'
           OR jsonb_typeof(r.elem->'vekt') <> 'number' THEN
            RAISE EXCEPTION 'stillingsprofil: krav % må ha kravnavn'
                ' (tekst) og vekt (tall)', v_i
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        -- HELTALL, ikke avrunding (CodeRabbit major): 2.7 skal avvises,
        -- aldri stille bli 3 — vekten er kundens eksplisitte valg.
        IF (r.elem->>'vekt')::numeric <> trunc((r.elem->>'vekt')::numeric)
        THEN
            RAISE EXCEPTION 'stillingsprofil: vekten i krav % må være et'
                ' heltall', v_i
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        INSERT INTO public.stillingsprofil_krav
            (tenant, profil_id, versjon, rekkefolge, kravnavn, vekt)
        VALUES (p_tenant, v_id, v_versjon, v_i,
                btrim(r.elem->>'kravnavn'),
                (r.elem->>'vekt')::numeric::int);
    END LOOP;
    ut_profil_id := v_id; ut_versjon := v_versjon;
    RETURN NEXT;
END $$;

REVOKE ALL ON FUNCTION opprett_stillingsprofil_versjon(
    TEXT, UUID, TEXT, TEXT, JSONB, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION opprett_stillingsprofil_versjon(
    TEXT, UUID, TEXT, TEXT, JSONB, TEXT) TO disponit;

RESET ROLE;
