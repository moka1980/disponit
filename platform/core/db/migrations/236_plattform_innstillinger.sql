-- 236 — PLATTFORMENS INNSTILLINGER: prøveperioden og bransjemalene, i
-- dashbordet.
--
-- Eiers ordre 27/11: «alt må betjenes via dashboard både for kunden og
-- admin panel, ikke gjennom koden».
--
-- FØR: prøveperiodens lengde var en konstant i `api/firmaregistrering.py`
-- (`PROVE_DOGN = 30`), og hvilke bransjemaler en ny kunde kunne velge sto
-- som en liste i samme fil og i registreringsflaten. Nå: én rad, et skjema
-- i plattformadmin, og registreringen leser herfra.
--
-- BRANSJENE ER ET UTVALG, IKKE NYE MALER. Malene er policyfiler som er
-- byte-bundet til M-02s akseptartefakt — de skrives ikke i et skjema. Eieren
-- velger HVILKE av dem som tilbys; CHECK-en holder utvalget innenfor de
-- malene som følger med appen, og minst én må stå på (en registrering uten
-- noe å velge er en stengt registrering i forkledning).
--
-- PRØVEPERIODEN: 1-365 døgn, samme grense som `firma_registrer` (190)
-- håndhever. Endringen gjelder NYE firmaer — et firma som alt er i prøve,
-- beholder fristen det fikk.
--
-- LESINGEN ER ÅPEN, SKRIVINGEN ER EIERENS — som leverandøren (234). Tallet
-- står på forsiden («Prøv gratis i N dager»), og bransjene står i
-- registreringsskjemaet.
-- ============================================================

CREATE TABLE plattform_innstilling (
    id          INT PRIMARY KEY DEFAULT 1 CONSTRAINT innstilling_en_rad
                                          CHECK (id = 1),
    prove_dogn  INT NOT NULL DEFAULT 30 CONSTRAINT innstilling_prove_dogn
                    CHECK (prove_dogn BETWEEN 1 AND 365),
    bransjer    TEXT[] NOT NULL CONSTRAINT innstilling_bransjer
                    CHECK (cardinality(bransjer) >= 1
                           AND bransjer <@ ARRAY['tjenestebedrift',
                                                 'handverk-bygg',
                                                 'netthandel']::TEXT[]),
    endret      TIMESTAMPTZ NOT NULL DEFAULT now(),
    endret_av   TEXT NOT NULL CHECK (endret_av ~ '[^[:space:]]')
);
REVOKE ALL ON TABLE plattform_innstilling FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON TABLE plattform_innstilling
    TO disponit_plattform_eier;

-- STARTVERDIENE ER DE SOM STO I KODEN, så ingenting endrer seg før eieren
-- endrer det.
INSERT INTO plattform_innstilling (id, prove_dogn, bransjer, endret_av)
VALUES (1, 30, ARRAY['tjenestebedrift', 'handverk-bygg', 'netthandel'],
        'migrasjon:236');

SET LOCAL ROLE disponit_plattform_eier;

CREATE FUNCTION plattform_innstillinger_les()
RETURNS TABLE(prove_dogn INT, bransjer TEXT[], endret TIMESTAMPTZ)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT i.prove_dogn, i.bransjer, i.endret
      FROM public.plattform_innstilling i WHERE i.id = 1
$$;

CREATE FUNCTION plattform_innstillinger_sett(
        p_bruker_id TEXT, p_prove_dogn INT, p_bransjer TEXT[])
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.plattform_krev_eier(p_bruker_id,
                                       'plattform_innstillinger_sett');
    INSERT INTO public.plattform_innstilling
        (id, prove_dogn, bransjer, endret, endret_av)
    VALUES (1, p_prove_dogn, p_bransjer, now(), 'bruker:' || p_bruker_id)
    ON CONFLICT (id) DO UPDATE SET
        prove_dogn = EXCLUDED.prove_dogn, bransjer = EXCLUDED.bransjer,
        endret = EXCLUDED.endret, endret_av = EXCLUDED.endret_av;
END $$;

REVOKE ALL ON FUNCTION plattform_innstillinger_les() FROM PUBLIC;
REVOKE ALL ON FUNCTION plattform_innstillinger_sett(TEXT, INT, TEXT[])
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION plattform_innstillinger_les() TO disponit;
GRANT EXECUTE ON FUNCTION plattform_innstillinger_sett(TEXT, INT, TEXT[])
    TO disponit;

RESET ROLE;
