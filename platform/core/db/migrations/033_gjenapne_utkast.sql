-- ============================================================
-- 033 — Gjenåpne et validert utkast for redigering (eiers krav 17/8)
--
-- 🔴 FUNNET (eier, i produksjon): et validert utkast er frosset, og eneste
-- vei videre ved en feil var å forkaste det og bygge et nytt fra bunnen.
-- Eier ga direkte beskjed: «man må kunne redigere samme policy selv etter
-- validering … men da kan den igjen bli attestert og validert.»
--
-- Statusmaskinen har tillatt `validert → utkast` siden migrasjon 012 — det
-- var HASHEN som sperret veien: kolonnelåsen fryser `innholds_hash` én gang
-- for alltid (NULL → verdi), så et gjenåpnet utkast satt igjen med hashen
-- av et innhold det ikke lenger var bundet til, og en ny validering kunne
-- aldri skrive den nye.
--
-- 033 åpner NØYAKTIG den manglende overgangen: `innholds_hash` kan settes
-- tilbake til NULL i samme UPDATE som statusen går `validert → utkast` —
-- og bare der. Enhver annen endring av en satt hash er fortsatt frosset.
-- Ingen fullmakt endres: det gjenåpnede utkastet må valideres på nytt (ny
-- frysing, ny hash) og gjennom en helt ny fire-øyne-runde før aktivering.
-- Attestasjoner peker på RUNDENS frosne kopi (`utkast_innholds_hash`,
-- migrasjon 012 7c — alle bindingsfelt uforanderlige), så historikken står
-- urørt uansett hva utkastraden gjør etterpå.
--
-- Kroppen under er 012 sin GJELDENDE kropp (ingen senere migrasjon rører
-- `policyutkast_kolonnelaas`), diff-endret i ett vilkår — aldri skrevet fra
-- hukommelsen (jf. 028-lærdommen, der en husket kropp mistet tre rettelser).
-- ============================================================

CREATE OR REPLACE FUNCTION policyutkast_kolonnelaas()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.opprettet_av IS DISTINCT FROM OLD.opprettet_av
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet
       OR NEW.utkast_id IS DISTINCT FROM OLD.utkast_id
       OR NEW.policy_id IS DISTINCT FROM OLD.policy_id THEN
        RAISE EXCEPTION 'policyutkast: identitet/opphav er uforanderlig';
    END IF;
    -- Frosset hash kan settes ÉN gang (NULL→verdi ved validering) — og
    -- NULLSTILLES i samme skriv som gjenåpningen `validert → utkast` (033):
    -- da er frysingen sant nok opphevet, for innholdet er redigerbart igjen
    -- og MÅ valideres på nytt før noen kan signere på det. Alle andre
    -- endringer av en satt hash er fortsatt sperret.
    IF OLD.innholds_hash IS NOT NULL
       AND NEW.innholds_hash IS DISTINCT FROM OLD.innholds_hash
       AND NOT (NEW.innholds_hash IS NULL
                AND OLD.status = 'validert' AND NEW.status = 'utkast') THEN
        RAISE EXCEPTION 'policyutkast: innholds_hash er frosset';
    END IF;
    -- Statusmaskin.
    IF NOT (
        (OLD.status = 'utkast'   AND NEW.status IN ('utkast','validert','forkastet')) OR
        (OLD.status = 'validert' AND NEW.status IN ('utkast','validert','godkjent','forkastet')) OR
        (OLD.status = 'godkjent' AND NEW.status IN ('validert','aktivert','forkastet')) OR
        (OLD.status = NEW.status)
    ) THEN
        RAISE EXCEPTION 'policyutkast: ulovlig statusovergang % -> %', OLD.status, NEW.status;
    END IF;
    -- Terminaltilstander er uforanderlige.
    IF OLD.status IN ('forkastet','aktivert') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'policyutkast: % er terminal', OLD.status;
    END IF;
    -- Innholdsendring krever versjonsøkning (aldri tapt skriving).
    IF NEW.innhold IS DISTINCT FROM OLD.innhold
       AND NEW.utkastversjon <= OLD.utkastversjon THEN
        RAISE EXCEPTION 'policyutkast: innholdsendring krever høyere utkastversjon';
    END IF;
    RETURN NEW;
END $$;
