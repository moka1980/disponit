-- 237 — LOVPÅLAGTE FRISTER: plattformens pliktkatalog og firmaets
-- pliktprofil. Frist- og pliktvakten, fase 1 (uten modell).
--
-- Eier 27/11: kjernen kunden betaler for er «Frist- og pliktvakt», og
-- «du kan fikse ting som ikke krever ki modell kall og har verdi for
-- kunder nå først».
--
-- TO HALVDELER:
--
-- * KATALOGEN er plattformens (`plattform_pliktmal`, `plattform_pliktfrist`):
--   hvilke plikter som finnes, hvem de gjelder for (organisasjonsform,
--   MVA-registrert, har ansatte), hjemmelen, kilden — og de KONKRETE
--   fristene, AVLEST fra myndigheten. Plattformeieren vedlikeholder den i
--   plattformadmin (eiers ordre: «alt må betjenes via dashboard»).
--
-- * PROFILEN er firmaets (`firma_pliktprofil`): organisasjonsformen, om det
--   er MVA-registrert og om det har ansatte — hentet fra Enhetsregisteret og
--   bekreftet av kunden. Profilen + katalogen gir firmaets frister, som
--   registreres i M-21s pliktregister (096) med kunden som eier. Der finnes
--   varslene (30/7/1 døgn før, i appen og på e-post) og kvitteringen.
--
-- FRISTENE ER AVLEST, IKKE REGNET (M-47s dom, 123): fristene er
-- myndighetens, de flyttes (helg og helligdag skyver dem til første
-- virkedag), og en utregnet frist ville sett like sikker ut som en avlest.
-- Derfor er hver frist en rad med en dato, ikke en regel.
--
-- STARTKATALOGEN (des. 2026 – des. 2027) er avlest fra hovedreglene hos
-- Skatteetaten, Altinn og Brønnøysundregistrene, med helg- og
-- helligdagsforskyvningen til første virkedag. Eieren kontrollerer og
-- vedlikeholder den i plattformadmin.
-- ============================================================

CREATE TABLE plattform_pliktmal (
    nokkel        TEXT PRIMARY KEY CHECK (nokkel ~ '^[a-z][a-z0-9_]{2,63}$'),
    tittel        TEXT NOT NULL CHECK (tittel ~ '[^[:space:]]'
                                       AND length(tittel) <= 200),
    myndighet     TEXT NOT NULL CHECK (myndighet IN (
                      'skatteetaten', 'brreg', 'altinn', 'nav',
                      'arbeidstilsynet', 'ssb', 'annen')),
    hjemmel       TEXT NOT NULL CHECK (hjemmel ~ '[^[:space:]]'
                                       AND length(hjemmel) <= 300),
    kilde_url     TEXT NOT NULL CHECK (kilde_url ~ '^https://[^[:space:]]+$'
                                       AND length(kilde_url) <= 500),
    -- Tom liste = alle organisasjonsformer. Kodene er Enhetsregisterets
    -- (AS, ASA, ENK, NUF, DA, ANS, SA …).
    orgformer     TEXT[] NOT NULL DEFAULT '{}' CONSTRAINT pliktmal_orgformer
                  CHECK (array_to_string(orgformer, ',')
                         ~ '^([A-Z]{2,4}(,[A-Z]{2,4})*)?$'),
    krever_mva     BOOLEAN NOT NULL DEFAULT false,
    krever_ansatte BOOLEAN NOT NULL DEFAULT false,
    aktiv         BOOLEAN NOT NULL DEFAULT true,
    endret        TIMESTAMPTZ NOT NULL DEFAULT now(),
    endret_av     TEXT NOT NULL CHECK (endret_av ~ '[^[:space:]]')
);

CREATE TABLE plattform_pliktfrist (
    nokkel     TEXT NOT NULL REFERENCES plattform_pliktmal (nokkel),
    -- Hva fristen gjelder: «1. termin 2027», «Inntektsåret 2026».
    periode    TEXT NOT NULL CHECK (periode ~ '[^[:space:]]'
                                    AND length(periode) <= 100),
    frist      DATE NOT NULL,
    endret     TIMESTAMPTZ NOT NULL DEFAULT now(),
    endret_av  TEXT NOT NULL CHECK (endret_av ~ '[^[:space:]]'),
    PRIMARY KEY (nokkel, frist)
);

CREATE TABLE firma_pliktprofil (
    tenant      TEXT PRIMARY KEY CHECK (length(btrim(tenant)) > 0),
    orgnummer   TEXT CHECK (orgnummer IS NULL OR orgnummer ~ '^[0-9]{9}$'),
    orgform     TEXT NOT NULL CHECK (orgform ~ '^[A-Z]{2,4}$'),
    mva         BOOLEAN NOT NULL,
    ansatte     BOOLEAN NOT NULL,
    -- Hvor opplysningene kom fra: Enhetsregisteret, eller kunden selv.
    kilde       TEXT NOT NULL CHECK (kilde IN ('brreg', 'manuell')),
    oppdatert   TIMESTAMPTZ NOT NULL DEFAULT now(),
    oppdatert_av TEXT NOT NULL CHECK (oppdatert_av ~ '[^[:space:]]')
);
ALTER TABLE firma_pliktprofil ENABLE ROW LEVEL SECURITY;
ALTER TABLE firma_pliktprofil FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON firma_pliktprofil
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

REVOKE ALL ON TABLE plattform_pliktmal, plattform_pliktfrist,
                    firma_pliktprofil FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON TABLE plattform_pliktmal
    TO disponit_plattform_eier;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE plattform_pliktfrist
    TO disponit_plattform_eier;
GRANT SELECT, INSERT, UPDATE ON TABLE firma_pliktprofil
    TO disponit_plattform_eier;

-- ---------------------------------------------------------------------
-- STARTKATALOGEN
-- ---------------------------------------------------------------------
INSERT INTO plattform_pliktmal (nokkel, tittel, myndighet, hjemmel,
                                kilde_url, orgformer, krever_mva,
                                krever_ansatte, endret_av) VALUES
 ('mva_termin', 'MVA-melding og betaling (alminnelig termin)',
  'skatteetaten', 'Skatteforvaltningsforskriften § 8-3-10',
  'https://info.altinn.no/starte-og-drive/skatt-og-avgift/avgift/rapportering-og-betaling-av-mva/',
  '{}', true, false, 'migrasjon:237'),
 ('a_melding', 'A-melding',
  'skatteetaten', 'A-opplysningsloven',
  'https://www.skatteetaten.no/en/business-and-organisation/employer/the-a-melding/deadlines-and-payment/',
  '{}', false, true, 'migrasjon:237'),
 ('aga_forskuddstrekk', 'Betaling av arbeidsgiveravgift og forskuddstrekk',
  'skatteetaten', 'Skattebetalingsloven § 10-10',
  'https://www.skatteetaten.no/en/business-and-organisation/employer/the-a-melding/deadlines-and-payment/',
  '{}', false, true, 'migrasjon:237'),
 ('forskuddsskatt_selskap', 'Forskuddsskatt for aksjeselskap',
  'skatteetaten', 'Skattebetalingsloven § 10-20',
  'https://www.skatteetaten.no/bedrift-og-organisasjon/skatt/forskuddsskatt/',
  '{AS,ASA}', false, false, 'migrasjon:237'),
 ('forskuddsskatt_enk', 'Forskuddsskatt for enkeltpersonforetak',
  'skatteetaten', 'Skattebetalingsloven § 10-20',
  'https://www.skatteetaten.no/bedrift-og-organisasjon/skatt/forskuddsskatt/',
  '{ENK}', false, false, 'migrasjon:237'),
 ('aksjonaerregisteroppgave', 'Aksjonærregisteroppgaven',
  'skatteetaten', 'Skatteforvaltningsloven § 7-8',
  'https://www.skatteetaten.no/en/business-and-organisation/reporting-and-industries/shareholder-register-statement/',
  '{AS,ASA}', false, false, 'migrasjon:237'),
 ('skattemelding_selskap', 'Skattemelding for aksjeselskap',
  'skatteetaten', 'Skatteforvaltningsloven § 8-2',
  'https://www.skatteetaten.no/bedrift-og-organisasjon/skatt/skattemelding-for-aksjeselskap/',
  '{AS,ASA}', false, false, 'migrasjon:237'),
 ('skattemelding_enk', 'Skattemelding for næringsdrivende (ENK)',
  'skatteetaten', 'Skatteforvaltningsloven § 8-2',
  'https://www.skatteetaten.no/person/skatt/skattemelding/',
  '{ENK}', false, false, 'migrasjon:237'),
 ('generalforsamling', 'Ordinær generalforsamling (fastsette årsregnskapet)',
  'annen', 'Aksjeloven § 5-5',
  'https://www.brreg.no/en/other-topics/remember-the-deadline-31-july/',
  '{AS}', false, false, 'migrasjon:237'),
 ('arsregnskap', 'Årsregnskap til Regnskapsregisteret',
  'brreg', 'Regnskapsloven § 8-2',
  'https://www.brreg.no/en/other-topics/remember-the-deadline-31-july/',
  '{AS,ASA}', false, false, 'migrasjon:237');

INSERT INTO plattform_pliktfrist (nokkel, periode, frist, endret_av)
SELECT f.nokkel, f.periode, f.frist::date, 'migrasjon:237'
  FROM (VALUES
 ('mva_termin', '5. termin 2026 (sep–okt)', '2026-12-10'),
 ('mva_termin', '6. termin 2026 (nov–des)', '2027-02-10'),
 ('mva_termin', '1. termin 2027 (jan–feb)', '2027-04-12'),
 ('mva_termin', '2. termin 2027 (mar–apr)', '2027-06-10'),
 ('mva_termin', '3. termin 2027 (mai–jun)', '2027-08-31'),
 ('mva_termin', '4. termin 2027 (jul–aug)', '2027-10-11'),
 ('mva_termin', '5. termin 2027 (sep–okt)', '2027-12-10'),
 ('a_melding', 'November 2026', '2026-12-07'),
 ('a_melding', 'Desember 2026', '2027-01-05'),
 ('a_melding', 'Januar 2027', '2027-02-05'),
 ('a_melding', 'Februar 2027', '2027-03-05'),
 ('a_melding', 'Mars 2027', '2027-04-05'),
 ('a_melding', 'April 2027', '2027-05-05'),
 ('a_melding', 'Mai 2027', '2027-06-07'),
 ('a_melding', 'Juni 2027', '2027-07-05'),
 ('a_melding', 'Juli 2027', '2027-08-05'),
 ('a_melding', 'August 2027', '2027-09-06'),
 ('a_melding', 'September 2027', '2027-10-05'),
 ('a_melding', 'Oktober 2027', '2027-11-05'),
 ('a_melding', 'November 2027', '2027-12-06'),
 ('aga_forskuddstrekk', 'November–desember 2026', '2027-01-15'),
 ('aga_forskuddstrekk', 'Januar–februar 2027', '2027-03-15'),
 ('aga_forskuddstrekk', 'Mars–april 2027', '2027-05-18'),
 ('aga_forskuddstrekk', 'Mai–juni 2027', '2027-07-15'),
 ('aga_forskuddstrekk', 'Juli–august 2027', '2027-09-15'),
 ('aga_forskuddstrekk', 'September–oktober 2027', '2027-11-15'),
 ('forskuddsskatt_selskap', 'Inntektsåret 2026, 1. termin', '2027-02-15'),
 ('forskuddsskatt_selskap', 'Inntektsåret 2026, 2. termin', '2027-04-15'),
 ('forskuddsskatt_enk', '4. termin 2026', '2026-12-15'),
 ('forskuddsskatt_enk', '1. termin 2027', '2027-03-15'),
 ('forskuddsskatt_enk', '2. termin 2027', '2027-06-15'),
 ('forskuddsskatt_enk', '3. termin 2027', '2027-09-15'),
 ('forskuddsskatt_enk', '4. termin 2027', '2027-12-15'),
 ('aksjonaerregisteroppgave', 'Inntektsåret 2026', '2027-02-01'),
 ('skattemelding_selskap', 'Inntektsåret 2026', '2027-05-31'),
 ('skattemelding_enk', 'Inntektsåret 2026', '2027-05-31'),
 ('generalforsamling', 'Regnskapsåret 2026', '2027-06-30'),
 ('arsregnskap', 'Regnskapsåret 2026', '2027-07-31')
  ) AS f (nokkel, periode, frist);

SET LOCAL ROLE disponit_plattform_eier;

-- KATALOGEN, åpen for lesing: den er plattformens egen og inneholder bare
-- offentlige regler.
CREATE FUNCTION plattform_pliktkatalog()
RETURNS TABLE(nokkel TEXT, tittel TEXT, myndighet TEXT, hjemmel TEXT,
              kilde_url TEXT, orgformer TEXT[], krever_mva BOOLEAN,
              krever_ansatte BOOLEAN, aktiv BOOLEAN, periode TEXT,
              frist DATE)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT m.nokkel, m.tittel, m.myndighet, m.hjemmel, m.kilde_url,
           m.orgformer, m.krever_mva, m.krever_ansatte, m.aktiv,
           f.periode, f.frist
      FROM public.plattform_pliktmal m
      LEFT JOIN public.plattform_pliktfrist f ON f.nokkel = m.nokkel
     ORDER BY m.nokkel, f.frist
$$;

CREATE FUNCTION plattform_pliktmal_sett(
        p_bruker_id TEXT, p_nokkel TEXT, p_tittel TEXT, p_myndighet TEXT,
        p_hjemmel TEXT, p_kilde_url TEXT, p_orgformer TEXT[],
        p_krever_mva BOOLEAN, p_krever_ansatte BOOLEAN, p_aktiv BOOLEAN)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.plattform_krev_eier(p_bruker_id, 'plattform_pliktmal_sett');
    INSERT INTO public.plattform_pliktmal
        (nokkel, tittel, myndighet, hjemmel, kilde_url, orgformer,
         krever_mva, krever_ansatte, aktiv, endret, endret_av)
    VALUES (p_nokkel, btrim(p_tittel), p_myndighet, btrim(p_hjemmel),
            btrim(p_kilde_url), coalesce(p_orgformer, '{}'),
            coalesce(p_krever_mva, false), coalesce(p_krever_ansatte, false),
            coalesce(p_aktiv, true), now(), 'bruker:' || p_bruker_id)
    ON CONFLICT (nokkel) DO UPDATE SET
        tittel = EXCLUDED.tittel, myndighet = EXCLUDED.myndighet,
        hjemmel = EXCLUDED.hjemmel, kilde_url = EXCLUDED.kilde_url,
        orgformer = EXCLUDED.orgformer, krever_mva = EXCLUDED.krever_mva,
        krever_ansatte = EXCLUDED.krever_ansatte, aktiv = EXCLUDED.aktiv,
        endret = EXCLUDED.endret, endret_av = EXCLUDED.endret_av;
END $$;

CREATE FUNCTION plattform_pliktfrist_sett(
        p_bruker_id TEXT, p_nokkel TEXT, p_periode TEXT, p_frist DATE)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.plattform_krev_eier(p_bruker_id,
                                       'plattform_pliktfrist_sett');
    INSERT INTO public.plattform_pliktfrist
        (nokkel, periode, frist, endret, endret_av)
    VALUES (p_nokkel, btrim(p_periode), p_frist, now(),
            'bruker:' || p_bruker_id)
    ON CONFLICT (nokkel, frist) DO UPDATE SET
        periode = EXCLUDED.periode, endret = EXCLUDED.endret,
        endret_av = EXCLUDED.endret_av;
END $$;

CREATE FUNCTION plattform_pliktfrist_slett(
        p_bruker_id TEXT, p_nokkel TEXT, p_frist DATE)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_rader INT;
BEGIN
    PERFORM public.plattform_krev_eier(p_bruker_id,
                                       'plattform_pliktfrist_slett');
    DELETE FROM public.plattform_pliktfrist
     WHERE nokkel = p_nokkel AND frist = p_frist;
    GET DIAGNOSTICS v_rader = ROW_COUNT;
    RETURN v_rader > 0;
END $$;

-- PROFILEN, bundet til kallerens tenantkontekst (235-formen).
CREATE FUNCTION firma_pliktprofil_les(p_tenant TEXT)
RETURNS TABLE(orgnummer TEXT, orgform TEXT, mva BOOLEAN, ansatte BOOLEAN,
              kilde TEXT, oppdatert TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '')
       IS DISTINCT FROM p_tenant THEN
        RAISE EXCEPTION 'firma_pliktprofil_les: p_tenant er ikke kallerens'
                        ' kontekst' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN QUERY
        SELECT p.orgnummer, p.orgform, p.mva, p.ansatte, p.kilde, p.oppdatert
          FROM public.firma_pliktprofil p WHERE p.tenant = p_tenant;
END $$;

CREATE FUNCTION firma_pliktprofil_sett(
        p_tenant TEXT, p_orgnummer TEXT, p_orgform TEXT, p_mva BOOLEAN,
        p_ansatte BOOLEAN, p_kilde TEXT, p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '')
       IS DISTINCT FROM p_tenant THEN
        RAISE EXCEPTION 'firma_pliktprofil_sett: p_tenant er ikke kallerens'
                        ' kontekst' USING ERRCODE = 'insufficient_privilege';
    END IF;
    INSERT INTO public.firma_pliktprofil
        (tenant, orgnummer, orgform, mva, ansatte, kilde, oppdatert,
         oppdatert_av)
    VALUES (p_tenant, nullif(btrim(p_orgnummer), ''), upper(btrim(p_orgform)),
            p_mva, p_ansatte, p_kilde, now(), p_aktor)
    ON CONFLICT (tenant) DO UPDATE SET
        orgnummer = EXCLUDED.orgnummer, orgform = EXCLUDED.orgform,
        mva = EXCLUDED.mva, ansatte = EXCLUDED.ansatte,
        kilde = EXCLUDED.kilde, oppdatert = EXCLUDED.oppdatert,
        oppdatert_av = EXCLUDED.oppdatert_av;
END $$;

REVOKE ALL ON FUNCTION plattform_pliktkatalog() FROM PUBLIC;
REVOKE ALL ON FUNCTION plattform_pliktmal_sett(TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, TEXT[], BOOLEAN, BOOLEAN, BOOLEAN) FROM PUBLIC;
REVOKE ALL ON FUNCTION plattform_pliktfrist_sett(TEXT, TEXT, TEXT, DATE)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION plattform_pliktfrist_slett(TEXT, TEXT, DATE)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION firma_pliktprofil_les(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION firma_pliktprofil_sett(TEXT, TEXT, TEXT, BOOLEAN,
    BOOLEAN, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION plattform_pliktkatalog() TO disponit;
GRANT EXECUTE ON FUNCTION plattform_pliktmal_sett(TEXT, TEXT, TEXT, TEXT,
    TEXT, TEXT, TEXT[], BOOLEAN, BOOLEAN, BOOLEAN) TO disponit;
GRANT EXECUTE ON FUNCTION plattform_pliktfrist_sett(TEXT, TEXT, TEXT, DATE)
    TO disponit;
GRANT EXECUTE ON FUNCTION plattform_pliktfrist_slett(TEXT, TEXT, DATE)
    TO disponit;
GRANT EXECUTE ON FUNCTION firma_pliktprofil_les(TEXT) TO disponit;
GRANT EXECUTE ON FUNCTION firma_pliktprofil_sett(TEXT, TEXT, TEXT, BOOLEAN,
    BOOLEAN, TEXT, TEXT) TO disponit;

RESET ROLE;
