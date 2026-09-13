-- 198: ANDRE FIRMAER SKAL KUNNE LOGGE INN MED SIN EGEN MICROSOFT 365.
--
-- MÅLT 13/9 MOT MICROSOFT, FØR NOE BLE SKREVET:
--
--   common        → issuer: https://login.microsoftonline.com/{tenantid}/v2.0
--   organizations → issuer: https://login.microsoftonline.com/{tenantid}/v2.0
--   consumers     → issuer: https://login.microsoftonline.com/9188040d-…/v2.0
--   <guid>        → issuer: https://login.microsoftonline.com/<guid>/v2.0
--   <domene>      → issuer: https://login.microsoftonline.com/<guid>/v2.0
--
-- De to første svarer med en LITTERAL plassholder. `oidc.py` sammenligner
-- issuer med EKSAKT LIKHET to steder (discovery-dokumentet og `iss`-claimet),
-- og mot `{tenantid}` slår begge feil. ÉN Microsoft-tenant fungerer altså
-- allerede i dag — eierens egen, med en enkelttenant-discovery-URL. Det som
-- IKKE fungerer er hele poenget: at et hvilket som helst firma kan bruke sin
-- egen M365 uten at noen skriver en rad for dem først.
--
-- EN MAL, IKKE ET REGEX
-- ---------------------
-- Den nærliggende formen er en regex-kolonne. Den forkastes: en rad skrevet
-- for hånd på verten er nøyaktig der en tastefeil kommer inn, og `.*` i en
-- issuer-regex gjør enhver utsteder i verden gyldig — stille, akkurat som den
-- tomme algoritmelisten i 197. En MAL kan ikke utvides ved en tastefeil:
-- basen lagrer bare teksten med `{tenantid}` i, og KODEN bygger mønsteret med
-- `re.escape` rundt malen og et fast GUID-mønster i plassholderen. Det verste
-- en feilskrevet mal kan gjøre er å matche ingenting.
--
-- BINDINGEN SOM GJØR DET TRYGT
-- ----------------------------
-- En mal alene ville godtatt `iss` fra HVILKEN SOM HELST Microsoft-tenant,
-- uansett hvilken tenant tokenet faktisk kom fra. Microsoft dokumenterer
-- kuren, og den håndheves i `_valider_id_token`: tenant-GUID-en i `iss` må
-- være den samme som `tid`-claimet. Uten den koblingen er malen et hull.
--
-- IDENTITETEN BLIR PER TENANT, IKKE PER MAL
-- `brukeridentitet` er `(issuer, sub)`. Ble malen stående som issuer, ville
-- ALLE Microsoft-brukere i verden delt én issuer-verdi hos oss. Den løste
-- issueren — med den faktiske GUID-en — er det som lagres.
-- ============================================================

ALTER TABLE oidc_provider
    ADD COLUMN IF NOT EXISTS issuer_mal TEXT;

-- Malen MÅ inneholde plassholderen, ellers er den ikke en mal — da skal
-- raden bruke `issuer` og eksakt likhet som før. `NULL !~ mønster` er NULL,
-- og en CHECK slipper NULL gjennom; her er det RIKTIG (NULL = ingen mal),
-- og `IS NULL`-grenen sier det uttrykkelig i stedet for å hvile på det.
-- Samme felle som 182 og 194 — forskjellen er at den her er tilsiktet.
ALTER TABLE oidc_provider
    ADD CONSTRAINT oidc_provider_issuer_mal_form
    CHECK (issuer_mal IS NULL
           OR (issuer_mal LIKE 'https://%'
               AND position('{tenantid}' in issuer_mal) > 0));

-- HVA `issuer` BETYR NÅR MALEN ER SATT
-- Kolonnen er NOT NULL UNIQUE og kan ikke droppes, men den SAMMENLIGNES ikke
-- for en mal-rad — den er identitet og unikhetsnøkkel, ikke en kontrakt. Det
-- er verdt å si uttrykkelig: mitt første portutkast lot `issuer` og
-- `issuer_mal` bære samme streng, og da gikk mutasjonen «sammenlign
-- discovery mot `issuer` alene» GRØNN. Testdataene gjorde feilen usynlig.
COMMENT ON COLUMN oidc_provider.issuer IS
    'Utstederen, sammenlignet med EKSAKT likhet — unntatt når `issuer_mal` er '
    'satt: da er denne kolonnen bare identitet og unikhetsnøkkel, og malen '
    'er det som sammenlignes.';

COMMENT ON COLUMN oidc_provider.issuer_mal IS
    'Issuer-MAL for en flerleietaker-IdP, f.eks. '
    '"https://login.microsoftonline.com/{tenantid}/v2.0". NULL = eksakt '
    'likhet mot `issuer`, som før. Dette er en MAL, ikke et regex: koden '
    'escaper teksten og setter inn sitt eget GUID-mønster, så en tastefeil '
    'kan bare gjøre malen SNEVRERE, aldri videre. Brukes malen, kreves i '
    'tillegg at tenant-GUID-en i `iss` er lik `tid`-claimet.';
