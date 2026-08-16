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
-- `locales/`. I INNBOKSEN gir det mottakerens eget språk — flaten rendrer
-- nøkkelen med det språket leseren har valgt, ikke det avsenderen tilfeldigvis
-- hadde da hendelsen skjedde.
--
-- E-POSTEN ER EN ANNEN SAK, og det skal stå her og ikke bare i senderen:
-- språkvalget i portalen lever i nettleseren (URL-ledd + `localStorage`, se
-- `ui/static/js/i18n.js`), og INGEN serverlagret språkpreferanse finnes —
-- profil-DTO-en fra IdP-en er lukket til tre felt, og `varselvalg` bærer bare
-- kanalvalget. Senderen kan derfor ikke vite hvilket språk mottakeren leser
-- på; den rendrer i INSTALLASJONENS språk. At nøkkelen og ikke setningen
-- lagres er likevel det som gjør det billig å rette senere: den dagen en
-- lagret preferanse finnes, gjelder den også for det som alt står i kø.
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
    -- HVILKEN forekomst av hendelsen på den ressursen. Et utkast kan få runde
    -- 1 som forfaller, runde 2 som avbrytes, runde 3 som venter på nettopp
    -- deg — og det er tre forskjellige varsler, ikke ett gjentatt. Uten dette
    -- leddet i unikhetsnøkkelen nedenfor ville `ON CONFLICT DO NOTHING` tatt
    -- runde 3 for en retry av runde 1 og slukt den i stillhet.
    -- Tom streng når ressursen SELV er hendelsen og det ikke finnes noen
    -- forekomst å skille på.
    hendelse        text NOT NULL DEFAULT '',
    tekstnokkel     text NOT NULL,
    parametre       jsonb NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(parametre) = 'object'),
    opprettet       timestamptz NOT NULL DEFAULT now(),
    lest_ts         timestamptz,
    -- E-postens tilstand. `ikke_aktuelt` når mottakeren har valgt kun portal:
    -- da er det et bevisst fravær, ikke en feilet sending.
    --
    -- `under_sending` er senderens KLAIM (027): en rad som er tatt ut av køen
    -- og committet som tatt FØR SMTP-kallet begynner. Uten en slik tilstand
    -- var plukket en ren `SELECT` av `koet`, og to sendere som overlappet
    -- kunne hente samme rad, begge sende e-posten, og først etterpå
    -- konkurrere om statusen — en e-post er ikke noe en tapt UPDATE kan
    -- trekke tilbake (Codex P1).
    epost_status    text NOT NULL DEFAULT 'koet'
                    CHECK (epost_status IN ('koet', 'under_sending', 'sendt',
                                            'feilet', 'ikke_aktuelt')),
    epost_forsok    integer NOT NULL DEFAULT 0 CHECK (epost_forsok >= 0),
    -- HVEM sitt klaim (Codex P2). `under_sending` sier at raden er tatt, ikke
    -- av hvem, og med en lease som gjenopptar døde klaim er det ikke nok:
    -- kommer sender A tilbake etter at leasen løp ut og B har rekøet og
    -- klaimet raden på nytt, står raden `under_sending` igjen — men det er
    -- B sitt klaim. En fullføring gjerdet på status alene ville da latt A
    -- skrive `sendt` over B sin levende sending, og B ville stått igjen uten
    -- noe sted å skrive sitt eget resultat.
    --
    -- Tokenet gjør klaimet identifiserbart: klaimet setter en fersk uuid og
    -- returnerer den, fullføringen krever nøyaktig den, og gjenopptakingen
    -- nuller den ut. Da eier hver fullføring bare sitt eget klaim, og et
    -- utløpt klaim kan ikke røre erstatteren sin.
    epost_klaim     uuid,
    epost_ts        timestamptz,
    epost_feil      text
);

-- Innboksen spørres på TO måter, og de trenger hvert sitt indeks.
--
--   `innboks(..., kun_uleste=False)` er STANDARDEN — portalvisningen «mine
--   varsler, uleste først, deretter nyeste først», leste som uleste. Den kan
--   ikke bruke delindeksen nedenfor, for spørringen innebærer ikke
--   `lest_ts IS NULL`. Uten et fullt indeks ville den fått seq scan + sort
--   over en voksende flertenant-tabell.
--
--   `(lest_ts IS NULL) DESC` står som eget ledd fordi det er sorteringens
--   FØRSTE nøkkel: uleste først er det som gjør ulest-telleren sann, siden en
--   ren `opprettet DESC` skjøv et gammelt ulest varsel ut av siden så snart
--   det lå nok nyere leste over det (Codex P2). Uten leddet i indekset ville
--   den prioriteringen kostet en sortering av hele brukerens historikk ved
--   hver eneste henting.
CREATE INDEX IF NOT EXISTS varsel_innboks
    ON varsel (tenant, bruker_id, ((lest_ts IS NULL)) DESC, opprettet DESC);
--   `kun_uleste=True` og `antall_uleste` leter derimot etter de FÅ radene i en
--   tabell der de leste blir de mange. Delindeksen beholdes fordi den blir
--   liten og ikke vokser med historikken: den koster lite å vedlikeholde og
--   holder ulest-telleren rask lenge etter at fullindekset er blitt stort.
CREATE INDEX IF NOT EXISTS varsel_uleste
    ON varsel (tenant, bruker_id, opprettet DESC) WHERE lest_ts IS NULL;
-- Senderen spør «hva står i kø», på tvers av tenanter (den kjører som drift).
CREATE INDEX IF NOT EXISTS varsel_koet
    ON varsel (epost_status, opprettet) WHERE epost_status = 'koet';
-- …og re-køingen (027) spør det motsatte, hver eneste kjøring: «hvem har
-- feilet ferdig backoff, og hvilket klaim har løpt ut?» Uten dette leddet blir
-- det en seq scan over hele flertenant-tabellen hvert 5. minutt. Delindeksen
-- vokser ikke med historikken: `sendt` og `ikke_aktuelt` er terminale og
-- faller ut av den, så den holder bare det som fortsatt er underveis.
CREATE INDEX IF NOT EXISTS varsel_ufullfort
    ON varsel (epost_status, epost_ts)
 WHERE epost_status IN ('under_sending', 'feilet');
-- Samme hendelse skal ikke kunne varsle samme person to ganger. Uten dette
-- ville en retry av runde-åpningen fylt innboksen med duplikater.
--
-- `hendelse` MÅ være med: nøkkelen skal fange en RETRY av samme hendelse, ikke
-- en ny hendelse på samme ressurs. Uten den ville en runde 2 på et utkast som
-- forfalt eller ble avbrutt hatt nøyaktig samme nøkkel som runde 1, blitt lest
-- som en dublett, og aldri nådd godkjenneren — verst i det tilfellet der noen
-- har lest det gamle varselet og altså ikke ser noe nytt.
CREATE UNIQUE INDEX IF NOT EXISTS varsel_en_per_hendelse
    ON varsel (tenant, bruker_id, art, ressurs_type, ressurs_id, hendelse);

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
