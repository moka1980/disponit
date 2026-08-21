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
