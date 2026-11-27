-- 234 — LEVERANDØREN SOM DATA: hvem Disponit er, redigert i dashbordet.
--
-- Eiers ordre 27/11: «det må være et skjema for admin også slik at admin
-- kan selv legge inn/redigere slike info også, målet med hele nettstedet er
-- alt kan gjøres selvbetjent i dashboard både for admin og kunder.»
--
-- FØR: leverandørens navn, org.nr., adresse og kontakt sto i
-- `docs/juridisk/*.md` — i kode, i et OFFENTLIG repo, og bare en utvikler
-- kunne endre dem. Prøvebannerets kontaktadresse lå i en miljøvariabel på
-- verten. Nå: én rad, et skjema i plattformadmin, og vilkårssidene og
-- banneret leser herfra.
--
-- ÉN RAD (`id = 1`): plattformen har én leverandør. Den starter TOM —
-- eieren fyller den inn, og flatene sier «ikke fylt inn» til hun har gjort
-- det i stedet for å vise en gjetning.
--
-- LESINGEN ER ÅPEN, SKRIVINGEN ER EIERENS. Opplysningene er de som står
-- på vilkårssidene og i banneret — offentlige med vilje. Skrivedøra krever
-- `plattform_krev_eier` (199), samme fullmakt som firmadørene.
-- ============================================================

CREATE TABLE plattform_leverandor (
    id            INT PRIMARY KEY DEFAULT 1 CONSTRAINT leverandor_en_rad
                                            CHECK (id = 1),
    navn          TEXT NOT NULL CHECK (navn ~ '[^[:space:]]'
                                       AND length(navn) <= 200),
    orgnummer     TEXT CONSTRAINT leverandor_orgnr_form
                       CHECK (orgnummer IS NULL OR orgnummer ~ '^[0-9]{9}$'),
    adresse       TEXT CHECK (adresse IS NULL OR length(adresse) <= 300),
    kontakt_epost TEXT CONSTRAINT leverandor_kontakt_form
                       CHECK (kontakt_epost IS NULL
                              OR kontakt_epost ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
    verneting     TEXT CHECK (verneting IS NULL OR length(verneting) <= 200),
    endret        TIMESTAMPTZ NOT NULL DEFAULT now(),
    endret_av     TEXT NOT NULL CHECK (endret_av ~ '[^[:space:]]')
);
REVOKE ALL ON TABLE plattform_leverandor FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON TABLE plattform_leverandor
    TO disponit_plattform_eier;

SET LOCAL ROLE disponit_plattform_eier;

CREATE FUNCTION plattform_leverandor_les()
RETURNS TABLE(navn TEXT, orgnummer TEXT, adresse TEXT, kontakt_epost TEXT,
              verneting TEXT, endret TIMESTAMPTZ)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT l.navn, l.orgnummer, l.adresse, l.kontakt_epost, l.verneting,
           l.endret
      FROM public.plattform_leverandor l WHERE l.id = 1
$$;

CREATE FUNCTION plattform_leverandor_sett(
        p_bruker_id TEXT, p_navn TEXT, p_orgnummer TEXT, p_adresse TEXT,
        p_kontakt_epost TEXT, p_verneting TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.plattform_krev_eier(p_bruker_id,
                                       'plattform_leverandor_sett');
    INSERT INTO public.plattform_leverandor
        (id, navn, orgnummer, adresse, kontakt_epost, verneting,
         endret, endret_av)
    VALUES (1, btrim(p_navn), nullif(btrim(p_orgnummer), ''),
            nullif(btrim(p_adresse), ''), nullif(btrim(p_kontakt_epost), ''),
            nullif(btrim(p_verneting), ''), now(), 'bruker:' || p_bruker_id)
    ON CONFLICT (id) DO UPDATE SET
        navn = EXCLUDED.navn, orgnummer = EXCLUDED.orgnummer,
        adresse = EXCLUDED.adresse, kontakt_epost = EXCLUDED.kontakt_epost,
        verneting = EXCLUDED.verneting, endret = EXCLUDED.endret,
        endret_av = EXCLUDED.endret_av;
END $$;

REVOKE ALL ON FUNCTION plattform_leverandor_les() FROM PUBLIC;
REVOKE ALL ON FUNCTION plattform_leverandor_sett(TEXT, TEXT, TEXT, TEXT,
                                                  TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION plattform_leverandor_les() TO disponit;
GRANT EXECUTE ON FUNCTION plattform_leverandor_sett(TEXT, TEXT, TEXT, TEXT,
                                                     TEXT, TEXT) TO disponit;

RESET ROLE;
