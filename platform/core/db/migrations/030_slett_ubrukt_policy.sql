-- ============================================================
-- 030 — Angre en feilopprettet policy: slett den som ALDRI er brukt
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
--     så en riktig opprettelse etterpå ikke stoppes av 020-monotonien;
--   * utkast og runder RØRES IKKE: at mennesker attesterte er et faktum om
--     fortiden, og det skal stå igjen selv om resultatet angres. (Nøyaktig
--     det de manuelle oppryddingene bevarte.) Det ENESTE kalleren skriver på
--     en runde er `apen|klar → utlopt` for en runde som allerede har passert
--     `utloper` — en overgang som var forfalt uansett, ikke en opprydding;
--     attestasjonene blir stående.
--
-- `revisjonslogg`-kontrollen bruker `LIKE pid || '@%'`: loggformatet er
-- `pid@versjon/handling`, og skjemaet forbyr `@` i policy_id, så prefikset er
-- entydig. Escaping trengs ikke for `@`, men `_`/`%` i pid kan ikke
-- forekomme (`^[a-z0-9-]+$`), så mønsteret kan ikke feiltreffe.
-- ============================================================

-- DROP først (samme lærdom som 027): etter første kjøring eies funksjonen av
-- policy_eier, og migrator kan ikke REPLACE noe den ikke eier — re-kjøringen
-- ville dødd med «must be owner».
DROP FUNCTION IF EXISTS slett_ubrukt_policy(TEXT, TEXT);

CREATE OR REPLACE FUNCTION slett_ubrukt_policy(p_tenant TEXT, p_policy_id TEXT)
RETURNS int
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_brukt int;
    v_apen  int;
    n       int;
BEGIN
    IF p_tenant IS NULL OR btrim(p_tenant) = ''
       OR current_setting('disponit.tenant', true) IS DISTINCT FROM p_tenant
    THEN
        RAISE EXCEPTION 'slett_ubrukt_policy: tenantkontekst mangler/avviker'
            USING ERRCODE = 'insufficient_privilege';
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

    SELECT count(*) INTO v_brukt FROM revisjonslogg
     WHERE tenant = p_tenant AND policy_id LIKE p_policy_id || '@%';
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

    DELETE FROM policyer
     WHERE tenant = p_tenant AND policy_id = p_policy_id;
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n = 0 THEN
        RAISE EXCEPTION 'slett_ubrukt_policy: policyen finnes ikke'
            USING ERRCODE = 'no_data_found';
    END IF;
    RETURN n;
END $$;

ALTER FUNCTION slett_ubrukt_policy(TEXT, TEXT) OWNER TO disponit_policy_eier;
REVOKE ALL ON FUNCTION slett_ubrukt_policy(TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION slett_ubrukt_policy(TEXT, TEXT) TO disponit;
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
