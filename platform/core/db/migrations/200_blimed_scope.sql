-- 200: INNLØSNINGEN FÅR SITT EGET SCOPE.
--
-- MÅLT FØR DETTE: `/v1/invitasjoner/innloes` krevde `firma:opprett` — altså
-- REGISTRANTENS scope, det eneste `registrant`-rollen har. Konsekvensen var
-- at bare en som IKKE hadde et firma kunne innløse en invitasjon. En ansatt
-- i firma A som ble invitert til firma B ble avvist.
--
-- HVORFOR DET STO SLIK, og hvorfor det ikke lenger skal:
-- Kravet var opprinnelig et VERN. Så lenge to medlemskap låste en bruker ute
-- av BEGGE firmaene (`firma_ikke_valgt`), ville en ansatt i A som innløste en
-- invitasjon til B mistet tilgangen til firmaet hun alt jobbet i, i samme
-- klikk. 196 (firmavelgeren) fjernet den utestengelsen — og dermed vernets
-- forutsetning. Det sto dokumentert fire steder at kravet «skal løftes».
--
-- ET LÅNT SCOPE LYVER OM HVA DET GJELDER. `firma:opprett` betyr «kan opprette
-- et firma på plattformen». Det er en langt større fullmakt enn «kan ta imot
-- en invitasjon», og at de falt sammen var en tilfeldighet i `_autentiser`s
-- form — den er bygget for ETT påkrevd scope og avviser `None` — ikke en
-- vurdering noen har gjort.
--
-- AUTORITETEN ER TOKENET, ikke scopet. Sesjonen beviser hvem hun er, CSRF at
-- det er hennes egen nettleser, og `invitasjon_innloes` at engangstokenet er
-- gyldig, uforbrukt og hører til firmaet. Scopet slipper henne bare forbi den
-- generelle porten. Derfor kan det trygt gis til ALLE roller: det åpner ingen
-- dør noen ikke allerede kunne gå med et gyldig token i hånden.
-- ============================================================

INSERT INTO rolle_scope (rolle, scope) VALUES
    ('leser',             'firma:blimed'),
    ('sikkerhet',         'firma:blimed'),
    ('admin',             'firma:blimed'),
    ('godkjenner',        'firma:blimed'),
    ('policyforvalter',   'firma:blimed'),
    ('domeneadjudikator', 'firma:blimed'),
    -- Registranten har det OGSÅ. Hun trenger det ikke for å registrere seg,
    -- men en invitasjon kan komme før hun rekker å opprette noe eget — og da
    -- skal ikke veien være stengt fordi hun tilfeldigvis sto i det ene løpet.
    ('registrant',        'firma:blimed')
ON CONFLICT DO NOTHING;
