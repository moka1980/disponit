-- 060: inndata-resolveren (#162 PR-2) — modulens lesevei, i B-FORM.
--
-- VALG B (#200, delegat-dom på Codex' P1 fra #196-mergen): RADEN er den
-- eneste sannheten om hvilken bunt et oppdrag eier. Payloaden navngir
-- ingen bunt lenger (`soknadsbunt_ref` avpubliseres fra kontrakten i
-- egen endring), og modulen slår derfor opp via sitt EGET claimede
-- oppdrag — aldri via en id fra payloaden. `inndata_artefakt_oppdrag`
-- er UNIK: ett oppdrag, høyst én bunt, og `bind_inndata` er koblingens
-- eneste skriver.
--
-- KORREKSJON AV 059s Z1-KOMMENTARER (#200; 059 er byte-frossen og kan
-- aldri redigeres): 059:8-10/236-237/678-682 sier at fødselsattesten
-- «håndhever Z1». Det er for sterkt — samme transaksjon gir ATOMISITET
-- (payload og binding lander sammen eller ikke), ikke ENIGHET om at de
-- navngir samme bunt. Enigheten er ikke lenger et krav noe sted: under
-- valg B finnes det ingen payload-referanse å være enig med, og
-- resolveren her leser KUN bindingsraden.
--
-- Modulen er TENANTLØS (035): autorisasjonen er CLAIMET — og claimet
-- er en KAPABILITET, ikke bare en tilstand (CodeRabbit major,
-- pre-commit): to deployments av samme modul skal ikke kunne hente
-- hverandres bunter, så kalleren må presentere `owner_claim_id`-en den
-- fikk av claim-svaret, og predikatet krever likhet mot radens. Retten
-- til bunten er nøyaktig retten til å holde det plukkede oppdraget den
-- er bundet til — ikke en rolle, ikke et scope, ikke en liste. Funksjonen
-- er kryss-tenant med definer-eieren (domene_eier, 016/019-formen:
-- avgjørelsen er iboende kryss-tenant), og hele predikatet står i én
-- spørring: bundet inndata ∧ samme eiermodul begge sider ∧ oppdraget
-- PLUKKET. Ingen rad → ingenting, samme svar uansett årsak (et
-- oppslagsverk over andres bunter skal ikke finnes).

SET LOCAL ROLE disponit_domene_eier;

CREATE FUNCTION hent_inndata_for_oppdrag(
    p_oppdrag_id BIGINT, p_eiermodul TEXT, p_owner_claim_id TEXT)
RETURNS TABLE (tenant TEXT, lager_sti TEXT, key_id TEXT, nonce BYTEA,
               innhold_sha256 TEXT, faktiske_bytes BIGINT)
LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog AS $$
    SELECT i.tenant, i.lager_sti, i.key_id, i.nonce, i.innhold_sha256,
           i.faktiske_bytes
      FROM public.inndata_artefakt i
      JOIN public.oppdrag o
        ON o.tenant = i.tenant AND o.id = i.oppdrag_id
     WHERE i.oppdrag_id = p_oppdrag_id
       AND i.status = 'bundet'
       AND i.eiermodul = p_eiermodul
       AND o.eiermodul = p_eiermodul
       AND o.status = 'plukket'
       AND o.owner_claim_id = p_owner_claim_id;
$$;

REVOKE ALL ON FUNCTION hent_inndata_for_oppdrag(BIGINT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hent_inndata_for_oppdrag(BIGINT, TEXT, TEXT)
    TO disponit;

RESET ROLE;
