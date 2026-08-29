-- ============================================================
-- 032 — Angre en feilopprettet policy: slett den som ALDRI er brukt
--
-- Eier: «det skal også være mulig at en policy er opprettet at man kan angre
-- og slette den.» Behovet er målt, ikke tenkt: `tjenestebedrift1` og
-- `tjenestebedrift2` ble begge aktivert ved feil, og eneste vei ut var
-- håndskrevet SQL som postgres — to ganger på én dag.
--
-- GRENSEN ER «ALDRI BRUKT». En policy som har styrt én eneste beslutning kan
-- ikke slettes: revisjonsloggen refererer `pid@versjon/handling`, og et spor
-- som peker på noe som ikke finnes er ikke et revisjonsspor. De policyene
-- AVVIKLES (fremtidig, styrt handling) — de slettes aldri. Skillet er det
-- samme som mellom å forkaste et utkast og å kaste en godkjenning.
--
-- Formen speiler `aktiver_policy` (013): SECURITY DEFINER eid av
-- `disponit_policy_eier`, EXECUTE til runtime, alle kontroller INNE i
-- funksjonen — et direkte kall utenom endepunktet når aldri forbi dem.
--
-- Hva som skjer, i én transaksjon:
--   * pekeren nullstilles (ankerraden BESTÅR — den er append-only med vilje,
--     og `revisjon` teller også denne hendelsen);
--   * policyens rader i `policyer` slettes — versjonene blir ledige igjen,
--     så en riktig opprettelse etterpå ikke stoppes av 020-monotonien. ETT
--     unntak: en versjon som er BASE for en aktiveringsrunde blir stående
--     (inaktiv), fordi attestasjonene på den runden er signaturer på en diff
--     mot nettopp det dokumentet, og runden lagrer bare hashen av det;
--   * utkast og runder RØRES IKKE: at mennesker attesterte er et faktum om
--     fortiden, og det skal stå igjen selv om resultatet angres. (Nøyaktig
--     det de manuelle oppryddingene bevarte.) Det ENESTE kalleren skriver på
--     en runde er `apen|klar → utlopt` for en runde som allerede har passert
--     `utloper` — en overgang som var forfalt uansett, ikke en opprydding;
--     attestasjonene blir stående.
--
-- `revisjonslogg`-kontrollen spør på TO former, og må gjøre det: `LIKE
-- pid || '@%'` (loggformatet er `pid@versjon/handling`, og skjemaet forbyr `@`
-- i policy_id, så prefikset er entydig — escaping trengs ikke for `@`, og
-- `_`/`%` kan ikke forekomme i en pid, `^[a-z0-9-]+$`) OG snapshothashen til
-- hver versjon som slettes. Den siste er der fordi id-en i databasen og id-en
-- i dokumentet kan sprike på gamle rader; se ved selve prøven under.
--
-- SLETTINGEN ER BUNDET TIL DEN VERSJONEN OPERATØREN SÅ (Codex P1). Kalleren
-- oppgir `forventet_versjon`/`forventet_hash` — identiteten flaten VISTE — og
-- de sammenlignes her, under låsen, mot den raden som faktisk står aktiv. Uten
-- den bindingen slettet forespørselen ALLE versjoner av `policy_id`, også en
-- som ble godkjent og aktivert etter at siden ble lastet (eller mens
-- slettingen ventet på policylåsen): fire øyne attesterte en ny policy, en
-- annen operatør bekreftet en dialog om den gamle, og tenanten sto igjen uten
-- den nye. Kontrollen er den SAMME som `aktiver_policy` gjør på `base_versjon`
-- («den godkjennerne diffet mot må fortsatt være aktiv»), speilvendt: den
-- operatøren så må fortsatt være den aktive.
--
-- Sammenligningen leser `policyer`-raden med `aktiv`, ikke `policy_hode`-
-- pekeren. De to er samme svar der hoderaden finnes (`policy_peker_konsistent`
-- håndhever det ved commit), men policyer registrert før PR-013 er
-- grandfathered UTEN hoderad — og de er nettopp de gamle feilene eier kan
-- trenge å angre. Å lese pekeren ville gjort dem uslettelige. Raden er
-- dessuten NØYAKTIG den `/v1/policy/aktiv` og `/v1/policy/aktive` serverte,
-- så det som sammenlignes er det flaten faktisk viste.
--
-- HASHEN er med fordi versjonsnummeret alene ikke er en identitet: slettingen
-- FRIGJØR versjonene (over), så `1.0.0` kan aktiveres på nytt med et annet
-- innhold. Da ville en versjonssjekk alene sagt «uendret» om en policy som er
-- byttet ut under operatøren. Paret (versjon, innholds_hash) er den identiteten
-- begge leseendepunktene allerede gir ut.
-- ============================================================

-- DROP først (samme lærdom som 027): etter første kjøring eies funksjonen av
-- policy_eier, og migrator kan ikke REPLACE noe den ikke eier — re-kjøringen
-- ville dødd med «must be owner». Den GAMLE toargumentsformen droppes også:
-- den slettet uten å binde seg til en versjon, og skal ikke bli stående som en
-- vei utenom kontrollen.
DROP FUNCTION IF EXISTS slett_ubrukt_policy(TEXT, TEXT);
DROP FUNCTION IF EXISTS slett_ubrukt_policy(TEXT, TEXT, TEXT, TEXT);

CREATE OR REPLACE FUNCTION slett_ubrukt_policy(
    p_tenant TEXT, p_policy_id TEXT,
    p_forventet_versjon TEXT, p_forventet_hash TEXT)
RETURNS int
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_versjon TEXT;
    v_hash    TEXT;
    v_brukt  int;
    v_apen   int;
    n        int;
BEGIN
    IF p_tenant IS NULL OR btrim(p_tenant) = ''
       OR current_setting('disponit.tenant', true) IS DISTINCT FROM p_tenant
    THEN
        RAISE EXCEPTION 'slett_ubrukt_policy: tenantkontekst mangler/avviker'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- Den forventede identiteten er PÅKREVD, ikke valgfri. En NULL her ville
    -- vært en sletting uten binding — altså akkurat den formen kontrollen
    -- finnes for å fjerne — og et direkte kall utenom endepunktet skal ikke
    -- kunne velge den bort (samme grunn som at alle andre vilkår står her
    -- inne og ikke i kalleren).
    IF p_forventet_versjon IS NULL OR p_forventet_hash IS NULL THEN
        RAISE EXCEPTION 'slett_ubrukt_policy: forventet versjon/hash mangler'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- SERIALISERINGEN, og den må komme aller først.
    --
    -- Radlåsen på `policy_hode` under er ikke nok, og kunne aldri blitt det.
    -- De to veiene som gjør en policy BRUKT rører ikke den raden i det hele
    -- tatt: beslutningsveien leser `policyer` via `policyregister.hent_aktiv`,
    -- og runde-åpningen leste `policy_hode` med en naken SELECT. Begge kunne
    -- derfor gå helt forbi låsen, la denne funksjonen se en revisjonslogg uten
    -- spor og null åpne runder, slette — og først ETTERPÅ committe sin egen
    -- revisjonsrad eller aktiveringsrunde. Resultatet er nøyaktig det garantien
    -- «aldri brukt» skulle utelukke: et revisjonsspor som peker på en policy
    -- som ikke finnes, eller godkjennere i en runde som aldri kan aktiveres.
    --
    -- Runtime kan heller ikke låse rader i `policy_hode`: den har KUN SELECT
    -- (V10), og Postgres krever UPDATE-privilegium for `FOR SHARE`. Det som
    -- gjenstår, og som virker fra begge sider, er en advisory-nøkkel.
    --
    -- Nøkkelen er `tenant \x1f 'policy' \x1f policy_id`, hashet med
    -- `hashtextextended(..., 0)` — SAMME streng som `db.pg.policylasnokkel`
    -- bygger. Endres den ene, MÅ den andre endres: da tar de to sidene hvert
    -- sitt lås og serialiserer ingenting.
    --
    -- Her tas den EKSKLUSIVT, mens brukerne tar den DELT. Denne funksjonen
    -- venter derfor på hver beslutning og hver runde-åpning som allerede er i
    -- gang (til den har committet, ikke bare til den har lest), og enhver ny
    -- må vente på oss. Beslutninger blokkerer aldri hverandre.
    PERFORM pg_advisory_xact_lock(hashtextextended(
        p_tenant || E'\x1f' || 'policy' || E'\x1f' || p_policy_id, 0));

    -- Deretter ankeret: en samtidig aktivering av samme policy skal enten
    -- skje FØR (og da er policyen kanskje brukt) eller vente til vi er
    -- ferdige (og da finner den ingen aktiv base — som er sannheten).
    PERFORM 1 FROM policy_hode
      WHERE tenant = p_tenant AND policy_id = p_policy_id FOR UPDATE;

    -- FØRST NÅ kan den aktive raden leses: en aktivering som var i gang har
    -- committet (den holder samme hoderad `FOR UPDATE` gjennom hele
    -- transaksjonen), og en ny kan ikke komme forbi før vi er ferdige. Leses
    -- den før låsene, er svaret bare et øyeblikksbilde fra før køen — og
    -- kontrollen ville vært den samme kappløpet den skal stoppe.
    --
    -- Kontrollen kommer FØR de øvrige vilkårene med vilje: er policyen byttet
    -- ut under operatøren, er det DET hun må få vite. «Policyen har styrt
    -- beslutninger» ville vært et sant utsagn om en annen policy enn den hun
    -- ba om å få slettet.
    SELECT versjon, innholds_hash INTO v_versjon, v_hash
      FROM policyer
     WHERE tenant = p_tenant AND policy_id = p_policy_id AND aktiv;

    -- «FINNES IKKE» MÅLES FØR IDENTITETEN SAMMENLIGNES (Codex P2). Er det
    -- ingen aktiv rad, står `v_versjon`/`v_hash` som NULL, og sammenligningen
    -- under er da `NULL IS DISTINCT FROM <påkrevd ikke-NULL>` — alltid sann.
    -- En policy en annen operatør allerede har slettet kom derfor ut som
    -- `policy_endret`, og flaten fortalte at en ANNEN VERSJON er aktivert:
    -- en usann forklaring, og en som sender eier på leting etter en versjon
    -- som ikke finnes. Målingen lenger nede (`v_antall = 0`) kunne aldri ta
    -- den — den står etter denne sammenligningen, og enhver forespørsel bærer
    -- den påkrevde ikke-NULL identiteten.
    --
    -- «Ingen aktiv rad» dekker begge formene av det samme svaret: policyen er
    -- borte i sin helhet, eller den står igjen som ren historikk (versjoner
    -- bevart som attestasjonsbase, se DELETE-en under). I begge tilfeller er
    -- det ingen aktiv policy å angre, og `policy_ukjent` er det sanne svaret.
    IF NOT FOUND THEN
        RAISE EXCEPTION 'slett_ubrukt_policy: policyen finnes ikke'
            USING ERRCODE = 'no_data_found';
    END IF;

    IF v_versjon IS DISTINCT FROM p_forventet_versjon
       OR v_hash IS DISTINCT FROM p_forventet_hash THEN
        RAISE EXCEPTION
            'slett_ubrukt_policy: aktiv versjon er % (forventet %)',
            v_versjon, p_forventet_versjon
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    -- BRUKSPRØVEN MÅ VÆRE MINST SÅ VID SOM RETENSJONSVAKTA (Codex P2).
    -- Prefikset alene var det ikke. `policy_id` i databasen og
    -- `innhold.meta.policy_id` i dokumentet er samme verdi for alt som er
    -- registrert etter identitetskontrollen i PR-013 — men eldre rader kan ha
    -- ulike, og beslutninger tatt den gangen skrev DOKUMENTETS id inn i
    -- `revisjonslogg.policy_id`. Prefikstesten så da null spor på en policy
    -- som har styrt beslutninger. DELETE-en ble riktignok stoppet likevel, av
    -- `policy_retention_vakt` (V3) som matcher `policy_content_hash` — men
    -- den kaster `P0001`, ikke et vilkårsbrudd, så flaten fikk en 500 der
    -- svaret skulle vært `policy_i_bruk`. Det er samme rad som avgjør; det var
    -- bare to ulike spørsmål om den.
    --
    -- Nå spør denne det samme som vakten: hashen til HVER versjon som skal
    -- slettes. Prefikset står ved siden av, ikke i stedet for —
    -- `revisjonslogg.policy_content_hash` er nullbar (den kom i 003, og ble
    -- aldri satt NOT NULL), så de eldste sporene har bare id-en å kjennes på.
    -- Sammen dekker de begge former.
    SELECT count(*) INTO v_brukt FROM revisjonslogg r
     WHERE r.tenant = p_tenant
       AND (r.policy_id LIKE p_policy_id || '@%'
            OR r.policy_content_hash IN (
                SELECT p.innholds_hash FROM policyer p
                 WHERE p.tenant = p_tenant AND p.policy_id = p_policy_id));
    IF v_brukt > 0 THEN
        RAISE EXCEPTION
            'slett_ubrukt_policy: policyen har styrt % beslutning(er)', v_brukt
            USING ERRCODE = 'check_violation';
    END IF;

    -- En LEVENDE åpen runde betyr attestasjoner i omløp — samme vern som
    -- forkast. En FORFALT runde er derimot ikke i omløp: ingen kan attestere
    -- den (`attester_aktivering` nekter den), og den skal ikke blokkere.
    -- Vilkåret måles ikke her: `policyadmin._lukk_forfalte_runder` kjører
    -- `apen|klar → utlopt`-overgangen FØR dette kallet, gjennom den samme
    -- `_lukk_forfalt_runde` som forkasting og runde-åpning bruker. Statusen
    -- under er derfor à jour når den leses, og «forfalt» har fortsatt bare
    -- ÉN definisjon — ikke en kopi til, med sin egen klokke, i SQL.
    SELECT count(*) INTO v_apen
      FROM aktiveringsrunde r JOIN policyutkast u
        ON u.tenant = r.tenant AND u.utkast_id = r.utkast_id
     WHERE r.tenant = p_tenant AND u.policy_id = p_policy_id
       AND r.status IN ('apen', 'klar');
    IF v_apen > 0 THEN
        RAISE EXCEPTION 'slett_ubrukt_policy: åpen aktiveringsrunde'
            USING ERRCODE = 'check_violation';
    END IF;

    UPDATE policy_hode
       SET aktiv_versjon = NULL, revisjon = revisjon + 1
     WHERE tenant = p_tenant AND policy_id = p_policy_id;

    -- («Finnes ikke» sto tidligere HER, som en telling av rader etter at
    -- pekeren var nullstilt. Den kunne aldri fyre: identitetskontrollen over
    -- passeres bare når en aktiv rad finnes, så tellingen var minst 1 hver
    -- gang den ble nådd. Målingen står nå der den kan si noe — på den aktive
    -- raden, før identiteten sammenlignes.)

    -- Pekeren er nullstilt, og da kan ingen rad stå igjen som aktiv:
    -- `policy_peker_konsistent` (012) håndhever «peker NULL ⇔ ingen aktiv
    -- rad» ved commit. Flagget tas derfor ned FØR slettingen — for raden kan
    -- være en base som blir stående (under). Slettes den likevel, er
    -- oppdateringen virkningsløs.
    UPDATE policyer SET aktiv = false
     WHERE tenant = p_tenant AND policy_id = p_policy_id AND aktiv;

    -- Retensjonsvakta er siste ord, og den skal HØRES. `policy_retention_vakt`
    -- (V3) kjenner referanser denne funksjonen med vilje ikke teller opp selv
    -- — ikke-terminale unntak, oppdrag, reparasjonsoperasjoner — og en fjerde
    -- kopi av det settet her inne ville vært den duplikatformen resten av
    -- modulen er skrevet for å unngå. Men vakten kaster `P0001`, og en
    -- uoversatt `P0001` blir en 500: en policy som ER referert fikk «noe gikk
    -- galt» i stedet for grunnen. Avvisningen oversettes derfor her, til det
    -- samme vilkårsbruddet de øvrige grensene bruker, med vaktens egen
    -- forklaring i behold.
    --
    -- EN VERSJON SOM ER BASE FOR EN RUNDE BLIR STÅENDE (Codex P2). Runden
    -- lagrer `base_policy_hash`, ikke basedokumentet, og attestasjonene er
    -- signaturer på en DIFF mellom den basen og utkastet. Slettes basen, står
    -- runden og attestasjonene igjen — men det de sier ja til kan ikke lenger
    -- leses. «Utkast og runder røres ikke» ville da vært sant om radene og
    -- usant om historikken de er der for å bære. (Bootstrap-runder rammes
    -- ikke: den første aktiveringen måles mot `DENY_ALL_V1`, en konstant i
    -- koden, så en policy med én versjon slettes i sin helhet som før — som
    -- er nettopp tilfellet funksjonen ble skrevet for.)
    --
    -- Versjonene som blir stående er ikke aktive (flagget over), så de styrer
    -- ingenting; de er historikk på linje med utkastene og attestasjonene.
    -- Numrene deres forblir opptatt — det er prisen for at godkjenningen kan
    -- leses — mens alle andre versjonsnumre frigjøres som før.
    BEGIN
        DELETE FROM policyer p
         WHERE p.tenant = p_tenant AND p.policy_id = p_policy_id
           AND NOT EXISTS (
                 SELECT 1 FROM aktiveringsrunde r JOIN policyutkast u
                     ON u.tenant = r.tenant AND u.utkast_id = r.utkast_id
                  WHERE r.tenant = p_tenant AND u.policy_id = p_policy_id
                    AND r.base_policy_hash = p.innholds_hash);
        GET DIAGNOSTICS n = ROW_COUNT;
    EXCEPTION WHEN raise_exception THEN
        RAISE EXCEPTION 'slett_ubrukt_policy: %', SQLERRM
            USING ERRCODE = 'check_violation';
    END;
    RETURN n;
END $$;

ALTER FUNCTION slett_ubrukt_policy(TEXT, TEXT, TEXT, TEXT)
    OWNER TO disponit_policy_eier;
-- REVOKE/GRANT som EIEREN (Codex P1, samme klasse 028 dokumenterer): etter
-- ALTER OWNER er migrator bare et INHERIT FALSE-medlem, og en naken REVOKE
-- blir en stille WARNING — funksjonen beholder PostgreSQLs standard
-- PUBLIC EXECUTE, og enhver DB-innlogging kan kalle en SECURITY
-- DEFINER-sletting og tilfredsstille tenantsjekken ved å sette GUC-en selv.
SET LOCAL ROLE disponit_policy_eier;
REVOKE ALL ON FUNCTION slett_ubrukt_policy(TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION slett_ubrukt_policy(TEXT, TEXT, TEXT, TEXT)
    TO disponit;
RESET ROLE;
-- Eieren må selv kunne slette radene og lese loggen den kontrollerer mot.
GRANT DELETE ON policyer TO disponit_policy_eier;
GRANT SELECT ON revisjonslogg TO disponit_policy_eier;
-- …og på ALT retention-vakten leser: DELETE-triggeren på policyer
-- (policy_retention_vakt, GO-vilkår V3) kjører som den som sletter — altså
-- eieren her — og spør unntak, oppdrag, reparasjonsoperasjoner og
-- revisjonslogg. Én tabell om gangen ble tre feilrunder; settet er lest ut
-- av triggerens egen kropp, ikke gjettet.
GRANT SELECT ON unntak, oppdrag, reparasjonsoperasjoner
    TO disponit_policy_eier;
