-- 194: EN BEDRIFT KAN TA MED SEG DE ANSATTE.
--
-- MÅLT: INGEN dør skriver `brukermedlemskap`. Tabellen fylles bare av en
-- operatør med direkte basetilgang — runtime har kun SELECT. Et firma som
-- registrerte seg selv (192) blir derfor en ÉNPERSONSBEDRIFT for alltid:
-- hun er admin, og kan ikke slippe inn en eneste kollega.
--
-- Det gjør selvbetjeningen halv. Eiers mål var «100% automatisert og
-- selvbetjent når policyen er satt»; et firma der bare grunnleggeren kommer
-- inn, er ikke et firma som kan bruke systemet.
--
-- INVITASJON VED ENGANGSLENKE, IKKE VED E-POSTADRESSE
-- ---------------------------------------------------
-- Den nærliggende formen — «skriv inn kollegaens e-post» — har to problemer
-- huset allerede har tatt stilling til:
--
--   1. `brukeridentitet` ER `(issuer, sub)`. E-posten finnes bare i
--      OIDC-profilen, som en LUKKET DTO uten autoritetsverdi (v5 §5). Å
--      lagre en adresse her ville vært å innføre et nytt persondatalager i
--      en tabell som ikke har ett i dag.
--   2. Skulle vi i stedet lagre pseudonymet, måtte innløsningen slå det opp
--      på TVERS av tenanter ved innlogging — og `tenant_pseudonym` (183) er
--      tenant-skopet nettopp for å gjøre det umulig.
--
-- Et engangstoken unngår begge: ingen personopplysning lagres, og
-- invitasjonen binder seg til den som FAKTISK logger inn og åpner lenken.
-- Admin sender lenken slik hun vil — e-post, chat, på papir.
--
-- KUN HASHEN LAGRES, som i `oidc_logintransaksjon` (010) og
-- `brukersesjon`: den som får tak i basen skal ikke kunne bruke en
-- invitasjon, bare se at den finnes.
-- ============================================================

CREATE TABLE IF NOT EXISTS firmainvitasjon (
    token_hash   TEXT PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    tenant       TEXT NOT NULL,
    -- Rollene den inviterte får. Samme lukkede mønster som
    -- `brukermedlemskap.roller`, og CHECK-en under gjør at en invitasjon
    -- aldri kan bære plattformrollen inn i et kundefirma.
    -- `cardinality`, IKKE `array_length` (CodeRabbit). For et tomt array
    -- gir `array_length(roller, 1)` NULL, og `NULL >= 1` er NULL — som en
    -- CHECK SLIPPER GJENNOM. Et invitasjon med tom rolleliste ville gitt
    -- kollegaen et medlemskap uten scopes: hun kommer inn og ser ingenting,
    -- uten at noe sier hvorfor. `cardinality('{}')` er 0, og 0 >= 1 er
    -- FALSE. Samme feilklasse som `NULL !~ mønster` i 182.
    roller       TEXT[] NOT NULL
                 CONSTRAINT firmainvitasjon_roller_ikke_tom
                 CHECK (cardinality(roller) >= 1),
    opprettet_av TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    opprettet    TIMESTAMPTZ NOT NULL DEFAULT now(),
    utloper      TIMESTAMPTZ NOT NULL,
    -- Engangsbruk: satt ved innløsning, og aldri nullstilt.
    brukt_ts     TIMESTAMPTZ,
    brukt_av     TEXT REFERENCES brukeridentitet (bruker_id),

    -- EN INVITASJON KAN ALDRI GI PLATTFORMFULLMAKT. 192 la CHECK-en på
    -- `brukermedlemskap`; uten den samme her ville invitasjonen vært en
    -- omvei rundt den — raden ville blitt avvist ved innløsning, men først
    -- etter at en admin trodde hun hadde gitt den bort.
    CONSTRAINT firmainvitasjon_ingen_plattformrolle
        CHECK (NOT ('plattformeier' = ANY(roller))),
    CONSTRAINT firmainvitasjon_brukt_helhet
        CHECK ((brukt_ts IS NULL AND brukt_av IS NULL)
            OR (brukt_ts IS NOT NULL AND brukt_av IS NOT NULL))
);

ALTER TABLE firmainvitasjon ENABLE ROW LEVEL SECURITY;
ALTER TABLE firmainvitasjon FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON firmainvitasjon
    USING (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

CREATE INDEX firmainvitasjon_apne ON firmainvitasjon (tenant, utloper)
    WHERE brukt_ts IS NULL;

-- ============================================================
-- Å OPPRETTE EN INVITASJON er en handling INNE i firmaet: admin står i sin
-- egen kontekst, og `krev_tenantkontekst` holder som ellers.
-- ============================================================
CREATE OR REPLACE FUNCTION invitasjon_opprett(p_tenant TEXT,
                                              p_token_hash TEXT,
                                              p_roller TEXT[],
                                              p_aktor TEXT,
                                              p_timer INT DEFAULT 168)
RETURNS TIMESTAMPTZ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_utloper TIMESTAMPTZ; v_ukjent TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'invitasjon_opprett');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'invitasjon_opprett: en invitasjon har en avsender'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_timer IS NULL OR p_timer NOT BETWEEN 1 AND 720 THEN
        RAISE EXCEPTION 'invitasjon_opprett: gyldighet må være 1-720 timer'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- ROLLENE MÅ VÆRE KJENTE. `scopes_for_roller` gir en ukjent rolle INGEN
    -- scopes (default-deny), så en skrivefeil ville gitt kollegaen en konto
    -- uten fullmakter — og ingen feilmelding før hun satt der og lurte.
    SELECT r INTO v_ukjent FROM unnest(p_roller) AS r
     WHERE NOT EXISTS (SELECT 1 FROM public.rolle_scope rs WHERE rs.rolle = r)
     LIMIT 1;
    IF v_ukjent IS NOT NULL THEN
        RAISE EXCEPTION 'invitasjon_opprett: ukjent rolle %', v_ukjent
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    v_utloper := now() + make_interval(hours => p_timer);
    INSERT INTO public.firmainvitasjon (token_hash, tenant, roller,
                                        opprettet_av, utloper)
         VALUES (p_token_hash, p_tenant, p_roller, p_aktor, v_utloper);
    RETURN v_utloper;
END $$;
REVOKE ALL ON FUNCTION invitasjon_opprett(TEXT, TEXT, TEXT[], TEXT, INT)
    FROM PUBLIC;


-- ============================================================
-- Å INNLØSE EN ER DET MOTSATTE: den inviterte har INGEN medlemskap i
-- firmaet ennå, så hun står ikke i dets kontekst — hun står i
-- `_registrering` (192) eller i sitt eget, andre firma.
--
-- Døra setter derfor konteksten SELV, som `firma_selvregistrer` gjør, og
-- legger den tilbake etterpå. Tenanten kommer fra lenken; den er ingen
-- hemmelighet (den inviterte skal jo inn dit), og den autoriserer
-- ingenting alene. AUTORITETEN ER TOKENET.
--
-- KRAVET ER ATOMISK. `UPDATE ... WHERE brukt_ts IS NULL RETURNING` gjør at
-- to samtidige innløsninger av samme lenke ikke begge kan lykkes: den andre
-- finner ingen rad. En SELECT-så-UPDATE ville gitt to medlemskap på én
-- invitasjon — samme kappløp partsregisteret måtte rettes for (183).
-- ============================================================
CREATE OR REPLACE FUNCTION invitasjon_innloes(p_tenant TEXT,
                                              p_token_hash TEXT,
                                              p_bruker_id TEXT)
RETURNS TEXT[] LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kontekst TEXT; v_roller TEXT[];
BEGIN
    v_kontekst := coalesce(current_setting('disponit.tenant', true), '');
    IF p_bruker_id IS NULL OR btrim(p_bruker_id) = '' THEN
        RAISE EXCEPTION 'invitasjon_innloes: mangler identitet'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.brukeridentitet b
                    WHERE b.bruker_id = p_bruker_id) THEN
        RAISE EXCEPTION 'invitasjon_innloes: ukjent identitet'
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    PERFORM set_config('disponit.tenant', p_tenant, true);
    UPDATE public.firmainvitasjon
       SET brukt_ts = now(), brukt_av = p_bruker_id
     WHERE firmainvitasjon.token_hash = p_token_hash
       AND firmainvitasjon.tenant = p_tenant
       AND firmainvitasjon.brukt_ts IS NULL
       AND firmainvitasjon.utloper > now()
    RETURNING roller INTO v_roller;
    IF v_roller IS NULL THEN
        PERFORM set_config('disponit.tenant', v_kontekst, true);
        -- ÉN FEILKODE FOR ALLE FIRE TILFELLENE (finnes ikke, feil tenant,
        -- brukt, utløpt). Å skille dem ville latt noen prøve seg fram og
        -- lære hvilke tokener som finnes.
        RAISE EXCEPTION 'invitasjon_innloes: ugyldig eller brukt invitasjon'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    -- Er hun alt medlem, er invitasjonen brukt opp uten skade: ON CONFLICT
    -- lar rollene stå som de er. Å overskrive dem ville latt en gammel
    -- lenke DEGRADERE en kollega som i mellomtiden ble admin.
    INSERT INTO public.brukermedlemskap (tenant, bruker_id, roller, aktiv)
         VALUES (p_tenant, p_bruker_id, v_roller, true)
    ON CONFLICT (tenant, bruker_id) DO NOTHING;

    -- Registrantraden ryddes, av samme grunn som i `firma_selvregistrer`:
    -- med den stående ville hun hatt to medlemskap og fått
    -- `firma_ikke_valgt` ved neste innlogging.
    PERFORM set_config('disponit.tenant', '_registrering', true);
    DELETE FROM public.brukermedlemskap
     WHERE tenant = '_registrering' AND bruker_id = p_bruker_id;

    PERFORM set_config('disponit.tenant', v_kontekst, true);
    RETURN v_roller;
END $$;
REVOKE ALL ON FUNCTION invitasjon_innloes(TEXT, TEXT, TEXT) FROM PUBLIC;


CREATE OR REPLACE FUNCTION invitasjon_liste(p_tenant TEXT)
RETURNS TABLE (token_hash TEXT, roller TEXT[], opprettet_av TEXT,
               opprettet TIMESTAMPTZ, utloper TIMESTAMPTZ,
               brukt_ts TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'invitasjon_liste');
    RETURN QUERY
    SELECT i.token_hash, i.roller, i.opprettet_av, i.opprettet, i.utloper,
           i.brukt_ts
      FROM public.firmainvitasjon i
     WHERE i.tenant = p_tenant
     ORDER BY i.opprettet DESC
     LIMIT 100;
END $$;
REVOKE ALL ON FUNCTION invitasjon_liste(TEXT) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        GRANT EXECUTE ON FUNCTION
            invitasjon_opprett(TEXT, TEXT, TEXT[], TEXT, INT) TO disponit;
        GRANT EXECUTE ON FUNCTION
            invitasjon_innloes(TEXT, TEXT, TEXT) TO disponit;
        GRANT EXECUTE ON FUNCTION invitasjon_liste(TEXT) TO disponit;
    END IF;
END $$;
