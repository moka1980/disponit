#!/usr/bin/env python3
"""Eneste vei inn for migrasjoner på en server: den herdede kjøreren.

Codex' P1 i PR-005a-reviewen: oppsett-skriptet og CI kjørte
migrasjonsfilene med `psql -f`. Det ser uskyldig ut — filene er
idempotente — men det omgår alt kjøreren finnes for: advisory-låsen
(to samtidige oppsett kan kjøre samme migrasjon), transaksjonen kjøreren
eier for versjon >= 3 (delvis anvendt migrasjon ved feil midtveis),
checksum-registreringen (historikken blir ikke immutable) og avvisningen
av endret historikk.

En herdet kjører som kan omgås av oppsettet sitt eget skript, er ikke en
kjører — den er en anbefaling.

Rettighetene til runtime settes her, ETTER migrasjonene, fordi en GRANT på
en tabell som ikke finnes ennå er stille virkningsløs. Migrator eier
tabellene og kan derfor dele ut rettigheter selv; superbruker trengs ikke.

BRUK:  DISPONIT_MIGRATOR_URL=... python3 deploy/staging/migrer.py [runtime-rolle]
"""
import importlib.util
import os
import sys
from pathlib import Path

ROT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROT / "platform/core"))

# Runtime får nøyaktig det den trenger, aldri mer:
#   unntak_historikk  INSERT-only — historikken skal aldri kunne endres
#   policyer          lesetilgang — policyer endres av en egen vei
#   revisjonslogg     INSERT+SELECT — append-only håndheves i tillegg av trigger
#: `REVOKE ALL ON ALL TABLES IN SCHEMA public` var riktig helt til PR-006:
#: `arbeidskapabiliteter` eies av `disponit_m37_claimer`, og migrator
#: verken eier den eller arver eierrollens rettigheter (`WITH INHERIT
#: FALSE`). Da feiler hele rettighetsblokken på «permission denied» — altså
#: setter deployet INGEN rettigheter, etter å ha kjørt alle migrasjonene.
#:
#: Nullstillingen gjelder derfor tabellene migrator FAKTISK eier. Det er
#: ikke en innsnevring av kontrollen: tabeller migrator ikke eier, kan den
#: uansett ikke ha gitt bort.
NULLSTILL_TABELLER = """
DO $$
DECLARE t regclass;
BEGIN
    FOR t IN SELECT c.oid::regclass
               FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
                AND c.relowner = (SELECT r.oid FROM pg_roles r
                                   WHERE r.rolname = current_user)
    LOOP
        EXECUTE format('REVOKE ALL ON %s FROM %I', t, '{rolle}');
    END LOOP;
END $$;
"""

RETTIGHETER = """
GRANT USAGE ON SCHEMA public TO {rolle};
GRANT SELECT, INSERT ON revisjonslogg, frekvens_hendelser TO {rolle};
GRANT SELECT ON migrasjoner TO {rolle};
GRANT SELECT, INSERT ON unntak_historikk, attestasjon_jti TO {rolle};
GRANT SELECT, INSERT, UPDATE ON unntak, idempotens TO {rolle};
GRANT SELECT, INSERT, UPDATE ON tenant_nokler TO {rolle};
GRANT SELECT ON policyer TO {rolle};
-- #189: stillingsprofilen — leseflaten går rett på tabellene (RLS-gated);
-- all skriving eies av domene_eier-døren. Grantes HER (migrator eier
-- tabellene): inne i en SET LOCAL ROLE-blokk ville GRANT-en feilet på
-- manglende grant-rett og rullet hele blokken.
GRANT SELECT ON stillingsprofil, stillingsprofil_krav TO {rolle};
-- 079 (#160): kundeeid utsendingstekst — runtime leser, dørene skriver.
GRANT SELECT ON utsendingstekst TO {rolle};
-- 074: slett = enveis skjuling gjennom døren — aldri rå UPDATE.
GRANT EXECUTE ON FUNCTION skjul_stillingsprofil(TEXT, UUID) TO {rolle};
-- PR-013: policyadministrasjon. Runtime LESER hodet/utkast/runder og SKRIVER
-- utkast/runder/attestasjoner direkte (RLS-gated), men når ALDRI `policyer`
-- eller `policy_hode`-pekeren — aktivering går kun via den herdede
-- `aktiver_policy` (EXECUTE gitt i migrasjon 013).
GRANT SELECT ON policy_hode TO {rolle};
GRANT SELECT, INSERT, UPDATE ON policyutkast, aktiveringsattestasjon TO {rolle};
-- 047: runtime oppdaterer KUN rundens status (utlopt/kansellert i
-- policyadmin.py) — versjonsbindingen `aktivert_som_versjon` er
-- hendelsens og settes bare av aktiver_policy (eier-definer). Et
-- tabellnivå-UPDATE her ville lagt kolonnen åpen igjen ved hvert deploy.
GRANT SELECT, INSERT ON aktiveringsrunde TO {rolle};
GRANT UPDATE (status) ON aktiveringsrunde TO {rolle};
-- 057: M-57s kandidatlagre. Runtime leser og skriver gjennom API-veien
-- (RLS-gated). INGEN UPDATE — eneste lovlige mutasjon er reap-overgangen,
-- og den bor i `reap_kandidatdata` (definer). INGEN DELETE noensinne:
-- kandidatrader reapes (payload til NULL), de slettes aldri som rader.
-- ANKERET får KUN SELECT (Codex P1): et INSERT på `rekrutteringsprosess`
-- er en vei utenom `opprett_rekrutteringsprosess`, som er den eneste
-- veien som binder oppdraget, eiermodulen og fristen sammen ved
-- fødselen. Radvakten har siden fått en egen INSERT-gren (Cursor P2), og
-- kommentaren her sa fortsatt «BEFORE UPDATE OR DELETE» — misvisende for
-- drift (Cursor P3). De to lagene står SAMMEN og med vilje: vakten
-- gjelder enhver rolle, også claimeren som må ha INSERT for å være
-- definer, mens denne rettigheten er den som holder runtime helt utenfor.
-- EXECUTE på de to prosessfunksjonene ligger i M37_RETTIGHETER_API.
GRANT SELECT ON rekrutteringsprosess TO {rolle};
-- 075 (#157): ankeret leses av runtime; fødselen er dørens.
GRANT SELECT ON kandidat TO {rolle};
GRANT SELECT, INSERT ON kandidat_originaldokument,
    kandidat_parsettekst, kandidat_evalueringsartefakt,
    kandidat_intervjusporsmal, kandidat_utsendingsdata,
    kandidat_avmaskering TO {rolle};
-- 058: inndata-artefaktet — runtime leser metadata (RLS-gated);
-- skrivingene går KUN gjennom domene_eier-dørene (EXECUTE i 058).
GRANT SELECT ON inndata_artefakt TO {rolle};
-- 082 (M-8): kundens tidsvalg-leseflate går rett på tabellene
-- (RLS-gated); all skriving eies av definer-dørene. `m8_tidsvalgtoken`
-- står bevisst IKKE her (api_tokener-formen): runtime når kapabiliteten
-- KUN gjennom de authenticator-eide dørene under.
GRANT SELECT ON m8_slot, m8_slotvalg TO {rolle};
-- 089 (M-35): kontinuitetsflaten leser registeret og tidslinjen rett
-- på tabellene (RLS-gated); ALL skriving eies av de claimer-eide
-- definer-dørene (EXECUTE i M37_RETTIGHETER_API under).
GRANT SELECT ON kontinuitet_tjeneste, beredskapskontakt,
    kontinuitetshendelse, kontinuitetshendelse_post TO {rolle};
-- 094 (M-5): malregisteret leses rett på tabellene (RLS-gated) — flaten
-- lister familier, versjoner, komponenter og feltdeklarasjoner. ALL
-- skriving eies av de mal_eier-eide definer-dørene (EXECUTE i den egne
-- blokken lenger nede), og verken INSERT, UPDATE eller DELETE gis noe
-- sted her: en publisert mal endres aldri, den etterfølges.
GRANT SELECT ON malfamilie, malversjon, malkomponent, malfelt TO {rolle};
-- De to offentlige tidsvalg-dørene er authenticator-eide og grantes som
-- verifiser_token: migrator arver authenticator (fullt INHERIT, i
-- motsetning til claimer-medlemskapet), så granten gis direkte her.
GRANT EXECUTE ON FUNCTION m8_tidsvalg_oppslag(TEXT, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION m8_velg_slot(TEXT, TEXT, UUID) TO {rolle};
-- 090 (M-10) / 091 (M-11): driftstatusens LESEDØRER. Tabellene
-- `backupverifisering`, `selvtest_kjoring` og `selvtest_probe` står
-- bevisst IKKE i noen GRANT-liste her: runtime har ingen SELECT på dem i
-- det hele tatt (SP-7), og når dem KUN gjennom de to definer-dørene, som
-- krever tenantkontekst først (051-formen). Migrasjonene navngir ikke
-- runtime-rollen (057-lærdommen); DENNE blokken er eneste rettighetskilde
-- for den konfigurerte rollen.
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION backup_status(TEXT, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION selvtest_status(TEXT, INT) TO {rolle};
-- 092 (M-3): datakvalitetens LESEDØRER. De fire tabellene
-- (`kvalitetsregel`, `kvalitetskjoring`, `kvalitetsprofil`,
-- `kvalitetsfunn`) står bevisst IKKE i noen GRANT-liste her: runtime har
-- ingen SELECT på dem i det hele tatt (SP-7), og når dem KUN gjennom
-- dørene, som krever tenantkontekst først (051-formen). Dørene eies av
-- `disponit_kvalitet_eier`, så granten gis under DENS rolle.
--
-- `m3_kvalitetsfunn_tverrgaaende` er PLATTFORMDRIFTENS dør og står her
-- fordi endepunktet `/v1/datakvalitet` gater den på `platform:admin`
-- (utrullings-presedensen) — mekanismen er basens, autorisasjonen er
-- endepunktets. `m3_profiler` står IKKE her og skal aldri gjøre det:
-- profileringen er timerens, med sin egen rolle, og et web-API som kunne
-- utløse en profilering kunne også skrive en grønn profil over en rød
-- base.
SET LOCAL ROLE disponit_kvalitet_eier;
GRANT EXECUTE ON FUNCTION m3_regelregister(TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION m3_kvalitetsprofil(TEXT, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION m3_kvalitetsfunn(TEXT, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION m3_kvalitetsfunn_tverrgaaende(TEXT, INT) TO {rolle};
REVOKE ALL ON FUNCTION m3_profiler(INT) FROM {rolle};
REVOKE ALL ON FUNCTION m3_reis_funn(TEXT, TEXT, TEXT, UUID, JSONB) FROM {rolle};
RESET ROLE;
-- 095 (M-9): begrepsregisterets TO LESEDØRER. `begrep` og `begrepsfunn`
-- står bevisst IKKE i noen GRANT-liste her: runtime har ingen SELECT på
-- dem i det hele tatt (SP-7), og når dem KUN gjennom disse to dørene,
-- som krever tenantkontekst først. Isolasjonen inne i dørene er RLS
-- sin, ikke et WHERE-ledd — se hodekommentaren i 095.
--
-- SKRIVEDØRENE (`m9_registrer_begrep`, `m9_ny_begrepsversjon`) og
-- SVEIPEN står med vilje ikke her, og REVOKEs under: v1 har INGEN
-- HTTP-mutasjon (M-31-formen, 086), og sveipen er kryss-tenant og
-- setter selv RLS-konteksten — et grant der ville gitt forespørsels-
-- veien nøyaktig det vinduet `disponit_kunnskapssveip` finnes for å
-- nekte den. En rettighet som bare slutter å bli gitt er ikke trukket
-- tilbake (035), derfor REVOKE og ikke bare fravær.
SET LOCAL ROLE disponit_kunnskap_eier;
GRANT EXECUTE ON FUNCTION m9_sok(TEXT, TEXT, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION m9_apne_funn(TEXT, INT) TO {rolle};
REVOKE ALL ON FUNCTION m9_registrer_begrep(
    TEXT, TEXT, TEXT, TEXT, TEXT, DATE, TEXT, UUID) FROM {rolle};
REVOKE ALL ON FUNCTION m9_ny_begrepsversjon(
    TEXT, TEXT, TEXT, TEXT, TEXT, DATE, TEXT, UUID) FROM {rolle};
REVOKE ALL ON FUNCTION m9_sveip_utlopte(INT) FROM {rolle};
RESET ROLE;
SET LOCAL ROLE disponit_m37_claimer;
-- SKRIVEDØRENE OG SVEIPENE ER ANDRE ROLLERS, og en rettighet som bare
-- slutter å bli gitt er ikke trukket tilbake (035). Skrivedørene hører
-- lesejobbene til (`disponit_driftstatus`/`disponit_selvtest`): et
-- kompromittert web-API skal ikke kunne dikte en verifisering det ikke
-- har gjort, eller en grønn selvtestrunde over en rød plattform.
-- Sveipene hører senderen til: de tar tenanten som parameter og setter
-- DENS RLS-kontekst, så et grant her ville gitt forespørselsveien
-- nøyaktig det kryss-tenant-vinduet senderrollen finnes for å nekte den.
REVOKE ALL ON FUNCTION registrer_backupverifisering(
    TIMESTAMPTZ, TIMESTAMPTZ, NUMERIC, INT, BIGINT) FROM {rolle};
REVOKE ALL ON FUNCTION registrer_selvtest(UUID, JSONB, TEXT) FROM {rolle};
REVOKE ALL ON FUNCTION varsle_backupverifisering_uteblitt(TEXT) FROM {rolle};
REVOKE ALL ON FUNCTION varsle_selvtest_uteblitt(TEXT) FROM {rolle};
RESET ROLE;
-- 093 (M-4): retensjonsflatens LESEDØRER. Samme form og samme grunn som
-- 090/091 over: `retensjonslager`, `retensjonsmaaling`,
-- `retensjonsstorrelse`, `retensjonsbeholdning` og `retensjonsfunn` står
-- bevisst IKKE i noen GRANT-liste her — runtime har ingen SELECT på dem i
-- det hele tatt, og når dem KUN gjennom definer-dørene, som krever
-- tenantkontekst først.
--
-- MÅLEDØREN ER MÅLERENS. `m4_mal_lagre` er kryss-tenant (utvalget er
-- predikatet), og et grant her ville gitt forespørselsveien nøyaktig det
-- vinduet `disponit_lagermaaler` finnes for å innelukke. Den REVOKEs, og
-- en rettighet som bare slutter å bli gitt er ikke trukket tilbake (035).
SET LOCAL ROLE disponit_lager_eier;
GRANT EXECUTE ON FUNCTION m4_siste_maaling(TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION m4_retensjonsbilde(TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION m4_retensjonskatalog(TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION m4_retensjonsfunn(TEXT) TO {rolle};
REVOKE ALL ON FUNCTION m4_mal_lagre(INT, BOOLEAN) FROM {rolle};
REVOKE ALL ON FUNCTION m4_reap_egne_maalinger(INT) FROM {rolle};
-- 096 (M-21): pliktregisterets LESEDØR, samme snitt og samme
-- begrunnelse som de to over. `plikt`, `pliktvarsling` og
-- `pliktvarsel_sendt` står bevisst IKKE i noen GRANT-liste her: runtime
-- har ingen SELECT på dem i det hele tatt (SP-7) og når registeret KUN
-- gjennom dørene, som krever tenantkontekst først. Skrivedørene ligger
-- i M37_RETTIGHETER_API (menneskelige handlinger i flaten); SVEIPEN
-- hører senderen til (VARSLER_RETTIGHETER) og er kryss-tenant — et
-- grant her ville gitt forespørselsveien nøyaktig det vinduet
-- senderrollen finnes for å nekte den.
SET LOCAL ROLE disponit_plikt_eier;
GRANT EXECUTE ON FUNCTION m21_plikter(TEXT, INT) TO {rolle};
REVOKE ALL ON FUNCTION m21_koe_fristvarsler(INT) FROM {rolle};
RESET ROLE;
-- 097 (M-12): tilgangsregisterets TO LESEDØRER — tilgangsbildet og den
-- åpne funnlisten. Samme snitt og samme begrunnelse som de over:
-- `tilgangsobjekt`, `tilgang` og `tilgangsfunn` står bevisst IKKE i noen
-- GRANT-liste her — runtime har ingen SELECT på dem i det hele tatt
-- (SP-7) og når registeret KUN gjennom dørene, som krever
-- tenantkontekst først.
--
-- SVEIPEN REVOKEs, og det er en sikkerhetsdom og ikke en formalitet:
-- `m12_sveip_gjennomganger` er kryss-tenant og setter selv
-- RLS-konteksten per tenant — altså nøyaktig det vinduet
-- `disponit_tilgangssveip` finnes for å nekte forespørselsveien. Samme
-- snitt som 038-reaperen. Per-tenant-armen REVOKEs sammen med den: en
-- rolle som kunne kalle `m12_sveip_for_tenant` direkte ville kunnet
-- reise og lukke funn på kommando, utenom den ene inngangen som er
-- tenkt.
SET LOCAL ROLE disponit_tilgang_eier;
GRANT EXECUTE ON FUNCTION m12_tilgangsbilde(TEXT, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION m12_objekter(TEXT, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION m12_apne_funn(TEXT, INT) TO {rolle};
REVOKE ALL ON FUNCTION m12_sveip_gjennomganger(INT) FROM {rolle};
REVOKE ALL ON FUNCTION m12_sveip_for_tenant(TEXT, INT) FROM {rolle};
RESET ROLE;
-- 088 (M-6): e-postlagrene. Runtime LESER (RLS-gated) — payloaden er
-- tenant-DEK-kryptert, så et direkte SELECT gir bare ciphertext (M-6
-- port 2 måler det). KUN SELECT i PR-A: innhenterens skrivevei kommer
-- med M-6 PR-C og får sine INSERT-grants der; den eneste mutasjonen er
-- reap-overgangen, og den bor i `reap_epostdata` (kryss-tenant,
-- betinget blokk i 088 — timerrollens, aldri en parameterisert rolles).
-- KILDEN får KOLONNEGRANT (CodeRabbit på PR-A): web-API-rollen har
-- ingenting å gjøre med credential-trioen (auth_kryptert/nonce/key_id)
-- eller leverandørcursoren — flaten lister postbokser og status, og
-- innhenterens credential-lesing (PR-C) er sin egen vei med sin egen
-- rolleavgjørelse. Et kompromittert web-API skal ikke engang kunne
-- eksfiltrere ciphertext.
GRANT SELECT (tenant, kilde_id, leverandor, postboks, status,
    sist_hentet_ts, opprettet) ON epost_kilde TO {rolle};
-- M-6 PR-B: kildens skrivevei FØDES her — OAuth-callbacken skriver
-- credential-trioen (kryptert i API-laget FØR den når basen), rekobling
-- av samme postboks roterer den, og deaktivering flipper status.
-- KOLONNEGRANT som leseflaten over: fortsatt INGEN SELECT på
-- credential-trioen (callbacken SETTER den med parametre, aldri
-- EXCLUDED — et upsert-uttrykk som LESER kolonnen ville krevd SELECT),
-- og ingen DELETE — `deaktivert` er avviklingsformen (088-vakten).
-- Innhenterens lesing av trioen (PR-C) er sin egen vei med sin egen
-- rolleavgjørelse; web-API-rollen kan skrive ciphertext den aldri får
-- lese tilbake.
GRANT INSERT (tenant, leverandor, postboks, auth_kryptert, nonce, key_id)
    ON epost_kilde TO {rolle};
GRANT UPDATE (auth_kryptert, nonce, key_id, status)
    ON epost_kilde TO {rolle};
GRANT SELECT ON epost_melding, epost_klassifisering,
    epost_utkast, epost_oppfolging, epost_vedlegg TO {rolle};
-- Varsler: flaten leser og merker som lest; tjenesten oppretter. Senderen
-- oppdaterer e-poststatus. Ingen DELETE — rydding er en driftsoppgave med
-- egen rolle, ikke noe forespørselsveien skal kunne gjøre.
GRANT SELECT, INSERT, UPDATE ON varsel TO {rolle};
-- Senderfunksjonene er BEVISST utelatt her (Codex P1): de er kryss-tenant,
-- og web-API-rollen skal ikke kunne enumerere andre tenanters varsler om
-- forespørselsveien kompromitteres. De tilhører `disponit_varselsender` alene —
-- se VARSLER_RETTIGHETER. REVOKE fordi eldre kjøringer av dette skriptet
-- faktisk ga dem: en grant som bare slutter å bli GITT er ikke trukket
-- tilbake.
SET LOCAL ROLE disponit_domene_eier;
REVOKE ALL ON FUNCTION varsel_klaim_epost(int, int) FROM PUBLIC;
REVOKE ALL ON FUNCTION varsel_sett_epoststatus(bigint, uuid, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION varsel_rekoe(interval, int, interval) FROM PUBLIC;
REVOKE ALL ON FUNCTION varsel_klaim_epost(int, int) FROM {rolle};
REVOKE ALL ON FUNCTION varsel_sett_epoststatus(bigint, uuid, text, text) FROM {rolle};
REVOKE ALL ON FUNCTION varsel_rekoe(interval, int, interval) FROM {rolle};
RESET ROLE;
-- 035: familiehorisont-sveipen er senderens pre-pass og hører til samme
-- grense — den tar tenanten som parameter og setter DENS RLS-kontekst, så
-- et grant her ville gitt forespørselsveien et kryss-tenant-vindu.
SET LOCAL ROLE disponit_modul_eier;
REVOKE ALL ON FUNCTION varsle_tokenfamilie_utlop(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION varsle_tokenfamilie_utlop(text) FROM {rolle};
RESET ROLE;
-- 056: utsendingsveien er SAMME KLASSE, og verre — den er IRREVERSIBEL
-- (Cursor P2 på #140). `frigi_utsendelse` og `opprett_frigivelsesoppdrag`
-- gis KUN til `disponit_varselsender` (VARSLER_RETTIGHETER); web-API-rollen
-- skal aldri kunne frigi en signert liste. Læren fra varsel-funnet over
-- gjelder ordrett: en grant som bare slutter å bli GITT er ikke trukket
-- tilbake — en tidligere kjøring, eller en manuell grant, ville overlevd
-- alle senere migreringer i stillhet.
SET LOCAL ROLE disponit_m37_claimer;
REVOKE ALL ON FUNCTION frigi_utsendelse(TEXT, UUID, TEXT) FROM {rolle};
REVOKE ALL ON FUNCTION opprett_frigivelsesoppdrag(TEXT, UUID, TEXT, TEXT, TEXT, BYTEA, TEXT, BYTEA, TIMESTAMPTZ, TIMESTAMPTZ) FROM {rolle};
REVOKE ALL ON FUNCTION m57_neste_sendinger(TEXT, INT, INT) FROM {rolle};
REVOKE ALL ON FUNCTION m57_start_sending(TEXT, UUID, UUID, UUID, INT) FROM {rolle};
REVOKE ALL ON FUNCTION m57_fullfor_sending(TEXT, UUID, UUID, UUID, TEXT, TEXT) FROM {rolle};
RESET ROLE;
-- 081: kryss-tenant-dørene eies av domene-eieren (027-formen) — REVOKE
-- må gis av eieren, i eierens egen blokk.
SET LOCAL ROLE disponit_domene_eier;
REVOKE ALL ON FUNCTION m57_sendeklare_tenanter(INT, INT) FROM {rolle};
REVOKE ALL ON FUNCTION m57_merk_uviss(INTERVAL) FROM {rolle};
RESET ROLE;
GRANT SELECT, INSERT, UPDATE ON varselvalg TO {rolle};
-- PR-014a: modulregisteret. Runtime LESER det (default-deny, GRANT-modell §4) —
-- INGEN INSERT/UPDATE/DELETE på registertabellene. Alle skriv går via de herdede
-- overgangsfunksjonene (CP2), som `aktiver_policy`. En direkte skriving fra
-- runtime skal gi `permission denied` (Codex-port 17).
GRANT SELECT ON modulkontrakt, modulhode, modulrelease, moduldeployment,
    oppdragstype_register, modulregister_hendelse TO {rolle};
-- 049: akseptflaten. Codex' P1 på PR #117 (runde 14): dette var ett
-- ufiltrert `GRANT SELECT` på `moduldrill`, `modulaksept` og
-- `modulaksept_punkt` — evidenstabeller som bærer tenantidentifikatorer,
-- oppdrags-IDer, artefakt-UUIDer, aktører og evidensreferanser, og som
-- den gang sto UTEN RLS. Nabotabellen `artefakt` er tenant-filtrert;
-- disse var det ikke, så en kjøretidsrolle utenfor sin egen
-- tenantkontekst — eller en kompromittert sådan — leste hver eneste
-- tenants driftsbevis. Fullmakten er trukket tilbake (nullstillingen
-- over REVOKEr den også på baser som fikk den av en tidligere kjøring),
-- og 049 setter tenantpolicyene på radene i tillegg.
--
-- Det statusetiketten faktisk trenger — at (modul, miljø, release) er
-- akseptert mot et krav, når, og hvilken drill den hviler på — står i
-- den sanerte visningen `modulaksept_status`, som ikke bærer en eneste
-- tenantidentifikator. Bevisradene leses av eier- og driftsveien.
GRANT SELECT ON modulaksept_status, akseptkrav_punkt TO {rolle};
GRANT SELECT ON domenekontroll, artefakt, artefakttype_register TO {rolle};
-- 072 (BESLUTNING-168): skjemaoppslaget går via gjeldende versjon —
-- runtime leser versjonstabellen, skriver den aldri.
GRANT SELECT ON artefakttype_versjon TO {rolle};
-- 086 (M-31): modellstyringsregisteret. Runtime LESER det (model card
-- er en avledet leseflate) — ALL skriving går via de herdede dørene
-- (registrer_golden_sett/sett_evalueringskrav/registrer_evalueringskjoring),
-- som for 014-registeret over.
GRANT SELECT ON golden_sett, evalueringskrav, evalueringskjoring,
    modellstyring_hendelse TO {rolle};
-- PR-014c: skjemavalidering ved opplasting/promotering og aktiveringsporten
-- for `ekstern_lesing` leses i API-prosessen. Runtime skriver aldri.
GRANT SELECT ON artefaktskjema, malautorisasjonsvilkar TO {rolle};
-- 038 §6.1: idempotensregisteret for bestillinger. Kun SELECT+INSERT
-- — radene er immutable (trigger avviser UPDATE og DELETE), og et
-- grant her ville bare skjult at triggeren er porten.
GRANT SELECT, INSERT ON bestilling_idempotens TO {rolle};
-- 017/035: artefaktkapabiliteten. Funksjonene eies av `disponit_domene_eier`
-- (SECURITY DEFINER-veien inn i kapabilitetstabellen), så grantene MÅ gis
-- som eieren — som migrator blir de en stille WARNING, samme felle som
-- M37_RETTIGHETER under. 035 gir begge et haleargument for deploymenten og
-- DROPper de gamle formene; signaturene her må derfor følge 035 eksakt.
SET LOCAL ROLE disponit_domene_eier;
GRANT EXECUTE ON FUNCTION utsted_artefaktkapabilitet(TEXT, BIGINT, TEXT, TEXT, INT, TEXT, BIGINT, TEXT, TEXT, INT, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION innlos_artefaktkapabilitet(TEXT, TEXT, TEXT, TEXT) TO {rolle};
-- 043: valideringen UTEN bevaring — den sene kvitteringsveien må kunne
-- avvise et fremmed/feil-hashet artefakt også når artefaktet IKKE skal
-- bevares (`direkte`-reversibilitet: resultatet forkastes, artefaktet
-- ryddes). Samme eier som resten av artefaktveien, derfor samme blokk.
GRANT EXECUTE ON FUNCTION verifiser_artefaktbinding(UUID, TEXT, BIGINT, TEXT) TO {rolle};
-- 058: inndata-artefaktet (#162) — samme eier, derfor samme blokk. Codex P1:
-- migrasjonen gir EXECUTE til `disponit` HARDKODET, som 017 gjør. På en
-- installasjon som kaller dette skriptet med en annen runtime-rolle sto den
-- rollen dermed uten EXECUTE — SELECT-en over var alt den fikk — og både
-- reservasjonen og opplastingen svarte `permission denied`. Kjøreren er
-- autoritativ for runtimerollens rettigheter (Cursor P1 på #140); en
-- migrasjons grants overlever heller ikke en gjenoppbygging av skjemaet
-- uten radene her. `bind_inndata` hører med: bestillingsveien kaller den i
-- sin egen transaksjon, altså som runtimerollen.
GRANT EXECUTE ON FUNCTION reserver_inndata(TEXT, TEXT, TEXT, BIGINT, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION registrer_inndata_lastet(TEXT, TEXT, BIGINT, TEXT, TEXT, BYTEA, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION bind_inndata(TEXT, UUID, BIGINT, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION opprett_stillingsprofil_versjon(TEXT, UUID, TEXT, TEXT, JSONB, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION opprett_utsendingstekst_versjon(TEXT, UUID, TEXT, TEXT, TEXT, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION skjul_utsendingstekst(TEXT, UUID) TO {rolle};
GRANT EXECUTE ON FUNCTION hent_inndata_for_oppdrag(BIGINT, TEXT, TEXT, TEXT, TEXT) TO {rolle};
RESET ROLE;
-- 094 (M-5): malregisterets fem dører, eid av `disponit_mal_eier` — så
-- grantene MÅ gis som eieren (samme felle som domene_eier-blokken over:
-- som migrator blir de en stille WARNING og ingen rettighet).
--
-- `m5_fyll_mal` står her SELV OM DEN IKKE SKRIVER, og det er poenget:
-- utfyllingen er erklært STABLE, så PostgreSQL avviser enhver skriving i
-- kroppen. Runtime får altså EXECUTE på en funksjon som per definisjon
-- ikke kan lagre et dokument — v1-dommen håndhevet av basen, ikke av at
-- ingen har skrevet en INSERT ennå.
SET LOCAL ROLE disponit_mal_eier;
GRANT EXECUTE ON FUNCTION m5_opprett_malfamilie(TEXT, TEXT, TEXT, TEXT, UUID) TO {rolle};
GRANT EXECUTE ON FUNCTION m5_opprett_malversjon(TEXT, UUID, JSONB, JSONB, TEXT, UUID) TO {rolle};
GRANT EXECUTE ON FUNCTION m5_publiser_malversjon(TEXT, UUID, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION m5_trekk_tilbake_malversjon(TEXT, UUID, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION m5_fyll_mal(TEXT, UUID, JSONB) TO {rolle};
RESET ROLE;
-- 035: modul-onboarding og modultokener. Hele denne veien er
-- SECURITY DEFINER-funksjoner eid av `disponit_modul_eier`; runtime har
-- verken lesing eller skriving på tabellene bak dem. `verifiser_modultoken`
-- er selve autentiseringen av et modultoken, så uten EXECUTE her svarer
-- API-et `permission denied` på hver eneste modulforespørsel.
SET LOCAL ROLE disponit_modul_eier;
GRANT EXECUTE ON FUNCTION utsted_onboarding_hemmelighet(TEXT, TEXT, TEXT, UUID, TEXT, INT, INT, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION innlos_onboarding(UUID, TEXT, UUID, TEXT, INT, TEXT, UUID) TO {rolle};
GRANT EXECUTE ON FUNCTION verifiser_modultoken(TEXT) TO {rolle};
-- Revalideringen ved innløsning av en kapabilitet: uten EXECUTE her ville
-- hver kvittering og hver artefaktopplasting fra et modultoken svart
-- `permission denied`.
GRANT EXECUTE ON FUNCTION modultoken_fortsatt_autorisert(UUID, TEXT, TEXT, TEXT, BIGINT) TO {rolle};
GRANT EXECUTE ON FUNCTION roter_modultoken(UUID, UUID, TEXT, INT, TEXT, UUID) TO {rolle};
GRANT EXECUTE ON FUNCTION tilbakekall_modultoken(UUID, TEXT, TEXT) TO {rolle};
-- ... og INGEN direkte tabelltilgang (klarsignalet §3). Tabellene eies av
-- modul_eier, så `NULLSTILL_TABELLER` (som bare rører migrators egne
-- tabeller) når dem ikke; REVOKE-en må stå her, som eieren.
REVOKE ALL ON modul_onboarding, modultoken, modultoken_hendelse FROM {rolle};
RESET ROLE;
-- PR-006: outbox-protokollen. `oppdrag` og `reparasjonsoperasjoner` er
-- append+status som `unntak` — INSERT og status-UPDATE, aldri DELETE.
-- `arbeidskapabiliteter` står bevisst IKKE her: den eies av
-- disponit_m37_claimer og nås KUN gjennom SECURITY DEFINER-funksjonene.
-- Et bordgrant der ville gjort hele kapabilitetsmodellen til pynt —
-- runtime kunne satt `status='brukt'` selv, eller utstedt seg en
-- kapabilitet til en handling saken aldri ble klassifisert for.
-- 038 (port 7): INSERT på oppdrag er trukket — begge opphavsveiene
-- går gjennom hver sin herdede funksjon (opprett_reparasjonsoppdrag /
-- opprett_beslutningsoppdrag), som setter `opprinnelse` selv.
GRANT SELECT, UPDATE ON oppdrag TO {rolle};
-- 056: utsendingskjeden — flaten leser lister/signaturer/frigivelser;
-- skriving går KUN gjennom kjedefunksjonene (eid av m37_claimer, EXECUTE
-- i M37_RETTIGHETER_API/VARSLER_RETTIGHETER). Kjøreren er autoritativ:
-- uten radene her overlever ikke migrasjonens grants neste kjøring
-- (Cursor P1 på #140).
GRANT SELECT ON utsendingsliste, utsendingssignatur, utsendingsfrigivelse TO {rolle};
GRANT SELECT ON utsendingsliste_medlem TO {rolle};
GRANT SELECT, INSERT, UPDATE ON reparasjonsoperasjoner TO {rolle};
GRANT SELECT ON verifikasjonsgenerasjon, verifikasjonsbevis, utforelsesklasser TO {rolle};
GRANT SELECT ON verifikasjonskonflikt TO {rolle};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {rolle};
-- api_tokener står bevisst IKKE i listen over, og REVOKE-en på toppen
-- fjerner den hvis en tidligere kjøring ga den bort. Runtime skal nå
-- tokentabellen KUN gjennom SECURITY DEFINER-funksjonen (korreksjon 2):
-- da kan en full lesing av runtimes tilgjengelige tabeller aldri gi
-- secret_mac, og pepperet finnes uansett bare i API-prosessen.
GRANT EXECUTE ON FUNCTION verifiser_token(TEXT, TEXT) TO {rolle};
-- PR-010: OIDC-sesjon. NULLSTILL over har fjernet migrasjon 010s inline-
-- grants; DETTE er den autoritative runtime-tilgangen som overlever.
-- `oidc_provider`/`tenant_oidc_provider`: KUN SELECT (provider skrives
-- aldri av runtime). `brukersesjon` nås for skriving (login/logout), men
-- LESES via SECURITY DEFINER `slaa_opp_sesjon` (som over: aldri hasher ut).
GRANT SELECT ON oidc_provider, tenant_oidc_provider TO {rolle};
GRANT SELECT, INSERT, UPDATE ON brukeridentitet TO {rolle};
GRANT SELECT ON brukermedlemskap TO {rolle};
GRANT SELECT, INSERT, UPDATE ON oidc_logintransaksjon, brukersesjon TO {rolle};
GRANT SELECT, INSERT, UPDATE, DELETE ON oidc_rate TO {rolle};
GRANT EXECUTE ON FUNCTION slaa_opp_sesjon(TEXT) TO {rolle};
-- PR-012: unntaksbehandling. `menneskelig_attestasjon` og
-- `godkjenningsutfall` er append-only (INSERT; UPDATE/DELETE stoppes uansett
-- av trigger). `godkjenningsrunde` trenger status-UPDATE (apen→klar→brukt/
-- utlopt/kansellert), aldri DELETE. Skriving skjer kun fra den herdede
-- behandle_unntakshandling-veien; bindings-/append-only-/kolonnelås-triggere
-- + RLS gjør direkte grant trygt (runtime kan ikke forfalske tilhørighet).
GRANT SELECT, INSERT ON menneskelig_attestasjon, godkjenningsutfall TO {rolle};
GRANT SELECT, INSERT, UPDATE ON godkjenningsrunde TO {rolle};
"""

# PR-006: de herdede M-37-funksjonene. Hver av dem eies av NOLOGIN-rollen
# `disponit_m37_claimer` og er den ENESTE veien til rettigheten den
# innkapsler. Uten EXECUTE her kan verken arbeideren eller API-veien claime,
# utstede eller innløse noe som helst — og med EXECUTE kan de fortsatt bare
# gjøre nøyaktig det funksjonens signatur tillater.
#
# Hvorfor `SET LOCAL ROLE` og ikke bare en GRANT som resten: bare EIEREN kan
# gi bort rettigheter på et objekt, og migrator eier ikke disse funksjonene.
# Den er MEDLEM av eierrollen (kreves for OWNER TO i migrasjon 005), men
# medlemskapet er `WITH INHERIT FALSE` — nettopp for at migrator ikke skal
# arve dispatcher-rollens tilgang til alle tenanters rader. Medlemskapet gir
# fortsatt SET ROLE, og den muligheten brukes her, eksplisitt og avgrenset
# til disse syv GRANT-ene.
#
# `SET LOCAL` gjelder til transaksjonen avsluttes; commiten rett etter
# lukker den, og rollen er tilbake til migrator uansett utfall.
M37_RETTIGHETER = """
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION claim_neste_sak(TEXT, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION forny_claim(TEXT, BIGINT, TEXT, INT, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION frigi_utlopte_claims() TO {rolle};
GRANT EXECUTE ON FUNCTION utsted_arbeidskapabilitet(TEXT, INT, TEXT, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION reserver_kapabilitet(TEXT, TEXT, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION bruk_kapabilitet(TEXT, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION frigi_hengende_kapabiliteter() TO {rolle};
GRANT EXECUTE ON FUNCTION claim_neste_oppdrag(TEXT, TEXT[], TEXT, INT, TEXT, TEXT, BIGINT) TO {rolle};
-- 063 (#165): fornyelsesveien — heartbeat er et claim-livssyklussteg og
-- kalles av API-et som runtimerollen, som claim selv.
GRANT EXECUTE ON FUNCTION forny_oppdragslease(BIGINT, TEXT, TEXT, INT, INT) TO {rolle};
-- PR-007: tofaseprotokollen.
GRANT EXECUTE ON FUNCTION registrer_verifikasjonsbevis(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT, INT, INT, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION start_verifikasjonsgenerasjon(TEXT, BIGINT, TEXT, INT, JSONB, TEXT, TEXT, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION knytt_verifikasjonsoppdrag(TEXT, BIGINT, TEXT, INT, BIGINT) TO {rolle};
-- 035: begge fikk haleargumenter for DEPLOYMENTEN (miljø + release).
-- Signaturen her MÅ følge migrasjonen — 035 dropper de gamle formene, og
-- en GRANT mot en signatur som ikke finnes er en hard feil, ikke en
-- advarsel.
GRANT EXECUTE ON FUNCTION utsted_kvitteringskapabilitet(BIGINT, TEXT, INT, TEXT, TEXT, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION innlos_kvitteringskapabilitet(TEXT, TEXT, TEXT, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION bruk_kvitteringskapabilitet(TEXT, TEXT) TO {rolle};
-- 066 (#159): LESEVEIEN hører til DEN DELTE blokken, ikke til API-blokken
-- (Cursor P2, runde 5 på #247). Oppslaget som gjør «auditert» til en
-- egenskap ved basen skjer i `kjor_bunt` — og den kjører i arbeideren
-- (`disponit-m57.service`), som får DENNE blokken og ikke API-ens. Sto
-- `les_revisjonshendelse` bare i `M37_RETTIGHETER_API`, var utfallet etter
-- migrering enten `permission denied` på avskruingsveien, eller — verre —
-- en usikker dict båret fra API til arbeider, altså tilbake til
-- selvattesten hele #159 finnes for å fjerne.
-- SKRIVEVEIEN følger IKKE med hit: hendelsen skrives av innloggede
-- mennesker gjennom API-et, aldri av en bakgrunnsarbeider. Samme
-- selektivitet som 043 gjorde for oppløsningsveien, bare motsatt vei.
GRANT EXECUTE ON FUNCTION les_revisjonshendelse(TEXT, UUID) TO {rolle};
-- `arkiver_policyversjon` gis IKKE til runtime. Arkivering er en
-- administrativ operasjon, ikke noe forespørselsveien skal kunne utløse.
"""

# 043 (Gate 14b): oppløsningsveien. EGEN blokk fordi den KUN gjelder
# runtime-rollen — M37_RETTIGHETER over kjøres også for `disponit_arbeider`,
# og arbeideren har ingenting med et menneskelig nei å gjøre. Samme
# selektivitet som 038 gjorde for `opprett_beslutningsoppdrag`: autoriteten
# gis der veien faktisk går, ikke der blokken tilfeldigvis bor.
#
# Migrasjon 043 grantet disse til rollenavnet `disponit` direkte. Det
# fungerer lokalt og i test (der runtime HETER disponit), men denne kjøreren
# tar runtime-rollens navn som argument — og på en installasjon med et annet
# navn ville migrasjonens grant enten truffet feil rolle eller feilet på en
# rolle som ikke finnes. Den parameteriserte blokken er den autoritative;
# migrasjonens egen grant er betinget av at rollen finnes.
M37_RETTIGHETER_API = """
SET LOCAL ROLE disponit_m37_claimer;
-- Treargsformen: samme atomiske kappløpsport som kvitteringsveien, med
-- utfallet `avvist` (oppløsningen) og `sen_evidens` (sen kvittering på et
-- kansellert oppdrag).
GRANT EXECUTE ON FUNCTION bruk_kvitteringskapabilitet(TEXT, TEXT, TEXT) TO {rolle};
-- Reversibiliteten fra modulkontrakten — lesejobben sen-kvitteringsveien
-- utleder kompensasjons-/irreversibilitetssaken av.
GRANT EXECUTE ON FUNCTION reversibilitet_for_oppdrag(TEXT, BIGINT) TO {rolle};
-- Selve oppløsningen: kalles av avvis-veien i unntaksbehandlingen, som er
-- scope-gatet (`exceptions:handle`) i app-laget og tenantbundet i
-- funksjonen selv.
--
-- EXECUTE er ikke kanselleringsautoritet (Codex P1, runde 8). Scopeporten
-- og saksversjonen bor i app-laget; en runtime-spørring som omgår dem har
-- fortsatt denne granten. Derfor krever funksjonen SELV en attestert
-- avvisning: en `avvis`-rad i `menneskelig_attestasjon` på saken, av
-- kalleren, skrevet i SAMME transaksjon (043 §7). Beviset er den samme
-- append-only raden `behandle_unntakshandling` skriver rett før kallet, så
-- den lovlige veien merker ingenting — og en direkte kaller får
-- `insufficient_privilege` i stedet for et fencet og kansellert oppdrag.
--
-- ... og raden må være AUTORISERT, ikke bare tilstede (Codex P1, runde 9).
-- Runtime har INSERT på attestasjonstabellen — en port kalleren selv kan
-- fylle er ingen port. Funksjonen krever derfor at attestasjonen navngir et
-- AKTIVT medlemskap i tenanten, med medlemskapets gjeldende
-- `authz_version`, en rolle brukeren faktisk har, og et rollesett som bærer
-- `exceptions:reject` (043 §6b). `brukermedlemskap` er den ene
-- autorisasjonsinngangen runtime IKKE kan skrive (010: OIDC-forvaltet, kun
-- SELECT herfra), så granten under gir ikke lenger rett til å kansellere på
-- vegne av hvem som helst — bare til å utføre et nei et navngitt,
-- avvisningsberettiget menneske har sagt.
GRANT EXECUTE ON FUNCTION avvis_med_opplosning(TEXT, BIGINT, BIGINT[], TEXT, TEXT) TO {rolle};
-- 056: M-57s API-veier — listen opprettes og signeres av innloggede
-- MENNESKER gjennom API-et (runtime alene; utsendingsveien — frigivelse
-- og frigivelsesoppdrag — bor hos varsleren, se VARSLER_RETTIGHETER).
GRANT EXECUTE ON FUNCTION opprett_utsendingsliste(TEXT, UUID, UUID, BIGINT, TEXT, TEXT, UUID[], UUID, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION signer_utsendingsliste(TEXT, UUID, TEXT, TEXT) TO {rolle};
-- 057: kandidatprosessens to herdede veier. Migrasjonen navngir ikke
-- runtime-rollen i det hele tatt lenger (Cursor P2, samme form som 056):
-- denne blokken er ENESTE rettighetskilde. Uten den får en installasjon
-- `permission denied` på prosessfødselen og på lukkingen (som starter
-- slettefristen) etter migrering. Reaperen står bevisst IKKE her: den er
-- kryss-tenant og hører til timerrollen (038-formen, betinget DO-blokk i
-- migrasjonen).
GRANT EXECUTE ON FUNCTION opprett_rekrutteringsprosess(TEXT, BIGINT, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION opprett_kandidat(TEXT, UUID, UUID) TO {rolle};
GRANT EXECUTE ON FUNCTION lukk_rekrutteringsprosess(TEXT, UUID, TIMESTAMPTZ) TO {rolle};
GRANT EXECUTE ON FUNCTION bestill_tidligsletting(TEXT, UUID) TO {rolle};
-- 082 (M-8): kundens slot-administrasjon — innloggede mennesker gjennom
-- API-et, samme vindu og samme eier som 057-dørene over. Utsteder-
-- døren står bevisst IKKE her: den hører senderen til
-- (VARSLER_RETTIGHETER) — web-API-rollen skal aldri kunne mynte en
-- kandidatkapabilitet selv.
GRANT EXECUTE ON FUNCTION m8_opprett_slot(TEXT, UUID, TIMESTAMPTZ, TIMESTAMPTZ, INT, UUID) TO {rolle};
GRANT EXECUTE ON FUNCTION m8_deaktiver_slot(TEXT, UUID) TO {rolle};
-- 089 (M-35): kontinuitetsdørene — innloggede mennesker gjennom API-et
-- (kontinuitet:write i flaten), samme vindu og samme eier som 057/082-
-- dørene over. Lukkedøren krever selv at etteranalysen står i
-- tidslinjen; append-only på postene håndheves av vakten, ikke her.
GRANT EXECUTE ON FUNCTION m35_opprett_tjeneste(TEXT, TEXT, TEXT, TEXT, INT, INT, TEXT, TEXT, TEXT, UUID) TO {rolle};
GRANT EXECUTE ON FUNCTION m35_oppdater_tjeneste(TEXT, UUID, TEXT, INT, INT, TEXT, TEXT, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION m35_opprett_kontakt(TEXT, TEXT, SMALLINT, TEXT, TEXT, UUID) TO {rolle};
GRANT EXECUTE ON FUNCTION m35_bekreft_kontakt(TEXT, UUID, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION m35_opprett_hendelse(TEXT, TEXT, JSONB, TEXT, TEXT, UUID) TO {rolle};
GRANT EXECUTE ON FUNCTION m35_legg_post(TEXT, UUID, TEXT, TEXT, TEXT, UUID) TO {rolle};
GRANT EXECUTE ON FUNCTION m35_lukk_hendelse(TEXT, UUID, TEXT, TEXT) TO {rolle};
RESET ROLE;
-- 096 (M-21): pliktregisterets tre skrivedører — registrering,
-- kvittering og bortfall. Menneskelige handlinger i flaten
-- (`bestilling:opprett` + CSRF + Idempotency-Key), samme vindu som
-- 057/082/089-dørene over, men en ANNEN eier — derfor sin egen
-- rolleblokk. Kvitteringskravet og bortfallsbegrunnelsen håndheves i
-- dørene og i CHECK-ene, ikke her.
SET LOCAL ROLE disponit_plikt_eier;
GRANT EXECUTE ON FUNCTION m21_registrer_plikt(TEXT, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ, TEXT, INT[], TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION m21_lukk_plikt(TEXT, UUID, TEXT, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION m21_marker_bortfalt(TEXT, UUID, TEXT, TEXT) TO {rolle};
RESET ROLE;
-- 097 (M-12): tilgangsregisterets tre skrivedører — objektet, tilgangen
-- og gjennomgangen. Menneskelige handlinger i flaten
-- (`bestilling:opprett` + CSRF + Idempotency-Key), samme vindu som
-- 057/082/089/096-dørene over, men en ANNEN eier — derfor sin egen
-- rolleblokk.
--
-- MERK HVA SOM IKKE STÅR HER, og at fraværet er hele v1-dommen: det
-- finnes ingen dør som OPPRETTER, FLYTTER eller FJERNER en tilgang i et
-- fremmed system. Registeret registrerer; det provisjonerer ikke. Kravet
-- om eier og hjemmel håndheves i dørene og i CHECK-ene, ikke her.
SET LOCAL ROLE disponit_tilgang_eier;
GRANT EXECUTE ON FUNCTION m12_registrer_objekt(TEXT, UUID, TEXT, TEXT, TEXT, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION m12_registrer_tilgang(TEXT, UUID, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, INT, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION m12_registrer_gjennomgang(TEXT, UUID, TEXT) TO {rolle};
SET LOCAL ROLE disponit_m37_claimer;
-- 066 (#159): revisjonshendelsens SKRIVEVEI — runtime alene. Det er API-et
-- innloggede mennesker skriver hendelsen gjennom; en bakgrunnsarbeider har
-- ingenting med å føre den. Leseveien står i den DELTE blokken over, fordi
-- oppslaget skjer i arbeideren (se kommentaren der).
-- Migrasjonens egen grant er betinget av at rollen HETER `disponit`; denne
-- er den autoritative for den konfigurerte rollen.
GRANT EXECUTE ON FUNCTION skriv_revisjonshendelse(TEXT, TEXT, TEXT, TEXT, TEXT) TO {rolle};
"""

# Token-administrasjonen er en EGEN rolle som eier ingenting (korreksjon 2).
# Kolonnenivå med vilje: `secret_mac` er ikke med i SELECT-listen, så en
# kompromittert token-admin kan opprette og deaktivere tokens, men ikke lese
# ut de eksisterende hemmelighetenes MAC. UPDATE er begrenset til de tre
# feltene rotasjon og deaktivering faktisk trenger.
# PR-009 v2 §3: arbeiderens rollesett = runtime-tabellene + M-37-
# funksjonene, MINUS verifiser_token. Arbeideren autentiserer aldri
# API-tokens — den identiteten hører API-prosessen til, og et
# kompromittert arbeidermiljø skal ikke kunne verifisere (og dermed
# time-orakle) kunders tokens.
ARBEIDER_RETTIGHETER = """
GRANT USAGE ON SCHEMA public TO {rolle};
GRANT SELECT, INSERT ON revisjonslogg, frekvens_hendelser TO {rolle};
GRANT SELECT ON migrasjoner TO {rolle};
GRANT SELECT, INSERT ON unntak_historikk, attestasjon_jti TO {rolle};
GRANT SELECT, INSERT, UPDATE ON unntak, idempotens TO {rolle};
GRANT SELECT, INSERT, UPDATE ON tenant_nokler TO {rolle};
GRANT SELECT ON policyer TO {rolle};
-- 038 (port 7): INSERT på oppdrag er trukket — begge opphavsveiene
-- går gjennom hver sin herdede funksjon (opprett_reparasjonsoppdrag /
-- opprett_beslutningsoppdrag), som setter `opprinnelse` selv.
GRANT SELECT, UPDATE ON oppdrag TO {rolle};

GRANT SELECT, INSERT, UPDATE ON reparasjonsoperasjoner TO {rolle};
GRANT SELECT ON verifikasjonsgenerasjon, verifikasjonsbevis, utforelsesklasser TO {rolle};
GRANT SELECT ON verifikasjonskonflikt TO {rolle};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {rolle};
"""

# Senderfunksjonene eies av `disponit_domene_eier` (kryss-tenant, BYPASSRLS),
# og migrator er medlem `WITH INHERIT FALSE` — uten SET LOCAL ROLE blir hver
# GRANT en STILLE WARNING («no privileges were granted») og rollen står uten
# noe som helst. Nøyaktig samme felle og samme løsning som M37_RETTIGHETER
# over; skjemagranten må derimot gis som migrator, som eier skjemaet.
VARSLER_RETTIGHETER = """
GRANT USAGE ON SCHEMA public TO {rolle};
SET LOCAL ROLE disponit_domene_eier;
GRANT EXECUTE ON FUNCTION varsel_klaim_epost(int, int) TO {rolle};
GRANT EXECUTE ON FUNCTION varsel_sett_epoststatus(bigint, uuid, text, text) TO {rolle};
GRANT EXECUTE ON FUNCTION varsel_rekoe(interval, int, interval) TO {rolle};
RESET ROLE;
-- 035: familiehorisont-sveipen (senderens pre-pass). Eies av en ANNEN rolle
-- enn de tre over, derfor sin egen SET LOCAL ROLE.
SET LOCAL ROLE disponit_modul_eier;
GRANT EXECUTE ON FUNCTION varsle_tokenfamilie_utlop(text) TO {rolle};
RESET ROLE;
-- 090/091: de to nye sveipene i senderens pre-pass. Begge er
-- claimer-eide (samme vindu og samme port som resten av 051-dørene), og
-- begge varsler om TAUSHET — noe som IKKE har skjedd. En taushet kan per
-- definisjon ikke varsle om seg selv: lesejobben kjører ikke når det
-- ikke finnes noe å lese, og selvtesten kan ikke rapportere sin egen
-- død. Derfor bor de her, hos den ene timerdrevne prosessen som
-- allerede eier varselkøens rytme.
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION varsle_backupverifisering_uteblitt(TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION varsle_selvtest_uteblitt(TEXT) TO {rolle};
RESET ROLE;
-- 096 (M-21): fristsveipen — den FJERDE sveipen i senderens pre-pass, og
-- den eneste som er kryss-tenant per konstruksjon (registeret er
-- kundenes, ikke plattformens). Den bor her av 035s grunn: senderen er
-- den ene timerdrevne prosessen som allerede eier varselkøens rytme,
-- backoff og idempotens, og en ny varslingsvei er en ny vei å miste et
-- varsel i.
--
-- ÉN EXECUTE, INGEN TABELLRETTIGHETER. Senderrollen har ingen
-- tabellrettigheter i dag, og M-21 gir den ingen: den kan kjøre sveipen
-- og ikke lese en eneste plikt.
SET LOCAL ROLE disponit_plikt_eier;
GRANT EXECUTE ON FUNCTION m21_koe_fristvarsler(INT) TO {rolle};
RESET ROLE;
-- 056: utsendingsveien — det er SENDEREN som konsumerer signerte lister:
-- frigivelse per mottaker og frigivelsesoppdraget (tredje opphavsvei).
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION frigi_utsendelse(TEXT, UUID, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION opprett_frigivelsesoppdrag(TEXT, UUID, TEXT, TEXT, TEXT, BYTEA, TEXT, BYTEA, TIMESTAMPTZ, TIMESTAMPTZ) TO {rolle};
RESET ROLE;
-- 081: senderbenen — sendeklar-lesing, klaim og kvittering (claimer-eid),
-- tenantsveip og uviss-merking (domene-eid, kryss-tenant).
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION m57_neste_sendinger(TEXT, INT, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION m57_start_sending(TEXT, UUID, UUID, UUID, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION m57_fullfor_sending(TEXT, UUID, UUID, UUID, TEXT, TEXT) TO {rolle};
-- 082 (M-8): tidsvalg-kapabiliteten myntes av UTSENDEREN — tokenet
-- committes FØR send(), så en e-post med død lenke er urepresenterbar.
GRANT EXECUTE ON FUNCTION m8_utsted_tidsvalgtoken(TEXT, UUID, UUID, TEXT, TEXT, INT) TO {rolle};
RESET ROLE;
SET LOCAL ROLE disponit_domene_eier;
GRANT EXECUTE ON FUNCTION m57_sendeklare_tenanter(INT, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION m57_merk_uviss(INTERVAL) TO {rolle};
RESET ROLE;
"""

PLAN_RETTIGHETER = """
GRANT USAGE ON SCHEMA public TO {rolle};
-- 048 (#108): plan-arbeiderens rolle — varselsender-modellen. Rollen
-- kjører HELE bestillingsveien in-prosess (044-avviket, ratifisert), så
-- den trenger runtime-DELMENGDEN bestillingsveien faktisk bruker — og
-- claim-funksjonene runtime nettopp mistet. IKKE: saker, varsel-sending,
-- policy-skriving, tokener, domener (negativ port 3 måler).
GRANT SELECT ON migrasjoner TO {rolle};
GRANT SELECT, INSERT ON revisjonslogg, frekvens_hendelser TO {rolle};
GRANT SELECT, INSERT ON unntak_historikk, attestasjon_jti TO {rolle};
-- SAKER: SELECT + INSERT, ALDRI UPDATE (Codex P1). Bestillingsveien
-- SKRIVER en ny `unntak`-rad (`kjerne._skriv_unntak`) og LESER egne
-- rader; den rører aldri en eksisterende sak. En tabell-UPDATE ville
-- gått UTENOM saksbehandlingsfunksjonene: en kompromittert
-- plan-credential kunne satt tenantkontekst, enumerert tenantens saker
-- og selv gjort triggergyldige overganger (f.eks. `ny → manuell`) og
-- dermed tatt saker ut av automatisk behandling — uten claim, uten
-- kapabilitet og uten menneskelig autorisasjon. Det er nøyaktig
-- «IKKE: saker»-grensen over.
GRANT SELECT, INSERT ON unntak TO {rolle};
GRANT SELECT, INSERT, UPDATE ON idempotens TO {rolle};
-- TENANTNØKLER: SELECT + INSERT, ALDRI UPDATE (Codex P1).
-- `hent_eller_opprett_aktiv_dek` leser den aktive DEK-en og oppretter
-- den ved første behov — det er hele behovet. UPDATE er
-- DESTRUKSJONSveien (`kryptering.destruer`: wrapped_dek = NULL,
-- destruert_ts = now(), aktiv = false), og den overgangen er gyldig for
-- enhver rad som er synlig under en valgt tenantkontekst. En
-- kompromittert plan-credential kunne dermed crypto-shreddet en tenants
-- nøkler og gjort alle dens krypterte saker permanent uleselige.
-- Arbeideren verken roterer eller destruerer nøkler.
GRANT SELECT, INSERT ON tenant_nokler TO {rolle};
GRANT SELECT, INSERT ON bestilling_idempotens TO {rolle};
GRANT SELECT ON policyer, policy_hode TO {rolle};
GRANT SELECT ON oppdrag TO {rolle};
GRANT SELECT ON domenekontroll TO {rolle};
GRANT SELECT ON oppdragstype_register, modulkontrakt, modulhode,
                moduldeployment, modulregister_hendelse TO {rolle};
GRANT SELECT ON malautorisasjonsvilkar TO {rolle};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {rolle};
-- Plan-familiens definere (claimer-eide) — nøyaktig kallsettet fra
-- plan/materialiser.py + plan/klassifiser.py + utfor_bestilling-stien;
-- den statiske porten i test_claim_tillitsgrense måler at settet her og
-- kallsettet er samme mengde, så listen ikke kan drifte.
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION claim_planvindu(TEXT, UUID, TIMESTAMPTZ, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION terminaliser_planvindu(TEXT, UUID, TIMESTAMPTZ, UUID, TEXT, TEXT, BIGINT, JSONB) TO {rolle};
GRANT EXECUTE ON FUNCTION frigi_planvindu(TEXT, UUID, TIMESTAMPTZ, UUID) TO {rolle};
GRANT EXECUTE ON FUNCTION forfalte_planvinduer(INT) TO {rolle};
GRANT EXECUTE ON FUNCTION utlopte_planvinduer(INT, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION planvinduer_til_klassifisering(INT, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION plan_nedetid_kandidater(INT, INT) TO {rolle};
GRANT EXECUTE ON FUNCTION plan_nedetid_aggregert(TEXT, UUID, TIMESTAMPTZ, TIMESTAMPTZ, INT, TEXT, TEXT, BOOLEAN) TO {rolle};
GRANT EXECUTE ON FUNCTION pause_plan(TEXT, UUID, TEXT, TEXT, TEXT, JSONB) TO {rolle};
GRANT EXECUTE ON FUNCTION planer_med_menneskelig_avvis() TO {rolle};
GRANT EXECUTE ON FUNCTION planer_gjentatt_uten_resultat() TO {rolle};
GRANT EXECUTE ON FUNCTION planer_med_gjentatt_brudd() TO {rolle};
GRANT EXECUTE ON FUNCTION planer_med_ubehandlet_stopp() TO {rolle};
GRANT EXECUTE ON FUNCTION pause_gjentatt_uten_resultat(TEXT, UUID, TEXT, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION varsle_plan_brudd(TEXT, UUID, TEXT, TEXT) TO {rolle};
GRANT EXECUTE ON FUNCTION opprett_beslutningsoppdrag(TEXT, BIGINT, TEXT, TEXT, TEXT, BYTEA, TEXT, BYTEA, TIMESTAMPTZ, TIMESTAMPTZ) TO {rolle};
RESET ROLE;
"""

# 090 (M-10): lesejobbens rolle. ÉN rettighet, og fraværet av alt annet
# ER rollen (varselsender-modellen, ordrett): ingen tabellrettigheter,
# ingen lesedør, ingen sveip, ingen kunderad. En kompromittert lesejobb
# kan dikte en verifisering som ikke har skjedd — og det er alt den kan.
# Ingen `GRANT USAGE ON SCHEMA public`: EXECUTE på en SECURITY DEFINER-
# funksjon i public krever USAGE på skjemaet, så den ene linjen står,
# men ikke en eneste tabell.
DRIFTSTATUS_RETTIGHETER = """
GRANT USAGE ON SCHEMA public TO {rolle};
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION registrer_backupverifisering(
    TIMESTAMPTZ, TIMESTAMPTZ, NUMERIC, INT, BIGINT) TO {rolle};
-- LESEDØREN STÅR BEVISST IKKE HER. Lesejobben skriver; den leser aldri
-- historikken tilbake, og en `backup_status` den ikke trenger ville vært
-- en tenantsveip den ikke skal ha.
RESET ROLE;
"""

# 091 (M-11): selvtestens rolle. Samme form og samme begrunnelse som
# DRIFTSTATUS_RETTIGHETER over — og her er innsnevringen skarpere enn den
# ser ut: `registrer_selvtest` køer varsler i sin egen transaksjon, så
# rollen kan utløse et varsel uten å kunne lese en eneste varselrad.
SELVTEST_RETTIGHETER = """
GRANT USAGE ON SCHEMA public TO {rolle};
SET LOCAL ROLE disponit_m37_claimer;
GRANT EXECUTE ON FUNCTION registrer_selvtest(UUID, JSONB, TEXT) TO {rolle};
RESET ROLE;
"""

# 092 (M-3): profileringsjobbens rolle. Samme form og samme begrunnelse
# som DRIFTSTATUS_/SELVTEST_RETTIGHETER over, og her er innsnevringen
# modulens viktigste sikkerhetsegenskap: NULL tabellrettigheter i hele
# basen, og EXECUTE på nøyaktig ÉN funksjon. En kompromittert
# profileringsjobb kan telle, og ingenting annet — den kan ikke engang
# lese profilene den selv skrev.
KVALITETSMAALER_RETTIGHETER = """
GRANT USAGE ON SCHEMA public TO {rolle};
SET LOCAL ROLE disponit_kvalitet_eier;
GRANT EXECUTE ON FUNCTION m3_profiler(INT) TO {rolle};
-- LESEDØRENE STÅR BEVISST IKKE HER. Måleren skriver tall; den leser dem
-- aldri tilbake, og en `m3_kvalitetsfunn_tverrgaaende` den ikke trenger
-- ville vært et kryss-tenant-vindu i en jobb som bare skal telle.
RESET ROLE;
"""

# 093 (M-4): retensjonsmålerens rolle. Samme form som de to over — og her
# er innsnevringen på sitt skarpeste: rollen har NULL tabellrettigheter i
# HELE basen og EXECUTE på NØYAKTIG én funksjon. Selve reaperen
# (`m4_reap_egne_maalinger`) står bevisst ikke her: den kalles av
# `m4_mal_lagre` når kjøringen lukker seg, og en reaper med sin egen
# inngang er en slettevei til.
LAGERMAALER_RETTIGHETER = """
GRANT USAGE ON SCHEMA public TO {rolle};
SET LOCAL ROLE disponit_lager_eier;
GRANT EXECUTE ON FUNCTION m4_mal_lagre(INT, BOOLEAN) TO {rolle};
REVOKE ALL ON FUNCTION m4_reap_egne_maalinger(INT) FROM {rolle};
REVOKE ALL ON FUNCTION m4_siste_maaling(TEXT) FROM {rolle};
REVOKE ALL ON FUNCTION m4_retensjonsbilde(TEXT) FROM {rolle};
REVOKE ALL ON FUNCTION m4_retensjonskatalog(TEXT) FROM {rolle};
REVOKE ALL ON FUNCTION m4_retensjonsfunn(TEXT) FROM {rolle};
RESET ROLE;
"""

# 095 (M-9): begrepssveipens rolle. Samme form og samme begrunnelse som
# DRIFTSTATUS_/SELVTEST_RETTIGHETER over: nøyaktig ÉN EXECUTE, ingen
# tabellrettigheter. Innsnevringen er skarpere enn den ser ut —
# `m9_sveip_utlopte` er KRYSS-TENANT og setter selv RLS-konteksten per
# tenant, så rollen kan reise funn i alle tenanter uten å kunne LESE en
# eneste begrepsrad selv. Hele autoriteten står i den eier-eide
# defineren, revidérbar på ett sted.
KUNNSKAPSSVEIP_RETTIGHETER = """
GRANT USAGE ON SCHEMA public TO {rolle};
SET LOCAL ROLE disponit_kunnskap_eier;
GRANT EXECUTE ON FUNCTION m9_sveip_utlopte(INT) TO {rolle};
-- LESEDØRENE STÅR BEVISST IKKE HER. Sveipen skriver funn; den leser
-- aldri ordlisten tilbake gjennom en tenantbundet dør, og en `m9_sok`
-- den ikke trenger ville vært en lesevei den ikke skal ha.
RESET ROLE;
"""

# 097 (M-12): gjennomgangssveipens rolle. Samme form og samme begrunnelse
# som KUNNSKAPSSVEIP_RETTIGHETER over: NØYAKTIG én EXECUTE, ingen
# tabellrettigheter noe sted i basen. Innsnevringen er skarpere enn den
# ser ut — `m12_sveip_gjennomganger` er KRYSS-TENANT og setter selv
# RLS-konteksten per tenant, så rollen kan reise funn i alle tenanter
# uten å kunne LESE en eneste tilgangsrad selv. Hele autoriteten står i
# den eier-eide defineren, revidérbar på ett sted.
#
# PER-TENANT-ARMEN STÅR BEVISST IKKE HER. `m12_sveip_for_tenant` er
# inngangen sveipen bruker INNENFRA, etter at den har bundet konteksten
# til radens tenant; en jobb som kunne kalle den direkte kunne reise og
# lukke funn i én valgt tenant på kommando. Sveipen har én inngang, og
# den er den som ser hele basen og derfor er den som er revidert.
TILGANGSSVEIP_RETTIGHETER = """
GRANT USAGE ON SCHEMA public TO {rolle};
SET LOCAL ROLE disponit_tilgang_eier;
GRANT EXECUTE ON FUNCTION m12_sveip_gjennomganger(INT) TO {rolle};
-- LESEDØRENE STÅR BEVISST IKKE HER. Sveipen skriver funn; den leser
-- aldri tilgangsbildet tilbake gjennom en tenantbundet dør, og et
-- `m12_tilgangsbilde` den ikke trenger ville vært en lesevei inn i
-- hvem-har-tilgang-til-hva som ingen jobb skal ha.
RESET ROLE;
"""

TOKEN_ADMIN_RETTIGHETER = """
REVOKE ALL ON FUNCTION verifiser_token(TEXT, TEXT) FROM {rolle};
GRANT USAGE ON SCHEMA public TO {rolle};
GRANT SELECT (token_id, tenant, rolle, scopes, status, utloper, last_used_at,
              opprettet) ON api_tokener TO {rolle};
GRANT INSERT ON api_tokener TO {rolle};
GRANT UPDATE (status, utloper, secret_mac) ON api_tokener TO {rolle};
GRANT INSERT ON revisjonslogg TO {rolle};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {rolle};
-- PR-009 V2: PENDING-verifikasjonen — metadata + MAC for et PENDING-token,
-- aldri pepper (pepperet er aldri i databasen og aldri funksjonsargument).
GRANT EXECUTE ON FUNCTION hent_pending_token(TEXT) TO {rolle};
"""


def last_bootstrap():
    """Bootstrap-modulen har bindestrek i navnet og kan ikke importeres
    vanlig. Egen funksjon på modulnivå slik at tester kan bytte den ut —
    herdingen skal finnes ett sted, og den er samme fil som kjøres manuelt.
    """
    spek = importlib.util.spec_from_file_location(
        "migrasjon_bootstrap",
        Path(__file__).with_name("migrasjon-bootstrap.py"))
    modul = importlib.util.module_from_spec(spek)
    spek.loader.exec_module(modul)
    return modul


def main(argv: list[str] | None = None) -> int:
    # argv som parameter, ikke sys.argv direkte: da kan tester kalle
    # inngangen som den kalles i drift, uten å rote med prosessens argumenter.
    argv = sys.argv[1:] if argv is None else argv
    dsn = os.environ.get("DISPONIT_MIGRATOR_URL")
    if not dsn:
        print("AVBRUTT: DISPONIT_MIGRATOR_URL mangler — migrasjoner kjøres"
              " av skjemaeieren, aldri av runtime.")
        return 2
    rolle = argv[0] if argv else "disponit"
    if not rolle.replace("_", "").isalnum():
        print(f"AVBRUTT: ugyldig rollenavn {rolle!r}")
        return 2

    from db.kjorer import LAAS, LEGACY_MAKS, migrer
    from db.pg import koble

    bootstrap = last_bootstrap()
    conn = koble(dsn)
    try:
        # YTTERLÅS rundt HELE overgangen legacy -> herding -> 003.
        #
        # Codex' P1: hvert `migrer()`-kall tok og slapp låsen selv, og
        # `herd_historikk()` tok ingen. «Herding før 003» var derfor bare
        # sant inne i én prosess: to samtidige oppsett kunne rekke å kjøre
        # 003 i vinduet mellom stegene, og da er den bindende rekkefølgen
        # brutt selv om hvert enkelt steg var låst.
        #
        # PostgreSQL teller session-låser per sesjon, så kjørerens egne
        # lock/unlock inne i denne blokken er reentrante og holder låsen
        # oppe hele veien. Antall lås og opplås må balansere — derfor
        # slippes ytterlåsen i finally, uansett utfall.
        conn.execute("SELECT pg_advisory_lock(%s)", (LAAS,))
        conn.commit()
        # Kontraktens rekkefølge (v3-delta): legacy først, så herding av
        # historikken, så resten. Kjøres 003 før checksum-kolonnen er
        # NOT NULL, er historikken fortsatt muterbar mens den nye
        # migrasjonen legges oppå — og oppsettet ville rapportert suksess.
        legacy = migrer(conn, til_og_med=LEGACY_MAKS)
        print(f"legacy-migrasjoner: {legacy or 'ingen'}")
        bootstrap.herd_historikk(conn)
        print("historikk herdet: checksum er NOT NULL")

        # PR-006: backfillen av policysnapshotet må ligge MELLOM 005 (som
        # legger kolonnene nullable) og 006 (som setter NOT NULL). Samme
        # grep som legacy/herding over, og av samme grunn: et steg som må
        # skje i en bestemt rekkefølge håndheves av rekkefølgen, ikke av et
        # notat. Gjør noen dette i feil rekkefølge, feiler 006 med en
        # melding som sier hva som mangler.
        #
        # Migrasjonene kjøres altså i tre etapper, ikke to. Se
        # `db.m37_backfill.KJOR_ETTER_MIGRASJON` — tallet står der og ikke
        # her, fordi det er backfillens kontrakt og ikke deployens valg.
        from db import m37_backfill
        for_backfill = migrer(conn, til_og_med=m37_backfill.KJOR_ETTER_MIGRASJON)
        res = m37_backfill.backfill(conn)
        print(f"m37-backfill: {res.fra_evidens} fra evidens, {res.legacy}"
              f" legacy->manuell, {res.tenanter} tenanter"
              + (f", grunner: {dict(sorted(res.grunner.items()))}"
                 if res.grunner else ""))
        kjort = legacy + for_backfill + migrer(conn)
        print(f"migrasjoner kjørt: {kjort or 'ingen — alt var oppdatert'}")
        conn.execute(NULLSTILL_TABELLER.format(rolle=rolle))
        conn.execute(RETTIGHETER.format(rolle=rolle))
        conn.commit()
        conn.execute(M37_RETTIGHETER.format(rolle=rolle))
        conn.commit()      # avslutter SET LOCAL ROLE
        # 043: oppløsningsveien — runtime ALENE (se blokken).
        conn.execute(M37_RETTIGHETER_API.format(rolle=rolle))
        conn.commit()
        # PR-013: policy_eier sitt skrivegrant på `policyer`/`policy_hode` bor
        # i migrasjon 013 sammen med funksjonen — der overlever det enhver
        # skjemagjenoppbygging (også testenes _nullstill + re-migrer), ikke
        # bare denne kjøringen. Ikke dupliser det her.
        print(f"rettigheter satt for {rolle}")
        # Token-admin er valgfri på eldre installasjoner: rollen opprettes av
        # oppsett-skriptet, og en GRANT til en rolle som ikke finnes er en
        # hard feil — ikke en advarsel. Betinget, som 003 gjør for runtime.
        token_admin = os.environ.get("DISPONIT_TOKEN_ADMIN_ROLLE",
                                     "disponit_token_admin")
        if not token_admin.replace("_", "").isalnum():
            print(f"AVBRUTT: ugyldig rollenavn {token_admin!r}")
            return 2
        finnes = conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s",
                              (token_admin,)).fetchone()
        if finnes:
            conn.execute(NULLSTILL_TABELLER.format(rolle=token_admin))
            conn.execute(TOKEN_ADMIN_RETTIGHETER.format(rolle=token_admin))
            conn.commit()
            print(f"rettigheter satt for {token_admin}")
        else:
            conn.rollback()
            print(f"hopper over {token_admin}: rollen finnes ikke"
                  " (opprettes av oppsett-postgresql.sh)")
        # PR-009: arbeiderrollen — betinget som token-admin, av samme grunn.
        arbeider = "disponit_arbeider"
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s",
                        (arbeider,)).fetchone():
            conn.execute(NULLSTILL_TABELLER.format(rolle=arbeider))
            conn.execute(ARBEIDER_RETTIGHETER.format(rolle=arbeider))
            conn.commit()
            conn.execute(M37_RETTIGHETER.format(rolle=arbeider))
            conn.commit()
            print(f"rettigheter satt for {arbeider}")
        else:
            conn.rollback()
            print(f"hopper over {arbeider}: rollen finnes ikke"
                  " (opprettes av oppsett-postgresql.sh)")
        # Varselsenderens rolle — betinget som de andre, av samme grunn.
        # KUN de tre funksjonene: SECURITY DEFINER gjør tabellgrants
        # unødvendige, og fraværet av dem ER poenget med rollen (Codex P1:
        # et kompromittert web-API skal ikke ha senderens kryss-tenant-vindu).
        varsler = "disponit_varselsender"
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s",
                        (varsler,)).fetchone():
            conn.execute(VARSLER_RETTIGHETER.format(rolle=varsler))
            conn.commit()
            print(f"rettigheter satt for {varsler}")
        else:
            conn.rollback()
            print(f"hopper over {varsler}: rollen finnes ikke"
                  " (opprettes av oppsett-postgresql.sh)")
        # 048 (#108): plan-arbeiderens rolle — betinget som de andre.
        # Rollen bærer bestillingsveiens delmengde + claim-funksjonene
        # runtime mistet; se PLAN_RETTIGHETER for grensen og porten.
        planarb = "disponit_plan_arbeider"
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s",
                        (planarb,)).fetchone():
            conn.execute(NULLSTILL_TABELLER.format(rolle=planarb))
            conn.execute(PLAN_RETTIGHETER.format(rolle=planarb))
            conn.commit()
            print(f"rettigheter satt for {planarb}")
        else:
            conn.rollback()
            print(f"hopper over {planarb}: rollen finnes ikke"
                  " (opprettes av oppsett-postgresql.sh)")
        # 090/091: driftstatusens og selvtestens roller — betinget som de
        # andre, av samme grunn (en GRANT til en rolle som ikke finnes er
        # en hard feil, ikke en advarsel). Ingen NULLSTILL_TABELLER for
        # disse to: de har ingen tabellrettigheter å nullstille, og et
        # kall som listet tabellene ville vært den første antydningen om
        # at de skulle hatt noen.
        # 092 (M-3): profileringsjobbens rolle står i SAMME løkke og av
        # samme grunn — ingen tabellrettigheter å nullstille, og en
        # GRANT til en rolle som ikke finnes er en hard feil.
        for navn, mal in (("disponit_driftstatus", DRIFTSTATUS_RETTIGHETER),
                          ("disponit_selvtest", SELVTEST_RETTIGHETER),
                          ("disponit_kvalitetsmaaler",
                           KVALITETSMAALER_RETTIGHETER),
                          ("disponit_lagermaaler", LAGERMAALER_RETTIGHETER),
                          # 095 (M-9): begrepssveipens rolle står i samme
                          # løkke og med samme betingelse — den har heller
                          # ingen tabellrettigheter å nullstille, bare den
                          # ene EXECUTEn.
                          ("disponit_kunnskapssveip",
                           KUNNSKAPSSVEIP_RETTIGHETER),
                          # 097 (M-12): gjennomgangssveipens rolle står i
                          # samme løkke og med samme betingelse — heller
                          # ingen tabellrettigheter å nullstille, bare
                          # den ene EXECUTEn.
                          ("disponit_tilgangssveip",
                           TILGANGSSVEIP_RETTIGHETER)):
            if conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s",
                            (navn,)).fetchone():
                conn.execute(mal.format(rolle=navn))
                conn.commit()
                print(f"rettigheter satt for {navn}")
            else:
                conn.rollback()
                print(f"hopper over {navn}: rollen finnes ikke"
                      " (opprettes av oppsett-postgresql.sh)")
        # Sluttkontroll. En advarsel med exit 0 er ingen port: klarer vi
        # ikke å bevise at historikken er låst, skal oppsettet feile.
        versjoner = conn.execute(
            "SELECT versjon, checksum IS NOT NULL FROM migrasjoner"
            " ORDER BY versjon").fetchall()
        nullable = conn.execute(
            "SELECT is_nullable FROM information_schema.columns"
            " WHERE table_name='migrasjoner' AND column_name='checksum'"
        ).fetchone()
        conn.rollback()
        uten = [v for v, cs in versjoner if not cs]
        if uten or not nullable or nullable[0] != "NO":
            print(f"AVBRUTT: historikken er ikke låst — uten checksum: {uten},"
                  f" kolonne nullable: {nullable and nullable[0]}")
            return 1

        # GO-vilkår V3: retention-vakten skal finnes OG være aktiv når
        # deployet er ferdig. Event-triggeren i oppsett-skriptet hindrer at
        # den DEAKTIVERES; denne kontrollen fanger at den er FJERNET.
        # Uten begge er «referert policyversjon kan ikke slettes» en
        # egenskap ved en trigger noen kan droppe i en migrasjon.
        vakt = conn.execute(
            "SELECT t.tgenabled FROM pg_trigger t"
            " WHERE t.tgrelid='public.policyer'::regclass"
            "   AND t.tgname='policy_retention'").fetchone()
        conn.rollback()
        if vakt is None or vakt[0] == "D":
            print("AVBRUTT: policy_retention mangler eller er deaktivert —"
                  " GO-vilkår V3 er ikke håndhevet i denne basen.")
            return 1
        print("policy_retention: aktiv (GO-vilkår V3)")
        print("register: " + ", ".join(str(v) for v, _ in versjoner)
              + "  (alle med checksum, kolonnen er NOT NULL)")
    finally:
        try:
            # ROLLBACK FØRST. Feiler noe over, står transaksjonen i
            # «aborted», og da avvises ENHVER setning — inkludert
            # opplåsingen. Uten denne linjen kastet `finally` en
            # `InFailedSqlTransaction` som ERSTATTET den opprinnelige
            # feilen, og enhver migrasjonsfeil så ut som det samme
            # innholdsløse problemet. Feilsøkingen av 005 gikk i sirkel på
            # nettopp det: den ekte meldingen fantes aldri i utskriften.
            #
            # Advisory-låsen er på SESJONSnivå og overlever rollback, så
            # opplåsingen er fortsatt både nødvendig og korrekt her.
            conn.rollback()
            conn.execute("SELECT pg_advisory_unlock(%s)", (LAAS,))
            conn.commit()
        finally:
            conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
