-- 183 — PARTSREGISTERET: én kunde, ett sted.
--
-- EIERS ORD 11/9 (ordrett): «målet er også gjøre modulene brukervennlig,
-- fordi hele målet var i begynnelsen 100% automatisert og selvbtjent når
-- policyen er satt … Ikke gå gjennom hver modul og fylle, så hva blir
-- vitsen hvis alt må fylles manuelt». Og: «nå må fikses den ordentlig
-- slik at det blir ikke flere runder».
--
-- ÅRSAKEN, MÅLT FØR DESIGNET: det finnes ikke noe kunderegister.
-- `kunde_ref` er FRITEKST i fire tabeller (fordring, tilbud, prosjekt,
-- onboardinglop) uten en eneste fremmednøkkel, og tre moduler bærer HVER
-- SIN krypterte kontakt med hvert sitt kolonnenavn:
--
--   fordring.mottaker_{maske,kryptert,nonce,key_id,hash}
--   tilbud.{kunde_maske,kunde_kryptert,nonce_kunde,kunde_key_id,kunde_hash}
--   kampanjemottaker.kontakt_{maske,kryptert,nonce,key_id,hash} + hash_salt
--
-- En kunde lagt inn i tilbud FINNES IKKE i fordring. Det er derfor hver
-- modul spør på nytt, og derfor 25 flater har over femten felt.
--
-- HVORFOR NÅ: registeret koster 23 rader å innføre i dag (6 fordringer,
-- 6 tilbud, 1 prosjekt, 1 onboardingløp, 4 kampanjemottakere, 5
-- motpartssubjekter). Med kunder i basen koster det en datamigrering per
-- modul. Billigst nå, dyrest senere — og aldri gratis.
--
-- GJENKJENNING PÅ TVERS UTEN Å LAGRE ADRESSEN: `m57_pseudonym` (078)
-- finnes alt og er nøyaktig riktig mekanisme — deterministisk (samme
-- adresse gir samme nøkkel), ikke-reverserbar (klarteksten fantes aldri
-- i kolonnen), tenant-skopet (samme adresse er IKKE gjenkjennelig på
-- tvers av tenanter), normalisert på en pinnet form, og nøkkelen forlater
-- aldri basen. `kampanjemottaker` salter PER RAD og kan derfor ikke
-- sammenlignes med noe; registeret bruker tenantens ene nøkkel.
--
-- Navnet `m57_pseudonym` er modulens, mekanismen er plattformens. Under
-- står `tenant_pseudonym` som et NØYTRALT navn med ÉN implementasjon bak
-- — aldri en andre HMAC som kan drifte fra den første.
--
-- DØRENE ER CLAIMER-EIDE (m6-presedensen, 175–182). Registeret er
-- kryss-modul, og `disponit_m37_claimer` eier alt annet som er det. En NY
-- eierrolle ville krevd at eier kjører `oppsett-postgresql.sh` på verten
-- før deployen kan gå — en blokkering uten motytelse her.
--
-- HVA DENNE MIGRASJONEN IKKE GJØR: den rører ikke én eneste eksisterende
-- tabell. Ingen modul peker hit ennå. Koblingen skjer per modul, én PR om
-- gangen, med backfill fra modulens egne data — og de gamle kolonnene dør
-- først når ingenting leser dem.

CREATE TABLE part (
    tenant          TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    part_id         UUID NOT NULL,
    -- KUNDENS EGEN REFERANSE, og den er UNIK per tenant. Det er denne
    -- backfillen matcher på: fire tabeller bærer alt `kunde_ref` som
    -- fritekst, og uten unikhet her ville «ACME» blitt to parter.
    part_ref        TEXT NOT NULL CHECK (part_ref ~ '[^[:space:]]'),
    -- NAVNET STÅR I KLARTEKST, og det er et VALG med presedens:
    -- `tilbud.kunde_navn` er klartekst i dag, fordi et firmanavn står på
    -- tilbudet som skal ut. Kontaktpunktene under er kryptert; det er DE
    -- som er personopplysningen.
    navn            TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'),
    -- Ni siffer, eller ingenting. Formen måles her så M-48s
    -- `motpartssubjekt.organisasjonsnummer` kan møte registeret senere
    -- uten en oversetter i midten.
    orgnummer       TEXT
        CONSTRAINT part_orgnr_form CHECK (orgnummer IS NULL
                                          OR orgnummer ~ '^[0-9]{9}$'),
    parttype        TEXT NOT NULL DEFAULT 'bedrift'
        CONSTRAINT part_type_lukket CHECK (parttype IN ('bedrift', 'person')),
    aktiv           BOOLEAN NOT NULL DEFAULT true,
    -- Deaktivering er ENVEIS og etterlater et spor, som kildene i M-6:
    -- en kunde man har sluttet med, er ikke en kunde man aldri hadde.
    deaktivert_ts   TIMESTAMPTZ,
    deaktivert_av   TEXT,
    opprettet       TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av    TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    endret_ts       TIMESTAMPTZ,
    endret_av       TEXT,
    CONSTRAINT part_pk PRIMARY KEY (tenant, part_id),
    CONSTRAINT part_ref_unik UNIQUE (tenant, part_ref),
    CONSTRAINT part_deaktivering_helhet CHECK (
        (aktiv AND deaktivert_ts IS NULL AND deaktivert_av IS NULL)
        OR (NOT aktiv AND deaktivert_ts IS NOT NULL
            AND deaktivert_av IS NOT NULL AND deaktivert_av ~ '[^[:space:]]'))
);
ALTER TABLE part ENABLE ROW LEVEL SECURITY;
ALTER TABLE part FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON part
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
CREATE INDEX part_navn ON part (tenant, aktiv, navn);
CREATE INDEX part_orgnr ON part (tenant, orgnummer) WHERE orgnummer IS NOT NULL;

-- KONTAKTPUNKTENE ER EN EGEN TABELL, og det er ikke normaliseringsiver.
-- Fordringen trenger en e-postadresse å purre til, kampanjen en å sende
-- til, tilbudet en å levere til — og det er ofte SAMME kunde med ULIKE
-- adresser (fakturamottaker, daglig leder, felles postkasse). En kolonne
-- per modul i `part` ville vært de tre øyene på nytt, inne i registeret.
CREATE TABLE partkontakt (
    tenant          TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    kontakt_id      UUID NOT NULL,
    part_id         UUID NOT NULL,
    kanal           TEXT NOT NULL
        CONSTRAINT partkontakt_kanal_lukket
        CHECK (kanal IN ('epost', 'telefon')),
    -- 058-formen, ordrett som de tre modulene bruker i dag: masken vises,
    -- ciphertext bæres, nonce er 12 byte, nøkkel-id-en sier hvilken DEK.
    --
    -- NULLBARE FORDI RADEN SKAL KUNNE TØMMES (088s form, ordrett).
    -- Reaperen blanker payloaden og setter `slettet_ts`; raden består
    -- med pseudonymet, som er ikke-reverserbart og som 078 uttrykkelig
    -- lar overleve TTL-utløpet — sporet «denne parten hadde et
    -- kontaktpunkt» er ikke personopplysningen, adressen var det.
    -- Vakten under holder de fire samlet: enten alle, eller ingen, og
    -- ingen bare når raden er tømt.
    verdi_maske     TEXT CHECK (verdi_maske IS NULL
                                OR verdi_maske ~ '[^[:space:]]'),
    verdi_kryptert  BYTEA,
    verdi_nonce     BYTEA CHECK (verdi_nonce IS NULL
                                 OR octet_length(verdi_nonce) = 12),
    verdi_key_id    TEXT CHECK (verdi_key_id IS NULL
                                OR verdi_key_id ~ '[^[:space:]]'),
    -- GJENKJENNINGEN. Formen er 078s egen og uversjonert med vilje —
    -- rotasjon er forbudt der, og et versjonert prefiks her ville lovet
    -- noe nøkkelen ikke holder.
    verdi_pseudonym TEXT NOT NULL
        CONSTRAINT partkontakt_psn_form CHECK (verdi_pseudonym ~ '^psn-[0-9a-f]{64}$'),
    -- ÉN primær per kanal per part, håndhevet av indeksen under. Uten den
    -- måtte hver modul valgt selv hvilken av tre adresser den skulle
    -- bruke, og da er valget tilbake i modulen.
    primar          BOOLEAN NOT NULL DEFAULT false,
    merkelapp       TEXT CHECK (merkelapp IS NULL OR merkelapp ~ '[^[:space:]]'),
    opprettet       TIMESTAMPTZ NOT NULL DEFAULT now(),
    opprettet_av    TEXT NOT NULL CHECK (opprettet_av ~ '[^[:space:]]'),
    slettet_ts      TIMESTAMPTZ,
    slettet_av      TEXT,
    CONSTRAINT partkontakt_pk PRIMARY KEY (tenant, kontakt_id),
    CONSTRAINT partkontakt_part_fk FOREIGN KEY (tenant, part_id)
        REFERENCES part (tenant, part_id),
    CONSTRAINT partkontakt_sletting_helhet CHECK (
        (slettet_ts IS NULL AND slettet_av IS NULL)
        OR (slettet_ts IS NOT NULL AND slettet_av IS NOT NULL
            AND slettet_av ~ '[^[:space:]]')),
    -- PAYLOADEN ER HEL ELLER BORTE, og de to tilstandene er BUNDET TIL
    -- `slettet_ts`. Uten `slettet_ts IS NULL` i første gren (CodeRabbit)
    -- var en SLETTET rad med adressen i behold fullt lovlig — altså
    -- nøyaktig den tilstanden retensjonen finnes for å hindre, og den
    -- ville sett ryddet ut i enhver liste som filtrerer på `slettet_ts`.
    -- En levende rad uten ciphertext er like galt: et kontaktpunkt
    -- ingen kan bruke og ingen kan se at er borte.
    CONSTRAINT partkontakt_payload_helhet CHECK (
        (slettet_ts IS NULL
         AND verdi_maske IS NOT NULL AND verdi_kryptert IS NOT NULL
         AND verdi_nonce IS NOT NULL AND verdi_key_id IS NOT NULL)
        OR (slettet_ts IS NOT NULL
            AND verdi_maske IS NULL AND verdi_kryptert IS NULL
            AND verdi_nonce IS NULL AND verdi_key_id IS NULL))
);
ALTER TABLE partkontakt ENABLE ROW LEVEL SECURITY;
ALTER TABLE partkontakt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON partkontakt
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
-- SAMME ADRESSE TO GANGER PÅ SAMME PART ER ÉN RAD (levende rader teller).
CREATE UNIQUE INDEX partkontakt_unik ON partkontakt
    (tenant, part_id, kanal, verdi_pseudonym) WHERE slettet_ts IS NULL;
CREATE UNIQUE INDEX partkontakt_en_primar ON partkontakt
    (tenant, part_id, kanal) WHERE primar AND slettet_ts IS NULL;
-- OPPSLAG PÅ TVERS: «hvem er dette?» fra en adresse en modul alt har.
CREATE INDEX partkontakt_psn ON partkontakt (tenant, verdi_pseudonym)
    WHERE slettet_ts IS NULL;

-- ------------------------------------------------------------------
-- DØRENE. Claimer-eide, som alt annet kryss-modul (m6-presedensen).
-- ------------------------------------------------------------------
SET LOCAL ROLE disponit_m37_claimer;

-- NØYTRALT NAVN, ÉN IMPLEMENTASJON. `m57_pseudonym` bærer modulens navn
-- fordi den ble født der; mekanismen er plattformens. En egen HMAC her
-- ville vært en andre implementasjon av den samme determinismen, og to
-- implementasjoner av én nøkkel drifter før eller siden.
CREATE FUNCTION tenant_pseudonym(p_tenant TEXT, p_verdi TEXT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    -- Samme binding som resten: pseudonymet ER tenantens, og en kaller
    -- som kunne bedt om en ANNEN tenants pseudonym for en adresse den
    -- alt kjenner, kunne prøvd seg fram mot det registeret.
    PERFORM public.krev_tenantkontekst(p_tenant, 'tenant_pseudonym');
    RETURN public.m57_pseudonym(p_tenant, p_verdi);
END $$;
REVOKE ALL ON FUNCTION tenant_pseudonym(TEXT, TEXT) FROM PUBLIC;

-- Opprett eller finn. IDEMPOTENT PÅ `part_ref`, fordi det er det en
-- import kommer til å kalle tusen ganger på rad: samme referanse er
-- samme part, og en retry skal ikke føde en tvilling.
CREATE FUNCTION part_registrer(p_tenant TEXT, p_ref TEXT, p_navn TEXT,
                               p_orgnummer TEXT, p_parttype TEXT,
                               p_aktor TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'part_registrer');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'part_registrer: en part har en registrator'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- ÉN SETNING, IKKE SELECT-SÅ-INSERT (CodeRabbit). To samtidige kall
    -- med samme referanse ville begge funnet ingenting og begge forsøkt
    -- å skrive — og nøyaktig det gjør en import: den er det eneste her
    -- som kjører tusen kall etter hverandre, gjerne på nytt etter en
    -- avbrutt runde. Unikhetsbruddet ville kommet som en hard feil midt
    -- i en import brukeren trodde var idempotent.
    --
    -- `DO UPDATE` med et WHERE som bare treffer ekte endringer: en
    -- uendret rad skal ikke få nytt `endret_ts` av at importen gikk om
    -- igjen. Treffer WHERE ingenting, gir RETURNING ingen rad, og
    -- oppslaget under henter id-en. Raden er låst uansett.
    INSERT INTO public.part (tenant, part_id, part_ref, navn, orgnummer,
                             parttype, opprettet_av)
         VALUES (p_tenant, public.gen_random_uuid(), p_ref, p_navn,
                 p_orgnummer, coalesce(p_parttype, 'bedrift'), p_aktor)
    ON CONFLICT (tenant, part_ref) DO UPDATE
       SET navn = EXCLUDED.navn,
           orgnummer = coalesce(EXCLUDED.orgnummer, public.part.orgnummer),
           endret_ts = now(), endret_av = p_aktor
     WHERE public.part.navn IS DISTINCT FROM EXCLUDED.navn
        OR (EXCLUDED.orgnummer IS NOT NULL
            AND public.part.orgnummer IS DISTINCT FROM EXCLUDED.orgnummer)
    RETURNING part_id INTO v_id;
    IF v_id IS NULL THEN
        SELECT part_id INTO v_id FROM public.part
         WHERE tenant = p_tenant AND part_ref = p_ref;
    END IF;
    RETURN v_id;
END $$;
REVOKE ALL ON FUNCTION part_registrer(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC;

-- Kontaktpunktet. Klarteksten kommer ALDRI inn hit: kalleren krypterer
-- med tenantens DEK (som M-6 og M-26 gjør), og sender masken, ciphertext
-- og pseudonymet. Døra ser aldri en adresse.
CREATE FUNCTION part_sett_kontakt(p_tenant TEXT, p_part_id UUID,
                                  p_kanal TEXT, p_maske TEXT,
                                  p_kryptert BYTEA, p_nonce BYTEA,
                                  p_key_id TEXT, p_pseudonym TEXT,
                                  p_primar BOOLEAN, p_merkelapp TEXT,
                                  p_aktor TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'part_sett_kontakt');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'part_sett_kontakt: et kontaktpunkt har en kilde'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM 1 FROM public.part
     WHERE tenant = p_tenant AND part_id = p_part_id AND aktiv;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'part_sett_kontakt: parten finnes ikke eller er'
            ' deaktivert' USING ERRCODE = 'foreign_key_violation';
    END IF;
    -- Samme atomiske form som over: adressen er enten der, eller den
    -- skrives — aldri «sjekk, så skriv» med et vindu imellom.
    -- Konfliktmålet må nevne indeksens predikat, fordi unikheten bare
    -- gjelder LEVENDE rader (en slettet adresse skal kunne legges inn
    -- på nytt).
    INSERT INTO public.partkontakt
        (tenant, kontakt_id, part_id, kanal, verdi_maske,
         verdi_kryptert, verdi_nonce, verdi_key_id, verdi_pseudonym,
         merkelapp, opprettet_av)
    VALUES (p_tenant, public.gen_random_uuid(), p_part_id, p_kanal,
            p_maske, p_kryptert, p_nonce, p_key_id, p_pseudonym,
            p_merkelapp, p_aktor)
    ON CONFLICT (tenant, part_id, kanal, verdi_pseudonym)
        WHERE slettet_ts IS NULL DO NOTHING
    RETURNING kontakt_id INTO v_id;
    IF v_id IS NULL THEN
        SELECT kontakt_id INTO v_id FROM public.partkontakt
         WHERE tenant = p_tenant AND part_id = p_part_id
           AND kanal = p_kanal AND verdi_pseudonym = p_pseudonym
           AND slettet_ts IS NULL;
    END IF;
    IF coalesce(p_primar, false) THEN
        -- ÉN PRIMÆR PER KANAL: den forrige trer til side i samme
        -- transaksjon, ellers feller delindeksen skrivingen.
        UPDATE public.partkontakt SET primar = false
         WHERE tenant = p_tenant AND part_id = p_part_id
           AND kanal = p_kanal AND primar AND slettet_ts IS NULL
           AND kontakt_id <> v_id;
        UPDATE public.partkontakt SET primar = true
         WHERE tenant = p_tenant AND kontakt_id = v_id;
    END IF;
    RETURN v_id;
END $$;
REVOKE ALL ON FUNCTION part_sett_kontakt(TEXT, UUID, TEXT, TEXT, BYTEA,
    BYTEA, TEXT, TEXT, BOOLEAN, TEXT, TEXT) FROM PUBLIC;

-- Deaktivering er ENVEIS og bevarer sporet.
CREATE FUNCTION part_deaktiver(p_tenant TEXT, p_part_id UUID, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_aktiv BOOLEAN;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'part_deaktiver');
    IF p_aktor IS NULL OR btrim(p_aktor) = '' THEN
        RAISE EXCEPTION 'part_deaktiver: en avvikling har en aktør'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT aktiv INTO v_aktiv FROM public.part
     WHERE tenant = p_tenant AND part_id = p_part_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'part_deaktiver: parten finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF NOT v_aktiv THEN
        RETURN false;                          -- alt avviklet: stille ja
    END IF;
    UPDATE public.part
       SET aktiv = false, deaktivert_ts = now(), deaktivert_av = p_aktor
     WHERE tenant = p_tenant AND part_id = p_part_id;
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION part_deaktiver(TEXT, UUID, TEXT) FROM PUBLIC;

-- LESEDØRA. Den gir masken, aldri ciphertext: en flate som skal VISE en
-- kunde trenger ikke adressen, og en modul som skal SENDE henter den
-- gjennom sin egen dekrypteringsvei med sitt eget scope.
CREATE FUNCTION part_liste(p_tenant TEXT, p_sok TEXT, p_grense INT)
RETURNS TABLE(part_id UUID, part_ref TEXT, navn TEXT, orgnummer TEXT,
              parttype TEXT, aktiv BOOLEAN, epost_maske TEXT,
              telefon_maske TEXT, antall_kontakter BIGINT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    -- TENANTEN BINDES TIL KONTEKSTEN, ikke til parameteret alene
    -- (102-formen). Uten denne linja reddet RLS oss likevel — men den
    -- reddet oss STILLE: et oppslag på feil tenant ga null rader, og
    -- «ingen kunder» og «feil firma» så like ut på skjermen. Målt ved å
    -- kjøre, før noen rakk å tro på det tomme svaret.
    PERFORM public.krev_tenantkontekst(p_tenant, 'part_liste');
    RETURN QUERY
    SELECT p.part_id, p.part_ref, p.navn, p.orgnummer, p.parttype, p.aktiv,
           (SELECT k.verdi_maske FROM public.partkontakt k
             WHERE k.tenant = p.tenant AND k.part_id = p.part_id
               AND k.kanal = 'epost' AND k.primar AND k.slettet_ts IS NULL),
           (SELECT k.verdi_maske FROM public.partkontakt k
             WHERE k.tenant = p.tenant AND k.part_id = p.part_id
               AND k.kanal = 'telefon' AND k.primar AND k.slettet_ts IS NULL),
           (SELECT count(*) FROM public.partkontakt k
             WHERE k.tenant = p.tenant AND k.part_id = p.part_id
               AND k.slettet_ts IS NULL)
      FROM public.part p
     WHERE p.tenant = p_tenant
       AND (p_sok IS NULL OR btrim(p_sok) = ''
            OR p.navn ILIKE '%' || p_sok || '%'
            OR p.part_ref ILIKE '%' || p_sok || '%'
            OR p.orgnummer = p_sok)
     ORDER BY p.aktiv DESC, p.navn
     LIMIT least(greatest(coalesce(p_grense, 200), 1), 1000);
END $$;
REVOKE ALL ON FUNCTION part_liste(TEXT, TEXT, INT) FROM PUBLIC;

-- «HVEM ER DETTE?» fra en adresse en modul alt har. Dette er selve
-- gjenkjenningen på tvers: M-6 har en avsender, M-23 har en mottaker,
-- M-44 har en kontakt — alle tre kan spørre registeret uten å oppgi
-- adressen i klartekst, fordi pseudonymet er det de sammenligner på.
CREATE FUNCTION part_fra_pseudonym(p_tenant TEXT, p_pseudonym TEXT)
RETURNS TABLE(part_id UUID, part_ref TEXT, navn TEXT, kanal TEXT)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'part_fra_pseudonym');
    RETURN QUERY
    SELECT p.part_id, p.part_ref, p.navn, k.kanal
      FROM public.partkontakt k
      JOIN public.part p ON p.tenant = k.tenant AND p.part_id = k.part_id
     WHERE k.tenant = p_tenant AND k.verdi_pseudonym = p_pseudonym
       AND k.slettet_ts IS NULL
     ORDER BY p.aktiv DESC, k.primar DESC
     LIMIT 1;
END $$;
REVOKE ALL ON FUNCTION part_fra_pseudonym(TEXT, TEXT) FROM PUBLIC;

-- GRANTENE GIS AV EIEREN, altså her inne (#140-læren): migrator kan ikke
-- dele ut rettigheter på en funksjon claimeren eier.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION tenant_pseudonym(TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION part_registrer(TEXT, TEXT,'
            ' TEXT, TEXT, TEXT, TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION part_sett_kontakt(TEXT, UUID,'
            ' TEXT, TEXT, BYTEA, BYTEA, TEXT, TEXT, BOOLEAN, TEXT, TEXT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION part_deaktiver(TEXT, UUID,'
            ' TEXT) TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION part_liste(TEXT, TEXT, INT)'
            ' TO disponit';
        EXECUTE 'GRANT EXECUTE ON FUNCTION part_fra_pseudonym(TEXT, TEXT)'
            ' TO disponit';
    END IF;
END $$;

RESET ROLE;

-- Claimeren EIER dørene og skriver gjennom dem, men MIGRATOR eier
-- tabellene (registermønsteret: tabell hos migrator, dør hos modulrolle).
-- Grantene må derfor gis her ute, av eieren — 179s form, ordrett.
--
-- FORCE RLS gjelder også claimeren: den er ikke tabelleier, og
-- `tenant_isolasjon` er skrevet uten `TO`, så den treffer alle roller.
-- Dørene krever tenantkontekst (`krev_tenantkontekst`), så policyen har
-- alltid en verdi å måle mot — dette er IKKE en kryss-tenant-dør.
GRANT SELECT, INSERT, UPDATE ON part TO disponit_m37_claimer;
GRANT SELECT, INSERT, UPDATE ON partkontakt TO disponit_m37_claimer;
