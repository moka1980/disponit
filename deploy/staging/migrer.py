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
-- PR-013: policyadministrasjon. Runtime LESER hodet/utkast/runder og SKRIVER
-- utkast/runder/attestasjoner direkte (RLS-gated), men når ALDRI `policyer`
-- eller `policy_hode`-pekeren — aktivering går kun via den herdede
-- `aktiver_policy` (EXECUTE gitt i migrasjon 013).
GRANT SELECT ON policy_hode TO {rolle};
GRANT SELECT, INSERT, UPDATE ON policyutkast, aktiveringsrunde, aktiveringsattestasjon TO {rolle};
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
GRANT SELECT, INSERT, UPDATE ON varselvalg TO {rolle};
-- PR-014a: modulregisteret. Runtime LESER det (default-deny, GRANT-modell §4) —
-- INGEN INSERT/UPDATE/DELETE på registertabellene. Alle skriv går via de herdede
-- overgangsfunksjonene (CP2), som `aktiver_policy`. En direkte skriving fra
-- runtime skal gi `permission denied` (Codex-port 17).
GRANT SELECT ON modulkontrakt, modulhode, modulrelease, moduldeployment,
    oppdragstype_register, modulregister_hendelse TO {rolle};
GRANT SELECT ON domenekontroll, artefakt, artefakttype_register TO {rolle};
-- PR-014c: skjemavalidering ved opplasting/promotering og aktiveringsporten
-- for `ekstern_lesing` leses i API-prosessen. Runtime skriver aldri.
GRANT SELECT ON artefaktskjema, malautorisasjonsvilkar TO {rolle};
-- 038 §6.1: idempotensregisteret for bestillinger. KUN SELECT — gjenspillet
-- leser raden, men skriveveien er `registrer_bestilling_idempotens` (045).
-- INSERT sto her til 045: raden i `plan:`-rommet er beviset et planvindu
-- kan terminaliseres på uten claim, og en tabell runtime selv kunne skrive
-- i ga beviset et innhold uten et opphav. Radene er dessuten immutable
-- (trigger avviser UPDATE og DELETE), og et grant der ville bare skjult at
-- triggeren er porten.
GRANT SELECT ON bestilling_idempotens TO {rolle};
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
