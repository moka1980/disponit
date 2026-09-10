-- 177 — M-5: et utkast kan forkastes (eiers funn 10/9: «når man lager en
-- mal, er det ikke mulig å endre eller slette den»).
--
-- 094 ga malversjonen to overganger: utkast → publisert og publisert →
-- tilbaketrukket. Det er riktig for en mal som ER I KRAFT: ferdige
-- dokumenter viser tilbake til versjonen de ble laget fra, så en
-- publisert mal endres aldri og slettes aldri — den etterfølges, og
-- skjules ved tilbaketrekking.
--
-- Men et UTKAST er ikke i kraft, og var det aldri. Ingen dokumenter
-- peker på det, `m5_fyll_mal` nekter det, og likevel sto det for alltid.
-- En malfamilie som fikk et feilaktig førsteutkast kunne aldri bli ryddig
-- igjen. Denne migrasjonen gir utkastet den ene overgangen det manglet:
--
--     utkast → forkastet
--
-- Formen er tilbaketrekkingens, ikke slettingens: raden består, med
-- tidspunkt og aktør, og innholdet står urørt (`malkomponent` og
-- `malfelt` er append-only, 094s doktrine). En forkastet versjon er
-- terminal — den kan verken publiseres eller gjenopplives, og
-- versjonsnummeret er brukt: neste utkast blir det NESTE nummeret, så
-- historikken viser at forsøket fantes.

ALTER TABLE malversjon
    ADD COLUMN forkastet_ts TIMESTAMPTZ,
    ADD COLUMN forkastet_av TEXT;

-- Eierrollens UPDATE på `malversjon` er KOLONNEBEGRENSET (094): den kan
-- skrive livssyklusen og ingenting annet, så identiteten er utenfor
-- rekkevidde selv for døren som eier tabellen. De to nye kolonnene
-- hører til livssyklusen — uten dette grantet ville forkastingsdøren
-- feilet med «permission denied», og API-et oversatt det til en
-- tilstandsdom som aldri fantes.
GRANT UPDATE (forkastet_ts, forkastet_av) ON malversjon
    TO disponit_mal_eier;

ALTER TABLE malversjon DROP CONSTRAINT malversjon_status_total;
ALTER TABLE malversjon ADD CONSTRAINT malversjon_status_total CHECK (
    (status = 'utkast'
        AND publisert_ts IS NULL AND publisert_av IS NULL
        AND tilbaketrukket_ts IS NULL AND tilbaketrukket_av IS NULL
        AND forkastet_ts IS NULL AND forkastet_av IS NULL)
 OR (status = 'publisert'
        AND publisert_ts IS NOT NULL AND publisert_av IS NOT NULL
        AND tilbaketrukket_ts IS NULL AND tilbaketrukket_av IS NULL
        AND forkastet_ts IS NULL AND forkastet_av IS NULL)
 OR (status = 'tilbaketrukket'
        AND publisert_ts IS NOT NULL AND publisert_av IS NOT NULL
        AND tilbaketrukket_ts IS NOT NULL AND tilbaketrukket_av IS NOT NULL
        AND forkastet_ts IS NULL AND forkastet_av IS NULL)
 -- Et forkastet utkast ble ALDRI publisert: publiseringskolonnene er
 -- tomme, og det er nettopp det som skiller det fra en tilbaketrukket.
 OR (status = 'forkastet'
        AND publisert_ts IS NULL AND publisert_av IS NULL
        AND tilbaketrukket_ts IS NULL AND tilbaketrukket_av IS NULL
        AND forkastet_ts IS NOT NULL AND forkastet_av IS NOT NULL));

-- 094 skrev statussettet INLINE på kolonnen, så PostgreSQL navnga
-- constrainten selv: `malversjon_status_check`. Den må vekk, ellers
-- står det gamle settet igjen og nekter `forkastet` uansett hva vi
-- legger til ved siden av. Det NYE settet får et navn vi eier.
ALTER TABLE malversjon DROP CONSTRAINT IF EXISTS malversjon_status_check;
ALTER TABLE malversjon DROP CONSTRAINT IF EXISTS malversjon_status_lukket;
ALTER TABLE malversjon ADD CONSTRAINT malversjon_status_lukket
    CHECK (status IN ('utkast', 'publisert', 'tilbaketrukket', 'forkastet'));

CREATE OR REPLACE FUNCTION m5_versjon_vakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'malversjon: % avvist — versjonene er'
            ' append-only (redigering = ny versjon)', TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.versjon_id IS DISTINCT FROM OLD.versjon_id
       OR NEW.familie_id IS DISTINCT FROM OLD.familie_id
       OR NEW.versjonsnr IS DISTINCT FROM OLD.versjonsnr
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av
       OR NEW.innhold_hash IS DISTINCT FROM OLD.innhold_hash THEN
        RAISE EXCEPTION 'malversjon: innholdet og identiteten er frosset'
            ' — en publisert mal endres aldri, den etterfølges'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status = 'utkast' AND NEW.status = 'publisert' THEN
        IF NEW.publisert_ts > pg_catalog.now() THEN
            RAISE EXCEPTION 'malversjon: publiseringen skjer nå, aldri'
                ' frem i tid' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.forkastet_ts IS NOT NULL OR NEW.forkastet_av IS NOT NULL THEN
            RAISE EXCEPTION 'malversjon: publiseringen skriver ikke'
                ' forkastingsmerket' USING ERRCODE = 'insufficient_privilege';
        END IF;
    ELSIF OLD.status = 'publisert' AND NEW.status = 'tilbaketrukket' THEN
        IF NEW.publisert_ts IS DISTINCT FROM OLD.publisert_ts
           OR NEW.publisert_av IS DISTINCT FROM OLD.publisert_av THEN
            RAISE EXCEPTION 'malversjon: tilbaketrekkingen skriver ikke'
                ' om publiseringen' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.tilbaketrukket_ts > pg_catalog.now() THEN
            RAISE EXCEPTION 'malversjon: tilbaketrekkingen skjer nå,'
                ' aldri frem i tid'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    -- 177: den tredje overgangen. Et utkast som aldri kom i kraft kan
    -- forkastes; raden består som spor på at forsøket fantes.
    ELSIF OLD.status = 'utkast' AND NEW.status = 'forkastet' THEN
        IF NEW.forkastet_ts > pg_catalog.now() THEN
            RAISE EXCEPTION 'malversjon: forkastingen skjer nå, aldri'
                ' frem i tid' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.publisert_ts IS NOT NULL OR NEW.publisert_av IS NOT NULL THEN
            RAISE EXCEPTION 'malversjon: et forkastet utkast ble aldri'
                ' publisert' USING ERRCODE = 'insufficient_privilege';
        END IF;
    ELSE
        -- Inkluderer status=status (en «oppdatering» som ikke er en
        -- overgang), alt ut av `tilbaketrukket` og alt ut av
        -- `forkastet`: begge er terminale, som i 079.
        RAISE EXCEPTION 'malversjon: % → % er ikke en lovlig overgang'
            ' (utkast→publisert, publisert→tilbaketrukket og'
            ' utkast→forkastet er de tre)', OLD.status, NEW.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

SET LOCAL ROLE disponit_mal_eier;

CREATE FUNCTION m5_forkast_malversjon(
    p_tenant TEXT, p_versjon_id UUID, p_aktor TEXT)
RETURNS INT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_status TEXT; v_nr INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm5_forkast_malversjon');
    SELECT v.status, v.versjonsnr INTO v_status, v_nr
      FROM public.malversjon v
     WHERE v.tenant = p_tenant AND v.versjon_id = p_versjon_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'malversjon: ukjent versjon'
            USING ERRCODE = 'no_data_found';
    END IF;
    IF v_status <> 'utkast' THEN
        RAISE EXCEPTION 'malversjon: versjonen er %, ikke utkast — en mal'
            ' som har vært i kraft trekkes tilbake, den forkastes ikke',
            v_status USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    UPDATE public.malversjon
       SET status = 'forkastet',
           forkastet_ts = pg_catalog.now(),
           forkastet_av = p_aktor
     WHERE tenant = p_tenant AND versjon_id = p_versjon_id;
    RETURN v_nr;
END $$;
REVOKE ALL ON FUNCTION m5_forkast_malversjon(TEXT, UUID, TEXT) FROM PUBLIC;

-- EXECUTE-grantet til runtime bor i `migrer.py` ved siden av de to andre
-- overgangsdørene (056/057-læren: runtime-grants bor der, på
-- `{rolle}`-form, og nullstilles og settes ved hver kjøring).

RESET ROLE;
