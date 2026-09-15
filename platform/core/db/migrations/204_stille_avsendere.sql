-- 204 — STILLE AVSENDERE: regelklassifisering av innkomne henvendelser.
--
-- SETT PÅ SKJERMEN 15/9: broen (203) gjorde 18 e-poster til henvendelser,
-- og alle 18 var Microsofts egne varsler («Ny app koblet til kontoen
-- din»). Hver av dem sto som ÅPEN og UKLASSIFISERT til et menneske
-- klikket seg gjennom dem — det er ikke «resten går av seg selv».
--
-- DOMMEN (eier 15/9): REGEL FØRST, MODELL SENERE. En avsender tenanten
-- selv har navngitt som stille — et domene eller en adresse — får en
-- klassifisering av seg selv, uten modellkall og uten kostnad. Modellen
-- kommer etterpå, for resten, gjennom policyporten slik manifestet alt
-- sier.
--
-- REGELEN EIES AV BASEN (104-formen): planrunden kaller ÉN definer og får
-- to tall tilbake. Ingen liste av domener i Python, ingen dømmekraft i
-- et transportledd. Listen er TENANTENS, satt gjennom en dør — akkurat
-- som purretrinnene.
--
-- TRE VERN:
--   * `kilde = 'regel'` er sin egen kilde. `klassifisering` låste kilden
--     til menneske/modell med en CHECK som binder `modell_digest` til
--     hver av dem. En regel har ingen digest og er ingen av delene;
--     å kalle den «menneske» ville løyet i evidenskjeden.
--   * ADRESSER LAGRES SOM HASH, aldri i klartekst — samme
--     `avsender_hash` som henvendelsen bærer, så en adresseregel
--     matcher uten at adressen finnes noe sted i regeltabellen. Domener
--     er ikke persondata og står som tekst.
--   * KUN ÅPNE, UKLASSIFISERTE henvendelser røres. En regel
--     omklassifiserer aldri det et menneske alt har avgjort.

-- TABELLENE EIES AV MIGRATOR (som alle M-17s tabeller); funksjonene av
-- `disponit_kundeservice_eier`. Derfor to rolleblokker: ALTER/CREATE
-- TABLE her, definererne under `SET LOCAL ROLE` lenger ned — nøyaktig
-- 102s form. Første utkast satte eierrollen først og fikk «must be
-- owner of table klassifisering».
SET LOCAL ROLE disponit_migrator;

-- ------------------------------------------------------------
-- 1. Klassifiseringens kilde: `regel` kommer til.
--
-- Kilde-CHECK-en i 102 er ANONYM. Den finnes deterministisk (relasjon
-- + contype + definisjonen) — varselenum-lærdommen: et oppslag som kan
-- treffe to rader er avhengig av spørreplanen. Én rad, ellers stopp.
-- ------------------------------------------------------------
DO $$
DECLARE v_navn TEXT; v_antall INT;
BEGIN
    SELECT count(*), min(conname) INTO v_antall, v_navn
      FROM pg_constraint
     WHERE conrelid = 'public.klassifisering'::regclass
       AND contype = 'c'
       AND pg_get_constraintdef(oid) LIKE '%kilde%'
       AND pg_get_constraintdef(oid) LIKE '%menneske%'
       AND pg_get_constraintdef(oid) NOT LIKE '%modell_digest%';
    IF v_antall <> 1 THEN
        RAISE EXCEPTION '204: fant % kilde-CHECK(er) på klassifisering,'
            ' ventet nøyaktig én — migrasjonen tar ingen sjanse', v_antall;
    END IF;
    EXECUTE format('ALTER TABLE public.klassifisering DROP CONSTRAINT %I',
                   v_navn);
END $$;
ALTER TABLE klassifisering
    ADD CONSTRAINT klassifisering_kilde_chk
    CHECK (kilde IN ('menneske', 'modell', 'regel'));

ALTER TABLE klassifisering
    DROP CONSTRAINT klassifisering_modell_krever_digest;
-- HVERT LEDD ER EKSPLISITT, og hver kilde nevnes med navn: en CHECK som
-- evaluerer til NULL PASSERER, så et ledd som glemte en kilde ville
-- sluppet den gjennom i stillhet.
ALTER TABLE klassifisering
    ADD CONSTRAINT klassifisering_modell_krever_digest
    CHECK ((kilde = 'modell' AND modell_digest IS NOT NULL
            AND modell_digest ~ '[^[:space:]]')
           OR (kilde = 'menneske' AND modell_digest IS NULL)
           OR (kilde = 'regel' AND modell_digest IS NULL));

-- ------------------------------------------------------------
-- 2. Regeltabellen — tenantens egen liste over stille avsendere.
-- ------------------------------------------------------------
CREATE TABLE kundeserviceregel (
    tenant        TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    regel_id      UUID NOT NULL DEFAULT gen_random_uuid(),
    art           TEXT NOT NULL CHECK (art IN ('domene', 'adresse')),
    -- domene: små bokstaver, uten «@». adresse: sha256-hex av den
    -- normaliserte adressen (samme form som henvendelse.avsender_hash).
    monster       TEXT NOT NULL CHECK (monster ~ '[^[:space:]]'),
    handlingstype TEXT NOT NULL DEFAULT 'til_info'
        CHECK (handlingstype IN ('til_info', 'nyhetsbrev', 'oppgave',
                                 'mistenkelig')),
    prioritet     TEXT NOT NULL DEFAULT 'lav'
        CHECK (prioritet IN ('kritisk', 'hoy', 'normal', 'lav')),
    tema          TEXT NOT NULL DEFAULT 'annet'
        CHECK (tema IN ('faktura', 'leveranse', 'teknisk', 'salg',
                        'klage', 'annet')),
    opprettet     TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av  TEXT NOT NULL,
    CONSTRAINT kundeserviceregel_pk PRIMARY KEY (tenant, regel_id),
    CONSTRAINT kundeserviceregel_unik UNIQUE (tenant, art, monster),
    CONSTRAINT kundeserviceregel_form CHECK (
        (art = 'domene'  AND monster = lower(monster) AND monster !~ '@')
     OR (art = 'adresse' AND monster ~ '^[0-9a-f]{64}$'))
);
ALTER TABLE kundeserviceregel ENABLE ROW LEVEL SECURITY;
ALTER TABLE kundeserviceregel FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON kundeserviceregel
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
-- Kryss-tenant-lesning for definereren under, samme form som
-- `m17_sveip_tenantliste` på henvendelse: BARE med tom kontekst.
CREATE POLICY m17_regel_tenantliste ON kundeserviceregel
    FOR SELECT TO disponit_kundeservice_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);
CREATE INDEX kundeserviceregel_tenant ON kundeserviceregel (tenant);
-- Definererne kjører som eieren: setteren sletter og setter inn, sveipen
-- leser. Ingen runtime-rolle rører tabellen direkte.
GRANT SELECT, INSERT, DELETE ON kundeserviceregel
    TO disponit_kundeservice_eier;

RESET ROLE;
SET LOCAL ROLE disponit_kundeservice_eier;

-- ------------------------------------------------------------
-- 3. Setteren: HELE settet i én transaksjon (m23_sett_purreplan-formen).
-- ------------------------------------------------------------
CREATE FUNCTION m17_sett_stilleregler(
    p_tenant TEXT, p_regler JSONB, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_i INT; v_r JSONB; v_antall INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_sett_stilleregler');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF jsonb_typeof(p_regler) <> 'array' THEN
        RAISE EXCEPTION 'm17_sett_stilleregler: reglene må være en liste'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_antall := jsonb_array_length(p_regler);
    IF v_antall > 200 THEN
        RAISE EXCEPTION 'm17_sett_stilleregler: maks 200 regler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Tom liste er lovlig: det er «ingen stille avsendere».
    DELETE FROM public.kundeserviceregel WHERE tenant = p_tenant;
    FOR v_i IN 0 .. v_antall - 1 LOOP
        v_r := p_regler -> v_i;
        INSERT INTO public.kundeserviceregel
            (tenant, art, monster, handlingstype, prioritet, tema,
             opprettet_av)
        VALUES (p_tenant, v_r ->> 'art',
                CASE WHEN v_r ->> 'art' = 'domene'
                     THEN lower(btrim(v_r ->> 'monster'))
                     ELSE v_r ->> 'monster' END,
                coalesce(v_r ->> 'handlingstype', 'til_info'),
                coalesce(v_r ->> 'prioritet', 'lav'),
                coalesce(v_r ->> 'tema', 'annet'),
                p_aktor);
    END LOOP;
    PERFORM public.m17_evidens(
        p_tenant, NULL, 'stilleregler.satt', p_aktor,
        jsonb_build_object('antall', v_antall));
    RETURN v_antall;
END $$;
REVOKE ALL ON FUNCTION m17_sett_stilleregler(TEXT, JSONB, TEXT) FROM PUBLIC;

CREATE FUNCTION m17_stillereglene(p_tenant TEXT)
RETURNS TABLE(regel_id UUID, art TEXT, monster TEXT, handlingstype TEXT,
              prioritet TEXT, tema TEXT, opprettet TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_stillereglene');
    RETURN QUERY
    SELECT r.regel_id, r.art, r.monster, r.handlingstype, r.prioritet,
           r.tema, r.opprettet
      FROM public.kundeserviceregel r
     WHERE r.tenant = p_tenant
     ORDER BY r.art, r.monster;
END $$;
REVOKE ALL ON FUNCTION m17_stillereglene(TEXT) FROM PUBLIC;

-- ------------------------------------------------------------
-- 4. Definereren planrunden kaller. KRYSS-TENANT, som m17_sveip_henvendelser.
-- ------------------------------------------------------------
CREATE FUNCTION m17_klassifiser_etter_regel(p_grense INT DEFAULT 500)
RETURNS TABLE(tenanter INT, klassifisert INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_tenanter TEXT[]; v_t TEXT; v_n INT; v_grense INT; v_h RECORD;
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm17_klassifiser_etter_regel: kryss-tenant-døra'
            ' nekter tenantkontekst' USING ERRCODE = 'insufficient_privilege';
    END IF;
    v_grense := greatest(least(coalesce(p_grense, 500), 5000), 1);
    tenanter := 0; klassifisert := 0;
    -- Bare tenanter som HAR regler — resten koster ingenting.
    SELECT array_agg(DISTINCT r.tenant ORDER BY r.tenant) INTO v_tenanter
      FROM public.kundeserviceregel r;
    FOREACH v_t IN ARRAY coalesce(v_tenanter, ARRAY[]::TEXT[]) LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        PERFORM set_config('disponit.aktor', 'agent:stilleregel', true);
        tenanter := tenanter + 1;
        v_n := 0;
        FOR v_h IN
            SELECT h.henvendelse_id, r.handlingstype, r.prioritet, r.tema
              FROM public.henvendelse h
              JOIN public.kundeserviceregel r
                ON r.tenant = h.tenant
               AND ((r.art = 'domene'
                     AND h.avsender_maske IS NOT NULL
                     AND lower(split_part(h.avsender_maske, '@', 2))
                         = r.monster)
                    OR (r.art = 'adresse'
                        AND h.avsender_hash = r.monster))
             WHERE h.tenant = v_t
               -- ÅPEN = ikke lukket, og ikke i unntakskøen: det et
               -- menneske har sendt til M-37 er alt under behandling,
               -- og en regel skal ikke dømme over hodet på den køen.
               AND h.lukket_ts IS NULL
               AND h.unntak_id IS NULL
               AND NOT EXISTS (SELECT 1 FROM public.klassifisering k
                                WHERE k.tenant = h.tenant
                                  AND k.henvendelse_id = h.henvendelse_id)
             ORDER BY h.mottatt, h.henvendelse_id
             LIMIT v_grense
        LOOP
            INSERT INTO public.klassifisering
                (tenant, henvendelse_id, prioritet, tema, handlingstype,
                 kilde, modell_digest, opprettet_av)
            VALUES (v_t, v_h.henvendelse_id, v_h.prioritet, v_h.tema,
                    v_h.handlingstype, 'regel', NULL, 'agent:stilleregel')
            ON CONFLICT (tenant, henvendelse_id) DO NOTHING;
            IF FOUND THEN
                v_n := v_n + 1;
                PERFORM public.m17_evidens(
                    v_t, v_h.henvendelse_id, 'henvendelse.klassifisert',
                    'agent:stilleregel',
                    jsonb_build_object('prioritet', v_h.prioritet,
                                       'tema', v_h.tema,
                                       'handlingstype', v_h.handlingstype,
                                       'kilde', 'regel'));
            END IF;
        END LOOP;
        klassifisert := klassifisert + v_n;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
    PERFORM set_config('disponit.aktor', '', true);
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m17_klassifiser_etter_regel(INT) FROM PUBLIC;

-- ------------------------------------------------------------
-- 5. Rettighetene. Setter/leser til runtime; sveipen til planarbeideren.
--    FUNKSJONSgrants overlever deployens nullstilling; det er derfor de
--    står her og ikke i `RETTIGHETER`.
-- ------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m17_sett_stilleregler(TEXT, JSONB, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_stillereglene(TEXT)'
            ' TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_plan_arbeider') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m17_klassifiser_etter_regel(INT) TO disponit_plan_arbeider';
    END IF;
END $$;

RESET ROLE;
