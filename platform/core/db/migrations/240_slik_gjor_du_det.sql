-- 240 — «SLIK GJØR DU DET»: en kort sjekkliste og lenke per plikt.
--
-- Eier 28/11: «bygg punkt 3». Fristvakten sa NÅR; kunden som ser
-- «Aksjonærregisteroppgaven — om 9 dager» for første gang, vet ikke HVA
-- hun skal gjøre eller hvor. Nå har hver plikt i katalogen noen få steg og
-- én lenke til myndighetens side for innsendingen.
--
-- EIERENS TEKST (eiers ordre: «alt må betjenes via dashboard»): stegene
-- og lenken redigeres i Pliktkatalog i plattformadmin. Startteksten under
-- er hovedveien slik myndighetene beskriver den; lenkene er kontrollert
-- (svarte 200) 28/11/2026.
--
-- EGEN TABELL, ikke kolonner på `plattform_pliktmal`: nattsveipen (238)
-- legger fristene inn på nytt hos alle firmaer når malen er ENDRET. En
-- rettet skrivefeil i en veiledning skal ikke sette det i gang.
--
-- RETTER OGSÅ en død kildelenke fra 237 (skattemelding for aksjeselskap
-- svarte 404) — bare der eieren ikke alt har rettet den selv.
-- ============================================================

CREATE TABLE plattform_pliktveiledning (
    nokkel      TEXT PRIMARY KEY
                REFERENCES plattform_pliktmal (nokkel) ON DELETE CASCADE,
    steg        TEXT[] NOT NULL DEFAULT '{}'
                CHECK (cardinality(steg) <= 8
                       AND array_position(steg, NULL) IS NULL
                       AND length(array_to_string(steg, '')) <= 2400),
    skjema_navn TEXT CHECK (skjema_navn IS NULL
                            OR (skjema_navn ~ '[^[:space:]]'
                                AND length(skjema_navn) <= 80)),
    skjema_url  TEXT CHECK (skjema_url IS NULL
                            OR (skjema_url ~ '^https://[^[:space:]]+$'
                                AND length(skjema_url) <= 500)),
    endret      TIMESTAMPTZ NOT NULL DEFAULT now(),
    endret_av   TEXT NOT NULL CHECK (endret_av ~ '[^[:space:]]'),
    CONSTRAINT pliktveiledning_lenke_hel
        CHECK ((skjema_navn IS NULL) = (skjema_url IS NULL)),
    CONSTRAINT pliktveiledning_ikke_tom
        CHECK (cardinality(steg) > 0 OR skjema_url IS NOT NULL)
);

REVOKE ALL ON TABLE plattform_pliktveiledning FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE plattform_pliktveiledning
    TO disponit_plattform_eier;

UPDATE plattform_pliktmal
   SET kilde_url = 'https://www.skatteetaten.no/bedrift-og-organisasjon/skatt/skattemelding-naringsdrivende/selskap/'
 WHERE nokkel = 'skattemelding_selskap'
   AND kilde_url = 'https://www.skatteetaten.no/bedrift-og-organisasjon/skatt/skattemelding-for-aksjeselskap/';

-- Startteksten. Bare for plikter som fortsatt finnes (eieren kan ha
-- ryddet i katalogen).
INSERT INTO plattform_pliktveiledning (nokkel, steg, skjema_navn, skjema_url,
                                       endret_av)
SELECT v.nokkel, v.steg, v.skjema_navn, v.skjema_url, 'migrasjon:240'
  FROM (VALUES
 ('mva_termin', ARRAY[
    'Gjør ferdig bokføringen for terminen.',
    'Send MVA-meldingen fra regnskapssystemet, eller logg inn hos Skatteetaten og fyll den ut der.',
    'Betal beløpet med KID-nummeret som står i kvitteringen.',
    'Skal dere ha penger til gode, trenger dere bare å sende meldingen.'],
  'Skatteetaten: merverdiavgift',
  'https://www.skatteetaten.no/bedrift-og-organisasjon/avgifter/mva/'),
 ('a_melding', ARRAY[
    'Kjør lønn for måneden.',
    'Send a-meldingen fra lønnssystemet. Uten lønnssystem kan den fylles ut direkte i Altinn.',
    'Les tilbakemeldingen i Altinn og rett feil før fristen.'],
  'Skatteetaten: a-meldingen',
  'https://www.skatteetaten.no/bedrift-og-organisasjon/arbeidsgiver/a-meldingen/'),
 ('aga_forskuddstrekk', ARRAY[
    'Finn beløpene og KID-numrene i tilbakemeldingen på a-meldingene for de to månedene.',
    'Betal forskuddstrekket og arbeidsgiveravgiften hver for seg.',
    'Forskuddstrekket betales fra skattetrekkskontoen.'],
  'Skatteetaten: frister og betaling',
  'https://www.skatteetaten.no/bedrift-og-organisasjon/arbeidsgiver/a-meldingen/frister-og-betaling-i-a-meldingen/'),
 ('forskuddsskatt_selskap', ARRAY[
    'Finn beløpet og KID-nummeret i fakturaen fra Skatteetaten. Den ligger i innboksen i Altinn.',
    'Betal innen fristen.',
    'Blir overskuddet høyere eller lavere enn antatt, kan forskuddsskatten endres hos Skatteetaten.'],
  'Skatteetaten: forskuddsskatt',
  'https://www.skatteetaten.no/bedrift-og-organisasjon/skatt/forskuddsskatt/'),
 ('forskuddsskatt_enk', ARRAY[
    'Finn beløpet og KID-nummeret i fakturaen fra Skatteetaten.',
    'Betal innen fristen.',
    'Tjener du mer eller mindre enn antatt, endrer du forskuddsskatten hos Skatteetaten.'],
  'Skatteetaten: forskuddsskatt',
  'https://www.skatteetaten.no/bedrift-og-organisasjon/skatt/forskuddsskatt/'),
 ('aksjonaerregisteroppgave', ARRAY[
    'Ha klart hvem som eier aksjene, hvor mange hver har, og hva som skjedde i løpet av året (kjøp, salg, utbytte).',
    'Logg inn hos Skatteetaten og fyll ut aksjonærregisteroppgaven.',
    'Send inn og ta vare på kvitteringen.'],
  'Skatteetaten: aksjonærregisteroppgaven',
  'https://www.skatteetaten.no/bedrift-og-organisasjon/rapportering-og-bransjer/aksjonarregisteroppgaven/'),
 ('skattemelding_selskap', ARRAY[
    'Gjør ferdig årsregnskapet.',
    'Send skattemeldingen fra regnskaps- eller årsoppgjørssystemet.',
    'Ta vare på kvitteringen.'],
  'Skatteetaten: skattemelding for selskap',
  'https://www.skatteetaten.no/bedrift-og-organisasjon/skatt/skattemelding-naringsdrivende/selskap/'),
 ('skattemelding_enk', ARRAY[
    'Gjør ferdig regnskapet for året.',
    'Logg inn hos Skatteetaten og kontroller det som er fylt ut på forhånd.',
    'Legg inn opplysningene om virksomheten og send inn.'],
  'Skatteetaten: skattemelding for næringsdrivende',
  'https://www.skatteetaten.no/bedrift-og-organisasjon/skatt/skattemelding-naringsdrivende/'),
 ('generalforsamling', ARRAY[
    'Styret innkaller aksjonærene skriftlig i god tid før møtet.',
    'Generalforsamlingen godkjenner årsregnskapet og et eventuelt utbytte.',
    'Skriv protokoll, signer den og ta vare på den.'],
  NULL, NULL),
 ('arsregnskap', ARRAY[
    'Få årsregnskapet godkjent av generalforsamlingen.',
    'Send det til Regnskapsregisteret fra årsoppgjørssystemet eller i Altinn.',
    'For sen innsending gir forsinkelsesgebyr.'],
  'Altinn: årsregnskap',
  'https://info.altinn.no/starte-og-drive/regnskap-og-revisjon/regnskap/arsregnskap/')
  ) AS v (nokkel, steg, skjema_navn, skjema_url)
 WHERE EXISTS (SELECT 1 FROM plattform_pliktmal m WHERE m.nokkel = v.nokkel);

SET LOCAL ROLE disponit_plattform_eier;

-- Åpen for lesing, som katalogen: plattformens egen tekst om offentlige
-- regler.
CREATE FUNCTION plattform_pliktveiledning()
RETURNS TABLE(nokkel TEXT, steg TEXT[], skjema_navn TEXT, skjema_url TEXT)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT v.nokkel, v.steg, v.skjema_navn, v.skjema_url
      FROM public.plattform_pliktveiledning v
     ORDER BY v.nokkel
$$;

-- Tomme steg og ingen lenke = veiledningen fjernes.
CREATE FUNCTION plattform_pliktveiledning_sett(
        p_bruker_id TEXT, p_nokkel TEXT, p_steg TEXT[], p_skjema_navn TEXT,
        p_skjema_url TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_steg TEXT[]; v_navn TEXT; v_url TEXT;
BEGIN
    PERFORM public.plattform_krev_eier(p_bruker_id,
                                       'plattform_pliktveiledning_sett');
    SELECT coalesce(array_agg(btrim(s.steg) ORDER BY s.nr), '{}')
      INTO v_steg
      FROM unnest(coalesce(p_steg, '{}')) WITH ORDINALITY AS s (steg, nr)
     WHERE btrim(coalesce(s.steg, '')) <> '';
    IF EXISTS (SELECT 1 FROM unnest(v_steg) AS x (steg)
                WHERE length(x.steg) > 300) THEN
        RAISE EXCEPTION 'plattform_pliktveiledning_sett: et steg er for langt'
            USING ERRCODE = 'check_violation';
    END IF;
    v_navn := nullif(btrim(coalesce(p_skjema_navn, '')), '');
    v_url := nullif(btrim(coalesce(p_skjema_url, '')), '');
    IF cardinality(v_steg) = 0 AND v_url IS NULL AND v_navn IS NULL THEN
        DELETE FROM public.plattform_pliktveiledning WHERE nokkel = p_nokkel;
        RETURN;
    END IF;
    INSERT INTO public.plattform_pliktveiledning
        (nokkel, steg, skjema_navn, skjema_url, endret, endret_av)
    VALUES (p_nokkel, v_steg, v_navn, v_url, now(), 'bruker:' || p_bruker_id)
    ON CONFLICT (nokkel) DO UPDATE SET
        steg = EXCLUDED.steg, skjema_navn = EXCLUDED.skjema_navn,
        skjema_url = EXCLUDED.skjema_url, endret = EXCLUDED.endret,
        endret_av = EXCLUDED.endret_av;
END $$;

REVOKE ALL ON FUNCTION plattform_pliktveiledning() FROM PUBLIC;
REVOKE ALL ON FUNCTION plattform_pliktveiledning_sett(TEXT, TEXT, TEXT[],
    TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION plattform_pliktveiledning() TO disponit;
GRANT EXECUTE ON FUNCTION plattform_pliktveiledning_sett(TEXT, TEXT, TEXT[],
    TEXT, TEXT) TO disponit;

RESET ROLE;
