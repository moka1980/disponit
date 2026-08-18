-- ============================================================
-- 037 — PR-014c: migrasjonsporten for skjemaoppslaget
--
-- Hver registrert artefakttype må ha et OPPSLAGBART skjema før
-- valideringen slås på. Innholdet i porten er uendret fra 036; det som
-- endret seg er HVILKEN transaksjon den står i (Codex P1, runde 2).
--
-- Hvorfor en egen fil:
--
-- `registrer_artefakttype` har vært kallbar siden 016/035, og enhver type
-- som ble registrert PÅ EN OPPGRADERT BASE før 036 bærer en `skjema_hash`
-- uten rad i `artefaktskjema` — bindingen er en hash, ikke en
-- fremmednøkkel, så ingenting stoppet det. Fra og med 036 slår
-- `/v1/artefakt` opp skjemaet ubetinget og avviser med
-- `artefaktskjema_mangler` når det ikke finnes. En artefakttype som tok
-- imot opplastninger i går ville altså blitt fullstendig ubrukelig — og
-- ikke reparerbar bakover, siden både skjemarader og typebindinger er
-- immutable: det eneste som kan fikse den er å registrere NØYAKTIG det
-- skjemaet hashen peker på.
--
-- Innholdet kan ikke bakfylles fra basen (den har hashen, ikke skjemaet).
-- Derfor stopper migrasjonen i stedet for å gå igjennom og la
-- opplastningene begynne å feile.
--
-- Men en port som stopper må være mulig å komme forbi. Sto denne blokka i
-- 036, rullet unntaket tilbake den samme transaksjonen som oppretter
-- `artefaktskjema` OG `registrer_artefaktskjema`: oppskriften i
-- feilmeldingen var da umulig å følge, og hvert nye forsøk feilet
-- identisk. Kjøreren (`db/kjorer.py`) commiter PER FIL, så med porten her
-- står 036 igjen ferdig når 037 stopper — lageret finnes, funksjonen
-- finnes, EXECUTE er gitt til admin-rollene. Rekkefølgen ved deploy blir:
--
--   1. kjør migrasjonene; 037 stopper og NAVNGIR type + hash
--   2. `SELECT registrer_artefaktskjema(<kanonisk skjema>, <hash>, <aktør>)`
--      (eller `api.artefaktskjema.registrer`) for hver av dem
--   3. kjør migrasjonene på nytt — 037 er alt som gjenstår
--
-- Deployen stopper fortsatt: `migrer()` reiser videre, og feilen kommer
-- hos den som kan gjøre noe med den, ikke hos en modul midt i et oppdrag.
-- Forskjellen er at steg 2 nå lar seg utføre.
--
-- På en fersk base er dette en no-op: den eneste raden er 036-seeden.
-- ============================================================
DO $$
DECLARE v_mangler TEXT;
BEGIN
    SELECT string_agg(format('%s (%s)', r.artefakttype, r.skjema_hash),
                      ', ' ORDER BY r.artefakttype)
      INTO v_mangler
      FROM public.artefakttype_register r
     WHERE NOT EXISTS (SELECT 1 FROM public.artefaktskjema s
                        WHERE s.skjema_hash = r.skjema_hash);
    IF v_mangler IS NOT NULL THEN
        RAISE EXCEPTION 'artefaktskjema mangler for registrerte'
            ' artefakttyper: % — 036 er kjørt, så registrer skjemaene med'
            ' registrer_artefaktskjema(...) og kjør migrasjonen om igjen;'
            ' ellers avvises alle opplastninger for disse typene',
            v_mangler
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
END $$;
