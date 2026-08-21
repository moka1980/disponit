-- 050 — m56-akseptflipp-v2: SYNLIG grenserevisjon
-- (BESLUTNING-AKSEPTFLIPP-049 §1, valg a).
--
-- ENDRINGSNOTAT — hvorfor grensen reversjoneres:
--   FJERNET: `egress.proxytoken_til_ikke_ekstern_lesing`. Punktet
--   krevde negativ måling av en mekanisme som ikke finnes: ingen
--   komponent utsteder egress-tokens (egress-rollen og
--   `v_domeneautorisasjon` finnes; en utsteder gjør det ikke, og
--   evidens.jsonl har ingen hendelse som ber om et token). Det brøt
--   grunnregelen «ingen deploy-port skal kreve en tilstand bare senere
--   arbeid kan skape» — og mappingen til port24-tallet var pynting av
--   det (Codex P1, #117 runde 5). UMAALTE-mekanismen som blokkerte er
--   ratifisert som stående: et umålt punkt blokkerer, aldri pyntes.
--   ERSTATTET AV de to målbare invariantene som faktisk bærer
--   egress-sikkerheten i dag:
--     * `egress.sideeffektklasse_gater_aktivering` — 036-porten:
--       aktiveringsveien KREVER `ekstern_lesing`-klassifisering med
--       målautorisasjonsflagg; porttestene er røde ved brudd
--       (ci_kjoring).
--     * `egress.hemmeligheter_i_browsermiljo` — port24-tallet under
--       sitt RIKTIGE navn: målingen av at DISPONIT_KEK/DATABASE_URL
--       aldri når browser-containerens miljø (evidensfil).
--   FREMTID: bygges en utstedende egress-proxy, får DEN arcen
--   proxytoken-punktet tilbake i sin egen grense, med negativ måling
--   mot den faktiske utstederen. Intensjonen skal ikke forsvinne med
--   punktet — den står her.
--
-- Registeret er append-only med krav-lås (`akseptkrav_punkt_ingen_
-- tillegg`): et registrert krav kan aldri endres eller utvides. En
-- revisjon ER derfor et nytt krav_id — nøyaktig formen 049 tvinger
-- frem, og grunnen til at denne fila finnes i stedet for en UPDATE.
-- v1-radene består som historie; ingen aksept ble skrevet mot dem.

INSERT INTO akseptkrav_punkt (krav_id, punkt, kilde_type, grenseverdi,
                              maalt_krav) VALUES
    ('m56-akseptflipp-v2', 'kontroll.ti_kjoringer_signert_innen_frist',
     'evidensfil', '10/10', '10/10'),
    ('m56-akseptflipp-v2', 'funn.avvik_mot_fasit', 'evidensfil', '0', '0'),
    ('m56-akseptflipp-v2', 'robots.brudd_i_mallogg', 'evidensfil', '0', '0'),
    ('m56-akseptflipp-v2', 'frekvens.over_grense_utfort',
     'evidensfil', '0', '0'),
    -- NY (erstatter proxytoken-punktet, ledd 2): port24 under riktig navn.
    ('m56-akseptflipp-v2', 'egress.hemmeligheter_i_browsermiljo',
     'evidensfil', '0', '0'),
    ('m56-akseptflipp-v2', 'skjema.brudd_promotert',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v2', 'skjema.hash_uten_rad_akseptert',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v2', 'skjema.mutert_ureferert',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v2', 'skjema.slettet',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v2', 'rapport.uten_pakrevd_arlighetsfelt_akseptert',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v2', 'rapport.klartekst_i_logg_eller_dump',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v2', 'domene.kontroll_uten_verifisering',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v2', 'payload.felt_utover_skjema_utlevert',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v2', 'deploy.registerrad_uten_kodefestet_type',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v2',
     'deploy.ekstern_lesing_uten_malautorisasjonsflagg',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v2', 'klasse.eksisterende_kontrakt_omklassifisert',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v2', 'klasse.aktivering_uten_frekvensgrense_lyktes',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v2', 'klasse.aktivering_uten_malautorisasjon_lyktes',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    -- NY (erstatter proxytoken-punktet, ledd 1): 036-porten.
    ('m56-akseptflipp-v2', 'egress.sideeffektklasse_gater_aktivering',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v2', 'malautorisasjon.ikke_registrert_vilkar_talte',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v2', 'malautorisasjon.feil_maldomene_godtatt',
     'ci_kjoring', '0 (porttest rød ved brudd)',
     '0 (grønn CI på akseptcommiten)'),
    ('m56-akseptflipp-v2', 'malautorisasjon.positiv_sti_virker',
     'ci_kjoring', 'ja', 'ja');

-- CI-kravet følger grensen: samme arbeidsflyt-kontrakt som v1.
INSERT INTO akseptkrav_ci (krav_id, arbeidsflyt, hendelse, gren,
                           konklusjon) VALUES
    ('m56-akseptflipp-v2', '.github/workflows/ci.yml', 'push', 'main',
     'success');

-- ------------------------------------------------------------
-- EVIDENSATTESTEN MÅ TÅLE AT KRAVET REVISJONERES (Codex P2, #121).
--
-- 049 nøklet `evidensfil_attest` på `(sha256, punkt)` — «attesten hører
-- til BYTENE, ikke til stien» — men la samtidig `krav_id` inn i
-- motsigelseskontrollen: et referat som spriker fra det lagrede på
-- krav_id, sti ELLER måletall ble avvist. De to påstandene er ikke
-- forenlige når grensen reversjoneres.
--
-- Fire av v2s evidensbårne punkter er GJENBRUKT fra v1
-- (`kontroll.ti_kjoringer_signert_innen_frist`, `funn.avvik_mot_fasit`,
-- `robots.brudd_i_mallogg`, `frekvens.over_grense_utfort`). Er de
-- samme bytene alt attestert under `wcag-kontroll-v1` — samme sha,
-- samme sti, samme måletall — så avviser `attester_evidensfil`
-- v2-kallet med «alt attestert med et annet innhold», selv om
-- INNHOLDET er identisk og bare kravrevisjonen er ny. Aksepten som
-- 050 finnes for å muliggjøre, ville dødd på sin egen revisjon.
--
-- Én immutabel fillesning skal kunne bære FLERE kravrevisjoner.
-- Kravet går derfor inn i attestens identitet — som det alt er i
-- oppslaget `aksepter_moduldeployment` gjør (`AND e.krav_id =
-- p_krav_id`) og i FK-en mot `akseptkrav_punkt (krav_id, punkt)`.
--
-- Og invarianten 049 ville ha — «én fil har ett innhold» — mistes
-- ikke: den flyttes ut av nøkkelen og inn i en EGEN kontroll som
-- gjelder PÅ TVERS av kravrevisjoner. Samme bytes, samme punkt, to
-- ulike måletall er fortsatt to motstridende referater av én fil, og
-- det høres fortsatt — nå også når de to står under hvert sitt krav.
-- MEN: en kontroll som flyttes ut av nøkkelen mister nøkkelens
-- serialisering og må ta den selv. Funksjonen under låser derfor på
-- bytene før den leser (Codex P2, #121 runde 2).
-- Tabellen eies av migrator (049: «de nye eies av migrator»), så
-- nøkkelbyttet trenger intet eiervindu. Attestene som alt står blir
-- liggende: `(sha256, punkt)` er unik i den gamle nøkkelen, så hver
-- eksisterende rad er unik også i den nye.
ALTER TABLE evidensfil_attest
    DROP CONSTRAINT evidensfil_attest_pkey,
    ADD PRIMARY KEY (sha256, punkt, krav_id);

-- Funksjonen eies av `disponit_modul_eier` (049) og migrator har rollen
-- WITH INHERIT FALSE, så erstatningen skjer i EIERVINDUET —
-- 047/048-disiplinen. CREATE OR REPLACE beholder eier og ACL, så
-- REVOKE/GRANT-bildet fra 049 står uendret: ingen GRANT til
-- `disponit_modules_admin`, EXECUTE bare til `disponit_ci_verifikator`.
SET LOCAL ROLE disponit_modul_eier;

CREATE OR REPLACE FUNCTION attester_evidensfil(
    p_krav_id TEXT, p_sti TEXT, p_sha256 TEXT, p_punkter JSONB,
    p_aktor TEXT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog AS $$
DECLARE v_sha TEXT; v_punkt TEXT; v_verdi TEXT; v_annet RECORD;
BEGIN
    v_sha := lower(p_sha256);
    IF v_sha !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'attester_evidensfil: «%» er ingen sha256 — en'
            ' attest som ikke navngir bytene, binder ingenting', p_sha256
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_punkter IS NULL OR jsonb_typeof(p_punkter) <> 'object'
       OR p_punkter = '{}'::jsonb THEN
        RAISE EXCEPTION 'attester_evidensfil: ingen punkter — en lesning'
            ' uten måletall er ikke et referat'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    -- LÅSEN FØR KONTROLLEN (Codex P2, #121 runde 2).
    --
    -- I 049 var motsigelseskontrollen under gjorde jobben SAMMEN med
    -- nøkkelen: PK-en var `(sha256, punkt)`, så to samtidige attester om
    -- de samme bytene kolliderte i unikindeksen uansett hva SELECT-en
    -- rakk å se. Nøkkelbyttet over gir bort nettopp den serialiseringen
    -- — `(sha256, punkt, krav_id)` lar to revisjoner stå side om side,
    -- som er hele poenget, men da kan også to SESJONER som skriver hver
    -- sin revisjon begge se ingen konflikt og begge sette inn. Resultatet
    -- ville vært to immutable, motstridende referater om én fil: akkurat
    -- det kontrollen finnes for å hindre, tapt i et kappløp.
    --
    -- Låsen er transaksjonsscopet og slippes ved commit/rollback. Den
    -- nøkles på BYTENE, ikke på (bytes, punkt): hele kallet er ÉN
    -- fillesning, alle punktene hører til samme sha, og én lås pr. kall
    -- er både en overmengde av det invarianten trenger og fri for
    -- låserekkefølge mellom punktene. Attester skrives én gang pr. fil
    -- pr. grense, så det koster ingenting å ta den grovt.
    PERFORM pg_advisory_xact_lock(hashtextextended('evidensfil:' || v_sha,
                                                   0));
    FOR v_punkt, v_verdi IN
        SELECT j.k, j.v #>> '{}' FROM jsonb_each(p_punkter) AS j(k, v)
         ORDER BY j.k LOOP
        IF v_verdi IS NULL THEN
            RAISE EXCEPTION 'attester_evidensfil: punkt % mangler måletall',
                v_punkt USING ERRCODE = 'invalid_parameter_value';
        END IF;
        -- FØRST: «én fil har ett innhold», på tvers av kravrevisjoner.
        -- Kontrollen er 049s, minus krav_id-leddet som nå er en del av
        -- nøkkelen: to referater om de SAMME bytene og det SAMME punktet
        -- skal si det samme, uansett hvilken grense som spurte. Sprik i
        -- sti eller måletall er fortsatt en programfeil.
        SELECT * INTO v_annet FROM public.evidensfil_attest e
         WHERE e.sha256 = v_sha AND e.punkt = v_punkt
           AND (e.sti IS DISTINCT FROM p_sti
                OR e.maalt_verdi IS DISTINCT FROM v_verdi)
         LIMIT 1;
        IF FOUND THEN
            RAISE EXCEPTION 'attester_evidensfil: sha256:% er alt'
                ' attestert for punkt % med et annet innhold («%» fra'
                ' «%», krav %) — én fil har ett innhold, og et referat'
                ' som spriker fra det lagrede er en programfeil',
                v_sha, v_punkt, v_annet.maalt_verdi, v_annet.sti,
                v_annet.krav_id
                USING ERRCODE = 'invalid_parameter_value';
        END IF;
        -- SÅ: replay for NØYAKTIG dette kravet er en no-op (SP-2), og
        -- en ny kravrevisjon får sin egen rad om de samme bytene.
        -- Innholdet er alt bevist likt av kontrollen over, så det som
        -- gjenstår er om DENNE revisjonens rad finnes.
        IF NOT EXISTS (SELECT 1 FROM public.evidensfil_attest e
                        WHERE e.sha256 = v_sha AND e.punkt = v_punkt
                          AND e.krav_id = p_krav_id) THEN
            INSERT INTO public.evidensfil_attest (sha256, punkt, krav_id,
                sti, maalt_verdi, aktor, attestert_av)
            VALUES (v_sha, v_punkt, p_krav_id, p_sti, v_verdi, p_aktor,
                    session_user);
        END IF;
    END LOOP;
END $$;

RESET ROLE;
