-- 184 — Retensjonen for partsregisteret: kontaktpunktet dør når
-- kundeforholdet er over.
--
-- FØR NOEN FLATE KAN SKRIVE HIT. 183 la registeret; denne gjør det
-- reapbart og navngir det i M-4s register. Rekkefølgen er med vilje:
-- eiers egen gjennomgang fant at «M-4s retensjonsregister mangler de
-- fleste modulenes lagre → personvernsaker kan ikke navngi dem», og et
-- nytt lager med personopplysninger skulle ikke bli det neste som
-- manglet. Ingen kundedata finnes ennå — importen kommer i PR 4.
--
-- HVA SOM DØR OG HVA SOM BESTÅR (088s form, ordrett): adressen dør,
-- sporet består. Reaperen blanker maske, ciphertext, nonce og nøkkel-id
-- og setter `slettet_ts`. `verdi_pseudonym` BLIR STÅENDE, og det er
-- 078s egen dom: pseudonymet er ikke-reverserbart, klarteksten fantes
-- aldri i kolonnen, og unikheten må bestå etter TTL-utløp. «Denne
-- parten hadde et kontaktpunkt» er ikke personopplysningen — adressen
-- var det.
--
-- FRISTEN LØPER FRA DEAKTIVERINGEN, ikke fra opprettelsen, og det er
-- forskjellen mellom dette lageret og en e-postmelding. En melding er
-- en hendelse som blir gammel. En kunde er et FORHOLD som varer, og
-- adressen skal virke så lenge forholdet gjør. Klokken starter når
-- eieren sier at kunden er avviklet.
--
-- HVOR MANGE DØGN ER TENANTENS VALG, ikke husets. Standarden er 90, som
-- e-postinntaket, og kolonnen ligger på parten så hver kunde kan ha sin
-- egen. Dette er en DATAMINIMERINGSREGEL og ingen juridisk påstand:
-- oppbevaringsplikten gjelder bilagene og fakturaene, ikke
-- kontaktregisteret, og de lever i sine egne lagre med sine egne
-- frister.

ALTER TABLE part ADD COLUMN slettefrist_dogn INT NOT NULL DEFAULT 90
    CONSTRAINT part_frist_intervall CHECK (slettefrist_dogn BETWEEN 30 AND 365);
COMMENT ON COLUMN part.slettefrist_dogn IS
    'Døgn fra `deaktivert_ts` til kontaktpunktene reapes. Tenantens'
    ' valg (30–365, standard 90); fristen løper fra AVVIKLINGEN, ikke'
    ' fra opprettelsen — en kunde er et forhold, ikke en hendelse.';

-- ------------------------------------------------------------------
-- REAPEREN. Kryss-tenant (038-læren, 057-blokken som `reap_epostdata`):
-- den setter radens tenant selv og legger konteksten tilbake etterpå.
-- ------------------------------------------------------------------
--
-- KRYSS-TENANT KREVER SIN EGEN RADPOLICY, og dette er 088s form ordrett.
-- `part`/`partkontakt` har FORCE RLS og `tenant_isolasjon`, som måler
-- mot `disponit.tenant`. Reaperens FØRSTE spørring går FØR den har satt
-- noen kontekst — uten en policy for claimeren ville den sett bare den
-- tenanten som tilfeldigvis sto i sesjonen, og en ryddejobb som rydder
-- én kunde av gangen avhengig av hvem som kalte den, er ingen ryddejobb.
--
-- BREDDEN ER REELL OG DEN ER INNELUKKET: claimeren ser alle rader, men
-- alle de PER-TENANT dørene (183) krever `krev_tenantkontekst` og
-- filtrerer på parameteret, så bredden finnes bare inne i reaperen.
-- Alternativet, BYPASSRLS på rollen, ville gitt samme bredde overalt.
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['part', 'partkontakt'] LOOP
        EXECUTE format(
            'CREATE POLICY part_reaper ON %I TO disponit_m37_claimer
                USING (CURRENT_USER = ''disponit_m37_claimer'')
                WITH CHECK (CURRENT_USER = ''disponit_m37_claimer'')', t);
    END LOOP;
END $$;

SET LOCAL ROLE disponit_m37_claimer;

CREATE FUNCTION reap_partkontakt(p_grense INT DEFAULT 50)
RETURNS TABLE (tenant TEXT, kontakt_id UUID)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE r RECORD; v_kontekst TEXT; v_naa TIMESTAMPTZ;
BEGIN
    v_kontekst := current_setting('disponit.tenant', true);
    v_naa := pg_catalog.now();
    FOR r IN
        SELECT k.tenant AS t, k.kontakt_id AS kid
          FROM public.partkontakt k
          JOIN public.part p
            ON p.tenant = k.tenant AND p.part_id = k.part_id
         WHERE k.slettet_ts IS NULL
           AND p.deaktivert_ts IS NOT NULL
           AND v_naa > p.deaktivert_ts
                       + p.slettefrist_dogn * interval '1 day'
         ORDER BY p.deaktivert_ts
         LIMIT p_grense
         FOR UPDATE OF k SKIP LOCKED
    LOOP
        PERFORM set_config('disponit.tenant', r.t, true);
        UPDATE public.partkontakt
           SET verdi_maske = NULL, verdi_kryptert = NULL,
               verdi_nonce = NULL, verdi_key_id = NULL,
               primar = false,
               slettet_ts = v_naa, slettet_av = 'retensjon'
         WHERE partkontakt.tenant = r.t
           AND partkontakt.kontakt_id = r.kid;
        tenant := r.t; kontakt_id := r.kid;
        RETURN NEXT;
    END LOOP;
    -- Konteksten legges tilbake slik den var — en reaper som lot en
    -- fremmed tenant stå igjen i sesjonen ville vært en lekkasje i det
    -- neste kallet, ikke i sitt eget.
    PERFORM set_config('disponit.tenant', coalesce(v_kontekst, ''), true);
END $$;
REVOKE ALL ON FUNCTION reap_partkontakt(INT) FROM PUBLIC;

-- Samme vaktede grantform som 088: HAR oppsettet en egen ryddekonto,
-- hører reaperen dit og web-API-rollen skal ikke ha den. Et grant som
-- bare slutter å bli gitt er ikke trukket tilbake.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit_domener') THEN
        GRANT EXECUTE ON FUNCTION reap_partkontakt(INT) TO disponit_domener;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
            REVOKE EXECUTE ON FUNCTION reap_partkontakt(INT) FROM disponit;
        END IF;
    ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'disponit') THEN
        GRANT EXECUTE ON FUNCTION reap_partkontakt(INT) TO disponit;
    END IF;
END $$;

RESET ROLE;

-- Claimeren må kunne blanke payloaden; 183 ga den SELECT/INSERT/UPDATE,
-- og UPDATE dekker dette. Linjen står her som en påminnelse om at
-- reaperen ER en skrivevei, ikke en lesevei.

-- ------------------------------------------------------------------
-- M-4: LAGRENE NAVNGIS. Et lager som ikke står her, kan ingen
-- personvernsak dekke — og det var nettopp funnet fra eiers
-- gjennomgang.
-- ------------------------------------------------------------------
-- MÅLEREN MÅ NÅ LAGERET SITT, og det kommer ikke av seg selv
-- (CodeRabbit, verifisert ved å fjerne og kjøre migrasjonen om igjen).
-- 093 §6.3 deler ut kolonnegrantene og `m4_maaler`-policyen i en
-- ENGANGS `DO`-blokk, utledet av registeret slik det så ut DA. Et lager
-- registrert senere får ingenting — og et målt lager måleren ikke kan
-- lese, er en registrering uten måling.
--
-- KOLONNEGRANT, ALDRI TABELLGRANT, og nøyaktig de tre kolonnene
-- registerraden navngir: tenant, alder og reap-markør. Ingen
-- payloadkolonne, og porten i test_m4_retensjon måler nettopp det.
-- `part` trenger ingenting: den er `uten_frist_akseptert` og måles
-- ikke.
GRANT SELECT (tenant, opprettet, slettet_ts) ON partkontakt
    TO disponit_lager_eier;
CREATE POLICY m4_maaler ON partkontakt TO disponit_lager_eier
    USING (CURRENT_USER = 'disponit_lager_eier');

SET LOCAL ROLE disponit_lager_eier;
INSERT INTO retensjonslager
    (lager_id, relasjon, klasse, tenantkolonne, alderskolonne,
     reapetkolonne, fristkilde, frist_dogn, reaper, dom,
     dom_begrunnelse, dom_migrasjon)
VALUES
    -- PARTEN SELV har ingen frist, og grunnen er valgt, ikke glemt:
    -- raden er selve kundeforholdet, og den blir referert av fordringer,
    -- tilbud og prosjekter som har sine EGNE oppbevaringsplikter. Å
    -- slette parten under dem ville etterlatt bilag som peker i løse
    -- luften. Navnet består; adressen gjør det ikke.
    ('part', 'part', 'persondata', 'tenant', 'opprettet',
     NULL, NULL, NULL, NULL, 'uten_frist_akseptert',
     'Parten ER kundeforholdet og blir referert av fordringer, tilbud'
     ' og prosjekter med egne oppbevaringsplikter. Navnet og'
     ' referansen bestaar; kontaktpunktene har sin egen frist og'
     ' reapes av reap_partkontakt.', '184'),
    ('partkontakt', 'partkontakt', 'persondata', 'tenant', 'opprettet',
     'slettet_ts', 'part.slettefrist_dogn regnet fra part.deaktivert_ts',
     90, 'reap_partkontakt', 'under_frist',
     'Adressen doer naar kundeforholdet er avviklet: fristen loeper fra'
     ' deaktiveringen, ikke fra opprettelsen. Pseudonymet bestaar (078)'
     ' — det er ikke-reverserbart, og unikheten maa overleve'
     ' TTL-utloepet.', '184');
RESET ROLE;
