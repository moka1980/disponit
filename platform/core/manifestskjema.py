"""Validering av modulmanifester mot manifest-skjema.json (v3-delta pkt. 7).

Registeret (`registry.py`) leser manifester for å bestemme avhengigheter og
aktivering. Det bryr seg ikke om staging-sjekklisten. Sjekklisten er
derimot den ENESTE maskinlesbare kilden til om en modul faktisk er bevist
klar — og uten et skjema er «ja» og «nei» fritekst som kan endres til
hva som helst uten at noe protesterer.

Kjøres i CI. Kaster aldri: feilformet manifest gir feilliste, ikke
exception — samme kontrakt som `policy_validator.schema.valider_policy`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SKJEMA_STI = Path(__file__).resolve().parent / "manifest-skjema.json"
ARTEFAKTSKJEMA_STI = Path(__file__).resolve().parent / "artefakt-skjema.json"
REPOROT = Path(__file__).resolve().parents[2]

#: Grensene ytelsesporten faktisk krever (v2 Del 6). De står HER, som data
#: CI leser, og ikke bare i et manifestnotat: et tall i en kommentar kan
#: ikke gjøre en kjøring rød.
KRAVGRENSER: dict[str, dict] = {
    "wcag-kontroll-v1": {
        # 014c-klarsignalet §12: fasitrunden. 10/10 utført innen frist,
        # null avvik mot fasit, og feilveiene beviste seg (kvittering
        # uten evidens; reaper → sak). Frekvens: taket skal både slippe
        # gjennom OG avvise — en port som aldri målte et avslag har ikke
        # målt taket.
        "min_kjoringer": 10,
        "maks_avvik_mot_fasit": 0,
        "maks_robots_private": 0,
        # 5xx-regresjonen skal være PRØVD: et krav på 0 sider er ikke en
        # bestått port, det er en port som aldri kjørte (Codex, #117).
        "min_robots_5xx_krav": 1,
        # Taket er FIRE. En kjøring som slapp gjennom fem har alt utført
        # en forespørsel over grensen — at den sjette ble avvist redder
        # den ikke. Derfor eksakt, ikke minimum (Codex, #117).
        "frekvens_tillat_eksakt": 4,
        "min_frekvens_avvist": 1,
        "maks_egress_lekkasjer": 0,
        # …og lekkasjetallet teller bare hvis proben KJØRTE (Codex P1,
        # #121). En port24-kjøring som døde med returkode ≠ 0 har null
        # lekkasjer å vise, og «0 ≤ 0» ville lest fraværet av en måling
        # som en bestått port. Samme form som `min_robots_5xx_krav`.
        "min_egress_motormiljo_maalt": 1,
        "min_feilet_med_kvittering": 1,
        "maks_promoterte_ved_feil": 0,
        "min_evidensfrist_reapet": 1,
        "min_evidensfrist_sak": 1,
        # PUNKTBINDING (#166): hvilke MÅLINGER som kan bevise hvert
        # punkt. Et punkt som ikke står her er UFLIPPBART.
        "punktbinding": {
            "feilinjisering_til_unntakskø": (
                "maalt.evidensfrist_reapet",
                "maalt.feilinjisering_feilet_med_kvittering",
                "maalt.feilinjisering_promoterte_artefakter",
            ),
            "tester_gronne_pa_staging": (
                "maalt.avvik_mot_fasit",
                "maalt.kjoringer_rent_innen_frist",
            ),
            "ytelse_bestatt": (
                "maalt.kjoringer_krav",
                "maalt.kjoringer_rent_innen_frist",
            ),
        },
    },
    "rollback-m56-v1": {
        # 049-flippedrillen: den drillede releasen claimer INGENTING
        # etter drenering (målt i minst 20 s), det løpende oppdraget får
        # et rent, signert utfall, og kandidaten plukker og promoterer.
        "maks_claims_etter_drenering": 0,
        "maks_falske_verdikter": 0,
        "min_inflight": 1,
        # (b2) SELVE RULLBAKKEN: den tilbakerullede releasen skal ha
        # BOOTET og gjort arbeid. Uten disse to måler drillen bare at
        # den gamle arbeideren sluttet å claime — en forrige release som
        # ikke lar seg kjøre på verten ga grønt bevis (Codex, #117).
        "min_rullback_claims": 1,
        "min_rullback_promoterte": 1,
        "min_kandidat_claims": 1,
        "min_kandidat_promoterte": 1,
        "min_ventetid_s": 20.0,
        # TO utfall, ikke tre (Codex P2, #117). `avbrutt` sto her, men
        # ingen annen del av apparatet kjenner det: `registrer_moduldrill`
        # regner `rene_utfall_ok` bare for `oppdrag.status IN ('utfort',
        # 'feilet')`, og drillsonden venter bare på de to. Filvalidatoren
        # kunne altså godkjenne — og manifestbindingen merke rullbakk-
        # punktet grønt på — evidens akseptbasen ALDRI kan kvalifisere.
        # En grense som er videre enn den den håndhever, er ingen grense;
        # den er et løfte som brytes ett steg senere.
        "rene_utfall": ("utfort", "feilet"),
        # PUNKTBINDING (#166): hvilke MÅLINGER som kan bevise hvert
        # punkt. Et punkt som ikke står her er UFLIPPBART.
        "punktbinding": {
            "rollback_testet": (
                "maalt.falske_verdikter",
                "maalt.inflight_har_signert_kvittering",
                "maalt.kandidat_claimet_oppdrag",
                "maalt.nye_oppdrag_claimet_av_drillet_release",
                "maalt.overtakelse_s",
                "maalt.rullback_claimet_oppdrag",
                "maalt.rullback_har_signert_kvittering",
            ),
        },
    },
    "perf-m01-v1": {
        "min_antall": 6000,
        "maks_feil": 0,
        "maks_rate_begrenset": 0,
        "maks_p95_ms": 150.0,
        # Lastprofilen er en del av kravet, ikke pynt. 6 000 forespørsler
        # sier ingenting om de ble sendt på ett minutt eller på to timer.
        "min_rate_per_sek": 100.0,
        "min_samtidige": 20,
        # Open-loop-generatoren treffer ikke nominell rate på desimalen, og
        # artefaktet runder til én desimal. 1 % gir rom for det uten å gi
        # rom for en kjøring på halv fart.
        "rate_slingringsmonn": 0.01,
        "varighet_slingringsmonn": 0.05,
        # PUNKTBINDING (#166): hvilke MÅLINGER som kan bevise hvert
        # punkt. Et punkt som ikke står her er UFLIPPBART.
        "punktbinding": {
            "ytelse_bestatt": (
                "etterkontroll.auditerte_svar",
                "etterkontroll.en_til_en",
                "etterkontroll.revisjonsrader",
                "maalt.feil",
                "maalt.svartid_ms.p95",
            ),
        },
    },
    # --- PR-006 -----------------------------------------------------------
    # Begge grensene defineres FØR arbeidet som skal måles (brief §5).
    # Rekkefølgen er ikke pedanteri: `rollback-m01-v1` har manglet i denne
    # dict-en helt siden PR-005c, og et `ja` ble derfor avvist med «ukjent
    # krav_id». Fail-closed var riktig, men det betød at den som skulle
    # gjøre rollback-arbeidet ikke hadde noen fasit å måle mot.
    "feilinjisering-m01-v1": {
        "min_injisert": 20,
        "min_kategorier": 3,
        # Andelene er 1.0 og ikke «minst 0.95». En injisert feil som ikke
        # ble behandlet, er nettopp den tilstanden punktet
        # `feilinjisering_til_unntakskø` skal bevise at ikke finnes.
        "krev_terminal_andel": 1.0,
        "krev_lost_andel": 1.0,
        "krev_manuell_andel": 1.0,
        "maks_varighet_sek": 300.0,
        # Målt MENS arbeideren kjører. Det er hele beviset for
        # prosessisolasjonen: er tallet innenfor mens M-37 maler på samme
        # boks, spiste ikke behandlingen ytelsesmarginen.
        "maks_p95_api_under_last_ms": 150.0,
        # Minst én sak SKAL gjennom lease-tap + re-claim (v2-delta pkt. 8).
        # Uten den er gjenopptaksveien udokumentert — og en gjenopptaksvei
        # som aldri er kjørt er en hypotese.
        "min_lease_tap_re_claim": 1,
        # PUNKTBINDING (#166): hvilke MÅLINGER som kan bevise hvert
        # punkt. Et punkt som ikke står her er UFLIPPBART.
        "punktbinding": {
            "feilinjisering_til_unntakskø": (
                "etterkontroll.historikk_komplett",
                "etterkontroll.klartekst_payload_funnet",
                "maalt.lease_tap_re_claim",
                "maalt.lost_andel",
                "maalt.manuell_andel",
                "maalt.terminal_andel",
            ),
            "ytelse_bestatt": (
                "maalt.p95_api_under_last_ms",
                "maalt.varighet_sek",
            ),
        },
    },
    "rollback-m01-v1": {
        "maks_deaktivering_s": 5.0,
        "maks_reaktivering_s": 5.0,
        "maks_tapte_loggposter": 0,
        "krev_avvist_andel": 1.0,
        "maks_halvferdige": 0,
        # PUNKTBINDING (#166): hvilke MÅLINGER som kan bevise hvert
        # punkt. Et punkt som ikke står her er UFLIPPBART.
        "punktbinding": {
            "rollback_testet": (
                "etterkontroll.radtelling_etter.revisjonslogg",
                "etterkontroll.radtelling_for.revisjonslogg",
                "maalt.deaktivering_effektiv_s",
                "maalt.paagaaende_requests_korrekt_avvist",
                "maalt.tapte_loggposter",
            ),
        },
    },
    # --- PR-012 (menneskelig unntaksbehandling) -------------------------
    # 12 saker over 4 kategorier (spec §10 + v2-delta): avvis-vei terminal ·
    # godkjenn-vei ny beslutning · sideeffekt → venter_utførelse → løst ·
    # fire-øyne to brukere. Pluss de harde invariantene: saksversjonskonflikt
    # gir 409 UTEN sideeffekt · samtidig arbeider + menneske → nøyaktig én
    # vinner · ingen klartekst i logg/dump · alle handlinger med aktør.
    # Andelene er 1.0: en injisert sak som ikke nådde sin terminaltilstand er
    # nettopp hullet artefaktet skal bevise at ikke finnes.
    "behandling-m37-v1": {
        "min_injisert": 12,
        # Kategorimengden må være EKSAKT de fire kontraktskategoriene (avvis,
        # godkjenn, sideeffekt, fire_oyne) — håndheves som settlikhet, ikke
        # som «minst fire» — og hver kategori krever `utfall == injisert > 0`.
        "krev_avvis_terminal_andel": 1.0,
        "krev_godkjenn_beslutning_andel": 1.0,
        # PR-012s menneskelige vei ender ved `venter_utførelse` (levert til
        # M-37-outboxen); →løst tilhører M-37 og bevises av `feilinjisering-m01`.
        "krev_sideeffekt_utforelse_andel": 1.0,
        "krev_fire_oyne_andel": 1.0,
        # Minst én saksversjonskonflikt SKAL kjøres — en 409-vei som aldri er
        # utløst er en hypotese — og den skal ALDRI ha en sideeffekt.
        "min_saksversjonskonflikt": 1,
        "maks_saksversjonskonflikt_sideeffekt": 0,
        # Minst én ekte konkurranse; «nøyaktig én vinner» håndheves fra
        # råtellinger (startet/fullført/vinnere/tapere), ikke et flagg.
        "min_samtidig_konkurranse": 1,
        # Scope-beslutningen §3: den EKTE kvitterings-vs-avvis-rasen (ikke to
        # menneskelige godkjenn). Minst én kjøring; begge tråder fullfører;
        # avvis flagger avklaring og kvitteringen (`bruk_kvitteringskapabilitet`)
        # bevares — og saken påstår ALDRI `avvist` mens et oppdrag lever
        # (`falskt_avvist` = 0, ikke «lite»).
        "min_kvitteringsrace": 1,
        "maks_kvitteringsrace_falskt_avvist": 0,
        # Ingen klartekst-begrunnelse i logg eller DB-dump — 0, ikke «lite».
        "maks_klartekst_treff": 0,
        "maks_varighet_sek": 300.0,
        # PUNKTBINDING (#166): hvilke MÅLINGER som kan bevise hvert
        # punkt. Et punkt som ikke står her er UFLIPPBART.
        "punktbinding": {
            "menneskelig_behandling_bestatt": (
                "maalt.avvis.terminal",
                "maalt.fire_oyne.fullfort",
                "maalt.godkjenn.ny_beslutning",
                "maalt.handlinger_med_aktor",
                "maalt.klartekst_treff",
                "maalt.kvitteringsrace_falskt_avvist",
                "maalt.kvitteringsrace_kvittering_brukt",
                "maalt.samtidig_vinnere",
                "maalt.sideeffekt.til_utforelse",
            ),
        },
    },
    # PR-013: policyadministrasjon. Fire kategori-veier beviser
    # fire-øyne-fullmaktsmodellen; de harde invariantene beviser V10 (runtime
    # kan ikke skrive policyer), atomisiteten (aldri flere aktive) og at
    # godkjenneren attesterte DIFFEN. Alle andeler er 1.0: en injisert vei som
    # ikke nådde sin kontraktstilstand er nettopp hullet artefaktet skal
    # avkrefte.
    "policyadmin-v1": {
        # 4 kategorier × 2 = 8 (én kjøring er en anekdote; to per vei).
        "min_injisert": 8,
        # Kategorimengden EKSAKT (settlikhet), hver vei `utfall == injisert > 0`.
        "krev_utvider_aktivert_andel": 1.0,
        "krev_forfatter_alene_stopp_andel": 1.0,
        "krev_innsnevrer_aktivert_andel": 1.0,
        "krev_rebasering_andel": 1.0,
        # Atomisiteten (V10/V1): INGEN policy ender med mer enn én aktiv rad.
        # 0, ikke «lite».
        "maks_flere_aktive": 0,
        # V10: runtime MÅ nektes direkte skriving til `policyer` (målt: 1).
        "krev_runtime_skrivenekt": 1,
        # Godkjenneren attesterer DIFFEN: hver attestasjons `diff_hash` MÅ matche
        # rundens (treff == totalt > 0).
        "krev_diff_binding_full": True,
        "maks_varighet_sek": 300.0,
        # PUNKTBINDING (#166): hvilke MÅLINGER som kan bevise hvert
        # punkt. Et punkt som ikke står her er UFLIPPBART.
        "punktbinding": {
            "policyadministrasjon_bestatt": (
                "maalt.diff_binding_treff",
                "maalt.forfatter_alene.stoppet",
                "maalt.innsnevrer.aktivert",
                "maalt.policyer_med_flere_aktive",
                "maalt.rebasering.rebasert",
                "maalt.runtime_skrivenekt",
                "maalt.utvider.aktivert",
            ),
        },
    },
}

# --- 052 (aksept-arc-klarsignalet §1) ---------------------------------
# `wcag-kontroll-v2` ER v1-runden pluss de tre målingene som manglet —
# avledet, ikke kopiert, så de tretten arvede grensene aldri kan gli fra
# v1s i stillhet. Revisjonen er SYNLIG (050-formen): v1 består som
# historie for artefaktet fra 2026-08-18; en ny runde måler mot v2.
KRAVGRENSER["wcag-kontroll-v2"] = {
    **KRAVGRENSER["wcag-kontroll-v1"],
    # §1.3: null avvik i BEGGE tellinger — 9/10 er rødt, ikke «nesten».
    # Eksaktheten (attestert == krav, rader == krav) er strukturell og
    # håndheves i `_grenser_wcag_kontroll_v2`, samme form som
    # `kjoringer_rent_innen_frist == kjoringer_krav` i v1.
    "maks_kvittering_attest_avvik": 0,
    "maks_revisjonsrad_avvik": 0,
    # §1.2: byte-likhet i begge ledd (SP-11). Negativ port: én byte
    # endret i ett av leddene → punktet rødt.
    "krev_datasett_sha_lik_innsjekket": True,
    # PUNKTBINDING (#166): hvilke MÅLINGER som kan bevise hvert
    # punkt. Et punkt som ikke står her er UFLIPPBART.
    "punktbinding": {
        "revisjonslogg_korrekt": (
            "maalt.kjoringer_med_attestert_kvittering",
            "maalt.kvittering_attest_avvik",
            "maalt.revisjonsrad_avvik",
            "maalt.revisjonsrader_mot_bestilt",
        ),
        "syntetisk_datasett_likt_lokalt": (
            "maalt.avvik_mot_fasit",
            "oppsett.datasett_sha256",
        ),
    },
}

# --- m02-aksept-arcen (klarsignal 6d1cf8ec…, §3) ----------------------
# De to nye artefaktene er arcens eneste nye målearbeid. Begge er
# MÅLINGS-artefakter (perf-m01-formen): produsert av en kjøring, ikke
# avledet av en fil — så porten re-regner de interne invariantene og
# håndhever grensene, og SP-11-bindingen (sha i manifestet/akseptveien)
# gjør bytene immutable.

#: M-2s andel av suiten — PINNET, ikke bare «navngitt». Delingsbetingelsen
#: krever at punktet navngir hvilken MÅLING som beviser det for nettopp
#: denne modulen, og et fritt `m2_filer` oppfylte bare halve kravet: et
#: artefakt med `m2_filer: ["noen andres tester"]` klarte tallene og
#: passerte porten. Porten under krever nøyaktig DENNE lista, og
#: produsenten (`deploy/staging/m02-suite-artefakt.py`) kjører den samme —
#: ett sted, to lesere, så utvalget ikke kan gli.
#:
#: Utvalget er node-id-er, ikke filer: M-2 deler hver av disse filene med
#: M-1 og plattformlaget, og en hel fil hadde tatt med målinger som ikke
#: sier noe om revisjonsloggen. Den forrige lista tok med HELE
#: `test_kjorer_og_kryptering.py` med begrunnelsen at append-only-portene
#: lå der. Det stemte ikke (Codex P1, runde 2): den filen nevner ikke
#: revisjonsloggen med ett ord — den måler migrasjonskjøreren, DEK-ene og
#: tokenene — mens de tre append-only-portene står i
#: `test_pg_og_attestering.py` og manglet i utvalget helt. Hele suiten
#: kjøres uansett og har sine egne grenser; det er ANDELEN som skal være
#: M-2s egne porter.
M02_SUITE_ANDEL: tuple[str, ...] = (
    # Append-only håndhevet i BASEN, ikke av applikasjonskode.
    "platform/core/tests/test_pg_og_attestering.py::"
    "test_revisjonslogg_er_append_only_i_databasen",
    "platform/core/tests/test_pg_og_attestering.py::"
    "test_runtime_kan_ikke_skru_av_append_only",
    "platform/core/tests/test_pg_og_attestering.py::"
    "test_append_only_triggeren_star_paa_etter_migrasjon",
    # Loggposten selv: evidensfeltene, aktøren fra serverkonteksten,
    # replay uten ny rad, og at et feilet unntaksskriv ruller loggposten.
    "platform/core/tests/test_api.py::"
    "test_tillat_gir_loggpost_med_evidensfelter",
    "platform/core/tests/test_api.py::"
    "test_unntakshistorikk_far_aktor_fra_serverkontekst",
    "platform/core/tests/test_api.py::"
    "test_idempotent_replay_er_byteidentisk_uten_ny_loggpost",
    "platform/core/tests/test_api.py::"
    "test_unntaksskriv_feilet_ruller_ogsa_loggposten",
)

KRAVGRENSER["m02-suite-v1"] = {
    # Suitekjøringen PÅ STAGING: hele suiten grønn, og M-2s ANDEL er
    # pinnet og grønn — delingsbetingelsen i RUTINER.md gjort målbar.
    # Gulvet for helheten er romslig med vilje: det måler at KJØRINGEN var
    # hel, ikke at antallet aldri vokser (det gjør det, hver uke).
    # Gulvene måles mot KJØRTE tester (totalt minus hoppede), og M-2s
    # andel tåler ingen hoppede i det hele tatt: både
    # `test_pg_og_attestering.py` og `test_api.py` er `skipif(not DSN)`,
    # så uten den porten ville en vert uten oppsatt testbase levert en
    # «grønn» andel der ingen av testene hadde kjørt.
    "min_tester": 1500,
    "maks_feilet": 0,
    # Én node-id er én test, så gulvet ER lengden på det pinnede
    # utvalget: alle sju skal ha kjørt, ikke «minst noen av dem».
    "min_m2_tester": len(M02_SUITE_ANDEL),
    "maks_m2_feilet": 0,
    "maks_m2_hoppet": 0,
    "m2_andel_pakrevd": M02_SUITE_ANDEL,
    # PUNKTBINDING (#166): hvilke MÅLINGER som kan bevise hvert
    # punkt. Et punkt som ikke står her er UFLIPPBART.
    "punktbinding": {
        "tester_gronne_pa_staging": (
            "maalt.m2_feilet",
            "maalt.m2_tester",
            "maalt.tester_feilet",
            "maalt.tester_totalt",
        ),
    },
}
KRAVGRENSER["m02-fordeling-v1"] = {
    # Det syntetiske settet er REPRODUSERT, ikke historisk: basen på
    # disponit-srv har ingen rader fra m01-rundens opprinnelige 180
    # (målt 2026-08-21 — null STOPP i hele basen; radene ble aldri med
    # fra gammel staging). Fasiten 84/3/93 er derfor drevet PÅ NYTT
    # gjennom beslutningsveien, med samme fordeling — og CI kjører det
    # SAMME settet mot lokal base (test_m02_fordeling), så «likt
    # lokalt» er en stående måling, ikke et minne.
    "fordeling_eksakt": {"TILLAT": 84, "STOPP": 3, "UNNTAK": 93},
    "min_hendelser": 180,
    # ... og «samme sett» må være MÅLT, ikke oppgitt: driverens bytes
    # hashes i begge ledd og må stemme (samme form som §1.2 for
    # WCAG-datasettet). Uten den kunne staging drevet helt andre
    # hendelser til samme sum og likevel passert.
    "krev_sett_sha_lik_innsjekket": True,
    # PUNKTBINDING (#166): hvilke MÅLINGER som kan bevise hvert
    # punkt. Et punkt som ikke står her er UFLIPPBART.
    "punktbinding": {
        "syntetisk_datasett_likt_lokalt": (
            "maalt.antall_stopp",
            "maalt.antall_tillat",
            "maalt.antall_unntak",
        ),
    },
}

#: M-57-klarsignalet §10, registrert FØR bygging (§0). Hver numeriske
#: invariant måles som et PAR: `<navn>_forsok` (antall ganger bruddet ble
#: FORSØKT konstruert) og `<navn>_brudd` (antall som slapp gjennom).
#: Grensen er null brudd OG minst ett forsøk — en port som aldri kjørte
#: har ikke målt noe (feedback: en fraværstest går grønn på søppel;
#: samme form som `min_robots_5xx_krav` og `min_egress_motormiljo_maalt`).
M57_INVARIANTER: tuple[str, ...] = (
    # Sikkerhetsinvariantene (§10 første liste)
    "utsending_uten_signaturkjede",
    "liste_signert_versjon_endret",
    "serie_to_signerte_versjoner",
    "liste_forelder_i_annen_serie",
    "ttl_persondata_funnet_etter_reaping",
    "blinding_maskert_felt_i_modellinput",
    "utsending_modellgenerert_fritekst",
    "arkiv_utpakking_utenfor_grense",
    # Øvrige (§10 andre liste)
    "oppdrag_frigivelse_id_endret",
    "oppdrag_frigivelse_pa_annen_opprinnelse",
    "serie_forgrenet_historikk",
    "serie_uten_entydig_rot",
    "funn_uten_kildereferanse",
    "blinding_avskrudd_uten_auditrad",
    "bias_maling_mangler_for_digest",
    "ttl_lager_utenfor_kandidatgrensen",
    "bestilling_over_5000_akseptert",
    "kjoring_delvis_resultat_promotert",
    "ui_axe_alvorlige_brudd",
)
KRAVGRENSER["m57-v1"] = {
    # Settet er PINNET her, ikke avledet av artefaktet: et artefakt som
    # utelater en invariant skal felles på fraværet, ikke definere det
    # bort. «Et punkt uten definert, målbar grense regnes som nei» (§10).
    "invarianter": M57_INVARIANTER,
    "maks_brudd": 0,
    "min_forsok": 1,
    # De to ja-punktene: bokstavelig `true`, alt annet er nei.
    "krav_ja": ("ui_tastaturgjennomgang_dokumentert",
                "ddl_begge_kjoringer_gronne"),
    # YTELSEN ER EN MÅLING, IKKE ET JA-PUNKT (Codex P1).
    # `staging_sjekkliste.ytelse_bestatt` peker på `m57-v1`, men grensen
    # bar bare invariantpar og to booleans: et skjemagyldig, grønt
    # artefakt kunne krysse av for ytelse uten at noen hadde kjørt en
    # eneste søknad, og en modul som ikke er levedyktig ville passert
    # aktiveringen. «Et punkt uten definert, målbar grense regnes som
    # nei» (§10) — så punktet får sin grense her, FØR bygging (§0), som
    # resten av `m57-v1`.
    #
    # De to tallene henger sammen og må måles sammen: en varighet uten
    # last er en tom kjøring, og en last uten varighet er en påstand om
    # at det gikk. 5000 er den lovede fulle bunten (§4, samme tak som
    # `antall_soknader`); 240 minutter er utførelsesfristen for `bunt`
    # (§4, samme tall som `UTFORELSESFRIST_VALG`). Drifter de to fra
    # hverandre, sier `test_ytelsesgrensen_er_klarsignalets_tall` ifra.
    "ytelse_min_soknader": 5000,
    "ytelse_maks_minutter": 240,
    # PUNKTBINDING (#166): TOM MED VILJE, og det er det tilsiktede utfallet.
    #
    # Codex felte seks punkter på M-57 der notatet lovet en måling `m57-v1`
    # ikke bærer — suitetotaler, datasett-digester, revisjonsmålinger,
    # køelementer, flippedrillen. Manifestets notater sier dette ærlig i dag
    # (#153s mellomtilstand), men ærlig prosa er ikke en port: uten binding
    # kunne punktene flippes av en hvilken som helst sti som tilfeldigvis
    # fantes i artefaktet.
    #
    # Tom binding gjør dem UFLIPPBARE til målingene finnes. M-57-aksepten
    # skal uansett ikke kjøre før utførelsesarmen har gitt dem, så dette er
    # §10s egen setning gjort mekanisk — ikke en blokkering vi må rundt.
    #
    # Hvert punkt som får sin måling, får sin linje her, pinnet før bygging.
    "punktbinding": {},
}

#: KATALOGAKSENE (A-vedtaket på #152, K2): `status` og `driftstilstand`
#: er katalogens AVLESNING av en aksepthendelse — de er ikke del av den
#: identiteten aksepten binder. En aksept autoriserer flippet av dem;
#: hadde de vært inne i identiteten, ville enhver flippcommit
#: ugyldiggjort sitt eget bevis (fikspunktet målt i #152).
KATALOGAKSER: tuple[str, ...] = ("status", "driftstilstand")


def kanonisk_projeksjon(tekst: str) -> str:
    """sha256 over manifestets KANONISKE PROJEKSJON: parset YAML minus
    katalogaksene, dumpet deterministisk.

    Formen er en ekte parser, aldri en byte-allowlist (K4/SP-13):
    kommentarer og formatering dør i parsingen, så en «ren
    historikkkommentar» trenger intet unntak — og dermed måler
    projeksjonen identitet, ikke formatering. Alt STRUKTURELT
    (`kjerne`, `avhengigheter`, `id`, sjekklistepunktene…) er med, og
    én endring der flytter hashen.
    """
    import json as _json

    import yaml as _yaml
    data = _yaml.safe_load(tekst) or {}
    for akse in KATALOGAKSER:
        data.pop(akse, None)
    kanonisk = _json.dumps(data, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(kanonisk.encode("utf-8")).hexdigest()


#: De skrevne aksepthendelsene, som PROJEKSJONER (A-vedtaket): hver
#: modul med en aksept i basen står her med akseptcommiten og
#: projeksjonen av manifestet SLIK AKSEPTEN MÅLTE DET. Porten i
#: `test_aksept_projeksjon` krever at HEAD-manifestets projeksjon er
#: identisk — katalogaksene og kommentarer kan flippes/skrives, alt
#: annet er en NY identitet som krever ny aksept.
AKSEPTERTE_GENERASJONER: dict[str, dict[str, str]] = {
    "m02_revisjonslogg": {
        "commit": "2aaca01c7187dbf46d06f4e09a3688a79d367739",
        "manifest": "platform/modules/m02_revisjonslogg/manifest.yaml",
        "projeksjon": "700962385382e16713cfb0bfcf918864fd53bad724d86b25651b6ceb7dc41f9a",
    },
    "wcag_audit": {
        "commit": "2aaca01c7187dbf46d06f4e09a3688a79d367739",
        "manifest": "platform/modules/m56_wcag_audit/manifest.yaml",
        "projeksjon": "b2ea178fdab16783a4c626f14013353b51d63ec427cc0e73e9a5d830d5f30142",
    },
}


#: Settdriveren begge ledd deler — bytene som ER settet.
M02_SETT_STI = REPOROT / "deploy/staging/m02_fordeling.py"

#: Hvilket LUKKEDE skjema som gjelder for hvilket krav. Uten dette ville
#: alle artefakter blitt målt mot ytelsesskjemaet, og et feilinjiserings-
#: artefakt ville feilet på «mangler `krav`» i stedet for å bli validert.
ARTEFAKTSKJEMAER: dict[str, str] = {
    "perf-m01-v1": "artefakt-skjema.json",
    "feilinjisering-m01-v1": "artefakt-feilinjisering-skjema.json",
    "rollback-m01-v1": "artefakt-rollback-skjema.json",
    "behandling-m37-v1": "artefakt-behandling-skjema.json",
    "policyadmin-v1": "artefakt-policyadmin-skjema.json",
    "wcag-kontroll-v1": "artefakt-wcag-kontroll-skjema.json",
    "wcag-kontroll-v2": "artefakt-wcag-kontroll-v2-skjema.json",
    "m02-suite-v1": "artefakt-m02-suite-skjema.json",
    "m02-fordeling-v1": "artefakt-m02-fordeling-skjema.json",
    "rollback-m56-v1": "artefakt-rollback-m56-skjema.json",
    "m57-v1": "artefakt-m57-skjema.json",
}


def _skjema() -> dict:
    return json.loads(SKJEMA_STI.read_text(encoding="utf-8"))


def valider_manifest(manifest: object) -> list[str]:
    """Tom liste == gyldig."""
    try:
        import jsonschema
        validator = jsonschema.Draft202012Validator(_skjema())
        return sorted(
            f"{'/'.join(str(p) for p in e.absolute_path) or '<rot>'}: {e.message}"
            for e in validator.iter_errors(manifest))
    except Exception as e:  # siste skanse — aldri ukontrollert exception
        return [f"intern valideringsfeil ({type(e).__name__}): {e}"]


def valider_alle(modulrot: Path) -> dict[str, list[str]]:
    """-> {modul-id: feilliste}. Alle nøkler med tom liste == alt gyldig."""
    import yaml
    ut: dict[str, list[str]] = {}
    for fil in sorted(Path(modulrot).glob("*/manifest.yaml")):
        data = yaml.safe_load(fil.read_text(encoding="utf-8"))
        ut[fil.parent.name] = valider_manifest(data)
    return ut


def _les_artefakt(sti: Path) -> tuple[dict | None, str | None, str]:
    """-> (innhold, sha256, feilmelding). Åpner og hasher i ETT lesesteg.

    Leses filen to ganger — én gang for hashen og én for innholdet — er det
    i prinsippet to forskjellige filer som valideres. Her hashes nøyaktig de
    bytene som deretter tolkes.
    """
    try:
        raa = sti.read_bytes()
    except OSError as e:
        return None, None, f"artefaktet kan ikke åpnes: {type(e).__name__}"
    sha = hashlib.sha256(raa).hexdigest()
    try:
        data = json.loads(raa.decode("utf-8"))
    except Exception as e:
        return None, sha, f"artefaktet er ikke gyldig JSON ({type(e).__name__})"
    if not isinstance(data, dict):
        return None, sha, "artefaktet er ikke et JSON-objekt"
    return data, sha, ""


def _tall(kilde: object, navn: str, felt: str) -> tuple[float | None, str]:
    """Fail-closed avlesning av en numerisk måling. -> (verdi, feilmelding).

    To feller dette finnes for, begge fant Codex i PR #8:

    * `bool` er en SUBKLASSE av `int` i Python. En `isinstance(x, int)`-test
      slipper derfor `feil: false` gjennom og leser den som 0 — altså
      «ingen feil» fordi feltet var en boolsk verdi, ikke et tall.
    * NaN gjør enhver sammenligning False. `p95: NaN` ville passert
      `p95 >= 150` og dermed ethvert tak vi setter. Et tak man ikke kan
      bryte er ikke et tak.
    """
    if not isinstance(kilde, dict):
        return None, f"{navn}: mangler (ingen `{felt}`-blokk)"
    verdi = kilde.get(felt)
    if isinstance(verdi, bool) or not isinstance(verdi, (int, float)):
        return None, f"{navn}={verdi!r} er ikke et tall"
    tall = float(verdi)
    if tall != tall or tall in (float("inf"), float("-inf")):
        return None, f"{navn}={verdi!r} er ikke et endelig tall"
    return tall, ""


def _teller(kilde: object, navn: str, felt: str) -> tuple[int | None, str]:
    """En TELLING: heltall >= 0. -> (verdi, feilmelding).

    Codex' P1 nr. 4 på PR #8: `_tall()` håndhevet at verdien var et endelig
    tall, men ikke hva tallet BETYR. Fire umulige artefakter passerte:
    negativt antall feil (−5 er «<= 0» og besto taket), negative
    beslutningstellinger som matematisk utlignet hverandre til riktig sum,
    og brøkdeler av forespørsler.

    Ingen av dem kan oppstå i en ekte kjøring. En validator som godtar dem
    validerer aritmetikk, ikke virkelighet.

    Flyttall avvises helt, også `6000.0`: produsenten teller med `len()` og
    heltallsaddisjon, så en float i et telle-felt betyr at noe har regnet
    der det skulle telt.
    """
    if not isinstance(kilde, dict):
        return None, f"{navn}: mangler (ingen `{felt}`-blokk)"
    verdi = kilde.get(felt)
    if isinstance(verdi, bool) or not isinstance(verdi, int):
        return None, (f"{navn}={verdi!r} er ikke en heltallstelling"
                      f" ({type(verdi).__name__})")
    if verdi < 0:
        return None, f"{navn}={verdi} er negativ — en telling kan ikke være det"
    return verdi, ""


def _unike(verdier) -> set[str]:
    """Avduplikering som IKKE kan kaste på uhashbare JSON-verdier.

    Codex' P2 på PR #117: `valider_artefakter` går BEVISST videre inn i
    domenesjekkene etter en skjemafeil — formatet skal ikke maskere
    måletallene. Derfor kan listene her inneholde `{}` eller `[]` selv om
    skjemaet har rapportert dem som formatfeil, og `set(liste)` kastet da
    `TypeError`. Valideringen slapp ut med en traceback i stedet for de
    akkumulerte feilene, altså ÅPENT, mens hele denne veien skal feile
    lukket.

    En kanonisk JSON-tekst per oppføring kan ikke kaste, og skiller
    verdier like presist som `set` gjorde for de hashbare: `1`, `"1"` og
    `true` får hver sin tekst.
    """
    return {json.dumps(v, sort_keys=True, ensure_ascii=False)
            for v in verdier}


def _positiv(kilde: object, navn: str, felt: str) -> tuple[float | None, str]:
    """En STØRRELSE som må være strengt positiv: tid, rate, svartid.

    Null er ikke en gyldig måling her. En kjøring som varte 0 sekunder har
    ikke skjedd, og en rate på 0 er ingen last. Kravet er skilt fra
    `_teller` fordi disse ER kontinuerlige — 60,03 sekunder er riktig.
    """
    tall, feil = _tall(kilde, navn, felt)
    if feil:
        return None, feil
    if tall <= 0:
        return None, f"{navn}={tall:g} må være > 0"
    return tall, ""


def _andel(kilde: object, navn: str, felt: str) -> tuple[float | None, str]:
    """En ANDEL: endelig tall i [0, 1]. -> (verdi, feilmelding).

    Egen leser fordi `_tall` ville sluppet gjennom 1.5 og −0.2. En andel på
    1.5 er ikke et høyt tall — det er en umulig måling, på samme måte som
    en negativ telling er det. Samme lærdom som ga `_teller` og `_positiv`
    i PR #8 runde 4: spør hva tallet ER, ikke bare hvor stort det er.
    """
    tall, feil = _tall(kilde, navn, felt)
    if feil:
        return None, feil
    if not (0.0 <= tall <= 1.0):
        return None, f"{navn}={tall:g} er ikke en andel i [0, 1]"
    return tall, ""


def valider_artefaktformat(art: object,
                           krav_id: str | None = None) -> list[str]:
    """Artefaktet mot det LUKKEDE skjemaet. Tom liste == gyldig.

    Codex' P1 nr. 5 på PR #8: `_sjekk_grenser` leste feltene den kjente til
    og var blind for alt annet. Tre artefakter passerte derfor:
    `sikkerhet: 500` blant kø-tellingene, `UKJENT: 500` blant
    beslutningsutfallene, og `feiltyper: false` i stedet for en liste (falsy
    ⇒ «ingen feiltyper»).

    `additionalProperties: false` snur standarden: en ukjent nøkkel er en
    FEIL, ikke stillhet. Utvider noen artefaktformatet, må de utvide porten
    i samme slengen — det er hele poenget.

    Og et krav_id UTEN registrert skjema er en FEIL, ikke en stille
    tilbakefallsvalidering (Codex P2, #121). Oppslaget falt før tilbake
    til det generiske ytelsesskjemaet, og da gjelder svaret et helt annet
    format: enhver kaller som skriver feil krav_id — f.eks. akseptens
    `m56-akseptflipp-v2` der artefaktets `wcag-kontroll-v1` skulle stått
    — får en full feilliste uansett hva artefaktet inneholder, og en
    `assert valider_artefaktformat(...)` er grønn av samme grunn som den
    ville vært grønn ved et ekte funn. Standardskjemaet gjelder BARE når
    ingen krav_id er oppgitt (`perf-m01-v1`s eget format).
    """
    try:
        import jsonschema
        if krav_id is not None and krav_id not in ARTEFAKTSKJEMAER:
            return [f"<rot>: ukjent krav_id {krav_id!r} — ingen registrert"
                    " artefaktskjema; svaret ville gjeldt et annet format"]
        filnavn = ARTEFAKTSKJEMAER.get(krav_id or "")
        sti = (ARTEFAKTSKJEMA_STI.parent / filnavn) if filnavn \
            else ARTEFAKTSKJEMA_STI
        skjema = json.loads(sti.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(skjema)
        return sorted(
            f"{'/'.join(str(p) for p in e.absolute_path) or '<rot>'}: {e.message}"
            for e in validator.iter_errors(art))
    except Exception as e:
        return [f"intern valideringsfeil ({type(e).__name__}): {e}"]


def _sjekk_grenser(krav_id: str, art: dict) -> list[str]:
    """Håndhever KRAVGRENSER og artefaktets INTERNE invarianter.

    Dette er selve poenget med porten: `bestatt: true` inne i artefaktet er
    produsentens EGEN påstand, og det samme gjelder `en_til_en` og
    `routing_stemmer`. Codex' P1 nr. 3 på PR #8 viste hvorfor det ikke
    holder å lese sammendragsboolene: et artefakt med
    `unntaksrader_per_sakstype.normal = 0`, `forventede_normalsaker = 9999`
    og `routing_stemmer: true` passerte porten. Tallene motsa flagget, og
    bare flagget ble lest.

    Derfor REGNES invariantene ut på nytt her, og lastprofilen håndheves:
    6 000 forespørsler sier ingenting om de ble sendt på ett minutt eller
    på to timer, og en kjøring med rate 1/s og samtidighet 1 er ikke den
    porten kravet beskriver.
    """
    grense = KRAVGRENSER.get(krav_id)
    if grense is None:
        return [f"ukjent krav_id {krav_id!r} — ingen grenser å håndheve"]
    feil: list[str] = []
    if art.get("krav_id") != krav_id:
        feil.append(f"artefaktet gjelder {art.get('krav_id')!r}, "
                    f"manifestet påstår {krav_id!r}")
    if art.get("bestatt") is not True:
        feil.append("artefaktet sier ikke bestatt: true")

    # Hvert krav har sine egne domenegrenser. Felleskontrollene over
    # (krav_id stemmer, bestatt er true) gjelder alle; alt under er
    # kravspesifikt, og en felles «les tallene»-rutine ville uansett måttet
    # kjenne hvert felt for å kunne si noe om hva det BETYR.
    if krav_id == "feilinjisering-m01-v1":
        return feil + _grenser_feilinjisering(grense, art)
    if krav_id == "rollback-m01-v1":
        return feil + _grenser_rollback(grense, art)
    if krav_id == "behandling-m37-v1":
        return feil + _grenser_behandling(grense, art)
    if krav_id == "policyadmin-v1":
        return feil + _grenser_policyadmin(grense, art)
    if krav_id == "wcag-kontroll-v1":
        return feil + _grenser_wcag_kontroll(grense, art)
    if krav_id == "wcag-kontroll-v2":
        return feil + _grenser_wcag_kontroll_v2(grense, art)
    if krav_id == "m02-suite-v1":
        return feil + _grenser_m02_suite(grense, art)
    if krav_id == "m02-fordeling-v1":
        return feil + _grenser_m02_fordeling(grense, art)
    if krav_id == "rollback-m56-v1":
        return feil + _grenser_rollback_m56(grense, art)
    if krav_id == "m57-v1":
        return feil + _grenser_m57(grense, art)

    m = art.get("maalt")
    if not isinstance(m, dict):
        return feil + ["artefaktet mangler `maalt`"]
    oppsett = art.get("oppsett")
    if not isinstance(oppsett, dict):
        return feil + ["artefaktet mangler `oppsett` — lastprofilen er ukjent"]

    # --- Volum, feil og svartid -------------------------------------------
    # Alt som TELLES leses med `_teller`: heltall >= 0. Alt som MÅLES med
    # `_positiv`: endelig og > 0.
    antall, m_feil = _teller(m, "antall", "antall")
    if m_feil:
        feil.append(m_feil)
    elif antall < grense["min_antall"]:
        feil.append(f"antall={antall}, krever >= {grense['min_antall']}")

    # Konfigurert antall må stemme med målt antall. Feltet lå i artefaktet
    # og ble aldri lest: et oppsett som ba om 6 000 og en måling som
    # rapporterte noe annet, er to ulike kjøringer i samme fil.
    oppsett_antall, m_feil = _teller(oppsett, "oppsett.antall", "antall")
    if m_feil:
        feil.append(m_feil)
    elif antall is not None and oppsett_antall != antall:
        feil.append(f"oppsett.antall={oppsett_antall} != maalt.antall={antall}")

    for felt, tak in (("feil", grense["maks_feil"]),
                      ("rate_begrenset", grense["maks_rate_begrenset"])):
        verdi, m_feil = _teller(m, felt, felt)
        if m_feil:
            feil.append(m_feil)
        elif verdi > tak:
            feil.append(f"{felt}={verdi}, krever <= {tak}")
    # `feiltyper` MÅ være en liste. `false` er falsy og ble lest som «ingen
    # feiltyper» — skjemaet tar det nå, men kontrollen står også her, fordi
    # denne funksjonen kalles direkte fra testene.
    feiltyper = m.get("feiltyper")
    if not isinstance(feiltyper, list):
        feil.append(f"feiltyper={feiltyper!r} er ikke en liste")
    elif feiltyper:
        feil.append(f"artefaktet har feiltyper: {feiltyper}")

    p95, m_feil = _positiv(m.get("svartid_ms"), "p95", "p95")
    if m_feil:
        feil.append(m_feil)
    elif p95 >= grense["maks_p95_ms"]:
        feil.append(f"p95={p95} ms, krever < {grense['maks_p95_ms']} ms")

    # --- Lastprofilen: rate, samtidighet, varighet ------------------------
    krevd_rate = grense["min_rate_per_sek"]
    nedre_rate = krevd_rate * (1.0 - grense["rate_slingringsmonn"])
    for leser, kilde, navn, felt, nedre in (
            (_positiv, oppsett, "oppsett.rate_per_sek", "rate_per_sek",
             krevd_rate),
            (_positiv, m, "maalt.oppnadd_rate", "oppnadd_rate", nedre_rate),
            (_teller, oppsett, "oppsett.samtidige", "samtidige",
             grense["min_samtidige"])):
        verdi, m_feil = leser(kilde, navn, felt)
        if m_feil:
            feil.append(m_feil)
        elif verdi < nedre:
            feil.append(f"{navn}={verdi:g}, krever >= {nedre:g}")

    # `_positiv` avviser 0 og negativ FØR sammenligningen under. Tidligere
    # sto det `elif antall is not None and varighet > 0:` — altså hoppet en
    # varighet på 0 over sin egen kontroll. En vakt som lar ugyldige verdier
    # slippe forbi kontrollen sin, er fail-open.
    varighet, m_feil = _positiv(m, "maalt.varighet_sek", "varighet_sek")
    if m_feil:
        feil.append(m_feil)
    elif antall is not None:
        # Varigheten MÅ stemme med antall/rate. Uten denne kunne et
        # artefakt oppgi 6 000 forespørsler, rate 100 og varighet 3 600 s:
        # hvert enkelt tall består sin egen grense, mens kjøringen i
        # virkeligheten gikk på 1,7/s.
        forventet = antall / krevd_rate
        avvik = abs(varighet - forventet) / forventet
        if avvik > grense["varighet_slingringsmonn"]:
            feil.append(
                f"varighet_sek={varighet:g} passer ikke med antall={antall:.0f}"
                f" @ {krevd_rate:g}/s (forventet ~{forventet:g} s,"
                f" avvik {avvik * 100:.0f} %)")

    # --- Interne invarianter: tallene mot hverandre, ikke mot flagg -------
    b = m.get("beslutninger")
    unntak_besluttet = None
    if not isinstance(b, dict) or not b:
        feil.append("artefaktet mangler `beslutninger`")
    else:
        # `_teller` er avgjørende her: med `_tall` kunne TILLAT=6000,
        # STOPP=-1200 og UNNTAK=1200 summere seg til 6 000 og bestå. En
        # negativ beslutningstelling finnes ikke, men aritmetikken bryr seg
        # ikke — den utligner bare.
        sum_b = 0
        gyldig = True
        for utfall in ("TILLAT", "STOPP", "UNNTAK"):
            verdi, m_feil = _teller(b, f"beslutninger.{utfall}", utfall)
            if m_feil:
                feil.append(m_feil)
                gyldig = False
            else:
                sum_b += verdi
                if utfall == "UNNTAK":
                    unntak_besluttet = verdi
        if gyldig and antall is not None and sum_b != antall:
            feil.append(f"summen av beslutningene ({sum_b}) er ikke lik"
                        f" antall ({antall})")

    k = art.get("etterkontroll")
    if not isinstance(k, dict):
        feil.append("artefaktet mangler `etterkontroll`")
        return feil

    svar, f1 = _teller(k, "auditerte_svar", "auditerte_svar")
    rader, f2 = _teller(k, "revisjonsrader", "revisjonsrader")
    for m_feil in (f1, f2):
        if m_feil:
            feil.append(m_feil)
    if not f1 and not f2 and antall is not None:
        if not (svar == rader == antall):
            feil.append(f"auditerte_svar={svar}, revisjonsrader={rader},"
                        f" antall={antall} — alle tre må være like")
    if k.get("en_til_en") is not True:
        feil.append("etterkontroll: en_til_en er ikke true")

    # Routing: sammenlign TALLENE. Flagget leses i tillegg, aldri i stedet.
    per_sakstype = k.get("unntaksrader_per_sakstype")
    if not isinstance(per_sakstype, dict):
        feil.append("etterkontroll mangler `unntaksrader_per_sakstype`")
    else:
        normale, f3 = _teller(per_sakstype, "unntaksrader normal", "normal")
        forventet, f4 = _teller(k, "forventede_normalsaker",
                                "forventede_normalsaker")
        for m_feil in (f3, f4):
            if m_feil:
                feil.append(m_feil)
        if not f3 and not f4:
            if normale != forventet:
                feil.append(f"normal-kørader ({normale}) != forventede"
                            f" normalsaker ({forventet})")
            if unntak_besluttet is not None and normale != unntak_besluttet:
                feil.append(f"normal-kørader ({normale}) != antall"
                            f" UNNTAK-beslutninger ({unntak_besluttet})")
        # ALLE sakstyper telles med, ikke bare `normal`. `sikkerhet` og
        # `drift` er lovlige sakstyper — men den syntetiske miksen i
        # perf-m01-v1 produserer bare normalsaker, så en rad i en annen kø
        # betyr at kjøringen gjorde noe annet enn den rapporterer.
        # Skjemaet stopper en UKJENT sakstype; dette stopper en kjent
        # sakstype med et uventet antall.
        sum_koer = 0
        alle_gyldige = True
        for sakstype in sorted(per_sakstype):
            verdi, m_feil = _teller(per_sakstype, f"unntaksrader {sakstype}",
                                    sakstype)
            if m_feil:
                if sakstype != "normal":       # normal er alt rapportert over
                    feil.append(m_feil)
                alle_gyldige = False
            else:
                sum_koer += verdi
        if alle_gyldige and unntak_besluttet is not None \
                and sum_koer != unntak_besluttet:
            feil.append(f"summen av alle kø-rader ({sum_koer}) != antall"
                        f" UNNTAK-beslutninger ({unntak_besluttet})"
                        f" — fordeling: {dict(sorted(per_sakstype.items()))}")
    if k.get("routing_stemmer") is not True:
        feil.append("etterkontroll: routing_stemmer er ikke true")
    return feil


def _grenser_wcag_kontroll(grense: dict, art: dict) -> list[str]:
    """`wcag-kontroll-v1` — stagingsjekklisterunden for m56, 014c §12.

    Tallene er runde-sammendraget avledet mekanisk av evidens.jsonl
    (deploy/staging/wcag-kontroll-artefakt.py). Forholdene regnes ut på
    nytt her: `bestatt: true` er produsentens påstand, ikke beviset —
    samme regel som alle de andre kravene.
    """
    feil: list[str] = []
    m = art.get("maalt")
    if not isinstance(m, dict):
        return ["artefaktet mangler `maalt`"]
    # FRISTEN og SIGNATUREN er to målinger, ikke ett navn (Codex P2, #117
    # runde 15). Feltet het `kjoringer_signert_innen_frist` og bar bare
    # fristmålingen; her måles derfor det nye navnet, og signaturtallet
    # står ved siden av — under samme «ikke over kravet»-regel, siden en
    # signatur uten en kjøring bak seg er like umulig som en ellevte
    # kjøring av ti.
    kjort, m1 = _teller(m, "kjoringer_rent_innen_frist",
                        "kjoringer_rent_innen_frist")
    krav, m2 = _teller(m, "kjoringer_krav", "kjoringer_krav")
    signert, m3 = _teller(m, "kjoringer_med_maalt_signatur",
                          "kjoringer_med_maalt_signatur")
    for melding in (m1, m2, m3):
        if melding:
            feil.append(melding)
    if not m1 and not m2:
        if krav < grense["min_kjoringer"]:
            feil.append(f"kjoringer_krav={krav}, krever >="
                        f" {grense['min_kjoringer']}")
        # Nøyaktig kravet: under er runden ufullført, OVER er tallet
        # internt umulig — flere rene kjøringer enn det ble kjørt er
        # ikke et strengere bevis, det er et bevis som ikke stemmer med
        # seg selv (og akseptraden ville båret «11/10»).
        if kjort != krav:
            feil.append(f"kjoringer_rent_innen_frist={kjort} av {krav}"
                        " — alle skal være utført innen frist, og flere"
                        " enn de kjørte finnes ikke")
    if not m2 and not m3 and signert > krav:
        feil.append(f"kjoringer_med_maalt_signatur={signert} av {krav}"
                    " — flere målte signaturer enn kjøringer finnes ikke")
    for felt, tak in (
            ("avvik_mot_fasit", grense["maks_avvik_mot_fasit"]),
            ("robots_private_forisporsler", grense["maks_robots_private"]),
            ("egress_lekkasjer", grense["maks_egress_lekkasjer"]),
            ("feilinjisering_promoterte_artefakter",
             grense["maks_promoterte_ved_feil"])):
        verdi, melding = _teller(m, felt, felt)
        if melding:
            feil.append(melding)
        elif verdi > tak:
            feil.append(f"{felt}={verdi}, krever <= {tak}")
    # Egress-proben: «0 lekkasjer» er bare et bevis hvis proben KJØRTE
    # (Codex P1, #121). Produsenten måler det selv — `ok` er returkode 0
    # OG ingen lekkasjer — og sammendraget bærer det nå. Uten denne
    # kontrollen ville en probe som falt over på returkode ≠ 0 båret
    # `egress_lekkasjer=0` rett inn i en immutabel akseptrad.
    maalt_probe, melding = _teller(m, "egress_motormiljo_maalt",
                                   "egress_motormiljo_maalt")
    if melding:
        feil.append(melding)
    elif maalt_probe < grense["min_egress_motormiljo_maalt"]:
        feil.append("egress_motormiljo_maalt=0 — port24-proben kom aldri"
                    " i gang eller gikk ikke rent; «0 lekkasjer» er da"
                    " fravær av en måling, ikke en bestått port")
    # 5xx-siden: en 500-side som «ren» var nettopp dogfooding-fellen —
    # antall kontrollerte sider må VÆRE kravet, ikke bare over null.
    sider, m1 = _teller(m, "robots_5xx_sider_kontrollert",
                        "robots_5xx_sider_kontrollert")
    sider_krav, m2 = _teller(m, "robots_5xx_krav", "robots_5xx_krav")
    for melding in (m1, m2):
        if melding:
            feil.append(melding)
    if not m1 and not m2:
        if sider_krav < grense["min_robots_5xx_krav"]:
            feil.append(f"robots_5xx_krav={sider_krav} — 5xx-siden ble"
                        " aldri prøvd; likheten 0 == 0 er fravær av en"
                        " kontroll, ikke en bestått kontroll")
        elif sider != sider_krav:
            feil.append(f"robots_5xx: kontrollert {sider}, krav {sider_krav}")
    # Frekvensporten: nøyaktig grensen tillates, ingenting over utføres.
    tillat, m1 = _teller(m, "frekvens_tillat", "frekvens_tillat")
    avvist, m2 = _teller(m, "frekvens_avvist_over_grense",
                         "frekvens_avvist_over_grense")
    for melding in (m1, m2):
        if melding:
            feil.append(melding)
    if not m1 and not m2:
        if avvist < grense["min_frekvens_avvist"]:
            feil.append(f"frekvens_avvist_over_grense={avvist} — porten"
                        " målte aldri at taket faktisk avviser")
        if tillat != grense["frekvens_tillat_eksakt"]:
            feil.append(f"frekvens_tillat={tillat}, krever nøyaktig"
                        f" {grense['frekvens_tillat_eksakt']} — under er"
                        " taket umålt, over er taket BRUTT (forespørselen"
                        " ble utført, uansett hva den neste fikk)")
    # Feilveien: en feilet kjøring har kvittering og INGEN evidens.
    for felt, minst in (
            ("feilinjisering_feilet_med_kvittering",
             grense["min_feilet_med_kvittering"]),
            ("evidensfrist_reapet", grense["min_evidensfrist_reapet"]),
            ("evidensfrist_sak_opprettet", grense["min_evidensfrist_sak"])):
        verdi, melding = _teller(m, felt, felt)
        if melding:
            feil.append(melding)
        elif verdi < minst:
            feil.append(f"{felt}={verdi}, krever >= {minst}")
    return feil


#: Datasettet punktet `syntetisk_datasett_likt_lokalt` binder: fasiten og
#: sidene den beskriver. `server.py` er SERVERINGEN, ikke datasettet — den
#: står utenfor identiteten med vilje, akkurat som blokkert-noten sier
#: («sider/ + fasit.json»).
DATASETT_STI = REPOROT / "platform/modules/m56_wcag_audit/testnettsted"


def datasett_sha256(rot: Path) -> str:
    """Kanonisk identitet for det syntetiske datasettet — BYTENE.

    Én funksjon, to lesere (aksept-arc-klarsignalet §1.2): sjekklisten
    kaller den på staging og skriver tallet i evidensstrømmen;
    `_grenser_wcag_kontroll_v2` kaller den på de innsjekkede bytene i CI
    og krever likhet. Punktet er `ja` kun ved byte-likhet BEGGE steder
    (SP-11) — og fordi begge ledd bruker nøyaktig denne funksjonen, kan
    ikke to kanoniseringer gli fra hverandre og gjøre likheten vakuøs.

    Formen: for hver fil, i sortert POSIX-stirekkefølge (fasit.json
    først, så sider/ rekursivt), hashes den relative stien og filens
    egen sha256 — sti OG innhold, så en fil som flyttes eller byttes med
    en annens bytes endrer identiteten. Ingen mtime, ingen eier, ingen
    tilfeldighet: samme bytes gir samme tall på enhver maskin.
    """
    h = hashlib.sha256()
    filer = [rot / "fasit.json"] + sorted(
        p for p in (rot / "sider").rglob("*") if p.is_file())
    for p in filer:
        rel = p.relative_to(rot).as_posix()
        h.update(rel.encode("utf-8") + b"\x00")
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def _grenser_wcag_kontroll_v2(grense: dict, art: dict) -> list[str]:
    """`wcag-kontroll-v2` — v1-runden + de tre målingene som manglet.

    Aksept-arc-klarsignalet §1: (1.2) datasettets sha256 målt på staging
    OG mot innsjekkede bytes, (1.3) kvittering-attest og revisjonsrad
    per kjøring med null avvik — 9/10 er rødt, ikke «nesten». De
    tretten v1-grensene håndheves uendret av `_grenser_wcag_kontroll`.
    """
    feil = _grenser_wcag_kontroll(grense, art)
    m = art.get("maalt")
    if not isinstance(m, dict):
        return feil
    krav, mk = _teller(m, "kjoringer_krav", "kjoringer_krav")
    # §1.3a: KVITTERINGEN — attestert er en EGEN måling
    # (`maal_kjoringsattest`, 052: avtrykk + claim-spor + artefakt-
    # likhet + regelsett), og tallet må være NØYAKTIG kravet: under er
    # runden ikke bevist, over er tallet internt umulig.
    attestert, m1 = _teller(m, "kjoringer_med_attestert_kvittering",
                            "kjoringer_med_attestert_kvittering")
    if m1:
        feil.append(m1)
    elif not mk and attestert != krav:
        feil.append(f"kjoringer_med_attestert_kvittering={attestert} av"
                    f" {krav} — alle de bestilte kjøringene skal bære en"
                    " attestert kvittering, og 9/10 er rødt, ikke"
                    " «nesten» (aksept-arc-klarsignalet §1.3)")
    avvik, m2 = _teller(m, "kvittering_attest_avvik",
                        "kvittering_attest_avvik")
    if m2:
        feil.append(m2)
    elif avvik > grense["maks_kvittering_attest_avvik"]:
        feil.append(f"kvittering_attest_avvik={avvik}, krever <="
                    f" {grense['maks_kvittering_attest_avvik']}")
    # §1.3b: REVISJONSRADEN — én DISTINKT rad per bestilt kjøring.
    rader, m3 = _teller(m, "revisjonsrader_mot_bestilt",
                        "revisjonsrader_mot_bestilt")
    if m3:
        feil.append(m3)
    elif not mk and rader != krav:
        feil.append(f"revisjonsrader_mot_bestilt={rader} av {krav} — ti"
                    " bestilte kjøringer skal bære ti distinkte"
                    " revisjonsrader, og 9/10 er rødt")
    ravvik, m4 = _teller(m, "revisjonsrad_avvik", "revisjonsrad_avvik")
    if m4:
        feil.append(m4)
    elif ravvik > grense["maks_revisjonsrad_avvik"]:
        feil.append(f"revisjonsrad_avvik={ravvik}, krever <="
                    f" {grense['maks_revisjonsrad_avvik']}")
    # #124: identitetene FØLGER tallene — tredjelaget, samme form som
    # drillens `_identiteter_stemmer`. Akseptporten re-måler nøyaktig
    # disse kjøringene i basen, så et sett som ikke stemmer med tallet
    # det står ved siden av, er to påstander i ett artefakt.
    ident = art.get("identiteter")
    kjoringer = ident.get("kjoringer") if isinstance(ident, dict) else None
    if not isinstance(kjoringer, list):
        feil.append("identiteter.kjoringer mangler — akseptporten kan"
                    " ikke re-måle kjøringer den ikke får navngitt")
    else:
        gyldige = [o for o in kjoringer
                   if isinstance(o, int) and not isinstance(o, bool)
                   and o > 0]
        if len(gyldige) != len(kjoringer):
            feil.append("identiteter.kjoringer bærer verdier som ikke er"
                        " oppdrags-IDer")
        if not mk and len(kjoringer) != krav:
            feil.append(f"identiteter.kjoringer har {len(kjoringer)} av"
                        f" {krav} — hver bestilte kjøring skal være"
                        " navngitt")
        if len(set(gyldige)) != len(gyldige):
            feil.append("identiteter.kjoringer gjentar et oppdrag — ett"
                        " oppdrag er én kjøring, aldri to")
    # §1.2: datasettets identitet — begge ledd, byte-likhet. Leddet her
    # er det LOKALE: de innsjekkede bytene hashes med nøyaktig samme
    # funksjon produsenten brukte på staging.
    oppsett = art.get("oppsett")
    sha = oppsett.get("datasett_sha256") if isinstance(oppsett, dict) \
        else None
    if not (isinstance(sha, str) and len(sha) == 64):
        feil.append("oppsett.datasett_sha256 mangler — staging-leddet av"
                    " datasettmålingen er umålt (§1.2)")
    elif grense.get("krev_datasett_sha_lik_innsjekket"):
        try:
            lokal = datasett_sha256(DATASETT_STI)
        except OSError as e:
            feil.append(f"datasettet lot seg ikke hashe lokalt: {e}")
        else:
            if sha != lokal:
                feil.append(
                    f"datasett_sha256={sha[:12]}… er ikke de innsjekkede"
                    f" bytenes {lokal[:12]}… — datasettet staging"
                    " serverte er ikke datasettet fasiten beskriver;"
                    " byte-likhet i BEGGE ledd er kravet (SP-11, §1.2)")
    return feil


#: PRODUSENTFLATEN bevisroten dekker — fastlåst liste, aldri glob:
#: en glob ville latt en NY fil utvide flaten uten at porten så det.
M02_BEVISROT_FILER = (
    "deploy/staging/m02_fordeling.py",
    "deploy/staging/m02-fordeling-artefakt.py",
    "deploy/staging/m02-suite-artefakt.py",
    "policies/bransjemal-tjenestebedrift.yaml",
)


def m02_bevisrot_sha256() -> str:
    """ÉN digest over hele m02-produsentflaten — tillitsgrensens anker.

    K2-beslutningen i #131 (spec: issue #132): artefaktet beviser
    KJØRINGEN — settet, byggerne og policyen slik de er sjekket inn —
    og DENNE digesten er den påstanden som bytes. Porten regner den ut
    over sitt eget tre og krever likhet med artefaktets; commit-feltet
    blir dermed informativt, byte-bindingen er porten. Samme kanoniske
    form som `datasett_sha256`: sti + innholds-sha per fil, i fastlåst
    rekkefølge.

    DYPERE BINDING ER EKSPLISITT UTENFOR GRENSEN: API-runtime på verten,
    credential-snapshots og importerte biblioteker bindes av
    deploymentkjeden (release → digest → CI-attest) — det er
    akseptmaskineriets jobb, ikke denne digestens. Hvert hakk under
    denne flaten peker på et nytt hakk (#131 runde 3 målte kjeden), og
    svaret er en besluttet grense, ikke en dypere hash. Neste review
    som åpner sporet: les #131-K2-beslutningen først.
    """
    h = hashlib.sha256()
    for rel in M02_BEVISROT_FILER:
        p = REPOROT / rel
        h.update(rel.encode("utf-8") + b"\x00")
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def _m02_bevisrot_feil(art: dict) -> list[str]:
    """Felles bevisrot-ledd for begge m02-artefaktene."""
    oppsett = art.get("oppsett")
    sha = oppsett.get("bevisrot_sha256") if isinstance(oppsett, dict) \
        else None
    if not (isinstance(sha, str) and len(sha) == 64):
        return ["oppsett.bevisrot_sha256 mangler — produsentflaten er"
                " ubundet, og artefaktet beviser da en kjøring av"
                " ukjente bytes"]
    try:
        lokal = m02_bevisrot_sha256()
    except OSError as e:
        return [f"bevisroten lot seg ikke hashe lokalt: {e}"]
    if sha != lokal:
        return [f"bevisrot_sha256={sha[:12]}… er ikke de innsjekkede"
                f" bytenes {lokal[:12]}… — kjøringen brukte en annen"
                " produsentflate enn treet porten står i"]
    return []


def _grenser_m02_suite(grense: dict, art: dict) -> list[str]:
    """`m02-suite-v1` — suitekjøringen på staging, med M-2s andel
    navngitt (m02-aksept-klarsignalet §3). Tallene re-regnes: andelen
    kan ikke overstige helheten, en feilfri suite har null feilede, og
    M-2s filer må faktisk stå i artefaktet."""
    feil: list[str] = []
    m = art.get("maalt")
    if not isinstance(m, dict):
        return ["artefaktet mangler `maalt`"]
    total, m1 = _teller(m, "tester_totalt", "tester_totalt")
    feilet, m2 = _teller(m, "tester_feilet", "tester_feilet")
    m2t, m3 = _teller(m, "m2_tester", "m2_tester")
    m2f, m4 = _teller(m, "m2_feilet", "m2_feilet")
    kode, m5 = _teller(m, "suite_exitkode", "suite_exitkode")
    m2k, m6 = _teller(m, "m2_exitkode", "m2_exitkode")
    hoppet, m7 = _teller(m, "tester_hoppet", "tester_hoppet")
    m2h, m8 = _teller(m, "m2_hoppet", "m2_hoppet")
    for melding in (m1, m2, m3, m4, m5, m6, m7, m8):
        if melding:
            feil.append(melding)
    if any((m1, m2, m3, m4, m5, m6, m7, m8)):
        return feil
    # Gulvene måles mot KJØRTE tester, ikke mot `tests`: junit teller en
    # hoppet test i `tests` og rapporterer null failures og null errors
    # for den, så en suite som er hoppet over ser ut som en suite som
    # gikk. En hoppet test er ikke en bestått test.
    if total - hoppet < grense["min_tester"]:
        feil.append(f"tester_totalt={total} minus tester_hoppet={hoppet}"
                    f" = {total - hoppet} kjørte, krever >="
                    f" {grense['min_tester']}")
    if feilet > grense["maks_feilet"]:
        feil.append(f"tester_feilet={feilet}, krever <="
                    f" {grense['maks_feilet']}")
    if m2t - m2h < grense["min_m2_tester"]:
        feil.append(f"m2_tester={m2t} minus m2_hoppet={m2h}"
                    f" = {m2t - m2h} kjørte, krever >="
                    f" {grense['min_m2_tester']}")
    if m2f > grense["maks_m2_feilet"]:
        feil.append(f"m2_feilet={m2f}, krever <= {grense['maks_m2_feilet']}")
    # ... og M-2s andel er PINNET og PÅKREVD: både
    # test_pg_og_attestering.py og test_api.py er `skipif(not DSN)`, så en
    # testbase som ikke er satt opp hadde gitt en andel uten én kjørt test.
    # Delingsbetingelsen krever en MÅLING for nettopp denne modulen —
    # en hoppet port måler ingenting.
    if m2h > grense["maks_m2_hoppet"]:
        feil.append(f"m2_hoppet={m2h}, krever <="
                    f" {grense['maks_m2_hoppet']} — M-2s navngitte andel"
                    " skal være KJØRT, ikke hoppet over")
    if m2t > total:
        feil.append(f"m2_tester={m2t} > tester_totalt={total} — andelen"
                    " kan ikke overstige helheten")
    # En AVBRUTT pytest skriver junit-XML likevel, over bare de testene
    # som rakk å bli ferdige: null failures, null errors, og et tall som
    # kan klare gulvet. Exitkoden er det eneste stedet avbruddet står, så
    # den er en egen port — en hel, grønn kjøring er exit 0, og ingenting
    # annet er det.
    for navn, verdi in (("suite_exitkode", kode), ("m2_exitkode", m2k)):
        if verdi != 0:
            feil.append(f"{navn}={verdi} — pytest avsluttet unormalt;"
                        " en junit-XML fra en avbrutt kjøring teller bare"
                        " testene som rakk å bli ferdige, og er ikke en"
                        " hel suite")
    # `or {}` fanget None, men ikke en SANN ikke-dict: er `oppsett` f.eks.
    # strengen "…", melder skjemaet formatfeilen riktig, mens
    # `valider_artefakter` med vilje går videre hit — og et `.get` på en
    # streng kastet AttributeError og rev med seg hele valideringskjøringen
    # i stedet for å returnere de røde funnene. Et vrangt artefakt skal
    # feile lukket, ikke krasje.
    oppsett = art.get("oppsett")
    feil += _m02_bevisrot_feil(art)
    filer = oppsett.get("m2_filer") if isinstance(oppsett, dict) else None
    if not (isinstance(filer, list) and filer
            and all(isinstance(x, str) and x for x in filer)):
        feil.append("oppsett.m2_filer mangler — M-2s andel skal være"
                    " NAVNGITT, ikke antatt (delingsbetingelsen)")
    # ... og NAVNGITT er ikke nok i seg selv: en hvilken som helst ikke-tom
    # strengliste passerte formkravet over, så et artefakt kunne klare
    # tallene med `m2_filer: ["noen andres tester"]` og likevel bli lest
    # som beviset for M-2. Utvalget er derfor PINNET i KRAVGRENSER, og
    # porten krever nøyaktig det — hvilke målinger som beviser punktet er
    # en godkjent beslutning, ikke noe produsenten oppgir om seg selv.
    elif sorted(filer) != sorted(grense["m2_andel_pakrevd"]):
        mangler = sorted(set(grense["m2_andel_pakrevd"]) - set(filer))
        ekstra = sorted(set(filer) - set(grense["m2_andel_pakrevd"]))
        feil.append(
            "oppsett.m2_filer er ikke det godkjente utvalget"
            + (f"; mangler {mangler}" if mangler else "")
            + (f"; ukjente {ekstra}" if ekstra else "")
            + " — delingsbetingelsen krever de PINNEDE målingene, ikke"
              " en liste produsenten valgte selv")
    return feil


def _grenser_m02_fordeling(grense: dict, art: dict) -> list[str]:
    """`m02-fordeling-v1` — det syntetiske settet gjennom
    beslutningsveien. Fordelingen RE-REGNES av radene artefaktet selv
    bærer (SP-11: bytene er bundet, så radene er målingen) og må være
    EKSAKT fasiten — 83 TILLAT er ikke «nesten», det er et annet sett."""
    feil: list[str] = []
    m = art.get("maalt")
    if not isinstance(m, dict):
        return ["artefaktet mangler `maalt`"]
    feil += _m02_bevisrot_feil(art)
    rader = art.get("rader")
    if not isinstance(rader, list) or not rader:
        return feil + ["artefaktet mangler `rader` — en fordeling uten"
                       " radene sine kan ikke re-regnes"]
    talt: dict[str, int] = {}
    ider = set()
    for r in rader:
        if not (isinstance(r, list) and len(r) == 2
                and isinstance(r[0], int) and not isinstance(r[0], bool)
                and r[0] > 0 and isinstance(r[1], str)):
            return feil + ["rader har en linje som ikke er"
                           " [loggpost_id, beslutning]"]
        ider.add(r[0])
        talt[r[1]] = talt.get(r[1], 0) + 1
    if len(ider) != len(rader):
        feil.append("rader gjentar en loggpost — én hendelse er én rad")
    if len(rader) < grense["min_hendelser"]:
        feil.append(f"{len(rader)} rader, krever >="
                    f" {grense['min_hendelser']}")
    if talt != grense["fordeling_eksakt"]:
        feil.append(f"fordelingen re-regnet av radene er {talt}, fasiten"
                    f" er {grense['fordeling_eksakt']} — eksakt, aldri"
                    " «nesten»")
    for nokkel, verdi in grense["fordeling_eksakt"].items():
        oppgitt, melding = _teller(m, f"antall_{nokkel.lower()}",
                                   f"antall_{nokkel.lower()}")
        if melding:
            feil.append(melding)
        elif oppgitt != verdi:
            feil.append(f"antall_{nokkel.lower()}={oppgitt} spriker fra"
                        f" fasiten {verdi}")
    feil += _sett_bundet(grense, art)
    return feil


def _sett_bundet(grense: dict, art: dict) -> list[str]:
    """Er fordelingen drevet av DET settet CI driver — eller et annet?

    Artefaktet bandt settet med `sett_versjon: "m02-sett-1"`, en streng
    noen skriver for hånd, og radene bærer loggpost-id og beslutning —
    ikke hendelsene som ble sendt inn. Et staging-ledd på en eldre
    utrulling, eller med en lokalt endret driver, kunne derfor telle
    84/3/93 av helt andre hendelser og likevel valideres som «likt
    lokalt» (Codex P1, runde 2). Da måler punktet at to kjøringer fikk
    samme SUM, ikke at de drev samme SETT.

    Bytene er bindingen: produsenten hasher driveren den faktisk kjørte
    (`m02_fordeling.sett_sha256`), og her hashes de innsjekkede bytene med
    nøyaktig samme uttrykk. Samme form som §1.2/SP-11 for
    WCAG-datasettet — likhet i BEGGE ledd, ellers rødt.
    """
    oppsett = art.get("oppsett")
    sha = oppsett.get("sett_sha256") if isinstance(oppsett, dict) else None
    if not (isinstance(sha, str) and len(sha) == 64):
        return ["oppsett.sett_sha256 mangler — uten driverens bytes er"
                " «samme sett» en påstand, ikke en måling"]
    if not grense.get("krev_sett_sha_lik_innsjekket"):
        return []
    try:
        lokal = hashlib.sha256(M02_SETT_STI.read_bytes()).hexdigest()
    except OSError as e:
        return [f"settdriveren lot seg ikke hashe lokalt: {e}"]
    if sha != lokal:
        return [f"sett_sha256={sha[:12]}… er ikke de innsjekkede bytenes"
                f" {lokal[:12]}… — settet staging drev er ikke settet CI"
                " driver, og da er ikke fordelingen «lik lokalt»"]
    return []


def _falske_verdikter(m: dict) -> list[str]:
    """SP-3-motsigelsen regnet ut på nytt av råtallene, ikke lest.

    Codex' P2 på PR #117 (runde 2): et falskt verdikt er at UTFALLET og
    EVIDENSEN motsier hverandre, og det går begge veier — et `utfort`
    uten promotert artefakt, og et ikke-`utfort` som likevel promoterte.
    Drillens gamle uttrykk ga alltid 0 for det siste tilfellet, og
    porten her hadde ingen egen telling å regne motsigelsen ut fra.

    RUNDE 3: den forrige formen tilga fravær av tellingen når utfallet
    var `utfort` — «ingen motsigelse er det eneste 0 kan bety». Det var
    å tro på tallet igjen, bare med et ekstra ledd: et `utfort` uten
    promotert artefakt er nettopp den omvendte falske verdikten, og det
    var den formen det innsjekkede drillartefaktet hadde. Tellingen er
    ikke valgfri for noe utfall — uten den er motsigelsen UMÅLT, og en
    umålt motsigelse rapporterer seg selv som null.
    """
    utfall = m.get("inflight_utfall")
    rapportert, melding = _teller(m, "falske_verdikter", "falske_verdikter")
    if melding:
        return []                       # alt rapportert av grensesløyfen
    if "inflight_promoterte_artefakter" not in m:
        return [f"inflight_utfall={utfall!r} uten"
                " `inflight_promoterte_artefakter` — et utfall kan bare"
                " kalles rent når evidensen bak det er TALT; både en"
                " feilet jobb som likevel promoterte OG en utført jobb"
                " uten et eneste artefakt er falske verdikter"]
    promotert, melding = _teller(m, "inflight_promoterte_artefakter",
                                 "inflight_promoterte_artefakter")
    if melding:
        return [melding]
    motsigelse = 0 if (utfall == "utfort") == (promotert > 0) else 1
    if rapportert != motsigelse:
        return [f"falske_verdikter={rapportert}, men utfallet {utfall!r} med"
                f" {promotert} promoterte artefakter gir {motsigelse}"]
    return []


def _bias_utledet(m: dict) -> list[str]:
    """Regner `bias_maling_mangler_for_digest` på nytt fra artefaktets data.

    Returnerer avvikene. Tom liste = det rapporterte tallet stemmer med det
    dataene viser.

    Formkravene er de samme som kjøretidsporten stiller (`krev_biasmaaling`
    i m57_ats/evaluering.py): digesten på formen `sha256:<64 hex>`,
    artefakthashen som 64 hex, tidspunktet som lesbar ISO 8601.
    Forskjellen er hvem som svarer for dem — her er de DATA i et
    hash-bundet akseptartefakt, ikke et kart levert av den samme kalleren
    som ber om evalueringen.
    """
    import re as _re
    from datetime import datetime as _datetime
    from collections import Counter as _Counter
    feil: list[str] = []
    digester = m.get("bias_digester_kjort")
    maalinger = m.get("bias_maalinger")
    if not isinstance(digester, list) or not digester:
        return ["bias_digester_kjort: mangler eller tom — invarianten kan"
                " ikke utledes, og en uutledbar invariant er ingen port"]
    if not isinstance(maalinger, list):
        return ["bias_maalinger: mangler — modulen påstår et bruddtall uten"
                " å vise målingene det hviler på"]

    # `\A`/`\Z`, IKKE `^`/`$`. Pythons `$` matcher rett FØR en avsluttende
    # linjeskift, så `"sha256:<64 hex>\n"` passerte — og skjemamønstrene
    # ved siden av har samme svakhet. Da telles en digest med hengende
    # data som gyldig bevis, mens kjøretidssiden slår opp på den nøyaktige
    # strengen og aldri finner den igjen (Codex P2, #241).
    # BEGGE VERSALFORMER, som kjøretidsporten (Codex P2, runde 3).
    # `_er_sha256` sammenligner `verdi.lower()`, så `krev_biasmaaling`
    # godtar en måling med store heksadesimaler. Grensen avviste den —
    # og en grense som er STRENGERE enn porten den lover å speile, feller
    # akseptartefakter for kjøringer som faktisk gikk igjennom. Løftet er
    # speilingen; da må skrivemåten være den samme.
    DIG = _re.compile(r"\Asha256:[0-9a-fA-F]{64}\Z")
    HEX = _re.compile(r"\A[0-9a-fA-F]{64}\Z")
    dekket = set()
    for i, mal in enumerate(maalinger):
        if not isinstance(mal, dict):
            feil.append(f"bias_maalinger[{i}]: ikke et objekt")
            continue
        d, a = mal.get("image_digest"), mal.get("artefakt_sha256")
        if not isinstance(d, str) or not DIG.match(d):
            feil.append(f"bias_maalinger[{i}].image_digest={d!r} har ikke"
                        " formen sha256:<64 hex>")
            continue
        if not isinstance(a, str) or not HEX.match(a):
            feil.append(f"bias_maalinger[{i}].artefakt_sha256 for {d} er"
                        " ikke en sha256")
            continue
        # TIDSPUNKTET MÅLES HER, FOR SKJEMAET MÅLER DET IKKE.
        # `valider_artefaktformat` kjører `Draft202012Validator` uten
        # `FormatChecker`, så `"format": "date-time"` på `ts` er inert:
        # `""` og `"ikke-en-dato"` passerer skjemaet. Kjøretidsporten
        # feller dem (`bias_maling_uten_tidspunkt`), så uten dette leddet
        # var evidenslaget svakere enn porten det skal speile — samme
        # klasse som «en oppføring er ikke en måling».
        # Datoen leses med kalenderen, ikke med et mønster: en
        # ISO-8601-regex i skjemaet ville vært en håndskrevet grammatikk
        # (K4), og `fromisoformat` er den samme lesningen som porten gjør.
        # GRAMMATIKKEN FØRST, KALENDEREN ETTERPÅ (Codex P2, runde 2).
        # `fromisoformat` er ISO 8601, ikke RFC 3339: den godtar
        # `"2026-01-01x00:00:00+00:00"` (vilkårlig separator), den
        # kompakte formen, og offset med sekunder. `tzinfo`-leddet lukket
        # bare de rapporterte eksemplene, ikke klassen — feltet er
        # DEKLARERT `date-time`, og skjemaet håndhever det ikke
        # (`Draft202012Validator` bygges uten `FormatChecker`).
        #
        # HVORFOR DETTE IKKE ER K4. Forbudet gjelder å hand-parse en
        # FREMMED grammatikk. RFC 3339 §5.6 er ti linjer ABNF, lukket og
        # uforanderlig, og det er VÅRT EGET skjemafelts erklærte form —
        # ikke et dokumentformat vi mottar. Mønsteret avgjør dessuten
        # bare FORMEN; `fromisoformat` under leser fortsatt KALENDEREN, så
        # `2026-02-30T00:00:00Z` felles av den, ikke av regexen. To ledd
        # som måler hver sin ting, ikke én håndskrevet parser.
        RFC3339 = _re.compile(
            r"\A\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?"
            r"([Zz]|[+-]\d{2}:\d{2})\Z")
        if not isinstance(mal.get("ts"), str) or not RFC3339.match(mal["ts"]):
            feil.append(f"bias_maalinger[{i}].ts={mal.get('ts')!r} for {d} er"
                        " ikke RFC 3339 — feltet er deklarert `date-time`,"
                        " og en form skjemaet ikke lover er ikke datert"
                        " bevis")
            continue
        try:
            # `[Zz]` I MØNSTERET, `Z` I NORMALISERINGEN — det var uenige
            # (Codex P2, runde 3). RFC 3339 tillater begge versalformer av
            # UTC-suffikset, mønsteret over godtar begge, men bare den
            # store ble byttet ut før `fromisoformat` — så en gyldig
            # `2026-01-01t00:00:00z` besto grammatikken og felte på
            # kalenderen. To ledd som måler hver sin ting skal ikke være
            # uenige om hva de leser.
            _datetime.fromisoformat(
                _re.sub(r"[Zz]\Z", "+00:00", str(mal.get("ts"))))
        except (TypeError, ValueError):
            # KALENDEREN, ikke formen: mønsteret over slipper
            # `2026-02-30T00:00:00Z` — riktig antall siffer på riktig
            # plass — mens datoen ikke finnes. To ledd som måler hver sin
            # ting er nettopp derfor begge er her.
            feil.append(f"bias_maalinger[{i}].ts={mal.get('ts')!r} for {d} er"
                        " ikke en dato som finnes — formen stemmer, men"
                        " kalenderen sier nei, og en måling uten tidspunkt"
                        " er ikke datert bevis")
            continue
        # `tzinfo`-leddet som sto her er BORTE, ikke glemt: RFC
        # 3339-mønsteret over krever offset, så en tidssonefri verdi når
        # aldri hit. En gren som ikke kan nås er en port som ser ut som en
        # port — og dens egen test ville vært vakuøs.
        # ÉN MÅLING PER DIGEST. Kjøretidssiden er `dict[str, Biasmaaling]`
        # — digesten er nøkkelen, så den bærer nøyaktig én måling. En
        # liste med to oppføringer for samme digest kan derfor ikke være
        # en tro gjengivelse av kartet, og hvilken av dem som er beviset
        # er ikke noe denne funksjonen kan avgjøre. Tvetydig bevis felles.
        if d in dekket:
            feil.append(f"bias_maalinger[{i}]: {d} er målt mer enn én gang"
                        " — kjøretidsporten bærer én måling per digest, og"
                        " to som utgir seg for samme er ikke bevis, men et"
                        " valg denne grensen ikke skal ta")
            continue
        # ... og settene sammenlignes på samme normalform. Med begge
        # versalformer lovlige ville `sha256:AB...` i digestlisten og
        # `sha256:ab...` i målingen sett ut som to ulike digester, og
        # dekningen ville rapportert både «mangler måling» og
        # «foreldreløs måling» for én og samme modellversjon.
        dekket.add(d.lower())

    ukjente = [d for d in digester if not isinstance(d, str) or not DIG.match(d)]
    if ukjente:
        feil.append(f"bias_digester_kjort inneholder verdier som ikke er"
                    f" digester: {ukjente}")
        return feil

    # DUPLIKATER ER IKKE FORSØK. Forsøkstallet måles mot `len(digester)`,
    # dekningen mot `set(digester)` — står samme digest tre ganger, blir
    # `forsok=3` sant med ÉN måling og null brudd. Det er nøyaktig hullet
    # #167 stengte («en kjøring med én digest kunne rapportere tre forsøk
    # og se grundigere ut enn den var»), gjenåpnet gjennom grunnlaget
    # invarianten utledes av. Listen navngir digestene kjøringen brukte;
    # å bruke den samme igjen er ikke en digest til.
    # Telt ÉN gang med `Counter`, ikke `list.count()` per element:
    # `bias_digester_kjort` har ingen `maxItems`, så den kvadratiske
    # formen gjorde valideringen dyrere jo større artefaktet ble — også
    # når hver digest var unik (Codex P2, #241).
    duplikater = sorted(
        d for d, n in _Counter(x.lower() for x in digester).items() if n > 1)
    if duplikater:
        feil.append(f"bias_digester_kjort gjentar digest(er):"
                    f" {duplikater} — en gjentakelse er ikke et forsøk"
                    " til, og et forsøkstall som teller den er ikke en"
                    " måling")
        return feil

    # DEKNINGEN MÅLES BEGGE VEIER. `set(digester) - dekket` finner
    # digester uten måling; den omvendte differansen finner målinger uten
    # digest — en måling for noe kjøringen aldri deklarerte at den brukte.
    # Da motsier evidenslistene hverandre, og forsøkstallet (= antall
    # deklarerte digester) beskriver ikke lenger kjøringen målingene
    # dokumenterer. Valg B gjorde det deklarerte digest-settet til
    # grunnlaget invarianten utledes av; ekstra biasbevis smuglet inn ved
    # siden av det settet er nettopp lineage-disiplinen B innførte.
    foreldrelose = sorted(dekket - {d.lower() for d in digester})
    if foreldrelose:
        feil.append(f"bias_maalinger måler digest(er) som ikke står i"
                    f" bias_digester_kjort: {foreldrelose} — en måling"
                    " for en digest kjøringen ikke sier den brukte,"
                    " dokumenterer en annen kjøring enn den som telles")
        return feil

    mangler = sorted({d.lower() for d in digester} - dekket)
    rapportert, f1 = _teller(m, "maalt.bias_maling_mangler_for_digest_brudd",
                             "bias_maling_mangler_for_digest_brudd")
    if f1:
        feil.append(f1)
    elif rapportert != len(mangler):
        feil.append(
            f"bias_maling_mangler_for_digest_brudd={rapportert}, men"
            f" dataene viser {len(mangler)} digest(er) uten måling"
            f" ({mangler or 'ingen'}) — et rapportert tall som ikke stemmer"
            " med målingene det skal hvile på, er ikke en måling")

    forsok, f2 = _teller(m, "maalt.bias_maling_mangler_for_digest_forsok",
                         "bias_maling_mangler_for_digest_forsok")
    if f2:
        feil.append(f2)
    elif forsok != len(digester):
        feil.append(
            f"bias_maling_mangler_for_digest_forsok={forsok}, men kjøringen"
            f" brukte {len(digester)} digest(er) — forsøkstallet er antallet"
            " digester porten faktisk ble stilt spørsmålet om")
    return feil


def _grenser_m57(grense: dict, art: dict) -> list[str]:
    """`m57-v1` — M-57-klarsignalet §10. Hver invariant er et par
    (forsøk, brudd): null brudd beviser ingenting uten minst ett forsøk,
    og settet av invarianter er grensens, ikke artefaktets."""
    feil: list[str] = []
    m = art.get("maalt")
    if not isinstance(m, dict):
        return ["artefaktet mangler `maalt`"]
    for navn in grense["invarianter"]:
        forsok, f1 = _teller(m, f"{navn}_forsok", f"{navn}_forsok")
        brudd, f2 = _teller(m, f"{navn}_brudd", f"{navn}_brudd")
        for melding in (f1, f2):
            if melding:
                feil.append(melding)
        if f1 or f2:
            continue
        if forsok < grense["min_forsok"]:
            feil.append(f"{navn}_forsok={forsok}, krever >="
                        f" {grense['min_forsok']} — en port som aldri"
                        " kjørte har ikke målt noe")
        if brudd > grense["maks_brudd"]:
            feil.append(f"{navn}_brudd={brudd}, krever <="
                        f" {grense['maks_brudd']}")
    # BIASINVARIANTEN UTLEDES, DEN LESES IKKE (#167 valg B).
    #
    # `bias_maling_mangler_for_digest` var to selvrapporterte tall: modulen
    # skrev «0 brudd», og grensen leste «0 brudd». Kjøretidsporten
    # `krev_biasmaaling` måler bare FORMEN på en måling — `"0" * 64` er en
    # syntaktisk gyldig artefakthash — så invarianten talte manglende
    # OPPFØRINGER, ikke manglende MÅLINGER, og leste sterkere enn noe sted
    # målte. Codex felte mekanismen tre ganger på #153.
    #
    # Artefaktet bærer nå dataene invarianten hviler på: hvilke
    # modelldigester kjøringen brukte, og hvilken biasartefakt som dekker
    # hver av dem. Bruddtallet regnes på NYTT herfra, og et avvik mellom
    # det utledede og det rapporterte er selve funnet — samme disiplin som
    # `_grenser_rollback`: tallene mot hverandre, ikke mot flagg.
    #
    # Dette gjør ikke porten absolutt: at biasartefakten FINNES i et lager
    # er #167 valg A, og hører i controlleren som har tenantkontekst — ikke
    # i en ren rangeringsfunksjon. Men «modulen påstår null» er nå erstattet
    # av «her er digestene, her er målingene, regn selv».
    feil += _bias_utledet(m)

    for navn in grense["krav_ja"]:
        if m.get(navn) is not True:
            feil.append(f"{navn}={m.get(navn)!r}, krever bokstavelig true"
                        " — et punkt uten målbar grense regnes som nei")
    # Ytelsen (§4): den fulle bunten OG tiden den tok, målt sammen.
    # Hver for seg beviser de ingenting — en rask kjøring på ti søknader
    # er ikke 240-minuttersløftet, og en full bunt uten varighet er bare
    # en påstand om at det gikk.
    soknader, f_ant = _teller(m, "maalt.ytelse_full_bunt_soknader",
                              "ytelse_full_bunt_soknader")
    if f_ant:
        feil.append(f_ant)
    elif soknader < grense["ytelse_min_soknader"]:
        feil.append(
            f"ytelse_full_bunt_soknader={soknader}, krever >="
            f" {grense['ytelse_min_soknader']} — ytelsespunktet er den"
            " FULLE bunten, ikke en prøve")
    minutter, f_tid = _positiv(m, "maalt.ytelse_full_bunt_minutter",
                               "ytelse_full_bunt_minutter")
    if f_tid:
        feil.append(f_tid)
    elif minutter > grense["ytelse_maks_minutter"]:
        feil.append(
            f"ytelse_full_bunt_minutter={minutter:g}, krever <="
            f" {grense['ytelse_maks_minutter']} (§4s utførelsesfrist)")
    return feil


def _grenser_rollback_m56(grense: dict, art: dict) -> list[str]:
    """`rollback-m56-v1` — flippedrillen for moduldeployment (049).

    Den bindende delen er fire påstander som alle regnes ut fra råtall:
    den drillede releasen claimet INGENTING etter dreneringen, det
    løpende oppdraget fikk et rent, signert utfall (SP-3: aldri et
    falskt verdikt), RULLBAKKEN SELV bootet og gjorde arbeid (Codex P1,
    #117: uten det måler drillen bare at den gamle arbeideren sluttet å
    claime), og kandidaten — byte-identisk med den drillede
    (A1) — plukket og promoterte. Digestlikheten måles her OG av
    `registrer_moduldrill` i basen; to porter, samme sannhet.
    """
    feil: list[str] = []
    m, o, k = art.get("maalt"), art.get("oppsett"), art.get("etterkontroll")
    if not isinstance(m, dict) or not isinstance(o, dict)             or not isinstance(k, dict):
        return ["artefaktet mangler `maalt`/`oppsett`/`etterkontroll`"]
    for felt, tak in (
            ("nye_oppdrag_claimet_av_drillet_release",
             grense["maks_claims_etter_drenering"]),
            ("falske_verdikter", grense["maks_falske_verdikter"])):
        verdi, melding = _teller(m, felt, felt)
        if melding:
            feil.append(melding)
        elif verdi > tak:
            feil.append(f"{felt}={verdi}, krever <= {tak}")
    for felt, minst in (
            ("inflight_oppdrag", grense["min_inflight"]),
            # (b2): rullbakken skal ha BOOTET og gjort arbeid.
            ("rullback_claimet_oppdrag", grense["min_rullback_claims"]),
            ("rullback_promoterte_artefakter",
             grense["min_rullback_promoterte"]),
            ("kandidat_claimet_oppdrag", grense["min_kandidat_claims"]),
            ("kandidat_promoterte_artefakter",
             grense["min_kandidat_promoterte"])):
        verdi, melding = _teller(m, felt, felt)
        if melding:
            feil.append(melding)
        elif verdi < minst:
            feil.append(f"{felt}={verdi}, krever >= {minst}")
    if m.get("inflight_har_signert_kvittering") is not True:
        feil.append("det løpende oppdraget mangler signert kvittering")
    # (052, aksept-arc-klarsignalet §1.1a): «rullbakken fullførte selv»
    # krever kvitteringen fra DENS kjøring — promoteringstellingen over
    # sier at et artefakt finnes, ikke at kvitteringsveien var innom.
    # `registrer_moduldrill` måler det samme i basen (claim_stopp_ok);
    # her måles filens egen påstand, samme form som inflight-leddet.
    if m.get("rullback_har_signert_kvittering") is not True:
        feil.append("rullbakkens oppdrag mangler signert kvittering —"
                    " en rullbakk som ikke selv fullførte med kvittering"
                    " fra SIN kjøring er ikke målt (§1.1a)")
    if m.get("inflight_utfall") not in grense["rene_utfall"]:
        feil.append(f"inflight_utfall={m.get('inflight_utfall')!r} er ikke"
                    f" et rent utfall ({sorted(grense['rene_utfall'])})")
    feil += _falske_verdikter(m)
    vent, melding = _positiv(m, "ventetid_ubehandlet_s",
                             "ventetid_ubehandlet_s")
    if melding:
        feil.append(melding)
    elif vent < grense["min_ventetid_s"]:
        feil.append(f"ventetid_ubehandlet_s={vent:g} — claim-stoppet må"
                    f" observeres i minst {grense['min_ventetid_s']:g} s")
    # A1: likheten regnes ut fra digestene, aldri fra flagget.
    if o.get("drillet_digest") != o.get("kandidat_digest"):
        feil.append("kandidatens digest er ikke den drillede — aksepterte"
                    " bytes må være drillede bytes")
    if k.get("digest_likhet") is not True:
        feil.append("etterkontrollen bekrefter ikke digestlikheten")
    # (b2), andre halvdel (Codex P1, #117 runde 6): rullbakken skal ha
    # bootet FORGJENGERENS bytes. Drillen registrerte før dette
    # rullback-releasen med den DRILLEDE deploymentens digest, så
    # rullbakken var kandidatens egne bytes under et annet navn — og
    # (b2) kunne stå grønt uten at det man ruller tilbake TIL noen gang
    # var prøvd. Som for A1 regnes likheten ut av digestene her, aldri
    # av flagget: flagget er drillens påstand, digestene er målingen.
    if o.get("rullback_digest") != o.get("forgjenger_digest"):
        feil.append("rullbakkens digest er ikke forgjengerens — en"
                    " rullbakk til andre bytes enn dem man rullet vekk"
                    " fra, prøver ikke det man ruller tilbake til")
    if k.get("rullback_bytes_er_forgjengerens") is not True:
        feil.append("etterkontrollen bekrefter ikke at rullbakken bar"
                    " forgjengerens bytes")
    if o.get("forgjenger_release") in (None, "", o.get("drillet_release")):
        feil.append(f"forgjenger_release={o.get('forgjenger_release')!r} —"
                    " en drill kan ikke rulle tilbake til seg selv, og"
                    " «rullet tilbake» uten retning er en påstand")
    if k.get("drillet_livslop") != "draining":
        feil.append(f"drillet_livslop={k.get('drillet_livslop')!r},"
                    " ventet 'draining' (drillen konsumerer den drillede)")
    # Rullbakken KJØRTE, og ble så konsumert av fram-rullingen — begge
    # deler er en del av påstanden «det gikk an å rulle tilbake».
    if k.get("rullback_livslop") != "draining":
        feil.append(f"rullback_livslop={k.get('rullback_livslop')!r},"
                    " ventet 'draining' (kandidaten overtok etter den)")
    if k.get("kandidat_livslop") != "claiming":
        feil.append(f"kandidat_livslop={k.get('kandidat_livslop')!r},"
                    " ventet 'claiming' (aksepten binder raden som kjører)")
    if k.get("modulstatus") != "aktiv":
        feil.append(f"modulstatus={k.get('modulstatus')!r} etter drillen")
    feil += _identiteter_stemmer(art)
    return feil


def _identiteter_stemmer(art: dict) -> list[str]:
    """Tellingene og identitetene må være samme observasjon.

    Codex' P2 på PR #117 (runde 3): drillartefaktet bar bare ANTALL, så
    aksepten kunne referere et E2E-artefakt drillen aldri så — FK-en i
    049 skiller ikke ett promotert artefakt fra et annet på samme
    release. Identitetene er nå med, og her måles at de er den SAMME
    målingen som tallene: et antall som ikke stemmer med listen betyr at
    minst én av dem er skrevet, ikke målt.
    """
    ident = art.get("identiteter")
    m = art.get("maalt") or {}
    if not isinstance(ident, dict):
        return ["artefaktet mangler `identiteter` — et antall uten"
                " identitet kan ikke bindes til aksepten"]
    feil: list[str] = []
    for felt, telling in (("inflight_artefakter",
                           "inflight_promoterte_artefakter"),
                          ("rullback_artefakter",
                           "rullback_promoterte_artefakter"),
                          ("kandidat_artefakter",
                           "kandidat_promoterte_artefakter")):
        liste = ident.get(felt)
        if not isinstance(liste, list):
            feil.append(f"identiteter.{felt} mangler")
            continue
        # En identitet er en artefakt-UUID. Bærer listen noe annet, er den
        # ikke en måling å binde aksepten til — og den skal si det som en
        # akkumulert feil, ikke som en traceback ut av valideringen.
        if any(not isinstance(x, str) or not x.strip() for x in liste):
            feil.append(f"identiteter.{felt} har oppføringer som ikke er en"
                        " artefakt-identitet — hver oppføring må være en"
                        " ikke-tom streng")
            continue
        antall, melding = _teller(m, telling, telling)
        if melding:
            continue                    # alt rapportert av grensesløyfen
        if len(_unike(liste)) != len(liste):
            feil.append(f"identiteter.{felt} har gjentakelser — samme"
                        " artefakt talt to ganger er ikke to artefakter")
        elif len(liste) != antall:
            feil.append(f"identiteter.{felt} har {len(liste)} artefakter,"
                        f" men {telling}={antall} — tallet og listen er"
                        " ikke samme måling")
    for felt in ("inflight_oppdrag_id", "rullback_oppdrag_id",
                 "kandidat_oppdrag_id"):
        if not str(ident.get(felt) or "").strip():
            feil.append(f"identiteter.{felt} mangler")
    return feil


def _grenser_behandling(grense: dict, art: dict) -> list[str]:
    """`behandling-m37-v1` — de fire kategori-veiene + de harde invariantene.

    Invariantene REGNES UT på nytt her, aldri lest ut av et flagg (lærdommen
    fra PR #8 runde 3): hver kategori-andel sjekkes mot `antall/injisert`, og
    en kategori med `injisert = 0` er en vei som aldri ble prøvd — ikke en
    bestått. De harde grensene (saksversjonskonflikt uten sideeffekt, nøyaktig
    én vinner, ingen klartekst) måles mot råtellingene.
    """
    feil: list[str] = []
    m, oppsett = art.get("maalt"), art.get("oppsett")
    if not isinstance(m, dict) or not isinstance(oppsett, dict):
        return ["artefaktet mangler `maalt` og/eller `oppsett`"]

    injisert, f = _teller(oppsett, "oppsett.injisert_antall", "injisert_antall")
    if f:
        feil.append(f)
    elif injisert < grense["min_injisert"]:
        feil.append(f"injisert_antall={injisert},"
                    f" krever >= {grense['min_injisert']}")

    # Kategorimengden må være EKSAKT de fire kontraktskategoriene — ikke bare
    # «minst fire». Ellers består et sett med en oppdiktet kategori som fyller
    # tallet mens en ekte mangler.
    KONTRAKT = {"avvis", "godkjenn", "sideeffekt", "fire_oyne"}
    for navn, verdi in (("oppsett.kategorier", oppsett.get("kategorier")),
                        ("kategorier_dekket", m.get("kategorier_dekket"))):
        if not isinstance(verdi, list) or _unike(verdi) != _unike(KONTRAKT) \
                or len(verdi) != len(KONTRAKT):
            feil.append(f"{navn}={verdi!r}, krever NØYAKTIG {sorted(KONTRAKT)}")

    # Hver kategori: andelen er 1.0, så kravet er EKSAKT `utfall == injisert`
    # med `injisert > 0`. `utfall/injisert >= 1.0` alene godtar teller > nevner
    # (2 av 1 = 200 %); det er en umulig måling, ikke en høy andel.
    sum_inj = 0
    for grp, antallsfelt in (("avvis", "terminal"),
                             ("godkjenn", "ny_beslutning"),
                             ("sideeffekt", "til_utforelse"),
                             ("fire_oyne", "fullfort")):
        u = m.get(grp)
        if not isinstance(u, dict):
            feil.append(f"maalt.{grp} mangler")
            continue
        inj, f1 = _teller(u, f"{grp}.injisert", "injisert")
        ant, f2 = _teller(u, f"{grp}.{antallsfelt}", antallsfelt)
        if f1 or f2:
            feil.extend(x for x in (f1, f2) if x)
            continue
        sum_inj += inj
        if inj == 0:
            feil.append(f"{grp}.injisert er 0 — veien ble aldri prøvd")
        elif ant != inj:
            feil.append(f"{grp}: {antallsfelt}={ant} != injisert={inj}"
                        f" — krever eksakt likhet (andel 1.0; teller > nevner"
                        f" er umulig)")

    # Summen av kategori-nevnerne MÅ være totalen: ellers består total=12 med
    # fire kategorier på 1/1 (bare fire faktiske forsøk).
    if sum_inj != injisert:
        feil.append(f"sum av kategori-injisert ({sum_inj}) != total"
                    f" injisert_antall ({injisert})")

    # «Nøyaktig én vinner» fra RÅTELLINGER, ikke et flagg: begge tråder må ha
    # FULLFØRT (ingen henger), og det skal være akkurat én vinner og resten
    # tapere per konkurranse. Null vinnere eller en hengende tråd = rødt.
    konk, fk = _teller(m, "samtidig_konkurranser", "samtidig_konkurranser")
    startet, fs = _teller(m, "samtidig_startet", "samtidig_startet")
    fullfort, ff = _teller(m, "samtidig_fullfort", "samtidig_fullfort")
    vinnere, fv = _teller(m, "samtidig_vinnere", "samtidig_vinnere")
    tapere, ft = _teller(m, "samtidig_tapere", "samtidig_tapere")
    if any((fk, fs, ff, fv, ft)):
        feil.extend(x for x in (fk, fs, ff, fv, ft) if x)
    else:
        if konk < grense["min_samtidig_konkurranse"]:
            feil.append(f"samtidig_konkurranser={konk}, krever >="
                        f" {grense['min_samtidig_konkurranse']}")
        if startet != 2 * konk:
            feil.append(f"samtidig_startet={startet} != 2*konkurranser"
                        f" ({2 * konk}) — to tråder per konkurranse")
        if fullfort != startet:
            feil.append(f"samtidig_fullfort={fullfort} != startet={startet}"
                        f" — en tråd fullførte ikke (hang / manglende resultat)")
        if vinnere != konk:
            feil.append(f"samtidig_vinnere={vinnere} != konkurranser={konk}"
                        f" — krever NØYAKTIG én vinner per konkurranse")
        if tapere != startet - vinnere:
            feil.append(f"samtidig_tapere={tapere} != startet-vinnere"
                        f" ({startet - vinnere})")

    # Kvitterings-vs-avvis-rasen (scope-beslutningen §3): begge tråder
    # fullfører, avvis flagger avklaring, kvitteringen bevares — og INGEN sak
    # påstår `avvist` mens et oppdrag lever. Alt fra råtellinger.
    krace, fkr = _teller(m, "kvitteringsrace_konkurranser",
                         "kvitteringsrace_konkurranser")
    kfull, fkf = _teller(m, "kvitteringsrace_fullfort", "kvitteringsrace_fullfort")
    kflagg, fkfl = _teller(m, "kvitteringsrace_avvis_flagget",
                           "kvitteringsrace_avvis_flagget")
    kbrukt, fkb = _teller(m, "kvitteringsrace_kvittering_brukt",
                          "kvitteringsrace_kvittering_brukt")
    kfalsk, fkfa = _teller(m, "kvitteringsrace_falskt_avvist",
                           "kvitteringsrace_falskt_avvist")
    if any((fkr, fkf, fkfl, fkb, fkfa)):
        feil.extend(x for x in (fkr, fkf, fkfl, fkb, fkfa) if x)
    else:
        if krace < grense["min_kvitteringsrace"]:
            feil.append(f"kvitteringsrace_konkurranser={krace}, krever >="
                        f" {grense['min_kvitteringsrace']}")
        if kfull != 2 * krace:
            feil.append(f"kvitteringsrace_fullfort={kfull} != 2*konkurranser"
                        f" ({2 * krace}) — avvis-tråd + kvittering-tråd må begge fullføre")
        if kflagg != krace:
            feil.append(f"kvitteringsrace_avvis_flagget={kflagg} != konkurranser"
                        f" ({krace}) — avvis skal flagge avklaring hver gang")
        if kbrukt != krace:
            feil.append(f"kvitteringsrace_kvittering_brukt={kbrukt} != konkurranser"
                        f" ({krace}) — kvitteringen skal bevares hver gang")
        if kfalsk > grense["maks_kvitteringsrace_falskt_avvist"]:
            feil.append(f"kvitteringsrace_falskt_avvist={kfalsk}, krever <="
                        f" {grense['maks_kvitteringsrace_falskt_avvist']} —"
                        f" saken påsto `avvist` mens et oppdrag levde")

    for felt, tak, notat in (
            ("saksversjonskonflikt_sideeffekt",
             grense["maks_saksversjonskonflikt_sideeffekt"],
             "en konflikt skal ALDRI ha sideeffekt"),
            ("klartekst_treff", grense["maks_klartekst_treff"],
             "ingen klartekst i logg/dump")):
        v, f = _teller(m, felt, felt)
        if f:
            feil.append(f)
        elif v > tak:
            feil.append(f"{felt}={v}, krever <= {tak} ({notat})")

    v409, f = _teller(m, "saksversjonskonflikt_409", "saksversjonskonflikt_409")
    if f:
        feil.append(f)
    elif v409 < grense["min_saksversjonskonflikt"]:
        feil.append(f"saksversjonskonflikt_409={v409}, krever >="
                    f" {grense['min_saksversjonskonflikt']}")

    med, f1 = _teller(m, "handlinger_med_aktor", "handlinger_med_aktor")
    tot, f2 = _teller(m, "handlinger_totalt", "handlinger_totalt")
    if f1 or f2:
        feil.extend(x for x in (f1, f2) if x)
    elif tot == 0 or med != tot:
        feil.append(f"handlinger_med_aktor={med} != handlinger_totalt={tot}"
                    f" — alle handlinger i revisjonsloggen MÅ ha aktør")

    varighet, f = _positiv(m, "maalt.varighet_sek", "varighet_sek")
    if f:
        feil.append(f)
    elif varighet > grense["maks_varighet_sek"]:
        feil.append(f"varighet_sek={varighet:g},"
                    f" krever <= {grense['maks_varighet_sek']:g}")
    return feil


def _grenser_policyadmin(grense: dict, art: dict) -> list[str]:
    """`policyadmin-v1` — de fire fullmakts-veiene + de harde invariantene.

    Som `_grenser_behandling`: hver andel REGNES UT på nytt fra råtellinger, og
    en kategori med `injisert = 0` er en vei som aldri ble prøvd — ikke en
    bestått. Kategorimengden håndheves som EKSAKT settlikhet (ikke «minst
    fire»), så et sett med en oppdiktet kategori som fyller tallet mens en ekte
    mangler, avvises. De harde invariantene (aldri flere aktive, runtime kan
    ikke skrive policyer, diff-binding full) måles mot råtellingene, aldri et
    flagg.
    """
    feil: list[str] = []
    m, oppsett = art.get("maalt"), art.get("oppsett")
    if not isinstance(m, dict) or not isinstance(oppsett, dict):
        return ["artefaktet mangler `maalt` og/eller `oppsett`"]

    injisert, f = _teller(oppsett, "oppsett.injisert_antall", "injisert_antall")
    if f:
        feil.append(f)
    elif injisert < grense["min_injisert"]:
        feil.append(f"injisert_antall={injisert},"
                    f" krever >= {grense['min_injisert']}")

    KONTRAKT = {"utvider", "forfatter_alene", "innsnevrer", "rebasering"}
    for navn, verdi in (("oppsett.kategorier", oppsett.get("kategorier")),
                        ("kategorier_dekket", m.get("kategorier_dekket"))):
        if not isinstance(verdi, list) or _unike(verdi) != _unike(KONTRAKT) \
                or len(verdi) != len(KONTRAKT):
            feil.append(f"{navn}={verdi!r}, krever NØYAKTIG {sorted(KONTRAKT)}")

    # Hver kategori: andelen er 1.0 → EKSAKT `utfall == injisert` med
    # `injisert > 0`. `>= 1.0` alene godtar teller > nevner (umulig måling).
    for grp, utfallsfelt in (("utvider", "aktivert"),
                             ("forfatter_alene", "stoppet"),
                             ("innsnevrer", "aktivert"),
                             ("rebasering", "rebasert")):
        u = m.get(grp)
        if not isinstance(u, dict):
            feil.append(f"maalt.{grp} mangler")
            continue
        inj, f1 = _teller(u, f"{grp}.injisert", "injisert")
        ant, f2 = _teller(u, f"{grp}.{utfallsfelt}", utfallsfelt)
        if f1 or f2:
            feil.append(f1 or f2)
            continue
        if inj <= 0:
            feil.append(f"maalt.{grp}.injisert={inj} — veien ble aldri prøvd")
        elif ant != inj:
            feil.append(f"maalt.{grp}: {utfallsfelt}={ant} != injisert={inj}"
                        f" (andelen skal være 1.0)")

    # V10/V1: INGEN policy ender med mer enn én aktiv rad.
    flere, f = _teller(m, "maalt.policyer_med_flere_aktive",
                       "policyer_med_flere_aktive")
    if f:
        feil.append(f)
    elif flere > grense["maks_flere_aktive"]:
        feil.append(f"policyer_med_flere_aktive={flere},"
                    f" krever <= {grense['maks_flere_aktive']}")

    # V10: runtime MÅ nektes direkte skriving til `policyer`.
    nekt, f = _teller(m, "maalt.runtime_skrivenekt", "runtime_skrivenekt")
    if f:
        feil.append(f)
    elif nekt < grense["krev_runtime_skrivenekt"]:
        feil.append(f"runtime_skrivenekt={nekt},"
                    f" krever >= {grense['krev_runtime_skrivenekt']}"
                    " (runtime kunne skrive policyer direkte — V10 brutt)")

    # Godkjenneren attesterte DIFFEN: hver attestasjons diff_hash == rundens.
    treff, f1 = _teller(m, "maalt.diff_binding_treff", "diff_binding_treff")
    tot, f2 = _teller(m, "maalt.diff_binding_totalt", "diff_binding_totalt")
    if f1 or f2:
        feil.append(f1 or f2)
    elif grense.get("krev_diff_binding_full"):
        if tot <= 0:
            feil.append("diff_binding_totalt=0 — ingen attestasjon å binde")
        elif treff != tot:
            feil.append(f"diff_binding: treff={treff} != totalt={tot}"
                        " (en attestasjon bandt ikke diffen den så)")

    varighet, f = _positiv(m, "maalt.varighet_sek", "varighet_sek")
    if f:
        feil.append(f)
    elif varighet > grense["maks_varighet_sek"]:
        feil.append(f"varighet_sek={varighet:g},"
                    f" krever <= {grense['maks_varighet_sek']:g}")
    return feil


def _grenser_feilinjisering(grense: dict, art: dict) -> list[str]:
    """`feilinjisering-m01-v1` — de ni målene fra v1 §5 og v2-delta pkt. 8.

    Invariantene REGNES UT på nytt her, aldri lest ut av et flagg. Det er
    lærdommen fra PR #8 runde 3: `bestatt`, `en_til_en` og `routing_stemmer`
    er alle produsentens EGEN påstand, og en port som leser konklusjonen
    validerer ingenting. Her betyr det at `terminal_andel` sjekkes mot
    `terminal_antall / injisert_antall`, ikke bare mot 1.0.
    """
    feil: list[str] = []
    m, oppsett = art.get("maalt"), art.get("oppsett")
    if not isinstance(m, dict) or not isinstance(oppsett, dict):
        return ["artefaktet mangler `maalt` og/eller `oppsett`"]
    k = art.get("etterkontroll")
    if not isinstance(k, dict):
        return ["artefaktet mangler `etterkontroll`"]

    injisert, f = _teller(oppsett, "oppsett.injisert_antall", "injisert_antall")
    if f:
        feil.append(f)
    elif injisert < grense["min_injisert"]:
        feil.append(f"injisert_antall={injisert},"
                    f" krever >= {grense['min_injisert']}")

    kategorier = m.get("kategorier_dekket")
    if not isinstance(kategorier, list):
        feil.append(f"kategorier_dekket={kategorier!r} er ikke en liste")
    elif len(_unike(kategorier)) < grense["min_kategorier"]:
        feil.append(f"kategorier_dekket har {len(_unike(kategorier))} unike,"
                    f" krever >= {grense['min_kategorier']}")

    # Andelene mot tellingene. En andel oppgitt som 1.0 mens tellingene
    # sier noe annet, er to ulike kjøringer i samme fil.
    for andelsfelt, antallsfelt, nevnerfelt, krav in (
            ("terminal_andel", "terminal_antall", None,
             grense["krev_terminal_andel"]),
            ("lost_andel", "lost_antall", "reparerbare",
             grense["krev_lost_andel"]),
            ("manuell_andel", "manuell_antall", "ikke_reparerbare",
             grense["krev_manuell_andel"])):
        andel, f1 = _andel(m, andelsfelt, andelsfelt)
        antall, f2 = _teller(m, antallsfelt, antallsfelt)
        nevner, f3 = ((injisert, "") if nevnerfelt is None
                      else _teller(m, nevnerfelt, nevnerfelt))
        for melding in (f1, f2, f3):
            if melding:
                feil.append(melding)
        if f1 or f2 or f3:
            continue
        if andel < krav:
            feil.append(f"{andelsfelt}={andel:g}, krever >= {krav:g}")
        if nevner == 0:
            # 0/0 er ikke 1.0. Et testsett uten reparerbare saker beviser
            # ikke at reparerbare saker blir løst — det beviser at ingen
            # ble prøvd. Uten denne kunne artefaktet oppgitt
            # reparerbare=0, lost=0, andel=1.0 og bestått.
            if antall == 0 and krav > 0:
                feil.append(f"{andelsfelt}: nevneren ({nevnerfelt or 'injisert'})"
                            f" er 0 — andelen er udefinert, ikke oppfylt")
            continue
        utregnet = antall / nevner
        if abs(utregnet - andel) > 1e-9:
            feil.append(f"{andelsfelt}={andel:g} stemmer ikke med"
                        f" {antallsfelt}/{nevnerfelt or 'injisert_antall'}"
                        f" = {antall}/{nevner} = {utregnet:g}")

    varighet, f = _positiv(m, "maalt.varighet_sek", "varighet_sek")
    if f:
        feil.append(f)
    elif varighet > grense["maks_varighet_sek"]:
        feil.append(f"varighet_sek={varighet:g},"
                    f" krever <= {grense['maks_varighet_sek']:g}")

    p95, f = _positiv(m, "p95_api_under_last_ms", "p95_api_under_last_ms")
    if f:
        feil.append(f)
    elif p95 >= grense["maks_p95_api_under_last_ms"]:
        feil.append(f"p95_api_under_last_ms={p95:g}, krever <"
                    f" {grense['maks_p95_api_under_last_ms']:g} — målt MENS"
                    " arbeideren kjører, ellers beviser tallet ingenting om"
                    " prosessisolasjonen")

    reclaim, f = _teller(m, "lease_tap_re_claim", "lease_tap_re_claim")
    if f:
        feil.append(f)
    elif reclaim < grense["min_lease_tap_re_claim"]:
        feil.append(f"lease_tap_re_claim={reclaim}, krever >="
                    f" {grense['min_lease_tap_re_claim']} — gjenopptaksveien"
                    " må være KJØRT, ikke bare implementert")

    if k.get("historikk_komplett") is not True:
        feil.append("etterkontroll: historikk_komplett er ikke true")
    if k.get("klartekst_payload_funnet") is not False:
        feil.append("etterkontroll: klartekst_payload_funnet er ikke false")
    if k.get("eiermodul_kun_api") is not True:
        feil.append("etterkontroll: eiermodul_kun_api er ikke true")
    canary = k.get("canary_verdier")
    if not isinstance(canary, list) or not canary:
        # Et grep uten kjente kanarifugler beviser bare at grep-mønsteret
        # ikke traff noe — ikke at klarteksten ikke var der (v2-delta pkt. 8).
        feil.append("etterkontroll: canary_verdier mangler — et grep uten"
                    " kjente verdier beviser ingenting om klartekst")

    # PID-ene er separat-prosess-beviset. Er de like, kjørte arbeideren
    # inne i API-prosessen, og hele arkitekturbeslutningen fra §0 er brutt
    # uten at noe annet tall ville avslørt det.
    api_pid, f1 = _teller(oppsett, "oppsett.api_pid", "api_pid")
    m37_pid, f2 = _teller(oppsett, "oppsett.m37_pid", "m37_pid")
    for melding in (f1, f2):
        if melding:
            feil.append(melding)
    if not f1 and not f2 and api_pid == m37_pid:
        feil.append(f"api_pid == m37_pid ({api_pid}) — arbeideren kjørte i"
                    " API-prosessen, i strid med PR-006 §0")

    fordeling = k.get("status_fordeling")
    if not isinstance(fordeling, dict):
        feil.append("etterkontroll mangler `status_fordeling`")
    else:
        sum_alle, gyldig = 0, True
        for status in sorted(fordeling):
            verdi, melding = _teller(fordeling, f"status_fordeling.{status}",
                                     status)
            if melding:
                feil.append(melding)
                gyldig = False
            else:
                sum_alle += verdi
        if gyldig and injisert is not None and sum_alle != injisert:
            feil.append(f"summen av status_fordeling ({sum_alle}) !="
                        f" injisert_antall ({injisert})"
                        f" — fordeling: {dict(sorted(fordeling.items()))}")
        # Terminal er `løst|avvist|manuell` og INGENTING annet.
        # `venter_utførelse` er en sak som venter på en kvittering, og en
        # sak som venter er ikke en sak som er behandlet ferdig.
        terminale = sum(fordeling.get(s, 0) for s in
                        ("løst", "avvist", "manuell")
                        if isinstance(fordeling.get(s), int)
                        and not isinstance(fordeling.get(s), bool))
        terminal_antall = m.get("terminal_antall")
        if isinstance(terminal_antall, int) \
                and not isinstance(terminal_antall, bool) \
                and terminale != terminal_antall:
            feil.append(f"terminal_antall={terminal_antall} != summen av"
                        f" løst/avvist/manuell i status_fordeling ({terminale})")
    return feil


def _grenser_rollback(grense: dict, art: dict) -> list[str]:
    """`rollback-m01-v1` — grensene som har manglet siden PR-005c.

    Den bindende delen er ikke tidene, men `halvferdige_transaksjoner = 0`
    og at radtellingene for de ANDRE tabellene er uendret. En rollback som
    er rask og etterlater en halv transaksjon er verre enn en treg som
    ikke gjør det.
    """
    feil: list[str] = []
    m, k = art.get("maalt"), art.get("etterkontroll")
    if not isinstance(m, dict) or not isinstance(k, dict):
        return ["artefaktet mangler `maalt` og/eller `etterkontroll`"]

    for felt, tak in (("deaktivering_effektiv_s", grense["maks_deaktivering_s"]),
                      ("reaktivering_effektiv_s", grense["maks_reaktivering_s"])):
        verdi, melding = _positiv(m, felt, felt)
        if melding:
            feil.append(melding)
        elif verdi > tak:
            feil.append(f"{felt}={verdi:g}, krever <= {tak:g}")

    for felt, tak in (("tapte_loggposter", grense["maks_tapte_loggposter"]),
                      ("halvferdige_transaksjoner", grense["maks_halvferdige"])):
        verdi, melding = _teller(m, felt, felt)
        if melding:
            feil.append(melding)
        elif verdi > tak:
            feil.append(f"{felt}={verdi}, krever <= {tak}")

    # ANDELEN REGNES UT PÅ NYTT FRA RÅTALLENE (Codex P1, runde 6).
    #
    # Porten leste tidligere bare det ferdigregnede tallet. Da besto blant
    # annet disse umulige artefaktene:
    #   requests_under_rollback=113, avviste_requests=1,  andel=1.0
    #   requests_under_rollback=0,   avviste_requests=0,  andel=1.0
    # Begge er «>= 1.0» og ingen av dem kan oppstå i en ekte kjøring.
    # Samme prinsipp som `bestatt` fra PR #8: når råtallene ligger i
    # artefaktet, er produsentens konklusjon ikke beviset.
    oppsett = art.get("oppsett")
    n, f1 = _teller(oppsett, "oppsett.requests_under_rollback",
                    "requests_under_rollback")
    avvist, f2 = _teller(m, "avviste_requests", "avviste_requests")
    andel, f3 = _andel(m, "paagaaende_requests_korrekt_avvist",
                       "paagaaende_requests_korrekt_avvist")
    for melding in (f1, f2, f3):
        if melding:
            feil.append(melding)
    if not (f1 or f2 or f3):
        if n == 0:
            # 0/0 er ikke 1.0. En rollback uten en eneste forespørsel i
            # av-vinduet beviser ikke at forespørsler blir avvist — den
            # beviser at ingen ble prøvd. Samme regel som for
            # `reparerbare = 0` i feilinjiseringen.
            feil.append("requests_under_rollback=0 — en rollback uten"
                        " trafikk i av-vinduet beviser ingen avvisning")
        elif avvist > n:
            feil.append(f"avviste_requests={avvist} >"
                        f" requests_under_rollback={n} — flere avvisninger"
                        " enn forespørsler")
        else:
            # Toleransen er halvparten av siste siffer produsenten runder
            # til (6 desimaler). Eksakt likhet ville gjort 1/3 umulig å
            # rapportere; en større slingring ville gjort kontrollen
            # meningsløs.
            faktisk = avvist / n
            if abs(andel - faktisk) > 5e-7:
                feil.append(
                    f"paagaaende_requests_korrekt_avvist={andel:g} stemmer"
                    f" ikke med {avvist}/{n}={faktisk:.6f} — andelen er"
                    " regnet ut, ikke lest")
            elif faktisk < grense["krev_avvist_andel"]:
                feil.append(f"paagaaende_requests_korrekt_avvist={faktisk:g},"
                            f" krever >= {grense['krev_avvist_andel']:g}")

    # AVVISNINGSKODEN er en del av kontrakten, ikke en fritekstetikett.
    # Skjemaet låser den også (`const`), men gaten kontrollerer den selv:
    # `_sjekk_grenser` kalles også uten formatvalidering, og en kontrakt som
    # bare håndheves ett sted er håndhevet i ett tilfelle.
    if m.get("avvisningskode") != "modul_inaktiv":
        feil.append(f"avvisningskode={m.get('avvisningskode')!r} —"
                    " kontrakten er 503 `modul_inaktiv`, og et artefakt kan"
                    " ikke bevise den med en annen kode")

    if k.get("andre_tabeller_uendret") is not True:
        feil.append("etterkontroll: andre_tabeller_uendret er ikke true")

    # Flagget over er produsentens påstand. Tallene er beviset — og de
    # sammenlignes her, ikke bare oppgis.
    for_, etter = k.get("radtelling_for"), k.get("radtelling_etter")
    if not isinstance(for_, dict) or not isinstance(etter, dict):
        feil.append("etterkontroll mangler radtelling_for/radtelling_etter")
    elif sorted(for_) != sorted(etter):
        feil.append(f"radtellingene dekker ulike tabeller: {sorted(for_)}"
                    f" vs {sorted(etter)}")
    else:
        for tabell in sorted(for_):
            a, f1 = _teller(for_, f"radtelling_for.{tabell}", tabell)
            b, f2 = _teller(etter, f"radtelling_etter.{tabell}", tabell)
            if f1 or f2:
                feil += [x for x in (f1, f2) if x]
            elif a != b:
                feil.append(f"{tabell}: {a} rader før, {b} etter —"
                            " rollbacken rørte en tabell den ikke skulle")
    return feil


def _slaa_opp(art: object, sti: str):
    """Følg en punktseparert sti inn i artefaktet. -> (verdi, funnet).

    Bevisst enkel: bare oppslag i objekter. En sti som må indeksere lister
    eller gjøre betingede valg for å treffe, er ikke en peker til én måling
    — den er et lite program, og et program i et manifest kan ikke leses av
    den som skal etterprøve påstanden.
    """
    node = art
    for ledd in sti.split("."):
        if not isinstance(node, dict) or ledd not in node:
            return None, False
        node = node[ledd]
    return node, True


def _punktbinding(krav_id: str, punkt: dict, navn: str) -> list[str]:
    """Målingene må bevise NETTOPP dette punktet (#166, eiers valg A).

    `_bevismaalinger_finnes` under måler at en påberopt sti FINNES i
    artefaktet. Den sier ingenting om hva stien beviser — så et hvilket som
    helst gyldig modulartefakt kunne flippe et hvilket som helst punkt, så
    lenge det navnga en sti som tilfeldigvis fantes. Codex felte den seks
    ganger på M-57 alene, hver gang som et nytt «funn», hver gang samme rot.

    `KRAVGRENSER[krav_id]["punktbinding"]` navngir nå, PINNET FØR BYGGING
    (§0-formen), hvilke målinger som kan bevise hvert punkt. Et punkt som
    ikke står der er UFLIPPBART — og det er det tilsiktede utfallet, ikke en
    bivirkning: «et punkt uten definert, målbar grense regnes som nei» (§10),
    gjort mekanisk.

    ARBEIDSDELINGEN ER UENDRET, BARE FLYTTET (RUTINER §2): at målingen
    FINNES er maskinelt, at den er RELEVANT for modulen er reviewansvar.
    Bindingen her er derfor unionen av det som lovlig beviser punktet i det
    artefaktet — ikke ett sett per modul. Delt evidens er villet: `rollback-
    m01-v1` beviser `rollback_testet` for M-1 gjennom deaktiveringstiden og
    for M-2 gjennom tapte loggposter, i samme kjøring. Det maskinen nå
    hindrer, er at et punkt bevises av en måling som ikke beviser DET.
    """
    grense = KRAVGRENSER.get(krav_id)
    if grense is None:
        return []                       # ukjent krav_id felles av _sjekk_grenser
    # FRAVÆR ER IKKE FRITAK. Første utgave leste «ingen binding» som
    # «ingenting å håndheve», og da slapp nettopp de grensene igjennom som
    # ikke har navngitt noe — altså `m57-v1`, som er hele grunnen til at
    # issuet finnes. En port som er blind der den mangler, er ingen port.
    binding = grense.get("punktbinding") or {}
    lovlige = binding.get(navn) if isinstance(binding, dict) else None
    if not lovlige:
        return [f"{navn}: `{krav_id}` navngir ingen målinger som beviser"
                f" dette punktet — det er UFLIPPBART til bindingen finnes"
                f" (#166). Et punkt uten målbar grense regnes som nei (§10)."]
    # EN MALFORMET BINDING SLIPPER INGENTING GJENNOM (Codex, PR #234). Er
    # `lovlige` en bar streng, blir `s not in lovlige` en delstrengtest: en
    # binding skrevet `"maalt.teller"` i stedet for `("maalt.teller",)`
    # ville da autorisert `maalt` — en KORTERE sti som beviser noe annet.
    # Porten kan ikke stole på sin egen tabell uten å måle den: feil form
    # er ikke en svakere binding, det er ingen binding.
    if (isinstance(lovlige, (str, bytes))
            or not isinstance(lovlige, (list, tuple))
            or not all(isinstance(s, str) and s for s in lovlige)):
        return [f"{navn}: `{krav_id}`s binding for dette punktet har feil"
                f" form ({lovlige!r}) — den må være en liste eller tuple av"
                f" ikke-tomme målestier. Punktet er UFLIPPBART til"
                f" bindingen er skrevet riktig (#166)."]
    oppgitt = punkt.get("bevismaalinger")
    if not isinstance(oppgitt, list):
        return []                       # formen felles av _bevismaalinger_finnes
    utenfor = [s for s in oppgitt if s not in lovlige]
    if utenfor:
        return [f"{navn}: bevismaaling(er) {utenfor} står ikke i"
                f" `{krav_id}`s binding for dette punktet — en sti som"
                f" finnes i artefaktet er ikke det samme som en måling som"
                f" beviser DETTE punktet. Lovlige: {sorted(lovlige)}"]
    return []


def _bevismaalinger_finnes(art: dict, punkt: dict, navn: str) -> list[str]:
    """Hver oppgitte måling må FINNES i artefaktet (Codex P1, PR #15).

    Delingsregelen i RUTINER.md krever at et manifest navngir hvilken måling
    i et delt artefakt som beviser punktet for nettopp den modulen. Den ble
    først håndhevet som «notatet er ikke tomt og ikke identisk med naboens»
    — og det består av `notat: "banan_maaling = true"`. Ikke-tom og unik
    fritekst er ingen binding til data.

    Dette beviser ikke at målingen er RELEVANT for modulen; det er
    reviewansvar. Det beviser at den påberopte målingen finnes i evidensen,
    og det er minstekravet en maskin kan og skal holde.
    """
    stier = punkt.get("bevismaalinger")
    if not isinstance(stier, list) or not stier:
        return [f"{navn}: peker på et artefakt uten å navngi hvilken måling"
                f" som beviser punktet (`bevismaalinger`)"]
    feil = []
    for sti in stier:
        if not isinstance(sti, str):
            feil.append(f"{navn}: bevismaaling {sti!r} er ikke en streng")
            continue
        _, funnet = _slaa_opp(art, sti)
        if not funnet:
            feil.append(f"{navn}: bevismaaling '{sti}' finnes ikke i"
                        f" artefaktet — en påstand om en måling som ikke er"
                        f" der, er ikke evidens")
    return feil


def valider_artefakter(manifest: dict, rot: Path | None = None) -> list[str]:
    """Håndhever evidenskjeden for hvert `ja` med krav_id. Tom liste == ok.

    Codex' P1 på PR #8: skjemaet krevde bare at `artefakt` var en ikke-tom
    STRENG. `artefakt: tull.json` passerte da like fint som en ekte måling,
    og hashen alene beviser bare at noen kjenner en streng. Her åpnes filen
    faktisk, hashen verifiseres mot innholdet, formatet valideres og
    tallene måles mot KRAVGRENSER.
    """
    rot = Path(rot) if rot is not None else REPOROT
    sjekkliste = (manifest or {}).get("staging_sjekkliste") or {}
    feil: list[str] = []
    for navn, p in sorted(sjekkliste.items()):
        if not isinstance(p, dict) or p.get("status") != "ja":
            continue
        krav_id = p.get("krav_id")
        if not krav_id:
            continue                      # ja uten krav_id krever ikke artefakt
        sti_tekst = p.get("artefakt")
        forventet = p.get("artefakt_sha256")
        if not sti_tekst or not forventet:
            feil.append(f"{navn}: ja med krav_id mangler artefakt/artefakt_sha256")
            continue
        sti = (rot / sti_tekst).resolve()
        try:
            sti.relative_to(rot.resolve())
        except ValueError:
            feil.append(f"{navn}: artefaktstien peker utenfor repoet")
            continue
        data, sha, melding = _les_artefakt(sti)
        if melding:
            feil.append(f"{navn}: {melding} ({sti_tekst})")
            continue
        if sha != forventet:
            feil.append(f"{navn}: sha256 stemmer ikke — manifestet sier "
                        f"{forventet[:12]}…, filen er {sha[:12]}…")
            continue
        # BEGGE lag kjører, alltid — formatet stopper ikke måletallene.
        #
        # Første utkast gjorde `continue` ved formatfeil. Det så ryddig ut,
        # men maskerte domenekontrollene: en negativ varighet bryter både
        # skjemaet (`exclusiveMinimum: 0`) og `_positiv`, og med et tidlig
        # avbrudd var det bare skjemaet som ble prøvd. Svekkes skjemaet
        # senere, ville domenetestene fortsatt vært grønne uten å ha kjørt.
        # To uavhengige lag er bare uavhengige hvis begge faktisk måles.
        feil += [f"{navn}: format — {m}"
                 for m in valider_artefaktformat(data, krav_id)]
        feil += [f"{navn}: {m}" for m in _sjekk_grenser(krav_id, data)]
        # TREDJE LAG: hvilken måling manifestet PÅBEROPER SEG (PR #15).
        #
        # De to over spør om artefaktet er gyldig og består grensene — det
        # samme svaret for alle moduler som deler filen. Dette laget spør
        # hva NETTOPP DETTE manifestet henter ut av den, og det er den
        # eneste kontrollen som skiller legitim deling fra en lånt
        # konklusjon. Kjøres etter hashkontrollen, så stien slås opp i en
        # fil vi har bevist er den manifestet mener.
        feil += _bevismaalinger_finnes(data, p, navn)
        feil += _punktbinding(krav_id, p, navn)
    return feil


def uavklarte_punkter(manifest: dict) -> list[str]:
    """Sjekklistepunkter som IKKE er `ja`.

    Regelen som aldri fravikes (RUTINER pkt. 2): en modul settes ikke til
    `aktiv` før alle punkter er ja. Funksjonen gjør regelen målbar i stedet
    for å be noen huske den.
    """
    sjekkliste = (manifest or {}).get("staging_sjekkliste") or {}
    return sorted(navn for navn, p in sjekkliste.items()
                  if not isinstance(p, dict) or p.get("status") != "ja")


def aktiv_uten_bevis(manifest: dict) -> list[str]:
    """Tom liste med mindre modulen er `aktiv` OG har uavklarte punkter."""
    if (manifest or {}).get("status") != "aktiv":
        return []
    return uavklarte_punkter(manifest)
