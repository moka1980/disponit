-- 078: pseudonymnøkkelen i utsendingsfrigivelsen (#156 — eiers valg
-- 23/8 i #153, delegert myndighet)
--
-- «Evidensen frigivelsen bærer er AT en bestemt mottaker fikk én
-- utsendelse — ikke adressen.» mottaker_ref bar mottakeren i klartekst
-- og overlevde reapingen; §5 krever at kandidatens personopplysninger
-- dør ved TTL-utløp, og unikheten (idempotensen) må samtidig bestå.
-- Pseudonymnøkkelen løser begge: deterministisk (unikheten består),
-- ikke-reverserbar (klarteksten fantes aldri i kolonnen), tenant-skopet
-- (samme mottaker er IKKE gjenkjennelig på tvers av tenanter).
--
-- NØKKELHÅNDTERINGEN (dommens harde del, avgjort her):
--   * Én nøkkel per tenant, født ved første bruk (gen_random_bytes(32)),
--     i tenant_pseudonymnokkel — append-only.
--   * ROTASJON ER FORBUDT (dommens første alternativ): nøkkelen ER
--     determinismen idempotensen hviler på, og formporten '^psn-…$' er
--     uversjonert med vilje. Skulle rotasjon en dag bli nødvendig, er
--     det en NY dom med versjonert prefiks — aldri en stille UPDATE
--     (vakten under nekter den).
--   * Nøkkelen forlater ALDRI basen: bare pseudonymfunksjonen (claimer-
--     eid definer) leser den; runtime har ingenting.
--
-- NORMALISERINGEN ER PINNET: normalize(lower(btrim(mottaker)), NFC).
-- To skrivemåter av samme adresse gir samme nøkkel — ellers var
-- idempotensen tilbake der den var (#140 runde 2).
--
-- PEKEREN I 056:285-301 er hermed løst («durabel pseudonymnøkkel»-
-- armen valgt); selve 056-fila står byte-frosset — den er en KJØRT
-- migrasjon med pinnet fasit, og historikkfiler skrives aldri om.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE tenant_pseudonymnokkel (
    tenant TEXT NOT NULL PRIMARY KEY,
    nokkel BYTEA NOT NULL,
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE FUNCTION tenant_pseudonymnokkel_laas()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'tenant_pseudonymnokkel: % avvist — nøkkelen ER'
        ' determinismen idempotensen hviler på; rotasjon er en ny dom'
        ' med versjonert prefiks, aldri en mutasjon (#156)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER tenant_pseudonymnokkel_laas
    BEFORE UPDATE OR DELETE ON tenant_pseudonymnokkel
    FOR EACH ROW EXECUTE FUNCTION tenant_pseudonymnokkel_laas();
CREATE TRIGGER tenant_pseudonymnokkel_ingen_truncate
    BEFORE TRUNCATE ON tenant_pseudonymnokkel
    FOR EACH STATEMENT EXECUTE FUNCTION tenant_pseudonymnokkel_laas();
-- Nøkkelen leses av pseudonymfunksjonen alene (claimer-definer);
-- claimeren trenger SELECT + INSERT for fødselen ved første bruk.
GRANT SELECT, INSERT ON tenant_pseudonymnokkel TO disponit_m37_claimer;

SET LOCAL ROLE disponit_m37_claimer;
CREATE FUNCTION m57_pseudonym(p_tenant TEXT, p_mottaker TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_nokkel BYTEA; v_norm TEXT;
BEGIN
    IF p_mottaker IS NULL OR btrim(p_mottaker) = '' THEN
        RAISE EXCEPTION 'm57_pseudonym: tom mottaker'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Alt pseudonymt er et stille pass-through: døren kan kalles i
    -- retry-kjeder der referansen alt er skrevet om, og en dobbel HMAC
    -- ville brutt idempotensen den finnes for.
    IF p_mottaker ~ '^psn-[0-9a-f]{64}$' THEN
        RETURN p_mottaker;
    END IF;
    v_norm := normalize(lower(btrim(p_mottaker)), NFC);
    SELECT nokkel INTO v_nokkel FROM public.tenant_pseudonymnokkel
     WHERE tenant = p_tenant;
    IF v_nokkel IS NULL THEN
        INSERT INTO public.tenant_pseudonymnokkel (tenant, nokkel)
             VALUES (p_tenant, public.gen_random_bytes(32))
        ON CONFLICT (tenant) DO NOTHING;
        SELECT nokkel INTO v_nokkel FROM public.tenant_pseudonymnokkel
         WHERE tenant = p_tenant;
    END IF;
    RETURN 'psn-' || encode(public.hmac(convert_to(v_norm, 'UTF8'),
                                        v_nokkel, 'sha256'), 'hex');
END $$;
REVOKE ALL ON FUNCTION m57_pseudonym(TEXT, TEXT) FROM PUBLIC;
-- Backfillen under kjører som migrator — grantet gis av eieren her.
GRANT EXECUTE ON FUNCTION m57_pseudonym(TEXT, TEXT)
    TO disponit_migrator;

-- ------------------------------------------------------------
-- Frigivelsesdøren (056-kroppen SPEILET; pseudonymiseringen er den ene
-- diffen — alt nedstrøms opererer på pseudonymet).
CREATE OR REPLACE FUNCTION frigi_utsendelse(
    p_tenant TEXT, p_liste_id UUID, p_mottaker_ref TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE s RECORD; v_id UUID := gen_random_uuid(); v_eksisterende UUID;
        v_frigitt INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'frigi_utsendelse');
    -- PSEUDONYMNØKKELEN (#156, eiers valg 23/8): evidensen frigivelsen
    -- bærer er AT en bestemt mottaker fikk én utsendelse — aldri
    -- adressen. Døren tar imot MOTTAKEREN og skriver pseudonymet;
    -- klarteksten bor kun i kandidat_utsendingsdata og dør med
    -- reapingen. Alt under (replay-oppslag, tak, INSERT, gjenlesning)
    -- opererer på pseudonymet — idempotensen består fordi nøkkelen er
    -- deterministisk over normalisert form.
    p_mottaker_ref := public.m57_pseudonym(p_tenant, p_mottaker_ref);
    -- SNAPSHOTKRAVET (Codex på #140, runde 4). Rotårsaken bak de tre
    -- rundene på denne funksjonen: BEGGE løftene her — taket mot det
    -- signerte `antall`, og «samme mottaker gir samme id» — er utledet av
    -- en LESNING (`count(*)`, og gjenlesningen etter `ON CONFLICT DO
    -- NOTHING`), ikke av en skrivekonflikt. En lesning ser bare det
    -- transaksjonens snapshot inneholder. Advisory-låsen serialiserer
    -- UTFØRELSEN, men den friskner ikke opp et snapshot: under REPEATABLE
    -- READ tas snapshotet ved transaksjonens første setning, så to kall
    -- som begge startet før den første committet, teller begge det gamle
    -- tallet og kan begge sette inn — antall=1 gir to irreversible
    -- e-poster. Samme snapshot gjør at gjenlesningen til slutt kan bomme
    -- på vinnerens rad og returnere NULL der kontrakten lover en id.
    --
    -- READ COMMITTED tar ferskt snapshot PER setning, så både tellingen
    -- og gjenlesningen etter låsen ser vinneren.
    --
    -- ... OG SERIALIZABLE HOLDER IKKE (Cursor P1 på #140, runde 5 — svaret
    -- på spørsmålet runde 4 selv stilte i tråden, med den fallbacken som
    -- da ble varslet). Runde 4 antok at SSI redder nivået. SSI redder
    -- OVERSENDINGEN (rw-syklusen mellom tellingen og en samtidig
    -- innsetting av en ANNEN mottaker avbrytes ved COMMIT), men den redder
    -- ikke REPLAY-IDEN: to førstegangskall for SAMME mottaker gir taperen
    -- et unik-brudd som `ON CONFLICT DO NOTHING` svelger uten feil, og
    -- gjenlesningen etterpå leser fortsatt taperens EGET snapshot — der
    -- vinnerens rad ikke finnes. Funksjonen returnerer da NULL, stille,
    -- der kontrakten lover «samme mottaker → samme id», og transaksjonen
    -- kan committe fint fordi taperen aldri skrev noe.
    --
    -- Nivåkravet er derfor det snevre og ærlige: READ COMMITTED. Det er
    -- det ENESTE nivået der hver setning ser ferske data, og begge løftene
    -- her er utledet av lesninger.
    --
    -- K1: alternativet — en skrivekonfliktende teller på listeraden — ville
    -- krevd et hull i append-only-vakten (`avvis_endring`) og er ny maskin,
    -- altså egen PR. Se tråden.
    -- `read uncommitted` er med fordi PostgreSQL BEHANDLER det som READ
    -- COMMITTED (nivået finnes bare som synonym); å avvise det ville vært
    -- en falsk avvisning på en irreversibel vei.
    IF current_setting('transaction_isolation')
       NOT IN ('read committed', 'read uncommitted') THEN
        RAISE EXCEPTION 'frigi_utsendelse: krever READ COMMITTED (fikk %)'
            ' — både telleporten mot det signerte antallet og'
            ' idempotensoppslaget er utledet av LESNINGER, og et fastholdt'
            ' snapshot gjør dem blinde for samtidige frigivelser',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    -- Signaturen OG listens `antall` i samme oppslag: tallet mennesket
    -- fikk se i signaturdialogen («Dette sender N e-poster. Kan ikke
    -- angres.») bor på listeversjonen signaturen binder.
    SELECT sg.innhold_hash, sg.utkast_serie, l.antall INTO s
      FROM public.utsendingssignatur sg
      JOIN public.utsendingsliste l
        ON l.tenant = sg.tenant AND l.liste_id = sg.liste_id
     WHERE sg.tenant = p_tenant AND sg.liste_id = p_liste_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'frigi_utsendelse: liste % er ikke signert',
            p_liste_id USING ERRCODE = 'no_data_found';
    END IF;
    -- Allerede frigitt for DENNE mottakeren? Da er svaret gitt, og
    -- replayet skal aldri møte telleporten under — en liste som står på
    -- taket må fortsatt kunne svare idempotent på et retry.
    SELECT frigivelse_id INTO v_eksisterende
      FROM public.utsendingsfrigivelse
     WHERE tenant = p_tenant AND liste_id = p_liste_id
       AND mottaker_ref = p_mottaker_ref;
    IF v_eksisterende IS NOT NULL THEN
        RETURN v_eksisterende;
    END IF;
    -- DET SIGNERTE ANTALLET ER ET TAK (Codex P1, runde 2). Unikheten på
    -- (liste, mottaker) hindret bare DUBLETTER — den sa ingenting om hvor
    -- MANGE forskjellige mottakere senderen kunne frigi. En liste
    -- presentert som «N e-poster» kunne dermed gi flere enn N irreversible
    -- utsendelser, forbi til og med skjemaets 5000-grense, uten at noe
    -- menneske signerte for det. Taket er en del av signaturens løfte.
    --
    -- Serialisert med en advisory-lås per (tenant, liste) — 014s mønster.
    -- Uten den kunne to samtidige kall begge lese `antall - 1` og begge
    -- sette inn: en ren count-så-INSERT er nøyaktig det TOCTOU-et runde 1
    -- lukket andre steder i denne filen. Låsen krever ingen rettighet (i
    -- motsetning til `SELECT ... FOR UPDATE`, som ville krevd UPDATE på en
    -- append-only tabell) og faller ved commit.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'm57:frigi:' || p_tenant || ':' || p_liste_id::text, 0));
    -- GJENLES ETTER LÅSEN (Codex på #140, runde 3): to FØRSTEGANGS-kall
    -- for SAMME mottaker kan begge bomme på oppslaget over (ingen rad
    -- ennå), og taperen ville da møtt telleporten under i stedet for
    -- replay-svaret — presis når taket er lite (f.eks. antall=1) og
    -- vinneren alt har committet før taperen får låsen. Uten denne
    -- gjenlesningen fikk et helt legitimt samtidig FØRSTE forsøk et
    -- avvist svar der idempotens-kontrakten lovte samme id.
    SELECT frigivelse_id INTO v_eksisterende
      FROM public.utsendingsfrigivelse
     WHERE tenant = p_tenant AND liste_id = p_liste_id
       AND mottaker_ref = p_mottaker_ref;
    IF v_eksisterende IS NOT NULL THEN
        RETURN v_eksisterende;
    END IF;
    SELECT count(*) INTO v_frigitt FROM public.utsendingsfrigivelse
     WHERE tenant = p_tenant AND liste_id = p_liste_id;
    IF v_frigitt >= s.antall THEN
        RAISE EXCEPTION 'frigi_utsendelse: liste % er signert for %'
            ' mottakere, og % er alt frigitt — en ny mottaker krever en ny'
            ' signert versjon', p_liste_id, s.antall, v_frigitt
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Idempotent UNDER kappløp (Cursor P2 på #140): SELECT-så-INSERT lot
    -- taperen få unik-bruddet i fanget. `ON CONFLICT DO NOTHING` +
    -- gjenlesning gir begge kallerne VINNERENS id — 038s mønster
    -- (`sikre_sak_for_oppdrag`), i insert-form.
    INSERT INTO public.utsendingsfrigivelse (tenant, frigivelse_id,
        liste_id, innhold_hash, utkast_serie, mottaker_ref)
    VALUES (p_tenant, v_id, p_liste_id, s.innhold_hash, s.utkast_serie,
            p_mottaker_ref)
    ON CONFLICT (tenant, liste_id, mottaker_ref) DO NOTHING;
    SELECT frigivelse_id INTO v_eksisterende
      FROM public.utsendingsfrigivelse
     WHERE tenant = p_tenant AND liste_id = p_liste_id
       AND mottaker_ref = p_mottaker_ref;
    RETURN v_eksisterende;
END $$;
RESET ROLE;

-- ------------------------------------------------------------
-- Backfill av bebodde rader (klartekst → pseudonym, vaktene av i samme
-- transaksjon — 059/072-formen), så formporten kan stå absolutt.
ALTER TABLE utsendingsfrigivelse DISABLE TRIGGER USER;
ALTER TABLE utsendingsfrigivelse NO FORCE ROW LEVEL SECURITY;
-- KOLLISJONSPOLITIKKEN ER EKSPLISITT (CodeRabbit): to klartekstformer
-- som normaliserer til samme nøkkel i SAMME liste er to frigivelses-
-- rader for samme mottaker — evidens for to irreversible utsendelser.
-- Å slå dem sammen ville slettet evidens fra en append-only tabell;
-- politikken er derfor FAIL-CLOSED med navngitte rader: migrasjonen
-- stopper, og dubletten krever en menneskelig dom før neste kjøring.
DO $$
DECLARE v_rad RECORD; v_feil TEXT := '';
BEGIN
    FOR v_rad IN
        SELECT f.tenant, f.liste_id,
               m57_pseudonym(f.tenant, f.mottaker_ref) AS psn,
               count(*) AS antall
          FROM utsendingsfrigivelse f
         WHERE f.mottaker_ref !~ '^psn-[0-9a-f]{64}$'
         GROUP BY 1, 2, 3
        HAVING count(*) > 1
    LOOP
        v_feil := v_feil || format(' [tenant=%s liste=%s psn=%s: %s rader]',
                                   v_rad.tenant, v_rad.liste_id,
                                   left(v_rad.psn, 16) || '…',
                                   v_rad.antall);
    END LOOP;
    IF v_feil <> '' THEN
        RAISE EXCEPTION 'utsendingsfrigivelse: normaliserte dubletter i'
            ' samme liste —%s. To rader er evidens for to irreversible'
            ' utsendelser og slås ALDRI sammen maskinelt; dubletten'
            ' krever menneskelig dom (#156) før migrasjonen kjøres'
            ' igjen.', v_feil;
    END IF;
END $$;
UPDATE utsendingsfrigivelse f
   SET mottaker_ref = m57_pseudonym(f.tenant, f.mottaker_ref)
 WHERE f.mottaker_ref !~ '^psn-[0-9a-f]{64}$';
ALTER TABLE utsendingsfrigivelse FORCE ROW LEVEL SECURITY;
ALTER TABLE utsendingsfrigivelse ENABLE TRIGGER USER;

-- Formporten: en klartekstadresse kan ALDRI skrives dit igjen — heller
-- ikke via direkte DML.
ALTER TABLE utsendingsfrigivelse
    ADD CONSTRAINT mottaker_ref_er_pseudonym
    CHECK (mottaker_ref ~ '^psn-[0-9a-f]{64}$');
