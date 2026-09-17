-- 211 — M-57: SIGNATUREN OG FRIGIVELSEN BLIR REVISJONSHENDELSER.
--
-- §6 lover at signaturhendelser og frigivelser står i revisjonsloggen.
-- Målt 17/9 gjorde de det ikke: `signer_utsendingsliste` (056) skriver
-- bare `utsendingssignatur`, og frigivelsen fødes i `frigi_utsendelse`
-- og skriver bare `utsendingsfrigivelse`. Begge er varige, tenantbundne
-- rader — men ingen av dem er en hendelse MED IDENTITET i loggen §6
-- peker på, og `m57-v1`s `revisjonslogg_korrekt` kunne derfor aldri bli
-- `ja`.
--
-- EVIDENSEN ER EN TRIGGER, IKKE ET KALLSTED. Et kall kan glemmes i den
-- neste veien som skriver raden; en trigger kan ikke. Samme form som
-- `unntak_historikkforing` (003) og `oppdrag`-sporet: raden ER hendelsen,
-- og hendelsen fødes i samme transaksjon som den.
--
-- IDENTITETEN ER SIGNATARENS, også for frigivelsen. Frigivelsen fødes av
-- utsenderen (en maskin, uten medlemskap), men den er ikke maskinens
-- beslutning: 056 slipper ingen frigivelse gjennom uten en signatur på
-- nøyaktig det innholdet, og signataren er mennesket som autoriserte
-- utsendelsen. Derfor slås signataren opp fra `utsendingssignatur` —
-- fremmednøkkelen på `utsendingsfrigivelse` garanterer at raden finnes.
--
-- `aktor` skiller likevel hvem som UTFØRTE: `bruker:<bid>` for
-- signaturen (mennesket trykket), `m57-utsender` for frigivelsen
-- (maskinen handlet på menneskets signatur). Et menneske som leser
-- loggen skal kunne se forskjellen.
--
-- CHECKen på `handling` (068) er en lukket mengde og utvides her — en
-- ny hendelsestype er en kontraktsendring og hører i en migrasjon.
ALTER TABLE revisjonshendelse DROP CONSTRAINT IF EXISTS revisjonshendelse_handling_check;
ALTER TABLE revisjonshendelse ADD CONSTRAINT revisjonshendelse_handling_check
    CHECK (handling IN ('m57.blinding_avskrudd',
                        'm57.utsendingsliste_signert',
                        'm57.utsending_frigitt'));

-- TO EIERE, TO VINDUER (027-fellen): tabellene `utsendingssignatur` og
-- `utsendingsfrigivelse` er MIGRATORENS (056 lager dem før sitt
-- `SET LOCAL ROLE`), så triggerne må lages av migratoren — men
-- FUNKSJONENE må eies av claimeren, for det er den rollen som har INSERT
-- på `revisjonshendelse` (068), og `SECURITY DEFINER` gir dem den
-- rettigheten. Lages funksjonen av migratoren i stedet, feiler hver
-- eneste signatur på «permission denied for table revisjonshendelse».
SET LOCAL ROLE disponit_m37_claimer;

CREATE OR REPLACE FUNCTION m57_signatur_revisjon()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    INSERT INTO public.revisjonshendelse
        (tenant, handling, aktor, begrunnelse, bruker_id)
    VALUES (NEW.tenant, 'm57.utsendingsliste_signert',
            'bruker:' || NEW.signatar,
            'liste ' || NEW.liste_id::text || ' · innhold '
                || left(NEW.innhold_hash, 12),
            NEW.signatar);
    RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION m57_frigivelse_revisjon()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_signatar TEXT;
BEGIN
    SELECT s.signatar INTO v_signatar
      FROM public.utsendingssignatur s
     WHERE s.tenant = NEW.tenant AND s.liste_id = NEW.liste_id
       AND s.innhold_hash = NEW.innhold_hash
       AND s.utkast_serie = NEW.utkast_serie;
    IF v_signatar IS NULL THEN
        -- Uråd så lenge fremmednøkkelen står; fail-closed uansett, for en
        -- frigivelse uten identitet er nettopp det §6 forbyr.
        RAISE EXCEPTION 'm57_frigivelse_revisjon: frigivelse % mangler'
            ' signatur', NEW.frigivelse_id USING ERRCODE = 'no_data_found';
    END IF;
    INSERT INTO public.revisjonshendelse
        (tenant, handling, aktor, begrunnelse, bruker_id)
    VALUES (NEW.tenant, 'm57.utsending_frigitt', 'm57-utsender',
            'frigivelse ' || NEW.frigivelse_id::text || ' · liste '
                || NEW.liste_id::text,
            v_signatar);
    RETURN NEW;
END $$;
RESET ROLE;

-- Triggerne lages av TABELLENES eier (migratoren).
DROP TRIGGER IF EXISTS utsendingssignatur_revisjon ON utsendingssignatur;
CREATE TRIGGER utsendingssignatur_revisjon
    AFTER INSERT ON utsendingssignatur
    FOR EACH ROW EXECUTE FUNCTION m57_signatur_revisjon();

DROP TRIGGER IF EXISTS utsendingsfrigivelse_revisjon ON utsendingsfrigivelse;
CREATE TRIGGER utsendingsfrigivelse_revisjon
    AFTER INSERT ON utsendingsfrigivelse
    FOR EACH ROW EXECUTE FUNCTION m57_frigivelse_revisjon();
