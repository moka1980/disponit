-- ============================================================
-- 029 — Rundene som alt VENTET da modulen ble installert (Codex P2)
--
-- 026 lager en TOM varseltabell, og de eneste veiene som fyller den er en
-- runde som ÅPNES etter oppgraderingen, og en replay av nøyaktig den
-- forespørselen (`_forson_rundevarsling`). Alt som sto og ventet FØR deployen
-- fikk derfor verken portalrad eller e-post.
--
-- Det er ikke et hjørnetilfelle, det er hovedtilfellet. Modulen finnes fordi
-- «en runde står åpen og venter på en uavhengig godkjenner, men ingenting
-- forteller henne det» (026, eiers behov). De rundene som står slik i det
-- øyeblikket deployen kjører, er nettopp de menneskene har ventet lengst på —
-- og uten dette steget var de de eneste som aldri ble varslet. Funksjonen
-- ville sett ut som den virket: alt NYTT fungerte, mens etterslepet var
-- usynlig, og eneste vei ut var at en klient tilfeldigvis sendte den gamle
-- idempotensnøkkelen om igjen.
--
-- EGEN FIL, IKKE EN UTVIDELSE AV 026. Historikken er immutable — kjøreren
-- avviser en migrasjon som er endret etter at den er kjørt (checksum). Lå
-- backfillen i 026, ville den aldri kjørt på en base som alt har 026, og det
-- er nettopp de basene den finnes for.
--
-- KJØRER ÉN GANG, men er trygg å kjøre om igjen: `ON CONFLICT DO NOTHING` på
-- hendelsesnøkkelen fra 026 er den samme idempotensen `varsel.opprett` bruker.
-- En rad som alt finnes — fordi runden ble åpnet etter 026 — røres ikke.
--
-- REGLENE ER SPEILET FRA PYTHON, og det er en bevisst DUPLISERING:
-- `varsel.mottakere_for_runde`, `policyadmin._runde_status` og
-- `policyadmin._gjenstaar_effektivt` er kildene, og de kan endre seg. Denne
-- filen kan ikke. Det er riktig sånn: en backfill er et ØYEBLIKKSBILDE av hva
-- som var sant da modulen ble installert, ikke en andre implementasjon som
-- skal holdes i takt. Endres reglene senere, gjelder de for det som skjer
-- etterpå — denne kjøringen er da for lengst historie i `migrasjoner`.
-- ============================================================

-- ------------------------------------------------------------
-- RLS-VINDUET, samme grep og samme grunn som migrasjon 008.
--
-- Alle tabellene under står med FORCE ROW LEVEL SECURITY mot
-- `disponit.tenant`, og migrator har ingen tenantkontekst. Med FORCE på ville
-- SELECT-en truffet null rader og INSERT-en satt inn ingenting — og
-- migrasjonen ville sett ut som en suksess. (Nøyaktig FIX-008-feilklassen: en
-- tilkobling uten kontekst ser ingenting, og tomhet ligner på at det ikke var
-- noe å gjøre.) Backfillen er dessuten KRYSS-TENANT av natur: den skal treffe
-- hver kundes ventende runder, og det finnes ingen ett-tenant-kontekst som er
-- riktig å sette.
--
-- FORCE slås derfor av for EIEREN i vinduet og på igjen rett etter
-- kontrollsteget, alt i samme transaksjon — feiler noe underveis, rulles også
-- RLS-endringen tilbake. Vanlige roller er urørt hele veien: NO FORCE unntar
-- kun tabelleieren, og `ALTER TABLE` holder ACCESS EXCLUSIVE, så ingen andre
-- sesjon kan lese i vinduet.
--
-- Alternativet — `SET LOCAL ROLE disponit_domene_eier` (BYPASSRLS), som 027
-- bruker for gjerdet sitt — ville krevd nye, PERMANENTE grants (INSERT på
-- `varsel`, SELECT på fire tabeller til) for et steg som kjører én gang.
-- En engangsjobb skal ikke etterlate seg stående rettigheter.
-- ------------------------------------------------------------
ALTER TABLE varsel                 NO FORCE ROW LEVEL SECURITY;
ALTER TABLE varselvalg             NO FORCE ROW LEVEL SECURITY;
ALTER TABLE aktiveringsrunde       NO FORCE ROW LEVEL SECURITY;
ALTER TABLE aktiveringsattestasjon NO FORCE ROW LEVEL SECURITY;
ALTER TABLE policyutkast           NO FORCE ROW LEVEL SECURITY;
ALTER TABLE brukermedlemskap       NO FORCE ROW LEVEL SECURITY;

INSERT INTO varsel (tenant, bruker_id, art, ressurs_type, ressurs_id,
                    hendelse, tekstnokkel, parametre, epost_status)
SELECT r.tenant,
       m.bruker_id,
       'attestering_venter',
       'policyutkast',
       r.utkast_id,
       -- Hendelsen er RUNDENUMMERET, som i `varsle_runde_venter`. Det er den
       -- som skiller «samme hendelse igjen» fra «ny runde på samme utkast»,
       -- og det er den `ON CONFLICT` under måler mot.
       r.runde::text,
       'varsel.attestering_venter',
       jsonb_build_object(
           'policy_id',    u.policy_id,
           'runde',        r.runde,
           'risikoklasse', r.risikoklasse,
           -- `_gjenstaar_effektivt`: terskelen har TO betingelser, og
           -- differansen alene teller bare den ene. Har forfatteren attestert
           -- en INNSNEVRER/NØYTRAL-runde, er `pakrevd - antall` null mens
           -- runden fortsatt venter på den uavhengige godkjenneren — altså på
           -- nøyaktig den som leser dette varselet.
           'gjenstaar',    greatest(
               0,
               r.pakrevd_antall_godkjennere - a.antall,
               CASE WHEN a.ikke_forfatter >= 1 THEN 0 ELSE 1 END)),
       -- Kanalvalget respekteres, som i `varsel.opprett`: et bevisst fravær
       -- skal ikke se ut som en sending som aldri kom. På en fersk
       -- installasjon er `varselvalg` tom og alt blir `koet` — men en base der
       -- 026 alt har kjørt kan ha valg, og de gjelder også her.
       CASE WHEN EXISTS (SELECT 1 FROM varselvalg vv
                          WHERE vv.tenant = r.tenant
                            AND vv.bruker_id = m.bruker_id
                            AND vv.kanal = 'kun_portal')
            THEN 'ikke_aktuelt' ELSE 'koet' END
  FROM aktiveringsrunde r
  JOIN policyutkast u
    ON u.tenant = r.tenant AND u.utkast_id = r.utkast_id
  -- `mottakere_for_runde`: aktive policyforvaltere som IKKE alt har attestert
  -- DENNE runden. «Alt attestert» måles på runden, ikke på utkastet — en
  -- attestasjon binder én rundes diff og følger ikke med videre.
  JOIN brukermedlemskap m
    ON m.tenant = r.tenant
   AND m.aktiv
   AND 'policyforvalter' = ANY(m.roller)
  CROSS JOIN LATERAL (
       SELECT count(*) AS antall,
              count(*) FILTER (WHERE NOT aa.er_forfatter) AS ikke_forfatter
         FROM aktiveringsattestasjon aa
        WHERE aa.tenant = r.tenant AND aa.utkast_id = r.utkast_id
          AND aa.runde = r.runde) a
 -- `_runde_status`: `apen`/`klar` som har passert `utloper` ER `utlopt`, også
 -- før en skrivesti har rukket å skrive det ned. Å varsle om en forfalt runde
 -- ville vært å starte modulen med en løgn i innboksen.
 WHERE r.status IN ('apen', 'klar')
   AND r.utloper > now()
   AND NOT EXISTS (SELECT 1 FROM aktiveringsattestasjon a2
                    WHERE a2.tenant = r.tenant
                      AND a2.utkast_id = r.utkast_id
                      AND a2.runde = r.runde
                      AND a2.bruker_id = m.bruker_id)
ON CONFLICT (tenant, bruker_id, art, ressurs_type, ressurs_id, hendelse)
DO NOTHING;

-- ------------------------------------------------------------
-- KONTROLL: ingen ventende runde står igjen uten varsel til dem som kan
-- bringe den videre. Fail-hard, som i 008 — en backfill som traff null rader
-- fordi RLS eller en join tok dem, skal ikke kunne rapportere suksess.
-- ------------------------------------------------------------
DO $$
DECLARE mangler int;
BEGIN
    SELECT count(*) INTO mangler
      FROM aktiveringsrunde r
      JOIN brukermedlemskap m
        ON m.tenant = r.tenant AND m.aktiv
       AND 'policyforvalter' = ANY(m.roller)
     WHERE r.status IN ('apen', 'klar')
       AND r.utloper > now()
       AND NOT EXISTS (SELECT 1 FROM aktiveringsattestasjon a
                        WHERE a.tenant = r.tenant AND a.utkast_id = r.utkast_id
                          AND a.runde = r.runde AND a.bruker_id = m.bruker_id)
       AND NOT EXISTS (SELECT 1 FROM varsel v
                        WHERE v.tenant = r.tenant AND v.bruker_id = m.bruker_id
                          AND v.art = 'attestering_venter'
                          AND v.ressurs_type = 'policyutkast'
                          AND v.ressurs_id = r.utkast_id
                          AND v.hendelse = r.runde::text);
    IF mangler > 0 THEN
        RAISE EXCEPTION
            'migrasjon 029: % ventende mottakere står uten varsel — avbryter',
            mangler;
    END IF;
END $$;

-- RLS-vinduet lukkes.
ALTER TABLE varsel                 FORCE ROW LEVEL SECURITY;
ALTER TABLE varselvalg             FORCE ROW LEVEL SECURITY;
ALTER TABLE aktiveringsrunde       FORCE ROW LEVEL SECURITY;
ALTER TABLE aktiveringsattestasjon FORCE ROW LEVEL SECURITY;
ALTER TABLE policyutkast           FORCE ROW LEVEL SECURITY;
ALTER TABLE brukermedlemskap       FORCE ROW LEVEL SECURITY;
