-- 169 — M-26 (ARC B tilbud, PR 1): TILBUDSREGISTERET.
--
-- 108 sa «det finnes ingen tilbudsdør» — og det står: prisboka setter
-- ingen pris, og 108 rører vi ikke. Dette er registeret VED SIDEN AV:
-- et tilbud er en avskrift av boka på en dato, mot én kunde, med
-- standardklausulene bundet ved sin tekst-hash. Hver linje bærer
-- prisversjonen og listeprisen den siterte; en tilbudt pris kan være
-- lavere (rabatt), aldri høyere, enn boka. Det er dette som gjør
-- `priser_fra_prisbok` og `laste_klausuler_uendret` — bransjemalens
-- vilkår for `tilbud.generer` — til noe registeret kan MÅLE, ikke
-- påstå: lesedøra regner begge per tilbud, av radene selv.
--
-- KUNDENS ADRESSE LEVER BARE KRYPTERT (160-formen, AAD `m26:kunde`):
-- flaten og lista bærer masken; klarteksten går til eiermodulen i
-- claim-svaret (PR 4). Navnet er forretningsmotparten og står i klartekst
-- som leverandørnavnet i M-24.
--
-- INGEN SENDING HER. Et tilbud blir `godkjent` av et menneske; det er
-- utløseren (PR 3) og eiermodulen (PR 4) som gjør det til en e-post.

CREATE TABLE tilbud (
    tenant          TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    tilbud_id       UUID NOT NULL,
    kunde_navn      TEXT NOT NULL CHECK (kunde_navn ~ '[^[:space:]]'),
    kunde_ref       TEXT CHECK (kunde_ref IS NULL OR kunde_ref ~ '[^[:space:]]'),
    kunde_hash      TEXT NOT NULL
        CONSTRAINT tilbud_kundehash_form CHECK (kunde_hash ~ '^[0-9a-f]{64}$'),
    kunde_maske     TEXT NOT NULL CHECK (kunde_maske ~ '[^[:space:]]'),
    kunde_kryptert  BYTEA NOT NULL,
    nonce_kunde     BYTEA NOT NULL CHECK (octet_length(nonce_kunde) = 12),
    kunde_key_id    TEXT NOT NULL CHECK (kunde_key_id ~ '[^[:space:]]'),
    tilbudsdato     DATE NOT NULL,
    gyldig_til      DATE NOT NULL,
    valuta          TEXT NOT NULL DEFAULT 'NOK'
        CONSTRAINT tilbud_valuta_form CHECK (valuta ~ '^[A-Z]{3}$'),
    innledning      TEXT CHECK (innledning IS NULL OR innledning ~ '[^[:space:]]'),
    sum_ore         BIGINT NOT NULL CHECK (sum_ore >= 0),
    status          TEXT NOT NULL DEFAULT 'utkast'
        CONSTRAINT tilbud_status_lukket
        CHECK (status IN ('utkast', 'godkjent', 'forkastet')),
    avgjort_ts      TIMESTAMPTZ,
    avgjort_av      TEXT,
    opprettet       TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av    TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    CONSTRAINT tilbud_pk PRIMARY KEY (tenant, tilbud_id),
    CONSTRAINT tilbud_gyldig_etter_dato CHECK (gyldig_til >= tilbudsdato),
    CONSTRAINT tilbud_avgjorelse_helhet CHECK (
        (status = 'utkast' AND avgjort_ts IS NULL AND avgjort_av IS NULL)
        OR (status <> 'utkast' AND avgjort_ts IS NOT NULL
            AND avgjort_av IS NOT NULL AND avgjort_av ~ '[^[:space:]]'))
);
ALTER TABLE tilbud ENABLE ROW LEVEL SECURITY;
ALTER TABLE tilbud FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON tilbud
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
CREATE INDEX tilbud_status ON tilbud (tenant, status, tilbudsdato);

CREATE TABLE tilbudslinje (
    tenant          TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    tilbud_id       UUID NOT NULL,
    linje_nr        INT NOT NULL CHECK (linje_nr >= 1),
    produkt_id      UUID NOT NULL,
    produktkode     TEXT NOT NULL CHECK (produktkode ~ '[^[:space:]]'),
    produktnavn     TEXT NOT NULL CHECK (produktnavn ~ '[^[:space:]]'),
    enhet           TEXT NOT NULL CHECK (enhet ~ '[^[:space:]]'),
    antall          INT NOT NULL CHECK (antall >= 1),
    prisversjon     INT NOT NULL CHECK (prisversjon >= 1),
    listepris_ore   BIGINT NOT NULL CHECK (listepris_ore >= 0),
    enhetspris_ore  BIGINT NOT NULL CHECK (enhetspris_ore >= 0),
    linjesum_ore    BIGINT NOT NULL CHECK (linjesum_ore >= 0),
    CONSTRAINT tilbudslinje_pk PRIMARY KEY (tenant, tilbud_id, linje_nr),
    CONSTRAINT tilbudslinje_tilbud_fk FOREIGN KEY (tenant, tilbud_id)
        REFERENCES tilbud (tenant, tilbud_id),
    CONSTRAINT tilbudslinje_produkt_fk FOREIGN KEY (tenant, produkt_id)
        REFERENCES produkt (tenant, produkt_id),
    CONSTRAINT tilbudslinje_pris_fk FOREIGN KEY (tenant, produkt_id, prisversjon)
        REFERENCES pris (tenant, produkt_id, versjon),
    -- ALDRI OVER BOKA: rabatt er lov, påslag er ikke et tilbud fra boka.
    CONSTRAINT tilbudslinje_aldri_over_boka
        CHECK (enhetspris_ore <= listepris_ore),
    CONSTRAINT tilbudslinje_sum_gar_opp
        CHECK (linjesum_ore = antall::bigint * enhetspris_ore)
);
ALTER TABLE tilbudslinje ENABLE ROW LEVEL SECURITY;
ALTER TABLE tilbudslinje FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON tilbudslinje
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

CREATE TABLE tilbudsklausul (
    tenant      TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    tilbud_id   UUID NOT NULL,
    kode        TEXT NOT NULL CHECK (kode ~ '[^[:space:]]'),
    versjon     INT NOT NULL CHECK (versjon >= 1),
    tekst_hash  TEXT NOT NULL
        CONSTRAINT tilbudsklausul_hash_form CHECK (tekst_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT tilbudsklausul_pk PRIMARY KEY (tenant, tilbud_id, kode),
    CONSTRAINT tilbudsklausul_tilbud_fk FOREIGN KEY (tenant, tilbud_id)
        REFERENCES tilbud (tenant, tilbud_id),
    CONSTRAINT tilbudsklausul_klausul_fk FOREIGN KEY (tenant, kode, versjon)
        REFERENCES klausul (tenant, kode, versjon)
);
ALTER TABLE tilbudsklausul ENABLE ROW LEVEL SECURITY;
ALTER TABLE tilbudsklausul FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON tilbudsklausul
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

GRANT SELECT, INSERT, UPDATE ON tilbud TO disponit_prisbok_eier;
GRANT SELECT, INSERT ON tilbudslinje TO disponit_prisbok_eier;
GRANT SELECT, INSERT ON tilbudsklausul TO disponit_prisbok_eier;

-- Vaktene: et tilbud er et utspill mot en kunde — innholdet fryses når
-- det er skrevet, statusen går bare framover, ingenting slettes.
CREATE FUNCTION m26_tilbud_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
DECLARE v_aktor TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'tilbud: DELETE avvist — et tilbud som ble gitt,'
            ' ble gitt' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.tilbud_id IS DISTINCT FROM OLD.tilbud_id
       OR NEW.kunde_navn IS DISTINCT FROM OLD.kunde_navn
       OR NEW.kunde_ref IS DISTINCT FROM OLD.kunde_ref
       OR NEW.kunde_hash IS DISTINCT FROM OLD.kunde_hash
       OR NEW.kunde_maske IS DISTINCT FROM OLD.kunde_maske
       OR NEW.kunde_kryptert IS DISTINCT FROM OLD.kunde_kryptert
       OR NEW.nonce_kunde IS DISTINCT FROM OLD.nonce_kunde
       OR NEW.kunde_key_id IS DISTINCT FROM OLD.kunde_key_id
       OR NEW.tilbudsdato IS DISTINCT FROM OLD.tilbudsdato
       OR NEW.gyldig_til IS DISTINCT FROM OLD.gyldig_til
       OR NEW.valuta IS DISTINCT FROM OLD.valuta
       OR NEW.innledning IS DISTINCT FROM OLD.innledning
       OR NEW.sum_ore IS DISTINCT FROM OLD.sum_ore
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av THEN
        RAISE EXCEPTION 'tilbud: innholdet er frosset — et rettet tilbud er'
            ' et NYTT tilbud' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status
       AND NOT (OLD.status = 'utkast'
                AND NEW.status IN ('godkjent', 'forkastet')) THEN
        RAISE EXCEPTION 'tilbud: status går bare fra utkast til godkjent'
            ' eller forkastet — et avgjort tilbud er avgjort'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.status <> 'utkast' AND OLD.status = 'utkast' THEN
        v_aktor := nullif(current_setting('disponit.aktor', true), '');
        IF v_aktor IS NULL OR NEW.avgjort_av IS DISTINCT FROM v_aktor THEN
            RAISE EXCEPTION 'tilbud: avgjort_av (%) er ikke aktøren som'
                ' avgjør (%)', coalesce(NEW.avgjort_av, '<null>'),
                coalesce(v_aktor, '<ingen>')
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m26_tilbud_vakt() FROM PUBLIC;
CREATE TRIGGER m26_tilbud_vakt
    BEFORE UPDATE OR DELETE ON tilbud
    FOR EACH ROW EXECUTE FUNCTION m26_tilbud_vakt();

CREATE FUNCTION m26_tilbudsrad_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION '%: linjene og klausulene er append-only — et rettet'
        ' tilbud er et NYTT tilbud', TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END $$;
REVOKE ALL ON FUNCTION m26_tilbudsrad_vakt() FROM PUBLIC;
CREATE TRIGGER m26_tilbudslinje_vakt
    BEFORE UPDATE OR DELETE ON tilbudslinje
    FOR EACH ROW EXECUTE FUNCTION m26_tilbudsrad_vakt();
CREATE TRIGGER m26_tilbudsklausul_vakt
    BEFORE UPDATE OR DELETE ON tilbudsklausul
    FOR EACH ROW EXECUTE FUNCTION m26_tilbudsrad_vakt();

SET LOCAL ROLE disponit_prisbok_eier;

-- Skrivedøra: tilbudet FØDES av boka. Hver linje slår opp prisen på
-- tilbudsdatoen (m26_pris_paa_dato); et produkt uten pris den dagen er
-- en feil, ikke en gjetning. Enhetsprisen er listeprisen om ikke
-- kalleren gir en LAVERE (rabatt); høyere avvises. Standardklausulene
-- gyldige på datoen bindes ved sin hash. SP-2: samme tilbud_id én gang.
CREATE FUNCTION m26_lag_tilbud(
    p_tenant TEXT, p_tilbud_id UUID, p_kunde_navn TEXT, p_kunde_ref TEXT,
    p_kunde_hash TEXT, p_kunde_maske TEXT, p_kunde_kryptert BYTEA,
    p_nonce_kunde BYTEA, p_key_id TEXT, p_tilbudsdato DATE,
    p_gyldig_til DATE, p_innledning TEXT, p_linjer JSONB, p_aktor TEXT)
RETURNS TABLE(ny BOOLEAN, lagret_tilbud_id UUID, sum_ore BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_l JSONB; v_nr INT := 0; v_sum BIGINT := 0; v_p RECORD;
        v_pris RECORD; v_enhetspris BIGINT; v_antall INT; v_pid UUID;
        v_k RECORD; v_gammel BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_lag_tilbud');
    SELECT t.sum_ore INTO v_gammel FROM public.tilbud t
     WHERE t.tenant = p_tenant AND t.tilbud_id = p_tilbud_id;
    IF FOUND THEN
        ny := false; lagret_tilbud_id := p_tilbud_id; sum_ore := v_gammel;
        RETURN NEXT; RETURN;                       -- gjenspill: stille ja
    END IF;
    IF p_linjer IS NULL OR jsonb_typeof(p_linjer) <> 'array'
       OR jsonb_array_length(p_linjer) = 0 THEN
        RAISE EXCEPTION 'm26_lag_tilbud: et tilbud har minst én linje'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- FØRSTE PASS: linjene valideres og summen regnes — FØR raden fødes,
    -- for raden er frosset fra fødselen (vakten nekter å endre sum_ore).
    FOR v_l IN SELECT * FROM jsonb_array_elements(p_linjer) LOOP
        v_nr := v_nr + 1;
        BEGIN
            v_pid := (v_l->>'produkt_id')::uuid;
        EXCEPTION WHEN others THEN
            RAISE EXCEPTION 'm26_lag_tilbud: linje % mangler produkt_id', v_nr
                USING ERRCODE = 'invalid_parameter_value';
        END;
        v_antall := coalesce((v_l->>'antall')::int, 0);
        IF v_antall < 1 THEN
            RAISE EXCEPTION 'm26_lag_tilbud: linje % må ha antall >= 1', v_nr
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        SELECT pr.kode, pr.navn, pr.enhet, pr.aktiv INTO v_p
          FROM public.produkt pr
         WHERE pr.tenant = p_tenant AND pr.produkt_id = v_pid;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'm26_lag_tilbud: linje % peker på et produkt som'
                ' ikke finnes', v_nr USING ERRCODE = 'foreign_key_violation';
        END IF;
        IF NOT v_p.aktiv THEN
            RAISE EXCEPTION 'm26_lag_tilbud: linje % (%): produktet er'
                ' deaktivert', v_nr, v_p.kode
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        SELECT r.versjon, r.listepris_ore INTO v_pris
          FROM public.m26_pris_paa_dato(p_tenant, v_pid, p_tilbudsdato) r;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'm26_lag_tilbud: linje % (%): produktet har ingen'
                ' pris i boka på tilbudsdatoen — et tilbud siterer boka,'
                ' det gjetter ikke', v_nr, v_p.kode
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        v_enhetspris := coalesce((v_l->>'enhetspris_ore')::bigint,
                                 v_pris.listepris_ore);
        IF v_enhetspris < 0 OR v_enhetspris > v_pris.listepris_ore THEN
            RAISE EXCEPTION 'm26_lag_tilbud: linje % (%): enhetsprisen kan'
                ' ikke være negativ eller over boka (% øre)', v_nr, v_p.kode,
                v_pris.listepris_ore USING ERRCODE = 'invalid_parameter_value';
        END IF;
        v_sum := v_sum + v_antall::bigint * v_enhetspris;
    END LOOP;
    INSERT INTO public.tilbud
        (tenant, tilbud_id, kunde_navn, kunde_ref, kunde_hash, kunde_maske,
         kunde_kryptert, nonce_kunde, kunde_key_id, tilbudsdato, gyldig_til,
         innledning, sum_ore, opprettet_av)
    VALUES (p_tenant, p_tilbud_id, btrim(p_kunde_navn), nullif(btrim(p_kunde_ref), ''),
            p_kunde_hash, p_kunde_maske, p_kunde_kryptert, p_nonce_kunde,
            p_key_id, p_tilbudsdato, p_gyldig_til,
            nullif(btrim(coalesce(p_innledning, '')), ''), v_sum, p_aktor);
    -- ANDRE PASS: radene, med samme oppslag — boka svarer likt to ganger i
    -- samme transaksjon.
    v_nr := 0;
    FOR v_l IN SELECT * FROM jsonb_array_elements(p_linjer) LOOP
        v_nr := v_nr + 1;
        v_pid := (v_l->>'produkt_id')::uuid;
        v_antall := (v_l->>'antall')::int;
        SELECT pr.kode, pr.navn, pr.enhet INTO v_p
          FROM public.produkt pr
         WHERE pr.tenant = p_tenant AND pr.produkt_id = v_pid;
        SELECT r.versjon, r.listepris_ore INTO v_pris
          FROM public.m26_pris_paa_dato(p_tenant, v_pid, p_tilbudsdato) r;
        v_enhetspris := coalesce((v_l->>'enhetspris_ore')::bigint,
                                 v_pris.listepris_ore);
        INSERT INTO public.tilbudslinje
            (tenant, tilbud_id, linje_nr, produkt_id, produktkode, produktnavn,
             enhet, antall, prisversjon, listepris_ore, enhetspris_ore,
             linjesum_ore)
        VALUES (p_tenant, p_tilbud_id, v_nr, v_pid, v_p.kode, v_p.navn,
                v_p.enhet, v_antall, v_pris.versjon, v_pris.listepris_ore,
                v_enhetspris, v_antall::bigint * v_enhetspris);
    END LOOP;
    -- Standardklausulene som gjelder på tilbudsdatoen: nyeste versjon per
    -- kode, bundet ved hashen.
    FOR v_k IN
        SELECT DISTINCT ON (k.kode) k.kode, k.versjon, k.tekst_hash
          FROM public.klausul k
         WHERE k.tenant = p_tenant AND k.standard
           AND k.gyldig_fra <= p_tilbudsdato
           AND (k.gyldig_til IS NULL OR k.gyldig_til >= p_tilbudsdato)
         ORDER BY k.kode, k.versjon DESC
    LOOP
        INSERT INTO public.tilbudsklausul (tenant, tilbud_id, kode, versjon,
                                           tekst_hash)
        VALUES (p_tenant, p_tilbud_id, v_k.kode, v_k.versjon, v_k.tekst_hash);
    END LOOP;
    PERFORM public.m26_evidens(
        p_tenant, p_tilbud_id, 'tilbud.opprettet', p_aktor,
        jsonb_build_object('linjer', v_nr, 'sum_ore', v_sum,
                           'tilbudsdato', p_tilbudsdato,
                           'kunde_maske', p_kunde_maske));
    ny := true; lagret_tilbud_id := p_tilbud_id; sum_ore := v_sum;
    RETURN NEXT;
END $$;
REVOKE ALL ON FUNCTION m26_lag_tilbud(TEXT, UUID, TEXT, TEXT, TEXT, TEXT,
    BYTEA, BYTEA, TEXT, DATE, DATE, TEXT, JSONB, TEXT) FROM PUBLIC;

CREATE FUNCTION m26_avgjor_tilbud(
    p_tenant TEXT, p_tilbud_id UUID, p_status TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_avgjor_tilbud');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_status IS NULL OR p_status NOT IN ('godkjent', 'forkastet') THEN
        RAISE EXCEPTION 'm26_avgjor_tilbud: dommen er godkjent eller'
            ' forkastet — «sendt» er aldri en dom, det er det som skjer'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT t.status INTO v_status FROM public.tilbud t
     WHERE t.tenant = p_tenant AND t.tilbud_id = p_tilbud_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm26_avgjor_tilbud: tilbudet finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_status = p_status THEN
        RETURN false;                              -- stille ja
    END IF;
    IF v_status <> 'utkast' THEN
        RAISE EXCEPTION 'm26_avgjor_tilbud: tilbudet er alt % — et avgjort'
            ' tilbud er avgjort', v_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.tilbud
       SET status = p_status, avgjort_ts = now(), avgjort_av = p_aktor
     WHERE tenant = p_tenant AND tilbud_id = p_tilbud_id;
    PERFORM public.m26_evidens(
        p_tenant, p_tilbud_id, 'tilbud.' || p_status, p_aktor,
        jsonb_build_object('status', p_status));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m26_avgjor_tilbud(TEXT, UUID, TEXT, TEXT) FROM PUBLIC;

-- Lesedøra: lista, med de to faktaene policyen bygger på — REGNET AV
-- RADENE. `priser_fra_boka`: hver linje er innenfor tenantens
-- rabattgrense mot listeprisen den siterte (m26_innenfor_rabatt på
-- tilbudsdatoen). `klausuler_uendret`: hver bundet klausul har samme hash
-- som kodens NYESTE versjon i dag.
CREATE FUNCTION m26_tilbudene(p_tenant TEXT, p_grense INT)
RETURNS TABLE(tilbud_id UUID, kunde_navn TEXT, kunde_ref TEXT,
              kunde_maske TEXT, tilbudsdato DATE, gyldig_til DATE,
              valuta TEXT, sum_ore BIGINT, status TEXT, antall_linjer INT,
              priser_fra_boka BOOLEAN, klausuler_uendret BOOLEAN,
              avgjort_ts TIMESTAMPTZ, avgjort_av TEXT,
              opprettet TIMESTAMPTZ, opprettet_av TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_tilbudene');
    RETURN QUERY
    SELECT t.tilbud_id, t.kunde_navn, t.kunde_ref, t.kunde_maske,
           t.tilbudsdato, t.gyldig_til, t.valuta, t.sum_ore, t.status,
           (SELECT count(*)::int FROM public.tilbudslinje l
             WHERE l.tenant = t.tenant AND l.tilbud_id = t.tilbud_id),
           NOT EXISTS (
               SELECT 1 FROM public.tilbudslinje l
                WHERE l.tenant = t.tenant AND l.tilbud_id = t.tilbud_id
                  AND coalesce(public.m26_innenfor_rabatt(
                          t.tenant, l.produkt_id, t.tilbudsdato,
                          l.enhetspris_ore), l.enhetspris_ore = l.listepris_ore)
                      IS NOT TRUE),
           NOT EXISTS (
               SELECT 1 FROM public.tilbudsklausul tk
                WHERE tk.tenant = t.tenant AND tk.tilbud_id = t.tilbud_id
                  AND tk.tekst_hash IS DISTINCT FROM (
                      SELECT k.tekst_hash FROM public.klausul k
                       WHERE k.tenant = tk.tenant AND k.kode = tk.kode
                       ORDER BY k.versjon DESC LIMIT 1)),
           t.avgjort_ts, t.avgjort_av, t.opprettet, t.opprettet_av
      FROM public.tilbud t
     WHERE t.tenant = p_tenant
     ORDER BY t.opprettet DESC, t.tilbud_id
     LIMIT greatest(least(coalesce(p_grense, 200), 1000), 1);
END $$;
REVOKE ALL ON FUNCTION m26_tilbudene(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m26_tilbudet(p_tenant TEXT, p_tilbud_id UUID)
RETURNS TABLE(innledning TEXT, linjer JSONB, klausuler JSONB)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm26_tilbudet');
    RETURN QUERY
    SELECT t.innledning,
           coalesce((SELECT jsonb_agg(jsonb_build_object(
                        'linje_nr', l.linje_nr, 'produkt_id', l.produkt_id,
                        'produktkode', l.produktkode,
                        'produktnavn', l.produktnavn, 'enhet', l.enhet,
                        'antall', l.antall, 'prisversjon', l.prisversjon,
                        'listepris_ore', l.listepris_ore,
                        'enhetspris_ore', l.enhetspris_ore,
                        'linjesum_ore', l.linjesum_ore)
                        ORDER BY l.linje_nr)
                       FROM public.tilbudslinje l
                      WHERE l.tenant = t.tenant AND l.tilbud_id = t.tilbud_id),
                    '[]'::jsonb),
           coalesce((SELECT jsonb_agg(jsonb_build_object(
                        'kode', tk.kode, 'versjon', tk.versjon,
                        'tekst_hash', tk.tekst_hash,
                        'tittel', k.tittel, 'tekst', k.tekst)
                        ORDER BY tk.kode)
                       FROM public.tilbudsklausul tk
                       JOIN public.klausul k
                         ON k.tenant = tk.tenant AND k.kode = tk.kode
                        AND k.versjon = tk.versjon
                      WHERE tk.tenant = t.tenant AND tk.tilbud_id = t.tilbud_id),
                    '[]'::jsonb)
      FROM public.tilbud t
     WHERE t.tenant = p_tenant AND t.tilbud_id = p_tilbud_id;
END $$;
REVOKE ALL ON FUNCTION m26_tilbudet(TEXT, UUID) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_lag_tilbud(TEXT, UUID, TEXT,'
            ' TEXT, TEXT, TEXT, BYTEA, BYTEA, TEXT, DATE, DATE, TEXT, JSONB,'
            ' TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_avgjor_tilbud(TEXT, UUID,'
            ' TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_tilbudene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m26_tilbudet(TEXT, UUID)'
            ' TO disponit';
    END IF;
END $$;

RESET ROLE;
