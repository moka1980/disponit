-- 196: FIRMAVELGEREN — to medlemskap skal ikke låse noen ute.
--
-- MÅLT: `_firma_for_bruker` (189/192) svarer `firma_ikke_valgt` når en
-- identitet hører til mer enn ett firma, og det gjelder BEGGE. En ansatt som
-- får et medlemskap nummer to mister tilgangen til firmaet hun alt jobber i.
--
-- Det var riktig da det ble skrevet — å velge det første alfabetisk ville
-- logget noen inn i feil firma uten å si det — men det gjorde et helt
-- bruksmønster utilgjengelig: en regnskapsfører med to klienter, en
-- daglig leder i to selskaper, en som blir invitert av en kunde.
--
-- SESJONEN KAN IKKE BYTTE FIRMA. `brukersesjon_kolonnelaas` (010) forbyr å
-- endre `tenant` på en levende sesjon, og det skal den fortsette med: en
-- sesjon ER en fullmakt i ETT firma, og en bryter som flyttet den ville
-- gjort revisjonssporet tvetydig. Å bytte firma er derfor ALLTID en ny
-- innlogging — ikke en knapp som endrer tilstand.
--
-- ØNSKET FØLGER MED INN I RUNDEN. `/v1/oidc/start` tar imot et firma, og det
-- lagres her sammen med resten av logintransaksjonen. Callbacken bruker det
-- BARE hvis brukeren faktisk er medlem der — ønsket er en preferanse, aldri
-- en fullmakt. Kommer det fra en fremmed, er det verdiløst: hun må uansett
-- gjennom leverandøren og eie et medlemskap.
--
-- UTEN ØNSKE VELGES DET FØRSTE ALFABETISK, og det er en bevisst nedgradering
-- fra dagens nei: hun lander et sted hun HAR tilgang, skallet sier hvilket
-- firma hun er i, og bytteren er ett klikk unna. Å stenge henne ute helt var
-- verre.
-- ============================================================
ALTER TABLE oidc_logintransaksjon
    ADD COLUMN IF NOT EXISTS firma_onske TEXT;

COMMENT ON COLUMN oidc_logintransaksjon.firma_onske IS
    'Firmaet brukeren ba om ved innlogging. En PREFERANSE, ikke en '
    'fullmakt: callbacken bruker den bare hvis medlemskapet finnes.';
