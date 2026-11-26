-- 231 — M-17: svaret går fra postboksen saken kom inn på.
--
-- FUNNET 26.11.2026 (eier, første ekte prøve med Gmail): en sak som kom
-- inn på disponit.ai@gmail.com ble besvart fra post@wcagvakt.no. Kunde-
-- serviceflaten kjenner bare husets vei (163: claim → husets SMTP →
-- kvittering), mens postboksens vei (180–181, 229) bare var å finne i
-- e-postflaten. Kunden ser to adresser for samme samtale, og svaret
-- havner ikke i «Sendt» i den felles innboksen.
--
-- HVA DETTE GJØR. Har saken kommet inn på en postboks som kan sende,
-- skriver mennesket svaret i kundeservice, og det legges i postboksens
-- egen kø (`m6_skriv_svarutkast` + `m6_send_svaret`, eiervedtak 10/9:
-- menneskets eget svar fra kundens egen postboks er ingen agenthandling).
-- Denne migrasjonen gir det to ting:
--
--   * KOBLINGEN: hvilket postbokssvar som besvarer hvilken sak. Uten den
--     kan ingen si at saken er besvart, eller av hvem.
--   * UTFALLET TILBAKE TIL SAKEN: når postboksens bakgrunnsprosess har
--     sendt (eller ikke fått sendt), skal saken vite det. Det skjer i en
--     trigger på `epost_utkast`, ikke i `m6_svar_sendt` — M-6s dører står
--     ordrett, og bare et utkast som HAR en kobling berøres. Alt M-6 gjør
--     uten kundeservice, gjør det som før.
--
-- SAKEN LUKKES FØRST NÅR SVARET ER UTE. Feiler sendingen, står saken
-- åpen med en sporrad som sier hvorfor; et nytt svar kan skrives.

CREATE TABLE henvendelse_postbokssvar (
    tenant TEXT NOT NULL CHECK (length(btrim(tenant)) > 0),
    epost_utkast_id UUID NOT NULL,
    henvendelse_id UUID NOT NULL,
    svarutkast_id UUID NOT NULL,
    postboks TEXT NOT NULL CHECK (length(btrim(postboks)) BETWEEN 3 AND 254),
    sendt_av TEXT NOT NULL CHECK (length(btrim(sendt_av)) > 0),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    utfall TEXT CHECK (utfall IS NULL OR utfall IN ('sendt', 'feilet')),
    utfall_ts TIMESTAMPTZ,
    CONSTRAINT henvendelse_postbokssvar_pk PRIMARY KEY (tenant, epost_utkast_id)
);
CREATE INDEX henvendelse_postbokssvar_sak
    ON henvendelse_postbokssvar (tenant, henvendelse_id, opprettet DESC);

ALTER TABLE henvendelse_postbokssvar ENABLE ROW LEVEL SECURITY;
ALTER TABLE henvendelse_postbokssvar FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON henvendelse_postbokssvar
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

REVOKE ALL ON henvendelse_postbokssvar FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON henvendelse_postbokssvar
    TO disponit_kundeservice_eier;

SET LOCAL ROLE disponit_kundeservice_eier;

-- KOBLINGSDØRA. Kalles av API-et i SAMME transaksjon som postboksutkastet
-- skrives og settes i kø. Utkastet i registeret merkes `brukt_manuelt` —
-- et menneske sendte det, plattformen gjorde det ikke — og det er nettopp
-- det «besvart» krever (164) når saken lukkes.
CREATE FUNCTION m17_koble_postbokssvar(
    p_tenant TEXT, p_henvendelse_id UUID, p_svarutkast_id UUID,
    p_epost_utkast_id UUID, p_postboks TEXT, p_aktor TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_lukket TIMESTAMPTZ; v_hid UUID; v_status TEXT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_koble_postbokssvar');
    PERFORM set_config('disponit.aktor', p_aktor, true);
    IF p_aktor IS NULL OR btrim(p_aktor) = '' OR p_epost_utkast_id IS NULL
       OR length(btrim(coalesce(p_postboks, ''))) NOT BETWEEN 3 AND 254 THEN
        RAISE EXCEPTION 'm17_koble_postbokssvar: utkast, postboks og aktør'
            ' kreves' USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT lukket_ts INTO v_lukket FROM public.henvendelse
     WHERE tenant = p_tenant AND henvendelse_id = p_henvendelse_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'm17_koble_postbokssvar: henvendelsen finnes ikke'
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_lukket IS NOT NULL THEN
        RAISE EXCEPTION 'm17_koble_postbokssvar: henvendelsen er lukket'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    SELECT henvendelse_id, status INTO v_hid, v_status
      FROM public.svarutkast
     WHERE tenant = p_tenant AND utkast_id = p_svarutkast_id FOR UPDATE;
    IF v_hid IS NULL OR v_hid <> p_henvendelse_id THEN
        RAISE EXCEPTION 'm17_koble_postbokssvar: utkastet er ikke'
            ' henvendelsens' USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF v_status <> 'foreslatt' THEN
        RAISE EXCEPTION 'm17_koble_postbokssvar: utkastet er %', v_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    UPDATE public.svarutkast SET status = 'brukt_manuelt'
     WHERE tenant = p_tenant AND utkast_id = p_svarutkast_id;
    INSERT INTO public.henvendelse_postbokssvar
        (tenant, epost_utkast_id, henvendelse_id, svarutkast_id, postboks,
         sendt_av)
    VALUES (p_tenant, p_epost_utkast_id, p_henvendelse_id, p_svarutkast_id,
            lower(btrim(p_postboks)), p_aktor);
    PERFORM public.m17_evidens(
        p_tenant, p_henvendelse_id, 'svar.til_postboks', p_aktor,
        jsonb_build_object('utkast_id', p_svarutkast_id::text,
                           'epost_utkast_id', p_epost_utkast_id::text,
                           'postboks', lower(btrim(p_postboks))));
    RETURN true;
END $$;
REVOKE ALL ON FUNCTION m17_koble_postbokssvar(TEXT, UUID, UUID, UUID, TEXT,
    TEXT) FROM PUBLIC;

-- UTFALLET TILBAKE TIL SAKEN. Triggerfunksjon på `epost_utkast`: bare en
-- overgang FRA `sendes` til `sendt` eller `feilet`, og bare for et utkast
-- som har en kobling. Lukkingen bærer AKTØREN SOM SENDTE (vakten i 164
-- krever at `lukket_av` er aktøren i konteksten), og konteksten settes
-- tilbake etterpå: transaksjonen er postboksprosessens, ikke vår.
CREATE FUNCTION m17_postbokssvar_utfall()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_rader INT; v_lukket BOOLEAN := false; v_forrige TEXT;
BEGIN
    SELECT * INTO r FROM public.henvendelse_postbokssvar k
     WHERE k.tenant = NEW.tenant AND k.epost_utkast_id = NEW.utkast_id
       FOR UPDATE;
    IF NOT FOUND OR r.utfall IS NOT NULL THEN
        RETURN NEW;
    END IF;
    UPDATE public.henvendelse_postbokssvar
       SET utfall = NEW.status, utfall_ts = now()
     WHERE tenant = NEW.tenant AND epost_utkast_id = NEW.utkast_id;
    v_forrige := current_setting('disponit.aktor', true);
    PERFORM set_config('disponit.aktor', r.sendt_av, true);
    IF NEW.status = 'sendt' THEN
        UPDATE public.henvendelse
           SET lukket_ts = now(), lukket_av = r.sendt_av,
               lukket_utfall = 'besvart'
         WHERE tenant = NEW.tenant AND henvendelse_id = r.henvendelse_id
           AND lukket_ts IS NULL;
        GET DIAGNOSTICS v_rader = ROW_COUNT;
        v_lukket := v_rader = 1;
        PERFORM public.m17_evidens(
            NEW.tenant, r.henvendelse_id, 'svar.sendt', r.sendt_av,
            jsonb_build_object('via', 'postboks', 'postboks', r.postboks,
                               'utkast_id', r.svarutkast_id::text,
                               'epost_utkast_id', NEW.utkast_id::text,
                               'sendt_ts', coalesce(NEW.sendt_ts, now()),
                               'lukket', v_lukket));
    ELSE
        PERFORM public.m17_evidens(
            NEW.tenant, r.henvendelse_id, 'svar.feilet', r.sendt_av,
            jsonb_build_object('via', 'postboks', 'postboks', r.postboks,
                               'utkast_id', r.svarutkast_id::text,
                               'epost_utkast_id', NEW.utkast_id::text,
                               'grunn', NEW.feilgrunn));
    END IF;
    PERFORM set_config('disponit.aktor', coalesce(v_forrige, ''), true);
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m17_postbokssvar_utfall() FROM PUBLIC;
-- Den som OPPRETTER triggeren må kunne utføre funksjonen (migratoren,
-- under RESET ROLE nederst). Ingen andre: en triggerfunksjon kalles ikke.
GRANT EXECUTE ON FUNCTION m17_postbokssvar_utfall() TO disponit_migrator;

-- SENDTE SVAR, for flaten «Sendte svar»: hver sporrad om et svar som gikk
-- ut, ble lagt i kø eller feilet — begge veier (husets og postboksens),
-- nyeste først. Teksten følger ikke med; den hentes per sak.
CREATE FUNCTION m17_sendte_svar(p_tenant TEXT, p_grense INT)
RETURNS TABLE(henvendelse_id UUID, handling TEXT, aktor TEXT, detalj JSONB,
              ts TIMESTAMPTZ, lukket_utfall TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_sendte_svar');
    RETURN QUERY
    SELECT s.henvendelse_id, s.handling, s.aktor, s.detalj, s.ts,
           h.lukket_utfall
      FROM public.henvendelsesspor s
      JOIN public.henvendelse h
        ON h.tenant = s.tenant AND h.henvendelse_id = s.henvendelse_id
     WHERE s.tenant = p_tenant
       AND s.handling IN ('svar.sendt', 'svar.til_postboks', 'svar.feilet')
     ORDER BY s.ts DESC, s.spor_id DESC
     LIMIT greatest(least(coalesce(p_grense, 100), 500), 1);
END $$;
REVOKE ALL ON FUNCTION m17_sendte_svar(TEXT, INT) FROM PUBLIC;

-- HVOR SAKEN KOM FRA. Runtime har ingen SELECT på registeret (SP-7), så
-- API-et får kanalen og leverandørens melding-id gjennom en dør — og
-- slår selv opp postboksen i e-postens egne tabeller, der det har lov.
CREATE FUNCTION m17_kilde_ref(p_tenant TEXT, p_henvendelse_id UUID)
RETURNS TABLE(kanal TEXT, ekstern_ref TEXT, lukket BOOLEAN)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'm17_kilde_ref');
    RETURN QUERY
    SELECT h.kanal, h.ekstern_ref, h.lukket_ts IS NOT NULL
      FROM public.henvendelse h
     WHERE h.tenant = p_tenant AND h.henvendelse_id = p_henvendelse_id;
END $$;
REVOKE ALL ON FUNCTION m17_kilde_ref(TEXT, UUID) FROM PUBLIC;

RESET ROLE;

CREATE TRIGGER epost_utkast_til_henvendelse
    AFTER UPDATE OF status ON epost_utkast
    FOR EACH ROW
    WHEN (OLD.status = 'sendes' AND NEW.status IN ('sendt', 'feilet'))
    EXECUTE FUNCTION m17_postbokssvar_utfall();
