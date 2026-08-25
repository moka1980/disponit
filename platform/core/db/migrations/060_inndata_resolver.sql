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
-- PLUKKET MED LEVENDE LEASE ∧ SAMME DEPLOYMENT. Ingen rad → ingenting,
-- samme svar uansett årsak (et oppslagsverk over andres bunter skal ikke
-- finnes).
--
-- DEPLOYMENTEN, IKKE BARE MODULEN (Codex P1, #202). `owner_claim_id`
-- alene sier at kalleren har SETT et claim-svar, ikke hvem som fikk det.
-- Sammen med `eiermodul` er hele kapabiliteten «en deployment av
-- m57_ats som kjenner strengen» — og en modul har normalt flere levende
-- deployments samtidig (staging og prod, gammel og ny release; 035 §5b/
-- §5c binder derfor artefakt- og kvitteringskapabiliteter til `miljo` +
-- `release_id`, ikke til modulen). Lekket eller misrutet claim_id lot da
-- en staging-deployment hente prod-bunten til samme modul: nøyaktig den
-- hullsklassen 035 alt hadde lukket for de andre kapabilitetene.
--
-- Ingen ny maskin trengs for å lukke den (K1): claim-porten stempler
-- allerede `oppdrag.claim_release_id`/`claim_miljo` (049:77-78, satt i
-- 049:294) med NØYAKTIG den deploymenten porten verifiserte som den
-- claiming (049:249-272), og modultokenet bærer allerede sitt eget
-- `miljo`/`release_id` gjennom `ModulAutentisert` (035). Vi sammenligner
-- de to. Likhet, ikke `IS NOT DISTINCT FROM`: et pre-049-claim, eller et
-- legacy-claim på en uregistrert oppdragstype, står med NULL i sporet og
-- skal da ikke kunne hente noe — en rad som ikke vet hvilken deployment
-- som tok den, kan ikke svare på om kalleren er den.
--
-- LEASEN ER EN DEL AV RETTEN (Codex P1, #202). `plukket` alene er ikke
-- holdet: etter `owner_lease_utloper` er raden reclaimbar (015:198-199,
-- 037:105-106, 005:894-895 — `plukket AND owner_lease_utloper < now()`),
-- men status og `owner_claim_id` står urørt til noen faktisk tar den.
-- I det vinduet ville en kapabilitet fra en død holder fortsatt hentet
-- PII, og hvis ingen reclaimer noen gang kommer, ville den gjort det for
-- alltid. Verre: mellom utløpet og reclaimen kunne BÅDE den gamle
-- holderen og den nye claimeren lese samme bunt. «Retten til bunten er
-- retten til å holde claimet» (over) er da bare sann så lenge holdet
-- varer, og leddet under er nøyaktig det. Fail-closed mot NULL: en rad
-- uten lease har ingen holdefrist å vise til.

SET LOCAL ROLE disponit_domene_eier;

CREATE FUNCTION hent_inndata_for_oppdrag(
    p_oppdrag_id BIGINT, p_eiermodul TEXT, p_owner_claim_id TEXT,
    p_release_id TEXT, p_miljo TEXT)
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
       AND o.owner_claim_id = p_owner_claim_id
       AND o.owner_lease_utloper IS NOT NULL
       AND o.owner_lease_utloper > pg_catalog.now()
       AND o.claim_release_id = p_release_id
       AND o.claim_miljo = p_miljo;
$$;

REVOKE ALL ON FUNCTION
    hent_inndata_for_oppdrag(BIGINT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
    hent_inndata_for_oppdrag(BIGINT, TEXT, TEXT, TEXT, TEXT)
    TO disponit;

RESET ROLE;
