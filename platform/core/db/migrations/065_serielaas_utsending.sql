-- 065: serielås mot spissjekkens TOCTOU (#180)
--
-- Utsatt fra #176 (Codex P1 runde 2 + Cursor P1 samme HEAD) under K1: en
-- fiksrunde bygger ikke, og låsen krevde en ny migrasjon fordi
-- `opprett_utsendingsliste` bor i 056 — hash-pinnet til akseptcommiten
-- (`KJORT_056`, `test_migrasjonsfasit`).
--
-- KAPPLØPET, MÅLT PÅ KODEN: `signer_endepunkt` avviser signering av en
-- foreldet listeversjon ved å lese «finnes det et barn med
-- `forrige_liste_id = liste_id`» og svare 409 `liste_utdatert`. Sjekken og
-- `signer_utsendingsliste` er to steg i samme READ COMMITTED-transaksjon,
-- uten lås på serien. Committer en annen transaksjon en barnversjon i
-- mellomrommet, autoriseres feil innhold irreversibelt — og seriens ene
-- signatur-slot er brent, så den faktiske spissen blir permanent
-- usignerbar.
--
-- VINDUET ER IKKE NÅBART I DAG, og det står her fordi det er sant: de tre
-- rekrutteringsrutene er lesing, en kodet blinding-avvisning og signering,
-- og ingen produksjonsvei oppretter en barnversjon — `opprett_utsendingsliste`
-- kalles bare fra tester og fra demo-seeden, som lager en ROT med
-- `forrige_liste_id = NULL`. Kappløpet blir nåbart i det redigeringsbenet
-- lander, og da skal låsen alt stå her. En port bygget etter at veien er
-- åpnet, er en port som kom for sent.
--
-- Kroppen er 056 ORDRETT — eneste endring er låsen (SPEIL-presedensen fra
-- 062: aldri skriv naboens dør fra hukommelsen).

SET LOCAL ROLE disponit_m37_claimer;

CREATE OR REPLACE FUNCTION opprett_utsendingsliste(
    p_tenant TEXT, p_utkast_serie UUID, p_forrige UUID, p_oppdrag_id BIGINT,
    p_listetype TEXT, p_malversjon TEXT, p_innhold_hash TEXT, p_antall INT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID := gen_random_uuid(); v_forelder_oppdrag BIGINT;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'opprett_utsendingsliste');
    -- SERIELÅSEN (#180). Signeringsveiens spissjekk og selve signaturen
    -- er to steg i samme READ COMMITTED-transaksjon. Committer DENNE
    -- funksjonen en barnversjon i mellomrommet, er utfallet: hash-ekkoet
    -- stemmer fortsatt (forelderens `innhold_hash` er uendret),
    -- `signer_utsendingsliste` verifiserer ikke spiss — den signerer
    -- hvilken som helst `liste_id` — og `en_signert_versjon_per_serie`
    -- gir serien nøyaktig ÉN signatur-slot. Altså: feil innhold
    -- irreversibelt autorisert, og den faktiske spissen permanent
    -- usignerbar.
    --
    -- HVORFOR ADVISORY OG IKKE `FOR UPDATE`: PostgreSQL krever
    -- UPDATE-privilegium for ENHVER radlåsklausul, også `FOR SHARE` —
    -- grensen 019 skrev ned og 056 §7b siterer. Runtime-rollen har kun
    -- SELECT på `utsendingsliste`, så signeringsveien kan ikke ta en
    -- radlås på forelderen. En advisory-lås krever ingen privilegier og
    -- kan derfor tas av BEGGE veier — det er hele grunnen til at det er
    -- denne formen og ikke den andre.
    --
    -- Nøkkelen er serien, ikke raden: det er serien som har én
    -- signatur-slot, og det er der to skrivere kolliderer. Samme mønster
    -- som `frigi_utsendelse` (056 §7c).
    PERFORM pg_advisory_xact_lock(
        hashtextextended('m57:serie:' || p_tenant || ':'
                         || p_utkast_serie::text, 0));
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

RESET ROLE;
