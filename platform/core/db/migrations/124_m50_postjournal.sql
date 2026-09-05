-- =====================================================================
-- M-50 POSTJOURNAL- OG INNSYNSVAKTEN (v1) — OFFENTLIG ER IKKE FRITT.
-- =====================================================================
--
-- V1-DOMMEN: MODULEN HENTER INGENTING OG SENDER INGEN HENVENDELSE.
--
-- DEN NÆRLIGGENDE BEGRUNNELSEN TREFFER IKKE HER, og det er verdt å si
-- rett ut: postjournaler ER offentlige. Innvendingen mot utgående
-- oppslag — «vi har ikke lov til å se på det» — gjelder ikke.
--
-- DET SOM TREFFER ER NOE ANNET.
--
-- EN POSTJOURNAL INNEHOLDER NAVNGITTE PRIVATPERSONER. At en opplysning
-- er offentlig tilgjengelig gjør den ikke fri å samle, sammenstille og
-- bruke til salg. Formålsbegrensningen gjelder UANSETT KILDE, og en
-- systematisk høsting er en HELT ANNEN BEHANDLING enn det enkeltoppslag
-- et menneske gjør i en kommunes journal.
--
-- Forskjellen er ikke gradvis. Ett oppslag er innsyn; ti tusen oppslag
-- sammenstilt i et register er en profil — og profilen er VÅR, ikke
-- kommunens. Det er derfor modulen er en VAKT og ikke en høstemaskin.
--
-- DERFOR REGISTRERER v1 JOURNALPOSTER ET MENNESKE HAR HENTET, og
-- knytter dem til en sak. Et automatisk søk mot hver kommunes journal
-- hører hjemme i oppdragskontraktens `ekstern_lesing` med
-- målautorisasjon og frekvensgrense — ikke i en modulfil, og ikke uten
-- at formålet er skrevet ned først.
--
-- HVERT TREFF BÆRER FORMÅLET SITT, og det er ikke en kolonne til pynt:
-- uten den er sammenstillingen en behandling ingen kan gjøre rede for.
-- «VI FANT DET PÅ NETT» ER IKKE ET RETTSLIG GRUNNLAG.
--
-- OG HVER NAVNGITT PERSON BÆRER EN SLETTEFRIST. Den er NOT NULL, ikke
-- et sveipefunn: en personopplysning uten sletteplan skal ikke kunne
-- OPPSTÅ. Samme form som M-52s forslag uten grunnlag — det farlige
-- gjøres umulig, ikke oppdaget.
--
-- MODULENS EGET FUNN INGEN KAN LUKKE er derfor et annet: en
-- slettefrist som HAR gått mens raden fortsatt står. Det er ikke en
-- mening man kan være uenig i, og et menneske som klikket det bort
-- ville skrudd av det ene varselet som sier at vi oppbevarer en
-- navngitt privatperson lenger enn vi selv har bestemt.
--
-- KLYNGENS DELTE DOM: journalens format er KOMMUNENS, og det endres.
-- Hver registrering bærer kildeversjonen sin, snapshotet.
--
-- GRENSEN MOT M-18: M-18 eier ONBOARDINGEN av en kunde vi har. M-50
-- finner en sak som KAN bli en kunde. Overgangen er et menneskes.
--
-- GRENSEN MOT M-30: M-30 eier personvernforespørsler — den registrerer
-- at NOEN HAR BEDT om innsyn eller sletting, og måler fristen. M-50
-- eier vår egen oppbevaring av opplysninger vi selv har samlet inn.
-- M-30 svarer på det noen spør om; M-50 rydder etter oss selv.
-- =====================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles
                    WHERE rolname = 'disponit_postjournal_eier') THEN
        RAISE EXCEPTION 'rollen disponit_postjournal_eier mangler —'
            ' kjør deploy/staging/oppsett-postgresql.sh først';
    END IF;
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO disponit_postjournal_eier;
GRANT INSERT ON revisjonslogg TO disponit_postjournal_eier;

SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION krev_tenantkontekst(TEXT, TEXT)
    TO disponit_postjournal_eier;
RESET ROLE;

-- TABELLENE EIES AV MIGRATOREN, FUNKSJONENE AV MODULROLLEN (122/123s
-- form). RLS slås på av en `ALTER TABLE`, og bare eieren kan gjøre det:
-- lager modulrollen tabellene, kan den også ta radvakten AV igjen.

-- ---------------------------------------------------------------------
-- TENANTENS EGNE GRENSER.
--
-- HVOR LENGE VI KAN OPPBEVARE ER TENANTENS BESLUTNING, ikke vår. En
-- kommune som følger med på egne saker og et byrå som kartlegger et
-- marked har ikke samme grunnlag — og et tak vi satte for dem ville
-- vært en fullmakt modulen ga seg selv over kundens etterlevelse.
-- ---------------------------------------------------------------------
CREATE TABLE journalkrav (
    tenant TEXT PRIMARY KEY CHECK (length(btrim(tenant)) > 0),
    -- MAKS oppbevaring for en navngitt privatperson. Døra nekter en
    -- slettefrist lenger enn denne.
    sletteplan_maks_dogn INT NOT NULL DEFAULT 365
        CHECK (sletteplan_maks_dogn BETWEEN 1 AND 3650),
    -- Hvor lenge før slettefristen vi sier fra.
    slettevarsel_dogn INT NOT NULL DEFAULT 30
        CHECK (slettevarsel_dogn BETWEEN 1 AND 365),
    -- Hvor lenge før en kildeversjon avvikles vi sier fra.
    kildevarsel_dogn INT NOT NULL DEFAULT 60
        CHECK (kildevarsel_dogn BETWEEN 1 AND 730),
    versjon INT NOT NULL DEFAULT 1 CHECK (versjon > 0),
    -- IDEMPOTENSNØKKELEN LEVER PÅ RADEN (M-51s lærdom 119, M-47s 123).
    -- Hvert funn bærer `kravversjon`: en versjon som økte uten at en
    -- grense endret seg gjør funnhistorikken uleselig.
    siste_nokkel TEXT,
    satt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    satt_av TEXT NOT NULL CHECK (length(btrim(satt_av)) > 0)
);

-- ---------------------------------------------------------------------
-- KILDEN — KOMMUNENS, IKKE VÅR.
--
-- Identiteten er FROSSET. Bare `gyldig_til` kan settes senere, fordi en
-- kommune som legger om journalformatet er nettopp den endringen
-- modulen skal følge med på (121s dom, 122/123s form).
-- ---------------------------------------------------------------------
CREATE TABLE journalkilde (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kilde_id UUID NOT NULL,
    PRIMARY KEY (tenant, kilde_id),
    -- Hvem som fører journalen.
    organ TEXT NOT NULL CHECK (organ ~ '[^[:space:]]'),
    organnummer TEXT CHECK (organnummer IS NULL
        OR organnummer ~ '^[0-9]{9}$'),
    -- Formatet og versjonen. KLYNGENS DELTE INVARIANT.
    format TEXT NOT NULL CHECK (format IN (
        'noark5', 'einnsyn', 'kommunal_web', 'annet')),
    versjon TEXT NOT NULL CHECK (versjon ~ '[^[:space:]]'),
    gyldig_fra DATE NOT NULL,
    gyldig_til DATE,
    CONSTRAINT journalkilde_vindu CHECK (
        gyldig_til IS NULL OR gyldig_til >= gyldig_fra),
    innhold_sha256 TEXT NOT NULL
        CHECK (innhold_sha256 ~ '^[0-9a-f]{64}$'),
    kilde_url TEXT,
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (length(btrim(registrert_av)) > 0),
    CONSTRAINT journalkilde_unik UNIQUE (tenant, organ, format,
                                         versjon)
);
CREATE INDEX journalkilde_gyldig_idx
    ON journalkilde (tenant, gyldig_til);

-- ---------------------------------------------------------------------
-- SAKEN — VÅRT EGET SPOR, ikke kommunens.
--
-- FORMÅLET STÅR HER, PÅ SAKEN, og ikke bare på hver post. Grunnen er
-- at en sammenstilling er en behandling: ti journalposter samlet under
-- ett spor er noe annet enn ti oppslag, og det er sporet som må kunne
-- gjøre rede for seg.
-- ---------------------------------------------------------------------
CREATE TABLE journalsak (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    sak_id UUID NOT NULL,
    PRIMARY KEY (tenant, sak_id),
    tittel TEXT NOT NULL CHECK (tittel ~ '[^[:space:]]'),
    -- FORMÅLET, SKREVET NED. Ikke en enum: et formål som kan velges
    -- fra en liste er et formål ingen har tenkt gjennom, og
    -- «markedsføring» sier ikke hva vi faktisk skal gjøre.
    formaal TEXT NOT NULL CHECK (length(btrim(formaal)) >= 16),
    -- BEHANDLINGSGRUNNLAGET. Hvilken hjemmel i personvernforordningen
    -- sammenstillingen hviler på. «Vi fant det på nett» står ikke i
    -- lista, og det er hele poenget.
    grunnlag TEXT NOT NULL CHECK (grunnlag IN (
        'berettiget_interesse', 'avtale', 'rettslig_forpliktelse',
        'samtykke')),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av TEXT NOT NULL
        CHECK (length(btrim(opprettet_av)) > 0)
);

-- ---------------------------------------------------------------------
-- JOURNALPOSTEN — ÉN OPPFØRING ET MENNESKE HAR HENTET.
--
-- HELE RADEN ER FROSSET. `hentet_av_person` heter det den er: et
-- menneske gjorde oppslaget. Det finnes ingen `hentet_automatisk`, og
-- det er ikke en forglemmelse.
--
-- SNAPSHOTET STÅR VED SIDEN AV FREMMEDNØKKELEN: nøkkelen binder til
-- raden, snapshotet til TEKSTEN — og det er snapshotet som svarer
-- «hvilket format leste vi dette i» når kommunen har lagt om.
-- ---------------------------------------------------------------------
CREATE TABLE journalpost (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    post_id UUID NOT NULL,
    PRIMARY KEY (tenant, post_id),
    sak_id UUID NOT NULL,
    kilde_id UUID NOT NULL,
    CONSTRAINT journalpost_sak_fk FOREIGN KEY (tenant, sak_id)
        REFERENCES journalsak (tenant, sak_id),
    CONSTRAINT journalpost_kilde_fk FOREIGN KEY (tenant, kilde_id)
        REFERENCES journalkilde (tenant, kilde_id),
    -- SNAPSHOTET: hva kilden HET da posten ble registrert.
    organ_ved_registrering TEXT NOT NULL,
    format_ved_registrering TEXT NOT NULL,
    kildeversjon_ved_registrering TEXT NOT NULL,
    -- KOMMUNENS EGEN IDENTIFIKASJON av posten.
    journalnummer TEXT NOT NULL CHECK (journalnummer ~ '[^[:space:]]'),
    journaldato DATE NOT NULL,
    dokumenttittel TEXT NOT NULL
        CHECK (dokumenttittel ~ '[^[:space:]]'),
    -- FORMÅLET PÅ POSTEN OGSÅ, ikke bare på saken. En post kan hentes
    -- inn i en sak av en annen grunn enn saken ble opprettet for, og
    -- da er det DEN grunnen som må kunne gjøres rede for.
    formaal TEXT NOT NULL CHECK (length(btrim(formaal)) >= 16),
    -- ET MENNESKE HENTET DEN. Navnet står, og kolonnen heter det den
    -- er: den som leser skal ikke kunne tro at systemet gjorde det.
    hentet_av_person TEXT NOT NULL
        CHECK (length(btrim(hentet_av_person)) > 0),
    hentet_dato DATE NOT NULL,
    kravversjon INT NOT NULL CHECK (kravversjon > 0),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (length(btrim(registrert_av)) > 0),
    -- SAMME JOURNALNUMMER FRA SAMME ORGAN ER ÉN POST. To rader ville
    -- gitt to formål for det samme oppslaget.
    CONSTRAINT journalpost_unik UNIQUE (tenant, kilde_id,
                                        journalnummer)
);
CREATE INDEX journalpost_sak_idx ON journalpost (tenant, sak_id);
CREATE INDEX journalpost_kilde_idx ON journalpost (tenant, kilde_id);

-- ---------------------------------------------------------------------
-- DEN NAVNGITTE PERSONEN — MODULENS TYNGSTE TABELL.
--
-- SLETTEFRISTEN ER `NOT NULL`, OG DET ER HELE POENGET.
--
-- Invarianten heter `personopplysning_uten_sletteplan`, og den er ikke
-- et sveipefunn her: en personopplysning uten sletteplan skal ikke
-- kunne OPPSTÅ. Samme form som M-52s forslag uten grunnlag — det
-- farlige gjøres UMULIG, ikke oppdaget i etterkant.
--
-- Grunnen er at oppdagelsen kommer for sent. Et forslag uten grunnlag
-- kan trekkes tilbake; en personopplysning som har ligget i registeret
-- i et halvår uten plan HAR ligget der, og det kan ingen sveip gjøre
-- ugjort.
--
-- `anonymisert_ts` OG IKKE `DELETE`: at vi HAR oppbevart noen skal
-- fortsatt kunne leses av den som spør — men uten navnet. Raden blir
-- et spor av en behandling, ikke en person. Sletting av selve raden
-- ville fjernet beviset på at vi hadde den.
-- ---------------------------------------------------------------------
CREATE TABLE journalperson (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    person_id UUID NOT NULL,
    PRIMARY KEY (tenant, person_id),
    post_id UUID NOT NULL,
    CONSTRAINT journalperson_post_fk FOREIGN KEY (tenant, post_id)
        REFERENCES journalpost (tenant, post_id),
    -- NAVNET. Nullbart BARE fordi anonymiseringen tømmer det — ved
    -- registrering krever døra at det står.
    navn TEXT,
    rolle TEXT NOT NULL CHECK (rolle IN (
        'avsender', 'mottaker', 'part', 'omtalt')),
    -- SLETTEFRISTEN. NOT NULL — se tabellkommentaren.
    slettefrist DATE NOT NULL,
    anonymisert_ts TIMESTAMPTZ,
    anonymisert_av TEXT,
    CONSTRAINT journalperson_anonymisering CHECK (
        (anonymisert_ts IS NULL AND anonymisert_av IS NULL
             AND navn IS NOT NULL)
        OR (anonymisert_ts IS NOT NULL AND anonymisert_av IS NOT NULL
             AND navn IS NULL)),
    kravversjon INT NOT NULL CHECK (kravversjon > 0),
    registrert TIMESTAMPTZ NOT NULL DEFAULT now(),
    registrert_av TEXT NOT NULL
        CHECK (length(btrim(registrert_av)) > 0)
);
CREATE INDEX journalperson_frist_idx
    ON journalperson (tenant, slettefrist)
    WHERE anonymisert_ts IS NULL;
CREATE INDEX journalperson_post_idx ON journalperson (tenant, post_id);

-- ---------------------------------------------------------------------
-- FUNNENE — NATTENS MÅLING.
--
-- TO KAN IKKE LUKKES AV ET MENNESKE:
--
--   `post_mot_utlopt_kilde` er KLYNGENS. Posten ble lest i et format
--   som siden er lagt om. Den ser velformet ut, og feltene kan bety
--   noe annet enn de gjorde. Den forsvinner når posten registreres på
--   nytt mot gjeldende kildeversjon — en HANDLING.
--
--   `slettefrist_passert` er MODULENS EGET, og det tyngste her. Vi
--   oppbevarer en navngitt privatperson lenger enn vi SELV har
--   bestemt. Det er ikke en mening man kan være uenig i, og et
--   menneske som klikket det bort ville skrudd av det ene varselet som
--   sier at vi bryter vår egen sletteplan. Det lukkes av at raden
--   ANONYMISERES — altså av at opplysningen faktisk er borte.
--
-- `slettefrist_naermer_seg` KAN lukkes: «jeg har sett den, den skal
-- forlenges» er en legitim beslutning om noe som ennå ikke er brutt.
-- Skillet er det samme som M-47s mellom en påminnelse og et avvik.
-- ---------------------------------------------------------------------
CREATE TABLE journalfunn (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    funn_id UUID NOT NULL,
    PRIMARY KEY (tenant, funn_id),
    funntype TEXT NOT NULL
        CONSTRAINT journalfunn_type CHECK (funntype IN (
            'ingen_krav',
            'kilde_utlopt',
            'kilde_utloper_snart',
            'post_mot_utlopt_kilde',
            'slettefrist_naermer_seg',
            'slettefrist_passert')),
    kilde_id UUID,
    post_id UUID,
    person_id UUID,
    CONSTRAINT journalfunn_nivaa CHECK (
        CASE funntype
          WHEN 'ingen_krav' THEN
            kilde_id IS NOT NULL OR post_id IS NOT NULL
          WHEN 'kilde_utlopt' THEN kilde_id IS NOT NULL
          WHEN 'kilde_utloper_snart' THEN kilde_id IS NOT NULL
          WHEN 'post_mot_utlopt_kilde' THEN post_id IS NOT NULL
          ELSE person_id IS NOT NULL
        END),
    CONSTRAINT journalfunn_en_noekkel CHECK (
        num_nonnulls(kilde_id, post_id, person_id) = 1),
    -- DØGN, MED FORTEGN BÅRET AV FUNNTYPEN: `naermer_seg` teller ned,
    -- `passert` teller opp. Tallet er poenget — en frist som gikk i
    -- går og en som gikk for et halvår siden er to ulike brudd.
    over_grense INT,
    detalj TEXT CHECK (detalj IS NULL OR detalj ~ '[^[:space:]]'),
    kravversjon INT,
    forst_sett TIMESTAMPTZ NOT NULL DEFAULT now(),
    sist_sett_sveip TIMESTAMPTZ NOT NULL DEFAULT now(),
    apen BOOLEAN NOT NULL DEFAULT true,
    lukket_ts TIMESTAMPTZ,
    lukket_av TEXT,
    lukkenotat TEXT,
    CONSTRAINT journalfunn_lukking CHECK (
        (apen AND lukket_ts IS NULL AND lukket_av IS NULL
             AND lukkenotat IS NULL)
        OR (NOT apen AND lukket_ts IS NOT NULL))
);
CREATE UNIQUE INDEX journalfunn_kilde_unik
    ON journalfunn (tenant, kilde_id, funntype)
    WHERE kilde_id IS NOT NULL;
CREATE UNIQUE INDEX journalfunn_post_unik
    ON journalfunn (tenant, post_id, funntype)
    WHERE post_id IS NOT NULL;
CREATE UNIQUE INDEX journalfunn_person_unik
    ON journalfunn (tenant, person_id, funntype)
    WHERE person_id IS NOT NULL;
CREATE INDEX journalfunn_apne_idx
    ON journalfunn (tenant, apen, funntype);

-- FUNNENE INGEN KAN LUKKE, SOM EN TABELL OG IKKE EN HUSKEREGEL.
-- Lista står her, én gang, og både døra og lesedøra leser den.
CREATE FUNCTION m50_funn_er_sveipens(p_funntype TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT p_funntype IN ('post_mot_utlopt_kilde',
                          'slettefrist_passert')
$$;

CREATE FUNCTION m50_kilde_gyldig(p_fra DATE, p_til DATE)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog AS $$
    SELECT p_fra <= current_date
       AND (p_til IS NULL OR p_til >= current_date)
$$;

-- HERFRA EIES DØRENE AV POSTJOURNALEIEREN.
SET LOCAL ROLE disponit_postjournal_eier;

CREATE FUNCTION m50_evidens(p_tenant TEXT, p_sak_id UUID,
                            p_handling TEXT, p_aktor TEXT,
                            p_detalj JSONB)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_kanon TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm50_evidens');
    v_kanon := jsonb_build_object(
        'v', '1', 'modul', 'm50_postjournal', 'handling', p_handling,
        'sak_id', p_sak_id::text, 'detalj', p_detalj)::text;
    INSERT INTO public.revisjonslogg
        (tenant, aktor, kilde, input_hash, policy_id, beslutning,
         begrunnelse, handling)
    VALUES (p_tenant, p_aktor, 'm50_postjournal',
            encode(sha256(convert_to(v_kanon, 'UTF8')), 'hex'),
            'plattform:postjournal', 'TILLAT',
            jsonb_build_array(p_handling), p_handling);
END $$;
REVOKE ALL ON FUNCTION m50_evidens(TEXT, UUID, TEXT, TEXT, JSONB)
    FROM PUBLIC;

-- =====================================================================
-- DØRENE.
-- =====================================================================

CREATE FUNCTION m50_sett_krav(p_tenant TEXT, p_maks_dogn INT,
                              p_slettevarsel INT, p_kildevarsel INT,
                              p_aktor TEXT, p_nokkel TEXT)
RETURNS TABLE (sletteplan_maks_dogn INT, slettevarsel_dogn INT,
               kildevarsel_dogn INT, versjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_ny INT;
    v_nokkel TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm50_sett_krav');
    IF p_nokkel IS NULL OR btrim(p_nokkel) = '' THEN
        RAISE EXCEPTION 'm50_sett_krav: idempotensnøkkel mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- LÅS FØRST, LES ETTERPÅ (klynge 6s lærdom, fem ganger).
    PERFORM 1 FROM public.journalkrav
     WHERE tenant = p_tenant FOR UPDATE;

    -- EN REPLAY ER IKKE EN ENDRING (M-51s 119, M-47s 123). Hvert funn
    -- bærer `kravversjon`, og en versjon som økte uten at en grense
    -- endret seg gjør funnhistorikken uleselig.
    SELECT k.siste_nokkel INTO v_nokkel FROM public.journalkrav k
     WHERE k.tenant = p_tenant;
    IF v_nokkel IS NOT NULL AND v_nokkel = p_nokkel THEN
        RETURN QUERY SELECT k.sletteplan_maks_dogn,
                            k.slettevarsel_dogn, k.kildevarsel_dogn,
                            k.versjon
                       FROM public.journalkrav k
                      WHERE k.tenant = p_tenant;
        RETURN;
    END IF;

    INSERT INTO public.journalkrav
        (tenant, sletteplan_maks_dogn, slettevarsel_dogn,
         kildevarsel_dogn, versjon, satt_av, siste_nokkel)
    VALUES (p_tenant, p_maks_dogn, p_slettevarsel, p_kildevarsel,
            1, p_aktor, p_nokkel)
    ON CONFLICT (tenant) DO UPDATE SET
        sletteplan_maks_dogn = EXCLUDED.sletteplan_maks_dogn,
        slettevarsel_dogn = EXCLUDED.slettevarsel_dogn,
        kildevarsel_dogn = EXCLUDED.kildevarsel_dogn,
        versjon = public.journalkrav.versjon + 1,
        satt_ts = now(), satt_av = EXCLUDED.satt_av,
        siste_nokkel = EXCLUDED.siste_nokkel
    RETURNING public.journalkrav.versjon INTO v_ny;

    PERFORM public.m50_evidens(p_tenant, NULL, 'krav_satt', p_aktor,
        jsonb_build_object('sletteplan_maks_dogn', p_maks_dogn,
                           'slettevarsel_dogn', p_slettevarsel,
                           'kildevarsel_dogn', p_kildevarsel,
                           'versjon', v_ny, 'nokkel', p_nokkel));

    RETURN QUERY SELECT k.sletteplan_maks_dogn, k.slettevarsel_dogn,
                        k.kildevarsel_dogn, k.versjon
                   FROM public.journalkrav k
                  WHERE k.tenant = p_tenant;
END $$;
REVOKE ALL ON FUNCTION m50_sett_krav(TEXT, INT, INT, INT, TEXT, TEXT)
    FROM PUBLIC;

-- KILDEN REGISTRERES — OGSÅ EN SOM ALT ER AVVIKLET.
--
-- 121s LÆRDOM: arkivet skal kunne svare på hvilket format vi leste
-- noe i den gangen. Skillet går ved POSTEN — `m50_registrer_post`
-- nekter mot en kildeversjon som ikke gjelder i dag.
CREATE FUNCTION m50_registrer_kilde(
    p_tenant TEXT, p_kilde_id UUID, p_organ TEXT, p_organnummer TEXT,
    p_format TEXT, p_versjon TEXT, p_gyldig_fra DATE,
    p_gyldig_til DATE, p_sha TEXT, p_url TEXT, p_aktor TEXT)
RETURNS TABLE (kilde_id UUID, gyldig_naa BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
        'm50_registrer_kilde');
    INSERT INTO public.journalkilde
        (tenant, kilde_id, organ, organnummer, format, versjon,
         gyldig_fra, gyldig_til, innhold_sha256, kilde_url,
         registrert_av)
    VALUES (p_tenant, p_kilde_id, btrim(p_organ),
            nullif(btrim(coalesce(p_organnummer, '')), ''),
            p_format, btrim(p_versjon), p_gyldig_fra, p_gyldig_til,
            lower(btrim(p_sha)), p_url, p_aktor);

    PERFORM public.m50_evidens(p_tenant, NULL, 'kilde_registrert',
        p_aktor, jsonb_build_object(
            'kilde_id', p_kilde_id::text, 'organ', p_organ,
            'format', p_format, 'versjon', p_versjon));

    RETURN QUERY
    SELECT k.kilde_id,
           public.m50_kilde_gyldig(k.gyldig_fra, k.gyldig_til)
      FROM public.journalkilde k
     WHERE k.tenant = p_tenant AND k.kilde_id = p_kilde_id;
END $$;
REVOKE ALL ON FUNCTION m50_registrer_kilde(
    TEXT, UUID, TEXT, TEXT, TEXT, TEXT, DATE, DATE, TEXT, TEXT, TEXT)
    FROM PUBLIC;

CREATE FUNCTION m50_sett_gyldig_til(p_tenant TEXT, p_kilde_id UUID,
                                    p_gyldig_til DATE, p_aktor TEXT)
RETURNS TABLE (kilde_id UUID, gyldig_til DATE, gyldig_naa BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_fra DATE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
        'm50_sett_gyldig_til');
    SELECT k.gyldig_fra INTO v_fra FROM public.journalkilde k
     WHERE k.tenant = p_tenant AND k.kilde_id = p_kilde_id
       FOR UPDATE;
    IF v_fra IS NULL THEN
        RAISE EXCEPTION 'm50_sett_gyldig_til: ukjent kilde %',
            p_kilde_id USING ERRCODE = 'no_data_found';
    END IF;
    IF p_gyldig_til IS NOT NULL AND p_gyldig_til < v_fra THEN
        RAISE EXCEPTION 'm50_sett_gyldig_til: % er før kildens'
            ' startdato %', p_gyldig_til, v_fra
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.journalkilde k SET gyldig_til = p_gyldig_til
     WHERE k.tenant = p_tenant AND k.kilde_id = p_kilde_id;

    PERFORM public.m50_evidens(p_tenant, NULL, 'kilde_avvikles',
        p_aktor, jsonb_build_object('kilde_id', p_kilde_id::text,
                                    'gyldig_til', p_gyldig_til));

    RETURN QUERY
    SELECT k.kilde_id, k.gyldig_til,
           public.m50_kilde_gyldig(k.gyldig_fra, k.gyldig_til)
      FROM public.journalkilde k
     WHERE k.tenant = p_tenant AND k.kilde_id = p_kilde_id;
END $$;
REVOKE ALL ON FUNCTION m50_sett_gyldig_til(TEXT, UUID, DATE, TEXT)
    FROM PUBLIC;

CREATE FUNCTION m50_opprett_sak(
    p_tenant TEXT, p_sak_id UUID, p_tittel TEXT, p_formaal TEXT,
    p_grunnlag TEXT, p_aktor TEXT)
RETURNS TABLE (sak_id UUID, formaal TEXT, grunnlag TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm50_opprett_sak');
    INSERT INTO public.journalsak
        (tenant, sak_id, tittel, formaal, grunnlag, opprettet_av)
    VALUES (p_tenant, p_sak_id, btrim(p_tittel), btrim(p_formaal),
            p_grunnlag, p_aktor);

    PERFORM public.m50_evidens(p_tenant, p_sak_id, 'sak_opprettet',
        p_aktor, jsonb_build_object('tittel', p_tittel,
                                    'formaal', p_formaal,
                                    'grunnlag', p_grunnlag));

    RETURN QUERY SELECT s.sak_id, s.formaal, s.grunnlag
                   FROM public.journalsak s
                  WHERE s.tenant = p_tenant AND s.sak_id = p_sak_id;
END $$;
REVOKE ALL ON FUNCTION m50_opprett_sak(
    TEXT, UUID, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- POSTEN OG PERSONENE SKRIVES I SAMME SETNING.
--
-- MODULENS SKARPESTE DØR, og formen er M-52s (122): forslaget og
-- grunnene der, posten og personene her.
--
-- Hadde personene vært et eget kall etterpå, ville en journalpost med
-- navngitte privatpersoner EKSISTERT i vinduet mellom de to — uten
-- slettefrister. En sveip som kjørte i det vinduet ville sett en post
-- uten personer, altså ingenting å rydde, mens navnene lå der.
--
-- TRE NEKT:
--
--   1. INGEN POST UTEN KRAV. Uten tenantens grenser finnes det ingen
--      maksimal oppbevaringstid å måle slettefristen mot.
--
--   2. INGEN POST MOT EN AVVIKLET KILDEVERSJON. Arkivet tar imot den;
--      en NY post lest i et format som er lagt om ville vært en
--      registrering der feltene kan bety noe annet enn de gjorde.
--
--   3. INGEN SLETTEFRIST LENGER ENN TENANTENS TAK. En frist på ti år
--      i et register med ett års tak er ikke en plan, det er en
--      omgåelse av planen.
-- ---------------------------------------------------------------------
CREATE FUNCTION m50_registrer_post(
    p_tenant TEXT, p_post_id UUID, p_sak_id UUID, p_kilde_id UUID,
    p_journalnummer TEXT, p_journaldato DATE, p_tittel TEXT,
    p_formaal TEXT, p_hentet_av TEXT, p_hentet_dato DATE,
    p_person_navn TEXT[], p_person_roller TEXT[],
    p_person_frister DATE[], p_aktor TEXT)
RETURNS TABLE (post_id UUID, antall_personer INT, organ TEXT,
               format TEXT, kildeversjon TEXT, kravversjon INT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_kravversjon INT;
    v_maks INT;
    v_organ TEXT;
    v_format TEXT;
    v_ver TEXT;
    v_fra DATE;
    v_til DATE;
    v_n INT;
    v_frist DATE;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant,
        'm50_registrer_post');

    -- 1: INGEN POST UTEN KRAV.
    SELECT k.versjon, k.sletteplan_maks_dogn
      INTO v_kravversjon, v_maks
      FROM public.journalkrav k WHERE k.tenant = p_tenant;
    IF v_kravversjon IS NULL THEN
        RAISE EXCEPTION 'm50_registrer_post: tenanten har ingen'
            ' oppbevaringsgrenser. Uten dem finnes det ingen maksimal'
            ' oppbevaringstid å måle slettefristen mot — og en'
            ' personopplysning uten en plan noen har bestemt er'
            ' nettopp det modulen finnes for å hindre'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT k.organ, k.format, k.versjon, k.gyldig_fra, k.gyldig_til
      INTO v_organ, v_format, v_ver, v_fra, v_til
      FROM public.journalkilde k
     WHERE k.tenant = p_tenant AND k.kilde_id = p_kilde_id;
    IF v_organ IS NULL THEN
        RAISE EXCEPTION 'm50_registrer_post: ukjent kilde %',
            p_kilde_id USING ERRCODE = 'no_data_found';
    END IF;

    -- 2: INGEN NY POST MOT EN AVVIKLET KILDEVERSJON.
    IF NOT public.m50_kilde_gyldig(v_fra, v_til) THEN
        RAISE EXCEPTION 'm50_registrer_post: kilden % % % gjelder ikke'
            ' i dag (% – %). Arkivet tar imot den; en NY post lest i'
            ' et format som er lagt om ville vært en registrering der'
            ' feltene kan bety noe annet enn de gjorde',
            v_organ, v_format, v_ver, v_fra,
            coalesce(v_til::text, 'åpen')
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- LISTENE MÅ VÆRE LIKE LANGE, OG INGEN AV DEM NULL.
    --
    -- `cardinality(NULL)` ER NULL (122s CodeRabbit-funn, lært der og
    -- anvendt her): var én liste NULL, ble sammenligningen NULL —
    -- altså ikke SANN — og vakten slo ikke til. Da ville `unnest` gitt
    -- NULL RADER, og posten stått der med navn som aldri ble
    -- registrert med en frist.
    IF p_person_navn IS NULL OR p_person_roller IS NULL
       OR p_person_frister IS NULL THEN
        RAISE EXCEPTION 'm50_registrer_post: en av personlistene er'
            ' NULL (navn %, roller %, frister %). Tre lister av samme'
            ' lengde er invarianten',
            (p_person_navn IS NULL), (p_person_roller IS NULL),
            (p_person_frister IS NULL)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF cardinality(p_person_roller) <> cardinality(p_person_navn)
       OR cardinality(p_person_frister) <> cardinality(p_person_navn)
    THEN
        RAISE EXCEPTION 'm50_registrer_post: personlistene har ulik'
            ' lengde (%, %, %)', cardinality(p_person_navn),
            cardinality(p_person_roller),
            cardinality(p_person_frister)
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- …OG INGEN AV NAVNENE ER TOMME. `btrim` på et blankt navn gir
    -- en tom streng, og raden ville stått der som en person uten navn
    -- — altså sett anonymisert ut uten å være det, med et
    -- anonymiseringsspor som mangler (CodeRabbit).
    IF EXISTS (SELECT 1 FROM unnest(p_person_navn) AS n
                WHERE n IS NULL OR btrim(n) = '') THEN
        RAISE EXCEPTION 'm50_registrer_post: et personnavn er tomt.'
            ' En rad uten navn ser anonymisert ut uten å være det'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- 3: INGEN SLETTEFRIST LENGER ENN TENANTENS TAK.
    SELECT max(f) INTO v_frist FROM unnest(p_person_frister) AS f;
    IF v_frist IS NOT NULL
       AND v_frist > current_date + make_interval(days => v_maks) THEN
        RAISE EXCEPTION 'm50_registrer_post: en slettefrist (%) ligger'
            ' lenger fram enn tenantens tak på % døgn. En frist utover'
            ' taket er ikke en plan — det er en omgåelse av planen',
            v_frist, v_maks USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- POSTEN OG PERSONENE I SAMME SETNING (122s form).
    --
    -- Data-modifiserende CTE-er kjører BARE hvis de refereres, og
    -- rekkefølgen tvinges av at `g` leser `p`: uten det kunne
    -- personene forsøkt skrevet før posten de peker på.
    WITH p AS (
        INSERT INTO public.journalpost
            (tenant, post_id, sak_id, kilde_id,
             organ_ved_registrering, format_ved_registrering,
             kildeversjon_ved_registrering, journalnummer,
             journaldato, dokumenttittel, formaal, hentet_av_person,
             hentet_dato, kravversjon, registrert_av)
        VALUES (p_tenant, p_post_id, p_sak_id, p_kilde_id, v_organ,
                v_format, v_ver, btrim(p_journalnummer),
                p_journaldato, btrim(p_tittel), btrim(p_formaal),
                btrim(p_hentet_av), p_hentet_dato, v_kravversjon,
                p_aktor)
        RETURNING 1),
    g AS (
        INSERT INTO public.journalperson
            (tenant, person_id, post_id, navn, rolle, slettefrist,
             kravversjon, registrert_av)
        SELECT p_tenant, gen_random_uuid(), p_post_id,
               btrim(n.navn), r.rolle, f.frist, v_kravversjon, p_aktor
          FROM unnest(p_person_navn) WITH ORDINALITY AS n(navn, i)
          JOIN unnest(p_person_roller) WITH ORDINALITY AS r(rolle, i)
            ON r.i = n.i
          JOIN unnest(p_person_frister) WITH ORDINALITY AS f(frist, i)
            ON f.i = n.i
          CROSS JOIN (SELECT count(*) FROM p) pp(n)
        RETURNING 1)
    SELECT gg.n INTO v_n FROM (SELECT count(*)::int AS n FROM g) gg;

    -- ANDRE GJERDE: antallet SKREVNE personer måles mot det lovede.
    -- Vaktene over er argumenter om hva som ikke KAN skje; dette er en
    -- måling av hva som SKJEDDE. Slår den til, rulles hele setningen
    -- tilbake og posten finnes ikke — som er hele poenget med at de
    -- deler én setning.
    IF v_n IS DISTINCT FROM cardinality(p_person_navn) THEN
        RAISE EXCEPTION 'm50_registrer_post: % personer ble lovet, %'
            ' ble skrevet', cardinality(p_person_navn), v_n
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    PERFORM public.m50_evidens(p_tenant, p_sak_id, 'post_registrert',
        p_aktor, jsonb_build_object(
            'post_id', p_post_id::text, 'kilde_id', p_kilde_id::text,
            'journalnummer', btrim(p_journalnummer),
            'formaal', btrim(p_formaal),
            'hentet_av_person', btrim(p_hentet_av),
            'antall_personer', v_n, 'kildeversjon', v_ver));

    RETURN QUERY
    SELECT j.post_id, v_n, j.organ_ved_registrering,
           j.format_ved_registrering,
           j.kildeversjon_ved_registrering, j.kravversjon
      FROM public.journalpost j
     WHERE j.tenant = p_tenant AND j.post_id = p_post_id;
END $$;
REVOKE ALL ON FUNCTION m50_registrer_post(
    TEXT, UUID, UUID, UUID, TEXT, DATE, TEXT, TEXT, TEXT, DATE,
    TEXT[], TEXT[], DATE[], TEXT) FROM PUBLIC;

-- ---------------------------------------------------------------------
-- ANONYMISERINGEN — HANDLINGEN SOM LUKKER MODULENS EGET FUNN.
--
-- IKKE `DELETE`, OG DET ER EN DOM: at vi HAR oppbevart noen skal
-- fortsatt kunne leses av den som spør — men uten navnet. Raden blir et
-- spor av en behandling, ikke en person. Sletting av selve raden ville
-- fjernet beviset på at vi hadde den, og da kunne ingen etterprøve at
-- vi ryddet.
--
-- DEN ER IDEMPOTENT PÅ EN ALT ANONYMISERT RAD: en gjentatt kjøring
-- skal ikke feile, for da ville et menneske som trykket to ganger fått
-- en feilmelding om noe som faktisk er i orden.
-- ---------------------------------------------------------------------
CREATE FUNCTION m50_anonymiser(p_tenant TEXT, p_person_id UUID,
                               p_aktor TEXT)
RETURNS TABLE (person_id UUID, anonymisert BOOLEAN,
               var_alt_anonymisert BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_post UUID;
    v_alt TIMESTAMPTZ;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm50_anonymiser');
    SELECT p.post_id, p.anonymisert_ts INTO v_post, v_alt
      FROM public.journalperson p
     WHERE p.tenant = p_tenant AND p.person_id = p_person_id
       FOR UPDATE;
    IF v_post IS NULL THEN
        RAISE EXCEPTION 'm50_anonymiser: ukjent person %', p_person_id
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_alt IS NOT NULL THEN
        RETURN QUERY SELECT p_person_id, true, true;
        RETURN;
    END IF;

    UPDATE public.journalperson p
       SET navn = NULL, anonymisert_ts = now(),
           anonymisert_av = p_aktor
     WHERE p.tenant = p_tenant AND p.person_id = p_person_id;

    -- NAVNET STÅR IKKE I EVIDENSEN. Å skrive det ned i revisjonsloggen
    -- i det øyeblikket vi sletter det ville vært å flytte
    -- opplysningen, ikke å fjerne den.
    PERFORM public.m50_evidens(p_tenant, NULL, 'person_anonymisert',
        p_aktor, jsonb_build_object('person_id', p_person_id::text,
                                    'post_id', v_post::text));

    RETURN QUERY SELECT p_person_id, true, false;
END $$;
REVOKE ALL ON FUNCTION m50_anonymiser(TEXT, UUID, TEXT) FROM PUBLIC;

-- Å LUKKE ET FUNN — OG DE TO SOM IKKE KAN LUKKES.
CREATE FUNCTION m50_lukk_funn(p_tenant TEXT, p_funn_id UUID,
                              p_notat TEXT, p_aktor TEXT)
RETURNS TABLE (funn_id UUID, apen BOOLEAN)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_type TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm50_lukk_funn');
    IF p_notat IS NULL OR length(btrim(p_notat)) < 4 THEN
        RAISE EXCEPTION 'm50_lukk_funn: lukkingen krever et notat'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT f.funntype INTO v_type FROM public.journalfunn f
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id
       FOR UPDATE;
    IF v_type IS NULL THEN
        RAISE EXCEPTION 'm50_lukk_funn: ukjent funn %', p_funn_id
            USING ERRCODE = 'no_data_found';
    END IF;

    IF public.m50_funn_er_sveipens(v_type) THEN
        RAISE EXCEPTION 'm50_lukk_funn: % kan ikke lukkes for hånd.'
            ' Det forsvinner når tilstanden er borte — posten'
            ' registrert på nytt mot gjeldende kildeversjon, eller'
            ' personopplysningen faktisk anonymisert. Det er en'
            ' handling, ikke en mening', v_type
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE public.journalfunn f
       SET apen = false, lukket_ts = now(), lukket_av = p_aktor,
           lukkenotat = btrim(p_notat)
     WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;

    PERFORM public.m50_evidens(p_tenant, NULL, 'funn_lukket', p_aktor,
        jsonb_build_object('funn_id', p_funn_id::text,
                           'funntype', v_type,
                           'notat', btrim(p_notat)));

    RETURN QUERY SELECT f.funn_id, f.apen FROM public.journalfunn f
                  WHERE f.tenant = p_tenant AND f.funn_id = p_funn_id;
END $$;
REVOKE ALL ON FUNCTION m50_lukk_funn(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;

-- =====================================================================
-- SVEIPEN.
--
-- TENANTLISTA ER BEGGE REGISTRENE (122s CodeRabbit-funn, lært der og
-- anvendt uten å måtte finnes på nytt): en tenant som har poster men
-- ingen kilder — eller omvendt — skal ikke hoppes over. Han er nettopp
-- den som har konfigurert halvveis.
-- =====================================================================
CREATE FUNCTION m50_sveip_postjournal(p_maks_tenanter INT)
RETURNS TABLE (tenanter INT, nye BIGINT, oppdaterte BIGINT,
               lukkede BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE
    v_tenanter TEXT[];
    v_t TEXT;
    v_antall INT := 0;
    v_nye BIGINT := 0;
    v_oppdaterte BIGINT := 0;
    v_lukket BIGINT := 0;
    v_n BIGINT;
    v_n2 BIGINT;
BEGIN
    IF p_maks_tenanter IS NULL OR p_maks_tenanter < 1 THEN
        RAISE EXCEPTION 'm50_sveip_postjournal: maks_tenanter må være'
            ' minst 1 (%)', p_maks_tenanter
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM set_config('disponit.tenant', '', true);

    -- MATERIALISERT FØR LØKKA (klynge 6s lærdom om den late markøren).
    SELECT array_agg(DISTINCT t ORDER BY t) INTO v_tenanter
      FROM (SELECT k.tenant AS t FROM public.journalkilde k
            UNION
            SELECT p.tenant FROM public.journalpost p) s;
    IF v_tenanter IS NULL THEN
        RETURN QUERY SELECT 0, 0::bigint, 0::bigint, 0::bigint;
        RETURN;
    END IF;
    IF cardinality(v_tenanter) > p_maks_tenanter THEN
        v_tenanter := v_tenanter[1:p_maks_tenanter];
    END IF;

    FOREACH v_t IN ARRAY v_tenanter
    LOOP
        v_antall := v_antall + 1;
        PERFORM set_config('disponit.tenant', v_t, true);

        -- KILDENIVÅET.
        WITH krav AS (
            SELECT k.kildevarsel_dogn, k.versjon
              FROM public.journalkrav k WHERE k.tenant = v_t),
        kand AS (
            -- INGEN CROSS JOIN krav (121s funn): funnet handler om at
            -- kravet MANGLER.
            SELECT k.kilde_id, 'ingen_krav'::text AS funntype,
                   NULL::int AS over_grense,
                   'oppbevaringsgrensene er tenantens og er ikke'
                   || ' satt'::text AS detalj,
                   NULL::int AS kravversjon
              FROM public.journalkilde k
             WHERE k.tenant = v_t AND NOT EXISTS (SELECT 1 FROM krav)
            UNION ALL
            SELECT k.kilde_id, 'kilde_utlopt',
                   (current_date - k.gyldig_til),
                   k.organ || ' ' || k.format || ' ' || k.versjon,
                   NULL
              FROM public.journalkilde k
             WHERE k.tenant = v_t AND k.gyldig_til IS NOT NULL
               AND k.gyldig_til < current_date
               AND NOT EXISTS (
                   SELECT 1 FROM public.journalkilde k2
                    WHERE k2.tenant = v_t AND k2.organ = k.organ
                      AND k2.format = k.format
                      AND public.m50_kilde_gyldig(k2.gyldig_fra,
                                                  k2.gyldig_til))
            UNION ALL
            SELECT k.kilde_id, 'kilde_utloper_snart',
                   (k.gyldig_til - current_date),
                   k.organ || ' ' || k.format || ' ' || k.versjon,
                   kr.versjon
              FROM public.journalkilde k CROSS JOIN krav kr
             WHERE k.tenant = v_t AND k.gyldig_til IS NOT NULL
               AND k.gyldig_til >= current_date
               AND k.gyldig_til <= current_date
                   + make_interval(days => kr.kildevarsel_dogn)
        ),
        skrevet AS (
            INSERT INTO public.journalfunn
                (tenant, funn_id, kilde_id, funntype, over_grense,
                 detalj, kravversjon)
            SELECT v_t, gen_random_uuid(), k.kilde_id, k.funntype,
                   k.over_grense, k.detalj, k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, kilde_id, funntype)
                WHERE kilde_id IS NOT NULL
            -- ET MENNESKES LUKKING SKAL STÅ (CodeRabbit).
            --
            -- Med `apen = true` ubetinget gjenåpnet sveipen HVER NATT
            -- et funn noen hadde lukket, mens tilstanden var uendret.
            -- «Jeg har sett den, den skal forlenges» ble da borte til
            -- neste morgen, og lukkeknappen var pynt.
            --
            -- SVEIPENS EGEN LUKKING GJENÅPNES DERIMOT: den betyr
            -- «tilstanden var borte», og er tilstanden tilbake, er
            -- funnet tilbake. Skillet står på `lukket_av`.
            --
            -- Blir tilstanden VERRE, er det en annen funntype og
            -- dermed en annen rad — et lukket «nærmer seg» skjuler
            -- ikke et «passert».
            DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                detalj = EXCLUDED.detalj,
                kravversjon = EXCLUDED.kravversjon,
                sist_sett_sveip = now(),
                apen = (public.journalfunn.apen
                        OR public.journalfunn.lukket_av = 'm50_sveip'),
                lukket_ts = CASE
                    WHEN public.journalfunn.apen
                      OR public.journalfunn.lukket_av = 'm50_sveip'
                    THEN NULL ELSE public.journalfunn.lukket_ts END,
                lukket_av = CASE
                    WHEN public.journalfunn.apen
                      OR public.journalfunn.lukket_av = 'm50_sveip'
                    THEN NULL ELSE public.journalfunn.lukket_av END,
                lukkenotat = CASE
                    WHEN public.journalfunn.apen
                      OR public.journalfunn.lukket_av = 'm50_sveip'
                    THEN NULL ELSE public.journalfunn.lukkenotat END
            RETURNING (xmax = 0) AS var_ny)
        SELECT count(*) FILTER (WHERE var_ny),
               count(*) FILTER (WHERE NOT var_ny)
          INTO v_n, v_n2
          FROM skrevet;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);

        -- POSTNIVÅET: klyngens funn.
        --
        -- INGEN ETTERFØLGER-UNNTAK (123s lærdom, funnet av min egen
        -- port der): på kildenivået er unntaket riktig, men på
        -- postnivået ville funnet forsvunnet i det øyeblikket noen
        -- registrerte en NY kildeversjon — mens den gamle posten
        -- fortsatt var lest i det gamle formatet.
        WITH kand AS (
            SELECT p.post_id,
                   (current_date - k.gyldig_til) AS over_grense,
                   (p.organ_ved_registrering || ' '
                    || p.kildeversjon_ved_registrering)::text
                       AS detalj,
                   (SELECT versjon FROM public.journalkrav
                     WHERE tenant = v_t) AS kravversjon
              FROM public.journalpost p
              JOIN public.journalkilde k
                ON k.tenant = v_t AND k.kilde_id = p.kilde_id
             WHERE p.tenant = v_t AND k.gyldig_til IS NOT NULL
               AND k.gyldig_til < current_date
        ),
        skrevet AS (
            INSERT INTO public.journalfunn
                (tenant, funn_id, post_id, funntype, over_grense,
                 detalj, kravversjon)
            SELECT v_t, gen_random_uuid(), k.post_id,
                   'post_mot_utlopt_kilde', k.over_grense, k.detalj,
                   k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, post_id, funntype)
                WHERE post_id IS NOT NULL
            -- ET MENNESKES LUKKING SKAL STÅ (CodeRabbit).
            --
            -- Med `apen = true` ubetinget gjenåpnet sveipen HVER NATT
            -- et funn noen hadde lukket, mens tilstanden var uendret.
            -- «Jeg har sett den, den skal forlenges» ble da borte til
            -- neste morgen, og lukkeknappen var pynt.
            --
            -- SVEIPENS EGEN LUKKING GJENÅPNES DERIMOT: den betyr
            -- «tilstanden var borte», og er tilstanden tilbake, er
            -- funnet tilbake. Skillet står på `lukket_av`.
            --
            -- Blir tilstanden VERRE, er det en annen funntype og
            -- dermed en annen rad — et lukket «nærmer seg» skjuler
            -- ikke et «passert».
            DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                detalj = EXCLUDED.detalj,
                kravversjon = EXCLUDED.kravversjon,
                sist_sett_sveip = now(),
                apen = (public.journalfunn.apen
                        OR public.journalfunn.lukket_av = 'm50_sveip'),
                lukket_ts = CASE
                    WHEN public.journalfunn.apen
                      OR public.journalfunn.lukket_av = 'm50_sveip'
                    THEN NULL ELSE public.journalfunn.lukket_ts END,
                lukket_av = CASE
                    WHEN public.journalfunn.apen
                      OR public.journalfunn.lukket_av = 'm50_sveip'
                    THEN NULL ELSE public.journalfunn.lukket_av END,
                lukkenotat = CASE
                    WHEN public.journalfunn.apen
                      OR public.journalfunn.lukket_av = 'm50_sveip'
                    THEN NULL ELSE public.journalfunn.lukkenotat END
            RETURNING (xmax = 0) AS var_ny)
        SELECT count(*) FILTER (WHERE var_ny),
               count(*) FILTER (WHERE NOT var_ny)
          INTO v_n, v_n2
          FROM skrevet;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);

        -- PERSONNIVÅET: MODULENS EGET FUNN.
        --
        -- BARE RADER SOM IKKE ER ANONYMISERT. En anonymisert rad har
        -- ingen personopplysning igjen å oppbevare for lenge.
        WITH krav AS (
            SELECT k.slettevarsel_dogn, k.versjon
              FROM public.journalkrav k WHERE k.tenant = v_t),
        levende AS (
            SELECT p.person_id, p.slettefrist, p.post_id
              FROM public.journalperson p
             WHERE p.tenant = v_t AND p.anonymisert_ts IS NULL),
        kand AS (
            SELECT l.person_id,
                   'slettefrist_passert'::text AS funntype,
                   (current_date - l.slettefrist) AS over_grense,
                   'oppbevart etter egen slettefrist'::text AS detalj,
                   (SELECT versjon FROM krav) AS kravversjon
              FROM levende l
             WHERE l.slettefrist < current_date
            UNION ALL
            SELECT l.person_id, 'slettefrist_naermer_seg',
                   (l.slettefrist - current_date),
                   'slettefristen naermer seg'::text, k.versjon
              FROM levende l CROSS JOIN krav k
             WHERE l.slettefrist >= current_date
               AND l.slettefrist <= current_date
                   + make_interval(days => k.slettevarsel_dogn)
        ),
        skrevet AS (
            INSERT INTO public.journalfunn
                (tenant, funn_id, person_id, funntype, over_grense,
                 detalj, kravversjon)
            SELECT v_t, gen_random_uuid(), k.person_id, k.funntype,
                   k.over_grense, k.detalj, k.kravversjon
              FROM kand k
            ON CONFLICT (tenant, person_id, funntype)
                WHERE person_id IS NOT NULL
            -- ET MENNESKES LUKKING SKAL STÅ (CodeRabbit).
            --
            -- Med `apen = true` ubetinget gjenåpnet sveipen HVER NATT
            -- et funn noen hadde lukket, mens tilstanden var uendret.
            -- «Jeg har sett den, den skal forlenges» ble da borte til
            -- neste morgen, og lukkeknappen var pynt.
            --
            -- SVEIPENS EGEN LUKKING GJENÅPNES DERIMOT: den betyr
            -- «tilstanden var borte», og er tilstanden tilbake, er
            -- funnet tilbake. Skillet står på `lukket_av`.
            --
            -- Blir tilstanden VERRE, er det en annen funntype og
            -- dermed en annen rad — et lukket «nærmer seg» skjuler
            -- ikke et «passert».
            DO UPDATE SET
                over_grense = EXCLUDED.over_grense,
                detalj = EXCLUDED.detalj,
                kravversjon = EXCLUDED.kravversjon,
                sist_sett_sveip = now(),
                apen = (public.journalfunn.apen
                        OR public.journalfunn.lukket_av = 'm50_sveip'),
                lukket_ts = CASE
                    WHEN public.journalfunn.apen
                      OR public.journalfunn.lukket_av = 'm50_sveip'
                    THEN NULL ELSE public.journalfunn.lukket_ts END,
                lukket_av = CASE
                    WHEN public.journalfunn.apen
                      OR public.journalfunn.lukket_av = 'm50_sveip'
                    THEN NULL ELSE public.journalfunn.lukket_av END,
                lukkenotat = CASE
                    WHEN public.journalfunn.apen
                      OR public.journalfunn.lukket_av = 'm50_sveip'
                    THEN NULL ELSE public.journalfunn.lukkenotat END
            RETURNING (xmax = 0) AS var_ny)
        SELECT count(*) FILTER (WHERE var_ny),
               count(*) FILTER (WHERE NOT var_ny)
          INTO v_n, v_n2
          FROM skrevet;
        v_nye := v_nye + coalesce(v_n, 0);
        v_oppdaterte := v_oppdaterte + coalesce(v_n2, 0);
    END LOOP;

    -- LUKKINGEN I EGEN RUNDE (117–123s form). Et funn som ikke lenger
    -- er sant skal ikke bli stående — men det lukkes av at TILSTANDEN
    -- er borte, ikke av at noen trykket.
    FOREACH v_t IN ARRAY v_tenanter
    LOOP
        PERFORM set_config('disponit.tenant', v_t, true);
        WITH krav AS (
            SELECT k.slettevarsel_dogn, k.kildevarsel_dogn
              FROM public.journalkrav k WHERE k.tenant = v_t),
        levende AS (
            SELECT p.person_id, p.slettefrist
              FROM public.journalperson p
             WHERE p.tenant = v_t AND p.anonymisert_ts IS NULL),
        fortsatt AS (
            SELECT k.kilde_id, NULL::uuid AS post_id,
                   NULL::uuid AS person_id,
                   'ingen_krav'::text AS funntype
              FROM public.journalkilde k
             WHERE k.tenant = v_t AND NOT EXISTS (SELECT 1 FROM krav)
            UNION ALL
            SELECT k.kilde_id, NULL, NULL, 'kilde_utlopt'
              FROM public.journalkilde k
             WHERE k.tenant = v_t AND k.gyldig_til IS NOT NULL
               AND k.gyldig_til < current_date
               AND NOT EXISTS (
                   SELECT 1 FROM public.journalkilde k2
                    WHERE k2.tenant = v_t AND k2.organ = k.organ
                      AND k2.format = k.format
                      AND public.m50_kilde_gyldig(k2.gyldig_fra,
                                                  k2.gyldig_til))
            UNION ALL
            SELECT k.kilde_id, NULL, NULL, 'kilde_utloper_snart'
              FROM public.journalkilde k CROSS JOIN krav kr
             WHERE k.tenant = v_t AND k.gyldig_til IS NOT NULL
               AND k.gyldig_til >= current_date
               AND k.gyldig_til <= current_date
                   + make_interval(days => kr.kildevarsel_dogn)
            UNION ALL
            -- SPEILER KANDIDATLISTA: ingen etterfølger-unntak her
            -- heller. Sto det, ville lukkingen fjernet nøyaktig det
            -- kandidatlista med vilje ikke slipper unna.
            SELECT NULL, p.post_id, NULL, 'post_mot_utlopt_kilde'
              FROM public.journalpost p
              JOIN public.journalkilde k
                ON k.tenant = v_t AND k.kilde_id = p.kilde_id
             WHERE p.tenant = v_t AND k.gyldig_til IS NOT NULL
               AND k.gyldig_til < current_date
            UNION ALL
            SELECT NULL, NULL, l.person_id, 'slettefrist_passert'
              FROM levende l WHERE l.slettefrist < current_date
            UNION ALL
            SELECT NULL, NULL, l.person_id, 'slettefrist_naermer_seg'
              FROM levende l CROSS JOIN krav k
             WHERE l.slettefrist >= current_date
               AND l.slettefrist <= current_date
                   + make_interval(days => k.slettevarsel_dogn)
        ),
        lukket AS (
            UPDATE public.journalfunn f
               SET apen = false, lukket_ts = now(),
                   lukket_av = 'm50_sveip',
                   lukkenotat = 'tilstanden er borte'
             WHERE f.tenant = v_t AND f.apen
               AND NOT EXISTS (
                   SELECT 1 FROM fortsatt s
                    WHERE s.funntype = f.funntype
                      AND s.kilde_id IS NOT DISTINCT FROM f.kilde_id
                      AND s.post_id IS NOT DISTINCT FROM f.post_id
                      AND s.person_id
                          IS NOT DISTINCT FROM f.person_id)
            RETURNING 1)
        SELECT count(*) INTO v_n FROM lukket;
        v_lukket := v_lukket + coalesce(v_n, 0);
    END LOOP;

    PERFORM set_config('disponit.tenant', '', true);
    RETURN QUERY SELECT v_antall, v_nye, v_oppdaterte, v_lukket;
END $$;
REVOKE ALL ON FUNCTION m50_sveip_postjournal(INT) FROM PUBLIC;

-- =====================================================================
-- LESEDØRENE.
-- =====================================================================

CREATE FUNCTION m50_bildet(p_tenant TEXT, p_maks INT)
RETURNS TABLE (saker BIGINT, poster BIGINT, personer BIGINT,
               levende_personer BIGINT, frist_passert BIGINT,
               frist_naer BIGINT, kilder BIGINT, gyldige BIGINT,
               utlopte BIGINT, apne_funn BIGINT, har_krav BOOLEAN,
               sletteplan_maks_dogn INT, slettevarsel_dogn INT,
               kildevarsel_dogn INT, kravversjon INT, vist BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm50_bildet');
    RETURN QUERY
    WITH levende AS (
        SELECT p.person_id, p.slettefrist FROM public.journalperson p
         WHERE p.tenant = p_tenant AND p.anonymisert_ts IS NULL),
    k AS (SELECT * FROM public.journalkrav WHERE tenant = p_tenant)
    SELECT (SELECT count(*) FROM public.journalsak s
             WHERE s.tenant = p_tenant),
           (SELECT count(*) FROM public.journalpost p
             WHERE p.tenant = p_tenant),
           (SELECT count(*) FROM public.journalperson p
             WHERE p.tenant = p_tenant),
           (SELECT count(*) FROM levende),
           (SELECT count(*) FROM levende l
             WHERE l.slettefrist < current_date),
           (SELECT count(*) FROM levende l CROSS JOIN k
             WHERE l.slettefrist >= current_date
               AND l.slettefrist <= current_date
                   + make_interval(days => k.slettevarsel_dogn)),
           (SELECT count(*) FROM public.journalkilde c
             WHERE c.tenant = p_tenant),
           (SELECT count(*) FROM public.journalkilde c
             WHERE c.tenant = p_tenant
               AND public.m50_kilde_gyldig(c.gyldig_fra,
                                           c.gyldig_til)),
           (SELECT count(*) FROM public.journalkilde c
             WHERE c.tenant = p_tenant AND c.gyldig_til IS NOT NULL
               AND c.gyldig_til < current_date),
           (SELECT count(*) FROM public.journalfunn f
             WHERE f.tenant = p_tenant AND f.apen),
           (SELECT count(*) > 0 FROM k),
           (SELECT k.sletteplan_maks_dogn FROM k),
           (SELECT k.slettevarsel_dogn FROM k),
           (SELECT k.kildevarsel_dogn FROM k),
           (SELECT k.versjon FROM k),
           least((SELECT count(*) FROM public.journalpost p
                   WHERE p.tenant = p_tenant), p_maks);
END $$;
REVOKE ALL ON FUNCTION m50_bildet(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m50_kildene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (kilde_id UUID, organ TEXT, organnummer TEXT,
               format TEXT, versjon TEXT, gyldig_fra DATE,
               gyldig_til DATE, gyldig_naa BOOLEAN,
               dogn_til_utlop INT, innhold_sha256 TEXT,
               kilde_url TEXT, antall_poster BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm50_kildene');
    RETURN QUERY
    SELECT k.kilde_id, k.organ, k.organnummer, k.format, k.versjon,
           k.gyldig_fra, k.gyldig_til,
           public.m50_kilde_gyldig(k.gyldig_fra, k.gyldig_til),
           CASE WHEN k.gyldig_til IS NULL THEN NULL
                ELSE (k.gyldig_til - current_date)::int END,
           k.innhold_sha256, k.kilde_url,
           (SELECT count(*) FROM public.journalpost p
             WHERE p.tenant = p_tenant AND p.kilde_id = k.kilde_id)
      FROM public.journalkilde k
     WHERE k.tenant = p_tenant
     ORDER BY k.organ, k.format, k.gyldig_fra DESC
     LIMIT p_maks;
END $$;
REVOKE ALL ON FUNCTION m50_kildene(TEXT, INT) FROM PUBLIC;

CREATE FUNCTION m50_sakene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (sak_id UUID, tittel TEXT, formaal TEXT,
               grunnlag TEXT, opprettet TIMESTAMPTZ,
               opprettet_av TEXT, antall_poster BIGINT,
               antall_personer BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm50_sakene');
    RETURN QUERY
    SELECT s.sak_id, s.tittel, s.formaal, s.grunnlag, s.opprettet,
           s.opprettet_av,
           (SELECT count(*) FROM public.journalpost p
             WHERE p.tenant = p_tenant AND p.sak_id = s.sak_id),
           (SELECT count(*) FROM public.journalperson pe
              JOIN public.journalpost p2
                ON p2.tenant = pe.tenant AND p2.post_id = pe.post_id
             WHERE pe.tenant = p_tenant AND p2.sak_id = s.sak_id
               AND pe.anonymisert_ts IS NULL)
      FROM public.journalsak s
     WHERE s.tenant = p_tenant
     ORDER BY s.opprettet DESC
     LIMIT p_maks;
END $$;
REVOKE ALL ON FUNCTION m50_sakene(TEXT, INT) FROM PUBLIC;

-- POSTENE. HVER RAD BÆRER FORMÅLET, KILDEVERSJONEN OG HVEM SOM HENTET
-- DEN. En liste uten formålet ville vært en liste over oppslag ingen
-- kan gjøre rede for.
CREATE FUNCTION m50_postene(p_tenant TEXT, p_maks INT)
RETURNS TABLE (post_id UUID, sak_id UUID, saktittel TEXT,
               journalnummer TEXT, journaldato DATE,
               dokumenttittel TEXT, formaal TEXT, organ TEXT,
               format TEXT, kildeversjon TEXT,
               kilde_gyldig_naa BOOLEAN, hentet_av_person TEXT,
               hentet_dato DATE, antall_personer BIGINT,
               antall_levende BIGINT, naermeste_slettefrist DATE,
               kravversjon INT, registrert TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm50_postene');
    RETURN QUERY
    SELECT p.post_id, p.sak_id, s.tittel, p.journalnummer,
           p.journaldato, p.dokumenttittel, p.formaal,
           p.organ_ved_registrering, p.format_ved_registrering,
           p.kildeversjon_ved_registrering,
           public.m50_kilde_gyldig(k.gyldig_fra, k.gyldig_til),
           p.hentet_av_person, p.hentet_dato,
           (SELECT count(*) FROM public.journalperson pe
             WHERE pe.tenant = p_tenant AND pe.post_id = p.post_id),
           (SELECT count(*) FROM public.journalperson pe
             WHERE pe.tenant = p_tenant AND pe.post_id = p.post_id
               AND pe.anonymisert_ts IS NULL),
           (SELECT min(pe.slettefrist) FROM public.journalperson pe
             WHERE pe.tenant = p_tenant AND pe.post_id = p.post_id
               AND pe.anonymisert_ts IS NULL),
           p.kravversjon, p.registrert
      FROM public.journalpost p
      JOIN public.journalsak s
        ON s.tenant = p.tenant AND s.sak_id = p.sak_id
      JOIN public.journalkilde k
        ON k.tenant = p.tenant AND k.kilde_id = p.kilde_id
     WHERE p.tenant = p_tenant
     -- DEN NÆRMESTE SLETTEFRISTEN FØRST, og de passerte aller først.
     -- En liste sortert på registreringstidspunkt ville begravd
     -- bruddet under alt som er i orden.
     ORDER BY (SELECT min(pe.slettefrist)
                 FROM public.journalperson pe
                WHERE pe.tenant = p_tenant
                  AND pe.post_id = p.post_id
                  AND pe.anonymisert_ts IS NULL)
              NULLS LAST, p.post_id
     LIMIT p_maks;
END $$;
REVOKE ALL ON FUNCTION m50_postene(TEXT, INT) FROM PUBLIC;

-- PERSONENE PÅ ÉN POST. NAVNET ER `NULL` ETTER ANONYMISERING, og det
-- er ikke et hull i svaret — det ER svaret: raden er et spor av en
-- behandling, ikke en person.
CREATE FUNCTION m50_personene(p_tenant TEXT, p_post_id UUID)
RETURNS TABLE (person_id UUID, navn TEXT, rolle TEXT,
               slettefrist DATE, dogn_til_slettefrist INT,
               anonymisert_ts TIMESTAMPTZ, anonymisert_av TEXT,
               registrert TIMESTAMPTZ, registrert_av TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm50_personene');
    RETURN QUERY
    SELECT p.person_id, p.navn, p.rolle, p.slettefrist,
           (p.slettefrist - current_date)::int, p.anonymisert_ts,
           p.anonymisert_av, p.registrert, p.registrert_av
      FROM public.journalperson p
     WHERE p.tenant = p_tenant AND p.post_id = p_post_id
     ORDER BY (p.anonymisert_ts IS NOT NULL), p.slettefrist,
              p.person_id;
END $$;
REVOKE ALL ON FUNCTION m50_personene(TEXT, UUID) FROM PUBLIC;

CREATE FUNCTION m50_funnene(p_tenant TEXT, p_bare_apne BOOLEAN)
RETURNS TABLE (funn_id UUID, funntype TEXT, kilde_id UUID,
               post_id UUID, person_id UUID, organ TEXT,
               kildeversjon TEXT, journalnummer TEXT, rolle TEXT,
               slettefrist DATE, over_grense INT, detalj TEXT,
               kravversjon INT, kan_lukkes BOOLEAN,
               forst_sett TIMESTAMPTZ, sist_sett_sveip TIMESTAMPTZ,
               apen BOOLEAN, lukket_ts TIMESTAMPTZ, lukket_av TEXT,
               lukkenotat TEXT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm50_funnene');
    RETURN QUERY
    SELECT f.funn_id, f.funntype, f.kilde_id, f.post_id, f.person_id,
           coalesce(k.organ, p.organ_ved_registrering,
                    pp.organ_ved_registrering),
           coalesce(k.versjon, p.kildeversjon_ved_registrering,
                    pp.kildeversjon_ved_registrering),
           coalesce(p.journalnummer, pp.journalnummer),
           pe.rolle, pe.slettefrist,
           f.over_grense, f.detalj, f.kravversjon,
           -- FLATEN SKAL VITE HVA DEN KAN TILBY, og regelen bor ÉTT
           -- sted. En kopi i klienten ville vært en andre regel.
           NOT public.m50_funn_er_sveipens(f.funntype),
           f.forst_sett, f.sist_sett_sveip, f.apen, f.lukket_ts,
           f.lukket_av, f.lukkenotat
      FROM public.journalfunn f
      LEFT JOIN public.journalkilde k
        ON k.tenant = f.tenant AND k.kilde_id = f.kilde_id
      LEFT JOIN public.journalpost p
        ON p.tenant = f.tenant AND p.post_id = f.post_id
      LEFT JOIN public.journalperson pe
        ON pe.tenant = f.tenant AND pe.person_id = f.person_id
      LEFT JOIN public.journalpost pp
        ON pp.tenant = pe.tenant AND pp.post_id = pe.post_id
     WHERE f.tenant = p_tenant
       AND (NOT p_bare_apne OR f.apen)
     -- BRUDDET FØRST. En liste sortert alfabetisk ville lagt
     -- `slettefrist_passert` under `kilde_utlopt`.
     ORDER BY (f.funntype = 'slettefrist_passert') DESC,
              f.over_grense DESC NULLS LAST, f.forst_sett;
END $$;
REVOKE ALL ON FUNCTION m50_funnene(TEXT, BOOLEAN) FROM PUBLIC;

-- =====================================================================
-- RETTIGHETENE, RADVAKTENE OG FRYSINGEN.
-- =====================================================================

RESET ROLE;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['journalkrav', 'journalkilde',
                             'journalsak', 'journalpost',
                             'journalperson', 'journalfunn']
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL'
                       ' SECURITY', t);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL'
                       ' SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolasjon ON public.%I'
                       ' USING (tenant = current_setting('
                       '''disponit.tenant'', true))'
                       ' WITH CHECK (tenant = current_setting('
                       '''disponit.tenant'', true))', t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO'
                       ' disponit_postjournal_eier', t);
    END LOOP;
END $$;

-- KRYSS-TENANT-AUTORITETEN, EKSPLISITT OG SNEVER (111s form, 112–123):
-- bare FOR SELECT, bare til eieren, bare uten tenantkontekst — og på
-- BEGGE registrene sveipens tenantliste leser (122s lærdom).
CREATE POLICY m50_sveip_tenantliste ON journalkilde
    FOR SELECT TO disponit_postjournal_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);
CREATE POLICY m50_sveip_tenantliste_post ON journalpost
    FOR SELECT TO disponit_postjournal_eier
    USING (nullif(current_setting('disponit.tenant', true), '') IS NULL);

-- HISTORIKKTABELLENE FÅR IKKE UPDATE.
--
-- `journalsak` og `journalpost` ER HELT LUKKET: et formål og et oppslag
-- kan bare OPPSTÅ, aldri endres. Det er nettopp «hvorfor hentet vi
-- dette, og i hvilket format» som skal kunne leses år senere — og et
-- formål som lot seg redigere i ettertid er ikke et formål, det er en
-- forklaring man finner på når noen spør.
REVOKE UPDATE ON public.journalsak FROM disponit_postjournal_eier;
REVOKE UPDATE ON public.journalpost FROM disponit_postjournal_eier;

-- `journalkilde` FÅR BARE ENDRE `gyldig_til` (121s dom, 122/123s form).
REVOKE UPDATE ON public.journalkilde FROM disponit_postjournal_eier;
GRANT UPDATE (gyldig_til) ON public.journalkilde
    TO disponit_postjournal_eier;

-- `journalperson` FÅR BARE ANONYMISERES.
--
-- Navnet kan TØMMES og de to anonymiseringskolonnene settes — ingenting
-- annet. Slettefristen er frosset med vilje: kunne den flyttes, ville
-- «oppbevart etter egen frist» vært et funn man kunne fjerne ved å
-- utsette fristen, altså et gjerde som forsvant når man dyttet på det.
REVOKE UPDATE ON public.journalperson FROM disponit_postjournal_eier;
GRANT UPDATE (navn, anonymisert_ts, anonymisert_av)
    ON public.journalperson TO disponit_postjournal_eier;

-- INGEN AV TABELLENE FÅR SLETTES. `DELETE` står ikke i noen GRANT over
-- — den listen er `SELECT, INSERT, UPDATE`. Det står her fordi et
-- fravær er lettere å overse enn en setning, og porten leser begge.
--
-- FOR `journalperson` ER DET EN DOM, IKKE EN VANE: at vi HAR oppbevart
-- noen skal fortsatt kunne leses. Anonymisering fjerner opplysningen;
-- sletting ville fjernet beviset på at vi hadde den.

-- RADVAKTENE. Triggerne settes av MIGRATOREN, som eier tabellene:
-- `CREATE TRIGGER` krever eierskap, og en modulrolle som kunne sette
-- dem kunne også ta dem av igjen.
CREATE FUNCTION m50_kilde_frosset()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.kilde_id IS DISTINCT FROM OLD.kilde_id
       OR NEW.organ IS DISTINCT FROM OLD.organ
       OR NEW.organnummer IS DISTINCT FROM OLD.organnummer
       OR NEW.format IS DISTINCT FROM OLD.format
       OR NEW.versjon IS DISTINCT FROM OLD.versjon
       OR NEW.gyldig_fra IS DISTINCT FROM OLD.gyldig_fra
       OR NEW.innhold_sha256 IS DISTINCT FROM OLD.innhold_sha256
       OR NEW.registrert IS DISTINCT FROM OLD.registrert
       OR NEW.registrert_av IS DISTINCT FROM OLD.registrert_av THEN
        RAISE EXCEPTION 'journalkilde: identiteten er frosset — bare'
            ' gyldig_til kan settes'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER journalkilde_frosset
    BEFORE UPDATE ON journalkilde
    FOR EACH ROW EXECUTE FUNCTION m50_kilde_frosset();

-- PERSONRADEN: bare veien FRA navn TIL anonymisert er lovlig.
--
-- Retningen er en del av dommen. En rad som kunne gå tilbake fra
-- anonymisert til navngitt ville betydd at vi hadde navnet et sted
-- likevel — og da var anonymiseringen aldri ekte.
CREATE FUNCTION m50_person_frosset()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.person_id IS DISTINCT FROM OLD.person_id
       OR NEW.post_id IS DISTINCT FROM OLD.post_id
       OR NEW.rolle IS DISTINCT FROM OLD.rolle
       OR NEW.slettefrist IS DISTINCT FROM OLD.slettefrist
       OR NEW.kravversjon IS DISTINCT FROM OLD.kravversjon
       OR NEW.registrert IS DISTINCT FROM OLD.registrert
       OR NEW.registrert_av IS DISTINCT FROM OLD.registrert_av THEN
        RAISE EXCEPTION 'journalperson: raden er frosset — bare'
            ' anonymisering er en lovlig endring'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.anonymisert_ts IS NOT NULL
       AND NEW.anonymisert_ts IS DISTINCT FROM OLD.anonymisert_ts THEN
        RAISE EXCEPTION 'journalperson: en anonymisert rad kan ikke'
            ' gjøres om igjen'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.navn IS NOT NULL AND OLD.navn IS NULL THEN
        RAISE EXCEPTION 'journalperson: et navn kan ikke settes'
            ' tilbake etter anonymisering'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER journalperson_frosset
    BEFORE UPDATE ON journalperson
    FOR EACH ROW EXECUTE FUNCTION m50_person_frosset();

-- =====================================================================
-- EXECUTE — HVEM SOM FÅR ÅPNE HVILKEN DØR.
-- =====================================================================
SET LOCAL ROLE disponit_postjournal_eier;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION m50_bildet(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m50_kildene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m50_sakene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m50_postene(TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m50_personene(TEXT, UUID)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m50_funnene(TEXT, BOOLEAN)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m50_sett_krav(TEXT, INT, INT, INT, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m50_registrer_kilde('
            'TEXT, UUID, TEXT, TEXT, TEXT, TEXT, DATE, DATE, TEXT,'
            ' TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m50_sett_gyldig_til(TEXT, UUID, DATE, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m50_opprett_sak('
            'TEXT, UUID, TEXT, TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION m50_registrer_post('
            'TEXT, UUID, UUID, UUID, TEXT, DATE, TEXT, TEXT, TEXT,'
            ' DATE, TEXT[], TEXT[], DATE[], TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m50_anonymiser(TEXT, UUID, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m50_lukk_funn(TEXT, UUID, TEXT, TEXT) TO disponit';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_postjournalsveip') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION'
            ' m50_sveip_postjournal(INT)'
            ' TO disponit_postjournalsveip';
    END IF;
END $$;

-- SVEIPEN ER IKKE KJØRETIDSROLLENS.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'REVOKE EXECUTE ON FUNCTION'
            ' m50_sveip_postjournal(INT) FROM disponit';
    END IF;
END $$;

RESET ROLE;
