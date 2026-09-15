-- 203 — INNBOKSEN NÅR KUNDESERVICEREGISTERET.
--
-- MÅLT 15/9 i produksjon: `wcagvakt` hadde 18 innkomne e-poster og NULL
-- henvendelser. M-6 hentet dem inn og la dem i `epost_melding`; der ble
-- de liggende. Kjeden videre — sveipen som finner de oversette,
-- klassifiseringen, svarutkastet og utsendingen — er bygget, grantet og
-- i drift i M-17, men ingenting førte meldingene dit.
--
-- `m17_ta_imot` er grantet til `disponit` (runtime). Innhenteren kjører
-- i planarbeideren, som `disponit_plan_arbeider`, og den hadde IKKE
-- rettigheten — målt, ikke antatt. Det er hele grunnen til at broen
-- trenger en migrasjon og ikke bare en kodelinje.
--
-- HVORFOR EN MIGRASJON OG IKKE `RETTIGHETER` I `migrer.py`:
-- deployens `NULLSTILL_TABELLER` tar TABELLrettigheter fra runtime-
-- rollene etter migrasjonene. FUNKSJONSrettigheter overlever, og dette
-- er en funksjonsrettighet. (Motsatt vei ville en GRANT her blitt visket
-- ut ved neste deploy — den lærdommen sitter i husets minne.)
--
-- INGEN NY AUTORITET: planarbeideren får nøyaktig de to funksjonene
-- inntaket trenger, ingen tabellrettigheter, og ingen vei til å svare.
-- `m17_ta_imot` er SECURITY DEFINER og krever tenantkontekst som før.

-- EIEREN GRANTER, IKKE MIGRATOR. `m17_ta_imot` eies av
-- `disponit_kundeservice_eier` (målt), og bare eieren kan gi bort
-- rettigheter på den — migrator fikk «permission denied for function».
-- Samme form som 160 selv bruker.
SET LOCAL ROLE disponit_kundeservice_eier;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
                WHERE rolname = 'disponit_plan_arbeider') THEN
        -- Inntaksdøra, 15-arg-formen (160): tar imot meldingen OG setter
        -- avsenderen kryptert i samme kall.
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_ta_imot(TEXT, UUID, TEXT,'
            ' TEXT, TIMESTAMPTZ, TEXT, BYTEA, BYTEA, BYTEA, BYTEA, TEXT,'
            ' TEXT, TEXT, BYTEA, BYTEA) TO disponit_plan_arbeider';
        -- …og 12-arg-formen den delegerer til (102), for kanaler uten
        -- adresse. Uten den ville en melding fra en avsender UTEN
        -- e-postadresse feilet inne i definereren.
        EXECUTE 'GRANT EXECUTE ON FUNCTION m17_ta_imot(TEXT, UUID, TEXT,'
            ' TEXT, TIMESTAMPTZ, TEXT, BYTEA, BYTEA, BYTEA, BYTEA, TEXT,'
            ' TEXT) TO disponit_plan_arbeider';
    END IF;
END $$;

RESET ROLE;
