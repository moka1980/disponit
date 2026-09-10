-- 175 — M-6 innhenteren (PR-C a, «M-6 inntak», modul 6 i ARC B-rekka):
-- planarbeiderens runde henter meldingene fra den tilkoblede postboksen
-- (Graph, kun lesende — dommen 31/8) og skriver dem i `epost_melding`
-- slik 088 tegnet det: kroppen tenant-DEK-kryptert, avsender og emne
-- som hasher, ON CONFLICT DO NOTHING på (tenant, kilde_id,
-- leverandor_melding_id). Ingen modellvei, ingen sendevei, ingen flate
-- her (PR-D er neste).
--
-- INNHENTERENS ROLLE ER PLANARBEIDEREN (`disponit_plan_arbeider`): den
-- kjører alt i API-ets tillitsnivå (m37-presedensen, samme nøkkelregistre)
-- og har alt DEK-veien (`tenant_nokler`). 088 nektet web-API-rollen
-- credential-trioen med vilje; innhenteren MÅ ha den — det er
-- «innhenterens vei som får sine grants når den fødes». Grantene står
-- i migrer.py PLAN_RETTIGHETER (planrollens tabellrettigheter nullstilles
-- og settes der ved hver kjøring); speilporten der måler at runtime-rollen
-- fortsatt er uten trioen.
--
-- Kryss-tenant-kandidatdøra eies av CLAIMEREN (088/057-formen): dens
-- `m6_reaper`-policy dekket fire lagre, ikke kilden — kilden får sin
-- egen kryss-tenant-policy for claimeren her. Døra nekter tenantkontekst
-- (102-formen); innhenteren setter radens tenant selv før den leser
-- trioen og skriver meldingene under `tenant_isolasjon`.

CREATE POLICY m6_innhenter_kilder ON epost_kilde TO disponit_m37_claimer
    USING (CURRENT_USER = 'disponit_m37_claimer');
-- Claimeren fikk i 088 SELECT på fire lagre, ikke kilden: døra trenger
-- kildens hentemerker — KOLONNEGRANT uten credential-trioen (trioen er
-- innhenterens, under tenantkontekst).
GRANT SELECT (tenant, kilde_id, postboks, status, delta_token, sist_hentet_ts,
    opprettet) ON epost_kilde TO disponit_m37_claimer;

SET LOCAL ROLE disponit_m37_claimer;

CREATE FUNCTION m6_hentekandidater(p_grense INT)
RETURNS TABLE(tenant TEXT, kilde_id UUID, postboks TEXT,
              delta_token TEXT, sist_hentet_ts TIMESTAMPTZ)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog AS $$
BEGIN
    IF nullif(current_setting('disponit.tenant', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'm6_hentekandidater: kryss-tenant-døra nekter'
            ' tenantkontekst' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN QUERY
    SELECT k.tenant, k.kilde_id, k.postboks, k.delta_token, k.sist_hentet_ts
      FROM public.epost_kilde k
     WHERE k.status = 'aktiv'
     ORDER BY k.sist_hentet_ts NULLS FIRST, k.opprettet
     LIMIT greatest(least(coalesce(p_grense, 20), 200), 1);
END $$;
REVOKE ALL ON FUNCTION m6_hentekandidater(INT) FROM PUBLIC;

-- Grantet til planarbeideren bor i migrer.py (PLAN_RETTIGHETER), som
-- resten av plan-familiens claimer-eide definere: tabellrettighetene
-- nullstilles og settes der ved hver kjøring, og porten i
-- test_claim_tillitsgrense måler at listen og kallsettet er samme mengde.

RESET ROLE;

-- Innhenterens tabellrettigheter (trioen å lese, hentemerkene å skrive,
-- meldingene å føde) står i migrer.py PLAN_RETTIGHETER av samme grunn.
