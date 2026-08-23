-- 056: M-57 utsendingskjeden — oppdrag → frigivelse → signatur →
-- listeversjon → innhold, hvert ledd en navngitt constraint.
--
-- Klarsignalet (docs/pr/PR-M57-IMPLEMENTERINGSKLARSIGNAL.md, frosset):
-- evalueringen er RÅDGIVENDE (artefakter, ingenting utad); utsendelsen er
-- IRREVERSIBEL og skjer kun fordi et menneske signerte. Utsendelsen er
-- ikke et nytt modulnoppdrag, men en signaturbundet FRIGIVELSE:
-- opprinnelsesvalget er (b) — en tredje opprinnelse 'frigivelse' med sin
-- egen herdede funksjon, slik 038 selv valgte formen da beslutningsveien
-- kom til. CHECK-en dekker kombinasjonene uttømmende, og funksjonen er
-- eneste vei.
--
-- LISTEN ER EN VERSJON: immutabel rad; redigering gir ny liste_id i samme
-- utkast_serie. Lineage er lineær og serie-bundet — én rot per serie,
-- høyst ett barn per ledd, høyst én SIGNERT versjon per serie. Dermed:
-- ingen gyldig signatur => ingen representerbar ATS-utsendelse, bevisbart
-- med direkte DML (portene 6–12).
--
-- HVA CP1 IKKE PÅSTÅR — snevret eksplisitt (Cursor P1-2/P2-4, runde 7 på
-- #140; Funn 8 fra runde 2). Kjeden over binder frigivelsens IDENTITET:
-- «dette oppdraget hører til denne frigivelsen, som hører til denne
-- signerte listeversjonen». Den binder IKKE innholdet som faktisk sendes:
--   * `payload_kryptert` er utenfor retry-materialiteten (AES-GCM gir ny
--     nonce per kryptering, se §7d), så `disponit_varselsender` — rollen
--     som har EXECUTE — kan feste vilkårlig chiffertekst til en gyldig
--     frigivelse. Mennesket signerte listeinnholdet, ikke chifferteksten.
--   * `mottaker_ref` er et ANTALLTAK mot det signerte `antall`, ikke et
--     MEDLEMSKAP: basen har ingen representasjon av listens mottakere, så
--     «disse N mottakerne» er i dag «høyst N strenger».
-- Begge krever samme maskin — et per-mottaker-manifest skrevet før
-- signering og dekket av `innhold_hash` — altså ny produktflate, ikke en
-- fiksrunde-endring (K1, RUTINER §9). Sporet er GitHub-issue #149, og
-- `test_frigivelse_binder_ikke_payload_til_signert_innhold` dokumenterer
-- dagens tillatte avvik så regresjonen synes når manifestet kommer.
--
-- ... OG CP1 PÅSTÅR IKKE AT EIEREN AV FUNKSJONENE ER BUNDET AV DEM
-- (snevret i runde 10 på #140; sporet er issue #150). Den bærende
-- påstanden over er NEGATIV og gjelder alle: ingen gyldig signatur ⇒
-- ingen representerbar utsendelse. Den er en skjemasannhet — FK-kjeden og
-- constraintene i §1–§5 — og måles med direkte DML som EIEREN.
-- Påstander om MENGDE og HVEM er derimot funksjonssanne, ikke
-- skjemasanne: `frigi_utsendelse` håndhever taket mot signert `antall`,
-- `signer_utsendingsliste` håndhever signatarens aktive medlemskap, og
-- `opprett_frigivelsesoppdrag` avviser en alt utløpt `utforelsesfrist`.
-- De tre er sanne for enhver ORDINÆR rolle (runtime og varselsenderen har
-- ingen INSERT; §8 + porten `test_funksjonene_er_eneste_vei_...`), men
-- ikke for `disponit_m37_claimer`, som eier funksjonene og derfor må ha
-- INSERT på tabellene deres. Å speile hver forutsetning i en trigger
-- duplikaterer funksjonskroppene og lukker ikke klassen — rotårsaken er
-- at den herdede døren og bakdøren har SAMME eier. Fiksen er et
-- eierskille (egen NOLOGIN-rolle for kjedefunksjonene, claimerens INSERT
-- trukket), altså ny rigg i `oppsett-postgresql.sh`/`migrer.py`/SP-10 =
-- ny maskin = egen PR (K1). Issue #150 bærer den, med Cursors tre funn
-- (signert `antall`-tak, signatar-medlemskap, fristport) utsatt dit.
-- ... og fra runde 12 også Codex' flerrads-sykel i lineagen: LINEARITET
-- er schema-sann for en skriver som tar ÉN RAD OM GANGEN, men en enkelt
-- flerrads-`INSERT` fra en rolle med `INSERT` kan lukke en rotløs
-- 2-sykel. Se den fulle rotårsaken ved `en_rot_per_serie` i §1.

-- ------------------------------------------------------------
-- 1. Listeversjonene. Append-only: hele raden er innholdet signaturen
--    binder; en «redigering» er en NY versjon med forrige som forelder.
CREATE TABLE utsendingsliste (
    tenant TEXT NOT NULL,
    liste_id UUID NOT NULL,
    utkast_serie UUID NOT NULL,
    forrige_liste_id UUID,
    oppdrag_id BIGINT NOT NULL,          -- evalueringsoppdraget (BIGINT)
    listetype TEXT NOT NULL CHECK (listetype IN ('invitasjon', 'avslag')),
    malversjon TEXT NOT NULL,
    innhold_hash TEXT NOT NULL,
    antall INT NOT NULL CHECK (antall > 0 AND antall <= 5000),
    opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, liste_id),
    UNIQUE (tenant, liste_id, innhold_hash),
    UNIQUE (tenant, utkast_serie, liste_id),           -- serie-refererbar
    -- ... og den samme nøkkelen MED evalueringsoppdraget: FK-målet som
    -- gjør proveniensen til en skjemapåstand (se self-FK-en under).
    UNIQUE (tenant, utkast_serie, liste_id, oppdrag_id),
    UNIQUE (tenant, utkast_serie, innhold_hash),
    -- ... og en versjon kan ikke være SIN EGEN forelder (Cursor P2 på
    -- #140, runde 5). Self-FK-en er lovlig i PostgreSQL, så direkte DML
    -- kunne sette `forrige_liste_id = liste_id`: serien fikk da NULL
    -- røtter — `en_rot_per_serie` teller kun rader med forelder NULL — og
    -- kjeden signer → frigi → oppdrag virket likevel. «Én rot per serie»
    -- skal være schema-håndhevet, ikke en konvensjon funksjonsveien
    -- tilfeldigvis holder.
    CONSTRAINT utsendingsliste_ikke_egen_forelder
        CHECK (forrige_liste_id IS NULL OR forrige_liste_id <> liste_id),
    -- Forelderen må stå i SAMME serie (port 9) OG bære SAMME
    -- evalueringsoppdrag: FK-en går på serie-nøkkelen utvidet med
    -- `oppdrag_id`, så en versjon hverken kan adoptere en annen series
    -- historikk eller forgrene proveniensen inni sin egen.
    --
    -- Codex P2 (runde 6 på #140) + Cursor P2 (runde 5), samme funn: de to
    -- FK-ene var UAVHENGIGE — barnet måtte peke på forelderen i serien, og
    -- barnet måtte peke på ET evalueringsoppdrag, men ingenting knyttet de
    -- to. Direkte DML fra eier/claimer kunne dermed lage en «lineær» serie
    -- der `oppdrag_id` forgrenet seg, og den forgrenede versjonen var
    -- fortsatt signerbar og sendbar.
    --
    -- ROTÅRSAKEN, siden dette er tredje runde på proveniensen (K2):
    -- påstanden sto i FUNKSJONEN (`opprett_utsendingsliste`, lukket i
    -- runde 3) mens resten av lineage-kontrakten — `ett_barn_per_versjon`,
    -- `en_rot_per_serie`, `utsendingsliste_ikke_egen_forelder` — står i
    -- SKJEMAET. En funksjonsport beviser bare noe om den ene veien; hele
    -- klarsignalets bevisform er negativ og tas med direkte DML. Fiksen er
    -- ikke et nytt formforsøk, men å flytte den siste påstanden dit de
    -- andre allerede bor. Codex' egen anvisning («a composite relationship
    -- that includes `oppdrag_id`») gjør det uten trigger: barnets EGEN
    -- `oppdrag_id` er med i referansen, så den må være forelderens.
    --
    -- Røttene er upåvirket: `forrige_liste_id` er NULL der, og MATCH
    -- SIMPLE sjekker ikke en FK med NULL i noen kolonne.
    FOREIGN KEY (tenant, utkast_serie, forrige_liste_id, oppdrag_id)
        REFERENCES utsendingsliste
            (tenant, utkast_serie, liste_id, oppdrag_id),
    FOREIGN KEY (tenant, oppdrag_id) REFERENCES oppdrag (tenant, id));

-- Lineær lineage (portene 10–11): høyst ETT barn per forelder, og
-- HØYST ÉN rot per serie. Samtidige redigeringer serialiseres av
-- unik-bruddet — én vinner, taperen får konflikt og må lese vinnerens
-- versjon før et nytt forsøk.
--
-- «HØYST», ikke «nøyaktig» (Codex P2, runde 12 på #140): indeksen under
-- er UNIK, ikke tvingende, så NULL røtter er ikke et brudd. Det er
-- vanligvis uinteressant — hver rad må jo ha en forelder som finnes —
-- men ikke i ÉN setning: et flerrads `INSERT` kan legge inn A med B som
-- forelder og B med A som forelder samtidig. Self-FK-en er da oppfylt
-- (RI-triggerne fyrer etter setningen, og begge radene står),
-- `ett_barn_per_versjon` ser to ULIKE foreldre, og `utsendingsliste_
-- ikke_egen_forelder` ser to ULIKE id-er. Resultatet er en rotløs
-- 2-sykel: begge radene er fortsatt signerbare og frigivbare, men
-- serien har ingen opprinnelse — «denne versjonen avløser den» blir
-- sirkulær.
--
-- HVORFOR DEN IKKE LUKKES HER, K2 (RUTINER §9): dette er FJERDE runde
-- på lineage-mekanismen (runde 3: proveniensen sto i funksjonen; runde
-- 5: egen-forelder; runde 6: sammensatt self-FK med `oppdrag_id`), og
-- rotårsaken er den samme som §-hodet alt har skrevet ned for hele
-- klassen: den herdede døren (`opprett_utsendingsliste`, som setter
-- forelderen selv, én rad om gangen) og bakdøren (rå `INSERT`) har
-- SAMME eier. Asyklisitet er en GRAFEGENSKAP, og ingen per-rad-
-- constraint når den. Deklarativt finnes det én vei — en
-- generasjonskolonne med en generert `generasjon - 1` og self-FK-en
-- utvidet med den, så en sykel krever en uendelig synkende kjede — men
-- det er en omskriving av lineage-nøkkelen, funksjonen og SP-10-seeden,
-- altså NY MASKIN i en fiksrunde (K1). Og den ville duplisert jobben
-- til #150: når claimerens `INSERT` trekkes, finnes ingen rolle noen
-- kan LOGGE INN som med rettighet til setningen over. Funnet er derfor
-- navngitt i #150, ikke avvist, og
-- `test_flerrads_sykel_er_dagens_tillatte_avvik` dokumenterer avviket
-- så regresjonen synes når eierskillet lander.
CREATE UNIQUE INDEX ett_barn_per_versjon ON utsendingsliste
    (tenant, utkast_serie, forrige_liste_id)
    WHERE forrige_liste_id IS NOT NULL;
CREATE UNIQUE INDEX en_rot_per_serie ON utsendingsliste
    (tenant, utkast_serie)
    WHERE forrige_liste_id IS NULL;

DROP TRIGGER IF EXISTS utsendingsliste_append_only ON utsendingsliste;
CREATE TRIGGER utsendingsliste_append_only
    BEFORE UPDATE OR DELETE ON utsendingsliste
    FOR EACH ROW EXECUTE FUNCTION avvis_endring();
-- TRUNCATE fyrer ALDRI radtriggere (Codex P2, runde 1): uten en egen
-- statement-vakt kunne eieren tømme hele bevisrekken uten å møte
-- `avvis_endring` én eneste gang. Samme par som resten av husets
-- append-only-tabeller (011, 014, 053).
DROP TRIGGER IF EXISTS utsendingsliste_ingen_truncate ON utsendingsliste;
CREATE TRIGGER utsendingsliste_ingen_truncate
    BEFORE TRUNCATE ON utsendingsliste
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- PROMOTERINGEN ER EN SKJEMAPÅSTAND (Cursor P2, runde 7 på #140).
-- Klarsignal §1 + §7/port 28 sier at en liste promoterer NØYAKTIG en
-- fullført `rekruttering.evaluering` — «avbrutt kjøring ⇒ INGEN
-- promotert liste». Runde 2 la den påstanden i `opprett_utsendingsliste`,
-- men FK-en `(tenant, oppdrag_id)` sier bare at oppdraget finnes hos
-- tenanten, og `disponit_m37_claimer` har `INSERT` på listetabellene.
-- Direkte DML kunne derfor promotere en liste på et WCAG-oppdrag, på en
-- evaluering som fortsatt går eller ble kansellert, eller på et
-- FRIGIVELSESOPPDRAG — og etterpå er den listen signerbar og sendbar
-- som en hvilken som helst annen. Kjeden ville sirklet inn i seg selv.
--
-- Samme klasse og samme svar som `oppdrag_koblingsvakt` (008 → 038 §5):
-- semantikken er et OPPSLAG I VIRKELIG TILSTAND, håndhevet der raden
-- fødes (SP-13). Deklarativt er den ikke tilgjengelig — en FK kan ikke
-- peke på et partielt indeks, og `status` er ikke en konstant.
--
-- Kjøres under kallerens RLS, fail-closed som koblingsvakten: en
-- innsetting uten tenantkontekst ser ingen oppdrag og avvises.
-- Funksjonsporten i §7a beholdes som forsvar i dybden.
CREATE OR REPLACE FUNCTION utsendingsliste_promoteringsvakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.oppdrag o
         WHERE o.tenant = NEW.tenant
           AND o.id = NEW.oppdrag_id
           AND o.oppdragstype = 'rekruttering.evaluering'
           AND o.status = 'utfort'
           AND o.opprinnelse IN ('beslutning', 'm37_reparasjon')) THEN
        RAISE EXCEPTION 'utsendingsliste: oppdrag % er ikke en FULLFØRT'
            ' rekruttering.evaluering hos % — kjeden starter aldri i et'
            ' frigivelsesoppdrag, og en avbrutt kjøring promoteres aldri',
            NEW.oppdrag_id, NEW.tenant
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS utsendingsliste_promotering ON utsendingsliste;
CREATE TRIGGER utsendingsliste_promotering
    BEFORE INSERT ON utsendingsliste
    FOR EACH ROW EXECUTE FUNCTION utsendingsliste_promoteringsvakt();

-- ------------------------------------------------------------
-- 2. Signaturen. Én signert versjon per serie (port 12) — det er
--    versjonen som får sendes, og bare den. Signaturen binder innholdet
--    (hash) OG serien: FK-ene krever at (liste, hash) og (serie, liste)
--    faktisk er samme rad i utsendingsliste.
CREATE TABLE utsendingssignatur (
    tenant TEXT NOT NULL,
    liste_id UUID NOT NULL,
    utkast_serie UUID NOT NULL,
    innhold_hash TEXT NOT NULL,
    signatar TEXT NOT NULL REFERENCES brukeridentitet (bruker_id),
    signert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    operasjonsnokkel TEXT NOT NULL,      -- SP-2
    PRIMARY KEY (tenant, liste_id),
    UNIQUE (tenant, liste_id, innhold_hash, utkast_serie), -- refererbar
    UNIQUE (tenant, operasjonsnokkel),
    FOREIGN KEY (tenant, utkast_serie, liste_id)
        REFERENCES utsendingsliste (tenant, utkast_serie, liste_id),
    FOREIGN KEY (tenant, liste_id, innhold_hash)
        REFERENCES utsendingsliste (tenant, liste_id, innhold_hash));

CREATE UNIQUE INDEX en_signert_versjon_per_serie
    ON utsendingssignatur (tenant, utkast_serie);

DROP TRIGGER IF EXISTS utsendingssignatur_append_only ON utsendingssignatur;
CREATE TRIGGER utsendingssignatur_append_only
    BEFORE UPDATE OR DELETE ON utsendingssignatur
    FOR EACH ROW EXECUTE FUNCTION avvis_endring();
DROP TRIGGER IF EXISTS utsendingssignatur_ingen_truncate
    ON utsendingssignatur;
CREATE TRIGGER utsendingssignatur_ingen_truncate
    BEFORE TRUNCATE ON utsendingssignatur
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- ------------------------------------------------------------
-- 3. Frigivelsen: én rad per mottaker, og FK-kjeden krever at listen er
--    SIGNERT (port 6) — det finnes ingen representerbar frigivelse av en
--    usignert liste, uansett hvilken vei noen skriver.
--
--    RUNDE 3-BESLUTNING (K2 utløst på #140, ansvarlig valgte eksplisitt):
--    klarsignal §5s krav om at `mottaker_ref` slettes ved TTL-utløp hører
--    til TTL-kontrollpunktet (portene 18–20), IKKE til CP1. Runde 2 gjorde
--    kolonnen nullbar for å innfri §5 her og nå — men reaperen (funksjon
--    + rolle) finnes ikke ennå, og en nullbar mottakerreferanse under en
--    unik-nøkkel PÅ nøyaktig den kolonnen brøt idempotensen: PostgreSQL
--    regner NULL som ulik NULL, så en TTL-redigert mottaker kunne frigis
--    PÅ NYTT — mens et samtidig FØRSTEGANGS-kall (uten redaksjon) traff en
--    beslektet kappløpsklasse i samme runde. To runder på samme kolonne
--    for to ULIKE krav (slett kandidatreferansen vs. gjenkjenn den for
--    alltid) er definisjonen på et spesifikasjonsvalg — ikke et
--    formforsøk (K2 stopper formforsøk, ikke beslutninger).
--
--    Kolonnen er derfor NOT NULL igjen — skissens opprinnelige form i
--    §3. §5s krav flyttes i sin helhet til reaper-PR-en, som uansett må
--    eie funksjonen, rollen OG ta det spesifikasjonsvalget (durabel
--    pseudonymnøkkel, eller at TTL-utløp stenger listen for videre
--    frigivelse) — se diskusjonen på #140 for avveiningen.
CREATE TABLE utsendingsfrigivelse (
    tenant TEXT NOT NULL,
    frigivelse_id UUID NOT NULL,
    liste_id UUID NOT NULL,
    innhold_hash TEXT NOT NULL,
    utkast_serie UUID NOT NULL,
    mottaker_ref TEXT NOT NULL,
    frigitt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, frigivelse_id),
    UNIQUE (tenant, liste_id, mottaker_ref),
    FOREIGN KEY (tenant, liste_id, innhold_hash, utkast_serie)
        REFERENCES utsendingssignatur
            (tenant, liste_id, innhold_hash, utkast_serie));

DROP TRIGGER IF EXISTS utsendingsfrigivelse_append_only
    ON utsendingsfrigivelse;
CREATE TRIGGER utsendingsfrigivelse_append_only
    BEFORE UPDATE OR DELETE ON utsendingsfrigivelse
    FOR EACH ROW EXECUTE FUNCTION avvis_endring();
DROP TRIGGER IF EXISTS utsendingsfrigivelse_ingen_truncate
    ON utsendingsfrigivelse;
CREATE TRIGGER utsendingsfrigivelse_ingen_truncate
    BEFORE TRUNCATE ON utsendingsfrigivelse
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

-- ------------------------------------------------------------
-- 4. Tenant-isolasjon — samme form som 038s tabeller.
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['utsendingsliste', 'utsendingssignatur',
                             'utsendingsfrigivelse'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolasjon ON %I
                USING      (tenant = current_setting(''disponit.tenant'', true))
                WITH CHECK (tenant = current_setting(''disponit.tenant'', true))',
            t);
    END LOOP;
END $$;

-- ------------------------------------------------------------
-- 5. Den tredje opprinnelsen på oppdrag. Generisk referansekolonne +
--    constraint-swap i SAMME migrasjon: totalformen tar eksplisitt
--    stilling til HVERT referansefelt i HVER arm (SP-5-total). Den gamle
--    m37-armen tok aldri stilling til `beslutning_loggpost_id` — det
--    rettes her, sammen med utvidelsen.
ALTER TABLE oppdrag ADD COLUMN frigivelse_id UUID;

ALTER TABLE oppdrag
    ADD CONSTRAINT oppdrag_frigivelse_fk
    FOREIGN KEY (tenant, frigivelse_id)
    REFERENCES utsendingsfrigivelse (tenant, frigivelse_id);

-- ÉN frigivelse -> ETT oppdrag (Cursor P1 på #140): utsendelsen er
-- IRREVERSIBEL, så kardinaliteten er en sikkerhetsinvariant — samme form
-- som beslutningsveiens `oppdrag_en_per_beslutning` (008). To kall for
-- samme frigivelse serialiseres av indeksen; funksjonen under gjør
-- taperen idempotent i stedet for feilende.
CREATE UNIQUE INDEX oppdrag_en_per_frigivelse
    ON oppdrag (tenant, frigivelse_id)
    WHERE frigivelse_id IS NOT NULL;

-- KLARSIGNAL-KORREKSJON, målt av SP-10-seedet: skissens m37-arm sa
-- `beslutning_loggpost_id IS NULL`, men m37-oppdrag bærer LEGITIMT en
-- fase-2-beslutningsloggpost (`koblingsstatus='KOBLET'` KREVER den —
-- se `oppdrag_kobling_konsistent`), og legacy-rader har den ikke.
-- Stillingen til `beslutning_loggpost_id` i m37-armen EIES derfor av
-- koblingsvilkåret, ikke av opprinnelsesformen — armen tar eksplisitt
-- stilling til alt annet. En swap etter skissen hadde stoppet på første
-- bebodde base, presis 047-klassen SP-10 finnes for.
-- Verdi-enumen utvides i samme swap: 'frigivelse' er en lovlig
-- opprinnelse fra nå.
ALTER TABLE oppdrag DROP CONSTRAINT oppdrag_opprinnelse_check;
ALTER TABLE oppdrag ADD CONSTRAINT oppdrag_opprinnelse_check CHECK (
    opprinnelse IN ('m37_reparasjon', 'beslutning', 'frigivelse'));

ALTER TABLE oppdrag DROP CONSTRAINT oppdrag_opprinnelse_komplett;
ALTER TABLE oppdrag ADD CONSTRAINT oppdrag_opprinnelse_komplett CHECK (
       (opprinnelse = 'm37_reparasjon'
          AND unntak_id IS NOT NULL AND loggpost_id IS NOT NULL
          AND repair_operation_id IS NOT NULL
          AND frigivelse_id IS NULL)
    OR (opprinnelse = 'beslutning'
          AND beslutning_loggpost_id IS NOT NULL
          AND unntak_id IS NULL AND loggpost_id IS NULL
          AND repair_operation_id IS NULL AND frigivelse_id IS NULL)
    OR (opprinnelse = 'frigivelse'
          AND frigivelse_id IS NOT NULL
          AND unntak_id IS NULL AND loggpost_id IS NULL
          AND repair_operation_id IS NULL
          AND beslutning_loggpost_id IS NULL
          -- ... OG KONTRAKTEN ER EN DEL AV OPPRINNELSESFORMEN (Cursor
          -- P1, runde 7 på #140). Runde 6 lukket at senderen kunne velge
          -- trippelen gjennom `opprett_frigivelsesoppdrag`, men porten
          -- ble stående i FUNKSJONEN — og `disponit_m37_claimer` har
          -- `INSERT ON oppdrag` (038). Direkte DML kunne dermed føde et
          -- KOBLET, frigivelses-bærende oppdrag i en ANNEN moduls kø
          -- (`claim_neste_oppdrag` plukker på eiermodul +
          -- handlingsprefiks) — og fordi `oppdrag_en_per_frigivelse`
          -- gir frigivelsen NØYAKTIG ETT forsøk, ville den raden
          -- samtidig BRENT den signerte utsendelsen: aldri plukkbar for
          -- `m57_ats`, aldri erstattbar.
          --
          -- Samme flytting som runde 6 gjorde med serie-proveniensen:
          -- klarsignalets bevisform er NEGATIV og tas med direkte DML,
          -- så påstanden hører i totalformen der resten av
          -- opprinnelsesarmene alt bor. Funksjonsporten i §7d beholdes
          -- som forsvar i dybden (den gir kalleren en presis feil).
          -- Konsekvensen er den samme som runde 6 skrev ned: en senere
          -- utsendingsvariant krever en migrasjon som utvider listen —
          -- nå to steder, begge bevisste.
          AND oppdragstype = 'rekruttering.utsending'
          AND handling = 'rekruttering.utsending'
          AND eiermodul = 'm57_ats'));

-- Koblingsvilkåret får frigivelses-armen: et KOBLET oppdrag er koblet
-- til autorisasjonen sin — beslutningsloggposten ELLER frigivelsen.
-- Opprinnelsesformen over avgjør hvilken av dem som finnes per arm.
ALTER TABLE oppdrag DROP CONSTRAINT oppdrag_kobling_konsistent;
ALTER TABLE oppdrag ADD CONSTRAINT oppdrag_kobling_konsistent CHECK (
       (koblingsstatus = 'KOBLET'
          AND (beslutning_loggpost_id IS NOT NULL
               OR frigivelse_id IS NOT NULL)
          AND oppdragstype <> 'verifikasjon')
    OR (koblingsstatus = 'LEGACY_UKJENT'
          AND beslutning_loggpost_id IS NULL AND frigivelse_id IS NULL)
    OR (koblingsstatus = 'VERIFIKASJON'
          AND beslutning_loggpost_id IS NULL AND frigivelse_id IS NULL
          AND oppdragstype = 'verifikasjon'));

-- ------------------------------------------------------------
-- 6. Kolonnelåsen dekker BEGGE opprinnelses-referansene. Klarsignalet
--    antok at nye referansekolonner ble immutable «gratis» — målt mot
--    basen er låsen en EKSPLISITT liste, og 038 glemte å utvide den:
--    `beslutning_loggpost_id` kunne repekes av runtime etter INSERT.
--    Klassen rettes: begge inn i listen. Kroppen under er den GJELDENDE
--    (pg_get_functiondef fra migrert base) med de to linjene som eneste
--    endring.
CREATE OR REPLACE FUNCTION oppdrag_kolonnelaas()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.tenant IS DISTINCT FROM OLD.tenant
       OR NEW.unntak_id IS DISTINCT FROM OLD.unntak_id
       OR NEW.loggpost_id IS DISTINCT FROM OLD.loggpost_id
       OR NEW.repair_operation_id IS DISTINCT FROM OLD.repair_operation_id
       -- 056: opprinnelses-referansene er fødselsattributter, alle tre.
       OR NEW.beslutning_loggpost_id IS DISTINCT FROM OLD.beslutning_loggpost_id
       OR NEW.frigivelse_id IS DISTINCT FROM OLD.frigivelse_id
       OR NEW.oppdragstype IS DISTINCT FROM OLD.oppdragstype
       OR NEW.handling IS DISTINCT FROM OLD.handling
       OR NEW.eiermodul IS DISTINCT FROM OLD.eiermodul
       OR NEW.payload_kryptert IS DISTINCT FROM OLD.payload_kryptert
       OR NEW.key_id IS DISTINCT FROM OLD.key_id
       OR NEW.alg IS DISTINCT FROM OLD.alg
       OR NEW.nonce IS DISTINCT FROM OLD.nonce
       OR NEW.utforelsesfrist IS DISTINCT FROM OLD.utforelsesfrist
       OR NEW.evidensfrist IS DISTINCT FROM OLD.evidensfrist
       OR NEW.opprettet IS DISTINCT FROM OLD.opprettet THEN
        RAISE EXCEPTION 'oppdrag: kun status-, owner- og kvitteringsfelter kan endres';
    END IF;
    IF NEW.owner_generation < OLD.owner_generation THEN
        RAISE EXCEPTION 'oppdrag: owner_generation kan aldri reduseres';
    END IF;
    -- CP5 + Codex P1: kontraktbinding/epoch endres KUN av den herdede claim-
    -- funksjonen (eid av disponit_m37_claimer). Runtime har direkte UPDATE på
    -- oppdrag; uten denne current_user-sjekken kunne runtime initialisere en
    -- uclaimet rad med vilkårlig modul/versjon/hash (forfalske bindingen) eller
    -- nulle en stemplet epoch.
    IF current_user <> 'disponit_m37_claimer' AND (
           NEW.modul_id        IS DISTINCT FROM OLD.modul_id
        OR NEW.kontraktversjon IS DISTINCT FROM OLD.kontraktversjon
        OR NEW.kontrakt_hash   IS DISTINCT FROM OLD.kontrakt_hash
        OR NEW.module_epoch    IS DISTINCT FROM OLD.module_epoch) THEN
        RAISE EXCEPTION 'oppdrag: kontraktbinding/epoch settes kun av claim-funksjonen';
    END IF;
    -- ... OG DEN TREDJE OPPRINNELSEN ER IKKE RUNTIMES RAD I DET HELE TATT
    -- (Codex P1, runde 12 på #140). `deploy/staging/migrer.py` gir
    -- runtime-rollen `SELECT, UPDATE ON oppdrag` (PR-006 → 038), og for de
    -- to ELDRE opprinnelsene er det riktig: runtime utfører dem selv, og
    -- statusmaskinen over er hele porten den trenger. Den tredje er en
    -- annen sak — autorisasjonen er en menneskelig signatur, utsendelsen
    -- er IRREVERSIBEL, og `oppdrag_en_per_frigivelse` (§5) gir frigivelsen
    -- NØYAKTIG ETT forsøk. Uten porten her kunne runtime, som med vilje
    -- ikke har EXECUTE på en eneste kjedefunksjon, likevel med rå UPDATE:
    --   * sette et ferskt frigivelsesoppdrag til `kansellert`/`feilet` —
    --     den autoriserte e-posten forsvinner, og raden kan aldri
    --     erstattes fordi frigivelsen alt har brukt sitt ene oppdrag;
    --   * kjøre det `opprettet`→`plukket`→`utfort` og fylle de initielt
    --     tomme kvitteringsfeltene selv — en utsending som ALDRI skjedde
    --     ser sendt ut, og kvitteringen er uforanderlig når den er satt.
    -- Begge er tap av selve påstanden kjeden finnes for, så porten dekker
    -- ALLE endringer på slike rader, ikke bare status og kvittering.
    -- Formen er en TILLATELSESLISTE, ikke en nektelsesliste: runtime-
    -- rollens NAVN er et installasjonsvalg (kjøreren tar det som
    -- argument), mens `disponit_m37_claimer` er husets faste eier av de to
    -- veiene som ER lovlige i dag — `claim_neste_oppdrag` (049) og
    -- `reap_evidensfrister` (§10), begge SECURITY DEFINER — og av
    -- kvitteringsveien CP3 må legge til. Å nekte «disponit» ved navn ville
    -- vært en port som forsvant på enhver installasjon med et annet navn.
    IF OLD.opprinnelse = 'frigivelse'
       AND current_user <> 'disponit_m37_claimer' THEN
        RAISE EXCEPTION 'oppdrag: et frigivelsesoppdrag endres kun av'
            ' kjedens egen eier (fikk %) — utsendelsen er irreversibel og'
            ' frigivelsen har nøyaktig ett forsøk', current_user
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Write-once (gjelder også claim-funksjonen på en reclaim): satt → frosset.
    IF OLD.modul_id IS NOT NULL AND (
           NEW.modul_id        IS DISTINCT FROM OLD.modul_id
        OR NEW.kontraktversjon IS DISTINCT FROM OLD.kontraktversjon
        OR NEW.kontrakt_hash   IS DISTINCT FROM OLD.kontrakt_hash) THEN
        RAISE EXCEPTION 'oppdrag: kontraktbindingen er frosset når den er satt';
    END IF;
    -- module_epoch er monoton OG kan ikke nulles etter at den er satt (Codex P2:
    -- non-NULL→NULL fjernet fencing-generasjonen uten feil).
    IF OLD.module_epoch IS NOT NULL
       AND (NEW.module_epoch IS NULL OR NEW.module_epoch < OLD.module_epoch) THEN
        RAISE EXCEPTION 'oppdrag: module_epoch kan aldri reduseres/nulles';
    END IF;
    IF OLD.kvittering IS NOT NULL
       AND (NEW.kvittering IS DISTINCT FROM OLD.kvittering
            OR NEW.kvittering_signatur IS DISTINCT FROM OLD.kvittering_signatur
            OR NEW.resultathash IS DISTINCT FROM OLD.resultathash) THEN
        RAISE EXCEPTION 'oppdrag: kvitteringen er uforanderlig når den først er lagret';
    END IF;
    IF NOT (
        (OLD.status = 'opprettet' AND NEW.status IN ('plukket','kansellert','feilet')) OR
        (OLD.status = 'plukket'   AND NEW.status IN ('utfort','feilet','opprettet')) OR
        (OLD.status = NEW.status)
    ) THEN
        RAISE EXCEPTION 'oppdrag: ulovlig statusovergang % -> %', OLD.status, NEW.status;
    END IF;
    IF OLD.status IN ('utfort','feilet','kansellert') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'oppdrag: % er terminal', OLD.status;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        NEW.status_ts := now();
    END IF;
    RETURN NEW;
END $$;

-- ------------------------------------------------------------
-- 7. Kjedens funksjoner. Samme eier som outbox-familiens opphavsveier
--    (disponit_m37_claimer) — tabellene tar INSERT kun gjennom dem.
SET LOCAL ROLE disponit_m37_claimer;

-- 7a. Ny listeversjon. Roten (forrige=NULL) og barn samme vei; lineariteten
--     og serie-bindingen håndheves av indeksene/FK-ene i §1 — funksjonen
--     validerer ingenting indeksene alt beviser, den setter bare feltene
--     selv (aldri fra request-kroppen).
CREATE FUNCTION opprett_utsendingsliste(
    p_tenant TEXT, p_utkast_serie UUID, p_forrige UUID, p_oppdrag_id BIGINT,
    p_listetype TEXT, p_malversjon TEXT, p_innhold_hash TEXT, p_antall INT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID := gen_random_uuid(); v_forelder_oppdrag BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'opprett_utsendingsliste');
    -- SERIEN PEKER PÅ ÉN EVALUERING (Cursor P2 på #140, runde 3): uten
    -- dette kunne et barn i en «lineær» serie likevel adoptere en ANNEN
    -- fullført evaluering enn forelderen sin — proveniensen ville
    -- forgrene seg inni en kjede klarsignalet beskriver som lineær.
    -- Forelderen eier evalueringspekeren; barnet arver den, det velger
    -- den ikke.
    IF p_forrige IS NOT NULL THEN
        SELECT oppdrag_id INTO v_forelder_oppdrag
          FROM public.utsendingsliste
         WHERE tenant = p_tenant AND liste_id = p_forrige;
        IF FOUND AND v_forelder_oppdrag IS DISTINCT FROM p_oppdrag_id THEN
            RAISE EXCEPTION 'opprett_utsendingsliste: barn må peke på'
                ' samme evalueringsoppdrag som forelderen (%), ikke %',
                v_forelder_oppdrag, p_oppdrag_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
    END IF;
    -- LISTEN PROMOTERER EN FULLFØRT EVALUERING (Codex P1 + Cursor P2 på
    -- #140, runde 2). FK-en på (tenant, oppdrag_id) sier bare at
    -- oppdraget finnes hos tenanten. Den skiller verken
    --   * RETNING — et frigivelsesoppdrag kunne startet en ny liste, og
    --     kjeden ville sirklet inn i seg selv, eller
    --   * PROVENIENS — feil oppdragstype, en kjøring som fortsatt går,
    --     eller en som feilet/ble kansellert, kunne bære en liste videre
    --     gjennom signatur og frigivelse som en gyldig kjede.
    -- Klarsignalet er entydig på begge: ÉN oppdragstype for evalueringen
    -- (`rekruttering.evaluering`, §1), og «avbrutt kjøring → INGEN
    -- promotert liste» (§7, port 28). Måles her, før raden finnes —
    -- etterpå er listen signerbar, og en signert liste er sendbar.
    PERFORM 1 FROM public.oppdrag o
     WHERE o.tenant = p_tenant AND o.id = p_oppdrag_id
       AND o.oppdragstype = 'rekruttering.evaluering'
       AND o.status = 'utfort'
       AND o.opprinnelse IN ('beslutning', 'm37_reparasjon');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'opprett_utsendingsliste: oppdrag % er ikke en'
            ' FULLFØRT rekruttering.evaluering hos % (kjeden starter aldri'
            ' i et frigivelsesoppdrag, og en avbrutt kjøring promoteres'
            ' aldri)', p_oppdrag_id, p_tenant
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    INSERT INTO public.utsendingsliste (tenant, liste_id, utkast_serie,
        forrige_liste_id, oppdrag_id, listetype, malversjon, innhold_hash,
        antall)
    VALUES (p_tenant, v_id, p_utkast_serie, p_forrige, p_oppdrag_id,
            p_listetype, p_malversjon, p_innhold_hash, p_antall);
    RETURN v_id;
END $$;

-- 7b. Signaturen. SP-2 på operasjonsnøkkelen, og HELE innholdet er
--     materielt — også signataren (055-lærdommen: aktøren er materiell;
--     to parter kan ikke begge tro de signerte).
CREATE FUNCTION signer_utsendingsliste(
    p_tenant TEXT, p_liste_id UUID, p_signatar TEXT, p_nokkel TEXT)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE l RECORD; s RECORD;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'signer_utsendingsliste');
    -- SNAPSHOTKRAVET (Codex P2, runde 7 på #140) — samme klasse som
    -- §7c, og formen der er den ratifiserte: dette er ikke et nytt
    -- formforsøk, men den samme porten på det gjenstående stedet.
    -- Replay-løftet under («samme nøkkel + samme innhold ⇒ no-op») er
    -- utledet av LESNINGER i BEGGE ender: nøkkel-oppslaget FØR
    -- innsettingen, og gjenlesningen i `unique_violation`-armen.
    -- PostgreSQL oversetter ikke et unik-brudd mot en samtidig
    -- COMMITTET rad til en serialiseringsfeil — taperen får 23505 også
    -- under REPEATABLE READ og SERIALIZABLE. Subtransaksjonen rulles
    -- tilbake, men transaksjonens snapshot står fast fra første
    -- setning: gjenlesningen ser ikke vinnerens signatur, armen faller
    -- til `RAISE`, og et helt legitimt replay får en feil der
    -- kontrakten lover en no-op. `read uncommitted` er med av samme
    -- grunn som i §7c (PostgreSQL behandler nivået som READ COMMITTED).
    IF current_setting('transaction_isolation')
       NOT IN ('read committed', 'read uncommitted') THEN
        RAISE EXCEPTION 'signer_utsendingsliste: krever READ COMMITTED'
            ' (fikk %) — replay-løftet er utledet av LESNINGER, og et'
            ' fastholdt snapshot gjør dem blinde for en samtidig'
            ' committet signatur',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    -- FERDIG REPLAY AVGJØRES FØR REAUTORISERINGEN (Codex P2, runde 11 på
    -- #140). Medlemskapsporten under er nå en LÅS, og den svarer på
    -- spørsmålet «får denne signataren signere NÅ». Et identisk replay
    -- stiller ikke det spørsmålet: signaturen STÅR alt, den er
    -- append-only, og den autoriserer utsendingen uansett hva som skjer
    -- med medlemskapet etterpå. Kom porten først, ville den vanlige
    -- tvetydige committen — signaturen landet, svaret gikk tapt, brukeren
    -- ble deaktivert i mellomtiden — gitt retryet `insufficient_privilege`
    -- der kontrakten lover no-op. Kalleren kan da ikke skille en AVVIST
    -- signatur fra en VELLYKKET: den ene betyr «ikke send», den andre
    -- «allerede autorisert», og forskjellen er irreversibel e-post.
    -- SNEVERT MED VILJE: bare den nøyaktig identiske raden (samme liste,
    -- samme signatar) svares her. Alt annet — ukjent nøkkel, eller nøkkel
    -- gjenbrukt med annet innhold — faller gjennom til låsen og møter
    -- nøyaktig de samme dommene i samme rekkefølge som før.
    SELECT * INTO s FROM public.utsendingssignatur
     WHERE tenant = p_tenant AND operasjonsnokkel = p_nokkel;
    IF FOUND AND s.liste_id IS NOT DISTINCT FROM p_liste_id
             AND s.signatar IS NOT DISTINCT FROM p_signatar THEN
        RETURN;                       -- identisk replay: ferdig, no-op
    END IF;
    -- SIGNATAREN ER AUTORISASJONEN (Codex P1 + Cursor P1, runde 2).
    -- FK-en mot `brukeridentitet` er GLOBAL: den sier bare at strengen er
    -- en kjent bruker et sted i installasjonen. Runtime har EXECUTE her og
    -- INSERT på `brukeridentitet` (010) — uten denne porten kunne den
    -- tilskrive signaturen en bruker i en ANNEN tenant, en avskrudd bruker
    -- eller en identitet den nettopp fabrikkerte, og senderen ville
    -- deretter sendt irreversibel e-post på et menneske som aldri sa ja.
    -- 043-doktrinen: den ene autorisasjonsinngangen runtime IKKE kan
    -- skrive er medlemskapet (OIDC-forvaltet, runtime har kun SELECT).
    -- ÆRLIG OM RESTEN: en kompromittert runtime kan fortsatt lese
    -- medlemskapstabellen og UTGI SEG FOR et aktivt medlem. Den resten er
    -- ikke lukkbar herfra (den forutsetter at basen kan verifisere
    -- konvolutten) — presis samme avgrensning som 043 skrev ned. Rolle-
    -- og scope-nivået hører til flatens egen autorisasjon (CP3), der
    -- signeringsrollen defineres; her bindes det basen faktisk eier.
    -- LÅST, IKKE BARE LEST (Codex P1, runde 10 på #140). En ren lesning er
    -- et øyeblikksbilde: en administrator kan skru av dette medlemskapet
    -- samtidig, og deaktiveringen kan committe ETTER lesningen og FØR
    -- signaturen står i tabellen. Da er en tilbakekalt bruker skrevet inn
    -- som den som autoriserte en irreversibel utsending — append-only, så
    -- den raden kan aldri rettes. Låsen tas gjennom `laas_godkjenner()`
    -- (013), ikke med en egen `FOR UPDATE` her, og grunnen er den HARDE
    -- grensen 019 alt skrev ned: PostgreSQL krever UPDATE-privilegium for
    -- ENHVER radlåsklausul, også `FOR SHARE`, og `disponit_m37_claimer` —
    -- som eier denne funksjonen — har kun SELECT på `brukermedlemskap`
    -- (043 §6b). Den skal ALDRI kunne skrive et medlemskap: det er nettopp
    -- uskrivbarheten som gjør medlemskapet til autorisasjonsinngangen i
    -- doktrinen over. `laas_godkjenner` er SECURITY DEFINER eid av
    -- tabellens egen eier og finnes for nøyaktig dette kappløpet (013s
    -- reautorisering av policygodkjennere, 019s attestasjoner); `FOR
    -- UPDATE` konflikter med tilbakekallingens egen radlås, så de
    -- serialiseres og deaktiveringen kan ikke committe før denne
    -- transaksjonen er ferdig. Funksjonen kjører som SIN eier og ikke som
    -- vår, men `krev_tenantkontekst` over har alt bundet `disponit.tenant`
    -- til `p_tenant`, så RLS-vinduet er det samme lesningen hadde.
    PERFORM 1 FROM public.laas_godkjenner(p_tenant, p_signatar);
    IF NOT FOUND THEN
        -- ... OG OPPSLAGET GJENTAS ETTER VENTINGEN (Codex P2, runde 12 på
        -- #140). Oppslaget over er tatt FØR låsen, og en lås er et
        -- VENTEPUNKT: verden kan ha flyttet seg mens vi sto i køen.
        -- Tre transaksjoner, og rekkefølgen er FIFO på medlemskapsraden:
        -- originalen signerer og holder låsen; en deaktivering stiller
        -- seg i kø; dette replayet gjør sitt tidlige oppslag, ser ingen
        -- signatur (originalen er ucommittet) og stiller seg BAK
        -- deaktiveringen. Originalen committer, deaktiveringen får låsen
        -- og committer — og da våkner vi til et medlemskap som ikke
        -- lenger er aktivt, enda den identiske signaturen NÅ står.
        -- Dommen ville vært `insufficient_privilege` på et retry der
        -- kontrakten lover no-op: nøyaktig feilen runde 11 lukket, bare
        -- med kappløpet i stedet for klokken. Under READ COMMITTED —
        -- som porten over KREVER — får denne setningen et ferskt
        -- snapshot, så originalens rad er synlig her.
        -- Samme predikat, samme snevre likhet som det tidlige oppslaget:
        -- bare den nøyaktig identiske raden svares. Ukjent nøkkel, eller
        -- nøkkel med annet innhold, faller fortsatt til RAISE-en under.
        SELECT * INTO s FROM public.utsendingssignatur
         WHERE tenant = p_tenant AND operasjonsnokkel = p_nokkel;
        IF FOUND AND s.liste_id IS NOT DISTINCT FROM p_liste_id
                 AND s.signatar IS NOT DISTINCT FROM p_signatar THEN
            RETURN;               -- replayet ble ferdig mens vi ventet
        END IF;
        RAISE EXCEPTION 'signer_utsendingsliste: signatar % mangler'
            ' aktivt medlemskap i %', p_signatar, p_tenant
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    SELECT * INTO l FROM public.utsendingsliste
     WHERE tenant = p_tenant AND liste_id = p_liste_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'signer_utsendingsliste: ukjent liste %', p_liste_id
            USING ERRCODE = 'no_data_found';
    END IF;
    SELECT * INTO s FROM public.utsendingssignatur
     WHERE tenant = p_tenant AND operasjonsnokkel = p_nokkel;
    IF FOUND THEN
        IF s.liste_id IS DISTINCT FROM p_liste_id
           OR s.signatar IS DISTINCT FROM p_signatar THEN
            RAISE EXCEPTION 'signer_utsendingsliste: nøkkel % gjenbrukt med'
                ' annet innhold', p_nokkel
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        RETURN;                                     -- identisk replay: no-op
    END IF;
    -- Innholdet signaturen binder settes fra LISTEN, aldri fra kallet:
    -- hash og serie er versjonens egne, og FK-ene i §2 beviser sammenhengen.
    -- Kappløpstaperen (Cursor P2 på #140): to samtidige kall passerer
    -- begge nøkkel-lesningen over, og taperen treffer unik-bruddet.
    -- Identisk replay er dokumentert no-op, så taperen skal inn i SAMME
    -- dom som en sekvensiell replay — 038s mønster.
    BEGIN
        INSERT INTO public.utsendingssignatur (tenant, liste_id,
            utkast_serie, innhold_hash, signatar, operasjonsnokkel)
        VALUES (p_tenant, p_liste_id, l.utkast_serie, l.innhold_hash,
                p_signatar, p_nokkel);
    EXCEPTION WHEN unique_violation THEN
        SELECT * INTO s FROM public.utsendingssignatur
         WHERE tenant = p_tenant AND operasjonsnokkel = p_nokkel;
        IF NOT FOUND
           OR s.liste_id IS DISTINCT FROM p_liste_id
           OR s.signatar IS DISTINCT FROM p_signatar THEN
            -- Bruddet var ikke nøkkelens (listen/serien alt signert av en
            -- annen operasjon) eller nøkkelen bærer annet innhold — det
            -- skal høres, aldri sluke.
            RAISE;
        END IF;
        -- vinnerens rad er identisk med dette kallet: no-op.
    END;
END $$;

-- 7c. Frigivelsen: én mottaker om gangen, idempotent på (liste, mottaker)
--     — unik-nøkkelen i §3 serialiserer, og FK-kjeden krever signaturen.
CREATE FUNCTION frigi_utsendelse(
    p_tenant TEXT, p_liste_id UUID, p_mottaker_ref TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE s RECORD; v_id UUID := gen_random_uuid(); v_eksisterende UUID;
        v_frigitt INT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'frigi_utsendelse');
    -- SNAPSHOTKRAVET (Codex på #140, runde 4). Rotårsaken bak de tre
    -- rundene på denne funksjonen: BEGGE løftene her — taket mot det
    -- signerte `antall`, og «samme mottaker gir samme id» — er utledet av
    -- en LESNING (`count(*)`, og gjenlesningen etter `ON CONFLICT DO
    -- NOTHING`), ikke av en skrivekonflikt. En lesning ser bare det
    -- transaksjonens snapshot inneholder. Advisory-låsen serialiserer
    -- UTFØRELSEN, men den friskner ikke opp et snapshot: under REPEATABLE
    -- READ tas snapshotet ved transaksjonens første setning, så to kall
    -- som begge startet før den første committet, teller begge det gamle
    -- tallet og kan begge sette inn — antall=1 gir to irreversible
    -- e-poster. Samme snapshot gjør at gjenlesningen til slutt kan bomme
    -- på vinnerens rad og returnere NULL der kontrakten lover en id.
    --
    -- READ COMMITTED tar ferskt snapshot PER setning, så både tellingen
    -- og gjenlesningen etter låsen ser vinneren.
    --
    -- ... OG SERIALIZABLE HOLDER IKKE (Cursor P1 på #140, runde 5 — svaret
    -- på spørsmålet runde 4 selv stilte i tråden, med den fallbacken som
    -- da ble varslet). Runde 4 antok at SSI redder nivået. SSI redder
    -- OVERSENDINGEN (rw-syklusen mellom tellingen og en samtidig
    -- innsetting av en ANNEN mottaker avbrytes ved COMMIT), men den redder
    -- ikke REPLAY-IDEN: to førstegangskall for SAMME mottaker gir taperen
    -- et unik-brudd som `ON CONFLICT DO NOTHING` svelger uten feil, og
    -- gjenlesningen etterpå leser fortsatt taperens EGET snapshot — der
    -- vinnerens rad ikke finnes. Funksjonen returnerer da NULL, stille,
    -- der kontrakten lover «samme mottaker → samme id», og transaksjonen
    -- kan committe fint fordi taperen aldri skrev noe.
    --
    -- Nivåkravet er derfor det snevre og ærlige: READ COMMITTED. Det er
    -- det ENESTE nivået der hver setning ser ferske data, og begge løftene
    -- her er utledet av lesninger.
    --
    -- K1: alternativet — en skrivekonfliktende teller på listeraden — ville
    -- krevd et hull i append-only-vakten (`avvis_endring`) og er ny maskin,
    -- altså egen PR. Se tråden.
    -- `read uncommitted` er med fordi PostgreSQL BEHANDLER det som READ
    -- COMMITTED (nivået finnes bare som synonym); å avvise det ville vært
    -- en falsk avvisning på en irreversibel vei.
    IF current_setting('transaction_isolation')
       NOT IN ('read committed', 'read uncommitted') THEN
        RAISE EXCEPTION 'frigi_utsendelse: krever READ COMMITTED (fikk %)'
            ' — både telleporten mot det signerte antallet og'
            ' idempotensoppslaget er utledet av LESNINGER, og et fastholdt'
            ' snapshot gjør dem blinde for samtidige frigivelser',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    -- Signaturen OG listens `antall` i samme oppslag: tallet mennesket
    -- fikk se i signaturdialogen («Dette sender N e-poster. Kan ikke
    -- angres.») bor på listeversjonen signaturen binder.
    SELECT sg.innhold_hash, sg.utkast_serie, l.antall INTO s
      FROM public.utsendingssignatur sg
      JOIN public.utsendingsliste l
        ON l.tenant = sg.tenant AND l.liste_id = sg.liste_id
     WHERE sg.tenant = p_tenant AND sg.liste_id = p_liste_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'frigi_utsendelse: liste % er ikke signert',
            p_liste_id USING ERRCODE = 'no_data_found';
    END IF;
    -- Allerede frigitt for DENNE mottakeren? Da er svaret gitt, og
    -- replayet skal aldri møte telleporten under — en liste som står på
    -- taket må fortsatt kunne svare idempotent på et retry.
    SELECT frigivelse_id INTO v_eksisterende
      FROM public.utsendingsfrigivelse
     WHERE tenant = p_tenant AND liste_id = p_liste_id
       AND mottaker_ref = p_mottaker_ref;
    IF v_eksisterende IS NOT NULL THEN
        RETURN v_eksisterende;
    END IF;
    -- DET SIGNERTE ANTALLET ER ET TAK (Codex P1, runde 2). Unikheten på
    -- (liste, mottaker) hindret bare DUBLETTER — den sa ingenting om hvor
    -- MANGE forskjellige mottakere senderen kunne frigi. En liste
    -- presentert som «N e-poster» kunne dermed gi flere enn N irreversible
    -- utsendelser, forbi til og med skjemaets 5000-grense, uten at noe
    -- menneske signerte for det. Taket er en del av signaturens løfte.
    --
    -- Serialisert med en advisory-lås per (tenant, liste) — 014s mønster.
    -- Uten den kunne to samtidige kall begge lese `antall - 1` og begge
    -- sette inn: en ren count-så-INSERT er nøyaktig det TOCTOU-et runde 1
    -- lukket andre steder i denne filen. Låsen krever ingen rettighet (i
    -- motsetning til `SELECT ... FOR UPDATE`, som ville krevd UPDATE på en
    -- append-only tabell) og faller ved commit.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'm57:frigi:' || p_tenant || ':' || p_liste_id::text, 0));
    -- GJENLES ETTER LÅSEN (Codex på #140, runde 3): to FØRSTEGANGS-kall
    -- for SAMME mottaker kan begge bomme på oppslaget over (ingen rad
    -- ennå), og taperen ville da møtt telleporten under i stedet for
    -- replay-svaret — presis når taket er lite (f.eks. antall=1) og
    -- vinneren alt har committet før taperen får låsen. Uten denne
    -- gjenlesningen fikk et helt legitimt samtidig FØRSTE forsøk et
    -- avvist svar der idempotens-kontrakten lovte samme id.
    SELECT frigivelse_id INTO v_eksisterende
      FROM public.utsendingsfrigivelse
     WHERE tenant = p_tenant AND liste_id = p_liste_id
       AND mottaker_ref = p_mottaker_ref;
    IF v_eksisterende IS NOT NULL THEN
        RETURN v_eksisterende;
    END IF;
    SELECT count(*) INTO v_frigitt FROM public.utsendingsfrigivelse
     WHERE tenant = p_tenant AND liste_id = p_liste_id;
    IF v_frigitt >= s.antall THEN
        RAISE EXCEPTION 'frigi_utsendelse: liste % er signert for %'
            ' mottakere, og % er alt frigitt — en ny mottaker krever en ny'
            ' signert versjon', p_liste_id, s.antall, v_frigitt
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Idempotent UNDER kappløp (Cursor P2 på #140): SELECT-så-INSERT lot
    -- taperen få unik-bruddet i fanget. `ON CONFLICT DO NOTHING` +
    -- gjenlesning gir begge kallerne VINNERENS id — 038s mønster
    -- (`sikre_sak_for_oppdrag`), i insert-form.
    INSERT INTO public.utsendingsfrigivelse (tenant, frigivelse_id,
        liste_id, innhold_hash, utkast_serie, mottaker_ref)
    VALUES (p_tenant, v_id, p_liste_id, s.innhold_hash, s.utkast_serie,
            p_mottaker_ref)
    ON CONFLICT (tenant, liste_id, mottaker_ref) DO NOTHING;
    SELECT frigivelse_id INTO v_eksisterende
      FROM public.utsendingsfrigivelse
     WHERE tenant = p_tenant AND liste_id = p_liste_id
       AND mottaker_ref = p_mottaker_ref;
    RETURN v_eksisterende;
END $$;

-- 7d. Eneste vei til opprinnelse='frigivelse' (klarsignal §2): setter
--     opprinnelse og frigivelse_id SELV, og krever at frigivelsesraden
--     finnes — CHECK-en i §5 håndhever formen ved direkte DML uansett.
CREATE FUNCTION opprett_frigivelsesoppdrag(
    p_tenant TEXT, p_frigivelse_id UUID, p_oppdragstype TEXT,
    p_handling TEXT, p_eiermodul TEXT, p_payload BYTEA, p_key_id TEXT,
    p_nonce BYTEA, p_utforelsesfrist TIMESTAMPTZ, p_evidensfrist TIMESTAMPTZ)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'opprett_frigivelsesoppdrag');
    -- SNAPSHOTKRAVET (Codex P2, runde 7 på #140) — tredje og siste
    -- stedet i denne filen der et løfte er utledet av en LESNING.
    -- Retry-løftet i `unique_violation`-armen under («samme frigivelse +
    -- samme kontrakt ⇒ vinnerens oppdrag-id») leser oppdraget PÅ NYTT
    -- etter bruddet. Under REPEATABLE READ/SERIALIZABLE står snapshotet
    -- fast fra transaksjonens første setning, så et retry som startet
    -- FØR vinneren committet finner ingen rad — hverken i det snevrede
    -- materialitetsoppslaget eller i `EXISTS`-en som skiller «annet
    -- innhold» fra «bruddet var ikke frigivelsens». Armen faller da til
    -- bar `RAISE`, og den dokumenterte idempotente retry-veien er
    -- brutt nettopp der den finnes for: etter en tvetydig commit.
    -- Utsendelsen er irreversibel; et retry som får en feil den ikke
    -- skulle hatt, blir prøvd igjen av mennesker.
    IF current_setting('transaction_isolation')
       NOT IN ('read committed', 'read uncommitted') THEN
        RAISE EXCEPTION 'opprett_frigivelsesoppdrag: krever READ'
            ' COMMITTED (fikk %) — retryets gjenlesning etter'
            ' unik-bruddet er utledet av en LESNING, og et fastholdt'
            ' snapshot gjør den blind for det vinnende oppdraget',
            current_setting('transaction_isolation')
            USING ERRCODE = 'invalid_transaction_state';
    END IF;
    -- FRIGIVELSEN AUTORISERER ÉN KONTRAKT, IKKE HVILKEN SOM HELST (Codex P1
    -- runde 6 + Cursor P1 runde 5 på #140 — samme funn fra begge
    -- reviewerne). Funksjonen setter `opprinnelse` og `frigivelse_id` selv,
    -- men lot kalleren velge HVILKEN outbox-jobb signaturen skulle
    -- autorisere. `claim_neste_oppdrag` plukker på eiermodul +
    -- handlingsprefiks, så en kompromittert eller feilende
    -- `disponit_varselsender` kunne føde et KOBLET, frigivelses-bærende
    -- oppdrag i en ANNEN moduls kø — som den modulen så dekrypterer og
    -- utfører. Mennesket signerte en utsendingsliste, ikke en
    -- WCAG-kontroll.
    --
    -- Samme doktrine som at `opprinnelse` aldri kommer fra request:
    -- argumentene beholdes (signaturen, og dermed grants/kallere, er
    -- urørt), men de må BESKRIVE ATS-utsendelsen. Trippelen er den samme
    -- som klarsignalet, testene og SP-10-prøvekjøringen alt bruker.
    --
    -- Skal en ANNEN utsendingsvariant (påminnelse, tilbaketrekking, en
    -- annen kanal) fødes av en frigivelse senere, er det en utvidelse av
    -- listen her — en bevisst migrasjon med sin egen gjennomgang, ikke noe
    -- senderen kan velge i farten.
    IF p_oppdragstype IS DISTINCT FROM 'rekruttering.utsending'
       OR p_handling IS DISTINCT FROM 'rekruttering.utsending'
       OR p_eiermodul IS DISTINCT FROM 'm57_ats' THEN
        RAISE EXCEPTION 'opprett_frigivelsesoppdrag: (%, %, %) er ikke en'
            ' godkjent utsendingskontrakt — en frigivelse autoriserer kun'
            ' rekruttering.utsending/rekruttering.utsending/m57_ats',
            p_oppdragstype, p_handling, p_eiermodul
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    PERFORM 1 FROM public.utsendingsfrigivelse
     WHERE tenant = p_tenant AND frigivelse_id = p_frigivelse_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'opprett_frigivelsesoppdrag: ukjent frigivelse %',
            p_frigivelse_id USING ERRCODE = 'no_data_found';
    END IF;
    -- EN DØDFØDT JOBB SKAL IKKE FØDES (Codex P2, runde 5 på #140).
    -- `claim_neste_oppdrag` plukker kun rader med `utforelsesfrist >
    -- now()`, og `oppdrag_en_per_frigivelse` gir frigivelsen nøyaktig ETT
    -- forsøk: et oppdrag opprettet med en alt utløpt frist er derfor
    -- uplukkbart fra første sekund, OG blokkerer det gyldige oppdraget
    -- frigivelsen aldri får. Reaperen i §10 rydder opp i rader som løper
    -- ut etterpå; denne porten hindrer at de i det hele tatt oppstår.
    --
    -- Porten står FØR innsettingen, også på retry-veien: fristene er med
    -- vilje utenfor materialiteten (se under) fordi et legitimt retry
    -- regner dem PÅ NYTT. Et retry som sender inn en utløpt frist beskriver
    -- en jobb som ikke kan utføres, og skal høre det — ikke få et
    -- uplukkbart oppdrag tilbake som om alt var i orden.
    --
    -- KLOKKEN ER VEGGKLOKKEN, IKKE TRANSAKSJONENS FØDSELSTID (Codex P2,
    -- runde 9 på #140). `now()` er `transaction_timestamp()`: den fryses
    -- ved transaksjonens FØRSTE setning og står stille resten av
    -- transaksjonen. En sender som åpner transaksjonen, gjør sitt arbeid,
    -- og først deretter kaller hit, målte da fristen mot et tidspunkt som
    -- alt hadde passert — en frist som var i fremtiden ved BEGIN, men
    -- utløpt ved kallet, slapp gjennom porten og fødte nøyaktig det
    -- dødfødte oppdraget porten finnes for å hindre. Verre enn ingen
    -- port: frigivelsens ENE forsøk (`oppdrag_en_per_frigivelse`) er
    -- brukt opp på et oppdrag som aldri kan plukkes.
    --
    -- `clock_timestamp()` leser veggklokken der og da, altså den faktiske
    -- kalltiden, og er den eneste av de tre som svarer på spørsmålet
    -- porten stiller: er fristen utløpt NÅ?
    IF p_utforelsesfrist <= clock_timestamp() THEN
        RAISE EXCEPTION 'opprett_frigivelsesoppdrag: utforelsesfrist % er'
            ' alt utløpt — oppdraget ville aldri kunne plukkes',
            p_utforelsesfrist USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- Én frigivelse -> ett oppdrag (indeksen over håndhever det):
    -- kappløpstaperen får VINNERENS oppdrag tilbake — en irreversibel
    -- utsending skal aldri kunne dobles av et retry (Cursor P1 på #140).
    BEGIN
        INSERT INTO public.oppdrag (tenant, oppdragstype, handling,
            eiermodul, payload_kryptert, key_id, nonce, utforelsesfrist,
            evidensfrist, frigivelse_id, koblingsstatus, opprinnelse)
        VALUES (p_tenant, p_oppdragstype, p_handling, p_eiermodul,
                p_payload, p_key_id, p_nonce, p_utforelsesfrist,
                p_evidensfrist, p_frigivelse_id, 'KOBLET', 'frigivelse')
        RETURNING id INTO v_id;
    EXCEPTION WHEN unique_violation THEN
        -- RUNDE 3-BESLUTNING (K2 utløst på #140, ansvarlig valgte
        -- eksplisitt): materialiteten snevres til de DETERMINISTISKE
        -- feltene. `db/kryptering.py` genererer en FERSK tilfeldig nonce
        -- for HVER kryptering (AES-GCM) — et legitimt retry som
        -- krypterer identisk klartekst på nytt (etter en tvetydig
        -- commit/timeout) ville ALDRI fått samme `payload_kryptert`/
        -- `nonce` som forrige forsøk. Runde 2s fulle byte-likhet målte
        -- derfor feil ting: den avviste nøyaktig det retryet som skulle
        -- godkjennes (Codex + Cursor, runde 3).
        --
        -- Ekte binding til det SIGNERTE innholdet er en annen sak (Funn 8,
        -- utsatt fra runde 2 — krever et per-mottaker-manifest ELLER en
        -- kalleroppgitt digest, altså ny maskin under K1) og står fortsatt
        -- åpen som eget spørsmål i tråden. Denne porten løser bare
        -- kappløps-/retry-klassen: samme frigivelse + samme
        -- jobbtype/håndterer/eiermodul/nøkkel er samme logiske utsendelse,
        -- uansett chiffertekstbyte. Fristene er utenfor materialiteten av
        -- samme grunn som før: et legitimt retry regner klokkefrister på
        -- nytt.
        SELECT id INTO v_id FROM public.oppdrag o
         WHERE o.tenant = p_tenant AND o.frigivelse_id = p_frigivelse_id
           AND o.oppdragstype = p_oppdragstype
           AND o.handling = p_handling
           AND o.eiermodul = p_eiermodul
           AND o.key_id = p_key_id;
        IF NOT FOUND THEN
            IF EXISTS (SELECT 1 FROM public.oppdrag
                        WHERE tenant = p_tenant
                          AND frigivelse_id = p_frigivelse_id) THEN
                RAISE EXCEPTION 'opprett_frigivelsesoppdrag: frigivelse %'
                    ' bærer alt et oppdrag med ANNET innhold — et retry'
                    ' beskriver samme oppdrag eller feiler', p_frigivelse_id
                    USING ERRCODE = 'invalid_parameter_value';
            END IF;
            RAISE;                       -- bruddet var ikke frigivelsens
        END IF;
    END;
    RETURN v_id;
END $$;

-- FUNKSJONSRETTIGHETENE EIES AV EIEREN (Cursor-runden på #140 målte
-- klassen): PostgreSQL gir nye funksjoner EXECUTE til PUBLIC ved
-- fødselen, og både REVOKE og GRANT på claimer-eide funksjoner fra
-- migrator er virkningsløse/ulovlige. Alt funksjonsrettslig skjer derfor
-- HER, under eierrollen, før fullmakten legges ned: PUBLIC trekkes, og
-- API-veiene (liste + signering) gis runtime; utsendingsveien (frigivelse
-- + frigivelsesoppdrag) gis driftsrollen NÅR den finnes — ingen
-- else-arm, for et fallback til runtime ville flytt videre til alt som
-- arver runtime (plan-arbeideren), og kjøreren (migrer.py) er den
-- autoritative rettighetskilden lokalt og i test.
REVOKE ALL ON FUNCTION opprett_utsendingsliste(TEXT, UUID, UUID, BIGINT,
    TEXT, TEXT, TEXT, INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION signer_utsendingsliste(TEXT, UUID, TEXT, TEXT)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION frigi_utsendelse(TEXT, UUID, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION opprett_frigivelsesoppdrag(TEXT, UUID, TEXT, TEXT,
    TEXT, BYTEA, TEXT, BYTEA, TIMESTAMPTZ, TIMESTAMPTZ) FROM PUBLIC;

-- ... OG RUNTIME-ROLLEN NEVNES IKKE HER I DET HELE TATT (Codex P1, runde
-- 13 på #140; runde 5 tok bare halve funnet). `disponit` er LOKAL-/
-- TESTNAVNET på runtime-rollen, og `deploy/staging/migrer.py` tar navnet
-- som ARGUMENT. En literal grant her har derfor to utfall på en
-- installasjon med et annet navn:
--   * rollen finnes ikke → hele 056 ruller tilbake, FØR kjøreren rekker
--     `M37_RETTIGHETER_API`. Runde 5 lukket NØYAKTIG dette, med 043 §14bs
--     `IF EXISTS`-form;
--   * rollen finnes som en urelatert eller UTRANGERT `disponit`-
--     innlogging → betingelsen er sann, og den innloggingen får EXECUTE
--     på både liste-opprettelsen og SIGNERINGEN. Kjøreren tar den aldri
--     igjen: `NULLSTILL`/REVOKE-blokkene gjelder den KONFIGURERTE rollen,
--     ikke alle roller. Betingelsen gjorde altså feiltildelingen STILLE i
--     stedet for å hindre den — en utrangert kreditt kan opprette og
--     signere uforanderlige utsendingslister for alltid.
-- Rotårsaken er at migrasjonen i det hele tatt navngir et
-- installasjonsvalg. Den ENESTE rettighetskilden for runtime er kjørerens
-- parameteriserte `M37_RETTIGHETER_API` (migrer.py: `GRANT EXECUTE ON
-- FUNCTION opprett_utsendingsliste/signer_utsendingsliste TO {rolle}`),
-- og den er autoritativ ved HVER kjøring. Speilingen er derfor fjernet,
-- ikke gjort enda mer betinget; `test_056_navngir_aldri_runtime_rollen`
-- holder den borte.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_varselsender') THEN
        GRANT EXECUTE ON FUNCTION frigi_utsendelse(TEXT, UUID, TEXT)
            TO disponit_varselsender;
        GRANT EXECUTE ON FUNCTION opprett_frigivelsesoppdrag(TEXT, UUID,
            TEXT, TEXT, TEXT, BYTEA, TEXT, BYTEA, TIMESTAMPTZ, TIMESTAMPTZ)
            TO disponit_varselsender;
    END IF;
END $$;

RESET ROLE;

-- ------------------------------------------------------------
-- 8. Tabellrettigheter (tabellene eies av migrator): lesing til runtime
--    og eieren av kjedefunksjonene; INSERT kun gjennom funksjonene.
REVOKE ALL ON utsendingsliste, utsendingssignatur, utsendingsfrigivelse
    FROM PUBLIC;
-- Samme rotårsak og samme fjerning som funksjonsgrantene i §7: kjørerens
-- `GRANT SELECT ON utsendingsliste, utsendingssignatur,
-- utsendingsfrigivelse TO {rolle}` (migrer.py §056-linjen) er den
-- autoritative lesetilgangen for den KONFIGURERTE runtime-rollen. En
-- betinget speiling på lokalnavnet ga en utrangert `disponit`-innlogging
-- varig SELECT på mottakerreferanser og signaturer — lavere alvor enn
-- EXECUTE på signeringen, men nøyaktig samme klasse, så den går samme vei.
GRANT SELECT, INSERT ON utsendingsliste, utsendingssignatur,
    utsendingsfrigivelse TO disponit_m37_claimer;
-- Signaturporten (§7b) LÅSER signatarens medlemskap gjennom
-- `laas_godkjenner()` (013). Granten hører HIT og ikke i rolleblokken over:
-- `laas_godkjenner` eies av migrator, og retten til å gi bort et privilegium
-- følger EIERSKAPET — inne i `SET LOCAL ROLE disponit_m37_claimer` ville
-- denne linjen vært en stille no-op med en advarsel (027-fellen).
-- EXECUTE, ikke DML: eieren av kjedefunksjonene får LÅSE en medlemskapsrad,
-- aldri skrive den. `brukermedlemskap` er OIDC-forvaltet, og 043 §6b ga med
-- vilje kun SELECT.
GRANT EXECUTE ON FUNCTION laas_godkjenner(TEXT, TEXT)
    TO disponit_m37_claimer;

-- ------------------------------------------------------------
-- 9. sikre_sak_for_oppdrag (038 → 041 §16) — KOPI av gjeldende kropp,
--    diff-endret: den tredje opprinnelsen får en REVISJONSLINJE.
--
--    Codex P1 (runde 5 på #140): saksveien for oppdrag utleder loggposten
--    av `coalesce(o.beslutning_loggpost_id, o.loggpost_id)`. Frigivelses-
--    armen i §5 setter BEGGE til NULL — med vilje, for autorisasjonen er
--    signaturen — og dermed ville `unntak`-innsettingen brutt sin egen
--    `loggpost_id NOT NULL` og rullet tilbake HELE den sene kvitteringen
--    eller sikkerhetskonflikten. Den nye armen slår opp linjen gjennom
--    frigivelse → liste → evalueringsoppdrag i stedet.
--
--    `CREATE OR REPLACE` beholder eier og grants; blokken må derfor stå
--    som EIEREN (`disponit_m37_claimer`), som i 041.
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION sikre_sak_for_oppdrag(p_tenant text, p_oppdrag_id bigint, p_arsak text, p_aktor text, p_request_id text)
 RETURNS bigint
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE o RECORD; v_id BIGINT; v_logg BIGINT; v_policy TEXT; v_policy_hash TEXT;
        v_forsok INT := 0;
BEGIN
    -- Tenantporten FØRST — før GUC-ene under settes og før noe leses.
    -- Dette er den API-kallbare formen, og uten porten var `p_tenant`
    -- kallerens frie valg (se `krev_tenantkontekst`).
    PERFORM public.krev_tenantkontekst(p_tenant, 'sikre_sak_for_oppdrag');
    -- Historikktriggeren på unntak krever aktør + request-id i GUC-ene.
    -- Funksjonen FÅR dem eksplisitt — den setter dem selv (LOCAL), så
    -- reaper-/kvitteringsveiene ikke er avhengige av at kalleren husket
    -- nøyaktig hvilken kontekstvariant den satte.
    PERFORM set_config('disponit.aktor', p_aktor, true);
    PERFORM set_config('disponit.request_id', p_request_id, true);
    -- OPPDRAGSRADEN LÅSES FØRST, også på gjenbruksveien. Låsrekkefølgen
    -- er oppdrag → unntak overalt: reaperen (§5) holder alt `FOR UPDATE`
    -- på oppdraget når den kaller hit, og kvitteringsveien likeså. Ble
    -- unntaket låst først her, hadde to veier tatt de samme to låsene i
    -- hver sin rekkefølge — altså vranglås.
    SELECT * INTO o FROM public.oppdrag k
     WHERE k.tenant = p_tenant AND k.id = p_oppdrag_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'sikre_sak_for_oppdrag: ukjent oppdrag %',
            p_oppdrag_id USING ERRCODE = 'no_data_found';
    END IF;
    v_logg := coalesce(o.beslutning_loggpost_id, o.loggpost_id);
    -- ... OG DEN TREDJE OPPRINNELSEN HAR INGEN EGEN LOGGPOST (Codex P1,
    -- runde 5 på #140). `oppdrag_opprinnelse_komplett` tvinger BEGGE
    -- referansene til NULL på frigivelses-armen — autorisasjonen der er
    -- SIGNATUREN, ikke en revisjonslogg-rad — så `coalesce` over gir
    -- NULL, mens `unntak.loggpost_id` er NOT NULL. Uten linjen under dør
    -- hver sen kvittering og hver sikkerhetskonflikt på et
    -- frigivelsesoppdrag i en rollback: den IRREVERSIBLE utsendelsen
    -- blir verken ført som sak eller synlig for et menneske — presis det
    -- utfallet unntaksveien finnes for å hindre.
    --
    -- Linjen FINNES, den var bare ikke slått opp: frigivelsen bærer
    -- listen, og listen bærer EVALUERINGSOPPDRAGET
    -- (`opprett_utsendingsliste` krever en FULLFØRT
    -- `rekruttering.evaluering` med opprinnelse `beslutning` eller
    -- `m37_reparasjon`) — og BEGGE de armene har en NOT NULL loggpost i
    -- den samme totalformen. Oppslag i virkelig tilstand, ingen ny
    -- maskin: saken havner på loggposten som autoriserte evalueringen
    -- kjeden springer ut av, og policysnapshotet under arver den samme.
    IF v_logg IS NULL AND o.frigivelse_id IS NOT NULL THEN
        SELECT coalesce(e.beslutning_loggpost_id, e.loggpost_id)
          INTO v_logg
          FROM public.utsendingsfrigivelse f
          JOIN public.utsendingsliste l
            ON l.tenant = f.tenant AND l.liste_id = f.liste_id
          JOIN public.oppdrag e
            ON e.tenant = l.tenant AND e.id = l.oppdrag_id
         WHERE f.tenant = p_tenant
           AND f.frigivelse_id = o.frigivelse_id;
        IF v_logg IS NULL THEN
            -- Skal ikke kunne skje (FK-kjeden + totalformen), men en
            -- NOT NULL-krasj to setninger senere er en dårligere
            -- diagnose enn en navngitt.
            RAISE EXCEPTION 'sikre_sak_for_oppdrag: frigivelsesoppdrag %'
                ' mangler revisjonslinje gjennom frigivelse %',
                p_oppdrag_id, o.frigivelse_id
                USING ERRCODE = 'no_data_found';
        END IF;
    END IF;
    -- Policysnapshotet (011) arver saken fra BESLUTNINGSLOGGPOSTEN — det
    -- er den policyen som autoriserte oppdraget. `maks_auto_forsok` er 0:
    -- en oppdragssak finnes for MENNESKER (evidensfrist/sikkerhet), aldri
    -- for auto-reparasjon.
    SELECT r.policy_id, r.policy_content_hash INTO v_policy, v_policy_hash
      FROM public.revisjonslogg r
     WHERE r.tenant = p_tenant AND r.id = v_logg;
    -- «TERMINAL GJENBRUKES ALDRI» ER EN LÅS, IKKE ET BLIKK (Codex P2).
    --
    -- Uten `FOR UPDATE` leste gjenbruksveien saken i sitt eget snapshot:
    -- en saksbehandler som akkurat da satte `løst`/`avvist` uten å ha
    -- committet var usynlig, og hendelsen ble hengt på en sak som et
    -- øyeblikk senere var endelig — stikk i strid med regelen indeksen
    -- håndhever for INNSETTING. Med låsen venter vi på den transaksjonen,
    -- og READ COMMITTED revaluerer `NOT terminal` mot den nye versjonen:
    -- ble saken terminal, er raden ikke lenger et treff, og vi faller
    -- gjennom til å opprette en ny åpen sak. Det er nettopp utfallet
    -- regelen ber om.
    --
    -- Løkken er kappløpets andre halvdel: taper vi unik-bruddet, finnes
    -- vinnerens rad, og neste runde LÅSER den og leser den (eller ser at
    -- den alt er terminal og prøver innsettingen på nytt). Et tak på
    -- forsøkene, så et patologisk ping-pong mellom opprettelse og løsning
    -- blir en feil vi ser og ikke en evig løkke.
    LOOP
        v_forsok := v_forsok + 1;
        SELECT u.id INTO v_id FROM public.unntak u
         WHERE u.tenant = p_tenant AND u.oppdrag_id = p_oppdrag_id
           AND u.arsak = p_arsak AND NOT u.terminal
           FOR UPDATE;
        IF FOUND THEN
            RETURN v_id;                          -- idempotent (port 25)
        END IF;
        BEGIN
            INSERT INTO public.unntak (tenant, loggpost_id, handling, kategori,
                sakstype, prioritet, payload_kryptert, key_id, nonce,
                maks_auto_forsok_snapshot, policy_versjon, policy_content_hash,
                oppdrag_id, arsak,
                -- 041: payload_type er NOT NULL uten default; oppdragssaker
                -- arver alltid kryptert payload; sakskilde eksplisitt.
                payload_type, sakskilde)
            VALUES (p_tenant, v_logg, o.handling, 'teknisk_feil',
                    CASE p_arsak WHEN 'sikkerhet' THEN 'sikkerhet'
                                 ELSE 'normal' END,
                    CASE p_arsak WHEN 'sikkerhet' THEN 'hoy' ELSE 'normal' END,
                    o.payload_kryptert, o.key_id, o.nonce,
                    0, coalesce(v_policy, 'ukjent'),
                    coalesce(v_policy_hash, ''),
                    p_oppdrag_id, p_arsak,
                    'kryptert', 'oppdrag')
            RETURNING id INTO v_id;
            EXIT;                                 -- innsettingsveien
        EXCEPTION WHEN unique_violation THEN
            -- Kappløpstaperen. Retur skjer i NESTE runde, gjennom
            -- gjenbruksveien over — ikke gjennom innsettingsveiens hale.
            -- Sakskoblingen er én HENDELSE, ikke en tilstand: raden er
            -- idempotent fordi indeksen gjør den det, men historikken er
            -- append-only og teller. Falt taperen ut i den felles halen,
            -- fikk ETT oppdrag TO `sak_for_oppdrag`-rader for den samme
            -- koblingen — og det skjer i praksis, med samtidige sene
            -- kvitteringer eller sikkerhetskonflikter fra hver sin
            -- claim-generasjon. Å telle hendelser i sporet er nettopp det
            -- sporet er til for.
            IF v_forsok >= 5 THEN
                RAISE;
            END IF;
        END;
    END LOOP;
    -- Kun på INNSETTINGSVEIEN: koblingen skjedde nettopp, her.
    INSERT INTO public.unntak_historikk (tenant, unntak_id, hendelse,
                                         aktor, request_id, detalj)
    VALUES (p_tenant, v_id, 'sak_for_oppdrag', p_aktor, p_request_id,
            jsonb_build_object('oppdrag_id', p_oppdrag_id,
                               'arsak', p_arsak));
    RETURN v_id;
END $function$;

RESET ROLE;

-- ------------------------------------------------------------
-- 10. reap_evidensfrister (038 §5 → 043 §12) — KOPI av gjeldende kropp,
--     diff-endret: ÉN linje i predikatet, den tredje opprinnelsen med.
--
--     Codex P2 (runde 5 på #140): et frigivelsesoppdrag som passerer
--     `utforelsesfrist` mens det står i kø kan ikke lenger PLUKKES —
--     `claim_neste_oppdrag` tar kun rader med `utforelsesfrist > now()`
--     (005/015/037/049) — og INGEN vei førte det videre: reaperen tok
--     bare `opprinnelse = 'beslutning'`. Raden ble stående ikke-terminal
--     for alltid, og en e-post som ALDRI ble sendt så ut som en jobb som
--     fortsatt skulle sendes.
--
--     038 holdt m37-veien utenfor med en uttalt grunn: dens oppdrag hører til
--     en sak som ALT finnes, med egen fase-2-oppfølging. Frigivelsesveien har
--     ingen slik sak — autorisasjonen er en signatur, ikke et unntak — så den
--     hører til nettopp her, sammen med beslutningsveien. Saksveien er
--     tilgjengelig fordi §9 over ga frigivelsesoppdrag en revisjonslinje.
--
--     `CREATE OR REPLACE` beholder eier og grants (timerrollen).
-- ------------------------------------------------------------
SET LOCAL ROLE disponit_m37_claimer;
CREATE OR REPLACE FUNCTION reap_evidensfrister(p_grense INT DEFAULT 200)
RETURNS TABLE (tenant TEXT, oppdrag_id BIGINT, unntak_id BIGINT)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_sak BIGINT; v_rid TEXT; v_kontekst TEXT;
        v_kandidat BIGINT;
BEGIN
    v_rid := 'reap-' || replace(gen_random_uuid()::text, '-', '');
    v_kontekst := current_setting('disponit.tenant', true);
    FOR r IN
        SELECT o.tenant AS t, o.id AS oid FROM public.oppdrag o
         WHERE o.opprinnelse IN ('beslutning', 'frigivelse')
           AND o.status IN ('opprettet', 'plukket')
           AND now() > o.evidensfrist
         ORDER BY o.evidensfrist
         LIMIT p_grense
         FOR UPDATE OF o SKIP LOCKED
    LOOP
        PERFORM set_config('disponit.tenant', r.t, true);
        -- SAKEN, MED SAMME REGEL SOM OPPDRAGET (043 §9): finnes den alt,
        -- må låsen være ledig — ellers er dette sveipets kandidat, ikke
        -- dette sveipets rad.
        SELECT u.id INTO v_kandidat FROM public.unntak u
         WHERE u.tenant = r.t AND u.oppdrag_id = r.oid
           AND u.arsak = 'evidensfrist' AND NOT u.terminal;
        IF v_kandidat IS NOT NULL THEN
            PERFORM 1 FROM public.unntak u
             WHERE u.tenant = r.t AND u.id = v_kandidat
               FOR UPDATE SKIP LOCKED;
            IF NOT FOUND THEN
                CONTINUE;
            END IF;
        END IF;
        v_sak := public.sikre_sak_for_oppdrag(
            r.t, r.oid, 'evidensfrist', 'evidensreaper', v_rid);
        UPDATE public.oppdrag o SET status = 'feilet'
         WHERE o.tenant = r.t AND o.id = r.oid
           AND o.status IN ('opprettet', 'plukket');
        tenant := r.t; oppdrag_id := r.oid; unntak_id := v_sak;
        RETURN NEXT;
    END LOOP;
    PERFORM set_config('disponit.tenant', coalesce(v_kontekst, ''), true);
END $$;

RESET ROLE;

-- ------------------------------------------------------------
-- 11. Koblingsvakta (008 → 038 §5) — KOPI av gjeldende kropp, diff-endret
--     i TO punkter: den tredje opprinnelsen får sin egen INSERT-arm, og
--     UPDATE-armen fryser `frigivelse_id` sammen med de to andre.
--
--     Codex P2 (runde 3) og Cursor P2 (runde 5) fant det samme: §6 over
--     speiler fødselsattributtet i KOLONNELÅSEN, men koblingsvakten —
--     husets andre lag — sto igjen med bare `koblingsstatus` og
--     `beslutning_loggpost_id`. To lag er mønsteret nettopp fordi ett lag
--     er en regresjonsflate: en senere «rydding» i kolonnelåsen ville
--     åpnet repeking av autorisasjonen på en IRREVERSIBEL vei, uten at
--     noen port sa fra.
--
--     Vakten er en trigger på `oppdrag` og eies av migrator; ingen
--     rollebytte trengs.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION oppdrag_koblingsvakt()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.koblingsstatus = 'LEGACY_UKJENT' THEN
            RAISE EXCEPTION
                'oppdrag: LEGACY_UKJENT kan kun settes av migrasjon 008 — '
                'runtime skal levere beslutning_loggpost_id (KOBLET) eller '
                'et verifikasjonsoppdrag (VERIFIKASJON)';
        END IF;
        IF NEW.koblingsstatus NOT IN ('KOBLET', 'VERIFIKASJON') THEN
            RAISE EXCEPTION 'oppdrag: ukjent koblingsstatus %',
                NEW.koblingsstatus;
        END IF;
        -- 038: beslutningsopphavet. Loggposten er beslutningen selv;
        -- kravet er at den ER en TILLAT-beslutning hos samme tenant —
        -- under kallerens RLS, fail-closed som resten.
        IF NEW.opprinnelse = 'beslutning' THEN
            IF NEW.koblingsstatus <> 'KOBLET' THEN
                RAISE EXCEPTION 'oppdrag: et beslutningsoppdrag er alltid '
                    'KOBLET — det ER koblingen';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.revisjonslogg r
                 WHERE r.tenant = NEW.tenant
                   AND r.id = NEW.beslutning_loggpost_id
                   AND r.beslutning = 'TILLAT') THEN
                RAISE EXCEPTION
                    'oppdrag: beslutning_loggpost_id % er ikke en '
                    'TILLAT-beslutning hos tenanten',
                    NEW.beslutning_loggpost_id;
            END IF;
            RETURN NEW;
        END IF;
        -- 056: FRIGIVELSESOPPHAVET. Beslutningsarmen over har sin egen
        -- eksplisitte arm; den tredje opprinnelsen manglet sin (Codex P2
        -- runde 3 + Cursor P2 runde 5 — samme funn, to reviewere). Her ER
        -- signaturen autorisasjonen, og FK-en mot `utsendingsfrigivelse`
        -- beviser at frigivelsen finnes; det vakten må si, er at raden
        -- ikke kan påstå et frigivelsesopphav og samtidig være ukoblet.
        IF NEW.opprinnelse = 'frigivelse' THEN
            IF NEW.koblingsstatus <> 'KOBLET' THEN
                RAISE EXCEPTION 'oppdrag: et frigivelsesoppdrag er alltid '
                    'KOBLET — signaturen ER koblingen';
            END IF;
            IF NEW.frigivelse_id IS NULL THEN
                RAISE EXCEPTION 'oppdrag: et frigivelsesoppdrag uten '
                    'frigivelse_id har ingen autorisasjon';
            END IF;
            RETURN NEW;
        END IF;
        -- Codex P1 (review-runde 1): FK-en beviser at LOGGPOSTEN FINNES,
        -- ikke at den er RIKTIG beslutning. Uten dette kunne en KOBLET rad
        -- peke på en vilkårlig revisjonsrad hos samme tenant — og
        -- lese-API-et ville vist en fremmed beslutnings «utførelse» med
        -- full FK-integritet. Porten er SEMANTISK og ligger i databasen:
        -- loggposten må være nøyaktig fase-2-TILLAT-beslutningen for
        -- DETTE oppdragets reparasjonsidentitet — samme tre predikater som
        -- backfillen og arbeiderens oppslag, håndhevet der raden fødes.
        -- Kjøres under kallerens RLS: en innsetting uten tenantkontekst
        -- ser ingen loggpost og avvises — fail-closed, ikke en bypass.
        -- NULL-FK-en overlates til CHECK-en `oppdrag_kobling_konsistent`
        -- (BEFORE-triggere kjører FØR CHECK; uten IS NOT NULL her ville
        -- den navngitte constrainten aldri fått rapportere sitt eget brudd).
        IF NEW.koblingsstatus = 'KOBLET'
           AND NEW.beslutning_loggpost_id IS NOT NULL
           AND NOT EXISTS (
            SELECT 1 FROM public.revisjonslogg r
             WHERE r.tenant = NEW.tenant
               AND r.id = NEW.beslutning_loggpost_id
               AND r.idempotency_key = NEW.repair_operation_id
               AND r.kilde = 'arbeidskapabilitet'
               AND r.beslutning = 'TILLAT') THEN
            RAISE EXCEPTION
                'oppdrag: beslutning_loggpost_id % er ikke fase-2-TILLAT-'
                'beslutningen for repair_operation_id % (semantisk kobling '
                'kreves: idempotency_key + kilde + beslutning)',
                NEW.beslutning_loggpost_id, NEW.repair_operation_id;
        END IF;
        RETURN NEW;
    END IF;
    -- UPDATE: koblingen er uforanderlig etter innsetting (v5 pkt. 1).
    IF NEW.koblingsstatus IS DISTINCT FROM OLD.koblingsstatus
       OR NEW.beslutning_loggpost_id IS DISTINCT FROM OLD.beslutning_loggpost_id
       OR NEW.frigivelse_id IS DISTINCT FROM OLD.frigivelse_id THEN
        RAISE EXCEPTION
            'oppdrag: koblingsstatus, beslutning_loggpost_id og '
            'frigivelse_id er uforanderlige etter innsetting';
    END IF;
    RETURN NEW;
END $$;
