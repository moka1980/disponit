-- 066: revisjonshendelse — kjernens udødelige, tenant-bundne hendelseslogg (#159)
--
-- FUNNET SOM KREVDE DEN (Codex P1 ×2 på #153, runde 2 og 9): den auditerte
-- avskruingen av blindingen var SELVATTESTERT. Den som ba om å skru av
-- blindingen leverte selv beviset på at handlingen var auditert, og beviset
-- var en dict med tre sanne verdier:
--
--     evaluer_kandidat(..., blinding_av=True,
--                      auditrad={"aktor": "x", "ts": "x",
--                                "begrunnelse": "x"})
--
-- Et repo-vidt søk fant ingen produsent og ingen persisteringsvei for
-- `auditrad`. Klarsignal §6 lover «avskruing auditert», og port 16b måler
-- `blinding_avskrudd_uten_auditrad` — mot en påstand. **En sann påstand om
-- en revisjonshendelse er ikke en revisjonshendelse.**
--
-- HVORFOR IKKE EN STRENGERE FORMPORT. Å kreve `ts` som gyldig ISO-8601 og
-- en `revisjon_id` som ser ut som en UUID flytter påstanden ett hakk: en
-- velformet UUID beviser ikke at en rad finnes. Det ville vært det tredje
-- formforsøket på samme rot, og §9 K2 forbyr det. Derfor en TABELL:
-- «auditert» blir en egenskap ved basen, ikke ved kallet.
--
-- EIERS VALG A (delegert myndighet, natten 28→29/8): bygg hendelsen.
-- Issuet anbefalte A framfor B («fjern døra»), og begrunnelsen holder:
-- tabellen trengs uansett av manifestets `revisjonslogg_korrekt`, av
-- signaturhendelsene og av frigivelsene, som alle lover revisjonslogg i §6.
--
-- FORMEN ER HUSETS, ikke en ny: append-only med `avvis_endring()` på rad OG
-- statement (011/014/036/053/056), RLS med `disponit.tenant`, og
-- `krev_tenantkontekst` i skriveveien (038). Ingenting her er oppfunnet.
--
-- TRANSAKSJONEN EIES AV KJØREREN, ikke av fila: `db/kjorer.py` avviser
-- `BEGIN` og `COMMIT` i alt fra og med 003, fordi en migrasjon som
-- committer selv kan etterlate historikken halvskrevet hvis den neste
-- feiler. Regelen håndheves av kjøreren, ikke av en pytest — den felte
-- første utgave av denne fila i CI-steget «Databaseroller».

CREATE TABLE revisjonshendelse (
    tenant TEXT NOT NULL,
    hendelse_id UUID NOT NULL DEFAULT gen_random_uuid(),
    -- HANDLINGEN ER ET LUKKET SETT, ikke en fri streng. En ny slags
    -- revisjonshendelse er en KONTRAKTSENDRING og hører i en migrasjon,
    -- ikke i et kallargument — samme doktrine som `eiermodul` i 058 og
    -- `formaal`/`innholdstype` ved siden av den. Uten CHECK-en kunne en
    -- kaller skrevet «m57.blinding_avskrudd_liksom» og fått en rad som
    -- SER ut som beviset porten leter etter.
    handling TEXT NOT NULL CHECK (handling IN ('m57.blinding_avskrudd')),
    -- Aktøren er den ANSVARLIGE, ikke prosessen. En tom eller
    -- plassholderaktør gjør hendelsen ubrukelig som revisjonsspor, og en
    -- ubrukelig revisjonshendelse er verre enn ingen: den ser ut som et
    -- svar på spørsmålet «hvem bestemte dette».
    --
    -- VI SLUTTER Å REGNE OPP BLANKTEGN (Codex P2 ×3). Tre runder på samme
    -- akse: enargs `btrim` tok bare mellomrom, toargsformen tok hele
    -- ASCII-klassen, og så kom U+00A0 — hardt mellomrom — og passerte
    -- begge. Hver runde utvidet listen, og listen kan alltid utvides én
    -- gang til. Det er §9 K2s mønster, og svaret er ikke en fjerde liste.
    --
    -- Regelen snus: i stedet for å fjerne hvert blanktegn vi klarer å
    -- navngi, trimmer vi med `\s` — PostgreSQLs regexklasse, som i en
    -- UTF-8-base dekker Unicodes blanktegn og ikke bare ASCII. Da er det
    -- ingen liste å glemme noe fra.
    --
    -- Grensen måles på den TRIMMEDE lengden, ikke på råteksten: ellers
    -- ville ni mellomrom og én bokstav vært en ti tegns begrunnelse.
    aktor TEXT NOT NULL
        CHECK (length(regexp_replace(aktor, '^\s+|\s+$', '', 'g'))
               BETWEEN 1 AND 200),
    -- BEGRUNNELSEN HAR EN NEDRE GRENSE. «x» er ikke en begrunnelse, og
    -- funnet som skapte denne tabellen brukte nettopp «x». Ti tegn er
    -- ikke mye, men det skiller en setning fra et tastetrykk — og etter
    -- linjen over er det ti TEGN, ikke ti tastetrykk på enter eller ti
    -- harde mellomrom.
    begrunnelse TEXT NOT NULL
        CHECK (length(regexp_replace(begrunnelse, '^\s+|\s+$', '', 'g'))
               BETWEEN 10 AND 2000),
    -- PRINSIPALEN BAK AKTØREN (Cursor P1, runde 5 på #247). `aktor` er en
    -- ETIKETT — den er fri tekst, og fri tekst beviser ingenting: runtime
    -- har EXECUTE på skriveveien, så en direkte kaller kunne plantet
    -- `m57.blinding_avskrudd` i tenantens navn med en oppdiktet aktør, og
    -- oppslaget ville godkjent raden som base-evidens. Da hadde vi flyttet
    -- selvattesten ett lag ned i stedet for å fjerne den — nøyaktig den
    -- klassen hele tabellen finnes for.
    --
    -- 019-FORMEN ORDRETT: etiketten beholdes (den er det mennesket leser i
    -- sporet), og prinsipalen ved siden av den er den basen faktisk kan
    -- binde. `bruker_id` navngir et AKTIVT `brukermedlemskap` i tenanten —
    -- den ene autorisasjonsinngangen runtime ikke kan skrive (010:
    -- OIDC-forvaltet, kun SELECT). Kolonnen er NOT NULL fordi tabellen er
    -- ny: det finnes ingen historiske rader å være bakoverkompatibel med,
    -- og en rad uten prinsipal kunne ikke reautoriseres senere.
    bruker_id TEXT NOT NULL
        REFERENCES brukeridentitet (bruker_id),
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant, hendelse_id)
);

-- Oppslaget grensen gjør er (tenant, hendelse_id, handling) — indeksen
-- følger den, ikke en tenkt rapportvei.
CREATE INDEX revisjonshendelse_handling
    ON revisjonshendelse (tenant, handling, ts DESC);

-- APPEND-ONLY, BEGGE VEIER. Radtriggeren stopper UPDATE/DELETE;
-- statement-triggeren stopper TRUNCATE, som ALDRI fyrer radtriggere
-- (Codex P2 på #140, runde 1: uten den kunne eieren tømt hele bevisrekken
-- uten å møte `avvis_endring` én eneste gang).
DROP TRIGGER IF EXISTS revisjonshendelse_append_only ON revisjonshendelse;
CREATE TRIGGER revisjonshendelse_append_only
    BEFORE UPDATE OR DELETE ON revisjonshendelse
    FOR EACH ROW EXECUTE FUNCTION avvis_endring();
DROP TRIGGER IF EXISTS revisjonshendelse_ingen_truncate ON revisjonshendelse;
CREATE TRIGGER revisjonshendelse_ingen_truncate
    BEFORE TRUNCATE ON revisjonshendelse
    FOR EACH STATEMENT EXECUTE FUNCTION avvis_endring();

ALTER TABLE revisjonshendelse ENABLE ROW LEVEL SECURITY;
ALTER TABLE revisjonshendelse FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON revisjonshendelse
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));

-- ------------------------------------------------------------
-- FUNKSJONENE EIES AV `disponit_m37_claimer`, IKKE AV MIGRATOR — og det er
-- ikke stil, det er den eneste formen som virker (CI: «permission denied for
-- function krev_tenantkontekst» inne i begge definer-funksjonene).
--
-- Porten `krev_tenantkontekst` er REVOKEt fra PUBLIC i 038 og eies av
-- `disponit_m37_claimer`; migrator er medlem WITH INHERIT FALSE og arver
-- derfor INGENTING. En definer-funksjon eid av migrator kjører altså som en
-- rolle som ikke får kalle porten, og hele skrive-/leseveien er død.
-- 038/056/065 løser det ved å lage funksjonene SOM eieren av porten — samme
-- rolle, ingen ny grant, ingen ny rettighetsflate. Det gjør 066 nå også.
--
-- Alternativet — å gi migrator EXECUTE på porten — ville i tillegg latt hver
-- fremtidig definer-funksjon kjøre med migrators fulle autoritet (eier av
-- hver tabell i huset). Den veien utvider en flate 038 med vilje holdt smal.
--
-- RETTIGHETENE PÅ FUNKSJONENE HØRER HIT, inne i rolleblokken: PostgreSQL
-- gir nye funksjoner EXECUTE til PUBLIC ved fødselen, og både REVOKE og
-- GRANT på claimer-eide funksjoner er virkningsløse fra migrator (027-fellen,
-- beskrevet i 056 §7).
SET LOCAL ROLE disponit_m37_claimer;

-- Skriveveien. SECURITY DEFINER med tenanten bundet til KONTEKSTEN, aldri
-- til parameteret alene (038-formen): en kaller som oppgir en annen tenant
-- enn sin egen blir avvist, ikke betjent.
--
-- ... OG AKTØREN ER BUNDET SOM TENANTEN (Cursor P1, runde 5). Tenantporten
-- alene svarer på «hvilken kunde», ikke på «hvem». `p_bruker_id` er
-- prinsipalen bak etiketten, og den LÅSES mot et aktivt medlemskap før
-- raden skrives — 043/056-doktrinen ordrett: den ene autorisasjons-
-- inngangen runtime ikke selv kan skrive. Låsen lånes av `laas_godkjenner`
-- (013) og ikke tatt med en egen `FOR UPDATE`, av den harde grunnen 019 og
-- 056 alt skrev ned: enhver radlåsklausul krever UPDATE-privilegium, og
-- `disponit_m37_claimer` skal ALDRI kunne skrive `brukermedlemskap`. Uten
-- låsen kunne en tilbakekalling committet mellom oppslaget og INSERT-en,
-- og en append-only rad ville for alltid navngitt en tilbakekalt bruker som
-- den som autoriserte avskruingen.
--
-- ÆRLIG OM GRENSEN, som 056 §7b: en kompromittert runtime kan lese
-- medlemskapstabellen og UTGI SEG FOR et aktivt medlem. Den resten er ikke
-- lukkbar herfra (den forutsetter at basen kan verifisere konvolutten).
-- Det denne porten fjerner er den frie oppdiktingen: aktøren må være noen
-- som faktisk finnes, er aktiv, og hører til DENNE tenanten.
CREATE OR REPLACE FUNCTION skriv_revisjonshendelse(
    p_tenant TEXT, p_handling TEXT, p_aktor TEXT, p_begrunnelse TEXT,
    p_bruker_id TEXT)
RETURNS UUID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_id UUID;
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'skriv_revisjonshendelse');
    PERFORM 1 FROM public.laas_godkjenner(p_tenant, p_bruker_id);
    IF NOT FOUND THEN
        RAISE EXCEPTION 'skriv_revisjonshendelse: % mangler aktivt'
            ' medlemskap i %', coalesce(p_bruker_id, '<null>'), p_tenant
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    INSERT INTO public.revisjonshendelse
        (tenant, handling, aktor, begrunnelse, bruker_id)
    VALUES (p_tenant, p_handling, p_aktor, p_begrunnelse, p_bruker_id)
    RETURNING hendelse_id INTO v_id;
    RETURN v_id;
END $$;

-- Leseveien grensen bruker. Den returnerer HANDLINGEN og TIDSPUNKTET, ikke
-- en boolsk — en modul som bare får «ja» kan ikke vise i artefaktet HVA
-- som ble autorisert, og et revisjonsspor som ikke kan gjengis er ikke et
-- spor. NULL betyr «finnes ikke i DIN tenant», og de to tilfellene skilles
-- bevisst ikke: at en hendelse finnes hos naboen er ikke din opplysning.
CREATE OR REPLACE FUNCTION les_revisjonshendelse(
    p_tenant TEXT, p_hendelse_id UUID)
RETURNS TABLE (handling TEXT, aktor TEXT, ts TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    PERFORM public.krev_tenantkontekst(p_tenant, 'les_revisjonshendelse');
    RETURN QUERY
        SELECT r.handling, r.aktor, r.ts
          FROM public.revisjonshendelse r
         WHERE r.tenant = p_tenant AND r.hendelse_id = p_hendelse_id;
END $$;

REVOKE ALL ON FUNCTION skriv_revisjonshendelse(TEXT, TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION les_revisjonshendelse(TEXT, UUID) FROM PUBLIC;
-- `disponit` er LOKAL-/TESTNAVNET på runtime-rollen, ikke rollen (Codex P1).
-- `deploy/staging/migrer.py` tar runtime-rollens navn som argument: på en
-- installasjon med et annet navn traff en literal grant her enten feil rolle
-- — den konfigurerte rollen fikk INGEN execute, og hele #159-veien var stengt
-- etter migrering — eller den falt hardt på en rolle som ikke finnes, og da
-- rullet 066 tilbake FØR kjøreren rakk sine parameteriserte grants.
--
-- Den AUTORITATIVE granten er derfor kjørerens `M37_RETTIGHETER_API`, der
-- begge signaturene nå står; denne er betinget, med 043s form ordrett (der
-- står den tre ganger, av nøyaktig samme funn).
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
    GRANT EXECUTE ON FUNCTION
      skriv_revisjonshendelse(TEXT, TEXT, TEXT, TEXT, TEXT) TO disponit;
    GRANT EXECUTE ON FUNCTION les_revisjonshendelse(TEXT, UUID) TO disponit;
  END IF;
END $$;

RESET ROLE;

-- RETTIGHETSGRENSEN. Runtime skriver og leser GJENNOM funksjonene og har
-- ingen tabellrettigheter i det hele tatt: da finnes det ingen vei til en
-- UPDATE som triggeren må stoppe, og triggeren er beltet i tillegg til
-- selen. `disponit` er runtime-rollen (samme som resten av huset bruker).
--
-- TABELLEN EIES AV MIGRATOR (den er laget utenfor rolleblokken over, og
-- skal være det: testoppryddingen må kunne `ALTER TABLE ... DISABLE
-- TRIGGER`, som krever eierskap). Rettighetene på den gis derfor herfra —
-- retten til å gi bort et privilegium følger EIERSKAPET, så disse linjene
-- ville vært stille no-ops inne i `SET LOCAL ROLE` (056 §8, 027-fellen).
--
-- Funksjonseieren trenger `SELECT, INSERT` fordi den nå ER den som utfører
-- dem: 056-formen ordrett. RLS står uendret i veien for begge — tabellen
-- har FORCE, og eieren av funksjonene er ikke eieren av tabellen.
REVOKE ALL ON revisjonshendelse FROM PUBLIC;
GRANT SELECT, INSERT ON revisjonshendelse TO disponit_m37_claimer;

