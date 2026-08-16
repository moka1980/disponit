-- ============================================================
-- 026 — Varsler: «noe venter på DEG», i portalen og på e-post
--
-- Eier: «mokhtar.eliassi@gmail.com skal få epost og samtidig melding i
-- kundeadmin … Det skal være option.»
--
-- Behovet er konkret og kommer fra fire-øyne-flyten: en runde står åpen og
-- venter på en UAVHENGIG godkjenner, men ingenting forteller henne det. I dag
-- må noen sende en melding utenom systemet — og en styringsflate der neste
-- steg formidles på SMS er ikke en styringsflate.
--
-- TO TABELLER, med hvert sitt ansvar:
--
--   `varsel`      — én rad per mottaker per hendelse. Dette ER innboksen;
--                   e-posten er en KOPI av den, ikke omvendt. Derfor er
--                   e-poststatusen en kolonne her og ikke en egen kø: et
--                   varsel som ikke kunne sendes skal fortsatt være synlig i
--                   portalen, og skal kunne prøves igjen uten å bli duplisert.
--
--   `varselvalg`  — valget eier ba om, PER BRUKER (ikke per tenant). I en
--                   fire-øyne-runde er de to godkjennerne forskjellige
--                   mennesker med forskjellige vaner; en tenant-bryter ville
--                   tvunget den ene. Fraværende rad = standarden
--                   (`epost_og_portal`), så ingen går glipp av noe fordi de
--                   aldri har åpnet innstillingene.
--
-- TEKSTEN LAGRES IKKE. Bare `tekstnokkel` + `parametre`, som resten av
-- plattformen: locale-kontrakten sier at all synlig tekst kommer fra
-- `locales/`, og et varsel skal leses på MOTTAKERENS språk — ikke på språket
-- avsenderen tilfeldigvis hadde da hendelsen skjedde. Det avgjør også
-- e-posten: den rendres når den sendes, ikke når den køes.
--
-- Varsler er IKKE append-only som revisjonsloggen. De er driftsdata: de leses,
-- de blir gamle, og de skal kunne ryddes. Revisjonssporet for selve
-- handlingen ligger i `revisjonslogg` og `aktiveringsattestasjon` — det er
-- DEN som er beviset, ikke varselet om den.
-- ============================================================

CREATE TABLE IF NOT EXISTS varsel (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant          text NOT NULL CHECK (length(btrim(tenant)) > 0),
    -- Mottakeren. FK til identiteten, ikke til medlemskapet: mister hun
    -- medlemskapet, skal varselet slutte å vises (RLS + oppslaget), men raden
    -- skal ikke rives ut under en pågående sending.
    bruker_id       text NOT NULL REFERENCES brukeridentitet(bruker_id),
    art             text NOT NULL CHECK (art IN ('attestering_venter',
                                                 'validering_venter',
                                                 'runde_apnet')),
    -- Hva varselet handler om. Fritt par så flaten kan lenke dit uten at
    -- tabellen må kjenne hver ressurstype.
    ressurs_type    text NOT NULL CHECK (ressurs_type IN ('policyutkast')),
    ressurs_id      text NOT NULL,
    tekstnokkel     text NOT NULL,
    parametre       jsonb NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(parametre) = 'object'),
    opprettet       timestamptz NOT NULL DEFAULT now(),
    lest_ts         timestamptz,
    -- E-postens tilstand. `ikke_aktuelt` når mottakeren har valgt kun portal:
    -- da er det et bevisst fravær, ikke en feilet sending.
    epost_status    text NOT NULL DEFAULT 'koet'
                    CHECK (epost_status IN ('koet', 'sendt', 'feilet',
                                            'ikke_aktuelt')),
    epost_forsok    integer NOT NULL DEFAULT 0 CHECK (epost_forsok >= 0),
    epost_ts        timestamptz,
    epost_feil      text
);

-- Innboksen spør alltid «mine uleste, nyeste først». Delindeks: de leste er
-- de mange, og de er ikke det spørringen leter etter.
CREATE INDEX IF NOT EXISTS varsel_uleste
    ON varsel (tenant, bruker_id, opprettet DESC) WHERE lest_ts IS NULL;
-- Senderen spør «hva står i kø», på tvers av tenanter (den kjører som drift).
CREATE INDEX IF NOT EXISTS varsel_koet
    ON varsel (epost_status, opprettet) WHERE epost_status = 'koet';
-- Samme hendelse skal ikke kunne varsle samme person to ganger. Uten dette
-- ville en retry av runde-åpningen fylt innboksen med duplikater.
CREATE UNIQUE INDEX IF NOT EXISTS varsel_en_per_hendelse
    ON varsel (tenant, bruker_id, art, ressurs_type, ressurs_id);

CREATE TABLE IF NOT EXISTS varselvalg (
    tenant          text NOT NULL CHECK (length(btrim(tenant)) > 0),
    bruker_id       text NOT NULL REFERENCES brukeridentitet(bruker_id),
    kanal           text NOT NULL DEFAULT 'epost_og_portal'
                    CHECK (kanal IN ('epost_og_portal', 'kun_portal')),
    oppdatert       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, bruker_id)
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['varsel', 'varselvalg'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolasjon ON %I', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolasjon ON %I
                USING      (tenant = current_setting(''disponit.tenant'', true))
                WITH CHECK (tenant = current_setting(''disponit.tenant'', true))', t);
    END LOOP;
END $$;
