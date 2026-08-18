-- ============================================================
-- 040 — `registrer_oppdragstype` er no-op på identisk innhold (Codex P1,
--       runde 16 på #91)
--
-- Registreringsfamilien i 014 har én felles kontrakt, og den er skrevet
-- ned i `deploy/staging/registrer-m-wcag-audit.py`: «Idempotent: alle
-- funksjonene er no-op på identisk innhold.» Den holdt for tre av fire:
--
--   * `installer_modul`      — `ON CONFLICT (modul_id) DO NOTHING`
--   * `registrer_kontrakt`   — finnes raden med identisk tuppel: `RETURN`;
--                              med et ANNET tuppel: «kontrakt er immutable»
--   * `registrer_release`    — samme mønster, samme to utfall
--   * `registrer_artefakttype` (036) — samme mønster, etter at 035/036
--                              ga tvillingen nøyaktig denne grenen
--
-- `registrer_oppdragstype` var den ENESTE som aldri fikk den. Den gikk
-- rett på overlappssjekken:
--
--     WHERE starts_with(p_oppdragstype, oppdragstype)
--        OR starts_with(oppdragstype, p_oppdragstype)
--
-- og en IDENTISK streng tilfredsstiller BEGGE ledd. En re-registrering av
-- akkurat den typen som alt står der, med samme eier og samme
-- kontrakt-hash, ble derfor rapportert som `unique_violation` —
-- «oppdragstype X overlapper eksisterende X». Ikke en dispatch-kollisjon:
-- raden overlapper seg selv.
--
-- HVA DET KOSTET, konkret. `fase2` i `deploy/staging/
-- wcag-staging-sjekkliste.py` kjører `registrer-m-wcag-audit.py` som
-- subprosess og feller runden på `returncode != 0`. Andre gang fase 2
-- kjøres for en modul som alt eier sin oppdragstype, exiter subprosessen
-- ulikt null og fase 2 dør på «registrering feilet» — FØR
-- `_gjenapne_modulen` og `bytt_release`. Det rammer nettopp den
-- dokumenterte gjenopprettingen etter en rød fase 9: `WCAG_RELEASE`-
-- overstyringen kan ikke åpne modulen igjen, fordi kjøringen aldri når
-- fram til reaktiveringen. Sjekklista er annonsert som idempotent; her
-- var det andre kallet garantert rødt.
--
-- FIKSEN er å gi den familiens egen gren, i familiens egen form
-- (036-tvillingen, ord for ord der det er mulig): identiteten leses
-- FØRST, under den låsen som allerede holdes.
--
--   * samme tuppel  → `RETURN` uten hendelse, som `registrer_kontrakt`
--                     og `registrer_release`. Ingen ny revisjonsrad for
--                     et arbeid som ikke ble gjort.
--   * annet tuppel  → `unique_violation`, «oppdragstype % er immutable».
--                     Raden ER immutabel (append-only-triggeren fra 014
--                     §7a), så en annen eier eller en annen kontrakt-hash
--                     på samme type må fortsatt være en hard feil — det
--                     er nettopp den eierkonflikten global entydighet
--                     finnes for.
--   * ingen rad     → overlappssjekken som før, uendret.
--
-- Overlappssemantikken er BEVISST IKKE RØRT. Tvillingen i 036 bruker
-- punktumgrense (`oppdragstype || '.'`) så `a.b.cd` ikke regnes som
-- overlapp av `a.b.c`; denne funksjonen bruker naken `starts_with` og er
-- dermed strengere. Det er en reell asymmetri, men den er ikke dette
-- funnet, og å løsne en global entydighetssjekk uforespurt hører hjemme i
-- sin egen endring. Etter denne migrasjonen er den strenge sjekken
-- uansett bare nådd når raden IKKE finnes fra før.
--
-- Ingen ny tabell, ingen ny kolonne, ingen ny parameter: `CREATE OR
-- REPLACE` av én funksjon. Rettigheter og eierskap følger med replace,
-- så REVOKE/GRANT fra 014 §9 gjentas ikke (samme som 036 gjorde for
-- `registrer_artefakttype`).
-- ============================================================

-- Funksjonen er SECURITY DEFINER og eies av `disponit_modul_eier` (014
-- definerte den under `SET LOCAL ROLE disponit_modul_eier`). Replace må
-- skje som samme eier — ellers bytter definer-identiteten, og funksjonen
-- ville kjørt med andre fullmakter enn den ble revidert med.
SET LOCAL ROLE disponit_modul_eier;

CREATE OR REPLACE FUNCTION registrer_oppdragstype(
    p_oppdragstype   TEXT,
    p_eiermodul      TEXT,
    p_kontraktversjon INT,
    p_kontrakt_hash  TEXT,
    p_aktor          TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_konflikt TEXT;
BEGIN
    -- GLOBAL lås, uendret fra 014: prefiks-overlappen er en avgjørelse om
    -- HELE registeret, og uten låsen kan `a.b.c` og `a.b.c.d` passere hver
    -- sin sjekk samtidig og begge committe. 036 måtte ta en ekstra
    -- identitetslås for tvillingen sin; her dekker den globale låsen også
    -- identitetslesingen under — den serialiserer alt som skriver til
    -- dette registeret, så to samtidige registreringer av samme type kan
    -- ikke begge passere eksistenssjekken og møtes i et PK-brudd der
    -- kontrakten lover en no-op.
    PERFORM pg_advisory_xact_lock(
        hashtextextended('modulregister:oppdragstype', 0));
    -- IDENTITETEN FØRST (Codex P1, runde 16 på #91). En identisk streng
    -- tilfredsstiller begge `starts_with`-leddene under, så uten denne
    -- grenen meldte funksjonen at raden «overlapper» seg selv. Hele det
    -- immutable tuppelet sammenlignes: en re-registrering med samme navn,
    -- men annen eier eller annen kontrakt-hash, skal IKKE rapporteres som
    -- en vellykket no-op når bindingen ikke ble anvendt.
    SELECT eiermodul, kontraktversjon, kontrakt_hash INTO r
      FROM public.oppdragstype_register WHERE oppdragstype = p_oppdragstype;
    IF FOUND THEN
        IF (r.eiermodul, r.kontraktversjon, r.kontrakt_hash)
           IS DISTINCT FROM
           (p_eiermodul, p_kontraktversjon, p_kontrakt_hash) THEN
            RAISE EXCEPTION 'oppdragstype % er immutable', p_oppdragstype
                USING ERRCODE = 'unique_violation';
        END IF;
        RETURN;                                   -- idempotent
    END IF;
    SELECT oppdragstype INTO v_konflikt FROM public.oppdragstype_register
     WHERE starts_with(p_oppdragstype, oppdragstype)
        OR starts_with(oppdragstype, p_oppdragstype)
     LIMIT 1;
    IF v_konflikt IS NOT NULL THEN
        RAISE EXCEPTION 'oppdragstype % overlapper eksisterende %',
            p_oppdragstype, v_konflikt USING ERRCODE = 'unique_violation';
    END IF;
    INSERT INTO public.oppdragstype_register
        (oppdragstype, eiermodul, kontraktversjon, kontrakt_hash)
        VALUES (p_oppdragstype, p_eiermodul, p_kontraktversjon, p_kontrakt_hash);
    INSERT INTO public.modulregister_hendelse
        (modul_id, hendelse, kontraktversjon, kontrakt_hash, aktor, detalj)
        VALUES (p_eiermodul, 'oppdragstype_registrert', p_kontraktversjon,
                p_kontrakt_hash, p_aktor,
                jsonb_build_object('oppdragstype', p_oppdragstype));
END $$;

RESET ROLE;
