-- 187 — Fordringen peker på KUNDEN, ikke på en tekststreng.
--
-- EIERS MÅL: «en enkel plass der firmaene … legge til deres kunder …
-- Ikke gå gjennom hver modul og fylle». Registeret (183) ga plassen.
-- Denne migrasjonen er den første modulen som FAKTISK leser derfra —
-- uten den er registeret bare et ekstra sted å vedlikeholde.
--
-- I DAG skriver man kundereferansen som fritekst i fordringsflaten, og
-- e-postadressen i et eget felt, for en kunde som kanskje alt ligger i
-- registeret med nøyaktig den adressen. Etter denne velger man kunden.
--
-- `kunde_ref` ER BROEN, og den blir stående. Døra resolver `part_id` fra
-- den; finnes ingen part med den referansen, blir `part_id` NULL og alt
-- virker som før. Signaturen på `m23_registrer_fordring` er UENDRET —
-- ingen kaller brytes, verken modulens arm eller integrasjoner.
--
-- HVORFOR IKKE NOT NULL NÅ: fordi det ville brutt hver eneste kaller som
-- ikke har en part ennå. Kolonnen strammes når ingenting skriver NULL
-- lenger, og `kunde_ref` dør først når ingenting leser den. Det er
-- samme løfte som ble gitt i 183: «de gamle kolonnene dør først når
-- ingenting leser dem».

ALTER TABLE fordring ADD COLUMN part_id UUID;
ALTER TABLE fordring ADD CONSTRAINT fordring_part_fk
    FOREIGN KEY (tenant, part_id) REFERENCES part (tenant, part_id);
COMMENT ON COLUMN fordring.part_id IS
    'Kunden i partsregisteret (183). NULL = ingen part matcher'
    ' `kunde_ref` ennå. Broen er `kunde_ref` = `part.part_ref`.';
CREATE INDEX fordring_part ON fordring (tenant, part_id)
    WHERE part_id IS NOT NULL;

-- ------------------------------------------------------------------
-- BACKFILL: én part per distinkt `kunde_ref`, ALDRI en sammenslåing.
--
-- Referansene i drift identifiserer IKKE den samme kunden på tvers av
-- moduler: fordring har både firmanavn («Havnegata Eiendom AS») og
-- koder («kunde-nordbyen»), tilbud har ingen referanse i det hele tatt,
-- og kampanje bruker et tredje sett. En backfill som GJETTET at to av
-- dem er den samme kunden, ville slått sammen to virkelige kunder — en
-- feil som er vanskelig å oppdage og verre å rette.
--
-- Derfor: én part per referanse, og et menneske slår sammen senere (den
-- veien bygges når tilbud kobles på, som er der problemet først biter).
-- Partene fødes med `part_ref = kunde_ref` og navnet satt likt, fordi
-- det er alt fordring VET om kunden.
-- ------------------------------------------------------------------
DO $$
DECLARE t TEXT; r RECORD; v_id UUID; v_ny INT := 0; v_koblet INT := 0;
BEGIN
    FOR t IN SELECT DISTINCT tenant FROM public.fordring LOOP
        PERFORM set_config('disponit.tenant', t, true);
        FOR r IN SELECT DISTINCT kunde_ref FROM public.fordring
                  WHERE tenant = t AND part_id IS NULL LOOP
            SELECT part_id INTO v_id FROM public.part
             WHERE tenant = t AND part_ref = r.kunde_ref;
            IF v_id IS NULL THEN
                v_id := public.gen_random_uuid();
                INSERT INTO public.part
                    (tenant, part_id, part_ref, navn, opprettet_av)
                VALUES (t, v_id, r.kunde_ref, r.kunde_ref, 'migrasjon-187');
                v_ny := v_ny + 1;
            END IF;
            UPDATE public.fordring SET part_id = v_id
             WHERE tenant = t AND kunde_ref = r.kunde_ref
               AND part_id IS NULL;
            v_koblet := v_koblet + 1;
        END LOOP;
    END LOOP;
    PERFORM set_config('disponit.tenant', '', true);
    RAISE NOTICE 'm23-backfill: % nye parter, % referanser koblet',
                 v_ny, v_koblet;
END $$;

-- ------------------------------------------------------------------
-- DØRENE EIES AV `disponit_fordring_eier`, ikke av claimeren: M-23 har
-- sin EGEN modulrolle (104), og `m23_fordringene` kan bare erstattes av
-- den som eier den. Målt ved å kjøre — første utkast brukte claimeren,
-- som resten av partsregisteret, og fikk «must be owner of function».
-- ------------------------------------------------------------------
SET LOCAL ROLE disponit_fordring_eier;

-- REGISTRERINGEN RESOLVER PARTEN SELV, og signaturen er uendret. En
-- kaller som sender en referanse registeret kjenner, får koblingen
-- gratis; en som sender noe annet, får NULL og nøyaktig samme oppførsel
-- som før. Ingen kaller brytes — verken modulens arm eller en
-- integrasjon.
CREATE OR REPLACE FUNCTION m23_registrer_fordring(p_tenant text, p_fordring_id uuid, p_kunde_ref text, p_fakturanummer text, p_belop_ore bigint, p_utstedt date, p_forfall date, p_aktor text)
 RETURNS boolean
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE v_rader INT; v_gammel RECORD; v_nr TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
                                       'm23_registrer_fordring');
    IF coalesce(p_belop_ore, 0) <= 0 THEN
        RAISE EXCEPTION 'm23_registrer_fordring: beløpet må være positivt'
            ' — en negativ fordring er en kreditnota, og det er noe annet'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    v_nr := btrim(coalesce(p_fakturanummer, ''));
    IF v_nr = '' THEN
        RAISE EXCEPTION 'm23_registrer_fordring: fakturanummeret kan ikke'
            ' være tomt' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_kunde_ref IS NULL OR p_kunde_ref !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'm23_registrer_fordring: kundereferansen kan ikke'
            ' være tom — et krav ingen kan si hvem gjelder, kan ingen'
            ' kreve inn' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_forfall < p_utstedt THEN
        RAISE EXCEPTION 'm23_registrer_fordring: forfall før utstedelse'
            ' er ingen frist' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- 187: KUNDEN RESOLVES HER, i samme setning som raden fødes. Et
    -- eget etterkall ville vært en vei til å glemme, og en fordring uten
    -- kobling ser ut som en fordring med kobling i enhver liste.
    -- Finnes ingen part med referansen, blir den NULL og alt virker som
    -- før — `kunde_ref` er fortsatt broen, og signaturen er uendret.
    INSERT INTO public.fordring
        (tenant, fordring_id, kunde_ref, fakturanummer, belop_ore,
         utstedt, forfall, opprettet_av, part_id)
    VALUES (p_tenant, p_fordring_id, btrim(p_kunde_ref), v_nr,
            p_belop_ore, p_utstedt, p_forfall, p_aktor,
            (SELECT p.part_id FROM public.part p
              WHERE p.tenant = p_tenant
                AND p.part_ref = btrim(p_kunde_ref)))
        ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    IF v_rader = 0 THEN
        SELECT * INTO v_gammel FROM public.fordring
         WHERE tenant = p_tenant AND fordring_id = p_fordring_id;
        IF v_gammel IS NULL
           OR v_gammel.fakturanummer IS DISTINCT FROM v_nr
           OR v_gammel.belop_ore IS DISTINCT FROM p_belop_ore
           OR v_gammel.kunde_ref IS DISTINCT FROM btrim(p_kunde_ref)
           OR v_gammel.forfall IS DISTINCT FROM p_forfall THEN
            RAISE EXCEPTION 'm23_registrer_fordring: fakturanummeret er i'
                ' bruk, eller samme fordring_id med annet innhold —'
                ' materiell konflikt' USING ERRCODE = 'unique_violation';
        END IF;
        RETURN false;
    END IF;
    PERFORM public.m23_evidens(
        p_tenant, p_fordring_id, 'fordring.registrert', p_aktor,
        jsonb_build_object('forfall', p_forfall));
    RETURN true;
END $function$;

CREATE OR REPLACE FUNCTION public.m23_registrer_fordring(p_tenant text, p_fordring_id uuid, p_kunde_ref text, p_fakturanummer text, p_belop_ore bigint, p_utstedt date, p_forfall date, p_aktor text, p_mottaker_maske text, p_mottaker_kryptert bytea, p_mottaker_nonce bytea, p_mottaker_key_id text, p_mottaker_hash text)
 RETURNS boolean
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE v_ny BOOLEAN;
BEGIN
    v_ny := public.m23_registrer_fordring(
        p_tenant, p_fordring_id, p_kunde_ref, p_fakturanummer,
        p_belop_ore, p_utstedt, p_forfall, p_aktor);
    -- BARE NÅR FORDRINGEN ER NY: et gjentatt registreringskall (SP-2)
    -- skal ikke skrive over en mottaker som er rettet i mellomtiden.
    IF v_ny AND p_mottaker_maske IS NOT NULL THEN
        PERFORM public.m23_sett_mottaker(
            p_tenant, p_fordring_id, p_mottaker_maske,
            p_mottaker_kryptert, p_mottaker_nonce, p_mottaker_key_id,
            p_mottaker_hash, p_aktor);
    END IF;
    RETURN v_ny;
END $function$;

-- ...OG EN EGEN DØR FOR Å KNYTTE I ETTERTID: kunden legges ofte inn
-- ETTER at kravet er registrert. Uten denne måtte raden skrives om.
--
-- TO RETTELSER FØR DEN VAR RIKTIG:
--
-- 1. TENANTEN BINDES TIL KONTEKSTEN (CodeRabbit, kritisk). Uten
--    `krev_tenantkontekst` er dette en definer som tar tenanten som
--    PARAMETER og skriver. Og `fordring` har `m23_sveip_tenantliste`,
--    som åpner hele tabellen for eierrollen NÅR KONTEKSTEN ER TOM — så
--    en kaller som lot være å sette kontekst, kunne knyttet en annen
--    tenants krav. Samme feil som lesedørene i 183 hadde, og samme
--    dom: «definer-veiene binder tenanten til konteksten, aldri til
--    parameteret alene».
--
-- 2. REFERANSEN ER RADENS EGEN, ikke et parameter. Første utkast tok
--    `p_kunde_ref` og matchet på den — så et krav kunne bli pekt på en
--    kunde som ikke er kravets egen `kunde_ref`, og raden ville sagt to
--    forskjellige ting om hvem den gjelder. Døra leser referansen fra
--    raden; da er uoverensstemmelsen umulig å konstruere.
CREATE OR REPLACE FUNCTION m23_knytt_part(p_tenant TEXT, p_fordring_id UUID)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_knytt_part');
    UPDATE public.fordring f
       SET part_id = p.part_id
      FROM public.part p
     WHERE f.tenant = p_tenant AND f.fordring_id = p_fordring_id
       AND p.tenant = f.tenant AND p.part_ref = f.kunde_ref
       AND f.part_id IS DISTINCT FROM p.part_id;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    RETURN v_rader > 0;
END $$;
REVOKE ALL ON FUNCTION m23_knytt_part(TEXT, UUID) FROM PUBLIC;

DROP FUNCTION m23_fordringene(TEXT, INT);
CREATE FUNCTION m23_fordringene(p_tenant text, p_grense integer)
 RETURNS TABLE(fordring_id uuid, kunde_ref text, fakturanummer text, belop_ore bigint, betalt_ore bigint, rest_ore bigint, utstedt date, forfall date, dogn_over_forfall integer, status text, trinn integer, trinn_navn text, moden_for_trinn integer, apne_funn text[], mottaker_maske text, part_id uuid, part_navn text)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm23_fordringene');
    RETURN QUERY
    SELECT f.fordring_id, f.kunde_ref, f.fakturanummer, f.belop_ore,
           f.betalt_ore, f.belop_ore - f.betalt_ore, f.utstedt, f.forfall,
           (current_date - f.forfall)::int,
           f.status, f.trinn,
           (SELECT t.navn FROM public.purretrinn t
             WHERE t.tenant = f.tenant AND t.trinn_nr = f.trinn),
           -- MODEN FOR TRINN: det HØYESTE trinnet fordringens alder har
           -- passert. Er det høyere enn `trinn`, står den og venter på
           -- et menneske — og det er hele opplysningen flaten finnes
           -- for. Regnet i basen, i samme skann som raden.
           (SELECT max(t.trinn_nr) FROM public.purretrinn t
             WHERE t.tenant = f.tenant
               AND current_date - f.forfall >= t.dogn_etter_forfall),
           coalesce((SELECT array_agg(ff.funntype ORDER BY ff.funntype)
                       FROM public.fordringsfunn ff
                      WHERE ff.tenant = f.tenant
                        AND ff.fordring_id = f.fordring_id
                        AND ff.apen), ARRAY[]::TEXT[]),
           f.mottaker_maske,
           -- 187: KUNDEN FRA REGISTERET. `part_id` er NULL for rader
           -- ingen part matcher ennå; flaten viser da `kunde_ref` som
           -- før. Navnet leses gjennom fremmednøkkelen, ikke kopiert
           -- inn — et navn som er rettet ETT sted skal være rettet
           -- overalt, og det er hele poenget med registeret.
           f.part_id,
           (SELECT p.navn FROM public.part p
             WHERE p.tenant = f.tenant AND p.part_id = f.part_id)
      FROM public.fordring f
     WHERE f.tenant = p_tenant
     -- Åpne først, deretter mest forfalt. `fordring_id` som tiebreaker
     -- (100s bitmap-lærdom): to fordringer med samme forfall skal ikke
     -- bytte plass mellom to kall.
     ORDER BY (f.status <> 'apen'), f.forfall, f.fordring_id
     LIMIT greatest(least(coalesce(p_grense, 100), 1000), 1);
END $function$;

REVOKE ALL ON FUNCTION m23_fordringene(TEXT, INT) FROM PUBLIC;

RESET ROLE;

-- Fordringseieren skriver `part_id` gjennom døra og LESER `part` for
-- navnet. KOLONNEGRANT, aldri tabellgrant: modulen skal se hvem kunden
-- ER, aldri kontaktpunktene hennes — de har sin egen vei og sitt eget
-- scope.
GRANT SELECT (tenant, part_id, part_ref, navn) ON part
    TO disponit_fordring_eier;
GRANT UPDATE (part_id) ON fordring TO disponit_fordring_eier;

SET LOCAL ROLE disponit_fordring_eier;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_knytt_part(TEXT, UUID)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_fordringene(TEXT, INT)'
            ' TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
               WHERE rolname = 'disponit_fordringssveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m23_fordringene(TEXT, INT)'
            ' TO disponit_fordringssveip';
    END IF;
END $$;
RESET ROLE;
